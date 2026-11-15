"""Several changes a generation, the best of them made to replicate."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdiff.selfevolve import Harness, self_evolve
from test_reliability import make_traj

TASKS = ["t1", "t2", "t3", "t4"]
OPTIONS = [(f"opt{i}", f"instruction {i}", "synthetic") for i in range(4)]


def has(h: Harness, i: int) -> bool:
    return f"instruction {i}" in h.instructions


class Arm:
    """A runner whose tasks pass by a rule over the harness. Records each arm's label."""

    def __init__(self, passes, runs=2):
        self.passes, self.runs, self.labels = passes, runs, []

    def __call__(self, h, label, only=None, rounds=None):
        self.labels.append(label)
        return [make_traj("a", t, bool(self.passes(t, h, label)), run_id=f"{label}-r{r}")
                for t in TASKS if only is None or t in only for r in range(self.runs)]


def evolve(arm, breadth, generations=1, **kw):
    return self_evolve(arm, generations=generations, explore=True, confirm=True, breadth=breadth,
                       explore_first=OPTIONS, **kw)


class TournamentTest(unittest.TestCase):
    def test_the_helper_wins_among_placebos(self):
        arm = Arm(lambda t, h, label: has(h, 2) or t == "t4")
        res = evolve(arm, breadth=3)
        g0 = res["lineage"][0]
        self.assertEqual(g0["tournament"]["breadth"], 3)
        self.assertEqual(g0["tournament"]["winner"], "explore:opt2")
        self.assertEqual(g0["action"]["remedy"]["id"], "explore:opt2")
        self.assertTrue(has(Harness.from_dict(res["harness"]), 2))
        tested = {x["remedy"]: x for x in g0["tournament"]["tested"]}
        self.assertTrue(tested["explore:opt2"]["replicated"])
        self.assertEqual(tested["explore:opt0"]["verdict"], "reverted")
        # each contender ran its own arm; the winner's replication ran its own too
        self.assertIn("g0-h1-c3", arm.labels)
        self.assertIn("g0-h1-c3-confirm", arm.labels)
        self.assertIn("g0-h0-confirm-c3", arm.labels)

    def test_a_lucky_best_that_does_not_replicate_gives_way(self):
        # opt0 wins three tasks, but only on its first runs; opt1 wins two, every time
        def passes(t, h, label):
            if has(h, 0) and "confirm" not in label and t in ("t1", "t2", "t3"):
                return True
            return has(h, 1) and t in ("t1", "t2")
        res = evolve(Arm(passes), breadth=2)
        tour = res["lineage"][0]["tournament"]
        self.assertEqual(tour["winner"], "explore:opt1")
        notes = {x["remedy"]: x["note"] for x in tour["tested"]}
        self.assertEqual(notes["explore:opt0"], "did not replicate")
        self.assertEqual(res["ledger"]["tried"]["explore:opt0"], "reverted")

    def test_a_runner_up_is_tested_again_on_top_of_the_winner(self):
        # opt0 fixes t1 and t2, opt1 fixes t3: both win, opt0 more; next generation opt1 goes on top
        def passes(t, h, label):
            return (has(h, 0) and t in ("t1", "t2")) or (has(h, 1) and t == "t3") or t == "t4"
        res = evolve(Arm(passes), breadth=2, generations=2)
        g0, g1 = res["lineage"][0], res["lineage"][1]
        self.assertEqual(g0["tournament"]["winner"], "explore:opt0")
        self.assertIn("also won", g0["tournament"]["says"])
        self.assertNotIn("explore:opt1", {k for k, v in res["ledger"]["tried"].items() if v == "reverted"})
        self.assertEqual(g1["action"]["remedy"]["id"], "explore:opt1")
        h = Harness.from_dict(res["harness"])
        self.assertTrue(has(h, 0) and has(h, 1))

    def test_a_runner_that_cannot_run_single_tasks_is_said(self):
        class NoOnly:
            def __call__(self, h, label):
                return [make_traj("a", t, has(h, 1) or t == "t4", run_id=f"{label}-r{r}")
                        for t in TASKS for r in range(2)]
        res = self_evolve(NoOnly(), generations=1, explore=True, confirm=True, breadth=2, explore_first=OPTIONS)
        tour = res["lineage"][0]["tournament"]
        self.assertEqual(tour["winner"], "explore:opt1")
        self.assertIn("cannot run single tasks", {x["note"] for x in tour["tested"]
                                                  if x["remedy"] == "explore:opt1"}.pop())

    def test_breadth_one_is_the_loop_as_it_was(self):
        arm = Arm(lambda t, h, label: has(h, 0) or t == "t4")
        res = evolve(arm, breadth=1)
        self.assertNotIn("tournament", res["lineage"][0])
        self.assertEqual(arm.labels[:2], ["g0-h0", "g0-h1"])

    def test_nothing_kept(self):
        res = evolve(Arm(lambda t, h, label: t == "t4"), breadth=3)
        tour = res["lineage"][0]["tournament"]
        self.assertIsNone(tour["winner"])
        self.assertEqual(res["lineage"][0]["action"]["test"]["verdict"], "reverted")
        self.assertEqual(Harness.from_dict(res["harness"]).version, 0)


class CardTest(unittest.TestCase):
    def test_the_card_says_it(self):
        from agentdiff.hub.evolvecards import _tournament
        res = evolve(Arm(lambda t, h, label: has(h, 2) or t == "t4"), breadth=3)
        self.assertIn("best of 3 tested, replicated", _tournament(res["lineage"][0]["tournament"]))
        self.assertEqual(_tournament(None), "")
        self.assertEqual(_tournament({"breadth": 1}), "")


if __name__ == "__main__":
    unittest.main()
