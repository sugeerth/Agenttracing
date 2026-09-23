"""The long-horizon evaluation suite, and what the evaluation sees in it.

`demo/horizon/suite/` is sixteen long tasks and thirty-two runs: sixteen
that finish and sixteen that are correct for most of their length and then
exhibit one named long-horizon failure mode (`docs/HORIZON.md`).

What this file pins is not the suite's contents but **the evaluation's
performance on it** — the catch matrix: for each mode, the signal that
finds it, and for the one mode nothing finds, that nothing does. A test
that asserted only the catches would let the measurement quietly get
worse in the one place it is already blind.

Three properties hold the rest up.

*No false positives.* Twenty correct runs, and every dimension clean on
all of them. A measurement that flags a long run for being long is worse
than none, and these are the runs that would show it.

*The grade is the weakest instrument here.* Seven of the twelve failing
runs are graded a pass; six of those are caught by something else. The
test pins which, because that ratio is the argument for measuring a long
run's trajectory at all.

*The generator is deterministic.* The suite can be rebuilt from source
and compared byte for byte, so a change in these numbers is a change in
the engine, never in the data.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.harness.judge import STEP_EXCERPT  # noqa: E402
from deepcompare.scorecard import detection, score_run, scorecard, signals_of  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

SUITE = ROOT / "demo" / "horizon" / "suite"
GOLDEN = ROOT / "demo" / "horizon" / "suite_golden.json"
GENERATOR = ROOT / "demo" / "horizon" / "generate_suite.py"
FINISHER, FAILER = "summit-lh", "drift-lh"


def _generator():
    spec = importlib.util.spec_from_file_location("horizon_suite_gen", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["horizon_suite_gen"] = module
    spec.loader.exec_module(module)
    return module


#: the catch matrix, as measured: (mode, graded pass?, the signals the
#: evaluation produces).  Every entry is pinned exactly, not as "at least
#: one signal" — a mode caught by a *different* dimension than before is a
#: change worth failing on, because which dimension catches a failure is
#: what tells a reader where to look.
EXPECTED = {
    # the skipped package's absence also leaves the final check failing and
    # unrepaired, so the unrecovered-error count sees it too
    "skipped_unit": (True, {"milestones", "grounded", "unrecovered"}),
    "stale_value": (False, {"grade", "grounded"}),
    "retry_stall": (False, {"grade", "milestones", "loop", "unrecovered", "redundant", "flags"}),
    # the edit after the green build: everything it reports was true when it
    # was measured and nothing has measured it since
    "regression": (True, {"flags"}),
    "forgotten_constraint": (True, {"policy", "flags"}),
    "budget_exhausted": (False, {"grade", "milestones", "unrecovered", "flags"}),
    "unverified_handoff": (True, {"milestones"}),
    "drift": (False, {"grade", "milestones", "flags"}),
    "swallowed_error": (True, {"unrecovered", "flags"}),
    "context_overflow": (True, {"loop", "redundant"}),
    "out_of_order": (True, {"order"}),
    "late_fault": (False, {"grade", "milestones", "kept_looking"}),
}


def _signals(run: dict) -> set:
    """Every dimension of the scorecard that says something is wrong —
    the engine's own reading (`scorecard.signals_of`), so this file pins
    what `agentdiff eval` reports rather than a copy of it."""
    return set(signals_of(run))


@unittest.skipUnless(SUITE.is_dir() and GOLDEN.is_file(), "the long-horizon suite is not generated")
class SuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gen = _generator()
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cls.tasks = {t["id"]: t for t in cls.golden["tasks"]}
        cls.runs = {}
        for task in cls.gen.TASKS:
            for agent in (FINISHER, FAILER):
                path = SUITE / f"{task.id}__{agent}.json"
                raw = json.loads(path.read_text(encoding="utf-8"))
                cls.runs[(task.id, agent)] = score_run(
                    Trajectory.from_json(path), cls.tasks[task.id], cls.golden["policy"], raw)

    # ------------------------------------------------------- the suite itself

    def test_the_suite_is_long_and_covers_every_mode_with_its_controls(self):
        self.assertEqual(len(self.gen.TASKS), 16)
        modes = [t.mode for t in self.gen.TASKS]
        self.assertEqual(sorted(set(modes) - {"control"}), sorted(EXPECTED))
        self.assertEqual(modes.count("control"), 4, "the false-positive controls")
        for (task_id, agent), run in self.runs.items():
            self.assertGreaterEqual(run["spend"]["steps"], 150, f"{task_id} {agent} is not a long run")
            self.assertGreaterEqual(run["milestones"]["total"], 7, task_id)

    def test_every_task_names_milestones_evidenced_by_the_world_not_the_agent(self):
        """A milestone matched against the agent's own summary measures the
        summary. The `skipped_unit` run says "package ledger complete" about
        a package it never opened; the milestone that catches it is the
        checker's output, so the golden set must not accept a claim."""
        for task in self.gen.TASKS:
            stones = self.tasks[task.id]["milestones"]
            self.assertGreaterEqual(len(stones), len(task.units) + 1)
            for stone in stones:
                self.assertEqual(stone["in"], "output", f"{task.id}/{stone['id']} may be matched against a claim")

    # -------------------------------------------------------- the catch matrix

    def test_the_catch_matrix_holds_including_what_is_missed(self):
        for task in self.gen.TASKS:
            if task.mode == "control":
                continue
            graded_pass, expected = EXPECTED[task.mode]
            run = self.runs[(task.id, FAILER)]
            self.assertEqual(run["success"] is not False, graded_pass,
                             f"{task.mode}: the grade changed side")
            self.assertEqual(_signals(run), expected, f"{task.mode} on {task.id}")

    def test_the_regression_run_is_a_regression_the_trace_can_show(self):
        """This mode was the suite's blind spot until the corpus was read
        rather than trusted.

        Its first version applied the breaking edit and then ran the whole
        suite green *after* it — a run labelled with a failure its own
        evidence denied. Nothing caught it because nothing was there. The
        edit now lands after the last verification and nothing runs again,
        which is the only shape in which "never re-checked" is a fact about
        the trace, and `unverified_write` sees it.

        The generator's own check (`manifests`) now asserts this property,
        so the corpus cannot quietly go back to being un-failable."""
        task = next(t for t in self.gen.TASKS if t.mode == "regression")
        run = self.runs[(task.id, FAILER)]
        self.assertTrue(run["success"], "the answer a correct run would have given")
        self.assertTrue(run["milestones"]["complete"], "every unit was green when it was checked")
        self.assertEqual([f["kind"] for f in run["safety"]["risk_flags"]], ["unverified_write"])
        raw = json.loads((SUITE / f"{task.id}__{FAILER}.json").read_text(encoding="utf-8"))
        self.assertIsNone(self.gen.manifests(raw, "regression", task))
        # and the check that would have caught the old corpus
        broken = {"steps": raw["steps"] + [{"index": 999, "type": "tool_call", "name": "run_checks",
                                            "input": "run_checks(scope='all')", "output": "980 passed"}],
                  "outcome": raw["outcome"]}
        self.assertIn("nothing is left unverified", self.gen.manifests(broken, "regression", task))

    def test_no_correct_run_is_flagged_for_being_long(self):
        """Twenty runs that do everything right, including the four control
        tasks' failing-agent runs. Any signal here is a false positive, and
        the whole suite exists to keep this at zero."""
        clean = 0
        for task in self.gen.TASKS:
            agents = (FINISHER, FAILER) if task.mode == "control" else (FINISHER,)
            for agent in agents:
                run = self.runs[(task.id, agent)]
                self.assertEqual(_signals(run), set(), f"false positive on {task.id} {agent}")
                clean += 1
        self.assertEqual(clean, 20)

    def test_the_grade_alone_misses_more_than_half_of_them(self):
        """The argument for the rest of the card, as a number: seven of the
        twelve failures are graded a pass, and every one of those is caught
        by a dimension that is not the outcome."""
        failing = [t for t in self.gen.TASKS if t.mode != "control"]
        graded_pass = [t for t in failing if self.runs[(t.id, FAILER)]["success"] is not False]
        self.assertEqual(len(graded_pass), 7, [t.mode for t in graded_pass])
        caught_otherwise = [t for t in graded_pass if _signals(self.runs[(t.id, FAILER)])]
        self.assertEqual(len(caught_otherwise), 7)

    # ------------------------------------------------ the dimensions themselves

    def test_milestones_name_where_each_run_stalled(self):
        stalls = {t.mode: self.runs[(t.id, FAILER)]["milestones"]["stalled_at"]
                  for t in self.gen.TASKS if t.mode != "control"}
        self.assertEqual(stalls["skipped_unit"], "unit_invoice")
        self.assertEqual(stalls["retry_stall"], "unit_jun")
        self.assertEqual(stalls["budget_exhausted"], "unit_shard_h")
        self.assertEqual(stalls["unverified_handoff"], "unit_encryption")
        self.assertEqual(stalls["late_fault"], "verified", "every unit green, the whole wrong")
        for mode in ("regression", "forgotten_constraint", "context_overflow", "out_of_order", "swallowed_error"):
            self.assertIsNone(stalls[mode], f"{mode} reaches every milestone")

    def test_the_aggregate_reports_progress_and_where_the_runs_stalled(self):
        trajectories = [Trajectory.from_json(SUITE / f"{t.id}__{a}.json")
                        for t in self.gen.TASKS for a in (FINISHER, FAILER)]
        card = scorecard(trajectories, {"tasks": self.tasks, "policy": self.golden["policy"]})
        summit = card["agents"][FINISHER]
        drift = card["agents"][FAILER]
        self.assertEqual(summit["rates"]["milestones_complete"]["successes"], 16)
        self.assertEqual(drift["rates"]["milestones_complete"]["successes"], 10)
        self.assertEqual(summit["milestones"]["reached"], summit["milestones"]["total"])
        self.assertLess(drift["milestones"]["reached"], drift["milestones"]["total"])
        self.assertIn("unit_invoice", drift["milestones"]["stalled_at"])
        self.assertEqual(drift["milestones"]["out_of_order_runs"], 1)
        # the long-run dimensions that used to be unreadable
        self.assertEqual(summit["rates"]["loop_free"]["successes"], 16, "no correct long run loops")
        self.assertEqual(summit["rates"]["tool_correct"]["successes"], 16, "a read is a tool call")
        self.assertEqual(summit["rates"]["grounded"]["runs"], 16, "every long answer is checkable")

    def test_the_card_scores_itself_against_the_known_failures(self):
        """`detection` is the catch matrix as the engine computes it: the
        suite's whole argument, in the product rather than in a test."""
        trajectories = [Trajectory.from_json(SUITE / f"{t.id}__{a}.json")
                        for t in self.gen.TASKS for a in (FINISHER, FAILER)]
        det = scorecard(trajectories, {"tasks": self.tasks, "policy": self.golden["policy"]})["detection"]
        self.assertTrue(det["measurable"])
        self.assertEqual((det["caught"], det["total"]), (12, 12))
        self.assertEqual(det["missed"], [])
        self.assertEqual(det["graded_pass"], 7)
        self.assertEqual(det["graded_pass_caught_otherwise"], 7)
        self.assertEqual(det["controls"], {"runs": 20, "flagged": 0, "false_positives": []})
        self.assertIn("12 of 12 known failures caught", det["narrative"])
        self.assertIn("0 of 20 control runs flagged", det["narrative"])
        # which dimensions did the catching. `by_signal` is ordered by how
        # many runs each caught, so the first entry is the busiest.
        self.assertEqual(sorted(det["by_signal"]),
                         ["flags", "grade", "grounded", "kept_looking", "loop", "milestones",
                          "order", "policy", "redundant", "unrecovered"])
        self.assertGreaterEqual(det["by_signal"]["milestones"], det["by_signal"]["grade"],
                                "the milestone line catches at least as many as the grade")

    def test_a_golden_set_without_known_failures_says_so_rather_than_scoring_zero(self):
        trajectories = [Trajectory.from_json(SUITE / f"{t.id}__{a}.json")
                        for t in self.gen.TASKS[:2] for a in (FINISHER, FAILER)]
        bare = {tid: {k: v for k, v in task.items() if k not in ("failure_mode", "failure_mode_agents", "known_correct")}
                for tid, task in self.tasks.items()}
        det = scorecard(trajectories, {"tasks": bare})["detection"]
        self.assertFalse(det["measurable"])
        self.assertIn("nothing to detect and nothing to miss", det["reason"])
        self.assertEqual((det["caught"], det["total"]), (0, 0))

    def test_the_markdown_card_carries_the_milestone_line(self):
        from deepcompare.scorecard import render_scorecard_markdown
        trajectories = [Trajectory.from_json(SUITE / f"{t.id}__{a}.json")
                        for t in self.gen.TASKS for a in (FINISHER, FAILER)]
        text = render_scorecard_markdown(scorecard(trajectories, {"tasks": self.tasks, "policy": self.golden["policy"]}))
        self.assertIn("| milestones |", text)
        self.assertIn("stalled at", text)
        self.assertIn("every milestone reached (golden)", text)
        self.assertIn("## Does the evaluation see it?", text)
        self.assertIn("| `regression` |", text)

    # ------------------------------------------------------------ determinism

    def test_the_generator_reproduces_the_shipped_suite_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, str(GENERATOR), tmp],
                                  cwd=str(ROOT), capture_output=True)
            self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace")[-400:])
            written = sorted(Path(tmp).glob("*.json"))
            self.assertEqual(len(written), 32)
            for path in written:
                shipped = SUITE / path.name
                self.assertTrue(shipped.is_file(), path.name)
                self.assertEqual(path.read_bytes(), shipped.read_bytes(), f"{path.name} is not reproducible")


