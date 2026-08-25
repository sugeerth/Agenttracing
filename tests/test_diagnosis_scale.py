"""The diagnoser measured at scale: 200 procedurally generated pairs.

The handcrafted corpus proves the machinery; the procedural corpus
(demo/diagnosis_bench/generate_scale.py) measures the diagnoser on cases
nobody hand-tuned it against — 10 cause families x domains x lengths x
distractors, ground truth derived from construction.  CI runs a 200-pair
sample (10x the handcrafted corpus); the full 2,000-pair run is one CLI
command away and, being seeded from the same generator, measures the
same population.

The first full-scale run of this generator caught two real bugs before
these tests existed: an incoherent implant (a blind-write scenario whose
failing answer restated the expected answer — the engine's grader
verdict was CORRECT on that evidence) and an engine gap (an answer
differing from expected only in its numeric value still earned coverage
support).  Both fixes are pinned here.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from deepcompare.bench import floor_violations, run_benchmark

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "demo" / "diagnosis_bench" / "generate_scale.py"

PAIRS = 200


def _load_generator():
    spec = importlib.util.spec_from_file_location("diag_gen_scale", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestScaledBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = _load_generator()
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.manifest = cls.generator.generate(out, PAIRS)
        cls.result = run_benchmark(out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_corpus_covers_every_family(self):
        causes = {s["cause"] for s in self.manifest["scenarios"]}
        # blind_write falls back to divergence_only in write-less domains,
        # so it may appear less often, but every family the sample drew
        # must be scored
        self.assertEqual(causes, set(self.result["by_cause"]))
        self.assertEqual(len(self.manifest["scenarios"]), PAIRS)

    def test_floors_hold_at_scale(self):
        self.assertEqual(
            floor_violations(self.result), [],
            "scaled-corpus floors broken; misses: "
            + repr(self.result["misses"][:5]
                   + self.result["step_misses"][:5]))

    def test_abstention_is_perfect_where_no_step_exists(self):
        # grader mislabels and harness kills at scale: naming a step for
        # any of them would be a spurious_step miss
        self.assertEqual(self.result["abstention"]["correct"],
                         self.result["abstention"]["total"])

    def test_secondary_contributors_stay_visible_at_scale(self):
        multi = self.result["multi_cause"]
        self.assertEqual(multi["secondary_visible"], multi["scenarios"])

    def test_every_result_is_accounted(self):
        overall = self.result["overall"]
        outcomes = {"correct": 0, "wrong": 0, "contested": 0,
                    "secondary_only": 0}
        for r in self.result["results"]:
            outcomes[r["outcome"]] += 1
        self.assertEqual(outcomes["correct"], overall["correct"])
        self.assertEqual(sum(outcomes.values()), overall["total"])

    def test_generation_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            again = self.generator.generate(Path(tmp), PAIRS)
            self.assertEqual(self.manifest, again)


class TestScaleCaughtBugsStayFixed(unittest.TestCase):
    """The two bugs the first 2,000-pair run exposed, pinned."""

    @classmethod
    def setUpClass(cls):
        cls.generator = _load_generator()
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.generator.generate(out, PAIRS)
        cls.result = run_benchmark(out)
        cls.by_scenario = {r["scenario"]: r for r in cls.result["results"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_blind_write_answers_never_restate_the_expected_answer(self):
        # generator coherence: a blind-write failure whose answer equals
        # the expected answer is a grader case, not a blind-write case
        for r in self.result["results"]:
            if r["cause"] == "blind_write":
                self.assertNotEqual(r["outcome"], "wrong", r["scenario"])
                self.assertNotIn("grader_or_label", r["actual"],
                                 r["scenario"])

    def test_wrong_value_answers_never_lead_grader(self):
        # engine rule: an exclusive typed claim contradicting the expected
        # answer voids the coverage support outright
        for r in self.result["results"]:
            if r["cause"] in ("wrong_fact", "cascade", "late_symptom"):
                self.assertNotIn("grader_or_label", r["actual"],
                                 r["scenario"])


if __name__ == "__main__":
    unittest.main()
