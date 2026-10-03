"""What changed, as it changes: the hub's one publish/subscribe point.

Two things move while agents run. A trace file grows on disk, frame by
frame, as the harness writes it (a poller sees that, in
:mod:`agentdiff.harness.hub_server`). A vector arrives from an agent
somewhere else, over the ingest endpoint (the telemetry store says so).
Both publish here, and every open page subscribed to the event stream
hears of it.

An event carries no content, only what changed (``{"v", "kind", "id"}``).
A page then fetches the fragment it shows, as the signed-in user, through
the same views that drew it the first time. So the stream cannot leak
what the pages would not show, and there is one renderer, not two.

The bus keeps the last :data:`KEEP` events, so a subscriber that blinked
(a slow reader, a reconnect with ``Last-Event-ID``) gets what it missed.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Callable, Iterator, List, Optional

__all__ = ["LiveBus", "KEEP", "sse"]

KEEP = 256


class LiveBus:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._cond = threading.Condition()
        self._events: deque = deque(maxlen=KEEP)
        self._version = 0
        self._clock = clock
        self.closed = False

    @property
    def version(self) -> int:
        return self._version

    def publish(self, kind: str, ident: str = "") -> int:
        with self._cond:
            self._version += 1
            self._events.append({"v": self._version, "kind": kind, "id": ident, "at": round(self._clock(), 3)})
            self._cond.notify_all()
            return self._version

    def since(self, version: int) -> List[dict]:
        with self._cond:
            return [e for e in self._events if e["v"] > version]

    def wait(self, version: int, timeout: float) -> List[dict]:
        """Events after ``version``, waiting up to ``timeout`` for the first."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._version <= version and not self.closed:
                left = deadline - time.monotonic()
                if left <= 0:
                    return []
                self._cond.wait(left)
            return [e for e in self._events if e["v"] > version]

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()


def sse(bus: LiveBus, since: int, *, keepalive_s: float = 15.0, max_s: float = 600.0,
        wait: Optional[Callable[[int, float], List[dict]]] = None) -> Iterator[bytes]:
    """The event stream: one ``change`` event per batch of changes, a comment
    every ``keepalive_s`` so proxies keep the connection, and an end after
    ``max_s`` (the browser reconnects with the last id it saw, and misses
    nothing the bus still holds)."""
    wait = wait or bus.wait
    started = time.monotonic()
    yield b"retry: 2000\n\n"
    version = since
    missed = bus.since(version) if since else []
    if missed:
        version = missed[-1]["v"]
        yield _frame(version, missed)
    while not bus.closed and time.monotonic() - started < max_s:
        events = wait(version, keepalive_s)
        if not events:
            if bus.closed:
                break
            yield b": keepalive\n\n"
            continue
        version = events[-1]["v"]
        yield _frame(version, events)


def _frame(version: int, events: list) -> bytes:
    # one frame per batch: a page refreshes once, however many traces moved
    ids = sorted({e["id"] for e in events if e["id"]})
    kinds = sorted({e["kind"] for e in events})
    data = json.dumps({"v": version, "kinds": kinds, "ids": ids[:64]})
    return f"id: {version}\nevent: change\ndata: {data}\n\n".encode("utf-8")
