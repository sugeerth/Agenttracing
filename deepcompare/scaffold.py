"""Scaffold hypotheses: the changes the loop could make and never could.

The agentic loop (:mod:`deepcompare.harness.loop`) had exactly one
actuator. ``ACTIONS`` was ``("compare", "test-prompt", "stop")`` and the
state it edited was ``state["prompts"]``: it could change how an agent
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
offered and the loop that runs it — and the loop was blind to all eleven.
It could be told "reconcile the tool list with the tools it actually
calls" and had no way to change a tool list. So the engine could
*recommend* a scaffold change, :mod:`deepcompare.harnessevo` could
*detect* that a gain came from the scaffold, and the thing that drives
improvement could do neither.

This module is the hypothesis source that closes that. It reads the
triage actions the engine already produced and returns two lists:

``proposed``
    scaffold changes expressed in a knob a harness can genuinely vary:
    the tool table a run is offered (``Trajectory.tools``) and the
    settings the loop enforces (``Trajectory.budget``). Both are exactly
    what :func:`deepcompare.harnessevo.fingerprint` reads back, so a
    change made here is visible to the reading that judges it. That is
    the rule the knob list is held to, and the reason it is short: a knob
    whose effect no trace records could never be judged.

``unactionable``
    every other scaffold recommendation, with the effort class and the
    reason no knob reaches it. This list is not a failure of the module;
    it is the finding, and it is still the longer of the two.

Five settings inside ``budget`` are read by the loop
(:data:`BUDGET_KNOBS`), and each was added to answer a recommendation
class this module had to refuse:

=============================  ====================================
``max_steps``                  the harness stopping runs (terminations)
``max_tool_errors``            ``recovery`` (control-flow)
``dedupe_tool_calls``          ``result_cache`` (infrastructure)
``require_before_answer``      ``verification``, ``calibration``
                               (architecture)
``require_read_before_write``  ``safety`` (architecture)
=============================  ====================================

With the last of them the whole ``architecture`` class is reachable.
Most of its findings are still unactionable — but with the sentence
their own guard wrote.

Each rule is guarded by what the traces actually say, and a guard that
does not clear puts the finding in ``unactionable`` with the specific
reason rather than the generic one. A ``recovery`` finding on runs that
all reached an answer stays unactionable: moving a cap no run reached
changes nothing that was measured.

Pure stdlib, no network: an aggregate in, hypotheses out. Nothing here
runs an agent or decides anything — :mod:`deepcompare.planner` schedules
these and the paired experiment decides.
"""

from __future__ import annotations

import re
from typing import Optional

from ._text import join_names, pct, plural
from .trace import budget_value_ok
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

#: The settings inside ``budget`` the agent loop actually reads, and the
#: recommendation class each one answers.  A knob is only listed here once
#: :mod:`deepcompare.harness.agent` obeys it *and* the trace records it —
#: a knob whose effect no trace carries could never be judged, so there
#: isn't one.
BUDGET_KNOBS: dict = {
    "max_steps": "how many provider turns a run may take",
    "max_tool_errors": "how many failed tool calls end the run",
    "dedupe_tool_calls": "identical read-only calls served from a harness cache",
    "require_before_answer": "a tool the run must call before it may answer",
    "require_read_before_write": "the first write is refused until something has been read",
    "parallel_tool_calls": "how many declared reads may be in flight at once (under two is off)",
}

