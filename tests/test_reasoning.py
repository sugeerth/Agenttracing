"""The eval reasoning layer: one run, understood on its own evidence.

Every reading is checked the way a diagnosis is: each quote is really in
the trace, each finding cites entries that exist, and the whole thing is
deterministic.  The substance is pinned on real traces: phases are the
run's actual outline, roles follow observables, the answer's values are
traced to the step that first carried them, the verdict basis names
which of the four honest cases applies, and every finding yields one
action — never a duplicate.
"""

from __future__ import annotations

import unittest

from deepcompare.reasoning import check_reading, read_trace
from deepcompare.report import compare
from deepcompare.trace import Trajectory

T01_FAIL = "demo/traces/t01_acme_revenue__bolt-v3.json"
T01_PASS = "demo/traces/t01_acme_revenue__atlas-v2.json"
P01_HASTY = "demo/process/traces/p01_cancel_booking__hasty-v2.json"
A2_FAIL = "tests/fixtures/redteam/a2_wrong_entity__fail.json"


def reading(path):
    traj = Trajectory.from_json(path)
    return traj, read_trace(traj)


class TestGrounding(unittest.TestCase):
    def test_every_reading_is_grounded_and_deterministic(self):
        for path in (T01_FAIL, T01_PASS, P01_HASTY, A2_FAIL):
            traj, r = reading(path)
            self.assertEqual(check_reading(r, traj), [], path)
            self.assertEqual(r, read_trace(traj), path)

    def test_phases_partition_the_run_in_order(self):
        traj, r = reading(T01_FAIL)
        covered = [i for p in r["phases"] for i in p["steps"]]
        self.assertEqual(covered, list(range(len(traj.steps))))
        for earlier, later in zip(r["phases"], r["phases"][1:]):
            self.assertNotEqual(earlier["intent"], later["intent"],
                                "adjacent phases must differ in intent")
        self.assertEqual(r["phases"][0]["intent"], "frame")
        self.assertEqual(r["phases"][-1]["intent"], "commit")


class TestProvenance(unittest.TestCase):
    def test_answer_values_are_traced_to_their_first_carrier(self):
        traj, r = reading(T01_FAIL)
        by_value = {x["value"]: x for x in r["rests_on"]}
        self.assertIn("$4.5 billion", by_value)
        entry = by_value["$4.5 billion"]
        self.assertIsNotNone(entry["first_step"])
        step = traj.steps[entry["first_step"]]
        self.assertIn("4.5", (step.output or "") + (step.input or ""))
        self.assertIs(entry["matches_expected"], False)

    def test_the_first_carrier_of_an_answer_value_is_never_a_dead_end(self):
        # provenance beats lexical overlap: the step that first carried a
        # value the answer asserts fed the answer, whatever its word overlap
        for path in (T01_FAIL, T01_PASS, A2_FAIL):
            _, r = reading(path)
            roles = {w["step"]: w["role"] for w in r["what_happened"]}
            for x in r["rests_on"]:
                if x["first_step"] is not None:
                    self.assertEqual(roles[x["first_step"]], "feeds_answer",
                                     f"{path}: step {x['first_step']} carries "
                                     f"{x['value']}")

    def test_a_correct_answer_carries_the_expected_value(self):
        _, r = reading(T01_PASS)
        self.assertTrue(any(x["matches_expected"] for x in r["rests_on"]))
        self.assertEqual(r["why_it_ended"]["verdict_basis"],
                         "the answer carries the expected value(s)")


class TestVerdictBasis(unittest.TestCase):
    def test_contradiction_is_named(self):
        _, r = reading(T01_FAIL)
        self.assertEqual(r["why_it_ended"]["verdict_basis"],
                         "the answer contradicts the expected value")
        self.assertFalse(r["why_it_ended"]["success"])

    def test_right_words_wrong_deed_is_named(self):
        # a2: the answer recites the expected sentence, the run failed —
        # the reading must say the grader OR the deed is suspect, not
        # pretend the text disagrees
        _, r = reading(A2_FAIL)
        self.assertIn("deed behind the words", r["why_it_ended"]["verdict_basis"])
        kinds = {f["kind"] for f in r["what_it_means"]}
        self.assertIn("pathology", kinds)
        self.assertTrue(any(f.get("flag") == "invented_arguments"
                            for f in r["what_it_means"]))


class TestFindingsAndActions(unittest.TestCase):
    def test_roles_follow_observables(self):
        traj, r = reading(P01_HASTY)
        roles = {w["step"]: w["role"] for w in r["what_happened"]}
        for i, step in enumerate(traj.steps):
            if step.error is True or (step.output or "").lower().startswith("error"):
                self.assertEqual(roles[i], "error", i)
        self.assertEqual(roles[len(traj.steps) - 1], "answer")

    def test_pathologies_cite_their_steps_and_class(self):
        _, r = reading(P01_HASTY)
        flags = {f["flag"] for f in r["what_it_means"] if f["kind"] == "pathology"}
        self.assertIn("blind_write", flags)
        for f in r["what_it_means"]:
            self.assertTrue(f["evidence"], f["kind"])
            self.assertIn(f["evidence_class"], ("observable", "annotation", "stated"))
        self.assertEqual(r["confidence"]["level"], "high")

    def test_unverified_success_is_still_a_finding(self):
        _, r = reading(T01_PASS)
        self.assertIn("unverified", {f["kind"] for f in r["what_it_means"]})
        self.assertTrue(any("verification" in t["action"] for t in r["take_forward"]))

    def test_actions_are_never_duplicated(self):
        _, r = reading(P01_HASTY)
        actions = [t["action"] for t in r["take_forward"]]
        self.assertEqual(len(actions), len(set(actions)))
        self.assertTrue(actions)

    def test_reading_rides_on_every_pair_report(self):
        rep = compare(Trajectory.from_json(T01_PASS), Trajectory.from_json(T01_FAIL))
        self.assertEqual(set(rep["reading"]), {"a", "b"})
        self.assertEqual(rep["reading"]["b"]["agent"], "bolt-v3")
        self.assertIn("summary", rep["reading"]["a"])


