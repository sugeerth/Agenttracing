"""Counterfactual replay and the causal window: a decisive-step claim is a
hypothesis until re-execution confirms it.

Scripted providers that *read* the conversation stand in for models, so
the replays are deterministic and the verdict logic is tested for real:
a correction that changes what the model sees flips the outcome, an
irrelevant one does not, and the engine's own decisive_step carries the
window, the verification label and the recipe the replay consumes.
"""

from __future__ import annotations

import json
import re
import unittest

from deepcompare.harness import ScriptedProvider, Tool, run_task
from deepcompare.harness.replay import messages_from_steps, replay
from deepcompare.report import compare
from deepcompare.trace import Trajectory

TASK = {"id": "t_refund", "prompt": "What refund applies to booking BK1?",
        "expected": "$120.00"}


def broken_tool(amount="$90.00"):
    def get_refund(reference: str):
        return {"reference": reference, "refund": amount}
    return Tool("get_refund", get_refund, "refund lookup",
                {"type": "object", "properties": {"reference": {"type": "string"}},
                 "required": ["reference"]}, effect="read")


def echoing_model(messages, tools):
    """A 'model' that answers with whatever refund the last tool result
    reported — so correcting the tool result is exactly what flips it."""
    for m in reversed(messages):
        if m["role"] == "tool":
            found = re.search(r"\$[\d,]+\.\d\d", m["content"])
            if found:
                return {"text": f"The refund for BK1 is {found.group(0)}."}
    return {"tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK1"}}]}


def stubborn_model(messages, tools):
    return {"text": "The refund for BK1 is $90.00."}


class TestReplayVerdicts(unittest.TestCase):
    def failing_trace(self):
        return run_task(ScriptedProvider(echoing_model, model="echo"), TASK,
                        [broken_tool()], out_dir=None)

    def test_correcting_the_decisive_tool_result_verifies_the_step(self):
        trace = self.failing_trace()
        self.assertFalse(trace["outcome"]["success"])
        tool_idx = next(i for i, s in enumerate(trace["steps"])
                        if s["type"] == "tool_call")
        result = replay(trace, ScriptedProvider(echoing_model, model="echo"),
                        [broken_tool()], tool_idx,
                        {"output": '{"reference": "BK1", "refund": "$120.00"}'},
                        replays=3,
                        provider_factory=lambda: ScriptedProvider(echoing_model, model="echo"))
        self.assertEqual(result["verdict"], "replay-verified")
        self.assertEqual(result["flipped"], 3)
        self.assertEqual(result["flip_rate"], 1.0)
        # every replay is a full, comparable trajectory: prefix marked,
        # correction marked, answer re-derived
        rerun = result["runs"][0]["trace"]
        notes = [s.get("note") for s in rerun["steps"]]
        self.assertIn("replayed prefix", notes) if tool_idx > 0 else None
        self.assertIn("counterfactual correction", notes)
        self.assertTrue(rerun["outcome"]["success"])
        Trajectory.from_dict(rerun)

    def test_an_irrelevant_correction_is_refuted(self):
        trace = run_task(ScriptedProvider(stubborn_model, model="stub"), TASK,
                         [broken_tool()], out_dir=None)
        result = replay(trace, ScriptedProvider(stubborn_model), [broken_tool()],
                        0, {"text": "Let me think harder."}, replays=2,
                        provider_factory=lambda: ScriptedProvider(stubborn_model))
        self.assertEqual(result["verdict"], "replay-refuted")
        self.assertEqual(result["flipped"], 0)

    def test_the_replay_never_replays_the_answer(self):
        trace = self.failing_trace()
        msgs = messages_from_steps(trace["steps"], TASK["prompt"], "sys")
        self.assertFalse(any("$90.00" in m["content"] and m["role"] == "assistant"
                             for m in msgs if m["role"] == "assistant"))
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["content"], TASK["prompt"])

    def test_rejects_corrections_without_substance(self):
        trace = self.failing_trace()
        with self.assertRaises(ValueError):
            replay(trace, ScriptedProvider(echoing_model), [], 0, {}, replays=1)
        with self.assertRaises(ValueError):
            replay(trace, ScriptedProvider(echoing_model), [], 99,
                   {"text": "x"}, replays=1)


class TestCausalWindow(unittest.TestCase):
    """The engine's decisive_step commits to a window, labels itself
    hypothesized, and hands the harness a recipe."""

    def diagnosis(self, a, b):
        return compare(Trajectory.from_json(a), Trajectory.from_json(b))["diagnosis"]

    def test_window_runs_from_the_anchor_to_the_last_account_step(self):
        d = self.diagnosis("demo/traces/t01_acme_revenue__atlas-v2.json",
                           "demo/traces/t01_acme_revenue__bolt-v3.json")
        dec = d["decisive_step"]
        self.assertIsNotNone(dec["step"])
        account = [e["step"] for e in d["causal_account"]]
        before_answer = [s for s in account if s < max(account)]
        self.assertEqual(dec["point_of_no_return"], max(before_answer))
        self.assertEqual(dec["window"]["earliest"], dec["step"])
        self.assertGreaterEqual(dec["point_of_no_return"], dec["step"])
        self.assertEqual(dec["window"]["steps"],
                         dec["point_of_no_return"] - dec["step"] + 1)

    def test_commitment_is_labelled_hypothesized_with_a_recipe(self):
        d = self.diagnosis("demo/traces/t05_flight_duration__atlas-v2.json",
                           "demo/traces/t05_flight_duration__bolt-v3.json")
        dec = d["decisive_step"]
        self.assertEqual(dec["verification"], "hypothesized")
        recipe = dec["replay_recipe"]
        self.assertEqual(recipe["step"], dec["step"])
        self.assertEqual(recipe["side"], d["subject"])
        self.assertIn("flips", recipe["expects"])

    def test_contested_lists_joint_candidates_instead_of_dropping_them(self):
        d = self.diagnosis("tests/fixtures/redteam/a3_garbage_args__fail.json",
                           "tests/fixtures/redteam/a3_garbage_args__pass.json")
        dec = d["decisive_step"]
        self.assertIsNone(dec["step"])
        self.assertIsNone(dec["verification"])
        self.assertTrue(dec["joint_candidates"])
        kinds = {c["kind"] for c in dec["joint_candidates"]}
        self.assertIn("environment_error", kinds)
        for c in dec["joint_candidates"]:
            self.assertIsInstance(c["step"], int)

    def test_abstentions_carry_no_window_and_no_recipe(self):
        d = self.diagnosis("demo/process/traces/p01_cancel_booking__steady-v1.json",
                           "demo/process/traces/p01_cancel_booking__hasty-v2.json")
        dec = d["decisive_step"]
        self.assertIsNone(dec["step"])
        self.assertIsNone(dec["window"])
        self.assertIsNone(dec["replay_recipe"])
        self.assertIn("grader", dec["reason"])


if __name__ == "__main__":
    unittest.main()