#: effort classes whose findings point at the tool table
_TOOL_CLASSES = ("tool-schema",)
#: the terminations that mean the harness stopped the run, not the agent
_HARNESS_STOPS = ("budget_exhausted", "max_steps", "step_limit", "timeout")
#: the termination that means the loop's tool-error cap ended the run.  It
#: is kept out of `_HARNESS_STOPS` on purpose: the errors were the agent's,
#: the harness only counted them and decided when to stop counting.
_ERROR_STOP = "too_many_errors"
#: categories whose fix is "cache the repeated identical call at the harness"
_CACHE_CATEGORIES = ("result_cache",)
#: categories whose fix is "verify before you claim success".  `calibration`
#: is here because the engine's own fix for it is an independent
#: verification step, not a confidence threshold.
_GATE_CATEGORIES = ("verification", "calibration")
#: categories about error handling around a tool call
_RECOVERY_CATEGORIES = ("recovery",)
#: categories whose fix is "read the state before you change it"
_SAFETY_CATEGORIES = ("safety",)
#: categories whose fix is "issue the independent reads at once"
_PARALLEL_CATEGORIES = ("parallel_reads",)
#: The last two, and the reason each is refused. A category with no knob
#: still deserves a sentence about *itself*: "this harness varies only
#: budget and tools" is true of every one of them and tells a reader
#: nothing about which door is shut.
_NO_KNOB_REASONS: dict = {
    "prompt_cache": (
        "a stable prompt prefix is what the provider's cache wants, and this loop already sends one — the system "
        "prompt and tool declarations lead every turn unchanged. Whether the provider caches it is the provider's "
        "to decide, not a setting here. What this harness can now do is *measure* it: a step records "
        "cached_tokens when the provider says how much of the input it served from its own cache, so this "
        "finding can be checked against what was actually paid for"),
    "efficiency": (
        "the loop can already cap a run (max_steps), serve an identical repeat from a cache (dedupe_tool_calls) "
        "and overlap independent reads (parallel_tool_calls) — those are the efficiency findings that are the "
        "harness's. What is left here is the agent gathering more once its evidence is sufficient, and no "
        "setting makes an agent stop: that one is prompt-shaped and belongs to the prompt loop"),
}
#: reads in flight at once when the rule proposes it, capped so the
#: hypothesis is a schedule change and not a load test
MAX_PARALLEL = 8
#: a tool named in a finding is only withdrawn when the agent leaned on it
MIN_CALLS = 3
#: raise the cap by this much of itself when runs are hitting it
CAP_STEP = 0.5
#: the share of runs that must end on the harness's cap before raising it
CAP_SHARE = 0.2
#: the share of runs that must end on the tool-error cap before raising it
ERROR_SHARE = 0.2
#: the loop's tool-error cap when a budget does not name one, kept in step
#: with `deepcompare.harness.agent.run_task`
DEFAULT_TOOL_ERRORS = 3


def _tool_names(text: str, offered) -> list:
    """Tool names a finding's text quotes, kept to the ones on offer.

    The triage detail quotes the step it saw (``at "read_file"``), so the
    names are read from the quotes rather than guessed out of prose.
    """
    offered = {str(t) for t in offered or []}
    quoted = set(re.findall(r'"([^"]{1,64})"', text or "")) | set(re.findall(r"`([^`]{1,64})`", text or ""))
    return sorted(n for n in quoted if n in offered)


def _effects(tools) -> dict:
    """``{tool name: declared effect}`` over the offered table.

    ``None`` stays ``None``: an undeclared effect is undeclared, and the
    one thing this module will not do is read it as "read-only".
    """
    out: dict = {}
    for t in tools or []:
        if isinstance(t, dict):
            name, effect = t.get("name"), t.get("effect")
        else:
            name, effect = getattr(t, "name", None), getattr(t, "effect", None)
        if name:
            out[str(name)] = str(effect) if effect else None
    return out


