"""Tests for the CI emitters (SCHEMA.md v23).

Four properties are the contract here, and each one is a way the artifacts
could quietly become useless:

* **They parse.**  A JUnit file no runner accepts is worse than no JUnit
  file, and agent output is arbitrary text — so the hostile-input cases feed
  ``]]>``, raw ``<``, quotes and control bytes through the whole pipeline and
  insist the result still parses.
* **An unmeasurable check is skipped, never passed.**  A disabled criterion,
  an undefined threshold, a process check the trace did not declare enough
  to compute: all ``<skipped>`` / ``notApplicable``, and never a green
  testcase.
* **Fingerprints are stable.**  GitHub deduplicates code-scanning alerts on
  them, so a fingerprint that moves between two identical runs turns one
  finding into a new alert on every push.
* **Output is byte-identical for identical input.**  Otherwise a diff of
  yesterday's artifact against today's shows the clock, not the agent.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, ci
from deepcompare.conformance import check_suite
from deepcompare.gate import evaluate_gate
from deepcompare.report import compare

#: everything an XML or Markdown escaper can get wrong, in one string: a
#: CDATA terminator, raw markup, quotes, a table delimiter, characters XML
#: 1.0 cannot represent in any escaped form, and non-ASCII.
HOSTILE = ']]> <![CDATA[ <tag attr="v"> & | \x00\x0b\x1f ünïcode 😀'

TOOLS = [
    {"name": "get_booking", "effect": "read",
     "parameters": {"properties": {"reference": {"type": "string"}},
                    "required": ["reference"]}},
    {"name": "cancel_booking", "effect": "write",
     "parameters": {"properties": {"reference": {"type": "string"}},
                    "required": ["reference"]}},
]

CLEAN_STEPS = [
    ("tool_call", "get_booking", "get_booking(reference='QX7')",
     "booking QX7 found", False, "read"),
    ("tool_call", "cancel_booking", "cancel_booking(reference='QX7')",
     "cancelled", False, "write"),
    ("answer", "final_answer", "compose the confirmation",
     "QX7 cancelled", False, None),
]

#: writes before it has read anything, then repeats the same call and result.
PATHOLOGICAL_STEPS = [
    ("tool_call", "cancel_booking", "cancel_booking(reference='QX7')",
     "cancelled", False, "write"),
    ("tool_call", "get_booking", "get_booking(reference='QX7')",
     "booking QX7 found", False, "read"),
    ("tool_call", "get_booking", "get_booking(reference='QX7')",
     "booking QX7 found", False, "read"),
    ("answer", "final_answer", "compose the confirmation",
     "QX7 cancelled", False, None),
]

HOSTILE_STEPS = [
    ("tool_call", HOSTILE, f"{HOSTILE}(reference='QX7')",
     "error: no such booking", True, "read"),
    ("tool_call", "cancel_booking", "cancel_booking(reference='QX7')",
     "cancelled", False, "write"),
    ("answer", "final_answer", "compose the confirmation", HOSTILE, False, None),
]


def make_traj(agent, task, success, steps, *, declared=True, cost=0.01,
              latency=3.0, answer="QX7 cancelled"):
    """A trajectory with the optional process fields under our control.

    ``declared=False`` drops the tool list, the budget and the termination
    reason — the case where a real log simply does not say, and the process
    checks must report themselves unmeasurable instead of scoring 100%.
    """
    payload = {
        "schema_version": 1,
        "trace_id": f"{agent}-{task}",
        "agent": {"name": agent, "model": "m", "version": "v1"},
        "task": {"id": task, "prompt": "cancel booking QX7",
                 "expected": "cancelled"},
        "outcome": {"success": success, "answer": answer,
                    "score": 1.0 if success else 0.0},
        "totals": {"input_tokens": 100, "output_tokens": 100,
                   "cost_usd": cost, "latency_s": latency},
        "steps": [
            {"index": i, "type": kind, "name": name, "input": text,
             "output": out, "tokens": 50, "latency_s": 1.0, "quality": None,
             "note": None, "error": error, "effect": effect}
            for i, (kind, name, text, out, error, effect) in enumerate(steps)
        ],
    }
    if declared:
        payload["tools"] = TOOLS
        payload["budget"] = {"max_steps": 10}
        payload["outcome"]["termination"] = "agent_stop"
    return Trajectory.from_json(payload)


def gate_case(baseline_steps=CLEAN_STEPS, candidate_steps=CLEAN_STEPS, *,
              baseline_success=True, candidate_success=True, declared=True,
              baseline_cost=0.01, candidate_cost=0.01, task="t1",
              candidate_answer="QX7 cancelled", **gate_kwargs):
    """Build (gate, reports) for one baseline/candidate pair."""
    base = make_traj("base-v1", task, baseline_success, baseline_steps,
                     declared=declared, cost=baseline_cost)
    cand = make_traj("cand-v2", task, candidate_success, candidate_steps,
                     declared=declared, cost=candidate_cost,
                     answer=candidate_answer)
    report = compare(base, cand)
    return evaluate_gate([report], **gate_kwargs), [report]


def clean_gate():
    return gate_case()


def regressed_gate():
    return gate_case(candidate_steps=HOSTILE_STEPS, candidate_success=False,
                     candidate_answer=HOSTILE)


def pathological_gate():
    """Gate criteria all pass; the candidate's *process* is the finding."""
    return gate_case(candidate_steps=PATHOLOGICAL_STEPS)


