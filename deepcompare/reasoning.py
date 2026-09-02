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

READING_VERSION = 2

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


OBSERVATION_TYPES = ("search", "retrieve", "read", "tool_call")


def _rests_on(traj: Trajectory, answer_idx: int, expected: Optional[str]) -> list:
    """The answer's typed values, each traced to the earliest step in this
    run that carried it, with a status that says what kind of carrier
    that was (the evidence-tracing distinction between an observation the
    world returned and a value the agent merely said):

    * ``supported`` — first carried by an observation step's OUTPUT
      (search / retrieve / read / tool_call): the world told the agent;
    * ``self_asserted`` — carried only by plan / reason steps, or by what
      the agent typed into a tool: the agent said it, nothing returned it;
    * ``unsupported`` — no earlier step carried it at all;
    * ``stale`` — supported, but the supporting observation was later
      superseded (the same call, same input, returned something else);
    * ``contradicted`` — the run OBSERVED the expected value and answered
      with a different one.
    """
    answer_text = traj.steps[answer_idx].output or traj.steps[answer_idx].input or ""
    expected_atoms = {norm for _, _, norm in extract_from_text(expected or "")}
    observed_norms: set = set()
    for i in range(answer_idx):
        if traj.steps[i].type in OBSERVATION_TYPES:
            observed_norms |= {n for _, _, n in extract_from_text(traj.steps[i].output or "")}
    out: list = []
    seen = set()
    for kind, value, norm in extract_from_text(answer_text):
        if (kind, norm) in seen:
            continue
        seen.add((kind, norm))
        supported_at = None
        asserted_at = None
        for i in range(answer_idx):
            step = traj.steps[i]
            if (step.type in OBSERVATION_TYPES
                    and any(n == norm for _, _, n in extract_from_text(step.output or ""))):
                supported_at = i
                break
            if asserted_at is None and any(
                    n == norm for _, _, n in extract_from_text(
                        f"{step.input}\n{step.output}")):
                asserted_at = i
        first = supported_at if supported_at is not None else asserted_at
        if supported_at is not None:
            status = "supported"
            sup = traj.steps[supported_at]
            for j in range(supported_at + 1, answer_idx):
                later = traj.steps[j]
                if (later.type == sup.type and later.name == sup.name
                        and later.input == sup.input
                        and (later.output or "") != (sup.output or "")):
                    status = "stale"
                    break
        elif asserted_at is not None:
            status = "self_asserted"
        else:
            status = "unsupported"
        matches = (norm in expected_atoms) if expected_atoms else None
        if (matches is False and expected_atoms & observed_norms
                and kind in {k for k, _, _ in extract_from_text(expected or "")}):
            status = "contradicted"
        out.append({
            "kind": kind, "value": value, "status": status,
            "first_step": first,
            "source": (traj.steps[first].name or traj.steps[first].type)
            if first is not None else None,
            "matches_expected": matches,
        })
    return out


def _answer_basis(rests_on: list, answer_idx: int,
                  steps: Optional[list] = None) -> dict:
    """Roll the atoms up: the basis steps, when the basis was complete,
    and how many steps the run spent after the answer was available."""
    statuses = [r["status"] for r in rests_on]
    basis_steps = sorted({r["first_step"] for r in rests_on
                          if r["status"] in ("supported", "stale")
                          and r["first_step"] is not None})
    if not rests_on:
        overall = "no_typed_values"
    elif "contradicted" in statuses:
        overall = "contradicted"
    elif "stale" in statuses:
        overall = "stale"
    elif all(x == "supported" for x in statuses):
        overall = "supported"
    elif any(x == "supported" for x in statuses):
        overall = "partial"
    elif all(x == "self_asserted" for x in statuses):
        overall = "self_asserted"
    else:
        overall = "unsupported"
    complete_at = max(basis_steps) if basis_steps else None
    # spend after the basis counts information steps only: a write after
    # the basis is the task's deed, not wasted work
    after = ([i for i in range(complete_at + 1, answer_idx)
              if steps is None or getattr(steps[i], "effect", None) != "write"]
             if complete_at is not None else None)
    return {
        "status": overall,
        "basis_steps": basis_steps,
        "basis_complete_at": complete_at,
        "steps_after_basis_complete": len(after) if after is not None else None,
        "spent_steps": after or [],
        "atoms": len(rests_on),
        "supported": sum(1 for x in statuses if x == "supported"),
    }


