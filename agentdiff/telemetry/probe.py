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
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, List, Optional, Protocol

from .fields import DEFAULT_INSTRUCTIONS, FIELDS, WORD_MAX, Registry, STATUSES
from .wire import CLAMPED, OVERFLOW, Vector, WireError, decode, encode, from_text, name_hash, to_text

__all__ = ["Probe", "NullProbe", "Hop", "Handoff", "Clock", "SystemClock", "size_of", "DEFAULT_MAX_HOPS"]

#: hops a vector takes before it refuses more (and says so)
DEFAULT_MAX_HOPS = 4096


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def wall(self) -> float: ...


class SystemClock:
    monotonic = staticmethod(time.monotonic)
    wall = staticmethod(time.time)


def size_of(value: Any) -> int:
    """Bytes a value takes as text: what a hop records instead of the value."""
    if value is None:
        return 0
    if type(value) is str:
        # most arguments are ASCII, whose length is its size in bytes
        return len(value) if value.isascii() else len(value.encode("utf-8"))
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
    """One call in flight, and its own context manager.

    Its values are slots, set as the call goes, and the hop itself is what
    the probe keeps when it ends; turning it into words waits until the
    vector is read. The agent's path pays for two clock reads and an
    append. A field a deployment registered goes in ``extra``.
    """

    __slots__ = ("_probe", "tool", "kind", "status", "start", "latency", "bytes_in", "bytes_out",
                 "tokens_in", "tokens_out", "extra", "_t")

    def __init__(self, probe: "Probe", tool: str, kind: str, bytes_in: int, extra: Optional[dict]) -> None:
        self._probe = probe
        self.tool = tool
        self.kind = kind
        self.status = "ok"
        self.bytes_in = bytes_in
        self.bytes_out = self.tokens_in = self.tokens_out = 0
        self.extra = extra
        self.latency = 0.0
        self._t = t = probe._now()
        self.start = probe._base + t

    def __enter__(self) -> "Hop":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        probe = self._probe
        self.latency = probe._now() - self._t
        if exc_type is not None:
            self.status = ("cancelled" if issubclass(exc_type, KeyboardInterrupt) else
                           "timeout" if issubclass(exc_type, TimeoutError) else "error")
        probe._pending.append(self)
        return False

    @property
    def values(self) -> dict:
        """Everything this hop will write, by field name."""
        out = {"tool": self.tool, "kind": self.kind, "status": self.status, "start": self.start,
               "latency": max(0.0, self.latency), "bytes_in": self.bytes_in, "bytes_out": self.bytes_out,
               "tokens_in": self.tokens_in, "tokens_out": self.tokens_out}
        if self.extra:
            out.update(self.extra)
        return out

    def result(self, value: Any) -> "Hop":
        self.bytes_out = size_of(value)
        return self

    def output_bytes(self, n: int) -> "Hop":
        """The result's size when the hop counted it itself (a stream)."""
        self.bytes_out = int(n)
        return self

    def tokens(self, prompt: int = 0, completion: int = 0) -> "Hop":
        self.tokens_in = int(prompt or 0)
        self.tokens_out = int(completion or 0)
        return self

    def cost(self, usd: float) -> "Hop":
        return self._extra("cost", float(usd or 0.0))

    def reward(self, value: float) -> "Hop":
        """The environment's reward for this step (an RL rollout)."""
        return self._extra("reward", float(value))

    def fail(self, status: str = "error") -> "Hop":
        if status not in STATUSES:
            raise ValueError(f"status {status!r} is not one of {', '.join(STATUSES)}")
        self.status = status
        return self

    def set(self, field: str, value: Any) -> "Hop":
        """Any registered field by name: the way to fill a field a deployment added."""
        self._probe.registry.get(field)
        if field in _SLOTTED:
            setattr(self, field, value)
            return self
        return self._extra(field, value)

    def _extra(self, field: str, value: Any) -> "Hop":
        if self.extra is None:
            self.extra = {}
        self.extra[field] = value
        return self


#: the fields a hop keeps as slots; any other goes in its ``extra``
_SLOTTED = frozenset(("tool", "kind", "status", "start", "latency", "bytes_in", "bytes_out", "tokens_in",
                      "tokens_out"))


