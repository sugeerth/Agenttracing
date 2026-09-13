"""Attribute-based failure analysis (SCHEMA.md v15).

Step-level attribution answers "what went wrong in *this* run".  Across a
corpus the more useful question is different: **which behavioural attributes
travel with failure?**  Does skipping verification predict failure?  Does a
low-confidence step?  Does using more tools help or just cost more?

Each trajectory is reduced to a handful of binary or bucketed attributes, and
each attribute is scored by how much the failure rate differs between runs
that have it and runs that do not.  The technique is deliberately plain —
contingency counts and a difference in proportions with a deterministic
bootstrap interval — because the alternatives (regression, uplift models)
would add dependencies and, on the sample sizes eval suites actually have,
would dress up the same evidence in heavier machinery.

Every attribute is scored twice: raw across the corpus, and **stratified
within each task**.  That second number is not a refinement, it is a guard.
Task difficulty confounds every marginal association here — hard tasks both
provoke different behaviour and cause more failures — and the trajectory-
length signal in agent traces is documented to *reverse* once difficulty is
controlled.  An attribute whose sign flips under stratification is reported
with a ``reverses_under_stratification`` flag and is never counted as a
finding, because its raw association is an artifact of which tasks provoke it.

**These are associations, not causes.**  Even a stratified lift can arise
because the attribute causes failure, because a third factor causes both, or
because of something the task id does not capture.  Every finding carries its
sample sizes and intervals, and the module never uses causal language.  The
honest use is triage: where to look and what to test next, not a conclusion to
act on blindly.
"""

from __future__ import annotations

from typing import Callable, Optional

from .statistics import two_group_bootstrap_difference, wilson_interval
from .trace import Trajectory

#: step types that count as reaching outside the model.
_TOOL_TYPES = frozenset({"tool_call", "search", "retrieve", "read"})
#: text cues marking a verification-intent step.
_VERIFY_CUES = ("verify", "confirm", "cross-check", "double-check", "corroborat",
                "validate", "sanity", "reliable")
#: minimum runs on each side of a split before a lift is worth reporting.
MIN_GROUP = 2
#: absolute failure-rate difference at or above which an attribute is notable.
NOTABLE_LIFT = 0.25


def _has_verify(trajectory: Trajectory) -> bool:
    for step in trajectory.steps:
        text = f"{step.name} {step.input}".lower()
        if any(cue in text for cue in _VERIFY_CUES):
            return True
    return False


def _has_plan(trajectory: Trajectory) -> bool:
    return any(step.type == "plan" for step in trajectory.steps)


def _has_poor_step(trajectory: Trajectory) -> bool:
    return any(step.quality in ("weak", "bad") for step in trajectory.steps)


def _low_confidence(trajectory: Trajectory) -> Optional[bool]:
    values = [
        float(step.model["confidence"]) for step in trajectory.steps
        if step.model and step.model.get("confidence") is not None
    ]
    if not values:
        return None
    return min(values) < 0.7


def _many_tools(trajectory: Trajectory) -> bool:
    calls = sum(1 for s in trajectory.steps if s.type in _TOOL_TYPES)
    return calls > 2


def _long_run(trajectory: Trajectory) -> bool:
    return len(trajectory.steps) > 6


def _repeated_search(trajectory: Trajectory) -> bool:
    searches = [s for s in trajectory.steps if s.type == "search"]
    return len(searches) > 1


#: attribute name -> (predicate, human phrasing).  A predicate returning None
#: means "not measurable for this run" and excludes it from that attribute.
ATTRIBUTES: dict[str, tuple[Callable[[Trajectory], Optional[bool]], str]] = {
    "no_verification_step": (
        lambda t: not _has_verify(t), "the run never verified its evidence"),
    "no_plan_step": (
        lambda t: not _has_plan(t), "the run acted without stating a plan"),
    "poor_quality_step": (
        _has_poor_step, "the run contains a step annotated weak or bad"),
    "low_confidence_step": (
        _low_confidence, "some step fell below 70% model confidence"),
    "many_tool_calls": (
        _many_tools, "the run made more than two tool calls"),
    "long_trajectory": (
        _long_run, "the run took more than six steps"),
    "repeated_search": (
        _repeated_search, "the run searched more than once"),
}


