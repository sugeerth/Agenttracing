"""Long horizons: a run subdivided, and its sub-agents.

A run of three hundred steps is not read step by step. This module
folds it into a tree a reader can open on demand: the run, the
delegation spans inside it (which agent acted — sub-agents nest
through their parent span), the *subdivisions* within each span (a
stretch of steps in one phase of the reading, split again wherever the
agent stopped to frame or decide), and the steps. Every node carries
its steps, seconds, tokens, tool calls, wasted seconds (the reading's
verdict), errors, whether the fault's path runs through it, whether the
decisive step is inside, and the answer values it produced — so a
subdivision can be judged without opening it. The summary names how
many subdivisions there were, which took the time, what each sub-agent
cost and returned, and where the fault entered.
"""

from __future__ import annotations

from typing import Optional

from .timing import time_attribution
from .trace import Trajectory

TOOLISH = ("tool_call", "search", "retrieve", "read")
BOUNDARY_INTENTS = ("frame", "decide", "plan")


def _fmt_s(v: float) -> str:
    return f"{v:.1f}s" if v >= 1 else f"{v:.2f}s"


def _node(kind: str, key: str, label: str, steps: list, rows: dict, fault: set, decisive: Optional[int], values: dict, agent: Optional[str]) -> dict:
    secs = sum(rows[i]["latency_s"] for i in steps if i in rows)
    wasted = sum(rows[i]["latency_s"] for i in steps if i in rows and rows[i]["wasted"])
    tools: dict = {}
    for i in steps:
        r = rows.get(i)
        if r and r["category"] == "tool":
            tools[r["name"] or "?"] = tools.get(r["name"] or "?", 0) + 1
    top = max(tools, key=lambda k: (tools[k], k)) if tools else None
    return {
        "kind": kind, "key": key, "label": label, "agent": agent,
        "from": steps[0] if steps else None, "to": steps[-1] if steps else None, "count": len(steps),
        "seconds": round(secs, 4), "wasted_s": round(wasted, 4),
        "wasted_steps": sum(1 for i in steps if i in rows and rows[i]["wasted"]),
        "tokens": sum(int(rows[i]["tokens"] or 0) for i in steps if i in rows and isinstance(rows[i]["tokens"], (int, float))),
        "tool_calls": sum(tools.values()), "tools": tools, "top_tool": top,
        "errors": sum(1 for i in steps if i in rows and rows[i]["wasted"] == "error"),
        "fault": any(i in fault for i in steps), "decisive": decisive in steps if decisive is not None else False,
        "values": [v for i in steps for v in values.get(i, [])],
        "children": [],
    }