#: fields whose value is a name, carried as its hash
_NAMED = ("tool", "node", "span")


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
        self._hashes: dict = {}
        #: hops ended and not yet encoded; a deque, whose append and popleft
        #: are each atomic, so the agent's threads never take a lock
        self._pending: deque = deque()
        fresh = vector is None
        if fresh:
            if not 0 < max_hops <= 0xFFFF:
                raise ValueError("max_hops must be between 1 and 65535")
            vector = Vector(trace_id=secrets.token_bytes(8), instructions=registry.bitmap(instructions),
                            t0=int(self._clock.wall() * 1000), remaining=max_hops)
        self._vector = vector
        #: the clock's reading, bound once: a hop reads it twice
        self._now = self._clock.monotonic
        if fresh:
            vector.agent, vector.task = self._name(agent), self._name(task)
        # the encoding plan, once: each word's (field name, encoder); None for a
        # bit this registry does not know, whose word is written as 0
        self._plan = [(f.name, f.encode) if f else None for _, f in registry.selected(vector.instructions)]
        self.node = node or _default_node()
        self._node = self._name(self.node)
        # this process's monotonic clock, pinned to the run's start
        self._mono0 = self._clock.monotonic()
        self._lead = self._clock.wall() - vector.t0 / 1000
        #: a reading of this process's clock, plus this, is seconds since the run began
        self._base = self._lead - self._mono0

    # ---------------------------------------------------------- continuing
    @classmethod
    def resume(cls, text: str, *, node: Optional[str] = None, clock: Optional[Clock] = None,
               registry: Registry = FIELDS, names: bool = True) -> "Probe":
        """Continue a vector another process started (from its text form)."""
        return cls(vector=decode(from_text(text)), node=node, clock=clock, registry=registry, names=names)

    @property
    def vector(self) -> Vector:
        """The vector, every hop recorded so far encoded into it."""
        with self._lock:
            self._settle()
            return self._vector

    def text(self) -> str:
        with self._lock:
            self._settle()
            return to_text(encode(self._vector))

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
            self._settle()
            v = self._vector
            if other.trace_id != v.trace_id:
                raise WireError("that vector belongs to another run")
            if other.instructions != v.instructions:
                raise WireError("that vector asks for other fields")
            new = other.hops[handoff.base:]
            v.hops.extend(new)
            v.remaining = max(0, v.remaining - len(new))
            v.dropped += max(0, other.dropped - handoff.dropped)
            v.flags |= other.flags
            v.names.update(other.names)
        return len(new)

    def handover(self) -> Handoff:
        """The vector to give a callee, marked with where its hops will start."""
        with self._lock:
            self._settle()
            v = self._vector
            return Handoff(to_text(encode(v)), len(v.hops), v.dropped)

    # ------------------------------------------------------------- stamping
    def hop(self, tool: str, *, kind: str = "tool_call", args: Any = None, effect: Optional[str] = None,
            attempt: Optional[int] = None, wait: Optional[float] = None) -> Hop:
        """A hop for one call: ``with probe.hop("search") as hop: ...``."""
        extra = None
        if effect is not None or attempt is not None or wait is not None or self._spans:
            extra = {k: v for k, v in (("effect", effect), ("attempt", attempt), ("wait", wait),
                                       ("span", self._spans[-1] if self._spans else None)) if v is not None}
        return Hop(self, tool, kind, size_of(args) if args is not None else 0, extra)

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
        h = self._hashes.get(text)
        if h is None:
            h = self._hashes[text] = name_hash(text)
            if self._names:
                self._vector.names[h] = text
        return h

    def _offset(self, t: float) -> float:
        return self._lead + (t - self._mono0)


    def _settle(self) -> None:
        """Encode the hops recorded since the vector was last read (lock held)."""
        if not self._pending:
            return
        v, plan, name, node = self._vector, self._plan, self._name, self._node
        clamped = False
        hops = v.hops
        # take exactly the hops that have ended so far: one ending meanwhile
        # stays queued for the next read, never lost
        queue = self._pending
        pending = [queue.popleft() for _ in range(len(queue))]
        # the hop budget: what does not fit is refused, and the vector says so
        room = max(0, v.remaining)
        if len(pending) > room:
            v.dropped += len(pending) - room
            v.flags |= OVERFLOW
            pending = pending[:room]
        v.remaining -= len(pending)
        for hop in pending:
            tool, extra = hop.tool, hop.extra
            words = []
            add = words.append
            for step in plan:
                if step is None:
                    add(0)
                    continue
                fname, encode = step
                if fname == "tool":
                    add(name(tool))
                elif fname == "node":
                    add(node)
                else:
                    raw = getattr(hop, fname) if fname in _SLOTTED else (extra.get(fname) if extra else None)
                    if fname == "latency" and raw < 0:
                        raw = 0.0
                    if raw.__class__ is str and fname in _NAMED:
                        add(name(raw))
                        continue
                    w = int(encode(raw))
                    if w > WORD_MAX:
                        w, clamped = WORD_MAX, True
                    elif w < 0:
                        w, clamped = 0, True
                    add(w)
            hops.append(words)
        if clamped:
            v.flags |= CLAMPED


class NullProbe:
    """A probe that records nothing: what a tool gets when no vector came
    with its call. Same methods, same return shapes."""

    vector = None
    registry = FIELDS
    node = ""

    def hop(self, tool: str, **_: Any) -> "_NullHop":
        return _NullHop()

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
    __slots__ = ()

    def __enter__(self) -> "_NullHop":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def result(self, value: Any) -> "_NullHop":
        return self

    def reward(self, value: float) -> "_NullHop":
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
