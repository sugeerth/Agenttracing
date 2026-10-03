"""The hub: settings in layers, sign-in, sessions, the catalog, ingest, the pages.

Almost everything is tested through ``App.handle`` with no socket, which
is the point of keeping the app a function; one test runs the real server
to check what only the wire can show (headers, the client).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.hub.app import App, Request, SESSION_COOKIE
from agentdiff.hub.auth import JsonUserStore, MemoryUserStore, User, check_password, hash_password, sync_demo
from agentdiff.hub.catalog import Catalog
from agentdiff.hub.config import HubConfig, load
from agentdiff.hub.ingest import TelemetryStore
from agentdiff.hub.sessions import LoginThrottle, SessionStore
from agentdiff.hub.urls import parse_query, quote, split, unquote
from agentdiff.telemetry import Probe

ROOT = Path(__file__).resolve().parent.parent
FAST = 1_000   # password iterations for tests: the hash's shape, not its cost


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _root(tmp: Path) -> Path:
    root = tmp / "root"
    shutil.copytree(ROOT / "demo" / "vendors" / "live", root / "projects" / "live",
                    ignore=shutil.ignore_patterns("raw", "page"))
    # the page a duel writes (built output, not committed): a stand-in
    page = root / "projects" / "live" / "page"
    page.mkdir()
    (page / "report.html").write_text("<!doctype html><title>report</title><script>1</script>")
    return root


def _app(root: Path, *, demo: bool = True, clock=None) -> App:
    config = HubConfig(root=str(root), password_iterations=FAST, demo=demo)
    users = MemoryUserStore()
    sync_demo(users, demo, config.demo_user, config.demo_password, FAST)
    telemetry = TelemetryStore(root / ".agentdiff-hub" / "telemetry")
    return App(config, users=users, sessions=SessionStore(3600, clock or Clock()),
               throttle=LoginThrottle(3, 60, clock or Clock()),
               catalog=Catalog(root, depth=3, limit=100, extra=telemetry.entries), telemetry=telemetry,
               ingest_token="ingest-secret-token", loopback=True, public_url="http://127.0.0.1:8790")


def _form(**kw) -> bytes:
    return "&".join(f"{k}={quote(v)}" for k, v in kw.items()).encode()


def _sign_in(app: App, user="demo", password="demo", next_path="/"):
    page = app.handle(Request("GET", "/login")).body.decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    return app.handle(Request("POST", "/login", body=_form(csrf=token, user=user, password=password, next=next_path)))


def _cookie(resp) -> dict:
    value = resp.headers["Set-Cookie"].split(";")[0]
    return {"Cookie": value}


class ConfigTest(unittest.TestCase):
    def test_defaults_then_file_then_environment_then_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".agentdiff-hub").mkdir()
            (Path(tmp) / ".agentdiff-hub" / "hub.json").write_text(json.dumps({"port": 9001, "title": "Team"}))
            c = load(tmp, env={})
            self.assertEqual((c.port, c.title, c.host), (9001, "Team", "127.0.0.1"))
            c = load(tmp, env={"AGENTDIFF_HUB_PORT": "9002", "AGENTDIFF_HUB_DEMO": "false"})
            self.assertEqual((c.port, c.demo), (9002, False))
            c = load(tmp, env={"AGENTDIFF_HUB_PORT": "9002"}, overrides={"port": 9003})
            self.assertEqual(c.port, 9003)
            (Path(tmp) / ".agentdiff-hub" / "hub.json").write_text(json.dumps({"prot": 1}))
            with self.assertRaises(ValueError):
                load(tmp, env={})
        self.assertNotIn("demo_password", HubConfig().public())

    def test_the_demo_is_on_here_and_off_on_a_network_unless_asked(self):
        self.assertTrue(HubConfig().effective_demo(loopback=True))
        self.assertFalse(HubConfig().effective_demo(loopback=False))
        self.assertTrue(HubConfig(demo=True).effective_demo(loopback=False))
        self.assertFalse(HubConfig(demo=False).effective_demo(loopback=True))


class AuthTest(unittest.TestCase):
    def test_a_password_is_kept_as_a_salted_hash(self):
        a, b = hash_password("correct horse", FAST), hash_password("correct horse", FAST)
        self.assertNotEqual(a, b, "salted")
        self.assertNotIn("correct horse", a)
        self.assertTrue(check_password("correct horse", a))
        self.assertFalse(check_password("wrong", a))
        self.assertFalse(check_password("x", "garbage"))

    def test_the_user_file_persists_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            store = JsonUserStore(path)
            store.put(User("alice", hash_password("pw-alice-123", FAST)))
            self.assertEqual(JsonUserStore(path).get("alice").name, "alice")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(ValueError):
                store.put(User("bad name!", "x"))

    def test_the_demo_account_follows_the_settings_and_spares_a_real_user(self):
        store = MemoryUserStore()
        sync_demo(store, True, "demo", "demo", FAST)
        self.assertTrue(store.get("demo").demo)
        sync_demo(store, False, "demo", "demo", FAST)
        self.assertIsNone(store.get("demo"))
        store.put(User("demo", hash_password("a real one", FAST)))
        sync_demo(store, True, "demo", "demo", FAST)
        self.assertTrue(check_password("a real one", store.get("demo").password_hash), "a real user is untouched")


class SessionTest(unittest.TestCase):
    def test_sessions_expire_and_tokens_are_single_use(self):
        clock = Clock()
        s = SessionStore(60, clock)
        sess = s.create("demo")
        self.assertIs(s.get(sess.token), sess)
        self.assertTrue(s.csrf_ok(sess, sess.csrf))
        self.assertFalse(s.csrf_ok(sess, "other"))
        clock.t += 61
        self.assertIsNone(s.get(sess.token))
        pre = s.prelogin()
        self.assertTrue(s.take_prelogin(pre))
        self.assertFalse(s.take_prelogin(pre), "a login form's token works once")

    def test_the_throttle_locks_a_name_after_repeated_failures(self):
        clock = Clock()
        t = LoginThrottle(3, 60, clock)
        for _ in range(3):
            t.failed("demo")
        self.assertGreater(t.wait_s("demo"), 0)
        clock.t += 61
        self.assertEqual(t.wait_s("demo"), 0)
        t.succeeded("demo")
        self.assertEqual(t.wait_s("demo"), 0)


class UrlsTest(unittest.TestCase):
    def test_the_small_parser_round_trips(self):
        self.assertEqual(unquote(quote("/runs/x?y=1 &é")), "/runs/x?y=1 &é")
        self.assertEqual(unquote("100%"), "100%")
        self.assertEqual(parse_query("a=1&a=2&b=%41+c"), {"a": ["1", "2"], "b": ["A c"]})
        self.assertEqual(split("/x?next=%2F#frag"), ("/x", "next=%2F"))


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _root(Path(self.tmp.name))
        self.app = _app(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_everything_but_the_login_and_health_wants_a_session(self):
        self.assertEqual(self.app.handle(Request("GET", "/healthz")).status, 200)
        r = self.app.handle(Request("GET", "/"))
        self.assertEqual((r.status, r.headers["Location"]), (303, "/login?next=%2F"))
        self.assertEqual(self.app.handle(Request("GET", "/api/v1/runs")).status, 401)
        login = self.app.handle(Request("GET", "/login")).body.decode()
        self.assertIn("Demo account", login)

    def test_sign_in_wrong_then_right_then_out(self):
        self.assertEqual(_sign_in(self.app, password="nope").status, 401)
        ok = _sign_in(self.app)
        self.assertEqual((ok.status, ok.headers["Location"]), (303, "/"))
        cookie = ok.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        h = _cookie(ok)
        page = self.app.handle(Request("GET", "/", h)).body.decode()
        self.assertIn("projects", page.replace("live", "projects"))
        self.assertEqual(self.app.handle(Request("POST", "/logout", h, _form(csrf="forged"))).status, 403)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        out = self.app.handle(Request("POST", "/logout", h, _form(csrf=csrf)))
        self.assertEqual(out.status, 303)
        self.assertEqual(self.app.handle(Request("GET", "/", h)).status, 303, "the session ended")

    def test_a_login_form_without_its_token_is_refused(self):
        r = self.app.handle(Request("POST", "/login", body=_form(user="demo", password="demo")))
        self.assertEqual(r.status, 401)
        self.assertIn("expired", r.body.decode())

    def test_repeated_failures_lock_the_name(self):
        for _ in range(3):
            _sign_in(self.app, password="nope")
        r = _sign_in(self.app)
        self.assertEqual(r.status, 401)
        self.assertIn("Too many failed attempts", r.body.decode())

    def test_after_sign_in_it_goes_where_it_was_asked_but_never_off_the_hub(self):
        self.assertEqual(_sign_in(self.app, next_path="/api/v1/runs").headers["Location"], "/api/v1/runs")
        for evil in ("//evil.example", "https://evil.example", "/\\evil"):
            with self.subTest(next=evil):
                self.assertEqual(_sign_in(self.app, next_path=evil).headers["Location"], "/")

    def test_the_catalog_shows_the_duel_its_scoreboard_and_its_sandboxed_report(self):
        h = _cookie(_sign_in(self.app))
        runs = json.loads(self.app.handle(Request("GET", "/api/v1/runs", h)).body)["runs"]
        duel = next(r for r in runs if r["kind"] == "duel")
        self.assertEqual(duel["summary"]["agents"], ["haiku", "sonnet"])
        page = self.app.handle(Request("GET", f"/runs/{duel['id']}", h))
        self.assertEqual(page.status, 200)
        body = page.body.decode()
        self.assertIn("2 of 2", body)
        report = self.app.handle(Request("GET", f"/runs/{duel['id']}/page", h))
        self.assertEqual(report.status, 200)
        self.assertTrue(report.sandboxed, "a run's own page is served in a sandbox")
        self.assertIn(b"<script>", report.body)
        self.assertEqual(self.app.handle(Request("GET", "/runs/000000000000", h)).status, 404)
        self.assertEqual(self.app.handle(Request("GET", "/runs/../../etc/passwd", h)).status, 404)
        self.assertEqual(self.app.handle(Request("DELETE", "/", h)).status, 405)
        self.assertEqual(self.app.handle(Request("PUT", "/login")).status, 405)

    def test_agents_post_telemetry_with_the_ingest_token_and_only_with_it(self):
        p = Probe(agent="haiku", task="slugify")
        with p.hop("pytest") as hop:
            hop.fail()
        body = json.dumps({"vector": p.text(), "success": False, "prompt": "do it"}).encode()
        self.assertEqual(self.app.handle(Request("POST", "/api/v1/telemetry", {}, body)).status, 401)
        bad = {"Authorization": "Bearer wrong"}
        self.assertEqual(self.app.handle(Request("POST", "/api/v1/telemetry", bad, body)).status, 401)
        auth = {"Authorization": "Bearer ingest-secret-token"}
        r = self.app.handle(Request("POST", "/api/v1/telemetry", auth, body))
        self.assertEqual(r.status, 201, r.body)
        for junk in (b"not json", json.dumps({"vector": "adi1.zz"}).encode(), json.dumps({"x": 1}).encode(),
                     json.dumps({"vector": p.text(), "success": "yes"}).encode()):
            with self.subTest(junk=junk[:20]):
                self.assertEqual(self.app.handle(Request("POST", "/api/v1/telemetry", auth, junk)).status, 400)
        h = _cookie(_sign_in(self.app))
        runs = json.loads(self.app.handle(Request("GET", "/api/v1/runs", h)).body)["runs"]
        tel = next(r for r in runs if r["kind"] == "telemetry")
        page = self.app.handle(Request("GET", f"/runs/{tel['id']}", h)).body.decode()
        self.assertIn("<svg", page)
        self.assertIn("pytest", page)
        # the posted run is a trace every other command reads
        traces = list((self.root / ".agentdiff-hub" / "telemetry" / "traces").glob("*.json"))
        self.assertEqual(len(traces), 1)

    def test_a_shorter_late_copy_never_replaces_a_longer_one(self):
        p = Probe()
        with p.hop("a"):
            pass
        early = p.text()
        with p.hop("b"):
            pass
        store = TelemetryStore(self.root / "t")
        self.assertEqual(store.save(p.text())["kept"], "this")
        self.assertEqual(store.save(early)["kept"], "earlier")

    def test_with_the_demo_off_there_is_no_demo_account(self):
        app = _app(self.root, demo=False)
        self.assertEqual(_sign_in(app).status, 401)
        self.assertNotIn("Demo account", app.handle(Request("GET", "/login")).body.decode())


class ServerTest(unittest.TestCase):
    def test_the_real_server_sets_its_headers_and_the_client_posts(self):
        from agentdiff.harness.hub_server import build_app, make_server, send
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            app = build_app(load(str(root), env={}, overrides={"port": 0, "password_iterations": FAST}))
            server = make_server(app)
            app.public_url = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                import urllib.request
                with urllib.request.urlopen(app.public_url + "/login") as resp:
                    csp = resp.headers["Content-Security-Policy"]
                    self.assertIn("default-src 'none'", csp)
                    self.assertNotIn("script-src", csp, "the hub's own pages run no script")
                    self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
                p = Probe()
                with p.hop("x"):
                    pass
                status, body = send(app.public_url, app.ingest_token, p.text(), success=True)
                self.assertEqual(status, 201, body)
                self.assertEqual(send(app.public_url, "wrong", p.text())[0], 401)
                token_file = root / ".agentdiff-hub" / "ingest.token"
                self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
                self.assertTrue((root / ".agentdiff-hub" / ".gitignore").is_file(), "the hub's state ignores itself")
            finally:
                server.shutdown()
                server.server_close()


class CommandTest(unittest.TestCase):
    def _run(self, *args, env=None, timeout=60):
        e = {**os.environ, "PYTHONPATH": str(ROOT), **(env or {})}
        return subprocess.run([sys.executable, "-m", "agentdiff", "hub", *args], capture_output=True, text=True,
                              env=e, timeout=timeout)

    def test_it_refuses_a_network_address_without_allow_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, "--host", "0.0.0.0")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--allow-remote", r.stderr)

    def test_add_user_stores_a_hash_never_the_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run(tmp, "--add-user", "alice", env={"AGENTDIFF_HUB_NEW_PASSWORD": "a long secret",
                                                           "AGENTDIFF_HUB_PASSWORD_ITERATIONS": str(FAST)})
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (Path(tmp) / ".agentdiff-hub" / "users.json").read_text()
            self.assertIn("alice", text)
            self.assertNotIn("a long secret", text)
            short = self._run(tmp, "--add-user", "bob", env={"AGENTDIFF_HUB_NEW_PASSWORD": "short"})
            self.assertEqual(short.returncode, 2)


if __name__ == "__main__":
    unittest.main()
