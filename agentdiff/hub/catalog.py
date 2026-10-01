"""What the hub shows: every run under its root, found on disk.

The hub keeps no database. The catalog walks the root (to a depth the
settings give) and asks each detector whether a directory is something it
knows: a duel (``duel.json``), a report (``aggregate.json`` beside
``report.html``). A directory a detector claims is not descended into, so
a duel's own page is not listed twice. The in-band telemetry agents post
is a store of its own (:mod:`.ingest`) and is listed beside them.

Detectors are a list: a new kind of run is one more detector, and nothing
here changes.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional

__all__ = ["Entry", "Catalog", "detect_duel", "detect_report", "DETECTORS"]

_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", "dist", "build"}


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    title: str
    path: Path
    updated: float
    summary: dict = field(default_factory=dict)
    page: Optional[Path] = None


def entry_id(kind: str, path: Path) -> str:
    return hashlib.sha256(f"{kind}:{path}".encode("utf-8")).hexdigest()[:12]


def _json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _duel_title(plan: dict, agents: list, d: Path) -> str:
    """The project, then what was asked: ``feature-slugify · fix · pytest -q``."""
    tasks = plan.get("tasks") or []
    # the project: the workspace when the plan names it, else the directory
    # the duel wrote into (its parent, for the default duel-out*)
    own = d.parent.name if d.name.startswith("duel-out") else d.name
    project = Path(tasks[0]["workspace"]).name if tasks and tasks[0].get("workspace") else own
    if not tasks:
        return f"{project} · {' vs '.join(agents) or d.name}"
    t = tasks[0]
    if t.get("id") == "fix" and t.get("check"):
        what = f"fix · {t['check']}"
    else:
        first = (t.get("prompt") or "").strip().splitlines()
        what = first[0] if first else t.get("id", "task")
    more = f" (+{len(tasks) - 1} task(s))" if len(tasks) > 1 else ""
    text = f"{project} · {what}{more}"
    return text if len(text) <= 96 else text[:95] + "…"


def detect_duel(d: Path, root: Path) -> Optional[Entry]:
    report = _json(d / "duel.json") if (d / "duel.json").is_file() else None
    if report is None:
        return None
    per = report.get("per_agent") or {}
    agents = list(report.get("agents") or per)
    rows = []
    for a in agents:
        p = per.get(a) or {}
        rows.append({"agent": a, "passed": p.get("passed"), "graded": p.get("graded"),
                     "cost_usd": p.get("cost_usd"), "tokens": (p.get("median") or {}).get("tokens"),
                     "wall_s": (p.get("median") or {}).get("wall_s")})
    plan = _json(d / "plan.json") or {}
    title = _duel_title(plan, agents, d)
    page = d / "page" / "report.html"
    return Entry(entry_id("duel", d.relative_to(root)), "duel", title, d, _mtime(d / "duel.json"),
                 {"agents": agents, "rows": rows, "tasks": report.get("tasks") or [], "runs": report.get("runs"),
                  "measurable": report.get("measurable"), "unequal": report.get("unequal") or [],
                  "narrative": report.get("narrative") or report.get("reason") or "",
                  "already_passing": (report.get("baseline") or {}).get("already_passing") or []},
                 page if page.is_file() else None)


def detect_report(d: Path, root: Path) -> Optional[Entry]:
    if not ((d / "aggregate.json").is_file() and (d / "report.html").is_file()):
        return None
    agg = _json(d / "aggregate.json") or {}
    agents = agg.get("agents")
    names = list(agents) if isinstance(agents, (list, dict)) else []
    return Entry(entry_id("report", d.relative_to(root)), "report",
                 " vs ".join(str(n) for n in names[:2]) or d.name, d, _mtime(d / "aggregate.json"),
                 {"agents": names, "tasks": agg.get("tasks")}, d / "report.html")


#: in order: the first detector to claim a directory owns it
DETECTORS: List[Callable[[Path, Path], Optional[Entry]]] = [detect_duel, detect_report]


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


class Catalog:
    def __init__(self, root: Path, *, depth: int, limit: int,
                 detectors: Optional[Iterable[Callable[[Path, Path], Optional[Entry]]]] = None,
                 extra: Optional[Callable[[], List[Entry]]] = None) -> None:
        self.root = Path(root).resolve()
        self.depth, self.limit = depth, limit
        self.detectors = list(detectors or DETECTORS)
        self.extra = extra

    def entries(self) -> List[Entry]:
        found: List[Entry] = []
        for dirpath, dirnames, _ in os.walk(self.root):
            d = Path(dirpath)
            level = len(d.relative_to(self.root).parts)
            claimed = None
            for detect in self.detectors:
                claimed = detect(d, self.root)
                if claimed:
                    break
            if claimed:
                found.append(claimed)
                dirnames[:] = []
            else:
                dirnames[:] = sorted(n for n in dirnames if n not in _SKIP and not n.startswith("."))
            if level >= self.depth:
                dirnames[:] = []
            if len(found) >= self.limit:
                break
        if self.extra:
            found.extend(self.extra())
        return sorted(found, key=lambda e: e.updated, reverse=True)[: self.limit]

    def get(self, entry_id_: str) -> Optional[Entry]:
        return next((e for e in self.entries() if e.id == entry_id_), None)

    def inside(self, path: Path) -> bool:
        """Whether ``path`` is under the root: nothing outside it is ever served."""
        try:
            Path(path).resolve().relative_to(self.root)
            return True
        except ValueError:
            return False
