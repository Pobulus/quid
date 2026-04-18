from unittest.mock import patch

from django.test import TestCase
from pydantic import ValidationError

from game import balance as B
from game import events, finance, sage
from game.state import CalendarEvent, CreditCard, Loan, new_game


class _RngStub:
    """Deterministic stand-in for random.Random — returns queued values for .random()."""

    def __init__(self, values):
        self._values = list(values)

    def random(self):
        return self._values.pop(0)

    def randint(self, a, b):
        return a


class PracticeSkillTest(TestCase):
    def _fresh(self):
        s = new_game(seed=42)
        s.player.skills["cooking"] = 0
        s.player.skill_practice_counts["cooking"] = 0
        return s

    def test_formula_matches_balance(self):
        for attempts in range(0, 10):
            expected = min(
                B.SKILL_PRACTICE_CAP,
                B.SKILL_PRACTICE_BASE + B.SKILL_PRACTICE_STEP * attempts,
            )
            state = self._fresh()
            state.player.skill_practice_counts["cooking"] = attempts
            # Stub rng to return a value just above expected → miss, counter++
            stub = _RngStub([expected + 0.0001])
            with patch("random.Random", return_value=stub):
                state, msg = events.practice_skill(state, "cooking")
            self.assertEqual(state.player.skills["cooking"], 0, msg)
            self.assertEqual(state.player.skill_practice_counts["cooking"], attempts + 1)

    def test_level_up_resets_counter(self):
        state = self._fresh()
        state.player.skill_practice_counts["cooking"] = 3
        stub = _RngStub([0.0])  # guaranteed hit
        with patch("random.Random", return_value=stub):
            state, msg = events.practice_skill(state, "cooking")
        self.assertEqual(state.player.skills["cooking"], 1)
        self.assertEqual(state.player.skill_practice_counts["cooking"], 0)
        self.assertIn("LEVEL UP", msg)

    def test_probability_caps(self):
        # attempts high enough that BASE + STEP*attempts > CAP
        high = int((B.SKILL_PRACTICE_CAP - B.SKILL_PRACTICE_BASE) / B.SKILL_PRACTICE_STEP) + 5
        state = self._fresh()
        state.player.skill_practice_counts["cooking"] = high
        # roll just above CAP should still miss (prob capped at CAP)
        stub = _RngStub([B.SKILL_PRACTICE_CAP + 0.001])
        with patch("random.Random", return_value=stub):
            state, _ = events.practice_skill(state, "cooking")
        self.assertEqual(state.player.skills["cooking"], 0)


