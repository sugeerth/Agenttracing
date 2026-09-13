"""The statistics the sections share, implemented once.

Each function here was moved from the section that first needed it and
kept to the digit: ``percentile`` from the impact and RL sections,
``mean_ci`` from the RL aggregate, ``iqm``, ``median``, ``mean``,
``optimality_gap``, ``percentile_interval`` and the stratified bootstrap
from the small-sample statistics. Nothing is re-derived: a section that
switches to these produces the bytes it produced before.

Randomness: :func:`rng` is the one way a section gets a stream. It seeds
``random.Random`` with a string that names the section, the seed and the
statistic, so every statistic has its own stream and adding a section
never moves another's numbers. ``random.Random`` hashes a string with
SHA-512, which is deterministic across processes and platforms.
"""

from __future__ import annotations

import math
import random
from typing import Optional, Sequence

#: the fraction cut from each end for the interquartile mean.
TRIM = 0.25
#: two-sided coverage of a percentile interval.
CONFIDENCE = 0.95


def mean(values: Sequence[float]) -> Optional[float]:
    """The arithmetic mean; None on an empty sample."""
    return sum(values) / len(values) if values else None


def pvar(values: Sequence[float]) -> Optional[float]:
    """Population variance — the denominator an explained-variance figure
    wants, since both sides are the same sample."""
    if not values:
        return None
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def median(values: Sequence[float]) -> Optional[float]:
    n = len(values)
    if n == 0:
        return None
    v = sorted(values)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def percentile(values: Sequence[float], q: float) -> float:
    """The ``q``-quantile (``0 ≤ q ≤ 1``) by linear interpolation between
    order statistics; ``0.0`` on an empty sample, which is what the callers
    that threshold on it want."""
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def mean_ci(values: Sequence[float]) -> tuple:
    """``(mean, [lo, hi])`` — a normal-approximation 95% interval with the
    sample standard deviation, rounded to four places; the interval is
    None under two values, the mean None under one."""
    n = len(values)
    if n == 0:
        return None, None
    m = sum(values) / n
    if n < 2:
        return round(m, 4), None
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    half = 1.96 * math.sqrt(var / n)
    return round(m, 4), [round(m - half, 4), round(m + half, 4)]


def iqm(values: Sequence[float], trim: float = TRIM) -> Optional[float]:
    """The interquartile mean: the mean of the middle 50% of ``values``.

    ``int(len(values) * trim)`` values are dropped from each end, the same
    truncation the published implementation uses — so at three runs nothing
    is cut and the IQM is the mean, which is honest rather than clever.
    None on an empty sample.
    """
    n = len(values)
    if n == 0:
        return None
    cut = int(n * trim)
    middle = sorted(values)[cut:n - cut] if n - 2 * cut > 0 else sorted(values)
    return sum(middle) / len(middle)


def optimality_gap(values: Sequence[float], target: float,
                   higher_is_better: bool = True) -> Optional[float]:
    """How far the runs fall short of ``target``, averaged over runs.

    ``mean(max(0, target − score))`` when higher is better, and the mirror
    when it is not; a run at or past the target contributes 0, so the gap is
    never negative and a policy cannot buy its way out of a failure with one
    exceptional episode.
    """
    if not values:
        return None
    if higher_is_better:
        return sum(max(0.0, target - v) for v in values) / len(values)
    return sum(max(0.0, v - target) for v in values) / len(values)


def percentile_interval(values: Sequence[float], confidence: float = CONFIDENCE) -> tuple:
    """``(lo, hi)`` percentile interval of a bootstrap distribution, the
    same index rule the gate statistics use; ``(None, None)`` when empty."""
    if not values:
        return None, None
    v = sorted(values)
    n = len(v)
    tail = (1.0 - confidence) / 2.0
    return v[max(0, int(tail * n) - 1)], v[min(n - 1, int((1.0 - tail) * n))]


def stratified_resamples(by_task: dict, samples: int, rng: random.Random) -> list:
    """``samples`` resamples of a score matrix, runs redrawn with
    replacement *within* each task.

    Each resample keeps every task's own run count, so a task with three
    runs contributes three runs to every resample and a task with eight
    contributes eight. Tasks are visited in sorted order, which is what
    makes the stream reproducible.
    """
    tasks = sorted(t for t, runs in by_task.items() if runs)
    out = []
    for _ in range(max(0, samples)):
        draw: list = []
        for tid in tasks:
            runs = by_task[tid]
            n = len(runs)
            for _ in range(n):
                draw.append(runs[rng.randrange(n)])
        out.append(draw)
    return out


def rng(seed: int, label: str, section: str = "rlstats") -> random.Random:
    """One stream per section and statistic: ``random.Random`` seeded with
    ``"agentdiff.<section>:<seed>:<label>"``.

    ``section`` names the module the stream belongs to, so two sections
    asking for the same label and seed still draw different numbers, and a
    section's stream never changes because another section was added.
    """
    return random.Random(f"agentdiff.{section}:{seed}:{label}")


__all__ = ["TRIM", "CONFIDENCE", "mean", "pvar", "median", "percentile", "mean_ci", "iqm",
           "optimality_gap", "percentile_interval", "stratified_resamples", "rng"]
