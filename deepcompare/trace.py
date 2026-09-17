"""Trace data model for DeepCompare AI.

Implements the dataclasses backing the Trajectory JSON contract defined in
SCHEMA.md (schema v1): a Trajectory is an agent run made of Steps, plus
agent / task / outcome / totals metadata.  All loading goes through
:meth:`Trajectory.from_json`, which validates the input and raises
``ValueError`` with a clear message on any schema violation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

SCHEMA_VERSION = 1

STEP_TYPES = ("plan", "search", "retrieve", "read", "tool_call", "reason", "answer")
QUALITY_VALUES = ("good", "weak", "bad")
#: whether a step's token count was reported by the provider or estimated
#: from text length.  Carried through load/save because an estimate that
#: loses its label becomes indistinguishable from a measurement.
TOKEN_BASES = ("measured", "estimated")
#: whether a step changed anything outside the agent (SCHEMA.md v22).
EFFECTS = ("read", "write")
#: ``budget`` settings that are not limits, and so are not numbers.  A loop
#: has switches and settings that name something as well as caps, and all of
#: them belong on the trace for the same reason: this is what a reader must
#: know to say two runs had the same harness.  The vocabulary is closed
#: because the point of the check is to catch ``{"max_steps": "twenty"}`` —
#: a budget that accepts any string for any key stops being a contract.
#: `deepcompare.harness.agent` reads these; `deepcompare.scaffold` proposes
#: them.
BUDGET_FLAGS = ("dedupe_tool_calls", "require_read_before_write")
#: what the *harness* did to a step, as opposed to what the agent did.
#: These are the loop's scaffold settings acting (`deepcompare.scaffold`),
#: and they are a field rather than a prose ``note`` because a reader that
#: has to match on English to find them cannot be trusted to have found
#: them all.  Closed, for the same reason `TERMINATIONS` is.
#:
#: ``cache_hit``    an identical read served from the harness cache
#:                  (``dedupe_tool_calls``); the call was not re-executed
#: ``answer_gate``  an answer held back until a required tool was called
#:                  (``require_before_answer``); pushed back once
#: ``write_gate``   a first write refused until something had been read
#:                  (``require_read_before_write``); the call did not run
SCAFFOLD_ACTIONS = ("cache_hit", "answer_gate", "write_gate")
BUDGET_NAMES = ("require_before_answer",)


def budget_value_ok(key, value) -> bool:
    """Whether ``budget[key] = value`` is a setting this schema allows."""
    if key in BUDGET_FLAGS:
        return isinstance(value, bool)
    if key in BUDGET_NAMES:
        return isinstance(value, str) and bool(value)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def budget_value_error(key) -> str:
    if key in BUDGET_FLAGS:
        return f"budget.{key} must be true or false"
    if key in BUDGET_NAMES:
        return f"budget.{key} must be a non-empty string naming a tool"
    return f"budget.{key} must be a number"

#: why the run stopped.  Reported as a distribution and never averaged
#: across, because a metric like "share of correct steps" rewards an agent
#: that quit early over one that kept working.  ``infrastructure_error``
#: exists so harness failures can be excluded from reliability statistics
#: instead of being counted as the agent's.
#: Taken from tau2-bench's TerminationReason enum rather than invented, so a
#: run logged for one tool is comparable in the other.
TERMINATIONS = (
    "agent_stop", "user_stop", "max_steps", "timeout",
    "context_window_exceeded", "too_many_errors", "agent_error",
    "infrastructure_error", "unexpected_error",
)
#: reasons that are the harness failing, not the agent.  Excluded from
#: reliability statistics: counting a rate-limited run as an agent failure
#: makes the agent look worse and the harness look fine.
HARNESS_TERMINATIONS = ("infrastructure_error", "unexpected_error")


def _require(obj: dict, key: str, where: str) -> Any:
    """Return ``obj[key]`` or raise a ValueError naming the missing field."""
    if not isinstance(obj, dict):
        raise ValueError(f"{where} must be a JSON object, got {type(obj).__name__}")
    if key not in obj:
        raise ValueError(f"missing required field '{key}' in {where}")
    return obj[key]


@dataclass
class AgentInfo:
    """Identity of the agent that produced a trajectory."""

    name: str
    model: str = ""
    version: str = ""
    #: the instructions the agent was given, when the trace records them
    #: (``agent.system_prompt`` as text, ``agent.config`` as an object).
    #: Kept off ``to_dict`` so a report side is byte-identical with or
    #: without them; the data section reads them (:mod:`deepcompare.data`).
    system_prompt: Optional[str] = field(default=None, compare=False)
    config: Optional[dict] = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentInfo":
        name = _require(d, "name", "agent")
        if not isinstance(name, str) or not name:
            raise ValueError("agent.name must be a non-empty string")
        prompt = d.get("system_prompt")
        config = d.get("config")
        return cls(name=name, model=str(d.get("model", "")), version=str(d.get("version", "")),
                   system_prompt=prompt if isinstance(prompt, str) else None,
                   config=dict(config) if isinstance(config, dict) else None)

    def to_dict(self) -> dict:
        return {"name": self.name, "model": self.model, "version": self.version}


@dataclass
class TaskInfo:
    """The task shared by the trajectories being compared."""

    id: str
    prompt: str
    expected: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "TaskInfo":
        tid = _require(d, "id", "task")
        prompt = _require(d, "prompt", "task")
        if not isinstance(tid, str) or not tid:
            raise ValueError("task.id must be a non-empty string")
        expected = d.get("expected")
        if expected is not None and not isinstance(expected, str):
            raise ValueError("task.expected must be a string or null")
        return cls(id=tid, prompt=str(prompt), expected=expected)

    def to_dict(self) -> dict:
        return {"id": self.id, "prompt": self.prompt, "expected": self.expected}


@dataclass
class Outcome:
    """Final result of a trajectory."""

    success: bool
    answer: str
    score: Optional[float] = None
    #: why the run stopped, one of TERMINATIONS (SCHEMA.md v22).  None means
    #: the log did not say; process analysis then reports "undeclared"
    #: rather than guessing a reason it cannot observe.
    termination: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Outcome":
        success = _require(d, "success", "outcome")
        answer = _require(d, "answer", "outcome")
        if not isinstance(success, bool):
            raise ValueError("outcome.success must be a boolean")
        score = d.get("score")
        if score is not None and not isinstance(score, (int, float)):
            raise ValueError("outcome.score must be a number or null")
        termination = d.get("termination")
        if termination is not None and termination not in TERMINATIONS:
            raise ValueError(
                f"outcome.termination must be one of {', '.join(TERMINATIONS)} or null"
            )
        return cls(success=success, answer=str(answer),
                   score=None if score is None else float(score),
                   termination=termination)

    def to_dict(self) -> dict:
        return {"success": self.success, "answer": self.answer, "score": self.score,
                "termination": self.termination}


@dataclass
class Totals:
    """Whole-run token / cost / latency totals."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "Totals":
        if not isinstance(d, dict):
            raise ValueError("totals must be a JSON object")
        for key in ("input_tokens", "output_tokens", "cost_usd", "latency_s"):
            val = d.get(key, 0)
            if not isinstance(val, (int, float)):
                raise ValueError(f"totals.{key} must be a number")
        return cls(
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            cost_usd=float(d.get("cost_usd", 0.0)),
            latency_s=float(d.get("latency_s", 0.0)),
        )

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "latency_s": self.latency_s,
        }


