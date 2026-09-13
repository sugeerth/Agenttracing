"""Bring your own agent: the harness runs *any* agent, not only its own loop.

Two adapters turn an existing agent into a graded SCHEMA trace with the
same manifest, naming, grading and termination discipline the built-in
tool loop gets:

* :class:`PythonAgent` — ``module:callable``; the callable receives
  ``(task, tools)`` and returns either a SCHEMA trajectory dict (it used
  :class:`~deepcompare.record.Recorder` or built one itself) or an
  OpenAI-style message list, which :func:`deepcompare.adapters.from_openai_messages`
  converts.
* :class:`CommandAgent` — a shell command template; ``{prompt_file}`` is
  a JSON file with the task, ``{out_file}`` is where the agent writes
  its trace or message list.  Anything that can read one file and write
  another is an agent.

The harness keeps the parts an agent must not be trusted with: the
grader decides success, the termination is declared from what the trace
shows (an answer step → ``agent_stop``; none → ``agent_error``; a crash
or a timeout → ``infrastructure_error`` with the reason recorded), the
task's expected answer is stamped on, and the file lands under the
``<task>__<agent>__<run>.json`` name every other command reads.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ..adapters import from_openai_messages
from .agent import contains_grader

__all__ = ["ExternalAgent", "PythonAgent", "CommandAgent", "agent_from_spec",
           "normalise_trace"]


class ExternalAgent:
    """Base: ``produce(task, tools) -> trace dict | message list``."""

    kind = "external"

    def __init__(self, name: str) -> None:
        self.name = name
        self.model = name

    def produce(self, task: dict, tools: list) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError


class PythonAgent(ExternalAgent):
    kind = "python"

    def __init__(self, spec: str, name: Optional[str] = None) -> None:
        if ":" not in spec:
            raise ValueError(f"python agent spec {spec!r} must be module:callable")
        module_name, attr = spec.rsplit(":", 1)
        module = importlib.import_module(module_name)
        fn = getattr(module, attr)
        if not callable(fn):
            raise ValueError(f"{spec} is not callable")
        super().__init__(name or attr)
        self.fn = fn
        self.model = f"python:{spec}"

    def produce(self, task: dict, tools: list) -> Any:
        return self.fn(task, tools)


class CommandAgent(ExternalAgent):
    kind = "cmd"

    def __init__(self, template: str, name: Optional[str] = None,
                 timeout: float = 600.0) -> None:
        if "{out_file}" not in template:
            raise ValueError("cmd agent template must contain {out_file}")
        super().__init__(name or "cmd-agent")
        self.template = template
        self.timeout = timeout
        self.model = "cmd:" + template.split()[0]

    def produce(self, task: dict, tools: list) -> Any:
        with tempfile.TemporaryDirectory() as tmp:
            prompt_file = Path(tmp) / "task.json"
            out_file = Path(tmp) / "out.json"
            prompt_file.write_text(json.dumps({
                "id": task.get("id"), "prompt": task.get("prompt"),
                "tools": [t.schema_entry() for t in tools],
            }, indent=1), encoding="utf-8")
            command = self.template.format(prompt_file=str(prompt_file),
                                           out_file=str(out_file))
            proc = subprocess.run(command, shell=True, capture_output=True,
                                  text=True, timeout=self.timeout)
            if proc.returncode != 0:
                raise RuntimeError(f"agent command exited {proc.returncode}: "
                                   f"{(proc.stderr or proc.stdout).strip()[:400]}")
            if not out_file.is_file():
                raise RuntimeError("agent command wrote no output file")
            return json.loads(out_file.read_text(encoding="utf-8"))


def agent_from_spec(spec: str, name: Optional[str] = None) -> ExternalAgent:
    """``python:module:callable`` or ``cmd:<template>``."""
    kind, _, rest = spec.partition(":")
    kind = kind.strip().lower()
    if kind == "python":
        return PythonAgent(rest, name)
    if kind == "cmd":
        return CommandAgent(rest, name)
    raise ValueError(f"unknown agent kind {kind!r}; known: python, cmd")


def _trace_from_product(product: Any, agent: ExternalAgent, task: dict) -> dict:
    if isinstance(product, dict) and isinstance(product.get("steps"), list):
        return json.loads(json.dumps(product))
    if isinstance(product, list):
        meta = {
            "agent": {"name": agent.name, "model": agent.model, "version": ""},
            "task": {"id": task.get("id"), "prompt": task.get("prompt"),
                     "expected": task.get("expected")},
        }
        try:
            trace, _warnings = from_openai_messages(product, meta=meta)
        except ValueError as exc:
            if "no final assistant answer" not in str(exc):
                raise
            # the agent stopped without answering: convert what it did do
            # with a placeholder answer, then drop the placeholder so the
            # harness declares the termination it actually deserves
            placeholder = {"role": "assistant", "content": "\u2205"}
            trace, _warnings = from_openai_messages(list(product) + [placeholder], meta=meta)
            trace["steps"] = [s for s in trace["steps"] if s.get("type") != "answer"]
        return trace
    raise ValueError("an external agent must return a SCHEMA trace dict or an "
                     "OpenAI-style message list")


def normalise_trace(trace: dict, agent: ExternalAgent, task: dict, *,
                    run_id: Optional[str], grader: Callable,
                    budget: Optional[dict], elapsed_s: float) -> dict:
    """Stamp the harness's facts onto an agent-produced trace: identity,
    task, graded outcome, declared termination, file-stem trace id."""
    trace = dict(trace)
    trace["schema_version"] = trace.get("schema_version") or 1
    trace["agent"] = {"name": agent.name, "model": agent.model,
                      "version": str((trace.get("agent") or {}).get("version") or "")}
    trace["task"] = {"id": str(task.get("id")), "prompt": str(task.get("prompt")),
                     "expected": task.get("expected")}
    steps = trace.get("steps") or []
    for i, step in enumerate(steps):
        step["index"] = i
        if not isinstance(step.get("tokens"), (int, float)):
            step["tokens"] = 0
        if not isinstance(step.get("latency_s"), (int, float)):
            step["latency_s"] = 0.0
    answer_step = next((s for s in reversed(steps) if s.get("type") == "answer"), None)
    answer = str(answer_step.get("output") or answer_step.get("input") or "") if answer_step else ""
    outcome = dict(trace.get("outcome") or {})
    if answer_step is not None:
        verdict = grader(answer, task)
        if verdict is None:
            raise ValueError(f"grader returned None for task {task.get('id')!r}")
        outcome.update({"success": bool(verdict), "answer": answer,
                        "score": 1.0 if verdict else 0.0,
                        "termination": "agent_stop"})
    else:
        outcome.update({"success": False, "answer": outcome.get("answer") or "",
                        "score": 0.0, "termination": "agent_error"})
    if answer_step is None:
        # SCHEMA ends every trajectory with an answer step; a run that
        # never answered gets an empty one that says so, exactly as the
        # Recorder does for a terminated run
        steps.append({"index": len(steps), "type": "answer", "name": "final",
                      "input": "", "output": "", "tokens": 0, "latency_s": 0.0,
                      "note": "no answer was given; termination declared by the harness"})
        trace["steps"] = steps
    trace["outcome"] = outcome
    totals = dict(trace.get("totals") or {})
    totals.setdefault("input_tokens", sum(int(s.get("tokens") or 0) for s in steps))
    totals.setdefault("output_tokens", 0)
    totals.setdefault("cost_usd", 0.0)
    totals["latency_s"] = round(float(totals.get("latency_s") or elapsed_s), 4)
    trace["totals"] = totals
    if budget:
        trace["budget"] = dict(budget)
    if run_id:
        trace["run_id"] = run_id
    stem = f"{task['id']}__{agent.name}" + (f"__{run_id}" if run_id else "")
    trace["trace_id"] = stem
    trace["harness"] = {"adapter": agent.kind, "graded_by": "harness",
                        "note": "trace produced by an external agent; the grade, "
                                "the termination and the identity are the harness's"}
    return trace


def run_external(agent: ExternalAgent, task: dict, tools: Optional[list] = None, *,
                 run_id: Optional[str] = None, budget: Optional[dict] = None,
                 grader: Optional[Callable] = None,
                 out_dir: Optional[Union[str, Path]] = "traces") -> dict:
    """Run one task through an external agent; grade, declare, write."""
    tools = list(tools or [])
    grade = grader or contains_grader
    if grader is None and not (isinstance(task.get("expected"), str)
                               and task["expected"].strip()):
        raise ValueError(
            f"task {task.get('id')!r} has no expected answer and no grader was "
            "given: an ungraded run cannot honestly enter a success rate")
    started = time.perf_counter()
    try:
        product = agent.produce(task, tools)
        trace = _trace_from_product(product, agent, task)
    except Exception as exc:  # the agent is outside the harness: its crash is a fact
        trace = {"steps": [{"index": 0, "type": "reason", "name": "reason",
                            "input": f"agent failure: {exc.__class__.__name__}: {exc}",
                            "output": "", "error": True,
                            "note": "the external agent failed; harness fault"}],
                 "outcome": {"success": False, "answer": "", "score": 0.0,
                             "termination": "infrastructure_error"}}
        trace = normalise_trace(trace, agent, task, run_id=run_id, grader=grade,
                                budget=budget, elapsed_s=time.perf_counter() - started)
        trace["outcome"]["termination"] = "infrastructure_error"
    else:
        trace = normalise_trace(trace, agent, task, run_id=run_id, grader=grade,
                                budget=budget, elapsed_s=time.perf_counter() - started)
    # validate through the engine's own reader before anything is written
    from ..trace import Trajectory
    Trajectory.from_dict(json.loads(json.dumps(trace)))
    if out_dir is not None:
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (trace["trace_id"] + ".json")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return trace
