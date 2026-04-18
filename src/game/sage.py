"""SAGE — event source.

Phase 1 Track B ships a *mock*: no Ollama, no prompt builder, no validator. It
picks a random hand-written event from `events_fallback.FALLBACK_EVENTS` and
returns it. The real LLM-backed implementation (build_prompt / call_ollama /
validate / retry) lands later behind the same `generate_event` interface.

Pure-ish: takes state, returns (event_dict, EventRef-shaped dict). Mutates the
inbox via the caller — this module never touches I/O or globals beyond the RNG.
"""

from __future__ import annotations

import random
import uuid
from typing import Optional

from game import balance as B
from game.events_fallback import FALLBACK_EVENTS
from game.state import EventRef, GameState, RecentEvent


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

    state.recent_events.append(RecentEvent(slug=ref.event["slug"], days_ago=0))
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
