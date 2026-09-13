"""Tests for the speed-quality exchange (v25).

Pinned: the three cases stay apart; a fake dilemma is never manufactured
from dominance; speed on a failed run is never framed as a saving; and
exchange rates appear only where an exchange actually happened.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare.tradeoff import pair_tradeoff


def report(ok_a=True, ok_b=True, score_a=None, score_b=None,
           spend_a=(100, 0.01, 10.0, 5), spend_b=(200, 0.02, 20.0, 8)):
    def side(name, ok, score, spend):
        tokens, cost, latency, steps = spend
        return {
            "agent": {"name": name},
            "outcome": {"success": ok, "answer": "x", "score": score},
            "totals": {"input_tokens": tokens // 2, "output_tokens": tokens - tokens // 2,
                       "cost_usd": cost, "latency_s": latency},
            "steps": [{"index": i} for i in range(steps)],
        }
    return {"a": side("alpha", ok_a, score_a, spend_a),
            "b": side("beta", ok_b, score_b, spend_b)}


class TestDominance(unittest.TestCase):
    def test_right_and_cheaper_is_dominance_not_a_dilemma(self):
        result = pair_tradeoff(report(ok_a=True, ok_b=False))
        self.assertEqual(result["case"], "dominance")
        self.assertEqual(result["dominant"], "alpha")
        self.assertIn("no trade-off", result["statement"])

    def test_both_succeed_better_and_cheaper_is_dominance(self):
        result = pair_tradeoff(report(score_a=1.0, score_b=0.5))
        # beta scores lower AND spends more: alpha dominates
        self.assertIn(result["case"], ("quality_for_spend",))
        self.assertEqual(result["dominant"], "alpha")
        self.assertIn("dominance", result["statement"])


class TestPriceOfCorrectness(unittest.TestCase):
    def test_the_correct_answers_extra_spend_is_priced(self):
        result = pair_tradeoff(report(
            ok_a=False, ok_b=True,
            spend_a=(100, 0.01, 10.0, 5), spend_b=(300, 0.03, 25.0, 9)))
        self.assertEqual(result["case"], "price_of_correctness")
        self.assertEqual(result["dominant"], "beta")
        self.assertEqual(result["price_of_correctness"]["latency_s"], 15.0)

    def test_a_fast_failure_is_never_framed_as_a_saving(self):
        result = pair_tradeoff(report(
            ok_a=False, ok_b=True,
            spend_a=(100, 0.01, 10.0, 5), spend_b=(300, 0.03, 25.0, 9)))
        self.assertIn("not a saving", result["statement"])
        self.assertIn("still unsolved", result["statement"])


class TestGenuineExchange(unittest.TestCase):
    def test_rates_emitted_only_when_quality_and_spend_move_together(self):
        result = pair_tradeoff(report(
            score_a=0.6, score_b=0.9,
            spend_a=(100, 0.01, 10.0, 5), spend_b=(400, 0.05, 30.0, 9)))
        self.assertEqual(result["case"], "quality_for_spend")
        rates = result["exchange"]["rates"]
        self.assertTrue(rates)
        self.assertAlmostEqual(rates["score per dollar"], 0.3 / 0.04, places=3)
        self.assertIn("product decision", result["statement"])

    def test_equal_outcomes_reduce_to_spend(self):
        result = pair_tradeoff(report())
        self.assertEqual(result["case"], "equal_outcome_cheaper_run")
        self.assertEqual(result["dominant"], "alpha")

    def test_identical_runs_have_nothing_to_trade(self):
        same = (100, 0.01, 10.0, 5)
        result = pair_tradeoff(report(spend_a=same, spend_b=same))
        self.assertEqual(result["case"], "equivalent")
        self.assertIsNone(result["dominant"])


class TestHonesty(unittest.TestCase):
    def test_both_failed_offers_no_quality_verdict(self):
        result = pair_tradeoff(report(ok_a=False, ok_b=False))
        self.assertEqual(result["case"], "both_failed")
        self.assertIsNone(result["dominant"])
        self.assertIn("retry budget", result["statement"])

    def test_every_result_carries_the_single_task_caveat(self):
        for kwargs in ({}, {"ok_b": False}, {"ok_a": False, "ok_b": False},
                       {"score_a": 0.5, "score_b": 0.9}):
            result = pair_tradeoff(report(**kwargs))
            self.assertIn("descriptive of this pair only", result["caveat"])

    def test_deterministic(self):
        self.assertEqual(pair_tradeoff(report()), pair_tradeoff(report()))


if __name__ == "__main__":
    unittest.main()
