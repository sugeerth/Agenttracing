"""Process integrity: what a trajectory did, apart from whether it worked (v22).

Outcome-only evaluation is blind by construction.  A run can satisfy its
oracle while looping, swallowing an error, writing something it was never
asked to write, or stopping because it hit the step ceiling — and a run can
fail with a completely clean process because the oracle itself is wrong.
Claw-Eval measures the first half of that gap at 44% of safety violations
and 13% of robustness failures invisible to outcome-only grading
(arXiv 2604.06132); OpenClawBench names it the *outcome-process gap* and
needs 31,264 annotated trajectories to characterise it (arXiv 2605.29253).

Everything here is the deterministic subset of that: computed from a logged
trace with no judge and no re-execution.  That restriction is a feature.
Frozen-transition studies find the *same* step scored positive by one
evaluator channel and negative by another, with cross-channel sign
disagreement exceeding same-channel retry disagreement by 48 percentage
points (arXiv 2607.04419) — so the parts that can be settled by counting are
worth settling by counting, and the rest is left to a human or a judge with
the evidence laid out for them.

Two disciplines run through the module:

* **Declared beats inferred, and the difference is reported.**  A log that
  states a step's ``effect`` or the run's ``termination`` is believed.  When
  it does not, effects are inferred from tool names and termination is
  ``undeclared`` — never guessed into a definite value.  Every count carries
  the basis it was computed on.
* **Never average across termination.**  Per-step rates are confounded by
  length: weaker models show *inflated* shares of correct steps because they
  stop early (arXiv 2603.14465).  A quitter must not outrank a striver on a
  ratio, so termination is always reported beside anything per-step.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from .tooldiff import TOOLISH_TYPES, parse_args
from .trace import EFFECTS, Step, Trajectory

#: tool-name stems that change something outside the agent.  Only consulted
#: when the log does not declare an effect; a guess that is labelled a guess.
_WRITE_STEMS = (
    "create", "update", "delete", "remove", "write", "insert", "post", "put",
    "patch", "send", "email", "book", "cancel", "reserve", "pay", "purchase",
    "transfer", "submit", "upload", "commit", "push", "merge", "deploy",
    "execute", "run", "install", "modify", "set_", "add_", "drop",
)
_READ_STEMS = (
    "get", "list", "read", "search", "find", "fetch", "query", "lookup",
    "view", "show", "describe", "check", "browse", "open", "select", "load",
)

#: markers of an error observation, used only when ``step.error`` is absent.
_ERROR_MARKERS = (
    "error", "exception", "traceback", "failed", "failure", "not found",
    "invalid", "denied", "unauthorized", "forbidden", "timeout", "timed out",
    "no such", "cannot", "unable to", "does not exist", "rate limit",
)
_ERROR_CODE = re.compile(r"\b(4\d{2}|5\d{2})\b")

#: a run that spent at least this share of its step budget is close enough to
#: the ceiling that finishing may have been luck rather than judgement.
BUDGET_PRESSURE = 0.8


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def _digest(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:16]


def _signature(step: Step) -> str:
    """A step's identity for repeat detection: tool plus canonical arguments.

    Falls back to the raw input when the call does not parse, so an
    unparseable-but-identical call still counts as a repeat.
    """
    args = parse_args(step.input)
    if args is None:
        return f"{step.name}|{_norm(step.input)}"
    rendered = ",".join(f"{k}={_norm(v)}" for k, v in sorted(args.items()))
    return f"{step.name}|{rendered}"


def is_error(step: Step) -> tuple[bool, str]:
    """Whether a step's observation was an error, and on what basis.

    Returns ``(error, basis)`` where basis is ``"declared"`` or
    ``"inferred"``.  Text matching is a heuristic and says so: an
    observation that merely discusses an error reads the same as one that is
    an error, which is exactly why the declared field exists.
    """
    if step.error is not None:
        return bool(step.error), "declared"
    text = _norm(step.output)
    if not text:
        return False, "inferred"
    head = text[:200]
    if any(marker in head for marker in _ERROR_MARKERS) or _ERROR_CODE.search(head):
        return True, "inferred"
    return False, "inferred"


def effect_of(step: Step, declared_tools: Optional[dict] = None) -> tuple[str, str]:
    """A step's read/write effect, and whether it was declared or inferred.

    Only tool-ish steps can have an effect at all; thinking is not a write.
    Order matters: the step's own declaration wins, then the tool table, then
    the name.  Unknown stays ``"read"`` with basis ``"assumed"`` so a caller
    can exclude assumptions from a side-effect ledger rather than treating a
    guess as evidence of safety.
    """
    if step.type not in TOOLISH_TYPES:
        return "read", "not-a-tool"
    if step.effect in EFFECTS:
        return step.effect, "declared"
    if declared_tools:
        declared = declared_tools.get(step.name)
        if declared in EFFECTS:
            return declared, "declared"
    name = (step.name or "").lower()
    for stem in _WRITE_STEMS:
        if name.startswith(stem) or f"_{stem}" in name:
            return "write", "inferred"
    for stem in _READ_STEMS:
        if name.startswith(stem) or f"_{stem}" in name:
            return "read", "inferred"
    return "read", "assumed"


def _tool_table(trajectory: Trajectory) -> dict:
    return {
        str(tool.get("name")): tool.get("effect")
        for tool in (trajectory.tools or [])
        if isinstance(tool, dict) and tool.get("name")
    }


def termination(trajectory: Trajectory) -> dict:
    """Why the run stopped — declared, or ``undeclared`` with what is visible.

    Never inferred into one of the real reasons.  "The last step was an
    answer" does not distinguish an agent that decided it was done from one
    the harness cut off, and pretending otherwise would corrupt every rate
    conditioned on it.
    """
    declared = trajectory.outcome.termination
    steps = len(trajectory.steps)
    limit = trajectory.budget.get("max_steps")
    at_limit = bool(limit) and steps >= float(limit)
    return {
        "reason": declared or "undeclared",
        "declared": declared is not None,
        "steps": steps,
        "max_steps": limit,
        "at_step_limit": at_limit,
        "budget_used": round(steps / float(limit), 4) if limit else None,
        "under_budget_pressure": bool(limit) and steps / float(limit) >= BUDGET_PRESSURE,
    }


def side_effects(trajectory: Trajectory) -> dict:
    """The write ledger: what this run changed outside itself.

    Writes get their own accounting because they are the steps that cannot
    be re-run to check, and because a write before any read is a decision
    taken without looking — the deterministic shadow of "premature
    commitment under uncertainty".
    """
    table = _tool_table(trajectory)
    writes, reads, assumed = [], [], 0
    first_read: Optional[int] = None
    blind_writes = []
    for step in trajectory.steps:
        if step.type not in TOOLISH_TYPES:
            continue
        effect, basis = effect_of(step, table)
        if basis == "assumed":
            assumed += 1
        record = {"index": step.index, "name": step.name, "basis": basis}
        if effect == "write":
            writes.append(record)
            if first_read is None:
                blind_writes.append(record)
        else:
            reads.append(record)
            # Only a read that *worked* counts as having looked. Three failed
            # lookups followed by a write is the blind-write case exactly, and
            # crediting the failures would hide it.
            if first_read is None and not is_error(step)[0]:
                first_read = step.index
    return {
        "writes": len(writes),
        "reads": len(reads),
        "write_steps": writes,
        "writes_before_any_read": len(blind_writes),
        "blind_write_steps": blind_writes,
        "unclassified": assumed,
        "basis": "declared" if (trajectory.tools or any(
            s.effect for s in trajectory.steps)) else "inferred from tool names",
    }


def repeats(trajectory: Trajectory) -> dict:
    """Repeated calls, cycles, and steps that returned nothing new.

    Three different pathologies that all look like "the agent is stuck":

    * a **repeat** is the same call made twice;
    * a **cycle** is the same (call, observation) pair recurring, which is
      the shape of a loop rather than a retry;
    * a **no-information step** returns an observation byte-identical to one
      already seen, so the step advanced nothing even if the call was new.

    A retry after an error is deliberately *not* counted as a repeat
    pathology — retrying a failed call is correct behaviour, and conflating
    it with looping would penalise recovery.
    """
    seen_calls: dict[str, int] = {}
    seen_pairs: dict[str, int] = {}
    seen_outputs: dict[str, int] = {}
    repeated, cycles, no_information = [], [], []

    for step in trajectory.steps:
        if step.type not in TOOLISH_TYPES:
            continue
        signature = _signature(step)
        errored, _ = is_error(step)
        previous = seen_calls.get(signature)
        if previous is not None:
            prior_step = trajectory.steps[previous] if previous < len(trajectory.steps) else None
            after_error = bool(prior_step) and is_error(prior_step)[0]
            if not after_error:
                repeated.append({"index": step.index, "name": step.name,
                                 "first_seen": previous})
        seen_calls.setdefault(signature, step.index)

        pair = f"{signature}=>{_digest(step.output)}"
        if pair in seen_pairs:
            cycles.append({"index": step.index, "name": step.name,
                           "period": step.index - seen_pairs[pair],
                           "first_seen": seen_pairs[pair]})
        else:
            seen_pairs[pair] = step.index

        if step.output.strip() and not errored:
            digest = _digest(step.output)
            if digest in seen_outputs:
                no_information.append({"index": step.index, "name": step.name,
                                       "same_as": seen_outputs[digest]})
            else:
                seen_outputs[digest] = step.index

    return {
        "repeated_calls": len(repeated),
        "repeated_steps": repeated,
        "cycles": len(cycles),
        "cycle_steps": cycles,
        "no_information_steps": len(no_information),
        "no_information_detail": no_information,
    }


def loops(steps: list) -> dict:
    """Repeated k-grams: the shape of a loop rather than a stutter.

    A single repeated call is a retry; the same *sequence* of calls going
    round again is a loop, and MAST's Step Repetition mode (1.3) is defined
    on exactly that. Reported as the longest back-to-back repeat found,
    with its period, so "A,B,A,B,A,B" reads as period 2 repeated 3 times
    rather than as six unremarkable steps.
    """
    signatures = [_signature(s) for s in steps if s.type in TOOLISH_TYPES]
    indices = [s.index for s in steps if s.type in TOOLISH_TYPES]
    best = {"period": 0, "repeats": 0, "starts_at": None, "length": 0}
    n = len(signatures)
    for period in range(1, n // 2 + 1):
        for start in range(0, n - 2 * period + 1):
            repeats = 1
            while True:
                nxt = start + repeats * period
                if nxt + period > n:
                    break
                if signatures[nxt:nxt + period] != signatures[start:start + period]:
                    break
                repeats += 1
            if repeats >= 2 and repeats * period > best["length"]:
                best = {"period": period, "repeats": repeats,
                        "starts_at": indices[start], "length": repeats * period}
    multiplicity = {}
    for signature in signatures:
        multiplicity[signature] = multiplicity.get(signature, 0) + 1
    top = max(multiplicity.values()) if multiplicity else 0
    return {
        "longest_repeated_block": best,
        "max_call_multiplicity": top,
        # Two independent readings of "stuck": a block that recurs, or one
        # call made three or more times.  Either alone is enough.
        "looping": best["repeats"] >= 2 or top >= 3,
    }


#: phrases an agent uses to assert it finished.  Only consulted against the
#: trace's own evidence — the assertion is never treated as the outcome.
_COMPLETION_CLAIMS = (
    "task completed", "task is complete", "successfully", "success",
    "done", "all set", "completed the", "i have completed", "finished",
    "has been created", "has been updated", "has been sent", "has been booked",
    "i've completed", "is now complete",
)


def false_success(trajectory: Trajectory, ledger: dict) -> dict:
    """Does the run *claim* it did something it never did?

    This is the failure mode judges are worst at: across five judges and
    five prompting strategies given full task specifications, none exceeded
    AUROC 0.65 at spotting it, because they latch onto confident closing
    language and action volume rather than verified state change
    (arXiv 2606.09863).  It is also common — 45-48% of failures in
    single-control tau2-bench domains.

    Deterministically it is a contradiction between two things already in
    the log: the answer asserts completion, and no step wrote anything.
    Only flagged when the run had write tools available to it, because a
    question-answering run that says "done" has nothing to write and is not
    lying.
    """
    answer = _norm(trajectory.outcome.answer)
    claims = [phrase for phrase in _COMPLETION_CLAIMS if phrase in answer]
    table = _tool_table(trajectory)
    could_write = any(effect == "write" for effect in table.values()) if table else None
    wrote = ledger["writes"] > 0
    # Undeterminable without knowing what the agent was offered: silence
    # about the tool list is not evidence that no write was possible.
    if could_write is None:
        verdict, flagged = "unmeasurable", False
    elif claims and could_write and not wrote:
        verdict, flagged = "claimed completion without writing anything", True
    elif claims and not could_write:
        verdict, flagged = "claim is about an answer, not an action", False
    else:
        verdict, flagged = "no contradiction", False
    return {
        "flagged": flagged,
        "verdict": verdict,
        "claim_phrases": claims,
        "write_tools_offered": could_write,
        "writes": ledger["writes"],
        "measurable": could_write is not None,
    }


def _schema_errors(step: Step, schema: dict) -> list:
    """Required-key, unknown-key and primitive-type violations for one call.

    A shallow walk on purpose: deep validation of nested JSON against a full
    JSON Schema needs a real validator, and the arguments here were parsed
    heuristically out of a call string, so anything deeper would be
    confident about text it only half understands.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    args = parse_args(step.input)
    if args is None:
        return []
    problems = []
    required = schema.get("required")
    for key in (required if isinstance(required, list) else []):
        if key not in args:
            problems.append({"kind": "missing_required", "argument": key})
    for key, value in sorted(args.items()):
        if key not in properties:
            problems.append({"kind": "unknown_argument", "argument": key})
            continue
        expected = (properties[key] or {}).get("type")
        text = str(value).strip().strip("\"'")
        if expected in ("integer", "number"):
            try:
                float(text)
            except ValueError:
                problems.append({"kind": "type_mismatch", "argument": key,
                                 "expected": expected})
        elif expected == "boolean" and text.lower() not in ("true", "false"):
            problems.append({"kind": "type_mismatch", "argument": key,
                             "expected": expected})
    return problems


