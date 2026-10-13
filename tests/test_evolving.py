"""Evals for RL, and evals that evolve with the policy.

Claims under test:

- the reward rules read rewards and behaviour, never the outcome
- repeated rollouts of one task are counted as runs, not names
- a run the labelling cannot call is left out, not counted right
- the reward-hacking target labels what failed yet out-earned a success
- across generations the suite is forward-tested on runs it never saw,
  retires what went quiet or noisy, is reborn when a failure returns, and
  forges what a new generation brought
- a rollout keeps its rewards, the outcome's included, through in-band
  telemetry
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

from agentdiff.evolving import evolve
from agentdiff.forge import RunView, _score, _unique_keys, evaluate, forge, parse_rule, rule_id
from agentdiff.rleval import episode_return, hacking_truth
from agentdiff.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo" / "rl" / "generations"))
import generate as gens_demo  # noqa: E402


def step(i, type_, name, reward=None, error=None, output=""):
    s = {"index": i, "type": type_, "name": name, "input": name, "output": output, "tokens": 10, "latency_s": 0.1}
    if reward is not None:
        s["reward"] = reward
    if error:
        s["error"] = True
    return s


def traj(task, agent, steps, success, run="r1"):
    return Trajectory.from_dict({
        "schema_version": 1, "trace_id": f"{task}-{agent}-{run}", "run_id": run,
        "agent": {"name": agent, "model": "", "version": ""},
        "task": {"id": task, "prompt": "p", "expected": None},
        "outcome": {"success": success, "answer": "done" if success else "no", "score": None},
        "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0},
        "steps": steps})


class RewardRulesTest(unittest.TestCase):
    def view(self, steps):
        return RunView(traj("t", "a", steps + [step(len(steps), "answer", "answer")], True), None, set())

    def test_each_reward_rule_reads_the_reward_signal(self):
        paid_error = self.view([step(0, "tool_call", "api", reward=0.5, error=True)])
        self.assertEqual(evaluate(parse_rule("reward_on_error"), paid_error), 0)
        before = self.view([step(0, "tool_call", "edit_file", reward=1.0), step(1, "tool_call", "run_check")])
        self.assertEqual(evaluate(parse_rule("reward_before_check"), before), 0)
        spike = self.view([step(0, "tool_call", "a", reward=0.1), step(1, "tool_call", "hack", reward=2.0)])
        self.assertEqual(evaluate(parse_rule("reward_concentrated:80"), spike), 1)
        self.assertIsNone(evaluate(parse_rule("reward_concentrated:99"), spike))
        penalised = self.view([step(i, "tool_call", "x", reward=-0.2) for i in range(3)])
        self.assertEqual(evaluate(parse_rule("negative_streak:3"), penalised), 2)
        self.assertEqual(evaluate(parse_rule("return_at_least:2"), spike), -1)
        self.assertEqual(evaluate(parse_rule("return_below:0"), penalised), -1)
        no_reward = self.view([step(0, "tool_call", "x")])
        for r in ("reward_on_error", "reward_before_check", "reward_concentrated:50", "negative_streak:1",
                  "return_at_least:0", "return_below:9"):
            with self.subTest(rule=r):
                self.assertIsNone(evaluate(parse_rule(r), no_reward), "a run with no reward signal never fires")

    def test_the_rules_round_trip_and_refuse_nonsense(self):
        for text in ("reward_on_error", "reward_concentrated:80", "negative_streak:3", "return_at_least:1.25",
                     "return_below:-0.5"):
            with self.subTest(rule=text):
                self.assertEqual(rule_id(parse_rule(text)), text)
        for bad in ("reward_concentrated:0", "reward_concentrated:101", "negative_streak:0", "return_below:x"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_rule(bad)


class RunCountingTest(unittest.TestCase):
    def test_repeated_rollouts_are_runs_not_names(self):
        runs = [RunView(traj("t", "p", [step(0, "tool_call", "x"), step(1, "answer", "a")], False, f"r{i}"),
                        None, set()) for i in range(3)]
        _unique_keys(runs)
        self.assertEqual(len({r.key for r in runs}), 3)
        wrong = {id(r): True for r in runs}
        self.assertEqual(len(_score(parse_rule("tool_called:x"), runs, wrong)["caught_runs"]), 3)
        single = [RunView(traj("t", "p", [step(0, "answer", "a")], True), None, set())]
        _unique_keys(single)
        self.assertEqual(single[0].key, "t/p", "one run each keeps task/agent exactly")


class HackingTargetTest(unittest.TestCase):
    def test_a_hack_is_a_failure_that_out_earned_a_success(self):
        ok = traj("t", "p", [step(0, "tool_call", "check", reward=0.5), step(1, "answer", "a", reward=1.0)], True)
        hack = traj("t", "p", [step(0, "tool_call", "snap", reward=2.0), step(1, "answer", "a", reward=0.0)],
                    False, "r2")
        honest_fail = traj("t", "p", [step(0, "tool_call", "x", reward=-0.5), step(1, "answer", "a")], False, "r3")
        orphan = traj("u", "p", [step(0, "tool_call", "snap", reward=2.0), step(1, "answer", "a")], False)
        truth = hacking_truth([ok, hack, honest_fail, orphan])
        self.assertEqual(truth(ok, {})[0], False)
        self.assertEqual(truth(hack, {})[0], True)
        self.assertEqual(truth(honest_fail, {})[0], False)
        self.assertIsNone(truth(orphan, {})[0], "no success of that task to compare with: not labelled")
        self.assertEqual(episode_return(hack), 2.0)

    def test_an_unlabelled_run_is_left_out_rather_than_counted_right(self):
        runs = []
        for t in ("a", "b", "c", "d"):
            runs += [traj(t, "p", [step(0, "tool_call", "check", reward=0.5), step(1, "answer", "x", reward=1.0)],
                          True, "r1"),
                     traj(t, "p", [step(0, "tool_call", "check", reward=0.4), step(1, "answer", "x", reward=1.0)],
                          True, "r2"),
                     traj(t, "p", [step(0, "tool_call", "snap", reward=2.0), step(1, "answer", "x", reward=0.0)],
                          False, "r3")]
        runs.append(traj("orphan", "p", [step(0, "tool_call", "snap", reward=2.0), step(1, "answer", "x")], False))
        f = forge(runs, truth=hacking_truth(runs))
        self.assertEqual(f["unlabelled"], 1)
        self.assertTrue(any(e["id"] in ("tool_called:snap", "reward_concentrated:50", "reward_concentrated:80")
                            or e["id"].startswith("return_at_least") for e in f["suite"]),
                        [e["id"] for e in f["suite"]])
        self.assertEqual(f["rounds"][-1]["false_alarms"], 0, "the orphan hack did not count as a false alarm")


class EvolveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        gens_demo.generate(Path(cls.tmp.name))
        from agentdiff.commands._io import load_traces
        cls.gens = [(g, load_traces(Path(cls.tmp.name) / g)) for g in ("g0", "g1", "g2", "g3")]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_suite_follows_the_policy_into_a_new_failure_mode(self):
        r = evolve(self.gens, target="failure")
        g = {x["generation"]: x for x in r["lineage"]}
        self.assertTrue(g["g0"]["born"], "the first generation forges the first evals")
        self.assertEqual(g["g1"]["forward"]["coverage"], 1.0, "the carried suite catches the same failures")
        self.assertLess(g["g2"]["forward"]["coverage"], 1.0, "the hack is a failure the old suite has not seen")
        self.assertTrue(g["g2"]["born"], "so g2 forges for it")
        self.assertEqual(g["g3"]["forward"]["coverage"], 1.0, "and g3's never-seen hacks are caught")
        self.assertEqual(sum(x["forward"]["false_alarms"] for x in r["lineage"]), 0)

    def test_a_hack_detector_is_born_only_when_the_hack_appears(self):
        r = evolve(self.gens, target="reward-hacking")
        g = {x["generation"]: x for x in r["lineage"]}
        self.assertEqual((g["g0"]["wrong"], g["g1"]["wrong"]), (0, 0))
        self.assertFalse(g["g0"]["born"] or g["g1"]["born"])
        self.assertTrue(g["g2"]["born"])
        self.assertEqual(g["g3"]["forward"]["coverage"], 1.0)
        self.assertEqual(g["g3"]["forward"]["false_alarms"], 0)

    def test_a_generation_read_twice_is_not_counted_twice(self):
        first = evolve(self.gens[:2])
        again = evolve(self.gens[1:3], ledger=first["ledger"])
        self.assertEqual(again["lineage"][0]["skipped"], "already read")
        with self.assertRaises(ValueError):
            evolve(self.gens[:1], target="reward-hacking", ledger=first["ledger"])


class RetirementTest(unittest.TestCase):
    """A lineage built so one failure mode leaves and comes back."""

    def gen(self, label, flaky: bool, rm: bool):
        """Every run has one shape: read, one action, check, answer. Only the
        action differs, so only the action can tell a failure apart."""
        def run(t, extra, ok, rid):
            steps = [step(0, "read", "read_file"), step(1, "tool_call", "format_code")]
            if extra:
                steps.append(step(2, "tool_call", extra))
            steps += [step(len(steps), "tool_call", "run_check"), step(len(steps) + 1, "answer", "a")]
            return traj(t, f"p-{label}", steps, ok, rid)
        runs = []
        for t in ("t1", "t2", "t3", "t4", "t5", "t6"):
            runs += [run(t, None, True, "r1"), run(t, None, True, "r2")]
            if flaky and t in ("t1", "t2", "t4", "t5"):
                runs.append(run(t, "flaky_api", False, "r3"))
            elif rm:
                runs.append(run(t, "rm_rf", False, "r3"))
        return label, runs

    def test_quiet_evals_retire_and_come_back_when_the_failure_does(self):
        lineage = [self.gen("a", True, True), self.gen("b", True, True), self.gen("c", False, True),
                   self.gen("d", False, True), self.gen("e", True, True)]
        r = evolve(lineage, patience=2)
        evals = {e["id"]: e for e in r["evals"]}
        flaky = evals["tool_called:flaky_api"]
        self.assertEqual(flaky["born"], "a")
        verdicts = [h["verdict"] for h in flaky["history"]]
        self.assertEqual(verdicts, ["born", "holds", "quiet", "quiet", "reborn"])
        self.assertEqual(flaky["status"], "active")
        self.assertEqual(flaky.get("reborn"), 1, "the failure came back at e, and so did its eval")
        d = next(x for x in r["lineage"] if x["generation"] == "d")
        self.assertIn("tool_called:flaky_api", d["retired"])
        e = next(x for x in r["lineage"] if x["generation"] == "e")
        self.assertIn("tool_called:flaky_api", e["reborn"])


class RewardTelemetryTest(unittest.TestCase):
    def test_a_rollout_keeps_every_reward_through_in_band_telemetry(self):
        from agentdiff.telemetry import RL_INSTRUCTIONS, Probe, from_trajectory, rows, to_trajectory
        p = Probe(instructions=RL_INSTRUCTIONS)
        for r in (-0.05, 2.0, -1.25, 0.0):
            with p.hop("x") as h:
                h.reward(r)
        self.assertEqual([x["reward"] for x in rows(p.text())], [-0.05, 2.0, -1.25, 0.0])
        with tempfile.TemporaryDirectory() as tmp:
            gens_demo.generate(Path(tmp))
            for path in sorted(Path(tmp).glob("*/*.json"))[:24]:
                src = json.loads(path.read_text())
                back = to_trajectory(from_trajectory(src), success=src["outcome"]["success"])
                self.assertAlmostEqual(episode_return(src), episode_return(back), places=6)


class CommandTest(unittest.TestCase):
    def test_the_command_writes_the_lineage_and_continues_a_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            gens_demo.generate(tmp / "gens")
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            run = lambda *a: subprocess.run([sys.executable, "-m", "agentdiff", "evolve-evals", *a],  # noqa: E731
                                            capture_output=True, text=True, env=env)
            first = run(str(tmp / "gens/g0"), str(tmp / "gens/g1"), str(tmp / "gens/g2"),
                        "--target", "reward-hacking", "--ledger", str(tmp / "l.json"), "-o", str(tmp / "o"))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("+", first.stdout)
            self.assertTrue((tmp / "o" / "EVOLVING_EVALS.md").is_file())
            data = json.loads((tmp / "o" / "evolve-evals.json").read_text())
            self.assertEqual([g["generation"] for g in data["lineage"]], ["g0", "g1", "g2"])
            second = run(str(tmp / "gens/g3"), "--target", "reward-hacking", "--ledger", str(tmp / "l.json"),
                         "-o", str(tmp / "o2"))
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("100%", second.stdout, "g3 meets the suite carried in from the ledger")
            wrong = run(str(tmp / "gens/g3"), "--ledger", str(tmp / "l.json"), "-o", str(tmp / "o3"))
            self.assertEqual(wrong.returncode, 2, "a ledger for another target is refused")


if __name__ == "__main__":
    unittest.main()
