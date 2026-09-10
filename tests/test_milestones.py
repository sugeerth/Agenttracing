"""Milestones: progress through a long task measured before the answer —
first evidence wins, deadlines are read not guessed, two runs compare per
rung, and the demo's long pair reads the way it was generated."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from deepcompare.milestones import compare, evaluate, lost_and_gained
from deepcompare.report import attach_milestones
from deepcompare.scorecard import load_golden

ROOT = Path(__file__).resolve().parents[1]

MS = [
    {"id": "found", "label": "the value is found", "evidence": ["42"], "in": "output", "by_step": 2},
    {"id": "written", "label": "the value is written", "evidence": ["write answer.txt"], "in": "input", "by_seconds": 5.0},
    {"id": "checked", "label": "the value is checked", "evidence": ["ok: 42"], "in": "any"},
]


def run(steps, name="r"):
    return {"agent": {"name": name}, "task": {"id": "t"}, "steps": [dict(s, index=i) for i, s in enumerate(steps)]}


class EvaluateTest(unittest.TestCase):
    def test_first_evidence_marks_the_milestone_with_step_second_and_agent(self):
        r = run([
            {"type": "search", "name": "web_search", "input": "value?", "output": "no results", "latency_s": 1.0},
            {"type": "search", "name": "web_search", "input": "value again", "output": "the value is 42", "latency_s": 2.0, "span": {"id": "s1", "agent": "researcher"}},
            {"type": "tool_call", "name": "write_file", "input": "write answer.txt", "output": "ok", "latency_s": 1.5},
            {"type": "tool_call", "name": "write_file", "input": "write answer.txt", "output": "ok", "latency_s": 1.0},
            {"type": "answer", "name": "final", "input": "42", "output": "42", "latency_s": 0.5},
        ])
        out = evaluate(r, MS)
        self.assertTrue(out["measurable"])
        self.assertEqual((out["reached"], out["total"], out["progress"]), (2, 3, round(2 / 3, 4)))
        found, written, checked = out["milestones"]
        self.assertEqual((found["step"], found["seconds"], found["agent"], found["on_time"], found["evidence_hit"]), (1, 3.0, "researcher", True, "42"))
        self.assertEqual((written["step"], written["seconds"], written["on_time"]), (2, 4.5, True))
        self.assertFalse(checked["reached"])
        self.assertIsNone(checked["on_time"])
        self.assertTrue(out["in_order"])
        self.assertEqual(out["last_reached_step"], 2)
        self.assertEqual(out["steps_after_last"], 2)
        self.assertIn("reached 2 of 3", out["narrative"])
        self.assertIn("never reached: the value is checked", out["narrative"])

    def test_deadlines_are_read_against_the_step_and_the_clock(self):
        r = run([
            {"type": "reason", "name": "reason", "input": "think", "output": "…", "latency_s": 4.0},
            {"type": "reason", "name": "reason", "input": "think", "output": "…", "latency_s": 4.0},
            {"type": "reason", "name": "reason", "input": "think", "output": "…", "latency_s": 4.0},
            {"type": "search", "name": "web_search", "input": "q", "output": "value 42", "latency_s": 1.0},
            {"type": "tool_call", "name": "write_file", "input": "write answer.txt", "output": "ok", "latency_s": 1.0},
        ])
        out = evaluate(r, MS)
        self.assertEqual(out["milestones"][0]["on_time"], False)   # step 3 > by_step 2
        self.assertEqual(out["milestones"][1]["on_time"], False)   # 14s > by_seconds 5
        self.assertIn("late:", out["narrative"])

    def test_out_of_order_is_reported_not_scored(self):
        r = run([
            {"type": "tool_call", "name": "write_file", "input": "write answer.txt", "output": "ok", "latency_s": 1.0},
            {"type": "search", "name": "web_search", "input": "q", "output": "value 42", "latency_s": 1.0},
        ])
        out = evaluate(r, MS)
        self.assertEqual(out["reached"], 2)
        self.assertFalse(out["in_order"])
        self.assertIn("out of the listed order", out["narrative"])

    def test_no_milestones_measures_nothing_and_says_so(self):
        out = evaluate(run([{"type": "answer", "name": "final", "input": "x", "output": "x"}]), None)
        self.assertFalse(out["measurable"])
        self.assertIsNone(out["progress"])
        self.assertIn("no milestones", out["narrative"])
        self.assertFalse(compare(out, out)["measurable"])

    def test_evidence_matching_is_case_insensitive_and_scoped_to_the_field(self):
        r = run([{"type": "search", "name": "web_search", "input": "OK: 42", "output": "nothing", "latency_s": 1.0}])
        out = evaluate(r, MS)
        self.assertFalse(out["milestones"][0]["reached"], "42 sits in the input, the milestone reads the output")
        self.assertTrue(out["milestones"][2]["reached"], "'any' reads both, case-insensitively")


class CompareTest(unittest.TestCase):
    def test_two_runs_compare_per_rung_with_who_first_and_the_gap(self):
        a = evaluate(run([
            {"type": "search", "name": "s", "input": "q", "output": "value 42", "latency_s": 2.0},
            {"type": "tool_call", "name": "write_file", "input": "write answer.txt", "output": "ok", "latency_s": 1.0},
            {"type": "tool_call", "name": "check", "input": "check", "output": "ok: 42", "latency_s": 1.0},
        ], "fast"), MS)
        b = evaluate(run([
            {"type": "reason", "name": "reason", "input": "hmm", "output": "…", "latency_s": 5.0},
            {"type": "search", "name": "s", "input": "q", "output": "value 42", "latency_s": 2.0},
            {"type": "tool_call", "name": "write_file", "input": "write answer.txt", "output": "ok", "latency_s": 1.0},
        ], "slow"), MS)
        d = compare(a, b, ("fast", "slow"))
        self.assertTrue(d["measurable"])
        self.assertEqual(d["further"], "a")
        rows = {r["id"]: r for r in d["rows"]}
        self.assertEqual((rows["found"]["first"], rows["found"]["gap_steps"], rows["found"]["gap_seconds"]), ("a", -1, -5.0))
        self.assertEqual(rows["checked"]["first"], "a")
        self.assertIsNone(rows["checked"]["gap_steps"])
        self.assertIn("fast got further: 3 of 3 milestones against 2", d["narrative"])
        self.assertIn("the first milestone only one run reached is the value is checked (fast, step 2)", d["narrative"])
        self.assertIn("slow arrived 5.0s later", d["narrative"])
        lg = lost_and_gained(a, b)
        self.assertEqual((lg["lost"], lg["gained"], lg["same"]), (["checked"], [], False))


class DemoTest(unittest.TestCase):
    def test_the_long_demo_pair_reads_as_generated_and_the_report_carries_it(self):
        golden = load_golden(ROOT / "demo" / "horizon" / "golden.json")
        ms = golden["tasks"]["h02_migrate_service"]["milestones"]
        good = json.loads((ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").read_text(encoding="utf-8"))
        bad = json.loads((ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__comet-lh.json").read_text(encoding="utf-8"))
        self.assertGreater(len(good["steps"]), 400)
        a, b = evaluate(good, ms), evaluate(bad, ms)
        self.assertEqual((a["reached"], a["total"]), (9, 9))
        self.assertEqual(b["reached"], 7)
        self.assertEqual([m["id"] for m in b["milestones"] if not m["reached"]], ["pkg_ledger", "verified"])
        ledger = next(m for m in a["milestones"] if m["id"] == "pkg_ledger")
        self.assertEqual(ledger["agent"], "migrator-ledger")
        report = {"task": {"id": "h02_migrate_service"}, "a": good, "b": bad}
        attach_milestones(report, golden)
        self.assertEqual(report["milestones"]["diff"]["further"], "a")
        self.assertEqual(report["milestones"]["source"], golden["path"])
        self.assertIn("atlas-lh got further", report["milestones"]["narrative"])
        # without a golden task the section says so and measures nothing
        other = {"task": {"id": "nope"}, "a": good, "b": bad}
        attach_milestones(other, golden)
        self.assertFalse(other["milestones"]["a"]["measurable"])
        self.assertIsNone(other["milestones"]["source"])


if __name__ == "__main__":
    unittest.main()
