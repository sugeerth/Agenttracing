"""The cluster-robust error bar (statistics.clustered_se): undefined with one cluster rather than 0, the CR2 sandwich
with a t critical value on Bell-McCaffrey degrees of freedom (G-1 when clusters are the same size), a note when
clusters are few, and an interval that stays within the data."""

from __future__ import annotations

import math
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

    def test_equal_clusters_reduce_to_cr1_on_g_minus_one(self):
        rng = random.Random(2)
        vals = [float(rng.random() < 0.7) for _ in range(7 * 30)]
        cl = [i // 30 for i in range(len(vals))]
        r = clustered_se(vals, cl)
        mean = sum(vals) / len(vals)
        sums = [sum(v - mean for v, c in zip(vals, cl) if c == g) for g in range(7)]
        cr1 = math.sqrt(sum(s * s for s in sums) / len(vals) ** 2 * 7 / 6)
        self.assertAlmostEqual(r["clustered_se"], round(cr1, 6), places=6)
        self.assertEqual(r["df"], 6)
        self.assertAlmostEqual(r["critical"], round(t_critical(6), 4))

    def test_unequal_clusters_have_fewer_degrees_of_freedom_and_a_wider_interval(self):
        """One family of 300 beside nine of 10: the big one carries most of the weight, so the interval rests
        on fewer effective clusters than 10."""
        rng = random.Random(4)
        sizes = [300] + [10] * 9
        cl = [g for g, m in enumerate(sizes) for _ in range(m)]
        vals = [float(rng.random() < (0.9 if g % 2 else 0.5)) for g in cl]
        r = clustered_se(vals, cl)
        self.assertLess(r["df"], 9)
        mean = sum(vals) / len(vals)
        sums = [sum(v - mean for v, c in zip(vals, cl) if c == g) for g in range(10)]
        cr1 = math.sqrt(sum(s * s for s in sums) / len(vals) ** 2 * 10 / 9)
        self.assertGreater(r["ci95_clustered"][1] - r["ci95_clustered"][0], 2 * t_critical(9) * cr1)

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

    def test_coverage_with_skewed_family_sizes(self):
        """10 families with lognormal sizes (median 55, a few in the hundreds): the CR1 interval on G-1 degrees
        of freedom held the mean 0.80 of the time in 2,000 simulated corpora, this one 0.85. Here, over 300, it
        holds it more often than CR1 does."""
        rng = random.Random(23)
        mix = [0.98, 0.98, 0.98, 0.9, 0.5, 0.1]
        truth = sum(mix) / len(mix)
        held = held_cr1 = 0
        for _ in range(300):
            vals, cl = [], []
            for g in range(10):
                p = rng.choice(mix)
                for _ in range(max(5, int(rng.lognormvariate(4, 1.0)))):
                    vals.append(1.0 if rng.random() < p else 0.0)
                    cl.append(g)
            r = clustered_se(vals, cl)
            lo, hi = r["ci95_clustered"]
            held += lo <= truth <= hi
            n = len(vals)
            mean = sum(vals) / n
            sums = {}
            for v, c in zip(vals, cl):
                sums[c] = sums.get(c, 0.0) + v - mean
            cr1 = math.sqrt(sum(s * s for s in sums.values()) / n ** 2 * 10 / 9)
            held_cr1 += mean - t_critical(9) * cr1 <= truth <= mean + t_critical(9) * cr1
        self.assertGreater(held, held_cr1 + 6, (held, held_cr1))
        self.assertGreaterEqual(held / 300, 0.82)


if __name__ == "__main__":
    unittest.main()
