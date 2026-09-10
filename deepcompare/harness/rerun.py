"""Predictable replay: re-execute a recorded run hermetically and diff it
against the recording.

Two questions a production team asks of a trace, answered without a
network:

* **Can this recording be reproduced?**  ``rerun(trace)`` replays the
  run with the model's own recorded turns and the world's recorded tool
  results (a :class:`~.cassette.Cassette`), through the same recorder
  and grader a live run uses, and diffs the result against the recording
  step by step.  A faithful rerun means every non-deterministic input was
  captured — the property Temporal enforces on workflow histories — so
  the trace is a fixture a CI job can replay forever.  An unfaithful one
  names the first step that differs and why.
* **What does a different model do in the recorded world?**
  ``rerun(trace, provider=...)`` drives that model through the run with
  the world frozen: every tool call is served from the cassette, so the
  first call the recording never made — a **cassette miss** — is exactly
  where the new model departed.  The diff and the misses are the debug
  session; no live tool ran.

Both produce one result shape: ``faithful``, ``first_divergence``, the
list of ``differences`` (index, field, recorded, replayed), the cassette
summary, both outcomes, and the replayed trace itself — a SCHEMA trace
that ``compare`` can align against the original like any other pair.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Union
from xml.etree import ElementTree as ET

from ..horizon import segment
from ..milestones import evaluate as evaluate_milestones, lost_and_gained
from ..record import Recorder
from ..trace import TERMINATIONS, Trajectory
from .agent import DEFAULT_SYSTEM, Tool, contains_grader, _drive
from .cassette import TOOLISH, Cassette, CassetteMiss, CassetteRecordedError, canonical_args, key_of_step, parse_call, same_words_grader
from .providers import Provider


def _family(step_type: str) -> str:
    return "tool" if step_type in TOOLISH else "answer" if step_type == "answer" else "think"


def _norm(text: Any) -> str:
    text = " ".join(str("" if text is None else text).split())
    # a recorded error replays as the same words behind the cassette's label
    for prefix in ("error: CassetteRecordedError: ", "CassetteRecordedError: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _text_of(step: dict) -> str:
    return _norm(step.get("input") or step.get("output") or "")


# --------------------------------------------------------------------------
# the mirror loop: the model's recorded turns, the cassette's world
# --------------------------------------------------------------------------

def _mirror(recorder: Recorder, steps: list, tools: list, task: dict, grade: Callable,
            termination: Optional[str] = None, until: Optional[int] = None, finish: bool = True) -> None:
    """Replay the recorded steps through the recorder: the model's words
    verbatim, every tool call served by the cassette tools, the answer
    re-graded.  The recorder measures nothing it did not see, so tokens
    are carried over as the recording's counts."""
    by_name = {t.name: t for t in tools}
    for step in steps:
        if until is not None and isinstance(step.get("index"), int) and step["index"] > until:
            break
        kind = str(step.get("type") or "reason")
        name = str(step.get("name") or "")
        tokens = step.get("tokens") if isinstance(step.get("tokens"), int) else None
        if kind == "answer":
            answer = str(step.get("output") or step.get("input") or "")
            verdict = grade(answer, task)
            recorder.answer(answer, success=bool(verdict), tokens=tokens, latency_s=0.0,
                            name=name or "final", input=str(step.get("input") or answer))
            return
        if kind in TOOLISH:
            parsed_name, args = parse_call(str(step.get("input") or ""))
            call_args: Any = args if (parsed_name and (parsed_name == name or not name)) else str(step.get("input") or "")
            tool = by_name.get(name or parsed_name)
            recorded_error = step.get("error") if isinstance(step.get("error"), bool) else None
            handle = recorder.tool(name or parsed_name, call_args, type=kind, effect=step.get("effect"),
                                   tokens=tokens, latency_s=0.0)
            if tool is None:
                handle.observe(f"error: no such tool {name!r}", error=True)
                continue
            try:
                result = tool.fn(call_args) if isinstance(call_args, str) else tool.fn(**call_args)
                handle.observe(result, error=recorded_error)
            except CassetteRecordedError as exc:
                handle.observe(str(exc), error=True)
            except CassetteMiss as exc:
                handle.observe(f"CassetteMiss: {exc}", error=True)
            continue
        recorder.step(kind, name or kind, str(step.get("input") or ""), str(step.get("output") or ""),
                      tokens=tokens, latency_s=0.0, error=step.get("error") if isinstance(step.get("error"), bool) else None)
    if finish and recorder._termination is None and not recorder._answered:
        recorder.terminate("user_stop" if until is not None else (termination if termination in TERMINATIONS else "agent_stop"))


