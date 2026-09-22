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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.scorecard import scorecard  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

GENERATOR = ROOT / "demo" / "horizon" / "generate_suite.py"
PAIRS = 200

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
        trajectories = [Trajectory.from_json(p) for p in sorted(out.glob("*.json")) if p.name != "golden.json"]
        cls.n_runs = len(trajectories)
        cls.steps = sum(len(t.steps) for t in trajectories)
        cls.card = scorecard(trajectories, {"tasks": {t["id"]: t for t in cls.golden["tasks"]},
                                            "policy": cls.golden["policy"]})
        cls.det = cls.card["detection"]

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
