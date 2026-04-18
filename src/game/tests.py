from unittest.mock import patch

from django.test import TestCase
from pydantic import ValidationError

from game import balance as B
from game import events, sage
from game.state import CalendarEvent, Loan, new_game


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
