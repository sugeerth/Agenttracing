"""Recording a trajectory while the agent runs (SCHEMA.md v22).

Every analysis in this repository begins with a conformant trace, and until
now there were exactly two ways to obtain one: write the JSON by hand, or own
a log that :mod:`deepcompare.adapters` already understands.  Neither helps the
person writing an agent *right now*, which is the person who most wants the
comparison.  This module closes that gap — five lines around an agent loop and
the run lands on disk as a validated trajectory.

The design follows one rule taken from the rest of the tool: **a recorder must
not make the trace look better informed than the run was.**  That has three
concrete consequences here.

* **Estimated is never written as measured.**  A token count the caller
  supplies is a measurement; ``len(text)/4`` is an estimate.  Both end up in
  ``steps[].tokens`` because every downstream metric needs a number there, so
  each step also carries ``tokens_basis`` and the file carries a
  ``token_accounting`` summary.  A trace that quietly estimated everything and
  a trace that measured everything must not read identically.
* **Termination is declared, not deduced.**  The recorder knows exactly one
  thing the log cannot recover afterwards: whether the ``with`` block ended by
  finishing or by raising.  It records that (``agent_stop`` / ``agent_error``,
  plus the two exception types with an unambiguous reading) and leaves
  ``max_steps``, ``timeout`` and the harness reasons to
  :meth:`Recorder.terminate`, because the harness is the only party that knows
  them.  Nothing else is inferred.
* **What the agent was offered is recorded, not just what it did.**  Without
  ``tools`` and per-step ``effect``, :mod:`deepcompare.process` reports
  schema grounding, permission and blind-write checks as *unmeasurable* rather
  than passing — an unchecked call is not a valid one.  So the tool list and
  the effects are first-class constructor arguments rather than an advanced
  feature, and a recorded run is measurable by default.

Wall-clock timing is used deliberately.  Analysis in this tool is
deterministic on purpose; *recording* cannot be, because latency is one of the
things being recorded.  The determinism boundary is the trace file: everything
after it is reproducible, everything before it is a measurement of the world.

Failed runs are the interesting ones, so an exception inside the block still
writes a valid trace — with a synthesised final step that says, in the trace
itself, that no answer was produced.

Zero dependencies, as everywhere else here.  The provider-response helper is
duck-typed: it reads OpenAI-, Anthropic- and Ollama-shaped payloads (objects
or dicts) through ``getattr``/``get`` and hands any logprobs to
:mod:`deepcompare.logprobs`, so the module imports fine with no SDK installed.

Typical use::

    from deepcompare.record import Recorder

    with Recorder(task="t01_refund", prompt="Cancel booking QX7T2",
                  agent="my-agent", model="claude-sonnet-5",
                  tools=TOOLS, budget={"max_steps": 20}) as run:
        run.plan("Read the booking, then cancel")
        call = run.tool("get_booking", {"reference": "QX7T2"}, effect="read")
        call.observe(get_booking("QX7T2"))
        run.answer("Cancelled and refunded.", success=True)
    # traces/t01_refund__my-agent.json
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .logprobs import extract_logprobs, telemetry_from_logprobs
from .trace import (
    EFFECTS,
    QUALITY_VALUES,
    SCHEMA_VERSION,
    STEP_TYPES,
    TERMINATIONS,
    Trajectory,
)

#: how a token count is produced when the caller has none.  Named in the file
#: so a reader can tell what the number is worth; it is the same estimator the
#: adapters use, kept identical so recorded and converted traces are on one
#: scale rather than two.
TOKEN_ESTIMATOR = "len(text)/4"

#: filename separator for ``<task>__<agent>[__<run>].json``.  The CLI parses
#: run ids back out of it, so components containing it are rejected rather
#: than silently producing a file the ``runs`` command misreads.
NAME_SEP = "__"

#: exception type -> termination reason, for the two readings that are not a
#: guess.  Everything else is ``agent_error``: the code inside the block is
#: the agent, and an exception escaping it is the agent failing.  Harness
#: reasons (``infrastructure_error``, ``max_steps``, ``context_window_exceeded``)
#: are deliberately absent — only the harness knows those, and it can say so
#: with :meth:`Recorder.terminate`.
_EXCEPTION_TERMINATION: tuple[tuple[type, str], ...] = (
    (TimeoutError, "timeout"),
    (KeyboardInterrupt, "user_stop"),
    (SystemExit, "user_stop"),
)

#: what a synthesised answer step says when the run produced none.  Written
#: into the trace rather than left blank so a reader of the JSON — or of a
#: report built from it — sees that the answer is the recorder's, not the
#: agent's.
NO_ANSWER = "<no answer: {reason}>"


def estimate_tokens(text: str) -> int:
    """Token estimate for a piece of text (``len/4``), matching the adapters.

    An estimate, and labelled one everywhere it is used.  Kept identical to
    :mod:`deepcompare.adapters` so a run recorded live and the same run
    converted from a log do not sit on two different scales.
    """
    return max(1, len(text) // 4) if text else 0


def _check(condition: bool, message: str) -> None:
    """Raise ``ValueError(message)`` unless ``condition`` holds.

    Recording errors are raised where the mistake was made, not at write
    time: a stack trace pointing at the offending ``run.tool(...)`` is worth
    more than one pointing at ``__exit__`` half a run later.
    """
    if not condition:
        raise ValueError(message)


def _component(value: str, label: str) -> str:
    """Validate one filename component of ``<task>__<agent>[__<run>]``."""
    text = str(value or "").strip()
    _check(bool(text), f"{label} must be a non-empty string")
    _check(NAME_SEP not in text,
           f"{label} {text!r} contains {NAME_SEP!r}, which separates fields in "
           f"trace filenames (<task>{NAME_SEP}<agent>{NAME_SEP}<run>.json); "
           f"the 'runs' command would read the wrong run id out of it")
    for bad in ("/", "\\", os.sep):
        _check(bad not in text, f"{label} {text!r} must not contain {bad!r}")
    return text


def _render_value(value: Any) -> str:
    """One argument value, in the ``name(k=v)`` dialect the tools here parse.

    :func:`deepcompare.tooldiff.parse_args` — which the tool diff, the repeat
    signatures, the schema check and the argument-provenance check all read
    through — understands ``k='v'``, bare numbers and bare ``true``/``false``.
    Rendering to that dialect is what makes a recorded call *comparable*; a
    ``repr`` of a dict would be recorded faithfully and analysed as nothing.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # Single quotes unless the value contains one; json.dumps then gives a
        # correctly escaped double-quoted form rather than a mangled value.
        return f"'{value}'" if "'" not in value else json.dumps(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return f"'{value}'"


def render_call(name: str, args: Union[dict, str, None]) -> str:
    """A call string for a tool step: ``get_booking(reference='QX7T2')``.

    A string ``args`` is taken verbatim — callers who already hold a rendered
    call, or whose "arguments" are a search query, should not have it
    re-quoted around them.
    """
    if isinstance(args, str):
        return args
    if not args:
        return f"{name}()"
    body = ", ".join(f"{key}={_render_value(value)}"
                     for key, value in args.items())
    return f"{name}({body})"


def _as_payload(response: Any) -> Any:
    """A provider response as plain data, without importing any SDK.

    Tries the serialisation methods the SDKs converge on and falls back to the
    object itself, which the ``getattr`` readers below still handle.
    """
    for method in ("model_dump", "to_dict", "dict"):
        fn = getattr(response, method, None)
        if callable(fn):
            try:
                data = fn()
            except TypeError:
                continue
            if isinstance(data, dict):
                return data
    return response


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a mapping or an attribute off an object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _first_int(obj: Any, keys: tuple[str, ...]) -> Optional[int]:
    for key in keys:
        value = _get(obj, key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _response_text(payload: Any) -> Optional[str]:
    """The generated text out of an OpenAI/Anthropic/Ollama-shaped response."""
    direct = _get(payload, "output_text")          # OpenAI Responses API
    if isinstance(direct, str) and direct:
        return direct

    choices = _get(payload, "choices")             # OpenAI chat completions
    if isinstance(choices, (list, tuple)) and choices:
        message = _get(choices[0], "message")
        content = _get(message, "content") if message is not None else None
        if isinstance(content, str) and content:
            return content
        text = _get(choices[0], "text")
        if isinstance(text, str) and text:
            return text

    content = _get(payload, "content")             # Anthropic content blocks
    if isinstance(content, (list, tuple)) and content:
        parts = [str(_get(block, "text") or "") for block in content]
        joined = "".join(part for part in parts if part)
        if joined:
            return joined
    if isinstance(content, str) and content:
        return content

    message = _get(payload, "message")             # Ollama
    if message is not None:
        text = _get(message, "content")
        if isinstance(text, str) and text:
            return text
    return None


def usage_from_response(response: Any) -> dict:
    """Pull text, token usage and telemetry out of a provider response.

    Duck-typed on purpose: this reads OpenAI, Anthropic, Ollama and anything
    OpenAI-compatible (vLLM, TGI) in one pass, through ``get``/``getattr``,
    with no SDK imported and nothing installed.  Logprobs go to
    :mod:`deepcompare.logprobs` rather than being re-derived here, so a
    recorded step's ``model`` block is the same block the converters produce
    and :mod:`deepcompare.uncertainty` reads.

    Returns ``{"text", "input_tokens", "output_tokens", "model", "telemetry"}``
    with ``None`` for anything the response did not carry — the caller then
    estimates and *says* it estimated, rather than receiving a zero that looks
    like a measurement.
    """
    payload = _as_payload(response)
    usage = _get(payload, "usage") or {}
    input_tokens = _first_int(usage, ("prompt_tokens", "input_tokens",
                                      "prompt_eval_count"))
    output_tokens = _first_int(usage, ("completion_tokens", "output_tokens",
                                       "eval_count"))
    if input_tokens is None:
        input_tokens = _first_int(payload, ("prompt_eval_count",))
    if output_tokens is None:
        output_tokens = _first_int(payload, ("eval_count",))

    model_name = _get(payload, "model")
    telemetry = None
    entries = extract_logprobs(payload if isinstance(payload, dict) else None)
    if entries:
        telemetry = telemetry_from_logprobs(
            entries,
            temperature=_get(payload, "temperature"),
            source="provider-logprobs",
        )
    return {
        "text": _response_text(payload),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": str(model_name) if isinstance(model_name, str) else None,
        "telemetry": telemetry,
    }


class RecordedStep:
    """A handle on the step just recorded, so its observation can arrive later.

    An agent calls a tool on one line and gets the result on the next; a
    recorder that demanded both halves at once would make the caller restructure
    their code around the instrumentation.  The handle also lets the wall clock
    do something useful: the time between recording the call and observing its
    result *is* the tool's latency, and it is attributed to this step.
    """

    __slots__ = ("_recorder", "_data")

    def __init__(self, recorder: "Recorder", data: dict) -> None:
        self._recorder = recorder
        self._data = data

    @property
    def index(self) -> int:
        return int(self._data["index"])

    @property
    def name(self) -> str:
        return str(self._data["name"])

    def as_dict(self) -> dict:
        """The step as it will be written (a copy — edit through the API)."""
        return dict(self._data)

    def observe(self, output: Any = None, *, error: Optional[bool] = None,
                tokens: Optional[int] = None, response: Any = None,
                note: Optional[str] = None, quality: Optional[str] = None,
                latency_s: Optional[float] = None) -> "RecordedStep":
        """Record what this step returned.

        ``error=True`` marks the observation as a failure — a *declaration*,
        which :mod:`deepcompare.process` believes over its own text heuristic,
        and the difference matters: an observation that merely mentions the
        word "error" reads identically to one that is an error.
        """
        self._recorder._observe(self._data, output, error=error, tokens=tokens,
                                response=response, note=note, quality=quality,
                                latency_s=latency_s)
        return self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<RecordedStep {self._data['index']} "
                f"{self._data['type']}:{self._data['name']}>")


class Recorder:
    """Records one agent run and writes it as a validated trajectory.

    Constructor arguments split into the three things a trace needs to be
    worth comparing:

    *identity* — ``task``, ``prompt``, ``agent``, ``model``, ``version``,
    ``expected`` (the gold answer, when there is one) and ``run_id`` for
    repetitions of the same (task, agent) pair;

    *what the agent was allowed to do* — ``tools`` (the SCHEMA v22 list, with
    ``effect`` and ``parameters``) and ``budget`` (e.g. ``{"max_steps": 20}``).
    These are what make the process analysis measurable; omitting them is
    allowed and reported downstream as unmeasurable, never as a pass;

    *where it lands* — ``out_dir`` (``traces/`` by default; ``None`` records
    without writing, which is what tests and pipelines want).

    Used as a context manager the exit is the write: the trace is built,
    validated through :meth:`Trajectory.from_json` and written atomically, so
    a half-written or invalid file never appears on disk.
    """

    def __init__(self, task: str, prompt: str, agent: str, model: str = "",
                 version: str = "", *, expected: Optional[str] = None,
                 tools: Optional[list] = None, budget: Optional[dict] = None,
                 run_id: Optional[str] = None,
                 out_dir: Optional[Union[str, Path]] = "traces",
                 trace_id: Optional[str] = None,
                 input_tokens: Optional[int] = None,
                 stream: bool = False) -> None:
        self.task = _component(task, "task")
        #: stream=True writes the run-so-far to ``<file>.live.json`` after
        #: every step (and every observation), for a watcher to draw while
        #: the agent is still running; the live file is removed on close
        self.stream = bool(stream)
        self.agent = _component(agent, "agent")
        self.run_id = _component(run_id, "run_id") if run_id else None
        _check(isinstance(prompt, str) and prompt.strip() != "",
               "prompt must be a non-empty string — it is the task text every "
               "comparison, alignment and provenance check reads against")
        self.prompt = prompt
        self.model = str(model or "")
        self.version = str(version or "")
        self.expected = expected
        _check(expected is None or isinstance(expected, str),
               "expected must be a string (the gold answer) or None")

        self.tools = self._validate_tools(tools)
        self._tool_effects = {
            str(tool["name"]): tool.get("effect") for tool in self.tools
        }
        self.budget = self._validate_budget(budget)
        self.out_dir = None if out_dir is None else Path(out_dir)
        self.trace_id = trace_id or "-".join(
            part for part in (self.task, self.agent, self.run_id) if part)

        self._steps: list[dict] = []
        self._answered = False
        self._closed = False
        self._termination: Optional[str] = None
        self._declared_termination = False
        self._outcome: dict = {"success": None, "answer": "", "score": None}
        self._cost_usd = 0.0
        self._measured_input_tokens = 0
        self._input_tokens = input_tokens
        _check(input_tokens is None or (isinstance(input_tokens, int)
                                        and not isinstance(input_tokens, bool)),
               "input_tokens must be an integer or None")
        self._path: Optional[Path] = None
        #: the boundary the next step's latency is measured from.  Reset on
        #: ``__enter__`` so setup done between constructing the recorder and
        #: entering the block is not billed to the agent's first step.
        self._mark = time.monotonic()

    # ------------------------------------------------------------------
    # construction-time validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_tools(tools: Optional[list]) -> list:
        """Check the declared tool list before a single step is recorded.

        The tool list is the thing several analyses are *checked against*, so
        a malformed one has to fail here rather than at write time, or the
        caller discovers it after the expensive part of the run.
        """
        if tools is None:
            return []
        _check(isinstance(tools, (list, tuple)),
               "tools must be a list of {'name', 'effect'?, 'parameters'?} objects")
        checked = []
        for position, tool in enumerate(tools):
            if isinstance(tool, str):
                tool = {"name": tool}
            _check(isinstance(tool, dict) and str(tool.get("name", "")),
                   f"tools[{position}] must be an object with a name (or a name string)")
            effect = tool.get("effect")
            _check(effect is None or effect in EFFECTS,
                   f"tools[{position}]: invalid effect {effect!r}; must be one of "
                   f"{', '.join(EFFECTS)} or None")
            parameters = tool.get("parameters")
            _check(parameters is None or isinstance(parameters, dict),
                   f"tools[{position}]: parameters must be a JSON-schema object or None")
            checked.append(dict(tool))
        return checked

    @staticmethod
    def _validate_budget(budget: Optional[dict]) -> dict:
        if budget is None:
            return {}
        _check(isinstance(budget, dict), "budget must be an object, e.g. {'max_steps': 20}")
        for key, value in budget.items():
            _check(isinstance(value, (int, float)) and not isinstance(value, bool),
                   f"budget.{key} must be a number")
        return dict(budget)

    # ------------------------------------------------------------------
    # the low-level step
    # ------------------------------------------------------------------

    def step(self, type: str, name: str = "", input: str = "", output: str = "",
             *, tokens: Optional[int] = None, latency_s: Optional[float] = None,
             quality: Optional[str] = None, note: Optional[str] = None,
             error: Optional[bool] = None, effect: Optional[str] = None,
             response: Any = None, cost_usd: float = 0.0,
             model: Optional[dict] = None) -> RecordedStep:
        """Record one step; every other method here is sugar over this one.

        Arguments are named for the SCHEMA fields they fill, so the API is
        readable as the contract.  ``tokens`` given is a measurement; omitted,
        the count is estimated at write time from the step's own text and
        marked as an estimate.  ``latency_s`` given is a measurement; omitted,
        it is the wall time since the previous step boundary.

        ``response`` accepts a provider response object (see
        :func:`usage_from_response`) and fills output text, real token usage
        and model telemetry from it in one go.
        """
        _check(type in STEP_TYPES,
               f"invalid step type {type!r}; must be one of {', '.join(STEP_TYPES)}")
        _check(not self._answered,
               "the answer step has already been recorded; 'answer' may only "
               "appear as the last step of a trajectory")
        _check(not self._closed, "this recorder is closed; start a new Recorder for a new run")
        _check(quality is None or quality in QUALITY_VALUES,
               f"invalid quality {quality!r}; must be one of "
               f"{', '.join(QUALITY_VALUES)} or None")
        _check(effect is None or effect in EFFECTS,
               f"invalid effect {effect!r}; must be one of {', '.join(EFFECTS)} or None")
        _check(error is None or isinstance(error, bool),
               "error must be True, False or None (None = undeclared)")
        _check(tokens is None or (isinstance(tokens, int) and not isinstance(tokens, bool)),
               "tokens must be an integer measurement or None (then it is estimated)")
        _check(note is None or isinstance(note, str), "note must be a string or None")

        now = time.monotonic()
        elapsed = max(0.0, now - self._mark)
        self._mark = now

        data = {
            "index": len(self._steps),
            "type": type,
            "name": str(name or ""),
            "input": _text(input),
            "output": _text(output),
            "tokens": 0,
            "latency_s": round(float(latency_s), 6) if latency_s is not None
            else round(elapsed, 6),
            "quality": quality,
            "note": note,
            "model": model,
            "error": error,
            "effect": effect,
            # Not a SCHEMA field: metadata about the recording, kept beside the
            # number it qualifies so an estimate cannot read as a measurement.
            "tokens_basis": "measured" if tokens is not None else "estimated",
        }
        if tokens is not None:
            data["tokens"] = int(tokens)
        self._steps.append(data)
        self._cost_usd += float(cost_usd or 0.0)
        handle = RecordedStep(self, data)
        if response is not None:
            # The response is the measurement; applying it after the step
            # exists keeps one code path for "response now" and "response
            # later", and lets it correct an estimated count into a real one.
            self._observe(data, None, response=response, latency_s=latency_s)
        self._stream_now()
        return handle

    # ------------------------------------------------------------ streaming

    @property
    def live_path(self) -> Optional[Path]:
        """Where the run-so-far is written while streaming, else None."""
        if not self.stream or self.out_dir is None:
            return None
        return Path(self.out_dir) / (self.filename[:-len(".json")] + ".live.json")

    def _stream_now(self) -> None:
        """Write the partial trace (no validation: it has no answer yet)."""
        path = self.live_path
        if path is None or self._closed:
            return
        data = self._payload(validate=False)
        data["in_progress"] = True
        data["updated_at"] = time.time()
        try:
            directory = path.parent
            directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + f".tmp{os.getpid()}")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass   # streaming is best-effort; the final write is what counts

    # ------------------------------------------------------------------
    # sugar
    # ------------------------------------------------------------------

    def plan(self, text: str, *, name: str = "plan", **kwargs: Any) -> RecordedStep:
        """The agent stating or revising its plan."""
        return self.step("plan", name, text, **kwargs)

    def reason(self, text: str, *, name: str = "reason", **kwargs: Any) -> RecordedStep:
        """Intermediate reasoning or a decision, in the agent's own words."""
        return self.step("reason", name, text, **kwargs)

    def search(self, query: str, *, name: str = "search", output: str = "",
               **kwargs: Any) -> RecordedStep:
        """A search query issued (web, vector store, code search)."""
        return self.step("search", name, query, output, **kwargs)

    def retrieve(self, what: str, *, name: str = "retrieve", output: str = "",
                 **kwargs: Any) -> RecordedStep:
        """Results selected or received from a retrieval step."""
        return self.step("retrieve", name, what, output, **kwargs)

    def read(self, source: str, *, name: Optional[str] = None, output: str = "",
             **kwargs: Any) -> RecordedStep:
        """A document, page or file read; ``source`` names what was opened."""
        return self.step("read", name if name is not None else source, source,
                         output, **kwargs)

    def tool(self, name: str, args: Union[dict, str, None] = None,
             output: Any = None, *, effect: Optional[str] = None,
             error: Optional[bool] = None, call: Optional[Callable] = None,
             type: str = "tool_call", **kwargs: Any) -> Any:
        """Record a tool call.  ``args`` is rendered as ``name(k='v')``.

        Two shapes, both honest about timing:

        * without ``call``, the step is recorded now and returns a
          :class:`RecordedStep`; ``handle.observe(result)`` (or
          :meth:`observe`) attaches the result, and the elapsed time in
          between becomes the step's latency;
        * with ``call=fn``, the recorder invokes ``fn(**args)`` (or ``fn(args)``
          when ``args`` is a bare string), times it, records the return value
          as the observation — or marks the step ``error=True`` and re-raises
          — and returns whatever ``fn`` returned.

        ``effect`` is the read/write declaration.  When it is omitted but the
        tool was declared in ``tools`` with an effect, that declaration is
        copied onto the step, so the step is self-describing.  It is never
        *invented* from the tool name — :mod:`deepcompare.process` does that
        inference itself, and labels it as inferred.

        Calls to tools that were never declared are recorded exactly as made:
        an unauthorised call is a finding for the grounding check, not
        something a recorder should quietly prevent.
        """
        _check(bool(str(name or "")), "a tool call needs a name")
        if effect is None:
            effect = self._tool_effects.get(str(name))
        handle = self.step(type, str(name), render_call(str(name), args),
                           "" if output is None else output,
                           effect=effect, error=error if call is None else None,
                           **kwargs)
        if call is None:
            return handle
        _check(callable(call), "call must be a callable, or None")
        try:
            result = call(**args) if isinstance(args, dict) else (
                call(args) if args is not None else call())
        except Exception as exc:
            # The failed call is the evidence: record it as an error
            # observation and let the exception continue on its way.
            handle.observe(f"{exc.__class__.__name__}: {exc}", error=True)
            raise
        handle.observe(_awaited(result), error=error)
        return result

    def answer(self, text: str, success: Optional[bool] = None, *,
               score: Optional[float] = None, termination: Optional[str] = None,
               **kwargs: Any) -> RecordedStep:
        """Record the final answer and the run's outcome.

        ``success`` is required and has no default on purpose.  It is the
        grader's verdict, not the agent's, and a recorder that defaulted it to
        ``True`` would manufacture success rates out of nothing.
        """
        _check(isinstance(success, bool),
               "answer(...) needs success=True or success=False — whether the run "
               "succeeded is the grader's verdict and will not be guessed here")
        _check(score is None or isinstance(score, (int, float)),
               "score must be a number or None")
        handle = self.step("answer", kwargs.pop("name", "final"),
                           kwargs.pop("input", text), text, **kwargs)
        self._answered = True
        self._outcome = {
            "success": bool(success),
            "answer": _text(text),
            "score": None if score is None else float(score),
        }
        if termination is not None:
            self.terminate(termination)
        return handle

    # ------------------------------------------------------------------
    # observations, cost, termination
    # ------------------------------------------------------------------

    def observe(self, output: Any = None, **kwargs: Any) -> RecordedStep:
        """Attach an observation to the most recent step.

        The convenience form of ``handle.observe(...)`` for the common case
        where the call and its result are adjacent in the code.
        """
        if isinstance(output, RecordedStep):
            raise TypeError(
                "observe() takes the tool's result, not the step handle; use "
                "handle.observe(result) or run.observe(result)")
        _check(bool(self._steps),
               "there is no step to observe yet — record one first, e.g. "
               "run.tool('get_booking', {...}) then run.observe(result)")
        handle = RecordedStep(self, self._steps[-1])
        return handle.observe(output, **kwargs)

    def _observe(self, data: dict, output: Any, *, error: Optional[bool] = None,
                 tokens: Optional[int] = None, response: Any = None,
                 note: Optional[str] = None, quality: Optional[str] = None,
                 latency_s: Optional[float] = None) -> None:
        self._observe_inner(data, output, error=error, tokens=tokens, response=response,
                            note=note, quality=quality, latency_s=latency_s)
        # an observation changes a step a watcher may already be drawing
        self._stream_now()

    def _observe_inner(self, data: dict, output: Any, *, error: Optional[bool] = None,
                       tokens: Optional[int] = None, response: Any = None,
                       note: Optional[str] = None, quality: Optional[str] = None,
                       latency_s: Optional[float] = None) -> None:
        """Apply an observation to a step dict (the one path for all forms)."""
        _check(error is None or isinstance(error, bool),
               "error must be True, False or None (None = undeclared)")
        _check(quality is None or quality in QUALITY_VALUES,
               f"invalid quality {quality!r}; must be one of "
               f"{', '.join(QUALITY_VALUES)} or None")

        if response is not None:
            usage = usage_from_response(response)
            if usage["text"] and output is None and not data["output"]:
                # An output the caller passed explicitly wins over the one
                # recovered from the response: they know which part of a
                # multi-block reply is the step.
                output = usage["text"]
            if usage["output_tokens"] is not None and tokens is None:
                tokens = usage["output_tokens"]
            if usage["input_tokens"]:
                # What the server actually processed, re-sent history and all,
                # which is also what a provider bills for.
                self._measured_input_tokens += int(usage["input_tokens"])
            if usage["telemetry"] and not data.get("model"):
                data["model"] = usage["telemetry"]

        if output is not None:
            data["output"] = _text(output)
        if error is not None:
            data["error"] = error
        if note is not None:
            data["note"] = note
        if quality is not None:
            data["quality"] = quality
        if tokens is not None:
            _check(isinstance(tokens, int) and not isinstance(tokens, bool),
                   "tokens must be an integer measurement or None")
            data["tokens"] = int(tokens)
            data["tokens_basis"] = "measured"

        now = time.monotonic()
        if latency_s is not None:
            data["latency_s"] = round(float(latency_s), 6)
        elif self._steps and data is self._steps[-1]:
            # Time spent waiting for this observation belongs to this step.
            data["latency_s"] = round(data["latency_s"] + max(0.0, now - self._mark), 6)
        self._mark = now

    def add_cost(self, usd: float) -> None:
        """Add to the run's cost total (the only number nobody can estimate)."""
        _check(isinstance(usd, (int, float)) and not isinstance(usd, bool),
               "cost must be a number")
        self._cost_usd += float(usd)

    def terminate(self, reason: str) -> None:
        """Declare why the run stopped, overriding what the exit would record.

        Needed for the reasons only the harness knows — ``max_steps``,
        ``timeout``, ``context_window_exceeded``, ``infrastructure_error``.
        Nothing here deduces those: a trace whose last step is an answer looks
        the same whether the agent decided it was done or the harness cut it
        off, and every rate conditioned on termination would inherit the guess.
        """
        _check(reason in TERMINATIONS,
               f"invalid termination {reason!r}; must be one of "
               f"{', '.join(TERMINATIONS)} (tau2-bench's TerminationReason values)")
        self._termination = reason
        self._declared_termination = True

    def instrument(self, fn: Optional[Callable] = None, *,
                   name: Optional[str] = None, effect: Optional[str] = None,
                   type: str = "tool_call") -> Callable:
        """Wrap a tool function so that calling it records a step.

        The one place a decorator genuinely beats the explicit call: an agent
        with a tool registry can instrument the registry once and leave the
        agent loop untouched::

            @run.instrument(effect="write")
            def cancel_booking(reference, refund=False): ...

        Arguments are bound to their parameter names, so the recorded call
        string is the real one and stays parseable by the tool diff.

        Async tools get an async wrapper that awaits the result before
        recording it.  Recording the coroutine object instead would produce a
        trace that validates, reads plausibly, and contains
        ``<coroutine object ...>`` where the observation should be — the exact
        kind of quiet corruption everything downstream would inherit.
        """
        def decorate(target: Callable) -> Callable:
            tool_name = name or getattr(target, "__name__", "tool")

            if inspect.iscoroutinefunction(target):
                @functools.wraps(target)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    bound = _bind(target, args, kwargs)
                    handle = self.tool(tool_name, bound, effect=effect, type=type)
                    try:
                        result = await target(*args, **kwargs)
                    except Exception as exc:
                        handle.observe(f"{exc.__class__.__name__}: {exc}", error=True)
                        raise
                    handle.observe(result)
                    return result

                return async_wrapper

            @functools.wraps(target)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = _bind(target, args, kwargs)
                handle = self.tool(tool_name, bound, effect=effect, type=type)
                try:
                    result = target(*args, **kwargs)
                except Exception as exc:
                    handle.observe(f"{exc.__class__.__name__}: {exc}", error=True)
                    raise
                handle.observe(_awaited(result))
                return result

            return wrapper

        return decorate if fn is None else decorate(fn)

    # ------------------------------------------------------------------
    # closing and writing
    # ------------------------------------------------------------------

    def __enter__(self) -> "Recorder":
        self._mark = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        """Write the trace, whether the block finished or blew up.

        The runs that raise are the ones worth having, so the exception is
        recorded — as the termination reason, as a failed outcome, and as the
        text of a synthesised final step — and then allowed to propagate.
        """
        if self._closed:
            return False
        if exc_type is None:
            self.close()
            return False
        reason = "agent_error"
        for kind, mapped in _EXCEPTION_TERMINATION:
            if issubclass(exc_type, kind):
                reason = mapped
                break
        detail = f"{exc_type.__name__}: {exc}".strip().rstrip(":").strip()
        termination = None if self._declared_termination else reason
        if self._answered:
            # The agent finished and something afterwards raised.  The outcome
            # it recorded stands — overwriting a graded answer because of a
            # teardown failure would be the recorder inventing a verdict — and
            # the termination reason carries the fact that the block blew up.
            self.close(termination=termination)
        else:
            self.close(
                success=False, answer=NO_ANSWER.format(reason=detail),
                termination=termination,
                note=f"the run raised {detail}; final step written by the recorder")
        return False  # never swallow the caller's exception

    def close(self, *, success: Optional[bool] = None,
              answer: Optional[str] = None, score: Optional[float] = None,
              termination: Optional[str] = None,
              note: Optional[str] = None) -> Optional[Path]:
        """Finish the run: validate, then write (when ``out_dir`` is set).

        A run that never called :meth:`answer` still produces a conformant
        trajectory — SCHEMA requires a final ``answer`` step — but the step
        says so in its text and note, and the outcome is ``success: false``.
        The alternative, inventing an answer, is the one thing a recorder must
        never do.
        """
        _check(not self._closed, "this recorder is already closed")
        if termination is not None:
            self.terminate(termination)
        if not self._answered:
            reason = note or "the run ended without producing an answer"
            text = answer if answer is not None else NO_ANSWER.format(reason=reason)
            self.step("answer", "final", "", text, note=reason)
            self._answered = True
            self._outcome = {
                "success": bool(success) if success is not None else False,
                "answer": text,
                "score": score,
            }
        else:
            if success is not None:
                self._outcome["success"] = bool(success)
            if answer is not None:
                self._outcome["answer"] = answer
            if score is not None:
                self._outcome["score"] = score
        if self._termination is None:
            # The one thing only the recorder knows: the block ran to its end.
            self._termination = "agent_stop"
        self._closed = True

        data = self.to_dict()
        if self.out_dir is not None:
            self._path = self._write(data)
            live = self.live_path
            if live is not None:
                try:
                    live.unlink()
                except OSError:
                    pass
        return self._path

    def to_dict(self) -> dict:
        """The trajectory as a dict, validated before it is returned.

        Validation happens here rather than in :meth:`_write` so the check is
        the same whether the trace is written, posted, or kept in memory.
        """
        return self._payload(validate=True)

    def _payload(self, validate: bool = True) -> dict:
        steps = []
        for raw in self._steps:
            step = dict(raw)
            if step["tokens_basis"] == "estimated":
                # Estimated last, when the text is final: an observation that
                # arrived after the call must be part of what is counted.
                step["tokens"] = estimate_tokens(
                    " ".join(part for part in (step["input"], step["output"]) if part))
            steps.append(step)

        estimated_input = self._input_tokens is None and not self._measured_input_tokens
        if self._input_tokens is not None:
            input_tokens = int(self._input_tokens)
        elif self._measured_input_tokens:
            input_tokens = self._measured_input_tokens
        else:
            input_tokens = estimate_tokens(self.prompt)

        data = {
            "schema_version": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "run_id": self.run_id or "r1",
            "agent": {"name": self.agent, "model": self.model,
                      "version": self.version},
            "task": {"id": self.task, "prompt": self.prompt,
                     "expected": self.expected},
            "outcome": {
                "success": bool(self._outcome["success"]) if self._answered else False,
                "answer": self._outcome["answer"] if self._answered else "",
                "score": self._outcome["score"] if self._answered else None,
                "termination": self._termination,
            },
            "totals": {
                "input_tokens": input_tokens,
                "output_tokens": sum(step["tokens"] for step in steps),
                "cost_usd": round(self._cost_usd, 6),
                "latency_s": round(sum(step["latency_s"] for step in steps), 6),
            },
            "steps": steps,
            "tools": self.tools,
            "budget": self.budget,
            "token_accounting": self.token_accounting(steps, estimated_input),
        }
        if not validate:
            return data
        try:
            Trajectory.from_json(data)
        except ValueError as exc:
            raise ValueError(
                f"the recorder produced an invalid trajectory and refused to "
                f"write it: {exc}") from exc
        return data

    def token_accounting(self, steps: Optional[list] = None,
                         estimated_input: Optional[bool] = None) -> dict:
        """How the token numbers in this trace were arrived at.

        Not a SCHEMA field — extra keys are ignored by the loader — but
        written into the file all the same, because "840 tokens" measured and
        "840 tokens" estimated are different claims and the file is where a
        reader checks which one they are holding.
        """
        steps = self._steps if steps is None else steps
        if estimated_input is None:
            estimated_input = (self._input_tokens is None
                               and not self._measured_input_tokens)
        measured = sum(1 for step in steps if step["tokens_basis"] == "measured")
        estimated = len(steps) - measured
        if measured and not estimated:
            basis = "measured"
        elif measured:
            basis = "mixed"
        else:
            basis = "estimated"
        return {
            "basis": basis,
            "measured_steps": measured,
            "estimated_steps": estimated,
            "input_tokens_basis": "estimated" if estimated_input else "measured",
            "estimator": TOKEN_ESTIMATOR,
            "note": ("token counts without a measurement are estimated as "
                     f"{TOKEN_ESTIMATOR}; compare against runs recorded the same way"),
        }

    @property
    def path(self) -> Optional[Path]:
        """Where the trace was written, or None (not closed, or ``out_dir=None``)."""
        return self._path

    @property
    def filename(self) -> str:
        """``<task>__<agent>.json``, or ``<task>__<agent>__<run>.json``."""
        parts = [self.task, self.agent] + ([self.run_id] if self.run_id else [])
        return NAME_SEP.join(parts) + ".json"

    def _write(self, data: dict) -> Path:
        """Write atomically: a reader never sees a half-written trace."""
        directory = Path(self.out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self.filename
        temporary = path.with_name(path.name + f".tmp{os.getpid()}")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        temporary.replace(path)
        return path


def _text(value: Any) -> str:
    """Step text from whatever the agent handed over.

    Tool results are rarely strings.  Dicts and lists are serialised as JSON
    rather than ``repr``-ed, because the claim, provenance and diff analyses
    all read this text and JSON is the form they were written against.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return str(value)
    return str(value)


def _awaited(result: Any) -> Any:
    """Refuse to record an un-awaited result.

    ``str(coroutine)`` is ``<coroutine object f at 0x...>``: a trace that
    validates, reads plausibly and contains no observation at all.  Better to
    stop at the call site than to hand the analyses a plausible lie.
    """
    if inspect.isawaitable(result):
        # The recorder made this call, so the un-awaited object is its to
        # close; leaving it open only adds a RuntimeWarning to the real error.
        closer = getattr(result, "close", None)
        if callable(closer):
            closer()
        raise TypeError(
            "the tool returned an awaitable; await it and record the result "
            "with handle.observe(result), or decorate the async function with "
            "run.instrument() which awaits it for you")
    return result


def _bind(fn: Callable, args: tuple, kwargs: dict) -> dict:
    """Positional arguments back to their parameter names, where possible.

    A recorded call is only comparable if its arguments are named; falling
    back to ``arg0`` keeps the call parseable when the signature cannot be
    read (builtins, C functions).
    """
    try:
        bound = inspect.signature(fn).bind(*args, **kwargs)
        bound.apply_defaults()
        return {key: value for key, value in bound.arguments.items()
                if key != "self"}
    except (TypeError, ValueError):
        named = {f"arg{i}": value for i, value in enumerate(args)}
        named.update(kwargs)
        return named
