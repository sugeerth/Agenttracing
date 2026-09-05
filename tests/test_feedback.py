"""The loop back: step labels, a preference pair, prompt suggestions.

Everything the feedback module emits must be traceable to a report field,
must never alter the report, and must say what it is — a suggestion is a
hypothesis until a replay flips the outcome.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.feedback import (
    feedback_signal, preference_pair, prompt_suggestions, reward_shaping,
    step_labels, to_jsonl,
)

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "demo" / "traces"


def _report(task: str) -> dict:
    a = Trajectory.from_dict(json.loads((TRACES / f"{task}__atlas-v2.json").read_text()))
    b = Trajectory.from_dict(json.loads((TRACES / f"{task}__bolt-v3.json").read_text()))
    return compare(a, b)


class StepLabelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = _report("t05_flight_duration")
        cls.labels = step_labels(cls.report)

    def test_every_step_of_both_runs_is_labelled_once(self):
        rep = self.report
        self.assertEqual(len(self.labels), len(rep["a"]["steps"]) + len(rep["b"]["steps"]))
        for rec in self.labels:
            self.assertTrue(rec["labels"], rec)
            for l in rec["labels"]:
                self.assertIn("source", l)

    def test_the_fault_labels_follow_the_diagnosis(self):
        rep = self.report
        side = rep["diagnosis"]["subject"]
        dec = rep["diagnosis"]["decisive_step"]["step"]
        by = {(r["side"], r["step"]): [l["label"] for l in r["labels"]] for r in self.labels}
        self.assertIn("fault_enters", by[(side, dec)])
        answer = len(rep[side]["steps"]) - 1
        self.assertIn("wrong_answer", by[(side, answer)])
        carried = {l["step"] for l in rep["diagnosis"]["causal_account"]} - {dec, answer}
        for i in carried:
            self.assertIn("fault_carried", by[(side, i)], i)
        # the passing run carries no fault label
        other = "a" if side == "b" else "b"
        for (s, i), labels in by.items():
            if s == other:
                self.assertFalse({"fault_enters", "fault_carried", "wrong_answer"} & set(labels), (s, i, labels))

    def test_spent_and_role_labels_come_from_the_reading(self):
        rep = self.report
        for side in ("a", "b"):
            reading = rep["reading"][side]
            spent = set(reading["answer_basis"].get("spent_steps") or [])
            roles = {w["step"]: w["role"] for w in reading["what_happened"]}
            for rec in self.labels:
                if rec["side"] != side:
                    continue
                labels = {l["label"] for l in rec["labels"]}
                self.assertEqual("spent_after_basis" in labels, rec["step"] in spent, rec)
                self.assertEqual("dead_end" in labels, roles.get(rec["step"]) == "dead_end", rec)
                self.assertEqual("fed_answer" in labels, roles.get(rec["step"]) == "feeds_answer", rec)


class PreferencePairTest(unittest.TestCase):
    def test_chosen_is_the_splice_when_the_report_has_one(self):
        rep = _report("t05_flight_duration")
        pair = preference_pair(rep)
        self.assertEqual(pair["rejected"]["side"], "b")
        self.assertEqual(pair["chosen"]["side"], "a")
        splice = rep["counterfactual"]["splice"]
        froms = [(t["from"], t["step"]) for t in pair["chosen"]["turns"]]
        self.assertEqual(froms, [("b", i) for i in splice["prefix_steps"]] + [("a", i) for i in splice["adopted_steps"]])
        self.assertIn("estimate", pair["chosen"]["basis"])
        self.assertEqual(pair["diverges_at"]["step"], rep["diagnosis"]["decisive_step"]["step"])
        self.assertEqual(pair["confidence"], rep["counterfactual"]["confidence"])
        self.assertEqual(pair["prompt"], rep["task"]["prompt"])
        self.assertEqual([t["step"] for t in pair["rejected"]["turns"]], list(range(len(rep["b"]["steps"]))))

    def test_no_pair_when_neither_run_passed(self):
        rep = _report("t02_cve_libfoo")
        if rep["a"]["outcome"]["success"] == rep["b"]["outcome"]["success"]:
            self.assertIsNone(preference_pair(rep))
            self.assertEqual(prompt_suggestions(rep), [])

    def test_jsonl_has_one_line_per_pair_and_skips_pairless_reports(self):
        sigs = [feedback_signal(_report("t05_flight_duration")), feedback_signal(_report("t02_cve_libfoo"))]
        text = to_jsonl(sigs)
        lines = [json.loads(l) for l in text.splitlines()]
        self.assertEqual(len(lines), sum(1 for s in sigs if s["preference_pair"]))
        self.assertEqual(set(lines[0]), {"task_id", "prompt", "expected", "chosen", "rejected", "chosen_basis", "diverges_at", "confidence"})


class PromptSuggestionsTest(unittest.TestCase):
    def test_one_suggestion_per_finding_kind_each_citing_its_finding_and_its_test(self):
        rep = _report("t05_flight_duration")
        out = prompt_suggestions(rep)
        kinds = [s["kind"] for s in out]
        self.assertEqual(len(kinds), len(set(kinds)))
        take = {t["because"] for t in rep["reading"]["b"]["take_forward"]}
        for s in out:
            self.assertTrue(s["text"])
            self.assertIn("hypothesis", s["status"])
            if s["kind"] in take:
                self.assertTrue(s["derived_from"]["refs"], s)
                self.assertTrue(s["test"], "each suggestion carries the replay that would test it")
        self.assertNotIn("annotated_weakness", kinds, "a human's quality mark is not a prompt")

    def test_the_tool_selection_suggestion_names_both_tools_from_the_trace(self):
        rep = _report("t05_flight_duration")
        tool = [s for s in prompt_suggestions(rep) if s["kind"] == "tool_selection"]
        self.assertEqual(len(tool), 1)
        self.assertIn("datetime_diff", tool[0]["text"])
        self.assertIn("calculator", tool[0]["text"])
        self.assertEqual(tool[0]["derived_from"]["tools"], {"failing_tool": "calculator", "passing_tool": "datetime_diff"})


class SignalContractTest(unittest.TestCase):
    def test_the_signal_never_mutates_the_report_and_is_deterministic(self):
        rep = _report("t05_flight_duration")
        before = copy.deepcopy(rep)
        one = feedback_signal(rep)
        two = feedback_signal(rep)
        self.assertEqual(rep, before)
        self.assertEqual(one, two)
        self.assertEqual(one["version"], 1)
        self.assertIn("replay", one["note"])

    def test_reward_shaping_counts_match_the_labels(self):
        rep = _report("t05_flight_duration")
        labels = step_labels(rep)
        shaping = {r["event"]: r for r in reward_shaping(rep)}
        for ev, row in shaping.items():
            n = sum(1 for rec in labels for l in rec["labels"] if l["label"] == ev)
            self.assertEqual(row["count"], n, ev)
            self.assertIn(row["sign"], (-1, 1))
        self.assertEqual(shaping["fed_answer"]["sign"], 1)
        self.assertEqual(shaping["fault_enters"]["sign"], -1)

    def test_every_demo_pair_yields_a_signal(self):
        for path in sorted(TRACES.glob("*__atlas-v2.json")):
            task = path.name.split("__")[0]
            sig = feedback_signal(_report(task))
            self.assertEqual(sig["task_id"], task)
            self.assertTrue(sig["step_labels"])


if __name__ == "__main__":
    unittest.main()
