"""A self-evolve you start, watch and keep from the hub: the Evolve page's Start and Stop (agentdiff.hub.jobs),
progress.json and each generation as it lands, the running card, and adopting the harness it ended with into a
project's Claude Code (agentdiff.adopt). The agent is the Claude Code stand-in throughout: no real agent runs."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.adopt import adopt, unadopt  # noqa: E402
from agentdiff.hub.evolvecards import live_strip  # noqa: E402
from agentdiff.selfevolve import self_evolve  # noqa: E402
from test_selfevolve import _traj  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "vendors" / "fake_claude.py"
HARNESS = {"version": 2, "instructions": ["Run the check after your last edit."], "deny_tools": ["WebFetch"],
           "max_turns": None}


def _hub(tmp: Path, *, loopback: bool = True):
    from agentdiff.harness.hub_server import build_app
    from agentdiff.hub.app import Request
    from agentdiff.hub.config import load
    from agentdiff.hub.urls import quote
    app = build_app(load(str(tmp / "root"), env={}, overrides={"demo": True, "port": 0, "password_iterations": 1000}))
    app.loopback = loopback
    app.evolve_home = tmp / "self-evolve"
    app.catalog.add_root(app.evolve_home)
    page = app.handle(Request("GET", "/login")).body.decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    user, password = app.demo_account()
    resp = app.handle(Request("POST", "/login", body=f"csrf={quote(token)}&user={user}&password={password}".encode()))
    cookie = {"Cookie": resp.headers["Set-Cookie"].split(";")[0]}

    def get(path):
        return app.handle(Request("GET", path, headers=cookie))

    def post(path, **form):
        html = get("/evolve").body.decode()
        csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
        body = "&".join(f"{k}={quote(str(v))}" for k, v in {"csrf": csrf, **form}.items())
        return app.handle(Request("POST", path, body=body.encode(), headers=cookie))
    return app, get, post


def _claude_on_path(tmp: Path) -> dict:
    """`claude` on PATH is the stand-in, for the hub's own check and for the agents it starts."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").symlink_to(FAKE)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "AGENTDIFF_CLAUDE_BIN": str(FAKE)}


def _wait(cond, seconds=120.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


@unittest.skipIf(os.name == "nt", "the stand-in is started by its shebang")
class StartFromTheHubTest(unittest.TestCase):
    def test_start_watch_it_run_and_see_it_land(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "root").mkdir()
            env = dict(_claude_on_path(tmp), FAKE_VENDOR_MODE="careless", AGENTDIFF_HOME=str(tmp / "home"))
            with mock.patch.dict(os.environ, env):
                app, get, post = _hub(tmp)
                page = get("/evolve").body.decode()
                self.assertIn('action="/evolve/start"', page, "a hub on this machine with claude has a Start button")
                resp = post("/evolve/start", mode="demo", agent="haiku", generations=1, runs=1)
                self.assertEqual(resp.status, 303)
                out = tmp / "self-evolve" / "demo-haiku"
                self.assertTrue(_wait(lambda: (out / "progress.json").is_file()), "it says where it is")
                running = get("/evolve").body.decode()
                self.assertIn("demo-haiku · self-evolving harness", running)
                self.assertTrue(_wait(lambda: not app.jobs.running()), "the stand-in finishes")
                prog = json.loads((out / "progress.json").read_text())
                self.assertEqual(prog["status"], "done", (out / "hub-job.log").read_text())
                self.assertEqual(prog["runs_done"], 6, "six tasks, one run each")
                self.assertTrue((out / "self-evolve.json").is_file())
                done = get("/evolve").body.decode()
                self.assertIn("✓ ended", done)
                self.assertNotIn('http-equiv="refresh"', done, "nothing running: the page stays still")
                self.assertIn("demo: six tasks", done, "the end of what it printed")

    def test_stop_ends_it_and_the_agents_it_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "root").mkdir()
            env = dict(_claude_on_path(tmp), FAKE_VENDOR_SLOW="3", AGENTDIFF_HOME=str(tmp / "home"))
            with mock.patch.dict(os.environ, env):
                app, get, post = _hub(tmp)
                post("/evolve/start", mode="demo", agent="haiku", generations=2, runs=1)
                out = tmp / "self-evolve" / "demo-haiku"
                self.assertTrue(_wait(lambda: (out / "progress.json").is_file()))
                page = get("/evolve").body.decode()
                self.assertIn('http-equiv="refresh"', page, "running: the page refreshes itself, with no script")
                self.assertIn("● running", page)
                again = post("/evolve/start", mode="demo", agent="haiku", generations=1, runs=1)
                self.assertEqual(again.status, 409, "one at a time")
                self.assertEqual(post("/evolve/stop").status, 303)
                self.assertTrue(_wait(lambda: not app.jobs.running(), 60))
                self.assertTrue(_wait(lambda: json.loads((out / "progress.json").read_text())["status"] == "stopped",
                                      30), (out / "hub-job.log").read_text())
                self.assertIn("■ stopped", get("/evolve").body.decode())


