"""The hub on the network: the HTTP adapter, the wiring, and the client.

:mod:`agentdiff.hub` is the platform with no sockets in it. This module is
the only part that touches the network, as the engine's rule requires
(only ``agentdiff.harness`` may):

- :func:`build_app` assembles the app from the settings: the user file,
  the demo account, sessions, the catalog, the telemetry store, and the
  ingest token (made once, kept in the state directory, readable by its
  owner only).
- :func:`make_server` serves it: bytes to :class:`~agentdiff.hub.app.Request`
  and back, a body limit, and security headers on every response.
- :func:`send` is the client an agent (or ``agentdiff telemetry send``)
  uses to post a vector to a hub.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

from ..hub.app import App, Request, Response
from ..hub.auth import JsonUserStore, sync_demo
from ..hub.catalog import Catalog
from ..hub.config import HubConfig
from ..hub.ingest import TelemetryStore
from ..hub.sessions import LoginThrottle, SessionStore
from .watch import is_loopback

__all__ = ["build_app", "make_server", "send", "ingest_token"]

#: pages the hub renders itself: no scripts at all
_HUB_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
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
    host = config.host if ":" not in config.host else f"[{config.host}]"
    return App(config, users=users, sessions=SessionStore(config.session_hours * 3600),
               throttle=LoginThrottle(config.login_attempts, config.login_lockout_s),
               catalog=catalog, telemetry=telemetry, ingest_token=ingest_token(state), loopback=loopback,
               public_url=f"http://{host}:{config.port}")


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
        self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", _REPORT_CSP if resp.sandboxed else _HUB_CSP)
        for k, v in resp.headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.body)

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
         answer: str = "", model: str = "", timeout: float = 10.0) -> Tuple[int, dict]:
    """Post a vector to a hub. ``(status, body)``; never raises on an HTTP error."""
    payload = {"vector": vector, "prompt": prompt, "answer": answer, "model": model}
    if success is not None:
        payload["success"] = success
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
