"""Tests for Shapley credit assignment and attribute-based analysis (v15).

The Shapley tests pin the cooperative-game axioms — efficiency, symmetry and
the null player — because those are the whole reason to prefer this over an
ad-hoc split of the cost. The attribute tests pin the honesty properties:
associations must never be phrased as causes, and tiny groups must not
produce confident findings.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.attributes import (
    ATTRIBUTES,
    attribute_analysis,
    attribute_profiles,
)
from deepcompare.shapley import shapley_attribution


def step(index, stype, name, text="text", tokens=100, quality=None,
         confidence=None):
    payload = {"index": index, "type": stype, "name": name,
               "input": f"{name} {text}", "output": f"{name} out {text}",
               "tokens": tokens, "latency_s": 1.0, "quality": quality,
               "note": None}
    if confidence is not None:
        payload["model"] = {"confidence": confidence}
    return payload


def traj(agent, task, success, steps, cost=0.02):
    return Trajectory.from_json({
        "schema_version": 1, "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "p", "expected": "gold"},
        "outcome": {"success": success, "answer": "answer",
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 200, "output_tokens": 200,
                   "cost_usd": cost, "latency_s": float(len(steps))},
        "steps": steps,
    })


REFERENCE = [
    step(0, "plan", "plan"),
    step(1, "search", "web_search"),
    step(2, "retrieve", "select_result", "official filing"),
    step(3, "answer", "final"),
]


class TestShapleyAxioms(unittest.TestCase):
    def two_divergence_case(self):
        """A run that goes wrong twice, each costing extra tokens."""
        a = traj("good", "t1", True, REFERENCE)
        b = traj("weak", "t1", False, [
            step(0, "plan", "plan"),
            step(1, "search", "web_search", "different query", tokens=300),
            step(2, "retrieve", "select_result", "random blog", tokens=400,
                 quality="bad"),
            step(3, "answer", "final"),
        ])
        return compare(a, b), a, b

    def test_efficiency_allocations_sum_to_the_whole(self):
        # The defining Shapley property: the parts add up exactly.
        report, a, b = self.two_divergence_case()
        result = shapley_attribution(report, a, b)
        self.assertTrue(result["available"])
        total = sum(row["shapley"] for row in result["allocations"])
        self.assertAlmostEqual(total, result["total_saving"], places=3)
        self.assertAlmostEqual(result["efficiency_check"], 0.0, places=5)

    def test_shares_sum_to_one_when_there_is_a_gap(self):
        report, a, b = self.two_divergence_case()
        result = shapley_attribution(report, a, b)
        if abs(result["total_saving"]) > 1e-9:
            self.assertAlmostEqual(
                sum(r["share"] for r in result["allocations"]), 1.0, places=2)

    def test_null_player_gets_zero(self):
        # A divergence that changes nothing about cost must receive no blame.
        a = traj("good", "t1", True, REFERENCE)
        b = traj("same_cost", "t1", True, [
            step(0, "plan", "plan"),
            step(1, "search", "web_search", "reworded query"),  # same tokens
            step(2, "retrieve", "select_result", "official filing"),
            step(3, "answer", "final"),
        ])
        result = shapley_attribution(compare(a, b), a, b)
        if result and result.get("available"):
            for row in result["allocations"]:
                self.assertAlmostEqual(row["shapley"], 0.0, places=3)

    def test_no_divergences_returns_none(self):
        a = traj("x", "t1", True, REFERENCE)
        b = traj("y", "t1", True, REFERENCE)
        self.assertIsNone(shapley_attribution(compare(a, b), a, b))

    def test_blame_falls_on_the_failing_side(self):
        report, a, b = self.two_divergence_case()
        result = shapley_attribution(report, a, b)
        self.assertEqual(result["loser"], "weak")
        self.assertEqual(result["winner"], "good")

    def test_metric_choice_changes_the_units_not_the_structure(self):
        report, a, b = self.two_divergence_case()
        tokens = shapley_attribution(report, a, b, metric="tokens")
        steps = shapley_attribution(report, a, b, metric="steps")
        self.assertEqual(len(tokens["allocations"]), len(steps["allocations"]))
        self.assertEqual(tokens["metric"], "tokens")
        self.assertEqual(steps["metric"], "steps")

    def test_deterministic(self):
        report, a, b = self.two_divergence_case()
        self.assertEqual(shapley_attribution(report, a, b),
                         shapley_attribution(report, a, b))

    def test_outcome_credit_withheld_when_several_causal(self):
        report, a, b = self.two_divergence_case()
        result = shapley_attribution(report, a, b)
        causal = [d for d in report["divergences"]
                  if (d.get("downstream") or {}).get("caused_failure")]
        if len(causal) != 1:
            self.assertFalse(result["outcome_attributable"])
            self.assertIn("cannot perform", result["outcome_note"])

    def test_single_causal_divergence_is_attributable(self):
        a = traj("good", "t1", True, REFERENCE)
        b = traj("weak", "t1", False, [
            step(0, "plan", "plan"),
            step(1, "search", "web_search"),
            step(2, "retrieve", "select_result", "random blog", quality="bad"),
            step(3, "answer", "final"),
        ])
        result = shapley_attribution(compare(a, b), a, b)
        self.assertTrue(result["outcome_attributable"])
        self.assertIn("attributable", result["outcome_note"])


class TestAttributeAnalysis(unittest.TestCase):
    def corpus(self):
        """Runs where a poor-quality step tracks failure perfectly."""
        runs = []
        for i in range(4):
            runs.append(traj("agent", f"good{i}", True, REFERENCE))
        for i in range(4):
            runs.append(traj("agent", f"bad{i}", False, [
                step(0, "plan", "plan"),
                step(1, "search", "web_search"),
                step(2, "retrieve", "select_result", "blog", quality="bad"),
                step(3, "answer", "final"),
            ]))
        return runs

    def test_perfect_separator_is_found_and_notable(self):
        result = attribute_analysis(self.corpus())
        row = next(r for r in result["attributes"]
                   if r["attribute"] == "poor_quality_step")
        self.assertEqual(row["with"]["failure_rate"], 1.0)
        self.assertEqual(row["without"]["failure_rate"], 0.0)
        self.assertEqual(row["lift"], 1.0)
        self.assertTrue(row["notable"])

    def test_findings_are_labelled_associations_not_causes(self):
        result = attribute_analysis(self.corpus())
        self.assertIn("association", result["narrative"].lower())
        self.assertIn("association", result["caveat"].lower())
        for word in ("causes the failure", "because of"):
            self.assertNotIn(word, result["narrative"])

    def test_tiny_groups_are_not_called_notable(self):
        # One run on one side cannot support a finding.
        runs = [traj("a", "t1", False, [
            step(0, "plan", "plan"),
            step(1, "retrieve", "r", quality="bad"),
            step(2, "answer", "final"),
        ])] + [traj("a", f"t{i}", True, REFERENCE) for i in range(2, 5)]
        result = attribute_analysis(runs)
        row = next(r for r in result["attributes"]
                   if r["attribute"] == "poor_quality_step")
        self.assertFalse(row["notable"])
        self.assertFalse(row["measurable"])

    def test_unmeasurable_attribute_excludes_runs(self):
        # Confidence is only defined for runs carrying telemetry.
        runs = [
            traj("a", "t1", True, [step(0, "plan", "p", confidence=0.9),
                                   step(1, "answer", "f", confidence=0.9)]),
            traj("a", "t2", False, [step(0, "plan", "p", confidence=0.4),
                                    step(1, "answer", "f", confidence=0.5)]),
            traj("a", "t3", True, REFERENCE),  # no telemetry at all
        ]
        result = attribute_analysis(runs)
        row = next(r for r in result["attributes"]
                   if r["attribute"] == "low_confidence_step")
        self.assertEqual(row["with"]["runs"] + row["without"]["runs"], 2)

    def test_intervals_reported_for_measurable_attributes(self):
        result = attribute_analysis(self.corpus())
        row = next(r for r in result["attributes"]
                   if r["attribute"] == "poor_quality_step")
        self.assertIsNotNone(row["interval"])
        self.assertEqual(len(row["with"]["ci"]), 2)

    def test_empty_corpus_is_handled(self):
        result = attribute_analysis([])
        self.assertEqual(result["runs"], 0)
        self.assertEqual(result["attributes"], [])

    def test_no_separation_says_so_plainly(self):
        runs = [traj("a", f"t{i}", i % 2 == 0, REFERENCE) for i in range(6)]
        result = attribute_analysis(runs)
        self.assertEqual(result["notable"], 0)
        self.assertIn("No behavioural attribute", result["narrative"])

    def test_deterministic(self):
        corpus = self.corpus()
        self.assertEqual(attribute_analysis(corpus), attribute_analysis(corpus))

    def test_every_attribute_has_human_phrasing(self):
        for name, (_, phrasing) in ATTRIBUTES.items():
            self.assertTrue(phrasing.strip(), name)
            self.assertNotIn("_", phrasing, name)

    def test_profiles_cover_corpus_and_each_agent(self):
        by_agent = {
            "x": [traj("x", "t1", True, REFERENCE)],
            "y": [traj("y", "t1", False, REFERENCE)],
        }
        profiles = attribute_profiles(by_agent)
        self.assertEqual(sorted(profiles["agents"]), ["x", "y"])
        self.assertEqual(profiles["corpus"]["runs"], 2)


class TestAgainstRealFleet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "fleet" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo fleet traces not present")
        cls.trajs = [Trajectory.from_json(p) for p in sorted(traces.glob("*.json"))]

    def test_quality_annotation_tracks_failure_in_the_corpus(self):
        result = attribute_analysis(self.trajs)
        row = next(r for r in result["attributes"]
                   if r["attribute"] == "poor_quality_step")
        self.assertGreater(row["lift"], 0.0)
        self.assertTrue(row["notable"])

    def test_every_reported_rate_is_a_valid_proportion(self):
        result = attribute_analysis(self.trajs)
        for row in result["attributes"]:
            for side in ("with", "without"):
                rate = row[side]["failure_rate"]
                self.assertGreaterEqual(rate, 0.0)
                self.assertLessEqual(rate, 1.0)


if __name__ == "__main__":
    unittest.main()
