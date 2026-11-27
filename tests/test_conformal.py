"""Replay sets (agentdiff.conformal): a replay order over every step of the failing run, and a split-conformal k
whose top-k covers the decisive step at a stated rate on cases like the calibration set."""

from __future__ import annotations

import importlib.util
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.bench import REPLAY_ALPHAS, format_scorecard, run_benchmark  # noqa: E402
from agentdiff.conformal import conformal_k, evaluate, held_out, rank_of, replay_order  # noqa: E402
from agentdiff.report import compare  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

GENERATOR = ROOT / "demo" / "diagnosis_bench" / "generate_scale.py"


class ThresholdTest(unittest.TestCase):
    def test_k_is_the_conformal_quantile(self):
        scores = list(range(1, 20))  # m = 19: ceil(20 * 0.9) = 18th smallest
        self.assertEqual(conformal_k(scores, 0.1), 18)
        self.assertEqual(conformal_k([1, 1, 1, 2], 0.5), 1)  # ceil(5 * 0.5) = 3rd smallest

    def test_too_few_cases_for_the_target_give_no_k(self):
        self.assertIsNone(conformal_k([1] * 8, 0.1), "ceil(9 * 0.9) = 9 > 8")
        self.assertIsNone(conformal_k([], 0.1))

    def test_the_guarantee_holds_on_exchangeable_data(self):
        """Over many random calibration/test draws from one distribution, top-k covers at least 1 - alpha."""
        rng = random.Random(3)
        covered = total = 0
        for _ in range(300):
            draw = [min(int(rng.expovariate(0.6)) + 1, 9) for _ in range(60)]
            k = conformal_k(draw[:40], 0.1)
            covered += sum(1 for r in draw[40:] if r <= k)
            total += 20
        self.assertGreaterEqual(covered / total, 0.9)

    def test_the_halves_are_deterministic_and_both_used(self):
        ids = [f"case{i}" for i in range(200)]
        self.assertEqual([held_out(i) for i in ids], [held_out(i) for i in ids])
        self.assertTrue(40 < sum(map(held_out, ids)) < 160)


class OrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = ROOT / "demo" / "diagnosis_bench" / "traces"
        import json
        cls.cases = []
        for sc in json.loads((root / "MANIFEST.json").read_text())["scenarios"]:
            f, p = Trajectory.from_json(root / sc["fail"]), Trajectory.from_json(root / sc["pass"])
            cls.cases.append((sc, f, p, compare(f, p)["diagnosis"]))

    def test_every_step_once_with_the_committed_step_first(self):
        for sc, f, p, d in self.cases:
            order = replay_order(d, f, p)
            self.assertEqual(sorted(order), list(range(len(f.steps))), sc["id"])
            if d["decisive_step"]["step"] is not None:
                self.assertEqual(order[0], d["decisive_step"]["step"])
                self.assertEqual(d["decisive_step"]["replay_order"], order[:5])
            else:
                self.assertEqual(d["decisive_step"]["replay_order"], [])

    def test_rank_is_the_best_ranked_true_step(self):
        self.assertEqual(rank_of([4, 2, 0, 1, 3], [0, 3]), 3)
        self.assertIsNone(rank_of([1, 2], [7]))


class EvaluateTest(unittest.TestCase):
    def test_both_orders_go_through_the_same_procedure(self):
        rows = [{"id": f"c{i}", "rank": 1 + (i % 7 == 0), "index_rank": 1 + i % 5, "steps": 6} for i in range(300)]
        out = evaluate(rows, 0.1)
        # six in seven true steps sit at rank 1 (86%, short of 90%), the rest at rank 2: k is 2
        self.assertEqual(out["replay_order"]["k"], 2)
        self.assertLessEqual(out["replay_order"]["k"], out["step_order"]["k"])
        self.assertGreaterEqual(out["replay_order"]["coverage"], 0.8)
        self.assertEqual(out["replay_order"]["calibration"] + out["replay_order"]["test"], 300)


class GeneratedCorpusTest(unittest.TestCase):
    def test_the_scorecard_reports_held_out_coverage_against_step_order(self):
        spec = importlib.util.spec_from_file_location("diag_gen_scale", GENERATOR)
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        with tempfile.TemporaryDirectory() as tmp:
            gen.generate(Path(tmp), 150, strip=True)
            result = run_benchmark(Path(tmp))
        sets = result["replay_sets"]
        self.assertEqual([s["alpha"] for s in sets], list(REPLAY_ALPHAS))
        for s in sets:
            ro, so = s["replay_order"], s["step_order"]
            if ro["k"] is not None and so["k"] is not None:
                self.assertLessEqual(ro["k"], so["k"], "the replay order is never worse than step order here")
        self.assertIn("replay sets     held out", format_scorecard(result))


if __name__ == "__main__":
    unittest.main()
