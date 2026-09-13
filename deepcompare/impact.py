"""Where it mattered: a run's steps weighed, for a focus-and-context timeline.

A five-hundred-step run is not shown at one scale. This module clusters a
run's steps into contiguous stretches and gives each an *impact* — a sum
of what the other sections already established about the steps inside —
so a timeline can dilate the stretches where the run was decided and
constrict the ones where nothing notable happened. Nothing here is a new
judgement: every point comes from a section that is itself measured or
adjudicated (the diagnosis's decisive step, the attribution's fault path,
the ranked divergences, the timing ledger's errors, retries and wasted
seconds, the milestones reached, the answer, and the step's own tokens).

Scoring per step (``WEIGHTS``; the sum over a cluster's steps is its raw
score, plus ``0.2 × ln(1 + seconds)`` so a long stretch is never invisible):

    decisive          5    the diagnosis's decisive step (when its subject is this side)
    fault             3    a step on ``attribution.chain`` (when ``failed_agent`` is this side)
    first_divergence  3    this side's step on the lowest-ranked divergence row that has one
    divergence        1.5  this side's step on any ranked divergence row
    error             2    ``step.error`` or the timing ledger's ``wasted == "error"``
    retry             1    a timing row with ``retry_of`` set
    wasted            0.5  per wasted second, capped at ``WASTED_CAP`` (3) per step
    milestone         2    a milestone reached at this step
    answer            1    the answer step
    tokens            0.5  × tokens / the run's largest step tokens

``impact`` is the cluster's score divided by the largest cluster score
over **both** runs of the pair (``scale: "pair"``), so equal impacts mean
equal scores and a uniform clean run reads quiet beside the failing one;
the pair's maximum is 1.0. Only when the other side has no steps is a run
scaled to its own maximum (``scale: "run"``). Every impact is 0 when every
score is 0. ``kind`` is ``hot`` at impact ≥ 0.5, ``work`` at ≥ 0.15, else
``quiet``; ``hot`` names up to five hot cluster ids by impact.

Clustering: a boundary opens at every lane change (the depth-1 sub-agent
acting, from ``step.span`` — nested spans belong to their depth-1
ancestor), at every change of reading phase, and before a step whose
intent frames or decides (``BOUNDARY_INTENTS`` from :mod:`horizon`). Then
same-lane neighbours that both score at or below the 25th percentile
merge, so quiet stretches coalesce; any cluster over ``SPLIT_OVER`` (40) steps is cut
into chunks of at most ``CHUNK`` (25) at its lowest-scoring steps; and, when
more than ``TARGET_MAX`` (48) clusters remain, same-lane neighbours merge
until the count fits, the pair whose merge changes the picture least first
(the smallest ``steps × |difference in score per step|``, so alike
stretches join before a hot chunk is diluted into a quiet one; never
across a lane, never above ``SPLIT_OVER`` steps). The result covers every
step exactly once, in order, never fewer than one cluster.
"""

from __future__ import annotations

import math
from typing import Optional

from .horizon import BOUNDARY_INTENTS

TOOLISH = ("tool_call", "search", "retrieve", "read")

#: the points a step earns, per notable thing about it (see the module docstring)
WEIGHTS = {
    "decisive": 5.0,
    "fault": 3.0,
    "first_divergence": 3.0,
    "divergence": 1.5,
    "error": 2.0,
    "retry": 1.0,
    "wasted_per_s": 0.5,
    "milestone": 2.0,
    "answer": 1.0,
    "tokens": 0.5,
}
WASTED_CAP = 3.0        #: the most a step can earn from wasted seconds
TIME_WEIGHT = 0.2       #: cluster score += TIME_WEIGHT × ln(1 + seconds)
HOT = 0.5               #: impact at or above this is "hot"
WORK = 0.15             #: impact at or above this (and below HOT) is "work"
MAX_HOT = 5             #: how many ids ImpactRun.hot names
MAX_MARKS = 12          #: marks per cluster
SPLIT_OVER = 40         #: clusters with more steps than this are split ...
CHUNK = 25              #: ... into chunks of at most this many steps
TARGET_MAX = 48         #: coalesce until at most this many clusters remain
MARK_ORDER = {"decisive": 0, "fault": 1, "error": 2, "retry": 3, "milestone": 3, "divergence": 3, "answer": 3}


def _fmt_s(v: float) -> str:
    return f"{v:.0f}s" if v >= 10 else f"{v:.1f}s" if v >= 1 else f"{v:.2f}s"


