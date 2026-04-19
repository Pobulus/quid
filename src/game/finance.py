"""Pure finance functions: (state) -> (state, log_message).

No I/O, no globals beyond `balance`. Money is always grosze (int).
"""

from __future__ import annotations

from game import balance as B
from game.state import CalendarEvent, CreditCard, Deposit, GameState, Loan


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _record_expense(state: GameState, label: str, amount: int) -> None:
    """Append a line to `state.flags['monthly_expenses']` so the UI can show
    where this month's money went. Amount is positive grosze spent."""
    if amount <= 0:
        return
    expenses = state.flags.setdefault("monthly_expenses", [])
    expenses.append({"label": label, "amount": int(amount)})


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
    _record_expense(state, "Rent", rent)
    return state, f"Rent: -{rent/100:.2f} PLN"


def apply_daily_food(state: GameState) -> tuple[GameState, str | None]:
    """Daily food charge + stat deltas. Resolves current tier each day so
    mid-month budget changes take effect immediately. Walks down tiers if
    checking can't cover today's cost; if nothing is affordable, applies
    cheap-tier stat penalties and skips the hunger restore (scraps day)."""
    chosen = state.flags.get("budget", {}).get("food_tier", B.FOOD_DEFAULT_TIER)
    if chosen not in B.FOOD_TIER_ORDER:
        chosen = B.FOOD_DEFAULT_TIER
    chosen_idx = B.FOOD_TIER_ORDER.index(chosen)
    shift = state.player.skills.get("cooking", 0) // B.COOKING_SHIFT_DIVISOR
    effective_idx = min(len(B.FOOD_TIER_ORDER) - 1, chosen_idx + shift)

    afforded_idx = None
    for idx in range(effective_idx, -1, -1):
        cost = B.FOOD_TIERS[B.FOOD_TIER_ORDER[idx]]["cost"]
        if state.accounts.checking >= cost:
            afforded_idx = idx
            break

    stats = state.player.stats
    if afforded_idx is None:
        cfg = B.FOOD_TIERS["cheap"]
        for key in ("health", "sanity", "energy"):
            stats[key] = _clamp(stats[key] + cfg[key], 0, B.STAT_MAX)
        return state, "No food today — eating scraps"

    tier_name = B.FOOD_TIER_ORDER[afforded_idx]
    cfg = B.FOOD_TIERS[tier_name]
    state.accounts.checking -= cfg["cost"]
    _bump_food_expense(state, tier_name, cfg["cost"])
    stats["hunger"] = _clamp(stats["hunger"] + cfg["daily_hunger"], 0, B.STAT_MAX)
    for key in ("health", "sanity", "energy"):
        stats[key] = _clamp(stats[key] + cfg[key], 0, B.STAT_MAX)
    return state, None  # no per-day log spam; expense card shows the running total


def _bump_food_expense(state: GameState, tier_name: str, amount: int) -> None:
    """Aggregate today's food cost into a single per-tier line in monthly_expenses."""
    label = f"Food ({tier_name})"
    expenses = state.flags.setdefault("monthly_expenses", [])
    for line in expenses:
        if line.get("label") == label:
            line["amount"] = int(line.get("amount", 0)) + int(amount)
            return
    expenses.append({"label": label, "amount": int(amount)})


def charge_heating(state: GameState, month: int) -> tuple[GameState, str]:
    mult = B.HEATING_MONTH_MULTIPLIER.get(month, 1.0)
    amount = int(B.HEATING_BASE * mult)
    state.accounts.checking -= amount
    _record_expense(state, "Heating", amount)
    return state, f"Heating: -{amount/100:.2f} PLN"


# ---- Interest ------------------------------------------------------------------


def apply_monthly_interest(state: GameState) -> tuple[GameState, list[str]]:
    """Applied at month rollover. Savings grows, loan balances grow, CC carried balance grows."""
    logs: list[str] = []

    # Savings interest — tier chosen explicitly via set_savings_tier (T3.22).
    if state.accounts.savings > 0:
        rate = (
            B.SAVINGS_PREMIUM_MONTHLY_RATE
            if state.accounts.savings_tier == "premium"
            else B.SAVINGS_BASIC_MONTHLY_RATE
        )
        gain = int(state.accounts.savings * rate)
        state.accounts.savings += gain
        if gain > 0:
            logs.append(f"Savings interest: +{gain/100:.2f} PLN")

    # Fixed-term deposit accrues at a locked premium rate.
    dep = state.accounts.deposit
    if dep and dep.principal > 0:
        dep_gain = int(dep.principal * B.DEPOSIT_MONTHLY_RATE)
        dep.principal += dep_gain
        if dep_gain > 0:
            logs.append(f"Deposit interest: +{dep_gain/100:.2f} PLN")

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
        _record_expense(state, "CC min payment", min_payment)
        return state, f"CC min payment: -{min_payment/100:.2f} PLN"
    state.flags.setdefault("cc_payments_missed", 0)
    state.flags["cc_payments_missed"] += 1
    return state, "CC min payment MISSED (insufficient funds)"


