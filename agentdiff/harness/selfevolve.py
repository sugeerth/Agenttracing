"""Real agents under an evolving harness: Claude Code and Codex CLI, each knob made concrete.

:mod:`agentdiff.selfevolve` decides; this runs. A :class:`~agentdiff.selfevolve.Harness`
reaches each CLI through what the CLI itself offers, and every run's
trace records the harness it ran under (``harness.evolved``), so the
reading that judges a change can see it:

==================  =========================================  ==========================================
knob                Claude Code                                Codex CLI
==================  =========================================  ==========================================
instructions        ``--append-system-prompt``                 appended to the task prompt, marked
deny_tools          ``--disallowedTools``                      not offered (the CLI has no such flag):
                                                               the remedy is refused before it is tried
max_turns           ``--max-turns``                            not offered: refused likewise
==================  =========================================  ==========================================

Each arm is an ordinary duel output (``<out>/<label>/``: traces, records,
diffs), so every run can be read, streamed to a hub (``--hub``), and
opened on its own page like any other.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Callable, List, Optional

from ..selfevolve import Harness
from .vendors import VendorSpec, baseline_check, run_duel

__all__ = ["vendor_arm", "harness_args", "UNSUPPORTED"]

#: knobs a CLI does not offer: a remedy that needs one is refused for that agent
UNSUPPORTED = {"codex": ("deny_tool", "max_turns"), "claude": ()}
PROMPT_MARK = "\n\n---\nNotes from the harness:\n"


def harness_args(spec: VendorSpec, harness: Harness) -> List[str]:
    if spec.kind != "claude":
        return []
    args: List[str] = []
    if harness.instructions:
        args += ["--append-system-prompt", "\n".join(harness.instructions)]
    if harness.deny_tools:
        args += ["--disallowedTools", ",".join(harness.deny_tools)]
    if harness.max_turns:
        args += ["--max-turns", str(harness.max_turns)]
    return args


def vendor_arm(tasks: list, specs: List[VendorSpec], out: Path, *, runs: int = 1,
               on_event: Optional[Callable[[dict], None]] = None, **kw) -> Callable[[Harness, str], list]:
    """A ``run_arm`` for :func:`agentdiff.selfevolve.self_evolve`: every agent,
    every task, ``runs`` times, under the harness given. Each task's check is
    run once on an untouched copy first and reused by every arm."""
    out = Path(out)
    baselines: dict = {}

    def run_arm(harness: Harness, label: str) -> list:
        for t in tasks:
            if t.get("check") and t["id"] not in baselines:
                baselines[t["id"]] = baseline_check(t, kw.get("check_timeout_s", 600.0))
        arm_specs = []
        for s in specs:
            s2 = copy.deepcopy(s)
            s2.extra = list(s.extra) + harness_args(s, harness)
            arm_specs.append(s2)
        arm_tasks = tasks
        if harness.instructions and any(s.kind == "codex" for s in specs):
            # Codex has no system-prompt flag: the notes ride on the prompt, marked as the harness's
            arm_tasks = [dict(t, prompt=t["prompt"] + PROMPT_MARK + "\n".join(f"- {i}" for i in harness.instructions))
                         for t in tasks]
        d = out / label
        records = run_duel(arm_tasks, arm_specs, d, runs=runs, parallel=True, baselines=baselines,
                           on_event=on_event, **kw)
        trajs = []
        for r in records:
            if r.get("failed"):
                continue
            t = r["trajectory"]
            t.setdefault("harness", {})["evolved"] = {"version": harness.version, "remedies": list(harness.remedies),
                                                      "instructions": list(harness.instructions),
                                                      "deny_tools": list(harness.deny_tools),
                                                      "max_turns": harness.max_turns}
            (d / r["trace"]).write_text(json.dumps(t, indent=1, ensure_ascii=False), encoding="utf-8")
            trajs.append(t)
        (d / "records").mkdir(parents=True, exist_ok=True)
        (d / "harness.json").write_text(json.dumps({"label": label, "version": harness.version,
                                                    "remedies": harness.remedies,
                                                    "instructions": harness.instructions,
                                                    "deny_tools": harness.deny_tools,
                                                    "max_turns": harness.max_turns}, indent=1), encoding="utf-8")
        return trajs

    return run_arm
