"""A second model that judges the output.

When a task has no expected answer — or the answer is one exact match
cannot grade — a judging model reads the task, the agent's final
answer and, when asked, the steps, and returns a verdict: solved or
not, a score in [0, 1], and a rationale in its own words. The verdict is
recorded on the trace as ``outcome.judge`` beside the existing outcome,
with the judging model named and the rubric quoted, and it never
replaces ``outcome.success`` unless ``apply=True`` — in which case the
trace says ``outcome.graded_by: "model"`` so a success rate built on
it can be read for what it is.

The judge is a provider like any other (OpenAI-compatible, Anthropic,
Ollama, or scripted for tests), so this lives in the harness — the
network boundary — and the engine never calls it. Two agents judged
by the same model on the same rubric are comparable; a model judging
its own run is flagged (``self_judged``) because it is not independent.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .providers import Provider, ProviderError

DEFAULT_RUBRIC = (
    "Judge whether the agent's final answer correctly and completely answers the task. "
    "Be strict: a partially right answer, an unsupported claim, or a wrong unit is not correct. "
    "Reply with JSON only: {\"success\": true|false, \"score\": 0.0-1.0, \"rationale\": \"one or two sentences\"}."
)


def _prompt(trace: dict, rubric: str, with_steps: bool) -> list:
    task = trace.get("task") or {}
    outcome = trace.get("outcome") or {}
    parts = [f"TASK:\n{task.get('prompt', '')}"]
    if task.get("expected"):
        parts.append(f"REFERENCE ANSWER (may be partial or phrased differently):\n{task['expected']}")
    if with_steps:
        lines = []
        for s in (trace.get("steps") or [])[:-1][:40]:
            lines.append(f"[{s.get('index')}] {s.get('type')} {s.get('name', '')}: "
                         f"{str(s.get('input', ''))[:200]} -> {str(s.get('output', ''))[:300]}")
        if lines:
            parts.append("STEPS THE AGENT TOOK:\n" + "\n".join(lines))
    parts.append(f"AGENT'S FINAL ANSWER:\n{outcome.get('answer', '')}")
    return [{"role": "system", "content": rubric},
            {"role": "user", "content": "\n\n".join(parts)}]


def _parse(text: str) -> Optional[dict]:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or "success" not in data:
        return None
    score = data.get("score")
    try:
        score = max(0.0, min(1.0, float(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return {"success": bool(data["success"]), "score": score, "rationale": str(data.get("rationale", ""))[:600]}


def judge_trace(trace: dict, provider: Provider, *, rubric: str = DEFAULT_RUBRIC,
                with_steps: bool = False, apply: bool = False) -> dict:
    """Judge one trace in place; returns the ``judge`` block written to
    ``trace["outcome"]["judge"]``."""
    messages = _prompt(trace, rubric, with_steps)
    try:
        response = provider.complete(messages, None)
        text = getattr(response, "text", "") or ""
        verdict = _parse(text)
        error = None if verdict else "the judge did not return the JSON asked for"
    except ProviderError as exc:
        verdict, text, error = None, "", f"provider error: {exc}"
    agent_model = str(((trace.get("agent") or {}).get("model")) or "")
    block = {
        "model": getattr(provider, "model", "") or getattr(provider, "name", "judge"),
        "provider": getattr(provider, "kind", "provider"),
        "rubric": rubric,
        "with_steps": with_steps,
        "success": verdict["success"] if verdict else None,
        "score": verdict["score"] if verdict else None,
        "rationale": verdict["rationale"] if verdict else None,
        "raw": text[:800],
        "error": error,
        "self_judged": bool(agent_model) and agent_model == (getattr(provider, "model", "") or ""),
        "applied": False,
    }
    outcome = trace.setdefault("outcome", {})
    prior = {"success": outcome.get("success"), "score": outcome.get("score"),
             "graded_by": outcome.get("graded_by", "exact-match" if (trace.get("task") or {}).get("expected") else "ungraded")}
    block["prior"] = prior
    block["agrees_with_prior"] = (verdict["success"] == prior["success"]) if verdict and isinstance(prior["success"], bool) else None
    if apply and verdict:
        outcome["success"] = verdict["success"]
        if verdict["score"] is not None:
            outcome["score"] = verdict["score"]
        outcome["graded_by"] = "model"
        outcome.pop("note", None)
        block["applied"] = True
    outcome["judge"] = block
    return block


def judge_many(traces: list, provider_factory: Callable[[], Provider], **kwargs) -> dict:
    """Judge a list of trace dicts (in place); a fresh provider per trace
    so scripted judges replay cleanly. Returns counts."""
    judged = agreed = disagreed = failed = 0
    for trace in traces:
        block = judge_trace(trace, provider_factory(), **kwargs)
        if block["error"]:
            failed += 1
            continue
        judged += 1
        if block["agrees_with_prior"] is True:
            agreed += 1
        elif block["agrees_with_prior"] is False:
            disagreed += 1
    return {"judged": judged, "agreed_with_prior": agreed, "disagreed_with_prior": disagreed, "failed": failed}


__all__ = ["DEFAULT_RUBRIC", "judge_trace", "judge_many"]
