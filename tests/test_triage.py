"""Tests for triage: the ranked "what do I fix first" list (v23).

The properties worth pinning are the ones that make a ranked list either
trustworthy or worthless.  A ranking that moves between runs cannot be cited;
a ranking where one anecdote outranks a recurring failure teaches people to
ignore it; three rows describing one problem are the review fatigue this
module exists to remove; a pathology on a passing run that nobody surfaces is
a bug that ships; and an invented impact number is worse than no number at
all, because it will be repeated in a planning meeting.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.metrics import aggregate
from deepcompare.triage import render_triage_text, triage


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def step(index, stype, name, text="text", quality=None, output=None,
         error=None, effect=None, tokens=100):
    return {"index": index, "type": stype, "name": name,
            "input": f"{name}({text})",
            "output": output if output is not None else f"{name} out {text}",
            "tokens": tokens, "latency_s": 1.0, "quality": quality,
            "note": None, "error": error, "effect": effect}


def traj(agent, task, success, steps, tokens=400, cost=0.02, answer="answer",
         tools=None, budget=None):
    payload = {
        "schema_version": 1, "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "find the revenue in the annual report",
                 "expected": "gold"},
        "outcome": {"success": success, "answer": answer,
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": tokens // 2, "output_tokens": tokens // 2,
                   "cost_usd": cost, "latency_s": float(len(steps))},
        "steps": steps,
    }
    if tools is not None:
        payload["tools"] = tools
    if budget is not None:
        payload["budget"] = budget
    return Trajectory.from_json(payload)


def clean_run(agent, task, success=True):
    """A short, unremarkable run: one search, one good source, one answer."""
    return traj(agent, task, success, [
        step(0, "plan", "plan"),
        step(1, "search", "web_search"),
        step(2, "retrieve", "select_result", "official filing"),
        step(3, "answer", "final"),
    ])


def weak_source_run(agent, task, success=False):
    """Same shape, but selects a source annotated bad — a retrieval divergence."""
    return traj(agent, task, success, [
        step(0, "plan", "plan"),
        step(1, "search", "web_search"),
        step(2, "retrieve", "select_result", "random blog", quality="bad"),
        step(3, "reason", "reason", "reconcile"),
        step(4, "answer", "final"),
    ], tokens=900)


def looping_run(agent, task, success=True):
    """A run that calls the same tool three times with the same arguments.

    Passing or failing is the caller's choice, which is the whole point: the
    same process is either the report's most under-served finding or a
    footnote to a failure already explained.
    """
    return traj(agent, task, success, [
        step(0, "plan", "plan"),
        step(1, "tool_call", "get_policy", "id=7", output="policy: none"),
        step(2, "tool_call", "get_policy", "id=7", output="policy: none"),
        step(3, "tool_call", "get_policy", "id=7", output="policy: none"),
        step(4, "answer", "final"),
    ], tokens=800)


def corpus(pairs):
    """Build comparison reports from [(trajectory_a, trajectory_b), ...]."""
    return [compare(a, b) for a, b in pairs]


def fake_report(task, cost=0.02, tokens=1000):
    """A report shell with no divergences and no process block.

    Used where the point of the test is the ranking rule, not the analysis
    that fed it: the aggregate is then handwritten, so exactly one thing is
    under test.
    """
    side = {
        "agent": {"name": "A"}, "outcome": {"success": True, "answer": "x"},
        "totals": {"input_tokens": tokens // 2, "output_tokens": tokens // 2,
                   "cost_usd": cost, "latency_s": 1.0},
        "steps": [],
    }
    other = json.loads(json.dumps(side))
    other["agent"]["name"] = "B"
    return {"task": {"id": task, "prompt": "p"}, "a": side, "b": other,
            "divergences": [], "attribution": {"failed_agent": None},
            "process": {}}


def issue(iid, kind, tasks, failures=0, tokens=0, agents=(), occurrences=None,
          suppressed=False, latency=0.0):
    tasks = list(tasks)
    return {
        "id": iid, "kind": kind, "title": f"{kind} problem",
        "tasks": tasks, "agents": list(agents),
        "failures_caused": failures, "extra_tokens": tokens,
        "extra_steps": 0, "extra_latency_s": latency,
        "occurrence_count": occurrences if occurrences is not None else len(tasks),
        "occurrences": [{"task": t, "rank": 1, "a_index": 1, "b_index": 1,
                         "caused_failure": bool(failures), "extra_steps": 0,
                         "extra_tokens": tokens, "extra_latency_s": latency,
                         "summary": f"{kind} on {t}"} for t in tasks],
        "severity": "critical" if failures else "minor",
        "suppressed": suppressed, "recurring": len(tasks) > 1,
        "summary": f"{kind} problem — seen on {len(tasks)} task(s).",
        "example": None,
    }


def handmade(issues=(), recommendations=(), regressions=(), **extra):
    payload = {
        "tasks": extra.pop("tasks", 4),
        "agents": {"a": "A", "b": "B"},
        "regressions": list(regressions),
        "recommendations": list(recommendations),
        "issues": {"issues": list(issues), "active": len(list(issues)),
                   "suppressed": 0, "total_divergences": 0,
                   "counts": {}, "narrative": ""},
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    """A ranking that moves between identical runs cannot be cited."""

    @classmethod
    def setUpClass(cls):
        cls.reports = corpus([
            (clean_run("steady", "t1"), weak_source_run("hasty", "t1")),
            (clean_run("steady", "t2"), weak_source_run("hasty", "t2")),
            (clean_run("steady", "t3"), looping_run("hasty", "t3", success=True)),
        ])
        cls.aggregate = aggregate(cls.reports)

    def test_two_runs_produce_identical_output(self):
        first = triage(self.reports, self.aggregate)
        second = triage(self.reports, self.aggregate)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_result_is_json_serializable(self):
        json.dumps(triage(self.reports, self.aggregate))

    def test_ranks_are_dense_and_ordered(self):
        actions = triage(self.reports, self.aggregate)["actions"]
        self.assertEqual([a["rank"] for a in actions],
                         list(range(1, len(actions) + 1)))
        scores = [a["score"] for a in actions]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_aggregate_carries_the_triage_key(self):
        self.assertIn("triage", self.aggregate)
        self.assertTrue(self.aggregate["triage"]["actions"])

    def test_empty_batch_triages_to_nothing_without_crashing(self):
        empty = aggregate([])
        self.assertEqual(empty["triage"]["actions"], [])
        self.assertIn("Nothing to triage", empty["triage"]["narrative"])


class TestSampleSizeDamping(unittest.TestCase):
    """One occurrence is an anecdote, and the report has to say so."""

    def setUp(self):
        self.reports = [fake_report(f"t{i}") for i in range(1, 5)]

    def test_single_occurrence_cannot_outrank_a_well_evidenced_finding(self):
        agg = handmade(issues=[
            issue("once", "tool_execution", ["t1"], failures=1, tokens=9000,
                  agents=["A"]),
            issue("often", "retrieval", ["t1", "t2", "t3"], failures=1,
                  tokens=100, agents=["B"]),
        ])
        actions = triage(self.reports, agg)["actions"]
        self.assertEqual(actions[0]["evidence"]["task_count"], 3)
        self.assertGreater(actions[0]["score"], actions[1]["score"])

    def test_the_damping_is_stated_not_silent(self):
        agg = handmade(issues=[issue("once", "retrieval", ["t1"], failures=1,
                                     agents=["A"])])
        action = triage(self.reports, agg)["actions"][0]
        basis = " ".join(action["rank_basis"])
        self.assertIn("0.6", basis)
        self.assertIn("single occurrence", basis)
        self.assertEqual(action["confidence"]["level"], "low")

    def test_a_single_occurrence_never_reaches_high_confidence(self):
        agg = handmade(issues=[issue("once", "retrieval", ["t1"], failures=5,
                                     tokens=99999, agents=["A"])])
        action = triage(self.reports, agg)["actions"][0]
        self.assertEqual(action["confidence"]["level"], "low")
        self.assertTrue(any("one-off" in reason
                            for reason in action["confidence"]["basis"]))

    def test_confidence_carries_its_denominator_and_interval(self):
        agg = handmade(issues=[issue("often", "retrieval", ["t1", "t2", "t3"],
                                     failures=1, agents=["A"])])
        confidence = triage(self.reports, agg)["actions"][0]["confidence"]
        self.assertEqual((confidence["occurrences"], confidence["of"]), (3, 4))
        low, high = confidence["rate_interval"]
        self.assertLess(low, 0.75)
        self.assertGreater(high, 0.75)
        self.assertEqual(confidence["level"], "high")

    def test_a_failure_outranks_a_costlier_non_failure(self):
        agg = handmade(issues=[
            issue("cost", "retrieval", ["t1", "t2", "t3"], tokens=50000,
                  agents=["B"]),
            issue("fatal", "tool_execution", ["t1"], failures=1, tokens=10,
                  agents=["A"]),
        ])
        actions = triage(self.reports, agg)["actions"]
        self.assertEqual(actions[0]["severity_class"], "failure")


class TestMerging(unittest.TestCase):
    """The same problem arriving three ways must become one row."""

    def setUp(self):
        self.reports = [fake_report(f"t{i}") for i in range(1, 5)]

    def test_issue_and_recommendation_merge_into_one_action(self):
        agg = handmade(
            issues=[issue("i1", "retrieval", ["t1", "t2"], failures=1,
                          tokens=500, agents=["B"])],
            recommendations=[{
                "agent": "B", "category": "retrieval", "severity": "critical",
                "finding": "B failed 1 of 4 task(s) (t1)",
                "evidence_tasks": ["t1"], "suggested_prompt": "prefer primary",
                "expected_gain": "up to +25pt success",
            }],
        )
        result = triage(self.reports, agg)
        self.assertEqual(len(result["actions"]), 1)
        action = result["actions"][0]
        self.assertEqual(action["sources"], ["issues", "recommendations"])
        self.assertEqual(action["merged_from"], 2)
        self.assertEqual(action["fix_hint"], "prefer primary")

    def test_merged_counts_use_max_not_sum(self):
        # Both blocks describe the same failing run; adding them would promise
        # two avoidable failures where only one exists.
        agg = handmade(
            issues=[issue("i1", "retrieval", ["t1"], failures=1, agents=["B"])],
            recommendations=[{
                "agent": "B", "category": "retrieval", "severity": "critical",
                "finding": "B failed t1", "evidence_tasks": ["t1"],
                "suggested_prompt": "p", "expected_gain": "g",
            }],
        )
        action = triage(self.reports, agg)["actions"][0]
        self.assertEqual(action["impact"]["failures_avoided"]["value"], 1)

    def test_findings_about_different_agents_do_not_merge(self):
        agg = handmade(issues=[
            issue("i1", "retrieval", ["t1"], failures=1, agents=["A"]),
            issue("i2", "retrieval", ["t1"], failures=1, agents=["B"]),
        ])
        result = triage(self.reports, agg)
        self.assertEqual(len(result["actions"]), 2)
        self.assertNotEqual(result["actions"][0]["agents"],
                            result["actions"][1]["agents"])

    def test_an_unattributed_finding_needs_a_shared_task_to_merge(self):
        agg = handmade(issues=[
            issue("named", "retrieval", ["t1"], failures=1, agents=["B"]),
            issue("anon", "retrieval", ["t4"], tokens=100),
        ])
        self.assertEqual(len(triage(self.reports, agg)["actions"]), 2)

    def test_process_issue_and_recommendation_merge_across_three_sources(self):
        reports = corpus([
            (clean_run("steady", "t1"),
             traj("hasty", "t1", False, [
                 step(0, "plan", "plan"),
                 step(1, "tool_call", "book", "id=1", output="error: no seat"),
                 step(2, "tool_call", "book", "id=1", output="error: no seat"),
                 step(3, "answer", "final"),
             ], tokens=900)),
        ])
        agg = aggregate(reports)
        result = triage(reports, agg)
        multi = [a for a in result["actions"] if a["merged_from"] > 1]
        self.assertTrue(multi, "nothing merged at all")
        self.assertTrue(
            any(len(a["sources"]) >= 2 for a in multi),
            "merged rows all came from a single source",
        )
        self.assertEqual(result["counts"]["merged"],
                         sum(a["merged_from"] - 1 for a in result["actions"]))

    def test_no_two_actions_share_the_same_imperative(self):
        reports = corpus([
            (clean_run("steady", "t1"), weak_source_run("hasty", "t1")),
            (clean_run("steady", "t2"), weak_source_run("hasty", "t2", True)),
            (clean_run("steady", "t3"), looping_run("hasty", "t3")),
        ])
        actions = triage(reports, aggregate(reports))["actions"]
        texts = [a["action"] for a in actions]
        self.assertEqual(len(texts), len(set(texts)))


class TestPathologicalPass(unittest.TestCase):
    """A bad process behind a green tick is the finding nothing else reports."""

    @classmethod
    def setUpClass(cls):
        # "hasty" loops on t1 and passes; "steady" loops on t2 and fails.
        cls.reports = corpus([
            (clean_run("steady", "t1"), looping_run("hasty", "t1", success=True)),
            (looping_run("steady", "t2", success=False), clean_run("hasty", "t2")),
        ])
        cls.result = triage(cls.reports, aggregate(cls.reports))

    def _loops(self, agent):
        for action in self.result["actions"]:
            if action["category"] == "efficiency" and agent in action["agents"]:
                return action
        raise AssertionError(f"no loop action for {agent}: "
                             f"{[a['action'] for a in self.result['actions']]}")

    def test_the_passing_pathology_is_surfaced(self):
        action = self._loops("hasty")
        self.assertEqual(action["severity_class"], "passing_pathology")
        self.assertEqual(action["on_passing_runs"], ["t1"])
        self.assertIn("process", action["sources"])

    def test_it_outranks_the_same_pathology_on_a_failing_run(self):
        self.assertLess(self._loops("hasty")["rank"],
                        self._loops("steady")["rank"])
        self.assertEqual(self._loops("steady")["severity_class"],
                         "failing_pathology")

    def test_the_narrative_says_the_outcome_hides_it(self):
        self.assertIn("passed", self.result["narrative"])

    def test_the_terminal_rendering_marks_it(self):
        text = "\n".join(render_triage_text(self.result, limit=10))
        self.assertIn("PASSED", text)
        self.assertIn("Triage", text)

    def test_evidence_names_the_steps_it_rests_on(self):
        evidence = self._loops("hasty")["evidence"]
        self.assertTrue(evidence["details"])
        self.assertTrue(evidence["steps"])
        self.assertTrue(all("index" in s for s in evidence["steps"]))


class TestImpact(unittest.TestCase):
    """No number is better than a plausible one."""

    def setUp(self):
        self.reports = [fake_report(f"t{i}") for i in range(1, 5)]

    def test_unestimable_impact_is_none_not_zero(self):
        # A recommendation states its cost only in prose, so there is no
        # token figure to report — and none may be invented.
        agg = handmade(recommendations=[{
            "agent": "B", "category": "retrieval", "severity": "critical",
            "finding": "B failed t1", "evidence_tasks": ["t1"],
            "suggested_prompt": "p", "expected_gain": "some tokens",
        }])
        impact = triage(self.reports, agg)["actions"][0]["impact"]
        self.assertIsNone(impact["tokens_saved"])
        self.assertIsNone(impact["latency_saved_s"])
        self.assertIsNone(impact["cost_usd_saved"])
        self.assertTrue(any("not estimable" in reason
                            for reason in impact["unestimable"]))

    def test_every_impact_number_states_its_basis(self):
        agg = handmade(issues=[issue("i1", "retrieval", ["t1", "t2"],
                                     failures=2, tokens=500, latency=3.5,
                                     agents=["B"])])
        impact = triage(self.reports, agg)["actions"][0]["impact"]
        for key in ("failures_avoided", "tokens_saved", "latency_saved_s",
                    "cost_usd_saved"):
            self.assertIsNotNone(impact[key], key)
            self.assertTrue(impact[key]["basis"].strip(), key)
        self.assertEqual(impact["failures_avoided"]["of_tasks"], 4)
        self.assertIn("realised price", impact["cost_usd_saved"]["basis"])

    def test_no_dollar_figure_when_the_corpus_reports_no_cost(self):
        free = [fake_report(f"t{i}", cost=0.0) for i in range(1, 5)]
        agg = handmade(issues=[issue("i1", "retrieval", ["t1", "t2"],
                                     tokens=500, agents=["B"])])
        impact = triage(free, agg)["actions"][0]["impact"]
        self.assertIsNotNone(impact["tokens_saved"])
        self.assertIsNone(impact["cost_usd_saved"])
        self.assertTrue(any("no dollar estimate" in reason
                            for reason in impact["unestimable"]))

    def test_a_measured_zero_is_reported_as_measured_not_unknown(self):
        agg = handmade(issues=[issue("i1", "retrieval", ["t1", "t2"],
                                     tokens=500, agents=["B"])])
        impact = triage(self.reports, agg)["actions"][0]["impact"]
        self.assertIsNone(impact["failures_avoided"])
        self.assertTrue(any("measured at zero" in reason
                            for reason in impact["unestimable"]))

    def test_effort_is_labelled_a_heuristic(self):
        agg = handmade(issues=[issue("i1", "retrieval", ["t1"], failures=1,
                                     agents=["B"])])
        effort = triage(self.reports, agg)["actions"][0]["effort"]
        self.assertTrue(effort["heuristic"])
        self.assertIn("Heuristic", effort["note"])
        self.assertEqual(effort["class"], "prompt")


class TestNotActionable(unittest.TestCase):
    """Everything left out is accounted for, with a reason."""

    def setUp(self):
        self.reports = [fake_report(f"t{i}") for i in range(1, 5)]

    def test_every_exclusion_carries_a_reason_and_detail(self):
        reports = corpus([
            (clean_run("steady", "t1"), weak_source_run("hasty", "t1")),
            (clean_run("steady", "t2"), weak_source_run("hasty", "t2", True)),
        ])
        result = triage(reports, aggregate(reports))
        self.assertTrue(result["not_actionable"])
        for entry in result["not_actionable"]:
            for key in ("finding", "source", "reason", "detail"):
                self.assertIn(key, entry)
                self.assertTrue(str(entry[key]).strip(),
                                f"empty {key} in {entry}")

    def test_a_suppressed_issue_is_listed_not_dropped(self):
        agg = handmade(issues=[issue("i1", "retrieval", ["t1"], failures=1,
                                     agents=["B"], suppressed=True)])
        result = triage(self.reports, agg)
        self.assertEqual(result["actions"], [])
        self.assertEqual(len(result["not_actionable"]), 1)
        self.assertEqual(result["not_actionable"][0]["reason"], "suppressed")

    def test_a_finding_with_no_measurable_cost_is_excluded_loudly(self):
        agg = handmade(issues=[issue("i1", "reasoning", ["t1"])])
        result = triage(self.reports, agg)
        self.assertEqual(result["actions"], [])
        excluded = result["not_actionable"][0]
        self.assertEqual(excluded["reason"], "no measurable cost")
        self.assertIn("0 extra tokens", excluded["detail"])

    def test_an_attribute_lift_whose_interval_includes_zero_is_refused(self):
        agg = handmade(attributes={
            "runs": 8, "failures": 3,
            "attributes": [{
                "attribute": "long_trajectory",
                "phrasing": "the run took more than six steps",
                "with": {"runs": 4, "failures": 2, "failure_rate": 0.5,
                         "ci": [0.15, 0.85]},
                "without": {"runs": 4, "failures": 1, "failure_rate": 0.25,
                            "ci": [0.05, 0.7]},
                "lift": 0.25, "measurable": True,
                "interval": {"observed": 0.25, "low": -0.25, "high": 0.75,
                             "significant": False, "samples": 2000},
            }],
        })
        result = triage(self.reports, agg)
        self.assertEqual(result["actions"], [])
        entry = result["not_actionable"][0]
        self.assertIn("includes zero", entry["detail"])

    def test_a_significant_lift_is_ranked_but_only_as_a_signal(self):
        agg = handmade(attributes={
            "runs": 8, "failures": 4,
            "attributes": [{
                "attribute": "poor_quality_step",
                "phrasing": "the run contains a step annotated bad",
                "with": {"runs": 4, "failures": 4, "failure_rate": 1.0,
                         "ci": [0.5, 1.0]},
                "without": {"runs": 4, "failures": 0, "failure_rate": 0.0,
                            "ci": [0.0, 0.49]},
                "lift": 1.0, "measurable": True,
                "interval": {"observed": 1.0, "low": 0.5, "high": 1.0,
                             "significant": True, "samples": 2000},
            }],
        })
        action = triage(self.reports, agg)["actions"][0]
        self.assertEqual(action["severity_class"], "signal")
        self.assertEqual(action["confidence"]["level"], "medium")
        self.assertEqual(action["confidence"]["unit"], "run")


class TestReliabilityRefusal(unittest.TestCase):
    """When the sample cannot support a comparison, say so — do not rank it."""

    def setUp(self):
        self.reports = [fake_report(f"t{i}") for i in range(1, 5)]
        self.reliability = {"per_agent": {"a": {
            "agent": "A",
            "runs_advisory": {
                "tier": "insufficient", "n_min": 3,
                "message": "3 run(s) per task at the thinnest task.",
                "does_not_support": ["any claim that one agent is more "
                                     "reliable than another"],
            },
        }}}

    def test_a_cross_agent_claim_is_capped_at_low_confidence(self):
        agg = handmade(
            issues=[issue("i1", "retrieval", ["t1", "t2", "t3"], failures=2,
                          tokens=900, agents=["B"])],
            reliability=self.reliability,
        )
        ungated = triage(self.reports, handmade(issues=[
            issue("i1", "retrieval", ["t1", "t2", "t3"], failures=2,
                  tokens=900, agents=["B"])]))
        gated = triage(self.reports, agg)
        self.assertEqual(ungated["actions"][0]["confidence"]["level"], "high")
        self.assertEqual(gated["actions"][0]["confidence"]["level"], "low")
        self.assertLess(gated["actions"][0]["score"],
                        ungated["actions"][0]["score"])

    def test_the_refusal_is_quoted_not_paraphrased(self):
        agg = handmade(issues=[issue("i1", "retrieval", ["t1", "t2"],
                                     failures=1, agents=["B"])],
                       reliability=self.reliability)
        result = triage(self.reports, agg)
        self.assertIsNotNone(result["reliability_gate"])
        text = " ".join(result["actions"][0]["rank_basis"]) + \
            " ".join(result["actions"][0]["confidence"]["basis"])
        self.assertIn("3 run(s) per task", text)
        self.assertIn("more reliable than another", text)
        self.assertIn("cannot support", result["narrative"] + text)

    def test_a_single_trace_pathology_is_not_damped_by_run_count(self):
        reports = corpus([
            (clean_run("steady", "t1"), looping_run("hasty", "t1", True)),
        ])
        agg = aggregate(reports)
        without = triage(reports, agg)
        agg_gated = dict(agg, reliability=self.reliability)
        with_gate = triage(reports, agg_gated)

        def loop_score(result):
            for action in result["actions"]:
                if action["category"] == "efficiency":
                    return action["score"]
            raise AssertionError("no loop action")

        self.assertEqual(loop_score(without), loop_score(with_gate))


class TestSchemeAndNarrative(unittest.TestCase):
    """The rule ships with the ranking; a rule nobody can read is not a rule."""

    def setUp(self):
        self.reports = [fake_report(f"t{i}") for i in range(1, 5)]
        self.agg = handmade(issues=[
            issue("i1", "retrieval", ["t1", "t2"], failures=1, tokens=700,
                  agents=["B"]),
            issue("i2", "tool_execution", ["t3"], tokens=200, agents=["A"]),
        ])

    def test_the_scheme_is_published_with_the_result(self):
        scheme = triage(self.reports, self.agg)["scheme"]
        self.assertIn("formula", scheme)
        self.assertEqual(set(scheme["base"]),
                         {"failure", "passing_pathology", "failing_pathology",
                          "cost", "signal"})
        self.assertTrue(scheme["merge_rule"])
        self.assertFalse(scheme["reliability"]["active"])

    def test_the_narrative_names_the_first_action_and_the_exclusions(self):
        result = triage(self.reports, self.agg)
        self.assertIn(result["actions"][0]["action"], result["narrative"])
        self.assertIn("not actionable", result["narrative"])

    def test_counts_reconcile_with_the_lists(self):
        result = triage(self.reports, self.agg)
        self.assertEqual(result["counts"]["actions"], len(result["actions"]))
        self.assertEqual(result["counts"]["excluded"],
                         len(result["not_actionable"]))

    def test_every_action_says_which_analysis_produced_it(self):
        for action in triage(self.reports, self.agg)["actions"]:
            self.assertTrue(action["source"])
            self.assertTrue(action["sources"])
            self.assertTrue(action["finding_ids"])

    def test_rendering_an_empty_result_does_not_crash(self):
        lines = render_triage_text(triage([], {}))
        self.assertTrue(any("Triage" in line for line in lines))




class TestEfficiencySource(unittest.TestCase):
    """Serving-cost opportunities join the ranking without corrupting it."""

    def aggregate_with_efficiency(self):
        return {
            "efficiency": {"per_agent": {"b": {
                "agent": "hasty-v2",
                "opportunities": [{
                    "kind": "prompt_cache", "rank": 1, "basis": "estimated",
                    "action": "Add a stable-prefix prompt cache",
                    "evidence": {"occurrences": 4,
                                 "tasks": ["t0", "t1", "t2", "t3"]},
                    "saving": {"tokens": 1783, "latency_s": None,
                               "cost_usd": 0.0053},
                }],
            }}},
        }

    def reports(self):
        return [fake_report(f"t{i}") for i in range(4)]

    def find(self, actions):
        return [a for a in actions if "efficiency" in a["sources"]]

    def test_efficiency_opportunities_become_ranked_actions(self):
        result = triage(self.reports(), self.aggregate_with_efficiency())
        found = self.find(result["actions"])
        self.assertEqual(len(found), 1)
        self.assertIn("stable prompt prefix", found[0]["action"])
        self.assertEqual(found[0]["severity_class"], "cost")

    def test_a_ceiling_saving_never_outranks_a_caused_failure(self):
        aggregate = self.aggregate_with_efficiency()
        aggregate["issues"] = {"issues": [{
            "id": "fp1", "kind": "retrieval", "title": "bad source",
            "tasks": ["t0", "t1"], "occurrence_count": 2,
            "occurrences": [{"task": "t0"}, {"task": "t1"}],
            "failures_caused": 2, "extra_tokens": 100,
            "severity": "critical", "recurring": True, "suppressed": False,
            "agents": ["hasty-v2"], "summary": "s",
        }]}
        result = triage(self.reports(), aggregate)
        failure_rank = min(a["rank"] for a in result["actions"]
                           if a["severity_class"] == "failure")
        cost_rank = min(a["rank"] for a in self.find(result["actions"]))
        self.assertLess(failure_rank, cost_rank)

    def test_the_ceiling_caveat_survives_into_the_action(self):
        result = triage(self.reports(), self.aggregate_with_efficiency())
        caveats = " ".join(self.find(result["actions"])[0]["evidence"]["caveats"])
        self.assertIn("ceiling, not a forecast", caveats)

    def test_efficiency_is_exempt_from_the_reliability_gate(self):
        # A repeated identical call is visible in one run; no second agent or
        # repeat count bears on whether it happened.
        aggregate = self.aggregate_with_efficiency()
        aggregate["reliability"] = {"per_agent": {"a": {
            "agent": "x",
            "runs_advisory": {"tier": "insufficient", "n_min": 1,
                              "message": "one run supports nothing",
                              "does_not_support": ["any comparison"]},
        }}}
        result = triage(self.reports(), aggregate)
        action = self.find(result["actions"])[0]
        self.assertIsNone(action["confidence"].get("capped_by"))


class TestDiagnosisSource(unittest.TestCase):
    """Adjudicated diagnoses redirect effort instead of adding fix work."""

    ROOT = Path(__file__).resolve().parent.parent

    @classmethod
    def setUpClass(cls):
        traces = cls.ROOT / "demo" / "process" / "traces"
        cls.reports = []
        for task in ("p01_cancel_booking", "p02_book_flight",
                     "p03_change_seats", "p04_policy_lookup"):
            a = Trajectory.from_json(str(traces / f"{task}__steady-v1.json"))
            b = Trajectory.from_json(str(traces / f"{task}__hasty-v2.json"))
            cls.reports.append(compare(a, b))
        cls.result = triage(cls.reports, aggregate(cls.reports))

    def diagnosis_actions(self):
        return [a for a in self.result["actions"]
                if "diagnosis" in a["sources"]]

    def test_grader_suspect_becomes_a_ranked_action(self):
        actions = self.diagnosis_actions()
        self.assertTrue(actions)
        joined = " ".join(a["action"] for a in actions)
        self.assertIn("Re-grade", joined)
        self.assertIn("grader or label first, not the agent", joined)

    def test_regrade_action_names_the_task_and_agent(self):
        regrade = [a for a in self.diagnosis_actions()
                   if "p01_cancel_booking" in a["action"]]
        self.assertEqual(len(regrade), 1)
        self.assertIn("steady-v1", regrade[0]["action"])

    def test_verification_is_a_human_check_not_a_rerun(self):
        for action in self.diagnosis_actions():
            how = action["verification"]["how"]
            self.assertIn("human verdict settles this without a re-run", how)
            self.assertEqual(action["verification"]["checks"], [])
            self.assertIn("do not spend engineering time on the agent first",
                          action["verification"]["caveat"])

    def test_details_quote_score_and_margin(self):
        detail = " ".join(
            d for a in self.diagnosis_actions()
            for d in a["evidence"]["details"])
        self.assertIn("score", detail)
        self.assertIn("margin", detail)

    def test_reports_without_diagnosis_key_are_fine(self):
        stripped = json.loads(json.dumps(self.reports))
        for report in stripped:
            report.pop("diagnosis", None)
        result = triage(stripped, {})
        self.assertFalse([a for a in result["actions"]
                          if "diagnosis" in a["sources"]])


class TestConsolidatedSource(unittest.TestCase):
    """Cross-run consolidated verdicts join triage and supersede the
    per-pair human-check actions they settle."""

    ROOT = Path(__file__).resolve().parent.parent

    @classmethod
    def setUpClass(cls):
        import glob as _glob
        import os
        import tempfile
        from deepcompare.consolidate import consolidate_diagnoses
        from deepcompare.stability import medoid_pairs
        data = []
        for f in sorted(_glob.glob(str(cls.ROOT / "demo/runs/traces/t01*.json"))):
            data.append(json.loads(Path(f).read_text()))
        passing = next(d for d in data if "atlas" in d["agent"]["name"])
        failing = next(d for d in data if "bolt" in d["agent"]["name"]
                       and not d["outcome"]["success"])
        failing["outcome"]["answer"] = passing["outcome"]["answer"]
        failing["steps"][-1]["output"] = passing["outcome"]["answer"]
        rbt = {"t01_acme_revenue": {"a": [], "b": []}}
        with tempfile.TemporaryDirectory() as tmp:
            for i, d in enumerate(data):
                path = os.path.join(tmp, f"{i}.json")
                Path(path).write_text(json.dumps(d))
                t = Trajectory.from_json(path)
                side = "a" if "atlas" in t.agent.name else "b"
                rbt["t01_acme_revenue"][side].append(t)
        reports = [compare(a, b) for a, b in medoid_pairs(rbt)]
        agg = aggregate(reports)
        agg["diagnosis_consolidated"] = consolidate_diagnoses(rbt)
        cls.result = triage(reports, agg)

    def test_confirmed_cause_becomes_an_action(self):
        confirmed = [a for a in self.result["actions"]
                     if "confirmed cause" in a["action"]]
        self.assertEqual(len(confirmed), 1)
        self.assertIn("executed check against the corpus", confirmed[0]["action"])

    def test_confirmed_verification_needs_no_further_check(self):
        confirmed = next(a for a in self.result["actions"]
                         if "confirmed cause" in a["action"])
        self.assertIn("nothing further to check",
                      confirmed["verification"]["how"])
        self.assertIn("resets it", confirmed["verification"]["caveat"])

    def test_human_check_action_is_superseded_loudly(self):
        superseded = [n for n in self.result["not_actionable"]
                      if n.get("reason") == "superseded by executed check"]
        self.assertEqual(len(superseded), 1)
        self.assertIn("grader-or-label", superseded[0]["finding"])
        regrade = [a for a in self.result["actions"]
                   if "Re-grade" in a["action"]]
        self.assertEqual(regrade, [])


if __name__ == "__main__":
    unittest.main()
