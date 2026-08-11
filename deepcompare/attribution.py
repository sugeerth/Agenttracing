"""Failure attribution for DeepCompare AI.

When exactly one of the two agents failed, walks the failing trajectory from
the first causal divergence to its answer and builds a causal chain: the root
divergent step, the steps whose input textually propagates from the root
step's output, any step annotated with a weak/bad quality, and the final
answer step.  Produces the SCHEMA.md ``attribution`` object with a
multi-sentence explanation.  When both agents succeeded (or both failed) the
attribution instead explains comparative efficiency, with ``failed_agent``
null.
"""

from __future__ import annotations

from typing import Optional

from .align import jaccard
from .divergence import _describe, _snippet
from .trace import Trajectory

#: minimum Jaccard word overlap between a step's input and the root step's
#: output for the step to count as causal propagation.
PROPAGATION_THRESHOLD = 0.3

_ROOT_REASONS = {
    "retrieval": "selected the wrong retrieval result",
    "tool_selection": "made an incorrect tool call",
    "planning": "followed a flawed plan",
    "reasoning": "took an incorrect reasoning path",
    "stopping": "kept taking extra steps instead of answering",
}


def _efficiency_explanation(a: Trajectory, b: Trajectory, both_succeeded: bool) -> str:
    """Comparative-efficiency explanation used when no single agent failed."""
    fate = "succeeded" if both_succeeded else "failed"
    tok_a = a.totals.input_tokens + a.totals.output_tokens
    tok_b = b.totals.input_tokens + b.totals.output_tokens
    parts = [
        f"Both agents {fate} on this task, so no failure is attributed.",
        (
            f"Agent A ({a.agent.name}) used {len(a.steps)} steps, {tok_a} tokens and "
            f"{a.totals.latency_s:g}s; Agent B ({b.agent.name}) used {len(b.steps)} steps, "
            f"{tok_b} tokens and {b.totals.latency_s:g}s."
        ),
    ]
    score_a = (len(a.steps), tok_a, a.totals.latency_s)
    score_b = (len(b.steps), tok_b, b.totals.latency_s)
    if score_a == score_b:
        parts.append("The two runs were equally efficient.")
    else:
        winner = "A" if score_a < score_b else "B"
        parts.append(f"Agent {winner} was the more efficient of the two.")
    return " ".join(parts)


def _root_step_index(
    failed_key: str, divergence: dict, alignment: list[dict]
) -> Optional[int]:
    """Failed-side step index where the causal divergence starts."""
    idx = divergence.get(failed_key)
    if idx is not None:
        return idx
    # The first divergence is a pure gap on the other side; fall back to the
    # first non-match alignment entry that has a failed-side index.
    for entry in alignment:
        if entry["op"] != "match" and entry[failed_key] is not None:
            return entry[failed_key]
    return None


def attribute(
    a: Trajectory, b: Trajectory, alignment: list[dict], divergences: list[dict]
) -> dict:
    """Build the SCHEMA.md ``attribution`` object for a compared pair.

    Returns a dict with keys ``failed_agent`` ("a"/"b"/None),
    ``root_cause_step``, ``chain``, ``category`` and ``explanation``.
    """
    if a.outcome.success == b.outcome.success:
        return {
            "failed_agent": None,
            "root_cause_step": None,
            "chain": [],
            "category": None,
            "explanation": _efficiency_explanation(a, b, both_succeeded=a.outcome.success),
        }

    failed_side = "a" if not a.outcome.success else "b"
    failed = a if failed_side == "a" else b
    other_side = "b" if failed_side == "a" else "a"
    label = failed_side.upper()

    if not divergences:
        answer_idx = len(failed.steps) - 1
        return {
            "failed_agent": failed_side,
            "root_cause_step": None,
            "chain": [answer_idx],
            "category": None,
            "explanation": (
                f"Agent {label} failed even though its trajectory did not structurally "
                f"diverge from Agent {other_side.upper()}'s. The failure cannot be "
                f"attributed to a specific divergent step; only the final answer at "
                f"step {answer_idx} differs in outcome."
            ),
        }

    causal = divergences[0]
    category = causal["kind"]
    root = _root_step_index(f"{failed_side}_index", causal, alignment)
    answer_idx = len(failed.steps) - 1
    if root is None:
        root = answer_idx

    root_step = failed.steps[root]
    chain = [root]
    for i in range(root + 1, len(failed.steps)):
        step = failed.steps[i]
        propagated = jaccard(step.input, root_step.output) >= PROPAGATION_THRESHOLD
        annotated = step.quality in ("weak", "bad")
        if propagated or annotated:
            chain.append(i)
    if answer_idx not in chain:
        chain.append(answer_idx)
    chain = sorted(set(chain))

    reason = _ROOT_REASONS.get(category, "took a divergent step")
    if root_step.quality in ("weak", "bad"):
        reason += f' ({root_step.quality}-quality: "{root_step.name or _snippet(root_step.input)}")'
    elif root_step.name or root_step.input:
        reason += f' ("{root_step.name or _snippet(root_step.input)}")'

    sentences = [f"Agent {label} diverged at step {root} because it {reason}."]
    intermediates = [i for i in chain if root < i < answer_idx]
    if intermediates:
        first_mid = failed.steps[intermediates[0]]
        where = f"step {intermediates[0]}"
        if len(intermediates) > 1:
            others = ", ".join(str(i) for i in intermediates[1:])
            where += f" (and steps {others})"
        sentences.append(
            f"That propagated into the step where it {_describe(first_mid)} at {where} "
            f"and caused the final failure."
        )
    else:
        sentences.append("That divergence carried directly into the final answer.")
    sentences.append(
        f"The answer emitted at step {answer_idx} was wrong, so the failure is "
        f"attributed to {category} at step {root}."
    )

    return {
        "failed_agent": failed_side,
        "root_cause_step": root,
        "chain": chain,
        "category": category,
        "explanation": " ".join(sentences),
    }
