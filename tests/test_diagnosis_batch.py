"""Batch-level diagnosis rollup: the aggregate must say which causes repeat.

A pair diagnosis is a local finding; the batch rollup is what turns four
local findings into one systemic one. The properties worth pinning are the
honest-denominator ones: the count of diagnosed failures must equal the
number of single-failure pairs (not the number of pairs, not the number of
hypotheses), and every by-kind entry must carry its denominator and the
tasks behind it, so "3 of 4" can never quietly become "3".
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.diagnosis import systemic_diagnosis
from deepcompare.metrics import aggregate


class TestBatchDiagnosis(unittest.TestCase):
    """The demo batch (atlas-v2 vs bolt-v3, t01..t08) rolled up."""

    @classmethod
    def setUpClass(cls):
        traces = Path(__file__).resolve().parent.parent / "demo" / "traces"
        if not traces.is_dir():
            raise unittest.SkipTest("demo traces not present")
        by_task: dict[str, dict[str, Trajectory]] = {}
        for path in sorted(traces.glob("*.json")):
            t = Trajectory.from_json(path)
            by_task.setdefault(t.task.id, {})[t.agent.name] = t
        cls.reports = [
            compare(pair["atlas-v2"], pair["bolt-v3"])
            for _, pair in sorted(by_task.items())
            if "atlas-v2" in pair and "bolt-v3" in pair
        ]
        cls.aggregate = aggregate(cls.reports)

    def test_aggregate_carries_the_diagnosis_key(self):
        self.assertIn("diagnosis", self.aggregate)
        self.assertEqual(self.aggregate["diagnosis"],
                         systemic_diagnosis(self.reports))

    def test_diagnosed_failures_counts_single_failure_pairs(self):
        single = sum(
            1 for r in self.reports
            if (r.get("diagnosis") or {}).get("mode") == "single_failure")
        self.assertGreater(single, 0, "demo batch should contain failures")
        self.assertEqual(
            self.aggregate["diagnosis"]["diagnosed_failures"], single)

    def test_by_leading_kind_entries_carry_count_of_and_tasks(self):
        diagnosis = self.aggregate["diagnosis"]
        self.assertTrue(diagnosis["by_leading_kind"])
        for entry in diagnosis["by_leading_kind"]:
            self.assertIn("count", entry)
            self.assertIn("of", entry)
            self.assertIn("tasks", entry)
            self.assertEqual(len(entry["tasks"]), entry["count"])

    def test_denominator_is_diagnosed_failures_on_every_entry(self):
        diagnosis = self.aggregate["diagnosis"]
        for entry in diagnosis["by_leading_kind"]:
            self.assertEqual(entry["of"], diagnosis["diagnosed_failures"])

    def test_counts_never_exceed_the_denominator(self):
        diagnosis = self.aggregate["diagnosis"]
        leads = sum(e["count"] for e in diagnosis["by_leading_kind"])
        self.assertLessEqual(
            leads + diagnosis["contested"], diagnosis["diagnosed_failures"])

    def test_rollup_is_json_serializable_and_deterministic(self):
        first = json.dumps(systemic_diagnosis(self.reports), sort_keys=True)
        second = json.dumps(systemic_diagnosis(self.reports), sort_keys=True)
        self.assertEqual(first, second)

    def test_empty_batch_diagnoses_nothing_without_crashing(self):
        empty = aggregate([])
        self.assertEqual(empty["diagnosis"]["diagnosed_failures"], 0)
        self.assertEqual(empty["diagnosis"]["by_leading_kind"], [])
        self.assertIn("no single-failure pairs", empty["diagnosis"]["note"])


if __name__ == "__main__":
    unittest.main()
