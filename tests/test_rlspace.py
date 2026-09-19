"""The behaviour space: what a policy does, and where two policies part.

A hand-built set of four episodes whose vocabulary, trie shape, branch
point, n-gram ratios and distances are all known on paper checks every
reading; the fast edit distance is checked against the textbook DP on
thousands of random pairs; the layout is checked for determinism (twice,
the same bytes) and for the one invariance that matters (two identical
episodes land in the same place). Both prunings are checked to fold what
they say they fold and to say so. Every degenerate case — no episodes, no
steps, one episode, a policy with no episodes at all — returns a reason
rather than a number. The demo numbers are pinned against the 96-episode
training set and the section is checked to arrive in `aggregate["rl"]`.
"""

from __future__ import annotations

import json
import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.rl import rl_aggregate  # noqa: E402
from deepcompare.rlspace import (  # noqa: E402
    MATRIX_JSON_EPISODES, MAX_DISTANCE_EPISODES, MAX_DISTANCE_TOKENS, _edit_dp, _edit_myers,
    behaviour_episodes, behaviour_space, branch_points, build_trie, distances, edit_distance,
    episode_tokens, mds, ngrams, normalised_distance, policy_trie, rl_space, step_token, vocabulary,
)
from deepcompare.trace import Trajectory  # noqa: E402

TRAIN = ROOT / "demo" / "rl" / "train"
SMALL = ROOT / "demo" / "rl" / "traces"


def ep(policy, run, tokens, ret, success):
    return {"policy": policy, "task_id": "t1", "run_id": run, "tokens": list(tokens),
            "return": ret, "success": success}


#: old plans, searches twice and answers wrong; new plans, reads once and
#: answers right. The two runs of each policy are identical to each other.
HAND = [
    ep("old", "r1", ["plan", "search", "search", "answer"], -1.0, False),
    ep("old", "r2", ["plan", "search", "search", "answer"], -3.0, False),
    ep("new", "r1", ["plan", "read", "answer"], 5.0, True),
    ep("new", "r2", ["plan", "read", "answer"], 7.0, True),
]
NAMES = ("old", "new")


def _trajectories(where: Path) -> list:
    return [Trajectory.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(where.glob("*.json"))]


class TokenTest(unittest.TestCase):
    """One step, one token: the tool's name when it is tool-ish, the step's
    own family otherwise."""

    def test_a_toolish_step_is_its_tool_name(self):
        self.assertEqual(step_token("tool_call", "grep"), "grep")
        self.assertEqual(step_token("search", "search"), "search")
        self.assertEqual(step_token("read", "read_file"), "read_file")

    def test_a_thinking_step_is_its_family(self):
        self.assertEqual(step_token("plan", "plan"), "plan")
        self.assertEqual(step_token("reason", "think hard"), "reason")
        self.assertEqual(step_token("answer", "final"), "answer")

    def test_a_nameless_tool_falls_back_to_its_family(self):
        self.assertEqual(step_token("tool_call", ""), "tool_call")
        self.assertEqual(step_token("tool_call", None), "tool_call")
        self.assertEqual(step_token(None, None), "step")

    def test_a_stream_reads_dicts_and_objects_alike(self):
        steps = [{"type": "plan", "name": "plan"}, {"type": "tool_call", "name": "grep"}]
        self.assertEqual(episode_tokens(steps), ["plan", "grep"])
        self.assertEqual(episode_tokens([]), [])

    def test_the_demo_traces_reduce_to_their_tools_and_families(self):
        traj = _trajectories(SMALL)[0]
        tokens = episode_tokens(traj.steps)
        self.assertEqual(len(tokens), len(traj.steps))
        self.assertEqual(tokens[0], "plan")
        self.assertEqual(tokens[-1], "answer")
        self.assertIn("grep", tokens)


class VocabularyTest(unittest.TestCase):
    """Four episodes, eight steps of `old` and six of `new`, counted by hand."""

    def setUp(self):
        self.v = vocabulary([dict(e, steps=len(e["tokens"])) for e in HAND], list(NAMES))

    def test_every_token_once_sorted(self):
        self.assertEqual(self.v["tokens"], ["answer", "plan", "read", "search"])
        self.assertEqual(self.v["size"], 4)

    def test_the_frequency_is_a_count_per_policy(self):
        self.assertEqual(self.v["frequency"]["old"], {"answer": 2, "plan": 2, "read": 0, "search": 4})
        self.assertEqual(self.v["frequency"]["new"], {"answer": 2, "plan": 2, "read": 2, "search": 0})
        self.assertEqual(self.v["steps"], {"old": 8, "new": 6})
        self.assertAlmostEqual(self.v["rates"]["old"]["search"], 0.5)
        self.assertAlmostEqual(self.v["rates"]["new"]["read"], 1 / 3, places=3)

    def test_the_signature_is_what_only_one_policy_ever_does(self):
        self.assertEqual(self.v["signature"], {"old": ["search"], "new": ["read"]})
        self.assertEqual(self.v["shared"], ["answer", "plan"])


