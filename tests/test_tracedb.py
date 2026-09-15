"""The trace database: one SQLite file, the same trajectories a directory
would give, indexed for the questions a store gets asked.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.tracedb import TraceDB, family_of

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "traces"
RUNS = ROOT / "demo" / "runs" / "traces"


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = TraceDB(Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_import_is_idempotent_and_round_trips_the_trajectories(self):
        first = self.db.add_directory(DEMO, source="demo")
        again = self.db.add_directory(DEMO, source="demo")
        self.assertEqual(first["added"], 16)
        self.assertEqual(again["added"], 16)
        self.assertEqual(self.db.count(), 16, "re-importing replaces, never duplicates")
        from_dir = sorted((json.loads(p.read_text(encoding="utf-8")) for p in DEMO.glob("*.json")), key=lambda d: d["trace_id"])
        from_db = sorted((self.db.get(r["trace_id"]) for r in self.db.query()), key=lambda d: d["trace_id"])
        self.assertEqual([d["trace_id"] for d in from_dir], [d["trace_id"] for d in from_db])
        for a, b in zip(from_dir, from_db):
            self.assertEqual(Trajectory.from_dict(a).to_dict(), Trajectory.from_dict(b).to_dict())
        self.assertEqual(len(self.db.trajectories(agent="bolt-v3")), 8)

    def test_columns_index_the_json_and_filters_compose(self):
        self.db.add_directory(DEMO)
        failed = self.db.query(agent="bolt-v3", success=False)
        self.assertEqual(len(failed), 3)
        for r in failed:
            self.assertEqual(r["success"], 0)
            self.assertEqual(r["agent"], "bolt-v3")
        one = self.db.query(task="t05_flight_duration", agent="atlas-v2")[0]
        raw = json.loads((DEMO / "t05_flight_duration__atlas-v2.json").read_text(encoding="utf-8"))
        self.assertEqual(one["steps"], len(raw["steps"]))
        self.assertEqual(one["tool_calls"], sum(1 for s in raw["steps"] if s["type"] in ("tool_call", "search", "retrieve", "read")))
        self.assertAlmostEqual(one["cost_usd"], raw["totals"]["cost_usd"])
        self.assertEqual(one["family"], "t05_flight_duration")
        self.assertEqual(self.db.count(family="t05_flight_duration"), 2)
        self.assertEqual(self.db.count(source="import"), 16)

    def test_runs_keep_their_run_ids_and_pairs_group_them(self):
        self.db.add_directory(RUNS)
        self.assertEqual(self.db.count(), 48)
        pairs = self.db.pairs()
        self.assertEqual(len(pairs), 8)
        self.assertEqual({len(v["atlas-v2"]) for v in pairs.values()}, {3})
        self.assertEqual(sorted({r["run_id"] for r in self.db.query()}), ["r1", "r2", "r3"])

    def test_search_finds_a_step_by_its_text(self):
        self.db.add_directory(DEMO)
        hits = self.db.search("UTC")
        self.assertTrue(hits)
        self.assertTrue(all(h["trace_id"] for h in hits))
        self.assertTrue(any(h["task_id"] == "t05_flight_duration" for h in hits))
        self.assertEqual(self.db.search("no such phrase zzqx"), [])

    def test_summary_counts_what_is_there(self):
        self.db.add_directory(DEMO, source="demo")
        s = self.db.summary()
        self.assertEqual(s["traces"], 16)
        self.assertEqual(s["by"]["agent"], {"atlas-v2": 8, "bolt-v3": 8})
        self.assertEqual(s["by"]["source"], {"demo": 16})
        self.assertEqual(s["success_by_agent"]["bolt-v3"]["successes"], 5)
        self.assertEqual(s["schema_version"], 1)

    def test_an_invalid_trace_is_refused_not_stored(self):
        with self.assertRaises(ValueError):
            self.db.add({"agent": {"name": "x"}, "steps": []})
        self.assertEqual(self.db.count(), 0)
        self.assertEqual(family_of("t05_flight_duration__r2"), "t05_flight_duration")

    def test_checkpoints_keep_the_run_so_far_per_step(self):
        raw = json.loads((DEMO / "t05_flight_duration__bolt-v3.json").read_text(encoding="utf-8"))
        for k in (1, 2, 3):
            partial = dict(raw); partial["steps"] = raw["steps"][:k]; partial["in_progress"] = True
            self.db.checkpoint(partial, label=f"after step {k - 1}", source="watch", recorded_at=100.0 + k)
        self.db.checkpoint({**raw, "steps": raw["steps"][:3]}, source="watch")   # same step: replaced
        cps = self.db.checkpoints("t05_flight_duration__bolt-v3")
        self.assertEqual([c["step"] for c in cps], [1, 2, 3])
        self.assertEqual(cps[0]["label"], "after step 0")
        self.assertEqual(len(cps[2]["trace"]["steps"]), 3)
        self.assertEqual(self.db.checkpoint_ids()[0]["latest"], 3)
        self.assertEqual(self.db.summary()["checkpoints"], {"count": 3, "runs": 1})

    def test_remove_and_export(self):
        self.db.add_directory(DEMO)
        self.assertTrue(self.db.remove("t05_flight_duration__bolt-v3"))
        self.assertFalse(self.db.remove("t05_flight_duration__bolt-v3"))
        self.assertEqual(self.db.count(), 15)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM steps WHERE trace_id = ?", ("t05_flight_duration__bolt-v3",)).fetchone()[0], 0)


class CliTest(unittest.TestCase):
    def test_import_summary_query_export_and_route_from_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.sqlite"
            run = lambda *a: subprocess.run([sys.executable, "-m", "deepcompare", *a], cwd=str(ROOT), capture_output=True, text=True)
            p = run("db", "--db", str(db), "import", str(RUNS), "--source", "demo-runs")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("imported 48", p.stdout)
            p = run("db", "--db", str(db), "summary", "--json")
            self.assertEqual(json.loads(p.stdout)["traces"], 48)
            p = run("db", "--db", str(db), "query", "--agent", "bolt-v3", "--outcome", "failed", "--json")
            rows = json.loads(p.stdout)
            self.assertTrue(rows and all(r["success"] == 0 for r in rows))
            p = run("db", "--db", str(db), "export", "-o", str(Path(tmp) / "out"), "--task", "t05_flight_duration")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(len(list((Path(tmp) / "out").glob("*.json"))), 6)
            p = run("route", "--db", str(db), "-o", str(Path(tmp) / "routing.json"))
            self.assertEqual(p.returncode, 0, p.stderr)
            table = json.loads((Path(tmp) / "routing.json").read_text(encoding="utf-8"))
            self.assertEqual(len(table["families"]), 8)
            self.assertEqual(table["overall"]["candidates"][0]["features"]["n"], 24)

    def test_the_hook_ingests_its_final_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "traces.sqlite"
            payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": "a.py"}, "tool_response": "x = 1"})
            base = [sys.executable, "-m", "deepcompare", "hook", "--traces", tmp, "--task", "t1", "--agent", "cc", "--db", str(db), "--prompt", "read a.py"]
            subprocess.run(base, input=payload, capture_output=True, text=True, cwd=str(ROOT), check=True)
            p = subprocess.run(base, input=json.dumps({"hook_event_name": "Stop"}), capture_output=True, text=True, cwd=str(ROOT), check=True)
            out = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(out.get("db"), str(db), out)
            with TraceDB(db) as store:
                self.assertEqual(store.count(source="hook"), 1)
                self.assertEqual(store.query()[0]["agent"], "cc")


if __name__ == "__main__":
    unittest.main()
