"""A bundle behind a read-only local HTTP API and its page.

``agentdiff serve --bundle DIR`` answers GET, and only GET, over the same
three levels the MCP server exposes: ``/api/v1/overview`` (level 1),
``/api/v1/runs`` with the filters and sort of the ``runs`` tool (level 2),
``/api/v1/runs/<key>``, ``/api/v1/runs/<key>/fetches``,
``/api/v1/runs/<key>/data`` and ``/api/v1/runs/<key>/steps/<index>``
(level 3, the last one the whole text of one step),
``/api/v1/budget``, ``/api/v1/lineage``, ``/api/v1/key``,
``/api/v1/verify``, the page at ``/`` and ``/report.html``, and
``/bundle.json``. Every response is JSON (the page apart) with
``Cache-Control: no-store``; anything else is a 404 with a reason; no
directory is ever listed and no path outside the bundle is ever read —
runs are found through the bundle's own index, not the filesystem.

This module lives in the harness because it opens a socket, which the
engine may not; it computes nothing, and the numbers it serves are the
bundle's, which are the members' — counts and sums over recorded steps,
``null`` where nothing was recorded.
"""

from __future__ import annotations

import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from ..bundle import Bundle, decode_key

API = "/api/v1/"


def _bool(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    if text.lower() in ("true", "1", "yes"):
        return True
    if text.lower() in ("false", "0", "no"):
        return False
    raise ValueError(f"success must be true or false, not {text!r}")


def _int(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"limit must be an integer, not {text!r}") from None


def route(bundle: Bundle, path: str, query: dict) -> tuple:
    """``(status, payload)`` for one API path; the page and the manifest
    are handled by the handler itself. A missing key is 404 with a
    reason, a bad filter 400."""
    if not path.startswith(API):
        return 404, {"error": "not found", "reason": f"no route {path!r}; the API is under {API}"}
    rest = path[len(API):]
    try:
        if rest == "overview":
            return 200, bundle.overview
        if rest == "runs":
            rows = bundle.runs(agent=query.get("agent"), task=query.get("task"), member=query.get("member"),
                               success=_bool(query.get("success")), sort=query.get("sort"), limit=_int(query.get("limit")))
            return 200, {"runs": rows, "n": len(rows), "of": len(bundle.rows)}
        if rest.startswith("runs/"):
            key = unquote(rest[len("runs/"):])
            want = None
            step_index = None
            if key.endswith("/fetches"):
                key, want = key[: -len("/fetches")], "fetches"
            elif key.endswith("/data"):
                key, want = key[: -len("/data")], "data"
            else:
                m = re.fullmatch(r"(.+)/steps/([^/]+)", key)
                if m:
                    key, want = m.group(1), "step"
                    try:
                        step_index = int(m.group(2))
                    except ValueError:
                        raise ValueError(f"a step index is an integer, not {m.group(2)!r}") from None
            record = bundle.run(key)
            if record is None:
                return 404, {"error": "not found", "reason": f"no run {key!r}; keys are <member>/<task>/<agent>/<run>, listed by /api/v1/runs"}
            if want == "step":
                try:
                    return 200, bundle.step(key, step_index)
                except ValueError as exc:
                    return 404, {"error": "not found", "reason": str(exc)}
            return 200, record[want] if want else record
        if rest == "budget":
            return 200, bundle.budget(query.get("agent"), query.get("task"))
        if rest == "lineage":
            return 200, bundle.lineage(query.get("family"))
        if rest == "key":
            return 200, {"key": bundle.key, "overview": decode_key(bundle.key)}
        if rest == "verify":
            return 200, bundle.verify()
    except ValueError as exc:
        return 400, {"error": "bad request", "reason": str(exc)}
    return 404, {"error": "not found", "reason": f"no route {path!r}; overview, runs, runs/<key>, runs/<key>/fetches, "
                                                  "runs/<key>/data, runs/<key>/steps/<index>, budget, lineage, key, verify"}


class Handler(BaseHTTPRequestHandler):
    bundle: Bundle = None  # type: ignore[assignment]
    quiet = True

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401 — quiet by default
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parts = urlsplit(self.path)
        path = parts.path
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        if path in ("/", "/index.html", "/report.html"):
            page = self.bundle.path / "report.html"
            if page.is_file():
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "not found", "reason": "the bundle has no report.html"})
            return
        if path == "/bundle.json":
            self._send(200, (self.bundle.path / "bundle.json").read_bytes(), "application/json; charset=utf-8")
            return
        try:
            status, payload = route(self.bundle, path, query)
        except (OSError, KeyError) as exc:
            # a record the index names but the disk no longer holds, or one that cannot be read: an answer, not a dropped connection
            what = getattr(exc, "strerror", None) or str(exc)
            status, payload = 500, {"error": "server error", "reason": f"the bundle cannot be read: {what}"}
        self._json(status, payload)


def _family(host: str) -> int:
    """The address family a host binds: IPv6 for a literal with a colon
    (``::1``), IPv4 otherwise (``127.0.0.1``, ``localhost``)."""
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def make_server(bundle: Bundle, host: str = "127.0.0.1", port: int = 8787, quiet: bool = True) -> ThreadingHTTPServer:
    """A server bound to ``host:port`` (0 picks a free port), not yet
    serving: the caller runs ``serve_forever`` — in a thread, in a test.
    An IPv6 literal (``::1``) binds an IPv6 socket; ``OSError`` when the
    address cannot be bound."""
    handler = type("BundleHandler", (Handler,), {"bundle": bundle, "quiet": quiet})
    server_cls = type("BundleServer", (ThreadingHTTPServer,), {"address_family": _family(host)})
    return server_cls((host, port), handler)


__all__ = ["API", "route", "Handler", "make_server"]
