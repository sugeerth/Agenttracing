"""Browser tests for the composable blocks page (v21).

Skipped unless Playwright and a Chromium build are present, because the
engine itself stays dependency-free — but the properties checked here cannot
be checked any other way. They are about the promises the page makes to the
person reading it: that their layout persists, that the visitor id is theirs
and erasable, and that no block can take the page down with it.

``file://`` and ``http://`` are both exercised. They genuinely differ:
Chromium refuses cookies for file:// origins, so the store must fall back to
localStorage and *say* it did, rather than dropping the visitor silently.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False


def find_chromium():
    """A Chromium the installed Playwright can actually launch."""
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    if base.is_dir():
        for candidate in sorted(base.glob("chromium-*/chrome-linux/chrome")):
            if candidate.is_file():
                return str(candidate)
    return None


CHROMIUM = find_chromium() if HAVE_PLAYWRIGHT else None
VID = "() => AgentDiff._internals.Store.get('agentdiff:vid')"


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class BlocksPageTest(unittest.TestCase):
    server = None
    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "batch",
             str(ROOT / "demo" / "telemetry" / "traces"), "-o", str(out),
             "--template", str(ROOT / "web" / "blocks.html")],
            cwd=str(ROOT), check=True, capture_output=True)
        cls.report = out / "report.html"
        assert cls.report.is_file(), "batch did not write a report"

        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(out))
        socketserver.TCPServer.allow_reuse_address = True
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open(self, scheme="http"):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("console",
                lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        url = (f"file://{self.report}" if scheme == "file"
               else f"http://127.0.0.1:{self.port}/report.html")
        page.goto(url)
        page.wait_for_timeout(400)
        return context, page, errors

    # ------------------------------------------------------------ rendering

    def test_page_loads_without_console_errors(self):
        context, page, errors = self.open()
        self.assertEqual(errors, [], "console errors on load")
        context.close()

    def test_every_registered_block_renders_or_is_deliberately_hidden(self):
        # A block that throws is caught and shown as a failure notice; that
        # notice must not appear for any block on the shipped demo data.
        context, page, errors = self.open()
        broken = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('.block').forEach(function (card) {
                const body = card.querySelector('.block-body');
                if (body && body.textContent.indexOf('failed to render') >= 0) {
                    out.push(card.getAttribute('data-block'));
                }
            });
            return out;
        }""")
        self.assertEqual(broken, [], "blocks threw during render")
        context.close()

    def test_the_page_never_scrolls_sideways(self):
        # Wide content belongs inside a scrolling card. A page-level
        # horizontal scrollbar means something is pushed off screen — and on
        # a phone the thing pushed off was the toolbar's own buttons, which
        # is worse than awkward: the control is unreachable.
        context, page, _ = self.open()
        for width in (1440, 1280, 1024, 768, 480, 360):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(250)
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth "
                    "- document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1,
                                     f"page scrolls horizontally at {width}px")
        context.close()

    def test_every_toolbar_control_stays_on_screen(self):
        context, page, _ = self.open()
        for width in (1440, 480, 360):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(250)
                offscreen = page.evaluate("""() => {
                    const limit = document.documentElement.clientWidth + 1;
                    const bad = [];
                    document.querySelectorAll('.topbar button, .topbar select')
                        .forEach(function (el) {
                            const box = el.getBoundingClientRect();
                            if (box.right > limit || box.left < -1) bad.push(el.id || el.textContent);
                        });
                    return bad;
                }""")
                self.assertEqual(offscreen, [], f"controls off screen at {width}px")
        context.close()

    def test_no_block_clips_content_it_cannot_scroll_to(self):
        # The card clips for its rounded corners, so a table wider than the
        # column would be unreachable rather than merely awkward.
        context, page, _ = self.open()
        clipped = page.evaluate("""() => {
            const bad = [];
            document.querySelectorAll('.block').forEach(function (card) {
                const body = card.querySelector('.block-body');
                if (!body) return;
                const style = getComputedStyle(body);
                if (body.scrollWidth > body.clientWidth + 2 &&
                    style.overflowX !== 'auto' && style.overflowX !== 'scroll') {
                    bad.push(card.getAttribute('data-block'));
                }
            });
            return bad;
        }""")
        self.assertEqual(clipped, [], "blocks clip unreachable content")
        context.close()

    def test_blocks_render_content_rather_than_an_empty_state(self):
        # The demo batch exercises every analysis, so a block that renders
        # only its empty state here is not reading the data it claims to.
        context, page, _ = self.open()
        blank = page.evaluate("""() => {
            const bad = [];
            document.querySelectorAll('.block:not(.collapsed)').forEach(function (card) {
                const body = card.querySelector('.block-body');
                if (body && body.textContent.trim().length < 40) {
                    bad.push(card.getAttribute('data-block'));
                }
            });
            return bad;
        }""")
        self.assertEqual(blank, [], "blocks rendered (nearly) nothing on the demo batch")
        context.close()

    def test_a_throwing_block_does_not_take_the_page_down(self):
        context, page, errors = self.open()
        survived = page.evaluate("""() => {
            const I = AgentDiff._internals;
            const first = I.State.layout.stacks.flat()[0];
            if (!first) return 'no blocks';
            const entry = I.BY_ID[first.id];
            const original = entry.render;
            entry.render = function () { throw new Error('deliberate'); };
            try {
                document.getElementById('btn-cols').click();  // forces a re-render
                return document.querySelectorAll('.block').length > 1 ? 'ok' : 'page emptied';
            } finally {
                entry.render = original;
            }
        }""")
        self.assertEqual(survived, "ok")
        context.close()

    # ------------------------------------------------------------- identity

    def test_visitor_id_is_a_real_uuid(self):
        context, page, _ = self.open()
        vid = page.evaluate(VID)
        self.assertIsInstance(vid, str)
        self.assertGreaterEqual(len(vid), 32)
        self.assertFalse(vid.startswith("fallback-"),
                         "crypto uuid source was unavailable")
        context.close()

    def test_cookie_is_set_over_http(self):
        context, page, _ = self.open("http")
        cookie = page.evaluate(
            "() => (document.cookie.match(/agentdiff_vid=([^;]+)/) || [])[1] || null")
        self.assertEqual(cookie, page.evaluate(VID))
        context.close()

    def test_file_urls_fall_back_and_report_the_backend(self):
        # Chromium gives file:// pages no cookies. Losing the visitor there
        # would be invisible; falling back and saying so is not.
        context, page, _ = self.open("file")
        self.assertIn(page.evaluate("() => AgentDiff._internals.Store.backendName()"),
                      ("localStorage", "memory (this tab only)"))
        self.assertIsInstance(page.evaluate(VID), str)
        context.close()

    def test_identity_and_layout_survive_a_reload(self):
        for scheme in ("file", "http"):
            with self.subTest(scheme=scheme):
                context, page, _ = self.open(scheme)
                vid = page.evaluate(VID)
                page.evaluate("""() => {
                    const I = AgentDiff._internals;
                    I.State.layout.cols = 2;
                    I.Store.set('agentdiff:v1:' + I.Store.get('agentdiff:vid') + ':layout',
                                I.State.layout);
                }""")
                page.reload()
                page.wait_for_timeout(350)
                self.assertEqual(page.evaluate(VID), vid)
                self.assertEqual(page.evaluate("() => AgentDiff._internals.State.layout.cols"), 2)
                context.close()

    def test_erasing_produces_a_genuinely_new_visitor(self):
        context, page, _ = self.open()
        vid = page.evaluate(VID)
        page.evaluate("""() => {
            const I = AgentDiff._internals;
            I.Store.keys('agentdiff:').forEach(function (k) { I.Store.remove(k); });
            document.cookie = 'agentdiff_vid=;path=/;max-age=0';
        }""")
        page.reload()
        page.wait_for_timeout(350)
        self.assertNotEqual(page.evaluate(VID), vid)
        context.close()

    # ------------------------------------------------------ personalization

    def test_interest_halves_every_fortnight(self):
        context, page, _ = self.open()
        decayed = page.evaluate("""() => {
            const d = AgentDiff._internals.decay, now = Date.now();
            return {half: d(10, now - 14*86400000, now), old: d(10, now - 56*86400000, now)};
        }""")
        self.assertAlmostEqual(decayed["half"], 5.0, places=2)
        self.assertLess(decayed["old"], 1.0)
        context.close()

    def test_personalization_off_ranks_purely_on_the_data(self):
        context, page, _ = self.open()
        pure = page.evaluate("""() => {
            AgentDiff._internals.State.prefs.personalize = false;
            return AgentDiff._internals.rank({report: null, reports: [], aggregate: {}})
                .every(function (r) { return Math.abs(r.score - r.relevance) < 1e-9; });
        }""")
        self.assertTrue(pure)
        context.close()

    def test_layout_is_never_reordered_without_consent(self):
        # Auto-apply is off by default: a layout the visitor arranged is a
        # stronger signal than one inferred from their clicks.
        context, page, _ = self.open()
        self.assertFalse(page.evaluate("() => AgentDiff._internals.State.prefs.autoApply"))
        before = page.evaluate(
            "() => AgentDiff._internals.State.layout.stacks.map(s => s.map(x => x.id))")
        page.evaluate("""() => {
            const I = AgentDiff._internals;
            const ids = I.REGISTRY.map(function (e) { return e.id; });
            ids.forEach(function (id, i) {
                I.State.signals[id] = {weight: i * 5, count: i, last: Date.now()};
            });
        }""")
        page.reload()
        page.wait_for_timeout(400)
        after = page.evaluate(
            "() => AgentDiff._internals.State.layout.stacks.map(s => s.map(x => x.id))")
        self.assertEqual(before, after, "layout moved without the visitor asking")
        context.close()

    def test_unknown_block_ids_are_dropped_when_reconciling(self):
        # A stored layout outlives the build that wrote it.
        context, page, _ = self.open()
        flat = page.evaluate("""() => {
            const out = AgentDiff._internals.reconcile(
                {cols: 3, stacks: [[{id: 'ghost'}], [], []], hidden: ['gone']},
                {report: null, reports: [], aggregate: {}});
            return out.stacks.flat().map(function (x) { return x.id; }).concat(out.hidden);
        }""")
        self.assertNotIn("ghost", flat)
        self.assertNotIn("gone", flat)
        context.close()

    # --------------------------------------------------------------- panels

    def test_the_you_panel_discloses_what_is_stored(self):
        context, page, _ = self.open()
        page.click("#btn-you")
        page.wait_for_timeout(200)
        body = page.inner_text("#you-body")
        self.assertIn(page.evaluate(VID), body)
        self.assertIn("leaves this browser", body)
        context.close()

    def test_an_open_panel_does_not_trap_you(self):
        # A panel covering the top bar hides the button for the other panel.
        context, page, _ = self.open()
        page.click("#btn-you")
        page.wait_for_timeout(200)
        page.click("#btn-drawer")
        page.wait_for_timeout(200)
        self.assertTrue(page.evaluate(
            "() => document.getElementById('drawer').classList.contains('open')"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        self.assertFalse(page.evaluate(
            "() => document.getElementById('drawer').classList.contains('open')"))
        context.close()

    def test_theme_toggle_reaches_both_explicit_themes(self):
        context, page, _ = self.open()
        seen = set()
        for _ in range(3):
            page.click("#btn-theme")
            page.wait_for_timeout(120)
            seen.add(page.evaluate(
                "() => document.documentElement.getAttribute('data-theme')"))
        self.assertIn("dark", seen)
        self.assertIn("light", seen)
        context.close()


if __name__ == "__main__":
    unittest.main()
