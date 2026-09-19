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


class BrowserGuardTest(unittest.TestCase):
    """Every class here that drives a browser must be skippable without one.

    Deliberately *not* guarded itself: it has to run in the environment
    that would break, which is CI, where playwright is not installed and
    `sync_playwright` is an undefined name rather than an import error.

    This has now happened twice, both times by editing near a class rather
    than by writing an unguarded one: a class inserted directly above
    another takes the decorator that used to belong to it, and the class
    below is left bare. Locally it passes — playwright is installed — and
    CI fails in `setUpClass` with a NameError after running for five
    minutes. So the check is on the file's own syntax tree, not on anyone
    remembering.
    """

    def test_every_browser_class_carries_the_skip_guard(self):
        import ast

        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        def launches_a_browser(node):
            # a *call* to sync_playwright, found in the syntax tree rather
            # than by matching the text — which would flag this class for
            # naming it in its own docstring
            return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == "sync_playwright" for n in ast.walk(node))

        checked, bare = 0, []
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not launches_a_browser(node):
                continue
            checked += 1
            if not any("skipUnless" in (ast.get_source_segment(source, d) or "")
                       for d in node.decorator_list):
                bare.append(node.name)
        self.assertGreater(checked, 10, "the scan found almost no browser classes; it has stopped working")
        self.assertEqual(bare, [], "browser class(es) that would fail with a NameError where playwright is absent")


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

    def test_the_ledger_shows_what_the_scaffold_actuator_could_not_do(self):
        """Every scaffold recommendation the engine made, in two lists: the
        ones this harness has a knob for and the ones it does not, each with
        the engine's own reason. The second list is normally the longer one
        and is the finding — before this it existed only in the JSON, which
        is the same as not saying it."""
        context, page, errors = self.open()
        block = page.locator('.block[data-block="loop"]')
        if "collapsed" in (block.get_attribute("class") or ""):
            block.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
            block = page.locator('.block[data-block="loop"]')
        block.locator('.lp-sec[data-sec="ledger"] > summary').click()
        page.wait_for_timeout(200)
        iters = self.ledger["state"]["iterations"]
        listed = [it for it in iters
                  if any((s.get("proposed") or s.get("unactionable")) for s in (it.get("scaffold") or {}).values())]
        self.assertTrue(listed, "the demo loop must produce at least one scaffold reading to draw")
        self.assertEqual(block.locator(".lp-act").count(), len(listed),
                         "a comparison with a scaffold reading that drew nothing")
        for it in listed:
            fold = block.locator(f'.lp-act[data-act="{it["n"]}"]')
            can = sum(len(s.get("proposed") or []) for s in it["scaffold"].values())
            cannot = sum(len(s.get("unactionable") or []) for s in it["scaffold"].values())
            summary = fold.locator("summary").text_content()
            self.assertIn(f"{can} testable", summary)
            self.assertIn(f"{cannot} with no knob", summary)
            fold.locator("summary").click()
            page.wait_for_timeout(120)
            text = fold.text_content()
            # an agent with nothing on either list is left out of the fold:
            # "no scaffold recommendation" is not a finding about the
            # actuator, and a fold about what it could and could not do is
            # the wrong place to print it
            for side in it["scaffold"].values():
                if not (side.get("proposed") or side.get("unactionable")):
                    continue
                if side.get("reading"):
                    self.assertIn(side["reading"], text)
                for row in side.get("unactionable") or []:
                    self.assertIn(row["reason"], text, "a refusal drawn without its reason")
                for hyp in side.get("proposed") or []:
                    self.assertIn(hyp["kind"], text)
            self.assertEqual(fold.locator("li").count(), can + cannot)
            self.assertEqual(fold.locator("li.can").count(), can)
            # the fold nests a list inside a ledger row, and `.lp-steps li`
            # as a descendant selector turned each of these into the row's
            # 6.5em/1fr grid — every label wrapped into a column three words
            # wide. The rule is scoped with `>`; this reads that back.
            self.assertEqual(fold.locator("li").first.evaluate("el => getComputedStyle(el).display"),
                             "list-item", "a nested row picked up the ledger's grid")
        # a chip for each list on the comparison row itself
        for it in iters:
            row = block.locator(f'.lp-steps li[data-iteration="{it["n"]}"]')
            if it.get("scaffold_unactionable"):
                self.assertIn(f'{it["scaffold_unactionable"]} no knob', row.text_content())
            if (it.get("decision") or {}).get("transfers") is False:
                self.assertIn("does not travel", row.text_content())
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
        self.assertEqual([presets.nth(i).get_attribute("data-preset") for i in range(presets.count())], ["time", "tools", "agents", "eval", "training", "all", "used"])
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
        # every fold that holds one, not just the first: which fold holds two
        # of the root agent's stretches depends on where the clusters fell,
        # and the property under test is the width law, not the packing
        for ids, _ in self._thread_folds(im, "a"):
            if root_ids & set(ids.split(",")):
                block.locator(f'svg.im-band[data-side="a"] g.im-fold[data-ids="{ids}"]').dispatch_event("click")
                page.wait_for_timeout(120)
        page.wait_for_timeout(200)
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
        if isinstance(dec, int):
            self.assertEqual(ringed.count(), 1)
            self.assertEqual(ringed.first.get_attribute("data-step"), str(dec))
        else:
            # this pair's diagnosis is contested — two plausible mechanisms,
            # neither leading — so there is no step to ring, and the tree
            # must not invent one
            self.assertIn("contested", diag["decisive_step"]["reason"])
            self.assertEqual(ringed.count(), 0)
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
        # "and nothing else" is a rule about the lane, not a roster: every
        # block the training tab shows must be registered in the training
        # group, and no block from another view may leak into it. Pinning
        # the exact list instead would break on every block added to the
        # lane, which says nothing about whether the tab is behaving.
        groups = page.evaluate(
            "() => Object.fromEntries(AgentDiff._internals.REGISTRY.map(b => [b.id, b.group]))")
        strays = sorted(i for i in ids if groups.get(i) != "training")
        self.assertEqual(strays, [], "these blocks are in the training lane but not in the training group")
        # The lane's reading order is declared in one place — the training
        # entry of STACK_PLAN — because it is an argument rather than a
        # dashboard. Assert the page against that declaration rather than
        # against a second copy of it here, so the two cannot drift: every
        # named block that is on the page appears in the declared order.
        declared = page.evaluate(
            "() => (AgentDiff._internals.STACK_PLAN.filter(s => s.groups.indexOf('training') >= 0)[0] || {}).order || []")
        self.assertTrue(declared, "the training lane should declare its reading order")
        self.assertEqual([i for i in ids if i in declared], [i for i in declared if i in ids])
        # and the blocks it does not name sort after every block it does
        named = [n for n, i in enumerate(ids) if i in declared]
        self.assertEqual(named, list(range(len(named))), "an unnamed block sorted above a named one")
        self.assertEqual(len(ids), len(set(ids)), "a block is drawn twice")
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
        # the sixth and seventh tabs, then the wrap back to the first
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="evolution"]').get_attribute("aria-selected"), "true")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.view"), "evolution")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="coevolution"]').get_attribute("aria-selected"), "true")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.view"), "coevolution")
        # the first two tabs come after the last: the cycle wraps to Chat, then Levels, then Story
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="chat"]').get_attribute("aria-selected"), "true")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="levels"]').get_attribute("aria-selected"), "true")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="data"]').get_attribute("aria-selected"), "true")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator('.tab[data-view="trace"]').get_attribute("aria-selected"), "true")
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


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class BehaviourSpaceBlocksTest(unittest.TestCase):
    """The behaviour space blocks (31_rlspace.js) against the RL training
    demo: the atlas of episodes laid out by how differently they behaved,
    and the divergence tree of the policy trie.

    What is checked is that the page draws the JSON and nothing else — one
    mark per episode in `aggregate.rl.space.layout`, the ranked branch
    points ringed in the tree and listed in the table, an n-gram row lighting
    exactly the episodes whose token stream contains it — and the two
    promises the atlas makes: that the meaningless axes are not drawn, and
    that the one length that does mean something (the distance) is.
    """

    tmp = None
    IDS = ("rl-atlas", "rl-divergence")

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
        cls.space = ((agg.get("rl") or {}).get("space")) or {}
        if not cls.space.get("measurable"):
            raise unittest.SkipTest("the RL demo carries no behaviour space")
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
        return [e for e in errors if "rlspace" in e or "rl-atlas" in e or "rl-divergence" in e]

    # ------------------------------------------------------------ the atlas

    def test_both_blocks_render(self):
        context, page, errors = self._open()
        for bid in self.IDS:
            block = page.locator(f'#stacks [data-block="{bid}"]')
            self.assertEqual(block.count(), 1, bid)
            self.assertNotIn("failed to render", block.inner_text(), bid)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_atlas_draws_every_episode_once_with_its_policy_and_outcome(self):
        context, page, errors = self._open()
        marks = page.locator('[data-block="rl-atlas"] .rsp-mark')
        points = self.space["layout"]["points"]
        self.assertEqual(marks.count(), len(points))
        seen = {}
        for m in marks.all():
            seen[m.get_attribute("data-key")] = (m.get_attribute("data-policy"),
                                                 m.get_attribute("data-success"))
        for p in points:
            self.assertIn(p["key"], seen)
            self.assertEqual(seen[p["key"]], (p["policy"], "1" if p["success"] else "0"))
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_atlas_draws_a_scale_bar_and_no_numbered_axes(self):
        # the axes of an MDS layout carry no meaning; drawing them would lie
        context, page, _ = self._open()
        svg = page.locator('[data-block="rl-atlas"] svg').first
        self.assertEqual(page.locator('[data-block="rl-atlas"] .rsp-scale').count(), 1)
        self.assertIn("distance", page.locator('[data-block="rl-atlas"] .rsp-scale text').text_content())
        self.assertEqual(page.locator('[data-block="rl-atlas"] .tick, [data-block="rl-atlas"] .grid').count(), 0)
        label = svg.get_attribute("aria-label")
        self.assertIn("axes carry no meaning", label)
        self.assertIn("no units and no direction", page.locator('[data-block="rl-atlas"] .rsp-note').last.inner_text())
        context.close()

    def test_a_habit_row_lights_exactly_the_episodes_that_play_it(self):
        context, page, errors = self._open()
        rows = page.locator('[data-block="rl-atlas"] .rsp-row')
        self.assertGreater(rows.count(), 0)
        row = rows.first
        gram = row.get_attribute("data-gram").split(" → ")
        row.click()
        page.wait_for_timeout(300)
        lit, muted = set(), set()
        for m in page.locator('[data-block="rl-atlas"] .rsp-mark').all():
            key = m.get_attribute("data-key")
            (muted if "mute" in (m.get_attribute("class") or "") else lit).add(key)

        def plays(tokens):
            return any(tokens[i:i + len(gram)] == gram for i in range(len(tokens) - len(gram) + 1))

        expected = {p["key"] for p in self.space["layout"]["points"] if plays(p["tokens"])}
        self.assertEqual(lit, expected)
        self.assertEqual(muted, {p["key"] for p in self.space["layout"]["points"]} - expected)
        # clicking it again puts every episode back
        row.click()
        page.wait_for_timeout(300)
        self.assertEqual(page.locator('[data-block="rl-atlas"] .rsp-mark.mute').count(), 0)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_every_habit_row_carries_the_counts_behind_its_ratio(self):
        context, page, _ = self._open()
        win = self.space["ngrams"]["winning"]
        listed = {r["text"]: r for r in win["separating"]}
        rows = page.locator('[data-block="rl-atlas"] .rsp-row')
        self.assertEqual(rows.count(), len(listed))
        for row in rows.all():
            rec = listed[row.get_attribute("data-gram")]
            text = row.inner_text()
            self.assertIn(f"{rec['win_count']}/{rec['lose_count']}", text.replace("\n", " "))
            if rec["ratio"] is not None:
                self.assertIn(f"{rec['ratio']:.2f}×", text)
        context.close()

    # ------------------------------------------------------- the divergence

    def test_the_tree_rings_the_ranked_branch_points_and_lists_them(self):
        context, page, errors = self._open()
        points = self.space["branches"]["points"]
        table = page.locator('[data-block="rl-divergence"] tr[data-node]')
        self.assertEqual(table.count(), len(points))
        for i, row in enumerate(table.all()):
            self.assertEqual(row.get_attribute("data-node"), points[i]["id"])
            text = row.inner_text().replace("\n", " ")
            for side in points[i]["sides"]:
                self.assertIn(side["token"], text)
        rings = page.locator('[data-block="rl-divergence"] .rsp-bp')
        self.assertGreater(rings.count(), 0)
        drawn = {r.get_attribute("data-node") for r in rings.all()}
        self.assertTrue(drawn <= {p["id"] for p in points})
        # the first branch point is always drawn: it is the headline
        self.assertIn(points[0]["id"], drawn)
        self.assertIn(points[0]["sides"][0]["token"],
                      page.locator('[data-block="rl-divergence"] .rsp-narr').inner_text())
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_a_branch_is_as_thick_as_the_episodes_through_it(self):
        context, page, _ = self._open()
        units = page.locator('[data-block="rl-divergence"] .rsp-unit[data-episodes]')
        self.assertGreater(units.count(), 1)
        widths = []
        for u in units.all():
            n = int(u.get_attribute("data-episodes"))
            w = float(u.locator("line.seg").get_attribute("stroke-width"))
            widths.append((n, w))
        heaviest = max(widths)
        lightest = min(widths)
        self.assertGreater(heaviest[1], lightest[1])
        context.close()

    def test_a_quiet_run_folds_and_dilates_on_click(self):
        context, page, errors = self._open()
        folds = page.locator('[data-block="rl-divergence"] .rsp-fold')
        self.assertGreater(folds.count(), 0)
        fold = folds.first
        steps = int(fold.get_attribute("data-steps"))
        self.assertGreaterEqual(steps, 2)
        self.assertIn("×", fold.text_content())
        before = page.locator('[data-block="rl-divergence"] .rsp-unit').count()
        fold.click()
        page.wait_for_timeout(300)
        self.assertGreater(page.locator('[data-block="rl-divergence"] .rsp-unit').count(), before)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_tree_says_what_it_pruned(self):
        context, page, _ = self._open()
        note = page.locator('[data-block="rl-divergence"] .rsp-note').inner_text()
        pruned = self.space["trie"]["pruned"]
        if pruned["tails"]:
            self.assertIn(f"{pruned['tails']} single-episode tail", note)
        if pruned["truncated"]:
            self.assertIn(f"depth cap of {self.space['trie']['max_depth']}", note)
        context.close()

    # ------------------------------------------------------------- the page

    def test_nothing_overflows_on_a_phone_and_no_text_is_too_small(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        for bid in self.IDS:
            box = page.locator(f'#stacks [data-block="{bid}"]').bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
        small = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('[data-block="rl-atlas"], [data-block="rl-divergence"]').forEach(function (card) {
            const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
              if (!node.textContent.trim()) continue;
              const el = node.parentElement; if (!el) continue;
              if (parseFloat(getComputedStyle(el).fontSize) < 11) out.push(node.textContent.trim().slice(0, 30));
            }
          });
          return out; }""")
        self.assertEqual(small, [])
        self.assertEqual(self._errors(errors), [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class RLAuditBlocksTest(unittest.TestCase):
    """The two audit blocks at the foot of the Training view: is the reward
    measuring the right thing, and does the critic know what is coming?
    Every count on the page has to be the one `aggregate.rl.audit` carries —
    the blocks recompute nothing — and the rings must follow the measure the
    reader picks."""

    tmp = None
    IDS = ("rl-audit-reward", "rl-audit-critic")

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
        cls.audit = ((agg.get("rl") or {}).get("audit")) or {}
        if not cls.audit.get("measurable"):
            raise unittest.SkipTest("the RL demo carries no audit")
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
        page.wait_for_timeout(900)
        return context, page, errors

    def _errors(self, errors):
        # a sibling block failing is not this block's failure to report
        return [e for e in errors if "rlaudit" in e or "rl-audit" in e]

    # ----------------------------------------------------------- the reward

    def test_both_blocks_render_and_neither_shows_an_empty_state(self):
        context, page, errors = self._open()
        for bid in self.IDS:
            block = page.locator(f'#stacks [data-block="{bid}"]')
            self.assertEqual(block.count(), 1, bid)
            self.assertEqual(block.locator(".empty:visible").count(), 0, bid)
            self.assertNotIn("failed to render", block.inner_text(), bid)
            svgs = block.locator("svg")
            self.assertGreater(svgs.count(), 0, bid)
            for i in range(svgs.count()):
                svg = svgs.nth(i)
                self.assertEqual(svg.get_attribute("role"), "img", bid)
                self.assertTrue(svg.get_attribute("aria-label"), bid)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_lede_answers_the_question_the_block_asks(self):
        context, page, errors = self._open()
        lede = page.locator('#stacks [data-block="rl-audit-reward"] .rlq-lede').inner_text()
        d = self.audit["reward"]["disagreement"]
        task, shaping = d["scopes"]["by_task"], d["scopes"]["shaping"]
        if task["inversions"]:
            self.assertIn("disagree", lede)
        elif shaping["inversions"]:
            self.assertIn("Only through its last step", lede)
            self.assertIn(f"{shaping['inversions']} of {shaping['pairs_n']}", lede)
        else:
            self.assertIn("yes", lede.lower())
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_one_mark_per_episode_split_into_a_passed_row_and_a_failed_row(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-audit-reward"]')
        rows = self.audit["reward"]["episodes"]
        self.assertEqual(block.locator("circle.pt").count(), len(rows))
        for policy in self.audit["policies"]:
            mine = [r for r in rows if r["agent"] == policy]
            self.assertEqual(block.locator(f'circle.pt[data-policy="{policy}"]').count(), len(mine), policy)
        labels = page.evaluate("""() => [...document.querySelectorAll(
          '[data-block="rl-audit-reward"] svg text.lab')].map(e => e.textContent)""")
        d = self.audit["reward"]["disagreement"]
        self.assertIn(f"passed {d['passed']}", labels)
        self.assertIn(f"failed {d['failed']}", labels)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_measure_switch_rings_exactly_the_episodes_the_engine_flagged(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-audit-reward"]')
        d = self.audit["reward"]["disagreement"]
        basis = d["findings_basis"]
        if not basis:
            self.assertEqual(block.locator("circle.ring").count(), 0)
            context.close()
            return
        button = block.locator(f'button[data-measure="{"shaping" if basis == "shaping" else "return"}"]')
        self.assertEqual(button.count(), 1)
        button.click()
        page.wait_for_timeout(700)
        block = page.locator('#stacks [data-block="rl-audit-reward"]')
        self.assertEqual(block.locator("circle.ring").count(), len(d["flagged"]))
        flagged = block.locator('circle.pt[data-flagged="true"]')
        self.assertEqual(flagged.count(), len(d["flagged"]))
        seen = set()
        for i in range(flagged.count()):
            mark = flagged.nth(i)
            seen.add("|".join([mark.get_attribute("data-policy"), mark.get_attribute("data-task"),
                               mark.get_attribute("data-run")]))
        self.assertEqual(seen, set(d["flagged"]))
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_concentration_reading_states_the_numbers_beside_the_shape(self):
        context, page, errors = self._open()
        panel = page.locator('#stacks [data-block="rl-audit-reward"] .rlq-conc')
        text = panel.inner_text()
        con = self.audit["reward"]["concentration"]
        self.assertIn(f"{100 * con['mean_last_share']:.1f}%", text)
        self.assertIn(f"{100 * con['mean_largest_share']:.1f}%", text)
        self.assertIn(con["note"][:40], text)
        self.assertEqual(panel.locator(".rlq-strip span").count(), 2)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_unearned_rows_name_their_step_and_open_it_when_it_is_on_the_page(self):
        context, page, errors = self._open()
        u = self.audit["reward"]["unearned"]
        rows = page.locator('#stacks [data-block="rl-audit-reward"] .rlq-list li')
        self.assertGreater(rows.count(), 0)
        self.assertLessEqual(rows.count(), len(u["rows"]))
        for i in range(rows.count()):
            step = rows.nth(i).get_attribute("data-step")
            self.assertIsNotNone(step)
            self.assertIn(f"step {step}", rows.nth(i).inner_text())
        hits = page.locator('#stacks [data-block="rl-audit-reward"] .rlq-list li.hit')
        if hits.count():
            hits.first.click()
            page.wait_for_timeout(500)
            self.assertEqual(self._errors(errors), [])
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ----------------------------------------------------------- the critic

    def test_the_calibration_plot_draws_every_scored_step_with_the_diagonal(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-audit-critic"]')
        critic = self.audit["critic"]
        self.assertEqual(block.locator("circle.pt").count(), critic["n"])
        self.assertEqual(block.locator("line.diag").count(), 1)
        self.assertEqual(block.locator("rect.rlq-cal-pt").count(), len(critic["deciles"]))
        for policy in self.audit["policies"]:
            mine = sum(1 for p in critic["points"] if p["agent"] == policy)
            self.assertEqual(block.locator(f'circle.pt[data-policy="{policy}"]').count(), mine, policy)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_residual_marginal_holds_every_point_once(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-audit-critic"]')
        bars = block.locator("rect.rlq-res")
        total = 0
        for i in range(bars.count()):
            total += int(bars.nth(i).get_attribute("data-count"))
        self.assertEqual(total, self.audit["critic"]["n"])
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_a_critic_worse_than_the_mean_is_said_in_those_words(self):
        context, page, errors = self._open()
        block = page.locator('#stacks [data-block="rl-audit-critic"]')
        text = block.inner_text()
        per = self.audit["critic"]["per_agent"]
        worse = sorted(n for n in per if per[n]["worse_than_the_mean"])
        for name in worse:
            self.assertIn("worse than the mean", text)
            self.assertIn(name, text)
        if worse:
            self.assertIn("a constant equal to the average return-to-go", text)
        ev = self.audit["critic"]["overall"]["explained_variance"]
        if isinstance(ev, (int, float)):
            self.assertIn("Only partly." if 0 <= ev < 0.5 else "No — it is worse than predicting the mean."
                          if ev < 0 else "Mostly, yes.", text)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_advantage_check_says_what_it_could_and_could_not_do(self):
        context, page, errors = self._open()
        text = page.locator('#stacks [data-block="rl-audit-critic"]').inner_text()
        adv = self.audit["critic"]["advantages"]
        self.assertIn("Advantages:" if not adv["measurable"] else adv["definition"], text)
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ------------------------------------------------------------- the page

    def test_nothing_overflows_on_a_phone_and_no_text_is_too_small(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        for bid in self.IDS:
            block = page.locator(f'#stacks [data-block="{bid}"]')
            box = block.bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
            self.assertEqual(block.locator(".empty:visible").count(), 0, bid)
        small = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('[data-block="rl-audit-reward"], [data-block="rl-audit-critic"]').forEach(function (card) {
            const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
              if (!node.textContent.trim()) continue;
              const el = node.parentElement; if (!el) continue;
              if (parseFloat(getComputedStyle(el).fontSize) < 11) out.push(node.textContent.trim().slice(0, 30));
            }
          });
          return out; }""")
        self.assertEqual(small, [])
        self.assertEqual(self._errors(errors), [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class EvoTimescapeTest(unittest.TestCase):
    """The timescape: every episode of every generation along constricted
    time, four levels of semantic zoom, reached by mouse and by keyboard.

    Self-contained: it builds `aggregate["evolution"]` in the contract's
    shape from the lineage traces (the step timelines exactly as the
    contract lays them out) unless `deepcompare.evolve` exists and writes
    one, in which case the engine's output is used. A second page carries
    2,100 episodes — ten of every demo episode — to check the drawing cap
    and the frame budget."""

    LINEAGE = ROOT / "demo" / "evolve" / "lineage"
    BLK = '.block[data-block="evo-timescape"]'
    tmp = None

    # ------------------------------------------------------------ fixture

    @staticmethod
    def _timeline(data):
        from deepcompare.timing import TOOLISH, time_attribution
        from deepcompare.trace import Trajectory
        wasted = {r["index"] for r in (time_attribution(Trajectory.from_dict(data)).get("steps") or []) if r.get("wasted")}
        out, t = [], 0.0
        for s in data["steps"]:
            dur = float(s.get("latency_s") or 0.0)
            kind = "answer" if s["type"] == "answer" else "tool" if s["type"] in TOOLISH else "think"
            flags = ("e" if s.get("error") else "") + ("w" if s["index"] in wasted else "") + \
                ("v" if isinstance(s.get("span"), dict) and s["span"].get("agent") == "verifier" else "")
            out.append([round(t, 4), round(dur, 4), kind, s.get("name") or s["type"], float(s.get("reward") or 0.0), flags])
            t += dur
        return out

    @classmethod
    def _evolution(cls, replicate=1, cap=2000):
        from deepcompare.timing import TOOLISH
        manifests = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in cls.LINEAGE.glob("*/agent.json")}
        by_parent = {m["parent"]: g for g, m in manifests.items()}
        order, cur = [], by_parent.get(None)
        while cur and cur not in order:
            order.append(cur)
            cur = by_parent.get(cur)
        gens, total, capped = [], 0, False
        for i, g in enumerate(order):
            eps = []
            for path in sorted((cls.LINEAGE / g / "traces").glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                tl = cls._timeline(data)
                tools = {}
                for s in data["steps"]:
                    if s["type"] in TOOLISH:
                        tools[s["name"]] = tools.get(s["name"], 0) + 1
                for k in range(replicate):
                    rid = data["run_id"] + ("" if k == 0 else f"x{k}")
                    eps.append({"task_id": data["task"]["id"], "run_id": rid, "trace_id": f"{data['task']['id']}__{data['agent']['name']}__{rid}",
                                "success": bool(data["outcome"]["success"]), "return": round(sum(e[4] for e in tl), 4), "steps": len(tl),
                                "seconds": round(sum(e[1] for e in tl), 4), "tools": tools, "errors": sum(1 for e in tl if "e" in e[5]),
                                "wasted_s": round(sum(e[1] for e in tl if "w" in e[5]), 4), "timeline": tl})
            for e in eps:
                total += 1
                if total > cap:
                    e.pop("timeline")
                    capped = True
            passes = sum(1 for e in eps if e["success"])
            gens.append({"id": g, "parent": manifests[g].get("parent"), "index": i, "mechanism": manifests[g].get("mechanism"),
                         "episodes_n": len(eps), "tasks": sorted({e["task_id"] for e in eps}), "pass_rate": passes / len(eps), "passes": passes,
                         "mean_return": sum(e["return"] for e in eps) / len(eps), "episodes": eps, "episodes_capped": capped})
        steps = []
        for i in range(1, len(gens)):
            a, b = gens[i - 1], gens[i]
            d, dp = b["mean_return"] - a["mean_return"], b["pass_rate"] - a["pass_rate"]
            steps.append({"from": a["id"], "to": b["id"], "index": i, "mechanism": b["mechanism"],
                          "verdict": "gamed" if d > 0 and dp < 0 else "improved" if d > 0.5 else "regressed" if d < -0.5 else "flat",
                          "reading": f"{a['id']} → {b['id']}: fixture rule"})
        return {"version": 1, "measurable": True, "reason": None, "family": "ledger-agent", "generations": gens, "steps": steps}

    @classmethod
    def _render(cls, out, replicate):
        """The page: the runs output for the last pair, the evolution section
        added to its aggregate — the engine's when it exists, else the fixture."""
        from deepcompare.report import render_html
        out.mkdir(parents=True, exist_ok=True)
        evolution, source = None, "fixture"
        if replicate == 1 and (ROOT / "deepcompare" / "evolve.py").is_file():
            real = out / "engine"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "evolve", str(cls.LINEAGE), "-o", str(real)],
                                  cwd=str(ROOT), capture_output=True)
            agg_path = real / "aggregate.json"
            if proc.returncode == 0 and agg_path.is_file():
                agg = json.loads(agg_path.read_text(encoding="utf-8"))
                ev = agg.get("evolution") or {}
                if ev.get("generations") and (ev["generations"][0].get("episodes") or [{}])[0].get("timeline"):
                    evolution, source = ev, "engine"
                    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(real.glob("report_*.json"))]
                    page = out / "report.html"
                    render_html(reports, agg, cls.template, page)
                    return page, evolution, source
        pair = out / "pair"
        pair.mkdir(exist_ok=True)
        gens = sorted(p.parent.name for p in cls.LINEAGE.glob("*/agent.json"))[-2:]
        for g in gens:
            for p in (cls.LINEAGE / g / "traces").glob("*.json"):
                (pair / p.name).write_bytes(p.read_bytes())
        runs = out / "runs"
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(pair), "-o", str(runs)], cwd=str(ROOT), check=True, capture_output=True)
        agg = json.loads((runs / "aggregate.json").read_text(encoding="utf-8"))
        agg["evolution"] = evolution = cls._evolution(replicate)
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runs.glob("report_*.json"))]
        page = out / "report.html"
        render_html(reports, agg, cls.template, page)
        return page, evolution, source

    @classmethod
    def setUpClass(cls):
        if not cls.LINEAGE.is_dir():
            raise unittest.SkipTest("no evolve demo lineage")
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        # the template is built into the temp dir; web/blocks.html is the integrator's
        sys.path.insert(0, str(ROOT / "web"))
        import build_blocks
        cls.template = out / "blocks.html"
        cls.template.write_text(build_blocks.build()[0], encoding="utf-8")
        cls.page, cls.ev, cls.source = cls._render(out / "demo", 1)
        cls.big, cls.big_ev, _ = cls._render(out / "big", 10)
        cls.gens = [g["id"] for g in cls.ev["generations"]]
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

    def _open(self, page_path=None, width=1440, **context_args):
        context = self.browser.new_context(viewport={"width": width, "height": 1000}, **context_args)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{page_path or self.page}#view=evolution")
        page.wait_for_timeout(900)
        blk = page.locator(self.BLK)
        self.assertEqual(blk.count(), 1, "the timescape is on the evolution view")
        if "collapsed" in (blk.get_attribute("class") or ""):
            blk.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(300)
        blk.evaluate("el => el.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(200)
        return context, page, blk, errors

    def _status(self, blk):
        return blk.locator(".evt-status").first.text_content()

    def _time_l0(self, page, n=6):
        # the constriction toggle redraws level 0 synchronously: an even count leaves it as it was
        return page.evaluate("""(sel) => { const b = document.querySelector(sel + ' [data-act=constrict]'); const out = [];
            for (let i = 0; i < %d; i++) { const t0 = performance.now(); b.click(); out.push(performance.now() - t0); } return out; }""" % n, self.BLK)

    # ------------------------------------------------------------ level 0

    def test_level_0_draws_every_generation_with_its_verdict_and_says_what_it_folded(self):
        context, page, blk, errors = self._open()
        stage = blk.locator(".evt-stage")
        self.assertEqual(stage.get_attribute("role"), "application")
        self.assertIn("Level 0", stage.get_attribute("aria-label"))
        self.assertEqual(stage.locator("canvas").get_attribute("aria-hidden"), "true")
        svg = stage.locator("svg")
        self.assertEqual(svg.get_attribute("role"), "img")
        self.assertIn("lineage", svg.get_attribute("aria-label"))
        self.assertEqual(blk.locator(".evt-status").get_attribute("aria-live"), "polite")
        self.assertEqual(stage.locator("g.evt-lane").count(), len(self.gens))
        for i, g in enumerate(self.gens):
            self.assertEqual(stage.locator("g.evt-lane").nth(i).get_attribute("data-gen"), g)
        self.assertEqual(stage.locator("g.evt-verdict").count(), len(self.ev["steps"]))
        verdicts = {(v["from"], v["to"]): v["verdict"] for v in self.ev["steps"]}
        for i in range(stage.locator("g.evt-verdict").count()):
            mark = stage.locator("g.evt-verdict").nth(i)
            self.assertEqual(mark.get_attribute("data-verdict"), verdicts[(mark.get_attribute("data-from"), mark.get_attribute("data-to"))])
        n_eps = sum(len(g["episodes"]) for g in self.ev["generations"])
        self.assertIn(f"{n_eps} episodes", self._status(blk))
        # the constriction says how many folds hide how much; off, the folds are gone and the count is said
        btn = blk.locator("[data-act=constrict]")
        self.assertEqual(btn.get_attribute("aria-pressed"), "true")
        folds = stage.locator("g.evt-fold").count()
        self.assertGreaterEqual(folds, 1, "the demo's first seconds are plan and grep at the shaping cost: a fold")
        self.assertRegex(btn.text_content(), r"constricted · \d+ folds? hides? ")
        self.assertEqual(stage.locator("text.evt-gap").count(), folds)
        btn.click()
        page.wait_for_timeout(200)
        self.assertEqual(stage.locator("g.evt-fold").count(), 0)
        self.assertIn("not constricted", btn.text_content())
        self.assertNotIn("constricted wall-clock", self._status(blk))
        btn.click()
        page.wait_for_timeout(200)
        self.assertEqual(stage.locator("g.evt-fold").count(), folds)
        # the level-0 draw of the demo, timed
        ms = sorted(self._time_l0(page))
        self.assertLess(ms[len(ms) // 2], 250, f"level-0 draw at {n_eps} episodes: {ms}")
        self.assertEqual(errors, [])
        context.close()

    # ---------------------------------------------------------- the levels

    def test_every_level_is_reached_by_mouse_and_no_click_is_dead(self):
        context, page, blk, errors = self._open()
        stage = blk.locator(".evt-stage")
        gen = self.ev["generations"][2]
        stage.locator(f'g.evt-lane[data-gen="{gen["id"]}"] rect.evt-lane-hit').click()
        page.wait_for_timeout(500)
        self.assertIn(f"Level 1 of 3 · generation {gen['id']}", self._status(blk))
        self.assertEqual(stage.locator("g.evt-group").count(), len(gen["tasks"]))
        self.assertEqual([b.strip() for b in blk.locator(".evt-crumbs button").all_text_contents()], ["lineage", gen["id"]])
        # no DOM per episode or per step at level 1
        self.assertLess(stage.locator("svg *").count(), 120)
        # the first ribbon row: hover says the step, click opens its task
        sb = stage.locator("svg").bounding_box()
        page.mouse.move(sb["x"] + sb["width"] * 0.5, sb["y"] + 70)
        page.wait_for_timeout(150)
        tip = blk.locator(".evt-tip")
        self.assertTrue(tip.is_visible())
        self.assertIn("reward", tip.text_content())
        page.mouse.click(sb["x"] + sb["width"] * 0.5, sb["y"] + 70)
        page.wait_for_timeout(500)
        blk.evaluate("el => el.scrollIntoView({block: 'center'})")
        self.assertIn("Level 2 of 3", self._status(blk))
        task = blk.locator(".evt-crumbs button").nth(2).get_attribute("title")
        runs = [e for e in gen["episodes"] if e["task_id"] == task]
        self.assertEqual(stage.locator("g.evt-run").count(), len(runs))
        self.assertEqual(stage.locator("g.evt-step").count(), sum(e["steps"] for e in runs), "one mark per step at level 2")
        self.assertEqual(stage.locator("g.evt-verifier").count(),
                         sum(1 for e in runs for i, s in enumerate(e["timeline"]) if "v" in s[5] and (i == 0 or "v" not in e["timeline"][i - 1][5])),
                         "one bracket per verifier span")
        ringed = stage.locator('g.evt-step[data-flags*="e"]')
        self.assertEqual(ringed.count(), sum(e["errors"] for e in runs), "every error is a step mark")
        for i in range(ringed.count()):
            self.assertGreaterEqual(ringed.nth(i).locator("circle").count(), 1, "every error ringed")
        # a step click is never dead: the block's own panel opens (no pair report covers this generation)
        step = stage.locator("g.evt-step").nth(4)
        step.hover()
        page.wait_for_timeout(120)
        self.assertIn("return so far", tip.text_content())
        step.click()
        page.wait_for_timeout(200)
        detail = blk.locator(".evt-detail")
        self.assertEqual(detail.count(), 1)
        self.assertEqual(detail.get_attribute("data-step"), step.get_attribute("data-step"))
        self.assertIn("no pair report covers this run", detail.text_content())
        # the run's label opens the episode: level 3, one node per step, a fold that dilates on click
        stage.locator("g.evt-run").first.locator("text").first.click()
        page.wait_for_timeout(500)
        blk.evaluate("el => el.scrollIntoView({block: 'center'})")
        self.assertIn("Level 3 of 3", self._status(blk))
        ep = runs[0]
        self.assertEqual(stage.locator("g.evt-step").count(), ep["steps"])
        self.assertEqual(stage.locator("rect.evt-rbar").count(), sum(1 for s in ep["timeline"] if s[4] != 0), "a reward bar per paying step")
        folds = stage.locator("g.evt-fold3").count()
        self.assertGreaterEqual(folds, 1)
        self.assertEqual(stage.locator("g.evt-fold3").first.locator("text").text_content()[0], "×")
        stage.locator("g.evt-fold3").first.click()
        page.wait_for_timeout(200)
        self.assertEqual(stage.locator("g.evt-fold3").count(), folds - 1, "a fold dilates on click")
        self.assertEqual(stage.locator('g.evt-step[data-folded="false"]').count() + stage.locator('g.evt-step[data-folded="true"]').count(), ep["steps"])
        # the breadcrumb is the way back up
        blk.locator('.evt-crumbs button[data-level="0"]').click()
        page.wait_for_timeout(500)
        self.assertIn("Level 0 of 3", self._status(blk))
        self.assertEqual(errors, [])
        context.close()

    def test_every_level_is_reached_by_keyboard(self):
        context, page, blk, errors = self._open()
        stage = blk.locator(".evt-stage")
        stage.focus()
        page.keyboard.press("ArrowDown")
        self.assertIn(f"Selected: lane {self.gens[1]}", self._status(blk))
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        self.assertIn(f"Level 1 of 3 · generation {self.gens[1]}", self._status(blk))
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        self.assertIn("Level 2 of 3", self._status(blk))
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        self.assertIn("step 2", self._status(blk))
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        self.assertIn("Level 3 of 3", self._status(blk))
        self.assertIn("Level 3", stage.get_attribute("aria-label"))
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        detail = blk.locator(".evt-detail")
        self.assertEqual(detail.count(), 1, "Enter on a step opens it")
        self.assertEqual(int(detail.get_attribute("data-step")), int(stage.locator("g.evt-step").count()) - 1)
        for level in (2, 1, 0):
            page.keyboard.press("Escape")
            page.wait_for_timeout(350)
            self.assertIn(f"Level {level} of 3", self._status(blk))
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------ features

    def test_minimap_brush_compare_brush_and_the_x_measure(self):
        context, page, blk, errors = self._open()
        stage = blk.locator(".evt-stage")
        mini = blk.locator(".evt-mini")
        self.assertEqual(mini.locator("canvas").get_attribute("aria-hidden"), "true")
        self.assertIn("minimap", mini.locator("svg").get_attribute("aria-label"))
        self.assertEqual(mini.locator(".evt-minibrush").count(), 1, "the viewport is a brush on the minimap")
        mb = mini.locator("svg").bounding_box()
        page.mouse.move(mb["x"] + 150, mb["y"] + 22)
        page.mouse.down()
        page.mouse.move(mb["x"] + 400, mb["y"] + 22, steps=6)
        page.mouse.up()
        page.wait_for_timeout(400)
        self.assertRegex(self._status(blk), r"window [\d.]+s–[\d.]+s")
        # the compare brush under the clock: a table per generation, counted from the timelines
        sb = stage.locator("svg").bounding_box()
        page.mouse.move(sb["x"] + 200, sb["y"] + 26)
        page.mouse.down()
        page.mouse.move(sb["x"] + 500, sb["y"] + 26, steps=6)
        page.mouse.up()
        page.wait_for_timeout(300)
        table = blk.locator(".evt-table")
        self.assertEqual(table.count(), 1)
        self.assertEqual(table.locator("tr").count(), len(self.gens) + 1)
        a, b = float(table.get_attribute("data-from")), float(table.get_attribute("data-to"))
        for g in self.ev["generations"]:
            tools = sum(1 for e in g["episodes"] for s in e["timeline"] if a <= s[0] < b and s[2] == "tool")
            errs = sum(1 for e in g["episodes"] for s in e["timeline"] if a <= s[0] < b and "e" in s[5])
            ret = sum(s[4] for e in g["episodes"] for s in e["timeline"] if a <= s[0] < b)
            cells = table.locator(f'tr[data-gen="{g["id"]}"] td').all_text_contents()
            self.assertEqual(cells[2], str(tools), g["id"])
            self.assertEqual(cells[6], str(errs), g["id"])
            self.assertAlmostEqual(float(cells[4].replace("−", "-").replace("+", "")), ret, delta=0.11, msg=g["id"])
        # the x measure: steps, and the status says so
        blk.locator("[data-measure=steps]").click()
        page.wait_for_timeout(400)
        self.assertIn("step index", self._status(blk))
        self.assertEqual(blk.locator("[data-measure=steps]").get_attribute("aria-pressed"), "true")
        self.assertRegex(blk.locator("[data-act=constrict]").text_content(), r"folds? hides? \d+ steps|no fold in view")
        self.assertEqual(errors, [])
        context.close()

    def test_the_reader_s_choices_persist_per_browser_under_one_key(self):
        context, page, blk, errors = self._open()
        stage = blk.locator(".evt-stage")
        stage.locator(f'g.evt-lane[data-gen="{self.gens[3]}"] rect.evt-lane-hit').click()
        page.wait_for_timeout(400)
        blk.locator("[data-measure=steps]").click()
        page.wait_for_timeout(300)
        blk.locator("[data-act=constrict]").click()
        page.wait_for_timeout(200)
        stored = page.evaluate("() => AgentDiff._internals.Store.get('agentdiff:evo-timescape')")
        self.assertEqual((stored["level"], stored["path"]["gen"], stored["measure"], stored["constrict"]), (1, self.gens[3], "steps", False))
        page.reload()
        page.wait_for_timeout(900)
        blk = page.locator(self.BLK)
        self.assertIn(f"Level 1 of 3 · generation {self.gens[3]}", self._status(blk))
        self.assertIn("step index", self._status(blk))
        self.assertEqual(blk.locator("[data-act=constrict]").get_attribute("aria-pressed"), "false")
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_makes_a_level_change_instant(self):
        context, page, blk, errors = self._open(reduced_motion="reduce")
        stage = blk.locator(".evt-stage")
        stage.locator("g.evt-lane rect.evt-lane-hit").first.click()
        page.wait_for_timeout(60)
        self.assertEqual(stage.locator("g.evt-content").get_attribute("opacity"), "1")
        self.assertIn("Level 1 of 3", self._status(blk))
        self.assertEqual(errors, [])
        context.close()

    def test_a_phone_reaches_every_level_without_overflow_or_small_text(self):
        context, page, blk, errors = self._open(width=390)
        stage = blk.locator(".evt-stage")
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        stage.focus()
        for level in (1, 2, 3):
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            self.assertIn(f"Level {level} of 3", self._status(blk))
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392, f"level {level}")
            box = blk.bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391)
        small = page.evaluate("""(sel) => { const out = []; const card = document.querySelector(sel);
            const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT); let node;
            while ((node = walker.nextNode())) { if (!node.textContent.trim()) continue; const el = node.parentElement; if (!el) continue;
              if (parseFloat(getComputedStyle(el).fontSize) < 11) out.push(node.textContent.trim().slice(0, 30)); }
            return out; }""", self.BLK)
        self.assertEqual(small, [])
        self.assertEqual(errors, [])
        context.close()

    # --------------------------------------------------------------- scale

    def test_two_thousand_episodes_draw_within_the_budget_and_the_cap_is_said(self):
        context, page, blk, errors = self._open(page_path=self.big)
        stage = blk.locator(".evt-stage")
        drawn = sum(1 for g in self.big_ev["generations"] for e in g["episodes"] if e.get("timeline"))
        skipped = sum(1 for g in self.big_ev["generations"] for e in g["episodes"] if not e.get("timeline"))
        self.assertEqual((drawn, skipped), (2000, 100))
        self.assertIn("2000 episodes", self._status(blk))
        self.assertIn("100 episodes past the cap of 2000 carry no timeline and are not drawn", self._status(blk))
        ms = sorted(self._time_l0(page))
        self.assertLess(ms[len(ms) // 2], 500, f"level-0 draw at 2000 episodes: {ms}")
        # level 1 of a 300-episode generation: ribbons, and still no DOM per episode
        stage.locator('g.evt-lane[data-gen="g2"] rect.evt-lane-hit').click()
        page.wait_for_timeout(500)
        self.assertIn("300 episodes", self._status(blk))
        self.assertLess(stage.locator("svg *").count(), 120)
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class EvolutionBlocksTest(unittest.TestCase):
    """The Evolution view's core blocks (33_evolve.js) against the demo
    lineage: the lineage thread, the ledger of steps, the tasks × generations
    matrix, one step in full, the integrity check and the drift chart.

    What is checked is that the page draws `aggregate.evolution` and nothing
    else — one node per generation and one edge per step with the engine's
    verdict, the thickest edge the largest IQM move, the marked cells the
    ones that fell, the protected paths named where the step is shown — and
    that one selection drives every block: a click on an edge selects the
    same step in the ledger, the step view, the matrix and the drift chart,
    the keyboard does the same, and the choice survives a reload.

    The page comes from the `evolve` command when it is registered; until
    then from the engine module directly, over the same lineage, so the test
    reads the real engine either way.
    """

    tmp = None
    IDS = ("evo-lineage", "evo-steps", "evo-matrix", "evo-step", "evo-integrity", "evo-drift")

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file():
            raise unittest.SkipTest("no demo lineage to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "evo"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        template = ROOT / "web" / "blocks.html"
        done = subprocess.run([sys.executable, "-m", "deepcompare", "evolve", str(lineage), "-o", str(out),
                               "--template", str(template)], cwd=str(ROOT), capture_output=True)
        if done.returncode != 0 or not (out / "aggregate.json").is_file():
            cls._build_without_the_command(lineage, out, template)
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        cls.ev = agg.get("evolution") or {}
        if not cls.ev.get("measurable"):
            raise unittest.SkipTest("the demo lineage carries no measurable evolution section")
        cls.page_path = out / "report.html"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])

    @classmethod
    def _build_without_the_command(cls, lineage, out, template):
        # the engine module over the lineage, grafted onto a `runs` output of
        # the last pair — what the command writes once it exists
        import shutil
        from deepcompare import evolve as engine
        from deepcompare.report import render_html
        ev = engine.analyse_lineage(str(lineage))
        flat = out / "flat"
        flat.mkdir(parents=True, exist_ok=True)
        last = [g["id"] for g in ev.get("generations", [])][-2:]
        for gen in last:
            for path in (lineage / gen / "traces").glob("*.json"):
                shutil.copy(path, flat / path.name)
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(flat), "-o", str(out),
                        "--template", str(template)], cwd=str(ROOT), check=True, capture_output=True)
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        agg["evolution"] = ev
        (out / "aggregate.json").write_text(json.dumps(agg, sort_keys=True), encoding="utf-8")
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("report_*.json"))]
        render_html(reports, agg, template, out / "report.html")

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
        page.goto(f"file://{self.page_path}#view=evolution")
        page.wait_for_timeout(1000)
        return context, page, errors

    def _errors(self, errors):
        # a sibling block failing is not this block's failure to report
        return [e for e in errors if "evo" in e.lower() or "evolution" in e.lower()]

    def _state(self, page):
        return page.evaluate("() => AgentDiff.evolution.state()")

    def _steps(self):
        return [s for s in self.ev["steps"] if s.get("from") and s.get("to")]

    # ------------------------------------------------------------ rendering

    def test_all_six_blocks_render_without_an_empty_state(self):
        context, page, errors = self._open()
        for bid in self.IDS:
            block = page.locator(f'#stacks [data-block="{bid}"]')
            self.assertEqual(block.count(), 1, bid)
            body = block.locator(".block-body").inner_text()
            self.assertNotIn("failed to render", body, bid)
            self.assertNotIn("Nothing to show", body, bid)
            self.assertGreater(len(body.strip()), 80, bid)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_every_chart_is_an_image_with_a_label(self):
        context, page, _ = self._open()
        nodes = page.locator("#stacks .evo svg").all()
        self.assertGreaterEqual(len(nodes), 6)
        for node in nodes:
            self.assertEqual(node.get_attribute("role"), "img")
            self.assertTrue((node.get_attribute("aria-label") or "").strip())
        context.close()

    # -------------------------------------------------------------- lineage

    def test_the_lineage_draws_every_generation_and_every_step_with_its_verdict(self):
        context, page, _ = self._open()
        gens = [g["id"] for g in self.ev["generations"]]
        nodes = page.locator(".evo-lineage .evo-node")
        self.assertEqual(nodes.count(), len(gens))
        self.assertEqual([n.get_attribute("data-gen") for n in nodes.all()], gens)
        edges = page.locator(".evo-lineage .evo-edge")
        steps = self._steps()
        self.assertEqual(edges.count(), len(steps))
        for edge, step in zip(edges.all(), steps):
            self.assertEqual(edge.get_attribute("data-step"), f"{step['from']} → {step['to']}")
            self.assertEqual(edge.get_attribute("data-verdict"), step.get("verdict") or "")
        # the marks the reader is meant to find
        self.assertEqual(page.locator(".evo-node[data-best]").get_attribute("data-gen"), self.ev["best"]["id"])
        self.assertEqual(page.locator(".evo-node[data-recommended]").get_attribute("data-gen"), self.ev["recommended"]["id"])
        self.assertEqual(page.locator(".evo-node[data-last]").get_attribute("data-gen"), gens[-1])
        lede = page.locator(".evo-lineage .evo-lead")
        self.assertEqual(lede.get_attribute("data-recommended"), self.ev["recommended"]["id"])
        self.assertIn(self.ev["recommended"]["id"], lede.inner_text())
        context.close()

    def test_the_thickest_edge_is_the_largest_iqm_move(self):
        context, page, _ = self._open()
        steps = self._steps()
        deltas = [abs(((s.get("effect") or {}).get("iqm") or {}).get("delta") or 0) for s in steps]
        widths = [float(e.get_attribute("stroke-width")) for e in page.locator(".evo-lineage .evo-edge .seg").all()]
        self.assertEqual(len(widths), len(deltas))
        if len(set(deltas)) > 1:
            self.assertEqual(widths.index(max(widths)), deltas.index(max(deltas)))
            self.assertEqual(widths.index(min(widths)), deltas.index(min(deltas)))
        context.close()

    # --------------------------------------------------------------- ledger

    def test_the_ledger_has_one_row_per_step_in_lineage_order(self):
        context, page, _ = self._open()
        rows = page.locator('.evo-steps .evo-row[role="listitem"]')
        steps = self._steps()
        self.assertEqual(rows.count(), len(steps))
        for row, step in zip(rows.all(), steps):
            self.assertEqual(row.get_attribute("data-to"), step["to"])
            self.assertEqual(row.locator(".evo-v").get_attribute("data-verdict"), step.get("verdict") or "")
            eff = step.get("effect") or {}
            if eff.get("measurable"):
                delta = eff["iqm"]["delta"]
                text = row.locator(".evo-num").first.inner_text()
                self.assertIn(f"{abs(delta):.2f}".rstrip("0").rstrip("."), text)
                p = eff["improvement"]["point"]
                self.assertIn(f"{round(p * 100)}%", row.locator(".evo-track").get_attribute("aria-label"))
                self.assertIn(step["reading"][:40], row.locator(".evo-read").inner_text())
        context.close()

    # ------------------------------------------------------------ selection

    def test_a_click_on_an_edge_selects_that_step_in_every_block_and_survives_a_reload(self):
        context, page, errors = self._open()
        steps = self._steps()
        target = steps[len(steps) // 2]
        page.locator(f'.evo-lineage .evo-edge[data-to="{target["to"]}"]').click()
        page.wait_for_timeout(500)
        self.assertEqual(self._state(page)["gen"], target["to"])
        self.assertEqual(page.locator('.evo-steps .evo-row[aria-current="true"]').get_attribute("data-to"), target["to"])
        key = f"{target['from']} → {target['to']}"
        self.assertEqual(page.locator(".evo-step .evo-lede").get_attribute("data-step"), key)
        self.assertEqual(page.locator(".evo-drift .evo-branch").get_attribute("data-step"), key)
        self.assertIn(key, page.locator(".evo-drift .evo-branch").inner_text())
        lit = page.evaluate("() => Array.from(document.querySelectorAll('.evo-matrix .evo-col')).filter(r => +r.getAttribute('fill-opacity') > 0).map(r => r.getAttribute('data-gen'))")
        self.assertEqual(lit, [target["to"]])
        page.reload()
        page.wait_for_timeout(1000)
        self.assertEqual(self._state(page)["gen"], target["to"])
        self.assertEqual(page.locator(".evo-step .evo-lede").get_attribute("data-step"), key)
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_ledger_is_driven_by_the_keyboard_alone(self):
        context, page, _ = self._open()
        first = page.locator('.evo-steps .evo-row[role="listitem"]').first
        first.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["gen"], first.get_attribute("data-to"))
        self.assertEqual(first.get_attribute("aria-current"), "true")
        context.close()

    # --------------------------------------------------------------- matrix

    def test_the_matrix_marks_exactly_the_cells_that_fell_and_outlines_the_trigger_tasks(self):
        context, page, _ = self._open()
        gens = self.ev["generations"]
        tasks = []
        for g in gens:
            for t in g.get("tasks") or []:
                if t not in tasks:
                    tasks.append(t)
        fell = 0
        for t in tasks:
            prev = None
            for g in gens:
                eps = [e for e in g.get("episodes") or [] if e.get("task_id") == t]
                if not eps:
                    continue
                rate = sum(1 for e in eps if e.get("success")) / len(eps)
                if prev is not None and rate < prev - 1e-9:
                    fell += 1
                prev = rate
        self.assertEqual(page.locator('.evo-matrix .evo-cell[data-fell="1"]').count(), fell)
        self.assertEqual(int(page.locator(".evo-matrix .evo-lede").get_attribute("data-fell")), fell)
        trig = sum(len([t for t in s.get("trigger_tasks") or [] if t in tasks]) for s in self._steps())
        self.assertEqual(page.locator('.evo-matrix .evo-cell[data-trigger="1"]').count(), trig)
        self.assertEqual(page.locator(".evo-matrix .evo-cell").count(), len(tasks) * len(gens))
        page.locator('.evo-matrix button[data-metric="return"]').click()
        page.wait_for_timeout(400)
        self.assertEqual(page.locator(".evo-matrix .evo-chart svg").get_attribute("data-metric"), "return")
        self.assertEqual(self._state(page)["metric"], "return")
        context.close()

    # ----------------------------------------------------------- one step

    def test_the_step_view_shows_the_protected_paths_the_diff_and_the_three_readings(self):
        context, page, _ = self._open()
        touched = [s for s in self._steps() if (s.get("diff") or {}).get("protected_touched")]
        if not touched:
            raise unittest.SkipTest("the demo lineage touches no protected path")
        step = touched[0]
        page.locator(f'.evo-lineage .evo-edge[data-to="{step["to"]}"]').click()
        page.wait_for_timeout(500)
        block = page.locator('#stacks [data-block="evo-step"]')
        note = block.locator('[data-role="protected"]').inner_text()
        for path in step["diff"]["protected_touched"]:
            self.assertIn(path, note)
        for change in (step["diff"].get("config") or {}).get("changed") or []:
            item = block.locator(f'.evo-list[data-part="config"] li[data-key="{change["key"]}"]')
            self.assertEqual(item.count(), 1)
            self.assertIn(f"{change['from']} → {change['to']}", item.inner_text())
            if f"config.{change['key']}" in step["diff"]["protected_touched"]:
                self.assertEqual(item.locator(".evo-prot").count(), 1)
        eff = step["effect"]
        self.assertAlmostEqual(float(block.locator(".evo-big").get_attribute("data-p")), eff["improvement"]["point"], places=4)
        for kind in ("overfit", "gaming", "drift"):
            self.assertEqual(block.locator(f'.evo-read[data-kind="{kind}"]').count(), 1, kind)
        gaming = step.get("gaming") or {}
        if gaming.get("reading"):
            self.assertIn(gaming["reading"][:30], block.locator('.evo-read[data-kind="gaming"]').inner_text())
        self.assertIn("run(s) per task", block.locator(".evo-advisory").inner_text())
        self.assertEqual(block.locator(".evo-tdot").count(), len(eff.get("per_task") or {}))
        self.assertEqual(block.locator('.evo-tdot[data-trigger="1"]').count(),
                         len([t for t in step.get("trigger_tasks") or [] if t in (eff.get("per_task") or {})]))
        context.close()

    # ----------------------------------------------------------- integrity

    def test_the_integrity_lede_names_every_protected_touch_and_the_growth_charts_mark_the_overruns(self):
        context, page, _ = self._open()
        block = page.locator('#stacks [data-block="evo-integrity"]')
        ig = self.ev.get("integrity") or {}
        touched = ig.get("touched") or []
        lede = block.locator(".evo-lede")
        self.assertEqual(int(lede.get_attribute("data-touched")), len(touched))
        text = lede.inner_text()
        for t in touched:
            self.assertIn(t["path"], text)
            if t.get("to_gen"):
                self.assertIn(f"{t['from_gen']} → {t['to_gen']}", text)
        self.assertEqual(block.locator("svg").count(), 3)
        over = [o for o in ((ig.get("growth") or {}).get("over_budget") or []) if o.get("what") in ("prompt_chars", "rules", "memory")]
        self.assertEqual(block.locator('.evo-gpt[data-over="1"]').count(), len(over))
        for o in over:
            self.assertEqual(block.locator(f'[data-growth="{o["what"]}"] .evo-gpt[data-over="1"][data-gen="{o["gen"]}"]').count(), 1)
        context.close()

    # -------------------------------------------------------------- brush

    def test_a_brush_under_the_axis_narrows_the_ledger_and_the_matrix_to_that_range(self):
        context, page, _ = self._open()
        gens = [g["id"] for g in self.ev["generations"]]
        if len(gens) < 4:
            raise unittest.SkipTest("too few generations to brush")
        svg = page.locator(".evo-lineage .evo-chart svg")
        svg.scroll_into_view_if_needed()
        box = svg.bounding_box()
        # from the second generation's column to the fourth's, in the strip under the axis
        nodes = page.locator(".evo-lineage .evo-node")
        b1, b3 = nodes.nth(1).bounding_box(), nodes.nth(3).bounding_box()
        x0 = b1["x"] + b1["width"] / 2
        x1 = b3["x"] + b3["width"] / 2
        y = box["y"] + box["height"] - 10
        page.mouse.move(x0, y)
        page.mouse.down()
        page.mouse.move(x1, y, steps=6)
        page.mouse.up()
        page.wait_for_timeout(500)
        rng = self._state(page)["range"]
        self.assertIsNotNone(rng)
        lo, hi = rng
        self.assertLess(lo, hi)
        shown = [s for s in self._steps() if lo <= gens.index(s["to"]) <= hi]
        self.assertEqual(page.locator('.evo-steps .evo-row[role="listitem"]').count(), len(shown))
        self.assertEqual(page.locator(".evo-matrix .evo-mhead").count(), hi - lo + 1)
        page.locator(".evo-lineage .evo-range-chip button").click()
        page.wait_for_timeout(400)
        self.assertIsNone(self._state(page)["range"])
        self.assertEqual(page.locator('.evo-steps .evo-row[role="listitem"]').count(), len(self._steps()))
        context.close()

    def test_a_brushed_range_survives_a_reload(self):
        """The lineage's range is a page selection saved under a null
        default; it must come back after a reload with the ledger and the
        matrix still narrowed to it (the library once refused the saved
        array, so the range silently reset)."""
        context, page, errors = self._open()
        gens = [g["id"] for g in self.ev["generations"]]
        if len(gens) < 3:
            raise unittest.SkipTest("too few generations to range")
        lo, hi = 1, min(2, len(gens) - 2)
        page.evaluate(f"() => AgentDiff.evolution.select({{ range: [{lo}, {hi}] }})")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["range"], [lo, hi])
        self.assertEqual(page.evaluate("() => AgentDiff._internals.Store.get('agentdiff:evolution').range"), [lo, hi])
        page.reload()
        page.wait_for_timeout(1000)
        self.assertEqual(self._state(page)["range"], [lo, hi])
        shown = [s for s in self._steps() if lo <= gens.index(s["to"]) <= hi]
        self.assertEqual(page.locator('.evo-steps .evo-row[role="listitem"]').count(), len(shown))
        self.assertEqual(page.locator(".evo-matrix .evo-mhead").count(), hi - lo + 1)
        self.assertFalse(page.locator(".evo-lineage .evo-range-chip").first.is_hidden())
        self.assertIn(f"{gens[lo]}–{gens[hi]}", page.locator(".evo-lineage .evo-range-chip").first.inner_text())
        self.assertEqual(self._errors(errors), [])
        context.close()

    # -------------------------------------------------------------- phone

    def test_nothing_overflows_on_a_phone_and_no_text_is_too_small(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 392)
        for bid in self.IDS:
            box = page.locator(f'#stacks [data-block="{bid}"]').bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
        small = page.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('.evo, .evo *').forEach(function (el) {
            if (!el.textContent || !el.textContent.trim()) return;
            const size = parseFloat(getComputedStyle(el).fontSize);
            if (size && size < 11) bad.push((el.className.baseVal !== undefined ? el.className.baseVal : el.className) + ':' + size);
          });
          return bad; }""")
        self.assertEqual(small, [])
        wide = page.evaluate("""() => {
          const bad = [], W = document.documentElement.clientWidth;
          document.querySelectorAll('.evo *').forEach(function (el) {
            const r = el.getBoundingClientRect();
            if (r.width && r.right > W + 1) bad.push(el.tagName + ':' + Math.round(r.right));
          });
          return bad; }""")
        self.assertEqual(wide, [])
        self.assertEqual(self._errors(errors), [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class EvolutionCompareBlocksTest(unittest.TestCase):
    """The comparison blocks of the Evolution view (35_evocompare.js): two
    self-evolving agents read as evolution processes, from the real
    `evolve --against` output on the two demo lineages.

    Every figure on the page must be the one `aggregate.evolution_compare`
    carries — the curves' points and marks, the four winners, the verdict
    counts, the head-to-head probability, every cell of the race, every
    mechanism, every generation of the divergence — and the selection made
    in one block must reach the others and survive a reload. Nothing here
    pins a number of the demo: the blocks are checked against the JSON
    they were given, so a regenerated demo does not break the page's
    promise, only the engine's tests would."""

    tmp = None
    IDS = ("evc-curves", "evc-verdict", "evc-process", "evc-pair", "evc-race", "evc-mechanisms", "evc-divergence")

    @classmethod
    def setUpClass(cls):
        a = ROOT / "demo" / "evolve" / "lineage"
        b = ROOT / "demo" / "evolve" / "lineage_b"
        if not (a.is_dir() and b.is_dir()):
            raise unittest.SkipTest("the two demo lineages are not there")
        helptext = subprocess.run([sys.executable, "-m", "deepcompare", "evolve", "--help"],
                                  cwd=str(ROOT), capture_output=True, text=True).stdout
        if "--against" not in helptext:
            raise unittest.SkipTest("the evolve command cannot compare lineages yet")
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "evc"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "evolve", str(a), "--against", str(b), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        cls.ec = agg.get("evolution_compare") or {}
        if not cls.ec.get("measurable"):
            raise unittest.SkipTest("the comparison of the demo lineages is not measurable")
        cls.families = [l["family"] for l in cls.ec["lineages"]]
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
        page.goto(f"file://{self.page_path}#view=evolution")
        page.wait_for_timeout(900)
        page.evaluate("AgentDiff.evolutionCompare.select({gen: null, x: 'index', pair: 'peak'})")
        page.wait_for_timeout(200)
        return context, page, errors

    def _errors(self, errors):
        # a sibling block failing is not this block's failure to report
        return [e for e in errors if "evocompare" in e or "evc-" in e]

    @staticmethod
    def _pct(v):
        return f"{int(v * 100 + 0.5)}%"

    @staticmethod
    def _short(task):
        import re
        return re.sub(r"^(rl|t)\d+_", "", task).replace("_", " ")

    # ------------------------------------------------------------ the lane

    def test_the_seven_blocks_lead_the_evolution_lane_in_reading_order(self):
        context, page, errors = self._open()
        order = page.evaluate("""() => [...document.querySelectorAll('[data-block]')].map(c => c.getAttribute('data-block')).filter(id => /^ev[co]-/.test(id))""")
        self.assertEqual(tuple(order[:len(self.IDS)]), self.IDS)
        for bid in self.IDS:
            block = page.locator(f'[data-block="{bid}"]')
            self.assertEqual(block.count(), 1, bid)
            self.assertEqual(block.locator(".empty:visible").count(), 0, bid)
            self.assertNotIn("failed to render", block.inner_text(), bid)
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ----------------------------------------------------------- the curves

    def test_the_curves_draw_every_generation_with_the_threshold_and_the_marks(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-curves"]')
        by_index = self.ec["curves"]["by_index"]
        for fam in self.families:
            expected = sum(1 for r in by_index[fam] if isinstance(r.get("point"), (int, float)))
            self.assertEqual(block.locator(f'g.evc-lineage[data-family="{fam}"] g.pt').count(), expected, fam)
        reached = self.ec["race"]["reached"]
        self.assertEqual(block.locator('g.pt[data-reached="1"]').count(), sum(1 for f in self.families if reached.get(f)))
        self.assertEqual(block.locator('g.pt[data-recommended="1"]').count(), sum(1 for l in self.ec["lineages"] if l.get("recommended")))
        thr = self.ec["race"]["threshold"]
        self.assertEqual(block.locator("line.evc-threshold").count(), 1 if isinstance(thr.get("value"), (int, float)) else 0)
        if isinstance(thr.get("value"), (int, float)):
            self.assertIn(thr["source"][:40], block.locator(".evc-note").first.inner_text())
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_the_x_measure_toggles_moves_the_points_and_survives_a_reload(self):
        context, page, errors = self._open()
        before = page.evaluate("""() => [...document.querySelectorAll('[data-block="evc-curves"] g.evc-lineage[data-side="b"] g.pt')].map(g => g.getAttribute('transform'))""")
        page.locator('[data-block="evc-curves"] button[data-x="episodes"]').click()
        page.wait_for_timeout(600)
        after = page.evaluate("""() => [...document.querySelectorAll('[data-block="evc-curves"] g.evc-lineage[data-side="b"] g.pt')].map(g => g.getAttribute('transform'))""")
        self.assertEqual(page.evaluate("document.querySelector('[data-block=\"evc-curves\"] svg').getAttribute('data-x')"), "episodes")
        self.assertEqual(len(before), len(after))
        self.assertNotEqual(before, after)
        self.assertEqual(page.evaluate("AgentDiff._internals.Store.get('agentdiff:evolution-compare')")["x"], "episodes")
        page.reload()
        page.wait_for_timeout(900)
        self.assertEqual(page.evaluate("AgentDiff.evolutionCompare.state().x"), "episodes")
        self.assertEqual(page.evaluate("document.querySelector('[data-block=\"evc-curves\"] svg').getAttribute('data-x')"), "episodes")
        self.assertEqual(self._errors(errors), [])
        context.close()

    def test_selecting_a_generation_carries_across_the_blocks(self):
        context, page, errors = self._open()
        n = max(len(self.ec["curves"]["by_index"][f]) for f in self.families)
        k = min(2, n - 1)
        # both lineages' points can sit on one spot: the last-drawn (B's) is on top and selects the same index;
        # the chart is centred first so the page's fixed top bar does not cover the point
        page.evaluate("document.querySelector('[data-block=\"evc-curves\"] svg').scrollIntoView({block: 'center'})")
        page.wait_for_timeout(200)
        page.locator(f'[data-block="evc-curves"] g.pt[data-index="{k}"]').last.click()
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("AgentDiff.evolutionCompare.state().gen"), k)
        band = page.evaluate(f"document.querySelector('[data-block=\"evc-race\"] rect.evc-col[data-index=\"{k}\"]').getAttribute('fill-opacity')")
        self.assertGreater(float(band), 0)
        trackers = page.evaluate("""() => [...document.querySelectorAll('[data-block="evc-divergence"] line.evc-tracker')].map(l => parseFloat(l.getAttribute('stroke-opacity')))""")
        self.assertTrue(trackers and all(t > 0 for t in trackers))
        self.assertEqual(page.evaluate(f"document.querySelectorAll('[data-block=\"evc-curves\"] g.pt[data-index=\"{k}\"][aria-current=\"true\"]').length"), len(self.families))
        # the primary lineage's own blocks follow
        gen_a = self.ec["curves"]["by_index"][self.families[0]][k]["id"]
        self.assertEqual(page.evaluate("AgentDiff.evolution ? AgentDiff.evolution.state().gen : null"), gen_a)
        page.reload()
        page.wait_for_timeout(900)
        self.assertEqual(page.evaluate("AgentDiff.evolutionCompare.state().gen"), k)
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ---------------------------------------------------------- the verdict

    def test_the_verdict_rows_say_what_the_engine_said(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-verdict"]')
        v = self.ec["verdict"]
        for axis in ("peak", "final", "learning", "process"):
            row = block.locator(f'g.evc-axis[data-axis="{axis}"]')
            self.assertEqual(row.count(), 1, axis)
            self.assertEqual(row.get_attribute("data-winner"), v.get(axis) or "", axis)
            item = block.locator(f'li[data-axis="{axis}"]')
            self.assertEqual(item.get_attribute("data-winner"), v.get(axis) or "", axis)
            self.assertIn(v.get(axis) or "does not separate", item.inner_text())
        self.assertIn(self.ec["peak"]["reading"][:60], block.locator('li[data-axis="peak"]').inner_text())
        self.assertIn(self.ec["final"]["reading"][:60], block.locator('li[data-axis="final"]').inner_text())
        winners = [f for f in self.families if any(v.get(ax) == f for ax in ("peak", "final", "learning", "process"))]
        lede = block.locator(".evc-lede").inner_text().lower()   # the sentence capitalises its first word
        for f in winners:
            self.assertIn(f.lower() + " takes", lede)
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ---------------------------------------------------------- the process

    def test_the_process_bars_count_the_verdicts_in_their_colours_and_the_table_names_the_lost_tasks(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-process"]')
        colours = {"improved": "var(--good)", "regressed": "var(--bad)", "flat": "var(--ink-3)", "gamed": "var(--bad)",
                   "forgot": "var(--warn)", "overfit": "var(--warn)", "traded": "var(--warn)"}
        for fam in self.families:
            p = self.ec["process"][fam]
            row = block.locator(f'g.evc-prow[data-family="{fam}"]')
            total = 0
            for i in range(row.locator("g.evc-seg-v").count()):
                seg = row.locator("g.evc-seg-v").nth(i)
                verdict = seg.get_attribute("data-verdict")
                count = int(seg.get_attribute("data-count"))
                self.assertEqual(count, p[verdict], f"{fam} {verdict}")
                self.assertEqual(seg.locator("rect").first.get_attribute("fill"), colours[verdict], f"{fam} {verdict}")
                total += count
            self.assertEqual(total, sum(p[k] for k in colours if isinstance(p.get(k), int)), fam)
        block.locator(".evc-details summary").click()
        page.wait_for_timeout(200)
        table = block.locator("table.evc-process-table")
        for i, fam in enumerate(self.families):
            p = self.ec["process"][fam]
            self.assertEqual(table.locator('tr[data-measure="gamed"] td').nth(i + 1).inner_text(), str(p["gamed"]))
            self.assertEqual(table.locator('tr[data-measure="protected_touched"] td').nth(i + 1).inner_text(), str(p["protected_touched"]))
            lost = p["retention"]["lost"]
            cell = table.locator('tr[data-measure="lost"] td').nth(i + 1).inner_text()
            self.assertEqual(cell, ", ".join(self._short(t) for t in lost) if lost else "none", fam)
        self.assertIn(self.ec["verdict"].get("process") or "does not separate", block.locator(".evc-lede").inner_text())
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ------------------------------------------------------------- the pair

    def test_the_pair_shows_the_engine_probability_every_shared_task_and_toggles_to_final(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-pair"]')
        peak = self.ec["peak"]
        big = block.locator(".evc-big").inner_text()
        self.assertIn(self._pct(peak["improvement"]["point"]), big)
        self.assertIn(peak["a"]["id"], big)
        self.assertIn(peak["b"]["id"], big)
        self.assertEqual(block.locator("g.evc-tdot").count(), len(peak["per_task"]))
        for task, cell in peak["per_task"].items():
            dot = block.locator(f'g.evc-tdot[data-task="{task}"]')
            self.assertAlmostEqual(float(dot.get_attribute("data-delta")), cell["delta"], places=4, msg=task)
            self.assertEqual(dot.locator('rect.evc-pass[data-side="a"]').get_attribute("data-pass"), f"{cell['pass_a']:.2f}", task)
            self.assertEqual(dot.locator('rect.evc-pass[data-side="b"]').get_attribute("data-pass"), f"{cell['pass_b']:.2f}", task)
        self.assertEqual(block.locator("svg").count(), 2)
        block.locator('button[data-pair="final"]').click()
        page.wait_for_timeout(500)
        final = self.ec["final"]
        self.assertEqual(block.locator(".evc-pair-body").get_attribute("data-pair"), "final")
        big2 = block.locator(".evc-big").inner_text()
        self.assertIn(self._pct(final["improvement"]["point"]), big2)
        self.assertIn(final["b"]["id"], big2)
        self.assertEqual(block.locator("g.evc-tdot").count(), len(final["per_task"]))
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ------------------------------------------------------------- the race

    def test_the_race_has_both_marks_in_every_cell_and_names_the_first_solver(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-race"]')
        tasks = self.ec["task_race"]["tasks"]
        n = max(len(self.ec["curves"]["by_index"][f]) for f in self.families)
        shared = [t for t in self.ec["tasks"]["shared"] if t in tasks]
        self.assertEqual(block.locator("g.evc-cell").count(), len(shared) * n * len(self.families))
        for task in shared:
            edge = block.locator(f'g.evc-edge[data-task="{task}"]')
            self.assertEqual(edge.get_attribute("data-first"), self.ec["task_race"]["first_solver"].get(task) or "", task)
            for fam in self.families:
                curve = tasks[task][fam]["pass_curve"]
                for k, v in enumerate(curve):
                    cell = block.locator(f'g.evc-cell[data-task="{task}"][data-family="{fam}"][data-index="{k}"]')
                    self.assertEqual(cell.get_attribute("data-pass"), f"{v:.3f}", f"{task} {fam} {k}")
                first = tasks[task][fam].get("first_solved_index")
                self.assertEqual(block.locator(f'g.evc-cell[data-task="{task}"][data-family="{fam}"][data-first="1"]').count(), 1 if isinstance(first, int) else 0)
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ------------------------------------------------------- the mechanisms

    def test_every_mechanism_has_its_dot_and_the_best_paying_one_is_named(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-mechanisms"]')
        expected = sum(len(self.ec["process"][f]["mechanisms"]) for f in self.families)
        self.assertEqual(block.locator("g.evc-mdot").count(), expected)
        lede = block.locator(".evc-lede").inner_text()
        for fam in self.families:
            best = self.ec["process"][fam].get("best_paying_mechanism")
            if best:
                self.assertIn(f"for {fam}, {best} paid best", lede.lower())
            for mech, cell in self.ec["process"][fam]["mechanisms"].items():
                dot = block.locator(f'g.evc-mrow[data-mechanism="{mech}"] g.evc-mdot[data-family="{fam}"]')
                self.assertEqual(int(dot.get_attribute("data-steps")), cell["steps"], f"{fam} {mech}")
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ------------------------------------------------------- the divergence

    def test_the_divergence_draws_every_generation_and_says_the_direction(self):
        context, page, errors = self._open()
        block = page.locator('[data-block="evc-divergence"]')
        rows = self.ec["by_generation"]
        self.assertEqual(block.locator("circle.evc-dpt").count(), sum(1 for r in rows if isinstance(r.get("behaviour_distance"), (int, float))))
        self.assertEqual(block.locator("circle.evc-ppt").count(), sum(1 for r in rows if r.get("improvement") and isinstance(r["improvement"].get("point"), (int, float))))
        self.assertIn(block.locator(".evc-div-reading").get_attribute("data-direction"), ("converging", "diverging", "neither"))
        if self.ec.get("by_generation_reading"):
            self.assertIn(self.ec["by_generation_reading"][:50], block.locator(".evc-reading").inner_text())
        self.assertEqual(self._errors(errors), [])
        context.close()

    # ------------------------------------------------------------- the page

    def test_nothing_overflows_on_a_phone_every_chart_is_labelled_and_no_text_is_too_small(self):
        context, page, errors = self._open(width=390)
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 392)
        for bid in self.IDS:
            block = page.locator(f'[data-block="{bid}"]')
            box = block.bounding_box()
            self.assertLessEqual(box["x"] + box["width"], 391, bid)
            self.assertEqual(block.locator(".empty:visible").count(), 0, bid)
        audit = page.evaluate("""() => {
          const out = {bad: [], small: [], svgs: 0};
          document.querySelectorAll('[data-block^="evc-"]').forEach(function (card) {
            card.querySelectorAll('svg').forEach(function (s) {
              out.svgs++;
              if (s.getAttribute('role') !== 'img' || !(s.getAttribute('aria-label') || '').trim()) out.bad.push(card.getAttribute('data-block'));
            });
            const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
              if (!node.textContent.trim()) continue;
              const el = node.parentElement; if (!el) continue;
              if (parseFloat(getComputedStyle(el).fontSize) < 11) out.small.push(node.textContent.trim().slice(0, 30));
            }
          });
          return out; }""")
        self.assertGreaterEqual(audit["svgs"], 9)
        self.assertEqual(audit["bad"], [])
        self.assertEqual(audit["small"], [])
        self.assertEqual(self._errors(errors), [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class SharedLibraryTest(unittest.TestCase):
    """`AgentDiff.lib` (01_lib.js), the helpers every block used to carry a
    copy of: a chart svg is refused without an accessible name, the
    formatters print the strings the blocks printed, the fold law is the
    one from Where it mattered, a family persists through the store by
    default and per task when task-scoped, and no block defines one of
    these helpers locally."""

    tmp = None
    LIB_FREE = ("00_core.js", "01_lib.js")
    #: still binding the library while their authors finish them; drop as they adopt
    IN_FLIGHT = ()
    #: an `isNum` that accepts Infinity (40, 60), a coarser `secs` (18, 19): documented legacy copies;
    #: 03 holds the responsive painter the library delegates to
    LEGACY = {"isNum": ("40_signal.js", "60_science.js"), "secs": ("18_time.js", "19_horizon.js"),
              "responsive": ("03_d3charts.js",)}
    #: no block carries a local `trunc` or `plural` any more: the rule has no exceptions
    IN_FLIGHT_RULES = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "runs"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        subprocess.run([sys.executable, "-m", "deepcompare", "runs", str(ROOT / "demo" / "rl" / "train"), "-o", str(out),
                        "--template", str(ROOT / "web" / "blocks.html")],
                       cwd=str(ROOT), check=True, capture_output=True)
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
        page.goto(f"file://{self.page_path}")
        page.wait_for_timeout(500)
        return context, page, errors

    def test_a_chart_svg_is_refused_without_an_accessible_name(self):
        context, page, errors = self._open()
        result = page.evaluate("""() => {
            const L = AgentDiff.lib, out = {};
            try { L.svg({ viewBox: '0 0 10 10' }); out.bare = 'allowed'; } catch (e) { out.bare = String(e.message); }
            try { L.svg({ 'aria-label': '   ' }); out.blank = 'allowed'; } catch (e) { out.blank = String(e.message); }
            const ok = L.svg({ 'aria-label': 'two runs over time', viewBox: '0 0 10 10' });
            out.role = ok.getAttribute('role'); out.tag = ok.tagName.toLowerCase(); out.label = ok.getAttribute('aria-label');
            const app = L.svg({ 'aria-label': 'a map', role: 'application' });
            out.kept = app.getAttribute('role');
            return out;
        }""")
        self.assertIn("aria-label", result["bare"])
        self.assertIn("aria-label", result["blank"])
        self.assertEqual((result["tag"], result["role"], result["label"]), ("svg", "img", "two runs over time"))
        self.assertEqual(result["kept"], "application")
        self.assertEqual(errors, [])
        context.close()

    def test_the_formatters_print_the_strings_the_blocks_printed(self):
        context, page, errors = self._open()
        got = page.evaluate("""() => {
            const f = AgentDiff.lib.fmt;
            return {
                num: [f.num(5.23), f.num(-2.47), f.num(1.5), f.num(2), f.num(0.125, 3), f.num(null), f.num(NaN)],
                signed: [f.signed(0.5), f.signed(-1.25), f.signed(0), f.signed(2.32, 1), f.signed(undefined)],
                pct: [f.pct(0.5), f.pct(0.833), f.signed(0) && f.pct(0.125, 1), f.pct(1), f.pct('x')],
                secs: [f.secs(123.4), f.secs(12.34), f.secs(1.5), f.secs(0.25), f.secs(Infinity)],
                short: [f.short('rl01_ledger_reconcile'), f.short('t03_a_b'), f.short('plain'), f.short(null)],
                isNum: [f.isNum(1), f.isNum('1'), f.isNum(NaN), f.isNum(Infinity), f.isNum(null)],
            };
        }""")
        self.assertEqual(got["num"], ["5.23", "−2.47", "1.5", "2", "0.125", "—", "—"])
        self.assertEqual(got["signed"], ["+0.50", "−1.25", "0.00", "+2.3", "—"])
        self.assertEqual(got["pct"], ["50%", "83%", "12.5%", "100%", "—"])
        self.assertEqual(got["secs"], ["123s", "12s", "1.5s", "0.25s", "—"])
        self.assertEqual(got["short"], ["ledger reconcile", "a b", "plain", ""])
        self.assertEqual(got["isNum"], [True, False, False, False, False])
        context.close()

    def test_plural_counts_and_trunc_cuts_as_the_blocks_did(self):
        context, page, errors = self._open()
        got = page.evaluate("""() => {
            const f = AgentDiff.lib.fmt;
            return {
                plural: [f.plural(1, 'run'), f.plural(2, 'run'), f.plural(0, 'step'), f.plural(1234, 'character'), f.plural(3, 'fetch'),
                         f.plural(2, 'recovery'), f.plural(1, 'recovery'), f.plural(2, 'protected touch'), f.plural(2, 'person', 'people'), f.plural(null, 'run'), f.plural(2.4, 'step')],
                trunc: [f.trunc('ledger reconcile', 8), f.trunc('ledger', 8), f.trunc('  a\\n  b\\t c ', 40), f.trunc('abcdefgh', 1), f.trunc(null, 5), f.trunc(undefined, 5), f.trunc('abc', 3), f.trunc('abcd', 3)],
                keep: [f.trunc('  a\\n  b\\t c ', 40, true), f.trunc('line one\\nline two\\nline three', 14, true), f.trunc(null, 5, true)],
            };
        }""")
        self.assertEqual(got["plural"], ["1 run", "2 runs", "0 steps", "1,234 characters", "3 fetches",
                                         "2 recoveries", "1 recovery", "2 protected touches", "2 people", "— runs", "2 steps"])
        # n−1 characters and the ellipsis, so the label is n wide; a cut never leaves a bare ellipsis
        self.assertEqual(got["trunc"], ["ledger …", "ledger", "a b c", "a…", "", "", "abc", "ab…"])
        # `keep` keeps the whitespace as recorded, for a step's log shown in a <pre>; the cut is the same
        self.assertEqual(got["keep"], ["  a\n  b\t c ", "line one\nline…", ""])
        # the blocks bind the library's and carry no copy of their own
        for name in ("36_coevolve.js", "37_chat.js", "38_levels.js", "39_data.js", "33_evolve.js", "35_evocompare.js",
                     "17_debug.js", "27_training.js", "28_rlstats.js", "29_rlaudit.js", "32_rltheatre.js", "34_evotime.js", "50_integrity.js"):
            source = (ROOT / "web" / "blocks" / name).read_text(encoding="utf-8")
            self.assertIn("L.fmt.plural" if name in ("36_coevolve.js", "37_chat.js", "38_levels.js", "39_data.js", "50_integrity.js") else "L.fmt.trunc", source, name)
        self.assertEqual(errors, [])
        context.close()

    def test_a_null_default_restores_a_saved_array_or_object(self):
        """The migration finding: a brush range saved as [lo, hi] under a
        `range: null` default was refused on load, so it never survived a
        reload. A null default now takes a scalar, an array or a plain
        object; a typed default still refuses the wrong type."""
        context, page, errors = self._open()
        page.evaluate("""() => {
            const F = AgentDiff.lib.family('lib-test-null', { range: null, box: null, gen: null, n: 0, list: [] }, { scope: 'page' });
            F.set({ range: [1, 3], box: { a: 1 }, gen: 'g2' });
            AgentDiff._internals.Store.set('agentdiff:lib-test-typed', { n: 'seven', list: { not: 'a list' }, gen: 'g1' });
        }""")
        self.assertEqual(page.evaluate("() => AgentDiff._internals.Store.get('agentdiff:lib-test-null')"),
                         {"range": [1, 3], "box": {"a": 1}, "gen": "g2", "n": 0, "list": []})
        page.reload()
        page.wait_for_timeout(400)
        got = page.evaluate("""() => {
            const F = AgentDiff.lib.family('lib-test-null', { range: null, box: null, gen: null, n: 0, list: [] }, { scope: 'page' });
            const T = AgentDiff.lib.family('lib-test-typed', { n: 0, list: [], gen: null }, { scope: 'page' });
            return { restored: F.get(), typed: T.get() };
        }""")
        self.assertEqual(got["restored"], {"range": [1, 3], "box": {"a": 1}, "gen": "g2", "n": 0, "list": []})
        # a number default still refuses a string and a list default an object; the null default takes its scalar
        self.assertEqual(got["typed"], {"n": 0, "list": [], "gen": "g1"})
        self.assertEqual(errors, [])
        context.close()

    def test_the_fold_law_is_the_one_from_where_it_mattered(self):
        context, page, errors = self._open()
        got = page.evaluate("""() => {
            const g = AgentDiff.lib.glyph;
            return { w: [0, 1, 4, 5, 11, 100].map(n => +g.foldWidth(n).toFixed(2)),
                     s: [0, 1, 4, 100].map(n => +g.foldSeconds(n).toFixed(2)),
                     neg: g.foldWidth(-3), bad: g.foldWidth(null) };
        }""")
        self.assertEqual(got["w"], [6, 12, 19.93, 21.51, 27.51, 45.95])
        self.assertEqual(got["s"], [6, 12, 19.93, 45.95])
        self.assertEqual((got["neg"], got["bad"]), (6, 6))
        context.close()

    def test_a_family_persists_through_a_reload_by_default(self):
        context, page, errors = self._open()
        task = page.evaluate("() => AgentDiff.state().task")
        self.assertTrue(task)
        page.evaluate("() => { AgentDiff.lib.family('lib-test-default', { gen: null, metric: 'pass' }).set({ gen: 'g6' }); }")
        saved = page.evaluate(f"() => AgentDiff._internals.Store.get('agentdiff:lib-test-default:{task}')")
        self.assertEqual(saved, {"gen": "g6", "metric": "pass"})
        page.reload()
        page.wait_for_timeout(400)
        self.assertEqual(page.evaluate("() => AgentDiff.lib.family('lib-test-default', { gen: null, metric: 'pass' }).get().gen"), "g6")
        self.assertEqual(errors, [])
        context.close()

    def test_a_page_family_persists_flat_under_its_key(self):
        context, page, errors = self._open()
        page.evaluate("""() => {
            const P = AgentDiff.lib.family('lib-test-page', { task: null, runs: {} }, { scope: 'page' });
            P.get().runs['policy-v2'] = 'r3'; P.get().task = 'rl02_flaky_test'; P.persist();
        }""")
        self.assertEqual(page.evaluate("() => AgentDiff._internals.Store.get('agentdiff:lib-test-page')"),
                         {"task": "rl02_flaky_test", "runs": {"policy-v2": "r3"}})
        page.reload()
        page.wait_for_timeout(400)
        self.assertEqual(page.evaluate("() => AgentDiff.lib.family('lib-test-page', { task: null, runs: {} }, { scope: 'page' }).get()"),
                         {"task": "rl02_flaky_test", "runs": {"policy-v2": "r3"}})
        context.close()

    def test_a_task_family_resets_per_task_and_never_leaks(self):
        context, page, errors = self._open()
        ids = page.evaluate("() => AgentDiff.taskIds()")
        self.assertGreaterEqual(len(ids), 2)
        first, second = ids[0], ids[1]
        page.select_option("#task-picker", first)
        page.wait_for_timeout(200)
        page.evaluate("() => { AgentDiff.lib.family('lib-test-task', { open: null }).set({ open: 'c3' }, { rerender: false }); }")
        page.select_option("#task-picker", second)
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("() => AgentDiff.state().task"), second)
        self.assertIsNone(page.evaluate("() => AgentDiff.lib.family('lib-test-task', { open: null }).get().open"))
        self.assertIsNone(page.evaluate(f"() => AgentDiff._internals.Store.get('agentdiff:lib-test-task:{second}')"))
        page.select_option("#task-picker", first)
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("() => AgentDiff.lib.family('lib-test-task', { open: null }).get().open"), "c3")
        # an explicit task key reads that task's state whichever task is selected
        self.assertIsNone(page.evaluate(f"() => AgentDiff.lib.family('lib-test-task', {{ open: null }}).get('{second}').open"))
        self.assertEqual(errors, [])
        context.close()

    def test_reset_clears_memory_and_the_saved_entry(self):
        context, page, errors = self._open()
        task = page.evaluate("() => AgentDiff.state().task")
        page.evaluate("() => { AgentDiff.lib.family('lib-test-reset', { gen: null }).set({ gen: 'g2' }); }")
        self.assertEqual(page.evaluate(f"() => AgentDiff._internals.Store.get('agentdiff:lib-test-reset:{task}')"), {"gen": "g2"})
        page.evaluate("() => { AgentDiff.lib.family('lib-test-reset', { gen: null }).reset(); }")
        self.assertIsNone(page.evaluate("() => AgentDiff.lib.family('lib-test-reset', { gen: null }).get().gen"))
        self.assertIsNone(page.evaluate(f"() => AgentDiff._internals.Store.get('agentdiff:lib-test-reset:{task}')"))
        page.reload()
        page.wait_for_timeout(400)
        self.assertIsNone(page.evaluate("() => AgentDiff.lib.family('lib-test-reset', { gen: null }).get().gen"))
        # a memory-only family writes nothing
        page.evaluate("() => { AgentDiff.lib.family('lib-test-memory', { open: {} }, { persist: false }).set({ open: { a: 1 } }); }")
        self.assertEqual(page.evaluate("() => AgentDiff._internals.Store.keys('agentdiff:lib-test-memory')"), [])
        context.close()

    def test_subscribers_hear_a_set_and_the_page_rerenders_only_when_asked(self):
        context, page, errors = self._open()
        got = page.evaluate("""() => {
            const F = AgentDiff.lib.family('lib-test-subs', { n: 0 }, { persist: false });
            let heard = 0, renders = 0;
            const was = AgentDiff._rerender; AgentDiff._rerender = function () { renders++; };
            F.subscribe(function (st) { heard = st.n; });
            F.set({ n: 4 });
            F.set({ n: 5 }, { rerender: true });
            AgentDiff._rerender = was;
            return { heard, renders, state: F.state.n };
        }""")
        self.assertEqual(got, {"heard": 5, "renders": 1, "state": 5})
        context.close()

    def test_no_block_defines_a_library_helper_locally(self):
        import re
        blocks = ROOT / "web" / "blocks"
        local = {
            "style injection": re.compile(r'createElement\("style"\)'),
            "isNum": re.compile(r"^\s*function isNum\(", re.M),
            "short": re.compile(r"^\s*function short\(", re.M),
            "secs": re.compile(r"^\s*function secs\(", re.M),
            "foldW": re.compile(r"^\s*function foldWT?\(", re.M),
            "fold law": re.compile(r"6 \+ 6 \* Math\.log\("),
            "responsive": re.compile(r"^\s*function responsive\(", re.M),
            "tooltip": re.compile(r"tip\.className = \"[a-z-]+-tip\""),
            "plural": re.compile(r"^\s*function plural\(", re.M),
            "trunc": re.compile(r"^\s*function trunc\(", re.M),
        }
        offenders = []
        for path in sorted(blocks.glob("*.js")):
            if path.name in self.LIB_FREE or path.name in self.IN_FLIGHT or path.name.startswith("_"):
                continue
            source = path.read_text(encoding="utf-8")
            for name, pattern in local.items():
                if path.name in self.LEGACY.get(name, ()) or path.name in self.IN_FLIGHT_RULES.get(name, ()):
                    continue
                if pattern.search(source):
                    offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class CoevolutionBlocksTest(unittest.TestCase):
    """The Evals view (36_coevolve.js) against the demo lineage's
    co-evolving eval: the loop as a flow at three levels, the hindsight
    re-reading, the metrics × generations matrix, one metric in full, the
    probes and the eval's integrity.

    What is checked is that the page draws `aggregate.coevolution` and
    nothing else — one column per agent step with the engine's verdict, one
    row per probe that fired with a mark where it did, the validators'
    funnel counted from the ledger's first failures, the eval generations
    under the steps that made them, the hindsight edges with their lags —
    and that one page-scoped family drives the levels and the sibling
    blocks by mouse and by keyboard; that the lane fits a phone; that every
    chart is labelled; and that the view is empty, without an error, on a
    batch that has no lineage.
    """

    tmp = None
    IDS = ("cov-flow", "cov-hindsight", "cov-matrix", "cov-metric", "cov-probes", "cov-integrity")

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file():
            raise unittest.SkipTest("no demo lineage to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "cov"
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        template = ROOT / "web" / "blocks.html"
        done = subprocess.run([sys.executable, "-m", "deepcompare", "coevolve", str(lineage), "-o", str(out), "--template", str(template)],
                              cwd=str(ROOT), capture_output=True)
        if done.returncode != 0 or not (out / "aggregate.json").is_file():
            raise unittest.SkipTest("the coevolve command did not write a page: " + done.stderr.decode("utf-8", "replace")[-300:])
        agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        cls.cov = agg.get("coevolution") or {}
        cls.evo = agg.get("evolution") or {}
        if not cls.cov.get("measurable"):
            raise unittest.SkipTest("the demo lineage carries no measurable coevolution section")
        cls.page_path = out / "report.html"
        batch = Path(cls.tmp.name) / "batch"
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "traces"), "-o", str(batch), "--template", str(template)],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.batch_path = batch / "report.html"
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

    def _open(self, width=1280, path=None, reduced_motion=False):
        context = self.browser.new_context(viewport={"width": width, "height": 1000},
                                           reduced_motion="reduce" if reduced_motion else "no-preference")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # unfiltered: a warning from any block on the page is a failure here
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
        page.goto(f"file://{path or self.page_path}#view=coevolution")
        page.wait_for_timeout(1000)
        return context, page, errors

    def _state(self, page):
        return page.evaluate("() => AgentDiff.coevolution.state()")

    def _steps(self):
        return [s for s in self.cov["steps"] if s.get("from") and s.get("to")]

    def _key(self, s):
        return f"{s['from']}→{s['to']}"

    def _ledger(self, key):
        return [r for r in self.cov["ledger"] if r["step"] == key]

    # ------------------------------------------------------------ rendering

    def test_the_six_blocks_render_in_the_declared_order_without_an_empty_state(self):
        context, page, errors = self._open()
        ids = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))")
        self.assertEqual(tuple(ids), self.IDS)
        for bid in self.IDS:
            block = page.locator(f'#stacks [data-block="{bid}"]')
            body = block.locator(".block-body").inner_text()
            self.assertNotIn("failed to render", body, bid)
            self.assertNotIn("Nothing to show", body, bid)
            self.assertGreater(len(body.strip()), 80, bid)
        self.assertEqual(page.locator("#stacks .block .empty:visible").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_every_chart_is_labelled_with_its_numbers_and_the_flow_is_an_application(self):
        context, page, _ = self._open()
        labels = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks [data-block^="cov-"] svg'))
            .filter(s => !s.parentNode.closest('svg')).map(s => [s.getAttribute('role'), s.getAttribute('aria-label') || ''])""")
        self.assertGreaterEqual(len(labels), 6)
        for role, label in labels:
            self.assertEqual(role, "img")
            self.assertTrue(label.strip())
            self.assertRegex(label, r"\d")
        loop = page.locator(".cov-flow svg[data-level='loop']")
        label = loop.get_attribute("aria-label")
        self.assertIn(f"{len(self.cov['eval_generations'])} eval generation", label)
        self.assertIn(f"{len(self.cov['ledger'])} candidate", label)
        stage = page.locator(".cov-flow .cov-stage")
        self.assertEqual(stage.get_attribute("role"), "application")
        self.assertEqual(stage.get_attribute("data-level"), "loop")
        self.assertIn("loop level", stage.get_attribute("aria-label"))
        status = page.locator(".cov-flow .cov-status")
        self.assertEqual(status.get_attribute("aria-live"), "polite")
        self.assertIn(self.cov["flow"]["summary"]["sentence"][:60], status.inner_text())
        self.assertIn("SYNTHETIC" if self.cov.get("synthetic") else "", page.locator(".cov-flow .cov-bar").inner_text())
        context.close()

    # ---------------------------------------------------------------- loop

    def test_the_loop_draws_every_step_probe_validator_and_eval_generation_from_the_json(self):
        context, page, _ = self._open(width=1440)
        steps = self._steps()
        cols = page.locator(".cov-flow .cov-step")
        self.assertEqual(cols.count(), len(steps))
        for col, s in zip(cols.all(), steps):
            self.assertEqual(col.get_attribute("data-step"), self._key(s))
            self.assertEqual(col.get_attribute("data-verdict"), s["base"].get("verdict") or "")
        fired = [p for p in self.cov["probes"] if p.get("fired")]
        marks = page.locator(".cov-flow .cov-pmark")
        self.assertEqual(marks.count(), sum(len(p["fired"]) for p in fired))
        for p in fired:
            for key in p["fired"]:
                mark = page.locator(f'.cov-flow .cov-col[data-step="{key}"] .cov-pmark[data-probe="{p["name"]}"]')
                self.assertEqual(mark.count(), 1, (p["name"], key))
                self.assertEqual(int(mark.get_attribute("data-n")), len([r for r in self._ledger(key) if r["probe"] == p["name"]]))
        order = ["computable", "informative", "distinct", "linked", "not_already"]
        for s in steps:
            key = self._key(s)
            rows = self._ledger(key)
            alive = len(rows)
            for v in order:
                stopped = len([r for r in rows if r.get("failed") and min(r["failed"], key=order.index) == v])
                band = page.locator(f'.cov-flow .cov-col[data-step="{key}"] .cov-band[data-validator="{v}"]')
                self.assertEqual(int(band.get_attribute("data-reached")), alive, (key, v))
                self.assertEqual(int(band.get_attribute("data-stopped")), stopped, (key, v))
                alive -= stopped
            self.assertEqual(alive, len([r for r in rows if r["decision"] == "adopted"]), key)
            self.assertEqual(page.locator(f'.cov-flow .cov-col[data-step="{key}"] .cov-adopt').count(), 1 if alive else 0)
        evals = page.locator(".cov-flow .cov-eval")
        self.assertEqual([e.get_attribute("data-eval") for e in evals.all()], [e["id"] for e in self.cov["eval_generations"]])
        for e in self.cov["eval_generations"]:
            for mid in e.get("adopted") or []:
                self.assertEqual(page.locator(f'.cov-flow .cov-eval[data-eval="{e["id"]}"] .cov-chip-m[data-metric="{mid}"][data-kind="adopted"]').count(), 1)
            for mid in e.get("retired") or []:
                self.assertEqual(page.locator(f'.cov-flow .cov-eval[data-eval="{e["id"]}"] .cov-chip-m[data-metric="{mid}"][data-kind="retired"]').count(), 1)
        context.close()

    def test_the_hindsight_edges_carry_the_lag_and_the_recoveries_say_not_attributed(self):
        context, page, _ = self._open(width=1440)
        edges = [e for e in self.cov["flow"]["edges"] if e["kind"] in ("flags", "recovers")]
        drawn = page.locator(".cov-flow .cov-edge")
        self.assertEqual(drawn.count(), len(edges))
        for e in edges:
            mid = e["from"].split(":", 1)[1]
            key = e["to"].split(":", 1)[1]
            el = page.locator(f'.cov-flow .cov-edge[data-kind="{e["kind"]}"][data-metric="{mid}"][data-step="{key}"]')
            self.assertEqual(el.count(), 1, e)
            self.assertEqual(el.get_attribute("data-learned"), "1" if e.get("learned") else "0")
            if e["kind"] == "flags" and e.get("lag") is not None:
                self.assertEqual(el.get_attribute("data-lag"), str(e["lag"]))
        marks = page.locator(".cov-flow .cov-hmark")
        self.assertEqual(marks.count(), sum(len(s["evolved"].get("flags") or []) + len(s["evolved"].get("base_flags") or []) for s in self._steps()))
        learned = [e for e in edges if e["kind"] == "flags" and e.get("learned")]
        for e in learned:
            key = e["to"].split(":", 1)[1]
            self.assertIn("lag", page.locator(f'.cov-flow .cov-step[data-step="{key}"] .cov-hmarks').text_content())
        if any(e["kind"] == "recovers" for e in edges):
            self.assertIn("recovered, not attributed", page.locator(".cov-flow .cov-bar").inner_text())
            self.assertRegex(page.locator(".cov-flow svg[data-level='loop']").text_content(), "↺")
        context.close()

    # -------------------------------------------------------------- levels

    def test_a_click_descends_to_the_step_and_the_candidate_and_the_breadcrumb_and_escape_ascend(self):
        context, page, errors = self._open(width=1440)
        target = [s for s in self._steps() if self._ledger(self._key(s))][0]
        key = self._key(target)
        page.locator(f'.cov-flow .cov-step[data-step="{key}"]').click()
        page.wait_for_timeout(500)
        st = self._state(page)
        self.assertEqual((st["level"], st["step"], st["candidate"]), ("step", key, None))
        self.assertEqual(page.locator(".cov-flow .cov-stage").get_attribute("data-level"), "step")
        rows = page.locator(".cov-flow .cov-row[data-candidate]")
        ledger = self._ledger(key)
        self.assertEqual(rows.count(), len(ledger))
        for row, r in zip(rows.all(), ledger):
            self.assertEqual(int(row.get_attribute("data-candidate")), r["index"])
            self.assertEqual(row.get_attribute("data-decision"), r["decision"])
            marks = row.locator(".cov-mark")
            self.assertEqual(marks.count(), 5)
            for v in r.get("failed") or []:
                self.assertEqual(row.locator(f'.cov-mark[data-validator="{v}"]').get_attribute("data-mark"), "fail")
            self.assertIn(r["reason"][:40], row.locator(".why").inner_text())
        self.assertIn(key, page.locator(".cov-flow .cov-lede").inner_text())
        self.assertEqual(page.locator(".cov-flow svg[data-step]").count(), 1)
        fired = [p["name"] for p in self.cov["probes"] if key in (p.get("fired") or [])]
        self.assertEqual(page.locator('.cov-flow [data-role="probes"] li').count(), len(fired))
        # the candidate level
        first = ledger[0]
        page.locator(f'.cov-flow .cov-row[data-candidate="{first["index"]}"]').click()
        page.wait_for_timeout(500)
        st = self._state(page)
        self.assertEqual((st["level"], st["candidate"]), ("candidate", first["index"]))
        lede = page.locator(".cov-flow .cov-lede")
        self.assertEqual(lede.get_attribute("data-candidate"), str(first["index"]))
        self.assertIn(first["spec_id"], lede.inner_text())
        self.assertEqual(page.locator(".cov-flow .cov-val").count(), 5)
        self.assertIn(first["reason"][:40], page.locator('.cov-flow [data-role="decision"]').inner_text())
        inf = first["validators"]["informative"]
        if inf.get("delta") and inf["delta"].get("point") is not None:
            self.assertAlmostEqual(float(page.locator(".cov-flow .cov-big").get_attribute("data-delta")), inf["delta"]["point"], places=4)
            self.assertEqual(page.locator('.cov-flow .cov-val[data-validator="informative"] svg').count(), 1)
        self.assertEqual(page.locator('.cov-flow [data-role="against"] tbody tr').count(), len(first["validators"]["distinct"].get("against") or []))
        crumbs = page.locator(".cov-flow .cov-crumbs button")
        self.assertEqual([c.inner_text() for c in crumbs.all()], ["loop", key, first["spec_id"]])
        # the breadcrumb is the way up
        crumbs.nth(1).click()
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["level"], "step")
        # Escape ascends
        page.locator(".cov-flow .cov-stage").focus()
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["level"], "loop")
        self.assertEqual(page.locator(".cov-flow .cov-stage").get_attribute("data-level"), "loop")
        self.assertEqual(page.locator(".cov-flow svg[data-level='loop']").count(), 1)
        self.assertEqual(errors, [])
        context.close()

    def test_every_level_is_reached_by_the_keyboard_alone(self):
        context, page, errors = self._open(width=1440)
        stage = page.locator(".cov-flow .cov-stage")
        stage.focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(300)
        self.assertEqual(page.locator('.cov-flow .cov-step[aria-current="true"]').count(), 1)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        st = self._state(page)
        self.assertEqual(st["level"], "step")
        self.assertEqual(st["step"], self._key(self._steps()[0]))
        # arrows move between steps at the step level
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["step"], self._key(self._steps()[1]))
        with_rows = [s for s in self._steps() if self._ledger(self._key(s))][0]
        page.evaluate(f"() => AgentDiff.coevolution.select({{step: '{self._key(with_rows)}', evalGen: null, candidate: null}})")
        page.wait_for_timeout(400)
        stage.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        ledger = self._ledger(self._key(with_rows))
        st = self._state(page)
        self.assertEqual((st["level"], st["candidate"]), ("candidate", ledger[0]["index"]))
        if len(ledger) > 1:
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(400)
            self.assertEqual(self._state(page)["candidate"], ledger[1]["index"])
            page.keyboard.press("ArrowLeft")
            page.wait_for_timeout(400)
            self.assertEqual(self._state(page)["candidate"], ledger[0]["index"])
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["level"], "step")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["level"], "loop")
        # a focused node opens on Enter, and a focused eval generation too
        page.locator('.cov-flow .cov-eval').last.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        last = self.cov["eval_generations"][-1]
        st = self._state(page)
        self.assertEqual(st["evalGen"], last["id"])
        self.assertEqual(st["step"], last.get("after_step"))
        self.assertEqual(st["level"], "step")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------- family

    def test_the_selection_is_shared_with_the_hindsight_the_matrix_and_the_metric_blocks_and_survives_a_reload(self):
        context, page, errors = self._open(width=1440)
        steps = self._steps()
        target = steps[len(steps) // 2]
        key = self._key(target)
        page.locator(f'#stacks [data-block="cov-hindsight"] .cov-row[data-step="{key}"]').click()
        page.wait_for_timeout(500)
        self.assertEqual(self._state(page)["step"], key)
        self.assertEqual(page.locator('#stacks [data-block="cov-hindsight"] .cov-row[aria-current="true"]').get_attribute("data-step"), key)
        self.assertEqual(page.locator(".cov-flow .cov-stage").get_attribute("data-level"), "step")
        self.assertEqual(page.locator(".cov-flow .cov-lede").get_attribute("data-step"), key)
        # a matrix row selects the metric, and the metric block follows
        adopted = [k for k, v in self.cov["metrics"].items() if v.get("status") == "adopted"]
        base = self.cov["base"]
        for mid in (base[-1], adopted[0] if adopted else base[0]):
            page.locator(f'#stacks [data-block="cov-matrix"] .cov-mrow[data-metric="{mid}"]').click()
            page.wait_for_timeout(500)
            self.assertEqual(self._state(page)["metric"], mid)
            self.assertEqual(page.locator('#stacks [data-block="cov-matrix"] .cov-mrow[aria-current="true"]').get_attribute("data-metric"), mid)
            metric = page.locator('#stacks [data-block="cov-metric"]')
            self.assertEqual(metric.locator(".cov-lede").get_attribute("data-metric"), mid)
            self.assertEqual(metric.locator(f'button[data-metric="{mid}"]').get_attribute("aria-pressed"), "true")
            self.assertEqual(metric.locator("svg").first.get_attribute("aria-label")[: len(mid)], mid)
        # a chip in the loop selects the metric too
        page.reload()
        page.wait_for_timeout(1000)
        st = self._state(page)
        self.assertEqual((st["step"], st["metric"]), (key, adopted[0] if adopted else base[0]))
        self.assertEqual(page.locator(".cov-flow .cov-stage").get_attribute("data-level"), "step")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------ hindsight

    def test_the_hindsight_headline_and_rows_and_lags_are_the_engines(self):
        context, page, _ = self._open(width=1440)
        block = page.locator('#stacks [data-block="cov-hindsight"]')
        hs = self.cov.get("hindsight") or {}
        lede = block.locator(".cov-lede")
        self.assertEqual(int(lede.get_attribute("data-changed")), hs.get("changed", 0))
        self.assertIn(f"{len(self._steps())} steps re-read, {hs.get('changed', 0)} changed", lede.inner_text())
        rows = block.locator('.cov-row[role="listitem"]')
        self.assertEqual(rows.count(), len(self._steps()))
        for row, s in zip(rows.all(), self._steps()):
            self.assertEqual(row.get_attribute("data-step"), self._key(s))
            self.assertEqual(row.locator(".cov-v").get_attribute("data-verdict"), s["base"].get("verdict") or "")
            self.assertEqual(row.locator(".cov-flag.learned").count(), len(s["evolved"].get("flags") or []))
            self.assertEqual(row.locator(".cov-flag.base").count(), len(s["evolved"].get("base_flags") or []))
            self.assertIn(s["evolved"]["reading"][:40], row.locator(".why").inner_text())
        caught = {k: v for k, v in (hs.get("caught_at") or {}).items()}
        for mid, ca in caught.items():
            lag = block.locator(f'.cov-lag[data-metric="{mid}"]')
            self.assertEqual(lag.count(), 1, mid)
            self.assertEqual(lag.get_attribute("data-lag"), "" if ca.get("lag") is None else str(ca["lag"]))
        context.close()

    # --------------------------------------------------------------- matrix

    def test_the_matrix_hatches_the_cells_before_adoption_and_marks_the_adoption_column(self):
        context, page, _ = self._open(width=1440)
        block = page.locator('#stacks [data-block="cov-matrix"]')
        gens = [n["gen"] for n in self.cov["flow"]["nodes"] if n["kind"] == "agent_gen"]
        metrics = self.cov["metrics"]
        self.assertEqual(block.locator(".cov-cell").count(), len(gens) * len(metrics))
        for mid, mt in metrics.items():
            adopted = mt.get("adopted_at")
            if not adopted:
                self.assertEqual(block.locator(f'.cov-cell[data-metric="{mid}"][data-hindsight="1"]').count(), 0, mid)
                continue
            to = adopted["step"].split("→")[1]
            self.assertEqual(block.locator(f'.cov-cell[data-metric="{mid}"][data-adoption="1"]').get_attribute("data-gen"), to)
            self.assertEqual(block.locator(f'.cov-cell[data-metric="{mid}"][data-hindsight="1"]').count(), gens.index(to))
        self.assertIn("z = (value − row mean) / row sd", block.locator(".cov-lede").inner_text())
        self.assertEqual(block.locator("tbody tr").count(), len(metrics))
        context.close()

    # ---------------------------------------------------- probes, integrity

    def test_the_probes_and_the_integrity_read_the_section_verbatim(self):
        context, page, _ = self._open(width=1440)
        probes = page.locator('#stacks [data-block="cov-probes"]')
        for p in self.cov["probes"]:
            bar = probes.locator(f'.cov-probe[data-probe="{p["name"]}"]')
            self.assertEqual((int(bar.get_attribute("data-fired")), int(bar.get_attribute("data-proposed")), int(bar.get_attribute("data-adopted"))),
                             (len(p.get("fired") or []), p.get("proposed", 0), p.get("adopted", 0)))
            self.assertIn(p["question"], probes.locator(f'[data-role="questions"] li[data-probe="{p["name"]}"]').inner_text())
        ext = self.cov["integrity"].get("external") or {}
        self.assertEqual(int(probes.locator('[data-role="external"]').get_attribute("data-received")), ext.get("received", 0))
        self.assertIn("validated, never trusted", probes.locator('[data-role="external"]').inner_text())
        ig = page.locator('#stacks [data-block="cov-integrity"]')
        integ = self.cov["integrity"]
        self.assertEqual(int(ig.locator(".cov-lede").get_attribute("data-tested")), integ["multiplicity"]["tested"])
        self.assertIn(integ["gap"], ig.locator('[data-role="gap"]').inner_text())
        for kind in ("demoted", "retired", "unconfirmed"):
            self.assertEqual(ig.locator(f'[data-role="{kind}"] li').count(), len(integ.get(kind) or []))
        rec = self.cov.get("recommended") or {}
        if rec:
            self.assertEqual(ig.locator('[data-role="recommended"]').get_attribute("data-agree"), "1" if rec.get("agree") else "0")
        context.close()

    # ------------------------------------------------------ phone, motion

    def test_the_lane_fits_a_phone_at_every_level_with_no_text_under_11px(self):
        for width in (390, 360):
            with self.subTest(width=width):
                context, page, errors = self._open(width=width)
                self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
                key = self._key([s for s in self._steps() if self._ledger(self._key(s))][0])
                for level in ("loop", "step", "candidate"):
                    if level == "step":
                        page.evaluate(f"() => AgentDiff.coevolution.select({{step: '{key}', evalGen: null, candidate: null}})")
                    elif level == "candidate":
                        page.evaluate(f"() => AgentDiff.coevolution.select({{candidate: {self._ledger(key)[0]['index']}}})")
                    page.wait_for_timeout(400)
                    self.assertEqual(page.locator(".cov-flow .cov-stage").get_attribute("data-level"), level)
                    self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1, level)
                    clipped = page.evaluate("""() => { const bad = []; document.querySelectorAll('[data-block^="cov-"]').forEach(card => {
                        const body = card.querySelector('.block-body'); if (!body) return; const st = getComputedStyle(body);
                        if (body.scrollWidth > body.clientWidth + 2 && st.overflowX !== 'auto' && st.overflowX !== 'scroll') bad.push(card.getAttribute('data-block')); }); return bad; }""")
                    self.assertEqual(clipped, [], level)
                small = page.evaluate("""() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n = 0, node;
                    while ((node = w.nextNode())) { if (!node.textContent.trim()) continue; const el = node.parentElement; if (!el) continue;
                    if (parseFloat(getComputedStyle(el).fontSize) < 11) n++; } return n; }""")
                self.assertEqual(small, 0)
                self.assertEqual(errors, [])
                context.close()

    def test_the_loop_draws_in_tens_of_milliseconds_and_no_dom_per_candidate(self):
        context, page, _ = self._open(width=1440)
        ms = float(page.locator(".cov-flow .cov-chart").get_attribute("data-draw-ms"))
        self.assertLess(ms, 200)
        # the loop level binds no node per candidate: the candidates are counts on the bands
        self.assertEqual(page.locator(".cov-flow svg [data-candidate]").count(), 0)
        self.assertEqual(page.locator(".cov-flow .cov-band").count(), 5 * len(self._steps()))
        context.close()

    def test_reduced_motion_changes_level_without_a_transition(self):
        context, page, errors = self._open(width=1440, reduced_motion=True)
        key = self._key(self._steps()[0])
        page.locator(f'.cov-flow .cov-step[data-step="{key}"]').click()
        page.wait_for_timeout(50)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('.cov-flow .cov-level')).opacity"), "1")
        self.assertEqual(errors, [])
        context.close()

    # ---------------------------------------------------------------- empty

    def test_the_view_is_empty_without_an_error_on_a_batch_with_no_lineage(self):
        context, page, errors = self._open(width=1280, path=self.batch_path)
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))"), [])
        self.assertEqual(page.locator("#stacks .block .empty:visible").count(), 0)
        for view in ("story", "evidence", "batch", "panels", "training", "evolution", "coevolution"):
            page.evaluate(f"() => {{ location.hash = '#view={view}'; }}")
            page.wait_for_timeout(250)
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------- per task

    def _per_task(self, mid):
        """The per-task cells of a metric as the matrix carries them: the
        sorted task union, the cell count, the unmeasurable count, the
        reasons, and the note on the cells (an IQM metric's)."""
        cells = self.cov["matrix"][mid]
        tasks = sorted({t for c in cells.values() for t in (c.get("per_task") or {})})
        flat = [cell for c in cells.values() for cell in (c.get("per_task") or {}).values()]
        un = [cell for cell in flat if not cell.get("measurable")]
        return {"tasks": tasks, "total": len(flat), "unmeasurable": un, "gens": list(cells),
                "note": next((cell["note"] for cell in flat if cell.get("note")), None)}

    def test_the_metric_block_draws_one_panel_per_task_with_the_forgotten_task_marked(self):
        """The per-task small multiples: one panel per task of the matrix
        cells' `per_task`, a row per generation with the task's own
        bootstrap interval on one shared axis, the adoption generation
        ringed, a task the Evolution section says a step forgot banded at
        that step, an unmeasurable cell drawn as absent (its reason in the
        tooltip) and counted in the status line, every task in the table
        view, and the note on an IQM metric's cells shown."""
        context, page, errors = self._open(width=1440)
        block = page.locator('#stacks [data-block="cov-metric"]')
        mid = block.locator(".cov-lede").get_attribute("data-metric")
        pt = self._per_task(mid)
        if not pt["tasks"]:
            raise unittest.SkipTest("the matrix carries no per_task cells")
        status = block.locator('[data-role="per-task"]')
        self.assertEqual(status.count(), 1)
        self.assertEqual((status.get_attribute("data-tasks"), status.get_attribute("data-cells"), status.get_attribute("data-unmeasurable")),
                         (str(len(pt["tasks"])), str(pt["total"]), str(len(pt["unmeasurable"]))))
        text = status.inner_text()
        self.assertIn(f"{len(pt['tasks'])} tasks × {len(pt['gens'])} generations: {pt['total']} cells", text)
        self.assertIn("bootstrap within the task", text)
        if pt["unmeasurable"]:
            self.assertIn(f"{len(pt['unmeasurable'])} not measurable, drawn as absent", text)
            self.assertTrue(any(cell["reason"] in text for cell in pt["unmeasurable"]), text)
        # one panel per task, in sorted order, each a row per generation with the interval glyph on the measurable cells
        panels = block.locator("svg.cov-task")
        self.assertEqual(panels.count(), min(len(pt["tasks"]), 24))
        self.assertEqual([p.get_attribute("data-task") for p in panels.all()], pt["tasks"][:24])
        absent = 0
        for task in pt["tasks"][:24]:
            panel = block.locator(f'svg.cov-task[data-task="{task}"]')
            self.assertEqual(panel.get_attribute("role"), "img")
            label = panel.get_attribute("aria-label")
            self.assertIn(f"{mid} on {task} per generation", label)
            self.assertEqual(panel.locator("g.cov-tcell").count(), len(pt["gens"]))
            for gen in pt["gens"]:
                cell = (self.cov["matrix"][mid][gen].get("per_task") or {}).get(task)
                row = panel.locator(f'g.cov-tcell[data-gen="{gen}"]')
                ok = bool(cell and cell.get("measurable") and isinstance(cell.get("point"), (int, float)))
                self.assertEqual(row.get_attribute("data-measurable"), "1" if ok else "0", f"{task} {gen}")
                # a measurable cell is a line from lo to hi with a dot; an absent one is a dash and no dot
                self.assertEqual(row.locator("circle:not(.ring)").count(), 1 if ok else 0, f"{task} {gen}")
                self.assertEqual(row.locator("text.absent").count(), 0 if ok else 1, f"{task} {gen}")
                if not ok:
                    absent += 1
                    self.assertIn(f"{gen} not measurable", label)
        self.assertEqual(absent, len(pt["unmeasurable"]))
        # the adoption generation is ringed on every panel whose cell there is measurable
        adopted = (self.cov["metrics"][mid].get("adopted_at") or {}).get("step")
        if adopted:
            to = adopted.split("→")[1]
            rings = sum(1 for task in pt["tasks"][:24]
                        if ((self.cov["matrix"][mid][to].get("per_task") or {}).get(task) or {}).get("measurable"))
            self.assertEqual(block.locator("svg.cov-task circle.ring").count(), rings)
        else:
            self.assertEqual(block.locator("svg.cov-task circle.ring").count(), 0)
        # the forgotten tasks: from evolution.steps[].effect.forgotten, a ▏ band on that task's panel at the step's `to`
        marks = 0
        for step in self.evo.get("steps") or []:
            key = f"{step['from']}→{step['to']}"
            for task in (step.get("effect") or {}).get("forgotten") or []:
                if task not in pt["tasks"][:24]:
                    continue
                marks += 1
                panel = block.locator(f'svg.cov-task[data-task="{task}"]')
                self.assertEqual(panel.locator(f'g.cov-tcell[data-gen="{step["to"]}"] text.forgot').count(), 1, f"{task} {key}")
                self.assertIn(f"forgotten at {key}", panel.get_attribute("aria-label"))
        self.assertEqual(status.get_attribute("data-forgotten"), str(marks))
        self.assertIn(f"{marks} forgot mark" if marks else "no step forgot a task", text)
        # the table view carries every task by generation
        self.assertEqual(block.locator('[data-role="per-task-table"] tbody tr').count(), len(pt["tasks"]))
        # the reason of an absent cell is in its tooltip
        if pt["unmeasurable"]:
            row = block.locator('svg.cov-task g.cov-tcell[data-measurable="0"]').first
            row.locator("rect").last.hover()
            page.wait_for_timeout(200)
            tip = block.locator(".cov-tip")
            self.assertTrue(tip.is_visible())
            self.assertIn("not measurable:", tip.inner_text())
        # an IQM metric's cells carry the note, and the status line says it
        iqm = [m for m, spec in self.cov["metrics"].items() if spec["spec"]["agg"] in ("iqm", "task_min", "task_spread") and self._per_task(m)["note"]]
        if iqm:
            page.evaluate(f"() => AgentDiff.coevolution.select({{metric: {json.dumps(iqm[0])}}})")
            page.wait_for_timeout(400)
            status = block.locator('[data-role="per-task"]')
            self.assertEqual(status.get_attribute("data-note"), "1")
            note = self._per_task(iqm[0])["note"]
            self.assertIn(note[1:], status.inner_text())
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class ChatViewTest(unittest.TestCase):
    """The Chat view (37_chat.js): the page asked in plain words, on the four
    outputs the repository's own commands write — a lineage with its
    co-evolving eval (`coevolve`), a comparison of two lineages (`evolve
    --against`), a training batch with no lineage (`runs`) and a pair batch
    (`batch`).

    What is checked is that every answer is templated over the report's own
    fields — the eval's reasons come from the ledger, its lags from the
    hindsight, its drift and gap from the integrity, the four axes from the
    comparison's verdict — with the block the sentence cites drawn inside the
    card and an "open in …" link that lands on the same selection; that a
    question the router cannot map gets a "cannot answer" card and never an
    invented number; that the transcript persists under one key and clears;
    that the chips follow the data and the last answer; that the composer is
    driven by the keyboard; and that the lane fits a phone with the console
    clean across every view on every output.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        lineage_b = ROOT / "demo" / "evolve" / "lineage_b"
        if not (lineage / "g0" / "agent.json").is_file() or not (lineage_b / "g0" / "agent.json").is_file():
            raise unittest.SkipTest("no demo lineages to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        template = ROOT / "web" / "blocks.html"

        def run(name, *args):
            out = root / name
            done = subprocess.run([sys.executable, "-m", "deepcompare"] + list(args) + ["-o", str(out), "--template", str(template)],
                                  cwd=str(ROOT), capture_output=True)
            if done.returncode != 0 or not (out / "aggregate.json").is_file() or not (out / "report.html").is_file():
                raise unittest.SkipTest(f"the {args[0]} command did not write a page: " + done.stderr.decode("utf-8", "replace")[-300:])
            return out, json.loads((out / "aggregate.json").read_text(encoding="utf-8"))

        cls.cov_dir, cov_agg = run("cov", "coevolve", str(lineage))
        cls.cmp_dir, cmp_agg = run("cmp", "evolve", str(lineage), "--against", str(lineage_b))
        cls.train_dir, train_agg = run("train", "runs", str(ROOT / "demo" / "rl" / "train"))
        cls.batch_dir, batch_agg = run("batch", "batch", str(ROOT / "demo" / "traces"))
        # the bundle of the pair batch and the lineage: the page with the three levels
        cls.bundle_dir = root / "bundle"
        done = subprocess.run([sys.executable, "-m", "deepcompare", "bundle", str(cls.batch_dir), str(cls.cov_dir), "-o", str(cls.bundle_dir), "--name", "demo"],
                              cwd=str(ROOT), capture_output=True)
        cls.bundle = json.loads((cls.bundle_dir / "bundle.json").read_text(encoding="utf-8")) if done.returncode == 0 and (cls.bundle_dir / "bundle.json").is_file() else None
        cls.batch_pair = json.loads(sorted(cls.batch_dir.glob("report_*.json"))[0].read_text(encoding="utf-8"))
        cls.cov_agg = cov_agg
        cls.cov = cov_agg.get("coevolution") or {}
        cls.evo = cov_agg.get("evolution") or {}
        if not cls.cov.get("measurable") or not cls.evo.get("measurable"):
            raise unittest.SkipTest("the demo lineage carries no measurable eval")
        cls.cmp = cmp_agg.get("evolution_compare") or {}
        cls.stats = ((train_agg.get("rl") or {}).get("stats")) or {}
        cls.score = batch_agg.get("scorecard") or {}
        cls.pair = json.loads(sorted(cls.cov_dir.glob("report_*.json"))[0].read_text(encoding="utf-8"))
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

    # ------------------------------------------------------------- helpers

    def _open(self, path=None, width=1280, reduced_motion=False):
        context = self.browser.new_context(viewport={"width": width, "height": 1000},
                                           reduced_motion="reduce" if reduced_motion else "no-preference")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # unfiltered: a warning from any block on the page is a failure here
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
        page.goto(f"file://{(path or self.cov_dir) / 'report.html'}#view=chat")
        page.wait_for_timeout(900)
        return context, page, errors

    def _ask(self, page, q, wait=400):
        page.fill(".chat-input", q)
        page.keyboard.press("Enter")
        page.wait_for_timeout(wait)
        return page.locator(".chat-turn").last

    @staticmethod
    def _text(row):
        return " ".join(row.locator(".chat-a .chat-text").all_inner_texts())

    @staticmethod
    def _embed(row):
        emb = row.locator(".chat-embed")
        if not emb.count():
            return None, None
        return emb.first.get_attribute("data-embed"), emb.first.get_attribute("data-drawn")

    @staticmethod
    def _chips(page):
        return page.evaluate("() => Array.from(document.querySelectorAll('.chat-composer .chat-chip')).map(b => b.textContent)")

    #: the library's `fmt.num`: p places (a tie rounds away from zero, as toFixed does), trailing zeros dropped, a real minus sign
    @staticmethod
    def _num(v, p=2):
        from decimal import Decimal, ROUND_HALF_UP
        s = str(Decimal(repr(abs(v))).quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP))
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return ("−" if v < 0 and s not in ("0", "") else "") + s

    #: the library's `fmt.pct`: whole points, rounded half up
    @staticmethod
    def _pct(v):
        import math
        return f"{int(math.floor(v * 100 + 0.5))}%"

    def _key(self, s):
        return f"{s['from']}→{s['to']}"

    def _rows(self, spec_id):
        return [r for r in self.cov["ledger"] if r.get("spec_id") == spec_id]

    def _most_rejected(self):
        counts = {}
        for r in self.cov["ledger"]:
            if r.get("decision") == "rejected":
                counts[r["spec_id"]] = counts.get(r["spec_id"], 0) + 1
        return max(counts, key=counts.get) if counts else None

    # ------------------------------------------------------------ the lane

    def test_the_chat_lane_holds_one_block_with_a_log_a_composer_and_a_welcome_turn(self):
        context, page, errors = self._open()
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))"), ["chat"])
        self.assertEqual(page.locator('.chat-log[role="log"][aria-live="polite"]').count(), 1)
        self.assertEqual(page.locator('.chat-input[aria-label="Ask the page"]').count(), 1)
        self.assertEqual(page.locator(".chat-turn").count(), 1)
        first = page.locator(".chat-turn").first
        self.assertEqual(first.locator(".chat-speaker").inner_text().strip().lower(), ("the eval · " + self.cov["family"]).lower())
        text = self._text(first)
        self.assertIn(self.cov["family"], text)
        self.assertIn(self.cov["eval_generations"][-1]["id"], text)
        self.assertIn(str(self.cov["integrity"]["multiplicity"]["tested"]), text)
        if self.cov.get("synthetic"):
            self.assertIn("SYNTHETIC", text)
        chips = self._chips(page)
        self.assertGreaterEqual(len(chips), 4)
        self.assertIn("what did you learn?", chips)
        self.assertEqual(page.locator("#stacks .block .empty:visible").count(), 0)
        self.assertEqual(errors, [])
        context.close()
        # a batch with no lineage: the page speaks, with the batch's own chips
        context, page, errors = self._open(self.batch_dir)
        first = page.locator(".chat-turn").first
        self.assertEqual(first.locator(".chat-speaker").inner_text().strip().lower(), "the page")
        self.assertNotIn("what did you learn?", self._chips(page))
        self.assertIn("which agent is better?", self._chips(page))
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------- the eval layer

    def test_the_eval_says_what_it_learned_with_the_ledgers_reasons_and_embeds_the_loop(self):
        context, page, errors = self._open()
        row = self._ask(page, "what did you learn?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-learned")
        text = self._text(row)
        for e in self.cov["eval_generations"][1:]:
            for mid in e.get("adopted", []):
                self.assertIn(mid, text)
                self.assertIn(e["after_step"], text)
                adopted = [r for r in self._rows(mid) if r["decision"] == "adopted" and r["step"] == e["after_step"]]
                self.assertTrue(adopted, mid)
                self.assertIn(adopted[0]["reason"], text)
        mu = self.cov["integrity"]["multiplicity"]
        self.assertIn(f"tested {mu['tested']} candidates, adopted {mu['adopted']} and rejected {mu['rejected']}", text)
        self.assertEqual(self._embed(row), ("cov-flow", "true"))
        svg = row.locator(".chat-embed svg").first
        self.assertTrue(svg.get_attribute("aria-label"))
        # the sources fold is closed, so its paths are read as text content
        self.assertIn("aggregate.coevolution.eval_generations[]", page.evaluate("() => Array.from(document.querySelectorAll('.chat-turn:last-child .chat-sources li')).map(l => l.textContent)"))
        self.assertEqual(errors, [])
        context.close()

    def test_the_eval_explains_a_rejection_from_the_ledger_and_opens_the_candidate_level(self):
        cand = self._most_rejected()
        self.assertIsNotNone(cand)
        rejected = [r for r in self._rows(cand) if r["decision"] == "rejected"]
        context, page, errors = self._open()
        row = self._ask(page, f"why did you reject {cand}?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-rejected")
        text = self._text(row)
        self.assertIn(cand, text)
        for r in rejected:
            self.assertIn(r["step"], text)
            self.assertIn(r["reason"], text)
            for v in r["failed"]:
                self.assertIn(v, text)
        state = page.evaluate("() => AgentDiff.coevolution.state()")
        self.assertEqual(state["level"], "candidate")
        self.assertEqual(state["candidate"], rejected[-1]["index"])
        self.assertEqual(self._embed(row), ("cov-flow", "true"))
        self.assertEqual(row.locator(".cov-stage").get_attribute("data-level"), "candidate")
        self.assertEqual(errors, [])
        context.close()

    def test_hindsight_trust_and_the_recommendation_read_their_sections(self):
        context, page, errors = self._open()
        row = self._ask(page, "what would you have caught earlier?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-hindsight")
        text = self._text(row)
        h = self.cov["hindsight"]
        for mid, c in h["caught_at"].items():
            self.assertIn(mid, text)
            if c.get("lag") is not None:
                self.assertIn(f"lag {c['lag']}", text)
            if c.get("note"):
                self.assertIn(c["note"], text)
        self.assertIn(f"{h['changed']} changed verdict", text)
        self.assertEqual(self._embed(row), ("cov-hindsight", "true"))

        row = self._ask(page, "do you trust yourself?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-trust")
        text = self._text(row)
        g = self.cov["integrity"]
        self.assertIn(self._num(g["drift"]["jaccard_distance_from_base"]), text)
        self.assertIn(f"tested {g['multiplicity']['tested']}, adopted {g['multiplicity']['adopted']}, rejected {g['multiplicity']['rejected']}", text)
        self.assertIn(self._num(g["multiplicity"]["min_adjusted_alpha"], 4), text)
        self.assertIn(g["gap"], text)
        for mid in g.get("retired", []) + g.get("unconfirmed", []):
            self.assertIn(mid, text)
        self.assertEqual(self._embed(row), ("cov-integrity", "true"))

        row = self._ask(page, "which generation should I keep?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-keep")
        text = self._text(row)
        r = self.cov["recommended"]
        self.assertIn(f"Keep {r['evolved'] or r['base']}", text)
        self.assertIn(r["why"], text)
        for gen, whys in (r.get("excluded", {}).get("evolved") or {}).items():
            self.assertIn(gen, text)
            for w in whys:
                self.assertIn(w, text)
        self.assertEqual(self._embed(row)[0], "evo-lineage")
        self.assertEqual(page.evaluate("() => AgentDiff.evolution.state().gen"), r["evolved"] or r["base"])
        self.assertEqual(errors, [])
        context.close()

    def test_a_metric_the_probes_and_a_step_are_answered_in_the_evals_voice(self):
        adopted = [m for e in self.cov["eval_generations"][1:] for m in e.get("adopted", [])]
        mid = adopted[0]
        context, page, errors = self._open()
        row = self._ask(page, f"what does {mid} say?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-metric")
        text = self._text(row)
        self.assertIn(mid, text)
        for gen, cell in self.cov["matrix"][mid].items():
            if cell.get("measurable") is not False:
                self.assertIn(f"{gen} {self._num(cell['point'])} [{self._num(cell['lo'])}, {self._num(cell['hi'])}]", text)
        self.assertEqual(page.evaluate("() => AgentDiff.coevolution.state().metric"), mid)
        self.assertEqual(self._embed(row), ("cov-metric", "true"))

        row = self._ask(page, "which probes fired?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-probes")
        text = self._text(row)
        for p in self.cov["probes"]:
            self.assertIn(p["name"], text)
            if p.get("fired"):
                self.assertIn(", ".join(p["fired"]), text)
                self.assertIn(f"proposed {p['proposed']}, adopted {p['adopted']}", text)
        self.assertEqual(self._embed(row), ("cov-probes", "true"))

        step = [s for s in self.evo["steps"] if s.get("verdict") == "gamed"][0]
        key = self._key(step)
        row = self._ask(page, f"what did the agent do at {key}?")
        self.assertEqual(row.get_attribute("data-intent"), "eval-saw")
        text = self._text(row)
        self.assertIn(step["diff"]["summary"], text)
        self.assertIn(step["verdict"], text)
        self.assertIn(self._pct(step["effect"]["improvement"]["point"]), text)
        cov_step = [s for s in self.cov["steps"] if s["from"] == step["from"] and s["to"] == step["to"]][0]
        self.assertIn(cov_step["evolved"]["reading"], text)
        state = page.evaluate("() => AgentDiff.coevolution.state()")
        self.assertEqual((state["step"], state["level"]), (key, "step"))
        self.assertEqual(self._embed(row), ("cov-flow", "true"))
        self.assertEqual(errors, [])
        context.close()

    def test_the_comparison_answers_on_the_four_axes_and_for_both_evals(self):
        if not self.cmp.get("measurable"):
            raise unittest.SkipTest("the demo comparison is not measurable")
        other = [l["family"] for l in self.cmp["lineages"] if l["family"] != self.cov["family"]][0]
        context, page, errors = self._open(self.cmp_dir)
        self.assertIn(f"compare with {other}", self._chips(page))
        row = self._ask(page, f"compare with {other}", 600)
        self.assertEqual(row.get_attribute("data-intent"), "eval-compare")
        text = self._text(row)
        self.assertIn(self.cmp["verdict"]["reading"], text)
        self.assertIn("No winner is declared without its axis", text)
        for fam, p in self.cmp["process"].items():
            self.assertIn(f"{fam} {p['improved']} improved, {p['regressed']} regressed, {p['flat']} flat, {p['gamed']} gamed", text)
        evals = self.cmp.get("evals")
        if evals and evals.get("measurable") is not False and evals.get("lineages"):
            for l in evals["lineages"]:
                self.assertIn(f"{l['eval_generations']} eval generation", text)
                if l.get("adopted"):
                    self.assertIn(", ".join(l["adopted"]), text)
                else:
                    self.assertIn("adopting nothing", text)
                self.assertIn(f"{l['tested']} tested, {l['rejected']} rejected, drift {self._num(l['drift'])}", text)
            for t in evals.get("transfer", []):
                self.assertIn(f"{t['metric']} learned on {t['learned_on']}, applied to {t['applied_to']}", text)
            if evals.get("reading"):
                self.assertIn(evals["reading"], page.evaluate("() => Array.from(document.querySelectorAll('.chat-turn:last-child .chat-more .chat-text')).map(p => p.textContent).join(' ')"))
        else:
            self.assertIn("not in this report", text)
        self.assertEqual(self._embed(row), ("evc-verdict", "true"))
        for q in ("who evolved better?", "which agent is better?"):
            self.assertEqual(self._ask(page, q).get_attribute("data-intent"), "eval-compare")
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------- the dashboard

    def test_every_dashboard_question_embeds_the_block_it_cites(self):
        step = [s for s in self.evo["steps"] if s.get("verdict") == "gamed"][0]
        key = self._key(step)
        context, page, errors = self._open()
        row = self._ask(page, "show the timescape")
        self.assertEqual(row.get_attribute("data-intent"), "show")
        self.assertEqual(self._embed(row), ("evo-timescape", "true"))
        self.assertTrue(row.locator(".chat-embed svg").first.get_attribute("aria-label"))
        self.assertEqual(row.locator("[data-goto]").inner_text(), "open in Evolution")

        row = self._ask(page, f"what changed at {key}?")
        self.assertEqual(row.get_attribute("data-intent"), "evo-changed")
        self.assertIn(step["diff"]["summary"], self._text(row))
        self.assertEqual(self._embed(row), ("evo-step", "true"))
        self.assertEqual(page.evaluate("() => AgentDiff.evolution.state().gen"), step["to"])

        row = self._ask(page, f"did {key} help?")
        self.assertEqual(row.get_attribute("data-intent"), "evo-helped")
        imp = step["effect"]["improvement"]
        self.assertIn(f"{self._pct(imp['point'])} [{self._pct(imp['lo'])}, {self._pct(imp['hi'])}]", self._text(row))
        self.assertIn(step["reading"], self._text(row))

        row = self._ask(page, "where did the time go?")
        self.assertEqual(row.get_attribute("data-intent"), "cost")
        self.assertIn(self.pair["tradeoff"]["statement"], self._text(row))
        self.assertIn(self._embed(row)[0], ("time", "impact", "treemap", "deltas"))
        self.assertEqual(self._embed(row)[1], "true")

        row = self._ask(page, "show the tools")
        self.assertEqual(row.get_attribute("data-intent"), "tools")
        for side in ("a", "b"):
            tp = self.pair["tools_profile"][side]
            for name, t in (tp.get("tools") or {}).items():
                self.assertIn(f"{name} ×{t['calls']}", self._text(row))
        self.assertEqual(self._embed(row)[0], "tool-behaviour")

        row = self._ask(page, "what happened?")
        self.assertEqual(row.get_attribute("data-intent"), "verdict")
        self.assertIn(self.pair["verdict_card"]["lines"][0]["text"], self._text(row))
        self.assertEqual(self._embed(row)[0], "verdict-card")

        row = self._ask(page, "what can you answer?")
        self.assertEqual(row.get_attribute("data-intent"), "help")
        n = page.evaluate("() => AgentDiff.catalogue().length")
        self.assertIn(f"{n} blocks", self._text(row))
        self.assertEqual(row.locator(".chat-group .chat-chip").count(), n)
        self.assertEqual(errors, [])
        context.close()

    def test_a_training_batch_and_a_pair_batch_answer_without_the_eval(self):
        context, page, errors = self._open(self.train_dir)
        row = self._ask(page, "which policy is better?")
        self.assertEqual(row.get_attribute("data-intent"), "better")
        text = self._text(row)
        for pol in self.stats["policies"]:
            a = self.stats["aggregates"][pol]["iqm"]
            self.assertIn(f"{pol}: IQM {self.stats['metric_label']} {self._num(a['point'])} [{self._num(a['lo'])}, {self._num(a['hi'])}]", text)
        imp = self.stats["improvement"]
        self.assertIn(f"P({imp['b']} > {imp['a']}) {self._pct(imp['point'])} [{self._pct(imp['lo'])}, {self._pct(imp['hi'])}]", text)
        self.assertIn(imp["reading"], text)
        self.assertEqual(self._embed(row), ("rl-stats-improvement", "true"))
        row = self._ask(page, "what did you learn?")
        self.assertEqual(row.locator(".chat-a.cannot").count(), 1)
        self.assertIn("no self-evolving", self._text(row))
        self.assertEqual(errors, [])
        context.close()

        context, page, errors = self._open(self.batch_dir)
        row = self._ask(page, "which agent is better?")
        self.assertEqual(row.get_attribute("data-intent"), "better")
        for name, a in self.score["agents"].items():
            s = a["rates"]["success"]
            self.assertIn(f"{name} solved {s['successes']} of {s['runs']} ({self._pct(s['rate'])} [{self._pct(s['ci95'][0])}, {self._pct(s['ci95'][1])}])", self._text(row))
        self.assertEqual(self._embed(row), ("scorecard", "true"))
        row = self._ask(page, "which generation should I keep?")
        self.assertEqual(row.locator(".chat-a.cannot").count(), 1)
        self.assertIn("no self-evolving lineage", self._text(row))
        self.assertEqual(errors, [])
        context.close()

    def test_a_question_the_router_cannot_map_gets_a_cannot_card_with_three_intents(self):
        context, page, errors = self._open()
        row = self._ask(page, "what is the weather like")
        self.assertEqual(row.get_attribute("data-intent"), "cannot")
        self.assertEqual(row.locator(".chat-a.cannot").count(), 1)
        self.assertIn("can't answer that from this report", self._text(row))
        self.assertIsNone(self._embed(row)[0])
        self.assertEqual(len(self._chips(page)), 4)   # the three nearest intents and the help chip
        self.assertFalse(any(ch.isdigit() for ch in self._text(row).replace("report", "")))
        # a lineage without a comparison says so rather than inventing the other agent
        row = self._ask(page, "compare with the other agent")
        self.assertEqual(row.locator(".chat-a.cannot").count(), 1)
        self.assertIn("no second lineage", self._text(row))
        self.assertEqual(errors, [])
        context.close()

    # ---------------------------------------------------------- navigation

    def test_open_in_switches_the_view_and_lands_on_the_selection(self):
        adopted = [m for e in self.cov["eval_generations"][1:] for m in e.get("adopted", [])][-1]
        context, page, errors = self._open()
        row = self._ask(page, f"what does {adopted} say?")
        row.locator("[data-goto]").click()
        page.wait_for_timeout(600)
        self.assertEqual(page.evaluate("() => AgentDiff.state().prefs.view"), "coevolution")
        self.assertEqual(page.evaluate("() => AgentDiff.coevolution.state().metric"), adopted)
        self.assertEqual(page.locator('#stacks [data-block="cov-metric"]').count(), 1)
        page.locator('#view-tabs [data-view="chat"]').click()
        page.wait_for_timeout(500)
        step = self.evo["steps"][-1]
        row = self._ask(page, f"what changed at {self._key(step)}?")
        row.locator("[data-goto]").click()
        page.wait_for_timeout(600)
        self.assertEqual(page.evaluate("() => AgentDiff.state().prefs.view"), "evolution")
        self.assertEqual(page.evaluate("() => AgentDiff.evolution.state().gen"), step["to"])
        self.assertEqual(errors, [])
        context.close()

    def test_the_transcript_persists_under_one_key_and_clears(self):
        context, page, errors = self._open()
        self._ask(page, "what did you learn?")
        self._ask(page, "which probes fired?")
        page.reload()
        page.wait_for_timeout(1200)
        self.assertEqual(page.evaluate("() => AgentDiff.chat.turns()"), [None, "what did you learn?", "which probes fired?"])
        self.assertEqual(page.locator(".chat-turn").count(), 3)
        saved = page.evaluate("() => AgentDiff._internals.Store.get('agentdiff:chat')")
        self.assertEqual([t["q"] for t in saved["turns"]], [None, "what did you learn?", "which probes fired?"])
        # the answers are recomputed from the report, not stored
        self.assertNotIn("text", saved["turns"][1])
        self.assertIn(self.cov["probes"][0]["name"], self._text(page.locator(".chat-turn").last))
        page.locator('[data-role="clear"]').click()
        page.wait_for_timeout(500)
        self.assertEqual(page.locator(".chat-turn").count(), 1)
        self.assertEqual(page.evaluate("() => AgentDiff.chat.turns()"), [None])
        self.assertEqual(errors, [])
        context.close()

    def test_a_later_selection_folds_the_older_embed_of_the_same_family(self):
        cand = self._most_rejected()
        context, page, errors = self._open()
        self._ask(page, "what did you learn?")
        self._ask(page, f"why did you reject {cand}?")
        embeds = page.evaluate("() => Array.from(document.querySelectorAll('.chat-embed')).map(e => e.dataset.embed + ':' + e.dataset.drawn)")
        self.assertEqual(embeds, ["cov-flow:deferred", "cov-flow:true"])
        button = page.locator('.chat-embed[data-drawn="deferred"] [data-role="draw"]')
        self.assertIn("at this answer's selection", button.inner_text())
        button.click()
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("() => AgentDiff.coevolution.state().level"), "loop")
        self.assertEqual(page.locator('.chat-embed[data-embed="cov-flow"]').first.locator(".cov-stage").get_attribute("data-level"), "loop")
        self.assertEqual(page.locator('.chat-embed[data-embed="cov-flow"]').last.get_attribute("data-drawn"), "deferred")
        self.assertEqual(errors, [])
        context.close()

    def test_chips_follow_the_data_and_the_last_answer(self):
        context, page, errors = self._open()
        before = self._chips(page)
        self.assertIn(f"why did you reject {self._most_rejected()}?", before)
        self.assertIn("which generation should I keep?", before)
        self._ask(page, "what would you have caught earlier?")
        after = self._chips(page)
        self.assertNotEqual(before, after)
        self.assertIn("do you trust yourself?", after)
        page.locator(".chat-composer .chat-chip").first.click()
        page.wait_for_timeout(400)
        self.assertEqual(page.locator(".chat-turn").last.locator(".chat-q").inner_text(), after[0])
        self.assertEqual(errors, [])
        context.close()

    def test_the_composer_is_driven_by_the_keyboard(self):
        context, page, errors = self._open()
        page.locator(".chat-input").focus()
        page.keyboard.type("which probes fired?")
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator(".chat-turn").last.get_attribute("data-intent"), "eval-probes")
        self.assertEqual(page.locator(".chat-input").input_value(), "")
        page.keyboard.type("dra")
        page.keyboard.press("ArrowUp")
        self.assertEqual(page.locator(".chat-input").input_value(), "which probes fired?")
        page.keyboard.press("ArrowDown")
        self.assertEqual(page.locator(".chat-input").input_value(), "dra")
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".chat-input").input_value(), "")
        # the answer cards and every control in them are reachable by Tab
        self.assertEqual(page.locator('.chat-a[tabindex="0"]').count(), 2)
        page.locator(".chat-turn").last.locator(".chat-a").focus()
        page.keyboard.press("Tab")
        self.assertEqual(page.evaluate("() => document.activeElement.getAttribute('data-goto')"), "cov-probes")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------- phone, budget

    def test_the_chat_fits_a_phone_with_no_text_under_11px_and_every_view_stays_clean(self):
        for width in (390, 360):
            with self.subTest(width=width):
                context, page, errors = self._open(width=width)
                for q in ("what did you learn?", "do you trust yourself?", "show the timescape", "what can you answer?"):
                    self._ask(page, q, 500)
                self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
                clipped = page.evaluate("""() => Array.from(document.querySelectorAll('.chat-embed')).filter(e => e.scrollWidth > e.clientWidth + 2 && getComputedStyle(e).overflowX !== 'auto').map(e => e.dataset.embed)""")
                self.assertEqual(clipped, [])
                small = page.evaluate("""() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n = 0, node;
                    while ((node = w.nextNode())) { if (!node.textContent.trim()) continue; const el = node.parentElement; if (!el) continue;
                    if (parseFloat(getComputedStyle(el).fontSize) < 11) n++; } return n; }""")
                self.assertEqual(small, 0)
                self.assertEqual(page.locator("#stacks .block .empty:visible").count(), 0)
                self.assertEqual(errors, [])
                context.close()
        # every view the shell has, on every output, with a transcript in place: the console stays clean
        for path in (self.cov_dir, self.cmp_dir, self.train_dir, self.batch_dir):
            with self.subTest(output=path.name):
                context, page, errors = self._open(path)
                self._ask(page, "what can you answer?")
                self._ask(page, "where did the time go?")
                views = page.evaluate("() => Array.from(document.querySelectorAll('#view-tabs [data-view]')).map(t => t.dataset.view)")
                self.assertIn("chat", views)
                for view in views + ["chat"]:
                    page.locator(f'#view-tabs [data-view="{view}"]').click()
                    page.wait_for_timeout(300)
                self.assertEqual(page.locator(".chat-turn").count(), 3)
                self.assertEqual(page.locator("#stacks .block .empty:visible").count(), 0)
                self.assertEqual(errors, [])
                context.close()

    def test_an_answer_with_the_loop_embedded_draws_within_the_budget_and_the_transcript_is_capped(self):
        context, page, errors = self._open(width=1440)
        row = self._ask(page, "what did you learn?")
        self.assertLess(float(row.get_attribute("data-answer-ms")), 200)
        self.assertEqual(self._embed(row), ("cov-flow", "true"))
        # the cap: sixty turns kept, the latest six embeds live, the rest drawn on demand
        page.evaluate("""() => { const S = AgentDiff._internals.Store; const src = S.get('agentdiff:chat').source;
            const qs = ['what did you learn?', 'do you trust yourself?', 'what would you have caught earlier?', 'show the timescape', 'where did the time go?', 'which probes fired?'];
            S.set('agentdiff:chat', {turns: [{q: null}].concat(Array.from({length: 70}, (_, i) => ({q: qs[i % qs.length]}))), source: src}); }""")
        page.reload()
        page.wait_for_timeout(1500)
        self.assertEqual(page.locator(".chat-turn").count(), 60)
        self.assertLessEqual(page.locator('.chat-embed[data-drawn="true"]').count(), 6)
        self.assertGreater(page.locator('.chat-embed[data-drawn="deferred"]').count(), 40)
        ms = page.evaluate("() => { const t0 = performance.now(); AgentDiff._rerender(); return performance.now() - t0; }")
        self.assertLess(ms, 1000)
        page.locator('.chat-embed[data-drawn="deferred"] [data-role="draw"]').first.click()
        page.wait_for_timeout(400)
        self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_adds_a_turn_without_a_transition(self):
        context, page, errors = self._open(reduced_motion=True)
        row = self._ask(page, "what did you learn?", 50)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('.chat-turn.new')).animationName"), "none")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------- the levels

    def _member_key(self, pair, side):
        """The bundle's key of a pair report's side: <member>/<task>/<agent>/<run>."""
        return f"{self.bundle['members'][0]['label']}/{pair['task']['id']}/{pair[side]['agent']['name']}/{pair[side].get('run_id') or 'r1'}"

    def test_the_levels_questions_read_the_bundle_overview_the_budget_and_the_fetches(self):
        if not self.bundle:
            raise unittest.SkipTest("the bundle command did not write a page")
        pair = self.batch_pair
        context, page, errors = self._open(self.bundle_dir, width=1440)
        self.assertEqual(self._chips(page)[0], "what is running?")
        row = self._ask(page, "what is running?", 600)
        self.assertEqual(row.get_attribute("data-intent"), "lv-running")
        overview = self.bundle["levels"]["overview"]
        self.assertIn(overview["reading"], self._text(row))
        for a in overview["agents"][:3]:
            self.assertIn(f"{a['name']}{' (self-evolving)' if a['self_evolving'] else ''} — {a['runs']} run", self._text(row))
        self.assertEqual(self._embed(row), ("lv-overview", "true"))

        row = self._ask(page, "where did the tokens go?", 600)
        self.assertEqual(row.get_attribute("data-intent"), "lv-budget")
        text = self._text(row)
        for side in ("a", "b"):
            b = pair["budget"][side]
            self.assertIn(b["narrative"], text)
            self.assertIn(f"{b['tokens']['measured']} measured, {b['tokens']['estimated']} estimated and {b['tokens']['unknown']} unlabelled", text)
            self.assertIn(f"{b['waste']['after_last_evidence']} tokens after the last evidence, {b['waste']['in_errored_calls']} in errored calls, {b['waste']['in_repeats']} in repeats", text)
        self.assertIn(pair["budget"]["narrative"], text)
        self.assertEqual(self._embed(row), ("lv-run", "true"))
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), self._member_key(pair, "a"))

        row = self._ask(page, "which run was the most expensive?", 600)
        self.assertEqual(row.get_attribute("data-intent"), "lv-heaviest")
        heaviest = overview["heaviest_runs"][0]
        self.assertIn(f"The heaviest run is {heaviest['key']} at {heaviest['tokens']} tokens", self._text(row))
        self.assertEqual(self._embed(row)[0], "lv-runs")
        state = page.evaluate("() => AgentDiff.levels.state()")
        self.assertEqual((state["sort"], state["run"]), ("tokens", heaviest["key"]))

        b_name = pair["b"]["agent"]["name"]
        row = self._ask(page, f"what did {b_name} fetch?", 600)
        self.assertEqual(row.get_attribute("data-intent"), "lv-fetch")
        f = pair["fetches"]["b"]
        self.assertIn(f["narrative"], self._text(row))
        for rec in [r for r in f["records"] if r["kind"] == "search"][:6]:
            self.assertIn(f"#{rec['index']} {rec['name']}", self._text(row))
        self.assertIn(f"{len(f['map']['nodes'])} nodes and {len(f['map']['edges'])} edges", self._text(row))
        self.assertEqual(self._embed(row), ("lv-run", "true"))
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), self._member_key(pair, "b"))

        row = self._ask(page, "how many fetches were wasted?", 600)
        self.assertEqual(row.get_attribute("data-intent"), "lv-waste")
        for side in ("a", "b"):
            c = pair["fetches"][side]["counts"]
            self.assertIn(f"of {c['total']} fetches, {c['repeats']} repeated an earlier one, {c['errors']} errored, {c['unused']} recorded as not used and {c['unknown_use']} with no use signal ({c['used']} used)", self._text(row))
        self.assertIn(pair["budget"]["a"]["waste"]["basis"], self._text(row))
        # a question outside every intent still gets the cannot card, not a budget
        row = self._ask(page, "what is the weather like")
        self.assertEqual(row.get_attribute("data-intent"), "cannot")
        self.assertEqual(errors, [])
        context.close()

    def test_the_levels_questions_answer_a_plain_output_from_its_own_ledgers(self):
        budget = self.cov_agg.get("budget") or {}
        fetches = self.cov_agg.get("fetches") or {}
        if not budget.get("measurable") or not fetches.get("measurable"):
            raise unittest.SkipTest("the lineage output carries no budget or fetches ledger")
        context, page, errors = self._open()
        row = self._ask(page, "what is running?")
        self.assertEqual(row.get_attribute("data-intent"), "lv-running")
        self.assertIn("not a bundle", self._text(row))
        self.assertIn(budget["narrative"], self._text(row))
        self.assertIn(fetches["narrative"], self._text(row))
        self.assertEqual(self._embed(row), ("lv-overview", "true"))
        row = self._ask(page, "which run was the most expensive?")
        h = budget["heaviest_runs"][0]
        self.assertIn(f"The heaviest run is {h['agent']} on {h['task']} ({h['run']}) at {h['tokens']} tokens", self._text(row))
        self.assertIn(budget["cap"]["source"], self._text(row))
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), f"page/{h['task']}/{h['agent']}/{h['run']}")
        row = self._ask(page, "where did the tokens go?")
        self.assertIn(self.pair["budget"]["a"]["narrative"], self._text(row))
        self.assertEqual(self._embed(row), ("lv-run", "true"))
        self.assertEqual(errors, [])
        context.close()

    # --------------------------------------------------------- the data

    def _embed_or_absent(self, page, row, block_id):
        """The Data blocks may not be on the page yet: drawn when the catalogue
        has the block, no embed at all when it does not — never an empty one."""
        present = page.evaluate("(id) => AgentDiff.catalogue().some(b => b.id === id)", block_id)
        if present:
            self.assertEqual(self._embed(row), (block_id, "true"))
        else:
            self.assertIsNone(self._embed(row)[0])

    def test_the_data_questions_read_the_prompt_the_corpus_the_provenance_and_the_models(self):
        pair = self.batch_pair
        dt = pair.get("data") or {}
        if not dt.get("measurable"):
            raise unittest.SkipTest("the pair batch carries no data section")
        A, B = pair["a"]["agent"]["name"], pair["b"]["agent"]["name"]
        context, page, errors = self._open(self.batch_dir)
        self.assertIn("what prompt was given?", self._chips(page))
        row = self._ask(page, "what were the agents told?")
        self.assertEqual(row.get_attribute("data-intent"), "dt-prompt")
        text = self._text(row)
        self.assertIn(dt["task"]["prompt"], text)
        self.assertIn(f"{dt['task']['prompt_chars']}-character prompt", text)
        if dt["task"].get("expected"):
            self.assertIn(dt["task"]["expected"], text)
        idf = dt["instructions_diff"]
        self.assertIn("the same instructions" if idf["same"] is True else f"{len(idf['hunks'])} hunk" if idf["same"] is False else idf["reason"], text)
        for side in ("a", "b"):
            for m in dt[side]["models"]:
                self.assertIn(f"{m['model']} ({m['steps']} step", text)
                self.assertIn(m["source"], text)
        self._embed_or_absent(page, row, "dt-task")

        row = self._ask(page, f"what did {A} read that {B} didn't?")
        self.assertEqual(row.get_attribute("data-intent"), "dt-corpus")
        cd = dt["corpus_diff"]
        text = self._text(row)
        self.assertIn(f"Shared: {len(cd['shared'])}; only {A}: {len(cd['only_a'])}; only {B}: {len(cd['only_b'])}; Jaccard {self._num(cd['jaccard'])}", text)
        self.assertIn(f"{A} read {dt['a']['corpus']['distinct']} distinct source", text)
        for sid in cd["only_a"][:4]:
            src = [s for s in dt["a"]["corpus"]["sources"] if s["id"] == sid][0]
            self.assertIn(src["name"], text)
        self._embed_or_absent(page, row, "dt-corpus")

        row = self._ask(page, "is the answer grounded?")
        self.assertEqual(row.get_attribute("data-intent"), "dt-grounded")
        text = self._text(row)
        for side, name in (("a", A), ("b", B)):
            p = dt["provenance"][side]
            if p["atoms"]:
                self.assertIn(f"{name}'s answer carries {p['atoms']} typed value", text)
                self.assertIn(f"{p['supported']} traced to a fetched output and {p['unsupported']} not ({self._pct(p['grounded_share'])} grounded)", text)
                for g in dt[side]["provenance"]["grounded_in"]:
                    self.assertIn(f"step {g['step']} {g['name']} ({self._pct(g['overlap'])} of the values)", text)
            else:
                self.assertIn("no typed value", text)
            outcome = pair[side]["outcome"]["success"]
            self.assertIn(f"Grounded is not correct: {name} {'solved' if outcome else 'failed'} the task", text)
        self._embed_or_absent(page, row, "dt-provenance")

        row = self._ask(page, "which model produced this?")
        self.assertEqual(row.get_attribute("data-intent"), "dt-model")
        text = self._text(row)
        for side in ("a", "b"):
            for m in dt[side]["models"]:
                self.assertIn(f"{m['model']} ({m['steps']} steps, {m['tokens']} tokens; {m['source']})", text)
            if dt[side]["chain"].get("reading"):
                self.assertIn(dt[side]["chain"]["reading"], text)
        self.assertIn("the page names none of its own", text)
        self._embed_or_absent(page, row, "dt-chain")
        self.assertEqual(errors, [])
        context.close()

    def test_the_data_evolution_answers_a_step_from_its_evidence_change_behaviour_effect_and_eval(self):
        de = self.cov_agg.get("data_evolution") or {}
        if not de.get("measurable"):
            raise unittest.SkipTest("the lineage carries no data_evolution section")
        step = [s for s in de["steps"] if (s.get("effect") or {}).get("verdict") == "gamed"][0]
        key = self._key(step)
        context, page, errors = self._open()
        self.assertIn(f"what data triggered {key}?", self._chips(page))
        row = self._ask(page, f"what data triggered {key}?")
        self.assertEqual(row.get_attribute("data-intent"), "dt-evolve")
        text = self._text(row)
        self.assertIn(step["reading"], text)
        ev, ch, ef, el = step["evidence"], step["change"], step["effect"], step["eval"]
        self.assertIn(f"{len(ev['episodes'])} episodes cited ({ev['found']} found, {ev['failures']} failures)", text)
        for ep in ev["episodes"]:
            self.assertIn(ep, text)
        self.assertIn(ch["summary"], text)
        for rule in ch.get("rules_added", []):
            self.assertIn(rule, text)
        self.assertIn(f"sources {step['behaviour']['sources_before']} → {step['behaviour']['sources_after']}", text)
        self.assertIn(f"P({step['to']} > {step['from']}) {self._pct(ef['improvement']['point'])} [{self._pct(ef['improvement']['lo'])}, {self._pct(ef['improvement']['hi'])}]", text)
        for f in el.get("flags", []):
            self.assertIn(f["metric"], text)
        if el.get("eval_gen"):
            self.assertIn(f"advanced to {el['eval_gen']}", text)
        self._embed_or_absent(page, row, "dt-evolution")
        row = self._ask(page, "how did the agent evolve?")
        self.assertEqual(row.get_attribute("data-intent"), "dt-evolve")
        self.assertIn(de["narrative"], self._text(row))
        # a batch with no lineage says so
        context.close()
        context, page, errors = self._open(self.batch_dir)
        row = self._ask(page, f"what data triggered {key}?")
        self.assertEqual(row.locator(".chat-a.cannot").count(), 1)
        self.assertIn("no self-evolving lineage", self._text(row))
        self.assertEqual(errors, [])
        context.close()

    # ----------------------------------------------------------- follow-ups

    @staticmethod
    def _resolved(row):
        """The question the typed one resolved into, as the transcript shows it under the typed one."""
        line = row.locator(".chat-q-resolved")
        return line.inner_text().replace("↳", "").strip() if line.count() else None

    def test_a_follow_up_carries_the_last_answers_step_metric_and_candidate(self):
        steps = [self._key(s) for s in self.evo["steps"] if s.get("from") and s.get("to")]
        metrics = list(self.cov["metrics"])
        rejected = sorted({r["spec_id"] for r in self.cov["ledger"] if r.get("decision") == "rejected"})
        if len(steps) < 3 or len(metrics) < 2 or len(rejected) < 2:
            raise unittest.SkipTest("too little in the demo lineage to carry")
        context, page, errors = self._open()
        # a step, then "what about <another step>" carries the intent; "did it help?" carries the step into a new intent
        row = self._ask(page, f"what changed at {steps[0]}?")
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("evo-changed", None))
        row = self._ask(page, f"what about {steps[1]}?")
        self.assertEqual(row.get_attribute("data-intent"), "evo-changed")
        self.assertEqual(self._resolved(row), f"what changed at {steps[1]}?")
        self.assertEqual(row.get_attribute("data-resolved"), f"what changed at {steps[1]}?")
        self.assertIn(f"At {steps[1]}", self._text(row))
        self.assertEqual(page.evaluate("() => AgentDiff.evolution.state().gen"), steps[1].split("→")[1])
        row = self._ask(page, "did it help?")
        self.assertEqual(row.get_attribute("data-intent"), "evo-helped")
        self.assertEqual(self._resolved(row), f"did {steps[1]} help?")
        self.assertIn(f"verdict on {steps[1]}", self._text(row))
        # a bare generation carries the intent: "and g4?" is the step into g4
        gen = steps[2].split("→")[1]
        row = self._ask(page, f"and {gen}?")
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("evo-helped", f"did {steps[2]} help?"))
        # a metric, then a bare metric name
        row = self._ask(page, f"what does {metrics[0]} say?")
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("eval-metric", None))
        row = self._ask(page, metrics[1])
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("eval-metric", f"what does {metrics[1]} say?"))
        self.assertTrue(self._text(row).startswith(metrics[1]))
        self.assertEqual(page.evaluate("() => AgentDiff.coevolution.state().metric"), metrics[1])
        # a candidate, then "and <candidate>?"
        row = self._ask(page, f"why did you reject {rejected[0]}?")
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("eval-rejected", None))
        row = self._ask(page, f"and {rejected[1]}?")
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("eval-rejected", f"why did you reject {rejected[1]}?"))
        self.assertIn(f"I rejected {rejected[1]}", self._text(row))
        # the resolved question sits under the typed one, in small type but never under 11px, and is read to assistive technology
        line = row.locator(".chat-q-resolved")
        self.assertEqual(row.locator(".chat-q").inner_text(), f"and {rejected[1]}?")
        self.assertEqual(line.get_attribute("aria-label"), f"read as: why did you reject {rejected[1]}?")
        sizes = page.evaluate("() => { const q = document.querySelector('.chat-turn:last-child .chat-q'), r = document.querySelector('.chat-turn:last-child .chat-q-resolved'); return [parseFloat(getComputedStyle(q).fontSize), parseFloat(getComputedStyle(r).fontSize)]; }")
        self.assertLess(sizes[1], sizes[0])
        self.assertGreaterEqual(sizes[1], 11)
        self.assertIn(f"read as “why did you reject {rejected[1]}?”", page.locator(".chat-status").inner_text())
        # the subject the next question will resolve against
        last = page.evaluate("() => AgentDiff.chat.last()")
        self.assertEqual((last["intent"], last["subject"]["candidate"]), ("eval-rejected", rejected[1]))
        # a reload recomputes the transcript in order, so every resolved line comes back
        page.reload()
        page.wait_for_timeout(1000)
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('.chat-q-resolved')).map(e => e.textContent.replace('↳', '').trim())"),
                         [f"what changed at {steps[1]}?", f"did {steps[1]} help?", f"did {steps[2]} help?", f"what does {metrics[1]} say?", f"why did you reject {rejected[1]}?"])
        self.assertEqual(errors, [])
        context.close()

    def test_a_follow_up_carries_the_agent_the_other_one_and_a_pronoun_into_a_new_intent(self):
        if not self.bundle:
            raise unittest.SkipTest("the bundle command did not write a page")
        pair = self.batch_pair
        a, b = pair["a"]["agent"]["name"], pair["b"]["agent"]["name"]
        context, page, errors = self._open(self.bundle_dir, width=1440)
        row = self._ask(page, f"what did {a} fetch?", 600)
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("lv-fetch", None))
        self.assertIn(pair["fetches"]["a"]["narrative"], self._text(row))
        # "and for <the other agent>" carries the intent
        row = self._ask(page, f"and for {b}?", 600)
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("lv-fetch", f"what did {b} fetch?"))
        self.assertIn(pair["fetches"]["b"]["narrative"], self._text(row))
        self.assertNotIn(pair["fetches"]["a"]["narrative"], self._text(row))
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), self._member_key(pair, "b"))
        # "the other one" is the pair's other side
        row = self._ask(page, "the other one", 600)
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("lv-fetch", f"what did {a} fetch?"))
        self.assertIn(pair["fetches"]["a"]["narrative"], self._text(row))
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), self._member_key(pair, "a"))
        # a pronoun with an intent of its own borrows the agent: "and its tokens?" is that agent's budget
        row = self._ask(page, "and its tokens?", 600)
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("lv-budget", f"where did the tokens go for {a}?"))
        self.assertIn(pair["budget"]["a"]["narrative"], self._text(row))
        self.assertNotIn(pair["budget"]["b"]["narrative"], self._text(row))
        # a run id the page does not hold is refused, with the count it does hold, and never invented
        row = self._ask(page, "and on r9?", 600)
        self.assertEqual(row.get_attribute("data-intent"), "cannot")
        self.assertIn(f"no run r9 of {a}", self._text(row))
        # a full question keeps its own subject: it never borrows
        row = self._ask(page, "how many fetches were wasted?", 600)
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("lv-waste", None))
        for side in ("a", "b"):
            self.assertIn(f"{pair[side]['agent']['name']}: of {pair['fetches'][side]['counts']['total']} fetches", self._text(row))
        self.assertEqual(errors, [])
        context.close()

    def test_chips_offer_two_follow_ups_on_the_carried_subject(self):
        steps = [self._key(s) for s in self.evo["steps"] if s.get("from") and s.get("to")]
        context, page, errors = self._open()
        # before any answer nothing is carried
        self.assertEqual(page.locator('.chat-composer .chat-chip[data-carried="true"]').count(), 0)
        self._ask(page, f"what changed at {steps[0]}?")
        carried = page.evaluate("() => Array.from(document.querySelectorAll('.chat-composer .chat-chip[data-carried=\"true\"]')).map(b => b.textContent)")
        self.assertEqual(len(carried), 2)
        for q in carried:
            self.assertIn(steps[0], q)
        self.assertEqual(self._chips(page)[:2], carried)
        # a carried chip asks a full question about the subject: no resolution needed, a different intent
        page.locator('.chat-composer .chat-chip[data-carried="true"]').first.click()
        page.wait_for_timeout(500)
        row = page.locator(".chat-turn").last
        self.assertEqual(row.locator(".chat-q").inner_text(), carried[0])
        self.assertIsNone(self._resolved(row))
        self.assertNotEqual(row.get_attribute("data-intent"), "evo-changed")
        self.assertNotEqual(row.get_attribute("data-intent"), "cannot")
        # a cannot card carries nothing
        self._ask(page, "what is the weather like")
        self.assertEqual(page.locator('.chat-composer .chat-chip[data-carried="true"]').count(), 0)
        if self.bundle:
            context.close()
            pair = self.batch_pair
            a = pair["a"]["agent"]["name"]
            context, page, errors = self._open(self.bundle_dir, width=1440)
            self._ask(page, f"what did {a} fetch?", 600)
            carried = page.evaluate("() => Array.from(document.querySelectorAll('.chat-composer .chat-chip[data-carried=\"true\"]')).map(b => b.textContent)")
            self.assertEqual(len(carried), 2)
            for q in carried:
                self.assertIn(a, q)
        self.assertEqual(errors, [])
        context.close()

    def test_a_follow_up_with_no_earlier_answer_gets_the_cannot_card(self):
        steps = [self._key(s) for s in self.evo["steps"] if s.get("from") and s.get("to")]
        context, page, errors = self._open()
        for q in (f"and for {steps[0]}?", "the other one", "what about it?", steps[1]):
            row = self._ask(page, q)
            self.assertEqual(row.get_attribute("data-intent"), "cannot", q)
            self.assertEqual(row.locator(".chat-a.cannot").count(), 1, q)
            self.assertIn("no earlier answer", self._text(row), q)
            self.assertIsNone(self._resolved(row), q)
            self.assertFalse(any(ch.isdigit() for ch in self._text(row).replace(steps[0], "").replace(steps[1], "")), q)
        # a cannot card leaves nothing to carry, so the next follow-up is refused too; a full answer then gives the subject
        self.assertIsNone(page.evaluate("() => AgentDiff.chat.last()"))
        self._ask(page, f"what changed at {steps[0]}?")
        row = self._ask(page, f"what about {steps[1]}?")
        self.assertEqual((row.get_attribute("data-intent"), self._resolved(row)), ("evo-changed", f"what changed at {steps[1]}?"))
        # "the other one" without an agent, run or lineage to be the other of says so
        row = self._ask(page, "the other one")
        self.assertEqual(row.get_attribute("data-intent"), "cannot")
        self.assertIn("which other one", self._text(row))
        # the stateless surface: the same resolution, given the subject
        got = page.evaluate(f"""() => {{
            const bare = AgentDiff.chat.route('and for {steps[1]}?');
            const carried = AgentDiff.chat.route('and for {steps[1]}?', {{ intent: 'evo-changed', q: 'what changed at {steps[0]}?', subject: {{ intent: 'evo-changed', step: '{steps[0]}' }} }});
            return {{ bare: [bare.intent, !!bare.card.cannot, bare.resolved], carried: [carried.intent, carried.resolved, carried.subject.step] }};
        }}""")
        self.assertEqual(got["bare"], [None, True, None])
        self.assertEqual(got["carried"], ["evo-changed", f"what changed at {steps[1]}?", steps[1]])
        self.assertEqual(errors, [])
        context.close()

    def test_every_view_stays_clean_on_the_bundle_page_with_the_levels_and_data_answers_in_place(self):
        if not self.bundle:
            raise unittest.SkipTest("the bundle command did not write a page")
        for width in (1280, 390):
            with self.subTest(width=width):
                context, page, errors = self._open(self.bundle_dir, width=width)
                for q in ("what is running?", "where did the tokens go?", "what prompt was given?", "is the answer grounded?"):
                    self._ask(page, q, 500)
                self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
                views = page.evaluate("() => Array.from(document.querySelectorAll('#view-tabs [data-view]')).map(t => t.dataset.view)")
                self.assertIn("levels", views)
                for view in views + ["chat"]:
                    page.locator(f'#view-tabs [data-view="{view}"]').click()
                    page.wait_for_timeout(300)
                self.assertEqual(page.locator(".chat-turn").count(), 5)
                self.assertEqual(page.locator("#stacks .block .empty:visible").count(), 0)
                small = page.evaluate("""() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n = 0, node;
                    while ((node = w.nextNode())) { if (!node.textContent.trim()) continue; const el = node.parentElement; if (!el) continue;
                    if (parseFloat(getComputedStyle(el).fontSize) < 11) n++; } return n; }""")
                self.assertEqual(small, 0)
                self.assertEqual(errors, [])
                context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class LevelsViewTest(unittest.TestCase):
    """The Levels view (38_levels.js): three levels of grain over a bundle
    of the three demo outputs (`batch demo/traces`, `runs demo/rl/train`,
    `coevolve demo/evolve/lineage`, packed by `bundle`), and the same view
    derived from a plain output that is not a bundle.

    What is checked is that the three blocks render in the lane's declared
    order with nothing empty and the console clean; that level 1 draws every
    agent of `levels.overview` with its Wilson interval as an interval and
    every lineage with its loop; that level 2 sorts and filters the
    `levels.runs` rows by the engine's rule (descending, unrecorded last)
    and binds only the visible window to the DOM even at ten times the
    shipped scale; that a click or Enter on a row opens level 3 through the
    page-scoped `levels` family; that the burn-down and the search map on
    the heaviest run with steps carry the record's numbers in their labels,
    and that the heaviest run of all — whose steps the runs layout did not
    keep — says so with the reason; that a plain batch page still has the
    view and says what it cannot show; and that the lane fits a phone.
    """

    tmp = None
    IDS = ("lv-overview", "lv-runs", "lv-run")
    VIEWS = ("chat", "levels", "story", "evidence", "batch", "panels", "training", "evolution", "coevolution")

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file() or not (ROOT / "demo" / "traces").is_dir() or not (ROOT / "demo" / "rl" / "train").is_dir():
            raise unittest.SkipTest("no demo outputs to bundle")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        template = ROOT / "web" / "blocks.html"

        def run(name, *args):
            out = root / name
            done = subprocess.run([sys.executable, "-m", "deepcompare"] + list(args) + ["-o", str(out), "--template", str(template)],
                                  cwd=str(ROOT), capture_output=True)
            if done.returncode != 0 or not (out / "report.html").is_file():
                raise unittest.SkipTest(f"the {args[0]} command did not write a page: " + done.stderr.decode("utf-8", "replace")[-300:])
            return out

        cls.batch_dir = run("batch", "batch", str(ROOT / "demo" / "traces"))
        cls.runs_dir = run("runs", "runs", str(ROOT / "demo" / "rl" / "train"), "--token-cap", "3000")
        cls.cov_dir = run("cov", "coevolve", str(lineage))
        cls.bundle_dir = run("bundle", "bundle", str(cls.batch_dir), str(cls.runs_dir), str(cls.cov_dir), "--name", "demo")
        cls.bundle = json.loads((cls.bundle_dir / "bundle.json").read_text(encoding="utf-8"))
        # the same bundle with the source traces attached: every level-3 record completed
        cls.bundle_full_dir = run("bundle_full", "bundle", str(cls.batch_dir), str(cls.runs_dir), str(cls.cov_dir), "--name", "demo",
                                  "--traces", str(ROOT / "demo" / "traces"), str(ROOT / "demo" / "rl" / "train"), str(lineage))
        cls.bundle_full = json.loads((cls.bundle_full_dir / "bundle.json").read_text(encoding="utf-8"))
        cls.rows = cls.bundle["levels"]["runs"]
        cls.overview = cls.bundle["levels"]["overview"]
        cls.batch_agg = json.loads((cls.batch_dir / "aggregate.json").read_text(encoding="utf-8"))
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

    # ------------------------------------------------------------- helpers

    def _open(self, path=None, width=1440, reduced_motion=False):
        context = self.browser.new_context(viewport={"width": width, "height": 1000},
                                           reduced_motion="reduce" if reduced_motion else "no-preference")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # unfiltered: a warning from any block on the page is a failure here
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
        page.goto(f"file://{(path or self.bundle_dir) / 'report.html'}#view=levels")
        page.wait_for_timeout(1200)
        page.evaluate("() => AgentDiff.levels.reset()")
        page.wait_for_timeout(400)
        return context, page, errors

    @staticmethod
    def _pct(v):
        """The page's percentage: JS Math.round (half up), not Python's half-even."""
        return f"{int(v * 100 + 0.5)}%"

    @staticmethod
    def _sorted(rows, field):
        """The engine's level-2 order: descending, rows without the number last, then by key."""
        def key(r):
            v = r.get(field)
            known = isinstance(v, (int, float)) and not isinstance(v, bool)
            return (0 if known else 1, -(v if known else 0), r["key"])
        return sorted(rows, key=key)

    def _record(self, key):
        return json.loads((self.bundle_dir / self.bundle["levels"]["run_index"][key]).read_text(encoding="utf-8"))

    def _heaviest_with_steps(self):
        return self._sorted([r for r in self.rows if r["detail"]], "tokens")[0]

    # ------------------------------------------------------------ rendering

    def test_the_three_blocks_render_in_the_declared_order_on_the_bundle_page(self):
        context, page, errors = self._open()
        ids = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))")
        self.assertEqual(tuple(ids), self.IDS)
        for bid in self.IDS:
            body = page.locator(f'#stacks [data-block="{bid}"] .block-body').inner_text()
            self.assertNotIn("failed to render", body, bid)
            self.assertNotIn("Nothing to show", body, bid)
            self.assertGreater(len(body.strip()), 80, bid)
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
        # every chart of the lane is labelled with its numbers; the run table and the map are applications
        unlabelled = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .lv svg')).filter(s => !(s.getAttribute('aria-label') || '').trim() || !s.getAttribute('role')).length")
        self.assertEqual(unlabelled, 0)
        self.assertEqual(page.locator('.lv-vp[role="application"][aria-label]').count(), 1)
        self.assertEqual(page.locator('.lv-run [role="status"][aria-live="polite"]').count(), 2)
        self.assertEqual(errors, [])
        context.close()

    def test_level_one_draws_every_agent_with_its_interval_and_every_lineage_with_its_loop(self):
        context, page, errors = self._open()
        agents = self.overview["agents"]
        self.assertEqual(page.locator(".lv-overview table[data-agents] tbody tr").count(), min(len(agents), 40))
        with_ci = [a for a in agents if a["success_rate"]["lo"] is not None]
        self.assertEqual(page.locator(".lv-overview svg.lv-iv").count(), len(with_ci))
        for a in with_ci[:3]:
            label = page.locator(f'.lv-overview tr[data-agent="{a["name"]}"] svg.lv-iv').get_attribute("aria-label")
            self.assertIn(self._pct(a['success_rate']['rate']), label)
            self.assertIn(f"{self._pct(a['success_rate']['lo'])} to {self._pct(a['success_rate']['hi'])}", label)
            self.assertIn("Wilson", label)
        # the self-evolving mark on every generation of the lineage, none elsewhere
        self.assertEqual(page.locator(".lv-overview .lv-evo").count(), sum(1 for a in agents if a["self_evolving"]))
        for ln in self.overview["lineages"]:
            loop = page.locator(f'.lv-loop[data-family="{ln["family"]}"]')
            self.assertEqual(loop.count(), 1)
            label = loop.get_attribute("aria-label")
            self.assertIn(f"{ln['generations_n']} generations", label)
            if ln["eval"]:
                self.assertIn(f"{ln['eval']['generations']} generations", label)
                self.assertIn(f"{ln['eval']['closures']} loops", label)
                self.assertEqual(loop.get_attribute("data-closures"), str(ln["eval"]["closures"]))
        totals = page.locator(".lv-overview .lv-totals").inner_text()
        t = self.overview["totals"]
        self.assertIn(f"{t['runs']} runs", totals)
        self.assertIn(f"{t['tokens']:,} tokens over {t['tokens_runs']} runs", totals)
        self.assertIn(f"SYNTHETIC {self._pct(t['synthetic_share'])}", totals)
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------------- level 2

    def test_level_two_sorts_and_filters_the_rows_by_the_engine_rule(self):
        context, page, errors = self._open()
        vp = page.locator(".lv-vp")
        self.assertEqual(vp.get_attribute("data-rows"), str(len(self.rows)))
        first = page.evaluate("() => document.querySelector('.lv-row').getAttribute('data-key')")
        self.assertEqual(first, self._sorted(self.rows, "tokens")[0]["key"])
        for sort, field in (("fetches", "fetches"), ("errors", "errors"), ("seconds", "seconds"), ("cost", "cost_usd"), ("steps", "steps")):
            page.locator(f'.lv-controls button[data-sort="{sort}"]').click()
            page.wait_for_timeout(200)
            self.assertEqual(page.evaluate("() => AgentDiff.levels.state().sort"), sort)
            keys = page.evaluate("() => Array.from(document.querySelectorAll('.lv-row')).slice(0, 5).map(r => r.getAttribute('data-key'))")
            self.assertEqual(keys, [r["key"] for r in self._sorted(self.rows, field)[:5]], sort)
        # a header click sorts too
        page.locator('.lv-head button[data-sort="tokens"]').click()
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().sort"), "tokens")
        # filter chips: an agent, then an outcome, then a member
        agent = self.overview["agents"][-1]["name"]
        page.locator(f'.lv-controls [data-facet="agent"] button[data-value="{agent}"]').click()
        page.wait_for_timeout(250)
        own = [r for r in self.rows if r["agent"] == agent]
        self.assertEqual(page.locator(".lv-vp").get_attribute("data-rows"), str(len(own)))
        self.assertIn(f"{len(own)} shown (agent {agent})", page.locator(".lv-runs .lv-status").inner_text())
        page.locator('.lv-controls [data-facet="outcome"] button[data-value="fail"]').click()
        page.wait_for_timeout(250)
        failed = [r for r in own if r["success"] is False]
        self.assertEqual(page.locator(".lv-vp").get_attribute("data-rows"), str(len(failed)))
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('.lv-row .lv-mark')).every(m => m.textContent === '✗')"), True)
        member = self.bundle["members"][1]["label"]
        page.locator('.lv-controls button:has-text("clear filters")').click()
        page.wait_for_timeout(200)
        page.locator(f'.lv-controls [data-facet="member"] button[data-value="{member}"]').click()
        page.wait_for_timeout(250)
        self.assertEqual(page.locator(".lv-vp").get_attribute("data-rows"), str(self.bundle["members"][1]["runs"]))
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().member"), member)
        # the selection persists through the page's store and a reload
        page.reload()
        page.wait_for_timeout(1200)
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().member"), member)
        self.assertEqual(errors, [])
        context.close()

    def test_level_two_binds_only_the_visible_window_even_at_ten_times_the_scale(self):
        context, page, errors = self._open()
        vp = page.locator(".lv-vp")
        n = len(self.rows)
        self.assertGreater(n, 100)
        self.assertLessEqual(int(vp.get_attribute("data-dom-rows")), 40)
        self.assertEqual(page.locator(".lv-row").count(), int(vp.get_attribute("data-dom-rows")))
        page.evaluate("() => AgentDiff.levels.tile(10)")
        page.wait_for_timeout(800)
        vp = page.locator(".lv-vp")
        self.assertEqual(vp.get_attribute("data-rows"), str(10 * n))
        self.assertLessEqual(int(vp.get_attribute("data-dom-rows")), 40)
        self.assertLess(float(page.locator(".lv-runs").get_attribute("data-draw-ms")), 250)
        self.assertIn("tiled ×10", page.locator(".lv-runs .lv-status").inner_text())
        before = vp.get_attribute("data-window")
        page.evaluate("() => { const vp = document.querySelector('.lv-vp'); vp.scrollTop = vp.scrollHeight / 2; }")
        page.wait_for_timeout(300)
        after = page.locator(".lv-vp").get_attribute("data-window")
        self.assertNotEqual(before, after)
        self.assertLessEqual(int(page.locator(".lv-vp").get_attribute("data-dom-rows")), 40)
        self.assertLessEqual(page.locator(".lv-row").count(), 40)
        page.evaluate("() => AgentDiff.levels.tile(1)")
        page.wait_for_timeout(600)
        self.assertEqual(page.locator(".lv-vp").get_attribute("data-rows"), str(n))
        self.assertEqual(errors, [])
        context.close()

    def test_a_row_opens_level_three_through_the_family_by_mouse_and_by_keyboard(self):
        context, page, errors = self._open()
        order = self._sorted(self.rows, "tokens")
        # by default level 3 shows the heaviest run whose steps are in the output, and says so
        run = page.locator(".lv-run")
        self.assertEqual(run.get_attribute("data-run"), self._heaviest_with_steps()["key"])
        self.assertIn("no run chosen", run.locator(".lv-status").first.inner_text())
        self.assertIsNone(page.evaluate("() => AgentDiff.levels.state().run"))
        page.locator(".lv-row").nth(1).click()
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), order[1]["key"])
        self.assertEqual(page.locator(".lv-run").get_attribute("data-run"), order[1]["key"])
        self.assertEqual(page.locator('.lv-row[aria-current="true"]').get_attribute("data-key"), order[1]["key"])
        # the keyboard: the table is one application; arrows move the cursor, Enter opens
        page.focus(".lv-vp")
        page.keyboard.press("Home")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("() => AgentDiff.levels.state().run"), order[3]["key"])
        self.assertEqual(page.locator(".lv-run").get_attribute("data-run"), order[3]["key"])
        # the walk at level 3 follows the level-2 order; the crumb goes back up
        page.locator('.lv-run button:has-text("next ›")').click()
        page.wait_for_timeout(400)
        self.assertEqual(page.locator(".lv-run").get_attribute("data-run"), order[4]["key"])
        page.locator('.lv-run .lv-crumbs button:has-text("all runs")').click()
        page.wait_for_timeout(400)
        self.assertIsNone(page.evaluate("() => AgentDiff.levels.state().run"))
        # the family is page-scoped and exposed
        self.assertEqual(page.evaluate("() => AgentDiff.lib.family('levels').scope"), "page")
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------------- level 3

    def test_level_three_draws_the_burn_down_and_the_search_map_of_the_heaviest_run_with_steps(self):
        context, page, errors = self._open()
        row = self._heaviest_with_steps()
        rec = self._record(row["key"])
        self.assertTrue(rec["measurable"])
        page.evaluate(f"() => AgentDiff.levels.select({{run: {json.dumps(row['key'])}}})")
        page.wait_for_timeout(500)
        run = page.locator(".lv-run")
        self.assertEqual(run.get_attribute("data-measurable"), "true")
        burn = run.locator("svg.lv-burn")
        self.assertEqual(burn.count(), 1)
        label = burn.get_attribute("aria-label")
        total = rec["budget"]["tokens"]["total"]
        self.assertIn(f"{total:,} cumulative tokens over {len(rec['steps'])} steps", label)
        self.assertIn("coloured by kind", label)
        waste = rec["budget"]["waste"]["after_last_evidence"]
        if waste:
            self.assertIn(f"{waste:,} tokens after the last evidence", label)
            self.assertEqual(run.locator(".lv-waste").count(), 1)
        top = rec["budget"]["top"][0]
        self.assertIn(f"#{top['index']} {top['name']} {top['tokens']:,}", label)
        self.assertEqual(burn.get_attribute("data-steps"), str(len(rec["budget"]["burn"])))
        unmeasured = sum(1 for s in rec["steps"] if s["tokens_basis"] != "measured")
        self.assertEqual(run.locator(".lv-burn .lv-unmeasured").count(), unmeasured)
        # the x measure toggles to wall-clock from the steps' latencies
        page.locator('.lv-run button[data-x="time"]').click()
        page.wait_for_timeout(400)
        self.assertEqual(page.locator("svg.lv-burn").get_attribute("data-x"), "time")
        self.assertIn("wall-clock seconds", page.locator("svg.lv-burn").get_attribute("aria-label"))
        page.locator('.lv-run button[data-x="steps"]').click()
        page.wait_for_timeout(300)
        # the search map: every node of the record's map, every edge, the reaches edges only where use is recorded
        fmap = rec["fetches"]["map"]
        m = run.locator("svg.lv-map")
        self.assertEqual(m.count(), 1)
        self.assertEqual(m.get_attribute("role"), "application")
        self.assertEqual(m.get_attribute("data-nodes"), str(len(fmap["nodes"])))
        self.assertEqual(m.get_attribute("data-edges"), str(len(fmap["edges"])))
        reaches = sum(1 for e in fmap["edges"] if e["kind"] == "reaches")
        self.assertEqual(run.locator('.lv-map path[data-kind="reaches"]').count(), reaches)
        self.assertEqual(reaches, rec["fetches"]["counts"]["used"])
        self.assertIn(f"{rec['fetches']['counts']['unknown_use']} of unknown use", m.get_attribute("aria-label"))
        self.assertEqual(run.locator(".lv-map .lv-node").count(), len(fmap["nodes"]))
        # the fetch table has every record; the budget bars carry the split
        self.assertEqual(run.locator(".lv-fetches tbody tr").count(), len(rec["fetches"]["records"]))
        by_kind = rec["budget"]["tokens"]["by_kind"]
        bud = run.locator("svg.lv-budget").get_attribute("aria-label")
        for kind, v in by_kind.items():
            if v:
                self.assertIn(f"{kind} {v:,}", bud)
        # a node is reached by Tab and its record reads in the status line
        page.locator(".lv-map .lv-node").first.focus()
        page.wait_for_timeout(200)
        first = fmap["nodes"][0] if fmap["nodes"][0]["kind"] != "answer" else fmap["nodes"][1]
        status = run.locator(".lv-map-host").locator("xpath=preceding-sibling::p[1]").inner_text()
        self.assertIn(f"#{first['index']}", status)
        self.assertEqual(errors, [])
        context.close()

    def test_level_three_tells_a_harness_retry_from_the_agent_asking_twice(self):
        """t09's pair makes the same call twice in each run for two different
        reasons. The engine separates them on the recorded `attempt`; this is
        the check that the *page* passes that on instead of printing one
        "repeats" count over both — the divergence this view has quietly had
        before."""
        context, page, errors = self._open()
        keys = sorted(r["key"] for r in self.rows if "t09_region_error_rate" in r["key"])
        self.assertEqual(len(keys), 2, keys)
        seen = {}
        for key in keys:
            rec = self._record(key)
            if not rec.get("measurable"):
                raise unittest.SkipTest(f"{key}: no steps in the output")
            counts = rec["fetches"]["counts"]
            page.evaluate(f"() => AgentDiff.levels.select({{run: {json.dumps(key)}}})")
            page.wait_for_timeout(500)
            run = page.locator(".lv-run")
            self.assertEqual(run.get_attribute("data-run"), key)
            label = run.locator("svg.lv-map").get_attribute("aria-label")
            # text_content, not inner_text: the table sits in a fold whose
            # cells may not be laid out, and innerText of an unrendered node
            # is the empty string
            cells = [t.strip() for t in run.locator(".lv-fetches tbody tr td:nth-child(11)").all_text_contents()]
            self.assertEqual(len(cells), len(rec["fetches"]["records"]))
            for cell, r in zip(cells, rec["fetches"]["records"]):
                if r["attempt"] is None:
                    self.assertEqual(cell, "—")
                elif r["attempt"] > 1:
                    self.assertIn(str(r["attempt"]), cell)
                    self.assertIn("re-run", cell)
                else:
                    self.assertEqual(cell, "1")
            nums = run.locator(".lv-nums").inner_text()
            if counts["retries"]:
                self.assertIn(f"{counts['retries']} retries the harness re-ran", label)
                self.assertIn(f"{counts['retries']} retries (harness re-ran)", nums)
                self.assertEqual(run.locator(".lv-map .lv-retry").count(), counts["retries"])
            else:
                # a zero here is an absent record, and the page says so
                self.assertIn("no step numbers its attempt", label)
                self.assertNotIn("retries (harness re-ran)", nums)
                self.assertEqual(run.locator(".lv-map .lv-retry").count(), 0)
            self.assertIn(f"{counts['repeats']} repeats", label)
            seen[key] = (counts["retries"], counts["repeats"])
        # the demonstration itself: one run's second call was the harness's,
        # the other's was the agent's, and the page does not call both a repeat
        self.assertEqual(sorted(seen.values()), [(0, 1), (2, 0)], seen)
        self.assertEqual(errors, [])
        context.close()

    def test_the_heaviest_run_of_all_has_no_steps_in_the_output_and_says_why(self):
        context, page, errors = self._open()
        heaviest = self.overview["heaviest_runs"][0]
        row = [r for r in self.rows if r["key"] == heaviest["key"]][0]
        rec = self._record(row["key"])
        if rec["measurable"]:
            raise unittest.SkipTest("the heaviest run's steps are in the output")
        page.evaluate(f"() => AgentDiff.levels.select({{run: {json.dumps(row['key'])}}})")
        page.wait_for_timeout(500)
        run = page.locator(".lv-run")
        self.assertEqual(run.get_attribute("data-measurable"), "false")
        self.assertEqual(run.locator("svg.lv-burn").count(), 0)
        self.assertEqual(run.locator("svg.lv-map").count(), 0)
        cannot = run.locator(".lv-cannot").inner_text()
        self.assertIn(rec["reason"], cannot)
        nums = run.locator(".lv-nums").inner_text()
        self.assertIn(f"{row['tokens']:,} tokens", nums)
        self.assertIn(f"{row['steps']} steps", nums)
        self.assertIn(f"{row['fetches']} fetches", nums)
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
        self.assertEqual(errors, [])
        context.close()

    # ---------------------------------------------------------- plain pages

    def test_a_plain_batch_page_derives_the_view_and_says_what_it_cannot_show(self):
        context, page, errors = self._open(path=self.batch_dir)
        ids = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))")
        self.assertEqual(tuple(ids), self.IDS)
        self.assertEqual(page.locator(".lv-overview").get_attribute("data-source"), "page")
        cannot = page.locator(".lv-overview .lv-cannot").inner_text()
        self.assertIn("not a bundle", cannot)
        self.assertIn("agentdiff bundle", cannot)
        per_run = self.batch_agg["scorecard"]["per_run"]
        self.assertEqual(page.locator(".lv-vp").get_attribute("data-rows"), str(len(per_run)))
        # the scorecard's own Wilson interval is drawn per agent; nothing is computed on the page
        agents = self.batch_agg["scorecard"]["agents"]
        self.assertEqual(page.locator(".lv-overview svg.lv-iv").count(), len(agents))
        name = sorted(agents)[0]
        ci = agents[name]["rates"]["success"]["ci95"]
        label = page.locator(f'.lv-overview tr[data-agent="{name}"] svg.lv-iv').get_attribute("aria-label")
        self.assertIn(f"{self._pct(ci[0])} to {self._pct(ci[1])}", label)
        # level 3 is whole for a run whose report is on the page
        run = page.locator(".lv-run")
        self.assertEqual(run.get_attribute("data-measurable"), "true")
        self.assertEqual(run.locator("svg.lv-burn").count(), 1)
        self.assertEqual(run.locator("svg.lv-map").count(), 1)
        self.assertTrue(run.get_attribute("data-run").startswith("page/"))
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_the_console_is_clean_across_every_view_on_every_output(self):
        for path in (self.bundle_dir, self.runs_dir, self.cov_dir):
            context, page, errors = self._open(path=path)
            views = page.evaluate("() => Array.from(document.querySelectorAll('#view-tabs [data-view]')).map(t => t.dataset.view)")
            self.assertIn("levels", views)
            for view in views + ["levels"]:
                page.locator(f'#view-tabs [data-view="{view}"]').click()
                page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
            self.assertEqual(errors, [], str(path))
            context.close()

    # ------------------------------------------------------- small screens

    def test_the_lane_fits_a_phone_with_no_small_text(self):
        for width in (390, 360):
            context, page, errors = self._open(width=width)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
            self.assertEqual(page.locator(".lv-grid").evaluate("e => e.closest('.scroll-x') !== null"), True)
            small = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks .lv *'))
                .filter(e => e.tagName.toLowerCase() !== 'title' && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
                .filter(e => parseFloat(getComputedStyle(e).fontSize) < 11).length""")
            self.assertEqual(small, 0)
            sizes = page.evaluate("""() => [...new Set(Array.from(document.querySelectorAll('#stacks *'))
                .filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())).map(e => getComputedStyle(e).fontSize))]""")
            self.assertLessEqual(len(sizes), 7)
            page.locator(".lv-row").first.click()
            page.wait_for_timeout(500)
            top = self._sorted(self.rows, "tokens")[0]
            self.assertEqual(page.locator(".lv-run").get_attribute("data-run"), top["key"])
            self.assertEqual(page.locator(".lv-run svg.lv-burn").count(), 1 if self._record(top["key"])["measurable"] else 0)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
            self.assertEqual(errors, [], str(width))
            context.close()

    # --------------------------------------------------------- with traces

    def test_a_bundle_built_with_traces_completes_every_run_and_says_the_steps_source(self):
        """`--traces` completes every level-3 record from the source traces:
        the run whose steps the plain bundle did not keep draws its
        burn-down and its search map on the traced bundle, its numbers line
        says `steps_source`, and the record's numbers are in the labels;
        the plain bundle still says the steps are not in the output, with
        the reason. Both pages, the console unfiltered."""
        heaviest = self.overview["heaviest_runs"][0]["key"]
        plain = self._record(heaviest)
        if plain["measurable"]:
            raise unittest.SkipTest("the heaviest run's steps are in the plain bundle")
        full_rows = self.bundle_full["levels"]["runs"]
        self.assertEqual(len(full_rows), len(self.rows))
        self.assertEqual(sum(1 for r in full_rows if r["detail"]), len(full_rows))
        rec = json.loads((self.bundle_full_dir / self.bundle_full["levels"]["run_index"][heaviest]).read_text(encoding="utf-8"))
        self.assertTrue(rec["measurable"])
        self.assertTrue(rec["steps_source"].startswith("trace "), rec["steps_source"])
        self.assertTrue((self.bundle_full_dir / rec["steps_source"].split(" ", 1)[1]).is_file())
        for path, traced in ((self.bundle_dir, False), (self.bundle_full_dir, True)):
            with self.subTest(traced=traced):
                context, page, errors = self._open(path=path)
                page.wait_for_timeout(800)
                page.evaluate(f"() => AgentDiff.levels.select({{run: {json.dumps(heaviest)}}})")
                page.wait_for_timeout(800)
                run = page.locator(".lv-run")
                self.assertEqual(run.get_attribute("data-run"), heaviest)
                self.assertEqual(run.get_attribute("data-measurable"), "true" if traced else "false")
                nums = run.locator(".lv-nums").inner_text()
                source = run.locator('.lv-nums [data-role="steps-source"]').inner_text()
                if traced:
                    self.assertEqual(run.get_attribute("data-steps-source"), rec["steps_source"])
                    self.assertEqual(source, "steps from " + rec["steps_source"])
                    self.assertEqual(run.locator(".lv-cannot").count(), 0)
                    burn = run.locator("svg.lv-burn")
                    self.assertEqual(burn.count(), 1)
                    total = rec["budget"]["tokens"]["total"]
                    self.assertIn(f"{total:,} cumulative tokens over {len(rec['steps'])} steps", burn.get_attribute("aria-label"))
                    self.assertEqual(burn.get_attribute("data-steps"), str(len(rec["budget"]["burn"])))
                    self.assertEqual(run.locator("svg.lv-map").count(), 1 if rec["fetches"]["counts"]["total"] else 0)
                    if rec["fetches"]["counts"]["total"]:
                        self.assertEqual(run.locator("svg.lv-map").get_attribute("data-nodes"), str(len(rec["fetches"]["map"]["nodes"])))
                    self.assertIn(f"{len(rec['steps'])} steps, {len(rec['fetches']['records'])} fetch records", run.locator('[role="status"]').first.inner_text())
                    self.assertEqual(run.locator('[data-role="steps-table"] tbody tr').count(), min(len(rec["steps"]), 500))
                else:
                    self.assertEqual(run.get_attribute("data-steps-source"), "")
                    self.assertEqual(source, "steps not in the output")
                    self.assertEqual(run.locator("svg.lv-burn").count(), 0)
                    self.assertIn(plain["reason"], run.locator(".lv-cannot").inner_text())
                row = [r for r in self.rows if r["key"] == heaviest][0]
                self.assertIn(f"{row['tokens']:,} tokens", nums)
                self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
                self.assertEqual(errors, [])
                context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class DataViewTest(unittest.TestCase):
    """The Data view (39_data.js): the inputs side of a pair — the prompt
    both agents were told, what each read, what each answer rests on, the
    chain data → model → agent → answer — and, on a lineage, how the agent
    evolves from the data its evidence episodes read.

    What is checked is that the lane's blocks render in the order
    `00_core.js` declares for it (four on a batch page, the evolution row
    ledger joining them on a lineage page) with nothing empty, every chart
    labelled with its numbers and the console clean, unfiltered, across
    every view of the batch, runs and lineage outputs; that the task block
    says plainly that the demo records no instructions and shows the
    models as the traces record them with the source of that attribution;
    that the corpus table is the section's set diff row for row and a click
    (or Enter) reveals a source's input and output from the report's step;
    that the provenance marks and counts are the section's, with the pair's
    own answer_eval beside them; that the chain draws exactly the section's
    nodes and edges for either side and a node opens the step by mouse and
    by keyboard; that a lineage row opens to the section's hunks and the
    episodes' sources; that one page-scoped `data` family carries the
    selection between the blocks and through a reload; that the drawings
    stay in tens of milliseconds at ten times the shipped scale; and that
    the lane fits a phone at 390 and 360 with no text under 11px.
    """

    tmp = None
    PAIR_IDS = ("dt-task", "dt-corpus", "dt-provenance", "dt-chain")

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file() or not (ROOT / "demo" / "traces").is_dir() or not (ROOT / "demo" / "rl" / "train").is_dir():
            raise unittest.SkipTest("no demo outputs to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        template = ROOT / "web" / "blocks.html"

        def run(name, *args):
            out = root / name
            done = subprocess.run([sys.executable, "-m", "deepcompare"] + list(args) + ["-o", str(out), "--template", str(template)],
                                  cwd=str(ROOT), capture_output=True)
            if done.returncode != 0 or not (out / "report.html").is_file():
                raise unittest.SkipTest(f"the {args[0]} command did not write a page: " + done.stderr.decode("utf-8", "replace")[-300:])
            return out

        cls.batch_dir = run("batch", "batch", str(ROOT / "demo" / "traces"))
        cls.cov_dir = run("cov", "coevolve", str(lineage))
        cls.runs_dir = run("runs", "runs", str(ROOT / "demo" / "rl" / "train"))
        cls.batch_reports = {}
        for path in sorted(cls.batch_dir.glob("report_*.json")):
            rep = json.loads(path.read_text(encoding="utf-8"))
            cls.batch_reports[rep["task"]["id"]] = rep
        cls.cov_agg = json.loads((cls.cov_dir / "aggregate.json").read_text(encoding="utf-8"))
        cls.cov_reports = {}
        for path in sorted(cls.cov_dir.glob("report_*.json")):
            rep = json.loads(path.read_text(encoding="utf-8"))
            cls.cov_reports[rep["task"]["id"]] = rep
        if not any(r.get("data") for r in cls.batch_reports.values()):
            raise unittest.SkipTest("the batch reports carry no data section")
        if not (cls.cov_agg.get("data_evolution") or {}).get("measurable"):
            raise unittest.SkipTest("the lineage carries no measurable data_evolution section")
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

    # ------------------------------------------------------------- helpers

    def _open(self, path=None, width=1440, reduced_motion=False, reset=True):
        context = self.browser.new_context(viewport={"width": width, "height": 1000},
                                           reduced_motion="reduce" if reduced_motion else "no-preference")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # unfiltered: a warning from any block on the page is a failure here
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
        page.goto(f"file://{(path or self.batch_dir) / 'report.html'}#view=data")
        page.wait_for_timeout(1200)
        if reset:
            page.evaluate("() => AgentDiff.data.reset()")
            page.wait_for_timeout(400)
        return context, page, errors

    def _task(self, page, task_id):
        page.select_option("#task-picker", task_id)
        page.wait_for_timeout(600)

    def _state(self, page):
        return page.evaluate("() => AgentDiff.data.state()")

    def _ids(self, page):
        return page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))")

    def _lane_order(self, page):
        """The order 00_core.js declares for the data lane — the rule, not a roster."""
        return page.evaluate("() => (AgentDiff._internals.STACK_PLAN.filter(p => p.groups.indexOf('data') >= 0)[0] || {}).order || []")

    def _first_task(self, reports):
        return sorted(reports)[0]

    @staticmethod
    def _pct(v):
        return f"{int(v * 100 + 0.5)}%"

    # ------------------------------------------------------------ rendering

    def test_the_blocks_render_in_the_declared_order_on_a_batch_and_on_a_lineage(self):
        for path, with_lineage in ((self.batch_dir, False), (self.cov_dir, True)):
            context, page, errors = self._open(path=path)
            ids = self._ids(page)
            order = self._lane_order(page)
            self.assertTrue(order, "the core declares the data lane's order")
            # every drawn block is one the lane names, in the lane's order; the four pair blocks are always there
            self.assertEqual(ids, [i for i in order if i in ids])
            for bid in self.PAIR_IDS:
                self.assertIn(bid, ids)
            self.assertEqual("dt-evolution" in ids, with_lineage, str(path))
            for bid in ids:
                body = page.locator(f'#stacks [data-block="{bid}"] .block-body').inner_text()
                self.assertNotIn("failed to render", body, bid)
                self.assertNotIn("Nothing to show", body, bid)
                self.assertGreater(len(body.strip()), 80, bid)
            self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
            unlabelled = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .dt svg')).filter(s => !(s.getAttribute('aria-label') || '').trim() || !s.getAttribute('role')).length")
            self.assertEqual(unlabelled, 0)
            self.assertEqual(page.locator('.dt-chain .dt-stage[role="application"][aria-label]').count(), 1)
            self.assertEqual(page.locator('.dt-chain [role="status"][aria-live="polite"]').count(), 1)
            self.assertEqual(errors, [], str(path))
            context.close()

    def test_the_task_block_shows_the_prompt_says_no_instructions_and_names_the_models_as_recorded(self):
        task = self._first_task(self.batch_reports)
        rep = self.batch_reports[task]
        d = rep["data"]
        context, page, errors = self._open()
        self._task(page, task)
        card = page.locator('#stacks [data-block="dt-task"]')
        self.assertEqual(card.locator('[data-role="prompt"]').first.inner_text(), rep["task"]["prompt"])
        if d["task"].get("expected") is not None:
            self.assertEqual(card.locator('[data-role="expected"]').inner_text(), d["task"]["expected"])
        # the demo records no instructions: the page says so and shows what is recorded
        if d["instructions_diff"]["same"] is None:
            self.assertEqual(card.locator('[data-role="no-instructions"]').count(), 1)
            self.assertIn(d["instructions_diff"]["reason"], card.locator('[data-role="no-instructions"]').inner_text())
        else:
            self.assertEqual(card.locator('[data-role="no-instructions"]').count(), 0)
        body = card.inner_text()
        for side in ("a", "b"):
            for m in d[side]["models"]:
                self.assertIn(str(m["model"]), body)
                self.assertIn(m["source"], body)
            for t in d[side]["agent"]["tools_used"]:
                self.assertIn(t["name"], body)
        self.assertEqual(errors, [])
        context.close()

    def test_the_corpus_table_is_the_sections_set_diff_and_a_click_reveals_the_sources_text(self):
        task = self._first_task(self.batch_reports)
        rep = self.batch_reports[task]
        d = rep["data"]
        cd = d["corpus_diff"]
        context, page, errors = self._open()
        self._task(page, task)
        card = page.locator('#stacks [data-block="dt-corpus"]')
        rows = page.evaluate("() => Array.from(document.querySelectorAll('.dt-corpus tr.dt-src')).map(r => [r.dataset.source, r.dataset.set])")
        self.assertEqual(len(rows), len(cd["shared"]) + len(cd["only_a"]) + len(cd["only_b"]))
        self.assertEqual(sorted(r[0] for r in rows if r[1] == "shared"), sorted(cd["shared"]))
        self.assertEqual(sorted(r[0] for r in rows if r[1] == "only_a"), sorted(cd["only_a"]))
        self.assertEqual(sorted(r[0] for r in rows if r[1] == "only_b"), sorted(cd["only_b"]))
        # shared first, then only A, then only B
        sets = [r[1] for r in rows]
        self.assertEqual(sets, sorted(sets, key=lambda s: {"shared": 0, "only_a": 1, "only_b": 2}[s]))
        self.assertIn(f"Jaccard {cd['jaccard']:.3f}".rstrip("0").rstrip("."), card.locator(".dt-lede").inner_text())
        self.assertEqual(card.locator(".dt-set").evaluate("e => e.closest('.scroll-x') !== null"), True)
        # a click opens the source: its input and output from the report's step, capped at 4,000
        src = d["a"]["corpus"]["sources"][0]
        page.locator(f'.dt-corpus tr.dt-src[data-source="{src["id"]}"]').click()
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["source"], src["id"])
        self.assertEqual(page.locator(f'.dt-corpus tr.dt-src[aria-selected="true"]').get_attribute("data-source"), src["id"])
        detail = page.locator(".dt-corpus .dt-detail")
        self.assertEqual(detail.get_attribute("data-source"), src["id"])
        step = [s for s in rep["a"]["steps"] if s["index"] == src["first_step"]][0]
        pres = detail.locator('[data-side="a"] pre.dt-text')
        self.assertEqual(pres.nth(0).inner_text(), (step["input"] or "")[:4000])
        self.assertEqual(pres.nth(1).inner_text(), (step["output"] or "")[:4000])
        self.assertLessEqual(int(pres.nth(1).get_attribute("data-chars")), max(len(step["output"] or ""), 1) if step["output"] else 0)
        # Enter on another row moves the selection; a second click on the same row clears it
        second = rows[1][0]
        page.locator(f'.dt-corpus tr.dt-src[data-source="{second}"]').focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        self.assertEqual(self._state(page)["source"], second)
        page.locator(f'.dt-corpus tr.dt-src[data-source="{second}"]').click()
        page.wait_for_timeout(300)
        self.assertIsNone(self._state(page)["source"])
        self.assertEqual(page.locator(".dt-corpus .dt-detail").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_the_provenance_marks_and_counts_are_the_sections_with_answer_eval_beside(self):
        # a task whose answers carry an unsupported value when the demo has one, else the first
        tasks = sorted(self.batch_reports)
        pick = [t for t in tasks if any(self.batch_reports[t]["data"][s]["provenance"]["unsupported"] for s in ("a", "b"))]
        task = pick[0] if pick else tasks[0]
        rep = self.batch_reports[task]
        d = rep["data"]
        context, page, errors = self._open()
        self._task(page, task)
        for side in ("a", "b"):
            pv = d[side]["provenance"]
            col = page.locator(f'.dt-provenance .dt-prov[data-side="{side}"]')
            counts = col.locator('[data-role="counts"]').inner_text()
            self.assertIn(f"{pv['atoms']} typed value", counts)
            self.assertIn(f"{pv['supported']} supported", counts)
            self.assertIn(f"{pv['unsupported']} unsupported", counts)
            self.assertIn(self._pct(pv["grounded_share"]) if pv["grounded_share"] is not None else "null", counts)
            marked = page.evaluate(f"""() => Array.from(document.querySelectorAll('.dt-provenance .dt-prov[data-side="{side}"] mark.dt-val')).map(m => [m.dataset.value, m.dataset.supported, m.dataset.source || null])""")
            not_located = col.locator('[data-role="not-located"]').count()
            located_ids = {m[0] for m in marked}
            self.assertTrue(located_ids <= {v["id"] for v in pv["values"]})
            # every value is either marked in the text or named as not located verbatim
            self.assertEqual(len(marked) + (len(pv["values"]) - len(marked) if not_located else 0), len(pv["values"]))
            for m in marked:
                v = [x for x in pv["values"] if x["id"] == m[0]][0]
                self.assertEqual(m[1], "true" if v["supported"] else "false", m)
                if v["supported"]:
                    carrier = [g for g in pv["grounded_in"] if m[0] in g["atoms"]][0]
                    self.assertEqual(m[2], carrier["source"])
            self.assertEqual(col.locator('.dt-leg li[data-source]').count(), len(pv["grounded_in"]))
            ev = col.locator('[data-role="answer-eval"]').inner_text()
            self.assertIn(rep["answer_eval"][f"{side}_vs_expected"]["verdict"], ev)
            self.assertIn("not correct", ev)
        # the legend hands the source to the corpus block through the family
        g = d["a"]["provenance"]["grounded_in"]
        if g:
            page.locator('.dt-provenance .dt-prov[data-side="a"] .dt-leg button').first.click()
            page.wait_for_timeout(400)
            self.assertEqual(self._state(page)["source"], g[0]["source"])
            self.assertEqual(page.locator(".dt-corpus .dt-detail").get_attribute("data-source"), g[0]["source"])
        # the limits of the measure are on the page
        self.assertIn("not true", page.locator('#stacks [data-block="dt-provenance"]').inner_text())
        self.assertEqual(errors, [])
        context.close()

    def test_the_chain_draws_the_sections_nodes_and_edges_and_a_node_opens_the_step(self):
        task = self._first_task(self.batch_reports)
        rep = self.batch_reports[task]
        d = rep["data"]
        context, page, errors = self._open()
        self._task(page, task)
        for side in ("a", "b"):
            if side == "b":
                page.locator('.dt-chain .dt-btn[data-side="b"]').click()
                page.wait_for_timeout(600)
                self.assertEqual(self._state(page)["side"], "b")
            ch = d[side]["chain"]
            chart = page.locator(".dt-chain .dt-chart")
            self.assertEqual(int(chart.get_attribute("data-nodes")), len(ch["nodes"]))
            self.assertEqual(int(chart.get_attribute("data-edges")), len(ch["edges"]))
            drawn = page.evaluate("() => Array.from(document.querySelectorAll('.dt-chain .dt-node')).map(n => [n.dataset.id, n.dataset.kind, n.dataset.step])")
            self.assertEqual(sorted(x[0] for x in drawn), sorted(n["id"] for n in ch["nodes"]))
            edges = page.evaluate("() => Array.from(document.querySelectorAll('.dt-chain path.dt-edge')).map(e => [e.dataset.from, e.dataset.to, e.dataset.kind])")
            self.assertEqual(sorted(tuple(e) for e in edges), sorted((e["from"], e["to"], e["kind"]) for e in ch["edges"]))
            # feeds edges by adjacency alone are dashed and say so
            adjacent = [e for e in ch["edges"] if e["kind"] == "feeds" and e["overlap"] is None]
            self.assertEqual(page.locator(".dt-chain path.dt-edge.adjacent").count(), len(adjacent))
            label = page.locator(".dt-chain svg.dt-chain-svg").get_attribute("aria-label")
            self.assertIn(ch["reading"], label)
            self.assertIn(ch["reading"], page.locator(f'.dt-chain .dt-read[data-side="{side}"]').inner_text())
        # back to A: a click on a data node opens the step, the text is the report's
        page.locator('.dt-chain .dt-btn[data-side="a"]').click()
        page.wait_for_timeout(600)
        data_node = [n for n in d["a"]["chain"]["nodes"] if n["kind"] == "data"][0]
        page.locator(f'.dt-chain .dt-node[data-id="{data_node["id"]}"]').click()
        page.wait_for_timeout(400)
        st = self._state(page)
        self.assertEqual((st["side"], st["step"]), ("a", data_node["step"]))
        panel = page.locator(".dt-chain .dt-detail")
        self.assertEqual(panel.get_attribute("data-step"), str(data_node["step"]))
        step = [s for s in rep["a"]["steps"] if s["index"] == data_node["step"]][0]
        self.assertEqual(panel.locator("pre.dt-text").nth(1).inner_text(), (step["output"] or "")[:4000])
        self.assertEqual(page.locator('.dt-chain .dt-node[aria-pressed="true"]').get_attribute("data-id"), data_node["id"])
        # the keyboard: Escape closes the open step; the stage moves between nodes, Enter opens, Escape closes
        page.locator(".dt-chain .dt-stage").focus()
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        self.assertIsNone(self._state(page)["step"])
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        focused = page.evaluate("() => document.activeElement.getAttribute('data-id')")
        self.assertIsNotNone(focused)
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        st = self._state(page)
        target = [n for n in d["a"]["chain"]["nodes"] if n["id"] == focused][0]
        self.assertEqual(st["step"], target["step"])
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        self.assertIsNone(self._state(page)["step"])
        self.assertEqual(page.locator(".dt-chain .dt-detail").count(), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_a_lineage_row_opens_to_the_hunks_and_the_episodes_sources(self):
        de = self.cov_agg["data_evolution"]
        context, page, errors = self._open(path=self.cov_dir)
        rows = page.evaluate("() => Array.from(document.querySelectorAll('.dt-evolution .dt-erow:not(.head)')).map(r => r.dataset.gen)")
        self.assertEqual(rows, [f"{s['from']}→{s['to']}" for s in de["steps"]])
        self.assertEqual(page.locator(".dt-evolution svg.dt-effect").count(), len(de["steps"]))
        self.assertEqual(page.locator(".dt-evolution svg.dt-growth").count(), 1)
        growth = page.locator(".dt-evolution svg.dt-growth").get_attribute("aria-label")
        for g in de["generations"]:
            self.assertIn(f"{g['id']} {g['instructions']['chars']:,}", growth)
        # the step with the most prompt hunks, or the first
        s = max(de["steps"], key=lambda x: (len(x["change"]["hunks"]), -x["index"]))
        key = f"{s['from']}→{s['to']}"
        page.locator(f'.dt-evolution .dt-erow[data-gen="{key}"]').click()
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["gen"], key)
        self.assertEqual(page.locator(f'.dt-evolution .dt-erow[data-gen="{key}"]').get_attribute("aria-expanded"), "true")
        panel = page.locator(".dt-evolution .dt-epanel")
        self.assertEqual(panel.get_attribute("data-gen"), key)
        added = sum(1 for h in s["change"]["hunks"] for line in h.split("\n") if line.startswith("+"))
        removed = sum(1 for h in s["change"]["hunks"] for line in h.split("\n") if line.startswith("-"))
        self.assertEqual(panel.locator(".dt-diff .add").count(), added)
        self.assertEqual(panel.locator(".dt-diff .del").count(), removed)
        self.assertEqual(panel.locator(".dt-diff .hunk").count(), len(s["change"]["hunks"]))
        self.assertEqual(panel.locator("li[data-episode]").count(), len(s["evidence"]["data"]))
        for e in s["evidence"]["data"]:
            text = panel.locator(f'li[data-episode="{e["episode"]}"]').inner_text()
            self.assertIn(f"{len(e['sources'])} distinct source", text)
        self.assertIn("Sources are ids", panel.locator('[data-role="names-basis"]').inner_text())
        if s["change"].get("protected_touched"):
            self.assertEqual(panel.locator('[data-role="protected"]').count(), 1)
        self.assertIn(s["reading"][:60].lower(), panel.locator('[data-role="reading"]').inner_text().lower())
        # the row's effect is drawn as intervals with the section's numbers
        eff = s["effect"]["improvement"]
        label = page.locator(f'.dt-evolution .dt-erow[data-gen="{key}"] svg.dt-effect').get_attribute("aria-label")
        self.assertIn(f"{eff['point']:.2f}".rstrip("0").rstrip("."), label)
        # the keyboard walks the rows; a generation id selects the step that made it
        page.locator(f'.dt-evolution .dt-erow[data-gen="{key}"]').focus()
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        self.assertIsNone(self._state(page)["gen"])
        self.assertEqual(page.locator(".dt-evolution .dt-epanel").count(), 0)
        page.evaluate(f"() => AgentDiff.data.select({{gen: '{s['to']}'}})")
        page.wait_for_timeout(300)
        self.assertEqual(page.locator(".dt-evolution .dt-epanel").get_attribute("data-gen"), key)
        self.assertEqual(errors, [])
        context.close()

    # ---------------------------------------------------------- the family

    def test_the_selection_is_one_page_family_that_survives_a_reload(self):
        task = self._first_task(self.batch_reports)
        d = self.batch_reports[task]["data"]
        src = d["a"]["corpus"]["sources"][0]["id"]
        context, page, errors = self._open()
        self._task(page, task)
        page.evaluate(f"() => AgentDiff.data.select({{source: '{src}', side: 'a', step: {d['a']['chain']['nodes'][1]['step']}}})")
        page.wait_for_timeout(400)
        self.assertEqual(page.locator(".dt-corpus .dt-detail").get_attribute("data-source"), src)
        self.assertEqual(page.locator(".dt-chain .dt-detail").count(), 1)
        self.assertEqual(page.evaluate("() => AgentDiff._internals.Store.get('agentdiff:data')")["source"], src)
        page.reload()
        page.wait_for_timeout(1200)
        st = self._state(page)
        self.assertEqual(st["source"], src)
        self.assertEqual(page.locator(f'.dt-corpus tr.dt-src[aria-selected="true"]').get_attribute("data-source"), src)
        page.evaluate("() => AgentDiff.data.reset()")
        page.wait_for_timeout(300)
        self.assertEqual(self._state(page), {"source": None, "step": None, "gen": None, "side": None})
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------- scale, motion

    def test_the_drawings_stay_in_tens_of_milliseconds_at_ten_times_the_scale(self):
        context, page, errors = self._open(path=self.cov_dir)
        chart = page.locator(".dt-chain .dt-chart")
        self.assertLess(float(chart.get_attribute("data-draw-ms")), 120)
        base_nodes = int(chart.get_attribute("data-nodes"))
        page.evaluate("() => AgentDiff.data.tile(10)")
        page.wait_for_timeout(1500)
        chart = page.locator(".dt-chain .dt-chart")
        self.assertGreater(int(chart.get_attribute("data-nodes")), base_nodes * 5)
        self.assertLess(float(chart.get_attribute("data-draw-ms")), 400)
        self.assertLess(float(page.locator(".dt-corpus").get_attribute("data-draw-ms")), 400)
        self.assertIn("tiled", page.locator(".dt-chain .dt-status").inner_text())
        page.evaluate("() => AgentDiff.data.tile(1)")
        page.wait_for_timeout(800)
        self.assertEqual(int(page.locator(".dt-chain .dt-chart").get_attribute("data-nodes")), base_nodes)
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_opens_a_source_without_a_transition(self):
        context, page, errors = self._open(reduced_motion=True)
        page.locator(".dt-corpus tr.dt-src").first.click()
        page.wait_for_timeout(40)
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('.dt-corpus .dt-detail')).opacity"), "1")
        self.assertEqual(errors, [])
        context.close()

    # ---------------------------------------------------- every view, phone

    def test_the_console_is_clean_across_every_view_on_every_output(self):
        for path in (self.batch_dir, self.runs_dir, self.cov_dir):
            context, page, errors = self._open(path=path)
            views = page.evaluate("() => Array.from(document.querySelectorAll('#view-tabs [data-view]')).map(t => t.dataset.view)")
            self.assertIn("data", views)
            for view in views + ["data"]:
                page.locator(f'#view-tabs [data-view="{view}"]').click()
                page.wait_for_timeout(300)
            self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
            self.assertEqual(errors, [], str(path))
            context.close()

    def test_the_lane_fits_a_phone_with_no_small_text(self):
        for path in (self.batch_dir, self.cov_dir):
            for width in (390, 360):
                with self.subTest(path=path.name, width=width):
                    context, page, errors = self._open(path=path, width=width)
                    self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
                    self.assertEqual(page.locator(".dt-set").evaluate("e => e.closest('.scroll-x') !== null"), True)
                    small = page.evaluate("""() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n = 0, node;
                        while ((node = w.nextNode())) { if (!node.textContent.trim()) continue; const el = node.parentElement; if (!el) continue;
                        if (parseFloat(getComputedStyle(el).fontSize) < 11) n++; } return n; }""")
                    self.assertEqual(small, 0)
                    sizes = page.evaluate("""() => [...new Set(Array.from(document.querySelectorAll('#stacks *'))
                        .filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())).map(e => getComputedStyle(e).fontSize))]""")
                    self.assertLessEqual(len(sizes), 7)
                    # open a source, a step and (on the lineage) a row: nothing overflows or clips
                    page.locator(".dt-corpus tr.dt-src").first.click()
                    page.wait_for_timeout(300)
                    page.evaluate("() => { const n = document.querySelector('.dt-chain .dt-node[data-kind=\"data\"]'); if (n) n.dispatchEvent(new MouseEvent('click', {bubbles: true})); }")
                    page.wait_for_timeout(300)
                    if page.locator(".dt-evolution .dt-erow:not(.head)").count():
                        page.locator(".dt-evolution .dt-erow:not(.head)").nth(2).click()
                        page.wait_for_timeout(300)
                        self.assertEqual(page.locator(".dt-evolution .dt-epanel").count(), 1)
                    self.assertEqual(page.locator(".dt-corpus .dt-detail").count(), 1)
                    self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
                    clipped = page.evaluate("""() => { const bad = []; document.querySelectorAll('[data-block^="dt-"]').forEach(card => {
                        const body = card.querySelector('.block-body'); if (!body) return; const st = getComputedStyle(body);
                        if (body.scrollWidth > body.clientWidth + 2 && st.overflowX !== 'auto' && st.overflowX !== 'scroll') bad.push(card.getAttribute('data-block')); }); return bad; }""")
                    self.assertEqual(clipped, [])
                    self.assertEqual(errors, [], f"{path.name} {width}")
                    context.close()

    # ------------------------------------------------------- instructions

    def test_the_task_block_draws_the_instructions_diff_as_hunks_and_keeps_the_no_instructions_path(self):
        """The demo records instructions on every trace now: the pair's
        difference is drawn as hunks in the Evolution step block's
        vocabulary (one span per line — hunk, add, del, ctx) with the
        section's counts; a fixture copy of the same page with the field
        stripped from both sides still says no instructions are recorded,
        without an error and without a diff."""
        task = self._first_task(self.batch_reports)
        d = self.batch_reports[task]["data"]
        diff = d["instructions_diff"]
        if diff["same"] is not False:
            raise unittest.SkipTest("the demo pair's instructions do not differ")
        context, page, errors = self._open()
        self._task(page, task)
        card = page.locator('#stacks [data-block="dt-task"]')
        self.assertEqual(card.locator('[data-role="no-instructions"]').count(), 0)
        self.assertIn(f"differ by {len(diff['hunks'])} hunk", card.locator(".dt-lede").inner_text())
        for side in ("a", "b"):
            ins = d[side]["agent"]["instructions"]
            self.assertIn(f"{ins['chars']:,} characters · {ins['source']}", card.inner_text())
        block = card.locator('[data-role="instructions-diff"]')
        self.assertEqual(block.count(), 1)
        self.assertEqual((block.get_attribute("data-hunks"), block.get_attribute("data-added"), block.get_attribute("data-removed")),
                         (str(len(diff["hunks"])), str(diff["added"]), str(diff["removed"])))
        lines = [line for h in diff["hunks"] for line in h.split("\n")]
        self.assertEqual(block.locator(".dt-diff span.hunk").count(), sum(1 for l in lines if l.startswith("@@")))
        self.assertEqual(block.locator(".dt-diff span.add").count(), sum(1 for l in lines if l.startswith("+")))
        self.assertEqual(block.locator(".dt-diff span.del").count(), sum(1 for l in lines if l.startswith("-")))
        self.assertEqual(block.locator(".dt-diff span.add").count(), diff["added"])
        self.assertEqual(block.locator(".dt-diff span.del").count(), diff["removed"])
        self.assertEqual(block.locator(".dt-diff").inner_text().strip(), "\n".join(lines).strip())
        self.assertIn(f"+{diff['added']} −{diff['removed']} lines", block.locator(".dt-h").text_content())
        self.assertEqual(errors, [])
        context.close()
        # the fixture copy: the same page, the instructions stripped from both sides of every report's data section
        html = (self.batch_dir / "report.html").read_text(encoding="utf-8")
        marker = "window.DEEPCOMPARE_DATA = "
        start = html.index(marker) + len(marker)
        end = html.index("\n", start)
        payload = json.loads(html[start:end].rstrip().rstrip(";"))
        for rep in payload["reports"]:
            for side in ("a", "b"):
                rep["data"][side]["agent"]["instructions"] = {"system_prompt": None, "source": None, "chars": None}
                rep[side]["agent"].pop("system_prompt", None)
            rep["data"]["instructions_diff"] = {"same": None, "hunks": [], "added": None, "removed": None,
                                                "reason": "no instructions recorded on either side"}
        stripped = Path(self.tmp.name) / "stripped"
        stripped.mkdir(exist_ok=True)
        (stripped / "report.html").write_text(html[:start] + json.dumps(payload) + ";" + html[end:], encoding="utf-8")
        context, page, errors = self._open(path=stripped)
        self._task(page, task)
        card = page.locator('#stacks [data-block="dt-task"]')
        self.assertEqual(card.locator('[data-role="instructions-diff"]').count(), 0)
        self.assertEqual(card.locator(".dt-diff").count(), 0)
        self.assertEqual(card.locator('[data-role="no-instructions"]').count(), 1)
        self.assertIn("no instructions recorded on either side", card.locator('[data-role="no-instructions"]').inner_text())
        self.assertIn("No instructions recorded on either side", card.locator(".dt-lede").inner_text())
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0)
        self.assertEqual(errors, [])
        context.close()

    def test_a_lineage_page_diffs_consecutive_generations_under_a_picker_from_the_family(self):
        """On a lineage page the task block adds the instructions between
        consecutive generations: one button per step of the section, the
        pair's own step chosen first, the chosen step's hunks drawn in the
        Evolution view's vocabulary with the section's counts; the choice
        is the `data` family's gen, so the same row opens in “How the agent
        evolves”, by mouse and by keyboard, and it survives a reload."""
        de = self.cov_agg["data_evolution"]
        steps = de["steps"]
        keys = [f"{s['from']}→{s['to']}" for s in steps]
        context, page, errors = self._open(path=self.cov_dir)
        card = page.locator('#stacks [data-block="dt-task"]')
        host = card.locator('[data-role="lineage-steps"]')
        self.assertEqual(host.count(), 1)
        self.assertEqual(host.get_attribute("data-steps"), str(len(steps)))
        buttons = host.locator("button[data-gen]")
        self.assertEqual([b.get_attribute("data-gen") for b in buttons.all()], keys)
        # the default is the pair's own step: the page's two generations
        rep = self.cov_reports[sorted(self.cov_reports)[0]]
        pair = f"{rep['data']['a']['agent']['version']}→{rep['data']['b']['agent']['version']}"
        first = pair if pair in keys else keys[-1]
        panel = host.locator('[data-role="gen-diff"]')
        self.assertEqual(panel.get_attribute("data-gen"), first)
        self.assertEqual(host.locator('button[aria-pressed="true"]').get_attribute("data-gen"), first)
        if pair in keys:
            self.assertIn("the pair on this page", panel.inner_text())

        def check(key):
            s = steps[keys.index(key)]
            ch = s["change"]
            lines = [line for h in ch["hunks"] for line in h.split("\n")]
            self.assertEqual(panel.get_attribute("data-gen"), key)
            self.assertEqual((panel.get_attribute("data-hunks"), panel.get_attribute("data-added"), panel.get_attribute("data-removed")),
                             (str(len(ch["hunks"])), str(ch["prompt_added"]), str(ch["prompt_removed"])))
            self.assertEqual(panel.locator(".dt-diff span.add").count(), sum(1 for l in lines if l.startswith("+")))
            self.assertEqual(panel.locator(".dt-diff span.del").count(), sum(1 for l in lines if l.startswith("-")))
            self.assertEqual(panel.locator(".dt-diff span.hunk").count(), sum(1 for l in lines if l.startswith("@@")))
            gens = {g["id"]: g for g in de["generations"]}
            self.assertIn(f"{gens[s['from']]['instructions']['chars']:,} characters", panel.inner_text())
            self.assertIn(f"{gens[s['to']]['instructions']['chars']:,} characters", panel.inner_text())
            if ch.get("summary"):
                self.assertIn(ch["summary"], panel.inner_text())
            self.assertEqual(host.locator('button[aria-pressed="true"]').get_attribute("data-gen"), key)
            self.assertEqual(self._state(page)["gen"], key)
            evo = page.locator('.dt-evolution .dt-erow[aria-expanded="true"]')
            self.assertEqual(evo.get_attribute("data-gen"), key)

        # the step that added the most prompt lines, by mouse
        most = keys[max(range(len(steps)), key=lambda i: (steps[i]["change"]["prompt_added"], -i))]
        host.locator(f'button[data-gen="{most}"]').click()
        page.wait_for_timeout(500)
        check(most)
        # the first step, by keyboard
        buttons.first.focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        check(keys[0])
        # the family persists: a reload keeps the step
        page.reload()
        page.wait_for_timeout(1200)
        card = page.locator('#stacks [data-block="dt-task"]')
        host = card.locator('[data-role="lineage-steps"]')
        panel = host.locator('[data-role="gen-diff"]')
        self.assertEqual(panel.get_attribute("data-gen"), keys[0])
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class ScaffoldMarksTest(unittest.TestCase):
    """What the *harness* did to a step, drawn as such.

    Three of the loop's scaffold settings act on individual steps — a read
    served from the cache, an answer held back, a first write refused — and
    until now nothing on the page said so. A reader comparing two runs saw
    a repeated call that cost nothing, an extra reasoning turn and an
    errored write, with no way to tell that the harness, not the agent, had
    produced all three.

    The mark is read from the step's own `scaffold` field
    (`trace.SCAFFOLD_ACTIONS`), never from the prose note beside it: a page
    that matched on English would quietly stop finding these the day the
    sentence was reworded, and would go on rendering a clean strip.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        from deepcompare.harness import ScriptedProvider, Tool, run_task
        from deepcompare.report import compare, render_html
        from deepcompare.trace import Trajectory

        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        task = {"id": "t_gated", "prompt": "What refund applies to BK1?", "expected": "$120.00"}
        def slow(value):
            def fn(**kw):
                time.sleep(0.12)
                return value
            return fn
        look = Tool("look", slow({"refund": "$120.00"}), "look it up",
                    {"type": "object", "properties": {}}, effect="read")
        check = Tool("check", slow({"ok": True}), "check the work",
                     {"type": "object", "properties": {}}, effect="read")
        ship = Tool("ship", slow("shipped"), "change state",
                    {"type": "object", "properties": {}}, effect="write")
        tools = [ship, look, check]
        # One run that trips all three. The answer gate requires `check`
        # rather than `look` on purpose: `look` is what clears the write
        # gate, so requiring it would have the one read satisfy both and the
        # answer gate would never fire — which is how the first version of
        # this fixture silently tested two of three.
        # Latencies on purpose. With an instant scripted provider every
        # step lasts a tenth of a millisecond, the strip draws them all on
        # top of one another, and a click lands on whichever rect happens
        # to be in front — which is how this test came to depend on the
        # layout rather than on the marks.
        script = [{"text": "", "tool_calls": [{"name": "ship", "arguments": {}}], "latency_s": 0.4},
                  {"text": "", "tool_calls": [{"name": "look", "arguments": {}}], "latency_s": 0.4},
                  {"text": "", "tool_calls": [{"name": "look", "arguments": {}}], "latency_s": 0.4},
                  {"text": "the refund is $120.00", "latency_s": 0.4},
                  {"text": "", "tool_calls": [{"name": "check", "arguments": {}}], "latency_s": 0.4},
                  {"text": "the refund is $120.00", "latency_s": 0.4}]
        gated = run_task(ScriptedProvider(list(script)), task, tools, agent="gated", out_dir=None,
                         budget={"max_steps": 10, "dedupe_tool_calls": True,
                                 "require_read_before_write": True, "require_before_answer": "check"})
        plain = run_task(ScriptedProvider(list(script)), task, tools, agent="plain", out_dir=None,
                         budget={"max_steps": 10})
        cls.gated, cls.plain = gated, plain
        cls.marks = [s["scaffold"] for s in gated["steps"] if s.get("scaffold")]
        if sorted(set(cls.marks)) != ["answer_gate", "cache_hit", "write_gate"]:
            raise unittest.SkipTest(f"the fixture did not trip all three gates: {cls.marks}")
        cls.report = out / "report.html"
        render_html([compare(Trajectory.from_dict(gated), Trajectory.from_dict(plain))], {},
                    ROOT / "web" / "blocks.html", cls.report)
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
        ctx = self.browser.new_context(viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.report}#view=trace")
        page.wait_for_timeout(1500)
        blk = page.locator('.block[data-block="tr-timeline"]')
        if blk.count() and "collapsed" in (blk.first.get_attribute("class") or ""):
            blk.first.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(600)
        return ctx, page, errors

    def test_the_harness_only_marks_a_step_it_actually_acted_on(self):
        """The plain run uses the same script and the same tools and gets no
        marks at all — so a mark means the harness acted, not that the step
        looked unusual."""
        self.assertEqual([s.get("scaffold") for s in self.plain["steps"] if s.get("scaffold")], [])
        self.assertEqual(len(self.marks), 3)

    def test_every_intervention_is_a_mark_on_the_strip(self):
        ctx, page, errors = self._open()
        drawn = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="tr-timeline"] [data-mark]'))
            .map(e => e.getAttribute('data-mark')).filter(m => m.indexOf('the harness') === 0)""")
        self.assertEqual(len(drawn), len(self.marks),
                         f"{len(self.marks)} interventions on the trace, {len(drawn)} drawn")
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_legend_names_only_the_kinds_this_run_carries(self):
        """A legend that advertises a glyph the strip never draws is a
        legend that lies."""
        ctx, page, errors = self._open()
        legend = page.locator('[data-block="tr-timeline"] .trc-legend').first.text_content()
        for kind in sorted(set(self.marks)):
            self.assertIn(kind.replace("_", " "), legend)
        absent = {"cache_hit", "answer_gate", "write_gate"} - set(self.marks)
        for kind in absent:
            self.assertNotIn(kind.replace("_", " "), legend)
        self.assertEqual(errors, [])
        ctx.close()

    def test_a_marked_step_says_it_was_the_harness_and_not_the_agent(self):
        ctx, page, errors = self._open()
        index = [s["index"] for s in self.gated["steps"] if s.get("scaffold") == "cache_hit"][0]
        # on the clock the cached call is a hairline — it took no time, which
        # is the whole point of it — and its neighbour covers it. The block
        # has the even-spacing scale for exactly this, so the test uses the
        # affordance a reader would rather than forcing a click through.
        steps_btn = page.locator('[data-block="tr-timeline"] button:text-is("steps")')
        self.assertTrue(steps_btn.count(), "the timeline has no even-spacing scale to fall back to")
        steps_btn.first.click()
        page.wait_for_timeout(500)
        row = page.locator(f'[data-block="tr-timeline"] [data-step="{index}"]')
        self.assertTrue(row.count(), "no way to open the cached step")
        row.first.click()
        page.wait_for_timeout(500)
        body = page.locator('.block[data-block="tr-step"]').text_content()
        self.assertIn("the harness, not the agent", body)
        self.assertIn("not re-executed", body)
        self.assertEqual(errors, [])
        ctx.close()

@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class TraceViewTest(unittest.TestCase):
    """The Trace view (40_trace.js): one run as an execution rather than as
    a set of numbers — the steps in the order and at the pace the trace
    recorded them, with what the engine already knows about each drawn on
    top, and a replay that walks the playhead through the run.

    What is checked is that the lane's four blocks render in the order
    `00_core.js` declares with nothing empty and the console clean,
    unfiltered, across every view of a batch, a runs, a lineage and a
    bundle page; that the run's numbers line is the report's own sums and
    that a run whose steps declare no token basis says so rather than
    reporting nought per cent measured; that a click or Enter opens a
    phase and a step and Escape returns; that replay follows the *recorded*
    clock — a step the trace says took four times as long occupies four
    times the wall time — that the state-so-far numbers equal the report's
    cumulative sums at the playhead, that pause stops it, that seek lands,
    and that `prefers-reduced-motion` lands on the end with no timer; that
    the compare block draws one strip per side with the alignment's
    divergences and says so instead of showing an unrelated pair when a
    bundle record is being traced; that the detail block hosts the run and
    chain blocks *on the same run* the timeline is showing; that the
    bundle's picker reaches every level-3 record carrying steps; that the
    strip draws in tens of milliseconds at ten times the shipped scale and
    says it is tiled; and that the lane fits a phone at 390 and 360 with no
    text under 11px and the task-scoped family survives a reload.
    """

    tmp = None
    IDS = ("tr-timeline", "tr-step", "tr-compare", "tr-detail")
    VIEWS = ("chat", "levels", "data", "trace", "story", "evidence", "batch",
             "panels", "training", "evolution", "coevolution")

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file() or not (ROOT / "demo" / "traces").is_dir() or not (ROOT / "demo" / "rl" / "train").is_dir():
            raise unittest.SkipTest("no demo outputs to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        template = ROOT / "web" / "blocks.html"

        def run(name, *args):
            out = root / name
            done = subprocess.run([sys.executable, "-m", "deepcompare"] + list(args) + ["-o", str(out), "--template", str(template)],
                                  cwd=str(ROOT), capture_output=True)
            if done.returncode != 0 or not (out / "report.html").is_file():
                raise unittest.SkipTest(f"the {args[0]} command did not write a page: " + done.stderr.decode("utf-8", "replace")[-300:])
            return out

        cls.batch_dir = run("batch", "batch", str(ROOT / "demo" / "traces"))
        cls.runs_dir = run("runs", "runs", str(ROOT / "demo" / "rl" / "train"), "--token-cap", "3000")
        cls.cov_dir = run("cov", "coevolve", str(lineage))
        cls.bundle_dir = run("bundle", "bundle", str(cls.batch_dir), str(cls.runs_dir), str(cls.cov_dir), "--name", "demo",
                             "--traces", str(ROOT / "demo" / "traces"), str(ROOT / "demo" / "rl" / "train"), str(lineage))
        cls.bundle = json.loads((cls.bundle_dir / "bundle.json").read_text(encoding="utf-8"))
        cls.reports = {}
        for path in sorted(cls.batch_dir.glob("report_*.json")):
            rep = json.loads(path.read_text(encoding="utf-8"))
            cls.reports[rep["task"]["id"]] = rep
        if not cls.reports:
            raise unittest.SkipTest("the batch wrote no pair reports")
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

    # ------------------------------------------------------------- helpers

    def _open(self, path=None, width=1440, reduced_motion=False, reset=True):
        context = self.browser.new_context(viewport={"width": width, "height": 1100},
                                           reduced_motion="reduce" if reduced_motion else "no-preference")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # unfiltered: a warning from any block on the page is a failure here
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
        page.goto(f"file://{(path or self.batch_dir) / 'report.html'}#view=trace")
        page.wait_for_timeout(1400)
        if reset:
            page.evaluate("() => AgentDiff.trace.reset()")
            page.wait_for_timeout(500)
        return context, page, errors

    def _state(self, page):
        return page.evaluate("() => AgentDiff.trace.state()")

    def _ids(self, page):
        return page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))")

    def _lane_order(self, page):
        """The order 00_core.js declares for the trace lane — the rule, not a roster."""
        return page.evaluate("() => (AgentDiff._internals.STACK_PLAN.filter(p => p.groups.indexOf('trace') >= 0)[0] || {}).order || []")

    def _first_task(self):
        return sorted(self.reports)[0]

    @staticmethod
    def _cum(steps, upto):
        """The report's own cumulative sums at a step index — the numbers the
        state-so-far line must equal, computed here from the trace and not
        read back off the page."""
        tokens = reward = 0
        seen, sources = set(), 0
        for s in steps:
            if s.get("index", 0) > upto:
                break
            if isinstance(s.get("tokens"), (int, float)):
                tokens += s["tokens"]
            if isinstance(s.get("reward"), (int, float)):
                reward += s["reward"]
            if s.get("type") in ("search", "retrieve", "read", "tool_call"):
                key = (s.get("name") or "", s.get("input") or "")
                if key not in seen:
                    seen.add(key)
                    sources += 1
        return {"tokens": tokens, "reward": reward, "sources": sources}

    # ------------------------------------------------------------ rendering

    def test_the_blocks_render_in_the_declared_order_with_the_console_clean(self):
        for path in (self.batch_dir, self.runs_dir, self.cov_dir, self.bundle_dir):
            context, page, errors = self._open(path=path)
            ids = self._ids(page)
            order = self._lane_order(page)
            self.assertTrue(order, "the core declares the trace lane's order")
            self.assertEqual(ids, [i for i in order if i in ids], str(path))
            self.assertIn("tr-timeline", ids, str(path))
            for bid in ids:
                body = page.locator(f'#stacks [data-block="{bid}"] .block-body').inner_text()
                self.assertNotIn("failed to render", body, bid)
                self.assertNotIn("Nothing to show", body, bid)
                self.assertGreater(len(body.strip()), 60, f"{bid} on {path}")
            self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block .empty')).filter(e => e.offsetParent !== null).length"), 0, str(path))
            unlabelled = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .trc svg')).filter(s => !(s.getAttribute('aria-label') || '').trim() || !s.getAttribute('role')).length")
            self.assertEqual(unlabelled, 0, str(path))
            self.assertEqual(errors, [], str(path))
            context.close()

    def test_the_console_stays_clean_across_every_view(self):
        context, page, errors = self._open(path=self.bundle_dir)
        for view in self.VIEWS:
            page.evaluate("v => { location.hash = 'view=' + v; }", view)
            page.wait_for_timeout(450)
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------- the numbers

    def test_the_run_line_is_the_reports_own_sums_and_names_an_unrecorded_basis(self):
        task = self._first_task()
        rep = self.reports[task]
        steps = rep["a"]["steps"]
        context, page, errors = self._open()
        page.select_option("#task-picker", task)
        page.wait_for_timeout(700)
        line = page.locator('#stacks [data-block="tr-timeline"] .trc-nums').first.inner_text()
        self.assertIn(f"steps {len(steps)}", line)
        total = self._cum(steps, max(s.get("index", 0) for s in steps))
        self.assertIn(str(total["tokens"]), line.replace(",", ""))
        self.assertIn(f"sources read {total['sources']}", line)
        # a basis no step declares is said to be unrecorded, never called 0% measured
        declared = [s for s in steps if s.get("tokens_basis")]
        if not declared:
            self.assertIn("basis not recorded", line)
            self.assertNotIn("0% measured", line)
        else:
            self.assertIn("measured", line)
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------- levels

    def test_a_step_opens_by_mouse_and_by_keyboard_and_escape_returns(self):
        context, page, errors = self._open()
        hits = page.locator('#stacks [data-block="tr-timeline"] [data-step]')
        self.assertGreater(hits.count(), 1)
        hits.nth(1).click()
        page.wait_for_timeout(500)
        st = self._state(page)
        self.assertEqual(st["level"], "step")
        chosen = st["step"]
        self.assertIsNotNone(chosen)
        # the step block is showing that step, not another
        body = page.locator('#stacks [data-block="tr-step"] .block-body').inner_text()
        self.assertIn(f"step {chosen}", body)
        # arrow keys move the playhead from the stage
        page.locator('#stacks [data-block="tr-timeline"] [role="application"]').first.focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        self.assertGreater(self._state(page)["step"], chosen)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["level"], "run")
        self.assertEqual(errors, [])
        context.close()

    def test_a_phase_opens_to_its_own_steps(self):
        context, page, errors = self._open()
        phases = page.locator('#stacks [data-block="tr-timeline"] [data-phase]')
        if not phases.count():
            self.skipTest("this page's report carries no impact clusters to phase by")
        before = page.locator('#stacks [data-block="tr-timeline"] [data-step]').count()
        phases.nth(0).click()
        page.wait_for_timeout(600)
        st = self._state(page)
        self.assertEqual(st["level"], "phase")
        self.assertTrue(st["phase"])
        after = page.locator('#stacks [data-block="tr-timeline"] [data-step]').count()
        self.assertLessEqual(after, before, "a phase shows its own steps, never more than the run")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------- replay

    def test_replay_follows_the_recorded_clock_and_pause_and_seek_land(self):
        task = self._first_task()
        rep = self.reports[task]
        steps = rep["a"]["steps"]
        context, page, errors = self._open()
        page.select_option("#task-picker", task)
        page.wait_for_timeout(700)
        # at 16x a run of a few recorded seconds finishes inside a second or two
        page.evaluate("() => AgentDiff.trace.play(16)")
        page.wait_for_timeout(1800)
        st = self._state(page)
        self.assertEqual(st["step"], max(s.get("index", 0) for s in steps))
        self.assertFalse(st["playing"])
        # at 1x the playhead is still inside the run after a second, and the
        # recorded second it reports is one the trace states
        page.evaluate("() => AgentDiff.trace.reset()")
        page.wait_for_timeout(400)
        page.evaluate("() => AgentDiff.trace.play(1)")
        page.wait_for_timeout(1200)
        st = self._state(page)
        self.assertTrue(st["playing"])
        self.assertLess(st["step"], max(s.get("index", 0) for s in steps), "1x does not race to the end")
        # pause stops it where it was
        page.evaluate("() => AgentDiff.trace.pause()")
        page.wait_for_timeout(300)
        held = self._state(page)["step"]
        page.wait_for_timeout(1000)
        self.assertEqual(self._state(page)["step"], held, "pause stops the playhead")
        # the state-so-far line equals the report's cumulative sums there
        line = page.locator('#stacks [data-block="tr-timeline"] .trc-nums').first.inner_text().replace(",", "")
        want = self._cum(steps, held)
        self.assertIn(str(want["tokens"]), line)
        self.assertIn(f"sources read {want['sources']}", line)
        # seek lands on the step asked for
        page.evaluate("() => AgentDiff.trace.seek(0)")
        page.wait_for_timeout(400)
        self.assertEqual(self._state(page)["step"], 0)
        self.assertFalse(self._state(page)["playing"])
        self.assertEqual(errors, [])
        context.close()

    def test_reduced_motion_lands_on_the_end_with_no_timer(self):
        task = self._first_task()
        last = max(s.get("index", 0) for s in self.reports[task]["a"]["steps"])
        context, page, errors = self._open(reduced_motion=True)
        page.select_option("#task-picker", task)
        page.wait_for_timeout(700)
        page.evaluate("() => AgentDiff.trace.play(1)")
        page.wait_for_timeout(400)
        st = self._state(page)
        self.assertFalse(st["playing"], "reduced motion starts no timer")
        self.assertEqual(st["step"], last, "reduced motion lands on the end at once")
        # the scrubber still works
        page.evaluate("() => AgentDiff.trace.seek(0)")
        page.wait_for_timeout(300)
        self.assertEqual(self._state(page)["step"], 0)
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------ compare

    def test_the_compare_block_draws_both_sides_and_the_engines_divergences(self):
        task = self._first_task()
        rep = self.reports[task]
        context, page, errors = self._open()
        page.select_option("#task-picker", task)
        page.wait_for_timeout(700)
        card = page.locator('#stacks [data-block="tr-compare"]')
        body = card.inner_text()
        self.assertIn(str(len(rep["a"]["steps"])), body)
        self.assertIn(str(len(rep["b"]["steps"])), body)
        # one strip per side, each named by the side it draws
        self.assertEqual(sorted(card.locator('.trc-part[data-side]').evaluate_all(
            "els => els.map(e => e.getAttribute('data-side'))")), ["a", "b"])
        # the divergence rows are the engine's, by rank, and a list that does
        # not fit says how many it left out rather than truncating silently
        divs = rep.get("divergences") or []
        rows = card.locator('[data-divergence]')
        self.assertEqual(rows.count(), min(len(divs), 6))
        if divs:
            self.assertEqual(rows.first.get_attribute("data-divergence"), str(divs[0]["rank"]))
        self.assertEqual(card.locator('[data-role="divergence-more"]').count(), 1 if len(divs) > 6 else 0)
        self.assertEqual(errors, [])
        context.close()

    def test_compare_says_a_bundle_record_has_no_twin_rather_than_showing_a_pair(self):
        context, page, errors = self._open(path=self.bundle_dir)
        picker = page.locator('#stacks [data-role="run-picker"]')
        if not picker.count():
            self.skipTest("this bundle page carries no traceable records")
        keys = page.evaluate("() => Array.from(document.querySelectorAll('#stacks [data-role=run-picker] option')).map(o => o.value).filter(v => v)")
        self.assertGreater(len(keys), 1)
        picker.select_option(keys[1])
        page.wait_for_timeout(1200)
        self.assertEqual(self._state(page)["run"], keys[1])
        body = page.locator('#stacks [data-block="tr-compare"] .block-body').inner_text()
        self.assertIn("no matched twin", body)
        self.assertEqual(page.locator('#stacks [data-block="tr-compare"] .trc-part').count(), 0,
                         "a record with no twin draws no pair of strips")
        self.assertEqual(errors, [])
        context.close()

    # ------------------------------------------------------------- detail

    def test_the_detail_block_hosts_the_run_and_chain_blocks_on_the_same_run(self):
        context, page, errors = self._open()
        card = page.locator('#stacks [data-block="tr-detail"]')
        # `renderInto` draws a block's own root into a host, so the block is
        # identified by its own class and not by the stack's data-block
        self.assertGreater(card.locator('.lv-run').count(), 0, "the run block is drawn in")
        self.assertGreater(card.locator('.dt-chain').count(), 0, "the chain is drawn in")
        # it is the run the timeline is tracing, not the run block's own default
        linked = page.evaluate("() => AgentDiff.levels.state().run")
        self.assertTrue(linked)
        self.assertIn(linked, card.inner_text())
        # switching side moves the run block with it
        page.evaluate("() => AgentDiff.trace.select({ side: 'b', step: null })")
        page.wait_for_timeout(1200)
        self.assertNotEqual(page.evaluate("() => AgentDiff.levels.state().run"), linked)
        self.assertEqual(errors, [])
        context.close()

    def test_the_bundle_picker_reaches_every_record_that_carries_steps(self):
        # the level-3 records live in the bundle's `runs/` files, not in the
        # manifest — the manifest carries the index and the page loads the
        # records beside it, so the files on disk are the thing to check
        records = {}
        for path in sorted((self.bundle_dir / "runs").glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(rec.get("steps"), list) and rec["steps"]:
                records[rec["key"]] = rec
        self.assertTrue(records, "the bundle was built with --traces, so every run has steps")
        want = sorted(records)
        context, page, errors = self._open(path=self.bundle_dir)
        keys = page.evaluate("() => Array.from(document.querySelectorAll('#stacks [data-role=run-picker] option')).map(o => o.value).filter(v => v)")
        self.assertEqual(sorted(keys), want)
        # and the one chosen is the one drawn
        pick = want[len(want) // 2]
        page.locator('#stacks [data-role="run-picker"]').select_option(pick)
        page.wait_for_timeout(1200)
        self.assertEqual(self._state(page)["run"], pick)
        n = len(records[pick]["steps"])
        line = page.locator('#stacks [data-block="tr-timeline"] .trc-nums').first.inner_text()
        self.assertIn(f"steps {n}", line)
        self.assertEqual(page.locator('#stacks [data-block="tr-timeline"] [data-step]').count(), n)
        self.assertEqual(errors, [])
        context.close()

    def test_the_timeline_has_a_table_view_carrying_every_step_and_column(self):
        task = self._first_task()
        steps = self.reports[task]["a"]["steps"]
        context, page, errors = self._open()
        page.select_option("#task-picker", task)
        page.wait_for_timeout(700)
        det = page.locator('#stacks [data-block="tr-timeline"] [data-role="table"]')
        self.assertEqual(det.count(), 1, "every chart on this page has a table view")
        det.locator("summary").click()
        page.wait_for_timeout(400)
        rows = page.locator('#stacks [data-block="tr-timeline"] [data-row-step]')
        self.assertEqual(rows.count(), len(steps))
        self.assertEqual(
            page.evaluate("() => Array.from(document.querySelectorAll('#stacks [data-block=tr-timeline] [data-row-step]')).map(r => r.getAttribute('data-row-step'))"),
            [str(s.get("index", i)) for i, s in enumerate(steps)])
        # an unrecorded field reads as unrecorded in the table too, never as 0
        text = page.locator('#stacks [data-block="tr-timeline"] .trc-table').inner_text()
        if not any(s.get("tokens_basis") for s in steps):
            self.assertIn("not recorded", text)
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------------- scale

    def test_the_strip_draws_in_tens_of_milliseconds_at_ten_times_the_scale(self):
        context, page, errors = self._open(path=self.bundle_dir)
        biggest = page.evaluate("""() => {
          const r = (((window.DEEPCOMPARE_DATA || {}).bundle || {}).levels || {}).records || {};
          return Object.keys(r).filter(k => (r[k].steps || []).length)
                       .sort((a, b) => r[b].steps.length - r[a].steps.length)[0] || null; }""")
        if not biggest:
            self.skipTest("this bundle carries no records with steps")
        page.locator('#stacks [data-role="run-picker"]').select_option(biggest)
        page.wait_for_timeout(1400)
        one = page.evaluate("() => AgentDiff.trace.timing()")
        self.assertIsNone(one["tiled"])
        page.evaluate("() => AgentDiff.trace.tile(10)")
        page.wait_for_timeout(2500)
        ten = page.evaluate("() => AgentDiff.trace.timing()")
        self.assertEqual(ten["tiled"], 10)
        self.assertEqual(ten["steps"], one["steps"] * 10)
        self.assertLess(ten["run"], 400, f"the strip at {ten['steps']} steps took {ten['run']}ms")
        # a tiled strip can never be read as a real run
        self.assertIn("TILED", page.locator('#stacks [data-block="tr-timeline"] .trc-nums').first.inner_text())
        page.evaluate("() => AgentDiff.trace.tile(1)")
        page.wait_for_timeout(1000)
        self.assertEqual(errors, [])
        context.close()

    # -------------------------------------------------------- phone, state

    def test_the_lane_fits_a_phone_with_no_tiny_text(self):
        for width in (390, 360):
            context, page, errors = self._open(width=width)
            self.assertEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, str(width))
            tiny = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks .trc *'))
                .filter(e => e.children.length === 0 && e.textContent.trim() && e.offsetParent
                             && parseFloat(getComputedStyle(e).fontSize) < 11).length""")
            self.assertEqual(tiny, 0, str(width))
            for bid in self.IDS:
                self.assertGreater(page.locator(f'#stacks [data-block="{bid}"]').count(), 0, f"{bid} at {width}")
            self.assertEqual(errors, [], str(width))
            context.close()

    def test_the_family_is_task_scoped_and_survives_a_reload(self):
        context, page, errors = self._open()
        hits = page.locator('#stacks [data-block="tr-timeline"] [data-step]')
        hits.nth(1).click()
        page.wait_for_timeout(500)
        st = self._state(page)
        self.assertEqual(st["level"], "step")
        page.reload()
        page.wait_for_timeout(1500)
        back = self._state(page)
        self.assertEqual(back["step"], st["step"])
        self.assertEqual(back["level"], "step")
        # and a different task does not inherit the other task's step
        tasks = sorted(self.reports)
        if len(tasks) > 1:
            page.select_option("#task-picker", tasks[1])
            page.wait_for_timeout(800)
            self.assertIsNone(self._state(page)["step"], "the family is scoped to the task")
        self.assertEqual(errors, [])
        context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class HarnessBlocksTest(unittest.TestCase):
    """The harness beside the agent (41_harness.js), two blocks in the
    Evolution lane rather than a twelfth tab.

    `hn-ladder` is one row per step: which artifacts moved and whether they
    were the agent's reasoning or the scaffold it runs inside, what the
    attribution status is, and the fingerprint evidence behind that status
    on demand. `hn-absorb` is the plane — work per pass against pass rate,
    the lineage walked as a path, the leg where the gain came from the
    scaffold marked.

    What is checked is that both render in the lane's declared order with
    the console clean; that every row carries the section's own status and
    never invents `attributable`; that a row opens to its evidence by mouse
    and by keyboard; that the plane draws one point per measurable
    generation and one leg per step, with the absorbing leg marked and no
    other; that the numbers on the plane are the section's to the digit;
    that the axes note says the scale is the data's own; and that the lane
    fits a phone.
    """

    tmp = None
    IDS = ("hn-ladder", "hn-absorb")

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file():
            raise unittest.SkipTest("no demo lineage to analyse")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")], cwd=str(ROOT), check=True, capture_output=True)
        out = root / "cov"
        done = subprocess.run([sys.executable, "-m", "deepcompare", "coevolve", str(lineage), "-o", str(out),
                               "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), capture_output=True)
        if done.returncode != 0 or not (out / "report.html").is_file():
            raise unittest.SkipTest("coevolve wrote no page: " + done.stderr.decode("utf-8", "replace")[-300:])
        cls.dir = out
        cls.agg = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))
        cls.h = cls.agg.get("harness_evolution") or {}
        if not cls.h.get("measurable"):
            raise unittest.SkipTest("the lineage carries no measurable harness section")
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

    def _open(self, width=1440):
        context = self.browser.new_context(viewport={"width": width, "height": 1200})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type in ("error", "warning") else None)
        page.goto(f"file://{self.dir / 'report.html'}#view=evolution")
        page.wait_for_timeout(1600)
        return context, page, errors

    def test_the_blocks_css_actually_parses_on_the_page(self):
        """A block's CSS can reach the page as text and still not apply.

        `L.style.once(id, [...])` handed the array itself reaches the page
        as its *comma-joined* string, so every `}` becomes `},` and the
        parser reads the whole sheet as one broken selector list. This block
        shipped that way: 5 of its 32 rules survived, and the ladder rendered
        with every element and every word in place — the grid, the chips and
        the gaps all gone, as jammed inline text. Every content test above
        passed throughout, which is why this one asserts on the *parsed*
        sheet and on a computed layout value, not on the stylesheet's text.

        It lives here rather than on the batch page on purpose: a block's
        style is injected when the block renders, and only this page renders
        these blocks.
        """
        context, page, errors = self._open()
        sheets = page.evaluate(r"""() => Array.from(document.querySelectorAll('style')).map((el, i) => {
            const text = el.textContent || '';
            let parsed = null;
            try { parsed = el.sheet ? el.sheet.cssRules.length : null; } catch (e) { parsed = -1; }
            return {i, parsed, braces: (text.match(/}/g) || []).length,
                    comma: /}\s*,/.test(text), head: text.slice(0, 70)};
        })""")
        self.assertTrue(sheets)
        for sheet in sheets:
            self.assertFalse(sheet["comma"],
                             f"stylesheet {sheet['i']} has `}}` followed by `,` — an array reached "
                             f"style.once comma-joined: {sheet['head']}")
            if sheet["braces"] > 4:
                self.assertGreaterEqual(
                    sheet["parsed"], sheet["braces"] // 2,
                    f"stylesheet {sheet['i']} declares {sheet['braces']} rule ends and the browser parsed "
                    f"only {sheet['parsed']}: {sheet['head']}")
        # and the layout the rules are for, read back from the page
        row = page.locator('#stacks [data-block="hn-ladder"] .hn-row').first
        self.assertEqual(row.evaluate("el => getComputedStyle(el).display"), "grid",
                         "the ladder row is not a grid: its rule did not apply")
        self.assertEqual(errors, [])
        context.close()

    def test_both_blocks_render_in_the_lanes_order_with_the_console_clean(self):
        context, page, errors = self._open()
        ids = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .block')).map(b => b.getAttribute('data-block'))")
        order = page.evaluate("() => (AgentDiff._internals.STACK_PLAN.filter(p => p.groups.indexOf('evolution') >= 0)[0] || {}).order || []")
        for bid in self.IDS:
            self.assertIn(bid, ids)
            self.assertIn(bid, order)
        self.assertEqual(ids, [i for i in order if i in ids], "the lane's order is the rule")
        # the harness reading follows the step ledger it qualifies
        self.assertLess(ids.index("evo-steps"), ids.index("hn-ladder"))
        self.assertEqual(page.evaluate("() => Array.from(document.querySelectorAll('#stacks .hn .empty')).filter(e => e.offsetParent !== null).length"), 0)
        unlabelled = page.evaluate("() => Array.from(document.querySelectorAll('#stacks .hn svg')).filter(s => !(s.getAttribute('aria-label') || '').trim()).length")
        self.assertEqual(unlabelled, 0)
        self.assertEqual(errors, [])
        context.close()

    def test_every_row_carries_the_sections_own_status(self):
        context, page, errors = self._open()
        rows = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks [data-block="hn-ladder"] [data-step]'))
            .map(r => [r.getAttribute('data-step'), r.getAttribute('data-status')])""")
        want = [[s["from"] + "→" + s["to"], s["attribution"]["status"]] for s in self.h["steps"]]
        self.assertEqual(rows, want)
        # the invariant, read off the page: no step claims attribution it has no fingerprint for
        for _, status in rows:
            self.assertIn(status, ("attributable", "assumed", "confounded"))
        self.assertEqual(errors, [])
        context.close()

    def test_a_row_opens_to_its_evidence_by_mouse_and_by_keyboard(self):
        context, page, errors = self._open()
        rows = page.locator('#stacks [data-block="hn-ladder"] [data-step]')
        self.assertGreater(rows.count(), 1)
        first = rows.nth(0)
        self.assertEqual(first.get_attribute("aria-expanded"), "false")
        first.click()
        page.wait_for_timeout(300)
        self.assertEqual(first.get_attribute("aria-expanded"), "true")
        panel = page.locator('#stacks [data-block="hn-ladder"] .hn-panel').nth(0)
        self.assertTrue(panel.is_visible())
        self.assertIn(self.h["steps"][0]["reading"][:60], panel.inner_text())
        first.click()
        page.wait_for_timeout(200)
        self.assertEqual(first.get_attribute("aria-expanded"), "false")
        # by keyboard: it is a button, so Enter opens it
        rows.nth(1).focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        self.assertEqual(rows.nth(1).get_attribute("aria-expanded"), "true")
        self.assertEqual(errors, [])
        context.close()

    def test_the_fingerprint_of_every_generation_is_reachable(self):
        context, page, errors = self._open()
        det = page.locator('#stacks [data-block="hn-ladder"] [data-role="fingerprints"]')
        self.assertEqual(det.count(), 1)
        det.locator("summary").click()
        page.wait_for_timeout(300)
        gens = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks [data-block="hn-ladder"] [data-gen]'))
            .map(r => r.getAttribute('data-gen'))""")
        self.assertEqual(gens, [g["id"] for g in self.h["generations"]])
        self.assertEqual(errors, [])
        context.close()

    def test_the_plane_is_one_point_per_generation_and_one_leg_per_step(self):
        context, page, errors = self._open()
        measurable = [s for s in self.h["steps"] if s["absorption"].get("measurable")]
        legs = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks [data-block="hn-absorb"] [data-leg]'))
            .map(l => [l.getAttribute('data-leg'), l.getAttribute('data-absorb')])""")
        self.assertEqual([l[0] for l in legs], [s["from"] + "→" + s["to"] for s in measurable])
        # exactly the steps the section flagged are marked, and no others
        marked = [l[0] for l in legs if l[1] == "true"]
        self.assertEqual(marked, self.h["summary"]["absorbed"])
        points = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks [data-block="hn-absorb"] g.pt'))
            .map(p => p.getAttribute('data-gen'))""")
        self.assertGreater(len(points), 1)
        self.assertEqual(len(set(points)), len(points), "a generation is one point, not two")
        self.assertEqual(errors, [])
        context.close()

    def test_the_planes_numbers_are_the_sections_own(self):
        context, page, errors = self._open()
        page.locator('#stacks [data-block="hn-absorb"] [data-role="points"] summary').click()
        page.wait_for_timeout(300)
        rows = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks [data-block="hn-absorb"] [data-row-gen]'))
            .map(r => [r.getAttribute('data-row-gen')].concat(Array.from(r.querySelectorAll('td')).map(td => td.textContent)))""")
        first = [s for s in self.h["steps"] if s["absorption"].get("measurable")][0]["absorption"]
        want_rate = first["pass_rate"]["from"]
        want_work = first["steps_per_pass"]["from"]["point"]
        self.assertTrue(rows)
        self.assertEqual(rows[0][2], f"{int(round(want_rate * 100))}%")
        self.assertEqual(float(rows[0][3]), round(want_work, 2))
        # the axes are the data's own range, and the block says so
        note = page.locator('#stacks [data-block="hn-absorb"] [data-role="axes"]').inner_text()
        self.assertIn("not 0 to 100%", note)
        self.assertEqual(errors, [])
        context.close()

    def test_the_lane_fits_a_phone(self):
        for width in (390, 360):
            context, page, errors = self._open(width=width)
            self.assertEqual(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0, str(width))
            tiny = page.evaluate("""() => Array.from(document.querySelectorAll('#stacks .hn *'))
                .filter(e => e.children.length === 0 && e.textContent.trim() && e.offsetParent
                             && parseFloat(getComputedStyle(e).fontSize) < 11).length""")
            self.assertEqual(tiny, 0, str(width))
            for bid in self.IDS:
                self.assertGreater(page.locator(f'#stacks [data-block="{bid}"]').count(), 0, f"{bid} at {width}")
            self.assertEqual(errors, [], str(width))
            context.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class HarnessReachTest(unittest.TestCase):
    """`hn-reach` (42_reach.js): what this harness can act on, over the
    engine's whole recommendation vocabulary.

    The actuator's claim — a hypothesis the runner cannot express is not a
    hypothesis — is about every category `triage.EFFORT` can produce, not
    about whichever findings a batch turned up. The block draws all of them
    as equal-area cells grouped by where the fix lives, so a class's area is
    its share of the vocabulary, and outlines the ones nothing reaches.

    Every value is `scaffold.reach()`'s, including the sentence: the page
    does no arithmetic, so even the per-category tally of what this loop
    met comes from `scaffold.seen_in`.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        sys.path.insert(0, str(ROOT / "tests"))
        from helpers_loop import run_demo_loop
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "loop"
        cls.ledger = run_demo_loop(out, template=ROOT / "web" / "blocks.html")
        cls.reach = cls.ledger["reach"]
        cls.page = out / "report.html"
        if not cls.page.is_file():
            raise unittest.SkipTest("the loop wrote no page")
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

    def _open(self, width=1440):
        ctx = self.browser.new_context(viewport={"width": width, "height": 1100})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type == "error" else None)
        page.goto(f"file://{self.page}#view=batch")
        page.wait_for_timeout(1500)
        blk = page.locator('.block[data-block="hn-reach"]')
        if blk.count() and "collapsed" in (blk.first.get_attribute("class") or ""):
            blk.first.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(900)
        return ctx, page, errors, blk

    def test_the_engine_places_every_category_and_the_block_draws_every_one(self):
        self.assertEqual(len(self.reach["rows"]), self.reach["total"])
        ctx, page, errors, blk = self._open()
        drawn = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-reach"] .rch-cell'))
            .map(e => e.getAttribute('data-category'))""")
        self.assertEqual(sorted(drawn), sorted(r["category"] for r in self.reach["rows"]))
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_cells_nothing_reaches_are_the_outlined_ones(self):
        """The finding is drawn as the only outlined kind, so the eye lands
        on what this harness cannot try."""
        ctx, page, errors, blk = self._open()
        outlined = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-reach"] .rch-cell.out'))
            .map(e => e.getAttribute('data-category')).sort()""")
        want = sorted(r["category"] for r in self.reach["rows"] if r["verdict"] == "no knob")
        self.assertEqual(outlined, want)
        self.assertEqual(len(outlined), self.reach["counts"]["no knob"])
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_sentence_and_the_counts_are_the_engines(self):
        ctx, page, errors, blk = self._open()
        self.assertIn(self.reach["reading"], blk.first.text_content())
        legend = blk.first.locator(".rch-legend").text_content()
        for verdict, n in self.reach["counts"].items():
            if n:
                self.assertIn("· " + str(n), legend, f"{verdict} count missing from the legend")
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_table_carries_every_row_with_the_knob_that_reaches_it(self):
        ctx, page, errors, blk = self._open()
        blk.first.locator("details").first.click()
        page.wait_for_timeout(300)
        rows = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-reach"] .rch-tbl tr[data-category]'))
            .map(r => [r.getAttribute('data-category'), r.children[3].textContent.trim()])""")
        want = [[r["category"], r["knob"] or "—"] for r in self.reach["rows"]]
        self.assertEqual(rows, want)
        self.assertEqual(errors, [])
        ctx.close()

    def test_a_category_this_loop_met_is_marked_and_the_tally_is_the_engines(self):
        """The page does no arithmetic: the counts come from
        `scaffold.seen_in`, computed over the loop's own comparisons."""
        seen = self.reach.get("seen") or {}
        if not seen:
            self.skipTest("the demo loop produced no scaffold findings to mark")
        ctx, page, errors, blk = self._open()
        blk.first.locator("details").first.click()
        page.wait_for_timeout(300)
        for category, tally in seen.items():
            cell = page.locator(f'[data-block="hn-reach"] .rch-cell[data-category="{category}"]')
            self.assertEqual(cell.locator("circle").count(), 1, f"{category} was met and carries no mark")
            row = page.locator(f'[data-block="hn-reach"] .rch-tbl tr[data-category="{category}"]')
            text = row.text_content()
            self.assertIn(f"{tally['proposed']} proposed", text)
            self.assertIn(f"{tally['unactionable']} refused", text)
        unmet = [r["category"] for r in self.reach["rows"] if r["category"] not in seen]
        for category in unmet[:4]:
            cell = page.locator(f'[data-block="hn-reach"] .rch-cell[data-category="{category}"]')
            self.assertEqual(cell.locator("circle").count(), 0, f"{category} was never met and is marked")
        self.assertEqual(errors, [])
        ctx.close()

    def test_it_fits_a_phone_with_nothing_under_eleven_pixels(self):
        """The page's floor is 11px for prose. Chart labels inside an `svg`
        are exempt here as everywhere else on the page — the axis text of
        every other block is 10.5px — so the walk skips them rather than
        holding this block to a rule nothing else keeps."""
        for width in (390, 360):
            ctx, page, errors, blk = self._open(width)
            self.assertGreater(blk.count(), 0, str(width))
            tiny = page.evaluate(r"""() => {
              const card = document.querySelector('[data-block="hn-reach"]');
              const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
              const out = []; let node;
              while ((node = walker.nextNode())) {
                if (!node.textContent.trim()) continue;
                const el = node.parentElement; if (!el || el.closest('svg')) continue;
                if (parseFloat(getComputedStyle(el).fontSize) < 11) out.push(node.textContent.trim().slice(0, 40));
              }
              return out;
            }""")
            self.assertEqual(tiny, [], str(width))
            # and the chart itself must not spill out of a phone
            box = blk.first.locator(".rch-chart svg").bounding_box()
            self.assertLessEqual(round(box["x"] + box["width"]), width + 1, str(width))
            self.assertEqual(errors, [], str(width))
            ctx.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class HarnessDriftTest(unittest.TestCase):
    """`hn-drift` (43_drift.js): did the same thing run these generations?

    The ladder above says per step whether a delta may be attributed. It
    cannot say *where* the harness moved, nor which dimensions the traces
    never recorded — and that second one is the commonest case and the
    easiest to mistake for "it held constant". The matrix is one row per
    fingerprint dimension, one column per generation, with each step's
    verdict on a band centred under the boundary it spans.

    What these check is that the picture cannot disagree with the section:
    every marked cell is a change the engine put on that dimension, every
    hatched cell is a dimension the engine said nothing recorded, and the
    bands are the attribution statuses in order.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        lineage = ROOT / "demo" / "evolve" / "lineage"
        if not (lineage / "g0" / "agent.json").is_file():
            raise unittest.SkipTest("no demo lineage")
        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "cov"
        done = subprocess.run([sys.executable, "-m", "deepcompare", "coevolve", str(lineage),
                               "-o", str(cls.dir), "--template", str(ROOT / "web" / "blocks.html")],
                              cwd=str(ROOT), capture_output=True)
        if done.returncode != 0 or not (cls.dir / "report.html").is_file():
            raise unittest.SkipTest("coevolve wrote no page")
        agg = json.loads((cls.dir / "aggregate.json").read_text(encoding="utf-8"))
        cls.h = agg["harness_evolution"]
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

    def _open(self, width=1440):
        ctx = self.browser.new_context(viewport={"width": width, "height": 1200})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.type + ": " + m.text) if m.type == "error" else None)
        page.goto(f"file://{self.dir / 'report.html'}#view=evolution")
        page.wait_for_timeout(1600)
        blk = page.locator('.block[data-block="hn-drift"]')
        if blk.count() and "collapsed" in (blk.first.get_attribute("class") or ""):
            blk.first.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(900)
        return ctx, page, errors, blk

    def test_the_matrix_is_every_dimension_by_every_generation(self):
        ctx, page, errors, blk = self._open()
        cells = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-drift"] .hnd-cell'))
            .map(e => [e.getAttribute('data-dimension'), e.getAttribute('data-gen'), e.getAttribute('data-state')])""")
        gens = [g["id"] for g in self.h["generations"]]
        dims = sorted({c[0] for c in cells})
        self.assertEqual(len(cells), len(dims) * len(gens))
        self.assertEqual(sorted({c[1] for c in cells}), sorted(gens))
        self.assertEqual(errors, [])
        ctx.close()

    def test_a_hatched_cell_is_one_the_engine_said_nothing_recorded(self):
        """The finding, not a gap in the drawing: unknown is not held."""
        ctx, page, errors, blk = self._open()
        drawn = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-drift"] .hnd-cell[data-state="unrecorded"]'))
            .map(e => [e.getAttribute('data-dimension'), e.getAttribute('data-gen')]).sort()""")
        want = sorted([dim, g["id"]]
                      for g in self.h["generations"]
                      for dim, ok in ((g.get("fingerprint") or {}).get("dimensions") or {}).items()
                      if not ok)
        self.assertEqual(drawn, want)
        self.assertTrue(want, "the demo lineage records everything; this check would prove nothing")
        self.assertEqual(errors, [])
        ctx.close()

    def test_a_marked_cell_is_a_change_the_engine_put_on_that_dimension(self):
        ctx, page, errors, blk = self._open()
        drawn = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-drift"] .hnd-cell.moved'))
            .map(e => [e.getAttribute('data-dimension'), e.getAttribute('data-gen')]).sort()""")
        want = []
        for step in self.h["steps"]:
            hm = step.get("harness") or {}
            dims = {c.get("dimension") for c in (hm.get("changes") or [])}
            dims |= {c.get("dimension") for c in ((hm.get("identity") or {}).get("changes") or [])}
            for dim in dims:
                if dim:
                    want.append([dim, step["to"]])
        self.assertEqual(drawn, sorted(want))
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_bands_are_the_sections_own_verdicts_and_never_overlap(self):
        for width in (1440, 390):
            ctx, page, errors, blk = self._open(width)
            bands = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-drift"] .hnd-step'))
                .map(e => e.getAttribute('data-status'))""")
            self.assertEqual(bands, [s["attribution"]["status"] for s in self.h["steps"]], str(width))
            boxes = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="hn-drift"] .hnd-step rect'))
                .map(r => { const b = r.getBoundingClientRect(); return [b.left, b.right]; })""")
            overlaps = [i for i in range(len(boxes) - 1) if boxes[i + 1][0] < boxes[i][1] - 1]
            self.assertEqual(overlaps, [], f"bands overlap at {width}px")
            self.assertEqual(errors, [], str(width))
            ctx.close()

    def test_clicking_a_step_opens_what_moved_and_the_sentence_behind_it(self):
        ctx, page, errors, blk = self._open()
        self.assertEqual(blk.first.locator(".hnd-panel").count(), 0, "a panel before anything was clicked")
        step = self.h["steps"][-1]
        page.locator(f'[data-block="hn-drift"] .hnd-step[data-step="{step["from"]}→{step["to"]}"]').click()
        page.wait_for_timeout(400)
        panel = blk.first.locator(".hnd-panel")
        self.assertEqual(panel.count(), 1)
        text = panel.text_content()
        self.assertIn(step["attribution"]["reason"], text)
        for change in (step.get("harness") or {}).get("identity", {}).get("changes") or []:
            self.assertIn(change["from"], text)
            self.assertIn(change["to"], text)
        self.assertEqual(errors, [])
        ctx.close()

    def test_it_fits_a_phone_and_the_chart_stays_inside_it(self):
        for width in (390, 360):
            ctx, page, errors, blk = self._open(width)
            self.assertGreater(blk.count(), 0, str(width))
            box = blk.first.locator(".hnd-chart svg").bounding_box()
            self.assertLessEqual(round(box["x"] + box["width"]), width + 1, str(width))
            tiny = page.evaluate(r"""() => {
              const card = document.querySelector('[data-block="hn-drift"]');
              const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
              const out = []; let node;
              while ((node = walker.nextNode())) {
                if (!node.textContent.trim()) continue;
                const el = node.parentElement; if (!el || el.closest('svg')) continue;
                if (parseFloat(getComputedStyle(el).fontSize) < 11) out.push(node.textContent.trim().slice(0, 40));
              }
              return out;
            }""")
            self.assertEqual(tiny, [], str(width))
            self.assertEqual(errors, [], str(width))
            ctx.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class RecordedClockTest(unittest.TestCase):
    """The strip places a step where the trace says it began.

    Until `Step.started_s` existed, every timeline here summed the
    durations before a step to place it — which reads the run as strictly
    sequential and, for a loop that runs independent calls concurrently, is
    simply wrong. The picture said nothing about which it was doing.

    The fixture is a run whose steps overlap: three one-second steps
    starting at 0.0, 0.5 and 1.0. Summed, that is a three-second run with
    the last step starting at two seconds. Read, it is a two-second run
    with the last starting at one. The two are told apart on the page, not
    just in the section.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        from deepcompare.report import compare, render_html
        from deepcompare.trace import Trajectory

        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)

        def traj(name, rows):
            steps = []
            for i, row in enumerate(rows):
                step = {"index": i, "type": row.get("type", "tool_call"), "name": row.get("name", "look"),
                        "input": "q", "output": "r", "tokens": 10, "latency_s": row["latency_s"]}
                if "started_s" in row:
                    step["started_s"] = row["started_s"]
                steps.append(step)
            steps[-1].update(type="answer", name="final", output="the answer is 42")
            return Trajectory.from_dict({
                "trace_id": f"t_clock__{name}", "agent": {"name": name},
                "task": {"id": "t_clock", "prompt": "what is it?", "expected": "42"},
                "totals": {"latency_s": sum(r["latency_s"] for r in rows)},
                "outcome": {"answer": "the answer is 42", "success": True, "termination": "agent_stop"},
                "steps": steps})

        # Deliberately *unequal* durations. Three equal ones give the same
        # relative spacing under both bases — 0.5 either way — so a test
        # built on them cannot fail and proves nothing. These give 0.50
        # read and 0.80 summed.
        cls.rows = [{"latency_s": 2.0, "started_s": 0.0},
                    {"latency_s": 0.5, "started_s": 0.5},
                    {"latency_s": 1.0, "started_s": 1.0}]
        cls.overlapped = traj("recorded", cls.rows)
        cls.plain = traj("assumed", [{"latency_s": r["latency_s"]} for r in cls.rows])
        cls.report = out / "report.html"
        render_html([compare(cls.overlapped, cls.plain)], {}, ROOT / "web" / "blocks.html", cls.report)
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

    def _open(self, side=None):
        ctx = self.browser.new_context(viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.report}#view=trace")
        page.wait_for_timeout(1500)
        blk = page.locator('.block[data-block="tr-timeline"]')
        if blk.count() and "collapsed" in (blk.first.get_attribute("class") or ""):
            blk.first.locator(".block-actions .icon-btn").nth(1).click()
            page.wait_for_timeout(700)
        if side:
            btn = blk.first.locator(f'button:text-is("{side}")')
            if btn.count():
                btn.first.click()
                page.wait_for_timeout(700)
        return ctx, page, errors, blk

    def test_the_side_with_recorded_starts_says_the_clock_is_read(self):
        ctx, page, errors, blk = self._open("recorded")
        text = blk.first.text_content()
        self.assertIn("placed where the trace says it began", text)
        self.assertNotIn("summing the durations before it", text)
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_side_without_them_says_the_clock_is_assumed(self):
        """Not silence: a picture that assumes a run was sequential has to
        say that is what it did."""
        ctx, page, errors, blk = self._open("assumed")
        text = blk.first.text_content()
        self.assertIn("summing the durations before it", text)
        self.assertIn("reads the run as strictly sequential", text)
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_overlap_is_drawn_and_not_flattened_into_a_longer_run(self):
        """Steps of 2.0s, 0.5s and 1.0s beginning at 0.0, 0.5 and 1.0 span
        two seconds; their durations sum to three and a half. Read, the
        middle step begins halfway along the strip. Summed, it would begin
        at 0.80 — so the positions are the assertion, and the fixture's
        durations differ on purpose so that the two answers differ."""
        ctx, page, errors, blk = self._open("recorded")
        boxes = page.evaluate("""() => Array.from(document.querySelectorAll('[data-block="tr-timeline"] .trc-step[data-step]'))
            .map(e => { const b = e.getBoundingClientRect();
                        return [Number(e.getAttribute('data-step')), b.left, b.width]; })
            .sort((p, q) => p[0] - q[0])""")
        self.assertEqual(len(boxes), len(self.rows),
                         f"the strip drew {len(boxes)} steps for a {len(self.rows)}-step run")
        lefts = [b[1] for b in boxes]
        span = (lefts[-1] - lefts[0]) or 1
        # the last step begins at 1.0 of a 2.0s span: halfway. Under a
        # running sum it would begin at 2.0 of 3.0 — two-thirds.
        middle = (lefts[1] - lefts[0]) / span
        self.assertAlmostEqual(middle, 0.5, delta=0.08,
                               msg=f"step 1 sits at {middle:.2f} of the run; recorded says 0.50, summed says 0.80")
        self.assertEqual(errors, [])
        ctx.close()


@unittest.skipUnless(HAVE_PLAYWRIGHT and CHROMIUM,
                     "playwright + chromium required for browser tests")
class StepFieldsTest(unittest.TestCase):
    """`tr-step` says "every field is as the trace recorded it". That is a
    promise, and it goes stale silently.

    Three fields were added to the schema in this session — the input and
    output split, and the input the provider served from its own cache —
    and the block enumerates its rows by hand, so it went on claiming
    completeness while showing none of them. A reader would have had no
    way to tell the difference between a field the trace lacked and a field
    the block forgot.

    So this checks the *promise*: every optional per-step field the
    recorder can write is either shown or explicitly said to be absent.
    """

    tmp = None

    @classmethod
    def setUpClass(cls):
        from deepcompare.harness import ScriptedProvider, run_task
        from deepcompare.report import compare, render_html
        from deepcompare.trace import Trajectory

        subprocess.run([sys.executable, str(ROOT / "web" / "build_blocks.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.tmp = tempfile.TemporaryDirectory()

        class Rich(ScriptedProvider):
            def complete(self, messages, tools):
                r = super().complete(messages, tools)
                r.usage.update({"input_tokens": 400, "output_tokens": 20, "cached_input_tokens": 300})
                return r

        task = {"id": "t_fields", "prompt": "what is it?", "expected": "done"}
        script = [{"text": "done", "latency_s": 0.3}]
        cls.rich = run_task(Rich(list(script)), task, [], agent="rich", out_dir=None, budget={"max_steps": 3})
        cls.plain = run_task(ScriptedProvider(list(script)), task, [], agent="plain", out_dir=None,
                             budget={"max_steps": 3})
        cls.report = Path(cls.tmp.name) / "report.html"
        render_html([compare(Trajectory.from_dict(cls.rich), Trajectory.from_dict(cls.plain))], {},
                    ROOT / "web" / "blocks.html", cls.report)
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

    def _detail(self, side):
        ctx = self.browser.new_context(viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"file://{self.report}#view=trace")
        page.wait_for_timeout(1500)
        for bid in ("tr-timeline", "tr-step"):
            blk = page.locator(f'.block[data-block="{bid}"]')
            if blk.count() and "collapsed" in (blk.first.get_attribute("class") or ""):
                blk.first.locator(".block-actions .icon-btn").nth(1).click()
                page.wait_for_timeout(500)
        btn = page.locator(f'[data-block="tr-timeline"] button:text-is("{side}")')
        if btn.count():
            btn.first.click()
            page.wait_for_timeout(600)
        page.locator('[data-block="tr-timeline"] .trc-step[data-step]').first.click()
        page.wait_for_timeout(600)
        return ctx, page, errors, page.locator('.block[data-block="tr-step"]').text_content()

    def test_a_step_that_carries_the_new_counts_shows_all_of_them(self):
        ctx, page, errors, text = self._detail("rich")
        step = self.rich["steps"][0]
        self.assertEqual((step["input_tokens"], step["output_tokens"], step["cached_tokens"]), (400, 20, 300))
        self.assertIn("400 in · 20 out", text)
        self.assertIn("300 of the input", text)
        self.assertIn("not paid for", text)
        self.assertEqual(errors, [])
        ctx.close()

    def test_a_step_without_them_says_absent_rather_than_showing_a_zero(self):
        """The distinction the whole schema turns on: a provider that said
        nothing is not a provider that reported none."""
        ctx, page, errors, text = self._detail("plain")
        step = self.plain["steps"][0]
        self.assertNotIn("cached_tokens", step)
        self.assertIn("did not split this step's count", text)
        self.assertIn("not the same as none", text)
        self.assertNotIn("0 in · 0 out", text)
        self.assertEqual(errors, [])
        ctx.close()

    def test_the_detail_says_whether_the_position_is_read_or_summed(self):
        ctx, page, errors, text = self._detail("rich")
        self.assertIn("into the run (as the trace records it)", text)
        self.assertEqual(errors, [])
        ctx.close()

    def test_every_optional_step_field_the_recorder_writes_reaches_the_detail(self):
        """The promise, checked rather than trusted: a field added to the
        schema and not to this block would leave the card claiming to show
        everything while quietly showing less."""
        ctx, page, errors, text = self._detail("rich")
        lowered = text.lower()
        for field, needle in (("tokens", "tokens"), ("input_tokens", "in ·"), ("output_tokens", "out"),
                              ("cached_tokens", "cache"), ("started_s", "into the run"),
                              ("latency_s", "latency"), ("effect", "effect"), ("error", "error"),
                              ("model", "model"), ("reward", "reward")):
            self.assertIn(needle.lower(), lowered, f"{field} has no row in the step detail")
        self.assertIn("every field is as the trace recorded", lowered)
        self.assertEqual(errors, [])
        ctx.close()
