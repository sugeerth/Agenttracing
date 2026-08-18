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


if __name__ == "__main__":
    unittest.main()