def _fmt_score_s(v: float) -> str:
    return f"{v:.0f}s" if v >= 10 else f"{v:.1f}s"


# ---------------------------------------------------------------- lanes

def _lanes_of(steps: list, root_agent: str) -> tuple:
    """Per step: the lane agent (the root, or the depth-1 sub-agent whose
    span — directly or through nesting — the step sits in), and the span
    agent as recorded. Returns (lane_of[list], span_agent_of[list], lanes[list])."""
    spans: dict = {}
    for st in steps:
        sp = st.get("span") if isinstance(st.get("span"), dict) else None
        if sp and sp.get("id") and sp.get("agent"):
            sid = str(sp["id"])
            if sid not in spans:
                spans[sid] = {"agent": str(sp["agent"]), "parent": str(sp["parent"]) if sp.get("parent") else None}

    def top(sid: str) -> Optional[str]:
        """The depth-1 ancestor span id of ``sid`` (itself when it is depth 1)."""
        seen = set()
        cur = sid
        while cur in spans and cur not in seen:
            seen.add(cur)
            parent = spans[cur]["parent"]
            if parent is None or parent not in spans:
                return cur
            cur = parent
        return sid if sid in spans else None

    lane_of: list = []
    span_agent: list = []
    lanes: list = []
    seen_lane: dict = {}
    for st in steps:
        sp = st.get("span") if isinstance(st.get("span"), dict) else None
        sid = str(sp["id"]) if sp and sp.get("id") and str(sp["id"]) in spans else None
        agent = spans[sid]["agent"] if sid else root_agent
        span_agent.append(agent)
        if sid is None:
            lane, depth, parent = root_agent, 0, None
        else:
            t = top(sid)
            lane, depth, parent = spans[t]["agent"], 1, root_agent
        lane_of.append(lane)
        if lane not in seen_lane:
            seen_lane[lane] = {"agent": lane, "depth": depth, "parent": parent, "clusters": []}
            lanes.append(seen_lane[lane])
    if root_agent not in seen_lane:
        lanes.insert(0, {"agent": root_agent, "depth": 0, "parent": None, "clusters": []})
    return lane_of, span_agent, lanes


# ---------------------------------------------------------------- per-step facts

def _step_facts(report: dict, side: str, steps: list) -> list:
    """Everything a step can score for, read from the report's sections;
    every section is optional and a missing one contributes nothing."""
    timing = ((report.get("timing") or {}).get(side) or {})
    trows = {r["index"]: r for r in (timing.get("steps") or []) if isinstance(r, dict) and isinstance(r.get("index"), int)}
    diag = report.get("diagnosis") or {}
    decisive = None
    if diag.get("subject") == side:
        d = (diag.get("decisive_step") or {}).get("step")
        decisive = d if isinstance(d, int) else None
    attribution = report.get("attribution") or {}
    fault = set()
    if attribution.get("failed_agent") == side:
        fault = {i for i in (attribution.get("chain") or []) if isinstance(i, int)}
    key = f"{side}_index"
    rows: dict = {}
    first_div = None
    for row in sorted((r for r in (report.get("divergences") or []) if isinstance(r, dict)), key=lambda r: r.get("rank") if isinstance(r.get("rank"), (int, float)) else 1e9):
        idx = row.get(key)
        if isinstance(idx, int):
            rows.setdefault(idx, []).append(row)
            if first_div is None:
                first_div = idx
    ms_at: dict = {}
    for m in (((report.get("milestones") or {}).get(side) or {}).get("milestones") or []):
        if isinstance(m, dict) and m.get("reached") and isinstance(m.get("step"), int):
            ms_at.setdefault(m["step"], []).append(str(m.get("id") or m.get("label") or ""))
    max_tokens = max((float(st.get("tokens") or 0) for st in steps if isinstance(st.get("tokens"), (int, float))), default=0.0)
    facts: list = []
    clock = 0.0
    for pos, st in enumerate(steps):
        i = st.get("index") if isinstance(st.get("index"), int) else pos
        tr = trows.get(i) or {}
        lat = float(tr.get("latency_s") or 0.0) if timing.get("measurable") else 0.0
        wasted_s = lat if tr.get("wasted") else 0.0
        error = bool(st.get("error")) or tr.get("wasted") == "error"
        retry = tr.get("retry_of") if isinstance(tr.get("retry_of"), int) else None
        tokens = float(st.get("tokens") or 0) if isinstance(st.get("tokens"), (int, float)) else 0.0
        f = {
            "index": i, "pos": pos, "type": st.get("type"), "name": st.get("name") or "",
            "latency_s": lat, "start_s": clock, "end_s": clock + lat,
            "decisive": decisive == i, "fault": i in fault, "first_divergence": first_div == i,
            "divergence_rows": rows.get(i, []), "error": error, "retry_of": retry,
            "wasted_s": wasted_s, "milestones": ms_at.get(i, []), "answer": st.get("type") == "answer",
            "tokens": int(tokens),
        }
        f["score"] = _step_score(f, max_tokens)
        facts.append(f)
        clock += lat
    return facts


