"""Comparing two policies from few episodes, the way the RL field asks for.

``deepcompare.rl`` reports a policy's mean return with a normal-approximation
interval. With three runs per task that interval is the wrong tool twice
over: the mean is dragged by one lucky or unlucky episode, and the normal
approximation assumes a sample size nobody running agents actually has.
Agarwal, Schwarzer, Castro, Courville and Bellemare (2021) measured how
badly that goes — published deep-RL comparisons drawn from a handful of
runs routinely reverse when the runs are redrawn — and proposed the
toolkit this module implements, from scratch and in the standard library:

* **IQM**, the interquartile mean: sort every run's score, cut the bottom
  and top quarter, average the middle half. It ignores the outlier episode
  the mean chases while using far more of the sample than the median, which
  is why it is the headline number here rather than the mean.
* **median**, **mean** and the **optimality gap** — how far the runs fall
  short of a stated target. The target is explicit and configurable and the
  output always names it (by default the best score any run of any policy
  actually reached, which makes the gap a within-sample statement).
* a **stratified bootstrap** interval on every one of those. Tasks are the
  strata: runs are exchangeable *within* a task and not across tasks, so a
  resample redraws each task's runs with replacement, keeps every task's run
  count, and recomputes the statistic. Pooling the runs first and resampling
  the pool would quietly assume a run on one task could have landed on
  another.
* the **performance profile** — the fraction of runs scoring at least τ, for
  every τ — which is a distribution rather than a point, and shows directly
  whether one policy is ahead everywhere or only above some threshold.
* the **probability of improvement**, P(B > A): the average over tasks of
  the chance a random run of B beats a random run of A, ties counting half
  (a Mann-Whitney statistic, averaged over the strata). It answers a
  different question from "B's mean is higher", and on small samples the two
  regularly disagree.

Every interval here is a *bootstrap over the runs that were recorded*. It
describes how much this sample's statistic moves when these runs are
redrawn; it is not a confidence statement about a population of runs that
were never made, and with three runs per task it will be wide. The block
carries :func:`sample_advisory` for exactly that reason, in the same voice
as the reliability layer's runs advisory, and nothing here is presented as
a finding on its own.

Determinism: one fixed seed (:data:`BOOTSTRAP_SEED`), one deterministic
resample stream per section, tasks visited in sorted order, so the same
episodes produce the same bytes. Scores come from the episodes
``rl_aggregate`` already built, so nothing is re-derived and no reward is
invented; the metric is selectable (:data:`METRICS`) because the same
machinery answers "is it better" for return, discounted return, success,
steps or seconds.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from typing import Callable, Optional, Sequence

VERSION = 1
#: fixed seed — the whole section must be byte-identical run to run.
BOOTSTRAP_SEED = 20260913
#: resamples per statistic; 2000 is enough for a percentile interval and
#: cheap enough that the runs layout does not notice it.
BOOTSTRAP_SAMPLES = 2000
#: two-sided coverage of the reported intervals.
CONFIDENCE = 0.95
#: the fraction cut from each end for the interquartile mean.
TRIM = 0.25
#: points on the shared τ grid of the performance profile.
PROFILE_POINTS = 41

#: the selectable scores: name -> (label, reader, higher_is_better).
#: A reader returns None when the episode does not carry the number, and a
#: policy's missing episodes are simply absent from its strata.
METRICS: dict = {
    "return": ("episode return", lambda e: _f(e.get("return")), True),
    "discounted_return": ("discounted return", lambda e: _f(e.get("discounted_return")), True),
    "success": ("success (1 / 0)", lambda e: 1.0 if e.get("success") is True else 0.0, True),
    "steps": ("steps", lambda e: _f(e.get("steps")), False),
    "seconds": ("seconds", lambda e: _f(e.get("seconds")), False),
}


def _f(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(float(v)) else None


# ---------------------------------------------------------------- formatting

def _num(v: Optional[float], places: int = 2) -> str:
    """A score as text, with a real minus sign; ``—`` when there is none."""
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        text = f"{int(round(v))}"
    else:
        text = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _pct(p: Optional[float]) -> str:
    return "—" if p is None else f"{round(100 * p):.0f}%"


# ---------------------------------------------------------------- statistics

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


def median(values: Sequence[float]) -> Optional[float]:
    n = len(values)
    if n == 0:
        return None
    v = sorted(values)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


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


def _profile_fractions(values: Sequence[float], taus: Sequence[float],
                       higher_is_better: bool = True) -> list:
    """Fraction of ``values`` reaching each τ (``≥ τ``, or ``≤ τ`` when
    lower is better). Monotone by construction, so the curve reads left to
    right without smoothing; counted by bisection so the bootstrap band
    stays cheap enough for the suite."""
    if not values:
        return [0.0 for _ in taus]
    v = sorted(values)
    n = len(v)
    if higher_is_better:
        return [(n - bisect_left(v, t)) / n for t in taus]
    return [bisect_right(v, t) / n for t in taus]


def _interval(values: Sequence[float], confidence: float = CONFIDENCE) -> tuple:
    """Percentile interval of a bootstrap distribution, the same index rule
    the gate statistics use."""
    if not values:
        return None, None
    v = sorted(values)
    n = len(v)
    tail = (1.0 - confidence) / 2.0
    return v[max(0, int(tail * n) - 1)], v[min(n - 1, int((1.0 - tail) * n))]


def _band(point: Optional[float], boots: Sequence[float], places: int = 4) -> dict:
    lo, hi = _interval(boots)
    if point is None:
        return {"point": None, "lo": None, "hi": None, "width": None, "degenerate": True,
                "reason": "no runs carry this score"}
    if lo is None or hi is None:
        return {"point": round(point, places), "lo": round(point, places), "hi": round(point, places),
                "width": 0.0, "degenerate": True, "reason": "no resamples were drawn"}
    flat = abs(hi - lo) < 1e-12
    return {"point": round(point, places), "lo": round(lo, places), "hi": round(hi, places),
            "width": round(abs(hi - lo), places), "degenerate": flat,
            "reason": "every resample gave the same value, so the interval has no width"
                      if flat else None}


# ---------------------------------------------------------------- resampling

def stratified_resamples(by_task: dict, samples: int, rng: random.Random) -> list:
    """``samples`` resamples of the score matrix, runs redrawn with
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


