"""The package was `deepcompare` before it was AgentDiff. Code, hooks and
notebooks written against the old name keep running, on the same module
objects, and nothing in the repository still uses it."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


class OldNameTest(unittest.TestCase):
    def test_an_old_import_is_the_same_module(self):
        import agentdiff
        import agentdiff.harness.vendors
        import agentdiff.report
        import deepcompare
        import deepcompare.harness.vendors
        import deepcompare.report
        self.assertIs(deepcompare.report, agentdiff.report)
        self.assertIs(deepcompare.harness.vendors, agentdiff.harness.vendors)
        self.assertIs(deepcompare.Trajectory, agentdiff.Trajectory)
        from deepcompare.report import compare
        self.assertIs(compare, agentdiff.report.compare)

    def test_python_dash_m_with_the_old_name_is_the_cli(self):
        done = subprocess.run([sys.executable, "-m", "deepcompare", "--version"], cwd=str(ROOT),
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("agentdiff "))

    def test_an_operators_agent_still_gets_the_prompt_under_the_old_variable(self):
        from agentdiff.harness import loop
        self.assertEqual(loop.PROMPT_ENV, "AGENTDIFF_SYSTEM_PROMPT")
        self.assertEqual(loop.LEGACY_PROMPT_ENV, "DEEPCOMPARE_SYSTEM_PROMPT")
        source = (ROOT / "agentdiff" / "harness" / "loop.py").read_text(encoding="utf-8")
        self.assertIn("for k in previous:\n                os.environ[k] = prompt", source)

    def test_nothing_else_in_the_repository_uses_the_old_name(self):
        listed = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        allowed = {"deepcompare/__init__.py", "deepcompare/__main__.py", "tests/test_old_name.py",
                   "agentdiff/harness/loop.py", "pyproject.toml", "docs/CHANGELOG.md",
                   "tests/test_production.py"}
        stale = []
        for rel in listed.stdout.split():
            if rel in allowed or not (ROOT / rel).is_file():
                continue
            try:
                text = (ROOT / rel).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # the one branch name that carries it is a real git ref
            text = text.replace("claude/deepcompare-ai-agents-9vyj2n", "")
            if "deepcompare" in text.lower():
                stale.append(rel)
        self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