class TrieTest(unittest.TestCase):
    """root → plan → {search…, read…}: the shape, the counts and the mean
    return below each node are all on paper."""

    def setUp(self):
        self.space = behaviour_space(HAND, names=NAMES)
        self.trie = self.space["trie"]

    def test_the_root_holds_every_episode(self):
        root = self.trie["root"]
        self.assertEqual(root["episodes"], 4)
        self.assertEqual(root["by_policy"], {"old": 2, "new": 2})
        self.assertEqual(root["prefix"], [])
        self.assertIsNone(root["token"])
        self.assertAlmostEqual(root["mean_return"], 2.0)      # (−1 −3 + 5 + 7) / 4
        self.assertAlmostEqual(root["success_rate"], 0.5)

    def test_the_spine_is_one_child_until_the_policies_part(self):
        root = self.trie["root"]
        self.assertEqual([c["token"] for c in root["children"]], ["plan"])
        plan = root["children"][0]
        self.assertEqual(plan["prefix"], ["plan"])
        self.assertEqual(plan["episodes"], 4)
        self.assertEqual(sorted(c["token"] for c in plan["children"]), ["read", "search"])

    def test_each_branch_carries_the_return_below_it(self):
        plan = self.trie["root"]["children"][0]
        by = {c["token"]: c for c in plan["children"]}
        self.assertEqual(by["search"]["by_policy"], {"old": 2, "new": 0})
        self.assertEqual(by["read"]["by_policy"], {"old": 0, "new": 2})
        self.assertAlmostEqual(by["search"]["mean_return"], -2.0)
        self.assertAlmostEqual(by["read"]["mean_return"], 6.0)
        self.assertAlmostEqual(by["search"]["success_rate"], 0.0)
        self.assertAlmostEqual(by["read"]["success_rate"], 1.0)

    def test_a_stream_that_ends_at_a_node_is_counted_there(self):
        node = self.trie["root"]
        for token in ("plan", "read", "answer"):
            node = [c for c in node["children"] if c["token"] == token][0]
        self.assertEqual(node["ends_here"], 2)
        self.assertEqual(node["children"], [])

    def test_nothing_is_pruned_when_every_branch_carries_two_episodes(self):
        pruned = self.trie["pruned"]
        self.assertEqual((pruned["tails"], pruned["truncated"]), (0, 0))
        self.assertIn("nothing pruned", pruned["note"])


