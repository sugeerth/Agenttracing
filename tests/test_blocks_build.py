"""Tests for the composable blocks page build (v21).

``web/blocks.html`` is assembled from modules, and the properties that make
it usable are exactly the ones a concatenating build can silently break: it
must stay one offline-openable file, it must keep the single injection point
the CLI writes report data into, and one module must not be able to break
the page for the others.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
BLOCKS = WEB / "blocks"

from deepcompare.report import DATA_MARKER, render_html


def build() -> str:
    result = subprocess.run(
        [sys.executable, str(WEB / "build_blocks.py")],
        capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        raise AssertionError(f"build_blocks.py failed:\n{result.stdout}\n{result.stderr}")
    return (WEB / "blocks.html").read_text(encoding="utf-8")


class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = build()

    def test_every_module_is_inlined(self):
        modules = [p for p in sorted(BLOCKS.glob("*.js")) if not p.name.startswith("_")]
        self.assertTrue(modules, "no block modules found")
        for module in modules:
            self.assertIn(f"<!-- {module.name} -->", self.page,
                          f"{module.name} was not inlined")

    def test_exactly_one_injection_point(self):
        hits = [line for line in self.page.splitlines() if DATA_MARKER in line]
        self.assertEqual(len(hits), 1)

    def test_the_cli_can_inject_report_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.html"
            render_html([{"task": {"id": "t1"}}], {"tasks": 1},
                        WEB / "blocks.html", out)
            rendered = out.read_text(encoding="utf-8")
        self.assertIn('"t1"', rendered)
        self.assertEqual(
            len([l for l in rendered.splitlines() if DATA_MARKER in l]), 1)

    def test_no_external_resources(self):
        # A CDN reference would work on the machine that built it and fail on
        # the air-gapped one that needs to read the report.
        for pattern in (r"https?://[^\s\"')]+", r"src\s*=\s*[\"']//"):
            for match in re.findall(pattern, self.page):
                self.assertNotIn("cdn", match.lower())
                self.assertFalse(
                    re.search(r"\.(js|css|woff2?|png|svg)(\?|$)", match),
                    f"external asset referenced: {match}")

    def test_no_module_closes_the_script_tag_early(self):
        # A literal </script> inside a JS string ends the tag and silently
        # dumps the rest of the module into the document as text.
        body = self.page.split(DATA_MARKER, 1)[1]
        self.assertNotIn("</script><", body.replace("</script>\n", ""))
        opens = self.page.count("<script>")
        closes = self.page.count("</script>")
        self.assertEqual(opens, closes)

    def test_registered_ids_are_unique(self):
        ids = re.findall(r"id:\s*[\"']([a-z0-9-]+)[\"']", self.page)
        registered = [i for i in ids if i]
        self.assertEqual(len(registered), len(set(registered)),
                         "duplicate block id would be dropped at registration")

    def test_page_is_self_contained_and_small_enough_to_open(self):
        self.assertLess(len(self.page.encode("utf-8")), 4 * 1024 * 1024)
        self.assertIn("<!doctype html>", self.page.lower())


class TestShellContract(unittest.TestCase):
    """The shell and core must keep the guarantees modules are told to rely on."""

    def setUp(self):
        self.shell = (BLOCKS / "_shell.html").read_text(encoding="utf-8")
        self.core = (BLOCKS / "00_core.js").read_text(encoding="utf-8")

    def test_shell_has_both_build_markers(self):
        self.assertIn("<!--@CORE@-->", self.shell)
        self.assertIn("<!--@MODULES@-->", self.shell)

    def test_theme_tokens_are_defined_on_bare_root(self):
        # Defined only inside a media query, a token is undefined for anyone
        # whose OS theme does not match.
        root = self.shell.split(":root {", 1)[1].split("}", 1)[0]
        for token in ("--bg", "--surface", "--ink", "--rule", "--a", "--b",
                      "--good", "--bad", "--warn"):
            self.assertIn(token, root, f"{token} has no light-mode definition")

    def test_dark_theme_is_reachable_both_ways(self):
        self.assertIn("prefers-color-scheme: dark", self.shell)
        self.assertIn('[data-theme="dark"]', self.shell)

    def test_core_exposes_the_documented_api(self):
        for name in ("block:", "boot:"):
            self.assertIn(name, self.core)

    def test_core_has_no_network_calls(self):
        # The privacy claim in the You panel is only true if this holds.
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket",
                          "navigator.sendBeacon", "import("):
            self.assertNotIn(forbidden, self.core,
                             f"core must not use {forbidden}")

    def test_readme_documents_the_contract(self):
        readme = (BLOCKS / "README.md").read_text(encoding="utf-8")
        for term in ("relevance", "ctx.empty", "AgentDiff.block", "IIFE"):
            self.assertIn(term, readme)


if __name__ == "__main__":
    unittest.main()
