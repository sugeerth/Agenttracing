"""The worked examples (examples/*.py) run from a plain checkout, and print exactly what examples/README.md shows:
a changed number in the library shows up here as a stale example, not as a silently wrong doc."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _shown() -> dict:
    """{script name: the output examples/README.md quotes for it}."""
    text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in
            re.finditer(r"```\n\$ python3 examples/(\w+)\.py\n(.*?)\n```", text, re.S)}


class ExamplesTest(unittest.TestCase):
    def test_every_example_is_shown(self):
        scripts = {p.stem for p in EXAMPLES.glob("*.py") if not p.stem.startswith("_")}
        self.assertEqual(scripts, set(_shown()))

    def test_each_prints_what_the_readme_shows(self):
        for name, want in sorted(_shown().items()):
            with self.subTest(example=name):
                got = subprocess.run([sys.executable, str(EXAMPLES / f"{name}.py")], cwd=str(ROOT / "docs"),
                                     capture_output=True, text=True, timeout=600)
                self.assertEqual(got.returncode, 0, got.stderr)
                self.assertEqual(got.stdout.rstrip("\n"), want)


if __name__ == "__main__":
    unittest.main()