def _stratified_lift(
    trajectories: list[Trajectory],
    predicate: Callable[[Trajectory], Optional[bool]],
) -> Optional[dict]:
    """Pooled within-task lift, controlling for task difficulty.

    A raw lift across a corpus confounds the attribute with task difficulty:
    hard tasks both provoke different behaviour and cause more failures, so
    the marginal association can point the wrong way entirely (Simpson's
    paradox — documented for trajectory length in agent traces).  Comparing
    only *within* a task removes difficulty from the comparison.

    Strata where every run has the attribute (or none does) carry no
    information and are skipped.  Pooling uses Mantel-Haenszel weights
    ``n_with * n_without / n``, which weight a stratum by how balanced it is.
    Returns None when no stratum can contribute.
    """
    by_task: dict[str, list[Trajectory]] = {}
    for trajectory in trajectories:
        by_task.setdefault(trajectory.task.id, []).append(trajectory)

    numerator = 0.0
    weight_total = 0.0
    strata_used = 0
    for task in sorted(by_task):
        with_fail = with_n = without_fail = without_n = 0
        for trajectory in by_task[task]:
            value = predicate(trajectory)
            if value is None:
                continue
            failed = not trajectory.outcome.success
            if value:
                with_n += 1
                with_fail += 1 if failed else 0
            else:
                without_n += 1
                without_fail += 1 if failed else 0
        if with_n == 0 or without_n == 0:
            continue
        total = with_n + without_n
        weight = (with_n * without_n) / total
        difference = with_fail / with_n - without_fail / without_n
        numerator += weight * difference
        weight_total += weight
        strata_used += 1

    if weight_total <= 0:
        return None
    return {
        "lift": round(numerator / weight_total, 4),
        "strata": strata_used,
        "method": "Mantel-Haenszel pooled within-task risk difference",
    }


def _lift_row(name: str, phrasing: str, with_fail: int, with_n: int,
              without_fail: int, without_n: int) -> dict:
    rate_with = with_fail / with_n if with_n else 0.0
    rate_without = without_fail / without_n if without_n else 0.0
    lift = rate_with - rate_without

    # The two groups are independent and typically differ in size, so each is
    # resampled at its own size.  Forcing them to a common length rewrites
    # both rates and can invert the sign of the difference.
    interval = None
    if min(with_n, without_n) >= MIN_GROUP:
        interval = two_group_bootstrap_difference(
            [True] * with_fail + [False] * (with_n - with_fail),
            [True] * without_fail + [False] * (without_n - without_fail),
        )

    return {
        "attribute": name,
        "phrasing": phrasing,
        "with": {"runs": with_n, "failures": with_fail,
                 "failure_rate": round(rate_with, 4),
                 "ci": list(wilson_interval(with_fail, with_n))},
        "without": {"runs": without_n, "failures": without_fail,
                    "failure_rate": round(rate_without, 4),
                    "ci": list(wilson_interval(without_fail, without_n))},
        "lift": round(lift, 4),
        "interval": interval,
        "notable": abs(lift) >= NOTABLE_LIFT and min(with_n, without_n) >= MIN_GROUP,
        "measurable": min(with_n, without_n) >= MIN_GROUP,
    }