class BranchPointTest(unittest.TestCase):
    """The branch point: total variation between the policies' splits,
    weighted by the episodes that reach the node."""

    def setUp(self):
        self.space = behaviour_space(HAND, names=NAMES)
        self.points = self.space["branches"]["points"]

    def test_the_only_branch_is_where_they_part_and_it_scores_one(self):
        self.assertEqual(len(self.points), 1)
        point = self.points[0]
        self.assertEqual(point["depth"], 1)
        self.assertEqual(point["prefix"], ["plan"])
        self.assertEqual(point["episodes"], 4)
        # the splits are disjoint, every episode reaches it: 1.0 × 1.0
        self.assertAlmostEqual(point["imbalance"], 1.0)
        self.assertAlmostEqual(point["weight"], 1.0)
        self.assertAlmostEqual(point["score"], 1.0)

    def test_it_names_the_return_on_each_side(self):
        sides = {s["policy"]: s for s in self.points[0]["sides"]}
        self.assertEqual(sides["old"]["token"], "search")
        self.assertEqual(sides["new"]["token"], "read")
        self.assertAlmostEqual(sides["old"]["mean_return"], -2.0)
        self.assertAlmostEqual(sides["new"]["mean_return"], 6.0)
        self.assertIn("old takes search", self.points[0]["label"])
        self.assertIn("new takes read", self.points[0]["label"])

    def test_a_shared_split_is_no_branch_point(self):
        # both policies split the same way: the traffic is balanced, so the
        # node scores zero and never leads
        same = [ep("old", "r1", ["a", "b"], 1.0, True), ep("old", "r2", ["a", "c"], 1.0, True),
                ep("new", "r1", ["a", "b"], 1.0, True), ep("new", "r2", ["a", "c"], 1.0, True)]
        points = behaviour_space(same, names=NAMES)["branches"]["points"]
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0]["imbalance"], 0.0)
        self.assertAlmostEqual(points[0]["score"], 0.0)

    def test_a_deep_branch_is_weighted_down_against_a_shallow_one(self):
        # everything divides at step 0; two of the four then divide again
        eps = [ep("old", "r1", ["x", "p"], 1.0, True), ep("old", "r2", ["x", "q"], 1.0, True),
               ep("new", "r1", ["y", "p"], 1.0, True), ep("new", "r2", ["y", "p"], 1.0, True)]
        points = behaviour_space(eps, names=NAMES)["branches"]["points"]
        self.assertEqual(points[0]["depth"], 0)
        self.assertAlmostEqual(points[0]["score"], 1.0)

    def test_one_policy_cannot_have_a_branch_point(self):
        branches = behaviour_space(HAND[:2], names=NAMES)["branches"]
        self.assertFalse(branches["measurable"])
        self.assertIn("two policies", branches["reason"])
        self.assertEqual(branches["points"], [])


class NgramTest(unittest.TestCase):
    """The habits: counts per policy, and a ratio of rates between the
    winning and the losing episodes with all four counts carried."""

    def setUp(self):
        self.grams = behaviour_space(HAND, names=NAMES)["ngrams"]

    def test_the_commonest_grams_per_policy_are_counted(self):
        old2 = {r["text"]: r["count"] for r in self.grams["by_policy"]["old"]["2"]}
        self.assertEqual(old2, {"plan → search": 2, "search → search": 2, "search → answer": 2})
        new3 = {r["text"]: r["count"] for r in self.grams["by_policy"]["new"]["3"]}
        self.assertEqual(new3, {"plan → read → answer": 2})

    def test_a_gram_the_losers_never_play_leads_with_no_ratio(self):
        win = self.grams["winning"]
        self.assertTrue(win["measurable"])
        self.assertEqual((win["winners"], win["losers"]), (2, 2))
        top = win["top"][0]
        self.assertEqual(top["text"], "plan → read")
        self.assertIsNone(top["ratio"])
        self.assertTrue(top["only_in_winners"])
        self.assertEqual((top["win_count"], top["lose_count"]), (2, 0))
        self.assertEqual((top["win_episodes"], top["lose_episodes"]), (2, 0))

    def test_the_losing_end_is_the_losers_own_habit(self):
        bottom = self.grams["winning"]["bottom"][0]
        self.assertEqual(bottom["text"], "plan → search")
        self.assertAlmostEqual(bottom["ratio"], 0.0)
        self.assertEqual((bottom["win_count"], bottom["lose_count"]), (0, 2))

    def test_the_ratio_is_the_two_rates_and_the_rates_are_shares(self):
        # winners play 4 two-grams in all, losers 6; a gram in half of the
        # winners' and a third of the losers' is 1.5× over-represented
        eps = [ep("new", "r1", ["a", "b", "a", "b"], 1.0, True),
               ep("new", "r2", ["a", "b", "a", "b"], 1.0, True),
               ep("old", "r1", ["a", "b", "c", "c"], -1.0, False),
               ep("old", "r2", ["a", "b", "c", "c"], -1.0, False)]
        rows = {r["text"]: r for r in ngrams(
            [dict(e, steps=len(e["tokens"])) for e in eps], ["new", "old"], sizes=(2,))["winning"]["top"]}
        ab = rows["a → b"]
        self.assertEqual((ab["win_count"], ab["lose_count"]), (4, 2))
        self.assertAlmostEqual(ab["win_rate"], 4 / 6, places=4)      # 6 two-grams among the winners
        self.assertAlmostEqual(ab["lose_rate"], 2 / 6, places=4)
        self.assertAlmostEqual(ab["ratio"], 2.0)
        self.assertAlmostEqual(ab["win_per_episode"], 2.0)
        self.assertAlmostEqual(ab["lose_per_episode"], 1.0)

    def test_the_separating_list_ranks_both_ends_by_distance_from_parity(self):
        rows = self.grams["winning"]["separating"]
        self.assertTrue(rows)
        # a gram the losers never play has no ratio at all: it leads
        self.assertIsNone(rows[0]["ratio"])
        rest = [r for r in rows if r["ratio"] is not None]
        lifts = [abs(math.log(r["ratio"])) if r["ratio"] else float("inf") for r in rest]
        self.assertEqual(lifts, sorted(lifts, reverse=True))
        # both ends are in it: the losers' habit is here too
        self.assertIn("plan → search", [r["text"] for r in rows])

    def test_a_gram_under_the_minimum_count_is_not_ranked(self):
        eps = [ep("new", "r1", ["a", "b", "c"], 1.0, True), ep("old", "r1", ["a", "b", "d"], -1.0, False)]
        win = behaviour_space(eps, names=("new", "old"))["ngrams"]["winning"]
        self.assertFalse(win["measurable"])
        self.assertIn("2 occurrences", win["reason"])

    def test_one_sided_outcomes_are_not_a_ratio(self):
        wins = [dict(e, success=True) for e in HAND]
        win = behaviour_space(wins, names=NAMES)["ngrams"]["winning"]
        self.assertFalse(win["measurable"])
        self.assertIn("every episode succeeded", win["reason"])
        self.assertEqual(win["top"], [])

    def test_the_rate_denominator_is_declared(self):
        note = self.grams["winning"]["note"]
        self.assertIn("share of every gram of that length", note)
        self.assertIn("per_episode", note)


