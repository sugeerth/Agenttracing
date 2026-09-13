"""Cohort comparison — groups rather than individuals (v18).

Pairwise comparison answers "is this agent better than that one".  The
questions teams actually argue about are usually one level up: *does this
model family beat that one*, *did prompt v2 help*, *are the cheap models good
enough here*.  Comparing one representative run from each side answers none
of those — it just picks a champion and hopes.

A cohort is any labelled group of runs.  Grouping is by a key the caller
chooses — model, agent version, prompt variant, provider, anything recorded —
and every cohort is then compared as a population: outcome rates with
intervals, spend, behavioural shape, and which attributes separate them.

The statistics are the ones already used for gate decisions, for the same
reason: on the sample sizes eval suites have, a difference of one or two runs
is usually noise, and a comparison that cannot say so will be used to justify
a migration it does not support.  Every pairwise difference carries an
interval and a `significant` flag, and the narrative refuses to call an
overlapping difference a win.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from .attributes import ATTRIBUTES
from .similarity import Profile, cosine, facet_similarity
from .statistics import two_group_bootstrap_difference, wilson_interval
from .trace import Trajectory

#: grouping keys the CLI understands out of the box.
GROUPERS: dict[str, Callable[[Trajectory], str]] = {
    "model": lambda t: t.agent.model or "unknown",
    "agent": lambda t: t.agent.name,
    "version": lambda t: t.agent.version or "unknown",
    "task": lambda t: t.task.id,
}


def group_runs(
    trajectories: Iterable[Trajectory],
    by: str = "model",
    key: Optional[Callable[[Trajectory], str]] = None,
) -> dict[str, list[Trajectory]]:
    """Group runs into cohorts by a recorded field or a caller's function."""
    if key is None:
        if by not in GROUPERS:
            raise ValueError(
                f"unknown grouping {by!r}; known: {', '.join(sorted(GROUPERS))}"
                f" (or pass key=)"
            )
        key = GROUPERS[by]
    cohorts: dict[str, list[Trajectory]] = {}
    for trajectory in trajectories:
        cohorts.setdefault(key(trajectory), []).append(trajectory)
    return {name: cohorts[name] for name in sorted(cohorts)}


def _cohort_summary(name: str, runs: list[Trajectory]) -> dict:
    successes = sum(1 for t in runs if t.outcome.success)
    tokens = [float(sum(s.tokens for s in t.steps)) for t in runs]
    cost = [float(t.totals.cost_usd) for t in runs]
    latency = [float(t.totals.latency_s) for t in runs]
    steps = [float(len(t.steps)) for t in runs]

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 6) if values else 0.0

    return {
        "cohort": name,
        "runs": len(runs),
        "agents": sorted({t.agent.name for t in runs}),
        "models": sorted({t.agent.model for t in runs if t.agent.model}),
        "tasks": sorted({t.task.id for t in runs}),
        "successes": successes,
        "success_rate": round(successes / len(runs), 4) if runs else 0.0,
        "success_ci": list(wilson_interval(successes, len(runs))),
        "mean_tokens": mean(tokens),
        "mean_cost_usd": mean(cost),
        "mean_latency_s": mean(latency),
        "mean_steps": mean(steps),
    }


def _shared_task_outcomes(
    left: list[Trajectory], right: list[Trajectory]
) -> tuple[list[bool], list[bool]]:
    """Outcome vectors restricted to tasks both cohorts attempted.

    Cohorts that ran different task sets cannot be compared on raw success
    rate — one may simply have drawn easier work — so the comparison is made
    on the intersection.
    """
    shared = ({t.task.id for t in left} & {t.task.id for t in right})
    return (
        [t.outcome.success for t in left if t.task.id in shared],
        [t.outcome.success for t in right if t.task.id in shared],
    )


def _attribute_gaps(left: list[Trajectory], right: list[Trajectory]) -> list[dict]:
    """Which behavioural attributes differ in prevalence between cohorts."""
    rows: list[dict] = []
    for name, (predicate, phrasing) in sorted(ATTRIBUTES.items()):
        left_values = [predicate(t) for t in left]
        right_values = [predicate(t) for t in right]
        left_known = [bool(v) for v in left_values if v is not None]
        right_known = [bool(v) for v in right_values if v is not None]
        if len(left_known) < 2 or len(right_known) < 2:
            continue
        left_rate = sum(left_known) / len(left_known)
        right_rate = sum(right_known) / len(right_known)
        difference = left_rate - right_rate
        if abs(difference) < 0.2:
            continue
        rows.append({
            "attribute": name,
            "phrasing": phrasing,
            "left_rate": round(left_rate, 4),
            "right_rate": round(right_rate, 4),
            "difference": round(difference, 4),
            "interval": two_group_bootstrap_difference(left_known, right_known),
        })
    rows.sort(key=lambda r: -abs(r["difference"]))
    return rows


