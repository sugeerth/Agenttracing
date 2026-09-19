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


class TestDiagnosisShift(unittest.TestCase):
    """diagnosis_shift: did the fix move the leading cause, task by task?

    The batches are real engine output — the demo process traces
    (p01..p04, steady-v1 vs hasty-v2) compared and aggregated exactly the
    way ``deepcompare batch`` does — with the after-batch produced by
    editing traces the way a fix would: repairing an answer, resolving an
    outcome, muddying the evidence until no single cause leads.
    """

    TRACES = Path(__file__).resolve().parent.parent / "demo" / "process" / "traces"
    TASKS = ("p01_cancel_booking", "p02_book_flight",
             "p03_change_seats", "p04_policy_lookup")

    @classmethod
    def _trace(cls, task, agent):
        return json.loads(
            (cls.TRACES / f"{task}__{agent}.json").read_text(encoding="utf-8"))

    @classmethod
    def _engine_batch(cls, name, pairs):
        """A real batch output dir: compared reports plus their aggregate."""
        from deepcompare import Trajectory, compare
        from deepcompare.metrics import aggregate as build_aggregate
        directory = Path(cls.tmp) / name
        directory.mkdir(exist_ok=True)
        reports = [compare(Trajectory.from_dict(a), Trajectory.from_dict(b))
                   for a, b in pairs]
        for rep in reports:
            task = rep["task"]["id"]
            (directory / f"report_{task}.json").write_text(
                json.dumps(rep), encoding="utf-8")
        (directory / "aggregate.json").write_text(
            json.dumps(build_aggregate(reports)), encoding="utf-8")
        return directory

    @classmethod
    def setUpClass(cls):
        if not cls.TRACES.is_dir():
            raise unittest.SkipTest("demo process traces not present")
        import copy
        import shutil
        cls.tmp = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.tmp)

        base = {task: (cls._trace(task, "steady-v1"), cls._trace(task, "hasty-v2"))
                for task in cls.TASKS}

        # p01 before: steady-v1 fails although its answer matches the
        # expected answer — grader_or_label leads.  The "fix" corrects the
        # label mismatch story: the answer is now genuinely wrong, so the
        # grader hypothesis collapses and divergence leads instead.
        steady_shifted = copy.deepcopy(base["p01_cancel_booking"][0])
        wrong = "The booking could not be cancelled; no refund applies."
        steady_shifted["outcome"]["answer"] = wrong
        steady_shifted["steps"][-1]["input"] = wrong
        steady_shifted["steps"][-1]["output"] = wrong

        # Same broken answer plus an unrecovered tool error: divergence and
        # environment_error land within the lead margin — contested.
        steady_contested = copy.deepcopy(steady_shifted)
        answer = steady_contested["steps"].pop()
        steady_contested["steps"].append({
            "index": answer["index"], "type": "tool_call",
            "name": "cancel_booking",
            "input": "cancel_booking(reference='QX7T2', refund=true)",
            "output": "ERROR: booking service unavailable",
            "tokens": 40, "latency_s": 1.2, "quality": None, "note": None,
            "effect": "write", "error": True})
        answer["index"] += 1
        steady_contested["steps"].append(answer)

        # p03 after: the failing agent now passes — no failure to diagnose.
        hasty_resolved = copy.deepcopy(base["p03_change_seats"][1])
        hasty_resolved["outcome"]["success"] = True

        cls.before = cls._engine_batch(
            "before", [base[task] for task in cls.TASKS])
        cls.after = cls._engine_batch("after", [
            (steady_shifted, base["p01_cancel_booking"][1]),   # cause shifts
            base["p02_book_flight"],                           # unchanged
            (base["p03_change_seats"][0], hasty_resolved),     # resolved
            base["p04_policy_lookup"],                         # no failure
        ])
        cls.after_contested = cls._engine_batch("after_contested", [
            (steady_contested, base["p01_cancel_booking"][1]),
            base["p02_book_flight"],
        ])
        cls.result = compare_progress(cls.before, cls.after)
        cls.shift = cls.result["diagnosis_shift"]

    def entry(self, shift, task):
        matches = [t for t in shift["tasks"] if t["task"] == task]
        self.assertEqual(len(matches), 1,
                         f"expected exactly one entry for {task}: {shift}")
        return matches[0]

    def test_same_leading_kind_is_cause_unchanged(self):
        # A batch compared against itself is the one construction where
        # "cause unchanged" is guaranteed by real engine output rather
        # than by an expectation about a particular trace pair.
        result = compare_progress(self.before, self.before)
        entry = self.entry(result["diagnosis_shift"], "p01_cancel_booking")
        self.assertEqual(entry["before"], "grader_or_label")
        self.assertEqual(entry["after"], "grader_or_label")
        self.assertEqual(entry["verdict"], "cause unchanged")
        self.assertEqual(entry["agent"], "steady-v1")

    def test_contested_in_both_runs_is_stated_plainly(self):
        # p02's failing agent (hasty-v2) raises process flags its clean
        # counterpart does not, so a matching answer alone can no longer
        # put the grader in the lead: the diagnosis is honestly contested
        # — in both batches, since the fix left p02 untouched.
        entry = self.entry(self.shift, "p02_book_flight")
        self.assertIsNone(entry["before"])
        self.assertIsNone(entry["after"])
        self.assertEqual(entry["verdict"],
                         "contested in both runs — no single cause led "
                         "either time")
        self.assertEqual(entry["agent"], "hasty-v2")

    def test_a_changed_leading_kind_is_a_shift_naming_both(self):
        entry = self.entry(self.shift, "p01_cancel_booking")
        self.assertEqual(entry["before"], "grader_or_label")
        self.assertEqual(entry["after"], "divergence")
        self.assertEqual(entry["verdict"],
                         "cause shifted: grader_or_label → divergence")
        self.assertIn("progress, but not done", self.shift["note"])

    def test_a_resolved_failure_is_skipped_not_duplicated(self):
        # p03 was diagnosed before; after, both agents pass — issue
        # tracking already reports the resolution, so it is not re-listed.
        tasks = {t["task"] for t in self.shift["tasks"]}
        self.assertNotIn("p03_change_seats", tasks)
        # and a task with no single failure in either run never appears
        self.assertNotIn("p04_policy_lookup", tasks)

    def test_becoming_contested_is_stated_plainly(self):
        result = compare_progress(self.before, self.after_contested)
        entry = self.entry(result["diagnosis_shift"], "p01_cancel_booking")
        self.assertEqual(entry["before"], "grader_or_label")
        self.assertIsNone(entry["after"])
        self.assertEqual(entry["verdict"],
                         "grader_or_label led, now contested")

    def test_a_contested_diagnosis_that_settles_is_stated_plainly(self):
        result = compare_progress(self.after_contested, self.before)
        entry = self.entry(result["diagnosis_shift"], "p01_cancel_booking")
        self.assertIsNone(entry["before"])
        self.assertEqual(entry["after"], "grader_or_label")
        self.assertEqual(entry["verdict"],
                         "was contested, now grader_or_label leads")

    def test_old_outputs_without_diagnosis_degrade_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [issue("fp1", ["t0"])],
                               [action(1, ["fp1"], ["t0"])],
                               [report("t0", ok_a=False)])
            after = batch_dir(tmp, "after", [], [], [report("t0")])
            result = compare_progress(before, after)
            self.assertEqual(result["diagnosis_shift"],
                             {"tasks": [],
                              "note": "no diagnosis objects in one or both "
                                      "batches"})

    def test_diagnosis_shift_is_informational_not_gate_worthy(self):
        from deepcompare.progress import regressions_in
        # the shift (and the contested turn) must not add gate findings
        for result in (self.result,
                       compare_progress(self.before, self.after_contested)):
            for finding in regressions_in(result):
                self.assertNotIn("cause", finding)
                self.assertNotIn("contested", finding)


