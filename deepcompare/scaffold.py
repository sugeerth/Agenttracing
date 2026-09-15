"""Scaffold hypotheses: the changes the loop could make and never could.

The agentic loop (:mod:`deepcompare.harness.loop`) has exactly one
actuator. ``ACTIONS`` is ``("compare", "test-prompt", "stop")`` and the
state it edits is ``state["prompts"]``. It can change how an agent
thinks, and nothing else.

Meanwhile the triage engine classifies every recommendation it makes by
*where the fix lives* (:data:`deepcompare.triage.EFFORT`), and of its
nineteen categories only four are prompt-shaped:

=================  ==  ===================================================
``prompt``          4  retrieval, tool_selection, planning, reasoning
``tool-schema``     3  tool_availability, tool_execution, grounding
``control-flow``    3  efficiency, parallel_reads, recovery
``architecture``    3  safety, verification, calibration
``infrastructure``  2  prompt_cache, result_cache
``investigation``   4  latency_concentration, attribute, regression, oracle
=================  ==  ===================================================

Eleven of those nineteen are the scaffold — the tools the agent is
offered and the loop that runs it — and the agentic loop is blind to all
eleven. It can be told "reconcile the tool list with the tools it
actually calls" and has no way to change a tool list. So the engine can
*recommend* a scaffold change, :mod:`deepcompare.harnessevo` can *detect*
that a gain came from the scaffold, and the thing that drives improvement
can do neither.

This module is the hypothesis source that closes that. It reads the
triage actions the engine already produced and returns two lists:

``proposed``
    scaffold changes expressed in a knob a harness can genuinely vary.
    There are two, because there are two: the tool table a run is offered
    (``Trajectory.tools``) and the limits the loop enforces
    (``Trajectory.budget``). Both are exactly what
    :func:`deepcompare.harnessevo.fingerprint` reads back, so a change
    made here is visible to the reading that judges it.

``unactionable``
    every other scaffold recommendation, with the effort class and the
    reason no knob reaches it. This list is not a failure of the module;
    it is the finding, and it is longer than the other one.

Pure stdlib, no network: an aggregate in, hypotheses out. Nothing here
runs an agent or decides anything — :mod:`deepcompare.planner` schedules
these and the paired experiment decides.
"""

from __future__ import annotations

import re
from typing import Optional

from ._text import join_names, plural
from .triage import EFFORT

VERSION = 1

#: Where each of the triage engine's effort classes lives, in the terms
#: `harnessevo.KINDS` uses. ``None`` is not a change to the agent at all.
WHERE: dict = {
    "prompt": "reasoning",
    "tool-schema": "scaffold",
    "control-flow": "scaffold",
    "infrastructure": "scaffold",
    "architecture": "scaffold",
    "investigation": None,
}

#: The knobs a harness can actually vary, and what each lands in.  Kept
#: this short on purpose: a hypothesis the runner cannot express is not a
#: hypothesis, it is a wish, and it belongs in `unactionable` where it can
#: be counted.
KNOBS: dict = {
    "tools": "the tool table a run is offered (Trajectory.tools)",
    "budget": "the limits the loop enforces (Trajectory.budget)",
}

#: effort classes whose findings point at the tool table
_TOOL_CLASSES = ("tool-schema",)
#: the terminations that mean the harness stopped the run, not the agent
_HARNESS_STOPS = ("budget_exhausted", "max_steps", "step_limit", "timeout")
#: a tool named in a finding is only withdrawn when the agent leaned on it
MIN_CALLS = 3
#: raise the cap by this much of itself when runs are hitting it
CAP_STEP = 0.5
#: the share of runs that must end on the harness's cap before raising it
CAP_SHARE = 0.2


def _tool_names(text: str, offered) -> list:
    """Tool names a finding's text quotes, kept to the ones on offer.

    The triage detail quotes the step it saw (``at "read_file"``), so the
    names are read from the quotes rather than guessed out of prose.
    """
    offered = {str(t) for t in offered or []}
    quoted = set(re.findall(r'"([^"]{1,64})"', text or "")) | set(re.findall(r"`([^`]{1,64})`", text or ""))
    return sorted(n for n in quoted if n in offered)


