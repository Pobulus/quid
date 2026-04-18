"""SAGE — event source.

Two paths share one interface:

  * `generate_event(state)` — mock path. Picks a random event from
    `events_fallback.FALLBACK_EVENTS`. Used by the live `/api/sage/event`
    endpoint. No I/O, deterministic shape.

  * `generate_event_via_llm(state, call_fn)` — full pipeline:
        build_prompt → call_fn(prompt) → validate_event → (one retry) → fallback.
    `call_fn` is injected (not implemented here — Ollama client is B2). When
    the LLM is wired, the endpoint flips to this path; until then it lives as
    a tested shell.

Validator + prompt builder are shipped now so the contract is locked in: any
LLM output the system later accepts must satisfy `validate_event`, and the
prompt enforces that contract by example.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any, Callable, Optional

import requests
from django.conf import settings
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from game import balance as B
from game.events_fallback import FALLBACK_EVENTS
from game.state import EventRef, GameState, RecentEvent


# Flipped to False by apps.GameConfig.ready() when OLLAMA_HOST is unreachable.
OLLAMA_AVAILABLE = True


# ---- Probability gate -----------------------------------------------------------


def event_probability(state: GameState) -> float:
    """Stress-scaled fire chance: base + coeff * (debt_pressure + stat_deficit)."""
    cc = state.credit_card
    cc_util = (cc.balance / cc.limit) if cc and cc.limit > 0 else 0.0
    loan_load = sum(l.remaining for l in state.loans) / 1_000_000  # per 10k PLN
    debt_pressure = min(2.0, cc_util + loan_load)

    deficit = sum(
        max(0, 50 - state.player.stats.get(k, 100)) / 50
        for k in B.STAT_KEYS
    )

    raw = B.EVENT_BASE_PROB + B.EVENT_PRESSURE_COEFF * (debt_pressure + deficit)
    return min(B.EVENT_PROB_CAP, raw)


# ---- Mock generator -------------------------------------------------------------


def generate_event(
    state: GameState,
    rng: Optional[random.Random] = None,
) -> dict:
    """Return a fresh event payload (with a unique event_id).

    Avoids slugs already present in `recent_events` if possible. If every
    fallback event is in recent history, picks any.
    """
    rng = rng or random.Random()
    recent_slugs = {r.slug for r in state.recent_events}

    pool = [e for e in FALLBACK_EVENTS if e["slug"] not in recent_slugs]
    if not pool:
        pool = list(FALLBACK_EVENTS)

    template = rng.choice(pool)
    event = dict(template)
    event["event_id"] = uuid.uuid4().hex
    return event


def push_to_inbox(state: GameState, event: dict) -> EventRef:
    ref = EventRef(
        event_id=event["event_id"],
        received_day=state.day,
        received_month=state.month,
        status="unread",
        event=event,
    )
    state.inbox.append(ref)
    return ref


# ---- Resolution -----------------------------------------------------------------


def _clamp_stat(value: int) -> int:
    return max(0, min(B.STAT_MAX, value))


def _clamp_skill(value: int) -> int:
    return max(0, min(B.SKILL_MAX, value))


def _clamp_credit(value: int) -> int:
    return max(B.CREDIT_SCORE_MIN, min(B.CREDIT_SCORE_MAX, value))


def apply_effects(state: GameState, effects: dict) -> dict:
    """Mutates state in place. Returns the (clamped) deltas actually applied."""
    applied: dict = {}
    for key, raw_delta in effects.items():
        if key not in B.EFFECT_KEYS:
            continue  # silently drop — the validator catches this for real LLM output

        lo, hi = B.EFFECT_DELTA_BOUNDS.get(key, (-10**9, 10**9))
        delta = max(lo, min(hi, int(raw_delta)))

        if key == "money":
            state.accounts.checking += delta
            applied[key] = delta
        elif key in B.STAT_KEYS:
            before = state.player.stats[key]
            state.player.stats[key] = _clamp_stat(before + delta)
            applied[key] = state.player.stats[key] - before
        elif key in B.SKILL_KEYS:
            before = state.player.skills[key]
            state.player.skills[key] = _clamp_skill(before + delta)
            applied[key] = state.player.skills[key] - before
        elif key == "credit_score":
            before = state.credit_score
            state.credit_score = _clamp_credit(before + delta)
            applied[key] = state.credit_score - before

    return applied


def resolve_event(
    state: GameState,
    event_id: str,
    option_id: str,
    roll_d20: int,
) -> dict:
    """Look up event in inbox, apply chosen option, mark resolved.

    Returns: {rolled, dc, skill, passed, effects_applied}.
    Raises ValueError if event missing, already resolved, or option unknown.
    """
    if not 1 <= roll_d20 <= 20:
        raise ValueError(f"roll_d20 out of range: {roll_d20}")

    ref = next((r for r in state.inbox if r.event_id == event_id), None)
    if ref is None:
        raise ValueError(f"event_id not in inbox: {event_id}")
    if ref.status == "resolved":
        raise ValueError(f"event already resolved: {event_id}")

    option = next((o for o in ref.event["options"] if o["id"] == option_id), None)
    if option is None:
        raise ValueError(f"option_id not in event: {option_id}")

    sc = option.get("skill_check")
    if sc is None:
        passed = True
        skill_name = None
        dc = None
        skill_value = None
        total = roll_d20
        effects = option["effects_on_success"]
    else:
        skill_name = sc["skill"]
        dc = int(sc["difficulty_class"])
        skill_value = state.player.skills.get(skill_name, 0)
        total = roll_d20 + skill_value
        passed = total >= dc
        effects = option["effects_on_success"] if passed else option["effects_on_failure"]

    applied = apply_effects(state, effects)

    ref.status = "resolved"

    dedup_key = "_".join(ref.event.get("title", ref.event_id).lower().split()[:5])
    state.recent_events.append(RecentEvent(slug=dedup_key, days_ago=0))
    state.recent_events = state.recent_events[-8:]

    return {
        "rolled": roll_d20,
        "skill": skill_name,
        "skill_value": skill_value,
        "dc": dc,
        "total": total,
        "passed": passed,
        "effects_applied": applied,
    }


# ---- Prompt builder (B1) --------------------------------------------------------


_SYSTEM_PROMPT = """\
You are SAGE, the event generator for QUID — a deliberately unforgiving
financial-literacy RPG set in modern-day Poland. Currency is PLN. Tone:
realistic, dry, lightly cynical, educational. No fantasy, no metaphors, no
moralising. Mundane events with real financial weight.