def schema_validity(trajectory: Trajectory) -> dict:
    """Do the calls typecheck against the tools the run was offered?

    Unmeasurable without declared parameter schemas, and reported that way
    rather than scored 100% — an unchecked call is not a valid one.
    """
    schemas = {
        str(tool.get("name")): tool.get("parameters")
        for tool in (trajectory.tools or [])
        if isinstance(tool, dict) and isinstance(tool.get("parameters"), dict)
    }
    if not schemas:
        return {"measurable": False, "checked": 0, "violations": 0,
                "detail": [], "validity": None,
                "note": "no tool parameter schemas declared; call validity unchecked"}
    checked, violations = 0, []
    for step in trajectory.steps:
        if step.type not in TOOLISH_TYPES or step.name not in schemas:
            continue
        checked += 1
        for problem in _schema_errors(step, schemas[step.name]):
            problem.update({"index": step.index, "name": step.name})
            violations.append(problem)
    return {
        "measurable": True,
        "checked": checked,
        "violations": len(violations),
        "detail": violations[:12],
        "validity": round(1 - len(violations) / checked, 4) if checked else None,
        "note": None,
    }


def recovery(trajectory: Trajectory) -> dict:
    """What the agent did after something went wrong.

    An error is only a failure if it is not recovered from.  For each error
    observation, the next tool step either changes the call (an attempt) or
    repeats it verbatim (not an attempt), and either succeeds or does not.
    Errors with no following tool step are counted separately — abandoning
    after an error is a different behaviour from failing to fix it.
    """
    errors, attempts, recovered, abandoned = [], 0, 0, 0
    declared_basis = any(step.error is not None for step in trajectory.steps)
    tool_steps = [s for s in trajectory.steps if s.type in TOOLISH_TYPES]

    for position, step in enumerate(tool_steps):
        errored, basis = is_error(step)
        if not errored:
            continue
        record = {"index": step.index, "name": step.name, "basis": basis}
        following = tool_steps[position + 1] if position + 1 < len(tool_steps) else None
        if following is None:
            abandoned += 1
            record["outcome"] = "abandoned"
        else:
            changed = _signature(following) != _signature(step)
            next_ok = not is_error(following)[0]
            if changed:
                attempts += 1
            if changed and next_ok:
                recovered += 1
                record["outcome"] = "recovered"
            elif changed:
                record["outcome"] = "retried, still failing"
            else:
                record["outcome"] = "repeated the failing call"
        errors.append(record)

    return {
        "errors": len(errors),
        "error_steps": errors,
        "recovery_attempts": attempts,
        "recovered": recovered,
        "abandoned_after_error": abandoned,
        "recovery_rate": round(recovered / len(errors), 4) if errors else None,
        "basis": "declared" if declared_basis else "inferred from observation text",
    }


