"""Tests for reference profiles and cohort comparison (v18).

Both features exist to remove a restriction — needing a partner run, and
needing to compare individuals — so the tests pin that the freedom is real
and that neither feature overclaims when the evidence is thin.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.cohort import GROUPERS, compare_cohorts, group_runs
from deepcompare.profile import (
    THIN_EVIDENCE,
    build_profile,
    profile_suite,
    score_run,
)


def step(index, stype, name, tokens=100):
    return {"index": index, "type": stype, "name": name,
            "input": f"{name} input text", "output": f"{name} output text",
            "tokens": tokens, "latency_s": 1.0, "quality": None, "note": None}


def traj(agent, task, success, steps, model="m", cost=0.02, version="v1"):
    return Trajectory.from_json({
        "schema_version": 1, "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": model, "version": version},
        "task": {"id": task, "prompt": "p", "expected": "gold"},
        "outcome": {"success": success, "answer": "answer",
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 100, "output_tokens": 100,
                   "cost_usd": cost, "latency_s": float(len(steps))},
        "steps": steps,
    })


CANONICAL = [step(0, "plan", "plan"), step(1, "search", "web_search"),
             step(2, "retrieve", "select_result"), step(3, "answer", "final")]


def normal_runs(n=8, task="t1"):
    return [traj(f"a{i}", task, True, CANONICAL) for i in range(n)]


class TestProfileBuilding(unittest.TestCase):
    def test_canonical_path_is_a_real_recorded_path(self):
        profile = build_profile(normal_runs())
        # A medoid, never an invented average.
        self.assertEqual(profile["canonical_path"],
                         [s["type"] for s in CANONICAL])

    def test_failures_are_excluded_by_default(self):
        runs = normal_runs(6) + [
            traj("bad", "t1", False, [step(0, "answer", "final")])
        ]
        profile = build_profile(runs)
        self.assertEqual(profile["runs_used"], 6)
        self.assertEqual(profile["runs_excluded"], 1)
        self.assertTrue(profile["successes_only"])

    def test_failures_can_be_included_deliberately(self):
        runs = normal_runs(3) + [
            traj("bad", "t1", False, [step(0, "answer", "final")])
        ]
        profile = build_profile(runs, successes_only=False)
        self.assertEqual(profile["runs_used"], 4)

    def test_expected_types_require_near_universal_presence(self):
        runs = normal_runs(9)
        runs.append(traj("odd", "t1", True, [
            step(0, "plan", "plan"), step(1, "tool_call", "calc"),
            step(2, "answer", "final"),
        ]))
        profile = build_profile(runs)
        self.assertIn("search", profile["expected_step_types"])
        self.assertNotIn("tool_call", profile["expected_step_types"])

    def test_bands_describe_the_middle_half(self):
        runs = [traj(f"a{i}", "t1", True,
                     [step(0, "plan", "plan", tokens=100 * (i + 1)),
                      step(1, "answer", "final")])
                for i in range(8)]
        band = build_profile(runs)["bands"]["tokens"]
        self.assertLessEqual(band["low"], band["median"])
        self.assertLessEqual(band["median"], band["high"])
        self.assertLessEqual(band["min"], band["low"])
        self.assertGreaterEqual(band["max"], band["high"])

    def test_thin_evidence_is_declared(self):
        profile = build_profile(normal_runs(2))
        self.assertTrue(profile["thin_evidence"])
        self.assertIn("thin evidence", profile["caveat"])
        thick = build_profile(normal_runs(THIN_EVIDENCE + 2))
        self.assertFalse(thick["thin_evidence"])

    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            build_profile([])

    def test_all_failures_raises_with_a_usable_message(self):
        runs = [traj("a", "t1", False, CANONICAL)]
        with self.assertRaisesRegex(ValueError, "successes_only=False"):
            build_profile(runs)

    def test_deterministic(self):
        runs = normal_runs()
        self.assertEqual(build_profile(runs), build_profile(runs))


class TestScoringWithoutAPartner(unittest.TestCase):
    def test_a_conforming_run_scores_on_profile(self):
        profile = build_profile(normal_runs())
        result = score_run(traj("new", "t1", True, CANONICAL), profile)
        self.assertEqual(result["verdict"], "on-profile")
        self.assertEqual(result["path_similarity"], 1.0)
        self.assertEqual(result["missing_step_types"], [])

    def test_a_run_skipping_an_expected_step_is_off_profile(self):
        profile = build_profile(normal_runs())
        short = traj("new", "t1", True, [
            step(0, "plan", "plan"), step(1, "answer", "final"),
        ])
        result = score_run(short, profile)
        self.assertEqual(result["verdict"], "off-profile")
        self.assertIn("search", result["missing_step_types"])
        self.assertIn("skipped", result["narrative"])

    def test_an_expensive_but_conforming_run_is_costly_not_broken(self):
        profile = build_profile(normal_runs())
        heavy = traj("new", "t1", True,
                     [step(0, "plan", "plan", tokens=5000),
                      step(1, "search", "web_search", tokens=5000),
                      step(2, "retrieve", "select_result", tokens=5000),
                      step(3, "answer", "final", tokens=5000)])
        result = score_run(heavy, profile)
        self.assertEqual(result["verdict"], "costly")
        self.assertIn("tokens", result["outside_band"])

    def test_a_failed_run_is_reported_as_failed_first(self):
        profile = build_profile(normal_runs())
        result = score_run(traj("new", "t1", False, CANONICAL), profile)
        self.assertEqual(result["verdict"], "failed")

    def test_unexpected_step_types_are_named(self):
        profile = build_profile(normal_runs())
        odd = traj("new", "t1", True, [
            step(0, "plan", "plan"), step(1, "search", "web_search"),
            step(2, "retrieve", "select_result"),
            step(3, "tool_call", "calculator"), step(4, "answer", "final"),
        ])
        result = score_run(odd, profile)
        self.assertIn("tool_call", result["unexpected_step_types"])

    def test_thin_profile_caveat_reaches_the_score(self):
        profile = build_profile(normal_runs(2))
        result = score_run(traj("new", "t1", True, CANONICAL), profile)
        self.assertIn("indicative", result["narrative"])

    def test_suite_reports_unprofiled_runs_rather_than_dropping_them(self):
        profiles = {"t1": build_profile(normal_runs())}
        runs = [traj("x", "t1", True, CANONICAL),
                traj("y", "t9", True, CANONICAL)]
        suite = profile_suite(profiles, runs)
        self.assertEqual(len(suite["scored"]), 1)
        self.assertEqual(suite["unprofiled"], ["t9/y"])


class TestCohorts(unittest.TestCase):
    def mixed(self):
        runs = []
        for i in range(6):
            runs.append(traj(f"fast{i}", f"t{i}", i > 0, CANONICAL,
                             model="cheap-model", cost=0.01))
            runs.append(traj(f"slow{i}", f"t{i}", True, CANONICAL,
                             model="dear-model", cost=0.09))
        return runs

    def test_grouping_by_each_known_key(self):
        runs = self.mixed()
        for key in GROUPERS:
            cohorts = group_runs(runs, by=key)
            self.assertTrue(cohorts)
            self.assertEqual(sum(len(v) for v in cohorts.values()), len(runs))

    def test_unknown_grouping_names_the_known_ones(self):
        with self.assertRaisesRegex(ValueError, "model"):
            group_runs(self.mixed(), by="nonsense")

    def test_custom_grouping_function(self):
        cohorts = group_runs(self.mixed(), key=lambda t: t.task.id[:2])
        self.assertIn("t0", cohorts)

    def test_cohort_summary_carries_intervals(self):
        result = compare_cohorts(group_runs(self.mixed(), by="model"))
        for summary in result["cohorts"]:
            self.assertEqual(len(summary["success_ci"]), 2)
            self.assertGreaterEqual(summary["success_rate"], 0.0)
            self.assertLessEqual(summary["success_rate"], 1.0)

    def test_small_difference_is_not_called_a_win(self):
        result = compare_cohorts(group_runs(self.mixed(), by="model"))
        pair = result["pairs"][0]
        self.assertFalse(pair["success_difference"]["significant"])
        self.assertIn("indistinguishable", pair["verdict"])
        # ... and the cheaper cohort is still recommended on cost.
        self.assertIn("cheap-model", pair["verdict"])

    def test_clear_difference_is_called_with_its_interval(self):
        runs = []
        for i in range(12):
            runs.append(traj(f"good{i}", f"t{i}", True, CANONICAL, model="good"))
            runs.append(traj(f"bad{i}", f"t{i}", False, CANONICAL, model="bad"))
        result = compare_cohorts(group_runs(runs, by="model"))
        pair = result["pairs"][0]
        self.assertTrue(pair["success_difference"]["significant"])
        self.assertIn("beats", pair["verdict"])

    def test_cohorts_with_no_shared_task_are_not_compared_on_success(self):
        runs = [traj("a", "t1", True, CANONICAL, model="x"),
                traj("b", "t2", False, CANONICAL, model="y")]
        result = compare_cohorts(group_runs(runs, by="model"))
        pair = result["pairs"][0]
        self.assertFalse(pair["comparable"])
        self.assertIn("not comparable", pair["verdict"])

    def test_single_cohort_says_so(self):
        runs = [traj("a", "t1", True, CANONICAL, model="only")]
        result = compare_cohorts(group_runs(runs, by="model"))
        self.assertEqual(result["pairs"], [])
        self.assertIn("at least two", result["narrative"])

    def test_deterministic(self):
        cohorts = group_runs(self.mixed(), by="model")
        self.assertEqual(compare_cohorts(cohorts), compare_cohorts(cohorts))


class TestAgainstRealFleet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "fleet" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo fleet traces not present")
        cls.trajs = [Trajectory.from_json(p) for p in sorted(traces.glob("*.json"))]

    def test_profiles_build_per_task_and_score_the_whole_fleet(self):
        by_task: dict[str, list] = {}
        for t in self.trajs:
            by_task.setdefault(t.task.id, []).append(t)
        profiles = {tid: build_profile(runs, name=tid)
                    for tid, runs in by_task.items()}
        suite = profile_suite(profiles, self.trajs)
        self.assertEqual(len(suite["scored"]), len(self.trajs))
        self.assertEqual(suite["unprofiled"], [])
        # Every failing run must be reported as failed, never as on-profile.
        failed = {f"{t.task.id}/{t.agent.name}"
                  for t in self.trajs if not t.outcome.success}
        reported = {f"{r['task']}/{r['agent']}"
                    for r in suite["scored"] if r["verdict"] == "failed"}
        self.assertEqual(failed, reported)

    def test_model_cohorts_report_intervals_for_every_pair(self):
        result = compare_cohorts(group_runs(self.trajs, by="model"))
        self.assertGreater(len(result["cohorts"]), 2)
        for pair in result["pairs"]:
            self.assertIn("success_difference", pair)
            self.assertIn("significant", pair["success_difference"])

    def test_most_model_pairs_are_within_noise_on_this_corpus(self):
        # The honest headline: 8 tasks cannot separate most models.
        result = compare_cohorts(group_runs(self.trajs, by="model"))
        decided = [p for p in result["pairs"]
                   if p["success_difference"]["significant"]]
        self.assertLess(len(decided), len(result["pairs"]))


if __name__ == "__main__":
    unittest.main()
