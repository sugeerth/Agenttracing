"""The harness: any model in, SCHEMA traces out — and the engine never
touches a network.

No test here reaches the internet.  Wire formats are exercised against a
local ``http.server`` that speaks each vendor's response shape, so the
translation layers are tested for real without a key in sight.
"""

from __future__ import annotations

import ast
import functools
import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare.harness import (  # noqa: E402
    AnthropicProvider, OllamaProvider, OpenAICompatProvider, ProviderError,
    ScriptedProvider, Tool, provider_from_spec, run_suite, run_task,
)
from deepcompare.harness.runner import load_tasks  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402


def refund_tool():
    def get_refund(reference: str):
        return {"reference": reference, "refund": "$120.00"}
    return Tool("get_refund", get_refund, "Look up a booking's refund",
                {"type": "object",
                 "properties": {"reference": {"type": "string"}},
                 "required": ["reference"]}, effect="read")


TASK = {"id": "t_refund", "prompt": "What refund applies to booking BK1?",
        "expected": "$120.00"}


class TestNetworkBoundary(unittest.TestCase):
    """The analysis engine contains no network code and never imports the
    harness; the harness is the only module allowed to."""

    NETWORK = {"urllib", "http", "socket", "ssl", "requests", "httpx"}

    def test_only_the_harness_imports_network_modules(self):
        offenders = []
        for path in (ROOT / "deepcompare").rglob("*.py"):
            if "harness" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root in self.NETWORK:
                        offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])

    def test_the_engine_never_imports_the_harness(self):
        offenders = []
        for path in (ROOT / "deepcompare").rglob("*.py"):
            if "harness" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # cli.py imports it lazily inside the run command only;
                # a module-level import anywhere would load network code
                # into every analysis command
                if isinstance(node, ast.ImportFrom) and node.module \
                        and "harness" in node.module and node.col_offset == 0:
                    offenders.append(path.name)
        self.assertEqual(offenders, [])


class TestScriptedToolLoop(unittest.TestCase):
    def test_reason_tool_answer_becomes_a_graded_trace(self):
        provider = ScriptedProvider([
            {"text": "Looking it up.",
             "tool_calls": [{"name": "get_refund",
                             "arguments": {"reference": "BK1"}}],
             "usage": {"input_tokens": 40, "output_tokens": 12}},
            {"text": "The refund for BK1 is $120.00.",
             "usage": {"input_tokens": 80, "output_tokens": 10}},
        ], model="good")
        trace = run_task(provider, TASK, [refund_tool()], out_dir=None)
        self.assertEqual([s["type"] for s in trace["steps"]],
                         ["reason", "tool_call", "answer"])
        self.assertTrue(trace["outcome"]["success"])
        self.assertEqual(trace["outcome"]["termination"], "agent_stop")
        tool_step = trace["steps"][1]
        self.assertIn("get_refund(reference='BK1')", tool_step["input"])
        self.assertIn("$120.00", tool_step["output"])
        self.assertEqual(tool_step["effect"], "read")
        self.assertFalse(tool_step["error"])
        # the endpoint's own token counts land on the model turns
        self.assertEqual(trace["steps"][0]["tokens"], 52)
        self.assertEqual(trace["agent"]["name"], "scripted-good")

    def test_wrong_answer_is_graded_false_not_guessed(self):
        provider = ScriptedProvider([{"text": "The refund is $90.00."}])
        trace = run_task(provider, TASK, [refund_tool()], out_dir=None)
        self.assertFalse(trace["outcome"]["success"])

    def test_ungraded_tasks_are_refused_before_any_model_call(self):
        calls = []
        provider = ScriptedProvider(lambda m, t: calls.append(1) or {"text": "x"})
        with self.assertRaises(ValueError):
            run_task(provider, {"id": "t", "prompt": "p"}, out_dir=None)
        self.assertEqual(calls, [])

    def test_custom_grader_wins_over_containment(self):
        provider = ScriptedProvider([{"text": "one hundred twenty"}])
        trace = run_task(provider, TASK, out_dir=None,
                         grader=lambda answer, task: "twenty" in answer)
        self.assertTrue(trace["outcome"]["success"])

    def test_budget_exhaustion_is_declared_max_steps(self):
        provider = ScriptedProvider(lambda m, t: {
            "tool_calls": [{"name": "get_refund",
                            "arguments": {"reference": "BK1"}}]})
        trace = run_task(provider, TASK, [refund_tool()], out_dir=None,
                         budget={"max_steps": 3})
        self.assertEqual(trace["outcome"]["termination"], "max_steps")
        self.assertFalse(trace["outcome"]["success"])
        self.assertEqual(
            sum(1 for s in trace["steps"] if s["type"] == "tool_call"), 3)

    def test_undeclared_and_failing_tools_are_recorded_then_capped(self):
        def boom(reference: str):
            raise RuntimeError("service down")
        bad_tool = Tool("get_refund", boom, "", refund_tool().parameters, "read")
        provider = ScriptedProvider(lambda m, t: {
            "tool_calls": [{"name": "get_refund",
                            "arguments": {"reference": "BK1"}},
                           {"name": "no_such_tool", "arguments": {}}]})
        trace = run_task(provider, TASK, [bad_tool], out_dir=None,
                         max_tool_errors=3)
        self.assertEqual(trace["outcome"]["termination"], "too_many_errors")
        errors = [s for s in trace["steps"] if s["type"] == "tool_call"]
        self.assertTrue(all(s["error"] for s in errors))
        self.assertIn("no such tool", errors[1]["output"])

    def test_provider_failure_is_the_harness_fault(self):
        def fail(messages, tools):
            raise ProviderError("endpoint unreachable")
        trace = run_task(ScriptedProvider(fail), TASK, out_dir=None)
        self.assertEqual(trace["outcome"]["termination"], "infrastructure_error")
        self.assertTrue(trace["steps"][0]["error"])

    def test_two_scripted_agents_diff_end_to_end(self):
        good = ScriptedProvider([
            {"tool_calls": [{"name": "get_refund",
                             "arguments": {"reference": "BK1"}}]},
            {"text": "The refund for BK1 is $120.00."}], model="good")
        bad = ScriptedProvider([{"text": "The refund is $90.00."}], model="bad")
        a = run_task(good, TASK, [refund_tool()], out_dir=None)
        b = run_task(bad, TASK, [refund_tool()], out_dir=None)
        report = compare(Trajectory.from_dict(b), Trajectory.from_dict(a))
        self.assertEqual(report["diagnosis"]["mode"], "single_failure")
        self.assertEqual(report["diagnosis"]["subject_name"], "scripted-bad")