def segment(traj: Trajectory, reading: Optional[dict] = None, *, fault_steps: Optional[set] = None,
            decisive_step: Optional[int] = None) -> dict:
    reading = reading or {}
    timing = time_attribution(traj, reading)
    rows = {r["index"]: r for r in timing.get("steps", [])}
    for st in traj.steps:            # unmeasurable runs still get a tree, with zero seconds
        rows.setdefault(st.index, {"index": st.index, "name": st.name, "type": st.type, "latency_s": 0.0, "share": 0.0,
                                   "category": "answer" if st.type == "answer" else "tool" if st.type in TOOLISH else "think",
                                   "wasted": None, "tokens": st.tokens})
    phase_of, intent_of = {}, {}
    for ph in reading.get("phases") or []:
        for i in ph.get("steps") or []:
            phase_of[i] = ph.get("intent")
    for w in reading.get("what_happened") or []:
        if isinstance(w, dict) and isinstance(w.get("step"), int):
            intent_of[w["step"]] = w.get("intent")
    values: dict = {}
    for r in reading.get("rests_on") or []:
        if isinstance(r, dict) and isinstance(r.get("first_step"), int):
            values.setdefault(r["first_step"], []).append({"value": str(r.get("value")), "status": r.get("status"), "matches_expected": r.get("matches_expected")})
    fault = set(fault_steps or [])

    # --- spans: which agent acted, nested by parent
    spans: dict = {}
    order: list = []
    root_agent = traj.agent.name
    for st in traj.steps:
        sp = st.span or {"id": "root", "agent": root_agent, "parent": None}
        sid = sp["id"] if st.span else "root"
        if sid not in spans:
            spans[sid] = {"id": sid, "agent": sp["agent"], "parent": (sp.get("parent") or ("root" if st.span else None)), "steps": []}
            order.append(sid)
        spans[sid]["steps"].append(st.index)
    if "root" not in spans:
        spans["root"] = {"id": "root", "agent": root_agent, "parent": None, "steps": []}
        order.insert(0, "root")
    for sid, sp in spans.items():
        if sp["parent"] not in spans and sid != "root":
            sp["parent"] = "root"

    # --- subdivisions within a span: phase runs, split again at frame/decide steps
    def episodes(step_ids: list) -> list:
        out: list = []
        cur: list = []
        cur_phase = None
        for i in step_ids:
            ph = phase_of.get(i)
            leads = intent_of.get(i) in BOUNDARY_INTENTS or rows.get(i, {}).get("type") == "plan"
            # a new subdivision opens where the phase changes, or where the
            # agent stops to frame or decide after at least two steps of work;
            # the framing step leads the subdivision it opens
            gap = bool(cur) and i != cur[-1] + 1          # a child span sits between: never bridge it
            boundary = bool(cur) and (gap or (ph != cur_phase and not _lone_boundary(cur)) or (leads and len(cur) >= 2))
            if boundary:
                out.append((_majority_phase(cur), cur))
                cur = []
            cur.append(i)
            cur_phase = ph
        if cur:
            out.append((_majority_phase(cur), cur))
        return out

    def _lone_boundary(cur: list) -> bool:
        # a subdivision that so far holds only its framing step stays open
        return len(cur) == 1 and (intent_of.get(cur[0]) in BOUNDARY_INTENTS or rows.get(cur[0], {}).get("type") == "plan")

    def _majority_phase(ids: list):
        counts: dict = {}
        for i in ids:
            ph = phase_of.get(i)
            if ph and ph not in BOUNDARY_INTENTS or len(ids) == 1:
                counts[ph] = counts.get(ph, 0) + 1
        if not counts:
            for i in ids:
                counts[phase_of.get(i)] = counts.get(phase_of.get(i), 0) + 1
        return max(counts, key=lambda k: (counts[k], str(k))) if counts else None

    def build_span(sid: str) -> dict:
        sp = spans[sid]
        own = sorted(sp["steps"])
        children_spans = [s for s in order if spans[s]["parent"] == sid and s != sid]
        # the span's own steps, interleaved in step order with its child spans
        all_steps = sorted(own + [i for c in children_spans for i in _all_steps(c)])
        node = _node("span", f"span:{sid}", sp["agent"], all_steps, rows, fault, decisive_step, values, sp["agent"])
        node["own_steps"] = len(own)
        node["delegations"] = len(children_spans)
        kids: list = []
        for ph, ids in episodes(own):
            ep = _node("episode", f"ep:{sid}:{ids[0]}", (ph or "steps") + (f" · {rows[ids[0]]['name']}" if len(ids) == 1 and rows[ids[0]]["name"] else ""),
                       ids, rows, fault, decisive_step, values, sp["agent"])
            ep["phase"] = ph
            if ep["top_tool"] and ep["count"] > 1:
                ep["label"] = f"{ph or 'steps'} · {ep['top_tool']} ×{ep['tools'][ep['top_tool']]}"
            ep["children"] = [_node("step", f"step:{i}", rows[i]["name"] or rows[i]["type"], [i], rows, fault, decisive_step, values, sp["agent"]) for i in ids]
            for ch in ep["children"]:
                ch["type"] = rows[ch["from"]]["type"]
                ch["wasted"] = rows[ch["from"]]["wasted"]
                ch["wasted_label"] = rows[ch["from"]].get("wasted_label")
                ch["children"] = []
            kids.append(ep)
        for c in children_spans:
            kids.append(build_span(c))
        kids.sort(key=lambda n: (n["from"] if n["from"] is not None else -1))
        node["children"] = kids
        return node

    def _all_steps(sid: str) -> list:
        return spans[sid]["steps"] + [i for c in order if spans[c]["parent"] == sid and c != sid for i in _all_steps(c)]

    root = build_span("root")
    root["kind"] = "run"
    root["key"] = "run"
    root["label"] = root_agent
    # --- the summary
    eps = []
    def collect(n):
        if n["kind"] == "episode":
            eps.append(n)
        for c in n["children"]:
            collect(c)
    collect(root)
    total = root["seconds"] or 0.0
    agents: dict = {}
    for sid in order:
        if sid == "root":
            continue
        sp = spans[sid]
        a = agents.setdefault(sp["agent"], {"agent": sp["agent"], "delegations": 0, "steps": 0, "seconds": 0.0, "wasted_s": 0.0, "errors": 0})
        n = _node("span", sid, sp["agent"], sorted(_all_steps(sid)), rows, fault, decisive_step, values, sp["agent"])
        a["delegations"] += 1
        a["steps"] += n["count"]
        a["seconds"] = round(a["seconds"] + n["seconds"], 4)
        a["wasted_s"] = round(a["wasted_s"] + n["wasted_s"], 4)
        a["errors"] += n["errors"]
    longest = max(eps, key=lambda e: e["seconds"]) if eps else None
    fault_ep = next((e for e in eps if e["fault"]), None)
    dec_ep = next((e for e in eps if e["decisive"]), None)
    parts = [f"{root_agent}: {len(traj.steps)} step(s) in {len(eps)} subdivision(s)" + (f" across {len(agents)} sub-agent(s)" if agents else "")]
    if longest and total:
        parts.append(f"the longest, '{longest['label']}' (steps {longest['from']}–{longest['to']}), took {_fmt_s(longest['seconds'])} — {longest['seconds'] / total:.0%} of the run"
                     + (f", {longest['wasted_s'] / longest['seconds']:.0%} of it wasted" if longest["seconds"] and longest["wasted_s"] else ""))
    for a in sorted(agents.values(), key=lambda x: -x["seconds"]):
        parts.append(f"sub-agent {a['agent']}: {a['delegations']} delegation(s), {a['steps']} step(s), {_fmt_s(a['seconds'])}"
                     + (f" of which {_fmt_s(a['wasted_s'])} wasted" if a["wasted_s"] else "") + (f", {a['errors']} error(s)" if a["errors"] else ""))
    if dec_ep:
        parts.append(f"the decisive step is inside '{dec_ep['label']}' (steps {dec_ep['from']}–{dec_ep['to']})" + (f", the work of sub-agent {dec_ep['agent']}" if dec_ep["agent"] != root_agent else ""))
    elif fault_ep:
        parts.append(f"the fault's path begins in '{fault_ep['label']}' (steps {fault_ep['from']}–{fault_ep['to']})")
    return {
        "measurable": timing["measurable"], "total_s": round(total, 4), "steps": len(traj.steps),
        "subdivisions": len(eps), "spans": len(spans), "agents": sorted(agents.values(), key=lambda x: -x["seconds"]),
        "depth": _depth(root), "tree": root, "summary": "; ".join(parts) + ".",
        "basis": ("subdivisions = the reading's phases, split again wherever the agent framed or decided; spans = step.span as recorded "
                  "(none recorded means the root agent acted throughout); seconds as recorded, wasted per the reading"),
    }