def pay_credit_card(state: GameState, amount: int) -> tuple[GameState, str]:
    """Player-initiated CC payment. Extra / full payoff.

    `flags.cc_payments_made` is only incremented when the payment covers the
    current statement minimum — partial payments below that don't game the
    on-time-history slice of `update_credit_score`.
    """
    cc = state.credit_card
    if cc is None:
        raise ValueError("No credit card on file.")
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("Amount must be a positive integer (grosze).")
    if amount > cc.balance:
        raise ValueError("Amount exceeds the card balance.")
    if amount > state.accounts.checking:
        raise ValueError("Not enough in checking to cover this payment.")
    min_payment = max(1, int(cc.balance * cc.min_payment_pct))
    state.accounts.checking -= amount
    cc.balance -= amount
    if amount >= min_payment:
        state.flags.setdefault("cc_payments_made", 0)
        state.flags["cc_payments_made"] += 1
    _record_expense(state, "CC payment", amount)
    return state, f"CC payment: -{amount/100:.2f} PLN"


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
        _record_expense(state, f"Loan payment ({loan.kind})", pay)
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


def transfer(state: GameState, direction: str, amount: int) -> tuple[GameState, str]:
    """Move integer grosze between checking and savings. Raises ValueError on
    bad direction, non-positive amount, or insufficient funds in the source."""
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("amount must be a positive integer (grosze)")
    if direction == "to_savings":
        if state.accounts.checking < amount:
            raise ValueError("Not enough in checking to transfer.")
        state.accounts.checking -= amount
        state.accounts.savings += amount
        return state, f"Transferred {amount/100:.2f} PLN to savings."
    if direction == "to_checking":
        if state.accounts.savings < amount:
            raise ValueError("Not enough in savings to transfer.")
        state.accounts.savings -= amount
        state.accounts.checking += amount
        return state, f"Transferred {amount/100:.2f} PLN to checking."
    raise ValueError(f"unknown direction: {direction}")


def apply_for_credit_card(state: GameState, tier: str) -> tuple[GameState, str]:
    """Issue a credit card in the given tier. Raises ValueError if the player
    already has one, the tier is unknown, or the unlock requirements aren't met."""
    if state.credit_card is not None:
        raise ValueError("You already have a credit card.")
    if tier == "starter":
        unlock_key = "cc_starter"
        limit, apr = B.CC_STARTER_LIMIT, B.CC_STARTER_APR
    elif tier == "better":
        unlock_key = "cc_better"
        limit, apr = B.CC_BETTER_LIMIT, B.CC_BETTER_APR
    else:
        raise ValueError(f"unknown credit card tier: {tier}")

    if unlock_key not in available_products(state):
        raise ValueError(f"{unlock_key} unlock requirements not met")

    state.credit_card = CreditCard(
        limit=limit,
        balance=0,
        apr=apr,
        due_day=B.CC_DUE_DAY,
        min_payment_pct=B.CC_MIN_PAYMENT_PCT,
    )
    # Seed cc_due for the current month if not already present.
    has_cc_due_this_month = any(
        e.kind == "cc_due" and e.month == state.month for e in state.calendar
    )
    if not has_cc_due_this_month:
        state.calendar.append(
            CalendarEvent(
                day=B.CC_DUE_DAY,
                month=state.month,
                kind="cc_due",
                amount=0,
                auto_resolve=True,
            )
        )
    return state, f"Approved: {tier} credit card, limit {limit/100:.0f} PLN @ {apr*100:.0f}% APR"


def set_savings_tier(state: GameState, tier: str) -> tuple[GameState, str]:
    """Switch the savings account between basic and premium tiers."""
    if tier not in B.SAVINGS_TIERS:
        raise ValueError(f"unknown savings tier: {tier}")
    if tier == "premium" and "savings_premium" not in available_products(state):
        raise ValueError("savings_premium unlock requirements not met")
    state.accounts.savings_tier = tier
    return state, f"Savings tier set to {tier}."


