"""The diagnoser measured at scale: 200 procedurally generated pairs.

The handcrafted corpus proves the machinery; the procedural corpus
(demo/diagnosis_bench/generate_scale.py) measures the diagnoser on cases
nobody hand-tuned it against — 18 cause families x domains x lengths x
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

    def test_abstention_holds_where_no_step_exists(self):
        # grader mislabels, harness kills and paraphrased-answer cases:
        # naming a step for any of them is a spurious_step miss.  Not
        # pinned at perfect: the paraphrase_grader family is the corpus's
        # deliberate open challenge, and its valueless-domain scenarios
        # (no typed claim to match) are expected misses that stay in the
        # measured number
        abst = self.result["abstention"]
        self.assertGreaterEqual(abst["correct"] / abst["total"], 0.75)

    def test_paraphrase_challenge_is_measured_not_hidden(self):
        # the open-challenge family must exist, be hard, and be visible:
        # typed-value domains are recovered by the claim-match rule, the
        # valueless ones stay as honest misses
        bucket = self.result["by_cause"].get("paraphrase_grader")
        self.assertIsNotNone(bucket)
        self.assertGreaterEqual(bucket["accuracy"], 0.5)
        self.assertLessEqual(bucket["accuracy"], 1.0)
        for m in self.result["misses"]:
            if m["truth"] == "paraphrase_grader":
                self.assertIn("ticket", m["scenario"],
                              "only valueless domains may miss: "
                              + m["scenario"])

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

    def test_twin_steps_never_anchor_the_divergence(self):
        # the twin rule: duplicate identical calls (distractors) shift
        # which copy the aligner matches, but a step the other run also
        # took verbatim cannot be the decisive decision — every distractor
        # scenario must anchor exactly, not one step early
        for r in self.result["results"]:
            if r["cause"] == "distractor":
                self.assertEqual(r["step_outcome"], "exact", r["scenario"])

    def test_wrong_value_answers_never_lead_grader(self):
        # engine rule: an exclusive typed claim contradicting the expected
        # answer voids the coverage support outright
        for r in self.result["results"]:
            if r["cause"] in ("wrong_fact", "cascade", "late_symptom"):
                self.assertNotIn("grader_or_label", r["actual"],
                                 r["scenario"])

    def test_invented_entities_never_lead_grader(self):
        # the g0027 inversion, pinned: an agent that wrote to an invented
        # entity while reciting the expected sentence led grader_or_label
        # at 1.0, because the exclusive-flags gate read only the gap flags
        # and a shared filler literal masked the exclusive invention
        for r in self.result["results"]:
            if r["cause"] == "wrong_entity":
                self.assertNotIn("grader_or_label", r["actual"],
                                 r["scenario"])
                self.assertEqual(r["outcome"], "correct", r["scenario"])

    def test_duplicated_writes_are_one_account_not_a_contest(self):
        # the g0013 inversion, pinned: four flags all describing the same
        # duplicated write contested each other while the divergence they
        # corroborated sat at 0.25 — flags on the root's signature merge
        for r in self.result["results"]:
            if r["cause"] == "causal_duplicate":
                self.assertEqual(r["outcome"], "correct", r["scenario"])

    def test_adversarial_open_challenges_stay_measured(self):
        # negation_answer and garbage_args are expected-miss families:
        # they must exist in the corpus and in the scorecard, and their
        # misses must be honest (contested, or a named wrong lead) — a
        # corpus that quietly dropped them would measure nothing new
        for family in ("negation_answer", "garbage_args"):
            self.assertIn(family, self.result["by_cause"], family)
            bucket = self.result["by_cause"][family]
            self.assertGreater(bucket["total"], 0)


class TestStrippedAnnotations(unittest.TestCase):
    """--strip-annotations: the de-circularized measurement condition.

    The generator writes the very flags the engine reads, so the
    annotated scorecard partly measures agreement with its own labels.
    The stripped corpus nulls every step's error/quality/note; the
    engine must infer from observation text alone.  The stripped numbers
    are published, not gated — they fail the annotated-condition chain
    floor and that gap is the measured value of structured metadata.
    """

    PAIRS = 45  # three per family: enough to prove the mechanics

    @classmethod
    def setUpClass(cls):
        cls.generator = _load_generator()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.manifest = cls.generator.generate(cls.out, cls.PAIRS, strip=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_annotation_is_nulled(self):
        import json
        for sc in self.manifest["scenarios"]:
            for name in (sc["fail"], sc["pass"]):
                raw = json.loads((self.out / name).read_text())
                for step in raw["steps"]:
                    for field in ("error", "quality", "note"):
                        self.assertIsNone(step.get(field),
                                          f"{name} step {step['index']} "
                                          f"kept {field}")

    def test_the_manifest_declares_the_condition(self):
        self.assertTrue(self.manifest["stripped"])
        with tempfile.TemporaryDirectory() as tmp:
            annotated = self.generator.generate(Path(tmp), self.PAIRS)
            self.assertFalse(annotated["stripped"])

    def test_ground_truth_is_identical_across_conditions(self):
        # stripping changes the traces, never the answer key: the same
        # scenarios, causes and decisive steps in both conditions
        with tempfile.TemporaryDirectory() as tmp:
            annotated = self.generator.generate(Path(tmp), self.PAIRS)
        strip_key = [{k: v for k, v in s.items()} for s in
                     self.manifest["scenarios"]]
        self.assertEqual(strip_key, annotated["scenarios"])

    def test_stripped_generation_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            again = self.generator.generate(Path(tmp), self.PAIRS,
                                            strip=True)
            self.assertEqual(self.manifest, again)

    def test_the_bench_still_runs_and_reports_honestly(self):
        result = run_benchmark(self.out)
        self.assertEqual(result["overall"]["total"], self.PAIRS)
        # no floor assertion on purpose: the stripped condition is a
        # measurement, not a gate


if __name__ == "__main__":
    unittest.main()
