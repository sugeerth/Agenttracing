"""Long-running agents: a run of hours or days, read at the scale it ran.

A run that lasts a night is not a long short run. Most of its wall time
is idle: waiting on a model, on CI, on a person. Its work comes in
bursts, and its loops last hours rather than steps. Three questions
matter, and each has a rule:

**Where did the time go?** The run is clustered by its own gaps:

- **Sessions** are split by idle of at least :data:`SESSION_GAP_S` (30
  minutes): the agent stopped, overnight, or waited on a person.
- **Bursts** inside a session are split by a gap the run itself sets.
  The threshold falls between the run's two kinds of gap, the short gap
  between calls and the long pause between bursts (Otsu's split of the
  log gaps). It is clamped to [:data:`BURST_GAP_MIN_S`,
  :data:`SESSION_GAP_S`]. When the gaps show no two kinds, it is ten
  times the median gap. With no recorded clock, nothing can be split by
  time, so a burst ends after each check, and the basis says so.

**When did it last move forward?** *Progress* is a check passing where
its previous run (the same command) did not pass, or passing for the
first time, or passing with more cases than it ever had (``4 passed`` then
``6 passed``). An edit is not progress, and neither is a call. A
*regression* is a check failing where its previous run passed. The
**stall** is the longest stretch between two progress events (or from
the start, or up to the end or now), measured as wall time, as the
time the agent was active, and in calls.

**Is it going round?** A burst *repeats* an earlier one when its calls
(tool and input) overlap that burst's by at least :data:`REPEAT_JACCARD`
(Jaccard) and it made no progress. Bursts repeating in a row are a
**loop over hours**: where it started, how long it lasted, the calls
they share, and the check that kept failing.

:func:`longrun` is one linear pass over the steps. The hub's long view
draws it (``agentdiff.hub.viz``), and ``agentdiff timeline TRACE
--long`` prints it. :func:`window` gives the calls inside one burst or
time range, for drawing every call.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Dict, List, Optional

from .timeline import _items

__all__ = ["longrun", "window", "is_long", "SESSION_GAP_S", "BURST_GAP_MIN_S", "REPEAT_JACCARD", "LONG_S",
           "LONG_STEPS", "check_key", "check_part"]

#: idle that splits two sessions
SESSION_GAP_S = 1800.0
#: the shortest pause that may split two bursts
BURST_GAP_MIN_S = 20.0
#: calls shared with an earlier burst (Jaccard) for a burst to repeat it
REPEAT_JACCARD = 0.6
#: how many bursts back a repeat is looked for
REPEAT_WINDOW = 40
#: a run at least this long, or with this many steps, is read as a long run
LONG_S = 3600.0
LONG_STEPS = 600
_NICE = (60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400)
_SHELL = ("Bash", "bash", "shell", "exec_command", "local_shell")
DAY = 86400.0


def _data(traj) -> dict:
    return traj.to_dict() if hasattr(traj, "to_dict") else traj


def is_long(traj_or_summary) -> bool:
    d = _data(traj_or_summary)
    if "steps" in d and isinstance(d["steps"], list):
        steps = d["steps"]
        span = float((d.get("totals") or {}).get("latency_s") or 0.0)
        last = steps[-1] if steps else {}
        if isinstance(last.get("started_s"), (int, float)):
            span = max(span, float(last["started_s"]) + float(last.get("latency_s") or 0))
        return span >= LONG_S or len(steps) >= LONG_STEPS
    return float(d.get("seconds") or d.get("elapsed_s") or 0) >= LONG_S or int(d.get("steps") or 0) >= LONG_STEPS


def _command(step: dict) -> str:
    raw = str(step.get("input") or "")
    if str(step.get("name") or "") in _SHELL and raw.startswith("{"):
        try:
            raw = str(json.loads(raw).get("command") or raw)
        except ValueError:
            pass
    return raw


def check_key(step: dict) -> str:
    """What a check checked, so its runs can be followed: the command, without
    the plumbing round it (``2>&1``, ``| tail``)."""
    return f"{step.get('name')}:{check_part(_command(step))[:120]}"


def check_part(cmd: str) -> str:
    """The part of a shell command that is the check: in ``cat > f <<EOF … EOF
    && python -m pytest -q | tail``, ``python -m pytest -q``."""
    from .duel import _VERIFY
    parts = [p.strip() for p in re.split(r"&&|\|\||;|\n", str(cmd or "")) if p.strip()]
    pick = next((p for p in parts if _VERIFY.search(p)), parts[0] if parts else "")
    pick = re.split(r"\s+\|\s*|\s+2>&1", pick, maxsplit=1)[0]
    return " ".join(pick.split())


def _sig(step: dict) -> str:
    return f"{step.get('name')}:{' '.join(_command(step).split())[:100]}"


def _passing(step: dict) -> Optional[int]:
    """How many cases a passing check said passed (``12 passed``, ``Ran 12 tests … OK``), when it said."""
    out = str(step.get("output") or "")
    m = re.findall(r"\b(\d+) passed\b", out) or re.findall(r"\bRan (\d+) tests?\b", out)
    return int(m[-1]) if m else None


def _otsu(values: List[float], bins: int = 64) -> tuple:
    """The threshold that best splits ``values`` in two, and how well (0..1)."""
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return hi, 0.0
    hist = [0] * bins
    for v in values:
        hist[min(bins - 1, int((v - lo) / (hi - lo) * bins))] += 1
    n = len(values)
    mids = [lo + (k + 0.5) * (hi - lo) / bins for k in range(bins)]
    mean = sum(h * m for h, m in zip(hist, mids)) / n
    var = sum(h * (m - mean) ** 2 for h, m in zip(hist, mids)) / n
    best, thr, w0, s0 = -1.0, hi, 0, 0.0
    for k in range(bins - 1):
        w0 += hist[k]
        s0 += hist[k] * mids[k]
        if w0 == 0 or w0 == n:
            continue
        m0, m1 = s0 / w0, (mean * n - s0) / (n - w0)
        between = w0 * (n - w0) * (m0 - m1) ** 2 / (n * n)
        if between > best:
            best, thr = between, lo + (k + 1) * (hi - lo) / bins
    return thr, (best / var if var > 0 else 0.0)


def _burst_gap(gaps: List[float]) -> tuple:
    pos = sorted(g for g in gaps if 0.05 < g < SESSION_GAP_S)
    if len(pos) < 8:
        return BURST_GAP_MIN_S * 3, "few gaps: a pause of a minute splits two bursts"
    thr, eta = _otsu([math.log10(g) for g in pos])
    if eta >= 0.5:
        g, how = 10 ** thr, f"between the run's short and long gaps (Otsu on log gaps, separation {eta:.2f})"
    else:
        g, how = 10 * pos[len(pos) // 2], f"ten times the median gap (the gaps show no two kinds, {eta:.2f})"
    return min(SESSION_GAP_S, max(BURST_GAP_MIN_S, g)), how


def _bucket(span: float) -> int:
    return next((b for b in _NICE if span / b <= 120), _NICE[-1])


def longrun(traj, *, now_s: Optional[float] = None) -> dict:
    data = _data(traj)
    steps = [s for s in data.get("steps") or [] if isinstance(s, dict)]
    items, basis = _items(data)
    running = bool(data.get("in_progress"))
    started_at = data.get("started_at") if isinstance(data.get("started_at"), (int, float)) else None
    order = sorted(items, key=lambda x: (x["start"], x["index"]))
    span = max([x["end"] for x in items] or [0.0])
    total = (data.get("totals") or {}).get("latency_s")
    if isinstance(total, (int, float)) and total > span and basis != "reconstructed":
        span = float(total)
    if running and now_s is not None:
        span = max(span, now_s)
    # ---------------------------------------------------------- the gaps
    gaps, reach = [], 0.0
    for x in order:
        gaps.append(max(0.0, x["start"] - reach) if gaps or reach else 0.0)
        reach = max(reach, x["end"])
    timed = basis != "reconstructed"
    burst_gap, how = _burst_gap(gaps) if timed else (math.inf, "no recorded clock: a burst ends after each check")
    # ---------------------------------------------------------- sessions and bursts
    sessions: List[dict] = []
    bursts: List[dict] = []
    cur: Optional[dict] = None
    for x, g in zip(order, gaps):
        new_session = not sessions or (timed and g >= SESSION_GAP_S)
        new_burst = cur is None or new_session or (timed and g >= burst_gap) or (not timed and cur.get("_closed"))
        if new_session:
            sessions.append({"n": len(sessions) + 1, "from": x["start"], "to": x["end"], "idle_before_s": round(g, 1),
                             "bursts": []})
        if new_burst:
            cur = {"n": len(bursts) + 1, "session": sessions[-1]["n"], "from": x["start"], "to": x["end"],
                   "pause_before_s": round(g, 1), "_items": []}
            bursts.append(cur)
            sessions[-1]["bursts"].append(cur["n"])
        cur["_items"].append(x)
        cur["to"] = max(cur["to"], x["end"])
        sessions[-1]["to"] = max(sessions[-1]["to"], x["end"])
        if not timed and x["check"] is not None:
            cur["_closed"] = True
    # ---------------------------------------------------------- progress, regressions
    last_of: Dict[str, Optional[bool]] = {}
    most_of: Dict[str, int] = {}
    events: List[dict] = []
    for x in order:
        if x["check"] is None:
            continue
        s = steps[x["index"]] if x["index"] < len(steps) else {}
        key = check_key(s)
        before = last_of.get(key)
        n_pass = _passing(s) if x["check"] is True else None
        if x["check"] is True and before is not True:
            events.append({"kind": "progress", "index": x["index"], "t": x["start"], "check": key[key.find(":") + 1:],
                           "how": "first pass" if before is None else "fixed"})
        elif x["check"] is True and n_pass and n_pass > most_of.get(key, n_pass):
            events.append({"kind": "progress", "index": x["index"], "t": x["start"], "check": key[key.find(":") + 1:],
                           "how": f"more passing ({most_of[key]} → {n_pass})"})
        elif x["check"] is False and before is True:
            events.append({"kind": "regression", "index": x["index"], "t": x["start"],
                           "check": key[key.find(":") + 1:]})
        last_of[key] = x["check"]
        if n_pass:
            most_of[key] = max(most_of.get(key, 0), n_pass)
    progress = [ev for ev in events if ev["kind"] == "progress"]
    # ---------------------------------------------------------- each burst
    prev_fps: List[tuple] = []
    for b in bursts:
        its = b.pop("_items")
        b.pop("_closed", None)
        calls = [x for x in its if x["type"] not in ("reason", "answer", "plan")]
        mix = Counter(x["activity"] for x in its)
        tools = Counter(x["name"] for x in calls)
        checks = [x for x in its if x["check"] is not None]
        idx = {x["index"] for x in its}
        mine = [ev for ev in events if ev["index"] in idx]
        fp = Counter(_sig(steps[x["index"]]) for x in calls if x["index"] < len(steps))
        # the same failing call, again and again inside the burst
        streak, best_streak, best_sig, last_sig = 0, 0, "", None
        for x in calls:
            sg = _sig(steps[x["index"]]) if x["index"] < len(steps) else ""
            failed = x["error"] or x["check"] is False
            streak = streak + 1 if (sg == last_sig and failed) else (1 if failed else 0)
            last_sig = sg
            if streak > best_streak:
                best_streak, best_sig = streak, sg
        repeat_of, sim = None, 0.0
        if fp and not any(ev["kind"] == "progress" for ev in mine):
            keys = set(fp)
            for n, pk in reversed(prev_fps[-REPEAT_WINDOW:]):
                j = len(keys & pk) / len(keys | pk) if keys | pk else 0.0
                if j >= REPEAT_JACCARD and j > sim:
                    repeat_of, sim = n, j
        prev_fps.append((b["n"], set(fp)))
        failed = sum(1 for x in checks if x["check"] is False)
        status = ("progress" if any(ev["kind"] == "progress" for ev in mine) else
                  "regressed" if any(ev["kind"] == "regression" for ev in mine) else
                  "stuck" if repeat_of or best_streak >= 3 else
                  "failing" if failed else
                  "done" if any(x["type"] == "answer" for x in its) else
                  "working" if mix.get("edit") or mix.get("run") or mix.get("delegate") else "exploring")
        lat = [x["latency_s"] for x in calls]
        b.update({"from": round(b["from"], 3), "to": round(b["to"], 3), "seconds": round(b["to"] - b["from"], 1),
                  "first": min(idx), "last": max(idx), "steps": len(its), "calls": len(calls),
                  "mix": dict(mix.most_common()), "tools": tools.most_common(3),
                  "errors": sum(1 for x in its if x["error"]), "edits": mix.get("edit", 0),
                  "checks": {"passed": sum(1 for x in checks if x["check"] is True), "failed": failed},
                  "tokens": sum(x["tokens"] for x in its), "sec_per_call": round(sum(lat) / len(lat), 2) if lat else None,
                  "events": mine, "repeat_of": repeat_of, "similarity": round(sim, 2) if repeat_of else None,
                  "streak": {"calls": best_streak, "call": best_sig} if best_streak >= 3 else None,
                  "status": status})
        b["note"] = _note(b)
    for s in sessions:
        mine = [bursts[n - 1] for n in s["bursts"]]
        s.update({"from": round(s["from"], 3), "to": round(s["to"], 3), "seconds": round(s["to"] - s["from"], 1),
                  "active_s": round(sum(b["seconds"] for b in mine), 1), "calls": sum(b["calls"] for b in mine),
                  "steps": sum(b["steps"] for b in mine),
                  "progress": sum(1 for b in mine for ev in b["events"] if ev["kind"] == "progress"),
                  "stuck": sum(1 for b in mine if b["status"] == "stuck")})
    # ---------------------------------------------------------- the stall
    marks = [{"t": 0.0, "index": None, "burst": 1}] + [
        {"t": ev["t"], "index": ev["index"], "burst": _burst_of(bursts, ev["index"])} for ev in progress]
    end = {"t": span, "index": None, "burst": len(bursts)}
    stall = None
    for a, b in zip(marks, marks[1:] + [end]):
        st = _stretch(bursts, a, b)
        if st["calls"] and (stall is None or st["active_s"] > stall["active_s"]):
            stall = st
    if stall:
        stall["open"] = stall["to_index"] is None
        stall["sentence"] = _stall_sentence(stall, running, started_at, span)
    # ---------------------------------------------------------- loops over hours
    loops, chain = [], []
    for b in bursts + [None]:
        if b is not None and b["repeat_of"]:
            chain.append(b)
            continue
        if len(chain) >= 2:
            loops.append(_loop(chain, steps, bursts[chain[0]["repeat_of"] - 1]))
        chain = []
    # ---------------------------------------------------------- pace, rhythm
    bucket = _bucket(span or 1.0)
    pace: Dict[int, dict] = {}
    for x in order:
        k = int(x["start"] // bucket)
        p = pace.setdefault(k, {"from": k * bucket, "calls": 0, "steps": 0, "errors": 0, "failed": 0, "passed": 0,
                                "lat": 0.0, "tokens": 0, "mix": Counter()})
        p["steps"] += 1
        p["mix"][x["activity"]] += 1
        p["tokens"] += x["tokens"]
        if x["type"] not in ("reason", "answer", "plan"):
            p["calls"] += 1
            p["lat"] += x["latency_s"]
        p["errors"] += int(x["error"])
        p["failed"] += int(x["check"] is False)
        p["passed"] += int(x["check"] is True)
    pace_rows = []
    for k in sorted(pace):
        p = pace[k]
        pace_rows.append({"from": p["from"], "to": p["from"] + bucket, "calls": p["calls"], "steps": p["steps"],
                          "errors": p["errors"], "failed": p["failed"], "passed": p["passed"],
                          "sec_per_call": round(p["lat"] / p["calls"], 2) if p["calls"] else None,
                          "tokens_per_step": round(p["tokens"] / p["steps"]) if p["steps"] else None,
                          "mix": dict(p["mix"])})
    active = sum(b["seconds"] for b in bursts)
    calls = sum(b["calls"] for b in bursts)
    here = _look(stall, loops, bursts, events, running)
    rhythm = _rhythm(order, bursts, events, span, started_at)
    out = {"task": (data.get("task") or {}).get("id"), "agent": (data.get("agent") or {}).get("name"),
           "success": None if running else (data.get("outcome") or {}).get("success"), "in_progress": running,
           "started_at": started_at, "span_s": round(span, 1), "active_s": round(active, 1),
           "idle_s": round(max(0.0, span - active), 1), "steps": len(items), "calls": calls,
           "basis": {"clock": basis, "session_gap_s": SESSION_GAP_S,
                     "burst_gap_s": None if not timed else round(burst_gap, 1), "burst_gap_how": how,
                     "progress": "a check passing where its previous run did not, or with more cases passing than ever; an edit "
                                 "alone is not progress",
                     "repeat": f"a burst sharing ≥{REPEAT_JACCARD:.0%} of its calls with an earlier one, without progress"},
           "sessions": sessions, "bursts": bursts, "events": events, "stall": stall, "loops": loops,
           "pace": {"bucket_s": bucket, "rows": pace_rows, "trend": _trend(pace_rows)}, "rhythm": rhythm,
           "chapters": _chapters(sessions, bursts, loops), "look_here": here}
    if running and order:
        out["quiet_s"] = round(max(0.0, (now_s if now_s is not None else span) - reach), 1)
    out["sentence"] = _headline(out)
    return out


def _rhythm(order: List[dict], bursts: List[dict], events: List[dict], span: float, started_at) -> Optional[dict]:
    """The run as a grid: a row per day and a column per hour (a row per hour and
    a column per 5 minutes for a run of hours), each cell what happened in it.
    On the wall clock (UTC) when the trace says when it started."""
    if span >= 12 * 3600:
        row_u, col_u, unit = DAY, 3600.0, "day × hour"
    elif span >= 3600:
        row_u, col_u, unit = 3600.0, 300.0, "hour × 5 minutes"
    else:
        return None
    origin = float(started_at) if started_at is not None else 0.0
    base = math.floor(origin / row_u) * row_u

    def cell_of(t: float) -> tuple:
        a = origin + t - base
        return int(a // row_u), int((a % row_u) // col_u)
    cells: Dict[tuple, dict] = {}
    for x in order:
        c = cells.setdefault(cell_of(x["start"]), {"steps": 0, "calls": 0, "errors": 0, "failed": 0, "passed": 0,
                                                   "progress": 0, "stuck": False})
        c["steps"] += 1
        c["calls"] += int(x["type"] not in ("reason", "answer", "plan"))
        c["errors"] += int(x["error"])
        c["failed"] += int(x["check"] is False)
        c["passed"] += int(x["check"] is True)
    for ev in events:
        if ev["kind"] == "progress":
            cells.setdefault(cell_of(ev["t"]), {"steps": 0, "calls": 0, "errors": 0, "failed": 0, "passed": 0,
                                                "progress": 0, "stuck": False})["progress"] += 1
    for b in bursts:
        if b["status"] == "stuck":
            k = cell_of(b["from"])
            if k in cells:
                cells[k]["stuck"] = True
    rows = cell_of(span)[0] + 1
    out = []
    for (r, c), v in sorted(cells.items()):
        t0 = base + r * row_u + c * col_u - origin
        out.append(dict(v, row=r, col=c, t0=round(t0, 1), t1=round(t0 + col_u, 1)))
    return {"unit": unit, "row_s": row_u, "col_s": col_u, "rows": rows, "cols": int(row_u // col_u),
            "wall_clock": started_at is not None, "row0_s": round(base - origin, 1), "cells": out}


def _trend(rows: List[dict]) -> Optional[dict]:
    """Whether the agent slowed down or its context grew: the first third of its
    working buckets against the last third."""
    work = [r for r in rows if r["calls"]]
    if len(work) < 6:
        return None
    k = max(1, len(work) // 3)

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None
    a_sec, b_sec = mean(r["sec_per_call"] for r in work[:k]), mean(r["sec_per_call"] for r in work[-k:])
    a_tok, b_tok = mean(r["tokens_per_step"] for r in work[:k]), mean(r["tokens_per_step"] for r in work[-k:])
    bits = []
    if a_sec and b_sec and abs(b_sec / a_sec - 1) >= 0.25:
        bits.append(f"seconds per call went from {a_sec:.1f} to {b_sec:.1f}")
    if a_tok and b_tok and abs(b_tok / a_tok - 1) >= 0.25:
        bits.append(f"tokens per step from {a_tok:,.0f} to {b_tok:,.0f}")
    return {"sec_per_call": [a_sec, b_sec], "tokens_per_step": [a_tok, b_tok], "buckets": [k, len(work)],
            "sentence": ("Between its first and last thirds, " + " and ".join(bits) + ".") if bits else
            "Its pace held: seconds per call and tokens per step stayed within a quarter of where they began."}


def _chapters(sessions: List[dict], bursts: List[dict], loops: List[dict]) -> List[dict]:
    """The run's story in order: a session break, a burst, or a loop of bursts as one line."""
    in_loop = {}
    for k, lp in enumerate(loops):
        for n in range(lp["bursts"][0], lp["bursts"][1] + 1):
            in_loop[n] = k
    out, seen_loop, last_session = [], set(), None
    for b in bursts:
        k = in_loop.get(b["n"])
        if b["session"] != last_session:
            s = sessions[b["session"] - 1]
            # idle inside a loop is part of the loop: it went round, slept, and went round again
            if last_session is not None and not (k is not None and b["n"] != loops[k]["bursts"][0]):
                out.append({"kind": "idle", "seconds": s["idle_before_s"], "session": s["n"], "t": s["from"]})
            last_session = b["session"]
        if k is not None:
            if k not in seen_loop:
                seen_loop.add(k)
                out.append({"kind": "loop", "loop": k, **loops[k]})
            continue
        out.append({"kind": "burst", "burst": b["n"]})
    return out


