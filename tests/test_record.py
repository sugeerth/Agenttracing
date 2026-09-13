"""Tests for the live recorder (deepcompare.record).

A recorder is trusted infrastructure: everything downstream is computed from
what it wrote, so the properties worth pinning are the ones where it could
quietly produce a *plausible* trace rather than a true one — an estimate
written as a measurement, a termination reason nobody declared, a failed run
that vanishes because the exception ate the file, or an invalid trace that
reaches disk and poisons a batch later.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.cli import _run_id_from_name
from deepcompare.process import analyse
from deepcompare.record import (
    NAME_SEP,
    Recorder,
    estimate_tokens,
    render_call,
    usage_from_response,
)
from deepcompare.tooldiff import parse_args

TOOLS = [
    {"name": "get_booking", "effect": "read",
     "parameters": {"properties": {"reference": {"type": "string"}},
                    "required": ["reference"]}},
    {"name": "cancel_booking", "effect": "write",
     "parameters": {"properties": {"reference": {"type": "string"},
                                   "refund": {"type": "boolean"}},
                    "required": ["reference"]}},
]


class RecorderCase(unittest.TestCase):
    """Base: every recorder writes into a throwaway directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def recorder(self, **kwargs) -> Recorder:
        params = {"task": "t01_refund", "prompt": "Cancel booking QX7T2",
                  "agent": "my-agent", "model": "claude-sonnet-5",
                  "out_dir": self.out}
        params.update(kwargs)
        return Recorder(**params)

    def written(self, run: Recorder) -> dict:
        self.assertIsNotNone(run.path, "the run wrote no file")
        return json.loads(Path(run.path).read_text(encoding="utf-8"))


class TestStepTypes(RecorderCase):
    """Every step type the schema allows must be reachable and valid."""

    def test_every_step_type_produces_a_valid_trace(self):
        with self.recorder(tools=TOOLS) as run:
            run.plan("Read the booking, then cancel")
            run.search("refund policy QX7T2", output="3 results")
            run.retrieve("policy.md", output="Refunds within 24h")
            run.read("policy.md", output="Refunds within 24h")
            run.tool("get_booking", {"reference": "QX7T2"}).observe("{ok}")
            run.reason("It is refundable")
            run.step("tool_call", "cancel_booking",
                     "cancel_booking(reference='QX7T2')", "cancelled",
                     effect="write")
            run.answer("Cancelled and refunded.", success=True, score=1.0)

        trajectory = Trajectory.from_json(run.path)
        self.assertEqual(
            [step.type for step in trajectory.steps],
            ["plan", "search", "retrieve", "read", "tool_call", "reason",
             "tool_call", "answer"],
        )
        self.assertTrue(trajectory.outcome.success)
        self.assertEqual(trajectory.outcome.answer, "Cancelled and refunded.")
        self.assertEqual(trajectory.outcome.score, 1.0)

    def test_tool_arguments_are_recorded_in_the_parseable_dialect(self):
        with self.recorder(out_dir=None) as run:
            run.tool("cancel_booking", {"reference": "QX7T2", "refund": True,
                                        "seats": 2})
            run.answer("done", success=True)
        step = run.to_dict()["steps"][0]
        self.assertEqual(step["input"],
                         "cancel_booking(reference='QX7T2', refund=true, seats=2)")
        # The whole tool-diff / schema / provenance stack reads through this.
        self.assertEqual(parse_args(step["input"]),
                         {"reference": "QX7T2", "refund": "true", "seats": "2"})

    def test_render_call_keeps_values_with_quotes_intact(self):
        rendered = render_call("note", {"text": "it's fine"})
        self.assertEqual(parse_args(rendered)["text"], "it's fine")

    def test_tool_can_run_the_callable_and_record_both_sides(self):
        with self.recorder(out_dir=None) as run:
            result = run.tool("get_booking", {"reference": "QX7T2"},
                              call=lambda reference: f"booking {reference}")
            run.answer("done", success=True)
        self.assertEqual(result, "booking QX7T2")
        step = run.to_dict()["steps"][0]
        self.assertEqual(step["output"], "booking QX7T2")

    def test_observe_fills_the_most_recent_step(self):
        with self.recorder(out_dir=None) as run:
            run.tool("get_booking", {"reference": "QX7T2"})
            run.observe({"refundable": True}, error=False)
            run.answer("done", success=True)
        step = run.to_dict()["steps"][0]
        self.assertEqual(json.loads(step["output"]), {"refundable": True})
        self.assertIs(step["error"], False)


