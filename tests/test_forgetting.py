"""Forgetting across an evolution (selfevolve.forgetting): a task the evolution once solved that a later harness
no longer passes. ``decide`` guards each step on its own (always passed before, never after, reverts), so a drift
over two kept changes passes it twice. The runs below are scripted, and the lineages are labelled synthetic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.selfevolve import _by_task, decide, decline_p, forgetting, self_evolve  # noqa: E402
from test_selfevolve import TASKS, _traj  # noqa: E402


def _runs(label, passing):
    """Two runs of each task; ``passing`` maps a task to how many of its two runs pass."""
    return [_traj(t, r < passing.get(t, 0), checked=True, run=f"{label}-r{r}") for t in TASKS for r in range(2)]


def _generation(label, version, runs, test=None):
    g = {"generation": label, "harness": {"version": version}, "runs": len(runs),
         "per_task": {t: list(v) for t, v in sorted(_by_task(runs).items())}}
    if test is not None:
        g["action"] = {"remedy": {"id": f"change-{label}"}, "test": test}
    return g


class DeclineTest(unittest.TestCase):
    def test_exact_values(self):
        self.assertAlmostEqual(decline_p([2, 2], [0, 2]), 1 / 6)
        self.assertAlmostEqual(decline_p([3, 3], [0, 3]), 1 / 20)
        self.assertAlmostEqual(decline_p([2, 2], [1, 2]), 0.5)
        self.assertEqual(decline_p([2, 2], [2, 2]), 1.0)


class DriftTest(unittest.TestCase):
    def setUp(self):
        self.h0 = _runs("g0-h0", {"t1": 2})
        self.h1 = _runs("g0-h1", {"t1": 1, "t2": 2, "t3": 2, "t4": 2})
        self.h2 = _runs("g1-h2", {"t2": 2, "t3": 2, "t4": 2, "t5": 2, "t6": 2})
        self.first, self.second = decide(self.h0, self.h1), decide(self.h1, self.h2)

    def test_each_step_passes_decide(self):
        self.assertEqual((self.first["verdict"], self.second["verdict"]), ("kept", "kept"))
        self.assertEqual(self.first["broke"] + self.second["broke"], [], "neither step broke t1 on its own")

    def test_the_drift_is_forgotten_across_the_lineage(self):
        lineage = [_generation("g0", 0, self.h0, self.first), _generation("g1", 1, self.h1, self.second)]
        f = forgetting(lineage)
        self.assertEqual(f["final"], "g1 (kept)")
        self.assertEqual([r["task"] for r in f["forgotten"]], ["t1"])
        row = f["forgotten"][0]
        self.assertEqual((row["then"], row["now"], row["solved_at"]), ([2, 2], [0, 2], "g0"))
        self.assertTrue(row["harness_changed"])
        self.assertAlmostEqual(row["p"], round(1 / 6, 4))
        self.assertIn("t1 2/2 at g0 → 0/2", f["says"])

    def test_a_fall_under_the_same_harness_is_called_noise(self):
        again = _runs("g1-h0", {"t1": 1})
        lineage = [_generation("g0", 0, self.h0, decide(self.h0, self.h0)), _generation("g1", 0, again)]
        f = forgetting(lineage)
        self.assertEqual([r["task"] for r in f["weakened"]], ["t1"])
        self.assertFalse(f["weakened"][0]["harness_changed"])
        self.assertIn("same harness: run noise", f["says"])

    def test_one_state_compares_nothing(self):
        self.assertEqual(forgetting([_generation("g0", 0, self.h0)])["forgotten"], [])
        self.assertIn("nothing to compare", forgetting([])["says"])


class LoopTest(unittest.TestCase):
    def test_the_evolution_reports_forgetting_and_keeps_per_task_counts(self):
        def arm(h, label):
            told = any("check" in i.lower() for i in h.instructions)
            return [_traj(t, told or t in ("t5", "t6"), checked=told or t in ("t5", "t6"), run=f"{label}-{r}")
                    for t in TASKS for r in ("r1", "r2")]
        res = self_evolve(arm, generations=3, check="pytest -q")
        g0 = res["lineage"][0]
        self.assertEqual(g0["per_task"]["t5"], [2, 2])
        self.assertEqual(g0["action"]["test"]["per_task"]["changed"]["t1"], [2, 2])
        self.assertEqual(res["forgetting"]["forgotten"], [])
        self.assertEqual(res["forgetting"]["solved"], 6)


if __name__ == "__main__":
    unittest.main()