def _burst_of(bursts: List[dict], index: int) -> Optional[int]:
    for b in bursts:
        if b["first"] <= index <= b["last"]:
            return b["n"]
    return None


def _stretch(bursts: List[dict], a: dict, b: dict) -> dict:
    """The work between two progress marks: wall time, active time, calls."""
    t0, t1 = a["t"], b["t"]
    active = calls = 0.0
    inside = []
    for x in bursts:
        lo, hi = max(t0, x["from"]), min(t1, x["to"])
        if hi > lo or (x["from"] >= t0 and x["to"] <= t1):
            share = (hi - lo) / x["seconds"] if x["seconds"] > 0 else 1.0
            active += max(0.0, hi - lo)
            calls += x["calls"] * min(1.0, max(0.0, share))
            inside.append(x["n"])
    return {"from": round(t0, 1), "to": round(t1, 1), "wall_s": round(t1 - t0, 1), "active_s": round(active, 1),
            "calls": int(round(calls)), "from_index": a["index"], "to_index": b["index"],
            "bursts": [inside[0], inside[-1]] if inside else None}


def _loop(chain: List[dict], steps: List[dict], origin: dict) -> dict:
    """Bursts repeating in a row, from the burst the first of them repeats."""
    shared = None
    for b in chain:
        fp = {_sig(steps[i]) for i in range(b["first"], b["last"] + 1)
              if i < len(steps) and steps[i].get("type") not in ("reason", "answer", "plan")}
        shared = fp if shared is None else shared & fp
    failing = Counter()
    for b in chain:
        for i in range(b["first"], b["last"] + 1):
            s = steps[i] if i < len(steps) else {}
            if s.get("error"):
                failing[check_key(s)] += 1
    return {"bursts": [origin["n"], chain[-1]["n"]], "count": len(chain) + 1,
            "sessions": len({origin["session"]} | {b["session"] for b in chain}),
            "from": origin["from"], "to": chain[-1]["to"], "wall_s": round(chain[-1]["to"] - origin["from"], 1),
            "active_s": round(origin["seconds"] + sum(b["seconds"] for b in chain), 1),
            "calls": origin["calls"] + sum(b["calls"] for b in chain),
            "first_index": origin["first"], "shared": sorted(shared or [])[:6],
            "failing": failing.most_common(1)[0][0].split(":", 1)[1] if failing else None}


