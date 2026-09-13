"""CI artifacts: AgentDiff findings in the formats CI already reads (v23).

The gate can already decide that a candidate regressed — it exits non-zero
and writes Markdown a human can read.  That is one step short of useful,
because the place a team actually notices a regression is not a Markdown
file in an artifact bundle: it is the red test tab, the PR annotation next
to the changed file, and the security/code-scanning view.  Every one of
those surfaces speaks a specific format, and none of them speak "gate.json".

So this module translates, and translates *only* — it computes nothing new.
Each emitter is a pure function from a result dict (a ``gate`` payload or a
``check_suite`` payload) to a string, so the same findings can be written to
disk, diffed between two runs, or piped somewhere without a hosted service
in the middle.  Everything is stdlib: ``xml.etree.ElementTree``, ``json``,
``html``.  A CI integration that requires a vendor account is a integration
a team cannot adopt on a Friday afternoon.

Four surfaces
-------------

``to_junit``
    JUnit XML — the lingua franca of CI test reporting, understood by
    GitHub, GitLab, Jenkins, Buildkite, CircleCI and every dashboard built
    on them.  One ``<testcase>`` per gate criterion, per task, and per
    process pathology.
``to_sarif``
    SARIF 2.1.0 — what GitHub code scanning ingests.  Carries the issue
    fingerprints so GitHub can recognise a finding it has already shown,
    instead of re-reporting it on every run.
``to_github_annotations``
    ``::error file=…::message`` workflow commands, which put the finding on
    the PR itself.
``to_job_summary``
    Markdown for ``$GITHUB_STEP_SUMMARY`` — the run's front page.

Three disciplines carry over from the rest of the tool
------------------------------------------------------

**An unmeasurable check is ``skipped``, never ``passed``.**  A gate criterion
that was disabled, or whose baseline was zero so no relative threshold could
be applied, produced no evidence.  Rendering that as a green testcase would
let a gate pass because it never looked, which is the one failure mode a
gate must not have.  Those become ``<skipped>`` in JUnit, ``notApplicable``
in SARIF, and are called out by name in the job summary.

**Deterministic output.**  No wall-clock anywhere in the payload — no
``timestamp`` attribute, no SARIF ``invocations`` block.  Durations are
emitted only where the trace carried them.  Two runs over the same input
produce byte-identical artifacts, so diffing yesterday's JUnit against
today's shows what changed in the agent rather than what changed on the
clock.

**Escaping is not optional.**  Agent output is arbitrary text — it contains
``<``, ``&``, ``]]>``, and occasionally control bytes that are not legal in
XML 1.0 at all.  ``ElementTree`` handles the first two; the third would emit
a file no parser accepts, so it is sanitised here.

Failure vs. exit code
---------------------

The test report and the exit code answer different questions on purpose.
JUnit and SARIF report **what happened** — a task the baseline solved and
the candidate did not is a failure there, always.  The exit code reports
**what policy says about it**, and the policy lives in the gate thresholds
the team chose plus ``--fail-on``.  A run where one task regressed and
another improved can therefore show a red testcase and still exit 0: the
team set ``--max-success-drop`` to tolerate exactly that.  Suppressing the
testcase instead would hide the evidence to protect the verdict.
"""

from __future__ import annotations

import html
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional, Union

from .issues import fingerprint as issue_fingerprint
# The wording of each pathology lives with the rule that produces it; reusing
# it here keeps the SARIF rule descriptions from drifting away from what the
# process analysis actually reports.
from .process import _PATHOLOGIES as PROCESS_PATHOLOGIES

#: severities, ascending.  ``informational`` is "worth knowing, including
#: 'this was never measured'", ``pathology`` is a process problem that the
#: outcome hides, ``regression`` is the candidate doing worse than baseline.
SEVERITY_ORDER = ("informational", "pathology", "regression")

#: what ``--fail-on`` accepts, and the minimum severity each one gates on.
FAIL_ON_THRESHOLDS = {
    "never": None,
    "regression": "regression",
    "pathology": "pathology",
    "any": "informational",
}
FAIL_ON_CHOICES = tuple(FAIL_ON_THRESHOLDS)
DEFAULT_FAIL_ON = "regression"

#: exit codes, documented once and used everywhere.  2 is not produced here:
#: it belongs to the CLI, which reports usage and data errors before any
#: analysis exists to emit.
EXIT_CODES = {
    0: "no gating finding at or above the --fail-on threshold",
    1: "at least one gating finding at or above the --fail-on threshold",
    2: "usage or data error (bad directory, unreadable traces); the CLI "
       "reports this before any finding exists",
}
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

#: SARIF level per severity.  ``error`` is reserved for a real regression;
#: a process pathology is a ``warning`` because the run still met its
#: oracle; everything else — including "this was not measured" — is a note.
SARIF_LEVEL = {
    "regression": "error",
    "pathology": "warning",
    "informational": "note",
}
#: GitHub workflow-command name per severity.
ANNOTATION_LEVEL = {
    "regression": "error",
    "pathology": "warning",
    "informational": "notice",
}

TOOL_NAME = "AgentDiff"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"

#: long agent output is evidence, not a novel; artifacts stay reviewable.
MAX_MESSAGE_CHARS = 800

