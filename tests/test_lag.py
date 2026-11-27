"""When a run's code first passed its held-out check, rebuilt edit by edit (agentdiff.lag, ``agentdiff lag``).

Scored on runs written here against a small workspace and check (synthetic), and on the real runs recorded
under demo/ (recorded), whose rebuilds must end byte for byte where the runs ended.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.lag import apply_edit, lag_over, rebuild, termination_lag  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

WRONG = "def f():\n    return 1\n"
RIGHT = "def f():\n    return 2\n"
CHECK = 'python3 -c "import m; assert m.f() == 2"'
WORK = "/tmp/agentdiff-a-x/work"


def _write(path, content):
    return ("tool_call", "Write", {"file_path": path, "content": content}, "ok")


def _edit(path, old, new):
    return ("tool_call", "Edit", {"file_path": path, "old_string": old, "new_string": new}, "ok")


def _run(name, ok, steps):
    body = [{"index": i, "type": t, "name": n, "input": json.dumps(a) if isinstance(a, dict) else a,
             "output": o, "tokens": 10} for i, (t, n, a, o) in enumerate(steps)]
    body.append({"index": len(body), "type": "answer", "name": "final", "input": "", "output": "done", "tokens": 10})
    return Trajectory.from_dict({"schema_version": 1, "trace_id": name, "run_id": "r1", "agent": {"name": "a"},
                                 "task": {"id": "t", "prompt": "Fix f."}, "outcome": {"success": ok, "answer": "done"},
                                 "totals": {}, "steps": body})


def _record(final_text, passed):
    sha = hashlib.sha256(final_text.encode()).hexdigest()
    return {"check": {"command": CHECK, "passed": passed},
            "diff": {"files": [{"path": "m.py", "status": "modified", "after_sha": sha}]}}


class ApplyTest(unittest.TestCase):
    def test_each_tool_as_it_edits(self):
        files = {"m.py": "a a b"}
        self.assertIsNone(apply_edit(files, "m.py", "Edit", {"old_string": "b", "new_string": "c"}))
        self.assertEqual(files["m.py"], "a a c")
        self.assertIn("2 times", apply_edit(files, "m.py", "Edit", {"old_string": "a", "new_string": "z"}))
        self.assertIsNone(apply_edit(files, "m.py", "Edit", {"old_string": "a", "new_string": "z", "replace_all": True}))
        self.assertEqual(files["m.py"], "z z c")
        self.assertIsNone(apply_edit(files, "m.py", "MultiEdit", {"edits": [{"old_string": "c", "new_string": "d"},
                                                                         {"old_string": "d", "new_string": "e"}]}))
        self.assertEqual(files["m.py"], "z z e")
        self.assertIn("not in", apply_edit(files, "m.py", "Edit", {"old_string": "q", "new_string": "r"}))
        self.assertIsNone(apply_edit(files, "n.py", "Write", {"content": "new"}))
        self.assertIn("does not exist", apply_edit(files, "o.py", "Edit", {"old_string": "x", "new_string": "y"}))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        ws = self.root / "demo/vendors/suite/t"
        ws.mkdir(parents=True)
        (ws / "m.py").write_text("def f():\n    raise NotImplementedError\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_correct_late_after_a_wrong_edit(self):
        steps = [("tool_call", "Read", {"file_path": f"{WORK}/m.py"}, "..."), _write(f"{WORK}/m.py", WRONG),
                 _edit(f"{WORK}/m.py", "return 1", "return 2"), ("tool_call", "Bash", {"command": "python3 -m unittest"},
                                                                 "Ran 1 test in 0.0s\n\nOK"),
                 ("reason", "message", "", "All good.")]
        got = termination_lag(_run("late", True, steps), _record(RIGHT, True), self.root)
        self.assertTrue(got["rebuilt"])
        self.assertEqual(got["graded"], [False, True])
        self.assertEqual((got["first_correct"], got["first_edit_correct"], got["undone"]), (2, False, False))
        self.assertEqual(got["lag"], 3, "steps 3, 4 and the answer came after the fix")
        self.assertEqual(got["after"]["check"], 1)

    def test_a_fix_that_was_lost(self):
        steps = [_write(f"{WORK}/m.py", RIGHT), _edit(f"{WORK}/m.py", "return 2", "return 3")]
        got = termination_lag(_run("lost", False, steps), _record(RIGHT.replace("2", "3"), False), self.root)
        self.assertEqual((got["first_edit_correct"], got["final_correct"], got["undone"]), (True, False, True))

    def test_a_rebuild_that_ends_elsewhere_is_set_aside(self):
        steps = [_write(f"{WORK}/m.py", RIGHT), ("tool_call", "Bash", {"command": "sed -i s/2/4/ m.py"}, "")]
        got = termination_lag(_run("sed", True, steps), _record(RIGHT.replace("2", "4"), True), self.root)
        self.assertFalse(got["rebuilt"])
        self.assertIn("ends differently", got["why"])

    def test_edits_outside_the_project_are_not_applied(self):
        traj = _run("scratch", True, [_write("/tmp/claude-0/x/scratchpad/test_m.py", "x"), _write(f"{WORK}/m.py", RIGHT)])
        rb = rebuild(traj, self.root / "demo/vendors/suite/t")
        self.assertEqual((rb["outside"], rb["edits"]), (1, 1))
        self.assertNotIn("test_m.py", rb["states"][-1]["files"])


class RecordedTest(unittest.TestCase):
    def test_the_recorded_real_runs(self):
        got = lag_over([ROOT / "demo/vendors", ROOT / "demo/selfevolve/examples"], root=ROOT)
        self.assertEqual((got["runs"], got["rebuilt"], got["graded"]), (70, 68, 66))
        self.assertEqual((got["passed"], got["first_edit_correct"], got["undone"]), (62, 54, 0))
        self.assertEqual(got["median_lag"], 3)
        self.assertEqual(got["excluded"], 12, "the scripted stand-in's runs")
        self.assertTrue(all("globmatch" in r["trace"] for r in got["set_aside"]))


class CommandTest(unittest.TestCase):
    def test_the_command(self):
        r = subprocess.run([sys.executable, "-m", "agentdiff", "lag", "demo/vendors/live"], capture_output=True,
                           text=True, cwd=str(ROOT), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("rebuilt edit by edit", r.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-m", "agentdiff", "lag", tmp], capture_output=True, text=True,
                               cwd=str(ROOT), timeout=60)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