def attribute_analysis(trajectories: list[Trajectory]) -> dict:
    """Score every attribute by how strongly it separates failing runs.

    Accepts a flat list of trajectories (any mix of agents); pass one agent's
    runs to characterise that agent, or the whole corpus to characterise the
    task set.
    """
    if not trajectories:
        return {"runs": 0, "attributes": [], "narrative": "No runs to analyse."}

    rows: list[dict] = []
    for name, (predicate, phrasing) in sorted(ATTRIBUTES.items()):
        with_fail = with_n = without_fail = without_n = 0
        for trajectory in trajectories:
            value = predicate(trajectory)
            if value is None:
                continue
            failed = not trajectory.outcome.success
            if value:
                with_n += 1
                with_fail += 1 if failed else 0
            else:
                without_n += 1
                without_fail += 1 if failed else 0
        if with_n == 0 and without_n == 0:
            continue
        row = _lift_row(name, phrasing, with_fail, with_n,
                        without_fail, without_n)
        stratified = _stratified_lift(trajectories, predicate)
        row["stratified"] = stratified
        # A marginal association that flips sign once task difficulty is
        # controlled is an artifact of the task mix, not a property of the
        # attribute.  Flag it loudly and never call it notable.
        reverses = bool(
            stratified
            and abs(row["lift"]) >= 0.05
            and abs(stratified["lift"]) >= 0.05
            and (row["lift"] > 0) != (stratified["lift"] > 0)
        )
        row["reverses_under_stratification"] = reverses
        if reverses:
            row["notable"] = False
        rows.append(row)

    def strength(row: dict) -> float:
        stratified = row.get("stratified")
        return abs(stratified["lift"]) if stratified else abs(row["lift"])

    rows.sort(key=lambda r: (not r["notable"], -strength(r), r["attribute"]))

    notable = [r for r in rows if r["notable"]]
    failures = sum(1 for t in trajectories if not t.outcome.success)
    if not notable:
        narrative = (
            f"No behavioural attribute separates the {failures} failure(s) across "
            f"{len(trajectories)} run(s) strongly enough to be worth acting on — "
            f"either the sample is too small or failures are not attribute-linked."
        )
    else:
        top = notable[0]
        stratified = top.get("stratified")
        headline = stratified["lift"] if stratified else top["lift"]
        direction = "more" if headline > 0 else "less"
        narrative = (
            f"Of {len(trajectories)} run(s) with {failures} failure(s), the "
            f"strongest association is that {top['phrasing']}: within the same "
            f"task, such runs fail {abs(headline):.0%} {direction} often"
            if stratified else
            f"Of {len(trajectories)} run(s) with {failures} failure(s), the "
            f"strongest association is that {top['phrasing']}: such runs fail "
            f"{abs(headline):.0%} {direction} often"
        )
        narrative += (
            f" (raw {top['with']['failure_rate']:.0%} of {top['with']['runs']} "
            f"versus {top['without']['failure_rate']:.0%} of "
            f"{top['without']['runs']}). This is an association, not a cause."
        )
        if len(notable) > 1:
            narrative += f" {len(notable) - 1} other attribute(s) also separate."

    # Reversals are reported whether or not anything else was notable: the
    # case where the only strong-looking signal turns out to be an artifact
    # is exactly when the reader most needs to be told.
    flipped = [r for r in rows if r.get("reverses_under_stratification")]
    if flipped:
        names = ", ".join(r["attribute"] for r in flipped)
        narrative += (
            f" {len(flipped)} attribute(s) reverse sign once task difficulty "
            f"is controlled ({names}) — their raw association is an artifact of "
            f"which tasks provoke them, and they are not reported as findings."
        )

    return {
        "runs": len(trajectories),
        "failures": failures,
        "attributes": rows,
        "notable": len(notable),
        "caveat": (
            "Attributes are associations measured on logged traces. An attribute "
            "may travel with failure because it causes it, because a common "
            "factor causes both, or because harder tasks provoke it."
        ),
        "narrative": narrative,
    }


def attribute_profiles(
    trajs_by_agent: dict[str, list[Trajectory]]
) -> dict:
    """Per-agent attribute analysis, plus the corpus-wide view."""
    profiles = {
        name: attribute_analysis(trajs)
        for name, trajs in sorted(trajs_by_agent.items())
    }
    everything = [t for trajs in trajs_by_agent.values() for t in trajs]
    return {
        "corpus": attribute_analysis(everything),
        "agents": profiles,
    }
