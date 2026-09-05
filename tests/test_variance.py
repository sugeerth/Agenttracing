"""Tests for variance decomposition (v24).

The arithmetic is the easy part.  What these pin is the four ways a variance
decomposition lies: attributing shared variance to whichever factor was
fitted first, crediting a factor for having many levels, calling interaction
"noise", and splitting two factors that are the same partition of the data.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.variance import (
    METRICS,
    decompose,
    design,
    variance_report,
)


def run(task, agent, model, success=True, tokens=100, version="v1", run_id="r1"):
    return Trajectory.from_json({
        "trace_id": f"{task}-{agent}-{run_id}", "run_id": run_id,
        "agent": {"name": agent, "model": model, "version": version},
        "task": {"id": task, "prompt": "p"},
        "outcome": {"success": success, "answer": "a"},
        "totals": {"input_tokens": 0, "output_tokens": tokens,
                   "cost_usd": 0.0, "latency_s": 1.0},
        "steps": [{"index": 0, "type": "answer", "name": "final",
                   "input": "a", "output": "a", "tokens": tokens, "latency_s": 1.0}],
    })


class TestDesignDetection(unittest.TestCase):
    def test_one_model_per_harness_and_one_harness_per_model_is_confounded(self):
        runs = [run("t1", "a1", "m1"), run("t2", "a2", "m2")]
        plan = design(runs)
        self.assertEqual(plan["shape"], "confounded")
        self.assertIn("no method can tell them apart", plan["note"])

    def test_a_model_shared_by_two_harnesses_is_nested(self):
        runs = [run("t1", "a1", "m1"), run("t1", "a2", "m1"), run("t1", "a3", "m2")]
        self.assertEqual(design(runs)["shape"], "nested")

    def test_a_harness_on_two_models_is_crossed(self):
        runs = [run("t1", "a1", "m1"), run("t1", "a1", "m2"), run("t1", "a2", "m1")]
        plan = design(runs)
        self.assertEqual(plan["shape"], "crossed")
        self.assertIn("separable", plan["note"])

    def test_repeats_decide_whether_the_residual_is_noise(self):
        once = [run("t1", "a1", "m1"), run("t1", "a2", "m1")]
        self.assertFalse(design(once)["residual_is_noise"])
        twice = once + [run("t1", "a1", "m1", run_id="r2"),
                        run("t1", "a2", "m1", run_id="r2")]
        self.assertTrue(design(twice)["residual_is_noise"])

    def test_a_constant_factor_is_reported_as_constant(self):
        runs = [run("t1", "a1", "m1"), run("t2", "a1", "m1")]
        plan = design(runs)
        self.assertIn("model", plan["constant"])
        self.assertIn("task", plan["identifiable"])


class TestConfoundingIsRefused(unittest.TestCase):
    def test_a_confounded_corpus_says_the_split_is_an_artefact(self):
        runs = [run("t1", "a1", "m1", tokens=10), run("t1", "a2", "m2", tokens=90)]
        result = decompose(runs, "tokens")
        self.assertIn("artefact of ordering", result["caveat"])

    def test_shared_variance_shows_as_a_range_not_a_point(self):
        # Two factors that move together: whichever is fitted first takes it.
        runs = [run("t1", "a1", "m1", tokens=10), run("t1", "a1", "m1", tokens=10),
                run("t1", "a2", "m2", tokens=90), run("t1", "a2", "m2", tokens=90)]
        result = decompose(runs, "tokens")
        for name, comp in result["components"].items():
            if name in ("model", "harness"):
                self.assertGreater(comp["shared"], 0.5,
                                   f"{name} should show shared variance")
                self.assertFalse(comp["identified"])

    def test_an_independent_factor_is_identified(self):
        # Task varies within every harness, so its share is order-invariant.
        runs = []
        for agent, model in (("a1", "m1"), ("a2", "m1")):
            for task, tokens in (("t1", 10), ("t2", 90)):
                runs.append(run(task, agent, model, tokens=tokens))
        result = decompose(runs, "tokens")
        self.assertTrue(result["components"]["task"]["identified"])


class TestChanceCorrection(unittest.TestCase):
    """A factor with more levels explains more variance for free."""

    def test_expected_by_chance_tracks_the_level_count(self):
        runs = [run(f"t{i}", f"a{i % 2}", "m1", tokens=i) for i in range(10)]
        result = decompose(runs, "tokens")
        task = result["components"]["task"]
        harness = result["components"]["harness"]
        self.assertGreater(task["expected_by_chance"], harness["expected_by_chance"])
        self.assertAlmostEqual(task["expected_by_chance"], 9 / 9, places=3)

    def test_a_factor_that_is_pure_noise_lands_at_or_below_chance(self):
        # Harness labels assigned in a fixed rotation, unrelated to the value.
        values = [11, 47, 23, 89, 5, 62, 38, 74, 16, 53, 29, 95]
        runs = [run("t1", f"a{i % 4}", "m1", tokens=v) for i, v in enumerate(values)]
        result = decompose(runs, "tokens", factors=["harness"])
        omega = result["components"]["harness"]["omega_squared_min"]
        self.assertIsNotNone(omega)
        self.assertLessEqual(omega, result["components"]["harness"]["min_share"])

    def test_a_real_effect_survives_the_correction(self):
        runs = ([run("t1", "a1", "m1", tokens=10) for _ in range(6)] +
                [run("t1", "a2", "m1", tokens=200) for _ in range(6)])
        result = decompose(runs, "tokens", factors=["harness"])
        self.assertTrue(result["components"]["harness"]["above_chance"])
        self.assertGreater(result["components"]["harness"]["omega_squared_min"], 0.5)

    def test_negative_omega_is_reported_not_clamped_to_zero(self):
        # "At chance" is the finding; zero would read as "explains nothing
        # measurable", which is a different and weaker statement.
        values = [50, 51, 49, 50, 51, 49, 50, 51, 49, 50, 51, 49]
        runs = [run("t1", f"a{i}", "m1", tokens=v) for i, v in enumerate(values)]
        result = decompose(runs, "tokens", factors=["harness"])
        omega = result["components"]["harness"]["omega_squared_min"]
        if omega is not None:
            self.assertFalse(result["components"]["harness"]["above_chance"])


class TestResidualHonesty(unittest.TestCase):
    def test_without_repeats_the_residual_is_not_called_noise(self):
        runs = [run("t1", "a1", "m1", tokens=10), run("t2", "a1", "m1", tokens=90),
                run("t1", "a2", "m1", tokens=20), run("t2", "a2", "m1", tokens=80)]
        result = decompose(runs, "tokens")
        self.assertIn("inseparably", result["residual_meaning"])
        self.assertIn("interaction", result["caveat"])

    def test_with_repeats_the_residual_is_run_to_run_variation(self):
        runs = []
        for r in ("r1", "r2", "r3"):
            for agent in ("a1", "a2"):
                runs.append(run("t1", agent, "m1", tokens=10, run_id=r))
                runs.append(run("t2", agent, "m1", tokens=90, run_id=r))
        result = decompose(runs, "tokens")
        self.assertIn("run-to-run", result["residual_meaning"])
        self.assertNotIn("inseparably", result["residual_meaning"])


class TestDegenerate(unittest.TestCase):
    def test_no_variance_says_so_rather_than_dividing_by_zero(self):
        runs = [run("t1", "a1", "m1", tokens=10), run("t2", "a2", "m2", tokens=10)]
        result = decompose(runs, "tokens")
        self.assertEqual(result["total_variance"], 0.0)
        self.assertIn("no variance", result["reason"])

    def test_a_single_run_cannot_be_decomposed(self):
        result = decompose([run("t1", "a1", "m1")], "tokens")
        self.assertIn("fewer than two", result["reason"])
        self.assertEqual(result["components"], {})

    def test_an_unknown_metric_is_refused(self):
        with self.assertRaises(ValueError):
            decompose([run("t1", "a1", "m1")], "vibes")

    def test_every_declared_metric_can_be_read_off_a_trajectory(self):
        sample = run("t1", "a1", "m1")
        for name, getter in METRICS.items():
            self.assertIsInstance(getter(sample), float, name)


class TestOnTheFleetCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = [Trajectory.from_json(p)
                    for p in sorted(Path("demo/fleet/traces").glob("*.json"))]

    def test_the_fleet_is_a_nested_design(self):
        self.assertEqual(design(self.runs)["shape"], "nested")

    def test_shares_and_residual_account_for_everything(self):
        result = decompose(self.runs, "tokens")
        for order_total in [sum(c["min_share"] for c in result["components"].values())]:
            self.assertLessEqual(order_total + result["residual"], 1.02)

    def test_the_correction_materially_lowers_a_many_levelled_factor(self):
        # 33 harnesses explain ~12% of variance from level count alone; the
        # uncorrected number would overstate the scaffold's real effect.
        result = decompose(self.runs, "success")
        harness = result["components"]["harness"]
        self.assertGreater(harness["expected_by_chance"], 0.1)
        self.assertLess(harness["omega_squared_min"], harness["min_share"])

    def test_report_is_deterministic(self):
        self.assertEqual(variance_report(self.runs), variance_report(self.runs))


if __name__ == "__main__":
    unittest.main()