def delegation_graph(h: dict) -> dict:
    """The aggregated view of a run's delegations (what a graph view of an
    agent framework shows for one run): every agent once, with how often it
    was delegated to and what it cost; every delegation edge parent → child
    with its count. Built from the horizon tree."""
    nodes: dict = {}
    edges: dict = {}

    def visit(n: dict, parent_agent: Optional[str]) -> None:
        if n["kind"] in ("run", "span"):
            a = nodes.setdefault(n["agent"], {"agent": n["agent"], "delegations": 0, "steps": 0, "seconds": 0.0, "wasted_s": 0.0,
                                               "errors": 0, "tool_calls": 0, "fault": False, "decisive": False, "root": n["kind"] == "run"})
            if n["kind"] == "span":
                a["delegations"] += 1
                key = f"{parent_agent}→{n['agent']}"
                e = edges.setdefault(key, {"from": parent_agent, "to": n["agent"], "count": 0, "seconds": 0.0})
                e["count"] += 1
                e["seconds"] = round(e["seconds"] + n["seconds"], 4)
            own = [c for c in n["children"] if c["kind"] != "span"]
            a["steps"] += sum(c["count"] for c in own)
            a["seconds"] = round(a["seconds"] + sum(c["seconds"] for c in own), 4)
            a["wasted_s"] = round(a["wasted_s"] + sum(c["wasted_s"] for c in own), 4)
            a["errors"] += sum(c["errors"] for c in own)
            a["tool_calls"] += sum(c["tool_calls"] for c in own)
            a["fault"] = a["fault"] or any(c["fault"] for c in own)
            a["decisive"] = a["decisive"] or any(c["decisive"] for c in own)
            for c in n["children"]:
                if c["kind"] == "span":
                    visit(c, n["agent"])
    visit(h["tree"], None)
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def blame(h: dict) -> Optional[dict]:
    """Which agent, which step: the decisive step's agent, the span it
    worked in, who delegated it, and how deep — the Who&When shape,
    derived from the diagnosis's decisive step, not guessed."""
    path: list = []

    def find(n: dict, trail: list) -> Optional[dict]:
        trail = trail + [n]
        if n["kind"] == "step" and n["decisive"]:
            return {"trail": trail}
        for c in n["children"]:
            hit = find(c, trail)
            if hit:
                return hit
        return None
    hit = find(h["tree"], path)
    if not hit:
        return None
    trail = hit["trail"]
    spans = [n for n in trail if n["kind"] in ("run", "span")]
    step = trail[-1]
    part = next((n for n in reversed(trail) if n["kind"] == "episode"), None)
    agent_node = spans[-1]
    delegator = spans[-2]["agent"] if len(spans) >= 2 else None
    return {"agent": agent_node["agent"], "step": step["from"], "step_name": step["label"], "span": agent_node["key"],
            "delegated_by": delegator, "depth": len(spans) - 1, "part": part["label"] if part else None,
            "chain": [n["agent"] for n in spans],
            "sentence": ((f"The decisive step is inside sub-agent {agent_node['agent']} (delegated by {delegator}"
                          + (f", {len(spans) - 1} levels deep" if len(spans) > 2 else "") + f"): step {step['from']}, {step['label']}"
                          if delegator else
                          f"The decisive step is {agent_node['agent']}'s own step {step['from']}, {step['label']} — not a sub-agent's")
                         + (f", in the part '{part['label']}'" if part else "") + ".")}