def _validity(traj: Trajectory, answer_idx: int, expected: Optional[str],
              basis: dict, recovery: dict, term: dict) -> dict:
    """Measurement validity BEFORE agent attribution (HAL, transcript-flaw
    scanners): a harness kill, a leaked gold answer, or an answer with no
    basis at all are reasons to doubt the measurement, and anything raised
    here suppresses agent-attributed actions with the reason stated."""
    from .trace import HARNESS_TERMINATIONS
    calls = [i for i, s in enumerate(traj.steps) if s.type == "tool_call"]
    failed = [i for i in calls if traj.steps[i].error is True
              or i in {e.get("index") for e in (recovery.get("error_steps") or [])
                       if isinstance(e, dict)}]
    leaked = []
    if isinstance(expected, str) and expected.strip():
        needle = " ".join(expected.split()).lower()
        for i in range(answer_idx):
            step = traj.steps[i]
            if step.type in OBSERVATION_TYPES and needle in " ".join(
                    (step.output or "").split()).lower():
                leaked.append(i)
    harness_terminated = term.get("reason") in HARNESS_TERMINATIONS
    answer_without_basis = basis["atoms"] > 0 and basis["supported"] == 0 \
        and basis["status"] not in ("stale",)
    if harness_terminated:
        status, reason = "harness_fault", (
            f"the run ended by {term.get('reason')} — the harness failed, "
            "not the agent; nothing below is attributable to the agent")
    elif leaked:
        status, reason = "suspect", (
            "the expected answer appears verbatim in an observation at step "
            f"{leaked[0]} — the environment leaked the gold answer, so success "
            "here does not measure the agent")
    else:
        status, reason = "clean", None
    return {
        "status": status, "reason": reason,
        "harness_terminated": harness_terminated,
        "tool_failure_rate": {"failed": len(failed), "calls": len(calls),
                              "rate": round(len(failed) / len(calls), 4)
                              if calls else None},
        "environment_error_steps": failed,
        "answer_without_basis": bool(answer_without_basis),
        "expected_leaked": bool(leaked), "leak_steps": leaked,
    }


def _effects(traj: Trajectory) -> list:
    """Per-step read/write effect (declared, else inferred by the process
    module and labelled so), ``None`` for steps that are not actions."""
    table = _process._tool_table(traj)
    out = []
    for step in traj.steps:
        if step.type in OBSERVATION_TYPES:
            effect, _basis = _process.effect_of(step, table)
            out.append(effect if effect in ("read", "write") else "read")
        else:
            out.append(None)
    return out


