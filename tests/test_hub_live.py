"""The hub's sections and its live half: traces, evals, the account, the event stream, streaming in.

Like ``test_hub``, almost everything goes through ``App.handle``; one test
runs the real server for what only the wire shows (the stream, the
script's own content policy).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.hub.app import App, Request
from agentdiff.hub.auth import MemoryUserStore, User, hash_password, sync_demo
from agentdiff.hub.catalog import Catalog
from agentdiff.hub.config import HubConfig, load
from agentdiff.hub.ingest import TelemetryStore
from agentdiff.hub.live import LiveBus, sse
from agentdiff.hub.sessions import LoginThrottle, SessionStore
from agentdiff.hub.traces import TraceIndex, trace_id
from agentdiff.hub.urls import quote

ROOT = Path(__file__).resolve().parent.parent
FAST = 1_000


def _root(tmp: Path) -> Path:
    root = tmp / "root"
    shutil.copytree(ROOT / "demo" / "loops" / "traces", root / "loops" / "traces")
    evals = root / "rl-evals"
    evals.mkdir()
    (evals / "evolve-evals.json").write_text(json.dumps({
        "target": "failure", "says": "the run failed", "patience": 2, "fpr_max": 0.1, "narrative": "n",
        "lineage": [{"generation": "g0", "corpus": "c", "runs": 4, "wrong": 2, "right": 2, "arrived": 0,
                     "forward": {"caught": 0, "coverage": None, "false_alarms": 0, "fpr": None},
                     "verdicts": {}, "retired": [], "born": ["e1"], "reborn": [],
                     "forge": {"measurable": True, "reason": None, "tried": 3}, "leaving": {}}],
        "evals": [{"id": "e1", "rule": "x", "says": "it never ran the tests", "status": "active", "born": "g0",
                   "history": [{"generation": "g0", "verdict": "born", "caught": 2, "wrong": 2,
                                "false_alarms": 0, "right": 2}]}],
        "active": ["e1"], "retired": []}))
    return root


def _app(root: Path) -> App:
    config = HubConfig(root=str(root), password_iterations=FAST, demo=True)
    users = MemoryUserStore()
    sync_demo(users, True, config.demo_user, config.demo_password, FAST)
    users.put(User(name="alice", password_hash=hash_password("first-password", FAST)))
    telemetry = TelemetryStore(root / ".agentdiff-hub" / "telemetry")
    return App(config, users=users, sessions=SessionStore(3600), throttle=LoginThrottle(5, 60),
               catalog=Catalog(root, depth=3, limit=100, extra=telemetry.entries), telemetry=telemetry,
               ingest_token="ingest-secret-token", loopback=True, public_url="http://127.0.0.1:8790")


def _form(**kw) -> bytes:
    return "&".join(f"{k}={quote(v)}" for k, v in kw.items()).encode()


def _sign_in(app: App, user="demo", password="demo") -> dict:
    page = app.handle(Request("GET", "/login")).body.decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = app.handle(Request("POST", "/login", body=_form(csrf=token, user=user, password=password, next="/")))
    assert r.status == 303, r.body
    return {"Cookie": r.headers["Set-Cookie"].split(";")[0]}


def _csrf(app: App, h: dict) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', app.handle(Request("GET", "/account", h)).body.decode()).group(1)


class SectionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _root(Path(self.tmp.name))
        self.app = _app(self.root)
        self.h = _sign_in(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, path, h=None):
        return self.app.handle(Request("GET", path, self.h if h is None else h))

    def test_every_section_wants_a_session_and_renders_with_one(self):
        for path in ("/", "/runs", "/traces", "/live", "/evals", "/account", "/api/v1/traces", "/api/v1/events"):
            with self.subTest(path=path):
                self.assertIn(self.get(path, {}).status, (303, 401))
                if path != "/api/v1/events":
                    self.assertEqual(self.get(path).status, 200)
        self.assertEqual(self.app.handle(Request("GET", "/static/live.js")).status, 200, "a static file needs no session")

    def test_the_trace_page_draws_the_loop_and_says_it_is_stuck(self):
        tid = trace_id("loops/traces/parse_duration__agent-stuck.json")
        r = self.get(f"/traces/{tid}")
        body = r.body.decode()
        self.assertEqual(r.status, 200)
        self.assertFalse(r.scripted, "a finished trace's page runs no script")
        for want in ("lap 5", "same as lap 4", "stuck: a block of 2 step(s)", "<svg", "pytest -q"):
            self.assertIn(want, body)
        self.assertEqual(self.get(f"/traces/{tid}/panel").status, 200)
        self.assertEqual(json.loads(self.get(f"/api/v1/traces/{tid}").body)["steps"][1]["name"], "Read")
        self.assertEqual(self.get("/traces/000000000000").status, 404)

    def test_the_index_filters_and_a_live_trace_keeps_its_id_when_it_finishes(self):
        stuck = self.get("/traces?show=stuck").body.decode()
        self.assertIn("agent-stuck", stuck)
        self.assertNotIn("agent-converges", stuck)
        self.assertIn("agent-converges", self.get("/traces?q=converges").body.decode())
        live = self.root / "loops" / "traces" / "t__a.live.json"
        live.write_text(json.dumps({"in_progress": True, "steps": [{"type": "tool_call", "name": "Read"}],
                                    "task": {"id": "t"}, "agent": {"name": "a"}}))
        tid = trace_id("loops/traces/t__a.live.json")
        r = self.get(f"/traces/{tid}")
        self.assertTrue(r.scripted, "a running trace's page is live")
        self.assertIn("so far", r.body.decode(), "drawn, never judged, while it runs")
        self.assertNotIn("stuck:", r.body.decode())
        self.assertIn(f"/traces/{tid}", self.get("/live/panel").body.decode())
        (self.root / "loops" / "traces" / "t__a.json").write_text(json.dumps(
            {"steps": [{"type": "tool_call", "name": "Read"}], "task": {"id": "t"}, "agent": {"name": "a"},
             "outcome": {"success": True}}))
        r = self.get(f"/traces/{tid}")
        self.assertFalse(r.scripted)
        self.assertIn("passed", r.body.decode())
        self.assertEqual(trace_id("loops/traces/t__a.json"), tid)

    def test_what_a_trace_holds_is_escaped_on_every_page(self):
        (self.root / "loops" / "traces" / "x__y.json").write_text(json.dumps(
            {"steps": [{"type": "tool_call", "name": "<script>alert(1)</script>", "input": "<img src=x onerror=1>",
                        "output": "</td><script>"}], "task": {"id": "<b>t</b>"}, "agent": {"name": "a"}}))
        tid = trace_id("loops/traces/x__y.json")
        for path in ("/traces", f"/traces/{tid}", "/", "/live"):
            body = self.get(path).body.decode()
            self.assertNotIn("<script>alert", body, path)
            self.assertNotIn("<img src=x", body, path)
            self.assertNotIn("<b>t</b>", body, path)

    def test_the_evals_section_draws_each_suite(self):
        body = self.get("/evals").body.decode()
        self.assertIn("rl-evals", body)
        self.assertIn("<svg", body)
        eid = next(x.id for x in self.app.catalog.entries() if x.kind == "evals")
        page = self.get(f"/runs/{eid}").body.decode()
        self.assertIn("it never ran the tests", page)

    def test_a_password_changes_only_with_the_current_one_and_ends_other_sessions(self):
        a1 = _sign_in(self.app, "alice", "first-password")
        a2 = _sign_in(self.app, "alice", "first-password")

        def change(h, **kw):
            return self.app.handle(Request("POST", "/account/password", h, _form(csrf=_csrf(self.app, h), **kw)))
        self.assertEqual(change(a1, current="wrong-password", new="second-password", again="second-password").status, 400)
        self.assertEqual(change(a1, current="first-password", new="short", again="short").status, 400)
        self.assertEqual(change(a1, current="first-password", new="second-password", again="other-password").status, 400)
        forged = self.app.handle(Request("POST", "/account/password", a1,
                                         _form(csrf="forged", current="first-password", new="x" * 9, again="x" * 9)))
        self.assertEqual(forged.status, 403)
        ok = change(a1, current="first-password", new="second-password", again="second-password")
        self.assertEqual(ok.status, 200)
        self.assertIn("Password changed", ok.body.decode())
        fresh = {"Cookie": ok.headers["Set-Cookie"].split(";")[0]}
        self.assertEqual(self.get("/account", a2).status, 303, "the other session ended")
        self.assertEqual(self.get("/account", a1).status, 303, "this session's old token too")
        self.assertEqual(self.get("/account", fresh).status, 200)
        self.assertEqual(_sign_in(self.app, "alice", "second-password")["Cookie"][:5], "agent")
        demo = change(self.h, current="demo", new="another-pass", again="another-pass")
        self.assertEqual(demo.status, 400, "the demo's password comes from the settings")

    def test_a_vector_posted_live_shows_as_running_then_finished(self):
        from agentdiff.telemetry import Probe
        p = Probe(agent="remote", task="far")
        with p.hop("Read", kind="tool_call"):
            pass
        auth = {"Authorization": "Bearer ingest-secret-token"}
        since = self.app.bus.version
        r = self.app.handle(Request("POST", "/api/v1/telemetry", auth, json.dumps({"vector": p.text(), "live": True}).encode()))
        self.assertEqual(r.status, 201, r.body)
        self.assertTrue(self.app.bus.since(since), "the bus heard of it")
        live = [t for t in json.loads(self.get("/api/v1/traces").body)["traces"] if t["agent"] == "remote"]
        self.assertEqual([t["live"] for t in live], [True])
        with p.hop("Bash", kind="tool_call"):
            pass
        self.app.handle(Request("POST", "/api/v1/telemetry", auth, json.dumps({"vector": p.text(), "success": True}).encode()))
        done = [t for t in json.loads(self.get("/api/v1/traces").body)["traces"] if t["agent"] == "remote"]
        self.assertEqual([(t["live"], t["success"]) for t in done], [(False, True)])
        bad = self.app.handle(Request("POST", "/api/v1/telemetry", auth, json.dumps({"vector": p.text(), "live": "yes"}).encode()))
        self.assertEqual(bad.status, 400)


class EvolveSectionTest(unittest.TestCase):
    def test_an_evolving_harness_is_drawn_with_its_changes_and_its_evals(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_selfevolve import TASKS, _traj
        from agentdiff.selfevolve import self_evolve

        def arm(h, label):
            told = any("check" in i.lower() for i in h.instructions)
            return [_traj(t, told or t in ("t5", "t6"), checked=told or t in ("t5", "t6"), run=f"{label}-{r}")
                    for t in TASKS for r in ("r1", "r2")]
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            out = root / "evo"
            out.mkdir()
            result = self_evolve(arm, generations=3, check="pytest -q")
            (out / "self-evolve.json").write_text(json.dumps({k: v for k, v in result.items() if k != "ledger"}))
            app = _app(root)
            h = _sign_in(app)
            page = app.handle(Request("GET", "/evolve", h)).body.decode()
            self.assertIn("✓ kept", page)
            self.assertIn("claims_without_check", page)
            eid = next(x.id for x in app.catalog.entries() if x.kind == "evolution")
            one = app.handle(Request("GET", f"/runs/{eid}", h)).body.decode()
            for want in ("The harness now", "Before you say the work is done", "4/12 → 12/12", "candidate(s) weighed"):
                self.assertIn(want, one)
            self.assertIn("Agents evolving", app.handle(Request("GET", "/", h)).body.decode())
            self.assertIn("evo", app.handle(Request("GET", "/evals", h)).body.decode(), "its evals are listed too")


class BusTest(unittest.TestCase):
    def test_the_stream_sends_what_changed_and_resumes_from_the_last_id(self):
        bus = LiveBus()
        bus.publish("trace", "aaa")
        bus.publish("trace", "bbb")
        frames = sse(bus, 1, keepalive_s=0.05, max_s=0.2)
        self.assertEqual(next(frames), b"retry: 2000\n\n")
        missed = next(frames).decode()
        self.assertIn("id: 2", missed)
        self.assertIn('"bbb"', missed)
        self.assertNotIn('"aaa"', missed, "what the last id already saw is not sent again")
        self.assertEqual(next(frames), b": keepalive\n\n")
        threading.Timer(0.02, lambda: bus.publish("telemetry", "ccc")).start()
        nxt = next(frames)
        while nxt.startswith(b":"):
            nxt = next(frames)
        self.assertIn(b'"ccc"', nxt)
        self.assertNotIn(b"steps", nxt, "an event names what changed, never its content")

    def test_a_closed_bus_ends_every_stream(self):
        bus = LiveBus()
        frames = sse(bus, 0, keepalive_s=5)
        next(frames)
        threading.Timer(0.05, bus.close).start()
        self.assertEqual(list(frames), [])

    def test_the_poller_names_the_traces_that_moved(self):
        from agentdiff.harness.hub_server import TracePoller
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "traces"
            d.mkdir()
            index = TraceIndex(Path(tmp), depth=2, limit=100)
            bus = LiveBus()
            poller = TracePoller(index, bus)
            self.assertEqual(poller.tick(), [])
            (d / "a__b.live.json").write_text(json.dumps({"steps": []}))
            index.RESCAN_S = 0
            self.assertEqual(poller.tick(), [trace_id("traces/a__b.live.json")])
            self.assertEqual(poller.tick(), [])


class StreamerTest(unittest.TestCase):
    def test_a_directory_streams_as_one_run_that_grows_then_finishes(self):
        from agentdiff.harness.hub_server import TraceStreamer
        from agentdiff.telemetry import read
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            app = _app(root)
            posts = []

            def poster(url, token, text, **kw):
                posts.append((read(text).trace_id, kw.get("live")))
                r = app.handle(Request("POST", "/api/v1/telemetry", {"Authorization": f"Bearer {token}"},
                                       json.dumps({"vector": text, "live": kw.get("live"),
                                                   "success": kw.get("success")}).encode()))
                return r.status, json.loads(r.body)
            out = Path(tmp) / "out"
            out.mkdir()
            s = TraceStreamer(out, "http://hub", "ingest-secret-token", poster=poster)
            frame = {"in_progress": True, "steps": [{"type": "tool_call", "name": "Read", "input": "secret.txt"}],
                     "task": {"id": "t"}, "agent": {"name": "a"}}
            (out / "t__a__r1.live.json").write_text(json.dumps(frame))
            self.assertEqual(s.tick(), 1)
            self.assertEqual(s.tick(), 0, "an unchanged frame is not sent again")
            frame["steps"].append({"type": "tool_call", "name": "Bash", "input": "pytest"})
            (out / "t__a__r1.live.json").write_text(json.dumps(frame))
            s.tick()
            final = dict(frame, in_progress=False, outcome={"success": False})
            (out / "t__a__r1.json").write_text(json.dumps(final))
            s.tick()
            self.assertEqual(len({p[0] for p in posts}), 1, "one trace id for every copy of the run")
            self.assertEqual([p[1] for p in posts], [True, True, False])
            refs = [t for t in json.loads(app.handle(Request("GET", "/api/v1/traces", _sign_in(app))).body)["traces"]
                    if t["agent"] == "a"]
            self.assertEqual([(t["live"], t["steps"], t["success"]) for t in refs], [(False, 3, False)])
            for f in (root / ".agentdiff-hub" / "telemetry" / "traces").iterdir():
                self.assertNotIn("secret.txt", f.read_text(), "in-band: sizes, never content")

    def test_a_hub_that_is_down_is_tried_again_at_the_next_look(self):
        from agentdiff.harness.hub_server import TraceStreamer
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def down(url, token, text, **kw):
                calls.append(1)
                if len(calls) == 1:
                    raise OSError("connection refused")
                return 201, {"id": "x", "kept": "this", "hops": 1}
            (Path(tmp) / "a.json").write_text(json.dumps({"steps": [{"type": "tool_call", "name": "x"}]}))
            s = TraceStreamer(tmp, "http://hub", "t", poster=down)
            self.assertEqual(s.tick(), 0)
            self.assertEqual((s.failed, s.last_error), (1, "connection refused"))
            self.assertEqual(s.tick(), 1)


class ServerTest(unittest.TestCase):
    def test_the_live_page_may_run_the_hub_script_and_the_stream_flows(self):
        import urllib.request
        from agentdiff.harness.hub_server import TracePoller, build_app, make_server
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            app = build_app(load(str(root), env={}, overrides={"port": 0, "password_iterations": FAST,
                                                               "live_keepalive_s": 0.2}))
            server = make_server(app)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            app.traces.RESCAN_S = 0
            poller = TracePoller(app.traces, app.bus, 0.05).start()
            try:
                cookie = _sign_in(app)
                req = urllib.request.Request(base + "/live", headers=cookie)
                with urllib.request.urlopen(req) as resp:
                    csp = resp.headers["Content-Security-Policy"]
                    self.assertIn("script-src 'self'", csp)
                    self.assertNotIn("unsafe-inline'; style", csp.split("script-src")[1][:20])
                    self.assertIn('src="/static/live.js"', resp.read().decode())
                with urllib.request.urlopen(urllib.request.Request(base + "/traces", headers=cookie)) as resp:
                    self.assertNotIn("script-src", resp.headers["Content-Security-Policy"])
                stream = urllib.request.urlopen(urllib.request.Request(base + "/api/v1/events", headers=cookie),
                                                timeout=5)
                self.assertEqual(stream.headers["Content-Type"].split(";")[0], "text/event-stream")
                self.assertEqual(stream.readline(), b"retry: 2000\n")
                time.sleep(0.2)
                (root / "loops" / "traces" / "n__m.live.json").write_text(json.dumps({"steps": []}))
                want = trace_id("loops/traces/n__m.live.json").encode()
                deadline = time.time() + 5
                seen = b""
                while want not in seen and time.time() < deadline:
                    seen += stream.readline()
                self.assertIn(want, seen, "a new trace on disk reaches the stream")
                stream.close()
            finally:
                poller.stop()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
