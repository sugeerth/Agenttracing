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
    GET  /                      the overview: running now, stuck, recent
    GET  /runs                  every run under the root (?kind=)
    GET  /runs/<id>             one run: a duel, a report, a telemetry path, an eval suite
    GET  /runs/<id>/page        the report page the run wrote
    GET  /traces                every trace (?show=live|passed|failed|stuck, ?q=)
    GET  /traces/<id>           one trace: on its clock (threads, laps, folds, where to look first), its
                                loop lap by lap, its flow, its steps (?vs=<id>&axis=step|time: two runs)
    GET  /traces/<id>/panel     the part of that page that moves (for the live script)
    GET  /timeline              many runs, one row each, on one clock (?g=<group>, ?run=<id>, ?task=, ?scale=)
    GET  /timeline/panel        its moving part
    GET  /live                  every running trace, updating itself
    GET  /live/panel            its moving part
    GET  /evolve                every self-evolving harness: agents, evals, the changes tried
    GET  /evals                 every self-evolving eval suite
    GET  /account               who is signed in; change the password
    POST /account/password      change it (session form token, current password)
    GET  /api/v1/runs           the catalog as JSON (signed in)
    GET  /api/v1/traces         the trace index as JSON
    GET  /api/v1/traces/<id>    one trace, as written
    GET  /api/v1/traces/<id>/long  a long run's reading (sessions, bursts, phases) and its calls, compact, for the lens
    GET  /api/v1/events         server-sent events: what changed, never its content
    POST /api/v1/telemetry      an agent posts its vector (bearer ingest token)
    GET  /static/live.js        the one script, for pages that say they are live
    GET  /healthz               liveness, no sign-in
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from ..telemetry import rows as telemetry_rows, summary as telemetry_summary
from . import views
from .urls import parse_query, quote, split
from .auth import UserStore, check_password, hash_password
from .catalog import Catalog
from .config import HubConfig
from .ingest import TelemetryStore
from .live import LiveBus, sse
from .sessions import LoginThrottle, Session, SessionStore
from .traces import TraceIndex, trace_id

_STATIC = Path(__file__).with_name("static")

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
    #: a live page: it may load the hub's own script and open the event stream
    scripted: bool = False
    #: a body sent as it is made (the event stream), instead of ``body``
    stream: Optional[Iterable[bytes]] = None

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


def _now_on_its_clock(data: dict) -> Optional[float]:
    """Now, in the seconds of a running trace's own clock: the wall clock since it
    started, when that agrees with its steps (no earlier than its last, within six
    hours of it). A replay, or a clock that is not the wall clock, gets none."""
    start = data.get("started_at")
    steps = data.get("steps") or []
    if not isinstance(start, (int, float)) or not steps:
        return None
    last = steps[-1]
    end = float(last.get("started_s") or 0) + float(last.get("latency_s") or 0)
    now = time.time() - float(start)
    return now if end - 5 <= now <= end + 6 * 3600 else None


def _json_list(path: Path, key: str) -> list:
    try:
        v = json.loads(path.read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError, AttributeError):
        return []
    return v if isinstance(v, list) else []


