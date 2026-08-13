"""Tests for reference-based tool-call comparison (v22).

The point of this module is interoperability, so the tests pin fidelity to
the published definitions rather than to whatever seems reasonable — and
they pin the disagreements too.  Four libraries compute four different
numbers under the name "tool call accuracy"; a test suite that quietly
picked one would recreate exactly the confusion the module exists to
document.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.toolmatch import (
    ARG_MODES,
    MATCH_MODES,
    args_match,
    calls_of,
    evaluate,
    tool_call_f1,
    tool_permission,
    trajectory_match,
    weighted_lcs_correctness,
)


def build(calls, **kwargs):
    steps = [{"index": i, "type": "tool_call", "name": name,
              "input": f"{name}({args})", "output": "ok",
              "tokens": 1, "latency_s": 0.1}
             for i, (name, args) in enumerate(calls)]
    steps.append({"index": len(steps), "type": "answer", "name": "final",
                  "input": "done", "output": "done", "tokens": 1, "latency_s": 0.1})
    data = {
        "trace_id": "t", "agent": {"name": kwargs.get("name", "a"), "model": "m"},
        "task": {"id": "t1", "prompt": "p"},
        "outcome": {"success": True, "answer": "done"},
        "totals": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
                   "latency_s": 1.0},
        "steps": steps,
    }
    if "tools" in kwargs:
        data["tools"] = kwargs["tools"]
    return Trajectory.from_json(data)


REFERENCE = build([("search", "q='acme'"), ("read", "url='ir.acme.com'")])


class TestArgumentModes(unittest.TestCase):
    left = {"query": "acme", "limit": "10", "sort": "desc"}
    right = {"query": "acme", "limit": "10", "sort": "asc"}

    def test_the_same_pair_scores_differently_under_each_mode(self):
        # This is the module's reason for existing: an "accuracy" number is
        # meaningless without the algorithm that produced it.
        scores = {mode: args_match(self.left, self.right, mode) for mode in ARG_MODES}
        self.assertEqual(scores["exact"], 0.0)
        self.assertEqual(scores["ignore"], 1.0)
        self.assertAlmostEqual(scores["key_fraction"], 2 / 3, places=3)
        self.assertGreater(len(set(scores.values())), 2)

    def test_key_fraction_divides_by_the_union_not_the_overlap(self):
        # Over the intersection, a call omitting half its arguments would
        # score perfectly for the half it kept.
        self.assertAlmostEqual(
            args_match({"a": "1"}, {"a": "1", "b": "2"}, "key_fraction"), 0.5, places=3)

    def test_subset_means_the_run_passed_nothing_extra(self):
        self.assertEqual(args_match({"a": "1"}, {"a": "1", "b": "2"}, "subset"), 1.0)
        self.assertEqual(args_match({"a": "1", "b": "2"}, {"a": "1"}, "subset"), 0.0)

    def test_superset_is_the_other_direction(self):
        self.assertEqual(args_match({"a": "1", "b": "2"}, {"a": "1"}, "superset"), 1.0)

    def test_identical_arguments_match_under_every_mode(self):
        for mode in ARG_MODES:
            self.assertEqual(args_match(self.left, self.left, mode), 1.0, mode)

    def test_an_unknown_mode_is_refused_rather_than_defaulted(self):
        with self.assertRaises(ValueError):
            args_match({}, {}, "vibes")


class TestMatchModes(unittest.TestCase):
    def test_an_identical_run_matches_every_mode(self):
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'")])
        for mode in MATCH_MODES:
            self.assertTrue(trajectory_match(run, REFERENCE, mode)["match"], mode)

    def test_reordering_fails_strict_and_passes_unordered(self):
        run = build([("read", "url='ir.acme.com'"), ("search", "q='acme'")])
        self.assertFalse(trajectory_match(run, REFERENCE, "strict")["match"])
        self.assertTrue(trajectory_match(run, REFERENCE, "unordered")["match"])

    def test_an_extra_call_fails_subset_and_passes_superset(self):
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'"),
                     ("extra", "x='1'")])
        self.assertFalse(trajectory_match(run, REFERENCE, "subset")["match"])
        self.assertTrue(trajectory_match(run, REFERENCE, "superset")["match"])

    def test_a_missing_call_passes_subset_and_fails_superset(self):
        run = build([("search", "q='acme'")])
        self.assertTrue(trajectory_match(run, REFERENCE, "subset")["match"])
        self.assertFalse(trajectory_match(run, REFERENCE, "superset")["match"])

    def test_the_polarity_is_spelled_out_in_words(self):
        # subset/superset invert in conversation constantly, so each result
        # carries its meaning rather than only its name.
        result = trajectory_match(build([]), REFERENCE, "subset")
        self.assertIn("only tools the reference called", result["means"])

    def test_every_result_names_its_algorithm(self):
        for mode in MATCH_MODES:
            result = trajectory_match(build([]), REFERENCE, mode)
            self.assertIn("agentevals", result["algorithm"])
            self.assertIn(mode, result["algorithm"])

    def test_match_is_boolean_with_no_invented_partial_credit(self):
        run = build([("search", "q='acme'")])
        self.assertIsInstance(trajectory_match(run, REFERENCE, "strict")["match"], bool)

    def test_an_unknown_match_mode_is_refused(self):
        with self.assertRaises(ValueError):
            trajectory_match(build([]), REFERENCE, "roughly")


class TestF1(unittest.TestCase):
    def test_a_perfect_run_scores_one(self):
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'")])
        self.assertEqual(tool_call_f1(run, REFERENCE)["f1"], 1.0)

    def test_an_extra_call_costs_precision_not_recall(self):
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'"),
                     ("extra", "x='1'")])
        report = tool_call_f1(run, REFERENCE)
        self.assertEqual(report["recall"], 1.0)
        self.assertLess(report["precision"], 1.0)
        self.assertEqual(report["false_positives"], 1)

    def test_a_missing_call_costs_recall_not_precision(self):
        run = build([("search", "q='acme'")])
        report = tool_call_f1(run, REFERENCE)
        self.assertEqual(report["precision"], 1.0)
        self.assertLess(report["recall"], 1.0)
        self.assertEqual(report["false_negatives"], 1)

    def test_the_two_ways_of_being_wrong_are_named(self):
        # An F1 of 0.67 hides whether the agent did too much or too little,
        # and those call for opposite fixes.
        run = build([("search", "q='acme'"), ("extra", "x='1'")])
        report = tool_call_f1(run, REFERENCE)
        self.assertEqual(report["extra_calls"], ["extra"])
        self.assertEqual(report["missed_calls"], ["read"])

    def test_f1_ignores_order(self):
        forward = build([("search", "q='acme'"), ("read", "url='ir.acme.com'")])
        reverse = build([("read", "url='ir.acme.com'"), ("search", "q='acme'")])
        self.assertEqual(tool_call_f1(forward, REFERENCE)["f1"],
                         tool_call_f1(reverse, REFERENCE)["f1"])

    def test_an_argument_mismatch_is_total_under_exact_and_partial_under_fraction(self):
        run = build([("search", "q='wrong'"), ("read", "url='ir.acme.com'")])
        self.assertLess(tool_call_f1(run, REFERENCE, "exact")["f1"], 1.0)
        self.assertEqual(tool_call_f1(run, REFERENCE, "ignore")["f1"], 1.0)


class TestWeightedLCS(unittest.TestCase):
    def test_an_identical_run_scores_one(self):
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'")])
        self.assertEqual(weighted_lcs_correctness(run, REFERENCE)["score"], 1.0)

    def test_reordering_keeps_partial_credit_rather_than_zeroing(self):
        # The whole reason to prefer LCS over an order gate: a right-calls
        # wrong-order run is not as bad as a wrong-calls run.
        reordered = build([("read", "url='ir.acme.com'"), ("search", "q='acme'")])
        wrong = build([("delete", "id='1'"), ("purge", "id='2'")])
        self.assertGreater(weighted_lcs_correctness(reordered, REFERENCE)["score"],
                           weighted_lcs_correctness(wrong, REFERENCE)["score"])
        self.assertGreater(weighted_lcs_correctness(reordered, REFERENCE)["score"], 0.0)

    def test_score_is_capped_at_one_however_many_extra_calls(self):
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'")] * 4)
        self.assertLessEqual(weighted_lcs_correctness(run, REFERENCE)["score"], 1.0)

    def test_a_reference_with_no_calls_scores_none_not_zero(self):
        self.assertIsNone(
            weighted_lcs_correctness(build([]), build([]))["score"])


class TestPermission(unittest.TestCase):
    def test_a_denied_tool_is_a_violation(self):
        run = build([("delete_all", "")])
        report = tool_permission(run, allowed=["delete_all"], denied=["delete_all"])
        # Deny beats allow: an overlapping allowlist must not re-enable a
        # tool that should never run.
        self.assertEqual(report["violations"], 1)
        self.assertEqual(report["detail"][0]["rule"], "denied")

    def test_a_tool_outside_the_allowlist_is_a_violation(self):
        report = tool_permission(build([("ghost", "")]), allowed=["search"])
        self.assertEqual(report["violations"], 1)

    def test_declared_tools_act_as_the_allowlist(self):
        run = build([("ghost", "")], tools=[{"name": "search"}])
        self.assertEqual(tool_permission(run)["violations"], 1)

    def test_unmeasurable_without_any_list(self):
        report = tool_permission(build([("anything", "")]))
        self.assertFalse(report["measurable"])
        self.assertIsNone(report["score"])

    def test_a_run_that_called_nothing_violated_nothing(self):
        self.assertEqual(tool_permission(build([]), allowed=["search"])["score"], 1.0)


class TestEvaluate(unittest.TestCase):
    def test_all_four_modes_are_reported_together(self):
        result = evaluate(build([("search", "q='acme'")]), REFERENCE)
        self.assertEqual(sorted(result["matches"]), sorted(MATCH_MODES))

    def test_modes_may_legitimately_disagree(self):
        # A run can be a superset match and not a strict one; showing which
        # modes pass is more informative than one verdict.
        run = build([("search", "q='acme'"), ("read", "url='ir.acme.com'"),
                     ("extra", "x='1'")])
        result = evaluate(run, REFERENCE)
        self.assertTrue(result["matches"]["superset"]["match"])
        self.assertFalse(result["matches"]["strict"]["match"])

    def test_the_narrative_warns_that_the_arg_mode_changes_the_number(self):
        result = evaluate(build([("search", "q='acme'")]), REFERENCE)
        self.assertIn("argument matching", result["narrative"])

    def test_unparseable_calls_are_kept_not_dropped(self):
        # Dropping them would quietly improve every score.
        run = build([("weird", "this is not a call syntax")])
        self.assertEqual(len(calls_of(run)), 1)

    def test_deterministic(self):
        run = build([("search", "q='acme'")])
        self.assertEqual(evaluate(run, REFERENCE), evaluate(run, REFERENCE))


class TestOnRealDemoData(unittest.TestCase):
    def test_evaluates_a_real_pair(self):
        reference = Trajectory.from_json(
            "demo/telemetry/traces/t01_acme_revenue__atlas-v2.json")
        run = Trajectory.from_json(
            "demo/telemetry/traces/t01_acme_revenue__bolt-v3.json")
        result = evaluate(run, reference)
        self.assertIsNotNone(result["f1"]["f1"])
        self.assertTrue(result["narrative"])


if __name__ == "__main__":
    unittest.main()
