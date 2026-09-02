"""The eval reasoning layer: an executive reading of ONE trace.

Everything else in the engine compares.  This module *understands*: for
a single run it says what happened, what the answer rests on, why the
run ended the way it did, what that means, and what to take forward —
mechanically, from the trace's own events, with every finding citing
the step and quote it stands on.  An LLM may narrate a reading under
the covenant (:mod:`deepcompare.narrate`), but the reading exists and
is complete without one.

Two rules from the literature shape it:

* **Observables outrank rationales.**  Reasoning models verbalize what
  actually drove them only a minority of the time (Anthropic 2025;
  Arcuschin et al. 2025), so a step's *stated* reason is a claim, not
  evidence.  Every finding carries an ``evidence_class`` — ``observable``
  (a call made, an argument passed, an output returned, an answer
  emitted, a declared termination), ``annotation`` (a quality or note
  mark someone wrote on the log) or ``stated`` (the agent's own words) —
  and the reading's confidence is set by the strongest class that
  supports its central findings, never by how articulate the agent was.
* **Provenance before judgement.**  The answer's typed values are
  traced back to the earliest step in this run that carried them; a
  value with no upstream source is named as such.  "Rests on" is a
  list of (value, first step, source), not a paraphrase.

Determinism is absolute: no clock, no randomness, no model.
"""

from __future__ import annotations

from typing import Optional

from . import process as _process
from .align import jaccard
from .semantic import _step_intent, extract_from_text
from .trace import Trajectory

READING_VERSION = 1

#: word-overlap at or above which a step's output is judged to feed the
#: final answer (the same measurable link the causal account uses)
FEEDS_ANSWER_FLOOR = 0.2

_INTENT_VERB = {
    "frame": "framed the task", "acquire": "gathered information",
    "decide": "chose a direction", "verify": "checked its work",
    "transform": "acted with a tool", "commit": "committed to an answer",
}

_FLAG_MEANING = {
    "false_success": "claimed success the evidence does not support",
    "looped": "looped over the same steps",
    "loop_block": "spent a block of steps going in circles",
    "repeated_calls": "made the same call more than once",
    "no_information_steps": "took steps that returned nothing new",
    "swallowed_error": "hit an error and carried on as if it had not",
    "blind_write": "wrote before reading anything that justified the write",
    "budget_pressure": "ran close to its step budget",
    "undeclared_tools": "called tools it had not declared",
    "invented_arguments": "passed argument values with no source in the trace",
    "schema_violation": "called a tool with arguments that break its schema",
}

_FLAG_ACTION = {
    "false_success": "grade against the evidence, not the agent's claim",
    "looped": "add a loop guard: the same call twice should change strategy",
    "loop_block": "add a loop guard: the same call twice should change strategy",
    "repeated_calls": "cache or dedupe repeated calls; a repeat should be deliberate",
    "no_information_steps": "make each step's expected information explicit before taking it",
    "swallowed_error": "stop on a tool error unless a recovery is recorded",
    "blind_write": "require a read that justifies the write before any write",
    "budget_pressure": "raise the budget or shorten the plan — the constraint binds",
    "undeclared_tools": "declare every tool the agent may call",
    "invented_arguments": "ground every argument in a prior observation or the prompt",
    "schema_violation": "validate arguments against the declared schema before calling",
}


class _Evidence:
    """A ledger of quotes and events the reading's findings cite."""

    def __init__(self, traj: Trajectory) -> None:
        self.traj = traj
        self.items: list = []

    def span(self, step: int, field: str, note: str, cls: str) -> str:
        text = getattr(self.traj.steps[step], field, "") or ""
        quote = text[:120]
        eid = f"R{len(self.items) + 1}"
        self.items.append({"id": eid, "type": "span", "step": step,
                           "field": field, "quote": quote, "note": note,
                           "evidence_class": cls})
        return eid

    def fact(self, path: str, value, note: str, cls: str) -> str:
        eid = f"R{len(self.items) + 1}"
        self.items.append({"id": eid, "type": "fact", "path": path,
                           "value": value, "note": note, "evidence_class": cls})
        return eid