def _rng(seed: int, label: str) -> random.Random:
    """A stream per section, so adding a section never moves another's
    numbers. ``random.Random`` hashes a string with SHA-512, which is
    deterministic across processes."""
    return random.Random(f"agentdiff.rlstats:{seed}:{label}")


# ---------------------------------------------------------------- the matrix

def score_matrix(rl_block: dict, metric: str = "return") -> dict:
    """The tasks x runs score matrix per policy, read off ``aggregate["rl"]``.

    Returns ``{metric, metric_label, higher_is_better, policies, tasks,
    by_policy: {policy: {task: [scores]}}, by_task: {task: {policy:
    [scores]}}, runs_per_task, n, episodes_dropped}``. A policy with no
    episode on a task simply has no entry for that task: the task is not one
    of its strata, and nothing is imputed.
    """
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; choose one of "
                         f"{', '.join(sorted(METRICS))}")
    label, read, higher = METRICS[metric]
    agents = (rl_block or {}).get("agents")
    agents = agents if isinstance(agents, dict) else {}
    by_policy: dict = {}
    dropped = 0
    for name in agents:
        block = agents[name] if isinstance(agents[name], dict) else {}
        per_task: dict = {}
        for ep in (block.get("episodes") or []):
            if not isinstance(ep, dict):
                continue
            score = read(ep)
            if score is None:
                dropped += 1
                continue
            per_task.setdefault(str(ep.get("task_id")), []).append(score)
        by_policy[str(name)] = {t: sorted(v) for t, v in sorted(per_task.items())}
    policies = list(by_policy)
    tasks = sorted({t for per in by_policy.values() for t in per})
    by_task: dict = {}
    for t in tasks:
        by_task[t] = {p: list(by_policy[p].get(t, [])) for p in policies if by_policy[p].get(t)}
    return {
        "metric": metric, "metric_label": label, "higher_is_better": higher,
        "policies": policies, "tasks": tasks,
        "by_policy": by_policy, "by_task": by_task,
        "runs_per_task": {p: {t: len(v) for t, v in by_policy[p].items()} for p in policies},
        "n": {p: sum(len(v) for v in by_policy[p].values()) for p in policies},
        "episodes_dropped": dropped,
    }


