"""The cassette: a recorded run's tool results, served back on replay.

Production agent systems settle on one discipline for reproducing a run
(Temporal's durable execution, VCR-style HTTP cassettes, Docker cagent's
session recording, langchain-replay): record every non-deterministic
input the agent received, then replay with those inputs served from the
recording, so the only thing that can vary is the thing under test.  For
an agent the non-deterministic inputs are two — the model's turns and
the world's answers to its tool calls.  A cassette holds the second.

A cassette is built from a SCHEMA trace: every tool-ish step (``search``,
``retrieve``, ``read``, ``tool_call``) becomes an entry keyed by the call
as the agent made it — the tool's name and its arguments, canonicalised —
with the outputs in the order they were observed, so a call repeated
with the same arguments replays each result in turn.  Serving is
hermetic: no tool code runs, no network is touched.

A call the recording never made is a **miss**.  A miss is the signal,
not an error to hide: on a self-replay it means the recording cannot be
reproduced from itself; with a different model it marks exactly where
the new model departed from the recorded trajectory.  The policy says
what the agent is told: ``strict`` (an error result naming the miss),
``empty`` (an empty result) or ``live`` (the declared tool runs for
real — the one policy that leaves the hermetic boundary, chosen
explicitly).  Every miss is kept on the cassette for the report.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from .agent import Tool

#: step types whose output is an observation from the world, not the model
TOOLISH = ("tool_call", "search", "retrieve", "read")
POLICIES = ("strict", "empty", "live")
#: between a tool name and its arguments in a key; never part of a name
SEP = "\x1f"


def parse_call(rendered: str) -> tuple[str, Any]:
    """``name(k='v', n=3)`` → ``("name", {"k": "v", "n": "3"})``; anything
    not in that dialect is returned verbatim as ``("", text)`` — a search
    query recorded as its own text is an argument, not a malformed call."""
    match = re.match(r"^\s*([\w.\-]+)\((.*)\)\s*$", rendered or "", re.S)
    if not match:
        return "", (rendered or "")
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


def canonical_args(args: Any) -> str:
    """One string for one set of arguments, however they were carried:
    a dict (sorted keys, values as strings), a bare string, or nothing."""
    if args is None:
        return ""
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, dict):
        if set(args) == {"_raw"}:
            return str(args["_raw"]).strip()
        return json.dumps({str(k): ("" if v is None else str(v)) for k, v in args.items()},
                          sort_keys=True, ensure_ascii=False)
    return str(args).strip()


def call_key(name: str, args: Any) -> str:
    return f"{name}{SEP}{canonical_args(args)}"


def key_of_step(step: dict) -> str:
    """The cassette key of a recorded tool-ish step: the recorded
    ``name`` and its ``input`` — parsed when it is a rendered call, taken
    verbatim when it is the argument itself."""
    name = str(step.get("name") or "")
    parsed_name, args = parse_call(str(step.get("input") or ""))
    if parsed_name and (parsed_name == name or not name):
        return call_key(parsed_name, args)
    return call_key(name, str(step.get("input") or ""))


class Cassette:
    """The recorded tool results of one run, replayable in order."""

    def __init__(self, entries: Optional[dict] = None, *, source: str = "") -> None:
        # key → list of {"output", "error", "step"} in observation order
        self.entries: dict[str, list] = {k: list(v) for k, v in (entries or {}).items()}
        self._cursor: dict[str, int] = {}
        self.source = source
        self.hits: list = []
        self.misses: list = []

    # ---------------------------------------------------------------- build
    @classmethod
    def from_trace(cls, trace: dict) -> "Cassette":
        entries: dict[str, list] = {}
        for step in trace.get("steps") or []:
            if step.get("type") not in TOOLISH:
                continue
            key = key_of_step(step)
            entries.setdefault(key, []).append({
                "output": step.get("output") if step.get("output") is not None else "",
                "error": bool(step.get("error")),
                "step": step.get("index"),
            })
        return cls(entries, source=str(trace.get("trace_id") or ""))

    def to_dict(self) -> dict:
        return {"source": self.source, "entries": [
            {"name": k.split(SEP, 1)[0], "args": k.split(SEP, 1)[1], "results": v}
            for k, v in self.entries.items()]}

    @classmethod
    def from_dict(cls, data: dict) -> "Cassette":
        entries = {call_key(e["name"], e["args"]): e["results"] for e in data.get("entries") or []}
        return cls(entries, source=str(data.get("source") or ""))

    @property
    def names(self) -> list:
        seen: list = []
        for k in self.entries:
            name = k.split(SEP, 1)[0]
            if name and name not in seen:
                seen.append(name)
        return seen

    @property
    def recorded_calls(self) -> int:
        return sum(len(v) for v in self.entries.values())

    # ---------------------------------------------------------------- serve
    def lookup(self, name: str, args: Any) -> Optional[dict]:
        """The next recorded result for this call, or ``None`` on a miss.
        A call made more often than it was recorded replays its last
        result again and is counted as a hit with ``reused: True``."""
        key = call_key(name, args)
        results = self.entries.get(key)
        if not results:
            self.misses.append({"name": name, "args": canonical_args(args), "call": len(self.hits) + len(self.misses)})
            return None
        i = self._cursor.get(key, 0)
        reused = i >= len(results)
        result = results[min(i, len(results) - 1)]
        self._cursor[key] = i + 1
        self.hits.append({"name": name, "args": canonical_args(args), "step": result.get("step"), "reused": reused})
        return result

    def rewind(self) -> None:
        self._cursor = {}
        self.hits = []
        self.misses = []

    def tools(self, declared: Optional[list] = None, policy: str = "strict") -> list:
        """Tools that answer from this cassette.  ``declared`` supplies
        schemas, effects, and — under the ``live`` policy — the real
        function to fall back to on a miss.  Every tool name the recording
        used gets a tool, whether or not it was declared, so the replayed
        agent can make every call the recorded one made."""
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {', '.join(POLICIES)}, not {policy!r}")
        by_name = {t.name: t for t in (declared or [])}
        names = list(self.names)
        for name in by_name:
            if name not in names:
                names.append(name)
        return [self._tool(name, by_name.get(name), policy) for name in names]

    def _tool(self, name: str, declared: Optional[Tool], policy: str) -> Tool:
        cassette = self

        def serve(*positional: Any, **kwargs: Any) -> Any:
            args: Any = positional[0] if positional and not kwargs else kwargs
            hit = cassette.lookup(name, args)
            if hit is not None:
                if hit.get("error"):
                    raise CassetteRecordedError(str(hit.get("output") or "error"))
                return hit.get("output")
            if policy == "live" and declared is not None:
                return declared.fn(*positional, **kwargs)
            if policy == "empty":
                return ""
            raise CassetteMiss(f"cassette miss: {name}({canonical_args(args)}) was never recorded")

        return Tool(name, serve,
                    declared.description if declared else f"{name} (served from the recording)",
                    declared.parameters if declared else {"type": "object", "properties": {}, "additionalProperties": True},
                    effect=declared.effect if declared else None)

    def summary(self) -> dict:
        return {"source": self.source, "recorded_calls": self.recorded_calls, "tools": self.names,
                "hits": len(self.hits), "reused": sum(1 for h in self.hits if h.get("reused")),
                "misses": list(self.misses)}


class CassetteMiss(LookupError):
    """A call the recording never made, under the strict policy."""


class CassetteRecordedError(RuntimeError):
    """The recording says this call errored; the replay errors the same way."""


def same_words_grader(trace: dict) -> Callable:
    """The grader of a self-replay: the recording's verdict is part of the
    recording.  The same answer text keeps the recorded verdict; any
    other text is graded as the opposite, so a drift in the answer shows
    up in the outcome as well as in the step diff."""
    outcome = trace.get("outcome") or {}
    recorded_answer = " ".join(str(outcome.get("answer") or "").split())
    recorded_success = bool(outcome.get("success"))

    def same_words(answer: str, _task: dict) -> bool:
        return recorded_success if " ".join(str(answer).split()) == recorded_answer else (not recorded_success)

    return same_words
