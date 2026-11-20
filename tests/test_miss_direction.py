"""Which way the diagnoser's wrong steps fall: before the decisive step, or after it (bench.miss_direction).

A localizer that misses late names a symptom where the cause was. One audit of failure localizers found most
wrong guesses after the true step, many steps late; this says where AgentDiff's own misses fall.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.bench import format_scorecard, miss_direction, run_benchmark  # noqa: E402


def row(cause, truth, got):
    return {"cause": cause, "decisive_truth": truth, "decisive_predicted": got}


class DirectionTest(unittest.TestCase):
    def test_early_late_and_what_does_not_count(self):
        got = miss_direction([
            row("a", [5], 4), row("a", [5], 3), row("b", [5], 7),
            row("b", [5], 5),            # exact: not a miss
            row("c", [5], None),         # names no step: not a wrong step
            row("d", [], 3),             # nothing to find: scored as abstention elsewhere
        ])
        self.assertEqual((got["early"], got["late"], got["farthest"]), (2, 1, 2))
        self.assertEqual(got["offsets"], {"-2": 1, "-1": 1, "2": 1})
        self.assertEqual(got["by_cause"], {"a": {"early": 2, "late": 0}, "b": {"early": 0, "late": 1}})
        self.assertIn("2 before the decisive step, 1 after it", got["says"])

    def test_the_nearest_acceptable_step_is_the_reference(self):
        # two acceptable decisive steps: a guess between them is measured to the nearer one
        got = miss_direction([row("a", [3, 9], 8)])
        self.assertEqual(got["offsets"], {"-1": 1})
        # equally near both: measured to the earlier, so the miss reads late
        self.assertEqual(miss_direction([row("a", [4, 8], 6)])["offsets"], {"2": 1})

    def test_no_misses(self):
        got = miss_direction([row("a", [2], 2)])
        self.assertEqual((got["early"], got["late"], got["offsets"]), (0, 0, {}))
        self.assertEqual(got["says"], "every step named was the decisive one")


class ShippedCorpusTest(unittest.TestCase):
    def test_the_scorecard_carries_it(self):
        result = run_benchmark(ROOT / "demo" / "diagnosis_bench" / "traces")
        step = result["step_localization"]
        direction = step["direction"]
        self.assertEqual(direction["early"] + direction["late"], step["total"] - step["exact"]
                         - sum(1 for r in result["results"] if r["decisive_truth"] and r["decisive_predicted"] is None))
        self.assertIn(direction["says"], format_scorecard(result))


if __name__ == "__main__":
    unittest.main()