class JudgeBesideTheCardTest(unittest.TestCase):
    """An LLM judge on the long-horizon suite: scored like every other
    dimension, and kept out of every number the engine computes.

    The verdicts here come from a **stand-in**, not a model — no network
    is touched and nothing below is a finding about how any model judges.
    What is pinned is the wiring and the invariant: whatever the judge
    says, `caught`, `missed`, `by_signal` and `controls` do not move. Every
    other number on this card is reproducible from the traces alone, and
    one sampled verdict folded into them would end that quietly.
    """

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads((ROOT / "demo" / "horizon" / "suite_golden.json").read_text(encoding="utf-8"))
        cls.tasks = {t["id"]: t for t in cls.golden["tasks"]}
        cls.paths = sorted(SUITE.glob("*.json"))
        cls.trajectories = [Trajectory.from_json(p) for p in cls.paths]

    def card(self, verdict=None, **judge):
        """The card over the suite, optionally with a stand-in verdict on
        every run. `verdict` is what the stand-in says: True, False, or a
        callable taking the raw trace."""
        raws = {}
        for path in self.paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if verdict is not None:
                said = verdict(raw) if callable(verdict) else verdict
                raw.setdefault("outcome", {})["judge"] = dict(
                    {"model": "stand-in", "success": said, "score": None, "rationale": "stand-in",
                     "rubric_name": "long-run", "with_steps": True, "steps_shown": 40,
                     "steps_total": len(raw.get("steps") or []), "prior": {"success": raw["outcome"].get("success")},
                     "agrees_with_prior": None, "applied": False}, **judge)
            raws[raw["trace_id"]] = raw
        return scorecard(self.trajectories, {"tasks": self.tasks, "policy": self.golden["policy"]}, raws=raws)

    def test_without_a_judge_the_block_says_so_rather_than_scoring_zero(self):
        judge = self.card()["detection"]["judge"]
        self.assertFalse(judge["measurable"])
        self.assertIn("agentdiff judge", judge["reason"])
        self.assertEqual((judge["judged"], judge["of"]), (0, 12))

    def test_the_judge_cannot_move_a_number_the_engine_computed(self):
        """The one that matters. A judge saying everything is wrong and a
        judge saying everything is right leave the card identical."""
        plain = self.card()["detection"]
        keys = ("caught", "total", "missed", "graded_pass", "graded_pass_caught_otherwise",
                "by_signal", "controls", "narrative")
        for said in (True, False):
            det = self.card(said)
            self.assertEqual({k: det["detection"][k] for k in keys}, {k: plain[k] for k in keys},
                             f"a judge that says {said} to everything moved a computed number")

    def test_a_judge_that_calls_everything_wrong_is_visible_as_such(self):
        """It catches all twelve — and calls all twenty correct runs wrong.
        Without the second number the first one reads like an instrument."""
        judge = self.card(False)["detection"]["judge"]
        self.assertTrue(judge["measurable"])
        self.assertEqual((judge["judged"], judge["of"]), (12, 12))
        self.assertEqual((judge["caught"], judge["missed"]), (12, 0))
        self.assertEqual(judge["controls_called_wrong"], 20)
        self.assertEqual(judge["only_the_judge"], [], "this card already catches all twelve")
        self.assertIn("20 of 20 control runs called wrong", judge["narrative"])

    def test_a_judge_that_passes_everything_catches_nothing_and_says_so(self):
        judge = self.card(True)["detection"]["judge"]
        self.assertEqual((judge["caught"], judge["missed"]), (0, 12))
        self.assertEqual(judge["controls_called_wrong"], 0)

    def test_a_run_the_judge_never_read_is_neither_caught_nor_missed(self):
        """Absent is not a pass. A judge that errored on half the corpus
        must not be reported as having approved it."""
        first = self.tasks and sorted(self.tasks)[0]
        judge = self.card(lambda raw: False if raw["task"]["id"] != first else None)["detection"]["judge"]
        self.assertEqual(judge["judged"], 11)
        self.assertEqual(judge["of"], 12)
        self.assertEqual(judge["caught"], 11)

    def test_a_verdict_over_an_excerpt_is_reported_as_one(self):
        judge = self.card(False)["detection"]["judge"]
        self.assertEqual(judge["on_an_excerpt"], 12, "every run in this suite is longer than the judge's cap")
        self.assertEqual(judge["rubrics"], ["long-run"])
        self.assertIn("about the part it was shown", judge["basis"])

    def test_only_the_judge_names_what_nothing_else_caught(self):
        """Constructed rather than measured: this suite has no uncaught
        mode left, and the field exists for the corpus that does."""
        rows = [{"task": "T1", "agent": "a", "success": True, "milestones": {"stalled_at": None, "complete": True,
                 "in_order": True}, "grounding": {"grounded": True}, "safety": {"policy_compliant": True,
                 "risk_flags": []}, "trajectory": {"loop_free": True, "redundant_stretch": 0,
                 "stopped_when_done": True}, "recovery": {"errors": 0, "recovered": 0},
                 "judge": {"success": False, "model": "stand-in", "rubric_name": "strict",
                           "steps_shown": 5, "steps_total": 5}}]
        det = detection(rows, {"tasks": {"T1": {"failure_mode": "quiet", "failure_mode_agents": ["a"]}}})
        self.assertEqual((det["caught"], det["total"]), (0, 1), "nothing deterministic sees it")
        self.assertEqual(det["judge"]["only_the_judge"], [{"mode": "quiet", "task": "T1", "agent": "a"}])
        self.assertEqual(det["judge"]["also_caught_deterministically"], 0)