def _phase_checks(traj: Trajectory, effects: list, ev: "_Evidence") -> dict:
    """Order checks from the coding-agent literature (Lucky-Pass
    AgentLens, TRAJEVAL): did the run act before it looked, did it check
    after it changed things, and did it go back to looking after acting
    — the regression cycle."""
    steps = traj.steps
    answer_idx = len(steps) - 1
    # a read that errored justifies nothing: only successful reads count
    # (the process module's blind_write uses the same rule)
    reads = [i for i, e in enumerate(effects) if e == "read" and i < answer_idx
             and not _process.is_error(steps[i])[0]]
    writes = [i for i, e in enumerate(effects) if e == "write" and i < answer_idx]
    first_write_before_any_read = bool(writes) and (not reads or writes[0] < reads[0])
    verification_after_last_write = None
    verify_step = None
    if writes:
        last_write = writes[-1]
        for i in range(last_write + 1, answer_idx):
            if effects[i] == "read" or _step_intent(steps[i]) == "verify":
                verify_step = i
                break
        verification_after_last_write = verify_step is not None
    # regression cycles: act → look → act
    coarse = ["W" if e == "write" else "R" if e == "read" else None
              for e in effects[:answer_idx]]
    cycles = []
    seen_write = None
    seen_read_after = None
    for i, c in enumerate(coarse):
        if c == "W":
            if seen_write is not None and seen_read_after is not None:
                cycles.append([seen_write, seen_read_after, i])
            seen_write, seen_read_after = i, None
        elif c == "R" and seen_write is not None and seen_read_after is None:
            seen_read_after = i
    refs = []
    if first_write_before_any_read:
        refs.append(ev.span(writes[0], "input", "write before any read", "observable"))
    if verification_after_last_write is False:
        refs.append(ev.span(writes[-1], "input", "last write, never checked", "observable"))
    for cycle in cycles[:3]:
        refs.append(ev.span(cycle[2], "input", "write after going back to look", "observable"))
    return {
        "first_write_before_any_read": first_write_before_any_read,
        "verification_after_last_write": verification_after_last_write,
        "verification_step": verify_step,
        "regression_cycles": len(cycles),
        "cycles": cycles,
        "writes": writes, "reads": reads,
        "refs": refs,
    }


