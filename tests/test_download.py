"""The download: agentdiff.pyz runs on its own, from anywhere, with its examples.

The standalone binaries are built by CI on Linux, macOS and Windows
(``.github/workflows/binaries.yml``); this builds the one-file archive and
runs it as someone who downloaded it would: another folder, an empty cache.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PyzTest(unittest.TestCase):
    def test_the_archive_runs_from_anywhere_and_carries_its_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            subprocess.run([sys.executable, str(ROOT / "packaging" / "build.py"), "--dist", str(dist)], check=True,
                           capture_output=True, timeout=300)
            pyz = dist / "agentdiff.pyz"
            names = zipfile.ZipFile(pyz).namelist()
            for want in ("__main__.py", "agentdiff/cli.py", "agentdiff/hub/static/longview.js",
                         "agentdiff/_examples/long-runs/traces/ledger_v2__agent-3day.json"):
                self.assertIn(want, names)
            self.assertFalse(any("__pycache__" in n for n in names))
            env = dict(os.environ, XDG_CACHE_HOME=str(Path(tmp) / "cache"), HOME=str(Path(tmp) / "home"),
                       PYTHONPATH="")
            (Path(tmp) / "home").mkdir()
            run = subprocess.run([sys.executable, str(pyz), "--version"], cwd=tmp, env=env, capture_output=True,
                                 text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("agentdiff", run.stdout)
            again = subprocess.run([sys.executable, str(pyz), "timeline", "--help"], cwd=tmp, env=env,
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(again.returncode, 0, again.stderr)
            unpacked = list((Path(tmp) / "cache" / "agentdiff").iterdir())
            self.assertEqual(len(unpacked), 1, "unpacked once, reused after")


if __name__ == "__main__":
    unittest.main()