# --------------------------------------------------------------------------
# the diff
# --------------------------------------------------------------------------

def diff_runs(recorded: dict, replayed: dict, *, from_step: int = 0, until: Optional[int] = None) -> dict:
    """Step-for-step comparison of two traces of the same run.

    A step matches when its family (think / tool / answer), its name,
    its call (canonicalised) and its output text agree; tokens, latency
    and cost are measurements and are not compared.  The first mismatch
    is ``first_divergence``; every mismatch is listed with the field and
    both values, and a length difference is one more entry.
    """
    a = list(recorded.get("steps") or [])
    b = list(replayed.get("steps") or [])
    if until is not None:
        a, b = a[:until + 1], b[:until + 1]
    differences: list = []
    for i in range(from_step, min(len(a), len(b))):
        sa, sb = a[i], b[i]
        checks = [("type", _family(str(sa.get("type"))), _family(str(sb.get("type"))))]
        if _family(str(sa.get("type"))) == "tool" and _family(str(sb.get("type"))) == "tool":
            # a thinking step's name is the recorder's label (plan, reason, final);
            # a tool step's name is the call, and the call is compared
            checks.append(("name", _norm(sa.get("name")), _norm(sb.get("name"))))
            checks.append(("call", key_of_step(sa), key_of_step(sb)))
        else:
            checks.append(("input", _norm(sa.get("input")), _norm(sb.get("input"))))
        checks.append(("output", _norm(sa.get("output")), _norm(sb.get("output"))))
        checks.append(("error", bool(sa.get("error")), bool(sb.get("error"))))
        for field, va, vb in checks:
            if va != vb:
                differences.append({"step": i, "field": field, "recorded": va, "replayed": vb})
    if len(a) != len(b) and max(len(a), len(b)) > from_step:
        differences.append({"step": min(len(a), len(b)), "field": "length", "recorded": len(a), "replayed": len(b)})
    oa, ob = recorded.get("outcome") or {}, replayed.get("outcome") or {}
    outcome = {"recorded": oa.get("success"), "replayed": ob.get("success"),
               "same": bool(oa.get("success")) == bool(ob.get("success")),
               "recorded_termination": oa.get("termination"), "replayed_termination": ob.get("termination")}
    first = min((d["step"] for d in differences), default=None)
    return {"faithful": not differences and outcome["same"], "first_divergence": first,
            "differences": differences, "steps": {"recorded": len(a), "replayed": len(b)},
            "outcome": outcome}


# --------------------------------------------------------------------------
# rerun
# --------------------------------------------------------------------------

def span_range(trace: dict, span: str) -> tuple:
    """The step range ``(first, last)`` of a sub-agent in a trace, by
    span id or agent name (the first span with that agent when several);
    nested spans count as inside.  Raises ``ValueError`` when absent."""
    steps = list(trace.get("steps") or [])
    by_id: dict = {}
    for s in steps:
        sp = s.get("span")
        if isinstance(sp, dict) and sp.get("id"):
            by_id.setdefault(sp["id"], {"agent": sp.get("agent"), "parent": sp.get("parent"), "steps": []})["steps"].append(int(s.get("index", 0)))
    if not by_id:
        raise ValueError(f"the trace records no spans, so --span {span!r} names nothing")
    target = span if span in by_id else next((sid for sid, sp in by_id.items() if sp["agent"] == span), None)
    if target is None:
        raise ValueError(f"no span {span!r}; the trace has " + ", ".join(f"{sid} ({sp['agent']})" for sid, sp in by_id.items()))
    inside = set()
    frontier = [target]
    while frontier:
        sid = frontier.pop()
        inside.add(sid)
        frontier.extend(c for c, sp in by_id.items() if sp["parent"] == sid and c not in inside)
    idx = sorted(i for sid in inside for i in by_id[sid]["steps"])
    return idx[0], idx[-1]


