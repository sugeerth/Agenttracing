"""``group_runs`` / ``analyse_runs`` take an explicit (A, B) order.

Two unrelated agents sort alphabetically and that is fine. Two
generations of one lineage do not: ``family@g10`` sorts before
``family@g9``, so a lineage reader that means "parent, then child" has
to say which is which, or the tenth step's pair report flips its sides.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from deepcompare.suite import SuiteError, group_runs
from deepcompare.trace import Trajectory


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "evolve" / "lineage" / "g0" / "traces"


def _traj(agent: str, task: str, run: str) -> Trajectory:
    """A real demo trace, re-labelled: the schema is the demo's, only the
    agent name is the test's."""
    t = Trajectory.from_json(next(iter(sorted(DEMO.glob(f"{task}__*__{run}.json")))))
    t.agent.name = agent
    t.run_id = run
    return t


class NamesTest(unittest.TestCase):
    def setUp(self):
        self.trajs = [_traj("family@g10", "rl01_ledger_reconcile", "r1"), _traj("family@g9", "rl01_ledger_reconcile", "r1")]

    def test_the_default_sorts_alphabetically_and_flips_a_lineage_pair(self):
        a, b, _ = group_runs(self.trajs)
        self.assertEqual((a, b), ("family@g10", "family@g9"))

    def test_an_explicit_order_is_honoured(self):
        a, b, runs = group_runs(self.trajs, names=("family@g9", "family@g10"))
        self.assertEqual((a, b), ("family@g9", "family@g10"))
        self.assertEqual(runs["rl01_ledger_reconcile"]["a"][0].agent.name, "family@g9")
        self.assertEqual(runs["rl01_ledger_reconcile"]["b"][0].agent.name, "family@g10")

    def test_names_that_do_not_match_the_traces_are_an_error(self):
        with self.assertRaises(SuiteError):
            group_runs(self.trajs, names=("family@g9", "family@g11"))
        with self.assertRaises(SuiteError):
            group_runs(self.trajs, names=("family@g9",))


if __name__ == "__main__":
    unittest.main()
