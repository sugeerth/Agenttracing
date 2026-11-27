"""Counterfactual replay: the only way a decisive-step claim gets verified.

The engine commits to a decisive step as a *hypothesis* — "correct this
step and the outcome flips" — read from trace evidence.  AgenTracer and
Causal Agent Replay establish the field's ground truth for that claim:
re-execute the run from the corrected step and see whether the outcome
actually flips, over several rollouts because agent policies are
stochastic.  This module does exactly that, using the same provider and
tools the original run had.

The replay rebuilds the conversation from the recorded steps up to the
decisive one, substitutes the correction, and lets the model continue.
Every replay is itself a first-class recorded trace (prefix steps
marked as replayed, the corrected step marked as counterfactual), so a
replay can be diffed against the original like any other pair.

Verdict vocabulary, deliberately three-valued:

* ``replay-verified`` — the outcome flipped in at least half the replays,
  and in at least half the replays more often than without the correction;
* ``replay-refuted`` — it never flipped: the step was not decisive, or
  not sufficient on its own (a joint cause, or a wrong anchor);
* ``replay-mixed`` — flipped sometimes; stochasticity or a partial cause.

Every corrected replay has a control: the same prefix replayed with
nothing changed. A failed run re-executed from a recorded prefix does not
always fail again (*Repair or Resample?*, SymTrace, reproduced 68% of
failures), so an outcome that flips with the correction may flip without
it too, and then the correction cannot take the credit. The controls'
flip rate is reported as how often the failure did not reproduce, and a
step is verified only when the correction beats it by half the replays.
``controls=0`` runs none, and the result says it was uncontrolled.

Replay conclusions are themselves unstable — editing one step changes
every downstream prompt — which is why the counts and the rates are
reported, never a bare boolean.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ..record import Recorder
from .agent import DEFAULT_SYSTEM, Tool, contains_grader, _drive
from .providers import Provider


def _parse_call(rendered: str) -> tuple[str, dict]:
    """Best-effort inverse of ``render_call``: ``name(k='v', n=3)`` →
    ``("name", {"k": "v", "n": "3"})``.  Anything unparseable lands under
    ``_raw`` so the model still sees what was called."""
    match = re.match(r"^\s*([\w.\-]+)\((.*)\)\s*$", rendered or "", re.S)
    if not match:
        return (rendered or "").strip(), {}
    name, inner = match.group(1), match.group(2).strip()
    if not inner:
        return name, {}
    args: dict = {}
    for part in re.split(r",\s*(?=[\w.\-]+=)", inner):
        if "=" not in part:
            args.setdefault("_raw", part)
            continue
        key, value = part.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        args[key.strip()] = value
    return name, args


def messages_from_steps(steps: list, prompt: str, system_prompt: str,
                        correction: Optional[dict] = None,
                        at: Optional[int] = None) -> list:
    """Rebuild the neutral message list from recorded steps ``[0, at]``,
    applying ``correction`` to the step at ``at``.

    ``correction`` is ``{"text": ...}`` for a reason/plan step (the
    agent's own words are replaced), ``{"output": ...}`` for a tool call
    (the tool's result is replaced — what the world told the agent), or
    ``{"input": ...}`` to replace what the agent asked of the tool.
    """
    messages: list = [{"role": "system", "content": system_prompt},
                      {"role": "user", "content": prompt}]
    limit = len(steps) if at is None else at + 1
    for i, step in enumerate(steps[:limit]):
        fix = correction if (correction and i == at) else {}
        kind = step.get("type")
        if kind == "answer":
            continue  # a replay never replays the answer; the model re-answers
        if kind == "tool_call":
            name, args = _parse_call(fix.get("input", step.get("input", "")))
            call_id = f"replay_{i}"
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"id": call_id, "name": name,
                                             "arguments": args}]})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "name": name,
                             "content": str(fix.get("output", step.get("output", "")))})
            continue
        text = fix.get("text", step.get("input") or step.get("output") or "")
        if text:
            messages.append({"role": "assistant", "content": str(text)})
    return messages


def replay_verdict(flipped: int, replays: int, control_flipped: int = 0, controls: int = 0) -> str:
    """The three-valued verdict over the corrected replays' flips and the uncorrected controls'."""
    if not replays or flipped == 0:
        return "replay-refuted"
    rate = flipped / replays
    beats = not controls or rate - control_flipped / controls >= 0.5
    return "replay-verified" if rate >= 0.5 and beats else "replay-mixed"


def replay(trace: dict, provider: Provider, tools: Optional[list], step: int,
           correction: dict, *, replays: int = 3, controls: Optional[int] = None,
           grader: Optional[Callable] = None,
           system_prompt: str = DEFAULT_SYSTEM,
           budget: Optional[dict] = None,
           out_dir: Optional[Union[str, Path]] = None,
           provider_factory: Optional[Callable[[], Provider]] = None) -> dict:
    """Replay ``trace`` from ``step`` with ``correction`` applied, ``replays``
    times, and the same prefix uncorrected ``controls`` times (default: as
    many as ``replays``); return the verdict with every replayed trace attached.

    ``provider_factory`` (zero-argument) rebuilds a fresh provider per
    replay — required for scripted providers, optional for live ones.
    """
    steps = trace.get("steps") or []
    if not (0 <= step < len(steps)):
        raise ValueError(f"step {step} is outside the trace's {len(steps)} steps")
    if not correction or not any(k in correction for k in ("text", "output", "input")):
        raise ValueError("correction must carry 'text', 'output' or 'input'")
    controls = replays if controls is None else max(0, int(controls))
    task = dict(trace.get("task") or {})
    tools = list(tools or [])
    grade = grader or contains_grader
    if grader is None and not (isinstance(task.get("expected"), str)
                               and task["expected"].strip()):
        raise ValueError("replay needs a grader or a task with an expected answer")
    original_success = bool((trace.get("outcome") or {}).get("success"))
    budget = dict(budget or trace.get("budget") or {"max_steps": 12})
    max_steps = int(budget.get("max_steps") or 12)
    agent = (trace.get("agent") or {}).get("name", "agent")

    def one(n: int, fix_at_step: dict, kind: str) -> dict:
        live = provider_factory() if provider_factory else provider
        recorder = Recorder(
            task=str(task.get("id")), prompt=str(task.get("prompt")),
            agent=f"{agent}-{kind}", model=live.model,
            expected=task.get("expected"), run_id=f"{kind}{n + 1}",
            tools=[t.schema_entry() for t in tools] or None,
            budget=budget, out_dir=out_dir,
            trace_id=f"{trace.get('trace_id', task.get('id'))}-{kind}{n + 1}")
        with recorder:
            # the replayed prefix, step for step, so the replay trace is a
            # complete trajectory a diff can align against the original
            for i, s in enumerate(steps[:step + 1]):
                if s.get("type") == "answer":
                    continue
                fix = fix_at_step if i == step else {}
                recorder.step(
                    s.get("type", "reason"), s.get("name", ""),
                    str(fix.get("input", fix.get("text", s.get("input", "")))),
                    str(fix.get("output", s.get("output", ""))),
                    tokens=s.get("tokens"), latency_s=0.0,
                    effect=s.get("effect"), error=s.get("error"),
                    note=("counterfactual correction" if i == step and fix_at_step
                          else "replayed prefix"))
            messages = messages_from_steps(steps, str(task.get("prompt")),
                                           system_prompt, fix_at_step or None, step)
            remaining = max(1, max_steps - (step + 1))
            _drive(recorder, live, messages, tools, task, grade, remaining)
        result = recorder.to_dict()
        flipped = bool(result["outcome"]["success"]) != original_success
        return {"run": n + 1, "success": result["outcome"]["success"],
                "termination": result["outcome"].get("termination"),
                "flipped": flipped, "trace": result}

    runs = [one(n, correction, "replay") for n in range(replays)]
    control_runs = [one(n, {}, "control") for n in range(controls)]
    flips = sum(1 for r in runs if r["flipped"])
    control_flips = sum(1 for r in control_runs if r["flipped"])
    rate = flips / replays if replays else 0.0
    control_rate = control_flips / controls if controls else None
    return {
        "step": step, "correction": correction, "replays": replays,
        "flipped": flips, "flip_rate": round(rate, 4),
        "controls": controls, "control_flipped": control_flips,
        "control_flip_rate": round(control_rate, 4) if control_rate is not None else None,
        "verdict": replay_verdict(flips, replays, control_flips, controls),
        "original_success": original_success,
        "note": ("a flip in at least half the replays, beating the uncorrected controls by half the replays, "
                 "verifies the step as decisive on this evidence; zero flips refutes it as a sufficient single "
                 "cause; anything between is mixed. A control that flips is the failure not reproducing: the "
                 "correction cannot take credit for an outcome the run reaches without it"
                 + ("" if controls else "; no controls ran, so this verdict is uncontrolled")),
        "runs": runs, "control_runs": control_runs,
    }
