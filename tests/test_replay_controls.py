"""Replay controls (harness.replay): every corrected replay is matched by the same prefix replayed with nothing
changed. A step is verified only when the correction flips at least half its replays and beats the controls by
half the replays; a control that flips is the failure not reproducing."""

from __future__ import annotations

import itertools
import sys
import unittest
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.harness import ScriptedProvider, run_task  # noqa: E402
from agentdiff.harness.replay import replay, replay_verdict  # noqa: E402
from test_harness_agents import TASK, refund_tool  # noqa: E402


def _reads_the_tool(messages, tools):
    """Answers with whatever amount the last tool result carried; asks the tool first."""
    import re
    for m in reversed(messages):
        if m["role"] == "tool" and "$" in m["content"]:
            return {"text": f"The refund for BK1 is {re.search(r'[$][0-9,]+[.][0-9][0-9]', m['content']).group(0)}."}
    return {"tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK1"}}]}


def _always_right(messages, tools):
    return {"text": "The refund for BK1 is $120.00."}


def _failing_trace():
    trace = run_task(ScriptedProvider(_reads_the_tool, model="echo"), TASK, [refund_tool("$90.00")],
                     agent="bad", out_dir=None)
    assert trace["outcome"]["success"] is False
    step = next(i for i, s in enumerate(trace["steps"]) if s["type"] == "tool_call")
    return trace, step


CORRECTION = {"output": "{'reference': 'BK1', 'refund': '$120.00'}"}


class VerdictTest(unittest.TestCase):
    def test_the_rule(self):
        self.assertEqual(replay_verdict(3, 3, 0, 3), "replay-verified")
        self.assertEqual(replay_verdict(3, 3, 2, 3), "replay-mixed", "3/3 against 2/3 is not half the replays")
        self.assertEqual(replay_verdict(2, 3, 0, 3), "replay-verified")
        self.assertEqual(replay_verdict(0, 3, 3, 3), "replay-refuted")
        self.assertEqual(replay_verdict(3, 3, 3, 0), "replay-verified", "uncontrolled: the old rule")

    def test_the_false_verification_rates_the_docs_quote(self):
        """A correction that changes nothing succeeds as often as the control (0.32 here, the failure
        reproducing 68% of the time): with 3 replays each, verified 0.242 of the time without controls and
        0.091 with them."""
        def p(n, k, q):
            return comb(n, k) * q ** k * (1 - q) ** (n - k)
        b = 0.32
        old = sum(p(3, x, b) for x in range(4) if replay_verdict(x, 3) == "replay-verified")
        new = sum(p(3, x, b) * p(3, y, b) for x, y in itertools.product(range(4), repeat=2)
                  if replay_verdict(x, 3, y, 3) == "replay-verified")
        self.assertAlmostEqual(old, 0.242, places=3)
        self.assertAlmostEqual(new, 0.091, places=3)


class ReplayTest(unittest.TestCase):
    def test_a_decisive_correction_beats_controls_that_fail_again(self):
        trace, step = _failing_trace()
        out = replay(trace, None, [refund_tool("$90.00")], step, CORRECTION, replays=3,
                     provider_factory=lambda: ScriptedProvider(_reads_the_tool, model="echo"))
        self.assertEqual((out["flipped"], out["controls"], out["control_flipped"]), (3, 3, 0))
        self.assertEqual(out["verdict"], "replay-verified")
        self.assertEqual(len(out["control_runs"]), 3)
        notes = [s.get("note") for s in out["control_runs"][0]["trace"]["steps"]]
        self.assertNotIn("counterfactual correction", notes, "a control changes nothing")

    def test_an_outcome_reached_without_the_correction_is_not_credited_to_it(self):
        trace, step = _failing_trace()
        out = replay(trace, None, [refund_tool("$90.00")], step, CORRECTION, replays=3,
                     provider_factory=lambda: ScriptedProvider(_always_right, model="echo"))
        self.assertEqual((out["flipped"], out["control_flipped"]), (3, 3))
        self.assertEqual(out["verdict"], "replay-mixed")
        uncontrolled = replay(trace, None, [refund_tool("$90.00")], step, CORRECTION, replays=3, controls=0,
                              provider_factory=lambda: ScriptedProvider(_always_right, model="echo"))
        self.assertEqual(uncontrolled["verdict"], "replay-verified")
        self.assertIn("uncontrolled", uncontrolled["note"])
        self.assertIsNone(uncontrolled["control_flip_rate"])


if __name__ == "__main__":
    unittest.main()