def _flat(per_task: dict) -> list:
    return [v for t in sorted(per_task) for v in per_task[t]]


def default_target(matrix: dict) -> tuple:
    """``(value, source)`` for the optimality gap: the best score any run of
    any policy actually reached, which keeps the gap a statement about this
    sample rather than an imported benchmark ceiling."""
    every = [v for per in matrix["by_policy"].values() for t in per for v in per[t]]
    if not every:
        return None, "no runs"
    return (max(every) if matrix["higher_is_better"] else min(every)), "the best score observed in this sample"


# ---------------------------------------------------------------- aggregates

#: the four rows of the interval plot, in the order the paper reads them.
AGGREGATES = ("iqm", "median", "mean", "optimality_gap")


def aggregate_metrics(matrix: dict, policy: str, target: Optional[float] = None,
                      samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED) -> dict:
    """IQM, median, mean and optimality gap for one policy, each with its
    stratified-bootstrap interval over the observed runs."""
    per_task = matrix["by_policy"].get(policy) or {}
    flat = _flat(per_task)
    higher = matrix["higher_is_better"]
    if target is None:
        target = default_target(matrix)[0]
    draws = stratified_resamples(per_task, samples if flat else 0,
                                 _rng(seed, f"aggregate:{matrix['metric']}:{policy}"))
    fns: dict = {
        "iqm": iqm,
        "median": median,
        "mean": mean,
        "optimality_gap": (lambda v: optimality_gap(v, target, higher)) if target is not None
        else (lambda v: None),
    }
    out: dict = {}
    for name in AGGREGATES:
        fn: Callable = fns[name]
        point = fn(flat)
        boots = [b for b in (fn(d) for d in draws) if b is not None]
        out[name] = _band(point, boots)
    out["n"] = len(flat)
    out["tasks"] = len(per_task)
    return out


# ------------------------------------------------------------------- profile

def tau_grid(matrix: dict, points: int = PROFILE_POINTS) -> list:
    """A shared τ axis from the lowest to the highest observed score — the
    axis starts where the data starts, never at zero."""
    every = [v for per in matrix["by_policy"].values() for t in per for v in per[t]]
    if not every:
        return []
    lo, hi = min(every), max(every)
    n = max(2, int(points))
    if abs(hi - lo) < 1e-12:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def performance_profile(matrix: dict, points: int = PROFILE_POINTS,
                        samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED) -> dict:
    """Per policy, ``[(tau, fraction, lo, hi)]`` on one shared τ grid.

    ``fraction`` is the share of that policy's runs reaching τ; the band is
    the stratified bootstrap of the same fraction. Where one curve sits at or
    above the other at every τ, the ordering holds at every threshold;
    ``crossings`` names the τ values where it does not.
    """
    taus = tau_grid(matrix, points)
    higher = matrix["higher_is_better"]
    curves: dict = {}
    for policy in matrix["policies"]:
        per_task = matrix["by_policy"].get(policy) or {}
        flat = _flat(per_task)
        point = _profile_fractions(flat, taus, higher)
        draws = stratified_resamples(per_task, samples if flat else 0,
                                     _rng(seed, f"profile:{matrix['metric']}:{policy}"))
        boots = [_profile_fractions(d, taus, higher) for d in draws]
        rows = []
        for i, t in enumerate(taus):
            column = [b[i] for b in boots]
            lo, hi = _interval(column)
            rows.append([round(t, 4), round(point[i], 4),
                         round(point[i] if lo is None else lo, 4),
                         round(point[i] if hi is None else hi, 4)])
        curves[policy] = rows
    result = {"rule": ("fraction of runs scoring at least τ" if higher
                       else "fraction of runs scoring at most τ"),
              "taus": [round(t, 4) for t in taus], "curves": curves,
              "crossings": [], "dominant": None, "reading": ""}
    names = matrix["policies"]
    if len(names) != 2 or not taus:
        result["reading"] = ("a profile needs exactly two policies to be compared"
                             if len(names) != 2 else "no runs to profile")
        return result
    a, b = names
    diffs = [curves[b][i][1] - curves[a][i][1] for i in range(len(taus))]
    crossings = []
    last = 0
    for i, d in enumerate(diffs):
        sign = 0 if abs(d) < 1e-12 else (1 if d > 0 else -1)
        if sign and last and sign != last:
            crossings.append(round(taus[i], 4))
        if sign:
            last = sign
    result["crossings"] = crossings
    if not crossings and any(abs(d) > 1e-12 for d in diffs):
        ahead = b if max(diffs) > 0 else a
        behind = a if ahead == b else b
        result["dominant"] = ahead
        result["reading"] = (f"{ahead}'s curve is at or above {behind}'s at every τ, so more of its "
                             f"runs clear every threshold — the ordering does not depend on where "
                             f"the bar is set")
    elif crossings:
        result["reading"] = (f"the curves cross at "
                             + ", ".join(f"τ = {_num(t)}" for t in crossings[:3])
                             + (" and beyond" if len(crossings) > 3 else "")
                             + ", so which policy is ahead depends on the threshold chosen")
    else:
        result["reading"] = "the two profiles are identical at every τ"
    return result


