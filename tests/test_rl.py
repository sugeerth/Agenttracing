"""The run as an episode: reward, return and credit.

The schema fields round-trip and reject non-numbers; a shaped reward is
derived from the report's labels by the stated rule and every sum,
return-to-go and discount checks by hand; credit spreads each Shapley
allocation over the region's steps with the winner positive; clusters
cover every step once on one scale across the pair; a recorded reward
wins over a shaped one; the demo policies order as generated; the
aggregate carries three episodes per agent per task; the CLI runs; and
everything is deterministic.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import Trajectory, compare  # noqa: E402
from deepcompare.record import Recorder  # noqa: E402
from deepcompare.report import attach_milestones  # noqa: E402
from deepcompare.rl import (  # noqa: E402
    ANSWER_REWARD, DECISIVE_REWARD, GAMMA, MILESTONE_REWARD, SHAPED_WEIGHTS, mean_ci, rl_aggregate, rl_pair,
    rl_run_from_trace,
)
from deepcompare.suite import analyse_runs  # noqa: E402

RL_TRACES = ROOT / "demo" / "rl" / "traces"
DEMO_TRACES = ROOT / "demo" / "traces"
LONG_TRACES = ROOT / "demo" / "horizon" / "long"


def _step(i, typ, name, *, tokens=10, latency=1.0, span=None, error=None, reward=None, value=None, output=""):
    return {"index": i, "type": typ, "name": name, "input": "", "output": output, "tokens": tokens,
            "latency_s": latency, "quality": None, "note": None, "model": None, "tokens_basis": None,
            "error": error, "effect": None, "span": span, "reward": reward, "value": value, "advantage": None}


def _hand_report():
    """orch (a) fails: step 2 errors, 3 is a dead end, 4 is decisive, 5 feeds
    the answer and reaches a milestone, 6 is the wrong answer. other (b)
    passes in four steps. Two Shapley regions: rows 1–3 (300 tokens) and
    row 5 (100 tokens)."""
    s1 = {"id": "s1", "agent": "researcher", "parent": None}
    a_steps = [
        _step(0, "plan", "plan"), _step(1, "reason", "reason"),
        _step(2, "search", "web_search", span=s1, error=True),
        _step(3, "read", "open_page", span=s1), _step(4, "tool_call", "calc", span=s1),
        _step(5, "reason", "reason", value=1.5), _step(6, "answer", "answer"),
    ]
    b_steps = [_step(0, "reason", "reason"), _step(1, "search", "web_search"),
               _step(2, "tool_call", "calc"), _step(3, "answer", "answer")]
    alignment = [
        {"op": "match", "a_index": 0, "b_index": 0}, {"op": "substitute", "a_index": 1, "b_index": 1},
        {"op": "delete", "a_index": 2, "b_index": None}, {"op": "delete", "a_index": 3, "b_index": None},
        {"op": "match", "a_index": 4, "b_index": 2}, {"op": "delete", "a_index": 5, "b_index": None},
        {"op": "match", "a_index": 6, "b_index": 3},
    ]
    return {
        "task": {"id": "t", "prompt": "how far?", "expected": "42"},
        "a": {"agent": {"name": "orch"}, "outcome": {"success": False}, "steps": a_steps},
        "b": {"agent": {"name": "other"}, "outcome": {"success": True}, "steps": b_steps},
        "alignment": alignment,
        "reading": {"a": {"phases": [{"intent": "frame", "steps": [0, 1]}, {"intent": "acquire", "steps": [2, 3, 4]},
                                     {"intent": "commit", "steps": [5, 6]}],
                          "what_happened": [{"step": 2, "role": "error", "intent": "acquire"},
                                            {"step": 3, "role": "dead_end", "intent": "acquire"},
                                            {"step": 5, "role": "feeds_answer", "intent": "decide"}],
                          "answer_basis": {"spent_steps": []}, "errors": []},
                    "b": {"phases": [{"intent": "acquire", "steps": [0, 1, 2]}, {"intent": "commit", "steps": [3]}],
                          "what_happened": [{"step": 1, "role": "dead_end"}, {"step": 2, "role": "feeds_answer"}],
                          "answer_basis": {"spent_steps": []}, "errors": []}},
        "diagnosis": {"subject": "a", "decisive_step": {"step": 4, "verification": None},
                      "causal_account": [{"step": 4, "mechanism": "wrong value"}, {"step": 6, "mechanism": "committed"}]},
        "attribution": {"failed_agent": "a", "root_cause_step": 4, "chain": [4, 6]},
        "divergences": [],
        "shapley": {"available": True, "metric": "tokens", "winner": "other", "loser": "orch",
                    "allocations": [{"region": 0, "alignment_rows": [1, 2, 3], "shapley": 300.0},
                                    {"region": 1, "alignment_rows": [5], "shapley": 100.0}]},
        "milestones": {"a": {"measurable": True, "milestones": [{"id": "m1", "label": "found it", "reached": True, "step": 5}]},
                       "b": {"measurable": True, "milestones": [{"id": "m1", "reached": False, "step": None}]}},
    }


def _covers(clusters, n):
    seen = []
    for c in clusters:
        seen.extend(range(c["from"], c["to"] + 1))
    return seen == list(range(n))


class SchemaFieldsTest(unittest.TestCase):
    def test_reward_value_advantage_round_trip_through_the_recorder(self):
        with Recorder(task="t", prompt="p", agent="a", out_dir=None) as run:
            run.reason("think", reward=-1, value=0.5)
            run.tool("f", {"x": 1}, output="ok", advantage=0.25)
            run.tool("g", {"x": 2}, output="ok")
            run.answer("done", success=True, reward=5)
        data = run.to_dict()
        self.assertEqual((data["steps"][0]["reward"], data["steps"][0]["value"]), (-1.0, 0.5))
        self.assertEqual(data["steps"][1]["advantage"], 0.25)
        self.assertNotIn("reward", data["steps"][2], "a step without a reward does not write one")
        t = Trajectory.from_dict(data)
        self.assertEqual([s.reward for s in t.steps], [-1.0, None, None, 5.0])
        self.assertEqual([s.value for s in t.steps], [0.5, None, None, None])
        self.assertEqual(t.steps[1].advantage, 0.25)
        back = Trajectory.from_dict(t.to_dict())
        self.assertEqual([s.to_dict() for s in back.steps], [s.to_dict() for s in t.steps])

    def test_non_numbers_are_rejected_at_both_ends(self):
        with Recorder(task="t", prompt="p", agent="a", out_dir=None) as run:
            for key in ("reward", "value", "advantage"):
                with self.assertRaisesRegex(ValueError, f"{key} must be a number or None"):
                    run.reason("x", **{key: "high"})
            with self.assertRaises(ValueError):
                run.reason("x", reward=True)
            run.answer("done", success=True)
        data = run.to_dict()
        for key in ("reward", "value", "advantage"):
            bad = json.loads(json.dumps(data))
            bad["steps"][0][key] = "high"
            with self.assertRaisesRegex(ValueError, f"steps\\[0\\]: {key} must be a number or null"):
                Trajectory.from_dict(bad)
        bad = json.loads(json.dumps(data))
        bad["steps"][0]["reward"] = False
        with self.assertRaises(ValueError):
            Trajectory.from_dict(bad)


class ShapedRewardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = _hand_report()
        cls.rl = rl_pair(cls.report)

    def test_the_contract_shape(self):
        rl = self.rl
        self.assertEqual(rl["version"], 1)
        self.assertTrue(rl["measurable"])
        self.assertEqual((rl["source"], rl["gamma"]), ("shaped", GAMMA))
        for side in ("a", "b"):
            run = rl[side]
            for key in ("agent", "measurable", "steps", "return", "discounted_return", "positive", "negative", "zero",
                        "seconds", "rewards", "largest", "credit", "clusters", "narrative"):
                self.assertIn(key, run)
            for row in run["rewards"]:
                for key in ("step", "reward", "cum", "to_go", "discounted_to_go", "credit", "labels", "agent", "kind", "name"):
                    self.assertIn(key, row)
        self.assertEqual(rl["a"]["seconds"], 7.0)

    def test_labels_become_rewards_by_the_stated_rule(self):
        a = self.rl["a"]
        rewards = [r["reward"] for r in a["rewards"]]
        # 2 error −1; 3 dead end −0.1; 4 decisive −3; 5 fed_answer +1 and milestone +2; 6 wrong_answer −5 in place of the outcome
        self.assertEqual(rewards, [0.0, 0.0, -1.0, -0.1, DECISIVE_REWARD, 1.0 + MILESTONE_REWARD, SHAPED_WEIGHTS["wrong_answer"]])
        self.assertEqual(SHAPED_WEIGHTS["wrong_answer"], -ANSWER_REWARD, "a wrong answer pays what a failed outcome would, once")
        self.assertIn("fault_enters", a["rewards"][4]["labels"])
        self.assertEqual(a["rewards"][5]["why"], "fed answer, milestone m1")
        self.assertEqual(a["rewards"][6]["why"], "wrong answer")
        b = self.rl["b"]
        self.assertEqual([r["reward"] for r in b["rewards"]], [0.0, -0.1, 1.0, ANSWER_REWARD])
        self.assertEqual((a["return"], b["return"]), (-6.1, 5.9))
        self.assertEqual((a["positive"], a["negative"], a["zero"]), (1, 4, 2))
        self.assertEqual(a["rewards"][2]["agent"], "researcher")
        self.assertEqual(a["rewards"][0]["agent"], "orch")
        self.assertEqual(a["rewards"][5]["value"], 1.5)

    def test_cum_to_go_and_the_discount_add_up(self):
        a = self.rl["a"]
        rewards = [r["reward"] for r in a["rewards"]]
        self.assertEqual([r["cum"] for r in a["rewards"]], [0.0, 0.0, -1.0, -1.1, -4.1, -1.1, -6.1])
        self.assertEqual([r["to_go"] for r in a["rewards"]], [-6.1, -6.1, -6.1, -5.1, -5.0, -2.0, -5.0])
        expected = sum(r * GAMMA ** i for i, r in enumerate(rewards))
        self.assertAlmostEqual(a["discounted_return"], expected, places=3)
        self.assertAlmostEqual(a["rewards"][0]["discounted_to_go"], expected, places=3)
        for i, row in enumerate(a["rewards"][:-1]):
            self.assertAlmostEqual(row["discounted_to_go"], row["reward"] + GAMMA * a["rewards"][i + 1]["discounted_to_go"], places=3)
        self.assertEqual(a["rewards"][-1]["discounted_to_go"], a["rewards"][-1]["reward"])

    def test_largest_orders_by_magnitude_then_step(self):
        self.assertEqual([r["step"] for r in self.rl["a"]["largest"]], [6, 4, 5, 2, 3])
        self.assertEqual(self.rl["a"]["largest"][0], {"step": 6, "reward": -5.0, "why": "wrong answer"})
        self.assertEqual(len(self.rl["b"]["largest"]), 3, "zero rewards are not 'largest'")

    def test_credit_spreads_each_allocation_with_the_winner_positive(self):
        a, b = self.rl["a"], self.rl["b"]
        self.assertEqual(a["credit"]["source"], "shapley")
        self.assertEqual(a["credit"]["metric"], "tokens")
        # region 0 (rows 1–3): orch's steps 1, 2, 3 carry −100 each; region 1 (row 5): step 5 carries −100
        self.assertEqual([r["credit"] for r in a["rewards"]], [0.0, -100.0, -100.0, -100.0, 0.0, -100.0, 0.0])
        self.assertEqual(a["credit"]["total"], -400.0)
        self.assertEqual([c["step"] for c in a["credit"]["top"]], [1, 2, 3, 5])
        # other has one step (1) in region 0 and none in region 1
        self.assertEqual([r["credit"] for r in b["rewards"]], [0.0, 300.0, 0.0, 0.0])
        self.assertEqual(b["credit"]["total"], 300.0)

    def test_credit_is_none_without_shapley(self):
        report = _hand_report()
        report["shapley"] = {"available": False, "reason": "too many regions"}
        rl = rl_pair(report)
        self.assertEqual(rl["a"]["credit"], {"source": None, "metric": None, "top": [], "total": 0.0})
        self.assertTrue(all(r["credit"] is None for r in rl["a"]["rewards"]))
        self.assertIn("no credit allocated", rl["narrative"])

    def test_clusters_cover_every_step_once_on_one_scale(self):
        a, b = self.rl["a"], self.rl["b"]
        self.assertTrue(_covers(a["clusters"], 7))
        self.assertTrue(_covers(b["clusters"], 4))
        self.assertEqual(max(c["impact"] for r in (a, b) for c in r["clusters"]), 1.0)
        for c in a["clusters"] + b["clusters"]:
            for key in ("id", "from", "to", "steps", "start_s", "end_s", "seconds", "lane", "agents", "impact", "score",
                        "kind", "reasons", "why", "label", "marks"):
                self.assertIn(key, c)
            self.assertIn(c["kind"], ("hot", "work", "quiet"))
            self.assertEqual(c["impact"], round(c["score"] / max(cc["score"] for r in (a, b) for cc in r["clusters"]), 4))
        # score = Σ|reward| + Σ|credit| + 2 per fault label, so a cluster holding step 6 (−5, wrong_answer) scores ≥ 7
        holding = next(c for c in a["clusters"] if c["from"] <= 6 <= c["to"])
        self.assertGreaterEqual(holding["score"], 7.0)
        self.assertIn("the answer", holding["why"])
        lanes = {c["lane"] for c in a["clusters"]}
        self.assertEqual(lanes, {"orch", "researcher"})

    def test_marks_are_the_largest_rewards_plus_decisive_and_answer(self):
        a = self.rl["a"]
        marks = [m for c in a["clusters"] for m in c["marks"]]
        kinds = {(m["step"], m["kind"]) for m in marks}
        self.assertIn((4, "decisive"), kinds)
        self.assertIn((6, "answer"), kinds)
        self.assertIn((6, "reward−"), kinds)
        self.assertNotIn((0, "reward+"), kinds)
        label = next(m["label"] for m in marks if m["kind"] == "reward−" and m["step"] == 6)
        self.assertEqual(label, "reward −5: wrong answer")

    def test_the_narratives_quote_the_numbers(self):
        a, b = self.rl["a"]["narrative"], self.rl["b"]["narrative"]
        self.assertIn("orch: return −6.1 over 7 steps (shaped): 4 negative steps, 1 positive", a)
        self.assertIn("the largest penalty at step 6 (−5, wrong answer)", a)
        self.assertIn("the largest reward at step 5 (+3, fed answer, milestone m1)", a)
        self.assertIn("credit −400 tokens on 4 steps, most in researcher", a)
        self.assertIn("other: return 5.9 over 4 steps (shaped)", b)
        self.assertIn("credit +300 tokens on 1 step in other", b)
        pair = self.rl["narrative"]
        self.assertIn("other earned more: return 5.9 against −6.1 for orch (shaped)", pair)
        self.assertIn("the returns part at step 2 (orch −1 against 0.9 so far)", pair)
        self.assertIn("credit splits −400 tokens to orch and +300 tokens to other", pair)
        self.assertIn("preference: other chosen (the passing run, verbatim), orch rejected", pair)
        self.assertEqual(self.rl["preference"]["chosen"]["agent"], "other")
        self.assertEqual(self.rl["preference"]["diverges_at"]["step"], 4)

    def test_recorded_rewards_win_over_shaped(self):
        report = _hand_report()
        report["b"]["steps"][2]["reward"] = 2.5
        rl = rl_pair(report)
        self.assertEqual(rl["source"], "recorded")
        self.assertEqual(rl["a"]["source"], "recorded")
        self.assertEqual(rl["a"]["return"], 0.0, "no label shapes a reward once one side records rewards")
        self.assertEqual([r["reward"] for r in rl["b"]["rewards"]], [0.0, 0.0, 2.5, 0.0])
        self.assertEqual(rl["b"]["largest"], [{"step": 2, "reward": 2.5, "why": "fed answer"}])
        self.assertIn("(recorded)", rl["narrative"])

    def test_a_step_with_several_labels_sums_its_weights_and_the_table_is_exposed(self):
        report = _hand_report()
        report["reading"]["a"]["what_happened"][1]["role"] = "repeat"
        report["reading"]["a"]["what_happened"][1]["invented_argument"] = True
        rl = rl_pair(report)
        self.assertEqual(rl["a"]["rewards"][3]["reward"], round(SHAPED_WEIGHTS["repeat"] + SHAPED_WEIGHTS["invented_argument"], 4))
        self.assertEqual(rl["a"]["rewards"][3]["why"], "repeat, invented argument")
        for key in ("fault_enters", "wrong_answer", "error", "repeat", "invented_argument", "spent_after_basis",
                    "dead_end", "no_information", "fed_answer", "milestone", "answer"):
            self.assertIn(key, SHAPED_WEIGHTS)
        self.assertNotIn("fault_carried", SHAPED_WEIGHTS, "carrying the fault forward is not paid twice")

    def test_a_failed_answer_without_the_label_still_pays_the_outcome_once(self):
        report = _hand_report()
        report["diagnosis"] = {}
        report["attribution"] = {}
        rl = rl_pair(report)
        self.assertEqual(rl["a"]["rewards"][6]["reward"], -ANSWER_REWARD)
        self.assertEqual(rl["a"]["rewards"][6]["why"], "answer failed")

    def test_deterministic(self):
        again = rl_pair(_hand_report())
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.rl, sort_keys=True))


class MilestonesRecomputeTest(unittest.TestCase):
    def test_attach_milestones_pays_two_at_the_reached_step_when_shaped(self):
        a = Trajectory.from_json(DEMO_TRACES / "t05_flight_duration__atlas-v2.json")
        b = Trajectory.from_json(DEMO_TRACES / "t05_flight_duration__bolt-v3.json")
        report = compare(a, b)
        self.assertEqual(report["rl"]["source"], "shaped")
        before = {r["step"]: r["reward"] for r in report["rl"]["a"]["rewards"]}
        golden = {"path": "golden.json", "tasks": {"t05_flight_duration": {"milestones": [
            {"id": "utc", "label": "converted to UTC", "evidence": ["convert to UTC"], "in": "output"}]}}}
        attach_milestones(report, golden)
        reached = report["milestones"]["a"]["milestones"][0]
        self.assertTrue(reached["reached"])
        after = {r["step"]: r["reward"] for r in report["rl"]["a"]["rewards"]}
        self.assertEqual(after[reached["step"]], before[reached["step"]] + MILESTONE_REWARD)
        self.assertEqual(report["rl"]["a"]["return"], round(sum(before.values()) + MILESTONE_REWARD, 4))
        self.assertIn(f"milestone utc", report["rl"]["a"]["rewards"][reached["step"]]["why"])


class LongDemoTest(unittest.TestCase):
    def test_the_passing_long_run_earns_more_and_is_not_drowned_by_its_length(self):
        a = Trajectory.from_json(LONG_TRACES / "h02_migrate_service__atlas-lh.json")
        b = Trajectory.from_json(LONG_TRACES / "h02_migrate_service__comet-lh.json")
        rl = compare(a, b)["rl"]
        self.assertEqual(rl["source"], "shaped")
        self.assertTrue(a.outcome.success and not b.outcome.success)
        self.assertGreater(rl["a"]["return"], rl["b"]["return"])
        self.assertGreater(rl["a"]["return"], -60.0)
        self.assertIn("atlas-lh earned more", rl["narrative"])


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = sorted(RL_TRACES.glob("*.json"))
        cls.trajectories = []
        for path in cls.paths:
            t = Trajectory.from_json(path)
            t.run_id = path.stem.split("__")[2]
            cls.trajectories.append(t)

    def test_the_demo_ships_twelve_labelled_traces_with_rewards_on_every_step(self):
        self.assertEqual(len(self.paths), 12)
        for path in self.paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("SYNTHETIC", data["harness"]["note"])
            self.assertTrue(all(isinstance(s.get("reward"), (int, float)) for s in data["steps"]), path.name)
            self.assertTrue(30 <= len(data["steps"]) <= 60, path.name)
            self.assertTrue(any(isinstance(s.get("value"), (int, float)) for s in data["steps"] if s["type"] in ("plan", "reason")))
            self.assertTrue(any(s.get("span") for s in data["steps"]), "a sub-agent lane exists")
            self.assertEqual(len(path.stem.split("__")), 3)

    def test_a_pair_from_the_demo_is_recorded_on_both_sides(self):
        by_name = {p.name: p for p in self.paths}
        a = Trajectory.from_json(by_name["rl01_ledger_reconcile__policy-v1__r1.json"])
        b = Trajectory.from_json(by_name["rl01_ledger_reconcile__policy-v2__r1.json"])
        rl = compare(a, b)["rl"]
        self.assertTrue(rl["measurable"])
        self.assertEqual(rl["source"], "recorded")
        for side, traj in (("a", a), ("b", b)):
            self.assertEqual(rl[side]["source"], "recorded")
            self.assertEqual(rl[side]["return"], round(sum(s.reward for s in traj.steps), 4))
            self.assertTrue(_covers(rl[side]["clusters"], len(traj.steps)))
        self.assertEqual(max(c["impact"] for s in ("a", "b") for c in rl[s]["clusters"]), 1.0)
        self.assertGreater(rl["b"]["return"], rl["a"]["return"])
        self.assertIn("(recorded)", rl["narrative"])

    def test_runs_aggregate_carries_three_episodes_per_agent_per_task_and_orders_the_policies(self):
        analysed = analyse_runs(self.trajectories)
        agg = analysed["aggregate"]["rl"]
        self.assertEqual(agg["source"], "recorded")
        self.assertEqual(agg["gamma"], GAMMA)
        self.assertEqual(set(agg["agents"]), {"policy-v1", "policy-v2"})
        for name, block in agg["agents"].items():
            self.assertEqual(block["episodes_n"], 6)
            per_task = {}
            for e in block["episodes"]:
                per_task[e["task_id"]] = per_task.get(e["task_id"], 0) + 1
            self.assertEqual(per_task, {"rl01_ledger_reconcile": 3, "rl02_flaky_test": 3})
            self.assertEqual(len(block["return_ci"]), 2)
            self.assertLessEqual(block["return_ci"][0], block["mean_return"])
        self.assertGreater(agg["agents"]["policy-v2"]["mean_return"], agg["agents"]["policy-v1"]["mean_return"])
        self.assertIn("policy-v2: mean return", agg["narrative"])
        self.assertIn("rewards recorded", agg["narrative"])

    def test_episode_detail_arrays_and_counts(self):
        agg = analyse_runs(self.trajectories)["aggregate"]["rl"]
        by_id = {(t.agent.name, t.task.id, t.run_id): t for t in self.trajectories}
        for name, block in agg["agents"].items():
            for e in block["episodes"]:
                traj = by_id[(name, e["task_id"], e["run_id"])]
                n = len(traj.steps)
                self.assertEqual(e["steps"], n)
                for key in ("rewards", "cum", "values", "advantages"):
                    self.assertEqual(len(e[key]), n, key)
                self.assertEqual(e["cum"][-1], e["return"])
                self.assertEqual(e["rewards"], [round(s.reward, 4) for s in traj.steps])
                self.assertEqual(e["values"], [s.value for s in traj.steps])
                for v, a in zip(e["values"], e["advantages"]):
                    self.assertEqual(v is None, a is None)
                self.assertEqual(e["success"], traj.outcome.success)
                self.assertEqual(sum(e["tools"].values()), sum(1 for s in traj.steps if s.type in ("tool_call", "search", "retrieve", "read")))
                self.assertGreaterEqual(e["distinct_inputs"], 1)
                self.assertGreater(e["seconds"], 0)
                run = rl_run_from_trace(traj)
                events = {}
                for row in run["rewards"]:
                    for l in row["labels"]:
                        if l != "clean":
                            events[l] = events.get(l, 0) + 1
                self.assertEqual(e["events"], events)
                self.assertNotIn("clean", e["events"])

    def test_tasks_deltas_and_preferences(self):
        agg = analyse_runs(self.trajectories)["aggregate"]["rl"]
        self.assertEqual(sorted(agg["tasks"]), ["rl01_ledger_reconcile", "rl02_flaky_test"])
        for tid, block in agg["tasks"].items():
            v1, v2 = block["policy-v1"], block["policy-v2"]
            self.assertEqual(len(v1["returns"]), 3)
            self.assertEqual(v1["mean_return"], round(sum(v1["returns"]) / 3, 4))
            self.assertAlmostEqual(block["delta"], v2["mean_return"] - v1["mean_return"], places=3)
            self.assertEqual(block["sign"], 1 if block["delta"] > 0 else -1 if block["delta"] < 0 else 0)
        self.assertEqual(len(agg["pairs"]), 2)
        for p in agg["preferences"]:
            self.assertIn(p["chosen"], ("policy-v1", "policy-v2"))
            self.assertNotEqual(p["chosen"], p["rejected"])
            self.assertIn("basis", p)

    def test_rl_run_from_trace_matches_the_recorded_sum(self):
        traj = self.trajectories[0]
        run = rl_run_from_trace(traj)
        self.assertEqual(run["source"], "recorded")
        self.assertEqual(run["return"], round(sum(s.reward for s in traj.steps), 4))
        self.assertEqual(run["credit"]["source"], None)
        self.assertTrue(_covers(run["clusters"], len(traj.steps)))
        self.assertEqual(max(c["impact"] for c in run["clusters"]), 1.0)
        self.assertEqual(run["task_id"], traj.task.id)

    def test_shaped_from_a_trace_alone_uses_its_own_reading(self):
        traj = Trajectory.from_json(DEMO_TRACES / "t05_flight_duration__bolt-v3.json")
        run = rl_run_from_trace(traj)
        self.assertEqual(run["source"], "shaped")
        self.assertEqual(run["rewards"][-1]["reward"], -ANSWER_REWARD)
        self.assertIn("(shaped)", run["narrative"])

    def test_aggregate_is_deterministic(self):
        one = rl_aggregate([], self.trajectories, names=("policy-v1", "policy-v2"))
        two = rl_aggregate([], list(reversed(self.trajectories)), names=("policy-v1", "policy-v2"))
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))

    def test_mean_ci(self):
        self.assertEqual(mean_ci([]), (None, None))
        self.assertEqual(mean_ci([3.0]), (3.0, None))
        mean, ci = mean_ci([1.0, 3.0])
        self.assertEqual(mean, 2.0)
        self.assertAlmostEqual(ci[1] - mean, 1.96 * (2.0 ** 0.5) / (2 ** 0.5), places=3)


class CliTest(unittest.TestCase):
    def test_rl_on_the_demo_directory(self):
        result = subprocess.run([sys.executable, "-m", "deepcompare", "rl", str(RL_TRACES)],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[recorded]", result.stdout)
        self.assertIn("Per agent (runs layout):", result.stdout)
        self.assertIn("policy-v2: mean return", result.stdout)

    def test_rl_json_on_one_trace(self):
        path = sorted(RL_TRACES.glob("*.json"))[0]
        result = subprocess.run([sys.executable, "-m", "deepcompare", "rl", str(path), "--json"],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        runs = json.loads(result.stdout)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["source"], "recorded")


if __name__ == "__main__":
    unittest.main()