class InteractiveLoanInboxTest(TestCase):
    """T2.7 — synthetic inbox entry for missed loan payments."""

    PAY = 20000  # 200 PLN

    def _state_with_missed_loan(self, *, checking=0, savings=0):
        s = new_game(seed=1)
        s.accounts.checking = checking
        s.accounts.savings = savings
        s.loans = [
            Loan(
                kind="bnpl",
                principal=self.PAY,
                remaining=self.PAY,
                apr=0.0,
                monthly_payment=self.PAY,
                due_day=s.day,
                payments_made=0,
                payments_missed=0,
            )
        ]
        ev = CalendarEvent(
            day=s.day, month=s.month, kind="loan_due",
            amount=self.PAY, auto_resolve=False,
        )
        events._fire_loan_due(s, ev)
        return s

    def test_miss_creates_inbox_entry_with_four_options(self):
        s = self._state_with_missed_loan(checking=0, savings=self.PAY * 2)
        self.assertEqual(len(s.inbox), 1)
        ref = s.inbox[0]
        self.assertEqual(ref.status, "unread")
        self.assertTrue(ref.event_id.startswith("cal_loan_due_"))
        ids = [o["id"] for o in ref.event["options"]]
        self.assertEqual(
            ids, ["opt_pay_now", "opt_from_savings", "opt_payday_loan", "opt_skip"]
        )
        self.assertEqual(s.loans[0].payments_missed, 1)

    def test_from_savings_pays_and_resolves(self):
        s = self._state_with_missed_loan(checking=0, savings=self.PAY * 2)
        eid = s.inbox[0].event_id
        before_savings = s.accounts.savings
        res = events.resolve_calendar_event(s, eid, "opt_from_savings")
        self.assertTrue(res["passed"])
        self.assertEqual(s.inbox[0].status, "resolved")
        self.assertEqual(s.loans[0].remaining, 0)
        self.assertEqual(before_savings - s.accounts.savings, self.PAY)

    def test_payday_loan_creates_new_loan_and_resolves(self):
        s = self._state_with_missed_loan(checking=0, savings=0)
        eid = s.inbox[0].event_id
        res = events.resolve_calendar_event(s, eid, "opt_payday_loan")
        self.assertTrue(res["passed"])
        self.assertEqual(s.inbox[0].status, "resolved")
        self.assertEqual(s.loans[0].remaining, 0)  # original paid
        payday_loans = [l for l in s.loans if l.kind == "payday"]
        self.assertEqual(len(payday_loans), 1)
        # new loan_due seeded on calendar
        self.assertTrue(any(e.kind == "loan_due" for e in s.calendar))

    def test_skip_applies_credit_penalty_and_resolves(self):
        s = self._state_with_missed_loan(checking=0, savings=0)
        eid = s.inbox[0].event_id
        score_before = s.credit_score
        sanity_before = s.player.stats["sanity"]
        remaining_before = s.loans[0].remaining
        res = events.resolve_calendar_event(s, eid, "opt_skip")
        self.assertTrue(res["passed"])
        self.assertEqual(s.inbox[0].status, "resolved")
        self.assertEqual(s.credit_score, score_before - 15)
        self.assertEqual(s.player.stats["sanity"], sanity_before - 5)
        self.assertEqual(s.loans[0].remaining, remaining_before)

    def test_pay_now_from_broke_reports_failure(self):
        s = self._state_with_missed_loan(checking=0, savings=0)
        eid = s.inbox[0].event_id
        missed_before = s.loans[0].payments_missed
        res = events.resolve_calendar_event(s, eid, "opt_pay_now")
        self.assertFalse(res["passed"])
        self.assertEqual(s.inbox[0].status, "unread")
        self.assertEqual(s.loans[0].payments_missed, missed_before + 1)

    def test_demo_new_game_seeds_opening_event(self):
        s = new_game(demo=True)
        self.assertEqual(s.seed, B.DEMO_SEED)
        self.assertEqual(len(s.inbox), 1)
        ref = s.inbox[0]
        self.assertEqual(ref.event["slug"], "demo_opening_bnpl")
        self.assertEqual(ref.status, "unread")
        self.assertEqual(len(ref.event["options"]), 3)

    def test_pay_now_succeeds_when_cash_arrived(self):
        s = self._state_with_missed_loan(checking=0, savings=0)
        eid = s.inbox[0].event_id
        s.accounts.checking = self.PAY  # payday landed between tick and click
        res = events.resolve_calendar_event(s, eid, "opt_pay_now")
        self.assertTrue(res["passed"])
        self.assertEqual(s.inbox[0].status, "resolved")
        self.assertEqual(s.loans[0].remaining, 0)


def _valid_event(**overrides) -> dict:
    ev = {
        "title": "Test event",
        "sender": "Nobody",
        "body": "Body.",
        "options": [
            {
                "id": "a",
                "label": "Do the thing",
                "skill_check": {"skill": "handiwork", "difficulty_class": 12},
                "effects_on_success": {"money": 1000},
                "effects_on_failure": {"money": -1000},
            },
            {
                "id": "b",
                "label": "Skip",
                "skill_check": None,
                "effects_on_success": {"sanity": -2},
                "effects_on_failure": {"sanity": -2},
            },
        ],
    }
    ev.update(overrides)
    return ev


