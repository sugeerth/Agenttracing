"""What a self-evolve verdict's sign test could ever show (selfevolve.resolution): its best possible chance, its
exact power against a change that wins three in four of the tasks it moves, and the tasks that would give 80%."""

from __future__ import annotations

import itertools
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.hub.evolvecards import resolution_line  # noqa: E402
from agentdiff.selfevolve import decide, resolution, resolution_text, self_evolve, sign_test  # noqa: E402
from test_settle import _rounds_arm, _runs  # noqa: E402


def _brute_power(tasks: int, move: float, win: float, alpha: float) -> float:
    """Every way each task can go (won, lost, unmoved), weighted by its chance: the share whose sign test
    reaches alpha."""
    total = 0.0
    chance = {"w": move * win, "l": move * (1 - win), "s": 1 - move}
    for ways in itertools.product("wls", repeat=tasks):
        wins, losses = ways.count("w"), ways.count("l")
        p = sign_test(wins, losses)
        if p is not None and p <= alpha:
            weight = 1.0
            for x in ways:
                weight *= chance[x]
            total += weight
    return total


class ResolutionTest(unittest.TestCase):
    def test_the_best_chance_is_every_task_won(self):
        self.assertEqual(resolution(4, 2)["best_p"], 1 / 16)
        self.assertFalse(resolution(4, 2)["reachable"], "1 in 16 is above 0.05")
        self.assertTrue(resolution(5, 2)["reachable"], "1 in 32 is not")

    def test_power_is_exact(self):
        for tasks, moved in ((3, 2), (5, 3), (6, 1), (7, 7)):
            got = resolution(tasks, moved)["power"]
            self.assertAlmostEqual(got, _brute_power(tasks, moved / tasks, 0.75, 0.05), places=4, msg=(tasks, moved))

    def test_tasks_needed_reach_the_target_and_fewer_do_not_always(self):
        r = resolution(6, 3)
        self.assertIsNotNone(r["tasks_needed"])
        self.assertGreaterEqual(resolution(r["tasks_needed"], r["tasks_needed"] // 2)["power"], 0.8 - 0.05)
        self.assertLess(r["power"], 0.8)

    def test_nothing_moved_measures_nothing(self):
        r = resolution(6, 0)
        self.assertEqual((r["power"], r["tasks_needed"]), (0.0, None))
        self.assertIn("no task moved", resolution_text(r))
        self.assertIn("no task was graded", resolution_text(resolution(0, 0)))

    def test_the_text_says_when_the_test_cannot_reach_the_level(self):
        self.assertIn("cannot reach 0.05 however the tasks move", resolution_text(resolution(3, 3)))
        self.assertNotIn("cannot", resolution_text(resolution(6, 3)))


class WiredTest(unittest.TestCase):
    def test_every_verdict_carries_it_and_decides_nothing_by_it(self):
        base = _runs({"t1": (0, 2), "t2": (0, 2), "t3": (2, 2)}, "a")
        cand = _runs({"t1": (2, 2), "t2": (1, 2), "t3": (2, 2)}, "b")
        out = decide(base, cand)
        self.assertEqual(out["verdict"], "kept", "the paired rule over counts decides, power or not")
        self.assertEqual(out["resolution"]["moved"], [2, 3])
        self.assertFalse(out["resolution"]["reachable"])

    def test_the_run_says_it(self):
        arm, _ = _rounds_arm(helps=True)
        said = []
        self_evolve(arm, generations=1, explore=True, on_progress=said.append)
        self.assertTrue(any("what the test could show: with 2 task(s)" in s for s in said), said)

    def test_the_card_shows_it_for_a_record_from_before_the_field(self):
        data = json.loads((ROOT / "demo" / "selfevolve" / "examples" / "stand-in-learns-to-check"
                           / "self-evolve.json").read_text())
        html = resolution_line(data)
        self.assertIn("What the test could show", html)
        self.assertIn("with 3 tasks no outcome reaches 0.05", html)
        self.assertNotIn("<0", html.replace("<div", "").replace("</div", "").replace("<b", "").replace("</b", ""))


if __name__ == "__main__":
    unittest.main()