def grounding(trajectory: Trajectory) -> dict:
    """Were the calls made real, and did their arguments come from somewhere?

    Two checks, both containment tests over the trace:

    * **schema grounding** — did the call name a tool the run was offered?
      Unmeasurable without a declared tool list, and reported as such rather
      than scored 100%.
    * **argument provenance** — does each argument value appear in an
      earlier observation, or in the task prompt?  A value that appears in
      neither was invented at that step.  This is the deterministic core of
      what judges are usually asked to call "weak evidence grounding", and
      it needs no model: it is a substring check against what the agent had
      actually seen by then.
    """
    table = _tool_table(trajectory)
    calls, ungrounded_names = 0, []
    invented, checked_values = [], 0
    context = _norm(trajectory.task.prompt)

    for step in trajectory.steps:
        if step.type in TOOLISH_TYPES:
            calls += 1
            if table and step.name and step.name not in table:
                ungrounded_names.append({"index": step.index, "name": step.name})
            args = parse_args(step.input) or {}
            for key, value in sorted(args.items()):
                text = _norm(value).strip("\"'")
                # Short values are not evidence of anything — a page number
                # or a boolean matches by accident, in either direction.
                if len(text) < 6:
                    continue
                checked_values += 1
                if text not in context:
                    invented.append({"index": step.index, "name": step.name,
                                     "argument": key, "value": value[:60]})
        context += " " + _norm(step.output)

    return {
        "calls": calls,
        "schema_checked": bool(table),
        "undeclared_tool_calls": len(ungrounded_names),
        "undeclared_tool_steps": ungrounded_names,
        "schema_grounding": (
            round(1 - len(ungrounded_names) / calls, 4) if table and calls else None),
        "arguments_checked": checked_values,
        "arguments_without_source": len(invented),
        "argument_provenance": (
            round(1 - len(invented) / checked_values, 4) if checked_values else None),
        "invented_arguments": invented[:12],
    }


