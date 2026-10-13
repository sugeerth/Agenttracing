"""Self-evolve, faster and surer: runs at once (run_duel jobs=), a weak kept verdict confirmed on the tasks it
moved (self_evolve confirm=), the harness it ends with tested on tasks it never evolved on (--holdout), and what
a change cost in tokens on its card."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.harness import vendors  # noqa: E402
from agentdiff.selfevolve import self_evolve  # noqa: E402
from test_explore import _silent  # noqa: E402

FAKE = ROOT / "tests" / "fixtures" / "vendors" / "fake_claude.py"


class JobsTest(unittest.TestCase):
    def test_runs_go_at_once_and_come_back_in_order(self):
        live, most = [0], [0]
        lock = threading.Lock()

        def run_vendor(spec, task, out, *, run, **kw):
            with lock:
                live[0] += 1
                most[0] = max(most[0], live[0])
            time.sleep(0.2)
            with lock:
                live[0] -= 1
            return {"task": task["id"], "run": run, "agent": spec.agent}
        tasks = [{"id": f"t{i}", "prompt": "p", "workspace": "."} for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(vendors, "run_vendor", run_vendor):
            spec = SimpleNamespace(agent="a")
            t0 = time.time()
            recs = vendors.run_duel(tasks, [spec], Path(tmp), runs=2, baseline=False, jobs=4)
            took = time.time() - t0
        self.assertEqual(most[0], 4, "four at once")
        self.assertLess(took, 1.2, "eight runs of 0.2 s, four at a time")
        self.assertEqual([(r["task"], r["run"]) for r in recs],
                         [(f"t{i}", f"r{n}") for i in range(4) for n in (1, 2)], "task and run order")


def _lucky_arm(holds: bool):
    """t1 passes under both; t2 passes once with the change, by luck unless ``holds``; t3 always fails.
    A confirmation (only=) runs t2 again: with the change it passes again only when ``holds``."""
    calls = []

    def arm(harness, label, only=None):
        calls.append((label, only))
        changed = bool(harness.remedies)
        out = []
        for t in only or ("t1", "t2", "t3"):
            for i in (1, 2):
                if t == "t1":
                    ok = True
                elif t == "t2":
                    ok = changed and (holds or (i == 1 and only is None))
                else:
                    ok = False
                out.append(_silent(t, ok, f"{label}-{i}"))
        return out
    return arm, calls


class ConfirmTest(unittest.TestCase):
    def test_a_lucky_win_is_run_again_and_reverted_when_it_does_not_hold(self):
        arm, calls = _lucky_arm(holds=False)
        out = self_evolve(arm, generations=1, explore=True, confirm=True)
        t = out["lineage"][0]["action"]["test"]
        self.assertEqual(t["confirm"]["tasks"], ["t2"])
        self.assertFalse(t["confirm"]["held"])
        self.assertEqual(t["confirm"]["first"]["verdict"], "kept")
        self.assertEqual(t["verdict"], "reverted", "decided on every run of t2 together")
        self.assertIn(("g0-h0-confirm", ["t2"]), calls)
        self.assertIn(("g0-h1-confirm", ["t2"]), calls)

    def test_a_win_that_holds_is_kept(self):
        arm, _ = _lucky_arm(holds=True)
        out = self_evolve(arm, generations=1, explore=True, confirm=True)
        t = out["lineage"][0]["action"]["test"]
        self.assertTrue(t["confirm"]["held"])
        self.assertEqual(t["verdict"], "kept")

    def test_without_confirm_one_lucky_task_is_enough(self):
        arm, calls = _lucky_arm(holds=False)
        out = self_evolve(arm, generations=1, explore=True)
        self.assertEqual(out["lineage"][0]["action"]["test"]["verdict"], "kept")
        self.assertFalse(any(label.endswith("-confirm") for label, _ in calls))


@unittest.skipIf(os.name == "nt", "the stand-in is started by its shebang")
class HoldoutTest(unittest.TestCase):
    def test_the_harness_it_ends_with_is_tested_on_a_task_it_never_saw(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tasks = []
            for i in range(3):
                shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", tmp / f"w{i}")
                tasks.append({"id": f"pricing-{i}", "prompt": "Fix the bug in pricing.py.", "workspace": f"w{i}",
                              "check": "python3 -m unittest -q"})
            (tmp / "tasks.json").write_text(json.dumps({"tasks": tasks}))
            out = tmp / "evo"
            done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--task", str(tmp / "tasks.json"),
                                   "--agent", "claude", "--claude-bin", str(FAKE), "--runs", "1", "--generations", "2",
                                   "--holdout", "1", "--jobs", "2", "-o", str(out)],
                                  capture_output=True, text=True, timeout=300,
                                  env=dict(os.environ, FAKE_VENDOR_MODE="careless", PYTHONPATH=str(ROOT)))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertIn("held out: pricing-2", done.stdout)
            result = json.loads((out / "self-evolve.json").read_text())
            self.assertEqual(result["lineage"][0]["runs"], 2, "the held-out task never ran while it evolved")
            h = result["holdout"]
            self.assertEqual(h["tasks"], ["pricing-2"])
            self.assertEqual(h["test"]["passed"]["current"], [0, 1])
            self.assertEqual(h["test"]["passed"]["changed"], [1, 1])
            self.assertEqual(h["says"], "it helped on tasks it never saw")
            self.assertTrue((out / "holdout-h0" / "traces").is_dir())

    def test_holding_out_every_task_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t.json").write_text(json.dumps({"tasks": [
                {"id": "a", "prompt": "p", "workspace": ".", "check": "true"}]}))
            done = subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--task",
                                   str(Path(tmp) / "t.json"), "--holdout", "1", "--dry-run"],
                                  capture_output=True, text=True, timeout=60, env=dict(os.environ, PYTHONPATH=str(ROOT)))
            self.assertEqual(done.returncode, 2)
            self.assertIn("no task to evolve on", done.stderr)


class CardTest(unittest.TestCase):
    def test_the_card_says_whether_it_held_what_it_cost_and_how_it_did_unseen(self):
        from agentdiff.hub.evolvecards import evolve_card
        test = {"verdict": "kept", "why": "it won 1 task(s) and lost 0", "passed": {"current": [3, 6], "changed": [6, 8]},
                "p_value": 0.5, "chance": "x", "median_tokens": {"current": 1000, "changed": 1300}, "wins": ["t2"],
                "losses": [], "confirm": {"tasks": ["t2"], "runs": 4, "held": True, "first": {"chance": "y"}}}
        data = {"lineage": [{"generation": "g0", "runs": 6, "failed": 3, "harness": {"version": 0},
                             "action": {"remedy": {"id": "explore:edge_cases"}, "because": {"source": "exploration"},
                                        "test": test}, "evals": {}},
                            {"generation": "g1", "runs": 6, "failed": 0, "harness": {"version": 1}, "action": None,
                             "evals": {}}],
                "harness": {"version": 1, "instructions": ["x"], "deny_tools": []},
                "holdout": {"tasks": ["t4", "t5"], "version": 1, "says": "it helped on tasks it never saw",
                            "test": {"verdict": "kept", "passed": {"current": [1, 4], "changed": [4, 4]},
                                     "p_value": 0.25}}}
        out = evolve_card(SimpleNamespace(id="a" * 12, title="t", summary={}), data)
        self.assertIn("held when t2 ran again", out)
        self.assertIn("tokens +30%", out)
        self.assertIn("On tasks it never evolved on</b> (t4, t5)", out)
        self.assertIn("1/4 as it ships → 4/4 with v1", out)
        self.assertIn("1 in 4 by chance", out)


if __name__ == "__main__":
    unittest.main()