class SageTest(TestCase):
    def test_validate_event_rejects_unknown_effect_key(self):
        ev = _valid_event()
        ev["options"][0]["effects_on_success"] = {"karma": 5}
        with self.assertRaises(ValidationError):
            sage.validate_event(ev)

    def test_validate_event_rejects_out_of_bounds_delta(self):
        ev = _valid_event()
        ev["options"][0]["effects_on_success"] = {"sanity": 999}
        ev["options"][0]["effects_on_failure"] = {"sanity": 999}
        with self.assertRaises(ValidationError):
            sage.validate_event(ev)

    def test_validate_event_rejects_no_check_with_differing_effects(self):
        ev = _valid_event()
        ev["options"][1]["effects_on_failure"] = {"sanity": -5}
        with self.assertRaises(ValidationError):
            sage.validate_event(ev)

    def test_validate_event_accepts_valid_payload(self):
        out = sage.validate_event(_valid_event())
        self.assertEqual(out["title"], "Test event")
        self.assertNotIn("event_id", out)

    def test_validate_single_assigns_uuid_event_id(self):
        ev = sage._validate_single(_valid_event())
        self.assertIn("event_id", ev)
        self.assertEqual(len(ev["event_id"]), 32)

    def test_validate_single_rejects_non_object(self):
        with self.assertRaises(ValueError):
            sage._validate_single([_valid_event()])

    def test_resolve_event_raises_when_already_resolved(self):
        s = new_game(seed=1)
        ev = _valid_event()
        ev["event_id"] = "abc123"
        sage.push_to_inbox(s, ev)
        sage.resolve_event(s, event_id="abc123", option_id="b", roll_d20=10)
        with self.assertRaises(ValueError):
            sage.resolve_event(s, event_id="abc123", option_id="b", roll_d20=10)

    def test_resolve_event_applies_success_branch(self):
        s = new_game(seed=1)
        s.player.skills["handiwork"] = 10
        before = s.accounts.checking
        ev = _valid_event()
        ev["event_id"] = "ev_pass"
        sage.push_to_inbox(s, ev)
        res = sage.resolve_event(s, event_id="ev_pass", option_id="a", roll_d20=20)
        self.assertTrue(res["passed"])
        self.assertEqual(s.accounts.checking - before, 1000)

    def test_resolve_event_applies_failure_branch(self):
        s = new_game(seed=1)
        s.player.skills["handiwork"] = 0
        before = s.accounts.checking
        ev = _valid_event()
        ev["event_id"] = "ev_fail"
        sage.push_to_inbox(s, ev)
        res = sage.resolve_event(s, event_id="ev_fail", option_id="a", roll_d20=1)
        self.assertFalse(res["passed"])
        self.assertEqual(s.accounts.checking - before, -1000)

    def test_resolve_event_rejects_bad_roll(self):
        s = new_game(seed=1)
        ev = _valid_event()
        ev["event_id"] = "ev_x"
        sage.push_to_inbox(s, ev)
        with self.assertRaises(ValueError):
            sage.resolve_event(s, event_id="ev_x", option_id="a", roll_d20=0)

    def test_generate_event_via_llm_falls_back_when_unavailable(self):
        s = new_game(seed=1)
        with patch.object(sage, "OLLAMA_AVAILABLE", False):
            ev, src = sage.generate_event_via_llm(s, call_fn=lambda *_: None)
        self.assertEqual(src, "fallback")
        self.assertIn("options", ev)

    def test_generate_event_via_llm_drains_prefetch_queue(self):
        s = new_game(seed=1)
        queued = _valid_event(title="Queued")
        queued["event_id"] = "pre_1"
        s.flags["event_queue"] = [queued, _valid_event(title="Queued2")]

        def fail_call(system, user):
            raise AssertionError("should not hit LLM when queue has items")

        with patch.object(sage, "OLLAMA_AVAILABLE", True):
            ev, src = sage.generate_event_via_llm(s, fail_call)
        self.assertEqual(src, "llm_queue")
        self.assertEqual(ev["event_id"], "pre_1")
        self.assertEqual(len(s.flags["event_queue"]), 1)

    def test_generate_single_event_via_llm_returns_one(self):
        s = new_game(seed=1)

        def fake_call(system, user):
            return _valid_event(title="Solo")

        with patch.object(sage, "OLLAMA_AVAILABLE", True):
            ev, src = sage.generate_single_event_via_llm(s, fake_call)
        self.assertEqual(src, "llm")
        self.assertEqual(ev["title"], "Solo")
        self.assertIn("event_id", ev)
        self.assertEqual(s.flags.get("event_queue", []), [])

    def test_generate_event_via_llm_retries_then_falls_back(self):
        s = new_game(seed=1)
        calls = {"n": 0}

        def bad_call(system, user):
            calls["n"] += 1
            return {"title": "bad", "sender": "x", "body": "y", "options": []}

        with patch.object(sage, "OLLAMA_AVAILABLE", True):
            ev, src = sage.generate_event_via_llm(s, bad_call)
        self.assertEqual(src, "fallback")
        self.assertEqual(calls["n"], 2)

    def test_event_probability_monotonic_with_stress(self):
        s = new_game(seed=1)
        base = sage.event_probability(s)
        s.player.stats["hunger"] = 0
        s.player.stats["sanity"] = 0
        stressed = sage.event_probability(s)
        self.assertGreaterEqual(stressed, base)