def _action_text(action: dict) -> str:
    bits = [str(action.get("title") or "")]
    ev = action.get("evidence") or {}
    bits.extend(str(d) for d in (ev.get("details") or []))
    return " ".join(bits)


def _for_agent(action: dict, agent: str) -> bool:
    ev = action.get("evidence") or {}
    agents = [str(a) for a in (ev.get("agents") or [])]
    return not agents or agent in agents


def hypotheses(aggregate: dict, agent: str, *, tools=(), budget=None,
               terminations: Optional[dict] = None, calls: Optional[dict] = None) -> dict:
    """Scaffold hypotheses for one agent, from the engine's own findings.

    ``tools`` is the tool table the agent is being offered and ``budget``
    the limits in force — the two things a variant can differ in.
    ``terminations`` is ``{termination: runs}`` over this agent's episodes
    and ``calls`` ``{tool: n}``; both are read from the traces by the
    caller because an aggregate does not carry them per agent.

    Returns ``{"proposed": [...], "unactionable": [...], "skipped": [...]}``.
    A proposal is ``{"kind", "knob", "change", "why", "from_tasks",
    "source", "category", "effort"}`` where ``change`` is the difference
    from the current scaffold, never the whole of it.
    """
    offered = [str(t.get("name") if isinstance(t, dict) else t) for t in (tools or [])]
    offered = [t for t in offered if t]
    triage = (aggregate or {}).get("triage") or {}
    actions = [a for a in (triage.get("actions") or []) if isinstance(a, dict) and _for_agent(a, agent)]
    proposed, unactionable, skipped = [], [], []
    seen_kinds: set = set()

    for action in actions:
        category = str(action.get("category") or "")
        effort = (action.get("effort") or {}).get("class") or (EFFORT.get(category) or ("investigation",))[0]
        where = WHERE.get(effort)
        ev = action.get("evidence") or {}
        from_tasks = [str(t) for t in (ev.get("tasks") or [])]
        row = {"category": category, "effort": effort, "title": action.get("title"),
               "from_tasks": from_tasks}
        if where == "reasoning":
            skipped.append({**row, "reason": "prompt-shaped: the loop already tests these as prompt hypotheses"})
            continue
        if where is None:
            skipped.append({**row, "reason": "investigation, not a change: there is nothing to test until someone looks"})
            continue

        # a tool-schema finding points at the tool table, which is a knob
        if effort in _TOOL_CLASSES and offered:
            named = [n for n in _tool_names(_action_text(action), offered)
                     if (calls or {}).get(n, MIN_CALLS) >= MIN_CALLS]
            named = [n for n in named if len(offered) > 1]
            if named:
                kind = "withdraw_tool:" + named[0]
                if kind in seen_kinds:
                    continue
                seen_kinds.add(kind)
                proposed.append({
                    "kind": kind, "knob": "tools", "change": {"drop_tools": [named[0]]},
                    "category": category, "effort": effort, "source": "triage",
                    "from_tasks": from_tasks,
                    "why": (f"{action.get('title')} — a {effort} finding over "
                            f"{plural(len(from_tasks), 'task')}. The tool table is a knob this harness has, so the "
                            f"hypothesis is testable: withdraw {named[0]} and see whether the agent does better "
                            f"without it. A win here is the scaffold's, not the agent's."),
                })
                continue
            unactionable.append({**row, "reason": (
                "a tool-schema finding, but it names no tool this run is offered that was called at least "
                + plural(MIN_CALLS, "time") + ", so there is nothing specific to withdraw")})
            continue

        unactionable.append({**row, "reason": (
            f"{effort} is the scaffold, but this harness varies only "
            + join_names(sorted(KNOBS)) + f", and no {effort} change is expressible in either")})

    # runs the harness stopped are a budget hypothesis, not an agent one
    total = sum((terminations or {}).values())
    stopped = sum(n for t, n in (terminations or {}).items() if str(t) in _HARNESS_STOPS)
    if total and stopped / total >= CAP_SHARE:
        cap = None
        for key in ("max_steps", "steps", "step_cap"):
            if isinstance(budget, dict) and isinstance(budget.get(key), (int, float)):
                cap = (key, int(budget[key]))
                break
        if cap:
            kind = "raise_cap:" + cap[0]
            if kind not in seen_kinds:
                seen_kinds.add(kind)
                nxt = int(cap[1] + max(1, round(cap[1] * CAP_STEP)))
                proposed.append({
                    "kind": kind, "knob": "budget", "change": {"budget": {cap[0]: nxt}},
                    "category": "budget", "effort": "control-flow", "source": "terminations",
                    "from_tasks": [],
                    "why": (f"{stopped} of {plural(total, 'run')} ended because the harness stopped them, not because "
                            f"the agent did. That is the loop's own limit and not a fault of the agent, so raise "
                            f"{cap[0]} {cap[1]} → {nxt} and see whether the outcome moves. A win here is the "
                            f"scaffold's: it buys room rather than making the agent need less."),
                })
        else:
            unactionable.append({"category": "budget", "effort": "control-flow", "title": None, "from_tasks": [],
                                 "reason": (f"{stopped} of {plural(total, 'run')} were stopped by the harness, but no "
                                            f"step cap is recorded in the budget, so there is no number to raise")})
    return {"version": VERSION, "proposed": proposed, "unactionable": unactionable, "skipped": skipped,
            "knobs": dict(KNOBS), "reading": reading(proposed, unactionable, skipped)}


