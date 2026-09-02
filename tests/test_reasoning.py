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


if __name__ == "__main__":
    unittest.main()
