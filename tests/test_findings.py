"""The ten findings of docs/FINDINGS.md, checked against a set of runs (agentdiff.findings, ``agentdiff findings``).

Each check is scored on runs written here, where the answer is known (synthetic), and on the runs recorded
under demo/ (recorded), whose answers are pinned.
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

from agentdiff.findings import (APPLIES, CANNOT, CLEAR, arms, check_findings, evolution_records,  # noqa: E402
                                findings_html, fisher_one_sided, load)

RECORDED = ["demo/vendors/aa-suite/traces", "demo/vendors/live-suite", "demo/vendors/live", "demo/selfevolve/examples"]


def _bash(cmd, out):
    return {"type": "tool_call", "name": "Bash", "input": json.dumps({"command": cmd}), "output": out}


def _edit():
    return {"type": "tool_call", "name": "Edit", "input": json.dumps({"file_path": "/w/a.py"}), "output": "ok"}


def _trace(task, agent, run, ok, steps, tokens=100):
    body = [dict(s, index=i, tokens=tokens) for i, s in enumerate(steps)]
    body.append({"index": len(body), "type": "answer", "name": "final", "input": "", "output": "done"})
    return {"schema_version": 1, "trace_id": f"{task}__{agent}__{run}", "run_id": run, "agent": {"name": agent},
            "task": {"id": task, "prompt": "Fix it."}, "outcome": {"success": ok, "answer": "done"},
            "totals": {}, "steps": body}


def _write(root: Path, traces) -> Path:
    for t in traces:
        (root / f"{t['trace_id']}.json").write_text(json.dumps(t))
    return root


def _status(result, no):
    return next(f for f in result["findings"] if f["no"] == no)


class FisherTest(unittest.TestCase):
    def test_against_the_recorded_count(self):
        self.assertAlmostEqual(fisher_one_sided(10, 38, 3, 62), 0.00286, places=4)
        self.assertIsNone(fisher_one_sided(0, 5, 0, 5), "no failures: nothing to test")
        self.assertEqual(fisher_one_sided(1, 1, 0, 1), 0.5)


class SyntheticTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, traces):
        runs, recs, _ = load([_write(self.root, traces)])
        return check_findings(runs, recs, evolution_records([self.root]))

    def test_unchecked_finishes_that_fail_more(self):
        traces = [_trace(f"t{i}", "a", "r1", False, [_edit()]) for i in range(4)]
        traces += [_trace(f"t{i}", "a", "r2", True, [_edit(), _bash("pytest -q", "3 passed in 0.1s")]) for i in range(4)]
        f1 = _status(self.check(traces), 1)
        self.assertEqual((f1["status"], f1["counts"]), (APPLIES, [4, 4, 0, 4]))
        self.assertIn("4 of 4 unchecked finishes failed, against 0 of 4 checked ones", f1["yours"])

    def test_every_run_checked_is_clear(self):
        traces = [_trace("t", "a", f"r{i}", True, [_edit(), _bash("pytest -q", "3 passed in 0.1s")]) for i in range(3)]
        self.assertEqual(_status(self.check(traces), 1)["status"], CLEAR)

    def test_one_arm_cannot_be_compared_and_thin_arms_are_said(self):
        one = self.check([_trace("t", "a", f"r{i}", True, [_edit()]) for i in range(2)])
        self.assertEqual(_status(one, 7)["status"], CANNOT)
        for f in self.root.glob("*.json"):
            f.unlink()
        two = self.check([_trace("t", agent, f"r{i}", True, [_edit()]) for agent in ("a", "b") for i in range(3)])
        f7 = _status(two, 7)
        self.assertEqual((f7["status"], f7["figure"]), (APPLIES, "1 of 1"))

    def test_a_kept_change_never_rerun_and_kept_on_chance(self):
        traces = [_trace("t", "a", "r1", True, [_edit()])]
        _write(self.root, traces)
        record = {"lineage": [{"generation": "g0", "action": {"remedy": {"id": "x"}, "test": {
            "verdict": "kept", "p_value": 0.5}}}]}
        (self.root / "self-evolve.json").write_text(json.dumps(record))
        result = self.check(traces)
        self.assertEqual(_status(result, 2)["status"], APPLIES)
        self.assertEqual(_status(result, 3)["status"], APPLIES)
        record["lineage"][0]["action"]["test"].update(confirm={"runs": 6}, p_value=0.01)
        (self.root / "self-evolve.json").write_text(json.dumps(record))
        result = self.check(traces)
        self.assertEqual((_status(result, 2)["status"], _status(result, 3)["status"]), (CLEAR, CLEAR))

    def test_no_evolution_and_no_grader_cannot_be_told(self):
        result = self.check([_trace("t", "a", "r1", True, [_edit()])])
        self.assertEqual([_status(result, n)["status"] for n in (2, 3, 10)], [CANNOT] * 3)

    def test_two_folders_with_one_agent_name_are_two_arms(self):
        for sub in ("v1/traces", "v2/traces"):
            d = self.root / sub
            d.mkdir(parents=True)
            _write(d, [_trace("t", "a", "r1", True, [_edit()])])
        runs, recs, _ = load([self.root])
        self.assertEqual(sorted(arms(runs, recs)), ["a (v1)", "a (v2)"])

    def test_the_page(self):
        traces = [_trace("t<x>", "a", "r1", False, [_edit()]), _trace("t<x>", "a", "r2", True, [_edit()])]
        page = findings_html(self.check(traces), sources=["runs/"])
        self.assertEqual(page.count('class="tile '), 10)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("t<x>", page, "a task name is text, never markup")


class RecordedTest(unittest.TestCase):
    def test_the_recorded_runs(self):
        runs, recs, _ = load([ROOT / p for p in RECORDED])
        result = check_findings(runs, recs, evolution_records([ROOT / p for p in RECORDED]))
        self.assertEqual((result["runs"], result["failed"], result["passed"]), (106, 16, 90))
        self.assertEqual(result["counts"], {APPLIES: 7, CLEAR: 2, CANNOT: 1})
        self.assertEqual(_status(result, 1)["counts"], [10, 38, 3, 62])
        self.assertEqual(_status(result, 8)["figure"], "0 of 13")


class CommandTest(unittest.TestCase):
    def test_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f.html"
            r = subprocess.run([sys.executable, "-m", "agentdiff", "findings", "demo/vendors/aa-suite/traces",
                                "--html", str(out)], capture_output=True, text=True, cwd=str(ROOT), timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("of the 10 findings apply to these runs", r.stdout)
            self.assertIn("Each finding, in your runs", out.read_text())
            r = subprocess.run([sys.executable, "-m", "agentdiff", "findings", tmp], capture_output=True, text=True,
                               cwd=str(ROOT), timeout=120)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
