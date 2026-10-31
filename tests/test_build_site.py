"""The web version (web/build_site.py): the repeatability dashboard's engine zip runs on its own, the three suites
are there and labelled, and no model identifier is written. Pyodide itself is not in this test; the zip is imported by
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
        for name in ("try/index.html", "try/engine.zip", "try/samples.json"):
            self.assertTrue((self.site / name).is_file(), name)
        manifest = json.loads((self.site / "try/samples.json").read_text())
        self.assertEqual([d["id"] for d in manifest["datasets"]], ["harness", "aa", "sim"])
        for d in manifest["datasets"]:
            self.assertIn(d["data"], ("synthetic", "recorded"))
            for name in d["files"]:
                self.assertTrue((self.site / "try/samples" / d["id"] / name).is_file(), name)

    def test_the_recorded_harness_suite_names_its_harness_not_its_model(self):
        names = {json.loads(p.read_text())["agent"]["name"] for p in (self.site / "try/samples/harness").glob("*.json")}
        self.assertEqual(names, {"harness v0", "harness v1"})

    def test_the_engine_zip_leaves_out_what_a_browser_cannot_run(self):
        names = zipfile.ZipFile(self.site / "try/engine.zip").namelist()
        self.assertIn("agentdiff/report.py", names)
        for left_out in ("agentdiff/harness/", "agentdiff/hub/", "agentdiff/commands/", "agentdiff/vendors.py"):
            self.assertFalse(any(n.startswith(left_out) for n in names), left_out)

    def test_the_engine_runs_from_the_zip_alone(self):
        """A clean interpreter, the repository off the path: what the dashboard runs, on the recorded harness
        suite, gives the numbers the package gives."""
        code = (
            "import sys, json, glob\n"
            f"sys.path = [p for p in sys.path if not p.startswith({str(ROOT)!r})]\n"
            f"sys.path.insert(0, {str(self.site / 'try/engine.zip')!r})\n"
            "from agentdiff.trace import Trajectory\n"
            "from agentdiff import registry\n"
            "from agentdiff.reliability import reliability\n"
            "assert 'Agenttracing/agentdiff' not in registry.__file__, registry.__file__\n"
            "data = {}\n"
            f"for p in sorted(glob.glob({str(self.site / 'try/samples/harness')!r} + '/*.json')):\n"
            "    t = Trajectory.from_dict(registry.convert(json.load(open(p)))['trajectory'])\n"
            "    data.setdefault(t.task.id, {}).setdefault(t.agent.name, []).append(t)\n"
            "r = reliability(data)['per_agent']\n"
            "print(json.dumps({a: [v['runs_used'], v['pass_hat_k']['curve'][-1]['value']] for a, v in sorted(r.items())}))\n")
        got = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=600)
        self.assertEqual(got.returncode, 0, got.stderr[-2000:])
        self.assertEqual(json.loads(got.stdout), {"harness v0": [21, 0.8667], "harness v1": [21, 1.0]})

    def test_the_playground_names_its_runtime_exactly(self):
        page = (self.site / "try/index.html").read_text(encoding="utf-8")
        self.assertIn("https://cdn.jsdelivr.net/npm/pyodide@0.29.5/pyodide.js", page)
        self.assertNotIn("innerHTML = l", page, "trace text is set as text, never as markup")
        self.assertIn("How repeatable is your agent?", page)


if __name__ == "__main__":
    unittest.main()