Your job: produce ONE inbox event for the player as a single JSON object.
No prose, no markdown fences, no commentary, no surrounding array. JSON object only.
ALL THE RESPONSES SHOULD BE IN ENGLISH.

EFFECT KEYS (closed set — no others allowed):
  health, hunger, sanity, energy, money, credit_score,
  cooking, handiwork, charisma, physique

EFFECT BOUNDS (per-option, success or failure):
  money         : -50000 .. 50000   (grosze; 100 grosze = 1 PLN)
  credit_score  : -25 .. 25
  health/hunger/sanity/energy : -30 .. 30
  cooking/handiwork/charisma/physique : -2 .. 2

OPTION SHAPE (uniform — every option has the same shape):
  {
    "id": "a" | "b" | "c" | "d",
    "label": "<short verb phrase>",
    "skill_check": {"skill": "<one of cooking|handiwork|charisma|physique>",
                    "difficulty_class": <int 5..25>}
                    OR null,
    "effects_on_success": {<effect_key>: <int>, ...},
    "effects_on_failure": {<effect_key>: <int>, ...},
  }

If skill_check is null, effects_on_success and effects_on_failure MUST be the
same object (no implicit roll).

EVENT SHAPE:
  {
    "title": "<inbox subject line, ~6 words>",
    "sender": "<who the message is from>",
    "body": "<short markdown body — paragraphs, **bold**, *italic*. No HTML.>",
    "options": [<2..4 options>]
  }

