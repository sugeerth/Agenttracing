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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ..record import Recorder
from ..semantic import normalize_for_containment
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
    return normalize_for_containment(expected) in normalize_for_containment(answer)


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
    #: the loop's other settings, read from the budget so they are recorded
    #: on the trace beside `max_steps` rather than living only in a call
    #: signature.  A scaffold experiment can turn any of them, and
    #: `harnessevo.fingerprint` reads them back, so a run's control flow is
    #: part of what "the same harness" means.
    if budget.get("max_tool_errors") is not None:
        max_tool_errors = int(budget["max_tool_errors"])
    dedupe = bool(budget.get("dedupe_tool_calls"))
    gate_writes = bool(budget.get("require_read_before_write"))
    # a number, not a flag: the operator sets how many reads may be in
    # flight at once, and under two is off. A bool would have been a
    # second shape for a setting the schema already types as a limit.
    parallel = budget.get("parallel_tool_calls")
    parallel = int(parallel) if isinstance(parallel, (int, float)) and not isinstance(parallel, bool) else 0
    require_before_answer = budget.get("require_before_answer") or None
    if require_before_answer is not None:
        require_before_answer = str(require_before_answer)
    grade = grader or contains_grader
    if grader is None and not (isinstance(task.get("expected"), str)
                               and task["expected"].strip()):
        raise ValueError(
            f"task {task.get('id')!r} has no expected answer and no grader was "
            "given: an ungraded run cannot honestly enter a success rate")

    agent_name = agent or provider.name
    recorder = Recorder(
        task=str(task["id"]), prompt=str(task["prompt"]),
        agent=agent_name, model=provider.model, version=version,
        expected=task.get("expected"), run_id=run_id,
        tools=[t.schema_entry() for t in tools] or None,
        budget=budget, out_dir=out_dir,
        # the trace id IS the file stem, so a trace and its file never
        # disagree about what they are called
        trace_id=f"{task['id']}__{agent_name}" + (f"__{run_id}" if run_id else ""))

    messages: list = [{"role": "system", "content": system_prompt},
                      {"role": "user", "content": str(task["prompt"])}]
    with recorder:
        _drive(recorder, provider, messages, tools, task, grade, max_steps,
               max_tool_errors=max_tool_errors, dedupe=dedupe,
               require_before_answer=require_before_answer, gate_writes=gate_writes,
               parallel=parallel)
    return recorder.to_dict()


def _parallel_batch(calls, by_name, parallel: int, dedupe: bool, seen_calls: dict):
    """The turn's calls, when the whole turn may be issued at once.

    Returns ``[]`` unless every call resolves to a tool that *declares* a
    read, there are at least two of them, and none would be served from
    the cache. An undeclared effect is undeclared, not read-only: the
    whole point of running these together is that they do not affect one
    another, and that is a claim only a declaration can support.
    """
    if parallel < 2 or len(calls or []) < 2:
        return []
    batch = []
    for call in calls:
        tool = by_name.get(call.name)
        if tool is None or not str(tool.effect or "").startswith("read"):
            return []
        args = call.arguments
        if isinstance(args, dict) and set(args) == {"_raw"}:
            args = str(args["_raw"])
        if dedupe and (call.name, json.dumps(args, sort_keys=True, default=str)) in seen_calls:
            return []
        batch.append((call, args, tool))
    return batch


def _run_parallel(batch, began, parallel: int):
    """Run the batch at once; return each call's real start and duration.

    Timed individually rather than as a block: the overlap is the finding,
    and a block time would hide it behind one number.
    """
    from concurrent.futures import ThreadPoolExecutor

    out = [None] * len(batch)

    def one(i):
        call, args, tool = batch[i]
        started = began()
        t0 = time.monotonic()
        try:
            value, exc = (tool.fn(**args) if isinstance(args, dict) else tool.fn(args)), None
        except Exception as err:      # the failure is the evidence; it is recorded, not swallowed
            value, exc = None, err
        out[i] = (call, args, tool, started, round(max(0.0, time.monotonic() - t0), 6), value, exc)

    with ThreadPoolExecutor(max_workers=min(parallel, len(batch))) as pool:
        list(pool.map(one, range(len(batch))))
    # returned in the order the model asked, never in the order they landed:
    # a reshuffled trace would be a different run from the one that happened
    return out


