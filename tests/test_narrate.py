"""Tests for LLM narration (v25).

What is pinned here is not prose quality — the engine does not produce
prose.  It is the covenant: the engine never calls a model, the narration
never changes a finding, and text that invents a number is stored flagged
rather than stored trusted or silently dropped.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare.narrate import (
    check_narration,
    ingest_narration,
    narrate,
    narration_brief,
    narration_prompt,
)


def sample_report() -> dict:
    return {
        "task": {"id": "t1", "prompt": "find the revenue"},
        "a": {"agent": {"name": "atlas"}, "outcome": {"success": True, "answer": "$4.82 billion"},
              "totals": {"input_tokens": 625, "output_tokens": 215,
                         "cost_usd": 0.0051, "latency_s": 9.95},
              "steps": [{"index": i} for i in range(5)]},
        "b": {"agent": {"name": "bolt"}, "outcome": {"success": False, "answer": "$4.5B"},
              "totals": {"input_tokens": 1156, "output_tokens": 366,
                         "cost_usd": 0.009, "latency_s": 18.4},
              "steps": [{"index": i} for i in range(10)]},
        "divergences": [{"rank": 1, "kind": "retrieval",
                         "summary": "bolt selected a weaker source",
                         "downstream": {"extra_steps_b": 5, "extra_tokens_b": 682}}],
        "attribution": {"failed_agent": "b", "root_cause_step": 2,
                        "chain": [2, 5, 9], "category": "retrieval",
                        "explanation": "bolt diverged at step 2."},
        "metrics_delta": {"tokens": {"a": 840, "b": 1522}},
    }


class TestBrief(unittest.TestCase):
    def test_facts_come_only_from_the_report(self):
        brief = narration_brief(sample_report())
        self.assertGreater(len(brief["facts"]), 4)
        for fact in brief["facts"]:
            self.assertTrue(fact["id"].startswith("F"))
            self.assertTrue(fact["text"])

    def test_the_brief_is_deterministic_and_digested(self):
        one = narration_brief(sample_report())
        two = narration_brief(sample_report())
        self.assertEqual(one["brief_digest"], two["brief_digest"])
        self.assertEqual(one["allowed_numbers"], two["allowed_numbers"])

    def test_report_numbers_are_allowed_in_both_scales(self):
        brief = narration_brief(sample_report())
        # 682 extra tokens appears verbatim; ratios may be quoted as percents.
        self.assertIn("682", brief["allowed_numbers"])

    def test_the_prompt_carries_the_hard_rules(self):
        prompt = narration_prompt(narration_brief(sample_report()))
        self.assertIn("no number that does not appear", prompt)
        self.assertIn("machine-checked", prompt)
        self.assertIn("[F1]", prompt)


class TestFaithfulness(unittest.TestCase):
    def setUp(self):
        self.brief = narration_brief(sample_report())

    def test_supported_numbers_pass(self):
        check = check_narration(self.brief,
                                "bolt spent 682 extra tokens after step 2 [F5].")
        self.assertTrue(check["faithful"])
        self.assertEqual(check["unsupported_numbers"], [])

    def test_an_invented_number_is_flagged_not_accepted(self):
        check = check_narration(self.brief, "We estimate a 93% recurrence risk.")
        self.assertFalse(check["faithful"])
        self.assertIn("93%", check["unsupported_numbers"])

    def test_an_invented_dollar_figure_is_flagged(self):
        check = check_narration(self.brief, "That wasted $14.50 of spend.")
        self.assertIn("$14.50", check["unsupported_numbers"])

    def test_a_citation_to_a_nonexistent_fact_is_flagged(self):
        check = check_narration(self.brief, "bolt failed [F99].")
        self.assertIn("F99", check["invalid_citations"])

    def test_formatting_variants_of_a_real_number_all_pass(self):
        for variant in ("1,522", "1522", "1522.0"):
            check = check_narration(self.brief, f"bolt used {variant} tokens.")
            self.assertEqual(check["unsupported_numbers"], [], variant)

    def test_the_checkers_own_limit_is_declared(self):
        check = check_narration(self.brief, "no numbers here at all")
        self.assertIn("causal claim", check["limit"])


class TestSegregation(unittest.TestCase):
    """Narration must be powerless over the findings."""

    def test_ingestion_touches_nothing_but_its_own_key(self):
        report = sample_report()
        before = copy.deepcopy(report)
        ingest_narration(report, "bolt failed [F1]. Invented: 77%.", model="m")
        after = {k: v for k, v in report.items() if k != "narration"}
        self.assertEqual(before, after,
                         "narration modified a finding — covenant broken")

    def test_unfaithful_text_is_stored_with_its_violations_attached(self):
        report = ingest_narration(sample_report(), "Invented: 77%.", model="m")
        block = report["narration"]
        self.assertFalse(block["faithfulness"]["faithful"])
        self.assertIn("77%", block["faithfulness"]["unsupported_numbers"])
        self.assertIn("commentary only", block["authority"])

    def test_the_engine_makes_no_network_call(self):
        source = Path("deepcompare/narrate.py").read_text(encoding="utf-8")
        for forbidden in ("urllib", "http.client", "requests", "socket"):
            self.assertNotIn(forbidden, source)

    def test_narrate_round_trip_uses_the_callers_callable(self):
        calls = []
        def fake_model(prompt):
            calls.append(prompt)
            return "bolt spent 682 extra tokens [F5]."
        report = narrate(sample_report(), fake_model, model="fake")
        self.assertEqual(len(calls), 1)
        self.assertIn("FACTS:", calls[0])
        self.assertTrue(report["narration"]["faithfulness"]["faithful"])
        self.assertEqual(report["narration"]["model"], "fake")

    def test_provenance_binds_narration_to_the_brief_it_saw(self):
        report = sample_report()
        brief = narration_brief(report)
        ingest_narration(report, "fine [F1]", brief=brief, model="m")
        self.assertEqual(report["narration"]["brief_digest"], brief["brief_digest"])




class TestEvalAgentShapes(unittest.TestCase):
    """The eval-agent role: briefs over aggregates and experiments."""

    def test_an_aggregate_is_detected_and_briefed(self):
        aggregate = {"tasks": 8, "agents": {"a": "atlas", "b": "bolt"},
                     "success_rate": {"a": 0.875, "b": 0.625},
                     "means": {"a": {"tokens": 908.0, "cost_usd": 0.005,
                                     "latency_s": 10.75},
                               "b": {"tokens": 1208.6, "cost_usd": 0.007,
                                     "latency_s": 14.5}},
                     "failure_origins": {"retrieval": 0.5}}
        brief = narration_brief(aggregate)
        self.assertEqual(brief["shape"], "aggregate")
        self.assertGreaterEqual(len(brief["facts"]), 3)
        prompt = narration_prompt(brief)
        self.assertIn("evaluation agent", prompt)
        self.assertIn("machine-checked", prompt)

    def test_an_experiments_result_is_detected_and_briefed(self):
        result = {"experiments": [{"name": "expA", "runs": 24,
                                   "success_rate": 0.875,
                                   "means": {"tokens": 900.0}}],
                  "diffs": [{"narrative": "noise-level difference",
                             "success_diff": {"observed": 0.25},
                             "similarity": {"note": "behaviour moved",
                                            "cross": 0.75, "within": 0.96}}],
                  "narrative": "overall"}
        brief = narration_brief(result)
        self.assertEqual(brief["shape"], "experiments")
        check = check_narration(brief, "cross similarity 0.75 vs within 0.96 [F2]")
        self.assertTrue(check["faithful"])

    def test_a_progress_result_is_detected_and_briefed(self):
        result = {"action_counts": {"resolved": 2, "persists": 3},
                  "actions": [{"status": "resolved", "action": "fix the thing",
                               "reason": "fingerprint gone"}],
                  "new_issues": [],
                  "success_by_agent": {"x": {"before": "2/4", "after": "3/4",
                                             "tasks_compared": 4}},
                  "task_drift": {"dropped": [], "added": []},
                  "narrative": "overall"}
        brief = narration_brief(result)
        self.assertEqual(brief["shape"], "progress")
        self.assertIn("fix attempt", narration_prompt(brief))
        check = check_narration(brief, "2 resolved and 3 persist [F1].")
        self.assertTrue(check["faithful"])
        bad = check_narration(brief, "a 45% cost cut")
        self.assertIn("45%", bad["unsupported_numbers"])

    def test_fabrication_is_caught_in_every_shape(self):
        aggregate = {"tasks": 8, "agents": {"a": "x", "b": "y"},
                     "success_rate": {"a": 0.5, "b": 0.5},
                     "means": {"a": {"tokens": 100}, "b": {"tokens": 100}}}
        brief = narration_brief(aggregate)
        check = check_narration(brief, "y is 37% more reliable than x.")
        self.assertIn("37%", check["unsupported_numbers"])


class TestDiagnosisInBrief(unittest.TestCase):
    """The adjudicated diagnosis reaches the narrator as numbered facts,
    quoted verbatim, with its scores admitted to the allowed-numbers set."""

    @classmethod
    def setUpClass(cls):
        from deepcompare.report import compare
        from deepcompare.trace import Trajectory
        root = Path(__file__).resolve().parent.parent
        a = Trajectory.from_json(
            str(root / "demo" / "traces" / "t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(
            str(root / "demo" / "traces" / "t05_flight_duration__bolt-v3.json"))
        cls.report = compare(a, b)
        cls.brief = narration_brief(cls.report)

    def test_brief_carries_the_verdict_and_hypotheses(self):
        by_source = {}
        for fact in self.brief["facts"]:
            by_source.setdefault(fact["source"], []).append(fact)
        self.assertIn("diagnosis", by_source)
        self.assertEqual(by_source["diagnosis"][0]["text"],
                         self.report["diagnosis"]["verdict"])
        self.assertIn("diagnosis.hypothesis", by_source)
        self.assertGreaterEqual(len(by_source["diagnosis.hypothesis"]), 1)
        self.assertLessEqual(len(by_source["diagnosis.hypothesis"]), 4)
        # merged hypotheses stay out of the brief
        for fact in by_source["diagnosis.hypothesis"]:
            self.assertNotIn("[merged]", fact["text"])
        # the leading hypothesis's discriminator and the confidence ride along
        self.assertIn("diagnosis.discriminator", by_source)
        self.assertTrue(by_source["diagnosis.discriminator"][0]["text"]
                        .startswith("to settle it: "))
        self.assertIn("diagnosis.confidence", by_source)

    def test_quoting_a_diagnosis_score_passes(self):
        leading = next(
            h for h in self.report["diagnosis"]["hypotheses"]
            if h["id"] == self.report["diagnosis"]["leading"])
        text = (f"The leading hypothesis scored {leading['score']}, leading "
                f"by {self.report['diagnosis']['margin']} [F1].")
        check = check_narration(self.brief, text)
        self.assertTrue(check["faithful"], check)

    def test_an_invented_diagnosis_score_is_flagged(self):
        check = check_narration(
            self.brief, "The diagnosis scored this cause at 0.93.")
        self.assertFalse(check["faithful"])
        self.assertIn("0.93", check["unsupported_numbers"])

    def test_a_report_without_a_diagnosis_still_briefs(self):
        old = {k: v for k, v in self.report.items() if k != "diagnosis"}
        brief = narration_brief(old)
        self.assertGreater(len(brief["facts"]), 0)
        for fact in brief["facts"]:
            self.assertFalse(fact["source"].startswith("diagnosis"))
        # sample_report has no diagnosis key either — same guarantee
        brief2 = narration_brief(sample_report())
        self.assertGreater(len(brief2["facts"]), 0)

    def test_contradictions_are_quoted_when_present(self):
        from deepcompare.report import compare
        from deepcompare.trace import Trajectory
        root = Path(__file__).resolve().parent.parent
        a = Trajectory.from_json(str(
            root / "demo" / "process" / "traces"
            / "p01_cancel_booking__steady-v1.json"))
        b = Trajectory.from_json(str(
            root / "demo" / "process" / "traces"
            / "p01_cancel_booking__hasty-v2.json"))
        report = compare(a, b)
        contradictions = report["diagnosis"]["contradictions"]
        self.assertTrue(contradictions)
        brief = narration_brief(report)
        quoted = [f["text"] for f in brief["facts"]
                  if f["source"] == "diagnosis.contradiction"]
        self.assertEqual(quoted, contradictions)


class TestCrossRunFactsInAggregateBrief(unittest.TestCase):
    """The aggregate brief carries systemic and cross-run diagnosis facts."""

    @classmethod
    def setUpClass(cls):
        import glob
        from deepcompare.consolidate import consolidate_diagnoses
        from deepcompare.metrics import aggregate as build_aggregate
        from deepcompare.report import compare
        from deepcompare.stability import medoid_pairs
        root = Path(__file__).resolve().parent.parent
        from deepcompare.trace import Trajectory
        runs_by_task = {}
        for f in sorted(glob.glob(str(root / "demo/runs/traces/*.json"))):
            t = Trajectory.from_json(f)
            side = "a" if t.agent.name == "atlas-v2" else "b"
            runs_by_task.setdefault(t.task.id, {"a": [], "b": []})[side].append(t)
        reports = [compare(a, b) for a, b in medoid_pairs(runs_by_task)]
        agg = build_aggregate(reports)
        agg["diagnosis_consolidated"] = consolidate_diagnoses(runs_by_task)
        cls.brief = narration_brief(agg)

    def test_cross_run_verdicts_are_facts(self):
        texts = [f["text"] for f in self.brief["facts"]
                 if f["source"] == "diagnosis.cross_run"]
        self.assertTrue(any("fails 3 of 3 runs" in t and "reproducible" in t
                            for t in texts))

    def test_executed_checks_are_facts(self):
        texts = [f["text"] for f in self.brief["facts"]
                 if f["source"] == "diagnosis.check"]
        self.assertTrue(any("grader_consistency" in t for t in texts))
        self.assertTrue(any("inconclusive" in t for t in texts))

    def test_systemic_rollup_is_a_fact(self):
        texts = [f["text"] for f in self.brief["facts"]
                 if f["source"] == "diagnosis.systemic"]
        self.assertTrue(any("of 4 diagnosed failure" in t for t in texts))

    def test_reproduction_counts_enter_allowed_numbers(self):
        result = check_narration(
            self.brief, "the cause reproduces in 3 of 3 runs")
        self.assertTrue(result["faithful"])
        invented = check_narration(self.brief, "confidence 0.93")
        self.assertFalse(invented["faithful"])

    def test_aggregate_without_consolidation_still_briefs(self):
        from deepcompare.metrics import aggregate as build_aggregate
        from deepcompare.report import compare
        from deepcompare.trace import Trajectory
        root = Path(__file__).resolve().parent.parent / "demo" / "traces"
        a = Trajectory.from_json(str(root / "t01_acme_revenue__atlas-v2.json"))
        b = Trajectory.from_json(str(root / "t01_acme_revenue__bolt-v3.json"))
        brief = narration_brief(build_aggregate([compare(a, b)]))
        self.assertEqual(
            [f for f in brief["facts"] if f["source"] == "diagnosis.cross_run"],
            [])


class TestDecisiveStepAndAccountInBrief(unittest.TestCase):
    """The pair brief carries the decisive step (or its abstention reason)
    and the mechanism-labelled causal account, verbatim."""

    @classmethod
    def setUpClass(cls):
        from deepcompare.report import compare
        from deepcompare.trace import Trajectory
        cls.compare = staticmethod(compare)
        globals()["compare"] = compare
        root = Path(__file__).resolve().parent.parent
        a = Trajectory.from_json(
            str(root / "demo/traces/t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(
            str(root / "demo/traces/t05_flight_duration__bolt-v3.json"))
        cls.brief = narration_brief(compare(a, b))
        a2 = Trajectory.from_json(
            str(root / "demo/process/traces/p01_cancel_booking__steady-v1.json"))
        b2 = Trajectory.from_json(
            str(root / "demo/process/traces/p01_cancel_booking__hasty-v2.json"))
        cls.grader_brief = narration_brief(compare(a2, b2))

    def _facts(self, brief, source):
        return [f["text"] for f in brief["facts"] if f["source"] == source]

    def test_decisive_step_fact_carries_criterion_and_basis(self):
        facts = self._facts(self.brief, "diagnosis.decisive_step")
        self.assertEqual(len(facts), 1)
        self.assertIn("decisive step 1", facts[0])
        self.assertIn("earliest step whose correction", facts[0])

    def test_abstention_reason_is_a_fact_not_an_omission(self):
        facts = self._facts(self.grader_brief, "diagnosis.decisive_step")
        self.assertEqual(len(facts), 1)
        self.assertIn("no decisive step committed", facts[0])
        self.assertIn("grader or label", facts[0])

    def test_account_facts_carry_their_mechanisms(self):
        facts = self._facts(self.brief, "diagnosis.account")
        self.assertTrue(facts)
        joined = " ".join(facts)
        self.assertIn("word overlap", joined)
        self.assertIn("typed claim provenance", joined)

    def test_account_numbers_enter_the_allowed_set(self):
        result = check_narration(
            self.brief,
            "the fault propagated with word overlap 0.29 into the answer")
        self.assertTrue(result["faithful"])
        wrong = check_narration(self.brief, "word overlap 0.87")
        self.assertFalse(wrong["faithful"])

    def test_reports_without_decisive_step_still_brief(self):
        stripped = {"task": {"id": "t", "prompt": "p"},
                    "a": {"agent": {"name": "x"}, "outcome": {}, "steps": []},
                    "b": {"agent": {"name": "y"}, "outcome": {}, "steps": []}}
        brief = narration_brief(stripped)
        self.assertEqual(
            self._facts(brief, "diagnosis.decisive_step"), [])


class TestSpectrumFactsInAggregateBrief(unittest.TestCase):
    """The aggregate brief carries spectrum facts — the top suspicious
    signature with its counts, or the honest both-classes refusal."""

    @classmethod
    def setUpClass(cls):
        import glob
        from deepcompare.consolidate import consolidate_diagnoses
        from deepcompare.metrics import aggregate as build_aggregate
        from deepcompare.report import compare
        from deepcompare.stability import medoid_pairs
        from deepcompare.trace import Trajectory
        root = Path(__file__).resolve().parent.parent
        rbt = {}
        for f in sorted(glob.glob(str(root / "demo/runs/traces/*.json"))):
            t = Trajectory.from_json(f)
            side = "a" if t.agent.name == "atlas-v2" else "b"
            rbt.setdefault(t.task.id, {"a": [], "b": []})[side].append(t)
        reports = [compare(a, b) for a, b in medoid_pairs(rbt)]
        agg = build_aggregate(reports)
        agg["diagnosis_consolidated"] = consolidate_diagnoses(rbt)
        cls.brief = narration_brief(agg)
        cls.facts = [f["text"] for f in cls.brief["facts"]
                     if f["source"] == "diagnosis.spectrum"]

    def test_measurable_spectrum_becomes_a_fact_with_counts(self):
        measurable = [t for t in self.facts if "suspiciousness" in t]
        self.assertTrue(measurable)
        self.assertIn("2 of 2 failing", measurable[0])
        self.assertIn("0 of 1 passing", measurable[0])

    def test_refusals_are_facts_too(self):
        refusals = [t for t in self.facts if "needs both classes" in t]
        self.assertTrue(refusals)

    def test_spectrum_numbers_enter_the_allowed_set(self):
        result = check_narration(
            self.brief,
            "the suspicious call appears in 2 of 2 failing runs and 0 of "
            "1 passing runs (suspiciousness 1.0)")
        self.assertTrue(result["faithful"])


if __name__ == "__main__":
    unittest.main()
