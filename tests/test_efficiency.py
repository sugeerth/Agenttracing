"""Tests for serving efficiency (v24).

The properties worth pinning are the places where an efficiency figure can
quietly lie: counting a retry as a cacheable repeat, declaring two reads
independent when one's arguments came from the other's output, dividing
estimated tokens into a "throughput", presenting a ceiling as a forecast,
or reporting an unrecorded cost as zero (free).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.efficiency import (
    aggregate_efficiency,
    analyse,
    compare_efficiency,
    context_growth,
    latency_concentration,
    parallel_reads,
    result_cache,
    throughput,
)

ROOT = Path(__file__).resolve().parent.parent
PROCESS_TRACES = ROOT / "demo" / "process" / "traces"
TELEMETRY_TRACES = ROOT / "demo" / "telemetry" / "traces"


def build(steps, **kwargs):
    """A minimal valid trajectory whose last step is an answer."""
    body = []
    for i, step in enumerate(steps):
        item = {"index": i, "type": step.get("type", "tool_call"),
                "name": step.get("name", "tool"), "input": step.get("input", ""),
                "output": step.get("output", ""),
                "tokens": step.get("tokens", 10),
                "latency_s": step.get("latency_s", 1.0)}
        for key in ("quality", "note", "error", "effect", "tokens_basis", "model"):
            if key in step:
                item[key] = step[key]
        body.append(item)
    body.append({"index": len(body), "type": "answer", "name": "final",
                 "input": kwargs.get("answer", "done"),
                 "output": kwargs.get("answer", "done"),
                 "tokens": kwargs.get("answer_tokens", 10),
                 "latency_s": kwargs.get("answer_latency", 1.0)})
    data = {
        "trace_id": "t", "agent": {"name": kwargs.get("agent", "a"), "model": "m"},
        "task": {"id": kwargs.get("task", "t1"),
                 "prompt": kwargs.get("prompt", "do the thing")},
        "outcome": {"success": kwargs.get("success", True),
                    "answer": kwargs.get("answer", "done")},
        "totals": {"input_tokens": kwargs.get("input_tokens", 100),
                   "output_tokens": kwargs.get("output_tokens", 30),
                   "cost_usd": kwargs.get("cost_usd", 0.0),
                   "latency_s": kwargs.get("latency_s", 1.0)},
        "steps": body,
    }
    for key in ("tools", "budget", "token_accounting"):
        if key in kwargs:
            data[key] = kwargs[key]
    return Trajectory.from_json(data)


class TestResultCache(unittest.TestCase):
    def test_identical_call_and_result_is_cacheable(self):
        run = build([
            {"name": "get_policy", "input": "get_policy(topic='baggage')",
             "output": "One personal item.", "tokens": 40, "latency_s": 1.2},
            {"name": "get_policy", "input": "get_policy(topic='baggage')",
             "output": "One personal item.", "tokens": 40, "latency_s": 1.2},
        ])
        cache = result_cache(run)
        self.assertEqual(cache["count"], 1)
        self.assertEqual(cache["cacheable_repeats"][0]["first_seen"], 0)
        self.assertEqual(cache["recoverable"]["tokens"], 40)
        self.assertEqual(cache["recoverable"]["latency_s"], 1.2)

    def test_retry_after_an_error_is_not_a_cacheable_repeat(self):
        # Retrying a failed call is correct behaviour, not waste.
        run = build([
            {"name": "get_booking", "input": "get_booking(reference='QX')",
             "output": "Error: not found", "error": True},
            {"name": "get_booking", "input": "get_booking(reference='QX')",
             "output": "Error: not found", "error": True},
        ])
        cache = result_cache(run)
        self.assertEqual(cache["count"], 0)
        self.assertEqual(cache["excluded_retries_after_error"], 1)

    def test_same_call_different_result_is_evidence_against_caching(self):
        run = build([
            {"name": "get_time", "input": "get_time(zone='utc')", "output": "10:00"},
            {"name": "get_time", "input": "get_time(zone='utc')", "output": "10:05"},
        ])
        cache = result_cache(run)
        self.assertEqual(cache["count"], 0)
        self.assertEqual(cache["repeats_with_different_results"], 1)

    def test_unrecorded_tokens_yield_none_not_zero(self):
        run = build([
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v", "tokens": 0},
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v", "tokens": 0},
        ])
        recoverable = result_cache(run)["recoverable"]
        self.assertIsNone(recoverable["tokens"])
        self.assertIn("unrecorded", recoverable["tokens_reason"])

    def test_saving_is_named_a_ceiling(self):
        run = build([{"name": "get_x", "input": "get_x(k='aaaa')", "output": "v"}])
        self.assertIn("ceiling", result_cache(run)["assumption"])


class TestParallelReads(unittest.TestCase):
    def independent_reads(self):
        return [
            {"name": "get_policy", "input": "get_policy(topic='fares')",
             "output": "Fare families exist.", "effect": "read", "latency_s": 1.0},
            {"name": "get_policy", "input": "get_policy(topic='seats')",
             "output": "Seats are assigned.", "effect": "read", "latency_s": 2.0},
            {"name": "get_policy", "input": "get_policy(topic='pets')",
             "output": "Pets fly in cargo.", "effect": "read", "latency_s": 3.0},
        ]

    def test_independent_reads_form_a_run_with_sum_minus_max_saving(self):
        report = parallel_reads(build(self.independent_reads()))
        self.assertEqual(report["count"], 1)
        run = report["runs"][0]
        self.assertEqual(run["steps"], [0, 1, 2])
        self.assertEqual(run["wall_clock_saving_s"], 3.0)   # 6.0 - 3.0

    def test_provenance_link_breaks_the_run(self):
        steps = self.independent_reads()
        # The third call's argument value comes from the first call's output.
        steps[2]["input"] = "get_policy(topic='fare families')"
        report = parallel_reads(build(steps))
        self.assertEqual(report["runs"][0]["steps"], [0, 1])
        self.assertTrue(any("provenance" in b["reason"] for b in report["breaks"]))

    def test_unparseable_arguments_break_the_run(self):
        steps = self.independent_reads()
        steps[1]["input"] = "just some free text, not a call"
        report = parallel_reads(build(steps))
        # No two adjacent checkable reads remain on either side of the break.
        self.assertTrue(any("unparseable" in b["reason"] for b in report["breaks"]))
        for run in report["runs"]:
            self.assertNotIn(1, run["steps"])

    def test_an_error_observation_breaks_the_run(self):
        steps = self.independent_reads()
        steps[1]["error"] = True
        report = parallel_reads(build(steps))
        for run in report["runs"]:
            self.assertNotIn(1, run["steps"])

    def test_a_write_breaks_the_run(self):
        steps = self.independent_reads()
        steps[1]["effect"] = "write"
        steps[1]["name"] = "set_policy"
        report = parallel_reads(build(steps))
        for run in report["runs"]:
            self.assertNotIn(1, run["steps"])

    def test_the_check_says_it_is_textual_and_conservative(self):
        report = parallel_reads(build(self.independent_reads()))
        self.assertIn("conservative", report["method"])
        self.assertIn("ceiling", report["assumption"])

    def test_unrecorded_latency_makes_the_saving_none_not_zero(self):
        steps = self.independent_reads()
        for s in steps:
            s["latency_s"] = 0.0
        report = parallel_reads(build(steps))
        self.assertEqual(report["count"], 1)
        self.assertIsNone(report["runs"][0]["wall_clock_saving_s"])
        self.assertIsNone(report["total_wall_clock_saving_s"])


class TestThroughput(unittest.TestCase):
    def test_refused_on_estimated_tokens(self):
        run = build([{"tokens": 100, "latency_s": 2.0, "tokens_basis": "estimated"}],
                    answer_tokens=0)
        report = throughput(run)
        self.assertIsNone(report["median_tokens_per_s"])
        self.assertIn("estimates", report["reason"])

    def test_refused_on_undeclared_basis(self):
        # An unlabelled count cannot be verified as a measurement.
        run = build([{"tokens": 100, "latency_s": 2.0}], answer_tokens=0)
        report = throughput(run)
        self.assertIsNone(report["median_tokens_per_s"])
        self.assertIn("undeclared", report["reason"])

    def test_computed_only_from_measured_steps(self):
        run = build([
            {"tokens": 100, "latency_s": 2.0, "tokens_basis": "measured"},
            {"tokens": 999, "latency_s": 1.0, "tokens_basis": "estimated"},
        ], answer_tokens=0)
        report = throughput(run)
        self.assertEqual(report["steps_measured"], 1)
        self.assertEqual(report["median_tokens_per_s"], 50.0)
        self.assertEqual(report["steps_excluded"]["estimated"], 1)

    def test_run_level_accounting_basis_fills_in(self):
        run = build([{"tokens": 100, "latency_s": 2.0}], answer_tokens=0,
                    token_accounting={"basis": "measured"})
        self.assertEqual(throughput(run)["median_tokens_per_s"], 50.0)

    def test_zero_latency_is_excluded_not_divided(self):
        run = build([{"tokens": 100, "latency_s": 0.0, "tokens_basis": "measured"}],
                    answer_tokens=0)
        report = throughput(run)
        self.assertIsNone(report["median_tokens_per_s"])
        self.assertEqual(report["steps_excluded"]["no_latency"], 1)

    def test_model_telemetry_sources_are_noted(self):
        run = build([{"tokens": 100, "latency_s": 2.0,
                      "model": {"confidence": 0.9, "source": "ollama-logprobs"}}])
        self.assertEqual(throughput(run)["model_telemetry_sources"],
                         ["ollama-logprobs"])


class TestContextGrowth(unittest.TestCase):
    def test_resend_overhead_is_input_minus_unique_content(self):
        run = build([{}], input_tokens=1000, output_tokens=300,
                    prompt="p" * 40)   # ~10 prompt tokens
        growth = context_growth(run)
        self.assertEqual(growth["prompt_tokens_estimate"], 10)
        self.assertEqual(growth["unique_content_estimate"], 310)
        self.assertEqual(growth["resend_overhead_tokens"], 690)
        self.assertEqual(growth["prompt_cache_absorbable_tokens"], 690)
        self.assertEqual(growth["resend_share"], 0.69)

    def test_the_estimate_is_labelled_estimated_with_its_assumptions(self):
        growth = context_growth(build([{}], input_tokens=1000, output_tokens=300))
        self.assertEqual(growth["basis"], "estimated")
        self.assertTrue(any("ceiling" in a for a in growth["assumptions"]))

    def test_unrecorded_input_is_unmeasurable_not_free(self):
        growth = context_growth(build([{}], input_tokens=0, output_tokens=300))
        self.assertFalse(growth["measurable"])
        self.assertIsNone(growth["resend_overhead_tokens"])
        self.assertIn("unrecorded", growth["reason"])

    def test_estimated_step_tokens_are_flagged_on_the_estimate(self):
        run = build([{"tokens_basis": "estimated"}], input_tokens=1000,
                    output_tokens=300)
        self.assertIn("estimates", context_growth(run)["tokens_note"])

    def test_no_overhead_means_no_opportunity(self):
        run = build([{}], input_tokens=100, output_tokens=300)
        report = analyse(run)
        self.assertFalse([o for o in report["opportunities"]
                          if o["kind"] == "prompt_cache"])


class TestLatencyConcentration(unittest.TestCase):
    def test_flags_and_names_the_dominant_steps(self):
        run = build([{"latency_s": 20.0, "name": "slow_search"},
                     {"latency_s": 1.0}, {"latency_s": 1.0}, {"latency_s": 1.0}])
        report = latency_concentration(run)
        self.assertTrue(report["concentrated"])
        self.assertIn("slow_search", report["note"])
        self.assertEqual(report["top"][0]["index"], 0)

    def test_uniform_short_runs_are_not_called_concentrated(self):
        # In a three-step run "half the time in two steps" is arithmetic,
        # not a finding.
        run = build([{"latency_s": 1.0}, {"latency_s": 1.0}])
        self.assertFalse(latency_concentration(run)["concentrated"])

    def test_unrecorded_latency_is_unmeasurable_not_fast(self):
        run = build([{"latency_s": 0.0}], answer_latency=0.0)
        report = latency_concentration(run)
        self.assertFalse(report["measurable"])
        self.assertIsNone(report["gini"])
        self.assertIn("unmeasurable", report["reason"])

    def test_gini_is_zero_for_uniform_and_high_for_concentrated(self):
        uniform = build([{"latency_s": 1.0}] * 4)
        spiky = build([{"latency_s": 100.0}, {"latency_s": 0.1},
                       {"latency_s": 0.1}, {"latency_s": 0.1}],
                      answer_latency=0.1)
        self.assertEqual(latency_concentration(uniform)["gini"], 0.0)
        self.assertGreater(latency_concentration(spiky)["gini"], 0.7)


class TestOpportunities(unittest.TestCase):
    def test_savings_carry_basis_and_ceiling_assumption(self):
        run = build([
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v",
             "tokens": 40, "latency_s": 1.2, "tokens_basis": "measured"},
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v",
             "tokens": 40, "latency_s": 1.2, "tokens_basis": "measured"},
        ], input_tokens=100, output_tokens=90)
        ops = analyse(run)["opportunities"]
        cache = [o for o in ops if o["kind"] == "result_cache"][0]
        self.assertEqual(cache["saving"]["tokens"], 40)
        self.assertEqual(cache["basis"], "measured")
        self.assertIn("ceiling", cache["assumption"])
        self.assertIn("Cache get_x", cache["action"])
        self.assertIn("2×", cache["action"])

    def test_unestimable_saving_components_carry_reasons(self):
        run = build([
            {"name": "get_policy", "input": "get_policy(topic='fares')",
             "output": "Fare families exist.", "effect": "read"},
            {"name": "get_policy", "input": "get_policy(topic='pets')",
             "output": "Pets fly in cargo.", "effect": "read"},
        ], input_tokens=10, output_tokens=30)
        ops = analyse(run)["opportunities"]
        parallel = [o for o in ops if o["kind"] == "parallel_reads"][0]
        self.assertIsNone(parallel["saving"]["tokens"])
        self.assertIn("tokens", parallel["saving_notes"])
        self.assertIsNone(parallel["saving"]["cost_usd"])

    def test_ranked_and_deterministic(self):
        run = build([
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v",
             "tokens": 40, "latency_s": 1.2},
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v",
             "tokens": 40, "latency_s": 1.2},
        ], input_tokens=1000, output_tokens=100, cost_usd=0.01)
        first = analyse(run)
        second = analyse(run)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))
        ranks = [o["rank"] for o in first["opportunities"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        # tokens rank first: prompt cache (900-ish) above result cache (40)
        self.assertEqual(first["opportunities"][0]["kind"], "prompt_cache")

    def test_unrecorded_cost_never_becomes_a_zero_dollar_saving(self):
        run = build([
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v"},
            {"name": "get_x", "input": "get_x(k='aaaa')", "output": "v"},
        ], cost_usd=0.0)
        cache = [o for o in analyse(run)["opportunities"]
                 if o["kind"] == "result_cache"][0]
        self.assertIsNone(cache["saving"]["cost_usd"])
        self.assertIn("unrecorded", cache["saving_notes"]["cost_usd"])


class TestPairwiseAndAggregate(unittest.TestCase):
    def pair(self, cost=0.01, success_b=True):
        a = build([{"name": "get_x", "input": "get_x(k='aaaa')", "output": "v"}],
                  agent="left", cost_usd=cost)
        b = build([{"name": "get_x", "input": "get_x(k='aaaa')", "output": "v"}],
                  agent="right", cost_usd=cost, success=success_b)
        return a, b

    def test_pairwise_block_has_both_sides_and_a_narrative(self):
        a, b = self.pair()
        block = compare_efficiency(a, b)
        self.assertEqual(block["a"]["agent"], "left")
        self.assertEqual(block["b"]["agent"], "right")
        self.assertIn("left", block["narrative"])

    def test_report_and_aggregate_are_wired(self):
        from deepcompare.metrics import aggregate
        from deepcompare.report import compare
        a, b = self.pair()
        report = compare(a, b)
        self.assertIn("efficiency", report)
        rollup = aggregate([report])
        self.assertIn("efficiency", rollup)
        self.assertEqual(rollup["efficiency"]["agents"]["a"], "left")

    def test_cost_per_success_divides_total_cost_by_successes(self):
        from deepcompare.report import compare
        a, b = self.pair(cost=0.01, success_b=False)
        rollup = aggregate_efficiency([compare(a, b)])
        self.assertEqual(rollup["per_agent"]["a"]["cost_per_success"]["value_usd"],
                         0.01)
        b_side = rollup["per_agent"]["b"]["cost_per_success"]
        self.assertIsNone(b_side["value_usd"])
        self.assertIn("zero denominator", b_side["reason"])

    def test_zero_cost_everywhere_is_unmeasurable_not_free(self):
        from deepcompare.report import compare
        a, b = self.pair(cost=0.0)
        rollup = aggregate_efficiency([compare(a, b)])
        side = rollup["per_agent"]["a"]["cost_per_success"]
        self.assertIsNone(side["value_usd"])
        self.assertIn("unrecorded ≠ free", side["reason"])

    def test_empty_aggregate_shape(self):
        rollup = aggregate_efficiency([])
        self.assertEqual(rollup["tasks"], 0)
        self.assertIsNone(rollup["per_agent"]["a"])

    def test_reports_without_the_block_are_counted_not_skipped_silently(self):
        from deepcompare.report import compare
        a, b = self.pair()
        report = compare(a, b)
        del report["efficiency"]
        rollup = aggregate_efficiency([report])
        self.assertEqual(rollup["reports_missing_efficiency"], 1)
        # cost per success still covers the report — it needs only totals.
        self.assertEqual(rollup["per_agent"]["a"]["cost_per_success"]["runs"], 1)


class TestDemoCorpora(unittest.TestCase):
    """The real corpora: sane, non-empty output where the pattern exists,
    honest emptiness where it does not."""

    def load(self, directory, name):
        return Trajectory.from_json(directory / name)

    def test_process_corpus_finds_the_policy_repeat_and_excludes_the_retries(self):
        hasty = self.load(PROCESS_TRACES, "p04_policy_lookup__hasty-v2.json")
        cache = result_cache(hasty)
        self.assertEqual(cache["count"], 1)   # get_policy(topic='cabin') again
        self.assertEqual(cache["cacheable_repeats"][0]["name"], "get_policy")
        booking = self.load(PROCESS_TRACES, "p01_cancel_booking__hasty-v2.json")
        retry = result_cache(booking)
        self.assertEqual(retry["count"], 0)   # three failing lookups = retries
        self.assertEqual(retry["excluded_retries_after_error"], 2)

    def test_process_corpus_finds_parallelizable_policy_reads(self):
        hasty = self.load(PROCESS_TRACES, "p04_policy_lookup__hasty-v2.json")
        report = parallel_reads(hasty)
        self.assertGreaterEqual(report["count"], 1)
        self.assertEqual(report["runs"][0]["steps"], [0, 1, 2, 3, 4])
        self.assertEqual(report["runs"][0]["wall_clock_saving_s"], 4.8)

    def test_telemetry_corpus_has_resend_overhead_but_no_cacheable_repeats(self):
        atlas = self.load(TELEMETRY_TRACES, "t01_acme_revenue__atlas-v2.json")
        report = analyse(atlas)
        growth = report["context_growth"]
        # The demo generator uses input ≈ 3× output; most input is re-send.
        self.assertGreater(growth["resend_share"], 0.5)
        kinds = {o["kind"] for o in report["opportunities"]}
        self.assertIn("prompt_cache", kinds)
        self.assertEqual(report["result_cache"]["count"], 0)   # honest emptiness
        self.assertEqual(report["parallel_reads"]["count"], 0)

    def test_demo_throughput_is_refused_because_basis_is_undeclared(self):
        atlas = self.load(TELEMETRY_TRACES, "t01_acme_revenue__atlas-v2.json")
        report = throughput(atlas)
        self.assertIsNone(report["median_tokens_per_s"])
        self.assertIn("undeclared", report["reason"])
        self.assertEqual(report["model_telemetry_sources"], ["synthetic-demo"])

    def test_demo_analysis_is_deterministic(self):
        hasty = self.load(PROCESS_TRACES, "p04_policy_lookup__hasty-v2.json")
        self.assertEqual(json.dumps(analyse(hasty), sort_keys=True),
                         json.dumps(analyse(hasty), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