def unmeasurable_gate():
    return gate_case(declared=False)


def conformance_case(run_steps=CLEAN_STEPS, *, run_success=True):
    goldens = {"t1": make_traj("golden", "t1", True, CLEAN_STEPS)}
    runs = {"t1": make_traj("run-v2", "t1", run_success, run_steps),
            "t2": make_traj("run-v2", "t2", True, CLEAN_STEPS)}
    goldens["t3"] = make_traj("golden", "t3", True, CLEAN_STEPS)
    return check_suite(goldens, runs)


def by_id(findings):
    return {f["id"]: f for f in findings}


class TestFindings(unittest.TestCase):
    def test_clean_gate_has_no_gating_finding(self):
        gate, reports = clean_gate()
        findings = ci.collect_findings(gate, reports)
        self.assertTrue(findings)
        self.assertTrue(all(f["status"] == "passed" for f in findings))
        self.assertEqual(ci.gating_findings(findings, "any"), [])

    def test_regression_is_reported_per_criterion_and_per_task(self):
        gate, reports = regressed_gate()
        findings = by_id(ci.collect_findings(gate, reports))
        criterion = findings["gate.criterion.success_rate_drop"]
        self.assertEqual(criterion["status"], "failed")
        self.assertEqual(criterion["severity"], "regression")
        task = findings["gate.task.t1"]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["severity"], "regression")
        # Evidence, not policy: the thresholds decide the exit code.
        self.assertFalse(task["gating"])

    def test_per_task_finding_carries_the_issue_fingerprint(self):
        from deepcompare.issues import fingerprint as issue_fingerprint
        gate, reports = regressed_gate()
        task = by_id(ci.collect_findings(gate, reports))["gate.task.t1"]
        self.assertEqual(
            task["fingerprint"],
            issue_fingerprint(reports[0], reports[0]["divergences"][0]),
        )

    def test_process_pathology_is_a_warning_not_a_regression(self):
        gate, reports = pathological_gate()
        findings = by_id(ci.collect_findings(gate, reports))
        blind = findings["gate.process.t1.blind_write"]
        self.assertEqual(blind["severity"], "pathology")
        self.assertEqual(blind["status"], "failed")
        self.assertTrue(blind["gating"])
        # ... and the gate itself is still green, which is the whole point.
        self.assertEqual(gate["verdict"], "pass")

    def test_pre_existing_pathology_is_informational(self):
        gate, reports = gate_case(baseline_steps=PATHOLOGICAL_STEPS,
                                  candidate_steps=PATHOLOGICAL_STEPS)
        blind = by_id(ci.collect_findings(gate, reports))[
            "gate.process.t1.blind_write"]
        self.assertEqual(blind["severity"], "informational")
        self.assertFalse(blind["gating"])
        self.assertIn("pre-existing", blind["message"])

    def test_detect_kind(self):
        gate, _ = clean_gate()
        self.assertEqual(ci.detect_kind(gate), "gate")
        self.assertEqual(ci.detect_kind(conformance_case()), "conformance")
        with self.assertRaises(ValueError):
            ci.detect_kind({"nothing": "recognisable"})


