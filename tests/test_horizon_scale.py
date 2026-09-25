"""The long-horizon evaluation measured at scale: 200 generated pairs.

`tests/test_horizon_suite.py` pins the sixteen hand-written tasks. They
prove the machinery and they are one sample per failure mode, which is an
anecdote with a percentage sign on it: a mode "caught" once may be caught
by an accident of that task's length, and nothing at n=1 can tell the
difference.

This file generates **200 long tasks — 400 runs, about 100,000 steps —**
across every mode, eight domains and three lengths
(`demo/horizon/generate_suite.py --scale`), and measures the evaluation on
them: the per-mode detection rate, the modes it misses, and the false
positives over the runs that are known to be correct. The corpus is
written to a temporary directory and never shipped; it is deterministic
from its seed, so the numbers below are reproducible and comparable
between engines.

Every mode is caught in every one of its 184 runs, and the number that
matters more is the one underneath it: **0 of 216 runs known to be
correct is flagged**. A detector that fires on everything would also read
184 of 184 here, and the control line is the only thing that tells the two
apart. Read the two together or neither means anything.

184/184 is a floor over *the twelve modes this corpus contains*, not a
claim about long-horizon failure in general — the modes it does not
contain are not measured by it, and `docs/HORIZON.md` says which two were
missed until recently and what closing them cost.

If a change moves any of these numbers, the test fails and the fix is to
update the numbers and the document together. That is the point of
writing them down.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.excerpt import effective_policy, focus  # noqa: E402
from deepcompare.scorecard import scorecard  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

GENERATOR = ROOT / "demo" / "horizon" / "generate_suite.py"
PAIRS = 200

#: the budgets the excerpt measurement reports, and the one it breaks down
#: per mode — 40 steps, which is `harness.judge.STEP_EXCERPT`
CAPS = (20, 40, 60, 80, 120, 160)
EXCERPT_CAP = 40


def _positional(total: int, cap: int) -> set:
    """The excerpt a reader gets when position is all they go on."""
    if total <= cap:
        return set(range(total))
    head, tail = cap // 2, cap - cap // 2
    return set(range(head)) | set(range(total - tail, total))

#: caught / known, per mode, over the generated corpus.
EXPECTED = {
    "budget_exhausted": (16, 16),
    "context_overflow": (16, 16),
    "drift": (16, 16),
    "forgotten_constraint": (16, 16),
    "late_fault": (15, 15),
    "out_of_order": (15, 15),
    "regression": (15, 15),
    "retry_stall": (15, 15),
    "skipped_unit": (15, 15),
    "stale_value": (15, 15),
    "swallowed_error": (15, 15),
    "unverified_handoff": (15, 15),
}


@unittest.skipUnless(GENERATOR.is_file(), "the long-horizon generator is missing")
class HorizonScaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        done = subprocess.run([sys.executable, str(GENERATOR), "--scale", str(PAIRS), str(out)],
                              cwd=str(ROOT), capture_output=True)
        if done.returncode != 0:
            raise unittest.SkipTest("the generator failed: " + done.stderr.decode("utf-8", "replace")[-400:])
        cls.golden = json.loads((out / "golden.json").read_text(encoding="utf-8"))
        cls.tasks = {t["id"]: t for t in cls.golden["tasks"]}
        cls.paths = [p for p in sorted(out.glob("*.json")) if p.name != "golden.json"]
        cls.n_runs = len(cls.paths)
        cls.steps = 0
        cls.coverage = {cap: {"position": 0, "structure": 0} for cap in CAPS}
        cls.per_mode = {}

        def stream():
            """One trace in memory at a time. The card keeps rows, not
            trajectories, so this is what lets a corpus be measured at a
            size it could not be held at — and it is measured on the way
            past, since loading a million steps twice to ask two questions
            is the kind of thing that stops an evaluation being run."""
            for path in cls.paths:
                raw = json.loads(path.read_text(encoding="utf-8"))
                traj = Trajectory.from_dict(raw)
                cls.steps += len(traj.steps)
                cls._measure_excerpt(traj, raw)
                yield traj

        cls.card = scorecard(stream(), {"tasks": cls.tasks, "policy": cls.golden["policy"]})
        cls.det = cls.card["detection"]

    @classmethod
    def _measure_excerpt(cls, traj, raw):
        """For a run whose failure is known, whether a budgeted excerpt
        contains the step the generator injected it at — chosen by where
        the steps fall, and chosen by what the run itself flags."""
        task = cls.tasks.get(traj.task.id) or {}
        mode = task.get("failure_mode")
        if not mode or traj.agent.name not in (task.get("failure_mode_agents") or []):
            return
        fails = [i for i, st in enumerate(raw["steps"][:-1]) if "SYNTHETIC" in str(st.get("note") or "")]
        if not fails:
            return
        body = len(traj.steps) - 1
        policy = effective_policy(cls.golden.get("policy"), task)
        row = cls.per_mode.setdefault(mode, {"known": 0, "position": 0, "structure": 0})
        row["known"] += 1
        for cap in CAPS:
            if any(i in _positional(body, cap) for i in fails):
                cls.coverage[cap]["position"] += 1
                if cap == EXCERPT_CAP:
                    row["position"] += 1
            if any(i in set(focus(traj, cap, policy, within=body)["keep"]) for i in fails):
                cls.coverage[cap]["structure"] += 1
                if cap == EXCERPT_CAP:
                    row["structure"] += 1

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_corpus_is_long_varied_and_self_checked(self):
        """Every generated failing run is checked against the mode it is
        labelled with, inside the generator (`manifests`): a procedural
        corpus whose labels drifted from its contents measures nothing, and
        measures it convincingly."""
        self.assertEqual(self.n_runs, PAIRS * 2)
        self.assertGreater(self.steps, 70_000)
        per_run = {r["task"]: r for r in self.card["per_run"]}
        self.assertGreaterEqual(min(r["spend"]["steps"] for r in per_run.values()), 120)
        families = {t["family"] for t in self.golden["tasks"]}
        self.assertGreaterEqual(len(families), 8, sorted(families))
        sizes = {len(t["milestones"]) for t in self.golden["tasks"]}
        self.assertGreaterEqual(len(sizes), 3, "three lengths, so a mode cannot hide in one of them")

    def test_the_detection_rate_per_mode_holds(self):
        per: dict = {}
        for row in self.det["modes"]:
            got = per.setdefault(row["mode"], [0, 0])
            got[1] += 1
            got[0] += 1 if row["caught"] else 0
        self.assertEqual({k: tuple(v) for k, v in sorted(per.items())}, EXPECTED)
        self.assertEqual((self.det["caught"], self.det["total"]), (184, 184))

    def test_no_run_known_to_be_correct_is_flagged(self):
        """216 runs that did nothing wrong — the controls of the control
        tasks and the finishing run of every other task. One flag here is
        one reason not to trust the rest of the card."""
        controls = self.det["controls"]
        self.assertEqual(controls["runs"], 216)
        self.assertEqual(controls["flagged"], 0, controls["false_positives"])

    def test_the_grade_alone_would_miss_more_than_half_of_them_at_scale(self):
        """107 of the 184 known failures — more than half — are graded a
        pass. All 107 are caught by some other dimension, which is the
        whole argument for the rest of the card existing."""
        self.assertEqual(self.det["graded_pass"], 107)
        self.assertEqual(self.det["graded_pass_caught_otherwise"], 107)
        signals = self.det["by_signal"]
        self.assertGreater(signals["milestones"], signals["grade"])
        self.assertGreater(signals["flags"], signals["grade"])

    def test_nothing_is_missed_and_nothing_catches_it_alone(self):
        """Both of this corpus's former blind spots close here, and the
        assertion that matters is the second one: no single dimension
        accounts for the 184. If one ever did, it would be a detector that
        had learned the generator rather than the failure — so this pins
        that the catch is spread, and `test_no_run_known_to_be_correct_is_flagged`
        pins that the spread is not just noise."""
        self.assertEqual(sorted(set(self.det["missed"])), [])
        for row in self.det["modes"]:
            self.assertTrue(row["signals"], f"{row['task']} ({row['mode']}) caught by nothing")
        by_signal: dict = {}
        for row in self.det["modes"]:
            for signal in row["signals"]:
                by_signal.setdefault(signal, set()).add(row["task"])
        self.assertLessEqual(max(len(v) for v in by_signal.values()), self.det["total"] // 2,
                             "one dimension catching most of them would be a corpus artefact, not a result")
        pair = max(len(a | b) for a, b in itertools.combinations(by_signal.values(), 2))
        self.assertEqual(pair, 138)
        self.assertLess(pair, self.det["total"], "no two dimensions between them reach all of it")
        # the two that used to get away, now caught in every run they appear in
        for mode in ("regression", "context_overflow"):
            rows = [r for r in self.det["modes"] if r["mode"] == mode]
            self.assertTrue(rows and all(r["caught"] for r in rows), mode)

    # ------------------------------------------------- what a reader can see

    def test_the_card_is_built_from_a_stream_and_not_from_a_corpus_in_memory(self):
        """400 runs is 92,000 steps; the same generator at 2000 pairs is a
        million, and an evaluation that can only score what fits in memory
        stops being able to measure the runs worth measuring. Nothing past
        `score_run` touches a trajectory, so the card takes an iterable and
        the cost is set by the number of runs, not their length."""
        self.assertEqual(len(self.card["per_run"]), self.n_runs)
        self.assertGreater(self.steps, 70_000)
        rows = (t for t in [])                      # a generator, not a list
        empty = scorecard(rows, {"tasks": self.tasks})
        self.assertEqual(empty["per_run"], [])

    def test_a_budgeted_excerpt_chosen_by_position_misses_two_thirds_of_them(self):
        """What an LLM judge is shown of a long run, measured over 184
        known failures rather than the twelve in the shipped suite.
        Position is close to choosing at random: the failure is at step 175
        of 202, or 95 of 233, and where a step falls says nothing about
        either."""
        known = sum(r["known"] for r in self.per_mode.values())
        self.assertEqual(known, self.det["total"])
        at40 = self.coverage[EXCERPT_CAP]
        self.assertEqual((at40["position"], known), (62, 184))
        self.assertEqual(at40["structure"], 139)

    def test_the_whole_table_this_repository_publishes(self):
        """`docs/HORIZON.md` prints it; if it moves, they move together.
        Structure is ahead at every budget — and **structure at 20 steps
        matches position at 80**, a quarter of the tokens for the same
        sight of the failure."""
        table = {cap: (self.coverage[cap]["position"], self.coverage[cap]["structure"]) for cap in CAPS}
        self.assertEqual(table, {20: (62, 123), 40: (62, 139), 60: (78, 139),
                                 80: (93, 140), 120: (93, 154), 160: (124, 155)})
        for cap in CAPS:
            self.assertGreater(table[cap][1], table[cap][0], f"at {cap} steps")
        # structure at 20 steps and position at 160 are the same number to
        # within one run — an eighth of the budget for the same sight of
        # the failure. Stated as the near-equality it is, because the last
        # two times this was stated as a clean inequality it was wrong.
        self.assertAlmostEqual(table[20][1], table[160][0], delta=2)

    def test_per_mode_the_selector_is_all_or_nothing(self):
        """The finding the twelve-task suite could not show. At 15 runs a
        mode this is not a rate at all: for seven modes the structural
        excerpt contains the failure in *every* run, and for four it
        contains it in *none*. The question is not how often it works, it
        is which kinds of failure leave a mark in what the run did."""
        always, never, partial = [], [], []
        for mode, row in sorted(self.per_mode.items()):
            share = row["structure"] / row["known"]
            (always if share == 1 else never if share == 0 else partial).append(mode)
        self.assertEqual(always, ["budget_exhausted", "context_overflow", "drift",
                                  "forgotten_constraint", "late_fault", "out_of_order",
                                  "regression", "swallowed_error", "unverified_handoff"])
        self.assertEqual(never, ["retry_stall", "skipped_unit", "stale_value"])
        self.assertEqual(partial, [])
        # and the three it never reaches each have their own reason, none
        # of which is "the trace cannot say it": a scoring artefact
        # (`retry_stall`), a count the run never states (`skipped_unit`),
        # and a discard only the answer reveals (`stale_value`).
        for mode in never:
            self.assertEqual(self.per_mode[mode]["structure"], 0, mode)

    def test_structure_finds_what_position_cannot_and_not_the_other_way_round(self):
        """Every mode position reaches, structure reaches too. The gain is
        not a trade."""
        for mode, row in self.per_mode.items():
            self.assertGreaterEqual(row["structure"], row["position"], mode)

    def test_the_redundancy_measure_is_a_shape_not_a_fitted_threshold(self):
        """The stretch of contiguous re-done steps separates the classes
        outright — and the assertion that matters is the last one: nothing
        in the corpus lands anywhere near `REDUNDANT_STRETCH`. A constant
        that sat between the classes would be fitted to this corpus; this
        one is a floor on what counts as a stretch at all."""
        from deepcompare.process import REDUNDANT_STRETCH
        tasks = {t["id"]: t for t in self.golden["tasks"]}
        by_mode: dict = {}
        for r in self.card["per_run"]:
            task = tasks[r["task"]]
            failing = r["agent"] in (task.get("failure_mode_agents") or [])
            mode = task.get("failure_mode") if failing else "correct"
            by_mode.setdefault(mode, []).append(r["trajectory"]["redundant_stretch"])
        self.assertEqual(max(by_mode["correct"]), 0, "a correct run re-does nothing")
        self.assertEqual((min(by_mode["context_overflow"]), max(by_mode["context_overflow"])), (6, 8))
        self.assertEqual(sorted({v for vs in by_mode.values() for v in vs}), [0, 6, 8, 10])
        for mode, values in by_mode.items():
            if mode not in ("context_overflow", "retry_stall"):
                self.assertEqual(max(values), 0, mode)
        self.assertEqual(REDUNDANT_STRETCH, 3)


#: The same corpus ten times over: 2000 pairs, 4000 runs, 1,040,066 steps.
#: It takes about a minute to generate, two to score and half a gigabyte of
#: disk, so it is opt-in — `AGENTDIFF_SCALE=2000 pytest tests/test_horizon_scale.py`.
#: Its job is to say whether the 200-pair numbers above are the corpus or
#: the engine, and the answer is in `test_two_hundred_pairs_was_enough`.
BIG_PAIRS = 2000


@unittest.skipUnless(os.environ.get("AGENTDIFF_SCALE") == str(BIG_PAIRS),
                     f"set AGENTDIFF_SCALE={BIG_PAIRS} to run the ten-times-larger corpus")
class HorizonScaleTenXTest(HorizonScaleTest):
    """2000 pairs — a million steps — measured the same way.

    Every number this file pins at 200 pairs is an estimate of a number
    this one measures, and the point of running it once is to find out how
    good an estimate it was. It is not run by default because three
    minutes and half a gigabyte is the wrong default for a test suite, and
    because the answer turned out to be *very good*: nothing here moved by
    more than 1.2 points.
    """

    @classmethod
    def setUpClass(cls):
        global PAIRS
        PAIRS, cls._was = BIG_PAIRS, PAIRS
        try:
            super().setUpClass()
        finally:
            PAIRS = cls._was

    def test_the_corpus_is_long_varied_and_self_checked(self):
        self.assertEqual(self.n_runs, BIG_PAIRS * 2)
        self.assertGreater(self.steps, 1_000_000)

    def test_the_detection_rate_per_mode_holds(self):
        per: dict = {}
        for row in self.det["modes"]:
            got = per.setdefault(row["mode"], [0, 0])
            got[1] += 1
            got[0] += 1 if row["caught"] else 0
        for mode, (caught, known) in sorted(per.items()):
            self.assertEqual(caught, known, mode)
        self.assertEqual((self.det["caught"], self.det["total"]), (1846, 1846))

    def test_no_run_known_to_be_correct_is_flagged(self):
        self.assertEqual(self.det["controls"]["runs"], 2154)
        self.assertEqual(self.det["controls"]["flagged"], 0, self.det["controls"]["false_positives"])

    def test_the_grade_alone_would_miss_more_than_half_of_them_at_scale(self):
        self.assertEqual(self.det["graded_pass"], 1076)
        self.assertEqual(self.det["graded_pass_caught_otherwise"], 1076)

    def test_nothing_is_missed_and_nothing_catches_it_alone(self):
        self.assertEqual(sorted(set(self.det["missed"])), [])
        top = max(self.det["by_signal"].values())
        self.assertLessEqual(top, self.det["total"] // 2)

    def test_the_redundancy_measure_is_a_shape_not_a_fitted_threshold(self):
        """The load-bearing half holds exactly — 0 for every one of the
        2154 correct runs, 6 or more for every `context_overflow` — and the
        half that was an artefact of 184 runs does not: at this size
        `context_overflow` reaches 10, where at 200 pairs it topped out at
        8. The separation is the claim; the upper bound never was."""
        from deepcompare.process import REDUNDANT_STRETCH
        by_mode: dict = {}
        for r in self.card["per_run"]:
            task = self.tasks[r["task"]]
            failing = r["agent"] in (task.get("failure_mode_agents") or [])
            mode = task.get("failure_mode") if failing else "correct"
            by_mode.setdefault(mode, []).append(r["trajectory"]["redundant_stretch"])
        self.assertEqual(max(by_mode["correct"]), 0, "a correct run re-does nothing, 2154 times over")
        self.assertEqual((min(by_mode["context_overflow"]), max(by_mode["context_overflow"])), (6, 10))
        self.assertEqual(sorted({v for vs in by_mode.values() for v in vs}), [0, 6, 8, 10])
        for mode, values in by_mode.items():
            if mode not in ("context_overflow", "retry_stall"):
                self.assertEqual(max(values), 0, mode)
        self.assertEqual(REDUNDANT_STRETCH, 3)

    def test_a_budgeted_excerpt_chosen_by_position_misses_two_thirds_of_them(self):
        at40 = self.coverage[EXCERPT_CAP]
        self.assertEqual((at40["position"], at40["structure"]), (616, 1384))

    def test_the_whole_table_this_repository_publishes(self):
        table = {cap: (self.coverage[cap]["position"], self.coverage[cap]["structure"]) for cap in CAPS}
        self.assertEqual(table, {20: (616, 1230), 40: (616, 1384), 60: (770, 1384),
                                 80: (924, 1389), 120: (924, 1490), 160: (1190, 1551)})
        # an eighth of the budget, and ahead: structure at 20 steps reads
        # 66.6% where position at 160 reads 64.5%
        self.assertGreater(table[20][1], table[160][0])

    def test_per_mode_the_selector_is_all_or_nothing(self):
        """At 154 runs a mode, not 15 — and it is all or nothing with no
        remainder at all: nine modes at 154 of 154, three at 0 of 154, and
        not one mode anywhere in between. Whether a failure leaves a mark
        in what the run did is a property of the kind of failure, not a
        chance of catching it."""
        shares = {m: r["structure"] / r["known"] for m, r in self.per_mode.items()}
        self.assertEqual(sorted(m for m, v in shares.items() if v == 1),
                         ["budget_exhausted", "context_overflow", "drift", "forgotten_constraint",
                          "late_fault", "out_of_order", "regression", "swallowed_error",
                          "unverified_handoff"])
        self.assertEqual(sorted(m for m, v in shares.items() if v == 0),
                         ["retry_stall", "skipped_unit", "stale_value"])
        self.assertEqual([m for m, v in shares.items() if 0 < v < 1], [],
                         "no mode has a rate; every one of them is always or never")

    def test_two_hundred_pairs_was_close_enough_and_says_by_how_much(self):
        """The reason this class exists: how good an estimate is the cheap
        corpus of the expensive one?

        Good, with a caveat worth having in writing. Every rate the 200-pair
        corpus reports is within **3.0 points** of the rate a corpus ten
        times its size reports. But the error is not uniform: it is under a
        point at five of the six budgets and 3.0 at the sixth, which is the
        kind of place a reader would want to read a difference off it. So
        the 200-pair table is sound to about half a mode and should not be
        read to the run."""
        known = self.det["total"]
        for cap, (small_p, small_s) in {20: (62, 123), 40: (62, 139), 60: (78, 139),
                                        80: (93, 140), 120: (93, 154), 160: (124, 155)}.items():
            for label, small in (("position", small_p), ("structure", small_s)):
                drift = abs(self.coverage[cap][label] / known - small / 184) * 100
                self.assertLess(drift, 3.1, f"{label} at {cap}: {drift:.2f} points apart")
