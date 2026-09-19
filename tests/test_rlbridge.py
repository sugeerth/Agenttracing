"""The bridge to RL trainers, both directions.

In: veRL agent-loop rollouts and Agent Lightning span / event exports become
SCHEMA trajectories with the rewards on the steps and fidelity counters that
say what could not be mapped.  Out: a directory of reports becomes the
records a trainer reads — per-trajectory rewards for a veRL reward manager,
the compute_score template that serves them, DPO-style preference pairs,
per-step transitions — every record saying whether its reward was recorded
or shaped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.adapters import (
    detect_agent_lightning, detect_verl, from_agent_lightning, from_verl, register_formats,
)
from deepcompare.registry import convert as registry_convert, detect_format
from deepcompare.rlexport import (
    export, load_reports, to_agent_lightning_transitions, to_jsonl, to_preferences,
    to_verl_compute_score_template, to_verl_rewards,
)

ROOT = Path(__file__).resolve().parent.parent


def _verl_record(**extra) -> dict:
    record = {
        "data_source": "hotpotqa", "uid": "q17", "model": "qwen3-8b", "num_turns": 5,
        "messages": [
            {"role": "system", "content": "Answer with tools."},
            {"role": "user", "content": "What year was the Eiffel Tower completed?"},
            {"role": "assistant", "content": "I should search.",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "search", "arguments": "{\"q\": \"Eiffel Tower completed\"}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "Completed in 1889 for the World's Fair."},
            {"role": "assistant", "content": "It was completed in 1889."},
        ],
        "reward_model": {"ground_truth": "1889"},
        "tools": [{"type": "function", "function": {"name": "search"}}],
    }
    record.update(extra)
    return record


class FromVerlTest(unittest.TestCase):
    def test_turns_become_steps_and_tool_results_pair_by_call_id(self):
        traj, warnings = from_verl(_verl_record(reward_score=1.0))
        Trajectory.from_json(traj)
        types = [(s["type"], s["name"]) for s in traj["steps"]]
        self.assertEqual(types, [("reason", "reason"), ("search", "search"), ("answer", "final")])
        self.assertIn("Eiffel Tower", traj["steps"][1]["input"])
        self.assertIn("1889", traj["steps"][1]["output"])
        self.assertEqual(traj["task"]["prompt"], "Answer with tools.\nWhat year was the Eiffel Tower completed?")
        self.assertEqual(traj["outcome"]["answer"], "It was completed in 1889.")
        self.assertEqual(traj["tools"], [{"name": "search"}])
        self.assertEqual(warnings, [])

    def test_task_and_agent_naming(self):
        traj, _ = from_verl(_verl_record(reward_score=1.0))
        self.assertEqual(traj["task"]["id"], "hotpotqa-q17")
        self.assertEqual(traj["task"]["expected"], "1889")
        self.assertEqual(traj["agent"], {"name": "qwen3-8b", "model": "qwen3-8b", "version": "verl"})
        self.assertEqual(traj["trace_id"], "hotpotqa-q17-qwen3-8b")
        traj, _ = from_verl(_verl_record(reward_score=1.0), agent="policy-step-40")
        self.assertEqual(traj["agent"]["name"], "policy-step-40")
        self.assertEqual(traj["agent"]["model"], "qwen3-8b")
        rec = _verl_record(reward_score=1.0)
        del rec["model"]
        traj, warnings = from_verl(rec)
        self.assertEqual(traj["agent"]["name"], "verl-policy")
        self.assertTrue(any("--agent" in w for w in warnings))

    def test_episode_reward_falls_on_the_answer_step(self):
        traj, _ = from_verl(_verl_record(reward_score=1.0))
        rewards = [s.get("reward") for s in traj["steps"]]
        self.assertEqual(rewards, [None, None, 1.0])
        self.assertEqual(traj["outcome"]["score"], 1.0)
        self.assertEqual(traj["source"]["fidelity"]["rewards"], "episode")

    def test_per_turn_rewards_land_on_their_turns(self):
        traj, _ = from_verl(_verl_record(reward_scores={"score": 1.0}, turn_scores=[0.2, 0.8]))
        rewards = [s.get("reward") for s in traj["steps"]]
        # turn 1 = reason + search (the reward on the turn's last step), turn 2 = the answer
        self.assertEqual(rewards, [None, 0.2, 0.8])
        self.assertEqual(traj["outcome"]["score"], 1.0)
        self.assertEqual(traj["source"]["fidelity"]["rewards"], "per_turn")
        self.assertEqual(traj["source"]["reward_terms"]["per_turn_key"], "turn_scores")

    def test_tool_rewards_land_on_tool_steps_and_extra_fields_are_read(self):
        traj, _ = from_verl(_verl_record(reward_score=1.0, extra_fields={"tool_rewards": [0.5]}))
        self.assertEqual(traj["steps"][1]["reward"], 0.5)
        self.assertEqual(traj["source"]["fidelity"]["rewards"], "per_tool")

    def test_success_from_a_0_1_reward(self):
        self.assertTrue(from_verl(_verl_record(reward_score=1.0))[0]["outcome"]["success"])
        self.assertFalse(from_verl(_verl_record(reward_score=0))[0]["outcome"]["success"])
        self.assertEqual(from_verl(_verl_record(reward_score=0))[0]["source"]["fidelity"]["success_basis"], "reward_0_1")

    def test_success_from_ground_truth_containment_when_the_reward_is_not_binary(self):
        traj, _ = from_verl(_verl_record(reward_score=0.7))
        self.assertTrue(traj["outcome"]["success"])
        self.assertEqual(traj["source"]["fidelity"]["success_basis"], "ground_truth")
        rec = _verl_record(reward_score=0.7)
        rec["messages"][-1]["content"] = "It was completed in 1887."
        traj, _ = from_verl(rec)
        self.assertFalse(traj["outcome"]["success"])
        self.assertEqual(traj["outcome"]["score"], 0.7)

    def test_no_reward_at_all_is_warned_not_invented(self):
        traj, warnings = from_verl(_verl_record())
        self.assertTrue(all(s.get("reward") is None for s in traj["steps"]))
        self.assertIsNone(traj["outcome"]["score"])
        self.assertTrue(any("no reward" in w for w in warnings))
        self.assertTrue(traj["outcome"]["success"])  # ground truth is in the answer

    def test_fidelity_counters(self):
        rec = _verl_record(reward_score=1.0, num_turns=9)
        rec["messages"].insert(4, {"role": "tool", "tool_call_id": "orphan", "content": "?"})
        traj, warnings = from_verl(rec)
        fid = traj["source"]["fidelity"]
        self.assertEqual(fid["assistant_turns"], 2)
        self.assertEqual(fid["tool_calls"], 1)
        self.assertEqual(fid["tool_results_paired"], 1)
        self.assertEqual(fid["tool_results_unpaired"], 1)
        self.assertEqual(fid["tokens"], "estimated")
        self.assertFalse(fid["answer_synthesized"])
        self.assertTrue(any("num_turns=9" in w for w in warnings))
        self.assertEqual(traj["source"]["format"], "verl")
        self.assertEqual(traj["source"]["data_source"], "hotpotqa")

    def test_measured_tokens_from_ids_and_mask(self):
        traj, _ = from_verl(_verl_record(reward_score=1.0, prompt_ids=list(range(30)),
                                         response_ids=list(range(12)), response_mask=[1] * 8 + [0] * 4))
        self.assertEqual(traj["totals"]["input_tokens"], 30)
        self.assertEqual(traj["totals"]["output_tokens"], 8)
        self.assertEqual(traj["source"]["fidelity"]["tokens"], "measured")

    def test_a_rollout_that_ended_on_a_tool_call_gets_an_empty_answer_step(self):
        rec = _verl_record(reward_score=0.0, max_assistant_turns=1)
        rec["messages"] = rec["messages"][:4]
        traj, warnings = from_verl(rec)
        Trajectory.from_json(traj)
        self.assertEqual(traj["steps"][-1]["type"], "answer")
        self.assertEqual(traj["steps"][-1]["output"], "")
        self.assertTrue(traj["source"]["fidelity"]["answer_synthesized"])
        self.assertEqual(traj["outcome"]["termination"], "max_steps")
        self.assertEqual(traj["budget"], {"max_assistant_turns": 1})
        self.assertTrue(any("ended after a tool call" in w for w in warnings))

    def test_minimal_prompt_response_record(self):
        traj, _ = from_verl({"prompt": "2+2?", "response": "4", "score": 1.0, "ground_truth": "4",
                             "data_source": "gsm8k", "index": 3})
        Trajectory.from_json(traj)
        self.assertEqual([s["type"] for s in traj["steps"]], ["answer"])
        self.assertEqual(traj["task"], {"id": "gsm8k-3", "prompt": "2+2?", "expected": "4"})
        self.assertEqual(traj["steps"][0]["reward"], 1.0)
        # the rollout-dump columns too
        traj, _ = from_verl({"input": "2+2?", "output": "5", "gts": "4", "score": 0.0, "step": 12})
        self.assertFalse(traj["outcome"]["success"])
        self.assertEqual(traj["task"]["expected"], "4")

    def test_raw_prompt_messages_plus_response_text(self):
        traj, _ = from_verl({"raw_prompt": [{"role": "user", "content": "hi"}], "response": "hello", "reward": 1})
        self.assertEqual(traj["task"]["prompt"], "hi")
        self.assertEqual(traj["outcome"]["answer"], "hello")

    def test_a_list_converts_every_record(self):
        out = from_verl([_verl_record(reward_score=1.0, uid="a"), _verl_record(reward_score=0.0, uid="b")])
        self.assertEqual([t["task"]["id"] for t, _ in out], ["hotpotqa-a", "hotpotqa-b"])

    def test_garbage_raises_a_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            from_verl({"foo": 1})
        self.assertIn("messages", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            from_verl("not a record")
        self.assertIn("JSON object", str(ctx.exception))
        with self.assertRaises(ValueError):
            from_verl([])
        with self.assertRaises(ValueError) as ctx:
            from_verl({"messages": [{"role": "user", "content": "only a prompt"}]})
        self.assertIn("no assistant turn", str(ctx.exception))


def _agl_spans() -> list:
    return [
        {"rollout_id": "r1", "sequence_id": 0, "name": "agentlightning.virtual", "attributes": {}},
        {"rollout_id": "r1", "sequence_id": 1, "name": "openai.chat.completion", "start_time": 1.0, "end_time": 1.5,
         "attributes": {"gen_ai.request.model": "qwen", "agent.name": "calc-agent",
                        "gen_ai.prompt.0.role": "user", "gen_ai.prompt.0.content": "capital of france?",
                        "gen_ai.completion.0.role": "assistant", "gen_ai.completion.0.content": "",
                        "gen_ai.completion.0.tool_calls.0.name": "lookup",
                        "gen_ai.completion.0.tool_calls.0.arguments": "{\"q\": \"france capital\"}",
                        "gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 5}},
        {"rollout_id": "r1", "sequence_id": 2, "name": "execute_tool lookup", "start_time": 1.5, "end_time": 1.7,
         "attributes": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup",
                        "gen_ai.tool.call.result": "Paris"}},
        {"rollout_id": "r1", "sequence_id": 3, "name": "openai.chat.completion", "start_time": 2.0, "end_time": 2.4,
         "attributes": {"gen_ai.request.model": "qwen",
                        "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": "Paris."}]),
                        "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 3}},
        {"rollout_id": "r1", "sequence_id": 4, "name": "agentlightning.reward",
         "attributes": {"agentlightning.reward.value": 1.0}},
    ]


class FromAgentLightningTest(unittest.TestCase):
    def test_spans_become_steps_with_the_tool_result_paired_and_the_reward_on_the_last_step(self):
        traj, warnings = from_agent_lightning(_agl_spans())
        Trajectory.from_json(traj)
        self.assertEqual(warnings, [])
        self.assertEqual([(s["type"], s["name"]) for s in traj["steps"]], [("tool_call", "lookup"), ("answer", "final")])
        self.assertEqual(traj["steps"][0]["input"], "{\"q\": \"france capital\"}")
        self.assertEqual(traj["steps"][0]["output"], "Paris")
        self.assertAlmostEqual(traj["steps"][0]["latency_s"], 0.2)
        self.assertEqual([s.get("reward") for s in traj["steps"]], [None, 1.0])
        self.assertEqual(traj["outcome"], {"success": True, "answer": "Paris.", "score": 1.0, "termination": None})
        self.assertEqual(traj["task"], {"id": "agl-r1", "prompt": "capital of france?", "expected": None})
        self.assertEqual(traj["agent"], {"name": "calc-agent", "model": "qwen", "version": "agent-lightning"})
        self.assertEqual(traj["totals"]["input_tokens"], 32)
        fid = traj["source"]["fidelity"]
        self.assertEqual((fid["llm_calls"], fid["tool_calls"], fid["tool_results_paired"], fid["reward_spans"],
                          fid["spans_skipped"]), (2, 1, 1, 1, 1))
        self.assertEqual(fid["tokens"], "measured")

    def test_spans_are_ordered_by_sequence_id_and_the_agent_override_wins(self):
        spans = list(reversed(_agl_spans()))
        traj, _ = from_agent_lightning({"spans": spans, "input": "capital of france?"}, agent="override")
        self.assertEqual(traj["steps"][-1]["output"], "Paris.")
        self.assertEqual(traj["agent"]["name"], "override")

    def test_v1_events_export(self):
        record = {"rollout": {"rollout_id": "r9", "input": {"q": "hi"}}, "events": [
            {"event_type": "model_request", "timestamp": 10.0,
             "data": {"model": "m", "request": {"messages": [{"role": "user", "content": "hi"}]},
                      "response": {"choices": [{"message": {"role": "assistant", "content": "hello"}}]},
                      "latency_ms": 250, "usage": {"prompt_tokens": 3, "completion_tokens": 2}}},
            {"event_type": "reward", "timestamp": 11.0, "data": {"value": 0.0}},
        ]}
        traj, warnings = from_agent_lightning(record)
        Trajectory.from_json(traj)
        self.assertEqual(traj["task"]["id"], "agl-r9")
        self.assertEqual([(s["type"], s["reward"], s["latency_s"]) for s in traj["steps"]], [("answer", 0.0, 0.25)])
        self.assertFalse(traj["outcome"]["success"])
        self.assertEqual(warnings, [])

    def test_no_reward_is_warned_and_a_tool_ending_synthesizes_the_answer(self):
        spans = _agl_spans()[:3]
        traj, warnings = from_agent_lightning(spans)
        Trajectory.from_json(traj)
        self.assertEqual(traj["steps"][-1]["type"], "answer")
        self.assertTrue(traj["source"]["fidelity"]["answer_synthesized"])
        self.assertEqual(traj["source"]["fidelity"]["rewards"], "none")
        self.assertTrue(any("no reward" in w for w in warnings))

    def test_garbage_raises_a_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            from_agent_lightning({"foo": 1})
        self.assertIn("spans", str(ctx.exception))
        with self.assertRaises(ValueError):
            from_agent_lightning([{"name": "agentlightning.virtual", "attributes": {}}])
        with self.assertRaises(ValueError):
            from_agent_lightning(42)


class DetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_formats()

    def test_reward_scores_with_messages_is_verl_not_openai(self):
        record = _verl_record(reward_scores={"score": 1.0})
        detection = detect_format(record)
        self.assertEqual(detection["best"], "verl")
        openai = next(c for c in detection["candidates"] if c["format"] == "openai")
        self.assertGreater(detection["confidence"], openai["confidence"])
        result = registry_convert(record, None)
        self.assertEqual(result["format"], "verl")
        self.assertEqual(result["trajectory"]["steps"][-1]["reward"], 1.0)

    def test_plain_messages_stay_openai(self):
        record = {"messages": _verl_record()["messages"]}
        self.assertEqual(detect_format(record)["best"], "openai")
        self.assertEqual(detect_verl(record)[0], 0.0)

    def test_agent_lightning_spans_and_events_are_detected(self):
        self.assertEqual(detect_format(_agl_spans())["best"], "agent-lightning")
        self.assertGreater(detect_agent_lightning({"events": [{"event_type": "reward", "data": {"value": 1}}]})[0], 0.9)
        self.assertEqual(detect_agent_lightning({"spans": [{"name": "chat", "attributes": {"gen_ai.prompt": "x"}}]})[0], 0.0)

    def test_the_formats_are_selectable_on_the_cli(self):
        result = subprocess.run([sys.executable, "-m", "deepcompare", "convert", "--list-formats"],
                                capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verl", result.stdout)
        self.assertIn("agent-lightning", result.stdout)

    def test_convert_writes_one_trace_per_rollout_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollouts = Path(tmp) / "rollouts.jsonl"
            rollouts.write_text("".join(json.dumps(_verl_record(reward_score=1.0, uid=i)) + "\n" for i in range(3)),
                                encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "deepcompare", "convert", str(rollouts), "-o", tmp,
                                     "--format", "verl", "--agent", "policy-40"],
                                    capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Wrote 3 of 3", result.stdout)
            written = sorted(p.name for p in Path(tmp).glob("*__policy-40.json"))
            self.assertEqual(written, ["hotpotqa-0__policy-40.json", "hotpotqa-1__policy-40.json", "hotpotqa-2__policy-40.json"])
            Trajectory.from_json(Path(tmp) / written[0])


class RlExportTest(unittest.TestCase):
    """Over the demo: the shipped pairs, the horizon pair with its golden
    milestones, and the long-horizon pair."""

    @classmethod
    def setUpClass(cls):
        from deepcompare.cli import main
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.dirs = []
        for name, traces, golden in (("demo", ROOT / "demo" / "traces", None),
                                     ("horizon", ROOT / "demo" / "horizon" / "traces", ROOT / "demo" / "horizon" / "golden.json"),
                                     ("long", ROOT / "demo" / "horizon" / "long", ROOT / "demo" / "horizon" / "golden.json")):
            out = root / name
            argv = ["batch", str(traces), "-o", str(out)] + (["--golden", str(golden)] if golden else [])
            code = main(argv)
            assert code == 0, f"batch {name} exited {code}"
            cls.dirs.append(out)
        cls.reports = []
        for d in cls.dirs:
            cls.reports += load_reports(d)
        cls.steps = sum(len(r[side]["steps"]) for r in cls.reports for side in ("a", "b"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_demo_reports_loaded(self):
        files = sum(len(list(d.glob("report_*.json"))) for d in self.dirs)
        self.assertEqual(len(self.reports), files)
        self.assertGreaterEqual(len(self.reports), 7)  # the shipped pairs, the horizon pair, the long pair
        self.assertGreater(self.steps, 100)

    def test_every_record_of_every_format_says_source_and_trajectory(self):
        for fmt in ("verl-rewards", "preferences", "agent-lightning"):
            records, count = export(self.reports, fmt)
            self.assertEqual(count, len(records))
            self.assertTrue(records, fmt)
            for rec in records:
                self.assertIn(rec["source"], ("recorded", "shaped"), fmt)
                self.assertTrue(rec["trajectory_id"], fmt)
            # deterministic
            self.assertEqual(to_jsonl(records), to_jsonl(export(self.reports, fmt)[0]))

    def test_verl_rewards_one_per_trajectory_with_terms_and_milestones(self):
        records = to_verl_rewards(self.reports)
        self.assertEqual(len(records), 2 * len(self.reports))
        self.assertEqual(len({r["trajectory_id"] for r in records}), len(records))
        for rec in records:
            self.assertEqual(rec["source"], "shaped")  # the demo traces carry no recorded reward
            self.assertAlmostEqual(rec["reward"], sum(rec["step_rewards"]), places=3)
            self.assertIsInstance(rec["outcome"], bool)
            self.assertTrue(rec["data_source"] and rec["uid"] and rec["agent"])
            for label, signed in rec["reward_terms"].items():
                self.assertEqual(signed > 0, label == "fed_answer", (label, signed))
        with_milestones = [r for r in records if r["milestones"]]
        self.assertTrue(with_milestones, "the horizon pairs reach golden milestones")
        self.assertTrue(all(r["uid"].startswith("h0") for r in with_milestones))
        failing = [r for r in records if not r["outcome"]]
        passing = [r for r in records if r["outcome"]]
        self.assertTrue(failing and passing)
        # the answer term shows in every record; the rest is the run's own labels
        self.assertEqual({r["step_rewards"][-1] > 0 for r in passing}, {True})
        # within a pair, the passing run's shaped return is above the failing one's (docs/RL.md)
        by_task: dict = {}
        for rec in records:
            by_task.setdefault(rec["uid"], []).append(rec)
        compared = 0
        for uid, pair in by_task.items():
            won = [r for r in pair if r["outcome"]]
            lost = [r for r in pair if not r["outcome"]]
            if won and lost:
                self.assertGreater(won[0]["reward"], lost[0]["reward"], uid)
                compared += 1
        self.assertGreater(compared, 0)

    def test_the_rewards_follow_the_report_rl_section(self):
        records = {r["trajectory_id"]: r for r in to_verl_rewards(self.reports)}
        for report in self.reports:
            for side in ("a", "b"):
                run = report["rl"][side]
                rec = records[report[side]["trace_id"] or f"{report['task']['id']}__{run['agent']}"]
                self.assertAlmostEqual(rec["reward"], run["return"], places=3)
                self.assertEqual(rec["step_rewards"], [round(r["reward"], 4) for r in run["rewards"]])

    def test_the_compute_score_template_compiles_runs_and_returns_the_stored_reward(self):
        source = to_verl_compute_score_template()
        compile(source, "agentdiff_reward.py", "exec")
        records = to_verl_rewards(self.reports)
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "agentdiff_rewards.jsonl"
            jsonl.write_text(to_jsonl(records), encoding="utf-8")
            namespace = {"__file__": str(Path(tmp) / "agentdiff_reward.py")}
            exec(source, namespace)  # noqa: S102 - the template is our own source
            compute_score = namespace["compute_score"]
            for rec in records[:4]:
                result = compute_score(rec["data_source"], "whatever the policy said", None,
                                       {"trajectory_id": rec["trajectory_id"]})
                self.assertAlmostEqual(result["score"], rec["reward"], places=3)
                self.assertEqual(result["agentdiff_source"], "shaped")
                self.assertEqual(json.loads(result["agentdiff_terms"]), rec["reward_terms"])
            # by uid, then the fallbacks: ground-truth containment, then nothing
            by_uid = compute_score(records[0]["data_source"], "", None, {"uid": records[0]["uid"]})
            self.assertAlmostEqual(by_uid["score"], records[0]["reward"], places=3)
            self.assertEqual(compute_score("d", "the answer is 42", "42", {"trajectory_id": "none"}),
                             {"score": 1.0, "agentdiff_source": "ground_truth_fallback"})
            self.assertEqual(compute_score("d", "nothing", None, None)["score"], 0.0)
            # the env var names the file when the template lives elsewhere
            env_ns = {"__file__": "/nonexistent/agentdiff_reward.py"}
            exec(source, env_ns)  # noqa: S102
            old = os.environ.get("AGENTDIFF_REWARDS_JSONL")
            os.environ["AGENTDIFF_REWARDS_JSONL"] = str(jsonl)
            try:
                self.assertAlmostEqual(env_ns["compute_score"]("d", "", None, {"trajectory_id": records[1]["trajectory_id"]})["score"],
                                       records[1]["reward"], places=3)
            finally:
                if old is None:
                    del os.environ["AGENTDIFF_REWARDS_JSONL"]
                else:
                    os.environ["AGENTDIFF_REWARDS_JSONL"] = old

    def test_preferences_are_dpo_shaped_and_complement_feedback(self):
        records = to_preferences(self.reports)
        pairs = sum(1 for r in self.reports if r["feedback"]["preference_pair"])
        self.assertEqual(len(records), pairs)
        self.assertGreater(pairs, 0)
        for rec in records:
            self.assertTrue(rec["prompt"])
            self.assertEqual(rec["chosen"][-1]["role"], "assistant")
            self.assertEqual(rec["rejected"][-1]["role"], "assistant")
            self.assertTrue(any("tool_calls" in m for m in rec["rejected"]) or len(rec["rejected"]) == 1)
            self.assertIn(rec["chosen_trajectory_id"], rec["trajectory_id"])
            self.assertIn(rec["rejected_trajectory_id"], rec["trajectory_id"])
            self.assertIn(rec["source"], ("recorded", "shaped"))
            self.assertEqual(rec["source"] == "shaped", "splice" in rec["basis"])
            self.assertNotIn("labels", rec)  # the labels stay with feedback.to_jsonl

    def test_transitions_one_per_step_with_state_and_next_state(self):
        records = to_agent_lightning_transitions(self.reports)
        self.assertEqual(len(records), self.steps)
        by_traj: dict = {}
        for rec in records:
            by_traj.setdefault(rec["trajectory_id"], []).append(rec)
        self.assertEqual(len(by_traj), 2 * len(self.reports))
        for tid, rows in by_traj.items():
            self.assertEqual([r["step"] for r in rows], list(range(len(rows))))
            self.assertEqual([r["done"] for r in rows], [False] * (len(rows) - 1) + [True])
            self.assertEqual(rows[-1]["action"]["type"], "answer")
            for prev, cur in zip(rows, rows[1:]):
                self.assertEqual(prev["next_state"]["prior_tools"], cur["state"]["prior_tools"])
                self.assertEqual(prev["next_state"]["step"], cur["state"]["step"])
            tools = [r["action"]["name"] for r in rows if r["action"]["type"] not in ("answer", "reason", "plan")]
            self.assertEqual(rows[-1]["next_state"]["prior_tools"], tools)
        rewards = {r["trajectory_id"]: r for r in to_verl_rewards(self.reports)}
        for tid, rows in by_traj.items():
            self.assertAlmostEqual(sum(r["reward"] for r in rows), rewards[tid]["reward"], places=3)

    def test_the_cli_exits_0_and_prints_counts(self):
        n = len(list(self.dirs[0].glob("report_*.json")))
        with tempfile.TemporaryDirectory() as tmp:
            for fmt, count, needle in (("verl-rewards", 2 * n, "trajectory reward record"),
                                       ("preferences", None, "preference pair"),
                                       ("agent-lightning", None, "transition"),
                                       ("verl-reward-fn", 1, "compute_score template")):
                out = Path(tmp) / f"{fmt}.out"
                result = subprocess.run([sys.executable, "-m", "deepcompare", "rlexport", str(self.dirs[0]),
                                         "--format", fmt, "-o", str(out)],
                                        capture_output=True, text=True, cwd=str(ROOT))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(needle, result.stdout)
                self.assertIn(f"from {n} report(s)", result.stdout)
                self.assertTrue(out.is_file())
                if count is not None:
                    self.assertIn(f"{count} {needle}", result.stdout)
                if fmt != "verl-reward-fn":
                    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
                    self.assertTrue(lines)
                    self.assertTrue(all("source" in l and "trajectory_id" in l for l in lines))
            # one report, to stdout
            report = sorted(self.dirs[0].glob("report_*.json"))[0]
            result = subprocess.run([sys.executable, "-m", "deepcompare", "rlexport", str(report)],
                                    capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.strip().splitlines()), 2)
            self.assertIn("2 trajectory reward record(s) from 1 report(s)", result.stderr)
        result = subprocess.run([sys.executable, "-m", "deepcompare", "rlexport", "/nonexistent/dir"],
                                capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
