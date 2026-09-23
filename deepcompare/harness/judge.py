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

A long run does not fit in a prompt, so ``with_steps`` shows an
**excerpt** and says so: the opening and the closing of the run with an
explicit line naming how many steps were dropped and which. What the
judge was shown is recorded on the block (``steps_shown``,
``steps_total``, ``steps_basis``), because a verdict over the first
forty steps of a three-hundred-step run is a verdict about the opening,
and nothing downstream can know that unless the block says so.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .providers import Provider, ProviderError

#: how many steps of the run to put in front of the judge.  A cap, not a
#: sample: what does not fit is named rather than dropped silently.
STEP_EXCERPT = 40

_JSON_CONTRACT = ("Reply with JSON only: {\"success\": true|false, \"score\": 0.0-1.0, "
                  "\"rationale\": \"one or two sentences\"}.")

DEFAULT_RUBRIC = (
    "Judge whether the agent's final answer correctly and completely answers the task. "
    "Be strict: a partially right answer, an unsupported claim, or a wrong unit is not correct. "
    + _JSON_CONTRACT
)

#: for runs long enough that the answer is the smallest part of them.  A
#: long run is usually right about most of itself and wrong in one place,
#: so an answer that reads well is weak evidence: this rubric asks after
#: the work rather than the prose.  It is an instruction, not a hint —
#: the judge is never shown the golden set, or it would be grading itself
#: against the answers.
LONG_RUN_RUBRIC = (
    "Judge whether the agent actually did the whole task, not whether its final answer reads well. "
    "A long run is usually correct for most of its length and wrong in one place, and a fluent summary "
    "is the easiest part of it to get right. Ask: is every part of the task accounted for by work you "
    "can see, and was each part checked by something other than the agent's own say-so? "
    "Treat a claim the steps do not show as not done. If you are shown an excerpt of the run, judge "
    "only what you can see and say in the rationale what you could not. "
    + _JSON_CONTRACT
)

#: rubrics by name, so `--rubric long-run` is a thing a reader can repeat
RUBRICS = {"strict": DEFAULT_RUBRIC, "long-run": LONG_RUN_RUBRIC}


def resolve_rubric(rubric: Optional[str]) -> tuple:
    """``(text, name)`` for a rubric given as a name from :data:`RUBRICS`
    or as the instruction itself.  The name travels onto the block so two
    cards can be compared only when they asked the same question."""
    if not rubric:
        return DEFAULT_RUBRIC, "strict"
    if rubric in RUBRICS:
        return RUBRICS[rubric], rubric
    # a caller that passed the text of a known rubric asked the same
    # question as the caller that passed its name, and the block should
    # say so — two cards are comparable on the question, not on the spelling
    for name, text in RUBRICS.items():
        if rubric == text:
            return text, name
    return rubric, "custom"


def _steps_block(steps: list, cap: int = STEP_EXCERPT) -> tuple:
    """The steps as the judge will see them: ``(text, shown, total, basis)``.

    Under the cap this is the whole run.  Over it, the opening and the
    closing with the gap named — the failure in a long run is usually late,
    and a head-only excerpt puts the judge in front of the part that went
    fine and asks it about the part it cannot see.
    """
    def line(s):
        return (f"[{s.get('index')}] {s.get('type')} {s.get('name', '')}: "
                f"{str(s.get('input', ''))[:200]} -> {str(s.get('output', ''))[:300]}")

    total = len(steps)
    if total <= cap:
        return "\n".join(line(s) for s in steps), total, total, ("every step of the run" if total else "no steps")
    head, tail = cap // 2, cap - cap // 2
    dropped = steps[head:total - tail]
    first, last = dropped[0].get("index"), dropped[-1].get("index")
    body = ([line(s) for s in steps[:head]]
            + [f"... {len(dropped)} steps omitted here (indexes {first}-{last}) ..."]
            + [line(s) for s in steps[total - tail:]])
    return ("\n".join(body), cap, total,
            f"an excerpt: the first {head} and last {tail} of {total} steps, with {len(dropped)} named as omitted")


def _prompt(trace: dict, rubric: str, with_steps: bool, cap: int = STEP_EXCERPT) -> tuple:
    """``(messages, shown, total, basis)`` — the prompt and, beside it,
    what of the run it actually contains."""
    task = trace.get("task") or {}
    outcome = trace.get("outcome") or {}
    parts = [f"TASK:\n{task.get('prompt', '')}"]
    if task.get("expected"):
        parts.append(f"REFERENCE ANSWER (may be partial or phrased differently):\n{task['expected']}")
    shown = total = 0
    basis = "the answer only; the steps were not shown"
    if with_steps:
        body, shown, total, basis = _steps_block((trace.get("steps") or [])[:-1], cap)
        if body:
            parts.append(f"STEPS THE AGENT TOOK ({basis}):\n" + body)
    parts.append(f"AGENT'S FINAL ANSWER:\n{outcome.get('answer', '')}")
    return ([{"role": "system", "content": rubric},
             {"role": "user", "content": "\n\n".join(parts)}], shown, total, basis)


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


def judge_trace(trace: dict, provider: Provider, *, rubric: Optional[str] = None,
                with_steps: bool = False, apply: bool = False, cap: int = STEP_EXCERPT) -> dict:
    """Judge one trace in place; returns the ``judge`` block written to
    ``trace["outcome"]["judge"]``."""
    rubric, rubric_name = resolve_rubric(rubric)
    messages, shown, total, steps_basis = _prompt(trace, rubric, with_steps, cap)
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
        "rubric_name": rubric_name,
        "with_steps": with_steps,
        # what the judge was actually shown.  A verdict over an excerpt is
        # a verdict about the excerpt, and this is where that is written down
        "steps_shown": shown,
        "steps_total": total,
        "steps_basis": steps_basis,
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


__all__ = ["DEFAULT_RUBRIC", "LONG_RUN_RUBRIC", "RUBRICS", "STEP_EXCERPT",
           "resolve_rubric", "judge_trace", "judge_many"]
