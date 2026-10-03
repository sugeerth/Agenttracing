"""Long-running agents: hours and days, read at the scale they ran.

The demo runs (``demo/longrun``, SYNTHETIC, made by ``make_trace.py``) are a
three-day agent that loops through a night, and the same agent with
``agentdiff guard`` installed. The tests read them, draw them, serve them,
and drive the lens in a browser when one is present.
"""

from __future__ import annotations

import io
import json
import math
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.longrun import _otsu, check_key, is_long, longrun, window
from agentdiff.timeline import _items

DEMO = ROOT / "demo" / "longrun" / "traces"


def _load(name: str) -> dict:
    return json.loads((DEMO / f"ledger_v2__{name}.json").read_text(encoding="utf-8"))


def _svg_ok(test, svg: str) -> None:
    """Every SVG the long views draw parses as XML: an attribute left unquoted
    before ``/>`` swallows the rest of the drawing in a browser."""
    for chunk in svg.split("<svg")[1:]:
        body = "<svg" + chunk.split("</svg>")[0] + "</svg>"
        try:
            ET.fromstring(body.replace("&nbsp;", " "))
        except ET.ParseError as exc:  # pragma: no cover - the failure message is the point
            test.fail(f"not well-formed: {exc}: {body[:300]}")


def _steps(spec):
    """Steps from (start, latency, name, input, output, error) tuples."""
    out = []
    for k, (st, lat, name, inp, outp, err) in enumerate(spec):
        s = {"index": k, "type": "tool_call", "name": name, "input": inp, "output": outp, "started_s": st,
             "latency_s": lat, "tokens": 100}
        if err:
            s["error"] = True
        out.append(s)
    return {"schema_version": 1, "agent": {"name": "a"}, "task": {"id": "t", "prompt": "p"},
            "outcome": {"success": False}, "totals": {"latency_s": spec[-1][0] + spec[-1][1]}, "steps": out}


class ReadingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = _load("agent-3day")
        cls.r = longrun(cls.t)

    def test_the_run_is_cut_into_sessions_by_idle_and_bursts_by_its_own_gaps(self):
        r = self.r
        self.assertEqual(len(r["sessions"]), 6)
        self.assertTrue(all(s["idle_before_s"] >= 1800 for s in r["sessions"][1:]), "sessions part at 30m idle")
        self.assertEqual(r["basis"]["clock"], "recorded")
        self.assertGreaterEqual(r["basis"]["burst_gap_s"], 20)
        self.assertEqual(sum(b["steps"] for b in r["bursts"]), len(self.t["steps"]), "every step in one burst")
        self.assertAlmostEqual(r["active_s"] + r["idle_s"], r["span_s"], delta=1)

    def test_a_loop_through_the_night_is_found_and_is_where_to_look(self):
        lp = max(self.r["loops"], key=lambda x: x["active_s"])
        self.assertGreater(lp["wall_s"], 12 * 3600)
        self.assertEqual(lp["sessions"], 2, "it went round, slept, and went round again")
        self.assertEqual(lp["failing"], "pytest -q tests/integration/test_ledger.py")
        here = self.r["look_here"]
        self.assertEqual((here["kind"], here["burst"]), ("loop", lp["bursts"][0]))
        self.assertIn("did the same calls over 14h", here["sentence"])

    def test_progress_is_a_check_starting_to_pass_and_the_stall_is_between_two(self):
        prog = [ev for ev in self.r["events"] if ev["kind"] == "progress"]
        self.assertEqual(len(prog), 19)
        st = self.r["stall"]
        self.assertGreater(st["wall_s"], 15 * 3600)
        self.assertLess(st["active_s"], st["wall_s"], "the night's idle is not work")
        self.assertIn("No progress for 15h", st["sentence"])
        # the stall ends where the reviewer's note led to the fix
        fixed = next(ev for ev in prog if ev["check"].endswith("integration/test_ledger.py"))
        self.assertEqual(st["to_index"], fixed["index"])

    def test_the_guarded_run_has_no_loop_and_nothing_to_point_at(self):
        g = longrun(_load("agent-guarded"))
        self.assertEqual(g["loops"], [])
        self.assertIsNone(g["look_here"])
        self.assertLess(g["span_s"], self.r["span_s"])

    def test_phases_fold_filler_and_keep_idle_inside_a_loop_inside_it(self):
        from agentdiff.hub.longviz import phases
        ph = phases(self.r)
        kinds = [p["kind"] for p in ph]
        self.assertEqual(kinds.count("loop"), 1)
        self.assertEqual(len(ph), 23)
        k = kinds.index("loop")
        self.assertNotEqual(kinds[k + 1:k + 3], ["idle", "idle"], "the night's idle belongs to the loop")
        self.assertTrue(any(p.get("lead_in") for p in ph), "reading before a change joins the change")

    def test_rhythm_pace_and_window(self):
        g = self.r["rhythm"]
        self.assertEqual((g["unit"], g["rows"], g["wall_clock"]), ("day × hour", 3, True))
        self.assertEqual(sum(c["steps"] for c in g["cells"]), len(self.t["steps"]))
        self.assertIn("seconds per call went from", self.r["pace"]["trend"]["sentence"])
        b = self.r["bursts"][22]
        w = window(self.t, burst=23, reading=self.r)
        self.assertEqual([x["index"] for x in w["steps"]], list(range(b["first"], b["last"] + 1)))
        self.assertTrue(all("call" in x for x in w["steps"]))

    def test_without_a_clock_a_burst_ends_after_each_check(self):
        t = _load("agent-guarded")
        for s in t["steps"]:
            s.pop("started_s", None)
        r = longrun(t)
        self.assertEqual(r["basis"]["clock"], "reconstructed")
        self.assertEqual(len(r["sessions"]), 1, "sessions cannot be told apart without a clock")
        self.assertIn("after each check", r["basis"]["burst_gap_how"])
        self.assertGreater(len(r["bursts"]), 10)

    def test_a_running_run_is_quiet_since_its_last_step_and_its_stall_is_open(self):
        t = dict(_load("agent-3day"), in_progress=True)
        t["steps"] = t["steps"][:600]
        last = t["steps"][-1]
        r = longrun(t, now_s=last["started_s"] + last["latency_s"] + 900)
        self.assertIsNone(r["success"])
        self.assertAlmostEqual(r["quiet_s"], 900, delta=1)
        self.assertTrue(r["stall"]["open"])
        self.assertIn("still going", r["stall"]["sentence"])

    def test_a_check_is_followed_by_its_command_without_the_plumbing(self):
        self.assertEqual(check_key({"name": "Bash", "input": '{"command": "pytest -q tests 2>&1 | tail -5"}'}),
                         "Bash:pytest -q tests")
        self.assertEqual(check_key({"name": "Bash", "input": "make test; echo done"}), "Bash:make test")
        heredoc = "cat >> t.py <<'EOF'\ndef f():\n    return 1\nEOF\npython3 -m unittest -q 2>&1 | tail -3"
        self.assertEqual(check_key({"name": "Bash", "input": heredoc}), "Bash:python3 -m unittest -q")

    def test_the_burst_gap_falls_between_the_two_kinds_of_gap(self):
        r = random.Random(3)
        gaps = [r.uniform(1, 6) for _ in range(300)] + [r.uniform(200, 900) for _ in range(30)]
        thr, eta = _otsu([math.log10(g) for g in gaps])
        self.assertTrue(math.log10(6) < thr < math.log10(200))
        self.assertGreater(eta, 0.5)

    def test_what_counts_as_long(self):
        self.assertTrue(is_long(self.t))
        short = _steps([(0, 1, "Bash", "ls", "a", False), (2, 1, "Bash", "ls", "a", False)])
        self.assertFalse(is_long(short))
        self.assertTrue(is_long({"seconds": 4000}))

    def test_twenty_thousand_steps_read_in_seconds(self):
        base = self.t["steps"]
        span = self.t["totals"]["latency_s"] + 3600
        steps = []
        for rep in range(10):
            for s in base:
                steps.append(dict(s, index=len(steps), started_s=s["started_s"] + rep * span))
        big = dict(self.t, steps=steps, totals=dict(self.t["totals"], latency_s=span * 10))
        t0 = time.perf_counter()
        r = longrun(big)
        took = time.perf_counter() - t0
        self.assertLess(took, 5.0, f"{took:.2f}s for {len(steps)} steps")
        self.assertEqual(len(r["sessions"]), 60)
        from agentdiff.laps import laps
        t0 = time.perf_counter()
        laps(big)
        self.assertLess(time.perf_counter() - t0, 20.0, "the loop search stays near linear per period")