def _note(b: dict) -> str:
    bits = []
    for ev in b["events"]:
        bits.append(f"{'✓ ' + ev['how'] if ev['kind'] == 'progress' else '✗ regressed'}: {ev['check'][:60]}")
    if b["repeat_of"]:
        bits.append(f"repeats burst {b['repeat_of']} ({b['similarity']:.0%} of its calls)")
    if b["streak"]:
        bits.append(f"the same failing call {b['streak']['calls']}× in a row")
    if not bits and b["checks"]["failed"]:
        bits.append(f"{b['checks']['failed']} failed check(s)")
    return "; ".join(bits)


def dur(s: float) -> str:
    s = float(s or 0)
    if s >= DAY:
        return f"{int(s // DAY)}d {int(s % DAY // 3600)}h"
    if s >= 3600:
        return f"{int(s // 3600)}h {int(s % 3600 // 60):02d}m"
    if s >= 60:
        return f"{int(s // 60)}m {int(s % 60):02d}s"
    return f"{s:.0f}s"


def when(t: float, started_at: Optional[float], span: float) -> str:
    """A moment of the run as a reader says it: ``day 2 03:14`` (UTC) when the
    trace says when it started, else ``+27h 14m`` from its start."""
    if started_at is not None:
        import datetime as _dt
        w = _dt.datetime.fromtimestamp(started_at + t, tz=_dt.timezone.utc)
        d0 = _dt.datetime.fromtimestamp(started_at, tz=_dt.timezone.utc).date()
        day = (w.date() - d0).days + 1
        return f"day {day} {w:%H:%M}" if span >= 12 * 3600 else f"{w:%H:%M:%S}" if span < 3600 else f"{w:%H:%M}"
    return f"+{dur(t)}"