# ---------------------------------------------------------------------------
# wire formats against a local fake endpoint

class _FakeVendor(http.server.BaseHTTPRequestHandler):
    """Speaks whichever vendor shape the path names; records requests."""

    requests: list = []

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        _FakeVendor.requests.append({"path": self.path, "payload": payload,
                                     "headers": dict(self.headers)})
        if self.path.endswith("/chat/completions"):
            body = {"model": "fake-gpt", "choices": [{"finish_reason": "tool_calls",
                    "message": {"content": None, "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "get_refund",
                                     "arguments": "{\"reference\": \"BK1\"}"}}]}}],
                    "usage": {"prompt_tokens": 33, "completion_tokens": 9}}
        elif self.path.endswith("/v1/messages"):
            body = {"model": "fake-claude", "stop_reason": "tool_use",
                    "content": [{"type": "text", "text": "Checking."},
                                {"type": "tool_use", "id": "toolu_1",
                                 "name": "get_refund",
                                 "input": {"reference": "BK1"}}],
                    "usage": {"input_tokens": 21, "output_tokens": 7}}
        elif self.path.endswith("/api/chat"):
            body = {"model": "fake-llama", "done_reason": "stop",
                    "message": {"role": "assistant", "content": "",
                                "tool_calls": [{"function": {
                                    "name": "get_refund",
                                    "arguments": {"reference": "BK1"}}}]},
                    "prompt_eval_count": 18, "eval_count": 5,
                    "total_duration": 2_500_000_000}
        elif self.path.endswith("/broken"):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TestWireFormats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.TCPServer.allow_reuse_address = True
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), _FakeVendor)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _FakeVendor.requests.clear()

    MESSAGES = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "refund for BK1?"}]
    TOOLS = [refund_tool().declaration()]

    def test_openai_compatible_round_trip(self):
        os.environ["OPENAI_API_KEY"] = "test-key-never-logged"
        try:
            provider = OpenAICompatProvider("fake-gpt", base_url=self.base)
            turn = provider.complete(self.MESSAGES, self.TOOLS)
        finally:
            del os.environ["OPENAI_API_KEY"]
        self.assertEqual(turn.tool_calls[0].name, "get_refund")
        self.assertEqual(turn.tool_calls[0].arguments, {"reference": "BK1"})
        self.assertEqual(turn.usage, {"input_tokens": 33, "output_tokens": 9})
        sent = _FakeVendor.requests[0]
        self.assertEqual(sent["headers"]["Authorization"], "Bearer test-key-never-logged")
        self.assertEqual(sent["payload"]["tools"][0]["function"]["name"], "get_refund")

    def test_anthropic_round_trip_splits_system_and_tool_results(self):
        provider = AnthropicProvider("fake-claude", base_url=self.base)
        messages = self.MESSAGES + [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "toolu_0", "name": "get_refund",
                             "arguments": {"reference": "BK1"}}]},
            {"role": "tool", "tool_call_id": "toolu_0", "name": "get_refund",
             "content": "{\"refund\": \"$120.00\"}"}]
        turn = provider.complete(messages, self.TOOLS)
        self.assertEqual(turn.text, "Checking.")
        self.assertEqual(turn.tool_calls[0].arguments, {"reference": "BK1"})
        self.assertEqual(turn.usage, {"input_tokens": 21, "output_tokens": 7})
        sent = _FakeVendor.requests[0]["payload"]
        self.assertEqual(sent["system"], "sys")
        self.assertEqual(sent["tools"][0]["input_schema"]["required"], ["reference"])
        self.assertEqual(sent["messages"][-1]["content"][0]["type"], "tool_result")
        self.assertEqual(sent["messages"][-1]["content"][0]["tool_use_id"], "toolu_0")

    def test_ollama_round_trip_keeps_real_counts_and_durations(self):
        provider = OllamaProvider("fake-llama", base_url=self.base)
        turn = provider.complete(self.MESSAGES, self.TOOLS)
        self.assertEqual(turn.tool_calls[0].arguments, {"reference": "BK1"})
        self.assertEqual(turn.usage, {"input_tokens": 18, "output_tokens": 5})
        self.assertAlmostEqual(turn.latency_s, 2.5)

    def test_http_errors_become_provider_errors_without_the_key(self):
        os.environ["OPENAI_API_KEY"] = "secret-key-value"
        try:
            provider = OpenAICompatProvider("x", base_url=self.base + "/broken")
            provider.base_url = self.base  # so the path ends in /broken below
            with self.assertRaises(ProviderError) as ctx:
                from deepcompare.harness.providers import _post_json
                _post_json(self.base + "/broken", {}, provider._headers(), 5)
        finally:
            del os.environ["OPENAI_API_KEY"]
        self.assertEqual(ctx.exception.status, 500)
        self.assertNotIn("secret-key-value", str(ctx.exception))

    def test_unreachable_endpoint_is_a_provider_error(self):
        provider = OllamaProvider("x", base_url="http://127.0.0.1:9", timeout=2)
        with self.assertRaises(ProviderError):
            provider.complete(self.MESSAGES)


