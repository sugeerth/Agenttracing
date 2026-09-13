"""Tests for model-telemetry fusion (SCHEMA.md v12).

The flagged/silent split is the contract: calling a silent failure "flagged"
would tell a user to install a confidence gate that cannot work.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.uncertainty import (
    analyze,
    calibration_profile,
    confidence_series,
    has_telemetry,
)


def step(index, stype, name, confidence=None, quality=None, text="text"):
    payload = {
        "index": index, "type": stype, "name": name,
        "input": f"{name} {text}", "output": f"{name} output {text}",
        "tokens": 100, "latency_s": 1.0, "quality": quality, "note": None,
    }
    if confidence is not None:
        payload["model"] = {
            "confidence": confidence,
            "min_token_confidence": max(0.01, confidence - 0.2),
            "entropy": round((1 - confidence) * 2, 4),
            "tokens_scored": 50,
            "temperature": 0.3,
        }
    return payload


def traj(agent, success, steps, task="t1"):
    return Trajectory.from_json({
        "schema_version": 1, "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "gold answer"},
        "outcome": {"success": success, "answer": "answer",
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 100, "output_tokens": 100,
                   "cost_usd": 0.01, "latency_s": 4.0},
        "steps": steps,
    })


def good_run(agent="good", confidences=(0.92, 0.90, 0.91, 0.93)):
    return traj(agent, True, [
        step(0, "plan", "plan", confidences[0]),
        step(1, "search", "web_search", confidences[1]),
        step(2, "retrieve", "official_filing", confidences[2], text="official source"),
        step(3, "answer", "final", confidences[3]),
    ])


def failing_run(agent, confidence_at_fault):
    """Same shape, but step 2 picks a bad source and the run fails."""
    return traj(agent, False, [
        step(0, "plan", "plan", 0.92),
        step(1, "search", "web_search", 0.90),
        step(2, "retrieve", "random_blog", confidence_at_fault,
             quality="bad", text="unreliable blog"),
        step(3, "answer", "final", 0.89),
    ])


class TestTelemetryDetection(unittest.TestCase):
    def test_absent_telemetry_is_reported_not_faked(self):
        a = traj("a", True, [step(0, "plan", "plan"), step(1, "answer", "final")])
        b = traj("b", False, [step(0, "plan", "plan"), step(1, "answer", "final")])
        report = compare(a, b)
        self.assertFalse(has_telemetry(a))
        self.assertEqual(report["uncertainty"], {"available": False})

    def test_partial_telemetry_keeps_none_placeholders(self):
        run = traj("a", True, [
            step(0, "plan", "plan", 0.9),
            step(1, "answer", "final"),
        ])
        self.assertEqual(confidence_series(run), [0.9, None])
        self.assertTrue(has_telemetry(run))


class TestSignalClassification(unittest.TestCase):
    def test_confidence_collapse_is_flagged(self):
        report = compare(good_run("good"), failing_run("shaky", 0.45))
        signal = report["uncertainty"]["signal"]
        self.assertEqual(signal["verdict"], "flagged")
        self.assertEqual(signal["failed_agent"], "b")
        self.assertGreater(signal["drop"], 0.15)
        self.assertIn("confidence gate", signal["mitigation"])

    def test_steady_confidence_while_wrong_is_silent(self):
        report = compare(good_run("good"), failing_run("smooth", 0.91))
        signal = report["uncertainty"]["signal"]
        self.assertEqual(signal["verdict"], "silent")
        self.assertIn("verification", signal["mitigation"])
        self.assertTrue(report["uncertainty"]["calibration"]["confident_when_wrong"])

    def test_silent_failure_is_never_called_flagged(self):
        # The dangerous misreport: telling a user a gate would have caught it.
        report = compare(good_run("good"), failing_run("smooth", 0.95))
        narrative = report["uncertainty"]["narrative"]
        self.assertIn("no warning", narrative)
        self.assertNotIn("would have caught", narrative)

    def test_baseline_excludes_the_root_step_itself(self):
        # A run that is uniformly unconfident has no *drop* at the fault step.
        flat = traj("flat", False, [
            step(0, "plan", "plan", 0.55),
            step(1, "search", "web_search", 0.55),
            step(2, "retrieve", "random_blog", 0.55, quality="bad",
                 text="unreliable blog"),
            step(3, "answer", "final", 0.55),
        ])
        report = compare(good_run("good"), flat)
        signal = report["uncertainty"]["signal"]
        self.assertEqual(signal["verdict"], "silent")
        self.assertAlmostEqual(signal["drop"], 0.0, places=3)

    def test_no_signal_when_both_succeed(self):
        report = compare(good_run("a"), good_run("b"))
        self.assertTrue(report["uncertainty"]["available"])
        self.assertIsNone(report["uncertainty"]["signal"])

    def test_stats_summarize_each_side(self):
        report = compare(good_run("good"), failing_run("shaky", 0.40))
        stats = report["uncertainty"]["b"]
        self.assertEqual(stats["min_confidence"], 0.40)
        self.assertEqual(stats["steps_scored"], 4)
        self.assertIsNotNone(stats["mean_entropy"])


class TestCalibrationProfile(unittest.TestCase):
    def reports(self):
        return [
            compare(good_run("good"), failing_run("smooth", 0.93)),
            compare(good_run("good", (0.9, 0.9, 0.9, 0.9)),
                    failing_run("smooth", 0.90)),
        ]

    def test_silent_failing_agent_flagged_as_such(self):
        profile = calibration_profile(self.reports())
        self.assertTrue(profile["available"])
        row = profile["agents"]["smooth"]
        self.assertEqual(row["verdict"], "silent-failing")
        self.assertEqual(row["flagged"], 0)
        self.assertIn("verification steps", profile["narrative"])

    def test_supervisable_agent_flagged_as_such(self):
        reports = [compare(good_run("good"), failing_run("shaky", 0.35))]
        profile = calibration_profile(reports)
        self.assertEqual(profile["agents"]["shaky"]["verdict"], "supervisable")
        self.assertIn("signal their own mistakes", profile["narrative"])

    def test_no_telemetry_yields_unavailable(self):
        plain_a = traj("a", True, [step(0, "plan", "p"), step(1, "answer", "f")])
        plain_b = traj("b", False, [step(0, "plan", "p"), step(1, "answer", "f")])
        profile = calibration_profile([compare(plain_a, plain_b)])
        self.assertFalse(profile["available"])


class TestDemoTelemetry(unittest.TestCase):
    """The shipped demo must actually demonstrate both failure classes."""

    @classmethod
    def setUpClass(cls):
        cls.traces = Path(__file__).resolve().parent.parent / "demo" / "telemetry" / "traces"
        if not cls.traces.is_dir():
            raise unittest.SkipTest("demo telemetry traces not generated")

    def report_for(self, task):
        a = Trajectory.from_json(self.traces / f"{task}__atlas-v2.json")
        b = Trajectory.from_json(self.traces / f"{task}__bolt-v3.json")
        return compare(a, b)

    def test_bolt_fails_silently_on_retrieval(self):
        signal = self.report_for("t01_acme_revenue")["uncertainty"]["signal"]
        self.assertEqual(signal["failed_agent"], "b")
        self.assertEqual(signal["verdict"], "silent")

    def test_atlas_failure_is_flagged_by_its_own_confidence(self):
        signal = self.report_for("t07_build_failure")["uncertainty"]["signal"]
        self.assertEqual(signal["failed_agent"], "a")
        self.assertEqual(signal["verdict"], "flagged")

    def test_telemetry_is_labelled_synthetic(self):
        traj_obj = Trajectory.from_json(self.traces / "t01_acme_revenue__atlas-v2.json")
        for step_obj in traj_obj.steps:
            self.assertEqual(step_obj.model["source"], "synthetic-demo")


class TestSchemaValidation(unittest.TestCase):
    def test_confidence_out_of_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "probability"):
            traj("a", True, [
                step(0, "plan", "plan", 1.4),
                step(1, "answer", "final", 0.9),
            ])

    def test_negative_entropy_rejected(self):
        bad = {
            "index": 0, "type": "plan", "name": "plan", "input": "i", "output": "o",
            "tokens": 1, "latency_s": 0.1, "quality": None, "note": None,
            "model": {"confidence": 0.5, "entropy": -1.0},
        }
        with self.assertRaisesRegex(ValueError, "entropy"):
            traj("a", True, [bad, step(1, "answer", "final")])

    def test_non_object_model_rejected(self):
        bad = {
            "index": 0, "type": "plan", "name": "plan", "input": "i", "output": "o",
            "tokens": 1, "latency_s": 0.1, "quality": None, "note": None,
            "model": "high",
        }
        with self.assertRaisesRegex(ValueError, "model must be an object"):
            traj("a", True, [bad, step(1, "answer", "final")])


if __name__ == "__main__":
    unittest.main()
