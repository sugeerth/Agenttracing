"""AgentDiff Live — the deployable demo page.

Built from the demo reports, it must carry the engine's precomputed
findings for its tasks, replay without a model wherever it is opened,
speak to nothing but the claude.ai runtime it is framed in, and say what
it is: a light browser diff, with the full story left to the engine.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class LivePageBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / "web" / "build_live.py")],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.page = (ROOT / "web" / "live.html").read_text(encoding="utf-8")
        m = re.search(r"var PRE = (\{.*?\});\n", cls.page, re.S)
        assert m, "no precomputed block"
        cls.pre = json.loads(m.group(1).replace("<\\/", "</"))

    def test_the_precomputed_findings_come_from_the_engine(self):
        for task in ("t05_flight_duration", "t01_acme_revenue"):
            p = self.pre[task]
            self.assertEqual(len(p["verdict"]), 5)
            self.assertIn(p["decisive"]["side"], ("a", "b"))
            self.assertIsInstance(p["decisive"]["step"], int)
            self.assertTrue(p["prompts"])
            self.assertTrue(p["agents"]["a"]["steps"] and p["agents"]["b"]["steps"])
            self.assertNotEqual(p["agents"]["a"]["success"], p["agents"]["b"]["success"])

    def test_the_page_is_self_contained_and_reaches_only_the_runtime(self):
        self.assertNotIn("<!doctype", self.page.lower(), "the artifact wraps the document itself")
        self.assertNotIn("<html", self.page.lower())
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource"):
            self.assertNotIn(forbidden, self.page)
        srcs = re.findall(r'src="(https?://[^"]+)"', self.page)
        self.assertEqual(srcs, [], "no external scripts")
        hrefs = re.findall(r'href="(https?://[^"]+)"', self.page)
        self.assertTrue(all(h.startswith("https://fonts.googleapis.com/") for h in hrefs), hrefs)
        for cap in ('claude.use("sample")', 'claude.use("db")', 'claude.use("downloads")'):
            self.assertIn(cap, self.page)

    def test_the_page_says_what_it_is(self):
        self.assertIn("shown, never judged", self.page)
        self.assertIn("browser-side", self.page)
        self.assertIn("deepcompare batch", self.page)
        self.assertIn("FINAL ANSWER", self.page)
        self.assertIn("tokens (est.)", self.page)
        self.assertIn("prefers-reduced-motion", self.page)
        self.assertIn('prefers-color-scheme: dark', self.page)
        self.assertIn('[data-theme="dark"]', self.page)


if __name__ == "__main__":
    unittest.main()