def _stall_sentence(st: dict, running: bool, started_at, span: float) -> str:
    since = "the start" if st["from_index"] is None else f"step {st['from_index']} ({when(st['from'], started_at, span)})"
    until = ("and it is still going" if running else "to the end of the run") if st["to_index"] is None else \
        f"until step {st['to_index']} ({when(st['to'], started_at, span)})"
    return (f"No progress for {dur(st['wall_s'])} ({dur(st['active_s'])} of it working, {st['calls']:,} calls): "
            f"from {since} {until}.")


def _look(stall, loops, bursts, events, running) -> Optional[dict]:
    """For a long run, where to look first: the start of its longest loop over
    hours, else the start of its stall, else its first regression."""
    if loops:
        lp = max(loops, key=lambda x: x["active_s"])
        return {"index": lp["first_index"], "burst": lp["bursts"][0], "kind": "loop",
                "sentence": f"Burst {lp['bursts'][0]}: from here {lp['count']} bursts in a row did the same calls "
                            f"over {dur(lp['wall_s'])} ({lp['calls']:,} calls)"
                            + (f", with `{lp['failing'][:60]}` failing" if lp["failing"] else "") + "."}
    if stall and stall["active_s"] >= 600 and stall["bursts"]:
        b = bursts[stall["bursts"][0] - 1]
        return {"index": b["first"] if stall["from_index"] is None else stall["from_index"], "burst": b["n"],
                "kind": "stall", "sentence": stall["sentence"]}
    reg = next((ev for ev in events if ev["kind"] == "regression"), None)
    if reg:
        return {"index": reg["index"], "burst": _burst_of(bursts, reg["index"]), "kind": "regression",
                "sentence": f"Step {reg['index']}: `{reg['check'][:60]}` failed after it had passed."}
    return None