class LoopSearchTest(unittest.TestCase):
    """The faster search for repeated blocks gives the answer the slow one did."""

    @staticmethod
    def _reference(signatures):
        best = {"period": 0, "repeats": 0, "starts_at": None, "length": 0}
        n = len(signatures)
        for period in range(1, n // 2 + 1):
            for start in range(0, n - 2 * period + 1):
                repeats = 1
                while True:
                    nxt = start + repeats * period
                    if nxt + period > n or signatures[nxt:nxt + period] != signatures[start:start + period]:
                        break
                    repeats += 1
                if repeats >= 2 and repeats * period > best["length"]:
                    best = {"period": period, "repeats": repeats, "starts_at": start, "length": repeats * period}
        return best

    def test_the_same_block_as_comparing_every_block(self):
        from agentdiff.process import loops
        from agentdiff.trace import Step
        rnd = random.Random(11)
        for _ in range(400):
            sig = [rnd.randint(0, 3) for _ in range(rnd.randint(0, 30))]
            if sig and rnd.random() < 0.5:
                blk = [rnd.randint(0, 3) for _ in range(rnd.randint(1, 4))]
                at = rnd.randint(0, len(sig))
                sig = sig[:at] + blk * rnd.randint(2, 5) + sig[at:]
            steps = [Step(index=i, type="tool_call", name=f"t{v}", input="x") for i, v in enumerate(sig)]
            got = loops(steps)["longest_repeated_block"]
            self.assertEqual(got, self._reference([f"t{v}" for v in sig]), sig)


class DrawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = _load("agent-3day")
        cls.r = longrun(cls.t)
        cls.items = _items(cls.t)[0]

    def test_every_view_draws_well_formed(self):
        from agentdiff.hub import longviz
        g = _load("agent-guarded")
        views = [longviz.long_overview(self.r, self.items, base="?v=1"),
                 longviz.long_overview(self.r, self.items, base="?v=1", t0=60000, t1=64000),
                 longviz.rhythm_grid(self.r, base="?v=1"), longviz.pace_chart(self.r),
                 longviz.phase_treemap(self.r, self.items, base="?v=1"),
                 longviz.burst_calls(window(self.t, burst=23, reading=self.r), self.r, base="?v=1"),
                 longviz.diff_timeline(self.t, g, self.items, _items(g)[0], names=("<a>", "b"))]
        for v in views:
            self.assertTrue(v)
            _svg_ok(self, v)
        self.assertNotIn("<a>", views[-1].replace("<a href", ""), "names are escaped")
        over = views[0]
        for want in ("↻ loop · bursts", "no progress for 15h", "◆ look here: burst", "idle", "S4 · day 2 00:30"):
            self.assertIn(want, over)
        self.assertTrue(all('class="viz soft' in v for v in views if "<svg" in v),
                        "every long view wears the soft class: its colours at a lower intensity")
        self.assertIn("They part most between", views[-1])

    def test_the_story_is_one_row_per_phase(self):
        from agentdiff.hub import longviz
        html = longviz.chapters_table(self.r, base="?v=1")
        self.assertEqual(html.count("<tr>") - 1, 23 - html.count('class="idle"'))
        self.assertIn("across 2 sessions and the idle between", html)
        self.assertIn("kept failing", html)

    def test_a_burst_of_many_calls_pages_and_brackets_the_retries(self):
        from agentdiff.hub import longviz
        w = window(self.t, t0=59000, t1=90000, reading=self.r)
        self.assertGreater(len(w["steps"]), 600)
        html = longviz.burst_calls(w, self.r, per_page=600, base="?v=1")
        self.assertIn("page=1", html)
        self.assertIn("the same failing call", html)
        _svg_ok(self, html)


class HubTest(unittest.TestCase):
    def setUp(self):
        from test_hub_live import _app, _root, _sign_in
        self.tmp = tempfile.TemporaryDirectory()
        root = _root(Path(self.tmp.name))
        shutil.copytree(DEMO, root / "longrun" / "traces")
        self.app = _app(root)
        self.h = _sign_in(self.app)
        from agentdiff.hub.traces import trace_id
        self.a = trace_id("longrun/traces/ledger_v2__agent-3day.json")
        self.b = trace_id("longrun/traces/ledger_v2__agent-guarded.json")

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, path):
        from agentdiff.hub.app import Request
        return self.app.handle(Request("GET", path, self.h))

    def test_every_view_is_a_tab_phases_one_of_them_and_the_fix_is_at_the_bottom(self):
        closed = self.get(f"/traces/{self.a}").body.decode()
        self.assertIn('<section class="tabp" id="p-long"', closed, "an option, not forced on a run for its length")
        self.assertIn('class="tabp default" id="p-start"', closed)
        self.assertNotIn("longview.js", closed, "a page on another tab runs no script")
        for tab in ("p-trunk", "p-lanes", "p-laps", "p-flow", "p-seconds", "p-code"):
            self.assertIn(f'id="{tab}"', closed)
        self.assertNotIn("Draw it", closed, "every view is drawn")
        self.assertIn("What to change in the agent", closed)
        self.assertIn("agentdiff guard --install", closed, "a loop of hours: the repeat guard, even though it passed")
        self.assertIn("claude --max-turns", closed)
        t0 = time.perf_counter()
        resp = self.get(f"/traces/{self.a}?view=long")
        self.assertLess(time.perf_counter() - t0, 5.0)
        page = resp.body.decode()
        self.assertTrue(resp.scripted, "the lens is the hub's own script")
        for want in ('class="tabp default" id="p-long"', 'class="lens" data-lens="/api/v1/traces/',
                     'src="/static/longview.js"', "The story, phase by phase", "When it worked", "Its pace",
                     "Where the working time went", ">phases<"):
            self.assertIn(want, page)

    def test_a_run_of_thousands_upon_thousands_draws_its_heaviest_on_request(self):
        from agentdiff.hub import views
        old = views.HUGE_STEPS
        views.HUGE_STEPS = 100
        try:
            page = self.get(f"/traces/{self.a}").body.decode()
            self.assertIn("Draw it", page)
            self.assertNotIn("Draw it", self.get(f"/traces/{self.a}?open=trunk").body.decode().split('id="p-trunk"')[1]
                             .split("</section>")[0])
        finally:
            views.HUGE_STEPS = old

    def test_a_burst_a_session_and_a_stretch_open_on_their_calls(self):
        page = self.get(f"/traces/{self.a}?view=long&burst=23").body.decode()
        for want in ("Every call", "Burst 23:", "← burst 22", "burst 24 →", 'id="s'):
            self.assertIn(want, page)
        self.assertIn("Session 4:", self.get(f"/traces/{self.a}?view=long&session=4").body.decode())
        self.assertIn("Every call", self.get(f"/traces/{self.a}?view=long&t0=60000&t1=63600").body.decode())
        self.assertEqual(self.get(f"/traces/{self.a}?view=long&burst=x9").status, 200, "a bad number is ignored")

    def test_beside_the_guarded_run_checkpoint_by_checkpoint(self):
        page = self.get(f"/traces/{self.a}?view=long&vs={self.b}").body.decode()
        self.assertIn("Beside the other run, checkpoint by checkpoint", page)
        self.assertIn("They part most between", page)

    def test_the_lens_reads_compact_columns_and_the_script_is_served(self):
        resp = self.get(f"/api/v1/traces/{self.a}/long")
        d = json.loads(resp.body)
        n = len(_load("agent-3day")["steps"])
        self.assertEqual({len(v) for v in d["calls"].values()}, {n})
        self.assertEqual(len(d["phases"]), 23)
        self.assertNotIn("pace", d["reading"])
        js = self.get("/static/longview.js")
        self.assertEqual(js.status, 200)
        self.assertIn(b"Lens.prototype.fish", js.body)
        self.assertEqual(self.get("/api/v1/traces/000000000000/long").status, 404)

    def test_a_short_run_has_phases_too_one_click_away(self):
        from agentdiff.hub.traces import trace_id
        tid = trace_id("loops/traces/parse_duration__agent-stuck.json")
        page = self.get(f"/traces/{tid}").body.decode()
        self.assertIn('<section class="tabp" id="p-long"', page)
        self.assertIn("The story, phase by phase", page, "a short run's phases are drawn, one tab away")
        opened = self.get(f"/traces/{tid}?view=long").body.decode()
        self.assertIn('class="tabp default" id="p-long"', opened)
        self.assertIn("agentdiff guard --install", page, "a stuck run: the guard that would have stopped it")


