"""Live guards: the remedies, enforced through Claude Code's hooks.

Each test feeds the guard the payloads Claude Code sends (the shapes were
read off a real session: a failed command arrives as
``PostToolUseFailure`` with its output in ``error``) and reads the answer
it would print.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.guard import decide, failed_output, is_check_command, summary

ROOT = Path(__file__).resolve().parent.parent


def _pre(cmd=None, tool="Bash", path=None):
    tin = {"command": cmd} if tool == "Bash" else {"file_path": path, "old_string": "a", "new_string": "b"}
    return {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": tool, "tool_input": tin}


def _post(cmd=None, tool="Bash", path=None, out="ok", failed=False):
    p = _pre(cmd, tool, path)
    if failed:
        p.update(hook_event_name="PostToolUseFailure", error=f"Exit code 1\n{out}", is_interrupt=False)
    else:
        p.update(hook_event_name="PostToolUse", tool_response={"stdout": out, "stderr": "", "interrupted": False})
    return p


STOP = {"hook_event_name": "Stop", "session_id": "s1", "stop_hook_active": False}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, *payloads, **opts):
        out = None
        for p in payloads:
            out = decide(p, self.root, **opts)
        return out

    def _denied(self, out):
        return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_a_failing_command_rerun_unchanged_is_refused_with_its_failure(self):
        fail = _post("pytest -q", out="1 failed, 3 passed", failed=True)
        self.assertIsNone(self.feed(_pre("pytest -q"), fail, _pre("pytest -q")), "once failed is no loop")
        out = self.feed(fail, _pre("pytest -q"))
        self.assertTrue(self._denied(out))
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("2 times in a row", reason)
        self.assertIn("1 failed, 3 passed", reason)

    def test_running_it_again_after_an_edit_is_the_normal_loop(self):
        fail = _post("pytest -q", out="1 failed", failed=True)
        self.feed(fail, fail)
        self.assertIsNone(self.feed(_post(tool="Edit", path=str(self.root / "a.py")), _pre("pytest -q")))

    def test_a_command_that_passes_is_never_refused(self):
        ok = _post("python a.py", out="hello")
        self.assertIsNone(self.feed(ok, ok, ok, _pre("python a.py")))

    def test_finishing_with_unchecked_edits_is_refused_a_bounded_number_of_times(self):
        self.feed(_post(tool="Edit", path=str(self.root / "a.py")))
        first = self.feed(STOP, check="pytest -q")
        self.assertEqual(first["decision"], "block")
        self.assertIn("`pytest -q`", first["reason"])
        self.assertEqual(self.feed(STOP)["decision"], "block")
        self.assertIsNone(self.feed(STOP), "never refused forever")

    def test_finishing_after_a_passing_check_or_without_edits_is_let_through(self):
        self.assertIsNone(self.feed(_post("ls"), STOP), "no edits, nothing to check")
        self.feed(_post(tool="Write", path=str(self.root / "a.py")), _post("python -m pytest -q", out="4 passed"))
        self.assertIsNone(self.feed(STOP))

    def test_a_failed_last_check_keeps_the_agent_working(self):
        self.feed(_post(tool="Edit", path=str(self.root / "a.py")),
                  _post("pytest -q", out="FAILED t.py::test_a - AssertionError", failed=True))
        out = self.feed(STOP)
        self.assertIn("the last check failed", out["reason"])
        self.assertIn("AssertionError", out["reason"])

    def test_an_edit_that_failed_to_land_is_not_an_edit(self):
        self.feed(_post(tool="Edit", path=str(self.root / "a.py"), failed=True, out="old_string not found"))
        self.assertIsNone(self.feed(STOP))

    def test_test_files_are_protected_only_when_asked(self):
        edit = _pre(tool="Edit", path=str(self.root / "tests" / "test_a.py"))
        self.assertIsNone(self.feed(edit))
        out = self.feed(edit, enabled=("tests",))
        self.assertTrue(self._denied(out))
        self.assertIn("is a test", out["hookSpecificOutput"]["permissionDecisionReason"])
        code = _pre(tool="Edit", path=str(self.root / "src" / "a.py"))
        self.assertIsNone(self.feed(code, enabled=("tests",)))

    def test_every_refusal_is_logged_and_counted(self):
        fail = _post("pytest -q", out="1 failed", failed=True)
        self.feed(fail, fail, _pre("pytest -q"), _post(tool="Edit", path="a.py"), STOP)
        s = summary(self.root)
        self.assertEqual((s["fired"], s["sessions"], s["by_guard"]), (2, 1, {"repeat": 1, "check": 1}))
        self.assertIsNone(summary(self.root / "nowhere"))

    def test_a_new_prompt_starts_a_new_turn(self):
        def turn(p, t):
            return dict(p, prompt_id=t)
        fail = _post("pytest -q", out="1 failed", failed=True)
        self.feed(turn(fail, "a"), turn(fail, "a"), turn(_post(tool="Edit", path="a.py"), "a"))
        self.assertIsNone(self.feed(turn(_pre("pytest -q"), "b")), "the person may have changed things since")
        self.assertIsNone(self.feed(turn(STOP, "b")), "no edit in this turn")

    def test_a_session_is_graded_by_its_last_check_after_its_last_edit(self):
        from agentdiff.guard import Guard, graded
        self.feed(_post(tool="Edit", path="a.py"), _post("pytest -q", out="1 failed", failed=True))
        self.assertEqual(graded(Guard(self.root, "s1").state), {"success": False, "by": "pytest -q"})
        self.feed(_post(tool="Edit", path="a.py"))
        self.assertIsNone(graded(Guard(self.root, "s1").state), "edited since: ungraded")

    def test_a_corrupt_state_file_lets_everything_through(self):
        d = self.root / ".agentdiff" / "guard"
        d.mkdir(parents=True)
        (d / "s1.json").write_text("{not json")
        self.assertIsNone(self.feed(_pre("pytest -q")))

    def test_what_counts_as_a_check_and_a_failure(self):
        for c in ("pytest -q", "python -m pytest tests/", "npm test", "go test ./...", "cargo test", "ruff check ."):
            self.assertTrue(is_check_command(c), c)
        for c in ("ls", "cat pytest.ini", "python a.py"):
            self.assertFalse(is_check_command(c), c)
        self.assertTrue(is_check_command("./run_checks.sh", check="./run_checks.sh"))
        self.assertTrue(failed_output({"stdout": "", "exit_code": 2}))
        self.assertFalse(failed_output({"stdout": "4 passed, 0 failed"}))
        self.assertTrue(failed_output("Traceback (most recent call last):"))
        self.assertIsNone(failed_output({"stdout": ""}))


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv, stdin=""):
        from agentdiff.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("sys.stdin", io.StringIO(stdin)), \
                mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}):
            code = main(list(argv))
        return code, buf.getvalue()

    def test_install_merges_with_what_is_there_and_uninstall_takes_only_ours(self):
        settings = self.root / ".claude" / "settings.local.json"
        settings.parent.mkdir()
        mine = {"type": "command", "command": "echo mine"}
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash"]},
                                        "hooks": {"PostToolUse": [{"matcher": "*", "hooks": [mine]}]}}))
        for _ in range(2):  # twice: installed once, not twice
            code, out = self.cli("guard", "--install", "--project", str(self.root), "--check", "pytest -q")
            self.assertEqual(code, 0)
        data = json.loads(settings.read_text())
        self.assertEqual(data["permissions"], {"allow": ["Bash"]})
        self.assertEqual(sorted(data["hooks"]),
                         ["PostToolUse", "PostToolUseFailure", "PreToolUse", "Stop", "UserPromptSubmit"])
        self.assertEqual(len(data["hooks"]["PostToolUse"]), 2)
        self.assertIn("--check 'pytest -q'", data["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.cli("guard", "--uninstall", "--project", str(self.root))
        data = json.loads(settings.read_text())
        self.assertEqual(data["hooks"], {"PostToolUse": [{"matcher": "*", "hooks": [mine]}]})

    def test_the_installed_command_runs_as_a_hook_and_traces_the_session(self):
        self.cli("guard", "--install", "--project", str(self.root))
        cmd = json.loads((self.root / ".claude" / "settings.local.json").read_text())["hooks"]["Stop"][0]["hooks"][0]
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        env.pop("PYTHONPATH", None)

        def hook(payload):
            r = subprocess.run(["sh", "-c", cmd["command"]], input=json.dumps(payload), capture_output=True,
                               text=True, env=env, cwd=str(self.root), timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout) if r.stdout.strip() else None

        fail = _post("pytest -q", out="1 failed", failed=True)
        hook(fail)
        hook(fail)
        self.assertEqual(hook(_pre("pytest -q"))["hookSpecificOutput"]["permissionDecision"], "deny")
        hook(_post(tool="Edit", path=str(self.root / "a.py")))
        self.assertEqual(hook(STOP)["decision"], "block")
        traces = self.root / ".agentdiff" / "traces"
        live = list(traces.glob("*.live.json"))
        self.assertEqual(len(live), 1, "a refused stop leaves the session live")
        steps = json.loads(live[0].read_text())["steps"]
        self.assertEqual([bool(s.get("error")) for s in steps], [True, True, False])
        hook(_post("pytest -q", out="===== 4 passed in 0.1s ====="))
        self.assertIsNone(hook(STOP))
        final = [p for p in traces.glob("*.json") if not p.name.endswith(".live.json")]
        self.assertEqual([p.name for p in final], [f"{self.root.name}-s1__claude-code.json"])
        t = json.loads(final[0].read_text())
        calls = [x for x in t["steps"] if x["type"] == "tool_call"]
        self.assertEqual(calls[-1]["output"], "===== 4 passed in 0.1s =====", "printed text, not JSON")
        self.assertTrue(t["outcome"]["success"])
        self.assertIn("graded by the check it ran last", t["outcome"]["note"])
        self.assertEqual(t["harness"]["graded_by"], "its last check after its last edit, `pytest -q`")

    def test_the_hook_fails_open_and_status_reads_the_log(self):
        code, out = self.cli("guard", stdin="not json")
        self.assertEqual((code, out), (0, ""))
        _, out = self.cli("guard", "--status", "--project", str(self.root))
        self.assertIn("refused nothing", out)
        fail = json.dumps(_post("pytest -q", out="1 failed", failed=True))
        for _ in range(2):
            self.cli("guard", stdin=fail)
        _, out = self.cli("guard", stdin=json.dumps(_pre("pytest -q")))
        self.assertIn('"deny"', out)
        _, out = self.cli("guard", "--status", "--project", str(self.root))
        self.assertIn("1 refusal(s) over 1 session(s): repeat 1", out)


class HubTest(unittest.TestCase):
    def test_the_overview_lists_each_refusal_linked_to_its_sessions_trace(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_hub_live import _app, _root, _sign_in
        from agentdiff.hub.app import Request
        from agentdiff.claude_code import hook_event
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            proj = root / "proj"
            proj.mkdir()
            fail = dict(_post("pytest -q", out="2 failed", failed=True), session_id="abcdef1234")
            for _ in range(2):
                decide(fail, proj)
                hook_event(fail, traces=proj / ".agentdiff" / "traces", task="proj-abcdef12", agent="claude-code")
            decide(dict(_pre("pytest -q"), session_id="abcdef1234"), proj)
            hook_event({"hook_event_name": "Stop", "session_id": "abcdef1234"},
                       traces=proj / ".agentdiff" / "traces", task="proj-abcdef12", agent="claude-code")
            app = _app(root)
            h = _sign_in(app)
            page = app.handle(Request("GET", "/", h)).body.decode()
            self.assertIn("Guards · refused while the agent worked", page)
            self.assertIn("a failing command rerun unchanged", page)
            ref = next(r for r in app.traces.refs() if r.name.startswith("proj-abcdef12"))
            self.assertIn(f'<a href="/traces/{ref.id}">trace</a>', page)
            self.assertNotIn("agentdiff guard: you", page, "the prefix is dropped on the page")


if __name__ == "__main__":
    unittest.main()
