"""Where it mattered: steps clustered along lanes, phases and framing
steps; every cluster scored from what the other sections established
(decisive step, fault path, divergences, errors, retries, wasted seconds,
milestones, the answer, tokens) and normalised so the run's hottest is 1;
the clusters cover every step once; the narratives quote the hot ones."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import impact as I  # noqa: E402
from deepcompare.impact import CHUNK, MAX_MARKS, SPLIT_OVER, WEIGHTS, impact_pair, impact_run  # noqa: E402


def _step(i, typ, name, *, tokens=10, latency=1.0, span=None, error=None):
    return {"index": i, "type": typ, "name": name, "input": "", "output": "", "tokens": tokens,
            "latency_s": latency, "quality": None, "note": None, "model": None, "tokens_basis": None,
            "error": error, "effect": None, "span": span}


def _timing_rows(steps, wasted=None, retries=None):
    wasted = wasted or {}
    retries = retries or {}
    return [{"index": s["index"], "type": s["type"], "name": s["name"], "latency_s": s["latency_s"], "share": 0.0,
             "category": "answer" if s["type"] == "answer" else "tool" if s["type"] in I.TOOLISH else "think",
             "role": None, "wasted": wasted.get(s["index"]), "wasted_label": None,
             "retry_of": retries.get(s["index"]), "tokens": s["tokens"]} for s in steps]


def _hand_report():
    """orch runs; researcher is delegated (span s1) and nests a reader (s2 under
    s1); step 4 is decisive, step 2 errors, step 3 is wasted, step 5 reaches a
    milestone, the fault's path is [4, 6]; b has two unmeasured steps."""
    s1 = {"id": "s1", "agent": "researcher", "parent": None}
    s2 = {"id": "s2", "agent": "reader", "parent": "s1"}
    a_steps = [
        _step(0, "plan", "plan", tokens=10),
        _step(1, "reason", "reason", tokens=5),
        _step(2, "search", "web_search", span=s1, error=True, latency=2.0),
        _step(3, "read", "open_page", span=s2, latency=4.0),
        _step(4, "tool_call", "calc", span=s1, tokens=40),
        _step(5, "reason", "reason", tokens=20),
        _step(6, "answer", "answer", tokens=8),
    ]
    b_steps = [_step(0, "reason", "reason", latency=0.0), _step(1, "answer", "answer", latency=0.0)]
    return {
        "task": {"id": "t"},
        "a": {"agent": {"name": "orch"}, "outcome": {"success": False}, "steps": a_steps},
        "b": {"agent": {"name": "other"}, "outcome": {"success": True}, "steps": b_steps},
        "timing": {"a": {"measurable": True, "total_s": 11.0, "steps": _timing_rows(a_steps, wasted={2: "error", 3: "dead_end"})},
                   "b": {"measurable": False, "total_s": 0.0, "steps": []}},
        "reading": {"a": {"phases": [{"intent": "frame", "steps": [0, 1]}, {"intent": "acquire", "steps": [2, 3, 4]},
                                     {"intent": "decide", "steps": [5]}, {"intent": "commit", "steps": [6]}],
                          "what_happened": [{"step": 0, "intent": "frame"}, {"step": 1, "intent": "frame"}, {"step": 2, "intent": "acquire"},
                                            {"step": 3, "intent": "acquire"}, {"step": 4, "intent": "acquire"}, {"step": 5, "intent": "decide"},
                                            {"step": 6, "intent": "commit"}]},
                    "b": {"phases": [{"intent": "commit", "steps": [0, 1]}], "what_happened": []}},
        "diagnosis": {"subject": "a", "decisive_step": {"step": 4}},
        "attribution": {"failed_agent": "a", "root_cause_step": 4, "chain": [4, 6]},
        "divergences": [{"rank": 2, "a_index": 5, "b_index": 0}, {"rank": 1, "a_index": 2, "b_index": None}],
        "milestones": {"a": {"measurable": True, "milestones": [{"id": "m1", "label": "found it", "reached": True, "step": 5},
                                                                 {"id": "m2", "reached": False, "step": None}]}},
    }


