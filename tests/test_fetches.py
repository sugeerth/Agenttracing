"""Every fetch and what came back: the fetches section records the
searches, retrievals, reads and tool calls as the steps recorded them.

What this pins: the records with their query (truncated, the full length
kept), output size, tokens, latency, error, effect, the fetch each
repeats and whether its result was used — a recorded reward or quality
label, else null with the reason; the counts; the sources; the search
map with its yields/reads edges and the reaches edges only for recorded
use; the pair delta; the aggregate with its ledger; SYNTHETIC through;
and that the section attaches under its key.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import sections  # noqa: E402
from deepcompare.fetches import FETCH_KINDS, QUERY_CHARS, fetches_aggregate, fetches_pair, fetches_run  # noqa: E402
from deepcompare.commands._io import load_traces  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402
from tests.test_budget import step, trace  # noqa: E402

DEMO = ROOT / "demo" / "traces"
TRAIN = ROOT / "demo" / "rl" / "train"


class RunTest(unittest.TestCase):
    def test_the_demo_run_records_its_three_fetches_and_their_use(self):
        f = fetches_run(Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json"))
        self.assertTrue(f["measurable"])
        self.assertEqual([r["kind"] for r in f["records"]], ["search", "retrieve", "read"])
        self.assertEqual([r["used"] for r in f["records"]], [True, True, True])
        self.assertEqual(f["records"][0]["used_basis"], "quality label good")
        self.assertEqual(f["counts"]["total"], 3)
        self.assertEqual(list(f["counts"]["by_kind"]), list(FETCH_KINDS))
        self.assertEqual(f["sources"][0]["name"], "open_page")
        kinds = {e["kind"] for e in f["map"]["edges"]}
        self.assertEqual(kinds, {"yields", "reads", "reaches"})
        self.assertEqual(sum(1 for e in f["map"]["edges"] if e["kind"] == "reaches"), 3)
        self.assertEqual([n["kind"] for n in f["map"]["nodes"]], ["query", "result", "read", "answer"])
        self.assertIn("3 fetches", f["narrative"])

    def test_repeats_errors_latency_and_the_query_length(self):
        long_query = "q" * (QUERY_CHARS + 50)
        t = trace([step(0, "search", "s", tokens=3, input=long_query, latency_s=1.5),
                   step(1, "search", "s", tokens=3, input=long_query, error=True),
                   step(2, "tool_call", "grep", tokens=2, effect="read", span={"id": "s1", "agent": "helper"}),
                   step(3, "answer", tokens=1)])
        f = fetches_run(t)
        r0, r1, r2 = f["records"]
        self.assertEqual(len(r0["query"]), QUERY_CHARS)
        self.assertEqual(r0["query_chars"], QUERY_CHARS + 50)
        self.assertEqual(r0["latency_s"], 1.5)
        self.assertIsNone(r1["latency_s"])
        self.assertEqual(r1["repeat_of"], 0)
        self.assertTrue(r1["error"])
        self.assertEqual((r2["effect"], r2["span"], r2["kind"]), ("read", "helper", "tool_call"))
        self.assertEqual(f["counts"]["repeats"], 1)
        self.assertEqual(f["counts"]["errors"], 1)

    def test_use_is_a_recorded_signal_or_null(self):
        t = trace([step(0, "search", "s", reward=1.0), step(1, "search", "s", input="other", reward=-0.1),
                   step(2, "read", "r", quality="bad"), step(3, "read", "r", input="x", quality="weak"),
                   step(4, "read", "r", input="y"), step(5, "answer")])
        f = fetches_run(t)
        self.assertEqual([r["used"] for r in f["records"]], [True, False, False, None, None])
        self.assertEqual(f["records"][4]["used_basis"], "no reward or quality label recorded")
        self.assertEqual(f["counts"]["unknown_use"], 2)
        self.assertEqual(f["map"]["unknown_use"], 2)
        reaches = [e["from"] for e in f["map"]["edges"] if e["kind"] == "reaches"]
        self.assertEqual(reaches, ["query0"])
        self.assertIn("2 fetches of unknown use have no edge", f["map"]["basis"])

    def test_synthetic_and_nothing_fetched(self):
        t = trace([step(0, "answer")], harness={"note": "SYNTHETIC: invented"})
        f = fetches_run(t)
        self.assertTrue(f["synthetic"])
        self.assertEqual(f["counts"]["total"], 0)
        self.assertIn("fetched nothing", f["narrative"])
        self.assertFalse(fetches_run({"agent": {"name": "x"}, "steps": []})["measurable"])


class PairTest(unittest.TestCase):
    def test_the_pair_section_attaches_with_the_delta(self):
        a = Trajectory.from_json(DEMO / "t01_acme_revenue__atlas-v2.json")
        b = Trajectory.from_json(DEMO / "t01_acme_revenue__bolt-v3.json")
        report = compare(a, b)
        sec = report["fetches"]
        self.assertEqual(list(sec)[:3], ["version", "measurable", "reason"])
        self.assertEqual(sec["delta"]["total"], sec["a"]["counts"]["total"] - sec["b"]["counts"]["total"])
        self.assertEqual(sec, fetches_pair(report))
        self.assertEqual(list(report)[-1], "fetches")
        self.assertIn("fetches", sections.registered("aggregate"))


class AggregateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trajs = [t for t in load_traces(TRAIN, run_ids=True) if t.task.id.startswith("rl02_")]

    def test_agents_ledger_and_heaviest(self):
        sec = fetches_aggregate(self.trajs)
        self.assertTrue(sec["measurable"])
        ag = sec["agents"]["policy-v2"]
        self.assertEqual(ag["runs"], 8)
        self.assertEqual(ag["fetches"], sum(r["fetches"] for r in sec["runs"] if r["agent"] == "policy-v2"))
        self.assertIsNotNone(ag["used_share"])       # the RL traces record a reward per step
        self.assertEqual(sum(ag["by_tool"].values()), ag["fetches"])
        self.assertEqual(len(sec["runs"]), 16)
        self.assertIn("by_tool", sec["runs"][0])
        self.assertEqual(sec["heaviest_runs"][0]["fetches"], max(r["fetches"] for r in sec["runs"]))
        self.assertTrue(all(r["synthetic"] for r in sec["runs"]))

    def test_no_signal_gives_no_share(self):
        sec = fetches_aggregate([trace([step(0, "search", "s"), step(1, "answer")])])
        self.assertIsNone(sec["agents"]["probe"]["used_share"])
        self.assertIn("no fetch carries a use signal", sec["narrative"])
        self.assertFalse(fetches_aggregate([])["measurable"])

    def test_same_input_same_bytes(self):
        self.assertEqual(json.dumps(fetches_aggregate(self.trajs)), json.dumps(fetches_aggregate(list(reversed(self.trajs)))))


if __name__ == "__main__":
    unittest.main()
