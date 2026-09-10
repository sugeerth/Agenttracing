"""Milestones: progress through a long task, measured before the answer.

A long-horizon run is not pass or fail until its last step, and a run
that fails after four hours tells nothing about the three hours that
went well.  A golden task may therefore name **milestones** — the
states a correct solution passes through, each recognised by evidence
in a step's text — and every run is read against them: which
milestones it reached, at which step and second, in which sub-agent,
whether in time, and where it stalled.

A golden task's ``milestones`` is a list of::

    {"id": "commit_found", "label": "the breaking commit is identified",
     "evidence": ["9f3c2e1"], "in": "output", "by_step": 12, "by_seconds": 20}

``evidence`` is a list of strings, any of which (case-insensitive)
marks the milestone reached; ``in`` is ``output`` (the world's answer
or the agent's conclusion; the default), ``input`` (what the agent
asked or wrote) or ``any``; ``by_step`` / ``by_seconds`` are optional
deadlines a run is measured against.  Milestones are ordered: the order
they are listed is the order a solution should pass them, and a run
that reaches them out of order is reported as such, not scored down.

Everything here is a count or a first index over the steps; no model
is consulted and nothing is estimated.
"""

from __future__ import annotations

from typing import Any, Optional


def _text(step: dict, where: str) -> str:
    if where == "input":
        return str(step.get("input") or "")
    if where == "any":
        return str(step.get("input") or "") + "\n" + str(step.get("output") or "")
    return str(step.get("output") or "")


def _steps_of(run: Any) -> list:
    """Steps as dicts from a trace dict or a Trajectory."""
    if isinstance(run, dict):
        return list(run.get("steps") or [])
    out = []
    for st in getattr(run, "steps", []) or []:
        out.append({"index": st.index, "type": st.type, "name": st.name, "input": st.input, "output": st.output,
                    "latency_s": st.latency_s, "span": st.span})
    return out


def evaluate(run: Any, milestones: Optional[list]) -> dict:
    """Read one run against its task's milestones.

    Returns ``{"measurable", "total", "reached", "progress", "milestones":
    [...], "in_order", "last_reached_step", "steps_after_last",
    "seconds_after_last", "narrative"}``; each milestone carries
    ``reached``, ``step``, ``seconds`` (cumulative latency at that step),
    ``agent`` (the span acting), ``on_time`` (``None`` without a deadline)
    and ``evidence_hit``.
    """
    milestones = [m for m in (milestones or []) if isinstance(m, dict) and m.get("evidence")]
    steps = _steps_of(run)
    if not milestones:
        return {"measurable": False, "total": 0, "reached": 0, "progress": None, "milestones": [], "in_order": True,
                "last_reached_step": None, "steps_after_last": None, "seconds_after_last": None,
                "narrative": "The task names no milestones, so progress before the answer is not measured."}
    clock = 0.0
    clocks: list = []
    for s in steps:
        lat = s.get("latency_s")
        clock += float(lat) if isinstance(lat, (int, float)) else 0.0
        clocks.append(clock)
    total_seconds = clocks[-1] if clocks else 0.0
    out: list = []
    for m in milestones:
        where = str(m.get("in") or "output")
        needles = [str(e).lower() for e in (m.get("evidence") or []) if str(e)]
        hit = None
        for i, s in enumerate(steps):
            text = _text(s, where).lower()
            found = next((n for n in needles if n in text), None)
            if found is not None:
                hit = (i, found)
                break
        entry = {"id": str(m.get("id") or m.get("label") or f"m{len(out) + 1}"), "label": str(m.get("label") or m.get("id") or ""),
                 "reached": hit is not None, "step": None, "seconds": None, "agent": None, "on_time": None, "evidence_hit": None,
                 "by_step": m.get("by_step"), "by_seconds": m.get("by_seconds")}
        if hit is not None:
            i, found = hit
            entry.update({"step": steps[i].get("index", i), "seconds": round(clocks[i], 4), "evidence_hit": found,
                          "agent": (steps[i].get("span") or {}).get("agent") if isinstance(steps[i].get("span"), dict) else None})
            deadline_ok: Optional[bool] = None
            if isinstance(m.get("by_step"), int):
                deadline_ok = i <= m["by_step"]
            if isinstance(m.get("by_seconds"), (int, float)):
                by_s = clocks[i] <= float(m["by_seconds"])
                deadline_ok = by_s if deadline_ok is None else (deadline_ok and by_s)
            entry["on_time"] = deadline_ok
        out.append(entry)
    reached = [e for e in out if e["reached"]]
    order_steps = [e["step"] for e in reached]
    in_order = order_steps == sorted(order_steps)
    last_step = max(order_steps) if order_steps else None
    steps_after = (len(steps) - 1 - last_step) if last_step is not None else len(steps)
    seconds_after = round(total_seconds - (clocks[last_step] if last_step is not None and last_step < len(clocks) else 0.0), 4)
    result = {"measurable": True, "total": len(out), "reached": len(reached),
              "progress": round(len(reached) / len(out), 4) if out else None, "milestones": out,
              "in_order": in_order, "last_reached_step": last_step, "steps_after_last": steps_after,
              "seconds_after_last": seconds_after, "total_steps": len(steps), "total_seconds": round(total_seconds, 4)}
    result["narrative"] = _narrative(run, result)
    return result


