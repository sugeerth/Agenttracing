"""Every trace under the hub's root, one at a time.

A *run* (:mod:`.catalog`) is a directory: a duel, a report, a suite of
evals. A *trace* is one agent's attempt at one task, a SCHEMA file, and a
run holds many of them. The index finds every ``traces/`` directory under
the root, and the telemetry store's own, and lists the files in them:

- ``<name>.json`` a finished trace
- ``<name>.live.json`` a trace still being written, frame by frame, by the
  harness while the agent runs (listed only until its final file lands)

A trace's id is a hash of its path under the root, so a link stays put as
the run around it grows, and a live trace keeps its id when it finishes.
Summaries are read once per file version (its size and mtime) and kept;
the loop analysis (:mod:`agentdiff.laps`) runs only for the page that
asks for it.

Nothing outside the root is ever read: a ``traces`` directory reached
through a link that leaves the root is skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = ["TraceRef", "TraceIndex", "LIVE_SUFFIX", "trace_id", "load_record", "record_paths"]

LIVE_SUFFIX = ".live.json"
_NOT_TRACES = {"RUN_MANIFEST.json", "aggregate.json", "plan.json", "duel.json"}
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", "dist", "build"}


def trace_id(rel: str) -> str:
    """The id of the trace at ``rel`` (its path under the root, live suffix
    removed): the same before and after it finishes."""
    stem = rel[: -len(LIVE_SUFFIX)] + ".json" if rel.endswith(LIVE_SUFFIX) else rel
    return hashlib.sha256(f"trace:{stem}".encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class TraceRef:
    id: str
    path: Path
    #: the directory it is in, under the root (``demo/loops/traces``)
    group: str
    live: bool
    updated: float
    summary: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        n = self.path.name
        return n[: -len(LIVE_SUFFIX)] if n.endswith(LIVE_SUFFIX) else n[: -len(".json")]


def _is_trace_file(name: str) -> bool:
    return name.endswith(".json") and name not in _NOT_TRACES and not name.startswith(("report_", ".")) \
        and not name.endswith((".meta.json", ".tmp"))


def record_paths(path: Path) -> tuple:
    """The harness's record of a trace and its patch, when it wrote them:
    ``<run dir>/records/<stem>.json`` beside ``<run dir>/traces/<stem>.json``."""
    name = path.name
    stem = name[: -len(LIVE_SUFFIX)] if name.endswith(LIVE_SUFFIX) else name[: -len(".json")]
    base = path.parent.parent
    rec = base / "records" / f"{stem}.json"
    return rec, base


def load_record(path: Path) -> tuple:
    """(record, patch text) for a trace; (None, "") when the harness wrote none."""
    rec_path, base = record_paths(path)
    try:
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ""
    patch = ""
    p = rec.get("patch") if isinstance(rec, dict) else None
    if isinstance(p, str) and ".." not in Path(p).parts:
        try:
            patch = (base / p).read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            patch = ""
    return (rec if isinstance(rec, dict) else None), patch


def _insight(data: dict, path: Path) -> Optional[dict]:
    """What a list needs of a run's meaning: how it failed, the fix it points at, what code it left."""
    from ..insight import agent_fix, code_change
    from ..timeline import timeline
    try:
        tl = timeline(data)
    except (ValueError, KeyError, TypeError):
        return None
    kind = (tl.get("look_here") or {}).get("kind")
    fix = agent_fix(data, look_kind=kind)
    rec, _ = load_record(path)
    change = code_change(rec) if rec else None
    return {"look_kind": kind, "look": (tl.get("look_here") or {}).get("sentence"),
            "fix_rule": (fix or {}).get("rule"), "fix_change": (fix or {}).get("change"),
            "lines": (change["added"] + change["removed"]) if change else None,
            "files": len(change["files"]) if change else None,
            "tests_edited": bool(change and change["tests"]), "flags": [f["kind"] for f in (change or {}).get("flags") or []]}


def summarize(data: dict) -> dict:
    """What a list shows of a trace: never its content, only its shape."""
    steps = data.get("steps") or []
    tokens = sum(int(s.get("tokens") or 0) for s in steps if isinstance(s, dict))
    seconds = sum(float(s.get("latency_s") or 0) for s in steps if isinstance(s, dict))
    total = (data.get("totals") or {}).get("latency_s") if isinstance(data.get("totals"), dict) else None
    if isinstance(total, (int, float)) and total > seconds:
        seconds = float(total)      # the run's own clock: model time included, not only the tools'
    errors = sum(1 for s in steps if isinstance(s, dict) and s.get("error"))
    outcome = data.get("outcome") or {}
    task = data.get("task") or {}
    agent = data.get("agent") or {}
    return {"task": str(task.get("id") or "task") if isinstance(task, dict) else "task",
            "agent": str(agent.get("name") or "agent") if isinstance(agent, dict) else "agent",
            "model": (agent.get("model") if isinstance(agent, dict) else None),
            "steps": len(steps), "tokens": tokens, "seconds": round(seconds, 3), "errors": errors,
            "success": outcome.get("success") if isinstance(outcome, dict) else None,
            "in_progress": bool(data.get("in_progress")), "elapsed_s": data.get("elapsed_s"),
            "run": data.get("run"), "laps": _laps(data)}