class WhatTheJudgeCanSeeTest(unittest.TestCase):
    """Whether the judge is shown the step where the run goes wrong.

    No model is involved and none is needed: the suite marks the step it
    injected each failure at, and the excerpt rule is arithmetic. So the
    question *can a judge reading an excerpt of this run possibly see the
    failure* has a deterministic answer, and it is the one worth knowing
    before paying for a verdict.
    """

    @classmethod
    def setUpClass(cls):
        golden = {t["id"]: t for t in json.loads(
            (ROOT / "demo" / "horizon" / "suite_golden.json").read_text(encoding="utf-8"))["tasks"]}
        cls.runs = []
        for path in sorted(SUITE.glob(f"*__{FAILER}.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            mode = golden.get(raw["task"]["id"], {}).get("failure_mode")
            if not mode:
                continue
            steps = raw["steps"][:-1]          # the answer step is shown separately
            cls.runs.append((mode, len(steps),
                             [i for i, st in enumerate(steps) if "SYNTHETIC" in str(st.get("note") or "")]))

    def covered(self, cap, head_only=False):
        """How many of the twelve have their injected failure inside the
        excerpt. `head_only` is the rule this repository used to apply."""
        seen = 0
        for _mode, total, marks in self.runs:
            if total <= cap:
                visible = set(range(total))
            elif head_only:
                visible = set(range(cap))
            else:
                head, tail = cap // 2, cap - cap // 2
                visible = set(range(head)) | set(range(total - tail, total))
            seen += any(m in visible for m in marks)
        return seen

    def test_the_old_head_only_cut_showed_the_judge_one_failure_in_twelve(self):
        """Forty steps off the front of a 237-step run is the opening, and
        eleven of these twelve failures happen after it."""
        self.assertEqual(len(self.runs), 12)
        self.assertEqual(self.covered(STEP_EXCERPT, head_only=True), 1)

    def test_naming_the_gap_and_keeping_both_ends_shows_four(self):
        """Better, and still a minority: the fix makes the excerpt honest,
        it does not make it sufficient."""
        self.assertEqual(self.covered(STEP_EXCERPT), 4)

    def test_no_excerpt_short_of_the_whole_run_shows_every_failure(self):
        """274 of a mean 237 steps — there is no excerpt that works at this
        length. A judge either reads the run or is guessing about the part
        it was not shown, and this is the number that says so."""
        smallest = next(c for c in range(2, 400, 2) if self.covered(c) == len(self.runs))
        self.assertEqual(smallest, 274)
        self.assertGreater(smallest, sum(r[1] for r in self.runs) / len(self.runs))


if __name__ == "__main__":
    unittest.main()
