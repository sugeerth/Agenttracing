"""Small-sample statistics for gate decisions (SCHEMA.md v14).

A gate that fires on noise gets switched off, and then it protects nothing.
On an eight-task suite one flipped task moves the success rate by 12.5%,
which is well inside what resampling the same agent would produce — so a
raw point-estimate comparison cannot tell "the candidate got worse" from
"we ran it again".

These helpers put an honest interval around the numbers the gate compares.
They are deliberately exact and dependency-free: Wilson intervals in closed
form, and a deterministic bootstrap (fixed seed, so a gate decision never
changes between runs on the same data).

Nothing here decides anything on its own — the gate still applies the
thresholds a team chose.  What it adds is a `significant` flag saying whether
the observed move is larger than the noise floor, so a team can require both.
"""

from __future__ import annotations

import math
import random
from typing import Optional, Sequence

#: fixed seed — gate decisions must not move between runs on identical data.
BOOTSTRAP_SEED = 20260812
#: resamples for the paired bootstrap.
BOOTSTRAP_SAMPLES = 2000
#: two-sided coverage for reported intervals.
CONFIDENCE = 0.95
#: z for a 95% two-sided normal interval.
_Z = 1.959963985


def wilson_interval(successes: int, trials: int,
                    z: float = _Z) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays inside [0, 1]
    and behaves at the extremes — which is exactly where small eval suites
    live (0/8 and 8/8 are common).
    """
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = (
        z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    ) / denominator
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def pass_at_k(successes: int, runs: int, k: int) -> Optional[float]:
    """Probability that all ``k`` sampled runs pass, given ``successes``/``runs``.

    The strict reading of reliability ("pass^k" in the agent-eval literature):
    an agent that passes 2 of 3 runs is not 67% reliable when a user needs it
    to work three times in a row.  Returns None when there are fewer than
    ``k`` runs to sample from.
    """
    if runs < k or k <= 0:
        return None
    if successes < k:
        return 0.0
    probability = 1.0
    for i in range(k):
        probability *= (successes - i) / (runs - i)
    return round(probability, 4)


def paired_bootstrap_difference(
    baseline: Sequence[bool],
    candidate: Sequence[bool],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Bootstrap the baseline-minus-candidate success-rate difference.

    Tasks are resampled *in pairs* because both agents ran the same suite;
    resampling them independently would discard that pairing and overstate
    the uncertainty.  Returns the observed difference, its interval, and
    whether that interval excludes zero.
    """
    n = min(len(baseline), len(candidate))
    if n == 0:
        return {"observed": 0.0, "low": 0.0, "high": 0.0,
                "significant": False, "samples": 0}

    pairs = list(zip(baseline[:n], candidate[:n]))
    observed = (
        sum(1 for b, _ in pairs if b) - sum(1 for _, c in pairs if c)
    ) / n

    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(samples):
        picked = [pairs[rng.randrange(n)] for _ in range(n)]
        base_rate = sum(1 for b, _ in picked if b) / n
        cand_rate = sum(1 for _, c in picked if c) / n
        differences.append(base_rate - cand_rate)
    differences.sort()

    tail = (1 - CONFIDENCE) / 2
    low = differences[max(0, int(tail * samples) - 1)]
    high = differences[min(samples - 1, int((1 - tail) * samples))]
    return {
        "observed": round(observed, 4),
        "low": round(low, 4),
        "high": round(high, 4),
        # A drop is meaningful only if the whole interval sits above zero.
        "significant": low > 0.0,
        "samples": samples,
    }


def two_group_bootstrap_difference(
    group_a: Sequence[bool],
    group_b: Sequence[bool],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Bootstrap the difference in rates between two *independent* groups.

    Unlike :func:`paired_bootstrap_difference`, the groups are unrelated and
    may differ in size — comparing runs that have an attribute against runs
    that do not, for instance.  Each group is resampled at its own size, which
    is what keeps the resampled rates faithful to the observed ones.  (Forcing
    the groups to a common length and rebuilding them instead silently
    rewrites both rates, and can flip the sign of the difference.)

    Returns ``observed`` = rate(a) − rate(b), the interval, and whether that
    interval excludes zero.
    """
    n_a, n_b = len(group_a), len(group_b)
    if n_a == 0 or n_b == 0:
        return {"observed": 0.0, "low": 0.0, "high": 0.0,
                "significant": False, "samples": 0}

    rate_a = sum(1 for v in group_a if v) / n_a
    rate_b = sum(1 for v in group_b if v) / n_b
    observed = rate_a - rate_b

    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(samples):
        sampled_a = sum(1 for _ in range(n_a) if group_a[rng.randrange(n_a)]) / n_a
        sampled_b = sum(1 for _ in range(n_b) if group_b[rng.randrange(n_b)]) / n_b
        differences.append(sampled_a - sampled_b)
    differences.sort()

    tail = (1 - CONFIDENCE) / 2
    low = differences[max(0, int(tail * samples) - 1)]
    high = differences[min(samples - 1, int((1 - tail) * samples))]
    return {
        "observed": round(observed, 4),
        "low": round(low, 4),
        "high": round(high, 4),
        "significant": low > 0.0 or high < 0.0,
        "samples": samples,
    }


def describe_significance(result: dict, n_tasks: int) -> str:
    """Plain-language reading of a bootstrap result."""
    if result["samples"] == 0:
        return "No paired tasks to compare."
    observed = result["observed"]
    if observed <= 0:
        return (
            f"The candidate did not lose ground ({observed:+.1%} success "
            f"difference across {n_tasks} task(s))."
        )
    if result["significant"]:
        return (
            f"The {observed:.1%} drop holds up under resampling "
            f"(95% interval {result['low']:+.1%} to {result['high']:+.1%}, "
            f"entirely above zero) — this is a real regression, not noise."
        )
    return (
        f"The {observed:.1%} drop is within noise for {n_tasks} task(s): the "
        f"95% interval ({result['low']:+.1%} to {result['high']:+.1%}) includes "
        f"zero, so the same agent re-run could produce it. Add tasks or runs "
        f"before treating this as a regression."
    )
