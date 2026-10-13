"""A self-evolve started from the hub's Evolve page: one at a time, its output in a log, stoppable.

The hub starts the same command a terminal would (``self-evolve --demo``,
or ``fix --evolve`` in a project), as its own process group, so Stop ends
it and the agents it started. What it writes, ``progress.json`` and the
lineage, is what the Evolve page draws; this module only knows the
process, its command and the end of its log. Only a hub on loopback, for
a signed-in user, with the form's CSRF token, ever gets here (``app``).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

__all__ = ["Jobs", "agentdiff_argv", "LOG"]

#: the job's output, in the folder it writes to
LOG = "hub-job.log"


def agentdiff_argv() -> tuple:
    """How to run this agentdiff again as a command, and the environment it needs: the binary itself when this
    is a downloaded build, else this Python with the package it was loaded from on its path."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        return [sys.executable], env
    pkg = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = pkg + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return [sys.executable, "-m", "agentdiff"], env


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.job: Optional[dict] = None

    def running(self) -> bool:
        with self.lock:
            return bool(self.job and self.job["proc"].poll() is None)

    def start(self, args: List[str], *, cwd: Path, out: Path, what: str) -> dict:
        """Start ``agentdiff ARGS`` in ``cwd``; refuses while another is running."""
        with self.lock:
            if self.job and self.job["proc"].poll() is None:
                raise RuntimeError(f"one is running already: {self.job['what']}")
            base, env = agentdiff_argv()
            out.mkdir(parents=True, exist_ok=True)
            log = out / LOG
            fh = open(log, "w", encoding="utf-8")
            kw: dict = {"cwd": str(cwd), "env": env, "stdout": fh, "stderr": subprocess.STDOUT,
                        "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kw["start_new_session"] = True
            proc = subprocess.Popen(base + list(args), **kw)
            fh.close()
            self.job = {"proc": proc, "args": list(args), "cwd": str(cwd), "out": str(out), "log": str(log),
                        "what": what, "started_at": time.time(), "stopped": False}
            return self.view_locked()

    def stop(self) -> bool:
        with self.lock:
            if not self.job or self.job["proc"].poll() is not None:
                return False
            proc = self.job["proc"]
            self.job["stopped"] = True
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(proc.pid, signal.SIGTERM)  # it and the agents it started
            except OSError:
                proc.terminate()
            return True

    def view(self) -> Optional[dict]:
        with self.lock:
            return self.view_locked()

    def view_locked(self) -> Optional[dict]:
        if not self.job:
            return None
        j = self.job
        code = j["proc"].poll()
        return {"what": j["what"], "args": j["args"], "cwd": j["cwd"], "out": j["out"], "log": j["log"],
                "started_at": j["started_at"], "running": code is None, "exit": code, "stopped": j["stopped"],
                "tail": _tail(Path(j["log"]))}


def _tail(path: Path, lines: int = 6) -> List[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 8192))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [x for x in text.splitlines() if x.strip()][-lines:]