class TestUnmeasurableIsSkipped(unittest.TestCase):
    """The discipline that matters most: a check that did not run is not a
    check that passed."""

    def test_disabled_criterion_is_skipped(self):
        gate, reports = gate_case(allow_new_failure_modes=True)
        finding = by_id(ci.collect_findings(gate, reports))[
            "gate.criterion.new_failure_modes"]
        self.assertEqual(finding["status"], "skipped")
        self.assertEqual(finding["severity"], "informational")
        self.assertIn("not evaluated", finding["message"])
        # The gate's own dict calls it a pass; the CI artifact must not.
        self.assertTrue(
            [c for c in gate["checks"]
             if c["name"] == "new_failure_modes" and c["pass"]]
        )

    def test_zero_baseline_threshold_is_skipped_not_passed(self):
        gate, reports = gate_case(baseline_cost=0.0, candidate_cost=0.0)
        finding = by_id(ci.collect_findings(gate, reports))[
            "gate.criterion.cost_increase"]
        self.assertEqual(finding["status"], "skipped")
        self.assertIn("undefined", finding["message"])

    def test_undeclared_trace_makes_process_checks_skipped(self):
        gate, reports = unmeasurable_gate()
        findings = by_id(ci.collect_findings(gate, reports))
        for key in ("schema_validity", "tool_grounding", "false_success",
                    "termination"):
            finding = findings[f"gate.process.t1.unmeasurable.{key}"]
            self.assertEqual(finding["status"], "skipped")
            self.assertTrue(finding["gating"])

    def test_missing_reference_is_skipped(self):
        suite = conformance_case()
        findings = by_id(ci.collect_findings(suite))
        self.assertEqual(
            findings["conformance.missing_reference.t2"]["status"], "skipped")
        self.assertEqual(
            findings["conformance.unused_reference.t3"]["status"], "skipped")

    def test_skipped_renders_as_skipped_everywhere(self):
        gate, reports = gate_case(allow_new_failure_modes=True)
        root = ET.fromstring(ci.to_junit(gate, reports))
        case = [c for c in root.iter("testcase")
                if c.get("name") == "new_failure_modes"][0]
        self.assertEqual(len(case.findall("skipped")), 1)
        self.assertEqual(case.findall("failure"), [])

        sarif = json.loads(ci.to_sarif(gate, reports))
        result = [r for r in sarif["runs"][0]["results"]
                  if r["ruleId"].endswith("new_failure_modes")][0]
        self.assertEqual(result["kind"], "notApplicable")
        # SARIF requires level "none" for any kind other than "fail".
        self.assertEqual(result["level"], "none")

        summary = ci.to_job_summary(gate, reports)
        self.assertIn("## Not measured", summary)
        self.assertIn("skipped (not measured)", summary)


