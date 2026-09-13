"""Two policies from few episodes: IQM, the stratified bootstrap, the
performance profile and the probability of improvement.

Every statistic is checked against a value computed by hand on a matrix
small enough to check by hand; the bootstrap is checked for byte identity
under a fixed seed and for actually stratifying (a resample keeps every
task's run count, which is the whole claim); the probability of improvement
is checked on a matrix whose answer is arithmetic; and every degenerate
shape the runs layout can hand it — one task, one run, all-equal scores, a
policy missing a task, one policy, none — returns something honest rather
than a number. The shipped demo numbers are pinned for both demo sets.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import Trajectory  # noqa: E402
from deepcompare.rl import rl_aggregate  # noqa: E402
from deepcompare.rlstats import (  # noqa: E402
    AGGREGATES, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED, METRICS, PROFILE_POINTS, VERSION,
    aggregate_metrics, default_target, iqm, mean, median, optimality_gap,
    performance_profile, probability_of_improvement, rl_stats, sample_advisory,
    score_matrix, stratified_resamples, tau_grid, within_task_probability,
)
from deepcompare.rlstats import _rng  # noqa: E402

RL_TRACES = ROOT / "demo" / "rl" / "traces"
RL_TRAIN = ROOT / "demo" / "rl" / "train"


def _block(episodes_by_policy: dict) -> dict:
    """The slice of ``aggregate["rl"]`` that :func:`rl_stats` reads."""
    agents = {}
    for policy, rows in episodes_by_policy.items():
        episodes = []
        for i, (task, score) in enumerate(rows):
            episodes.append({"task_id": task, "run_id": f"r{i}", "return": score,
                             "discounted_return": score, "steps": 10, "seconds": 1.0,
                             "success": score > 0})
        agents[policy] = {"episodes": episodes, "episodes_n": len(episodes)}
    return {"version": 1, "gamma": 0.99, "source": "recorded", "agents": agents}


def _matrix(episodes_by_policy: dict, metric: str = "return") -> dict:
    return score_matrix(_block(episodes_by_policy), metric)


def _aggregate(directory: Path) -> dict:
    trajectories = []
    for path in sorted(directory.glob("*.json")):
        traj = Trajectory.from_json(path)
        traj.run_id = path.stem.split("__")[2]
        trajectories.append(traj)
    names = tuple(sorted({t.agent.name for t in trajectories}))
    return rl_aggregate([], trajectories, names=names)


# --------------------------------------------------------------- the pieces

class InterquartileMeanTest(unittest.TestCase):
    def test_the_middle_half_by_hand(self):
        # 8 values, a quarter (2) cut from each end: mean(3, 4, 5, 6)
        self.assertAlmostEqual(iqm([8, 1, 3, 5, 4, 6, 7, 2]), 4.5)
        # 4 values, one cut from each end: mean(2, 3)
        self.assertAlmostEqual(iqm([1, 2, 3, 4]), 2.5)
        # 12 values, three cut from each end: mean(4..9)
        self.assertAlmostEqual(iqm(list(range(1, 13))), 6.5)

    def test_it_ignores_the_outlier_the_mean_chases(self):
        clean = [4, 5, 5, 6, 5, 5, 4, 6]
        spoilt = clean[:-1] + [1000]
        self.assertEqual(iqm(clean), iqm(spoilt))
        self.assertNotEqual(mean(clean), mean(spoilt))

    def test_under_four_values_nothing_is_cut(self):
        for values in ([7], [7, 9], [7, 9, 11]):
            self.assertAlmostEqual(iqm(values), mean(values), msg=str(values))

    def test_an_empty_sample_has_no_interquartile_mean(self):
        self.assertIsNone(iqm([]))
        self.assertIsNone(median([]))
        self.assertIsNone(mean([]))


class PointStatisticsTest(unittest.TestCase):
    def test_median_by_hand(self):
        self.assertAlmostEqual(median([3, 1, 2]), 2.0)
        self.assertAlmostEqual(median([4, 1, 3, 2]), 2.5)

    def test_the_optimality_gap_is_the_mean_shortfall_and_never_negative(self):
        # target 10: shortfalls 8, 5, 0, 0 -> 13/4
        self.assertAlmostEqual(optimality_gap([2, 5, 10, 12], 10), 3.25)
        self.assertAlmostEqual(optimality_gap([12, 20], 10), 0.0)

    def test_the_gap_mirrors_when_lower_is_better(self):
        # target 10 steps: overruns 0, 0, 5, 10 -> 15/4
        self.assertAlmostEqual(
            optimality_gap([2, 10, 15, 20], 10, higher_is_better=False), 3.75)

    def test_the_default_target_is_the_best_score_observed_and_says_so(self):
        matrix = _matrix({"A": [("t1", 1.0), ("t1", 3.0)], "B": [("t1", 9.0), ("t1", 2.0)]})
        value, source = default_target(matrix)
        self.assertEqual(value, 9.0)
        self.assertIn("best score observed", source)

    def test_the_default_target_flips_when_lower_is_better(self):
        matrix = _matrix({"A": [("t1", 1.0)], "B": [("t1", 9.0)]}, metric="steps")
        # every constructed episode carries steps=10, so the best is 10
        self.assertEqual(default_target(matrix)[0], 10.0)


class WithinTaskProbabilityTest(unittest.TestCase):
    def test_every_b_run_beats_every_a_run(self):
        self.assertAlmostEqual(within_task_probability([0, 1], [2, 3]), 1.0)

    def test_ties_count_half(self):
        # pairs: (0,1) win, (0,1) win, (1,1) tie, (1,1) tie -> 3/4
        self.assertAlmostEqual(within_task_probability([0, 1], [1, 1]), 0.75)
        self.assertAlmostEqual(within_task_probability([5, 5], [5, 5]), 0.5)

    def test_it_reverses_when_lower_is_better(self):
        self.assertAlmostEqual(
            within_task_probability([5, 5], [1, 1], higher_is_better=False), 1.0)
        self.assertAlmostEqual(
            within_task_probability([1, 1], [5, 5], higher_is_better=False), 0.0)

    def test_a_side_with_no_runs_has_no_probability(self):
        self.assertIsNone(within_task_probability([], [1]))
        self.assertIsNone(within_task_probability([1], []))


# ---------------------------------------------------------------- bootstrap

class StratifiedBootstrapTest(unittest.TestCase):
    def test_a_resample_keeps_every_task_run_count(self):
        by_task = {"t1": [0.0, 0.0, 0.0], "t2": [100.0] * 4}
        draws = stratified_resamples(by_task, 200, _rng(BOOTSTRAP_SEED, "test"))
        self.assertEqual(len(draws), 200)
        for draw in draws:
            self.assertEqual(len(draw), 7)
            self.assertEqual(sum(1 for v in draw if v == 0.0), 3)
            self.assertEqual(sum(1 for v in draw if v == 100.0), 4)

    def test_stratifying_is_not_the_same_as_pooling(self):
        # each task is internally constant, so within-task resampling cannot
        # move the statistic at all; a pooled bootstrap plainly would.
        matrix = _matrix({"A": [("t1", 0.0)] * 3 + [("t2", 100.0)] * 4})
        row = aggregate_metrics(matrix, "A", target=100.0, samples=400)
        self.assertTrue(row["mean"]["degenerate"])
        self.assertEqual(row["mean"]["width"], 0.0)
        self.assertAlmostEqual(row["mean"]["point"], (0 * 3 + 100 * 4) / 7, places=3)

    def test_runs_within_a_task_are_the_thing_that_moves(self):
        matrix = _matrix({"A": [("t1", 0.0), ("t1", 10.0), ("t2", 0.0), ("t2", 10.0)]})
        row = aggregate_metrics(matrix, "A", target=10.0, samples=400)
        self.assertFalse(row["mean"]["degenerate"])
        self.assertLess(row["mean"]["lo"], row["mean"]["point"])
        self.assertGreater(row["mean"]["hi"], row["mean"]["point"])

    def test_the_same_seed_gives_the_same_bytes_twice(self):
        block = _block({"A": [("t1", 1.0), ("t1", 4.0), ("t2", -2.0)],
                        "B": [("t1", 3.0), ("t1", 2.0), ("t2", 9.0)]})
        first = json.dumps(rl_stats(block, samples=300), sort_keys=True)
        second = json.dumps(rl_stats(block, samples=300), sort_keys=True)
        self.assertEqual(first, second)

    def test_a_different_seed_moves_the_interval_but_not_the_point(self):
        block = _block({"A": [("t1", 1.0), ("t1", 4.0), ("t2", -2.0), ("t2", 8.0)],
                        "B": [("t1", 3.0), ("t1", 2.0), ("t2", 9.0), ("t2", 0.0)]})
        one = rl_stats(block, samples=300)
        two = rl_stats(block, samples=300, seed=BOOTSTRAP_SEED + 1)
        self.assertEqual(one["aggregates"]["A"]["iqm"]["point"],
                         two["aggregates"]["A"]["iqm"]["point"])
        self.assertNotEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))

    def test_the_interval_is_labelled_a_bootstrap_over_the_observed_runs(self):
        stats = rl_stats(_block({"A": [("t1", 1.0)], "B": [("t1", 2.0)]}), samples=50)
        self.assertIn("not a claim about a population", stats["bootstrap"]["basis"])
        self.assertIn("within each task", stats["bootstrap"]["method"])
        self.assertEqual(stats["bootstrap"]["seed"], BOOTSTRAP_SEED)


# ------------------------------------------------------------------ profile

class PerformanceProfileTest(unittest.TestCase):
    def test_the_grid_is_shared_and_starts_where_the_data_starts(self):
        matrix = _matrix({"A": [("t1", -3.0), ("t1", 1.0)], "B": [("t1", 0.0), ("t1", 5.0)]})
        taus = tau_grid(matrix)
        self.assertEqual(len(taus), PROFILE_POINTS)
        self.assertAlmostEqual(taus[0], -3.0)
        self.assertAlmostEqual(taus[-1], 5.0)

    def test_the_curve_starts_at_one_and_falls(self):
        matrix = _matrix({"A": [("t1", 0.0), ("t1", 2.0), ("t1", 4.0)],
                          "B": [("t1", 1.0), ("t1", 3.0), ("t1", 5.0)]})
        profile = performance_profile(matrix, samples=200)
        for policy in ("A", "B"):
            rows = profile["curves"][policy]
            self.assertAlmostEqual(rows[0][1], 1.0)
            for earlier, later in zip(rows, rows[1:]):
                self.assertLessEqual(later[1], earlier[1] + 1e-12)
                self.assertLessEqual(later[2], later[1] + 1e-12)
                self.assertGreaterEqual(later[3], later[1] - 1e-12)

    def test_a_dominant_policy_is_named_and_no_crossing_is_reported(self):
        matrix = _matrix({"A": [("t1", 0.0), ("t1", 1.0), ("t1", 2.0)],
                          "B": [("t1", 5.0), ("t1", 6.0), ("t1", 7.0)]})
        profile = performance_profile(matrix, samples=200)
        self.assertEqual(profile["dominant"], "B")
        self.assertEqual(profile["crossings"], [])
        self.assertIn("every τ", profile["reading"])

    def test_crossing_curves_are_reported_as_crossing(self):
        # A is reliably mediocre, B is either terrible or excellent: below the
        # middle A has more runs clearing the bar, above it B does.
        matrix = _matrix({"A": [("t1", 4.0), ("t1", 5.0), ("t1", 6.0), ("t1", 5.0)],
                          "B": [("t1", 0.0), ("t1", 0.0), ("t1", 10.0), ("t1", 10.0)]})
        profile = performance_profile(matrix, samples=200)
        self.assertTrue(profile["crossings"], "the curves must cross")
        self.assertIsNone(profile["dominant"])
        self.assertIn("depends on the threshold", profile["reading"])

    def test_identical_policies_have_identical_profiles(self):
        matrix = _matrix({"A": [("t1", 1.0), ("t1", 2.0)], "B": [("t1", 1.0), ("t1", 2.0)]})
        profile = performance_profile(matrix, samples=100)
        self.assertEqual(profile["curves"]["A"], profile["curves"]["B"])
        self.assertEqual(profile["crossings"], [])
        self.assertIsNone(profile["dominant"])
        self.assertIn("identical", profile["reading"])


# ------------------------------------------------- probability of improvement

class ProbabilityOfImprovementTest(unittest.TestCase):
    def test_the_answer_is_the_average_over_tasks_not_over_runs(self):
        # t1: every B run beats every A run -> 1.0
        # t2: every A run beats every B run -> 0.0
        # the average over tasks is 0.5 even though t1 has more runs
        matrix = _matrix({
            "A": [("t1", 0.0), ("t1", 0.0), ("t1", 0.0), ("t2", 9.0)],
            "B": [("t1", 1.0), ("t1", 1.0), ("t1", 1.0), ("t2", 1.0)],
        })
        result = probability_of_improvement(matrix, samples=200)
        self.assertAlmostEqual(result["point"], 0.5)
        self.assertEqual(result["per_task"], {"t1": 1.0, "t2": 0.0})
        self.assertEqual(result["regressions"], ["t2"])

    def test_a_hand_computed_mixture_with_ties(self):
        # t1: A [0, 1] vs B [1, 1] -> (1 + 1 + 0.5 + 0.5)/4 = 0.75
        # t2: A [2, 2] vs B [2, 3] -> (0.5 + 0.5 + 1 + 1)/4 = 0.75
        matrix = _matrix({"A": [("t1", 0.0), ("t1", 1.0), ("t2", 2.0), ("t2", 2.0)],
                          "B": [("t1", 1.0), ("t1", 1.0), ("t2", 2.0), ("t2", 3.0)]})
        result = probability_of_improvement(matrix, samples=200)
        self.assertEqual(result["per_task"], {"t1": 0.75, "t2": 0.75})
        self.assertAlmostEqual(result["point"], 0.75)

    def test_it_is_not_the_same_question_as_whose_mean_is_higher(self):
        # B wins 3 of 4 runs on every task, but A's one huge run carries the mean
        matrix = _matrix({"A": [("t1", 100.0), ("t1", 0.0), ("t1", 0.0), ("t1", 0.0)],
                          "B": [("t1", 1.0), ("t1", 1.0), ("t1", 1.0), ("t1", 1.0)]})
        result = probability_of_improvement(matrix, samples=200)
        self.assertAlmostEqual(result["point"], 0.75)
        self.assertGreater(mean([100.0, 0.0, 0.0, 0.0]), mean([1.0] * 4))

    def test_a_task_only_one_policy_ran_is_skipped_and_named(self):
        matrix = _matrix({"A": [("t1", 0.0), ("t2", 0.0)], "B": [("t1", 1.0)]})
        result = probability_of_improvement(matrix, samples=100)
        self.assertEqual(result["tasks_used"], 1)
        self.assertEqual(result["tasks_skipped"], ["t2"])
        self.assertAlmostEqual(result["point"], 1.0)

    def test_it_needs_two_policies(self):
        result = probability_of_improvement(_matrix({"A": [("t1", 1.0)]}), samples=10)
        self.assertFalse(result["measurable"])
        self.assertIn("two policies", result["reason"])

    def test_it_reverses_when_lower_is_better(self):
        block = _block({"A": [("t1", 1.0)], "B": [("t1", 2.0)]})
        block["agents"]["A"]["episodes"][0]["steps"] = 50
        block["agents"]["B"]["episodes"][0]["steps"] = 10
        result = probability_of_improvement(score_matrix(block, "steps"), samples=50)
        self.assertAlmostEqual(result["point"], 1.0)  # B takes fewer steps


# ---------------------------------------------------------------- the matrix

class ScoreMatrixTest(unittest.TestCase):
    def test_every_metric_is_selectable(self):
        block = _block({"A": [("t1", 3.0)], "B": [("t1", -1.0)]})
        for metric in sorted(METRICS):
            matrix = score_matrix(block, metric)
            self.assertEqual(matrix["metric"], metric)
            self.assertEqual(sorted(matrix["policies"]), ["A", "B"])
            self.assertTrue(matrix["by_policy"]["A"]["t1"])

    def test_success_reads_as_one_or_zero(self):
        block = _block({"A": [("t1", 3.0), ("t1", -1.0)]})
        matrix = score_matrix(block, "success")
        self.assertEqual(matrix["by_policy"]["A"]["t1"], [0.0, 1.0])

    def test_steps_and_seconds_are_lower_is_better(self):
        for metric in ("steps", "seconds"):
            self.assertFalse(METRICS[metric][2], metric)
        for metric in ("return", "discounted_return", "success"):
            self.assertTrue(METRICS[metric][2], metric)

    def test_an_unknown_metric_is_a_clear_error(self):
        with self.assertRaises(ValueError) as caught:
            score_matrix(_block({"A": [("t1", 1.0)]}), "vibes")
        self.assertIn("discounted_return", str(caught.exception))

    def test_an_episode_without_the_score_is_dropped_and_counted(self):
        block = _block({"A": [("t1", 1.0), ("t1", 2.0)]})
        block["agents"]["A"]["episodes"][0]["return"] = None
        matrix = score_matrix(block, "return")
        self.assertEqual(matrix["episodes_dropped"], 1)
        self.assertEqual(matrix["by_policy"]["A"]["t1"], [2.0])


# ----------------------------------------------------------------- degenerate

class DegenerateShapeTest(unittest.TestCase):
    def test_no_episodes_at_all(self):
        stats = rl_stats({"agents": {}}, samples=50)
        self.assertFalse(stats["measurable"])
        self.assertIn("no episodes", stats["reason"])
        self.assertIn("nothing can be compared", stats["narrative"])

    def test_a_missing_rl_block(self):
        self.assertFalse(rl_stats({}, samples=50)["measurable"])
        self.assertFalse(rl_stats(None, samples=50)["measurable"])

    def test_one_task_still_bootstraps(self):
        stats = rl_stats(_block({"A": [("t1", 1.0), ("t1", 5.0), ("t1", 9.0)],
                                 "B": [("t1", 2.0), ("t1", 6.0), ("t1", 7.0)]}), samples=200)
        self.assertTrue(stats["measurable"])
        self.assertEqual(stats["tasks"], ["t1"])
        self.assertFalse(stats["aggregates"]["A"]["mean"]["degenerate"])

    def test_one_run_per_task_gives_an_interval_with_no_width_and_says_so(self):
        stats = rl_stats(_block({"A": [("t1", 1.0), ("t2", 5.0)],
                                 "B": [("t1", 2.0), ("t2", 6.0)]}), samples=200)
        row = stats["aggregates"]["A"]["mean"]
        self.assertTrue(row["degenerate"])
        self.assertEqual(row["width"], 0.0)
        self.assertIn("no width", row["reason"])

    def test_all_equal_scores_collapse_every_interval(self):
        stats = rl_stats(_block({"A": [("t1", 2.0)] * 4, "B": [("t1", 2.0)] * 4}), samples=200)
        for policy in ("A", "B"):
            for name in AGGREGATES:
                row = stats["aggregates"][policy][name]
                self.assertTrue(row["degenerate"], f"{policy}.{name}")
        self.assertAlmostEqual(stats["improvement"]["point"], 0.5)
        self.assertEqual(stats["aggregates"]["A"]["optimality_gap"]["point"], 0.0)

    def test_a_policy_missing_a_task_keeps_its_own_strata(self):
        stats = rl_stats(_block({"A": [("t1", 1.0), ("t1", 3.0), ("t2", 5.0), ("t2", 7.0)],
                                 "B": [("t1", 2.0), ("t1", 4.0)]}), samples=200)
        self.assertEqual(stats["aggregates"]["A"]["tasks"], 2)
        self.assertEqual(stats["aggregates"]["B"]["tasks"], 1)
        self.assertEqual(stats["improvement"]["tasks_skipped"], ["t2"])

    def test_a_single_policy_reports_its_own_aggregates_and_no_comparison(self):
        stats = rl_stats(_block({"A": [("t1", 1.0), ("t1", 3.0)]}), samples=100)
        self.assertTrue(stats["measurable"])
        self.assertAlmostEqual(stats["aggregates"]["A"]["mean"]["point"], 2.0)
        self.assertFalse(stats["improvement"]["measurable"])
        self.assertIn("two policies", stats["profile"]["reading"])

    def test_zero_samples_is_honest_rather_than_crashing(self):
        stats = rl_stats(_block({"A": [("t1", 1.0), ("t1", 3.0)],
                                 "B": [("t1", 2.0)]}), samples=0)
        row = stats["aggregates"]["A"]["iqm"]
        self.assertAlmostEqual(row["point"], 2.0)
        self.assertTrue(row["degenerate"])
        self.assertIn("no resamples", row["reason"])


# ------------------------------------------------------------------ advisory

class AdvisoryTest(unittest.TestCase):
    def test_it_names_the_runs_per_task_and_what_they_permit(self):
        matrix = _matrix({"A": [("t1", 1.0)] * 3 + [("t2", 2.0)] * 3,
                          "B": [("t1", 1.0)] * 3 + [("t2", 2.0)] * 3})
        advisory = sample_advisory(matrix)
        self.assertEqual(advisory["n_min"], 3)
        self.assertEqual(advisory["tier"], "insufficient")
        self.assertIn("3 run(s) per task", advisory["message"])
        self.assertIn("stratified bootstrap", advisory["message"])
        self.assertIn("never as 'the policies are equal'", advisory["message"])
        self.assertEqual(advisory["runs_per_task"]["A"], {"t1": 3, "t2": 3})

    def test_eight_runs_per_task_clears_the_structured_floor(self):
        matrix = _matrix({"A": [("t1", 1.0)] * 8, "B": [("t1", 2.0)] * 8})
        advisory = sample_advisory(matrix)
        self.assertEqual(advisory["tier"], "structured-ok")
        self.assertIn("stratified bootstrap", advisory["message"])

    def test_no_wide_interval_is_ever_left_unlabelled(self):
        stats = rl_stats(_block({"A": [("t1", -9.0), ("t1", 9.0), ("t1", 0.0)],
                                 "B": [("t1", -8.0), ("t1", 8.0), ("t1", 1.0)]}), samples=300)
        self.assertIn("describes the episodes recorded", stats["narrative"])
        self.assertIn("bootstrap", stats["advisory"]["message"])


# ---------------------------------------------------------------- the wiring

class AggregateWiringTest(unittest.TestCase):
    def test_rl_aggregate_attaches_the_section(self):
        agg = _aggregate(RL_TRACES)
        self.assertIn("stats", agg)
        stats = agg["stats"]
        self.assertEqual(stats["version"], VERSION)
        self.assertTrue(stats["measurable"])
        self.assertEqual(stats["metric"], "return")
        self.assertEqual(stats["bootstrap"]["samples"], BOOTSTRAP_SAMPLES)
        self.assertEqual(stats["policies"], ["policy-v1", "policy-v2"])

    def test_the_section_survives_a_json_round_trip(self):
        agg = _aggregate(RL_TRACES)
        again = json.loads(json.dumps(agg["stats"]))
        self.assertEqual(again, agg["stats"])


# --------------------------------------------------------------- the demos

class SmallDemoTest(unittest.TestCase):
    """demo/rl/traces — 12 episodes, 2 policies x 2 tasks x 3 runs."""

    @classmethod
    def setUpClass(cls):
        cls.stats = _aggregate(RL_TRACES)["stats"]

    def test_the_shape_is_the_one_the_demo_ships(self):
        self.assertEqual(self.stats["tasks"], ["rl01_ledger_reconcile", "rl02_flaky_test"])
        self.assertEqual(self.stats["n"], {"policy-v1": 6, "policy-v2": 6})
        self.assertEqual(self.stats["advisory"]["n_min"], 3)
        self.assertEqual(self.stats["advisory"]["tier"], "insufficient")

    def test_the_interquartile_means_are_the_hand_computed_ones(self):
        # policy-v1 returns sorted: −7.3 −6.5 −6.3 −5.1 4.9 5.5
        #   one cut from each end -> mean(−6.5, −6.3, −5.1, 4.9) = −3.25
        # policy-v2 returns sorted: −3.6 5.8 6.4 7.2 7.8 7.8
        #   one cut from each end -> mean(5.8, 6.4, 7.2, 7.8) = 6.8
        self.assertAlmostEqual(self.stats["aggregates"]["policy-v1"]["iqm"]["point"], -3.25)
        self.assertAlmostEqual(self.stats["aggregates"]["policy-v2"]["iqm"]["point"], 6.8)

    def test_the_shipped_intervals(self):
        self.assertEqual([self.stats["aggregates"]["policy-v1"]["iqm"]["lo"],
                          self.stats["aggregates"]["policy-v1"]["iqm"]["hi"]], [-6.7, 2.55])
        self.assertEqual([self.stats["aggregates"]["policy-v2"]["iqm"]["lo"],
                          self.stats["aggregates"]["policy-v2"]["iqm"]["hi"]], [1.8, 7.65])

    def test_the_target_is_the_best_observed_return(self):
        self.assertEqual(self.stats["target"]["value"], 7.8)
        self.assertFalse(self.stats["target"]["explicit"])
        self.assertIn("best score observed", self.stats["target"]["source"])

    def test_the_probability_of_improvement(self):
        imp = self.stats["improvement"]
        self.assertEqual(imp["a"], "policy-v1")
        self.assertEqual(imp["b"], "policy-v2")
        self.assertEqual(imp["point"], 0.9445)
        self.assertEqual([imp["lo"], imp["hi"]], [0.7778, 1.0])
        self.assertEqual(imp["per_task"], {"rl01_ledger_reconcile": 1.0,
                                           "rl02_flaky_test": 0.8889})

    def test_at_three_runs_the_intervals_overlap_and_the_page_must_say_so(self):
        self.assertIn("do not separate the policies", self.stats["narrative"])

    def test_the_profiles_do_not_cross(self):
        self.assertEqual(self.stats["profile"]["crossings"], [])
        self.assertEqual(self.stats["profile"]["dominant"], "policy-v2")


class TrainDemoTest(unittest.TestCase):
    """demo/rl/train — 96 episodes, 2 policies x 6 tasks x 8 runs, with one
    task on which the stronger policy is a genuine regression."""

    @classmethod
    def setUpClass(cls):
        if not RL_TRAIN.is_dir():
            raise unittest.SkipTest("demo/rl/train is not present")
        cls.stats = _aggregate(RL_TRAIN)["stats"]

    def test_the_shape_is_the_one_the_demo_ships(self):
        self.assertEqual(len(self.stats["tasks"]), 6)
        self.assertEqual(self.stats["n"], {"policy-v1": 48, "policy-v2": 48})
        self.assertEqual(self.stats["advisory"]["n_min"], 8)
        self.assertEqual(self.stats["advisory"]["tier"], "structured-ok")

    def test_the_shipped_interquartile_means_and_intervals(self):
        v1 = self.stats["aggregates"]["policy-v1"]["iqm"]
        v2 = self.stats["aggregates"]["policy-v2"]["iqm"]
        self.assertEqual([v1["point"], v1["lo"], v1["hi"]], [-6.2042, -6.8, -4.6])
        self.assertEqual([v2["point"], v2["lo"], v2["hi"]], [6.0958, 4.1875, 6.7375])

    def test_the_interquartile_mean_is_the_middle_half_of_the_recorded_runs(self):
        scores = sorted(v for task in self.stats["by_task"].values()
                        for v in task.get("policy-v2", []))
        self.assertEqual(len(scores), 48)
        cut = 48 // 4
        self.assertAlmostEqual(self.stats["aggregates"]["policy-v2"]["iqm"]["point"],
                               sum(scores[cut:-cut]) / (48 - 2 * cut), places=4)

    def test_at_eight_runs_the_intervals_finally_separate(self):
        self.assertIn("do not overlap", self.stats["narrative"])

    def test_the_probability_of_improvement_and_the_task_it_hides(self):
        imp = self.stats["improvement"]
        self.assertEqual(imp["point"], 0.8802)
        self.assertEqual([imp["lo"], imp["hi"]], [0.8229, 0.9401])
        self.assertEqual(imp["tasks_used"], 6)
        # the aggregate says v2 wins; on one task it is the worse policy, and
        # the average must not be allowed to bury that
        self.assertEqual(imp["regressions"], ["rl05_incident_postmortem"])
        self.assertLess(imp["per_task"]["rl05_incident_postmortem"], 0.5)
        self.assertIn("rl05_incident_postmortem", imp["reading"])
        self.assertIn("rl05_incident_postmortem", self.stats["narrative"])

    def test_the_profiles_do_not_cross_on_this_set(self):
        profile = self.stats["profile"]
        self.assertEqual(profile["crossings"], [])
        self.assertEqual(profile["dominant"], "policy-v2")
        self.assertIn("every τ", profile["reading"])

    def test_the_profile_band_is_a_real_band_at_the_low_end(self):
        rows = self.stats["profile"]["curves"]["policy-v2"]
        self.assertTrue(any(row[3] - row[2] > 0 for row in rows))
        self.assertAlmostEqual(rows[0][1], 1.0)

    def test_the_same_aggregate_twice_gives_the_same_bytes(self):
        again = _aggregate(RL_TRAIN)["stats"]
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(self.stats, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
