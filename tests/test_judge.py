"""A second model judges the output: recorded beside the grade, applied
only on request, never confused with an exact match."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.harness.judge import (DEFAULT_RUBRIC, LONG_RUN_RUBRIC, RUBRICS, STEP_EXCERPT,
                                       judge_many, judge_trace, resolve_rubric)
from deepcompare.harness.providers import ScriptedProvider
from deepcompare.tracedb import TraceDB

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "traces"


def trace(name):
    return json.loads((DEMO / name).read_text(encoding="utf-8"))


def scripted(text, model="judge-model"):
    return ScriptedProvider([{"text": text, "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 5}}], model=model)


class JudgeTest(unittest.TestCase):
    def test_the_verdict_is_recorded_beside_the_grade_and_not_applied(self):
        t = trace("t05_flight_duration__bolt-v3.json")
        block = judge_trace(t, scripted('{"success": false, "score": 0.1, "rationale": "the durations ignore time zones"}'))
        self.assertFalse(block["success"])
        self.assertEqual(block["score"], 0.1)
        self.assertIn("time zones", block["rationale"])
        self.assertEqual(block["model"], "judge-model")
        self.assertEqual(block["prior"]["graded_by"], "exact-match")
        self.assertTrue(block["agrees_with_prior"])
        self.assertFalse(block["applied"])
        self.assertFalse(t["outcome"]["success"])
        self.assertNotIn("graded_by", t["outcome"])
        Trajectory.from_dict(t)

    def test_apply_replaces_the_grade_and_says_a_model_did(self):
        t = trace("t05_flight_duration__bolt-v3.json")
        block = judge_trace(t, scripted('{"success": true, "score": 0.9, "rationale": "close enough"}'), apply=True)
        self.assertTrue(block["applied"])
        self.assertFalse(block["agrees_with_prior"])
        self.assertTrue(t["outcome"]["success"])
        self.assertEqual(t["outcome"]["score"], 0.9)
        self.assertEqual(t["outcome"]["graded_by"], "model")

    def test_a_self_judgement_is_flagged(self):
        t = trace("t05_flight_duration__atlas-v2.json")
        block = judge_trace(t, scripted('{"success": true, "score": 1}', model=t["agent"]["model"]))
        self.assertTrue(block["self_judged"])

    def test_a_non_json_reply_is_no_verdict(self):
        t = trace("t05_flight_duration__atlas-v2.json")
        block = judge_trace(t, scripted("I think it is fine."))
        self.assertIsNone(block["success"])
        self.assertIn("JSON", block["error"])
        self.assertIsNone(t["outcome"].get("graded_by"))

    def test_the_rubric_and_steps_reach_the_judge(self):
        seen = {}
        def script(messages, tools):
            seen["messages"] = messages
            return {"text": '{"success": true, "score": 1, "rationale": "ok"}', "tool_calls": [], "usage": {}}
        t = trace("t05_flight_duration__atlas-v2.json")
        judge_trace(t, ScriptedProvider(script, model="j"), rubric="Be lenient. Reply JSON.", with_steps=True)
        self.assertEqual(seen["messages"][0]["content"], "Be lenient. Reply JSON.")
        self.assertIn("STEPS THE AGENT TOOK", seen["messages"][1]["content"])
        self.assertIn("REFERENCE ANSWER", seen["messages"][1]["content"])

    def test_judge_many_counts_agreement(self):
        traces = [trace("t05_flight_duration__atlas-v2.json"), trace("t05_flight_duration__bolt-v3.json")]
        counts = judge_many(traces, lambda: scripted('{"success": true, "score": 1, "rationale": "yes"}'))
        self.assertEqual(counts["judged"], 2)
        self.assertEqual(counts["agreed_with_prior"], 1)
        self.assertEqual(counts["disagreed_with_prior"], 1)

    def test_the_cli_judges_a_directory_with_a_scripted_provider_and_updates_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "traces"; d.mkdir()
            for name in ("t05_flight_duration__atlas-v2.json", "t05_flight_duration__bolt-v3.json"):
                (d / name).write_text((DEMO / name).read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "judge.json"
            script.write_text(json.dumps([{"text": '{"success": false, "score": 0.2, "rationale": "scripted"}', "tool_calls": []}]), encoding="utf-8")
            db = Path(tmp) / "t.sqlite"
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "judge", str(d), "--provider", f"j=scripted:{script}", "--db", str(db)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("2 judged", proc.stdout)
            judged = json.loads((d / "t05_flight_duration__atlas-v2.json").read_text(encoding="utf-8"))
            self.assertFalse(judged["outcome"]["judge"]["success"])
            self.assertTrue(judged["outcome"]["success"], "not applied: the exact-match grade stands")
            with TraceDB(db) as store:
                self.assertEqual(store.count(source="judge"), 2)
                self.assertIsNotNone(store.get("t05_flight_duration__bolt-v3")["outcome"]["judge"])


    def test_the_cli_carries_the_rubric_name_and_the_cap_onto_the_verdict(self):
        """The two things that decide what a verdict means — which question
        was asked, and how much of the run was shown — have to survive the
        command line, or a card cannot report them."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "traces"; d.mkdir()
            name = "L01_service_migration__drift-lh.json"
            source = ROOT / "demo" / "horizon" / "suite" / name
            if not source.is_file():
                self.skipTest("the long-horizon suite is not generated")
            (d / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "judge.json"
            script.write_text(json.dumps([{"text": '{"success": false, "score": 0.2, "rationale": "scripted"}'}]),
                              encoding="utf-8")
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "judge", str(d),
                                   "--provider", f"j=scripted:{script}", "--with-steps",
                                   "--rubric", "long-run", "--steps-cap", "60"],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            block = json.loads((d / name).read_text(encoding="utf-8"))["outcome"]["judge"]
            self.assertEqual(block["rubric_name"], "long-run")
            self.assertEqual(block["rubric"], LONG_RUN_RUBRIC)
            self.assertEqual(block["steps_shown"], 60)
            self.assertGreater(block["steps_total"], 200)
            self.assertIn("180 named as omitted", block["steps_basis"] or "")