class TestJUnit(unittest.TestCase):
    def test_parses_and_counts(self):
        gate, reports = regressed_gate()
        root = ET.fromstring(ci.to_junit(gate, reports))
        self.assertEqual(root.tag, "testsuites")
        cases = list(root.iter("testcase"))
        self.assertEqual(int(root.get("tests")), len(cases))
        self.assertEqual(int(root.get("failures")),
                         len(list(root.iter("failure"))))
        self.assertEqual(int(root.get("skipped")),
                         len(list(root.iter("skipped"))))
        self.assertEqual(root.get("errors"), "0")
        suites = {s.get("name") for s in root.iter("testsuite")}
        self.assertIn("agentdiff.gate", suites)
        self.assertIn("agentdiff.tasks", suites)

    def test_one_testcase_per_task_and_per_criterion(self):
        gate, reports = regressed_gate()
        root = ET.fromstring(ci.to_junit(gate, reports))
        names = {(c.get("classname"), c.get("name"))
                 for c in root.iter("testcase")}
        for check in gate["checks"]:
            self.assertIn(("agentdiff.gate", check["name"]), names)
        self.assertIn(("agentdiff.tasks", "t1"), names)

    def test_hostile_text_still_parses(self):
        gate, reports = regressed_gate()
        xml = ci.to_junit(gate, reports)
        root = ET.fromstring(xml)  # raises if the escaping is wrong
        text = "".join(
            (element.text or "") + " ".join(element.attrib.values())
            for element in root.iter()
        )
        self.assertIn("]]>", text)
        self.assertIn("<tag", text)
        self.assertIn("&", text)
        # XML 1.0 cannot carry these at all, so they must not survive raw.
        self.assertNotIn("\x00", xml)
        self.assertFalse(
            any(ord(c) < 32 and c not in "\t\n\r" for c in xml),
            "illegal control characters reached the XML",
        )
        self.assertIn("�", xml)  # replaced, not silently dropped

    def test_timing_only_where_the_data_has_it(self):
        gate, reports = regressed_gate()
        root = ET.fromstring(ci.to_junit(gate, reports))
        task_case = [c for c in root.iter("testcase")
                     if c.get("classname") == "agentdiff.tasks"][0]
        self.assertEqual(float(task_case.get("time")),
                         reports[0]["b"]["totals"]["latency_s"])
        criterion = [c for c in root.iter("testcase")
                     if c.get("classname") == "agentdiff.gate"][0]
        self.assertIsNone(criterion.get("time"))
        gate_suite = [s for s in root.iter("testsuite")
                      if s.get("name") == "agentdiff.gate"][0]
        self.assertIsNone(gate_suite.get("time"))

    def test_no_timestamp_attribute(self):
        gate, reports = regressed_gate()
        xml = ci.to_junit(gate, reports)
        self.assertNotIn("timestamp", xml)

    def test_fail_on_controls_which_findings_are_red(self):
        gate, reports = pathological_gate()
        lenient = ET.fromstring(ci.to_junit(gate, reports, fail_on="regression"))
        strict = ET.fromstring(ci.to_junit(gate, reports, fail_on="pathology"))
        self.assertEqual(list(lenient.iter("failure")), [])
        self.assertTrue(list(strict.iter("failure")))
        # Sub-threshold findings are still visible, not dropped.
        below = "".join(e.text or "" for e in lenient.iter("system-out"))
        self.assertIn("wrote before reading anything", below)