class DistanceTest(unittest.TestCase):
    """Normalised edit distance: the fast path against the textbook one, the
    hand-known matrix, and each policy's spread."""

    def test_the_bit_parallel_distance_is_the_textbook_distance(self):
        rnd = random.Random(11)
        for _ in range(3000):
            a = [rnd.choice("abc") for _ in range(rnd.randrange(0, 9))]
            b = [rnd.choice("abcd") for _ in range(rnd.randrange(0, 9))]
            self.assertEqual(edit_distance(a, b), _edit_dp(a, b), (a, b))
        for _ in range(60):
            a = [rnd.choice("abcdefg") for _ in range(rnd.randrange(1, 70))]
            b = [rnd.choice("abcdefg") for _ in range(rnd.randrange(1, 70))]
            self.assertEqual(_edit_myers(a, b), _edit_dp(a, b))

    def test_the_known_distances(self):
        self.assertEqual(edit_distance([], []), 0)
        self.assertEqual(edit_distance(["a"], []), 1)
        self.assertEqual(edit_distance(["a", "b"], ["a", "b"]), 0)
        # plan search search answer → plan read answer: substitute, delete
        self.assertEqual(edit_distance(HAND[0]["tokens"], HAND[2]["tokens"]), 2)
        self.assertAlmostEqual(normalised_distance(HAND[0]["tokens"], HAND[2]["tokens"]), 0.5)
        self.assertAlmostEqual(normalised_distance([], []), 0.0)
        self.assertAlmostEqual(normalised_distance(["a"], ["b"]), 1.0)

    def test_the_matrix_the_spread_and_the_distance_between_policies(self):
        dist = behaviour_space(HAND, names=NAMES)["distance"]
        self.assertEqual(dist["matrix"], [[0.0, 0.0, 0.5, 0.5], [0.0, 0.0, 0.5, 0.5],
                                          [0.5, 0.5, 0.0, 0.0], [0.5, 0.5, 0.0, 0.0]])
        self.assertAlmostEqual(dist["within"]["old"]["spread"], 0.0)
        self.assertAlmostEqual(dist["within"]["new"]["spread"], 0.0)
        self.assertAlmostEqual(dist["between"], 0.5)
        self.assertFalse(dist["capped"])
        self.assertEqual((dist["counted"], dist["of"]), (4, 4))

    def test_a_spread_needs_two_episodes(self):
        dist = behaviour_space(HAND[:1], names=NAMES)["distance"]
        self.assertIsNone(dist["within"]["old"]["spread"])
        self.assertIn("a spread needs two", dist["within"]["old"]["reason"])
        self.assertIsNone(dist["within"]["new"]["spread"])
        self.assertIsNone(dist["between"])

    def test_the_nearest_neighbour_names_the_other_policy_when_it_is_nearer(self):
        # one `new` episode behaves exactly like the `old` ones
        eps = HAND[:2] + [ep("new", "r1", ["plan", "search", "search", "answer"], 4.0, True), HAND[3]]
        dist = behaviour_space(eps, names=NAMES)["distance"]
        by = {e["key"]: n for e, n in zip(dist["episodes"], dist["nearest"])}
        stray = by["new|t1|r1"]
        self.assertTrue(stray["other_policy"])
        self.assertAlmostEqual(stray["distance"], 0.0)
        self.assertEqual(stray["nearest_other"]["policy"], "old")

    def test_the_episode_cap_is_round_robin_over_the_policies_and_says_so(self):
        many = [ep("old", f"r{i}", ["plan"] + ["search"] * (i + 1), 0.0, False) for i in range(10)]
        many += [ep("new", f"r{i}", ["plan"] + ["read"] * (i + 1), 1.0, True) for i in range(10)]
        dist = distances([dict(e, key=f"{e['policy']}|{e['run_id']}") for e in many],
                         ["old", "new"], cap=6, token_cap=MAX_DISTANCE_TOKENS)
        self.assertTrue(dist["capped"])
        self.assertEqual((dist["counted"], dist["of"]), (6, 20))
        self.assertEqual(sorted(e["policy"] for e in dist["episodes"]),
                         ["new", "new", "new", "old", "old", "old"])
        self.assertIn("6 of 20 episodes", dist["note"])

    def test_the_token_cap_truncates_and_says_so(self):
        long = [ep("old", "r1", ["plan"] * 40, 0.0, False), ep("new", "r1", ["read"] * 40, 1.0, True)]
        dist = distances([dict(e, key=e["policy"]) for e in long], ["old", "new"], cap=10, token_cap=8)
        self.assertEqual(dist["tokens_truncated"], 2)
        self.assertIn("cut at 8 tokens", dist["note"])

    def test_the_matrix_is_dropped_from_the_output_when_it_would_be_huge(self):
        many = [ep("old" if i % 2 else "new", f"r{i}", ["plan", "search", "answer"][: (i % 3) + 1],
                   float(i), i % 2 == 0) for i in range(MATRIX_JSON_EPISODES + 2)]
        dist = behaviour_space(many, names=NAMES)["distance"]
        self.assertIsNone(dist["matrix"])
        self.assertIn("not carried", dist["matrix_note"])


