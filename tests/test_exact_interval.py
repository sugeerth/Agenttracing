"""What repeated runs can rule out (statistics.exact_interval, fail_rate_at_most, runs_to_rule_out; the
per-task and per-agent fields reliability adds). A task that passed every one of three runs is reported as
consistent; the exact bound says it could still fail most of the time."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.reliability import reliability  # noqa: E402
from agentdiff.statistics import exact_interval, fail_rate_at_most, runs_to_rule_out  # noqa: E402
from test_pass_k_interval import _run  # noqa: E402


class ExactIntervalTest(unittest.TestCase):
    def test_published_values(self):
        """Clopper–Pearson 95% intervals as printed in standard tables."""
        self.assertEqual(exact_interval(7, 10), [0.3475, 0.9333])
        self.assertEqual(exact_interval(4, 6), [0.2228, 0.9567])
        self.assertEqual(exact_interval(3, 3), [round(0.025 ** (1 / 3), 4), 1.0])
        self.assertEqual(exact_interval(0, 3), [0.0, round(1 - 0.025 ** (1 / 3), 4)])
        self.assertIsNone(exact_interval(0, 0))

    def test_exact_coverage_never_runs_short(self):
        """Exact over the binomial distribution, not simulated: at every true rate on a grid and every run count
        here, the interval holds the rate at least 95% of the time."""
        for n in (2, 3, 5, 10, 30):
            intervals = [exact_interval(c, n) for c in range(n + 1)]
            for i in range(1, 100):
                p = i / 100
                held = sum(math.comb(n, c) * p ** c * (1 - p) ** (n - c)
                           for c, (lo, hi) in enumerate(intervals) if lo - 1e-4 <= p <= hi + 1e-4)
                self.assertGreaterEqual(held, 0.95 - 1e-9, (n, p))


class UnbrokenRecordTest(unittest.TestCase):
    def test_the_bound_is_where_the_record_turns_unlikely(self):
        for n in (1, 3, 6, 10):
            f = fail_rate_at_most(n)
            self.assertAlmostEqual((1 - f) ** n, 0.05, places=3, msg=n)
        self.assertEqual(round(fail_rate_at_most(3), 2), 0.63)
        self.assertIsNone(fail_rate_at_most(0))

    def test_runs_to_rule_out(self):
        self.assertEqual((runs_to_rule_out(0.10), runs_to_rule_out(0.05), runs_to_rule_out(0.01)), (29, 59, 299))
        for rate in (0.10, 0.05, 0.01):
            n = runs_to_rule_out(rate)
            self.assertLessEqual((1 - rate) ** n, 0.05, "n straight passes are unlikely at that rate")
            self.assertGreater((1 - rate) ** (n - 1), 0.05, "and n is the fewest that do it")
        self.assertIsNone(runs_to_rule_out(0))


class ReliabilityFieldsTest(unittest.TestCase):
    def test_per_task_and_per_agent(self):
        outcomes = {"always": [True] * 3, "never": [False] * 3, "flaky": [True, False, True, True, False, True]}
        data = {t: {"a": [_run(t, ok, r) for r, ok in enumerate(runs)]} for t, runs in outcomes.items()}
        rel = reliability(data)["per_agent"]["a"]
        rows = {r["task"]: r for r in rel["per_task"]}
        self.assertEqual(rows["always"]["unbroken"]["record"], "passed every run")
        self.assertEqual(round(rows["always"]["unbroken"]["other_rate_at_most"], 2), 0.63)
        self.assertIn("could still fail up to 63%", rows["always"]["unbroken"]["means"])
        self.assertEqual(rows["never"]["unbroken"]["record"], "failed every run")
        self.assertIsNone(rows["flaky"]["unbroken"])
        self.assertEqual(rows["flaky"]["success_rate_exact95"], exact_interval(4, 6))
        summary = rel["unbroken_records"]
        self.assertEqual((summary["passed_every_run"], summary["failed_every_run"], summary["fewest_runs"]), (1, 1, 3))
        self.assertIn("Ruling out a 10% failure rate takes 29 straight passes", summary["says"])


if __name__ == "__main__":
    unittest.main()
