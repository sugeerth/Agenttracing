"""A trajectory as OpenTelemetry spans, in OTLP/JSON, after the GenAI semantic conventions.

The run is one ``invoke_agent`` span. Each step beneath it is an
``execute_tool`` span (a tool call), ``retrieve`` or ``search`` (fetching
context), or ``chat`` (the model reasoning, planning or answering). The
spans carry what the conventions name: ``gen_ai.operation.name``,
``gen_ai.agent.name``, ``gen_ai.tool.name``, ``gen_ai.request.model`` and
token usage. A failed step's span has an error status.

What the conventions have no name for rides along under ``agentdiff.*``:
the step's own type, name and index, the task id, whether the run passed,
and which span is the run itself. That is what lets
:func:`agentdiff.adapters.from_otel_genai` read an exported trace back
step for step, while any other OpenTelemetry backend just shows the spans.

Content is opt-in, as the conventions make it: without ``content`` the
spans carry each step's sizes, never its text (prompts, tool arguments,
tool results, answers). Nothing here sends anything anywhere; the result
is a JSON document to write to a file or hand to a collector.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

__all__ = ["to_otlp", "OPERATIONS"]

#: a step's SCHEMA type -> the GenAI operation its span is
OPERATIONS = {"tool_call": "execute_tool", "retrieve": "retrieve", "search": "search",
              "reason": "chat", "plan": "chat", "answer": "chat"}
_ERROR = 2      # OTLP STATUS_CODE_ERROR
_INTERNAL = 1   # OTLP SPAN_KIND_INTERNAL


def _value(v: Any) -> dict:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}


def _attrs(d: Dict[str, Any]) -> List[dict]:
    return [{"key": k, "value": _value(v)} for k, v in d.items() if v is not None]


def _hex(seed: str, n: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:n]


def _text(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if v is not None else ""


def to_otlp(traj: dict, *, content: bool = False, start_s: Optional[float] = None,
            service: str = "agentdiff") -> dict:
    """``traj`` (a SCHEMA trajectory dict) as one OTLP/JSON ``resourceSpans`` document. Times start at the
    run's ``started_at``, else at ``start_s``, else at 0, and each step lasts its recorded ``latency_s``.
    The ids are derived from the trace id, so the same trace always exports to the same spans."""
    from . import __version__
    agent = traj.get("agent") or {}
    task = traj.get("task") or {}
    outcome = traj.get("outcome") or {}
    totals = traj.get("totals") or {}
    seed = str(traj.get("trace_id") or json.dumps(task, sort_keys=True) + str(agent.get("name")))
    trace_id = _hex("trace:" + seed, 32)
    root_id = _hex(f"span:{seed}:root", 16)
    t0 = int(round(float(traj.get("started_at") or start_s or 0) * 1e9))
    clock = t0
    spans: List[dict] = []
    for i, step in enumerate(traj.get("steps") or []):
        kind = str(step.get("type") or "tool_call")
        op = OPERATIONS.get(kind, "execute_tool")
        name = str(step.get("name") or kind)
        dur = int(round(float(step.get("latency_s") or 0) * 1e9))
        tokens = int(step.get("tokens") or 0)
        a: Dict[str, Any] = {"gen_ai.operation.name": op, "agentdiff.step.index": i, "agentdiff.step.type": kind,
                             "agentdiff.step.name": name, "agentdiff.step.tokens": tokens}
        if op == "chat":
            a["gen_ai.request.model"] = agent.get("model") or None
            a["gen_ai.usage.output_tokens"] = tokens
            if content:
                a["gen_ai.prompt"], a["gen_ai.completion"] = _text(step.get("input")), _text(step.get("output"))
        else:
            a["gen_ai.tool.name"] = name
            if content:
                a["gen_ai.tool.call.arguments"] = _text(step.get("input"))
                a["gen_ai.tool.call.result"] = _text(step.get("output"))
        if not content:
            a["agentdiff.input.chars"] = len(_text(step.get("input")))
            a["agentdiff.output.chars"] = len(_text(step.get("output")))
        span = {"traceId": trace_id, "spanId": _hex(f"span:{seed}:{i}", 16), "parentSpanId": root_id,
                "name": f"{op} {name}", "kind": _INTERNAL, "startTimeUnixNano": str(clock),
                "endTimeUnixNano": str(clock + dur), "attributes": _attrs(a)}
        if step.get("error"):
            span["status"] = {"code": _ERROR, "message": "the step failed"}
        spans.append(span)
        clock += dur
    root_attrs: Dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": agent.get("name") or "agent",
        "gen_ai.request.model": agent.get("model") or None,
        "gen_ai.usage.input_tokens": int(totals.get("input_tokens") or 0),
        "gen_ai.usage.output_tokens": int(totals.get("output_tokens") or 0),
        "agentdiff.root": True, "agentdiff.trace_id": traj.get("trace_id"), "agentdiff.task.id": task.get("id"),
        "agentdiff.outcome.success": outcome.get("success") if isinstance(outcome.get("success"), bool) else None,
        "agentdiff.agent.version": agent.get("version") or None}
    if content:
        root_attrs["agentdiff.task.prompt"] = _text(task.get("prompt"))
        root_attrs["agentdiff.task.expected"] = _text(task.get("expected")) if task.get("expected") is not None else None
        root_attrs["agentdiff.outcome.answer"] = _text(outcome.get("answer"))
    root = {"traceId": trace_id, "spanId": root_id, "name": f"invoke_agent {agent.get('name') or 'agent'}",
            "kind": _INTERNAL, "startTimeUnixNano": str(t0), "endTimeUnixNano": str(clock),
            "attributes": _attrs(root_attrs)}
    if outcome.get("success") is False:
        root["status"] = {"code": _ERROR, "message": "the run failed"}
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": service})},
        "scopeSpans": [{"scope": {"name": "agentdiff", "version": __version__}, "spans": [root] + spans}]}]}