def graph_diff(ga: dict, gb: dict, labels=("a", "b")) -> dict:
    """Two runs' delegation graphs, aligned by agent name: every node and
    edge marked as in both, only in the first, or only in the second, with
    the deltas — the diff of how two runs organised their work."""
    # the two root agents are the same role under different names: aligned as "root"
    def canon(g: dict):
        root = next((n["agent"] for n in g["nodes"] if n.get("root")), None)
        key = lambda name: "root" if name == root else name  # noqa: E731
        return ({key(n["agent"]): n for n in g["nodes"]},
                {f"{key(e['from'])}→{key(e['to'])}": dict(e, from_key=key(e["from"]), to_key=key(e["to"])) for e in g["edges"]})
    na, ea = canon(ga)
    nb, eb = canon(gb)
    nodes = []
    for agent in sorted(set(na) | set(nb), key=lambda k: (k != "root", k)):
        a, b = na.get(agent), nb.get(agent)
        nodes.append({"agent": agent, "in": "both" if a and b else labels[0] if a else labels[1], "a": a, "b": b,
                      "delta": {k: round((b or {}).get(k, 0) - (a or {}).get(k, 0), 4) for k in ("delegations", "steps", "seconds", "wasted_s", "errors")} if a and b else None})
    edges = []
    for key in sorted(set(ea) | set(eb)):
        a, b = ea.get(key), eb.get(key)
        edges.append({"from": (a or b)["from_key"], "to": (a or b)["to_key"], "in": "both" if a and b else labels[0] if a else labels[1],
                      "count_a": a["count"] if a else 0, "count_b": b["count"] if b else 0})
    only_a = [n["agent"] for n in nodes if n["in"] == labels[0]]
    only_b = [n["agent"] for n in nodes if n["in"] == labels[1]]
    uneven = [e for e in edges if e["in"] == "both" and e["count_a"] != e["count_b"]]
    return {"nodes": nodes, "edges": edges, "only_a": only_a, "only_b": only_b, "uneven": uneven,
            "same_agents": not only_a and not only_b and all(e["in"] == "both" for e in edges),
            "same_shape": not only_a and not only_b and all(e["in"] == "both" for e in edges) and not uneven}


