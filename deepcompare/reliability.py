"""Reliability over repeated runs: can the agent do it *every* time?

Every other multi-run number in AgentDiff answers "did it work".  This module
answers the question a team actually ships on: **if a user runs this agent k
times, does it work all k times?**  Those are different questions, and the gap
between them is the whole point.  An agent that solves a task on 3 of 4
attempts has a 75% success rate and a ``pass^4`` of exactly 0 — it has never
once demonstrated it can do the task four times running.  Reporting only the
mean hides that completely.

What is here, and where each definition comes from:

``pass^k``
    ``C(c, k) / C(n, k)`` — the probability that *all* k sampled runs pass,
    averaged over tasks.  This is tau-bench / tau2-bench's ``pass_hat_k``.
    We report the **whole curve** k = 1..max_k rather than one k, because the
    shape is the signal: a curve that falls off a cliff between k=1 and k=2 is
    a different engineering problem from one that decays gently.
    tau2-bench's guards ship with the formula, and matter as much as it does:
    ``max_k`` is the *minimum* per-task trial count (never report a k some
    task cannot support), unequal trial counts are flagged, and runs that died
    of a harness failure are removed before anything is computed.

``pass@k`` (coverage)
    The unbiased estimator ``1 - C(n-c, k) / C(n, k)`` (Chen et al. 2021,
    arXiv 2107.03374, as used throughout code-eval).  Reported alongside
    ``pass^k`` so the gap between "can it ever" and "can it every time" is
    visible on one screen.  Coverage rises with k; reliability falls.

``outcome_consistency``
    ``(2*p - 1)**2`` for a task's success rate p — HAL's
    ``reliability_eval/metrics/consistency.py``.  1.0 when every run agrees
    (all pass or all fail), 0.0 at p = 0.5, where the agent is a coin flip.
    Note that it does not care *which* way the runs agree: a task the agent
    reliably fails scores 1.0.  That is deliberate — this measures
    determinism, not quality, and it is read next to the success rate.

``trajectory_consistency``
    Mean pairwise ``1 - levenshtein(seq_i, seq_j) / max(|seq_i|, |seq_j|)``
    over the runs' action-name sequences (step ``type`` + ``name``), also HAL.
    HAL conditions this on *successful* runs and so do we: comparing the path
    of a run that worked against one that crashed at step two measures the
    crash, not the agent's determinism.  The condition is reported in
    ``basis`` rather than left implicit.

``resource_consistency``
    ``exp(-CV)`` averaged over the resources the trace actually carries
    (tokens, cost, latency, step count).  A resource whose values are all zero
    is *not* counted as perfectly consistent — it is counted as not logged,
    and dropped.  Otherwise an agent that reports no cost would score 1.0 for
    cost stability, which is a fabricated number.

``icc`` — one-way ICC(1)
    From the per-task run outcomes, via the standard one-way random-effects
    ANOVA: ``(MSB - MSW) / (MSB + (k0-1)*MSW)`` with ``k0`` the unbalanced-
    design correction.  It splits observed variance into "tasks differ in
    difficulty" versus "the same agent on the same task differs from itself".
    The ICC work on GAIA (arXiv 2512.06710) measures 0.304-0.774; an ICC of
    0.30 means roughly 70% of what you are looking at is the agent being
    inconsistent, not the tasks being hard.  We ship it with its caveats
    attached: it is an ANOVA estimator applied to binary outcomes, it is
    itself noisy at three runs per task, and negative estimates (which the
    model cannot represent) are reported raw *and* clamped rather than
    quietly floored.  Where it cannot be estimated — one task, one run per
    task, or zero total variance — it returns ``None`` with the reason.

The honesty rules that shape every return value here: a number that cannot be
computed comes back as ``None`` with a stated ``reason``, never as a plausible
zero; ``pass^k`` for k > n is ``None``, not 0.0; every rate carries its
denominator; and every qualifier (``excluded_runs``, ``unequal_trials``,
``max_k``, ``basis``) is a field in the output rather than a footnote in a
narrative someone may not read.

Pure stdlib, no wall-clock, no randomness — the same traces produce the same
bytes.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .stability import _cv, _levenshtein
from .statistics import pass_at_k
from .trace import HARNESS_TERMINATIONS, Trajectory

#: Below this many runs per task, no claim about a *difference* between two
#: agents survives contact with resampling.
MIN_RUNS_FOR_COMPARISON = 5
#: The defensible floor for structured tool-use tasks (tau2-bench territory).
RUNS_FLOOR_STRUCTURED = 8
#: Upper end of that floor; above it the marginal run buys little on
#: structured tasks.
RUNS_COMFORTABLE_STRUCTURED = 16
#: Open-ended reasoning tasks have far heavier outcome tails and need this.
RUNS_FLOOR_OPEN_ENDED = 32
#: ICC at or below which task difficulty explains less than a third of the
#: variance — i.e. most of what you see is the agent disagreeing with itself.
ICC_AGENT_DOMINATED = 0.5


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def coverage_at_k(successes: int, runs: int, k: int) -> Optional[float]:
    """Unbiased ``pass@k``: chance at least one of k sampled runs passes.

    ``1 - C(n-c, k) / C(n, k)`` (Chen et al. 2021, arXiv 2107.03374).  This is
    the optimistic reading — "can it ever" — and it is the number most people
    mean when they say pass@k.  Returns None when there are fewer than ``k``
    runs to sample from, because the estimator is undefined there and a 0.0
    would read as "it never works".
    """
    if runs < k or k <= 0:
        return None
    failures = runs - successes
    if failures < k:
        return 1.0
    return round(1.0 - math.comb(failures, k) / math.comb(runs, k), 4)


def outcome_consistency(successes: int, runs: int) -> Optional[float]:
    """``(2p - 1)**2`` over a task's runs (HAL consistency metric).

    1.0 = every run agreed; 0.0 = a coin flip.  Needs at least two runs:
    with one run "all runs agree" is vacuously true and scoring it 1.0 would
    manufacture certainty out of a single sample, so that case returns None.
    """
    if runs < 2:
        return None
    p = successes / runs
    return round((2 * p - 1) ** 2, 4)


def action_sequence(trajectory: Trajectory) -> list[str]:
    """The run's action-name sequence: ``type:name`` per step.

    Type alone collapses two different tools into "the same action"; name
    alone collapses a search and a read of the same resource.  Both together
    are what HAL compares.
    """
    return [f"{step.type}:{step.name}" for step in trajectory.steps]


def sequence_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    """``1 - levenshtein(a, b) / max(|a|, |b|)``, in [0, 1]."""
    longest = max(len(a), len(b))
    if longest == 0:
        # Two empty paths did not diverge; there is nothing to be
        # inconsistent about.
        return 1.0
    return round(1.0 - _levenshtein(list(a), list(b)) / longest, 4)


def sequence_consistency(runs: Sequence[Trajectory]) -> Optional[float]:
    """Mean pairwise action-sequence similarity over ``runs``.

    Returns None for fewer than two runs — a single path cannot disagree
    with itself, and reporting 1.0 there would overstate the evidence.
    """
    if len(runs) < 2:
        return None
    seqs = [action_sequence(t) for t in runs]
    scores = [
        sequence_similarity(seqs[i], seqs[j])
        for i in range(len(seqs))
        for j in range(i + 1, len(seqs))
    ]
    return round(sum(scores) / len(scores), 4)


def _resource_values(runs: Sequence[Trajectory]) -> dict[str, list[float]]:
    return {
        "tokens": [float(t.totals.input_tokens + t.totals.output_tokens) for t in runs],
        "cost_usd": [float(t.totals.cost_usd) for t in runs],
        "latency_s": [float(t.totals.latency_s) for t in runs],
        "steps": [float(len(t.steps)) for t in runs],
    }


def resource_consistency(runs: Sequence[Trajectory]) -> dict:
    """``exp(-CV)`` per resource, averaged over the resources actually logged.

    A resource is "available" only if some run reports a non-zero value for
    it.  An all-zero column means the harness did not log it; scoring that as
    exp(-0) = 1.0 would credit the agent with perfect cost stability for
    having no cost data at all.
    """
    if len(runs) < 2:
        return {"value": None, "runs": len(runs), "by_resource": {},
                "reason": "needs at least 2 runs to measure variation"}
    by_resource: dict[str, float] = {}
    for name, values in _resource_values(runs).items():
        if not any(v > 0 for v in values):
            continue  # not logged, not "perfectly consistent"
        by_resource[name] = round(math.exp(-_cv(values)), 4)
    if not by_resource:
        return {"value": None, "runs": len(runs), "by_resource": {},
                "reason": "no resource (tokens, cost, latency, steps) is logged"}
    value = round(sum(by_resource.values()) / len(by_resource), 4)
    return {"value": value, "runs": len(runs), "by_resource": by_resource,
            "reason": None}


def icc_one_way(groups: Sequence[Sequence[float]]) -> dict:
    """One-way random-effects ICC(1) over per-task run outcomes.

    ``groups`` is one sequence of numeric outcomes (1.0 / 0.0 for success) per
    task.  Returns the variance split between "tasks differ" and "the same
    agent differs from itself on the same task", plus every caveat as a field.

    The estimator is the textbook ANOVA one, with the unbalanced-design
    correction ``k0 = (N - sum(n_i^2)/N) / (m - 1)``.  It is applied to binary
    outcomes here, which is defensible (the ICC of a Bernoulli is a real
    quantity and the GAIA work in arXiv 2512.06710 does the same) but noisier
    than the continuous case — hence the ``caveat`` field, which is populated
    whenever a task carries fewer than :data:`MIN_RUNS_FOR_COMPARISON` runs.

    Negative estimates are possible when within-task variance exceeds
    between-task variance.  They are not a real correlation, but silently
    flooring them hides "this agent is *more* variable within a task than
    across tasks", which is the most alarming finding this metric can make.
    So ``icc1`` is the raw value and ``icc1_clamped`` the usable one, and the
    shares are computed from the clamped value.
    """
    usable = [list(g) for g in groups if len(g) >= 1]
    m = len(usable)
    n_total = sum(len(g) for g in usable)
    if m < 2:
        return {"icc1": None, "reason": "needs at least 2 tasks to separate "
                "between-task from within-task variance"}
    if n_total - m < 1:
        return {"icc1": None, "reason": "needs at least one task with 2+ runs "
                "to estimate within-task variance"}

    grand = sum(sum(g) for g in usable) / n_total
    means = [sum(g) / len(g) for g in usable]
    ss_between = sum(len(g) * (mean - grand) ** 2 for g, mean in zip(usable, means))
    ss_within = sum(
        sum((v - mean) ** 2 for v in g) for g, mean in zip(usable, means)
    )
    ms_between = ss_between / (m - 1)
    ms_within = ss_within / (n_total - m)
    k0 = (n_total - sum(len(g) ** 2 for g in usable) / n_total) / (m - 1)

    denominator = ms_between + (k0 - 1) * ms_within
    if denominator == 0:
        return {"icc1": None, "reason": "every eligible run has the same "
                "outcome, so there is no variance to partition"}

    icc = (ms_between - ms_within) / denominator
    clamped = min(1.0, max(0.0, icc))
    smallest = min(len(g) for g in usable)
    return {
        "icc1": round(icc, 4),
        "icc1_clamped": round(clamped, 4),
        "between_task_variance_share": round(clamped, 4),
        "within_task_variance_share": round(1.0 - clamped, 4),
        "tasks": m,
        "observations": n_total,
        "k0": round(k0, 4),
        "negative_raw": icc < 0,
        "caveat": (
            f"estimated from {smallest} run(s) on the smallest task; ICC from "
            f"fewer than {MIN_RUNS_FOR_COMPARISON} runs per task is itself a "
            f"noisy estimate, and these are binary outcomes rather than "
            f"continuous scores"
            if smallest < MIN_RUNS_FOR_COMPARISON else
            "estimated from binary run outcomes rather than continuous scores"
        ),
        "reason": None,
    }


def runs_advisory(trials_per_task: Sequence[int]) -> dict:
    """What the run count present actually supports — and what it does not.

    The literature gives no single N, so this reports the spread rather than
    inventing a number: below :data:`MIN_RUNS_FOR_COMPARISON` runs nothing can
    be said about a *difference* between two agents; 8-16 is the defensible
    floor for structured tool-use tasks; 32+ for open-ended reasoning, whose
    outcome distribution has a much heavier tail.
    """
    if not trials_per_task:
        return {"n_min": None, "tier": "none", "reason": "no eligible runs"}
    counts = sorted(trials_per_task)
    n_min, n_max = counts[0], counts[-1]
    median = counts[len(counts) // 2]

    supports: list[str] = []
    does_not: list[str] = []
    if n_min >= 1:
        supports.append("describing what these specific runs did")
    if n_min < MIN_RUNS_FOR_COMPARISON:
        tier = "insufficient"
        does_not += [
            "any claim that one agent is more reliable than another",
            f"a pass^k curve beyond k={n_min}",
        ]
    elif n_min < RUNS_FLOOR_STRUCTURED:
        tier = "below-floor"
        supports.append("a directional read on reliability")
        does_not.append(
            f"a defensible reliability claim; {RUNS_FLOOR_STRUCTURED} runs per "
            f"task is the floor for structured tool-use tasks"
        )
    elif n_min <= RUNS_COMFORTABLE_STRUCTURED:
        tier = "structured-ok"
        supports.append("reliability claims on structured tool-use tasks")
        does_not.append(
            f"open-ended reasoning tasks, which need {RUNS_FLOOR_OPEN_ENDED}+ runs"
        )
    elif n_min < RUNS_FLOOR_OPEN_ENDED:
        tier = "structured-ok"
        supports.append("reliability claims on structured tool-use tasks")
        does_not.append(
            f"open-ended reasoning tasks, which need {RUNS_FLOOR_OPEN_ENDED}+ runs"
        )
    else:
        tier = "open-ended-ok"
        supports += ["reliability claims on structured tool-use tasks",
                     "reliability claims on open-ended reasoning tasks"]

    message = (
        f"{n_min} run(s) per task at the thinnest task "
        f"(median {median}, max {n_max}). "
    )
    if tier == "insufficient":
        message += (
            f"That is below the {MIN_RUNS_FOR_COMPARISON}-run mark where a "
            f"difference between two agents starts to mean anything: treat "
            f"everything here as descriptive of these runs only. "
            f"{RUNS_FLOOR_STRUCTURED}-{RUNS_COMFORTABLE_STRUCTURED} runs is the "
            f"defensible floor for structured tool-use tasks, "
            f"{RUNS_FLOOR_OPEN_ENDED}+ for open-ended reasoning."
        )
    elif tier == "below-floor":
        message += (
            f"Enough to point a direction, short of the "
            f"{RUNS_FLOOR_STRUCTURED}-{RUNS_COMFORTABLE_STRUCTURED} runs that "
            f"make a reliability claim defensible on structured tool-use tasks."
        )
    elif tier == "structured-ok":
        message += (
            f"That meets the {RUNS_FLOOR_STRUCTURED}-"
            f"{RUNS_COMFORTABLE_STRUCTURED} floor for structured tool-use "
            f"tasks; open-ended reasoning still wants "
            f"{RUNS_FLOOR_OPEN_ENDED}+."
        )
    else:
        message += (
            f"That clears {RUNS_FLOOR_OPEN_ENDED} runs, enough for open-ended "
            f"reasoning tasks as well as structured ones."
        )
    return {
        "n_min": n_min,
        "n_max": n_max,
        "n_median": median,
        "tasks": len(counts),
        "tier": tier,
        "supports": supports,
        "does_not_support": does_not,
        "thresholds": {
            "no_comparison_below": MIN_RUNS_FOR_COMPARISON,
            "structured_floor": RUNS_FLOOR_STRUCTURED,
            "structured_comfortable": RUNS_COMFORTABLE_STRUCTURED,
            "open_ended_floor": RUNS_FLOOR_OPEN_ENDED,
        },
        "message": message,
        "reason": None,
    }


# --------------------------------------------------------------------------
# harness-failure exclusion
# --------------------------------------------------------------------------

def is_harness_failure(trajectory: Trajectory) -> bool:
    """Did the harness die, rather than the agent fail?"""
    return trajectory.outcome.termination in HARNESS_TERMINATIONS


def split_harness_failures(
    runs: Sequence[Trajectory],
) -> tuple[list[Trajectory], list[Trajectory]]:
    """``(eligible, excluded)`` — harness failures separated out.

    Counting a rate-limited run as an agent failure makes the agent look worse
    and the harness look fine, which is exactly backwards for whoever has to
    fix it.
    """
    eligible = [t for t in runs if not is_harness_failure(t)]
    excluded = [t for t in runs if is_harness_failure(t)]
    return eligible, excluded


# --------------------------------------------------------------------------
# per-agent assembly
# --------------------------------------------------------------------------

def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _agent_reliability(name: str,
                       runs_by_task: dict[str, list[Trajectory]]) -> dict:
    """Every reliability figure for one agent, across its tasks."""
    task_ids = sorted(runs_by_task)

    eligible: dict[str, list[Trajectory]] = {}
    excluded_rows: list[dict] = []
    by_termination: dict[str, int] = {}
    runs_total = 0
    for tid in task_ids:
        runs = sorted(runs_by_task[tid], key=lambda t: t.run_id)
        runs_total += len(runs)
        keep, drop = split_harness_failures(runs)
        eligible[tid] = keep
        for t in drop:
            reason = t.outcome.termination or "unknown"
            by_termination[reason] = by_termination.get(reason, 0) + 1
            excluded_rows.append({"task": tid, "run_id": t.run_id,
                                  "termination": reason})

    scored_tasks = [tid for tid in task_ids if eligible[tid]]
    empty_tasks = [tid for tid in task_ids if not eligible[tid]]
    trials = {tid: len(eligible[tid]) for tid in scored_tasks}
    runs_used = sum(trials.values())
    successes_total = sum(
        1 for tid in scored_tasks for t in eligible[tid] if t.outcome.success
    )

    excluded_block = {
        "count": len(excluded_rows),
        "of_runs": runs_total,
        "by_termination": {k: by_termination[k] for k in sorted(by_termination)},
        "runs": excluded_rows,
        "tasks_left_empty": empty_tasks,
        "basis": (
            "runs whose outcome.termination is a harness failure "
            f"({', '.join(HARNESS_TERMINATIONS)}) are removed before any "
            "reliability number is computed: they are the harness failing, "
            "not the agent"
        ),
    }

    counts = sorted(trials.values())
    unequal = bool(counts) and counts[0] != counts[-1]
    unequal_block = {
        "flagged": unequal,
        "min": counts[0] if counts else None,
        "max": counts[-1] if counts else None,
        "per_task": {tid: trials[tid] for tid in scored_tasks},
        "note": (
            "trial counts differ across tasks, so every k-curve below is "
            "capped at the thinnest task and tasks contribute unequal "
            "evidence to each mean"
            if unequal else
            "every task has the same number of eligible runs"
        ),
    }

    max_k = counts[0] if counts else 0
    max_k_block = {
        "max_k": max_k,
        "basis": (
            "the minimum per-task eligible trial count (tau2-bench): no k is "
            "reported that some task cannot support"
        ),
    }

    # ---- per-task detail -------------------------------------------------
    per_task: list[dict] = []
    hat_by_k: dict[int, list[float]] = {k: [] for k in range(1, max_k + 1)}
    cov_by_k: dict[int, list[float]] = {k: [] for k in range(1, max_k + 1)}
    outcome_scores: list[float] = []
    sequence_scores: list[float] = []
    resource_scores: list[float] = []
    resource_totals: dict[str, list[float]] = {}
    icc_groups: list[list[float]] = []

    for tid in task_ids:
        runs = eligible[tid]
        n = len(runs)
        c = sum(1 for t in runs if t.outcome.success)
        if n:
            icc_groups.append([1.0 if t.outcome.success else 0.0 for t in runs])

        hat_curve = []
        cov_curve = []
        for k in range(1, max_k + 1):
            hat = pass_at_k(c, n, k)
            cov = coverage_at_k(c, n, k)
            hat_curve.append({"k": k, "value": hat})
            cov_curve.append({"k": k, "value": cov})
            if hat is not None:
                hat_by_k[k].append(hat)
            if cov is not None:
                cov_by_k[k].append(cov)

        consistency = outcome_consistency(c, n)
        if consistency is not None:
            outcome_scores.append(consistency)

        successful = [t for t in runs if t.outcome.success]
        seq = sequence_consistency(successful)
        if seq is not None:
            sequence_scores.append(seq)

        resources = resource_consistency(runs)
        if resources["value"] is not None:
            resource_scores.append(resources["value"])
            for key, value in resources["by_resource"].items():
                resource_totals.setdefault(key, []).append(value)

        per_task.append({
            "task": tid,
            "runs": n,
            "runs_excluded": len(runs_by_task[tid]) - n,
            "successes": c,
            "success_rate": round(c / n, 4) if n else None,
            "pass_hat_k": hat_curve,
            "pass_at_k": cov_curve,
            "outcome_consistency": {
                "value": consistency,
                "runs": n,
                "reason": None if consistency is not None
                else "needs at least 2 eligible runs",
            },
            "trajectory_consistency": {
                "value": seq,
                "runs_compared": len(successful),
                "basis": "successful runs only (HAL)",
                "reason": None if seq is not None
                else f"needs at least 2 successful runs, has {len(successful)}",
            },
            "resource_consistency": resources,
        })

    # ---- curves ----------------------------------------------------------
    def _curve(bucket: dict[int, list[float]]) -> list[dict]:
        return [
            {"k": k, "value": round(_mean(bucket[k]), 4) if bucket[k] else None,
             "tasks": len(bucket[k])}
            for k in range(1, max_k + 1)
        ]

    hat_curve_all = _curve(hat_by_k)
    cov_curve_all = _curve(cov_by_k)
    no_curve_reason = (
        None if max_k >= 1 else
        "no task has an eligible run after excluding harness failures"
    )

    # a plug-in interval per k: the 95% Wilson interval of the pooled
    # per-run rate raised to the k-th power.  Stated as plug-in because it
    # treats runs as exchangeable across tasks; the curve's own value stays
    # the tau-bench mean over tasks
    from .statistics import wilson_interval
    lo, hi = wilson_interval(successes_total, runs_used) if runs_used else (0.0, 1.0)
    for point in hat_curve_all:
        if point["value"] is None:
            point["ci95"] = None
        else:
            point["ci95"] = [round(lo ** point["k"], 4), round(hi ** point["k"], 4)]
    pass_hat_block = {
        "curve": hat_curve_all,
        "max_k": max_k,
        "ci95_basis": ("plug-in: the 95% Wilson interval of the pooled per-run "
                       "success rate, raised to k; treats runs as exchangeable "
                       "across tasks"),
        "value_at_max_k": hat_curve_all[-1]["value"] if hat_curve_all else None,
        "tasks": len(scored_tasks),
        "runs": runs_used,
        "basis": "mean over tasks of C(c,k)/C(n,k), harness failures excluded",
        "source": "tau-bench / tau2-bench pass_hat_k",
        "reason": no_curve_reason,
    }
    pass_at_k_block = {
        "curve": cov_curve_all,
        "max_k": max_k,
        "value_at_max_k": cov_curve_all[-1]["value"] if cov_curve_all else None,
        "tasks": len(scored_tasks),
        "runs": runs_used,
        "basis": "mean over tasks of 1 - C(n-c,k)/C(n,k), harness failures excluded",
        "source": "unbiased pass@k estimator, Chen et al. 2021 (arXiv 2107.03374)",
        "reason": no_curve_reason,
    }

    outcome_block = {
        "value": round(_mean(outcome_scores), 4) if outcome_scores else None,
        "tasks_scored": len(outcome_scores),
        "of_tasks": len(task_ids),
        "basis": "(2p-1)^2 per task, averaged; 1.0 = every run agreed, "
                 "0.0 = coin flip. Measures agreement, not correctness.",
        "source": "HAL reliability_eval/metrics/consistency.py",
        "reason": None if outcome_scores
        else "no task has 2+ eligible runs",
    }
    sequence_block = {
        "value": round(_mean(sequence_scores), 4) if sequence_scores else None,
        "tasks_scored": len(sequence_scores),
        "of_tasks": len(task_ids),
        "basis": "mean pairwise 1 - levenshtein/max-length over type:name "
                 "action sequences, conditioned on successful runs only",
        "source": "HAL trajectory consistency",
        "reason": None if sequence_scores
        else "no task has 2+ successful eligible runs to compare paths across",
    }
    resource_block = {
        "value": round(_mean(resource_scores), 4) if resource_scores else None,
        "tasks_scored": len(resource_scores),
        "of_tasks": len(task_ids),
        "by_resource": {
            key: round(_mean(values), 4)
            for key, values in sorted(resource_totals.items())
        },
        "basis": "exp(-CV) per logged resource (tokens, cost, latency, steps), "
                 "averaged over the resources present; unlogged resources are "
                 "dropped rather than scored as perfectly consistent",
        "reason": None if resource_scores
        else "no task has 2+ eligible runs with a logged resource",
    }

    icc_block = icc_one_way(icc_groups)
    advisory = runs_advisory(list(trials.values()))

    result = {
        "agent": name,
        "tasks": len(task_ids),
        "tasks_scored": len(scored_tasks),
        "runs_total": runs_total,
        "runs_used": runs_used,
        "successes": successes_total,
        "mean_success_rate": (
            round(successes_total / runs_used, 4) if runs_used else None
        ),
        "excluded_runs": excluded_block,
        "unequal_trials": unequal_block,
        "max_k": max_k,
        "max_k_basis": max_k_block["basis"],
        "pass_hat_k": pass_hat_block,
        "pass_at_k": pass_at_k_block,
        "outcome_consistency": outcome_block,
        "trajectory_consistency": sequence_block,
        "resource_consistency": resource_block,
        "icc": icc_block,
        "runs_advisory": advisory,
        "per_task": per_task,
    }
    result["narrative"] = _agent_narrative(result)
    return result


def _agent_narrative(row: dict) -> str:
    name = row["agent"]
    parts: list[str] = []

    rate = row["mean_success_rate"]
    if rate is None:
        return (f"{name} has no eligible runs left after excluding "
                f"{row['excluded_runs']['count']} harness failure(s), so no "
                f"reliability number can be computed for it.")

    parts.append(
        f"{name} succeeded on {row['successes']} of {row['runs_used']} eligible "
        f"run(s) ({rate:.0%}) across {row['tasks_scored']} task(s)."
    )

    max_k = row["max_k"]
    hat = row["pass_hat_k"]["value_at_max_k"]
    cov = row["pass_at_k"]["value_at_max_k"]
    if max_k >= 2 and hat is not None and cov is not None:
        gap = cov - hat
        parts.append(
            f"Asked to do it {max_k} times in a row it holds up "
            f"{hat:.0%} of the time (pass^{max_k}), while its chance of "
            f"getting there at least once in {max_k} tries is {cov:.0%} "
            f"(pass@{max_k})"
            + (f" — that {gap:.0%} gap is the distance between 'can it ever' "
               f"and 'can it every time'." if gap >= 0.005 else
               ", and the two agree: on this suite, whatever it can do at all "
               "it does every time.")
        )
    elif max_k == 1:
        parts.append(
            "With one eligible run per task the curve stops at k=1, which is "
            "just the success rate: nothing here speaks to repeatability."
        )

    consistency = row["outcome_consistency"]["value"]
    if consistency is not None:
        if consistency >= 0.9:
            parts.append(
                f"Outcomes are near-deterministic (consistency {consistency:.2f} "
                f"over {row['outcome_consistency']['tasks_scored']} task(s)) — "
                f"re-running mostly reproduces the same verdict, pass or fail."
            )
        elif consistency <= 0.3:
            parts.append(
                f"Outcomes are close to a coin flip (consistency "
                f"{consistency:.2f}): the same task run twice often disagrees "
                f"with itself."
            )
        else:
            parts.append(
                f"Outcome consistency is {consistency:.2f} — partly "
                f"reproducible, partly luck."
            )

    seq = row["trajectory_consistency"]["value"]
    if seq is not None:
        parts.append(
            f"Its successful runs take the same path "
            f"{seq:.0%} of the way (action-sequence similarity over "
            f"{row['trajectory_consistency']['tasks_scored']} task(s), "
            f"successful runs only)."
        )
    res = row["resource_consistency"]["value"]
    if res is not None:
        parts.append(f"Resource use is {res:.0%} stable run to run.")

    icc = row["icc"]
    if icc.get("icc1") is not None:
        share = icc["within_task_variance_share"]
        parts.append(
            f"ICC(1) is {icc['icc1_clamped']:.2f}, so about {share:.0%} of the "
            f"variance is this agent disagreeing with itself rather than tasks "
            f"differing in difficulty"
            + (" — the tasks are barely separating it at all."
               if icc["icc1_clamped"] < ICC_AGENT_DOMINATED else ".")
        )
    else:
        parts.append(f"ICC could not be estimated: {icc['reason']}.")

    if row["excluded_runs"]["count"]:
        parts.append(
            f"{row['excluded_runs']['count']} of {row['excluded_runs']['of_runs']} "
            f"run(s) were excluded as harness failures "
            f"({', '.join(f'{k} x{v}' for k, v in row['excluded_runs']['by_termination'].items())}) "
            f"and count against neither the agent's successes nor its failures."
        )
    if row["unequal_trials"]["flagged"]:
        parts.append(
            f"Trial counts are unequal ({row['unequal_trials']['min']}-"
            f"{row['unequal_trials']['max']} runs per task), so every curve is "
            f"capped at the thinnest task."
        )
    parts.append(row["runs_advisory"]["message"])
    return " ".join(parts)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def reliability(runs_by_task_agent: dict[str, dict[str, list[Trajectory]]]) -> dict:
    """Reliability analysis over repeated runs.

    ``runs_by_task_agent`` maps task_id -> side -> [runs], the same shape
    :func:`deepcompare.stability.stability_analysis` takes (sides are usually
    ``"a"`` / ``"b"``, but any set of side keys works).  Returns a JSON-safe
    dict of plain types.  Raises ``ValueError`` on empty input.

    Nothing here compares the sides against each other: with the run counts
    that real suites carry, a *difference* in reliability between two agents
    is exactly the claim the data cannot support, and ``runs_advisory`` says
    so out loud.  Each side is reported on its own terms.
    """
    if not runs_by_task_agent:
        raise ValueError("reliability needs at least one task")

    task_ids = sorted(runs_by_task_agent)
    sides: list[str] = []
    for tid in task_ids:
        for side in sorted(runs_by_task_agent[tid]):
            if side not in sides:
                sides.append(side)
    if not sides:
        raise ValueError("reliability needs at least one agent side")

    names: dict[str, str] = {}
    per_agent: dict[str, dict] = {}
    for side in sides:
        by_task = {
            tid: list(runs_by_task_agent[tid].get(side) or [])
            for tid in task_ids
            if runs_by_task_agent[tid].get(side)
        }
        if not by_task:
            raise ValueError(f"side {side!r} has no runs on any task")
        first_task = sorted(by_task)[0]
        names[side] = by_task[first_task][0].agent.name
        per_agent[side] = _agent_reliability(names[side], by_task)

    result = {
        "agents": names,
        "tasks": len(task_ids),
        "per_agent": per_agent,
        "definitions": {
            "pass_hat_k": "C(c,k)/C(n,k) averaged over tasks — all k runs pass "
                          "(tau-bench / tau2-bench pass_hat_k)",
            "pass_at_k": "1 - C(n-c,k)/C(n,k) averaged over tasks — at least "
                         "one of k runs passes (Chen et al. 2021, "
                         "arXiv 2107.03374)",
            "outcome_consistency": "(2p-1)^2 per task (HAL consistency)",
            "trajectory_consistency": "mean pairwise 1 - levenshtein/max-length "
                                      "over action sequences, successful runs "
                                      "only (HAL)",
            "resource_consistency": "exp(-CV) over logged resources",
            "icc": "one-way ICC(1) over per-task run outcomes "
                   "(cf. arXiv 2512.06710, ICC 0.304-0.774 on GAIA)",
        },
    }
    result["narrative"] = _suite_narrative(result)
    return result


def _suite_narrative(result: dict) -> str:
    parts = [row["narrative"] for _, row in sorted(result["per_agent"].items())]
    advisories = [
        row["runs_advisory"] for _, row in sorted(result["per_agent"].items())
    ]
    weakest = min(
        (a for a in advisories if a.get("n_min") is not None),
        key=lambda a: a["n_min"],
        default=None,
    )
    if weakest and weakest["tier"] == "insufficient" and len(result["per_agent"]) > 1:
        parts.append(
            f"With {weakest['n_min']} run(s) per task these agents cannot be "
            f"ranked against each other on reliability — read each column on "
            f"its own, and add runs before calling one more reliable than the "
            f"other."
        )
    return " ".join(parts)
