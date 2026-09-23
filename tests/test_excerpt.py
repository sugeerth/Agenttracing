"""Choosing which part of a long run to show a reader with a budget.

`deepcompare/excerpt.py` picks the steps by what the run itself flags
instead of by where they fall. The tests that matter here are the two the
module could be wrong about in a way nothing else would notice: that it
never reads the golden set, and that the improvement it claims is real
and measured rather than asserted.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare.excerpt import (CONTEXT, ENDS, WEIGHTS, effective_policy,  # noqa: E402
                                 focus, notable_steps)
from deepcompare.trace import Trajectory  # noqa: E402

SUITE = ROOT / "demo" / "horizon" / "suite"
GOLDEN = ROOT / "demo" / "horizon" / "suite_golden.json"


def positional(total: int, cap: int) -> set:
    """The excerpt a reader gets when position is all they go on."""
    if total <= cap:
        return set(range(total))
    head, tail = cap // 2, cap - cap // 2
    return set(range(head)) | set(range(total - tail, total))


class PolicyBoundaryTest(unittest.TestCase):
    """The line the selector must not cross.

    A task's forbidden patterns are a constraint the agent was told about
    before it started — an input. Its milestones and expected answer are
    facts about how the run turned out. A selector that read the second
    would be shown the answer and then credited with finding it.
    """

    def test_a_task_s_stated_constraints_are_inputs_and_are_used(self):
        merged = effective_policy({"forbidden_tools": ["shell"]},
                                  {"forbidden_patterns": ["legacy/"], "milestones": [{"id": "x"}]})
        self.assertEqual(merged, {"forbidden_tools": ["shell"], "forbidden_patterns": ["legacy/"]})

    def test_nothing_about_the_outcome_survives_the_merge(self):
        merged = effective_policy(None, {"milestones": [{"id": "x"}], "expected": "42",
                                         "failure_mode": "skipped_unit", "failure_mode_agents": ["a"],
                                         "expected_evidence": ["e"], "forbidden_patterns": ["legacy/"]})
        self.assertEqual(merged, {"forbidden_patterns": ["legacy/"]})

    def test_no_policy_and_no_task_is_none_not_an_empty_rulebook(self):
        self.assertIsNone(effective_policy(None, None))
        self.assertIsNone(effective_policy({}, {}))


@unittest.skipUnless(SUITE.is_dir() and GOLDEN.is_file(), "the long-horizon suite is not generated")
class WhatItFindsTest(unittest.TestCase):
    """Measured on the long-horizon suite, which marks the step each
    failure was injected at — so "does the excerpt contain the failure"
    is arithmetic, and the comparison against choosing by position is a
    measurement rather than a claim."""

    @classmethod
    def setUpClass(cls):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        tasks = {t["id"]: t for t in golden["tasks"]}
        cls.runs = []
        for path in sorted(SUITE.glob("*__drift-lh.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            task = tasks.get(raw["task"]["id"]) or {}
            if not task.get("failure_mode"):
                continue
            cls.runs.append({
                "mode": task["failure_mode"],
                "traj": Trajectory.from_json(path),
                "policy": effective_policy(golden.get("policy"), task),
                "fails": [i for i, s in enumerate(raw["steps"][:-1])
                          if "SYNTHETIC" in str(s.get("note") or "")],
            })

    def coverage(self, cap, by_structure):
        hit = []
        for run in self.runs:
            body = len(run["traj"].steps) - 1          # the answer is shown separately
            keep = (set(focus(run["traj"], cap, run["policy"], within=body)["keep"])
                    if by_structure else positional(body, cap))
            if any(i in keep for i in run["fails"]):
                hit.append(run["mode"])

        return sorted(hit)

    def test_structure_beats_position_at_every_budget(self):
        self.assertEqual(len(self.runs), 12)
        for cap in (20, 40, 60, 80, 120, 160):
            by_position = len(self.coverage(cap, False))
            by_structure = len(self.coverage(cap, True))
            self.assertGreater(by_structure, by_position, f"at {cap} steps")

    def test_the_numbers_this_repository_publishes(self):
        """`docs/HORIZON.md` prints this table; if it moves, they move
        together. The right-hand column at 40 equals the left-hand one at
        160 — the same coverage for a quarter of the tokens."""
        measured = {cap: (len(self.coverage(cap, False)), len(self.coverage(cap, True)))
                    for cap in (20, 40, 60, 80, 120, 160)}
        self.assertEqual(measured, {20: (4, 6), 40: (4, 7), 60: (5, 7),
                                    80: (6, 7), 120: (6, 8), 160: (7, 10)})

    def test_what_it_cannot_reach_and_why(self):
        """The five it misses are the point. Four of them are failures of
        *absence* — a unit never worked, a check never run, a value quietly
        superseded — and nothing in what the run *did* can point at what it
        did not do. Those need the milestones, and the milestones are
        exactly what a judge must not be shown."""
        missed = sorted({r["mode"] for r in self.runs} - set(self.coverage(40, True)))
        self.assertEqual(missed, ["out_of_order", "retry_stall", "skipped_unit",
                                  "stale_value", "unverified_handoff"])

    def test_the_stall_is_shown_even_where_the_strict_measure_says_missed(self):
        """`retry_stall` is in that list on a technicality worth stating:
        the marked step is the one *after* the stall ends, and both edges
        of the stall itself are in the excerpt. The strict measure is kept
        strict rather than adjusted to flatter it."""
        run = next(r for r in self.runs if r["mode"] == "retry_stall")
        keep = set(focus(run["traj"], 40, run["policy"], within=len(run["traj"].steps) - 1)["keep"])
        stall = [p for p in keep if 190 <= p <= 220]
        self.assertGreaterEqual(len(stall), 4, "the stall region is shown")
        self.assertLess(min(stall), 200, "where it started")
        self.assertGreater(max(stall), 210, "and how it ended")


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class HowItChoosesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traj = Trajectory.from_json(SUITE / "L03_data_backfill__drift-lh.json")

    def test_a_run_of_failing_retries_is_one_place_to_look_not_ten(self):
        """Without clustering a single stall eats the whole budget and the
        rest of the run goes unseen — which is how the first version of
        this lost four of the modes it was built to find."""
        marks = notable_steps(self.traj)
        stall = [m for m in marks if 194 <= m["index"] <= 214 and m["kind"] == "unrecovered_error"]
        self.assertGreaterEqual(len(stall), 6, "the stall is many marks")
        chosen = focus(self.traj, 40, within=len(self.traj.steps) - 1)
        spent_on_stall = [p for p in chosen["keep"] if 190 <= p <= 218]
        self.assertLess(len(spent_on_stall), 12, "one event does not get the whole budget")
        self.assertGreater(len(chosen["kinds"]), 1, "more than one kind of place is looked at")

    def test_under_the_cap_the_whole_run_is_kept_and_it_says_so(self):
        chosen = focus(self.traj, len(self.traj.steps) + 10)
        self.assertEqual(chosen["keep"], list(range(len(self.traj.steps))))
        self.assertEqual(chosen["basis"], "every step of the run")
        self.assertEqual(chosen["marks"], [])

    def test_the_ends_are_always_kept(self):
        """The setup and the conclusion are not optional: a reader who
        cannot see the task being taken up cannot judge what came of it."""
        body = len(self.traj.steps) - 1
        keep = set(focus(self.traj, 40, within=body)["keep"])
        ends = max(1, 40 // ENDS)
        self.assertTrue(set(range(ends)) <= keep)
        self.assertTrue(set(range(body - ends, body)) <= keep)

    def test_the_budget_is_spent_and_not_overspent(self):
        for cap in (12, 40, 91):
            chosen = focus(self.traj, cap, within=len(self.traj.steps) - 1)
            self.assertEqual(len(chosen["keep"]), cap, cap)
            self.assertEqual(chosen["chosen"], cap)

    def test_the_weights_are_an_ordering_anyone_can_argue_with(self):
        """They are stated, not fitted: an error the run never came back to
        is a better place to look than a step that merely repeats. If these
        were tuned on a corpus the corpus would be what they were right
        about."""
        self.assertGreater(WEIGHTS["unrecovered_error"], WEIGHTS["recovered_error"])
        self.assertGreater(WEIGHTS["recovered_error"], WEIGHTS["cycle"])
        self.assertGreater(WEIGHTS["cycle"], WEIGHTS["no_information"])
        self.assertEqual(CONTEXT, 1)

    def test_every_mark_says_why_in_words_a_reader_can_check(self):
        for mark in notable_steps(self.traj)[:20]:
            self.assertIn(mark["kind"], WEIGHTS)
            self.assertTrue(mark["why"], mark)
            self.assertEqual(mark["weight"], WEIGHTS[mark["kind"]])


if __name__ == "__main__":
    unittest.main()
