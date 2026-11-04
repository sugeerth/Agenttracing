"""How much more of a resource one agent uses per run than another (efficiency.resource_ratio_over_runs) and
the exact sign-flip test behind it (statistics.sign_flip_p)."""

from __future__ import annotations

import glob
import json
import math
import random
import sys
import unittest
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.efficiency import resource_ratio_over_runs, resource_text, resources_per_run  # noqa: E402
from agentdiff.statistics import sign_flip_p  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402


def _traj(task, run, tokens=0, seconds=0.0, steps=1):
    return Trajectory.from_dict({
        "schema_version": 1, "trace_id": f"{task}-{run}", "run_id": f"r{run}", "agent": {"name": "x"},
        "task": {"id": task, "prompt": "p"}, "outcome": {"success": True, "answer": "a"},
        "totals": {"input_tokens": tokens, "output_tokens": 0, "latency_s": seconds},
        "steps": [{"index": i, "type": "answer", "name": "final", "input": "", "output": "a"} for i in range(steps)]})


class SignFlipTest(unittest.TestCase):
    def test_matches_enumeration(self):
        vals = [0.4, -0.1, 0.25, 0.3, -0.05]
        hits = sum(abs(sum(s * v for s, v in zip(signs, vals))) >= abs(sum(vals)) - 1e-12
                   for signs in product((1, -1), repeat=len(vals)))
        self.assertAlmostEqual(sign_flip_p(vals), hits / 32, places=12)
        self.assertIsNone(sign_flip_p([]))
        self.assertEqual(sign_flip_p([0.3] * 6), 2 / 64, "six values all one way: the lowest p six can give")

    def test_never_runs_over_its_level(self):
        """Exact over the null: with any fixed magnitudes and every sign pattern equally likely, the share of
        patterns with p at or below a level never exceeds that level."""
        rng = random.Random(4)
        for n in (3, 6, 9):
            mags = [abs(rng.gauss(0, 1)) + 0.01 for _ in range(n)]
            ps = [sign_flip_p([s * m for s, m in zip(signs, mags)]) for signs in product((1, -1), repeat=n)]
            for level in (0.05, 0.1, 0.25):
                self.assertLessEqual(sum(p <= level for p in ps) / len(ps), level + 1e-12, (n, level))

    def test_drawn_past_the_exact_limit_is_seeded_and_close(self):
        vals = [0.1 * ((-1) ** i) + 0.02 for i in range(20)]
        p = sign_flip_p(vals)
        self.assertEqual(p, sign_flip_p(vals), "the same seed, the same p")
        self.assertTrue(0 < p <= 1)


class ResourceRatioTest(unittest.TestCase):
    def test_twice_on_every_task(self):
        data = {f"t{i}": {"a": [_traj(f"t{i}", r, tokens=100 * (i + 1)) for r in range(2)],
                          "b": [_traj(f"t{i}", r, tokens=200 * (i + 1)) for r in range(3)]} for i in range(6)}
        r = resource_ratio_over_runs(data, "tokens")
        self.assertEqual((r["ratio"], r["low"], r["high"], r["tasks"]), (2.0, 2.0, 2.0, 6))
        self.assertEqual(r["runs"], {"a": 12, "b": 18})
        self.assertEqual(r["p"], round(2 / 64, 4))
        self.assertIn("uses 2.00× A's tokens per run", resource_text(r, {"a": "A", "b": "B"}))

    def test_a_task_counts_once_however_many_runs(self):
        """Ten cheap runs on one task do not outweigh one run on another: the ratio is over tasks."""
        data = {"big": {"a": [_traj("big", 0, tokens=1000)], "b": [_traj("big", 0, tokens=4000)]},
                "small": {"a": [_traj("small", r, tokens=10) for r in range(10)], "b": [_traj("small", r, tokens=10) for r in range(10)]},
                "mid": {"a": [_traj("mid", 0, tokens=100)], "b": [_traj("mid", 0, tokens=100)]}}
        self.assertAlmostEqual(resource_ratio_over_runs(data, "tokens")["ratio"], round(4 ** (1 / 3), 4), places=4)

    def test_unlogged_is_left_out_and_counted(self):
        data = {f"t{i}": {"a": [_traj(f"t{i}", 0, seconds=1.0 if i else 0.0)], "b": [_traj(f"t{i}", 0, seconds=1.5)]} for i in range(4)}
        r = resource_ratio_over_runs(data, "seconds")
        self.assertEqual((r["tasks"], r["tasks_unlogged"]), (3, ["t0"]))
        few = resource_ratio_over_runs({k: data[k] for k in ("t0", "t1", "t2")}, "seconds")
        self.assertIsNone(few["low"])
        self.assertIn("no interval", few["reason"])
        self.assertIn("no task has tokens", resource_ratio_over_runs(data, "tokens")["reason"])
        with self.assertRaises(ValueError):
            resource_ratio_over_runs(data, "watts")

    def test_null_suites_false_alarm_within_level(self):
        """Synthetic, seeded: two identical agents on tasks of very different sizes, each side's runs of a task
        sharing a batch nudge. The sign-flip test shows a difference at most about 5% of the time."""
        rng = random.Random(9)
        alarms, sims = 0, 300
        for _ in range(sims):
            data = {}
            for i in range(8):
                base = math.exp(rng.gauss(9, 1))
                na, nb = math.exp(rng.gauss(0, 0.2)), math.exp(rng.gauss(0, 0.2))
                data[f"t{i}"] = {"a": [_traj(f"t{i}", r, tokens=max(1, round(base * na * math.exp(rng.gauss(0, .3))))) for r in range(3)],
                                 "b": [_traj(f"t{i}", r, tokens=max(1, round(base * nb * math.exp(rng.gauss(0, .3))))) for r in range(3)]}
            alarms += resource_ratio_over_runs(data, "tokens")["p"] <= 0.05
        self.assertLessEqual(alarms / sims, 0.07)

    def test_recorded_aa_suite_shows_no_difference(self):
        """Real runs of one agent under two names: none of its resources should differ."""
        data = {}
        for path in sorted(glob.glob(str(ROOT / "demo/vendors/aa-suite/traces/*.json"))):
            t = Trajectory.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
            data.setdefault(t.task.id, {"a": [], "b": []})["a" if t.agent.name == "A" else "b"].append(t)
        for name, r in resources_per_run(data).items():
            self.assertEqual(r["tasks"], 6, name)
            self.assertGreater(r["p"], 0.05, name)


if __name__ == "__main__":
    unittest.main()
