"""Runs on their clocks: threads, laps, folds, where to look first; two runs; many runs.

The cases are the repo's own: the three loop runs (one converges, one is
stuck, one flails), the multi-agent horizon run with five threads, and
the long migration with 566 steps over 28 threads.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.hub import viz
from agentdiff.timeline import FOLD_MIN, compare, fold_width, ribbons, timeline

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


STUCK = "demo/loops/traces/parse_duration__agent-stuck.json"
CONVERGES = "demo/loops/traces/parse_duration__agent-converges.json"
FLAILS = "demo/loops/traces/parse_duration__agent-flails.json"
HORIZON = "demo/horizon/traces/h01_release_report__comet-v2.json"
LONG = "demo/horizon/long/h02_migrate_service__comet-lh.json"


class TimelineTest(unittest.TestCase):
    def test_each_agent_is_a_thread_in_the_order_it_first_acted(self):
        t = timeline(_load(HORIZON))
        self.assertEqual([l["label"] for l in t["lanes"]], ["root", "researcher", "coder", "coder.tests", "verifier"])
        self.assertEqual(sum(l["steps"] for l in t["lanes"]), len(t["steps"]))

    def test_steps_that_overlap_in_time_get_their_own_lanes(self):
        steps = [{"type": "tool_call", "name": "Read", "started_s": 0.0, "latency_s": 2.0},
                 {"type": "tool_call", "name": "Grep", "started_s": 0.5, "latency_s": 1.0},
                 {"type": "tool_call", "name": "Edit", "started_s": 3.0, "latency_s": 0.5}]
        t = timeline({"steps": steps})
        self.assertEqual(t["basis"], "recorded")
        self.assertEqual([l["label"] for l in t["lanes"]], ["root", "root ∥2"])
        self.assertEqual([s["lane"] for s in t["steps"]], ["root#0", "root#1", "root#0"])

    def test_a_run_with_no_recorded_starts_says_its_clock_is_reconstructed(self):
        self.assertEqual(timeline(_load(STUCK))["basis"], "reconstructed")

    def test_a_long_run_folds_its_quiet_stretches_and_keeps_its_events(self):
        t = timeline(_load(LONG))
        self.assertEqual((len(t["steps"]), len(t["lanes"])), (566, 28))
        folds = [s for s in t["segments"] if s["kind"] == "fold"]
        self.assertTrue(folds)
        self.assertTrue(all(s["steps"] >= FOLD_MIN or s.get("idle") for s in folds))
        folded = set()
        for s in folds:
            if s.get("steps"):
                folded |= {x["index"] for x in t["steps"] if s["from"] <= x["start"] and x["end"] <= s["to"]}
        events = {x["index"] for x in t["steps"] if x["error"] or x["activity"] in ("edit", "verify")}
        self.assertFalse(folded & events, "an edit, a check or an error is never folded away")
        self.assertGreater(fold_width(100), fold_width(1))

    def test_where_to_look_first_differs_by_how_the_run_failed(self):
        cases = {STUCK: ("loop", 6), FLAILS: ("check", 16), CONVERGES: ("pass", 11), LONG: ("loop", 398)}
        for rel, (kind, index) in cases.items():
            with self.subTest(rel=rel):
                here = timeline(_load(rel))["look_here"]
                self.assertEqual((here["kind"], here["index"]), (kind, index), here["sentence"])
        self.assertIn("11 time(s) in a row", timeline(_load(LONG))["look_here"]["sentence"])

    def test_a_failed_run_that_never_checked_is_said_to_be_that(self):
        steps = [{"type": "tool_call", "name": "Edit", "input": "f.py"}, {"type": "answer", "name": "answer"}]
        here = timeline({"steps": steps, "outcome": {"success": False}})["look_here"]
        self.assertEqual(here["kind"], "unchecked")

    def test_a_running_trace_is_pointed_at_its_newest_step_never_judged(self):
        d = dict(_load(STUCK), in_progress=True)
        here = timeline(d)["look_here"]
        self.assertEqual(here["kind"], "now")
        self.assertIn("judged when it ends", here["sentence"])


class CompareTest(unittest.TestCase):
    def test_two_runs_are_told_apart_at_their_first_different_call(self):
        c = compare(_load(STUCK), _load(CONVERGES))
        self.assertEqual((c["diverged_at"], c["same_prefix"]), (6, 6))
        self.assertIn("minutes default 0", c["sentence"])

    def test_a_run_against_itself_never_diverges(self):
        c = compare(_load(STUCK), _load(STUCK))
        self.assertIsNone(c["diverged_at"])


class RibbonsTest(unittest.TestCase):
    def test_many_runs_compact_to_rows_of_cells(self):
        rows = ribbons([_load(LONG), _load(STUCK)], cells=60)
        self.assertLessEqual(len(rows[0]["cells"]), 60)
        self.assertEqual(rows[0]["per_cell"], 10)
        self.assertEqual(sum(1 for c in rows[1]["cells"]), 19, "a short run keeps a cell per step")
        self.assertEqual(rows[1]["look_here"]["kind"], "loop")


class DrawTest(unittest.TestCase):
    def test_every_drawing_is_svg_with_titles_and_what_a_trace_holds_is_escaped(self):
        hostile = {"steps": [{"type": "tool_call", "name": "<script>x</script>", "input": "<img onerror=1>",
                              "span": {"agent": "<b>sub</b>"}}], "outcome": {"success": False}}
        t = timeline(hostile)
        for html in (viz.run_timeline(t), viz.pair_timeline(compare(hostile, hostile)),
                     viz.ribbons_svg(ribbons([hostile]))):
            self.assertIn("<svg", html)
            self.assertNotIn("<script>", html)
            self.assertNotIn("<b>sub", html)
        for html in (viz.trunk_svg([t]), viz.trunk_svg([t, t], cmp=compare(hostile, hostile))):
            self.assertIn("<svg", html)
            self.assertNotIn("<script>", html)
            self.assertNotIn("<b>sub", html)
        long_ = viz.run_timeline(timeline(_load(LONG)))
        self.assertIn("look here: step 398", long_)
        self.assertIn('href="#s398"', long_, "every bar opens its step")
        self.assertIn("⋯", long_)


class TrunkTest(unittest.TestCase):
    def test_a_run_is_a_trunk_its_tools_branch_off_and_its_sub_agents_hang_from_it(self):
        t = timeline(_load(HORIZON))
        html = viz.trunk_svg([t])
        for agent in ("researcher", "coder", "coder.tests", "verifier"):
            self.assertIn(f">{agent}<", html, "each sub-agent is a branch, named")
        self.assertIn("look here: step 20", html)
        self.assertIn('href="#s20"', html, "every leaf opens its step")
        self.assertIn(" C", html, "tool calls branch off on curves")

    def test_two_runs_face_each_other_and_the_first_difference_is_marked(self):
        c = compare(_load(STUCK), _load(CONVERGES))
        for axis in ("time", "step"):
            html = viz.trunk_svg([c["a"], c["b"]], cmp=c, axis=axis)
            self.assertIn("first difference: step 6", html)
            self.assertIn("A · agent-stuck", html)
            self.assertIn("B · agent-conve", html)

    def test_a_long_run_gathers_crowded_steps_into_bubbles(self):
        html = viz.trunk_svg([timeline(_load(LONG))])
        self.assertIn("×", html)
        self.assertLess(len(html), 200_000, "566 steps stay a page, not a dump")


class HubTest(unittest.TestCase):
    def test_the_trace_page_leads_with_its_clock_and_compares_on_request(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_hub_live import _app, _root, _sign_in
        from agentdiff.hub.app import Request
        from agentdiff.hub.traces import trace_id
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            shutil.copytree(ROOT / "demo" / "horizon" / "long", root / "long" / "traces")
            app = _app(root)
            h = _sign_in(app)
            stuck = trace_id("loops/traces/parse_duration__agent-stuck.json")
            conv = trace_id("loops/traces/parse_duration__agent-converges.json")
            page = app.handle(Request("GET", f"/traces/{stuck}", h)).body.decode()
            self.assertIn("Look here first.", page)
            self.assertIn("On its clock", page)
            self.assertIn(f'value="{conv}"', page, "the runs of the same task are offered to compare with")
            both = app.handle(Request("GET", f"/traces/{stuck}?vs={conv}&axis=step", h)).body.decode()
            self.assertIn("first difference: step 6", both)
            self.assertIn("Two runs on one axis", app.handle(Request("GET", f"/traces/{stuck}/panel?vs={conv}", h)).body.decode())
            long_id = trace_id("long/traces/h02_migrate_service__comet-lh.json")
            long_page = app.handle(Request("GET", f"/traces/{long_id}", h)).body.decode()
            self.assertIn("The steps that matter", long_page)
            self.assertIn('id="s398"', long_page, "the window holds the step to look at")
            self.assertLess(len(re.findall(r'<tr id="s', long_page)), 120)
            self.assertIn("All 122 laps", long_page)
            every = app.handle(Request("GET", f"/traces/{long_id}?steps=all", h)).body.decode()
            self.assertGreater(len(re.findall(r'<tr id="s', every)), 390)
            many = app.handle(Request("GET", "/timeline?g=loops/traces", h)).body.decode()
            self.assertIn("3 run(s), 2 failed", many)
            self.assertIn("went round the same lap", many)
            self.assertEqual(app.handle(Request("GET", "/timeline?g=loops/traces&scale=own", h)).status, 200)
            self.assertEqual(app.handle(Request("GET", "/timeline/panel?g=loops/traces", h)).status, 200)
            self.assertEqual(app.handle(Request("GET", "/timeline", {})).status, 303)


class CommandTest(unittest.TestCase):
    def test_one_two_and_many(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.html"
            for args, want in (([LONG], "look here: Lap 84"), ([STUCK, CONVERGES], "first step where they differ"),
                               (["demo/loops/traces"], "3 run(s), 2 failed")):
                done = subprocess.run([sys.executable, "-m", "agentdiff", "timeline", *args, "-o", str(out)],
                                      cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn(want, done.stdout)
                html = out.read_text()
                self.assertIn("<svg", html)
                self.assertNotIn("<script", html, "the page runs no script")
            bad = subprocess.run([sys.executable, "-m", "agentdiff", "timeline", "nope.json"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