def rerun(trace: dict, *, provider: Optional[Provider] = None, tools: Optional[list] = None,
          policy: str = "strict", cassette: Optional[Cassette] = None,
          grader: Optional[Callable] = None, system_prompt: str = DEFAULT_SYSTEM,
          out_dir: Optional[Union[str, Path]] = None, run_id: str = "rerun",
          from_step: Optional[int] = None, until: Optional[int] = None, span: Optional[str] = None,
          milestones: Optional[list] = None) -> dict:
    """Replay ``trace`` hermetically and diff it against itself.

    Without ``provider`` the model side is the recording (the mirror
    loop); with one, that model is driven through the recorded world.
    ``tools`` are the declared tools — schemas for the model, and the
    live fallback under ``policy='live'``.  The result carries the
    replayed trace under ``trace``.

    **Scope, for a long run.**  ``from_step`` replays the prefix before
    it from the recording and starts the replay proper there — a resume
    from a checkpoint; ``until`` stops the replay after that step (the
    replayed run ends ``user_stop``); ``span`` sets both from a
    sub-agent's steps.  The diff covers the scoped range only, and the
    drift map says which sub-agents and parts inside it reproduced.
    ``milestones`` (a golden task's) are read on the recording and on
    the replay, and the ones the replay lost or gained are listed.
    """
    task = dict(trace.get("task") or {})
    if not task.get("id") or task.get("prompt") is None:
        raise ValueError("the trace needs task.id and task.prompt to be replayed")
    steps_all = list(trace.get("steps") or [])
    if span:
        from_step, until = span_range(trace, span)
    if from_step is not None and not (0 <= from_step < len(steps_all)):
        raise ValueError(f"from_step {from_step} is outside the trace's {len(steps_all)} steps")
    if until is not None and (until < (from_step or 0) or until >= len(steps_all)):
        raise ValueError(f"until {until} is outside the range {from_step or 0}..{len(steps_all) - 1}")
    cassette = cassette or Cassette.from_trace(trace)
    cassette.rewind()
    served = cassette.tools(tools, policy)
    # self mode reproduces the recording, verdict included: the same words
    # keep the recorded verdict.  A different model's new answer is graded
    # the way a live run is — against the expected answer when there is one.
    if grader is not None:
        grading = "as given"
    elif provider is None:
        grader, grading = same_words_grader(trace), "self: the recorded verdict, kept for the recorded answer"
    elif isinstance(task.get("expected"), str) and task["expected"].strip():
        grader, grading = contains_grader, "contains grader against task.expected"
    else:
        grader, grading = same_words_grader(trace), ("no expected answer: the recording's words keep its verdict, other "
                                                     "words count as the opposite — a weak proxy; judge it properly")
    grade = grader
    agent = (trace.get("agent") or {}).get("name", "agent")
    budget = dict(trace.get("budget") or {"max_steps": max(12, len(trace.get("steps") or []))})
    recorder = Recorder(
        task=str(task["id"]), prompt=str(task["prompt"]),
        agent=f"{agent}-{run_id}", model=(provider.model if provider else str((trace.get("agent") or {}).get("model") or "recording")),
        expected=task.get("expected") if isinstance(task.get("expected"), str) else None,
        run_id=run_id, tools=[t.schema_entry() for t in served] or None, budget=budget, out_dir=out_dir,
        trace_id=f"{task['id']}__{agent}-{run_id}")
    with recorder:
        prefix = steps_all[:from_step] if from_step else []
        if prefix:
            # the checkpoint: everything before `from_step` is the recording, replayed verbatim
            _mirror(recorder, prefix, served, task, grade, termination=None, until=None, finish=False)
        if provider is None:
            _mirror(recorder, steps_all[len(prefix):], served, task, grade,
                    termination=(trace.get("outcome") or {}).get("termination"), until=until)
        else:
            from .context import messages_before
            messages = messages_before(prefix, str(task["prompt"]), system_prompt)
            turns = (until - (from_step or 0) + 1) if until is not None else max(1, int(budget.get("max_steps") or 12) - len(prefix))
            _drive(recorder, provider, messages, served, task, grade, max(1, turns))
            if until is not None and recorder._termination is None and not recorder._answered:
                recorder.terminate("user_stop")
    replayed = recorder.to_dict()
    # a call to a tool the recording never used is a departure too: the
    # drive loop records it as "no such tool" before any cassette could see it
    for s in replayed.get("steps") or []:
        out = str(s.get("output") or "")
        if s.get("error") and out.startswith("error: no such tool"):
            parsed_name, args = parse_call(str(s.get("input") or ""))
            cassette.misses.append({"name": s.get("name"), "args": canonical_args(args if parsed_name else str(s.get("input") or "")),
                                    "call": len(cassette.hits) + len(cassette.misses), "step": s.get("index"), "undeclared": True})
    scope = {"from": from_step or 0, "until": until if until is not None else len(steps_all) - 1, "span": span,
             "steps": (until if until is not None else len(steps_all) - 1) - (from_step or 0) + 1, "total": len(steps_all)}
    result = diff_runs(trace, replayed, from_step=scope["from"], until=scope["until"])
    result.update({
        "trace_id": trace.get("trace_id"), "agent": agent, "task": task.get("id"),
        "mode": "self" if provider is None else f"provider:{provider.name}",
        "policy": policy, "grading": grading, "scope": scope, "cassette": cassette.summary(), "trace": replayed,
    })
    if until is not None and provider is None:
        # a scoped self-replay ends where asked; the outcome is not part of the question
        result["outcome"]["same"] = True
        result["faithful"] = not result["differences"]
    if cassette.misses:
        result["faithful"] = False
        if result["first_divergence"] is None:
            result["first_divergence"] = _first_miss_step(replayed)
    result["drift_map"] = drift_map(trace, result)
    if milestones:
        # like with like: a scoped replay is read against the recording cut at the same step
        recorded_view = trace if until is None else {"agent": trace.get("agent"), "steps": steps_all[:until + 1]}
        before = evaluate_milestones(recorded_view, milestones)
        after = evaluate_milestones(replayed, milestones)
        result["milestones"] = {"recorded": before, "replayed": after, **lost_and_gained(before, after)}
    result["reading"] = _reading(result)
    return result