class LayoutTest(unittest.TestCase):
    """Classical MDS: deterministic to the byte, and two identical episodes
    land in the same place."""

    def test_the_same_input_gives_the_same_bytes_twice(self):
        first = json.dumps(behaviour_space(HAND, names=NAMES)["layout"], sort_keys=True)
        second = json.dumps(behaviour_space(HAND, names=NAMES)["layout"], sort_keys=True)
        self.assertEqual(first, second)

    def test_the_demo_layout_is_the_same_bytes_twice(self):
        trajs = _trajectories(SMALL)
        one = json.dumps(rl_space(trajs)["layout"], sort_keys=True)
        two = json.dumps(rl_space(trajs)["layout"], sort_keys=True)
        self.assertEqual(one, two)

    def test_identical_episodes_land_in_the_same_place(self):
        points = {p["key"]: (p["x"], p["y"]) for p in behaviour_space(HAND, names=NAMES)["layout"]["points"]}
        self.assertEqual(points["old|t1|r1"], points["old|t1|r2"])
        self.assertEqual(points["new|t1|r1"], points["new|t1|r2"])
        self.assertNotEqual(points["old|t1|r1"], points["new|t1|r1"])

    def test_the_two_clouds_sit_their_distance_apart(self):
        points = {p["key"]: (p["x"], p["y"]) for p in behaviour_space(HAND, names=NAMES)["layout"]["points"]}
        gap = abs(points["old|t1|r1"][0] - points["new|t1|r1"][0])
        self.assertAlmostEqual(gap, 0.5, places=4)          # the distance itself, embedded exactly
        self.assertAlmostEqual(behaviour_space(HAND, names=NAMES)["layout"]["stress"], 0.0, places=6)

    def test_a_single_point_sits_at_the_origin(self):
        layout = mds([[0.0]])
        self.assertEqual(layout["points"], [[0.0, 0.0]])
        self.assertEqual(mds([])["points"], [])

    def test_an_all_zero_matrix_collapses_rather_than_inventing_a_spread(self):
        layout = mds([[0.0, 0.0], [0.0, 0.0]])
        self.assertEqual(layout["points"], [[0.0, 0.0], [0.0, 0.0]])

    def test_the_axes_are_declared_meaningless(self):
        space = behaviour_space(HAND, names=NAMES)
        self.assertIn("only relative position", space["layout"]["axes"])
        self.assertIn("relative position", mds.__doc__)