# --------------------------------------------------- probability of improvement

def within_task_probability(a_runs: Sequence[float], b_runs: Sequence[float],
                            higher_is_better: bool = True) -> Optional[float]:
    """P(a random run of B beats a random run of A) on one task, ties half.

    Every B run is compared against every A run — the Mann-Whitney U
    statistic scaled to a probability — so one enormous episode cannot move
    it further than one win.
    """
    if not a_runs or not b_runs:
        return None
    a = sorted(a_runs)
    n = len(a)
    wins = 0.0
    for y in b_runs:
        left = bisect_left(a, y)
        ties = bisect_right(a, y) - left
        beaten = left if higher_is_better else n - left - ties
        wins += beaten + 0.5 * ties
    return wins / (n * len(b_runs))


def probability_of_improvement(matrix: dict, samples: int = BOOTSTRAP_SAMPLES,
                               seed: int = BOOTSTRAP_SEED) -> dict:
    """P(B > A) as the paper defines it: the within-task probability that a
    random run of B beats a random run of A, averaged over the tasks both
    policies ran, with a stratified-bootstrap interval.

    Tasks only one policy ran are named in ``tasks_skipped`` and excluded —
    a task with no B run says nothing about whether B improves on it.
    """
    names = matrix["policies"]
    if len(names) != 2:
        return {"measurable": False, "a": None, "b": None,
                "reason": f"needs exactly two policies, found {len(names)}",
                "point": None, "lo": None, "hi": None, "per_task": {},
                "tasks_used": 0, "tasks_skipped": [], "regressions": [], "ties": [],
                "reading": ""}
    a, b = names
    higher = matrix["higher_is_better"]
    a_by, b_by = matrix["by_policy"][a], matrix["by_policy"][b]
    shared = sorted(set(a_by) & set(b_by))
    skipped = sorted((set(a_by) | set(b_by)) - set(shared))
    if not shared:
        return {"measurable": False, "a": a, "b": b,
                "reason": "no task has runs from both policies",
                "point": None, "lo": None, "hi": None, "per_task": {},
                "tasks_used": 0, "tasks_skipped": skipped, "regressions": [], "ties": [],
                "reading": ""}
    per_task = {t: round(within_task_probability(a_by[t], b_by[t], higher), 4) for t in shared}
    point = sum(per_task[t] for t in shared) / len(shared)
    # the tasks the average hides: a policy can win the aggregate and still be
    # a regression somewhere, and that is the task a reader has to see.
    regressions = [t for t in shared if per_task[t] < 0.5]
    ties = [t for t in shared if abs(per_task[t] - 0.5) < 1e-12]
    rng = _rng(seed, f"improvement:{matrix['metric']}")
    boots = []
    for _ in range(max(0, samples)):
        total = 0.0
        for t in shared:
            ax, bx = a_by[t], b_by[t]
            draw_a = [ax[rng.randrange(len(ax))] for _ in range(len(ax))]
            draw_b = [bx[rng.randrange(len(bx))] for _ in range(len(bx))]
            total += within_task_probability(draw_a, draw_b, higher)
        boots.append(total / len(shared))
    band = _band(point, boots)
    out = {"measurable": True, "a": a, "b": b, "reason": None,
           "point": band["point"], "lo": band["lo"], "hi": band["hi"],
           "width": band["width"], "degenerate": band["degenerate"],
           "per_task": per_task, "tasks_used": len(shared), "tasks_skipped": skipped,
           "regressions": regressions, "ties": ties, "reading": ""}
    out["reading"] = _improvement_reading(out)
    return out