def drift_map(trace: dict, result: dict) -> list:
    """The recording's horizon tree (run → sub-agents → parts → steps)
    with the replay's verdict on every node: how many of its steps were
    inside the scope, how many differed, how many cassette misses landed
    in it, and whether it reproduced.  Depth-first, one row per node,
    steps left out — the map a long run is read by."""
    try:
        tree = segment(Trajectory.from_dict(trace))
    except Exception:  # a trace the engine cannot segment still gets a flat verdict
        return []
    scope = result.get("scope") or {}
    lo, hi = scope.get("from", 0), scope.get("until", len(trace.get("steps") or []) - 1)
    diff_steps = {d["step"] for d in result.get("differences") or [] if isinstance(d.get("step"), int)}
    miss_steps = {m.get("step") for m in (result.get("cassette") or {}).get("misses") or [] if isinstance(m.get("step"), int)}
    rows: list = []

    def walk(node: dict, depth: int) -> None:
        if node.get("kind") == "step":
            return
        a, b = node.get("from", 0), node.get("to", -1)
        inside = [i for i in range(a, b + 1) if lo <= i <= hi]
        differed = sorted(i for i in inside if i in diff_steps)
        missed = sorted(i for i in inside if i in miss_steps)
        rows.append({"kind": node.get("kind"), "key": node.get("key"), "label": node.get("label"), "agent": node.get("agent"),
                     "depth": depth, "from": a, "to": b, "in_scope": len(inside), "differed": len(differed), "misses": len(missed),
                     "first_difference": (differed or missed or [None])[0] if (differed or missed) else None,
                     "reproduced": (not differed and not missed) if inside else None,
                     "seconds": node.get("seconds"), "wasted_s": node.get("wasted_s")})
        for c in node.get("children") or []:
            walk(c, depth + 1)

    walk(tree.get("tree") or {}, 0)
    return rows


