"""A database for traces.

Directories of JSON files are fine for a demo and hopeless at ten
thousand runs. This is the trace store: one SQLite file (the standard
library's ``sqlite3``, nothing to install), one row per trajectory with
the full JSON beside the columns a query needs — task, family, agent,
model, run, outcome, termination, tokens, cost, latency, steps, when it
was recorded, where it came from — and one row per step, indexed, with
full-text search over step text when the build has FTS5. Every analysis
command that takes a trace directory also takes ``--db FILE`` and reads
the same trajectories from here; the watcher and the hook can ingest as
they write.

What it is not: a second schema. A row's ``json`` column is the SCHEMA
trajectory as written, and :meth:`TraceDB.trajectories` hands back the
same :class:`Trajectory` objects a directory would. Columns are an
index over that JSON, kept in step by :meth:`TraceDB.add`, never edited
on their own. The file is append-mostly: ``add`` replaces a trace with
the same id (``task__agent__run``) so re-ingesting a directory is
idempotent, and ``vacuum`` is explicit.

Design notes, in the interest of not reinventing badly: the layout is
what OpenTelemetry-style stores (spans with a trace id, attributes, a
start time) and LLM trace products converge on — a flat spans table
with indexed attributes and the raw payload — kept to what this engine
reads. Provenance is a column (``source``: harness, hook, transcript,
watch, import) because a comparison across sources must be able to say
so.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from .trace import Trajectory

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS traces (
  trace_id     TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  family       TEXT NOT NULL,
  agent        TEXT NOT NULL,
  model        TEXT,
  version      TEXT,
  run_id       TEXT NOT NULL,
  success      INTEGER,
  termination  TEXT,
  answer       TEXT,
  expected     TEXT,
  steps        INTEGER NOT NULL,
  tool_calls   INTEGER NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd     REAL,
  latency_s    REAL,
  source       TEXT,
  recorded_at  REAL NOT NULL,
  json         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS traces_task ON traces (task_id, agent, run_id);
CREATE INDEX IF NOT EXISTS traces_family ON traces (family, agent);
CREATE INDEX IF NOT EXISTS traces_agent ON traces (agent, success);
CREATE INDEX IF NOT EXISTS traces_when ON traces (recorded_at);
CREATE TABLE IF NOT EXISTS steps (
  trace_id  TEXT NOT NULL REFERENCES traces (trace_id) ON DELETE CASCADE,
  idx       INTEGER NOT NULL,
  type      TEXT NOT NULL,
  name      TEXT,
  input     TEXT,
  output    TEXT,
  tokens    INTEGER,
  latency_s REAL,
  quality   TEXT,
  error     INTEGER,
  PRIMARY KEY (trace_id, idx)
);
CREATE INDEX IF NOT EXISTS steps_name ON steps (name, type);
CREATE INDEX IF NOT EXISTS steps_type ON steps (type);
CREATE TABLE IF NOT EXISTS checkpoints (
  trace_id    TEXT NOT NULL,
  step        INTEGER NOT NULL,
  label       TEXT,
  source      TEXT,
  recorded_at REAL NOT NULL,
  json        TEXT NOT NULL,
  PRIMARY KEY (trace_id, step)
);
CREATE INDEX IF NOT EXISTS checkpoints_when ON checkpoints (recorded_at);
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS steps_fts USING fts5(trace_id UNINDEXED, idx UNINDEXED, name, input, output);
"""


def family_of(task_id: str) -> str:
    return re.sub(r"(__|[-_])r?\d+$", "", task_id) or task_id


