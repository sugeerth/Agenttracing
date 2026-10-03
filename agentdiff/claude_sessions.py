"""Your Claude Code sessions, read where Claude Code keeps them.

Claude Code writes each session to ``~/.claude/projects/<project>/<id>.jsonl``
(under ``$CLAUDE_CONFIG_DIR`` when that is set), and each sub-agent it
started to ``<id>/subagents/agent-<agent>.jsonl`` beside a ``.meta.json``
that names its type and what it was asked. :func:`sync` turns each session
into one SCHEMA trace in a folder the hub serves:

    <dest>/<project>/traces/<id>__claude-code.json

- **Named by what you asked.** The task is the project and your first
  prompt. A context summary, a slash command, its output or a system
  notice is not a prompt.
- **Every ask.** ``turns`` lists each prompt you typed with the step it
  came before, so a session of many asks reads as many.
- **Sub-agents as lanes.** A sub-agent's steps join the session at the
  times they ran, each under ``span.agent``: the timeline draws one lane
  per agent.
- **Not graded.** Nothing graded a session, so it is recorded as
  ungraded, never as a failure (``agentdiff guard --check`` grades the
  sessions after it).
- **Live.** A session written to in the last :data:`LIVE_S` seconds is
  written as ``.live.json`` and replaced by its final file when it goes
  quiet; a large one is read again at most every :func:`_every` seconds.
- **Once.** A session is read again only when its files changed (their
  sizes and times, kept in ``<dest>/.index.json``).

Nothing leaves the machine: the traces are files beside the hub, which
serves on 127.0.0.1 unless told otherwise.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .claude_code import read_transcript, transcript_to_trajectory

__all__ = ["Session", "projects_dir", "find", "prompt_text", "convert", "sync", "Follower", "LIVE_S"]

#: a session written to this recently is still going
LIVE_S = 300.0
_NOT_PROMPT = ("<command-", "<local-command", "Caveat:", "[Request interrupted", "<system-reminder>",
               "<task-notification>", "<bash-", "<user-memory-input>")
AGENT = "claude-code"


def projects_dir() -> Path:
    """Where Claude Code keeps its sessions on this machine."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "projects"


@dataclass
class Session:
    path: Path
    sid: str
    subagents: List[Path] = field(default_factory=list)
    size: int = 0
    mtime: float = 0.0

    @property
    def sig(self) -> List[float]:
        return [self.size, round(self.mtime, 3), len(self.subagents)]


def find(projects: Optional[Path] = None) -> List[Session]:
    """Every session under ``projects``, the most recently written first."""
    root = Path(projects) if projects else projects_dir()
    out: List[Session] = []
    try:
        project_dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return out
    for d in project_dirs:
        try:
            files = [f for f in d.iterdir() if f.suffix == ".jsonl" and f.is_file()]
        except OSError:
            continue
        for f in files:
            subs_dir = d / f.stem / "subagents"
            subs = sorted(subs_dir.glob("agent-*.jsonl")) if subs_dir.is_dir() else []
            try:
                stats = [p.stat() for p in [f] + subs]
            except OSError:
                continue
            out.append(Session(path=f, sid=f.stem, subagents=subs, size=sum(s.st_size for s in stats),
                               mtime=max(s.st_mtime for s in stats)))
    return sorted(out, key=lambda s: -s.mtime)


def prompt_text(entry: dict) -> Optional[str]:
    """What a person typed, when this entry is that; None for everything else."""
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return None
    if entry.get("isCompactSummary") or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text or text.startswith(_NOT_PROMPT):
        return None
    return text


def _when(entry: dict) -> Optional[float]:
    from .claude_code import _when as parse
    return parse(entry.get("timestamp"))


def _project(entries: List[dict], session: Session) -> str:
    cwd = next((e.get("cwd") for e in entries if isinstance(e, dict) and e.get("cwd")), None)
    if cwd:
        return Path(str(cwd)).name or str(cwd)
    return session.path.parent.name.strip("-").split("-")[-1] or "project"