def _phases(steps: list) -> list:
    """Contiguous runs of one intent, in order: the run's outline."""
    phases: list = []
    for i, step in enumerate(steps):
        intent = _step_intent(step)
        if phases and phases[-1]["intent"] == intent:
            phases[-1]["steps"].append(i)
        else:
            phases.append({"intent": intent, "steps": [i]})
    for phase in phases:
        names = []
        for i in phase["steps"]:
            name = steps[i].name or steps[i].type
            if name not in names:
                names.append(name)
        n = len(phase["steps"])
        phase["summary"] = (f"{n} step{'s' if n != 1 else ''} — "
                            f"{_INTENT_VERB.get(phase['intent'], phase['intent'])}"
                            f" ({', '.join(names[:4])}"
                            f"{', …' if len(names) > 4 else ''})")
    return phases


def _rests_on(traj: Trajectory, answer_idx: int, expected: Optional[str]) -> list:
    """The answer's typed values, each traced to the earliest step in this
    run that carried it.  ``source`` names that step; ``None`` when no
    earlier step carried the value — the answer asserts something the
    run never observed."""
    answer_text = traj.steps[answer_idx].output or traj.steps[answer_idx].input or ""
    expected_norm = {norm for _, _, norm in extract_from_text(expected or "")}
    out: list = []
    seen = set()
    for kind, value, norm in extract_from_text(answer_text):
        if (kind, norm) in seen:
            continue
        seen.add((kind, norm))
        first = None
        for i in range(answer_idx):
            text = f"{traj.steps[i].input}\n{traj.steps[i].output}"
            if any(n == norm for _, _, n in extract_from_text(text)):
                first = i
                break
        entry = {
            "kind": kind, "value": value,
            "first_step": first,
            "source": (traj.steps[first].name or traj.steps[first].type) if first is not None else None,
            "matches_expected": (norm in expected_norm) if expected_norm else None,
        }
        out.append(entry)
    return out


