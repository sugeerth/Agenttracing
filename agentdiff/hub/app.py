"""The hub as a function: a request in, a response out.

No sockets here. :class:`App` routes a :class:`Request` to a handler and
returns a :class:`Response`; the HTTP server in
:mod:`agentdiff.harness.hub_server` only translates bytes to and from these.
So every route, every refusal and the login itself are testable without
a server, and the app takes its collaborators (users, sessions, catalog,
telemetry store) as arguments rather than building them.

Routes::

    GET  /login                 the sign-in form
    POST /login                 sign in (form token, throttled)
    POST /logout                sign out (session form token)
    GET  /                      every run under the root
    GET  /runs/<id>             one run: a duel's scoreboard, a telemetry path
    GET  /runs/<id>/page        the report page the run wrote
    GET  /api/v1/runs           the catalog as JSON (signed in)
    POST /api/v1/telemetry      an agent posts its vector (bearer ingest token)
    GET  /healthz               liveness, no sign-in
"""

from __future__ import annotations

import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from ..telemetry import rows as telemetry_rows, summary as telemetry_summary
from . import views
from .urls import parse_query, quote, split
from .auth import UserStore, check_password
from .catalog import Catalog
from .config import HubConfig
from .ingest import TelemetryStore
from .sessions import LoginThrottle, Session, SessionStore

__all__ = ["Request", "Response", "App", "SESSION_COOKIE"]

SESSION_COOKIE = "agentdiff_hub"


@dataclass
class Request:
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str:
        return next((v for k, v in self.headers.items() if k.lower() == name.lower()), "")

    def cookie(self, name: str) -> Optional[str]:
        for part in self.header("Cookie").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def form(self) -> Dict[str, str]:
        return {k: v[0] for k, v in parse_query(self.body.decode("utf-8", "replace")).items()}

    @property
    def route(self) -> str:
        return split(self.path)[0]


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: Dict[str, str] = field(default_factory=dict)
    #: a page the hub did not render (a run's report): served in a sandbox
    sandboxed: bool = False

    @classmethod
    def html(cls, text: str, status: int = 200, **headers: str) -> "Response":
        return cls(status, text.encode("utf-8"), headers=dict(headers))

    @classmethod
    def json(cls, data, status: int = 200) -> "Response":
        return cls(status, json.dumps(data, indent=1, default=str).encode("utf-8"), "application/json")

    @classmethod
    def redirect(cls, to: str, **headers: str) -> "Response":
        return cls(303, b"", headers={"Location": to, **headers})


def _cookie(name: str, value: str, max_age: Optional[int] = None) -> str:
    age = f"; Max-Age={max_age}" if max_age is not None else ""
    return f"{name}={value}; HttpOnly; SameSite=Strict; Path=/{age}"


def _safe_next(path: str) -> str:
    """Only a path on this hub: never an absolute URL to somewhere else."""
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


