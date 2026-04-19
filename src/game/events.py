"""Day-tick engine and calendar processing. Pure functions.

`advance_day` is the atomic unit: apply decay, increment workdays if applicable,
fire today's calendar events, roll month over if needed, check game-over.
"""

from __future__ import annotations

import random as _rand

from game import balance as B
from game import finance as F
from game import sage
from game.state import CalendarEvent, EventRef, GameOver, GameState, seed_month_calendar


GAME_OVER_FLAVOR = {
    "health": "You collapse from exhaustion. Game over.",
    "hunger": "Starvation claims you. Game over.",
    "sanity": "Reality slips away. Game over.",
    "energy": "You can't get out of bed anymore. Game over.",
}


def _apply_stat_decay(state: GameState) -> None:
    for key, amt in B.STAT_DECAY_PER_DAY.items():
        state.player.stats[key] = max(0, state.player.stats[key] - amt)




def check_game_over(state: GameState) -> GameState:
    if state.game_over is not None:
        return state
    for key in B.STAT_KEYS:
        if state.player.stats[key] <= B.GAME_OVER_STAT:
            state.game_over = GameOver(cause=key, flavor=GAME_OVER_FLAVOR[key])
            return state
    return state


def _fire_calendar_for_today(state: GameState) -> list[str]:
    """Fire (and remove) calendar events matching today.

    Auto-resolve events fire silently via `_apply_calendar_event`. Non-auto
    `loan_due` entries attempt a payment and push an informational inbox
    entry on miss. Other non-auto events are left in place.
    """
    logs: list[str] = []
    remaining = []
    for ev in state.calendar:
        is_today = ev.day == state.day and ev.month == state.month
        if is_today and ev.auto_resolve:
            msg = _apply_calendar_event(state, ev)
            if msg:
                logs.append(msg)
            continue
        if is_today and ev.kind == "loan_due":
            msg = _fire_loan_due(state, ev)
            if msg:
                logs.append(msg)
            continue
        remaining.append(ev)
    state.calendar = remaining
    return logs


def _fire_loan_due(state: GameState, ev) -> str:
    """Find first matching active loan, attempt payment, push inbox on miss."""
    loan_index = next(
        (i for i, l in enumerate(state.loans) if l.due_day == ev.day and l.remaining > 0),
        None,
    )
    if loan_index is None:
        return ""
    _, msg = F.make_loan_payment(state, loan_index)
    if "MISSED" in msg:
        loan = state.loans[loan_index]
        event_id = f"cal_loan_due_{ev.month}_{ev.day}_{loan_index}"
        # Dedup: skip if the same synthetic entry is already pending.
        if any(r.event_id == event_id and r.status != "resolved" for r in state.inbox):
            return msg
        pay = loan.monthly_payment
        state.inbox.append(
            EventRef(
                event_id=event_id,
                received_day=state.day,
                received_month=state.month,
                status="unread",
                event={
                    "slug": f"cal_loan_due_m{ev.month}",
                    "sender": "Bank",
                    "title": f"Missed {loan.kind} loan payment — action required",
                    "body": (
                        f"Your {loan.kind} loan payment of **{pay/100:.2f} PLN** was missed "
                        "due to insufficient funds. Pick a way out before it hurts your score further."
                    ),
                    "options": [
                        {
                            "id": "opt_pay_now",
                            "label": "Pay from checking",
                            "skill_check": None,
                            "effects_on_success": {"money": -pay},
                            "effects_on_failure": {"money": -pay},
                        },
                        {
                            "id": "opt_from_savings",
                            "label": "Transfer from savings",
                            "skill_check": None,
                            "effects_on_success": {"money": 0},
                            "effects_on_failure": {"money": 0},
                        },
                        {
                            "id": "opt_payday_loan",
                            "label": "Take a payday loan to cover",
                            "skill_check": None,
                            "effects_on_success": {},
                            "effects_on_failure": {},
                        },
                        {
                            "id": "opt_skip",
                            "label": "Let it slide",
                            "skill_check": None,
                            "effects_on_success": {"credit_score": -15, "sanity": -5},
                            "effects_on_failure": {"credit_score": -15, "sanity": -5},
                        },
                    ],
                },
            )
        )
    return msg


# ---- Calendar event resolver ---------------------------------------------------


def _parse_loan_due_event_id(event_id: str) -> tuple[int, int, int] | None:
    """Parse `cal_loan_due_{month}_{day}_{loan_index}`; return None on mismatch."""
    prefix = "cal_loan_due_"
    if not event_id.startswith(prefix):
        return None
    try:
        month_s, day_s, idx_s = event_id[len(prefix):].split("_")
        return int(month_s), int(day_s), int(idx_s)
    except ValueError:
        return None


def _no_check_result(passed: bool, effects_applied: dict, note: str = "") -> dict:
    """Shape compatible with Track C's resolution panel for null skill-check events."""
    return {
        "rolled": None,
        "skill": None,
        "skill_value": None,
        "dc": None,
        "total": None,
        "passed": passed,
        "effects_applied": effects_applied,
        "note": note,
    }