def _first_miss_step(replayed: dict) -> Optional[int]:
    for s in replayed.get("steps") or []:
        out = str(s.get("output") or "")
        if out.startswith(("CassetteMiss", "error: CassetteMiss", "error: no such tool")):
            return s.get("index")
    return None


def _reading(result: dict) -> str:
    who = f"{result.get('agent')} on {result.get('task')}"
    cas = result.get("cassette") or {}
    scope = result.get("scope") or {}
    scoped = scope and (scope.get("from", 0) > 0 or scope.get("until", scope.get("total", 1) - 1) < scope.get("total", 1) - 1)
    where = (f" steps {scope['from']}–{scope['until']}" + (f" (sub-agent {scope['span']})" if scope.get("span") else "")
             + f" of {scope['total']}") if scoped else ""
    tail = ""
    ms = result.get("milestones")
    if ms and ms.get("recorded", {}).get("measurable"):
        if ms.get("lost"):
            tail += f"; milestones lost in the replay: {', '.join(ms['lost'])}"
        if ms.get("gained"):
            tail += f"; milestones gained: {', '.join(ms['gained'])}"
    if result["faithful"]:
        return (f"{who}: reproduced step for step{where} — {result['steps']['recorded'] if not scoped else scope['steps']} step(s), "
                f"{cas.get('hits', 0)} tool result(s) served from the recording, outcome unchanged{tail}.")
    parts = [f"{who}: not reproduced{where}"]
    node = next((r for r in result.get("drift_map") or [] if r.get("reproduced") is False and r.get("kind") in ("span", "episode")), None)
    if node and node.get("agent") and node.get("kind") == "span":
        parts.append(f"first inside sub-agent {node['agent']} (steps {node['from']}–{node['to']})")
    if cas.get("misses"):
        m = cas["misses"][0]
        parts.append(f"{len(cas['misses'])} cassette miss(es), the first {m['name']}({m['args']}) — a call the recording never made")
    diffs = result.get("differences") or []
    if diffs:
        d = diffs[0]
        parts.append(f"first difference at step {d['step']} in {d['field']}: recorded {str(d['recorded'])[:60]!r}, replayed {str(d['replayed'])[:60]!r}")
    if not result["outcome"]["same"]:
        parts.append(f"outcome recorded {result['outcome']['recorded']}, replayed {result['outcome']['replayed']}")
    return "; ".join(parts) + tail + "."


# --------------------------------------------------------------------------
# many traces, and the CI artifacts
# --------------------------------------------------------------------------

def rerun_paths(paths: list, **options: Any) -> dict:
    """Rerun every trace file; a summary with one result per trace (the
    replayed trace itself is kept only when ``options['keep_traces']``)."""
    keep = bool(options.pop("keep_traces", False))
    golden = options.pop("golden", None)
    results: list = []
    for path in paths:
        path = Path(path)
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
            task_id = str((trace.get("task") or {}).get("id") or "")
            golden_task = ((golden or {}).get("tasks") or {}).get(task_id) if golden else None
            result = rerun(trace, milestones=(golden_task or {}).get("milestones") if golden_task else None, **options)
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            result = {"faithful": False, "error": f"{exc.__class__.__name__}: {exc}", "trace_id": path.stem,
                      "agent": None, "task": None, "differences": [], "cassette": {}, "first_divergence": None,
                      "steps": {"recorded": 0, "replayed": 0}, "outcome": {"same": False},
                      "reading": f"{path.name}: could not be replayed — {exc.__class__.__name__}: {exc}"}
        result["path"] = str(path)
        if not keep:
            result.pop("trace", None)
        results.append(result)
    faithful = sum(1 for r in results if r.get("faithful"))
    return {"traces": len(results), "faithful": faithful, "drifted": len(results) - faithful,
            "mode": results[0]["mode"] if results and "mode" in results[0] else "self",
            "results": results}


