"""Sanity checks for the diagnoser, in the lineage of Adebayo et al.'s
sanity checks for saliency maps: an explanation method must produce a
null or inverted result when the thing it claims to explain is removed
or reversed, and the same result when only presentation changes.

Three properties, each on a real demo pair:

* identical traces → no decisive difference is manufactured;
* swapped outcome labels → the story moves to the other run (it must
  invert or abstain, never persist on the same side);
* swapped argument order → the same substance (the verdict cannot
  depend on which file was named first — the position-bias failure the
  LLM-judge literature documents).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from deepcompare.report import compare
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent
PAIRS = [
    ("demo/traces/t05_flight_duration__atlas-v2.json",
     "demo/traces/t05_flight_duration__bolt-v3.json"),
    ("demo/traces/t01_acme_revenue__atlas-v2.json",
     "demo/traces/t01_acme_revenue__bolt-v3.json"),
    ("demo/process/traces/p01_cancel_booking__steady-v1.json",
     "demo/process/traces/p01_cancel_booking__hasty-v2.json"),
]


def _load(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _diagnose(raw_a, raw_b):
    return compare(Trajectory.from_dict(raw_a),
                   Trajectory.from_dict(raw_b))["diagnosis"]


def _lead(diag):
    return next((h for h in diag["hypotheses"] if h["id"] == diag["leading"]), None)


def _substance(diag):
    lead = _lead(diag)
    return {
        "mode": diag["mode"],
        "subject": diag.get("subject_name"),
        "kind": lead.get("kind") if lead else None,
        "flag": lead.get("flag") if lead else None,
        "decisive": (diag.get("decisive_step") or {}).get("step"),
        "scores": sorted(round(h["score"], 4) for h in diag["hypotheses"]
                         if h["score"] is not None),
    }


class TestIdenticalTraces(unittest.TestCase):
    """The same run twice has no one-sided failure to explain."""

    def test_no_single_failure_is_manufactured(self):
        for _, failing in PAIRS:
            raw = _load(failing)
            if raw["outcome"]["success"] is not False:
                continue
            twin = copy.deepcopy(raw)
            twin["agent"]["name"] = raw["agent"]["name"] + "-twin"
            diag = _diagnose(raw, twin)
            self.assertNotEqual(diag["mode"], "single_failure", failing)
            self.assertIsNone((diag.get("decisive_step") or {}).get("step"),
                              f"a decisive step was named for identical runs: {failing}")


class TestSwappedLabels(unittest.TestCase):
    """Reversing which run failed must reverse the subject of the story."""

    def test_the_story_moves_with_the_label(self):
        for pass_rel, fail_rel in PAIRS:
            raw_pass, raw_fail = _load(pass_rel), _load(fail_rel)
            baseline = _diagnose(raw_pass, raw_fail)
            if baseline["mode"] != "single_failure":
                continue
            # invert each run's own label — which file is the failing one
            # is read from the outcome, never assumed from the filename
            swapped_pass = copy.deepcopy(raw_pass)
            swapped_fail = copy.deepcopy(raw_fail)
            swapped_pass["outcome"]["success"] = not raw_pass["outcome"]["success"]
            swapped_fail["outcome"]["success"] = not raw_fail["outcome"]["success"]
            swapped = _diagnose(swapped_pass, swapped_fail)
            self.assertEqual(swapped["mode"], "single_failure")
            self.assertNotEqual(swapped["subject_name"], baseline["subject_name"],
                                f"subject did not move with the label: {fail_rel}")


class TestArgumentOrder(unittest.TestCase):
    """Which file is named first is presentation, not evidence."""

    def test_order_does_not_change_the_substance(self):
        for pass_rel, fail_rel in PAIRS:
            raw_pass, raw_fail = _load(pass_rel), _load(fail_rel)
            forward = _substance(_diagnose(raw_pass, raw_fail))
            backward = _substance(_diagnose(raw_fail, raw_pass))
            self.assertEqual(forward, backward, fail_rel)


if __name__ == "__main__":
    unittest.main()
