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

from agentdiff.efficiency import (aggregate_efficiency, cost_per_success_over_runs, cost_per_success_ratio,  # noqa: E402
                                  ratio_text)
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

    def test_runs_with_no_recorded_cost_do_not_divide_by_zero(self):
        """Some of A's successful runs cost nothing (cached or unrecorded): a resample drawing only those has no
        ratio, and is counted rather than crashing the batch report."""
        reports = [_report(0.0, True, 1.0, True)] * 3 + [_report(1.0, True, 1.0, True)]
        r = cost_per_success_ratio(reports)
        self.assertGreater(r["undefined"], 0)
        self.assertIsNotNone(r["low"])

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


class AATest(unittest.TestCase):
    """Real recorded runs of one agent under two names (demo/vendors/aa-suite): the interval must not claim a
    difference, and on the runs dealt to the two names at random it excludes 1 near the nominal 5%. The
    percentile bootstrap it replaced excluded 1 on these runs, and in 11% of the deals."""

    @classmethod
    def setUpClass(cls):
        cls.runs = {}
        for path in sorted(glob.glob(str(ROOT / "demo/vendors/aa-suite/traces/*.json"))):
            task, name, _ = os.path.basename(path)[:-5].split("__")
            t = Trajectory.from_json(path)
            cls.runs.setdefault(task, {}).setdefault(name, []).append((t.totals.cost_usd, t.outcome.success))

    def test_one_agent_under_two_names_is_not_told_apart(self):
        self.assertEqual(len(self.runs), 6)
        reports = [_report(*v["A"][0], *v["B"][0]) for _, v in sorted(self.runs.items())]
        r = cost_per_success_ratio(reports)
        self.assertFalse(r["excludes_one"], r)
        self.assertIn("jackknife", r["method"])

    def test_random_deals_of_the_same_runs_exclude_one_near_five_percent(self):
        import random
        rng = random.Random(1)
        excluded = 0
        for _ in range(400):
            dealt = []
            for _, v in sorted(self.runs.items()):
                pool = v["A"] + v["B"]
                rng.shuffle(pool)
                dealt.append(_report(*pool[0], *pool[2]))
            excluded += bool(cost_per_success_ratio(dealt)["excludes_one"])
        self.assertLessEqual(excluded / 400, 0.06)


class OverRunsTest(unittest.TestCase):
    """Every run of every task (efficiency.cost_per_success_over_runs), the task as the jackknife's cluster."""

    @classmethod
    def setUpClass(cls):
        cls.trajs = {}
        for path in sorted(glob.glob(str(ROOT / "demo/vendors/aa-suite/traces/*.json"))):
            task, name, _ = os.path.basename(path)[:-5].split("__")
            cls.trajs.setdefault(task, {"a": [], "b": []})["a" if name == "A" else "b"].append(Trajectory.from_json(path))

    def test_every_run_counts_and_the_interval_narrows(self):
        pooled = cost_per_success_over_runs(self.trajs)
        self.assertEqual((pooled["tasks"], pooled["runs"]), (6, {"a": 12, "b": 12}))
        one = cost_per_success_ratio([_report(v["a"][0].totals.cost_usd, v["a"][0].outcome.success,
                                              v["b"][0].totals.cost_usd, v["b"][0].outcome.success)
                                      for _, v in sorted(self.trajs.items())])
        self.assertLess(pooled["high"] - pooled["low"], one["high"] - one["low"])

    def test_redealt_nulls_exclude_one_near_five_percent(self):
        import random
        rng = random.Random(3)
        excluded = 0
        for _ in range(400):
            dealt = {}
            for task, v in sorted(self.trajs.items()):
                pool = v["a"] + v["b"]
                rng.shuffle(pool)
                dealt[task] = {"a": pool[:2], "b": pool[2:]}
            excluded += bool(cost_per_success_over_runs(dealt)["excludes_one"])
        self.assertLessEqual(excluded / 400, 0.08)

    def test_unrecorded_cost_and_no_success_say_why(self):
        free = {t: {"a": [Trajectory.from_dict(dict(x.to_dict(), totals={"cost_usd": 0.0})) for x in v["a"]],
                    "b": v["b"]} for t, v in self.trajs.items()}
        self.assertIn("not recorded", cost_per_success_over_runs(free)["reason"])

    def test_runs_reports_it(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([sys.executable, "-m", "agentdiff", "runs", str(ROOT / "demo/vendors/aa-suite/traces"),
                                  "-o", tmp], cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        self.assertIn("Cost per success over every run (12 A, 12 B, 6 tasks)", out.stdout)


if __name__ == "__main__":
    unittest.main()
