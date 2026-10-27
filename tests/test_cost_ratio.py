"""B's cost per success over A's, with a paired bootstrap interval over tasks (efficiency.cost_per_success_ratio).
A failed run's spend stays in its side's numerator; a resample that leaves a side without a success is counted,
never dropped silently."""

from __future__ import annotations

import glob
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.efficiency import aggregate_efficiency, cost_per_success_ratio, ratio_text  # noqa: E402
from agentdiff.report import compare  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402


def _report(cost_a, ok_a, cost_b, ok_b):
    side = lambda c, ok: {"totals": {"cost_usd": c}, "outcome": {"success": ok}}  # noqa: E731
    return {"a": side(cost_a, ok_a), "b": side(cost_b, ok_b)}


class RatioTest(unittest.TestCase):
    def test_a_failed_run_still_costs(self):
        reports = [_report(1.0, True, 2.0, True), _report(1.0, True, 2.0, False)]
        r = cost_per_success_ratio(reports)
        self.assertEqual(r["a"], 1.0)
        self.assertEqual(r["b"], 4.0, "4 dollars over 1 success")
        self.assertEqual(r["ratio"], 4.0)

    def test_twice_the_price_on_every_task_is_told_apart(self):
        reports = [_report(1.0 + i / 10, True, 2.0 + i / 5, True) for i in range(8)]
        r = cost_per_success_ratio(reports)
        self.assertAlmostEqual(r["ratio"], 2.0)
        self.assertTrue(r["excludes_one"])
        self.assertIn("the interval excludes 1", ratio_text(r, {"a": "A", "b": "B"}))

    def test_a_dearer_side_on_half_the_tasks_cannot_be_told_apart(self):
        reports = [_report(1.0, True, 3.0 if i % 2 else 0.4, True) for i in range(6)]
        r = cost_per_success_ratio(reports)
        self.assertFalse(r["excludes_one"])
        self.assertIn("cannot tell which is dearer", ratio_text(r, {"a": "A", "b": "B"}))

    def test_resamples_without_a_success_are_counted(self):
        reports = [_report(1.0, True, 1.0, False)] * 3 + [_report(1.0, True, 1.0, True)]
        r = cost_per_success_ratio(reports)
        self.assertGreater(r["undefined"], 0)
        self.assertIn("left a side without a success", ratio_text(r, {"a": "A", "b": "B"}))

    def test_unmeasurable_with_the_reason(self):
        r = cost_per_success_ratio([_report(0.0, True, 1.0, True)])
        self.assertIsNone(r["ratio"])
        self.assertIn("not recorded", r["reason"])
        r = cost_per_success_ratio([_report(1.0, False, 1.0, True)])
        self.assertIn("no successful runs", r["reason"])

    def test_deterministic(self):
        reports = [_report(1.0 + i, i % 3 > 0, 2.0, True) for i in range(7)]
        self.assertEqual(cost_per_success_ratio(reports), cost_per_success_ratio(reports))


class RecordedRunsTest(unittest.TestCase):
    def test_the_recorded_live_suite(self):
        """Six real tasks, one run each per agent, real billed cost: the dearer agent is about 1.84 times the
        other per success, and the six tasks can tell."""
        by_task: dict = {}
        for path in sorted(glob.glob(str(ROOT / "demo/vendors/live-suite/traces/*__r1.json"))):
            by_task.setdefault(os.path.basename(path).split("__")[0], []).append(path)
        reports = []
        for task, (pa, pb) in sorted(by_task.items()):  # the same two agents on every task, in name order
            reports.append(compare(Trajectory.from_json(pa), Trajectory.from_json(pb)))
        r = cost_per_success_ratio(reports)
        self.assertEqual(r["tasks"], 6)
        self.assertAlmostEqual(r["ratio"], 1.8413, places=3)
        self.assertTrue(r["excludes_one"])
        agg = aggregate_efficiency(reports)
        self.assertEqual(agg["cost_per_success_ratio"], r)
        self.assertIn("per success (95% interval", agg["narrative"])


if __name__ == "__main__":
    unittest.main()
