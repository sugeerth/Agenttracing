"""What a session changed, read from its own edit calls (insight.code_from_steps), and what keeps happening
across sessions (hub.traces.digest, the Overview's tables): counted from the steps, said where it comes from."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff import claude_sessions as cs  # noqa: E402
from agentdiff.hub.traces import _error_line, digest  # noqa: E402
from agentdiff.insight import code_from_steps  # noqa: E402
from agentdiff.timeline import timeline  # noqa: E402
from test_claude_sessions import write_session  # noqa: E402


def _step(i, name, inp, out="", err=False, t=None):
    return {"index": i, "type": "tool_call", "name": name, "input": json.dumps(inp), "output": out,
            "started_s": float(i if t is None else t), "latency_s": 1.0, "error": err}


def _run(steps, cwd="/work/shop"):
    return {"schema_version": 1, "trace_id": "t__a", "task": {"id": "t"}, "agent": {"name": "a"},
            "steps": steps, "outcome": {"success": False, "note": "ungraded: nothing graded it"},
            "source": {"format": "claude-code-session", "cwd": cwd}}


class CodeFromStepsTest(unittest.TestCase):
    def test_edits_writes_and_what_it_cannot_read(self):
        steps = [
            _step(0, "Write", {"file_path": "/work/shop/ledger/new.py", "content": "a\nb\nc\n"}, "File created successfully at: x"),
            _step(1, "Edit", {"file_path": "/work/shop/ledger/new.py", "old_string": "b", "new_string": "B\nB2"}, "updated"),
            _step(2, "Edit", {"file_path": "/work/shop/tests/test_old.py", "old_string": "x == 1", "new_string": "x == 2"}, "ok"),
            _step(3, "Edit", {"file_path": "/work/shop/a.py", "old_string": "nope", "new_string": "z"}, "not found", err=True),
            _step(4, "MultiEdit", {"file_path": "/work/shop/a.py", "edits": [{"old_string": "1", "new_string": "2"},
                                                                         {"old_string": "", "new_string": "3"}]}, "ok"),
            _step(5, "Bash", {"command": "sed -i 's/a/b/' a.py"}, ""),
            _step(6, "Bash", {"command": "python -m pytest -q"}, "1 failed, 2 passed", err=True),
        ]
        c = code_from_steps(_run(steps))
        by = {f["path"]: f for f in c["files"]}
        self.assertEqual(sorted(by), ["a.py", "ledger/new.py", "tests/test_old.py"], "paths read under the project")
        self.assertEqual((by["ledger/new.py"]["status"], by["ledger/new.py"]["edits"]), ("created", 2))
        self.assertEqual((by["ledger/new.py"]["added"], by["ledger/new.py"]["removed"]), (3 + 2, 1))
        self.assertEqual((by["a.py"]["added"], by["a.py"]["removed"]), (2, 1))
        self.assertEqual((c["failed_edits"], c["shell_edits"]), (1, 1))
        self.assertEqual(c["source"], "steps")
        self.assertEqual(c["tests"], ["tests/test_old.py"], "an existing test changed is flagged")
        self.assertTrue(any(f["kind"] == "tests_edited" for f in c["flags"]))
        self.assertIs(c["passed"], False)
        self.assertEqual(c["check_step"], 6)
        self.assertIn("--- ledger/new.py  (step 0, Write)", c["patch"])
        self.assertIn("+B2", c["patch"])

    def test_a_new_test_file_is_work_not_a_warning(self):
        c = code_from_steps(_run([_step(0, "Write", {"file_path": "/work/shop/tests/test_new.py", "content": "x"},
                                        "File created successfully")]))
        self.assertEqual(c["tests"], [])
        self.assertFalse(any(f["kind"] == "tests_edited" for f in c["flags"]))

    def test_no_edit_call_is_no_change(self):
        self.assertIsNone(code_from_steps(_run([_step(0, "Read", {"file_path": "a.py"}, "x")])))


class DigestTest(unittest.TestCase):
    def test_failing_commands_are_named_without_their_plumbing(self):
        steps = [
            _step(0, "Bash", {"command": "S=/tmp/x && cd /w && python -m pytest tests/a.py -q 2>&1 | tail -3"},
                  "....F\nFAILED tests/a.py::t - assert 1\n1 failed, 3 passed", err=True),
            _step(1, "Bash", {"command": "python -m pytest tests/a.py -q"}, "E   KeyError: 'x'\n1 failed", err=True),
            _step(2, "Bash", {"command": "python3 - <<'EOF'\nimport unittest\nraise SystemExit(1)\nEOF"},
                  "Traceback (most recent call last):\n  File x\nValueError: bad", err=True),
            _step(3, "WebFetch", {"url": "https://x"}, "blocked", err=True),
        ]
        data = _run(steps)
        dg = digest(data, timeline(data), code_from_steps(data))
        self.assertEqual(dg["fails"]["Bash: python -m pytest tests/a.py -q"]["n"], 2)
        self.assertIn("Bash: python3 - <<EOF", dg["fails"])
        self.assertIn("WebFetch", dg["fails"])
        self.assertEqual(dg["fails"]["Bash: python -m pytest tests/a.py -q"]["step"], 1)
        self.assertEqual(dg["fails"]["Bash: python3 - <<EOF"]["line"], "ValueError: bad")

    def test_the_line_that_says_what_went_wrong(self):
        self.assertEqual(_error_line("ok\nFAILED tests/a.py::b - x\n1 failed"), "FAILED tests/a.py::b - x")
        self.assertEqual(_error_line("Traceback (most recent call last):\n  File y\nKeyError: 'k'"), "KeyError: 'k'")
        self.assertEqual(_error_line("just this"), "just this")


class OverviewAcrossTest(unittest.TestCase):
    def test_the_overview_adds_up_what_keeps_happening(self):
        from agentdiff.harness.hub_server import build_app
        from agentdiff.hub import views
        from agentdiff.hub.config import load
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_session(base / "projects", sid="aaaa0001-1111-2222-3333-444455556666")
            write_session(base / "projects", sid="aaaa0002-1111-2222-3333-444455556666")
            cs.sync(base / "home", projects=base / "projects")
            app = build_app(load(str(base / "home")))
            html = views.overview_page(brand="AgentDiff", user="demo", csrf="x", entries=[], refs=app.traces.refs(),
                                       ingest={"url": "u", "token": "t"})
            self.assertIn("What keeps failing", html)
            fails = html.split("What keeps failing")[1].split("</table>")[0]
            self.assertIn(">Bash: pytest -q</td>", fails)
            self.assertIn('<td class="n">2</td><td class="n">2</td>', fails, "failed twice, in two sessions")
            self.assertIn("The files your agents change most", html)
            self.assertIn("ledger/report.py", html)
            self.assertIn("Where the working time goes", html)


if __name__ == "__main__":
    unittest.main()