class TestSarif(unittest.TestCase):
    def sarif(self, payload=None):
        if payload is None:
            gate, reports = regressed_gate()
            payload = (gate, reports)
        return json.loads(ci.to_sarif(payload[0], payload[1]))

    def test_required_structure(self):
        doc = self.sarif()
        self.assertEqual(doc["$schema"], ci.SARIF_SCHEMA)
        self.assertEqual(doc["version"], "2.1.0")
        self.assertIsInstance(doc["runs"], list)
        run = doc["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "AgentDiff")
        self.assertIsInstance(run["results"], list)
        self.assertTrue(run["results"])
        for result in run["results"]:
            self.assertIn("ruleId", result)
            self.assertIn("text", result["message"])
            self.assertIn(result["kind"], ("fail", "notApplicable"))

    def test_rules_are_derived_from_the_findings_present(self):
        run = self.sarif()["runs"][0]
        rules = run["tool"]["driver"]["rules"]
        ids = [rule["id"] for rule in rules]
        self.assertEqual(ids, sorted(ids))          # deterministic order
        self.assertEqual(len(ids), len(set(ids)))
        for result in run["results"]:
            self.assertEqual(ids[result["ruleIndex"]], result["ruleId"])
        for rule in rules:
            self.assertIn(rule["defaultConfiguration"]["level"],
                          ("error", "warning", "note"))
            self.assertTrue(rule["fullDescription"]["text"])

    def test_levels_are_mapped_honestly(self):
        gate, reports = regressed_gate()
        doc = json.loads(ci.to_sarif(gate, reports))
        levels = {r["ruleId"]: r["level"] for r in doc["runs"][0]["results"]}
        self.assertEqual(levels["agentdiff/gate/success_rate_drop"], "error")
        self.assertEqual(levels["agentdiff/gate/task_regression"], "error")

        gate, reports = pathological_gate()
        doc = json.loads(ci.to_sarif(gate, reports))
        levels = {r["ruleId"]: r["level"] for r in doc["runs"][0]["results"]}
        self.assertEqual(levels["agentdiff/process/blind_write"], "warning")

        gate, reports = gate_case(baseline_steps=PATHOLOGICAL_STEPS,
                                  candidate_steps=PATHOLOGICAL_STEPS)
        doc = json.loads(ci.to_sarif(gate, reports))
        levels = {r["ruleId"]: r["level"] for r in doc["runs"][0]["results"]}
        self.assertEqual(levels["agentdiff/process/blind_write"], "note")

    def test_passing_findings_are_not_results(self):
        gate, reports = clean_gate()
        doc = json.loads(ci.to_sarif(gate, reports))
        self.assertEqual(doc["runs"][0]["results"], [])

    def test_location_is_the_trace_file_with_no_invented_region(self):
        gate, reports = regressed_gate()
        paths = {"t1::cand-v2": "traces/t1__cand-v2.json"}
        doc = json.loads(ci.to_sarif(gate, reports, paths))
        located = [r for r in doc["runs"][0]["results"] if "locations" in r]
        self.assertTrue(located)
        task_result = [r for r in located
                       if r["ruleId"].endswith("task_regression")][0]
        physical = task_result["locations"][0]["physicalLocation"]
        self.assertEqual(physical["artifactLocation"]["uri"],
                         "traces/t1__cand-v2.json")
        # No fabricated line numbers anywhere in the document.
        self.assertNotIn("region", json.dumps(doc))
        self.assertNotIn("startLine", json.dumps(doc))

    def test_logical_location_when_no_trace_path_is_known(self):
        gate, reports = regressed_gate()
        doc = json.loads(ci.to_sarif(gate, reports))
        task_result = [r for r in doc["runs"][0]["results"]
                       if r["ruleId"].endswith("task_regression")][0]
        location = task_result["locations"][0]
        self.assertNotIn("physicalLocation", location)
        self.assertEqual(location["logicalLocations"][0]["name"], "t1")

    def test_fingerprints_are_stable_across_identical_runs(self):
        first = json.loads(ci.to_sarif(*regressed_gate()))
        second = json.loads(ci.to_sarif(*regressed_gate()))
        prints = [r["partialFingerprints"] for r in first["runs"][0]["results"]]
        self.assertEqual(
            prints, [r["partialFingerprints"] for r in second["runs"][0]["results"]]
        )
        self.assertTrue(all("agentdiffFinding/v1" in p for p in prints))
        issue_prints = [p["agentdiffIssue/v1"] for p in prints
                        if "agentdiffIssue/v1" in p]
        self.assertTrue(issue_prints)
        # The issue fingerprint is the hash-free signature from issues.py, so
        # it can also be pasted into .agentdiffignore.
        self.assertTrue(any("/a:" in p for p in issue_prints))

    def test_hostile_text_survives_json(self):
        doc = json.loads(ci.to_sarif(*regressed_gate()))
        blob = json.dumps(doc)
        self.assertIn("]]>", blob)
        self.assertNotIn("\x00", blob)

    def test_no_wall_clock_in_the_payload(self):
        doc = json.loads(ci.to_sarif(*regressed_gate()))
        self.assertNotIn("invocations", doc["runs"][0])
        self.assertNotIn("startTimeUtc", json.dumps(doc))


class TestAnnotations(unittest.TestCase):
    def test_levels_and_file_but_never_a_line(self):
        gate, reports = regressed_gate()
        paths = {"t1::cand-v2": "traces/t1__cand-v2.json"}
        lines = ci.to_github_annotations(gate, reports, paths).splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("::") for line in lines))
        self.assertTrue(any(line.startswith("::error ") for line in lines))
        self.assertTrue(
            any("file=traces/t1__cand-v2.json" in line for line in lines))
        self.assertFalse(any("line=" in line for line in lines))

    def test_pathology_is_a_warning_and_unmeasurable_a_notice(self):
        lines = ci.to_github_annotations(*pathological_gate()).splitlines()
        self.assertTrue(any(line.startswith("::warning ") for line in lines))
        lines = ci.to_github_annotations(*unmeasurable_gate()).splitlines()
        self.assertTrue(any(line.startswith("::notice ") for line in lines))
        self.assertTrue(any("not measured" in line for line in lines))

    def test_workflow_command_escaping(self):
        lines = ci.to_github_annotations(*regressed_gate()).splitlines()
        blob = "\n".join(lines)
        self.assertIn("%25", blob)               # a literal % in a message
        self.assertNotIn("%0A", blob.split("::")[0])
        for line in lines:
            # Properties end at a comma or colon, so those must be escaped:
            # everything before the "::" separator is property territory.
            head = line.split("::", 2)[1]
            self.assertNotIn(":", head)
            self.assertEqual(head.count(","), head.count("=") - 1)

    def test_clean_run_annotates_nothing(self):
        self.assertEqual(ci.to_github_annotations(*clean_gate()), "")


