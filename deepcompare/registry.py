"""Pluggable trace-format registry (SCHEMA.md v17).

Agent stacks disagree about how a run is recorded, and new ones appear
faster than any fixed list of adapters can track.  So the format layer is a
registry rather than a hard-coded switch: an adapter is a *detector* plus a
*converter*, and adding support for a new stack means registering one more
pair — no change to the comparison engine, which only ever sees SCHEMA
trajectories.

Two properties make this safe to lean on:

**Detection is evidence-based, not guesswork.**  Each detector returns a
confidence in [0, 1] with a reason, and :func:`detect_format` reports the
ranked candidates rather than silently picking one.  A file that matches
nothing is a clear error naming what was looked for, not a crash.

**Conversion is inspectable before it is trusted.**  :func:`dry_run` reports
what each adapter *would* produce — step count, types, how much text and
timing survived, and every warning — so a new format can be checked before
its output is compared against anything.

The registry ships adapters for OpenTelemetry GenAI spans (the vendor-neutral
path, which is how "any model" is actually achieved: providers emit the same
``gen_ai.*`` attributes), OpenAI-style chat-completions messages, Anthropic-
style messages, and already-conformant SCHEMA trajectories.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .adapters import from_openai_messages, from_otel_genai
from .logprobs import attach_telemetry, extract_logprobs
from .trace import Trajectory

#: length above which a step's input reads as content rather than a label an
#: adapter fell back to (span names and tool names are short).
_LABEL_LENGTH = 40

#: name -> {detect, convert, description}
_ADAPTERS: dict[str, dict] = {}


def register(
    name: str,
    detect: Callable[[Any], tuple[float, str]],
    convert: Callable[[Any], tuple[dict, list[str]]],
    description: str,
) -> None:
    """Register a trace format.

    ``detect(data) -> (confidence, reason)`` scores how much the payload
    looks like this format.  ``convert(data) -> (trajectory, warnings)``
    returns a SCHEMA trajectory dict.
    """
    _ADAPTERS[name] = {"detect": detect, "convert": convert,
                       "description": description}


def formats() -> list[dict]:
    """Every registered format, name-sorted."""
    return [
        {"name": name, "description": _ADAPTERS[name]["description"]}
        for name in sorted(_ADAPTERS)
    ]


def detect_format(data: Any) -> dict:
    """Rank every registered format against a payload.

    Returns ``{"best": name|None, "confidence": float, "candidates": [...]}``.
    Nothing is chosen silently: the caller sees every score and reason.
    """
    candidates = []
    for name in sorted(_ADAPTERS):
        try:
            confidence, reason = _ADAPTERS[name]["detect"](data)
        except Exception as exc:  # a detector must never break discovery
            confidence, reason = 0.0, f"detector error: {exc}"
        candidates.append({"format": name, "confidence": round(confidence, 3),
                           "reason": reason})
    candidates.sort(key=lambda c: (-c["confidence"], c["format"]))
    best = candidates[0] if candidates and candidates[0]["confidence"] > 0 else None
    return {
        "best": best["format"] if best else None,
        "confidence": best["confidence"] if best else 0.0,
        "candidates": candidates,
    }


def convert(data: Any, format_name: Optional[str] = None) -> dict:
    """Convert a payload, detecting the format when not told which.

    Returns ``{"trajectory", "warnings", "format", "confidence"}``.  Raises
    ValueError naming the known formats when nothing matches.
    """
    if format_name is None:
        detection = detect_format(data)
        if not detection["best"]:
            known = ", ".join(sorted(_ADAPTERS))
            raise ValueError(
                f"could not identify the trace format; nothing matched. "
                f"Known formats: {known}. Pass --format explicitly, or add an "
                f"adapter with deepcompare.registry.register()."
            )
        format_name = detection["best"]
        confidence = detection["confidence"]
    else:
        if format_name not in _ADAPTERS:
            known = ", ".join(sorted(_ADAPTERS))
            raise ValueError(f"unknown format {format_name!r}; known: {known}")
        confidence, _ = _ADAPTERS[format_name]["detect"](data)

    trajectory, warnings = _ADAPTERS[format_name]["convert"](data)
    return {"trajectory": trajectory, "warnings": warnings,
            "format": format_name, "confidence": round(confidence, 3)}


def dry_run(data: Any, format_name: Optional[str] = None) -> dict:
    """Report what conversion *would* produce, without committing to it.

    Fidelity counters matter more than the step count: a mapping that
    produces steps with no text has technically succeeded and is useless,
    because alignment and every semantic analysis run on that text.
    """
    detection = detect_format(data)
    target = format_name or detection["best"]
    if target is None:
        return {"ok": False, "detection": detection,
                "error": "no format matched"}
    try:
        result = convert(data, target)
    except ValueError as exc:
        return {"ok": False, "detection": detection, "error": str(exc)}

    trajectory = result["trajectory"]
    steps = trajectory["steps"]
    types: dict[str, int] = {}
    for step in steps:
        types[step["type"]] = types.get(step["type"], 0) + 1
    # A step whose only "text" is the span/tool name it was named after has
    # no content to compare — adapters fall back to the name when the payload
    # carried nothing, and counting that as text hides the very failure this
    # report exists to surface.
    def substantive(step: dict) -> bool:
        if (step["output"] or "").strip():
            return True
        # No observation recorded: the input alone counts only if it looks
        # like real content rather than the label an adapter fell back to.
        # Span and tool names are short; queries, prompts and arguments are
        # not, so a length floor separates them without guessing at names.
        return len((step["input"] or "").strip()) > _LABEL_LENGTH

    with_text = sum(1 for s in steps if substantive(s))
    with_output = sum(1 for s in steps if (s["output"] or "").strip())
    with_timing = sum(1 for s in steps if s["latency_s"] > 0)
    with_tokens = sum(1 for s in steps if s["tokens"] > 0)

    notes: list[str] = []
    if steps and with_output < len(steps):
        notes.append(
            f"{len(steps) - with_output} of {len(steps)} step(s) recorded no "
            f"output; observations are what downstream analysis reads."
        )
    if steps and with_text < len(steps):
        notes.append(
            f"{len(steps) - with_text} of {len(steps)} step(s) carry no real "
            f"text (only the name they were labelled with) — alignment and "
            f"every semantic analysis read that text, so check the field "
            f"mapping before trusting a comparison."
        )
    if steps and with_timing == 0:
        notes.append("no step carries timing; latency analysis will be empty.")
    if steps and with_tokens == 0:
        notes.append("no step carries token counts; cost analysis will be empty.")

    return {
        "ok": True,
        "format": result["format"],
        "confidence": result["confidence"],
        "detection": detection,
        "agent": trajectory["agent"],
        "task": trajectory["task"]["id"],
        "steps": len(steps),
        "step_types": dict(sorted(types.items())),
        "fidelity": {
            "steps_with_text": with_text,
            "steps_with_timing": with_timing,
            "steps_with_tokens": with_tokens,
            "steps_with_output": with_output,
        },
        "warnings": result["warnings"],
        "notes": notes,
    }


# --------------------------------------------------------------------------
# Built-in adapters
# --------------------------------------------------------------------------

def _meta_of(data: Any) -> dict:
    meta = data.get("meta") if isinstance(data, dict) else None
    return meta if isinstance(meta, dict) else {}


def _outcome_of(data: Any) -> Optional[dict]:
    meta = _meta_of(data)
    outcome = (data.get("outcome") if isinstance(data, dict) else None) \
        or meta.get("outcome")
    if outcome is None and ("success" in meta or "answer" in meta):
        outcome = {k: meta[k] for k in ("success", "answer", "score") if k in meta}
    return outcome


def _detect_otel(data: Any) -> tuple[float, str]:
    if not isinstance(data, dict):
        return 0.0, "not a JSON object"
    spans = data.get("spans")
    if not isinstance(spans, list) or not spans:
        return 0.0, "no 'spans' array"
    genai = 0
    for span in spans[:20]:
        if not isinstance(span, dict):
            continue
        attrs = span.get("attributes")
        keys: list[str] = []
        if isinstance(attrs, dict):
            keys = list(attrs)
        elif isinstance(attrs, list):
            keys = [str(a.get("key")) for a in attrs if isinstance(a, dict)]
        if any(k.startswith("gen_ai.") for k in keys):
            genai += 1
    if genai:
        return (min(1.0, 0.6 + 0.4 * genai / min(len(spans), 20)),
                f"{genai} span(s) carry gen_ai.* attributes")
    return 0.35, "has a 'spans' array but no gen_ai.* attributes"


def _convert_otel(data: Any) -> tuple[dict, list[str]]:
    meta = _meta_of(data)
    return from_otel_genai(
        data["spans"],
        agent=data.get("agent") or meta.get("agent") or "otel-agent",
        task=data.get("task") or meta.get("task") or "task",
        outcome=_outcome_of(data),
    )


def _detect_openai(data: Any) -> tuple[float, str]:
    if not isinstance(data, dict):
        return 0.0, "not a JSON object"
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return 0.0, "no 'messages' array"
    roles = {m.get("role") for m in messages if isinstance(m, dict)}
    if "tool" in roles or any(
        isinstance(m, dict) and m.get("tool_calls") for m in messages
    ):
        return 0.95, "chat messages with tool_calls / tool results"
    if roles & {"assistant", "user"}:
        return 0.7, f"chat messages with roles {sorted(r for r in roles if r)}"
    return 0.3, "has a 'messages' array of unknown shape"


def _convert_openai(data: Any) -> tuple[dict, list[str]]:
    """Chat-completions messages, plus logprobs when the server returned them.

    vLLM and TGI both expose OpenAI-compatible endpoints, so a self-hosted
    open-weight model lands here — and those servers will return full
    logprobs, which is what turns the model-confidence analysis from
    unavailable into real.
    """
    trajectory, warnings = from_openai_messages(data["messages"],
                                                _meta_of(data) or None)
    responses = data.get("responses")
    if isinstance(responses, list) and responses:
        attached = 0
        for step, response in zip(trajectory["steps"], responses):
            if extract_logprobs(response):
                attach_telemetry(step, response, source="openai-compatible-logprobs")
                attached += 1
        if not attached:
            warnings.append(
                "responses were supplied but carried no logprobs; request "
                "logprobs to enable the model-confidence analysis"
            )
    return trajectory, warnings


def _detect_anthropic(data: Any) -> tuple[float, str]:
    if not isinstance(data, dict):
        return 0.0, "not a JSON object"
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return 0.0, "no 'messages' array"
    blocks = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in (
                    "tool_use", "tool_result", "text", "thinking"
                ):
                    blocks += 1
    if blocks:
        return 0.95, f"{blocks} typed content block(s) (tool_use/tool_result)"
    return 0.0, "no typed content blocks"


def _convert_anthropic(data: Any) -> tuple[dict, list[str]]:
    """Flatten Anthropic-style content blocks into chat-completions shape.

    Reusing the OpenAI converter keeps one mapping of message roles to step
    types instead of two that can drift apart.
    """
    warnings: list[str] = []
    flattened: list[dict] = []
    for message in data["messages"]:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        content = message.get("content")
        if isinstance(content, str):
            flattened.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            warnings.append(f"message with {type(content).__name__} content skipped")
            continue
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind in ("text", "thinking"):
                text_parts.append(str(block.get(kind, block.get("text", ""))))
            elif kind == "tool_use":
                import json as _json
                tool_calls.append({
                    "id": block.get("id", f"call_{len(tool_calls)}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", "tool"),
                        "arguments": _json.dumps(block.get("input", {}),
                                                 ensure_ascii=False),
                    },
                })
            elif kind == "tool_result":
                result = block.get("content")
                if isinstance(result, list):
                    result = " ".join(
                        str(b.get("text", "")) for b in result
                        if isinstance(b, dict)
                    )
                flattened.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": str(result or ""),
                })
            else:
                warnings.append(f"content block type {kind!r} not mapped")
        if text_parts or tool_calls:
            entry: dict = {"role": role, "content": " ".join(text_parts).strip()}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            flattened.append(entry)

    trajectory, more = from_openai_messages(flattened, _meta_of(data) or None)
    return trajectory, warnings + more


def _detect_schema(data: Any) -> tuple[float, str]:
    if not isinstance(data, dict):
        return 0.0, "not a JSON object"
    if "steps" in data and "agent" in data and "task" in data:
        try:
            Trajectory.from_json(data)
            return 1.0, "already a valid SCHEMA trajectory"
        except ValueError as exc:
            return 0.5, f"looks like a SCHEMA trajectory but is invalid: {exc}"
    return 0.0, "missing steps/agent/task"


def _convert_schema(data: Any) -> tuple[dict, list[str]]:
    Trajectory.from_json(data)
    return data, []


register("otel", _detect_otel, _convert_otel,
         "OpenTelemetry GenAI spans (gen_ai.* attributes; any provider)")
register("openai", _detect_openai, _convert_openai,
         "OpenAI chat-completions messages with tool_calls")
register("anthropic", _detect_anthropic, _convert_anthropic,
         "Anthropic messages with tool_use / tool_result content blocks")
register("schema", _detect_schema, _convert_schema,
         "an already-conformant AgentDiff trajectory")


def _detect_ollama(data: Any) -> tuple[float, str]:
    """Ollama /api/chat transcripts — the common open-weight runner."""
    if not isinstance(data, dict):
        return 0.0, "not a JSON object"
    turns = data.get("turns") or data.get("responses")
    if not isinstance(turns, list) or not turns:
        return 0.0, "no 'turns'/'responses' array"
    hits = sum(
        1 for t in turns
        if isinstance(t, dict) and isinstance(t.get("message"), dict)
        and ("eval_count" in t or "model" in t or "done" in t)
    )
    if hits:
        return (min(1.0, 0.75 + 0.25 * hits / len(turns)),
                f"{hits} Ollama-shaped turn(s) with message + eval fields")
    return 0.0, "turns present but not Ollama-shaped"


def _convert_ollama(data: Any) -> tuple[dict, list[str]]:
    """Flatten Ollama turns into chat-completions shape, keeping token counts.

    Ollama reports ``prompt_eval_count``/``eval_count`` per turn and, when
    asked, per-token logprobs — so an open-weight run converted this way
    arrives with real usage and real confidence rather than estimates.
    """
    warnings: list[str] = []
    meta = _meta_of(data)
    turns = data.get("turns") or data.get("responses") or []
    messages: list[dict] = []
    telemetry: list[Optional[dict]] = []

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        message = turn.get("message")
        if not isinstance(message, dict):
            warnings.append("turn without a message object skipped")
            continue
        entry: dict = {"role": message.get("role", "assistant"),
                       "content": message.get("content", "")}
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            import json as _json
            entry["tool_calls"] = [
                {"id": call.get("id", f"call_{i}"), "type": "function",
                 "function": {
                     "name": (call.get("function") or {}).get("name", "tool"),
                     "arguments": _json.dumps(
                         (call.get("function") or {}).get("arguments", {}),
                         ensure_ascii=False),
                 }}
                for i, call in enumerate(calls) if isinstance(call, dict)
            ]
        messages.append(entry)
        telemetry.append(turn)

    if not messages:
        raise ValueError("no Ollama turns could be mapped to messages")

    # Ollama has no tool-result role; the caller records those as user turns.
    trajectory, more = from_openai_messages(messages, meta or None)

    # Carry per-turn logprobs onto the steps they produced.  Steps and turns
    # are NOT positionally aligned — a tool-result turn fills an existing
    # step's output rather than creating one — so match on the text the turn
    # generated instead of zipping, which silently mis-attributes confidence.
    attached = 0
    used: set[int] = set()
    for turn in telemetry:
        if not extract_logprobs(turn):
            continue
        message = turn.get("message") or {}
        content = str(message.get("content") or "").strip()
        tool_names = {
            (call.get("function") or {}).get("name")
            for call in (message.get("tool_calls") or [])
            if isinstance(call, dict)
        }
        target = None
        for index, step in enumerate(trajectory["steps"]):
            if index in used:
                continue
            if tool_names and step.get("name") in tool_names:
                target = index
                break
            if content and (content in (step.get("output") or "")
                            or content in (step.get("input") or "")):
                target = index
                break
        if target is None:
            warnings.append(
                f"logprobs for a turn could not be matched to a step "
                f"({content[:40]!r}); telemetry dropped rather than guessed"
            )
            continue
        used.add(target)
        attach_telemetry(trajectory["steps"][target], turn,
                         temperature=(turn.get("options") or {}).get("temperature"),
                         source="ollama-logprobs")
        attached += 1
    if telemetry and not attached:
        warnings.append(
            "no logprobs in these turns; request them from the server to "
            "enable the model-confidence analysis"
        )
    return trajectory, warnings + more


register("ollama", _detect_ollama, _convert_ollama,
         "Ollama /api/chat turns (open-weight runners; keeps eval counts "
         "and logprobs when present)")
