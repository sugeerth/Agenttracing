"""Agent-as-a-Judge: a judge that *reads* the run instead of being handed a window.

The LLM judge in :mod:`deepcompare.harness.judge` has a measured ceiling
that no amount of prompting moves. A three-hundred-step run does not fit
in a prompt, so it is shown an excerpt, and on the long-horizon suite the
best excerpt anyone has built — 40 steps chosen by what the run itself
flags — contains the step where the run went wrong **75% of the time**
(`docs/HORIZON.md`). The other quarter is a verdict about a part of the
run that does not contain the failure, and nothing in the verdict says so.

The way out is not a bigger window. It is to stop choosing one: let the
judge ask. This follows *Agent-as-a-Judge* (Zhuge et al., 2024), which
reports 92.07% / 90.44% alignment with human consensus against
LLM-as-a-Judge's 70.76% / 60.38% on the same tasks, at 2.3% of the cost
of human evaluation. Their ablation keeps five modules — **graph, locate,
read, retrieve, ask** — and drops three, of which the finding that
matters here is that **memory hurt**: carrying earlier judgements forward
let one wrong call start a chain of them.

What that framework navigates is a workspace of files. What this one
navigates is the trace, which is the same idea over the artefact this
repository actually has:

===============  ===================================================
``graph``        the shape of the run: how long, which tools, how
                 often, and where its phases fall
``locate``       the steps whose call or result mentions a term
``read``         a range of steps, in full
``retrieve``     **the deterministic reading** — every place
                 ``deepcompare.excerpt`` flags, with its reason. The
                 engine already knows where a run stops behaving like
                 one that is going well, and it knows it without being
                 told what the task was; handing that to the judge as a
                 tool is this repository's one addition to the method
``answer``       the verdict, in the schema the card reads
===============  ===================================================

Three things this refuses to do, each for a reason that is measured
somewhere rather than assumed:

* **No memory between requirements.** Each is judged in its own loop with
  its own tool budget. This is the paper's ablation, taken at its word.
* **The requirements are the spec, never the answer.** A golden
  milestone has an ``id``/``label`` — what had to be done — and an
  ``evidence`` string — what the world said when it was. The first is
  the task; the second is the mark scheme. Only the first is ever shown
  (:func:`requirements_of`), and a test asserts it.
* **It cannot move a number.** Like every judge here, the verdict is
  recorded beside the card's own findings and never inside them.

The judge runs through :func:`deepcompare.harness.agent.run_task`, so it
is an agent like any other and its own run is a SCHEMA trace. A judge
that loops, or gives up, or answers without reading anything is visible
in exactly the way this repository measures every other agent — which
seemed the only honest way to ship one.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .agent import Tool, run_task
from .providers import Provider, ProviderError

#: how many turns the judge gets per requirement.  Small on purpose: the
#: tools are there so it can find the one place that settles the
#: question, not so it can read the whole run at a higher price than
#: sending the whole run would have cost.
TURNS = 8

#: how many steps `read` will return at once
READ_CAP = 40
#: how many hits `locate` will return
LOCATE_CAP = 25

SYSTEM = (
    "You are judging whether an AI agent actually did a piece of work, by reading the "
    "recorded trace of its run. You cannot see the run up front: use the tools to look. "
    "\n\n"
    "graph() first — it tells you how long the run is and what it did. Then locate(term) to "
    "find where a thing happened, read(from, to) to see those steps, and flags() to ask the "
    "deterministic analysis where this run stops behaving like one that is going well. "
    "\n\n"
    "Judge the work, not the prose. An agent saying a thing was done is not evidence that it "
    "was; a check, a test result or a read-back from the world is. If the trace does not show "
    "it, treat it as not done and say what you looked for. Length is not quality. "
    "\n\n"
    "When you are certain, reply with JSON only and no tool call: "
    '{"success": true|false, "score": 0.0-1.0, "rationale": "one or two sentences naming the '
    'step indexes you relied on"}.'
)


def requirements_of(golden_task: Optional[dict]) -> list:
    """The task's requirements as a judge may see them.

    A golden milestone is ``{id, label, evidence}``: the first two say
    what had to happen, the third is the string the *world* produced when
    it did. Showing the third would be handing over the mark scheme and
    then scoring the judge for finding it, so only the first two leave
    this function — and nothing else from the golden task does either.
    """
    out = []
    for stone in (golden_task or {}).get("milestones") or []:
        if not isinstance(stone, dict):
            continue
        name = str(stone.get("label") or stone.get("id") or "").strip()
        if name:
            out.append({"id": str(stone.get("id") or name), "requirement": name})
    return out


def _line(step: dict) -> str:
    return (f"[{step.get('index')}] {step.get('type')} {step.get('name', '')}: "
            f"{str(step.get('input', ''))[:200]} -> {str(step.get('output', ''))[:300]}")


def trace_tools(trace: dict, policy: Optional[dict] = None) -> list:
    """The judge's instruments over one trace: graph, locate, read, retrieve.

    All four are read-only and pure — no network, no files, nothing that
    can change the run being judged.
    """
    steps = list(trace.get("steps") or [])
    by_index = {s.get("index"): s for s in steps}

    def graph() -> dict:
        kinds: dict = {}
        tools: dict = {}
        for s in steps:
            kinds[s.get("type")] = kinds.get(s.get("type"), 0) + 1
            if s.get("name"):
                tools[s["name"]] = tools.get(s["name"], 0) + 1
        first = {name: min(s["index"] for s in steps if s.get("name") == name) for name in tools}
        return {"steps": len(steps),
                "indexes": [steps[0].get("index"), steps[-1].get("index")] if steps else [],
                "step_kinds": kinds,
                "tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
                "first_use": dict(sorted(first.items(), key=lambda kv: kv[1])),
                "task": str((trace.get("task") or {}).get("prompt") or "")[:600],
                "note": "the answer step is the last one; read(from, to) shows any range in full"}

    def locate(term: str, kind: str = "") -> dict:
        """Steps whose call, input or output mentions `term`."""
        needle = str(term or "").lower()
        hits = []
        for s in steps:
            if kind and s.get("type") != kind:
                continue
            hay = f"{s.get('name') or ''} {s.get('input') or ''} {s.get('output') or ''}".lower()
            if needle and needle in hay:
                hits.append({"index": s.get("index"), "type": s.get("type"),
                             "name": s.get("name"), "excerpt": _line(s)[:260]})
        return {"term": term, "found": len(hits), "shown": min(len(hits), LOCATE_CAP),
                "steps": hits[:LOCATE_CAP],
                "note": "more than shown were found; narrow the term" if len(hits) > LOCATE_CAP else ""}

    def read(start: int, end: int) -> dict:
        """The steps from `start` to `end` inclusive, in full."""
        try:
            lo, hi = int(start), int(end)
        except (TypeError, ValueError):
            return {"error": "from and to must be step indexes"}
        chosen = [s for s in steps if lo <= (s.get("index") or -1) <= hi]
        clipped = len(chosen) > READ_CAP
        return {"from": lo, "to": hi, "steps": [_line(s) for s in chosen[:READ_CAP]],
                "clipped": clipped,
                "note": f"{len(chosen)} steps in range, {READ_CAP} shown" if clipped else ""}

    def flags() -> dict:
        """Where the deterministic analysis says this run stops behaving
        like one that is going well — each with the reason it fired."""
        from ..excerpt import notable_steps
        from ..trace import Trajectory
        try:
            marks = notable_steps(Trajectory.from_dict(trace), policy)
        except Exception:                               # noqa: BLE001
            return {"measurable": False,
                    "reason": "the trace could not be read by the analysis; use locate and read"}
        ranked = sorted(marks, key=lambda m: (-m["weight"], m["index"]))[:20]
        return {"measurable": True, "found": len(marks),
                "places": [{"index": m["index"], "kind": m["kind"], "why": m["why"]} for m in ranked],
                "note": ("computed from the trace alone — it has not been told what the task was, "
                         "so a place it flags is a place to look, not a verdict")}

    return [
        Tool(name="graph", fn=graph, effect="read",
             description="The shape of the run: how many steps, which tools, how often, when each first appeared.",
             parameters={"type": "object", "properties": {}}),
        Tool(name="locate", fn=locate, effect="read",
             description="Find the steps whose call, input or output mentions a term.",
             parameters={"type": "object",
                         "properties": {"term": {"type": "string", "description": "text to look for"},
                                        "kind": {"type": "string",
                                                 "description": "optional step type: tool_call, read, reason, answer"}},
                         "required": ["term"]}),
        Tool(name="read", fn=read, effect="read",
             description="Read a range of steps in full, by step index.",
             parameters={"type": "object",
                         "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
                         "required": ["start", "end"]}),
        Tool(name="flags", fn=flags, effect="read",
             description=("Ask the deterministic analysis where this run stops behaving like one that is "
                          "going well: unrepaired errors, work re-done, a write nothing checked, a beat "
                          "the run's own rhythm skipped, an irreversible act with the run still to come."),
             parameters={"type": "object", "properties": {}}),
    ]


def _verdict(answer: str) -> Optional[dict]:
    m = re.search(r"\{[\s\S]*\}", answer or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or "success" not in data:
        return None
    score = data.get("score")
    try:
        score = max(0.0, min(1.0, float(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return {"success": bool(data["success"]), "score": score,
            "rationale": str(data.get("rationale", ""))[:600]}


def judge_requirement(trace: dict, provider: Provider, requirement: str, *,
                      policy: Optional[dict] = None, turns: int = TURNS,
                      out_dir=None) -> dict:
    """Judge one requirement against one trace, with tools.

    Returns the verdict and — the part that makes this auditable — the
    judge's own run: how many turns it took, which tools it used, and
    whether it ever looked at anything before answering.
    """
    task = {"id": f"judge:{requirement[:60]}",
            "prompt": (f"Did this run do the following, and is there evidence in the trace that it was "
                       f"checked rather than merely claimed?\n\nREQUIREMENT: {requirement}")}
    tools = trace_tools(trace, policy)
    looked = {"calls": 0, "by_tool": {}}
    wrapped = []
    for tool in tools:
        def count(fn=tool.fn, name=tool.name):
            def inner(*a, **kw):
                looked["calls"] += 1
                looked["by_tool"][name] = looked["by_tool"].get(name, 0) + 1
                return fn(*a, **kw)
            return inner
        wrapped.append(Tool(name=tool.name, fn=count(), description=tool.description,
                            parameters=tool.parameters, effect=tool.effect))
    try:
        run = run_task(provider, task, wrapped, agent="agent-judge",
                       budget={"max_steps": turns}, grader=lambda answer, t: True,
                       system_prompt=SYSTEM, out_dir=out_dir)
        answer = str(((run or {}).get("outcome") or {}).get("answer") or "")
        error = None
    except ProviderError as exc:
        run, answer, error = None, "", f"provider error: {exc}"
    verdict = _verdict(answer)
    if not error and not verdict:
        error = "the judge did not return the JSON asked for"
    return {
        "requirement": requirement,
        "success": verdict["success"] if verdict else None,
        "score": verdict["score"] if verdict else None,
        "rationale": verdict["rationale"] if verdict else None,
        "error": error,
        # the judge's own behaviour, on the same terms as any other agent
        "turns": len((run or {}).get("steps") or []),
        "tool_calls": looked["calls"],
        "tools_used": dict(sorted(looked["by_tool"].items())),
        "looked_before_answering": looked["calls"] > 0,
        "trace_id": (run or {}).get("trace_id"),
        "raw": answer[:600],
    }


def judge_trace(trace: dict, provider_factory: Callable[[], Provider], *,
                golden_task: Optional[dict] = None, policy: Optional[dict] = None,
                turns: int = TURNS, apply: bool = False, out_dir=None) -> dict:
    """Judge a trace requirement by requirement, and record the result as
    ``outcome.agent_judge``.

    Each requirement gets a **fresh provider and a fresh loop**: no
    memory between them. That is the one ablation from the paper this
    follows to the letter, because the finding was not that memory failed
    to help — it was that a wrong judgement carried forward started a
    chain of them.

    With no golden task, or one that names no milestones, there is one
    requirement — the task's own prompt — and the block says so rather
    than reporting a per-requirement score over nothing.
    """
    requirements = requirements_of(golden_task)
    basis = "one verdict per golden milestone, judged independently"
    if not requirements:
        prompt = str((trace.get("task") or {}).get("prompt") or "").strip()
        requirements = [{"id": "task", "requirement": prompt or "complete the task as asked"}]
        basis = ("no milestones in the golden task, so this is one verdict over the whole task — "
                 "the per-requirement reading needs requirements")

    rows = []
    for req in requirements:
        row = judge_requirement(trace, provider_factory(), req["requirement"],
                                policy=policy, turns=turns, out_dir=out_dir)
        row["id"] = req["id"]
        rows.append(row)

    answered = [r for r in rows if isinstance(r["success"], bool)]
    met = [r for r in answered if r["success"]]
    agent_model = str(((trace.get("agent") or {}).get("model")) or "")
    probe = provider_factory()
    judge_model = getattr(probe, "model", "") or getattr(probe, "name", "agent-judge")
    block = {
        "kind": "agent-as-a-judge",
        "model": judge_model,
        "provider": getattr(probe, "kind", "provider"),
        "requirements": rows,
        "judged": len(answered),
        "of": len(rows),
        "met": len(met),
        "success": (len(met) == len(answered)) if answered else None,
        "score": round(len(met) / len(answered), 4) if answered else None,
        "failed_requirements": [r["id"] for r in answered if not r["success"]],
        "turns": sum(r["turns"] for r in rows),
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "looked_at_all": all(r["looked_before_answering"] for r in answered) if answered else None,
        "errors": [r["error"] for r in rows if r["error"]],
        # the guards the literature says to keep, recorded rather than assumed
        "self_judged": bool(agent_model) and _same_family(agent_model, judge_model),
        "answer_chars": len(str(((trace.get("outcome") or {}).get("answer")) or "")),
        "memory_between_requirements": False,
        "basis": basis,
        "applied": False,
    }
    outcome = trace.setdefault("outcome", {})
    prior = {"success": outcome.get("success"),
             "graded_by": outcome.get("graded_by",
                                      "exact-match" if (trace.get("task") or {}).get("expected") else "ungraded")}
    block["prior"] = prior
    block["agrees_with_prior"] = (block["success"] == prior["success"]
                                  if isinstance(prior["success"], bool) and isinstance(block["success"], bool)
                                  else None)
    if apply and isinstance(block["success"], bool):
        outcome["success"] = block["success"]
        outcome["score"] = block["score"]
        outcome["graded_by"] = "agent-model"
        block["applied"] = True
    outcome["agent_judge"] = block
    return block


def _same_family(agent_model: str, judge_model: str) -> bool:
    """Whether the judge is the agent's own kind.

    Not string equality: a judge scores its own family's work higher by a
    reported 10–25%, and `gpt-4o` judging `gpt-4o-mini` is the same
    family however different the two strings are. The first token before
    a dash or a slash is the family.
    """
    def family(name: str) -> str:
        return re.split(r"[-/:]", str(name).strip().lower(), 1)[0]
    return bool(agent_model) and bool(judge_model) and family(agent_model) == family(judge_model)


__all__ = ["TURNS", "SYSTEM", "requirements_of", "trace_tools",
           "judge_requirement", "judge_trace"]