def _step_score(f: dict, max_tokens: float) -> float:
    w = WEIGHTS
    s = 0.0
    if f["decisive"]:
        s += w["decisive"]
    if f["fault"]:
        s += w["fault"]
    if f["first_divergence"]:
        s += w["first_divergence"]
    s += w["divergence"] * len(f["divergence_rows"])
    if f["error"]:
        s += w["error"]
    if f["retry_of"] is not None:
        s += w["retry"]
    s += min(WASTED_CAP, w["wasted_per_s"] * f["wasted_s"])
    s += w["milestone"] * len(f["milestones"])
    if f["answer"]:
        s += w["answer"]
    if max_tokens > 0 and f["tokens"] > 0:
        s += w["tokens"] * f["tokens"] / max_tokens
    return s


# ---------------------------------------------------------------- clustering

def _initial_groups(facts: list, lane_of: list, phase_of: dict, intent_of: dict) -> list:
    """Position ranges [start, end) that open at every lane change, phase
    change, and before a framing/deciding step."""
    groups: list = []
    start = 0
    for pos in range(1, len(facts)):
        i = facts[pos]["index"]
        leads = intent_of.get(i) in BOUNDARY_INTENTS or facts[pos]["type"] == "plan"
        if lane_of[pos] != lane_of[pos - 1] or phase_of.get(i) != phase_of.get(facts[pos - 1]["index"]) or leads:
            groups.append((start, pos))
            start = pos
    groups.append((start, len(facts)))
    return groups


def _raw(facts: list, lo: int, hi: int) -> float:
    return sum(facts[p]["score"] for p in range(lo, hi))


