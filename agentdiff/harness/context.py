"""What the model saw: the exact context at a step, rebuilt from the trace.

The most common question in an agent debug session is not "what did it
do" but "what was in front of it when it did that".  A SCHEMA trace
carries every step's input and output, so the message list the model
received before any step can be rebuilt deterministically — the same
rebuild the counterfactual replay feeds to a model, here rendered for a
person.  No network, no model: this is reconstruction, and it says so.

``context_at(trace, step)`` returns the messages before ``step``;
``render`` prints them; ``diff`` lines up two contexts — two runs at the
aligned row where they parted — so the difference in what each model
saw stands next to the difference in what each did.
"""

from __future__ import annotations

import difflib
from typing import Optional

from .agent import DEFAULT_SYSTEM
from .cassette import TOOLISH, parse_call


def messages_before(steps: list, prompt: str, system_prompt: str) -> list:
    """The neutral message list a provider would be handed after ``steps``:
    every tool-ish step (``search``, ``retrieve``, ``read``, ``tool_call``)
    as the assistant's call and the tool's result, every thinking step as
    the assistant's words, the answer never (a context is what precedes
    an answer)."""
    messages: list = [{"role": "system", "content": system_prompt},
                      {"role": "user", "content": prompt}]
    for i, step in enumerate(steps):
        kind = step.get("type")
        if kind == "answer":
            continue
        if kind in TOOLISH:
            name = str(step.get("name") or "")
            parsed_name, args = parse_call(str(step.get("input") or ""))
            if not (parsed_name and (parsed_name == name or not name)):
                args = {"_raw": str(step.get("input") or "")}
            call_id = f"step_{step.get('index', i)}"
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"id": call_id, "name": name or parsed_name, "arguments": args}]})
            messages.append({"role": "tool", "tool_call_id": call_id, "name": name or parsed_name,
                             "content": str(step.get("output") or ""),
                             **({"error": True} if step.get("error") else {})})
            continue
        words = [str(step.get("input") or "").strip(), str(step.get("output") or "").strip()]
        text = "\n".join(w for w in words if w) if words[0] != words[1] else words[0]
        if text:
            messages.append({"role": "assistant", "content": text})
    return messages


def context_at(trace: dict, step: int, *, system_prompt: str = DEFAULT_SYSTEM) -> list:
    """The message list the model had when it produced ``step`` — the
    system prompt, the task, and every step before ``step``.  ``step``
    0 sees only the prompt."""
    steps = list(trace.get("steps") or [])
    if not (0 <= step <= len(steps)):
        raise ValueError(f"step {step} is outside the trace's {len(steps)} steps")
    task = trace.get("task") or {}
    return messages_before(steps[:step], str(task.get("prompt") or ""), system_prompt)


def render(messages: list, *, width: int = 100) -> str:
    """Messages as a person reads them: one block per message, the role
    on its own line, tool calls and results named."""
    out: list = []
    for i, m in enumerate(messages):
        role = str(m.get("role") or "")
        head = f"[{i}] {role}"
        if m.get("tool_calls"):
            calls = ", ".join(f"{c.get('name')}({_args(c.get('arguments'))})" for c in m["tool_calls"])
            head += f" → {calls}"
        if role == "tool":
            head += f" ({m.get('name')})" + (" — error" if m.get("error") else "")
        out.append(head)
        content = str(m.get("content") or "").rstrip()
        if content:
            for line in content.splitlines() or [""]:
                while len(line) > width:
                    out.append("    " + line[:width])
                    line = line[width:]
                out.append("    " + line)
    return "\n".join(out) + "\n"


def _args(arguments) -> str:
    if isinstance(arguments, dict):
        if set(arguments) == {"_raw"}:
            return repr(arguments["_raw"])
        return ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return repr(arguments) if arguments else ""


def diff(trace_a: dict, step_a: int, trace_b: dict, step_b: int, *,
         names: Optional[tuple] = None, system_prompt: str = DEFAULT_SYSTEM) -> str:
    """A unified diff of the two contexts, labelled by agent and step."""
    names = names or ((trace_a.get("agent") or {}).get("name", "a"), (trace_b.get("agent") or {}).get("name", "b"))
    left = render(context_at(trace_a, step_a, system_prompt=system_prompt)).splitlines(keepends=True)
    right = render(context_at(trace_b, step_b, system_prompt=system_prompt)).splitlines(keepends=True)
    return "".join(difflib.unified_diff(left, right, fromfile=f"{names[0]} before step {step_a}",
                                        tofile=f"{names[1]} before step {step_b}", n=2))


def summary(trace: dict, step: int) -> dict:
    """Counts a page or a log line can carry: how many messages, how
    many tool results, and how much text the model had at ``step``."""
    messages = context_at(trace, step)
    return {"step": step, "messages": len(messages),
            "tool_results": sum(1 for m in messages if m.get("role") == "tool"),
            "chars": sum(len(str(m.get("content") or "")) for m in messages),
            "note": "reconstructed from the trace's recorded steps; the provider's own framing of these messages is not recorded"}