EXAMPLE OUTPUT (study the shape, do not copy verbatim):
{
  "title": "The boiler is making a noise again",
  "sender": "Landlord",
  "body": "Woke up to a grinding noise. Your landlord says it's *\\"basically working\\"*.",
  "options": [
    {"id": "a", "label": "Fix it yourself",
     "skill_check": {"skill": "handiwork", "difficulty_class": 12},
     "effects_on_success": {"handiwork": 1, "sanity": -5},
     "effects_on_failure": {"money": -25000, "sanity": -10}
    },
    {"id": "b", "label": "Pay a plumber",
     "skill_check": null,
     "effects_on_success": {"money": -30000, "sanity": -2},
     "effects_on_failure": {"money": -30000, "sanity": -2}
    }
  ]
}

Hard rules:
  * Output a single JSON object. No surrounding text, array, or code fences.
  * Avoid repeating recent situations (see `recent_events` in the context).
  * Every effect key must be in the closed set above.
  * Every effect delta must be within the bounds above.
  * Money in grosze (integer). 1 PLN = 100 grosze.
  * Each event must be plausibly tied to the player's current situation.
"""


def _state_slice(state: GameState) -> dict:
    """The minimal player snapshot SAGE needs. Matches the "context" block
    in the user prompt below."""
    return {
        "month": state.month,
        "day": state.day,
        "stats": dict(state.player.stats),
        "skills": dict(state.player.skills),
        "money_pln": round(state.accounts.checking / 100, 2),
        "savings_pln": round(state.accounts.savings / 100, 2),
        "credit_score": state.credit_score,
        "house_tier": state.house.tier,
        "has_credit_card": state.credit_card is not None,
        "open_loans": [
            {"kind": l.kind, "remaining_pln": round(l.remaining / 100, 2)}
            for l in state.loans
        ],
        "recent_events": [
            {"slug": r.slug, "days_ago": r.days_ago} for r in state.recent_events
        ],
    }


def build_prompt(state: GameState) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt). Caller assembles for whichever
    chat API. The system prompt is static; the user prompt embeds the state
    slice as JSON."""
    ctx = _state_slice(state)
    user = (
        "Generate one event for this player.\n\n"
        "PLAYER CONTEXT (JSON):\n"
        f"{json.dumps(ctx, ensure_ascii=False, indent=2)}\n\n"
        "Return one JSON object matching the event schema. JSON only."
    )
    return _SYSTEM_PROMPT, user


# ---- Validator (B3) -------------------------------------------------------------


class _SkillCheck(BaseModel):
    skill: str
    difficulty_class: int = Field(ge=5, le=25)

    @field_validator("skill")
    @classmethod
    def _skill_in_set(cls, v: str) -> str:
        if v not in B.SKILL_KEYS:
            raise ValueError(f"skill must be one of {B.SKILL_KEYS}, got {v!r}")
        return v


class _Option(BaseModel):
    id: str = Field(min_length=1, max_length=2)
    label: str = Field(min_length=1, max_length=80)
    skill_check: Optional[_SkillCheck] = None
    effects_on_success: dict[str, int]
    effects_on_failure: dict[str, int]

    @field_validator("effects_on_success", "effects_on_failure")
    @classmethod
    def _effects_clean(cls, v: dict[str, int]) -> dict[str, int]:
        for k, delta in v.items():
            if k not in B.EFFECT_KEYS:
                raise ValueError(f"unknown effect key {k!r}; allowed: {B.EFFECT_KEYS}")
            lo, hi = B.EFFECT_DELTA_BOUNDS.get(k, (-(10**9), 10**9))
            if not (lo <= delta <= hi):
                raise ValueError(f"effect {k}={delta} out of bounds [{lo}, {hi}]")
        return v

    @model_validator(mode="after")
    def _no_check_means_equal_effects(self) -> "_Option":
        if self.skill_check is None and self.effects_on_success != self.effects_on_failure:
            raise ValueError(
                "options with skill_check=null must have identical "
                "effects_on_success and effects_on_failure"
            )
        return self


