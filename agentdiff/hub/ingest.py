"""In-band telemetry the agents post, kept as runs.

An agent (or the last tool on its path) posts its vector; the store
decodes it, refusing anything that is not one, and keeps three files per
run, keyed by the vector's trace id:

- ``<id>.int``   the vector itself, its text form
- ``traces/<id>.json``  the SCHEMA trajectory the sink reads off it, so
  ``agentdiff batch <state>/telemetry/traces`` works on posted runs
- ``<id>.meta.json``  what the poster said beside it (outcome, prompt, and
  whether the run is still going: a vector posted ``live`` is shown as
  running until a copy without the flag arrives)

A run posted again as it grows replaces the earlier version only when the
new vector has at least as many hops, so a late, shorter copy never
erases a longer one.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

from ..telemetry import WireError, read, summary, to_trajectory, to_text, encode
from .catalog import Entry, entry_id

__all__ = ["TelemetryStore"]


class TelemetryStore:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self._lock = threading.Lock()

    def save(self, text: str, *, prompt: str = "", success: Optional[bool] = None, answer: str = "",
             model: str = "", live: bool = False) -> dict:
        try:
            vector = read(text)
        except (WireError, ValueError) as exc:
            raise ValueError(f"not a telemetry vector: {exc}") from None
        run_id = vector.trace_id.hex()
        info = summary(vector)
        traj = to_trajectory(vector, prompt=prompt, success=success, answer=answer, model=model)
        if live:
            traj["in_progress"] = True
            traj["elapsed_s"] = info.get("span_s")
        with self._lock:
            (self.dir / "traces").mkdir(parents=True, exist_ok=True)
            old = self._meta(run_id)
            if old and old.get("hops", 0) > info["hops"]:
                return {"id": run_id, "kept": "earlier", "hops": old["hops"]}
            self._write(self.dir / f"{run_id}.int", to_text(encode(vector)))
            self._write(self.dir / "traces" / f"{run_id}.json", json.dumps(traj, indent=1))
            self._write(self.dir / f"{run_id}.meta.json", json.dumps(
                {**info, "received_at": time.time(), "success": success, "live": live, "prompt": prompt[:2000]}, indent=1))
        return {"id": run_id, "kept": "this", "hops": info["hops"]}

    def _meta(self, run_id: str) -> Optional[dict]:
        p = self.dir / f"{run_id}.meta.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def vector(self, run_id: str) -> Optional[str]:
        if not run_id.isalnum():
            return None
        p = self.dir / f"{run_id}.int"
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def entries(self) -> List[Entry]:
        out = []
        for meta_path in sorted(self.dir.glob("*.meta.json")):
            meta = self._meta(meta_path.name[: -len(".meta.json")]) or {}
            run_id = meta.get("trace_id")
            if not run_id:
                continue
            title = f"{meta.get('agent') or 'agent'} · {meta.get('task') or 'task'}"
            out.append(Entry(entry_id("telemetry", Path(run_id)), "telemetry", title, self.dir,
                             float(meta.get("received_at") or 0.0), {**meta, "run_id": run_id}, None))
        return out