class RefusedTest(unittest.TestCase):
    def test_off_this_machine_or_without_the_form_nothing_starts(self):
        from agentdiff.hub.app import Request
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "root").mkdir()
            with mock.patch.dict(os.environ, _claude_on_path(tmp)):
                app, get, post = _hub(tmp, loopback=False)
                self.assertNotIn('action="/evolve/start"', get("/evolve").body.decode())
                self.assertEqual(post("/evolve/start", mode="demo").status, 403)
                app.loopback = True
                forged = app.handle(Request("POST", "/evolve/start", body=b"csrf=nope&mode=demo"))
                self.assertIn(forged.status, (302, 303, 401, 403), "no session, no CSRF token: refused")
                self.assertFalse(app.jobs.running())
                bad = post("/evolve/start", mode="project", project="relative/path")
                self.assertEqual(bad.status, 400)


class AdoptTest(unittest.TestCase):
    def test_adopt_writes_where_claude_code_reads_and_unadopt_takes_out_only_that(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "CLAUDE.md").write_text("# Shop\n\nUse tabs.\n")
            (proj / ".claude").mkdir()
            (proj / ".claude" / "settings.local.json").write_text(json.dumps(
                {"permissions": {"deny": ["Bash(rm:*)"]}, "hooks": {"Stop": []}}))
            got = adopt(HARNESS, proj, source="evo/")
            md = (proj / "CLAUDE.md").read_text()
            self.assertTrue(md.startswith("# Shop\n\nUse tabs.\n"), "what a person wrote stays first")
            self.assertIn("- Run the check after your last edit.", md)
            settings = json.loads((proj / ".claude" / "settings.local.json").read_text())
            self.assertEqual(settings["permissions"]["deny"], ["Bash(rm:*)", "WebFetch"])
            self.assertEqual(got["deny_added"], ["WebFetch"])
            adopt(HARNESS, proj, source="evo/")  # again: replaced, not stacked
            self.assertEqual((proj / "CLAUDE.md").read_text().count("From a self-evolving harness"), 1)
            unadopt(proj)
            self.assertEqual((proj / "CLAUDE.md").read_text(), "# Shop\n\nUse tabs.\n")
            settings = json.loads((proj / ".claude" / "settings.local.json").read_text())
            self.assertEqual(settings, {"permissions": {"deny": ["Bash(rm:*)"]}, "hooks": {"Stop": []}})
            self.assertIsNone(unadopt(proj), "nothing left to take out")

    def test_a_file_adopt_made_goes_when_it_does_and_a_bare_harness_is_nothing_to_adopt(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            adopt(HARNESS, proj)
            self.assertTrue((proj / "CLAUDE.md").is_file())
            unadopt(proj)
            self.assertFalse((proj / "CLAUDE.md").exists())
            self.assertTrue(adopt({"version": 0, "instructions": [], "deny_tools": []}, proj)["nothing"])

    def test_the_command_and_the_hub_adopt_the_same_way(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            evo, proj = tmp / "self-evolve" / "run", tmp / "proj"
            evo.mkdir(parents=True)
            proj.mkdir()
            src = ROOT / "demo" / "selfevolve" / "examples" / "stand-in-learns-to-check"
            for f in ("self-evolve.json", "harness.json"):
                (evo / f).write_text((src / f).read_text())
            done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--adopt", str(evo), "--into",
                                   str(proj)], capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("--append-system-prompt", done.stdout, "it says the channel differs from the test's")
            self.assertIn("Before you say the work is done", (proj / "CLAUDE.md").read_text())
            subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--unadopt", "--into", str(proj)],
                           check=True, capture_output=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
            self.assertFalse((proj / "CLAUDE.md").exists())
            (tmp / "root").mkdir()
            app, get, post = _hub(tmp)
            page = get("/evolve").body.decode()
            run_id = re.search(r'action="/evolve/adopt/([0-9a-f]{12})"', page).group(1)
            self.assertEqual(post(f"/evolve/adopt/{run_id}", project=str(proj)).status, 303)
            self.assertIn("Before you say the work is done", (proj / "CLAUDE.md").read_text())
            said = get("/evolve").body.decode()
            self.assertIn(f"adopted into {proj}", said, "the next page says what it wrote")
            self.assertNotIn(f"adopted into {proj}", get("/evolve").body.decode(), "once")


class LiveCardTest(unittest.TestCase):
    def _entry(self, **progress):
        base = {"kind": "self-evolve-progress", "status": "running", "pid": os.getpid(), "started_at": 1000.0,
                "updated_at": 1290.0, "generations": 3, "per_arm": 12, "generation": "g1", "arm": "g1-h1",
                "runs_done": 4, "testing": "Run the check.", "lines": ["g1: testing"], "end": None}
        return SimpleNamespace(id="a" * 12, title="t", summary={"progress": dict(base, **progress)})

    def test_where_it_is_while_it_runs(self):
        out = live_strip(self._entry(), now=1300.0)
        self.assertIn("● running", out)
        self.assertIn("testing a change: <q>Run the check.</q>", out)
        self.assertIn("4 of 12 runs of g1-h1", out)
        self.assertIn("generation 2 of 3", out)
        self.assertIn("started 5 min ago · last word 10 s ago", out)

    def test_how_it_ended_when_it_did_not_finish(self):
        self.assertIn("■ stopped", live_strip(self._entry(status="stopped"), now=1300.0))
        self.assertEqual(live_strip(self._entry(status="done")), "", "a finished run is its lineage")

    def test_a_process_that_is_gone_is_not_running(self):
        from agentdiff.hub.catalog import progress_of
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "progress.json").write_text(json.dumps({"kind": "self-evolve-progress", "status": "running",
                                                          "pid": 2 ** 22 + 12345, "updated_at": 0}))
            self.assertEqual(progress_of(d)["status"], "ended")


class EachGenerationTest(unittest.TestCase):
    def test_each_generation_that_ends_is_handed_over_while_the_run_goes_on(self):
        def arm(harness, label):
            told = bool(harness.instructions)
            return [_traj(t, told, checked=told, run=f"{label}-{i}") for t in ("t1", "t2", "t3") for i in (1, 2)]
        seen = []
        result = self_evolve(arm, generations=3, on_generation=seen.append)
        self.assertEqual(len(seen), 1, "g0 ended and g1 was still to come")
        self.assertTrue(seen[0]["running"])
        self.assertEqual(len(seen[0]["lineage"]), 1)
        self.assertIn("ledger", seen[0], "what a stopped run continues from")
        self.assertNotIn("running", result)
        self.assertIn("converged at g1", result["stop"])


if __name__ == "__main__":
    unittest.main()