class TestConsolidatedShift(unittest.TestCase):
    """consolidated_shift: did the fix move the cross-run verdicts?

    The batches are real runs-command output — the demo run corpus
    (task__agent__run.json, atlas-v2 vs bolt-v3, 3 runs each) consolidated
    with ``consolidate_diagnoses`` and written the way ``_cmd_runs`` writes
    aggregate.json — with the after-batch produced by mutating raw trace
    dicts the way a fix would: resolving bolt-v3's reproducible t01
    failure, resolving its t05 flake, and handing a failing t06 run the
    exact answer a passing run got credit for so the executed grader check
    confirms.
    """

    RUN_TRACES = Path(__file__).resolve().parent.parent / "demo" / "runs" / "traces"
    TASKS = ("t01_acme_revenue", "t05_flight_duration",
             "t06_bls_unemployment", "t07_build_failure")
    SIDES = {"atlas-v2": "a", "bolt-v3": "b"}

    @classmethod
    def _raw(cls, task, agent, run):
        path = cls.RUN_TRACES / f"{task}__{agent}__{run}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def _runs_batch(cls, name, mutate=None):
        """A real runs-command batch dir: medoid reports + the consolidated
        aggregate, written exactly the way ``_cmd_runs`` writes them.

        Mutated raw dicts are round-tripped through a tempfile and
        ``Trajectory.from_json`` so they pass the same schema validation
        as recorded traces.
        """
        import copy

        from deepcompare import Trajectory, compare
        from deepcompare.consolidate import consolidate_diagnoses
        from deepcompare.metrics import aggregate as build_aggregate, task_signal
        from deepcompare.reliability import reliability
        from deepcompare.stability import medoid_pairs, stability_analysis
        from deepcompare.triage import triage

        runs_by_task = {}
        with tempfile.TemporaryDirectory() as tmp:
            for task in cls.TASKS:
                for agent, side in cls.SIDES.items():
                    for run in ("r1", "r2", "r3"):
                        raw = cls._raw(task, agent, run)
                        if mutate is not None:
                            raw = mutate(copy.deepcopy(raw), task, agent,
                                         run) or raw
                        path = Path(tmp) / f"{task}__{agent}__{run}.json"
                        path.write_text(json.dumps(raw), encoding="utf-8")
                        t = Trajectory.from_json(path)
                        t.run_id = run
                        runs_by_task.setdefault(
                            task, {"a": [], "b": []})[side].append(t)

        reports = [compare(a, b) for a, b in medoid_pairs(runs_by_task)]
        agg = build_aggregate(reports)
        stability = stability_analysis(runs_by_task)
        agg["stability"] = stability
        agg["reliability"] = reliability(runs_by_task)
        agg["task_signal"] = task_signal(reports, stability)
        agg["diagnosis_consolidated"] = consolidate_diagnoses(runs_by_task)
        agg["triage"] = triage(reports, agg)

        directory = Path(cls.tmp) / name
        directory.mkdir(exist_ok=True)
        for rep in reports:
            (directory / f"report_{rep['task']['id']}.json").write_text(
                json.dumps(rep), encoding="utf-8")
        (directory / "aggregate.json").write_text(json.dumps(agg),
                                                  encoding="utf-8")
        return directory

    @classmethod
    def setUpClass(cls):
        if not cls.RUN_TRACES.is_dir():
            raise unittest.SkipTest("demo run traces not present")
        import shutil
        cls.tmp = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.tmp)

        # the exact answer a passing t06 run got credit for
        passing = cls._raw("t06_bls_unemployment", "atlas-v2",
                           "r1")["outcome"]["answer"]

        def fix(raw, task, agent, run):
            if agent != "bolt-v3":
                return raw
            if task == "t01_acme_revenue":
                # the reproducible failure stops failing entirely
                raw["outcome"]["success"] = True
            elif task == "t05_flight_duration" and run in ("r1", "r2"):
                # the 2-of-3 flake stops failing
                raw["outcome"]["success"] = True
            elif task == "t06_bls_unemployment" and run == "r1":
                # a failing run's answer becomes near-identical to a
                # passing run's: grader_consistency confirms
                raw["outcome"]["answer"] = passing
            return raw

        cls.before = cls._runs_batch("before")
        cls.after = cls._runs_batch("after", fix)
        cls.result = compare_progress(cls.before, cls.after)
        cls.shift = cls.result["consolidated_shift"]

    def entry(self, shift, task, agent):
        matches = [e for e in shift["entries"]
                   if e["task"] == task and e["agent"] == agent]
        self.assertEqual(len(matches), 1,
                         f"expected exactly one entry for {task}/{agent}: "
                         f"{shift}")
        return matches[0]

    def test_a_resolved_reproducible_cause_is_stated(self):
        entry = self.entry(self.shift, "t01_acme_revenue", "bolt-v3")
        self.assertEqual(entry["before_status"], "reproducible")
        self.assertEqual(entry["after_status"], "no failures")
        self.assertEqual(entry["verdict"], "reproducible cause resolved")

    def test_a_resolved_flake_keeps_its_flakiness_in_the_sentence(self):
        entry = self.entry(self.shift, "t05_flight_duration", "bolt-v3")
        self.assertEqual(entry["after_status"], "no failures")
        self.assertIn("flaky failure resolved", entry["verdict"])
        # three clean runs of a 2-of-3 flake are weak evidence, and the
        # row says so instead of banking the win
        self.assertIn("2 of 3", entry["verdict"])
        self.assertIn("luck", entry["verdict"])

    def test_a_new_confirmation_is_called_out_with_its_statement(self):
        entry = self.entry(self.shift, "t06_bls_unemployment", "bolt-v3")
        self.assertEqual(entry["before_status"], "reproducible")
        self.assertEqual(entry["after_status"], "confirmed")
        self.assertTrue(entry["verdict"].startswith("now confirmed: "))
        self.assertIn("grader treated near-identical answers differently",
                      entry["verdict"])

    def test_a_lost_confirmation_is_called_out(self):
        # the same batches the other way around: the executed check that
        # confirmed the grader hypothesis no longer fires
        reverse = compare_progress(self.after, self.before)
        entry = self.entry(reverse["consolidated_shift"],
                           "t06_bls_unemployment", "bolt-v3")
        self.assertEqual(entry["before_status"], "confirmed")
        self.assertEqual(entry["verdict"],
                         "was confirmed by an executed check, "
                         "now reproducible")
        # and a failure appearing where there was none is a row too
        entry = self.entry(reverse["consolidated_shift"],
                           "t01_acme_revenue", "bolt-v3")
        self.assertEqual(entry["verdict"],
                         "no failures before, now reproducible")

    def test_unchanged_entries_produce_no_row(self):
        # t07's reproducible atlas-v2 failure is untouched by the fix, and
        # every clean (no failures -> no failures) pair stays silent too
        pairs = {(e["task"], e["agent"]) for e in self.shift["entries"]}
        self.assertNotIn(("t07_build_failure", "atlas-v2"), pairs)
        self.assertEqual(pairs, {("t01_acme_revenue", "bolt-v3"),
                                 ("t05_flight_duration", "bolt-v3"),
                                 ("t06_bls_unemployment", "bolt-v3")})

    def test_the_note_rolls_up_the_transitions(self):
        note = self.shift["note"]
        self.assertIn("3 moved", note)
        self.assertIn("2 resolved", note)
        self.assertIn("1 newly confirmed by an executed check", note)

    def test_batches_without_consolidation_degrade_with_the_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = batch_dir(tmp, "before", [issue("fp1", ["t0"])],
                               [action(1, ["fp1"], ["t0"])],
                               [report("t0", ok_a=False)])
            after = batch_dir(tmp, "after", [], [], [report("t0")])
            for pair in ((before, after), (self.before, after)):
                result = compare_progress(*pair)
                self.assertEqual(result["consolidated_shift"],
                                 {"entries": [],
                                  "note": "no cross-run consolidation in "
                                          "one or both batches"})

    def test_consolidated_shift_is_informational_not_gate_worthy(self):
        from deepcompare.progress import regressions_in
        for result in (self.result, compare_progress(self.after, self.before)):
            findings = regressions_in(result)
            # the gate reads nothing from the section: stripping it
            # changes no finding, and no finding speaks its vocabulary
            stripped = {k: v for k, v in result.items()
                        if k != "consolidated_shift"}
            self.assertEqual(findings, regressions_in(stripped))
            for finding in findings:
                self.assertNotIn("confirmed", finding)
                self.assertNotIn("consolidat", finding)
                self.assertNotIn("reproducible", finding)


if __name__ == "__main__":
    unittest.main()
