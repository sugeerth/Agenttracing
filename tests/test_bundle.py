"""The bundle, its id and its key.

What this pins: an output directory reads as a member of the right
kind; the same inputs give the same bytes and the same id, in any
directory; verify recomputes the id and notices a changed byte; the
level-2 rows carry every run with numbers only where a source recorded
them; the level-3 record holds the steps, the budget, the fetches and
the timeline in the timescape's shape; a lineage's episodes become rows
with their generation and SYNTHETIC label; the key round-trips, stays
printable ASCII on one line under its size cap, truncates by counting
and refuses a malformed text with a reason; the commands' exit codes;
no timestamp and no model identifier anywhere in the index.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import bundle  # noqa: E402
from deepcompare.commands import key as key_cmd  # noqa: E402
from deepcompare.commands.paths import DEFAULT_TEMPLATE  # noqa: E402
from tests.helpers_bundle import batch_output, demo_bundle, parser  # noqa: E402


def _files(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _key_parser():
    import argparse
    p = argparse.ArgumentParser(prog="agentdiff")
    sub = p.add_subparsers(dest="command", required=True)
    key_cmd.register(sub)
    return p


def _run(cmd_module, args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cmd_module.run(args)
    return code, out.getvalue(), err.getvalue()


def fake_evolve_member() -> dict:
    """A lineage member built by hand: two generations of five episodes,
    a scorecard for the last pair, no reports."""
    def episode(task, run, ok, steps, tools):
        return {"task_id": task, "run_id": run, "trace_id": f"{task}-{run}", "success": ok, "return": 1.0 if ok else -1.0,
                "steps": steps, "seconds": 3.5, "tools": tools, "errors": 0, "wasted_s": 0.0, "source": "recorded",
                "timeline": [[0.0, 1.0, "think", "plan", 0.0, ""], [1.0, 2.5, "tool", "grep", -0.1, "w"]]}
    gens = []
    for i in range(2):
        gens.append({"id": f"g{i}", "parent": f"g{i - 1}" if i else None, "index": i, "policy": f"fam@g{i}",
                     "note": "SYNTHETIC: a generated generation", "measurable": True,
                     "episodes": [episode("t1", f"r{k}", k % 2 == 0, 10 + k, {"grep": 2, "read_file": 1}) for k in range(5)]})
    aggregate = {"tasks": 1, "agents": {"a": "fam@g0", "b": "fam@g1"},
                 "evolution": {"version": 1, "measurable": True, "reason": None, "family": "fam", "generations": gens,
                               "steps": [{"from": "g0", "to": "g1", "verdict": "improved", "flags": []}],
                               "recommended": {"id": "g1"}, "best": {"id": "g1"}, "integrity": {"reading": "nothing touched"}},
                 "scorecard": {"per_run": [{"task": "t1", "agent": "fam@g1", "run_id": "r0", "success": True,
                                            "spend": {"steps": 10, "tokens": 500, "cost_usd": 0.0, "latency_s": 3.5},
                                            "tools": {"calls": 2, "errors": 0}, "trajectory": {"repeated_calls": 1}}]}}
    return {"kind": "evolve", "source": "fake", "path": "/nonexistent", "aggregate": aggregate, "reports": [], "fleet": None,
            "tasks": ["t1"], "agents": ["fam@g0", "fam@g1"], "lineage": "fam", "sections": ["evolution", "scorecard"]}


class MemberTest(unittest.TestCase):
    def test_the_batch_output_reads_as_a_batch_member(self):
        m = bundle.read_member(batch_output())
        self.assertEqual(m["kind"], "batch")
        self.assertEqual(m["agents"], ["atlas-v2", "bolt-v3"])
        self.assertEqual(len(m["tasks"]), 8)
        self.assertIsNone(m["lineage"])
        self.assertIn("budget", m["sections"])
        self.assertIn("fetches", m["sections"])
        self.assertNotIn("task", m["sections"])

    def test_a_trace_or_an_empty_directory_is_refused_with_a_reason(self):
        with self.assertRaises(ValueError):
            bundle.read_member(ROOT / "demo" / "traces" / "t01_acme_revenue__atlas-v2.json")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            bundle.read_member(tmp)


class BuildTest(unittest.TestCase):
    def test_same_inputs_same_bytes_and_the_same_id_elsewhere(self):
        members = [bundle.read_member(batch_output())]
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            a = bundle.write_bundle(members, one, DEFAULT_TEMPLATE, name="demo")
            b = bundle.write_bundle(members, two, DEFAULT_TEMPLATE, name="demo")
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(a["key"], b["key"])
            self.assertEqual(_files(Path(one)), _files(Path(two)))
            self.assertEqual(a["id"], bundle.digest(members))
            self.assertTrue(a["id"].startswith("sha256:"))
            self.assertEqual(bundle.verify(one), {"id": a["id"], "recomputed": a["id"], "match": True})

    def test_the_demo_bundle_has_every_file_and_no_timestamp(self):
        root = demo_bundle()
        manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["name"], "demo")
        self.assertEqual(manifest["locators"], ["file:///tmp/demo"])
        self.assertEqual([m["kind"] for m in manifest["members"]], ["batch"])
        self.assertTrue((root / "members" / "0" / "aggregate.json").is_file())
        self.assertEqual(len(list((root / "members" / "0").glob("report_*.json"))), 8)
        self.assertEqual((root / "members" / "0" / "aggregate.json").read_bytes(), (batch_output() / "aggregate.json").read_bytes())
        self.assertEqual(len(list((root / "runs").glob("*.json"))), 16)
        self.assertEqual((root / "KEY.txt").read_text(encoding="utf-8").strip(), manifest["key"])
        text = (root / "bundle.json").read_text(encoding="utf-8")
        for word in ("generated_at", "timestamp", "created"):
            self.assertNotIn(word, text)
        page = (root / "report.html").read_text(encoding="utf-8")
        self.assertIn('"bundle": {"id": "sha256:', page)
        self.assertIn('"run_index"', page)

    def test_level_2_rows_and_level_3_records_of_the_demo(self):
        root = demo_bundle()
        b = bundle.Bundle(root)
        self.assertEqual(len(b.rows), 16)
        row = next(r for r in b.rows if r["key"] == "batch/t01_acme_revenue/atlas-v2/r1")
        self.assertEqual((row["success"], row["steps"], row["tokens"], row["fetches"], row["tool_calls"]), (True, 5, 840, 3, 0))
        self.assertEqual(row["tools"], {"open_page": 1, "select_result": 1, "web_search": 1})
        self.assertEqual(row["cost_usd"], 0.0051)
        self.assertTrue(row["detail"])
        self.assertIn("report", row["basis"])
        record = b.run(row["key"])
        self.assertTrue(record["measurable"])
        self.assertEqual(record["steps_n"], 5)
        self.assertEqual(len(record["steps"]), 5)
        self.assertEqual(record["budget"]["tokens"]["total"], 840)
        self.assertEqual(record["fetches"]["counts"]["total"], 3)
        self.assertEqual(len(record["timeline"]), 5)
        self.assertEqual(len(record["timeline"][0]), 6)
        self.assertEqual(record["timeline"][0][2], "think")
        self.assertIn("rl section", record["reward_basis"])
        self.assertIsNone(b.run("no/such/run/r1"))
        # the steps carry their text, capped and flagged; the data reading rides with the record
        st = record["steps"][3]
        self.assertEqual((st["input_text"], st["input_truncated"], st["output_truncated"]), ("https://ir.acmecorp.com/news/fy2025-results", False, False))
        self.assertEqual(len(st["output_text"]), st["output_chars"])
        self.assertTrue(record["data"]["measurable"])
        self.assertEqual(record["data"]["corpus"]["distinct"], 3)
        self.assertEqual(record["data"]["provenance"]["supported"], 3)
        report = json.loads((root / "members" / "0" / "report_t01_acme_revenue.json").read_text(encoding="utf-8"))
        self.assertEqual(record["data"], report["data"]["a"], "the report's own reading, not a second one")
        full = b.step(row["key"], 3)
        self.assertEqual((full["input"], full["output"]), (report["a"]["steps"][3]["input"], report["a"]["steps"][3]["output"]))
        self.assertEqual(full["source"], "members/0/report_t01_acme_revenue.json#a.steps[3]")
        self.assertEqual(b.data(row["key"]), record["data"])
        self.assertIsNone(b.data("no/such/run/r1"))
        with self.assertRaises(KeyError):
            b.step("no/such/run/r1", 0)
        with self.assertRaises(ValueError):
            b.step(row["key"], 5)
        self.assertEqual(bundle.select_runs(b.rows, sort="tokens", limit=1)[0]["tokens"], max(r["tokens"] for r in b.rows))
        self.assertEqual(len(bundle.select_runs(b.rows, agent="bolt-v3", success=False)), 3)
        with self.assertRaises(ValueError):
            bundle.select_runs(b.rows, sort="bogus")

    def test_level_1_of_the_demo(self):
        o = bundle.Bundle(demo_bundle()).overview
        names = [a["name"] for a in o["agents"]]
        self.assertEqual(names, ["atlas-v2", "bolt-v3"])
        atlas = o["agents"][0]
        self.assertEqual(atlas["success_rate"]["n"], 8)
        self.assertLess(atlas["success_rate"]["lo"], atlas["success_rate"]["rate"])
        self.assertGreater(atlas["success_rate"]["hi"], atlas["success_rate"]["rate"])
        self.assertEqual(atlas["tokens_runs"], 8)
        self.assertFalse(atlas["self_evolving"])
        self.assertEqual(o["lineages"], [])
        self.assertEqual(o["totals"]["runs"], 16)
        self.assertEqual(o["totals"]["synthetic_share"], 0.0)
        self.assertIn("budget", o["sections"])
        self.assertEqual(o["cap"]["source"], "none given")
        self.assertIn("2 agents over 8 tasks and 16 runs", o["reading"])

    def test_a_lineage_member_gives_rows_per_generation_with_synthetic_through(self):
        info = bundle.build([fake_evolve_member()], token_cap=400)
        rows = info["runs"]
        self.assertEqual(len(rows), 10)
        g0 = next(r for r in rows if r["agent"] == "fam@g0")
        self.assertEqual((g0["lineage_gen"], g0["tokens"], g0["fetches"], g0["synthetic"], g0["detail"]), ("g0", None, 3, True, False))
        merged = next(r for r in rows if r["key"] == "fake/t1/fam@g1/r0")
        self.assertEqual((merged["tokens"], merged["repeats"], merged["basis"]), (500, 1, ["evolution", "scorecard"]))
        self.assertIsNone(merged["cost_usd"])      # 0.0 in the scorecard is unrecorded, not free
        record = info["records"][g0["key"]]
        self.assertFalse(record["measurable"])
        self.assertEqual(len(record["timeline"]), 2)
        self.assertFalse(record["data"]["measurable"])
        self.assertEqual(record["data"]["models"], [])
        o = info["overview"]
        self.assertTrue(all(a["self_evolving"] and a["lineage"] == "fam" for a in o["agents"]))
        self.assertEqual(o["agents"][0]["tokens_runs"], 0)
        self.assertIsNone(o["agents"][0]["tokens_total"])
        self.assertEqual(o["lineages"][0]["recommended"], "g1")
        self.assertEqual(o["lineages"][0]["verdicts"], {"improved": 1})
        self.assertEqual(o["cap"], {"value": 400, "source": "--token-cap", "over": ["fake/t1/fam@g1/r0"]})
        self.assertEqual(o["totals"]["synthetic_share"], 1.0)

    def test_no_model_identifier_reaches_the_index(self):
        root = demo_bundle()
        models = set()
        for report in (batch_output()).glob("report_*.json"):
            data = json.loads(report.read_text(encoding="utf-8"))
            for side in ("a", "b"):
                models.add(data[side]["agent"]["model"])
        models.discard("")
        self.assertTrue(models)
        # the index (levels 1 and 2, the manifest) carries no model identifier; a level-3 record carries the
        # trace's own recorded model name in its data reading and nowhere else — it is the trace's data
        index = (root / "bundle.json").read_text(encoding="utf-8")
        for model in models:
            self.assertNotIn(model, index)
        for path in (root / "runs").glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            data = record.pop("data")
            rest = json.dumps(record, ensure_ascii=False)
            for model in models:
                self.assertNotIn(model, rest, path.name)
            self.assertEqual(data["agent"]["model"], data["models"][0]["model"])
            self.assertIn(data["agent"]["model"], models)
            self.assertEqual(data["models"][0]["source"], "trace.agent.model")


class StepTextTest(unittest.TestCase):
    def test_a_step_s_text_is_capped_at_text_cap_and_flagged_with_the_full_length_beside(self):
        from deepcompare.data import TEXT_CAP
        from tests.test_budget import step, trace
        long = "x" * (TEXT_CAP + 7)
        t = trace([step(0, "read", "open", input="short", output=long), step(1, "answer", output="a")])
        rows = bundle._step_rows(t)
        self.assertEqual((rows[0]["input_text"], rows[0]["input_truncated"], rows[0]["input_chars"]), ("short", False, 5))
        self.assertEqual((len(rows[0]["output_text"]), rows[0]["output_truncated"], rows[0]["output_chars"]), (TEXT_CAP, True, TEXT_CAP + 7))
        self.assertEqual(rows[0]["output_text"], long[:TEXT_CAP])


class VerifyTest(unittest.TestCase):
    def test_a_changed_byte_in_a_member_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "b"
            shutil.copytree(demo_bundle(), copy)
            target = copy / "members" / "0" / "aggregate.json"
            data = json.loads(target.read_text(encoding="utf-8"))
            data["tasks"] = data["tasks"] + 1
            target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            check = bundle.verify(copy)
            self.assertFalse(check["match"])
            self.assertNotEqual(check["id"], check["recomputed"])
        with self.assertRaises(ValueError):
            bundle.verify(tempfile.mkdtemp())


class KeyTest(unittest.TestCase):
    def test_the_key_round_trips_and_is_paste_safe(self):
        b = bundle.Bundle(demo_bundle())
        key = b.key
        self.assertTrue(key.startswith(bundle.KEY_PREFIX))
        self.assertLessEqual(len(key), bundle.KEY_MAX_BYTES)
        self.assertTrue(all(33 <= ord(c) <= 126 for c in key))
        self.assertNotIn("\n", key)
        payload = bundle.decode_key(key)
        self.assertEqual(payload["id"], b.id)
        self.assertEqual(payload["name"], "demo")
        self.assertEqual([a["name"] for a in payload["agents"]], ["atlas-v2", "bolt-v3"])
        self.assertEqual(payload["agents"][0]["success_rate"], {"rate": 0.875, "lo": 0.5291, "hi": 0.9776})
        self.assertEqual(payload["truncated"], 0)
        self.assertEqual(payload["locators"], ["file:///tmp/demo"])
        self.assertEqual(payload["totals"]["runs"], 16)
        self.assertEqual(bundle.decode_key("  " + key + "\n"), payload)

    def test_many_agents_are_counted_not_named_and_the_key_stays_under_the_cap(self):
        agents = [{"name": f"agent-with-a-long-name-{i:03d}", "family": None, "framework": None, "runs": 10, "tasks": 3,
                   "success_rate": {"rate": 0.5, "lo": 0.2, "hi": 0.8, "n": 10}, "tokens_total": 12345 * (i + 1),
                   "self_evolving": False} for i in range(60)]
        info = {"id": "sha256:" + "0" * 64, "name": "many", "locators": ["https://example.invalid/" + "x" * 300],
                "overview": {"agents": agents, "lineages": [], "totals": {"runs": 600, "tasks": 3, "agents": 60, "tokens": 1,
                                                                          "cost_usd": None, "seconds": 1.0, "fetches": 1,
                                                                          "synthetic_share": 0.0}}}
        key = bundle.encode_key(info)
        self.assertLessEqual(len(key), bundle.KEY_MAX_BYTES)
        payload = bundle.decode_key(key)
        self.assertLessEqual(len(payload["agents"]), bundle.KEY_AGENTS)
        self.assertEqual(payload["truncated"], 60 - len(payload["agents"]))
        self.assertEqual(payload["agents"][0]["name"], "agent-with-a-long-name-000")

    def test_a_malformed_key_is_refused_with_a_reason(self):
        for text in ("", "hello", "agentdiff1:", "agentdiff1:!!!", "agentdiff1:aGVsbG8", "agentdiff2:aGVsbG8"):
            with self.assertRaises(ValueError) as ctx:
                bundle.decode_key(text)
            self.assertIn("not an AgentDiff key", str(ctx.exception))


class CommandTest(unittest.TestCase):
    def test_bundle_prints_the_id_the_table_the_totals_and_the_key(self):
        from deepcompare.commands import bundle as bundle_cmd
        with tempfile.TemporaryDirectory() as tmp:
            args = parser().parse_args(["bundle", str(batch_output()), "-o", tmp, "--token-cap", "1500"])
            code, out, err = _run(bundle_cmd, args)
            self.assertEqual(code, 0, err)
            self.assertIn("Bundle batch: sha256:", out)
            self.assertIn("batch     ", out)
            self.assertIn("Level 1: 2 agent(s), 8 task(s), 16 run(s)", out)
            self.assertIn("agentdiff1:", out)
            manifest = json.loads((Path(tmp) / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["levels"]["overview"]["cap"]["value"], 1500)
        args = parser().parse_args(["bundle", "/nonexistent/dir", "-o", tempfile.mkdtemp()])
        code, out, err = _run(bundle_cmd, args)
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_key_decodes_verifies_and_re_derives(self):
        root = demo_bundle()
        key = bundle.Bundle(root).key
        code, out, err = _run(key_cmd, _key_parser().parse_args(["key", key]))
        self.assertEqual(code, 0, err)
        self.assertIn("atlas-v2", out)
        self.assertIn("totals: 16 run(s)", out)
        code, out, _ = _run(key_cmd, _key_parser().parse_args(["key", key, "--bundle", str(root)]))
        self.assertEqual(code, 0)
        self.assertIn("match:", out)
        code, out, _ = _run(key_cmd, _key_parser().parse_args(["key", "--from", str(root)]))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(key))
        code, out, err = _run(key_cmd, _key_parser().parse_args(["key", "agentdiff1:nope"]))
        self.assertEqual(code, 2)
        self.assertIn("not an AgentDiff key", err)
        code, _, err = _run(key_cmd, _key_parser().parse_args(["key"]))
        self.assertEqual(code, 2)

    def test_key_mismatch_prints_both_ids_and_exits_1(self):
        root = demo_bundle()
        other = dict(bundle.decode_key(bundle.Bundle(root).key))
        info = {"id": "sha256:" + "f" * 64, "name": "other", "locators": [],
                "overview": {"agents": [], "lineages": [], "totals": other["totals"]}}
        code, out, _ = _run(key_cmd, _key_parser().parse_args(["key", bundle.encode_key(info), "--bundle", str(root)]))
        self.assertEqual(code, 1)
        self.assertIn("mismatch", out)
        self.assertIn("f" * 64, out)
        self.assertIn(bundle.Bundle(root).id, out)


if __name__ == "__main__":
    unittest.main()
