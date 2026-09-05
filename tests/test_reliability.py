"""Tests for reliability over repeated runs (pass^k, consistency, ICC).

These assertions are mostly about *honesty*, not arithmetic.  The arithmetic
is four lines of ``math.comb``; what makes the module worth having is that it
refuses to produce a number it cannot support.  So the tests pin: pass^k is
None above the run count rather than 0.0, harness failures are removed and
counted rather than charged to the agent, unequal trial counts are flagged
rather than averaged over silently, and — the contrast the whole metric exists
for — a task solved 3 times out of 4 scores pass^4 = 0 while its success rate
reads a comfortable 75%.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.reliability import (
    MIN_RUNS_FOR_COMPARISON,
    RUNS_FLOOR_OPEN_ENDED,
    RUNS_FLOOR_STRUCTURED,
    action_sequence,
    coverage_at_k,
    icc_one_way,
    is_harness_failure,
    outcome_consistency,
    reliability,
    resource_consistency,
    runs_advisory,
    sequence_consistency,
    sequence_similarity,
    split_harness_failures,
)
from deepcompare.statistics import pass_at_k

CANON = [("plan", "plan"), ("search", "web_search"), ("read", "open_page"),
         ("answer", "final")]
OTHER = [("plan", "plan"), ("tool_call", "calculator"), ("answer", "final")]


def make_traj(agent, task, success, run_id="r1", steps=CANON, cost=0.01,
              latency=5.0, step_tokens=100, termination=None):
    step_dicts = [
        {"index": i, "type": step_type, "name": name,
         "input": f"{name} input", "output": f"{name} output",
         "tokens": step_tokens, "latency_s": 1.0}
        for i, (step_type, name) in enumerate(steps)
    ]
    total = step_tokens * len(steps)
    return Trajectory.from_json({
        "schema_version": 1,
        "trace_id": f"{agent}-{task}-{run_id}",
        "run_id": run_id,
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "e"},
        "outcome": {"success": success, "answer": "a",
                    "score": 1.0 if success else 0.0,
                    "termination": termination},
        "totals": {"input_tokens": total // 2, "output_tokens": total - total // 2,
                   "cost_usd": cost, "latency_s": latency},
        "steps": step_dicts,
    })


def suite(spec, agent="atlas-v2", side="a"):
    """``{task: [outcome-or-kwargs, ...]}`` -> a runs_by_task_agent mapping."""
    by_task = {}
    for task, runs in spec.items():
        built = []
        for i, run in enumerate(runs):
            kwargs = {"success": run} if isinstance(run, bool) else dict(run)
            built.append(make_traj(agent, task, run_id=f"r{i + 1}", **kwargs))
        by_task[task] = {side: built}
    return by_task


# --------------------------------------------------------------------------
# the contrast that justifies the metric
# --------------------------------------------------------------------------

class TestPassHatVersusMeanSuccess(unittest.TestCase):
    def test_three_of_four_has_pass_hat_four_of_zero(self):
        # The whole point: 75% success and 0% four-in-a-row reliability are
        # the same agent on the same task.
        result = reliability(suite({"t1": [True, True, True, False]}))
        row = result["per_agent"]["a"]
        self.assertEqual(row["mean_success_rate"], 0.75)
        self.assertEqual(row["max_k"], 4)
        curve = {point["k"]: point["value"] for point in row["pass_hat_k"]["curve"]}
        self.assertEqual(curve[1], 0.75)
        self.assertEqual(curve[4], 0.0)
        self.assertLess(curve[2], curve[1])
        self.assertLess(curve[3], curve[2])

    def test_coverage_rises_while_reliability_falls(self):
        result = reliability(suite({"t1": [True, True, True, False]}))
        row = result["per_agent"]["a"]
        hat = [point["value"] for point in row["pass_hat_k"]["curve"]]
        cov = [point["value"] for point in row["pass_at_k"]["curve"]]
        self.assertEqual(hat, sorted(hat, reverse=True))
        self.assertEqual(cov, sorted(cov))
        self.assertEqual(cov[3], 1.0)  # at least one of four passed
        self.assertEqual(hat[3], 0.0)  # not all four

    def test_whole_curve_is_reported_not_one_k(self):
        result = reliability(suite({"t1": [True, False, True, True, False]}))
        row = result["per_agent"]["a"]
        self.assertEqual([p["k"] for p in row["pass_hat_k"]["curve"]], [1, 2, 3, 4, 5])
        self.assertEqual([p["k"] for p in row["pass_at_k"]["curve"]], [1, 2, 3, 4, 5])


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

class TestPassHatK(unittest.TestCase):
    def test_none_above_the_run_count(self):
        # Never 0.0 — an unmeasurable k must not read as "it never works".
        self.assertIsNone(pass_at_k(3, 3, 4))
        self.assertIsNone(pass_at_k(0, 2, 3))

    def test_matches_the_combinatorial_definition(self):
        for n in range(1, 7):
            for c in range(n + 1):
                for k in range(1, n + 1):
                    expected = math.comb(c, k) / math.comb(n, k) if c >= k else 0.0
                    self.assertAlmostEqual(pass_at_k(c, n, k), round(expected, 4),
                                           places=4)


class TestCoverageAtK(unittest.TestCase):
    def test_none_above_the_run_count(self):
        self.assertIsNone(coverage_at_k(2, 3, 4))
        self.assertIsNone(coverage_at_k(2, 3, 0))

    def test_matches_the_unbiased_estimator(self):
        for n in range(1, 7):
            for c in range(n + 1):
                for k in range(1, n + 1):
                    failures = n - c
                    expected = (1.0 if failures < k else
                                1.0 - math.comb(failures, k) / math.comb(n, k))
                    self.assertAlmostEqual(coverage_at_k(c, n, k),
                                           round(expected, 4), places=4)

    def test_never_below_pass_hat_k(self):
        for n in range(1, 7):
            for c in range(n + 1):
                for k in range(1, n + 1):
                    self.assertGreaterEqual(coverage_at_k(c, n, k), pass_at_k(c, n, k))


class TestOutcomeConsistency(unittest.TestCase):
    def test_unanimous_runs_score_one(self):
        self.assertEqual(outcome_consistency(4, 4), 1.0)
        self.assertEqual(outcome_consistency(0, 4), 1.0)

    def test_coin_flip_scores_zero(self):
        self.assertEqual(outcome_consistency(2, 4), 0.0)
        self.assertEqual(outcome_consistency(5, 10), 0.0)

    def test_single_run_cannot_agree_with_itself(self):
        self.assertIsNone(outcome_consistency(1, 1))
        self.assertIsNone(outcome_consistency(0, 1))

    def test_monotone_towards_the_middle(self):
        self.assertGreater(outcome_consistency(3, 4), outcome_consistency(2, 4))


class TestSequenceConsistency(unittest.TestCase):
    def test_identical_paths_score_one(self):
        self.assertEqual(sequence_similarity(["a", "b"], ["a", "b"]), 1.0)

    def test_disjoint_paths_score_zero(self):
        self.assertEqual(sequence_similarity(["a", "b"], ["c", "d"]), 0.0)

    def test_two_empty_paths_did_not_diverge(self):
        self.assertEqual(sequence_similarity([], []), 1.0)

    def test_uses_type_and_name(self):
        traj = make_traj("x", "t1", True)
        self.assertEqual(action_sequence(traj),
                         ["plan:plan", "search:web_search", "read:open_page",
                          "answer:final"])

    def test_single_run_returns_none(self):
        self.assertIsNone(sequence_consistency([make_traj("x", "t1", True)]))

    def test_mean_pairwise_over_runs(self):
        same = [make_traj("x", "t1", True, run_id=f"r{i}") for i in range(3)]
        self.assertEqual(sequence_consistency(same), 1.0)
        mixed = same[:2] + [make_traj("x", "t1", True, run_id="r9", steps=OTHER)]
        self.assertLess(sequence_consistency(mixed), 1.0)

    def test_conditioned_on_successful_runs(self):
        # A failed run that took a wild path must not drag the score down:
        # HAL conditions on successes, and the basis says so.
        spec = {"t1": [True, True, {"success": False, "steps": OTHER}]}
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertEqual(row["trajectory_consistency"]["value"], 1.0)
        self.assertIn("successful runs only",
                      row["trajectory_consistency"]["basis"])
        self.assertEqual(row["per_task"][0]["trajectory_consistency"]["runs_compared"], 2)


class TestResourceConsistency(unittest.TestCase):
    def test_single_run_returns_none_with_reason(self):
        block = resource_consistency([make_traj("x", "t1", True)])
        self.assertIsNone(block["value"])
        self.assertTrue(block["reason"])

    def test_identical_runs_are_perfectly_consistent(self):
        runs = [make_traj("x", "t1", True, run_id=f"r{i}") for i in range(3)]
        self.assertEqual(resource_consistency(runs)["value"], 1.0)

    def test_unlogged_resource_is_dropped_not_scored_perfect(self):
        # cost_usd = 0 everywhere means "not logged", not "perfectly stable".
        runs = [make_traj("x", "t1", True, run_id=f"r{i}", cost=0.0)
                for i in range(3)]
        block = resource_consistency(runs)
        self.assertNotIn("cost_usd", block["by_resource"])
        self.assertIn("tokens", block["by_resource"])

    def test_no_resources_logged_at_all_returns_none(self):
        runs = []
        for i in range(3):
            traj = make_traj("x", "t1", True, run_id=f"r{i}", cost=0.0,
                             latency=0.0, step_tokens=0)
            traj.totals.input_tokens = 0
            traj.totals.output_tokens = 0
            traj.steps = []
            runs.append(traj)
        block = resource_consistency(runs)
        self.assertIsNone(block["value"])
        self.assertIn("logged", block["reason"])

    def test_variation_lowers_the_score(self):
        runs = [make_traj("x", "t1", True, run_id="r1", latency=1.0),
                make_traj("x", "t1", True, run_id="r2", latency=20.0)]
        block = resource_consistency(runs)
        self.assertLess(block["by_resource"]["latency_s"], 1.0)
        self.assertGreater(block["by_resource"]["latency_s"], 0.0)


class TestICC(unittest.TestCase):
    def test_one_task_cannot_separate_the_variances(self):
        block = icc_one_way([[1.0, 0.0, 1.0]])
        self.assertIsNone(block["icc1"])
        self.assertIn("2 tasks", block["reason"])

    def test_no_variance_returns_none_not_a_number(self):
        block = icc_one_way([[1.0, 1.0], [1.0, 1.0]])
        self.assertIsNone(block["icc1"])
        self.assertIn("no variance", block["reason"])

    def test_one_run_per_task_cannot_estimate_within_variance(self):
        block = icc_one_way([[1.0], [0.0], [1.0]])
        self.assertIsNone(block["icc1"])

    def test_perfectly_separated_tasks_give_icc_one(self):
        block = icc_one_way([[1.0, 1.0, 1.0], [0.0, 0.0, 0.0]])
        self.assertEqual(block["icc1_clamped"], 1.0)
        self.assertEqual(block["within_task_variance_share"], 0.0)

    def test_shares_sum_to_one(self):
        block = icc_one_way([[1.0, 0.0, 1.0], [1.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertAlmostEqual(block["between_task_variance_share"]
                               + block["within_task_variance_share"], 1.0, places=4)

    def test_negative_estimate_is_reported_raw_and_clamped(self):
        # Within-task variance above between-task variance: the raw estimate
        # goes negative, and hiding that would hide the alarming finding.
        block = icc_one_way([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        self.assertTrue(block["negative_raw"])
        self.assertLess(block["icc1"], 0.0)
        self.assertEqual(block["icc1_clamped"], 0.0)
        self.assertEqual(block["within_task_variance_share"], 1.0)

    def test_carries_its_denominators_and_a_caveat(self):
        block = icc_one_way([[1.0, 0.0, 1.0], [1.0, 1.0, 0.0]])
        self.assertEqual(block["tasks"], 2)
        self.assertEqual(block["observations"], 6)
        self.assertTrue(block["caveat"])

    def test_handles_unbalanced_groups(self):
        block = icc_one_way([[1.0, 1.0, 1.0, 1.0], [0.0, 0.0]])
        self.assertIsNotNone(block["icc1"])
        self.assertEqual(block["observations"], 6)


class TestRunsAdvisory(unittest.TestCase):
    def test_three_runs_supports_no_comparison(self):
        advisory = runs_advisory([3, 3, 3])
        self.assertEqual(advisory["tier"], "insufficient")
        self.assertTrue(any("more reliable than another" in s
                            for s in advisory["does_not_support"]))

    def test_names_the_actual_n(self):
        advisory = runs_advisory([3, 5, 9])
        self.assertEqual(advisory["n_min"], 3)
        self.assertEqual(advisory["n_max"], 9)
        self.assertIn("3 run(s) per task", advisory["message"])

    def test_structured_floor(self):
        self.assertEqual(runs_advisory([RUNS_FLOOR_STRUCTURED] * 3)["tier"],
                         "structured-ok")
        self.assertEqual(runs_advisory([RUNS_FLOOR_STRUCTURED - 2] * 3)["tier"],
                         "below-floor")
        self.assertEqual(runs_advisory([MIN_RUNS_FOR_COMPARISON - 1] * 3)["tier"],
                         "insufficient")

    def test_open_ended_floor(self):
        advisory = runs_advisory([RUNS_FLOOR_OPEN_ENDED] * 2)
        self.assertEqual(advisory["tier"], "open-ended-ok")
        self.assertTrue(any("open-ended" in s for s in advisory["supports"]))

    def test_thresholds_are_surfaced_as_fields(self):
        advisory = runs_advisory([4])
        self.assertEqual(advisory["thresholds"]["structured_floor"],
                         RUNS_FLOOR_STRUCTURED)


# --------------------------------------------------------------------------
# harness-failure exclusion
# --------------------------------------------------------------------------

class TestHarnessExclusion(unittest.TestCase):
    def test_recognises_harness_terminations(self):
        for reason in ("infrastructure_error", "unexpected_error"):
            self.assertTrue(is_harness_failure(
                make_traj("x", "t1", False, termination=reason)))
        for reason in (None, "agent_error", "max_steps", "timeout"):
            self.assertFalse(is_harness_failure(
                make_traj("x", "t1", False, termination=reason)))

    def test_split_keeps_order_and_counts(self):
        runs = [make_traj("x", "t1", True, run_id="r1"),
                make_traj("x", "t1", False, run_id="r2",
                          termination="infrastructure_error"),
                make_traj("x", "t1", True, run_id="r3")]
        keep, drop = split_harness_failures(runs)
        self.assertEqual([t.run_id for t in keep], ["r1", "r3"])
        self.assertEqual([t.run_id for t in drop], ["r2"])

    def test_harness_failure_is_not_charged_to_the_agent(self):
        spec = {"t1": [True, True,
                       {"success": False, "termination": "infrastructure_error"}]}
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertEqual(row["runs_total"], 3)
        self.assertEqual(row["runs_used"], 2)
        self.assertEqual(row["mean_success_rate"], 1.0)  # not 0.6667
        self.assertEqual(row["max_k"], 2)

    def test_exclusions_are_counted_and_itemised(self):
        spec = {
            "t1": [True, {"success": False, "termination": "infrastructure_error"}],
            "t2": [True, {"success": False, "termination": "unexpected_error"}],
        }
        block = reliability(suite(spec))["per_agent"]["a"]["excluded_runs"]
        self.assertEqual(block["count"], 2)
        self.assertEqual(block["of_runs"], 4)
        self.assertEqual(block["by_termination"],
                         {"infrastructure_error": 1, "unexpected_error": 1})
        self.assertEqual({r["task"] for r in block["runs"]}, {"t1", "t2"})

    def test_agent_error_is_still_the_agents_fault(self):
        spec = {"t1": [True, {"success": False, "termination": "agent_error"}]}
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertEqual(row["excluded_runs"]["count"], 0)
        self.assertEqual(row["mean_success_rate"], 0.5)

    def test_task_emptied_by_exclusion_is_reported_not_silently_dropped(self):
        spec = {
            "t1": [True, True],
            "t2": [{"success": False, "termination": "infrastructure_error"}],
        }
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertEqual(row["excluded_runs"]["tasks_left_empty"], ["t2"])
        self.assertEqual(row["tasks"], 2)
        self.assertEqual(row["tasks_scored"], 1)
        self.assertEqual(row["max_k"], 2)  # t2 does not cap the curve at 0
        t2 = [t for t in row["per_task"] if t["task"] == "t2"][0]
        self.assertIsNone(t2["success_rate"])
        self.assertTrue(all(p["value"] is None for p in t2["pass_hat_k"]))


# --------------------------------------------------------------------------
# guards on k
# --------------------------------------------------------------------------

class TestMaxK(unittest.TestCase):
    def test_max_k_is_the_thinnest_task(self):
        spec = {"t1": [True] * 5, "t2": [True, False]}
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertEqual(row["max_k"], 2)
        self.assertEqual(len(row["pass_hat_k"]["curve"]), 2)
        self.assertIn("minimum per-task", row["max_k_basis"])

    def test_unequal_trials_are_flagged(self):
        spec = {"t1": [True] * 5, "t2": [True, False]}
        block = reliability(suite(spec))["per_agent"]["a"]["unequal_trials"]
        self.assertTrue(block["flagged"])
        self.assertEqual(block["min"], 2)
        self.assertEqual(block["max"], 5)
        self.assertEqual(block["per_task"], {"t1": 5, "t2": 2})
        self.assertIn("differ", block["note"])

    def test_equal_trials_are_not_flagged(self):
        block = reliability(suite({"t1": [True] * 3,
                                   "t2": [False] * 3}))["per_agent"]["a"]["unequal_trials"]
        self.assertFalse(block["flagged"])

    def test_unequal_trials_flagged_after_exclusion_not_before(self):
        # Same raw trial count, different eligible count: the flag must read
        # the post-exclusion numbers.
        spec = {
            "t1": [True, True, True],
            "t2": [True, True,
                   {"success": True, "termination": "infrastructure_error"}],
        }
        block = reliability(suite(spec))["per_agent"]["a"]["unequal_trials"]
        self.assertTrue(block["flagged"])
        self.assertEqual(block["per_task"], {"t1": 3, "t2": 2})

    def test_single_run_per_task_stops_the_curve_at_one(self):
        row = reliability(suite({"t1": [True], "t2": [False]}))["per_agent"]["a"]
        self.assertEqual(row["max_k"], 1)
        self.assertEqual([p["k"] for p in row["pass_hat_k"]["curve"]], [1])
        self.assertIsNone(row["outcome_consistency"]["value"])
        self.assertTrue(row["outcome_consistency"]["reason"])

    def test_no_eligible_runs_leaves_an_empty_curve_with_a_reason(self):
        spec = {"t1": [{"success": True, "termination": "infrastructure_error"}]}
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertEqual(row["max_k"], 0)
        self.assertEqual(row["pass_hat_k"]["curve"], [])
        self.assertTrue(row["pass_hat_k"]["reason"])
        self.assertIsNone(row["mean_success_rate"])
        self.assertIn("no reliability number", row["narrative"])


# --------------------------------------------------------------------------
# assembled output
# --------------------------------------------------------------------------

class TestReliabilityOutput(unittest.TestCase):
    def setUp(self):
        self.spec_a = {
            "t1": [True, True, True],
            "t2": [True, False, True],
            "t3": [False, False, False],
        }

    def test_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            reliability({})

    def test_shape_is_json_safe(self):
        result = reliability(suite(self.spec_a))
        encoded = json.dumps(result)
        self.assertEqual(json.loads(encoded), result)

    def test_top_level_keys(self):
        result = reliability(suite(self.spec_a))
        self.assertEqual(set(result),
                         {"agents", "tasks", "per_agent", "definitions",
                          "narrative"})
        self.assertEqual(result["agents"], {"a": "atlas-v2"})
        self.assertEqual(result["tasks"], 3)
        self.assertTrue(result["narrative"])

    def test_every_rate_carries_its_denominator(self):
        row = reliability(suite(self.spec_a))["per_agent"]["a"]
        self.assertEqual(row["successes"], 5)
        self.assertEqual(row["runs_used"], 9)
        self.assertEqual(row["mean_success_rate"], round(5 / 9, 4))
        for key in ("outcome_consistency", "trajectory_consistency",
                    "resource_consistency"):
            self.assertIn("tasks_scored", row[key])
            self.assertIn("of_tasks", row[key])
        self.assertEqual(row["pass_hat_k"]["tasks"], 3)
        self.assertEqual(row["pass_hat_k"]["runs"], 9)
        for entry in row["per_task"]:
            self.assertIn("runs", entry)
            self.assertIn("successes", entry)

    def test_every_metric_states_its_source(self):
        row = reliability(suite(self.spec_a))["per_agent"]["a"]
        self.assertIn("tau", row["pass_hat_k"]["source"])
        self.assertIn("arXiv", row["pass_at_k"]["source"])
        self.assertIn("HAL", row["outcome_consistency"]["source"])
        self.assertIn("HAL", row["trajectory_consistency"]["source"])

    def test_pass_hat_k_averages_over_tasks(self):
        row = reliability(suite(self.spec_a))["per_agent"]["a"]
        curve = {p["k"]: p["value"] for p in row["pass_hat_k"]["curve"]}
        # t1 = 3/3, t2 = 2/3, t3 = 0/3.
        self.assertAlmostEqual(curve[1], round((1.0 + 2 / 3 + 0.0) / 3, 4), places=3)
        self.assertAlmostEqual(curve[3], round((1.0 + 0.0 + 0.0) / 3, 4), places=4)

    def test_stable_fail_task_is_perfectly_consistent_but_never_passes(self):
        row = reliability(suite({"t3": [False, False, False]}))["per_agent"]["a"]
        self.assertEqual(row["outcome_consistency"]["value"], 1.0)
        self.assertEqual(row["pass_hat_k"]["curve"][0]["value"], 0.0)
        self.assertIsNone(row["trajectory_consistency"]["value"])
        self.assertIn("successful", row["trajectory_consistency"]["reason"])

    def test_coin_flip_task_scores_zero_consistency(self):
        row = reliability(suite({"t1": [True, False]}))["per_agent"]["a"]
        self.assertEqual(row["outcome_consistency"]["value"], 0.0)

    def test_two_sides_are_reported_separately(self):
        spec = suite({"t1": [True, True], "t2": [True, False]})
        other = suite({"t1": [False, False], "t2": [True, True]},
                      agent="bolt-v3", side="b")
        for task in spec:
            spec[task].update(other[task])
        result = reliability(spec)
        self.assertEqual(result["agents"], {"a": "atlas-v2", "b": "bolt-v3"})
        self.assertEqual(result["per_agent"]["a"]["mean_success_rate"], 0.75)
        self.assertEqual(result["per_agent"]["b"]["mean_success_rate"], 0.5)
        self.assertIn("cannot be ranked", result["narrative"])

    def test_deterministic(self):
        spec = suite(self.spec_a)
        first = reliability(spec)
        second = reliability(copy.deepcopy(spec))
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_narrative_mentions_the_advisory(self):
        row = reliability(suite(self.spec_a))["per_agent"]["a"]
        self.assertIn("run(s) per task", row["narrative"])

    def test_narrative_reports_exclusions(self):
        spec = {"t1": [True, True,
                       {"success": False, "termination": "infrastructure_error"}]}
        row = reliability(suite(spec))["per_agent"]["a"]
        self.assertIn("harness failure", row["narrative"])


class TestDemoSuite(unittest.TestCase):
    """The shipped multi-run demo must produce a coherent block end to end."""

    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "runs" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo/runs/traces not present")
        by_task = {}
        for path in sorted(traces.glob("*.json")):
            traj = Trajectory.from_json(path)
            traj.run_id = path.stem.split("__")[2]
            by_task.setdefault(traj.task.id, {}).setdefault(traj.agent.name, [])
            by_task[traj.task.id][traj.agent.name].append(traj)
        cls.result = reliability(by_task)

    def test_reports_both_agents(self):
        self.assertEqual(len(self.result["per_agent"]), 2)

    def test_curves_are_complete_and_capped_at_three(self):
        for row in self.result["per_agent"].values():
            self.assertEqual(row["max_k"], 3)
            self.assertFalse(row["unequal_trials"]["flagged"])
            for point in row["pass_hat_k"]["curve"]:
                self.assertIsNotNone(point["value"])

    def test_advisory_refuses_the_agent_comparison_at_three_runs(self):
        for row in self.result["per_agent"].values():
            self.assertEqual(row["runs_advisory"]["tier"], "insufficient")

    def test_flaky_agent_shows_the_reliability_gap(self):
        rows = {row["agent"]: row for row in self.result["per_agent"].values()}
        flaky = rows["bolt-v3"]
        self.assertLess(flaky["pass_hat_k"]["value_at_max_k"],
                        flaky["pass_at_k"]["value_at_max_k"])


if __name__ == "__main__":
    unittest.main()
