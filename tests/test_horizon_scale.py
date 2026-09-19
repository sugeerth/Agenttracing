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

Two of the numbers pinned here are *failures of the evaluation*, and they
are pinned as such:

- `regression` — a late fix that breaks an early unit — is caught 0 of 15
  times. It is the blind spot `docs/HORIZON.md` names.
- `context_overflow` is caught 6 of 16 times, because it lands at 8–12%
  of a run's tool steps while the loop rule needs 10%: it is detected in
  the shorter tasks and missed in the longer ones. A threshold fitted to
  this corpus would "fix" it, which is why there isn't one.

If a change makes these better, the test fails and the fix is to update
the numbers and the document together. That is the point of writing them
down.
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

from deepcompare.scorecard import scorecard  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

GENERATOR = ROOT / "demo" / "horizon" / "generate_suite.py"
PAIRS = 200

#: caught / known, per mode, over the generated corpus.
EXPECTED = {
    "budget_exhausted": (16, 16),
    "context_overflow": (6, 16),      # the threshold's edge; see the docstring
    "drift": (16, 16),
    "forgotten_constraint": (16, 16),
    "late_fault": (15, 15),
    "out_of_order": (15, 15),
    "regression": (0, 15),            # the blind spot, pinned as a blind spot
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
        self.assertEqual((self.det["caught"], self.det["total"]), (159, 184))

    def test_no_run_known_to_be_correct_is_flagged(self):
        """216 runs that did nothing wrong — the controls of the control
        tasks and the finishing run of every other task. One flag here is
        one reason not to trust the rest of the card."""
        controls = self.det["controls"]
        self.assertEqual(controls["runs"], 216)
        self.assertEqual(controls["flagged"], 0, controls["false_positives"])

    def test_the_grade_alone_would_miss_more_than_half_of_them_at_scale(self):
        self.assertEqual(self.det["graded_pass"], 107)
        self.assertEqual(self.det["graded_pass_caught_otherwise"], 82)
        # the dimension that catches most of them, and by how far
        signals = self.det["by_signal"]
        self.assertEqual(next(iter(signals)), "milestones")
        self.assertGreater(signals["milestones"], signals["grade"])

    def test_the_blind_spots_are_still_blind_and_named(self):
        """When this fails because something improved, update `EXPECTED`,
        `docs/HORIZON.md` and this list together."""
        self.assertEqual(sorted(set(self.det["missed"])), ["context_overflow", "regression"])
        rows = {r["task"]: r for r in self.det["modes"]}
        for row in self.det["modes"]:
            if row["mode"] == "regression":
                self.assertEqual(row["signals"], [], row["task"])
        self.assertTrue(any(rows[t]["caught"] for t, r in rows.items() if r["mode"] == "context_overflow"),
                        "context_overflow is partly caught, not wholly missed")
