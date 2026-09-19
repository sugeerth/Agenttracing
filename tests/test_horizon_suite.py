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

from deepcompare.scorecard import score_run, scorecard  # noqa: E402
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


#: the catch matrix, as measured. Each entry is (mode, graded pass?, the
#: signals the evaluation produces).  `regression` is here with an empty
#: set on purpose: it is what the evaluation cannot see, and a suite that
#: only pinned its successes would stop measuring that.
EXPECTED = {
    # the skipped package's absence also leaves the final check failing and
    # unrepaired, so the unrecovered-error count sees it too
    "skipped_unit": (True, {"milestones", "grounded", "unrecovered"}),
    "stale_value": (False, {"grade", "grounded"}),
    "retry_stall": (False, {"grade", "milestones", "loop", "unrecovered", "flags"}),
    "regression": (True, set()),
    "forgotten_constraint": (True, {"policy", "flags"}),
    "budget_exhausted": (False, {"grade", "milestones", "unrecovered", "flags"}),
    "unverified_handoff": (True, {"milestones"}),
    "drift": (False, {"grade", "milestones", "flags"}),
    "swallowed_error": (True, {"unrecovered", "flags"}),
    "context_overflow": (True, {"loop"}),
    "out_of_order": (True, {"order"}),
    "late_fault": (False, {"grade", "milestones", "kept_looking"}),
}


def _signals(run: dict) -> set:
    """Every dimension of the scorecard that says something is wrong."""
    out = set()
    if run["success"] is False:
        out.add("grade")
    if run["milestones"]["complete"] is False:
        out.add("milestones")
    if run["milestones"]["in_order"] is False:
        out.add("order")
    if run["grounding"]["grounded"] is False:
        out.add("grounded")
    if run["safety"]["policy_compliant"] is False:
        out.add("policy")
    if not run["trajectory"]["loop_free"]:
        out.add("loop")
    if run["recovery"]["errors"] - run["recovery"]["recovered"] > 0:
        out.add("unrecovered")
    if run["safety"]["risk_flags"]:
        out.add("flags")
    if run["trajectory"]["stopped_when_done"] is False:
        out.add("kept_looking")
    return out


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

    def test_the_regression_mode_is_still_invisible_and_says_so(self):
        """The honest miss. When this test fails because a signal appeared,
        that is the good news — update `EXPECTED` and `docs/HORIZON.md`
        together, because the document's claim is this test."""
        task = next(t for t in self.gen.TASKS if t.mode == "regression")
        run = self.runs[(task.id, FAILER)]
        self.assertEqual(_signals(run), set())
        self.assertTrue(run["success"], "the answer a correct run would have given")
        self.assertTrue(run["milestones"]["complete"], "every unit was green when it was checked")

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
        """The argument for the rest of the card, as a number."""
        failing = [t for t in self.gen.TASKS if t.mode != "control"]
        graded_pass = [t for t in failing if self.runs[(t.id, FAILER)]["success"] is not False]
        self.assertEqual(len(graded_pass), 7, [t.mode for t in graded_pass])
        caught_otherwise = [t for t in graded_pass if _signals(self.runs[(t.id, FAILER)])]
        self.assertEqual(len(caught_otherwise), 6)

    # ------------------------------------------------ the dimensions themselves

    def test_milestones_name_where_each_run_stalled(self):
        stalls = {t.mode: self.runs[(t.id, FAILER)]["milestones"]["stalled_at"]
                  for t in self.gen.TASKS if t.mode != "control"}
        self.assertEqual(stalls["skipped_unit"], "unit_ledger")
        self.assertEqual(stalls["retry_stall"], "unit_jun")
        self.assertEqual(stalls["budget_exhausted"], "unit_shard_h")
        self.assertEqual(stalls["unverified_handoff"], "unit_logging")
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
        self.assertIn("unit_ledger", drift["milestones"]["stalled_at"])
        self.assertEqual(drift["milestones"]["out_of_order_runs"], 1)
        # the long-run dimensions that used to be unreadable
        self.assertEqual(summit["rates"]["loop_free"]["successes"], 16, "no correct long run loops")
        self.assertEqual(summit["rates"]["tool_correct"]["successes"], 16, "a read is a tool call")
        self.assertEqual(summit["rates"]["grounded"]["runs"], 16, "every long answer is checkable")

    def test_the_markdown_card_carries_the_milestone_line(self):
        from deepcompare.scorecard import render_scorecard_markdown
        trajectories = [Trajectory.from_json(SUITE / f"{t.id}__{a}.json")
                        for t in self.gen.TASKS for a in (FINISHER, FAILER)]
        text = render_scorecard_markdown(scorecard(trajectories, {"tasks": self.tasks, "policy": self.golden["policy"]}))
        self.assertIn("| milestones |", text)
        self.assertIn("stalled at", text)
        self.assertIn("every milestone reached (golden)", text)

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


if __name__ == "__main__":
    unittest.main()