def read_trace(traj: Trajectory, *, expected: Optional[str] = None) -> dict:
    """The reading of one run.  ``expected`` is the task's gold answer
    when there is one (defaults to the trace's own ``task.expected``)."""
    steps = traj.steps
    if not steps:
        return {"version": READING_VERSION, "error": "the trace has no steps"}
    if expected is None:
        expected = getattr(traj.task, "expected", None)
    ev = _Evidence(traj)
    proc = _process.analyse(traj)
    gap = proc.get("gap") or {}
    term = proc.get("termination") or {}
    repeats = proc.get("repeats") or {}
    recovery = proc.get("recovery") or {}
    grounding = proc.get("grounding") or {}
    answer_idx = len(steps) - 1
    answer_text = steps[answer_idx].output or steps[answer_idx].input or ""

    repeated = {int(i) for i in (repeats.get("repeated_steps") or [])
                if isinstance(i, int)}
    no_info = {d.get("index") if isinstance(d, dict) else d
               for d in (repeats.get("no_information_detail") or [])}
    error_steps = {e.get("index") for e in (recovery.get("error_steps") or [])
                   if isinstance(e, dict)}
    invented = {r.get("index") for r in (grounding.get("invented_arguments") or [])
                if isinstance(r, dict)}

    rests_on = _rests_on(traj, answer_idx, expected)
    # typed provenance beats lexical overlap: the step that first carried
    # a value the answer asserts fed the answer, whatever its word overlap
    carriers = {r["first_step"] for r in rests_on if r["first_step"] is not None}

    # --- what happened: one line per step, role decided by observables
    what_happened: list = []
    for i, step in enumerate(steps):
        intent = _step_intent(step)
        carrier = step.output or ""
        feeds = i < answer_idx and (
            i in carriers
            or (bool(carrier.strip())
                and jaccard(carrier, answer_text) >= FEEDS_ANSWER_FLOOR))
        if i == answer_idx:
            role = "answer"
        elif i in error_steps or step.error is True:
            role = "error"
        elif i in repeated:
            role = "repeat"
        elif i in no_info:
            role = "no_information"
        elif feeds:
            role = "feeds_answer"
        elif intent in ("frame", "decide", "verify"):
            role = intent
        else:
            role = "dead_end" if intent in ("acquire", "transform") else intent
        cls = "observable" if step.type in ("tool_call", "search", "retrieve",
                                            "read", "answer") else "stated"
        what_happened.append({
            "step": i, "type": step.type, "name": step.name, "intent": intent,
            "role": role, "feeds_answer": feeds,
            "invented_argument": i in invented,
            "evidence": ev.span(i, "output" if step.output else "input",
                                f"step {i} {role}", cls),
        })

    # --- why it ended: the outcome, on its grounds
    success = traj.outcome.success
    grounds: list = []
    if expected:
        matched = [r for r in rests_on if r["matches_expected"] is True]
        contradicted = [r for r in rests_on if r["matches_expected"] is False]
        if matched:
            grounds.append(ev.fact("outcome.answer", answer_text[:120],
                                   "the answer carries the expected value "
                                   + ", ".join(r["value"] for r in matched),
                                   "observable"))
        if contradicted:
            grounds.append(ev.fact("outcome.answer", answer_text[:120],
                                   "the answer asserts "
                                   + ", ".join(r["value"] for r in contradicted)
                                   + " where the expected answer does not",
                                   "observable"))
    reason = term.get("reason")
    grounds.append(ev.fact("outcome.termination", reason,
                           "declared termination" if term.get("declared")
                           else "termination not declared by the harness",
                           "observable"))
    carries = any(r["matches_expected"] for r in rests_on)
    contradicts = any(r["matches_expected"] is False for r in rests_on)
    if not expected:
        verdict_basis = ("no expected answer is recorded — the outcome is the "
                         "grader's verdict alone")
    elif success and carries:
        verdict_basis = "the answer carries the expected value(s)"
    elif not success and carries and not contradicts:
        # right words, wrong deed: the sentence matches, the run failed —
        # either the grader is wrong or the answer describes something the
        # run did not actually do (an invented entity, a write that never
        # happened); the findings say which
        verdict_basis = ("the answer carries the expected value yet the run "
                         "failed — the grader, or the deed behind the words, "
                         "is suspect")
    elif contradicts:
        verdict_basis = "the answer contradicts the expected value"
    else:
        verdict_basis = ("the answer and the expected answer share no typed "
                         "value — the verdict rests on the grader's reading "
                         "of the text")
    why_it_ended = {"success": success, "termination": reason,
                    "declared": bool(term.get("declared")),
                    "verdict_basis": verdict_basis, "grounds": grounds}

    # --- what it means: findings, each with its evidence class
    findings: list = []
    for flag in gap.get("raised") or []:
        if flag == "budget_pressure" and not term.get("under_budget_pressure"):
            continue
        steps_for = [i for i in (repeated if flag in ("repeated_calls", "looped", "loop_block")
                                 else no_info if flag == "no_information_steps"
                                 else error_steps if flag == "swallowed_error"
                                 else invented if flag == "invented_arguments"
                                 else set()) if isinstance(i, int)]
        cited = [ev.span(i, "output" if steps[i].output else "input",
                         f"process flag {flag}", "observable")
                 for i in sorted(steps_for)[:3]]
        if not cited:
            cited = [ev.fact(f"process.gap.flags.{flag}", True,
                             f"process flag {flag}", "observable")]
        findings.append({"kind": "pathology", "flag": flag,
                         "statement": f"the run {_FLAG_MEANING.get(flag, flag)}",
                         "steps": sorted(steps_for), "evidence": cited,
                         "evidence_class": "observable"})
    unsourced = [r for r in rests_on if r["first_step"] is None]
    if unsourced:
        findings.append({
            "kind": "unsourced_answer_value",
            "statement": "the answer asserts "
                         + ", ".join(r["value"] for r in unsourced)
                         + " which no earlier step observed",
            "steps": [answer_idx],
            "evidence": [ev.span(answer_idx, "output" if steps[answer_idx].output
                                 else "input", "answer with unsourced value",
                                 "observable")],
            "evidence_class": "observable"})
    intents_seen = {w["intent"] for w in what_happened}
    if "verify" not in intents_seen and answer_idx > 0:
        findings.append({
            "kind": "unverified",
            "statement": "no step checked the work before the answer was committed",
            "steps": [answer_idx],
            "evidence": [ev.fact("reading.phases.intents", sorted(intents_seen),
                                 "intents present in the run", "observable")],
            "evidence_class": "observable"})
    dead_ends = [w["step"] for w in what_happened if w["role"] == "dead_end"]
    productive = [w["step"] for w in what_happened if w["role"] == "feeds_answer"]
    if dead_ends and answer_idx > 0:
        findings.append({
            "kind": "wasted_work",
            "statement": f"{len(dead_ends)} of {answer_idx} step(s) before the "
                         f"answer fed nothing measurable into it",
            "steps": dead_ends,
            "evidence": [ev.span(i, "output" if steps[i].output else "input",
                                 "step whose output reaches nothing later",
                                 "observable") for i in dead_ends[:3]],
            "evidence_class": "observable"})
    annotated = [i for i, s in enumerate(steps) if s.quality in ("weak", "bad")]
    if annotated:
        findings.append({
            "kind": "annotated_weakness",
            "statement": f"{len(annotated)} step(s) carry a weak/bad quality mark",
            "steps": annotated,
            "evidence": [ev.fact(f"steps[{i}].quality", steps[i].quality,
                                 "quality annotation on the log", "annotation")
                         for i in annotated[:3]],
            "evidence_class": "annotation"})

    # --- take forward: one action per finding kind, derived not composed
    take_forward: list = []
    for f in findings:
        if f["kind"] == "pathology":
            action = _FLAG_ACTION.get(f["flag"])
        elif f["kind"] == "unsourced_answer_value":
            action = "trace every asserted value to an observation before answering"
        elif f["kind"] == "unverified":
            action = "add a verification step before committing the answer"
        elif f["kind"] == "wasted_work":
            action = "plan the information each step must yield; drop steps that yield none"
        else:
            action = "review the annotated steps by hand — the marks are someone's judgement, not a measurement"
        if action and not any(t["action"] == action for t in take_forward):
            take_forward.append({"action": action, "because": f["kind"],
                                 "steps": f["steps"]})
    if success is False and not findings:
        take_forward.append({"action": "compare this run against a passing one — "
                                       "nothing in the run alone explains the failure",
                             "because": "clean_failure", "steps": []})

    # --- confidence: by evidence class, never by eloquence
    classes = {f["evidence_class"] for f in findings}
    level = ("high" if "observable" in classes and findings else
             "medium" if findings else "low")
    basis = (f"{sum(1 for f in findings if f['evidence_class'] == 'observable')} "
             f"of {len(findings)} finding(s) rest on observable events"
             if findings else "no finding rises from the trace alone")

    return {
        "version": READING_VERSION,
        "agent": traj.agent.name, "task": traj.task.id,
        "outcome": {"success": success, "termination": reason,
                    "answer": answer_text},
        "phases": _phases(steps),
        "what_happened": what_happened,
        "rests_on": rests_on,
        "why_it_ended": why_it_ended,
        "what_it_means": findings,
        "take_forward": take_forward,
        "confidence": {"level": level, "basis": basis},
        "evidence": ev.items,
        "summary": _summary(traj, what_happened, rests_on, why_it_ended, findings),
    }