def _covers(clusters, n):
    seen = []
    for c in clusters:
        seen.extend(range(c["from"], c["to"] + 1))
    return seen == list(range(n))


class WeightsTest(unittest.TestCase):
    def test_the_weights_are_exposed_and_documented(self):
        self.assertEqual(set(WEIGHTS), {"decisive", "fault", "first_divergence", "divergence", "error", "retry",
                                        "wasted_per_s", "milestone", "answer", "tokens"})
        self.assertTrue(all(v > 0 for v in WEIGHTS.values()))
        self.assertEqual((WEIGHTS["decisive"], WEIGHTS["fault"], WEIGHTS["error"]), (5.0, 3.0, 2.0))
        for key in WEIGHTS:
            self.assertIn(key.replace("_per_s", ""), I.__doc__)


class HandBuiltTest(unittest.TestCase):
    def setUp(self):
        self.report = _hand_report()
        self.out = impact_pair(self.report)
        self.a = self.out["a"]

    def test_the_section_shape_and_coverage(self):
        self.assertEqual(self.out["version"], 1)
        self.assertTrue(self.a["measurable"])
        self.assertEqual(self.a["total_steps"], 7)
        self.assertAlmostEqual(self.a["total_s"], 11.0)
        self.assertTrue(_covers(self.a["clusters"], 7))
        self.assertEqual([c["id"] for c in self.a["clusters"]], [f"c{k}" for k in range(len(self.a["clusters"]))])
        for c in self.a["clusters"]:
            self.assertEqual(c["steps"], c["to"] - c["from"] + 1)
            self.assertAlmostEqual(c["seconds"], c["end_s"] - c["start_s"], places=3)
        # every cluster id sits in exactly one lane
        ids = [cid for ln in self.a["lanes"] for cid in ln["clusters"]]
        self.assertEqual(sorted(ids), sorted(c["id"] for c in self.a["clusters"]))
        # the other side: no latency, still clustered, seconds 0
        b = self.out["b"]
        self.assertTrue(b["measurable"])
        self.assertTrue(_covers(b["clusters"], 2))
        self.assertEqual(b["total_s"], 0.0)
        self.assertIn("no latency recorded", b["narrative"])

    def test_lanes_fold_the_nested_span_into_its_depth_one_ancestor(self):
        by_agent = {ln["agent"]: ln for ln in self.a["lanes"]}
        self.assertEqual([ln["agent"] for ln in self.a["lanes"]], ["orch", "researcher"])
        self.assertEqual((by_agent["orch"]["depth"], by_agent["orch"]["parent"]), (0, None))
        self.assertEqual((by_agent["researcher"]["depth"], by_agent["researcher"]["parent"]), (1, "orch"))
        inner = next(c for c in self.a["clusters"] if c["from"] <= 3 <= c["to"])
        self.assertEqual(inner["lane"], "researcher")
        self.assertEqual(inner["agents"], ["researcher", "reader"])
        self.assertEqual(inner["from"], 2)
        self.assertEqual(inner["to"], 4)
        self.assertNotIn("reader", by_agent)

    def test_scores_reasons_impact_and_kinds(self):
        hot = next(c for c in self.a["clusters"] if c["from"] == 2)
        self.assertEqual(hot["reasons"], {"fault_steps": 1, "decisive": True, "errors": 1, "retries": 0, "wasted_s": 6.0,
                                          "milestones": [], "divergence_rows": 1, "first_divergence": True, "answer": False, "tokens": 60})
        # decisive 5 + fault 3 + first divergence 3 + divergence row 1.5 + error 2
        # + wasted 0.5×2 (step 2) + 0.5×4 (step 3) + tokens 0.5×(10+10+40)/40 + 0.2 ln(1+7)
        import math
        expected = 5 + 3 + 3 + 1.5 + 2 + 1.0 + 2.0 + 0.5 * 60 / 40 + 0.2 * math.log1p(7.0)
        self.assertAlmostEqual(hot["score"], round(expected, 4), places=4)
        self.assertEqual(hot["impact"], 1.0)
        self.assertEqual(hot["kind"], "hot")
        self.assertEqual(max(c["impact"] for c in self.a["clusters"]), 1.0)
        # the pair shares one scale: the other side's single cluster is measured against orch's hottest
        self.assertEqual((self.a["scale"], self.out["b"]["scale"]), ("pair", "pair"))
        other = self.out["b"]["clusters"][0]
        self.assertAlmostEqual(other["impact"], round(other["score"] / hot["score"], 4), places=4)
        self.assertLess(other["impact"], 0.5)
        self.assertEqual(self.out["b"]["hot"], [])
        # alone, that same run is its own maximum
        alone = impact_run(self.report, "b")
        self.assertEqual((alone["scale"], alone["hot"], alone["clusters"][0]["impact"]), ("run", ["c0"], 1.0))
        for c in self.a["clusters"]:
            self.assertEqual(c["kind"], "hot" if c["impact"] >= 0.5 else "work" if c["impact"] >= 0.15 else "quiet")
            self.assertAlmostEqual(c["impact"], round(c["score"] / hot["score"], 4), places=4)
        self.assertEqual(self.a["hot"][0], hot["id"])
        self.assertTrue(all(next(c for c in self.a["clusters"] if c["id"] == cid)["impact"] >= 0.5 for cid in self.a["hot"]))
        self.assertLessEqual(len(self.a["hot"]), 5)
        for why in ("decisive step", "1 error", "6.0s wasted", "on the fault's path", "the first divergence"):
            self.assertIn(why, hot["why"])
        self.assertEqual(hot["label"], "web_search")
        ms = next(c for c in self.a["clusters"] if c["from"] <= 5 <= c["to"])
        self.assertIn("m1", ms["reasons"]["milestones"])
        self.assertIn("milestone m1", ms["why"])
        ans = next(c for c in self.a["clusters"] if c["to"] == 6)
        self.assertTrue(ans["reasons"]["answer"])
        self.assertIn("the answer", ans["why"])
        quiet = self.a["clusters"][0]
        self.assertEqual(quiet["reasons"]["errors"], 0)
        self.assertIn("nothing else notable", quiet["why"])
        self.assertEqual(quiet["label"], "thinking")

    def test_marks_lead_with_decisive_fault_and_error(self):
        hot = next(c for c in self.a["clusters"] if c["from"] == 2)
        kinds = [m["kind"] for m in hot["marks"]]
        self.assertEqual(kinds[:3], ["decisive", "fault", "error"])
        self.assertEqual([m["step"] for m in hot["marks"] if m["kind"] == "decisive"], [4])
        self.assertIn("divergence", kinds)
        div = next(m for m in hot["marks"] if m["kind"] == "divergence")
        self.assertEqual(div["step"], 2)
        self.assertIn("the first", div["label"])
        ans = next(c for c in self.a["clusters"] if c["to"] == 6)
        self.assertEqual([m["kind"] for m in ans["marks"]], ["fault", "answer"])

    def test_marks_are_capped(self):
        steps = [_step(i, "tool_call", "run_tests", error=True) for i in range(20)] + [_step(20, "answer", "answer")]
        report = {"a": {"agent": {"name": "x"}, "steps": steps}, "b": {"agent": {"name": "y"}, "steps": []}}
        run = impact_run(report, "a")
        self.assertEqual(len(run["clusters"]), 1)
        c = run["clusters"][0]
        self.assertEqual(c["reasons"]["errors"], 20)
        self.assertEqual(len(c["marks"]), MAX_MARKS)
        self.assertTrue(all(m["kind"] == "error" for m in c["marks"]))
        self.assertEqual(c["label"], "run_tests ×20")
        self.assertIn("20 errors", c["why"])

    def test_narratives_quote_the_hot_cluster(self):
        hot = next(c for c in self.a["clusters"] if c["from"] == 2)
        self.assertIn(f"orch: {len(self.a['clusters'])} clusters over 7 steps and 11s", self.a["narrative"])
        self.assertIn(f"{hot['id']} (researcher, steps 2–4: {hot['why']})", self.a["narrative"])
        self.assertIn("of the wall-clock sits in quiet clusters", self.a["narrative"])
        pair = self.out["narrative"]
        self.assertIn("on the shared scale orch has 1 hot cluster against 0 for other", pair)
        self.assertIn("other's hottest is c0 in other (steps 0–1, impact 0.", pair)
        self.assertIn(f"orch's hottest is {hot['id']} in researcher (steps 2–4, impact 1.00", pair)
        self.assertIn(f"orch's hottest is {hot['id']} in researcher (steps 2–4", pair)
        self.assertIn("quiet clusters hold orch", pair)

    def test_missing_sections_and_empty_sides_never_raise(self):
        bare = {"a": {"agent": {"name": "solo"}, "steps": [_step(0, "reason", "r"), _step(1, "answer", "answer")]}, "b": {}}
        out = impact_pair(bare)
        self.assertTrue(out["a"]["measurable"])
        self.assertTrue(_covers(out["a"]["clusters"], 2))
        self.assertFalse(out["b"]["measurable"])
        self.assertEqual(out["b"]["clusters"], [])
        self.assertEqual((out["a"]["scale"], out["b"]["scale"]), ("run", "run"))
        self.assertEqual(max(c["impact"] for c in out["a"]["clusters"]), 1.0)
        self.assertIn("no steps", out["narrative"])
        self.assertFalse(impact_run({}, "a")["measurable"])
        # a score of zero everywhere: impact 0, not a division by zero
        zero = {"a": {"agent": {"name": "z"}, "steps": [_step(0, "reason", "r", tokens=0, latency=0.0)]}}
        run = impact_run(zero, "a")
        self.assertEqual(run["clusters"][0]["impact"], 0.0)
        self.assertEqual(run["clusters"][0]["why"], "nothing notable")
        self.assertEqual(run["hot"], [])


