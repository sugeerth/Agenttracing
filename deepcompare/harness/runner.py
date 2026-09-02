"""Run a task set against several providers, N times each, as SCHEMA traces.

Output files are named ``<task>__<agent>.json`` (single run) or
``<task>__<agent>__<run>.json`` (repetitions) — exactly the layout
``batch``, ``fleet``, ``runs``, ``gate`` and the rest read, so a suite run
flows straight into every analysis without a conversion step.

A provider failure on one task does not abort the suite: the trace
records the ``infrastructure_error`` termination and the runner moves
on, because a partial suite with honest terminations is analysable and
an aborted one is not.  Failures that are the *runner's* fault (a task
without a grader, a malformed task) are raised up front, before any
model is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .agent import DEFAULT_SYSTEM, Tool, run_task
from .providers import Provider


def load_tasks(path: Union[str, Path]) -> list:
    """Tasks JSON: a list of ``{"id", "prompt", "expected"?}`` or a dict
    with a ``tasks`` list.  Ids must be unique — two tasks sharing an id
    would silently overwrite each other's traces."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = data["tasks"] if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{path}: expected a non-empty list of tasks")
    seen = set()
    for task in tasks:
        if not isinstance(task, dict) or not task.get("id") or not task.get("prompt"):
            raise ValueError(f"{path}: every task needs an id and a prompt: {task!r}")
        if task["id"] in seen:
            raise ValueError(f"{path}: duplicate task id {task['id']!r}")
        seen.add(task["id"])
    return tasks


def run_suite(providers: dict, tasks: list, tools: Optional[list] = None, *,
              out_dir: Union[str, Path] = "traces", runs: int = 1,
              budget: Optional[dict] = None, grader: Optional[Callable] = None,
              system_prompt: str = DEFAULT_SYSTEM,
              provider_factory: Optional[Callable[[str], Provider]] = None,
              progress: Optional[Callable[[str], None]] = None) -> dict:
    """``providers`` maps agent name → :class:`Provider` (or, with
    ``provider_factory``, agent name → spec string, so scripted providers
    can be rebuilt fresh for every task and run instead of replaying an
    exhausted script).  Returns a manifest: every trace written, with its
    task, agent, run and outcome, plus the count of provider failures."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"out_dir": str(out), "runs": runs,
                      "agents": sorted(providers), "traces": [],
                      "provider_failures": 0}
    for task in tasks:
        for agent, spec in providers.items():
            for run_index in range(runs):
                provider = (provider_factory(spec) if provider_factory
                            else spec)
                run_id = f"r{run_index + 1}" if runs > 1 else None
                if progress:
                    progress(f"{task['id']} · {agent}"
                             + (f" · {run_id}" if run_id else ""))
                trace = run_task(provider, task, tools, agent=agent,
                                 run_id=run_id, budget=budget, grader=grader,
                                 system_prompt=system_prompt, out_dir=out)
                outcome = trace.get("outcome") or {}
                if outcome.get("termination") == "infrastructure_error":
                    manifest["provider_failures"] += 1
                name = f"{task['id']}__{agent}" + (f"__{run_id}" if run_id else "")
                manifest["traces"].append({
                    "file": name + ".json", "task": task["id"], "agent": agent,
                    "run": run_id, "success": outcome.get("success"),
                    "termination": outcome.get("termination"),
                    "steps": len(trace.get("steps") or []),
                })
    (out / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def load_tools(spec: str) -> list:
    """``module:attribute`` → list of :class:`Tool`.  The attribute may be
    a list of Tools or a zero-argument callable returning one."""
    if ":" not in spec:
        raise ValueError(f"tools spec {spec!r} must be module:attribute")
    module_name, attr = spec.rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_name)
    value = getattr(module, attr)
    tools = value() if callable(value) and not isinstance(value, list) else value
    if not isinstance(tools, list) or not all(isinstance(t, Tool) for t in tools):
        raise ValueError(f"{spec} must yield a list of harness.Tool")
    return tools
