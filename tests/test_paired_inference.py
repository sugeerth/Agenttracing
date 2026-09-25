"""Paired inference and clustered standard errors (Miller, "Adding Error
Bars to Evals", 2024): the same tasks for both agents is a paired design,
and tasks that share a source are not independent draws.

Known-answer cases are hand-computed; the integration cases run the real
runs corpus and the real benchmark.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare.bench import run_benchmark
from deepcompare.statistics import (
    MIN_PAIRS_TO_DISTINGUISH, clustered_se, paired_inference, sign_test,
)

ROOT = Path(__file__).resolve().parent.parent


class TestSignTest(unittest.TestCase):
    def test_hand_computed_small_cases(self):
        # 5 discordant, all one way: p = 2 * (1/32) = 0.0625
        self.assertAlmostEqual(sign_test(5, 0), 0.0625, places=6)
        # 6 vs 0: 2 * (1/64) = 0.03125
        self.assertAlmostEqual(sign_test(6, 0), 0.03125, places=6)
        # 4 vs 1: 2 * (1 + 5)/32 = 0.375
        self.assertAlmostEqual(sign_test(4, 1), 0.375, places=6)
        # symmetric: p capped at 1
        self.assertEqual(sign_test(3, 3), 1.0)

    def test_no_discordant_pairs_means_nothing_to_test(self):
        self.assertIsNone(sign_test(0, 0))


class TestPairedInference(unittest.TestCase):
    def test_too_few_pairs_refuses_to_distinguish_and_says_why(self):
        result = paired_inference([(True, False)] * 4, labels=("x", "y"))
        self.assertEqual(result["n_pairs"], 4)
        self.assertIn("not distinguishable", result["verdict"])
        self.assertIn(str(MIN_PAIRS_TO_DISTINGUISH), result["verdict"])

    def test_a_clear_paired_win_is_named_with_its_denominator(self):
        pairs = [(True, False)] * 8 + [(True, True)] * 4
        result = paired_inference(pairs, labels=("x", "y"))
        self.assertEqual(result["n_pairs"], 12)
        self.assertEqual(result["a_wins"], 8)
        self.assertEqual(result["b_wins"], 0)
        self.assertEqual(result["ties"], 4)
        self.assertAlmostEqual(result["sign_test_p"], 2 / 256, places=6)
        self.assertEqual(result["verdict"], "x better")
        self.assertGreater(result["diff"], 0)
        self.assertLess(result["ci95"][0], result["ci95"][1])

    def test_unpaired_tasks_are_dropped_and_counted(self):
        result = paired_inference([(True, None), (None, False), (True, False)])
        self.assertEqual(result["n_pairs"], 1)
        self.assertEqual(result["dropped_unpaired"], 2)

    def test_rates_pair_as_well_as_booleans(self):
        pairs = [(0.75, 0.25)] * 10 + [(0.5, 0.5)] * 2
        result = paired_inference(pairs)
        self.assertEqual(result["a_wins"], 10)
        self.assertEqual(result["ties"], 2)
        self.assertAlmostEqual(result["diff"], 0.4167, places=3)


class TestClusteredSE(unittest.TestCase):
    def test_homogeneous_clusters_inflate_the_error_bar(self):
        # four clusters, each internally identical: the naive SE treats
        # 40 observations as independent; the clustered one knows there
        # are really 4 draws
        values = [1.0] * 10 + [0.0] * 10 + [1.0] * 10 + [0.0] * 10
        clusters = ["a"] * 10 + ["b"] * 10 + ["c"] * 10 + ["d"] * 10
        result = clustered_se(values, clusters)
        self.assertEqual(result["clusters"], 4)
        self.assertGreater(result["clustered_se"], result["naive_se"])
        self.assertGreater(result["ratio"], 1.0)

    def test_independent_units_keep_the_bars_close(self):
        values = [1.0, 0.0] * 20
        clusters = [str(i) for i in range(40)]   # every unit its own cluster
        result = clustered_se(values, clusters)
        # with one unit per cluster the sandwich collapses to the naive
        # estimator up to the small-sample factor
        self.assertAlmostEqual(result["ratio"], 1.0, delta=0.05)

    def test_degenerate_inputs_say_so(self):
        self.assertIsNone(clustered_se([1.0], ["a"])["clustered_se"])
        self.assertIn("fewer", clustered_se([1.0], ["a"])["note"])


class TestIntegration(unittest.TestCase):
    def test_bench_scorecard_carries_the_clustered_interval(self):
        result = run_benchmark(ROOT / "demo" / "diagnosis_bench" / "traces")
        clustered = result["overall"]["clustered_by_cause"]
        self.assertEqual(clustered["n"], result["overall"]["total"])
        self.assertGreater(clustered["clusters"], 1)
        self.assertIsNotNone(clustered["ci95_clustered"])

    def test_runs_command_prints_paired_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "deepcompare", "runs",
                 str(ROOT / "demo" / "runs" / "traces"), "-o", tmp],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Paired inference over", proc.stdout)
            agg = json.loads((Path(tmp) / "aggregate.json").read_text())
            paired = agg["paired_inference"]
            self.assertIn("verdict", paired)
            self.assertEqual(paired["n_pairs"],
                             paired["a_wins"] + paired["b_wins"] + paired["ties"])


if __name__ == "__main__":
    unittest.main()