class TestJobSummary(unittest.TestCase):
    def test_headline_criteria_and_exit_policy(self):
        gate, reports = regressed_gate()
        summary = ci.to_job_summary(gate, reports)
        self.assertIn("# AgentDiff gate: ❌ FAIL", summary)
        self.assertIn("## Criteria", summary)
        self.assertIn("`success_rate_drop`", summary)
        self.assertIn("## Exit codes", summary)
        self.assertIn("`--fail-on regression` → exit **1**", summary)

    def test_threshold_changes_the_stated_exit_code(self):
        gate, reports = pathological_gate()
        self.assertIn("→ exit **0**", ci.to_job_summary(gate, reports))
        self.assertIn("→ exit **1**",
                      ci.to_job_summary(gate, reports, fail_on="pathology"))

    def test_markdown_escaping(self):
        summary = ci.to_job_summary(*regressed_gate())
        self.assertNotIn("<tag", summary)      # raw HTML would render
        self.assertIn("&lt;tag", summary)
        for line in summary.splitlines():
            if line.startswith("|") and "&lt;tag" in line:
                # the hostile "|" must not have opened an extra cell
                self.assertIn("\\|", line)

    def test_conformance_summary(self):
        suite = conformance_case(run_success=False)
        summary = ci.to_job_summary(suite)
        self.assertIn("# AgentDiff conformance: ❌ FAIL", summary)
        self.assertIn("## Not measured", summary)


class TestConformanceEmitters(unittest.TestCase):
    def test_violation_is_a_regression(self):
        suite = conformance_case(run_success=False)
        finding = by_id(ci.collect_findings(suite))["conformance.task.t1"]
        self.assertEqual(finding["severity"], "regression")
        self.assertEqual(finding["status"], "failed")

    def test_deviation_is_a_pathology(self):
        extra = list(CLEAN_STEPS)
        extra.insert(1, ("tool_call", "get_booking",
                         "get_booking(reference='QX8')", "booking QX8 found",
                         False, "read"))
        suite = conformance_case(run_steps=extra)
        finding = by_id(ci.collect_findings(suite))["conformance.task.t1"]
        self.assertEqual(finding["properties"]["verdict"], "deviation")
        self.assertEqual(finding["severity"], "pathology")

    def test_junit_and_sarif_render(self):
        suite = conformance_case(run_success=False)
        root = ET.fromstring(ci.to_junit(suite))
        self.assertTrue(list(root.iter("failure")))
        self.assertTrue(list(root.iter("skipped")))
        doc = json.loads(ci.to_sarif(suite))
        self.assertEqual(doc["runs"][0]["tool"]["driver"]["name"], "AgentDiff")
        self.assertTrue(doc["runs"][0]["results"])


