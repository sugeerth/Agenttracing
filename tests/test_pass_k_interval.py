"""The pass^k interval (reliability): the wider bound of a task bootstrap of each task's own C(c,k)/C(n,k) and
the pooled per-run Wilson interval raised to k. The first follows tasks that differ; the second sees run noise
when every task scored alike. Either alone fails where the other holds."""

from __future__ import annotations

import random
import sys
import unittest
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.reliability import reliability  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402


def _run(task, ok, r):
    return Trajectory.from_dict({
        "schema_version": 1, "trace_id": f"{task}-{r}", "run_id": f"r{r}", "agent": {"name": "x"},
        "task": {"id": task, "prompt": "p"}, "outcome": {"success": ok, "answer": "a"}, "totals": {},
        "steps": [{"index": 0, "type": "answer", "name": "final", "input": "", "output": "a", "tokens": 1,
                   "latency_s": 0.1}]})


def _curve(outcomes):
    """{task: [bool, ...]} -> the pass^k curve."""
    data = {t: {"a": [_run(t, ok, r) for r, ok in enumerate(runs)]} for t, runs in outcomes.items()}
    return reliability(data)["per_agent"]["a"]["pass_hat_k"]["curve"]


class IntervalTest(unittest.TestCase):
    def test_tasks_that_differ_keep_the_estimate_inside(self):
        """Half the tasks always pass, half never: pass^4 is 0.5, which a pooled rate raised to 4 put outside
        its own interval ([0.02, 0.18])."""
        curve = _curve({f"t{i}": [i % 2 == 0] * 4 for i in range(10)})
        for point in curve:
            self.assertEqual(point["value"], 0.5)
            self.assertLessEqual(point["ci95"][0], 0.5)
            self.assertGreaterEqual(point["ci95"][1], 0.5)

    def test_tasks_that_all_passed_still_have_a_width(self):
        """Resampling tasks that all scored 1 gives [1, 1]; the run noise the pooled interval sees remains."""
        curve = _curve({f"t{i}": [True] * 2 for i in range(6)})
        self.assertEqual(curve[-1]["value"], 1.0)
        self.assertLess(curve[-1]["ci95"][0], 0.9)

    def test_coverage_on_a_labelled_synthetic_suite(self):
        """Each task passes with its own chance (0.05, 0.5 or 0.95); the true pass^3 is the mean of p^3. Over
        150 simulated suites of 10 tasks x 3 runs, the interval holds it at least 90% of the time."""
        rng = random.Random(5)
        held = 0
        for _ in range(150):
            ps = [rng.choice([0.05, 0.5, 0.95]) for _ in range(10)]
            truth = sum(p ** 3 for p in ps) / 10
            point = _curve({f"t{i}": [rng.random() < p for _ in range(3)] for i, p in enumerate(ps)})[-1]
            held += point["ci95"][0] <= truth <= point["ci95"][1]
        self.assertGreaterEqual(held / 150, 0.9)

    def test_one_task_has_only_the_pooled_interval(self):
        point = _curve({"t0": [True, False, True]})[-1]
        self.assertEqual(point["value"], comb(2, 3) / comb(3, 3), "two of three runs passed: never all three")
        self.assertIsNotNone(point["ci95"], "no spread over one task, but the pooled interval still stands")
        self.assertEqual(point["ci95"][0], 0.0, "the estimate is inside its own interval")


if __name__ == "__main__":
    unittest.main()
