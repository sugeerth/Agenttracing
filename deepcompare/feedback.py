"""The loop back: what a comparison hands to the environment and the
next prompt.

A report already says where a run went wrong and what to do instead.
This module turns that into three things a training or prompting loop
can consume, each read from the report as written and none invented:

* **step labels** — every step of both runs tagged with what the report
  found there (``fault_enters``, ``fault_carried``, ``wrong_answer``,
  ``dead_end``, ``no_information``, ``repeat``, ``error``,
  ``spent_after_basis``, ``invented_argument``, ``fed_answer``,
  ``clean``), each with the field it came from — dense supervision for
  a process reward or a reward-shaping term;
* a **preference pair** — the passing run (or the reconciled splice
  when the report carries one) as *chosen* and the failing run as
  *rejected*, with the step the two diverge at, in the shape a
  preference-optimisation loop expects (prompt, chosen, rejected);
* **prompt suggestions** — sentences for the next horizon's system
  prompt, one per finding the reading located, each quoting the
  report's own words and citing its references, and each paired with
  the replay that would test it.

Everything here is *derived* and labelled so: a suggestion is a
hypothesis until a replay flips the outcome. No score, verdict or
number in the report changes; this module only reads.
"""

from __future__ import annotations

import json
from typing import Optional

VERSION = 1

#: what the reading's `because` kinds and the attribution's categories
#: mean for the next prompt — templates, filled only from the report
_PROMPT_TEMPLATES = {
    "faithful_to_wrong_observation": (
        "Before relying on a tool result, restate what the tool was asked and "
        "check that it answers the question as posed; a faithful relay of a "
        "wrong observation is still wrong."),
    "spent_after_basis": (
        "Commit to the answer as soon as every value it needs is in hand; do "
        "not spend further steps once the basis is complete."),
    "wasted_work": (
        "Before each step, state what it must yield; skip any step that "
        "yields nothing the answer needs."),
    "dead_end": (
        "When a step returns nothing usable, change approach rather than "
        "repeating it."),
    "no_information": (
        "Treat an empty or irrelevant observation as a signal to change the "
        "query or the tool, not to continue."),
    "repeat": (
        "Never repeat a call with the same arguments; if the first result was "
        "insufficient, change the arguments or the tool."),
    "answer_without_basis": (
        "Do not state a value the trace has not observed; every value in the "
        "answer must come from a step that produced it."),
    "contradicted_basis": (
        "When a later observation contradicts an earlier value, use the later "
        "one and say why."),
    "meltdown": (
        "If the same call has been made several times in a row, stop and "
        "re-plan instead of continuing."),
    "invented_argument": (
        "Only pass a tool arguments that appear in the task or in an earlier "
        "observation; never invent an identifier or a value."),
    # the kinds the reading actually emits (reasoning.py), so a finding on
    # a failing run can become a sentence in the next prompt
    "unsourced_answer_value": (
        "Every value in the final answer must come from an observation in "
        "this run; if a value has not been observed, obtain it with a tool "
        "before answering."),
    "contradicted_by_own_observation": (
        "If the answer would contradict something a tool returned earlier in "
        "the run, resolve the contradiction before answering, and say which "
        "observation the answer rests on."),
    "stale_basis": (
        "When a later observation updates a value used earlier, recompute "
        "the answer from the latest observation."),
    "unresolved_error": (
        "Do not answer while a tool error stands unresolved; retry with "
        "corrected arguments or a different tool, or say that the value "
        "could not be obtained."),
    "meltdown_onset": (
        "If the same call has been made several times in a row, stop and "
        "re-plan instead of continuing."),
    "regression_cycle": (
        "Do not undo a change that a check has already confirmed; when a "
        "check fails, change something else."),
    "wrote_before_reading": (
        "Read the current state before writing to it; never modify what "
        "has not been observed in this run."),
    "unchecked_write": (
        "After every write, read back or test what was written before "
        "moving on."),
    "unverified": (
        "Before the final answer, check it against the task as posed and "
        "against the values observed in this run."),
}

