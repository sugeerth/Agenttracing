"""Tests for the adjudicated diagnosis (deepcompare/diagnosis.py).

These pin the behaviour that makes diagnosis deeper than attribution:
competing hypotheses are generated from every signal, corroborating ones
fuse into one account, a thin margin yields a contested verdict rather
than a confident story, and every evidence item is machine-checkable.
"""

import json
import tempfile
import unittest
from pathlib import Path

from deepcompare.diagnosis import (
    LEAD_MARGIN,
    check_diagnosis,
    diagnose,
    systemic_diagnosis,
)
from deepcompare.report import compare
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent
PROCESS = ROOT / "demo" / "process" / "traces"
TRACES = ROOT / "demo" / "traces"


def _pair(directory, task, agent_a, agent_b):
    a = Trajectory.from_json(str(directory / f"{task}__{agent_a}.json"))
    b = Trajectory.from_json(str(directory / f"{task}__{agent_b}.json"))
    return a, b, compare(a, b)


def _leading(diagnosis):
    return next(
        (h for h in diagnosis["hypotheses"] if h["id"] == diagnosis["leading"]),
        None,
    )


class TestGraderSuspect(unittest.TestCase):
    """p01: the failed agent's answer matched the expected answer and its
    process was clean — the grader hypothesis must outrank the divergence
    story, and the conflict must be stated."""

    @classmethod
    def setUpClass(cls):
        cls.a, cls.b, cls.report = _pair(
            PROCESS, "p01_cancel_booking", "steady-v1", "hasty-v2")
        cls.diag = cls.report["diagnosis"]

    def test_grader_leads(self):
        lead = _leading(self.diag)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["kind"], "grader_or_label")

    def test_divergence_demoted_not_hidden(self):
        divergence = [h for h in self.diag["hypotheses"]
                      if h["kind"] == "divergence"]
        self.assertEqual(len(divergence), 1)
        self.assertNotEqual(divergence[0]["status"], "leading")

    def test_answer_vs_outcome_contradiction_stated(self):
        joined = " ".join(self.diag["contradictions"])
        self.assertIn("matched the expected answer", joined)
        self.assertIn("failure", joined)

    def test_pathological_winner_contradiction_stated(self):
        joined = " ".join(self.diag["contradictions"])
        self.assertIn("not a process endorsement", joined)

    def test_evidence_fully_grounded(self):
        self.assertEqual(
            check_diagnosis(self.diag, self.report, self.a, self.b), [])

    def test_leading_has_discriminator(self):
        lead = _leading(self.diag)
        self.assertIn("re-grade", lead["discriminator"])

    def test_confidence_names_alternatives(self):
        conf = self.diag["confidence"]
        self.assertIn(conf["level"], ("low", "medium"))
        self.assertTrue(conf["basis"])


