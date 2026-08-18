"""Tests for experiment-level comparison (v25).

Pinned: pairing on shared tasks (never blending different exams), intervals
before verdicts, harness failures excluded, and the outcome/behaviour
distinction — including the two cases that make it worth having: behaviour
moves while scores do not, and scores move while behaviour does not.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.experiments import (
    compare_experiments,
    diff,
    load_experiment,
    summarise,
)


def run(task, agent="a", success=True, steps=("search", "read"), tokens=100,
        run_id="r1", termination=None):
    body = [{"index": i, "type": s, "name": s, "input": f"{s}()", "output": "ok",
             "tokens": tokens // (len(steps) + 1), "latency_s": 1.0}
            for i, s in enumerate(steps)]
    body.append({"index": len(body), "type": "answer", "name": "final",
                 "input": "done", "output": "done",
                 "tokens": tokens // (len(steps) + 1), "latency_s": 1.0})
    data = {
        "trace_id": f"{task}-{agent}-{run_id}", "run_id": run_id,
        "agent": {"name": agent, "model": "m"},
        "task": {"id": task, "prompt": "p"},
        "outcome": {"success": success, "answer": "done"},
        "totals": {"input_tokens": tokens, "output_tokens": tokens,
                   "cost_usd": tokens * 1e-5, "latency_s": float(len(body))},
        "steps": body,
    }
    if termination:
        data["outcome"]["termination"] = termination
    return Trajectory.from_json(data)


class TestSummarise(unittest.TestCase):
    def test_harness_failures_are_excluded_and_counted(self):
        runs = [run("t1"), run("t2", termination="infrastructure_error")]
        summary = summarise("e", runs)
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["excluded_harness_failures"], 1)

    def test_success_rate_carries_a_wilson_interval(self):
        summary = summarise("e", [run("t1"), run("t2", success=False)])
        self.assertEqual(summary["success_rate"], 0.5)
        low, high = summary["success_interval"]
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)


class TestPairing(unittest.TestCase):
    def test_no_shared_tasks_refuses_rather_than_blending(self):
        result = diff("A", [run("t1")], "B", [run("t2")])
        self.assertEqual(result["shared_tasks"], 0)
        self.assertIn("different exams", result["reason"])

    def test_unshared_tasks_are_listed_not_averaged(self):
        result = diff("A", [run("t1"), run("t2")], "B", [run("t1"), run("t3")])
        self.assertEqual(result["only_in_a"], ["t2"])
        self.assertEqual(result["only_in_b"], ["t3"])
        self.assertEqual(result["shared_tasks"], 1)

    def test_a_task_passes_only_if_every_kept_run_passed(self):
        # the pass^k reading, not the friendliest run
        left = [run("t1", run_id="r1"), run("t1", run_id="r2")]
        right = [run("t1", run_id="r1"), run("t1", run_id="r2", success=False)]
        result = diff("A", left, "B", right)
        self.assertEqual(result["success_diff"]["observed"], 1.0)


class TestUncertaintyFirst(unittest.TestCase):
    def test_a_large_gap_on_few_tasks_is_reported_as_noise(self):
        left = [run(f"t{i}") for i in range(4)]
        right = [run(f"t{i}", success=(i > 0)) for i in range(4)]
        result = diff("A", left, "B", right)
        self.assertFalse(result["success_diff"]["significant"])
        self.assertIn("noise-level", result["narrative"])

    def test_a_consistent_resource_shift_clears_its_interval(self):
        left = [run(f"t{i}", tokens=100) for i in range(8)]
        right = [run(f"t{i}", tokens=300) for i in range(8)]
        result = diff("A", left, "B", right)
        self.assertTrue(result["metric_diffs"]["tokens"]["significant"])
        self.assertGreater(result["metric_diffs"]["tokens"]["observed"], 0)


class TestBehaviourVsOutcome(unittest.TestCase):
    def test_behaviour_change_is_seen_when_scores_agree(self):
        # Same outcomes, different action sequences: the case scores miss.
        left = [run(f"t{i}", run_id=r, steps=("search", "read"))
                for i in range(3) for r in ("r1", "r2")]
        right = [run(f"t{i}", run_id=r,
                     steps=("plan", "tool_call", "tool_call", "reason"))
                 for i in range(3) for r in ("r1", "r2")]
        result = diff("A", left, "B", right)
        self.assertFalse(result["success_diff"]["significant"])
        self.assertTrue(result["similarity"]["behaviour_changed"])
        self.assertLess(result["similarity"]["cross"],
                        result["similarity"]["within"])

    def test_identical_behaviour_is_not_called_a_change(self):
        left = [run(f"t{i}", run_id=r) for i in range(3) for r in ("r1", "r2")]
        right = [run(f"t{i}", run_id=r) for i in range(3) for r in ("r1", "r2")]
        result = diff("A", left, "B", right)
        self.assertFalse(result["similarity"]["behaviour_changed"])

    def test_without_repeats_there_is_no_baseline_and_it_says_so(self):
        left = [run("t1")]
        right = [run("t1", steps=("plan", "reason"))]
        result = diff("A", left, "B", right)
        self.assertIsNone(result["similarity"]["behaviour_changed"])
        self.assertIn("no", result["similarity"]["note"][:60].lower())

    def test_scores_moving_without_behaviour_points_at_the_grader(self):
        left = [run(f"t{i}", run_id=r, success=True)
                for i in range(8) for r in ("r1", "r2")]
        right = [run(f"t{i}", run_id=r, success=False)
                 for i in range(8) for r in ("r1", "r2")]
        result = diff("A", left, "B", right)
        self.assertTrue(result["success_diff"]["significant"])
        self.assertFalse(result["similarity"]["behaviour_changed"])
        self.assertIn("grader", result["narrative"])


class TestDeterminismAndLoading(unittest.TestCase):
    def test_comparison_is_deterministic(self):
        left = [run(f"t{i}") for i in range(4)]
        right = [run(f"t{i}", tokens=200) for i in range(4)]
        one = compare_experiments([("A", left), ("B", right)])
        two = compare_experiments([("A", left), ("B", right)])
        self.assertEqual(one, two)

    def test_loader_skips_invalid_files_and_keeps_valid_ones(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp:
            good = run("t1").to_dict()
            (Path(tmp) / "good.json").write_text(json.dumps(good))
            (Path(tmp) / "bad.json").write_text("{not json")
            runs = load_experiment(Path(tmp))
            self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()
