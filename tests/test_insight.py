"""What a run means, what code it left, and what to change next.

The code a run produced is read from the harness's own record (the
workspace diffed before and after), so the tests use the real duel
records in ``demo/vendors/live``, plus small records made here for each
flag.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.insight import (agent_fix, check_failures, code_change, code_compare, corpus_insight, is_test_path,
                               verdict_card)
from agentdiff.timeline import timeline

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "demo" / "vendors" / "live"


def _rec(name: str) -> dict:
    return json.loads((LIVE / "records" / f"{name}.json").read_text(encoding="utf-8"))


def _record(files, passed=True, tail="", complete=True) -> dict:
    return {"diff": {"files": files, "added": sum(f.get("added", 0) for f in files),
                     "removed": sum(f.get("removed", 0) for f in files)},
            "check": {"command": "pytest -q", "passed": passed, "output_tail": tail}, "after": {"complete": complete}}


class CodeChangeTest(unittest.TestCase):
    def test_a_real_duel_record_reads_as_the_change_it_left(self):
        c = code_change(_rec("bugfix-pricing__haiku__r1"), "+x\n-y\n")
        self.assertEqual((c["added"], c["removed"], [f["path"] for f in c["files"]]), (1, 1, ["pricing.py"]))
        self.assertTrue(c["passed"])
        self.assertTrue(c["keepable"])
        self.assertEqual(c["flags"], [])

    def test_two_agents_that_left_the_same_bytes_are_said_to(self):
        a = code_change(_rec("bugfix-pricing__haiku__r1"))
        b = code_change(_rec("bugfix-pricing__sonnet__r1"))
        cmp = code_compare(a, b)
        self.assertEqual(cmp["both"], ["pricing.py"])
        self.assertIn("same bytes" if cmp["same"] else "differently", cmp["sentence"])

    def test_what_a_reader_must_see_before_keeping_a_change_is_flagged(self):
        edited = code_change(_record([{"path": "src/a.py", "added": 3}, {"path": "tests/test_a.py", "removed": 4}]))
        self.assertEqual([f["kind"] for f in edited["flags"]], ["tests_edited"])
        self.assertEqual(edited["tests"], ["tests/test_a.py"])
        gone = code_change(_record([{"path": "a.py", "status": "deleted", "removed": 10}]))
        self.assertIn("deleted", [f["kind"] for f in gone["flags"]])
        big = code_change(_record([{"path": "a.py", "added": 400}]))
        self.assertIn("large", [f["kind"] for f in big["flags"]])
        nothing = code_change(_record([]))
        self.assertIn("no_change", [f["kind"] for f in nothing["flags"]])
        self.assertFalse(nothing["keepable"])
        partial = code_change(_record([{"path": "a.py", "added": 1}], complete=False))
        self.assertIn("not_kept", [f["kind"] for f in partial["flags"]])
        self.assertFalse(partial["keepable"])

    def test_test_paths_are_recognised(self):
        for p in ("tests/x.py", "test_x.py", "pkg/test/x.go", "a_test.go", "web/x.spec.ts", "conftest.py"):
            self.assertTrue(is_test_path(p), p)
        for p in ("src/latest.py", "contest.py", "testing_utils/x.py"):
            self.assertFalse(is_test_path(p), p)

    def test_the_cases_a_check_failed_on_are_named(self):
        tail = ("FAIL: test_invalid (t.T.test_invalid) (bad='1.0.0-a_b')\nERROR: test_x (t.T.test_x)\n"
                "FAILED (failures=1, errors=1)\nFAILED tests/a.py::test_y - AssertionError: 1 != 2\n")
        got = check_failures(tail)
        self.assertEqual(got, ["test_invalid (bad='1.0.0-a_b')", "test_x",
                               "tests/a.py::test_y (AssertionError: 1 != 2)"])
        c = code_change(_record([{"path": "a.py", "added": 1}], passed=False, tail=tail))
        self.assertEqual(len(c["failures"]), 3)


class AgentFixTest(unittest.TestCase):
    def _load(self, rel):
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))

    def test_the_fix_answers_how_the_run_failed(self):
        stuck = self._load("demo/loops/traces/parse_duration__agent-stuck.json")
        fix = agent_fix(stuck, look_kind=timeline(stuck)["look_here"]["kind"])
        self.assertEqual(fix["rule"], "repeated_call:3")
        self.assertIn("do not run it a third time", fix["change"])

    def test_a_run_that_never_checked_is_told_to(self):
        steps = [{"index": 0, "type": "tool_call", "name": "Edit", "input": "a.py", "output": "ok", "effect": "write"},
                 {"index": 1, "type": "answer", "name": "answer", "input": "", "output": "Done, it works."}]
        t = {"schema_version": 1, "agent": {"name": "a"}, "task": {"id": "t", "prompt": "p"},
             "outcome": {"success": False, "answer": "Done, it works."},
             "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}, "steps": steps}
        fix = agent_fix(t, look_kind="unchecked")
        self.assertIn(fix["rule"], ("claims_without_check", "no_check_after_last_edit"))

    def test_a_run_that_passed_has_nothing_to_fix(self):
        conv = self._load("demo/loops/traces/parse_duration__agent-converges.json")
        self.assertIsNone(agent_fix(conv))


class CardTest(unittest.TestCase):
    def test_every_row_names_where_its_words_came_from(self):
        t = json.loads((LIVE / "traces" / "bugfix-pricing__haiku__r1.json").read_text())
        rows = verdict_card(t, timeline(t), peers=[{"tokens": 1000, "seconds": 3.0, "success": True}],
                            change=code_change(_rec("bugfix-pricing__haiku__r1")))
        self.assertEqual([r["label"] for r in rows], ["verdict", "where", "cost", "code", "keep", "confidence"])
        self.assertTrue(all(r["source"] for r in rows))
        self.assertIn("solved", rows[0]["text"])

    def test_across_many_runs_the_common_fix_is_counted(self):
        items = [{"look_kind": "loop", "fix_rule": "repeated_call:3", "fix_change": "x", "success": False, "agent": "a"},
                 {"look_kind": "loop", "fix_rule": "repeated_call:3", "fix_change": "x", "success": False, "agent": "a"},
                 {"look_kind": "check", "fix_rule": None, "success": False, "agent": "b", "lines": 4},
                 {"success": True, "agent": "b", "lines": 2, "tests_edited": True}]
        c = corpus_insight(items)
        self.assertEqual((c["runs"], c["failed"], c["kinds"]), (4, 3, {"loop": 2, "check": 1}))
        self.assertEqual(c["top_fix"], {"rule": "repeated_call:3", "runs": 2, "change": "x"})
        self.assertEqual([a["tests_edited"] for a in c["agents"]], [0, 1])


class HubTest(unittest.TestCase):
    def test_a_duel_trace_shows_its_card_its_code_and_the_command_to_keep_it(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_hub_live import _app, _root, _sign_in
        from agentdiff.hub.app import Request
        from agentdiff.hub.traces import trace_id
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            shutil.copytree(LIVE, root / "duel", ignore=shutil.ignore_patterns("raw"))
            app = _app(root)
            h = _sign_in(app)
            a = trace_id("duel/traces/bugfix-pricing__haiku__r1.json")
            b = trace_id("duel/traces/bugfix-pricing__sonnet__r1.json")
            page = app.handle(Request("GET", f"/traces/{a}?vs={b}&view=code", h)).body.decode()
            for want in ('class="vcard"', ">code<", "pricing.py", "Beside the other run", "Keep this change",
                         "agentdiff apply --dir", 'id="p-code" open', 'class="add"'):
                self.assertIn(want, page)
            # presets open different panels, and the rest stay one click away
            loop = app.handle(Request("GET", f"/traces/{a}?view=loop", h)).body.decode()
            self.assertIn('id="p-laps" open', loop)
            self.assertIn('<details class="panel" id="p-code">', loop)
            junk = app.handle(Request("GET", f"/traces/{a}?view=<script>", h)).body.decode()
            self.assertNotIn("<script>", junk.split("</style>", 1)[1].replace('<script src="/static/live.js" defer></script>', ""))
            over = app.handle(Request("GET", "/", h)).body.decode()
            self.assertIn("Start here", over)


if __name__ == "__main__":
    unittest.main()


class ReplicaTest(unittest.TestCase):
    """The report page's views, drawn by the hub: the trajectory map, the seconds,
    the agents' levels, the return; and the page's header grammar."""

    def _pair(self):
        a = json.loads((ROOT / "demo/loops/traces/parse_duration__agent-stuck.json").read_text())
        b = json.loads((ROOT / "demo/loops/traces/parse_duration__agent-converges.json").read_text())
        return a, b

    def test_alignment_matches_calls_in_order_and_names_where_they_part(self):
        from agentdiff.timeline import align
        a, b = self._pair()
        al = align(a, b)
        self.assertEqual(al["divergence"], 6)
        self.assertEqual([r["op"] for r in al["rows"][:7]], ["match"] * 6 + ["drift"])
        self.assertIsNone(align(a, a)["divergence"])

    def test_the_views_draw_and_escape(self):
        from agentdiff.hub import viz
        from agentdiff.timeline import align
        a, b = self._pair()
        m = viz.trajectory_map(a, b, align(a, b), names=("<b>x</b>", "y"))
        self.assertIn("diverge", m)
        self.assertNotIn("<b>x</b>", m)
        tm = viz.seconds_treemap([timeline(a), timeline(b)], ["A", "B"])
        self.assertIn('href="#s', tm)
        self.assertIn("50% [", viz.wilson_bar(15, 30))
        rl = json.loads((ROOT / "demo/rl/traces/rl01_ledger_reconcile__policy-v1__r1.json").read_text())
        self.assertIn("step 0", viz.reward_steps([timeline(rl)], ["policy"]))
        self.assertEqual(viz.reward_steps([timeline(a)], ["no rewards"]), "", "no reward recorded, no chart")

    def test_the_page_header_and_the_levels(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_hub_live import _app, _root, _sign_in
        from agentdiff.hub.app import Request
        from agentdiff.hub.traces import trace_id
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(Path(tmp))
            shutil.copytree(ROOT / "demo" / "rl" / "traces", root / "rl" / "traces")
            app = _app(root)
            h = _sign_in(app)
            a = trace_id("rl/traces/rl01_ledger_reconcile__policy-v1__r1.json")
            b = trace_id("rl/traces/rl01_ledger_reconcile__policy-v1__r2.json")
            page = app.handle(Request("GET", f"/traces/{a}?vs={b}&view=all", h)).body.decode()
            for want in ('class="taskchips"', 'class="prompt"', ">cause<", 'class="stepchip"', "START HERE",
                         "Trajectory map", "Where the seconds went", "Reward &amp; credit"):
                self.assertIn(want, page)
            over = app.handle(Request("GET", "/", h)).body.decode()
            self.assertIn("Levels · the agents", over)
            self.assertIn("95% Wilson", over)