class TranscriptClockTest(unittest.TestCase):
    def test_a_claude_code_transcript_keeps_its_real_clock(self):
        from agentdiff.claude_code import transcript_to_trajectory
        def at(sec):
            return f"2026-10-03T05:{sec // 60:02d}:{sec % 60:02d}.250Z"
        entries = [
            {"type": "user", "timestamp": at(0), "message": {"content": "fix it"}},
            {"type": "assistant", "timestamp": at(2), "message": {"content": [
                {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "pytest -q"}}]}},
            {"type": "user", "timestamp": at(9), "message": {"content": [
                {"type": "tool_result", "tool_use_id": "u1", "content": "3 passed"}]}},
            {"type": "assistant", "timestamp": at(130), "message": {"content": [
                {"type": "tool_use", "id": "u2", "name": "Bash", "input": {"command": "pytest -q"}}]}},
            {"type": "user", "timestamp": at(134), "message": {"content": [
                {"type": "tool_result", "tool_use_id": "u2", "content": "5 passed"}]}},
            {"type": "assistant", "timestamp": at(136), "message": {"content": [{"type": "text", "text": "done"}]}},
        ]
        t = transcript_to_trajectory(entries, task="t")
        calls = [s for s in t["steps"] if s["type"] == "tool_call"]
        self.assertEqual([(s["started_s"], s["latency_s"]) for s in calls], [(0.0, 7.0), (128.0, 4.0)])
        self.assertEqual(t["totals"]["latency_s"], 134.0)
        self.assertAlmostEqual(t["started_at"] % 60, 2.25)
        r = longrun(t)
        self.assertEqual(r["basis"]["clock"], "recorded")
        self.assertEqual(len(r["bursts"]), 2)
        self.assertEqual([ev["how"] for ev in r["events"]], ["first pass", "more passing (3 → 5)"])


