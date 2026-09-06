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

from deepcompare.horizon import segment  # noqa: E402
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