@unittest.skipUnless((ROOT / "demo" / "horizon" / "suite").is_dir(), "the long-horizon suite is not generated")
class FocusedJudgingTest(unittest.TestCase):
    """`--focus`: the steps shown are chosen by what the run itself flags.

    The engine already knows where a run stops behaving like one that is
    going well, and it knows it without being told what the task was. That
    reading is free and it is a far better guide to what to put in a prompt
    than where the steps happen to fall.
    """

    SUITE = ROOT / "demo" / "horizon" / "suite"
    GOLDEN = ROOT / "demo" / "horizon" / "suite_golden.json"

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(cls.GOLDEN.read_text(encoding="utf-8"))
        cls.tasks = {t["id"]: t for t in cls.golden["tasks"]}

    def judged(self, name, **kwargs):
        raw = json.loads((self.SUITE / f"{name}__drift-lh.json").read_text(encoding="utf-8"))
        seen = []

        def script(messages, tools):
            seen.append(messages)
            return {"text": '{"success": false, "score": 0.3, "rationale": "x"}'}
        block = judge_trace(raw, ScriptedProvider(script, model="j"), with_steps=True, **kwargs)
        return block, seen[0][1]["content"]

    def policy_for(self, name):
        from deepcompare.excerpt import effective_policy
        return effective_policy(self.golden.get("policy"), self.tasks[name])

    def test_the_forbidden_write_reaches_the_judge_and_would_not_have(self):
        """`forgotten_constraint` breaks a rule at step 176 of 202. By
        position that step is in neither end of the excerpt; by structure
        it is the first thing shown."""
        name = "L05_security_patch"
        focused, prompt = self.judged(name, focus=True, policy=self.policy_for(name))
        self.assertEqual(focused["steps_chosen_by"], "structure")
        self.assertIn("write legacy/shim.py", prompt, "the step that breaks the rule")
        self.assertIn("policy_breach", focused["steps_basis"])
        _by_position, plain = self.judged(name)
        self.assertNotIn("write legacy/shim.py", plain, "position puts it in neither end")

    def test_the_verdict_records_how_its_excerpt_was_chosen(self):
        name = "L03_data_backfill"
        focused, _ = self.judged(name, focus=True)
        positional, _ = self.judged(name)
        whole, _ = self.judged(name, focus=True, cap=10_000)
        self.assertEqual([focused["steps_chosen_by"], positional["steps_chosen_by"], whole["steps_chosen_by"]],
                         ["structure", "position", "all"])
        self.assertEqual(focused["steps_shown"], positional["steps_shown"], "the same budget, spent differently")

    def test_every_gap_in_a_focused_excerpt_is_named(self):
        """A gap the reader cannot see turns 'the agent did these things'
        into 'the agent did these things among others', and those are
        different claims to judge."""
        _block, prompt = self.judged("L03_data_backfill", focus=True)
        gaps = [line for line in prompt.splitlines() if "omitted here" in line]
        self.assertGreater(len(gaps), 3)
        shown = [line for line in prompt.splitlines() if line.startswith("[")]
        self.assertEqual(len(shown), 40)

    def test_the_judge_is_never_shown_the_golden_set(self):
        """The rubric, the prompt and the selector between them must not
        contain a milestone, the expected answer or the name of the mode.
        A judge handed those is being graded on reading, not on judging."""
        name = "L01_service_migration"
        task = self.tasks[name]
        _block, prompt = self.judged(name, focus=True, rubric="long-run", policy=self.policy_for(name))
        for milestone in task["milestones"]:
            for field in ("id", "label"):
                if milestone.get(field):
                    self.assertNotIn(str(milestone[field]), prompt, field)
        self.assertNotIn(task["failure_mode"], prompt)
        self.assertNotIn(str(task.get("failure_mode_detail") or "\x00"), prompt)

    def test_a_judged_corpus_shows_its_judge_on_the_page(self):
        """`batch` built the card without the raw traces, so a corpus that
        had been judged reported *"No judging model"* — the verdict on the
        trace, the engine able to read it, and the reader told there was
        none. The card only needs each trace's `outcome`, which is a few
        kilobytes over a corpus whose traces are half a gigabyte."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp) / "traces"
            traces.mkdir()
            for path in sorted(self.SUITE.glob("*.json"))[:6]:
                (traces / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "j.json"
            script.write_text(json.dumps([{"text": '{"success": false, "score": 0.2, "rationale": "s"}'}]),
                              encoding="utf-8")
            judged = subprocess.run([sys.executable, "-m", "deepcompare", "judge", str(traces),
                                     "--provider", f"j=scripted:{script}"],
                                    cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(judged.returncode, 0, judged.stderr)
            out = Path(tmp) / "out"
            done = subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(traces), "-o", str(out),
                                   "--golden", str(self.GOLDEN)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            card = json.loads((out / "aggregate.json").read_text(encoding="utf-8"))["scorecard"]
            agent = next(a for a in card["agents"].values() if a["judge"])
            self.assertEqual(agent["judge"]["model"], "script")
            self.assertTrue(card["detection"]["judge"]["measurable"],
                            card["detection"]["judge"].get("reason"))

    def test_a_selector_that_cannot_run_falls_back_rather_than_failing(self):
        """A broken reading of the run is a reason to show the ends, not a
        reason to refuse to judge."""
        raw = json.loads((self.SUITE / "L03_data_backfill__drift-lh.json").read_text(encoding="utf-8"))
        with mock.patch("deepcompare.excerpt.focus", side_effect=RuntimeError("no")):
            block = judge_trace(raw, scripted('{"success": true, "score": 1, "rationale": "x"}'),
                                with_steps=True, focus=True)
        self.assertIsNone(block["error"])
        self.assertEqual(block["steps_chosen_by"], "position")
        self.assertIn("by position", block["steps_basis"])


if __name__ == "__main__":
    unittest.main()


class JudgeAtLengthTest(unittest.TestCase):
    """What the judge is *shown* of a long run, and whether the block says so.

    A three-hundred-step run does not fit in a prompt. The question is not
    whether to cut it — it is whether the cut is visible. A judge handed
    the first forty steps of a three-hundred-step run is being shown the
    part that went fine and asked about the part it cannot see, and a
    verdict recorded without that fact reads like a verdict about the run.
    """

    @staticmethod
    def long_trace(n=300):
        steps = [{"index": i, "type": "tool_call", "name": f"call_{i}",
                  "input": f"in {i}", "output": f"out {i}"} for i in range(n)]
        steps.append({"index": n, "type": "answer", "name": "", "input": "", "output": "done"})
        return {"task": {"id": "long", "prompt": "do the long thing"},
                "agent": {"name": "a", "model": "m"},
                "steps": steps, "outcome": {"success": True, "answer": "all done"}}

    def sent(self, provider):
        return provider.seen[0][1]["content"]

    def recording_provider(self, text='{"success": true, "score": 1, "rationale": "ok"}'):
        seen = []

        def script(messages, tools):
            seen.append(messages)
            return {"text": text}
        provider = ScriptedProvider(script, model="j")
        provider.seen = seen
        return provider

    def test_a_long_run_is_shown_as_an_excerpt_that_names_what_it_omits(self):
        provider = self.recording_provider()
        block = judge_trace(self.long_trace(), provider, with_steps=True)
        prompt = self.sent(provider)
        self.assertIn("300 steps", block["steps_basis"])
        self.assertEqual((block["steps_shown"], block["steps_total"]), (STEP_EXCERPT, 300))
        # the gap is named in the prompt, with the indexes it covers
        self.assertIn("260 steps omitted here (indexes 20-279)", prompt)
        # and both ends are really there: the failure in a long run is late,
        # so a head-only excerpt would be the wrong half
        self.assertIn("call_0:", prompt)
        self.assertIn("call_299:", prompt)
        self.assertNotIn("call_150:", prompt)

    def test_a_short_run_is_shown_whole_and_says_so(self):
        provider = self.recording_provider()
        block = judge_trace(self.long_trace(6), provider, with_steps=True)
        self.assertEqual((block["steps_shown"], block["steps_total"]), (6, 6))
        self.assertEqual(block["steps_basis"], "every step of the run")
        self.assertNotIn("omitted", self.sent(provider))

    def test_without_steps_the_block_says_the_verdict_is_about_the_answer(self):
        block = judge_trace(self.long_trace(), self.recording_provider(), with_steps=False)
        self.assertEqual((block["steps_shown"], block["steps_total"]), (0, 0))
        self.assertIn("answer only", block["steps_basis"])

    def test_the_cap_is_the_caller_s_to_raise(self):
        provider = self.recording_provider()
        block = judge_trace(self.long_trace(), provider, with_steps=True, cap=100)
        self.assertEqual(block["steps_shown"], 100)
        self.assertIn("200 steps omitted", self.sent(provider))


class RubricTest(unittest.TestCase):
    def test_a_rubric_can_be_named_and_the_name_is_recorded(self):
        """Two cards are comparable when they asked the same question, so
        the question travels with the verdict."""
        provider = JudgeAtLengthTest().recording_provider()
        block = judge_trace(JudgeAtLengthTest.long_trace(4), provider, rubric="long-run")
        self.assertEqual(block["rubric_name"], "long-run")
        self.assertEqual(block["rubric"], LONG_RUN_RUBRIC)
        self.assertEqual(provider.seen[0][0]["content"], LONG_RUN_RUBRIC)

    def test_the_default_is_named_not_called_custom(self):
        self.assertEqual(resolve_rubric(None), (DEFAULT_RUBRIC, "strict"))
        self.assertEqual(resolve_rubric(DEFAULT_RUBRIC)[1], "strict")
        self.assertEqual(resolve_rubric("be lenient")[1], "custom")

    def test_the_long_run_rubric_asks_after_the_work_not_the_prose(self):
        """The rubric is the whole of what distinguishes this judge from
        one that reads a summary, so it is pinned rather than assumed."""
        self.assertIn("not whether its final answer reads well", LONG_RUN_RUBRIC)
        self.assertIn("excerpt", LONG_RUN_RUBRIC)
        for text in RUBRICS.values():
            self.assertIn("Reply with JSON only", text)
        # the judge never sees the golden set: a rubric that quoted the
        # milestones would be handing it the answers
        for text in RUBRICS.values():
            self.assertNotIn("milestone", text.lower())
