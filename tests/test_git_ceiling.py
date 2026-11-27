"""A run cannot find a git repository above its own directory, and a run that found one is said.

Git looks for a repository by walking up from where it runs. A run directory inside a checkout would hand the
agent that checkout's history; for a task built from a repository's history, its own fix. These run the scripted
stand-in agent through the real harness, in a run directory placed inside a repository made here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.gittasks import reached_outside  # noqa: E402
from agentdiff.harness.vendors import VendorSpec, run_vendor, vendor_env  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "vendors" / "fake_claude.py"
WORKSPACE = ROOT / "demo" / "vendors" / "bugfix"
FOUND = "found a git repository outside the workspace"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"})


def _repo_with_a_fix(root: Path) -> Path:
    repo = root / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pricing.py").write_text("def apply_discount(total, percent):  # THE-FIX\n    return total\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Fix apply_discount: THE-FIX")
    return repo


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class CeilingTest(unittest.TestCase):
    def _run(self, base: Path, workspace: Path, look: str, guard: bool = True) -> dict:
        tmpdir = base / "project" / ".cache" / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        env = {"TMPDIR": str(tmpdir), "FAKE_VENDOR_GIT": look, "FAKE_VENDOR_MODE": "idle", "ANTHROPIC_API_KEY": "k" * 20}
        task = {"id": "bugfix", "prompt": "Fix the discount.", "workspace": str(workspace), "check": "python3 -m unittest -q"}
        patches = [mock.patch.dict(os.environ, env), mock.patch.object(tempfile, "tempdir", None)]
        if not guard:
            patches.append(mock.patch("agentdiff.harness.vendors.vendor_env", lambda ceiling=None: vendor_env()))
        for p in patches:
            p.start()
        try:
            rec = run_vendor(VendorSpec(kind="claude", binary=str(FAKE)), task, base / "out", run=f"r{len(list((base).glob('out/traces/*')))}")
        finally:
            for p in reversed(patches):
                p.stop()
            tempfile.tempdir = None
        trace = json.loads((base / "out" / rec["trace"]).read_text(encoding="utf-8"))
        return trace

    @staticmethod
    def _look(trace: dict) -> str:
        return next(str(s["output"]) for s in trace["steps"] if s["name"] == "Bash")

    def test_a_repository_above_the_run_is_out_of_reach(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo_with_a_fix(base)
            for look in ("git log -p -1", "git show HEAD", "git log --all --oneline", "git reflog"):
                trace = self._run(base, WORKSPACE, look)
                out = self._look(trace)
                self.assertNotIn("THE-FIX", out, look)
                self.assertIn("not a git repository", out, look)
                self.assertEqual(reached_outside(Trajectory.from_dict(trace), ["bugfix"]), [], look)

    def test_without_the_ceiling_it_was_in_reach_and_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo_with_a_fix(base)
            trace = self._run(base, WORKSPACE, "git log -p -1", guard=False)
            self.assertIn("THE-FIX", self._look(trace))
            kinds = [x["kind"] for x in reached_outside(Trajectory.from_dict(trace), ["bugfix"])]
            self.assertEqual(kinds, [FOUND])

    def test_a_workspace_with_its_own_repository_still_has_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo_with_a_fix(base)
            own = base / "own-workspace"
            shutil.copytree(WORKSPACE, own)
            _git(own, "init", "-q")
            _git(own, "add", ".")
            _git(own, "commit", "-qm", "the workspace's own first commit")
            out = self._look(self._run(base, own, "git log --oneline"))
            self.assertIn("the workspace's own first commit", out)
            self.assertNotIn("THE-FIX", out)


class EnvTest(unittest.TestCase):
    def test_the_ceiling_is_the_run_directory_and_the_operators_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": "/srv"}):
                env = vendor_env(ceiling=Path(tmp))
            self.assertEqual(env["GIT_CEILING_DIRECTORIES"].split(os.pathsep), [str(Path(tmp).resolve()), "/srv"])
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIT_CEILING_DIRECTORIES", None)
                self.assertNotIn("GIT_CEILING_DIRECTORIES", vendor_env())


if __name__ == "__main__":
    unittest.main()
