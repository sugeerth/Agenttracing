"""From a trace to a vector: any run AgentDiff can read, compacted in-band.

Every adapter AgentDiff has (Claude Code, Codex, OpenTelemetry, the
framework converters, the Recorder) ends in a SCHEMA trajectory. This
turns one into the same telemetry vector a probe stamps, so a run traced
the heavy way can travel, be posted to a hub, and be read on the same
page as one traced in-band. Content is dropped, as it always is in-band;
the sizes of inputs and outputs stay.
"""

from __future__ import annotations

import secrets
import time
from typing import Iterable

from .fields import DEFAULT_INSTRUCTIONS, FIELDS, Registry
from .probe import size_of
from .wire import CLAMPED, Vector, name_hash

__all__ = ["from_trajectory"]


def from_trajectory(traj: dict, *, instructions: Iterable[str] = DEFAULT_INSTRUCTIONS + ("effect", "attempt", "span"),
                    node: str = "trace", registry: Registry = FIELDS) -> Vector:
    steps = [s for s in traj.get("steps") or [] if s.get("type") != "answer"]
    names: dict = {}

    def h(text: str) -> int:
        k = name_hash(text)
        names[k] = text
        return k
    v = Vector(trace_id=secrets.token_bytes(8), instructions=registry.bitmap(instructions),
               t0=int(time.time() * 1000), agent=h((traj.get("agent") or {}).get("name") or "agent"),
               task=h((traj.get("task") or {}).get("id") or "task"), remaining=0, names=names)
    clock = 0.0
    for s in steps:
        start = s.get("started_s")
        start = float(start) if isinstance(start, (int, float)) else clock
        latency = float(s.get("latency_s") or 0.0)
        clock = max(clock, start + latency)
        tin, tout = s.get("input_tokens"), s.get("output_tokens")
        if tin is None and tout is None:
            tin, tout = 0, int(s.get("tokens") or 0)
        span = (s.get("span") or {}).get("agent")
        values = {"tool": h(str(s.get("name") or s.get("type") or "step")), "start": start, "latency": latency,
                  "status": "error" if s.get("error") else "ok", "kind": s.get("type") or "tool_call",
                  "bytes_in": size_of(s.get("input")), "bytes_out": size_of(s.get("output")),
                  "tokens_in": tin or 0, "tokens_out": tout or 0, "node": h(node),
                  "effect": s.get("effect"), "attempt": s.get("attempt") or 0,
                  "span": h(span) if span else 0}
        words = []
        for _, f in registry.selected(v.instructions):
            if f is None:
                words.append(0)
                continue
            word, clamped = f.word(values.get(f.name))
            if clamped:
                v.flags |= CLAMPED
            words.append(word)
        v.hops.append(words)
    v.remaining = max(0, 0xFFFF - len(v.hops))
    return v
