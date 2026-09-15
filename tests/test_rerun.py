"""Predictable replay: a recording replays from itself step for step, a
different model in the recorded world is caught at its first departure,
the context a model saw is rebuilt exactly, and the CLI turns all of it
into exit codes and CI artifacts — with no network anywhere."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare.harness import ScriptedProvider, Tool, run_task
from deepcompare.harness.cassette import Cassette, CassetteMiss, canonical_args, key_of_step, same_words_grader
from deepcompare.harness.context import context_at, diff as context_diff, messages_before, render, summary
from deepcompare.harness.rerun import diff_runs, rerun, rerun_paths, to_annotations, to_junit, to_markdown

ROOT = Path(__file__).resolve().parents[1]
TASK = {"id": "t_refund", "prompt": "What refund applies to booking BK1?", "expected": "$120.00"}


def refund_tool(amount="$120.00"):
    calls = []

    def get_refund(reference: str):
        calls.append(reference)
        return {"reference": reference, "refund": amount}
    tool = Tool("get_refund", get_refund, "refund lookup",
                {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]},
                effect="read")
    tool.calls = calls  # type: ignore[attr-defined]
    return tool


def echo_model(messages, tools):
    for m in reversed(messages):
        if m["role"] == "tool":
            return {"text": f"The refund for BK1 is {json.loads(m['content'])['refund']}."}
    return {"text": "Looking it up.", "tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK1"}}]}


def curious_model(messages, tools):
    """A different model: it asks for a booking the recording never looked up."""
    seen = [m for m in messages if m["role"] == "tool"]
    if len(seen) >= 2:
        return {"text": "The refund for BK1 is unknown."}
    if seen:
        return {"tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK2"}}]}
    return {"tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK1"}}]}


def recorded_run():
    return run_task(ScriptedProvider(echo_model, model="echo"), TASK, [refund_tool()], out_dir=None)


class CassetteTest(unittest.TestCase):
    def test_a_cassette_holds_every_tool_result_keyed_by_the_call(self):
        trace = recorded_run()
        cassette = Cassette.from_trace(trace)
        self.assertEqual(cassette.recorded_calls, 1)
        self.assertEqual(cassette.names, ["get_refund"])
        hit = cassette.lookup("get_refund", {"reference": "BK1"})
        self.assertIsNotNone(hit)
        self.assertIn("$120.00", str(hit["output"]))
        self.assertIsNone(cassette.lookup("get_refund", {"reference": "BK2"}))
        self.assertEqual([m["args"] for m in cassette.misses], [canonical_args({"reference": "BK2"})])
        # the same call again replays the last result and says so
        again = cassette.lookup("get_refund", {"reference": "BK1"})
        self.assertTrue(cassette.hits[-1]["reused"])
        self.assertEqual(again, hit)
        # round trip through JSON
        back = Cassette.from_dict(json.loads(json.dumps(cassette.to_dict())))
        self.assertEqual(back.entries, cassette.entries)

    def test_raw_inputs_and_rendered_calls_key_the_same_way(self):
        rendered = {"type": "tool_call", "name": "web_search", "input": "web_search(q='acme revenue')", "output": "x"}
        raw = {"type": "search", "name": "web_search", "input": "acme revenue", "output": "x"}
        self.assertEqual(key_of_step(rendered), "web_search\x1f" + canonical_args({"q": "acme revenue"}))
        self.assertEqual(key_of_step(raw), "web_search\x1f" + "acme revenue")
        self.assertNotEqual(key_of_step(rendered), key_of_step(raw))

    def test_served_tools_replay_and_a_miss_is_an_error_or_a_fallback_by_policy(self):
        trace = recorded_run()
        live = refund_tool("$999.00")
        strict = Cassette.from_trace(trace).tools([live], "strict")[0]
        self.assertIn("$120.00", str(strict.fn(reference="BK1")))
        with self.assertRaises(CassetteMiss):
            strict.fn(reference="BK2")
        self.assertEqual(live.calls, [], "the strict policy never runs the real tool")
        empty = Cassette.from_trace(trace).tools([live], "empty")[0]
        self.assertEqual(empty.fn(reference="BK2"), "")
        fallback = Cassette.from_trace(trace).tools([live], "live")[0]
        self.assertIn("$999.00", str(fallback.fn(reference="BK2")))
        self.assertEqual(live.calls, ["BK2"])
        with self.assertRaises(ValueError):
            Cassette.from_trace(trace).tools([live], "guess")


class RerunTest(unittest.TestCase):
    def test_a_harness_recording_replays_from_itself_step_for_step(self):
        trace = recorded_run()
        result = rerun(trace)
        self.assertTrue(result["faithful"], result)
        self.assertIsNone(result["first_divergence"])
        self.assertEqual(result["differences"], [])
        self.assertEqual(result["cassette"]["hits"], 1)
        self.assertEqual(result["cassette"]["misses"], [])
        self.assertEqual(result["mode"], "self")
        self.assertTrue(result["outcome"]["same"])
        self.assertTrue(result["trace"]["agent"]["name"].endswith("-rerun"))
        self.assertEqual(len(result["trace"]["steps"]), len(trace["steps"]))
        self.assertIn("reproduced step for step", result["reading"])

    def test_every_shipped_demo_trace_is_a_replayable_fixture(self):
        paths = sorted((ROOT / "demo" / "traces").glob("*.json")) + sorted((ROOT / "demo" / "horizon" / "traces").glob("*.json"))
        self.assertGreater(len(paths), 4)
        summary = rerun_paths(paths)
        drifted = [r["reading"] for r in summary["results"] if not r["faithful"]]
        self.assertEqual(drifted, [])
        self.assertEqual(summary["faithful"], summary["traces"])

    def test_an_inconsistent_recording_is_caught_and_the_diff_names_the_step(self):
        trace = recorded_run()
        # the answer step on file no longer matches the outcome the recording claims
        tampered = json.loads(json.dumps(trace))
        tampered["steps"][-1]["output"] = "The refund for BK1 is $120.00, probably."
        result = rerun(tampered)
        self.assertFalse(result["faithful"])
        self.assertFalse(result["outcome"]["same"])
        self.assertIn("outcome recorded True, replayed False", result["reading"])
        # the step diff itself: a changed output names the step and the field
        replayed = json.loads(json.dumps(result["trace"]))
        replayed["steps"][-1]["output"] = "something else"
        d = diff_runs(tampered, replayed)
        self.assertFalse(d["faithful"])
        self.assertEqual(d["first_divergence"], len(tampered["steps"]) - 1)
        self.assertEqual(d["differences"][0]["field"], "output")

    def test_a_different_model_in_the_recorded_world_is_caught_at_its_first_departure(self):
        trace = recorded_run()
        live = refund_tool("$999.00")
        result = rerun(trace, provider=ScriptedProvider(curious_model, model="curious"), tools=[live], policy="strict")
        self.assertFalse(result["faithful"])
        self.assertEqual(result["mode"], "provider:scripted-curious")
        self.assertEqual(len(result["cassette"]["misses"]), 1)
        self.assertEqual(result["cassette"]["misses"][0]["args"], canonical_args({"reference": "BK2"}))
        self.assertEqual(live.calls, [], "the recorded world is frozen: no real tool ran")
        self.assertIsNotNone(result["first_divergence"])
        self.assertIn("cassette miss", result["reading"])
        self.assertIn("BK2", result["reading"])
        self.assertEqual(result["grading"], "contains grader against task.expected")
        # the same model with the recording's own decisions reproduces the run
        same = rerun(trace, provider=ScriptedProvider(echo_model, model="echo"), tools=[live])
        self.assertTrue(same["faithful"], same["differences"])
        self.assertEqual(live.calls, [])

    def test_a_call_to_a_tool_the_recording_never_used_is_a_miss_too(self):
        trace = recorded_run()

        def stranger(messages, tools):
            if any(m["role"] == "tool" for m in messages):
                return {"text": "The refund for BK1 is $120.00."}
            return {"tool_calls": [{"name": "git_blame", "arguments": "src/refunds.py"}]}
        result = rerun(trace, provider=ScriptedProvider(stranger, model="stranger"))
        self.assertFalse(result["faithful"])
        miss = result["cassette"]["misses"][0]
        self.assertEqual((miss["name"], miss["args"], miss.get("undeclared")), ("git_blame", "src/refunds.py", True))
        self.assertEqual(result["first_divergence"], 0)
        self.assertIn("git_blame(src/refunds.py)", result["reading"])

    def test_the_diff_ignores_measurements_and_compares_calls_canonically(self):
        trace = recorded_run()
        other = json.loads(json.dumps(trace))
        for s in other["steps"]:
            s["tokens"] = 1
            s["latency_s"] = 9.9
        tool_step = next(s for s in other["steps"] if s["type"] == "tool_call")
        tool_step["type"] = "search"  # a converter's family for the same call
        self.assertTrue(diff_runs(trace, other)["faithful"])
        other["steps"].append(dict(other["steps"][-1]))
        d = diff_runs(trace, other)
        self.assertEqual(d["differences"][-1]["field"], "length")

    def test_self_replay_keeps_the_recorded_verdict_for_the_recorded_answer(self):
        trace = recorded_run()
        trace["outcome"]["success"] = False  # a judge or a human overruled the contains grader
        grade = same_words_grader(trace)
        self.assertFalse(grade(trace["outcome"]["answer"], TASK))
        self.assertTrue(grade("other words", TASK))
        result = rerun(trace)
        self.assertTrue(result["faithful"])
        self.assertEqual(result["outcome"]["replayed"], False)


class ContextTest(unittest.TestCase):
    def test_the_context_before_a_step_is_the_prompt_plus_every_earlier_step(self):
        trace = recorded_run()
        self.assertEqual(len(context_at(trace, 0)), 2)
        at_answer = context_at(trace, len(trace["steps"]) - 1)
        roles = [m["role"] for m in at_answer]
        self.assertEqual(roles[:2], ["system", "user"])
        self.assertIn("tool", roles)
        call = next(m for m in at_answer if m.get("tool_calls"))
        self.assertEqual(call["tool_calls"][0]["name"], "get_refund")
        self.assertEqual(call["tool_calls"][0]["arguments"], {"reference": "BK1"})
        text = render(at_answer)
        self.assertIn("get_refund(reference='BK1')", text)
        self.assertIn("$120.00", text)
        s = summary(trace, len(trace["steps"]) - 1)
        self.assertEqual(s["tool_results"], 1)
        self.assertIn("reconstructed", s["note"])
        with self.assertRaises(ValueError):
            context_at(trace, 99)

    def test_raw_search_steps_become_a_call_and_a_result(self):
        steps = [{"index": 0, "type": "search", "name": "web_search", "input": "acme revenue", "output": "[1] ir.acme.com"}]
        messages = messages_before(steps, "find it", "sys")
        self.assertEqual(messages[2]["tool_calls"][0]["arguments"], {"_raw": "acme revenue"})
        self.assertEqual(messages[3]["content"], "[1] ir.acme.com")
        self.assertIn("web_search('acme revenue')", render(messages))

    def test_two_contexts_diff_where_the_runs_parted(self):
        a = recorded_run()
        b = json.loads(json.dumps(a))
        b["agent"]["name"] = "other"
        tool_step = next(s for s in b["steps"] if s["type"] == "tool_call")
        tool_step["output"] = tool_step["output"].replace("$120.00", "$90.00")
        out = context_diff(a, len(a["steps"]) - 1, b, len(b["steps"]) - 1)
        self.assertIn("--- scripted-echo before step", out)
        self.assertIn("+++ other before step", out)
        self.assertIn("-    " + json.dumps({"reference": "BK1", "refund": "$120.00"}), out)
        self.assertIn("$90.00", out)
        self.assertEqual(context_diff(a, 1, a, 1), "")


class RerunCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "deepcompare", *args], cwd=str(ROOT), capture_output=True, text=True)

    def test_rerun_writes_the_artifacts_and_exits_by_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp) / "traces"
            traces.mkdir()
            good = recorded_run()
            (traces / "good.json").write_text(json.dumps(good), encoding="utf-8")
            bad = json.loads(json.dumps(good))
            bad["trace_id"] = "bad"
            bad["task"] = {"id": "t_refund"}  # no prompt: cannot be replayed
            (traces / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
            out = Path(tmp) / "out"
            proc = self.run_cli("rerun", str(traces), "-o", str(out), "--junit", "--job-summary", "--github-annotations", "--traces")
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("::error file=", proc.stdout)
            self.assertIn("1 reproduced, 1 drifted", proc.stdout)
            summary = json.loads((out / "rerun.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["drifted"], 1)
            junit = (out / "junit.xml").read_text(encoding="utf-8")
            self.assertIn('tests="2" failures="1"', junit)
            self.assertIn("<failure", junit)
            self.assertIn("| trace | steps |", (out / "rerun-summary.md").read_text(encoding="utf-8"))
            self.assertTrue(list((out / "traces").glob("*.json")))
            # identical bytes on a second run: the artifacts carry no timestamps
            first = (out / "junit.xml").read_bytes()
            self.run_cli("rerun", str(traces), "-o", str(out), "--junit", "--no-fail-on-drift")
            self.assertEqual((out / "junit.xml").read_bytes(), first)
            ok = self.run_cli("rerun", str(traces / "good.json"), "-o", str(out / "one"))
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(self.run_cli("rerun", str(traces), "-o", str(out), "--no-fail-on-drift").returncode, 0)

    def test_context_prints_a_step_and_a_row_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = recorded_run()
            path = Path(tmp) / "trace.json"
            path.write_text(json.dumps(trace), encoding="utf-8")
            proc = self.run_cli("context", str(path), "--step", str(len(trace["steps"]) - 1))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("[0] system", proc.stdout)
            self.assertIn("get_refund(reference='BK1')", proc.stdout)
            self.assertEqual(self.run_cli("context", str(path)).returncode, 2)
            # a report: both runs at an aligned row
            a = ROOT / "demo" / "traces" / "t01_acme_revenue__atlas-v2.json"
            b = ROOT / "demo" / "traces" / "t01_acme_revenue__bolt-v3.json"
            report = Path(tmp) / "report_t01.json"
            self.assertEqual(self.run_cli("compare", str(a), str(b), "-o", str(report)).returncode, 0)
            rows = json.loads(report.read_text(encoding="utf-8"))["alignment"]
            row = next(i for i, r in enumerate(rows) if r.get("a_index") is not None and r.get("b_index") is not None)
            proc = self.run_cli("context", str(report), "--row", str(row), "--diff-only")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("=== diff", proc.stdout)
            self.assertIn("before step", proc.stdout)


if __name__ == "__main__":
    unittest.main()


class LongHorizonRerunTest(unittest.TestCase):
    """A long run replays by segment: from a checkpoint, until a step, or
    a sub-agent's span; the drift map says which sub-agents and parts
    reproduced; milestones are read like with like; a checkpoint bundle
    resumes; and hundreds of steps replay in well under a second."""

    @classmethod
    def setUpClass(cls):
        from deepcompare.scorecard import load_golden
        cls.golden = load_golden(ROOT / "demo" / "horizon" / "golden.json")
        cls.ms = cls.golden["tasks"]["h02_migrate_service"]["milestones"]
        cls.bad = json.loads((ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__comet-lh.json").read_text(encoding="utf-8"))
        cls.good = json.loads((ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json").read_text(encoding="utf-8"))

    def test_the_long_demo_replays_whole_and_fast(self):
        import time
        t0 = time.time()
        result = rerun(self.bad, milestones=self.ms)
        self.assertLess(time.time() - t0, 3.0)
        self.assertTrue(result["faithful"], result["reading"])
        self.assertEqual(result["scope"]["steps"], len(self.bad["steps"]))
        self.assertGreater(result["cassette"]["hits"], 300)
        spans = [n for n in result["drift_map"] if n["kind"] == "span"]
        self.assertGreater(len(spans), 15)
        self.assertTrue(all(n["reproduced"] for n in spans))
        self.assertTrue(result["milestones"]["same"])
        self.assertEqual(result["milestones"]["recorded"]["reached"], 7)

    def test_a_sub_agent_replays_on_its_own_and_the_scope_is_named(self):
        from deepcompare.harness.rerun import span_range
        lo, hi = span_range(self.bad, "migrator-ledger")
        self.assertLess(lo, hi)
        self.assertTrue(all((s.get("span") or {}).get("agent", "").startswith("migrator-ledger") for s in self.bad["steps"][lo:hi + 1]))
        result = rerun(self.bad, span="migrator-ledger", milestones=self.ms)
        self.assertTrue(result["faithful"], result["reading"])
        self.assertEqual((result["scope"]["from"], result["scope"]["until"], result["scope"]["span"]), (lo, hi, "migrator-ledger"))
        self.assertIn(f"steps {lo}–{hi} (sub-agent migrator-ledger)", result["reading"])
        self.assertEqual(result["trace"]["outcome"]["termination"], "user_stop")
        # like with like: milestones after the scope are not "lost"
        self.assertTrue(result["milestones"]["same"], result["milestones"])
        with self.assertRaises(ValueError):
            span_range(self.bad, "nobody")
        with self.assertRaises(ValueError):
            rerun(self.bad, from_step=10_000)

    def test_a_different_model_from_a_checkpoint_is_placed_by_sub_agent(self):
        from deepcompare.harness.rerun import span_range
        lo, hi = span_range(self.bad, "migrator-ledger")

        def impatient(messages, tools):
            return {"tool_calls": [{"name": "run_tests", "arguments": "pytest -q tests/everything"}]}
        result = rerun(self.bad, provider=ScriptedProvider(impatient, model="impatient"), from_step=lo, until=lo + 4, milestones=self.ms)
        self.assertFalse(result["faithful"])
        self.assertEqual(result["scope"]["from"], lo)
        # the prefix is the recording, verbatim
        # the scoped budget bounds the replay: at most five turns after the prefix, plus the recorder's closing answer
        self.assertTrue(lo < len(result["trace"]["steps"]) <= lo + 6, len(result["trace"]["steps"]))
        for i in range(lo):
            self.assertEqual(result["trace"]["steps"][i]["input"], self.bad["steps"][i]["input"])
        self.assertTrue(result["cassette"]["misses"])
        self.assertGreaterEqual(result["first_divergence"], lo)
        drifted = [n for n in result["drift_map"] if n["reproduced"] is False]
        self.assertIn("migrator-ledger", [n["agent"] for n in drifted if n["kind"] == "span"])
        self.assertIn("first inside sub-agent migrator-ledger", result["reading"])
        # every node outside the scope is not judged
        self.assertTrue(all(n["reproduced"] is None for n in result["drift_map"] if n["to"] < lo))

    def test_the_drift_map_covers_the_tree_and_the_markdown_lists_drifted_sub_agents(self):
        from deepcompare.harness.rerun import diff_runs, drift_map, to_markdown
        result = rerun(self.good)
        rows = result["drift_map"]
        self.assertEqual(rows[0]["kind"], "run")
        self.assertEqual((rows[0]["from"], rows[0]["to"]), (0, len(self.good["steps"]) - 1))
        self.assertEqual({n["depth"] for n in rows if n["kind"] == "span"} & {1}, {1})
        # a forged difference inside one sub-agent marks that sub-agent and the run, nothing else
        forged = dict(result, differences=[{"step": rows[1]["from"], "field": "output", "recorded": "a", "replayed": "b"}], faithful=False)
        rows2 = drift_map(self.good, forged)
        bad = [n for n in rows2 if n["reproduced"] is False]
        self.assertIn("run", [n["kind"] for n in bad])
        self.assertEqual({n["agent"] for n in bad if n["kind"] == "span"}, {rows[1]["agent"]})
        md = to_markdown({"traces": 1, "faithful": 0, "drifted": 1, "mode": "self", "results": [dict(forged, drift_map=rows2, trace_id="x", reading="r", steps={"recorded": 1, "replayed": 1}, cassette={"hits": 0, "misses": []})]})
        self.assertIn("Sub-agents that did not reproduce", md)
        self.assertIn(rows[1]["agent"], md)

    def test_checkpoint_bundle_then_resume_from_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ckpt"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "checkpoint", str(ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__comet-lh.json"),
                                   "--step", "340", "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "-o", str(out)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Checkpoint at step 340 of", proc.stdout)
            self.assertIn("milestones reached so far: 5", proc.stdout)
            for name in ("prefix.json", "cassette.json", "context.txt", "checkpoint.json"):
                self.assertTrue((out / name).is_file(), name)
            prefix = json.loads((out / "prefix.json").read_text(encoding="utf-8"))
            self.assertEqual(len(prefix["steps"]), 340)
            self.assertEqual(prefix["outcome"]["termination"], "user_stop")
            summary = json.loads((out / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual((summary["step"], summary["span"]["agent"]), (340, "migrator-ledger"))
            self.assertIn("[0] system", (out / "context.txt").read_text(encoding="utf-8"))
            # resume: the prefix from the recording, the segment served from the bundle's cassette
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "rerun", str(ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__comet-lh.json"),
                                   "--from", "340", "--until", "380", "--cassette", str(out / "cassette.json"), "--golden", str(ROOT / "demo" / "horizon" / "golden.json"),
                                   "-o", str(Path(tmp) / "rr")], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("steps 340–380 of", proc.stdout)
            self.assertIn("1 reproduced, 0 drifted", proc.stdout)
            bad = subprocess.run([sys.executable, "-m", "deepcompare", "checkpoint", str(ROOT / "demo" / "traces" / "t01_acme_revenue__atlas-v2.json"),
                                  "--step", "99", "-o", str(Path(tmp) / "x")], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)

    def test_span_from_the_cli_names_the_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "rerun", str(ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json"),
                                   "--span", "reviewer", "-o", tmp, "--job-summary"], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("(sub-agent reviewer)", proc.stdout)
            summary = json.loads((Path(tmp) / "rerun.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["results"][0]["scope"]["span"], "reviewer")
            missing = subprocess.run([sys.executable, "-m", "deepcompare", "rerun", str(ROOT / "demo" / "horizon" / "long" / "h02_migrate_service__atlas-lh.json"),
                                      "--span", "nobody", "-o", tmp], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("no span 'nobody'", (Path(tmp) / "rerun.json").read_text(encoding="utf-8"))

    def test_thousands_of_steps_replay_in_seconds(self):
        import time
        steps = []
        for i in range(3000):
            k = i % 3
            if k == 0:
                steps.append({"index": i, "type": "reason", "name": "reason", "input": f"think {i}", "output": "", "latency_s": 0.1, "span": {"id": f"s{i // 300}", "agent": f"worker-{i // 300}"}})
            elif k == 1:
                steps.append({"index": i, "type": "tool_call", "name": "probe", "input": f"probe(n={i})", "output": f"value {i}", "latency_s": 0.2, "span": {"id": f"s{i // 300}", "agent": f"worker-{i // 300}"}})
            else:
                steps.append({"index": i, "type": "read", "name": "read_file", "input": f"file{i}.txt", "output": f"contents {i}", "latency_s": 0.05, "span": {"id": f"s{i // 300}", "agent": f"worker-{i // 300}"}})
        steps.append({"index": 3000, "type": "answer", "name": "final", "input": "done", "output": "done: 3000 steps", "latency_s": 0.1})
        trace = {"schema_version": 1, "trace_id": "big__w", "agent": {"name": "w", "model": "m"}, "task": {"id": "big", "prompt": "do it", "expected": "3000 steps"},
                 "outcome": {"success": True, "answer": "done: 3000 steps", "termination": "agent_stop"},
                 "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 350.0}, "steps": steps}
        t0 = time.time()
        result = rerun(trace)
        elapsed = time.time() - t0
        self.assertTrue(result["faithful"], result["reading"])
        self.assertEqual(result["cassette"]["hits"], 2000)
        self.assertLess(elapsed, 8.0, f"{elapsed:.1f}s")
        self.assertEqual(len([n for n in result["drift_map"] if n["kind"] == "span"]), 10)
