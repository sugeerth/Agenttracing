"""Trace Claude Code — live through its hooks, and after the fact from
its transcript — as SCHEMA trajectories any other agent can be compared
with.

Live. Claude Code runs shell hooks around every tool call and at the end
of a turn, handing each a JSON payload on stdin (``hook_event_name``,
``tool_name``, ``tool_input``, ``tool_response``, ``transcript_path``,
``session_id`` …). ``python -m deepcompare hook --traces DIR --task ID``
is such a hook: every ``PostToolUse`` appends one ``tool_call`` step to
``DIR/<task>__<agent>.live.json`` — the file ``deepcompare watch`` draws
as it grows — and ``Stop`` writes the final trace from the transcript
(which also carries the assistant's text between calls, as ``reason``
steps, and the token counts) and removes the live file. Wire it once in
``.claude/settings.json``::

    {"hooks": {
      "PostToolUse": [{"matcher": "", "hooks": [{"type": "command",
        "command": "python -m deepcompare hook --traces traces --task my-task --agent claude-code"}]}],
      "Stop": [{"hooks": [{"type": "command",
        "command": "python -m deepcompare hook --traces traces --task my-task --agent claude-code --expected 'the answer'"}]}]}}

After the fact. A session's transcript is a JSONL file of ``user`` and
``assistant`` entries whose ``message.content`` holds ``text``,
``tool_use`` and ``tool_result`` blocks; :func:`transcript_to_trajectory`
turns it into a trace, and the ``claude-code`` format is registered with
the converter registry so ``deepcompare convert`` detects it.

Honesty. A run with no expected answer and no grader is written with
``outcome.success: false``, ``score: null`` and a note saying it is
ungraded — never a guessed success; the reading's validity block will
say so too. Token counts come from the transcript's ``usage`` when
present and are estimated otherwise, and the trace says which. Nothing
here talks to a network.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, Union

from .record import Recorder, estimate_tokens
from .trace import Trajectory

LIVE_SUFFIX = ".live.json"
UNGRADED = "ungraded: no expected answer and no grader were given, so success is recorded as false"


# ------------------------------------------------------------------ helpers

def _text_of(content: Any) -> str:
    """The text in a message content: a string, or the text blocks of a list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                kind = block.get("type")
                if kind == "text":
                    parts.append(str(block.get("text", "")))
                elif kind == "thinking":
                    parts.append(str(block.get("thinking", "")))
                elif kind == "tool_result":
                    parts.append(_text_of(block.get("content")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    if isinstance(block := content, dict):
        return _text_of(block.get("content") or block.get("text"))
    return str(content)


def _grade(answer: str, expected: Optional[str]) -> Optional[bool]:
    if not expected:
        return None
    return expected.strip().lower() in (answer or "").lower()


def _live_path(traces: Path, task: str, agent: str) -> Path:
    return traces / f"{task}__{agent}{LIVE_SUFFIX}"


def _final_path(traces: Path, task: str, agent: str) -> Path:
    return traces / f"{task}__{agent}.json"


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------ the transcript

def read_transcript(path: Union[str, Path]) -> list:
    """The transcript's entries, one per non-empty line; bad lines skipped."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def transcript_to_trajectory(entries: list, *, task: str, agent: str = "claude-code",
                             prompt: Optional[str] = None, expected: Optional[str] = None,
                             model: str = "", run_id: Optional[str] = None,
                             success: Optional[bool] = None) -> dict:
    """A Claude Code transcript (list of entries) as a SCHEMA trajectory.

    Assistant text before a tool call becomes a ``reason`` step; each
    ``tool_use`` becomes a ``tool_call`` step whose output is the matching
    ``tool_result``; the last assistant text is the ``answer``. Usage
    counts from the transcript are the steps' tokens when present.
    """
    first_prompt = prompt
    pending: dict = {}          # tool_use id -> step handle
    last_assistant_text = ""
    model_seen = model
    tool_steps = 0
    rec = Recorder(task=task, prompt=prompt or "(prompt from the transcript)", agent=agent,
                   model=model, expected=expected, run_id=run_id, out_dir=None,
                   trace_id=f"{task}__{agent}" + (f"__{run_id}" if run_id else ""))
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        message = entry.get("message") if isinstance(entry.get("message"), dict) else None
        content = message.get("content") if message else entry.get("content")
        if kind == "user":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        handle = pending.pop(str(block.get("tool_use_id", "")), None)
                        result = _text_of(block.get("content"))
                        is_error = bool(block.get("is_error"))
                        if handle is not None:
                            handle.observe(result, error=is_error or None)
            elif first_prompt is None:
                first_prompt = _text_of(content)
                rec.prompt = first_prompt or rec.prompt
        elif kind == "assistant":
            if message and message.get("model") and not model_seen:
                model_seen = str(message["model"])
                rec.model = model_seen
            usage = message.get("usage") if message else None
            out_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
            text_parts, tool_uses = [], []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif block.get("type") == "thinking":
                        text_parts.append(str(block.get("thinking", "")))
                    elif block.get("type") == "tool_use":
                        tool_uses.append(block)
            elif isinstance(content, str):
                text_parts.append(content)
            text = "\n".join(p for p in text_parts if p).strip()
            if tool_uses:
                if text:
                    rec.step("reason", "reason", "", text,
                             tokens=int(out_tokens) if isinstance(out_tokens, int) and len(tool_uses) == 0 else None)
                for block in tool_uses:
                    name = str(block.get("name") or "tool")
                    args = json.dumps(block.get("input", {}), ensure_ascii=False)
                    handle = rec.step("tool_call", name, args, "",
                                      tokens=int(out_tokens) if isinstance(out_tokens, int) and len(tool_uses) == 1 and not text else None)
                    pending[str(block.get("id", f"call_{tool_steps}"))] = handle
                    tool_steps += 1
            elif text:
                last_assistant_text = text
                rec.step("reason", "reason", "", text,
                         tokens=int(out_tokens) if isinstance(out_tokens, int) else None)
    # the last assistant text is the answer: turn its reason step into the answer
    steps = rec._steps
    if steps and steps[-1]["type"] == "reason" and steps[-1]["output"] == last_assistant_text and last_assistant_text:
        steps.pop()
    graded = _grade(last_assistant_text, expected) if success is None else success
    if graded is None:
        rec.answer(last_assistant_text or "(no final message)", success=False, score=None,
                   note=UNGRADED)
    else:
        rec.answer(last_assistant_text or "(no final message)", success=bool(graded))
    rec.terminate("agent_stop")
    rec.close()
    data = rec.to_dict()
    if graded is None:
        data["outcome"]["score"] = None
        data["outcome"]["note"] = UNGRADED
    data["source"] = {"format": "claude-code-transcript", "entries": len(entries)}
    return data


# ---------------------------------------------------------------- the hook

def hook_event(payload: dict, *, traces: Union[str, Path], task: str, agent: str = "claude-code",
               expected: Optional[str] = None, prompt: Optional[str] = None,
               now: Optional[float] = None) -> dict:
    """Handle one hook payload. Returns ``{"event", "action", "path"}``.

    ``PostToolUse`` appends a step to the live file; ``UserPromptSubmit``
    records the prompt; ``Stop`` (or ``SubagentStop``) writes the final
    trace from the transcript when one is named, else from the live steps,
    and removes the live file. Anything else is ignored.
    """
    traces = Path(traces)
    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    live = _live_path(traces, task, agent)
    now = time.time() if now is None else now

    def load_live() -> dict:
        if live.is_file():
            try:
                return json.loads(live.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {"schema_version": 1, "trace_id": f"{task}__{agent}", "run_id": "r1",
                "agent": {"name": agent, "model": "", "version": ""},
                "task": {"id": task, "prompt": prompt or "", "expected": expected},
                "outcome": {"success": False, "answer": "", "score": None, "termination": None},
                "totals": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0},
                "steps": [], "in_progress": True, "started_at": now,
                "session_id": payload.get("session_id"), "transcript_path": payload.get("transcript_path")}

    if event == "UserPromptSubmit":
        data = load_live()
        text = str(payload.get("prompt") or "")
        if text and not data["task"].get("prompt"):
            data["task"]["prompt"] = text
        data["updated_at"] = now
        _write_atomic(live, data)
        return {"event": event, "action": "prompt recorded", "path": str(live)}

    if event == "PostToolUse":
        data = load_live()
        name = str(payload.get("tool_name") or "tool")
        tool_input = payload.get("tool_input")
        response = payload.get("tool_response")
        args = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
        out = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False) if response is not None else ""
        last = data.get("last_at") or data.get("started_at") or now
        step = {"index": len(data["steps"]), "type": "tool_call", "name": name,
                "input": args[:4000], "output": out[:8000],
                "tokens": estimate_tokens(args + out), "latency_s": round(max(0.0, now - last), 3),
                "tokens_basis": "estimated"}
        data["steps"].append(step)
        data["last_at"] = now
        data["updated_at"] = now
        data["totals"]["output_tokens"] = sum(s.get("tokens", 0) for s in data["steps"])
        data["totals"]["latency_s"] = round(sum(s.get("latency_s", 0) for s in data["steps"]), 3)
        _write_atomic(live, data)
        return {"event": event, "action": f"step {step['index']} recorded", "path": str(live)}

    if event in ("Stop", "SubagentStop", "SessionEnd"):
        data = load_live()
        transcript = payload.get("transcript_path") or data.get("transcript_path")
        final: Optional[dict] = None
        if transcript and Path(transcript).is_file():
            try:
                final = transcript_to_trajectory(read_transcript(transcript), task=task, agent=agent,
                                                 prompt=data["task"].get("prompt") or prompt, expected=expected)
            except Exception as exc:  # noqa: BLE001 — fall back to what the hooks saw
                final = None
                data["note"] = f"transcript could not be read ({exc}); trace built from hook events"
        if final is None:
            rec = Recorder(task=task, prompt=data["task"].get("prompt") or prompt or "(no prompt recorded)",
                           agent=agent, expected=expected, out_dir=None, trace_id=f"{task}__{agent}")
            for s in data["steps"]:
                rec.step("tool_call", s["name"], s["input"], s["output"], latency_s=s.get("latency_s"))
            answer = str(payload.get("last_assistant_message") or "")
            graded = _grade(answer, expected)
            rec.answer(answer or "(no final message recorded by the hooks)", success=bool(graded) if graded is not None else False,
                       score=None if graded is None else None, note=None if graded is not None else UNGRADED)
            rec.terminate("agent_stop")
            rec.close()
            final = rec.to_dict()
            if graded is None:
                final["outcome"]["score"] = None
                final["outcome"]["note"] = UNGRADED
            final["source"] = {"format": "claude-code-hooks", "steps": len(data["steps"])}
        Trajectory.from_dict(final)   # validate before it lands beside other traces
        path = _final_path(traces, task, agent)
        _write_atomic(path, final)
        try:
            live.unlink()
        except OSError:
            pass
        return {"event": event, "action": "final trace written", "path": str(path)}

    return {"event": event, "action": "ignored", "path": str(live)}


def main_hook(argv: list) -> int:
    """``python -m deepcompare hook`` — stdin is the hook payload."""
    import argparse
    parser = argparse.ArgumentParser(prog="deepcompare hook")
    parser.add_argument("--traces", default="traces")
    parser.add_argument("--task", required=True)
    parser.add_argument("--agent", default="claude-code")
    parser.add_argument("--expected", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    result = hook_event(payload, traces=args.traces, task=args.task, agent=args.agent,
                        expected=args.expected, prompt=args.prompt)
    if args.db and result.get("action") == "final trace written":
        from .tracedb import TraceDB
        try:
            with TraceDB(args.db) as db:
                db.add(json.loads(Path(result["path"]).read_text(encoding="utf-8")), source="hook")
            result["db"] = args.db
        except Exception as exc:  # noqa: BLE001 — the trace file is already safe on disk
            result["db_error"] = str(exc)
    print(json.dumps(result))
    return 0


# ------------------------------------------------------- registry format

def detect_claude_code(data: Any) -> tuple:
    entries = data if isinstance(data, list) else (data.get("transcript") if isinstance(data, dict) else None)
    if not isinstance(entries, list) or not entries:
        return 0.0, "not a list of transcript entries"
    typed = sum(1 for e in entries if isinstance(e, dict) and e.get("type") in ("user", "assistant")
                and isinstance(e.get("message"), dict))
    if typed and typed >= len(entries) * 0.6:
        return 0.9, f"{typed} user/assistant transcript entr{'y' if typed == 1 else 'ies'} with message objects"
    return 0.0, "entries are not Claude Code transcript lines"


def convert_claude_code(data: Any) -> tuple:
    entries = data if isinstance(data, list) else data.get("transcript")
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    task = str(meta.get("task") or "claude-code-session")
    traj = transcript_to_trajectory(entries, task=task, agent=str(meta.get("agent") or "claude-code"),
                                    prompt=meta.get("prompt"), expected=meta.get("expected"))
    warnings = [] if meta.get("expected") else ["no expected answer: the run is ungraded (success recorded as false)"]
    return traj, warnings


def register_format() -> None:
    from . import registry
    registry.register("claude-code", detect_claude_code, convert_claude_code,
                      "Claude Code transcript (JSONL entries of user/assistant messages with tool_use/tool_result blocks)")
