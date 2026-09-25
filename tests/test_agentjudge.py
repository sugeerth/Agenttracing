"""Agent-as-a-Judge: a judge that reads the run instead of being handed a window.

Follows Zhuge et al. 2024, and the tests that matter are the ones about
what it is *not* allowed to do: see the mark scheme, carry a judgement
from one requirement to the next, or move a number the engine computed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare.harness.agentjudge import (  # noqa: E402
    SYSTEM, _same_family, judge_trace, requirements_of, trace_tools)
from deepcompare.harness.providers import ScriptedProvider  # noqa: E402
from deepcompare.scorecard import cohens_kappa, scorecard  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

SUITE = ROOT / "demo" / "horizon" / "suite"
GOLDEN = ROOT / "demo" / "horizon" / "suite_golden.json"


def navigator(verdict='{"success": false, "score": 0.3, "rationale": "stand-in"}', steps=("graph", "flags")):
    """A stand-in that navigates before answering. It is not a model and
    its verdicts are facts about this function, not about any judge."""
    def factory():
        state = {"n": 0}

        def script(messages, tools):
            state["n"] += 1
            if state["n"] <= len(steps):
                return {"text": "", "tool_calls": [{"name": steps[state["n"] - 1], "arguments": {}}]}
            return {"text": verdict}
        return ScriptedProvider(script, model="stand-in-nav")
    return factory


@unittest.skipUnless(SUITE.is_dir() and GOLDEN.is_file(), "the long-horizon suite is not generated")
class WhatTheJudgeMaySeeTest(unittest.TestCase):
    """The line between the task and the mark scheme."""

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.task = next(t for t in cls.golden["tasks"] if t["id"] == "L07_compliance_evidence")
        cls.raw = json.loads((SUITE / "L07_compliance_evidence__drift-lh.json").read_text(encoding="utf-8"))

    def test_the_requirements_are_the_spec_and_never_the_evidence(self):
        """A milestone says what had to happen *and* what the world said
        when it did. The first is the task; the second is the answer. A
        judge shown the second is being scored for reading."""
        reqs = requirements_of(self.task)
        self.assertEqual(len(reqs), len(self.task["milestones"]))
        blob = json.dumps(reqs)
        for stone in self.task["milestones"]:
            evidence = stone.get("evidence")
            if evidence:
                self.assertNotIn(str(evidence), blob, stone.get("id"))
        for leak in ("evidence", "in", "expected"):
            self.assertNotIn(f'"{leak}"', blob)

    def test_nothing_else_from_the_golden_task_leaves_that_function(self):
        reqs = requirements_of({"milestones": [{"id": "a", "label": "do a", "evidence": "a: 12 passed"}],
                                "expected": "42", "failure_mode": "skipped_unit",
                                "expected_evidence": ["x"]})
        self.assertEqual(reqs, [{"id": "a", "requirement": "do a"}])

    def test_a_task_with_no_milestones_says_so_rather_than_scoring_nothing(self):
        block = judge_trace(dict(self.raw), navigator(), golden_task={}, out_dir=None)
        self.assertEqual(block["of"], 1)
        self.assertIn("needs requirements", block["basis"])


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class TheJudgesInstrumentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads((SUITE / "L07_compliance_evidence__drift-lh.json").read_text(encoding="utf-8"))
        cls.tools = {t.name: t for t in trace_tools(cls.raw)}

    def test_the_four_modules_are_the_ones_the_ablation_kept(self):
        """graph, locate, read, retrieve — the paper's winning set, over a
        trace instead of a workspace. `flags` is retrieve: the
        deterministic reading, handed over as a tool."""
        self.assertEqual(sorted(self.tools), ["flags", "graph", "locate", "read"])
        for tool in self.tools.values():
            self.assertEqual(tool.effect, "read", f"{tool.name} must not be able to change the run")

    def test_graph_says_how_long_the_run_is_and_what_it_did(self):
        g = self.tools["graph"].fn()
        self.assertEqual(g["steps"], len(self.raw["steps"]))
        self.assertIn("read_file", g["tools"])
        self.assertGreater(g["tools"]["read_file"], 10)

    def test_locate_finds_the_subject_and_says_when_it_truncated(self):
        hit = self.tools["locate"].fn("encryption")
        self.assertGreater(hit["found"], 10)
        self.assertLessEqual(hit["shown"], hit["found"])
        if hit["found"] > hit["shown"]:
            self.assertIn("narrow", hit["note"])

    def test_read_returns_the_range_asked_for(self):
        got = self.tools["read"].fn(120, 128)
        self.assertEqual(len(got["steps"]), 9)
        self.assertTrue(got["steps"][0].startswith("[120]"))

    def test_flags_hands_over_the_deterministic_reading(self):
        """The engine already knows where this run stops behaving like one
        that is going well, without being told what the task was. On this
        run the strongest place it names is the beat the rhythm skipped —
        which is the failure."""
        out = self.tools["flags"].fn()
        self.assertTrue(out["measurable"])
        self.assertEqual(out["places"][0]["kind"], "skipped_beat")
        self.assertIn("not a verdict", out["note"])

    def test_a_trace_the_analysis_cannot_read_leaves_the_judge_its_other_tools(self):
        broken = {"steps": [{"index": 0, "type": "answer", "name": "", "input": "", "output": "x"}],
                  "task": {"id": "t", "prompt": "p"}, "outcome": {"answer": "x"}}
        out = {t.name: t for t in trace_tools(broken)}["flags"].fn()
        self.assertFalse(out["measurable"])
        self.assertIn("locate and read", out["reason"])


@unittest.skipUnless(SUITE.is_dir() and GOLDEN.is_file(), "the long-horizon suite is not generated")
class HowItJudgesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.task = next(t for t in cls.golden["tasks"] if t["id"] == "L07_compliance_evidence")
        cls.raw = json.loads((SUITE / "L07_compliance_evidence__drift-lh.json").read_text(encoding="utf-8"))

    def block(self, **kwargs):
        return judge_trace(dict(self.raw), navigator(), golden_task=self.task,
                           policy=self.golden.get("policy"), out_dir=None, **kwargs)

    def test_one_verdict_per_requirement_judged_independently(self):
        """The paper's ablation found memory actively harmful: a wrong
        judgement carried forward starts a chain of them. Each requirement
        here gets a fresh provider and a fresh loop."""
        block = self.block()
        self.assertEqual(block["kind"], "agent-as-a-judge")
        self.assertEqual(block["of"], len(self.task["milestones"]))
        self.assertEqual(block["judged"], block["of"])
        self.assertFalse(block["memory_between_requirements"])
        self.assertEqual(len({r["requirement"] for r in block["requirements"]}), block["of"])

    def test_the_judge_s_own_run_is_recorded_like_any_other_agent_s(self):
        """A judge that answers without looking is the failure this whole
        approach exists to avoid, so whether it looked is on the record."""
        block = self.block()
        self.assertTrue(block["looked_at_all"])
        self.assertGreater(block["tool_calls"], 0)
        self.assertEqual(block["tool_calls"], sum(r["tool_calls"] for r in block["requirements"]))
        row = block["requirements"][0]
        self.assertEqual(sorted(row["tools_used"]), ["flags", "graph"])
        self.assertTrue(row["looked_before_answering"])

    def test_a_judge_that_answers_without_looking_is_visible(self):
        straight = judge_trace(dict(self.raw), navigator(steps=()), golden_task=self.task, out_dir=None)
        self.assertFalse(straight["looked_at_all"])
        self.assertEqual(straight["tool_calls"], 0)

    def test_it_does_not_replace_the_grade_unless_asked(self):
        raw = dict(self.raw)
        before = raw["outcome"]["success"]
        block = judge_trace(raw, navigator(), golden_task=self.task, out_dir=None)
        self.assertFalse(block["applied"])
        self.assertEqual(raw["outcome"]["success"], before)
        applied = judge_trace(raw, navigator(), golden_task=self.task, apply=True, out_dir=None)
        self.assertTrue(applied["applied"])
        self.assertEqual(raw["outcome"]["graded_by"], "agent-model")

    def test_a_judge_that_returns_no_json_is_an_error_not_a_pass(self):
        block = judge_trace(dict(self.raw), navigator(verdict="Looks fine to me."),
                            golden_task=self.task, out_dir=None)
        self.assertEqual(block["judged"], 0)
        self.assertTrue(block["errors"])
        self.assertIsNone(block["success"])


class SelfPreferenceTest(unittest.TestCase):
    """A judge scores its own family's work a reported 10–25% higher, so
    the guard has to be about families, not strings."""

    def test_the_same_family_under_a_different_name_is_still_self_judging(self):
        self.assertTrue(_same_family("gpt-4o", "gpt-4o-mini"))
        self.assertTrue(_same_family("claude-opus-4", "claude-haiku-4"))
        self.assertFalse(_same_family("gpt-4o", "claude-opus-4"))
        self.assertFalse(_same_family("", "gpt-4o"))

    def test_the_rubric_tells_the_judge_not_to_reward_length(self):
        """Verbosity bias: long answers score higher even when they are
        worse, and the mitigation the literature gives is to separate
        correctness from style in the instruction."""
        self.assertIn("Length is not quality", SYSTEM)
        self.assertIn("not the prose", SYSTEM)
        self.assertIn("treat it as not done", SYSTEM)


class AgreementTest(unittest.TestCase):
    """Cohen's κ rather than raw agreement."""

    def test_raw_agreement_of_eighty_percent_can_be_no_agreement_at_all(self):
        """The case that makes the point: a judge that says pass to
        everything agrees with a mostly-passing truth 80% of the time and
        has told you nothing. κ says 0."""
        got = cohens_kappa([(True, True)] * 8 + [(True, False)] * 2)
        self.assertEqual(got["observed"], 0.8)
        self.assertEqual(got["kappa"], 0.0)
        self.assertEqual(got["reading"], "poor")

    def test_perfect_agreement_on_a_balanced_set_is_one(self):
        got = cohens_kappa([(True, True), (False, False)] * 4)
        self.assertEqual(got["kappa"], 1.0)
        self.assertEqual(got["reading"], "strong")

    def test_a_constant_verdict_is_unmeasurable_rather_than_perfect(self):
        got = cohens_kappa([(True, True)] * 5)
        self.assertFalse(got["measurable"])
        self.assertIn("cannot be shown to agree", got["reason"])

    def test_too_few_runs_is_unmeasurable_rather_than_noisy(self):
        self.assertFalse(cohens_kappa([(True, False)])["measurable"])