class TestSpecsAndSuite(unittest.TestCase):
    def test_specs_resolve_and_typos_fail_loudly(self):
        self.assertIsInstance(provider_from_spec("openai:gpt-x"), OpenAICompatProvider)
        self.assertIsInstance(provider_from_spec("anthropic:c"), AnthropicProvider)
        self.assertIsInstance(provider_from_spec("ollama:llama"), OllamaProvider)
        with self.assertRaises(ValueError):
            provider_from_spec("openia:gpt-x")
        with self.assertRaises(ValueError):
            provider_from_spec("gpt-x")

    def test_suite_writes_the_layout_every_command_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "turns.json"
            script.write_text(json.dumps({"model": "fx", "turns": [
                {"text": "The refund for BK1 is $120.00."}]}))
            tasks = Path(tmp) / "tasks.json"
            tasks.write_text(json.dumps([TASK, dict(TASK, id="t_two")]))
            manifest = run_suite(
                {"alpha": f"scripted:{script}", "beta": f"scripted:{script}"},
                load_tasks(tasks), out_dir=Path(tmp) / "traces", runs=2,
                provider_factory=provider_from_spec)
            names = sorted(t["file"] for t in manifest["traces"])
            self.assertEqual(len(names), 8)
            self.assertIn("t_refund__alpha__r1.json", names)
            self.assertIn("t_two__beta__r2.json", names)
            for name in names:
                Trajectory.from_json(str(Path(tmp) / "traces" / name))
            self.assertTrue((Path(tmp) / "traces" / "RUN_MANIFEST.json").is_file())

    def test_cli_run_then_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            good.write_text(json.dumps([{"text": "The refund for BK1 is $120.00."}]))
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps([{"text": "It is $90.00."}]))
            tasks = Path(tmp) / "tasks.json"
            tasks.write_text(json.dumps([TASK]))
            out = Path(tmp) / "traces"
            result = subprocess.run(
                [sys.executable, "-m", "deepcompare", "run",
                 "--provider", f"good=scripted:{good}",
                 "--provider", f"bad=scripted:{bad}",
                 "--tasks", str(tasks), "-o", str(out)],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1/2 succeeded", result.stdout)
            batch = subprocess.run(
                [sys.executable, "-m", "deepcompare", "batch", str(out),
                 "-o", str(Path(tmp) / "out")],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(batch.returncode, 0, batch.stderr)
            report = json.loads(
                (Path(tmp) / "out" / "report_t_refund.json").read_text())
            self.assertEqual(report["diagnosis"]["mode"], "single_failure")

    def test_cli_rejects_bad_specs_before_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.json"
            tasks.write_text(json.dumps([TASK]))
            result = subprocess.run(
                [sys.executable, "-m", "deepcompare", "run",
                 "--provider", "nope:x", "--tasks", str(tasks),
                 "-o", str(Path(tmp) / "t")],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unknown provider kind", result.stderr)


if __name__ == "__main__":
    unittest.main()
