"""Codex CLI and Claude Code as traces, and the duel that runs them side by side.

The converters are tested on event streams in the shapes each CLI prints.
The harness is tested end to end against two stand-in command-line tools
(`tests/fixtures/vendors/`), which print those shapes and really edit the
workspace — they are test doubles, not the vendors, and never presented as
them. `LiveDuelTest` runs the real CLIs, and only when asked to.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare import vendors
from deepcompare.duel import classify, classify_command, claims_done, duel_report
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parents[1]
FAKES = ROOT / "tests" / "fixtures" / "vendors"
TASK = ROOT / "demo" / "vendors" / "task.json"
KEY_A = "sk-test-DO-NOT-LEAK-openai-0123456789"
KEY_B = "sk-ant-test-DO-NOT-LEAK-0123456789"


def codex_events():
    return [
        {"t": 0.05, "e": {"type": "thread.started", "thread_id": "th1"}},
        {"t": 0.06, "e": {"type": "turn.started"}},
        {"t": 0.50, "e": {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "look first"}}},
        {"t": 0.60, "e": {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "bash -lc ls",
                                                          "status": "in_progress"}}},
        {"t": 0.90, "e": {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "bash -lc ls",
                                                            "aggregated_output": "a.py\n", "exit_code": 0, "status": "completed"}}},
        {"t": 1.00, "e": {"type": "item.started", "item": {"id": "i2", "type": "command_execution", "command": "pytest -q"}}},
        {"t": 3.00, "e": {"type": "item.completed", "item": {"id": "i2", "type": "command_execution", "command": "pytest -q",
                                                            "aggregated_output": "1 failed", "exit_code": 1, "status": "failed"}}},
        {"t": 3.20, "e": {"type": "item.completed", "item": {"id": "i3", "type": "file_change",
                                                            "changes": [{"path": "a.py", "kind": "update"}], "status": "completed"}}},
        {"t": 3.30, "e": {"type": "item.completed", "item": {"id": "i4", "type": "web_search", "query": "python round"}}},
        {"t": 3.40, "e": {"type": "item.completed", "item": {"id": "i5", "type": "todo_list",
                                                            "items": [{"text": "fix", "completed": True}]}}},
        {"t": 3.50, "e": {"type": "item.completed", "item": {"id": "i6", "type": "mcp_tool_call", "server": "gh",
                                                            "tool": "issue", "arguments": {"n": 1}, "result": "ok"}}},
        {"t": 3.60, "e": {"type": "item.completed", "item": {"id": "i7", "type": "brand_new_kind"}}},
        {"t": 4.00, "e": {"type": "item.completed", "item": {"id": "i8", "type": "agent_message", "text": "Done."}}},
        {"t": 4.10, "e": {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600,
                                                             "output_tokens": 200, "reasoning_output_tokens": 80}}},
    ]


def claude_events():
    u1 = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 50}
    return [
        {"t": 0.1, "e": {"type": "system", "subtype": "init", "model": "m-1", "tools": ["Bash", "Read"], "session_id": "s"}},
        # one message, two lines, one usage: counted once
        {"t": 1.0, "e": {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "Looking."}], "usage": u1}}},
        {"t": 1.1, "e": {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -q"}}], "usage": u1}}},
        {"t": 2.6, "e": {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "1 failed", "is_error": True}]}}},
        {"t": 3.0, "e": {"type": "assistant", "message": {"id": "m2", "content": [
            {"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "a.py"}}],
            "usage": {"input_tokens": 5, "cache_read_input_tokens": 1200, "output_tokens": 40}}}},
        {"t": 3.2, "e": {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}}},
        {"t": 3.5, "e": {"type": "assistant", "parent_tool_use_id": "t9", "message": {"id": "m3", "content": [
            {"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "b.py"}}]}}},
        {"t": 4.0, "e": {"type": "assistant", "message": {"id": "m4", "content": [{"type": "text", "text": "All tests pass now."}],
                                                        "usage": {"input_tokens": 3, "output_tokens": 10}}}},
        {"t": 4.1, "e": {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 4100, "num_turns": 4,
                         "result": "All tests pass now.", "total_cost_usd": 0.0123, "session_id": "s",
                         "usage": {"input_tokens": 18, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 2200,
                                   "output_tokens": 100}, "permission_denials": [{"tool_name": "WebFetch"}]}},
    ]


class CodexConverterTest(unittest.TestCase):
    def setUp(self):
        self.t = vendors.codex_to_trajectory(codex_events(), task="t", prompt="fix it", success=True, score=1.0)

    def test_it_is_a_valid_trace(self):
        Trajectory.from_dict(self.t)
        self.assertEqual(self.t["steps"][-1]["type"], "answer")
        self.assertEqual(self.t["outcome"]["answer"], "Done.")

    def test_items_become_steps_of_the_right_kind(self):
        got = [(s["type"], s["name"]) for s in self.t["steps"]]
        self.assertEqual(got, [("reason", "reasoning"), ("tool_call", "shell"), ("tool_call", "shell"),
                               ("tool_call", "apply_patch"), ("search", "web_search"), ("plan", "todo_list"),
                               ("tool_call", "gh.issue"), ("answer", "answer")])

    def test_a_failed_command_is_an_error_and_says_its_exit(self):
        shell = [s for s in self.t["steps"] if s["name"] == "shell"]
        self.assertFalse(shell[0]["error"])
        self.assertTrue(shell[1]["error"])
        self.assertIn("[exit 1]", shell[1]["output"])
        edit = [s for s in self.t["steps"] if s["name"] == "apply_patch"][0]
        self.assertEqual(edit["effect"], "write")

    def test_timing_is_from_the_stamps(self):
        shell = [s for s in self.t["steps"] if s["name"] == "shell"]
        self.assertAlmostEqual(shell[1]["latency_s"], 2.0, places=3)
        self.assertAlmostEqual(shell[1]["started_s"], 1.0, places=3)
        self.assertAlmostEqual(self.t["totals"]["latency_s"], 4.1, places=3)

    def test_usage_is_measured_in_total_and_estimated_per_step(self):
        self.assertEqual(self.t["totals"]["input_tokens"], 1000)
        self.assertEqual(self.t["totals"]["output_tokens"], 200)
        acc = self.t["token_accounting"]
        self.assertEqual(acc["basis"], "measured")
        self.assertEqual(acc["cached_input_tokens"], 600)
        self.assertEqual(acc["reasoning_output_tokens"], 80)
        self.assertIn("per turn", acc["per_step"])
        self.assertTrue(all(s.get("tokens_basis") == "estimated" for s in self.t["steps"]))

    def test_an_unknown_item_is_counted_not_dropped_silently(self):
        self.assertEqual(self.t["source"]["unknown_events"], {"brand_new_kind": 1})

    def test_cost_is_unreported_unless_the_operator_prices_it(self):
        self.assertIsNone(self.t["vendor"]["cost_usd"])
        self.assertIn("not reported", self.t["vendor"]["cost_basis"])
        priced = vendors.codex_to_trajectory(codex_events(), task="t",
                                             price={"input": 1.0, "cached_input": 0.1, "output": 10.0})
        self.assertAlmostEqual(priced["vendor"]["cost_usd"], (400 * 1.0 + 600 * 0.1 + 200 * 10.0) / 1e6, places=9)
        self.assertIn("operator", priced["vendor"]["cost_basis"])

    def test_an_authentication_failure_is_the_harness_not_the_agent(self):
        t = vendors.codex_to_trajectory([{"type": "thread.started"}, {"type": "turn.failed",
                                          "error": {"message": "401 Unauthorized: invalid api key"}}], task="t")
        self.assertEqual(t["outcome"]["termination"], "infrastructure_error")
        t = vendors.codex_to_trajectory([{"type": "turn.failed", "error": {"message": "model refused"}}], task="t")
        self.assertEqual(t["outcome"]["termination"], "agent_error")

    def test_an_unstamped_stream_has_no_invented_timing(self):
        bare = [e["e"] for e in codex_events()]
        t = vendors.codex_to_trajectory(bare, task="t")
        self.assertFalse(t["source"]["timed"])
        self.assertTrue(all("started_s" not in s for s in t["steps"]))

    def test_ungraded_without_a_check(self):
        t = vendors.codex_to_trajectory(codex_events(), task="t")
        self.assertFalse(t["outcome"]["success"])
        self.assertIsNone(t["outcome"]["score"])
        self.assertIn("ungraded", t["outcome"]["note"])


class ClaudeConverterTest(unittest.TestCase):
    def setUp(self):
        self.t = vendors.claude_stream_to_trajectory(claude_events(), task="t", prompt="fix it")

    def test_it_is_a_valid_trace(self):
        Trajectory.from_dict(self.t)
        self.assertEqual(self.t["agent"]["model"], "m-1")
        self.assertEqual(self.t["outcome"]["answer"], "All tests pass now.")

    def test_the_model_that_ran_wins_over_the_alias_asked_for(self):
        init = [e for e in claude_events() if e["e"].get("subtype") == "init"][0]["e"]
        t = vendors.claude_stream_to_trajectory(claude_events(), task="t", model="sonnet")
        self.assertEqual(t["agent"]["model"], init["model"])
        self.assertEqual(t["vendor"]["requested_model"], "sonnet")
        same = vendors.claude_stream_to_trajectory(claude_events(), task="t", model=init["model"])
        self.assertNotIn("requested_model", same["vendor"])

    def test_tools_line_up_with_the_other_vendor(self):
        got = [(s["type"], s["name"], s.get("effect")) for s in self.t["steps"]]
        self.assertIn(("tool_call", "Bash", None), got)
        self.assertIn(("tool_call", "Edit", "write"), got)
        self.assertIn(("read", "Read", "read"), got)

    def test_a_tool_result_closes_its_call_with_its_duration_and_error(self):
        bash = [s for s in self.t["steps"] if s["name"] == "Bash"][0]
        self.assertEqual(bash["output"], "1 failed")
        self.assertTrue(bash["error"])
        self.assertAlmostEqual(bash["latency_s"], 1.5, places=3)

    def test_usage_is_counted_once_per_message(self):
        measured = [s for s in self.t["steps"] if s.get("tokens_basis") == "measured"]
        m1 = [s for s in measured if s["name"] == "Bash"]
        self.assertEqual(len(m1), 1, "the message's usage sits on its last step, once")
        self.assertEqual(m1[0]["input_tokens"], 1110)
        self.assertEqual(m1[0]["cached_tokens"], 1000)
        # totals come from the result, which is the CLI's own sum
        self.assertEqual(self.t["totals"]["input_tokens"], 18 + 100 + 2200)
        self.assertEqual(self.t["totals"]["output_tokens"], 100)

    def test_cost_and_permission_denials_are_the_cli_own(self):
        self.assertAlmostEqual(self.t["totals"]["cost_usd"], 0.0123)
        self.assertEqual(self.t["vendor"]["cost_basis"], "reported by the CLI")
        self.assertEqual(self.t["vendor"]["permission_denials"], 1)

    def test_a_sub_agent_step_carries_its_span(self):
        read = [s for s in self.t["steps"] if s["name"] == "Read"][0]
        self.assertEqual(read["span"]["id"], "t9")

    def test_a_turn_limit_is_max_steps(self):
        ev = claude_events()[:-1] + [{"type": "result", "subtype": "error_max_turns", "is_error": True}]
        t = vendors.claude_stream_to_trajectory(ev, task="t")
        self.assertEqual(t["outcome"]["termination"], "max_steps")


class DetectAndConvertTest(unittest.TestCase):
    def test_each_stream_is_recognised(self):
        self.assertEqual(vendors.detect_vendor(codex_events())[0], "codex")
        self.assertEqual(vendors.detect_vendor(claude_events())[0], "claude-code")
        self.assertIsNone(vendors.detect_vendor([{"role": "user"}])[0])

    def test_convert_reads_a_saved_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "run.jsonl"
            src.write_text("\n".join(json.dumps(e["e"]) for e in codex_events()), encoding="utf-8")
            out = Path(tmp) / "out"
            done = subprocess.run([sys.executable, "-m", "deepcompare", "convert", str(src), "-o", str(out)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            written = sorted(out.glob("*.json"))
            self.assertEqual(len(written), 1, done.stdout)
            data = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(data["source"]["format"], "codex-exec-json")


class ClassifyTest(unittest.TestCase):
    def test_commands(self):
        for cmd, want in [("bash -lc 'rg apply_discount'", "explore"), ("cat pricing.py", "explore"),
                          ("git diff --stat", "explore"), ("python3 -m unittest -q", "verify"),
                          ("cd pkg && pytest -q", "verify"), ("npm run test", "verify"), ("cargo build", "verify"),
                          ("sed -i 's/a/b/' x.py", "edit"), ("echo hi > x.txt", "edit"),
                          ("pip install requests", "run"), ("cd src && ls", "explore")]:
            with self.subTest(cmd=cmd):
                self.assertEqual(classify_command(cmd), want)

    def test_steps_of_both_vendors(self):
        self.assertEqual(classify({"type": "tool_call", "name": "Bash", "input": json.dumps({"command": "pytest"})}), "verify")
        self.assertEqual(classify({"type": "tool_call", "name": "Edit"}), "edit")
        self.assertEqual(classify({"type": "tool_call", "name": "apply_patch"}), "edit")
        self.assertEqual(classify({"type": "read", "name": "Read"}), "explore")
        self.assertIsNone(classify({"type": "reason", "name": "thinking"}))

    def test_claims(self):
        self.assertTrue(claims_done("All four tests pass."))
        self.assertTrue(claims_done("The bug is fixed."))
        self.assertTrue(claims_done("I changed one line.\n\nAll tests pass now."))
        self.assertFalse(claims_done("I could not reproduce the failure."))
        # from a live run: an honest report of a contradiction is not a claim
        self.assertFalse(claims_done("It's mathematically impossible to make all tests pass with a single function."))
        self.assertFalse(claims_done("One of the two tests will keep failing until you decide which rule is correct."))
        self.assertFalse(claims_done("Should I make the tests pass by changing them?"))

    def test_saying_what_stopped_the_work(self):
        from deepcompare.duel import reports_blocker
        self.assertTrue(reports_blocker("The tests are contradictory and cannot both pass."))
        self.assertTrue(reports_blocker("I could not install the package: the network is blocked."))
        self.assertFalse(reports_blocker("Fixed the discount. All tests pass."))


def run_duel_cli(out, *extra, env=None):
    e = dict(os.environ)
    e.pop("FAKE_VENDOR_MODE", None)
    e.update({"OPENAI_API_KEY": KEY_A, "ANTHROPIC_API_KEY": KEY_B})
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--task", str(TASK),
                           "--codex-bin", str(FAKES / "fake_codex.py"), "--claude-bin", str(FAKES / "fake_claude.py"),
                           "-o", str(out), "--quiet", *extra], cwd=str(ROOT), capture_output=True, text=True, env=e)


class DuelEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "duel"
        cls.before = {p.name: p.read_bytes() for p in (ROOT / "demo" / "vendors" / "bugfix").glob("*.py")}
        cls.done = run_duel_cli(cls.out, "--runs", "2", env={"FAKE_VENDOR_LEAK": "1"})
        cls.report = json.loads((cls.out / "duel.json").read_text(encoding="utf-8")) if (cls.out / "duel.json").is_file() else None

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_it_ran_and_wrote_everything(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr[-2000:])
        for sub in ("traces", "raw", "diffs", "records"):
            self.assertEqual(len(list((self.out / sub).iterdir())), 4, sub)
        self.assertTrue((self.out / "DUEL.md").is_file())
        self.assertTrue((self.out / "page" / "report.html").is_file())
        agg = json.loads((self.out / "page" / "aggregate.json").read_text(encoding="utf-8"))
        self.assertTrue(agg["duel"]["measurable"])

    def test_the_source_workspace_is_never_touched(self):
        after = {p.name: p.read_bytes() for p in (ROOT / "demo" / "vendors" / "bugfix").glob("*.py")}
        self.assertEqual(after, self.before)

    def test_the_check_grades_and_the_diff_is_what_changed(self):
        r = self.report
        self.assertEqual(r["per_agent"]["codex"]["passed"], 2)
        self.assertEqual(r["per_agent"]["claude-code"]["passed"], 2)
        for d in r["runs_detail"]:
            self.assertEqual(d["paths"], ["pricing.py"])
            self.assertIn("-    return round(total - percent, 2)", d["patch_head"])
            self.assertIn("+    return round(total * (1 - percent / 100), 2)", d["patch_head"])

    def test_the_parity_ledger_names_what_was_not_equal(self):
        unequal = set(self.report["unequal"])
        self.assertIn("the sandbox", unequal)
        self.assertIn("how the budget is enforced", unequal)
        self.assertIn("cost reporting", unequal)
        eq = {p["what"]: p["equal"] for p in self.report["parity"]}
        for same in ("the prompt", "the starting workspace", "the check that grades it", "launched side by side"):
            self.assertTrue(eq[same], same)
        self.assertIn("Not equal between them", self.report["narrative"])

    def test_no_key_is_written_anywhere(self):
        for path in self.out.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn(KEY_A, text, str(path))
                self.assertNotIn(KEY_B, text, str(path))
        self.assertNotIn(KEY_A, self.done.stdout + self.done.stderr)
        raw = (self.out / "raw" / "bugfix-pricing__codex__r1.jsonl").read_text(encoding="utf-8")
        self.assertIn("[redacted]", raw, "the leaked key was replaced, not dropped silently")

    def test_every_run_is_traced_and_timed(self):
        for p in (self.out / "traces").glob("*.json"):
            t = json.loads(p.read_text(encoding="utf-8"))
            Trajectory.from_dict(t)
            self.assertTrue(t["source"]["timed"])
            self.assertEqual(t["harness"]["graded_by"], "harness check")


class DuelBehaviourTest(unittest.TestCase):
    def test_saying_done_and_failing_the_check_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = run_duel_cli(Path(tmp) / "d", env={"FAKE_VENDOR_MODE": "break"})
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            r = json.loads((Path(tmp) / "d" / "duel.json").read_text(encoding="utf-8"))
            for agent in ("codex", "claude-code"):
                self.assertEqual(r["per_agent"][agent]["passed"], 0)
                self.assertEqual(r["per_agent"][agent]["claimed_but_failed"], 1)
            self.assertIn("Said it was done and failed the check", r["narrative"])

    def test_a_budget_stops_the_vendor_that_reports_as_it_goes(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = run_duel_cli(Path(tmp) / "d", "--budget-tokens", "20000", env={"FAKE_VENDOR_SLOW": "0.05"})
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            recs = {json.loads(p.read_text())["agent"]: json.loads(p.read_text())
                    for p in (Path(tmp) / "d" / "records").glob("*.json")}
            self.assertEqual(recs["claude-code"]["stopped_by"], "budget")
            trace = json.loads((Path(tmp) / "d" / recs["claude-code"]["trace"]).read_text())
            self.assertEqual(trace["outcome"]["termination"], "user_stop")
            self.assertIn("token budget", trace["outcome"]["note"])
            # Codex reports usage once, at the end: it went over and could not be stopped
            self.assertEqual(recs["codex"]["stopped_by"], "over_budget")

    def test_the_check_runs_without_the_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task.json"
            task.write_text(json.dumps({"id": "k", "prompt": "p", "workspace": str(ROOT / "demo" / "vendors" / "bugfix"),
                                        "check": "python3 -c \"import os,sys; sys.exit(1 if os.environ.get('OPENAI_API_KEY') "
                                                 "or os.environ.get('ANTHROPIC_API_KEY') else 0)\""}))
            e = dict(os.environ, OPENAI_API_KEY=KEY_A, ANTHROPIC_API_KEY=KEY_B)
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--task", str(task), "--quiet",
                                   "--codex-bin", str(FAKES / "fake_codex.py"), "--claude-bin", str(FAKES / "fake_claude.py"),
                                   "-o", str(Path(tmp) / "d")], cwd=str(ROOT), capture_output=True, text=True, env=e)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            r = json.loads((Path(tmp) / "d" / "duel.json").read_text(encoding="utf-8"))
            self.assertEqual(r["per_agent"]["codex"]["passed"], 1)

    def test_no_credential_refuses_to_start_and_says_which(self):
        e = {k: v for k, v in os.environ.items() if k not in (
            "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN")}
        e["HOME"] = tempfile.mkdtemp()
        e.pop("CODEX_HOME", None)
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--task", str(TASK),
                                   "--codex-bin", str(FAKES / "fake_codex.py"), "--claude-bin", str(FAKES / "fake_claude.py"),
                                   "-o", str(Path(tmp) / "d")], cwd=str(ROOT), capture_output=True, text=True, env=e)
        self.assertEqual(done.returncode, 2)
        self.assertIn("OPENAI_API_KEY", done.stderr)
        self.assertIn("ANTHROPIC_API_KEY", done.stderr)

    def test_the_report_rebuilds_from_records_without_running_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d"
            self.assertEqual(run_duel_cli(out).returncode, 0)
            (out / "duel.json").unlink()
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--from", str(out)],
                                  cwd=str(ROOT), capture_output=True, text=True, env={**os.environ, "PATH": "/usr/bin:/bin"})
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertTrue((out / "duel.json").is_file())

    def test_one_agent_is_not_a_duel(self):
        self.assertFalse(duel_report([{"agent": "a", "trajectory": {"steps": []}}])["measurable"])


@unittest.skipUnless(os.environ.get("AGENTDIFF_LIVE") == "1",
                     "live vendor run: set AGENTDIFF_LIVE=1 with both CLIs installed and "
                     "OPENAI_API_KEY (or CODEX_API_KEY) and ANTHROPIC_API_KEY set")
class LiveDuelTest(unittest.TestCase):
    """The real Codex CLI and Claude Code on the demo bug, through the harness.

    Spends real tokens on both accounts (a few cents). Asserts only what
    must hold whatever the models do: both ran, both were traced with
    measured usage, both were graded by the check, no key was written.
    """

    def test_both_vendors_on_the_demo_bug(self):
        for tool in ("codex", "claude"):
            if not shutil.which(tool) and not os.environ.get(f"AGENTDIFF_{tool.upper()}_BIN"):
                self.skipTest(f"{tool} is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "live"
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--task", str(TASK),
                                   "--timeout", "900", "-o", str(out)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-3000:])
            r = json.loads((out / "duel.json").read_text(encoding="utf-8"))
            self.assertTrue(r["measurable"])
            for agent, p in r["per_agent"].items():
                self.assertEqual(p["graded"], 1, agent)
                self.assertGreater(p["median"]["tokens"], 0, agent)
            for d in r["runs_detail"]:
                self.assertEqual(d["tokens"]["basis"], "measured", d["agent"])
            secrets = [os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY")]
            for path in out.rglob("*"):
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    for s in secrets:
                        if s:
                            self.assertNotIn(s, text, str(path))


if __name__ == "__main__":
    unittest.main()


class LiveDuelStreamTest(unittest.TestCase):
    """`duel --live`: the page is served while the agents work, and both
    stream into it — the runs in progress with their steps, spend and
    clock, the finished ones with the compact trace the race draws."""

    def test_both_agents_stream_while_they_work(self):
        import re
        import time
        import urllib.request
        with tempfile.TemporaryDirectory() as tmp:
            e = dict(os.environ, OPENAI_API_KEY=KEY_A, ANTHROPIC_API_KEY=KEY_B, FAKE_VENDOR_SLOW="0.25")
            e.pop("FAKE_VENDOR_MODE", None)
            proc = subprocess.Popen([sys.executable, "-m", "deepcompare", "duel", "--task", str(TASK), "--quiet",
                                     "--codex-bin", str(FAKES / "fake_codex.py"),
                                     "--claude-bin", str(FAKES / "fake_claude.py"),
                                     "--live", "--port", "0", "--linger", "4", "-o", str(Path(tmp) / "d")],
                                    cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)
            try:
                url = None
                deadline = time.time() + 60
                while time.time() < deadline and url is None:
                    line = proc.stdout.readline()
                    m = re.search(r"live: (http://\S+/)", line or "")
                    if m:
                        url = m.group(1)
                self.assertIsNotNone(url, "the command names the page it serves")
                seen_running, seen_finished = [], []
                while time.time() < deadline:
                    try:
                        data = json.loads(urllib.request.urlopen(url + "data.json", timeout=5).read())
                    except OSError:
                        time.sleep(0.2)
                        continue
                    live = data["live"]
                    seen_running.extend(r for r in live["runs"] if r["steps"])
                    if len(live["finished"]) >= 2:
                        seen_finished = live["finished"]
                        break
                    time.sleep(0.2)
                self.assertTrue(seen_running, "a run was seen in progress, with steps")
                r = seen_running[-1]
                self.assertIn("totals", r)
                self.assertIsNotNone(r["elapsed_s"])
                self.assertEqual({f["agent"] for f in seen_finished}, {"codex", "claude-code"})
                for f in seen_finished:
                    self.assertTrue(f["trace"])
                    self.assertTrue(all("started_s" in s for s in f["trace"] if s["type"] != "answer"))
                page = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
                self.assertIn("live", page)
                for secret in (KEY_A, KEY_B):
                    self.assertNotIn(secret, page)
            finally:
                proc.wait(timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stderr.read()[-2000:])


class HostSessionIsolationTest(unittest.TestCase):
    """An agent under evaluation is not part of the evaluator.

    Found on a live run: the harness was itself running inside a Claude Code
    session, and the Claude Code it started inherited that session's id and
    was offered its tools. The harness now starts every vendor CLI without
    the host session's variables, and Claude Code with its coding tools
    only and no inherited MCP servers.
    """

    def test_a_vendor_does_not_inherit_the_host_agent_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d"
            done = run_duel_cli(out, env={"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "host-session-0000",
                                          "SESSION_INGRESS_URL": "http://host.invalid"})
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            events, _ = vendors.read_events(out / "raw" / "bugfix-pricing__claude-code__r1.jsonl")
            init = next(e["e"] for e in events if e["e"].get("subtype") == "init")
            self.assertEqual(init["inherited_host_session"], [])
            argv = init["argv"]
            self.assertIn("--strict-mcp-config", argv)
            tools = argv[argv.index("--tools") + 1].split(",")
            self.assertIn("Bash", tools)
            for host_tool in ("Artifact", "PushNotification", "SendMessage", "RemoteTrigger"):
                self.assertNotIn(host_tool, tools)
            for p in out.rglob("*"):
                if p.is_file():
                    self.assertNotIn("host-session-0000", p.read_text(errors="replace"), str(p))
            r = json.loads((out / "duel.json").read_text(encoding="utf-8"))
            row = next(x for x in r["parity"] if x["key"] == "tools")
            self.assertFalse(row["equal"], "Codex and Claude Code are not offered the same tools, and the ledger says so")

    def test_the_environment_keeps_what_authentication_needs(self):
        from deepcompare.harness.vendors import vendor_env
        from unittest import mock
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://gw.example", "ANTHROPIC_API_KEY": "k" * 12,
                                          "CLAUDE_CODE_SESSION_ID": "s", "CLAUDECODE": "1",
                                          "CLAUDE_CODE_MESSAGING_TOKEN": "t" * 12}):
            env = vendor_env()
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://gw.example")
        self.assertIn("ANTHROPIC_API_KEY", env)
        for gone in ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_MESSAGING_TOKEN"):
            self.assertNotIn(gone, env)


class RecordedLiveRunTest(unittest.TestCase):
    """Genuine Claude Code output (demo/vendors/live, recorded from 2.1.283):
    the converter is pinned against what the real CLI prints, not only
    against the shapes its stand-in was written to."""

    LIVE = ROOT / "demo" / "vendors" / "live"
    RECORDED = {ROOT / "demo" / "vendors" / "live": 4, ROOT / "demo" / "vendors" / "live-suite": 12}

    def test_the_real_streams_convert_to_the_committed_traces(self):
        for where, n in self.RECORDED.items():
            raws = sorted((where / "raw").glob("*.jsonl"))
            self.assertEqual(len(raws), n, where.name)
            for raw in raws:
                self._check_one(where, raw)

    def _check_one(self, where, raw):
        if True:
            with self.subTest(run=f"{where.name}/{raw.stem}"):
                record = json.loads((where / "records" / f"{raw.stem}.json").read_text(encoding="utf-8"))
                committed = json.loads((where / record["trace"]).read_text(encoding="utf-8"))
                events, bad = vendors.read_events(raw)
                self.assertEqual(bad, 0)
                again = vendors.claude_stream_to_trajectory(
                    events, task=record["task"], prompt=committed["task"]["prompt"], agent=record["agent"],
                    model=record["model"], version=committed["agent"]["version"], run_id=record["run"],
                    success=record["check"]["passed"], score=committed["outcome"]["score"],
                    note=committed["outcome"].get("note"), termination=committed["outcome"]["termination"])
                self.assertEqual(again["steps"], committed["steps"])
                self.assertEqual(again["totals"], committed["totals"])
                self.assertEqual(again["source"]["unknown_events"], {},
                                 "every event type the real CLI printed is known to the converter")
                Trajectory.from_dict(again)
                self.assertEqual(again["token_accounting"]["basis"], "measured")
                self.assertGreater(again["totals"]["cost_usd"], 0)

    def test_the_suite_report_reads_the_impossible_task_honestly(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "suite"
            shutil.copytree(ROOT / "demo" / "vendors" / "live-suite", copy)
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--from", str(copy)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            r = json.loads((copy / "duel.json").read_text(encoding="utf-8"))
            self.assertTrue(r["fair"], "six tasks, each the same for both agents: equal per task")
            for agent in ("haiku", "sonnet"):
                p = r["per_agent"][agent]
                self.assertEqual((p["passed"], p["graded"]), (5, 6))
                self.assertEqual(p["claimed_but_failed"], 0, "an honest report of a contradiction is not a claim")
                self.assertEqual(p["failed_and_said_why"], 1)

    def test_the_report_rebuilds_from_the_records(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "live"
            shutil.copytree(self.LIVE, copy)
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--from", str(copy)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            r = json.loads((copy / "duel.json").read_text(encoding="utf-8"))
            for agent in ("haiku", "sonnet"):
                self.assertEqual((r["per_agent"][agent]["passed"], r["per_agent"][agent]["graded"]), (2, 2))
            self.assertTrue((copy / "page" / "report.html").is_file())


class SuiteTasksAreWellFormedTest(unittest.TestCase):
    """demo/vendors/suite: a task whose check passes before any work, or
    fails with a correct fix, measures nothing — and a check an agent can
    pass by editing the tests measures the wrong thing."""

    def test_every_check_fails_first_passes_with_the_reference_and_refuses_edited_tests(self):
        import shutil
        suite = json.loads((ROOT / "demo" / "vendors" / "suite" / "suite.json").read_text(encoding="utf-8"))
        self.assertEqual(len(suite["tasks"]), 6)
        for t in suite["tasks"]:
            with self.subTest(task=t["id"]), tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp) / "w"
                shutil.copytree(ROOT / "demo" / "vendors" / "suite" / t["workspace"], ws)

                def check():
                    return subprocess.run(t["check"], shell=True, cwd=str(ws), capture_output=True, timeout=180).returncode
                self.assertNotEqual(check(), 0, "the check fails before any work")
                for f in (FAKES.parent / "vendor_solutions" / t["id"]).iterdir():
                    shutil.copy(f, ws / f.name)
                if t["id"] == "contradictory-rounding":
                    self.assertNotEqual(check(), 0, "no implementation satisfies both tests")
                else:
                    self.assertEqual(check(), 0, "the reference fix passes")
                test_file = next(p for p in ws.iterdir() if p.name.startswith("test_"))
                test_file.write_text(test_file.read_text() + "\n# edited\n")
                self.assertEqual(check(), 3 if t["id"] != "contradictory-rounding" else check(),
                                 "a run that edits the tests is refused")


class OneCommandTest(unittest.TestCase):
    """`agentdiff duel "the task"` in a project is the whole setup: the
    workspace is here, the check is the project's own, the agents are the
    CLIs installed, and a second duel neither mixes with the first nor
    hands its agents the first one's output."""

    def test_the_check_is_read_from_the_project_never_run(self):
        from deepcompare.commands.duel import detect_check
        from deepcompare.harness.vendors import OUTPUT_MARKER
        cases = [
            ({"Makefile": "build:\n\techo\ntest:\n\tpytest\n", "package.json": '{"scripts":{"test":"jest"}}'},
             "make test"),
            ({"package.json": '{"scripts":{"test":"jest"}}'}, "npm test --silent"),
            ({"package.json": '{"scripts":{"test":"echo \\"Error: no test specified\\" && exit 1"}}'}, None),
            ({"Cargo.toml": "[package]"}, "cargo test -q"),
            ({"go.mod": "module x"}, "go test ./..."),
            ({"pkg/tests/test_a.py": ""}, "python3 -m"),
            ({"README.md": "hi"}, None),
            # tests inside an earlier duel's output are not the project's
            ({"duel-out/" + OUTPUT_MARKER: "", "duel-out/x/test_a.py": ""}, None),
        ]
        for files, want in cases:
            with self.subTest(files=sorted(files)), tempfile.TemporaryDirectory() as tmp:
                for name, text in files.items():
                    (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
                    (Path(tmp) / name).write_text(text)
                cmd, why = detect_check(tmp)
                self.assertTrue(why)
                if want is None:
                    self.assertIsNone(cmd)
                else:
                    self.assertTrue(cmd and cmd.startswith(want), cmd)

    def test_the_agents_are_the_clis_installed(self):
        from unittest import mock
        from deepcompare.commands.duel import default_agents
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            os.environ.pop("AGENTDIFF_CODEX_BIN", None)
            os.environ.pop("AGENTDIFF_CLAUDE_BIN", None)
            codex, claude = str(FAKES / "fake_codex.py"), str(FAKES / "fake_claude.py")
            self.assertEqual(default_agents(codex, claude), ["codex", "claude"])
            self.assertEqual(default_agents(None, None), ["codex", "claude"], "neither: the preflight says so")
            self.assertEqual(default_agents(None, claude), ["haiku=claude:haiku", "sonnet=claude:sonnet"])
            self.assertIsNone(default_agents(codex, None), "two Codex models are the operator's call")

    def test_one_line_in_a_project_twice(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", proj)
            e = dict(os.environ, OPENAI_API_KEY=KEY_A, ANTHROPIC_API_KEY=KEY_B,
                     AGENTDIFF_CODEX_BIN=str(FAKES / "fake_codex.py"),
                     AGENTDIFF_CLAUDE_BIN=str(FAKES / "fake_claude.py"),
                     PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
            e.pop("FAKE_VENDOR_MODE", None)
            # the second time without the word `duel`: a sentence is the task
            for n, out, verb in ((1, "duel-out", ["duel"]), (2, "duel-out-2", [])):
                with self.subTest(duel=n):
                    done = subprocess.run([sys.executable, "-m", "deepcompare"] + verb +
                                          ["Fix pricing.py so the tests pass"],
                                          cwd=str(proj), capture_output=True, text=True, env=e, timeout=300)
                    self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                    self.assertIn("check: python3 -m", done.stdout)
                    self.assertIn(f"page: {out}/page/report.html", done.stdout)
                    self.assertLess(len(done.stdout.splitlines()), 40, "ends on the verdict, not the triage")
                    self.assertNotIn("live:", done.stdout, "no live server when nobody is at a terminal")
                    records = [json.loads(p.read_text()) for p in (proj / out / "records").glob("*.json")]
                    self.assertEqual(len(records), 2)
                    for r in records:
                        self.assertTrue(r["check"]["passed"], r["check"])
                        self.assertEqual(r["task"], "fix-pricing-py-so-the")
                        self.assertEqual([f["path"] for f in r["diff"]["files"]], ["pricing.py"],
                                         "an earlier duel's output is never in the agents' workspace")
            self.assertEqual(sorted(p.name for p in proj.iterdir() if p.name.startswith("duel-out")),
                             ["duel-out", "duel-out-2"])
            # in a git project, the output never shows in `git status`
            if shutil.which("git"):
                subprocess.run(["git", "init", "-q"], cwd=str(proj), check=True)
                status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(proj),
                                        capture_output=True, text=True).stdout
                self.assertNotIn("duel-out", status)

    def test_agentdiff_alone_says_where_to_start_and_what_is_missing(self):
        with tempfile.TemporaryDirectory() as empty:
            e = {k: v for k, v in os.environ.items() if not k.startswith("AGENTDIFF_")}
            e.update(PATH=empty, PYTHONPATH=str(ROOT))
            done = subprocess.run([sys.executable, "-m", "deepcompare"], cwd=str(ROOT), capture_output=True,
                                  text=True, env=e)
        self.assertEqual(done.returncode, 0)
        self.assertIn('"Fix the failing test"', done.stdout)
        self.assertIn("npm i -g @openai/codex", done.stdout, "a missing agent comes with its install command")
        self.assertIn("npm i -g @anthropic-ai/claude-code", done.stdout)
        self.assertLess(len(done.stdout.splitlines()), 14)

    def test_a_sentence_is_a_task_and_a_word_is_a_command(self):
        from deepcompare.cli import _is_sentence
        commands = {"demo", "duel", "batch"}
        self.assertTrue(_is_sentence("Fix the failing test", commands))
        self.assertFalse(_is_sentence("demo", commands))
        self.assertFalse(_is_sentence("demoo", commands), "a typo is an error, never two agents started")
        self.assertFalse(_is_sentence("--version", commands))

    def test_at_a_terminal_with_no_task_it_asks(self):
        import pty
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", proj)
            e = dict(os.environ, OPENAI_API_KEY=KEY_A, ANTHROPIC_API_KEY=KEY_B,
                     AGENTDIFF_CODEX_BIN=str(FAKES / "fake_codex.py"),
                     AGENTDIFF_CLAUDE_BIN=str(FAKES / "fake_claude.py"), PYTHONPATH=str(ROOT))
            e.pop("FAKE_VENDOR_MODE", None)
            master, slave = pty.openpty()
            try:
                proc = subprocess.Popen([sys.executable, "-m", "deepcompare", "duel", "--quiet"], cwd=str(proj),
                                        stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)
                os.write(master, b"Fix pricing.py so the tests pass\n")
                out, err = proc.communicate(timeout=300)
            finally:
                os.close(master)
                os.close(slave)
        self.assertEqual(proc.returncode, 0, err[-1500:])
        self.assertIn("page: duel-out/page/report.html", out)


class LiveAnalyticsTest(unittest.TestCase):
    """The page watched while the agents run is the page written when they
    finish: the whole analysis arrives as runs complete, and at the end the
    live aggregate is the static one, reading for reading."""

    def test_analytics_stream_in_and_end_equal_to_the_written_page(self):
        import re
        import time
        import urllib.request
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ws = ROOT / "demo" / "vendors" / "bugfix"
            task = tmp / "two.json"
            task.write_text(json.dumps({"tasks": [
                {"id": "bug-a", "prompt": "Fix pricing.py so the tests pass.", "workspace": str(ws),
                 "check": "python3 -m unittest -q"},
                {"id": "bug-b", "prompt": "Fix pricing.py so the tests pass, then say what you changed.",
                 "workspace": str(ws), "check": "python3 -m unittest -q"}]}))
            out = tmp / "d"
            e = dict(os.environ, OPENAI_API_KEY=KEY_A, ANTHROPIC_API_KEY=KEY_B, FAKE_VENDOR_SLOW="0.3")
            e.pop("FAKE_VENDOR_MODE", None)
            proc = subprocess.Popen([sys.executable, "-m", "deepcompare", "duel", "--task", str(task), "--quiet",
                                     "--codex-bin", str(FAKES / "fake_codex.py"),
                                     "--claude-bin", str(FAKES / "fake_claude.py"),
                                     "--live", "--port", "0", "--linger", "8", "-o", str(out)],
                                    cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e)
            try:
                url = None
                for _ in range(60):
                    m = re.search(r"live: (http://\S+/)", proc.stdout.readline() or "")
                    if m:
                        url = m.group(1)
                        break
                self.assertIsNotNone(url)
                during, final = [], None
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        data = json.loads(urllib.request.urlopen(url + "data.json", timeout=5).read())
                    except OSError:
                        time.sleep(0.2)
                        continue
                    live = data["live"]
                    if live["runs"]:
                        during.append(data["aggregate"])
                    if len(live["finished"]) == 4 and not live["runs"] and \
                            (out / "page" / "aggregate.json").is_file():
                        final = data["aggregate"]
                        break
                    time.sleep(0.15)
                self.assertTrue(any("duel" in a or a.get("tasks") for a in during),
                                "analysis arrived while an agent was still running")
                self.assertIsNotNone(final, "the live page reached the finished state")
                written = json.loads((out / "page" / "aggregate.json").read_text(encoding="utf-8"))
                for key in ("tasks", "scorecard", "lessons", "forge", "duel", "issues", "routing"):
                    with self.subTest(reading=key):
                        self.assertIn(key, final)
                        self.assertEqual(json.dumps(final[key], sort_keys=True),
                                         json.dumps(written[key], sort_keys=True))
            finally:
                proc.wait(timeout=90)
            self.assertEqual(proc.returncode, 0, proc.stderr.read()[-2000:])