def to_junit(summary: dict) -> str:
    """One testcase per trace in suite ``agentdiff.rerun``; a drifted trace
    is a failure whose message is the reading.  No timestamps, so two
    runs over one input produce identical bytes."""
    suite = ET.Element("testsuite", name="agentdiff.rerun", tests=str(summary["traces"]),
                       failures=str(summary["drifted"]), errors="0", skipped="0")
    for r in summary["results"]:
        case = ET.SubElement(suite, "testcase", classname="agentdiff.rerun", name=str(r.get("trace_id") or r.get("path")))
        if not r.get("faithful"):
            failure = ET.SubElement(case, "failure", message=str(r.get("reading") or "not reproduced")[:500])
            failure.text = json.dumps({k: r.get(k) for k in ("first_divergence", "differences", "cassette", "outcome", "error")},
                                      indent=1, ensure_ascii=False, default=str)
        out = ET.SubElement(case, "system-out")
        out.text = str(r.get("reading") or "")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suite, encoding="unicode") + "\n"


def to_markdown(summary: dict) -> str:
    lines = [f"## Replay check — {summary['faithful']}/{summary['traces']} trace(s) reproduced", ""]
    lines.append("| trace | steps | served | misses | first divergence | reading |")
    lines.append("|---|---|---|---|---|---|")
    for r in summary["results"]:
        cas = r.get("cassette") or {}
        steps = r.get("steps") or {}
        mark = "✓" if r.get("faithful") else "✗"
        lines.append(f"| {mark} `{r.get('trace_id') or r.get('path')}` | {steps.get('recorded', 0)} → {steps.get('replayed', 0)} "
                     f"| {cas.get('hits', 0)} | {len(cas.get('misses') or [])} | {r.get('first_divergence') if r.get('first_divergence') is not None else '—'} "
                     f"| {str(r.get('reading') or '').replace('|', '/')} |")
    drift = [(r, n) for r in summary["results"] for n in (r.get("drift_map") or []) if n.get("kind") == "span" and n.get("reproduced") is False]
    if drift:
        lines.append("")
        lines.append("Sub-agents that did not reproduce:")
        lines.append("")
        lines.append("| trace | sub-agent | steps | differed | misses | first |")
        lines.append("|---|---|---|---|---|---|")
        for r, n in drift:
            lines.append(f"| `{r.get('trace_id')}` | {n.get('agent')} | {n.get('from')}–{n.get('to')} | {n.get('differed')} | {n.get('misses')} | {n.get('first_difference')} |")
    lost = [(r, r["milestones"]) for r in summary["results"] if (r.get("milestones") or {}).get("lost")]
    if lost:
        lines.append("")
        lines.append("Milestones lost in the replay: " + "; ".join(f"`{r.get('trace_id')}`: {', '.join(m['lost'])}" for r, m in lost))
    lines.append("")
    lines.append("A reproduced trace is a fixture: every non-deterministic input it needed is in the recording. "
                 "A miss is a call the recording never made — with a different model, the exact step where it departed.")
    return "\n".join(lines) + "\n"


def to_annotations(summary: dict) -> str:
    out = []
    for r in summary["results"]:
        if r.get("faithful"):
            continue
        text = str(r.get("reading") or "").replace("%", "%25").replace("\n", "%0A")
        out.append(f"::error file={r.get('path', '')},title=replay drift::{text}")
    return "\n".join(out) + ("\n" if out else "")
