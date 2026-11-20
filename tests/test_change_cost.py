"""What a tested change costs a run, paired by task (selfevolve.change_cost), and how the evolution card says it."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.hub.evolvecards import _cost  # noqa: E402
from agentdiff.selfevolve import change_cost, decide  # noqa: E402
from test_resource_ratio import _traj  # noqa: E402


def _arm(scale, ok=True):
    runs = []
    for i in range(6):
        for r in range(2):
            t = _traj(f"t{i}", r, tokens=int(1000 * (i + 1) * scale), seconds=10.0 * scale, steps=1)
            d = t.to_dict()
            d["outcome"]["success"] = ok
            runs.append(d)
    return runs


class ChangeCostTest(unittest.TestCase):
    def test_paired_by_task_with_its_exact_test(self):
        c = change_cost(_arm(1.0), _arm(1.5))
        self.assertEqual((c["tokens"]["ratio"], c["tokens"]["tasks"]), (1.5, 6))
        self.assertEqual(c["tokens"]["p"], round(2 / 64, 4), "every task dearer: the lowest p six tasks can give")
        self.assertEqual(c["steps"]["ratio"], 1.0)

    def test_decide_carries_it_and_still_decides_by_passes(self):
        out = decide(_arm(1.0, ok=False), _arm(3.0, ok=True))
        self.assertEqual(out["verdict"], "kept", "a dearer change that wins tasks is still kept: cost decides nothing")
        self.assertEqual(out["cost"]["tokens"]["ratio"], 3.0)

    def test_records_that_are_not_traces_cost_nothing_and_break_nothing(self):
        self.assertEqual(change_cost([{"task": {"id": "x"}, "outcome": {"success": True}}], []), {})

    def test_the_card_says_whether_the_tasks_show_it(self):
        shown = _cost({"tokens": {"ratio": 1.69, "p": 0.0312, "tasks": 6}}, None)
        self.assertEqual(shown, " · tokens +69% a run, shown by 6 tasks (p 0.031)")
        self.assertIn("not shown by 6 tasks (p 0.16)", _cost({"tokens": {"ratio": 1.33, "p": 0.156, "tasks": 6}}, None))
        self.assertIn("about the same", _cost({"tokens": {"ratio": 1.02, "p": 1.0, "tasks": 3}}, None))
        self.assertEqual(_cost(None, {"current": 100, "changed": 150}), " · tokens +50%", "an older record keeps its median")


if __name__ == "__main__":
    unittest.main()
