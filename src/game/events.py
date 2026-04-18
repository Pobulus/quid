"""Day-tick engine and calendar processing. Pure functions.

`advance_day` is the atomic unit: apply decay, increment workdays if applicable,
fire today's calendar events, roll month over if needed, check game-over.
"""

from __future__ import annotations

from game import balance as B
from game import finance as F
from game.state import GameOver, GameState, seed_month_calendar


GAME_OVER_FLAVOR = {
    "health": "You collapse from exhaustion. Game over.",
    "hunger": "Starvation claims you. Game over.",
    "sanity": "Reality slips away. Game over.",
    "energy": "You can't get out of bed anymore. Game over.",
}


def _apply_stat_decay(state: GameState) -> None:
    for key, amt in B.STAT_DECAY_PER_DAY.items():
        state.player.stats[key] = max(0, state.player.stats[key] - amt)


def _apply_food_drip(state: GameState) -> None:
    drip = state.flags.get("food_daily_hunger", 0)
    if drip:
        state.player.stats["hunger"] = min(B.STAT_MAX, state.player.stats["hunger"] + drip)


def check_game_over(state: GameState) -> GameState:
    if state.game_over is not None:
        return state
    for key in B.STAT_KEYS:
        if state.player.stats[key] <= B.GAME_OVER_STAT:
            state.game_over = GameOver(cause=key, flavor=GAME_OVER_FLAVOR[key])
            return state
    return state


def _fire_calendar_for_today(state: GameState) -> list[str]:
    """Fire (and remove) all auto-resolve calendar events matching today."""
    logs: list[str] = []
    remaining = []
    for ev in state.calendar:
        if ev.day == state.day and ev.month == state.month and ev.auto_resolve:
            msg = _apply_calendar_event(state, ev)
            if msg:
                logs.append(msg)
        else:
            remaining.append(ev)
    state.calendar = remaining
    return logs


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

    _, interest_logs = F.apply_monthly_interest(state)
    logs.extend(interest_logs)

    _, score_msg = F.update_credit_score(state)
    logs.append(score_msg)

    _, food_logs = F.apply_monthly_food(state)
    logs.extend(food_logs)

    state.calendar.extend(
        seed_month_calendar(state.month, state.house.monthly_rent, has_cc=state.credit_card is not None)
    )
    return logs


def advance_day(state: GameState) -> tuple[GameState, list[str]]:
    """Advance exactly one day. Returns (state, log lines)."""
    if state.game_over is not None:
        return state, ["game over"]

    logs: list[str] = []
    _apply_stat_decay(state)
    _apply_food_drip(state)

    is_weekday = state.day_of_week < 5
    if is_weekday:
        state.player.workdays_this_month += 1

    logs.extend(_fire_calendar_for_today(state))

    # Advance pointers
    state.day += 1
    state.day_of_week = (state.day_of_week + 1) % 7
    state.actions_today = 1

    if state.day > B.MONTH_LEN:
        logs.extend(_rollover_month(state))

    check_game_over(state)
    return state, logs


def advance_until_event(state: GameState, max_days: int = 28) -> tuple[GameState, list[str], str]:
    """Tick days until something notable happens.

    Stopping conditions: game over, month rollover, any log emitted, or max_days hit.
    Returns (state, logs, reason).
    """
    logs: list[str] = []
    days_ticked = 0
    while days_ticked < max_days:
        start_month = state.month
        state, day_logs = advance_day(state)
        days_ticked += 1
        logs.extend(day_logs)
        if state.game_over is not None:
            return state, logs, "game_over"
        if state.month != start_month:
            return state, logs, "month_rollover"
        if day_logs:
            return state, logs, "calendar_event"
    return state, logs, "max_days"


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
    return state, f"Budget set for month {state.month}"
