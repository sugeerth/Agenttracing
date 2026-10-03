"""The eval forge: evals written from the traces, adopted only on the held-out half.

The loop's promise is that it improves without fooling itself, so most
of what is pinned here is the fooling-itself part: adoption is decided by
a half no candidate was chosen on, a judge never sees that half, a
proposal that does not parse is refused and fed back, and a corpus read
twice is not new evidence.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentdiff import forge
from agentdiff.commands._io import load_traces
from agentdiff.lessons import split
from agentdiff.scorecard import load_golden
from agentdiff.trace import Trajectory

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "demo" / "horizon" / "suite"
GOLDEN = ROOT / "demo" / "horizon" / "suite_golden.json"


def _suite():
    if not SUITE.is_dir():
        raise unittest.SkipTest("the long-horizon suite is not generated")
    return load_traces(SUITE), load_golden(GOLDEN)


def _run(steps, answer="Done, all tests pass.", task="t1", agent="a"):
    data = {"schema_version": 1, "trace_id": f"{task}__{agent}", "agent": {"name": agent},
            "task": {"id": task, "prompt": "p"},
            "outcome": {"success": False, "answer": answer, "termination": "agent_stop"},
            "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0},
            "steps": [dict({"index": i, "tokens": 0, "latency_s": 0.0}, **s) for i, s in enumerate(steps)]
            + [{"index": len(steps), "type": "answer", "name": "answer", "input": "", "output": answer,
                "tokens": 0, "latency_s": 0.0}]}
    return Trajectory.from_dict(data)


class GrammarTest(unittest.TestCase):
    def test_every_rule_parses_and_round_trips(self):
        for text in ("mark:unrecovered_error", "no_check_after_last_edit", "claims_without_check",
                     "signature:tool_selection/a:x/b:none", "tool_called:run_checks>=3", "tool_absent:pytest",
                     "repeated_call:3", "error_streak:2", "output_matches:Traceback", "answer_matches:(?i)done"):
            with self.subTest(text=text):
                rule = forge.parse_rule(text)
                self.assertEqual(forge.parse_rule(forge.spec(rule)), rule)
        both = forge.parse_rule({"all": ["mark:cycle", "tool_called:Bash"]})
        self.assertEqual(forge.parse_rule(forge.spec(both)), both)

    def test_what_is_outside_the_grammar_is_refused_with_the_reason(self):
        for bad, why in (("vibes:good", "unknown rule"), ("mark:nonsense", "unknown mark"),
                         ("repeated_call:0", "positive"), ("output_matches:" + "a" * 201, "200"),
                         ({"all": ["mark:cycle"]}, "at least two"), ("tool_called", "needs an argument")):
            with self.subTest(bad=str(bad)[:40]):
                with self.assertRaises(ValueError) as caught:
                    forge.parse_rule(bad)
                self.assertIn(why, str(caught.exception))
        with self.assertRaises(Exception):
            forge.parse_rule("output_matches:([")

    def test_each_rule_reads_the_run_it_is_given(self):
        run = _run([
            {"type": "tool_call", "name": "Bash", "input": '{"command": "cat a.py"}', "output": "x"},
            {"type": "tool_call", "name": "Edit", "input": "{}", "output": "ok"},
            {"type": "tool_call", "name": "shell", "input": "pytest -q", "output": "Traceback: boom", "error": True},
            {"type": "tool_call", "name": "shell", "input": "pytest -q", "output": "Traceback: boom", "error": True},
            {"type": "tool_call", "name": "shell", "input": "pytest -q", "output": "Traceback: boom", "error": True},
            {"type": "tool_call", "name": "Edit", "input": "{}", "output": "ok"},
        ])
        view = forge.RunView(run, None, {"sig/1"})

        def fires(text):
            return forge.evaluate(forge.parse_rule(text), view)
        self.assertEqual(fires("no_check_after_last_edit"), 5)
        self.assertIsNotNone(fires("claims_without_check"))
        self.assertEqual(fires("error_streak:3"), 4)
        self.assertIsNone(fires("error_streak:4"))
        self.assertEqual(fires("repeated_call:3"), 4)
        self.assertEqual(fires("tool_called:shell>=2"), 3)
        self.assertEqual(fires("tool_absent:WebSearch"), -1)
        self.assertIsNone(fires("tool_absent:Edit"))
        self.assertEqual(fires("output_matches:Traceback"), 2)
        self.assertEqual(fires("signature:sig/1"), -1)
        self.assertIsNone(fires("signature:sig/2"))
        self.assertEqual(forge.evaluate(forge.parse_rule({"all": ["error_streak:3", "tool_called:Edit"]}), view), 4)
        self.assertIsNone(forge.evaluate(forge.parse_rule({"all": ["error_streak:3", "tool_absent:Edit"]}), view))


class ForgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces, cls.gold = _suite()
        cls.out = forge.forge(cls.traces, cls.gold, cls.gold.get("policy"))
        cls.halves = split(t.task.id for t in cls.traces)

    def test_it_forges_a_suite(self):
        self.assertTrue(self.out["measurable"])
        self.assertTrue(self.out["suite"])
        self.assertEqual(self.out["wrong"], 12)

    def test_every_adopted_eval_passed_the_learn_half_then_the_held_out_half(self):
        for e in self.out["suite"]:
            learn, held = e["halves"]
            for h in (learn, held):
                self.assertGreaterEqual(h["caught"], 1, e["id"])
                self.assertLessEqual(h["fpr"], forge.FPR_MAX, e["id"])
            self.assertTrue(e["held_out_tested"])

    def test_the_held_out_half_is_only_met_by_what_passed_the_learn_half(self):
        met = [e for e in self.out["tested"] if e["held_out_tested"]]
        for e in self.out["tested"]:
            if e.get("failed_on") == "learn":
                self.assertFalse(e["held_out_tested"], e["id"])
        self.assertEqual(self.out["held_out_tests"], len(met))
        self.assertIn(f"{len(met)} passed it and met the held-out half once", self.out["narrative"])

    def test_the_rounds_never_trade_false_alarms_for_coverage(self):
        rounds = self.out["rounds"]
        for a, b in zip(rounds, rounds[1:]):
            self.assertGreaterEqual(b["caught"], a["caught"])
        for r in rounds:
            self.assertLessEqual(r["fpr"], forge.FPR_MAX)

    def test_the_suite_accounts_for_every_wrong_run(self):
        caught = {x for e in self.out["suite"] for x in e["adds"]}
        self.assertEqual(len(caught) + len(self.out["uncaught"]), self.out["wrong"])
        self.assertFalse(caught & set(self.out["uncaught"]))

    def test_refinements_are_built_from_the_learn_half_and_capped(self):
        refined = [e for e in self.out["tested"] if e["source"] == "refined"]
        self.assertTrue(refined, "a noisy eval was narrowed")
        for r in self.out["rounds"][1:]:
            self.assertLessEqual(r["tried"], forge.ROUND_CAP + 20)

    def test_a_reader_mark_becomes_a_candidate(self):
        # a step of a wrong run that carries a mark
        from agentdiff import excerpt
        for t in self.traces:
            marks = excerpt.notable_steps(t, self.gold.get("policy"))
            if marks and t.agent.name == "drift-lh":
                seed = {"task": t.task.id, "agent": t.agent.name, "step": marks[0]["index"]}
                break
        out = forge.forge(self.traces, self.gold, self.gold.get("policy"), seeds=[seed])
        self.assertEqual(out["seeds"], 1)
        self.assertTrue(any(e["source"] == "seed" or "reader" in e.get("why", "") for e in out["tested"]))

    def test_deterministic(self):
        again = forge.forge(self.traces, self.gold, self.gold.get("policy"))
        self.assertEqual(json.dumps(again, sort_keys=True, default=str),
                         json.dumps(self.out, sort_keys=True, default=str))


class JudgeInTheLoopTest(unittest.TestCase):
    """The judge proposes; the held-out half disposes."""

    @classmethod
    def setUpClass(cls):
        cls.traces, cls.gold = _suite()
        cls.halves = split(t.task.id for t in cls.traces)

    def test_the_judge_never_sees_the_held_out_half(self):
        from agentdiff.harness.forge_judge import make_proposer
        from agentdiff.harness.providers import ScriptedProvider
        seen = []

        def script(messages, tools):
            seen.append(json.dumps(messages, default=str))
            n = sum(1 for m in messages if m.get("role") == "tool")
            if n == 0:
                return {"tool_calls": [{"name": "runs", "arguments": {}}]}
            if n == 1:
                return {"tool_calls": [{"name": "try_rule", "arguments": {"rule": "mark:cycle"}}]}
            return {"text": json.dumps({"rules": [{"rule": "tool_called:run_checks>=40", "why": "checks without end"},
                                                   {"rule": "not_a_rule:1", "why": "typo"}]})}
        dicts = [t.to_dict() for t in self.traces]
        proposer = make_proposer(lambda: ScriptedProvider(script), dicts)
        out = forge.forge(self.traces, self.gold, self.gold.get("policy"), proposer=proposer, rounds=3)
        held_out = self.halves[1]
        self.assertTrue(seen, "the judge ran")
        for text in seen:
            for tid in held_out:
                self.assertNotIn(tid, text, "a held-out task reached the judge")
        self.assertTrue(out["judge"]["used"])
        self.assertTrue(any(e["source"] == "judge" for e in out["tested"]))
        # the rule that did not parse was refused, and the next round was told
        self.assertEqual([r["round"] for r in out["judge"]["rounds"]], [2, 3])
        self.assertTrue(any("not_a_rule" in text and "does_not_parse" in text for text in seen),
                        "round 3's brief carries round 2's refusal")

    def test_try_rule_scores_the_learn_half_only(self):
        captured = {}

        def proposer(rnd, context):
            captured["try"] = context["try_learn"]("mark:unrecovered_error")
            captured["bad"] = context["try_learn"]("nope:1")
            captured["learn"] = context["learn_tasks"]
            return []
        forge.forge(self.traces, self.gold, self.gold.get("policy"), proposer=proposer, rounds=2)
        got = captured["try"]
        learn_runs = sum(1 for t in self.traces if t.task.id in set(self.halves[0]))
        self.assertEqual(got["wrong"] + got["right"], learn_runs)
        self.assertEqual(sorted(captured["learn"]), sorted(self.halves[0]))
        self.assertFalse(captured["bad"]["parses"])

    def test_a_judge_that_fails_does_not_take_the_forge_with_it(self):
        def proposer(rnd, context):
            raise RuntimeError("network down")
        out = forge.forge(self.traces, self.gold, self.gold.get("policy"), proposer=proposer, rounds=3)
        self.assertTrue(out["measurable"])
        self.assertTrue(out["suite"])


class LedgerTest(unittest.TestCase):
    def test_the_suite_is_carried_and_re_tested_and_a_corpus_read_twice_is_not_counted(self):
        traces, gold = _suite()
        halves = split(t.task.id for t in traces)
        first = [t for t in traces if t.task.id in halves[0] or t.task.id in halves[1][:4]]
        second = [t for t in traces if t.task.id not in {x.task.id for x in first}] + \
                 [t for t in traces if t.task.id in halves[0][:3]]
        a = forge.forge(first, gold, gold.get("policy"))
        nxt = a["ledger"]["next"]
        self.assertEqual(sorted(nxt["evals"]), sorted(e["id"] for e in a["suite"]))
        b = forge.forge(second, gold, gold.get("policy"), ledger=nxt)
        self.assertEqual(len(b["ledger"]["rechecked"]), len(nxt["evals"]))
        for r in b["ledger"]["rechecked"]:
            self.assertIn(r["status"], ("kept", "retired_noisy", "blind_here", "untestable"))
        c = forge.forge(first, gold, gold.get("policy"), ledger=b["ledger"]["next"])
        self.assertTrue(c["ledger"]["seen_before"])
        self.assertEqual(c["ledger"]["rechecked"], [])

    def test_a_file_that_is_not_an_eval_ledger_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "e.json"
            self.assertIsNone(forge.load_ledger(p))
            p.write_text('{"version": 1, "lessons": {}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                forge.load_ledger(p)


class CommandsTest(unittest.TestCase):
    def test_batch_writes_the_eval_ledger_and_reads_seeds(self):
        if not SUITE.is_dir():
            raise unittest.SkipTest("the long-horizon suite is not generated")
        with tempfile.TemporaryDirectory() as tmp:
            seeds = Path(tmp) / "seeds.json"
            seeds.write_text(json.dumps({"seeds": [{"task": "L03_data_backfill", "agent": "drift-lh", "step": 5}]}))
            ledger = Path(tmp) / "evals.json"
            done = subprocess.run([sys.executable, "-m", "agentdiff", "batch", str(SUITE), "-o", str(Path(tmp) / "o"),
                                   "--golden", str(GOLDEN), "--evals", str(ledger), "--seeds", str(seeds)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertIn("Evals forged:", done.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertTrue(data["evals"])
            agg = json.loads((Path(tmp) / "o" / "aggregate.json").read_text(encoding="utf-8"))
            self.assertEqual(agg["forge"]["seeds"], 1)
            self.assertNotIn("next", agg["forge"]["ledger"])

    def test_forge_command_with_a_scripted_judge(self):
        if not SUITE.is_dir():
            raise unittest.SkipTest("the long-horizon suite is not generated")
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "judge.json"
            script.write_text(json.dumps({"model": "scripted-judge", "turns": [
                {"tool_calls": [{"name": "try_rule", "arguments": {"rule": "mark:cycle"}}]},
                {"text": json.dumps({"rules": [{"rule": "tool_called:run_checks>=40", "why": "checks without end"}]})},
            ]}))
            done = subprocess.run([sys.executable, "-m", "agentdiff", "forge", str(SUITE), "--golden", str(GOLDEN),
                                   "--judge", f"j=scripted:{script}", "-o", str(Path(tmp) / "f")],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertIn("judge:", done.stdout)
            agg = json.loads((Path(tmp) / "f" / "aggregate.json").read_text(encoding="utf-8"))
            self.assertTrue(agg["forge"]["judge"]["used"])
            self.assertEqual(agg["forge"]["judge"]["saw"], "the learn half only; the held-out half decided")


if __name__ == "__main__":
    unittest.main()
