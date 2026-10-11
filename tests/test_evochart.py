"""The evolution, run by run (agentdiff.hub.evochart): every task, every arm of every generation, every run, read
from the arms' records; the arm running now drawn live, a square for every run still to come."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.hub.evochart import arms_of, evolution_matrix  # noqa: E402

EXAMPLE = ROOT / "demo" / "selfevolve" / "examples" / "haiku-hard-confirms"


def _arm(d: Path, results: dict) -> None:
    """An arm's records: {task: [passed, ...]}."""
    (d / "records").mkdir(parents=True)
    for task, runs in results.items():
        for i, ok in enumerate(runs, 1):
            (d / "records" / f"{task}__a__r{i}.json").write_text(json.dumps({
                "task": task, "agent": "a", "run": f"r{i}", "trace": f"traces/{task}__a__r{i}.json",
                "check": {"passed": ok}, "wall_s": 3.0}))


class MatrixTest(unittest.TestCase):
    def test_the_columns_follow_the_lineage_confirmation_and_the_kept_change_carried(self):
        data = json.loads((EXAMPLE / "self-evolve.json").read_text())
        cols = arms_of(EXAMPLE, data)
        self.assertEqual([(c["gen"], c["head"], c["short"]) for c in cols],
                         [("g0", "as it was", "v0"), ("g0", "the change", "v1"), ("g0", "re-run", "v0"),
                          ("g0", "re-run", "v1"), ("g1", "kept", "v1")])
        self.assertEqual(cols[4]["dir"].name, "g0-h1", "g1 carried the runs of the kept change")
        self.assertEqual(list(cols[2]["runs"]), ["ranges"], "a re-run runs only the task that moved")

    def test_every_run_is_a_square_that_opens_its_trace(self):
        data = json.loads((EXAMPLE / "self-evolve.json").read_text())
        rec = sorted((EXAMPLE / "g0-h0" / "records").glob("ranges*.json"))[0]
        trace = EXAMPLE / "g0-h0" / json.loads(rec.read_text())["trace"]
        svg = evolution_matrix(EXAMPLE, data, {str(trace.resolve()): "abcdef123456"})
        self.assertEqual(svg.count('class="pass"') + svg.count('class="fail"'), 18 + 18 + 3 + 3 + 18)
        self.assertIn('href="/traces/abcdef123456"', svg)
        self.assertIn("kept · held on re-run", svg)
        self.assertIn('class="up"', svg, "ranges: the change won it")
        self.assertIn('class="movedbar"', svg)
        self.assertIn('class="band"', svg)
        self.assertIn(">17/18<", svg)

    def test_the_arm_running_now_fills_in_square_by_square(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _arm(d / "g0-h0", {"t1": [True, True], "t2": [False, False]})
            _arm(d / "g0-h1", {"t1": [True]})  # one run of four done
            pr = {"kind": "self-evolve-progress", "status": "running", "pid": os.getpid(), "generation": "g0",
                  "arm": "g0-h1", "arm_runs": 4, "per_arm": 4, "tasks": ["t1", "t2"], "testing": "Run the check."}
            svg = evolution_matrix(d, {"lineage": []}, progress=pr)
            self.assertEqual(svg.count('class="pending"'), 3, "three runs still to come")
            self.assertIn("● the change", svg)
            self.assertIn("1 of 4 run", svg)
            self.assertIn("testing a change", svg)

    def test_nothing_to_draw_is_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(evolution_matrix(Path(tmp), {"lineage": []}), "")


if __name__ == "__main__":
    unittest.main()
