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


if __name__ == "__main__":
    unittest.main()
