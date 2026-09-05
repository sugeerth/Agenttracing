"""A second model judges the output: recorded beside the grade, applied
only on request, never confused with an exact match."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.harness.judge import DEFAULT_RUBRIC, judge_many, judge_trace
from deepcompare.harness.providers import ScriptedProvider
from deepcompare.tracedb import TraceDB

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "traces"


def trace(name):
    return json.loads((DEMO / name).read_text(encoding="utf-8"))


def scripted(text, model="judge-model"):
    return ScriptedProvider([{"text": text, "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 5}}], model=model)


class JudgeTest(unittest.TestCase):
    def test_the_verdict_is_recorded_beside_the_grade_and_not_applied(self):
        t = trace("t05_flight_duration__bolt-v3.json")
        block = judge_trace(t, scripted('{"success": false, "score": 0.1, "rationale": "the durations ignore time zones"}'))
        self.assertFalse(block["success"])
        self.assertEqual(block["score"], 0.1)
        self.assertIn("time zones", block["rationale"])
        self.assertEqual(block["model"], "judge-model")
        self.assertEqual(block["prior"]["graded_by"], "exact-match")
        self.assertTrue(block["agrees_with_prior"])
        self.assertFalse(block["applied"])
        self.assertFalse(t["outcome"]["success"])
        self.assertNotIn("graded_by", t["outcome"])
        Trajectory.from_dict(t)

    def test_apply_replaces_the_grade_and_says_a_model_did(self):
        t = trace("t05_flight_duration__bolt-v3.json")
        block = judge_trace(t, scripted('{"success": true, "score": 0.9, "rationale": "close enough"}'), apply=True)
        self.assertTrue(block["applied"])
        self.assertFalse(block["agrees_with_prior"])
        self.assertTrue(t["outcome"]["success"])
        self.assertEqual(t["outcome"]["score"], 0.9)
        self.assertEqual(t["outcome"]["graded_by"], "model")

    def test_a_self_judgement_is_flagged(self):
        t = trace("t05_flight_duration__atlas-v2.json")
        block = judge_trace(t, scripted('{"success": true, "score": 1}', model=t["agent"]["model"]))
        self.assertTrue(block["self_judged"])

    def test_a_non_json_reply_is_no_verdict(self):
        t = trace("t05_flight_duration__atlas-v2.json")
        block = judge_trace(t, scripted("I think it is fine."))
        self.assertIsNone(block["success"])
        self.assertIn("JSON", block["error"])
        self.assertIsNone(t["outcome"].get("graded_by"))

    def test_the_rubric_and_steps_reach_the_judge(self):
        seen = {}
        def script(messages, tools):
            seen["messages"] = messages
            return {"text": '{"success": true, "score": 1, "rationale": "ok"}', "tool_calls": [], "usage": {}}
        t = trace("t05_flight_duration__atlas-v2.json")
        judge_trace(t, ScriptedProvider(script, model="j"), rubric="Be lenient. Reply JSON.", with_steps=True)
        self.assertEqual(seen["messages"][0]["content"], "Be lenient. Reply JSON.")
        self.assertIn("STEPS THE AGENT TOOK", seen["messages"][1]["content"])
        self.assertIn("REFERENCE ANSWER", seen["messages"][1]["content"])

    def test_judge_many_counts_agreement(self):
        traces = [trace("t05_flight_duration__atlas-v2.json"), trace("t05_flight_duration__bolt-v3.json")]
        counts = judge_many(traces, lambda: scripted('{"success": true, "score": 1, "rationale": "yes"}'))
        self.assertEqual(counts["judged"], 2)
        self.assertEqual(counts["agreed_with_prior"], 1)
        self.assertEqual(counts["disagreed_with_prior"], 1)

    def test_the_cli_judges_a_directory_with_a_scripted_provider_and_updates_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "traces"; d.mkdir()
            for name in ("t05_flight_duration__atlas-v2.json", "t05_flight_duration__bolt-v3.json"):
                (d / name).write_text((DEMO / name).read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "judge.json"
            script.write_text(json.dumps([{"text": '{"success": false, "score": 0.2, "rationale": "scripted"}', "tool_calls": []}]), encoding="utf-8")
            db = Path(tmp) / "t.sqlite"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "judge", str(d), "--provider", f"j=scripted:{script}", "--db", str(db)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("2 judged", proc.stdout)
            judged = json.loads((d / "t05_flight_duration__atlas-v2.json").read_text(encoding="utf-8"))
            self.assertFalse(judged["outcome"]["judge"]["success"])
            self.assertTrue(judged["outcome"]["success"], "not applied: the exact-match grade stands")
            with TraceDB(db) as store:
                self.assertEqual(store.count(source="judge"), 2)
                self.assertIsNotNone(store.get("t05_flight_duration__bolt-v3")["outcome"]["judge"])


if __name__ == "__main__":
    unittest.main()
