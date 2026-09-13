"""The evaluation scorecard: every dimension a count or an interval,
golden and policy make tool correctness and compliance measurable, the
judge stays beside the grade, and the CLI writes it out."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.scorecard import (  # noqa: E402
    RATE_DIMENSIONS, load_golden, load_policy, render_scorecard_markdown, score_run, scorecard,
)
from deepcompare.statistics import wilson_interval  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

DEMO = ROOT / "demo" / "runs" / "traces"
GOLDEN = ROOT / "demo" / "golden" / "tasks.json"


def _trace(task, agent, steps, success, expected="$120.00", answer="The refund is $120.00.", tools=None):
    out_steps = []
    for i, (kind, name, inp, outp) in enumerate(steps):
        out_steps.append({"index": i, "type": kind, "name": name, "input": inp, "output": outp,
                          "tokens": 10, "latency_s": 0.5, "effect": "write" if name and name.startswith("write") else None})
    out_steps.append({"index": len(out_steps), "type": "answer", "name": "final", "input": answer, "output": answer, "tokens": 10, "latency_s": 0.2})
    return {"schema_version": 1, "trace_id": f"{task}__{agent}", "agent": {"name": agent, "model": "m", "version": ""},
            "task": {"id": task, "prompt": "What is the answer?", "expected": expected},
            "outcome": {"success": success, "answer": answer, "termination": "agent_stop"},
            "totals": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01, "latency_s": 2.0},
            "steps": out_steps, "tools": tools or [{"name": "lookup", "effect": "read"}, {"name": "write_file", "effect": "write"}, {"name": "shell", "effect": "write"}]}


class ScoreRunTest(unittest.TestCase):
    def test_tool_correctness_needs_a_golden_task_and_then_counts(self):
        good = Trajectory.from_dict(_trace("t1", "a", [("tool_call", "lookup", "lookup(q='answer')", '{"refund": "$120.00"}')], True))
        r = score_run(good)
        self.assertIsNone(r["tools"]["tool_correct"], "no golden: not measurable, not False")
        r = score_run(good, {"id": "t1", "expected_tools": ["lookup"]})
        self.assertTrue(r["tools"]["tool_correct"])
        wrong = Trajectory.from_dict(_trace("t1", "a", [("tool_call", "shell", "shell(cmd='cat x')", '{"refund": "$120.00"}')], True))
        r = score_run(wrong, {"id": "t1", "expected_tools": ["lookup"]})
        self.assertFalse(r["tools"]["tool_correct"])
        self.assertEqual(r["tools"]["wrong_tool_calls"], 1)

    def test_policy_flags_forbidden_tools_and_patterns_and_blind_writes(self):
        t = Trajectory.from_dict(_trace("t1", "a", [
            ("tool_call", "write_file", "write_file(path='a', content='x')", "ok"),
            ("tool_call", "shell", "shell(cmd='rm -rf /tmp/x')", "ok")], True))
        r = score_run(t, policy={"forbidden_tools": ["shell"], "forbidden_patterns": ["rm -rf"], "write_requires_read": True})
        kinds = sorted(f["kind"] for f in r["safety"]["risk_flags"])
        self.assertIn("forbidden_tool", kinds)
        self.assertIn("forbidden_pattern", kinds)
        self.assertIn("blind_write", kinds)
        self.assertFalse(r["safety"]["policy_compliant"])
        self.assertFalse(r["safety"]["risk_free"])
        clean = Trajectory.from_dict(_trace("t1", "a", [("tool_call", "lookup", "lookup(q='answer')", '{"refund": "$120.00"}')], True))
        r = score_run(clean, policy={"forbidden_tools": ["shell"]})
        self.assertTrue(r["safety"]["policy_compliant"])
        self.assertIsNone(score_run(clean)["safety"]["policy_compliant"], "no policy: not measurable")

    def test_loops_and_stopping_when_done_are_read_from_the_trajectory(self):
        looping = Trajectory.from_dict(_trace("t1", "a", [("tool_call", "lookup", "lookup(q='x')", "")] * 5, False, answer="I do not know."))
        r = score_run(looping)
        self.assertFalse(r["trajectory"]["loop_free"])
        self.assertTrue(any(f["kind"] == "looping" for f in r["safety"]["risk_flags"]))
        self.assertGreaterEqual(r["trajectory"]["repeated_calls"], 1)
        done = Trajectory.from_dict(_trace("t1", "a", [("tool_call", "lookup", "lookup(q='answer')", '{"refund": "$120.00"}')], True))
        self.assertEqual(score_run(done)["trajectory"]["stopped_when_done"], True)
        dawdle = Trajectory.from_dict(_trace("t1", "a", [("tool_call", "lookup", "lookup(q='answer')", '{"refund": "$120.00"}'),
                                                          ("tool_call", "lookup", "lookup(q='other')", '{"refund": "$17.00"}')], True))
        self.assertEqual(score_run(dawdle)["trajectory"]["stopped_when_done"], False)
        self.assertEqual(score_run(dawdle)["trajectory"]["steps_after_done"], 1)

    def test_the_judge_is_read_from_the_raw_trace_and_compared_with_the_exact_match(self):
        raw = _trace("t1", "a", [("tool_call", "lookup", "lookup(q='answer')", '{"refund": "$120.00"}')], True)
        raw["outcome"]["graded_by"] = "model"
        raw["outcome"]["judge"] = {"model": "j", "success": True, "score": 0.8, "applied": True,
                                   "prior": {"success": False, "graded_by": "exact-match"}, "agrees_with_prior": False}
        r = score_run(Trajectory.from_dict(raw), raw=raw)
        self.assertEqual(r["graded_by"], "model")
        self.assertEqual((r["judge"]["success"], r["judge"]["grade"], r["judge"]["agrees_with_grade"]), (True, False, False))


class ScorecardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trajs = [Trajectory.from_json(p) for p in sorted(DEMO.glob("*.json"))]
        cls.card = scorecard(cls.trajs, load_golden(GOLDEN))

    def test_every_rate_is_a_count_with_a_wilson_interval(self):
        for agent, a in self.card["agents"].items():
            n = sum(1 for t in self.trajs if t.agent.name == agent)
            self.assertEqual(a["runs"], n)
            for key, _label in RATE_DIMENSIONS:
                r = a["rates"][key]
                if not r["runs"]:
                    self.assertIsNone(r["rate"])
                    continue
                self.assertEqual(r["rate"], round(r["successes"] / r["runs"], 4))
                lo, hi = wilson_interval(r["successes"], r["runs"])
                self.assertEqual(r["ci95"], [round(lo, 4), round(hi, 4)])
            succ = sum(1 for t in self.trajs if t.agent.name == agent and t.outcome.success)
            self.assertEqual(a["rates"]["success"]["successes"], succ)

    def test_golden_makes_tool_correctness_and_policy_measurable(self):
        self.assertEqual(self.card["mode"], "offline — golden set")
        self.assertEqual(self.card["golden"]["covered"], 8)
        bolt = self.card["agents"]["bolt-v3"]
        self.assertEqual(bolt["rates"]["tool_correct"]["runs"], 24)
        self.assertLess(bolt["rates"]["tool_correct"]["successes"], 24, "the calculator runs of t05 are the wrong tool")
        self.assertEqual(bolt["safety"]["flag_kinds"].get("forbidden_tool"), bolt["tools"]["wrong_tool_calls"])
        plain = scorecard(self.trajs)
        self.assertEqual(plain["mode"], "online — traces as recorded")
        self.assertEqual(plain["agents"]["bolt-v3"]["rates"]["tool_correct"]["runs"], 0)
        self.assertEqual(plain["agents"]["bolt-v3"]["rates"]["policy_compliant"]["runs"], 0)

    def test_risk_reward_is_reward_over_risk_and_none_without_a_flag(self):
        for a in self.card["agents"].values():
            rr = a["risk_reward"]
            self.assertEqual(rr["reward"], a["rates"]["success"]["rate"])
            self.assertEqual(rr["risk"], round(a["safety"]["flagged_runs"] / a["runs"], 4))
            if rr["risk"]:
                self.assertAlmostEqual(rr["ratio"], rr["reward"] / rr["risk"], places=2)
            else:
                self.assertIsNone(rr["ratio"])

    def test_markdown_carries_every_dimension(self):
        md = render_scorecard_markdown(self.card)
        for _key, label in RATE_DIMENSIONS:
            self.assertIn(label, md)
        self.assertIn("risk vs reward", md)
        self.assertIn("LLM judge", md)
        self.assertIn("Wilson", md)


class EvalCliTest(unittest.TestCase):
    def test_eval_writes_the_scorecard_and_a_scripted_judge_sits_beside_the_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "traces"; d.mkdir()
            for name in ("t05_flight_duration__atlas-v2__r1.json", "t05_flight_duration__bolt-v3__r1.json"):
                (d / name).write_text((DEMO / name).read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "judge.json"
            script.write_text(json.dumps([{"text": '{"success": true, "score": 0.9, "rationale": "scripted"}'}]), encoding="utf-8")
            out = Path(tmp) / "eval"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "eval", str(d), "--golden", str(GOLDEN),
                                   "--judge", f"j=scripted:{script}", "-o", str(out)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            card = json.loads((out / "eval.json").read_text(encoding="utf-8"))
            self.assertEqual(card["mode"], "offline — golden set")
            bolt = card["agents"]["bolt-v3"]
            self.assertEqual(bolt["rates"]["success"]["successes"], 0, "the grade stands")
            self.assertEqual(bolt["judge"]["success"]["successes"], 1, "the judge said solved")
            self.assertEqual(bolt["judge"]["confusion"]["grade_fail_judge_pass"], 1)
            self.assertEqual(card["judge_run"]["judged"], 2)
            self.assertTrue((out / "EVAL.md").is_file())
            self.assertIn("LLM judge", proc.stdout)
            raw = json.loads((d / "t05_flight_duration__bolt-v3__r1.json").read_text(encoding="utf-8"))
            self.assertNotIn("judge", raw["outcome"], "without --write the trace files are untouched")

    def test_eval_online_without_golden_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "eval", str(DEMO), "-o", str(out)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("online — traces as recorded", proc.stdout)
            self.assertIn("not measurable", proc.stdout)


if __name__ == "__main__":
    unittest.main()
