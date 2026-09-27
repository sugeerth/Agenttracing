"""Vendor coding agents as SCHEMA traces: Codex CLI and Claude Code, read the same way.

Two command-line agents, two event streams, one trace format — so the
same task run through both can be compared step for step by everything
else in this package.

**Codex CLI** (``codex exec --json``) prints one JSON object per line:
``thread.started``, ``turn.started``, then ``item.started`` /
``item.updated`` / ``item.completed`` around each item, and
``turn.completed`` with the turn's ``usage`` (``input_tokens``,
``cached_input_tokens``, ``cache_write_input_tokens``, ``output_tokens``,
``reasoning_output_tokens``) or ``turn.failed`` with an ``error``. An
item is an ``agent_message``, ``reasoning``, ``command_execution``
(``command``, ``aggregated_output``, ``exit_code``, ``status``),
``file_change`` (``changes[{path, kind: add|delete|update}]``),
``mcp_tool_call`` (``server``, ``tool``, ``arguments``, ``result``,
``error``), ``web_search`` (``query``), ``todo_list`` (``items``),
``collab_tool_call`` (a sub-agent spawned, messaged or closed) or
``error``. The names were read from the 0.157 binary rather than
remembered, and anything this module does not recognise is counted in
``source.unknown_events`` rather than dropped silently.

**Claude Code** (``claude -p … --output-format stream-json --verbose``)
prints ``system``/``init`` (model, tools, working directory), each
``assistant`` message (``text``, ``thinking`` and ``tool_use`` blocks,
with ``usage``), each ``user`` message carrying ``tool_result`` blocks,
and a closing ``result`` (``total_cost_usd``, ``duration_ms``,
``num_turns``, ``usage``, ``permission_denials``). One assistant message
can arrive as several lines sharing a ``message.id`` and one ``usage``;
it is counted once. A message whose ``parent_tool_use_id`` is set came
from a sub-agent, and its steps carry that span.

**Timing.** A stream read live is stamped as each line arrives: the
harness writes ``{"t": seconds, "e": event}`` lines, and every step then
has a real ``started_s`` and ``latency_s`` — a command's from its start
to its completion, a message's from the event before it. A bare vendor
stream (piped to a file with no stamps) converts too, with the timing
left unrecorded rather than invented.

**Tokens.** Claude Code reports usage per message, so steps carry
measured counts. Codex reports usage once per turn, so its steps carry
estimates labelled as estimates and only the totals are measured; the
trace's ``token_accounting`` says which. Cached input is a part of input
for both, and reasoning tokens a part of output.

**Cost.** Claude Code reports what it spent; Codex does not. A missing
cost is recorded as unreported — ``vendor.cost_basis`` — never as zero
dollars, and a price the operator supplies is labelled as theirs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from .record import estimate_tokens

__all__ = ["codex_to_trajectory", "claude_stream_to_trajectory", "read_events",
           "detect_vendor", "register_formats", "VENDORS"]

#: the vendors this module reads, and the stream each one prints
VENDORS = {
    "codex": {"stream": "codex exec --json", "usage": "per turn", "cost": "not reported"},
    "claude-code": {"stream": "claude -p --output-format stream-json --verbose",
                    "usage": "per message", "cost": "reported by the CLI"},
}

#: how much of a tool's output a step keeps; the raw stream keeps the rest
OUTPUT_CAP = 8000

UNGRADED = "ungraded: no check was run, so success is recorded as false"


def _stamped(events: Iterable) -> list:
    """``[(t or None, event)]`` from raw events or ``{"t", "e"}`` lines."""
    out = []
    for item in events:
        if isinstance(item, dict) and "e" in item and "t" in item and isinstance(item.get("e"), dict):
            t = item.get("t")
            out.append((float(t) if isinstance(t, (int, float)) else None, item["e"]))
        elif isinstance(item, dict):
            out.append((None, item))
    return out


def read_events(path: Union[str, Path]) -> list:
    """The JSON lines of a vendor stream (stamped or not); blank and
    unparseable lines are skipped and counted in the second element."""
    events, bad = [], 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            bad += 1
    return events, bad


def _cap(text: Any) -> str:
    s = text if isinstance(text, str) else ("" if text is None else json.dumps(text, ensure_ascii=False))
    return s if len(s) <= OUTPUT_CAP else s[:OUTPUT_CAP] + f"\n… ({len(s) - OUTPUT_CAP} more characters in the raw stream)"


def _step(index, stype, name, inp, out, *, t0=None, t1=None, error=None, effect=None,
          span=None, measured=None, note=None) -> dict:
    step = {"index": index, "type": stype, "name": name, "input": _cap(inp), "output": _cap(out),
            "tokens": 0, "latency_s": 0.0}
    if t0 is not None:
        step["started_s"] = round(t0, 3)
    if t0 is not None and t1 is not None:
        step["latency_s"] = round(max(0.0, t1 - t0), 3)
    if error is not None:
        step["error"] = bool(error)
    if effect:
        step["effect"] = effect
    if span:
        step["span"] = span
    if note:
        step["note"] = note
    if measured:
        step.update(measured)
    else:
        step["tokens"] = estimate_tokens(step["output"] or step["input"])
        step["tokens_basis"] = "estimated"
    return step


def _finish(steps: list, *, task: str, prompt: str, agent: str, model: str, version: str,
            run_id: Optional[str], success: Optional[bool], score: Optional[float],
            note: Optional[str], termination: str, answer: str, totals: dict,
            accounting: dict, source: dict, vendor: dict, expected: Optional[str]) -> dict:
    """Close a trace: the final message becomes the answer step."""
    if steps and steps[-1]["type"] == "reason" and steps[-1].get("name") == "message":
        last = steps.pop()
        answer = answer or last["output"]
    index = len(steps)
    steps.append({"index": index, "type": "answer", "name": "answer", "input": "",
                  "output": _cap(answer or "(no final message)"),
                  "tokens": estimate_tokens(answer or ""), "tokens_basis": "estimated",
                  "latency_s": 0.0})
    for i, s in enumerate(steps):
        s["index"] = i
    outcome = {"success": bool(success) if success is not None else False,
               "answer": _cap(answer or ""), "score": score if success is not None else None,
               "termination": termination}
    if success is None:
        outcome["note"] = note or UNGRADED
    elif note:
        outcome["note"] = note
    trace_id = f"{task}__{agent}" + (f"__{run_id}" if run_id else "")
    return {
        "schema_version": 1, "trace_id": trace_id, "run_id": run_id or "r1",
        "agent": {"name": agent, "model": model or "", "version": version or ""},
        "task": {"id": task, "prompt": prompt or "", "expected": expected},
        "outcome": outcome, "totals": totals, "steps": steps,
        "token_accounting": accounting, "source": source, "vendor": vendor,
    }


# ------------------------------------------------------------------ codex

def _codex_command(item: dict) -> str:
    cmd = item.get("command")
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd or "")


def codex_to_trajectory(events: Iterable, *, task: str, prompt: str = "", agent: str = "codex",
                        model: str = "", version: str = "", run_id: Optional[str] = None,
                        success: Optional[bool] = None, score: Optional[float] = None,
                        note: Optional[str] = None, expected: Optional[str] = None,
                        termination: Optional[str] = None, price: Optional[dict] = None) -> dict:
    """A ``codex exec --json`` stream as a SCHEMA trajectory.

    ``success`` is the harness's grade (a check command's exit status);
    None writes an ungraded run. ``termination`` overrides what the stream
    shows when the harness stopped the run (a budget, a timeout).
    ``price`` is ``{"input", "cached_input", "output"}`` in USD per
    million tokens, supplied by the operator; without it the cost is
    unreported.
    """
    stamped = _stamped(events)
    steps: list = []
    started: dict = {}               # item id -> start stamp
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
             "output_tokens": 0, "reasoning_output_tokens": 0}
    turns = failed = 0
    errors: list = []
    unknown: dict = {}
    thread_id = None
    last_t = None
    last_message = ""
    kinds: dict = {}
    for t, ev in stamped:
        kind = str(ev.get("type") or "")
        if kind == "thread.started":
            thread_id = ev.get("thread_id")
        elif kind == "turn.started":
            pass
        elif kind == "turn.completed":
            turns += 1
            u = ev.get("usage") or {}
            for key in usage:
                v = u.get(key)
                if isinstance(v, (int, float)):
                    usage[key] += int(v)
        elif kind == "turn.failed":
            failed += 1
            err = ev.get("error") or {}
            errors.append(str(err.get("message") if isinstance(err, dict) else err))
        elif kind == "error":
            errors.append(str(ev.get("message") or ev))
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = ev.get("item") or {}
            iid = str(item.get("id") or "")
            itype = str(item.get("type") or item.get("item_type") or "")
            if kind == "item.started":
                if iid and t is not None:
                    started.setdefault(iid, t)
                last_t = t if t is not None else last_t
                continue
            if kind == "item.updated":
                continue
            kinds[itype] = kinds.get(itype, 0) + 1
            t0 = started.get(iid, last_t if itype in ("reasoning", "agent_message") else t)
            t1 = t
            status = str(item.get("status") or "")
            if itype == "reasoning":
                steps.append(_step(len(steps), "reason", "reasoning", "", item.get("text") or "",
                                   t0=t0, t1=t1))
            elif itype == "agent_message":
                last_message = str(item.get("text") or "")
                steps.append(_step(len(steps), "reason", "message", "", last_message, t0=t0, t1=t1))
            elif itype == "command_execution":
                code = item.get("exit_code")
                bad = status == "failed" or (isinstance(code, int) and code != 0)
                out = str(item.get("aggregated_output") or "")
                if isinstance(code, int):
                    out = (out + f"\n[exit {code}]").lstrip("\n")
                steps.append(_step(len(steps), "tool_call", "shell", _codex_command(item), out,
                                   t0=t0, t1=t1, error=bad))
            elif itype == "file_change":
                changes = item.get("changes") or []
                said = "; ".join(f"{c.get('kind', 'update')} {c.get('path', '?')}" for c in changes
                                 if isinstance(c, dict))
                steps.append(_step(len(steps), "tool_call", "apply_patch",
                                   json.dumps(changes, ensure_ascii=False), said or status,
                                   t0=t0, t1=t1, error=status == "failed", effect="write"))
            elif itype == "mcp_tool_call":
                name = f"{item.get('server') or 'mcp'}.{item.get('tool') or 'tool'}"
                err = item.get("error")
                steps.append(_step(len(steps), "tool_call", name, item.get("arguments") or "",
                                   item.get("result") if err is None else err,
                                   t0=t0, t1=t1, error=bool(err) or status == "failed"))
            elif itype == "web_search":
                steps.append(_step(len(steps), "search", "web_search", item.get("query") or "",
                                   item.get("action") or "", t0=t0, t1=t1))
            elif itype == "todo_list":
                items = item.get("items") or []
                text = "\n".join(f"[{'x' if i.get('completed') else ' '}] {i.get('text', '')}"
                                 for i in items if isinstance(i, dict))
                steps.append(_step(len(steps), "plan", "todo_list", "", text, t0=t0, t1=t1))
            elif itype == "collab_tool_call":
                steps.append(_step(len(steps), "tool_call", str(item.get("tool") or "collab"),
                                   item.get("prompt") or "", item.get("agents_states") or status,
                                   t0=t0, t1=t1, error=status == "failed",
                                   span={"id": iid, "agent": "sub-agent"}))
            elif itype == "error":
                errors.append(str(item.get("message") or ""))
                steps.append(_step(len(steps), "reason", "error", "", item.get("message") or "",
                                   t0=t0, t1=t1, error=True))
            else:
                unknown[itype or "?"] = unknown.get(itype or "?", 0) + 1
        else:
            unknown[kind or "?"] = unknown.get(kind or "?", 0) + 1
        if t is not None:
            last_t = t

    if termination is None:
        if failed or (errors and not last_message):
            auth = any(w in " ".join(errors).lower() for w in ("unauthorized", "api key", "401", "403", "login"))
            termination = "infrastructure_error" if auth else "agent_error"
        else:
            termination = "agent_stop"
    total_in, total_out = usage["input_tokens"], usage["output_tokens"]
    cost, cost_basis = 0.0, "not reported by the CLI"
    if price:
        fresh = max(0, total_in - usage["cached_input_tokens"])
        cost = (fresh * float(price.get("input", 0)) + usage["cached_input_tokens"] *
                float(price.get("cached_input", price.get("input", 0))) +
                total_out * float(price.get("output", 0))) / 1e6
        cost_basis = "computed from the prices the operator gave"
    wall = max((t for t, _ in stamped if t is not None), default=0.0)
    totals = {"input_tokens": total_in, "output_tokens": total_out, "cost_usd": round(cost, 6),
              "latency_s": round(wall, 3)}
    measured = bool(turns and (total_in or total_out))
    accounting = {"basis": "measured" if measured else "estimated",
                  "per_step": "estimated from text length (len/4): the CLI reports usage per turn, not per item",
                  "cached_input_tokens": usage["cached_input_tokens"],
                  "cache_write_input_tokens": usage["cache_write_input_tokens"],
                  "reasoning_output_tokens": usage["reasoning_output_tokens"]}
    vendor = {"name": "codex", "thread_id": thread_id, "turns": turns, "usage": usage,
              "cost_usd": round(cost, 6) if price else None, "cost_basis": cost_basis,
              "errors": errors[:10], "item_kinds": kinds}
    source = {"format": "codex-exec-json", "events": len(stamped), "unknown_events": unknown,
              "timed": any(t is not None for t, _ in stamped)}
    if errors and not note and success is None:
        note = "the CLI reported: " + errors[0][:300]
    return _finish(steps, task=task, prompt=prompt, agent=agent, model=model, version=version,
                   run_id=run_id, success=success, score=score, note=note, termination=termination,
                   answer=last_message, totals=totals, accounting=accounting, source=source,
                   vendor=vendor, expected=expected)


# ------------------------------------------------------------ claude code

#: Claude Code tools by what they do, so the two vendors' steps line up:
#: a read is a read whichever CLI made it
_CLAUDE_TYPES = {"Read": ("read", "read"), "WebSearch": ("search", None),
                 "WebFetch": ("retrieve", "read"), "TodoWrite": ("plan", None)}
_CLAUDE_WRITES = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_CLAUDE_READS = {"Grep", "Glob", "LS"}


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(str(b.get("text", "")))
                elif b.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def claude_stream_to_trajectory(events: Iterable, *, task: str, prompt: str = "",
                                agent: str = "claude-code", model: str = "", version: str = "",
                                run_id: Optional[str] = None, success: Optional[bool] = None,
                                score: Optional[float] = None, note: Optional[str] = None,
                                expected: Optional[str] = None,
                                termination: Optional[str] = None) -> dict:
    """A ``claude -p --output-format stream-json`` stream as a SCHEMA trajectory."""
    stamped = _stamped(events)
    steps: list = []
    pending: dict = {}           # tool_use id -> (step, t0)
    by_message: dict = {}        # message id -> {"usage", "steps": [step]}
    order: list = []
    last_t = None
    result: dict = {}
    init: dict = {}
    unknown: dict = {}
    last_text = ""
    for t, ev in stamped:
        kind = str(ev.get("type") or "")
        if kind == "system":
            if ev.get("subtype") == "init":
                init = ev
                model = model or str(ev.get("model") or "")
        elif kind == "assistant":
            msg = ev.get("message") or {}
            mid = str(msg.get("id") or f"m{len(order)}")
            if mid not in by_message:
                by_message[mid] = {"usage": None, "steps": []}
                order.append(mid)
            if isinstance(msg.get("usage"), dict):
                by_message[mid]["usage"] = msg["usage"]
            model = model or str(msg.get("model") or "")
            parent = ev.get("parent_tool_use_id")
            span = {"id": str(parent), "agent": "sub-agent"} if parent else None
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in ("text", "thinking"):
                    text = str(block.get("text") or block.get("thinking") or "").strip()
                    if not text:
                        continue
                    name = "message" if btype == "text" else "thinking"
                    if btype == "text" and not parent:
                        last_text = text
                    step = _step(len(steps), "reason", name, "", text, t0=last_t, t1=t, span=span)
                    steps.append(step)
                    by_message[mid]["steps"].append(step)
                elif btype == "tool_use":
                    name = str(block.get("name") or "tool")
                    stype, effect = _CLAUDE_TYPES.get(name, ("tool_call", None))
                    if name in _CLAUDE_WRITES:
                        effect = "write"
                    elif name in _CLAUDE_READS:
                        effect = "read"
                    step = _step(len(steps), stype, name, block.get("input") or {}, "",
                                 t0=t, effect=effect, span=span)
                    steps.append(step)
                    by_message[mid]["steps"].append(step)
                    pending[str(block.get("id") or "")] = (step, t)
        elif kind == "user":
            msg = ev.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        got = pending.pop(str(block.get("tool_use_id") or ""), None)
                        if got is None:
                            continue
                        step, t0 = got
                        step["output"] = _cap(_block_text(block.get("content")))
                        if block.get("is_error"):
                            step["error"] = True
                        if t0 is not None and t is not None:
                            step["latency_s"] = round(max(0.0, t - t0), 3)
                        if step.get("tokens_basis") == "estimated":
                            step["tokens"] = estimate_tokens(step["output"] or step["input"])
            elif isinstance(content, str) and not prompt:
                prompt = content
        elif kind == "result":
            result = ev
        elif kind in ("stream_event",):
            pass
        else:
            unknown[kind or "?"] = unknown.get(kind or "?", 0) + 1
        if t is not None:
            last_t = t

    # usage once per message, on that message's last step
    per_msg_in = per_msg_out = per_msg_cached = 0
    for mid in order:
        u = by_message[mid]["usage"]
        msteps = by_message[mid]["steps"]
        if not isinstance(u, dict) or not msteps:
            continue
        fresh = int(u.get("input_tokens") or 0)
        created = int(u.get("cache_creation_input_tokens") or 0)
        read = int(u.get("cache_read_input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        per_msg_in += fresh + created + read
        per_msg_out += out
        per_msg_cached += read
        for s in msteps:
            s["tokens"], s["tokens_basis"] = 0, None
        last = msteps[-1]
        last.update({"tokens": fresh + created + read + out, "tokens_basis": "measured",
                     "input_tokens": fresh + created + read, "output_tokens": out,
                     "cached_tokens": read})

    ru = result.get("usage") if isinstance(result.get("usage"), dict) else None
    if ru:
        total_in = int(ru.get("input_tokens") or 0) + int(ru.get("cache_creation_input_tokens") or 0) + \
            int(ru.get("cache_read_input_tokens") or 0)
        total_out = int(ru.get("output_tokens") or 0)
        cached = int(ru.get("cache_read_input_tokens") or 0)
        written = int(ru.get("cache_creation_input_tokens") or 0)
        basis = "measured"
    else:
        total_in, total_out, cached, written = per_msg_in, per_msg_out, per_msg_cached, 0
        basis = "measured" if (per_msg_in or per_msg_out) else "estimated"
    cost = result.get("total_cost_usd")
    wall = (float(result["duration_ms"]) / 1000.0) if isinstance(result.get("duration_ms"), (int, float)) \
        else max((t for t, _ in stamped if t is not None), default=0.0)
    totals = {"input_tokens": total_in, "output_tokens": total_out,
              "cost_usd": round(float(cost), 6) if isinstance(cost, (int, float)) else 0.0,
              "latency_s": round(wall, 3)}
    subtype = str(result.get("subtype") or "")
    if termination is None:
        if not result:
            termination = "agent_error"
        elif subtype == "error_max_turns":
            termination = "max_steps"
        elif result.get("is_error") or subtype.startswith("error"):
            text = str(result.get("result") or "").lower()
            termination = "infrastructure_error" if any(
                w in text for w in ("api key", "401", "403", "authentication", "credit")) else "agent_error"
        else:
            termination = "agent_stop"
    answer = str(result.get("result") or last_text or "")
    accounting = {"basis": basis, "per_step": "measured per message, on the message's last step",
                  "cached_input_tokens": cached, "cache_write_input_tokens": written}
    denials = result.get("permission_denials") or []
    vendor = {"name": "claude-code", "session_id": result.get("session_id") or init.get("session_id"),
              "turns": result.get("num_turns"), "usage": ru or {},
              "cost_usd": round(float(cost), 6) if isinstance(cost, (int, float)) else None,
              "cost_basis": "reported by the CLI" if isinstance(cost, (int, float)) else "not reported",
              "duration_api_s": (float(result["duration_api_ms"]) / 1000.0)
              if isinstance(result.get("duration_api_ms"), (int, float)) else None,
              "permission_denials": len(denials) if isinstance(denials, list) else 0,
              "tools_offered": len(init.get("tools") or []), "subtype": subtype or None,
              "unanswered_tool_calls": len(pending)}
    source = {"format": "claude-code-stream-json", "events": len(stamped), "unknown_events": unknown,
              "timed": any(t is not None for t, _ in stamped)}
    return _finish(steps, task=task, prompt=prompt, agent=agent, model=model, version=version,
                   run_id=run_id, success=success, score=score, note=note, termination=termination,
                   answer=answer, totals=totals, accounting=accounting, source=source, vendor=vendor,
                   expected=expected)


# --------------------------------------------------------------- detection

def detect_vendor(events: list) -> tuple:
    """``(vendor or None, reason)`` from the first events of a stream."""
    head = [e for _, e in _stamped(events[:40])]
    kinds = {str(e.get("type") or "") for e in head}
    if kinds & {"thread.started", "turn.started", "item.started", "item.completed", "turn.completed"}:
        return "codex", "thread/turn/item events"
    if any(e.get("type") == "system" and e.get("subtype") == "init" for e in head) or \
            any(e.get("type") == "result" and "total_cost_usd" in e for e in head):
        return "claude-code", "a system/init or result event"
    if kinds & {"assistant", "user"} and any("session_id" in e for e in head):
        return "claude-code", "assistant/user events with a session id"
    return None, "neither a Codex nor a Claude Code event stream"


def _detect(kind: str):
    def detect(data: Any) -> tuple:
        if not isinstance(data, list) or not data:
            return 0.0, "not a list of events"
        vendor, why = detect_vendor(data)
        return (0.95, why) if vendor == kind else (0.0, why)
    return detect


def _convert(kind: str):
    def convert(data: Any) -> tuple:
        fn = codex_to_trajectory if kind == "codex" else claude_stream_to_trajectory
        traj = fn(data, task=f"{kind}-session")
        warnings = ["no check was run: the run is ungraded (success recorded as false)"]
        unknown = traj["source"].get("unknown_events") or {}
        if unknown:
            warnings.append("events this reader does not know were counted, not converted: " +
                            ", ".join(f"{k} ×{v}" for k, v in sorted(unknown.items())))
        return traj, warnings
    return convert


def register_formats() -> None:
    from . import registry
    registry.register("codex-exec", _detect("codex"), _convert("codex"),
                      "Codex CLI event stream (`codex exec --json`: thread/turn/item JSON lines)")
    registry.register("claude-code-stream", _detect("claude-code"), _convert("claude-code"),
                      "Claude Code headless stream (`claude -p --output-format stream-json --verbose`)")