def resolve_calendar_event(state: GameState, event_id: str, option_id: str) -> dict:
    """Resolve a synthetic `cal_*` inbox entry. No d20 roll.

    Currently only loan_due synthetic entries exist. Returns a resolution dict
    shaped like `sage.resolve_event`'s output so the UI renders uniformly.
    Raises ValueError on unknown event/option.
    """
    ref = next((r for r in state.inbox if r.event_id == event_id), None)
    if ref is None:
        raise ValueError(f"event_id not in inbox: {event_id}")
    if ref.status == "resolved":
        raise ValueError(f"event already resolved: {event_id}")

    parsed = _parse_loan_due_event_id(event_id)
    if parsed is None:
        raise ValueError(f"unsupported calendar event_id: {event_id}")
    _month, _day, loan_index = parsed

    if loan_index < 0 or loan_index >= len(state.loans):
        ref.status = "resolved"
        return _no_check_result(True, {}, note="loan no longer exists")

    loan = state.loans[loan_index]
    if loan.remaining <= 0:
        ref.status = "resolved"
        return _no_check_result(True, {}, note="loan already paid")

    pay = loan.monthly_payment

    if option_id == "opt_pay_now":
        before = state.accounts.checking
        _, msg = F.make_loan_payment(state, loan_index)
        spent = before - state.accounts.checking
        if "MISSED" in msg:
            return _no_check_result(
                False, {}, note="still couldn't pay — checking too low"
            )
        ref.status = "resolved"
        return _no_check_result(True, {"money": -spent}, note=msg)

    if option_id == "opt_from_savings":
        shortfall = max(0, pay - state.accounts.checking)
        moved = min(shortfall, state.accounts.savings)
        state.accounts.savings -= moved
        state.accounts.checking += moved
        before = state.accounts.checking
        _, msg = F.make_loan_payment(state, loan_index)
        spent = before - state.accounts.checking
        if "MISSED" in msg:
            return _no_check_result(
                False,
                {"money": 0},
                note=f"savings had only {moved/100:.2f} PLN — still short",
            )
        ref.status = "resolved"
        return _no_check_result(
            True,
            {"money": -spent},
            note=f"transferred {moved/100:.2f} PLN from savings, then paid",
        )

    if option_id == "opt_payday_loan":
        _, take_msg = F.take_loan(state, "payday", pay)
        before = state.accounts.checking
        _, pay_msg = F.make_loan_payment(state, loan_index)
        spent = before - state.accounts.checking
        ref.status = "resolved"
        return _no_check_result(
            True,
            {"money": -spent},
            note=f"{take_msg}; {pay_msg}",
        )

    if option_id == "opt_skip":
        from game import sage as _sage
        applied = _sage.apply_effects(state, {"credit_score": -15, "sanity": -5})
        ref.status = "resolved"
        return _no_check_result(True, applied, note="Let it slide — score takes a hit")

    raise ValueError(f"unknown option_id for calendar event: {option_id}")


def _apply_calendar_event(state, ev) -> str:
    if ev.kind == "payday":
        _, m = F.pay_salary(state)
        return m
    if ev.kind == "rent_due":
        _, m = F.charge_rent(state)
        return m
    if ev.kind == "heating_bill":
        _, m = F.charge_heating(state, ev.month)
        return m
    if ev.kind == "cc_due":
        _, m = F.charge_credit_card_bill(state)
        return m
    if ev.kind == "loan_due":
        # find first loan of matching amount-ish; for seeded path fire first
        if state.loans:
            _, m = F.make_loan_payment(state, 0)
            return m
    return ""


def _rollover_month(state: GameState) -> list[str]:
    logs: list[str] = []
    state.month += 1
    state.day = 1
    state.day_of_week = 0  # months always start on Monday
    state.player.workdays_this_month = 0
    state.flags.pop("took_bnpl_this_month", None)
    state.flags["monthly_expenses"] = []

    _, interest_logs = F.apply_monthly_interest(state)
    logs.extend(interest_logs)

    _, score_msg = F.update_credit_score(state)
    logs.append(score_msg)

    state.calendar.extend(
        seed_month_calendar(state.month, state.house.monthly_rent, has_cc=state.credit_card is not None)
    )
    for loan in state.loans:
        if loan.remaining > 0:
            state.calendar.append(
                CalendarEvent(
                    day=loan.due_day,
                    month=state.month,
                    kind="loan_due",
                    amount=loan.monthly_payment,
                    auto_resolve=False,
                )
            )
    return logs


def budget_required(state: GameState) -> bool:
    """True when a new-month budget must be set before time can advance.

    Month 1 starts with sensible defaults so the player isn't blocked immediately.
    From month 2 onward, `/api/set-budget` must stamp `budget_set_month` for the
    current month before any day-tick endpoint will proceed.
    """
    if state.month <= 1:
        return False
    return state.flags.get("budget_set_month") != state.month