def _improvement_reading(imp: dict) -> str:
    p, lo, hi = imp["point"], imp["lo"], imp["hi"]
    a, b = imp["a"], imp["b"]
    head = (f"a run of {b} picked at random beats a run of {a} on the same task "
            f"{_pct(p)} of the time ({_pct(lo)}–{_pct(hi)} across the bootstrap)")
    if lo is not None and lo > 0.5:
        tail = "every resample of these runs keeps it above the coin flip"
    elif hi is not None and hi < 0.5:
        tail = f"every resample of these runs keeps it below the coin flip, so {a} is the one ahead"
    else:
        tail = "the interval spans 50%, so these runs do not settle which policy is ahead"
    back = ""
    lost = imp.get("regressions") or []
    if lost:
        back = (f"; it is an average over tasks, and on "
                + ", ".join(lost[:3]) + (" and others" if len(lost) > 3 else "")
                + f" it falls below 50% — {b} is the worse policy there")
    return f"{head}; {tail}{back}."


# ------------------------------------------------------------------ advisory

def sample_advisory(matrix: dict) -> dict:
    """How many runs per task there are, and what that permits — the
    reliability layer's runs advisory, plus what it means for a bootstrap.

    Never let a wide interval read as a finding: at three runs per task every
    number in this section is descriptive of the episodes recorded, and the
    interval is the bootstrap's spread over exactly those episodes.
    """
    # imported here rather than at module scope: the reliability layer reaches
    # back through stability into report, and report imports this module's caller.
    from .reliability import runs_advisory
    counts = [n for p in matrix["policies"] for n in matrix["runs_per_task"][p].values()]
    base = runs_advisory(counts)
    n_min = base.get("n_min")
    note = ("Everything in this section is a statistic of the runs recorded here: the "
            "interval is a stratified bootstrap over those runs, resampled within each "
            "task, and not a confidence statement about runs that were never made. "
            "IQM is reported first because it is the least moved by a single "
            "exceptional episode.")
    if isinstance(n_min, int) and n_min < 5:
        note += (f" At {_plural(n_min, 'run')} per task the intervals are wide by "
                 f"construction; read an overlap as 'these runs do not separate the "
                 f"policies', never as 'the policies are equal'.")
    return {
        "runs_per_task": {p: dict(sorted(matrix["runs_per_task"][p].items()))
                          for p in matrix["policies"]},
        "n_min": n_min, "n_max": base.get("n_max"), "n_median": base.get("n_median"),
        "tier": base.get("tier"), "supports": base.get("supports") or [],
        "does_not_support": base.get("does_not_support") or [],
        "message": (base.get("message") or "") + " " + note,
    }


# -------------------------------------------------------------------- public