class FinanceTest(TestCase):
    def test_pay_salary_net_of_tax_full_month(self):
        s = new_game(seed=1)
        s.accounts.checking = 0
        s.player.workdays_this_month = B.WORKDAYS_PER_MONTH
        before_gross = s.player.salary_gross_monthly
        s, msg = finance.pay_salary(s)
        expected = int(before_gross * (1 - s.player.tax_rate))
        self.assertEqual(s.accounts.checking, expected)
        self.assertEqual(s.player.workdays_this_month, 0)
        self.assertIn("Salary", msg)

    def test_pay_salary_partial_workdays_proportional(self):
        s = new_game(seed=1)
        s.accounts.checking = 0
        s.player.workdays_this_month = B.WORKDAYS_PER_MONTH // 2
        s, _ = finance.pay_salary(s)
        full_gross = s.player.salary_gross_monthly
        expected = int(int(full_gross / 2) * (1 - s.player.tax_rate))
        self.assertEqual(s.accounts.checking, expected)

    def test_charge_rent_deducts_and_records_expense(self):
        s = new_game(seed=1)
        s.accounts.checking = s.house.monthly_rent + 10000
        s.flags["monthly_expenses"] = []
        s, msg = finance.charge_rent(s)
        self.assertEqual(s.accounts.checking, 10000)
        self.assertEqual(s.flags["monthly_expenses"][-1]["label"], "Rent")
        self.assertEqual(s.flags["monthly_expenses"][-1]["amount"], s.house.monthly_rent)

    def test_charge_rent_goes_negative_when_broke(self):
        # Rent is unconditional — MVP does not block payment, it just overdrafts.
        s = new_game(seed=1)
        s.accounts.checking = 0
        s, _ = finance.charge_rent(s)
        self.assertEqual(s.accounts.checking, -s.house.monthly_rent)

    def test_charge_heating_uses_seasonal_multiplier(self):
        s = new_game(seed=1)
        s.accounts.checking = 10**9
        base_before = s.accounts.checking
        s, _ = finance.charge_heating(s, month=1)  # 3x
        winter_cost = base_before - s.accounts.checking
        s.accounts.checking = 10**9
        s, _ = finance.charge_heating(s, month=7)  # 0.5x
        summer_cost = 10**9 - s.accounts.checking
        self.assertGreater(winter_cost, summer_cost)
        self.assertEqual(winter_cost, int(B.HEATING_BASE * 3.0))
        self.assertEqual(summer_cost, int(B.HEATING_BASE * 0.5))

    def test_update_credit_score_rises_with_on_time_payments(self):
        s = new_game(seed=1)
        s.credit_score = B.CREDIT_SCORE_MIN
        s.flags["cc_payments_made"] = 24
        s.flags["cc_payments_missed"] = 0
        s.month = 12
        before = s.credit_score
        s, _ = finance.update_credit_score(s)
        self.assertGreater(s.credit_score, before)

    def test_update_credit_score_drops_when_payments_missed(self):
        s = new_game(seed=1)
        s.flags["cc_payments_made"] = 0
        s.flags["cc_payments_missed"] = 10
        s, _ = finance.update_credit_score(s)
        good = s.credit_score
        s2 = new_game(seed=1)
        s2.flags["cc_payments_made"] = 10
        s2.flags["cc_payments_missed"] = 0
        s2, _ = finance.update_credit_score(s2)
        self.assertLess(good, s2.credit_score)

    def test_net_worth_sums_assets_minus_debts(self):
        s = new_game(seed=1)
        s.accounts.checking = 100000
        s.accounts.savings = 50000
        s.loans = [Loan(
            kind="personal", principal=20000, remaining=20000,
            apr=0.14, monthly_payment=2000, due_day=15,
            payments_made=0, payments_missed=0,
        )]
        s.credit_card = CreditCard(
            limit=100000, balance=10000, apr=0.34,
            due_day=25, min_payment_pct=0.05,
        )
        self.assertEqual(finance.net_worth(s), 100000 + 50000 - 20000 - 10000)

    def test_net_worth_no_debt(self):
        s = new_game(seed=1)
        s.accounts.checking = 42
        s.accounts.savings = 58
        s.loans = []
        s.credit_card = None
        self.assertEqual(finance.net_worth(s), 100)

    def test_take_loan_personal_blocked_below_credit_score(self):
        s = new_game(seed=1)
        s.credit_score = B.UNLOCK_TIERS["personal_loan"][0] - 1
        before_loans = len(s.loans)
        s, msg = finance.take_loan(s, "personal", 50000)
        self.assertEqual(len(s.loans), before_loans)
        self.assertIn("unavailable", msg)

    def test_take_loan_personal_success(self):
        s = new_game(seed=1)
        s.credit_score = B.UNLOCK_TIERS["personal_loan"][0] + 10
        before_cash = s.accounts.checking
        s, _ = finance.take_loan(s, "personal", 50000)
        self.assertEqual(len(s.loans), 1)
        self.assertEqual(s.accounts.checking, before_cash + 50000)
        self.assertTrue(any(e.kind == "loan_due" for e in s.calendar))

    def test_make_loan_payment_missed_when_broke(self):
        s = new_game(seed=1)
        s.accounts.checking = 0
        s.loans = [Loan(
            kind="personal", principal=20000, remaining=20000,
            apr=0.14, monthly_payment=5000, due_day=15,
            payments_made=0, payments_missed=0,
        )]
        s, msg = finance.make_loan_payment(s, 0)
        self.assertIn("MISSED", msg)
        self.assertEqual(s.loans[0].payments_missed, 1)
        self.assertEqual(s.loans[0].remaining, 20000)

    def test_charge_credit_card_bill_success_and_miss(self):
        s = new_game(seed=1)
        s.credit_card = CreditCard(
            limit=100000, balance=10000, apr=0.24,
            due_day=25, min_payment_pct=0.10,
        )
        s.accounts.checking = 100000
        s, msg = finance.charge_credit_card_bill(s)
        self.assertIn("CC min payment", msg)
        self.assertEqual(s.flags.get("cc_payments_made"), 1)
        # Now broke
        s.accounts.checking = 0
        s.credit_card.balance = 10000
        s, msg = finance.charge_credit_card_bill(s)
        self.assertIn("MISSED", msg)
        self.assertEqual(s.flags.get("cc_payments_missed"), 1)

    def test_apply_monthly_interest_grows_savings_and_cc(self):
        s = new_game(seed=1)
        s.accounts.savings = 100000
        s.credit_card = CreditCard(
            limit=100000, balance=50000, apr=0.24,
            due_day=25, min_payment_pct=0.05,
        )
        s, _ = finance.apply_monthly_interest(s)
        self.assertGreater(s.accounts.savings, 100000)
        self.assertGreater(s.credit_card.balance, 50000)

    def test_available_products_gates_by_score_and_networth(self):
        s = new_game(seed=1)
        s.credit_score = 600
        s.accounts.checking = 0
        s.accounts.savings = 0
        products = finance.available_products(s)
        self.assertIn("cc_starter", products)
        self.assertNotIn("personal_loan", products)
        self.assertNotIn("investments", products)

        s.credit_score = 800
        s.accounts.savings = 3_000_000  # 30k PLN
        products = finance.available_products(s)
        self.assertIn("personal_loan", products)
        self.assertIn("investments", products)


