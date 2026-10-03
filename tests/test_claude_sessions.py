"""Your Claude Code sessions, read from where Claude Code keeps them (agentdiff.claude_sessions),
and shown by the hub: named by what you asked, every ask, sub-agents as lanes, never a failure
when nothing graded them, live while they are written."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff import claude_sessions as cs  # noqa: E402
from agentdiff.claude_code import read_transcript  # noqa: E402
from agentdiff.timeline import outcome_of  # noqa: E402

T0 = 1_780_000_000.0


def _ts(s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(T0 + s)) + f".{int((s % 1) * 1000):03d}Z"


def _user(s, text, **kw):
    return dict({"type": "user", "timestamp": _ts(s), "cwd": "/work/shop", "message": {"role": "user", "content": text}}, **kw)


def _tool(s, ident, name, inp, model="m"):
    return {"type": "assistant", "timestamp": _ts(s), "message": {"model": model, "content": [
        {"type": "tool_use", "id": ident, "name": name, "input": inp}], "usage": {"output_tokens": 30}}}


def _result(s, ident, out, err=False):
    return {"type": "user", "timestamp": _ts(s), "message": {"content": [
        {"type": "tool_result", "tool_use_id": ident, "content": out, "is_error": err}]}}


def _say(s, text):
    return {"type": "assistant", "timestamp": _ts(s), "message": {"content": [{"type": "text", "text": text}],
                                                                  "usage": {"output_tokens": 12}}}


def write_session(projects: Path, sid="5e55a0aa-1111-2222-3333-444455556666", mtime=None, sub=True):
    d = projects / "-work-shop"
    d.mkdir(parents=True, exist_ok=True)
    entries = [
        _user(0, "This session is being continued from a previous conversation. Summary: ...", isCompactSummary=True),
        _user(1, "<command-name>/clear</command-name>"),
        _user(2, "Fix the totals in ledger/report.py so tests/test_report.py passes"),
        _tool(5, "t1", "Bash", {"command": "pytest -q"}),
        _result(9, "t1", "1 failed, 4 passed", err=True),
        _tool(10, "t2", "Agent", {"description": "find callers", "prompt": "find the callers"}),
        _result(40, "t2", "two callers"),
        _tool(41, "t3", "Edit", {"file_path": "ledger/report.py", "old_string": "a", "new_string": "b"}),
        _result(42, "t3", "ok"),
        _tool(43, "t4", "Bash", {"command": "pytest -q"}),
        _result(47, "t4", "5 passed"),
        _say(48, "Fixed: all tests pass."),
        _user(300, "Now add a test for the empty ledger"),
        _tool(305, "t5", "Write", {"file_path": "tests/test_empty.py", "content": "x"}),
        _result(306, "t5", "ok"),
        _say(310, "Added the test. It passes."),
    ]
    f = d / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    if sub:
        sd = d / sid / "subagents"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "agent-abc123.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "find callers"}))
        sub_entries = [
            {"type": "user", "isSidechain": True, "timestamp": _ts(11), "message": {"content": "find the callers"}},
            _tool(12, "s1", "Grep", {"pattern": "totals"}),
            _result(13, "s1", "ledger/report.py:3"),
            _tool(20, "s2", "Read", {"file_path": "ledger/report.py"}),
            _result(21, "s2", "def totals(): ..."),
            _say(39, "two callers"),
        ]
        (sd / "agent-abc123.jsonl").write_text("\n".join(json.dumps(e) for e in sub_entries) + "\n")
    t = time.time() - 3600 if mtime is None else mtime
    for p in [f] + list((d / sid).rglob("*")) if sub else [f]:
        os.utime(p, (t, t))
    return f


class ReadTest(unittest.TestCase):
    def test_a_line_separator_inside_a_string_is_not_a_line_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = write_session(Path(tmp), sub=False)
            entries = read_transcript(f)
            self.assertEqual(len(entries), 16)
            self.assertIn(" ", entries[-1]["message"]["content"][0]["text"])


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = Path(self.tmp.name) / "projects"
        write_session(self.projects)
        self.session = cs.find(self.projects)[0]
        self.data = cs.convert(self.session)

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_by_the_first_thing_you_asked_not_the_summary_or_a_command(self):
        task = self.data["task"]
        self.assertTrue(task["id"].startswith("shop · Fix the totals in ledger"), task["id"])
        self.assertNotIn("/", task["id"])
        self.assertEqual(task["prompt"], "Fix the totals in ledger/report.py so tests/test_report.py passes")

    def test_every_ask_with_the_step_it_came_before(self):
        turns = self.data["turns"]
        self.assertEqual([t["prompt"][:7] for t in turns], ["Fix the", "Now add"])
        steps = self.data["steps"]
        self.assertEqual(steps[turns[1]["step"]]["name"], "Write")
        self.assertEqual(turns[1]["at_s"], 300.0 - 5.0)   # the clock starts at the first step

    def test_sub_agent_steps_join_at_their_times_on_their_own_lane(self):
        steps = self.data["steps"]
        self.assertEqual([s["index"] for s in steps], list(range(len(steps))))
        starts = [s["started_s"] for s in steps[:-1]]
        self.assertEqual(starts, sorted(starts))
        mine = [s for s in steps if (s.get("span") or {}).get("agent")]
        self.assertEqual([s["name"] for s in mine], ["Grep", "Read", "sub-agent answer"])
        self.assertEqual(mine[0]["span"]["agent"], "find callers")
        self.assertEqual(steps[-1]["type"], "answer")
        self.assertEqual(self.data["source"]["subagents"], 1)
        from agentdiff.timeline import timeline
        lanes = [l["agent"] for l in timeline(self.data)["lanes"]]
        self.assertIn("find callers", lanes)

    def test_never_a_failure_when_nothing_graded_it(self):
        self.assertIsNone(outcome_of(self.data))
        self.assertEqual(self.data["harness"]["graded_by"], "ungraded")
        self.assertEqual(self.data["source"]["compactions"], 1)

    def test_a_session_written_to_just_now_is_live(self):
        self.assertFalse(self.data.get("in_progress"))
        live = cs.convert(self.session, now=self.session.mtime + 10)
        self.assertTrue(live["in_progress"])
        self.assertNotEqual(live["steps"][-1]["type"], "answer")


class SyncTest(unittest.TestCase):
    def test_once_then_unchanged_then_final_when_it_goes_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects, dest = Path(tmp) / "projects", Path(tmp) / "dest"
            f = write_session(projects, mtime=time.time())
            first = cs.sync(dest, projects=projects)
            self.assertEqual((first["read"], first["live"]), (1, 1))
            live = list(dest.rglob("*.live.json"))
            self.assertEqual(len(live), 1)
            self.assertEqual(live[0].parent.parent.name, "shop")
            again = cs.sync(dest, projects=projects)
            self.assertEqual((again["read"], again["unchanged"]), (0, 1))
            quiet = time.time() - 3600
            os.utime(f, (quiet, quiet))
            for p in (f.parent / f.stem).rglob("*"):
                os.utime(p, (quiet, quiet))
            done = cs.sync(dest, projects=projects)
            self.assertEqual(done["read"], 1)
            self.assertEqual(list(dest.rglob("*.live.json")), [])
            self.assertEqual(len(list(dest.rglob("*__claude-code.json"))), 1)

    def test_an_unreadable_session_is_counted_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            projects, dest = Path(tmp) / "projects", Path(tmp) / "dest"
            write_session(projects)
            (projects / "-work-shop" / "empty.jsonl").write_text("not json\n")
            counts = cs.sync(dest, projects=projects)
            self.assertEqual((counts["found"], counts["read"], counts["empty"]), (2, 1, 1))


class HubTest(unittest.TestCase):
    def setUp(self):
        from agentdiff.harness.hub_server import build_app
        from agentdiff.hub.config import load
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        projects, self.dest = base / "projects", base / "home" / "claude-code"
        write_session(projects)
        cs.sync(self.dest, projects=projects)
        ex = base / "root" / "demo" / "traces"
        ex.mkdir(parents=True)
        src = ROOT / "demo" / "loops" / "traces"
        for p in src.glob("*.json"):
            (ex / p.name).write_text(p.read_text())
        (ex / "EXAMPLE").write_text("synthetic: three agents on one task\n")
        self.app = build_app(load(str(base / "root")))
        self.app.traces.add_root(self.dest)
        self.session = self.app.sessions.create("demo")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_index_serves_an_added_root_and_labels_the_examples(self):
        refs = self.app.traces.refs()
        mine = [r for r in refs if r.summary.get("session")]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].summary["session"]["asks"], 2)
        self.assertTrue(mine[0].summary["ungraded"])
        self.assertIsNone(mine[0].summary["success"])
        self.assertTrue(mine[0].group.startswith("claude-code/"))
        examples = [r for r in refs if r.summary.get("example")]
        self.assertEqual(len(examples), 3)
        self.assertTrue(all(r.summary["example"].startswith("synthetic") for r in examples))

    def test_the_overview_leads_with_your_sessions_and_counts_them_honestly(self):
        from agentdiff.hub import views
        refs = self.app.traces.refs()
        html = views.overview_page(brand="AgentDiff", user="demo", csrf="x", entries=[], refs=refs, ingest={"url": "u", "token": "t"})
        self.assertIn("Your Claude Code sessions", html)
        self.assertIn("not the 3 bundled example(s)", html)
        self.assertIn("not graded", html)
        self.assertNotIn("✗</span>failed", html.split("Your Claude Code sessions")[1].split("</table>")[0])

    def test_a_session_page_says_what_you_asked_and_opens_a_step_from_there(self):
        from agentdiff.hub import views
        rows = views.asks_rows(cs.convert(cs.find(Path(self.tmp.name) / "projects")[0]))
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["checks"], rows[0]["failed"], rows[0]["edits"]), (2, 1, 1))
        self.assertEqual((rows[1]["edits"], rows[1]["checks"]), (1, 0))
        ref = next(r for r in self.app.traces.refs() if r.summary.get("session"))
        data = self.app.traces.load(ref)
        clock = self.app._clock(_req(f"/traces/{ref.id}?at=3"), ref, data)
        self.assertEqual(clock["at"], 3)
        from agentdiff.laps import laps
        html = views.trace_panel(ref=ref, data=data, lap=laps(data), **clock)
        self.assertIn("What you asked", html)
        self.assertIn(f"/traces/{ref.id}?at=", html)
        self.assertIn("nothing graded it", html)
        self.assertIn("Give it a check", html)
        self.assertNotIn("what it got wrong", html, "an ungraded session is not said to have got anything wrong")


class CommandTest(unittest.TestCase):
    def test_hub_claude_code_reads_your_sessions_and_says_so(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write_session(base / "claude" / "projects")
            env = dict(os.environ, CLAUDE_CONFIG_DIR=str(base / "claude"), AGENTDIFF_HOME=str(base / "home"),
                       PYTHONPATH=str(ROOT))
            proc = subprocess.Popen([sys.executable, "-m", "agentdiff", "hub", "--claude-code", "--port", "0"],
                                    cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = []
            try:
                deadline = time.time() + 60
                while time.time() < deadline:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    lines.append(line)
                    if "Ctrl-C" in line:
                        break
            finally:
                proc.terminate()
                proc.wait(timeout=20)
            out = "".join(lines)
            self.assertIn("yours: 1 Claude Code session(s)", out)
            self.assertIn(f"(root {base / 'home'})", out)
            self.assertEqual(len(list((base / "home" / "claude-code").rglob("*__claude-code.json"))), 1)
            self.assertFalse((Path(tmp) / "claude-code").exists(), "nothing written where it was started")


class SecondStartTest(unittest.TestCase):
    """A double-click on a hub already running opens it; a port someone else holds is swapped for a free one."""

    def _start(self, root: Path, *extra):
        import subprocess
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        env.pop("AGENTDIFF_HUB_PORT", None)
        return subprocess.Popen([sys.executable, "-m", "agentdiff", "hub", str(root), *extra], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    @staticmethod
    def _free_port() -> int:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _until(self, proc, word, most=60):
        lines, deadline = [], time.time() + most
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            if word in line:
                break
        return "".join(lines)

    def test_a_second_start_opens_the_first_and_a_held_port_is_swapped(self):
        import socket
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            port = self._free_port()
            (root / ".agentdiff-hub").mkdir()
            (root / ".agentdiff-hub" / "hub.json").write_text(json.dumps({"port": port}))
            first = self._start(root)
            try:
                self.assertIn(f"127.0.0.1:{port}/", self._until(first, "Ctrl-C"))
                second = self._start(root)
                out = second.communicate(timeout=60)[0]
                self.assertEqual(second.returncode, 0, out)
                self.assertIn(f"AgentDiff is already running at http://127.0.0.1:{port}/", out)
            finally:
                first.terminate()
                first.wait(timeout=20)
            port = self._free_port()            # a fresh one: the first is in TIME_WAIT a while
            (root / ".agentdiff-hub" / "hub.json").write_text(json.dumps({"port": port}))
            held = socket.socket()
            held.bind(("127.0.0.1", port))
            held.listen(1)
            third = self._start(root)
            try:
                out = self._until(third, "Ctrl-C")
                self.assertIn(f"port {port} is in use; listening on", out)
            finally:
                third.terminate()
                third.wait(timeout=20)
                held.close()


def _req(path):
    from agentdiff.hub.app import Request
    return Request("GET", path)


if __name__ == "__main__":
    unittest.main()
