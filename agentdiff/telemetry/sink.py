"""The end of the path: a vector read back as hops, and as a trace.

The sink is where in-band telemetry is taken off the packet. Here it
becomes two things: rows, one per hop, for a table or a page; and a SCHEMA
trajectory, so everything else AgentDiff reads (batch, duel, forge, the
page) reads a run that was only ever traced in-band.

What the vector never carried is never invented. Inputs and outputs are
sizes, not text; a name the vector left out is its hash; hops refused for
the hop budget, and values clamped to a word, are stated, not hidden.
"""

from __future__ import annotations

from typing import List, Optional, Union

from ..trace import STEP_TYPES, Trajectory
from .fields import FIELDS, Registry
from .wire import Vector, decode, from_text

__all__ = ["read", "rows", "to_trajectory", "summary"]


def read(data: Union[str, bytes, Vector]) -> Vector:
    if isinstance(data, Vector):
        return data
    if isinstance(data, str):
        return decode(from_text(data))
    return decode(bytes(data))


def _label(v: Vector, h: int) -> Optional[str]:
    if not h:
        return None
    return v.names.get(h, f"#{h:08x}")


def rows(data: Union[str, bytes, Vector], registry: Registry = FIELDS) -> List[dict]:
    """Every hop as ``{field: value}``, names resolved, in start order."""
    v = read(data)
    out = []
    selected = registry.selected(v.instructions)
    for i, words in enumerate(v.hops):
        row: dict = {"hop": i}
        for (bit, f), word in zip(selected, words):
            if f is None:
                row.setdefault("unknown", {})[bit] = word
            elif f.name in ("tool", "node", "span"):
                row[f.name] = _label(v, word)
            else:
                row[f.name] = f.decode(word)
        out.append(row)
    return sorted(out, key=lambda r: (r.get("start") or 0.0, r["hop"]))


def summary(data: Union[str, bytes, Vector], registry: Registry = FIELDS) -> dict:
    v = read(data)
    rs = rows(v, registry)
    ends = [(r.get("start") or 0.0) + (r.get("latency") or 0.0) for r in rs]
    return {
        "format": "agentdiff-int/1",
        "trace_id": v.trace_id.hex(),
        "agent": _label(v, v.agent), "task": _label(v, v.task),
        "hops": len(rs), "dropped": v.dropped, "overflowed": v.overflowed, "clamped": v.clamped,
        "remaining": v.remaining,
        "fields": [f.name if f else f"bit {b}" for b, f in registry.selected(v.instructions)],
        "nodes": sorted({r["node"] for r in rs if r.get("node")}),
        "tools": sorted({r["tool"] for r in rs if r.get("tool")}),
        "errors": sum(1 for r in rs if r.get("status") not in (None, "ok")),
        "span_s": round(max(ends, default=0.0), 6),
    }


def to_trajectory(data: Union[str, bytes, Vector], *, prompt: str = "", success: Optional[bool] = None,
                  answer: str = "", expected: Optional[str] = None, model: str = "",
                  registry: Registry = FIELDS) -> dict:
    """A SCHEMA trajectory of the run the vector traced, validated."""
    v = read(data)
    rs = rows(v, registry)
    has = {f.name for _, f in registry.selected(v.instructions) if f}
    steps, tin, tout, cost = [], 0, 0, 0.0
    for r in rs:
        step = {"index": len(steps), "type": r.get("kind") if r.get("kind") in STEP_TYPES else "tool_call",
                "name": r.get("tool") or "tool", "input": "", "output": "",
                "tokens": int((r.get("tokens_in") or 0) + (r.get("tokens_out") or 0)),
                "latency_s": float(r.get("latency") or 0.0)}
        if "start" in has:
            step["started_s"] = float(r.get("start") or 0.0)
        if "tokens_in" in has and "tokens_out" in has:
            step["input_tokens"], step["output_tokens"] = int(r.get("tokens_in") or 0), int(r.get("tokens_out") or 0)
        if r.get("status") not in (None, "ok"):
            step["error"] = True
        if r.get("effect") in ("read", "write"):
            step["effect"] = r["effect"]
        if r.get("attempt"):
            step["attempt"] = int(r["attempt"])
        if r.get("span"):
            step["span"] = {"id": r["span"], "agent": r["span"]}
        notes = []
        if r.get("status") not in (None, "ok"):
            notes.append(str(r["status"]))
        if "bytes_in" in r or "bytes_out" in r:
            notes.append(f"{r.get('bytes_in', 0):,} bytes in, {r.get('bytes_out', 0):,} out")
        if r.get("node"):
            notes.append(f"stamped by {r['node']}")
        if r.get("wait"):
            notes.append(f"waited {r['wait']:.3f}s")
        step["note"] = "; ".join(notes) or None
        tin += int(r.get("tokens_in") or 0)
        tout += int(r.get("tokens_out") or 0)
        cost += float(r.get("cost") or 0.0)
        steps.append(step)
    ends = [s.get("started_s", 0.0) + s["latency_s"] for s in steps]
    steps.append({"index": len(steps), "type": "answer", "name": "answer", "input": "", "output": answer,
                  "tokens": 0, "latency_s": 0.0, "note": None})
    info = summary(v, registry)
    outcome_note = None if success is not None else "the outcome was not reported to the sink"
    traj = {
        "schema_version": 1,
        "trace_id": f"int-{info['trace_id']}",
        "agent": {"name": info["agent"] or "agent", "model": model, "version": ""},
        "task": {"id": info["task"] or "task", "prompt": prompt, "expected": expected},
        "outcome": {"success": bool(success), "answer": answer, "score": None,
                    **({"note": outcome_note} if outcome_note else {})},
        "totals": {"input_tokens": tin, "output_tokens": tout, "cost_usd": round(cost, 6),
                   "latency_s": round(max(ends, default=0.0), 6)},
        "steps": steps,
        "source": {"format": "agentdiff-int", "hops": info["hops"], "dropped": info["dropped"],
                   "overflowed": info["overflowed"], "clamped": info["clamped"], "fields": info["fields"],
                   "nodes": info["nodes"],
                   "note": "traced in-band: sizes, times and outcomes per hop, never the content"},
    }
    Trajectory.from_dict(traj)
    return traj

