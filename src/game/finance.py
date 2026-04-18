"""Pure finance functions: (state) -> (state, log_message).

No I/O, no globals beyond `balance`. Money is always grosze (int).
"""

from __future__ import annotations

from game import balance as B
from game.state import CalendarEvent, CreditCard, GameState, Loan


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


# ---- Income --------------------------------------------------------------------


def pay_salary(state: GameState) -> tuple[GameState, str]:
    """Credit net pay proportional to workdays actually worked this month."""
    p = state.player
    gross = int(p.salary_gross_monthly * (p.workdays_this_month / B.WORKDAYS_PER_MONTH))
    net = int(gross * (1 - p.tax_rate))
    state.accounts.checking += net
    msg = f"Salary: +{net/100:.2f} PLN (worked {p.workdays_this_month}/{B.WORKDAYS_PER_MONTH} days)"
    p.workdays_this_month = 0
    return state, msg


# ---- Recurring charges ---------------------------------------------------------


def charge_rent(state: GameState) -> tuple[GameState, str]:
    rent = state.house.monthly_rent
    state.accounts.checking -= rent
    return state, f"Rent: -{rent/100:.2f} PLN"


def apply_monthly_food(state: GameState) -> tuple[GameState, list[str]]:
    """Monthly food purchase + stat deltas. Sets the daily hunger drip for next month."""
    logs: list[str] = []
    chosen = state.flags.get("budget", {}).get("food_tier", B.FOOD_DEFAULT_TIER)
    if chosen not in B.FOOD_TIER_ORDER:
        chosen = B.FOOD_DEFAULT_TIER
    chosen_idx = B.FOOD_TIER_ORDER.index(chosen)
    shift = state.player.skills.get("cooking", 0) // B.COOKING_SHIFT_DIVISOR
    effective_idx = min(len(B.FOOD_TIER_ORDER) - 1, chosen_idx + shift)

    # Walk down until affordable
    afforded_idx = None
    for idx in range(effective_idx, -1, -1):
        cost = B.FOOD_TIERS[B.FOOD_TIER_ORDER[idx]]["cost"]
        if state.accounts.checking >= cost:
            afforded_idx = idx
            break

    if afforded_idx is None:
        # Can't afford even cheap — apply cheap penalties, no drip
        cfg = B.FOOD_TIERS["cheap"]
        state.flags["food_daily_hunger"] = 0
        _apply_food_stat_deltas(state, cfg)
        logs.append("Can't afford food this month — eating scraps")
        return state, logs

    tier_name = B.FOOD_TIER_ORDER[afforded_idx]
    cfg = B.FOOD_TIERS[tier_name]
    state.accounts.checking -= cfg["cost"]
    state.flags["food_daily_hunger"] = cfg["daily_hunger"]
    _apply_food_stat_deltas(state, cfg)
    suffix = f" (chosen {chosen}, cooking shift +{shift})" if afforded_idx != chosen_idx else ""
    logs.append(f"Food ({tier_name}): -{cfg['cost']/100:.2f} PLN{suffix}")
    return state, logs


def _apply_food_stat_deltas(state: GameState, cfg: dict) -> None:
    stats = state.player.stats
    for key in ("health", "sanity", "energy"):
        stats[key] = _clamp(stats[key] + cfg[key], 0, 100)


def charge_heating(state: GameState, month: int) -> tuple[GameState, str]:
    mult = B.HEATING_MONTH_MULTIPLIER.get(month, 1.0)
    amount = int(B.HEATING_BASE * mult)
    state.accounts.checking -= amount
    return state, f"Heating: -{amount/100:.2f} PLN"


# ---- Interest ------------------------------------------------------------------


def apply_monthly_interest(state: GameState) -> tuple[GameState, list[str]]:
    """Applied at month rollover. Savings grows, loan balances grow, CC carried balance grows."""
    logs: list[str] = []

    # Savings interest
    if state.accounts.savings > 0:
        rate = (
            B.SAVINGS_PREMIUM_MONTHLY_RATE
            if state.accounts.savings >= B.UNLOCK_TIERS["savings_premium"][1]
            else B.SAVINGS_BASIC_MONTHLY_RATE
        )
        gain = int(state.accounts.savings * rate)
        state.accounts.savings += gain
        if gain > 0:
            logs.append(f"Savings interest: +{gain/100:.2f} PLN")

    # Loan interest accrues on remaining
    for loan in state.loans:
        monthly_rate = loan.apr / 12
        interest = int(loan.remaining * monthly_rate)
        loan.remaining += interest

    # CC interest on carried balance
    cc = state.credit_card
    if cc and cc.balance > 0:
        monthly_rate = cc.apr / 12
        interest = int(cc.balance * monthly_rate)
        cc.balance += interest
        logs.append(f"CC interest: +{interest/100:.2f} PLN on balance")

    return state, logs


# ---- Credit card / loans -------------------------------------------------------


