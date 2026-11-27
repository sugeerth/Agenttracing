"""What more runs buy against new tasks for an agent's pass rate (reliability.runs_or_tasks)."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.reliability import reliability, runs_or_tasks  # noqa: E402
from test_pass_k_interval import _run  # noqa: E402


class RunsOrTasksTest(unittest.TestCase):
    def test_by_hand(self):
        """ICC 0.5, 6 tasks, 3 runs each, 18 more runs: width ∝ sqrt((0.5 + 0.5/k) / T)."""
        p = runs_or_tasks(0.5, 6, 3)
        now = math.sqrt((0.5 + 0.5 / 3) / 6)
        self.assertAlmostEqual(p["more_runs"], round(1 - math.sqrt((0.5 + 0.5 / 6) / 6) / now, 4))
        self.assertAlmostEqual(p["more_tasks"], round(1 - math.sqrt(6 / 12), 4))
        self.assertEqual(p["effective_runs"], round(18 / (1 + 2 * 0.5), 2))
        self.assertEqual(p["floor_with_endless_runs"], round(1 - math.sqrt(0.5 / (0.5 + 0.5 / 3)), 4))
        self.assertTrue(p["rough"])

    def test_the_two_ends(self):
        none_shared = runs_or_tasks(0.0, 25, 4)
        self.assertEqual(none_shared["runs_share_of_tasks_gain"], 1.0, "runs as good as tasks when tasks do not differ")
        self.assertEqual(none_shared["floor_with_endless_runs"], 1.0)
        self.assertFalse(none_shared["rough"])
        all_shared = runs_or_tasks(1.0, 6, 2)
        self.assertEqual((all_shared["more_runs"], all_shared["runs_share_of_tasks_gain"]), (0.0, 0.0))
        self.assertEqual(all_shared["effective_runs"], 6.0)
        self.assertIn("buy 0% of what new tasks would", all_shared["says"])

    def test_new_tasks_never_buy_less_for_the_same_budget(self):
        for rho in (0.0, 0.1, 0.4, 0.9, 1.0):
            for budget in (6, 18, 60):
                p = runs_or_tasks(rho, 6, 3, budget=budget)
                self.assertGreaterEqual(p["more_tasks"] + 1e-9, p["more_runs"], (rho, budget))

    def test_no_icc_no_plan(self):
        self.assertIsNone(runs_or_tasks(None, 6, 3)["more_runs"])
        self.assertIn("no plan", runs_or_tasks(None, 6, 3)["says"])
        self.assertIsNone(runs_or_tasks(0.5, 1, 3)["more_runs"])

    def test_reliability_carries_it(self):
        outcomes = {"a": [True, True, True], "b": [False, False, False], "c": [True, False, True]}
        data = {t: {"x": [_run(t, ok, r) for r, ok in enumerate(runs)]} for t, runs in outcomes.items()}
        p = reliability(data)["per_agent"]["x"]["runs_or_tasks"]
        self.assertEqual((p["tasks"], p["runs_per_task"]), (3, 3.0))
        self.assertIsNotNone(p["more_tasks"])


if __name__ == "__main__":
    unittest.main()
