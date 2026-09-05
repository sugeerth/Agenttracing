"""Router features: which agent to pick for which kind of task, from the
traces. (Agent selection — best single, portfolios, the oracle ceiling —
lives in :mod:`deepcompare.routing`; this module is the per-family
feature table a live router reads.)

A router that chooses a model or an agent per request needs, per task
family, what each candidate actually did on that family: how often it
succeeded (with an interval, not a point), what it cost, how long it
took, how many steps and tool calls it spent, and — when reports are at
hand — what kind of fault it tends to make. This module computes those
features from trajectories and turns them into a pick per family under a
stated objective, with the evidence beside it.

Honesty rules. The pick is by the *lower bound* of the Wilson interval
on the success rate, so a candidate with one lucky run never outranks
one with ten solid ones; ties go to the cheaper. A family with fewer
than three runs per candidate is marked ``insufficient``; one whose top
two intervals overlap is marked ``overlapping`` and lists both — a
router should treat those as "either" or gather more runs, not as a
verdict. Nothing is estimated beyond the interval; every number is a
mean or a count over the runs listed.
"""

from __future__ import annotations

import re
from typing import Optional

from .statistics import wilson_interval
from .trace import Trajectory

OBJECTIVES = ("success", "cost", "latency", "steps")
MIN_RUNS = 3


def family_of(task_id: str, pattern: Optional[str] = None) -> str:
    """The task family a task id belongs to. Default: the id with a
    trailing run/number suffix removed and the ``tNN_`` prefix kept, so
    ``t05_flight_duration`` is its own family; ``--family-pattern`` can
    name a regex whose first group is the family."""
    if pattern:
        m = re.search(pattern, task_id)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return re.sub(r"(__|[-_])r?\d+$", "", task_id) or task_id


def _features(trajs: list) -> dict:
    n = len(trajs)
    succ = sum(1 for t in trajs if t.outcome.success is True)
    lo, hi = wilson_interval(succ, n) if n else (0.0, 1.0)
    cost = [t.totals.cost_usd for t in trajs if t.totals and t.totals.cost_usd is not None]
    lat = [t.totals.latency_s for t in trajs if t.totals and t.totals.latency_s is not None]
    steps = [len(t.steps) for t in trajs]
    tools = [sum(1 for s in t.steps if s.type in ("tool_call", "search", "retrieve", "read")) for t in trajs]
    tokens = [(t.totals.input_tokens or 0) + (t.totals.output_tokens or 0) for t in trajs if t.totals]
    terms: dict = {}
    for t in trajs:
        term = t.outcome.termination or "undeclared"
        terms[term] = terms.get(term, 0) + 1
    return {
        "n": n, "successes": succ, "rate": round(succ / n, 4) if n else None,
        "ci95": [round(lo, 4), round(hi, 4)],
        "cost_usd": round(sum(cost) / len(cost), 6) if cost else None,
        "latency_s": round(sum(lat) / len(lat), 3) if lat else None,
        "tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
        "steps": round(sum(steps) / len(steps), 2) if steps else None,
        "tool_calls": round(sum(tools) / len(tools), 2) if tools else None,
        "terminations": terms,
    }


def _rank_key(objective: str):
    def key(row: dict):
        f = row["features"]
        cost = f["cost_usd"] if f["cost_usd"] is not None else float("inf")
        lat = f["latency_s"] if f["latency_s"] is not None else float("inf")
        steps = f["steps"] if f["steps"] is not None else float("inf")
        if objective == "cost":
            return (-(f["ci95"][0] >= 0.5), cost, -f["ci95"][0])
        if objective == "latency":
            return (-(f["ci95"][0] >= 0.5), lat, -f["ci95"][0])
        if objective == "steps":
            return (-(f["ci95"][0] >= 0.5), steps, -f["ci95"][0])
        return (-f["ci95"][0], -(f["rate"] or 0), cost, lat)
    return key


