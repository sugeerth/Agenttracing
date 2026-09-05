"""Routing features: per family, what each agent did, and the pick.

The pick ranks by the lower bound of the Wilson interval, never a point
rate; thin evidence is called insufficient; overlapping intervals are
called overlapping and list both; every feature is a mean or a count
over the runs listed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.router import family_of, router_hints, routing_table

ROOT = Path(__file__).resolve().parent.parent


def load(directory):
    return [Trajectory.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted((ROOT / directory).glob("*.json"))]


class FamilyTest(unittest.TestCase):
    def test_default_family_strips_a_run_suffix_only(self):
        self.assertEqual(family_of("t05_flight_duration"), "t05_flight_duration")
        self.assertEqual(family_of("t05_flight_duration__r3"), "t05_flight_duration")
        self.assertEqual(family_of("search-7"), "search")
        self.assertEqual(family_of("t05_flight_duration", r"^(t\d+)"), "t05")


class RoutingTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = load("demo/runs/traces")
        cls.fleet = load("demo/fleet/traces")

    def test_features_are_means_and_counts_over_the_runs_listed(self):
        table = routing_table(self.runs)
        fam = table["families"]["t05_flight_duration"]
        for row in fam["candidates"]:
            mine = [t for t in self.runs if t.task.id.startswith("t05_flight_duration") and t.agent.name == row["agent"]]
            f = row["features"]
            self.assertEqual(f["n"], len(mine))
            self.assertEqual(f["successes"], sum(1 for t in mine if t.outcome.success))
            self.assertAlmostEqual(f["cost_usd"], sum(t.totals.cost_usd for t in mine) / len(mine), places=6)
            self.assertAlmostEqual(f["steps"], sum(len(t.steps) for t in mine) / len(mine), places=2)
            self.assertLessEqual(f["ci95"][0], f["rate"])
            self.assertLessEqual(f["rate"], f["ci95"][1])

    def test_the_pick_ranks_by_the_lower_bound_and_names_its_confidence(self):
        table = routing_table(self.fleet)
        for fam, row in table["families"].items():
            cands = row["candidates"]
            lows = [c["features"]["ci95"][0] for c in cands]
            self.assertEqual(lows, sorted(lows, reverse=True), fam)
            self.assertIn(row["confidence"], ("clear", "overlapping", "insufficient"))
            if row["confidence"] == "overlapping":
                self.assertEqual(len(row["either"]), 2)
                a, b = cands[0]["features"]["ci95"], cands[1]["features"]["ci95"]
                self.assertLessEqual(a[0], b[1])
            if row["confidence"] == "clear" and len(cands) > 1:
                self.assertGreater(cands[0]["features"]["ci95"][0], cands[1]["features"]["ci95"][1])
            self.assertTrue(row["why"])

    def test_thin_evidence_is_insufficient(self):
        two = [t for t in self.runs if t.run_id in ("r1", "r2")]
        table = routing_table(two)
        self.assertTrue(all(r["confidence"] == "insufficient" for r in table["families"].values()))
        self.assertTrue(all(h["route_to"] is None for h in router_hints(table)))

    def test_objectives_change_the_pick_but_never_below_half_success(self):
        cheap = routing_table(self.fleet, objective="cost")
        for fam, row in cheap["families"].items():
            top = row["candidates"][0]["features"]
            ok = [c for c in row["candidates"] if c["features"]["ci95"][0] >= 0.5]
            if ok:
                self.assertGreaterEqual(top["ci95"][0], 0.5, fam)
                self.assertEqual(top["cost_usd"], min(c["features"]["cost_usd"] for c in ok), fam)
        with self.assertRaises(ValueError):
            routing_table(self.fleet, objective="vibes")

    def test_reports_add_fault_kinds_per_agent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "traces"), "-o", tmp],
                           cwd=str(ROOT), check=True, capture_output=True)
            reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(tmp).glob("report_*.json"))]
            table = routing_table(load("demo/traces"), reports=reports)
            kinds = {}
            for row in table["families"].values():
                for c in row["candidates"]:
                    for k, v in c["fault_kinds"].items():
                        kinds[k] = kinds.get(k, 0) + v
            self.assertTrue(kinds, "diagnosed failures count as fault kinds for their agent")
            agg = json.loads((Path(tmp) / "aggregate.json").read_text(encoding="utf-8"))
            self.assertIn("routing", agg)
            self.assertEqual(agg["routing"]["objective"], "success")

    def test_the_cli_writes_a_routing_table_with_hints(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "routing.json"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "route", str(ROOT / "demo" / "fleet" / "traces"),
                                   "-o", str(out)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            table = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(table["hints"]), len(table["families"]))
            self.assertIn("overall", table)
            self.assertIn("Wilson", table["note"])


if __name__ == "__main__":
    unittest.main()
