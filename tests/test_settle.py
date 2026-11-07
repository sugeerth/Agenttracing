"""Self-evolve, settled early: the changed harness runs a round at a time and stops once no outcome of the runs
left could make it kept (selfevolve.could_keep). Exact over counts, so the verdict is the full arm's."""

from __future__ import annotations

import itertools
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff.selfevolve import could_keep, decide, self_evolve  # noqa: E402
from test_explore import _silent  # noqa: E402


def _runs(counts: dict, label: str) -> list:
    """{task: (passed, of)} as runs, passes first."""
    return [_silent(t, i < p, f"{label}-{i}") for t, (p, n) in counts.items() for i in range(n)]


class CouldKeepTest(unittest.TestCase):
    def test_a_loss_and_a_break_left_open_cannot_be_outrun(self):
        base = _runs({"t1": (2, 2), "t2": (0, 2)}, "a")
        # t1 failed its first run under the change: its best end is 1/2, a loss. t2's best is a win. Even
        self.assertFalse(could_keep(base, _runs({"t1": (0, 1), "t2": (0, 1)}, "b"), 1))
        self.assertTrue(could_keep(base, _runs({"t1": (1, 1), "t2": (0, 1)}, "b"), 1))

    def test_with_nothing_left_it_is_decide_itself(self):
        base = _runs({"t1": (1, 2), "t2": (0, 2)}, "a")
        for cand in ({"t1": (2, 2), "t2": (0, 2)}, {"t1": (1, 2), "t2": (0, 2)}, {"t1": (2, 2), "t2": (0, 0)}):
            runs = _runs(cand, "b")
            self.assertEqual(could_keep(base, runs, 0), decide(base, runs)["verdict"] == "kept", cand)

    def test_it_agrees_with_every_completion_decide_could_see(self):
        """Brute force: could_keep is True exactly when some outcome of the runs left makes decide keep it."""
        rng = random.Random(7)
        checked = 0
        for _ in range(400):
            tasks = [f"t{i}" for i in range(rng.randint(1, 3))]
            n = rng.randint(1, 3)
            base = {t: (rng.randint(0, n), n) for t in tasks}
            done = rng.randint(0, n)
            cand = {t: (rng.randint(0, done), done) for t in tasks}
            left = n - done
            reachable = False
            for ends in itertools.product(range(left + 1), repeat=len(tasks)):
                full = {t: (cand[t][0] + e, done + left) for t, e in zip(tasks, ends)}
                if decide(_runs(base, "a"), _runs(full, "b"))["verdict"] == "kept":
                    reachable = True
                    break
            self.assertEqual(could_keep(_runs(base, "a"), _runs(cand, "b"), left), reachable, (base, cand, left))
            checked += 1
        self.assertEqual(checked, 400)


def _rounds_arm(helps: bool):
    """Without the change t1 always passes and t2 always fails. With it, both pass if ``helps``; else each fails
    its first run and passes its second: t1 lost and t2 won, a tie, reverted. After that first round t1 can at
    best end 1/2, a loss, so t2's win can no longer outrun it. Records which rounds each arm ran."""
    calls = []

    def arm(harness, label, only=None, rounds=None):
        calls.append((label, rounds))
        changed = bool(harness.remedies)
        first, count = rounds or (1, 2)
        out = []
        for t in ("t1", "t2"):
            for i in range(first, first + count):
                ok = (helps or i == 2) if changed else t == "t1"
                out.append(_silent(t, ok, f"{label}-r{i}"))
        return out
    return arm, calls


class SelfEvolveSettleTest(unittest.TestCase):
    def test_a_change_that_cannot_win_stops_after_one_round_with_the_full_arms_verdict(self):
        arm, calls = _rounds_arm(helps=False)
        out = self_evolve(arm, generations=1, explore=True, settle=True)
        t = out["lineage"][0]["action"]["test"]
        self.assertEqual(t["verdict"], "reverted")
        self.assertEqual(t["settled"], {"after": 1, "of": 2, "skipped": 2})
        self.assertIn("settled after 1 of 2 round(s)", t["why"])
        self.assertEqual([c for c in calls if c[0] == "g0-h1"], [("g0-h1", (1, 1))], "round 2 never ran")
        from agentdiff.hub.evochart import _verdicts
        self.assertEqual(_verdicts(out)["g0"]["said"], "reverted · settled after 1 of 2 rounds")
        # the same change, every run run: the same verdict
        full_arm, _ = _rounds_arm(helps=False)
        full = self_evolve(full_arm, generations=1, explore=True)
        self.assertEqual(full["lineage"][0]["action"]["test"]["verdict"], "reverted")

    def test_a_change_that_could_still_win_runs_every_round(self):
        arm, calls = _rounds_arm(helps=True)
        out = self_evolve(arm, generations=1, explore=True, settle=True)
        t = out["lineage"][0]["action"]["test"]
        self.assertEqual(t["verdict"], "kept")
        self.assertNotIn("settled", t)
        self.assertEqual([c[1] for c in calls if c[0] == "g0-h1"], [(1, 1), (2, 1)])
        self.assertEqual(t["passed"]["changed"], [4, 4],
                         "the runs a kept change carries are complete")

    def test_an_arm_without_rounds_runs_whole(self):
        def arm(harness, label):
            return [_silent(t, False, f"{label}-{i}") for t in ("t1", "t2") for i in (1, 2)]
        out = self_evolve(arm, generations=1, explore=True, settle=True)
        self.assertNotIn("settled", out["lineage"][0]["action"]["test"])


class VendorArmTest(unittest.TestCase):
    def test_a_round_runs_only_its_runs_into_the_same_arm(self):
        import tempfile
        from agentdiff.harness import selfevolve as hse
        from agentdiff.selfevolve import Harness
        seen = []

        def run_duel(tasks, specs, d, *, runs, first_run=1, **kw):
            seen.append((d.name, first_run, runs))
            return []
        spec = mock.Mock(kind="claude", extra=[], agent="a", model="")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hse, "run_duel", run_duel):
            arm = hse.vendor_arm([{"id": "t1", "prompt": "p", "workspace": "."}], [spec], Path(tmp), runs=3)
            arm(Harness(), "g0-h0")
            arm(Harness(), "g0-h1", rounds=(2, 1))
        self.assertEqual(seen, [("g0-h0", 1, 3), ("g0-h1", 2, 1)])


if __name__ == "__main__":
    unittest.main()
