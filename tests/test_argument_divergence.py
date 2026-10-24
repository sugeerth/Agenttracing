"""The argument rule (diagnosis._argument_divergence): two calls of the same tool that the aligner paired by their
shared name, with different arguments and a different result that the run then reasoned on, are where the runs
parted — not the reasoning step that read the result. A failed call, a result that changed nothing, and a result
the run never reasoned on leave the anchor where it was."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.report import compare  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

PLAN = {"type": "plan", "name": "plan", "input": "Read the record, verify the value, then answer.", "output": ""}
GOOD_READ = {"type": "tool_call", "name": "read_page", "input": "open support.example.com/sla (current)",
             "output": "Current terms: Gold SLA priority-1 response 9 hours."}
MIRROR = "open cache.example.com/sla-2024 (old mirror)"
BASIS = "the same tool called with different arguments, returning a different result"


def _traj(steps, ok, answer):
    return Trajectory.from_dict({
        "schema_version": 1, "trace_id": f"sla-{ok}", "agent": {"name": "good" if ok else "bad"},
        "task": {"id": "sla", "prompt": "What is the Gold SLA?", "expected": "9 hours"},
        "outcome": {"success": ok, "answer": answer}, "totals": {},
        "steps": [dict({"index": i, "tokens": 1, "latency_s": 0.1}, **s) for i, s in enumerate(steps)]})


def _diagnose(read_input, read_output, reasoning):
    bad = _traj([PLAN, {"type": "tool_call", "name": "read_page", "input": read_input, "output": read_output},
                 {"type": "reason", "name": "reason", "input": reasoning, "output": ""},
                 {"type": "answer", "name": "final", "input": "The Gold SLA is 4 hours.",
                  "output": "The Gold SLA is 4 hours."}], False, "The Gold SLA is 4 hours.")
    good = _traj([PLAN, GOOD_READ,
                  {"type": "reason", "name": "reason", "input": "Per the record just read, the value is 9 hours.",
                   "output": ""},
                  {"type": "answer", "name": "final", "input": "The Gold SLA is 9 hours.",
                   "output": "The Gold SLA is 9 hours."}], True, "The Gold SLA is 9 hours.")
    return compare(bad, good)["diagnosis"]["decisive_step"]


class ArgumentRuleTest(unittest.TestCase):
    def test_a_different_source_the_run_reasoned_on_is_the_decisive_step(self):
        dec = _diagnose(MIRROR, "Secondary source: an unofficial summary, priority-1 response 4 hours.",
                        "Going with the unofficial summary's account.")
        self.assertEqual(dec["step"], 1)
        self.assertEqual(dec["basis"], BASIS)

    def test_a_result_the_run_never_reasoned_on_leaves_the_anchor(self):
        dec = _diagnose(MIRROR, "Secondary source: an unofficial summary, priority-1 response 4 hours.",
                        "Going with the value as stated.")
        self.assertNotEqual(dec["basis"], BASIS)

    def test_a_failed_call_belongs_to_the_error_hypotheses(self):
        dec = _diagnose(MIRROR, "Error: unofficial page not found.", "Going with the unofficial summary's account.")
        self.assertNotEqual(dec["basis"], BASIS)

    def test_different_arguments_with_the_same_result_changed_nothing(self):
        dec = _diagnose("open support.example.com/sla?ref=mail", GOOD_READ["output"],
                        "Going with the current terms anyway.")
        self.assertNotEqual(dec["basis"], BASIS)


if __name__ == "__main__":
    unittest.main()
