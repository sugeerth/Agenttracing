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
from functools import lru_cache
from typing import Optional, Sequence
from . import sections as _sections

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
    interval (t on n − 1 degrees of freedom, since a paired suite is often
    ten or twenty tasks, and kept within [−1, 1]), the discordant-pair
    counts, the exact sign-test p-value,
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
    critical = None
    if n >= 2:
        sd = _sample_stdev(diffs)
        se = sd / math.sqrt(n)
        critical = t_critical(n - 1)
        ci = [round(max(-1.0, mean - critical * se), 4), round(min(1.0, mean + critical * se), 4)]
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
        "ci95": ci, "critical": round(critical, 4) if critical is not None else None,
        "sign_test_p": p, "verdict": verdict,
        "method": "paired difference with paired SE and t on n − 1; exact "
                  "two-sided sign test on discordant pairs (McNemar)",
    }


@lru_cache(maxsize=None)
def t_critical(df: float, coverage: float = CONFIDENCE) -> float:
    """Two-sided Student t critical value on ``df`` degrees of freedom (a
    fractional df, as a Satterthwaite approximation gives, is allowed), by
    bisection on the CDF (Simpson's rule over the density); stdlib only."""
    if df < 1:
        raise ValueError("t needs at least one degree of freedom")
    target = 1 - (1 - coverage) / 2
    c = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)

    def pdf(x: float) -> float:
        return c * (1 + x * x / df) ** (-(df + 1) / 2)

    def cdf(x: float, steps: int = 2000) -> float:
        h = x / steps
        inner = sum((4 if i % 2 else 2) * pdf(i * h) for i in range(1, steps))
        return 0.5 + (pdf(0.0) + pdf(x) + inner) * h / 3

    lo, hi = 0.0, 1000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) < target else (lo, mid)
    return (lo + hi) / 2


#: below this many clusters the cluster-robust interval runs short of its
#: nominal coverage even with the t critical value: Cameron and Miller's
#: practitioner's guide puts "few" below 20 to 50, and in simulation it held
#: 0.87 at 5 clusters, 0.92 at 10, 0.94 at 19 (labelled synthetic; docs/CHANGELOG.md)
FEW_CLUSTERS = 20


def clustered_se(values: list, clusters: list) -> dict:
    """Cluster-robust standard error of a mean.  ``values`` are per-unit
    observations (e.g. 1/0 correct), ``clusters`` the cluster key of each
    unit (e.g. the scenario family).  Reports the naive SE, the
    cluster-robust SE and their ratio — how much the naive error bar
    understated the uncertainty.

    The clustered SE is the CR2 sandwich (Bell and McCaffrey): each
    cluster's residual sum is scaled by 1/sqrt(1 − n_g/n), its leverage in
    a mean.  The interval uses the t critical value on the Bell–McCaffrey
    (Satterthwaite) degrees of freedom, ``df``.  With equal cluster sizes
    both reduce exactly to the CR1 sandwich (factor G/(G−1)) on G−1
    degrees of freedom.  Unequal sizes leave fewer effective clusters
    than G, and the interval widens to say so.  With one cluster there is
    nothing to estimate the between-cluster spread from: the clustered
    figures are None, not 0."""
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
    # a mean cannot leave the range of its data: an accuracy's interval stops at 1
    floor, ceiling = min(values), max(values)

    def _within(lo: float, hi: float) -> list:
        return [round(max(floor, lo), 4), round(min(ceiling, hi), 4)]
    out = {"n": n, "clusters": g, "mean": round(mean, 4), "naive_se": round(naive, 6),
           "ci95_naive": _within(mean - _Z * naive, mean + _Z * naive)}
    if g < 2:
        return dict(out, clustered_se=None, ratio=None, ci95_clustered=None, critical=None,
                    note="one cluster: the spread between clusters cannot be estimated, so "
                         "there is no clustered error bar; the naive one is shown")
    sizes = [len(resid) for resid in groups.values()]
    sums = [sum(resid) for resid in groups.values()]
    clustered, df = _cr2(sums, sizes, n)
    t = t_critical(df)
    note = ("clusters share a source; the clustered interval is the honest one"
            if g >= FEW_CLUSTERS else
            f"only {g} clusters: the clustered interval runs short of 95% this few "
            "(in simulation 0.86 at 5 equal clusters, 0.94 at 19), so read it as a "
            "lower bound on the uncertainty")
    return dict(out, clustered_se=round(clustered, 6),
                ratio=round(clustered / naive, 3) if naive > 0 else None,
                ci95_clustered=_within(mean - t * clustered, mean + t * clustered),
                critical=round(t, 4), df=df, note=note)


def _cr2(sums: list, sizes: list, n: int) -> tuple:
    """The CR2 standard error of a mean from each cluster's residual sum and
    size, and its Bell–McCaffrey degrees of freedom, rounded to 2 places.
    Residual sums under iid errors have covariance proportional to
    M = diag(n_g) − n_g n_h / n.  The variance is a weighted sum of squares,
    with W = diag(1 / (n²(1 − n_g/n))), so its Satterthwaite df is
    tr(WM)² / tr((WM)²)."""
    w = [1.0 / (n * n * (1 - m / n)) for m in sizes]
    se = math.sqrt(sum(wg * s * s for wg, s in zip(w, sums)))
    # WM = W·diag(n_g) − (W·n)(n)ᵀ/n; its traces need only sums over clusters
    a = [wg * m for wg, m in zip(w, sizes)]           # diagonal part, w_g n_g
    tr = sum(a) - sum(ag * m for ag, m in zip(a, sizes)) / n
    # tr((D − u vᵀ)²) = tr(D²) − 2 vᵀ D u + (vᵀ u)², with D = diag(a), u = a/n, v = n_g
    vu = sum(m * ag for m, ag in zip(sizes, a)) / n
    vdu = sum(m * ag * ag for m, ag in zip(sizes, a)) / n
    tr2 = sum(ag * ag for ag in a) - 2 * vdu + vu * vu
    return se, round(max(1.0, tr * tr / tr2), 2) if tr2 > 0 else float(len(sizes) - 1)


@_sections.register("aggregate", "paired_inference", requires=("diagnosis_consolidated",))
def _section(agg: dict, ctx: "_sections.AggregateContext"):
    # the paired design the runs layout IS: both agents on the same tasks,
    # so the comparison is a paired difference with a sign test, never two
    # rates eyeballed against each other
    runs_by_task = ctx.runs_by_task
    return paired_inference(
        [(sum(1.0 for t in runs_by_task[tid]["a"] if t.outcome.success) / len(runs_by_task[tid]["a"]),
          sum(1.0 for t in runs_by_task[tid]["b"] if t.outcome.success) / len(runs_by_task[tid]["b"]))
         for tid in sorted(runs_by_task)],
        labels=tuple(ctx.names))
