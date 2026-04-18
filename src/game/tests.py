from unittest.mock import patch

from django.test import TestCase

from game import balance as B
from game import events
from game.state import new_game


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