class TestFilenames(RecorderCase):
    """Filenames are an interface: the CLI parses run ids back out of them."""

    def test_single_run_filename(self):
        with self.recorder() as run:
            run.answer("done", success=True)
        self.assertEqual(Path(run.path).name, "t01_refund__my-agent.json")
        self.assertIsNone(_run_id_from_name(Path(run.path)))

    def test_run_id_produces_the_multi_run_filename(self):
        paths = []
        for index in (1, 2, 3):
            with self.recorder(run_id=f"r{index}") as run:
                run.plan("go")
                run.answer("done", success=True)
            paths.append(Path(run.path))
        self.assertEqual([p.name for p in paths],
                         ["t01_refund__my-agent__r1.json",
                          "t01_refund__my-agent__r2.json",
                          "t01_refund__my-agent__r3.json"])
        self.assertEqual([_run_id_from_name(p) for p in paths],
                         ["r1", "r2", "r3"])
        self.assertEqual(Trajectory.from_json(paths[1]).run_id, "r2")

    def test_no_temporary_file_is_left_behind(self):
        with self.recorder() as run:
            run.answer("done", success=True)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["t01_refund__my-agent.json"])

    def test_out_dir_none_records_without_writing(self):
        with self.recorder(out_dir=None) as run:
            run.answer("done", success=True)
        self.assertIsNone(run.path)
        self.assertEqual(list(self.out.iterdir()), [])
        Trajectory.from_json(run.to_dict())


class TestTermination(RecorderCase):
    """Termination is declared or observed, never deduced from the shape."""

    def test_clean_exit_records_agent_stop(self):
        with self.recorder() as run:
            run.answer("done", success=True)
        self.assertEqual(self.written(run)["outcome"]["termination"], "agent_stop")

    def test_exception_still_writes_a_valid_trace(self):
        with self.assertRaises(RuntimeError):
            with self.recorder(tools=TOOLS) as run:
                run.plan("Read the booking, then cancel")
                run.tool("get_booking", {"reference": "QX7T2"}).observe("{}")
                raise RuntimeError("boom")

        data = self.written(run)
        trajectory = Trajectory.from_json(run.path)   # valid despite the crash
        self.assertEqual(data["outcome"]["termination"], "agent_error")
        self.assertFalse(data["outcome"]["success"])
        self.assertEqual(trajectory.steps[-1].type, "answer")
        self.assertIn("RuntimeError: boom", trajectory.outcome.answer)
        # The synthesised step says it is the recorder's, not the agent's.
        self.assertIn("recorder", trajectory.steps[-1].note)
        # The work done before the crash survives.
        self.assertEqual([s.name for s in trajectory.steps][:2],
                         ["plan", "get_booking"])

    def test_timeout_and_interrupt_have_their_own_reasons(self):
        with self.assertRaises(TimeoutError):
            with self.recorder(run_id="r1") as run:
                raise TimeoutError("too slow")
        self.assertEqual(self.written(run)["outcome"]["termination"], "timeout")

        with self.assertRaises(KeyboardInterrupt):
            with self.recorder(run_id="r2") as run:
                raise KeyboardInterrupt()
        self.assertEqual(self.written(run)["outcome"]["termination"], "user_stop")

    def test_caller_declares_max_steps_and_it_wins(self):
        with self.recorder(budget={"max_steps": 2}) as run:
            run.plan("a")
            run.terminate("max_steps")
            run.answer("ran out of steps", success=False)
        data = self.written(run)
        self.assertEqual(data["outcome"]["termination"], "max_steps")
        self.assertEqual(data["budget"], {"max_steps": 2})
        # A declared reason survives an exception too.
        with self.assertRaises(RuntimeError):
            with self.recorder(run_id="r9") as other:
                other.terminate("infrastructure_error")
                raise RuntimeError("rate limited")
        self.assertEqual(self.written(other)["outcome"]["termination"],
                         "infrastructure_error")

    def test_a_recorded_answer_is_not_overwritten_by_a_later_crash(self):
        with self.assertRaises(RuntimeError):
            with self.recorder() as run:
                run.answer("Cancelled and refunded.", success=True)
                raise RuntimeError("teardown blew up")
        data = self.written(run)
        self.assertTrue(data["outcome"]["success"])
        self.assertEqual(data["outcome"]["answer"], "Cancelled and refunded.")
        self.assertEqual(data["outcome"]["termination"], "agent_error")

    def test_no_answer_is_recorded_as_no_answer_not_as_success(self):
        with self.recorder() as run:
            run.plan("thinking about it")
        data = self.written(run)
        self.assertFalse(data["outcome"]["success"])
        self.assertIn("no answer", data["outcome"]["answer"])
        self.assertEqual(data["steps"][-1]["type"], "answer")
        self.assertEqual(data["outcome"]["termination"], "agent_stop")


