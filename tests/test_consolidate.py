"""Tests for cross-run consolidation (deepcompare.consolidate).

Three properties are pinned here, because each is a way a cross-run story
can lie:

- the demo corpus facts: which causes reproduce, which failure is a flake,
  and that no executed check pretends to settle what the corpus cannot;
- executed-check semantics: "confirmed"/"refuted" must come from a check
  run against the corpus (grader consistency, environment reproduction,
  harness flake rate), never from a score;
- consolidation honesty: n=1 stays labelled n=1, and disagreeing per-run
  diagnoses become "unstable" rather than a majority vote.

Demo-corpus assertions load the real traces; executed-check assertions
mutate a trace's JSON, write it to a tempfile and reload it through
Trajectory.from_json so the mutated run passes the same schema validation
as a recorded one.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare.consolidate import (
    ANSWER_MATCH_THRESHOLD,
    consolidate_diagnoses,
)
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent
RUN_TRACES = ROOT / "demo" / "runs" / "traces"

AGENT_SIDES = {"atlas-v2": "a", "bolt-v3": "b"}


def _load_raw(task: str, agent: str, run: str) -> dict:
    path = RUN_TRACES / f"{task}__{agent}__{run}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_demo_corpus() -> dict:
    """runs_by_task for the full demo corpus, grouped by filename pattern."""
    runs_by_task: dict = {}
    for path in sorted(RUN_TRACES.glob("*.json")):
        task, agent, _run = path.stem.split("__")
        side = AGENT_SIDES[agent]
        runs_by_task.setdefault(task, {"a": [], "b": []})[side].append(
            Trajectory.from_json(path))
    return runs_by_task


def single_task_corpus(task: str, mutate=None) -> dict:
    """One task's six runs, with an optional raw-dict mutation applied.

    ``mutate(raw, agent, run_id)`` may edit the dict in place or return a
    replacement.  Mutated dicts are round-tripped through a tempfile so
    they are loaded exactly the way recorded traces are.
    """
    sides = {"a": [], "b": []}
    with tempfile.TemporaryDirectory() as tmp:
        for agent, side in AGENT_SIDES.items():
            for run_id in ("r1", "r2", "r3"):
                raw = _load_raw(task, agent, run_id)
                if mutate is not None:
                    raw = mutate(copy.deepcopy(raw), agent, run_id) or raw
                path = Path(tmp) / f"{task}__{agent}__{run_id}.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                sides[side].append(Trajectory.from_json(path))
    return {task: sides}


def entry_for(result: dict, task: str, agent: str) -> dict:
    matches = [e for e in result["per_task_agent"]
               if e["task"] == task and e["agent"] == agent]
    assert len(matches) == 1, f"expected one entry for {task}/{agent}"
    return matches[0]


class TestDemoCorpus(unittest.TestCase):
    """Facts of the shipped 8-task x 2-agent x 3-run corpus."""

    @classmethod
    def setUpClass(cls):
        cls.result = consolidate_diagnoses(load_demo_corpus())

    def test_summary_counts(self):
        self.assertEqual(self.result["summary"], {
            "tasks": 8,
            "entries_with_failures": 4,
            "confirmed_by_checks": 0,
            "reproducible_causes": 4,
            "unstable_diagnoses": 0,
            "flaky_failures": 1,
        })

    def test_reproducible_causes_and_their_kinds(self):
        expected = {
            ("t01_acme_revenue", "bolt-v3"): "divergence",
            ("t05_flight_duration", "bolt-v3"): "divergence",
            ("t06_bls_unemployment", "bolt-v3"): "divergence",
            ("t07_build_failure", "atlas-v2"): "wrong_fact_propagation",
        }
        for (task, agent), kind in expected.items():
            entry = entry_for(self.result, task, agent)
            verdict = entry["consolidated"]
            self.assertEqual(verdict["status"], "reproducible",
                             f"{task}/{agent}")
            self.assertEqual(verdict["kind"], kind, f"{task}/{agent}")
            # a "reproducible" cause must actually lead every diagnosed run
            self.assertEqual(entry["leading_kinds"],
                             {kind: entry["diagnosed_runs"]})
        others = [e for e in self.result["per_task_agent"]
                  if (e["task"], e["agent"]) not in expected]
        self.assertTrue(all(e["consolidated"] is None for e in others))

    def test_failure_reproduction_verdicts(self):
        cases = {
            ("t01_acme_revenue", "bolt-v3"): (3, 3, "reproducible"),
            ("t05_flight_duration", "bolt-v3"): (2, 3, "flaky"),
            ("t06_bls_unemployment", "bolt-v3"): (3, 3, "reproducible"),
            ("t07_build_failure", "atlas-v2"): (3, 3, "reproducible"),
            # both agents' clean side of each failing task
            ("t01_acme_revenue", "atlas-v2"): (0, 3, "no failures"),
            ("t05_flight_duration", "atlas-v2"): (0, 3, "no failures"),
            ("t07_build_failure", "bolt-v3"): (0, 3, "no failures"),
            ("t02_cve_libfoo", "atlas-v2"): (0, 3, "no failures"),
        }
        for (task, agent), (k, n, verdict) in cases.items():
            entry = entry_for(self.result, task, agent)
            self.assertEqual(entry["failure_reproduction"],
                             {"k": k, "n": n, "verdict": verdict},
                             f"{task}/{agent}")

    def test_entries_without_failures_carry_no_diagnosis(self):
        entries = self.result["per_task_agent"]
        self.assertEqual(len(entries), 16)  # 8 tasks x 2 agents
        clean = [e for e in entries if e["failures"] == 0]
        self.assertEqual(len(clean), 12)
        for entry in clean:
            self.assertIsNone(entry["consolidated"])
            self.assertEqual(entry["checks_run"], [])
            self.assertEqual(entry["per_run"], [])
            self.assertEqual(entry["failure_reproduction"]["verdict"],
                             "no failures")

    def test_grader_checks_are_inconclusive_on_this_corpus(self):
        # no failing answer in the demo corpus is >= 80%-similar to a
        # passing one, so the executed grader check must decline to rule
        checked = 0
        for entry in self.result["per_task_agent"]:
            for check in entry["checks_run"]:
                self.assertEqual(check["check"], "grader_consistency")
                self.assertEqual(check["outcome"], "inconclusive")
                self.assertEqual(check["basis"], "measured")
                self.assertIn(f"{ANSWER_MATCH_THRESHOLD:.0%}",
                              check["detail"])
                checked += 1
        self.assertEqual(checked, 4)  # one per failing (task, agent)

    def test_narrative_names_the_flake_with_its_rate(self):
        narrative = self.result["narrative"]
        self.assertIn("4 cause(s) reproduce across every failing run",
                      narrative)
        self.assertIn("flaky failures", narrative)
        self.assertIn("bolt-v3 on t05_flight_duration (2 of 3)", narrative)
        self.assertNotIn("confirmed by executed checks", narrative)

    def test_deterministic_across_calls_and_reloads(self):
        again = consolidate_diagnoses(load_demo_corpus())
        self.assertEqual(json.dumps(self.result, sort_keys=True),
                         json.dumps(again, sort_keys=True))


class TestExecutedChecks(unittest.TestCase):
    """Checks answered offline from the corpus, via mutated traces."""

    def test_grader_consistency_confirms_and_upgrades_status(self):
        # give a failing bolt run the exact answer a passing atlas run got
        # credit for: token Jaccard 1.0 >= ANSWER_MATCH_THRESHOLD
        passing_answer = _load_raw(
            "t01_acme_revenue", "atlas-v2", "r1")["outcome"]["answer"]

        def mutate(raw, agent, run_id):
            if agent == "bolt-v3" and run_id == "r1":
                raw["outcome"]["answer"] = passing_answer
            return raw

        result = consolidate_diagnoses(
            single_task_corpus("t01_acme_revenue", mutate))
        entry = entry_for(result, "t01_acme_revenue", "bolt-v3")

        confirms = [c for c in entry["checks_run"]
                    if c["check"] == "grader_consistency"
                    and c["outcome"] == "confirms"]
        self.assertEqual(len(confirms), 1)
        check = confirms[0]
        self.assertEqual(check["hypothesis_kind"], "grader_or_label")
        # the check names both runs of the inconsistent pair
        self.assertIn("bolt-v3/r1", check["runs"])
        self.assertIn("atlas-v2/r1", check["runs"])
        self.assertIn("grader treated near-identical answers differently",
                      check["detail"])

        verdict = entry["consolidated"]
        self.assertEqual(verdict["status"], "confirmed")
        self.assertEqual(verdict["kind"], "grader_or_label")
        self.assertIn("executed check", verdict["basis"])
        self.assertEqual(result["summary"]["confirmed_by_checks"], 1)
        self.assertIn("confirmed by executed checks", result["narrative"])

    def test_environment_reproduction_refutes_transient_error(self):
        # make bolt r1's first step an error while the identical (name,
        # input) call succeeds elsewhere in the corpus
        def mutate(raw, agent, run_id):
            if agent == "bolt-v3" and run_id == "r1":
                raw["steps"][0]["error"] = True
                raw["steps"][0]["output"] = "Error: timeout"
            return raw

        result = consolidate_diagnoses(
            single_task_corpus("t01_acme_revenue", mutate))
        entry = entry_for(result, "t01_acme_revenue", "bolt-v3")

        refutes = [c for c in entry["checks_run"]
                   if c["check"] == "environment_reproduction"]
        self.assertEqual(len(refutes), 1)
        check = refutes[0]
        self.assertEqual(check["outcome"], "refutes")
        self.assertEqual(check["hypothesis_kind"], "environment_error")
        self.assertIn("transient", check["detail"])
        self.assertEqual(check["basis"], "measured")
        # the runs cited are the ones where the same call succeeded,
        # not the failing run itself
        self.assertTrue(check["runs"])
        self.assertNotIn("bolt-v3/r1", check["runs"])

    def test_harness_flake_rate_is_measured_over_the_agents_runs(self):
        def mutate(raw, agent, run_id):
            if agent == "bolt-v3" and run_id == "r1":
                raw["outcome"]["termination"] = "infrastructure_error"
            return raw

        result = consolidate_diagnoses(
            single_task_corpus("t01_acme_revenue", mutate))
        entry = entry_for(result, "t01_acme_revenue", "bolt-v3")

        flakes = [c for c in entry["checks_run"]
                  if c["check"] == "harness_flake_rate"]
        self.assertEqual(len(flakes), 1)
        check = flakes[0]
        self.assertEqual(check["outcome"], "confirms")
        self.assertEqual(check["hypothesis_kind"], "harness_termination")
        self.assertIn("1 of 3", check["detail"])
        self.assertEqual(check["runs"], ["bolt-v3/r1"])
        # an executed check outranks the scored divergence ranking
        self.assertEqual(entry["consolidated"]["status"], "confirmed")
        self.assertEqual(entry["consolidated"]["kind"], "harness_termination")


class TestConsolidationHonesty(unittest.TestCase):
    def test_single_failing_run_stays_unconfirmed(self):
        corpus = {"t01_acme_revenue": {
            "a": [Trajectory.from_json(
                RUN_TRACES / "t01_acme_revenue__atlas-v2__r1.json")],
            "b": [Trajectory.from_json(
                RUN_TRACES / "t01_acme_revenue__bolt-v3__r1.json")],
        }}
        result = consolidate_diagnoses(corpus)
        entry = entry_for(result, "t01_acme_revenue", "bolt-v3")
        self.assertEqual(entry["failure_reproduction"],
                         {"k": 1, "n": 1, "verdict": "single run"})
        verdict = entry["consolidated"]
        self.assertEqual(verdict["status"], "single_run")
        self.assertIn("n=1, unconfirmed", verdict["statement"])
        self.assertIn("single diagnosed run", verdict["basis"])
        # n=1 counts in none of the strong summary buckets
        self.assertEqual(result["summary"]["confirmed_by_checks"], 0)
        self.assertEqual(result["summary"]["reproducible_causes"], 0)

    def test_disagreeing_per_run_diagnoses_are_unstable(self):
        # make bolt r1's answer match the expected answer so grader_or_label
        # leads its diagnosis, while r2/r3 keep leading divergence; the
        # answer shares too few tokens with any passing answer for the
        # executed grader check to confirm, so the disagreement stands
        answer = ("Total revenue for fiscal year 2025 came to $4.82 "
                  "billion according to the investor relations release.")

        def mutate(raw, agent, run_id):
            if agent == "bolt-v3" and run_id == "r1":
                raw["outcome"]["answer"] = answer
                raw["steps"][-1]["output"] = answer
            return raw

        result = consolidate_diagnoses(
            single_task_corpus("t01_acme_revenue", mutate))
        entry = entry_for(result, "t01_acme_revenue", "bolt-v3")

        self.assertEqual(entry["leading_kinds"],
                         {"divergence": 2, "grader_or_label": 1})
        verdict = entry["consolidated"]
        self.assertEqual(verdict["status"], "unstable")
        self.assertIsNone(verdict["kind"])
        self.assertIn("divergence in 2", verdict["statement"])
        self.assertIn("grader_or_label in 1", verdict["statement"])
        self.assertIn("noise-sensitive", verdict["statement"])
        self.assertEqual(result["summary"]["unstable_diagnoses"], 1)
        self.assertIn("per-run diagnoses disagree", result["narrative"])
        # no executed check may claim to have settled this
        self.assertNotIn(
            "confirms",
            {c["outcome"] for c in entry["checks_run"]})


class TestSummaryAndNarrative(unittest.TestCase):
    def test_all_passing_corpus_has_nothing_to_diagnose(self):
        result = consolidate_diagnoses(single_task_corpus("t02_cve_libfoo"))
        self.assertEqual(result["summary"], {
            "tasks": 1,
            "entries_with_failures": 0,
            "confirmed_by_checks": 0,
            "reproducible_causes": 0,
            "unstable_diagnoses": 0,
            "flaky_failures": 0,
        })
        self.assertEqual(result["narrative"],
                         "no failures to diagnose across these runs")

    def test_summary_counts_recomputable_from_entries(self):
        result = consolidate_diagnoses(load_demo_corpus())
        entries = result["per_task_agent"]

        def n_status(status):
            return len([e for e in entries if e["consolidated"]
                        and e["consolidated"]["status"] == status])

        summary = result["summary"]
        self.assertEqual(summary["confirmed_by_checks"],
                         n_status("confirmed"))
        self.assertEqual(summary["reproducible_causes"],
                         n_status("reproducible"))
        self.assertEqual(summary["unstable_diagnoses"],
                         n_status("unstable"))
        self.assertEqual(
            summary["flaky_failures"],
            len([e for e in entries
                 if e["failure_reproduction"]["verdict"] == "flaky"]))
        self.assertEqual(
            summary["entries_with_failures"],
            len([e for e in entries if e["failures"]]))


if __name__ == "__main__":
    unittest.main()