class TraceDB:
    """The trace store. ``with TraceDB(path) as db:`` or call :meth:`close`."""

    def __init__(self, path: Union[str, Path] = "traces.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(_DDL)
        self.fts = self._enable_fts()
        cur = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        if row is None:
            self.conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('created_at', ?)", (repr(time.time()),))
            self.conn.commit()

    def _enable_fts(self) -> bool:
        try:
            self.conn.executescript(_FTS_DDL)
            return True
        except sqlite3.OperationalError:
            return False

    def __enter__(self) -> "TraceDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------- writing

    def add(self, trajectory: Union[Trajectory, dict], *, source: str = "import",
            recorded_at: Optional[float] = None) -> str:
        """Insert or replace one trajectory (validated). Returns its id."""
        data = trajectory.to_dict() if isinstance(trajectory, Trajectory) else trajectory
        traj = Trajectory.from_dict(data)   # never store what the engine cannot read
        trace_id = traj.trace_id or f"{traj.task.id}__{traj.agent.name}" + (f"__{traj.run_id}" if traj.run_id and traj.run_id != "r1" else "")
        tools = sum(1 for s in traj.steps if s.type in ("tool_call", "search", "retrieve", "read"))
        totals = traj.totals
        with self.conn:
            self.conn.execute("DELETE FROM steps WHERE trace_id = ?", (trace_id,))
            if self.fts:
                self.conn.execute("DELETE FROM steps_fts WHERE trace_id = ?", (trace_id,))
            self.conn.execute(
                "INSERT OR REPLACE INTO traces (trace_id, task_id, family, agent, model, version, run_id, success, "
                "termination, answer, expected, steps, tool_calls, input_tokens, output_tokens, cost_usd, latency_s, "
                "source, recorded_at, json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trace_id, traj.task.id, family_of(traj.task.id), traj.agent.name, traj.agent.model, traj.agent.version,
                 traj.run_id or "r1", 1 if traj.outcome.success else 0, traj.outcome.termination, traj.outcome.answer,
                 traj.task.expected, len(traj.steps), tools,
                 totals.input_tokens if totals else None, totals.output_tokens if totals else None,
                 totals.cost_usd if totals else None, totals.latency_s if totals else None,
                 source, recorded_at if recorded_at is not None else time.time(),
                 json.dumps(data, ensure_ascii=False)))
            rows = [(trace_id, s.index, s.type, s.name, s.input, s.output, s.tokens, s.latency_s, s.quality,
                     1 if s.error else 0) for s in traj.steps]
            self.conn.executemany("INSERT INTO steps (trace_id, idx, type, name, input, output, tokens, latency_s, quality, error) "
                                  "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            if self.fts:
                self.conn.executemany("INSERT INTO steps_fts (trace_id, idx, name, input, output) VALUES (?,?,?,?,?)",
                                      [(trace_id, s.index, s.name or "", s.input or "", s.output or "") for s in traj.steps])
        return trace_id

    # ---------------------------------------------------------- checkpoints

    def checkpoint(self, partial: dict, *, label: Optional[str] = None, source: str = "watch",
                   recorded_at: Optional[float] = None) -> Optional[str]:
        """Keep a run-so-far (a live or partial trace) under its trace id and
        step count, so a run can be replayed from any point it reached and a
        crash loses nothing it had already done. Idempotent per (id, step)."""
        if not isinstance(partial, dict):
            return None
        steps = partial.get("steps") or []
        task = ((partial.get("task") or {}).get("id")) or "task"
        agent = ((partial.get("agent") or {}).get("name")) or "agent"
        trace_id = partial.get("trace_id") or f"{task}__{agent}"
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO checkpoints (trace_id, step, label, source, recorded_at, json) VALUES (?,?,?,?,?,?)",
                              (trace_id, len(steps), label, source,
                               recorded_at if recorded_at is not None else time.time(),
                               json.dumps(partial, ensure_ascii=False)))
        return trace_id

    def checkpoints(self, trace_id: str) -> list:
        """The checkpoints of one run, oldest first: step, label, when, and the partial trace."""
        return [{"trace_id": r["trace_id"], "step": r["step"], "label": r["label"], "source": r["source"],
                 "recorded_at": r["recorded_at"], "trace": json.loads(r["json"])}
                for r in self.conn.execute("SELECT * FROM checkpoints WHERE trace_id = ? ORDER BY step", (trace_id,))]

    def checkpoint_ids(self) -> list:
        return [dict(r) for r in self.conn.execute(
            "SELECT trace_id, COUNT(*) AS n, MAX(step) AS latest, MAX(recorded_at) AS updated FROM checkpoints GROUP BY trace_id ORDER BY updated DESC")]

    def add_directory(self, directory: Union[str, Path], *, source: str = "import") -> dict:
        """Ingest every trace JSON in a directory; live and non-trace files are skipped."""
        added, skipped = [], []
        for path in sorted(Path(directory).glob("*.json")):
            if path.name.endswith(".live.json") or path.name.startswith(("report_", "aggregate", "RUN_MANIFEST", "fleet")):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                added.append(self.add(data, source=source, recorded_at=path.stat().st_mtime))
            except (OSError, ValueError) as exc:
                skipped.append(f"{path.name}: {exc}")
        return {"added": len(added), "skipped": skipped, "ids": added}

    def remove(self, trace_id: str) -> bool:
        with self.conn:
            if self.fts:
                self.conn.execute("DELETE FROM steps_fts WHERE trace_id = ?", (trace_id,))
            cur = self.conn.execute("DELETE FROM traces WHERE trace_id = ?", (trace_id,))
        return cur.rowcount > 0

    def vacuum(self) -> None:
        self.conn.execute("VACUUM")

    # ------------------------------------------------------------- reading

    def _where(self, task: Optional[str] = None, family: Optional[str] = None, agent: Optional[str] = None,
               run: Optional[str] = None, success: Optional[bool] = None, termination: Optional[str] = None,
               source: Optional[str] = None, since: Optional[float] = None, until: Optional[float] = None,
               model: Optional[str] = None) -> tuple:
        clauses, params = [], []
        for col, val in (("task_id", task), ("family", family), ("agent", agent), ("run_id", run),
                         ("termination", termination), ("source", source), ("model", model)):
            if val is not None:
                if isinstance(val, (list, tuple, set)):
                    clauses.append(f"{col} IN ({','.join('?' * len(val))})"); params.extend(val)
                else:
                    clauses.append(f"{col} = ?"); params.append(val)
        if success is not None:
            clauses.append("success = ?"); params.append(1 if success else 0)
        if since is not None:
            clauses.append("recorded_at >= ?"); params.append(since)
        if until is not None:
            clauses.append("recorded_at <= ?"); params.append(until)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def query(self, limit: Optional[int] = None, order: str = "recorded_at DESC", **filters) -> list:
        """Rows (dicts of the indexed columns, no JSON) matching the filters."""
        where, params = self._where(**filters)
        sql = ("SELECT trace_id, task_id, family, agent, model, version, run_id, success, termination, steps, tool_calls, "
               "input_tokens, output_tokens, cost_usd, latency_s, source, recorded_at FROM traces" + where +
               f" ORDER BY {order}" + (f" LIMIT {int(limit)}" if limit else ""))
        return [dict(r) for r in self.conn.execute(sql, params)]

    def trajectories(self, **filters) -> list:
        """The matching trajectories as engine objects, oldest first."""
        where, params = self._where(**filters)
        out = []
        for row in self.conn.execute("SELECT json FROM traces" + where + " ORDER BY recorded_at, trace_id", params):
            out.append(Trajectory.from_dict(json.loads(row["json"])))
        return out

    def get(self, trace_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT json FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def count(self, **filters) -> int:
        where, params = self._where(**filters)
        return int(self.conn.execute("SELECT COUNT(*) FROM traces" + where, params).fetchone()[0])

    def search(self, text: str, limit: int = 50) -> list:
        """Steps whose name, input or output mention ``text`` (FTS5 when
        available, LIKE otherwise), newest trace first."""
        if self.fts:
            sql = ("SELECT f.trace_id, f.idx, s.type, s.name, substr(s.output, 1, 200) AS output, t.task_id, t.agent, t.run_id "
                   "FROM steps_fts f JOIN steps s ON s.trace_id = f.trace_id AND s.idx = f.idx "
                   "JOIN traces t ON t.trace_id = f.trace_id WHERE steps_fts MATCH ? ORDER BY t.recorded_at DESC, f.idx LIMIT ?")
            try:
                return [dict(r) for r in self.conn.execute(sql, (self._fts_query(text), limit))]
            except sqlite3.OperationalError:
                pass
        like = f"%{text}%"
        sql = ("SELECT s.trace_id, s.idx, s.type, s.name, substr(s.output, 1, 200) AS output, t.task_id, t.agent, t.run_id "
               "FROM steps s JOIN traces t ON t.trace_id = s.trace_id WHERE s.name LIKE ? OR s.input LIKE ? OR s.output LIKE ? "
               "ORDER BY t.recorded_at DESC, s.idx LIMIT ?")
        return [dict(r) for r in self.conn.execute(sql, (like, like, like, limit))]

    @staticmethod
    def _fts_query(text: str) -> str:
        words = [w for w in re.split(r"\s+", text.strip()) if w]
        return " ".join('"' + w.replace('"', '""') + '"' for w in words) or '""'

    # ------------------------------------------------------------- summaries

    def summary(self) -> dict:
        """What the store holds: counts by agent, family and source, the
        success rate per agent, the time span."""
        by = {}
        for col in ("agent", "family", "source", "model"):
            by[col] = {r[col] or "": r["n"] for r in self.conn.execute(
                f"SELECT {col}, COUNT(*) AS n FROM traces GROUP BY {col} ORDER BY n DESC")}
        rate = {r["agent"]: {"n": r["n"], "successes": r["s"], "rate": round(r["s"] / r["n"], 4) if r["n"] else None}
                for r in self.conn.execute("SELECT agent, COUNT(*) AS n, SUM(success) AS s FROM traces GROUP BY agent")}
        span = self.conn.execute("SELECT MIN(recorded_at) AS lo, MAX(recorded_at) AS hi, COUNT(*) AS n, SUM(steps) AS steps FROM traces").fetchone()
        ckpt = self.conn.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT trace_id) AS runs FROM checkpoints").fetchone()
        return {"path": str(self.path), "traces": span["n"], "steps": span["steps"] or 0,
                "checkpoints": {"count": ckpt["n"], "runs": ckpt["runs"]},
                "recorded_between": [span["lo"], span["hi"]] if span["n"] else None,
                "by": by, "success_by_agent": rate, "fts": self.fts, "schema_version": SCHEMA_VERSION}

    def pairs(self, agents: Optional[Iterable[str]] = None) -> dict:
        """``{task_id: {agent: [Trajectory, ...]}}`` — the shape ``runs``
        and ``batch`` consume — optionally restricted to some agents."""
        out: dict = {}
        for t in self.trajectories(agent=list(agents) if agents else None):
            out.setdefault(t.task.id, {}).setdefault(t.agent.name, []).append(t)
        return out


def open_db(path: Union[str, Path]) -> TraceDB:
    return TraceDB(path)


__all__ = ["TraceDB", "open_db", "family_of", "SCHEMA_VERSION"]
