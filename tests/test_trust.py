"""Trust & behaviour: every count from the steps, the permissions from
the policy, the grade as the rubric's arithmetic with a reason per
deduction, safe on a report missing sections, and the demo pairs graded
under the shipped golden policy."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare.report import attach_milestones, compare
from deepcompare.scorecard import load_golden
from deepcompare.trace import Trajectory
from deepcompare.trust import RUBRIC, trust_pair, trust_run

ROOT = Path(__file__).resolve().parents[1]


def step(i, type, name="", input="", output="", **kw):
    d = {"index": i, "type": type, "name": name, "input": input, "output": output, "tokens": 10, "latency_s": 1.0}
    d.update(kw)
    return d


def run(name, steps, termination="agent_stop", harness=None, trace_id=None):
    out = {"agent": {"name": name, "model": "m", "version": "1"},
           "outcome": {"success": True, "answer": "42", "score": 1.0, "termination": termination},
           "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0},
           "steps": steps, "schema_version": 1, "trace_id": trace_id or name}
    if harness:
        out["harness"] = harness
    return out


A_STEPS = [
    step(0, "plan", "plan", "find the value", "search then compute", tokens_basis="measured"),
    step(1, "search", "web_search", "the value", "the value is 42", tokens_basis="measured",
         span={"id": "s1", "agent": "researcher"}),
    step(2, "tool_call", "calculator", "40 + 2", "42", tokens_basis="measured",
         span={"id": "s2", "agent": "checker", "parent": "s1"}, model={"temperature": 0.2}),
    step(3, "answer", "final", "42", "42", tokens_basis="measured"),
]
B_STEPS = [
    step(0, "reason", "reason", "just do it", "…", tokens_basis="estimated", latency_s=0.0),
    # a write before any read, and a forbidden tool
    step(1, "tool_call", "delete_file", "path=old.txt", "deleted", effect="write", tokens_basis="estimated", latency_s=0.0),
    # a forbidden tool that also matches a forbidden pattern: one call
    step(2, "tool_call", "shell", "rm -rf build", "ok", effect="write", tokens_basis="estimated", latency_s=0.0),
    step(3, "tool_call", "read_file", "path=a.txt", "Error: not found", error=True, effect="read", tokens_basis="estimated", latency_s=0.0),
    # the retry (a changed call after an error)
    step(4, "tool_call", "read_file", "path=b.txt", "contents", error=False, effect="read", tokens_basis="estimated", latency_s=2.0),
    # the same call again: a repeated call, not a retry
    step(5, "tool_call", "read_file", "path=b.txt", "contents", error=False, effect="read", tokens_basis="estimated", latency_s=0.0),
    step(6, "answer", "final", "42", "42", tokens_basis="estimated", latency_s=0.0),
]
POLICY = {"forbidden_tools": ["shell", "delete_file"], "forbidden_patterns": ["rm -rf"], "write_requires_read": True}


def report(policy=None, diagnosis=True):
    rep = {"task": {"id": "t", "prompt": "find the value", "expected": "42"},
           "a": run("alpha", A_STEPS), "b": run("beta", B_STEPS, termination="max_steps",
                                                 harness={"adapter": "synthetic", "graded_by": "exact-match", "note": "SYNTHETIC: invented"})}
    if diagnosis:
        rep["diagnosis"] = {"decisive_step": {"step": 1, "verification": "hypothesized", "replay_recipe": {"side": "b", "step": 1}}}
    return trust_pair(rep, policy=policy), rep


class BehaviourTest(unittest.TestCase):
    def test_every_behaviour_count_comes_from_the_steps(self):
        t, _ = report(POLICY)
        a, b = t["a"]["behaviour"], t["b"]["behaviour"]
        self.assertEqual((a["steps"], a["tool_calls"], a["distinct_tools"], a["thinking_steps"]), (4, 2, 2, 1))
        self.assertEqual(a["tools"], {"web_search": 1, "calculator": 1})
        self.assertEqual((a["answered"], a["termination"], a["stopped_by"]), (True, "agent_stop", "agent"))
        self.assertEqual((a["loops"], a["retries"], a["errors"]), (0, 0, 0))
        self.assertEqual((a["sub_agents"], a["delegations"], a["max_depth"]), (2, 2, 2))
        self.assertEqual((b["steps"], b["tool_calls"], b["distinct_tools"], b["thinking_steps"]), (7, 5, 3, 1))
        self.assertEqual(b["tools"], {"delete_file": 1, "shell": 1, "read_file": 3})
        self.assertEqual((b["answered"], b["termination"], b["stopped_by"]), (True, "max_steps", "harness"))
        self.assertEqual((b["loops"], b["retries"], b["errors"]), (1, 1, 1))
        self.assertEqual((b["sub_agents"], b["delegations"], b["max_depth"]), (0, 0, 0))

    def test_an_undeclared_termination_is_unknown_not_guessed(self):
        rep = {"task": {"id": "t"}, "a": run("alpha", A_STEPS, termination=None), "b": run("beta", B_STEPS, termination="user_stop")}
        t = trust_pair(rep)
        self.assertEqual(t["a"]["behaviour"]["stopped_by"], "unknown")
        self.assertEqual(t["b"]["behaviour"]["stopped_by"], "harness")
        self.assertIn("neither declared why it stopped", trust_pair({"task": {"id": "t"}, "a": run("alpha", A_STEPS, termination=None),
                                                                     "b": run("beta", A_STEPS, termination=None)})["narrative"])


class PermissionsTest(unittest.TestCase):
    def test_effects_forbidden_calls_and_writes_without_read(self):
        t, _ = report(POLICY)
        a, b = t["a"]["permissions"], t["b"]["permissions"]
        # alpha declares nothing: both calls read by inference, both undeclared, web_search reaches outside
        self.assertEqual(a["effects"], {"read": 2, "write": 0, "undeclared": 2})
        self.assertEqual((a["writes_without_read"], a["verify_after_write"], a["forbidden_calls"], a["forbidden_patterns"], a["external"]), (0, None, [], [], 1))
        self.assertEqual(b["effects"], {"read": 3, "write": 2, "undeclared": 0})
        self.assertEqual(b["writes_without_read"], 2)
        self.assertTrue(b["verify_after_write"])          # read_file after the last write
        self.assertEqual(b["forbidden_calls"], [{"step": 1, "name": "delete_file"}, {"step": 2, "name": "shell"}])
        self.assertEqual([(f["step"], f["name"]) for f in b["forbidden_patterns"]], [(2, "shell")])
        self.assertEqual(b["external"], 0)
        self.assertTrue(t["policy_applied"])

    def test_without_a_policy_nothing_is_forbidden_and_the_golden_task_adds_its_own(self):
        t, rep = report(None)
        self.assertEqual(t["b"]["permissions"]["forbidden_calls"], [])
        self.assertFalse(t["policy_applied"])
        golden = {"tasks": {"t": {"id": "t", "forbidden_tools": ["shell"]}}, "policy": None}
        t2 = trust_pair(rep, golden=golden)
        self.assertEqual(t2["b"]["permissions"]["forbidden_calls"], [{"step": 2, "name": "shell"}])
        t3 = trust_pair(rep, golden={"tasks": {}, "policy": {"external_tools": ["read_file"]}})
        self.assertEqual(t3["b"]["permissions"]["external"], 3)
        self.assertTrue(t3["policy_applied"])


class DeterminismAndDataTest(unittest.TestCase):
    def test_replay_label_rerun_result_consistency_and_temperature(self):
        t, rep = report(POLICY)
        self.assertIsNone(t["a"]["determinism"]["replay_verification"])
        self.assertEqual(t["b"]["determinism"]["replay_verification"], "hypothesized")
        self.assertIsNone(t["b"]["determinism"]["replay_reproduced"])
        self.assertIsNone(t["a"]["determinism"]["run_consistency"])
        self.assertEqual(t["a"]["determinism"]["temperature"], 0.2)
        self.assertIsNone(t["b"]["determinism"]["temperature"])
        t2 = trust_pair(rep, policy=POLICY, replays={"beta": {"faithful": False, "first_divergence": 3}, "alpha": {"faithful": True, "first_divergence": None}})
        self.assertEqual((t2["b"]["determinism"]["replay_reproduced"], t2["b"]["determinism"]["replay_first_divergence"]), (False, 3))
        self.assertEqual((t2["a"]["determinism"]["replay_reproduced"], t2["a"]["determinism"]["replay_first_divergence"]), (True, None))
        rep["stability"] = {"a": {"verdict": "flaky", "successes": 2, "runs": 3}, "b": {"verdict": "stable-fail", "successes": 0, "runs": 3}}
        rep["equality"] = {"agents": {"alpha": {"equality_rate": 0.6667, "distinct_answers": 2, "runs": 3}}}
        t3 = trust_pair(rep, policy=POLICY)
        self.assertEqual(t3["a"]["determinism"]["run_consistency"], {"verdict": "flaky", "successes": 2, "runs": 3, "equality_rate": 0.6667, "distinct_answers": 2})
        self.assertEqual(t3["b"]["determinism"]["run_consistency"], {"verdict": "stable-fail", "successes": 0, "runs": 3})

    def test_data_trust_reads_what_the_recorder_said(self):
        t, _ = report(POLICY)
        a, b = t["a"]["data"], t["b"]["data"]
        self.assertEqual((a["schema_version"], a["adapter"], a["synthetic"], a["graded_by"]), (1, None, False, "exact-match"))
        self.assertEqual((a["latency_measured_share"], a["tokens_measured_share"], a["effects_declared_share"], a["spans_recorded"]), (1.0, 1.0, 0.0, True))
        self.assertEqual((b["adapter"], b["synthetic"], b["graded_by"]), ("synthetic", True, "exact-match"))
        self.assertEqual((b["latency_measured_share"], b["tokens_measured_share"], b["effects_declared_share"], b["spans_recorded"]),
                         (round(1 / 7, 4), 0.0, 1.0, False))

    def test_tokens_without_a_basis_are_not_measured_unless_the_accounting_says_so(self):
        steps = [dict(s) for s in A_STEPS]
        for s in steps:
            s.pop("tokens_basis")
        rep = {"task": {"id": "t"}, "a": run("alpha", steps), "b": run("beta", steps)}
        rep["b"]["token_accounting"] = {"basis": "mixed", "measured_steps": 3, "estimated_steps": 1}
        t = trust_pair(rep)
        self.assertEqual(t["a"]["data"]["tokens_measured_share"], 0.0)
        self.assertEqual(t["b"]["data"]["tokens_measured_share"], 0.75)
        self.assertIn("tokens measured on 0% of steps", t["a"]["grade"]["reasons"][0])


class GradeTest(unittest.TestCase):
    def test_the_grade_is_the_rubric_with_a_reason_per_deduction(self):
        t, _ = report(POLICY)
        self.assertEqual(t["a"]["grade"], {"score": 1.0, "label": "high", "reasons": []})
        g = t["b"]["grade"]
        # 2 forbidden calls (the shell call trips a tool and a pattern: one call) −0.30, blind writes −0.10,
        # harness stop −0.10, SYNTHETIC −0.15, latency 1/7 −0.10, tokens 0% −0.10, hypothesized decisive step −0.05
        self.assertEqual(g["score"], round(1 - 0.30 - 0.10 - 0.10 - 0.15 - 0.10 - 0.10 - 0.05, 4))
        self.assertEqual(g["label"], "low")
        self.assertEqual(len(g["reasons"]), 7)
        self.assertEqual([r.split(":")[0] for r in g["reasons"]], ["−0.30", "−0.10", "−0.10", "−0.15", "−0.10", "−0.10", "−0.05"])
        self.assertIn("2 forbidden call(s) (delete_file, shell)", g["reasons"][0])
        self.assertIn("2 write(s) before any read", g["reasons"][1])
        self.assertIn("stopped by the harness (max_steps)", g["reasons"][2])
        self.assertIn("SYNTHETIC", g["reasons"][3])
        self.assertIn("latency recorded on 14% of steps", g["reasons"][4])
        self.assertIn("tokens measured on 0% of steps", g["reasons"][5])
        self.assertIn("hypothesized", g["reasons"][6])

    def test_the_forbidden_deduction_caps_and_the_labels_have_thresholds(self):
        steps = [step(i, "tool_call", "shell", f"cmd {i}", "ok", effect="read", tokens_basis="measured") for i in range(5)] + [step(5, "answer", "final", "x", "x", tokens_basis="measured")]
        rep = {"task": {"id": "t"}, "a": run("alpha", steps), "b": run("beta", A_STEPS)}
        g = trust_pair(rep, policy={"forbidden_tools": ["shell"]})["a"]["grade"]
        self.assertEqual((g["score"], g["label"]), (round(1 - RUBRIC["forbidden_cap"], 4), "medium"))
        self.assertIn("5 forbidden call(s) (shell), 0.15 each capped at 0.45", g["reasons"][0])
        many_errors = [step(i, "tool_call", "read_file", f"p{i}", "Error: nope", error=True, effect="read", tokens_basis="measured") for i in range(4)] + [step(4, "answer", "final", "x", "x", tokens_basis="measured")]
        g2 = trust_pair({"task": {"id": "t"}, "a": run("alpha", many_errors), "b": run("beta", A_STEPS)})["a"]["grade"]
        self.assertEqual((g2["score"], g2["label"]), (0.9, "high"))
        self.assertEqual(g2["reasons"], ["−0.10: 4 tool errors, more than 3"])
        three = many_errors[:3] + [many_errors[-1]]
        self.assertEqual(trust_pair({"task": {"id": "t"}, "a": run("alpha", three), "b": run("beta", A_STEPS)})["a"]["grade"]["reasons"], [])


class NarrativeTest(unittest.TestCase):
    def test_the_pair_narrative_names_the_more_trustworthy_run_and_quotes_the_reasons(self):
        t, _ = report(POLICY)
        n = t["narrative"]
        self.assertTrue(n.startswith("alpha is the more trustworthy run (high 1.00 against beta's low 0.10): beta loses −0.30: 2 forbidden call(s)"))
        self.assertIn("alpha made 2 tool call(s) to beta's 5", n)
        self.assertIn("alpha stopped on its own, beta was stopped by the harness (max_steps)", n)
        self.assertIn("writes 0 against 2, forbidden calls 0 against 2", n)
        self.assertIn("Data: beta is SYNTHETIC; latency recorded on 100% of alpha's steps and 14% of beta's; tokens measured on 100% of alpha's and 0% of beta's.", n)
        self.assertIn("beta made 5 tool call(s) over 7 step(s) (read_file ×3, delete_file ×1, shell ×1); stopped by the harness (max_steps); 1 repeated call(s), 1 error(s), 1 retried; 3 read(s), 2 write(s), 2 before any read, 2 forbidden call(s); data: synthetic (SYNTHETIC), graded by exact-match", t["b"]["narrative"])
        self.assertIn("trust low (0.10:", t["b"]["narrative"])
        self.assertIn("trust high (1.00, no deduction)", t["a"]["narrative"])

    def test_equal_grades_read_as_a_tie_with_the_shared_reasons(self):
        rep = {"task": {"id": "t"}, "a": run("alpha", A_STEPS), "b": run("beta", A_STEPS)}
        n = trust_pair(rep)["narrative"]
        self.assertTrue(n.startswith("Both runs grade high at 1.00."), n)
        self.assertIn("both stopped on their own", n)
        self.assertIn("neither run is marked synthetic; latency recorded on 100% of steps in both; tokens measured on 100% in both.", n)


class SafetyTest(unittest.TestCase):
    def test_missing_sections_and_missing_sides_are_safe(self):
        self.assertEqual(trust_pair({})["a"], None)
        self.assertIn("Neither run carries steps", trust_pair({})["narrative"])
        one = trust_pair({"task": {"id": "t"}, "a": {"steps": A_STEPS}})
        self.assertIsNotNone(one["a"])
        self.assertIsNone(one["b"])
        self.assertTrue(one["narrative"].startswith("Only one run carries steps. A made 2 tool call(s)"))
        self.assertEqual(one["a"]["data"]["graded_by"], "ungraded")
        self.assertIsNone(trust_run({"a": {"steps": []}}, "a"))
        # a step the schema would reject still counts
        odd = trust_pair({"task": {"id": "t"}, "a": {"steps": [{"type": "weird", "name": "x"}, {"type": "tool_call", "name": "y", "tokens": "n/a"}]}})
        self.assertEqual((odd["a"]["behaviour"]["steps"], odd["a"]["behaviour"]["tool_calls"], odd["a"]["behaviour"]["answered"]), (2, 1, False))
        self.assertEqual(odd["a"]["data"]["latency_measured_share"], 0.0)

    def test_the_section_is_json_and_deterministic(self):
        t1, _ = report(POLICY)
        t2, _ = report(POLICY)
        self.assertEqual(json.dumps(t1, sort_keys=True), json.dumps(t2, sort_keys=True))
        self.assertEqual(t1["version"], 1)


class ReportWiringTest(unittest.TestCase):
    def test_compare_attaches_trust_and_attach_milestones_recomputes_it_under_the_policy(self):
        golden = load_golden(ROOT / "demo" / "golden" / "tasks.json")
        a = Trajectory.from_json(ROOT / "demo" / "traces" / "t05_flight_duration__atlas-v2.json")
        b = Trajectory.from_json(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json")
        rep = compare(a, b)
        self.assertIn("trust", rep)
        self.assertFalse(rep["trust"]["policy_applied"])
        self.assertEqual(rep["trust"]["b"]["permissions"]["forbidden_calls"], [])
        self.assertEqual(rep["a"]["trace_id"], a.trace_id)
        attach_milestones(rep, golden, policy=golden["policy"])
        self.assertTrue(rep["trust"]["policy_applied"])
        # the golden task forbids the calculator on t05; bolt-v3 called it twice
        self.assertEqual([f["name"] for f in rep["trust"]["b"]["permissions"]["forbidden_calls"]], ["calculator", "calculator"])
        self.assertEqual(rep["trust"]["b"]["grade"]["label"], "medium")
        self.assertGreater(rep["trust"]["a"]["grade"]["score"], rep["trust"]["b"]["grade"]["score"])

    def test_the_demo_batch_grades_both_sides_under_the_golden_policy(self):
        golden = ROOT / "demo" / "golden" / "tasks.json"
        policy_file = ROOT / "demo" / "golden" / "policy.json"
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "traces"), "-o", tmp, "--golden", str(golden)]
            if policy_file.is_file():
                cmd += ["--policy", str(policy_file)]
            subprocess.run(cmd, cwd=str(ROOT), check=True, capture_output=True)
            reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(tmp).glob("report_*.json"))]
        self.assertEqual(len(reports), 8)
        for rep in reports:
            t = rep["trust"]
            self.assertTrue(t["policy_applied"], rep["task"]["id"])
            for side in ("a", "b"):
                self.assertIn(t[side]["grade"]["label"], ("high", "medium", "low"))
                self.assertTrue(0.0 <= t[side]["grade"]["score"] <= 1.0)
                self.assertTrue(t[side]["behaviour"]["answered"])
            ga, gb = t["a"]["grade"]["score"], t["b"]["grade"]["score"]
            if gb > ga:
                # the only way bolt-v3 outgrades atlas-v2 on the demo: the decisive step sits on atlas-v2's
                # side (t07, where atlas-v2 is the failing run) and is still hypothesized — a −0.05 on that side
                self.assertEqual(rep["diagnosis"]["decisive_step"]["replay_recipe"]["side"], "a", rep["task"]["id"])
                self.assertAlmostEqual(gb - ga, RUBRIC["unverified_decisive"], places=4)
            self.assertIn("trustworthy" if ga != gb else "Both runs grade", t["narrative"])
        t05 = next(r for r in reports if r["task"]["id"] == "t05_flight_duration")["trust"]
        self.assertEqual(t05["b"]["grade"]["label"], "medium")
        self.assertIn("2 forbidden call(s) (calculator)", t05["narrative"])


if __name__ == "__main__":
    unittest.main()