class TestFusion(unittest.TestCase):
    """t05: wrong fact downstream of the divergence — one fused account.
    t07: wrong fact *before* the divergence — the anchor moves earlier."""

    def test_downstream_wrong_fact_becomes_mechanism(self):
        a, b, report = _pair(TRACES, "t05_flight_duration",
                             "atlas-v2", "bolt-v3")
        diag = report["diagnosis"]
        lead = _leading(diag)
        self.assertEqual(lead["kind"], "divergence")
        self.assertEqual(lead.get("mechanism"), "wrong_fact_propagation")
        self.assertIn("entered at step", lead["statement"])
        merged = [h for h in diag["hypotheses"] if h["status"] == "merged"]
        self.assertTrue(any(
            h["kind"] == "wrong_fact_propagation"
            and h["merged_into_kind"] == "divergence" for h in merged))
        self.assertGreaterEqual(diag["margin"], LEAD_MARGIN)
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_shared_claims_do_not_anchor_the_wrong_fact(self):
        # Every contradicting claim in t07 appears in BOTH runs (both read
        # the same version numbers, and the passing run passed anyway), so
        # the claim-exclusivity rule must keep wrong_fact_propagation out
        # and leave the anchor on the failing run's own divergent step.
        a, b, report = _pair(TRACES, "t07_build_failure",
                             "atlas-v2", "bolt-v3")
        diag = report["diagnosis"]
        lead = _leading(diag)
        self.assertEqual(lead["kind"], "divergence")
        self.assertFalse([h for h in diag["hypotheses"]
                          if h["kind"] == "wrong_fact_propagation"])
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_earlier_exclusive_wrong_fact_reanchors_the_root(self):
        # Make t07's contradicting claims exclusive to the failing run by
        # stripping the shared values from the passing run's read steps:
        # the wrong fact then genuinely enters at step 1, before the
        # structural divergence at step 3, and the anchor must move there.
        import os
        fail_path = TRACES / "t07_build_failure__atlas-v2.json"
        pass_data = json.loads(
            (TRACES / "t07_build_failure__bolt-v3.json").read_text())
        for step in pass_data["steps"]:
            for bad in ("0.115.2", "2.0.36", "0.110.0", "2.0.30"):
                step["input"] = step["input"].replace(bad, "the pinned build")
                step["output"] = step["output"].replace(bad, "the pinned build")
        with tempfile.TemporaryDirectory() as tmp:
            mutated = os.path.join(tmp, "pass.json")
            Path(mutated).write_text(json.dumps(pass_data))
            a = Trajectory.from_json(str(fail_path))
            b = Trajectory.from_json(mutated)
            report = compare(a, b)
        diag = report["diagnosis"]
        lead = _leading(diag)
        self.assertEqual(lead["kind"], "wrong_fact_propagation")
        self.assertIn("predates the structural divergence", lead["statement"])
        divergence = next(h for h in diag["hypotheses"]
                          if h["kind"] == "divergence")
        self.assertTrue(divergence["contradicts"])
        self.assertIn("already in play", divergence["statement"])
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_merged_hypotheses_do_not_shape_the_margin(self):
        _, _, report = _pair(TRACES, "t05_flight_duration",
                             "atlas-v2", "bolt-v3")
        diag = report["diagnosis"]
        active = [h for h in diag["hypotheses"]
                  if h["score"] is not None and h["status"] != "merged"]
        active.sort(key=lambda h: -h["score"])
        expected = active[0]["score"] - (
            active[1]["score"] if len(active) > 1 else 0.0)
        self.assertAlmostEqual(diag["margin"], expected, places=4)


class TestModes(unittest.TestCase):
    def test_both_passed_cleanly(self):
        _, _, report = _pair(TRACES, "t02_cve_libfoo", "atlas-v2", "bolt-v3")
        diag = report["diagnosis"]
        self.assertEqual(diag["mode"], "both_succeeded")
        self.assertEqual(diag["hypotheses"], [])
        self.assertIn("both passed cleanly", diag["verdict"])

    def test_pathological_passer_is_diagnosed(self):
        _, _, report = _pair(PROCESS, "p04_policy_lookup",
                             "steady-v1", "hasty-v2")
        diag = report["diagnosis"]
        self.assertEqual(diag["mode"], "both_succeeded")
        self.assertTrue(diag["hypotheses"])
        self.assertTrue(all(h["kind"] == "process_pathology"
                            for h in diag["hypotheses"]))
        self.assertIn("outcome hides", diag["verdict"])
        joined = " ".join(h["statement"] for h in diag["hypotheses"])
        self.assertIn("passed anyway", joined)


