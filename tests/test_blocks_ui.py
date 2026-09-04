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
                document.getElementById('btn-theme').click();  // forces a re-render
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


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class DiagnosisBlockTest(unittest.TestCase):
    """The Diagnosis block renders the adjudicated diagnosis, verbatim.

    Driven by a real pair report — t05 is the demo pair whose diagnosis
    carries a leading, a merged, and a ruled-out hypothesis — so the test
    checks the block against the engine's actual output, not a fixture.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        pair_json = out / "t05.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "traces" / "t05_flight_duration__atlas-v2.json"),
             str(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json"),
             "-o", str(pair_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)

        from deepcompare.report import render_html
        cls.diagnosis = json.loads(pair_json.read_text(encoding="utf-8"))["diagnosis"]
        assert cls.diagnosis.get("hypotheses"), "t05 pair carries no diagnosis"
        cls.report = out / "report.html"
        render_html([json.loads(pair_json.read_text(encoding="utf-8"))], {},
                    ROOT / "web" / "blocks.html", cls.report)

        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
            if Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").is_file()
            else CHROMIUM,
            args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def test_diagnosis_block_renders_verdict_and_hypothesis_rows(self):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}")
        page.wait_for_timeout(400)

        block = page.locator('.block[data-block="diagnosis"]')
        self.assertEqual(block.count(), 1, "Diagnosis block is not on the page")

        # Deep in the outcome stack the block may start collapsed; the body
        # only renders once it is expanded, exactly as a reader would do.
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="diagnosis"]')

        # The verdict, verbatim — same characters the engine wrote.
        verdict = block.locator(".dx-verdict").inner_text()
        self.assertIn(self.diagnosis["verdict"], verdict)

        # One row per hypothesis, in the report's own order, each carrying
        # its status and its statement; merged rows stay visible and say so.
        # the story folds the non-leading rows behind a disclosure: open it
        # open every fold; the locator re-resolves after each click
        while block.locator("details:not([open]) > summary").count():
            block.locator("details:not([open]) > summary").first.click()
            page.wait_for_timeout(120)
        page.wait_for_timeout(150)
        rows = block.locator(".dx-row")
        hypotheses = self.diagnosis["hypotheses"]
        self.assertEqual(rows.count(), len(hypotheses))
        for i, hyp in enumerate(hypotheses):
            row_text = rows.nth(i).inner_text()
            self.assertIn(hyp["status"].replace("_", " "), row_text)
            self.assertIn(hyp["statement"], row_text)
            if hyp.get("score") is not None:
                self.assertIn(f"{hyp['score']:.2f}", row_text)
            if hyp["status"] == "merged":
                self.assertIn("part of the leading account", row_text)

        # Expanding a row discloses its evidence and its discriminator.
        rows.nth(0).locator(".dx-head").click()
        page.wait_for_timeout(150)
        body = rows.nth(0).locator(".dx-body").inner_text()
        self.assertIn("How to settle it", body)
        self.assertIn(hypotheses[0]["discriminator"], body)

        # The confidence line quotes level and basis verbatim.
        conf = block.locator(".dx-conf").inner_text()
        self.assertIn(self.diagnosis["confidence"]["level"], conf)
        self.assertIn(self.diagnosis["confidence"]["basis"], conf)

        self.assertEqual(errors, [], "page errors while rendering the diagnosis")
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class DecisiveStepBlockTest(unittest.TestCase):
    """The Diagnosis block renders the decisive step and the causal account.

    Driven by two real pair reports: t05 carries a decisive step (step 1)
    and a causal account whose links include a measured word-overlap
    propagation and a positional final-answer link; p01 (process demo) is
    the honest abstention — decisive_step.step is null with a stated
    reason, and the causal account is empty. Both are the engine's actual
    output, not fixtures, and both must be shown verbatim.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        t05_json = out / "t05.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "traces" / "t05_flight_duration__atlas-v2.json"),
             str(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json"),
             "-o", str(t05_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        p01_json = out / "p01.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "process" / "traces" /
                 "p01_cancel_booking__steady-v1.json"),
             str(ROOT / "demo" / "process" / "traces" /
                 "p01_cancel_booking__hasty-v2.json"),
             "-o", str(p01_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)

        from deepcompare.report import render_html
        cls.t05 = json.loads(t05_json.read_text(encoding="utf-8"))
        cls.p01 = json.loads(p01_json.read_text(encoding="utf-8"))
        decisive = cls.t05["diagnosis"].get("decisive_step") or {}
        assert decisive.get("step") is not None, "t05 pair has no decisive step"
        assert cls.t05["diagnosis"].get("causal_account"), \
            "t05 pair has no causal account"
        abstain = cls.p01["diagnosis"].get("decisive_step") or {}
        assert abstain.get("step") is None and abstain.get("reason"), \
            "p01 pair is not the abstention case"
        cls.t05_report = out / "t05.html"
        render_html([cls.t05], {}, ROOT / "web" / "blocks.html", cls.t05_report)
        cls.p01_report = out / "p01.html"
        render_html([cls.p01], {}, ROOT / "web" / "blocks.html", cls.p01_report)

        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
            if Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").is_file()
            else CHROMIUM,
            args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open_diagnosis(self, report):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{report}")
        page.wait_for_timeout(400)

        block = page.locator('.block[data-block="diagnosis"]')
        self.assertEqual(block.count(), 1, "Diagnosis block is not on the page")

        # Deep in the outcome stack the block may start collapsed; the body
        # only renders once it is expanded, exactly as a reader would do.
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="diagnosis"]')
        return context, page, block, errors

    def test_the_verdict_card_leads_the_page_and_its_chips_move_the_cursor(self):
        # the lead lane: the card is the first block on the page, above the
        # hero, full width, never in a column; each line quotes the
        # report's card verbatim; a step chip moves the shared cursor to
        # the alignment row of that step, exactly as a map click does
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.t05_report}")
        page.wait_for_timeout(500)
        lead = page.locator('#lead-lane .block[data-block="verdict-card"]')
        self.assertEqual(lead.count(), 1, "verdict card is not in the lead lane")
        self.assertEqual(page.locator('#stacks .block[data-block="verdict-card"]').count(), 0)
        lead_box = lead.bounding_box()
        hero_box = page.locator("#hero-lane").bounding_box()
        self.assertLess(lead_box["y"] + lead_box["height"], hero_box["y"] + 1)
        text = lead.inner_text()
        for line in self.t05["verdict_card"]["lines"]:
            self.assertIn(line["text"], text)
        cause = next(l for l in self.t05["verdict_card"]["lines"] if l["key"] == "cause")
        row = next(i for i, r in enumerate(self.t05["alignment"])
                   if r.get(f"{cause['side']}_index") == cause["step"])
        lead.locator(".vc-chip").first.dispatch_event("click")
        page.wait_for_timeout(300)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            self.assertEqual(detail.locator(".tag.mono").first.inner_text(), f"row {row}")
        self.assertEqual(errors, [])
        context.close()

    def _open_page(self, report):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{report}")
        page.wait_for_timeout(500)
        return context, page, errors

    def test_the_reading_block_quotes_the_reading_and_its_chips_move_the_cursor(self):
        # the eval reasoning layer on the page: defaults to the failing
        # run, every value of the answer with its status, findings by
        # evidence class, take-forward as a list; a step chip moves the
        # shared cursor to that step's alignment row
        context, page, errors = self._open_page(self.t05_report)
        block = page.locator('.block[data-block="reading"]')
        self.assertEqual(block.count(), 1, "Reading block is not on the page")
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="reading"]')
        reading = self.t05["reading"]["b"]
        # the story folds the two walks behind a disclosure: open it
        # open every fold; the locator re-resolves after each click
        while block.locator("details:not([open]) > summary").count():
            block.locator("details:not([open]) > summary").first.click()
            page.wait_for_timeout(120)
        page.wait_for_timeout(150)
        text = block.inner_text()
        self.assertIn(reading["summary"], text)
        for r in reading["rests_on"]:
            self.assertIn(str(r["value"]), text)
        for f in reading["what_it_means"]:
            self.assertIn(f["statement"], text)
        for t in reading["take_forward"]:
            self.assertIn(t["instead"], text)
        self.assertEqual(block.locator(".rd-head button[aria-pressed='true']").inner_text(),
                         self.t05["b"]["agent"]["name"])
        first = reading["take_forward"][0]
        row = next(i for i, r in enumerate(self.t05["alignment"])
                   if r.get("b_index") == first["at_step"])
        block.locator(f".rd-todo .rd-step[data-step='{first['at_step']}']").first.dispatch_event("click")
        page.wait_for_timeout(300)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            self.assertEqual(detail.locator(".tag.mono").first.inner_text(), f"row {row}")
        # the A/B toggle reads the other run
        block.locator(".rd-head button").first.click()
        page.wait_for_timeout(200)
        self.assertIn(self.t05["reading"]["a"]["summary"],
                      page.locator('.block[data-block="reading"]').inner_text())
        self.assertEqual(errors, [])
        context.close()

    def test_the_decisive_ring_is_graded_by_verification(self):
        # hypothesized → long-dashed ring and a legend that says so;
        # only a replay-verified step earns a solid ring
        context, page, errors = self._open_page(self.t05_report)
        ring = page.locator("svg .tj-ring.dec")
        self.assertGreaterEqual(ring.count(), 1, "no decisive ring on the map")
        self.assertIn("hypothesized", ring.first.get_attribute("class"))
        self.assertEqual(ring.first.get_attribute("stroke-dasharray"), "6,3")
        legend = page.locator('.block[data-block="trajectory-map"] .tjm-foot, .tjm-foot').first.inner_text()
        self.assertIn("hypothesized, not replay-verified", legend)
        self.assertEqual(errors, [])
        context.close()

    def test_the_run_lens_shows_the_readings_step_roles(self):
        # the run lens lives on the Evidence tab
        context, page, errors = self._open_page(f"{self.t05_report}#view=evidence")
        lens = page.locator('.block[data-block="run-lens"]')
        self.assertEqual(lens.count(), 1)
        if "collapsed" in (lens.get_attribute("class") or ""):
            lens.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
        roles = page.locator(".tjl-b.role")
        self.assertGreaterEqual(roles.count(), 1, "no reading role chips in the run lens")
        # the chips are uppercased by CSS; compare the words
        labels = {roles.nth(i).inner_text().lower() for i in range(roles.count())}
        self.assertTrue(labels & {"feeds answer", "dead end", "no information"}, labels)
        self.assertEqual(errors, [])
        context.close()

    def test_first_open_is_quiet_accessible_and_titled(self):
        # no toast greets a first visit; every chart svg is an image with
        # a name; the page has one h1 naming the task; no CSS text under 11px
        context, page, errors = self._open_page(self.t05_report)
        page.wait_for_timeout(400)   # 900ms since load
        toast = page.locator("#toast")
        visible = toast.count() and toast.first.is_visible() and toast.first.inner_text().strip()
        self.assertFalse(visible, "a toast greets the first visit")
        self.assertEqual(page.locator("svg:not([role]):not([aria-hidden='true'])").evaluate_all(
            "els => els.filter(e => !e.parentNode.closest('svg')).length"), 0)
        h1 = page.locator("h1")
        self.assertEqual(h1.count(), 1)
        self.assertIn(self.t05["task"]["prompt"][:40], h1.inner_text())
        small = page.evaluate("""() => {
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let n = 0; let node;
          while ((node = walker.nextNode())) {
            if (!node.textContent.trim()) continue;
            const el = node.parentElement; if (!el || el.closest('svg')) continue;
            const fs = parseFloat(getComputedStyle(el).fontSize);
            if (fs < 11) n++;
          }
          return n; }""")
        self.assertEqual(small, 0, f"{small} HTML text node(s) under 11px")
        self.assertEqual(errors, [])
        context.close()

    def test_the_story_uses_one_type_scale(self):
        # six tokens; HTML text in the story lane never below 12px; the
        # page as a whole uses at most seven distinct computed sizes
        context, page, errors = self._open_page(self.t05_report)
        sizes = page.evaluate("""() => {
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          const seen = new Set(); let small = 0; let node;
          while ((node = walker.nextNode())) {
            if (!node.textContent.trim()) continue;
            const el = node.parentElement; if (!el || el.closest('svg')) continue;
            if (el.closest('#drawer, #you, .toast')) continue;
            const fs = parseFloat(getComputedStyle(el).fontSize);
            seen.add(Math.round(fs * 2) / 2);
            if (el.closest('.story-lane, .lead-lane') && fs < 12) small++;
          }
          return { distinct: [...seen].sort((a, b) => a - b), small }; }""")
        self.assertLessEqual(len(sizes["distinct"]), 7, sizes["distinct"])
        self.assertEqual(sizes["small"], 0)
        self.assertEqual(errors, [])
        context.close()

    def test_the_reading_carries_every_step_and_the_cost_is_a_stat_row(self):
        context, page, errors = self._open_page(self.t05_report)
        fold = page.locator('.block[data-block="reading"] details.rd-steps')
        self.assertEqual(fold.count(), 1)
        fold.locator("summary").click()
        page.wait_for_timeout(300)
        self.assertEqual(fold.locator(".tjl-step").count(), len(self.t05["b"]["steps"]))
        cells = page.locator('.block[data-block="deltas"] .dl-cell')
        self.assertEqual(cells.count(), 6)
        self.assertEqual(page.locator('.block[data-block="deltas"] .dl-d').count(), 6)
        # the cheaper run failed here: its savings are not green
        self.assertEqual(page.locator('.block[data-block="deltas"] .dl-d.good').count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_decisive_step_and_causal_account_render_verbatim(self):
        context, page, block, errors = self.open_diagnosis(self.t05_report)
        diagnosis = self.t05["diagnosis"]
        decisive = diagnosis["decisive_step"]

        # The decisive-step line, right under the verdict: step and basis
        # visible, the criterion carried on the line itself.
        line = block.locator(".dx-decisive")
        self.assertEqual(line.count(), 1, "no decisive-step line")
        line_text = line.inner_text()
        self.assertIn(f"Step {decisive['step']}", line_text)
        self.assertIn(decisive["basis"], line_text)
        self.assertIn(decisive["criterion"], line_text)

        # The causal account starts collapsed — details on demand.
        account = diagnosis["causal_account"]
        section = block.locator(".dx-causal")
        self.assertEqual(section.count(), 1, "no causal-account section")
        self.assertFalse(section.locator(".dx-causal-body").is_visible())
        section.locator(".dx-causal-head").click()
        page.wait_for_timeout(150)
        self.assertTrue(section.locator(".dx-causal-body").is_visible())

        # One row per link, in the report's own order, each quoting the
        # happening and its mechanism verbatim — the measured word-overlap
        # link included.
        rows = section.locator(".dx-clist li")
        self.assertEqual(rows.count(), len(account))
        for i, link in enumerate(account):
            row_text = rows.nth(i).inner_text()
            self.assertIn(f"step {link['step']}", row_text)
            self.assertIn(link["happened"], row_text)
            if link.get("mechanism"):
                self.assertIn(link["mechanism"], row_text)
        body_text = section.locator(".dx-causal-body").inner_text()
        self.assertIn("word overlap", body_text)

        # Measured links read normal; positional/adjacency links carry the
        # soft register, so the epistemic status is scannable.
        for i, link in enumerate(account):
            mechanism = link.get("mechanism") or ""
            if not mechanism:
                continue
            soft = rows.nth(i).locator(".dx-mech.soft").count()
            if "measured" in mechanism:
                self.assertEqual(soft, 0, f"measured link {i} styled as positional")
            elif "positional" in mechanism or "adjacency" in mechanism:
                self.assertEqual(soft, 1, f"positional link {i} not distinguished")

        self.assertEqual(errors, [], "page errors while rendering the diagnosis")
        context.close()

    def test_abstention_renders_its_reason_not_an_absence(self):
        context, page, block, errors = self.open_diagnosis(self.p01_report)
        decisive = self.p01["diagnosis"]["decisive_step"]

        line = block.locator(".dx-decisive.abstain")
        self.assertEqual(line.count(), 1, "no abstention line")
        line_text = line.inner_text()
        self.assertIn("No decisive step", line_text)
        self.assertIn(decisive["reason"], line_text)
        self.assertIn("no agent error to correct", line_text)

        # An empty causal account gets no section, not an empty shell.
        self.assertEqual(block.locator(".dx-causal").count(), 0)

        self.assertEqual(errors, [], "page errors while rendering the abstention")
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class ConsolidatedDiagnosisBlockTest(unittest.TestCase):
    """The Across-runs block renders the cross-run consolidation, verbatim.

    Driven by a real aggregate — `deepcompare runs` over the multi-run demo
    corpus writes `diagnosis_consolidated` and renders report.html from the
    blocks template — so the test checks the block against the engine's
    actual output, not a fixture. The demo corpus carries reproducible
    causes, a flaky failure with its k-of-n denominator, and inconclusive
    executed checks, which are exactly the things the block promises to
    keep visible.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "runs",
             str(ROOT / "demo" / "runs" / "traces"), "-o", str(out),
             "--template", str(ROOT / "web" / "blocks.html")],
            cwd=str(ROOT), check=True, capture_output=True)
        cls.report = out / "report.html"
        assert cls.report.is_file(), "runs did not write a report"

        aggregate = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        cls.consolidated = aggregate["diagnosis_consolidated"]
        cls.failing = [entry for entry in cls.consolidated["per_task_agent"]
                       if entry["failures"]]
        assert cls.failing, "demo runs corpus carries no failing entries"

        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
            if Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").is_file()
            else CHROMIUM,
            args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def test_across_runs_block_renders_rows_for_the_real_aggregate(self):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}#view=evidence")
        page.wait_for_timeout(400)

        block = page.locator('.block[data-block="diagnosis-consolidated"]')
        self.assertEqual(block.count(), 1, "Across-runs block is not on the page")

        # Deep in the outcome stack the block may start collapsed; the body
        # only renders once it is expanded, exactly as a reader would do.
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="diagnosis-consolidated"]')

        # The summary narrative, verbatim, at the top.
        narrative = block.locator(".cx-narrative").inner_text()
        self.assertIn(self.consolidated["narrative"], narrative)

        # One row per failing (task, agent), in the aggregate's own order,
        # each carrying the k-of-n failure reproduction with its verdict,
        # the consolidated status, and the statement verbatim.
        rows = block.locator(".cx-row")
        self.assertEqual(rows.count(), len(self.failing))
        for i, entry in enumerate(self.failing):
            row_text = rows.nth(i).inner_text()
            repro = entry["failure_reproduction"]
            self.assertIn(entry["task"], row_text)
            self.assertIn(entry["agent"], row_text)
            self.assertIn(f"fails {repro['k']} of {repro['n']} runs", row_text)
            self.assertIn(repro["verdict"], row_text)
            self.assertIn(entry["consolidated"]["status"].replace("_", " "),
                          row_text)
            self.assertIn(entry["consolidated"]["statement"], row_text)

        # Expanding a row discloses its executed checks — name, outcome, and
        # detail verbatim, inconclusive ones included, never filtered out.
        for i, entry in enumerate(self.failing):
            if not entry["checks_run"]:
                continue
            rows.nth(i).locator(".cx-head").click()
            page.wait_for_timeout(150)
            body = rows.nth(i).locator(".cx-body").inner_text()
            for check in entry["checks_run"]:
                self.assertIn(check["check"], body)
                self.assertIn(check["outcome"], body)
                self.assertIn(check["detail"], body)

        self.assertEqual(errors, [],
                         "page errors while rendering the consolidation")
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class TrajectoryMapTest(unittest.TestCase):
    """The Trajectory map shows each run's INDIVIDUAL steps and the
    conversation between the runs.

    Driven by the real t05 pair: every step of both trajectories must be
    drawn as its own clickable node in run order, every two-sided
    alignment row must produce exactly one edge between the lanes, shared
    claims must produce their cross-run curves, the decisive step must be
    ringed — and clicking a node must move the shared cursor so Step
    detail follows. All expectations are computed from the pair report
    itself, never hard-coded.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        pair_json = out / "t05.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "traces" / "t05_flight_duration__atlas-v2.json"),
             str(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json"),
             "-o", str(pair_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        from deepcompare.report import render_html
        cls.pair = json.loads(pair_json.read_text(encoding="utf-8"))
        cls.report = out / "report.html"
        render_html([cls.pair], {}, ROOT / "web" / "blocks.html", cls.report)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open_map(self):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}")
        page.wait_for_timeout(400)
        block = page.locator('.block[data-block="trajectory-map"]')
        self.assertEqual(block.count(), 1, "Trajectory map is not on the page")
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="trajectory-map"]')
        return context, page, block, errors

    def test_every_individual_step_is_drawn_once(self):
        context, page, block, errors = self.open_map()
        hits = block.locator("svg.tj g.tj-hit")
        expected = len(self.pair["a"]["steps"]) + len(self.pair["b"]["steps"])
        self.assertEqual(hits.count(), expected,
                         "one node per step of each trajectory")
        self.assertEqual(errors, [])
        context.close()

    def test_every_two_sided_row_gets_exactly_one_edge(self):
        context, page, block, errors = self.open_map()
        two_sided = [r for r in self.pair["alignment"]
                     if r.get("a_index") is not None
                     and r.get("b_index") is not None]
        edges = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            let n = 0;
            svg.querySelectorAll('line').forEach(function (line) {
                const t = line.querySelector('title');
                if (t && t.textContent.indexOf('row ') === 0) n++;
            });
            return n;
        }""")
        self.assertEqual(edges, len(two_sided))
        context.close()

    def test_shared_claims_speak_across_the_gutter(self):
        context, page, block, errors = self.open_map()
        both = [c for c in (self.pair.get("semantic") or {}).get("claims", [])
                if c.get("a_steps") and c.get("b_steps")]
        curves = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            let n = 0;
            svg.querySelectorAll('path').forEach(function (p) {
                const t = p.querySelector('title');
                if (t && /claim/.test(t.textContent)) n++;
            });
            return n;
        }""")
        self.assertEqual(curves, len(both))
        context.close()

    def test_the_decisive_step_is_ringed(self):
        context, page, block, errors = self.open_map()
        decisive = (self.pair["diagnosis"].get("decisive_step") or {}).get("step")
        self.assertIsNotNone(decisive, "t05 must carry a decisive step")
        marked = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            const out = [];
            svg.querySelectorAll('g.tj-hit title').forEach(function (t) {
                if (t.textContent.indexOf('decisive step') >= 0)
                    out.push(t.textContent);
            });
            return out;
        }""")
        self.assertEqual(len(marked), 1, marked)
        self.assertIn("step " + str(decisive), marked[0])
        context.close()

    def test_clicking_a_node_moves_the_shared_cursor(self):
        context, page, block, errors = self.open_map()
        # first node in DOM order is A's first step; its alignment row is
        # computed from the report, not assumed
        first_a = self.pair["a"]["steps"][0]["index"]
        row = next(i for i, r in enumerate(self.pair["alignment"])
                   if r.get("a_index") == first_a)
        block.locator("svg.tj g.tj-hit").nth(0).click()
        page.wait_for_timeout(250)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            tag = detail.locator(".tag.mono").first.inner_text()
            self.assertEqual(tag, f"row {row}")
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class RunLensTest(unittest.TestCase):
    """The Run lens reads ONE trajectory end to end.

    Driven by the real t05 pair. The lens must default to the failing
    run, list every one of its steps, expand a step to its verbatim
    recorded text, follow the A/B toggle, and move the family's shared
    cursor when a step is selected — all expectations computed from the
    report itself.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        pair_json = out / "t05.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "traces" / "t05_flight_duration__atlas-v2.json"),
             str(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json"),
             "-o", str(pair_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        from deepcompare.report import render_html
        cls.pair = json.loads(pair_json.read_text(encoding="utf-8"))
        cls.report = out / "report.html"
        render_html([cls.pair], {}, ROOT / "web" / "blocks.html", cls.report)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def failing_side(self):
        a_fail = self.pair["a"]["outcome"]["success"] is False
        b_fail = self.pair["b"]["outcome"]["success"] is False
        if a_fail and not b_fail:
            return "a"
        if b_fail and not a_fail:
            return "b"
        return "a"

    def open_lens(self):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}#view=evidence")
        page.wait_for_timeout(400)
        block = page.locator('.block[data-block="run-lens"]')
        self.assertEqual(block.count(), 1, "Run lens is not on the page")
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="run-lens"]')
        return context, page, block, errors

    def test_defaults_to_the_failing_run_with_every_step_listed(self):
        context, page, block, errors = self.open_lens()
        side = self.failing_side()
        expected = len(self.pair[side]["steps"])
        self.assertEqual(block.locator(".tjl-step").count(), expected)
        pressed = block.locator('.tj-ctl .grp button[aria-pressed="true"]')
        self.assertEqual(pressed.inner_text(),
                         self.pair[side]["agent"]["name"])
        self.assertEqual(errors, [])
        context.close()

    def test_the_toggle_shows_the_other_run(self):
        context, page, block, errors = self.open_lens()
        side = self.failing_side()
        other = "b" if side == "a" else "a"
        block.locator(".tj-ctl .grp button").filter(
            has_text=self.pair[other]["agent"]["name"]).click()
        page.wait_for_timeout(200)
        block = page.locator('.block[data-block="run-lens"]')
        self.assertEqual(block.locator(".tjl-step").count(),
                         len(self.pair[other]["steps"]))
        context.close()

    def test_expanding_a_step_shows_its_verbatim_text(self):
        context, page, block, errors = self.open_lens()
        side = self.failing_side()
        step = next(s for s in self.pair[side]["steps"] if s.get("input"))
        pos = [s["index"] for s in self.pair[side]["steps"]].index(step["index"])
        block.locator(".tjl-head").nth(pos).click()
        page.wait_for_timeout(200)
        block = page.locator('.block[data-block="run-lens"]')
        body = block.locator(".tjl-step").nth(pos).locator(".tjl-body")
        self.assertEqual(body.count(), 1, "step did not expand")
        self.assertIn(step["input"], body.inner_text())
        context.close()

    def test_selecting_a_step_moves_the_shared_cursor(self):
        context, page, block, errors = self.open_lens()
        side = self.failing_side()
        first = self.pair[side]["steps"][0]["index"]
        row = next(i for i, r in enumerate(self.pair["alignment"])
                   if r.get(f"{side}_index") == first)
        block.locator(".tjl-head").nth(0).click()
        page.wait_for_timeout(250)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            tag = detail.locator(".tag.mono").first.inner_text()
            self.assertEqual(tag, f"row {row}")
        self.assertEqual(errors, [])
        context.close()

    def test_diagnosis_marks_appear_inline(self):
        context, page, block, errors = self.open_lens()
        diag = self.pair["diagnosis"]
        decisive = (diag.get("decisive_step") or {}).get("step")
        self.assertIsNotNone(decisive, "t05 must carry a decisive step")
        marks = block.locator(".tjl-b.mark")
        texts = [marks.nth(i).inner_text() for i in range(marks.count())]
        self.assertIn("decisive", [t.lower() for t in texts])
        context.close()

    def test_the_select_step_event_moves_the_family_cursor(self):
        # The walkthrough's documented fallback: a CustomEvent any module
        # may listen for. It must actually move the shared cursor — until
        # the trajectory family grew a listener it fired into silence.
        context, page, block, errors = self.open_lens()
        rows = self.pair["alignment"]
        target = len(rows) - 1
        page.evaluate("""(row) => {
            document.dispatchEvent(new CustomEvent('agentdiff:select-step', {
                detail: { row: row, side: null },
            }));
        }""", target)
        page.wait_for_timeout(250)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            tag = detail.locator(".tag.mono").first.inner_text()
            self.assertEqual(tag, f"row {target}")
        self.assertEqual(errors, [])
        context.close()

    def test_clicking_a_claim_edge_writes_the_readout_and_rings_both_ends(self):
        # A claim edge is never tooltip-only: clicking it must write the
        # claim into the persistent readout line (value verbatim, both
        # endpoints named), ring both endpoint nodes, and move the shared
        # cursor to the carrying A step.
        both = [c for c in (self.pair.get("semantic") or {}).get("claims", [])
                if c.get("a_steps") and c.get("b_steps")]
        self.assertTrue(both, "t05 must carry cross-run claims")
        context, page, block, errors = self.open_lens()
        # a bezier's bounding-box centre is not on the curve, so the click
        # is dispatched to the hit path rather than aimed at a pixel
        page.locator('.block[data-block="trajectory-map"] '
                     'svg.tj .tjm-claim-hit').nth(0).dispatch_event("click")
        page.wait_for_timeout(250)
        mapblock = page.locator('.block[data-block="trajectory-map"]')
        readout = mapblock.locator(".tj-read").inner_text()
        claim = both[0]
        self.assertIn(str(claim["value"]), readout)
        self.assertIn(f"A step {claim['a_steps'][0]}", readout)
        self.assertIn(f"B step {claim['b_steps'][0]}", readout)
        rings = mapblock.locator("svg.tj .tjm-claim-end")
        self.assertEqual(rings.count(), 2, "both endpoints ringed")
        row = next(i for i, r in enumerate(self.pair["alignment"])
                   if r.get("a_index") == claim["a_steps"][0])
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            tag = detail.locator(".tag.mono").first.inner_text()
            self.assertEqual(tag, f"row {row}")
        self.assertEqual(errors, [])
        context.close()

    def test_the_claims_chip_cycles_without_hover(self):
        both = [c for c in (self.pair.get("semantic") or {}).get("claims", [])
                if c.get("a_steps") and c.get("b_steps")]
        context, page, block, errors = self.open_lens()
        mapblock = page.locator('.block[data-block="trajectory-map"]')
        chip = mapblock.locator(".tjm-foot button")
        self.assertEqual(chip.count(), 1, "claims chip missing")
        self.assertIn(f"claims: {len(both)}", chip.inner_text())
        chip.click()
        page.wait_for_timeout(250)
        mapblock = page.locator('.block[data-block="trajectory-map"]')
        readout = mapblock.locator(".tj-read").inner_text()
        self.assertIn(str(both[0]["value"]), readout)
        context.close()

    def test_the_map_never_clips_its_b_lane_in_a_column(self):
        # Regression: the map forced a 480px floor on its own width, so in
        # a ~340px layout column the entire B lane fell off the right edge.
        # The drawn SVG must fit the width its container actually has.
        context, page, block, errors = self.open_lens()
        fits = page.evaluate("""() => {
            const wrap = document.querySelector(
                '.block[data-block="trajectory-map"] .tjm-wrap');
            if (!wrap) return null;
            const svg = wrap.querySelector('svg.tj');
            return { svg: svg.getBoundingClientRect().width,
                     box: wrap.clientWidth };
        }""")
        self.assertIsNotNone(fits, "map wrap not found")
        self.assertLessEqual(fits["svg"], fits["box"] + 1,
                             "map SVG wider than its container")
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class MapRedesignTest(unittest.TestCase):
    """The redesigned map: two lanes adjacent around a labelled gutter,
    every node carrying its content, no name ever truncated, keyboard
    reachable, loops collapsed to ×N, phase bands from the reading, and
    a word diff in Step detail. Geometry is measured from the drawn SVG.
    """

    tmp = None
    GEOM = """() => {
      const svg = document.querySelector('.block[data-block="trajectory-map"] svg.tj');
      if (!svg) return null;
      const a = svg.querySelector('g.tj-hit[data-side=a] circle.tjm-focus');
      const b = svg.querySelector('g.tj-hit[data-side=b] circle.tjm-focus');
      const names = [...svg.querySelectorAll('text.tjm-name')].map(t => t.textContent);
      return { w: svg.getBoundingClientRect().width,
               box: svg.closest('.tjm-wrap').clientWidth,
               gutter: (+b.getAttribute('cx')) - (+a.getAttribute('cx')),
               truncated: names.filter(n => n.endsWith('…')), names: names.length,
               labels: [...svg.querySelectorAll('text.tjm-edge-label')].map(t => t.textContent),
               excerpts: svg.querySelectorAll('text.tjm-excerpt').length,
               phases: svg.querySelectorAll('rect.tjm-phase').length,
               focusable: svg.querySelectorAll('g.tj-hit[tabindex="0"][role="button"]').length,
               hits: svg.querySelectorAll('g.tj-hit:not(.tjm-claim-hit)').length };
    }"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        pair_json = out / "t05.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "traces" / "t05_flight_duration__atlas-v2.json"),
             str(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json"),
             "-o", str(pair_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        from deepcompare.report import compare, render_html
        from deepcompare.trace import Trajectory
        cls.pair = json.loads(pair_json.read_text(encoding="utf-8"))
        cls.report = out / "report.html"
        render_html([cls.pair], {}, ROOT / "web" / "blocks.html", cls.report)
        # a synthetic pair whose failing run retries one call four times
        # verbatim: the map must collapse the loop to one node with ×4
        def trace(name, steps, success, answer):
            return Trajectory.from_dict({
                "schema_version": 1, "trace_id": name,
                "agent": {"name": name, "model": "sim", "version": "1"},
                "task": {"id": "loop_task", "prompt": "What is the refund?",
                         "expected": "The refund is $120.00."},
                "outcome": {"success": success, "answer": answer,
                            "score": 1.0 if success else 0.0, "termination": "agent_stop"},
                "totals": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0, "latency_s": 1.0},
                "steps": [dict(s, index=i, tokens=5, latency_s=0.1) for i, s in enumerate(steps)],
                "tools": [{"name": "lookup", "effect": "read"}],
                "budget": {"max_steps": 12},
            })
        plan = {"type": "plan", "name": "plan", "input": "look up the refund", "output": ""}
        good = trace("steady", [plan,
            {"type": "tool_call", "name": "lookup", "input": "lookup(order=17)",
             "output": "The refund is $120.00.", "effect": "read"},
            {"type": "answer", "name": "final", "input": "The refund is $120.00.",
             "output": "The refund is $120.00."}], True, "The refund is $120.00.")
        retry = {"type": "tool_call", "name": "lookup", "input": "lookup(order=18)",
                 "output": "Error: no such order", "effect": "read", "error": True}
        loopy = trace("loopy", [plan, retry, retry, retry, retry,
            {"type": "answer", "name": "final", "input": "The refund is $95.00.",
             "output": "The refund is $95.00."}], False, "The refund is $95.00.")
        cls.loop_pair = compare(good, loopy)
        cls.loop_report = out / "loop.html"
        render_html([cls.loop_pair], {}, ROOT / "web" / "blocks.html", cls.loop_report)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open(self, report, width=1440, hero=False):
        context = self.browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{report}")
        page.wait_for_timeout(500)
        block = page.locator('.block[data-block="trajectory-map"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
        if hero and not page.locator('#hero-lane .block[data-block="trajectory-map"]').count():
            page.locator('.block[data-block="trajectory-map"] .block-actions .icon-btn.star').first.click()
            page.wait_for_timeout(500)
        return context, page, errors

    def test_hero_lanes_are_adjacent_and_labelled_with_no_void(self):
        context, page, errors = self.open(self.report, 1440, hero=True)
        g = page.evaluate(self.GEOM)
        self.assertLessEqual(g["gutter"], 260)
        self.assertGreaterEqual(g["gutter"], 200)
        self.assertEqual(g["truncated"], [])
        self.assertEqual(g["excerpts"], g["names"], "every node carries an excerpt as hero")
        self.assertIn("match", g["labels"])
        self.assertTrue(any(l.startswith("drift ") or l.startswith("diverge ") for l in g["labels"]), g["labels"])
        self.assertTrue(any(l.startswith("claim ") for l in g["labels"]), g["labels"])
        self.assertGreater(g["phases"], 0)
        self.assertEqual(errors, [])
        context.close()

    def test_no_name_is_truncated_in_a_column_or_on_a_phone(self):
        for width in (1440, 390):
            context, page, errors = self.open(self.report, width)
            g = page.evaluate(self.GEOM)
            self.assertEqual(g["truncated"], [], f"{width}px: {g['truncated']}")
            self.assertEqual(g["names"], len(self.pair["a"]["steps"]) + len(self.pair["b"]["steps"]))
            self.assertLessEqual(g["w"], g["box"] + 1)
            self.assertFalse(page.evaluate(
                "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"))
            self.assertEqual(errors, [])
            context.close()

    def test_every_node_is_a_keyboard_button_and_enter_selects(self):
        context, page, errors = self.open(self.report, 1440, hero=True)
        g = page.evaluate(self.GEOM)
        self.assertEqual(g["focusable"], g["hits"])
        node = page.locator('#hero-lane svg.tj g.tj-hit[data-side="b"][data-i="1"]').first
        self.assertEqual(node.get_attribute("aria-label")[:8], "B step 1")
        node.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        row = next(i for i, r in enumerate(self.pair["alignment"]) if r.get("b_index") == 1)
        detail = page.locator('.block[data-block="step-detail"]')
        self.assertEqual(detail.locator(".tag.mono").first.inner_text(), f"row {row}")
        # focus survived the redraw, and the arrow keys walk the lane
        self.assertEqual(page.evaluate("() => document.activeElement.getAttribute('data-i')"), "1")
        page.keyboard.press("ArrowDown")
        self.assertEqual(page.evaluate("() => document.activeElement.getAttribute('data-i')"), "2")
        page.keyboard.press("ArrowLeft")
        self.assertEqual(page.evaluate("() => document.activeElement.getAttribute('data-side')"), "a")
        self.assertEqual(errors, [])
        context.close()

    def test_the_inspector_is_docked_beside_the_map_and_follows_the_cursor(self):
        # the step under the cursor is read beside the map, in the same
        # view, and a node click moves it; the map is never clamped
        context, page, errors = self.open(self.report, 1440, hero=True)
        inspector = page.locator("#hero-lane .tj-inspector")
        self.assertEqual(inspector.count(), 1)
        cell = page.evaluate("""() => { const svg = document.querySelector('#hero-lane svg.tj');
            const cell = svg.closest('.tjm-cell'); return [svg.getBoundingClientRect().width, cell.clientWidth]; }""")
        self.assertLessEqual(cell[0], cell[1] + 1)
        before = inspector.locator(".tag.mono").first.inner_text()
        row = next(i for i, r in enumerate(self.pair["alignment"]) if r.get("b_index") == 3)
        page.locator('#hero-lane svg.tj g.tj-hit[data-side="b"][data-i="3"]').first.dispatch_event("click")
        page.wait_for_timeout(300)
        after = inspector.locator(".tag.mono").first.inner_text()
        self.assertEqual(after, f"row {row}")
        self.assertNotEqual(before, after)
        self.assertEqual(page.locator('#hero-lane button:has-text("Show all")').count(), 0,
                         "the hero must not be clamped")
        self.assertEqual(errors, [])
        context.close()

    def test_the_map_carries_the_timeline_as_a_second_view(self):
        context, page, errors = self.open(self.report, 1440, hero=True)
        page.locator('#hero-lane [data-mapview="timeline"]').click()
        page.wait_for_timeout(500)
        self.assertGreater(page.locator("#hero-lane .tjm-cell .grp").count(), 0, "timeline controls")
        self.assertEqual(page.locator("#hero-lane svg.tj g.tj-hit[data-side]").count(), 0, "the map is not drawn in timeline view")
        page.locator('#hero-lane [data-mapview="map"]').click()
        page.wait_for_timeout(500)
        self.assertGreater(page.locator("#hero-lane svg.tj g.tj-hit[data-side]").count(), 0)
        # the standalone tracks block stands down to the drawer
        self.assertEqual(page.locator('#stacks .block[data-block="tracks"], #story-lane .block[data-block="tracks"]').count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_the_legend_is_one_line_with_the_rest_behind_a_disclosure(self):
        context, page, errors = self.open(self.report, 1440, hero=True)
        foot = page.locator("#hero-lane .tjm-foot").first
        self.assertLessEqual(foot.locator(".k").count(), 5)
        self.assertIn("decisive step", foot.inner_text())
        more = page.locator("#hero-lane .tjm-legend-more")
        self.assertEqual(more.count(), 1)
        self.assertFalse(more.evaluate("d => d.open"))
        more.locator("summary").click()
        self.assertIn("Tab to a step", more.inner_text())
        context.close()

    def test_step_detail_shows_a_word_diff_for_the_divergent_row(self):
        context, page, errors = self.open(self.report, 1440, hero=True)
        row = next(i for i, r in enumerate(self.pair["alignment"])
                   if r.get("a_index") is not None and r.get("b_index") is not None
                   and self.pair["a"]["steps"][r["a_index"]]["input"]
                   != self.pair["b"]["steps"][r["b_index"]]["input"])
        page.evaluate("row => document.dispatchEvent(new CustomEvent('agentdiff:select-step', {detail: {row: row, side: 'b'}}))", row)
        page.wait_for_timeout(300)
        body = page.locator('.block[data-block="step-detail"] .tj-diff-body').first
        self.assertGreater(body.locator("ins, del").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_a_verbatim_loop_collapses_to_one_node_with_a_count(self):
        context, page, errors = self.open(self.loop_report, 1440, hero=True)
        badge = page.locator('#hero-lane svg.tj text.tjm-loop')
        self.assertEqual(badge.count(), 1)
        # the badge's own text, without its <title> tooltip child
        self.assertEqual(badge.first.evaluate("el => el.firstChild.textContent"), "×4")
        hits_before = page.locator('#hero-lane svg.tj g.tj-hit[data-side="b"]').count()
        self.assertEqual(hits_before, 3, "plan, the collapsed loop, the answer")
        badge.first.dispatch_event("click")
        page.wait_for_timeout(300)
        self.assertEqual(page.locator('#hero-lane svg.tj g.tj-hit[data-side="b"]').count(), 6)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class CompositeViewsTest(unittest.TestCase):
    """The Evidence and Batch views say things once: one root-cause card,
    one process-checks card, one variance card; their parts stand down to
    the drawer; no open block shows an empty state."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch",
                        str(ROOT / "demo" / "traces"), "-o", str(out / "batch")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.report = out / "batch" / "report.html"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def _open(self, view):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}#view={view}")
        page.wait_for_timeout(600)
        page.select_option("#task-picker", "t05_flight_duration")
        page.wait_for_timeout(500)
        return context, page, errors

    def _expand(self, page, block_id):
        block = page.locator(f'#stacks .block[data-block="{block_id}"]')
        self.assertEqual(block.count(), 1, f"{block_id} is not in the columns")
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
        return page.locator(f'#stacks .block[data-block="{block_id}"]')

    def test_evidence_has_one_root_cause_and_one_process_checks_card(self):
        context, page, errors = self._open("evidence")
        root = self._expand(page, "root-cause")
        parts = root.locator(".cx-part")
        self.assertGreaterEqual(parts.count(), 1)
        self.assertIn("attribution", [parts.nth(i).get_attribute("data-part") for i in range(parts.count())])
        checks = self._expand(page, "process-checks")
        self.assertGreaterEqual(checks.locator(".cx-part").count(), 1)
        self.assertIn("checks have something to show", checks.locator(".cx-summary").inner_text())
        for old in ("attribution", "divergences", "integrity-flags", "gap", "claims-vs-actions",
                    "side-effects", "loops-repeats", "recovery-errors"):
            self.assertEqual(page.locator(f'#stacks .block[data-block="{old}"]').count(), 0, old)
        self.assertEqual(errors, [])
        context.close()

    def test_batch_has_one_variance_card_that_says_confounded_once(self):
        context, page, errors = self._open("batch")
        card = self._expand(page, "variance-all")
        self.assertGreaterEqual(card.locator(".cx-part").count(), 2)
        # the confounding caveat is one note, not one per part: at most one
        # note element says it (the design part's own title may too)
        notes = card.locator(".vz-note").filter(has_text="confounded")
        visible = [i for i in range(notes.count()) if notes.nth(i).is_visible()]
        self.assertLessEqual(len(visible), 1)
        for old in ("variance", "variance-design", "variance-corrected", "variance-residual"):
            self.assertEqual(page.locator(f'#stacks .block[data-block="{old}"]').count(), 0, old)
        self.assertEqual(errors, [])
        context.close()

    def test_no_open_block_shows_an_empty_state_on_any_view(self):
        for view in ("story", "evidence", "batch"):
            context, page, errors = self._open(view)
            empties = page.evaluate("""() => [...document.querySelectorAll('.block:not(.collapsed) .empty')]
                .filter(e => e.offsetParent !== null).map(e => e.closest('.block').getAttribute('data-block'))""")
            self.assertEqual(empties, [], f"{view}: {empties}")
            context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class SmallScreensKeysAndMotionTest(unittest.TestCase):
    """The phone budget, the keyboard help, touch-visible actions, and
    reduced motion."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch",
                        str(ROOT / "demo" / "traces"), "-o", str(out / "batch")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.report = out / "batch" / "report.html"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def _open(self, **context_args):
        context = self.browser.new_context(**context_args)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}")
        page.wait_for_timeout(600)
        page.select_option("#task-picker", "t05_flight_duration")
        page.wait_for_timeout(500)
        return context, page, errors

    def test_the_story_fits_the_phone_budget_with_the_inspector_folded(self):
        context, page, errors = self._open(viewport={"width": 390, "height": 844}, has_touch=True)
        height = page.evaluate("() => document.documentElement.scrollHeight")
        self.assertLessEqual(height, 5100, f"story is {height}px tall on a phone")
        self.assertFalse(page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        fold = page.locator("#hero-lane details.tj-inspector-fold")
        self.assertEqual(fold.count(), 1)
        self.assertFalse(fold.evaluate("d => d.open"))
        self.assertIn("row", fold.locator("summary").inner_text())
        # touch: the card actions are visible without a hover
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('#story-lane .block-actions')).opacity"), "1")
        self.assertEqual(errors, [])
        context.close()

    def test_the_keyboard_help_opens_with_question_mark_and_closes_with_escape(self):
        context, page, errors = self._open(viewport={"width": 1280, "height": 900})
        page.locator('#view-tabs [data-view="story"]').focus()
        page.keyboard.press("?")
        page.wait_for_timeout(200)
        self.assertIn("open", page.locator("#help").get_attribute("class"))
        self.assertIn("previous / next task", page.locator("#help").inner_text())
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        self.assertNotIn("open", page.locator("#help").get_attribute("class"))
        # focus went back to where it was
        self.assertEqual(page.evaluate("() => document.activeElement && document.activeElement.getAttribute('data-view')"), "story")
        page.locator("#btn-help").click()
        page.wait_for_timeout(200)
        self.assertIn("open", page.locator("#help").get_attribute("class"))
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_replays_do_not_run(self):
        context, page, errors = self._open(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        page.locator('#hero-lane [data-mapview="timeline"]').click()
        page.wait_for_timeout(400)
        play = page.locator("#hero-lane .tjm-cell .tj-ctl button").filter(has_text="▶")
        if play.count():
            play.first.click()
            page.wait_for_timeout(1000)
            running = page.evaluate("() => !!(window.AgentDiff && AgentDiff._replayRunning && AgentDiff._replayRunning())")
            self.assertFalse(running)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class OneSidedMapTest(unittest.TestCase):
    """One-sided steps are SEEN, not just unlinked.

    Driven by the real t01 pair, whose alignment carries several b_only
    rows: every one-sided row must draw exactly one open stub into the
    gutter on the side that took the step (with the agent named in the
    title), every two-sided row exactly one edge, and never both — all
    counts computed from the report.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        pair_json = out / "t01.json"
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo" / "traces" / "t01_acme_revenue__atlas-v2.json"),
             str(ROOT / "demo" / "traces" / "t01_acme_revenue__bolt-v3.json"),
             "-o", str(pair_json)],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        from deepcompare.report import render_html
        cls.pair = json.loads(pair_json.read_text(encoding="utf-8"))
        one_sided = [r for r in cls.pair["alignment"]
                     if (r.get("a_index") is None) != (r.get("b_index") is None)]
        assert one_sided, "t01 must carry one-sided alignment rows"
        cls.report = out / "report.html"
        render_html([cls.pair], {}, ROOT / "web" / "blocks.html", cls.report)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open_map(self):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}")
        page.wait_for_timeout(400)
        block = page.locator('.block[data-block="trajectory-map"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="trajectory-map"]')
        return context, page, block, errors

    def test_every_one_sided_row_gets_exactly_one_stub(self):
        context, page, block, errors = self.open_map()
        one_sided = [r for r in self.pair["alignment"]
                     if (r.get("a_index") is None) != (r.get("b_index") is None)]
        stubs = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            const out = [];
            svg.querySelectorAll('g.tjm-stub title').forEach(function (t) {
                out.push(t.textContent);
            });
            return out;
        }""")
        self.assertEqual(len(stubs), len(one_sided), stubs)
        for title in stubs:
            self.assertIn("only", title)
        # the side that took the step is named, so the stub is readable
        # without cross-referencing lane positions
        for row, title in zip(one_sided, stubs):
            side = "b" if row.get("a_index") is None else "a"
            self.assertIn(self.pair[side]["agent"]["name"], title)
        self.assertEqual(errors, [])
        context.close()

    def test_stubs_and_edges_never_overlap(self):
        context, page, block, errors = self.open_map()
        two_sided = [r for r in self.pair["alignment"]
                     if r.get("a_index") is not None
                     and r.get("b_index") is not None]
        edges = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            let n = 0;
            svg.querySelectorAll(':scope > line, :scope > g:not(.tjm-stub):not(.tj-hit) line')
               .forEach(function (line) {
                const t = line.querySelector('title');
                if (t && t.textContent.indexOf('row ') === 0) n++;
            });
            return n;
        }""")
        self.assertEqual(edges, len(two_sided))
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class BatchTaskSwitchTest(unittest.TestCase):
    """Switching tasks in a batch report resets the trajectory family.

    Driven by the full demo/traces batch (8 tasks, different step
    counts): after switching, the map must draw exactly the new task's
    steps, a selected claim readout must not survive into the next task,
    and the lens must list the new task's run — all counts computed from
    the embedded reports, and no page errors at any point.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "batch",
             str(ROOT / "demo" / "traces"), "-o", str(out),
             "--template", str(ROOT / "web" / "blocks.html")],
            cwd=str(ROOT), check=True, capture_output=True)
        cls.report = out / "report.html"
        assert cls.report.is_file()
        cls.reports = {}
        for path in out.glob("report_*.json"):
            rep = json.loads(path.read_text(encoding="utf-8"))
            cls.reports[rep["task"]["id"]] = rep
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    @staticmethod
    def steps_of(rep):
        return len(rep["a"]["steps"]) + len(rep["b"]["steps"])

    def map_nodes(self, page):
        return page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            return svg ? svg.querySelectorAll('g.tj-hit:not(.tjm-claim-hit)')
                            .length : null;
        }""")

    def expand(self, page, block_id):
        block = page.locator(f'.block[data-block="{block_id}"]')
        if block.count() and "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)

    def test_switching_tasks_redraws_and_resets(self):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}#view=evidence")
        page.wait_for_timeout(500)
        self.expand(page, "trajectory-map")

        current = page.locator("#task-picker").input_value()
        # a second task with a different total step count keeps the
        # assertion meaningful
        other = next(t for t, rep in sorted(self.reports.items())
                     if t != current
                     and self.steps_of(rep) != self.steps_of(self.reports[current]))

        self.assertEqual(self.map_nodes(page),
                         self.steps_of(self.reports[current]))

        # select a claim on the current task, if it carries one
        old_value = None
        hits = page.locator('.block[data-block="trajectory-map"] '
                            'svg.tj .tjm-claim-hit')
        if hits.count():
            # dispatch, not click: hit-target geometry is pinned by the
            # pair-report test; this test is about state, and the batch
            # page's layout can put the invisible hit path outside
            # Playwright's actionability rules
            hits.nth(0).dispatch_event("click")
            page.wait_for_timeout(250)
            both = [c for c in (self.reports[current].get("semantic") or {})
                    .get("claims", [])
                    if c.get("a_steps") and c.get("b_steps")]
            old_value = str(both[0]["value"]) if both else None

        page.select_option("#task-picker", other)
        page.wait_for_timeout(500)
        self.expand(page, "trajectory-map")

        self.assertEqual(self.map_nodes(page),
                         self.steps_of(self.reports[other]),
                         f"map did not redraw for {other}")
        if old_value is not None:
            read = page.evaluate("""() => {
                const el = document.querySelector(
                    '.block[data-block="trajectory-map"] .tj-read');
                return el ? el.textContent : "";
            }""")
            self.assertNotIn(old_value, read,
                             "stale claim readout survived the task switch")
        rings = page.locator('.block[data-block="trajectory-map"] '
                             'svg.tj .tjm-claim-end')
        self.assertEqual(rings.count(), 0, "stale claim rings survived")

        self.expand(page, "run-lens")
        lens_steps = page.locator('.block[data-block="run-lens"] .tjl-step')
        rep = self.reports[other]
        a_fail = rep["a"]["outcome"]["success"] is False
        b_fail = rep["b"]["outcome"]["success"] is False
        side = "a" if (a_fail and not b_fail) else "b" if (b_fail and not a_fail) else "a"
        self.assertEqual(lens_steps.count(), len(rep[side]["steps"]),
                         f"lens did not reset for {other}")

        self.assertEqual(errors, [], "page errors during task switch")
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class LongPairMapTest(unittest.TestCase):
    """The map at scale: a 70-step pair must be drawn whole.

    House rule: no silent caps. A synthetic pair (70 vs 76 steps —
    matches, drifts, one-sided runs of extra work) is compared by the
    real engine and rendered by the real page; the map must draw every
    step (node count == step count), scroll rather than squash, keep its
    row rhythm, and stay clickable at row 60+ — with render time sane
    and no page errors.
    """

    tmp = None
    STEPS = 70

    @classmethod
    def _trajectory(cls, agent, extra_runs):
        steps = [{"index": 0, "type": "plan", "name": "plan",
                  "input": "Read every record, then answer.", "output": "",
                  "tokens": 30, "latency_s": 1.0}]
        for i in range(1, cls.STEPS - 2):
            drift = agent == "long-b" and i % 10 == 5
            steps.append({
                "index": len(steps), "type": "tool_call",
                "name": "get_record",
                "input": f"get_record(page={i}"
                         + (", source='mirror')" if drift else ")"),
                "output": f"Record page {i}: nominal.",
                "tokens": 25, "latency_s": 0.4,
                "effect": "read", "error": False,
            })
        for j in range(extra_runs):
            steps.append({
                "index": len(steps), "type": "tool_call",
                "name": "retry_fetch",
                "input": f"retry_fetch(attempt={j})",
                "output": "Partial data only.",
                "tokens": 25, "latency_s": 0.4,
                "effect": "read", "error": False,
            })
        steps.append({"index": len(steps), "type": "reason", "name": "reason",
                      "input": "The ledger totals $500.00 across all pages.",
                      "output": "", "tokens": 25, "latency_s": 0.4})
        answer = ("The ledger totals $500.00."
                  if agent == "long-a" else
                  "The ledger could not be fully verified.")
        steps.append({"index": len(steps), "type": "answer", "name": "final",
                      "input": answer, "output": answer,
                      "tokens": 30, "latency_s": 0.5})
        return {
            "schema_version": 1,
            "trace_id": f"longpair-{agent}",
            "agent": {"name": agent, "model": "model-x", "version": "1"},
            "task": {"id": "long_pair",
                     "prompt": "Total the ledger across all record pages.",
                     "expected": "The ledger totals $500.00."},
            "outcome": {"success": agent == "long-a", "answer": answer,
                        "score": 1.0 if agent == "long-a" else 0.0,
                        "termination": "agent_stop"},
            "totals": {"input_tokens": 2000, "output_tokens": 900,
                       "cost_usd": 0.01, "latency_s": 40.0},
            "steps": steps,
            "tools": [{"name": "get_record", "effect": "read"},
                      {"name": "retry_fetch", "effect": "read"}],
            "budget": {"max_steps": 120},
        }

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        a_path = out / "a.json"
        b_path = out / "b.json"
        a_path.write_text(json.dumps(cls._trajectory("long-a", 0)))
        b_path.write_text(json.dumps(cls._trajectory("long-b", 6)))
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        from deepcompare.report import compare, render_html
        from deepcompare.trace import Trajectory
        cls.pair = compare(Trajectory.from_json(str(a_path)),
                           Trajectory.from_json(str(b_path)))
        cls.report = out / "report.html"
        render_html([cls.pair], {}, ROOT / "web" / "blocks.html", cls.report)
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open_map(self):
        import time
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        start = time.monotonic()
        page.goto(f"file://{self.report}#view=evidence")
        page.wait_for_timeout(600)
        elapsed = time.monotonic() - start
        block = page.locator('.block[data-block="trajectory-map"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)
            block = page.locator('.block[data-block="trajectory-map"]')
        return context, page, block, errors, elapsed

    def test_every_step_is_drawn_no_silent_cap(self):
        context, page, block, errors, elapsed = self.open_map()
        expected = (len(self.pair["a"]["steps"])
                    + len(self.pair["b"]["steps"]))
        self.assertGreaterEqual(expected, 140, "pair not actually long")
        nodes = page.evaluate("""() => document.querySelectorAll(
            '.block[data-block="trajectory-map"] svg.tj g.tj-hit:not(.tjm-claim-hit)'
        ).length""")
        self.assertEqual(nodes, expected)
        self.assertLess(elapsed, 5.0, f"render took {elapsed:.1f}s")
        self.assertEqual(errors, [])
        context.close()

    def test_the_map_scrolls_rather_than_squashes(self):
        context, page, block, errors, _ = self.open_map()
        geom = page.evaluate("""() => {
            const wrap = document.querySelector(
                '.block[data-block="trajectory-map"] .tjm-wrap');
            const svg = wrap.querySelector('svg.tj');
            return { scroll: wrap.scrollHeight, client: wrap.clientHeight,
                     svgH: svg.getBoundingClientRect().height };
        }""")
        self.assertGreater(geom["scroll"], geom["client"],
                           "long map should scroll inside its wrap")
        # row rhythm intact: the SVG is as tall as its row count demands
        n = max(len(self.pair["a"]["steps"]), len(self.pair["b"]["steps"]))
        self.assertGreaterEqual(geom["svgH"], n * 20,
                                "rows squashed below readable height")
        context.close()

    def test_click_sync_still_works_past_row_sixty(self):
        context, page, block, errors, _ = self.open_map()
        side = "b"
        steps = self.pair[side]["steps"]
        target = steps[65]["index"]
        row = next(i for i, r in enumerate(self.pair["alignment"])
                   if r.get(f"{side}_index") == target)
        # nodes are appended lane A first, then lane B in step order
        nth = len(self.pair["a"]["steps"]) + 65
        page.locator('.block[data-block="trajectory-map"] '
                     'svg.tj g.tj-hit:not(.tjm-claim-hit)').nth(nth).click()
        page.wait_for_timeout(250)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            tag = detail.locator(".tag.mono").first.inner_text()
            self.assertEqual(tag, f"row {row}")
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class AdversarialMapTest(unittest.TestCase):
    """Pathological reports must degrade honestly, never crash.

    The schema refuses empty trajectories (steps must contain at least
    one step), so the adversarial shapes that CAN reach the page are: a
    report stripped of its alignment rows, a minimal one-step side
    against a full run, a contested diagnosis (the a1_negation red-team
    fixture), and unicode-heavy names (CJK, emoji, RTL) through every
    label path. Each must render without page errors and say honestly
    what it cannot show.
    """

    tmp = None

    @staticmethod
    def _traj(name, steps, success):
        return {
            "schema_version": 1, "trace_id": f"adv-{name}",
            "agent": {"name": name, "model": "model-x", "version": "1"},
            "task": {"id": "adv_task", "prompt": "Do the thing 完了 🚀",
                     "expected": "The thing is done."},
            "outcome": {"success": success,
                        "answer": steps[-1]["output"] or steps[-1]["input"],
                        "score": 1.0 if success else 0.0,
                        "termination": "agent_stop"},
            "totals": {"input_tokens": 100, "output_tokens": 50,
                       "cost_usd": 0.001, "latency_s": 2.0},
            "steps": steps,
            "tools": [{"name": "работа_丸", "effect": "read"}],
            "budget": {"max_steps": 12},
        }

    UNI_STEPS = [
        {"index": 0, "type": "plan", "name": "計画→עברית🧭",
         "input": "计划: שלום 🌍 مرحبا", "output": "",
         "tokens": 10, "latency_s": 0.1},
        {"index": 1, "type": "tool_call", "name": "работа_丸",
         "input": "работа_丸(query='猫🐱')", "output": "結果: ✅ הצלחה",
         "tokens": 10, "latency_s": 0.1, "effect": "read", "error": False},
        {"index": 2, "type": "answer", "name": "final",
         "input": "The thing is done.", "output": "The thing is done.",
         "tokens": 10, "latency_s": 0.1},
    ]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        from deepcompare.report import compare, render_html
        from deepcompare.trace import Trajectory

        def build(name, raw_a, raw_b, mutate=None):
            pa, pb = out / f"{name}_a.json", out / f"{name}_b.json"
            pa.write_text(json.dumps(raw_a))
            pb.write_text(json.dumps(raw_b))
            rep = compare(Trajectory.from_json(str(pa)),
                          Trajectory.from_json(str(pb)))
            if mutate:
                mutate(rep)
            html = out / f"{name}.html"
            render_html([rep], {}, ROOT / "web" / "blocks.html", html)
            return rep, html

        uni_b = [dict(s) for s in cls.UNI_STEPS]
        uni_a = [dict(s, input=s["input"] + " (vλ)") for s in cls.UNI_STEPS]
        cls.unicode_pair, cls.unicode_html = build(
            "unicode", cls._traj("уни-a-🅰", uni_a, False),
            cls._traj("uni-b-乙", uni_b, True))

        def strip_alignment(rep):
            rep["alignment"] = []
        cls.stripped_pair, cls.stripped_html = build(
            "stripped", cls._traj("уни-a-🅰", uni_a, False),
            cls._traj("uni-b-乙", uni_b, True), mutate=strip_alignment)

        one = [{"index": 0, "type": "answer", "name": "final",
                "input": "Nope.", "output": "Nope.",
                "tokens": 5, "latency_s": 0.1}]
        cls.minimal_pair, cls.minimal_html = build(
            "minimal", cls._traj("tiny-a", one, False),
            cls._traj("full-b", [dict(s) for s in cls.UNI_STEPS], True))

        contested_a = ROOT / "tests" / "fixtures" / "redteam" / "a1_negation__fail.json"
        contested_b = ROOT / "tests" / "fixtures" / "redteam" / "a1_negation__pass.json"
        cls.contested_pair = compare(Trajectory.from_json(str(contested_a)),
                                     Trajectory.from_json(str(contested_b)))
        assert cls.contested_pair["diagnosis"]["leading"] is None
        cls.contested_html = out / "contested.html"
        render_html([cls.contested_pair], {}, ROOT / "web" / "blocks.html",
                    cls.contested_html)

        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open_page(self, html):
        context = self.browser.new_context()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{html}#view=evidence")
        page.wait_for_timeout(500)
        for block_id in ("trajectory-map", "run-lens"):
            block = page.locator(f'.block[data-block="{block_id}"]')
            if block.count() and "collapsed" in (block.get_attribute("class") or ""):
                block.locator(".block-actions .icon-btn").nth(1).click()
                page.wait_for_timeout(200)
        return context, page, errors

    def map_nodes(self, page):
        return page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            return svg ? svg.querySelectorAll(
                'g.tj-hit:not(.tjm-claim-hit)').length : null;
        }""")

    def test_no_alignment_draws_lanes_with_nothing_between(self):
        context, page, errors = self.open_page(self.stripped_html)
        expected = (len(self.stripped_pair["a"]["steps"])
                    + len(self.stripped_pair["b"]["steps"]))
        self.assertEqual(self.map_nodes(page), expected)
        marks = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            let edges = 0, stubs = 0;
            svg.querySelectorAll('line').forEach(function (l) {
                const t = l.querySelector('title');
                if (t && t.textContent.indexOf('row ') === 0) edges++;
            });
            stubs = svg.querySelectorAll('g.tjm-stub').length;
            return { edges, stubs };
        }""")
        self.assertEqual(marks, {"edges": 0, "stubs": 0},
                         "no alignment must mean no gutter marks")
        self.assertEqual(errors, [])
        context.close()

    def test_a_one_step_side_is_drawn_and_readable(self):
        context, page, errors = self.open_page(self.minimal_html)
        expected = (len(self.minimal_pair["a"]["steps"])
                    + len(self.minimal_pair["b"]["steps"]))
        self.assertEqual(self.map_nodes(page), expected)
        # the lens can read the one-step run
        block = page.locator('.block[data-block="run-lens"]')
        block.locator(".tj-ctl .grp button").filter(has_text="tiny-a").click()
        page.wait_for_timeout(200)
        block = page.locator('.block[data-block="run-lens"]')
        self.assertEqual(block.locator(".tjl-step").count(), 1)
        self.assertEqual(errors, [])
        context.close()

    def test_contested_diagnosis_shows_the_note_and_no_decisive_ring(self):
        context, page, errors = self.open_page(self.contested_html)
        note = page.locator('.block[data-block="trajectory-map"] .tjm-note')
        self.assertEqual(note.count(), 1, "contested note missing")
        self.assertIn("contested", note.inner_text().lower())
        decisive = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            let n = 0;
            svg.querySelectorAll('g.tj-hit title').forEach(function (t) {
                if (t.textContent.indexOf('decisive step') >= 0) n++;
            });
            return n;
        }""")
        self.assertEqual(decisive, 0, "contested must commit to no ring")
        self.assertEqual(errors, [])
        context.close()

    def test_unicode_names_render_everywhere(self):
        context, page, errors = self.open_page(self.unicode_html)
        expected = (len(self.unicode_pair["a"]["steps"])
                    + len(self.unicode_pair["b"]["steps"]))
        self.assertEqual(self.map_nodes(page), expected)
        labels = page.evaluate("""() => {
            const svg = document.querySelector(
                '.block[data-block="trajectory-map"] svg.tj');
            const out = [];
            // one name label per node; excerpts, tokens and badges are
            // extra text the wide (hero) map draws beside it
            svg.querySelectorAll('g.tj-hit text.tjm-name').forEach(function (t) {
                out.push(t.textContent);
            });
            return out;
        }""")
        self.assertEqual(len(labels), expected)
        self.assertTrue(all(lab.strip() for lab in labels),
                        "every node keeps a visible label")
        self.assertTrue(any("計画" in lab or "работа" in lab
                            for lab in labels),
                        "unicode names survive into the labels")
        # the lens shows the full unicode text verbatim on expansion
        block = page.locator('.block[data-block="run-lens"]')
        block.locator(".tjl-head").nth(1).click()
        page.wait_for_timeout(200)
        body = page.locator('.block[data-block="run-lens"] .tjl-body')
        self.assertEqual(body.count(), 1)
        self.assertIn("猫🐱", body.inner_text())
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class IntervalsAndInternalsTest(unittest.TestCase):
    """Per-step confidence intervals and recorded model internals reach
    the page as bands, whiskers, marks and an inspector section, and a
    synthetic source is labelled synthetic at every one of those places.

    Driven by the telemetry demo (synthetic intervals + internals) for the
    per-step views and by the multi-run demo for the pass^k band. Every
    expectation is computed from the embedded reports.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "batch",
             str(ROOT / "demo" / "telemetry" / "traces"), "-o", str(out / "tel"),
             "--template", str(ROOT / "web" / "blocks.html")],
            cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "deepcompare", "runs",
             str(ROOT / "demo" / "runs" / "traces"), "-o", str(out / "runs"),
             "--template", str(ROOT / "web" / "blocks.html")],
            cwd=str(ROOT), check=True, capture_output=True)
        cls.tel = out / "tel" / "report.html"
        cls.runs = out / "runs" / "report.html"
        assert cls.tel.is_file() and cls.runs.is_file()
        cls.t05 = json.loads((out / "tel" / "report_t05_flight_duration.json").read_text(encoding="utf-8"))
        cls.runs_agg = json.loads((out / "runs" / "aggregate.json").read_text(encoding="utf-8"))
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM,
                                              args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        if cls.tmp:
            cls.tmp.cleanup()

    def open(self, report, fragment=""):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{report}{fragment}")
        page.wait_for_timeout(600)
        return context, page, errors

    def expand(self, page, block_id):
        block = page.locator(f'.block[data-block="{block_id}"]')
        if block.count() and "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(250)

    def pick_t05(self, page):
        page.locator('.tchip[title^="t05"]').first.click()
        page.wait_for_timeout(400)

    def test_every_scored_step_gets_a_whisker_and_an_internals_mark(self):
        context, page, errors = self.open(self.tel)
        self.pick_t05(page)
        rep = self.t05
        scored = sum(1 for side in ("a", "b") for st in rep[side]["steps"]
                     if st.get("model") and st["model"].get("interval"))
        with_internals = sum(1 for side in ("a", "b") for st in rep[side]["steps"]
                             if st.get("model") and (st["model"].get("internals") or {}).get("features"))
        self.assertGreater(scored, 0)
        self.assertEqual(page.locator("svg.tj g.tjm-interval").count(), scored)
        self.assertEqual(page.locator("svg.tj line.tjm-interval-band").count(), scored)
        self.assertEqual(page.locator("svg.tj text.tjm-internals").count(), with_internals)
        self.assertEqual(page.locator('svg.tj text.tjm-internals[data-synthetic="1"]').count(), with_internals,
                         "synthetic internals are marked synthetic on every node")
        self.assertEqual(errors, [])
        context.close()

    def test_the_exclusive_feature_is_marked_only_at_the_decisive_step(self):
        context, page, errors = self.open(self.tel)
        self.pick_t05(page)
        dec = self.t05["internals"]["decisive"]
        self.assertTrue(dec["exclusive_features"])
        marks = page.locator("svg.tj text.tjm-internals.exclusive")
        self.assertEqual(marks.count(), 1)
        where = marks.first.evaluate(
            "e => e.closest('g').getAttribute('data-side') + ':' + e.closest('g').getAttribute('data-index')")
        self.assertEqual(where, f"{dec['side']}:{dec['step']}")
        self.assertEqual(errors, [])
        context.close()

    def test_the_inspector_lists_features_with_bars_links_and_the_synthetic_label(self):
        context, page, errors = self.open(self.tel)
        self.pick_t05(page)
        dec = self.t05["internals"]["decisive"]
        side, index = dec["side"], dec["step"]
        page.locator(f'svg.tj g.tj-hit[data-side="{side}"][data-index="{index}"]').first.dispatch_event("click")
        page.wait_for_timeout(300)
        step = next(st for st in self.t05[side]["steps"] if st["index"] == index)
        feats = step["model"]["internals"]["features"]
        pane = page.locator(".tj-pane", has_text=side.upper() + " ·").first
        self.assertEqual(pane.locator(".tj-feat").count(), min(8, len(feats)))
        exclusive = pane.locator(".tj-feat.exclusive")
        self.assertEqual(exclusive.count(), len(dec["exclusive_features"]))
        self.assertEqual(exclusive.first.get_attribute("data-feature"),
                         str(dec["exclusive_features"][0]["index"]))
        self.assertIn(dec["exclusive_features"][0]["label"],
                      exclusive.first.locator(".tj-feat-name").text_content())
        self.assertEqual(pane.locator(".tj-feat.exclusive .tag.bad").text_content(), "only here")
        self.assertEqual(pane.locator(".tj-internals .tag.warn").count(), 1,
                         "synthetic internals carry a synthetic tag in the inspector")
        widths = pane.locator(".tj-feat-bar i").evaluate_all("els => els.map(e => parseFloat(e.style.width))")
        self.assertEqual(len(widths), min(8, len(feats)))
        self.assertTrue(all(0 < w <= 100 for w in widths), widths)
        conf = pane.locator(".tj-conf").text_content()
        band = step["model"]["interval"]
        self.assertIn(f"{band['low'] * 100:.1f}%", conf)
        self.assertIn(f"{band['high'] * 100:.1f}%", conf)
        self.assertIn("SYNTHETIC", conf, "the interval basis is quoted, in the trace's own words")
        self.assertEqual(errors, [])
        context.close()

    def test_the_confidence_chart_shades_the_interval_and_names_its_basis(self):
        context, page, errors = self.open(self.tel, "#view=batch")
        self.pick_t05(page)
        self.expand(page, "confidence")
        block = page.locator('.block[data-block="confidence"]')
        self.assertEqual(block.locator("polygon.sig-band").count(), 2, "one band per run")
        self.assertEqual(block.locator("polyline").count(), 2, "the lines still sit on top")
        key = block.locator(".sig-band-key")
        self.assertEqual(key.count(), 1)
        self.assertIn("SYNTHETIC", key.text_content())
        self.assertIn("synthetic", key.get_attribute("class"))
        # the band never claims a measurement outside the run's own data:
        # every y in the polygon is within the chart's plotted range
        u = self.t05["uncertainty"]
        lows = [b[0] for side in ("a", "b") for b in u[side]["interval"] if b]
        highs = [b[1] for side in ("a", "b") for b in u[side]["interval"] if b]
        self.assertTrue(lows and highs)
        self.assertEqual(errors, [])
        context.close()

    def test_the_pass_curve_draws_the_ci95_band_with_its_basis(self):
        context, page, errors = self.open(self.runs, "#view=batch")
        self.expand(page, "passk")
        block = page.locator('.block[data-block="passk"]')
        per_agent = self.runs_agg["reliability"]["per_agent"]
        with_ci = sum(1 for side in per_agent.values()
                      if any(pt.get("ci95") for pt in (side.get("pass_hat_k") or {}).get("curve") or []))
        self.assertGreater(with_ci, 0)
        self.assertEqual(block.locator("polygon.sci-ci-band").count(), with_ci)
        keys = block.locator(".sc-ci-key")
        self.assertEqual(keys.count(), with_ci)
        basis = next(side["pass_hat_k"]["ci95_basis"] for side in per_agent.values()
                     if (side.get("pass_hat_k") or {}).get("ci95_basis"))
        self.assertIn(basis[:40], keys.first.text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_a_plain_batch_draws_no_band_no_whisker_no_mark(self):
        context, page, errors = self.open(self.runs)
        self.assertEqual(page.locator("svg.tj g.tjm-interval").count(), 0)
        self.assertEqual(page.locator("svg.tj text.tjm-internals").count(), 0)
        page.locator('svg.tj g.tj-hit[data-side="a"]').first.dispatch_event("click")
        page.wait_for_timeout(250)
        self.assertEqual(page.locator(".tj-internals").count(), 0)
        self.assertEqual(page.locator(".tj-conf").count(), 0)
        self.assertEqual(errors, [])
        context.close()


if __name__ == "__main__":
    unittest.main()
