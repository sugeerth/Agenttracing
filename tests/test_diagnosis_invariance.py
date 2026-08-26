"""Perturbation invariance: cosmetic changes must not move the diagnosis.

A diagnosis that changes when an agent is renamed, when tool declarations
are reordered, or when run ids are relabelled is reading identity, not
evidence.  These tests apply exactly such perturbations to real demo
pairs and require the diagnostic substance — leading kind, decisive
step, causal-account steps, hypothesis scores — to be identical, with
only the naturally identity-bearing text (agent names inside verdicts
and statements) allowed to differ.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deepcompare.report import compare
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent

PAIRS = [
    (ROOT / "demo/traces/t05_flight_duration__atlas-v2.json",
     ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"),
    (ROOT / "demo/traces/t07_build_failure__atlas-v2.json",
     ROOT / "demo/traces/t07_build_failure__bolt-v3.json"),
    (ROOT / "demo/process/traces/p01_cancel_booking__steady-v1.json",
     ROOT / "demo/process/traces/p01_cancel_booking__hasty-v2.json"),
]


def _mutated_pair(path_a, path_b, mutate):
    with tempfile.TemporaryDirectory() as tmp:
        out = []
        for i, path in enumerate((path_a, path_b)):
            raw = json.loads(Path(path).read_text())
            mutate(raw, i)
            target = Path(tmp) / f"{i}.json"
            target.write_text(json.dumps(raw))
            out.append(Trajectory.from_json(str(target)))
        return compare(out[0], out[1])


def _substance(diagnosis):
    """The identity-free core of a diagnosis: what must survive renames."""
    lead = next((h for h in diagnosis["hypotheses"]
                 if h["id"] == diagnosis["leading"]), None)
    return {
        "mode": diagnosis["mode"],
        "leading_kind": lead.get("kind") if lead else None,
        "leading_flag": lead.get("flag") if lead else None,
        "mechanism": lead.get("mechanism") if lead else None,
        "margin": diagnosis["margin"],
        "decisive": (diagnosis.get("decisive_step") or {}).get("step"),
        "account_steps": [entry["step"] for entry
                          in diagnosis.get("causal_account", [])],
        "scores": sorted(round(h["score"], 4)
                         for h in diagnosis["hypotheses"]
                         if h["score"] is not None),
        "statuses": sorted(h["status"] for h in diagnosis["hypotheses"]),
    }


class TestRenameInvariance(unittest.TestCase):
    """Renaming both agents changes no diagnostic substance."""

    def test_agent_renames_do_not_move_the_diagnosis(self):
        for path_a, path_b in PAIRS:
            baseline = compare(Trajectory.from_json(str(path_a)),
                               Trajectory.from_json(str(path_b)))

            def rename(raw, i):
                raw["agent"]["name"] = f"anon-{i}"
                raw["trace_id"] = f"anon-trace-{i}"

            renamed = _mutated_pair(path_a, path_b, rename)
            self.assertEqual(_substance(baseline["diagnosis"]),
                             _substance(renamed["diagnosis"]),
                             f"substance moved for {path_a.name}")

    def test_run_id_relabels_do_not_move_the_diagnosis(self):
        for path_a, path_b in PAIRS:
            baseline = compare(Trajectory.from_json(str(path_a)),
                               Trajectory.from_json(str(path_b)))

            def relabel(raw, i):
                raw["run_id"] = f"r{99 - i}"

            relabelled = _mutated_pair(path_a, path_b, relabel)
            self.assertEqual(_substance(baseline["diagnosis"]),
                             _substance(relabelled["diagnosis"]),
                             f"substance moved for {path_a.name}")


class TestToolOrderInvariance(unittest.TestCase):
    """Reordering the declared tool list changes no diagnostic substance:
    the declaration is a set, and reading order out of it would be reading
    an accident of serialization."""

    def test_reversed_tool_declarations(self):
        for path_a, path_b in PAIRS:
            raw_a = json.loads(path_a.read_text())
            if not raw_a.get("tools"):
                continue  # nothing to reorder
            baseline = compare(Trajectory.from_json(str(path_a)),
                               Trajectory.from_json(str(path_b)))

            def reverse_tools(raw, i):
                if raw.get("tools"):
                    raw["tools"] = list(reversed(raw["tools"]))

            reordered = _mutated_pair(path_a, path_b, reverse_tools)
            self.assertEqual(_substance(baseline["diagnosis"]),
                             _substance(reordered["diagnosis"]),
                             f"substance moved for {path_a.name}")


class TestModelFieldInvariance(unittest.TestCase):
    """The model name on the agent record is metadata for the reader, not
    evidence: swapping it must not move the diagnosis."""

    def test_model_swap(self):
        for path_a, path_b in PAIRS:
            baseline = compare(Trajectory.from_json(str(path_a)),
                               Trajectory.from_json(str(path_b)))

            def swap_model(raw, i):
                raw["agent"]["model"] = "some-other-model-v9"

            swapped = _mutated_pair(path_a, path_b, swap_model)
            self.assertEqual(_substance(baseline["diagnosis"]),
                             _substance(swapped["diagnosis"]),
                             f"substance moved for {path_a.name}")


if __name__ == "__main__":
    unittest.main()
