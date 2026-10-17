"""Tasks from a repository's history (agentdiff.gittasks): a commit that changed code and its tests is a task, its
tests held out as the grader, kept only when they fail on the parent and pass on the commit."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.gittasks import build, candidates, passed_ids  # noqa: E402

TEST_HEAD = "import unittest\n\nfrom calc import add{more}\n\n\nclass CalcTest(unittest.TestCase):\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com"))


def _commit(repo: Path, message: str, files: dict) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def make_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "Start the calculator with add and its test", {
        "calc.py": "def add(a, b):\n    return a + b\n",
        "test_calc.py": TEST_HEAD.format(more="") + "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"})
    _commit(repo, "Add subtract, which takes the second number from the first", {
        "calc.py": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
        "test_calc.py": TEST_HEAD.format(more=", subtract") + "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
                        "    def test_subtract(self):\n        self.assertEqual(subtract(5, 7), -2)\n"})
    _commit(repo, "Describe the calculator in the readme", {"README.md": "A calculator.\n"})
    _commit(repo, "Comment add, and test it with negative numbers too", {
        "calc.py": "def add(a, b):\n    # the sum\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
        "test_calc.py": TEST_HEAD.format(more=", subtract") + "    def test_add(self):\n        self.assertEqual(add(-2, 3), 1)\n\n"
                        "    def test_subtract(self):\n        self.assertEqual(subtract(5, 7), -2)\n"})
    _commit(repo, "fix", {
        "calc.py": "def add(a, b):\n    # the sum\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n\n\n"
                   "def double(a):\n    return a * 2\n",
        "test_calc.py": TEST_HEAD.format(more=", double") + "    def test_double(self):\n        self.assertEqual(double(4), 8)\n"})


@unittest.skipIf(shutil.which("git") is None, "needs git")
class GitTasksTest(unittest.TestCase):
    def test_commits_that_changed_code_and_tests_are_the_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            make_repo(repo)
            found = candidates(repo)
            self.assertEqual([c["message"].splitlines()[0] for c in found],
                             ["fix", "Comment add, and test it with negative numbers too",
                              "Add subtract, which takes the second number from the first"],
                             "newest first; the readme commit and the first (no parent) are not tasks")
            self.assertEqual(found[-1]["tests"], ["test_calc.py"])
            self.assertEqual(found[-1]["code"], ["calc.py"])

    def test_only_a_sound_task_is_kept_and_every_other_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, dest = Path(tmp) / "repo", Path(tmp) / "tasks"
            make_repo(repo)
            made = build(repo, dest, want=6, python=sys.executable, test_cmd=f"{sys.executable} -m unittest {{files}}")
            self.assertEqual([k["id"].split("-", 1)[1] for k in made["kept"]], ["add-subtract-which-takes-the"])
            why = {s["subject"]: s["why"] for s in made["skipped"]}
            self.assertEqual(why["fix"], "the message is too short to be a task")
            self.assertTrue(why["Comment add, and test it with negative numbers too"].startswith(
                "its tests already pass on the parent"))
            task = json.loads((dest / "tasks.json").read_text())["tasks"][0]
            self.assertIn("Add subtract, which takes the second number from the first", task["prompt"])
            self.assertIn("not given to you", task["prompt"])
            work = dest / task["workspace"]
            self.assertNotIn("subtract", (work / "calc.py").read_text(), "the workspace is the parent")
            self.assertNotIn("subtract", (work / "test_calc.py").read_text(), "the grader is not in it")
            self.assertNotIn(str(work), task["check"], "the grader lives outside the workspace")
            # the check, run as the harness runs it: fails on the untouched parent, passes once the change is made
            first = subprocess.run(task["check"], shell=True, cwd=work, capture_output=True, text=True)
            self.assertNotEqual(first.returncode, 0)
            (work / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
            self.assertEqual(subprocess.run(task["check"], shell=True, cwd=work, capture_output=True).returncode, 0)

    def test_a_grader_that_does_not_agree_with_itself_is_flaky_and_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, count = Path(tmp) / "repo", Path(tmp) / "runs-on-the-commit"
            make_repo(repo)
            # on the commit, the held-out test passes its first run and fails every later one
            flaky = (TEST_HEAD.format(more=", subtract") + "    def test_subtract(self):\n"
                     "        import pathlib\n"
                     f"        p = pathlib.Path({str(count)!r})\n"
                     "        n = int(p.read_text()) if p.exists() else 0\n"
                     "        p.write_text(str(n + 1))\n"
                     "        self.assertEqual(subtract(5, 7), -2)\n"
                     "        self.assertEqual(n, 0)\n")
            _commit(repo, "Add subtract again, now with a test that remembers its runs", {
                "calc.py": "def add(a, b):\n    return a + b\n",
                "test_calc.py": TEST_HEAD.format(more="") + "    def test_add(self):\n        self.assertEqual(add(1, 1), 2)\n"})
            _commit(repo, "Bring subtract back, which takes the second number from the first", {
                "calc.py": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
                "test_calc.py": flaky})
            cmd = f"{sys.executable} -m unittest {{files}}"
            once = build(repo, Path(tmp) / "once", want=1, python=sys.executable, test_cmd=cmd, checks=1)
            self.assertEqual(len(once["kept"]), 1, "one look each: the flaky grader passes")
            count.unlink()
            thrice = build(repo, Path(tmp) / "thrice", want=1, python=sys.executable, test_cmd=cmd, checks=3)
            why = {s["subject"]: s["why"] for s in thrice["skipped"]}
            self.assertEqual(why["Bring subtract back, which takes the second number from the first"],
                             "a flaky grader: on the commit its tests passed 1 time(s), then failed")
            self.assertEqual([k["id"].split("-", 1)[1] for k in thrice["kept"]], ["add-subtract-which-takes-the"],
                             "the next sound commit is taken instead")
            self.assertEqual(thrice["kept"][0]["checked"], 3)

    def test_a_run_that_skips_the_held_out_tests_does_not_pass(self):
        """Work that makes the held-out tests skip themselves exits 0. Held to the tests the commit passed, it fails."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            make_repo(repo)
            made = build(repo, Path(tmp) / "tasks", want=1, python=sys.executable)
            task = made["kept"][0]
            self.assertEqual(task["held_to"], 2, "test_add and test_subtract passed on the commit")
            work = Path(tmp) / "tasks" / task["workspace"]

            def graded(calc: str, held: bool = True):
                run = Path(tmp) / "run"
                shutil.rmtree(run, ignore_errors=True)
                shutil.copytree(work, run)
                (run / "calc.py").write_text(calc)
                check = task["check"] if held else task["check"].replace(
                    str(Path(tmp) / "tasks" / "expect" / f"{task['id']}.json"), "-")
                return subprocess.run(check, shell=True, cwd=run, capture_output=True, text=True)
            honest = "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"
            self.assertEqual(graded(honest).returncode, 0)
            sabotage = ("import unittest\nunittest.TestCase.setUp = lambda self: self.skipTest('not today')\n\n"
                        "def add(a, b):\n    return 0\n\n\ndef subtract(a, b):\n    return 0\n")
            self.assertEqual(graded(sabotage, held=False).returncode, 0, "unheld, every test skips and it exits 0")
            done = graded(sabotage)
            self.assertEqual(done.returncode, 1)
            self.assertIn("held-out tests that passed on the commit did not pass here (2)", done.stdout)

    def test_the_command_says_when_no_commit_makes_a_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")
            _commit(repo, "Only a readme in this one", {"README.md": "x\n"})
            _commit(repo, "Still only the readme here", {"README.md": "y\n"})
            done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--from-git", str(repo),
                                   "--dry-run"], capture_output=True, text=True, timeout=120,
                                  env=dict(os.environ, PYTHONPATH=str(ROOT), AGENTDIFF_HOME=str(Path(tmp) / "home")))
            self.assertEqual(done.returncode, 2)
            self.assertIn("no commit made a sound task", done.stderr)


