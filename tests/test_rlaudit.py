"""Auditing the reward and the critic.

The rank correlation, the explained variance, the concentration shares and
the advantage check are all computed by hand here first and the module has
to agree; the disagreement count is read off four hand-built episodes whose
inversions can be counted on fingers. The degenerate shapes — no values,
one episode, no variance, every episode passing — must say ``measurable:
False`` with a reason rather than produce a number. The wiring into
``aggregate["rl"]["audit"]`` and ``report["rl"]["audit"]`` is checked
against both RL demos, and the demo's own figures are pinned.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import Trajectory  # noqa: E402
from deepcompare.rlaudit import (  # noqa: E402
    ADV_TOL, BAD_LABELS, DOMINANT_SHARE, GOOD_LABELS, TERMINAL_SHARE, VERSION, critic_calibration,
    discounted_to_go, episodes_from_aggregate, episodes_from_pair, episodes_from_runs, rank_average,
    reward_integrity, rl_audit, spearman,
)
from deepcompare.suite import analyse_runs  # noqa: E402

RL_TRACES = ROOT / "demo" / "rl" / "traces"
RL_TRAIN = ROOT / "demo" / "rl" / "train"


# ------------------------------------------------------------------ builders

def row(step, reward, *, labels=None, value=None, advantage=None, kind=None, name=None):
    return {"step": step, "reward": reward, "labels": [] if labels is None else list(labels),
            "value": value, "advantage": advantage, "kind": kind, "name": name}


def run(agent, task, run_id, success, rewards, *, values=None, advantages=None, labels=None,
        kinds=None, names=None, seconds=1.0):
    """A run in the shape :func:`deepcompare.rl.rl_run_from_trace` returns,
    which is what the audit's full-detail adapter reads."""
    rows = []
    for i, r in enumerate(rewards):
        rows.append(row(i, r,
                        labels=(labels or {}).get(i) if isinstance(labels, dict) else (labels[i] if labels else None),
                        value=None if values is None else values[i],
                        advantage=None if advantages is None else advantages[i],
                        kind=None if kinds is None else kinds[i],
                        name=None if names is None else names[i]))
    return {"agent": agent, "measurable": True, "task_id": task, "run_id": run_id, "success": success,
            "seconds": seconds, "steps": len(rewards), "return": round(sum(rewards), 4),
            "discounted_return": None, "rewards": rows}


def episodes(*runs):
    return episodes_from_runs(list(runs))


# ------------------------------------------------------------------ the maths

