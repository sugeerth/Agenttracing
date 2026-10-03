"""A run on its clock: threads, laps, folds, and where to look first.

The page's Trace, Impact and Timescape views read a run as an execution.
This is the same reading as plain data, so a server can draw it
without a script and a terminal can print it:

**The clock.** A step's start is ``started_s`` when the trace recorded
it. Otherwise it is the end of the step before, and the ``basis`` says
"reconstructed": a run whose concurrency nothing wrote down is not one
that had none.

**Threads.** One lane per agent acting (the root, then each sub-agent in
the order it first acted, from ``span``). Inside an agent, steps that
overlap in time go to extra lanes, so a parallel fan-out shows as
parallel bars rather than as bars drawn on top of each other.

**Laps.** The rounds :mod:`agentdiff.laps` cuts the run into, as time
bands with how each closed.

**Folds.** A long run is mostly quiet. A stretch with nothing eventful
in it (no edit, no check, no error, no answer, no lap boundary, no
sub-agent starting) of at least :data:`FOLD_MIN` steps, or idle time
with no step at all, folds into a short segment, 6 + 6·log2(1 + its
seconds) wide, that says how much it holds. The open stretches share
what width is left in proportion to their seconds.

**Where to look first.** One step and one sentence, by rule, in this
order: the start of a block the run went round and never passed; the
last check that failed when no check after it passed; an error the run
never came back to; the answer of a run that never checked; for a run
that passed, the first check that passed. Every sentence is a count over
the recorded steps.

:func:`compare` lays two runs on one axis, by time or by step, and names
the first step where they did something different. :func:`ribbons`
compacts many runs into rows of cells for the view of everything at once.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional

from .laps import _activity, check_outcome, is_check, laps as lap_reading

__all__ = ["timeline", "compare", "ribbons", "look_here", "FOLD_MIN", "fold_width"]

#: quiet steps in a row before they fold
FOLD_MIN = 6
#: idle seconds with no step at all that fold on their own
IDLE_FOLD_S = 30.0
#: activities that are events: never folded away
EVENTFUL = ("edit", "verify", "delegate")


def fold_width(seconds: float) -> float:
    return 6 + 6 * math.log2(1 + max(0.0, seconds))


def _data(traj) -> dict:
    return traj.to_dict() if hasattr(traj, "to_dict") else traj


def _items(data: dict) -> List[dict]:
    """Every step with a start, an end, its agent and its activity."""
    steps = [s for s in data.get("steps") or [] if isinstance(s, dict)]
    recorded = sum(1 for s in steps if isinstance(s.get("started_s"), (int, float)))
    out, clock = [], 0.0
    for i, s in enumerate(steps):
        lat = float(s.get("latency_s") or 0.0)
        st = s.get("started_s")
        start = float(st) if isinstance(st, (int, float)) else clock
        span = s.get("span") if isinstance(s.get("span"), dict) else {}
        out.append({"index": i, "name": str(s.get("name") or s.get("type") or "step"), "type": s.get("type"),
                    "activity": _activity(s), "start": round(start, 3), "end": round(start + lat, 3),
                    "latency_s": lat, "tokens": int(s.get("tokens") or 0), "error": bool(s.get("error")),
                    "agent": str(span.get("agent") or "") or "root", "parent": span.get("parent"),
                    "check": check_outcome(s) if is_check(s) else None,
                    "reward": s.get("reward") if isinstance(s.get("reward"), (int, float)) else None})
        clock = max(clock, start + lat)
    basis = ("recorded" if steps and recorded == len([s for s in steps if s.get("type") != "answer"]) or
             (steps and recorded >= 0.8 * len(steps)) else "reconstructed" if not recorded else "partly recorded")
    # an answer the trace did not place sits where the run ended
    end = max([x["end"] for x in out] or [0.0])
    for x, s in zip(out, steps):
        if s.get("type") == "answer" and not isinstance(s.get("started_s"), (int, float)):
            x["start"] = x["end"] = round(end, 3)
    return out, basis


def _lanes(items: List[dict]) -> List[dict]:
    """One lane per agent, split where its steps overlap in time."""
    order: List[str] = []
    for x in items:
        if x["agent"] not in order:
            order.append(x["agent"])
    lanes: List[dict] = []
    for agent in order:
        mine: List[list] = []        # per sub-lane, the end of its last step
        for x in sorted((x for x in items if x["agent"] == agent), key=lambda x: (x["start"], x["index"])):
            for k, ends in enumerate(mine):
                if x["start"] >= ends[-1] - 1e-6:
                    ends.append(x["end"])
                    x["lane"] = f"{agent}#{k}"
                    break
            else:
                mine.append([x["end"]])
                x["lane"] = f"{agent}#{len(mine) - 1}"
        for k in range(len(mine)):
            parent = next((x["parent"] for x in items if x["agent"] == agent and x["parent"]), None)
            lanes.append({"id": f"{agent}#{k}", "agent": agent, "parallel": k,
                          "label": agent if k == 0 else f"{agent} ∥{k + 1}",
                          "depth": 0 if agent == "root" else 1 + (1 if parent and parent != "root" else 0),
                          "steps": sum(1 for x in items if x.get("lane") == f"{agent}#{k}")})
    return lanes


def _folds(items: List[dict], lap_ends: set, span: float) -> List[dict]:
    """The time segments, each open or folded."""
    if not items:
        return []
    evs = sorted(items, key=lambda x: (x["start"], x["index"]))
    firsts: Dict[str, int] = {}
    for x in evs:
        firsts.setdefault(x["agent"], x["index"])

    def eventful(x) -> bool:
        return (x["activity"] in EVENTFUL or x["error"] or x["type"] == "answer" or x["index"] in lap_ends
                or firsts.get(x["agent"]) == x["index"])
    segs: List[dict] = []
    state = {"t": 0.0}
    quiet: List[dict] = []

    def cut(kind: str, a: float, b: float, **extra) -> None:
        if a > state["t"]:
            segs.append({"kind": "open", "from": state["t"], "to": a})
        segs.append({"kind": kind, "from": a, "to": b, **extra})
        state["t"] = b

    def flush() -> None:
        if len(quiet) >= FOLD_MIN:
            a, b = quiet[0]["start"], max(q["end"] for q in quiet)
            if b > a:
                cut("fold", a, b, steps=len(quiet), first=quiet[0]["index"], last=quiet[-1]["index"])
        quiet.clear()

    last_end = 0.0
    for x in evs:
        if x["start"] - last_end >= IDLE_FOLD_S:
            flush()
            # time with no step at all: folded on its own
            cut("fold", max(last_end, state["t"]), x["start"], steps=0, idle=True)
        if eventful(x):
            flush()
        else:
            quiet.append(x)
        last_end = max(last_end, x["end"])
    flush()
    if state["t"] < span:
        segs.append({"kind": "open", "from": state["t"], "to": span})
    return [s for s in segs if s["to"] > s["from"]]


def look_here(data: dict, items: List[dict], lap: dict) -> Optional[dict]:
    steps = data.get("steps") or []
    if not steps:
        return None
    success = (data.get("outcome") or {}).get("success")
    in_progress = bool(data.get("in_progress"))
    rounds = lap.get("laps") or []
    passed_at = lap.get("first_pass_lap")
    if in_progress:
        last = items[-1]
        return {"index": last["index"], "kind": "now", "lap": len(rounds),
                "sentence": f"Running: step {last['index']} ({last['name']}) is the newest; judged when it ends."}
    if success is not True:
        stuck = (lap.get("stuck") or {}).get("longest_repeated_block") or {}
        # the longest stretch of laps that each repeated the one before
        best, cur = None, None
        for r in rounds:
            if r.get("same_as_previous"):
                cur = [cur[0], cur[1] + 1] if cur else [r["n"] - 1, 2]
                if best is None or cur[1] > best[1]:
                    best = list(cur)
            else:
                cur = None
        # a long stretch is where a failed run lost its time, even when a check passed somewhere else
        if best and (best[1] >= 4 or not passed_at):
            prev = rounds[best[0] - 1]
            i = prev["steps"][0]["index"]
            tail = " and never passed" if not passed_at else ", and the run still failed"
            return {"index": i, "kind": "loop", "lap": prev["n"],
                    "sentence": f"Lap {prev['n']} at step {i}: from here the run did the same lap {best[1]} time(s) "
                                f"in a row{tail}" + (f" (a block of {stuck['period']} step(s))"
                                                    if stuck.get("period") and not passed_at else "") + "."}
        failed_checks = [x for x in items if x["check"] is False]
        last_pass = max((y["index"] for y in items if y["check"] is True), default=-1)
        last_fail = next((x for x in reversed(failed_checks) if not last_pass > x["index"]), None)
        if last_fail:
            lapn = next((r["n"] for r in rounds if any(s["index"] == last_fail["index"] for s in r["steps"])), None)
            return {"index": last_fail["index"], "kind": "check", "lap": lapn,
                    "sentence": f"Step {last_fail['index']} ({last_fail['name']}): the last check failed and no check "
                                f"after it passed; {len(failed_checks)} of "
                                f"{sum(1 for x in items if x['check'] is not None)} check(s) failed."}
        errs = [x for x in items if x["error"]]
        last_ok: Dict[str, int] = {}
        for y in items:
            if not y["error"]:
                last_ok[y["name"]] = max(last_ok.get(y["name"], -1), y["index"])
        for x in errs:
            later_ok = last_ok.get(x["name"], -1) > x["index"]
            if not later_ok:
                return {"index": x["index"], "kind": "error", "lap": None,
                        "sentence": f"Step {x['index']} ({x['name']}): an error the run never came back to "
                                    f"(no later {x['name']} succeeded)."}
        if success is False and not any(x["check"] is not None for x in items):
            ans = next((x for x in reversed(items) if x["type"] == "answer"), items[-1])
            return {"index": ans["index"], "kind": "unchecked", "lap": len(rounds),
                    "sentence": f"Step {ans['index']}: it answered without any step running a test suite, lint or "
                                f"check, and the grader failed it."}
        if success is False:
            ans = items[-1]
            return {"index": ans["index"], "kind": "end", "lap": len(rounds),
                    "sentence": f"Every check it ran passed or said nothing, yet the grader failed it: the gap is "
                                f"between its checks and the grader's (see the last step, {ans['index']})."}
        return None
    first_ok = next((x for x in items if x["check"] is True), None)
    if first_ok:
        return {"index": first_ok["index"], "kind": "pass", "lap": passed_at,
                "sentence": f"Step {first_ok['index']} ({first_ok['name']}): its first passing check, on lap "
                            f"{passed_at} of {len(rounds)}."}
    return {"index": items[-1]["index"], "kind": "pass", "lap": len(rounds),
            "sentence": "It passed the grader without a check of its own on the record."}


def timeline(traj) -> dict:
    data = _data(traj)
    items, basis = _items(data)
    lap = lap_reading(data)
    lap_of: Dict[int, int] = {}
    lap_bands = []
    by_index = {x["index"]: x for x in items}
    for r in lap.get("laps") or []:
        idx = [s["index"] for s in r["steps"]]
        for i in idx:
            lap_of[i] = r["n"]
        ts = [by_index[i] for i in idx if i in by_index]
        if ts:
            closed = r.get("closed_by") or {}
            lap_bands.append({"n": r["n"], "from": min(x["start"] for x in ts), "to": max(x["end"] for x in ts),
                              "passed": closed.get("passed") if closed else None,
                              "repeat": bool(r.get("same_as_previous")), "steps": len(idx)})
    for x in items:
        x["lap"] = lap_of.get(x["index"])
    span = max([x["end"] for x in items] or [0.0])
    total = (data.get("totals") or {}).get("latency_s")
    if isinstance(total, (int, float)) and total > span and basis != "reconstructed":
        span = float(total)
    lanes = _lanes(items)
    lap_ends = {r["steps"][-1]["index"] for r in lap.get("laps") or [] if r.get("steps")}
    segs = _folds(items, lap_ends, span)
    cum, tok = [], 0
    for x in sorted(items, key=lambda x: x["end"]):
        tok += x["tokens"]
        cum.append([x["end"], tok])
    here = look_here(data, items, lap)
    running = bool(data.get("in_progress"))
    return {"task": (data.get("task") or {}).get("id"), "agent": (data.get("agent") or {}).get("name"),
            # a run still going has no outcome yet, whatever its half-written frame says
            "success": None if running else (data.get("outcome") or {}).get("success"), "in_progress": running,
            "basis": basis, "span_s": round(span, 3), "steps": items, "lanes": lanes, "laps": lap_bands,
            "segments": segs, "tokens": cum, "look_here": here, "lap_summary": lap.get("summary"),
            "folded_steps": sum(s.get("steps", 0) for s in segs if s["kind"] == "fold")}


def compare(a, b) -> dict:
    """Two runs on one axis, and the first step where they did different things."""
    ta, tb = timeline(a), timeline(b)
    da, db = _data(a), _data(b)
    sa = [(str(s.get("name")), " ".join(str(s.get("input") or "").split())[:200]) for s in da.get("steps") or []]
    sb = [(str(s.get("name")), " ".join(str(s.get("input") or "").split())[:200]) for s in db.get("steps") or []]
    first = next((i for i, (x, y) in enumerate(zip(sa, sb)) if x != y), None)
    if first is None and len(sa) != len(sb):
        first = min(len(sa), len(sb))
    shared = first if first is not None else len(sa)
    if first is None:
        sentence = f"The two runs made the same {len(sa)} call(s) in the same order."
    else:
        def what(seq):
            if first >= len(seq):
                return "nothing (it had ended)"
            name, call = seq[first]
            return f"{name} `{call[:60]}`" if call else name
        what_a, what_b = what(sa), what(sb)
        sentence = (f"Step {first}: the first step where they differ; the {shared} step(s) before it were the same "
                    f"calls. A did {what_a}, B did {what_b}.")
    return {"a": ta, "b": tb, "diverged_at": first, "same_prefix": shared, "sentence": sentence}


def ribbons(trajs: Iterable, *, cells: int = 60) -> List[dict]:
    """Many runs as rows: each run's steps compacted to at most ``cells`` cells
    on its own clock, with its laps, its outcome and where to look first."""
    rows = []
    for traj in trajs:
        t = timeline(traj)
        items = sorted(t["steps"], key=lambda x: (x["start"], x["index"]))
        n = len(items)
        if not n:
            continue
        per = max(1, math.ceil(n / cells))
        packed = []
        for k in range(0, n, per):
            chunk = items[k:k + per]
            acts: Dict[str, int] = {}
            for x in chunk:
                acts[x["activity"]] = acts.get(x["activity"], 0) + 1
            top = max(acts.items(), key=lambda kv: (kv[1], kv[0] != "think"))[0]
            packed.append({"from": chunk[0]["start"], "to": max(x["end"] for x in chunk), "activity": top,
                           "first": chunk[0]["index"], "last": chunk[-1]["index"],
                           "error": any(x["error"] for x in chunk),
                           "check": (False if any(x["check"] is False for x in chunk) else
                                     True if any(x["check"] is True for x in chunk) else None)})
        rows.append({"task": t["task"], "agent": t["agent"], "success": t["success"], "in_progress": t["in_progress"],
                     "span_s": t["span_s"], "steps": n, "cells": packed, "per_cell": per,
                     "laps": [{"n": b["n"], "at": b["to"], "passed": b["passed"], "repeat": b["repeat"]}
                              for b in t["laps"]],
                     "look_here": t["look_here"], "basis": t["basis"], "lanes": len(t["lanes"])})
    return rows


#: the phase a step's activity belongs to, as the report page names them
PHASES = {"plan": "frame", "research": "acquire", "explore": "acquire", "think": "reason", "edit": "change",
          "run": "change", "verify": "verify", "delegate": "delegate", "other": "act"}


def align(a, b) -> dict:
    """Two runs' steps matched in order (a longest common run of the same calls).
    Rows: ``match`` (the same call), ``drift`` (a step in the same place, a
    different call), ``a_only``/``b_only``. The first row that is not a match
    is the divergence."""
    import difflib
    da, db = _data(a), _data(b)

    def sig(s):
        # a step is its call; a model turn with no call is what it said, so two silences never match
        body = s.get("input") or (s.get("output") if s.get("type") in ("reason", "answer", "plan") else "") or ""
        return (str(s.get("name") or s.get("type")), " ".join(str(body).split())[:160] or f"#{id(s)}")
    sa = [sig(s) for s in da.get("steps") or [] if isinstance(s, dict)]
    sb = [sig(s) for s in db.get("steps") or [] if isinstance(s, dict)]
    rows = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
        if op == "equal":
            rows += [{"op": "match", "a": i, "b": j} for i, j in zip(range(i1, i2), range(j1, j2))]
        elif op == "replace":
            n = min(i2 - i1, j2 - j1)
            rows += [{"op": "drift", "a": i1 + k, "b": j1 + k, "same_tool": sa[i1 + k][0] == sb[j1 + k][0]}
                     for k in range(n)]
            rows += [{"op": "a_only", "a": i, "b": None} for i in range(i1 + n, i2)]
            rows += [{"op": "b_only", "a": None, "b": j} for j in range(j1 + n, j2)]
        elif op == "delete":
            rows += [{"op": "a_only", "a": i, "b": None} for i in range(i1, i2)]
        else:
            rows += [{"op": "b_only", "a": None, "b": j} for j in range(j1, j2)]
    first = next((k for k, r in enumerate(rows) if r["op"] != "match"), None)
    matched = sum(1 for r in rows if r["op"] == "match")
    return {"rows": rows, "divergence": first, "matched": matched, "a_steps": len(sa), "b_steps": len(sb),
            "sentence": (f"{matched} of their steps are the same call in the same order"
                         + (f"; they part at row {first}" if first is not None else "; they never part") + ".")}