def _fmt_s(v: float) -> str:
    return f"{v:.0f}s" if v >= 10 else f"{v:.1f}s"


def _name(run: Any) -> str:
    if isinstance(run, dict):
        return str(((run.get("agent") or {}).get("name")) or "the run")
    agent = getattr(run, "agent", None)
    return str(getattr(agent, "name", None) or "the run")


def _narrative(run: Any, r: dict) -> str:
    name = _name(run)
    if not r["total"]:
        return f"{name}: no milestones to measure."
    parts = [f"{name} reached {r['reached']} of {r['total']} milestone(s)"]
    if r["reached"]:
        last = max((m for m in r["milestones"] if m["reached"]), key=lambda m: m["step"])
        parts[0] += f", the last ({last['label'] or last['id']}) at step {last['step']}" + (f" after {_fmt_s(last['seconds'])}" if last["seconds"] else "")
        if last.get("agent"):
            parts[0] += f" inside {last['agent']}"
    missing = [m for m in r["milestones"] if not m["reached"]]
    if missing:
        parts.append("never reached: " + ", ".join(m["label"] or m["id"] for m in missing))
    late = [m for m in r["milestones"] if m["reached"] and m["on_time"] is False]
    if late:
        parts.append("late: " + ", ".join(f"{m['label'] or m['id']} (step {m['step']}" + (f", {_fmt_s(m['seconds'])}" if m["seconds"] else "") + ")" for m in late))
    if not r["in_order"]:
        parts.append("reached out of the listed order")
    if r["reached"] and r["steps_after_last"] and r["steps_after_last"] >= 3 and r["reached"] < r["total"]:
        parts.append(f"{r['steps_after_last']} step(s)" + (f" and {_fmt_s(r['seconds_after_last'])}" if r["seconds_after_last"] else "") + " after the last milestone with no further progress")
    return "; ".join(parts) + "."


def compare(ma: dict, mb: dict, names=("a", "b")) -> dict:
    """Two runs against the same milestones: per milestone, who reached
    it, who first, and the gap in steps and seconds; the run further
    along; a narrative."""
    if not (ma.get("measurable") and mb.get("measurable")):
        return {"measurable": False, "rows": [], "further": None, "narrative": "Milestones were not measured for both runs."}
    by_a = {m["id"]: m for m in ma["milestones"]}
    by_b = {m["id"]: m for m in mb["milestones"]}
    rows: list = []
    for mid, a in by_a.items():
        b = by_b.get(mid) or {"reached": False, "step": None, "seconds": None}
        first = None
        gap_steps = gap_seconds = None
        if a["reached"] and b["reached"]:
            first = "a" if a["step"] < b["step"] else "b" if b["step"] < a["step"] else "tie"
            gap_steps = a["step"] - b["step"]
            if a["seconds"] is not None and b["seconds"] is not None:
                gap_seconds = round(a["seconds"] - b["seconds"], 4)
        elif a["reached"] or b["reached"]:
            first = "a" if a["reached"] else "b"
        rows.append({"id": mid, "label": a.get("label") or mid, "a": {"reached": a["reached"], "step": a["step"], "seconds": a["seconds"], "on_time": a.get("on_time")},
                     "b": {"reached": b["reached"], "step": b["step"], "seconds": b["seconds"], "on_time": b.get("on_time")},
                     "first": first, "gap_steps": gap_steps, "gap_seconds": gap_seconds})
    further = "a" if ma["reached"] > mb["reached"] else "b" if mb["reached"] > ma["reached"] else None
    na, nb = names
    parts = []
    if further:
        parts.append(f"{na if further == 'a' else nb} got further: {max(ma['reached'], mb['reached'])} of {ma['total']} milestones against {min(ma['reached'], mb['reached'])}")
    else:
        parts.append(f"both reached {ma['reached']} of {ma['total']} milestones")
    diverge = next((r for r in rows if r["a"]["reached"] != r["b"]["reached"]), None)
    if diverge:
        who = na if diverge["a"]["reached"] else nb
        parts.append(f"the first milestone only one run reached is {diverge['label']} ({who}, step {diverge['a']['step'] if diverge['a']['reached'] else diverge['b']['step']})")
    slow = [r for r in rows if r["gap_seconds"] is not None and abs(r["gap_seconds"]) >= 1.0]
    if slow:
        worst = max(slow, key=lambda r: abs(r["gap_seconds"]))
        later = na if worst["gap_seconds"] > 0 else nb
        parts.append(f"the widest gap is at {worst['label']}: {later} arrived {_fmt_s(abs(worst['gap_seconds']))} later")
    return {"measurable": True, "rows": rows, "further": further, "narrative": "; ".join(parts) + "."}


def lost_and_gained(before: dict, after: dict) -> dict:
    """The milestones one reading of a run reached and another did not —
    a recording against its replay, or a run against its rerun with a
    different model."""
    if not (before.get("measurable") and after.get("measurable")):
        return {"lost": [], "gained": [], "same": True}
    b = {m["id"] for m in before["milestones"] if m["reached"]}
    a = {m["id"] for m in after["milestones"] if m["reached"]}
    return {"lost": sorted(b - a), "gained": sorted(a - b), "same": a == b}