class App:
    def __init__(self, config: HubConfig, *, users: UserStore, sessions: SessionStore, throttle: LoginThrottle,
                 catalog: Catalog, telemetry: TelemetryStore, ingest_token: str, loopback: bool,
                 public_url: str) -> None:
        self.config = config
        self.users, self.sessions, self.throttle = users, sessions, throttle
        self.catalog, self.telemetry = catalog, telemetry
        self.ingest_token = ingest_token
        self.loopback = loopback
        self.public_url = public_url
        self._routes: list = [
            ("GET", re.compile(r"^/healthz$"), self.healthz, False),
            ("GET", re.compile(r"^/login$"), self.login_form, False),
            ("POST", re.compile(r"^/login$"), self.login, False),
            ("POST", re.compile(r"^/logout$"), self.logout, True),
            ("POST", re.compile(r"^/api/v1/telemetry$"), self.ingest, False),
            ("GET", re.compile(r"^/$"), self.runs, True),
            ("GET", re.compile(r"^/api/v1/runs$"), self.api_runs, True),
            ("GET", re.compile(r"^/runs/([0-9a-f]{12})$"), self.run, True),
            ("GET", re.compile(r"^/runs/([0-9a-f]{12})/page$"), self.run_page, True),
        ]

    # --------------------------------------------------------------- routing
    def handle(self, req: Request) -> Response:
        allowed = set()
        for method, pattern, handler, needs_user in self._routes:
            m = pattern.match(req.route)
            if not m:
                continue
            allowed.add(method)
            if method != req.method:
                continue
            session = self.sessions.get(req.cookie(SESSION_COOKIE))
            if needs_user and session is None:
                if req.route.startswith("/api/"):
                    return Response.json({"error": "sign in first"}, 401)
                return Response.redirect("/login?next=" + quote(req.path))
            return handler(req, session, *m.groups())
        if allowed:
            return Response(405, b"method not allowed", "text/plain", {"Allow": ", ".join(sorted(allowed))})
        return Response.html(self._page("Not found", "There is nothing at that address."), 404)

    def _page(self, title: str, text: str, session: Optional[Session] = None) -> str:
        return views.message_page(brand=self.config.title, title=title, text=text,
                                  user=session.user if session else None, csrf=session.csrf if session else None)

    # ----------------------------------------------------------------- login
    def demo_account(self) -> Optional[tuple]:
        u = self.users.get(self.config.demo_user)
        if u is not None and u.demo:
            return (self.config.demo_user, self.config.demo_password)
        return None

    def login_form(self, req: Request, session: Optional[Session], error: Optional[str] = None) -> Response:
        if session is not None and error is None:
            return Response.redirect("/")
        nxt = _safe_next(parse_query(split(req.path)[1]).get("next", ["/"])[0])
        page = views.login_page(brand=self.config.title, token=self.sessions.prelogin(), error=error,
                                demo=self.demo_account(), next_path=nxt)
        return Response.html(page, 401 if error else 200)

    def login(self, req: Request, session: Optional[Session]) -> Response:
        form = req.form()
        if not self.sessions.take_prelogin(form.get("csrf")):
            return self.login_form(req, None, "The form expired. Try again.")
        name = (form.get("user") or "").strip()
        wait = self.throttle.wait_s(name)
        if wait > 0:
            return self.login_form(req, None, f"Too many failed attempts for {name}; wait {wait:.0f}s.")
        user = self.users.get(name)
        if user is None or not check_password(form.get("password") or "", user.password_hash):
            self.throttle.failed(name)
            return self.login_form(req, None, "That user and password do not match.")
        self.throttle.succeeded(name)
        new = self.sessions.create(user.name)
        return Response.redirect(_safe_next(form.get("next") or "/"),
                                 **{"Set-Cookie": _cookie(SESSION_COOKIE, new.token, int(self.sessions.ttl_s))})

    def logout(self, req: Request, session: Session) -> Response:
        if not self.sessions.csrf_ok(session, req.form().get("csrf")):
            return Response.html(self._page("Refused", "That form did not come from this hub.", session), 403)
        self.sessions.end(session.token)
        return Response.redirect("/login", **{"Set-Cookie": _cookie(SESSION_COOKIE, "", 0)})

    # ------------------------------------------------------------------ runs
    def runs(self, req: Request, session: Session) -> Response:
        page = views.runs_page(brand=self.config.title, user=session.user, csrf=session.csrf,
                               entries=self.catalog.entries(),
                               ingest={"url": self.public_url, "token": self.ingest_token})
        return Response.html(page)

    def api_runs(self, req: Request, session: Session) -> Response:
        return Response.json({"runs": [{"id": x.id, "kind": x.kind, "title": x.title, "updated": x.updated,
                                        "path": str(x.path), "summary": x.summary} for x in self.catalog.entries()]})

    def run(self, req: Request, session: Session, run_id: str) -> Response:
        entry = self.catalog.get(run_id)
        if entry is None:
            return Response.html(self._page("Not found", "No run by that id.", session), 404)
        common = dict(brand=self.config.title, user=session.user, csrf=session.csrf, entry=entry)
        if entry.kind == "duel":
            return Response.html(views.duel_page(**common))
        if entry.kind == "telemetry":
            text = self.telemetry.vector(entry.summary.get("run_id", ""))
            if not text:
                return Response.html(self._page("Gone", "That run's vector is no longer stored.", session), 404)
            return Response.html(views.telemetry_page(**common, rows=telemetry_rows(text),
                                                      info=telemetry_summary(text)))
        return Response.html(views.report_page(**common))

    def run_page(self, req: Request, session: Session, run_id: str) -> Response:
        entry = self.catalog.get(run_id)
        if entry is None or entry.page is None or not self.catalog.inside(entry.page):
            return Response.html(self._page("Not found", "That run wrote no page.", session), 404)
        # the report is a page the run wrote, with its own inline scripts
        return Response(200, entry.page.read_bytes(), sandboxed=True)

    # ---------------------------------------------------------------- ingest
    def ingest(self, req: Request, session: Optional[Session]) -> Response:
        given = req.header("Authorization").removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(given, self.ingest_token):
            return Response.json({"error": "a valid ingest token is required (Authorization: Bearer ...)"}, 401)
        if len(req.body) > self.config.max_post_bytes:
            return Response.json({"error": "too large"}, 413)
        try:
            data = json.loads(req.body.decode("utf-8")) if req.body else {}
        except ValueError:
            return Response.json({"error": "the body is JSON: {vector, prompt?, success?, answer?, model?}"}, 400)
        if not isinstance(data, dict) or not isinstance(data.get("vector"), str):
            return Response.json({"error": "the body is JSON: {vector, prompt?, success?, answer?, model?}"}, 400)
        success = data.get("success")
        if success is not None and not isinstance(success, bool):
            return Response.json({"error": "success is true, false or absent"}, 400)
        try:
            saved = self.telemetry.save(data["vector"], prompt=str(data.get("prompt") or ""), success=success,
                                        answer=str(data.get("answer") or ""), model=str(data.get("model") or ""))
        except ValueError as exc:
            return Response.json({"error": str(exc)}, 400)
        return Response.json(saved, 201)

    def healthz(self, req: Request, session: Optional[Session]) -> Response:
        return Response.json({"ok": True})
