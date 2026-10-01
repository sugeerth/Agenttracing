"""Stamping hops: what an agent, or a tool it calls, uses to add telemetry.

A :class:`Probe` holds one vector and appends a hop for each call made
through it::

    probe = Probe(agent="mine", task="t1")
    with probe.hop("search", kind="search", args=query) as hop:
        result = search(query)
        hop.result(result)                 # its size, never its content
        hop.tokens(prompt=812, completion=64)

    @probe.tool                            # a hop per call, named after the function
    def read_file(path): ...

Each hop records only what the vector's instructions ask for (fields.py),
measured on the probe's clock. A probe is safe across threads: hops that
run at the same time each keep their own start, and the sink places them
on one clock.

:class:`NullProbe` has the same methods and records nothing, so a tool
instrumented once runs the same with telemetry or without it.
"""

from __future__ import annotations

import functools
import json
import os
import platform
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, List, Optional, Protocol

from .fields import DEFAULT_INSTRUCTIONS, FIELDS, Registry, STATUSES
from .wire import CLAMPED, OVERFLOW, Vector, WireError, decode, encode, from_text, name_hash, to_text

__all__ = ["Probe", "NullProbe", "Hop", "Handoff", "Clock", "SystemClock", "size_of", "DEFAULT_MAX_HOPS"]

#: hops a vector takes before it refuses more (and says so)
DEFAULT_MAX_HOPS = 4096


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def wall(self) -> float: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()


def size_of(value: Any) -> int:
    """Bytes a value takes as text: what a hop records instead of the value."""
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, default=str, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


@dataclass(frozen=True)
class Handoff:
    """The vector as handed to a callee (a subprocess, a service), and where
    the callee's hops will begin, so they can be taken back without losing
    hops this side added meanwhile."""

    text: str
    base: int
    dropped: int


def _default_node() -> str:
    return f"{platform.node() or 'host'}/{os.getpid()}"


class Hop:
    """One call in flight. Values set on it are written when it ends."""

    def __init__(self, probe: "Probe", tool: str, kind: str, values: dict) -> None:
        self._probe = probe
        self.tool = tool
        self.values = {"kind": kind, "status": "ok", **values}
        self._t = probe._clock.monotonic()
        self.values["start"] = probe._offset(self._t)

    def result(self, value: Any) -> "Hop":
        return self.output_bytes(size_of(value))

    def output_bytes(self, n: int) -> "Hop":
        """The result's size when the hop counted it itself (a stream)."""
        self.values["bytes_out"] = int(n)
        return self

    def tokens(self, prompt: int = 0, completion: int = 0) -> "Hop":
        self.values["tokens_in"] = int(prompt or 0)
        self.values["tokens_out"] = int(completion or 0)
        return self

    def cost(self, usd: float) -> "Hop":
        self.values["cost"] = float(usd or 0.0)
        return self

    def fail(self, status: str = "error") -> "Hop":
        if status not in STATUSES:
            raise ValueError(f"status {status!r} is not one of {', '.join(STATUSES)}")
        self.values["status"] = status
        return self

    def set(self, field: str, value: Any) -> "Hop":
        """Any registered field by name: the way to fill a field a deployment added."""
        self._probe.registry.get(field)
        self.values[field] = value
        return self

    def _finish(self) -> None:
        self.values["latency"] = max(0.0, self._probe._clock.monotonic() - self._t)
        self._probe._append(self.tool, self.values)


