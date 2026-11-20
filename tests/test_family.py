"""Self-evolve, its tested changes as one family: each verdict's sign test also reported Holm-adjusted for every
change the run tested (selfevolve.family), beside it and never deciding anything."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.selfevolve import family, holm  # noqa: E402


def _gen(name, p, verdict="kept"):
    test = {"verdict": verdict, "why": "x", "p_value": p, "chance": "y", "wins": [], "losses": [],
            "passed": {"current": [1, 4], "changed": [3, 4]}, "median_tokens": {}}
    return {"generation": name, "runs": 4, "failed": 2, "harness": {"version": 0}, "evals": {},
            "action": {"remedy": {"id": f"explore:{name}"}, "because": {"source": "exploration"}, "test": test}}


class HolmTest(unittest.TestCase):
    def test_holm_steps_down_and_never_lets_a_later_p_fall_below_an_earlier(self):
        self.assertEqual(holm({"a": 0.125, "b": 0.5, "c": 0.25}), {"a": 0.375, "c": 0.5, "b": 0.5})
        self.assertEqual(holm({"a": 0.125}), {"a": 0.125}, "one test is its own family")
        self.assertEqual(holm({"a": 0.4, "b": 0.6}), {"a": 0.8, "b": 0.8})
        self.assertEqual(holm({}), {})

    def test_the_family_is_every_tested_change_with_a_sign_test(self):
        lineage = [_gen("g0", 0.125), _gen("g1", None, "reverted"), _gen("g2", 0.25, "reverted"),
                   {"generation": "g3", "runs": 4, "failed": 0, "action": None}]
        fam = family(lineage)
        self.assertEqual(fam["tests"], 2, "a change no task moved under has no sign test to adjust")
        self.assertEqual(fam["adjusted"], {"g0": 0.25, "g2": 0.25})

    def test_every_contender_of_a_tournament_is_a_test_made(self):
        g0 = _gen("g0", 0.125)
        g0["tournament"] = {"breadth": 3, "winner": "explore:g0", "tested": [
            {"remedy": "explore:g0", "verdict": "kept", "p_value": 0.125},
            {"remedy": "explore:b", "verdict": "reverted", "p_value": 0.5},
            {"remedy": "explore:c", "verdict": "reverted", "p_value": None}]}
        fam = family([g0, _gen("g1", 0.25, "reverted")])
        # the winner once, the other contender with a sign test, the next generation: three tests, not two
        self.assertEqual(fam["tests"], 3)
        self.assertEqual(fam["adjusted"], {"g0": 0.375, "g1": 0.5, "g0:explore:b": 0.5})

    def test_through_the_loop(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_tournament import Arm, evolve, has
        # opt0 fixes t1 and t2, opt1 fixes t3: both move tasks, so both have a sign test
        res = evolve(Arm(lambda t, h, label: (has(h, 0) and t in ("t1", "t2")) or (has(h, 1) and t == "t3")
                         or t == "t4"), breadth=3)
        rows = res["lineage"][0]["tournament"]["tested"]
        made = sum(r["p_value"] is not None for r in rows)
        self.assertGreater(made, 1)
        self.assertEqual(res["family"]["tests"], made)


class CardTest(unittest.TestCase):
    def _card(self, lineage):
        from agentdiff.hub.evolvecards import evolve_card
        data = {"lineage": lineage, "harness": {"version": 1, "instructions": ["x"], "deny_tools": []},
                "family": family(lineage)}
        return evolve_card(SimpleNamespace(id="a" * 12, title="t", summary={}), data)

    def test_a_verdict_among_several_says_its_chance_across_them(self):
        card = self._card([_gen("g0", 0.125), _gen("g1", 0.5), _gen("g2", 0.25, "reverted")])
        self.assertIn("1 in 8 by chance", card)
        self.assertIn("(1 in 3 across the 3 tested)", card)
        self.assertIn("(as likely across the 3 tested)", card)

    def test_a_single_tested_change_says_nothing_more(self):
        card = self._card([_gen("g0", 0.125)])
        self.assertIn("1 in 8 by chance", card)
        self.assertNotIn("across the", card)


if __name__ == "__main__":
    unittest.main()
