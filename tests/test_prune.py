"""Self-evolve, pruned: each change the evolution kept is tested again against the harness it ended with, the
newest first, and dropped when it no longer wins by the rule that kept it (selfevolve.prune)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.selfevolve import Harness, prune  # noqa: E402
from test_explore import _silent  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "vendors" / "fake_claude.py"
HELPS = {"id": "explore:edge_cases", "knob": "instruction", "value": "Test every edge case."}
LUCKY = {"id": "explore:review_diff", "knob": "instruction", "value": "Re-read the diff."}


def _kept(*remedies) -> dict:
    """A finished evolution that kept ``remedies`` in turn."""
    h = Harness()
    lineage = []
    for i, r in enumerate(remedies):
        lineage.append({"generation": f"g{i}", "harness": {"version": h.version}, "runs": 4, "failed": 2,
                        "evals": {}, "action": {"remedy": r, "because": {"source": "exploration"},
                                                "test": {"verdict": "kept"}}})
        h = h.with_remedy(r)
    return {"lineage": lineage, "harness": h.__dict__ | {}, "ledger": {"tried": {r["id"]: "kept" for r in remedies}}}


def _arm():
    """t1 passes only with the edge-case instruction; t2 never passes. The diff instruction changes nothing."""
    calls = []

    def arm(harness, label, only=None, rounds=None):
        calls.append((label, list(harness.remedies)))
        return [_silent(t, t == "t1" and HELPS["id"] in harness.remedies, f"{label}-{i}")
                for t in ("t1", "t2") for i in (1, 2)]
    return arm, calls


class PruneTest(unittest.TestCase):
    def test_a_change_that_no_longer_wins_is_dropped_and_one_that_does_stays(self):
        arm, calls = _arm()
        out = prune(arm, _kept(HELPS, LUCKY))
        p = out["prune"]
        self.assertEqual([(t["remedy"]["id"], t["stays"]) for t in p["tested"]],
                         [(LUCKY["id"], False), (HELPS["id"], True)], "newest first")
        self.assertEqual(p["dropped"], [LUCKY["id"]])
        self.assertEqual(out["harness"]["remedies"], [HELPS["id"]])
        self.assertEqual(out["harness"]["instructions"], [HELPS["value"]])
        self.assertEqual(p["says"], "1 of 2 kept change(s) no longer earned their place")
        self.assertEqual(out["ledger"]["harness"]["remedies"], [HELPS["id"]], "the next call continues from it")
        self.assertEqual(out["ledger"]["tried"][LUCKY["id"]], "pruned", "and does not try it again")
        # the edge-case instruction was tested against the harness the lucky one left, not the one it began with
        self.assertEqual([c[1] for c in calls], [[HELPS["id"], LUCKY["id"]], [HELPS["id"]], []])
        self.assertIn("no task changed", p["tested"][0]["test"]["why"])

    def test_the_card_says_what_each_test_again_found(self):
        from types import SimpleNamespace
        from agentdiff.hub.evolvecards import evolve_card
        arm, _ = _arm()
        out = prune(arm, _kept(HELPS, LUCKY))
        card = evolve_card(SimpleNamespace(id="a" * 12, title="t", summary={}), dict(out, harness=out["harness"]))
        self.assertIn("<b>Tested again</b>: 1 of 2 kept change(s) no longer earned their place", card)
        self.assertIn('<span class="mut">explore:review_diff dropped</span> (2/4 without, 2/4 with)', card)
        self.assertIn('<span class="ok">explore:edge_cases stays</span> (0/4 without, 2/4 with)', card)

    def test_nothing_kept_is_nothing_to_prune(self):
        arm, calls = _arm()
        out = prune(arm, {"lineage": [], "harness": Harness().__dict__})
        self.assertEqual(out["prune"]["says"], "nothing was kept, so nothing to prune")
        self.assertEqual(calls, [])


@unittest.skipIf(os.name == "nt", "the stand-in is started by its shebang")
class CommandTest(unittest.TestCase):
    def test_a_finished_run_is_pruned_with_no_generation_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tasks = []
            for i in range(2):
                shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", tmp / f"w{i}")
                tasks.append({"id": f"pricing-{i}", "prompt": "Fix the bug in pricing.py.", "workspace": f"w{i}",
                              "check": "python3 -m unittest -q"})
            (tmp / "tasks.json").write_text(json.dumps({"tasks": tasks}))
            base = [sys.executable, "-m", "agentdiff", "self-evolve", "--task", str(tmp / "tasks.json"),
                    "--agent", "claude", "--claude-bin", str(FAKE), "--runs", "1", "-o", str(tmp / "evo")]
            env = dict(os.environ, FAKE_VENDOR_MODE="careless", PYTHONPATH=str(ROOT))
            first = subprocess.run(base + ["--generations", "1"], capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            again = subprocess.run(base + ["--generations", "0", "--prune"], capture_output=True, text=True,
                                   timeout=300, env=env)
            self.assertEqual(again.returncode, 0, again.stderr + again.stdout)
            self.assertIn("prune: every one of the 1 kept change(s) still earns its place", again.stdout)
            result = json.loads((tmp / "evo" / "self-evolve.json").read_text())
            self.assertEqual(len(result["prune"]["tested"]), 1)
            self.assertTrue(result["prune"]["tested"][0]["stays"], "the careless stand-in fails without it")
            self.assertTrue((tmp / "evo" / "prune-h1" / "records").is_dir())
            self.assertTrue((tmp / "evo" / "prune-h2" / "records").is_dir(), "the harness without it ran too")


if __name__ == "__main__":
    unittest.main()
