"""A divergence pattern between two arms, against the arm labels dealt at random."""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdiff import stability
from agentdiff.report import compare
from agentdiff.stability import _first_divergence, _relabelled, _reproducibility, stability_analysis
from test_reliability import make_traj

ONE = [("plan", "plan"), ("search", "web_search"), ("read", "open_page"), ("answer", "final")]
TWO = [("plan", "plan"), ("tool_call", "calculator"), ("answer", "final")]
THREE = [("plan", "plan"), ("search", "web_search"), ("tool_call", "calculator"), ("answer", "final")]


def runs(agent, paths, task="t"):
    return [make_traj(agent, task, True, run_id=f"r{i}", steps=p) for i, p in enumerate(paths)]


class RelabelledTest(unittest.TestCase):
    def test_what_the_run_counts_allow(self):
        for n, least, can in ((2, 1 / 3, False), (3, 0.1, False), (4, 2 / 70, True)):
            a, b = runs("a", [ONE] * n), runs("b", [TWO] * n)
            got = _relabelled(a, b, _reproducibility(a, b)["rate"])
            self.assertAlmostEqual(got["least_p"], round(least, 4))
            self.assertIs(got["can_show"], can)

    def test_a_clean_split_at_four_a_side_is_beyond_chance(self):
        a, b = runs("a", [ONE] * 4), runs("b", [TWO] * 4)
        got = _relabelled(a, b, _reproducibility(a, b)["rate"])
        self.assertEqual(got["splits"], 70)
        # only the observed split and its mirror part the runs this cleanly
        self.assertAlmostEqual(got["p"], round(2 / 70, 4))
        self.assertTrue(got["beyond_chance"])

    def test_the_same_mix_on_both_sides_is_not(self):
        a = runs("a", [ONE, TWO, ONE, TWO])
        b = runs("b", [TWO, ONE, TWO, ONE])
        got = _relabelled(a, b, _reproducibility(a, b)["rate"])
        self.assertFalse(got["beyond_chance"])
        self.assertGreater(got["p"], 0.05)

    def test_unequal_sides_have_no_mirror(self):
        a, b = runs("a", [ONE] * 3), runs("b", [TWO] * 5)
        got = _relabelled(a, b, _reproducibility(a, b)["rate"])
        self.assertEqual(got["splits"], 56)
        self.assertAlmostEqual(got["least_p"], round(1 / 56, 4))
        self.assertAlmostEqual(got["p"], round(1 / 56, 4))

    def test_a_seeded_sample_past_the_limit(self):
        a, b = runs("a", [ONE] * 4), runs("b", [TWO] * 4)
        with mock.patch.object(stability, "RELABEL_SPLITS", 30):
            first = _relabelled(a, b, 1.0)
            again = _relabelled(a, b, 1.0)
        self.assertEqual(first, again)
        self.assertEqual(first["splits"], 30)
        self.assertIn("seeded", first["basis"])

    def test_identical_arms_keep_the_level_in_simulation(self):
        # synthetic, seeded: both arms draw from the same three paths
        rng = random.Random(11)
        hits = 0
        for _ in range(120):
            a = runs("a", [rng.choice([ONE, TWO, THREE]) for _ in range(4)])
            b = runs("b", [rng.choice([ONE, TWO, THREE]) for _ in range(4)])
            rep = _reproducibility(a, b)
            if rep["kind"] is not None:
                hits += bool(_relabelled(a, b, rep["rate"])["beyond_chance"])
        self.assertLessEqual(hits / 120, 0.08)


class FirstDivergenceTest(unittest.TestCase):
    def test_matches_the_pair_report(self):
        pairs = [(ONE, TWO), (TWO, THREE), (ONE, THREE), (ONE, ONE)]
        for x, y in pairs:
            ra, rb = make_traj("a", "t", True, steps=x), make_traj("b", "t", True, run_id="r2", steps=y)
            d = compare(ra, rb)["divergences"]
            want = None
            if d:
                idx = d[0]["a_index"] if d[0]["a_index"] is not None else d[0]["b_index"]
                want = (d[0]["kind"], idx if idx is not None else -1)
            self.assertEqual(_first_divergence(ra, rb), want)


class NarrativeTest(unittest.TestCase):
    def _analyse(self, n):
        tasks = {"t": {"a": runs("atlas", [ONE] * n), "b": runs("bolt", [TWO] * n)}}
        return stability_analysis(tasks)

    def test_too_few_runs_is_said(self):
        out = self._analyse(2)
        entry = out["per_task"][0]["divergence_reproducibility"]
        self.assertEqual(entry["verdict"], "systematic")
        self.assertFalse(entry["relabelled"]["can_show"])
        self.assertIn("too few runs", out["narrative"])
        self.assertNotIn("behavioral, not noise", out["narrative"])

    def test_beyond_chance_keeps_the_claim(self):
        out = self._analyse(4)
        self.assertTrue(out["per_task"][0]["divergence_reproducibility"]["relabelled"]["beyond_chance"])
        self.assertIn("behavioral, not noise", out["narrative"])

    def test_no_divergence_carries_no_test(self):
        tasks = {"t": {"a": runs("atlas", [ONE] * 3), "b": runs("bolt", [ONE] * 3)}}
        entry = stability_analysis(tasks)["per_task"][0]["divergence_reproducibility"]
        self.assertEqual(entry["verdict"], "none")
        self.assertNotIn("relabelled", entry)


if __name__ == "__main__":
    unittest.main()