class ClusteringTest(unittest.TestCase):
    def test_quiet_stretches_coalesce_and_a_hot_step_stands_alone(self):
        steps = [_step(i, "reason", "reason", tokens=1, latency=0.1) for i in range(12)]
        steps[6] = _step(6, "tool_call", "run_tests", tokens=1, latency=0.1, error=True)
        steps.append(_step(12, "answer", "answer", tokens=1, latency=0.1))
        report = {"a": {"agent": {"name": "x"}, "steps": steps}, "b": {},
                  "reading": {"a": {"phases": [{"intent": "acquire" if i % 2 else "transform", "steps": [i]} for i in range(13)], "what_happened": []}},
                  "diagnosis": {"subject": "a", "decisive_step": {"step": 6}}}
        run = impact_run(report, "a")
        self.assertTrue(_covers(run["clusters"], 13))
        self.assertLess(len(run["clusters"]), 13)
        hot = next(c for c in run["clusters"] if c["from"] <= 6 <= c["to"])
        self.assertEqual((hot["from"], hot["to"]), (6, 6))
        self.assertEqual(hot["kind"], "hot")
        self.assertEqual(run["hot"], [hot["id"]])

    def test_long_clusters_split_into_bounded_chunks_at_the_lowest_scores(self):
        n = SPLIT_OVER + 20
        steps = [_step(i, "tool_call", "grep", tokens=5, latency=0.5) for i in range(n)]
        for i in (20, 41):   # two cheap steps: the natural cut points
            steps[i]["tokens"] = 0
        report = {"a": {"agent": {"name": "x"}, "steps": steps}, "b": {}}
        run = impact_run(report, "a")
        self.assertTrue(_covers(run["clusters"], n))
        self.assertTrue(all(c["steps"] <= CHUNK for c in run["clusters"]))
        self.assertEqual([c["from"] for c in run["clusters"]][1:], [20, 41])

    def test_lane_changes_always_open_a_cluster(self):
        sub = {"id": "s1", "agent": "helper", "parent": None}
        steps = [_step(0, "reason", "r"), _step(1, "search", "s", span=sub), _step(2, "search", "s", span=sub), _step(3, "answer", "answer")]
        run = impact_run({"a": {"agent": {"name": "x"}, "steps": steps}}, "a")
        self.assertEqual([(c["from"], c["to"], c["lane"]) for c in run["clusters"]], [(0, 0, "x"), (1, 2, "helper"), (3, 3, "x")])
        self.assertEqual([ln["agent"] for ln in run["lanes"]], ["x", "helper"])
        self.assertEqual(run["lanes"][0]["clusters"], ["c0", "c2"])


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(ROOT / "demo" / "horizon" / "long"),
                        "--golden", str(ROOT / "demo" / "horizon" / "golden.json"), "-o", cls.tmp.name],
                       cwd=str(ROOT), check=True, capture_output=True)
        cls.report = json.loads((Path(cls.tmp.name) / "report_h02_migrate_service.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_long_demo_pair_is_weighed_and_the_ledger_is_where_comet_struggled(self):
        imp = self.report["impact"]
        self.assertEqual(imp["version"], 1)
        names = {s: self.report[s]["agent"]["name"] for s in "ab"}
        comet = "a" if names["a"] == "comet-lh" else "b"
        atlas = "b" if comet == "a" else "a"
        self.assertEqual(max(c["impact"] for s in "ab" for c in imp[s]["clusters"]), 1.0)
        self.assertLess(len(imp[atlas]["hot"]), len(imp[comet]["hot"]))
        for side in "ab":
            run = imp[side]
            self.assertTrue(run["measurable"])
            self.assertEqual(run["scale"], "pair")
            self.assertTrue(8 <= len(run["clusters"]) <= 48, len(run["clusters"]))
            self.assertTrue(_covers(run["clusters"], len(self.report[side]["steps"])))
            top = max(c["score"] for s in "ab" for c in imp[s]["clusters"])
            for c in run["clusters"]:
                self.assertAlmostEqual(c["impact"], round(c["score"] / top, 4), places=4)
                self.assertEqual(c["kind"], "hot" if c["impact"] >= 0.5 else "work" if c["impact"] >= 0.15 else "quiet")
            self.assertTrue(all(c["steps"] <= SPLIT_OVER for c in run["clusters"]))
            root = next(ln for ln in run["lanes"] if ln["agent"] == names[side])
            self.assertEqual((root["depth"], root["parent"]), (0, None))
            self.assertTrue(all(ln["parent"] == names[side] for ln in run["lanes"] if ln is not root))
            self.assertTrue(all(ln["depth"] in (0, 1) for ln in run["lanes"]))
            self.assertTrue(any(m["kind"] == "milestone" for c in run["clusters"] for m in c["marks"]), "milestone marks present")
            self.assertIn(f"{names[side]}: {len(run['clusters'])} clusters over {len(self.report[side]['steps'])} steps", run["narrative"])
        hottest = next(c for c in imp[comet]["clusters"] if c["id"] == imp[comet]["hot"][0])
        self.assertEqual(hottest["lane"], "migrator-ledger")
        self.assertGreaterEqual(hottest["reasons"]["errors"], 4)
        self.assertIn("errors", hottest["why"])
        self.assertIn("comet-lh's hottest is", imp["narrative"])
        self.assertIn("in migrator-ledger", imp["narrative"])
        # the decisive step of the failing side is marked where the diagnosis put it
        dec = self.report["diagnosis"]["decisive_step"]["step"]
        subj = self.report["diagnosis"]["subject"]
        holder = next(c for c in imp[subj]["clusters"] if c["from"] <= dec <= c["to"])
        self.assertTrue(holder["reasons"]["decisive"])
        self.assertEqual(holder["marks"][0], {"step": dec, "kind": "decisive", "label": holder["marks"][0]["label"]})

    def test_the_section_is_deterministic(self):
        one = json.dumps(impact_pair(self.report), sort_keys=True)
        two = json.dumps(impact_pair(self.report), sort_keys=True)
        self.assertEqual(one, two)
        self.assertEqual(one, json.dumps(self.report["impact"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