#: each flag is (key, human-readable phrase) — the phrase is what a report
#: prints, so the wording lives with the rule that produces it.
_PATHOLOGIES = (
    ("false_success", "claimed to have finished something it never did"),
    ("looped", "repeated the same call-and-result"),
    ("loop_block", "cycled through the same block of calls"),
    ("repeated_calls", "made the same call more than once"),
    ("no_information_steps", "took steps that returned nothing new"),
    ("swallowed_error", "hit an error and never recovered from it"),
    ("blind_write", "wrote before reading anything"),
    ("budget_pressure", "finished on the edge of its step budget"),
    ("undeclared_tools", "called a tool it was not offered"),
    ("invented_arguments", "used argument values with no source in the trace"),
    ("schema_violation", "called a tool with arguments that do not typecheck"),
)


def outcome_process_gap(trajectory: Trajectory, parts: dict) -> dict:
    """Whether the verdict and the process agree.

    Two disagreements matter, in opposite directions.  **Passed but
    pathological** is a success that should not be trusted: the oracle was
    satisfied by a run that looped, ignored an error, or wrote blind.
    **Failed but clean** is the reverse, and it is where broken oracles
    live — a run that did everything visible right and was still marked
    wrong is evidence about the grader, not only the agent.
    """
    repeat = parts["repeats"]
    fail = parts["recovery"]
    ledger = parts["side_effects"]
    ground = parts["grounding"]
    stop = parts["termination"]

    flags = {
        "false_success": parts["false_success"]["flagged"],
        "looped": repeat["cycles"] > 0,
        "loop_block": parts["loops"]["looping"],
        "repeated_calls": repeat["repeated_calls"] > 0,
        "no_information_steps": repeat["no_information_steps"] > 0,
        "swallowed_error": fail["errors"] > 0 and fail["recovered"] < fail["errors"],
        "blind_write": ledger["writes_before_any_read"] > 0,
        "budget_pressure": bool(stop["under_budget_pressure"]),
        "undeclared_tools": ground["undeclared_tool_calls"] > 0,
        "invented_arguments": ground["arguments_without_source"] > 0,
        "schema_violation": parts["schema"]["violations"] > 0,
    }
    raised = [key for key, _ in _PATHOLOGIES if flags[key]]
    phrases = [phrase for key, phrase in _PATHOLOGIES if flags[key]]
    success = trajectory.outcome.success

    if success and raised:
        verdict = "passed but pathological"
        narrative = (
            f"{trajectory.agent.name} passed, but the process is not clean: it "
            + _join(phrases) + ". The oracle was satisfied; the run was not sound."
        )
    elif success:
        verdict = "passed cleanly"
        narrative = (
            f"{trajectory.agent.name} passed and nothing in the trace contradicts "
            "it — no loops, no unrecovered errors, no blind writes."
        )
    elif raised:
        verdict = "failed with cause"
        narrative = (
            f"{trajectory.agent.name} failed, and the process shows why: it "
            + _join(phrases) + "."
        )
    else:
        verdict = "failed but clean"
        narrative = (
            f"{trajectory.agent.name} failed, yet nothing in its process went "
            "visibly wrong — no loops, no errors, no unsourced arguments. Either "
            "the mistake is in content this analysis cannot see, or the grader is "
            "wrong; a clean failure is worth checking the oracle over."
        )

    return {
        "success": success,
        "verdict": verdict,
        "flags": flags,
        "raised": raised,
        "clean": not raised,
        "narrative": narrative,
    }


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def analyse(trajectory: Trajectory) -> dict:
    """Everything in this module for one run."""
    ledger = side_effects(trajectory)
    parts = {
        "termination": termination(trajectory),
        "side_effects": ledger,
        "repeats": repeats(trajectory),
        "loops": loops(trajectory.steps),
        "recovery": recovery(trajectory),
        "grounding": grounding(trajectory),
        "schema": schema_validity(trajectory),
        "false_success": false_success(trajectory, ledger),
    }
    parts["gap"] = outcome_process_gap(trajectory, parts)
    parts["agent"] = trajectory.agent.name
    return parts


