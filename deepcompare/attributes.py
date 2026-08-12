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

**These are associations, not causes.**  An attribute can travel with failure
because it causes it, because a third factor causes both, or because the hard
tasks happen to provoke it.  Every finding is reported as a lift with its
sample size and interval, and the module never uses causal language.  The
honest use is triage: attributes that separate strongly are where to look and
what to test next, not conclusions to act on blindly.
"""

from __future__ import annotations

from typing import Callable, Optional

from .statistics import paired_bootstrap_difference, wilson_interval
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


def _lift_row(name: str, phrasing: str, with_fail: int, with_n: int,
              without_fail: int, without_n: int) -> dict:
    rate_with = with_fail / with_n if with_n else 0.0
    rate_without = without_fail / without_n if without_n else 0.0
    lift = rate_with - rate_without

    # Bootstrap the difference by treating the two groups as paired samples of
    # equal length; with tiny groups this is a coarse but honest interval.
    size = min(with_n, without_n)
    interval = None
    if size >= MIN_GROUP:
        left = [True] * min(with_fail, size) + [False] * (size - min(with_fail, size))
        right = [True] * min(without_fail, size) + [False] * (
            size - min(without_fail, size))
        interval = paired_bootstrap_difference(left, right)

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
        rows.append(_lift_row(name, phrasing, with_fail, with_n,
                              without_fail, without_n))

    rows.sort(key=lambda r: (not r["notable"], -abs(r["lift"]), r["attribute"]))

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
        direction = "more" if top["lift"] > 0 else "less"
        narrative = (
            f"Of {len(trajectories)} run(s) with {failures} failure(s), the "
            f"strongest association is that {top['phrasing']}: such runs fail "
            f"{abs(top['lift']):.0%} {direction} often "
            f"({top['with']['failure_rate']:.0%} of {top['with']['runs']} versus "
            f"{top['without']['failure_rate']:.0%} of {top['without']['runs']}). "
            f"This is an association, not a cause."
        )
        if len(notable) > 1:
            narrative += f" {len(notable) - 1} other attribute(s) also separate."

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