def _read_only(effects: dict) -> list:
    """Offered tools that declare a read effect, sorted."""
    return sorted(n for n, e in (effects or {}).items() if e and str(e).startswith("read"))


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
               terminations: Optional[dict] = None, calls: Optional[dict] = None,
               enforces_budget: bool = True) -> dict:
    """Scaffold hypotheses for one agent, from the engine's own findings.

    ``tools`` is the tool table the agent is being offered and ``budget``
    the limits in force — the two things a variant can differ in.
    ``terminations`` is ``{termination: runs}`` over this agent's episodes
    and ``calls`` ``{tool: n}``; both are read from the traces by the
    caller because an aggregate does not carry them per agent.

    ``enforces_budget`` is false for an agent that runs its own loop (an
    external adapter). The harness stamps the budget on such a trace but
    nothing obeys it, so turning a setting there would move the
    fingerprint without moving the run — a harness change that did not
    happen. Every budget rule then goes to ``unactionable`` with that
    reason, and only the tool table, which the runner really does hand
    over, stays actionable.

    Returns ``{"proposed": [...], "unactionable": [...], "skipped": [...]}``.
    A proposal is ``{"kind", "knob", "change", "why", "from_tasks",
    "source", "category", "effort"}`` where ``change`` is the difference
    from the current scaffold, never the whole of it.
    """
    effects = _effects(tools)
    offered = [n for n in effects]
    budget = budget if isinstance(budget, dict) else {}
    triage = (aggregate or {}).get("triage") or {}
    actions = [a for a in (triage.get("actions") or []) if isinstance(a, dict) and _for_agent(a, agent)]
    proposed, unactionable, skipped = [], [], []
    seen_kinds: set = set()

    # the terminations are read once: three rules below ask what ended the
    # runs, and they must all be asking about the same runs
    total = sum((terminations or {}).values())
    stopped = sum(n for t, n in (terminations or {}).items() if str(t) in _HARNESS_STOPS)
    errored = sum(n for t, n in (terminations or {}).items() if str(t) == _ERROR_STOP)
    not_enforced = (f"the budget is a knob this harness has, but {agent} runs its own loop: the harness records "
                    f"the settings on the trace and nothing obeys them, so turning one would move the harness "
                    f"fingerprint without moving the run")

    def keep(entry: dict) -> bool:
        if entry["kind"] in seen_kinds:
            return False
        seen_kinds.add(entry["kind"])
        proposed.append(entry)
        return True

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
                keep({
                    "kind": "withdraw_tool:" + named[0], "knob": "tools",
                    "change": {"drop_tools": [named[0]]},
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

        # "cache the repeated identical tool call at the harness" is a knob
        # the loop has, and it is the engine's own words for the fix
        if category in _CACHE_CATEGORIES:
            reason = not_enforced if not enforces_budget else _cache_rule(
                budget, effects, action, keep, from_tasks, category, effort)
            if reason:
                unactionable.append({**row, "reason": reason})
            continue

        # "verify before you claim success" is a gate the loop can enforce,
        # but only against a tool someone has named
        if category in _GATE_CATEGORIES:
            reason = not_enforced if not enforces_budget else _gate_rule(
                budget, effects, action, keep, from_tasks, category, effort, calls)
            if reason:
                unactionable.append({**row, "reason": reason})
            continue

        # "issue the independent reads at once" — expressible only since
        # the trace could say when a step began; see `_parallel_rule`
        if category in _PARALLEL_CATEGORIES:
            reason = not_enforced if not enforces_budget else _parallel_rule(
                budget, effects, action, keep, from_tasks, category, effort)
            if reason:
                unactionable.append({**row, "reason": reason})
            continue

        # "read before you write" is a gate the loop can enforce, but only
        # where the experiment that follows could see it
        if category in _SAFETY_CATEGORIES:
            reason = not_enforced if not enforces_budget else _safety_rule(
                budget, effects, action, keep, from_tasks, category, effort)
            if reason:
                unactionable.append({**row, "reason": reason})
            continue

        # error handling around a tool call: the loop owns exactly one
        # number here, and it only matters if it is what ended the runs
        if category in _RECOVERY_CATEGORIES:
            reason = not_enforced if not enforces_budget else _recovery_rule(
                budget, keep, from_tasks, category, effort, total, errored)
            if reason:
                unactionable.append({**row, "reason": reason})
            continue

        unactionable.append({**row, "reason": _NO_KNOB_REASONS.get(category) or (
            f"{effort} is the scaffold, but this harness varies only "
            + join_names(sorted(KNOBS)) + f", and no {effort} change is expressible in either")})

    # runs the harness stopped are a budget hypothesis, not an agent one
    if total and stopped / total >= CAP_SHARE and not enforces_budget:
        unactionable.append({"category": "budget", "effort": "control-flow", "title": None,
                             "from_tasks": [], "reason": not_enforced})
    elif total and stopped / total >= CAP_SHARE:
        cap = None
        for key in ("max_steps", "steps", "step_cap"):
            if isinstance(budget.get(key), (int, float)):
                cap = (key, int(budget[key]))
                break
        if cap:
            nxt = int(cap[1] + max(1, round(cap[1] * CAP_STEP)))
            keep({
                "kind": "raise_cap:" + cap[0], "knob": "budget",
                "change": {"budget": {cap[0]: nxt}},
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
            "knobs": dict(KNOBS), "budget_knobs": dict(BUDGET_KNOBS),
            "reading": reading(proposed, unactionable, skipped)}


def _cache_rule(budget: dict, effects: dict, action: dict, keep,
                from_tasks: list, category: str, effort: str) -> Optional[str]:
    """``dedupe_tool_calls``: same call, same arguments, served from the
    harness cache instead of paid for twice.

    Two guards, and the second one is the point.  A cache is only sound
    over calls that *read* — re-serving a write means the write silently
    did not happen the second time — so the loop caches read-declared
    tools only, and this rule will not propose a knob with nothing to
    cache.  An undeclared effect is undeclared, not read.
    """
    if budget.get("dedupe_tool_calls"):
        return ("the harness is already deduplicating identical read-only calls, so this finding is about "
                "repeats a cache cannot absorb")
    readable = _read_only(effects)
    if not readable:
        return ("caching repeated calls is a knob this harness has, but no tool on offer declares a read "
                "effect, and a repeat is only safe to serve from a cache when re-running it would have "
                "changed nothing")
    keep({
        "kind": "dedupe_tool_calls", "knob": "budget",
        "change": {"budget": {"dedupe_tool_calls": True}},
        "category": category, "effort": effort, "source": "triage",
        "from_tasks": from_tasks,
        "why": (f"{action.get('title')} — an {effort} finding over {plural(len(from_tasks), 'task')}. "
                f"The loop can serve an identical repeat from a cache, so the hypothesis is testable: turn "
                f"dedupe_tool_calls on for the {plural(len(readable), 'read-declared tool')} on offer "
                f"({join_names(readable)}) and see whether the outcome holds at lower cost. The repeat is "
                f"still recorded as a step, so the agent's behaviour stays visible; what changes is only "
                f"what the harness paid for it. A win here is the scaffold's."),
    })
    return None


def _gate_rule(budget: dict, effects: dict, action: dict, keep,
               from_tasks: list, category: str, effort: str, calls) -> Optional[str]:
    """``require_before_answer``: a tool the run must call before it may
    answer.

    The gate needs a tool to require, and this module will not pick one.
    The finding quotes the step it saw, so a name that is both quoted and
    on offer is a name someone stood behind; anything else would be the
    harness inventing the agent's verification step for it.
    """
    current = budget.get("require_before_answer")
    named = _tool_names(_action_text(action), list(effects))
    named = [n for n in named if n != current]
    if not named:
        if current:
            return (f"the harness already requires {current!r} before an answer, and this finding names no "
                    f"other tool on offer to require instead")
        return ("a gate before the answer is a knob this harness has, but the finding names no tool on "
                "offer to require, and the harness will not choose the agent's check for it")
    tool = named[0]
    seen = (calls or {}).get(tool)
    keep({
        "kind": "require_before_answer:" + tool, "knob": "budget",
        "change": {"budget": {"require_before_answer": tool}},
        "category": category, "effort": effort, "source": "triage",
        "from_tasks": from_tasks,
        "why": (f"{action.get('title')} — an {effort} finding over {plural(len(from_tasks), 'task')}. "
                f"The loop can hold an answer back until a named tool has been called, so the hypothesis is "
                f"testable: require {tool} before the answer"
                + (f" (the agent already calls it {plural(int(seen), 'time')} across these runs)" if seen else "")
                + f". The gate pushes back once and then lets the answer stand, because a harness that "
                f"refuses until it gets what it wants is writing the agent rather than measuring it. The "
                f"push-back spends a provider turn out of the same step cap, which is a real cost of the "
                f"change and is left to be measured rather than compensated for. If this wins it is the "
                f"scaffold's win — and it is exactly the shape harnessevo.absorption reads as the scaffold "
                f"carrying the run."),
    })
    return None


def _parallel_rule(budget: dict, effects: dict, action: dict, keep,
                   from_tasks: list, category: str, effort: str) -> Optional[str]:
    """``parallel_tool_calls``: issue the turn's independent reads at once.

    This one was unactionable until the trace could say when a step
    *began*. Not because the loop could not do it — it always could — but
    because nothing could have measured it: a timeline reconstructed by
    summing durations draws a concurrent run and a sequential one
    identically, so the experiment would have compared two pictures of the
    same length and found no difference. A knob whose effect no trace
    records could never be judged, and this was the case in point.

    The guard is the declaration. Only calls whose tool declares a read go
    out together, because the claim being made is that they do not affect
    one another, and an undeclared effect supports no such claim. Fewer
    than two read-declared tools on offer and there is nothing to overlap.
    """
    if isinstance(budget.get("parallel_tool_calls"), (int, float)) and budget["parallel_tool_calls"] >= 2:
        return (f"the harness already issues up to {int(budget['parallel_tool_calls'])} declared reads at once, so "
                f"this finding is about calls the schedule cannot overlap")
    readable = _read_only(effects)
    if len(readable) < 2:
        return ("issuing the reads at once is a knob this harness has, but "
                + ("no tool" if not readable else "only one tool") + " on offer declares a read effect, and a call "
                "whose effect is undeclared cannot be claimed not to affect the others")
    width = min(MAX_PARALLEL, len(readable))
    keep({
        "kind": "parallel_tool_calls", "knob": "budget",
        "change": {"budget": {"parallel_tool_calls": width}},
        "category": category, "effort": effort, "source": "triage",
        "from_tasks": from_tasks,
        "why": (f"{action.get('title')} — a {effort} finding over {plural(len(from_tasks), 'task')}. "
                f"The loop can issue a turn's declared reads together, so the hypothesis is testable: allow "
                f"{plural(width, 'read')} in flight at once over the {plural(len(readable), 'read-declared tool')} "
                f"on offer ({join_names(readable)}). A turn containing a write goes sequentially regardless — the "
                f"order of writes is part of what the run did — and the steps are still recorded in the order the "
                f"agent asked for them, with the times they really took, so the overlap is visible rather than "
                f"flattened. Judge it on timing.timeline.overlap_s and the wall clock, not on the token count: "
                f"this buys latency and nothing else, and it is the scaffold's win."),
    })
    return None


def _safety_rule(budget: dict, effects: dict, action: dict, keep,
                 from_tasks: list, category: str, effort: str) -> Optional[str]:
    """``require_read_before_write``: the first write is refused until
    something has been read.

    Four guards, and the fourth is the one worth reading.

    A gate needs something to gate (a write-declared tool) and something
    that can satisfy it (a read-declared tool): a gate the agent could
    never clear would refuse one write and buy nothing.

    And then the honest one. This loop decides every hypothesis by the
    outcome its grader measures. A gate that stops a state change buys
    safety, which the grader may not read at all — so when the finding
    sits only on runs that *passed*, the experiment cannot see the
    change, and would revert it for showing no difference. That is not a
    reason to pretend it is untestable here; it is a reason to say which
    reading is missing. The gap is in the eval, and naming it is more
    use than turning a knob whose effect nothing would score.
    """
    if budget.get("require_read_before_write"):
        return ("the harness already refuses a write before a read, so this finding is about writes the "
                "gate has already let through")
    writes = sorted(n for n, e in (effects or {}).items() if e and str(e).startswith("write"))
    if not writes:
        return ("a read-before-write gate is a knob this harness has, but no tool on offer declares a write "
                "effect, so there is nothing for it to hold back")
    if not _read_only(effects):
        return ("a read-before-write gate is a knob this harness has, but no tool on offer declares a read "
                "effect, so the agent could never clear the gate and it would refuse one write and buy "
                "nothing")
    passing = {str(t) for t in (action.get("on_passing_runs") or [])}
    if from_tasks and passing >= set(from_tasks):
        return ("a read-before-write gate is a knob this harness has, but every task this finding names also "
                "passed: the gate protects the state and this loop decides by the outcome the grader "
                "measures, so the experiment would see no difference and revert it. The missing reading is "
                "the eval's, not the harness's")
    keep({
        "kind": "require_read_before_write", "knob": "budget",
        "change": {"budget": {"require_read_before_write": True}},
        "category": category, "effort": effort, "source": "triage",
        "from_tasks": from_tasks,
        "why": (f"{action.get('title')} — an {effort} finding over {plural(len(from_tasks), 'task')}. "
                f"The loop can refuse the first write until something has been read, so the hypothesis is "
                f"testable: gate {join_names(writes)} and see whether the outcome moves. The refusal happens "
                f"once and then the gate is spent, and the held call stays on the trace as an errored write "
                f"— the agent did attempt it. Note what this does not do: it protects the state, it does not "
                f"teach the agent to look first, and writes_before_any_read will go on reporting the "
                f"attempt. A win here is the scaffold's, and it is the kind that does not travel."),
    })
    return None


def _recovery_rule(budget: dict, keep, from_tasks: list, category: str,
                   effort: str, total: int, errored: int) -> Optional[str]:
    """``max_tool_errors``: how many failed calls end the run.

    The loop owns this number and nothing else about error handling, so
    the rule only fires when the number is what ended the runs.  A
    recovery finding on runs that all reached an answer is real and is
    still unactionable here: raising or lowering a cap no run reached
    changes nothing that was measured.
    """
    if not total:
        return ("error handling is the scaffold, but no terminations were read for this agent, so there is "
                "no evidence the tool-error cap is what ended anything")
    if not errored or errored / total < ERROR_SHARE:
        return (f"error handling is the scaffold, and the one number the loop owns is the tool-error cap — "
                f"but {errored} of {plural(total, 'run')} ended on it, under the "
                f"{pct(ERROR_SHARE)} this rule needs, so moving it changes nothing that was measured")
    cap = budget.get("max_tool_errors")
    cap = int(cap) if isinstance(cap, (int, float)) else DEFAULT_TOOL_ERRORS
    nxt = int(cap + max(1, round(cap * CAP_STEP)))
    keep({
        "kind": "raise_cap:max_tool_errors", "knob": "budget",
        "change": {"budget": {"max_tool_errors": nxt}},
        "category": category, "effort": effort, "source": "triage+terminations",
        "from_tasks": from_tasks,
        "why": (f"{errored} of {plural(total, 'run')} ended on the harness's tool-error cap, not on an answer. "
                f"The errors were the agent's; the decision to stop counting at {cap} was the loop's. Raise "
                f"max_tool_errors {cap} → {nxt} and see whether those runs were one call from recovering or "
                f"going in circles — the second is as useful an answer as the first, and it is the one this "
                f"cap currently assumes without checking."),
    })
    return None


#: which knob each rule above can express a finding in.  Derived from the
#: rule constants themselves rather than written out again, so the map a
#: reader is shown cannot drift from the rules a hypothesis goes through.
def _rule_knobs() -> dict:
    out: dict = {}
    for category in _CACHE_CATEGORIES:
        out[category] = "budget: dedupe_tool_calls"
    for category in _GATE_CATEGORIES:
        out[category] = "budget: require_before_answer"
    for category in _SAFETY_CATEGORIES:
        out[category] = "budget: require_read_before_write"
    for category in _RECOVERY_CATEGORIES:
        out[category] = "budget: max_tool_errors"
    for category in _PARALLEL_CATEGORIES:
        out[category] = "budget: parallel_tool_calls"
    return out


def reach() -> dict:
    """Every category the engine can recommend, and whether this harness
    can act on it at all.

    The module's central claim is that a hypothesis the runner cannot
    express is not a hypothesis. That claim is about *the whole
    vocabulary*, not about whatever a particular batch happened to turn
    up, and until now it could only be read as a table in the docs — which
    is to say it could go out of date without anything noticing.

    Four verdicts, and the third is the one that matters:

    ``knob``          a rule here can express it, and in which knob
    ``prompt``        prompt-shaped; the loop already tests these
    ``no knob``       the scaffold, and nothing this harness varies reaches it
    ``investigation`` not a change at all until someone looks

    Returns ``{"rows", "counts", "reading"}``; the rows are sorted by
    effort class then category so the order is the vocabulary's and not
    a dict's.
    """
    knobs = _rule_knobs()
    rows, counts = [], {"knob": 0, "prompt": 0, "no knob": 0, "investigation": 0}
    for category, (effort, detail) in EFFORT.items():
        where = WHERE.get(effort)
        if where == "reasoning":
            verdict, knob = "prompt", None
        elif where is None:
            verdict, knob = "investigation", None
        elif category in knobs:
            verdict, knob = "knob", knobs[category]
        elif effort in _TOOL_CLASSES:
            verdict, knob = "knob", "tools: withdraw a tool"
        else:
            verdict, knob = "no knob", None
        counts[verdict] += 1
        rows.append({"category": category, "effort": effort, "detail": detail,
                     "verdict": verdict, "knob": knob})
    rows.sort(key=lambda r: (r["effort"], r["category"]))
    total = len(rows)
    return {"version": VERSION, "rows": rows, "counts": counts, "total": total,
             "knobs": dict(KNOBS), "budget_knobs": dict(BUDGET_KNOBS),
             "reading": (
                 f"Of {plural(total, 'category', 'categories')} the engine can recommend, "
                 f"{counts['knob']} can be expressed in a knob this harness has, {counts['prompt']} are "
                 f"prompt-shaped and go to the prompt loop, {counts['investigation']} are investigations rather "
                 f"than changes, and {counts['no knob']} name the scaffold with nothing here that reaches them. "
                 f"That last number is the honest one: it is what this harness cannot try, however the findings "
                 f"fall on any particular batch.")}


def seen_in(iterations) -> dict:
    """``{category: {"proposed", "unactionable"}}`` over a loop's own
    comparisons.

    The tally lives here and not on the page because the page does not do
    arithmetic: a count it computed would be a number with no reading
    behind it, and nothing to check it against.
    """
    out: dict = {}
    for it in iterations or []:
        for side in ((it or {}).get("scaffold") or {}).values():
            for key in ("proposed", "unactionable"):
                for row in (side or {}).get(key) or []:
                    category = str((row or {}).get("category") or "")
                    if not category:
                        continue
                    out.setdefault(category, {"proposed": 0, "unactionable": 0})[key] += 1
    return dict(sorted(out.items()))


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
        # "control-flow name changes this harness has no knob for" read as a
        # noun phrase and meant nothing; agreeing the verb only made it
        # "names changes". The sentence the reading wants is the plain one.
        bits.append(plural(len(unactionable), "scaffold recommendation") + " cannot: no knob reaches "
                    + join_names(classes, "or"))
    # only the prompt-shaped ones are "left to the prompt loop" — the other
    # skipped rows are investigations, which are left to a person.  Saying
    # "0 prompt-shaped findings is left to the prompt loop" claimed a
    # handover that did not happen, and disagreed with itself about number.
    prompt_shaped = len([s for s in skipped if "prompt-shaped" in s["reason"]])
    if prompt_shaped:
        bits.append(plural(prompt_shaped, "prompt-shaped finding")
                    + (" is" if prompt_shaped == 1 else " are") + " left to the prompt loop")
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
            # which shape a setting takes is the trace schema's decision, not
            # this module's: the same check the recorder applies, so a change
            # that gets past here cannot fail at the moment the run that
            # tests it is written down.  A value of the wrong shape is
            # dropped rather than quietly becoming a setting the loop would
            # then read as something else.
            if budget_value_ok(str(k), v):
                budget[str(k)] = v
    return {"tools": tools, "budget": budget}


def describe(change: dict) -> str:
    """What a change does, in one clause, for a ledger row."""
    bits = []
    for name in (change or {}).get("drop_tools") or []:
        bits.append(f"withdraw the {name} tool")
    for k, v in ((change or {}).get("budget") or {}).items():
        if k == "dedupe_tool_calls":
            bits.append("serve identical read-only calls from the harness cache"
                        if v else "stop caching identical calls")
        elif k == "require_before_answer":
            bits.append(f"require {v} before an answer" if v else "drop the answer gate")
        elif k == "parallel_tool_calls":
            bits.append(f"issue up to {int(v)} declared reads at once" if v and v >= 2
                        else "stop issuing reads together")
        elif k == "require_read_before_write":
            bits.append("refuse the first write until something has been read"
                        if v else "stop gating the first write")
        else:
            bits.append(f"set {k} to {v}")
    return join_names(bits) or "no change"


__all__ = ["VERSION", "WHERE", "KNOBS", "BUDGET_KNOBS", "MIN_CALLS", "CAP_SHARE", "reach",
           "CAP_STEP", "ERROR_SHARE", "DEFAULT_TOOL_ERRORS", "seen_in",
           "hypotheses", "reading", "apply_change", "describe"]
