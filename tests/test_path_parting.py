"""Where repeated runs' paths first part, and how much of what follows lines up again."""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdiff.reliability import (path_parting, path_parting_pair, path_parting_says,
                                   path_split_pair, reliability)
from test_reliability import make_traj, suite

READ, EDIT, RUN, GREP = ("tool_call", "Read"), ("tool_call", "Edit"), ("tool_call", "Bash"), ("tool_call", "Grep")
END = [("answer", "final")]


class PairTest(unittest.TestCase):
    def test_identical_paths_never_part(self):
        self.assertIsNone(path_parting_pair("abc", "abc"))
        self.assertIsNone(path_parting_pair("", ""))

    def test_a_detour_that_comes_back(self):
        # one step differs, then both go on alike
        got = path_parting_pair("abcde", "abxde")
        self.assertEqual(got["at"], 3)
        self.assertAlmostEqual(got["before"], 2 / 5)
        self.assertAlmostEqual(got["rejoin"], 2 / 3)

    def test_paths_that_never_come_back(self):
        got = path_parting_pair("abcd", "abxy")
        self.assertEqual((got["at"], got["rejoin"]), (3, 0.0))

    def test_parting_at_the_first_step(self):
        got = path_parting_pair("xbcd", "ybcd")
        self.assertEqual((got["at"], got["before"]), (1, 0.0))
        self.assertAlmostEqual(got["rejoin"], 3 / 4)

    def test_one_path_stops_where_the_other_goes_on(self):
        got = path_parting_pair("ab", "abc")
        self.assertEqual((got["at"], got["rejoin"]), (3, 0.0))
        self.assertAlmostEqual(got["before"], 2 / 3)

    def test_it_adds_up_to_the_split_agreement_on_random_paths(self):
        rng = random.Random(36)
        for _ in range(500):
            a = [rng.choice("abcd") for _ in range(rng.randint(0, 9))]
            b = [rng.choice("abcd") for _ in range(rng.randint(0, 9))]
            got = path_parting_pair(a, b)
            if got is None:
                self.assertEqual(a, b)
                continue
            agree = path_split_pair(a, b)["agree"]
            self.assertAlmostEqual(got["before"] + got["rejoin"] * (1 - got["before"]), agree)
            self.assertGreaterEqual(got["at"], 1)
            self.assertLessEqual(got["rejoin"], 1.0)


class RunsTest(unittest.TestCase):
    def test_needs_two_runs(self):
        self.assertIsNone(path_parting([]))
        self.assertIsNone(path_parting([make_traj("a", "t", True)]))

    def test_identical_pairs_are_counted_not_averaged(self):
        runs = [make_traj("a", "t", True, run_id=f"r{i}", steps=s) for i, s in
                enumerate([[READ, EDIT, RUN] + END, [READ, EDIT, RUN] + END, [READ, GREP, EDIT, RUN] + END])]
        got = path_parting(runs)
        self.assertEqual((got["pairs"], got["identical"], got["at"]), (3, 1, 2))
        # the two pairs that part: one step alike out of five, then 3 of the 4 after line up again
        self.assertAlmostEqual(got["before"], 0.2)
        self.assertAlmostEqual(got["rejoin"], 0.75)

    def test_nothing_parted(self):
        runs = [make_traj("a", "t", True, run_id=f"r{i}", steps=[READ] + END) for i in range(3)]
        got = path_parting(runs)
        self.assertEqual((got["identical"], got["at"], got["rejoin"]), (3, None, None))
        self.assertIsNone(path_parting_says(got))

    def test_says(self):
        line = path_parting_says({"at": 2, "before": 0.14, "rejoin": 0.54})
        self.assertIn("part at a median step 2, 14% of the way in", line)
        self.assertIn("54% of the steps line up again", line)
        self.assertIn("about step 2.5", path_parting_says({"at": 2.5, "before": 0.3, "rejoin": 0.6}))


class ReliabilityRowTest(unittest.TestCase):
    def test_each_task_and_the_agent_carry_it(self):
        spec = {
            "t1": [{"success": True, "steps": [READ, EDIT, RUN] + END},
                   {"success": True, "steps": [READ, GREP, EDIT, RUN] + END}],
            "t2": [{"success": True, "steps": [GREP, READ, EDIT] + END},
                   {"success": True, "steps": [READ, EDIT] + END},
                   {"success": False, "steps": [RUN] + END}],
            "t3": [{"success": True, "steps": [READ] + END}, {"success": True, "steps": [READ] + END}],
        }
        row = reliability(suite(spec))["per_agent"]["a"]
        tasks = {t["task"]: t["trajectory_consistency"]["parting"] for t in row["per_task"]}
        self.assertEqual(tasks["t1"]["at"], 2)
        self.assertEqual(tasks["t2"]["at"], 1)
        # failing runs are left out, as for the split
        self.assertEqual(tasks["t2"]["pairs"], 1)
        self.assertEqual(tasks["t3"]["identical"], 1)
        agent = row["trajectory_consistency"]["parting"]
        self.assertEqual(agent["tasks_scored"], 2)     # t3 never parted
        self.assertEqual(agent["at"], 1.5)
        self.assertIn("about step 1.5", agent["says"])

    def test_no_pairs_says_nothing(self):
        row = reliability(suite({"t": [True, False]}))["per_agent"]["a"]
        self.assertIsNone(row["trajectory_consistency"]["parting"]["says"])
        self.assertEqual(row["trajectory_consistency"]["parting"]["tasks_scored"], 0)


if __name__ == "__main__":
    unittest.main()
