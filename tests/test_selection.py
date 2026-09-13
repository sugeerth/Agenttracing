"""Tests for behavioral similarity and agent selection (SCHEMA.md v10).

The properties asserted here are the ones the product's advice rests on: if
"these two agents are interchangeable" or "retire this agent" is wrong, the
user makes a real procurement decision on a false premise.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.routing import routing_analysis
from deepcompare.similarity import (
    Profile,
    cosine,
    facet_similarity,
    find_complementarities,
    find_redundancies,
    lcs_ratio,
    similarity_analysis,
    similarity_matrix,
)


def make_traj(agent, task, success, steps, cost=0.01, latency=5.0,
              step_tokens=100):
    step_dicts = []
    for i, (step_type, name) in enumerate(steps):
        step_dicts.append({
            "index": i, "type": step_type, "name": name,
            "input": f"{name} input", "output": f"{name} output",
            "tokens": step_tokens, "latency_s": 1.0, "quality": None, "note": None,
        })
    total = step_tokens * len(steps)
    return Trajectory.from_json({
        "schema_version": 1,
        "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "e"},
        "outcome": {"success": success, "answer": "a", "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": total // 2, "output_tokens": total - total // 2,
                   "cost_usd": cost, "latency_s": latency},
        "steps": step_dicts,
    })


CANON = [("plan", "plan"), ("search", "web_search"), ("read", "open_page"),
         ("answer", "final")]


class TestPrimitives(unittest.TestCase):
    def test_lcs_ratio_bounds(self):
        self.assertEqual(lcs_ratio((), ()), 1.0)
        self.assertEqual(lcs_ratio(("a",), ()), 0.0)
        self.assertEqual(lcs_ratio(("a", "b"), ("a", "b")), 1.0)
        mid = lcs_ratio(("a", "b", "c"), ("a", "x", "c"))
        self.assertGreater(mid, 0.0)
        self.assertLess(mid, 1.0)

    def test_lcs_ratio_is_symmetric(self):
        a, b = ("plan", "search", "read", "answer"), ("plan", "read", "answer")
        self.assertEqual(lcs_ratio(a, b), lcs_ratio(b, a))

    def test_cosine_bounds(self):
        self.assertEqual(cosine({}, {}), 1.0)
        self.assertEqual(cosine({"a": 1}, {}), 0.0)
        self.assertEqual(cosine({"a": 1}, {"a": 5}), 1.0)  # direction, not scale
        self.assertEqual(cosine({"a": 1}, {"b": 1}), 0.0)


class TestFacets(unittest.TestCase):
    def profiles(self, a_trajs, b_trajs):
        return Profile("a1", a_trajs), Profile("b1", b_trajs)

    def test_identical_agents_score_one_everywhere(self):
        a = [make_traj("a1", "t1", True, CANON), make_traj("a1", "t2", True, CANON)]
        b = [make_traj("b1", "t1", True, CANON), make_traj("b1", "t2", True, CANON)]
        facets = facet_similarity(*self.profiles(a, b))
        for key in ("outcome", "process", "tools", "resources"):
            self.assertEqual(facets[key], 1.0, key)
        self.assertEqual(facets["shared_tasks"], 2)

    def test_same_outcomes_different_process_is_the_interesting_case(self):
        # The facet split exists for exactly this: agreeing on every answer
        # while working completely differently.
        a = [make_traj("a1", "t1", True, CANON)]
        detour = [("plan", "plan"), ("search", "web_search"),
                  ("search", "web_search"), ("search", "web_search"),
                  ("tool_call", "calculator"), ("answer", "final")]
        b = [make_traj("b1", "t1", True, detour)]
        facets = facet_similarity(*self.profiles(a, b))
        self.assertEqual(facets["outcome"], 1.0)
        self.assertLess(facets["process"], 1.0)
        self.assertLess(facets["tools"], 1.0)

    def test_opposite_outcomes_score_zero_outcome(self):
        a = [make_traj("a1", "t1", True, CANON)]
        b = [make_traj("b1", "t1", False, CANON)]
        facets = facet_similarity(*self.profiles(a, b))
        self.assertEqual(facets["outcome"], 0.0)
        self.assertEqual(facets["process"], 1.0)  # same path, different result

    def test_no_shared_tasks_scores_zero(self):
        a = [make_traj("a1", "t1", True, CANON)]
        b = [make_traj("b1", "t9", True, CANON)]
        facets = facet_similarity(*self.profiles(a, b))
        self.assertEqual(facets["shared_tasks"], 0)
        self.assertEqual(facets["outcome"], 0.0)

    def test_resources_use_ratio_not_absolute_difference(self):
        a = [make_traj("a1", "t1", True, CANON, step_tokens=100, latency=1.0)]
        b = [make_traj("b1", "t1", True, CANON, step_tokens=200, latency=2.0)]
        facets = facet_similarity(*self.profiles(a, b))
        # Tokens and latency are both 2x apart -> 0.5 each; step counts match
        # -> 1.0.  The mean of (0.5, 0.5, 1.0) is 2/3.
        self.assertAlmostEqual(facets["resources"], 2 / 3, places=2)

    def test_resource_similarity_is_scale_free(self):
        # Doubling both agents' spend must not change how similar they are.
        small = facet_similarity(
            Profile("a1", [make_traj("a1", "t1", True, CANON, step_tokens=100)]),
            Profile("b1", [make_traj("b1", "t1", True, CANON, step_tokens=200)]),
        )
        large = facet_similarity(
            Profile("a1", [make_traj("a1", "t1", True, CANON, step_tokens=1000)]),
            Profile("b1", [make_traj("b1", "t1", True, CANON, step_tokens=2000)]),
        )
        self.assertEqual(small["resources"], large["resources"])


class TestSimilarityAnalysis(unittest.TestCase):
    def fleet(self):
        """cheap and dear behave identically; odd fails what they solve and
        solves what they miss."""
        tasks = ["t1", "t2"]
        fleet = {
            "cheap": [make_traj("cheap", t, True, CANON, cost=0.01) for t in tasks],
            "dear": [make_traj("dear", t, True, CANON, cost=0.05) for t in tasks],
            "odd": [
                make_traj("odd", "t1", False,
                          [("plan", "plan"), ("tool_call", "calc"), ("answer", "f")],
                          cost=0.02),
                make_traj("odd", "t2", True,
                          [("plan", "plan"), ("tool_call", "calc"), ("answer", "f")],
                          cost=0.02),
            ],
        }
        return fleet

    def test_matrix_is_deterministic_and_sorted(self):
        result_a = similarity_matrix(
            [Profile(n, t) for n, t in sorted(self.fleet().items())]
        )
        result_b = similarity_matrix(
            [Profile(n, t) for n, t in sorted(self.fleet().items())]
        )
        self.assertEqual(result_a, result_b)
        composites = [row["composite"] for row in result_a]
        self.assertEqual(composites, sorted(composites, reverse=True))

    def test_identical_pair_ranks_first(self):
        analysis = similarity_analysis(self.fleet())
        top = analysis["pairs"][0]
        self.assertEqual({top["a"], top["b"]}, {"cheap", "dear"})
        self.assertEqual(top["facets"]["outcome"], 1.0)

    def test_redundancy_names_the_cheaper_keeper(self):
        analysis = similarity_analysis(self.fleet())
        self.assertTrue(analysis["redundancies"])
        row = analysis["redundancies"][0]
        self.assertEqual(row["drop"], "dear")
        self.assertEqual(row["keep"], "cheap")
        self.assertGreater(row["saving_per_task_usd"], 0)

    def test_redundancy_is_one_row_per_dropped_agent(self):
        fleet = self.fleet()
        fleet["cheap2"] = [make_traj("cheap2", t, True, CANON, cost=0.011)
                           for t in ("t1", "t2")]
        analysis = similarity_analysis(fleet)
        dropped = [row["drop"] for row in analysis["redundancies"]]
        self.assertEqual(len(dropped), len(set(dropped)))
        dear_row = next(r for r in analysis["redundancies"] if r["drop"] == "dear")
        self.assertIn("also_dominated_by", dear_row)

    def test_agents_that_disagree_are_not_redundant(self):
        analysis = similarity_analysis(self.fleet())
        for row in analysis["redundancies"]:
            self.assertNotIn("odd", (row["drop"], row["keep"]))

    def test_complementarity_found_between_disjoint_failures(self):
        fleet = {
            "x": [make_traj("x", "t1", True, CANON), make_traj("x", "t2", False, CANON)],
            "y": [make_traj("y", "t1", False, CANON), make_traj("y", "t2", True, CANON)],
        }
        analysis = similarity_analysis(fleet)
        self.assertTrue(analysis["complementarities"])
        row = analysis["complementarities"][0]
        self.assertEqual(row["union_coverage"], 1.0)
        self.assertEqual(row["best_alone_coverage"], 0.5)
        self.assertEqual(row["gain_tasks"], 1)

    def test_clusters_group_alike_agents_only(self):
        analysis = similarity_analysis(self.fleet())
        grouped = [c for c in analysis["clusters"] if c["size"] > 1]
        self.assertTrue(grouped)
        members = grouped[0]["members"]
        self.assertEqual(members, ["cheap", "dear"])
        self.assertEqual(grouped[0]["cheapest"], "cheap")

    def test_narrative_mentions_real_findings(self):
        analysis = similarity_analysis(self.fleet())
        self.assertIn("redundant", analysis["narrative"])


class TestRouting(unittest.TestCase):
    def complementary_fleet(self):
        return {
            "x": [make_traj("x", "t1", True, CANON, cost=0.01),
                  make_traj("x", "t2", False, CANON, cost=0.01)],
            "y": [make_traj("y", "t1", False, CANON, cost=0.03),
                  make_traj("y", "t2", True, CANON, cost=0.03)],
        }

    def test_headroom_when_agents_are_complementary(self):
        routing = routing_analysis(self.complementary_fleet())
        self.assertEqual(routing["best_single"]["coverage"], 0.5)
        self.assertEqual(routing["oracle"]["coverage"], 1.0)
        self.assertEqual(routing["oracle"]["coverage_headroom"], 0.5)

    def test_no_headroom_when_one_agent_dominates(self):
        fleet = {
            "x": [make_traj("x", t, True, CANON, cost=0.05) for t in ("t1", "t2")],
            "y": [make_traj("y", t, False, CANON, cost=0.01) for t in ("t1", "t2")],
        }
        routing = routing_analysis(fleet)
        self.assertEqual(routing["best_single"]["agent"], "x")
        self.assertEqual(routing["oracle"]["coverage_headroom"], 0.0)

    def test_cheapest_solver_per_task(self):
        fleet = {
            "cheap": [make_traj("cheap", "t1", True, CANON, cost=0.01)],
            "dear": [make_traj("dear", "t1", True, CANON, cost=0.09)],
        }
        routing = routing_analysis(fleet)
        row = routing["per_task"][0]
        self.assertEqual(row["cheapest_solver"], "cheap")
        self.assertEqual(row["solver_count"], 2)

    def test_portfolio_reaches_ceiling_and_is_labelled(self):
        routing = routing_analysis(self.complementary_fleet())
        two = next(p for p in routing["portfolios"] if p["k"] == 2)
        self.assertEqual(two["coverage"], 1.0)
        self.assertEqual(two["members"], ["x", "y"])
        self.assertIn(two["search"], ("exact", "greedy"))

    def test_unique_solves_attributed_correctly(self):
        routing = routing_analysis(self.complementary_fleet())
        self.assertEqual(routing["unique_solves"], {"x": ["t1"], "y": ["t2"]})

    def test_oracle_is_labelled_as_a_ceiling(self):
        routing = routing_analysis(self.complementary_fleet())
        self.assertIn("ceiling", routing["oracle"]["note"])
        self.assertIn("ceiling", routing["narrative"])

    def test_empty_fleet_does_not_crash(self):
        routing = routing_analysis({})
        self.assertEqual(routing["agents"], 0)
        self.assertIsNone(routing["best_single"])

    def test_deterministic_across_runs(self):
        first = routing_analysis(self.complementary_fleet())
        second = routing_analysis(self.complementary_fleet())
        self.assertEqual(first, second)


class TestAgainstRealFleet(unittest.TestCase):
    """Guards on the shipped demo fleet: the advice must stay coherent."""

    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "fleet" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo fleet traces not present")
        by_agent: dict[str, list] = {}
        for path in sorted(traces.glob("*.json")):
            traj = Trajectory.from_json(path)
            by_agent.setdefault(traj.agent.name, []).append(traj)
        cls.fleet = by_agent

    def test_every_dropped_agent_is_matched_on_outcomes(self):
        analysis = similarity_analysis(self.fleet)
        outcomes = {
            name: {t.task.id: t.outcome.success for t in trajs}
            for name, trajs in self.fleet.items()
        }
        for row in analysis["redundancies"]:
            self.assertEqual(
                outcomes[row["drop"]], outcomes[row["keep"]],
                f"{row['drop']} dropped for {row['keep']} despite differing outcomes",
            )

    def test_portfolio_coverage_never_exceeds_oracle(self):
        routing = routing_analysis(self.fleet)
        ceiling = routing["oracle"]["coverage"]
        for portfolio in routing["portfolios"]:
            self.assertLessEqual(portfolio["coverage"], ceiling + 1e-9)

    def test_portfolio_coverage_is_monotonic_in_k(self):
        routing = routing_analysis(self.fleet)
        coverages = [p["coverage"] for p in sorted(routing["portfolios"],
                                                   key=lambda p: p["k"])]
        for earlier, later in zip(coverages, coverages[1:]):
            self.assertLessEqual(earlier, later + 1e-9)

    def test_best_single_agent_actually_solves_its_claimed_tasks(self):
        routing = routing_analysis(self.fleet)
        best = routing["best_single"]
        solved = {t.task.id for t in self.fleet[best["agent"]] if t.outcome.success}
        self.assertEqual(set(best["covered_tasks"]), solved)


if __name__ == "__main__":
    unittest.main()