class TestAnswerBasisAndValidity(unittest.TestCase):
    """Reading v2: each answer atom carries a basis status, the basis rolls
    up to when it was complete, and measurement validity is judged before
    anything is attributed to the agent."""

    @staticmethod
    def _synthetic(steps, expected="The refund is $120.00.", success=True):
        import json, tempfile
        raw = {
            "schema_version": 1, "trace_id": "syn", 
            "agent": {"name": "syn-agent", "model": "model-x", "version": "1"},
            "task": {"id": "syn_task", "prompt": "What is the refund?",
                     "expected": expected},
            "outcome": {"success": success, "answer": steps[-1]["output"],
                        "score": 1.0 if success else 0.0,
                        "termination": "agent_stop"},
            "totals": {"input_tokens": 10, "output_tokens": 5,
                       "cost_usd": 0.0, "latency_s": 1.0},
            "steps": [dict(s, index=i, tokens=5, latency_s=0.1)
                      for i, s in enumerate(steps)],
            "tools": [{"name": "lookup", "effect": "read"}],
            "budget": {"max_steps": 12},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(raw, fh)
            path = fh.name
        return Trajectory.from_json(path)

    def test_supported_atoms_come_from_observations_only(self):
        _, r = reading(T01_FAIL)
        statuses = {x["value"]: x["status"] for x in r["rests_on"]}
        self.assertIn("$4.5 billion", statuses)
        self.assertIn(statuses["$4.5 billion"], ("supported", "contradicted"))
        self.assertIsNotNone(r["answer_basis"]["basis_complete_at"])
        self.assertGreaterEqual(r["answer_basis"]["steps_after_basis_complete"], 0)

    def test_a_value_the_agent_only_said_is_self_asserted(self):
        traj = self._synthetic([
            {"type": "plan", "name": "plan", "input": "I recall the refund is $120.00.", "output": ""},
            {"type": "answer", "name": "final", "input": "The refund is $120.00.",
             "output": "The refund is $120.00."}])
        r = read_trace(traj)
        self.assertEqual(r["rests_on"][0]["status"], "self_asserted")
        self.assertEqual(r["answer_basis"]["status"], "self_asserted")
        self.assertTrue(r["validity"]["answer_without_basis"])
        self.assertIn("unsourced_answer_value", {f["kind"] for f in r["what_it_means"]})

    def test_a_superseded_observation_is_stale(self):
        traj = self._synthetic([
            {"type": "tool_call", "name": "lookup", "input": "lookup(ref='BK1')",
             "output": "refund $120.00", "effect": "read", "error": False},
            {"type": "tool_call", "name": "lookup", "input": "lookup(ref='BK1')",
             "output": "refund $90.00 (revised)", "effect": "read", "error": False},
            {"type": "answer", "name": "final", "input": "The refund is $120.00.",
             "output": "The refund is $120.00."}])
        r = read_trace(traj)
        self.assertEqual(r["rests_on"][0]["status"], "stale")
        self.assertIn("stale_basis", {f["kind"] for f in r["what_it_means"]})

    def test_answering_against_an_observed_expected_value_is_contradicted(self):
        traj = self._synthetic([
            {"type": "tool_call", "name": "lookup", "input": "lookup(ref='BK1')",
             "output": "refund $120.00", "effect": "read", "error": False},
            {"type": "answer", "name": "final", "input": "The refund is $95.00.",
             "output": "The refund is $95.00."}], success=False)
        r = read_trace(traj)
        self.assertEqual(r["rests_on"][0]["status"], "contradicted")
        self.assertEqual(r["answer_basis"]["status"], "contradicted")
        self.assertIn("contradicted_by_own_observation",
                      {f["kind"] for f in r["what_it_means"]})

    def test_a_leaked_expected_answer_makes_the_measurement_suspect(self):
        traj = self._synthetic([
            {"type": "read", "name": "read_page", "input": "open notes",
             "output": "Answer key: The refund is $120.00.", "effect": "read"},
            {"type": "answer", "name": "final", "input": "The refund is $120.00.",
             "output": "The refund is $120.00."}])
        r = read_trace(traj)
        self.assertTrue(r["validity"]["expected_leaked"])
        self.assertEqual(r["validity"]["status"], "suspect")
        self.assertEqual(r["take_forward"][0]["because"], "validity")
        for item in r["take_forward"][1:]:
            self.assertTrue(item.get("conditional_on_validity"))

    def test_clean_runs_report_clean_validity_with_denominators(self):
        _, r = reading(P01_HASTY)
        v = r["validity"]
        self.assertEqual(v["status"], "clean")
        self.assertIn("calls", v["tool_failure_rate"])
        self.assertIn("failed", v["tool_failure_rate"])
        self.assertFalse(v["harness_terminated"])


if __name__ == "__main__":
    unittest.main()