def _headline(r: dict) -> str:
    n_s, n_b = len(r["sessions"]), len(r["bursts"])
    prog = sum(1 for ev in r["events"] if ev["kind"] == "progress")
    lead = (f"{dur(r['span_s'])} on the clock, {dur(r['active_s'])} of it working, in {n_s} session(s) and "
            f"{n_b} burst(s); {r['calls']:,} calls; {prog} step(s) forward (a check starting to pass, or passing more).")
    if r["loops"]:
        lp = max(r["loops"], key=lambda x: x["active_s"])
        lead += f" Its longest loop: {lp['count']} bursts doing the same calls over {dur(lp['wall_s'])}."
    elif r["stall"] and r["stall"]["active_s"] >= 600:
        lead += " " + r["stall"]["sentence"]
    return lead


def window(traj, *, burst: Optional[int] = None, t0: Optional[float] = None, t1: Optional[float] = None,
           reading: Optional[dict] = None) -> dict:
    """The steps inside one burst, or between two moments: every call, for drawing one by one."""
    data = _data(traj)
    r = reading or longrun(data)
    items, _ = _items(data)
    if burst is not None and 1 <= burst <= len(r["bursts"]):
        b = r["bursts"][burst - 1]
        lo, hi = b["first"], b["last"]
        mine = [x for x in items if lo <= x["index"] <= hi]
        t0, t1 = b["from"], b["to"]
    else:
        t0 = 0.0 if t0 is None else max(0.0, t0)
        t1 = r["span_s"] if t1 is None else t1
        mine = [x for x in items if x["end"] >= t0 and x["start"] <= t1]
    steps = data.get("steps") or []
    for x in mine:
        s = steps[x["index"]] if x["index"] < len(steps) else {}
        x["call"] = " ".join(_command(s).split())[:140]
        x["said"] = " ".join(str(s.get("output") or "").split())[:140]
    return {"from": t0, "to": max(t1, t0 + 1e-3), "steps": mine, "burst": burst}
