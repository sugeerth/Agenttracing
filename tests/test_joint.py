"""Tests for the joint attribute model (v16).

The properties that matter: the fit must be reproducible (a gate may depend
on it), it must not blow up on the perfectly separating attributes eval
corpora routinely contain, and it must say plainly when the sample is too
small to read.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.joint import MAX_ITER, joint_attribute_model


def step(index, stype, name, text="text", quality=None, confidence=None):
    payload = {"index": index, "type": stype, "name": name,
               "input": f"{name} {text}", "output": f"{name} out {text}",
               "tokens": 100, "latency_s": 1.0, "quality": quality, "note": None}
    if confidence is not None:
        payload["model"] = {"confidence": confidence}
    return payload


def traj(task, success, steps, agent="a"):
    return Trajectory.from_json({
        "schema_version": 1, "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "gold"},
        "outcome": {"success": success, "answer": "answer",
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 200, "output_tokens": 200,
                   "cost_usd": 0.02, "latency_s": float(len(steps))},
        "steps": steps,
    })


CLEAN = [step(0, "plan", "plan"), step(1, "search", "web_search"),
         step(2, "retrieve", "select_result"), step(3, "answer", "final")]


def dirty(extra_tool=False):
    steps = [step(0, "plan", "plan"), step(1, "search", "web_search"),
             step(2, "retrieve", "select_result", "blog", quality="bad")]
    if extra_tool:
        steps.append(step(3, "read", "open_page"))
    steps.append(step(len(steps), "answer", "final"))
    return steps


class TestFitBehaviour(unittest.TestCase):
    def mixed_corpus(self):
        runs = []
        for i in range(10):
            runs.append(traj(f"ok{i}", True, CLEAN))
        for i in range(10):
            runs.append(traj(f"bad{i}", False, dirty(extra_tool=i % 2 == 0)))
        # A few failures without the bad step, so nothing separates perfectly.
        for i in range(3):
            runs.append(traj(f"oddfail{i}", False, CLEAN))
        return runs

    def test_deterministic(self):
        corpus = self.mixed_corpus()
        self.assertEqual(joint_attribute_model(corpus),
                         joint_attribute_model(corpus))

    def test_converges_and_reports_iterations(self):
        model = joint_attribute_model(self.mixed_corpus())
        self.assertTrue(model["available"])
        self.assertTrue(model["converged"])
        self.assertLessEqual(model["iterations"], MAX_ITER)

    def test_harmful_attribute_gets_a_positive_coefficient(self):
        model = joint_attribute_model(self.mixed_corpus())
        row = next(r for r in model["coefficients"]
                   if r["attribute"] == "poor_quality_step")
        self.assertGreater(row["coefficient"], 0.0)
        self.assertGreater(row["odds_ratio"], 1.0)
        self.assertEqual(row["direction"], "raises")

    def test_coefficients_sorted_by_magnitude(self):
        model = joint_attribute_model(self.mixed_corpus())
        magnitudes = [abs(r["coefficient"]) for r in model["coefficients"]]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))

    def test_odds_ratio_matches_coefficient(self):
        import math
        model = joint_attribute_model(self.mixed_corpus())
        for row in model["coefficients"]:
            self.assertAlmostEqual(
                row["odds_ratio"], math.exp(row["coefficient"]), places=2)


class TestSeparation(unittest.TestCase):
    def separating_corpus(self):
        """Every run with a bad step fails; none without does."""
        runs = [traj(f"ok{i}", True, CLEAN) for i in range(8)]
        runs += [traj(f"bad{i}", False, dirty()) for i in range(8)]
        return runs

    def test_separation_does_not_produce_infinities(self):
        model = joint_attribute_model(self.separating_corpus())
        self.assertTrue(model["available"])
        for row in model["coefficients"]:
            self.assertTrue(abs(row["coefficient"]) < 1e6, row)
            self.assertEqual(row["coefficient"], row["coefficient"])  # not NaN

    def test_separation_is_flagged_and_explained(self):
        model = joint_attribute_model(self.separating_corpus())
        separating = [r for r in model["coefficients"] if r["separates"]]
        self.assertTrue(separating)
        self.assertIn("separate", model["narrative"])
        self.assertIn("penalty", model["narrative"])

    def test_ridge_is_reported_so_the_shrinkage_is_visible(self):
        model = joint_attribute_model(self.separating_corpus())
        self.assertIn("ridge", model["method"])
        self.assertGreater(model["ridge"], 0)


class TestGuards(unittest.TestCase):
    def test_empty_corpus(self):
        model = joint_attribute_model([])
        self.assertFalse(model["available"])
        self.assertIn("no runs", model["reason"])

    def test_all_same_outcome_is_unmodellable(self):
        runs = [traj(f"t{i}", True, CLEAN) for i in range(6)]
        model = joint_attribute_model(runs)
        self.assertFalse(model["available"])
        self.assertIn("same outcome", model["reason"])

    def test_constant_attributes_are_dropped_and_named(self):
        runs = [traj(f"t{i}", i % 2 == 0, CLEAN) for i in range(6)]
        model = joint_attribute_model(runs)
        dropped = {d["attribute"] for d in model.get("dropped", [])}
        self.assertTrue(dropped)
        for entry in model["dropped"]:
            self.assertIn("reason", entry)

    def test_partially_measurable_attribute_is_dropped_not_imputed(self):
        runs = [
            traj("t1", True, [step(0, "plan", "p", confidence=0.9),
                              step(1, "answer", "f", confidence=0.9)]),
            traj("t2", False, [step(0, "plan", "p"), step(1, "answer", "f")]),
            traj("t3", True, CLEAN),
            traj("t4", False, dirty()),
        ]
        model = joint_attribute_model(runs)
        dropped = {d["attribute"] for d in model.get("dropped", [])}
        self.assertIn("low_confidence_step", dropped)

    def test_small_sample_is_marked_unreliable_and_said_so(self):
        runs = [traj("t1", True, CLEAN), traj("t2", False, dirty()),
                traj("t3", True, CLEAN), traj("t4", False, dirty())]
        model = joint_attribute_model(runs)
        if model["available"]:
            self.assertFalse(model["reliable"])
            self.assertIn("indicative only", model["narrative"])

    def test_caveat_keeps_the_claim_associational(self):
        runs = [traj(f"ok{i}", True, CLEAN) for i in range(6)]
        runs += [traj(f"bad{i}", False, dirty()) for i in range(6)]
        model = joint_attribute_model(runs)
        self.assertIn("associations", model["caveat"])
        self.assertIn("not for task difficulty", model["caveat"])


class TestAgainstRealFleet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "fleet" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo fleet traces not present")
        cls.trajs = [Trajectory.from_json(p) for p in sorted(traces.glob("*.json"))]

    def test_fits_and_is_reliable_on_the_full_corpus(self):
        model = joint_attribute_model(self.trajs)
        self.assertTrue(model["available"])
        self.assertTrue(model["converged"])
        self.assertTrue(model["reliable"])

    def test_quality_annotation_dominates_jointly_too(self):
        model = joint_attribute_model(self.trajs)
        top = model["coefficients"][0]
        self.assertEqual(top["attribute"], "poor_quality_step")
        self.assertGreater(top["coefficient"], 0.0)


if __name__ == "__main__":
    unittest.main()
