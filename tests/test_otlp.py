"""OpenTelemetry both ways: a trajectory exported as OTLP/JSON spans after the GenAI conventions
(agentdiff.otlp_export), and a standard OTLP/JSON document read back (adapters.from_otlp)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.adapters import from_otlp, otlp_spans  # noqa: E402
from agentdiff.otlp_export import to_otlp  # noqa: E402
from agentdiff.registry import convert  # noqa: E402

TRACE = {
    "schema_version": 1, "trace_id": "t1__atlas", "started_at": 1790000000.0,
    "agent": {"name": "atlas", "model": "m-1", "version": "2"},
    "task": {"id": "t1", "prompt": "Fix the parser.", "expected": None},
    "outcome": {"success": False, "answer": "Done.", "score": None},
    "totals": {"input_tokens": 120, "output_tokens": 30, "cost_usd": 0.0, "latency_s": 3.5},
    "steps": [
        {"index": 0, "type": "reason", "name": "llm", "input": "", "output": "read it first\t", "tokens": 10,
         "latency_s": 1.0},
        {"index": 1, "type": "tool_call", "name": "Bash", "input": "pytest -q", "output": "SECRET-OUTPUT 1 failed",
         "tokens": 20, "latency_s": 2.0, "error": True},
        {"index": 2, "type": "answer", "name": "final", "input": "", "output": "Done.", "tokens": 5, "latency_s": 0.5},
    ]}


def _attrs(span):
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


class ExportTest(unittest.TestCase):
    def test_the_run_is_an_agent_span_and_each_step_a_span_beneath_it(self):
        doc = to_otlp(TRACE)
        spans = otlp_spans(doc)
        root, steps = spans[0], spans[1:]
        self.assertEqual(root["name"], "invoke_agent atlas")
        self.assertEqual(_attrs(root)["gen_ai.operation.name"], "invoke_agent")
        self.assertEqual([s["name"] for s in steps], ["chat llm", "execute_tool Bash", "chat final"])
        self.assertTrue(all(s["parentSpanId"] == root["spanId"] and s["traceId"] == root["traceId"] for s in steps))
        self.assertEqual(_attrs(steps[1])["gen_ai.tool.name"], "Bash")
        self.assertEqual(steps[1]["status"]["code"], 2, "the failed step's span is an error")
        self.assertEqual(root["status"]["code"], 2, "and so is the failed run's")
        self.assertEqual([int(s["startTimeUnixNano"]) for s in steps],
                         [1790000000 * 10 ** 9, 1790000001 * 10 ** 9, 1790000003 * 10 ** 9], "on the recorded clock")
        self.assertEqual(to_otlp(TRACE), doc, "the same trace exports to the same spans")

    def test_without_content_no_step_text_leaves(self):
        text = json.dumps(to_otlp(TRACE))
        for secret in ("SECRET-OUTPUT", "pytest -q", "Fix the parser.", "read it first"):
            self.assertNotIn(secret, text)
        self.assertIn("agentdiff.output.chars", text, "its sizes do")
        self.assertIn("SECRET-OUTPUT", json.dumps(to_otlp(TRACE, content=True)), "with content, the text is there")


class RoundTripTest(unittest.TestCase):
    def test_an_exported_trace_comes_back_step_for_step(self):
        back, _ = from_otlp(json.loads(json.dumps(to_otlp(TRACE, content=True))))
        keep = ("type", "name", "input", "output", "tokens", "latency_s")
        self.assertEqual([{k: s[k] for k in keep} for s in back["steps"]],
                         [{k: s[k] for k in keep} for s in TRACE["steps"]], "whitespace and empty inputs included")
        self.assertEqual([bool(s.get("error")) for s in back["steps"]], [False, True, False])
        self.assertEqual(back["trace_id"], "t1__atlas")
        self.assertEqual((back["task"]["id"], back["task"]["prompt"]), ("t1", "Fix the parser."))
        self.assertFalse(back["outcome"]["success"])
        self.assertEqual((back["totals"]["input_tokens"], back["totals"]["output_tokens"]), (120, 30))
        self.assertEqual(back["agent"]["model"], "m-1")

    def test_without_content_the_steps_come_back_with_no_text(self):
        back, _ = from_otlp(to_otlp(TRACE))
        self.assertEqual([(s["type"], s["name"], s["tokens"]) for s in back["steps"]],
                         [(s["type"], s["name"], s["tokens"]) for s in TRACE["steps"]])
        self.assertEqual({s["input"] for s in back["steps"]} | {s["output"] for s in back["steps"]}, {""},
                         "no text is made up from span names")

    def test_another_tools_otlp_document_is_read_too(self):
        def span(sid, parent, name, start, end, **attrs):
            return {"traceId": "a" * 32, "spanId": sid, "parentSpanId": parent, "name": name,
                    "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
                    "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()]}
        doc = {"resourceSpans": [{"scopeSpans": [{"spans": [
            span("r", "", "invoke_agent helper", 0, 9, **{"gen_ai.operation.name": "invoke_agent",
                                                         "gen_ai.agent.name": "helper"}),
            span("c", "r", "chat m", 1, 3, **{"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}),
            span("t", "r", "execute_tool grep", 3, 6, **{"gen_ai.operation.name": "execute_tool",
                                                        "gen_ai.tool.name": "grep",
                                                        "gen_ai.tool.call.result": "a.py:3"})]}]}]}
        result = convert(doc, None)
        self.assertEqual(result["format"], "otel")
        traj = result["trajectory"]
        self.assertEqual(traj["agent"]["name"], "helper")
        self.assertEqual([s["name"] for s in traj["steps"]][-2:], ["m", "grep"])


class SameStartTest(unittest.TestCase):
    def test_steps_with_no_recorded_latency_come_back_in_order(self):
        """Every span starts at the same nanosecond: the exported step index, not the span name, orders them."""
        traj = {"schema_version": 1, "trace_id": "z", "agent": {"name": "x"}, "task": {"id": "t", "prompt": "p"},
                "outcome": {"success": True, "answer": "a"}, "totals": {},
                "steps": [{"index": 0, "type": "tool_call", "name": "search_web", "input": "q", "output": "r",
                           "tokens": 1, "latency_s": 0},
                          {"index": 1, "type": "reason", "name": "reason", "input": "think", "output": "",
                           "tokens": 1, "latency_s": 0},
                          {"index": 2, "type": "answer", "name": "final", "input": "a", "output": "a",
                           "tokens": 1, "latency_s": 0}]}
        back, _ = from_otlp(to_otlp(traj, content=True))
        self.assertEqual([s["type"] for s in back["steps"]], ["tool_call", "reason", "answer"])


class CommandTest(unittest.TestCase):
    def test_convert_writes_otlp_and_reads_it_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "t.json").write_text(json.dumps(TRACE))
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            run = lambda *a: subprocess.run([sys.executable, "-m", "agentdiff", "convert", *a], capture_output=True,
                                            text=True, env=env, timeout=120)
            out = run("--to", "otel", str(tmp / "t.json"), "-o", str(tmp / "otel"))
            self.assertEqual(out.returncode, 0, out.stderr)
            exported = tmp / "otel" / "t1__atlas.otlp.json"
            self.assertNotIn("SECRET-OUTPUT", exported.read_text())
            back = run(str(exported), "-o", str(tmp / "back"))
            self.assertEqual(back.returncode, 0, back.stderr)
            self.assertIn("Detected format: otel", back.stdout)
            self.assertEqual(len(json.loads((tmp / "back" / "t1__atlas.json").read_text())["steps"]), 3)


if __name__ == "__main__":
    unittest.main()
