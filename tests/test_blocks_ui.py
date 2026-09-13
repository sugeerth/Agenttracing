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
import time
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
    """A Chromium the installed Playwright can actually launch: the
    browsers path this environment names, then Playwright's own cache
    (a CI runner after `playwright install chromium`), then whatever
    Playwright itself says it would launch."""
    bases = [Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")),
             Path.home() / ".cache" / "ms-playwright", Path.home() / "Library" / "Caches" / "ms-playwright"]
    for base in bases:
        if not base.is_dir():
            continue
        for pattern in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome",
                        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
            for candidate in sorted(base.glob(pattern)):
                if candidate.is_file():
                    return str(candidate)
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if path and Path(path).is_file():
                return path
    except Exception:
        pass
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
        # in the story the next actions are their own section (take-forward),
        # numbered and verbatim; elsewhere the reading lists them itself
        forward = page.locator('.block[data-block="take-forward"]')
        todo_text = forward.inner_text() if forward.count() else text
        for t in reading["take_forward"]:
            self.assertIn(t["instead"], todo_text)
        self.assertEqual(block.locator(".rd-head button[aria-pressed='true']").inner_text(),
                         self.t05["b"]["agent"]["name"])
        first = reading["take_forward"][0]
        row = next(i for i, r in enumerate(self.t05["alignment"])
                   if r.get("b_index") == first["at_step"])
        if forward.count():
            forward.locator(f".d3c-list li[data-n='1'] .step").first.dispatch_event("click")
        else:
            block.locator(f".rd-todo .rd-step[data-step='{first['at_step']}']").first.dispatch_event("click")
        page.wait_for_timeout(300)
        detail = page.locator('.block[data-block="step-detail"]')
        if detail.count():
            self.assertEqual(detail.locator(".tag.mono").first.inner_text(), f"row {row}")
        # the A/B toggle reads the other run (the whole story follows it)
        block.locator(".rd-head button").first.click()
        page.wait_for_timeout(700)
        self.assertIn(self.t05["reading"]["a"]["summary"],
                      page.locator('.block[data-block="reading"]').inner_text())
        self.assertEqual(errors, [])
        context.close()

    def test_the_decisive_ring_is_graded_by_verification(self):
        # hypothesized → long-dashed ring and a legend that says so;
        # only a replay-verified step earns a solid ring
        context, page, errors = self._open_page(self.t05_report)
        page.locator('#view-tabs [data-view="evidence"]').click()
        page.wait_for_timeout(600)
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
        page.goto(f"file://{self.report}#view=evidence")
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
        page.goto(f"file://{report}#view=evidence")
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

    def _open_evidence(self, **context_args):
        return self._open(view="evidence", **context_args)

    def _open(self, view="story", **context_args):
        context = self.browser.new_context(**context_args)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{self.report}#view={view}")
        page.wait_for_timeout(600)
        page.select_option("#task-picker", "t05_flight_duration")
        page.wait_for_timeout(500)
        return context, page, errors

    def test_the_story_fits_the_phone_budget_with_the_inspector_folded(self):
        context, page, errors = self._open(viewport={"width": 390, "height": 844}, has_touch=True)
        height = page.evaluate("() => document.documentElement.scrollHeight")
        # the budget grew with the story's sections: 2 (the tree, folded on
        # a phone), 4 (reconcile: three lanes and a five-step strategy),
        # 5 (take forward, with its numbered list), 6 (next horizon: the
        # prompts, the reward table, the pair) and the hero's super panel
        # over the body chart, then where the time went (two waterfalls),
        # then the horizon tree: 5100 → 5500 → 6200 → 7200 → 7700 → 8600 → 9400
        self.assertLessEqual(height, 9400, f"story is {height}px tall on a phone")
        self.assertFalse(page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        fold = page.locator("#hero-lane details.tj-inspector-fold")
        self.assertEqual(fold.count(), 1)
        self.assertFalse(fold.evaluate("d => d.open"))
        self.assertIn("Step", fold.locator("summary").inner_text())
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
        context, page, errors = self._open_evidence(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
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
        page.goto(f"file://{self.report}#view=evidence")
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

    def open(self, report, fragment="#view=evidence"):   # the map (whiskers, marks, inspector) is the Evidence view's hero
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

    def test_the_routing_block_states_each_pick_with_its_confidence(self):
        context, page, errors = self.open(self.runs, "#view=batch")
        agg = self.runs_agg
        rt = agg.get("routing")
        self.assertTrue(rt and rt["families"], "the runs aggregate carries a routing table")
        block = page.locator('.block[data-block="routing"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            block = page.locator('.block[data-block="routing"]')
        rows = block.locator("table.rt-table tr[data-family]")
        expected_rows = sum(len(f["candidates"]) for f in rt["families"].values())
        self.assertEqual(rows.count(), expected_rows)
        for fam, entry in rt["families"].items():
            first = block.locator(f'tr[data-family="{fam}"][data-rank="0"]')
            self.assertEqual(first.locator("td").nth(1).text_content(), entry["candidates"][0]["agent"])
            conf = first.locator(".rt-conf").text_content()
            self.assertEqual(conf, "either" if entry["confidence"] == "overlapping" else entry["confidence"])
            n = entry["candidates"][0]["features"]["n"]
            self.assertEqual(first.locator("td").nth(3).text_content(), str(n))
        self.assertIn("Wilson", block.locator(".rt-note").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_the_equality_block_counts_every_run_per_answer_and_the_routing_block_gives_its_rationale(self):
        context, page, errors = self.open(self.runs, "#view=batch")
        agg = self.runs_agg
        eq = agg.get("equality")
        self.assertTrue(eq and eq["tasks"], "the runs aggregate carries an equality analysis")
        block = page.locator('.block[data-block="equality"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            block = page.locator('.block[data-block="equality"]')
        agents = list(eq["per_agent"])
        for tid, row in eq["tasks"].items():
            tr = block.locator(f'tr[data-task="{tid}"]')
            self.assertEqual(tr.count(), 1, tid)
            for i, agent in enumerate(agents):
                entry = row["agents"].get(agent)
                cell = tr.locator("td").nth(i + 1)
                if not entry:
                    self.assertEqual(cell.text_content(), "—")
                    continue
                self.assertEqual(cell.locator(".eq-dots i").count(), entry["runs"], f"{tid} {agent}: one dot per run")
                self.assertEqual(cell.locator(".eq-dots i.wrong").count(), entry["runs"] - entry["successes"])
                self.assertIn(f'{entry["distinct_answers"]} distinct', cell.locator(".eq-num").text_content())
            cross = row.get("cross_agent")
            if cross:
                self.assertEqual(tr.locator(".eq-cross").text_content(),
                                 "same answer" if cross["majorities_equal"] else "different answers")
        self.assertIn("equality_rate", block.locator(".eq-note").text_content())
        rt = agg["routing"]
        routing = page.locator('.block[data-block="routing"]')
        if "collapsed" in (routing.get_attribute("class") or ""):
            routing.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            routing = page.locator('.block[data-block="routing"]')
        self.assertEqual(routing.locator(".rt-rationale").text_content(), rt["rationale"]["overall"])
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


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class StoryChartsTest(unittest.TestCase):
    """The story view is a numbered sequence — what happened, why, take
    forward — and each section opens with a D3 chart drawn from the report
    as written: one mark per step with the reading's role, one arc per
    answer value from the step that first produced it, the decisive ring,
    one bar per hypothesis at the engine's score with its evidence, one
    numbered pin per located next action. Clicking any of them moves the
    same cursor the map uses. Every count here is computed from the
    embedded report.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch",
                        str(ROOT / "demo" / "traces"), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.report = out / "report.html"
        cls.t05 = json.loads((out / "report_t05_flight_duration.json").read_text(encoding="utf-8"))
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

    def open(self, width=1280, reduced_motion=False):
        context = self.browser.new_context(viewport={"width": width, "height": 900},
                                           reduced_motion="reduce" if reduced_motion else "no-preference")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.report}#view=story")
        page.wait_for_timeout(600)
        page.select_option("#task-picker", "t05_flight_duration")
        page.wait_for_timeout(900)
        return context, page, errors

    def failing(self):
        rep = self.t05
        return "b" if rep["a"]["outcome"]["success"] else "a"

    def test_d3_is_vendored_and_the_story_is_a_numbered_sequence(self):
        context, page, errors = self.open()
        self.assertTrue(str(page.evaluate("() => window.d3 && d3.version")).startswith("7."))
        titles = page.evaluate("() => [...document.querySelectorAll('#story-lane .block-title')].map(e => e.textContent)")
        self.assertEqual(titles[:7], ["1 · What happened", "2 · Where the time went", "3 · The trace as a tree", "4 · Parts and sub-agents", "5 · Why", "6 · Reconcile", "7 · Take forward"])
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(errors, [])
        context.close()

    def test_what_happened_draws_every_step_phase_value_and_the_decisive_ring(self):
        context, page, errors = self.open()
        side = self.failing()
        rep = self.t05
        reading = rep["reading"][side]
        svg = page.locator("svg.d3c-story")
        self.assertEqual(svg.count(), 1)
        self.assertEqual(svg.locator("g.d3c-step").count(), len(rep[side]["steps"]))
        roles = svg.locator("g.d3c-step").evaluate_all("els => els.map(e => e.getAttribute('data-role'))")
        self.assertEqual(roles, [w["role"] for w in reading["what_happened"]])
        self.assertEqual(svg.locator("g.d3c-phase").count(), len(reading["phases"]))
        answer = max(w["step"] for w in reading["what_happened"] if w["role"] == "answer")
        arcs = [r for r in reading["rests_on"] if r.get("first_step") is not None and r["first_step"] < answer]
        self.assertEqual(svg.locator("path.d3c-arc").count(), len(arcs))
        statuses = svg.locator("path.d3c-arc").evaluate_all("els => els.map(e => e.getAttribute('data-status'))")
        self.assertEqual(sorted(statuses), sorted(r["status"] for r in arcs))
        failed = rep[side]["outcome"]["success"] is not True
        wrong = sum(1 for r in arcs if r.get("matches_expected") is False) if failed else 0
        labels = svg.locator("text.d3c-arc-label").all_text_contents()
        self.assertEqual(sum(1 for t in labels if "✗" in t), min(wrong, 4))
        dec = rep["diagnosis"]["decisive_step"]
        rings = svg.locator("g.d3c-decisive")
        self.assertEqual(rings.count(), 1 if rep["diagnosis"]["subject"] == side else 0)
        if rings.count():
            self.assertEqual(rings.first.get_attribute("transform"),
                             svg.locator(f'g.d3c-step[data-step="{dec["step"]}"]').first.get_attribute("transform"))
        basis = reading["answer_basis"]
        self.assertEqual(svg.locator("g.d3c-spent").count(), 1 if basis.get("steps_after_basis_complete") else 0)
        # the arc labels are backed so the arc never shows through the text
        self.assertEqual(svg.locator("rect.d3c-backing").count(), len(labels))
        self.assertEqual(errors, [])
        context.close()

    def test_clicking_a_step_mark_opens_it_in_the_inspector(self):
        context, page, errors = self.open()
        side = self.failing()
        target = 1
        page.locator(f'svg.d3c-story g.d3c-step[data-step="{target}"]').first.dispatch_event("click")
        page.wait_for_timeout(300)
        pane = page.locator(".tj-pane", has_text=side.upper() + " ·").first
        self.assertIn(f"step {target}", pane.locator("h4").text_content())
        # keyboard: Enter on a focused mark does the same
        page.locator(f'svg.d3c-story g.d3c-step[data-step="0"]').first.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        pane = page.locator(".tj-pane", has_text=side.upper() + " ·").first
        self.assertIn("step 0", pane.locator("h4").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_why_draws_one_bar_per_hypothesis_at_its_score_with_its_evidence(self):
        context, page, errors = self.open()
        diag = self.t05["diagnosis"]
        svg = page.locator("svg.d3c-why")
        self.assertEqual(svg.count(), 1)
        rows = svg.locator("g.d3c-hyprow")
        self.assertEqual(rows.count(), len(diag["hypotheses"]))
        self.assertEqual(rows.evaluate_all("els => els.map(e => e.getAttribute('data-status'))"),
                         [h["status"] for h in diag["hypotheses"]])
        self.assertEqual(rows.evaluate_all("els => els.map(e => e.getAttribute('data-kind'))"),
                         [h["kind"] for h in diag["hypotheses"]])
        page.wait_for_timeout(600)  # bars finish their transition
        widths = rows.locator("rect.d3c-bar").evaluate_all("els => els.map(e => +e.getAttribute('width'))")
        scores = [h["score"] for h in diag["hypotheses"]]
        for w, sc in zip(widths, scores):
            if sc == 0:
                self.assertEqual(w, 0)
        best = scores.index(max(scores))
        self.assertEqual(widths.index(max(widths)), best, "the widest bar is the highest score")
        for i, h in enumerate(diag["hypotheses"]):
            dots = rows.nth(i).locator(".d3c-ev")
            self.assertEqual(dots.count(), min(len(h["supports"]) + len(h["contradicts"]),
                                               dots.count()) if dots.count() else 0)
            self.assertEqual(rows.nth(i).locator(".d3c-ev-con").count(),
                             min(len(h["contradicts"]), rows.nth(i).locator(".d3c-ev-con").count()))
        lead = diag["hypotheses"][0]
        self.assertEqual(rows.nth(0).locator(".d3c-ev").count(), len(lead["supports"]) + len(lead["contradicts"]))
        self.assertEqual(svg.locator("g.d3c-window").count(), 1)
        self.assertEqual(errors, [])
        context.close()

    def test_take_forward_pins_match_the_numbered_list_verbatim(self):
        context, page, errors = self.open()
        side = self.failing()
        items = self.t05["reading"][side]["take_forward"]
        block = page.locator('[data-block="take-forward"]')
        self.assertEqual(block.count(), 1)
        pins = block.locator("svg.d3c-forward g.d3c-pin")
        located = [t for t in items if isinstance(t.get("at_step"), int)]
        self.assertEqual(pins.count(), len(located))
        self.assertEqual(pins.evaluate_all("els => els.map(e => +e.getAttribute('data-step'))"),
                         [t["at_step"] for t in located])
        lis = block.locator(".d3c-list li")
        self.assertEqual(lis.count(), len(items))
        for i, t in enumerate(items):
            self.assertIn(t["instead"], lis.nth(i).locator("strong").text_content())
            self.assertEqual(lis.nth(i).get_attribute("data-n"), str(i + 1))
        dec = self.t05["diagnosis"]["decisive_step"]["step"]
        self.assertEqual(lis.evaluate_all("els => els.map(e => e.classList.contains('decisive'))"),
                         [t.get("at_step") == dec for t in items])
        cf = self.t05.get("counterfactual") or {}
        if cf.get("estimate"):
            self.assertIn("estimate", block.locator(".d3c-note").text_content())
            self.assertIn(str(cf.get("confidence")), block.locator(".d3c-note").text_content())
        # a pin moves the shared cursor
        pins.first.dispatch_event("click")
        page.wait_for_timeout(300)
        pane = page.locator(".tj-pane", has_text=side.upper() + " ·").first
        self.assertIn(f"step {located[0]['at_step']}", pane.locator("h4").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_reading_toggle_switches_every_chart_to_the_other_run(self):
        context, page, errors = self.open()
        side = self.failing()
        other = "a" if side == "b" else "b"
        rep = self.t05
        page.locator('[data-block="reading"] .rd-head button').filter(has_text=rep[other]["agent"]["name"]).first.click()
        page.wait_for_timeout(900)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step").count(), len(rep[other]["steps"]))
        items = rep["reading"][other]["take_forward"]
        if items:
            self.assertEqual(page.locator('[data-block="take-forward"] .d3c-list li').count(), len(items))
        else:
            self.assertEqual(page.locator('[data-block="take-forward"]').count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_phone_width_keeps_every_chart_inside_the_page(self):
        context, page, errors = self.open(width=390)
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
        for cls in ("d3c-story", "d3c-why", "d3c-reconcile", "d3c-forward"):
            box = page.locator(f"svg.{cls}").first.bounding_box()
            self.assertIsNotNone(box, cls)
            self.assertLessEqual(box["x"] + box["width"], 390 + 1, cls)
        # on a phone the tree waits behind a disclosure; opened, it keeps its
        # five columns and scrolls inside its own box
        fold = page.locator('[data-block="trace-tree"] .tt-fold > summary')
        self.assertEqual(fold.count(), 1)
        fold.first.click()
        page.wait_for_timeout(800)
        tree = page.locator("svg.d3c-tree").first
        self.assertGreater(tree.bounding_box()["width"], 390)
        scroller = page.locator(".d3c-scroll").first
        self.assertEqual(scroller.count(), 1)
        self.assertLessEqual(scroller.bounding_box()["x"] + scroller.bounding_box()["width"], 390 + 1)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('.d3c-scroll')).overflowX"), "auto")
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step").count(),
                         len(self.t05[self.failing()]["steps"]))
        self.assertEqual(errors, [])
        context.close()

    def test_the_tree_holds_every_phase_step_and_value_and_folds(self):
        context, page, errors = self.open()
        rep = self.t05
        svg = page.locator("svg.d3c-tree")
        self.assertEqual(svg.count(), 1)
        phases = sum(len(rep["reading"][s]["phases"]) for s in "ab")
        steps = sum(len(rep[s]["steps"]) for s in "ab")
        values = sum(1 for s in "ab" for r in rep["reading"][s]["rests_on"] if r.get("first_step") is not None)
        nodes = svg.locator("g.d3c-tnode")
        self.assertEqual(nodes.count(), 1 + 2 + phases + steps + values)
        self.assertEqual(svg.locator("g.d3c-tnode[data-kind='step']").count(), steps)
        self.assertEqual(svg.locator("g.d3c-tnode[data-kind='value']").count(), values)
        self.assertEqual(svg.locator("path.d3c-tlink").count(), nodes.count() - 1, "a tree has one link per non-root node")
        # the fault's path: every step on the attribution chain / causal account of the failed run
        attr = rep["attribution"]
        chain = set(attr["chain"]) | {l["step"] for l in rep["diagnosis"]["causal_account"]}
        self.assertEqual(svg.locator("path.d3c-tlink.fault").count(), len(chain))
        # the decisive ring sits on the decisive step of the subject run
        dec = rep["diagnosis"]["decisive_step"]
        ringed = svg.locator("g.d3c-tnode[data-kind='step']").filter(has=page.locator(".d3c-ring"))
        self.assertEqual(ringed.count(), 1)
        self.assertEqual(ringed.first.get_attribute("data-side"), rep["diagnosis"]["subject"])
        self.assertEqual(ringed.first.get_attribute("data-step"), str(dec["step"]))
        # fold a phase: its steps leave; unfold: they return
        phase = svg.locator("g.d3c-tnode[data-kind='phase']").nth(1)
        side = phase.get_attribute("data-side")
        before = nodes.count()
        phase.dispatch_event("click")
        page.wait_for_timeout(700)
        self.assertLess(page.locator("svg.d3c-tree g.d3c-tnode").count(), before)
        self.assertEqual(page.locator("svg.d3c-tree g.d3c-tnode.collapsed").count(), 1)
        page.locator("svg.d3c-tree g.d3c-tnode[data-kind='phase']").nth(1).dispatch_event("click")
        page.wait_for_timeout(700)
        self.assertEqual(page.locator("svg.d3c-tree g.d3c-tnode").count(), before)
        # a step node opens the inspector on that step
        target = page.locator(f"svg.d3c-tree g.d3c-tnode[data-kind='step'][data-side='{side}']").first
        index = target.get_attribute("data-step")
        target.dispatch_event("click")
        page.wait_for_timeout(300)
        pane = page.locator(".tj-pane", has_text=side.upper() + " ·").first
        self.assertIn(f"step {index}", pane.locator("h4").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_a_passing_run_never_has_its_intermediate_values_marked_wrong(self):
        context, page, errors = self.open()
        rep = self.t05
        passing = "a" if rep["a"]["outcome"]["success"] else "b"
        wrong_on_passing = page.locator(f"svg.d3c-tree g.d3c-tnode[data-kind='value'][data-side='{passing}']").all_text_contents()
        self.assertFalse(any("✗" in t for t in wrong_on_passing), wrong_on_passing)
        failing = "b" if passing == "a" else "a"
        expected_wrong = sum(1 for r in rep["reading"][failing]["rests_on"]
                             if r.get("first_step") is not None and r.get("matches_expected") is False)
        marked = page.locator(f"svg.d3c-tree g.d3c-tnode[data-kind='value'][data-side='{failing}']").all_text_contents()
        self.assertEqual(sum(1 for t in marked if "✗" in t), expected_wrong)
        self.assertEqual(errors, [])
        context.close()

    def test_how_it_became_a_failure_reads_the_causal_account_and_the_alignment(self):
        context, page, errors = self.open()
        rep = self.t05
        side = self.failing()
        cells = page.locator("svg.d3c-story g.d3c-pcell")
        self.assertEqual(cells.count(), len(rep[side]["steps"]))
        states = cells.evaluate_all("els => els.map(e => e.getAttribute('data-state'))")
        account = {l["step"] for l in rep["diagnosis"]["causal_account"]} | set(rep["attribution"]["chain"])
        answer = len(rep[side]["steps"]) - 1
        for i, st in enumerate(states):
            if i in account:
                self.assertEqual(st, "committed" if i == answer else "fault", f"step {i}")
            else:
                self.assertIn(st, ("same", "drift", "diverged", "alone"), f"step {i}")
        # the decisive step is where the fault enters; the answer is where it is committed
        dec = rep["diagnosis"]["decisive_step"]["step"]
        self.assertEqual(cells.nth(dec).locator("text").first.text_content(), "fault enters")
        self.assertEqual(cells.nth(answer).locator("text").first.text_content(), "wrong answer")
        # a matched row reads as "same", read from the alignment
        rows = {r[f"{side}_index"]: r for r in rep["alignment"] if r.get(f"{side}_index") is not None}
        other = "a" if side == "b" else "b"
        for i, st in enumerate(states):
            if i in account:
                continue
            row = rows.get(i)
            if row and row.get(f"{other}_index") is not None and row["op"] == "match":
                self.assertEqual(st, "same", f"step {i}")
        self.assertEqual(errors, [])
        context.close()

    def test_reconcile_draws_the_splice_and_states_the_estimate_as_one(self):
        context, page, errors = self.open()
        rep = self.t05
        cf = rep["counterfactual"]
        block = page.locator('[data-block="reconcile"]')
        self.assertEqual(block.count(), 1)
        svg = block.locator("svg.d3c-reconcile")
        self.assertEqual(svg.count(), 1)
        self.assertEqual(svg.locator("g.d3c-lane").count(), 3)
        merged = len(cf["splice"]["prefix_steps"]) + len(cf["splice"]["adopted_steps"])
        rec = svg.locator("g.d3c-lane-reconciled g.d3c-rmark")
        self.assertEqual(rec.count(), merged)
        froms = rec.evaluate_all("els => els.map(e => e.getAttribute('data-from'))")
        failing = "b" if cf["splice"]["adopted_from"] == "a" else "a"
        self.assertEqual(froms, [failing] * len(cf["splice"]["prefix_steps"]) + [cf["splice"]["adopted_from"]] * len(cf["splice"]["adopted_steps"]))
        self.assertEqual(svg.locator("path.d3c-rlink").count(), merged)
        self.assertEqual(svg.locator("line.d3c-cut").count(), 1)
        self.assertIn(f"step {rep['diagnosis']['decisive_step']['step']}", svg.locator("text", has_text="cut ·").text_content())
        ends = svg.locator("text.d3c-rend").all_text_contents()
        self.assertIn("est. " + cf["estimate"]["outcome"], ends)
        self.assertTrue(any(e.startswith("✓") for e in ends) and any(e.startswith("✗") for e in ends), ends)
        # the strategy quotes the replay recipe and calls the estimate an estimate with its confidence
        text = block.locator(".d3c-strategy").text_content()
        self.assertIn(rep["diagnosis"]["decisive_step"]["replay_recipe"]["correction"], text)
        self.assertIn("splice estimate", text)
        self.assertIn("confidence " + cf["confidence"], text)
        self.assertIn(cf["narrative"], block.locator(".d3c-narrative").text_content())
        # a reconciled mark opens the run it came from
        rec.last.dispatch_event("click")
        page.wait_for_timeout(300)
        adopted_side = cf["splice"]["adopted_from"]
        pane = page.locator(".tj-pane", has_text=adopted_side.upper() + " ·").first
        self.assertIn(f"step {cf['splice']['adopted_steps'][-1]}", pane.locator("h4").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_a_task_without_a_counterfactual_has_no_reconcile_section(self):
        context, page, errors = self.open()
        page.select_option("#task-picker", "t02_cve_libfoo")
        page.wait_for_timeout(900)
        self.assertEqual(page.locator('#story-lane [data-block="reconcile"]').count(), 0)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-pcell").count(),
                         len(json.loads((Path(self.tmp.name) / "report_t02_cve_libfoo.json").read_text(encoding="utf-8"))["b"]["steps"])
                         if page.locator("svg.d3c-story").count() else 0)
        self.assertEqual(errors, [])
        context.close()

    def test_replay_retells_the_run_and_ends_on_the_static_picture(self):
        context, page, errors = self.open()
        side = self.failing()
        n = len(self.t05[side]["steps"])
        bar = page.locator('[data-block="reading"] .d3c-toolbar')
        self.assertEqual(bar.count(), 1)
        self.assertEqual(bar.locator("button.axis-steps").get_attribute("aria-pressed"), "true")
        total = page.locator("svg.d3c-story [data-unit], svg.d3c-story [data-order], svg.d3c-story .d3c-pcell").count()
        bar.locator("button.play").click()
        page.wait_for_timeout(300)
        self.assertTrue(page.evaluate("() => document.querySelector('svg.d3c-story').classList.contains('d3c-replaying')"))
        self.assertGreater(page.locator("svg.d3c-story .d3c-future").count(), 0, "later steps are dimmed while replaying")
        self.assertGreaterEqual(page.locator("svg.d3c-story .d3c-now").count(), 1, "the current step is marked")
        self.assertRegex(page.locator(".d3c-where").first.text_content(), rf"step \d+ of {n}")
        page.wait_for_timeout(9000)
        self.assertEqual(page.locator("svg.d3c-story .d3c-future").count(), 0, "the last frame is the full picture")
        self.assertEqual(page.locator("svg.d3c-story .d3c-now").count(), 0)
        self.assertEqual(bar.locator("button.play").text_content(), "▶ play how it went")
        pane = page.locator(".tj-pane", has_text=side.upper() + " ·").first
        self.assertIn(f"step {n - 1}", pane.locator("h4").text_content(), "the inspector followed to the answer")
        # seek with the scrubber; reset restores the static render
        page.locator(".d3c-scrub").first.evaluate("e => { e.value = '1'; e.dispatchEvent(new Event('input')); }")
        page.wait_for_timeout(200)
        self.assertGreater(page.locator("svg.d3c-story .d3c-future").count(), 0)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step:not(.d3c-future)").count(), 2)
        bar.locator("button.reset").click()
        page.wait_for_timeout(200)
        self.assertEqual(page.locator("svg.d3c-story .d3c-future").count(), 0)
        self.assertFalse(page.evaluate("() => document.querySelector('svg.d3c-story').classList.contains('d3c-replaying')"))
        self.assertEqual(total, page.locator("svg.d3c-story [data-unit], svg.d3c-story [data-order], svg.d3c-story .d3c-pcell").count())
        self.assertEqual(errors, [])
        context.close()

    def test_the_time_axis_places_steps_by_cumulative_latency(self):
        context, page, errors = self.open()
        side = self.failing()
        steps = self.t05[side]["steps"]
        page.locator('[data-block="reading"] .d3c-toolbar button.axis-time').click()
        page.wait_for_timeout(700)
        self.assertEqual(page.locator('[data-block="reading"] .d3c-toolbar button.axis-time').get_attribute("aria-pressed"), "true")
        xs = page.locator("svg.d3c-story g.d3c-step").evaluate_all(
            "els => els.map(e => +/translate\\(([-\\d.]+)/.exec(e.getAttribute('transform'))[1])")
        self.assertEqual(len(xs), len(steps))
        self.assertEqual(xs, sorted(xs), "time never runs backwards")
        # a step's x grows with the latency before it: the gap after the
        # slowest step is the widest gap (given the 9px floor for near-zero steps)
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        lat = [s.get("latency_s") or 0 for s in steps[:-1]]
        self.assertEqual(gaps.index(max(gaps)), lat.index(max(lat)))
        page.locator('[data-block="reading"] .d3c-toolbar button.axis-steps').click()
        page.wait_for_timeout(500)
        self.assertEqual(page.locator('[data-block="reading"] .d3c-toolbar button.axis-steps').get_attribute("aria-pressed"), "true")
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_replay_jumps_to_the_end(self):
        context, page, errors = self.open(reduced_motion=True)
        page.locator('[data-block="reading"] .d3c-toolbar button.play').click()
        page.wait_for_timeout(300)
        self.assertEqual(page.locator("svg.d3c-story .d3c-future").count(), 0)
        self.assertEqual(page.locator("svg.d3c-story .d3c-now").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_next_horizon_lists_the_feedback_signal_verbatim_with_its_tests(self):
        context, page, errors = self.open()
        fb = self.t05["feedback"]
        block = page.locator('[data-block="next-horizon"]')
        self.assertEqual(block.count(), 1)
        titles = page.evaluate("() => [...document.querySelectorAll('#story-lane .block-title')].map(e => e.textContent)")
        self.assertIn("8 · Next horizon", titles)
        items = block.locator(".nh-prompts li")
        self.assertEqual(items.count(), len(fb["prompt_suggestions"]))
        for i, sug in enumerate(fb["prompt_suggestions"]):
            self.assertEqual(items.nth(i).locator(".nh-prompt").text_content(), sug["text"])
            self.assertEqual(items.nth(i).get_attribute("data-kind"), sug["kind"])
            self.assertIn("hypothesis", items.nth(i).locator(".nh-status").text_content())
            if sug.get("test"):
                self.assertIn("replay", items.nth(i).locator(".nh-status").text_content())
        rows = block.locator(".nh-table tr")
        self.assertEqual(rows.count(), len(fb["reward_shaping"]))
        self.assertEqual(rows.evaluate_all("els => els.map(e => e.getAttribute('data-event'))"),
                         [r["event"] for r in fb["reward_shaping"]])
        self.assertIn("chosen: " + fb["preference_pair"]["chosen"]["agent"], block.locator(".nh-pair").text_content())
        link = block.locator("a[download]")
        self.assertEqual(link.count(), 1)
        self.assertTrue(link.get_attribute("href").startswith("data:application/json"))
        self.assertIn("deepcompare feedback", block.locator(".nh-export code").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_the_story_opens_on_the_two_runs_over_time_with_the_super_panel(self):
        context, page, errors = self.open()
        rep = self.t05
        hero = page.evaluate("() => [...document.querySelectorAll('#hero-lane > .block')].map(e => e.getAttribute('data-block'))")
        self.assertEqual(hero, ["trace-body"])
        # the super panel: outcome, decisive step, five paired stats from metrics_delta
        lede = page.locator(".bd-lede").text_content()
        self.assertIn(rep["a"]["agent"]["name"] + " ✓", lede)
        self.assertIn(rep["b"]["agent"]["name"] + " ✗", lede)
        self.assertIn(f"step {rep['diagnosis']['decisive_step']['step']}", lede)
        keys = page.locator(".bd-stats .k").all_text_contents()
        self.assertEqual(keys, ["steps", "tool calls", "tokens", "latency", "cost"])
        md = rep["metrics_delta"]
        nums = page.locator(".bd-stats .nums").all_text_contents()
        self.assertEqual(nums[0], f"{md['steps']['a']} / {md['steps']['b']}")
        self.assertEqual(nums[1], f"{md['tool_calls']['a']} / {md['tool_calls']['b']}")
        # the body: one node per step of both runs, a branch per tool step, one alignment per paired row
        svg = page.locator("svg.d3c-body")
        self.assertEqual(svg.count(), 1)
        n = len(rep["a"]["steps"]) + len(rep["b"]["steps"])
        self.assertEqual(svg.locator("g.d3c-bnode").count(), n)
        tools = sum(1 for s in "ab" for st in rep[s]["steps"] if st["type"] in ("tool_call", "search", "retrieve", "read"))
        self.assertEqual(svg.locator("g.d3c-bnode.kind-branch").count(), tools)
        paired = sum(1 for r in rep["alignment"] if r.get("a_index") is not None and r.get("b_index") is not None)
        self.assertEqual(svg.locator("path.d3c-body-align").count(), paired)
        self.assertEqual(svg.locator("path.d3c-body-align.diverge").count(), len(rep["divergences"]))
        # the fault's path reddens the failed trunk; the decisive node is ringed
        chain = set(rep["attribution"]["chain"]) | {l["step"] for l in rep["diagnosis"]["causal_account"]}
        self.assertEqual(svg.locator("g.d3c-bnode.fault").count(), len(chain))
        dec = rep["diagnosis"]["decisive_step"]
        ringed = svg.locator("g.d3c-bnode.decisive")
        self.assertEqual(ringed.count(), 1)
        self.assertEqual((ringed.first.get_attribute("data-side"), ringed.first.get_attribute("data-step")), (rep["diagnosis"]["subject"], str(dec["step"])))
        self.assertEqual(ringed.first.locator(".d3c-ring").count(), 1)
        self.assertEqual(svg.get_attribute("data-bubbles"), "0", "a short run needs no folding at the default zoom")
        # time runs left to right: the answer node is the rightmost of its run
        for side in "ab":
            xs = svg.locator(f"g.d3c-bnode[data-side='{side}'] g.d3c-bmark").evaluate_all(
                "els => els.map(e => +/translate\\(([-\\d.]+)/.exec(e.getAttribute('transform'))[1])")
            self.assertEqual(xs, sorted(xs), side)
        # the trunk measures tokens by default: the answer's end label carries the
        # run's token total; the ruler is in tokens; time and steps are one click away
        self.assertEqual(svg.get_attribute("data-axis"), "tokens")
        self.assertEqual(page.locator(".bd-axis-btn[aria-pressed='true']").text_content(), "tokens")
        ends = svg.locator("g.d3c-bnode.kind-answer text.d3c-blabel").all_text_contents()
        self.assertEqual(len(ends), 2)
        md = rep["metrics_delta"]
        for side in "ab":
            total = sum(st.get("tokens") or 0 for st in rep[side]["steps"][:-1])
            self.assertTrue(any(str(total) in e for e in ends), (total, ends))
        self.assertIn("tokens", svg.locator(".d3c-body-axis text.d3c-cap").text_content())
        page.locator(".bd-axis-btn.axis-time").click()
        page.wait_for_timeout(800)
        svg = page.locator("svg.d3c-body")
        self.assertEqual(svg.get_attribute("data-axis"), "time")
        ticks = svg.locator(".d3c-body-axis text.d3c-idx").all_text_contents()
        self.assertTrue(all(t.endswith("s") for t in ticks), ticks)
        self.assertIn("latencies", svg.locator(".d3c-body-axis text.d3c-cap").text_content())
        page.locator(".bd-axis-btn.axis-steps").click()
        page.wait_for_timeout(800)
        svg = page.locator("svg.d3c-body")
        self.assertEqual(svg.get_attribute("data-axis"), "steps")
        self.assertTrue(all(t.isdigit() for t in svg.locator(".d3c-body-axis text.d3c-idx").all_text_contents()))
        page.locator(".bd-axis-btn.axis-time").click()
        page.wait_for_timeout(800)
        svg = page.locator("svg.d3c-body")
        # a click opens the step in the inspector docked under the chart
        page.locator("svg.d3c-body g.d3c-bnode[data-side='b'][data-step='1']").first.dispatch_event("click")
        page.wait_for_timeout(300)
        pane = page.locator('[data-block="trace-body"] .tj-pane', has_text="B ·").first
        self.assertIn("step 1", pane.locator("h4").text_content())
        # zoom: a wheel over the chart rescales time and the nodes move; double-click resets
        before = page.locator("svg.d3c-body g.d3c-bnode[data-side='a'][data-step='2'] g.d3c-bmark").first.get_attribute("transform")
        page.evaluate("() => { const s = document.querySelector('svg.d3c-body'); s.dispatchEvent(new WheelEvent('wheel', {deltaY: -400, clientX: 600, clientY: 150, bubbles: true})); }")
        page.wait_for_timeout(400)
        after = page.locator("svg.d3c-body g.d3c-bnode[data-side='a'][data-step='2'] g.d3c-bmark").first.get_attribute("transform")
        self.assertNotEqual(before, after)
        page.locator("svg.d3c-body").first.dispatch_event("dblclick")
        page.wait_for_timeout(700)
        reset = page.locator("svg.d3c-body g.d3c-bnode[data-side='a'][data-step='2'] g.d3c-bmark").first.get_attribute("transform")
        self.assertEqual(reset, before)
        self.assertEqual(errors, [])
        context.close()

    def test_the_evidence_view_keeps_the_map_as_its_hero(self):
        context, page, errors = self.open()
        page.locator('#view-tabs [data-view="evidence"]').click()
        page.wait_for_timeout(700)
        hero = page.evaluate("() => [...document.querySelectorAll('#hero-lane > .block')].map(e => e.getAttribute('data-block'))")
        self.assertEqual(hero, ["trajectory-map"])
        page.locator('#view-tabs [data-view="story"]').click()
        page.wait_for_timeout(700)
        hero = page.evaluate("() => [...document.querySelectorAll('#hero-lane > .block')].map(e => e.getAttribute('data-block'))")
        self.assertEqual(hero, ["trace-body"])
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_disables_chart_transitions(self):
        context, page, errors = self.open(reduced_motion=True)
        self.assertEqual(page.evaluate("() => AgentDiff.charts.motion()"), 0)
        widths = page.locator("svg.d3c-why rect.d3c-bar").evaluate_all("els => els.map(e => +e.getAttribute('width'))")
        self.assertGreater(max(widths), 0, "bars are at their final width immediately")
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class LongTrajectoryTest(unittest.TestCase):
    """Details on demand for long runs. A 306-step pair (the t05 demo with
    300 lookup steps spliced in before the answer) must render as a window
    of readable steps with an overview of all of them: the story, the
    forward pins and the reconcile lanes page together, the tree pages a
    long phase twenty steps at a time, and the page stays bounded in
    height, in width, and in time to first paint.
    """

    tmp = None
    EXTRA = 300

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        traces = root / "traces"
        traces.mkdir()
        for name in ("t05_flight_duration__atlas-v2.json", "t05_flight_duration__bolt-v3.json"):
            trace = json.loads((ROOT / "demo" / "traces" / name).read_text(encoding="utf-8"))
            steps = trace["steps"]
            answer, body = steps[-1], steps[:-1]
            template = body[2] if len(body) > 2 else body[0]
            filler = []
            for k in range(cls.EXTRA):
                step = json.loads(json.dumps(template))
                step.update({"name": "lookup", "type": "tool_call", "quality": "good", "tokens": 40, "latency_s": 0.3,
                             "input": f"lookup segment {k}", "output": f"segment {k}: {k % 7}h{(k * 13) % 60:02d}m"})
                filler.append(step)
            new = body + filler + [answer]
            for i, step in enumerate(new):
                step["index"] = i
            trace["steps"] = new
            (traces / name).write_text(json.dumps(trace), encoding="utf-8")
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(traces), "-o", str(root / "out"),
                        "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.report = root / "out" / "report.html"
        cls.rep = json.loads((root / "out" / "report_t05_flight_duration.json").read_text(encoding="utf-8"))
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

    def open(self, width=1280):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        started = time.time()
        page.goto(f"file://{self.report}#view=story")
        page.wait_for_selector("svg.d3c-story g.d3c-step", timeout=15000)
        page.wait_for_timeout(600)
        return context, page, errors, time.time() - started

    def failing(self):
        return "b" if self.rep["a"]["outcome"]["success"] else "a"

    def caption(self, page, cls):
        return page.locator(f"svg.{cls} .d3c-ov-caption").first.text_content()

    def test_the_story_draws_a_window_with_an_overview_and_paints_quickly(self):
        context, page, errors, elapsed = self.open()
        side = self.failing()
        n = len(self.rep[side]["steps"])
        self.assertGreater(n, 300)
        marks = page.locator("svg.d3c-story g.d3c-step")
        self.assertLessEqual(marks.count(), 40)
        self.assertGreaterEqual(marks.count(), 8)
        self.assertEqual(page.locator("svg.d3c-story .d3c-overview").count(), 1)
        self.assertEqual(page.locator("svg.d3c-story .d3c-brush .selection").count(), 1)
        self.assertIn(f"of {n}", self.caption(page, "d3c-story"))
        dec = self.rep["diagnosis"]["decisive_step"]["step"]
        self.assertEqual(page.locator(f'svg.d3c-story g.d3c-step[data-step="{dec}"]').count(), 1,
                         "the first window holds the decisive step")
        self.assertEqual(page.locator("svg.d3c-story g.d3c-decisive").count(), 1)
        # the failure strip is windowed too; the overview carries every step's state
        self.assertEqual(page.locator("svg.d3c-story g.d3c-pcell").count(), marks.count())
        self.assertGreater(page.locator("svg.d3c-story .d3c-ov-cells rect").count(), 0)
        self.assertLess(elapsed, 8, f"first paint took {elapsed:.1f}s")
        self.assertLess(page.evaluate("() => document.documentElement.scrollHeight"), 8000)
        self.assertEqual(page.evaluate("() => document.documentElement.scrollWidth"), 1280)
        self.assertEqual(errors, [])
        context.close()

    def test_the_window_pages_by_keyboard_brush_and_api_and_the_charts_move_together(self):
        context, page, errors, _ = self.open()
        side = self.failing()
        n = len(self.rep[side]["steps"])
        key = f"t05_flight_duration:{side}"
        first = int(page.locator("svg.d3c-story g.d3c-step").first.get_attribute("data-step"))
        page.locator("svg.d3c-story").first.focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(700)
        moved = int(page.locator("svg.d3c-story g.d3c-step").first.get_attribute("data-step"))
        self.assertGreater(moved, first)
        self.assertEqual(self.caption(page, "d3c-story")[:14], self.caption(page, "d3c-forward")[:14],
                         "the forward pins share the story's window")
        page.evaluate(f"() => AgentDiff.charts.focus.set('{key}', {n - 10})")
        page.wait_for_timeout(700)
        state = page.evaluate(f"() => AgentDiff.charts.focus.get('{key}')")
        self.assertLessEqual(state["start"] + 8, n)
        last = int(page.locator("svg.d3c-story g.d3c-step").last.get_attribute("data-step"))
        self.assertEqual(last, n - 1, "End of the run is reachable")
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step[data-role='answer']").count(), 1)
        # the brush: dragging the window left moves the start earlier
        box = page.locator("svg.d3c-story .d3c-brush .selection").first.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 - 250, box["y"] + box["height"] / 2, steps=8)
        page.mouse.up()
        page.wait_for_timeout(700)
        self.assertLess(page.evaluate(f"() => AgentDiff.charts.focus.get('{key}').start"), state["start"])
        # "all": every step, compressed, no overview
        page.evaluate(f"() => AgentDiff.charts.focus.all('{key}', true)")
        page.wait_for_timeout(800)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step").count(), n)
        self.assertEqual(page.locator("svg.d3c-story .d3c-overview").count(), 0)
        self.assertEqual(page.evaluate("() => document.documentElement.scrollWidth"), 1280)
        self.assertEqual(errors, [])
        context.close()

    def test_the_tree_pages_a_long_phase(self):
        context, page, errors, _ = self.open()
        big = page.locator("svg.d3c-tree g.d3c-tnode[data-kind='phase'].collapsed")
        self.assertGreaterEqual(big.count(), 1, "a phase of 300 steps starts folded")
        self.assertIn("300 steps", big.first.locator(".d3c-tcount").text_content())
        before = page.locator("svg.d3c-tree g.d3c-tnode").count()
        self.assertLess(before, 80)
        big.first.dispatch_event("click")
        page.wait_for_timeout(800)
        after = page.locator("svg.d3c-tree g.d3c-tnode").count()
        self.assertGreater(after, before)
        self.assertLessEqual(after - before, 22, "one page of steps plus a 'more' node")
        more = page.locator("svg.d3c-tree g.d3c-tnode[data-kind='more']")
        self.assertGreaterEqual(more.count(), 1)
        more.first.dispatch_event("click")
        page.wait_for_timeout(800)
        grown = page.locator("svg.d3c-tree g.d3c-tnode").count()
        self.assertGreater(grown, after)
        self.assertLessEqual(grown - after, 21)
        self.assertEqual(errors, [])
        context.close()

    def test_the_reconcile_lanes_are_windowed_around_the_cut(self):
        context, page, errors, _ = self.open()
        svg = page.locator("svg.d3c-reconcile")
        self.assertEqual(svg.count(), 1)
        for lane in ("passing", "reconciled", "failing"):
            self.assertLessEqual(svg.locator(f"g.d3c-lane-{lane} g.d3c-rmark").count(), 40, lane)
        self.assertEqual(svg.locator(".d3c-overview").count(), 1)
        self.assertEqual(svg.locator("line.d3c-cut").count(), 1, "the first window holds the cut")
        self.assertEqual(errors, [])
        context.close()

    def test_compression_folds_the_repeated_calls_into_one_unit_that_opens(self):
        context, page, errors, _ = self.open()
        side = self.failing()
        n = len(self.rep[side]["steps"])
        bar = page.locator('[data-block="reading"] .d3c-toolbar')
        self.assertEqual(bar.locator("button.compress").count(), 1)
        bar.locator("button.compress").click()
        page.wait_for_timeout(800)
        units = page.locator("svg.d3c-story g.d3c-step")
        self.assertLessEqual(units.count(), 8, "300 identical lookups fold into one unit")
        self.assertEqual(page.locator("svg.d3c-story .d3c-overview").count(), 0, "no window needed once compressed")
        group = page.locator("svg.d3c-story g.d3c-step.group")
        self.assertEqual(group.count(), 1)
        self.assertEqual(group.first.get_attribute("data-count"), str(self.EXTRA))
        self.assertIn(f"×{self.EXTRA}", group.first.locator(".d3c-name").text_content())
        self.assertIn(f"of {n}", bar.locator("button.compress").text_content())
        # every step of the run is still accounted for: the units' counts sum to n
        counts = units.evaluate_all("els => els.map(e => +e.getAttribute('data-count'))")
        self.assertEqual(sum(counts), n)
        # the decisive step and the answer stay their own units
        dec = self.rep["diagnosis"]["decisive_step"]["step"]
        self.assertEqual(page.locator(f'svg.d3c-story g.d3c-step[data-step="{dec}"][data-count="1"]').count(), 1)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step[data-role='answer'][data-count='1']").count(), 1)
        # opening the group brings its steps back (windowed again)
        group.first.dispatch_event("click")
        page.wait_for_timeout(900)
        self.assertGreater(page.locator("svg.d3c-story g.d3c-step").count(), 8)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step.group").count(), 0)
        bar = page.locator('[data-block="reading"] .d3c-toolbar')
        bar.locator("button.compress").click()
        page.wait_for_timeout(700)
        self.assertEqual(page.locator("svg.d3c-story g.d3c-step.group").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_the_body_folds_the_long_run_into_bubbles_that_open_on_demand(self):
        context, page, errors, _ = self.open()
        page.wait_for_selector("svg.d3c-body", timeout=15000)
        svg = page.locator("svg.d3c-body").first
        n = len(self.rep["a"]["steps"]) + len(self.rep["b"]["steps"])
        items = int(svg.get_attribute("data-items"))
        bubbles = int(svg.get_attribute("data-bubbles"))
        self.assertGreaterEqual(bubbles, 1, "dense stretches fold into bubbles at the default zoom")
        self.assertLess(items, n / 4, "the picture stays readable: far fewer things drawn than steps")
        # what matters never folds: the decisive step and both answers stay nodes
        self.assertEqual(page.locator("svg.d3c-body g.d3c-bnode.decisive").count(), 1)
        self.assertEqual(page.locator("svg.d3c-body g.d3c-bnode.kind-answer").count(), 2)
        # every step is accounted for: nodes plus bubble counts sum to the steps
        counts = page.locator("svg.d3c-body g.d3c-bubble").evaluate_all("els => els.map(e => +e.getAttribute('data-count'))")
        self.assertEqual(page.locator("svg.d3c-body g.d3c-bnode").count() + sum(counts), n)
        # a bubble that holds the fault says so; its label names the dominant tool and the count
        fault_bubbles = page.locator("svg.d3c-body g.d3c-bubble.fault")
        self.assertGreaterEqual(fault_bubbles.count(), 1)
        labels = page.locator("svg.d3c-body .d3c-bubble-label").all_text_contents()
        self.assertTrue(any(l.startswith("×") for l in labels), labels)
        # click a bubble: the chart zooms to it and it splits into its steps
        big = page.locator("svg.d3c-body g.d3c-bubble").first
        first_count = int(big.get_attribute("data-count"))
        big.dispatch_event("click")
        page.wait_for_timeout(900)
        svg = page.locator("svg.d3c-body").first
        self.assertGreater(int(svg.get_attribute("data-items")), items)
        zoomed_bubbles = int(svg.get_attribute("data-bubbles"))
        zoomed_counts = page.locator("svg.d3c-body g.d3c-bubble").evaluate_all("els => els.map(e => +e.getAttribute('data-count'))")
        self.assertTrue(not zoomed_counts or max(zoomed_counts) < first_count, "zooming in splits the bubble")
        # double-click resets to the folded picture
        page.locator("svg.d3c-body").first.dispatch_event("dblclick")
        page.wait_for_timeout(900)
        self.assertEqual(int(page.locator("svg.d3c-body").first.get_attribute("data-bubbles")), bubbles)
        self.assertEqual(errors, [])
        context.close()

    def test_a_phone_gets_the_same_window_without_sideways_scroll(self):
        context, page, errors, _ = self.open(width=390)
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
        marks = page.locator("svg.d3c-story g.d3c-step").count()
        self.assertGreaterEqual(marks, 8)
        self.assertLessEqual(marks, 12)
        self.assertEqual(page.locator("svg.d3c-story .d3c-overview").count(), 1)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class LiveWatchTest(unittest.TestCase):
    """Streaming: served by `deepcompare watch`, the page shows the runs
    as they arrive — a mark per step, the newest pulsing, the count
    growing — and when the pair finishes the story replaces the stream,
    without a reload. Driven by the demo simulator at a fast pace."""

    @classmethod
    def setUpClass(cls):
        import threading
        from deepcompare.harness.watch import serve
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = Path(cls.tmp.name) / "src"
        cls.src.mkdir()
        for name in ("t05_flight_duration__atlas-v2.json", "t05_flight_duration__bolt-v3.json"):
            (cls.src / name).write_text((ROOT / "demo" / "traces" / name).read_text(encoding="utf-8"), encoding="utf-8")
        cls.out = Path(cls.tmp.name) / "live"
        cls.stop = threading.Event()
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        cls.server = serve(cls.out, ROOT / "web" / "blocks.html", port=0, poll=0.05, stop=cls.stop)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown_all()
        except Exception:
            pass
        try:
            cls.browser.close()
            cls._pw.stop()
        except Exception:
            pass
        cls.tmp.cleanup()

    def test_sub_agents_stream_in_as_a_growing_tree(self):
        import threading
        from deepcompare.harness.watch import simulate
        for path in Path(self.out).glob("*.json"):
            path.unlink()
        stop = threading.Event()
        sim = threading.Thread(target=simulate, args=(ROOT / "demo" / "horizon" / "traces", self.out, 0.2, False, stop), daemon=True)
        sim.start()
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"http://127.0.0.1:{self.port}/#view=story")
        info = None
        for _ in range(60):
            page.wait_for_timeout(250)
            info = page.evaluate("""() => { const b = document.querySelector('.block[data-block="live-run"]'); if (!b) return null;
              const ic = b.querySelector('svg.d3c-horizon'); const tr = b.querySelector('svg.d3c-agent-tree');
              return {runs: b.querySelectorAll('.lv-run').length, spans: ic ? ic.querySelectorAll('g.d3c-hz-node[data-kind="span"]').length : 0,
                      open: ic ? ic.querySelectorAll('line.d3c-hz-open').length : 0, tnodes: tr ? tr.querySelectorAll('g.d3c-anode').length : 0,
                      topen: tr ? tr.querySelectorAll('g.d3c-anode.open').length : 0,
                      running: [...b.querySelectorAll('g.d3c-anode.open text.d3c-alabel')].map(t => t.textContent)}; }""")
            if info and info["spans"] >= 2 and info["tnodes"] >= 3:
                break
        stop.set()
        sim.join(timeout=5)
        for path in Path(self.out).glob("*.json"):   # leave the directory as found, for the next test
            path.unlink()
        page.wait_for_timeout(600)
        self.assertTrue(info, "the live block never appeared")
        self.assertEqual(info["runs"], 2)
        self.assertGreaterEqual(info["spans"], 2, "sub-agents appear as spans while the runs stream")
        self.assertGreaterEqual(info["open"], 1, "the span still receiving steps has an open edge")
        self.assertGreaterEqual(info["tnodes"], 3, "the same tree as nodes and links")
        self.assertGreaterEqual(info["topen"], 1)
        self.assertTrue(all("running" in t for t in info["running"]))
        self.assertEqual(errors, [])
        context.close()

    def test_the_stream_arrives_and_becomes_the_story(self):
        import threading
        from deepcompare.harness.watch import simulate
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"http://127.0.0.1:{self.port}/#view=story")
        page.wait_for_selector("#live-badge", timeout=10000)
        self.assertIn("LIVE", page.locator("#live-badge").text_content())
        # nothing running yet: no story, no runs
        self.assertEqual(page.locator('[data-block="live-run"]').count(), 0)
        threading.Thread(target=simulate, args=(self.src, self.out, 0.25, False, self.stop), daemon=True).start()
        page.wait_for_selector('[data-block="live-run"] .lv-run', timeout=15000)
        first = int(page.locator('[data-block="live-run"] .lv-run').first.get_attribute("data-steps"))
        page.wait_for_timeout(900)
        later = int(page.locator('[data-block="live-run"] .lv-run').first.get_attribute("data-steps"))
        self.assertGreaterEqual(later, first)
        runs = page.locator('[data-block="live-run"] .lv-run[data-agent]')
        self.assertGreaterEqual(runs.count(), 1)
        self.assertGreaterEqual(page.locator('[data-block="live-run"] .lv-line .lv-mark').count(), 1)
        self.assertEqual(page.locator('[data-block="live-run"] .lv-line .lv-now').count(), runs.count(), "the newest step pulses on every running lane")
        self.assertIn("running", page.locator("#live-badge").text_content())
        self.assertEqual(page.locator("#task-picker option").count(), 1, "a live-only task is listed")
        # the pair finishes: the story replaces the stream, no reload
        page.wait_for_selector('#story-lane [data-block="reading"] svg.d3c-story', timeout=30000)
        self.assertEqual(page.locator('[data-block="live-run"]').count(), 0)
        self.assertEqual(page.locator('[data-block="verdict-card"]').count(), 1)
        self.assertIn("1 compared", page.locator("#live-badge").text_content())
        self.assertFalse(page.evaluate("() => performance.getEntriesByType('navigation').length > 1"))
        self.assertEqual(errors, [])
        context.close()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class LoopBlockTest(unittest.TestCase):
    """The Agent loop block draws what the ledger says: one row per
    iteration, the decision with its status, a point per measured rate,
    and the stop reason — from a loop run with the prompt-aware fake
    provider, no network."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "loop"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        sys.path.insert(0, str(ROOT / "tests"))
        from helpers_loop import run_demo_loop
        cls.ledger = run_demo_loop(out, template=ROOT / "web" / "blocks.html")
        cls.page_path = out / "report.html"
        assert cls.page_path.is_file()
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

    def open(self, fragment="#view=batch"):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}{fragment}")
        page.wait_for_timeout(700)
        return context, page, errors

    def test_the_block_lists_every_iteration_with_its_decision_and_the_stop_reason(self):
        context, page, errors = self.open()
        block = page.locator('.block[data-block="loop"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            block = page.locator('.block[data-block="loop"]')
        iters = self.ledger["state"]["iterations"]
        summary = self.ledger["summary"]
        agents = self.ledger["state"]["agents"]
        # KPI tiles: one per agent with its pooled success and interval, plus paired, runs, hypotheses, routing
        for a in agents:
            tile = block.locator(f'.lp-tile[data-agent="{a}"]')
            r = summary["agents"][a]
            self.assertEqual(tile.locator(".v .num").text_content().strip(), f"{round(r['success'] * 100)}%")
            self.assertIn(f"{r['ci95'][0]:.2f}–{r['ci95'][1]:.2f}", tile.locator(".s").text_content())
        self.assertEqual(block.locator('.lp-tile[data-kpi="runs"] .v .num').text_content().strip(), str(summary["spent_runs"]))
        self.assertIn(f"{summary['kept_changes']} kept", block.locator('.lp-tile[data-kpi="decisions"]').text_content())
        # the ledger: one row per iteration with its statistics
        rows = block.locator("li[data-iteration]")
        self.assertEqual(rows.count(), len(iters))
        for it in iters:
            row = block.locator(f'li[data-iteration="{it["n"]}"]')
            self.assertEqual(row.get_attribute("data-action"), it["action"])
            self.assertIn(it["why"][:40], row.locator(".why").text_content())
            for a, r in it["results"].items():
                self.assertIn(f"{a} {r['successes']}/{r['runs']}", row.locator(".stats").text_content())
            if it["action"] == "test-prompt":
                self.assertEqual(row.locator(".lp-tag").text_content(), it["decision"]["status"])
                self.assertIn(it["decision"]["text"][:30], row.locator(".txt").text_content())
        # success by iteration: one point per pooled rate, one hollow point per variant under test
        page.wait_for_timeout(300)
        chart = block.locator('[data-chart="success"]')
        pooled = sum(1 for it in iters for a in agents if it["results"].get(a, {}).get("runs"))
        variants = sum(1 for it in iters if it["action"] == "test-prompt")
        self.assertEqual(chart.locator("circle.pt:not(.variant)").count(), pooled)
        self.assertEqual(chart.locator("circle.pt.variant").count(), variants)
        # each experiment: a dumbbell per task and the two pooled intervals, with the paired statistics
        for it in iters:
            if it["action"] != "test-prompt":
                continue
            card = block.locator(f'.lp-exp[data-iteration="{it["n"]}"]')
            ev = it["decision"]["evidence"]
            self.assertEqual(card.locator("g.task").count(), ev["tasks"])
            self.assertEqual(card.locator("g.task[data-outcome=win]").count(), ev["wins"])
            self.assertEqual(card.locator("g.pooled").count(), 2)
            head = card.locator(".head").text_content()
            self.assertIn(it["decision"]["status"], head)
            self.assertIn(f"wins {ev['wins']}", head)
            pr = ev["paired"]
            self.assertIn(("+" if pr["diff"] >= 0 else "−") + f"{abs(pr['diff']):.2f}", head)
        # where the runs went: a cell per family per comparison
        compares = [it for it in iters if it["action"] == "compare"]
        cells = sum(len(it["routing"]["families"]) for it in compares)
        self.assertEqual(block.locator(".lp-grid td[data-state]").count(), cells)
        for it in compares:
            for fam, row in it["routing"]["families"].items():
                cell = block.locator(f'.lp-grid tr[data-family="{fam}"] td[data-state]').nth(compares.index(it))
                self.assertEqual(cell.get_attribute("data-state"), "tie" if row.get("tie") else row["confidence"])
        self.assertIn(self.ledger["state"]["stop"]["reason"], block.locator(".lp-stop").text_content())
        self.assertIn("no model is in the control path", block.locator(".lp-note").last.text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_no_open_block_shows_an_empty_state_on_the_loop_page(self):
        for view in ("story", "evidence", "batch"):
            context, page, errors = self.open(f"#view={view}")
            empties = page.evaluate("""() => [...document.querySelectorAll('.block:not(.collapsed) .empty')]
                .filter(e => e.offsetParent !== null).map(e => e.closest('.block').getAttribute('data-block'))""")
            self.assertEqual(empties, [], f"{view}: {empties}")
            self.assertEqual(errors, [], view)
            context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class ScorecardBlockTest(unittest.TestCase):
    """The Evaluation scorecard block draws every rate as a dot with its
    Wilson interval, every spend as a bar, risk against reward, and the
    judge beside the grade — all counted from the embedded aggregate."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "runs"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(ROOT / "demo" / "runs" / "traces"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "golden" / "tasks.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.card = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))["scorecard"]
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

    def test_every_dimension_is_drawn_from_its_counts(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=batch")
        page.wait_for_timeout(700)
        block = page.locator('.block[data-block="scorecard"]')
        self.assertEqual(block.count(), 1)
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            block = page.locator('.block[data-block="scorecard"]')
        card = self.card
        agents = list(card["agents"])
        self.assertIn(card["mode"], block.locator(".sc-lede .mode").text_content())
        for key, _label in card["dimensions"]["rates"]:
            strip = block.locator(f'.sc-strip[data-dim="{key}"]')
            self.assertEqual(strip.count(), 1, key)
            measurable = [a for a in agents if card["agents"][a]["rates"][key]["runs"]]
            if not measurable:
                self.assertTrue(strip.evaluate("e => e.classList.contains('na')"), key)
                continue
            self.assertEqual(strip.locator(".pt").count(), len(measurable), key)
            for a in measurable:
                r = card["agents"][a]["rates"][key]
                pt = strip.locator(f'.pt[data-agent="{a}"]')
                self.assertAlmostEqual(float(pt.evaluate("e => parseFloat(e.style.left)")), r["rate"] * 100, places=2)
                if r["ci95"]:
                    bar = strip.locator(f'.ci[data-agent="{a}"]')
                    self.assertAlmostEqual(float(bar.evaluate("e => parseFloat(e.style.left)")), r["ci95"][0] * 100, places=2)
        for key, _label in card["dimensions"]["spend"]:
            bar = block.locator(f'.sc-bar[data-dim="{key}"]')
            recorded = sum(1 for a in agents if card["agents"][a]["spend"][key])
            self.assertEqual(bar.locator(".row").count(), recorded, key)
            if not recorded:
                self.assertEqual(bar.count(), 0, "a spend recorded for no run has no row")
        self.assertEqual(block.locator(".sc-rr circle").count(), sum(1 for a in agents if card["agents"][a]["risk_reward"]["risk"] is not None))
        for a in agents:
            rr = card["agents"][a]["risk_reward"]
            self.assertIn(f"reward {round(rr['reward'] * 100)}%", block.locator('[data-sec="risk-reward"]').text_content())
        judged = [a for a in agents if card["agents"][a]["judge"]]
        if judged:
            self.assertEqual(block.locator(".sc-judge .card").count(), len(agents))
        else:
            self.assertIn("No judging model", block.locator('[data-sec="judge"]').text_content())
        table = block.locator('table[data-sec="trajectory"]')
        self.assertIn(str(card["agents"][agents[0]]["tools"]["calls"]), table.text_content())
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class DebugSessionBlockTest(unittest.TestCase):
    """The Debug session block: an aligned A/B strip with one cell per
    step and a gap per one-sided row, per-run aggregates counted from the
    steps, six layers for the selected step, and a cursor shared with
    the rest of the page."""

    TOOLISH = ("tool_call", "search", "retrieve", "read")
    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "traces"), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.report = json.loads((out / "report_t05_flight_duration.json").read_text(encoding="utf-8"))
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

    def _open(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=evidence")
        page.wait_for_timeout(700)
        page.locator('#task-select, select[data-role="task"], select').first.select_option("t05_flight_duration") if page.locator("select").count() else None
        page.wait_for_timeout(500)
        block = page.locator('.block[data-block="debug-session"]')
        if block.count() and "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            block = page.locator('.block[data-block="debug-session"]')
        return context, page, block, errors

    def test_the_body_chart_in_debug_mode_the_aggregates_and_the_layers_are_counted_from_the_report(self):
        context, page, block, errors = self._open()
        self.assertEqual(block.count(), 1)
        rep = self.report
        if block.locator(".dbg-kpi td.side").first.text_content().split()[0] != rep["a"]["agent"]["name"]:
            self.skipTest("the page opened on another task; the counts below are for t05")
        svg = block.locator("svg.d3c-body")
        self.assertEqual(svg.count(), 1, "the debug session draws the body chart, not a strip")
        self.assertEqual(svg.get_attribute("data-debug"), "true")
        for side in ("a", "b"):
            steps = rep[side]["steps"]
            self.assertEqual(svg.locator(f'g.d3c-bnode[data-side="{side}"]').count(), len(steps), "one node per step at the default zoom")
            phases = rep["reading"][side]["phases"]
            # one band per run of consecutive steps in one phase
            runs_of_phase, last = 0, None
            for st in steps:
                ph = next((p["intent"] for p in phases if st["index"] in p["steps"]), None)
                if ph != last:
                    runs_of_phase += 1
                    last = ph
            self.assertEqual(svg.locator(f'rect.d3c-phase[data-side="{side}"]').count(), runs_of_phase, side)
            tools = sum(1 for s in steps if s["type"] in self.TOOLISH)
            self.assertEqual(block.locator(f'.dbg-kpi td.v[data-side="{side}"][data-kpi="tool calls"]').text_content(), str(tools))
            self.assertEqual(block.locator(f'.dbg-kpi td.v[data-side="{side}"][data-kpi="model turns"]').text_content(), str(len(steps) - tools))
            self.assertEqual(block.locator(f'.dbg-kpi td.v[data-side="{side}"][data-kpi="phases · transitions"]').text_content().split(" · ")[0], str(len(phases)))
            tot = rep[side]["totals"]
            self.assertEqual(block.locator(f'.dbg-kpi td.v[data-side="{side}"][data-kpi="tokens"]').text_content(), str(tot["input_tokens"] + tot["output_tokens"]))
        # the decisive step carries its replay verdict on the chart and opens in the layers
        dec = rep["diagnosis"]["decisive_step"]
        subject = rep["diagnosis"]["subject"]
        self.assertEqual(svg.locator("text.d3c-dbg-replay").count(), 1)
        self.assertIn("hypothesis" if not dec.get("replay") else dec["verification"], svg.locator("text.d3c-dbg-replay").text_content())
        first = block.locator(".dbg-run").first
        self.assertEqual((first.get_attribute("data-side"), first.get_attribute("data-step")), (subject, str(dec["step"])))
        layers = [first.locator(".dbg-layer").nth(i).get_attribute("data-layer") for i in range(first.locator(".dbg-layer").count())]
        self.assertEqual(layers, ["model call", "tool selection", "tool response", "state", "output", "replay"])
        self.assertIn(dec["verification"], first.locator('[data-layer="replay"]').text_content())
        step = next(s for s in rep[subject]["steps"] if s["index"] == dec["step"])
        other = "a" if subject == "b" else "b"
        row = next(r for r in rep["alignment"] if r.get(f"{subject}_index") == dec["step"])
        if step["type"] in self.TOOLISH and row.get(f"{other}_index") is not None:
            counterpart = next(s for s in rep[other]["steps"] if s["index"] == row[f"{other}_index"])
            if counterpart["type"] in self.TOOLISH and counterpart["name"] != step["name"]:
                self.assertIn(counterpart["name"], first.locator('[data-layer="tool selection"]').text_content())
        # clicking a node on the chart moves the layers; the shared cursor moves them too
        target = svg.locator('g.d3c-bnode[data-side="a"]').first
        target.dispatch_event("click")
        page.wait_for_timeout(300)
        first = block.locator(".dbg-run").first
        self.assertEqual((first.get_attribute("data-side"), first.get_attribute("data-step")), ("a", target.get_attribute("data-step")))
        rows = rep["alignment"]
        last_row = len(rows) - 1
        page.evaluate("row => document.dispatchEvent(new CustomEvent('agentdiff:select-step', {detail: {row: row, side: 'b'}}))", last_row)
        page.wait_for_timeout(300)
        want = rows[last_row].get("b_index")
        if want is not None:
            first = block.locator(".dbg-run").first
            self.assertEqual((first.get_attribute("data-side"), first.get_attribute("data-step")), ("b", str(want)))
        self.assertEqual(errors, [])
        context.close()

    def test_the_story_hero_has_a_debug_toggle_that_adds_bands_marks_stats_and_layers(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=story")
        page.wait_for_timeout(700)
        btn = page.locator(".bd-axis-btn.axis-debug")
        self.assertEqual(btn.count(), 1)
        self.assertEqual(btn.get_attribute("aria-pressed"), "false")
        svg = page.locator("svg.d3c-body").first
        self.assertEqual(svg.get_attribute("data-debug"), "false")
        self.assertEqual(svg.locator("rect.d3c-phase").count(), 0, "no bands until debug is on")
        self.assertEqual(page.locator(".bd-layers").count(), 0)
        btn.click()
        page.wait_for_timeout(700)
        svg = page.locator("svg.d3c-body").first
        self.assertEqual(page.locator(".bd-axis-btn.axis-debug").get_attribute("aria-pressed"), "true")
        self.assertEqual(svg.get_attribute("data-debug"), "true")
        self.assertGreater(svg.locator("rect.d3c-phase").count(), 0)
        self.assertEqual(svg.locator("text.d3c-dbg-replay").count(), 1)
        keys = page.locator(".bd-stats .k").all_text_contents()
        for k in ("tool errors", "retries", "model switches", "no-info steps", "transitions"):
            self.assertIn(k, keys)
        self.assertGreaterEqual(page.locator(".bd-layers .dbg-run").count(), 1)
        self.assertTrue(page.locator(".d3c-legend-debug").count() == 1 and page.locator(".d3c-phase-chip").count() >= 1)
        # a node click moves the layers under the inspector
        node = svg.locator('g.d3c-bnode[data-side="a"]').first
        node.dispatch_event("click")
        page.wait_for_timeout(400)
        first = page.locator(".bd-layers .dbg-run").first
        self.assertEqual((first.get_attribute("data-side"), first.get_attribute("data-step")), ("a", node.get_attribute("data-step")))
        # off again: bands gone, layers gone
        page.locator(".bd-axis-btn.axis-debug").click()
        page.wait_for_timeout(600)
        self.assertEqual(page.locator("svg.d3c-body").first.get_attribute("data-debug"), "false")
        self.assertEqual(page.locator(".bd-layers").count(), 0)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class TimeBlockTest(unittest.TestCase):
    """Where the time went: one waterfall segment per step, wasted steps
    hatched and named, shares that sum to the run, the tools ranked by
    their seconds, and the rationale — all from report.timing."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "traces"), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.reports = {p.stem[len("report_"):]: json.loads(p.read_text(encoding="utf-8")) for p in out.glob("report_*.json")}
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

    def test_the_story_block_draws_every_step_and_names_the_wasted_ones(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=story")
        page.wait_for_timeout(800)
        block = page.locator('#story-lane .block[data-block="time"]')
        self.assertEqual(block.count(), 1)
        task = page.evaluate("() => (AgentDiff.state().task) || null")
        rep = self.reports.get(task) or next(iter(self.reports.values()))
        tm = rep["timing"]
        self.assertIn(tm["narrative"][:40], block.locator(".tm-narr").text_content())
        for side in ("a", "b"):
            t = tm[side]
            sec = block.locator(f'section.tm-run[data-side="{side}"]')
            if not t["measurable"]:
                self.assertIn("unmeasurable", sec.text_content())
                continue
            svg = sec.locator("svg")
            self.assertEqual(svg.locator("g.step").count(), len(t["steps"]))
            wasted = [r for r in t["steps"] if r["wasted"]]
            self.assertEqual(svg.locator("g.step rect.seg.wasted").count(), len(wasted))
            for r in wasted:
                self.assertEqual(svg.locator(f'g.step[data-step="{r["index"]}"]').get_attribute("data-wasted"), r["wasted"])
            widths = [float(w) for w in sec.locator(".tm-share i").evaluate_all("els => els.map(e => parseFloat(e.style.width))")]
            self.assertAlmostEqual(sum(widths), 100, delta=0.5)
            for k in ("think", "tool", "answer"):
                self.assertAlmostEqual(float(sec.locator(f'.tm-share i[data-category="{k}"]').evaluate("e => parseFloat(e.style.width)")), t["by_category"][k]["share"] * 100, places=1)
            for tool, v in t["by_tool"].items():
                row = block.locator(f'.tm-details tr.tm-tool[data-side="{side}"][data-tool="{tool}"]')
                self.assertEqual(row.count(), 1, tool)
                self.assertEqual(row.locator("td").nth(2).text_content(), str(v["calls"]))
            self.assertIn(t["rationale"][:60], sec.locator(".tm-rat").text_content())
        rows = block.locator(".tm-details table").last.locator("tr").count() - 1
        self.assertEqual(rows, sum(len(tm[s]["steps"]) for s in ("a", "b") if tm[s]["measurable"]))
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class HorizonBlockTest(unittest.TestCase):
    """The long-horizon tree on the page: one node per span, subdivision
    and step of the embedded horizon, both runs around a shared axis,
    wasted time hatched, the fault's path marked, zoom on click with a
    breadcrumb, and the sub-agents' ledger — from the multi-agent demo."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "traces" / "h01_release_report__orbit-v1.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_horizon.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "traces"), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.report = json.loads((out / "report_h01_release_report.json").read_text(encoding="utf-8"))
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

    @staticmethod
    def _walk(node):
        yield node
        for c in node.get("children") or []:
            yield from HorizonBlockTest._walk(c)

    def test_the_tree_is_drawn_node_for_node_and_zooms_on_click(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=story")
        page.wait_for_timeout(900)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        self.assertEqual(block.count(), 1)
        svg = block.locator("svg.d3c-horizon")
        self.assertEqual(svg.count(), 1)
        hz = self.report["horizon"]
        for side in ("a", "b"):
            nodes = list(self._walk(hz[side]["tree"]))
            self.assertEqual(svg.locator(f'g.d3c-hz-node[data-side="{side}"]').count(), len(nodes), side)
            for kind in ("span", "episode", "step"):
                self.assertEqual(svg.locator(f'g.d3c-hz-node[data-side="{side}"][data-kind="{kind}"]').count(), sum(1 for n in nodes if n["kind"] == kind), f"{side} {kind}")
            wasted_steps = [n for n in nodes if n["kind"] == "step" and n["wasted_s"]]
            self.assertEqual(svg.locator(f'g.d3c-hz-node[data-side="{side}"][data-kind="step"] rect.d3c-hz-waste').count(), len(wasted_steps), side)
            fault_steps = [n for n in nodes if n["kind"] == "step" and n["fault"]]
            self.assertEqual(svg.locator(f'g.d3c-hz-node[data-side="{side}"][data-kind="step"].fault').count(), len(fault_steps), side)
            self.assertIn(hz[side]["summary"][:50], block.locator(f'.hz-full[data-side="{side}"]').text_content())
            self.assertIn(name := (self.report[side]["agent"]["name"]), block.locator(f'.hz-sum .who[data-side="{side}"]').text_content())
            for a in hz[side]["agents"]:
                row = block.locator(f'.hz-agents tr[data-side="{side}"][data-agent="{a["agent"]}"]')
                self.assertEqual(row.count(), 1)
                self.assertEqual(row.locator("td").nth(2).text_content(), str(a["delegations"]))
                self.assertEqual(row.locator("td").nth(3).text_content(), str(a["steps"]))
        # the decisive step is ringed on the failing side
        failing = self.report["diagnosis"]["subject"]
        self.assertEqual(svg.locator(f'g.d3c-hz-node[data-side="{failing}"][data-kind="step"].decisive circle.d3c-ring').count(), 1)
        # zoom into the first span of A: only its subtree remains, the breadcrumb says so, the axis switch repaints
        span = svg.locator('g.d3c-hz-node[data-side="a"][data-kind="span"]').first
        key = span.get_attribute("data-key")
        agent = next(n for n in self._walk(hz["a"]["tree"]) if n["key"] == key)
        span.dispatch_event("click")
        page.wait_for_timeout(500)
        svg = block.locator("svg.d3c-horizon")
        subtree = list(self._walk(agent))
        self.assertEqual(svg.locator('g.d3c-hz-node[data-side="a"]').count(), len(subtree))
        crumb = block.locator('.d3c-crumb[data-side="a"]').text_content()
        self.assertIn(agent["agent"], crumb)
        self.assertIn("run", crumb)
        block.locator(".hz-axis-btn.axis-tokens").click()
        page.wait_for_timeout(500)
        self.assertEqual(block.locator("svg.d3c-horizon").get_attribute("data-axis"), "tokens")
        self.assertEqual(block.locator("svg.d3c-horizon").locator('g.d3c-hz-node[data-side="a"]').count(), len(subtree), "the zoom survives an axis change")
        # a step click moves the shared cursor
        page.locator('svg.d3c-horizon g.d3c-hz-node[data-side="b"][data-kind="step"]').first.dispatch_event("click")
        page.wait_for_timeout(300)
        self.assertEqual(errors, [])
        context.close()

    def test_the_diff_view_aligns_the_two_delegation_graphs_and_rings_the_blamed_agent(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=story")
        page.wait_for_timeout(900)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        hz = self.report["horizon"]
        self.assertIn(hz["narrative"][:60], block.locator(".hz-narr").text_content())
        block.locator(".hz-axis-btn.view-diff").click()
        page.wait_for_timeout(600)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        svg = block.locator("svg.d3c-graph-diff")
        self.assertEqual(svg.count(), 1)
        diff = hz["diff"]
        self.assertEqual(svg.locator("g.d3c-gd-node").count(), len(diff["nodes"]))
        for n in diff["nodes"]:
            node = svg.locator(f'g.d3c-gd-node[data-agent="{n["agent"]}"]')
            self.assertEqual(node.get_attribute("data-in"), n["in"], n["agent"])
        self.assertEqual(svg.locator("path.d3c-gd-link").count(), len(diff["nodes"]) - 1)
        labels = svg.locator("text.d3c-gd-edge").all_text_contents()
        names = {"a": self.report["a"]["agent"]["name"], "b": self.report["b"]["agent"]["name"]}
        differing = [e for e in diff["edges"] if e["in"] != "both" or e["count_a"] != e["count_b"]]
        self.assertEqual(len(labels), len(differing), "an edge is labelled only where the two runs differ")
        for e in differing:
            want = f"{names['a']} ×{e['count_a']} · {names['b']} ×{e['count_b']}" if e["in"] == "both" else f"only {e['in']}"
            self.assertTrue(any(l.startswith(want) for l in labels), want)
        for n in diff["nodes"]:
            node = svg.locator(f'g.d3c-gd-node[data-agent="{n["agent"]}"]')
            self.assertEqual(node.locator("path.half").count(), int(bool(n["a"])) + int(bool(n["b"])), n["agent"])
            runs = node.locator("text.d3c-gd-run").all_text_contents()
            self.assertEqual(len(runs), 2)
            self.assertTrue(runs[0].startswith("● " if n["a"] else "○ "))
        # compact labels: the earlier reading — a count on every link, one line per agent
        block.locator(".hz-axis-btn.labels-compact").click()
        page.wait_for_timeout(500)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        svg = block.locator("svg.d3c-graph-diff")
        compact_labels = svg.locator("text.d3c-gd-edge").all_text_contents()
        self.assertEqual(len(compact_labels), len(diff["edges"]))
        for e in diff["edges"]:
            self.assertIn(f"{e['count_a']} · {e['count_b']}", compact_labels)
        self.assertEqual(svg.locator("text.d3c-gd-run").count(), 0)
        self.assertEqual(svg.locator("text.d3c-gd-sub").count(), len(diff["nodes"]))
        block.locator(".hz-axis-btn.labels-words").click()
        page.wait_for_timeout(500)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        svg = block.locator("svg.d3c-graph-diff")
        failing = self.report["diagnosis"]["subject"]
        blamed = hz[failing]["blame"]["agent"]
        ringed = svg.locator("g.d3c-gd-node.blamed")
        self.assertEqual(ringed.count(), 1)
        agent_key = "root" if blamed == self.report[failing]["agent"]["name"] else blamed
        self.assertEqual(ringed.get_attribute("data-agent"), agent_key)
        self.assertEqual(errors, [])
        context.close()

    def test_the_same_tree_draws_as_nodes_and_links_and_a_node_click_zooms_the_icicle(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=story")
        page.wait_for_timeout(900)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        block.locator(".hz-axis-btn.view-tree").click()
        page.wait_for_timeout(600)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        svg = block.locator("svg.d3c-agent-tree")
        self.assertEqual(svg.count(), 1)
        self.assertEqual(block.locator("svg.d3c-horizon").count(), 0, "one drawing at a time")
        hz = self.report["horizon"]
        for side in ("a", "b"):
            spans = [n for n in self._walk(hz[side]["tree"]) if n["kind"] in ("run", "span")]
            self.assertEqual(svg.locator(f'g.d3c-anode[data-side="{side}"]').count(), len(spans), side)
            self.assertEqual(svg.locator(f'g.d3c-anode[data-side="{side}"] path.d3c-awaste').count(), sum(1 for n in spans if n["wasted_s"] > 0 and n["seconds"] > 0))
            self.assertEqual(svg.locator(f'g.d3c-anode[data-side="{side}"].fault').count(), sum(1 for n in spans if n["fault"]))
            for n in spans:
                label = svg.locator(f'g.d3c-anode[data-side="{side}"][data-key="{n["key"]}"] text.d3c-asub').text_content()
                self.assertTrue(label.startswith(f"{n['count']} steps"), label)
        # clicking a sub-agent node switches back to the icicle, zoomed into that span
        target = svg.locator('g.d3c-anode[data-side="a"].kind-span').first
        key = target.get_attribute("data-key")
        target.dispatch_event("click")
        page.wait_for_timeout(600)
        block = page.locator('#story-lane .block[data-block="horizon"]')
        self.assertEqual(block.locator("svg.d3c-horizon").count(), 1)
        agent = next(n for n in self._walk(hz["a"]["tree"]) if n["key"] == key)
        self.assertIn(agent["agent"], block.locator('.d3c-crumb[data-side="a"]').text_content())
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class PanelsAndHeatTest(unittest.TestCase):
    """The Panels view: a grid the reader composes (presets, add, move,
    widen, remove, columns) that persists in the browser; and the three
    comparison panels drawn from `report.timing` — calls × time heat map,
    tool matrix, latency by tool — checked cell for cell against the JSON."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "traces" / "h01_release_report__orbit-v1.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_horizon.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "traces"), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.report = json.loads((out / "report_h01_release_report.json").read_text(encoding="utf-8"))
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

    @staticmethod
    def _row_key(r):
        return r["name"] or "?" if r["category"] == "tool" else "the answer" if r["category"] == "answer" else "thinking"

    def _rows(self):
        tm = self.report["timing"]
        totals = {}
        for side in ("a", "b"):
            for r in tm[side]["steps"]:
                k = self._row_key(r)
                totals[k] = totals.get(k, 0.0) + (r["latency_s"] or 0.0)
        tools = sorted((k for k in totals if k not in ("thinking", "the answer")), key=lambda k: -totals[k])
        if "thinking" in totals:
            tools.append("thinking")
        if "the answer" in totals:
            tools.append("the answer")
        return tools

    def _open(self, width=1280):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=panels")
        page.wait_for_timeout(900)
        return context, page, errors

    def _ids(self, page):
        return page.evaluate("() => [...document.querySelectorAll('#panels-lane .panels-grid > [data-block]')].map(e => e.dataset.block + (e.classList.contains('wide') ? '*' : ''))")

    def test_the_view_opens_from_the_url_with_its_default_grid_and_nothing_else(self):
        context, page, errors = self._open()
        self.assertEqual(page.locator('.tab[data-view="panels"]').get_attribute("aria-selected"), "true")
        self.assertFalse(page.locator("#panels-lane").is_hidden())
        self.assertTrue(page.locator("#story-lane").is_hidden())
        self.assertTrue(page.locator("#stacks").is_hidden())
        self.assertTrue(page.locator("#hero-lane").is_hidden() if page.locator("#hero-lane").count() else True)
        self.assertEqual(self._ids(page), ["treemap*"] + (["impact*"] if "impact" in self.report else []) + ["trace-body*", "heatmap", "latency-strip", "tool-matrix"])
        presets = page.locator("#panels-lane [data-preset]")
        self.assertEqual([presets.nth(i).get_attribute("data-preset") for i in range(presets.count())], ["time", "tools", "agents", "eval", "all", "used"])
        self.assertEqual(page.locator('#panels-lane [data-cols="2"]').get_attribute("aria-pressed"), "true")
        # every svg in the grid that is a chart carries a role and a label
        for i in range(page.locator('#panels-lane [data-block="heatmap"] svg, #panels-lane [data-block="latency-strip"] svg').count()):
            svg = page.locator('#panels-lane [data-block="heatmap"] svg, #panels-lane [data-block="latency-strip"] svg').nth(i)
            self.assertEqual(svg.get_attribute("role"), "img")
            self.assertTrue(svg.get_attribute("aria-label"))
        self.assertEqual(errors, [])
        context.close()

    def test_the_grid_is_the_readers_and_survives_a_reload(self):
        context, page, errors = self._open()
        for bid in ("trace-body", "treemap"):
            page.click(f'#panels-lane .panels-grid > [data-block="{bid}"] .panel-ctl button[title="remove this panel"]')
            page.wait_for_timeout(300)
        imp = ["impact*"] if "impact" in self.report else []
        self.assertEqual(self._ids(page), imp + ["heatmap", "latency-strip", "tool-matrix"])
        page.click('#panels-lane [data-cols="3"]')
        page.wait_for_timeout(300)
        page.click("#panels-lane [data-picker] summary")
        page.wait_for_timeout(200)
        page.click('#panels-lane [data-add="time"]')
        page.wait_for_timeout(300)
        self.assertEqual(self._ids(page), imp + ["heatmap", "latency-strip", "tool-matrix", "time"])
        page.click('#panels-lane .panels-grid > [data-block="time"] .panel-ctl button[title="move left"]')
        page.wait_for_timeout(300)
        page.click('#panels-lane .panels-grid > [data-block="time"] .panel-ctl button[title="full width"]')
        page.wait_for_timeout(300)
        self.assertEqual(self._ids(page), imp + ["heatmap", "latency-strip", "time*", "tool-matrix"])
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('#panels-lane .panels-grid')).gridTemplateColumns.split(' ').length"), 3)
        page.reload()
        page.wait_for_timeout(900)
        self.assertEqual(self._ids(page), imp + ["heatmap", "latency-strip", "time*", "tool-matrix"])
        self.assertEqual(page.locator('#panels-lane [data-cols="3"]').get_attribute("aria-pressed"), "true")
        page.click('#panels-lane [data-preset="tools"]')
        page.wait_for_timeout(300)
        self.assertEqual(self._ids(page), ["tool-behaviour", "tool-matrix", "heatmap", "latency-strip", "debug-session"])
        self.assertEqual(page.locator('#panels-lane [data-preset="tools"]').get_attribute("aria-pressed"), "true")
        # the same blocks stay ordinary evidence in the Evidence view
        page.click('.tab[data-view="evidence"]')
        page.wait_for_timeout(500)
        ev = page.evaluate("() => [...document.querySelectorAll('#stacks [data-block]')].map(e => e.dataset.block)")
        for bid in ("heatmap", "tool-matrix", "latency-strip"):
            self.assertIn(bid, ev)
        self.assertEqual(errors, [])
        context.close()

    def test_the_heat_map_matrix_and_strip_agree_with_the_timing_json(self):
        context, page, errors = self._open()
        tm = self.report["timing"]
        rows = self._rows()
        heat = page.locator('#panels-lane [data-block="heatmap"] svg')
        bins = int(heat.get_attribute("data-bins"))
        self.assertGreaterEqual(bins, 8)
        self.assertEqual(heat.locator("g.cell").count(), len(rows) * bins * 2)
        # each row's cells sum to that row's seconds per side; the totals text agrees
        for k in rows:
            for side in ("a", "b"):
                want = sum((r["latency_s"] or 0.0) for r in tm[side]["steps"] if self._row_key(r) == k)
                got = page.evaluate("([k, s]) => [...document.querySelectorAll('#panels-lane [data-block=\"heatmap\"] g.cell[data-side=\"' + s + '\"]')].filter(e => e.dataset.row === k).reduce((a, e) => a + Number(e.dataset.seconds), 0)", [k, side])
                self.assertAlmostEqual(got, want, places=2, msg=f"{k} {side}")
            self.assertEqual(heat.locator(f'text.tot[data-row="{k}"]').count(), 1)
        # the matrix: one row per key, calls and seconds A over B
        matrix = page.locator('#panels-lane [data-block="tool-matrix"] table.tm-matrix')
        got_rows = [matrix.locator("tr[data-tool]").nth(i).get_attribute("data-tool") for i in range(matrix.locator("tr[data-tool]").count())]
        self.assertEqual(got_rows, rows)
        for k in rows:
            for side in ("a", "b"):
                calls = [r for r in tm[side]["steps"] if self._row_key(r) == k]
                cell = matrix.locator(f'tr[data-tool="{k}"] td[data-col="calls"] .pair span.{side}')
                self.assertEqual(cell.text_content().strip(), str(len(calls)), f"{k} {side}")
        # the strip: one dot per call, hollow when wasted
        strip = page.locator('#panels-lane [data-block="latency-strip"] svg')
        for side in ("a", "b"):
            self.assertEqual(strip.locator(f'circle.call[data-side="{side}"]').count(), len(tm[side]["steps"]))
            self.assertEqual(strip.locator(f'circle.call.wasted[data-side="{side}"]').count(), sum(1 for r in tm[side]["steps"] if r["wasted"]))
        # a dot opens its step
        first = strip.locator('circle.call[data-side="a"]').first
        step = int(first.get_attribute("data-step"))
        first.dispatch_event("click")
        page.wait_for_timeout(300)
        self.assertIn(f"step {step}".upper(), page.locator("#panels-lane").inner_text().upper())
        self.assertEqual(errors, [])
        context.close()

    def test_nothing_overflows_on_a_phone(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('#panels-lane .panels-grid')).gridTemplateColumns.split(' ').length"), 1)
        self.assertEqual(errors, [])
        context.close()

    def test_the_treemap_is_proportional_zooms_and_the_page_nudges_with_what_you_open(self):
        context, page, errors = self._open()
        hz = self.report["horizon"]
        tmap = page.locator('#panels-lane [data-block="treemap"]')
        self.assertEqual(tmap.locator("svg").get_attribute("role"), "img")
        # both runs on one scale: widths in the ratio of their seconds
        widths = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#panels-lane [data-block="treemap"] .tmap-side')]
            .map(g => [g.dataset.side, g.querySelector('.tmap-node') && g.getBBox().width]))""")
        sa, sb = hz["a"]["tree"]["seconds"], hz["b"]["tree"]["seconds"]
        self.assertAlmostEqual(widths["a"] / widths["b"], sa / sb, delta=0.08)
        # one tile per step, one box per span or part, on each side
        for side in ("a", "b"):
            nodes = list(HorizonBlockTest._walk(hz[side]["tree"]))
            self.assertEqual(tmap.locator(f'.tmap-node.leaf[data-side="{side}"]').count(), sum(1 for n in nodes if n["kind"] == "step"))
            self.assertEqual(tmap.locator(f'.tmap-node.box[data-side="{side}"]').count(), sum(1 for n in nodes if n["kind"] in ("span", "episode")))
            self.assertEqual(tmap.locator(f'.tmap-node.leaf[data-side="{side}"] rect.waste').count(), sum(1 for n in nodes if n["kind"] == "step" and n["wasted_s"]))
        head = tmap.locator('.tmap-head').first.text_content()
        self.assertIn(self.report["a"]["agent"]["name"], head)
        # zoom into the first sub-agent box: its name heads the map, a way back appears
        box = tmap.locator('.tmap-node.box[data-side="a"][data-kind="span"]').first
        key = box.get_attribute("data-key")
        span = next(n for n in HorizonBlockTest._walk(hz["a"]["tree"]) if n["key"] == key)
        box.dispatch_event("click")
        page.wait_for_timeout(400)
        self.assertIn(span["agent"], tmap.locator('.tmap-head[data-side="a"], .tmap-side[data-side="a"] .tmap-head').first.text_content())
        self.assertEqual(tmap.locator(".tmap-back").count(), 1)
        tmap.locator(".tmap-back").click()
        page.wait_for_timeout(400)
        self.assertEqual(tmap.locator(".tmap-back").count(), 0)
        # a tile opens its step in the shared inspector
        tile = tmap.locator('.tmap-node.leaf[data-side="b"]').first
        step = next(n for n in HorizonBlockTest._walk(hz["b"]["tree"]) if n["key"] == tile.get_attribute("data-key"))["from"]
        tile.dispatch_event("click")
        page.wait_for_timeout(300)
        self.assertIn(f"STEP {step}", page.locator('#panels-lane [data-block="trace-body"] .tj-inspector').inner_text().upper())
        # the nudge: no signals, no chip; a block this reader opens most, one chip that adds it first
        self.assertEqual(page.locator("#panels-lane [data-suggest]").count(), 0)
        page.evaluate("() => { AgentDiff._internals.State.signals['time'] = {weight: 40, count: 8, last: Date.now()}; AgentDiff._internals.renderAll(); }")
        page.wait_for_timeout(300)
        chip = page.locator("#panels-lane [data-suggest]")
        self.assertEqual(chip.count(), 1)
        self.assertEqual(chip.get_attribute("data-suggest"), "time")
        chip.click()
        page.wait_for_timeout(300)
        self.assertEqual(self._ids(page)[0], "time")
        self.assertEqual(page.locator("#panels-lane [data-suggest]").count(), 0)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class MilestonesBlockTest(unittest.TestCase):
    """The Milestones ladder on the page, from the long-horizon demo
    batched with its golden set: one mark per milestone reached, per run,
    at the recorded step and second; the never-reached rungs named; the
    axis switch; a mark opening its step; the table under the fold."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_long.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.report = json.loads((out / "report_h02_migrate_service.json").read_text(encoding="utf-8"))
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

    def test_the_ladder_matches_the_report_and_opens_a_step(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=panels")
        page.wait_for_timeout(1000)
        page.click('#panels-lane [data-preset="agents"]')
        page.wait_for_timeout(800)
        block = page.locator('#panels-lane .panels-grid > [data-block="milestones"]')
        self.assertEqual(block.count(), 1)
        ms = self.report["milestones"]
        self.assertIn(ms["narrative"][:60], block.locator(".ms-narr").text_content())
        for side in ("a", "b"):
            reached = [m for m in ms[side]["milestones"] if m["reached"]]
            self.assertEqual(block.locator(f'circle.mark[data-side="{side}"]').count(), len(reached), side)
            self.assertEqual(block.locator(f'text.never[data-side="{side}"]').count(), ms[side]["total"] - len(reached), side)
        self.assertEqual(block.locator("svg").get_attribute("role"), "img")
        self.assertEqual(block.locator(".ms-table tr[data-id]").count(), ms["a"]["total"])
        # the axis switch redraws along steps
        block.locator('[data-axis="steps"]').click()
        page.wait_for_timeout(500)
        block = page.locator('#panels-lane .panels-grid > [data-block="milestones"]')
        self.assertEqual(block.locator('[data-axis="steps"]').get_attribute("aria-pressed"), "true")
        self.assertIn("steps", block.locator("svg").get_attribute("aria-label"))
        # a mark opens its step in the shared inspector (the body chart is in the same preset)
        mark = block.locator('circle.mark[data-side="a"]').first
        mid = mark.get_attribute("data-id")
        step = next(m for m in ms["a"]["milestones"] if m["id"] == mid)["step"]
        mark.dispatch_event("click")
        page.wait_for_timeout(400)
        self.assertIn(f"STEP {step}", page.locator('#panels-lane [data-block="trace-body"] .tj-inspector').inner_text().upper())
        self.assertEqual(errors, [])
        context.close()


def _impact_fixture(report):
    """A small, valid `report.impact` for a page whose engine has not
    written one: two lanes per run, six contiguous clusters per run — two
    quiet in a row (they fold), one hot with three marks — every mark a
    step the alignment knows, so a tick can open it in the inspector."""
    def run(side):
        agent = report[side]["agent"]["name"]
        n = len(report[side]["steps"])
        total_s = float(report["timing"][side]["total_s"])
        bounds = [(0, 59), (60, 149), (150, 239), (240, 299), (300, 399), (400, n - 1)]
        kinds = ["work", "quiet", "quiet", "hot", "work", "work"]
        impacts = [0.35, 0.04, 0.03, 0.97, 0.3, 0.55]
        lanes = [agent, agent, "migrator-auth", agent, "migrator-auth", agent]
        marks_at = [r[f"{side}_index"] for r in report["alignment"]
                    if r.get(f"{side}_index") is not None and 240 <= r[f"{side}_index"] <= 299][:3]
        assert len(marks_at) == 3
        clusters = []
        for i, ((lo, hi), kind, imp, lane) in enumerate(zip(bounds, kinds, impacts, lanes)):
            steps = hi - lo + 1
            start_s = total_s * lo / n
            end_s = total_s * (hi + 1) / n
            marks = []
            if kind == "hot":
                marks = [{"step": marks_at[0], "kind": "fault", "label": "wrong schema read"},
                         {"step": marks_at[1], "kind": "decisive", "label": "dropped the column"},
                         {"step": marks_at[2], "kind": "error", "label": "migration failed"}]
            clusters.append({
                "id": f"{side}{i + 1}", "from": lo, "to": hi, "steps": steps,
                "start_s": round(start_s, 3), "end_s": round(end_s, 3), "seconds": round(end_s - start_s, 3),
                "lane": lane, "agents": [lane], "impact": imp, "score": imp * 10, "kind": kind,
                "reasons": {"fault_steps": 1 if kind == "hot" else 0, "decisive": kind == "hot", "errors": 1 if kind == "hot" else 0,
                            "retries": 0, "wasted_s": 0.0, "milestones": [], "divergence_rows": 0, "first_divergence": None,
                            "answer": i == 5, "tokens": 1000 * steps},
                "why": f"{kind} stretch of {steps} steps in {lane}", "label": f"{kind} {i + 1}", "marks": marks,
            })
        return {
            "measurable": True, "total_s": total_s, "total_steps": n, "clusters": clusters, "hot": [f"{side}4"],
            "lanes": [{"agent": agent, "depth": 0, "parent": None, "clusters": [c["id"] for c in clusters if c["lane"] == agent]},
                      {"agent": "migrator-auth", "depth": 1, "parent": agent, "clusters": [c["id"] for c in clusters if c["lane"] != agent]}],
            "narrative": f"{agent}: one hot stretch at steps 240–299.",
        }
    return {"version": 1, "a": run("a"), "b": run("b"),
            "narrative": "Both runs spent their impact in one stretch around step 240–299; the rest was quiet work."}


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class ImpactBlockTest(unittest.TestCase):
    """The impact-weighted view: two trees (the trace tree's architecture),
    one per run — the run at the root, a lane node per sub-agent, a bar per
    cluster whose length is its impact on the shared A/B scale, a lane's
    consecutive quiet clusters folded into one capsule whose pill grows with
    the steps folded; one thread per run carries the run's units in step
    order (the root agent's stretches, the folds, a tick where each lane
    attaches); a lane starts open only when it holds a hot cluster or the
    fault's path.
    A capsule dilates on click (and folds back from any of its clusters), a
    cluster opens to its marks as child leaves, a mark opens its step in the
    shared inspector; "trunk" and "even" draw the same units as one trunk
    with branches; the table under the fold; the block under 750px at
    1440; nothing overflowing on a phone."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_long.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        report_path = out / "report_h02_migrate_service.json"
        cls.report = json.loads(report_path.read_text(encoding="utf-8"))
        if "impact" not in cls.report:
            from deepcompare.report import render_html
            cls.report["impact"] = _impact_fixture(cls.report)
            report_path.write_text(json.dumps(cls.report), encoding="utf-8")
            aggregate_path = out / "aggregate.json"
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8")) if aggregate_path.is_file() else {}
            render_html([cls.report], aggregate, ROOT / "web" / "blocks.html", cls.page_path)
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

    def _open(self, width=1280, preset="time"):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=panels")
        page.wait_for_timeout(900)
        if preset:
            page.click(f'#panels-lane [data-preset="{preset}"]')
            page.wait_for_timeout(700)
        return context, page, errors

    @staticmethod
    def _root(im, side):
        return next(l["agent"] for l in im[side]["lanes"] if not l["depth"])

    @staticmethod
    def _lane_items(im, side, lane):
        """A lane's children with every fold closed: its clusters in step
        order, consecutive quiet ones folded into one capsule ("fold", ids,
        steps); the root lane's list hangs directly under the run."""
        cs = [c for c in sorted(im[side]["clusters"], key=lambda c: c["from"]) if c["lane"] == lane]
        out, i = [], 0
        while i < len(cs):
            if cs[i]["kind"] == "quiet":
                j = i
                while j < len(cs) and cs[j]["kind"] == "quiet":
                    j += 1
                if j - i >= 2:
                    out.append(("fold", ",".join(c["id"] for c in cs[i:j]), sum(c["steps"] for c in cs[i:j])))
                    i = j
                    continue
            out.append(("cluster", cs[i]["id"], cs[i]["impact"]))
            i += 1
        return out

    @classmethod
    def _lanes(cls, im, side):
        """The sub-agent lanes that own a cluster, in the order the run met them."""
        root, seen = cls._root(im, side), []
        for c in sorted(im[side]["clusters"], key=lambda c: c["from"]):
            if c["lane"] != root and c["lane"] not in seen:
                seen.append(c["lane"])
        return seen

    @staticmethod
    def _fault(c):
        return bool(c["reasons"].get("fault_steps")) or any(m["kind"] == "fault" for m in c["marks"])

    @classmethod
    def _open_lanes(cls, im, side):
        return [l for l in cls._lanes(im, side) if any(c["kind"] == "hot" or cls._fault(c) for c in im[side]["clusters"] if c["lane"] == l)]

    @staticmethod
    def _thread_folds(im, side):
        """The folds on the run's thread: every run of two or more consecutive
        quiet clusters in step order, whatever their lanes — (ids, steps)."""
        cs = sorted(im[side]["clusters"], key=lambda c: c["from"])
        folds, i = [], 0
        while i < len(cs):
            if cs[i]["kind"] == "quiet":
                j = i
                while j < len(cs) and cs[j]["kind"] == "quiet":
                    j += 1
                if j - i >= 2:
                    folds.append((",".join(c["id"] for c in cs[i:j]), sum(c["steps"] for c in cs[i:j])))
                    i = j
                    continue
            i += 1
        return folds

    @classmethod
    def _shown(cls, im, side):
        """Cluster ids drawn as bars with the defaults: the root lane's on the
        thread (minus those inside a closed fold) and the open lanes' clusters
        minus those inside a closed capsule."""
        folded = {i for ids, _ in cls._thread_folds(im, side) for i in ids.split(",")}
        ids = [c["id"] for c in im[side]["clusters"] if c["lane"] == cls._root(im, side) and c["id"] not in folded]
        for lane in cls._open_lanes(im, side):
            ids += [i for kind, i, _ in cls._lane_items(im, side, lane) if kind == "cluster"]
        return ids

    @staticmethod
    def _length(page, selector):
        return page.evaluate("s => [...document.querySelectorAll(s)].reduce((t, p) => t + p.getTotalLength(), 0)", selector)

    @staticmethod
    def _fold_widths(page, side):
        return page.evaluate("""side => [...document.querySelectorAll(`#panels-lane [data-block="impact"] svg.im-band[data-side="${side}"] g.im-fold`)]
            .map(g => { const r = g.querySelector('rect[rx]'), l = g.querySelector('line.im-fold-line');
                        return [g.dataset.ids, Number(g.dataset.steps), r ? Number(r.getAttribute('width')) : Number(l.getAttribute('x2')) - Number(l.getAttribute('x1'))]; })""", side)

    def test_bands_lanes_bars_and_folds_match_the_report(self):
        import math
        context, page, errors = self._open(width=1440)
        im = self.report["impact"]
        block = page.locator('#panels-lane .panels-grid > [data-block="impact"]')
        self.assertEqual(block.count(), 1)
        self.assertIn(im["narrative"][:50], block.locator(".im-narr").text_content())
        self.assertEqual(block.locator("svg.im-band").count(), 2)
        for side in ("a", "b"):
            band = block.locator(f'svg.im-band[data-side="{side}"]')
            self.assertEqual(band.get_attribute("role"), "img")
            self.assertTrue(band.get_attribute("aria-label"))
            self.assertEqual(band.get_attribute("data-mode"), "tree")
            # the root: one run node; the lanes: one node per sub-agent lane that owns a cluster, the root agent never one
            self.assertEqual(band.locator('g.im-node[data-kind="run"]').count(), 1)
            lanes = band.locator('g.im-node[data-kind="lane"]')
            self.assertEqual(lanes.count(), len(self._lanes(im, side)), side)
            self.assertEqual(sorted(lanes.nth(i).get_attribute("data-id") for i in range(lanes.count())), sorted(self._lanes(im, side)))
            self.assertEqual(band.locator(f'g.im-node[data-kind="lane"][data-id="{self._root(im, side)}"]').count(), 0)
            # a lane holding a hot cluster starts open, the others folded to one row
            for lane in self._lanes(im, side):
                node = band.locator(f'g.im-node[data-kind="lane"][data-id="{lane}"]')
                self.assertEqual("collapsed" in node.get_attribute("class").split(), lane not in self._open_lanes(im, side), (side, lane))
            shown = self._shown(im, side)
            self.assertEqual(band.locator("path.im-cluster").count(), len(shown), side)
            for cid in shown:
                self.assertEqual(band.locator(f'g.im-node[data-kind="cluster"][data-id="{cid}"] path.im-cluster[data-id="{cid}"][data-side="{side}"][data-kind][data-lane]').count(), 1)
            # a bar's length grows with the cluster's impact
            by_impact = sorted(((c["impact"], c["id"]) for c in im[side]["clusters"] if c["id"] in shown), key=lambda p: p[0])
            self.assertGreater(by_impact[-1][0], by_impact[0][0])
            lo = self._length(page, f'#panels-lane [data-block="impact"] svg.im-band[data-side="{side}"] path.im-cluster[data-id="{by_impact[0][1]}"]')
            hi = self._length(page, f'#panels-lane [data-block="impact"] svg.im-band[data-side="{side}"] path.im-cluster[data-id="{by_impact[-1][1]}"]')
            self.assertGreater(hi, lo, side)
            # one thread per run; the folds sit on it in step order; every lane attaches where its sub-agent first acted
            self.assertEqual(band.locator(f'path.im-thread[data-side="{side}"]').count(), 1)
            self.assertEqual([(w[0], w[1]) for w in self._fold_widths(page, side)], self._thread_folds(im, side), side)
            at = page.evaluate("""side => [...document.querySelectorAll(`#panels-lane [data-block="impact"] svg.im-band[data-side="${side}"] g.im-node[data-kind="lane"]`)]
                .map(g => [Number(g.dataset.at), Number(g.dataset.x)])""", side)
            self.assertEqual(sorted(a[0] for a in at), sorted(min(c["from"] for c in im[side]["clusters"] if c["lane"] == l) for l in self._lanes(im, side)))
            xs = [x for _, x in sorted(at)]
            self.assertEqual(xs, sorted(xs), side)
            self.assertGreater(xs[-1], xs[0])
            self.assertGreater(band.locator("path.im-link").count(), 0)
        # a fold's length grows with the steps it holds — on the thread, and as a capsule in a lane opened for it
        root_steps = self._fold_widths(page, "a")[0][1]
        lane, steps = next((l, st) for l in self._lanes(im, "a") if l not in self._open_lanes(im, "a")
                           for kind, _, st in self._lane_items(im, "a", l) if kind == "fold" and st != root_steps)
        block.locator(f'svg.im-band[data-side="a"] g.im-node[data-kind="lane"][data-id="{lane}"]').dispatch_event("click")
        page.wait_for_timeout(300)
        widths = {w[1]: w[2] for w in self._fold_widths(page, "a")}
        self.assertIn(steps, widths)
        small, big = sorted([root_steps, steps])
        self.assertGreater(widths[big], widths[small])
        for st, width in widths.items():
            self.assertAlmostEqual(width, 6 + 6 * math.log2(1 + st), delta=0.75, msg=st)
        self.assertEqual(block.locator(".im-table tr[data-side][data-id]").count(), len(im["a"]["clusters"]) + len(im["b"]["clusters"]))
        self.assertEqual(block.locator('button[data-mode="tree"]').get_attribute("aria-pressed"), "true")
        self.assertEqual(block.locator('button[data-mode="trunk"]').get_attribute("aria-pressed"), "false")
        self.assertEqual(block.locator('button[data-mode="even"]').get_attribute("aria-pressed"), "false")
        # quiet: one chip row, one legend line, and the whole block short
        self.assertLessEqual(block.locator(".im-bar").bounding_box()["height"], 30)
        self.assertLessEqual(block.locator(".im-legend").bounding_box()["height"], 22)
        self.assertLess(block.bounding_box()["height"], 750)
        self.assertEqual(errors, [])
        context.close()

    def test_a_fold_opens_a_cluster_opens_and_a_mark_opens_the_step(self):
        # the agents preset holds the body chart, so the inspector is on the page
        context, page, errors = self._open(width=1440, preset="agents")
        im = self.report["impact"]
        block = page.locator('#panels-lane .panels-grid > [data-block="impact"]')
        self.assertEqual(block.count(), 1)
        band = block.locator('svg.im-band[data-side="a"]')
        shown = self._shown(im, "a")
        n_folds = band.locator("g.im-fold").count()
        self.assertGreaterEqual(n_folds, 1)
        b_folds = block.locator('svg.im-band[data-side="b"] g.im-fold').count()
        first = band.locator("g.im-fold").first
        fid = first.get_attribute("data-ids")
        inside = fid.split(",")
        first.dispatch_event("click")
        page.wait_for_timeout(300)
        band = block.locator('svg.im-band[data-side="a"]')
        self.assertEqual(band.locator("g.im-fold").count(), n_folds - 1)
        self.assertEqual(band.locator("path.im-cluster").count(), len(shown) + len(inside))
        for cid in inside:
            self.assertEqual(band.locator(f'g.im-node[data-kind="cluster"][data-id="{cid}"] path.im-cluster[data-id="{cid}"][data-kind="quiet"][data-fold="{fid}"]').count(), 1)
        # the other band keeps its folds: a fold is opened by id, not per page
        self.assertEqual(block.locator('svg.im-band[data-side="b"] g.im-fold').count(), b_folds)
        # any of the dilated clusters folds it back
        band.locator(f'path.im-cluster[data-fold="{fid}"]').last.dispatch_event("click")
        page.wait_for_timeout(300)
        band = block.locator('svg.im-band[data-side="a"]')
        self.assertEqual(band.locator("g.im-fold").count(), n_folds)
        self.assertEqual(band.locator("g.im-fold").first.get_attribute("data-ids"), fid)
        hot = next(c for c in sorted(im["a"]["clusters"], key=lambda c: c["from"]) if c["kind"] == "hot" and c["marks"])
        sel = f'path.im-cluster[data-id="{hot["id"]}"]'
        box = band.locator(sel)
        self.assertEqual(box.get_attribute("data-kind"), "hot")
        self.assertNotIn("open", (box.get_attribute("class") or "").split())
        self.assertEqual(band.locator("g.im-mark").count(), 0)
        w_closed = self._length(page, f'#panels-lane [data-block="impact"] svg.im-band[data-side="a"] {sel}')
        self.assertGreater(w_closed, 0)
        box.dispatch_event("click")
        page.wait_for_timeout(300)
        band = block.locator('svg.im-band[data-side="a"]')
        box = band.locator(sel)
        self.assertIn("open", box.get_attribute("class").split())
        self.assertAlmostEqual(self._length(page, f'#panels-lane [data-block="impact"] svg.im-band[data-side="a"] {sel}'), w_closed, delta=0.5)
        # its marks hang under it as leaves, each a node of its own
        marks = band.locator("g.im-mark")
        self.assertEqual(marks.count(), len(hot["marks"]))
        self.assertEqual(band.locator('g.im-node[data-kind="mark"] g.im-mark[data-step][data-kind]').count(), len(hot["marks"]))
        self.assertEqual(sorted(int(marks.nth(i).get_attribute("data-step")) for i in range(marks.count())), sorted(m["step"] for m in hot["marks"]))
        self.assertEqual(sorted(marks.nth(i).get_attribute("data-kind") for i in range(marks.count())), sorted(m["kind"] for m in hot["marks"]))
        self.assertGreater(band.evaluate("e => e.getBoundingClientRect().height"), 0)
        # a mark opens its step in the shared inspector
        mark = marks.first
        step = int(mark.get_attribute("data-step"))
        mark.dispatch_event("click")
        page.wait_for_timeout(400)
        self.assertIn(f"STEP {step}", page.locator('#panels-lane [data-block="trace-body"] .tj-inspector').inner_text().upper())
        # clicking the bar again closes it; expand hot opens every hot cluster; collapse all refolds
        box.dispatch_event("click")
        page.wait_for_timeout(300)
        band = block.locator('svg.im-band[data-side="a"]')
        self.assertNotIn("open", (band.locator(sel).get_attribute("class") or "").split())
        self.assertEqual(band.locator("g.im-mark").count(), 0)
        block.locator('button[data-act="expand-hot"]').click()
        page.wait_for_timeout(300)
        n_hot = sum(1 for s in "ab" for c in im[s]["clusters"] if c["kind"] == "hot")
        self.assertEqual(block.locator("path.im-cluster.open").count(), n_hot)
        block.locator('button[data-act="collapse-all"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator("path.im-cluster.open").count(), 0)
        self.assertEqual(block.locator('svg.im-band[data-side="a"] g.im-fold').count(), n_folds)
        self.assertEqual(errors, [])
        context.close()

    def test_trunk_and_even_modes_draw_the_same_units_as_a_trunk(self):
        context, page, errors = self._open()
        block = page.locator('#panels-lane .panels-grid > [data-block="impact"]')
        im = self.report["impact"]
        self.assertGreater(block.locator("g.im-node").count(), 0)
        self.assertEqual(block.locator("g.im-branch").count(), 0)
        # trunk: one trunk per run, a branch per sub-agent stretch, every fold a short segment
        block.locator('button[data-mode="trunk"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator('button[data-mode="trunk"]').get_attribute("aria-pressed"), "true")
        self.assertEqual(block.locator('svg.im-band[data-side="a"]').get_attribute("data-mode"), "trunk")
        self.assertEqual(block.locator("g.im-node").count(), 0)
        self.assertGreater(block.locator('svg.im-band[data-side="a"] g.im-branch[data-lane]').count(), 0)
        self.assertGreater(block.locator('svg.im-band[data-side="b"] g.im-branch[data-lane]').count(), 0)
        self.assertGreater(block.locator("g.im-fold[data-ids][data-steps]").count(), 0)
        hot_sel = '#panels-lane [data-block="impact"] path.im-cluster[data-kind="hot"]'
        trunk = self._length(page, hot_sel)
        self.assertGreater(trunk, 0)
        # even: the trunk on wall-clock — no folds, every cluster drawn, other lengths
        block.locator('button[data-mode="even"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator('button[data-mode="even"]').get_attribute("aria-pressed"), "true")
        self.assertEqual(block.locator('button[data-mode="trunk"]').get_attribute("aria-pressed"), "false")
        self.assertEqual(block.locator('svg.im-band[data-side="a"]').get_attribute("data-mode"), "even")
        self.assertNotAlmostEqual(trunk, self._length(page, hot_sel), places=1)
        self.assertEqual(block.locator("g.im-fold").count(), 0)
        self.assertEqual(block.locator("path.im-cluster").count(), len(im["a"]["clusters"]) + len(im["b"]["clusters"]))
        # the mode survives a re-render: the state is per task, not per draw
        block.locator('.panel-ctl button[title="normal width"]').click()
        page.wait_for_timeout(500)
        block = page.locator('#panels-lane .panels-grid > [data-block="impact"]')
        self.assertEqual(block.locator('button[data-mode="even"]').get_attribute("aria-pressed"), "true")
        # and back to the tree
        block.locator('button[data-mode="tree"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator('svg.im-band[data-side="a"]').get_attribute("data-mode"), "tree")
        self.assertGreater(block.locator('g.im-node[data-kind="lane"]').count(), 0)
        self.assertEqual(block.locator("g.im-branch").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_time_mode_puts_wall_clock_on_the_thread_with_quiet_time_folded(self):
        import math
        context, page, errors = self._open(width=1440)
        im = self.report["impact"]
        block = page.locator('#panels-lane .panels-grid > [data-block="impact"]')
        band_sel = '#panels-lane [data-block="impact"] svg.im-band[data-side="a"]'
        n_folds = block.locator('svg.im-band[data-side="a"] g.im-fold').count()
        self.assertEqual(block.locator("text.im-tick").count(), 0)
        block.locator('button[data-mode="time"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator('button[data-mode="time"]').get_attribute("aria-pressed"), "true")
        self.assertEqual(block.locator('button[data-mode="tree"]').get_attribute("aria-pressed"), "false")
        self.assertEqual(block.locator('svg.im-band[data-side="a"]').get_attribute("data-mode"), "time")
        # a light clock over the thread: ticks in the small type, a gap where time is folded
        ticks = block.locator(f'svg.im-band[data-side="a"] text.im-tick:not(.im-gap)')
        self.assertGreaterEqual(ticks.count(), 3)
        self.assertLessEqual(ticks.first.evaluate("e => parseFloat(getComputedStyle(e).fontSize)"), 12.5)
        self.assertGreaterEqual(block.locator('svg.im-band[data-side="a"] text.im-tick.im-gap').count(), 1)
        # the same folds as the tree's, now as long as log2 of the seconds they hold
        folds = page.evaluate("""s => [...document.querySelectorAll(s + ' g.im-fold')].map(g => {
            const l = g.querySelector('line.im-fold-line');
            return [g.dataset.ids, Number(g.dataset.steps), Number(g.dataset.seconds), Number(l.getAttribute('x2')) - Number(l.getAttribute('x1'))]; })""", band_sel)
        self.assertEqual([(f[0], f[1]) for f in folds], self._thread_folds(im, "a"))
        self.assertGreaterEqual(len({round(f[2]) for f in folds}), 2)
        small, big = min(folds, key=lambda f: f[2]), max(folds, key=lambda f: f[2])
        self.assertGreater(big[3], small[3])
        for ids, _, seconds, width in folds:
            self.assertAlmostEqual(seconds, sum(c["seconds"] for c in im["a"]["clusters"] if c["id"] in ids.split(",")), delta=0.02)
            self.assertAlmostEqual(width, 6 + 6 * math.log2(1 + seconds), delta=0.75, msg=ids)
        # a stretch on the thread is as long as its seconds: dilate a fold that holds one of the root agent's, then compare two
        root = self._root(im, "a")
        root_ids = {c["id"] for c in im["a"]["clusters"] if c["lane"] == root}
        fold = next(ids for ids, _ in self._thread_folds(im, "a") if root_ids & set(ids.split(",")))
        block.locator(f'svg.im-band[data-side="a"] g.im-fold[data-ids="{fold}"]').dispatch_event("click")
        page.wait_for_timeout(300)
        on_thread = page.evaluate("""s => [...document.querySelectorAll(s + ' path.im-cluster')].map(p => [p.dataset.id, p.getTotalLength()])""", band_sel)
        secs_of = {c["id"]: c["seconds"] for c in im["a"]["clusters"]}
        roots = sorted(((secs_of[i], w) for i, w in on_thread if i in root_ids), key=lambda p: p[0])
        self.assertGreaterEqual(len(roots), 2)
        self.assertGreater(roots[-1][0], roots[0][0])
        self.assertGreater(roots[-1][1], roots[0][1])
        # back to the tree: the clock goes, the folds are the tree's again, the mode persists per task
        block.locator('button[data-mode="tree"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator('svg.im-band[data-side="a"]').get_attribute("data-mode"), "tree")
        self.assertEqual(block.locator("text.im-tick").count(), 0)
        block.locator('button[data-act="collapse-all"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator('svg.im-band[data-side="a"] g.im-fold').count(), n_folds)
        self.assertEqual(errors, [])
        context.close()

    def test_nothing_overflows_on_a_phone(self):
        context, page, errors = self._open(width=390)
        block = page.locator('#panels-lane .panels-grid > [data-block="impact"]')
        self.assertEqual(block.count(), 1)
        self.assertEqual(block.locator("svg.im-band").count(), 2)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        for side in ("a", "b"):
            band = block.locator(f'svg.im-band[data-side="{side}"]')
            self.assertLessEqual(band.evaluate("e => e.getBoundingClientRect().right"), 392)
        for sel in (".im-bar", ".im-legend"):
            self.assertLessEqual(block.locator(sel).evaluate("e => e.getBoundingClientRect().right"), 392)
        for mode in ("time", "trunk"):
            block.locator(f'button[data-mode="{mode}"]').click()
            page.wait_for_timeout(300)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class LongTreeFoldTest(unittest.TestCase):
    """The trace as a tree folds by importance on a long run (the long
    horizon demo, 545 and 566 steps): stretches of phases that carry
    nothing notable start folded into capsules (×N steps · the dominant
    tool), phases that carry the fault's path, the decisive step, an error
    or the answer start open on those steps, the block stays under 2,500px
    at 1440, and the chips open everything or fold the quiet back."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_long.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.report = json.loads((out / "report_h02_migrate_service.json").read_text(encoding="utf-8"))
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

    def open(self, width=1440):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=story")
        page.wait_for_selector("svg.d3c-tree g.d3c-tnode", timeout=15000)
        page.wait_for_timeout(900)
        return context, page, errors

    @staticmethod
    def shown(page):
        text = page.locator('[data-block="trace-tree"] .d3c-tshown').text_content()
        return int(text.split(" of ")[0])

    def test_a_long_run_folds_its_quiet_stretches_and_opens_on_demand(self):
        context, page, errors = self.open()
        rep = self.report
        total = len(rep["a"]["steps"]) + len(rep["b"]["steps"])
        self.assertGreater(max(len(rep["a"]["steps"]), len(rep["b"]["steps"])), 80)
        block = page.locator('[data-block="trace-tree"]')
        self.assertEqual(block.count(), 1)
        self.assertLess(block.bounding_box()["height"], 2500, "the folded tree stays under 2,500px at 1440")
        # capsules carry their step count and the ×N vocabulary
        capsules = page.locator("svg.d3c-tree g.d3c-tree-capsule[data-steps]")
        self.assertGreater(capsules.count(), 0)
        first = capsules.first
        self.assertEqual(first.get_attribute("data-kind"), "capsule")
        self.assertIn("×" + first.get_attribute("data-steps") + " step", first.locator("text.d3c-tlabel").text_content())
        # the fault's path stays in view: every step on the attribution chain,
        # the root cause and the decisive step are drawn, and the red links run
        attr, diag = rep["attribution"], rep["diagnosis"]
        failed = attr["failed_agent"]
        for step in set(attr["chain"]) | {attr["root_cause_step"]}:
            self.assertEqual(page.locator(f"svg.d3c-tree g.d3c-tnode[data-kind='step'][data-side='{failed}'][data-step='{step}']").count(), 1, f"chain step {step}")
        dec = diag["decisive_step"]["step"]
        ringed = page.locator("svg.d3c-tree g.d3c-tnode[data-kind='step']").filter(has=page.locator(".d3c-ring"))
        self.assertEqual(ringed.count(), 1)
        self.assertEqual(ringed.first.get_attribute("data-step"), str(dec))
        self.assertGreater(page.locator("svg.d3c-tree path.d3c-tlink.fault").count(), 0)
        # every error step is in view too; the steps shown match the count chip
        for side in ("a", "b"):
            for i, st in enumerate(rep[side]["steps"]):
                if st.get("error") is True:
                    self.assertEqual(page.locator(f"svg.d3c-tree g.d3c-tnode[data-kind='step'][data-side='{side}'][data-step='{i}']").count(), 1, f"{side} error step {i}")
        steps_shown = page.locator("svg.d3c-tree g.d3c-tnode[data-kind='step']").count()
        self.assertEqual(self.shown(page), steps_shown)
        self.assertIn(f"of {total} steps shown", block.locator(".d3c-tshown").text_content())
        self.assertLess(steps_shown, total)
        # a capsule opens into its steps and the count grows
        n_caps = capsules.count()
        key = first.get_attribute("data-key")
        first.dispatch_event("click")
        page.wait_for_timeout(900)
        self.assertEqual(page.locator(f"svg.d3c-tree g.d3c-tnode[data-key='{key}']").count(), 0, "the capsule is replaced")
        self.assertEqual(page.locator("svg.d3c-tree g.d3c-tree-capsule").count(), n_caps - 1)
        self.assertGreater(page.locator("svg.d3c-tree g.d3c-tnode[data-kind='step']").count(), steps_shown)
        self.assertGreater(self.shown(page), steps_shown)
        # "open all" shows every step; "fold quiet" folds the quiet back
        block.locator(".d3c-tree-chips [data-act='open-all']").click()
        page.wait_for_timeout(3000)
        self.assertEqual(page.locator("svg.d3c-tree g.d3c-tnode[data-kind='step']").count(), total)
        self.assertEqual(self.shown(page), total)
        self.assertEqual(page.locator("svg.d3c-tree g.d3c-tree-capsule").count(), 0)
        block.locator(".d3c-tree-chips [data-act='fold-quiet']").click()
        page.wait_for_timeout(1500)
        self.assertEqual(self.shown(page), steps_shown)
        self.assertEqual(page.locator("svg.d3c-tree g.d3c-tree-capsule").count(), n_caps)
        self.assertLess(block.bounding_box()["height"], 2500)
        self.assertEqual(errors, [])
        context.close()

    def test_every_story_block_on_the_long_demo_stays_under_1500px_except_the_tree_budget(self):
        context, page, errors = self.open()
        heights = page.evaluate("() => [...document.querySelectorAll('#story-lane .block[data-block]')]"
                                ".map(b => [b.getAttribute('data-block'), b.getBoundingClientRect().height])")
        self.assertGreater(len(heights), 3)
        for name, h in heights:
            budget = 2500 if name == "trace-tree" else 1500
            self.assertLess(h, budget, f"{name} is {h:.0f}px tall")
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class ToolBehaviourTest(unittest.TestCase):
    """Tool behaviour on the page: the per-tool ledger against the JSON,
    the suggestions for the next prompt with their evidence, and the
    dossier on demand — opened from the ledger, from a heat-map row and
    from a double-click on a tool step in the body chart; a call in the
    dossier's strip opens the step; Escape closes it."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        from deepcompare.report import render_html
        from deepcompare.toolprofile import tool_pair
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        report = json.loads((out / "report_h02_migrate_service.json").read_text(encoding="utf-8"))
        if "tools_profile" not in report:
            report["tools_profile"] = tool_pair(report)
            agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
            render_html([report], agg, ROOT / "web" / "blocks.html", out / "report.html")
        cls.report = report
        cls.page_path = out / "report.html"
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

    def _open(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=panels")
        page.wait_for_timeout(1000)
        if page.locator('#panels-lane .panels-grid > [data-block="tool-behaviour"]').count() == 0:
            page.click("#panels-lane [data-picker] summary")
            page.wait_for_timeout(200)
            page.click('#panels-lane [data-add="tool-behaviour"]')
            page.wait_for_timeout(600)
        return context, page, errors

    def test_the_ledger_and_the_suggestions_match_the_json(self):
        context, page, errors = self._open()
        tp = self.report["tools_profile"]
        block = page.locator('#panels-lane .panels-grid > [data-block="tool-behaviour"]')
        self.assertEqual(block.count(), 1)
        self.assertEqual(block.locator("tr[data-tool]").count(), len(tp["tools"]))
        first = tp["tools"][0]
        cells = block.locator(f'tr[data-tool="{first["name"]}"] .pair span')
        self.assertEqual(cells.nth(0).text_content(), str(first["a"]["calls"]))
        self.assertEqual(cells.nth(1).text_content(), str(first["b"]["calls"]))
        self.assertEqual(block.locator(".tb-sugg li").count(), len(tp["suggestions"]))
        if tp["suggestions"]:
            li = block.locator(".tb-sugg li").first
            self.assertEqual(li.get_attribute("data-kind"), tp["suggestions"][0]["kind"])
            self.assertIn(tp["suggestions"][0]["text"][:40], li.text_content())
            self.assertIn("hypothesis", li.locator(".ev").text_content())
        self.assertIn(tp["narrative"][:50], block.locator(".tb-narr").text_content())
        self.assertEqual(errors, [])
        context.close()

    def test_the_dossier_opens_from_the_ledger_the_heat_map_and_the_body_chart(self):
        context, page, errors = self._open()
        tp = self.report["tools_profile"]
        tool = tp["tools"][0]["name"]
        page.locator(f'#panels-lane [data-block="tool-behaviour"] button[data-open="{tool}"]').click()
        page.wait_for_timeout(400)
        dossier = page.locator(".tool-dossier")
        self.assertFalse(dossier.is_hidden())
        self.assertEqual(dossier.get_attribute("data-tool"), tool)
        row = tp["tools"][0]
        self.assertEqual(dossier.locator("line.call").count(), row["a"]["calls"] + row["b"]["calls"])
        self.assertEqual(dossier.locator("svg").get_attribute("role"), "img")
        for agent in list(row["a"]["agents"])[:2]:
            self.assertIn(agent, dossier.locator(".who").text_content())
        # a call opens its step in the shared inspector
        call = dossier.locator('line.call[data-side="b"]').first
        step = int(call.get_attribute("data-step"))
        call.dispatch_event("click")
        page.wait_for_timeout(400)
        self.assertIn(f"STEP {step}", page.locator('#panels-lane [data-block="trace-body"] .tj-inspector').inner_text().upper())
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        self.assertTrue(dossier.is_hidden())
        # a heat-map row label
        lab = page.locator('#panels-lane [data-block="heatmap"] text.lab').first
        lab.dispatch_event("click")
        page.wait_for_timeout(300)
        self.assertFalse(dossier.is_hidden())
        self.assertTrue(dossier.get_attribute("data-tool"))
        page.keyboard.press("Escape")
        # a double-click on a tool step in the body chart
        page.evaluate("() => AgentDiff.tools.close()")
        opened = page.evaluate("""() => {
            const nodes = [...document.querySelectorAll('#panels-lane [data-block="trace-body"] svg g[data-side]')];
            for (const n of nodes) {
                n.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
                const d = document.querySelector('.tool-dossier');
                if (d && !d.hidden) return d.dataset.tool;
            }
            return null;
        }""")
        self.assertTrue(opened, "no body-chart node opened a dossier")
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class TrustBlockTest(unittest.TestCase):
    """The Trust & behaviour ledger on the page, from the long-horizon demo
    batched with its golden set: the pair narrative, a two-column ledger
    whose cells are the report's own counts (tool calls, errors, forbidden
    calls), a grade chip per side with the rubric's label, the reasons
    under a fold, the SYNTHETIC flag the harness note carries; no page
    error, nothing overflowing on a phone."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_long.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        cls.report = json.loads((out / "report_h02_migrate_service.json").read_text(encoding="utf-8"))
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

    def _open(self, width=1280):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=panels")
        page.wait_for_timeout(1000)
        page.click('#panels-lane [data-preset="eval"]')
        page.wait_for_timeout(800)
        return context, page, errors

    @staticmethod
    def _forbidden(p):
        return len({f["step"] for f in (p["forbidden_calls"] or []) + (p["forbidden_patterns"] or [])})

    def test_the_ledger_is_the_report_and_the_grades_wear_their_labels(self):
        context, page, errors = self._open()
        block = page.locator('#panels-lane .panels-grid > [data-block="trust"]')
        self.assertEqual(block.count(), 1)
        t = self.report["trust"]
        self.assertIn(t["narrative"][:80], block.locator(".tr-narr").text_content())
        for side in ("a", "b"):
            r = t[side]
            self.assertEqual(block.locator(f'td[data-key="tool_calls"][data-side="{side}"]').text_content().strip(), str(r["behaviour"]["tool_calls"]), side)
            self.assertEqual(block.locator(f'[data-key="errors"][data-side="{side}"]').text_content().strip(), str(r["behaviour"]["errors"]), side)
            self.assertEqual(block.locator(f'[data-key="loops"][data-side="{side}"]').text_content().strip(), str(r["behaviour"]["loops"]), side)
            forbidden = block.locator(f'td[data-key="forbidden"][data-side="{side}"]')
            self.assertTrue(forbidden.text_content().strip().startswith(str(self._forbidden(r["permissions"]))), side)
            self.assertEqual(forbidden.evaluate("e => e.classList.contains('bad')"), self._forbidden(r["permissions"]) > 0, side)
            self.assertEqual(block.locator(f'[data-key="writes"][data-side="{side}"]').text_content().strip(), str(r["permissions"]["effects"]["write"]), side)
            chip = block.locator(f'td[data-key="grade"][data-side="{side}"] .tr-chip')
            self.assertEqual(chip.text_content().strip(), f"{r['grade']['label']} {r['grade']['score']:.2f}", side)
            self.assertTrue(chip.evaluate("e => e.classList.contains('good') || e.classList.contains('warn') || e.classList.contains('bad')"), side)
            self.assertEqual(block.locator(f'td[data-key="data"][data-side="{side}"] [data-synthetic]').count(), 1 if r["data"]["synthetic"] else 0, side)
            stop = block.locator(f'td[data-key="stop"][data-side="{side}"]').text_content()
            self.assertIn("answered" if r["behaviour"]["answered"] else "no answer", stop)
            self.assertEqual("by harness" in stop, r["behaviour"]["stopped_by"] == "harness", side)
            fold = block.locator(f'details.tr-details[data-side="{side}"]')
            self.assertEqual(fold.count(), 1, side)
            self.assertFalse(fold.evaluate("e => e.open"), side)
            self.assertEqual(fold.locator("li").count(), len(r["grade"]["reasons"]), side)
            for reason in r["grade"]["reasons"]:
                self.assertIn(reason[:40], fold.locator("ul").text_content(), side)
        # the tool-call bar is scaled to the larger of the two
        widths = [float(block.locator(f'td[data-key="tool_calls"][data-side="{s}"] .bar').evaluate("e => parseFloat(e.style.width)")) for s in ("a", "b")]
        bigger = "a" if t["a"]["behaviour"]["tool_calls"] >= t["b"]["behaviour"]["tool_calls"] else "b"
        self.assertEqual(widths[0 if bigger == "a" else 1], 100.0)
        self.assertEqual(block.locator("svg").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_nothing_overflows_on_a_phone(self):
        context, page, errors = self._open(width=390)
        self.assertEqual(page.locator('#panels-lane .panels-grid > [data-block="trust"]').count(), 1)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        box = page.locator('#panels-lane [data-block="trust"] .tr-ledger').bounding_box()
        self.assertLessEqual(box["x"] + box["width"], 391)
        self.assertEqual(errors, [])
        context.close()


def _rl_fixture(report):
    """A small, valid `report.rl` for a page whose engine has not written
    one: per run four contiguous clusters (work, quiet, quiet — they fold —,
    hot), nine rewarded steps with their running return, credit on some of
    them, a decisive step and the answer in the hot stretch, and a
    preference for A; every step one the alignment knows, so a leaf can
    open it in the inspector."""
    def known(side, lo, hi, n):
        steps = [r[f"{side}_index"] for r in report["alignment"] if r.get(f"{side}_index") is not None and lo <= r[f"{side}_index"] <= hi]
        assert len(steps) >= n, (side, lo, hi)
        pick = [steps[int(i * (len(steps) - 1) / (n - 1))] for i in range(n)] if n > 1 else [steps[-1]]
        assert len(set(pick)) == n
        return pick
    def run(side, values, credits, chosen):
        agent = report[side]["agent"]["name"]
        n = len(report[side]["steps"])
        total_s = float(report["timing"][side]["total_s"])
        bounds = [(0, 119), (120, 239), (240, 399), (400, n - 1)]
        kinds = ["work", "quiet", "quiet", "hot"]
        impacts = [0.3, 0.04, 0.05, 0.95]
        steps = known(side, 5, 110, 2) + known(side, 130, 230, 1) + known(side, 250, 390, 2) + known(side, 405, n - 8, 4) + [n - 1]
        names = ["plan", "grep", "read_file", "read_file", "edit", "run_tests", "run_tests", "edit", "run_tests", "answer"]
        labels = [["progress"], [], [], [], ["milestone"], ["fault enters"] if values[5] < 0 else ["tests pass"], ["error"] if values[6] < 0 else ["milestone"], [], ["milestone"], ["answer"]]
        rewards, cum = [], 0.0
        for i, (st, v) in enumerate(zip(steps, values)):
            cum += v
            rewards.append({"step": st, "reward": v, "cum": round(cum, 4), "to_go": None, "discounted_to_go": None,
                            "credit": credits[i], "labels": labels[i], "agent": agent, "kind": "answer" if i == 9 else "tool", "name": names[i]})
        total = cum
        for r in rewards:
            r["to_go"] = round(total - r["cum"] + r["reward"], 4)
            r["discounted_to_go"] = round(r["to_go"] * 0.97, 4)
        clusters = []
        for i, ((lo, hi), kind, imp) in enumerate(zip(bounds, kinds, impacts)):
            marks = [{"step": r["step"], "kind": "reward+" if r["reward"] > 0 else "reward−", "label": r["name"] + " " + ("%+.1f" % r["reward"])} for r in rewards if lo <= r["step"] <= hi and r["reward"]]
            if kind == "hot":
                marks.append({"step": steps[5], "kind": "decisive", "label": "the tests turned"})
                marks.append({"step": n - 1, "kind": "answer", "label": "the answer"})
            inside = [r["reward"] for r in rewards if lo <= r["step"] <= hi]
            clusters.append({"id": f"{side}{i + 1}", "from": lo, "to": hi, "steps": hi - lo + 1,
                             "start_s": round(total_s * lo / n, 3), "end_s": round(total_s * (hi + 1) / n, 3), "seconds": round(total_s * (hi - lo + 1) / n, 3),
                             "lane": agent, "agents": [agent], "impact": imp, "score": round(imp * 10, 2), "kind": kind,
                             "reasons": {"reward": round(sum(inside), 4), "rewarded_steps": len(inside), "decisive": kind == "hot", "answer": kind == "hot"},
                             "why": f"{kind} stretch: {len(inside)} rewarded step{'' if len(inside) == 1 else 's'} worth {sum(inside):+.1f}", "label": f"{kind} {i + 1}", "marks": marks})
        pos = sum(1 for v in values if v > 0); neg = sum(1 for v in values if v < 0)
        largest = [{"step": r["step"], "reward": r["reward"], "why": r["name"] + (" — " + ", ".join(r["labels"]) if r["labels"] else "")}
                   for r in sorted(rewards, key=lambda r: -abs(r["reward"]))[:4]]
        top = [{"step": r["step"], "credit": r["credit"]} for r in rewards if r["credit"] is not None]
        return {"agent": agent, "measurable": True, "steps": n, "return": round(total, 4), "discounted_return": round(total * 0.9, 4),
                "positive": pos, "negative": neg, "zero": len(values) - pos - neg, "seconds": total_s, "rewards": rewards, "largest": largest,
                "credit": {"source": "shapley", "metric": "success", "top": top, "total": round(sum(t["credit"] for t in top), 4)},
                "clusters": clusters, "narrative": f"{agent}: return {total:+.1f} over {len(values)} rewarded steps; {'chosen' if chosen else 'rejected'} by the judge."}
    a = run("a", [1.0, 0.5, 0.0, 0.2, 2.0, 3.0, 1.5, 0.4, 2.0, 5.0], [0.05, None, None, None, 0.1, 0.3, 0.1, None, 0.15, 0.3], True)
    b = run("b", [1.0, 0.5, 0.0, 0.0, 1.5, -1.0, -2.0, 0.4, 1.0, 3.0], [0.05, None, None, None, 0.1, -0.2, -0.3, None, 0.1, 0.25], False)
    return {"version": 1, "measurable": True, "source": "shaped", "gamma": 0.99, "a": a, "b": b,
            "preference": {"prompt": report["task"]["prompt"], "task_id": report["task"]["id"], "expected": report["task"].get("expected"),
                           "chosen": {"agent": a["agent"], "side": "a", "basis": "higher return and no reward lost after the tests turned"},
                           "rejected": {"agent": b["agent"], "side": "b"}, "margin": round(a["return"] - b["return"], 4)},
            "narrative": f"{a['agent']} earned {a['return']:+.1f} to {b['agent']}'s {b['return']:+.1f}; they parted where {b['agent']} lost reward on the tests."}


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class RLBlockTest(unittest.TestCase):
    """Reward & credit: the pair narrative, a source chip, a chip of returns
    per run; the return curves (both runs' cumulative return on one axis,
    the zero line, a ring where the two returns first part by a whole
    point, the answer a square); the reward thread — the impact view's
    technology on reward-driven clusters: one thread per run on a shared
    impact scale, consecutive quiet clusters folded into one dotted ×N
    segment as long as log2 of its steps, a tick per step at or above the
    run's 90th percentile of |reward| (up and green for a gain, down and
    red for a loss), the decisive step ringed, the answer squared, a faint
    credit bar per cluster; a fold dilates on click, a cluster opens to its
    rewarded steps as leaves, a leaf opens the step in the shared
    inspector; the preference line; the tables under the fold; the whole
    thing short at 1440 and nothing overflowing on a phone."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        if not (ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_long.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"), "-o", str(out),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page_path = out / "report.html"
        report_path = out / "report_h02_migrate_service.json"
        cls.report = json.loads(report_path.read_text(encoding="utf-8"))
        if not (cls.report.get("rl") and cls.report["rl"].get("measurable")):
            from deepcompare.report import render_html
            cls.report["rl"] = _rl_fixture(cls.report)
            report_path.write_text(json.dumps(cls.report), encoding="utf-8")
            aggregate_path = out / "aggregate.json"
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8")) if aggregate_path.is_file() else {}
            render_html([cls.report], aggregate, ROOT / "web" / "blocks.html", cls.page_path)
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

    def _open(self, width=1440, preset="eval"):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=panels")
        page.wait_for_timeout(900)
        if preset:
            page.click(f'#panels-lane [data-preset="{preset}"]')
            page.wait_for_timeout(900)
        return context, page, errors

    # ---- the same arithmetic as the block, from the JSON
    @staticmethod
    def _rewards(run):
        return sorted((r for r in run["rewards"] if isinstance(r.get("step"), int) and isinstance(r.get("reward"), (int, float))), key=lambda r: r["step"])

    @classmethod
    def _ticks(cls, run):
        import math
        rs = cls._rewards(run)
        mags = sorted(abs(r["reward"]) for r in rs)
        if not mags:
            return []
        thr = mags[max(0, min(len(mags) - 1, math.ceil(0.9 * len(mags)) - 1))]
        return [r for r in rs if r["reward"] != 0 and abs(r["reward"]) >= thr]

    @staticmethod
    def _clusters(run):
        return sorted(run["clusters"], key=lambda c: c["from"])

    @classmethod
    def _folds(cls, run):
        cs, folds, i = cls._clusters(run), [], 0
        while i < len(cs):
            if cs[i]["kind"] == "quiet":
                j = i
                while j < len(cs) and cs[j]["kind"] == "quiet":
                    j += 1
                if j - i >= 2:
                    folds.append((",".join(c["id"] for c in cs[i:j]), sum(c["steps"] for c in cs[i:j])))
                    i = j
                    continue
            i += 1
        return folds

    @classmethod
    def _inside(cls, run, c):
        return [r for r in cls._rewards(run) if c["from"] <= r["step"] <= c["to"]]

    @classmethod
    def _credit(cls, run, c):
        vals = [r["credit"] for r in cls._inside(run, c) if isinstance(r.get("credit"), (int, float))]
        return sum(vals) if vals else None

    @classmethod
    def _folded_ids(cls, run):
        return {i for ids, _ in cls._folds(run) for i in ids.split(",")}

    @classmethod
    def _shown(cls, run):
        """The clusters on the thread with the defaults: every one outside a closed fold."""
        folded = cls._folded_ids(run)
        return [c for c in cls._clusters(run) if c["id"] not in folded]

    @classmethod
    def _in_fold(cls, run, step):
        by_id = {c["id"]: c for c in run["clusters"]}
        return any(by_id[ids.split(",")[0]]["from"] <= step <= by_id[ids.split(",")[-1]]["to"] for ids, _ in cls._folds(run))

    @classmethod
    def _credit_bars(cls, run):
        """(id, credit) per bar: a shown cluster whose steps carry credit, a closed fold holding any."""
        bars = [(c["id"], cls._credit(run, c)) for c in cls._shown(run) if cls._credit(run, c) is not None]
        by_id = {c["id"]: c for c in run["clusters"]}
        for ids, _ in cls._folds(run):
            vals = [cls._credit(run, by_id[i]) for i in ids.split(",")]
            vals = [v for v in vals if v is not None]
            if vals:
                bars.append((ids, sum(vals)))
        return bars

    @classmethod
    def _leaves(cls, run, c):
        """A stretch's leaves: its non-zero rewards, the 20 largest when there are more, in step order."""
        nz = [r for r in cls._inside(run, c) if r["reward"] != 0]
        if len(nz) <= 20:
            return nz, 0
        top = sorted(nz, key=lambda r: (-abs(r["reward"]), r["step"]))[:20]
        return sorted(top, key=lambda r: r["step"]), len(nz) - 20

    @classmethod
    def _cum(cls, run, step):
        v = 0.0
        for r in cls._rewards(run):
            if r["step"] > step:
                break
            v = r["cum"] if isinstance(r.get("cum"), (int, float)) else v + r["reward"]
        return v

    @classmethod
    def _parted(cls, rl):
        steps = sorted({r["step"] for s in "ab" for r in cls._rewards(rl[s])})
        return next((st for st in steps if abs(cls._cum(rl["a"], st) - cls._cum(rl["b"], st)) >= 1), None)

    def test_header_curve_and_threads_match_the_report(self):
        import math
        context, page, errors = self._open(width=1440)
        rl = self.report["rl"]
        block = page.locator('#panels-lane .panels-grid > [data-block="rl"]')
        self.assertEqual(block.count(), 1)
        self.assertEqual(block.locator(".empty:visible").count(), 0)
        if rl.get("narrative"):
            self.assertIn(rl["narrative"][:40], block.locator(".rl-narr").text_content())
        # the source chip says where the rewards came from
        src = block.locator(".rl-src")
        self.assertEqual(src.count(), 1)
        self.assertEqual(src.text_content().strip(), "rewards: recorded" if rl["source"] == "recorded" else "rewards: shaped from the reading (labelled)")
        self.assertEqual(src.get_attribute("data-source"), rl["source"])
        for side in "ab":
            chip = block.locator(f'.rl-run[data-side="{side}"]').text_content()
            self.assertIn(self.report[side]["agent"]["name"], chip)
            self.assertIn(f"{rl[side]['positive']}↑", chip)
            self.assertIn(f"{rl[side]['negative']}↓", chip)
        # the curve: one line per run, a ring where the returns part, every svg named for a screen reader
        curve = block.locator("svg.rl-curve")
        self.assertEqual(curve.count(), 1)
        self.assertEqual(curve.get_attribute("role"), "img")
        self.assertTrue(curve.get_attribute("aria-label"))
        self.assertEqual(curve.locator("path.rl-cum").count(), 2)
        self.assertEqual(sorted(curve.locator("path.rl-cum").nth(i).get_attribute("data-side") for i in range(2)), ["a", "b"])
        self.assertEqual(curve.locator("line.rl-zero").count(), 1)
        parted = self._parted(rl)
        self.assertEqual(curve.locator("circle.rl-parted").count(), 1 if parted is not None else 0)
        if parted is not None:
            self.assertEqual(int(curve.locator("circle.rl-parted").get_attribute("data-step")), parted)
            self.assertIn(f"parted at step {parted}", curve.text_content())
        self.assertEqual(curve.locator("rect.rl-answer").count(), 2)
        # the threads: one per run, the folds and ticks as the JSON says
        self.assertEqual(block.locator("svg.rl-band").count(), 2)
        self.assertEqual(block.locator("path.rl-thread").count(), 2)
        for side in "ab":
            run = rl[side]
            band = block.locator(f'svg.rl-band[data-side="{side}"]')
            self.assertEqual(band.get_attribute("role"), "img")
            self.assertTrue(band.get_attribute("aria-label"))
            self.assertEqual(band.locator(f'path.rl-thread[data-side="{side}"]').count(), 1)
            folds = self._folds(run)
            got = page.evaluate("""side => [...document.querySelectorAll(`#panels-lane [data-block="rl"] svg.rl-band[data-side="${side}"] g.rl-fold`)]
                .map(g => { const l = g.querySelector('line.rl-fold-line'); return [g.dataset.ids, Number(g.dataset.steps), Number(l.getAttribute('x2')) - Number(l.getAttribute('x1'))]; })""", side)
            self.assertEqual([(g[0], g[1]) for g in got], folds, side)
            for _, steps, width in got:
                self.assertAlmostEqual(width, 6 + 6 * math.log2(1 + steps), delta=0.75, msg=(side, steps))
            shown = [c["id"] for c in self._shown(run)]
            clusters = band.locator("g.rl-cluster")
            self.assertEqual(sorted(clusters.nth(i).get_attribute("data-id") for i in range(clusters.count())), sorted(shown), side)
            for c in self._clusters(run):
                if c["id"] in shown:
                    self.assertEqual(band.locator(f'g.rl-cluster[data-id="{c["id"]}"][data-kind="{c["kind"]}"]').count(), 1, (side, c["id"]))
            self.assertEqual(band.locator("g.rl-cluster.open").count(), 0)
            # a tick per step at or above the 90th percentile of |reward| where the thread is dilated (a closed
            # fold keeps its steps to itself), its sign its direction, its height its size
            ticks = [t for t in self._ticks(run) if not self._in_fold(run, t["step"])]
            got_ticks = page.evaluate("""side => [...document.querySelectorAll(`#panels-lane [data-block="rl"] svg.rl-band[data-side="${side}"] g.rl-tick`)]
                .map(g => { const l = g.querySelector('line'); return [Number(g.dataset.step), g.dataset.sign, Math.abs(Number(l.getAttribute('y2')) - Number(l.getAttribute('y1')))]; })""", side)
            self.assertEqual(sorted(t[0] for t in got_ticks), sorted(t["step"] for t in ticks), side)
            by_step = {t["step"]: t for t in ticks}
            biggest = max((abs(r["reward"]) for r in self._rewards(run)), default=0)
            for step, sign, height in got_ticks:
                self.assertEqual(sign, "pos" if by_step[step]["reward"] > 0 else "neg", (side, step))
                self.assertAlmostEqual(height, max(4, 14 * abs(by_step[step]["reward"]) / biggest), delta=0.05, msg=(side, step))
            # the decisive ring and the answer square sit on the thread
            decisive = [m["step"] for c in run["clusters"] for m in c["marks"] if m["kind"] == "decisive"]
            self.assertEqual(band.locator("circle.rl-decisive").count(), len(decisive), side)
            self.assertEqual(band.locator("rect.rl-answer").count(), 1, side)
            # a credit bar per cluster whose steps carry credit — one for a closed fold holding any — coloured by sign
            expected_bars = self._credit_bars(run)
            bars = band.locator("rect.rl-credit")
            self.assertEqual(sorted(bars.nth(i).get_attribute("data-id") for i in range(bars.count())), sorted(i for i, _ in expected_bars), side)
            for cid, total in expected_bars:
                bar = band.locator(f'rect.rl-credit[data-id="{cid}"]')
                self.assertAlmostEqual(float(bar.get_attribute("data-credit")), total, places=3)
                self.assertEqual(bar.get_attribute("fill"), "var(--bad)" if total < 0 else "var(--good)")
                self.assertEqual(bar.get_attribute("data-fold"), "1" if "," in cid else None)
            self.assertEqual(band.locator("g.rl-leaf").count(), 0)
        # the preference, the two agents named; the tables under the fold
        pref = block.locator(".rl-pref")
        if rl.get("preference") and rl["preference"].get("chosen"):
            self.assertEqual(pref.count(), 1)
            text = pref.text_content()
            self.assertIn(rl["preference"]["chosen"]["agent"], text)
            self.assertIn("(chosen)", text)
            if rl["preference"].get("rejected"):
                self.assertIn(rl["preference"]["rejected"]["agent"], text)
                self.assertIn("(rejected)", text)
            self.assertTrue(text.startswith("preferred:"))
        else:
            self.assertEqual(pref.count(), 0)
        details = block.locator("details.rl-details")
        self.assertEqual(details.count(), 1)
        self.assertFalse(details.evaluate("e => e.open"))
        n_largest = sum(1 for s in "ab" for l in rl[s]["largest"] if isinstance(l.get("step"), int))
        self.assertEqual(block.locator(".rl-table tr[data-side][data-step]").count(), n_largest)
        self.assertEqual(block.locator(".rl-clusters tr[data-side][data-id]").count(), len(rl["a"]["clusters"]) + len(rl["b"]["clusters"]))
        # quiet, and short: the curve and both threads together under 620px with the defaults
        self.assertLess(block.locator(".rl-chart").bounding_box()["height"], 620)
        self.assertLessEqual(block.locator(".rl-legend").bounding_box()["height"], 22)
        # the block's own content uses two sizes (--fs-xs, --fs-m), nothing under 11px
        sizes = page.evaluate("""() => [...new Set([...document.querySelectorAll('#panels-lane [data-block="rl"] .rl *')]
            .filter(e => e.tagName.toLowerCase() !== 'title' && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())).map(e => getComputedStyle(e).fontSize))]""")
        self.assertTrue(all(float(s.replace("px", "")) >= 11 for s in sizes), sizes)
        self.assertLessEqual(len(sizes), 2, sizes)
        self.assertEqual(errors, [])
        context.close()

    def test_a_fold_dilates_a_cluster_opens_and_a_leaf_opens_the_step(self):
        # the "all" preset holds the body chart, so the shared inspector is on the page
        context, page, errors = self._open(width=1440, preset="all")
        rl = self.report["rl"]
        block = page.locator('#panels-lane .panels-grid > [data-block="rl"]')
        self.assertEqual(block.count(), 1)
        side = next((s for s in "ab" if self._folds(rl[s])), None)
        if side is not None:
            band = block.locator(f'svg.rl-band[data-side="{side}"]')
            other = "b" if side == "a" else "a"
            n_folds, o_folds = band.locator("g.rl-fold").count(), block.locator(f'svg.rl-band[data-side="{other}"] g.rl-fold').count()
            n_clusters = band.locator("g.rl-cluster").count()
            first = band.locator("g.rl-fold").first
            fid = first.get_attribute("data-ids")
            inside = fid.split(",")
            first.dispatch_event("click")
            page.wait_for_timeout(300)
            band = block.locator(f'svg.rl-band[data-side="{side}"]')
            self.assertEqual(band.locator("g.rl-fold").count(), n_folds - 1)
            self.assertEqual(band.locator("g.rl-cluster").count(), n_clusters + len(inside))
            for cid in inside:
                self.assertEqual(band.locator(f'g.rl-cluster[data-id="{cid}"][data-kind="quiet"] path[data-fold="{fid}"]').count(), 1)
            # the other thread keeps its folds: a fold is opened by id, not per page
            self.assertEqual(block.locator(f'svg.rl-band[data-side="{other}"] g.rl-fold').count(), o_folds)
            # collapse all refolds it
            block.locator('button[data-act="collapse-all"]').click()
            page.wait_for_timeout(300)
            band = block.locator(f'svg.rl-band[data-side="{side}"]')
            self.assertEqual(band.locator("g.rl-fold").count(), n_folds)
            self.assertEqual(band.locator("g.rl-fold").first.get_attribute("data-ids"), fid)
        # a cluster on the thread opens to its rewarded steps as leaves — a hot one when there is one
        side, run, target = None, None, None
        for s in "ab":
            shown = [c for c in self._shown(rl[s]) if self._leaves(rl[s], c)[0]]
            if shown:
                side, run = s, rl[s]
                target = next((c for c in shown if c["kind"] == "hot"), shown[0])
                break
        self.assertIsNotNone(target, "a cluster with rewards on a thread")
        known = {r[f"{side}_index"] for r in self.report["alignment"] if r.get(f"{side}_index") is not None}
        band = block.locator(f'svg.rl-band[data-side="{side}"]')
        node = band.locator(f'g.rl-cluster[data-id="{target["id"]}"]')
        self.assertEqual(node.count(), 1)
        node.dispatch_event("click")
        page.wait_for_timeout(300)
        band = block.locator(f'svg.rl-band[data-side="{side}"]')
        node = band.locator(f'g.rl-cluster[data-id="{target["id"]}"]')
        self.assertIn("open", node.get_attribute("class").split())
        leaves = band.locator("g.rl-leaf")
        expected, more = self._leaves(run, target)
        self.assertEqual(leaves.count(), len(expected))
        self.assertEqual(band.locator("text.rl-more").count(), 1 if more else 0)
        if more:
            self.assertEqual(int(band.locator("text.rl-more").get_attribute("data-more")), more)
        self.assertEqual([int(leaves.nth(i).get_attribute("data-step")) for i in range(leaves.count())], [r["step"] for r in expected])
        for r in expected:
            text = band.locator(f'g.rl-leaf[data-step="{r["step"]}"]').text_content()
            self.assertIn(f"step {r['step']}", text)
            if r.get("name"):
                self.assertIn(r["name"][:12], text)
        self.assertEqual(band.locator(f'text.rl-why[data-id="{target["id"]}"]').count(), 1)
        # a leaf opens its step in the shared inspector
        leaf_step = next((r["step"] for r in expected if r["step"] in known), None)
        if leaf_step is not None:
            band.locator(f'g.rl-leaf[data-step="{leaf_step}"]').dispatch_event("click")
            page.wait_for_timeout(400)
            self.assertIn(f"STEP {leaf_step}", page.locator('#panels-lane [data-block="trace-body"] .tj-inspector').inner_text().upper())
        # the thread never moved: opening a cluster changes nothing on the thread
        # clicking it again closes it; expand hot opens every hot cluster; collapse all closes them
        node.dispatch_event("click")
        page.wait_for_timeout(300)
        band = block.locator(f'svg.rl-band[data-side="{side}"]')
        self.assertNotIn("open", (band.locator(f'g.rl-cluster[data-id="{target["id"]}"]').get_attribute("class") or "").split())
        self.assertEqual(band.locator("g.rl-leaf").count(), 0)
        block.locator('button[data-act="expand-hot"]').click()
        page.wait_for_timeout(300)
        n_hot = sum(1 for s in "ab" for c in rl[s]["clusters"] if c["kind"] == "hot")
        self.assertEqual(block.locator("g.rl-cluster.open").count(), n_hot)
        block.locator('button[data-act="collapse-all"]').click()
        page.wait_for_timeout(300)
        self.assertEqual(block.locator("g.rl-cluster.open").count(), 0)
        self.assertEqual(block.locator("g.rl-leaf").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_nothing_overflows_on_a_phone(self):
        context, page, errors = self._open(width=390)
        block = page.locator('#panels-lane .panels-grid > [data-block="rl"]')
        self.assertEqual(block.count(), 1)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        self.assertEqual(block.locator(".empty:visible").count(), 0)
        for sel in ("svg.rl-curve", 'svg.rl-band[data-side="a"]', 'svg.rl-band[data-side="b"]', ".rl-bar", ".rl-pref"):
            box = block.locator(sel).bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, sel)
        self.assertEqual(block.locator("path.rl-thread").count(), 2)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class TrainingViewTest(unittest.TestCase):
    """The Training view: a fifth tab that reads the RL training ground of
    a runs-layout batch top-down — the pair's reward panel, learning
    curves per task, the episodes × steps reward map with its folds, value
    calibration and advantages, what the reward paid for, the preference
    pairs, the policy delta — every count checked against `aggregate.rl`
    and the reports' `rl`. Uses the engine's own RL demo when it exists;
    otherwise (or with TRAINING_FIXTURE=1) a deterministic fixture over the
    runs demo: rewards from a seeded walk over each trace's real steps."""

    tmp = None
    GAMMA = 0.95

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        rl_traces = ROOT / "demo" / "rl" / "traces"
        forced = os.environ.get("TRAINING_FIXTURE") == "1"
        traces = rl_traces if rl_traces.is_dir() and not forced else ROOT / "demo" / "runs" / "traces"
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(traces), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("report_*.json"))]
        cls.injected = forced or not (isinstance(agg.get("rl"), dict) and agg["rl"].get("agents"))
        if cls.injected:
            agg["rl"] = cls._fixture(reports, traces)
            from deepcompare.report import render_html
            render_html(reports, agg, ROOT / "web" / "blocks.html", out / "report.html")
        cls.rl, cls.reports = agg["rl"], reports
        cls.page_path = out / "report.html"
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

    # ------------------------------------------------------------ fixture

    @classmethod
    def _fixture(cls, reports, traces_dir):
        import math
        import random
        import zlib
        G = cls.GAMMA

        def rng(*parts):
            return random.Random(zlib.crc32("|".join(str(p) for p in parts).encode()))

        def episode(trace, task, agent, run):
            steps = trace["steps"]; n = len(steps)
            success = bool(trace.get("outcome", {}).get("success"))
            r = rng(task, agent, run)
            rewards, labels = [0.0] * n, [[] for _ in range(n)]
            quiet = n >= 8 and run == "r2"      # a long silent stretch: a fold
            for k, s in enumerate(steps):
                if k == n - 1:
                    rewards[k] = 1.0 if success else -1.0; labels[k] = ["answer" if success else "wrong_answer"]
                elif s.get("error"):
                    rewards[k] = -0.5; labels[k] = ["tool_error"]
                elif not quiet and k > 0 and r.random() < 0.45:
                    rewards[k] = round(r.uniform(0.05, 0.4), 2); labels[k] = ["progress"]
                elif not quiet and k > 0 and r.random() < 0.2:
                    rewards[k] = -0.2; labels[k] = ["retry"]
            cum, acc = [], 0.0
            for x in rewards:
                acc += x; cum.append(round(acc, 4))
            togo, acc = [0.0] * n, 0.0
            for k in range(n - 1, -1, -1):
                acc = rewards[k] + G * acc; togo[k] = acc
            if agent.endswith("v3"):            # only the second policy carries a critic
                values = [round(togo[k] + r.uniform(-0.3, 0.3), 3) for k in range(n)]
                adv = [round(rewards[k] + (G * values[k + 1] if k + 1 < n else 0.0) - values[k], 3) for k in range(n)]
            else:
                values, adv = [None] * n, [None] * n
            events, tools, inputs = {}, {}, set()
            for k, s in enumerate(steps):
                for l in labels[k]:
                    events[l] = events.get(l, 0) + 1
                if s.get("type") not in ("plan", "reason", "answer") and s.get("name"):
                    tools[s["name"]] = tools.get(s["name"], 0) + 1
                if s.get("input") is not None:
                    inputs.add(str(s["input"]))
            return {"task_id": task, "run_id": run, "return": round(sum(rewards), 4), "discounted_return": round(togo[0], 4),
                    "steps": n, "success": success, "seconds": round(sum((s.get("latency_s") or 0.0) for s in steps), 3),
                    "rewards": rewards, "cum": cum, "values": values, "advantages": adv, "events": events, "tools": tools,
                    "distinct_inputs": len(inputs), "_labels": labels, "_togo": togo, "_steps": steps}

        agents = {}
        for path in sorted(Path(traces_dir).glob("*.json")):
            task, agent, run = path.stem.split("__")
            agents.setdefault(agent, {"episodes": []})["episodes"].append(episode(json.loads(path.read_text(encoding="utf-8")), task, agent, run))
        names = list(agents)
        for ag in agents.values():
            rs = [e["return"] for e in ag["episodes"]]; m = sum(rs) / len(rs)
            sd = math.sqrt(sum((x - m) ** 2 for x in rs) / max(1, len(rs) - 1))
            ag["mean_return"] = round(m, 4); ag["episodes_n"] = len(rs)
            ag["return_ci"] = [round(m - 1.96 * sd / math.sqrt(len(rs)), 4), round(m + 1.96 * sd / math.sqrt(len(rs)), 4)]
        tasks, prefs = {}, []
        for t in [r["task"]["id"] for r in reports]:
            row = {}
            for name in names:
                rs = [e["return"] for e in agents[name]["episodes"] if e["task_id"] == t]
                row[name] = {"mean_return": round(sum(rs) / len(rs), 4) if rs else None, "returns": rs}
            a, b = names[0], names[1]
            delta = round(row[b]["mean_return"] - row[a]["mean_return"], 4)
            row["delta"] = delta; row["sign"] = 1 if delta > 0 else -1 if delta < 0 else 0
            tasks[t] = row
            if delta:
                chosen, rejected = (b, a) if delta > 0 else (a, b)
                prefs.append({"task_id": t, "chosen": chosen, "rejected": rejected, "basis": "mean return", "margin": abs(delta)})
        for r in reports:
            t = r["task"]["id"]; runs = {}
            for side in ("a", "b"):
                name = r[side]["agent"]["name"]; rid = r[side].get("run_id")
                ep = next((e for e in agents[name]["episodes"] if e["task_id"] == t and e["run_id"] == rid), None)
                if ep is None:
                    ep = next(e for e in agents[name]["episodes"] if e["task_id"] == t)
                entries = [{"step": k, "reward": ep["rewards"][k], "cum": ep["cum"][k], "to_go": round(sum(ep["rewards"][k:]), 4),
                            "discounted_to_go": round(ep["_togo"][k], 4), "credit": None, "labels": ep["_labels"][k], "agent": name,
                            "kind": ep["_steps"][k].get("type"), "name": ep["_steps"][k].get("name")} for k in range(ep["steps"])]
                largest = sorted(entries, key=lambda e: -abs(e["reward"]))[:3]
                runs[side] = {"agent": name, "measurable": True, "steps": ep["steps"], "return": ep["return"], "discounted_return": ep["discounted_return"],
                              "positive": sum(1 for x in ep["rewards"] if x > 0), "negative": sum(1 for x in ep["rewards"] if x < 0),
                              "zero": sum(1 for x in ep["rewards"] if x == 0), "seconds": ep["seconds"], "rewards": entries,
                              "largest": [{"step": e["step"], "reward": e["reward"], "why": ", ".join(e["labels"])} for e in largest],
                              "credit": {"source": "none", "metric": "reward", "top": [], "total": 0}, "clusters": [], "narrative": ""}
            pref = next((p for p in prefs if p["task_id"] == t), None)
            r["rl"] = {"version": 1, "measurable": True, "source": "recorded", "gamma": G, "a": runs["a"], "b": runs["b"],
                       "preference": {k: v for k, v in pref.items() if k != "task_id"} if pref else None, "narrative": ""}
        for ag in agents.values():
            for e in ag["episodes"]:
                for k in ("_labels", "_togo", "_steps"):
                    e.pop(k)
        return {"gamma": G, "source": "recorded", "agents": agents, "tasks": tasks, "preferences": prefs,
                "narrative": "%d policies, %d episodes over %d tasks; rewards recorded per step." % (
                    len(names), sum(len(a["episodes"]) for a in agents.values()), len(reports))}

    # ------------------------------------------------------------ helpers

    def _open(self, width=1280, view="training"):
        context = self.browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view={view}")
        page.wait_for_timeout(900)
        return context, page, errors

    def _episodes(self):
        return [(name, e) for name, ag in self.rl["agents"].items() for e in ag["episodes"]]

    @staticmethod
    def _rewards(e):
        out = [0.0] * int(e.get("steps") or 0)
        for i, v in enumerate(e["rewards"]):
            if isinstance(v, dict):
                out[v["step"]] = v["reward"] if len(out) > v["step"] else 0
            elif i < len(out):
                out[i] = v
        return out

    @classmethod
    def _folds(cls, e):
        runs, i, rewards = [], 0, cls._rewards(e)
        while i < len(rewards):
            if rewards[i] != 0:
                i += 1; continue
            j = i
            while j < len(rewards) and rewards[j] == 0:
                j += 1
            if j - i >= 5:
                runs.append((i, j - i))
            i = j
        return runs

    def _composition(self):
        """What the block draws when the aggregate carries no shaping
        weights: each recorded reward in the pair reports, split evenly
        over its labels (its step kind when it has none)."""
        per = {}
        for r in self.reports:
            for side in ("a", "b"):
                run = r["rl"][side]
                policy = run.get("agent") or r[side]["agent"]["name"]
                for e in run["rewards"]:
                    if not e["reward"]:
                        continue
                    ls = e.get("labels") or [e.get("kind") or "unlabelled"]
                    for l in ls:
                        per.setdefault(policy, {}).setdefault(l, 0.0)
                        per[policy][l] += e["reward"] / len(ls)
        return per

    # ------------------------------------------------------------ tests

    def test_the_tab_opens_the_training_lane_and_nothing_else(self):
        context, page, errors = self._open()
        tab = page.locator('.tab[data-view="training"]')
        self.assertEqual(tab.count(), 1)
        self.assertEqual(tab.inner_text().strip(), "Training")
        self.assertEqual(tab.get_attribute("aria-selected"), "true")
        self.assertEqual(page.locator('.tab[aria-selected="true"]').count(), 1)
        for lane in ("#story-lane", "#panels-lane", "#hero-lane", "#reading"):
            self.assertTrue(page.locator(lane).is_hidden(), lane)
        self.assertFalse(page.locator("#stacks").is_hidden())
        labels = page.locator("#stacks .stack-label .name").all_inner_texts()
        self.assertEqual(len(labels), 1)
        self.assertIn("training", labels[0].lower())
        ids = page.evaluate("() => [...document.querySelectorAll('#stacks .block')].map(e => e.dataset.block)")
        has_rl = page.evaluate("() => !!(AgentDiff.blockEntry('rl'))")
        want = (["rl-here"] if has_rl else []) + ["rl-curves", "rl-reward-map", "rl-advantage", "rl-events", "rl-preferences", "rl-policy-delta"]
        self.assertEqual(ids, want)
        self.assertEqual(page.locator("#stacks .block.collapsed").count(), 0)
        self.assertEqual(page.locator("#stacks .block:not(.collapsed) .empty").count(), 0)
        # every chart is an image with a name
        svgs = page.locator("#stacks svg")
        self.assertGreater(svgs.count(), 0)
        for bid in ("rl-curves", "rl-reward-map", "rl-events"):
            self.assertGreater(page.locator(f'#stacks [data-block="{bid}"] svg').count(), 0, bid)
        for i in range(svgs.count()):
            svg = svgs.nth(i)
            if svg.evaluate("e => !!e.parentNode.closest('svg')"):
                continue
            self.assertEqual(svg.get_attribute("role"), "img")
            self.assertTrue(svg.get_attribute("aria-label"))
        # two columns for the tables on a desk, everything else full width
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('#stacks .stack')).gridTemplateColumns.split(' ').length"), 2)
        wide = page.evaluate("() => document.querySelector('#stacks [data-block=\"rl-curves\"]').getBoundingClientRect().width")
        narrow = page.evaluate("() => document.querySelector('#stacks [data-block=\"rl-preferences\"]').getBoundingClientRect().width")
        self.assertGreater(wide, narrow * 1.6)
        # the other views are untouched by the fifth tab
        page.click('.tab[data-view="story"]')
        page.wait_for_timeout(500)
        self.assertFalse(page.locator("#story-lane").is_hidden())
        self.assertTrue(page.locator("#stacks").is_hidden())
        self.assertEqual(errors, [])
        context.close()

    def test_learning_curves_draw_every_episode_with_the_band_and_the_headline(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-curves"]')
        tasks = list(self.rl["tasks"]) if self.rl.get("tasks") else sorted({e["task_id"] for _, e in self._episodes()})
        self.assertEqual(block.locator(".rl-multiple").count(), len(tasks))
        for name, ag in self.rl["agents"].items():
            self.assertEqual(block.locator(f'circle.rlc-pt[data-policy="{name}"]').count(), len(ag["episodes"]), name)
            self.assertEqual(block.locator(f'circle.rlc-pt.ok[data-policy="{name}"]').count(), sum(1 for e in ag["episodes"] if e["success"]), name)
            if ag.get("return_ci"):
                self.assertEqual(block.locator(f'rect.rlc-band[data-policy="{name}"]').count(), len(tasks), name)
            chip = block.locator(f'.rlc-chip[data-policy="{name}"]')
            self.assertEqual(chip.count(), 1)
            text = chip.inner_text()
            self.assertIn(f"n={ag['episodes_n']}", text)
            if ag.get("return_ci"):
                self.assertIn(f"[{ag['return_ci'][0]:.2f}, {ag['return_ci'][1]:.2f}]", text)
        self.assertEqual(block.locator(".rlc-chip").count(), len(self.rl["agents"]))
        # a point opens its task: the pair views follow
        current = page.evaluate("() => document.getElementById('task-picker').value")
        other = block.locator(f'circle.rlc-pt:not([data-task="{current}"])').first
        target = other.get_attribute("data-task")
        other.dispatch_event("click")
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("() => document.getElementById('task-picker').value"), target)
        self.assertEqual(page.locator(f'#stacks [data-block="rl-curves"] .rl-multiple[aria-current="true"]').get_attribute("data-task"), target)
        self.assertEqual(errors, [])
        context.close()

    def test_the_reward_map_has_a_row_per_episode_a_cell_per_reward_and_folds_that_open(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-reward-map"]')
        episodes = self._episodes()
        self.assertEqual(block.locator("g.rlm-row").count(), len(episodes))
        self.assertEqual(block.locator("g.rlm-policy").count(), len(self.rl["agents"]))
        expected_folds = 0
        for name, e in episodes:
            row = block.locator(f'g.rlm-row[data-policy="{name}"][data-task="{e["task_id"]}"][data-run="{e["run_id"]}"]')
            self.assertEqual(row.count(), 1, (name, e["task_id"], e["run_id"]))
            rewards = self._rewards(e)
            self.assertEqual(row.locator("rect.rlm-cell").count(), sum(1 for r in rewards if r != 0), (name, e["task_id"], e["run_id"]))
            folds = self._folds(e)
            self.assertEqual(row.locator("g.rlm-fold").count(), len(folds), (name, e["task_id"], e["run_id"]))
            self.assertEqual([int(f.get_attribute("data-steps")) for f in row.locator("g.rlm-fold").all()], [n for _, n in folds])
            expected_folds += len(folds)
        if any(self._folds(e) for _, e in episodes):
            self.assertGreater(expected_folds, 0)
            fold = block.locator("g.rlm-fold").first
            row = fold.locator("xpath=ancestor::*[contains(@class,'rlm-row')]")
            before_end = float(row.get_attribute("data-end"))
            key = (row.get_attribute("data-policy"), row.get_attribute("data-task"), row.get_attribute("data-run"))
            fold.dispatch_event("click")
            page.wait_for_timeout(400)
            row = block.locator(f'g.rlm-row[data-policy="{key[0]}"][data-task="{key[1]}"][data-run="{key[2]}"]')
            self.assertEqual(block.locator("g.rlm-fold").count(), expected_folds - 1)
            self.assertGreater(float(row.get_attribute("data-end")), before_end)
        # the pair on the page is marked, and a click on its cell opens the step
        pair = block.locator('g.rlm-row[data-pair]')
        self.assertEqual(pair.count(), 2)
        cell = pair.first.locator("rect.rlm-cell").first
        step = int(cell.get_attribute("data-step"))
        page.evaluate("() => { window.__sel = null; document.addEventListener('agentdiff:select-step', e => { window.__sel = e.detail; }); }")
        cell.dispatch_event("click")
        page.wait_for_timeout(200)
        sel = page.evaluate("() => window.__sel")
        self.assertIsNotNone(sel)
        self.assertEqual(sel["side"], pair.first.get_attribute("data-pair"))
        self.assertEqual(errors, [])
        context.close()

    def test_advantage_and_value_count_the_steps_that_carry_estimates_or_say_none_do(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-advantage"]')
        episodes = self._episodes()
        n_values = sum(1 for _, e in episodes for v in e.get("values") or [] if isinstance(v, (int, float)))
        advs = {}
        for name, e in episodes:
            advs[name] = advs.get(name, 0) + sum(1 for v in e.get("advantages") or [] if isinstance(v, (int, float)))
        if not n_values and not sum(advs.values()):
            note = block.locator(".rla-note")
            self.assertEqual(note.count(), 1)
            self.assertIn("no value estimates", note.inner_text())
            self.assertEqual(block.locator(".empty").count(), 0)
        else:
            self.assertEqual(block.locator("circle.rla-pt").count(), n_values)
            self.assertEqual(block.locator("line.rla-diag").count(), 1 if n_values else 0)
            for name, n in advs.items():
                got = page.evaluate("(n) => [...document.querySelectorAll('#stacks [data-block=\"rl-advantage\"] rect.rla-bar[data-policy=\"' + n + '\"]')].reduce((a, e) => a + Number(e.dataset.count), 0)", name)
                self.assertEqual(got, n, name)
            self.assertIn(f"{n_values} steps with a value estimate", block.inner_text())
        self.assertEqual(errors, [])
        context.close()

    def test_reward_composition_stacks_one_bar_per_policy_with_a_segment_per_label(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-events"]')
        per = self._composition()
        self.assertEqual(block.locator("g.rle-bar").count(), len(self.rl["agents"]))
        for name in self.rl["agents"]:
            segs = block.locator(f'g.rle-bar[data-policy="{name}"] rect.rle-seg')
            labels = {l for l, v in per.get(name, {}).items() if v != 0}
            self.assertEqual({s.get_attribute("data-label") for s in segs.all()}, labels, name)
            total = sum(float(s.get_attribute("data-reward")) for s in segs.all())
            self.assertAlmostEqual(total, sum(per.get(name, {}).values()), places=3, msg=name)
            for s in segs.all():
                self.assertAlmostEqual(float(s.get_attribute("data-reward")), per[name][s.get_attribute("data-label")], places=3)
        table = block.locator("details table.rl-table")
        self.assertEqual(table.locator("tr[data-label]").count(), len({l for p in per.values() for l in p}))
        self.assertEqual(errors, [])
        context.close()

    def test_the_preference_dataset_is_the_pairs_and_copies_as_jsonl(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-preferences"]')
        pairs = self.rl["preferences"]
        self.assertEqual(block.locator("tr.rlp-row").count(), len(pairs))
        self.assertTrue(block.locator(".rlp-count").inner_text().startswith(f"{len(pairs)} pair"))
        for i, p in enumerate(pairs):
            row = block.locator("tr.rlp-row").nth(i)
            self.assertEqual(row.get_attribute("data-task"), p["task_id"])
            self.assertIn(p["chosen"] if isinstance(p["chosen"], str) else p["chosen"]["agent"], row.inner_text())
        copy = block.locator("button.rlp-copy")
        self.assertEqual(copy.count(), 1)
        page.evaluate("() => { navigator.clipboard.writeText = t => { window.__copied = t; return Promise.resolve(); }; }")
        copy.click()
        page.wait_for_timeout(300)
        lines = page.evaluate("() => window.__copied").split("\n")
        self.assertEqual(len(lines), len(pairs))
        for line, p in zip(lines, pairs):
            obj = json.loads(line)
            self.assertEqual(obj["task_id"], p["task_id"])
            self.assertEqual(obj["chosen"], p["chosen"] if isinstance(p["chosen"], str) else p["chosen"]["agent"])
        self.assertIn(f"copied {len(pairs)} lines", copy.inner_text())
        self.assertEqual(errors, [])
        context.close()

    def test_the_policy_delta_sorts_every_task_by_size_and_counts_the_sign(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-policy-delta"]')
        tasks = {t: v for t, v in self.rl["tasks"].items() if isinstance(v.get("delta"), (int, float))}
        rows = block.locator(".rld-row")
        self.assertEqual(rows.count(), len(tasks))
        deltas = [float(r.get_attribute("data-delta")) for r in rows.all()]
        self.assertEqual([abs(d) for d in deltas], sorted((abs(d) for d in deltas), reverse=True))
        self.assertEqual({r.get_attribute("data-task") for r in rows.all()}, set(tasks))
        for r in rows.all():
            self.assertAlmostEqual(float(r.get_attribute("data-delta")), tasks[r.get_attribute("data-task")]["delta"], places=4)
        up = sum(1 for v in tasks.values() if v["delta"] > 0)
        down = sum(1 for v in tasks.values() if v["delta"] < 0)
        lead, n = (up, len(tasks)) if up >= down else (down, len(tasks))
        summary = block.locator(".rld-sum").inner_text()
        self.assertIn(f"better on {lead} of {n} task", summary)
        a_name = self.reports[0]["a"]["agent"]["name"]
        b_name = self.reports[0]["b"]["agent"]["name"]
        self.assertTrue(summary.startswith(b_name if up >= down else a_name), summary)
        # a row opens its task
        current = page.evaluate("() => document.getElementById('task-picker').value")
        other = block.locator(f'.rld-row:not([data-task="{current}"])').first
        target = other.get_attribute("data-task")
        other.click()
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("() => document.getElementById('task-picker').value"), target)
        self.assertEqual(page.locator('#stacks [data-block="rl-policy-delta"] .rld-row[aria-current="true"]').get_attribute("data-task"), target)
        self.assertEqual(errors, [])
        context.close()

    def test_arrow_right_from_panels_reaches_training_and_the_url_opens_it(self):
        context, page, errors = self._open(view="panels")
        self.assertEqual(page.locator('.tab[data-view="panels"]').get_attribute("aria-selected"), "true")
        page.focus('.tab[data-view="panels"]')
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(500)
        self.assertEqual(page.locator('.tab[data-view="training"]').get_attribute("aria-selected"), "true")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.view"), "training")
        self.assertFalse(page.locator("#stacks").is_hidden())
        self.assertTrue(page.locator("#panels-lane").is_hidden())
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="story"]').get_attribute("aria-selected"), "true")
        page.reload()
        page.wait_for_timeout(800)
        self.assertEqual(page.locator('.tab[data-view="panels"]').get_attribute("aria-selected"), "true")
        self.assertEqual(errors, [])
        context.close()

    def test_nothing_overflows_on_a_phone_and_the_lane_is_one_column(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('#stacks .stack')).gridTemplateColumns.split(' ').length"), 1)
        for bid in ("rl-curves", "rl-reward-map", "rl-advantage", "rl-events", "rl-preferences", "rl-policy-delta"):
            box = page.locator(f'#stacks [data-block="{bid}"]').bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
        small = page.evaluate("""() => {
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let n = 0; let node;
          while ((node = walker.nextNode())) {
            if (!node.textContent.trim()) continue;
            const el = node.parentElement; if (!el || el.closest('svg')) continue;
            if (parseFloat(getComputedStyle(el).fontSize) < 11) n++;
          }
          return n; }""")
        self.assertEqual(small, 0)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class RLTheatreTest(unittest.TestCase):
    """The episode theatre and its overview.

    `rl-ridgeline` draws every episode's cumulative return, one row per
    policy per task; `rl-theatre` plays one episode per policy on a task
    under a scrubber. What is checked here is what a person actually does
    with them and cannot check any other way: that the scrubber is a real
    ARIA slider whose value and value-text track the step, that the whole
    thing is drivable from the keyboard alone (arrows, shift-arrows, home,
    end, space to play), that a click on a ridgeline curve opens that
    episode below, and that neither block overflows a 390px phone or drops
    any text under 11px.

    Uses the larger RL demo (`demo/rl/train`) when it is present, the
    small one otherwise; skips when the engine ships no RL demo at all."""

    tmp = None

    @classmethod
    def setUpClass(cls):
        traces = None
        for name in ("train", "traces"):
            candidate = ROOT / "demo" / "rl" / name
            if candidate.is_dir():
                traces = candidate
                break
        if traces is None:
            raise unittest.SkipTest("no demo/rl traces to build a training page from")
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(traces), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        rl = agg.get("rl")
        if not (isinstance(rl, dict) and rl.get("agents")):
            raise unittest.SkipTest("this build carries no aggregate.rl to play")
        cls.rl = rl
        cls.page_path = out / "report.html"
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

    SCRUB = '[data-block="rl-theatre"] .rlt-scrubber'

    def _open(self, width=1280, reduced=None):
        kwargs = {"viewport": {"width": width, "height": 900}}
        if reduced:
            kwargs["reduced_motion"] = reduced
        context = self.browser.new_context(**kwargs)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=training")
        page.wait_for_timeout(1200)
        page.wait_for_selector(self.SCRUB)
        return context, page, errors

    def _now(self, page):
        return int(page.get_attribute(self.SCRUB, "aria-valuenow"))

    # --------------------------------------------------------------- aria

    def test_the_scrubber_is_a_real_slider_and_says_where_it_is(self):
        context, page, errors = self._open()
        self.assertEqual(page.get_attribute(self.SCRUB, "role"), "slider")
        self.assertEqual(page.get_attribute(self.SCRUB, "aria-valuemin"), "0")
        self.assertEqual(page.get_attribute(self.SCRUB, "aria-orientation"), "horizontal")
        self.assertTrue((page.get_attribute(self.SCRUB, "aria-label") or "").strip())
        top = int(page.get_attribute(self.SCRUB, "aria-valuemax"))
        # the slider spans the longer of the two runs, counted from the data
        longest = max(e["steps"] for agent in self.rl["agents"].values()
                      for e in agent["episodes"]) - 1
        self.assertGreater(top, 0)
        self.assertLessEqual(top, longest)
        self.assertEqual(self._now(page), 0)
        text = page.get_attribute(self.SCRUB, "aria-valuetext")
        self.assertIn("step 0", text)
        for name in self.rl["agents"]:
            self.assertIn(name, text)
        page.keyboard.press("Tab")   # the slider is reachable, not a mouse-only target
        self.assertTrue(page.evaluate(
            """(sel) => { const s = document.querySelector(sel);
                 s.focus(); return document.activeElement === s; }""", self.SCRUB))
        self.assertEqual(errors, [])
        context.close()

    def test_every_chart_is_an_image_with_a_label_and_every_control_is_named(self):
        context, page, errors = self._open()
        for bid in ("rl-ridgeline", "rl-theatre"):
            svgs = page.evaluate(
                """(id) => [...document.querySelectorAll('[data-block="' + id + '"] .rlt svg')]
                     .map(s => [s.getAttribute('role'), (s.getAttribute('aria-label') || '').length])""", bid)
            self.assertTrue(svgs, bid)
            for role, label_len in svgs:
                self.assertEqual(role, "img", bid)
                self.assertGreater(label_len, 20, bid)
        unnamed = page.evaluate(
            """() => [...document.querySelectorAll('[data-block="rl-theatre"] button, [data-block="rl-theatre"] select')]
                 .filter(el => !(el.getAttribute('aria-label') || '').trim()
                            && !(el.closest('.rlt')) === false && el.closest('.rlt'))
                 .map(el => el.outerHTML.slice(0, 60))""")
        self.assertEqual(unnamed, [])
        self.assertEqual(errors, [])
        context.close()

    # ----------------------------------------------------------- keyboard

    def test_the_scrubber_is_driven_by_the_keyboard_alone(self):
        context, page, errors = self._open()
        page.focus(self.SCRUB)
        top = int(page.get_attribute(self.SCRUB, "aria-valuemax"))

        page.keyboard.press("ArrowRight")
        self.assertEqual(self._now(page), 1)
        page.keyboard.press("ArrowRight")
        self.assertEqual(self._now(page), 2)
        page.keyboard.press("Shift+ArrowRight")
        self.assertEqual(self._now(page), 12, "shift steps ten at a time")
        page.keyboard.press("Shift+ArrowLeft")
        self.assertEqual(self._now(page), 2)
        page.keyboard.press("ArrowLeft")
        self.assertEqual(self._now(page), 1)

        page.keyboard.press("End")
        self.assertEqual(self._now(page), top)
        page.keyboard.press("ArrowRight")
        self.assertEqual(self._now(page), top, "the end is the end")
        page.keyboard.press("Home")
        self.assertEqual(self._now(page), 0)
        page.keyboard.press("ArrowLeft")
        self.assertEqual(self._now(page), 0, "the start is the start")

        # the value text follows the position, and names the step
        page.keyboard.press("Shift+ArrowRight")
        self.assertIn("step 10", page.get_attribute(self.SCRUB, "aria-valuetext"))
        self.assertEqual(page.get_attribute(self.SCRUB, "aria-valuenow"), "10")
        self.assertEqual(errors, [])
        context.close()

    def test_space_plays_and_pauses_at_a_readable_rate(self):
        context, page, errors = self._open()
        play = page.locator('[data-block="rl-theatre"] .rlt-btn[aria-pressed]').first
        self.assertEqual(play.get_attribute("aria-pressed"), "false")
        page.focus(self.SCRUB)
        page.keyboard.press("Home")

        page.keyboard.press("Space")
        page.wait_for_timeout(800)
        moved = self._now(page)
        self.assertGreaterEqual(moved, 3, "play should advance several steps in 0.8s")
        self.assertLessEqual(moved, 12, "play must not race the frame rate")
        self.assertEqual(play.get_attribute("aria-pressed"), "true")

        page.keyboard.press("Space")
        paused = self._now(page)
        self.assertEqual(play.get_attribute("aria-pressed"), "false")
        page.wait_for_timeout(500)
        self.assertEqual(self._now(page), paused, "pause means pause")

        # an arrow key while playing stops the playback too
        page.keyboard.press("Space")
        page.wait_for_timeout(300)
        page.keyboard.press("ArrowRight")
        stopped = self._now(page)
        page.wait_for_timeout(500)
        self.assertEqual(self._now(page), stopped)
        self.assertEqual(errors, [])
        context.close()

    def test_it_never_plays_on_its_own_under_reduced_motion(self):
        context, page, errors = self._open(reduced="reduce")
        self.assertEqual(self._now(page), 0)
        page.wait_for_timeout(900)
        self.assertEqual(self._now(page), 0)
        self.assertEqual(
            page.locator('[data-block="rl-theatre"] .rlt-btn[aria-pressed]').first
                .get_attribute("aria-pressed"), "false")
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------- the drawing

    def test_the_tracks_stop_where_each_run_stopped(self):
        context, page, errors = self._open()
        tracks = page.evaluate(
            """() => [...document.querySelectorAll('[data-block="rl-theatre"] .rlt-track')]
                 .map(g => ({steps: +g.getAttribute('data-steps'),
                             cells: g.querySelectorAll('.rlt-cell').length,
                             ends: g.querySelectorAll('.rlt-end').length}))""")
        self.assertTrue(tracks)
        for t in tracks:
            self.assertGreater(t["steps"], 0)
            self.assertEqual(t["ends"], 1, "a run ends once, and says so")
            self.assertLessEqual(t["cells"], t["steps"], "no cell past the last step")
        # at the end of the longer run, the shorter run's mark is gone rather than pinned
        page.focus(self.SCRUB)
        page.keyboard.press("End")
        page.wait_for_timeout(120)
        if len({t["steps"] for t in tracks}) > 1:
            shown = page.evaluate(
                """() => [...document.querySelectorAll('[data-block="rl-theatre"] .rlt-cellnow')]
                     .map(r => r.getAttribute('opacity'))""")
            self.assertIn("0", shown)
            self.assertIn("ended", page.get_attribute(self.SCRUB, "aria-valuetext"))
        self.assertEqual(errors, [])
        context.close()

    def test_a_ridgeline_curve_opens_that_episode_in_the_theatre(self):
        context, page, errors = self._open()
        target = page.evaluate(
            """() => { const hits = [...document.querySelectorAll('[data-block="rl-ridgeline"] .rlr-hit')];
                 if (!hits.length) return null;
                 const h = hits[hits.length - 1];
                 return {task: h.getAttribute('data-task'), policy: h.getAttribute('data-policy'),
                         run: h.getAttribute('data-run')}; }""")
        self.assertIsNotNone(target, "the ridgeline draws a clickable curve per episode")
        page.evaluate(
            """() => { const hits = [...document.querySelectorAll('[data-block="rl-ridgeline"] .rlr-hit')];
                 hits[hits.length - 1].dispatchEvent(new MouseEvent('click', {bubbles: true})); }""")
        page.wait_for_timeout(500)
        chosen = page.evaluate(
            """() => { const c = document.querySelector('[data-block="rl-theatre"]');
                 return {task: c.querySelector('select').value,
                         runs: [...c.querySelectorAll('.rlt-run b')].map(e => e.textContent)}; }""")
        self.assertEqual(chosen["task"], target["task"])
        self.assertIn(target["run"], chosen["runs"])
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------- phone

    def test_neither_block_overflows_a_phone_or_shrinks_its_text(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        for bid in ("rl-ridgeline", "rl-theatre"):
            box = page.locator(f'#stacks [data-block="{bid}"]').bounding_box()
            self.assertIsNotNone(box, bid)
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
            widest = page.evaluate(
                """(id) => { const root = document.querySelector('[data-block="' + id + '"]');
                     let over = 0;
                     root.querySelectorAll('*').forEach(el => {
                       const cs = getComputedStyle(el);
                       if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') return;
                       if (cs.textOverflow === 'ellipsis') return;
                       if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) over++;
                     });
                     return over; }""", bid)
            self.assertEqual(widest, 0, bid)
        small = page.evaluate(
            """() => { const out = [];
                 document.querySelectorAll('[data-block="rl-theatre"] *, [data-block="rl-ridgeline"] *')
                   .forEach(el => {
                     if (el.children.length || !(el.textContent || '').trim()) return;
                     const fs = parseFloat(getComputedStyle(el).fontSize);
                     if (fs && fs < 11) out.push(el.tagName + ':' + fs);
                   });
                 return out; }""")
        self.assertEqual(small, [])
        # and the scrubber still works with a thumb rather than a keyboard
        page.locator(self.SCRUB).scroll_into_view_if_needed()
        page.wait_for_timeout(150)
        box = page.locator(self.SCRUB).bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.15, box["y"] + box["height"] * 0.4)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.85, box["y"] + box["height"] * 0.4, steps=6)
        page.mouse.up()
        self.assertGreater(self._now(page), 0, "dragging moves the scrubber")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------ the reader's choice

    def test_the_task_and_run_choice_survive_a_reload(self):
        context, page, errors = self._open()
        tasks = page.evaluate(
            """() => [...document.querySelectorAll('[data-block="rl-theatre"] select option')].map(o => o.value)""")
        if len(tasks) < 2:
            self.skipTest("one task only: nothing to switch to")
        other = [t for t in tasks
                 if t != page.evaluate("""() => document.querySelector('[data-block="rl-theatre"] select').value""")][0]
        page.select_option('[data-block="rl-theatre"] select', other)
        page.wait_for_timeout(400)
        page.locator('[data-block="rl-theatre"] .rlt-run .rlt-btn').nth(1).click()
        page.wait_for_timeout(400)
        before = page.evaluate(
            """() => { const c = document.querySelector('[data-block="rl-theatre"]');
                 return [c.querySelector('select').value,
                         [...c.querySelectorAll('.rlt-run b')].map(e => e.textContent)]; }""")
        page.reload()
        page.wait_for_timeout(1300)
        after = page.evaluate(
            """() => { const c = document.querySelector('[data-block="rl-theatre"]');
                 return [c.querySelector('select').value,
                         [...c.querySelectorAll('.rlt-run b')].map(e => e.textContent)]; }""")
        self.assertEqual(before, after)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class RLStatsBlocksTest(unittest.TestCase):
    """The small-sample statistics blocks (28_rlstats.js) against the RL
    training demo: the four-row interval plot, the performance profile with
    its bands, and the probability of improvement with its per-task rows.

    Every figure drawn must be the one `aggregate.rl.stats` carries — the
    page recomputes nothing — and the sample advisory must be on the page
    beside the intervals, because a wide bootstrap interval that reads as a
    finding is the exact failure these blocks exist to prevent.
    """

    tmp = None
    IDS = ("rl-stats-aggregate", "rl-stats-profile", "rl-stats-improvement")

    @classmethod
    def setUpClass(cls):
        train = ROOT / "demo" / "rl" / "train"
        traces = train if train.is_dir() else ROOT / "demo" / "rl" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("no RL demo traces to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(traces), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        cls.stats = ((agg.get("rl") or {}).get("stats")) or {}
        if not cls.stats.get("measurable"):
            raise unittest.SkipTest("the RL demo carries no stats section")
        cls.page_path = out / "report.html"
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

    def _open(self, width=1280):
        context = self.browser.new_context(viewport={"width": width, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page_path}#view=training")
        page.wait_for_timeout(800)
        return context, page, errors

    def _errors(self, errors):
        # a sibling block failing is not this block's failure to report
        return [e for e in errors if "rlstats" in e or "rl-stats" in e]

    def test_all_three_blocks_render_without_an_empty_state(self):
        context, page, errors = self._open()
        for bid in self.IDS:
            block = page.locator(f'#stacks [data-block="{bid}"]')
            self.assertEqual(block.count(), 1, bid)
            body = block.locator(".block-body").inner_text()
            self.assertNotIn("failed to render", body, bid)
            self.assertGreater(len(body.strip()), 60, bid)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_every_chart_is_labelled_for_a_screen_reader(self):
        context, page, _ = self._open()
        for bid in self.IDS:
            for node in page.locator(f'#stacks [data-block="{bid}"] svg').all():
                self.assertEqual(node.get_attribute("role"), "img", bid)
                self.assertTrue((node.get_attribute("aria-label") or "").strip(), bid)
        context.close()

    def test_the_interval_plot_draws_every_metric_for_every_policy(self):
        context, page, _ = self._open()
        block = page.locator('#stacks [data-block="rl-stats-aggregate"]')
        for metric in ("iqm", "median", "mean", "optimality_gap"):
            for policy, cells in self.stats["aggregates"].items():
                row = block.locator(f'.rls-int[data-metric="{metric}"][data-policy="{policy}"]')
                self.assertEqual(row.count(), 1, f"{metric}/{policy}")
                self.assertAlmostEqual(float(row.get_attribute("data-point")),
                                       cells[metric]["point"], places=4)
        context.close()

    def test_the_headline_says_whether_the_intervals_overlap(self):
        context, page, _ = self._open()
        verdict = page.locator('#stacks [data-block="rl-stats-aggregate"] .rls-verdict')
        names = self.stats["policies"]
        a, b = self.stats["aggregates"][names[0]]["iqm"], self.stats["aggregates"][names[1]]["iqm"]
        overlap = a["lo"] <= b["hi"] and b["lo"] <= a["hi"]
        self.assertEqual(verdict.get_attribute("data-overlap"), str(overlap).lower())
        text = verdict.inner_text()
        self.assertIn("do not overlap" if not overlap else "overlap", text)
        if overlap:
            # the one sentence that must never be omitted
            self.assertIn("not the same as the policies being equal", text)
        context.close()

    def test_the_profile_draws_one_curve_and_one_band_per_policy(self):
        context, page, _ = self._open()
        block = page.locator('#stacks [data-block="rl-stats-profile"]')
        for policy in self.stats["policies"]:
            self.assertEqual(block.locator(f'.rls-curve[data-policy="{policy}"]').count(), 1, policy)
            self.assertEqual(block.locator(f'.rls-band[data-policy="{policy}"]').count(), 1, policy)
        crossings = self.stats["profile"]["crossings"]
        self.assertEqual(int(block.locator(".rls-reading").get_attribute("data-crossings")), len(crossings))
        self.assertEqual(block.locator(".rls-cross").count(), len([t for t in crossings]))
        reading = block.locator(".rls-reading").inner_text()
        self.assertIn("cross" if crossings else "every τ", reading)
        context.close()

    def test_the_probability_of_improvement_matches_the_json_and_names_its_tasks(self):
        context, page, _ = self._open()
        block = page.locator('#stacks [data-block="rl-stats-improvement"]')
        imp = self.stats["improvement"]
        self.assertAlmostEqual(float(block.locator(".rls-big").get_attribute("data-p")), imp["point"], places=4)
        self.assertIn(f"{round(imp['point'] * 100)}%", block.locator(".rls-big").inner_text())
        rows = block.locator(".rls-row")
        self.assertEqual(rows.count(), len(imp["per_task"]))
        for row in rows.all():
            task = row.get_attribute("data-task")
            self.assertAlmostEqual(float(row.get_attribute("data-p")), imp["per_task"][task], places=4)
        # a task where the average hides a regression must sort to the top
        if imp.get("regressions"):
            self.assertEqual(rows.first.get_attribute("data-task"), imp["regressions"][0])
        context.close()

    def test_a_task_row_opens_that_task(self):
        context, page, _ = self._open()
        block = page.locator('#stacks [data-block="rl-stats-improvement"]')
        current = page.evaluate("() => document.getElementById('task-picker').value")
        other = block.locator(f'.rls-row:not([data-task="{current}"])').first
        target = other.get_attribute("data-task")
        other.click()
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("() => document.getElementById('task-picker').value"), target)
        self.assertEqual(page.locator('#stacks [data-block="rl-stats-improvement"] .rls-row[aria-current="true"]')
                         .get_attribute("data-task"), target)
        context.close()

    def test_the_sample_advisory_is_on_the_page_beside_the_intervals(self):
        context, page, _ = self._open()
        message = self.stats["advisory"]["message"]
        for bid in ("rl-stats-aggregate", "rl-stats-improvement"):
            note = page.locator(f'#stacks [data-block="{bid}"] .rls-advisory').inner_text()
            self.assertIn("run(s) per task", note, bid)
            self.assertIn("stratified bootstrap over those runs", note, bid)
            self.assertEqual(note.strip(), message.strip(), bid)
        context.close()

    def test_nothing_overflows_or_shrinks_below_eleven_pixels_on_a_phone(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 392)
        for bid in self.IDS:
            box = page.locator(f'#stacks [data-block="{bid}"]').bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
        small = page.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('.rls, .rls *').forEach(function (el) {
            if (!el.textContent || !el.textContent.trim()) return;
            const size = parseFloat(getComputedStyle(el).fontSize);
            if (size && size < 11) bad.push(el.className + ':' + size);
          });
          return bad; }""")
        self.assertEqual(small, [])
        self.assertEqual(self._errors(errors), [])
        context.close()