class TestTokens(RecorderCase):
    """An estimate must never be readable as a measurement."""

    def test_estimated_tokens_are_marked_estimated(self):
        with self.recorder() as run:
            run.plan("Read the booking, then cancel")
            run.answer("done", success=True)
        data = self.written(run)
        self.assertTrue(all(step["tokens_basis"] == "estimated"
                            for step in data["steps"]))
        self.assertEqual(data["token_accounting"]["basis"], "estimated")
        self.assertEqual(data["token_accounting"]["estimated_steps"], 2)
        self.assertEqual(data["token_accounting"]["measured_steps"], 0)
        self.assertEqual(data["token_accounting"]["input_tokens_basis"], "estimated")
        self.assertEqual(data["token_accounting"]["estimator"], "len(text)/4")

    def test_estimate_counts_the_observation_that_arrived_later(self):
        with self.recorder(out_dir=None) as run:
            call = run.tool("get_booking", {"reference": "QX7T2"})
            call.observe("a fairly long observation about the booking")
            run.answer("done", success=True)
        step = run.to_dict()["steps"][0]
        self.assertEqual(
            step["tokens"],
            estimate_tokens(step["input"] + " " + step["output"]))

    def test_supplied_counts_are_marked_measured_and_kept(self):
        with self.recorder(input_tokens=1200) as run:
            run.reason("some thinking", tokens=57)
            run.answer("done", success=True, tokens=9)
        data = self.written(run)
        self.assertEqual([step["tokens"] for step in data["steps"]], [57, 9])
        self.assertTrue(all(step["tokens_basis"] == "measured"
                            for step in data["steps"]))
        self.assertEqual(data["token_accounting"]["basis"], "measured")
        self.assertEqual(data["token_accounting"]["input_tokens_basis"], "measured")
        self.assertEqual(data["totals"]["input_tokens"], 1200)
        self.assertEqual(data["totals"]["output_tokens"], 66)

    def test_a_mix_is_reported_as_mixed(self):
        with self.recorder() as run:
            run.reason("measured", tokens=40)
            run.answer("estimated", success=True)
        accounting = self.written(run)["token_accounting"]
        self.assertEqual(accounting["basis"], "mixed")
        self.assertEqual((accounting["measured_steps"],
                          accounting["estimated_steps"]), (1, 1))

    def test_timing_is_captured_per_step_and_summed(self):
        with self.recorder() as run:
            run.plan("go")
            run.answer("done", success=True)
        data = self.written(run)
        self.assertTrue(all(step["latency_s"] >= 0 for step in data["steps"]))
        self.assertAlmostEqual(data["totals"]["latency_s"],
                               sum(step["latency_s"] for step in data["steps"]),
                               places=6)

    def test_explicit_latency_is_kept_verbatim(self):
        with self.recorder(out_dir=None) as run:
            run.reason("thinking", latency_s=2.5)
            run.answer("done", success=True, latency_s=0.5)
        self.assertEqual([s["latency_s"] for s in run.to_dict()["steps"]],
                         [2.5, 0.5])
        self.assertEqual(run.to_dict()["totals"]["latency_s"], 3.0)

    def test_cost_accumulates(self):
        with self.recorder(out_dir=None) as run:
            run.reason("thinking", cost_usd=0.002)
            run.add_cost(0.001)
            run.answer("done", success=True)
        self.assertAlmostEqual(run.to_dict()["totals"]["cost_usd"], 0.003)


