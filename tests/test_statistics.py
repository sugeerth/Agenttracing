"""Tests for small-sample statistics behind gate decisions (v14).

The point of this layer is to stop a gate from firing confidently on noise,
so the tests pin the boundary cases: tiny suites, unanimous outcomes, and
differences that should and should not survive resampling.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare.statistics import (
    describe_significance,
    paired_bootstrap_difference,
    pass_at_k,
    wilson_interval,
)


class TestWilsonInterval(unittest.TestCase):
    def test_bounds_stay_inside_zero_one(self):
        for successes, trials in ((0, 8), (8, 8), (1, 3), (5, 5), (0, 1)):
            low, high = wilson_interval(successes, trials)
            self.assertGreaterEqual(low, 0.0)
            self.assertLessEqual(high, 1.0)
            self.assertLessEqual(low, high)

    def test_perfect_score_still_has_uncertainty(self):
        # 8/8 is not proof of 100% reliability; the interval must reflect that.
        low, high = wilson_interval(8, 8)
        self.assertLess(low, 1.0)
        self.assertEqual(high, 1.0)

    def test_zero_score_still_has_uncertainty(self):
        low, high = wilson_interval(0, 8)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_interval_narrows_with_more_trials(self):
        narrow = wilson_interval(50, 100)
        wide = wilson_interval(5, 10)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_no_trials_is_maximally_uncertain(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_contains_the_point_estimate(self):
        low, high = wilson_interval(3, 8)
        self.assertLessEqual(low, 3 / 8)
        self.assertGreaterEqual(high, 3 / 8)


class TestPassAtK(unittest.TestCase):
    def test_all_runs_passing_gives_one(self):
        self.assertEqual(pass_at_k(3, 3, 3), 1.0)

    def test_flaky_agent_is_penalised_by_strictness(self):
        # 2 of 3 runs pass, but needing all 3 in a row is far less likely
        # than the 67% success rate suggests.
        value = pass_at_k(2, 3, 3)
        self.assertEqual(value, 0.0)
        self.assertLess(pass_at_k(2, 3, 2), 2 / 3)

    def test_fewer_runs_than_k_is_unknown(self):
        self.assertIsNone(pass_at_k(2, 2, 3))

    def test_zero_k_is_unknown(self):
        self.assertIsNone(pass_at_k(2, 3, 0))

    def test_monotonic_in_k(self):
        values = [pass_at_k(4, 5, k) for k in (1, 2, 3)]
        for earlier, later in zip(values, values[1:]):
            self.assertGreaterEqual(earlier, later)


class TestPairedBootstrap(unittest.TestCase):
    def test_identical_outcomes_show_no_difference(self):
        outcomes = [True, True, False, True]
        result = paired_bootstrap_difference(outcomes, outcomes)
        self.assertEqual(result["observed"], 0.0)
        self.assertFalse(result["significant"])

    def test_small_drop_on_a_short_suite_is_not_significant(self):
        # One flipped task out of eight is exactly the case a naive gate
        # would call a regression.
        base = [True] * 8
        cand = [True] * 7 + [False]
        result = paired_bootstrap_difference(base, cand)
        self.assertAlmostEqual(result["observed"], 0.125, places=3)
        self.assertFalse(result["significant"])

    def test_large_consistent_drop_is_significant(self):
        base = [True] * 20
        cand = [False] * 20
        result = paired_bootstrap_difference(base, cand)
        self.assertEqual(result["observed"], 1.0)
        self.assertTrue(result["significant"])

    def test_improvement_is_never_flagged_as_a_drop(self):
        base = [False] * 10
        cand = [True] * 10
        result = paired_bootstrap_difference(base, cand)
        self.assertLess(result["observed"], 0)
        self.assertFalse(result["significant"])

    def test_deterministic_across_runs(self):
        base = [True, True, False, True, True, False]
        cand = [True, False, False, True, False, False]
        first = paired_bootstrap_difference(base, cand)
        second = paired_bootstrap_difference(base, cand)
        self.assertEqual(first, second)

    def test_interval_brackets_the_observed_value(self):
        base = [True] * 10
        cand = [True] * 5 + [False] * 5
        result = paired_bootstrap_difference(base, cand)
        self.assertLessEqual(result["low"], result["observed"])
        self.assertGreaterEqual(result["high"], result["observed"])

    def test_empty_input_is_handled(self):
        result = paired_bootstrap_difference([], [])
        self.assertEqual(result["samples"], 0)
        self.assertFalse(result["significant"])

    def test_uses_pairing_not_independent_resampling(self):
        # Paired resampling keeps per-task correlation: when the candidate
        # fails exactly where the baseline fails, there is no difference at
        # all and the interval must collapse to zero.
        base = [True, False, True, False]
        result = paired_bootstrap_difference(base, base)
        self.assertEqual((result["low"], result["high"]), (0.0, 0.0))


class TestDescriptions(unittest.TestCase):
    def test_noise_wording_tells_the_user_what_to_do(self):
        base = [True] * 8
        cand = [True] * 7 + [False]
        text = describe_significance(paired_bootstrap_difference(base, cand), 8)
        self.assertIn("within noise", text)
        self.assertIn("Add tasks", text)

    def test_real_regression_wording_is_unambiguous(self):
        base = [True] * 20
        cand = [False] * 20
        text = describe_significance(paired_bootstrap_difference(base, cand), 20)
        self.assertIn("real regression", text)

    def test_improvement_wording(self):
        base = [False] * 8
        cand = [True] * 8
        text = describe_significance(paired_bootstrap_difference(base, cand), 8)
        self.assertIn("did not lose ground", text)


class TestGateIntegration(unittest.TestCase):
    def test_gate_reports_intervals_and_significance(self):
        from deepcompare import Trajectory
        from deepcompare.gate import evaluate_gate
        from deepcompare.report import compare

        def traj(agent, task, success):
            return Trajectory.from_json({
                "schema_version": 1, "trace_id": f"{agent}-{task}",
                "agent": {"name": agent, "model": "m", "version": "v1"},
                "task": {"id": task, "prompt": "p", "expected": "e"},
                "outcome": {"success": success, "answer": "a",
                            "score": 1.0 if success else 0.0},
                "totals": {"input_tokens": 50, "output_tokens": 50,
                           "cost_usd": 0.01, "latency_s": 2.0},
                "steps": [
                    {"index": 0, "type": "plan", "name": "plan", "input": "i",
                     "output": "o", "tokens": 50, "latency_s": 1.0,
                     "quality": None, "note": None},
                    {"index": 1, "type": "answer", "name": "final", "input": "i",
                     "output": "o", "tokens": 50, "latency_s": 1.0,
                     "quality": None, "note": None},
                ],
            })

        reports = [
            compare(traj("base", f"t{i}", True), traj("cand", f"t{i}", i > 0))
            for i in range(4)
        ]
        gate = evaluate_gate(reports)
        check = next(c for c in gate["checks"] if c["name"] == "success_rate_drop")
        self.assertIn("baseline_ci", check)
        self.assertIn("bootstrap", check)
        self.assertIn("significant", check)
        self.assertFalse(check["significant"])  # 1 of 4 is noise
        self.assertIn("within noise", check["detail"])


if __name__ == "__main__":
    unittest.main()


class TestTwoGroupBootstrap(unittest.TestCase):
    """Independent groups of unequal size — the attribute-lift case.

    Forcing two unequal groups to a common length rewrites both rates and can
    invert the sign of the difference, which is exactly the bug this replaced.
    """

    def test_observed_matches_the_actual_rate_difference(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        # 3/14 vs 1/2 -> 0.2143 - 0.5 = -0.2857
        a = [True] * 3 + [False] * 11
        b = [True] * 1 + [False] * 1
        result = two_group_bootstrap_difference(a, b)
        self.assertAlmostEqual(result["observed"], 3 / 14 - 1 / 2, places=4)

    def test_sign_is_preserved_for_unequal_groups(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        a = [True] * 3 + [False] * 11   # low rate, big group
        b = [True] * 1 + [False] * 1    # high rate, tiny group
        result = two_group_bootstrap_difference(a, b)
        self.assertLess(result["observed"], 0.0)

    def test_interval_brackets_the_observed_difference(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        a = [True] * 4 + [False] * 2
        b = [False] * 10
        result = two_group_bootstrap_difference(a, b)
        self.assertLessEqual(result["low"], result["observed"])
        self.assertGreaterEqual(result["high"], result["observed"])

    def test_clear_separation_is_significant(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        a = [True] * 20
        b = [False] * 20
        self.assertTrue(two_group_bootstrap_difference(a, b)["significant"])

    def test_two_sided_significance_detects_negative_differences(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        a = [False] * 20
        b = [True] * 20
        result = two_group_bootstrap_difference(a, b)
        self.assertTrue(result["significant"])
        self.assertLess(result["observed"], 0)

    def test_overlapping_groups_are_not_significant(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        a = [True, False, True, False]
        b = [True, False, False, True]
        self.assertFalse(two_group_bootstrap_difference(a, b)["significant"])

    def test_deterministic(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        a, b = [True] * 3 + [False] * 5, [True] + [False] * 6
        self.assertEqual(two_group_bootstrap_difference(a, b),
                         two_group_bootstrap_difference(a, b))

    def test_empty_group_is_handled(self):
        from deepcompare.statistics import two_group_bootstrap_difference
        result = two_group_bootstrap_difference([], [True])
        self.assertEqual(result["samples"], 0)
        self.assertFalse(result["significant"])
