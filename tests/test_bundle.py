"""The bundle, its id and its key.

What this pins: an output directory reads as a member of the right
kind; the same inputs give the same bytes and the same id, in any
directory; verify recomputes the id and notices a changed byte; the
level-2 rows carry every run with numbers only where a source recorded
them; the level-3 record holds the steps, the budget, the fetches and
the timeline in the timescape's shape; a lineage's episodes become rows
with their generation and SYNTHETIC label; with ``--traces`` every
record whose steps the output did not keep is completed from its trace
(matched by trace id, else by task, agent and run), the trace copied
into the bundle, and the demo's 324 runs all hold level 3, while
without it every byte is as before; the key round-trips, stays
printable ASCII on one line under its size cap, truncates by counting
and refuses a malformed text with a reason; the commands' exit codes;
no timestamp and no model identifier anywhere in the index.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import bundle  # noqa: E402
from deepcompare.commands import key as key_cmd  # noqa: E402
from deepcompare.commands.paths import DEFAULT_TEMPLATE  # noqa: E402
from tests.helpers_bundle import BATCH, LINEAGE, TRAIN, batch_output, demo_bundle, demo_outputs, full_bundle, parser  # noqa: E402


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
        self.assertEqual(len(m["tasks"]), 9)
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
            self.assertEqual(bundle.verify(one), {"id": a["id"], "recomputed": a["id"], "match": True,
                                                  "records_digest": a["records_digest"], "records_recomputed": a["records_digest"],
                                                  "records_match": True, "records_reason": None})
            self.assertEqual(a["records_digest"], b["records_digest"])
            self.assertTrue(a["records_digest"].startswith("sha256:") and a["records_digest"] != a["id"])

    def test_rewriting_into_an_existing_bundle_directory_leaves_nothing_stale(self):
        # finding 1: members/, runs/ and traces/ were only added to, so a second write kept stale members and records
        # and the bundle it had just written no longer verified
        with tempfile.TemporaryDirectory() as tmp:
            small_src = Path(tmp) / "batch"   # the batch output minus four of its eight reports
            small_src.mkdir()
            shutil.copyfile(batch_output() / "aggregate.json", small_src / "aggregate.json")
            kept = sorted(batch_output().glob("report_*.json"))[:4]
            for report in kept:
                shutil.copyfile(report, small_src / report.name)
            out = Path(tmp) / "x"
            bundle.write_bundle([bundle.read_member(batch_output())], out, DEFAULT_TEMPLATE, name="demo")
            (out / "traces" / "old").mkdir(parents=True)
            (out / "traces" / "old" / "stale.json").write_text("{}", encoding="utf-8")
            self.assertEqual(len(list((out / "runs").glob("*.json"))), 18)
            info = bundle.write_bundle([bundle.read_member(small_src)], out, DEFAULT_TEMPLATE, name="demo")
            check = bundle.verify(out)
            self.assertTrue(check["match"], check)
            self.assertTrue(check["records_match"], check)
            self.assertEqual(sorted(p.name for p in (out / "members" / "0").glob("*.json")),
                             ["aggregate.json"] + [r.name for r in kept])
            self.assertEqual(len(list((out / "runs").glob("*.json"))), len(info["run_index"]))
            self.assertFalse((out / "traces").exists(), "a stale traces/ directory is cleared too")
            self.assertEqual(sorted(_files(out)), sorted(info["files"]), "every file on disk is one the manifest wrote")

    def test_the_demo_bundle_has_every_file_and_no_timestamp(self):
        root = demo_bundle()
        manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["name"], "demo")
        self.assertEqual(manifest["locators"], ["file:///tmp/demo"])
        self.assertEqual([m["kind"] for m in manifest["members"]], ["batch"])
        self.assertTrue((root / "members" / "0" / "aggregate.json").is_file())
        self.assertEqual(len(list((root / "members" / "0").glob("report_*.json"))), 9)
        self.assertEqual((root / "members" / "0" / "aggregate.json").read_bytes(), (batch_output() / "aggregate.json").read_bytes())
        self.assertEqual(len(list((root / "runs").glob("*.json"))), 18)
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
        self.assertEqual(len(b.rows), 18)
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
        self.assertEqual(atlas["success_rate"]["n"], 9)
        self.assertLess(atlas["success_rate"]["lo"], atlas["success_rate"]["rate"])
        self.assertGreater(atlas["success_rate"]["hi"], atlas["success_rate"]["rate"])
        self.assertEqual(atlas["tokens_runs"], 9)
        self.assertFalse(atlas["self_evolving"])
        self.assertEqual(o["lineages"], [])
        self.assertEqual(o["totals"]["runs"], 18)
        self.assertEqual(o["totals"]["synthetic_share"], 0.0)
        self.assertIn("budget", o["sections"])
        self.assertEqual(o["cap"]["source"], "none given")
        self.assertIn("2 agents over 9 tasks and 18 runs", o["reading"])

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
            # the demo's plan, reason and answer steps carry their model as telemetry, so the first row is theirs
            self.assertEqual(data["models"][0]["source"], "steps[].model")


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


class TracesTest(unittest.TestCase):
    """``--traces``: the loader, the matching, the completed record, and
    nothing changed without it."""

    def test_load_traces_reads_every_demo_layout_and_passes_over_what_is_not_a_trace(self):
        entries, notes = bundle.load_traces([BATCH])
        self.assertEqual((len(entries), notes), (18, []))
        self.assertTrue(all(e["trajectory"].run_id == "r1" and e["root"] == BATCH for e in entries))
        entries, _ = bundle.load_traces([TRAIN])
        self.assertEqual(len(entries), 96)
        e = next(e for e in entries if e["rel"] == "rl01_ledger_reconcile__policy-v1__r2.json")
        self.assertEqual((e["trajectory"].run_id, e["trajectory"].trace_id, e["trajectory"].agent.name, e["trajectory"].harness["adapter"]),
                         ("r2", "rl01_ledger_reconcile-policy-v1-r2", "policy-v1", "synthetic"))
        entries, _ = bundle.load_traces([LINEAGE])
        self.assertEqual(len(entries), 210)
        self.assertEqual(entries[0]["rel"], "g0/traces/rl01_ledger_reconcile__ledger-agent@g0__r1.json")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "not_a_trace.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
            (root / "bad__x__r1.json").write_text(json.dumps({"task": {"id": "t"}, "steps": []}), encoding="utf-8")
            from tests.test_budget import step, trace
            good = trace([step(0, "plan"), step(1, "answer")], agent="x", task="t", run_id="r1")
            (root / "sub").mkdir()
            (root / "sub" / "t__x__r7.json").write_text(json.dumps({
                "schema_version": 1, "trace_id": good.trace_id, "run_id": "r1", "agent": {"name": "x", "model": "", "version": ""},
                "task": {"id": "t", "prompt": "p"}, "outcome": {"success": True, "answer": "a"}, "totals": {},
                "steps": [s.to_dict() for s in good.steps]}), encoding="utf-8")
            entries, notes = bundle.load_traces([root])
            self.assertEqual(len(entries), 1)
            self.assertEqual((entries[0]["rel"], entries[0]["trajectory"].run_id), ("sub/t__x__r7.json", "r7"), "the name's run id wins")
            self.assertEqual(len(notes), 1)
            self.assertIn("bad__x__r1.json", notes[0])
        with self.assertRaises(ValueError):
            bundle.load_traces(["/nonexistent/traces"])

    def test_a_lineage_member_s_earlier_generation_is_completed_from_its_traces(self):
        from tests.test_budget import step, trace
        member = fake_evolve_member()

        def write(root: Path, name: str, trace_id: str, run_id: str) -> None:
            t = trace([step(0, "plan", tokens=40, basis="measured", latency_s=1.0),
                       step(1, "tool_call", "grep", tokens=60, basis="measured", latency_s=2.5, reward=-0.1),
                       step(2, "answer", tokens=20, basis="measured")], agent="fam@g0", task="t1", run_id=run_id)
            (root / name).write_text(json.dumps({
                "schema_version": 1, "trace_id": trace_id, "run_id": run_id, "agent": {"name": "fam@g0", "model": "sim-x", "version": "g0"},
                "task": {"id": "t1", "prompt": "p"}, "outcome": {"success": True, "answer": "a"}, "totals": {"tokens": 120},
                "harness": {"adapter": "synthetic", "note": "SYNTHETIC: a test"},
                "steps": [s.to_dict() for s in t.steps]}), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "by_id.json", "t1-r0", "r9")               # matched by trace id, though its run id says r9
            write(root, "t1__fam@g0__r1.json", "some-other-id", "r1")  # matched by task, agent and run
            plain = bundle.build([member])
            info = bundle.build([member], traces=[root])
            self.assertEqual(bundle.build([member], traces=[])["records"], plain["records"], "no traces: nothing changes")
            self.assertEqual(info["id"], plain["id"], "the id is the members' content")
            self.assertEqual({k: v for k, v in info["traces"].items() if k != "files"},
                             {"dirs": [str(root)], "read": 2, "notes": [], "completed": 2})
            self.assertEqual([f["rel"] for f in info["traces"]["files"]], ["traces/fake/by_id.json", "traces/fake/t1__fam@g0__r1.json"])
            r0 = info["records"]["fake/t1/fam@g0/r0"]
            self.assertTrue(r0["measurable"])
            self.assertEqual((r0["steps_source"], r0["trace_path"], r0["trace_id"], r0["report"], r0["side"]),
                             ("trace traces/fake/by_id.json", "traces/fake/by_id.json", "t1-r0", None, None))
            self.assertEqual(len(r0["steps"]), 3)
            self.assertEqual(r0["steps"][1]["input_text"], "in1")
            self.assertEqual(r0["budget"]["tokens"]["total"], 120)
            self.assertEqual(r0["fetches"]["counts"]["total"], 1)
            self.assertTrue(r0["data"]["measurable"])
            self.assertEqual(r0["timeline"], plain["records"]["fake/t1/fam@g0/r0"]["timeline"], "the episode's timeline is kept")
            self.assertEqual(r0["reward_basis"], "the lineage's episode timeline")
            self.assertEqual((r0["tokens"], r0["tokens_measured_share"], r0["fetches_n"], r0["steps_n"], r0["seconds"]),
                             (120, 1.0, 3, 10, 3.5), "the episode's own numbers are kept; only what it lacked is filled")
            self.assertEqual((r0["detail"], r0["basis"], r0["synthetic"]), (True, ["evolution", "trace"], True))
            r1 = info["records"]["fake/t1/fam@g0/r1"]
            self.assertEqual(r1["steps_source"], "trace traces/fake/t1__fam@g0__r1.json")
            r2 = info["records"]["fake/t1/fam@g0/r2"]
            self.assertEqual((r2["measurable"], r2["steps"], r2.get("steps_source")), (False, [], None))
            row = next(r for r in info["runs"] if r["key"] == "fake/t1/fam@g0/r2")
            self.assertEqual((row["detail"], row["basis"]), (False, ["evolution"]))
            g0 = next(a for a in info["overview"]["agents"] if a["name"] == "fam@g0")
            self.assertEqual((g0["tokens_runs"], g0["tokens_total"]), (2, 240))

    def test_traces_that_complete_nothing_leave_the_batch_bundle_byte_identical(self):
        members = [bundle.read_member(batch_output())]
        with tempfile.TemporaryDirectory() as one:
            info = bundle.write_bundle(members, one, DEFAULT_TEMPLATE, name="demo", locators=["file:///tmp/demo"], traces=[BATCH])
            self.assertEqual((info["traces"]["read"], info["traces"]["completed"], info["traces"]["files"]), (18, 0, []))
            self.assertEqual(_files(Path(one)), _files(demo_bundle()))

    def test_the_demo_bundle_with_traces_has_level_3_for_all_324_runs(self):
        root = full_bundle()
        b = bundle.Bundle(root)
        outputs = demo_outputs()
        self.assertEqual(b.id, bundle.digest([bundle.read_member(outputs[k]) for k in ("batch", "runs", "coevolve")]))
        self.assertEqual([m["runs"] for m in b.members], [18, 96, 210])
        self.assertEqual(len(b.rows), 324)
        records = {key: b.run(key) for key in b.run_index}
        self.assertEqual(len(records), 324)
        self.assertTrue(all(r["measurable"] and r["steps"] for r in records.values()))
        self.assertEqual(sum(1 for r in records.values() if r.get("steps_source")), 282)
        self.assertEqual(sum(1 for r in records.values() if r.get("report")), 42, "the reports' 9 + 6 + 6 pairs")
        self.assertTrue(all(r["detail"] for r in b.rows))
        self.assertEqual(sum(1 for r in b.rows if "trace" in r["basis"]), 282)
        t = b.overview["totals"]
        self.assertEqual((t["runs"], t["tokens_runs"], t["fetches_runs"], t["cost_runs"]), (324, 324, 324, 18))
        self.assertTrue(all(a["tokens_runs"] == a["runs"] == a["fetches_runs"] for a in b.overview["agents"]))
        self.assertEqual(len(list((root / "traces").rglob("*.json"))), 282)
        self.assertTrue((root / "traces" / "coevolve" / "g0" / "traces" / "rl01_ledger_reconcile__ledger-agent@g0__r1.json").is_file())
        self.assertTrue((root / "traces" / "runs" / "rl01_ledger_reconcile__policy-v1__r2.json").is_file())
        self.assertFalse((root / "traces" / "batch").exists(), "every batch run has its report")
        # a completed record: the lineage's timeline kept, the steps from the trace, the step tool reading the copy
        key = "coevolve/rl01_ledger_reconcile/ledger-agent@g0/r1"
        rec = records[key]
        self.assertEqual(rec["steps_source"], "trace traces/coevolve/g0/traces/rl01_ledger_reconcile__ledger-agent@g0__r1.json")
        self.assertEqual((rec["lineage_gen"], rec["reward_basis"], rec["synthetic"]), ("g0", "the lineage's episode timeline", True))
        self.assertEqual(len(rec["steps"]), rec["steps_n"])
        self.assertTrue(rec["data"]["measurable"])
        self.assertIn(rec["data"]["models"][0]["source"], ("steps[].model", "trace.agent.model"))
        source = json.loads((LINEAGE / "g0" / "traces" / "rl01_ledger_reconcile__ledger-agent@g0__r1.json").read_text(encoding="utf-8"))
        full = b.step(key, 1)
        self.assertEqual((full["input"], full["output"]), (source["steps"][1]["input"], source["steps"][1]["output"]))
        self.assertEqual(full["source"], "traces/coevolve/g0/traces/rl01_ledger_reconcile__ledger-agent@g0__r1.json#steps[1]")
        with self.assertRaises(ValueError):
            b.step(key, len(source["steps"]))
        run_key = "runs/rl01_ledger_reconcile/policy-v1/r2"
        self.assertEqual(records[run_key]["reward_basis"], "steps as recorded (null where no reward was recorded)")
        self.assertEqual(records[run_key]["timeline"][1][4], -0.1)
        # the MCP tools and the HTTP routes read the completed records as they are
        from deepcompare import mcpserver
        from deepcompare.harness.serve import route
        server = mcpserver.Server(b)
        self.assertEqual(server.call("step", {"key": key, "index": 1}), full)
        self.assertTrue(server.call("run", {"key": key})["measurable"])
        self.assertEqual(server.call("data", {"key": key}), rec["data"])
        self.assertEqual(route(b, f"/api/v1/runs/{key}/steps/1", {}), (200, full))
        status, payload = route(b, f"/api/v1/runs/{key}", {})
        self.assertEqual((status, payload["steps_source"]), (200, rec["steps_source"]))


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

    def test_a_changed_run_record_or_trace_copy_is_a_records_mismatch_the_id_does_not_see(self):
        # improvement 2: the id covers the members' content only; a tampered runs/<key>.json used to verify clean
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "b"
            shutil.copytree(demo_bundle(), copy)
            manifest = json.loads((copy / "bundle.json").read_text(encoding="utf-8"))
            intact = bundle.verify(copy)
            self.assertEqual((intact["match"], intact["records_match"], intact["records_reason"]), (True, True, None))
            self.assertEqual(intact["records_digest"], manifest["records_digest"])
            self.assertEqual(intact["records_recomputed"], manifest["records_digest"])
            rel = next(iter(manifest["levels"]["run_index"].values()))
            target = copy / rel
            original = target.read_text(encoding="utf-8")
            target.write_text(original.replace('"tokens":', '"tokens_TAMPERED":', 1), encoding="utf-8")
            check = bundle.verify(copy)
            self.assertTrue(check["match"], "the id still matches: the members are untouched")
            self.assertFalse(check["records_match"])
            self.assertIn("differs from what the manifest recorded", check["records_reason"])
            self.assertNotEqual(check["records_recomputed"], check["records_digest"])
            target.unlink()
            check = bundle.verify(copy)
            self.assertFalse(check["records_match"])
            self.assertIn(f"missing from the bundle: {rel}", check["records_reason"])
            target.write_text(original, encoding="utf-8")
            self.assertTrue(bundle.verify(copy)["records_match"])
            del manifest["records_digest"]
            (copy / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            check = bundle.verify(copy)
            self.assertEqual((check["records_digest"], check["records_match"]), (None, False))
            self.assertEqual(check["records_reason"], "the manifest carries no records_digest")
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "full"
            shutil.copytree(full_bundle(), copy)
            trace = next(p for p in sorted((copy / "traces").rglob("*.json")))
            self.assertTrue(bundle.verify(copy)["records_match"])
            trace.write_bytes(trace.read_bytes() + b"\n")
            check = bundle.verify(copy)
            self.assertTrue(check["match"])
            self.assertFalse(check["records_match"], "a copied trace is under the records digest")


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
        self.assertEqual(payload["agents"][0]["success_rate"], {"rate": 0.8889, "lo": 0.565, "hi": 0.9801})
        self.assertEqual(payload["truncated"], 0)
        self.assertEqual(payload["locators"], ["file:///tmp/demo"])
        self.assertEqual(payload["totals"]["runs"], 18)
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

    def test_a_tampered_key_that_still_decodes_is_refused_with_a_reason_and_the_decode_is_capped(self):
        # finding 5: decode_key checked only v and id, and `agentdiff key` crashed on the fields it then read
        def key_of(payload):
            return bundle.KEY_PREFIX + base64.urlsafe_b64encode(zlib.compress(bundle.canonical(payload), 9)).decode().rstrip("=")

        cases = {"agents[0] is not an object with a name": {"v": 1, "id": "sha256:x", "agents": [{"runs": 1}]},
                 "agents[0].runs is not an integer": {"v": 1, "id": "sha256:x", "agents": [{"name": "a", "runs": "1"}]},
                 "agents is not a list": {"v": 1, "id": "sha256:x", "agents": "nope"},
                 "lineages[0] is not an object with a family": {"v": 1, "id": "sha256:x", "lineages": [{"generations": 2}]},
                 "lineages[0].eval_adopted is not a list of metric ids": {"v": 1, "id": "sha256:x", "lineages": [{"family": "f", "generations": 2, "eval_adopted": "x"}]},
                 "truncated is not a count": {"v": 1, "id": "sha256:x", "truncated": "many"},
                 "totals is not an object": {"v": 1, "id": "sha256:x", "totals": [1]},
                 "locators is not a list of strings": {"v": 1, "id": "sha256:x", "locators": [1]},
                 "name is not a string": {"v": 1, "id": "sha256:x", "name": 3}}
        for reason, payload in cases.items():
            with self.assertRaises(ValueError) as ctx:
                bundle.decode_key(key_of(payload))
            self.assertEqual(str(ctx.exception), f"not an AgentDiff key: {reason}")
            code, _, err = _run(key_cmd, _key_parser().parse_args(["key", key_of(payload)]))
            self.assertEqual(code, 2, reason)
            self.assertIn(reason, err)
        minimal = {"v": 1, "id": "sha256:x", "name": "n", "agents": [], "truncated": 0, "lineages": [], "totals": {}, "locators": []}
        self.assertEqual(bundle.decode_key(key_of(minimal)), minimal)
        big = key_of({"v": 1, "id": "x", "pad": "0" * 3_000_000})
        with self.assertRaises(ValueError) as ctx:
            bundle.decode_key(big)
        self.assertIn(f"over the {bundle.KEY_MAX_BYTES} a key can be", str(ctx.exception))
        bomb = key_of({"v": 1, "id": "x", "pad": "0" * 400_000})   # a few hundred bytes of text, 400 KB once inflated
        self.assertLessEqual(len(bomb), bundle.KEY_MAX_BYTES)
        with self.assertRaises(ValueError) as ctx:
            bundle.decode_key(bomb)
        self.assertIn(f"decompresses past {bundle.KEY_DECODED_MAX} bytes", str(ctx.exception))


class CommandTest(unittest.TestCase):
    def test_bundle_prints_the_id_the_table_the_totals_and_the_key(self):
        from deepcompare.commands import bundle as bundle_cmd
        with tempfile.TemporaryDirectory() as tmp:
            args = parser().parse_args(["bundle", str(batch_output()), "-o", tmp, "--token-cap", "1500"])
            code, out, err = _run(bundle_cmd, args)
            self.assertEqual(code, 0, err)
            self.assertIn("Bundle batch: sha256:", out)
            self.assertIn("batch     ", out)
            self.assertIn("Level 1: 2 agent(s), 9 task(s), 18 run(s)", out)
            self.assertIn("agentdiff1:", out)
            self.assertNotIn("Traces:", out)
            manifest = json.loads((Path(tmp) / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["levels"]["overview"]["cap"]["value"], 1500)
        args = parser().parse_args(["bundle", "/nonexistent/dir", "-o", tempfile.mkdtemp()])
        code, out, err = _run(bundle_cmd, args)
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        args = parser().parse_args(["bundle", str(batch_output()), "-o", tempfile.mkdtemp(), "--traces", "/nonexistent/traces"])
        code, out, err = _run(bundle_cmd, args)
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)
        with tempfile.TemporaryDirectory() as tmp:
            args = parser().parse_args(["bundle", str(batch_output()), "-o", tmp, "--traces", str(BATCH)])
            code, out, err = _run(bundle_cmd, args)
            self.assertEqual(code, 0, err)
            self.assertIn("Traces: 18 read under 1 dir(s); 0 record(s) completed from 0 file(s)", out)
            self.assertIn("18 of 18 record(s) hold their steps", out)

    def test_key_decodes_verifies_and_re_derives(self):
        root = demo_bundle()
        key = bundle.Bundle(root).key
        code, out, err = _run(key_cmd, _key_parser().parse_args(["key", key]))
        self.assertEqual(code, 0, err)
        self.assertIn("atlas-v2", out)
        self.assertIn("totals: 18 run(s)", out)
        code, out, _ = _run(key_cmd, _key_parser().parse_args(["key", key, "--bundle", str(root)]))
        self.assertEqual(code, 0)
        self.assertIn("match:", out)
        self.assertIn("records: match", out)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "b"
            shutil.copytree(root, copy)
            rel = next(iter(bundle.Bundle(copy).run_index.values()))
            (copy / rel).write_text((copy / rel).read_text(encoding="utf-8").replace('"tokens":', '"t":', 1), encoding="utf-8")
            code, out, _ = _run(key_cmd, _key_parser().parse_args(["key", key, "--bundle", str(copy)]))
            self.assertEqual(code, 1, "the id matches, the records do not: exit 1")
            self.assertIn("match:", out)
            self.assertIn("records: mismatch", out)
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
