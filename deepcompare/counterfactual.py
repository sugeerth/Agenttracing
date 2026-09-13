"""Counterfactual replay for DeepCompare AI (SCHEMA.md v8).

When a failure was attributed, estimates the what-if: the failing agent
adopts the winner's decision at the root divergence and inherits the
winner's suffix.  The spliced trajectory is the failing agent's own steps
before the causal divergence plus the winner's steps from the causal region
through its answer; step/token/latency/cost estimates are summed from the
actual spliced steps (per-side cost-per-token ratios, as in steps_eval).
"""

from __future__ import annotations

from typing import Optional

from .divergence import _describe
from .steps_eval import _cost_per_token
from .trace import Trajectory


def _root_row_pos(alignment: list[dict], failed_key: str, root: int) -> Optional[int]:
    """Position in the alignment of the row carrying the root step."""
    for pos, entry in enumerate(alignment):
        if entry[failed_key] == root:
            return pos
    return None


def _confidence(prefix_rows: list[dict]) -> str:
    """high: all-match prefix; medium: drift present; low: one-sided rows."""
    ops = {entry["op"] for entry in prefix_rows}
    if ops & {"a_only", "b_only"}:
        return "low"
    if "drift" in ops:
        return "medium"
    return "high"


def counterfactual(report: dict, a: Trajectory, b: Trajectory) -> Optional[dict]:
    """Build the SCHEMA.md v8 ``counterfactual`` object, or ``None``.

    Applicable only when ``attribution.failed_agent`` and
    ``attribution.root_cause_step`` are both set.  Splice = failing side of
    every row before the root divergence + the winner's steps from its side
    of the causal region through its answer.  Deltas are estimate minus the
    failing agent's real trajectory (negative = savings).
    """
    attribution = report["attribution"]
    failed_side = attribution["failed_agent"]
    root = attribution["root_cause_step"]
    if failed_side is None or root is None:
        return None

    winner_side = "b" if failed_side == "a" else "a"
    failing = a if failed_side == "a" else b
    winner = b if failed_side == "a" else a
    alignment = report["alignment"]
    failed_key = f"{failed_side}_index"
    winner_key = f"{winner_side}_index"

    root_pos = _root_row_pos(alignment, failed_key, root)
    if root_pos is None:
        return None
    prefix_rows = alignment[:root_pos]

    # Failing agent keeps its side of the paired rows before the divergence.
    prefix_steps = [
        entry[failed_key]
        for entry in prefix_rows
        if entry[failed_key] is not None and entry[winner_key] is not None
    ]

    # Winner's suffix: from its side of the causal region through its answer.
    winner_start = alignment[root_pos][winner_key]
    if winner_start is None:
        for entry in alignment[root_pos + 1 :]:
            if entry[winner_key] is not None:
                winner_start = entry[winner_key]
                break
    if winner_start is None:
        winner_start = len(winner.steps) - 1
    adopted_steps = list(range(winner_start, len(winner.steps)))

    ratio_f = _cost_per_token(failing)
    ratio_w = _cost_per_token(winner)
    prefix = [failing.steps[i] for i in prefix_steps]
    adopted = [winner.steps[i] for i in adopted_steps]

    est_steps = len(prefix) + len(adopted)
    prefix_tokens = sum(s.tokens for s in prefix)
    adopted_tokens = sum(s.tokens for s in adopted)
    est_tokens = prefix_tokens + adopted_tokens
    est_latency = round(sum(s.latency_s for s in prefix) + sum(s.latency_s for s in adopted), 4)
    est_cost = round(ratio_f * prefix_tokens + ratio_w * adopted_tokens, 6)

    real_steps = len(failing.steps)
    real_tokens = sum(s.tokens for s in failing.steps)
    real_latency = round(sum(s.latency_s for s in failing.steps), 4)
    real_cost = round(ratio_f * real_tokens, 6)

    estimate = {
        "outcome": "success" if winner.outcome.success else "failure",
        "steps": est_steps,
        "steps_delta": est_steps - real_steps,
        "tokens": est_tokens,
        "tokens_delta": est_tokens - real_tokens,
        "latency_s": est_latency,
        "latency_delta_s": round(est_latency - real_latency, 4),
        "cost_usd": est_cost,
        "cost_delta_usd": round(est_cost - real_cost, 6),
    }

    decision_step = winner.steps[winner_start] if 0 <= winner_start < len(winner.steps) else None
    decision = _describe(decision_step) if decision_step is not None else "taken the winning path"
    premise = f"had {failing.agent.name} made {winner.agent.name}'s decision at step {root}"

    savings_bits = []
    for delta, unit in (
        (estimate["steps_delta"], " steps"),
        (estimate["tokens_delta"], " tokens"),
        (estimate["latency_delta_s"], "s"),
    ):
        if delta < 0:
            mag = f"{-delta:,}" if isinstance(delta, int) else f"{-delta:g}"
            savings_bits.append(f"{mag}{unit}")
    if savings_bits:
        savings_txt = f" — saving {', '.join(savings_bits)} —"
    else:
        savings_txt = " — at no extra cost —"

    narrative = (
        f"Had {failing.agent.name} {decision} at step {root} "
        f"(as {winner.agent.name} did), the estimated run is {est_steps} steps, "
        f"{est_tokens:,} tokens, {est_latency:g}s{savings_txt} and ends in "
        f"{estimate['outcome']}."
    )

    return {
        "premise": premise,
        "splice": {
            "prefix_steps": prefix_steps,
            "adopted_from": winner_side,
            "adopted_steps": adopted_steps,
        },
        "estimate": estimate,
        "confidence": _confidence(prefix_rows),
        "narrative": narrative,
    }
