"""Self-evolve, which tasks carry the signal: per generation, the tasks that passed some runs and failed others,
those that always passed (a change can only break them) and those that always failed (it can only win them)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.selfevolve import self_evolve, signal_text, task_signal  # noqa: E402
from test_explore import _silent  # noqa: E402


def _runs(outcomes: dict) -> list:
    return [_silent(t, ok, f"r{i}") for t, oks in outcomes.items() for i, ok in enumerate(oks)]


class SignalTest(unittest.TestCase):
    def test_each_task_is_mixed_always_passed_or_always_failed(self):
        sig = task_signal(_runs({"a": [True, False], "b": [True, True], "c": [False, False], "d": [False, True]}))
        self.assertEqual(sig, {"mixed": ["a", "d"], "always_passed": ["b"], "always_failed": ["c"]})
        self.assertEqual(signal_text(sig, 0), "of 4 task(s), 2 passed some runs and failed others under v0; "
                                             "1 always passed (a change can only break them) and 1 always failed "
                                             "(a change can only win them)")

    def test_every_generation_records_it_and_says_it(self):
        lines = []

        def arm(harness, label):
            return _runs({"t1": [True, True], "t2": [True, False], "t3": [False, False]})
        out = self_evolve(arm, generations=1, explore=True, on_progress=lines.append)
        self.assertEqual(out["lineage"][0]["signal"],
                         {"mixed": ["t2"], "always_passed": ["t1"], "always_failed": ["t3"]})
        self.assertIn("g0: of 3 task(s), 1 passed some runs and failed others under v0; 1 always passed", "\n".join(lines))

    def test_the_card_counts_them_per_generation(self):
        from agentdiff.hub.evolvecards import signal_line
        data = {"lineage": [{"generation": "g0", "failed": 3, "signal": {"mixed": ["a"], "always_passed": ["b"],
                                                                      "always_failed": ["c"]}},
                            {"generation": "g1", "failed": 0, "signal": {"mixed": [], "always_passed": ["a", "b", "c"],
                                                                      "always_failed": []}}]}
        line = signal_line(data)
        self.assertIn("<b>Tasks that could tell a change apart</b> (passed some runs, failed others): g0 1/3", line)
        self.assertNotIn("g1", line, "a generation with nothing to fix has nothing to tell apart")
        self.assertEqual(signal_line({"lineage": [{"generation": "g0", "failed": 2}]}), "", "older runs: no line")


if __name__ == "__main__":
    unittest.main()