def _percentile(values: list, q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _merge_quiet(groups: list, facts: list, lane_of: list) -> list:
    scores = [_raw(facts, lo, hi) for lo, hi in groups]
    p25 = _percentile(scores, 0.25)
    quiet = [s <= p25 for s in scores]
    out: list = []
    for g, q in zip(groups, quiet):
        if out and q and out[-1][2] and lane_of[g[0]] == lane_of[out[-1][0]]:
            out[-1] = (out[-1][0], g[1], True)
        else:
            out.append((g[0], g[1], q))
    return [(lo, hi) for lo, hi, _ in out]


def _split_long(groups: list, facts: list) -> list:
    """Cut every group longer than SPLIT_OVER into chunks of at most CHUNK
    steps, choosing the cut points whose steps score least (a cut opens
    a chunk before that step)."""
    out: list = []
    for lo, hi in groups:
        n = hi - lo
        if n <= SPLIT_OVER:
            out.append((lo, hi))
            continue
        # best[j] = (total cut score, cuts) covering positions [0, j) with chunks <= CHUNK
        best: list = [None] * (n + 1)
        best[0] = (0.0, ())
        for j in range(1, n + 1):
            cand = None
            for k in range(max(0, j - CHUNK), j):
                if best[k] is None:
                    continue
                cost = best[k][0] + (facts[lo + k]["score"] if k > 0 else 0.0)
                if cand is None or cost < cand[0] - 1e-12:
                    cand = (cost, best[k][1] + ((k,) if k > 0 else ()))
            best[j] = cand
        cuts = list(best[n][1]) if best[n] else []
        edges = [0] + cuts + [n]
        for s, e in zip(edges, edges[1:]):
            out.append((lo + s, lo + e))
    return out


def _coalesce(groups: list, facts: list, lane_of: list) -> list:
    """Merge same-lane neighbours until at most TARGET_MAX groups remain,
    never above SPLIT_OVER steps. The pair merged first is the one whose
    merge changes the picture least: the smallest ``steps × |difference
    in score per step|`` — so alike stretches (a quiet run, or a struggle
    of repeated failing calls that the reading's phases chopped up) join
    before a hot chunk is diluted into a quiet one."""
    groups = list(groups)
    scores = [_raw(facts, lo, hi) for lo, hi in groups]
    while len(groups) > TARGET_MAX:
        best = None
        for k in range(len(groups) - 1):
            (lo1, hi1), (lo2, hi2) = groups[k], groups[k + 1]
            n1, n2 = hi1 - lo1, hi2 - lo2
            if lane_of[lo1] != lane_of[lo2] or n1 + n2 > SPLIT_OVER:
                continue
            cost = ((n1 + n2) * abs(scores[k] / n1 - scores[k + 1] / n2), scores[k] + scores[k + 1], k)
            if best is None or cost < best:
                best = cost
        if best is None:
            break
        k = best[2]
        groups[k:k + 2] = [(groups[k][0], groups[k + 1][1])]
        scores[k:k + 2] = [scores[k] + scores[k + 1]]
    return groups


# ---------------------------------------------------------------- clusters

def _mark_label(f: dict, kind: str) -> str:
    name = f["name"] or f["type"] or "step"
    if kind == "decisive":
        return f"decisive step ({name})"
    if kind == "fault":
        return f"on the fault's path ({name})"
    if kind == "error":
        return f"error: {name}"
    if kind == "retry":
        return f"{name}, a retry of step {f['retry_of']}"
    if kind == "milestone":
        return "milestone " + ", ".join(f["milestones"])
    if kind == "divergence":
        ranks = ", ".join(f"#{r.get('rank')}" for r in f["divergence_rows"])
        return f"divergence {ranks}" + (" — the first" if f["first_divergence"] else "")
    return "the answer"


def _marks(chunk: list) -> list:
    marks: list = []
    for f in chunk:
        for kind, hit in (("decisive", f["decisive"]), ("fault", f["fault"]), ("error", f["error"]),
                          ("retry", f["retry_of"] is not None), ("milestone", bool(f["milestones"])),
                          ("divergence", bool(f["divergence_rows"])), ("answer", f["answer"])):
            if hit:
                marks.append({"step": f["index"], "kind": kind, "label": _mark_label(f, kind)})
    marks.sort(key=lambda m: (MARK_ORDER[m["kind"]], m["step"]))
    return marks[:MAX_MARKS]


def _label(chunk: list) -> str:
    tools: dict = {}
    for f in chunk:
        if f["type"] in TOOLISH:
            tools[f["name"] or "?"] = tools.get(f["name"] or "?", 0) + 1
    if tools:
        top = max(tools, key=lambda k: (tools[k], k))
        return f"{top} ×{tools[top]}" if tools[top] > 1 else top
    if any(f["answer"] for f in chunk):
        return "the answer"
    return "thinking"


def _why(reasons: dict) -> str:
    bits: list = []
    if reasons["decisive"]:
        bits.append("decisive step")
    if reasons["errors"]:
        bits.append(f"{reasons['errors']} error{'s' if reasons['errors'] != 1 else ''}")
    if reasons["retries"]:
        bits.append(f"{reasons['retries']} retr{'ies' if reasons['retries'] != 1 else 'y'}")
    if reasons["wasted_s"] >= 0.05:
        bits.append(f"{_fmt_score_s(reasons['wasted_s'])} wasted")
    if reasons["fault_steps"]:
        n = reasons["fault_steps"]
        bits.append("on the fault's path" if n == 1 else f"{n} steps on the fault's path")
    if reasons["first_divergence"]:
        bits.append("the first divergence")
    if reasons["divergence_rows"]:
        n = reasons["divergence_rows"]
        bits.append(f"{n} divergence row{'s' if n != 1 else ''}")
    if reasons["milestones"]:
        bits.append("milestone " + ", ".join(reasons["milestones"]))
    if reasons["answer"]:
        bits.append("the answer")
    if not bits:
        return f"{reasons['tokens']} tokens, nothing else notable" if reasons["tokens"] else "nothing notable"
    return "; ".join(bits)


def _cluster(cid: str, chunk: list, lane: str, span_agent: list) -> dict:
    agents: list = []
    for f in chunk:
        a = span_agent[f["pos"]]
        if a not in agents:
            agents.append(a)
    seconds = sum(f["latency_s"] for f in chunk)
    raw = sum(f["score"] for f in chunk)
    reasons = {
        "fault_steps": sum(1 for f in chunk if f["fault"]),
        "decisive": any(f["decisive"] for f in chunk),
        "errors": sum(1 for f in chunk if f["error"]),
        "retries": sum(1 for f in chunk if f["retry_of"] is not None),
        "wasted_s": round(sum(f["wasted_s"] for f in chunk), 4),
        "milestones": [m for f in chunk for m in f["milestones"]],
        "divergence_rows": sum(len(f["divergence_rows"]) for f in chunk),
        "first_divergence": any(f["first_divergence"] for f in chunk),
        "answer": any(f["answer"] for f in chunk),
        "tokens": sum(f["tokens"] for f in chunk),
    }
    return {
        "id": cid, "from": chunk[0]["index"], "to": chunk[-1]["index"], "steps": len(chunk),
        "start_s": round(chunk[0]["start_s"], 4), "end_s": round(chunk[-1]["end_s"], 4), "seconds": round(seconds, 4),
        "lane": lane, "agents": agents,
        "impact": 0.0, "score": round(raw + TIME_WEIGHT * math.log1p(max(0.0, seconds)), 4), "kind": "quiet",
        "reasons": reasons, "why": _why(reasons), "label": _label(chunk), "marks": _marks(chunk),
    }


def _side_name(report: dict, side: str) -> str:
    return str((((report.get(side) or {}).get("agent") or {}).get("name")) or side)


def _empty(name: str) -> dict:
    return {"measurable": False, "total_s": 0.0, "total_steps": 0, "clusters": [], "hot": [], "lanes": [], "scale": "run",
            "narrative": f"{name}: no steps, so nothing to weigh."}


def impact_run(report: dict, side: str) -> dict:
    """One side of a report weighed: its clusters, the hot ones, its
    lanes, a narrative. Works on the report dict alone; a missing
    optional section contributes nothing and never raises."""
    name = _side_name(report, side)
    steps = [s for s in ((report.get(side) or {}).get("steps") or []) if isinstance(s, dict)]
    if not steps:
        return _empty(name)
    facts = step_facts(report, side, steps)
    grouped = cluster_steps(report, side, steps, facts)
    out = {"measurable": True, "total_s": grouped["total_s"], "total_steps": len(steps), "clusters": grouped["clusters"],
           "hot": [], "lanes": grouped["lanes"], "scale": "run", "narrative": ""}
    _finish(out, name, max((c["score"] for c in out["clusters"]), default=0.0), "run")
    return out


def step_facts(report: dict, side: str, steps: list) -> list:
    """Public: one fact record per step of ``side`` (index, pos, type,
    name, latency, clock, the flags every section established, tokens)
    with the impact ``score`` — the input :func:`cluster_steps` groups. A
    caller weighing the steps by something else (:mod:`deepcompare.rl`
    by reward and credit) replaces ``score`` before clustering."""
    return _step_facts(report, side, steps)


def cluster_steps(report: dict, side: str, steps: list, facts: list, marks=None) -> dict:
    """Public: the clustering pipeline over ready-made ``facts`` — the
    same lane, phase and framing boundaries, quiet merging, long-stretch
    splitting and coalescing :func:`impact_run` uses, grouped by whatever
    ``score`` the facts carry. ``marks`` (optional) is a callable over a
    chunk of facts returning that cluster's marks instead of the impact
    ones. Returns ``{"clusters", "lanes", "total_s"}`` with every cluster
    unnormalised (``impact`` 0, ``kind`` quiet); the caller scales."""
    name = _side_name(report, side)
    lane_of, span_agent, lanes = _lanes_of(steps, name)
    reading = ((report.get("reading") or {}).get(side) or {})
    phase_of: dict = {}
    for k, ph in enumerate(reading.get("phases") or []):
        for i in (ph.get("steps") or []) if isinstance(ph, dict) else []:
            phase_of[i] = k
    intent_of: dict = {}
    for w in reading.get("what_happened") or []:
        if isinstance(w, dict) and isinstance(w.get("step"), int):
            intent_of[w["step"]] = w.get("intent")
    groups = _initial_groups(facts, lane_of, phase_of, intent_of)
    groups = _merge_quiet(groups, facts, lane_of)
    groups = _split_long(groups, facts)
    groups = _coalesce(groups, facts, lane_of)
    clusters = [_cluster(f"c{k}", facts[lo:hi], lane_of[lo], span_agent) for k, (lo, hi) in enumerate(groups)]
    if marks is not None:
        for c, (lo, hi) in zip(clusters, groups):
            c["marks"] = marks(facts[lo:hi])
    by_lane = {ln["agent"]: ln for ln in lanes}
    for c in clusters:
        by_lane[c["lane"]]["clusters"].append(c["id"])
    return {"clusters": clusters, "lanes": lanes, "total_s": round(sum(c["seconds"] for c in clusters), 4)}


def _finish(run: dict, name: str, top: float, scale: str) -> None:
    """Set every cluster's impact and kind against ``top`` (the largest
    score on the chosen scale), the run's hot list, its scale and its
    narrative. Scores stay raw; only the normalisation changes."""
    for c in run["clusters"]:
        c["impact"] = round(c["score"] / top, 4) if top > 0 else 0.0
        c["kind"] = "hot" if c["impact"] >= HOT else "work" if c["impact"] >= WORK else "quiet"
    run["hot"] = [c["id"] for c in sorted((c for c in run["clusters"] if c["impact"] >= HOT), key=lambda c: (-c["impact"], c["from"]))[:MAX_HOT]]
    run["scale"] = scale
    run["narrative"] = _run_narrative(name, run, _quiet_share(run))


def _quiet_share(r: dict) -> Optional[float]:
    """The share of the run's wall-clock inside quiet clusters (None when unmeasured)."""
    total = sum(c["seconds"] for c in r["clusters"])
    return sum(c["seconds"] for c in r["clusters"] if c["kind"] == "quiet") / total if total > 0 else None


def _cluster_cite(c: dict) -> str:
    return f"{c['id']} ({c['lane']}, steps {c['from']}–{c['to']}: {c['why']})"


def _run_narrative(name: str, r: dict, quiet_share: Optional[float]) -> str:
    by_id = {c["id"]: c for c in r["clusters"]}
    head = f"{name}: {len(r['clusters'])} cluster{'s' if len(r['clusters']) != 1 else ''} over {r['total_steps']} step{'s' if r['total_steps'] != 1 else ''}"
    head += f" and {_fmt_s(r['total_s'])}" if r["total_s"] > 0 else " (no latency recorded)"
    parts = [head]
    if r["hot"]:
        cites = [_cluster_cite(by_id[cid]) for cid in r["hot"]]
        parts.append(("the hot ones are " if len(cites) > 1 else "the hot one is ") + (", ".join(cites[:-1]) + " and " + cites[-1] if len(cites) > 1 else cites[0]))
    else:
        parts.append("no cluster is hot")
    if quiet_share is not None:
        parts.append(f"{quiet_share:.0%} of the wall-clock sits in quiet clusters")
    return "; ".join(parts) + "."


def impact_pair(report: dict) -> dict:
    """``report["impact"]``: both sides weighed on one scale — impact is
    score over the largest cluster score of either run — and a pair
    narrative. A side is scaled to its own maximum only when the other
    has no steps."""
    a = impact_run(report, "a")
    b = impact_run(report, "b")
    na, nb = _side_name(report, "a"), _side_name(report, "b")
    parts: list = []
    if a["measurable"] and b["measurable"]:
        top = max(c["score"] for r in (a, b) for c in r["clusters"])
        _finish(a, na, top, "pair")
        _finish(b, nb, top, "pair")
        ha, hb = len(a["hot"]), len(b["hot"])
        if ha == hb:
            parts.append(f"on the shared scale both runs have {ha} hot cluster{'s' if ha != 1 else ''}")
        else:
            more, fewer = (na, nb) if ha > hb else (nb, na)
            parts.append(f"on the shared scale {more} has {max(ha, hb)} hot cluster{'s' if max(ha, hb) != 1 else ''} against {min(ha, hb)} for {fewer}")
        for name, r in ((na, a), (nb, b)):
            c = max(r["clusters"], key=lambda c: (c["impact"], -c["from"]))
            parts.append(f"{name}'s hottest is {c['id']} in {c['lane']} (steps {c['from']}–{c['to']}, impact {c['impact']:.2f}: {c['why']})")
        shares = [f"{name} {_quiet_share(r):.0%}" for name, r in ((na, a), (nb, b)) if _quiet_share(r) is not None]
        if shares:
            parts.append("quiet clusters hold " + " and ".join(shares) + " of the wall-clock")
        narrative = "; ".join(parts) + "."
    else:
        narrative = " ".join(r["narrative"] for r in (a, b) if not r["measurable"])
    return {"version": 1, "a": a, "b": b, "narrative": narrative}


__all__ = ["impact_run", "impact_pair", "step_facts", "cluster_steps", "WEIGHTS", "WASTED_CAP", "TIME_WEIGHT", "HOT", "WORK"]