@dataclass
class Step:
    """A single step of an agent trajectory (see SCHEMA.md Step)."""

    index: int
    type: str
    name: str = ""
    input: str = ""
    output: str = ""
    tokens: int = 0
    latency_s: float = 0.0
    quality: Optional[str] = None
    note: Optional[str] = None
    #: optional model-internal telemetry for this step (SCHEMA.md v12):
    #: confidence, min_token_confidence, entropy, tokens_scored, temperature.
    model: Optional[dict] = None
    #: whether this step's observation was an error (SCHEMA.md v22).  None
    #: means the log did not say, and process analysis infers it from the
    #: output text — an inference it labels as such rather than asserting.
    error: Optional[bool] = None
    #: "measured" | "estimated" | None — where ``tokens`` came from.
    tokens_basis: Optional[str] = None
    #: "read" | "write": whether the step changed anything outside the agent.
    #: None means undeclared; the effect is then inferred from the tool name.
    #: Writes are where offline analysis has the most leverage, because they
    #: are the steps you cannot re-run to check.
    effect: Optional[str] = None
    #: optional delegation span (SCHEMA.md): ``{"id", "agent", "parent"?}`` —
    #: the (sub-)agent acting at this step and the span it was delegated
    #: from. None means the root agent acted. Spans nest through ``parent``.
    span: Optional[dict] = None
    #: optional RL signal (SCHEMA.md): the reward the environment paid for
    #: this step, the policy's value estimate at it, and its advantage.
    #: None means the log did not say; returns are then *shaped* from the
    #: report's labels (:mod:`deepcompare.rl`) and labelled as such.
    reward: Optional[float] = None
    value: Optional[float] = None
    advantage: Optional[float] = None
    #: what the harness did to this step, from :data:`SCAFFOLD_ACTIONS`;
    #: None means the harness did nothing and the step is the agent's
    #: alone.  It is deliberately separate from ``note``, which is prose:
    #: a page that had to match on English to find a held write would
    #: silently stop finding them the day the sentence was reworded.
    scaffold: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict, position: int) -> "Step":
        where = f"steps[{position}]"
        if not isinstance(d, dict):
            raise ValueError(f"{where} must be a JSON object")
        stype = _require(d, "type", where)
        if stype not in STEP_TYPES:
            raise ValueError(
                f"{where}: invalid step type {stype!r}; must be one of {', '.join(STEP_TYPES)}"
            )
        quality = d.get("quality")
        if quality is not None and quality not in QUALITY_VALUES:
            raise ValueError(
                f"{where}: invalid quality {quality!r}; must be one of "
                f"{', '.join(QUALITY_VALUES)} or null"
            )
        index = d.get("index", position)
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"{where}: index must be an integer")
        tokens = d.get("tokens", 0)
        latency = d.get("latency_s", 0.0)
        if not isinstance(tokens, (int, float)) or not isinstance(latency, (int, float)):
            raise ValueError(f"{where}: tokens and latency_s must be numbers")
        note = d.get("note")
        if note is not None and not isinstance(note, str):
            raise ValueError(f"{where}: note must be a string or null")
        model = d.get("model")
        if model is not None:
            if not isinstance(model, dict):
                raise ValueError(f"{where}: model must be an object or null")
            for key in ("confidence", "min_token_confidence"):
                value = model.get(key)
                if value is not None:
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        raise ValueError(f"{where}: model.{key} must be a number")
                    if not 0.0 <= float(value) <= 1.0:
                        raise ValueError(
                            f"{where}: model.{key} must be a probability in [0, 1]"
                        )
            for key in ("entropy", "temperature"):
                value = model.get(key)
                if value is not None and (
                    not isinstance(value, (int, float)) or isinstance(value, bool)
                    or float(value) < 0
                ):
                    raise ValueError(f"{where}: model.{key} must be a non-negative number")
        tokens_basis = d.get("tokens_basis")
        if tokens_basis is not None and tokens_basis not in TOKEN_BASES:
            raise ValueError(
                f"{where}: invalid tokens_basis {tokens_basis!r}; must be one of "
                f"{', '.join(TOKEN_BASES)} or null"
            )
        error = d.get("error")
        if error is not None and not isinstance(error, bool):
            raise ValueError(f"{where}: error must be true, false or null")
        effect = d.get("effect")
        if effect is not None and effect not in EFFECTS:
            raise ValueError(
                f"{where}: invalid effect {effect!r}; must be one of "
                f"{', '.join(EFFECTS)} or null"
            )
        span = d.get("span")
        if span is not None:
            if not isinstance(span, dict) or not span.get("id") or not span.get("agent"):
                raise ValueError(f"{where}: span must be an object with id and agent (and an optional parent)")
            span = {"id": str(span["id"]), "agent": str(span["agent"]),
                    "parent": (str(span["parent"]) if span.get("parent") is not None else None)}
        scaffold = d.get("scaffold")
        if scaffold is not None and scaffold not in SCAFFOLD_ACTIONS:
            raise ValueError(
                f"{where}: invalid scaffold {scaffold!r}; must be one of "
                f"{', '.join(SCAFFOLD_ACTIONS)} or null"
            )
        signal: dict = {}
        for key in ("reward", "value", "advantage"):
            val = d.get(key)
            if val is not None and (not isinstance(val, (int, float)) or isinstance(val, bool)):
                raise ValueError(f"{where}: {key} must be a number or null")
            signal[key] = None if val is None else float(val)
        return cls(
            index=index,
            type=stype,
            name=str(d.get("name", "")),
            input=str(d.get("input", "")),
            output=str(d.get("output", "")),
            tokens=int(tokens),
            latency_s=float(latency),
            quality=quality,
            note=note,
            model=model,
            tokens_basis=tokens_basis,
            error=error,
            effect=effect,
            span=span,
            reward=signal["reward"],
            value=signal["value"],
            advantage=signal["advantage"],
            scaffold=scaffold,
        )

    def to_dict(self) -> dict:
        out = {
            "index": self.index,
            "type": self.type,
            "name": self.name,
            "input": self.input,
            "output": self.output,
            "tokens": self.tokens,
            "latency_s": self.latency_s,
            "quality": self.quality,
            "note": self.note,
            "model": self.model,
            "tokens_basis": self.tokens_basis,
            "error": self.error,
            "effect": self.effect,
            "span": self.span,
            "reward": self.reward,
            "value": self.value,
            "advantage": self.advantage,
        }
        # written only when the harness actually did something. The other
        # optional fields have been written as null since the schema began
        # and stay that way; adding a fifth null to every step of every
        # stored trace would have changed the bytes of every artifact in the
        # repository to say nothing, and "absent" and "null" mean the same
        # thing here — the harness did not act on this step.
        if self.scaffold is not None:
            out["scaffold"] = self.scaffold
        return out