def reading(proposed: list, unactionable: list, skipped: list) -> str:
    """One sentence on what the engine asked for and what could be tried."""
    if not proposed and not unactionable:
        return ("No scaffold recommendation on this reading: every actionable finding is prompt-shaped, which the "
                "loop already tests.")
    bits = []
    if proposed:
        bits.append(plural(len(proposed), "scaffold hypothesis", "scaffold hypotheses") + " can be tested here ("
                    + join_names(sorted({p["knob"] for p in proposed})) + ")")
    if unactionable:
        classes = sorted({u["effort"] for u in unactionable})
        bits.append(plural(len(unactionable), "scaffold recommendation") + " cannot: "
                    + join_names(classes) + " name changes this harness has no knob for")
    if skipped:
        bits.append(plural(len([s for s in skipped if "prompt-shaped" in s["reason"]]), "prompt-shaped finding")
                    + " is left to the prompt loop")
    return "; ".join(bits).capitalize() + "."


def apply_change(scaffold: dict, change: dict) -> dict:
    """The scaffold a variant runs under: the current one with ``change``
    applied. Never mutates the input, and never invents a knob — an
    unknown key is ignored rather than silently becoming a setting."""
    tools = [t for t in (scaffold or {}).get("tools") or []]
    budget = dict((scaffold or {}).get("budget") or {})
    drop = {str(t) for t in (change or {}).get("drop_tools") or []}
    if drop:
        tools = [t for t in tools if str(t.get("name") if isinstance(t, dict) else t) not in drop]
    add = (change or {}).get("budget")
    if isinstance(add, dict):
        for k, v in add.items():
            if isinstance(v, (int, float)):
                budget[str(k)] = v
    return {"tools": tools, "budget": budget}


def describe(change: dict) -> str:
    """What a change does, in one clause, for a ledger row."""
    bits = []
    for name in (change or {}).get("drop_tools") or []:
        bits.append(f"withdraw the {name} tool")
    for k, v in ((change or {}).get("budget") or {}).items():
        bits.append(f"set {k} to {v}")
    return join_names(bits) or "no change"


__all__ = ["VERSION", "WHERE", "KNOBS", "MIN_CALLS", "CAP_SHARE", "CAP_STEP",
           "hypotheses", "reading", "apply_change", "describe"]
