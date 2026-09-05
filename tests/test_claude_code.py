"""Tracing Claude Code: live through hooks, and from its transcript.

The hook writes what it saw and nothing more; the transcript converter
keeps the assistant's text, the tool calls with their results, and the
usage counts; a run without an expected answer is written ungraded and
says so; the final trace validates as SCHEMA and lands beside any other
agent's.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.claude_code import (
    LIVE_SUFFIX, UNGRADED, detect_claude_code, hook_event, transcript_to_trajectory,
)
from deepcompare.registry import detect_format, dry_run

ROOT = Path(__file__).resolve().parent.parent


def transcript(expected_answer="23 hours 45 minutes"):
    """A small session shaped like a Claude Code transcript."""
    return [
        {"type": "user", "message": {"role": "user", "content": "How long is the SIN-LHR-JFK journey?"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-x",
                                          "content": [{"type": "text", "text": "I'll convert every time to UTC first."},
                                                      {"type": "tool_use", "id": "tu1", "name": "Bash",
                                                       "input": {"command": "date -u -d '2025-06-10T09:00+08:00'"}}],
                                          "usage": {"input_tokens": 400, "output_tokens": 60}}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                                  "content": "Tue Jun 10 01:00:00 UTC 2025"}]}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-x",
                                          "content": [{"type": "tool_use", "id": "tu2", "name": "Bash",
                                                       "input": {"command": "python3 -c 'print(13*60+40+135+470)'"}}],
                                          "usage": {"input_tokens": 500, "output_tokens": 30}}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu2",
                                                                  "content": "1425", "is_error": False}]}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-x",
                                          "content": [{"type": "text", "text": f"Total elapsed: {expected_answer}."}],
                                          "usage": {"input_tokens": 600, "output_tokens": 20}}},
    ]


class TranscriptTest(unittest.TestCase):
    def test_reason_tool_and_answer_steps_with_usage_tokens(self):
        traj = transcript_to_trajectory(transcript(), task="t05_flight_duration", agent="claude-code",
                                        expected="23 hours 45 minutes")
        Trajectory.from_dict(traj)
        types = [(s["type"], s["name"]) for s in traj["steps"]]
        self.assertEqual(types, [("reason", "reason"), ("tool_call", "Bash"), ("tool_call", "Bash"), ("answer", "final")])
        self.assertEqual(traj["steps"][1]["output"], "Tue Jun 10 01:00:00 UTC 2025")
        self.assertEqual(traj["steps"][2]["output"], "1425")
        self.assertTrue(traj["outcome"]["success"])
        self.assertEqual(traj["agent"]["model"], "claude-x")
        self.assertEqual(traj["task"]["prompt"], "How long is the SIN-LHR-JFK journey?")
        self.assertEqual(traj["steps"][2]["tokens"], 30, "a lone tool call carries its turn's output tokens")
        self.assertEqual(traj["source"]["format"], "claude-code-transcript")

    def test_without_an_expected_answer_the_run_is_ungraded_not_guessed(self):
        traj = transcript_to_trajectory(transcript(), task="t", agent="claude-code")
        self.assertFalse(traj["outcome"]["success"])
        self.assertIsNone(traj["outcome"]["score"])
        self.assertEqual(traj["outcome"]["note"], UNGRADED)

    def test_a_wrong_answer_fails_the_grade(self):
        traj = transcript_to_trajectory(transcript("11 hours 45 minutes"), task="t", agent="cc", expected="23 hours 45 minutes")
        self.assertFalse(traj["outcome"]["success"])

    def test_the_registry_detects_the_transcript_shape(self):
        from deepcompare.claude_code import register_format
        register_format()
        det = detect_format(transcript())
        self.assertEqual(det["best"], "claude-code")
        report = dry_run(transcript())
        self.assertEqual(report["format"], "claude-code")
        self.assertTrue(any("ungraded" in w for w in report.get("warnings", [])))
        self.assertEqual(detect_claude_code({"foo": 1})[0], 0.0)

    def test_a_claude_code_trace_compares_with_any_other_agent(self):
        cc = Trajectory.from_dict(transcript_to_trajectory(transcript(), task="t05_flight_duration", agent="claude-code",
                                                           expected="23 hours 45 minutes"))
        other = Trajectory.from_dict(json.loads((ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json").read_text()))
        report = compare(cc, other)
        self.assertEqual(report["diagnosis"]["subject"], "b")
        self.assertTrue(report["verdict_card"]["lines"])


class HookTest(unittest.TestCase):
    def test_post_tool_use_streams_and_stop_finalises_from_the_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp) / "traces"
            tpath = Path(tmp) / "session.jsonl"
            tpath.write_text("\n".join(json.dumps(e) for e in transcript()), encoding="utf-8")
            common = dict(traces=traces, task="t05_flight_duration", agent="claude-code", expected="23 hours 45 minutes")
            r = hook_event({"hook_event_name": "UserPromptSubmit", "prompt": "How long is the journey?",
                            "session_id": "s1", "transcript_path": str(tpath)}, now=100.0, **common)
            self.assertEqual(r["action"], "prompt recorded")
            r = hook_event({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "date -u"},
                            "tool_response": "Tue Jun 10 01:00:00 UTC 2025", "transcript_path": str(tpath)}, now=101.5, **common)
            self.assertIn("step 0", r["action"])
            live = traces / ("t05_flight_duration__claude-code" + LIVE_SUFFIX)
            self.assertTrue(live.is_file())
            partial = json.loads(live.read_text(encoding="utf-8"))
            self.assertTrue(partial["in_progress"])
            self.assertEqual(len(partial["steps"]), 1)
            self.assertEqual(partial["steps"][0]["name"], "Bash")
            self.assertEqual(partial["steps"][0]["latency_s"], 1.5, "latency is the time since the previous event")
            self.assertEqual(partial["task"]["prompt"], "How long is the journey?")
            hook_event({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "python3 -c 1"},
                        "tool_response": "1425"}, now=103.0, **common)
            self.assertEqual(len(json.loads(live.read_text(encoding="utf-8"))["steps"]), 2)
            r = hook_event({"hook_event_name": "Stop", "transcript_path": str(tpath)}, now=104.0, **common)
            self.assertEqual(r["action"], "final trace written")
            self.assertFalse(live.exists())
            final = json.loads((traces / "t05_flight_duration__claude-code.json").read_text(encoding="utf-8"))
            Trajectory.from_dict(final)
            self.assertTrue(final["outcome"]["success"])
            self.assertEqual(final["source"]["format"], "claude-code-transcript")
            self.assertEqual([s["type"] for s in final["steps"]], ["reason", "tool_call", "tool_call", "answer"])

    def test_stop_without_a_transcript_builds_the_trace_from_the_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp)
            common = dict(traces=traces, task="t1", agent="cc", prompt="do the thing")
            hook_event({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "x.py"},
                        "tool_response": "print(1)"}, now=1.0, **common)
            r = hook_event({"hook_event_name": "Stop"}, now=2.0, **common)
            final = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
            Trajectory.from_dict(final)
            self.assertEqual(final["source"]["format"], "claude-code-hooks")
            self.assertEqual(final["outcome"]["note"], UNGRADED)
            self.assertEqual(final["steps"][0]["name"], "Read")

    def test_unknown_events_are_ignored_and_the_cli_reads_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = hook_event({"hook_event_name": "Notification"}, traces=tmp, task="t", agent="cc")
            self.assertEqual(r["action"], "ignored")
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "hook", "--traces", tmp, "--task", "t9", "--agent", "cc"],
                                  input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Grep",
                                                    "tool_input": {"pattern": "x"}, "tool_response": "a.py:1"}),
                                  capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertIn("step 0", out["action"])
            self.assertTrue((Path(tmp) / ("t9__cc" + LIVE_SUFFIX)).is_file())


if __name__ == "__main__":
    unittest.main()
