"""What each Claude Code session is told when it starts, and the check it is held to
(agentdiff.guard: detect_check, brief, and the SessionStart hook).

The detection is scored against the checks AgentDiff's own task files name for their workspaces (recorded), and
against small project skeletons written here for each kind of project (synthetic).
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.guard import BRIEF_MAX, brief, decide, detect_check  # noqa: E402
from test_guard import STOP, _post  # noqa: E402


def _named_checks() -> list:
    """(workspace, the check its task file names), for every task file under demo/ with both."""
    out = []
    for f in [ROOT / "demo/vendors/task.json", ROOT / "demo/vendors/suite/suite.json"]:
        data = json.loads(f.read_text())
        for t in data if isinstance(data, list) else data.get("tasks", [data]):
            if t.get("check") and t.get("workspace"):
                out.append((f.parent / t["workspace"], t["check"]))
    return out


def _write(root: Path, files: dict) -> Path:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


class DetectTest(unittest.TestCase):
    def test_the_checks_the_task_files_name(self):
        rows = _named_checks()
        self.assertEqual(len(rows), 7)
        for ws, named in rows:
            got = detect_check(ws)
            self.assertIsNotNone(got, ws)
            # the task's check runs the project's tests first, then guards its own test file
            self.assertTrue(named.startswith(got["command"]), (ws, got, named))

    def test_this_project_and_a_task_whose_tests_are_held_out(self):
        self.assertEqual(detect_check(ROOT)["command"], "python3 -m unittest discover -s tests -q")
        self.assertIsNone(detect_check(ROOT / "demo/selfevolve/tasks/pricing"), "its tests are held out of the workspace")

    def test_each_kind_of_project(self):
        cases = [
            ({"package.json": json.dumps({"scripts": {"test": "jest"}})}, "npm test"),
            ({"Cargo.toml": "[package]\nname='x'\n"}, "cargo test"),
            ({"go.mod": "module x\n"}, "go test ./..."),
            ({"Makefile": "build:\n\techo\ntest:\n\tpytest\n"}, "make test"),
            ({"pytest.ini": "[pytest]\n", "test_a.py": ""}, "python3 -m pytest -q"),
            ({"conftest.py": ""}, "python3 -m pytest -q"),
            ({"pyproject.toml": "[tool.pytest.ini_options]\naddopts='-q'\n"}, "python3 -m pytest -q"),
            ({"setup.cfg": "[tool:pytest]\n"}, "python3 -m pytest -q"),
            ({"tox.ini": "[pytest]\n"}, "python3 -m pytest -q"),
            ({"tests/test_a.py": "import pytest\n"}, "python3 -m pytest -q"),
            ({"test_a.py": "import unittest\n"}, "python3 -m unittest -q"),
            ({"tests/test_a.py": "import unittest\n"}, "python3 -m unittest discover -s tests -q"),
            ({"test/unit/test_a.py": "import unittest\n"}, "python3 -m unittest discover -s test -q"),
            # the package's own test script outranks the Python files beside it
            ({"package.json": json.dumps({"scripts": {"test": "vitest"}}), "test_a.py": ""}, "npm test"),
            # npm's placeholder is no test script
            ({"package.json": json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}),
              "test_a.py": ""}, "python3 -m unittest -q"),
        ]
        for files, want in cases:
            with tempfile.TemporaryDirectory() as tmp:
                got = detect_check(_write(Path(tmp), files))
                self.assertEqual((got or {}).get("command"), want, files)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(detect_check(_write(Path(tmp), {"README.md": "hi", "app.py": ""})))
            self.assertIsNone(detect_check(_write(Path(tmp), {"package.json": "not json"})))


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, *rows):
        d = self.root / ".agentdiff" / "guard"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "log.jsonl").open("a") as f:
            for session, guard, call in rows:
                f.write(json.dumps({"t": 1, "session": session, "guard": guard, "reason": "r", "call": call}) + "\n")

    def test_nothing_to_say_says_nothing(self):
        self.assertIsNone(brief(self.root))

    def test_the_check_and_what_earlier_sessions_were_refused(self):
        self.log(("a", "repeat", "Bash:pytest -q tests/test_x.py"), ("a", "repeat", "Bash:pytest -q tests/test_x.py"),
                 ("b", "repeat", "Bash:make lint"), ("b", "check", "Stop"), ("c", "tests", "Edit:tests/test_x.py"),
                 ("now", "check", "Stop"))
        text = brief(self.root, found={"command": "python3 -m pytest -q", "from": "pytest.ini"}, session="now")
        self.assertIn("`python3 -m pytest -q` (pytest.ini names it)", text)
        self.assertIn("In 3 earlier session(s) here", text, "the current session's own refusals are not earlier ones")
        self.assertIn("rerun unchanged 3 time(s) (`pytest -q tests/test_x.py`, `make lint`)", text)
        self.assertIn("unchecked edits or a failing check 1 time(s)", text)
        self.assertIn("an edit to a test file 1 time(s)", text)
        self.assertLessEqual(len(text), BRIEF_MAX)

    def test_an_earlier_session_that_ended_failing_its_own_check(self):
        for p in (_post(tool="Edit", path=str(self.root / "a.py")), _post("pytest -q", out="2 failed", failed=True)):
            decide(dict(p, session_id="old"), self.root, enabled=("repeat", "check"))
        text = brief(self.root, session="new")
        self.assertIn("1 of the last earlier sessions here ended with their own last check failing (`pytest -q`)", text)

    def test_a_long_history_stays_short(self):
        self.log(*[(f"s{i}", "repeat", "Bash:" + "x" * 300 + str(i)) for i in range(50)])
        self.assertLessEqual(len(brief(self.root, check="pytest -q")), BRIEF_MAX)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _write(Path(self.tmp.name), {"pytest.ini": "[pytest]\n", "tests/test_a.py": ""})

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, **opts):
        return decide({"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}, self.root, **opts)

    def test_session_start_carries_the_brief(self):
        out = self.start(enabled=("repeat", "check", "brief"))
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("`python3 -m pytest -q` (pytest.ini names it)", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.start(enabled=("repeat", "check")), "no brief when it is turned off")

    def test_a_check_the_operator_named_wins(self):
        out = self.start(enabled=("brief",), check="make ci")
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("`make ci`", text)
        self.assertNotIn("pytest", text)

    def test_the_stop_names_the_projects_own_check(self):
        decide(_post(tool="Edit", path=str(self.root / "a.py")), self.root, enabled=("check",))
        out = decide(STOP, self.root, enabled=("check",))
        self.assertIn("Run `python3 -m pytest -q` (pytest.ini names it)", out["reason"])
        state = json.loads((self.root / ".agentdiff" / "guard" / "s1.json").read_text())
        self.assertEqual(state["detected"]["command"], "python3 -m pytest -q", "found once and kept with the session")

    def test_the_installed_hook_answers_session_start(self):
        from io import StringIO
        from contextlib import redirect_stdout
        from unittest import mock
        from agentdiff.cli import main
        with redirect_stdout(StringIO()), mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}):
            main(["guard", "--install", "--project", str(self.root), "--no-traces"])
        hooks = json.loads((self.root / ".claude" / "settings.local.json").read_text())["hooks"]
        cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        env.pop("PYTHONPATH", None)
        r = subprocess.run(["sh", "-c", cmd], input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s9",
                                                                "source": "startup"}),
                           capture_output=True, text=True, env=env, cwd=str(self.root), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("python3 -m pytest -q", out["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