def compare_process(a: Trajectory, b: Trajectory) -> dict:
    """Process integrity for both sides of a pair, plus what differs.

    The comparison deliberately does not produce a single "process score".
    Weighting a loop against a blind write requires a judgement about the
    domain that this tool does not have; the flags are reported so a reader
    can apply their own.
    """
    left, right = analyse(a), analyse(b)
    differing = sorted(
        key for key, _ in _PATHOLOGIES
        if left["gap"]["flags"][key] != right["gap"]["flags"][key]
    )
    both = sorted(
        key for key, _ in _PATHOLOGIES
        if left["gap"]["flags"][key] and right["gap"]["flags"][key]
    )
    return {
        "a": left,
        "b": right,
        "differing_flags": differing,
        "shared_flags": both,
        "narrative": _pair_narrative(a, b, left, right, differing, both),
    }


def _pair_narrative(a, b, left, right, differing, both) -> str:
    phrases = dict(_PATHOLOGIES)
    if not differing and not both:
        return (f"Both runs are process-clean: nothing in either trace loops, "
                f"errors out unrecovered, or writes blind.")
    parts = []
    for key in differing:
        side = a.agent.name if left["gap"]["flags"][key] else b.agent.name
        parts.append(f"{side} {phrases[key]}")
    if both:
        parts.append("both " + _join([phrases[key] for key in both]))
    lead = _join(parts)
    if left["gap"]["verdict"] == "passed but pathological" or \
            right["gap"]["verdict"] == "passed but pathological":
        lead += " — and at least one of those runs passed anyway, so the outcome " \
                "hides it"
    return lead[0].upper() + lead[1:] + "."