def _under(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _safe_next(path: str) -> str:
    """Only a path on this hub: never an absolute URL to somewhere else."""
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


class App:
    def __init__(self, config: HubConfig, *, users: UserStore, sessions: SessionStore, throttle: LoginThrottle,
                 catalog: Catalog, telemetry: TelemetryStore, ingest_token: str, loopback: bool,
                 public_url: str, traces: Optional[TraceIndex] = None, bus: Optional[LiveBus] = None) -> None:
        self.config = config
        self.users, self.sessions, self.throttle = users, sessions, throttle
        self.catalog, self.telemetry = catalog, telemetry
        self.traces = traces or TraceIndex(catalog.root, depth=config.scan_depth, limit=config.max_entries,
                                           extra_dirs=[telemetry.dir / "traces"])
        self.bus = bus or LiveBus()
        self.ingest_token = ingest_token
        self.loopback = loopback
        self.public_url = public_url
        #: where this machine's own evolving harnesses are written (``hub --claude-code``), else None
        self.evolve_home: Optional[Path] = None
        #: rendering pages to read elsewhere (``hub --export``): no path or program of this machine on them
        self.exporting = False
        hexid = r"([0-9a-f]{12})"
        self._routes: list = [
            ("GET", re.compile(r"^/healthz$"), self.healthz, False),
            ("GET", re.compile(r"^/static/live\.js$"), self.static_live, False),
            ("GET", re.compile(r"^/static/longview\.js$"), self.static_longview, False),
            ("GET", re.compile(r"^/login$"), self.login_form, False),
            ("POST", re.compile(r"^/login$"), self.login, False),
            ("POST", re.compile(r"^/logout$"), self.logout, True),
            ("POST", re.compile(r"^/api/v1/telemetry$"), self.ingest, False),
            ("GET", re.compile(r"^/$"), self.overview, True),
            ("GET", re.compile(r"^/runs$"), self.runs, True),
            ("GET", re.compile(rf"^/runs/{hexid}$"), self.run, True),
            ("GET", re.compile(rf"^/runs/{hexid}/page$"), self.run_page, True),
            ("GET", re.compile(r"^/traces$"), self.trace_index, True),
            ("GET", re.compile(rf"^/traces/{hexid}$"), self.trace, True),
            ("GET", re.compile(rf"^/traces/{hexid}/panel$"), self.trace_fragment, True),
            ("GET", re.compile(r"^/timeline$"), self.timeline, True),
            ("GET", re.compile(r"^/timeline/panel$"), self.timeline_fragment, True),
            ("GET", re.compile(r"^/live$"), self.live, True),
            ("GET", re.compile(r"^/live/panel$"), self.live_fragment, True),
            ("GET", re.compile(r"^/evolve$"), self.evolve, True),
            ("GET", re.compile(r"^/evals$"), self.evals, True),
            ("GET", re.compile(r"^/account$"), self.account, True),
            ("POST", re.compile(r"^/account/password$"), self.change_password, True),
            ("GET", re.compile(r"^/api/v1/runs$"), self.api_runs, True),
            ("GET", re.compile(r"^/api/v1/traces$"), self.api_traces, True),
            ("GET", re.compile(rf"^/api/v1/traces/{hexid}$"), self.api_trace, True),
            ("GET", re.compile(rf"^/api/v1/traces/{hexid}/long$"), self.api_trace_long, True),
            ("GET", re.compile(r"^/api/v1/events$"), self.events, True),
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
    def _ingest_info(self) -> dict:
        return {"url": self.public_url, "token": self.ingest_token}

    def _common(self, session: Session) -> dict:
        return dict(brand=self.config.title, user=session.user, csrf=session.csrf)

    def overview(self, req: Request, session: Session) -> Response:
        from ..guard import summary
        guards = [(self.traces.rel(p), g) for p in self.traces.guarded() for g in [summary(p)] if g]
        return Response.html(views.overview_page(**self._common(session), entries=self.catalog.entries(),
                                                 refs=self.traces.refs(), ingest=self._ingest_info(), guards=guards))

    def runs(self, req: Request, session: Session) -> Response:
        kind = parse_query(split(req.path)[1]).get("kind", [""])[0]
        page = views.runs_page(**self._common(session), entries=self.catalog.entries(), ingest=self._ingest_info(),
                               kind=kind if re.fullmatch(r"[a-z]{0,16}", kind) else "")
        return Response.html(page)

    def api_runs(self, req: Request, session: Session) -> Response:
        return Response.json({"runs": [{"id": x.id, "kind": x.kind, "title": x.title, "updated": x.updated,
                                        "path": str(x.path), "summary": x.summary} for x in self.catalog.entries()]})

    def run(self, req: Request, session: Session, run_id: str) -> Response:
        entry = self.catalog.get(run_id)
        if entry is None:
            return Response.html(self._page("Not found", "No run by that id.", session), 404)
        common = dict(**self._common(session), entry=entry)
        if entry.kind == "duel":
            return Response.html(views.duel_page(**common, refs=self._refs_under(entry.path)))
        if entry.kind == "evolution":
            data = self._json_file(entry.path / "self-evolve.json")
            if data is None:
                return Response.html(self._page("Gone", "That run's file is no longer readable.", session), 404)
            return Response.html(views.evolution_page(**common, data=data, refs=self._refs_under(entry.path)))
        if entry.kind == "evals":
            data = self._json_file(entry.path / "evolve-evals.json")
            if data is None:
                return Response.html(self._page("Gone", "That suite's file is no longer readable.", session), 404)
            return Response.html(views.evals_run_page(**common, data=data))
        if entry.kind == "telemetry":
            run_id = entry.summary.get("run_id", "")
            text = self.telemetry.vector(run_id)
            if not text:
                return Response.html(self._page("Gone", "That run's vector is no longer stored.", session), 404)
            ref = self.traces.get(trace_id(self.traces.rel(self.telemetry.dir / "traces" / f"{run_id}.json")))
            return Response.html(views.telemetry_page(**common, rows=telemetry_rows(text),
                                                      info=telemetry_summary(text), trace_ref=ref))
        return Response.html(views.report_page(**common, refs=self._refs_under(entry.path)))

    def _refs_under(self, path: Path) -> list:
        return [r for r in self.traces.refs() if _under(r.path, path)]

    @staticmethod
    def _json_file(path: Path) -> Optional[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def run_page(self, req: Request, session: Session, run_id: str) -> Response:
        entry = self.catalog.get(run_id)
        if entry is None or entry.page is None or not self.catalog.inside(entry.page):
            return Response.html(self._page("Not found", "That run wrote no page.", session), 404)
        # the report is a page the run wrote, with its own inline scripts
        return Response(200, entry.page.read_bytes(), sandboxed=True)

    # ---------------------------------------------------------------- traces
    def trace_index(self, req: Request, session: Session) -> Response:
        query = parse_query(split(req.path)[1])
        show = query.get("show", [""])[0]
        q = query.get("q", [""])[0][:120]
        return Response.html(views.traces_page(**self._common(session), refs=self.traces.refs(), q=q,
                                               show=show if show in ("live", "passed", "failed", "stuck") else ""))

    def _trace(self, tid: str):
        from ..laps import laps
        ref = self.traces.get(tid)
        if ref is None:
            return None
        data = self.traces.load(ref)
        if data is None:
            return None
        return ref, data, laps(data)

    def _clock(self, req: Request, ref, data: dict) -> dict:
        """Everything a trace's page shows beside the trace: its clock, its card, the
        code it produced, the run it is compared with, and what to do next."""
        from ..insight import agent_fix, code_change, code_compare, code_from_steps, verdict_card
        from ..timeline import compare, timeline
        from .traces import load_record, record_paths
        query = parse_query(split(req.path)[1])
        vs = query.get("vs", [""])[0]
        axis = query.get("axis", ["step"])[0]
        axis = axis if axis in ("step", "time") else "step"
        view = query.get("view", ["focus"])[0]
        view = view if view in {k for k, _, _ in views.PRESETS} else "focus"
        task = ref.summary.get("task")
        others = [r for r in self.traces.refs() if r.id != ref.id and r.summary.get("task") == task][:60]
        tl = timeline(data)
        rec, patch = load_record(ref.path)
        change = code_change(rec, patch) if rec else code_from_steps(data)
        fix = agent_fix(data, look_kind=(tl.get("look_here") or {}).get("kind"))
        peers = [{"tokens": r.summary.get("tokens"), "seconds": r.summary.get("seconds"), "success": r.summary.get("success")}
                 for r in others if not r.live]
        card = verdict_card(data, tl, peers=peers, change=change, fix=fix)
        cmp = other_change = code_cmp = odata = al = None
        if re.fullmatch(r"[0-9a-f]{12}", vs or ""):
            other = next((r for r in others if r.id == vs), None) or self.traces.get(vs)
            odata = self.traces.load(other) if other else None
            if odata is not None:
                from ..timeline import align
                cmp = compare(data, odata)
                al = align(data, odata)
                orec, opatch = load_record(other.path)
                other_change = code_change(orec, opatch) if orec else code_from_steps(odata)
                code_cmp = code_compare(change, other_change)
        act = self._next_step(ref, data, change, fix, record_paths(ref.path)[1])
        long = self._long(query, ref, data, tl, odata if cmp else None, view)
        if long and (long["r"].get("loops") or len(long["r"]["sessions"]) > 1):
            card.insert(1, {"label": "phases", "text": long["r"]["sentence"],
                            "step": (long["r"].get("look_here") or {}).get("index"),
                            "tone": "bad" if long["r"].get("loops") else "",
                            "source": f"agentdiff.longrun: sessions split at {long['r']['basis']['session_gap_s'] / 60:.0f}m "
                                      f"idle, bursts at {long['r']['basis']['burst_gap_s'] or '—'}s "
                                      f"({long['r']['basis']['burst_gap_how']})"})
        extra = tuple(k for k in query.get("open", [""])[0].split(",") if k in {p for p, _ in views.PANELS})
        return {"tl": tl, "cmp": cmp, "others": others, "vs": vs if cmp else "", "axis": axis, "view": view,
                "long": long, "extra_open": extra, "fix": fix,
                "other_data": odata if cmp else None, "al": al, "task_nav": views.task_chips(ref, self.traces.refs()),
                "every": query.get("steps", [""])[0] == "all", "card": card, "change": change,
                "other_change": other_change, "code_cmp": code_cmp, "act": act,
                "at": int(query["at"][0]) if query.get("at", [""])[0].isdigit() else None}

    @staticmethod
    def _long(query: dict, ref, data: dict, tl: dict, odata: Optional[dict], view: str) -> Optional[dict]:
        """The run read in phases (sessions, bursts, loops), and the stretch of it asked
        for: a burst, a session, or t0..t1. Every run has it, as a view to open."""
        from ..longrun import is_long, longrun, window
        if not data.get("steps"):
            return None

        def num(key, kind=float):
            v = query.get(key, [""])[0]
            try:
                return kind(v) if v != "" else None
            except ValueError:
                return None
        r = longrun(data, now_s=_now_on_its_clock(data) if ref.live else None)
        burst, session, page = num("burst", int), num("session", int), num("page", int) or 0
        t0, t1 = num("t0"), num("t1")
        win = zoom = None
        if burst is not None and 1 <= burst <= len(r["bursts"]):
            win = window(data, burst=burst, reading=r)
        elif session is not None and 1 <= session <= len(r["sessions"]):
            s = r["sessions"][session - 1]
            zoom = (s["from"], s["to"])
        elif t0 is not None and t1 is not None and t1 > t0:
            zoom = (max(0.0, t0), t1)
            win = window(data, t0=zoom[0], t1=zoom[1], reading=r)
        other = None
        if odata is not None and is_long(odata):
            from ..timeline import _items
            other = {"data": odata, "items": _items(odata)[0], "r": longrun(odata)}
        return {"r": r, "data": data, "big": is_long(data), "items": tl.get("steps") or [], "win": win, "zoom": zoom, "burst": burst if win and
                burst is not None else None, "session": session if zoom and session else None, "page": page,
                "t0": t0, "t1": t1, "other": other}

    @staticmethod
    def _next_step(ref, data: dict, change: Optional[dict], fix: Optional[dict], arm: Path) -> Optional[dict]:
        """The command that acts on this run: keep its change, or test the change to the agent."""
        agent = str((data.get("agent") or {}).get("name") or "")
        run = str(data.get("run_id") or "") or ref.name.rsplit("__", 1)[-1]
        if change and change.get("keepable"):
            return {"title": "Keep this change", "why": "Its check passed and the harness kept every file it changed; "
                                                        "apply writes them into the project, and refuses if the project "
                                                        "changed since.",
                    "command": f"agentdiff apply --dir {arm} {agent} --run {run} --dry-run   # then without --dry-run"}
        if fix and fix.get("remedy") and (arm / "plan.json").is_file():
            spec = next((a for a in (_json_list(arm / "plan.json", "agents")) if str(a).split("=", 1)[0] == agent), agent)
            return {"title": "Test the change to the agent", "why": "One generation of self-evolve runs these tasks "
                                                                    "with and without the change, the same number of "
                                                                    "times, and keeps it only on the counts.",
                    "command": f"agentdiff self-evolve --task {arm / 'plan.json'} --agent {spec} --generations 1 "
                               f"-o {arm.parent / ('evolve-' + agent)}"}
        return None

    def trace(self, req: Request, session: Session, tid: str) -> Response:
        found = self._trace(tid)
        if found is None:
            return Response.html(self._page("Not found", "No trace by that id.", session), 404)
        ref, data, lap = found
        clock = self._clock(req, ref, data)
        page = views.trace_page(**self._common(session), ref=ref, data=data, lap=lap, **clock)
        return Response(200, page.encode("utf-8"), scripted=ref.live or views.lens_open(clock))

    def trace_fragment(self, req: Request, session: Session, tid: str) -> Response:
        found = self._trace(tid)
        if found is None:
            return Response(404, b"", "text/html; charset=utf-8")
        ref, data, lap = found
        return Response.html(views.trace_panel(ref=ref, data=data, lap=lap, **self._clock(req, ref, data)))

    # -------------------------------------------------------------- timeline
    def _ribbons_of(self, refs: list, last_s: Optional[float] = None):
        """Rows for many runs on one clock; with ``last_s``, a longer run shows only that much of its end."""
        from ..timeline import ribbons
        rows, links, labels = [], [], []
        for r in refs:
            data = self.traces.load(r)
            if data is None:
                continue
            cut = False
            if last_s:
                steps = data.get("steps") or []
                ends = [float(st.get("started_s") or 0) + float(st.get("latency_s") or 0) for st in steps
                        if st.get("started_s") is not None]
                if ends and max(ends) > last_s:
                    since = max(ends) - last_s
                    tail = [dict(st, started_s=round(float(st["started_s"]) - since, 3)) for st in steps
                            if st.get("started_s") is not None and float(st["started_s"]) >= since]
                    if tail:
                        totals = dict(data.get("totals") or {}, latency_s=float(last_s))
                        data, cut = dict(data, steps=tail, totals=totals), True
            got = ribbons([data])
            if not got:
                continue
            rows.append(got[0])
            links.append(f"/traces/{r.id}")
            run = r.summary.get("run")
            labels.append(f"{r.summary.get('task')} · {r.summary.get('agent')}" + (f" · {run}" if run else
                                                                                    f" · {r.name.rsplit('__', 1)[-1]}")
                          + (" · its last hour" if cut else ""))
        return rows, links, labels

    def _timeline_groups(self, req: Request):
        query = parse_query(split(req.path)[1])
        scale = query.get("scale", ["shared"])[0]
        scale = scale if scale in ("shared", "own") else "shared"
        task = query.get("task", [""])[0][:120]
        refs = self.traces.refs()
        title, base = "Every run on one clock", []
        run = query.get("run", [""])[0]
        groups_wanted = [g for g in query.get("g", []) if g][:12]
        if re.fullmatch(r"[0-9a-f]{12}", run or ""):
            entry = self.catalog.get(run)
            if entry is not None:
                refs = [r for r in refs if _under(r.path, entry.path)]
                title = entry.title
                base.append(f"run={run}")
        elif groups_wanted:
            refs = [r for r in refs if r.group in groups_wanted]
            title = groups_wanted[0] if len(groups_wanted) == 1 else f"{len(groups_wanted)} groups"
            base += [f"g={quote(g)}" for g in groups_wanted]
        if task:
            refs = [r for r in refs if r.summary.get("task") == task]
            base.append(f"task={quote(task)}")
            title += f" · {task}"
        by: Dict[str, list] = {}
        for r in refs[:400]:
            by.setdefault(r.group, []).append(r)
        groups = []
        # oldest version first: g0-h0 before g1-h1, so the history reads down the page
        for g in sorted(by):
            rs = sorted(by[g], key=lambda r: (str(r.summary.get("task")), str(r.summary.get("agent")), r.name))
            rows, links, labels = self._ribbons_of(rs)
            groups.append((g, rows, links, labels))
        live = any(r.live for r in refs)
        return title, groups, scale, "&".join(base) or "all=1", live

    def timeline(self, req: Request, session: Session) -> Response:
        title, groups, scale, base, live = self._timeline_groups(req)
        # always live: a page opened before its runs exist fills in as they start
        page = views.timeline_page(**self._common(session), title=title, groups=groups, scale=scale, base_q=base,
                                   live=True)
        return Response(200, page.encode("utf-8"), scripted=True)

    def timeline_fragment(self, req: Request, session: Session) -> Response:
        title, groups, scale, base, live = self._timeline_groups(req)
        html = "".join(views.ribbon_group(name, rows, links, labels, scale) for name, rows, links, labels in groups)
        return Response.html(html)

    def _steps_of(self, ref) -> list:
        data = self.traces.load(ref) or {}
        return list(data.get("steps") or [])

    def live(self, req: Request, session: Session) -> Response:
        panel = views.live_panel(refs=self.traces.refs(), steps_of=self._steps_of, ribbons_of=self._ribbons_of)
        return Response(200, views.live_page(**self._common(session), panel=panel).encode("utf-8"), scripted=True)

    def live_fragment(self, req: Request, session: Session) -> Response:
        return Response.html(views.live_panel(refs=self.traces.refs(), steps_of=self._steps_of,
                                              ribbons_of=self._ribbons_of))

    def api_traces(self, req: Request, session: Session) -> Response:
        return Response.json({"traces": [{"id": r.id, "group": r.group, "name": r.name, "live": r.live,
                                          "updated": r.updated, **{k: v for k, v in r.summary.items()}}
                                         for r in self.traces.refs()]})

    def api_trace(self, req: Request, session: Session, tid: str) -> Response:
        ref = self.traces.get(tid)
        data = self.traces.load(ref) if ref else None
        if data is None:
            return Response.json({"error": "no trace by that id"}, 404)
        return Response.json(data)

    def events(self, req: Request, session: Session) -> Response:
        last = req.header("Last-Event-ID")
        since = int(last) if last.isdigit() else self.bus.version
        return Response(200, b"", "text/event-stream; charset=utf-8", {"X-Accel-Buffering": "no"},
                        stream=sse(self.bus, since, keepalive_s=self.config.live_keepalive_s))

    def static_live(self, req: Request, session: Optional[Session]) -> Response:
        return Response(200, (_STATIC / "live.js").read_bytes(), "text/javascript; charset=utf-8",
                        {"Cache-Control": "no-cache"})

    def static_longview(self, req: Request, session: Optional[Session]) -> Response:
        return Response(200, (_STATIC / "longview.js").read_bytes(), "text/javascript; charset=utf-8",
                        {"Cache-Control": "no-cache"})

    def api_trace_long(self, req: Request, session: Session, tid: str) -> Response:
        """A long run for the lens: its reading, its phases, and every step as columns."""
        from ..longrun import longrun
        from .longviz import lens_payload
        ref = self.traces.get(tid)
        data = self.traces.load(ref) if ref else None
        if data is None:
            return Response.json({"error": "no trace by that id"}, 404)
        r = longrun(data, now_s=_now_on_its_clock(data) if ref.live else None)
        return Response.json(lens_payload(data, r, ident=ref.id, live=ref.live))

    # ----------------------------------------------------------------- evals
    def evolve(self, req: Request, session: Session) -> Response:
        from . import viz
        entries = [x for x in self.catalog.entries() if x.kind == "evolution"]
        rivers, datas = {}, {}
        for x in entries:
            data = self._json_file(x.path / "self-evolve.json")
            if data:
                rivers[x.id] = viz.harness_river(data)
                datas[x.id] = data
        return Response.html(views.evolve_page(**self._common(session), entries=entries, rivers=rivers, datas=datas,
                                               start=self._evolve_start()))

    def _evolve_start(self) -> str:
        """Evolve your own: the commands as this machine runs them, into the folder this hub lists."""
        import shlex
        import shutil
        import subprocess
        from .evolvecards import start_card
        if self.exporting:
            return start_card(prog="agentdiff", folder="~/.agentdiff/self-evolve", claude=None, here=False)
        prog = "agentdiff"
        if getattr(sys, "frozen", False):  # the downloaded binary: wherever it was unpacked
            prog = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
        folder = str(self.evolve_home or Path(self.config.root).resolve())
        return start_card(prog=prog, folder=folder, claude=shutil.which("claude"))

    def evals(self, req: Request, session: Session) -> Response:
        from . import viz
        # a suite on its own, and the suite inside every evolving harness
        entries = [x for x in self.catalog.entries() if x.kind in ("evals", "evolution")]
        rivers, datas = {}, {}
        for x in entries:
            if x.kind == "evals":
                data = self._json_file(x.path / "evolve-evals.json")
            else:
                data = (self._json_file(x.path / "self-evolve.json") or {}).get("evals")
            if data and data.get("lineage"):
                rivers[x.id] = viz.eval_river(data)
                datas[x.id] = data
        entries = [x for x in entries if x.id in rivers]
        return Response.html(views.evals_page(**self._common(session), entries=entries, rivers=rivers, datas=datas))

    # --------------------------------------------------------------- account
    def account(self, req: Request, session: Session, message: Optional[str] = None,
                error: Optional[str] = None, status: int = 200) -> Response:
        user = self.users.get(session.user)
        page = views.account_page(**self._common(session), role=user.role if user else "member",
                                  demo=bool(user and user.demo), expires=session.expires,
                                  sessions=self.sessions.count(session.user), message=message, error=error)
        return Response.html(page, status)

    def change_password(self, req: Request, session: Session) -> Response:
        form = req.form()
        if not self.sessions.csrf_ok(session, form.get("csrf")):
            return Response.html(self._page("Refused", "That form did not come from this hub.", session), 403)
        user = self.users.get(session.user)
        if user is None or user.demo:
            return self.account(req, session, error="The demo account's password comes from the settings.",
                                status=400)
        wait = self.throttle.wait_s(user.name)
        if wait > 0:
            return self.account(req, session, error=f"Too many wrong passwords; wait {wait:.0f}s.", status=429)
        if not check_password(form.get("current") or "", user.password_hash):
            self.throttle.failed(user.name)
            return self.account(req, session, error="The current password is not right.", status=400)
        new, again = form.get("new") or "", form.get("again") or ""
        if len(new) < 8:
            return self.account(req, session, error="A password of at least 8 characters.", status=400)
        if new != again:
            return self.account(req, session, error="The two new passwords differ.", status=400)
        self.throttle.succeeded(user.name)
        self.users.put(replace(user, password_hash=hash_password(new, self.config.password_iterations)))
        # every session of this user ends, then this one starts again
        self.sessions.end_user(user.name)
        fresh = self.sessions.create(user.name)
        resp = self.account(req, fresh, message="Password changed. Every other session of yours was signed out.")
        resp.headers["Set-Cookie"] = _cookie(SESSION_COOKIE, fresh.token, int(self.sessions.ttl_s))
        return resp

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
        live = data.get("live", False)
        if not isinstance(live, bool):
            return Response.json({"error": "live is true or false"}, 400)
        try:
            saved = self.telemetry.save(data["vector"], prompt=str(data.get("prompt") or ""), success=success,
                                        answer=str(data.get("answer") or ""), model=str(data.get("model") or ""),
                                        live=live)
        except ValueError as exc:
            return Response.json({"error": str(exc)}, 400)
        if saved.get("kept") == "this":
            self.bus.publish("telemetry", trace_id(self.traces.rel(self.telemetry.dir / "traces" / f"{saved['id']}.json")))
        return Response.json(saved, 201)

    def healthz(self, req: Request, session: Optional[Session]) -> Response:
        return Response.json({"ok": True})
