"""The diagnoser against one-rule baselines (bench.structural_baselines): the last step before the answer, the
first error, the first step without a twin in the passing run, the first write. An attribution method is worth
its margin over the best of them, not its score."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.bench import STRUCTURAL, format_scorecard, run_benchmark, structural_baselines  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402


def _traj(steps):
    return Trajectory.from_dict({
        "schema_version": 1, "trace_id": "x", "agent": {"name": "a"}, "task": {"id": "t", "prompt": "p"},
        "outcome": {"success": False, "answer": "a"}, "totals": {},
        "steps": [dict({"index": i, "tokens": 1, "latency_s": 0.1}, **s) for i, s in enumerate(steps)]})


class RulesTest(unittest.TestCase):
    def test_each_rule_names_the_step_its_rule_finds(self):
        failing = _traj([
            {"type": "tool_call", "name": "read", "input": "a.py", "output": "x", "effect": "read"},
            {"type": "tool_call", "name": "search", "input": "q2", "output": "Error: no such file"},
            {"type": "tool_call", "name": "edit", "input": "a.py", "output": "ok", "effect": "write"},
            {"type": "answer", "name": "final", "input": "", "output": "done"}])
        passing = _traj([
            {"type": "tool_call", "name": "read", "input": "a.py", "output": "x", "effect": "read"},
            {"type": "answer", "name": "final", "input": "", "output": "done"}])
        self.assertEqual(structural_baselines(failing, passing),
                         {"last_step": 2, "first_error": 1, "first_divergence": 1, "first_write": 2})

    def test_a_rule_that_finds_nothing_says_none(self):
        run = _traj([{"type": "tool_call", "name": "read", "input": "a", "output": "x"},
                     {"type": "answer", "name": "final", "input": "", "output": "done"}])
        got = structural_baselines(run, run)
        self.assertIsNone(got["first_error"])
        self.assertIsNone(got["first_divergence"], "every step has its twin")
        self.assertIsNone(got["first_write"])


class ShippedCorpusTest(unittest.TestCase):
    def test_the_engines_margin_over_the_best_rule_is_on_the_scorecard(self):
        result = run_benchmark(ROOT / "demo" / "diagnosis_bench" / "traces")
        s = result["structural_baselines"]
        self.assertEqual(set(s["rules"]), set(STRUCTURAL))
        self.assertEqual({r: v["exact"] for r, v in s["rules"].items()},
                         {"last_step": 0, "first_error": 3, "first_divergence": 11, "first_write": 3})
        self.assertEqual(s["best"], "first_divergence")
        self.assertAlmostEqual(s["engine_margin"], 16 / 16 - 11 / 16, places=4)
        self.assertIn("one-rule steps  last step 0/16, first error 3/16, first divergence 11/16, first write 3/16"
                      " — engine margin over the best (first divergence) +0.3125", format_scorecard(result))


if __name__ == "__main__":
    unittest.main()