class TestStatistics(unittest.TestCase):
    def test_average_ranks_split_a_tie_between_the_places_it_covers(self):
        self.assertEqual(rank_average([10, 20, 30]), [1.0, 2.0, 3.0])
        self.assertEqual(rank_average([5, 5, 9]), [1.5, 1.5, 3.0])
        self.assertEqual(rank_average([1, 1, 1, 1]), [2.5, 2.5, 2.5, 2.5])
        # the order of the input is the order of the output
        self.assertEqual(rank_average([9, 5, 5]), [3.0, 1.5, 1.5])

    def test_spearman_matches_the_hand_calculation(self):
        # ranks x = 1,2,3,4; ranks y = 1.5,1.5,3.5,3.5
        # cov = 4, var x = 5, var y = 4 -> 4 / sqrt(20)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [1, 1, 2, 2]), round(4 / math.sqrt(20), 4), places=4)
        self.assertEqual(spearman([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertEqual(spearman([1, 2, 3], [1, 2, 3]), 1.0)

    def test_spearman_refuses_what_it_cannot_answer(self):
        self.assertIsNone(spearman([1], [1]))
        self.assertIsNone(spearman([1, 2], [3]))
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))   # a constant has no order

    def test_the_discounted_return_to_go_is_the_backward_recurrence(self):
        self.assertEqual(discounted_to_go([1.0, 2.0, 3.0], 1.0), [6.0, 5.0, 3.0])
        got = discounted_to_go([0.0, 0.0, 5.0], 0.5)
        self.assertAlmostEqual(got[0], 1.25)
        self.assertAlmostEqual(got[1], 2.5)
        self.assertAlmostEqual(got[2], 5.0)
        self.assertEqual(discounted_to_go([], 0.99), [])


# ------------------------------------------------------- reward integrity

class TestDisagreement(unittest.TestCase):
    """One task, two passes and two failures, one of which out-earns a pass."""

    def setUp(self):
        self.eps = episodes(
            run("A", "t1", "r1", True, [10.0]),
            run("A", "t1", "r2", False, [12.0]),
            run("B", "t1", "r1", True, [20.0]),
            run("B", "t1", "r2", False, [1.0]),
        )
        self.block = reward_integrity(self.eps)

    def test_the_inversions_are_counted_over_every_pass_fail_pair(self):
        d = self.block["disagreement"]
        self.assertTrue(d["measurable"])
        self.assertEqual((d["passed"], d["failed"]), (2, 2))
        pooled = d["scopes"]["pooled"]
        self.assertEqual(pooled["pairs_n"], 4)
        self.assertEqual(pooled["inversions"], 1)      # only (pass 10, fail 12)
        self.assertEqual(pooled["ties"], 0)
        self.assertEqual(pooled["inversion_rate"], 0.25)
        self.assertEqual(pooled["separation"], -2.0)   # lowest pass 10 - highest fail 12

    def test_every_finding_names_its_evidence_and_is_only_a_signal(self):
        d = self.block["disagreement"]
        self.assertEqual(d["findings_n"], 2)
        kinds = sorted(f["kind"] for f in d["findings"])
        self.assertEqual(kinds, ["high_return_failed", "low_return_passed"])
        for f in d["findings"]:
            self.assertEqual(f["status"], "signal")
            self.assertEqual(f["task_id"], "t1")
            self.assertIn(f["agent"], ("A", "B"))
            self.assertEqual(f["gap"], 2.0)
            self.assertEqual(f["outranks"], 1)
            self.assertTrue(f["why"])
        # the episode table marks the same two and nobody else
        flagged = sorted((e["agent"], e["run_id"]) for e in self.block["episodes"] if e["flagged"])
        self.assertEqual(flagged, [("A", "r1"), ("A", "r2")])

    def test_returns_are_compared_within_a_task_as_well_as_pooled(self):
        two = episodes(
            run("A", "t1", "r1", True, [10.0]), run("A", "t1", "r2", False, [1.0]),
            run("A", "t2", "r1", True, [1.0]), run("A", "t2", "r2", False, [0.5]),
        )
        d = reward_integrity(two)["disagreement"]
        # pooled, t2's pass (1.0) sits under t1's failure (1.0) as a tie; within
        # each task nothing is out of order, which is the fair reading
        self.assertEqual(d["scopes"]["by_task"]["inversions"], 0)
        self.assertEqual(d["scopes"]["by_task"]["pairs_n"], 2)
        self.assertEqual(d["scopes"]["pooled"]["pairs_n"], 4)

    def test_the_shaping_reading_removes_the_last_step(self):
        # the answer step carries the outcome, so the full return agrees by
        # construction while what the policy collected on the way does not
        eps = episodes(
            run("A", "t1", "r1", True, [-1.0, -1.0, 5.0]),     # return 3, shaping -2
            run("A", "t1", "r2", False, [1.0, 1.0, -5.0]),     # return -3, shaping +2
        )
        d = reward_integrity(eps)["disagreement"]
        self.assertEqual(d["scopes"]["by_task"]["inversions"], 0)
        self.assertEqual(d["scopes"]["shaping"]["inversions"], 1)
        self.assertEqual(d["findings_basis"], "shaping")
        self.assertEqual([f["measure"] for f in d["findings"]], ["shaping return", "shaping return"])
        self.assertEqual(sorted(f["value"] for f in d["findings"]), [-2.0, 2.0])

    def test_the_policy_level_reading_catches_a_reward_that_prefers_the_loser(self):
        eps = episodes(
            run("A", "t1", "r1", True, [1.0]), run("A", "t1", "r2", True, [1.0]),
            run("B", "t1", "r1", False, [5.0]), run("B", "t1", "r2", False, [5.0]),
        )
        rows = reward_integrity(eps)["disagreement"]["by_policy"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["disagrees"])
        self.assertEqual(rows[0]["delta_mean_return"], 4.0)
        self.assertEqual(rows[0]["delta_pass_rate"], -1.0)
        self.assertIn("passes more often", rows[0]["why"])

    def test_one_outcome_only_is_not_measurable(self):
        for success in (True, False):
            d = reward_integrity(episodes(run("A", "t1", "r1", success, [1.0]),
                                          run("A", "t1", "r2", success, [2.0])))["disagreement"]
            self.assertFalse(d["measurable"])
            self.assertIn("no outcome to disagree with", d["reason"])


class TestRankAgreement(unittest.TestCase):
    def test_rho_and_its_ceiling_come_out_as_calculated_by_hand(self):
        eps = episodes(
            run("A", "t1", "r1", True, [10.0]), run("A", "t1", "r2", False, [12.0]),
            run("B", "t1", "r1", True, [20.0]), run("B", "t1", "r2", False, [1.0]),
        )
        rank = reward_integrity(eps)["rank_agreement"]
        self.assertTrue(rank["measurable"])
        self.assertAlmostEqual(rank["spearman"], round(2 / math.sqrt(20), 4), places=4)
        self.assertAlmostEqual(rank["ceiling"], round(4 / math.sqrt(20), 4), places=4)
        self.assertEqual(rank["n"], 4)
        self.assertIn("ceiling", rank["note"])
        self.assertIn("direction, not an estimate", rank["note"])   # under 20 episodes

    def test_a_perfect_reward_reaches_the_ceiling_and_no_further(self):
        eps = episodes(
            run("A", "t1", "r1", False, [-3.0]), run("A", "t1", "r2", False, [-2.0]),
            run("A", "t1", "r3", True, [4.0]), run("A", "t1", "r4", True, [5.0]),
        )
        rank = reward_integrity(eps)["rank_agreement"]
        self.assertEqual(rank["spearman"], rank["ceiling"])
        self.assertLess(rank["spearman"], 1.0)          # the binary outcome's ties bound it

    def test_two_episodes_and_one_outcome_are_both_refused(self):
        pair = reward_integrity(episodes(run("A", "t1", "r1", True, [1.0]),
                                         run("A", "t1", "r2", False, [2.0])))["rank_agreement"]
        self.assertFalse(pair["measurable"])
        self.assertIn("two points", pair["reason"])
        same = reward_integrity(episodes(run("A", "t1", "r1", True, [1.0]),
                                         run("A", "t1", "r2", True, [2.0]),
                                         run("A", "t1", "r3", True, [3.0])))["rank_agreement"]
        self.assertFalse(same["measurable"])
        self.assertIn("same outcome", same["reason"])


class TestConcentration(unittest.TestCase):
    def shape(self, rewards):
        block = reward_integrity(episodes(run("A", "t1", "r1", True, rewards)))["concentration"]
        return block, block["episodes"][0]

    def test_a_reward_paid_only_at_the_end_is_terminal(self):
        block, ep = self.shape([0.0, 0.0, 0.0, 5.0])
        self.assertEqual(ep["total_abs"], 5.0)
        self.assertEqual(ep["last_share"], 1.0)
        self.assertEqual(ep["largest_share"], 1.0)
        self.assertEqual(ep["nonzero"], 1)
        self.assertEqual(ep["peakedness"], 1.0)
        self.assertEqual(ep["kind"], "terminal")
        self.assertGreaterEqual(ep["last_share"], TERMINAL_SHARE)
        self.assertIn("sparse, terminal", block["note"])

    def test_an_evenly_paid_reward_is_dense(self):
        block, ep = self.shape([1.0, 1.0, 1.0, 1.0])
        self.assertEqual(ep["largest_share"], 0.25)
        self.assertEqual(ep["last_share"], 0.25)
        self.assertEqual(ep["peakedness"], 1.0)
        self.assertEqual(ep["kind"], "dense")
        self.assertEqual(block["mean_paid_share"], 1.0)

    def test_small_costs_under_one_big_payoff_are_terminal_dominated(self):
        block, ep = self.shape([-0.1] * 9 + [5.0])
        self.assertAlmostEqual(ep["total_abs"], 5.9, places=4)
        self.assertAlmostEqual(ep["last_share"], round(5.0 / 5.9, 4), places=4)
        self.assertTrue(ep["last_is_largest"])
        self.assertGreaterEqual(ep["last_share"], DOMINANT_SHARE)
        self.assertLess(ep["last_share"], TERMINAL_SHARE)
        self.assertEqual(ep["kind"], "terminal_dominated")
        self.assertIn("terminal payoff with a dense cost term", block["note"])
        # the share and the multiple are both in the sentence, so the reader
        # can disagree with the adjective
        self.assertIn("84.75%", block["note"])
        self.assertIn("8.47", block["note"])

    def test_a_peak_that_is_not_the_last_step_is_peaked(self):
        block, ep = self.shape([9.0, 1.0, 1.0])
        self.assertEqual(ep["largest_step"], 0)
        self.assertFalse(ep["last_is_largest"])
        self.assertEqual(ep["kind"], "peaked")

    def test_an_episode_paid_nothing_is_empty_not_a_division_by_zero(self):
        block, ep = self.shape([0.0, 0.0])
        self.assertEqual(ep["kind"], "empty")
        self.assertIsNone(ep["largest_share"])
        self.assertFalse(block["measurable"])
        self.assertIn("no episode was paid", block["reason"])


class TestUnearned(unittest.TestCase):
    def test_a_step_paid_while_labelled_bad_is_listed_with_its_evidence(self):
        eps = episodes(run("A", "t1", "r1", True, [1.0, -1.0, 0.5],
                           labels=[["dead_end"], ["fed_answer"], ["clean"]],
                           kinds=["read", "read", "answer"], names=["read_file", "read_file", "final"]))
        u = reward_integrity(eps)["unearned"]
        self.assertTrue(u["measurable"])
        self.assertEqual(u["steps_labelled"], 3)
        self.assertEqual(u["positive_while_bad"], 1)
        self.assertEqual(u["negative_while_good"], 1)
        kinds = sorted(r["kind"] for r in u["rows"])
        self.assertEqual(kinds, ["negative_while_good", "positive_while_bad"])
        bad = [r for r in u["rows"] if r["kind"] == "positive_while_bad"][0]
        self.assertEqual((bad["step"], bad["reward"], bad["labels"]), (0, 1.0, ["dead_end"]))
        self.assertEqual(bad["name"], "read_file")
        self.assertEqual(bad["status"], "signal")
        self.assertEqual(u["by_label"]["dead_end"]["A"], {"steps": 1, "reward": 1.0, "kind": "positive_while_bad"})

    def test_a_bad_label_beside_a_good_one_is_not_counted_as_punished_good(self):
        eps = episodes(run("A", "t1", "r1", True, [-1.0], labels=[["fed_answer", "error"]]))
        u = reward_integrity(eps)["unearned"]
        self.assertEqual(u["negative_while_good"], 0)
        self.assertEqual(u["positive_while_bad"], 0)

    def test_the_vocabularies_do_not_overlap(self):
        self.assertFalse(set(BAD_LABELS) & set(GOOD_LABELS))

    def test_unlabelled_steps_say_so_rather_than_report_zero(self):
        u = reward_integrity(episodes(run("A", "t1", "r1", True, [1.0])))["unearned"]
        # the builder gives every step an empty label list, which is not the
        # same as never having been labelled at all
        self.assertTrue(u["measurable"])
        self.assertEqual(u["positive_while_bad"], 0)
        agg = {"agents": {"A": {"episodes": [{"task_id": "t1", "run_id": "r1", "success": True,
                                              "rewards": [1.0], "values": [None], "advantages": [None],
                                              "return": 1.0, "steps": 1, "seconds": 1.0}]}}}
        bare = reward_integrity(episodes_from_aggregate(agg))["unearned"]
        self.assertFalse(bare["measurable"])
        self.assertIn("no step carries a label", bare["reason"])


class TestCostAndTools(unittest.TestCase):
    def test_return_per_step_and_per_second_separate_better_from_longer(self):
        eps = episodes(run("A", "t1", "r1", True, [2.0, 2.0], seconds=4.0),
                       run("B", "t1", "r1", True, [1.0, 1.0, 1.0, 1.0], seconds=2.0))
        cost = reward_integrity(eps)["cost"]["per_agent"]
        self.assertEqual(cost["A"]["return_per_step"], 2.0)
        self.assertEqual(cost["A"]["return_per_second"], 1.0)
        self.assertEqual(cost["B"]["return_per_step"], 1.0)
        self.assertEqual(cost["B"]["return_per_second"], 2.0)

    def test_the_reward_is_traced_through_the_tool_that_carried_it(self):
        eps = episodes(run("A", "t1", "r1", True, [2.0, -1.0, 5.0],
                           kinds=["read", "search", "answer"], names=["read_file", "search", "final"]))
        tools = reward_integrity(eps)["tools"]
        self.assertTrue(tools["measurable"])
        block = tools["per_agent"]["A"]
        self.assertEqual(block["tools"]["read_file"]["positive"], 2.0)
        self.assertEqual(block["tools"]["search"]["negative"], -1.0)
        self.assertEqual(block["top_tool"], "read_file")
        self.assertEqual(block["top_share"], 1.0)
        # the answer is not a tool, so its +5 never enters the tool split
        self.assertNotIn("final", block["tools"])
        self.assertEqual([f["kind"] for f in tools["findings"]], ["single_tool_reward"])
        self.assertIn("fragile policy", tools["findings"][0]["why"])

    def test_without_tool_names_the_check_says_so(self):
        tools = reward_integrity(episodes(run("A", "t1", "r1", True, [1.0])))["tools"]
        self.assertFalse(tools["measurable"])
        self.assertIn("no step names a tool", tools["reason"])


# ------------------------------------------------------ critic calibration

class TestCritic(unittest.TestCase):
    """rewards [1, 2, 3] at gamma 1 give returns-to-go [6, 5, 3], whose
    population variance is 14/9; every figure below follows from that."""

    REWARDS = [1.0, 2.0, 3.0]
    TOGO = [6.0, 5.0, 3.0]

    def critic(self, values, advantages=None, gamma=1.0):
        eps = episodes(run("A", "t1", "r1", True, self.REWARDS, values=values, advantages=advantages))
        return critic_calibration(eps, gamma=gamma)

    def test_the_residual_is_the_value_minus_what_actually_arrived(self):
        c = self.critic([5.0, 5.0, 4.0])
        self.assertTrue(c["measurable"])
        self.assertEqual([p["actual"] for p in c["points"]], self.TOGO)
        self.assertEqual([p["residual"] for p in c["points"]], [-1.0, 0.0, 1.0])

    def test_the_errors_and_the_explained_variance_match_the_hand_figures(self):
        c = self.critic([5.0, 5.0, 4.0])["overall"]
        var_actual = sum((v - 14 / 3) ** 2 for v in self.TOGO) / 3
        var_resid = (1 + 0 + 1) / 3
        self.assertEqual(c["n"], 3)
        self.assertEqual(c["mean_error"], 0.0)
        self.assertEqual(c["mean_absolute_error"], round(2 / 3, 4))
        self.assertAlmostEqual(c["rmse"], round(math.sqrt(2 / 3), 4), places=4)
        self.assertAlmostEqual(c["variance_actual"], round(var_actual, 4), places=4)
        self.assertAlmostEqual(c["explained_variance"], round(1 - var_resid / var_actual, 4), places=4)
        self.assertEqual(c["direction"], "unbiased")
        self.assertFalse(c["worse_than_the_mean"])

    def test_a_critic_worse_than_the_mean_scores_negative_and_says_so(self):
        c = self.critic([10.0, 0.0, 10.0])
        scores = c["overall"]
        var_actual = sum((v - 14 / 3) ** 2 for v in self.TOGO) / 3
        var_resid = sum((v - 2.0) ** 2 for v in (4.0, -5.0, 7.0)) / 3
        self.assertAlmostEqual(scores["explained_variance"], round(1 - var_resid / var_actual, 4), places=3)
        self.assertLess(scores["explained_variance"], 0)
        self.assertTrue(scores["worse_than_the_mean"])
        self.assertEqual(scores["direction"], "optimistic")
        self.assertIn("worse than predicting the mean", c["narrative"])
        self.assertIn("a constant equal to the average return-to-go", c["narrative"])

    def test_a_pessimistic_critic_is_named_pessimistic(self):
        self.assertEqual(self.critic([0.0, 0.0, 0.0])["overall"]["direction"], "pessimistic")

    def test_the_deciles_walk_the_predicted_value_from_low_to_high(self):
        values = [float(v) for v in range(20)]
        rewards = [1.0] * 20
        eps = episodes(run("A", "t1", "r1", True, rewards, values=values))
        c = critic_calibration(eps, gamma=1.0)
        self.assertEqual(len(c["deciles"]), 10)
        self.assertEqual([b["n"] for b in c["deciles"]], [2] * 10)
        self.assertEqual(c["deciles"][0]["predicted"], 0.5)
        self.assertEqual(c["deciles"][-1]["predicted"], 18.5)
        # fewer points than bins gives one bin per point, never an empty bin
        few = critic_calibration(episodes(run("A", "t1", "r1", True, [1.0, 1.0], values=[0.0, 1.0])), gamma=1.0)
        self.assertEqual(len(few["deciles"]), 2)
        self.assertTrue(all(b["n"] for b in few["deciles"]))

    def test_the_residual_marginal_covers_every_point_once(self):
        c = self.critic([5.0, 5.0, 4.0])
        self.assertEqual(sum(b["count"] for b in c["residual_bins"]), 3)
        for b in c["residual_bins"]:
            self.assertEqual(sum(b["by_agent"].values()), b["count"])

    def test_no_variance_in_what_arrived_leaves_nothing_to_explain(self):
        eps = episodes(run("A", "t1", "r1", True, [0.0, 0.0], values=[1.0, 1.0]))
        c = critic_calibration(eps, gamma=1.0)
        self.assertTrue(c["measurable"])
        self.assertIsNone(c["overall"]["explained_variance"])
        self.assertFalse(c["overall"]["worse_than_the_mean"])
        self.assertIn("no variance for a critic to explain", c["narrative"])

    def test_without_values_the_critic_is_not_measurable(self):
        c = critic_calibration(episodes(run("A", "t1", "r1", True, [1.0, 2.0])))
        self.assertFalse(c["measurable"])
        self.assertIn("no step", c["reason"])
        self.assertIn("value estimate", c["reason"])
        self.assertEqual(c["points"], [])
        self.assertEqual(c["n"], 0)


class TestAdvantages(unittest.TestCase):
    def test_a_recorded_advantage_that_matches_the_definition_passes(self):
        # to-go [6, 5, 3] at gamma 1, values [5, 5, 4] -> advantages [1, 0, -1]
        eps = episodes(run("A", "t1", "r1", True, [1.0, 2.0, 3.0], values=[5.0, 5.0, 4.0],
                           advantages=[1.0, 0.0, -1.0]))
        adv = critic_calibration(eps, gamma=1.0)["advantages"]
        self.assertTrue(adv["measurable"])
        self.assertEqual((adv["recorded"], adv["checked"], adv["inconsistent"]), (3, 3, 0))
        self.assertLessEqual(adv["max_error"], ADV_TOL)
        self.assertIn("discounted return-to-go", adv["definition"])

    def test_an_advantage_that_disagrees_with_its_own_rewards_is_reported(self):
        eps = episodes(run("A", "t1", "r1", True, [1.0, 2.0, 3.0], values=[5.0, 5.0, 4.0],
                           advantages=[1.0, 0.0, 5.0]))
        adv = critic_calibration(eps, gamma=1.0)["advantages"]
        self.assertTrue(adv["measurable"])
        self.assertEqual(adv["inconsistent"], 1)
        bad = adv["episodes"][0]
        self.assertEqual((bad["agent"], bad["task_id"], bad["run_id"]), ("A", "t1", "r1"))
        self.assertEqual(bad["steps"], [2])
        self.assertAlmostEqual(bad["max_error"], 6.0, places=4)
        self.assertEqual(bad["status"], "signal")
        self.assertIn("training-pipeline bug", adv["note"])

    def test_rounding_alone_never_trips_the_check(self):
        eps = episodes(run("A", "t1", "r1", True, [1.0, 2.0, 3.0], values=[5.0, 5.0, 4.0],
                           advantages=[1.0001, 0.0, -1.0001]))
        self.assertEqual(critic_calibration(eps, gamma=1.0)["advantages"]["inconsistent"], 0)

    def test_advantages_nobody_recorded_are_not_checked_against_ourselves(self):
        eps = episodes(run("A", "t1", "r1", True, [1.0, 2.0], values=[1.0, 1.0]))
        adv = critic_calibration(eps, gamma=1.0)["advantages"]
        self.assertFalse(adv["measurable"])
        self.assertIn("derived here", adv["reason"])
        self.assertEqual(adv["recorded"], 0)

    def test_an_advantage_without_a_value_cannot_be_evaluated(self):
        eps = episodes(run("A", "t1", "r1", True, [1.0, 2.0], advantages=[0.5, 0.5]))
        adv = critic_calibration(eps, gamma=1.0)["advantages"]
        self.assertFalse(adv["measurable"])
        self.assertEqual(adv["recorded"], 2)
        self.assertEqual(adv["checked"], 0)
        self.assertIn("none of them also records a value", adv["reason"])


# ---------------------------------------------------------------- the whole

class TestAudit(unittest.TestCase):
    def test_no_episodes_is_refused_with_a_reason(self):
        audit = rl_audit([])
        self.assertFalse(audit["measurable"])
        self.assertIn("no measurable episodes", audit["reason"])
        self.assertEqual(audit["version"], VERSION)
        self.assertTrue(audit["caveat"])

    def test_one_episode_measures_what_it_can_and_refuses_the_rest(self):
        audit = rl_audit(episodes(run("A", "t1", "r1", True, [1.0, -0.5, 5.0], values=[1.0, 2.0, 3.0])))
        self.assertTrue(audit["measurable"])
        self.assertEqual(audit["episodes_n"], 1)
        self.assertFalse(audit["reward"]["disagreement"]["measurable"])
        self.assertFalse(audit["reward"]["rank_agreement"]["measurable"])
        self.assertTrue(audit["reward"]["concentration"]["measurable"])
        self.assertTrue(audit["critic"]["measurable"])

    def test_every_finding_list_carries_the_caveat_and_a_signal_label(self):
        audit = rl_audit(episodes(
            run("A", "t1", "r1", True, [1.0], labels=[["dead_end"]]),
            run("A", "t1", "r2", False, [5.0]),
            run("B", "t1", "r1", True, [2.0]),
        ))
        self.assertIn("signal to investigate", audit["caveat"])
        self.assertIn("not a proven defect", audit["reward"]["disagreement"]["note"])
        for f in audit["reward"]["disagreement"]["findings"]:
            self.assertEqual(f["status"], "signal")
        for r in audit["reward"]["unearned"]["rows"]:
            self.assertEqual(r["status"], "signal")

    def test_the_same_episodes_always_give_the_same_bytes(self):
        def build():
            return rl_audit(episodes(
                run("B", "t2", "r1", False, [0.3, -0.2, -5.0], values=[0.1, 0.2, 0.3], labels=[["error"], [], []]),
                run("A", "t1", "r1", True, [1.0, -0.5, 5.0], values=[1.0, 2.0, 3.0], labels=[["dead_end"], [], []]),
                run("A", "t1", "r2", False, [2.0, 0.0, -5.0], values=[0.5, 0.5, 0.5], labels=[[], [], []]),
            ))
        first = json.dumps(build(), sort_keys=True)
        for _ in range(3):
            self.assertEqual(json.dumps(build(), sort_keys=True), first)

    def test_the_pair_adapter_keeps_the_side_so_a_page_can_open_the_step(self):
        rl = {"a": run("A", None, None, True, [1.0, 2.0], values=[1.0, 1.0]),
              "b": run("B", None, None, False, [-1.0, -2.0], values=[0.0, 0.0])}
        eps = episodes_from_pair(rl, task_id="t9", run_ids={"a": "r1", "b": "r2"})
        self.assertEqual([e["side"] for e in eps], ["a", "b"])
        self.assertEqual([e["task_id"] for e in eps], ["t9", "t9"])
        self.assertEqual([e["run_id"] for e in eps], ["r1", "r2"])
        self.assertEqual({p["side"] for p in rl_audit(eps, scope="pair")["critic"]["points"]}, {"a", "b"})


# --------------------------------------------------------------- the demos

@unittest.skipUnless(RL_TRACES.is_dir(), "the RL demo traces are not present")
class TestSmallDemo(unittest.TestCase):
    """demo/rl/traces: 12 episodes, 2 policies x 2 tasks x 3 runs."""

    @classmethod
    def setUpClass(cls):
        trajectories = [Trajectory.from_json(p) for p in sorted(RL_TRACES.glob("*.json"))]
        cls.result = analyse_runs(trajectories)
        cls.audit = cls.result["aggregate"]["rl"]["audit"]

    def test_the_audit_is_wired_into_the_aggregate(self):
        self.assertEqual(self.audit["version"], VERSION)
        self.assertTrue(self.audit["measurable"])
        self.assertEqual(self.audit["scope"], "batch")
        self.assertEqual(self.audit["episodes_n"], 12)
        self.assertEqual(self.audit["policies"], ["policy-v1", "policy-v2"])
        self.assertEqual(self.audit["gamma"], 0.99)

    def test_the_pair_report_carries_its_own_audit(self):
        report = self.result["reports"][0]
        audit = report["rl"]["audit"]
        self.assertEqual(audit["scope"], "pair")
        self.assertEqual(audit["episodes_n"], 2)
        self.assertTrue(audit["critic"]["measurable"])
        # two episodes cannot support a rank correlation and it says so
        self.assertFalse(audit["reward"]["rank_agreement"]["measurable"])

    def test_the_return_and_the_outcome_never_disagree_but_the_shaping_does(self):
        d = self.audit["reward"]["disagreement"]
        self.assertEqual((d["passed"], d["failed"]), (7, 5))
        self.assertEqual((d["scopes"]["pooled"]["inversions"], d["scopes"]["pooled"]["pairs_n"]), (0, 35))
        self.assertEqual((d["scopes"]["by_task"]["inversions"], d["scopes"]["by_task"]["pairs_n"]), (0, 17))
        self.assertEqual((d["scopes"]["shaping"]["inversions"], d["scopes"]["shaping"]["pairs_n"]), (1, 17))
        self.assertEqual(d["findings_basis"], "shaping")
        self.assertEqual(d["findings_n"], 2)

    def test_the_rank_correlation_sits_at_its_ceiling(self):
        rank = self.audit["reward"]["rank_agreement"]
        self.assertEqual(rank["n"], 12)
        self.assertEqual(rank["spearman"], 0.8584)
        self.assertEqual(rank["ceiling"], 0.8584)
        self.assertEqual(rank["spearman_shaping"], 0.6645)

    def test_the_reward_is_a_terminal_payoff_over_a_dense_cost(self):
        con = self.audit["reward"]["concentration"]
        self.assertEqual(con["kind"], "terminal_dominated")
        self.assertEqual(con["mean_largest_share"], 0.4219)
        self.assertEqual(con["mean_last_share"], 0.4219)
        self.assertEqual(len(con["episodes"]), 12)

    def test_both_critics_are_worse_than_the_mean_on_their_own_episodes(self):
        critic = self.audit["critic"]
        self.assertEqual(critic["n"], 91)
        self.assertEqual(critic["episodes_covered"], 12)
        self.assertEqual(critic["overall"]["explained_variance"], 0.2211)
        self.assertEqual(critic["per_agent"]["policy-v1"]["explained_variance"], -0.1151)
        self.assertEqual(critic["per_agent"]["policy-v2"]["explained_variance"], -0.1395)
        for name in ("policy-v1", "policy-v2"):
            self.assertTrue(critic["per_agent"][name]["worse_than_the_mean"], name)
            self.assertIn("worse than predicting the mean", critic["per_agent"][name]["words"])

    def test_the_demo_records_no_advantages_of_its_own(self):
        adv = self.audit["critic"]["advantages"]
        self.assertFalse(adv["measurable"])
        self.assertEqual(adv["recorded"], 0)


@unittest.skipUnless(RL_TRAIN.is_dir(), "the larger RL demo is not present")
class TestTrainDemo(unittest.TestCase):
    """demo/rl/train: 96 episodes, 2 policies x 6 tasks x 8 runs."""

    @classmethod
    def setUpClass(cls):
        trajectories = [Trajectory.from_json(p) for p in sorted(RL_TRAIN.glob("*.json"))]
        cls.audit = analyse_runs(trajectories)["aggregate"]["rl"]["audit"]

    def test_the_headline_counts(self):
        self.assertEqual(self.audit["episodes_n"], 96)
        d = self.audit["reward"]["disagreement"]
        self.assertEqual((d["passed"], d["failed"]), (45, 51))
        self.assertEqual((d["scopes"]["by_task"]["inversions"], d["scopes"]["by_task"]["pairs_n"]), (0, 367))
        self.assertEqual((d["scopes"]["shaping"]["inversions"], d["scopes"]["shaping"]["pairs_n"]), (23, 367))
        self.assertEqual(d["findings_n"], 24)
        self.assertEqual(len(d["flagged"]), 24)
        self.assertEqual(sum(1 for e in self.audit["reward"]["episodes"] if e["flagged"]), 24)

    def test_the_disagreements_concentrate_on_the_task_that_was_built_for_them(self):
        tasks = self.audit["reward"]["disagreement"]["scopes"]["shaping"]["tasks"]
        worst = max(tasks, key=lambda t: (tasks[t]["inversions"], t))
        self.assertEqual(worst, "rl05_incident_postmortem")
        self.assertEqual(tasks[worst]["inversions"], 11)
        self.assertEqual(tasks[worst]["passed"], 4)

    def test_the_rank_correlation_and_what_the_last_step_carries(self):
        rank = self.audit["reward"]["rank_agreement"]
        self.assertEqual((rank["n"], rank["spearman"], rank["ceiling"]), (96, 0.8647, 0.8647))
        self.assertEqual(rank["spearman_shaping"], 0.7027)

    def test_the_critics_explained_variance_per_policy(self):
        critic = self.audit["critic"]
        self.assertEqual(critic["n"], 762)
        self.assertEqual(critic["overall"]["explained_variance"], 0.4199)
        self.assertEqual(critic["per_agent"]["policy-v1"]["explained_variance"], -0.088)
        self.assertTrue(critic["per_agent"]["policy-v1"]["worse_than_the_mean"])
        self.assertEqual(critic["per_agent"]["policy-v2"]["explained_variance"], 0.4129)
        self.assertFalse(critic["per_agent"]["policy-v2"]["worse_than_the_mean"])
        self.assertEqual(len(critic["deciles"]), 10)

    def test_the_shaping_pays_for_dead_ends(self):
        u = self.audit["reward"]["unearned"]
        self.assertEqual(u["steps_labelled"], 3858)
        self.assertEqual(u["positive_while_bad"], 358)
        self.assertEqual(u["negative_while_good"], 0)
        self.assertEqual(sorted(u["by_label"]), ["dead_end"])
        self.assertEqual(u["by_label"]["dead_end"]["policy-v1"]["steps"], 175)
        self.assertEqual(u["by_label"]["dead_end"]["policy-v2"]["steps"], 183)
        # the listed rows are a spread, not a dozen from one run
        self.assertGreaterEqual(len({(r["agent"], r["task_id"]) for r in u["rows"]}), 6)

    def test_all_the_positive_tool_reward_runs_through_one_tool(self):
        tools = self.audit["reward"]["tools"]
        self.assertEqual([f["tool"] for f in tools["findings"]], ["read_file", "read_file"])
        for name in ("policy-v1", "policy-v2"):
            self.assertEqual(tools["per_agent"][name]["top_tool"], "read_file")
            self.assertEqual(tools["per_agent"][name]["top_share"], 1.0)

    def test_the_reward_per_unit_of_cost_separates_the_policies(self):
        cost = self.audit["reward"]["cost"]["per_agent"]
        self.assertLess(cost["policy-v1"]["return_per_step"], 0)
        self.assertGreater(cost["policy-v2"]["return_per_step"], 0)
        self.assertLess(cost["policy-v2"]["mean_steps"], cost["policy-v1"]["mean_steps"])

    def test_the_whole_section_is_stable_across_two_readings(self):
        trajectories = [Trajectory.from_json(p) for p in sorted(RL_TRAIN.glob("*.json"))]
        again = analyse_runs(trajectories)["aggregate"]["rl"]["audit"]
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.audit, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