class TestExitCodePolicy(unittest.TestCase):
    """The documented table, pinned case by case."""

    def codes(self, payload):
        result, reports = payload
        return {
            level: ci.exit_code(result, reports, level)
            for level in ci.FAIL_ON_CHOICES
        }

    def test_clean_run_is_zero_at_every_threshold(self):
        self.assertEqual(set(self.codes(clean_gate()).values()), {0})

    def test_regression_fails_from_regression_upwards(self):
        codes = self.codes(regressed_gate())
        self.assertEqual(codes["never"], 0)
        self.assertEqual(codes["regression"], 1)
        self.assertEqual(codes["pathology"], 1)
        self.assertEqual(codes["any"], 1)

    def test_pathology_only_fails_when_the_team_tightens(self):
        codes = self.codes(pathological_gate())
        self.assertEqual(codes["never"], 0)
        self.assertEqual(codes["regression"], 0)
        self.assertEqual(codes["pathology"], 1)
        self.assertEqual(codes["any"], 1)

    def test_unmeasurable_only_fails_at_any(self):
        codes = self.codes(unmeasurable_gate())
        self.assertEqual(codes["regression"], 0)
        self.assertEqual(codes["pathology"], 0)
        self.assertEqual(codes["any"], 1)

    def test_conformance_violation_matches_the_gate_policy(self):
        suite = conformance_case(run_success=False)
        self.assertEqual(ci.exit_code(suite, fail_on="regression"), 1)
        self.assertEqual(ci.exit_code(suite, fail_on="never"), 0)

    def test_unknown_threshold_is_an_error(self):
        gate, reports = clean_gate()
        with self.assertRaises(ValueError):
            ci.exit_code(gate, reports, "sometimes")

    def test_documented_codes(self):
        self.assertEqual(sorted(ci.EXIT_CODES), [0, 1, 2])
        self.assertEqual(ci.EXIT_OK, 0)
        self.assertEqual(ci.EXIT_FINDINGS, 1)
        self.assertEqual(ci.EXIT_USAGE, 2)


class TestDeterminism(unittest.TestCase):
    def test_every_emitter_is_byte_identical_across_runs(self):
        for build in (regressed_gate, pathological_gate, unmeasurable_gate):
            first_result, first_reports = build()
            second_result, second_reports = build()
            for emit in (ci.to_junit, ci.to_sarif, ci.to_github_annotations,
                         ci.to_job_summary):
                with self.subTest(build=build.__name__, emit=emit.__name__):
                    self.assertEqual(
                        emit(first_result, first_reports),
                        emit(second_result, second_reports),
                    )

    def test_conformance_emitters_are_byte_identical(self):
        for emit in (ci.to_junit, ci.to_sarif, ci.to_github_annotations,
                     ci.to_job_summary):
            with self.subTest(emit=emit.__name__):
                self.assertEqual(emit(conformance_case(run_success=False)),
                                 emit(conformance_case(run_success=False)))


class TestTracePaths(unittest.TestCase):
    def test_collects_task_and_task_agent_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            traj = make_traj("cand-v2", "t1", True, CLEAN_STEPS)
            (root / "t1__cand-v2.json").write_text(
                json.dumps(traj.to_dict()), encoding="utf-8")
            (root / "broken.json").write_text("{not json", encoding="utf-8")
            paths = ci.collect_trace_paths(root)
        self.assertEqual(paths["t1"], paths["t1::cand-v2"])
        self.assertTrue(paths["t1"].endswith("t1__cand-v2.json"))

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(ci.collect_trace_paths("/nonexistent/dir"), {})


