"""The cluster-robust error bar (statistics.clustered_se): undefined with one cluster rather than 0, a t critical
value on G-1 degrees of freedom, a note when clusters are few, and an interval that stays within the data."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.statistics import FEW_CLUSTERS, clustered_se, t_critical  # noqa: E402


class TCriticalTest(unittest.TestCase):
    def test_against_the_published_table(self):
        for df, want in ((1, 12.706), (4, 2.776), (9, 2.262), (18, 2.101), (30, 2.042)):
            self.assertAlmostEqual(t_critical(df), want, places=3, msg=df)
        self.assertLess(t_critical(1000), 1.97)


class ClusteredTest(unittest.TestCase):
    def test_one_cluster_has_no_clustered_error_bar(self):
        r = clustered_se([1, 0, 1, 1, 0, 1], ["a"] * 6)
        self.assertIsNone(r["clustered_se"])
        self.assertIsNone(r["ci95_clustered"])
        self.assertIsNone(r["ratio"], "a ratio of 0 said the naive bar overstated, the reverse of the truth")
        self.assertIsNotNone(r["ci95_naive"])

    def test_the_interval_uses_t_on_clusters_minus_one(self):
        vals = [1.0] * 30 + [0.0] * 10 + [1.0] * 20 + [0.0] * 20
        r = clustered_se(vals, ["a"] * 40 + ["b"] * 40)
        self.assertAlmostEqual(r["critical"], 12.706, places=3, msg="two clusters: one degree of freedom")
        self.assertGreater(r["clustered_se"], 0)

    def test_few_clusters_say_so(self):
        vals = [float(i % 2) for i in range(40)]
        few = clustered_se(vals, [i % 4 for i in range(40)])
        self.assertIn("only 4 clusters", few["note"])
        many = clustered_se(vals * 3, [i % 24 for i in range(120)])
        self.assertGreaterEqual(many["clusters"], FEW_CLUSTERS)
        self.assertIn("honest", many["note"])

    def test_an_accuracy_interval_stops_at_one(self):
        vals = [1.0] * 95 + [0.0] * 5
        r = clustered_se(vals, [i // 10 for i in range(100)])
        self.assertLessEqual(r["ci95_naive"][1], 1.0)
        self.assertLessEqual(r["ci95_clustered"][1], 1.0)

    def test_coverage_on_a_labelled_synthetic_corpus(self):
        """19 families of 60, each family's accuracy drawn from a bimodal mix (as the bench's are): the clustered
        interval covers the population mean at least 90% of the time."""
        rng = random.Random(11)
        mix = [0.98, 0.98, 0.98, 0.9, 0.5, 0.1]
        truth = sum(mix) / len(mix)
        held = 0
        for _ in range(300):
            vals, cl = [], []
            for g in range(19):
                p = rng.choice(mix)
                for _ in range(60):
                    vals.append(1.0 if rng.random() < p else 0.0)
                    cl.append(g)
            lo, hi = clustered_se(vals, cl)["ci95_clustered"]
            held += lo <= truth <= hi
        self.assertGreaterEqual(held / 300, 0.9)


if __name__ == "__main__":
    unittest.main()