class CommandTest(unittest.TestCase):
    def test_the_terminal_tells_the_story_phase_by_phase(self):
        from agentdiff.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["timeline", str(DEMO / "ledger_v2__agent-3day.json"),
                             str(DEMO / "ledger_v2__agent-guarded.json"), "--long", "-o", str(Path(tmp) / "t.html")])
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("look here: Burst 23", out)
            self.assertIn("↻ loop: the same calls 102x", out)
            self.assertIn("— 17h 32m idle —", out)
            page = (Path(tmp) / "t.html").read_text()
            self.assertIn("Two long runs, checkpoint by checkpoint", page)
            self.assertEqual(page.count('data-lens-inline="lens-'), 2, "the file carries its lens and its data")
            self.assertIn("Lens.prototype.fish", page)


def _chromium():
    try:
        from test_blocks_ui import HAVE_PLAYWRIGHT, find_chromium
    except Exception:  # noqa: BLE001
        return None
    return find_chromium() if HAVE_PLAYWRIGHT else None


@unittest.skipUnless(_chromium(), "needs Playwright and a Chromium")
class LensBrowserTest(unittest.TestCase):
    """The lens in a real browser, under the hub's own CSP."""

    def test_it_opens_on_the_loop_magnifies_and_moves_phase_by_phase(self):
        from playwright.sync_api import sync_playwright
        from agentdiff.harness.hub_server import build_app, make_server
        from agentdiff.hub.config import load
        from agentdiff.hub.traces import trace_id
        from test_hub_live import FAST, _sign_in
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            shutil.copytree(DEMO, root / "longrun" / "traces")
            app = build_app(load(str(root), env={}, overrides={"port": 0, "password_iterations": FAST}))
            server = make_server(app)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                name, value = _sign_in(app)["Cookie"].split("=", 1)
                tid = trace_id("longrun/traces/ledger_v2__agent-3day.json")
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=_chromium())
                    ctx = browser.new_context(viewport={"width": 1100, "height": 1000})
                    ctx.add_cookies([{"name": name, "value": value, "url": base}])
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                    page.goto(f"{base}/traces/{tid}?view=long")
                    page.wait_for_selector(".lens.on svg.lens-tape", timeout=15000)
                    self.assertIn("↻ loop", page.text_content(".lens-title"))
                    svg = page.query_selector("svg.lens-tape")
                    svg.scroll_into_view_if_needed()
                    box = svg.bounding_box()
                    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.6)
                    page.wait_for_timeout(150)
                    self.assertIn("under the lens", page.text_content(".lens-card"))
                    self.assertTrue(page.is_visible(".lens-halo circle"))
                    svg.focus()
                    page.keyboard.press("ArrowRight")
                    page.wait_for_timeout(600)
                    title = page.text_content(".lens-title")
                    self.assertNotIn("with no step at all", title, "idle is passed, not stopped at")
                    self.assertIn("burst 125", title)
                    page.click(".lens-chips a[data-f=fail]")
                    page.keyboard.press("[")
                    page.wait_for_timeout(600)
                    self.assertIn("loop", page.text_content(".lens-title"))
                    self.assertEqual(errors, [])
                    browser.close()
            finally:
                server.shutdown()


    def test_the_page_written_to_a_file_carries_a_working_lens(self):
        from playwright.sync_api import sync_playwright
        from agentdiff.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "long.html"
            with redirect_stdout(io.StringIO()):
                main(["timeline", str(DEMO / "ledger_v2__agent-3day.json"), "--long", "-o", str(out)])
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=_chromium())
                page = browser.new_page(viewport={"width": 1100, "height": 1000})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(out.as_uri())
                page.wait_for_selector(".lens.on svg.lens-tape", timeout=15000)
                svg = page.query_selector("svg.lens-tape")
                svg.scroll_into_view_if_needed()
                box = svg.bounding_box()
                page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.6)
                page.wait_for_timeout(150)
                self.assertIn("under the lens", page.text_content(".lens-card"))
                self.assertEqual(errors, [])
                browser.close()


