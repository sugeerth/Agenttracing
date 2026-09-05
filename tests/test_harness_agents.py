"""The harness runs ANY agent, and the loop closes end to end.

Bring-your-own-agent adapters (a Python callable, a shell command), the
replay command that turns a hypothesized decisive step into a verified
or refuted one, and the why command that narrates through a provider
under the covenant that the model never alters a number.  Everything
here runs offline through scripted providers and toy agents.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "agents"
sys.path.insert(0, str(FIXTURES))

from deepcompare.harness import (  # noqa: E402
    CommandAgent, PythonAgent, ScriptedProvider, Tool, agent_from_spec,
    run_suite, run_task,
)
from deepcompare.harness.external import run_external  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

TASK = {"id": "t_refund", "prompt": "What refund applies to booking BK1?",
        "expected": "$120.00"}


def refund_tool(amount="$120.00"):
    def get_refund(reference: str):
        return {"reference": reference, "refund": amount}
    return Tool("get_refund", get_refund, "refund lookup",
                {"type": "object", "properties": {"reference": {"type": "string"}},
                 "required": ["reference"]}, effect="read")


class TestPythonAgents(unittest.TestCase):
    def test_a_message_list_becomes_a_graded_trace_with_declared_termination(self):
        agent = PythonAgent("toy_agents:message_agent", "clerk")
        trace = run_external(agent, TASK, [refund_tool()], out_dir=None)
        self.assertTrue(trace["outcome"]["success"])
        self.assertEqual(trace["outcome"]["termination"], "agent_stop")
        self.assertEqual(trace["agent"]["name"], "clerk")
        self.assertEqual(trace["trace_id"], "t_refund__clerk")
        self.assertEqual([s["type"] for s in trace["steps"]][-1], "answer")
        self.assertEqual(trace["harness"]["graded_by"], "harness")
        Trajectory.from_dict(trace)

    def test_a_trace_dict_is_graded_by_the_harness_not_the_agent(self):
        agent = PythonAgent("toy_agents:trace_agent", "guesser")
        trace = run_external(agent, TASK, [], out_dir=None)
        self.assertFalse(trace["outcome"]["success"])
        self.assertEqual(trace["outcome"]["termination"], "agent_stop")
        self.assertEqual(trace["task"]["expected"], "$120.00")

    def test_a_crash_is_an_infrastructure_error_not_a_failure_of_the_agent(self):
        agent = PythonAgent("toy_agents:crashing_agent")
        trace = run_external(agent, TASK, [], out_dir=None)
        self.assertEqual(trace["outcome"]["termination"], "infrastructure_error")
        self.assertFalse(trace["outcome"]["success"])
        self.assertIn("fell over", trace["steps"][0]["input"])

    def test_no_answer_is_declared_agent_error(self):
        agent = PythonAgent("toy_agents:no_answer_agent")
        trace = run_external(agent, TASK, [], out_dir=None)
        self.assertEqual(trace["outcome"]["termination"], "agent_error")

    def test_ungraded_tasks_are_refused_before_the_agent_runs(self):
        agent = PythonAgent("toy_agents:crashing_agent")
        with self.assertRaises(ValueError):
            run_external(agent, {"id": "t", "prompt": "p"}, [], out_dir=None)

    def test_spec_parsing(self):
        self.assertIsInstance(agent_from_spec("python:toy_agents:message_agent"), PythonAgent)
        self.assertIsInstance(agent_from_spec("cmd:./x --out {out_file}"), CommandAgent)
        with self.assertRaises(ValueError):
            agent_from_spec("magic:whatever")
        with self.assertRaises(ValueError):
            agent_from_spec("cmd:./x")   # no {out_file}


class TestCommandAgent(unittest.TestCase):
    def test_a_shell_script_is_an_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "agent.sh"
            script.write_text(
                "#!/bin/sh\n"
                "# reads the task file, answers from a lookup table, writes messages\n"
                "PROMPT=$(cat \"$1\")\n"
                "cat > \"$2\" <<'EOF'\n"
                "[{\"role\": \"user\", \"content\": \"refund?\"},\n"
                " {\"role\": \"assistant\", \"content\": \"The refund for BK1 is $120.00.\"}]\n"
                "EOF\n", encoding="utf-8")
            os.chmod(script, 0o755)
            agent = CommandAgent(f"sh {script} {{prompt_file}} {{out_file}}", "shelly")
            trace = run_external(agent, TASK, [], out_dir=tmp)
            self.assertTrue(trace["outcome"]["success"])
            self.assertEqual(trace["agent"]["name"], "shelly")
            self.assertTrue((Path(tmp) / "t_refund__shelly.json").is_file())

    def test_a_failing_command_is_an_infrastructure_error(self):
        agent = CommandAgent("sh -c 'exit 3' {out_file}", "broken")
        trace = run_external(agent, TASK, [], out_dir=None)
        self.assertEqual(trace["outcome"]["termination"], "infrastructure_error")


class TestSuiteAndRuns(unittest.TestCase):
    def test_external_agents_and_providers_share_one_suite_and_runs_reads_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            turns = Path(tmp) / "turns.json"
            turns.write_text(json.dumps([{"text": "The refund for BK1 is $120.00."}]),
                             encoding="utf-8")
            from deepcompare.harness import provider_from_spec
            manifest = run_suite(
                {"scripted-good": f"scripted:{turns}"}, [TASK], [refund_tool()],
                out_dir=tmp, runs=2, provider_factory=provider_from_spec,
                agents={"guesser": PythonAgent("toy_agents:trace_agent", "guesser")})
            self.assertEqual(sorted(manifest["agents"]), ["guesser", "scripted-good"])
            self.assertEqual(len(manifest["traces"]), 4)
            names = sorted(p.name for p in Path(tmp).glob("*.json") if p.name != "RUN_MANIFEST.json"
                           and p.name != "turns.json")
            self.assertEqual(names, ["t_refund__guesser__r1.json", "t_refund__guesser__r2.json",
                                     "t_refund__scripted-good__r1.json",
                                     "t_refund__scripted-good__r2.json"])
            # trace id equals the file stem for both kinds
            for name in names:
                data = json.loads((Path(tmp) / name).read_text(encoding="utf-8"))
                self.assertEqual(data["trace_id"], name[:-5])
            out = Path(tmp) / "out"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "runs", tmp, "-o", str(out)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("pass^", proc.stdout)

    def test_the_cli_run_accepts_agents_and_provider_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "tasks.json"
            tasks.write_text(json.dumps([TASK]), encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(FIXTURES))
            proc = subprocess.run(
                [sys.executable, "-m", "deepcompare", "run", "--tasks", str(tasks),
                 "--agent", "clerk=python:toy_agents:message_agent",
                 "--agent", "guesser=python:toy_agents:trace_agent",
                 "-o", str(Path(tmp) / "traces"), "--temperature", "0.2",
                 "--base-url", "http://localhost:1/v1", "--api-key-env", "MY_KEY"],
                cwd=str(ROOT), capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("2 trace(s)", proc.stdout)
            self.assertIn("batch", proc.stdout)


def _echo_turns(path: Path, answer: str) -> str:
    path.write_text(json.dumps([{"text": answer}]), encoding="utf-8")
    return str(path)


class TestReplayCommand(unittest.TestCase):
    """A pair whose failing run read a broken tool: replaying from the
    decisive step with the passing run's observation borrowed flips the
    outcome (verified); a model that ignores the correction does not
    (refuted).  The report carries the counts and the ring's state."""

    def _report(self, tmp: Path) -> Path:
        def echo(messages, tools):
            for m in reversed(messages):
                if m["role"] == "tool" and "$" in m["content"]:
                    import re
                    found = re.search(r"\$[\d,]+\.\d\d", m["content"])
                    return {"text": f"The refund for BK1 is {found.group(0)}."}
            return {"tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK1"}}]}
        good = run_task(ScriptedProvider(echo, model="echo"), TASK, [refund_tool()],
                        agent="good", out_dir=None)
        bad = run_task(ScriptedProvider(echo, model="echo"), TASK, [refund_tool("$90.00")],
                       agent="bad", out_dir=None)
        report = compare(Trajectory.from_dict(good), Trajectory.from_dict(bad))
        self.assertFalse(report["b"]["outcome"]["success"])
        self.assertIsNotNone(report["diagnosis"]["decisive_step"]["step"])
        self.assertEqual(report["diagnosis"]["decisive_step"]["verification"], "hypothesized")
        path = tmp / "report_t_refund.json"
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        return path

    def _replay(self, report_path: Path, turns: str, extra=()):
        proc = subprocess.run(
            [sys.executable, "-m", "deepcompare", "replay", str(report_path),
             "--provider", f"echo=scripted:{turns}", "--replays", "3", *extra],
            cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return proc.stdout, json.loads(report_path.read_text(encoding="utf-8"))

    def test_a_correction_that_flips_the_outcome_verifies_the_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report_path = self._report(tmp)
            turns = _echo_turns(tmp / "flip.json", "The refund for BK1 is $120.00.")
            out, report = self._replay(report_path, turns, ["--traces", str(tmp / "replays")])
            decisive = report["diagnosis"]["decisive_step"]
            self.assertEqual(decisive["verification"], "replay-verified")
            self.assertEqual(decisive["replay"]["replays"], 3)
            self.assertEqual(decisive["replay"]["flipped"], 3)
            self.assertIn("borrowed_from", decisive["replay"]["correction"])
            self.assertIn("replay-verified", out)
            conf = next(l for l in report["verdict_card"]["lines"] if l["key"] == "confidence")
            self.assertIn("replay-verified (3/3 replays flipped the outcome)", conf["text"])
            self.assertEqual(len(list((tmp / "replays").glob("*.json"))), 3)

    def test_a_model_that_ignores_the_correction_refutes_the_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report_path = self._report(tmp)
            turns = _echo_turns(tmp / "stubborn.json", "The refund for BK1 is $90.00.")
            out, report = self._replay(report_path, turns)
            decisive = report["diagnosis"]["decisive_step"]
            self.assertEqual(decisive["verification"], "replay-refuted")
            self.assertEqual(decisive["replay"]["flipped"], 0)
            self.assertIn("replay-refuted", out)

    def test_a_page_beside_the_report_is_re_rendered_with_a_solid_ring(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report_path = self._report(tmp)
            from deepcompare.report import render_html
            page = report_path.with_suffix(".html")
            render_html([json.loads(report_path.read_text())], {}, ROOT / "web" / "blocks.html", page)
            before = page.read_text(encoding="utf-8")
            self.assertIn('"verification": "hypothesized"', before)
            turns = _echo_turns(tmp / "flip.json", "The refund for BK1 is $120.00.")
            out, _ = self._replay(report_path, turns)
            self.assertIn("Re-rendered", out)
            after = page.read_text(encoding="utf-8")
            self.assertIn('"verification": "replay-verified"', after)


class TestWhyCommand(unittest.TestCase):
    def _report(self, tmp: Path) -> Path:
        a = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"))
        path = tmp / "report_t05.json"
        path.write_text(json.dumps(compare(a, b), indent=1), encoding="utf-8")
        return path

    def test_narration_is_stored_checked_and_changes_no_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report_path = self._report(tmp)
            before = json.loads(report_path.read_text(encoding="utf-8"))
            turns = tmp / "why.json"
            turns.write_text(json.dumps([{"text": "bolt-v3 failed because it used local "
                                                  "clock times [F1]; it was 93% cheaper."}]),
                             encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "deepcompare", "why", str(report_path),
                 "--provider", f"narrator=scripted:{turns}"],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(proc.stdout.startswith("VERDICT"))
            self.assertIn("UNSUPPORTED numbers", proc.stdout)
            self.assertIn("93", proc.stdout)
            after = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(after["narration"]["source"], "harness-provider")
            self.assertFalse(after["narration"]["faithfulness"]["faithful"])
            self.assertTrue(any(n.startswith("93") for n in
                                after["narration"]["faithfulness"]["unsupported_numbers"]))
            # the covenant: nothing but the narration key changed
            del after["narration"]
            self.assertEqual(before, after)

    def test_a_provider_failure_changes_nothing_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report_path = self._report(tmp)
            before = report_path.read_text(encoding="utf-8")
            turns = tmp / "empty.json"
            turns.write_text("[]", encoding="utf-8")   # a script with no turns fails
            proc = subprocess.run(
                [sys.executable, "-m", "deepcompare", "why", str(report_path),
                 "--provider", f"narrator=scripted:{turns}"],
                cwd=str(ROOT), capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(report_path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