class PassedIdsTest(unittest.TestCase):
    def test_it_reads_the_tests_that_passed_from_pytest_and_unittest(self):
        pytest_out = ("..s\n==== short test summary info ====\nPASSED tests/test_a.py::test_one\n"
                      "PASSED tests/test_a.py::Case::test_two\nSKIPPED [1] tests/test_a.py:9: no\n"
                      "FAILED tests/test_a.py::test_four - assert 1 == 2\n")
        self.assertEqual(passed_ids(pytest_out), {"tests/test_a.py::test_one", "tests/test_a.py::Case::test_two"})
        new = "test_add (test_calc.CalcTest.test_add) ... ok\ntest_sub (test_calc.CalcTest.test_sub) ... skipped 'x'\n"
        old = "test_add (test_calc.CalcTest) ... ok\ntest_mul (test_calc.CalcTest) ... FAIL\n"
        documented = "test_div (test_calc.CalcTest.test_div)\nDivides by two. ... ok\n"
        self.assertEqual(passed_ids(new), {"test_calc.CalcTest.test_add"})
        self.assertEqual(passed_ids(old), {"test_calc.CalcTest.test_add"}, "Python 3.10 names it the same way")
        self.assertEqual(passed_ids(documented), {"test_calc.CalcTest.test_div"})
        self.assertEqual(passed_ids("Ran 3 tests\nOK\n"), set())
        coloured = "\x1b[32mPASSED\x1b[0m test_calc.py::\x1b[1mCalcTest::test_add\x1b[0m\n"
        self.assertEqual(passed_ids(coloured), {"test_calc.py::CalcTest::test_add"}, "a terminal's colour codes")


@unittest.skipIf(shutil.which("git") is None or os.name == "nt", "needs git, and the stand-in's shebang")
class HubTest(unittest.TestCase):
    def test_the_evolve_page_starts_one_on_a_repository_and_refuses_a_folder_that_is_not_one(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from unittest import mock
        from test_evolve_live import _claude_on_path, _hub, _wait
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "root").mkdir()
            make_repo(tmp / "repo")
            with mock.patch.dict(os.environ, dict(_claude_on_path(tmp), AGENTDIFF_HOME=str(tmp / "home"))):
                app, get, post = _hub(tmp)
                self.assertIn('value="git"', get("/evolve").body.decode())
                self.assertEqual(post("/evolve/start", mode="git", project=str(tmp / "root")).status, 400)
                self.assertEqual(post("/evolve/start", mode="git", project=str(tmp / "repo"), generations=1,
                                      runs=1).status, 303)
                self.assertTrue(_wait(lambda: not app.jobs.running(), 120))
                log = (tmp / "self-evolve" / "git-repo-haiku" / "hub-job.log").read_text()
                self.assertIn("from-git: 1 task(s)", log)


if __name__ == "__main__":
    unittest.main()
