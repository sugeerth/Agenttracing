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


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), exactly, via math.comb.

    The question a re-run answers is not "is the new rate outside the old
    interval" — the interval describes uncertainty about the old rate, not
    the sampling noise of the next run.  It is "how often would an
    *unchanged* agent produce a result this good?"  A 3-of-4 agent hits
    4-of-4 about 32% of the time by luck alone, which is why that jump can
    never confirm a fix on its own.
    """
    if n <= 0 or k <= 0:
        return 1.0
    if k > n:
        return 0.0
    p = min(1.0, max(0.0, p))
    from math import comb
    return sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
               for i in range(k, n + 1))


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


# ---------------------------------------------------------------------------
# paired inference and clustered standard errors (Miller, "Adding Error Bars
# to Evals", 2024): two agents on the SAME tasks is a paired design, and a
# paired test has dramatically more power than comparing two rates; tasks
# that share a source are not independent, and a naive standard error can
# be several times too small.

MIN_PAIRS_TO_DISTINGUISH = 10


def _sample_stdev(values: list) -> float:
    """Sample standard deviation (ddof=1); written out so this module never
    imports the stdlib module that shares its name."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def sign_test(a_wins: int, b_wins: int) -> Optional[float]:
    """Exact two-sided sign test on the discordant pairs (McNemar's exact
    form): under "no difference" each discordant pair is a fair coin, so
    the p-value is the two-sided binomial tail at the smaller count.
    ``None`` when there are no discordant pairs — nothing to test."""
    n = a_wins + b_wins
    if n == 0:
        return None
    k = min(a_wins, b_wins)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return round(min(1.0, 2.0 * tail), 6)


def paired_inference(pairs: list, labels: tuple = ("A", "B"),
                     min_pairs: int = MIN_PAIRS_TO_DISTINGUISH) -> dict:
    """Paired-difference inference over per-task outcome pairs.

    ``pairs`` is a list of ``(a, b)`` where each side is a success
    indicator (bool) or a success rate in [0, 1] (multi-run tasks);
    pairs with a missing side are dropped and counted.  Reports the
    mean difference (A minus B) with its paired standard error and 95%
    interval, the discordant-pair counts, the exact sign-test p-value,
    and a verdict that refuses to distinguish below ``min_pairs`` —
    with the denominator stated either way.
    """
    usable = [(float(a), float(b)) for a, b in pairs
              if a is not None and b is not None]
    dropped = len(pairs) - len(usable)
    n = len(usable)
    diffs = [a - b for a, b in usable]
    a_wins = sum(1 for d in diffs if d > 0)
    b_wins = sum(1 for d in diffs if d < 0)
    ties = n - a_wins - b_wins
    mean = (sum(diffs) / n) if n else None
    se = None
    ci = None
    if n >= 2:
        sd = _sample_stdev(diffs)
        se = sd / math.sqrt(n)
        ci = [round(mean - _Z * se, 4), round(mean + _Z * se, 4)]
    p = sign_test(a_wins, b_wins)
    if n < min_pairs:
        verdict = (f"not distinguishable: only {n} paired task(s) "
                   f"(needs at least {min_pairs})")
    elif p is not None and p < 0.05:
        verdict = (f"{labels[0]} better" if mean > 0 else f"{labels[1]} better")
    else:
        verdict = (f"not distinguishable on {n} paired task(s): "
                   f"{a_wins} favour {labels[0]}, {b_wins} favour {labels[1]}, "
                   f"{ties} tied")
    return {
        "labels": list(labels), "n_pairs": n, "dropped_unpaired": dropped,
        "a_wins": a_wins, "b_wins": b_wins, "ties": ties,
        "discordant": a_wins + b_wins,
        "diff": round(mean, 4) if mean is not None else None,
        "se": round(se, 4) if se is not None else None,
        "ci95": ci, "sign_test_p": p, "verdict": verdict,
        "method": "paired difference with paired SE; exact two-sided sign "
                  "test on discordant pairs (McNemar)",
    }


def clustered_se(values: list, clusters: list) -> dict:
    """Cluster-robust standard error of a mean.  ``values`` are per-unit
    observations (e.g. 1/0 correct), ``clusters`` the cluster key of each
    unit (e.g. the scenario family).  Reports the naive SE, the
    cluster-robust SE (the sandwich estimator for a mean, with the usual
    G/(G−1) small-sample factor) and their ratio — how much the naive
    error bar understated the uncertainty."""
    n = len(values)
    if n < 2 or len(clusters) != n:
        return {"n": n, "clusters": len(set(clusters)), "mean": None,
                "naive_se": None, "clustered_se": None, "ratio": None,
                "note": "fewer than two observations"}
    mean = sum(values) / n
    naive = _sample_stdev(values) / math.sqrt(n)
    groups: dict = {}
    for v, c in zip(values, clusters):
        groups.setdefault(c, []).append(v - mean)
    g = len(groups)
    sandwich = sum(sum(resid) ** 2 for resid in groups.values()) / (n * n)
    factor = g / (g - 1) if g > 1 else 1.0
    clustered = math.sqrt(sandwich * factor)
    return {
        "n": n, "clusters": g, "mean": round(mean, 4),
        "naive_se": round(naive, 6), "clustered_se": round(clustered, 6),
        "ratio": round(clustered / naive, 3) if naive > 0 else None,
        "ci95_naive": [round(mean - _Z * naive, 4), round(mean + _Z * naive, 4)],
        "ci95_clustered": [round(mean - _Z * clustered, 4),
                           round(mean + _Z * clustered, 4)],
        "note": ("one cluster: clustered SE is undefined, naive shown"
                 if g < 2 else
                 "clusters share a source; the clustered interval is the "
                 "honest one"),
    }
