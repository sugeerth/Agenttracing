"""Watch a trace directory and serve the report page live.

``deepcompare watch traces/`` starts a local HTTP server that renders the
same self-contained page ``batch`` writes, plus one thing a file cannot
do: it keeps the page current while agents are running. The recorder's
``stream=True`` writes the run-so-far to ``<trace>.live.json`` after every
step; this watcher polls the directory, rebuilds the payload when anything
changes — finished traces become reports through the ordinary engine,
running ones are handed over as they are, marked ``in_progress`` — and
pushes it to every open page over server-sent events. The page animates
what arrives and, when a pair finishes, the full story replaces the
stream.

Honesty rules: a running trace is never analysed (a diagnosis on half a
run would flip around as it grows); it is shown, with its step count, its
tokens so far and its last steps. Nothing leaves the machine: the server
binds to localhost by default and serves only this directory's traces.

``--demo`` replays the demo traces as if two agents were running them —
one step every ``--pace`` seconds — so the live view can be seen without
a model. This module is the only place a server lives, and it lives in
the harness package because that is the network boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import hmac
import ipaddress
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from typing import Callable, Optional, Union

from .. import Trajectory, compare
from ..metrics import aggregate as build_aggregate
from ..report import render_html

LIVE_SUFFIX = ".live.json"
SKIP_NAMES = {"RUN_MANIFEST.json", "aggregate.json"}


def _is_trace(path: Path) -> bool:
    return (path.suffix == ".json" and not path.name.endswith(LIVE_SUFFIX)
            and path.name not in SKIP_NAMES and not path.name.startswith("report_"))


def _agent_of(name: str) -> Optional[str]:
    parts = name.split("__")
    return parts[1] if len(parts) >= 2 else None


def _run_of(name: str, raw: dict):
    """A run's number: the one it carries, else the ``__r<n>`` its file
    name ends in, else None (a single run). ``"r2"`` and ``2`` are both 2."""
    def number(v):
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        v = str(v)
        return int(v[1:]) if len(v) > 1 and v[0] == "r" and v[1:].isdigit() else None
    if raw.get("run") is not None:
        return number(raw["run"]) if number(raw["run"]) is not None else raw["run"]
    stem = name[:-len(LIVE_SUFFIX)] if name.endswith(LIVE_SUFFIX) else Path(name).stem
    return number(stem.rsplit("__", 1)[-1])


def _run_order(run) -> tuple:
    return (0, run, "") if isinstance(run, int) else (1, 0, str(run or ""))


class Watcher:
    """Turns a directory of (possibly still growing) traces into the
    page payload, and knows when it changed."""

    def __init__(self, traces_dir: Union[str, Path], poll: float = 0.5, db: Optional[Union[str, Path]] = None,
                 golden: Optional[dict] = None, policy: Optional[dict] = None,
                 enrich: Optional[Callable[[], dict]] = None, also: Optional[list] = None) -> None:
        self.dir = Path(traces_dir)
        # directories whose changes also re-run the analysis (a duel's
        # records land just after its traces)
        self.also = [Path(d) for d in (also or [])]
        # the full analysis (`deepcompare.corpus.analyse`) runs over the
        # finished traces, once per change to that set — not on every live
        # frame, which arrives several times a second while agents work
        self.golden = golden
        self.policy = policy
        self.enrich = enrich
        self._finals_key: Optional[tuple] = None
        self._analysis: tuple = ([], {"tasks": 0}, [])
        self.poll = poll
        self.db_path = Path(db) if db else None
        self._ingested: set = set()
        self.version = 0
        self._signature: Optional[dict] = None
        self._payload: Optional[dict] = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._stop = threading.Event()
        self.errors: list = []

    # ------------------------------------------------------------ scanning

    def _also_key(self) -> list:
        out = []
        for d in self.also:
            if d.is_dir():
                for path in sorted(d.glob("*.json")):
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    out.append((f"{d.name}/{path.name}", st.st_mtime_ns, st.st_size))
        return out

    def signature(self) -> dict:
        sig = {}
        if not self.dir.is_dir():
            return sig
        for path in self.dir.iterdir():
            if path.is_file() and path.suffix == ".json" and path.name not in SKIP_NAMES:
                try:
                    st = path.stat()
                except OSError:
                    continue
                sig[path.name] = (st.st_mtime_ns, st.st_size)
        for name, mtime, size in self._also_key():
            sig[name] = (mtime, size)
        return sig

    def build(self) -> dict:
        """The payload: reports for every finished pair, live runs for
        every trace still being written, and the live block itself."""
        finals: dict = {}      # task -> agent -> Trajectory
        every_final: list = []   # every finished run, every repeat of it
        finals_key: list = []
        lives: list = []
        self.errors = []
        for path in sorted(self.dir.glob("*.json")):
            if path.name in SKIP_NAMES or path.name.startswith("report_"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue   # half-written: the next poll sees it whole
            if path.name.endswith(LIVE_SUFFIX):
                task = ((data.get("task") or {}).get("id")) or path.name.split("__")[0]
                agent = ((data.get("agent") or {}).get("name")) or _agent_of(path.stem) or "agent"
                if self.db_path is not None:
                    key = (path.name, len(data.get("steps") or []))
                    if key not in self._ingested:
                        try:
                            from ..tracedb import TraceDB
                            with TraceDB(self.db_path) as store:
                                store.checkpoint(data, source="watch", recorded_at=path.stat().st_mtime)
                            self._ingested.add(key)
                        except Exception as exc:  # noqa: BLE001
                            self.errors.append(f"db checkpoint: {path.name}: {exc}")
                vendor = data.get("vendor") or {}
                lives.append({"task": task, "agent": agent, "file": path.name, "run": _run_of(path.name, data),
                              "steps": data.get("steps") or [], "in_progress": True,
                              "updated_at": data.get("updated_at"),
                              "model": (data.get("agent") or {}).get("model"),
                              "run_id": data.get("run_id"),
                              # what a race needs as it runs: the clock, the
                              # spend so far and its basis, and the vendor's cost
                              "totals": data.get("totals") or {},
                              "elapsed_s": data.get("elapsed_s"),
                              "tokens_basis": (data.get("token_accounting") or {}).get("basis"),
                              "vendor": {"name": vendor.get("name"), "cost_usd": vendor.get("cost_usd")}
                              if vendor else None})
                continue
            try:
                traj = Trajectory.from_dict(data)
            except ValueError as exc:
                self.errors.append(f"{path.name}: {exc}")
                continue
            finals.setdefault(traj.task.id, {})[traj.agent.name] = traj
            every_final.append((path.name, traj, data))
            try:
                st = path.stat()
                finals_key.append((path.name, st.st_mtime_ns, st.st_size))
            except OSError:
                finals_key.append((path.name, 0, 0))
            if self.db_path is not None and path.name not in self._ingested:
                try:
                    from ..tracedb import TraceDB
                    with TraceDB(self.db_path) as store:
                        store.add(data, source="watch", recorded_at=path.stat().st_mtime)
                    self._ingested.add(path.name)
                except Exception as exc:  # noqa: BLE001 — the stream must not stop for the store
                    self.errors.append(f"db: {path.name}: {exc}")
        # a live file whose final exists is stale: drop it. The final is the
        # file of the same name, so a repeat of a finished run still streams
        done = {name[:-len(".json")] for name, _, _ in every_final}
        lives = [r for r in lives if r["file"][:-len(LIVE_SUFFIX)] not in done]

        agents_seen: list = []
        for task in sorted(finals):
            for agent in finals[task]:
                if agent not in agents_seen:
                    agents_seen.append(agent)
        pair = sorted(agents_seen)[:2]
        key = tuple(sorted(finals_key + self._also_key()))
        if key != self._finals_key:
            self._finals_key = key
            self._analysis = self._analyse(sorted(every_final, key=lambda x: x[0]))
        # the analysis's own errors stay on every frame it serves, not only the first
        reports, agg, analysis_errors = self._analysis
        self.errors.extend(analysis_errors)
        def compact(name, traj, raw):
            # a finished run, small enough to send on every update: the race
            # draws an agent that is done beside one still running
            t, a = traj.task.id, traj.agent.name
            steps = [{k: s.get(k) for k in ("type", "name", "started_s", "latency_s", "error", "tokens",
                                            "tokens_basis", "effect", "span") if s.get(k) is not None}
                     | {"input": str(s.get("input") or "")[:160]}
                     for s in raw.get("steps") or []]
            vendor = raw.get("vendor") or {}
            return {"task": t, "agent": a, "file": name, "run": _run_of(name, raw),
                    "success": traj.outcome.success, "steps": len(traj.steps), "trace": steps,
                    "totals": raw.get("totals") or {}, "note": (raw.get("outcome") or {}).get("note"),
                    "run_id": raw.get("run_id"),
                    "tokens_basis": (raw.get("token_accounting") or {}).get("basis"),
                    "vendor": {"name": vendor.get("name"), "cost_usd": vendor.get("cost_usd")} if vendor else None}
        # every finished run, every repeat of it, in run order
        finished = [compact(*f) for f in sorted(every_final, key=lambda f: (
            f[1].task.id, f[1].agent.name, _run_order(_run_of(f[0], f[2])), f[0]))]
        return {
            "reports": reports,
            "aggregate": agg,
            "live": {
                "enabled": True, "events": "/events", "version": self.version,
                "generated_at": time.time(), "agents": pair,
                "runs": sorted(lives, key=lambda r: (r["task"], r["agent"])),
                "finished": finished,
                "errors": list(self.errors),
                "directory": str(self.dir),
            },
        }

    def _analyse(self, finals: list) -> tuple:
        """``(reports, aggregate, errors)``: the same reading `batch` writes,
        over the runs finished so far."""
        from ..corpus import analyse
        errors: list = []
        trajectories = [t for _, t, _ in finals]
        outcomes = {}
        # as `commands._io.outcomes_from` reads them for `batch`
        for name, t, data in finals:
            if "outcome" not in data:
                continue
            key = data.get("trace_id") or Path(name).stem
            outcomes[key] = {"outcome": data["outcome"]}
            outcomes.setdefault(Path(name).stem, outcomes[key])
        try:
            result = analyse(trajectories, golden=self.golden, policy=self.policy, outcomes=outcomes)
            reports, agg = result["reports"], result["aggregate"]
        except ValueError as exc:
            # one agent finished, or no task has both yet: say so, keep streaming
            reports, agg = [], {"tasks": 0, "pending": str(exc)}
        except Exception as exc:   # noqa: BLE001 — the analysis must not stop the stream
            errors.append(f"analysis: {exc}")
            reports, agg = [], {"tasks": 0}
        if self.enrich is not None:
            try:
                agg.update(self.enrich() or {})
            except Exception as exc:   # noqa: BLE001
                errors.append(f"enrich: {exc}")
        return reports, agg, errors

    # ------------------------------------------------------------ the loop

    def refresh(self, force: bool = False) -> bool:
        sig = self.signature()
        with self._lock:
            if not force and sig == self._signature:
                return False
            self._signature = sig
        payload = self.build()
        with self._lock:
            self.version += 1
            payload["live"]["version"] = self.version
            self._payload = payload
            self._changed.notify_all()
        return True

    def payload(self) -> dict:
        with self._lock:
            if self._payload is None:
                self._lock.release()
                try:
                    self.refresh(force=True)
                finally:
                    self._lock.acquire()
            return self._payload  # type: ignore[return-value]

    def wait_for_change(self, since: int, timeout: float) -> Optional[dict]:
        """Block until the version passes ``since`` (or ``timeout``)."""
        with self._lock:
            if self.version > since:
                return self._payload
            self._changed.wait(timeout)
            return self._payload if self.version > since else None

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as exc:   # noqa: BLE001 — keep watching
                self.errors.append(str(exc))
            self._stop.wait(self.poll)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._changed.notify_all()


# ---------------------------------------------------------------- the demo

def simulate(source: Union[str, Path], out_dir: Union[str, Path], pace: float = 0.4,
             loop: bool = False, stop: Optional[threading.Event] = None) -> None:
    """Replay the traces under ``source`` into ``out_dir`` as if their agents
    were running now: for each task, both agents' steps arrive one at a
    time (``pace`` seconds apart) as ``.live.json``, then the finals land
    and the live files go. With ``loop`` the show starts over."""
    src = Path(source)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    traces = sorted(p for p in src.glob("*.json") if _is_trace(p))
    by_task: dict = {}
    for path in traces:
        data = json.loads(path.read_text(encoding="utf-8"))
        by_task.setdefault(data["task"]["id"], []).append((path, data))
    stop = stop or threading.Event()

    def write(path: Path, data: dict) -> None:
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    while not stop.is_set():
        for task in sorted(by_task):
            runs = by_task[task]
            longest = max(len(d["steps"]) for _, d in runs)
            for k in range(1, longest + 1):
                if stop.is_set():
                    return
                for path, data in runs:
                    if k > len(data["steps"]) or k == len(data["steps"]):
                        continue   # the answer step arrives with the final
                    partial = dict(data)
                    partial["steps"] = data["steps"][:k]
                    partial["outcome"] = {"success": False, "answer": "", "score": None, "termination": None}
                    partial["in_progress"] = True
                    partial["updated_at"] = time.time()
                    write(out / (path.stem + LIVE_SUFFIX), partial)
                stop.wait(pace)
            for path, data in runs:
                write(out / path.name, data)
                live = out / (path.stem + LIVE_SUFFIX)
                if live.exists():
                    live.unlink()
            stop.wait(pace * 2)
        if not loop:
            return
        # start over: clear the finals so the pairs stream again
        stop.wait(pace * 4)
        for path in out.glob("*.json"):
            path.unlink()


# ---------------------------------------------------------------- serving

def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bind_policy(host: str, allow_remote: bool = False) -> Optional[str]:
    """The token a server bound to ``host`` requires, or None on loopback.

    The page carries every trace in the directory — tool outputs, file
    contents, whatever the agents read — so serving it beyond this machine
    is an explicit choice (``--allow-remote``), and then every request must
    present a random token the operator was shown once. Refuses otherwise.
    """
    if is_loopback(host):
        return None
    if not allow_remote:
        raise ValueError(f"refusing to serve on {host!r}: the page carries every trace in the directory, "
                         f"including what the agents read. Bind to 127.0.0.1, or pass --allow-remote to "
                         f"serve it beyond this machine behind a random token.")
    return secrets.token_urlsafe(24)


class _Handler(BaseHTTPRequestHandler):
    watcher: Watcher = None  # type: ignore[assignment]
    template: Path = None    # type: ignore[assignment]
    quiet = True
    token: Optional[str] = None

    def _authorised(self) -> bool:
        """A server with no token is loopback-only; one with a token wants
        it on the query (once) or in the cookie that request set."""
        if not self.token:
            return True
        query = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        if query and hmac.compare_digest(query, self.token):
            self._set_cookie = True
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "agentdiff_token" and hmac.compare_digest(value, self.token):
                return True
        return False

    def log_message(self, fmt, *args):  # noqa: D401 — quiet by default
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if getattr(self, "_set_cookie", False):
            self.send_header("Set-Cookie", f"agentdiff_token={self.token}; HttpOnly; SameSite=Strict; Path=/")

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        self._set_cookie = False
        if not self._authorised():
            self._send(403, b"forbidden: this page is served behind a token; open the URL the command printed",
                       "text/plain; charset=utf-8")
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/report.html"):
            payload = self.watcher.payload()
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "report.html"
                render_html(payload["reports"], payload["aggregate"], self.template, out,
                            extra={"live": payload["live"]})
                body = out.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/data.json":
            body = json.dumps(self.watcher.payload(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/events":
            self._events()
        else:
            self._send(404, b"not found", "text/plain")

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self._security_headers()
        self.end_headers()
        since = -1
        try:
            while True:
                payload = self.watcher.wait_for_change(since, timeout=15.0)
                if payload is None:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                since = payload["live"]["version"]
                data = json.dumps(payload, ensure_ascii=False)
                self.wfile.write(("id: %d\nevent: report\ndata: %s\n\n" % (since, data)).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def serve(traces_dir: Union[str, Path], template: Union[str, Path], *,
          host: str = "127.0.0.1", port: int = 8765, poll: float = 0.5,
          demo: Optional[Union[str, Path]] = None, pace: float = 0.4, loop: bool = False,
          ready: Optional[threading.Event] = None, stop: Optional[threading.Event] = None,
          quiet: bool = True, db: Optional[Union[str, Path]] = None,
          token: Optional[str] = None, golden: Optional[dict] = None, policy: Optional[dict] = None,
          enrich: Optional[Callable[[], dict]] = None, also: Optional[list] = None) -> ThreadingHTTPServer:
    """Serve the live page. Returns the server after it has started (so a
    caller can ``serve_forever`` on it or stop it); with ``demo`` a
    simulator thread streams those traces into ``traces_dir``."""
    traces_dir = Path(traces_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    watcher = Watcher(traces_dir, poll=poll, db=db, golden=golden, policy=policy, enrich=enrich, also=also)
    stop = stop or threading.Event()
    threading.Thread(target=watcher.run, name="deepcompare-watch", daemon=True).start()
    if demo is not None:
        threading.Thread(target=simulate, args=(demo, traces_dir, pace, loop, stop),
                         name="deepcompare-demo", daemon=True).start()

    handler = type("Handler", (_Handler,), {"watcher": watcher, "template": Path(template), "quiet": quiet,
                                            "token": token})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.watcher = watcher  # type: ignore[attr-defined]
    server.stop_event = stop  # type: ignore[attr-defined]
    server.token = token  # type: ignore[attr-defined]

    def shutdown() -> None:
        stop.set()
        watcher.stop()
        server.shutdown()
        server.server_close()
    server.shutdown_all = shutdown  # type: ignore[attr-defined]
    if ready is not None:
        ready.set()
    return server


def clear_demo_dir(path: Union[str, Path]) -> Path:
    """A fresh directory for a demo stream (never the user's traces)."""
    out = Path(path) if path else Path(tempfile.mkdtemp(prefix="agentdiff-live-"))
    if out.exists() and any(out.iterdir()):
        for p in out.glob("*.json"):
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    return out


__all__ = ["Watcher", "serve", "simulate", "clear_demo_dir", "LIVE_SUFFIX"]
_ = shutil  # kept for callers that copy traces before streaming them