_CATEGORY_TEMPLATES = {
    "tool_selection": "For this kind of task, use {passing_tool} rather than {failing_tool}.",
    "tool_misuse": "Check the arguments of {failing_tool} against the task before calling it.",
    "reasoning": "Verify each intermediate value before it enters the answer.",
    "retrieval": "Prefer a primary or official source; do not commit on a secondary one.",
    "planning": "Write the plan as the values the answer needs and the step that yields each.",
    "stopping": "Do not answer until every value the answer needs has been observed in this run; if one is missing, obtain it first.",
}


def _reading(report: dict, side: str) -> dict:
    return (report.get("reading") or {}).get(side) or {}


def _steps(report: dict, side: str) -> list:
    return (report.get(side) or {}).get("steps") or []


def _agent(report: dict, side: str) -> str:
    return ((report.get(side) or {}).get("agent") or {}).get("name") or side.upper()


def _failing_side(report: dict) -> Optional[str]:
    a = (report.get("a") or {}).get("outcome") or {}
    b = (report.get("b") or {}).get("outcome") or {}
    if a.get("success") is True and b.get("success") is not True:
        return "b"
    if b.get("success") is True and a.get("success") is not True:
        return "a"
    diag = report.get("diagnosis") or {}
    return diag.get("subject") if diag.get("subject") in ("a", "b") else None


def step_labels(report: dict) -> list:
    """One record per step of both runs, with every label the report
    supports for it and the field each label came from."""
    out = []
    diag = report.get("diagnosis") or {}
    dec = diag.get("decisive_step") or {}
    subject = diag.get("subject")
    account = {}
    for link in diag.get("causal_account") or []:
        if isinstance(link, dict) and isinstance(link.get("step"), int):
            account[link["step"]] = link
    attr = report.get("attribution") or {}
    chain = set(i for i in (attr.get("chain") or []) if isinstance(i, int))
    for side in ("a", "b"):
        reading = _reading(report, side)
        steps = _steps(report, side)
        failed = ((report.get(side) or {}).get("outcome") or {}).get("success") is not True
        what = {w["step"]: w for w in reading.get("what_happened") or [] if isinstance(w, dict)}
        basis = reading.get("answer_basis") or {}
        spent = set(basis.get("spent_steps") or [])
        errors = {e["step"] for e in reading.get("errors") or [] if isinstance(e, dict) and isinstance(e.get("step"), int)}
        for i, step in enumerate(steps):
            labels = []
            w = what.get(i) or {}
            on_fault = (subject == side and i in account) or (attr.get("failed_agent") == side and i in chain)
            if on_fault and dec.get("step") == i and subject == side:
                labels.append({"label": "fault_enters", "source": "diagnosis.decisive_step"})
            elif on_fault and step.get("type") == "answer" and failed:
                labels.append({"label": "wrong_answer", "source": "diagnosis.causal_account"})
            elif on_fault:
                labels.append({"label": "fault_carried",
                               "source": "diagnosis.causal_account" if i in account else "attribution.chain",
                               "mechanism": (account.get(i) or {}).get("mechanism")})
            role = w.get("role")
            if role in ("dead_end", "no_information", "repeat", "error"):
                labels.append({"label": role, "source": "reading.what_happened.role"})
            if role == "feeds_answer":
                labels.append({"label": "fed_answer", "source": "reading.what_happened.role"})
            if i in spent:
                labels.append({"label": "spent_after_basis", "source": "reading.answer_basis.spent_steps"})
            if w.get("invented_argument"):
                labels.append({"label": "invented_argument", "source": "reading.what_happened.invented_argument"})
            if i in errors or step.get("error") is True:
                if not any(l["label"] == "error" for l in labels):
                    labels.append({"label": "error", "source": "reading.errors"})
            if not labels:
                labels.append({"label": "clean", "source": "no finding names this step"})
            out.append({"side": side, "agent": _agent(report, side), "step": i,
                        "type": step.get("type"), "name": step.get("name"),
                        "labels": labels})
    return out