if __name__ == "__main__":
    unittest.main()


class ExportTest(unittest.TestCase):
    """The hub as static files, every part of it, for a private static host."""

    def test_every_page_is_written_linked_signed_in_and_nothing_secret_leaves(self):
        from agentdiff.hub.export import export
        from test_hub_live import _root
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            shutil.copytree(DEMO, root / "longrun" / "traces")
            out = Path(tmp) / "site"
            counts = export(str(root), str(out), bare_index=True, title="Test Hub")
            self.assertGreater(counts["pages"], 10)
            names = {p.name for p in out.iterdir()}
            for want in ("index.html", "login.html", "account.html", "live.html", "evolve.html", "evals.html"):
                self.assertIn(want, names)
            index = (out / "index.html").read_text()
            self.assertNotIn("<html", index, "a bare index for a host's shell")
            self.assertIn("<title>Test Hub</title>", index[:8000])
            login = (out / "login.html").read_text()
            self.assertIn('id="signin"', login)
            self.assertNotIn('name="csrf"', login)
            for page in (out / n for n in names if n.endswith(".html") and not n.endswith("-page.html")):
                html = page.read_text()
                self.assertNotIn('href="/', html, f"{page.name}: every hub link points at a file")
                self.assertNotIn("ingest-secret-token", html)
                if page.name != "login.html":
                    self.assertIn("location.replace('login.html')", html, f"{page.name} asks for a sign-in")
                for target in re.findall(r'href="([^"#]+\.html)', html):
                    self.assertIn(target, names, f"{page.name} links to {target}, which was not written")
            long_page = next(n for n in names if n.endswith(".phases.html") and
                             "ledger" in (out / n).read_text()[:3000])
            html = (out / long_page).read_text()
            self.assertIn('data-lens-inline="lens-data"', html, "the lens carries its data")
            self.assertIn("Lens.prototype.fish", html, "the lens is inlined")
            self.assertIn("#burst-", html, "a burst link moves the lens")
            self.assertIn(':root[data-theme="dark"]', html, "dark follows the viewer's choice too")
            data = re.search(r'<script type="application/json" id="lens-data">(.*?)</script>', html, re.S).group(1)
            self.assertTrue(json.loads(data)["phases"])
            live = (out / "live.html").read_text()
            self.assertIn('data-replay="1"', live, "Live replays the longest run")
