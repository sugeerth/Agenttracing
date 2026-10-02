"""The hub on the network: the HTTP adapter, the wiring, and the client.

:mod:`agentdiff.hub` is the platform with no sockets in it. This module is
the only part that touches the network, as the engine's rule requires
(only ``agentdiff.harness`` may):

- :func:`build_app` assembles the app from the settings: the user file,
  the demo account, sessions, the catalog, the telemetry store, and the
  ingest token (made once, kept in the state directory, readable by its
  owner only).
- :func:`make_server` serves it: bytes to :class:`~agentdiff.hub.app.Request`
  and back, a body limit, security headers on every response, and a
  streamed body for the event stream.
- :class:`TracePoller` watches the trace files under the root and tells
  the live bus which traces were added, grew or finished.
- :func:`send` is the client an agent (or ``agentdiff telemetry send``)
  uses to post a vector to a hub.
- :class:`TraceStreamer` posts a trace directory to a hub as it grows
  (``agentdiff telemetry stream``, ``duel --hub``).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

from ..hub.app import App, Request, Response
from ..hub.auth import JsonUserStore, sync_demo
from ..hub.catalog import Catalog
from ..hub.config import HubConfig
from ..hub.ingest import TelemetryStore
from ..hub.live import LiveBus
from ..hub.sessions import LoginThrottle, SessionStore
from ..hub.traces import TraceIndex
from .watch import is_loopback

__all__ = ["build_app", "make_server", "send", "ingest_token", "TracePoller", "TraceStreamer"]

#: pages the hub renders itself: no scripts at all
_HUB_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
#: a live page: the hub's own script file (never an inline one), and the
#: event stream and fragments from this hub only
_LIVE_CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; "
             "img-src data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
#: a report a run wrote: its own inline scripts, in a sandbox with an origin
#: of its own, so nothing in a trace can act as the signed-in user
_REPORT_CSP = ("sandbox allow-scripts allow-popups allow-downloads; default-src 'none'; "
               "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
               "frame-ancestors 'none'")


def _mark_state(state: Path) -> None:
    from .vendors import mark_output
    mark_output(state)


def ingest_token(state: Path) -> str:
    """The token agents post telemetry with: made once, then read back."""
    path = state / "ingest.token"
    if path.is_file():
        token = path.read_text(encoding="ascii").strip()
        if token:
            return token
    state.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token + "\n", encoding="ascii")
    os.chmod(path, 0o600)
    return token


def build_app(config: HubConfig) -> App:
    state = config.state_dir
    _mark_state(state)
    loopback = is_loopback(config.host)
    users = JsonUserStore(state / "users.json")
    sync_demo(users, config.effective_demo(loopback), config.demo_user, config.demo_password,
              config.password_iterations)
    telemetry = TelemetryStore(state / "telemetry")
    catalog = Catalog(Path(config.root), depth=config.scan_depth, limit=config.max_entries,
                      extra=telemetry.entries)
    traces = TraceIndex(catalog.root, depth=config.scan_depth, limit=config.max_entries,
                        extra_dirs=[telemetry.dir / "traces"])
    host = config.host if ":" not in config.host else f"[{config.host}]"
    return App(config, users=users, sessions=SessionStore(config.session_hours * 3600),
               throttle=LoginThrottle(config.login_attempts, config.login_lockout_s),
               catalog=catalog, telemetry=telemetry, ingest_token=ingest_token(state), loopback=loopback,
               public_url=f"http://{host}:{config.port}", traces=traces, bus=LiveBus())


class TracePoller:
    """Looks at the trace files every ``interval`` seconds and publishes
    each one that was added, grew, finished or went away. Polling, not a
    file-system watch: it works the same on every platform and in every
    container, and a look costs a ``stat`` per trace."""

    def __init__(self, traces: TraceIndex, bus: LiveBus, interval: float = 0.5) -> None:
        self.traces, self.bus, self.interval = traces, bus, interval
        self._stop = threading.Event()
        self._seen = traces.signature()
        self._thread: Optional[threading.Thread] = None

    def tick(self) -> list:
        now = self.traces.signature()
        moved = sorted(k for k in set(now) | set(self._seen) if now.get(k) != self._seen.get(k))
        self._seen = now
        for tid in moved:
            self.bus.publish("trace", tid)
        return moved

    def start(self) -> "TracePoller":
        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 — a bad file must not stop the live view
                    pass
        self._thread = threading.Thread(target=loop, name="agentdiff-hub-poller", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.bus.close()


class _Handler(BaseHTTPRequestHandler):
    app: App = None  # type: ignore[assignment]
    quiet = True
    server_version = "agentdiff-hub"
    sys_version = ""

    def log_message(self, fmt, *args):  # noqa: D401
        if not self.quiet:
            super().log_message(fmt, *args)

    def _serve(self, method: str) -> None:
        body = b""
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > self.app.config.max_post_bytes:
                self._write(Response.json({"error": "a body of a stated size, under the limit"}, 413))
                return
            body = self.rfile.read(length)
        req = Request(method, self.path, {k: v for k, v in self.headers.items()}, body)
        try:
            resp = self.app.handle(req)
        except Exception:  # noqa: BLE001 — one bad request must not take the hub down
            resp = Response.json({"error": "the hub failed on that request"}, 500)
        self._write(resp)

    def _write(self, resp: Response) -> None:
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        if resp.stream is None:
            self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        csp = _REPORT_CSP if resp.sandboxed else _LIVE_CSP if resp.scripted else _HUB_CSP
        self.send_header("Content-Security-Policy", csp)
        for k, v in resp.headers.items():
            self.send_header(k, v)
        self.end_headers()
        if resp.stream is None:
            self.wfile.write(resp.body)
            return
        # the event stream: each frame as it is made, until either side stops
        self.close_connection = True
        try:
            for chunk in resp.stream:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            close = getattr(resp.stream, "close", None)
            if close:
                close()

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")


def make_server(app: App, *, quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("HubHandler", (_Handler,), {"app": app, "quiet": quiet})
    server = ThreadingHTTPServer((app.config.host, app.config.port), handler)
    server.daemon_threads = True
    return server


def send(url: str, token: str, vector: str, *, prompt: str = "", success: Optional[bool] = None,
         answer: str = "", model: str = "", live: bool = False, timeout: float = 10.0) -> Tuple[int, dict]:
    """Post a vector to a hub. ``(status, body)``; never raises on an HTTP error."""
    payload = {"vector": vector, "prompt": prompt, "answer": answer, "model": model}
    if success is not None:
        payload["success"] = success
    if live:
        payload["live"] = True
    req = urllib.request.Request(url.rstrip("/") + "/api/v1/telemetry", data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — the operator's own hub
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {"error": str(exc)}


class TraceStreamer:
    """A trace directory, posted to a hub as it grows.

    Every ``interval`` seconds it looks at the directory: a ``.live.json``
    frame that changed is posted as a live vector, a final trace as the
    finished one, each under one trace id per run (a hash of its name),
    so the hub holds one run that grows. What it posts is the in-band
    vector: sizes, times and outcomes, never what the agent read or wrote.
    A hub that is down is retried at the next look; nothing is lost while
    the files are on disk.
    """

    LIVE = ".live.json"

    def __init__(self, directory, url: str, token: str, *, interval: float = 1.0, poster=None) -> None:
        self.dir = Path(directory)
        self.url, self.token, self.interval = url, token, interval
        self.post = poster or send
        self._seen: dict = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.sent = 0
        self.failed = 0
        self.last_error: Optional[str] = None

    @staticmethod
    def run_key(name: str) -> str:
        return name[: -len(TraceStreamer.LIVE)] if name.endswith(TraceStreamer.LIVE) else name[: -len(".json")]

    def tick(self) -> int:
        from ..telemetry import encode, from_trajectory, to_text
        if not self.dir.is_dir():
            return 0
        names = sorted(n for n in os.listdir(self.dir) if n.endswith(".json") and not n.startswith((".", "report_"))
                       and n not in ("RUN_MANIFEST.json", "aggregate.json"))
        finals = {n for n in names if not n.endswith(self.LIVE)}
        posted = 0
        for n in names:
            live = n.endswith(self.LIVE)
            if live and self.run_key(n) + ".json" in finals:
                continue
            p = self.dir / n
            try:
                st = p.stat()
                version = (st.st_size, st.st_mtime_ns)
                if self._seen.get(n) == version:
                    continue
                traj = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue        # mid-write: the next look reads it whole
            if not isinstance(traj, dict) or not isinstance(traj.get("steps"), list):
                self._seen[n] = version
                continue
            tid = hashlib.sha256(f"agentdiff-stream:{self.run_key(n)}".encode("utf-8")).digest()[:8]
            try:
                text = to_text(encode(from_trajectory(traj, trace_id=tid)))
                outcome = traj.get("outcome") or {}
                status, body = self.post(self.url, self.token, text,
                                         prompt=str((traj.get("task") or {}).get("prompt") or "")[:2000],
                                         success=None if live else outcome.get("success"),
                                         model=str((traj.get("agent") or {}).get("model") or ""), live=live)
            except (OSError, ValueError) as exc:
                self.failed += 1
                self.last_error = str(exc)
                continue        # not marked seen: tried again at the next look
            if status != 201:
                self.failed += 1
                self.last_error = f"the hub said {status}: {body.get('error') or body}"
                if status in (400, 413):
                    self._seen[n] = version     # it will not be accepted as it is
                continue
            self._seen[n] = version
            self.sent += 1
            posted += 1
        return posted

    def start(self) -> "TraceStreamer":
        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001 — streaming must never stop the run
                    self.last_error = str(exc)
        self._thread = threading.Thread(target=loop, name="agentdiff-stream", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop, after one last look so the finished traces go too."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 5)
        try:
            self.tick()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