class TestProcessMeasurable(RecorderCase):
    """A recorded run must arrive measurable, not merely valid."""

    def test_declared_tools_and_effects_reach_the_analysis(self):
        with self.recorder(tools=TOOLS, budget={"max_steps": 10}) as run:
            run.plan("Read the booking, then cancel")
            run.tool("get_booking", {"reference": "QX7T2"}).observe("refundable")
            run.tool("cancel_booking", {"reference": "QX7T2", "refund": True},
                     "cancelled")
            run.answer("Cancelled and refunded.", success=True)

        result = analyse(Trajectory.from_json(run.path))
        # The three checks that are unmeasurable without a declared tool list.
        self.assertTrue(result["schema"]["measurable"])
        self.assertEqual(result["schema"]["violations"], 0)
        self.assertEqual(result["grounding"]["schema_grounding"], 1.0)
        self.assertTrue(result["false_success"]["measurable"])
        # Effects declared on the tools reach the steps, so the write ledger
        # is evidence rather than a guess from the tool name.
        self.assertEqual(result["side_effects"]["writes"], 1)
        self.assertEqual(result["side_effects"]["writes_before_any_read"], 0)
        self.assertEqual(result["side_effects"]["basis"], "declared")
        self.assertEqual(result["termination"]["reason"], "agent_stop")
        self.assertTrue(result["termination"]["declared"])
        self.assertEqual(result["gap"]["verdict"], "passed cleanly")

    def test_declared_errors_beat_the_text_heuristic(self):
        with self.recorder(tools=TOOLS, out_dir=None) as run:
            # Output that reads fine but was a failure, and vice versa.
            run.tool("get_booking", {"reference": "QX7T2"}).observe(
                "no matching booking", error=False)
            run.tool("get_booking", {"reference": "QX7T3"}).observe(
                "booking found", error=True)
            run.answer("done", success=True)
        result = analyse(Trajectory.from_json(run.to_dict()))
        self.assertEqual(result["recovery"]["errors"], 1)
        self.assertEqual(result["recovery"]["basis"], "declared")
        self.assertEqual(result["recovery"]["error_steps"][0]["index"], 1)

    def test_an_undeclared_tool_call_is_recorded_not_blocked(self):
        with self.recorder(tools=TOOLS, out_dir=None) as run:
            run.tool("refund_card", {"amount": 120}, "refunded")
            run.answer("done", success=True)
        result = analyse(Trajectory.from_json(run.to_dict()))
        self.assertEqual(result["grounding"]["undeclared_tool_calls"], 1)
        self.assertTrue(result["gap"]["flags"]["undeclared_tools"])

    def test_effect_is_not_invented_when_nothing_declares_it(self):
        with self.recorder(out_dir=None) as run:   # no tools declared
            run.tool("do_thing", {"x": 1}, "ok")
            run.answer("done", success=True)
        step = run.to_dict()["steps"][0]
        self.assertIsNone(step["effect"])

    def test_two_recorded_runs_compare(self):
        for agent, answer in (("agent-a", "Cancelled"), ("agent-b", "Not cancelled")):
            with self.recorder(agent=agent, tools=TOOLS) as run:
                run.plan("Read the booking, then cancel")
                run.tool("get_booking", {"reference": "QX7T2"}).observe("refundable")
                run.answer(answer, success=agent == "agent-a")
        report = compare(
            Trajectory.from_json(self.out / "t01_refund__agent-a.json"),
            Trajectory.from_json(self.out / "t01_refund__agent-b.json"),
        )
        self.assertEqual(report["task"]["id"], "t01_refund")
        self.assertIn("alignment", report)
        self.assertIn("process", report)


