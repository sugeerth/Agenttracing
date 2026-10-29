"""Replay sets: which steps to replay first, and how many to replay to cover the decisive one at a stated rate.

A diagnosis names one decisive step, and on hard cases one step is often wrong. What a person replaying a
failure needs is an order to try steps in and a number to stop at. :func:`replay_order` ranks every step of the
failing run: the committed step, then where the two runs first part, the contested candidates by score, the
causal account in trace order, the other steps without a twin in the passing run, and the rest.

:func:`conformal_k` turns that order into a set with a coverage guarantee (split conformal prediction, as in
*Conformal Agent Error Attribution*): on a labelled calibration set, the rank of the true step is the score; the
``ceil((m + 1)(1 - alpha))``-th smallest rank is k, and the top k steps of a new failure from the same
distribution contain its decisive step with probability at least ``1 - alpha``. The guarantee is marginal and
holds only under exchangeability: a k calibrated on one corpus says nothing about traces unlike it, so
:func:`evaluate` always reports the held-out coverage it actually got, beside the same procedure applied to plain
step order. Pure; no I/O.
"""

from __future__ import annotations

import hashlib
import math
from typing import Iterable, Optional

from .trace import Trajectory

__all__ = ["replay_order", "conformal_k", "evaluate", "held_out"]


def replay_order(diagnosis: dict, failing: Trajectory, passing: Trajectory) -> list[int]:
    """Every step index of the failing run, in the order to replay them."""
    from .diagnosis import twinless_steps
    dec = diagnosis.get("decisive_step") or {}
    n = len(failing.steps)
    twinless = twinless_steps(failing, passing)
    joint = sorted(dec.get("joint_candidates") or [], key=lambda c: -(c.get("score") or 0))
    account = sorted(e["step"] for e in diagnosis.get("causal_account") or [] if isinstance(e.get("step"), int))
    out: list[int] = []
    for i in ([dec.get("step")] + twinless[:1] + [c.get("step") for c in joint]
              + account + twinless + list(range(n))):
        if isinstance(i, int) and 0 <= i < n and i not in out:
            out.append(i)
    return out


def rank_of(order: list[int], truth: Iterable[int]) -> Optional[int]:
    """1-based rank of the earliest-ranked true step, or None when no true step is in the order."""
    ranks = [order.index(t) + 1 for t in truth if t in order]
    return min(ranks) if ranks else None


def conformal_k(scores: list[float], alpha: float) -> Optional[int]:
    """The split-conformal threshold: the ceil((m + 1)(1 - alpha))-th smallest calibration score, or None when
    that exceeds m (too few calibration cases for this alpha: no finite set carries the guarantee)."""
    m = len(scores)
    q = math.ceil((m + 1) * (1 - alpha))
    if m == 0 or q > m:
        return None
    return int(sorted(scores)[q - 1])


def held_out(case_id: str) -> bool:
    """The deterministic half a case falls in: True for the test half, False for calibration."""
    return hashlib.sha256(case_id.encode("utf-8")).digest()[0] % 2 == 1


def _side(rows: list[dict], key: str, alpha: float) -> dict:
    cal = [r for r in rows if not held_out(r["id"])]
    test = [r for r in rows if held_out(r["id"])]
    big = max([r["steps"] for r in rows] or [1]) + 1
    k = conformal_k([r[key] if r[key] is not None else big for r in cal], alpha)
    if k is None or not test:
        return {"k": k, "calibration": len(cal), "test": len(test), "coverage": None, "mean_set": None}
    hits = sum(1 for r in test if r[key] is not None and r[key] <= k)
    return {"k": k, "calibration": len(cal), "test": len(test), "coverage": round(hits / len(test), 4),
            "mean_set": round(sum(min(k, r["steps"]) for r in test) / len(test), 2)}


def evaluate(rows: list[dict], alpha: float = 0.1) -> dict:
    """Calibrate k on one half of ``rows`` and measure the other. Each row is ``{"id", "rank", "index_rank",
    "steps"}``: the true step's rank in the replay order, its rank in plain step order (step index + 1), and the
    run's length. Both orders go through the same procedure, so the second is the floor the first must beat."""
    return {"alpha": alpha, "target": round(1 - alpha, 4),
            "mean_steps": round(sum(r["steps"] for r in rows) / len(rows), 2) if rows else None,
            "replay_order": _side(rows, "rank", alpha), "step_order": _side(rows, "index_rank", alpha)}
