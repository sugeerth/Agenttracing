"""The agent's loop, lap by lap, and the charts that draw it.

The three synthetic runs in ``demo/loops`` are the cases a reader needs
told apart: one converges on lap 3, one does the same lap five times and
never passes, one tries something different every lap and never passes.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.hub import viz
from agentdiff.laps import check_outcome, is_check, laps

ROOT = Path(__file__).resolve().parent.parent
LOOPS = ROOT / "demo" / "loops" / "traces"


def _load(name: str) -> dict:
    return json.loads((LOOPS / f"parse_duration__agent-{name}.json").read_text(encoding="utf-8"))


class LapsTest(unittest.TestCase):
    def test_a_run_that_converges_passes_on_its_third_lap_and_is_not_called_stuck(self):
        r = laps(_load("converges"))
        self.assertEqual((r["basis"], r["count"], r["first_pass_lap"], r["checks_failed"]), ("checks", 3, 3, 2))
        self.assertNotIn("stuck:", r["summary"])

    def test_a_run_that_does_the_same_lap_again_is_stuck(self):
        r = laps(_load("stuck"))
        self.assertEqual(r["count"], 5)
        self.assertIsNone(r["first_pass_lap"])
        self.assertEqual(r["repeated_laps"], [3, 4, 5])
        self.assertEqual(r["longest_repeat_run"], 3)
        self.assertIn("stuck:", r["summary"])

    def test_a_run_that_flails_never_repeats_and_never_passes(self):
        r = laps(_load("flails"))
        self.assertEqual((r["count"], r["repeated_laps"], r["first_pass_lap"]), (4, [], None))

    def test_a_lap_that_edits_another_line_is_not_a_repeat(self):
        steps = []
        for k in range(3):
            steps += [{"type": "tool_call", "name": "Edit", "input": f"line {k}"},
                      {"type": "tool_call", "name": "Bash", "input": "pytest -q", "output": "1 failed"}]
        r = laps({"steps": steps})
        self.assertEqual(r["repeated_laps"], [], "same tools, different calls")

    def test_a_run_that_never_checks_is_cut_at_its_turns(self):
        steps = [{"type": "reason", "name": "think"}, {"type": "tool_call", "name": "Read"},
                 {"type": "reason", "name": "think"}, {"type": "tool_call", "name": "Write"},
                 {"type": "answer", "name": "answer"}]
        r = laps({"steps": steps})
        self.assertEqual((r["basis"], r["count"]), ("turns", 2))
        self.assertIn("no step ran a test suite", r["summary"])

    def test_check_outcomes_are_read_from_what_the_check_said(self):
        self.assertTrue(is_check({"type": "tool_call", "name": "run_tests"}))
        self.assertFalse(check_outcome({"output": "2 failed, 10 passed"}))
        self.assertTrue(check_outcome({"output": "12 passed, 0 failed"}))
        self.assertFalse(check_outcome({"output": "12 passed", "error": "exit 1"}), "an errored step failed")
        self.assertIsNone(check_outcome({"output": "done"}))

    def test_transitions_count_every_move_between_tools(self):
        r = laps(_load("stuck"))
        moves = {(t["from"], t["to"]): t["count"] for t in r["transitions"]}
        self.assertEqual(moves[("Edit", "Bash")], 5)
        self.assertEqual(moves[("Bash", "Edit")], 4)


class VizTest(unittest.TestCase):
    def test_every_chart_is_svg_with_a_legend_and_titles(self):
        r = laps(_load("stuck"))
        for html in (viz.lap_chart(r), viz.flow_ring(r)):
            self.assertIn("<svg", html)
            self.assertIn('class="legend"', html, "identity is never colour alone")
            self.assertIn("<title>", html)
        self.assertIn("same as lap 2", viz.lap_chart(r))
        self.assertIn("edge cyc", viz.flow_ring(r), "the Edit/Bash cycle is drawn as one")

    def test_what_a_trace_says_reaches_a_chart_escaped(self):
        hostile = {"steps": [{"type": "tool_call", "name": "<script>alert(1)</script>", "input": "x"},
                             {"type": "tool_call", "name": "Bash", "input": "\"><img onerror=1>"}]}
        r = laps(hostile)
        for html in (viz.lap_chart(r), viz.flow_ring(r), viz.lap_table(r), viz.step_ribbon(hostile["steps"])):
            self.assertNotIn("<script>", html)
            self.assertNotIn("<img", html)

    def test_the_strip_keeps_a_mark_per_lap(self):
        c = viz.compact_laps(laps(_load("stuck")))
        self.assertEqual(c["marks"], [["f", False], ["f", False], ["f", True], ["f", True], ["f", True]])
        self.assertTrue(c["stuck"])
        self.assertEqual(len(re.findall(r'class="a2"', viz.lap_strip(c))), 3, "a bar under each repeat")

    def test_the_eval_river_draws_every_generation(self):
        data = {"lineage": [{"generation": g, "arrived": i, "wrong": 2, "runs": 5,
                             "forward": {"coverage": 0.5 if i else None, "caught": 1, "false_alarms": 0}}
                            for i, g in enumerate(("g0", "g1"))],
                "evals": [{"id": "e1", "status": "active", "history": [
                    {"generation": "g0", "verdict": "born", "caught": 2, "wrong": 2, "false_alarms": 0},
                    {"generation": "g1", "verdict": "holds", "caught": 1, "wrong": 2, "false_alarms": 0}]}]}
        html = viz.eval_river(data)
        self.assertIn("g0", html)
        self.assertIn("50%", html)
        self.assertIn("e1", html)

    def test_the_hop_timeline_marks_a_hop_that_failed(self):
        rows = [{"tool": "pytest", "node": "n1", "start": 0.0, "latency": 1.0, "status": "error", "kind": "tool_call"}]
        self.assertIn('class="err"', viz.hop_timeline(rows, 1.0))


if __name__ == "__main__":
    unittest.main()
