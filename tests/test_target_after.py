"""Whether a kept change stopped what it was chosen to stop."""
from __future__ import annotations

import glob
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdiff.hub.evolvecards import _target
from agentdiff.selfevolve import self_evolve, target_after
from test_selfevolve import TASKS, _traj

ROOT = Path(__file__).resolve().parents[1]
RULE = "claims_without_check"


def arm(checked, label, tasks=("a", "b", "c"), runs=("r1", "r2")):
    """Runs that claim the work is done, with (``checked``) or without a check after the last edit."""
    return [_traj(t, checked, checked=checked, run=f"{label}-{r}") for t in tasks for r in runs]


class TargetAfterTest(unittest.TestCase):
    def test_stopped(self):
        got = target_after(RULE, arm(False, "a"), arm(True, "b"))
        self.assertEqual(got["reading"], "stopped")
        self.assertEqual((got["before"], got["after"]), ([6, 6], [0, 6]))
        self.assertEqual(got["fewer"], ["a", "b", "c"])
        self.assertEqual(got["p"], 0.25)
        self.assertIn("stopped", got["says"])

    def test_not_fewer(self):
        got = target_after(RULE, arm(False, "a"), arm(False, "b"))
        self.assertEqual(got["reading"], "not fewer")
        self.assertIsNone(got["p"])

    def test_fewer(self):
        before = arm(False, "a")
        after = arm(True, "b", tasks=("a", "b")) + arm(False, "b", tasks=("c",))
        got = target_after(RULE, before, after)
        self.assertEqual(got["reading"], "fewer")
        self.assertEqual((got["before"], got["after"]), ([6, 6], [2, 6]))

    def test_never_fired(self):
        self.assertEqual(target_after(RULE, arm(True, "a"), arm(True, "b"))["reading"], "never fired")

    def test_paired_by_task_only(self):
        got = target_after(RULE, arm(False, "a", tasks=("a", "b")), arm(True, "b", tasks=("b", "c")))
        self.assertEqual(got["tasks"], 1)
        self.assertEqual((got["before"], got["after"]), ([2, 2], [0, 2]))

    def test_what_cannot_be_read_gives_nothing(self):
        self.assertEqual(target_after("no such rule kind", arm(False, "a"), arm(True, "b")), {})

    def test_the_recorded_stand_in(self):
        # synthetic: the scripted stand-in's recorded evolution, its one change chosen by a rule
        root = ROOT / "demo/selfevolve/examples/stand-in-learns-to-check"
        base = [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(root / "g0-h0/traces/*.json")))]
        cand = [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(root / "g0-h1/traces/*.json")))]
        got = target_after(RULE, base, cand)
        self.assertEqual((got["reading"], got["before"], got["after"]), ("stopped", [6, 6], [0, 6]))


class LoopTest(unittest.TestCase):
    def test_a_rule_chosen_change_carries_its_target(self):
        def run_arm(h, label):
            told = any("check" in i.lower() for i in h.instructions)
            return [_traj(t, told or t in ("t5", "t6"), checked=told or t in ("t5", "t6"), run=f"{label}-{r}")
                    for t in TASKS for r in ("r1", "r2")]
        res = self_evolve(run_arm, generations=2, check="pytest -q")
        test = res["lineage"][0]["action"]["test"]
        self.assertEqual(test["verdict"], "kept")
        self.assertEqual(test["target"]["reading"], "stopped")
        self.assertEqual(test["target"]["rule"], RULE)


class CardTest(unittest.TestCase):
    def test_the_card_says_it(self):
        line = _target(target_after(RULE, arm(False, "a"), arm(True, "b")))
        self.assertIn("its target stopped: 6/6 runs before, 0/6 after", line)
        self.assertIn('class="ok"', line)
        self.assertIn('class="bad"', _target(target_after(RULE, arm(False, "a"), arm(False, "b"))))
        self.assertEqual(_target(None), "")
        self.assertEqual(_target({"reading": "never fired"}), "")


if __name__ == "__main__":
    unittest.main()