def routing_table(trajectories: list, *, objective: str = "success", family_pattern: Optional[str] = None,
                  reports: Optional[list] = None) -> dict:
    """Per family, every candidate's features and the pick under
    ``objective`` (``success`` — highest lower-bound success, then
    cheaper; ``cost``/``latency``/``steps`` — cheapest/fastest/shortest
    among candidates whose lower-bound success is at least one half)."""
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {', '.join(OBJECTIVES)}")
    by_family: dict = {}
    for t in trajectories:
        fam = family_of(t.task.id, family_pattern)
        by_family.setdefault(fam, {}).setdefault(t.agent.name, []).append(t)
    kinds: dict = {}
    for rep in reports or []:
        diag = rep.get("diagnosis") or {}
        side = diag.get("subject")
        lead = (diag.get("hypotheses") or [{}])[0] if diag.get("hypotheses") else {}
        if side in ("a", "b") and lead.get("kind"):
            agent = ((rep.get(side) or {}).get("agent") or {}).get("name")
            fam = family_of(((rep.get("task") or {}).get("id") or ""), family_pattern)
            kinds.setdefault(fam, {}).setdefault(agent, {})
            kinds[fam][agent][lead["kind"]] = kinds[fam][agent].get(lead["kind"], 0) + 1

    families = {}
    for fam in sorted(by_family):
        rows = []
        for agent in sorted(by_family[fam]):
            feats = _features(by_family[fam][agent])
            rows.append({"agent": agent, "features": feats,
                         "fault_kinds": kinds.get(fam, {}).get(agent, {})})
        rows.sort(key=_rank_key(objective))
        pick = rows[0]["agent"] if rows else None
        confidence = "clear"
        why = ""
        if any(r["features"]["n"] < MIN_RUNS for r in rows):
            confidence = "insufficient"
            why = f"fewer than {MIN_RUNS} runs for at least one candidate"
        elif len(rows) >= 2:
            a, b = rows[0]["features"], rows[1]["features"]
            if objective == "success" and a["ci95"][0] <= b["ci95"][1] and b["ci95"][0] <= a["ci95"][1]:
                confidence = "overlapping"
                why = (f"{rows[0]['agent']} {a['rate']:.0%} [{a['ci95'][0]:.2f}–{a['ci95'][1]:.2f}] and "
                       f"{rows[1]['agent']} {b['rate']:.0%} [{b['ci95'][0]:.2f}–{b['ci95'][1]:.2f}] overlap")
        if not why and rows:
            f = rows[0]["features"]
            why = (f"lower-bound success {f['ci95'][0]:.2f} over {f['n']} run(s)" if objective == "success"
                   else f"{objective} {f[{'cost': 'cost_usd', 'latency': 'latency_s', 'steps': 'steps'}[objective]]} with lower-bound success {f['ci95'][0]:.2f}")
        families[fam] = {"pick": pick, "confidence": confidence, "why": why,
                         "either": [r["agent"] for r in rows[:2]] if confidence == "overlapping" else None,
                         "candidates": rows}
    overall = _features_overall(trajectories, objective)
    return {
        "version": 1, "objective": objective, "min_runs": MIN_RUNS,
        "families": families,
        "overall": overall,
        "note": ("picks rank by the lower bound of the 95% Wilson interval on success, then cost; "
                 "'overlapping' means the top two intervals overlap — treat as either, or gather runs; "
                 "'insufficient' means a candidate has fewer than three runs"),
    }


def _features_overall(trajectories: list, objective: str) -> dict:
    by_agent: dict = {}
    for t in trajectories:
        by_agent.setdefault(t.agent.name, []).append(t)
    rows = [{"agent": a, "features": _features(ts), "fault_kinds": {}} for a, ts in sorted(by_agent.items())]
    rows.sort(key=_rank_key(objective))
    return {"pick": rows[0]["agent"] if rows else None, "candidates": rows}


def router_hints(table: dict) -> list:
    """One line per family a router can act on."""
    out = []
    for fam, row in (table.get("families") or {}).items():
        if row["confidence"] == "clear":
            out.append({"family": fam, "route_to": row["pick"], "basis": row["why"]})
        elif row["confidence"] == "overlapping":
            out.append({"family": fam, "route_to": row["either"], "basis": "either — " + row["why"]})
        else:
            out.append({"family": fam, "route_to": None, "basis": "gather more runs — " + row["why"]})
    return out