def _depth(n: dict) -> int:
    return 1 + max((_depth(c) for c in n.get("children") or []), default=0)


def horizon_pair(report: dict, a: Trajectory, b: Trajectory) -> dict:
    reading = report.get("reading") or {}
    diag = report.get("diagnosis") or {}
    dec = diag.get("decisive_step") or {}
    subject = diag.get("subject")
    fault = {"a": set(), "b": set()}
    for side in ("a", "b"):
        acc = ((report.get("attribution") or {}).get("chain") or []) if (report.get("attribution") or {}).get("failed_agent") == side else []
        fault[side] = set(i for i in acc if isinstance(i, int))
    out = {"a": segment(a, reading.get("a"), fault_steps=fault["a"], decisive_step=dec.get("step") if subject == "a" else None),
           "b": segment(b, reading.get("b"), fault_steps=fault["b"], decisive_step=dec.get("step") if subject == "b" else None)}
    for side in ("a", "b"):
        out[side]["graph"] = delegation_graph(out[side])
        out[side]["blame"] = blame(out[side])
    out["diff"] = graph_diff(out["a"]["graph"], out["b"]["graph"], labels=(a.agent.name, b.agent.name))
    d = out["diff"]
    failing = subject if subject in ("a", "b") else None
    bl = out[failing]["blame"] if failing else None
    def who(name: str, side_name: str) -> str:
        return side_name if name == "root" else name

    def times(n: int) -> str:
        return "once" if n == 1 else "twice" if n == 2 else f"{n} times"
    uneven_text = "; ".join(
        f"{who(e['from'], a.agent.name)} delegated to {e['to']} {times(e['count_a'])}, {who(e['from'], b.agent.name)} {times(e['count_b'])}"
        for e in d["uneven"])
    out["narrative"] = (
        (f"{bl['sentence']} " if bl else "")
        + ("Both runs organised the work the same way: the same agents, delegated the same number of times." if d["same_shape"]
           else " ".join(x for x in [
               ("Both runs used the same agents" + (f", but {uneven_text}." if d["uneven"] else ".")) if d["same_agents"] else "",
               f"Only {a.agent.name} used {', '.join(d['only_a'])}." if d["only_a"] else "",
               f"Only {b.agent.name} used {', '.join(d['only_b'])}." if d["only_b"] else "",
               (uneven_text[0].upper() + uneven_text[1:] + ".") if d["uneven"] and not d["same_agents"] else ""] if x))
    ).strip()
    return out


__all__ = ["segment", "horizon_pair", "delegation_graph", "blame", "graph_diff"]
