"""The training-ground demo: 96 SYNTHETIC episodes over six tasks.

The small demo (``demo/rl/traces``, twelve episodes) is what the rest of
the RL tests pin, because it is fast. It is also, by the engine's own
runs advisory, too thin to say anything: three runs per task is below
the 8-16 floor the advisory names for structured tool-use tasks. The
training set exists so that the statistics over a batch have something
to stand on, and so the page's training view can be read at a size a
real training run would produce.

What this file pins is the shape of that set and the one property it was
built to have: the aggregate says policy-v2 is the better policy, and
one task says the opposite. A training ground where every task points
the same way cannot show a reader what a regression looks like.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare.rl import rl_aggregate
from deepcompare.suite import group_runs
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "demo" / "rl" / "train"
GENERATOR = ROOT / "demo" / "rl" / "generate_rl.py"
#: the task built so that the stronger policy is worse at it
REGRESSION = "rl05_incident_postmortem"


def _load() -> list:
    out = []
    for path in sorted(TRAIN.glob("*.json")):
        t = Trajectory.from_json(path)
        t.run_id = path.stem.split("__")[2]
        out.append(t)
    return out


class ShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = sorted(TRAIN.glob("*.json"))

    def test_two_policies_six_tasks_eight_runs_each_all_labelled(self):
        self.assertEqual(len(self.paths), 96)
        tasks, policies, runs = set(), set(), set()
        for path in self.paths:
            task, policy, run = path.stem.split("__")
            tasks.add(task)
            policies.add(policy)
            runs.add(run)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("SYNTHETIC", data["harness"]["note"], path.name)
            self.assertTrue(all(isinstance(s.get("reward"), (int, float)) for s in data["steps"]), path.name)
            self.assertTrue(20 <= len(data["steps"]) <= 95, f"{path.name}: {len(data['steps'])} steps")
        self.assertEqual(len(tasks), 6)
        self.assertEqual(policies, {"policy-v1", "policy-v2"})
        self.assertEqual(len(runs), 8)
        # every cell of the tasks x policies x runs grid is filled: a
        # bootstrap that stratifies by task needs the strata to be equal
        self.assertEqual(len(self.paths), len(tasks) * len(policies) * len(runs))

    def test_the_episode_lengths_actually_vary_by_task(self):
        by_task: dict = {}
        for path in self.paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            by_task.setdefault(path.stem.split("__")[0], []).append(len(data["steps"]))
        means = {k: sum(v) / len(v) for k, v in by_task.items()}
        self.assertLess(min(means.values()) * 1.6, max(means.values()),
                        "the six tasks should differ in length, not be six copies")

    def test_the_generator_reproduces_the_set_byte_for_byte(self):
        before = {p.name: p.read_bytes() for p in self.paths}
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(GENERATOR), "--set", "train", tmp],
                           cwd=str(ROOT), check=True, capture_output=True)
            after = {p.name: p.read_bytes() for p in sorted(Path(tmp).glob("*.json"))}
        self.assertEqual(sorted(before), sorted(after))
        for name in before:
            self.assertEqual(before[name], after[name], f"{name} did not regenerate identically")


class AggregateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        trajectories = _load()
        groups = group_runs(trajectories)
        names = tuple(sorted({t.agent.name for t in trajectories}))
        cls.rl = rl_aggregate([], trajectories, names=names)
        cls.groups = groups

    def test_the_stronger_policy_leads_on_the_headline_and_the_intervals_are_apart(self):
        a = self.rl["agents"]["policy-v1"]
        b = self.rl["agents"]["policy-v2"]
        self.assertEqual((a["episodes_n"], b["episodes_n"]), (48, 48))
        self.assertLess(a["mean_return"], b["mean_return"])
        # the headline is unambiguous on this set: v1's interval sits
        # wholly below v2's, which is what makes the one task that
        # disagrees worth drawing rather than noise
        self.assertLess(a["return_ci"][1], b["return_ci"][0])

    def test_one_task_is_a_regression_against_the_headline(self):
        signs = {t: v["sign"] for t, v in self.rl["tasks"].items()}
        negative = sorted(t for t, s in signs.items() if s < 0)
        self.assertEqual(negative, [REGRESSION])
        self.assertLess(self.rl["tasks"][REGRESSION]["delta"], 0)
        # and the other five are not marginal, so the aggregate really
        # does hide the exception rather than merely averaging it
        others = [v["delta"] for t, v in self.rl["tasks"].items() if t != REGRESSION]
        self.assertTrue(all(d > 5.0 for d in others), others)

    def test_on_that_task_the_leading_policy_also_passes_less_often(self):
        def passes(policy: str) -> tuple:
            eps = [e for e in self.rl["agents"][policy]["episodes"] if e["task_id"] == REGRESSION]
            return sum(1 for e in eps if e["success"]), len(eps)
        v1, v2 = passes("policy-v1"), passes("policy-v2")
        self.assertEqual((v1[1], v2[1]), (8, 8))
        self.assertGreater(v1[0], v2[0], "the weaker policy should win this task on outcome too")

    def test_every_episode_carries_the_per_step_arrays_the_page_reads(self):
        for policy, agent in sorted(self.rl["agents"].items()):
            for e in agent["episodes"]:
                self.assertEqual(len(e["rewards"]), e["steps"], f"{policy} {e['run_id']}")
                self.assertEqual(len(e["cum"]), e["steps"])
                self.assertEqual(len(e["values"]), e["steps"])
                self.assertTrue(any(v is not None for v in e["values"]), "a critic estimate exists")
        self.assertEqual(self.rl["source"], "recorded")

    def test_deterministic(self):
        again = rl_aggregate([], _load(), names=("policy-v1", "policy-v2"))
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.rl, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