class PruningTest(unittest.TestCase):
    """Both prunings fold what they say they fold, and the output says what
    was folded."""

    def test_a_single_episode_tail_becomes_one_leaf(self):
        eps = [ep("old", "r1", ["a", "b", "c", "d", "e"], 1.0, True),
               ep("old", "r2", ["a", "b"], 2.0, True),
               ep("new", "r1", ["a", "b"], 3.0, True)]
        trie = build_trie([dict(e, steps=len(e["tokens"])) for e in eps], ["old", "new"])
        node = trie["root"]
        for token in ("a", "b", "c"):
            node = [c for c in node["children"] if c["token"] == token][0]
        self.assertEqual(node["tail"], 2)               # d and e folded under it
        self.assertEqual(node["children"], [])
        self.assertEqual(node["episodes"], 1)
        self.assertEqual(trie["pruned"]["tails"], 1)
        self.assertEqual(trie["pruned"]["tokens_folded"], 2)
        self.assertIn("single-episode tail", trie["pruned"]["note"])

    def test_the_depth_cap_stops_the_tree_and_counts_what_it_hid(self):
        eps = [ep("old", "r1", ["a"] * 10, 1.0, True), ep("old", "r2", ["a"] * 10, 2.0, True)]
        trie = build_trie([dict(e, steps=len(e["tokens"])) for e in eps], ["old"], max_depth=3)
        node = trie["root"]
        depth = 0
        while node["children"]:
            node = node["children"][0]
            depth += 1
        self.assertEqual(depth, 3)
        self.assertEqual(node["truncated"], 7)
        self.assertEqual(trie["pruned"]["truncated"], 1)
        self.assertEqual(trie["pruned"]["truncated_tokens"], 14)
        self.assertIn("depth cap of 3", trie["pruned"]["note"])

    def test_one_policys_own_trie_is_the_merged_one_restricted(self):
        space = behaviour_space(HAND, names=NAMES)
        old = policy_trie(space["trie"]["root"], "old")
        self.assertEqual(old["episodes"], 2)
        self.assertEqual(old["by_policy"], {"old": 2})
        tokens = []
        node = old
        while node["children"]:
            self.assertEqual(len(node["children"]), 1)
            node = node["children"][0]
            tokens.append(node["token"])
        self.assertEqual(tokens, ["plan", "search", "search", "answer"])
        self.assertIsNone(policy_trie(space["trie"]["root"], "nobody"))
        self.assertIn("by_policy", space["trie"]["per_policy"])

    def test_a_pruned_tail_still_carries_its_counts(self):
        eps = [ep("old", "r1", ["a", "b"], 4.0, True), ep("old", "r2", ["a", "c"], 1.0, True),
               ep("new", "r1", ["a", "c"], 3.0, False)]
        trie = build_trie([dict(e, steps=len(e["tokens"])) for e in eps], ["old", "new"])
        tail = [c for c in trie["root"]["children"][0]["children"] if c["token"] == "b"][0]
        self.assertEqual(tail["by_policy"], {"old": 1, "new": 0})
        self.assertAlmostEqual(tail["mean_return"], 4.0)
        self.assertAlmostEqual(tail["success_rate"], 1.0)