@unittest.skipUnless(SUITE.is_dir() and GOLDEN.is_file(), "the long-horizon suite is not generated")
class BesideTheCardTest(unittest.TestCase):
    """The invariant that applies to every judge here."""

    def test_the_agent_judge_cannot_move_a_number_the_engine_computed(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        tasks = {t["id"]: t for t in golden["tasks"]}
        paths = sorted(SUITE.glob("L0[1-6]*.json"))
        trajectories = [Trajectory.from_json(p) for p in paths]

        def card(verdict):
            raws = {}
            for path in paths:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if verdict is not None:
                    raw.setdefault("outcome", {})["agent_judge"] = {
                        "kind": "agent-as-a-judge", "model": "stand-in", "success": verdict,
                        "score": 1.0 if verdict else 0.0, "judged": 3, "of": 3,
                        "met": 3 if verdict else 0, "failed_requirements": [],
                        "turns": 9, "tool_calls": 6, "looked_at_all": True,
                        "self_judged": False, "agrees_with_prior": None, "applied": False,
                        "prior": {"success": raw["outcome"].get("success")}}
                raws[raw["trace_id"]] = raw
            return scorecard(trajectories, {"tasks": tasks, "policy": golden["policy"]}, raws=raws)

        plain = card(None)["detection"]
        keys = ("caught", "total", "missed", "graded_pass", "by_signal", "controls", "narrative")
        for said in (True, False):
            det = card(said)["detection"]
            self.assertEqual({k: det[k] for k in keys}, {k: plain[k] for k in keys},
                             f"an agent judge saying {said} to everything moved a computed number")
            self.assertTrue(det["agent_judge"]["measurable"])

    def test_without_one_the_card_names_the_command_that_makes_one(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        tasks = {t["id"]: t for t in golden["tasks"]}
        card = scorecard([Trajectory.from_json(p) for p in sorted(SUITE.glob("L0[1-3]*.json"))],
                         {"tasks": tasks, "policy": golden["policy"]})
        block = card["detection"]["agent_judge"]
        self.assertFalse(block["measurable"])
        self.assertIn("--agent", block["reason"])


@unittest.skipUnless(SUITE.is_dir() and GOLDEN.is_file(), "the long-horizon suite is not generated")
class CommandTest(unittest.TestCase):
    def test_the_cli_judges_by_requirement_and_records_the_look_ups(self):
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp) / "t"
            traces.mkdir()
            name = "L07_compliance_evidence__drift-lh.json"
            (traces / name).write_text((SUITE / name).read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "nav.json"
            script.write_text(json.dumps({"model": "stand-in-nav", "turns": [
                {"text": "", "tool_calls": [{"name": "graph", "arguments": {}}]},
                {"text": '{"success": false, "score": 0.3, "rationale": "stand-in"}'}]}), encoding="utf-8")
            done = subprocess.run([sys.executable, "-m", "deepcompare", "judge", str(traces), "--agent",
                                   "--provider", f"j=scripted:{script}", "--golden", str(GOLDEN)],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            self.assertIn("requirements met over", done.stdout)
            block = json.loads((traces / name).read_text(encoding="utf-8"))["outcome"]["agent_judge"]
            self.assertEqual(block["kind"], "agent-as-a-judge")
            self.assertEqual(block["of"], 8)
            self.assertTrue(block["looked_at_all"])


if __name__ == "__main__":
    unittest.main()
