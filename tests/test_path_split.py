"""Where repeated runs' paths part: other actions, or the same ones reordered."""
from __future__ import annotations

import itertools
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdiff.reliability import (path_split, path_split_pair, path_split_says,
                                   reliability)
from agentdiff.similarity import lcs_length, lcs_ratio
from test_reliability import CANON, make_traj, suite

READ, EDIT, RUN = ("tool_call", "Read"), ("tool_call", "Edit"), ("tool_call", "Bash")
END = [("answer", "final")]


class PairTest(unittest.TestCase):
    def test_reordering_is_order_not_actions(self):
        split = path_split_pair("abc", "cba")
        self.assertEqual(split["actions"], 0.0)
        self.assertAlmostEqual(split["order"], 2 / 3)
        self.assertAlmostEqual(split["agree"], 1 / 3)

    def test_a_swapped_action_is_actions_not_order(self):
        split = path_split_pair("abc", "abd")
        self.assertAlmostEqual(split["actions"], 1 / 3)
        self.assertEqual(split["order"], 0.0)

    def test_repeats_count_as_often_as_both_runs_took_them(self):
        # the second "a" is an action the shorter run never took again
        self.assertAlmostEqual(path_split_pair("aab", "ab")["actions"], 1 / 3)
        self.assertEqual(path_split_pair("aab", "ab")["order"], 0.0)

    def test_same_and_empty_paths_agree(self):
        self.assertEqual(path_split_pair("abc", "abc"), {"agree": 1.0, "actions": 0.0, "order": 0.0})
        self.assertEqual(path_split_pair("", ""), {"agree": 1.0, "actions": 0.0, "order": 0.0})
        self.assertEqual(path_split_pair("ab", "")["actions"], 1.0)

    def test_the_parts_add_up_exactly_on_random_paths(self):
        rng = random.Random(7)
        for _ in range(300):
            a = [rng.choice("abcd") for _ in range(rng.randint(0, 9))]
            b = [rng.choice("abcd") for _ in range(rng.randint(0, 9))]
            split = path_split_pair(a, b)
            self.assertAlmostEqual(split["agree"] + split["actions"] + split["order"], 1.0)
            self.assertGreaterEqual(split["order"], 0.0)
            self.assertGreaterEqual(split["actions"], 0.0)
            # agreement is the common subsequence over the longer path
            longest = max(len(a), len(b))
            if longest:
                self.assertAlmostEqual(split["agree"], lcs_length(a, b) / longest)

    def test_lcs_length_against_brute_force(self):
        rng = random.Random(3)
        for _ in range(100):
            a = [rng.choice("xyz") for _ in range(rng.randint(0, 6))]
            b = [rng.choice("xyz") for _ in range(rng.randint(0, 6))]
            best = 0
            for n in range(len(a), 0, -1):
                if any(_is_subsequence(c, b) for c in itertools.combinations(a, n)):
                    best = n
                    break
            self.assertEqual(lcs_length(a, b), best)

    def test_lcs_ratio_is_unchanged(self):
        self.assertEqual(lcs_ratio(("a", "b", "c"), ("a", "c")), 0.8)
        self.assertEqual(lcs_ratio((), ()), 1.0)
        self.assertEqual(lcs_ratio(("a",), ()), 0.0)


def _is_subsequence(sub, seq):
    it = iter(seq)
    return all(x in it for x in sub)


class RunsTest(unittest.TestCase):
    def test_needs_two_runs(self):
        self.assertIsNone(path_split([]))
        self.assertIsNone(path_split([make_traj("a", "t", True)]))

    def test_means_over_pairs(self):
        runs = [make_traj("a", "t", True, run_id="r1", steps=[READ, EDIT, RUN] + END),
                make_traj("a", "t", True, run_id="r2", steps=[READ, EDIT, RUN] + END),
                make_traj("a", "t", True, run_id="r3", steps=[EDIT, READ, RUN] + END)]
        split = path_split(runs)
        self.assertEqual(split["pairs"], 3)
        self.assertEqual(split["actions"], 0.0)
        # two of the three pairs swap Read and Edit: 1/4 each
        self.assertAlmostEqual(split["order"], round(2 / 12, 4))
        self.assertAlmostEqual(split["agree"] + split["actions"] + split["order"], 1.0, places=3)

    def test_says(self):
        self.assertIsNone(path_split_says(None))
        self.assertIn("same actions in the same order",
                      path_split_says({"agree": 1.0, "actions": 0.0, "order": 0.0}))
        self.assertIn("mostly other actions",
                      path_split_says({"agree": 0.8, "actions": 0.2, "order": 0.0}))
        self.assertIn("mostly the same actions in another order",
                      path_split_says({"agree": 0.8, "actions": 0.02, "order": 0.18}))
        self.assertIn("both", path_split_says({"agree": 0.6, "actions": 0.2, "order": 0.2}))


class ReliabilityRowTest(unittest.TestCase):
    def test_each_task_and_the_agent_carry_the_split(self):
        spec = {
            "t1": [{"success": True, "steps": [READ, EDIT, RUN] + END},
                   {"success": True, "steps": [EDIT, READ, RUN] + END}],
            "t2": [{"success": True, "steps": CANON},
                   {"success": True, "steps": CANON[:-1] + [RUN] + END},
                   {"success": False, "steps": [RUN] + END}],
            "t3": [{"success": True}, {"success": False}],
        }
        row = reliability(suite(spec))["per_agent"]["a"]
        tasks = {t["task"]: t for t in row["per_task"]}
        self.assertEqual(tasks["t1"]["trajectory_consistency"]["split"]["actions"], 0.0)
        self.assertGreater(tasks["t1"]["trajectory_consistency"]["split"]["order"], 0.0)
        self.assertEqual(tasks["t2"]["trajectory_consistency"]["split"]["order"], 0.0)
        # failing runs are left out, as for trajectory consistency
        self.assertEqual(tasks["t2"]["trajectory_consistency"]["split"]["pairs"], 1)
        self.assertIsNone(tasks["t3"]["trajectory_consistency"]["split"])
        agent = row["trajectory_consistency"]["split"]
        self.assertEqual(agent["tasks_scored"], 2)
        self.assertAlmostEqual(agent["agree"] + agent["actions"] + agent["order"], 1.0, places=3)
        self.assertIn("both other actions and another order", agent["says"])

    def test_no_pairs_says_nothing(self):
        row = reliability(suite({"t": [True, False]}))["per_agent"]["a"]
        self.assertIsNone(row["trajectory_consistency"]["split"]["says"])
        self.assertEqual(row["trajectory_consistency"]["split"]["tasks_scored"], 0)


if __name__ == "__main__":
    unittest.main()
