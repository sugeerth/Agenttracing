"""Tests for process integrity (v22).

The properties worth pinning are not the arithmetic — they are the places
where a process metric can quietly lie: inferring something the log never
said, counting a retry as a loop, scoring an unchecked call as valid, or
letting a clean-but-failed run be indistinguishable from a dirty one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.process import (
    analyse,
    compare_process,
    effect_of,
    false_success,
    grounding,
    is_error,
    loops,
    recovery,
    repeats,
    schema_validity,
    side_effects,
    termination,
)


def build(steps, **kwargs):
    """A minimal valid trajectory whose last step is an answer."""
    body = []
    for i, step in enumerate(steps):
        item = {"index": i, "type": step.get("type", "tool_call"),
                "name": step.get("name", "tool"), "input": step.get("input", ""),
                "output": step.get("output", ""), "tokens": 1, "latency_s": 0.1}
        for key in ("quality", "note", "error", "effect"):
            if key in step:
                item[key] = step[key]
        body.append(item)
    body.append({"index": len(body), "type": "answer", "name": "final",
                 "input": kwargs.get("answer", "done"),
                 "output": kwargs.get("answer", "done"),
                 "tokens": 1, "latency_s": 0.1})
    data = {
        "trace_id": "t", "agent": {"name": "a", "model": "m"},
        "task": {"id": "t1", "prompt": kwargs.get("prompt", "do the thing")},
        "outcome": {"success": kwargs.get("success", True),
                    "answer": kwargs.get("answer", "done")},
        "totals": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
                   "latency_s": 1.0},
        "steps": body,
    }
    if "termination" in kwargs:
        data["outcome"]["termination"] = kwargs["termination"]
    if "tools" in kwargs:
        data["tools"] = kwargs["tools"]
    if "budget" in kwargs:
        data["budget"] = kwargs["budget"]
    return Trajectory.from_json(data)


class TestDeclaredBeatsInferred(unittest.TestCase):
    def test_declared_error_wins_over_the_text_heuristic(self):
        # The word "error" in a summary is not an error.
        step = build([{"output": "no error was found in the logs", "error": False}]).steps[0]
        self.assertEqual(is_error(step), (False, "declared"))

    def test_error_is_inferred_only_when_undeclared(self):
        step = build([{"output": "Error: connection refused"}]).steps[0]
        self.assertEqual(is_error(step), (True, "inferred"))

    def test_declared_effect_beats_the_name_heuristic(self):
        step = build([{"name": "delete_everything", "effect": "read"}]).steps[0]
        self.assertEqual(effect_of(step), ("read", "declared"))

    def test_effect_inferred_from_name_when_undeclared(self):
        self.assertEqual(effect_of(build([{"name": "create_booking"}]).steps[0])[0], "write")
        self.assertEqual(effect_of(build([{"name": "get_user"}]).steps[0])[0], "read")

    def test_unknown_tool_names_are_assumed_not_asserted(self):
        effect, basis = effect_of(build([{"name": "zorble"}]).steps[0])
        self.assertEqual(basis, "assumed")

    def test_thinking_is_never_a_write(self):
        step = build([{"type": "reason", "name": "delete_all"}]).steps[0]
        self.assertEqual(effect_of(step), ("read", "not-a-tool"))

    def test_termination_is_never_guessed(self):
        # "It ended with an answer" does not tell you whether the agent
        # decided it was done or the harness cut it off.
        self.assertEqual(termination(build([{}]))["reason"], "undeclared")
        self.assertFalse(termination(build([{}]))["declared"])
        stopped = build([{}], termination="max_steps")
        self.assertEqual(termination(stopped)["reason"], "max_steps")
        self.assertTrue(termination(stopped)["declared"])


class TestBudget(unittest.TestCase):
    def test_budget_pressure_is_reported_against_a_declared_limit(self):
        run = build([{}] * 8, budget={"max_steps": 10})   # 8 + answer = 9 of 10
        report = termination(run)
        self.assertEqual(report["budget_used"], 0.9)
        self.assertTrue(report["under_budget_pressure"])

    def test_no_budget_means_no_pressure_claim(self):
        report = termination(build([{}] * 40))
        self.assertIsNone(report["budget_used"])
        self.assertFalse(report["under_budget_pressure"])


class TestSideEffects(unittest.TestCase):
    def test_writes_before_any_read_are_flagged(self):
        run = build([{"name": "create_order"}, {"name": "get_order"}])
        ledger = side_effects(run)
        self.assertEqual(ledger["writes"], 1)
        self.assertEqual(ledger["writes_before_any_read"], 1)

    def test_a_write_after_reading_is_not_blind(self):
        run = build([{"name": "get_order"}, {"name": "create_order"}])
        self.assertEqual(side_effects(run)["writes_before_any_read"], 0)

    def test_declared_tool_table_classifies_unguessable_names(self):
        run = build([{"name": "zorble"}], tools=[{"name": "zorble", "effect": "write"}])
        ledger = side_effects(run)
        self.assertEqual(ledger["writes"], 1)
        self.assertEqual(ledger["unclassified"], 0)


class TestRepeatsAndLoops(unittest.TestCase):
    def test_identical_calls_are_repeats(self):
        run = build([{"name": "s", "input": "s(q='x')", "output": "a"},
                     {"name": "s", "input": "s(q='x')", "output": "b"}])
        self.assertEqual(repeats(run)["repeated_calls"], 1)

    def test_a_retry_after_an_error_is_not_counted_as_a_repeat(self):
        # Retrying a failed call is correct behaviour; conflating it with
        # looping would penalise recovery.
        run = build([{"name": "s", "input": "s(q='x')", "output": "Error: timeout"},
                     {"name": "s", "input": "s(q='x')", "output": "fine"}])
        self.assertEqual(repeats(run)["repeated_calls"], 0)

    def test_same_call_and_same_result_is_a_cycle(self):
        run = build([{"name": "s", "input": "s(q='x')", "output": "same"},
                     {"name": "s", "input": "s(q='x')", "output": "same"}])
        self.assertEqual(repeats(run)["cycles"], 1)

    def test_a_new_call_returning_an_old_observation_advanced_nothing(self):
        run = build([{"name": "s", "input": "s(q='x')", "output": "identical text"},
                     {"name": "s", "input": "s(q='y')", "output": "identical text"}])
        self.assertEqual(repeats(run)["no_information_steps"], 1)

    def test_a_repeated_block_is_a_loop_with_a_period(self):
        pattern = [{"name": "a", "input": "a()"}, {"name": "b", "input": "b()"}] * 3
        report = loops(build(pattern).steps)
        self.assertTrue(report["looping"])
        self.assertEqual(report["longest_repeated_block"]["period"], 2)
        self.assertEqual(report["longest_repeated_block"]["repeats"], 3)

    def test_three_of_the_same_call_is_looping_even_without_a_block(self):
        run = build([{"name": "a", "input": "a()"}] * 3)
        self.assertTrue(loops(run.steps)["looping"])

    def test_a_varied_run_is_not_looping(self):
        run = build([{"name": n, "input": f"{n}()"} for n in ("a", "b", "c", "d")])
        self.assertFalse(loops(run.steps)["looping"])


class TestRecovery(unittest.TestCase):
    def test_changing_the_call_after_an_error_and_succeeding_is_recovery(self):
        run = build([{"name": "s", "input": "s(q='x')", "output": "Error: bad query"},
                     {"name": "s", "input": "s(q='y')", "output": "ok"}])
        report = recovery(run)
        self.assertEqual(report["errors"], 1)
        self.assertEqual(report["recovered"], 1)
        self.assertEqual(report["recovery_rate"], 1.0)

    def test_repeating_the_failing_call_is_not_a_recovery_attempt(self):
        run = build([{"name": "s", "input": "s(q='x')", "output": "Error: bad"},
                     {"name": "s", "input": "s(q='x')", "output": "Error: bad"}])
        report = recovery(run)
        self.assertEqual(report["recovery_attempts"], 0)
        self.assertEqual(report["recovered"], 0)

    def test_an_error_with_nothing_after_it_is_abandonment_not_failure_to_fix(self):
        run = build([{"name": "s", "input": "s(q='x')", "output": "Error: bad"}])
        self.assertEqual(recovery(run)["abandoned_after_error"], 1)

    def test_recovery_rate_is_none_rather_than_one_when_there_were_no_errors(self):
        self.assertIsNone(recovery(build([{"name": "s", "output": "ok"}]))["recovery_rate"])


class TestGrounding(unittest.TestCase):
    def test_a_call_to_an_undeclared_tool_is_flagged(self):
        run = build([{"name": "ghost", "input": "ghost()"}],
                    tools=[{"name": "real"}])
        self.assertEqual(grounding(run)["undeclared_tool_calls"], 1)

    def test_grounding_is_unmeasurable_without_a_declared_tool_list(self):
        # Silence about the tool list is not evidence that every call was valid.
        report = grounding(build([{"name": "anything", "input": "anything()"}]))
        self.assertFalse(report["schema_checked"])
        self.assertIsNone(report["schema_grounding"])

    def test_an_argument_from_the_prompt_has_a_source(self):
        run = build([{"name": "s", "input": "s(q='acme corporation')"}],
                    prompt="find the revenue of acme corporation")
        self.assertEqual(grounding(run)["arguments_without_source"], 0)

    def test_an_argument_appearing_nowhere_earlier_was_invented(self):
        run = build([{"name": "s", "input": "s(q='zanzibar holdings')"}],
                    prompt="find the revenue of acme corporation")
        self.assertEqual(grounding(run)["arguments_without_source"], 1)

    def test_an_argument_taken_from_an_earlier_observation_has_a_source(self):
        run = build([{"name": "a", "input": "a()", "output": "found zanzibar holdings"},
                     {"name": "b", "input": "b(q='zanzibar holdings')"}],
                    prompt="find something")
        self.assertEqual(grounding(run)["arguments_without_source"], 0)


class TestSchemaValidity(unittest.TestCase):
    tools = [{"name": "book", "parameters": {
        "properties": {"flight": {"type": "string"}, "seats": {"type": "integer"}},
        "required": ["flight"]}}]

    def test_unchecked_is_not_scored_as_valid(self):
        report = schema_validity(build([{"name": "book", "input": "book(flight='X')"}]))
        self.assertFalse(report["measurable"])
        self.assertIsNone(report["validity"])

    def test_a_missing_required_argument_is_a_violation(self):
        run = build([{"name": "book", "input": "book(seats=2)"}], tools=self.tools)
        report = schema_validity(run)
        self.assertEqual(report["violations"], 1)
        self.assertEqual(report["detail"][0]["kind"], "missing_required")

    def test_an_unknown_argument_is_a_violation(self):
        run = build([{"name": "book", "input": "book(flight='X', colour='red')"}],
                    tools=self.tools)
        self.assertEqual(schema_validity(run)["detail"][0]["kind"], "unknown_argument")

    def test_a_type_mismatch_is_a_violation(self):
        run = build([{"name": "book", "input": "book(flight='X', seats='many')"}],
                    tools=self.tools)
        self.assertEqual(schema_validity(run)["detail"][0]["kind"], "type_mismatch")

    def test_a_valid_call_scores_one(self):
        run = build([{"name": "book", "input": "book(flight='X', seats=2)"}],
                    tools=self.tools)
        self.assertEqual(schema_validity(run)["validity"], 1.0)


class TestFalseSuccess(unittest.TestCase):
    write_tools = [{"name": "book", "effect": "write"}, {"name": "get", "effect": "read"}]

    def test_claiming_completion_without_writing_is_flagged(self):
        run = build([{"name": "get", "input": "get()"}],
                    answer="Done — the booking has been created.",
                    tools=self.write_tools)
        report = false_success(run, side_effects(run))
        self.assertTrue(report["flagged"])

    def test_claiming_completion_after_writing_is_not_flagged(self):
        run = build([{"name": "book", "input": "book()"}],
                    answer="Done — the booking has been created.",
                    tools=self.write_tools)
        self.assertFalse(false_success(run, side_effects(run))["flagged"])

    def test_a_question_answering_run_saying_done_is_not_lying(self):
        # No write tool was offered, so there was nothing to write.
        run = build([{"name": "get", "input": "get()"}],
                    answer="Done. The revenue was $4.82B.",
                    tools=[{"name": "get", "effect": "read"}])
        self.assertFalse(false_success(run, side_effects(run))["flagged"])

    def test_unmeasurable_without_a_declared_tool_list(self):
        run = build([{}], answer="Task completed successfully.")
        report = false_success(run, side_effects(run))
        self.assertFalse(report["flagged"])
        self.assertFalse(report["measurable"])
        self.assertEqual(report["verdict"], "unmeasurable")


class TestOutcomeProcessGap(unittest.TestCase):
    def test_a_success_with_a_loop_is_passed_but_pathological(self):
        run = build([{"name": "a", "input": "a()", "output": "same"},
                     {"name": "a", "input": "a()", "output": "same"}], success=True)
        gap = analyse(run)["gap"]
        self.assertEqual(gap["verdict"], "passed but pathological")
        self.assertIn("looped", gap["raised"])

    def test_a_clean_success_is_reported_as_clean(self):
        run = build([{"name": "get_a", "input": "get_a()", "output": "x"},
                     {"name": "get_b", "input": "get_b()", "output": "y"}], success=True)
        self.assertEqual(analyse(run)["gap"]["verdict"], "passed cleanly")

    def test_a_clean_failure_points_at_the_oracle(self):
        # This is where broken graders live, so it must be a distinct verdict
        # rather than being lumped in with every other failure.
        run = build([{"name": "get_a", "input": "get_a()", "output": "x"}],
                    success=False)
        gap = analyse(run)["gap"]
        self.assertEqual(gap["verdict"], "failed but clean")
        self.assertIn("oracle", gap["narrative"])

    def test_a_failure_with_process_problems_is_failed_with_cause(self):
        run = build([{"name": "a", "input": "a()", "output": "Error: nope"}],
                    success=False)
        self.assertEqual(analyse(run)["gap"]["verdict"], "failed with cause")

    def test_no_single_process_score_is_invented(self):
        # Weighting a loop against a blind write needs domain judgement this
        # tool does not have; flags are reported instead.
        result = analyse(build([{}]))
        self.assertNotIn("score", result["gap"])


class TestPairComparison(unittest.TestCase):
    def test_differing_flags_name_the_side_that_has_them(self):
        clean = build([{"name": "get_a", "input": "get_a()", "output": "x"}])
        looped = build([{"name": "a", "input": "a()", "output": "same"},
                        {"name": "a", "input": "a()", "output": "same"}])
        result = compare_process(clean, looped)
        self.assertIn("looped", result["differing_flags"])
        self.assertTrue(result["narrative"])

    def test_two_clean_runs_say_so(self):
        left = build([{"name": "get_a", "input": "get_a()", "output": "x"}])
        right = build([{"name": "get_b", "input": "get_b()", "output": "y"}])
        result = compare_process(left, right)
        self.assertEqual(result["differing_flags"], [])
        self.assertIn("process-clean", result["narrative"])


class TestOnRealDemoData(unittest.TestCase):
    def test_every_demo_trace_analyses_without_error(self):
        for path in sorted(Path("demo/telemetry/traces").glob("*.json")):
            with self.subTest(path=path.name):
                result = analyse(Trajectory.from_json(path))
                self.assertIn(result["gap"]["verdict"], (
                    "passed cleanly", "passed but pathological",
                    "failed with cause", "failed but clean"))

    def test_the_process_demo_shows_all_four_verdicts(self):
        # The demo exists to show the outcome-process gap. If a change makes
        # every run read as clean, the showcase is silently gone.
        verdicts = {
            Trajectory.from_json(path).trace_id: analyse(
                Trajectory.from_json(path))["gap"]["verdict"]
            for path in sorted(Path("demo/process/traces").glob("*.json"))
        }
        self.assertEqual(len(set(verdicts.values())), 4, verdicts)

    def test_the_headline_pair_is_the_gap_itself(self):
        # A passing run with a filthy process, against a failing run with a
        # clean one — the case outcome-only evaluation cannot express.
        passed = analyse(Trajectory.from_json(
            "demo/process/traces/p01_cancel_booking__hasty-v2.json"))
        failed = analyse(Trajectory.from_json(
            "demo/process/traces/p01_cancel_booking__steady-v1.json"))
        self.assertEqual(passed["gap"]["verdict"], "passed but pathological")
        self.assertEqual(failed["gap"]["verdict"], "failed but clean")
        for flag in ("looped", "swallowed_error", "blind_write"):
            self.assertIn(flag, passed["gap"]["raised"])

    def test_a_write_after_only_failed_reads_is_blind(self):
        # Three failed lookups are not "having looked".
        run = Trajectory.from_json(
            "demo/process/traces/p01_cancel_booking__hasty-v2.json")
        self.assertEqual(side_effects(run)["writes_before_any_read"], 1)

    def test_analysis_is_deterministic(self):
        run = Trajectory.from_json(
            "demo/telemetry/traces/t01_acme_revenue__bolt-v3.json")
        self.assertEqual(analyse(run), analyse(run))


if __name__ == "__main__":
    unittest.main()
