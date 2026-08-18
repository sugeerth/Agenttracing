"""Tests for fix verification and before/after progress (v26).

The contract under test: an action is a testable hypothesis, and absence of
evidence is only evidence when the same exam was sat.  Resolved requires the
task to have re-run; fewer occurrences is improvement, not cure; new issues
are part of the answer; and a small suite's success rate is not allowed to
confirm what it cannot detect.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare.progress import compare_progress
from deepcompare.triage import _verification


def batch_dir(tmp, name, issues, actions, reports):
    directory = Path(tmp) / name
    directory.mkdir(exist_ok=True)
    aggregate = {
        "issues": {"issues": issues},
        "triage": {"actions": actions},
    }
    (directory / "aggregate.json").write_text(json.dumps(aggregate))
    for i, report in enumerate(reports):
        (directory / f"report_t{i}.json").write_text(json.dumps(report))
    return directory


def issue(fp, tasks, count=1):
    return {"id": fp, "title": fp, "tasks": tasks, "occurrence_count": count,
            "occurrences": [{}] * count, "failures_caused": 0}


def action(rank, fps, tasks, flags=(), agents=("agent-x",)):
    return {"rank": rank, "action": f"fix the thing #{rank}",
            "severity_class": "failure", "agents": list(agents),
            "evidence": {"fingerprints": list(fps), "tasks": list(tasks),
                         "process_flags": list(flags)}}


def report(task, agent_a="agent-x", agent_b="agent-y",
           ok_a=True, ok_b=True, flags_a=(), flags_b=()):
    def side(name, ok, flags):
        return {"agent": {"name": name}, "outcome": {"success": ok},
                "steps": [{"index": 0}]}
    return {
        "task": {"id": task},
        "a": side(agent_a, ok_a, flags_a), "b": side(agent_b, ok_b, flags_b),
        "process": {
            "a": {"agent": agent_a, "gap": {"raised": list(flags_a)}},
            "b": {"agent": agent_b, "gap": {"raised": list(flags_b)}},
        },
    }


class TestVerificationContract(unittest.TestCase):
    def candidate(self, **over):
        base = {"fingerprints": ["fp1"], "flags": [], "tasks": ["t1"],
                "failures": 1}
        base.update(over)
        return base

    def test_fingerprints_are_the_first_check(self):
        v = _verification(self.candidate(), {"k": 1, "n": 4, "unit": "task"}, 4, 1)
        self.assertEqual(v["checks"][0]["kind"], "fingerprint")
        self.assertIn("deterministic", v["checks"][0]["confirms"])

    def test_small_suites_are_told_the_rate_cannot_confirm(self):
        # 3/4 -> 4/4 stays inside the Wilson interval of 3/4.
        v = _verification(self.candidate(failures=1), {"k": 1, "n": 4, "unit": "task"}, 4, 1)
        metric = [c for c in v["checks"] if c["kind"] == "success_rate"][0]
        self.assertFalse(metric["single_rerun_can_confirm"])
        self.assertIn("cannot confirm", metric["note"])

    def test_a_large_expected_jump_is_confirmable(self):
        # 1/8 failing 7 -> hoped 8/8 clears the interval of 1/8.
        v = _verification(self.candidate(failures=7), {"k": 7, "n": 8, "unit": "task"}, 8, 7)
        metric = [c for c in v["checks"] if c["kind"] == "success_rate"][0]
        self.assertTrue(metric["single_rerun_can_confirm"])

    def test_the_drift_caveat_is_always_present(self):
        v = _verification(self.candidate(), {"k": 1, "n": 4, "unit": "task"}, 4, 0)
        self.assertIn("task-set drift", v["caveat"])


class TestProgress(unittest.TestCase):
    def test_resolved_requires_the_task_to_have_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before",
                               [issue("fp1", ["t0"])],
                               [action(1, ["fp1"], ["t0"])],
                               [report("t0")])
            # after: fp1 gone, but t0 did NOT re-run
            after = batch_dir(tmp, "after", [], [], [report("t9")])
            result = compare_progress(before, after)
            self.assertEqual(result["actions"][0]["status"], "unobservable")
            self.assertIn("proves nothing", result["actions"][0]["reason"])

    def test_a_cleared_fingerprint_on_a_rerun_task_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [issue("fp1", ["t0"])],
                               [action(1, ["fp1"], ["t0"])], [report("t0")])
            after = batch_dir(tmp, "after", [], [], [report("t0")])
            result = compare_progress(before, after)
            self.assertEqual(result["actions"][0]["status"], "resolved")

    def test_fewer_occurrences_is_improved_not_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [issue("fp1", ["t0", "t1"], count=3)],
                               [action(1, ["fp1"], ["t0", "t1"])],
                               [report("t0"), report("t1")])
            after = batch_dir(tmp, "after", [issue("fp1", ["t1"], count=1)],
                              [], [report("t0"), report("t1")])
            result = compare_progress(before, after)
            entry = result["actions"][0]
            self.assertEqual(entry["status"], "improved")
            self.assertIn("not resolved", entry["reason"])

    def test_process_flag_actions_are_tracked_through_the_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [],
                               [action(1, [], ["t0"], flags=["blind_write"])],
                               [report("t0", flags_a=("blind_write",))])
            after_fixed = batch_dir(tmp, "after1", [], [], [report("t0")])
            result = compare_progress(before, after_fixed)
            self.assertEqual(result["actions"][0]["status"], "resolved")
            after_broken = batch_dir(tmp, "after2", [], [],
                                     [report("t0", flags_a=("blind_write",))])
            result = compare_progress(before, after_broken)
            self.assertEqual(result["actions"][0]["status"], "persists")

    def test_new_issues_are_part_of_the_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [], [], [report("t0")])
            after = batch_dir(tmp, "after", [issue("fp_new", ["t0"])],
                              [], [report("t0")])
            result = compare_progress(before, after)
            self.assertEqual(len(result["new_issues"]), 1)
            self.assertIn("new issue", result["narrative"])

    def test_task_flips_are_named_in_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [], [],
                               [report("t0", ok_a=False), report("t1", ok_a=True)])
            after = batch_dir(tmp, "after", [], [],
                              [report("t0", ok_a=True), report("t1", ok_a=False)])
            result = compare_progress(before, after)
            s = result["success_by_agent"]["agent-x"]
            self.assertEqual(s["flips_fixed"], ["t0"])
            self.assertEqual(s["flips_broken"], ["t1"])
            self.assertIn("BROKE", result["narrative"])

    def test_a_small_suite_rate_is_not_allowed_to_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [], [],
                               [report(f"t{i}", ok_a=(i > 0)) for i in range(4)])
            after = batch_dir(tmp, "after", [], [],
                              [report(f"t{i}", ok_a=True) for i in range(4)])
            result = compare_progress(before, after)
            s = result["success_by_agent"]["agent-x"]
            self.assertFalse(s["confirmable_by_rate"])
            # 3/4 -> 4/4 happens 32% of the time with no fix at all
            self.assertIn("by luck alone", s["note"])
            self.assertGreater(s["chance_without_a_fix"], 0.25)

    def test_efficiency_shift_is_reported_per_shared_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            def agg_with_eff(cps, resend):
                return {"efficiency": {"per_agent": {"a": {
                    "agent": "agent-x",
                    "cost_per_success": {"value_usd": cps},
                    "resend_overhead_tokens": resend,
                    "cacheable_repeat_calls": 0}}}}
            before = batch_dir(tmp, "before", [], [], [report("t0")])
            after = batch_dir(tmp, "after", [], [], [report("t0")])
            import json as _json
            (Path(tmp) / "before" / "aggregate.json").write_text(_json.dumps(
                {"issues": {"issues": []}, "triage": {"actions": []},
                 **agg_with_eff(0.01, 1000)}))
            (Path(tmp) / "after" / "aggregate.json").write_text(_json.dumps(
                {"issues": {"issues": []}, "triage": {"actions": []},
                 **agg_with_eff(0.005, 600)}))
            result = compare_progress(Path(tmp) / "before", Path(tmp) / "after")
            shift = result["efficiency_shift"]
            self.assertTrue(shift["available"])
            entry = shift["per_agent"]["agent-x"]
            self.assertEqual(entry["cost_per_success_usd"]["delta"], -0.005)
            # the estimated figure keeps its estimated framing
            self.assertIn("structural change",
                          entry["resend_overhead_tokens"]["basis"])

    def test_efficiency_shift_degrades_without_shared_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [], [], [report("t0")])
            after = batch_dir(tmp, "after", [], [], [report("t0")])
            result = compare_progress(before, after)
            self.assertFalse(result["efficiency_shift"]["available"])

    def test_regressions_are_the_narrow_gate_worthy_subset(self):
        from deepcompare.progress import regressions_in
        result = {
            "actions": [
                {"status": "persists", "action": "still broken"},
                {"status": "worsened", "action": "got worse",
                 "occurrences": {"before": 1, "after": 3}},
            ],
            "new_issues": [{"title": "fresh problem", "occurrences": 2}],
            "success_by_agent": {"x": {"flips_broken": ["t3"],
                                       "flips_fixed": ["t1"]}},
        }
        findings = regressions_in(result)
        # persisting is a fix not landing, not a regression
        self.assertEqual(len(findings), 3)
        self.assertTrue(any("worsened" in f for f in findings))
        self.assertTrue(any("fresh problem" in f for f in findings))
        self.assertTrue(any("broke: x on t3" in f for f in findings))

    def test_a_clean_fix_loop_has_no_regressions(self):
        from deepcompare.progress import regressions_in
        result = {"actions": [{"status": "resolved", "action": "a"}],
                  "new_issues": [],
                  "success_by_agent": {"x": {"flips_broken": [],
                                             "flips_fixed": ["t0"]}}}
        self.assertEqual(regressions_in(result), [])

    def test_missing_directory_is_an_error_not_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [], [], [report("t0")])
            result = compare_progress(before, Path(tmp) / "nope")
            self.assertIn("error", result)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [issue("fp1", ["t0"])],
                               [action(1, ["fp1"], ["t0"])], [report("t0")])
            after = batch_dir(tmp, "after", [], [], [report("t0")])
            self.assertEqual(compare_progress(before, after),
                             compare_progress(before, after))


if __name__ == "__main__":
    unittest.main()
