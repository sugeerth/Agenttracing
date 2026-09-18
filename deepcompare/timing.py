"""Where the time went — and why a run took as long as it did.

A run's wall-clock is the sum of what its steps took. This module reads
those latencies as recorded and attributes them: to thinking (plan and
reason turns), to waiting on tools (each tool named, with its calls and
seconds), and to the answer; and, within those, to steps the reading
marked as wasted — a call that returned nothing new, a repeat, a dead
end, an error, or a step taken after every value the answer needed was
already in hand. The result is a per-step ledger, the shares, the
slowest steps, and a rationale in sentences whose every number is in
the ledger. A run with no recorded latencies is *unmeasurable*, never
fast.
"""

from __future__ import annotations

from functools import partial
from typing import Optional

from . import sections as _sections
from ._text import secs
from .section import unmeasurable
from .trace import Trajectory

TOOLISH = ("tool_call", "search", "retrieve", "read")
WASTE_LABEL = {
    "no_information": "returned nothing new",
    "repeat": "repeated an earlier call",
    "dead_end": "reached a dead end (nothing it returned reached the answer)",
    "error": "hit an error",
    "after_basis": "came after every value the answer needed was in hand",
}


#: tenths kept at any size — a gap of 12.3s is read to the tenth — hundredths under a second
_secs = partial(secs, whole_above=None)


def timeline(traj: Trajectory) -> dict:
    """Is this run's clock *read* or *assumed*?

    A trace that records only ``latency_s`` leaves one way to place a step
    on a clock: sum the durations before it.  That reconstruction is not a
    neutral convenience — it asserts the run was sequential, and for a loop
    that executes independent calls concurrently it is simply wrong, with
    nothing in the record to say so.  Every timeline this repository draws
    made that assumption silently until ``Step.started_s`` existed.

    So the basis is stated, and when the starts *are* recorded two things
    become measurable that were not:

    ``span_s``    first start to last end — the run's real wall-clock
    ``overlap_s`` the sum of the durations minus the span, when positive:
                  seconds two or more steps were running at once

    ``overlap_s`` is ``0.0`` on a sequential run and ``None`` when the
    starts are not recorded — not zero, because a run whose concurrency
    nothing wrote down is not a run that had none.

    One caveat, and it is not a small one: this is read from the trace's
    own numbers, so it measures what the trace *says* rather than what the
    world did.  A recorder that stamps a real start beside a duration it
    took from somewhere else — a provider reporting a latency longer than
    the call took, a fixture declaring one it did not wait for — writes
    steps whose spans overlap, and this reads that as concurrency.  The
    reading is correct about the trace; the trace is wrong.  Nothing here
    can tell the two apart, which is the same reason `basis` is stated at
    all: a number is only as good as the record under it.
    """
    steps = traj.steps
    if not steps:
        return {"measurable": False, "reason": "the run has no steps", "basis": None,
                "span_s": None, "sum_s": None, "overlap_s": None, "concurrent_steps": None,
                "recorded_starts": 0, "steps": 0, "reading": "No step to place on a clock."}
    starts = [s.started_s for s in steps]
    got = [v for v in starts if isinstance(v, (int, float))]
    lats = [float(s.latency_s) if isinstance(s.latency_s, (int, float)) and s.latency_s > 0 else 0.0 for s in steps]
    total = round(sum(lats), 4)
    if len(got) != len(steps):
        # all or nothing: mixing a recorded start with a reconstructed one
        # would put some steps on a clock and some on an assumption, and the
        # picture would not say which was which
        return {
            "measurable": False,
            "reason": ("no step records when it began" if not got else
                       f"only {len(got)} of {len(steps)} steps record when they began"),
            "basis": "reconstructed", "span_s": None, "sum_s": total, "overlap_s": None,
            "concurrent_steps": None, "recorded_starts": len(got), "steps": len(steps),
            "reading": ("This run's clock is reconstructed: each step is placed by summing the durations "
                        "before it, which reads the run as strictly sequential. Nothing here says it was. "
                        "One field per step — when it began — turns that assumption into a measurement."),
        }
    ends = [float(s) + lat for s, lat in zip(got, lats)]
    span = round(max(ends) - min(got), 4)
    overlap = round(max(0.0, total - span), 4)
    # a step that began before an earlier one ended
    order = sorted(zip(got, ends))
    concurrent, reach = 0, None
    for begin, end in order:
        if reach is not None and begin < reach - 1e-9:
            concurrent += 1
        reach = end if reach is None else max(reach, end)
    reading = (f"This run's clock is recorded: every step says when it began, so the timeline is read rather "
               f"than assumed. {_secs(total)} of step time over a {_secs(span)} run")
    reading += ("; nothing overlapped, so the run really was sequential." if not overlap else
                f", so {_secs(overlap)} had two or more steps running at once "
                f"({concurrent} of {len(steps)} began before an earlier one ended).")
    return {"measurable": True, "reason": None, "basis": "recorded", "span_s": span, "sum_s": total,
            "overlap_s": overlap, "concurrent_steps": concurrent, "recorded_starts": len(got),
            "steps": len(steps), "reading": reading}