def _drive(recorder: Recorder, provider: Provider, messages: list, tools: list,
           task: dict, grade: Callable, max_steps: int, *,
           max_tool_errors: int = 3, dedupe: bool = False,
           require_before_answer: Optional[str] = None,
           gate_writes: bool = False, parallel: int = 0) -> bool:
    """The loop itself, shared by a fresh run and a counterfactual replay:
    prompt, act, observe, until the answer or the budget.  ``messages`` is
    continued in place (a replay hands in a rebuilt prefix).  Returns
    whether the run answered; every other outcome is a declared
    termination on the recorder.

    ``max_tool_errors``, ``dedupe``, ``require_before_answer`` and
    ``gate_writes`` are the loop's scaffold knobs, read from ``budget`` by
    :func:`run_task` so that the settings a run obeyed are recorded on its
    trace and read back by
    :func:`deepcompare.harnessevo.fingerprint`."""
    by_name = {t.name: t for t in tools}
    declarations = [t.declaration() for t in tools]
    #: the run's own zero. Every step records when it began against it, so
    #: the timeline is read rather than reconstructed by summing durations
    #: — a sum that would assert this loop never overlapped anything.
    origin = time.monotonic()

    def began():
        """Seconds from the run's start, for the paths that measure their
        own duration too.  Everywhere else the recorder owns both numbers
        and is left to: a start from this clock beside a duration measured
        from the recorder's own mark describes no single interval, and the
        difference shows up as a phantom overlap in `timing.timeline`."""
        return round(max(0.0, time.monotonic() - origin), 6)

    tool_errors = 0
    answered = False
    #: identical (tool, arguments) already executed, for `dedupe`
    seen_calls: dict = {}
    #: whether the required tool has been called, for `require_before_answer`
    required_done = require_before_answer is None
    pushed_back = False
    #: whether anything declaring a read has run, for `gate_writes`, and
    #: whether the gate has already spent its one refusal
    read_done = False
    held_write = False
    for _turn in range(max_steps):
        turn_at = began()
        try:
            response: ProviderResponse = provider.complete(messages, declarations)
        except ProviderError as exc:
            recorder.reason(f"provider failure: {exc}", error=True, started_s=turn_at,
                            note="the model endpoint failed; harness fault")
            recorder.terminate("infrastructure_error")
            break
        tokens = (response.usage.get("input_tokens", 0)
                  + response.usage.get("output_tokens", 0)) or None
        # how much of the input the provider served from its own cache, when
        # it said so.  None stays None: a working cache and a provider that
        # does not report one must not look the same.
        def _count(key):
            v = response.usage.get(key)
            return int(v) if isinstance(v, int) and not isinstance(v, bool) else None
        cached = _count("cached_input_tokens")
        # the split, not just the sum: context re-sent and text generated
        # cost differently and are moved by different fixes
        split = {"input_tokens": _count("input_tokens"), "output_tokens": _count("output_tokens")}

        if not response.tool_calls:
            answer = response.text.strip()
            # the verification gate: an answer that never called the tool the
            # harness requires is pushed back *once*, with the reason, and the
            # loop continues.  Once only, and the second answer stands however
            # it comes: a harness that refuses until it gets what it wants is
            # not measuring an agent, it is writing one.  Note that a gate
            # that works shows up as the scaffold carrying the run —
            # `harnessevo.absorption` is built to see exactly this.
            if not required_done and not pushed_back:
                pushed_back = True
                recorder.reason(
                    f"the harness requires {require_before_answer!r} before an answer; "
                    f"this turn answered without calling it",
                    scaffold="answer_gate",
                    note="scaffold: verification gate, pushed back once")
                messages.append({"role": "assistant", "content": response.text})
                messages.append({"role": "user", "content": (
                    f"Before answering, call the {require_before_answer} tool to check your work, "
                    f"then answer.")})
                continue
            verdict = grade(answer, task)
            if verdict is None:
                raise ValueError(
                    f"grader returned None for task {task.get('id')!r}; a "
                    "verdict must be True or False")
            recorder.answer(answer, success=bool(verdict), tokens=tokens, cached_tokens=cached, **split,
                            latency_s=response.latency_s, started_s=turn_at,
                            model={"name": response.model} if response.model else None)
            answered = True
            break

        # a turn that both talks and acts: the prose is the agent's
        # reasoning, recorded before the calls it motivates
        if response.text.strip():
            recorder.reason(response.text.strip(), tokens=tokens, cached_tokens=cached, **split,
                            latency_s=response.latency_s, started_s=turn_at)
        messages.append({"role": "assistant", "content": response.text,
                         "tool_calls": [c.as_dict() for c in response.tool_calls]})

        # The engine's own fix for `parallel_reads`: issue independent
        # read-only calls at once instead of one after another.
        #
        # Only when *every* call in the turn is a declared read. A write in
        # the batch sends the whole turn down the sequential path, because
        # the order of writes is part of what the run did and reordering
        # them would be the harness changing the agent's behaviour rather
        # than its schedule. A cache hit does the same, for simplicity
        # rather than principle — the repeat is already free.
        #
        # The calls run outside the recorder and are recorded afterwards in
        # the order the model made them, each with the start and duration
        # actually measured. Two things follow, and both matter: the
        # recorded order stays the model's, so nothing downstream sees a
        # reshuffled trace; and the *timings overlap*, which is exactly what
        # `timing.timeline` now reads. Before `started_s` this change could
        # not have been represented at all — a running sum would have drawn
        # the concurrent run as the slow one.
        batch = _parallel_batch(response.tool_calls, by_name, parallel, dedupe, seen_calls)
        if batch:
            results = _run_parallel(batch, began, parallel)
            for call, args, tool, started, dur, value, exc in results:
                if exc is None:
                    result_text = _render_result(value)
                    if dedupe:
                        seen_calls[(call.name, json.dumps(args, sort_keys=True, default=str))] = result_text
                    recorder.tool(call.name, args, result_text, effect=tool.effect,
                                  started_s=started, latency_s=dur,
                                  note="scaffold: issued concurrently with the other reads of this turn")
                else:
                    result_text = f"error: {exc.__class__.__name__}: {exc}"
                    recorder.tool(call.name, args, result_text, effect=tool.effect, error=True,
                                  started_s=started, latency_s=dur,
                                  note="scaffold: issued concurrently with the other reads of this turn")
                    tool_errors += 1
                read_done = True
                if require_before_answer is not None and call.name == require_before_answer:
                    required_done = True
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "name": call.name, "content": result_text})
            if tool_errors >= max_tool_errors:
                recorder.terminate("too_many_errors")
                break
            continue

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
                # an argument string the provider could not parse is the
                # call as the model made it: recorded verbatim, handed to
                # the tool as one positional string
                args = call.arguments
                if isinstance(args, dict) and set(args) == {"_raw"}:
                    args = str(args["_raw"])
                # a cache is only sound over calls that *read*: serving a
                # write from a cache means the write silently did not happen
                # the second time.  An undeclared effect is undeclared, not
                # read-only, so it is executed.
                effect = str(tool.effect or "")
                cacheable = dedupe and effect.startswith("read")
                key = (call.name, json.dumps(args, sort_keys=True, default=str))
                if gate_writes and effect.startswith("write") and not read_done and not held_write:
                    # the engine's own fix for `safety`: read before you
                    # write.  The call is refused *once* and then the gate
                    # is spent, for the same reason the answer gate is —
                    # a harness that keeps refusing is writing the agent.
                    #
                    # The step is recorded because the agent did make the
                    # call, and with `error` because it did not succeed.
                    # It still reads as a write in `process.write_ledger`,
                    # and it should: the agent did attempt a blind write,
                    # and `writes_before_any_read` will go on saying so.
                    # This gate protects the *state*; it does not teach the
                    # agent to look first and does not launder the attempt
                    # out of the record.  Those are two different claims
                    # and the trace keeps them apart.
                    held_write = True
                    result_text = (f"error: this harness requires a read before a write; "
                                   f"{call.name} was not executed. Look at the state first, then act.")
                    recorder.tool(call.name, args, result_text, error=True, scaffold="write_gate",
                                  note="scaffold: read-before-write gate, the call was not executed")
                elif cacheable and key in seen_calls:
                    # the engine's own recommendation for `result_cache`:
                    # memoise identical tool calls at the harness layer. The
                    # step is still recorded — the agent did make the call —
                    # with a note saying it was served from the cache, so the
                    # repeat is visible rather than hidden.
                    result_text = seen_calls[key]
                    recorder.tool(call.name, args, result_text, effect=tool.effect, scaffold="cache_hit",
                                  note="scaffold: served from the harness cache, not re-executed")
                    read_done = True
                else:
                    try:
                        result = recorder.tool(call.name, args,
                                               call=tool.fn, effect=tool.effect)
                        result_text = _render_result(result)
                        # only a read that returned counts as having looked:
                        # a read that raised showed the agent nothing, and a
                        # gate satisfied by a failed lookup is not a gate
                        if effect.startswith("read"):
                            read_done = True
                        if cacheable:
                            seen_calls[key] = result_text
                    except Exception as exc:
                        result_text = f"error: {exc.__class__.__name__}: {exc}"
                        tool_errors += 1
                if require_before_answer is not None and call.name == require_before_answer:
                    required_done = True
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "name": call.name, "content": result_text})
        if tool_errors >= max_tool_errors:
            recorder.terminate("too_many_errors")
            break
    else:
        recorder.terminate("max_steps")

    if not answered and recorder._termination is None:
        recorder.terminate("agent_error")
    return answered
