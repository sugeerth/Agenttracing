"""Long horizons: spans from step.span (nested through parent),
subdivisions from the reading's phases split where the agent framed or
decided (never bridging a child span), every node's seconds and wasted
seconds summing from its steps, and a summary naming the sub-agents."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.horizon import blame, delegation_graph, graph_diff, segment  # noqa: E402
from deepcompare.adapters import from_otel_genai  # noqa: E402
from deepcompare.reasoning import read_trace  # noqa: E402
from deepcompare.record import Recorder  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

HZ = ROOT / "demo" / "horizon" / "traces"


def _walk(node):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


class SpanRecordingTest(unittest.TestCase):
    def test_the_recorder_stamps_nested_spans_and_the_schema_round_trips_them(self):
        r = Recorder(task="t", prompt="p", agent="orch", model="m", expected="42", out_dir=None)
        with r:
            r.reason("plan")
            with r.span("researcher"):
                r.step("search", "web_search", "q", "42 found")
                with r.span("reader", span_id="deep"):
                    r.step("read", "open_page", "u", "text 42")
            r.answer("42", success=True)
        d = r.to_dict()
        spans = [s.get("span") for s in d["steps"]]
        self.assertIsNone(spans[0])
        self.assertEqual((spans[1]["agent"], spans[1]["parent"]), ("researcher", None))
        self.assertEqual((spans[2]["id"], spans[2]["agent"], spans[2]["parent"]), ("deep", "reader", spans[1]["id"]))
        self.assertIsNone(spans[3], "after the spans close, the root agent acts again")
        t = Trajectory.from_dict(d)
        self.assertEqual(t.steps[2].span["agent"], "reader")
        self.assertEqual(t.to_dict()["steps"][2]["span"], spans[2])
        bad = dict(d); bad["steps"] = [dict(d["steps"][1], span={"id": "x"})]
        with self.assertRaises(ValueError):
            Trajectory.from_dict(bad)


class SegmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (HZ / "h01_release_report__orbit-v1.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "demo" / "horizon" / "generate_horizon.py")], check=True, capture_output=True)
        cls.a = Trajectory.from_json(HZ / "h01_release_report__orbit-v1.json")
        cls.b = Trajectory.from_json(HZ / "h01_release_report__comet-v2.json")
        cls.report = compare(cls.a, cls.b)

    def test_spans_nest_through_parent_and_every_step_lands_exactly_once(self):
        h = segment(self.b, read_trace(self.b))
        tree = h["tree"]
        self.assertEqual(tree["kind"], "run")
        self.assertEqual(tree["count"], len(self.b.steps))
        steps = [n for n in _walk(tree) if n["kind"] == "step"]
        self.assertEqual(sorted(n["from"] for n in steps), [s.index for s in self.b.steps])
        spans = [n for n in _walk(tree) if n["kind"] == "span"]
        agents = {n["agent"] for n in spans}
        self.assertEqual(agents, {"researcher", "coder", "coder.tests", "verifier"})
        coder = next(n for n in spans if n["agent"] == "coder")
        self.assertTrue(any(c["kind"] == "span" and c["agent"] == "coder.tests" for c in coder["children"]), "the tests span nests inside the coder span")
        self.assertEqual(coder["delegations"], 1)
        self.assertEqual(h["spans"], 1 + len(spans))
        self.assertEqual(sum(a["delegations"] for a in h["agents"]), len(spans))
        self.assertEqual(h["depth"], 5)

    def test_node_totals_sum_from_their_steps_and_a_subdivision_never_bridges_a_child_span(self):
        h = segment(self.a, read_trace(self.a))
        by_index = {s.index: s for s in self.a.steps}
        for n in _walk(h["tree"]):
            if n["kind"] == "step":
                continue
            leaves = [c for c in _walk(n) if c["kind"] == "step"]
            self.assertAlmostEqual(n["seconds"], sum(c["seconds"] for c in leaves), places=3, msg=n["key"])
            self.assertAlmostEqual(n["wasted_s"], sum(c["wasted_s"] for c in leaves), places=3, msg=n["key"])
            self.assertEqual(n["tokens"], sum(by_index[c["from"]].tokens for c in leaves))
            self.assertEqual(n["count"], len(leaves))
            if n["kind"] == "episode":
                idx = [c["from"] for c in n["children"]]
                self.assertEqual(idx, list(range(idx[0], idx[0] + len(idx))), "a subdivision is a contiguous run of one span's own steps")
        self.assertAlmostEqual(h["tree"]["seconds"], h["total_s"], places=3)

    def test_the_summary_names_the_count_the_longest_and_each_sub_agent(self):
        hz = self.report["horizon"]
        for side, traj in (("a", self.a), ("b", self.b)):
            h = hz[side]
            self.assertTrue(h["summary"].startswith(f"{traj.agent.name}: {len(traj.steps)} step(s) in {h['subdivisions']} subdivision(s) across 4 sub-agent(s)"))
            eps = [n for n in _walk(h["tree"]) if n["kind"] == "episode"]
            longest = max(eps, key=lambda e: e["seconds"])
            self.assertIn(f"'{longest['label']}' (steps {longest['from']}–{longest['to']})", h["summary"])
            for a in h["agents"]:
                self.assertIn(f"sub-agent {a['agent']}: {a['delegations']} delegation(s), {a['steps']} step(s)", h["summary"])
        failing = self.report["diagnosis"]["subject"]
        dec = self.report["diagnosis"]["decisive_step"]["step"]
        self.assertTrue(any(n["decisive"] for n in _walk(hz[failing]["tree"]) if n["kind"] == "step" and n["from"] == dec))
        self.assertIn("the decisive step is inside", hz[failing]["summary"])

    def test_a_run_without_spans_is_one_root_span_with_subdivisions(self):
        t = Trajectory.from_json(ROOT / "demo" / "traces" / "t05_flight_duration__bolt-v3.json")
        h = segment(t, read_trace(t))
        self.assertEqual(h["spans"], 1)
        self.assertEqual(h["agents"], [])
        self.assertGreaterEqual(h["subdivisions"], 2)
        self.assertEqual(h["tree"]["count"], len(t.steps))


if __name__ == "__main__":
    unittest.main()


class DelegationGraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = Trajectory.from_json(HZ / "h01_release_report__orbit-v1.json")
        cls.b = Trajectory.from_json(HZ / "h01_release_report__comet-v2.json")
        cls.report = compare(cls.a, cls.b)
        cls.hz = cls.report["horizon"]

    def test_the_graph_has_one_node_per_agent_and_one_edge_per_delegation_pair_with_counts(self):
        g = self.hz["b"]["graph"]
        agents = {n["agent"]: n for n in g["nodes"]}
        self.assertEqual(set(agents), {"comet-v2", "researcher", "coder", "coder.tests", "verifier"})
        self.assertTrue(agents["comet-v2"]["root"])
        self.assertEqual(agents["researcher"]["delegations"], 2, "comet delegated to the researcher twice")
        edges = {(e["from"], e["to"]): e["count"] for e in g["edges"]}
        self.assertEqual(edges[("comet-v2", "researcher")], 2)
        self.assertEqual(edges[("coder", "coder.tests")], 1)
        # a node's steps are its own steps only; seconds sum from them
        spans = [n for n in _walk(self.hz["b"]["tree"]) if n["kind"] == "span" and n["agent"] == "researcher"]
        own = sum(sum(c["count"] for c in sp["children"] if c["kind"] != "span") for sp in spans)
        self.assertEqual(agents["researcher"]["steps"], own)
        self.assertEqual(sum(n["steps"] for n in g["nodes"]), len(self.b.steps))

    def test_blame_names_the_agent_the_delegator_and_the_step(self):
        failing = self.report["diagnosis"]["subject"]
        dec = self.report["diagnosis"]["decisive_step"]["step"]
        bl = self.hz[failing]["blame"]
        self.assertEqual(bl["step"], dec)
        step = next(s for s in self.report[failing]["steps"] if s["index"] == dec)
        want_agent = (step.get("span") or {}).get("agent") or self.report[failing]["agent"]["name"]
        self.assertEqual(bl["agent"], want_agent)
        self.assertEqual(bl["chain"][0], self.report[failing]["agent"]["name"])
        self.assertEqual(bl["depth"], len(bl["chain"]) - 1)
        self.assertTrue(bl["sentence"].startswith(f"responsible agent: {want_agent}"))
        self.assertIsNone(self.hz["a" if failing == "b" else "b"]["blame"], "the passing run has no decisive step")
        # a decisive step inside a sub-agent blames that sub-agent and names its delegator
        h = segment(self.b, read_trace(self.b), decisive_step=next(s.index for s in self.b.steps if s.span and s.span["agent"] == "coder.tests"))
        deep = blame(h)
        self.assertEqual((deep["agent"], deep["delegated_by"], deep["depth"]), ("coder.tests", "coder", 2))

    def test_the_diff_aligns_the_roots_marks_only_one_side_and_uneven_counts(self):
        d = self.hz["diff"]
        by = {n["agent"]: n for n in d["nodes"]}
        self.assertEqual(by["root"]["in"], "both", "two roots under different names are the same role")
        self.assertTrue(all(n["in"] == "both" for n in d["nodes"]))
        self.assertTrue(d["same_agents"])
        self.assertFalse(d["same_shape"])
        uneven = {(e["from"], e["to"]): (e["count_a"], e["count_b"]) for e in d["uneven"]}
        self.assertEqual(uneven, {("root", "researcher"): (1, 2)})
        self.assertIn("root→researcher 1× in orbit-v1 against 2× in comet-v2", self.hz["narrative"])
        self.assertTrue(self.hz["narrative"].startswith("responsible agent:"))
        # a run without a verifier: the node and its edge are only on one side
        gb = delegation_graph(self.hz["b"])
        ga = {"nodes": [n for n in self.hz["a"]["graph"]["nodes"] if n["agent"] != "verifier"],
              "edges": [e for e in self.hz["a"]["graph"]["edges"] if e["to"] != "verifier"]}
        dd = graph_diff(ga, gb, labels=("orbit-v1", "comet-v2"))
        self.assertEqual(dd["only_b"], ["verifier"])
        self.assertEqual([e["in"] for e in dd["edges"] if e["to"] == "verifier"], ["comet-v2"])


class OtelDelegationTest(unittest.TestCase):
    def test_nested_invoke_agent_spans_become_delegation_spans(self):
        spans = [
            {"name": "invoke_agent orchestrator", "span_id": "s0", "attributes": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "orchestrator"}, "start_time_unix_nano": 0, "end_time_unix_nano": 10_000_000_000},
            {"name": "chat m", "span_id": "s1", "parent_span_id": "s0", "attributes": {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}, "start_time_unix_nano": 1, "end_time_unix_nano": 1_000_000_000},
            {"name": "invoke_agent researcher", "span_id": "s2", "parent_span_id": "s0", "attributes": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "researcher"}, "start_time_unix_nano": 2_000_000_000, "end_time_unix_nano": 6_000_000_000},
            {"name": "execute_tool web_search", "span_id": "s3", "parent_span_id": "s2", "attributes": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "web_search", "gen_ai.tool.call.arguments": "q", "gen_ai.tool.call.result": "42"}, "start_time_unix_nano": 3_000_000_000, "end_time_unix_nano": 4_000_000_000},
            {"name": "invoke_agent reader", "spanId": "s4", "parentSpanId": "s2", "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}}, {"key": "gen_ai.agent.name", "value": {"stringValue": "reader"}}], "start_time_unix_nano": 4_500_000_000, "end_time_unix_nano": 5_500_000_000},
            {"name": "execute_tool open_page", "spanId": "s5", "parentSpanId": "s4", "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}}, {"key": "gen_ai.tool.name", "value": {"stringValue": "open_page"}}], "start_time_unix_nano": 4_600_000_000, "end_time_unix_nano": 5_000_000_000},
            {"name": "chat m", "span_id": "s6", "parent_span_id": "s0", "attributes": {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m", "gen_ai.completion": "42"}, "start_time_unix_nano": 7_000_000_000, "end_time_unix_nano": 8_000_000_000},
        ]
        d, warnings = from_otel_genai(spans, agent="orch", task={"id": "t", "prompt": "p", "expected": "42"})
        self.assertEqual(warnings, [])
        spans_of = [s.get("span") for s in d["steps"]]
        self.assertIsNone(spans_of[0], "the root agent's own turn carries no span")
        self.assertIsNone(spans_of[1])
        self.assertEqual((spans_of[2]["agent"], spans_of[2]["parent"]), ("researcher", None))
        self.assertEqual(spans_of[3]["agent"], "researcher")
        self.assertEqual((spans_of[4]["agent"], spans_of[4]["parent"]), ("reader", "s2"))
        self.assertEqual((spans_of[5]["agent"], spans_of[5]["parent"]), ("reader", "s2"))
        self.assertIsNone(spans_of[6], "back in the root agent after the delegation")
        t = Trajectory.from_dict(d)
        h = segment(t, read_trace(t))
        self.assertEqual({a["agent"] for a in h["agents"]}, {"researcher", "reader"})
        g = delegation_graph(h)
        self.assertEqual({(e["from"], e["to"]) for e in g["edges"]}, {("orch", "researcher"), ("researcher", "reader")})
