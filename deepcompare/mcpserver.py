"""The MCP server: the three levels of a bundle as tools and its files as
resources, over JSON-RPC 2.0 on stdio.

A coding assistant given a key pastes it, adds this server to its
``mcpServers`` and pulls every level itself: ``overview`` for what ran,
``runs`` for one row per run with filters and a sort, ``run`` for the
whole third level of one run — its steps, budget, fetches and timeline —
and ``fetches``, ``budget``, ``lineage``, ``key`` and ``verify`` for the
readings by name. Every number a tool returns is the bundle's, which is
the members' — a count or a sum over recorded steps, an interval where
the engine drew one, ``null`` with a reason where nothing was recorded;
the server computes nothing new and never re-estimates.

The protocol is the Model Context Protocol's JSON-RPC shape: one message
per line on stdin, one response per line on stdout, notifications
answered with silence, errors as JSON-RPC errors with a reason. Stdlib
``json`` and ``sys`` only: no socket, no thread, so the module lives in
the engine and the network boundary stays where it is.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, TextIO

from . import __version__
from .bundle import SORT_FIELDS, Bundle, decode_key

#: the protocol revisions this server knows, newest first; a request naming
#: one of them is answered in kind, any other with the newest
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "agentdiff"
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, SERVER_ERROR = -32700, -32600, -32601, -32602, -32000

INSTRUCTIONS = ("AgentDiff bundle: call overview for what ran (level 1), runs for one row per run (level 2), "
                "run with a key for the steps, budget, fetches and timeline of one run (level 3). Every number "
                "is a count or a sum over recorded steps; null means unrecorded, never zero.")

_NUMBERS = ("Every number is a count or a sum over the steps the traces recorded, carried through from the "
            "bundle's members; a token count labelled estimated in the trace stays labelled; null means the trace "
            "did not record the quantity, never that it was zero; success rates carry a Wilson interval over the "
            "runs recorded, not a population claim; SYNTHETIC data is flagged per run.")

TOOLS = (
    {"name": "overview",
     "description": "Level 1: what agents ran — per agent the runs, tasks, success rate with its interval, tokens, "
                    "cost, seconds and fetches summed over the runs that recorded them (and how many did), whether it "
                    "is self-evolving and its lineage; the lineages with their eval loops; the totals; the sections "
                    f"present; a reading. {_NUMBERS}",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "runs",
     "description": "Level 2: one row per run across the bundle's members, keyed <member>/<task>/<agent>/<run>, with "
                    "success, steps, tool calls by tool, tokens and the measured share, cost, seconds, fetches, errors, "
                    "repeats, lineage generation and the SYNTHETIC flag; filter by agent, task, member or success, sort "
                    f"descending by tokens, cost, seconds, fetches, steps or errors, and cap the count. {_NUMBERS}",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
         "agent": {"type": "string"}, "task": {"type": "string"}, "member": {"type": "string"},
         "success": {"type": "boolean"}, "sort": {"type": "string", "enum": sorted(SORT_FIELDS)},
         "limit": {"type": "integer", "minimum": 0}}}},
    {"name": "run",
     "description": "Level 3: one run in full — its row, every step (kind, name, tokens and their basis, latency, "
                    "error, effect, reward, value, input and output sizes, sub-agent), where the tokens went (by kind, "
                    "by tool, the cumulative burn, the heaviest steps, the waste after the last evidence, in errored "
                    "calls and in repeats), every fetch and what came back with the search map, and the timeline in "
                    "the Evolution timescape's shape. A run whose steps the output did not keep says so and is not "
                    f"filled in. {_NUMBERS}",
     "inputSchema": {"type": "object", "required": ["key"], "additionalProperties": False,
                     "properties": {"key": {"type": "string", "description": "<member>/<task>/<agent>/<run>"}}}},
    {"name": "fetches",
     "description": "With a key, the fetch records of one run: each search, retrieve, read and tool call with its "
                    "query, output size, tokens, latency, error, the earlier fetch it repeats, and whether its result "
                    "was used — a recorded reward or quality label, else null. Without a key, the per-agent fetch "
                    f"summary of every member, narrowed to one agent when named. {_NUMBERS}",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"key": {"type": "string"}, "agent": {"type": "string"}}}},
    {"name": "budget",
     "description": "Without a key, the budget aggregate of every member — per agent the runs, total and mean "
                    "tokens, the split by kind and by tool, the measured share and the cost when recorded, per task the "
                    "mean per agent, the heaviest runs, the cap when one was given — narrowed to one agent and/or task "
                    f"when named. With a key, one run's budget: its split, cumulative burn, heaviest steps and waste. {_NUMBERS}",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"agent": {"type": "string"}, "task": {"type": "string"}, "key": {"type": "string"}}}},
    {"name": "lineage",
     "description": "The evolution and coevolution summaries of the bundle's lineages: generations, the recommended and "
                    "best generation, the verdict counts, the eval's generations, adopted metrics, loop closures and "
                    "drift, the integrity reading; one family when named. A verdict is the engine's reading of a "
                    f"step's effect on return and outcome, not a judgement of the agent. {_NUMBERS}",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"family": {"type": "string"}}}},
    {"name": "key",
     "description": "The bundle's key — the level-1 overview compressed into one paste-safe line that names the "
                    "bundle by its content hash — and the overview it decodes to. Agents past the first twelve are "
                    "counted in the key, not named.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "verify",
     "description": "Recompute the bundle's id from the members' copies and say whether it matches the id the "
                    "bundle claims; a mismatch means a member file changed after the bundle was written.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
)


class RpcError(Exception):
    """A JSON-RPC error: the code and the reason."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _check_args(tool: dict, args: Any) -> dict:
    """The arguments against the tool's schema: an object, only the named
    properties, the right primitive types, the required ones present."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, f"{tool['name']}: arguments must be an object")
    schema = tool["inputSchema"]
    props = schema.get("properties") or {}
    for name in schema.get("required") or ():
        if name not in args:
            raise RpcError(INVALID_PARAMS, f"{tool['name']}: {name} is required")
    for name, value in args.items():
        if name not in props:
            raise RpcError(INVALID_PARAMS, f"{tool['name']}: unknown argument {name!r}; one of {', '.join(sorted(props)) or 'none'}")
        want = props[name].get("type")
        ok = {"string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool)}[want](value)
        if not ok:
            raise RpcError(INVALID_PARAMS, f"{tool['name']}: {name} must be a {want}")
        if "enum" in props[name] and value not in props[name]["enum"]:
            raise RpcError(INVALID_PARAMS, f"{tool['name']}: {name} must be one of {', '.join(props[name]['enum'])}")
        if want == "integer" and "minimum" in props[name] and value < props[name]["minimum"]:
            raise RpcError(INVALID_PARAMS, f"{tool['name']}: {name} must be at least {props[name]['minimum']}")
    return args


class Server:
    """One bundle behind the protocol. :meth:`handle` answers one parsed
    message (``None`` for a notification); :meth:`serve` reads lines from
    a stream until EOF and writes one response line per request."""

    def __init__(self, bundle: Bundle) -> None:
        self.bundle = bundle
        self.initialized = False

    # ------------------------------------------------------------ tools

    def call(self, name: str, args: Optional[dict] = None) -> Any:
        """The result of one tool; :class:`RpcError` for an unknown tool,
        bad arguments or a key that names no run."""
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"unknown tool {name!r}; one of {', '.join(t['name'] for t in TOOLS)}")
        args = _check_args(tool, args)
        b = self.bundle
        if name == "overview":
            return b.overview
        if name == "runs":
            rows = b.runs(**args)
            return {"runs": rows, "n": len(rows), "of": len(b.rows), "filters": args}
        if name == "run":
            return self._run(args["key"])
        if name == "fetches":
            if "key" in args:
                return self._run(args["key"])["fetches"]
            return b.fetches_summary(args.get("agent"))
        if name == "budget":
            if "key" in args:
                return self._run(args["key"])["budget"]
            return b.budget(args.get("agent"), args.get("task"))
        if name == "lineage":
            return b.lineage(args.get("family"))
        if name == "key":
            return {"key": b.key, "overview": decode_key(b.key)}
        if name == "verify":
            return b.verify()
        raise RpcError(INVALID_PARAMS, f"unknown tool {name!r}")  # pragma: no cover — the table above is closed

    def _run(self, key: str) -> dict:
        record = self.bundle.run(key)
        if record is None:
            raise RpcError(SERVER_ERROR, f"no run {key!r} in the bundle; keys are <member>/<task>/<agent>/<run>, listed by runs")
        return record

    # --------------------------------------------------------- protocol

    def handle(self, message: Any) -> Optional[dict]:
        """The response to one message, or ``None`` for a notification."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "a request is a JSON object")
        rid = message.get("id")
        method = message.get("method")
        is_notification = "id" not in message
        if not isinstance(method, str):
            return None if is_notification else _error(rid, INVALID_REQUEST, "a request names a method")
        try:
            result = self._dispatch(method, message.get("params") or {}, is_notification)
        except RpcError as exc:
            return None if is_notification else _error(rid, exc.code, exc.message, exc.data)
        except (KeyError, ValueError, OSError) as exc:
            return None if is_notification else _error(rid, SERVER_ERROR, f"{type(exc).__name__}: {exc}")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _dispatch(self, method: str, params: Any, is_notification: bool) -> Any:
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            asked = params.get("protocolVersion")
            self.initialized = True
            return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [dict(t) for t in TOOLS]}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise RpcError(INVALID_PARAMS, "tools/call names a tool")
            result = self.call(name, params.get("arguments"))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "structuredContent": result, "isError": False}
        if method == "resources/list":
            return {"resources": self.bundle.resources()}
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str):
                raise RpcError(INVALID_PARAMS, "resources/read names a uri")
            try:
                text = self.bundle.read_resource(uri)
            except KeyError:
                raise RpcError(SERVER_ERROR, f"no resource {uri!r}; resources/list names them") from None
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
        raise RpcError(METHOD_NOT_FOUND, f"unknown method {method!r}")

    def handle_line(self, line: str) -> list:
        """The response lines for one input line: none for a blank line
        or a notification, one per request in a batch."""
        line = line.strip()
        if not line:
            return []
        try:
            message = json.loads(line)
        except ValueError as exc:
            return [json.dumps(_error(None, PARSE_ERROR, f"not JSON: {exc}"), ensure_ascii=False)]
        messages = message if isinstance(message, list) else [message]
        out = []
        for m in messages:
            response = self.handle(m)
            if response is not None:
                out.append(json.dumps(response, ensure_ascii=False))
        return out

    def serve(self, stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
        """Read stdin until EOF, one message per line; write one response
        per request, flushed, so a client that waits on a line gets it."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            for response in self.handle_line(line):
                stdout.write(response + "\n")
                stdout.flush()
        return 0


def _error(rid: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


__all__ = ["PROTOCOL_VERSIONS", "SERVER_NAME", "TOOLS", "RpcError", "Server"]