def _summary(traj, what_happened, rests_on, why, findings) -> str:
    n = len(traj.steps)
    productive = sum(1 for w in what_happened if w["role"] == "feeds_answer")
    sourced = sum(1 for r in rests_on if r["first_step"] is not None)
    bits = [f"{traj.agent.name} took {n} step(s) and "
            f"{'succeeded' if why['success'] else 'failed'}"
            + (f" ({why['termination']})" if why.get("declared")
               and why["termination"] != "agent_stop" else "") + "."]
    if rests_on:
        bits.append(f"The answer rests on {len(rests_on)} typed value(s), "
                    f"{sourced} of them traced to an earlier step.")
    if productive:
        bits.append(f"{productive} step(s) measurably fed the answer.")
    if findings:
        bits.append("Findings: " + "; ".join(f["statement"] for f in findings[:3])
                    + ("." if len(findings) <= 3 else f"; +{len(findings) - 3} more."))
    return " ".join(bits)


def check_reading(reading: dict, traj: Trajectory) -> list:
    """Verify every span in the reading quotes its step verbatim, and every
    finding cites ledger entries that exist.  Empty = grounded (not true —
    the same boundary as ``check_diagnosis``)."""
    problems: list = []
    known = set()
    for item in reading.get("evidence", []):
        known.add(item.get("id"))
        if item.get("type") != "span":
            continue
        idx = item.get("step")
        if idx is None or not (0 <= idx < len(traj.steps)):
            problems.append(f"{item.get('id')}: step {idx} out of range")
            continue
        text = getattr(traj.steps[idx], item.get("field", ""), None)
        if not isinstance(text, str) or item.get("quote", "") not in text:
            problems.append(f"{item.get('id')}: quote not found in steps[{idx}]"
                            f".{item.get('field')}")
    for f in reading.get("what_it_means", []):
        for ref in f.get("evidence", []):
            if ref not in known:
                problems.append(f"finding {f.get('kind')}: dangling evidence {ref}")
    for w in reading.get("what_happened", []):
        if w.get("evidence") not in known:
            problems.append(f"step {w.get('step')}: dangling evidence")
    return problems