def charge_credit_card_bill(state: GameState) -> tuple[GameState, str]:
    cc = state.credit_card
    if cc is None or cc.balance <= 0:
        return state, ""
    min_payment = max(1, int(cc.balance * cc.min_payment_pct))
    if state.accounts.checking >= min_payment:
        state.accounts.checking -= min_payment
        cc.balance -= min_payment
        state.flags.setdefault("cc_payments_made", 0)
        state.flags["cc_payments_made"] += 1
        return state, f"CC min payment: -{min_payment/100:.2f} PLN"
    state.flags.setdefault("cc_payments_missed", 0)
    state.flags["cc_payments_missed"] += 1
    return state, "CC min payment MISSED (insufficient funds)"


def make_loan_payment(state: GameState, loan_index: int) -> tuple[GameState, str]:
    if loan_index < 0 or loan_index >= len(state.loans):
        return state, "invalid loan id"
    loan = state.loans[loan_index]
    if loan.remaining <= 0:
        return state, ""
    pay = min(loan.monthly_payment, loan.remaining)
    if state.accounts.checking >= pay:
        state.accounts.checking -= pay
        loan.remaining -= pay
        loan.payments_made += 1
        return state, f"Loan payment ({loan.kind}): -{pay/100:.2f} PLN"
    loan.payments_missed += 1
    return state, f"Loan payment ({loan.kind}) MISSED"


def take_loan(state: GameState, kind: str, amount: int) -> tuple[GameState, str]:
    if kind == "personal":
        if state.credit_score < B.UNLOCK_TIERS["personal_loan"][0]:
            return state, "Personal loan unavailable (credit score too low)"
        apr = B.PERSONAL_LOAN_APR
    elif kind == "payday":
        apr = B.PAYDAY_LOAN_APR
    else:
        return state, f"unknown loan kind: {kind}"

    monthly_payment = max(1, int(amount / 12 * (1 + apr / 2)))  # crude 12-month amort
    due_day = B.RENT_DUE_DAY + 10
    state.loans.append(
        Loan(
            kind=kind,
            principal=amount,
            remaining=amount,
            apr=apr,
            monthly_payment=monthly_payment,
            due_day=due_day,
            payments_made=0,
            payments_missed=0,
        )
    )
    state.accounts.checking += amount
    _seed_loan_due(state, due_day, monthly_payment)
    return state, f"Took {kind} loan: +{amount/100:.2f} PLN @ {apr*100:.0f}% APR"


def take_bnpl(state: GameState, amount: int) -> tuple[GameState, str]:
    due_day = (state.day + B.BNPL_GRACE_DAYS - 1) % B.MONTH_LEN + 1
    state.loans.append(
        Loan(
            kind="bnpl",
            principal=amount,
            remaining=amount,
            apr=0.0,  # becomes BNPL_POST_GRACE_APR after grace days
            monthly_payment=amount,
            due_day=due_day,
            payments_made=0,
            payments_missed=0,
        )
    )
    state.flags["took_bnpl_this_month"] = True
    due_month = state.month if due_day >= state.day else state.month + 1
    _seed_loan_due(state, due_day, amount, month=due_month)
    return state, f"BNPL: +{amount/100:.2f} PLN (0% for {B.BNPL_GRACE_DAYS}d)"


def _seed_loan_due(state: GameState, due_day: int, amount: int, month: int | None = None) -> None:
    m = month if month is not None else state.month
    state.calendar.append(
        CalendarEvent(day=due_day, month=m, kind="loan_due", amount=amount, auto_resolve=False)
    )


# ---- Credit score --------------------------------------------------------------


def update_credit_score(state: GameState) -> tuple[GameState, str]:
    """Recompute monthly. Weighted: history / utilization / age."""
    w = B.CREDIT_SCORE_WEIGHTS
    made = state.flags.get("cc_payments_made", 0) + sum(l.payments_made for l in state.loans)
    missed = state.flags.get("cc_payments_missed", 0) + sum(l.payments_missed for l in state.loans)
    total = made + missed
    history_pct = (made / total) if total else 1.0

    cc = state.credit_card
    if cc and cc.limit > 0:
        utilization = cc.balance / cc.limit
        util_pct = max(0.0, 1.0 - utilization)  # low util = good
    else:
        util_pct = 0.7  # neutral when no CC

    age_months = (state.month - 1) + state.day / B.MONTH_LEN
    age_pct = min(1.0, age_months / 24)  # caps at 2 years

    composite = w["history"] * history_pct + w["utilization"] * util_pct + w["age"] * age_pct
    new_score = int(B.CREDIT_SCORE_MIN + composite * (B.CREDIT_SCORE_MAX - B.CREDIT_SCORE_MIN))
    new_score = _clamp(new_score, B.CREDIT_SCORE_MIN, B.CREDIT_SCORE_MAX)
    delta = new_score - state.credit_score
    state.credit_score = new_score
    sign = "+" if delta >= 0 else ""
    return state, f"Credit score: {new_score} ({sign}{delta})"


# ---- Queries -------------------------------------------------------------------


def net_worth(state: GameState) -> int:
    nw = state.accounts.checking + state.accounts.savings
    nw -= sum(l.remaining for l in state.loans)
    if state.credit_card:
        nw -= state.credit_card.balance
    return nw


def available_products(state: GameState) -> list[str]:
    nw = net_worth(state)
    out = []
    for name, (min_score, min_nw) in B.UNLOCK_TIERS.items():
        if min_score is not None and state.credit_score < min_score:
            continue
        if min_nw is not None and nw < min_nw:
            continue
        out.append(name)
    return out
