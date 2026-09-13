"""The loop's controller: rules over numbers, the same plan for the
same state, and a sentence with every decision."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.planner import (  # noqa: E402
    TIE_RUNS, add_candidates, apply_decision, decide_prompt, new_state, plan, summarise,
)


def _routing(confidences, n=3, tie=False):
    fams = {}
    for i, conf in enumerate(confidences):
        fams[f"fam{i}"] = {"confidence": conf, "why": conf, "tasks": [f"fam{i}"],
                           "candidates": [{"agent": "a", "features": {"rate": 1.0, "n": n, "ci95": [0.3, 1.0]}},
                                          {"agent": "b", "features": {"rate": 1.0 if tie else 0.5, "n": n, "ci95": [0.2, 0.9]}}]}
    return {"families": fams}


class PlanTest(unittest.TestCase):
    def test_the_first_iteration_is_a_full_baseline_and_the_plan_is_deterministic(self):
        st = new_state(["a", "b"], ["t1", "t2", "t3"])
        p = plan(st, runs=3)
        self.assertEqual(p["action"], "compare")
        self.assertEqual((p["tasks"], p["agents"], p["runs"]), (["t1", "t2", "t3"], ["a", "b"], 3))
        self.assertIn("baseline", p["why"])
        self.assertEqual(plan(st, runs=3), p)

    def test_budgets_stop_the_loop_with_the_reason(self):
        st = new_state(["a", "b"], ["t1"])
        self.assertEqual(plan(st, max_iterations=0)["kind"], "iterations")
        st["spent_runs"] = 10
        self.assertEqual(plan(st, max_runs=10)["kind"], "runs")
        st["spent_runs"] = 0
        p = plan(st, runs=3, max_runs=4)   # 1 task × 2 agents × 3 runs = 6 > 4 → shrink to 2 runs
        self.assertEqual((p["action"], p["runs"]), ("compare", 2))
        self.assertEqual(plan(st, runs=3, max_runs=1)["action"], "stop")

    def test_runs_go_to_uncertain_families_widest_first_and_a_clear_table_converges(self):
        st = new_state(["a", "b"], ["fam0", "fam1", "fam2"])
        st["iterations"].append({"action": "compare", "routing": _routing(["clear", "insufficient", "overlapping"])})
        p = plan(st, runs=2)
        self.assertEqual(p["action"], "compare")
        self.assertEqual(sorted(p["tasks"]), ["fam1", "fam2"])
        self.assertIn("not yet clear", p["why"])
        st["iterations"][-1]["routing"] = _routing(["clear", "clear"])
        self.assertEqual(plan(st)["kind"], "converged")

    def test_equal_rates_over_enough_runs_are_a_tie_not_uncertainty(self):
        st = new_state(["a", "b"], ["fam0"])
        st["iterations"].append({"action": "compare", "routing": _routing(["overlapping"], n=TIE_RUNS, tie=True)})
        self.assertEqual(plan(st)["kind"], "converged")
        st["iterations"][-1]["routing"] = _routing(["overlapping"], n=TIE_RUNS - 1, tie=True)
        self.assertEqual(plan(st)["action"], "compare")

    def test_a_queued_hypothesis_is_tested_before_more_runs_one_at_a_time(self):
        st = new_state(["a", "b"], ["t1", "t2"])
        st["iterations"].append({"action": "compare", "routing": _routing(["insufficient", "insufficient"])})
        self.assertEqual(add_candidates(st, "a", [{"kind": "k1", "text": "do X", "from_tasks": ["t1"]},
                                                  {"kind": "k2", "text": "do Y"}, {"kind": "k1", "text": "dup"}], source="s"), 2)
        p = plan(st, runs=2)
        self.assertEqual((p["action"], p["agent"], p["candidate"]["kind"], p["variant"]), ("test-prompt", "a", "k1", "p1"))
        self.assertEqual(p["tasks"], ["t1", "t2"], "every task, so a regression elsewhere shows")
        self.assertIn("one variable at a time", p["why"])
        tight = plan(st, runs=2, max_runs=4)
        self.assertEqual(tight["tasks"], ["t1"], "under a tight budget only the tasks it came from")

    def test_a_hypothesis_whose_failure_no_longer_reproduces_is_dropped_with_its_reason(self):
        st = new_state(["a", "b"], ["t1", "t2"])
        st["iterations"].append({"action": "compare", "routing": _routing(["clear", "clear"])})
        add_candidates(st, "a", [{"kind": "k1", "text": "do X", "from_tasks": ["t1"]}], source="s")
        st["latest"] = {"a": {"t1": [2, 2], "t2": [2, 2]}}
        self.assertEqual(plan(st)["kind"], "converged")
        hist = st["prompts"]["a"]["history"]
        self.assertEqual([h["status"] for h in hist], ["dropped"])
        self.assertIn("no longer reproduces", hist[0]["why"])

    def test_agents_without_a_tunable_prompt_get_no_candidates(self):
        st = new_state(["a", "py"], ["t1"], prompt_agents=["a"])
        self.assertEqual(add_candidates(st, "py", [{"kind": "k", "text": "x"}], source="s"), 0)

    def test_a_kept_change_re_measures_the_tasks_it_did_not_cover(self):
        st = new_state(["a", "b"], ["t1", "t2", "t3"])
        st["iterations"].append({"action": "compare", "routing": _routing(["clear", "clear", "clear"])})
        st["needs_runs"] = {"a": ["t2", "t3"]}
        p = plan(st, runs=2)
        self.assertEqual((p["action"], p["tasks"]), ("compare", ["t2", "t3"]))
        self.assertIn("re-measure", p["why"])


class DecideTest(unittest.TestCase):
    def test_keep_and_revert_carry_counts_and_a_sentence(self):
        cand = {"kind": "k", "text": "do X", "source": "s"}
        d = decide_prompt({"t1": (0, 2), "t2": (0, 2), "t3": (2, 2)}, {"t1": (2, 2), "t2": (2, 2), "t3": (2, 2)}, agent="a", candidate=cand)
        self.assertEqual(d["status"], "kept (provisional)")
        self.assertEqual((d["evidence"]["wins"], d["evidence"]["losses"], d["evidence"]["ties"]), (2, 0, 1))
        self.assertEqual(d["evidence"]["improvements"], ["t1", "t2"])
        self.assertIn("provisionally", d["why"])
        strong = decide_prompt({f"t{i}": (0, 1) for i in range(6)}, {f"t{i}": (1, 1) for i in range(6)}, agent="a", candidate=cand)
        self.assertEqual(strong["status"], "kept")
        self.assertLess(strong["evidence"]["sign_test_p"], 0.05)
        no_effect = decide_prompt({"t1": (1, 1)}, {"t1": (1, 1)}, agent="a", candidate=cand)
        self.assertEqual(no_effect["status"], "reverted")
        regress = decide_prompt({"t1": (0, 2), "t2": (0, 2), "t3": (2, 2)}, {"t1": (2, 2), "t2": (2, 2), "t3": (0, 2)}, agent="a", candidate=cand)
        self.assertEqual(regress["status"], "reverted")
        self.assertEqual(regress["evidence"]["regressions"], ["t3"])
        self.assertIn("regressed t3", regress["why"])

    def test_apply_moves_the_candidate_to_history_and_a_keep_changes_the_prompt(self):
        st = new_state(["a", "b"], ["t1"], base_prompt="Base.")
        add_candidates(st, "a", [{"kind": "k", "text": "Do X."}], source="s")
        d = decide_prompt({"t1": (0, 2)}, {"t1": (2, 2)}, agent="a", candidate=st["prompts"]["a"]["candidates"][0])
        apply_decision(st, d)
        slot = st["prompts"]["a"]
        self.assertEqual(slot["candidates"], [])
        self.assertEqual((slot["version"], slot["current"]), (1, "Base.\nDo X."))
        self.assertEqual(slot["history"][0]["prompt_version"], 1)
        s = summarise(st)
        self.assertEqual((s["kept_changes"], s["agents"]["a"]["kept"]), (1, ["k"]))


if __name__ == "__main__":
    unittest.main()