@dataclass
class Trajectory:
    """A full agent run: metadata plus an ordered list of Steps."""

    trace_id: str
    agent: AgentInfo
    task: TaskInfo
    outcome: Outcome
    totals: Totals
    steps: list[Step] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    run_id: str = "r1"
    #: tools the agent was offered, as [{"name", "effect"?, "parameters"?}]
    #: (SCHEMA.md v22).  Without it a call cannot be checked against what was
    #: actually available, so grounding is reported as unmeasurable rather
    #: than assumed perfect.
    tools: list[dict] = field(default_factory=list)
    #: how this run's token counts were obtained, when the recorder said so:
    #: {"basis", "measured_steps", "estimated_steps", "estimator", ...}.
    token_accounting: dict = field(default_factory=dict)
    #: limits the harness enforced, e.g. {"max_steps": 20}.  Needed to tell
    #: an agent that finished from one that ran out of room.
    budget: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, path_or_dict: Union[str, Path, dict]) -> "Trajectory":
        """Load and validate a Trajectory from a JSON file path or a dict.

        Raises ``ValueError`` on any schema violation (missing fields, invalid
        step types, last step not of type ``answer``, ...).
        """
        if isinstance(path_or_dict, dict):
            data = path_or_dict
            origin = "<dict>"
        else:
            path = Path(path_or_dict)
            origin = str(path)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{origin}: not valid JSON: {exc}") from exc
        try:
            return cls.from_dict(data)
        except ValueError as exc:
            if origin != "<dict>":
                raise ValueError(f"{origin}: {exc}") from exc
            raise

    @classmethod
    def from_dict(cls, data: dict) -> "Trajectory":
        """Build a validated Trajectory from an already-parsed dict."""
        if not isinstance(data, dict):
            raise ValueError("trajectory must be a JSON object")
        agent = AgentInfo.from_dict(_require(data, "agent", "trajectory"))
        task = TaskInfo.from_dict(_require(data, "task", "trajectory"))
        outcome = Outcome.from_dict(_require(data, "outcome", "trajectory"))
        totals = Totals.from_dict(_require(data, "totals", "trajectory"))
        raw_steps = _require(data, "steps", "trajectory")
        if not isinstance(raw_steps, list):
            raise ValueError("trajectory.steps must be a list")
        if not raw_steps:
            raise ValueError("trajectory.steps must contain at least one step")
        steps = [Step.from_dict(s, i) for i, s in enumerate(raw_steps)]
        if steps[-1].type != "answer":
            raise ValueError(
                f"last step must have type 'answer', got {steps[-1].type!r} "
                f"at steps[{len(steps) - 1}]"
            )
        for i, s in enumerate(steps[:-1]):
            if s.type == "answer":
                raise ValueError(
                    f"steps[{i}]: 'answer' step may only appear as the last step"
                )
        trace_id = str(data.get("trace_id", ""))
        version = data.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("schema_version must be an integer")
        run_id = data.get("run_id", "r1")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be a non-empty string")
        tools = data.get("tools", [])
        if not isinstance(tools, list):
            raise ValueError("trajectory.tools must be a list")
        for i, tool in enumerate(tools):
            if not isinstance(tool, dict) or not str(tool.get("name", "")):
                raise ValueError(f"tools[{i}] must be an object with a name")
            effect = tool.get("effect")
            if effect is not None and effect not in EFFECTS:
                raise ValueError(
                    f"tools[{i}]: invalid effect {effect!r}; must be one of "
                    f"{', '.join(EFFECTS)} or null"
                )
        accounting = data.get("token_accounting", {})
        if not isinstance(accounting, dict):
            raise ValueError("trajectory.token_accounting must be an object")
        budget = data.get("budget", {})
        if not isinstance(budget, dict):
            raise ValueError("trajectory.budget must be an object")
        for key, value in budget.items():
            if not budget_value_ok(key, value):
                raise ValueError(budget_value_error(key))
        return cls(
            trace_id=trace_id,
            agent=agent,
            task=task,
            outcome=outcome,
            totals=totals,
            steps=steps,
            schema_version=version,
            run_id=run_id,
            tools=tools,
            budget=budget,
            token_accounting=accounting,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "agent": self.agent.to_dict(),
            "task": self.task.to_dict(),
            "outcome": self.outcome.to_dict(),
            "totals": self.totals.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "tools": self.tools,
            "budget": self.budget,
            "token_accounting": self.token_accounting,
        }