class TestEvidenceChecking(unittest.TestCase):
    """check_diagnosis is the same contract as check_narration: tampering
    with any quote, value, or reference is caught."""

    @classmethod
    def setUpClass(cls):
        cls.a, cls.b, cls.report = _pair(
            PROCESS, "p01_cancel_booking", "steady-v1", "hasty-v2")

    def _fresh(self):
        return json.loads(json.dumps(self.report["diagnosis"]))

    def test_tampered_span_quote_is_caught(self):
        diag = self._fresh()
        span = next(e for e in diag["evidence"] if e["type"] == "span")
        span["quote"] = "this text is not in the step"
        problems = check_diagnosis(diag, self.report, self.a, self.b)
        self.assertTrue(any("quote not found" in p for p in problems))

    def test_tampered_metric_value_is_caught(self):
        diag = self._fresh()
        metric = next(e for e in diag["evidence"] if e["type"] == "metric")
        metric["value"] = "not-the-recorded-value"
        problems = check_diagnosis(diag, self.report, self.a, self.b)
        self.assertTrue(any("report holds" in p for p in problems))

    def test_dangling_reference_is_caught(self):
        diag = self._fresh()
        diag["hypotheses"][0]["supports"].append("E999")
        problems = check_diagnosis(diag, self.report, self.a, self.b)
        self.assertTrue(any("dangling" in p for p in problems))

    def test_no_duplicate_refs_within_a_hypothesis(self):
        for h in self.report["diagnosis"]["hypotheses"]:
            refs = h["supports"] + h["contradicts"]
            self.assertEqual(len(refs), len(set(refs)), h["id"])