def advance_day(state: GameState) -> tuple[GameState, list[str]]:
    """Advance exactly one day. Returns (state, log lines)."""
    if state.game_over is not None:
        return state, ["game over"]

    logs: list[str] = []
    _apply_stat_decay(state)
    _, food_log = F.apply_daily_food(state)
    if food_log:
        logs.append(food_log)

    is_weekday = state.day_of_week < 5
    if is_weekday:
        state.player.workdays_this_month += 1

    logs.extend(_fire_calendar_for_today(state))

    # Passive rest (T3.18): if the player didn't spend today's action slot, they slept.
    if state.actions_today > 0:
        state.player.stats["sanity"] = min(
            B.STAT_MAX, state.player.stats["sanity"] + B.REST_SANITY
        )
        state.player.stats["energy"] = min(
            B.STAT_MAX, state.player.stats["energy"] + B.REST_ENERGY
        )
        logs.append(f"Slept — +{B.REST_SANITY} sanity, +{B.REST_ENERGY} energy")

    # Advance pointers
    state.day += 1
    state.day_of_week = (state.day_of_week + 1) % 7
    state.actions_today = 1

    if state.day > B.MONTH_LEN:
        logs.extend(_rollover_month(state))

    check_game_over(state)
    return state, logs


def advance_until_event(
    state: GameState,
    max_days: int = 28,
    rng: _rand.Random | None = None,
) -> tuple[GameState, list[str], str, dict | None]:
    """Tick days until something notable happens.

    Stopping conditions (in priority): game over, month rollover, any calendar
    log emitted, SAGE probability roll fires on a quiet day, or `max_days` hit.
    Returns (state, logs, reason, event_or_none). `event` is non-null only when
    `reason == "sage_event"`.
    """
    rng = rng or _rand.Random()
    logs: list[str] = []
    if budget_required(state):
        return state, logs, "budget_required", None
    days_ticked = 0
    while days_ticked < max_days:
        start_month = state.month
        state, day_logs = advance_day(state)
        if budget_required(state):
            return state, logs + day_logs, "budget_required", None
        days_ticked += 1
        logs.extend(day_logs)
        if state.game_over is not None:
            return state, logs, "game_over", None
        if state.month != start_month:
            return state, logs, "month_rollover", None
        notable = [l for l in day_logs if not l.startswith("Slept —")]
        if notable:
            return state, logs, "calendar_event", None
        # Quiet day — roll SAGE probability gate.
        if rng.random() < sage.event_probability(state):
            if sage.OLLAMA_AVAILABLE:
                event, _src = sage.generate_event_via_llm(state, sage.call_ollama, rng=rng)
            else:
                event = sage.generate_event(state, rng=rng)
            sage.push_to_inbox(state, event)
            return state, logs, "sage_event", event
    return state, logs, "max_days", None


# ---- Player actions ------------------------------------------------------------


def practice_skill(state: GameState, skill: str) -> tuple[GameState, str]:
    import random as _r
    if skill not in B.SKILL_KEYS:
        return state, f"unknown skill: {skill}"
    if state.actions_today <= 0:
        return state, "no action left today"
    if state.player.skills[skill] >= B.SKILL_MAX:
        return state, f"{skill} is maxed"

    attempts = state.player.skill_practice_counts.get(skill, 0)
    prob = min(
        B.SKILL_PRACTICE_CAP,
        B.SKILL_PRACTICE_BASE + B.SKILL_PRACTICE_STEP * attempts,
    )
    rng = _r.Random(state.seed + state.day * 31 + state.month * 1009 + hash(skill) % 1000)
    state.seed = rng.randint(1, 2**31 - 1)  # advance deterministic seed
    state.actions_today = 0

    if rng.random() < prob:
        state.player.skills[skill] += 1
        state.player.skill_practice_counts[skill] = 0
        return state, f"Practiced {skill}: LEVEL UP to {state.player.skills[skill]}"
    state.player.skill_practice_counts[skill] = attempts + 1
    return state, f"Practiced {skill}: no progress (attempt {attempts+1}, {int(prob*100)}% chance)"


def rest(state: GameState) -> tuple[GameState, str]:
    if state.actions_today <= 0:
        return state, "no action left today"
    state.player.stats["sanity"] = min(B.STAT_MAX, state.player.stats["sanity"] + B.REST_SANITY)
    state.player.stats["energy"] = min(B.STAT_MAX, state.player.stats["energy"] + B.REST_ENERGY)
    state.actions_today = 0
    return state, f"Rested: +{B.REST_SANITY} sanity, +{B.REST_ENERGY} energy"


def set_budget(state: GameState, budget: dict) -> tuple[GameState, str]:
    """Stores budget in flags. `food_tier` is mechanical; other lines are visual.

    Accepts int values for arbitrary envelope labels (food/leisure/bills_buffer,
    all cosmetic) plus an optional `food_tier` in {cheap, normal, premium}.
    """
    stored: dict = {}
    for k, v in budget.items():
        if k == "food_tier":
            if v not in B.FOOD_TIER_ORDER:
                return state, f"invalid food_tier: {v}"
            stored["food_tier"] = v
        else:
            stored[k] = int(v)
    existing = state.flags.get("budget", {})
    existing.update(stored)
    state.flags["budget"] = existing
    state.flags["budget_set_month"] = state.month
    return state, f"Budget set for month {state.month}"
