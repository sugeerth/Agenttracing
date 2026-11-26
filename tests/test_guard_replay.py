"""The guards run over recorded runs before they are installed (guard.replay, ``agentdiff guard --replay``),
and the two misreadings the replay found in the live guards, fixed: a test run that printed a traceback and
then reported OK, and a test the agent wrote for itself outside the project.

The replay is scored on runs written here, whose outcome and steps are known (synthetic), and on the runs
recorded under demo/ (recorded).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.guard import Guard, failed_output, is_ungraded, replay, runner_verdict, workspace_of  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

UNITTEST_OK = ("Traceback (most recent call last):\n  ...\nValueError: malformed\n"
               "....\n----------------------------------------------------------------------\nRan 4 tests in 0.01s\n\nOK\n")
UNITTEST_FAIL = "F.\n" + "-" * 70 + "\nRan 2 tests in 0.00s\n\nFAILED (failures=1)\n"


def bash(cmd, out, error=None):
    return ("tool_call", "Bash", json.dumps({"command": cmd}), out, error)


def edit(path):
    return ("tool_call", "Edit", json.dumps({"file_path": path, "old_string": "a", "new_string": "b"}), "ok", None)


def run(name, ok, steps):
    """A recorded run; ``ok`` None is an ungraded one, recorded as the trace format records it."""
    body = [{"index": i, "type": t, "name": n, "input": inp, "output": out, **({"error": e} if e is not None else {})}
            for i, (t, n, inp, out, e) in enumerate(steps)]
    body.append({"index": len(body), "type": "answer", "name": "final", "input": "", "output": "done"})
    return Trajectory.from_dict({"schema_version": 1, "trace_id": name, "run_id": "r1", "agent": {"name": "a"},
                                 "task": {"id": name.split("__")[0], "prompt": "Fix it."},
                                 "outcome": {"success": bool(ok), "answer": "done",
                                             **({"note": "ungraded: no expected answer"} if ok is None else {})}, "totals": {}, "steps": body})


class VerdictTest(unittest.TestCase):
    def test_the_runners_last_word(self):
        self.assertIs(runner_verdict(UNITTEST_OK), False)
        self.assertIs(runner_verdict(UNITTEST_FAIL), True)
        self.assertIs(runner_verdict("===== 2 failed, 3 passed in 0.12s ====="), True)
        self.assertIs(runner_verdict("5 passed, 1 warning in 0.40s"), False)
        self.assertIs(runner_verdict("1 error in 0.02s"), True)
        self.assertIsNone(runner_verdict("Traceback (most recent call last):\nKeyError: 'x'"))
        # two runs in one command: the later verdict is the one that stands
        self.assertIs(runner_verdict(UNITTEST_FAIL + "\n" + UNITTEST_OK), False)

    def test_a_traceback_before_ok_passed(self):
        self.assertIs(failed_output(UNITTEST_OK), False)
        self.assertIs(failed_output(UNITTEST_FAIL), True)
        self.assertIs(failed_output("Traceback (most recent call last):\nKeyError: 'x'"), True, "no verdict: as before")
        self.assertIs(failed_output({"stdout": UNITTEST_OK, "exit_code": 1}), True, "an exit code still decides first")


class LiveGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_test_run_however_started_is_a_check(self):
        g = Guard(self.root, "s", enabled=("check",))
        g.after("Edit", {"file_path": str(self.root / "a.py")}, "ok")
        g.after("Bash", {"command": "python3 - <<'EOF'\nimport unittest\nEOF"}, UNITTEST_OK)
        self.assertIsNone(g.stop(), "its runner said OK after the last edit")
        g.after("Edit", {"file_path": str(self.root / "a.py")}, "ok")
        g.after("Bash", {"command": "sh run.sh"}, UNITTEST_FAIL)
        self.assertIn("the last check failed", g.stop())

    def test_only_the_projects_tests_are_protected(self):
        g = Guard(self.root, "s", enabled=("tests",))
        self.assertIsNotNone(g.before("Edit", {"file_path": str(self.root / "tests" / "test_a.py")}))
        self.assertIsNotNone(g.before("Edit", {"file_path": "tests/test_a.py"}), "a relative path is the project's")
        with tempfile.TemporaryDirectory() as scratch:
            own = str(Path(scratch) / "test_mine.py")
            self.assertIsNone(g.before("Edit", {"file_path": own}), "the agent's own scratch test")


class ReplayTest(unittest.TestCase):
    def test_catch_and_cost_with_their_denominators(self):
        runs = [
            run("a__unchecked_fail", False, [edit("/w/a.py")]),
            run("b__failing_check_fail", False, [edit("/w/a.py"), bash("pytest -q", "1 failed in 0.1s")]),
            run("c__checked_pass", True, [edit("/w/a.py"), bash("pytest -q", "3 passed in 0.1s")]),
            run("d__unchecked_pass", True, [edit("/w/a.py")]),
            run("e__no_edit_fail", False, [bash("ls", "a.py")]),
            run("f__ungraded", None, [edit("/w/a.py")]),
        ]
        self.assertTrue(is_ungraded({"outcome": {"success": False, "note": "ungraded: no expected answer"}}))
        self.assertTrue(is_ungraded({"outcome": {"success": False}, "harness": {"graded_by": "ungraded"}}))
        self.assertFalse(is_ungraded({"outcome": {"success": False}}))
        got = replay(runs, enabled=("repeat", "check"), ungraded={5})
        self.assertEqual((got["runs"], got["failed"], got["passed"], got["ungraded"]), (6, 3, 2, 1))
        check = got["guards"]["check"]
        self.assertEqual((check["in_failed"], check["in_passed"], check["in_ungraded"]), (2, 1, 1))
        self.assertEqual(check["why"], {"unchecked": 3, "failing": 1})
        self.assertEqual(got["guards"]["repeat"]["refused"], 0)
        self.assertIn("check: would have stepped into 2 of 3 failing run(s) and 1 of 2 passing run(s), "
                      "and 1 of 1 ungraded (3 finished with edits no check followed, 1 with their own last check "
                      "failing)", got["says"])

    def test_one_trace_id_in_two_folders_is_two_runs(self):
        from agentdiff.guard import replay_variants
        loop = [bash("pytest -q", "1 failed in 0.1s")] * 2
        runs = [run("t__r1", False, loop), run("t__r1", True, [bash("ls", "a.py")])]
        rows = {v["rule"]: v for v in replay_variants(runs) if v["guard"] == "repeat"}
        once = rows["a failing command rerun unchanged after 1 failure"]
        self.assertEqual((once["in_failed"], once["in_passed"]), (1, 0), "the passing namesake is not refused")

    def test_two_long_scripts_alike_at_the_start_are_two_commands(self):
        head = "python3 - <<'EOF'\n" + "x = 1\n" * 120
        g = Guard(Path(tempfile.mkdtemp()), "s", enabled=("repeat",), repeat_after=1)
        g.after("Bash", {"command": head + "print(check(1))\nEOF"}, "Traceback\nValueError: no")
        self.assertIsNone(g.before("Bash", {"command": head + "print(check(2))\nEOF"}), "the script was changed")
        self.assertIsNotNone(g.before("Bash", {"command": head + "print(check(1))\nEOF"}), "the same script again")

    def test_a_rerun_unchanged_is_where_repeat_steps_in(self):
        steps = [bash("pytest -q", "1 failed in 0.1s")] * 3
        got = replay([run("a__loop", False, steps)], enabled=("repeat",))
        self.assertEqual(got["guards"]["repeat"]["examples"][0]["step"], 2, "the third run of it")

    def test_the_tests_guard_only_where_the_project_is_known(self):
        cwd = "\nShell cwd was reset to /w"
        known = run("a__own_test", True, [bash("ls", "a.py" + cwd), edit("/tmp/scratch/test_a.py"),
                                          edit("/w/tests/test_a.py")])
        unknown = run("b__no_cwd", True, [edit("/w/tests/test_a.py")])
        self.assertEqual(workspace_of(known), "/w")
        self.assertIsNone(workspace_of(unknown))
        got = replay([known, unknown], enabled=("tests",))
        row = got["guards"]["tests"]
        self.assertEqual((row["runs"], row["refused"]), (1, 1))
        self.assertEqual(row["examples"][0]["call"], "Edit:/w/tests/test_a.py", "the project's test, not the scratch one")
        self.assertIn("(the 1 of 2 whose trace says where the project was)", got["says"][0])


class RecordedTest(unittest.TestCase):
    def test_the_recorded_runs(self):
        dirs = [ROOT / "demo/vendors/aa-suite/traces", ROOT / "demo/selfevolve/examples"]
        dirs += sorted(p for p in (ROOT / "demo/vendors").glob("live*") if p.is_dir())
        runs = []
        for d in dirs:
            for f in sorted(d.rglob("*.json")):
                try:
                    runs.append(Trajectory.from_json(f))
                except Exception:  # noqa: BLE001
                    pass
        got = replay(runs, enabled=("repeat", "check", "tests"))
        self.assertEqual((got["runs"], got["failed"], got["passed"]), (106, 16, 90))
        check = got["guards"]["check"]
        self.assertEqual((check["in_failed"], check["in_passed"]), (13, 30))
        self.assertEqual(got["guards"]["repeat"]["refused"], 0)
        self.assertEqual(got["guards"]["tests"]["refused"], 0, "the edits it once refused were the agents' own tests")


class VariantTest(unittest.TestCase):
    def test_each_rule_against_the_same_runs(self):
        from agentdiff.guard import replay_variants
        runs = [
            run("a__unchecked_fail", False, [edit("/w/a.py")]),
            run("b__failing_check_fail", False, [edit("/w/a.py"), bash("pytest -q", "1 failed in 0.1s")]),
            run("c__checked_pass", True, [edit("/w/a.py"), bash("pytest -q", "3 passed in 0.1s")]),
            run("d__once_failed_pass", True, [bash("pytest -q", "1 failed in 0.1s"), edit("/w/a.py"),
                                              bash("pytest -q", "3 passed in 0.1s")]),
            run("e__loop_fail", False, [bash("pytest -q", "1 failed in 0.1s")] * 3),
        ]
        rows = {(v["guard"], v["rule"]): v for v in replay_variants(runs)}
        installed = [v for v in rows.values() if v["installed"]]
        self.assertEqual([v["guard"] for v in installed], ["check", "repeat"])
        both = rows[("check", "edits no check followed, or a last check that failed")]
        self.assertEqual((both["in_failed"], both["failed"], both["in_passed"], both["passed"]), (2, 3, 0, 2))
        self.assertEqual(rows[("check", "edits no check followed, only")]["in_failed"], 1)
        self.assertEqual(rows[("check", "a last check that failed, only")]["in_failed"], 1)
        after = {n: rows[("repeat", f"a failing command rerun unchanged after {n} failure{'s' if n > 1 else ''}")]
                 for n in (1, 2, 3)}
        self.assertEqual([after[n]["in_failed"] for n in (1, 2, 3)], [1, 1, 0], "three runs: refused at 1 and 2")
        self.assertEqual(after[1]["in_passed"], 0, "a failure then an edit is not a rerun unchanged")
        lo, hi = both["catch_ci"]
        self.assertTrue(lo < 2 / 3 < hi)


class PageTest(unittest.TestCase):
    def test_the_page_draws_every_run_and_runs_no_script(self):
        from agentdiff.guard import replay_variants
        from agentdiff.guardreport import replay_html
        runs = [run("a__unchecked_fail", False, [edit("/w/a.py")]),
                run("b__checked_pass", True, [edit("/w/a.py"), bash("pytest -q", "3 passed in 0.1s")]),
                run("c__<odd>&name", True, [edit("/w/a.py")])]
        page = replay_html(replay(runs, enabled=("repeat", "check")), replay_variants(runs), sources=["runs/"])
        self.assertIn("The check guard steps into 1 of 1 failing runs, and 1 of 2 that passed", page)
        self.assertEqual(page.count("<svg viewBox"), 3 + 5 + 2, "one strip a run, the key's five marks, the threads "
                                                               "and the plane")
        self.assertEqual(page.count('stroke="var(--red)" stroke-width'), 2, "a red thread for each run sent back")
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<odd>", page, "a trace name is text, never markup")
        self.assertIn("c__&lt;odd&gt;&amp;name", page)
        self.assertIn("installed", page)


class CommandTest(unittest.TestCase):
    def test_the_command(self):
        traces = ROOT / "demo/vendors/aa-suite/traces"
        r = subprocess.run([sys.executable, "-m", "agentdiff", "guard", "--replay", str(traces)],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("check: would have stepped into", r.stdout)
        r = subprocess.run([sys.executable, "-m", "agentdiff", "guard", "--replay", str(traces), "--json"],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=120)
        self.assertEqual(json.loads(r.stdout)["runs"], len(json.loads(r.stdout)["per_run"]))
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-m", "agentdiff", "guard", "--replay", tmp],
                               capture_output=True, text=True, cwd=str(ROOT), timeout=120)
            self.assertEqual(r.returncode, 2)
            out = Path(tmp) / "replay.html"
            r = subprocess.run([sys.executable, "-m", "agentdiff", "guard", "--replay", str(traces), "--html", str(out)],
                               capture_output=True, text=True, cwd=str(ROOT), timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Each rule an operator could choose", out.read_text())


if __name__ == "__main__":
    unittest.main()
