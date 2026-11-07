"""How many task verdicts a rerun of the whole suite would change with nothing changed
(reliability.rerun_flips; statistics.poisson_binomial and central_range)."""

from __future__ import annotations

import math
import random
import sys
import unittest
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.reliability import FLIP_RANGE_MIN_RUNS, reliability, rerun_flips  # noqa: E402
from agentdiff.statistics import central_range, poisson_binomial  # noqa: E402
from test_pass_k_interval import _run  # noqa: E402


class CountDistributionTest(unittest.TestCase):
    def test_poisson_binomial_is_exact(self):
        """Against enumerating every outcome of the events."""
        chances = [0.1, 0.5, 0.75, 0.0, 1.0]
        want = [0.0] * (len(chances) + 1)
        for outcome in product((0, 1), repeat=len(chances)):
            want[sum(outcome)] += math.prod(q if o else 1 - q for q, o in zip(chances, outcome))
        for got, expect in zip(poisson_binomial(chances), want):
            self.assertAlmostEqual(got, expect, places=12)
        self.assertEqual(poisson_binomial([]), [1.0])

    def test_equal_chances_give_the_binomial(self):
        dist = poisson_binomial([0.3] * 8)
        for j, mass in enumerate(dist):
            self.assertAlmostEqual(mass, math.comb(8, j) * 0.3 ** j * 0.7 ** (8 - j), places=12)

    def test_central_range_leaves_at_most_the_tail_each_side(self):
        dist = poisson_binomial([0.3] * 40)
        lo, hi = central_range(dist, 0.90)
        self.assertLessEqual(sum(dist[:lo]), 0.05 + 1e-12)
        self.assertLessEqual(sum(dist[hi + 1:]), 0.05 + 1e-12)
        self.assertGreater(sum(dist[:lo + 1]), 0.05, "and it is the tightest such range")
        self.assertGreater(sum(dist[hi:]), 0.05)
        self.assertEqual(central_range([1.0], 0.90), [0, 0])


class RerunFlipsTest(unittest.TestCase):
    def test_the_pairs_that_disagree(self):
        rows = [{"task": "half", "runs": 4, "successes": 2}, {"task": "always", "runs": 3, "successes": 3},
                {"task": "one", "runs": 1, "successes": 1}]
        f = rerun_flips(rows)
        self.assertEqual((f["tasks"], f["tasks_without_two_runs"], f["tasks_that_can_flip"]), (2, 1, 1))
        self.assertEqual(f["per_task"], [{"task": "half", "runs": 4, "successes": 2, "flip_chance": 0.6667}])
        self.assertEqual(f["expected_flips"], 0.67)
        self.assertIsNone(f["range"], "3 runs at the thinnest task is too few for a range")
        self.assertIn("10 needed", f["says"])
        self.assertIsNone(rerun_flips([{"task": "x", "runs": 1, "successes": 0}])["expected_flips"])

    def test_the_estimate_is_unbiased(self):
        """Exact over the binomial: the share of disagreeing pairs averages to 2p(1-p) at any run count."""
        for n in (2, 3, 7):
            for p in (0.1, 0.5, 0.9):
                mean = sum(math.comb(n, c) * p ** c * (1 - p) ** (n - c) * 2 * c * (n - c) / (n * (n - 1))
                           for c in range(n + 1))
                self.assertAlmostEqual(mean, 2 * p * (1 - p), places=12, msg=(n, p))

    def test_the_range_from_enough_runs_holds_fresh_reruns(self):
        """Synthetic, seeded: 30 tasks with rates mixed between settled and not, 10 runs each; two fresh runs a
        task land inside the 90% range about 90% of the time."""
        rng = random.Random(3)
        hit, trials = 0, 300
        for _ in range(trials):
            ps = [rng.choice((1.0, 0.0, rng.random())) for _ in range(30)]
            rows = [{"task": str(i), "runs": FLIP_RANGE_MIN_RUNS,
                     "successes": sum(rng.random() < p for _ in range(FLIP_RANGE_MIN_RUNS))} for i, p in enumerate(ps)]
            f = rerun_flips(rows)
            fresh = sum((rng.random() < p) != (rng.random() < p) for p in ps)
            hit += f["range"][0] <= fresh <= f["range"][1]
        self.assertGreaterEqual(hit / trials, 0.85)

    def test_reliability_carries_it(self):
        outcomes = {"flaky": [True, False, True, False], "always": [True] * 4}
        data = {t: {"a": [_run(t, ok, r) for r, ok in enumerate(runs)]} for t, runs in outcomes.items()}
        f = reliability(data)["per_agent"]["a"]["rerun_flips"]
        self.assertEqual((f["tasks"], f["expected_flips"], f["fewest_runs"]), (2, 0.67, 4))


if __name__ == "__main__":
    unittest.main()