#: XML 1.0 cannot represent most control characters at all, in any escaped
#: form.  ElementTree will happily write them and no parser will read the
#: result back, so they are replaced rather than escaped.  The ranges are
#: assembled at import time so no literal control character lives in this
#: source file.
_XML_LEGAL_RANGES = (
    (0x09, 0x09), (0x0A, 0x0A), (0x0D, 0x0D),
    (0x20, 0xD7FF), (0xE000, 0xFFFD), (0x10000, 0x10FFFF),
)
_XML_ILLEGAL = re.compile(
    "[^" + "".join(
        chr(low) if low == high else f"{chr(low)}-{chr(high)}"
        for low, high in _XML_LEGAL_RANGES
    ) + "]"
)
#: U+FFFD REPLACEMENT CHARACTER, the standard "something was here" marker.
_REPLACEMENT = chr(0xFFFD)


# --------------------------------------------------------------------------
# rule catalogue
# --------------------------------------------------------------------------

#: gate criteria, keyed by the ``name`` the gate emits.  Unknown names still
#: get a rule (a third party can add a criterion) — only the prose is lost.
_GATE_RULE_TEXT = {
    "success_rate_drop": "The candidate solved fewer tasks than the baseline "
                         "by more than the allowed drop.",
    "cost_increase": "The candidate's mean cost per task rose more than the "
                     "allowed fraction.",
    "latency_increase": "The candidate's mean latency per task rose more than "
                        "the allowed fraction.",
    "new_failure_modes": "The candidate failed for a reason the baseline "
                         "never failed for.",
}

#: divergence kinds, as classified by ``deepcompare.divergence``.
_DIVERGENCE_RULE_TEXT = {
    "retrieval": "The two runs read different sources.",
    "tool_selection": "The two runs reached for different tools.",
    "tool_execution": "The same tool was called with arguments that did not "
                      "hold up.",
    "planning": "The two runs planned the task differently.",
    "reasoning": "The two runs reasoned along different paths.",
    "stopping": "One run kept working after the evidence was sufficient.",
}

_PROCESS_RULE_TEXT = dict(PROCESS_PATHOLOGIES)

_CONFORMANCE_RULE_TEXT = {
    "violation": "The run reached a different outcome than the reference "
                 "trajectory.",
    "deviation": "The run added or skipped steps relative to the reference "
                 "while still reaching its outcome.",
    "drift": "The run followed the reference's shape with different step "
             "content.",
    "conformant": "The run followed the reference exactly.",
}

_UNMEASURABLE_RULE_TEXT = {
    "gate_criterion": "A gate criterion produced no evidence — it was "
                      "disabled, or no threshold could be applied. It is "
                      "reported skipped, because a check that did not run is "
                      "not a check that passed.",
    "process": "A process check could not be computed because the trace did "
               "not declare what it needed (tool list, parameter schemas, "
               "termination reason).",
    "reference": "A task had no counterpart on the other side, so nothing "
                 "was checked for it.",
}


def _rule_id(category: str, key: str) -> str:
    return f"agentdiff/{category}/{key}"


def _rule_text(category: str, key: str) -> str:
    table = {
        "gate": _GATE_RULE_TEXT,
        "divergence": _DIVERGENCE_RULE_TEXT,
        "process": _PROCESS_RULE_TEXT,
        "conformance": _CONFORMANCE_RULE_TEXT,
        "unmeasurable": _UNMEASURABLE_RULE_TEXT,
    }.get(category, {})
    return table.get(key) or f"AgentDiff {category} finding: {key}."


# --------------------------------------------------------------------------
# text hygiene
# --------------------------------------------------------------------------