class TestHonestyEdges(unittest.TestCase):
    def _mutated_pair(self, mutate):
        """Load p01, apply ``mutate(data, side)`` to both raw dicts, and
        compare the mutated trajectories."""
        out = []
        with tempfile.TemporaryDirectory() as tmp:
            for side, name in (("a", "steady-v1"), ("b", "hasty-v2")):
                data = json.loads(
                    (PROCESS / f"p01_cancel_booking__{name}.json").read_text())
                mutate(data, side)
                path = Path(tmp) / f"{side}.json"
                path.write_text(json.dumps(data))
                out.append(Trajectory.from_json(str(path)))
        return out[0], out[1], compare(out[0], out[1])

    def test_no_expected_answer_makes_grader_untestable(self):
        def strip_expected(data, side):
            data["task"].pop("expected", None)
        _, _, report = self._mutated_pair(strip_expected)
        grader = [h for h in report["diagnosis"]["hypotheses"]
                  if h["kind"] == "grader_or_label"]
        self.assertEqual(len(grader), 1)
        self.assertEqual(grader[0]["status"], "untestable")
        self.assertIsNone(grader[0]["score"])
        self.assertIn("cannot be tested", grader[0]["statement"])

    def test_harness_kill_is_its_own_hypothesis(self):
        def kill_a(data, side):
            if side == "a":
                data["outcome"]["termination"] = "infrastructure_error"
        _, _, report = self._mutated_pair(kill_a)
        harness = [h for h in report["diagnosis"]["hypotheses"]
                   if h["kind"] == "harness_termination"]
        self.assertEqual(len(harness), 1)
        self.assertEqual(harness[0]["score"], 0.9)
        self.assertIn("harness killed the run", harness[0]["statement"])

    def test_deterministic(self):
        a = Trajectory.from_json(
            str(TRACES / "t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(
            str(TRACES / "t05_flight_duration__bolt-v3.json"))
        first = json.dumps(compare(a, b)["diagnosis"], sort_keys=True)
        second = json.dumps(compare(a, b)["diagnosis"], sort_keys=True)
        self.assertEqual(first, second)


class TestSystemic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = []
        for task in ("p01_cancel_booking", "p02_book_flight",
                     "p03_change_seats", "p04_policy_lookup"):
            cls.reports.append(
                _pair(PROCESS, task, "steady-v1", "hasty-v2")[2])
        for task in ("t01_acme_revenue", "t02_cve_libfoo", "t03_saas_pricing",
                     "t04_rope_paper", "t05_flight_duration",
                     "t06_bls_unemployment", "t07_build_failure",
                     "t08_changelog_diff"):
            cls.reports.append(_pair(TRACES, task, "atlas-v2", "bolt-v3")[2])
        cls.rollup = systemic_diagnosis(cls.reports)

    def test_denominator_is_single_failure_pairs(self):
        single = sum(1 for r in self.reports
                     if r["diagnosis"]["mode"] == "single_failure")
        self.assertEqual(self.rollup["diagnosed_failures"], single)
        self.assertEqual(single, 7)

    def test_counts_carry_their_denominator_and_tasks(self):
        total = 0
        for entry in self.rollup["by_leading_kind"]:
            self.assertEqual(entry["of"], 7)
            self.assertEqual(entry["count"], len(entry["tasks"]))
            total += entry["count"]
        self.assertEqual(total + self.rollup["contested"], 7)

    def test_repeated_cause_is_called_systemic(self):
        top = self.rollup["by_leading_kind"][0]
        self.assertGreaterEqual(top["count"], 2)
        self.assertIn("one central fix", self.rollup["note"])

    def test_empty_batch_is_honest(self):
        rollup = systemic_diagnosis([])
        self.assertEqual(rollup["diagnosed_failures"], 0)
        self.assertIn("no single-failure pairs", rollup["note"])


class TestErrorPrecedesDivergence(unittest.TestCase):
    """An abandoned tool error at or before the divergent step takes the
    anchor: the divergence is the agent's reaction to the error, and the
    swallowed_error flag merges in as the same event, not a competitor."""

    @classmethod
    def setUpClass(cls):
        bench = ROOT / "demo" / "diagnosis_bench" / "traces"
        cls.a = Trajectory.from_json(
            str(bench / "ef01_refund_gateway__fail.json"))
        cls.b = Trajectory.from_json(
            str(bench / "ef01_refund_gateway__pass.json"))
        cls.report = compare(cls.a, cls.b)
        cls.diag = cls.report["diagnosis"]

    def test_environment_leads(self):
        lead = _leading(self.diag)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["kind"], "environment_error")
        self.assertIn("precedes the structural divergence", lead["statement"])

    def test_swallowed_error_flag_is_merged_not_rival(self):
        merged = [h for h in self.diag["hypotheses"]
                  if h.get("flag") == "swallowed_error"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "merged")
        self.assertEqual(merged[0]["merged_into_kind"], "environment_error")

    def test_divergence_demoted_with_the_reason_stated(self):
        divergence = next(h for h in self.diag["hypotheses"]
                          if h["kind"] == "divergence")
        self.assertNotEqual(divergence["status"], "leading")
        self.assertIn("already in play", divergence["statement"])

    def test_evidence_still_fully_grounded(self):
        self.assertEqual(
            check_diagnosis(self.diag, self.report, self.a, self.b), [])