class DegenerateTest(unittest.TestCase):
    """Nothing is invented: every case that cannot be read says why."""

    def test_no_episodes(self):
        space = behaviour_space([])
        self.assertFalse(space["measurable"])
        self.assertEqual(space["reason"], "no episode to read")
        self.assertEqual(space["vocabulary"]["size"], 0)
        self.assertIsNone(space["trie"])
        self.assertIsNone(space["layout"])

    def test_an_empty_vocabulary(self):
        space = behaviour_space([ep("old", "r1", [], 0.0, False), ep("new", "r1", [], 0.0, True)],
                                names=NAMES)
        self.assertFalse(space["measurable"])
        self.assertIn("no behaviour to compare", space["reason"])
        self.assertEqual(space["vocabulary"]["size"], 0)

    def test_one_episode(self):
        space = behaviour_space(HAND[:1], names=NAMES)
        self.assertTrue(space["measurable"])
        self.assertEqual(space["episodes_n"], 1)
        self.assertFalse(space["branches"]["measurable"])
        self.assertEqual(space["layout"]["points"][0]["x"], 0.0)
        self.assertEqual(space["layout"]["points"][0]["y"], 0.0)
        self.assertIsNone(space["distance"]["nearest"][0])

    def test_identical_episodes_only(self):
        same = [ep("old", "r1", ["a", "b"], 1.0, True), ep("old", "r2", ["a", "b"], 1.0, True)]
        space = behaviour_space(same, names=("old",))
        self.assertEqual(space["distance"]["matrix"], [[0.0, 0.0], [0.0, 0.0]])
        self.assertAlmostEqual(space["distance"]["within"]["old"]["spread"], 0.0)
        self.assertEqual({tuple(p) for p in [(q["x"], q["y"]) for q in space["layout"]["points"]]},
                         {(0.0, 0.0)})

    def test_a_policy_with_no_episodes_at_all(self):
        space = behaviour_space(HAND[:2], names=NAMES)
        self.assertTrue(space["measurable"])
        self.assertEqual(space["vocabulary"]["frequency"]["new"], {"answer": 0, "plan": 0, "search": 0})
        self.assertEqual(space["vocabulary"]["signature"]["new"], [])
        self.assertEqual(space["distance"]["within"]["new"]["episodes"], 0)
        self.assertIsNone(space["distance"]["within"]["new"]["spread"])
        self.assertIsNone(space["distance"]["between"])
        self.assertFalse(space["branches"]["measurable"])

    def test_the_narrative_always_ends_in_a_sentence(self):
        for episodes in ([], HAND, HAND[:1], HAND[:2]):
            self.assertTrue(behaviour_space(episodes, names=NAMES)["narrative"].endswith("."))