class TestCliWiring(unittest.TestCase):
    @staticmethod
    def _run(argv):
        from deepcompare.cli import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue()

    def _write(self, dirpath, trajectories):
        dirpath.mkdir(parents=True, exist_ok=True)
        for traj in trajectories:
            (dirpath / f"{traj.task.id}__{traj.agent.name}.json").write_text(
                json.dumps(traj.to_dict()), encoding="utf-8")

    def _fixture(self, root, candidate_steps=HOSTILE_STEPS,
                 candidate_success=False):
        self._write(root / "base",
                    [make_traj("base-v1", "t1", True, CLEAN_STEPS)])
        self._write(root / "cand",
                    [make_traj("cand-v2", "t1", candidate_success,
                               candidate_steps)])

    def test_gate_writes_artifacts_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            code, out = self._run([
                "gate", str(root / "base"), str(root / "cand"),
                "-o", str(root / "out"), "--junit", "--sarif", "--job-summary",
            ])
            self.assertEqual(code, 1)
            junit = root / "out" / "junit.xml"
            sarif = root / "out" / "results.sarif"
            summary = root / "out" / "ci-summary.md"
            for path in (junit, sarif, summary):
                self.assertTrue(path.is_file(), path)
                self.assertIn(f"Wrote {path}", out)
            ET.fromstring(junit.read_text(encoding="utf-8"))
            doc = json.loads(sarif.read_text(encoding="utf-8"))
            self.assertEqual(doc["version"], "2.1.0")
            # SARIF points at the real candidate trace on disk.
            uris = {
                location["physicalLocation"]["artifactLocation"]["uri"]
                for result in doc["runs"][0]["results"]
                for location in result.get("locations", [])
                if "physicalLocation" in location
            }
            self.assertTrue(any(u.endswith("t1__cand-v2.json") for u in uris))

    def test_gate_default_exit_matches_the_pre_existing_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, candidate_steps=CLEAN_STEPS,
                          candidate_success=True)
            code, _ = self._run(["gate", str(root / "base"), str(root / "cand"),
                                 "-o", str(root / "out")])
            self.assertEqual(code, 0)

    def test_fail_on_never_reports_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            code, _ = self._run([
                "gate", str(root / "base"), str(root / "cand"),
                "-o", str(root / "out"), "--junit", "--fail-on", "never",
            ])
            self.assertEqual(code, 0)
            self.assertTrue((root / "out" / "junit.xml").is_file())

    def test_annotations_go_to_stdout_and_the_step_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            step_summary = root / "step_summary.md"
            step_summary.write_text("earlier step\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ, {"GITHUB_STEP_SUMMARY": str(step_summary)}
            ):
                code, out = self._run([
                    "gate", str(root / "base"), str(root / "cand"),
                    "-o", str(root / "out"), "--github-annotations",
                ])
            self.assertEqual(code, 1)
            self.assertIn("::error ", out)
            written = step_summary.read_text(encoding="utf-8")
            self.assertTrue(written.startswith("earlier step\n"))  # appended
            self.assertIn("# AgentDiff gate", written)

    def test_check_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "golden",
                        [make_traj("golden", "t1", True, CLEAN_STEPS)])
            self._write(root / "runs",
                        [make_traj("run-v2", "t1", False, CLEAN_STEPS)])
            code, _ = self._run([
                "check", str(root / "runs"), "--golden", str(root / "golden"),
                "-o", str(root / "out"), "--junit", "junit.xml",
                "--sarif", "scan.sarif",
            ])
            self.assertEqual(code, 1)
            root_el = ET.fromstring(
                (root / "out" / "junit.xml").read_text(encoding="utf-8"))
            self.assertTrue(list(root_el.iter("failure")))
            doc = json.loads((root / "out" / "scan.sarif").read_text("utf-8"))
            self.assertTrue(doc["runs"][0]["results"])

    def test_artifacts_are_identical_across_two_cli_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            outputs = []
            for run in ("out1", "out2"):
                self._run(["gate", str(root / "base"), str(root / "cand"),
                           "-o", str(root / run), "--junit", "--sarif",
                           "--job-summary"])
                outputs.append({
                    name: (root / run / name).read_bytes()
                    for name in ("junit.xml", "results.sarif", "ci-summary.md")
                })
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
