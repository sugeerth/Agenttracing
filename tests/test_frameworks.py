"""Framework / protocol detection and domain specs (docs/FRAMEWORKS.md).

Detection reads conventions a trace carries — MCP tool names, OpenAI
Agents SDK handoffs, Claude Code's tool set and adapter stamps — and says
nothing when none is present.  Domains complete a golden task from a
spec without overriding what the task states.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare import domains  # noqa: E402
from deepcompare.frameworks import detect, render_line  # noqa: E402
from deepcompare.scorecard import load_golden  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402


def trace(tool_names, agent="agent-x", extra=None, spans=None, types=None):
    steps = []
    for i, name in enumerate(tool_names):
        step = {"index": i, "type": (types or {}).get(name, "tool_call"), "name": name,
                "input": f"{name}()", "output": "ok", "tokens": 1, "latency_s": 0.1}
        if spans and spans[i]:
            step["span"] = spans[i]
        steps.append(step)
    steps.append({"index": len(steps), "type": "answer", "name": "final", "input": "", "output": "done",
                  "tokens": 1, "latency_s": 0.1})
    data = {"schema_version": 1, "trace_id": "t1__" + agent, "agent": {"name": agent, "model": "m", "version": ""},
            "task": {"id": "t1", "prompt": "do it", "expected": None},
            "outcome": {"success": True, "answer": "done", "score": None},
            "totals": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0, "latency_s": 1.0},
            "steps": steps}
    data.update(extra or {})
    Trajectory.from_dict(data)  # every fixture is a valid SCHEMA trace
    return data


class TestFrameworkDetection(unittest.TestCase):

    def test_mcp_tool_names_list_their_servers(self):
        det = detect(trace(["mcp__github__list_issues", "mcp__filesystem__read_file", "mcp__github__get_me"]))
        self.assertEqual(det["mcp_servers"], ["filesystem", "github"])
        self.assertIn("mcp", det["protocols"])
        # servers alone do not name a framework
        self.assertIsNone(det["framework"])

    def test_dotted_names_count_only_when_declared_as_mcp(self):
        bare = detect(trace(["ci.get_log"]))
        self.assertEqual(bare["mcp_servers"], [])
        declared = detect(trace(["ci.get_log"], extra={"tools": [{"name": "ci.get_log", "mcp": True}]}))
        self.assertEqual(declared["mcp_servers"], ["ci"])

    def test_handoff_tool_counts_and_names_openai_agents_medium(self):
        det = detect(trace(["transfer_to_billing", "lookup_order"],
                           spans=[None, {"id": "s1", "agent": "billing", "parent": None}]))
        self.assertEqual(det["handoffs"], 1)
        self.assertEqual(det["framework"], "openai-agents")
        self.assertEqual(det["confidence"], "medium")
        self.assertTrue(any("span agent became 'billing'" in s for s in det["signals"]))

    def test_claude_code_tool_set_plus_adapter_is_high(self):
        det = detect(trace(["Bash", "Read", "Edit", "Grep"], agent="claude-code",
                           extra={"source": {"format": "claude-code-transcript"}}))
        self.assertEqual((det["framework"], det["confidence"]), ("claude-code", "high"))

    def test_claude_code_tool_set_alone_is_medium(self):
        det = detect(trace(["Bash", "Read", "Edit"]))
        self.assertEqual((det["framework"], det["confidence"]), ("claude-code", "medium"))

    def test_otel_adapter_gives_protocol_only(self):
        det = detect(trace(["web_search"], extra={"harness": {"adapter": "otel"}}))
        self.assertEqual(det["protocols"], ["otel-genai"])
        self.assertIsNone(det["framework"])

    def test_plain_trace_is_unknown(self):
        det = detect(trace(["web_search", "open_page", "calculator"]))
        self.assertIsNone(det["framework"])
        self.assertIsNone(det["confidence"])
        self.assertEqual(det["protocols"], [])
        self.assertEqual(det["handoffs"], 0)

    def test_explicit_adapter_stamps_name_other_frameworks(self):
        for adapter, name in (("langgraph", "langgraph"), ("google-adk", "google-adk"), ("crewai", "crewai"),
                              ("pydantic-ai", "pydantic-ai"), ("autogen", "autogen")):
            det = detect(trace(["do_thing"], extra={"harness": {"adapter": adapter}}))
            self.assertEqual((det["framework"], det["confidence"]), (name, "high"), adapter)

    def test_identity_marker_is_only_low(self):
        det = detect(trace(["do_thing"], agent="my-crewai-crew"))
        self.assertEqual((det["framework"], det["confidence"]), ("crewai", "low"))

    def test_permission_decisions_are_counted(self):
        t = trace(["Bash", "Edit", "Read"])
        t["steps"][0]["permission"] = {"decision": "deny"}
        t["steps"][1]["note"] = "the hook asked for permission before the edit"
        t["steps"][2]["output"] = "Error: permission denied by the user"
        self.assertEqual(detect(t)["permissions"], {"asked": 1, "denied": 2})

    def test_a2a_remote_agent_span(self):
        det = detect(trace(["ask_remote"], spans=[{"id": "s9", "agent": "https://agents.example.com/billing", "parent": None}]))
        self.assertIn("a2a", det["protocols"])

    def test_never_raises_and_is_deterministic(self):
        for bad in ("nope", None, 3, {"steps": [1, {"type": "tool_call", "name": None}]}, {"steps": "x"}):
            det = detect(bad)
            self.assertIsNone(det["framework"])
        t = trace(["mcp__a__x", "transfer_to_b", "Bash", "Read"])
        self.assertEqual(json.dumps(detect(t), sort_keys=True), json.dumps(detect(t), sort_keys=True))

    def test_render_line_mentions_everything(self):
        det = detect(trace(["mcp__github__get_me", "transfer_to_billing"]))
        line = render_line("x.json", det, domains.infer(trace(["web_search"])))
        for token in ("x.json:", "framework=openai-agents (medium)", "mcp=github", "handoffs=1", "domain=research"):
            self.assertIn(token, line)


class TestDomains(unittest.TestCase):

    def test_five_specs_with_the_contract_keys(self):
        self.assertEqual(domains.names(), ["coding", "research", "support", "data", "computer_use"])
        for name in domains.names():
            s = domains.spec(name)
            for key in ("name", "description", "expected_tool_families", "must_read_before_write", "verify_after_write",
                        "forbidden_tools", "external_tools", "stop_rules", "milestone_templates"):
                self.assertIn(key, s, f"{name} lacks {key}")
            self.assertEqual(s["name"], name)
            self.assertIn("max_identical_retries", s["stop_rules"])
            self.assertIn("must_answer", s["stop_rules"])
            for m in s["milestone_templates"]:
                self.assertTrue({"id", "label", "evidence_hint"} <= set(m))

    def test_spec_returns_a_copy_and_rejects_unknown(self):
        a, b = domains.spec("coding"), domains.spec("coding")
        a["forbidden_tools"].append("x")
        self.assertNotIn("x", b["forbidden_tools"])
        with self.assertRaises(ValueError):
            domains.spec("gardening")

    def test_infer_coding_from_read_edit_bash(self):
        inf = domains.infer(trace(["Read", "Edit", "Bash"]))
        self.assertEqual((inf["domain"], inf["confidence"]), ("coding", "high"))

    def test_infer_research_from_search_and_page(self):
        inf = domains.infer(trace(["web_search", "open_page"], types={"web_search": "search", "open_page": "read"}))
        self.assertEqual((inf["domain"], inf["confidence"]), ("research", "high"))

    def test_infer_the_other_domains(self):
        self.assertEqual(domains.infer(trace(["get_user_details", "cancel_reservation"]))["domain"], "support")
        self.assertEqual(domains.infer(trace(["screenshot", "left_click", "type"]))["domain"], "computer_use")
        self.assertEqual(domains.infer(trace(["list_tables", "run_sql", "plot"]))["domain"], "data")

    def test_infer_none_when_nothing_matches(self):
        self.assertIsNone(domains.infer(trace(["frobnicate"]))["domain"])
        self.assertIsNone(domains.infer({})["domain"])
        self.assertIsNone(domains.infer("nope")["domain"])

    def test_mcp_names_are_matched_on_their_tool_part(self):
        self.assertEqual(domains.infer(trace(["mcp__fs__read_file", "mcp__fs__write_file", "mcp__sh__bash"]))["domain"], "coding")

    def test_apply_fills_without_overriding(self):
        task = {"id": "x", "forbidden_tools": ["mine"], "policy": {"verify_after_write": False}, "expected_tools": ["run_tests"]}
        out = domains.apply(task, domains.spec("coding"))
        self.assertEqual(out["forbidden_tools"], ["mine"])
        self.assertEqual(out["expected_tools"], ["run_tests"])
        self.assertEqual(out["policy"], {"verify_after_write": False, "write_requires_read": True})
        self.assertIn("expected_tool_families", out)
        self.assertIn("milestone_templates", out)
        self.assertEqual(out["domain"], "coding")
        self.assertNotIn("forbidden_tools", task.get("policy", {}))  # the input is untouched
        self.assertEqual(task, {"id": "x", "forbidden_tools": ["mine"], "policy": {"verify_after_write": False},
                                "expected_tools": ["run_tests"]})

    def test_load_golden_completes_a_task_with_a_domain(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "golden.json"
            path.write_text(json.dumps({"tasks": [
                {"id": "a", "domain": "coding", "expected_tools": ["run_tests"]},
                {"id": "b", "any_of_tools": ["web_search"]}]}), encoding="utf-8")
            golden = load_golden(path)
            self.assertEqual(golden["domains"], {"a": "coding"})
            self.assertEqual(golden["tasks"]["a"]["expected_tools"], ["run_tests"])
            self.assertEqual(golden["tasks"]["a"]["forbidden_tools"], domains.spec("coding")["forbidden_tools"])
            self.assertTrue(golden["tasks"]["a"]["policy"]["write_requires_read"])
            self.assertEqual(golden["tasks"]["b"], {"id": "b", "any_of_tools": ["web_search"]})
            path.write_text(json.dumps({"tasks": [{"id": "a", "domain": "gardening"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_golden(path)

    def test_existing_golden_files_still_load_unchanged(self):
        golden = load_golden(ROOT / "demo" / "golden" / "tasks.json")
        self.assertEqual(golden["domains"], {})
        self.assertEqual(len(golden["tasks"]), 8)
        self.assertEqual(golden["policy"]["forbidden_tools"], ["shell", "delete_file", "send_email"])


class TestFrameworksCommand(unittest.TestCase):

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "deepcompare", "frameworks", *args],
                              cwd=str(ROOT), capture_output=True, text=True)

    def test_one_line_per_trace_on_the_demo(self):
        traces = ROOT / "demo" / "traces"
        result = self.run_cli(str(traces))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), len(list(traces.glob("*.json"))))
        for line in lines:
            self.assertIn("framework=unknown", line)
            self.assertIn("domain=research", line)

    def test_long_horizon_runs_read_as_coding(self):
        result = self.run_cli(str(ROOT / "demo" / "horizon" / "long"), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["domain"]["domain"], "coding")
            self.assertIsNone(row["framework"]["framework"])

    def test_missing_target_exits_2(self):
        self.assertEqual(self.run_cli(str(ROOT / "no-such-dir")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