class DemoTest(unittest.TestCase):
    """The numbers this section reports on the 96-episode training demo
    (2 policies × 6 tasks × 8 runs), and that it arrives in the aggregate."""

    @classmethod
    def setUpClass(cls):
        cls.space = rl_aggregate([], _trajectories(TRAIN), names=("policy-v1", "policy-v2"))["space"]

    def test_the_vocabulary_is_the_demo_tool_set(self):
        vocab = self.space["vocabulary"]
        self.assertEqual(vocab["size"], 7)
        self.assertEqual(vocab["tokens"],
                         ["answer", "grep", "plan", "read_file", "reason", "run_check", "search"])
        # both policies use every token: the signature is empty, which is
        # itself the finding — they differ in how much, not in what
        self.assertEqual(vocab["signature"], {"policy-v1": [], "policy-v2": []})
        self.assertEqual(len(vocab["shared"]), 7)
        self.assertEqual(self.space["episodes_n"], 96)

    def test_the_top_branch_point_and_the_return_on_each_side(self):
        point = self.space["branches"]["points"][0]
        self.assertEqual(point["depth"], 12)
        self.assertEqual(point["episodes"], 48)
        self.assertEqual(point["by_policy"], {"policy-v1": 24, "policy-v2": 24})
        self.assertAlmostEqual(point["imbalance"], 0.5833, places=4)
        self.assertAlmostEqual(point["score"], 0.2917, places=4)
        sides = {s["policy"]: s for s in point["sides"]}
        self.assertEqual(sides["policy-v1"]["token"], "search")
        self.assertEqual(sides["policy-v2"]["token"], "reason")
        self.assertAlmostEqual(sides["policy-v1"]["mean_return"], -3.4909, places=3)
        self.assertAlmostEqual(sides["policy-v2"]["mean_return"], 3.3846, places=3)
        # ranked: nothing below it scores higher
        scores = [p["score"] for p in self.space["branches"]["points"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_the_behavioural_spread_of_each_policy(self):
        within = self.space["distance"]["within"]
        self.assertAlmostEqual(within["policy-v1"]["spread"], 0.2345, places=4)
        self.assertAlmostEqual(within["policy-v2"]["spread"], 0.2172, places=4)
        self.assertAlmostEqual(self.space["distance"]["between"], 0.3, places=4)
        # 96 episodes is inside the cap, so nothing was left out
        self.assertFalse(self.space["distance"]["capped"])
        self.assertEqual(self.space["distance"]["counted"], 96)
        self.assertLessEqual(96, MAX_DISTANCE_EPISODES)

    def test_the_winning_and_losing_habits(self):
        win = self.space["ngrams"]["winning"]
        self.assertEqual((win["winners"], win["losers"]), (45, 51))
        top = win["top"][0]
        self.assertEqual(top["text"], "grep → grep → grep")
        self.assertEqual((top["win_count"], top["lose_count"]), (90, 102))
        self.assertAlmostEqual(top["ratio"], 1.2236, places=4)
        # …and its per-episode counts are equal, which is why the counts ship
        self.assertAlmostEqual(top["win_per_episode"], top["lose_per_episode"])
        # the verifier's retry loop is the losing habit
        bottom = win["bottom"][0]
        self.assertEqual(bottom["text"], "reason → run_check → reason")
        # and it is what leads the list ranked by distance from parity
        self.assertEqual(win["separating"][0]["text"], "reason → run_check → reason")
        self.assertEqual((bottom["win_count"], bottom["lose_count"]), (9, 35))
        self.assertAlmostEqual(bottom["ratio"], 0.3566, places=4)
        self.assertIn("run_check", win["bottom"][1]["text"])

    def test_the_layout_places_every_episode_once(self):
        points = self.space["layout"]["points"]
        self.assertEqual(len(points), 96)
        self.assertEqual(len({p["key"] for p in points}), 96)
        self.assertTrue(all(isinstance(p["x"], float) and isinstance(p["y"], float) for p in points))
        self.assertTrue(any(p["x"] != 0.0 for p in points))
        self.assertLess(self.space["layout"]["stress"], 0.35)

    def test_the_counter_example_is_in_the_atlas_and_findable(self):
        # rl05: policy-v2 fails every run where policy-v1 passes half of them
        v2 = [p for p in self.space["layout"]["points"]
              if p["task_id"] == "rl05_incident_postmortem" and p["policy"] == "policy-v2"]
        self.assertEqual(len(v2), 8)
        self.assertTrue(all(not p["success"] for p in v2))
        self.assertTrue(all(p["return"] < 0 for p in v2))
        v1 = [p for p in self.space["layout"]["points"]
              if p["task_id"] == "rl05_incident_postmortem" and p["policy"] == "policy-v1"]
        self.assertTrue(any(p["success"] for p in v1))
        # every mark carries what the page needs to draw and to hunt with
        for point in v2:
            self.assertTrue(point["tokens"])
            self.assertIsNotNone(point["nearest"])

    def test_the_pruning_is_reported(self):
        pruned = self.space["trie"]["pruned"]
        self.assertEqual(pruned["tails"], 29)
        self.assertEqual(pruned["truncated"], 22)
        self.assertIn("folded into a leaf", pruned["note"])
        self.assertIn("depth cap of 24", pruned["note"])

    def test_the_section_is_deterministic(self):
        again = rl_aggregate([], _trajectories(TRAIN), names=("policy-v1", "policy-v2"))["space"]
        self.assertEqual(json.dumps(self.space, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_the_small_demo_is_wired_into_the_aggregate(self):
        block = rl_aggregate([], _trajectories(SMALL), names=("policy-v1", "policy-v2"))
        space = block["space"]
        self.assertTrue(space["measurable"])
        self.assertEqual(space["episodes_n"], 12)
        self.assertEqual(space["vocabulary"]["size"], 7)
        self.assertEqual(space["branches"]["points"][0]["depth"], 12)
        self.assertTrue(space["narrative"].endswith("."))

    def test_the_episodes_of_the_space_are_the_episodes_of_the_section(self):
        block = rl_aggregate([], _trajectories(SMALL), names=("policy-v1", "policy-v2"))
        by_key = {f"{name}|{e['task_id']}|{e['run_id']}": e["return"]
                  for name, agent in block["agents"].items() for e in agent["episodes"]}
        for point in block["space"]["layout"]["points"]:
            self.assertAlmostEqual(point["return"], by_key[point["key"]])

    def test_the_episodes_come_straight_off_the_traces(self):
        trajs = _trajectories(SMALL)
        eps = behaviour_episodes(trajs)
        self.assertEqual(len(eps), 12)
        by_key = {(t.agent.name, t.task.id, t.run_id): t for t in trajs}
        for e in eps:
            traj = by_key[(e["policy"], e["task_id"], e["run_id"])]
            self.assertEqual(len(e["tokens"]), len(traj.steps))
            self.assertIsNone(e["return"])      # no agents block: no return is invented
            self.assertEqual(e["success"], traj.outcome.success is True)


if __name__ == "__main__":
    unittest.main()