def _short(text: str, most: int = 60) -> str:
    one = " ".join(text.split())
    return one if len(one) <= most else one[: most - 1].rstrip(" .,;:") + "…"


def _task_id(text: str) -> str:
    """A task id is a file name part: no path separator, no double underscore."""
    text = text.replace("/", "\u2215").replace("\\", "\u2215")
    while "__" in text:
        text = text.replace("__", "_")
    return text


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60] or "project"


def _subagent(path: Path, task: str) -> Optional[dict]:
    meta: dict = {}
    try:
        meta = json.loads(path.with_name(path.name[: -len(".jsonl")] + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    entries = read_transcript(path)
    if not entries:
        return None
    ask = next((t for t in (_sub_prompt(e) for e in entries) if t), None)
    data = transcript_to_trajectory(entries, task=task, agent=AGENT, prompt=ask or "(sub-agent)")
    kind = str(meta.get("agentType") or "sub-agent")
    what = str(meta.get("description") or "").strip()
    data["_label"] = _short(what or kind, 40)         # what it was asked names it; its type is on every lane
    data["_id"] = path.stem.replace("agent-", "")
    return data


def _sub_prompt(entry: dict) -> Optional[str]:
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return None
    content = (entry.get("message") or {}).get("content") if isinstance(entry.get("message"), dict) else None
    return content.strip() if isinstance(content, str) and content.strip() else None


def convert(session: Session, *, now: Optional[float] = None) -> Optional[dict]:
    """One session, its sub-agents merged in, as a trace; None when it holds no step."""
    entries = read_transcript(session.path)
    if not any(isinstance(e, dict) and e.get("type") == "assistant" for e in entries):
        return None             # nothing the agent did: an empty or unreadable file
    asks = [(_when(e), t) for e in entries for t in [prompt_text(e)] if t]
    first = asks[0][1] if asks else None
    project = _project(entries, session)
    task = _task_id(f"{project} · {_short(first)}" if first else project)
    data = transcript_to_trajectory(entries, task=task, agent=AGENT, prompt=first or "(no prompt typed)",
                                    run_id=session.sid[:8])
    if not data.get("steps"):
        return None
    t0 = data.get("started_at")
    merged = [(t0 + float(s.get("started_s") or 0) if t0 is not None else None, 0, i, s)
              for i, s in enumerate(data["steps"])]
    subs = 0
    for path in session.subagents:
        try:
            sub = _subagent(path, task)
        except (OSError, ValueError):
            sub = None
        if not sub or not sub.get("steps") or sub.get("started_at") is None or t0 is None:
            continue
        subs += 1
        for j, s in enumerate(sub["steps"]):
            if s.get("type") == "answer":
                s = dict(s, type="reason", name="sub-agent answer")
            s = dict(s, span={"id": sub["_id"], "agent": sub["_label"], "parent": "root"})
            merged.append((sub["started_at"] + float(s.get("started_s") or 0), 1, j, s))
    if subs:
        main_n = len(data["steps"])
        answer = merged.pop(main_n - 1) if data["steps"][-1].get("type") == "answer" else None
        merged.sort(key=lambda m: (m[0] if m[0] is not None else 0.0, m[1], m[2]))
        if answer is not None:
            merged.append(answer)      # the session's answer stays its last step
        start = min(m[0] for m in merged if m[0] is not None)
        steps = []
        for k, (at, _, _, s) in enumerate(merged):
            s = dict(s, index=k)
            if at is not None:
                s["started_s"] = round(at - start, 3)
            steps.append(s)
        data["steps"] = steps
        data["started_at"] = round(start, 3)
        t0 = start
        totals = data.setdefault("totals", {})
        totals["output_tokens"] = sum(int(s.get("tokens") or 0) for s in steps)
    if t0 is not None:
        turns = []
        for at, text in asks:
            if at is None:
                continue
            ix = next((s["index"] for s in data["steps"] if float(s.get("started_s") or 0) + t0 >= at - 1e-3),
                      len(data["steps"]) - 1)
            turns.append({"step": ix, "at_s": round(max(0.0, at - t0), 3), "prompt": _short(text, 400)})
        data["turns"] = turns
    data["source"] = dict(data.get("source") or {}, format="claude-code-session", session=session.sid, project=project,
                          subagents=subs, compactions=sum(1 for e in entries if isinstance(e, dict)
                                                          and e.get("isCompactSummary")))
    data.setdefault("harness", {})["graded_by"] = "ungraded"
    now = time.time() if now is None else now
    if now - session.mtime < LIVE_S:
        data["in_progress"] = True
        data["elapsed_s"] = (data.get("totals") or {}).get("latency_s")
        last = data["steps"][-1]
        if last.get("type") == "answer":
            data["steps"][-1] = dict(last, type="reason", name="reason")
    return data


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _every(size: int) -> float:
    """How often a live session of ``size`` bytes is read again: a big one less often."""
    return 5.0 + size / 4_000_000


def sync(dest: Path, *, projects: Optional[Path] = None, limit: Optional[int] = None,
         now: Optional[float] = None, progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, int]:
    """Bring ``dest`` up to date with the sessions under ``projects``; returns the counts."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except OSError:
        pass
    index_path = dest / ".index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    now = time.time() if now is None else now
    sessions = find(projects)
    if limit:
        sessions = sessions[:limit]
    counts = {"found": len(sessions), "read": 0, "unchanged": 0, "empty": 0, "failed": 0, "live": 0}
    for n, s in enumerate(sessions):
        live = now - s.mtime < LIVE_S
        seen = index.get(str(s.path)) or {}
        out = seen.get("out")
        if seen.get("sig") == s.sig and seen.get("live") == live and (out is None or (dest / out).exists()):
            counts["unchanged"] += 1
            counts["live"] += 1 if live else 0
            continue
        if live and seen.get("live") and now - float(seen.get("at") or 0) < _every(s.size):
            counts["unchanged"] += 1
            counts["live"] += 1
            continue
        try:
            data = convert(s, now=now)
        except Exception as exc:  # noqa: BLE001 — one unreadable session must not stop the rest
            index[str(s.path)] = {"sig": s.sig, "live": live, "at": now, "error": f"{type(exc).__name__}: {exc}"[:200]}
            counts["failed"] += 1
            continue
        if data is None:
            index[str(s.path)] = {"sig": s.sig, "live": live, "at": now, "out": None}
            counts["empty"] += 1
            continue
        folder = dest / _slug(data["source"]["project"]) / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{s.sid[:8]}__{AGENT}.json"
        running = folder / f"{s.sid[:8]}__{AGENT}.live.json"
        _write(running if live else final, data)
        for stale in ([final] if live else [running]):
            try:
                stale.unlink()
            except OSError:
                pass
        index[str(s.path)] = {"sig": s.sig, "live": live, "at": now,
                              "out": str((running if live else final).relative_to(dest))}
        counts["read"] += 1
        counts["live"] += 1 if live else 0
        if progress:
            progress(n + 1, len(sessions))
    _write(index_path, index)
    return counts


class Follower:
    """Keeps ``dest`` in step with the sessions as Claude Code writes them."""

    def __init__(self, dest: Path, *, projects: Optional[Path] = None, interval: float = 5.0,
                 limit: Optional[int] = None) -> None:
        self.dest, self.projects, self.interval, self.limit = Path(dest), projects, interval, limit
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.counts: Dict[str, int] = {}
        self.error: Optional[str] = None

    def start(self) -> "Follower":
        def loop() -> None:
            while True:
                try:
                    self.counts = sync(self.dest, projects=self.projects, limit=self.limit)
                    self.error = None
                except Exception as exc:  # noqa: BLE001 — the hub keeps serving what it has
                    self.error = f"{type(exc).__name__}: {exc}"
                if self._stop.wait(self.interval):
                    return
        self._thread = threading.Thread(target=loop, name="agentdiff-sessions", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
