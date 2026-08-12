"""Tests for conformance checking against reference trajectories (v11).

The verdict ladder is the contract here: a run that reaches the reference
outcome by a different path must not be reported as a regression, and a run
that changes the outcome must never be filed as benign drift.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.conformance import (
    check_run,
    check_suite,
    render_conformance_markdown,
)

REFERENCE_STEPS = [
    ("plan", "plan", "find the fy2025 revenue in the official filing"),
    ("search", "web_search", "acme fy2025 annual results revenue"),
    ("read", "open_page", "https://ir.acmecorp.com/news/fy2025-results"),
    ("answer", "final_answer", "compose the answer from the filing"),
]


def make_traj(agent, task, success, steps, cost=0.01):
    step_dicts = [
        {"index": i, "type": t, "name": n, "input": text,
         "output": f"{n} produced output", "tokens": 100, "latency_s": 1.0,
         "quality": None, "note": None}
        for i, (t, n, text) in enumerate(steps)
    ]
    return Trajectory.from_json({
        "schema_version": 1,
        "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "e"},
        "outcome": {"success": success, "answer": "$4.82 billion",
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 200, "output_tokens": 200,
                   "cost_usd": cost, "latency_s": float(len(steps))},
        "steps": step_dicts,
    })


def reference(task="t01"):
    return make_traj("golden", task, True, REFERENCE_STEPS)


class TestVerdicts(unittest.TestCase):
    def test_identical_run_is_conformant(self):
        check = check_run(reference(), make_traj("run", "t01", True, REFERENCE_STEPS))
        self.assertEqual(check["verdict"], "conformant")
        self.assertEqual(check["conformance"], 1.0)
        self.assertTrue(check["outcome_matches_reference"])
        self.assertEqual(check["deviations"], [])

    def test_reworded_step_is_drift_not_regression(self):
        # Same shape, same outcome, different phrasing: benign.
        steps = copy.deepcopy(REFERENCE_STEPS)
        steps[1] = ("search", "web_search", "acme corporation revenue fiscal 2025")
        check = check_run(reference(), make_traj("run", "t01", True, steps))
        self.assertIn(check["verdict"], ("drift", "conformant"))
        self.assertTrue(check["outcome_matches_reference"])

    def test_extra_step_is_deviation_when_outcome_holds(self):
        steps = list(REFERENCE_STEPS)
        steps.insert(2, ("search", "web_search", "second corroborating search"))
        check = check_run(reference(), make_traj("run", "t01", True, steps))
        self.assertEqual(check["verdict"], "deviation")
        self.assertTrue(check["outcome_matches_reference"])
        self.assertTrue(check["deviations"])

    def test_changed_outcome_is_always_a_violation(self):
        check = check_run(reference(), make_traj("run", "t01", False, REFERENCE_STEPS))
        self.assertEqual(check["verdict"], "violation")
        self.assertFalse(check["outcome_matches_reference"])

    def test_violation_wins_even_when_path_is_identical(self):
        # Identical steps but a different result must not read as conformant.
        check = check_run(reference(), make_traj("run", "t01", False, REFERENCE_STEPS))
        self.assertEqual(check["verdict"], "violation")
        self.assertEqual(check["conformance"], 1.0)

    def test_max_extra_steps_tolerance(self):
        steps = list(REFERENCE_STEPS)
        steps.insert(2, ("search", "web_search", "second corroborating search"))
        strict = check_run(reference(), make_traj("run", "t01", True, steps))
        lenient = check_run(reference(), make_traj("run", "t01", True, steps),
                            max_extra_steps=2)
        self.assertEqual(strict["verdict"], "deviation")
        self.assertNotEqual(lenient["verdict"], "deviation")

    def test_task_mismatch_raises(self):
        with self.assertRaisesRegex(ValueError, "task mismatch"):
            check_run(reference("t01"), make_traj("run", "t99", True, REFERENCE_STEPS))

    def test_deviation_rows_describe_both_sides(self):
        steps = list(REFERENCE_STEPS)
        steps.pop(2)  # skip the read the reference performed
        check = check_run(reference(), make_traj("run", "t01", True, steps))
        self.assertTrue(check["deviations"])
        row = check["deviations"][0]
        self.assertIn("reference_did", row)
        self.assertIn("run_did", row)


class TestSuite(unittest.TestCase):
    def suite(self, run_specs):
        goldens = {task: reference(task) for task, _, _ in run_specs}
        runs = {
            task: make_traj("run", task, success, steps)
            for task, success, steps in run_specs
        }
        return check_suite(goldens, runs)

    def test_all_conformant(self):
        suite = self.suite([
            ("t01", True, REFERENCE_STEPS),
            ("t02", True, REFERENCE_STEPS),
        ])
        self.assertEqual(suite["counts"]["conformant"], 2)
        self.assertEqual(suite["violations"], [])
        self.assertEqual(suite["mean_conformance"], 1.0)

    def test_violations_listed_and_sorted_first(self):
        suite = self.suite([
            ("t01", True, REFERENCE_STEPS),
            ("t02", False, REFERENCE_STEPS),
        ])
        self.assertEqual(suite["violations"], ["t02"])
        self.assertEqual(suite["checks"][0]["verdict"], "violation")

    def test_unmatched_tasks_are_reported_not_dropped(self):
        goldens = {"t01": reference("t01"), "t02": reference("t02")}
        runs = {"t01": make_traj("run", "t01", True, REFERENCE_STEPS),
                "t09": make_traj("run", "t09", True, REFERENCE_STEPS)}
        suite = check_suite(goldens, runs)
        self.assertEqual(suite["missing_reference"], ["t09"])
        self.assertEqual(suite["unused_reference"], ["t02"])
        self.assertEqual(len(suite["checks"]), 1)

    def test_empty_intersection_is_handled(self):
        suite = check_suite({"t01": reference("t01")},
                            {"t09": make_traj("run", "t09", True, REFERENCE_STEPS)})
        self.assertEqual(suite["checks"], [])
        self.assertIn("No task", suite["narrative"])

    def test_markdown_reports_verdict_and_tasks(self):
        suite = self.suite([
            ("t01", True, REFERENCE_STEPS),
            ("t02", False, REFERENCE_STEPS),
        ])
        markdown = render_conformance_markdown(suite)
        self.assertIn("❌ FAIL", markdown)
        self.assertIn("t02", markdown)
        passing = render_conformance_markdown(self.suite([("t01", True, REFERENCE_STEPS)]))
        self.assertIn("✅ PASS", passing)

    def test_deterministic(self):
        specs = [("t01", True, REFERENCE_STEPS), ("t02", False, REFERENCE_STEPS)]
        first, second = self.suite(specs), self.suite(specs)
        self.assertEqual(
            [(c["task"], c["verdict"]) for c in first["checks"]],
            [(c["task"], c["verdict"]) for c in second["checks"]],
        )


class TestAgainstRealRegression(unittest.TestCase):
    """The shipped rondo-v1 -> rondo-v2 regression must be caught."""

    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "fleet" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo fleet traces not present")
        cls.goldens, cls.runs = {}, {}
        for path in sorted(traces.glob("*__rondo-v1.json")):
            traj = Trajectory.from_json(path)
            cls.goldens[traj.task.id] = traj
        for path in sorted(traces.glob("*__rondo-v2.json")):
            traj = Trajectory.from_json(path)
            cls.runs[traj.task.id] = traj

    def test_regression_produces_violations(self):
        suite = check_suite(self.goldens, self.runs)
        self.assertTrue(suite["violations"])
        for task in suite["violations"]:
            self.assertTrue(self.goldens[task].outcome.success)
            self.assertFalse(self.runs[task].outcome.success)

    def test_reference_against_itself_is_fully_conformant(self):
        suite = check_suite(self.goldens, self.goldens)
        self.assertEqual(suite["violations"], [])
        self.assertEqual(suite["mean_conformance"], 1.0)
        self.assertEqual(suite["counts"]["conformant"], len(self.goldens))


if __name__ == "__main__":
    unittest.main()