def _clean(text: object, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Collapse to one reviewable line of XML-legal text.

    Newlines are collapsed because a JUnit message attribute and a GitHub
    annotation are both single-line surfaces; the full narrative is already
    in ``gate.json``.  Characters outside XML 1.0's repertoire are replaced
    with U+FFFD rather than dropped, so a trace carrying a stray ``\\x00``
    still produces a file that parses and still shows that something was
    there.
    """
    raw = "" if text is None else str(text)
    raw = _XML_ILLEGAL.sub(_REPLACEMENT, raw)
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = raw[: limit - 1].rstrip() + "…"
    return raw


def _md(text: object) -> str:
    """Escape free text for a Markdown table cell.

    GitHub renders raw HTML inside Markdown, so an answer containing
    ``<script>`` or ``&`` has to be escaped as HTML too, and ``|`` has to be
    escaped or it ends the cell.
    """
    return html.escape(_clean(text), quote=False).replace("|", "\\|")


def _gha_data(text: object) -> str:
    """Escape the message half of a workflow command (per GitHub's spec)."""
    return (
        _clean(text)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _gha_property(text: object) -> str:
    """Escape a workflow-command property, where ``,`` and ``:`` also end
    the token."""
    return _gha_data(text).replace(",", "%2C").replace(":", "%3A")


# --------------------------------------------------------------------------
# findings — the one intermediate shape every emitter renders
# --------------------------------------------------------------------------


def detect_kind(result: dict) -> str:
    """Which AgentDiff payload this is: ``gate`` or ``conformance``.

    Structural, not a flag the caller has to pass: the two payloads are
    distinguishable by the keys they already carry, so an emitter can be
    handed whatever the command produced.
    """
    if not isinstance(result, dict):
        raise ValueError("result must be a dict")
    if "reports_summary" in result and "verdict" in result:
        return "gate"
    if "mean_conformance" in result and "counts" in result:
        return "conformance"
    raise ValueError(
        "unrecognised result payload: expected a gate dict (with "
        "'reports_summary') or a conformance suite (with 'mean_conformance')"
    )


def _finding(
    *,
    id: str,
    rule: str,
    suite: str,
    case: str,
    title: str,
    message: str,
    severity: str,
    status: str,
    gating: bool,
    task: Optional[str] = None,
    location: Optional[str] = None,
    duration_s: Optional[float] = None,
    fingerprint: Optional[str] = None,
    properties: Optional[dict] = None,
) -> dict:
    if severity not in SEVERITY_ORDER:
        raise ValueError(f"unknown severity {severity!r}")
    if status not in ("passed", "failed", "skipped"):
        raise ValueError(f"unknown status {status!r}")
    return {
        "id": id,
        "rule": rule,
        "suite": suite,
        "case": case,
        "title": _clean(title, 200),
        "message": _clean(message),
        "severity": severity,
        "status": status,
        # ``gating`` says whether this finding may set the exit code.  Gate
        # criteria and process checks do; per-task evidence does not, because
        # the thresholds are where a team already expressed that policy.
        "gating": bool(gating),
        "task": task,
        "location": location,
        "duration_s": duration_s,
        "fingerprint": fingerprint,
        "properties": dict(properties or {}),
    }


def _lookup_path(
    trace_paths: Optional[dict], task: Optional[str], agent: Optional[str]
) -> Optional[str]:
    """The trace file a finding points at, preferring the exact run."""
    if not trace_paths or not task:
        return None
    if agent:
        exact = trace_paths.get(f"{task}::{agent}")
        if exact:
            return exact
    return trace_paths.get(task)


def collect_trace_paths(directory: Union[str, Path]) -> dict[str, str]:
    """Map task ids (and ``task::agent``) to the trace files they came from.

    SARIF and PR annotations both want a file to point at, and the report
    payloads do not carry one — the engine works on parsed trajectories, not
    paths.  Rather than fabricate a location, the caller collects the real
    ones here and passes them in.  Paths are emitted as given (POSIX
    separators); pass repo-relative paths if you want GitHub to resolve them
    against the checkout.
    """
    paths: dict[str, str] = {}
    base = Path(directory)
    if not base.is_dir():
        return paths
    for path in sorted(base.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        task = (data.get("task") or {}).get("id")
        agent = (data.get("agent") or {}).get("name")
        if not task:
            continue
        uri = path.as_posix()
        paths.setdefault(str(task), uri)
        if agent:
            paths.setdefault(f"{task}::{agent}", uri)
    return paths


def _criterion_unmeasurable(gate: dict, check: dict) -> Optional[str]:
    """Why a gate criterion produced no evidence, or ``None`` if it did.

    Detected structurally rather than by reading the prose in ``detail``, so
    a reworded message cannot quietly turn an unevaluated check green.
    """
    if check.get("measurable") is False:  # future criteria may say so directly
        return "the criterion reported itself unmeasurable"
    name = check.get("name")
    thresholds = gate.get("thresholds") or {}
    if name == "new_failure_modes" and thresholds.get("allow_new_failure_modes"):
        return (
            "the new-failure-mode check was disabled with "
            "--allow-new-failure-modes, so no failure origins were compared"
        )
    baseline = check.get("baseline")
    if (
        name in ("cost_increase", "latency_increase")
        and check.get("threshold") is not None
        and isinstance(baseline, (int, float))
        and not isinstance(baseline, bool)
        and baseline == 0
    ):
        return (
            "the baseline mean is 0, so a relative rise is undefined and the "
            "threshold was never applied"
        )
    return None


def _process_evidence(side: dict, flag: str) -> str:
    """One concrete number behind a raised flag, so the reader can check it."""
    repeats = side.get("repeats") or {}
    loops = side.get("loops") or {}
    recovery = side.get("recovery") or {}
    effects = side.get("side_effects") or {}
    grounding = side.get("grounding") or {}
    schema = side.get("schema") or {}
    termination = side.get("termination") or {}
    block = loops.get("longest_repeated_block") or {}
    evidence = {
        "looped": f"{repeats.get('cycles', 0)} call-and-result cycle(s)",
        "loop_block": f"a {block.get('period', 0)}-step block repeated "
                      f"{block.get('repeats', 0)} time(s) from step "
                      f"{block.get('starts_at')}",
        "repeated_calls": f"{repeats.get('repeated_calls', 0)} repeated call(s)",
        "no_information_steps": f"{repeats.get('no_information_steps', 0)} "
                                f"step(s) returned nothing new",
        "swallowed_error": f"{recovery.get('errors', 0)} error(s), "
                           f"{recovery.get('recovered', 0)} recovered",
        "blind_write": f"{effects.get('writes_before_any_read', 0)} write(s) "
                       f"before any successful read",
        "budget_pressure": f"{termination.get('steps')} of "
                           f"{termination.get('max_steps')} step(s) used",
        "undeclared_tools": f"{grounding.get('undeclared_tool_calls', 0)} "
                            f"call(s) to tools the agent was not offered",
        "invented_arguments": f"{grounding.get('arguments_without_source', 0)} "
                              f"argument value(s) with no source in the trace",
        "schema_violation": f"{schema.get('violations', 0)} argument "
                            f"violation(s) against the declared parameters",
        "false_success": "the answer claims completion while nothing was written",
    }
    return evidence.get(flag, "")


#: process sub-checks that can be unmeasurable, and how to tell.  Each is
#: (key, predicate over the process side, why it could not be measured).
def _process_unmeasurable(side: dict) -> list[tuple[str, str]]:
    """Process checks the trace did not give enough information to compute."""
    rows: list[tuple[str, str]] = []
    schema = side.get("schema") or {}
    if schema.get("measurable") is False:
        rows.append((
            "schema_validity",
            _clean(schema.get("note"))
            or "no parameter schemas were declared, so arguments could not "
               "be typechecked",
        ))
    grounding = side.get("grounding") or {}
    if grounding.get("schema_checked") is False:
        rows.append((
            "tool_grounding",
            "no tool list was declared, so a call to an unoffered tool could "
            "not be detected — an unchecked call is not a valid one",
        ))
    false_success = side.get("false_success") or {}
    if false_success.get("measurable") is False:
        rows.append((
            "false_success",
            "no write-capable tool was declared, so a completion claim could "
            "not be checked against what was written",
        ))
    termination = side.get("termination") or {}
    if termination.get("declared") is False:
        rows.append((
            "termination",
            "the run did not declare why it stopped, so an agent that "
            "decided it was done cannot be told from one the harness cut off",
        ))
    return rows


def _process_findings(
    report: dict,
    task: str,
    location: Optional[str],
    prefix: str,
) -> list[dict]:
    """Findings for the candidate run's process, plus what went unmeasured.

    A flag the baseline also raised is reported at ``informational``: it is a
    pre-existing habit, not something this change introduced, and grading it
    as a new pathology would make every run of an already-noisy agent look
    like a fresh problem.
    """
    process = report.get("process") or {}
    side_b = process.get("b") or {}
    side_a = process.get("a") or {}
    if not side_b:
        return []
    agent = report.get("b", {}).get("agent", {}).get("name")
    raised_b = sorted((side_b.get("gap") or {}).get("raised") or [])
    raised_a = set((side_a.get("gap") or {}).get("raised") or [])

    findings: list[dict] = []
    for flag in raised_b:
        pre_existing = flag in raised_a
        phrase = _PROCESS_RULE_TEXT.get(flag, flag.replace("_", " "))
        evidence = _process_evidence(side_b, flag)
        findings.append(_finding(
            id=f"{prefix}.process.{task}.{flag}",
            rule=_rule_id("process", flag),
            suite="agentdiff.process",
            case=f"{task}/{flag}",
            title=f"{task}: {phrase}",
            message=(
                f"{agent or 'the candidate'} {phrase}"
                + (f" ({evidence})" if evidence else "")
                + "."
                + (" The baseline does this too, so it is pre-existing rather "
                   "than new." if pre_existing else "")
            ),
            severity="informational" if pre_existing else "pathology",
            status="failed",
            gating=not pre_existing,
            task=task,
            location=location,
            fingerprint=f"process/{flag}/{task}",
            properties={"flag": flag, "pre_existing": pre_existing,
                        "gap_verdict": (side_b.get("gap") or {}).get("verdict")},
        ))

    for key, why in _process_unmeasurable(side_b):
        findings.append(_finding(
            id=f"{prefix}.process.{task}.unmeasurable.{key}",
            rule=_rule_id("unmeasurable", "process"),
            suite="agentdiff.process",
            case=f"{task}/{key}",
            title=f"{task}: {key.replace('_', ' ')} could not be measured",
            message=f"{key.replace('_', ' ')} was not evaluated: {why}.",
            severity="informational",
            status="skipped",
            gating=True,
            task=task,
            location=location,
            fingerprint=f"unmeasurable/{key}/{task}",
            properties={"check": key},
        ))
    return findings


def _gate_findings(
    gate: dict,
    reports: Optional[list[dict]],
    trace_paths: Optional[dict],
) -> list[dict]:
    reports_by_task = {r["task"]["id"]: r for r in (reports or [])}
    findings: list[dict] = []

    for check in gate.get("checks", []):
        name = check.get("name", "check")
        why = _criterion_unmeasurable(gate, check)
        if why:
            status, severity = "skipped", "informational"
            message = (
                f"{name} was not evaluated: {why}. Reported skipped, not "
                f"passed — the gate's verdict does not rest on it. "
                f"({_clean(check.get('detail'), 300)})"
            )
        elif not check.get("pass", False):
            status, severity = "failed", "regression"
            message = _clean(check.get("detail"))
        else:
            status, severity = "passed", "informational"
            message = _clean(check.get("detail"))
        findings.append(_finding(
            id=f"gate.criterion.{name}",
            rule=_rule_id("gate", name),
            suite="agentdiff.gate",
            case=name,
            title=f"gate criterion {name}",
            message=message,
            severity=severity,
            status=status,
            gating=True,
            fingerprint=f"gate/{name}",
            properties={
                "baseline": check.get("baseline"),
                "candidate": check.get("candidate"),
                "threshold": check.get("threshold"),
                "significant": check.get("significant"),
            },
        ))

    for row in gate.get("reports_summary", []):
        task = row["task"]
        report = reports_by_task.get(task)
        agent = (report or {}).get("b", {}).get("agent", {}).get("name") \
            or gate.get("candidate_agent")
        location = _lookup_path(trace_paths, task, agent)
        duration = None
        fingerprint = None
        detail = ""
        if report:
            duration = ((report.get("b") or {}).get("totals") or {}).get("latency_s")
            divergences = report.get("divergences") or []
            if divergences:
                fingerprint = issue_fingerprint(report, divergences[0])
                detail = f"First divergence: {divergences[0].get('summary', '')} "
            detail += (report.get("attribution") or {}).get("explanation", "")
        regressed = bool(row.get("regressed"))
        if regressed:
            message = (
                f"{gate.get('baseline_agent')} solved {task} and "
                f"{agent} did not. {detail}"
            )
        else:
            message = (
                f"baseline "
                f"{'solved' if row.get('baseline_success') else 'failed'} / "
                f"candidate "
                f"{'solved' if row.get('candidate_success') else 'failed'} "
                f"{task}."
            )
        findings.append(_finding(
            id=f"gate.task.{task}",
            rule=_rule_id("gate", "task_regression"),
            suite="agentdiff.tasks",
            case=task,
            title=f"{task}: {'regressed' if regressed else 'no regression'}",
            message=message,
            severity="regression" if regressed else "informational",
            status="failed" if regressed else "passed",
            # Evidence, not policy: whether a per-task regression is
            # tolerable is what --max-success-drop already answers.
            gating=False,
            task=task,
            location=location,
            duration_s=duration,
            fingerprint=fingerprint,
            properties={
                "baseline_success": row.get("baseline_success"),
                "candidate_success": row.get("candidate_success"),
            },
        ))
        if report:
            findings.extend(_process_findings(report, task, location, "gate"))
    return findings


def _conformance_findings(
    suite: dict,
    trace_paths: Optional[dict],
) -> list[dict]:
    findings: list[dict] = []
    for check in suite.get("checks", []):
        task = check["task"]
        agent = check.get("agent")
        location = _lookup_path(trace_paths, task, agent)
        verdict = check.get("verdict", "conformant")
        report = check.get("report") or {}
        duration = ((report.get("b") or {}).get("totals") or {}).get("latency_s")
        divergences = report.get("divergences") or []
        fingerprint = (
            issue_fingerprint(report, divergences[0]) if report and divergences
            else f"conformance/{verdict}/{task}"
        )
        if verdict == "violation":
            status, severity = "failed", "regression"
        elif verdict == "deviation":
            status, severity = "failed", "pathology"
        else:
            status, severity = "passed", "informational"
        deviations = check.get("deviations") or []
        first = (
            f" First deviation: [{deviations[0]['kind']}] "
            f"{deviations[0]['summary']}" if deviations else ""
        )
        findings.append(_finding(
            id=f"conformance.task.{task}",
            rule=_rule_id("conformance", verdict),
            suite="agentdiff.conformance",
            case=task,
            title=f"{task}: {verdict}",
            message=f"{check.get('narrative', '')}{first}",
            severity=severity,
            status=status,
            gating=True,
            task=task,
            location=location,
            duration_s=duration,
            fingerprint=fingerprint,
            properties={
                "verdict": verdict,
                "conformance": check.get("conformance"),
                "reference_agent": check.get("reference_agent"),
            },
        ))
        if report:
            findings.extend(_process_findings(report, task, location,
                                              "conformance"))

    for task in suite.get("missing_reference", []):
        findings.append(_finding(
            id=f"conformance.missing_reference.{task}",
            rule=_rule_id("unmeasurable", "reference"),
            suite="agentdiff.conformance",
            case=f"{task}/reference",
            title=f"{task}: no reference trajectory",
            message=(
                f"{task} was run but has no reference trajectory, so it was "
                f"not checked. Skipped rather than passed: an unchecked run "
                f"is not a conformant one."
            ),
            severity="informational",
            status="skipped",
            gating=True,
            task=task,
            location=_lookup_path(trace_paths, task, None),
            fingerprint=f"unmeasurable/missing_reference/{task}",
        ))
    for task in suite.get("unused_reference", []):
        findings.append(_finding(
            id=f"conformance.unused_reference.{task}",
            rule=_rule_id("unmeasurable", "reference"),
            suite="agentdiff.conformance",
            case=f"{task}/run",
            title=f"{task}: no run to check",
            message=(
                f"{task} has a reference trajectory but no run to check "
                f"against it; the run set is incomplete."
            ),
            severity="informational",
            status="skipped",
            gating=True,
            task=task,
            fingerprint=f"unmeasurable/unused_reference/{task}",
        ))
    return findings


def collect_findings(
    result: dict,
    reports: Optional[list[dict]] = None,
    trace_paths: Optional[dict] = None,
) -> list[dict]:
    """Normalise a gate or conformance payload into a flat list of findings.

    Every emitter renders this one shape, so JUnit, SARIF, annotations and
    the job summary can never disagree about what was found — they differ
    only in how they say it.

    ``reports`` is the per-task comparison list the gate was built from.  It
    is optional because ``gate.json`` on its own is enough for the criteria
    and the per-task verdicts; passing it adds divergence summaries, issue
    fingerprints, per-task timing and the process findings.  A conformance
    suite already carries its reports inside each check.
    """
    kind = detect_kind(result)
    if kind == "gate":
        return _gate_findings(result, reports, trace_paths)
    return _conformance_findings(result, trace_paths)


# --------------------------------------------------------------------------
# severity policy and exit codes
# --------------------------------------------------------------------------


def _at_or_above(severity: str, threshold: Optional[str]) -> bool:
    if threshold is None:
        return False
    return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(threshold)


def gating_findings(findings: Iterable[dict], fail_on: str = DEFAULT_FAIL_ON) -> list[dict]:
    """The findings that would set the exit code at this threshold."""
    if fail_on not in FAIL_ON_THRESHOLDS:
        raise ValueError(
            f"unknown --fail-on value {fail_on!r}; expected one of "
            f"{', '.join(FAIL_ON_CHOICES)}"
        )
    threshold = FAIL_ON_THRESHOLDS[fail_on]
    return [
        f for f in findings
        if f["gating"]
        and f["status"] != "passed"
        and _at_or_above(f["severity"], threshold)
    ]


def exit_code(
    result: dict,
    reports: Optional[list[dict]] = None,
    fail_on: str = DEFAULT_FAIL_ON,
    trace_paths: Optional[dict] = None,
) -> int:
    """Process exit code under a severity threshold.

    ==== =========================================================
    code meaning
    ==== =========================================================
    0    no gating finding at or above the threshold
    1    at least one gating finding at or above the threshold
    2    usage or data error — raised by the CLI, never here
    ==== =========================================================

    Thresholds, from loosest to strictest:

    ``never``
        Report only.  Artifacts are still written; the build stays green.
    ``regression`` (default)
        A failed gate criterion or a conformance violation.  This is exactly
        the pre-existing gate behaviour, so adopting the flag changes
        nothing until a team chooses to tighten it.
    ``pathology``
        Also fails on process pathologies the outcome hid — a run that
        looped, swallowed an error or wrote blind — and on runs that left
        the reference path.
    ``any``
        Also fails when a check could not be measured.  The strictest
        reading: nothing unproven ships.

    A per-task regression is reported everywhere but does not itself set the
    exit code; ``--max-success-drop`` is where a team already stated how
    much of that they will accept.
    """
    findings = collect_findings(result, reports, trace_paths)
    return EXIT_FINDINGS if gating_findings(findings, fail_on) else EXIT_OK


# --------------------------------------------------------------------------
# JUnit XML
# --------------------------------------------------------------------------


def _fmt_time(seconds: float) -> str:
    return f"{float(seconds):.3f}"


def _junit_element(findings: list[dict], fail_on: str = DEFAULT_FAIL_ON) -> ET.Element:
    threshold = FAIL_ON_THRESHOLDS[fail_on] if fail_on in FAIL_ON_THRESHOLDS \
        else FAIL_ON_THRESHOLDS[DEFAULT_FAIL_ON]

    suites: dict[str, list[dict]] = {}
    for finding in findings:
        suites.setdefault(finding["suite"], []).append(finding)

    root = ET.Element("testsuites", {"name": "agentdiff"})
    total = failures = skipped = 0
    total_time = 0.0
    any_time = False

    for suite_name, rows in suites.items():
        suite_failures = sum(
            1 for f in rows
            if f["status"] == "failed" and _at_or_above(f["severity"], threshold)
        )
        suite_skipped = sum(1 for f in rows if f["status"] == "skipped")
        timed = [f["duration_s"] for f in rows if f["duration_s"] is not None]
        suite_attrs = {
            "name": suite_name,
            "tests": str(len(rows)),
            "failures": str(suite_failures),
            "skipped": str(suite_skipped),
            "errors": "0",
        }
        if timed:
            # Only from the data: a suite with no logged latency gets no
            # time attribute rather than a fabricated 0.
            suite_attrs["time"] = _fmt_time(sum(timed))
            total_time += sum(timed)
            any_time = True
        suite_el = ET.SubElement(root, "testsuite", suite_attrs)

        for f in rows:
            case_attrs = {"classname": suite_name, "name": f["case"]}
            if f["duration_s"] is not None:
                case_attrs["time"] = _fmt_time(f["duration_s"])
            case = ET.SubElement(suite_el, "testcase", case_attrs)
            if f["status"] == "skipped":
                ET.SubElement(case, "skipped", {"message": f["message"]})
            elif f["status"] == "failed" and _at_or_above(f["severity"], threshold):
                failure = ET.SubElement(case, "failure", {
                    "type": f["severity"],
                    "message": f["title"],
                })
                failure.text = f["message"]
            elif f["status"] == "failed":
                # Real, but below the --fail-on threshold: shown, not red.
                out = ET.SubElement(case, "system-out")
                out.text = (
                    f"[{f['severity']}] {f['message']} "
                    f"(reported below the --fail-on={fail_on} threshold)"
                )
            elif f["message"]:
                out = ET.SubElement(case, "system-out")
                out.text = f["message"]

        total += len(rows)
        failures += suite_failures
        skipped += suite_skipped

    root.set("tests", str(total))
    root.set("failures", str(failures))
    root.set("skipped", str(skipped))
    root.set("errors", "0")
    if any_time:
        root.set("time", _fmt_time(total_time))
    return root


def to_junit(
    result: dict,
    reports: Optional[list[dict]] = None,
    trace_paths: Optional[dict] = None,
    fail_on: str = DEFAULT_FAIL_ON,
) -> str:
    """Render findings as JUnit XML.

    One ``<testcase>`` per gate criterion, per task, and per process
    pathology, grouped into ``agentdiff.gate`` / ``agentdiff.tasks`` /
    ``agentdiff.process`` suites (``agentdiff.conformance`` for a check
    suite) so a dashboard's grouping matches how the findings were produced.

    ``<skipped>`` means the check produced no evidence — a disabled
    criterion, an undefined threshold, a process check the trace did not
    declare enough to compute.  It is deliberately not ``<failure>`` (it is
    not a regression) and deliberately not a bare pass (nothing was
    verified).

    ``fail_on`` decides which findings render as ``<failure>``; anything
    real but below the threshold is still emitted, in ``<system-out>``, so
    tightening the threshold later reveals findings that were visible all
    along rather than new ones.

    No ``timestamp`` attribute is written and ``time`` appears only where
    the trace logged latency, so two runs over one input produce identical
    bytes.
    """
    findings = collect_findings(result, reports, trace_paths)
    root = _junit_element(findings, fail_on)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


# --------------------------------------------------------------------------
# SARIF 2.1.0
# --------------------------------------------------------------------------


def _sarif_rules(findings: list[dict]) -> list[dict]:
    """One rule per finding kind actually present, sorted by id."""
    worst: dict[str, str] = {}
    for f in findings:
        current = worst.get(f["rule"])
        if current is None or SEVERITY_ORDER.index(f["severity"]) > \
                SEVERITY_ORDER.index(current):
            worst[f["rule"]] = f["severity"]

    rules = []
    for rule_id in sorted(worst):
        _, category, key = rule_id.split("/", 2)
        rules.append({
            "id": rule_id,
            "name": "".join(part.capitalize()
                            for part in f"{category}_{key}".split("_")),
            "shortDescription": {"text": _clean(f"{category}: "
                                                f"{key.replace('_', ' ')}", 200)},
            "fullDescription": {"text": _clean(_rule_text(category, key))},
            "defaultConfiguration": {"level": SARIF_LEVEL[worst[rule_id]]},
            "properties": {"category": category,
                           "tags": ["agentdiff", category]},
        })
    return rules


def to_sarif(
    result: dict,
    reports: Optional[list[dict]] = None,
    trace_paths: Optional[dict] = None,
) -> str:
    """Render findings as SARIF 2.1.0 for GitHub code scanning.

    Three choices are worth stating, because SARIF makes it easy to lie in
    all three places.

    **Locations.**  There is no source file behind an agent regression, and
    inventing ``startLine: 1`` in someone's ``agent.py`` would point a
    reviewer at code that is very likely not the cause.  The honest artifact
    is the *trace* the finding was computed from, so ``physicalLocation``
    names the trace file and carries no region at all.  When no trace path
    was supplied, the result gets a ``logicalLocation`` naming the task and
    no physical location — SARIF permits that, and it is better than a
    fabricated one.

    **Levels.**  ``error`` only for a real regression (a failed gate
    criterion, a task the baseline solved and the candidate did not, a
    conformance violation); ``warning`` for a process pathology, which is a
    run that met its oracle unsoundly; ``note`` for informational findings,
    including a pathology the baseline already had.

    **Fingerprints.**  ``partialFingerprints`` carries the issue fingerprint
    from ``deepcompare.issues`` — the same stable, hash-free signature a
    team can put in ``.agentdiffignore``.  GitHub uses it to recognise a
    finding across runs, so a recurring divergence stays one alert instead
    of reappearing on every push.

    Only findings that are not ``passed`` become results: SARIF results are
    findings, and shipping every green check into code scanning would bury
    the ones that matter.  Skipped checks *are* included, as
    ``kind: notApplicable`` with ``level: none`` (the spec requires ``none``
    for any non-``fail`` kind), so "not measured" survives the trip.
    """
    findings = collect_findings(result, reports, trace_paths)
    rules = _sarif_rules(findings)
    rule_index = {rule["id"]: i for i, rule in enumerate(rules)}

    results = []
    for f in findings:
        if f["status"] == "passed":
            continue
        entry: dict = {
            "ruleId": f["rule"],
            "ruleIndex": rule_index[f["rule"]],
            "message": {"text": f["message"] or f["title"]},
        }
        if f["status"] == "failed":
            entry["kind"] = "fail"
            entry["level"] = SARIF_LEVEL[f["severity"]]
        else:
            entry["kind"] = "notApplicable"
            entry["level"] = "none"
        if f["location"]:
            entry["locations"] = [{
                "physicalLocation": {
                    "artifactLocation": {"uri": f["location"]},
                },
            }]
        elif f["task"]:
            entry["locations"] = [{
                "logicalLocations": [
                    {"name": f["task"], "kind": "task"},
                ],
            }]
        prints = {"agentdiffFinding/v1": f["id"]}
        if f["fingerprint"]:
            prints["agentdiffIssue/v1"] = f["fingerprint"]
        entry["partialFingerprints"] = prints
        entry["properties"] = {
            "severity": f["severity"],
            "status": f["status"],
            "gating": f["gating"],
            **({"task": f["task"]} if f["task"] else {}),
            **f["properties"],
        }
        results.append(entry)

    kind = detect_kind(result)
    payload = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {"driver": {
                "name": TOOL_NAME,
                "informationUri": "https://github.com/agentdiff/agentdiff",
                "rules": rules,
            }},
            "columnKind": "utf16CodeUnits",
            "results": results,
            "properties": {
                "analysis": kind,
                "verdict": result.get("verdict")
                if kind == "gate"
                else ("fail" if result.get("violations") else "pass"),
                "findings": len(findings),
                "unmeasurable": sum(1 for f in findings
                                    if f["status"] == "skipped"),
            },
        }],
    }
    # No invocation block and no timestamps: the artifact must be diffable
    # between two runs.
    return json.dumps(payload, indent=2, ensure_ascii=False,
                      sort_keys=False) + "\n"


# --------------------------------------------------------------------------
# GitHub Actions
# --------------------------------------------------------------------------


def to_github_annotations(
    result: dict,
    reports: Optional[list[dict]] = None,
    trace_paths: Optional[dict] = None,
) -> str:
    """Render findings as GitHub Actions workflow commands.

    ``::error file=…::message`` puts the finding on the PR itself, which is
    the only place a reviewer reliably looks.  ``file=`` names the trace,
    and no ``line=`` is emitted: there is no line to point at, and a made-up
    one would anchor the annotation to an arbitrary byte of JSON.  GitHub
    renders a file-only annotation against the file as a whole.

    Passed findings produce no annotation — the PR is not a place to
    celebrate — but skipped ones do, at ``notice``, because "this was never
    measured" is exactly the thing a reviewer should see before merging.
    """
    findings = collect_findings(result, reports, trace_paths)
    lines: list[str] = []
    for f in findings:
        if f["status"] == "passed":
            continue
        level = ANNOTATION_LEVEL[f["severity"]]
        props = [f"title={_gha_property(TOOL_NAME + ': ' + f['title'])}"]
        if f["location"]:
            props.insert(0, f"file={_gha_property(f['location'])}")
        prefix = "not measured — " if f["status"] == "skipped" else ""
        lines.append(f"::{level} {','.join(props)}::{_gha_data(prefix + f['message'])}")
    return "\n".join(lines) + ("\n" if lines else "")


def to_job_summary(
    result: dict,
    reports: Optional[list[dict]] = None,
    trace_paths: Optional[dict] = None,
    fail_on: str = DEFAULT_FAIL_ON,
) -> str:
    """Render the run's front page for ``$GITHUB_STEP_SUMMARY``.

    Ordered the way a reader triages: the verdict, then what would fail the
    build at this threshold, then what was never measured, then everything
    else.  Unmeasurable checks get their own section rather than a footnote,
    because a gate that passes on checks it never ran should have to say so
    where people look.
    """
    findings = collect_findings(result, reports, trace_paths)
    kind = detect_kind(result)
    gating = gating_findings(findings, fail_on)
    code = EXIT_FINDINGS if gating else EXIT_OK
    skipped = [f for f in findings if f["status"] == "skipped"]
    failed = [f for f in findings if f["status"] == "failed"]

    if kind == "gate":
        headline = (
            f"# AgentDiff gate: {'✅ PASS' if result.get('verdict') == 'pass' else '❌ FAIL'}"
        )
        subtitle = (
            f"Baseline **{_md(result.get('baseline_agent'))}** vs candidate "
            f"**{_md(result.get('candidate_agent'))}** across "
            f"{result.get('tasks', 0)} task(s)."
        )
    else:
        violations = result.get("violations") or []
        headline = f"# AgentDiff conformance: {'❌ FAIL' if violations else '✅ PASS'}"
        subtitle = _md(result.get("narrative", ""))

    lines = [
        headline,
        "",
        subtitle,
        "",
        f"`--fail-on {fail_on}` → exit **{code}** "
        f"({len(gating)} gating finding(s) at or above `"
        f"{FAIL_ON_THRESHOLDS[fail_on] or 'nothing'}`).",
        "",
    ]

    if kind == "gate":
        lines += ["## Criteria", "",
                  "| criterion | result | baseline | candidate | threshold |",
                  "|---|---|---|---|---|"]
        for f in findings:
            if f["suite"] != "agentdiff.gate":
                continue
            mark = {"passed": "✅ pass", "failed": "❌ fail",
                    "skipped": "⚠️ skipped (not measured)"}[f["status"]]
            props = f["properties"]
            threshold = props.get("threshold")
            lines.append(
                f"| `{_md(f['case'])}` | {mark} | {_md(props.get('baseline'))} | "
                f"{_md(props.get('candidate'))} | "
                f"{'—' if threshold is None else _md(threshold)} |"
            )
        lines.append("")

    if failed:
        lines += ["## Findings", "",
                  "| severity | finding | detail |", "|---|---|---|"]
        for f in sorted(failed, key=lambda f: (
                -SEVERITY_ORDER.index(f["severity"]), f["id"])):
            lines.append(
                f"| {f['severity']} | {_md(f['title'])} | {_md(f['message'])} |"
            )
        lines.append("")

    if skipped:
        lines += [
            "## Not measured",
            "",
            "These checks produced no evidence. They are reported skipped, "
            "not passed — a gate must not draw confidence from a check it "
            "never ran.",
            "",
        ]
        for f in sorted(skipped, key=lambda f: f["id"]):
            lines.append(f"- **{_md(f['title'])}** — {_md(f['message'])}")
        lines.append("")

    lines += ["## Exit codes", "", "| code | meaning |", "|---|---|"]
    for value in sorted(EXIT_CODES):
        lines.append(f"| {value} | {EXIT_CODES[value]} |")
    lines += [
        "",
        "A per-task regression is reported here and in the test report but "
        "does not by itself set the exit code; the gate thresholds are where "
        "that policy lives.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# writing them out
# --------------------------------------------------------------------------


def write_ci_artifacts(
    result: dict,
    out_dir: Union[str, Path],
    reports: Optional[list[dict]] = None,
    trace_paths: Optional[dict] = None,
    junit: Optional[str] = None,
    sarif: Optional[str] = None,
    summary: Optional[str] = None,
    annotations: bool = False,
    fail_on: str = DEFAULT_FAIL_ON,
    stream=None,
    env: Optional[dict] = None,
) -> list[Path]:
    """Write the requested artifacts; return the paths written.

    Relative names resolve inside ``out_dir`` so a CI step can say
    ``--junit junit.xml`` and find it beside ``gate.json``.  When the
    workflow exports ``GITHUB_STEP_SUMMARY``, the job summary is appended
    there as well — appended, not overwritten, because other steps write to
    the same file.
    """
    environ = os.environ if env is None else env
    base = Path(out_dir)
    written: list[Path] = []

    def _write(name: str, text: str) -> None:
        path = Path(name)
        if not path.is_absolute():
            path = base / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    if junit:
        _write(junit, to_junit(result, reports, trace_paths, fail_on))
    if sarif:
        _write(sarif, to_sarif(result, reports, trace_paths))
    summary_text = None
    if summary:
        summary_text = to_job_summary(result, reports, trace_paths, fail_on)
        _write(summary, summary_text)
    if annotations:
        text = to_github_annotations(result, reports, trace_paths)
        if text:
            print(text, end="", file=stream)
        step_summary = environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            if summary_text is None:
                summary_text = to_job_summary(result, reports, trace_paths,
                                              fail_on)
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write(summary_text)
            written.append(Path(step_summary))
    return written