class Probe:
    """Holds one telemetry vector and adds a hop per call made through it."""

    def __init__(self, agent: str = "agent", task: str = "task", *,
                 instructions: Iterable[str] = DEFAULT_INSTRUCTIONS, max_hops: int = DEFAULT_MAX_HOPS,
                 node: Optional[str] = None, names: bool = True, clock: Optional[Clock] = None,
                 registry: Registry = FIELDS, vector: Optional[Vector] = None) -> None:
        self.registry = registry
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._names = names
        self._spans: List[str] = []
        fresh = vector is None
        if fresh:
            if not 0 < max_hops <= 0xFFFF:
                raise ValueError("max_hops must be between 1 and 65535")
            vector = Vector(trace_id=secrets.token_bytes(8), instructions=registry.bitmap(instructions),
                            t0=int(self._clock.wall() * 1000), remaining=max_hops)
        self.vector = vector
        if fresh:
            vector.agent, vector.task = self._name(agent), self._name(task)
        self.node = node or _default_node()
        self._node = self._name(self.node)
        # this process's monotonic clock, pinned to the run's start
        self._mono0 = self._clock.monotonic()
        self._lead = self._clock.wall() - vector.t0 / 1000

    # ---------------------------------------------------------- continuing
    @classmethod
    def resume(cls, text: str, *, node: Optional[str] = None, clock: Optional[Clock] = None,
               registry: Registry = FIELDS, names: bool = True) -> "Probe":
        """Continue a vector another process started (from its text form)."""
        return cls(vector=decode(from_text(text)), node=node, clock=clock, registry=registry, names=names)

    def text(self) -> str:
        with self._lock:
            return to_text(encode(self.vector))

    def flush(self) -> None:
        """Send the vector back to whoever handed it over (a carrier sets this)."""

    def absorb(self, text: Optional[str], handoff: Handoff) -> int:
        """Take back the hops a callee appended to the vector it was handed.
        Hops this probe added meanwhile (a parallel call's) are kept.
        Returns how many arrived."""
        if not text:
            return 0
        other = decode(from_text(text))
        with self._lock:
            if other.trace_id != self.vector.trace_id:
                raise WireError("that vector belongs to another run")
            if other.instructions != self.vector.instructions:
                raise WireError("that vector asks for other fields")
            new = other.hops[handoff.base:]
            self.vector.hops.extend(new)
            self.vector.remaining = max(0, self.vector.remaining - len(new))
            self.vector.dropped += max(0, other.dropped - handoff.dropped)
            self.vector.flags |= other.flags
            self.vector.names.update(other.names)
        return len(new)

    def handover(self) -> Handoff:
        """The vector to give a callee, marked with where its hops will start."""
        with self._lock:
            return Handoff(to_text(encode(self.vector)), len(self.vector.hops), self.vector.dropped)

    # ------------------------------------------------------------- stamping
    @contextmanager
    def hop(self, tool: str, *, kind: str = "tool_call", args: Any = None, effect: Optional[str] = None,
            attempt: Optional[int] = None, wait: Optional[float] = None) -> Iterator[Hop]:
        values: dict = {"bytes_in": size_of(args)}
        if effect is not None:
            values["effect"] = effect
        if attempt is not None:
            values["attempt"] = attempt
        if wait is not None:
            values["wait"] = wait
        if self._spans:
            values["span"] = self._spans[-1]
        hop = Hop(self, tool, kind, values)
        try:
            yield hop
        except BaseException as exc:
            hop.values["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else (
                "timeout" if isinstance(exc, TimeoutError) else "error")
            raise
        finally:
            hop._finish()

    def tool(self, fn: Optional[Callable] = None, *, name: Optional[str] = None, kind: str = "tool_call",
             effect: Optional[str] = None) -> Callable:
        """Decorator: a hop per call, its arguments' size in, its result's size out."""
        def wrap(f: Callable) -> Callable:
            label = name or f.__name__

            @functools.wraps(f)
            def inner(*a, **k):
                with self.hop(label, kind=kind, effect=effect, args=[a, k] if (a or k) else None) as h:
                    out = f(*a, **k)
                    h.result(out)
                    return out
            return inner
        return wrap(fn) if fn is not None else wrap

    @contextmanager
    def span(self, agent: str) -> Iterator[None]:
        """Hops inside are a sub-agent's."""
        self._spans.append(agent)
        try:
            yield
        finally:
            self._spans.pop()

    # ------------------------------------------------------------ internals
    def _name(self, text: str) -> int:
        h = name_hash(text)
        if self._names:
            self.vector.names[h] = text
        return h

    def _offset(self, t: float) -> float:
        return self._lead + (t - self._mono0)

    def _append(self, tool: str, values: dict) -> None:
        values = dict(values, tool=tool, node=self.node)
        with self._lock:
            v = self.vector
            if v.remaining <= 0:
                v.dropped += 1
                v.flags |= OVERFLOW
                return
            words = []
            for bit, f in self.registry.selected(v.instructions):
                if f is None:
                    words.append(0)
                    continue
                raw = values.get(f.name)
                if f.name in ("tool", "node", "span") and isinstance(raw, str):
                    raw = name_hash(raw)
                    if self._names:
                        v.names[raw] = values[f.name]
                word, clamped = f.word(raw)
                if clamped:
                    v.flags |= CLAMPED
                words.append(word)
            v.hops.append(words)
            v.remaining -= 1


class NullProbe:
    """A probe that records nothing: what a tool gets when no vector came
    with its call. Same methods, same return shapes."""

    vector = None
    registry = FIELDS

    @contextmanager
    def hop(self, tool: str, **_: Any) -> Iterator["_NullHop"]:
        yield _NullHop()

    def tool(self, fn: Optional[Callable] = None, **_: Any) -> Callable:
        return fn if fn is not None else (lambda f: f)

    @contextmanager
    def span(self, agent: str) -> Iterator[None]:
        yield

    def text(self) -> str:
        return ""

    def flush(self) -> None:
        pass

    def handover(self) -> Handoff:
        return Handoff("", 0, 0)

    def absorb(self, text: Optional[str], handoff: Handoff) -> int:
        return 0


class _NullHop:
    def result(self, value: Any) -> "_NullHop":
        return self

    def output_bytes(self, n: int) -> "_NullHop":
        return self

    def tokens(self, prompt: int = 0, completion: int = 0) -> "_NullHop":
        return self

    def cost(self, usd: float) -> "_NullHop":
        return self

    def fail(self, status: str = "error") -> "_NullHop":
        return self

    def set(self, field: str, value: Any) -> "_NullHop":
        return self
