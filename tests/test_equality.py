"""Equality of output across runs and agents: counts over the runs shown."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.equality import equality_analysis, equality_features, normalise

ROOT = Path(__file__).resolve().parent.parent


def runs_by_task(directory):
    out = {}
    for p in sorted((ROOT / directory).glob("*.json")):
        t = Trajectory.from_dict(json.loads(p.read_text(encoding="utf-8")))
        out.setdefault(t.task.id, {}).setdefault(t.agent.name, []).append(t)
    return out


class NormaliseTest(unittest.TestCase):
    def test_the_same_answer_in_different_words_is_equal(self):
        self.assertEqual(normalise("23 hours 45 minutes"), normalise("23h 45m."))
        self.assertEqual(normalise("$4.82 billion"), normalise("$4.82B"))
        self.assertEqual(normalise("4.1 percent"), normalise("4.1%"))
        self.assertEqual(normalise("1,425 minutes"), normalise("1425 minutes"))

    def test_a_different_answer_stays_different(self):
        self.assertNotEqual(normalise("23 hours 45 minutes"), normalise("11 hours 45 minutes"))
        self.assertNotEqual(normalise("libfoo 2.14.1"), normalise("libfoo 2.14.0"))


class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = runs_by_task("demo/runs/traces")
        cls.eq = equality_analysis(cls.runs)

    def test_counts_are_over_the_runs_listed(self):
        for tid, row in self.eq["tasks"].items():
            for agent, e in row["agents"].items():
                trajs = self.runs[tid][agent]
                self.assertEqual(e["runs"], len(trajs))
                self.assertEqual(sum(g["runs"] for g in e["answers"]), len(trajs))
                self.assertEqual(e["answers"][0]["runs"], max(g["runs"] for g in e["answers"]))
                self.assertAlmostEqual(e["equality_rate"], e["answers"][0]["runs"] / len(trajs), places=4)
                self.assertGreaterEqual(e["distinct_answers"], 1)
                self.assertEqual(e["successes"], sum(1 for t in trajs if t.outcome.success))

    def test_majority_match_and_cross_agent_agreement_are_stated(self):
        t05 = self.eq["tasks"]["t05_flight_duration"]
        self.assertTrue(t05["agents"]["atlas-v2"]["majority_matches_expected"])
        self.assertIn(t05["agents"]["bolt-v3"]["majority_matches_expected"], (True, False))
        self.assertIsNotNone(t05["cross_agent"])
        self.assertEqual(t05["cross_agent"]["agents"], ["atlas-v2", "bolt-v3"])
        self.assertEqual(self.eq["cross_agent"]["tasks_compared"], 8)
        for agent, p in self.eq["per_agent"].items():
            self.assertEqual(p["tasks"], 8)
            self.assertEqual(p["runs"], 24)
            self.assertLessEqual(p["unanimous_tasks"], 8)

    def test_features_fold_per_family_for_the_router(self):
        feats = equality_features(self.eq, "atlas-v2")
        self.assertEqual(set(feats), set(self.eq["tasks"]))
        for fam, f in feats.items():
            self.assertLessEqual(f["equality_rate"], 1.0)
            self.assertGreaterEqual(f["mean_distinct_answers"], 1.0)
        folded = equality_features(self.eq, "atlas-v2", lambda tid: "all")
        self.assertEqual(list(folded), ["all"])
        self.assertEqual(folded["all"]["equality_rate"], self.eq["per_agent"]["atlas-v2"]["equality_rate"])

    def test_the_router_carries_equality_and_a_rationale(self):
        from deepcompare.router import routing_table
        flat = [t for agents in self.runs.values() for ts in agents.values() for t in ts]
        table = routing_table(flat, equality=self.eq)
        fam = table["families"]["t05_flight_duration"]
        for c in fam["candidates"]:
            self.assertIn("equality_rate", c["features"])
        self.assertIn("t05_flight_duration", table["rationale"]["families"])
        text = table["rationale"]["families"]["t05_flight_duration"]
        self.assertIn("Output equality", text)
        self.assertIn("95% interval", text)
        self.assertTrue(table["rationale"]["overall"].startswith("Over every task"))


if __name__ == "__main__":
    unittest.main()
