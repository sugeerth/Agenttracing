"""Trace adapters for DeepCompare AI (SCHEMA.md v9).

Converts foreign trace formats into SCHEMA trajectories:

- :func:`from_otel_genai` — OpenTelemetry GenAI-convention spans (flat
  ``attributes`` dicts with ``gen_ai.*`` keys; span-name fallbacks;
  start/end nanoseconds -> latency).
- :func:`from_openai_messages` — a chat-completions style message array
  with tool calls (system + first user -> task prompt; assistant tool_calls
  -> tool-ish steps typed by name cues; tool-role results -> step outputs;
  plain assistant -> reason; final assistant -> answer).
- :func:`from_verl` — a veRL agent-loop rollout record (message history or
  prompt+response, reward_score / turn_scores / tool_rewards, data_source,
  ground_truth, uid); rewards land on the steps as SCHEMA ``reward``.
- :func:`from_agent_lightning` — an Agent Lightning rollout export
  (LightningStore spans, or v1 ``model_request`` / ``reward`` events).

All return ``(trajectory_dict, warnings)`` where the dict validates via
``Trajectory.from_json`` and warnings list items that could not be mapped;
the RL adapters also stamp ``source.fidelity`` counters.  :func:`register_formats`
adds ``verl`` and ``agent-lightning`` to the format registry (the CLI calls it).
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

#: operations that describe an agent thinking or planning rather than acting.
#: ``invoke_agent`` is how the GenAI conventions mark an agent turn.
_AGENT_OPS = ("invoke_agent", "invoke_workflow")

#: operations that fetch context rather than call a tool.
_RETRIEVAL_OPS = ("embeddings", "retrieve", "search", "create_memory",
                  "get_memory", "query_memory")

#: operations that carry no step of their own (setup / teardown bookkeeping).
_META_OPS = ("create_agent", "create_memory_store", "delete_memory_store",
             "delete_memory")

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


def _span_id(span: dict) -> Optional[str]:
    for key in ("span_id", "spanId", "id"):
        v = span.get(key)
        if v:
            return str(v)
    return None


def _parent_id(span: dict) -> Optional[str]:
    for key in ("parent_span_id", "parentSpanId", "parent_id", "parent"):
        v = span.get(key)
        if v:
            return str(v)
    return None


def _ancestors(span: dict, by_id: dict) -> list:
    out: list = []
    pid = _parent_id(span)
    seen = set()
    while pid and pid in by_id and pid not in seen:
        out.append(pid)
        seen.add(pid)
        pid = _parent_id(by_id[pid])
    return out


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

    # delegation: an ``invoke_agent`` span nested under another agent's span
    # is a sub-agent; every span beneath it carries that sub-agent's
    # SCHEMA ``span`` (id, agent, parent). The outermost agent span is the
    # root agent and stamps nothing.
    by_id: dict[str, dict] = {}
    for span in ordered:
        sid = _span_id(span)
        if sid:
            by_id[sid] = span
    agent_spans: dict[str, dict] = {}
    for span in ordered:
        attrs_ = _span_attrs(span)
        op_ = attrs_.get("gen_ai.operation.name") or (str(span.get("name", "")).split(" ")[0].lower())
        if op_ in _AGENT_OPS and _span_id(span):
            agent_spans[_span_id(span)] = span
    root_agent_ids = {sid for sid, sp in agent_spans.items()
                      if not any(anc in agent_spans for anc in _ancestors(sp, by_id))}

    def delegation_of(span: dict) -> Optional[dict]:
        chain = [a for a in _ancestors(span, by_id) if a in agent_spans and a not in root_agent_ids]
        if _span_id(span) in agent_spans and _span_id(span) not in root_agent_ids:
            chain = [_span_id(span)] + chain
        if not chain:
            return None
        own = chain[0]
        parent = next((a for a in chain[1:]), None)
        return {"id": own, "agent": str(_span_attrs(agent_spans[own]).get("gen_ai.agent.name") or agent_spans[own].get("name") or own),
                "parent": parent}

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
        elif op in _RETRIEVAL_OPS:
            step_type = "search" if op in ("search",) else "retrieve"
            step_name = str(attrs.get("gen_ai.tool.name", op))
            step_input = _first_attr(attrs, _TOOL_INPUT_KEYS) or name
            step_output = _first_attr(attrs, _TOOL_OUTPUT_KEYS)
        elif op in _AGENT_OPS:
            # An agent turn: planning if it carries no tool, else reasoning.
            step_type = "plan" if not attrs.get("gen_ai.tool.name") else "reason"
            step_name = str(attrs.get("gen_ai.agent.name", op))
            step_input = _first_attr(attrs, _PROMPT_KEYS) or name
            step_output = _first_attr(attrs, _COMPLETION_KEYS)
        elif op in _META_OPS:
            warnings.append(
                f"span {name!r} is {op} (lifecycle bookkeeping, not a step); skipped"
            )
            continue
        else:
            warnings.append(
                f"span {name!r} has no recognized gen_ai operation; skipped"
            )
            continue

        total_in += tok_in
        total_out += tok_out
        step = {
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
        delegated = delegation_of(span)
        if delegated:
            step["span"] = delegated
        steps.append(step)

    if not steps:
        raise ValueError("no spans could be mapped to steps")
    steps[-1]["type"] = "answer"
    answer_text = steps[-1]["output"] or steps[-1]["input"]

    outcome = outcome or {}
    task_d = _task_dict(task)
    agent_d = _agent_dict(agent)
    # The provider/model actually observed in the spans wins over a caller's
    # guess, so a converted trace always says which model produced it.
    for span in ordered:
        attrs = _span_attrs(span)
        model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
        if model and not agent_d.get("model"):
            agent_d["model"] = str(model)
        provider = attrs.get("gen_ai.provider.name")
        if provider and not agent_d.get("version"):
            agent_d["version"] = str(provider)
        if agent_d.get("model") and agent_d.get("version"):
            break
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


# ---------------------------------------------------------------------------
# RL training frameworks: veRL agent-loop rollouts, Agent Lightning traces
# ---------------------------------------------------------------------------
#
# What a trainer logs is not what an observability stack logs.  veRL's agent
# loop hands the trainer token ids, a response mask, ``num_turns`` and a
# ``reward_score`` (plus ``turn_scores`` / ``tool_rewards`` in
# ``extra_fields``); what people *keep* is the message history the loop
# built (OpenAI-style, tool calls and ``tool`` results), the dataset columns
# (``data_source``, ``reward_model.ground_truth``, ``extra_info``, ``uid``)
# and the scores.  Agent Lightning keeps OpenTelemetry spans (LLM calls under
# ``gen_ai.*``, rewards as ``agentlightning.reward`` / ``.annotation`` spans)
# or, from v1.0, ``model_request`` and ``reward`` events per rollout.  Both
# adapters accept those shapes, map turns to SCHEMA steps, carry the rewards
# onto the steps (SCHEMA ``reward``) and count what they could not map.

_NUMERIC = (int, float)

#: keys under which a veRL-style record carries the whole-episode reward,
#: most specific first (``reward_score`` is the AgentLoopOutput field;
#: ``score`` is the rollout-dump column).
_VERL_EPISODE_KEYS = ("reward_score", "reward", "score", "rm_score")
#: keys carrying one reward per assistant turn (``turn_scores`` is what the
#: tool agent loop puts in ``extra_fields``).
_VERL_TURN_KEYS = ("turn_scores", "turn_rewards", "step_rewards", "per_turn_rewards")
#: keys carrying one reward per tool call, in call order.
_VERL_TOOL_KEYS = ("tool_rewards", "tool_scores")
#: keys carrying the message history.
_VERL_MESSAGE_KEYS = ("messages", "multi_turn", "turns", "conversation", "history")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, _NUMERIC):
        return None
    return float(value)


def _text_of(value: Any) -> str:
    """Content as text: strings as they are, content-block lists joined,
    anything else JSON."""
    import json as _json
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return " ".join(p for p in parts if p).strip()
    if isinstance(value, dict):
        return _json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _call_parts(call: Any) -> tuple[str, str, str]:
    """(id, name, arguments) of a tool call in OpenAI, flat or Anthropic shape."""
    if not isinstance(call, dict):
        return "", "tool", str(call)
    fn = call.get("function") if isinstance(call.get("function"), dict) else call
    name = str(fn.get("name") or call.get("name") or "tool")
    args = fn.get("arguments", fn.get("input", call.get("arguments", call.get("input", ""))))
    return str(call.get("id") or ""), name, _text_of(args) if not isinstance(args, str) else args


def _new_step(steps: list, step_type: str, name: str, inp: str, out: str = "",
              latency: float = 0.0, tokens: Optional[int] = None) -> dict:
    step = {
        "index": len(steps), "type": step_type, "name": name, "input": inp, "output": out,
        "tokens": tokens if tokens is not None else _estimate_tokens(inp) + _estimate_tokens(out),
        "latency_s": latency, "quality": None, "note": None,
    }
    if tokens is None:
        step["tokens_basis"] = "estimated"
    steps.append(step)
    return step


def _contains_normalised(haystack: str, needle: str) -> bool:
    fold = lambda s: " ".join(str(s).lower().replace(",", "").split())  # noqa: E731
    return bool(fold(needle)) and fold(needle) in fold(haystack)


def _verl_messages(record: dict, warnings: list) -> list:
    """The rollout as an OpenAI-shaped message list, from whichever of the
    logged shapes the record has."""
    for key in _VERL_MESSAGE_KEYS:
        val = record.get(key)
        if isinstance(val, list) and val and all(isinstance(m, dict) for m in val):
            return list(val)
    prompt = record.get("raw_prompt")
    if not isinstance(prompt, list):
        prompt = record.get("prompt", record.get("input"))
    response = record.get("response", record.get("output"))
    messages: list = []
    if isinstance(prompt, list) and all(isinstance(m, dict) for m in prompt):
        messages.extend(prompt)
    elif prompt is not None:
        messages.append({"role": "user", "content": _text_of(prompt)})
    if isinstance(response, list) and all(isinstance(m, dict) for m in response):
        messages.extend(response)
    elif response is not None:
        messages.append({"role": "assistant", "content": _text_of(response)})
    if messages and not any(m.get("role") == "assistant" for m in messages):
        warnings.append("record carries a prompt but no response; nothing the policy did is logged")
    return messages


def _verl_reward_terms(record: dict, n_turns: int, warnings: list) -> tuple[Optional[float], list, list, dict]:
    """(episode reward, per-turn rewards, per-tool rewards, terms) from the
    several places a veRL record puts scores."""
    terms: dict = {}
    episode = None
    for key in _VERL_EPISODE_KEYS:
        episode = _num(record.get(key))
        if episode is not None:
            terms["episode_key"] = key
            break
    scores = record.get("reward_scores")
    per_turn: list = []
    if isinstance(scores, dict):
        for key in ("score", "reward", "reward_score"):
            if _num(scores.get(key)) is not None and episode is None:
                episode = _num(scores[key])
                terms["episode_key"] = f"reward_scores.{key}"
        numeric = {k: _num(v) for k, v in scores.items() if _num(v) is not None}
        if numeric:
            terms["reward_scores"] = numeric
            if episode is None:
                episode = float(sum(numeric.values()))
                terms["episode_key"] = "sum(reward_scores)"
    elif isinstance(scores, list) and scores:
        values = [_num(v) for v in scores]
        if all(v is not None for v in values):
            if len(values) == n_turns:
                per_turn = values
                terms["per_turn_key"] = "reward_scores"
            else:
                warnings.append(f"reward_scores has {len(values)} entries for {n_turns} assistant turn(s); "
                                f"summed as the episode reward rather than spread")
                if episode is None:
                    episode = float(sum(values))
                    terms["episode_key"] = "sum(reward_scores)"
    elif _num(scores) is not None and episode is None:
        episode = _num(scores)
        terms["episode_key"] = "reward_scores"
    if not per_turn:
        for key in _VERL_TURN_KEYS:
            val = record.get(key)
            if val is None and isinstance(record.get("extra_fields"), dict):
                val = record["extra_fields"].get(key)
            if isinstance(val, list) and val:
                values = [_num(v) for v in val]
                if any(v is None for v in values):
                    warnings.append(f"{key} carries non-numeric entries; ignored")
                    continue
                if len(values) != n_turns:
                    warnings.append(f"{key} has {len(values)} entries for {n_turns} assistant turn(s); "
                                    f"assigned in order, extras dropped")
                per_turn = values
                terms["per_turn_key"] = key
                break
    per_tool: list = []
    for key in _VERL_TOOL_KEYS:
        val = record.get(key)
        if val is None and isinstance(record.get("extra_fields"), dict):
            val = record["extra_fields"].get(key)
        if isinstance(val, list) and val and all(_num(v) is not None for v in val):
            per_tool = [_num(v) for v in val]
            terms["per_tool_key"] = key
            break
    if episode is None and per_turn:
        episode = float(sum(per_turn))
        terms["episode_key"] = f"sum({terms.get('per_turn_key')})"
    return episode, per_turn, per_tool, terms


def _verl_ground_truth(record: dict) -> Optional[str]:
    gt = record.get("ground_truth")
    if gt is None and isinstance(record.get("reward_model"), dict):
        gt = record["reward_model"].get("ground_truth")
    if gt is None:
        gt = record.get("gts")
    extra = record.get("extra_info")
    if gt is None and isinstance(extra, dict):
        gt = extra.get("ground_truth", extra.get("answer"))
    if gt is None:
        return None
    if isinstance(gt, list) and len(gt) == 1:
        gt = gt[0]
    return _text_of(gt) if not isinstance(gt, str) else gt


def from_verl(record: Union[dict, list], agent: Optional[str] = None) -> Any:
    """Convert a veRL agent-loop rollout record to a SCHEMA trajectory.

    Accepts the record as commonly logged: a ``messages`` (or ``multi_turn``
    / ``turns``) history in OpenAI shape, or ``raw_prompt`` + ``response``,
    or the rollout-dump columns ``input`` / ``output`` / ``gts`` / ``score``;
    optional ``tools`` schemas, ``reward_score`` / ``reward`` / ``score`` /
    ``reward_scores``, per-turn ``turn_scores`` and per-call ``tool_rewards``
    (top-level or under ``extra_fields``), ``num_turns``, ``data_source``,
    ``ground_truth`` (or ``reward_model.ground_truth``), ``extra_info``,
    ``uid`` / ``index``, ``model`` / ``policy`` / ``agent_name``.

    Mapping: an assistant tool call becomes a tool-ish step (typed by name
    cues) whose output is the ``tool`` result that answers it (by
    ``tool_call_id``, else the next unpaired result); assistant text becomes
    ``reason``; the final assistant text becomes ``answer``.  Rewards land on
    steps as SCHEMA ``reward``: per-turn rewards on the last step of their
    turn and per-call rewards on the tool steps when the record has them,
    else the episode reward on the answer step.  ``outcome.success`` is the
    reward when it is exactly 0 or 1, else ``ground_truth`` containment in
    the answer, else the record's own ``success``; ``outcome.score`` is the
    episode reward.  The task id is ``<data_source>-<uid>``; the agent name
    is ``agent`` (an override), else ``model`` / ``policy`` / ``agent_name``.

    A list of records returns a list of ``(trajectory, warnings)``; a dict
    returns one pair.  The trajectory carries ``source.format = "verl"`` and
    ``source.fidelity`` counters (turns, calls paired, rewards found, how
    success was decided).  Raises ``ValueError`` when nothing maps.
    """
    if isinstance(record, list):
        if not record:
            raise ValueError("from_verl: an empty list holds no rollout")
        return [from_verl(item, agent=agent) for item in record]
    if not isinstance(record, dict):
        raise ValueError(f"from_verl: a rollout record must be a JSON object, got {type(record).__name__}")
    warnings: list[str] = []
    messages = _verl_messages(record, warnings)
    if not messages:
        raise ValueError(
            "from_verl: no rollout found — expected 'messages' (a role/content list), "
            "'raw_prompt' + 'response', or 'prompt'/'input' + 'response'/'output'"
        )

    prompt_parts: list[str] = []
    steps: list[dict] = []
    by_call_id: dict[str, dict] = {}
    unpaired: list[dict] = []          # tool steps awaiting a result, in order
    turn_last_step: list[Optional[int]] = []   # per assistant turn: index of its last step
    tool_steps: list[int] = []
    counters = {"messages": len(messages), "assistant_turns": 0, "tool_calls": 0,
                "tool_results_paired": 0, "tool_results_unpaired": 0, "user_turns_dropped": 0,
                "roles_unmapped": 0}
    seen_user = False
    for pos, msg in enumerate(messages):
        role = str(msg.get("role") or "")
        content = _text_of(msg.get("content"))
        if role == "system":
            if content:
                prompt_parts.append(content)
        elif role == "user":
            if not seen_user:
                prompt_parts.append(content)
                seen_user = True
            elif not unpaired:
                counters["user_turns_dropped"] += 1
                warnings.append(f"user turn at position {pos} after the first (an interaction turn) not mapped")
            else:
                # some loops log the observation as a user turn: pair it
                step = unpaired.pop(0)
                step["output"] = content
                step["tokens"] += _estimate_tokens(content)
                counters["tool_results_paired"] += 1
        elif role == "assistant":
            counters["assistant_turns"] += 1
            calls = msg.get("tool_calls") or []
            if content:
                _new_step(steps, "reason", "reason", content)
            for call in calls:
                cid, name, args = _call_parts(call)
                step = _new_step(steps, _type_from_tool_name(name), name, args)
                counters["tool_calls"] += 1
                tool_steps.append(step["index"])
                unpaired.append(step)
                if cid:
                    by_call_id[cid] = step
            turn_last_step.append(steps[-1]["index"] if steps else None)
        elif role == "tool":
            cid = str(msg.get("tool_call_id") or "")
            step = by_call_id.get(cid) if cid else None
            if step is None and unpaired:
                step = unpaired[0]
            if step is None:
                counters["tool_results_unpaired"] += 1
                warnings.append(f"tool result at position {pos} answers no logged tool call; skipped")
                continue
            if step in unpaired:
                unpaired.remove(step)
            step["output"] = content
            step["tokens"] += _estimate_tokens(content)
            counters["tool_results_paired"] += 1
        else:
            counters["roles_unmapped"] += 1
            warnings.append(f"message role {role!r} at position {pos} not mapped")
    if not steps:
        raise ValueError("from_verl: the rollout has no assistant turn; nothing the policy did is logged")

    # the answer: the final assistant text; a run that ended on a tool call
    # (max turns, a parse failure) gets an empty answer step that says so
    answer_synthesized = False
    if steps[-1]["type"] == "reason":
        steps[-1]["type"] = "answer"
        steps[-1]["name"] = "final"
        steps[-1]["output"] = steps[-1]["input"]
    else:
        step = _new_step(steps, "answer", "final", "", "")
        step["note"] = "no final assistant text: the rollout ended after a tool call"
        answer_synthesized = True
        warnings.append("rollout ended after a tool call with no final assistant text; an empty answer step was added")
        turn_last_step[-1] = step["index"]
    answer_text = steps[-1]["output"]

    # rewards
    episode, per_turn, per_tool, terms = _verl_reward_terms(record, counters["assistant_turns"], warnings)
    reward_basis = "none"
    if per_turn:
        reward_basis = "per_turn"
        for turn, value in zip(turn_last_step, per_turn):
            if turn is not None:
                steps[turn]["reward"] = value
    if per_tool:
        reward_basis = "per_tool" if reward_basis == "none" else reward_basis + "+per_tool"
        if len(per_tool) != len(tool_steps):
            warnings.append(f"{terms.get('per_tool_key')} has {len(per_tool)} entries for {len(tool_steps)} tool call(s); "
                            f"assigned in order")
        for idx, value in zip(tool_steps, per_tool):
            prior = steps[idx].get("reward")
            steps[idx]["reward"] = value if prior is None else prior + value
    if reward_basis == "none" and episode is not None:
        reward_basis = "episode"
        steps[-1]["reward"] = episode
    if reward_basis == "none":
        warnings.append("no reward in the record (reward_score / reward / score / reward_scores / turn_scores); "
                        "returns will be shaped from the reading, not recorded")

    # success
    ground_truth = _verl_ground_truth(record)
    success_basis = "unknown"
    if episode is not None and episode in (0.0, 1.0):
        success, success_basis = episode == 1.0, "reward_0_1"
    elif ground_truth and answer_text:
        success, success_basis = _contains_normalised(answer_text, ground_truth), "ground_truth"
    elif isinstance(record.get("success"), bool):
        success, success_basis = record["success"], "record.success"
    elif episode is not None:
        success, success_basis = episode > 0, "reward_sign"
        warnings.append(f"success read as reward > 0 ({episode}); the reward is not 0/1 and no ground_truth was given")
    else:
        success = False
        warnings.append("success undetermined (no 0/1 reward, no ground_truth, no success field); recorded as false")

    # identity
    extra = record.get("extra_info") if isinstance(record.get("extra_info"), dict) else {}
    data_source = str(record.get("data_source") or extra.get("data_source") or "verl")
    uid = None
    for key in ("uid", "index", "id", "sample_index", "trajectory_id"):
        if record.get(key) is not None:
            uid = record[key]
            break
    if uid is None and extra:
        uid = extra.get("index", extra.get("uid"))
    task_id = f"{data_source}-{uid}" if uid is not None else data_source
    model = str(record.get("model") or record.get("policy") or "")
    agent_name = agent or model or str(record.get("agent_name") or record.get("agent") or "verl-policy")
    if not agent and not model and not (record.get("agent_name") or record.get("agent")):
        warnings.append("no model / policy / agent_name in the record; agent named 'verl-policy' (pass --agent)")

    # tokens: measured when the record carries ids, else estimated
    prompt_ids = record.get("prompt_ids")
    response_ids = record.get("response_ids")
    response_mask = record.get("response_mask")
    measured = isinstance(prompt_ids, list) or isinstance(response_ids, list)
    input_tokens = len(prompt_ids) if isinstance(prompt_ids, list) else _estimate_tokens("\n".join(prompt_parts))
    if isinstance(response_mask, list) and response_mask:
        output_tokens = int(sum(1 for m in response_mask if m))
    elif isinstance(response_ids, list):
        output_tokens = len(response_ids)
    else:
        output_tokens = sum(s["tokens"] for s in steps)

    num_turns = record.get("num_turns")
    if isinstance(num_turns, int) and num_turns != len(messages):
        warnings.append(f"num_turns={num_turns} but {len(messages)} message(s) were logged")

    tools = []
    for tool in record.get("tools") or []:
        if isinstance(tool, dict):
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
            if fn.get("name"):
                tools.append({"name": str(fn["name"])})
    budget: dict = {}
    for key in ("max_assistant_turns", "max_turns", "max_user_turns"):
        if _num(record.get(key)) is not None:
            budget[key] = record[key]

    fidelity = dict(counters)
    fidelity.update({"rewards": reward_basis, "success_basis": success_basis,
                     "answer_synthesized": answer_synthesized, "tokens": "measured" if measured else "estimated"})
    trajectory = {
        "schema_version": 1,
        "trace_id": f"{task_id}-{agent_name}",
        "agent": {"name": agent_name, "model": model, "version": "verl"},
        "task": {"id": task_id, "prompt": "\n".join(prompt_parts), "expected": ground_truth},
        "outcome": {"success": bool(success), "answer": answer_text, "score": episode,
                    "termination": "max_steps" if answer_synthesized and budget else None},
        "totals": {"input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
                   "cost_usd": 0.0, "latency_s": 0.0},
        "steps": steps,
        "tools": tools,
        "budget": budget,
        "token_accounting": {"basis": "measured" if measured else "estimated", "estimator": "len/4",
                             "measured_steps": 0, "estimated_steps": len(steps)},
        "source": {"format": "verl", "data_source": data_source, "uid": uid, "num_turns": num_turns,
                   "reward_terms": terms, "fidelity": fidelity},
    }
    Trajectory.from_json(trajectory)  # validate before handing back
    return trajectory, warnings


# ---------------------------------------------------------------- Agent Lightning

#: span names Agent Lightning reserves (its semantic conventions).
_AGL_REWARD_SPANS = ("agentlightning.reward", "agentlightning.annotation")
_AGL_OPERATION_SPAN = "agentlightning.operation"
#: attribute keys that carry a reward value, most specific first.
_AGL_REWARD_KEYS = ("agentlightning.reward.value", "reward.value", "reward", "value")
_AGL_INPUT_KEYS = _TOOL_INPUT_KEYS + ("agentlightning.operation.input",)
_AGL_OUTPUT_KEYS = _TOOL_OUTPUT_KEYS + ("agentlightning.operation.output",)
_AGL_AGENT_KEYS = ("agent.name", "gen_ai.agent.name", "agentlightning.agent.name")


def _agl_reward_of(span: dict, attrs: dict) -> Optional[float]:
    """The reward a span carries, or None when it is not a reward span."""
    name = str(span.get("name") or "")
    if name == _AGL_OPERATION_SPAN and str(attrs.get("agentlightning.operation.name") or "") != "reward":
        return None
    if attrs.get("type") == "reward" and _num(attrs.get("value")) is not None:
        return _num(attrs["value"])
    if name in _AGL_REWARD_SPANS or name == _AGL_OPERATION_SPAN or "reward" in name.lower():
        for key in _AGL_REWARD_KEYS + ("agentlightning.operation.output",):
            val = _num(attrs.get(key))
            if val is not None:
                return val
        for key, val in attrs.items():
            if str(key).startswith("agentlightning.reward") and _num(val) is not None:
                return _num(val)
    return None


def _agl_messages(attrs: dict, prefix: str, keys: tuple) -> list:
    """Messages from a JSON/list attribute or from indexed ``prefix.N.*`` keys
    (the agentops / OpenLLMetry spelling: ``gen_ai.completion.0.content``)."""
    import json as _json
    for key in keys:
        val = attrs.get(key)
        if isinstance(val, str) and val.strip().startswith(("[", "{")):
            try:
                val = _json.loads(val)
            except ValueError:
                continue
        if isinstance(val, dict):
            val = [val]
        if isinstance(val, list) and val and all(isinstance(m, dict) for m in val):
            return val
    indexed: dict[int, dict] = {}
    for key, val in attrs.items():
        key = str(key)
        if not key.startswith(prefix + "."):
            continue
        rest = key[len(prefix) + 1:].split(".")
        if not rest or not rest[0].isdigit():
            continue
        msg = indexed.setdefault(int(rest[0]), {})
        if len(rest) == 2:
            msg[rest[1]] = val
        elif len(rest) >= 4 and rest[1] == "tool_calls" and rest[2].isdigit():
            calls = msg.setdefault("tool_calls", {})
            calls.setdefault(int(rest[2]), {})[".".join(rest[3:])] = val
    out = []
    for i in sorted(indexed):
        msg = indexed[i]
        calls = msg.get("tool_calls")
        if isinstance(calls, dict):
            msg["tool_calls"] = [{"id": c.get("id", ""), "function": {"name": c.get("name", c.get("function.name", "tool")),
                                  "arguments": c.get("arguments", c.get("function.arguments", ""))}}
                                 for _, c in sorted(calls.items())]
        out.append(msg)
    return out


def _agl_events_to_spans(events: list, warnings: list) -> list:
    """Agent Lightning v1 events (``model_request`` / ``reward``) as spans, so
    one pipeline reads both exports."""
    spans = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("event_type") or ev.get("type") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        ts = _num(ev.get("timestamp")) or 0.0
        if kind == "model_request":
            req = data.get("request") if isinstance(data.get("request"), dict) else {}
            resp = data.get("response") if isinstance(data.get("response"), dict) else {}
            choices = resp.get("choices") if isinstance(resp.get("choices"), list) else []
            out_msgs = [c.get("message") for c in choices if isinstance(c, dict) and isinstance(c.get("message"), dict)]
            latency = (_num(data.get("latency_ms")) or 0.0) / 1000.0
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else (resp.get("usage") or {})
            spans.append({"name": "openai.chat.completion", "sequence_id": i, "start_time": ts, "end_time": ts + latency,
                          "attributes": {"gen_ai.request.model": data.get("model") or req.get("model") or "",
                                         "gen_ai.input.messages": req.get("messages") or [],
                                         "gen_ai.output.messages": out_msgs,
                                         "gen_ai.usage.input_tokens": (usage or {}).get("prompt_tokens", 0),
                                         "gen_ai.usage.output_tokens": (usage or {}).get("completion_tokens", 0)}})
        elif kind == "reward":
            spans.append({"name": "agentlightning.reward", "sequence_id": i, "start_time": ts, "end_time": ts,
                          "attributes": {"agentlightning.reward.value": data.get("value"),
                                         "agentlightning.reward.name": data.get("name") or data.get("source") or ""}})
        else:
            warnings.append(f"event {kind!r} at position {i} not mapped")
    return spans


def from_agent_lightning(record: Union[dict, list], agent: Optional[str] = None) -> tuple[dict, list[str]]:
    """Convert an Agent Lightning rollout export to a SCHEMA trajectory.

    Accepts the span export of the LightningStore (a list of spans, or
    ``{"spans": [...]}`` with optional ``rollout_id`` / ``input`` / ``agent``)
    and the v1 event export (``{"events": [...]}`` of ``model_request`` and
    ``reward`` events, optional ``rollout``).  Spans are ordered by
    ``sequence_id`` (else ``start_time``).

    Assumptions, kept conservative: an LLM call is a span named like
    ``openai.chat.completion`` or carrying ``gen_ai.request.model`` /
    a chat ``gen_ai.operation.name``; its output messages (``gen_ai.output.messages``,
    ``gen_ai.completion`` or indexed ``gen_ai.completion.N.*`` keys) give assistant
    text (a ``reason`` step) and tool calls (tool-ish steps awaiting a result);
    an ``execute_tool`` / ``agentops.span.kind = tool`` / ``gen_ai.tool.name``
    span fills the pending step of the same tool name, else stands as its own
    step; a reward span (``agentlightning.reward`` / ``.annotation``, or an
    ``agentlightning.operation`` named ``reward``) pays the most recent step
    (Agent Lightning's own first-occurrence match), and the last reward is
    the episode score.  Agent spans name the agent; other spans (traced
    functions, virtual roots) are counted and skipped.  The last LLM step is
    the answer; success is the final reward when it is 0/1, else the record's
    ``success``, else reward > 0 (warned).  Returns ``(trajectory, warnings)``
    with ``source.format = "agent-lightning"`` and fidelity counters.
    """
    warnings: list[str] = []
    meta: dict = {}
    if isinstance(record, dict):
        meta = record
        if isinstance(record.get("spans"), list):
            spans = record["spans"]
        elif isinstance(record.get("events"), list):
            spans = _agl_events_to_spans(record["events"], warnings)
        elif isinstance(record.get("trace"), list):
            spans = record["trace"]
        else:
            raise ValueError("from_agent_lightning: expected 'spans' (a span list) or 'events' "
                             "(model_request / reward events)")
    elif isinstance(record, list):
        if record and all(isinstance(s, dict) and ("event_type" in s or "event" in s) for s in record):
            spans = _agl_events_to_spans(record, warnings)
        else:
            spans = record
    else:
        raise ValueError(f"from_agent_lightning: a rollout must be a JSON object or a span list, "
                         f"got {type(record).__name__}")
    spans = [s for s in spans if isinstance(s, dict)]
    if not spans:
        raise ValueError("from_agent_lightning: no spans or events to map")

    def order_key(span: dict) -> tuple:
        seq = _num(span.get("sequence_id"))
        return (0 if seq is not None else 1, seq if seq is not None else 0.0,
                _num(span.get("start_time")) or 0.0, str(span.get("name") or ""))
    ordered = sorted(spans, key=order_key)

    steps: list[dict] = []
    pending: list[dict] = []
    counters = {"spans": len(ordered), "llm_calls": 0, "tool_calls": 0, "tool_spans": 0,
                "tool_results_paired": 0, "reward_spans": 0, "agent_spans": 0, "spans_skipped": 0}
    rewards_seen: list[float] = []
    agent_from_spans: Optional[str] = None
    model_from_spans = ""
    prompt_text = ""
    total_in = total_out = 0
    for span in ordered:
        name = str(span.get("name") or "")
        attrs = _span_attrs(span)
        start = _num(span.get("start_time")) or 0.0
        end = _num(span.get("end_time")) or start
        latency = round(max(0.0, end - start), 4)
        kind = str(attrs.get("agentops.span.kind") or attrs.get("span.kind") or "").lower()
        op = str(attrs.get("gen_ai.operation.name") or "").lower()

        reward = _agl_reward_of(span, attrs)
        if reward is not None:
            counters["reward_spans"] += 1
            rewards_seen.append(reward)
            if steps:
                prior = steps[-1].get("reward")
                steps[-1]["reward"] = reward if prior is None else prior + reward
            else:
                warnings.append(f"reward {reward} recorded before any LLM call; kept as the episode score only")
            continue
        for key in _AGL_AGENT_KEYS:
            if attrs.get(key) and not agent_from_spans:
                agent_from_spans = str(attrs[key])
        if kind == "agent" and not agent_from_spans:
            agent_from_spans = str(attrs.get("operation.name") or name or "")
        if kind == "agent" or op in _AGENT_OPS:
            counters["agent_spans"] += 1
            continue

        is_llm = (op in _CHAT_OPS or kind == "llm" or bool(attrs.get("gen_ai.request.model"))
                  or "chat.completion" in name.lower() or name.lower().startswith(("chat", "llm")))
        is_tool = (op == "execute_tool" or kind == "tool" or bool(attrs.get("gen_ai.tool.name"))
                   or name.lower().startswith("execute_tool"))
        if is_tool and not (op in _CHAT_OPS):
            counters["tool_spans"] += 1
            tool = str(attrs.get("gen_ai.tool.name") or attrs.get("tool.name") or attrs.get("operation.name")
                       or name.split(" ", 1)[-1] or "tool")
            inp = _first_attr(attrs, _AGL_INPUT_KEYS)
            out = _first_attr(attrs, _AGL_OUTPUT_KEYS)
            match = next((p for p in pending if p["name"] == tool), None)
            if match is not None:
                pending.remove(match)
                match["output"] = out
                match["tokens"] += _estimate_tokens(out)
                match["latency_s"] = latency
                if inp and not match["input"]:
                    match["input"] = inp
                counters["tool_results_paired"] += 1
            else:
                _new_step(steps, _type_from_tool_name(tool), tool, inp or name, out, latency)
            continue
        if is_llm:
            counters["llm_calls"] += 1
            model_from_spans = model_from_spans or str(attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model") or "")
            tok_in = int(_num(attrs.get("gen_ai.usage.input_tokens")) or 0)
            tok_out = int(_num(attrs.get("gen_ai.usage.output_tokens")) or 0)
            total_in += tok_in
            total_out += tok_out
            in_msgs = _agl_messages(attrs, "gen_ai.prompt", ("gen_ai.input.messages", "gen_ai.prompt", "agentlightning.operation.input"))
            if not prompt_text:
                users = [_text_of(m.get("content")) for m in in_msgs if m.get("role") in ("system", "user")]
                prompt_text = "\n".join(u for u in users if u) or _first_attr(attrs, _PROMPT_KEYS)
            out_msgs = _agl_messages(attrs, "gen_ai.completion", ("gen_ai.output.messages", "gen_ai.completion", "agentlightning.operation.output"))
            text = "\n".join(_text_of(m.get("content")) for m in out_msgs if _text_of(m.get("content")))
            if not out_msgs:
                text = _first_attr(attrs, _COMPLETION_KEYS)
            calls = [c for m in out_msgs for c in (m.get("tool_calls") or []) if isinstance(c, dict)]
            if text:
                _new_step(steps, "reason", "reason", text, "", latency if not calls else 0.0,
                          tokens=(tok_in + tok_out) if (tok_in or tok_out) and not calls else None)
            for call in calls:
                _, tool, args = _call_parts(call)
                step = _new_step(steps, _type_from_tool_name(tool), tool, args, "", latency,
                                 tokens=(tok_in + tok_out) if (tok_in or tok_out) else None)
                counters["tool_calls"] += 1
                pending.append(step)
            if not text and not calls:
                warnings.append(f"LLM span {name!r} at sequence {span.get('sequence_id')} carries no output text or tool call")
            continue
        counters["spans_skipped"] += 1
    if not steps:
        raise ValueError("from_agent_lightning: no LLM call or tool span could be mapped to a step")

    answer_synthesized = False
    if steps[-1]["type"] == "reason":
        steps[-1].update({"type": "answer", "name": "final", "output": steps[-1]["input"]})
    else:
        step = _new_step(steps, "answer", "final", "", "")
        step["note"] = "no final assistant text: the rollout ended after a tool call"
        answer_synthesized = True
        warnings.append("rollout ended after a tool call with no final assistant text; an empty answer step was added")
        if rewards_seen and steps[-2].get("reward") is not None:
            step["reward"], steps[-2]["reward"] = steps[-2]["reward"], None
    answer_text = steps[-1]["output"]
    final_reward = rewards_seen[-1] if rewards_seen else None
    if not rewards_seen:
        warnings.append("no reward span or event; returns will be shaped from the reading, not recorded")

    success_basis = "unknown"
    if final_reward is not None and final_reward in (0.0, 1.0):
        success, success_basis = final_reward == 1.0, "reward_0_1"
    elif isinstance(meta.get("success"), bool):
        success, success_basis = meta["success"], "record.success"
    elif final_reward is not None:
        success, success_basis = final_reward > 0, "reward_sign"
        warnings.append(f"success read as reward > 0 ({final_reward}); the reward is not 0/1")
    else:
        success = False
        warnings.append("success undetermined (no reward, no success field); recorded as false")

    rollout = meta.get("rollout") if isinstance(meta.get("rollout"), dict) else {}
    rollout_id = meta.get("rollout_id") or rollout.get("rollout_id") or next(
        (s.get("rollout_id") for s in ordered if s.get("rollout_id")), None)
    task_id = f"agl-{rollout_id}" if rollout_id else "agent-lightning-rollout"
    task_input = meta.get("input", rollout.get("input"))
    prompt = _text_of(task_input) if task_input is not None else prompt_text
    if not prompt:
        warnings.append("no rollout input and no user message in the LLM spans; task prompt is empty")
    agent_name = agent or str(meta.get("agent") or meta.get("agent_name") or agent_from_spans or model_from_spans
                              or "agent-lightning-agent")
    fidelity = dict(counters)
    fidelity.update({"rewards": "recorded" if rewards_seen else "none", "success_basis": success_basis,
                     "answer_synthesized": answer_synthesized,
                     "tokens": "measured" if (total_in or total_out) else "estimated"})
    trajectory = {
        "schema_version": 1,
        "trace_id": f"{task_id}-{agent_name}",
        "agent": {"name": agent_name, "model": model_from_spans, "version": "agent-lightning"},
        "task": {"id": task_id, "prompt": prompt, "expected": None},
        "outcome": {"success": bool(success), "answer": answer_text, "score": final_reward, "termination": None},
        "totals": {"input_tokens": total_in or _estimate_tokens(prompt),
                   "output_tokens": total_out or sum(s["tokens"] for s in steps),
                   "cost_usd": 0.0, "latency_s": round(sum(s["latency_s"] for s in steps), 4)},
        "steps": steps,
        "token_accounting": {"basis": "measured" if (total_in or total_out) else "estimated", "estimator": "len/4"},
        "source": {"format": "agent-lightning", "rollout_id": rollout_id, "rewards": rewards_seen, "fidelity": fidelity},
    }
    Trajectory.from_json(trajectory)  # validate before handing back
    return trajectory, warnings


# ---------------------------------------------------------------- registry hooks

def detect_verl(data: Any) -> tuple[float, str]:
    """A veRL rollout: reward / turn scores next to a message history or a
    prompt+response pair, with the dataset columns a trainer keeps."""
    probe = data[0] if isinstance(data, list) and data else data
    if not isinstance(probe, dict):
        return 0.0, "not a JSON object (or a list of them)"
    reward_keys = [k for k in ("reward_scores", "reward_score", "turn_scores", "tool_rewards", "rm_score")
                   if k in probe]
    column_keys = [k for k in ("data_source", "reward_model", "extra_info", "num_turns", "uid", "response_mask")
                   if k in probe]
    has_messages = any(isinstance(probe.get(k), list) for k in _VERL_MESSAGE_KEYS)
    has_pair = ("response" in probe or "output" in probe) and ("prompt" in probe or "input" in probe or "raw_prompt" in probe)
    if not (has_messages or has_pair):
        return 0.0, "no messages / prompt+response"
    if reward_keys and (has_messages or column_keys):
        return 0.97, f"veRL rollout: {', '.join(reward_keys)} with {'messages' if has_messages else 'prompt+response'}"
    if column_keys:
        return 0.8, f"veRL dataset columns {', '.join(column_keys)} with {'messages' if has_messages else 'prompt+response'}"
    if has_pair and not has_messages and ("score" in probe or "gts" in probe):
        return 0.6, "rollout-dump columns (input/output with score/gts)"
    return 0.0, "messages without veRL reward or dataset columns"


def detect_agent_lightning(data: Any) -> tuple[float, str]:
    """Agent Lightning: spans named ``agentlightning.*`` or carrying
    ``agentlightning.`` attributes, or ``model_request`` / ``reward`` events."""
    spans = None
    if isinstance(data, dict):
        spans = data.get("spans") if isinstance(data.get("spans"), list) else data.get("events")
    elif isinstance(data, list):
        spans = data
    if not isinstance(spans, list) or not spans:
        return 0.0, "no spans / events"
    agl = events = 0
    for span in spans[:50]:
        if not isinstance(span, dict):
            continue
        if str(span.get("event_type") or "") in ("model_request", "reward"):
            events += 1
        name = str(span.get("name") or "")
        attrs = _span_attrs(span)
        if name.startswith("agentlightning.") or any(str(k).startswith("agentlightning.") for k in attrs) \
                or "rollout_id" in span or "attempt_id" in span:
            agl += 1
    if events:
        return 0.96, f"{events} Agent Lightning event(s) (model_request / reward)"
    if agl:
        return 0.96, f"{agl} span(s) with agentlightning.* names, attributes or rollout ids"
    return 0.0, "spans carry no agentlightning.* marks"


def _convert_verl(data: Any) -> tuple[dict, list[str]]:
    if isinstance(data, list):
        if len(data) != 1:
            raise ValueError(f"{len(data)} veRL rollouts in one payload; use `convert --format verl` on the "
                             f"file to write one trace per record, or call from_verl on the list")
        data = data[0]
    return from_verl(data)


def register_formats() -> None:
    """Register ``verl`` and ``agent-lightning`` with the format registry.

    Called from the CLI beside the Claude Code registration; idempotent.
    (The registry imports this module, so registration cannot happen at
    import time here without a cycle.)
    """
    from . import registry
    registry.register("verl", detect_verl, _convert_verl,
                      "veRL agent-loop rollout (messages or prompt+response with reward_score / "
                      "turn_scores, data_source, ground_truth)")
    registry.register("agent-lightning", detect_agent_lightning, from_agent_lightning,
                      "Agent Lightning rollout export (LightningStore spans or v1 model_request / reward events)")