def _behaviour_distance(left: list[Trajectory], right: list[Trajectory]) -> dict:
    """How differently the two cohorts work, on the similarity facets."""
    left_profile = Profile("left", left)
    right_profile = Profile("right", right)
    facets = facet_similarity(left_profile, right_profile)
    tool_similarity = cosine(left_profile.tool_usage, right_profile.tool_usage)
    return {"process": facets["process"], "tools": tool_similarity,
            "resources": facets["resources"]}


def compare_cohorts(cohorts: dict[str, list[Trajectory]]) -> dict:
    """Compare every pair of cohorts as populations."""
    names = sorted(cohorts)
    if len(names) < 2:
        return {
            "cohorts": [_cohort_summary(n, cohorts[n]) for n in names],
            "pairs": [],
            "narrative": "Need at least two cohorts to compare.",
        }

    summaries = [_cohort_summary(name, cohorts[name]) for name in names]
    by_name = {s["cohort"]: s for s in summaries}

    pairs: list[dict] = []
    for i, left_name in enumerate(names):
        for right_name in names[i + 1:]:
            left, right = cohorts[left_name], cohorts[right_name]
            left_shared, right_shared = _shared_task_outcomes(left, right)
            comparable = len(left_shared) > 0 and len(right_shared) > 0
            interval = (
                two_group_bootstrap_difference(left_shared, right_shared)
                if comparable else
                {"observed": 0.0, "low": 0.0, "high": 0.0,
                 "significant": False, "samples": 0}
            )
            cost_left = by_name[left_name]["mean_cost_usd"]
            cost_right = by_name[right_name]["mean_cost_usd"]
            cost_ratio = (
                round(cost_left / cost_right, 3) if cost_right > 0 else None
            )
            pairs.append({
                "left": left_name,
                "right": right_name,
                "shared_tasks": len(set(t.task.id for t in left)
                                    & set(t.task.id for t in right)),
                "comparable": comparable,
                "success_difference": interval,
                "cost_ratio": cost_ratio,
                "behaviour": _behaviour_distance(left, right),
                "attribute_gaps": _attribute_gaps(left, right),
                "verdict": _pair_verdict(left_name, right_name, interval,
                                         cost_left, cost_right, comparable),
            })

    pairs.sort(key=lambda p: (not p["success_difference"]["significant"],
                              -abs(p["success_difference"]["observed"])))

    decided = [p for p in pairs if p["success_difference"]["significant"]]
    if not decided:
        narrative = (
            f"No cohort beats another on outcome with the evidence available "
            f"({len(pairs)} pair(s) compared). Differences that look large are "
            f"inside the resampling interval, so choose on cost or behaviour "
            f"rather than on a success gap."
        )
    else:
        top = decided[0]
        narrative = (
            f"{len(decided)} of {len(pairs)} cohort pair(s) differ on outcome "
            f"beyond noise. The clearest: {top['verdict']}"
        )
    return {"cohorts": summaries, "pairs": pairs, "narrative": narrative}


def _pair_verdict(left: str, right: str, interval: dict,
                  cost_left: float, cost_right: float, comparable: bool) -> str:
    if not comparable:
        return (f"{left} and {right} share no task, so their success rates are "
                f"not comparable.")
    observed = interval["observed"]
    if not interval["significant"]:
        cheaper, dearer = ((left, right) if cost_left < cost_right
                           else (right, left))
        gap = abs(cost_left - cost_right)
        if gap > 0:
            return (
                f"{left} and {right} are indistinguishable on outcome "
                f"({observed:+.0%}, interval {interval['low']:+.0%} to "
                f"{interval['high']:+.0%} includes zero) — but {cheaper} costs "
                f"${gap:.4f} less per run, so it wins on the evidence there is."
            )
        return (f"{left} and {right} are indistinguishable on outcome and cost.")
    better, worse = (left, right) if observed > 0 else (right, left)
    return (
        f"{better} beats {worse} by {abs(observed):.0%} on shared tasks "
        f"(interval {interval['low']:+.0%} to {interval['high']:+.0%}, "
        f"excluding zero)."
    )
