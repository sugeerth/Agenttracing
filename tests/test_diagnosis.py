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

    def test_earlier_wrong_fact_reanchors_the_root(self):
        a, b, report = _pair(TRACES, "t07_build_failure",
                             "atlas-v2", "bolt-v3")
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


if __name__ == "__main__":
    unittest.main()
