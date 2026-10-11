"""When the evals are silent the harness explores (selfevolve.EXPLORE), and every verdict says how far it is from
luck (selfevolve.sign_test). The runs here fail in what the code does, not in how the agent worked: no eval and no
rule over the steps can tell a failing run from a passing one."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.selfevolve import EXPLORE, decide, self_evolve, sign_test  # noqa: E402
from test_selfevolve import _traj  # noqa: E402

TASKS = ("t1", "t2", "t3")


def _silent(task: str, ok: bool, run: str) -> dict:
    """A run whose own check passed either way: only the held-out grader says whether it was right."""
    t = _traj(task, True, checked=True, run=run)
    t["outcome"]["success"] = ok
    return t


def _arm(helps: str):
    """t1 always passes; t2 and t3 fail in what the code does, unless the exploration instruction ``helps`` is in
    the harness. Every run reads, edits and checks the same way, so nothing in the steps tells them apart."""
    def arm(harness, label):
        fixed = f"explore:{helps}" in harness.remedies
        return [_silent(t, t == "t1" or fixed, f"{label}-{i}") for t in TASKS for i in (1, 2)]
    return arm


class SignTestTest(unittest.TestCase):
    def test_the_chance_a_change_that_does_nothing_does_this_well(self):
        self.assertAlmostEqual(sign_test(3, 0), 1 / 8)
        self.assertAlmostEqual(sign_test(2, 1), 0.5)
        self.assertIsNone(sign_test(0, 0))
        base = [_silent(t, False, "a") for t in TASKS]
        cand = [_silent(t, True, "b") for t in TASKS]
        v = decide(base, cand)
        self.assertEqual(v["verdict"], "kept")
        self.assertIn("1 time in 8", v["chance"])


class ExploreTest(unittest.TestCase):
    def test_silent_evals_stop_the_harness_unless_it_explores(self):
        quiet = self_evolve(_arm("edge_cases"), generations=3)
        self.assertIn("nothing the harness can act on", quiet["stop"])
        self.assertIsNone(quiet["lineage"][0]["action"])
        out = self_evolve(_arm("edge_cases"), generations=3, explore=True)
        a = out["lineage"][0]["action"]
        self.assertEqual(a["remedy"]["id"], "explore:edge_cases")
        self.assertEqual(a["because"]["source"], "exploration")
        self.assertEqual(a["test"]["verdict"], "kept")
        self.assertIn("converged at g1", out["stop"])
        self.assertIn("exploration: no eval named a change", out["narrative"])
        self.assertFalse(out["lineage"][0]["evals"].get("born"), "no rule, so no eval is born by intervention")

    def test_an_exploration_that_does_not_help_is_reverted_and_the_next_one_tried(self):
        out = self_evolve(_arm("spec_first"), generations=4, explore=True)
        ids = [(g["action"] or {}).get("remedy", {}).get("id") for g in out["lineage"]]
        verdicts = [((g["action"] or {}).get("test") or {}).get("verdict") for g in out["lineage"]]
        self.assertEqual(ids[:2], ["explore:edge_cases", "explore:spec_first"])
        self.assertEqual(verdicts[:2], ["reverted", "kept"])
        self.assertEqual(out["harness"]["remedies"], ["explore:spec_first"], "only the one that helped stays")

    def test_when_the_library_is_spent_it_says_so(self):
        out = self_evolve(_arm("nothing"), generations=len(EXPLORE) + 2, explore=True)
        self.assertEqual(len([g for g in out["lineage"] if g["action"]]), len(EXPLORE))
        self.assertIn("every exploration instruction was tried", out["stop"])

    def test_no_instruction_names_a_grader(self):
        for key, value, why in EXPLORE:
            for word in ("hidden", "grader", "test_", "{check}", "unittest", "pytest"):
                self.assertNotIn(word, value, key)


if __name__ == "__main__":
    unittest.main()