def _error_lifecycle(traj: Trajectory, recovery: dict, rests_on: list,
                     answer_basis: dict, ev: "_Evidence") -> tuple:
    """TrajDebug's lifecycle per error: was it resolved, and if not, did it
    leave a footprint on the answer.  The critical error is the earliest
    unresolved one with a footprint — a counterfactual claim, labelled
    hypothesized, with a replay recipe."""
    steps = traj.steps
    answer_idx = len(steps) - 1
    answer_atoms = {norm for _, _, norm in extract_from_text(
        steps[answer_idx].output or steps[answer_idx].input or "")}
    records = {e["index"]: e for e in (recovery.get("error_steps") or [])
               if isinstance(e, dict) and isinstance(e.get("index"), int)}
    for i, step in enumerate(steps[:answer_idx]):
        if step.error is True and i not in records:
            records[i] = {"index": i, "name": step.name, "outcome": "undetermined",
                          "basis": "declared"}
    errors = []
    critical = None
    for i in sorted(records):
        rec = records[i]
        # resolved = the SAME tool later succeeded with changed input; the
        # process module's "recovered" only says the next call of any tool
        # worked, which is a different, weaker fact
        resolved_at = None
        if True:
            for j in range(i + 1, answer_idx):
                later = steps[j]
                if (later.type in OBSERVATION_TYPES and later.name == steps[i].name
                        and later.input != steps[i].input
                        and not _process.is_error(later)[0]):
                    resolved_at = j
                    break
        footprint = sorted(
            {norm for _, _, norm in extract_from_text(steps[i].output or "")}
            & answer_atoms)
        basis_missing = answer_basis["status"] in (
            "unsupported", "self_asserted", "no_typed_values")
        if resolved_at is not None:
            state = "resolved"
        elif footprint or basis_missing:
            state = "unresolved_with_footprint"
        else:
            state = "unresolved_without_footprint"
        entry = {
            "step": i, "name": steps[i].name,
            "trigger": "environment_feedback",
            "outcome": rec.get("outcome"), "state": state,
            "resolved_at": resolved_at,
            "footprint_atoms": footprint,
            "footprint_reason": ("error output reached the answer" if footprint
                                 else "the value this call should have produced "
                                      "is absent and the answer has no observed basis"
                                 if basis_missing and state != "resolved" else None),
            "evidence": ev.span(i, "output" if steps[i].output else "input",
                                f"error at step {i}: {state}", "observable"),
        }
        errors.append(entry)
        if critical is None and state == "unresolved_with_footprint":
            critical = {
                "step": i, "name": steps[i].name,
                "why": "earliest unresolved error with a footprint on the answer",
                "verification": "hypothesized",
                "replay_recipe": {"step": i,
                                  "correction": "make the call succeed or route around it",
                                  "expects": "the outcome flips to success",
                                  "replays": "≥3 — agent policies are stochastic"},
            }
    if critical is None:
        critical = {"step": None,
                    "why": ("no error occurred" if not errors else
                            "every error was resolved or left no footprint"),
                    "verification": None, "replay_recipe": None}
    return errors, critical


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
    answer_basis = _answer_basis(rests_on, answer_idx, steps)
    validity = _validity(traj, answer_idx, expected, answer_basis, recovery, term)
    effects = _effects(traj)
    phase_checks = _phase_checks(traj, effects, ev)
    errors, critical_error = _error_lifecycle(traj, recovery, rests_on,
                                              answer_basis, ev)
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
    unsourced = [r for r in rests_on if r["status"] in ("unsupported", "self_asserted")]
    if unsourced:
        findings.append({
            "kind": "unsourced_answer_value",
            "statement": "the answer asserts "
                         + ", ".join(f"{r['value']} ({r['status'].replace('_', ' ')})"
                                     for r in unsourced)
                         + " which no observation returned",
            "steps": [answer_idx],
            "evidence": [ev.span(answer_idx, "output" if steps[answer_idx].output
                                 else "input", "answer with unsourced value",
                                 "observable")],
            "evidence_class": "observable"})
    stale = [r for r in rests_on if r["status"] == "stale"]
    if stale:
        findings.append({
            "kind": "stale_basis",
            "statement": "the answer rests on "
                         + ", ".join(r["value"] for r in stale)
                         + " from an observation a later identical call superseded",
            "steps": sorted({r["first_step"] for r in stale}),
            "evidence": [ev.span(r["first_step"], "output", "superseded observation",
                                 "observable") for r in stale[:3]],
            "evidence_class": "observable"})
    contradicted = [r for r in rests_on if r["status"] == "contradicted"]
    if contradicted:
        findings.append({
            "kind": "contradicted_by_own_observation",
            "statement": "the run observed the expected value and answered "
                         + ", ".join(r["value"] for r in contradicted) + " instead",
            "steps": [answer_idx],
            "evidence": [ev.span(answer_idx, "output" if steps[answer_idx].output
                                 else "input", "answer contradicting an observation",
                                 "observable")],
            "evidence_class": "observable"})
    if answer_basis["steps_after_basis_complete"]:
        findings.append({
            "kind": "spent_after_basis",
            "statement": f"the answer's basis was complete at step "
                         f"{answer_basis['basis_complete_at']}; "
                         f"{answer_basis['steps_after_basis_complete']} more step(s) "
                         f"were spent before committing",
            "steps": list(answer_basis["spent_steps"]),
            "evidence": [ev.fact("reading.answer_basis.basis_complete_at",
                                 answer_basis["basis_complete_at"],
                                 "last step that added a supported value",
                                 "observable")],
            "evidence_class": "observable"})
    unresolved = [e for e in errors if e["state"] != "resolved"]
    if unresolved:
        findings.append({
            "kind": "unresolved_error",
            "statement": f"{len(unresolved)} of {len(errors)} tool error(s) were "
                         "never resolved"
                         + (f"; the earliest with a footprint on the answer is "
                            f"step {critical_error['step']}"
                            if critical_error.get("step") is not None else ""),
            "steps": [e["step"] for e in unresolved],
            "evidence": [e["evidence"] for e in unresolved[:3]],
            "evidence_class": "observable"})
    if phase_checks["first_write_before_any_read"]:
        findings.append({
            "kind": "wrote_before_reading",
            "statement": f"the first write (step {phase_checks['writes'][0]}) came "
                         "before any read",
            "steps": [phase_checks["writes"][0]],
            "evidence": phase_checks["refs"][:1], "evidence_class": "observable"})
    if phase_checks["verification_after_last_write"] is False:
        findings.append({
            "kind": "unchecked_write",
            "statement": f"nothing was read or checked after the last write "
                         f"(step {phase_checks['writes'][-1]})",
            "steps": [phase_checks["writes"][-1]],
            "evidence": [r for r in phase_checks["refs"]
                         if r][:2], "evidence_class": "observable"})
    if phase_checks["regression_cycles"]:
        findings.append({
            "kind": "regression_cycle",
            "statement": f"{phase_checks['regression_cycles']} act→look→act "
                         "cycle(s): the run went back to gathering after acting",
            "steps": sorted({i for c in phase_checks["cycles"] for i in c}),
            "evidence": phase_checks["refs"][-min(3, len(phase_checks["cycles"])):],
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
        elif f["kind"] == "unresolved_error":
            action = "stop on unresolved tool errors: retry with changed arguments or route around them"
        elif f["kind"] == "wrote_before_reading":
            action = "read the state a write depends on before writing"
        elif f["kind"] == "unchecked_write":
            action = "read back after the last write to confirm it took"
        elif f["kind"] == "regression_cycle":
            action = "gather what an action needs before acting; a look-back after acting is a plan defect"
        elif f["kind"] == "stale_basis":
            action = "re-read before answering: the basis was superseded by a later call"
        elif f["kind"] == "contradicted_by_own_observation":
            action = "answer from the observation, not from memory — the right value was in the trace"
        elif f["kind"] == "spent_after_basis":
            action = "stop when the basis is complete; the extra steps bought nothing"
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
    # validity outranks attribution: a measurement problem makes every
    # agent-attributed action conditional, and says so
    if validity["status"] != "clean":
        for item in take_forward:
            item["conditional_on_validity"] = True
        take_forward.insert(0, {"action": "fix the measurement first: "
                                          + (validity["reason"] or validity["status"]),
                                "because": "validity", "steps": validity.get("leak_steps") or []})

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
        "answer_basis": answer_basis,
        "validity": validity,
        "phase_checks": phase_checks,
        "errors": errors,
        "critical_error": critical_error,
        "why_it_ended": why_it_ended,
        "what_it_means": findings,
        "take_forward": take_forward,
        "confidence": {"level": level, "basis": basis},
        "evidence": ev.items,
        "summary": _summary(traj, what_happened, rests_on, why_it_ended, findings,
                            critical_error),
    }


def _summary(traj, what_happened, rests_on, why, findings, critical=None) -> str:
    n = len(traj.steps)
    productive = sum(1 for w in what_happened if w["role"] == "feeds_answer")
    bits = [f"{traj.agent.name} took {n} step(s) and "
            f"{'succeeded' if why['success'] else 'failed'}"
            + (f" ({why['termination']})" if why.get("declared")
               and why["termination"] != "agent_stop" else "") + "."]
    if rests_on:
        supported = sum(1 for r in rests_on if r["status"] in ("supported", "stale"))
        bits.append(f"The answer rests on {len(rests_on)} typed value(s), "
                    f"{supported} of them returned by an observation.")
    if productive:
        bits.append(f"{productive} step(s) measurably fed the answer.")
    if critical and critical.get("step") is not None:
        bits.append(f"Critical error at step {critical['step']} "
                    f"({critical.get('name')}), hypothesized.")
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
    for e in reading.get("errors", []):
        if e.get("evidence") not in known:
            problems.append(f"error at step {e.get('step')}: dangling evidence")
    for ref in (reading.get("phase_checks") or {}).get("refs", []):
        if ref not in known:
            problems.append(f"phase check: dangling evidence {ref}")
    return problems