class TestLoudFailures(RecorderCase):
    """Wrong input fails where the mistake is, and never reaches disk."""

    def test_invalid_step_type(self):
        with self.assertRaises(ValueError) as caught:
            self.recorder(out_dir=None).step("think", "x")
        self.assertIn("invalid step type", str(caught.exception))
        self.assertIn("tool_call", str(caught.exception))

    def test_invalid_effect_quality_and_termination(self):
        run = self.recorder(out_dir=None)
        with self.assertRaises(ValueError):
            run.tool("t", {"a": 1}, effect="destroy")
        with self.assertRaises(ValueError):
            run.reason("x", quality="excellent")
        with self.assertRaises(ValueError) as caught:
            run.terminate("gave_up")
        self.assertIn("agent_stop", str(caught.exception))

    def test_answer_requires_an_explicit_verdict(self):
        run = self.recorder(out_dir=None)
        with self.assertRaises(ValueError) as caught:
            run.answer("done")
        self.assertIn("success=True", str(caught.exception))

    def test_answer_may_only_be_recorded_once(self):
        run = self.recorder(out_dir=None)
        run.answer("done", success=True)
        with self.assertRaises(ValueError):
            run.answer("done again", success=True)
        with self.assertRaises(ValueError) as caught:
            run.plan("more thinking")
        self.assertIn("last step", str(caught.exception))

    def test_identity_fields_are_validated_at_construction(self):
        for kwargs in ({"task": ""}, {"agent": ""}, {"prompt": "  "},
                       {"agent": "my__agent"}, {"task": "a/b"},
                       {"run_id": "r__1"}):
            with self.assertRaises(ValueError):
                self.recorder(**kwargs)

    def test_the_filename_separator_error_explains_itself(self):
        with self.assertRaises(ValueError) as caught:
            self.recorder(agent="my__agent")
        self.assertIn(NAME_SEP, str(caught.exception))
        self.assertIn("run id", str(caught.exception))

    def test_malformed_tools_and_budget_fail_at_construction(self):
        with self.assertRaises(ValueError):
            self.recorder(tools=[{"effect": "read"}])          # no name
        with self.assertRaises(ValueError):
            self.recorder(tools=[{"name": "t", "effect": "delete"}])
        with self.assertRaises(ValueError):
            self.recorder(tools=[{"name": "t", "parameters": "reference"}])
        with self.assertRaises(ValueError):
            self.recorder(budget={"max_steps": "twenty"})
        self.assertEqual(list(self.out.iterdir()), [])

    def test_observe_without_a_step_says_what_to_do(self):
        run = self.recorder(out_dir=None)
        with self.assertRaises(ValueError) as caught:
            run.observe("a result")
        self.assertIn("run.tool", str(caught.exception))

    def test_observing_the_handle_instead_of_the_result_is_caught(self):
        run = self.recorder(out_dir=None)
        handle = run.tool("get_booking", {"reference": "QX7T2"})
        with self.assertRaises(TypeError) as caught:
            run.observe(handle)
        self.assertIn("handle.observe(result)", str(caught.exception))

    def test_an_invalid_trace_is_never_written(self):
        run = self.recorder()
        run.plan("go")
        run._steps[0]["type"] = "ruminate"      # as if something corrupted it
        with self.assertRaises(ValueError) as caught:
            run.close(success=True, answer="done")
        self.assertIn("refused to write", str(caught.exception))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_closed_recorder_refuses_more_work(self):
        run = self.recorder(out_dir=None)
        run.answer("done", success=True)
        run.close()
        with self.assertRaises(ValueError):
            run.close()
        with self.assertRaises(ValueError):
            run.reason("more")


class TestProviderResponses(unittest.TestCase):
    """The provider helper is duck-typed: no SDK is installed here."""

    OPENAI = {
        "model": "gpt-x",
        "choices": [{"message": {"role": "assistant", "content": "Paris"},
                     "logprobs": {"content": [
                         {"token": "Paris", "logprob": -0.1,
                          "top_logprobs": [{"token": "Paris", "logprob": -0.1},
                                           {"token": "Lyon", "logprob": -2.5}]}]}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 3},
    }

    def test_openai_shaped_dict(self):
        usage = usage_from_response(self.OPENAI)
        self.assertEqual(usage["text"], "Paris")
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (100, 3))
        self.assertEqual(usage["telemetry"]["entropy_basis"], "top_k")
        self.assertAlmostEqual(usage["telemetry"]["confidence"], 0.9048, places=3)

    def test_anthropic_shaped_object(self):
        response = SimpleNamespace(
            model="claude-sonnet-5",
            content=[SimpleNamespace(type="text", text="Cancelled.")],
            usage=SimpleNamespace(input_tokens=42, output_tokens=7),
        )
        usage = usage_from_response(response)
        self.assertEqual(usage["text"], "Cancelled.")
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (42, 7))
        self.assertIsNone(usage["telemetry"])

    def test_ollama_shaped_dict(self):
        usage = usage_from_response({"message": {"content": "hi"},
                                     "prompt_eval_count": 11, "eval_count": 2})
        self.assertEqual((usage["text"], usage["input_tokens"],
                          usage["output_tokens"]), ("hi", 11, 2))

    def test_nothing_usable_invents_nothing(self):
        usage = usage_from_response({"weird": True})
        self.assertEqual(usage, {"text": None, "input_tokens": None,
                                 "output_tokens": None, "model": None,
                                 "telemetry": None})

    def test_a_response_makes_the_step_measured(self):
        with Recorder(task="t", prompt="Where?", agent="a", out_dir=None) as run:
            run.reason("thinking", response=self.OPENAI)
            run.answer("Paris", success=True)
        data = run.to_dict()
        step = data["steps"][0]
        self.assertEqual(step["output"], "Paris")
        self.assertEqual(step["tokens"], 3)
        self.assertEqual(step["tokens_basis"], "measured")
        self.assertEqual(step["model"]["source"], "provider-logprobs")
        # Prompt tokens the server reported beat the len/4 estimate.
        self.assertEqual(data["totals"]["input_tokens"], 100)
        self.assertEqual(data["token_accounting"]["input_tokens_basis"], "measured")

    def test_an_explicit_output_wins_over_the_response_text(self):
        with Recorder(task="t", prompt="Where?", agent="a", out_dir=None) as run:
            run.reason("thinking", output="just the last block",
                       response=self.OPENAI)
            run.answer("Paris", success=True)
        self.assertEqual(run.to_dict()["steps"][0]["output"], "just the last block")