def preference_pair(report: dict) -> Optional[dict]:
    """The passing run — or the reconciled splice — as *chosen*, the
    failing run as *rejected*; None when nothing passed."""
    failing = _failing_side(report)
    if failing is None:
        return None
    passing = "a" if failing == "b" else "b"
    if ((report.get(passing) or {}).get("outcome") or {}).get("success") is not True:
        return None
    task = report.get("task") or {}
    cf = report.get("counterfactual") or {}
    splice = cf.get("splice") if isinstance(cf.get("splice"), dict) else None
    dec = (report.get("diagnosis") or {}).get("decisive_step") or {}

    def turns(side: str, indices: Optional[list] = None) -> list:
        steps = _steps(report, side)
        picked = indices if indices is not None else list(range(len(steps)))
        out = []
        for i in picked:
            if 0 <= i < len(steps):
                s = steps[i]
                out.append({"from": side, "step": i, "type": s.get("type"), "name": s.get("name"),
                            "input": s.get("input"), "output": s.get("output")})
        return out

    chosen_basis = "the passing run, verbatim"
    chosen = turns(passing)
    if splice and splice.get("adopted_from") == passing:
        chosen = turns(failing, splice.get("prefix_steps") or []) + turns(passing, splice.get("adopted_steps") or [])
        chosen_basis = ("the reconciled splice: the failing run's prefix, then the passing run "
                        "from the decisive step — an estimate, not a replayed trajectory")
    return {
        "prompt": task.get("prompt"),
        "task_id": task.get("id"),
        "expected": task.get("expected"),
        "chosen": {"agent": _agent(report, passing), "side": passing, "basis": chosen_basis, "turns": chosen},
        "rejected": {"agent": _agent(report, failing), "side": failing, "basis": "the failing run, verbatim",
                     "turns": turns(failing)},
        "diverges_at": {"side": failing, "step": dec.get("step"), "verification": dec.get("verification")}
        if isinstance(dec.get("step"), int) else None,
        "estimate": cf.get("estimate") if splice else None,
        "confidence": cf.get("confidence") if splice else None,
    }


def _tools_at_decisive(report: dict) -> dict:
    dec = (report.get("diagnosis") or {}).get("decisive_step") or {}
    side = (report.get("diagnosis") or {}).get("subject")
    if not isinstance(dec.get("step"), int) or side not in ("a", "b"):
        return {}
    other = "a" if side == "b" else "b"
    failing_step = (_steps(report, side) or [None] * (dec["step"] + 1))[dec["step"]] if dec["step"] < len(_steps(report, side)) else None
    passing_step = None
    for row in report.get("alignment") or []:
        if row.get(f"{side}_index") == dec["step"] and isinstance(row.get(f"{other}_index"), int):
            idx = row[f"{other}_index"]
            steps = _steps(report, other)
            passing_step = steps[idx] if idx < len(steps) else None
            break
    return {"failing_tool": (failing_step or {}).get("name"), "passing_tool": (passing_step or {}).get("name")}


