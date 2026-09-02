"""A generic tool-loop agent, recorded as a SCHEMA trajectory.

The loop is deliberately plain — prompt, act, observe, repeat — because
the point is not a clever agent but a *comparable* one: the same loop
run against two providers yields two traces that differ only in what
the models did, which is exactly what the diff should measure.

Every model turn becomes a ``reason`` step (or the ``answer``) with the
endpoint's own token counts and measured latency; every tool call is
recorded with :meth:`Recorder.tool` so its arguments, result, error flag
and declared effect are on the step.  Terminations are declared, never
inferred: ``max_steps`` when the budget ends the run, ``too_many_errors``
when tools keep failing, ``infrastructure_error`` when the provider
itself failed — the harness's fault, excluded from the agent's
reliability statistics downstream.

Grading is explicit.  ``success`` on a trace is the grader's verdict,
and this module will not guess it: pass a ``grader`` callable, or give
the task an ``expected`` answer and the default containment grader
applies.  A task with neither is refused up front, because an ungraded
run silently entering a success rate is the one dishonesty a harness
must never commit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ..record import Recorder
from .providers import Provider, ProviderError, ProviderResponse

DEFAULT_SYSTEM = (
    "You are an agent completing a task with tools. Use tools when you "
    "need facts you do not have. When you are done, reply with the final "
    "answer as plain text and no tool calls."
)


@dataclass
class Tool:
    """A callable the agent may use.  ``parameters`` is the JSON schema
    the provider shows the model; ``effect`` is the read/write
    declaration the process analysis needs (``None`` is allowed and is
    reported downstream as undeclared, never guessed)."""

    name: str
    fn: Callable[..., Any]
    description: str = ""
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    effect: Optional[str] = None

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}

    def schema_entry(self) -> dict:
        entry: dict = {"name": self.name, "parameters": self.parameters}
        if self.effect:
            entry["effect"] = self.effect
        return entry


def contains_grader(answer: str, task: dict) -> Optional[bool]:
    """Default grader: the expected answer's normalised text appears in
    the answer.  Returns ``None`` when the task has no expected answer,
    which :func:`run_task` treats as ungradeable."""
    expected = task.get("expected")
    if not isinstance(expected, str) or not expected.strip():
        return None
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower())
    return norm(expected) in norm(answer)


def _render_result(result: Any, limit: int = 4000) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


def run_task(provider: Provider, task: dict, tools: Optional[list] = None, *,
             agent: Optional[str] = None, run_id: Optional[str] = None,
             budget: Optional[dict] = None, grader: Optional[Callable] = None,
             system_prompt: str = DEFAULT_SYSTEM, max_tool_errors: int = 3,
             out_dir: Optional[Union[str, Path]] = "traces",
             version: str = "") -> dict:
    """Run one task against one provider; return the trajectory dict.

    ``task`` is ``{"id": ..., "prompt": ..., "expected": ...?}``.
    ``budget`` defaults to ``{"max_steps": 12}`` — counted in *provider
    turns*, the unit the model controls.  The trace is written to
    ``out_dir`` (``None`` records without writing).
    """
    tools = list(tools or [])
    by_name = {t.name: t for t in tools}
    budget = dict(budget or {"max_steps": 12})
    max_steps = int(budget.get("max_steps") or 12)
    grade = grader or contains_grader
    if grader is None and not (isinstance(task.get("expected"), str)
                               and task["expected"].strip()):
        raise ValueError(
            f"task {task.get('id')!r} has no expected answer and no grader was "
            "given: an ungraded run cannot honestly enter a success rate")

    recorder = Recorder(
        task=str(task["id"]), prompt=str(task["prompt"]),
        agent=agent or provider.name, model=provider.model, version=version,
        expected=task.get("expected"), run_id=run_id,
        tools=[t.schema_entry() for t in tools] or None,
        budget=budget, out_dir=out_dir)

    messages: list = [{"role": "system", "content": system_prompt},
                      {"role": "user", "content": str(task["prompt"])}]
    declarations = [t.declaration() for t in tools]
    tool_errors = 0
    answered = False

    with recorder:
        for turn in range(max_steps):
            try:
                response: ProviderResponse = provider.complete(messages, declarations)
            except ProviderError as exc:
                recorder.reason(f"provider failure: {exc}", error=True,
                                note="the model endpoint failed; harness fault")
                recorder.terminate("infrastructure_error")
                break
            tokens = (response.usage.get("input_tokens", 0)
                      + response.usage.get("output_tokens", 0)) or None

            if not response.tool_calls:
                answer = response.text.strip()
                verdict = grade(answer, task)
                if verdict is None:
                    raise ValueError(
                        f"grader returned None for task {task.get('id')!r}; a "
                        "verdict must be True or False")
                recorder.answer(answer, success=bool(verdict), tokens=tokens,
                                latency_s=response.latency_s,
                                model={"name": response.model} if response.model else None)
                answered = True
                break

            # a turn that both talks and acts: the prose is the agent's
            # reasoning, recorded before the calls it motivates
            if response.text.strip():
                recorder.reason(response.text.strip(), tokens=tokens,
                                latency_s=response.latency_s)
            messages.append({"role": "assistant", "content": response.text,
                             "tool_calls": [c.as_dict() for c in response.tool_calls]})
            for call in response.tool_calls:
                tool = by_name.get(call.name)
                if tool is None:
                    # undeclared call: recorded exactly as made — it is a
                    # finding for the grounding check, not something to hide
                    recorder.tool(call.name, call.arguments,
                                  f"error: no such tool {call.name!r}", error=True)
                    result_text = f"error: no such tool {call.name!r}"
                    tool_errors += 1
                else:
                    try:
                        result = recorder.tool(call.name, call.arguments,
                                               call=tool.fn, effect=tool.effect)
                        result_text = _render_result(result)
                    except Exception as exc:
                        result_text = f"error: {exc.__class__.__name__}: {exc}"
                        tool_errors += 1
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "name": call.name, "content": result_text})
            if tool_errors >= max_tool_errors:
                recorder.terminate("too_many_errors")
                break
        else:
            recorder.terminate("max_steps")

        if not answered and recorder._termination is None:
            recorder.terminate("agent_error")

    return recorder.to_dict()