class TestInstrument(RecorderCase):
    """The decorator form: instrument the tool once, leave the loop alone."""

    def test_arguments_are_recorded_by_name_with_the_result(self):
        with self.recorder(tools=TOOLS, out_dir=None) as run:

            @run.instrument(effect="write")
            def cancel_booking(reference, refund=False):
                return {"cancelled": reference, "refund": refund}

            cancel_booking("QX7T2", refund=True)
            run.answer("done", success=True)

        step = run.to_dict()["steps"][0]
        self.assertEqual(step["name"], "cancel_booking")
        self.assertEqual(parse_args(step["input"]),
                         {"reference": "QX7T2", "refund": "true"})
        self.assertEqual(step["effect"], "write")
        self.assertEqual(json.loads(step["output"])["cancelled"], "QX7T2")

    def test_async_tools_are_awaited_before_they_are_recorded(self):
        import asyncio

        with self.recorder(out_dir=None) as run:

            @run.instrument(effect="read")
            async def get_booking(reference):
                return {"reference": reference, "refundable": True}

            asyncio.run(get_booking("QX7T2"))
            run.answer("done", success=True)

        step = run.to_dict()["steps"][0]
        self.assertEqual(json.loads(step["output"])["reference"], "QX7T2")
        self.assertNotIn("coroutine", step["output"])

    def test_an_un_awaited_result_is_refused_rather_than_recorded(self):
        async def get_booking(reference):
            return reference

        run = self.recorder(out_dir=None)
        with self.assertRaises(TypeError) as caught:
            run.tool("get_booking", {"reference": "QX7T2"}, call=get_booking)
        self.assertIn("await it", str(caught.exception))

    def test_a_raising_tool_is_recorded_as_an_error_and_still_raises(self):
        with self.assertRaises(RuntimeError):
            with self.recorder(out_dir=None) as run:

                @run.instrument()
                def send_email(to):
                    raise RuntimeError("smtp down")

                send_email("x@y.z")

        data = run.to_dict()
        step = data["steps"][0]
        self.assertTrue(step["error"])
        self.assertIn("smtp down", step["output"])
        self.assertEqual(data["outcome"]["termination"], "agent_error")


if __name__ == "__main__":   # pragma: no cover
    unittest.main()


class CheckpointTest(unittest.TestCase):
    def test_checkpoint_writes_the_run_so_far_beside_the_trace(self):
        import json as _json
        import tempfile as _tempfile
        from pathlib import Path as _Path
        with _tempfile.TemporaryDirectory() as tmp:
            with Recorder(task="t1", prompt="p", agent="bot", expected="4", out_dir=tmp) as rec:
                rec.plan("add")
                path = rec.checkpoint("after the plan")
                self.assertTrue(path.name.endswith(".ckpt-1.json"))
                data = _json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(data["in_progress"])
                self.assertEqual(data["checkpoint"]["label"], "after the plan")
                self.assertEqual(len(data["steps"]), 1)
                rec.answer("4", success=True)
            self.assertTrue((_Path(tmp) / "t1__bot.json").is_file())