class _Event(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    sender: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1)
    options: list[_Option] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _unique_option_ids(self) -> "_Event":
        ids = [o.id for o in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError(f"option ids must be unique, got {ids}")
        return self


def validate_event(payload: Any) -> dict:
    """Validate one LLM-produced event payload.

    Raises pydantic.ValidationError on shape/bounds problems.
    Returns the validated payload as a plain dict. Does NOT add `event_id` or
    `slug` — caller owns those.
    """
    event = _Event.model_validate(payload)
    return event.model_dump()


# ---- LLM pipeline shell (B1+B3+B4 wired; B2 injected) ---------------------------


CallFn = Callable[[str, str], Any]
"""Signature for the Ollama caller (to be implemented in B2):
    call_fn(system_prompt: str, user_prompt: str) -> dict | str
Should return parsed JSON when the model honours `format: json`, or a raw
string the pipeline will try to parse. Raises on transport failure."""


def _coerce_to_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    raise ValueError(f"call_fn returned unsupported type: {type(raw).__name__}")


def _validate_single(raw: Any) -> dict:
    """Parse and validate one LLM-returned event. Assigns a fresh event_id."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object, got {type(raw).__name__}")
    ev = validate_event(raw)
    ev["event_id"] = uuid.uuid4().hex
    return ev


def generate_single_event_via_llm(
    state: GameState,
    call_fn: CallFn,
    rng: Optional[random.Random] = None,
) -> tuple[dict, str]:
    """Synchronously generate ONE event via the LLM. No queue interaction.

    Returns (event_dict, source) where source is "llm" | "llm_retry" | "fallback".
    Used by the prefetch endpoint and as a fallback when the client queue is
    empty at the moment an event is requested.
    """
    if not OLLAMA_AVAILABLE:
        return generate_event(state, rng=rng), "fallback"

    system, user = build_prompt(state)

    last_error: Optional[str] = None
    for attempt in range(2):  # initial + one retry
        retry_suffix = (
            f"\n\nYour previous response was invalid:\n{last_error}\n"
            "Return one corrected JSON object — no prose, no array."
            if last_error
            else ""
        )
        try:
            raw = call_fn(system, user + retry_suffix)
            ev = _validate_single(raw)
            return ev, ("llm" if attempt == 0 else "llm_retry")
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_error = str(e)
        except Exception as e:
            last_error = f"transport: {e}"
            break

    return generate_event(state, rng=rng), "fallback"


def generate_event_via_llm(
    state: GameState,
    call_fn: CallFn,
    rng: Optional[random.Random] = None,
) -> tuple[dict, str]:
    """Drain the client-supplied prefetch queue first; if empty, synchronously
    generate one event. Returns (event_dict, source) where source is
    "llm_queue" | "llm" | "llm_retry" | "fallback".
    """
    queue: list[dict] = state.flags.get("event_queue", [])
    if queue:
        event = queue.pop(0)
        state.flags["event_queue"] = queue
        return event, "llm_queue"

    return generate_single_event_via_llm(state, call_fn, rng=rng)


# ---- Ollama HTTP client (B2) ----------------------------------------------------


OLLAMA_TIMEOUT_S = 120


def call_ollama(system: str, user: str) -> Any:
    """POST to {OLLAMA_HOST}/api/chat with format=json. Returns parsed JSON
    (list of events per _SYSTEM_PROMPT). Raises on transport or decode errors —
    `generate_event_via_llm` catches and falls back.
    """
    host = settings.OLLAMA_HOST.rstrip("/")
    model = settings.OLLAMA_MODEL
    if not host:
        raise RuntimeError("OLLAMA_HOST not set")

    resp = requests.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
        },
        timeout=OLLAMA_TIMEOUT_S,
    )
    resp.raise_for_status()
    body = resp.json()
    content = body.get("message", {}).get("content", "")
    if not content:
        raise ValueError("empty content from Ollama")
    return json.loads(content)