class TestRedTeamRules(unittest.TestCase):
    """Adversarial pairs from an independent red-team evaluation, kept as
    fixtures so the inversions they exposed can never silently return.

    Each pair was built to make the engine tell a confident wrong story:
    a negated answer that lexically matches the expected one, a wrong
    entity dressed in the right sentence shape, an agent-invented
    argument whose rejection looks environmental, and a duplicated write
    whose repetition IS the failure.  The fixes are exclusivity rules,
    not scenario-specific patches — these tests pin the rules.
    """

    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "redteam"

    @classmethod
    def _diagnose(cls, name):
        a = Trajectory.from_json(str(cls.FIXTURES / f"{name}__fail.json"))
        b = Trajectory.from_json(str(cls.FIXTURES / f"{name}__pass.json"))
        report = compare(a, b)
        return a, b, report, report["diagnosis"]

    def test_negated_answer_cannot_put_the_grader_in_the_lead(self):
        # a1: the failing answer is the expected answer with "not"
        # inserted — high lexical overlap, opposite meaning.  A
        # negator-count mismatch voids grader coverage support, so the
        # grader hypothesis has no answer evidence and must not lead.
        a, b, report, diag = self._diagnose("a1_negation")
        self.assertIsNone(diag["leading"])
        grader = next(h for h in diag["hypotheses"]
                      if h["kind"] == "grader_or_label")
        self.assertFalse(grader["answer_evidence"])
        self.assertNotEqual(grader["status"], "leading")
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_clean_gap_alone_is_not_answer_evidence(self):
        # a10: the failing answer swaps two names — the sentence shape
        # matches, the meaning does not, and the run is procedurally
        # clean.  A grader hypothesis whose only support is the absence
        # of process flags may stay plausible but can never lead.
        a, b, report, diag = self._diagnose("a10_name_swap")
        self.assertIsNone(diag["leading"])
        grader = next(h for h in diag["hypotheses"]
                      if h["kind"] == "grader_or_label")
        self.assertFalse(grader["answer_evidence"])
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_invented_argument_outranks_the_grader_story(self):
        # a2: the agent charged the wrong entity with an argument value
        # that appears nowhere upstream.  The invention is the exclusive
        # measured anomaly and must lead.
        a, b, report, diag = self._diagnose("a2_wrong_entity")
        lead = _leading(diag)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["kind"], "process_pathology")
        self.assertEqual(lead["flag"], "invented_arguments")
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_garbage_argument_error_is_not_pinned_on_the_environment(self):
        # a3: a tool correctly rejects an agent-invented argument —
        # error=true and abandonment make it LOOK environmental, and the
        # replay discriminator would confirm the wrong story (a garbage
        # call errors deterministically).  The grounding dock keeps the
        # environment from leading and flips its discriminator to a
        # provenance-first check.
        a, b, report, diag = self._diagnose("a3_garbage_args")
        self.assertIsNone(diag["leading"])
        env = next(h for h in diag["hypotheses"]
                   if h["kind"] == "environment_error")
        self.assertNotEqual(env["status"], "leading")
        self.assertIn("garbage in", env["statement"])
        self.assertIn("provenance", env["discriminator"])
        self.assertTrue(env["contradicts"])
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_extra_write_twin_anchors_the_duplicate_not_the_answer(self):
        # a5: the failing run charged the customer twice; every step has
        # an exact twin in the passing run EXCEPT that the write appears
        # once more.  The twin rule's write exception must let the
        # duplicated charge anchor the divergence — not the answer step.
        a, b, report, diag = self._diagnose("a5_dup_causal")
        lead = _leading(diag)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["kind"], "divergence")
        self.assertEqual(lead["root"], 2)
        self.assertEqual(diag["decisive_step"]["step"], 2)
        self.assertEqual(check_diagnosis(diag, report, a, b), [])

    def test_extra_read_twins_stay_excused(self):
        # The refinement that keeps the write exception from regressing
        # distractor scenarios: a duplicated READ is alignment noise, not
        # a cause.  Duplicating a1's read step must not hand it the
        # divergence anchor — the anchor stays on the genuinely divergent
        # reasoning step that follows it.
        raw = json.loads(
            (self.FIXTURES / "a1_negation__fail.json").read_text())
        read = dict(raw["steps"][1])
        read["index"] = 2
        raw["steps"] = (raw["steps"][:2] + [read]
                        + [dict(s, index=s["index"] + 1)
                           for s in raw["steps"][2:]])
        with tempfile.TemporaryDirectory() as tmp:
            mutated = Path(tmp) / "fail.json"
            mutated.write_text(json.dumps(raw))
            a = Trajectory.from_json(str(mutated))
            b = Trajectory.from_json(
                str(self.FIXTURES / "a1_negation__pass.json"))
            diag = compare(a, b)["diagnosis"]
        divergence = next(h for h in diag["hypotheses"]
                          if h["kind"] == "divergence")
        self.assertEqual(divergence["root"], 3)


if __name__ == "__main__":
    unittest.main()