def open_deposit(
    state: GameState, amount: int, term_months: int
) -> tuple[GameState, str]:
    """Open a fixed-term deposit. Moves `amount` grosze from savings into
    `accounts.deposit` and stamps the current month."""
    if "deposit" not in available_products(state):
        raise ValueError("deposit unlock requirements not met")
    if state.accounts.deposit is not None:
        raise ValueError("A deposit is already open.")
    if term_months not in B.DEPOSIT_TERMS:
        raise ValueError(f"term_months must be one of {B.DEPOSIT_TERMS}")
    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("Amount must be a positive integer (grosze).")
    if amount > state.accounts.savings:
        raise ValueError("Not enough in savings to open this deposit.")
    state.accounts.savings -= amount
    state.accounts.deposit = Deposit(
        principal=amount, opened_month=state.month, term_months=term_months
    )
    return state, f"Deposit opened: {amount/100:.2f} PLN for {term_months} months."


def close_deposit(state: GameState) -> tuple[GameState, str]:
    """Close the deposit and release principal + accrued interest to savings.
    If closed before term, a percentage of the current principal is forfeited."""
    dep = state.accounts.deposit
    if dep is None:
        raise ValueError("No deposit is open.")
    months_elapsed = state.month - dep.opened_month
    if months_elapsed < dep.term_months:
        penalty = int(dep.principal * B.DEPOSIT_EARLY_PENALTY_PCT)
        payout = dep.principal - penalty
        state.accounts.savings += payout
        state.accounts.deposit = None
        return state, (
            f"Deposit closed early: -{penalty/100:.2f} PLN penalty, "
            f"+{payout/100:.2f} PLN to savings."
        )
    payout = dep.principal
    state.accounts.savings += payout
    state.accounts.deposit = None
    return state, f"Deposit matured: +{payout/100:.2f} PLN to savings."


def move_house(state: GameState, target_tier: str) -> tuple[GameState, str]:
    """Move to a different rental tier (upgrade or downgrade).

    Upgrade: charges `MOVE_UPGRADE_FEE` + `DEPOSIT_RENT_MULTIPLIER × new rent`
    from checking. Stores the paid deposit in `flags.house_deposit_paid` so a
    later downgrade can refund it.
    Downgrade: charges `MOVE_DOWNGRADE_FEE` and refunds the previously paid
    deposit (if any) to checking.
    Raises ValueError on unknown tier, same-tier moves, missing unlock (upgrade
    only), or insufficient funds.
    """
    if target_tier not in B.HOUSE_TIERS:
        raise ValueError(f"unknown house tier: {target_tier}")
    current = state.house.tier
    if target_tier == current:
        raise ValueError("Already at that tier.")

    order = B.HOUSE_TIER_ORDER
    is_upgrade = order.index(target_tier) > order.index(current)
    cfg = B.HOUSE_TIERS[target_tier]

    if is_upgrade:
        unlock_key = f"move_{target_tier.split('_rental')[0]}_rental"
        if unlock_key not in available_products(state):
            raise ValueError(f"{unlock_key} unlock requirements not met")
        deposit = B.DEPOSIT_RENT_MULTIPLIER * cfg["rent"]
        total_cost = B.MOVE_UPGRADE_FEE + deposit
        if state.accounts.checking < total_cost:
            raise ValueError("Not enough in checking to cover move-in cost.")
        state.accounts.checking -= total_cost
        prior_deposit = state.flags.get("house_deposit_paid", 0)
        state.flags["house_deposit_paid"] = prior_deposit + deposit
        _record_expense(state, f"Moving → {target_tier}", total_cost)
        msg = (
            f"Moved to {target_tier}: -{B.MOVE_UPGRADE_FEE/100:.0f} PLN fee, "
            f"-{deposit/100:.0f} PLN deposit."
        )
    else:
        if state.accounts.checking < B.MOVE_DOWNGRADE_FEE:
            raise ValueError("Not enough in checking to cover moving fee.")
        state.accounts.checking -= B.MOVE_DOWNGRADE_FEE
        refund = state.flags.get("house_deposit_paid", 0)
        if refund > 0:
            state.accounts.checking += refund
            state.flags["house_deposit_paid"] = 0
        _record_expense(state, f"Moving → {target_tier}", B.MOVE_DOWNGRADE_FEE)
        msg = (
            f"Moved to {target_tier}: -{B.MOVE_DOWNGRADE_FEE/100:.0f} PLN fee"
            + (f", +{refund/100:.0f} PLN deposit refund." if refund else ".")
        )

    state.house.tier = target_tier
    state.house.monthly_rent = cfg["rent"]
    state.house.shoddiness = cfg["shoddiness"]
    state.house.durability = cfg["durability"]
    state.house.distance_to_work_km = cfg["distance_to_work_km"]
    return state, msg


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