def rl_stats(rl_block: dict, metric: str = "return", target: Optional[float] = None,
             samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED,
             profile_points: int = PROFILE_POINTS) -> dict:
    """``aggregate["rl"]["stats"]``: the whole small-sample toolkit over the
    episodes ``rl_aggregate`` already built.

    ``metric`` is any key of :data:`METRICS`; ``target`` sets the optimality
    gap's reference and defaults to the best score observed (the output says
    which). ``samples`` resamples, one fixed ``seed``, so the section is
    byte-identical run to run. Returns ``measurable: False`` with a reason
    when there is nothing to compare.
    """
    matrix = score_matrix(rl_block, metric)
    empty = {"version": VERSION, "metric": metric, "metric_label": METRICS[metric][0],
             "higher_is_better": METRICS[metric][2], "measurable": False,
             "policies": matrix["policies"], "tasks": matrix["tasks"],
             "aggregates": {}, "profile": None, "improvement": None,
             "advisory": sample_advisory(matrix), "by_task": matrix["by_task"],
             "narrative": ""}
    if not matrix["policies"] or not matrix["tasks"]:
        empty["reason"] = "no episodes carry this score"
        empty["narrative"] = f"No episode carries {METRICS[metric][0]}, so nothing can be compared."
        return empty
    value, source = default_target(matrix)
    explicit = target is not None
    if explicit:
        value, source = float(target), "given"
    aggregates = {p: aggregate_metrics(matrix, p, target=value, samples=samples, seed=seed)
                  for p in matrix["policies"]}
    profile = performance_profile(matrix, points=profile_points, samples=samples, seed=seed)
    improvement = probability_of_improvement(matrix, samples=samples, seed=seed)
    out = {
        "version": VERSION, "metric": metric, "metric_label": METRICS[metric][0],
        "higher_is_better": METRICS[metric][2], "measurable": True, "reason": None,
        "policies": matrix["policies"], "tasks": matrix["tasks"],
        "n": matrix["n"], "episodes_dropped": matrix["episodes_dropped"],
        "bootstrap": {
            "samples": samples, "seed": seed, "confidence": CONFIDENCE,
            "method": "stratified by task: runs resampled with replacement within each task, "
                      "every task keeping its own run count",
            "basis": "a bootstrap over the runs recorded here, not a claim about a population",
        },
        "target": {"value": None if value is None else round(value, 4), "source": source,
                   "explicit": explicit,
                   "note": f"the optimality gap is the mean shortfall against {_num(value)} "
                           f"({source}); a run at or past it contributes 0"},
        "aggregates": aggregates,
        "profile": profile,
        "improvement": improvement,
        "advisory": sample_advisory(matrix),
        "by_task": matrix["by_task"],
        "narrative": "",
    }
    out["narrative"] = narrative(out)
    return out


def narrative(stats: dict) -> str:
    """One paragraph: the IQMs with their intervals, whether they overlap,
    the probability of improvement, and what the profiles do."""
    if not stats.get("measurable"):
        return stats.get("narrative") or "nothing to compare."
    parts: list = []
    label = stats["metric_label"]
    for policy in stats["policies"]:
        row = stats["aggregates"][policy]["iqm"]
        parts.append(f"{policy}: IQM {label} {_num(row['point'])} "
                     f"[{_num(row['lo'])}, {_num(row['hi'])}] over {_plural(stats['n'][policy], 'run')}")
    if len(stats["policies"]) == 2:
        a, b = stats["policies"]
        ra, rb = stats["aggregates"][a]["iqm"], stats["aggregates"][b]["iqm"]
        if None not in (ra["lo"], ra["hi"], rb["lo"], rb["hi"]):
            overlap = ra["lo"] <= rb["hi"] and rb["lo"] <= ra["hi"]
            parts.append("the IQM intervals overlap, so these runs do not separate the policies"
                         if overlap else
                         "the IQM intervals do not overlap")
    imp = stats.get("improvement") or {}
    if imp.get("measurable"):
        parts.append(f"P({imp['b']} > {imp['a']}) = {_pct(imp['point'])} "
                     f"[{_pct(imp['lo'])}, {_pct(imp['hi'])}] over "
                     f"{_plural(imp['tasks_used'], 'shared task')}")
        if imp.get("regressions"):
            parts.append(f"below 50% on " + ", ".join(imp["regressions"]))
    profile = stats.get("profile") or {}
    if profile.get("reading"):
        parts.append(profile["reading"])
    tier = (stats.get("advisory") or {}).get("tier")
    n_min = (stats.get("advisory") or {}).get("n_min")
    if n_min is not None:
        parts.append(f"{_plural(n_min, 'run')} per task at the thinnest task [{tier}], so every "
                     f"number here describes the episodes recorded")
    return "; ".join(parts) + "."


__all__ = ["rl_stats", "score_matrix", "aggregate_metrics", "performance_profile",
           "probability_of_improvement", "within_task_probability", "stratified_resamples",
           "sample_advisory", "default_target", "tau_grid", "narrative",
           "iqm", "median", "mean", "optimality_gap",
           "METRICS", "AGGREGATES", "BOOTSTRAP_SAMPLES", "BOOTSTRAP_SEED", "CONFIDENCE",
           "TRIM", "PROFILE_POINTS", "VERSION"]
