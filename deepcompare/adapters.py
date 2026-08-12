"""Trace adapters for DeepCompare AI (SCHEMA.md v9).

Converts foreign trace formats into SCHEMA trajectories:

- :func:`from_otel_genai` — OpenTelemetry GenAI-convention spans (flat
  ``attributes`` dicts with ``gen_ai.*`` keys; span-name fallbacks;
  start/end nanoseconds -> latency).
- :func:`from_openai_messages` — a chat-completions style message array
  with tool calls (system + first user -> task prompt; assistant tool_calls
  -> tool-ish steps typed by name cues; tool-role results -> step outputs;
  plain assistant -> reason; final assistant -> answer).

Both return ``(trajectory_dict, warnings)`` where the dict validates via
``Trajectory.from_json`` and warnings list items that could not be mapped.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from .trace import Trajectory

#: tool-name cue -> step type, checked in order.
_TOOL_NAME_CUES: list[tuple[tuple[str, ...], str]] = [
    (("retriev",), "retrieve"),
    (("search", "browse"), "search"),
    (("fetch", "get", "read", "open"), "read"),
]

_CHAT_OPS = ("chat", "text_completion", "generate_content", "generate")

#: attribute keys carrying a tool call's arguments, most-specific first.
#: Real exporters disagree: the GenAI semantic conventions use
#: ``gen_ai.tool.call.arguments``, others emit ``gen_ai.tool.input``,
#: ``tool.arguments`` or a bare ``input``.
_TOOL_INPUT_KEYS = (
    "gen_ai.tool.call.arguments", "gen_ai.tool.arguments", "gen_ai.tool.input",
    "tool.arguments", "tool.input", "input",
)

#: attribute keys carrying a tool call's result, most-specific first.
_TOOL_OUTPUT_KEYS = (
    "gen_ai.tool.call.result", "gen_ai.tool.result", "gen_ai.tool.output",
    "tool.result", "tool.output", "output",
)

#: attribute keys carrying model prompt / completion text.
_PROMPT_KEYS = ("gen_ai.prompt", "gen_ai.input.messages", "gen_ai.request.prompt")
_COMPLETION_KEYS = (
    "gen_ai.completion", "gen_ai.output.messages", "gen_ai.response.text",
)


def _first_attr(attrs: dict, keys: tuple[str, ...]) -> str:
    """First non-empty attribute value among ``keys``, stringified.

    Lists (OTLP array values, message arrays) are flattened to text so the
    step still carries its content rather than a repr.
    """
    for key in keys:
        if key not in attrs:
            continue
        value = attrs[key]
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            parts = [str(_otlp_value(v)) for v in value if v is not None]
            text = " ".join(p for p in parts if p)
        elif isinstance(value, dict):
            text = str(_otlp_value(value))
        else:
            text = str(value)
        text = text.strip()
        if text:
            return text
    return ""


def _type_from_tool_name(name: str) -> str:
    lowered = name.lower()
    for cues, step_type in _TOOL_NAME_CUES:
        if any(cue in lowered for cue in cues):
            return step_type
    return "tool_call"


def _agent_dict(agent: Union[str, dict]) -> dict:
    if isinstance(agent, str):
        return {"name": agent, "model": "", "version": ""}
    return {
        "name": agent.get("name", "adapted-agent"),
        "model": agent.get("model", ""),
        "version": agent.get("version", ""),
    }


def _task_dict(task: Union[str, dict], prompt_fallback: str = "") -> dict:
    if isinstance(task, str):
        return {"id": task, "prompt": prompt_fallback, "expected": None}
    return {
        "id": task.get("id", "task"),
        "prompt": task.get("prompt", prompt_fallback),
        "expected": task.get("expected"),
    }


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _otlp_value(value: Any) -> Any:
    """Unwrap an OTLP AnyValue wrapper ({"stringValue": "x"}) to a plain value."""
    if isinstance(value, dict):
        for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if key in value:
                return value[key]
        if "arrayValue" in value:
            values = (value["arrayValue"] or {}).get("values", []) or []
            return [_otlp_value(v) for v in values]
    return value


def _span_attrs(span: dict) -> dict:
    """Attributes as a flat dict, accepting both the plain-mapping form and
    the OTLP wire form (a list of ``{"key": ..., "value": {...}}`` entries)."""
    attrs = span.get("attributes")
    if isinstance(attrs, dict):
        return attrs
    if isinstance(attrs, list):
        flat: dict[str, Any] = {}
        for item in attrs:
            if isinstance(item, dict) and "key" in item:
                flat[str(item["key"])] = _otlp_value(item.get("value"))
        return flat
    return {}


def _span_time(span: dict, which: str) -> int:
    """Span timestamp in nanoseconds, accepting snake_case or camelCase keys
    (OTLP JSON exports use camelCase; values may arrive as strings)."""
    keys = (f"{which}_time_unix_nano", f"{which}TimeUnixNano")
    for key in keys:
        if key in span and span[key] is not None:
            try:
                return int(span[key])
            except (TypeError, ValueError):
                return 0
    return 0


def from_otel_genai(
    spans: list[dict],
    agent: Union[str, dict],
    task: Union[str, dict],
    outcome: Optional[dict] = None,
) -> tuple[dict, list[str]]:
    """Convert OTel GenAI spans to a SCHEMA trajectory dict.

    Spans are ordered by ``start_time_unix_nano``; ``gen_ai.operation.name``
    (with span-name fallbacks) selects the step type: chat-like operations
    become ``reason`` (the last mapped span becomes ``answer``),
    ``execute_tool`` becomes a tool-ish step typed from ``gen_ai.tool.name``
    cues, ``retrieve`` maps directly.  Token counts come from
    ``gen_ai.usage.input_tokens``/``output_tokens``; latency from the span's
    nanosecond timestamps.  Unmapped spans produce warnings, not errors.
    Returns ``(trajectory_dict, warnings)``; the dict is pre-validated.
    """
    warnings: list[str] = []
    ordered = sorted(spans, key=lambda s: (_span_time(s, "start"),
                                           s.get("name", "")))
    steps: list[dict] = []
    total_in = total_out = 0

    for span in ordered:
        name = str(span.get("name", ""))
        attrs: dict[str, Any] = _span_attrs(span)
        op = attrs.get("gen_ai.operation.name")
        if not op:
            head = name.split(" ")[0].lower() if name else ""
            if head in _CHAT_OPS:
                op = "chat"
            elif head in ("execute_tool", "tool"):
                op = "execute_tool"
            elif head in ("retrieve", "search"):
                op = head
        start = _span_time(span, "start")
        end = _span_time(span, "end") or start
        latency = round(max(0, end - start) / 1e9, 4)
        tok_in = int(attrs.get("gen_ai.usage.input_tokens", 0) or 0)
        tok_out = int(attrs.get("gen_ai.usage.output_tokens", 0) or 0)

        if op in _CHAT_OPS:
            step_type = "reason"
            step_name = attrs.get("gen_ai.request.model", "llm")
            step_input = _first_attr(attrs, _PROMPT_KEYS) or name
            step_output = _first_attr(attrs, _COMPLETION_KEYS)
        elif op == "execute_tool":
            tool = str(attrs.get("gen_ai.tool.name", "")
                       or name.split(" ", 1)[-1] or "tool")
            step_type = _type_from_tool_name(tool)
            step_name = tool
            step_input = _first_attr(attrs, _TOOL_INPUT_KEYS) or name
            step_output = _first_attr(attrs, _TOOL_OUTPUT_KEYS)
        elif op in ("retrieve", "search"):
            step_type = op
            step_name = str(attrs.get("gen_ai.tool.name", op))
            step_input = _first_attr(attrs, _TOOL_INPUT_KEYS) or name
            step_output = _first_attr(attrs, _TOOL_OUTPUT_KEYS)
        else:
            warnings.append(
                f"span {name!r} has no recognized gen_ai operation; skipped"
            )
            continue

        total_in += tok_in
        total_out += tok_out
        steps.append(
            {
                "index": len(steps),
                "type": step_type,
                "name": step_name,
                "input": step_input,
                "output": step_output,
                "tokens": tok_in + tok_out,
                "latency_s": latency,
                "quality": None,
                "note": None,
            }
        )

    if not steps:
        raise ValueError("no spans could be mapped to steps")
    steps[-1]["type"] = "answer"
    answer_text = steps[-1]["output"] or steps[-1]["input"]

    outcome = outcome or {}
    task_d = _task_dict(task)
    agent_d = _agent_dict(agent)
    trajectory = {
        "schema_version": 1,
        "trace_id": f"{task_d['id']}-{agent_d['name']}",
        "agent": agent_d,
        "task": task_d,
        "outcome": {
            "success": bool(outcome.get("success", True)),
            "answer": str(outcome.get("answer", answer_text)),
            "score": outcome.get("score"),
        },
        "totals": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_usd": float(outcome.get("cost_usd", 0.0)),
            "latency_s": round(sum(s["latency_s"] for s in steps), 4),
        },
        "steps": steps,
    }
    Trajectory.from_json(trajectory)  # validate before handing back
    return trajectory, warnings


def from_openai_messages(
    messages: list[dict], meta: Optional[dict] = None
) -> tuple[dict, list[str]]:
    """Convert an OpenAI chat-completions message array to a trajectory.

    system + first user messages form the task prompt; assistant messages
    with ``tool_calls`` become tool-ish steps (typed by function-name cues),
    tool-role results fill the matching step's output, plain assistant
    messages become ``reason`` steps, and the final assistant message with
    content becomes the ``answer``.  Token counts are estimated as
    ``len(text)/4`` when no usage data exists.  Returns
    ``(trajectory_dict, warnings)``; the dict is pre-validated.
    """
    meta = meta or {}
    warnings: list[str] = []

    prompt_parts: list[str] = []
    final_answer_pos: Optional[int] = None
    for pos, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("content") and not msg.get("tool_calls"):
            final_answer_pos = pos

    steps: list[dict] = []
    call_step: dict[str, int] = {}
    seen_user = False
    for pos, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system":
            prompt_parts.append(content)
        elif role == "user":
            if not seen_user:
                prompt_parts.append(content)
                seen_user = True
            else:
                warnings.append(f"extra user message at position {pos} ignored")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                for call in tool_calls:
                    fn = (call.get("function") or {})
                    fname = fn.get("name", "tool")
                    args = fn.get("arguments", "")
                    call_step[call.get("id", f"call_{pos}")] = len(steps)
                    steps.append(
                        {
                            "index": len(steps),
                            "type": _type_from_tool_name(fname),
                            "name": fname,
                            "input": str(args),
                            "output": "",
                            "tokens": _estimate_tokens(str(args)),
                            "latency_s": 0.0,
                            "quality": None,
                            "note": None,
                        }
                    )
            elif content:
                step_type = "answer" if pos == final_answer_pos else "reason"
                steps.append(
                    {
                        "index": len(steps),
                        "type": step_type,
                        "name": "final" if step_type == "answer" else "reason",
                        "input": content,
                        "output": content if step_type == "answer" else "",
                        "tokens": _estimate_tokens(content),
                        "latency_s": 0.0,
                        "quality": None,
                        "note": None,
                    }
                )
        elif role == "tool":
            call_id = msg.get("tool_call_id")
            if call_id in call_step:
                step = steps[call_step[call_id]]
                step["output"] = content
                step["tokens"] += _estimate_tokens(content)
            else:
                warnings.append(
                    f"tool result at position {pos} has no matching tool call; skipped"
                )
        else:
            warnings.append(f"message role {role!r} at position {pos} not mapped")

    if not steps or steps[-1]["type"] != "answer":
        raise ValueError("messages contain no final assistant answer")

    answer_text = steps[-1]["output"]
    task_d = _task_dict(meta.get("task", "task"), prompt_fallback="\n".join(prompt_parts))
    if not task_d["prompt"]:
        task_d["prompt"] = "\n".join(prompt_parts)
    trajectory = {
        "schema_version": 1,
        "trace_id": f"{task_d['id']}-{_agent_dict(meta.get('agent', 'openai-agent'))['name']}",
        "agent": _agent_dict(meta.get("agent", "openai-agent")),
        "task": task_d,
        "outcome": {
            "success": bool(meta.get("success", True)),
            "answer": answer_text,
            "score": meta.get("score"),
        },
        "totals": {
            "input_tokens": _estimate_tokens("\n".join(prompt_parts)),
            "output_tokens": sum(s["tokens"] for s in steps),
            "cost_usd": float(meta.get("cost_usd", 0.0)),
            "latency_s": float(meta.get("latency_s", 0.0)),
        },
        "steps": steps,
    }
    Trajectory.from_json(trajectory)  # validate before handing back
    return trajectory, warnings