def time_attribution(traj: Trajectory, reading: Optional[dict] = None) -> dict:
    steps = traj.steps
    lats = [s.latency_s if isinstance(s.latency_s, (int, float)) and s.latency_s >= 0 else None for s in steps]
    measured = [v for v in lats if v]
    if not measured:
        return unmeasurable("no step recorded a latency — unmeasurable, not fast",
                            timeline=timeline(traj),
                            total_s=float(traj.totals.latency_s or 0.0), steps=[], by_category={}, by_tool={},
                            wasted_s=0.0, wasted_share=None, slowest=[], rationale=f"{traj.agent.name}: no step latency was recorded, so where its time went cannot be attributed.")
    total = sum(measured)
    reading = reading or {}
    roles = {}
    for w in reading.get("what_happened") or []:
        if isinstance(w, dict) and isinstance(w.get("step"), int):
            roles[w["step"]] = w.get("role")
    spent = set((reading.get("answer_basis") or {}).get("spent_steps") or [])
    rows = []
    by_cat = {"think": 0.0, "tool": 0.0, "answer": 0.0}
    by_tool: dict = {}
    wasted = 0.0
    last_tool: dict = {}
    for s, lat in zip(steps, lats):
        lat = lat or 0.0
        cat = "answer" if s.type == "answer" else "tool" if s.type in TOOLISH else "think"
        by_cat[cat] += lat
        role = roles.get(s.index)
        reason = None
        if s.type != "answer":
            if s.error or role == "error":
                reason = "error"
            elif role == "no_information":
                reason = "no_information"
            elif role == "repeat":
                reason = "repeat"
            elif role == "dead_end":
                reason = "dead_end"
            elif s.index in spent:
                reason = "after_basis"
        retry = None
        if cat == "tool":
            prev = last_tool.get(s.name)
            if prev and prev["error"]:
                retry = prev["index"]
            last_tool[s.name] = {"index": s.index, "error": bool(s.error) or role == "error"}
            t = by_tool.setdefault(s.name or "?", {"calls": 0, "seconds": 0.0, "wasted_calls": 0, "wasted_seconds": 0.0})
            t["calls"] += 1
            t["seconds"] += lat
            if reason:
                t["wasted_calls"] += 1
                t["wasted_seconds"] += lat
        if reason:
            wasted += lat
        rows.append({"index": s.index, "type": s.type, "name": s.name, "latency_s": round(lat, 4), "share": round(lat / total, 4) if total else 0.0,
                     "category": cat, "role": role, "wasted": reason, "wasted_label": WASTE_LABEL.get(reason) if reason else None,
                     "retry_of": retry, "tokens": s.tokens})
    for t in by_tool.values():
        t["seconds"] = round(t["seconds"], 4)
        t["wasted_seconds"] = round(t["wasted_seconds"], 4)
        t["share"] = round(t["seconds"] / total, 4) if total else 0.0
    slowest = sorted(rows, key=lambda r: -r["latency_s"])[:3]
    tokens = sum(s.tokens for s in steps if isinstance(s.tokens, (int, float)))
    # ---- the rationale, every number from above
    name = traj.agent.name
    parts = [f"{name} took {_secs(total)} over {len(steps)} step(s)"]
    if by_cat["tool"]:
        tool_bits = sorted(by_tool.items(), key=lambda kv: -kv[1]["seconds"])[:3]
        parts.append(f"{by_cat['tool'] / total:.0%} of it waiting on tools (" + ", ".join(
            f"{k} ×{v['calls']} = {_secs(v['seconds'])}" + (f", {v['wasted_calls']} of them wasted" if v["wasted_calls"] else "") for k, v in tool_bits) + ")")
    if by_cat["think"]:
        parts.append(f"{by_cat['think'] / total:.0%} thinking")
    if by_cat["answer"]:
        parts.append(f"{by_cat['answer'] / total:.0%} on the answer")
    sentence = "; ".join(parts) + "."
    if wasted:
        kinds: dict = {}
        for r in rows:
            if r["wasted"]:
                kinds[r["wasted"]] = kinds.get(r["wasted"], 0) + 1
        sentence += (f" {_secs(wasted)} ({wasted / total:.0%}) went to {sum(kinds.values())} step(s) the reading marks as wasted: "
                     + ", ".join(f"{n} {WASTE_LABEL[k]}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])) + ".")
    else:
        sentence += " No step is marked as wasted."
    top = slowest[0] if slowest else None
    if top and top["share"] >= 0.3:
        sentence += f" The slowest step was {top['index']} ({top['name'] or top['type']}, {_secs(top['latency_s'])}, {top['share']:.0%} of the run)."
    return {
        "measurable": True, "total_s": round(total, 4), "steps": rows,
        "by_category": {k: {"seconds": round(v, 4), "share": round(v / total, 4) if total else 0.0} for k, v in by_cat.items()},
        "by_tool": by_tool, "wasted_s": round(wasted, 4), "wasted_share": round(wasted / total, 4) if total else 0.0,
        "slowest": [{"index": r["index"], "name": r["name"], "type": r["type"], "latency_s": r["latency_s"], "share": r["share"], "wasted": r["wasted"]} for r in slowest],
        "tokens_per_s": round(tokens / total, 2) if total and tokens else None,
        "rationale": sentence,
        "timeline": timeline(traj),
        "basis": "step latencies as recorded; wasted = the reading's roles (no information, repeat, dead end, error) and steps after the answer's basis was complete",
    }


def compare_timing(a: Trajectory, b: Trajectory, reading: Optional[dict] = None) -> dict:
    reading = reading or {}
    ta = time_attribution(a, reading.get("a"))
    tb = time_attribution(b, reading.get("b"))
    out = {"a": ta, "b": tb, "delta": None, "narrative": ""}
    if ta["measurable"] and tb["measurable"]:
        slower, faster = (a, b) if ta["total_s"] >= tb["total_s"] else (b, a)
        ts, tf = (ta, tb) if ta["total_s"] >= tb["total_s"] else (tb, ta)
        out["delta"] = {"total_s": round(ta["total_s"] - tb["total_s"], 4), "wasted_s": round(ta["wasted_s"] - tb["wasted_s"], 4),
                        "tool_s": round(ta["by_category"]["tool"]["seconds"] - tb["by_category"]["tool"]["seconds"], 4)}
        gap = ts["total_s"] - tf["total_s"]
        if gap < 1e-9:
            out["narrative"] = "Both runs took the same time."
        else:
            explained = min(gap, max(0.0, ts["wasted_s"] - tf["wasted_s"]))
            out["narrative"] = (f"{slower.agent.name} took {_secs(gap)} longer than {faster.agent.name}"
                                + (f"; {explained / gap:.0%} of that gap is steps the reading marks as wasted" if explained > 0 else "; none of that gap is in steps marked as wasted — it is slower per productive step")
                                + f" ({_secs(ts['by_category']['tool']['seconds'])} against {_secs(tf['by_category']['tool']['seconds'])} waiting on tools).")
    else:
        out["narrative"] = "Time cannot be compared: at least one run recorded no step latencies."
    return out


__all__ = ["time_attribution", "compare_timing", "WASTE_LABEL"]


@_sections.register("pair", "timing", requires=("reading",))
def _section(report: dict, ctx: "_sections.PairContext"):
    return compare_timing(ctx.a, ctx.b, report["reading"])
