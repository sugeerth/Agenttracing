"""The web version (web/build_site.py): the playground's engine zip runs on its own, the samples are there, the
hosted report sets no cookies, and no model identifier is written. Pyodide itself is not in this test; the zip is imported by
CPython from the zip alone, which is the same import a browser makes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BuildSiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "web" / "build_site.py"), str(cls.out)], cwd=str(ROOT),
                       check=True, capture_output=True, timeout=900)
        cls.site = cls.out / "agentdiff"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_files_are_there(self):
        for name in ("try/index.html", "try/engine.zip", "try/blocks.html", "try/samples.json"):
            self.assertTrue((self.site / name).is_file(), name)
        manifest = json.loads((self.site / "try/samples.json").read_text())
        for pair in manifest["pairs"]:
            self.assertIn(pair["data"], ("synthetic", "recorded"))
            for side in ("a", "b"):
                self.assertTrue((self.site / "try/samples" / pair[side]).is_file())
        template = (self.site / "try/blocks.html").read_text(encoding="utf-8")
        self.assertIn("window.AGENTDIFF_DATA", template)
        self.assertIn('<meta name="agentdiff:no-cookies"', template.split("</head>")[0], "the hosted report sets none")

    def test_the_engine_zip_leaves_out_what_a_browser_cannot_run(self):
        names = zipfile.ZipFile(self.site / "try/engine.zip").namelist()
        self.assertIn("agentdiff/report.py", names)
        for left_out in ("agentdiff/harness/", "agentdiff/hub/", "agentdiff/commands/", "agentdiff/vendors.py"):
            self.assertFalse(any(n.startswith(left_out) for n in names), left_out)

    def test_the_engine_runs_from_the_zip_alone(self):
        """A clean interpreter, the repository off the path: what the playground runs, on a demo pair and on a
        trace in another format, rendered into the template."""
        code = (
            "import sys, json\n"
            f"sys.path = [p for p in sys.path if not p.startswith({str(ROOT)!r})]\n"
            f"sys.path.insert(0, {str(self.site / 'try/engine.zip')!r})\n"
            "from agentdiff.trace import Trajectory\n"
            "from agentdiff.report import compare, render_html\n"
            "from agentdiff import registry\n"
            "from agentdiff.claude_code import register_format; register_format()\n"
            "from agentdiff.adapters import register_formats; register_formats()\n"
            "assert 'Agenttracing/agentdiff' not in registry.__file__, registry.__file__\n"
            "load = lambda p: Trajectory.from_dict(registry.convert(json.load(open(p)))['trajectory'])\n"
            f"s = {str(self.site / 'try/samples')!r}\n"
            "rep = compare(load(s + '/t05_flight_duration__atlas-v2.json'), load(s + '/t05_flight_duration__bolt-v3.json'))\n"
            f"render_html([rep], {{}}, {str(self.site / 'try/blocks.html')!r}, sys.argv[1])\n"
            "print(json.dumps([rep['diagnosis']['subject'], rep['diagnosis']['decisive_step']['step']]))\n")
        out = self.out / "rendered.html"
        got = subprocess.run([sys.executable, "-I", "-c", code, str(out)], capture_output=True, text=True, timeout=600)
        self.assertEqual(got.returncode, 0, got.stderr[-2000:])
        self.assertEqual(json.loads(got.stdout), ["b", 1], "the same diagnosis the package gives")
        self.assertGreater(out.stat().st_size, 1_000_000)

    def test_the_playground_names_its_runtime_exactly(self):
        page = (self.site / "try/index.html").read_text(encoding="utf-8")
        self.assertIn("https://cdn.jsdelivr.net/npm/pyodide@0.29.5/pyodide.js", page)
        self.assertNotIn("innerHTML = l", page, "trace text is set as text, never as markup")


if __name__ == "__main__":
    unittest.main()
