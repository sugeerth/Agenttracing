"""Measured accuracy of the diagnoser against its ground-truth benchmark.

``demo/diagnosis_bench/generate.py`` implants one known cause per scenario
pair; ``deepcompare.bench.run_benchmark`` runs the full compare pipeline and
scores the leading hypothesis against the manifest.  These tests assert an
honest floor (>= 0.75 overall), not perfection: scenarios the diagnoser
genuinely cannot separate stay in the corpus and are reported as misses,
because a benchmark that only contains what the diagnoser already gets
right measures nothing.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from deepcompare.bench import run_benchmark

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "demo" / "diagnosis_bench"
TRACES = BENCH_DIR / "traces"

#: the honest floor: measured, not aspirational.  Do not raise this by
#: making scenarios easier; raise it by making the diagnoser better.
ACCURACY_FLOOR = 0.75

CAUSES = (
    "grader_mislabel", "harness_kill", "environment_fault",
    "wrong_fact", "blind_write", "divergence_only",
    # hard mode: the literature's documented attributor failure modes
    "late_symptom", "distractor", "cascade",
)


def _generate_corpus() -> None:
    spec = importlib.util.spec_from_file_location(
        "diagnosis_bench_generate", BENCH_DIR / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


class TestDiagnosisAccuracy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (TRACES / "MANIFEST.json").exists():
            _generate_corpus()
        cls.result = run_benchmark(TRACES)

    def test_corpus_shape(self):
        # 6 causes, at least 2 scenarios each, 12+ pairs total.
        by_cause = self.result["by_cause"]
        self.assertEqual(set(by_cause), set(CAUSES))
        for cause, bucket in by_cause.items():
            self.assertGreaterEqual(
                bucket["total"], 2, f"{cause} needs at least 2 scenarios")
        self.assertGreaterEqual(self.result["overall"]["total"], 12)

    def test_denominators_add_up(self):
        overall = self.result["overall"]
        self.assertEqual(
            overall["total"],
            sum(b["total"] for b in self.result["by_cause"].values()))
        self.assertEqual(
            overall["correct"],
            sum(b["correct"] for b in self.result["by_cause"].values()))
        self.assertEqual(len(self.result["results"]), overall["total"])

    def test_overall_accuracy_floor(self):
        overall = self.result["overall"]
        self.assertGreaterEqual(
            overall["accuracy"], ACCURACY_FLOOR,
            "diagnoser accuracy fell below the measured floor; misses: "
            + repr(self.result["misses"]))

    def test_every_miss_is_listed_with_its_actual_leading_kind(self):
        missed_ids = {m["scenario"] for m in self.result["misses"]}
        not_correct = {r["scenario"] for r in self.result["results"]
                       if r["outcome"] != "correct"}
        self.assertEqual(missed_ids, not_correct)
        for miss in self.result["misses"]:
            self.assertIn(miss["outcome"], ("wrong", "contested"))
            # what actually led (or the contested contenders) must be named
            self.assertTrue(miss["actually_led"])
            self.assertIn(miss["truth"], CAUSES)
            self.assertTrue(miss["acceptable"])

    def test_contested_never_counts_as_correct(self):
        for entry in self.result["results"]:
            if entry["outcome"] == "contested":
                self.assertIn(
                    entry["scenario"],
                    {m["scenario"] for m in self.result["misses"]})

    def test_deterministic(self):
        again = run_benchmark(TRACES)
        self.assertEqual(self.result, again)


class TestStepLocalization(unittest.TestCase):
    """The axis the field collapses on (14.2% Who&When, 30.3% Pro): the
    decisive step must be scored, abstention included, with floors that
    fail CI on regression rather than brittle 100% pins."""

    @classmethod
    def setUpClass(cls):
        _generate_corpus()
        cls.result = run_benchmark(TRACES)

    def test_step_metrics_present_with_denominators(self):
        step = self.result["step_localization"]
        self.assertEqual(step["total"], 14)
        self.assertLessEqual(step["exact"], step["within_1"])
        abst = self.result["abstention"]
        self.assertEqual(abst["total"], 4)

    def test_step_floor(self):
        self.assertGreaterEqual(
            self.result["step_localization"]["accuracy_exact"], 0.6)
        self.assertGreaterEqual(
            self.result["step_localization"]["accuracy_within_1"], 0.75)

    def test_abstention_floor(self):
        # naming a step for a grader mislabel or a harness kill is a miss,
        # not a near-hit; abstaining there is the correct answer
        self.assertGreaterEqual(self.result["abstention"]["accuracy"], 0.75)

    def test_every_step_miss_is_listed(self):
        listed = {m["scenario"] for m in self.result["step_misses"]}
        for r in self.result["results"]:
            if r["step_outcome"] not in ("exact", "correct_abstain"):
                self.assertIn(r["scenario"], listed)

    def _result(self, scenario):
        return next(r for r in self.result["results"]
                    if r["scenario"] == scenario)

    def test_symptom_trap_not_blamed_on_the_loud_error(self):
        # ls01: the loud rebook error at step 2 was caused by the quiet
        # stale-cache read at step 1, and the agent recovered from it —
        # environment_error must not lead, and the anchor is the quiet step
        r = self._result("ls01_delayed_segment")
        self.assertEqual(r["outcome"], "correct")
        self.assertNotIn("environment_error", r["actual"])
        self.assertEqual(r["step_outcome"], "exact")

    def test_distractor_pathology_not_blamed(self):
        for scenario in ("dp01_warranty_claim", "dp02_cache_flush"):
            r = self._result(scenario)
            self.assertEqual(r["outcome"], "correct", scenario)
            self.assertNotIn("repeated_calls", r["actual"])
            self.assertNotIn("no_information", r["actual"])

    def test_cascade_anchors_at_the_injection_point(self):
        # the failing run replays the passing prefix exactly; the anchor
        # must land where the fault was injected, not at step 0 and not at
        # the downstream symptoms
        for scenario in ("cs01_invoice_total", "cs02_sla_response"):
            r = self._result(scenario)
            self.assertEqual(r["decisive_truth"], [2], scenario)
            self.assertEqual(r["step_outcome"], "exact", scenario)


if __name__ == "__main__":
    unittest.main()