def _laps(data: dict) -> Optional[dict]:
    from ..laps import laps
    from .viz import compact_laps
    try:
        return compact_laps(laps(data))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


class TraceIndex:
    #: how long the list of trace directories is trusted before the root is walked again
    RESCAN_S = 5.0

    def __init__(self, root: Path, *, depth: int, limit: int, extra_dirs: Iterable[Path] = ()) -> None:
        self.root = Path(root).resolve()
        self.depth, self.limit = depth, limit
        self.extra_dirs = [Path(d) for d in extra_dirs]
        self._cache: Dict[str, Tuple[tuple, dict]] = {}
        self._dirs: Tuple[float, List[Path]] = (-1e9, [])
        self._lock = threading.Lock()

    # ------------------------------------------------------------- finding
    def directories(self) -> List[Path]:
        at, dirs = self._dirs
        if time.monotonic() - at < self.RESCAN_S:
            return list(dirs) + [d for d in self.extra_dirs if d.is_dir() and d not in dirs]
        dirs = self._walk()
        self._dirs = (time.monotonic(), dirs)
        return dirs

    def guarded(self) -> List[Path]:
        """The projects whose Claude Code sessions ``agentdiff guard`` watches
        (each has ``.agentdiff/guard``), the root among them."""
        out = [d.parent.parent for d in self.directories() if d.parent.name == ".agentdiff"]
        if (self.root / ".agentdiff" / "guard").is_dir() and self.root not in out:
            out.insert(0, self.root)
        return [p for p in dict.fromkeys(out) if (p / ".agentdiff" / "guard").is_dir()]

    def _walk(self) -> List[Path]:
        found: List[Path] = []
        for dirpath, dirnames, _ in os.walk(self.root):
            d = Path(dirpath)
            level = len(d.relative_to(self.root).parts)
            if d.name == "traces":
                found.append(d)
                dirnames[:] = []
                continue
            # a trace directory sits a level or two below a run the catalog finds
            dirnames[:] = [] if level >= self.depth + 2 else sorted(
                n for n in dirnames if n not in _SKIP and (not n.startswith(".") or n == ".agentdiff"))
        for d in self.extra_dirs:
            if d.is_dir() and d not in found:
                found.append(d)
        return [d for d in found if self._inside(d)]

    def _inside(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def rel(self, path: Path) -> str:
        """A path as the index names it: under the root."""
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def refs(self) -> List[TraceRef]:
        out: List[TraceRef] = []
        for d in self.directories():
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            finals = {n for n in names if _is_trace_file(n) and not n.endswith(LIVE_SUFFIX)}
            for n in names:
                if not _is_trace_file(n):
                    continue
                live = n.endswith(LIVE_SUFFIX)
                if live and (n[: -len(LIVE_SUFFIX)] + ".json") in finals:
                    continue            # it finished: the final file is the trace now
                p = d / n
                ref = self._ref(p, live)
                if ref is not None:
                    out.append(ref)
        out.sort(key=lambda r: (not r.live, -r.updated, r.group, r.name))
        return out[: self.limit]

    def _ref(self, path: Path, live: bool) -> Optional[TraceRef]:
        try:
            st = path.stat()
        except OSError:
            return None
        rel = self.rel(path)
        key = (st.st_size, st.st_mtime_ns)
        with self._lock:
            hit = self._cache.get(rel)
        if hit and hit[0] == key:
            summary = hit[1]
        else:
            data = self._load(path)
            if data is None:
                return None
            summary = summarize(data)
            summary["insight"] = _insight(data, path)
            with self._lock:
                self._cache[rel] = (key, summary)
        return TraceRef(trace_id(rel), path, self.rel(path.parent), live or bool(summary.get("in_progress")),
                        st.st_mtime, summary)

    @staticmethod
    def _load(path: Path) -> Optional[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            return None
        return data

    def get(self, tid: str) -> Optional[TraceRef]:
        return next((r for r in self.refs() if r.id == tid), None)

    def load(self, ref: TraceRef) -> Optional[dict]:
        """The whole trace, for its own page; None when it vanished or is not one."""
        if not self._inside(ref.path):
            return None
        data = self._load(ref.path)
        if data is None and ref.live:
            # the final may have just replaced the live frame
            data = self._load(ref.path.with_name(ref.name + ".json"))
        return data

    def signature(self) -> Dict[str, tuple]:
        """Each trace's id and file version: the live poller compares two of
        these to say which traces were added, grew or finished."""
        sig: Dict[str, tuple] = {}
        for d in self.directories():
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                if not _is_trace_file(n):
                    continue
                try:
                    st = (d / n).stat()
                except OSError:
                    continue
                tid = trace_id(self.rel(d / n))
                sig[tid] = tuple(sorted(set(sig.get(tid, ())) | {(n, st.st_size, st.st_mtime_ns)}))
        return sig