def prompt_suggestions(report: dict) -> list:
    """Sentences for the next system prompt, derived from the findings
    the reading located on the failing run and from the attribution's
    category; each cites its references and carries the replay that
    would test it. Ordered as the reading ordered its next actions."""
    failing = _failing_side(report)
    if failing is None:
        return []
    reading = _reading(report, failing)
    out = []
    seen = set()
    for item in reading.get("take_forward") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("because")
        template = _PROMPT_TEMPLATES.get(kind)
        if template is None:
            continue   # annotated weakness and the like are a person's judgement, not a prompt
        if kind in seen:
            continue
        seen.add(kind)
        out.append({
            "text": template,
            "kind": kind,
            "derived_from": {"finding": item.get("what"), "instead": item.get("instead"),
                             "refs": item.get("refs") or [], "at_step": item.get("at_step")},
            "test": item.get("replay_recipe"),
            "status": "suggested — a hypothesis until a replay flips the outcome",
        })
    attr = report.get("attribution") or {}
    category = attr.get("category")
    if category in _CATEGORY_TEMPLATES and attr.get("failed_agent") == failing:
        tools = _tools_at_decisive(report)
        text = _CATEGORY_TEMPLATES[category]
        if "{" in text:
            if not (tools.get("failing_tool") and tools.get("passing_tool")) and category == "tool_selection":
                text = None
            elif not tools.get("failing_tool") and category == "tool_misuse":
                text = None
            else:
                text = text.format(passing_tool=tools.get("passing_tool"), failing_tool=tools.get("failing_tool"))
        if text and category not in seen:
            seen.add(category)
            dec = (report.get("diagnosis") or {}).get("decisive_step") or {}
            out.append({
                "text": text,
                "kind": category,
                "derived_from": {"finding": attr.get("explanation"), "refs": [], "at_step": attr.get("root_cause_step"),
                                 "tools": tools},
                "test": dec.get("replay_recipe"),
                "status": "suggested — a hypothesis until a replay flips the outcome",
            })
    return out


def reward_shaping(report: dict) -> list:
    """Events a reward-shaping term could penalise or credit, with the
    sign and the report field that justifies each; counts from the labels."""
    labels = step_labels(report)
    counts: dict = {}
    for rec in labels:
        for l in rec["labels"]:
            counts[l["label"]] = counts.get(l["label"], 0) + 1
    table = [
        ("fault_enters", -1, "the decisive step: the earliest correction expected to flip the outcome"),
        ("fault_carried", -1, "a step that carried the fault forward (causal account)"),
        ("wrong_answer", -1, "the answer that contradicts the expected value"),
        ("dead_end", -1, "a step that fed nothing measurable into the answer"),
        ("no_information", -1, "a step that returned nothing usable"),
        ("repeat", -1, "a call repeated with the same arguments"),
        ("spent_after_basis", -1, "a step taken after every value the answer needed existed"),
        ("invented_argument", -1, "a tool argument that appears in no earlier observation"),
        ("error", -1, "a step that errored"),
        ("fed_answer", +1, "a step whose output measurably entered the answer"),
    ]
    return [{"event": ev, "sign": sign, "count": counts.get(ev, 0), "basis": basis}
            for ev, sign, basis in table if counts.get(ev, 0)]


def feedback_signal(report: dict) -> dict:
    """The whole loop-back bundle for one pair."""
    task = report.get("task") or {}
    failing = _failing_side(report)
    return {
        "version": VERSION,
        "task_id": task.get("id"),
        "failing_side": failing,
        "failing_agent": _agent(report, failing) if failing else None,
        "step_labels": step_labels(report),
        "preference_pair": preference_pair(report),
        "prompt_suggestions": prompt_suggestions(report),
        "reward_shaping": reward_shaping(report),
        "note": ("derived from the report as written: labels from the reading and the diagnosis, "
                 "the pair from the outcomes (or the counterfactual splice, an estimate), "
                 "suggestions from the findings' kinds — test each with `deepcompare replay` "
                 "before it becomes a rule"),
    }


def to_jsonl(signals: list) -> str:
    """Preference pairs as JSONL — one line per pair, the shape a
    preference-optimisation loader reads: prompt, chosen, rejected."""
    lines = []
    for sig in signals:
        pair = sig.get("preference_pair")
        if not pair:
            continue
        lines.append(json.dumps({
            "task_id": pair.get("task_id"), "prompt": pair.get("prompt"), "expected": pair.get("expected"),
            "chosen": pair["chosen"]["turns"], "rejected": pair["rejected"]["turns"],
            "chosen_basis": pair["chosen"]["basis"], "diverges_at": pair.get("diverges_at"),
            "confidence": pair.get("confidence"),
        }, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")