class EventsTest(TestCase):
    def test_rollover_increments_month_and_reseeds_calendar(self):
        s = new_game(seed=1)
        s.day = B.MONTH_LEN  # day 28
        s.calendar = []  # start with empty so we can inspect reseeding
        logs = events._rollover_month(s)
        self.assertEqual(s.month, 2)
        self.assertEqual(s.day, 1)
        self.assertEqual(s.day_of_week, 0)
        self.assertEqual(s.player.workdays_this_month, 0)
        kinds = {e.kind for e in s.calendar}
        self.assertIn("payday", kinds)
        self.assertIn("rent_due", kinds)
        self.assertIn("heating_bill", kinds)
        self.assertTrue(any("Credit score" in m for m in logs))

    def test_rollover_reseeds_loan_due_for_active_loans(self):
        s = new_game(seed=1)
        s.loans = [Loan(
            kind="personal", principal=50000, remaining=50000,
            apr=0.14, monthly_payment=5000, due_day=15,
            payments_made=0, payments_missed=0,
        )]
        s.calendar = []
        events._rollover_month(s)
        loan_dues = [e for e in s.calendar if e.kind == "loan_due"]
        self.assertEqual(len(loan_dues), 1)
        self.assertEqual(loan_dues[0].month, s.month)
        self.assertEqual(loan_dues[0].day, 15)

    def test_rollover_skips_loan_due_for_paid_loans(self):
        s = new_game(seed=1)
        s.loans = [Loan(
            kind="personal", principal=50000, remaining=0,
            apr=0.14, monthly_payment=5000, due_day=15,
            payments_made=10, payments_missed=0,
        )]
        s.calendar = []
        events._rollover_month(s)
        self.assertFalse(any(e.kind == "loan_due" for e in s.calendar))

    def test_advance_until_event_returns_month_rollover_at_boundary(self):
        s = new_game(seed=1)
        s.day = B.MONTH_LEN  # next tick rolls the month
        s.calendar = []  # no same-day calendar events
        s.flags["budget_set_month"] = 2  # pre-satisfy post-rollover gate
        # Force rng to never fire a sage event.
        class _Rng:
            def random(self_):
                return 0.99
            def randint(self_, a, b):
                return a
        state, logs, reason, event = events.advance_until_event(s, max_days=5, rng=_Rng())
        self.assertEqual(reason, "month_rollover")
        self.assertEqual(state.month, 2)
        self.assertIsNone(event)

    def test_advance_until_event_blocks_on_budget_required(self):
        s = new_game(seed=1)
        s.month = 2  # beyond month 1 — budget must be set
        s.flags.pop("budget_set_month", None)
        state, logs, reason, event = events.advance_until_event(s, max_days=3)
        self.assertEqual(reason, "budget_required")
        self.assertIsNone(event)

    def test_advance_day_triggers_game_over_on_stat_zero(self):
        s = new_game(seed=1)
        s.player.stats["hunger"] = 1  # decay is 4 → goes to 0
        s.accounts.checking = 0  # no food can cover it → no hunger restore
        s, _ = events.advance_day(s)
        self.assertIsNotNone(s.game_over)
        self.assertEqual(s.game_over.cause, "hunger")

    def test_advance_day_increments_workdays_on_weekday(self):
        s = new_game(seed=1)
        s.day_of_week = 0  # Monday
        s.accounts.checking = 10**9  # afford food
        before = s.player.workdays_this_month
        s, _ = events.advance_day(s)
        self.assertEqual(s.player.workdays_this_month, before + 1)

    def test_advance_day_skips_workdays_on_weekend(self):
        s = new_game(seed=1)
        s.day_of_week = 5  # Saturday
        s.accounts.checking = 10**9
        before = s.player.workdays_this_month
        s, _ = events.advance_day(s)
        self.assertEqual(s.player.workdays_this_month, before)
