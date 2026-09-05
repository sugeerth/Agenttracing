"""Reference profiles — a base style learned from many runs (v18).

Every comparison so far needs a partner: another agent, or a specific golden
run.  That is a real restriction.  A team with one agent and a pile of
history has no partner to diff against, and picking a single run as "golden"
is arbitrary — that run's quirks become the standard.

A **profile** is the alternative: a norm distilled from many runs.  It says
what this task normally looks like *when it goes well* — the canonical path
shape, the usual tool mix, the cost band, the steps that are always present —
and any single run can then be scored against it without another run
existing.

Design decisions worth stating, because they set what the score means:

* **Built from successes only, by default.**  A norm assembled from all runs
  encodes the failures too, and then a run is "normal" for repeating them.
* **Bands, not points.**  Cost and length are summarised as a median with an
  interquartile range, so "outside the norm" means outside where the middle
  half of runs sat — not merely different from an average.
* **Deliberately coarse.**  A profile records the shape of a run (step types,
  tool names, magnitudes), never its wording.  Two runs phrasing the same
  search differently are the same style; the semantic layer is where wording
  is examined.
* **Honest about thin evidence.**  A profile built from three runs says so,
  and the score carries that caveat rather than implying authority.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .similarity import cosine, lcs_ratio
from .trace import Trajectory

#: step types that reach outside the model.
_TOOL_TYPES = frozenset({"tool_call", "search", "retrieve", "read"})
#: fraction of runs a step type must appear in to count as expected.
EXPECTED_AT = 0.8
#: profiles built from fewer runs than this are marked thin.
THIN_EVIDENCE = 5
#: path similarity at or above which a run is on the canonical path.
ON_PATH = 0.75


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _quartiles(values: list[float]) -> tuple[float, float]:
    """Interquartile range — where the middle half of the runs sat."""
    if not values:
        return (0.0, 0.0)
    ordered = sorted(values)
    if len(ordered) < 4:
        return (float(ordered[0]), float(ordered[-1]))
    mid = len(ordered) // 2
    lower = ordered[:mid]
    upper = ordered[mid + 1:] if len(ordered) % 2 else ordered[mid:]
    return (_median(lower), _median(upper))


def _band(values: list[float]) -> dict:
    low, high = _quartiles(values)
    return {"median": round(_median(values), 4),
            "low": round(low, 4), "high": round(high, 4),
            "min": round(min(values), 4) if values else 0.0,
            "max": round(max(values), 4) if values else 0.0}


def _modal_path(sequences: list[tuple[str, ...]]) -> tuple[str, ...]:
    """The recorded path most like all the others — a medoid, not an average.

    Averaging step sequences would invent a path nobody took; the medoid is
    always a real run's shape.
    """
    if not sequences:
        return ()
    best, best_score = sequences[0], -1.0
    for candidate in sequences:
        score = sum(lcs_ratio(candidate, other) for other in sequences)
        if score > best_score:
            best, best_score = candidate, score
    return best


def build_profile(
    trajectories: Iterable[Trajectory],
    name: str = "reference",
    successes_only: bool = True,
) -> dict:
    """Distil a reference profile from a set of runs of the same task."""
    runs = list(trajectories)
    if not runs:
        raise ValueError("cannot build a profile from zero runs")

    task_ids = {t.task.id for t in runs}
    considered = [t for t in runs if t.outcome.success] if successes_only else runs
    excluded = len(runs) - len(considered)
    if not considered:
        raise ValueError(
            "no successful runs to build a profile from; pass "
            "successes_only=False to profile the failures too"
        )

    sequences = [tuple(s.type for s in t.steps) for t in considered]
    canonical = _modal_path(sequences)

    type_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    runs_with_type: dict[str, int] = {}
    for trajectory in considered:
        seen: set[str] = set()
        for step in trajectory.steps:
            type_counts[step.type] = type_counts.get(step.type, 0) + 1
            seen.add(step.type)
            if step.type in _TOOL_TYPES:
                key = step.name or step.type
                tool_counts[key] = tool_counts.get(key, 0) + 1
        for step_type in seen:
            runs_with_type[step_type] = runs_with_type.get(step_type, 0) + 1

    expected_types = sorted(
        step_type for step_type, count in runs_with_type.items()
        if count / len(considered) >= EXPECTED_AT
    )

    return {
        "name": name,
        "tasks": sorted(task_ids),
        "runs_used": len(considered),
        "runs_excluded": excluded,
        "successes_only": successes_only,
        "thin_evidence": len(considered) < THIN_EVIDENCE,
        "canonical_path": list(canonical),
        "expected_step_types": expected_types,
        "step_type_mix": dict(sorted(type_counts.items())),
        "tool_mix": dict(sorted(tool_counts.items())),
        "bands": {
            "steps": _band([float(len(t.steps)) for t in considered]),
            "tokens": _band([float(sum(s.tokens for s in t.steps))
                             for t in considered]),
            "latency_s": _band([float(t.totals.latency_s) for t in considered]),
            "cost_usd": _band([float(t.totals.cost_usd) for t in considered]),
        },
        "caveat": (
            f"Built from {len(considered)} run(s)"
            + (" — thin evidence, treat the bands as indicative."
               if len(considered) < THIN_EVIDENCE else ".")
        ),
    }


def _band_position(value: float, band: dict) -> tuple[str, str]:
    """Where a value sits relative to the profile's middle half."""
    if band["high"] <= 0 and band["low"] <= 0:
        return "unknown", "the profile records no spread for this measure"
    if value < band["low"]:
        return "below", f"below the usual {band['low']:g}–{band['high']:g}"
    if value > band["high"]:
        return "above", f"above the usual {band['low']:g}–{band['high']:g}"
    return "within", f"inside the usual {band['low']:g}–{band['high']:g}"


def score_run(trajectory: Trajectory, profile: dict) -> dict:
    """Score one run against a reference profile — no partner run needed."""
    sequence = tuple(s.type for s in trajectory.steps)
    canonical = tuple(profile.get("canonical_path", []))
    path_similarity = lcs_ratio(sequence, canonical)

    tools: dict[str, int] = {}
    present_types: set[str] = set()
    for step in trajectory.steps:
        present_types.add(step.type)
        if step.type in _TOOL_TYPES:
            tools[step.name or step.type] = tools.get(step.name or step.type, 0) + 1
    tool_similarity = cosine(tools, profile.get("tool_mix", {}))

    missing = [t for t in profile.get("expected_step_types", [])
               if t not in present_types]
    unexpected = sorted(
        t for t in present_types
        if t not in profile.get("step_type_mix", {})
    )

    measures = {
        "steps": float(len(trajectory.steps)),
        "tokens": float(sum(s.tokens for s in trajectory.steps)),
        "latency_s": float(trajectory.totals.latency_s),
        "cost_usd": float(trajectory.totals.cost_usd),
    }
    bands = profile.get("bands", {})
    positions: dict[str, dict] = {}
    outside = []
    for key, value in measures.items():
        band = bands.get(key)
        if not band:
            continue
        where, phrasing = _band_position(value, band)
        positions[key] = {"value": round(value, 4), "position": where,
                          "phrasing": phrasing, "band": band}
        if where == "above":
            outside.append(key)

    # Verdict: outcome first, then shape, then spend.
    if not trajectory.outcome.success:
        verdict = "failed"
    elif missing or path_similarity < ON_PATH:
        verdict = "off-profile"
    elif outside:
        verdict = "costly"
    else:
        verdict = "on-profile"

    parts = []
    if verdict == "failed":
        parts.append(f"{trajectory.agent.name} failed this run.")
    parts.append(
        f"Its path is {path_similarity:.0%} like the profile's canonical shape"
        + (f" and its tool mix {tool_similarity:.0%} like the norm." if tools
           else ".")
    )
    if missing:
        parts.append(
            f"It skipped step type(s) the profile always contains: "
            f"{', '.join(missing)}."
        )
    if unexpected:
        parts.append(f"It used step type(s) the profile never shows: "
                     f"{', '.join(unexpected)}.")
    for key in outside:
        parts.append(f"Its {key.replace('_', ' ')} is "
                     f"{positions[key]['phrasing']}.")
    if verdict == "on-profile":
        parts.append("Nothing about it sits outside the norm.")
    if profile.get("thin_evidence"):
        parts.append(
            f"The profile rests on {profile.get('runs_used')} run(s), so read "
            f"this as indicative."
        )

    return {
        "agent": trajectory.agent.name,
        "task": trajectory.task.id,
        "profile": profile.get("name"),
        "verdict": verdict,
        "path_similarity": round(path_similarity, 4),
        "tool_similarity": round(tool_similarity, 4),
        "missing_step_types": missing,
        "unexpected_step_types": unexpected,
        "measures": positions,
        "outside_band": outside,
        "narrative": " ".join(parts),
    }


def profile_suite(
    profiles: dict[str, dict], runs: Iterable[Trajectory]
) -> dict:
    """Score many runs against per-task profiles.

    ``profiles`` maps task id -> profile.  Runs whose task has no profile are
    reported rather than silently skipped.
    """
    scored: list[dict] = []
    unprofiled: list[str] = []
    for trajectory in runs:
        profile = profiles.get(trajectory.task.id)
        if profile is None:
            unprofiled.append(f"{trajectory.task.id}/{trajectory.agent.name}")
            continue
        scored.append(score_run(trajectory, profile))

    counts: dict[str, int] = {}
    for row in scored:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    order = ("failed", "off-profile", "costly", "on-profile")
    scored.sort(key=lambda r: (order.index(r["verdict"]), r["task"], r["agent"]))

    if not scored:
        narrative = "No run had a profile to be scored against."
    else:
        off = counts.get("failed", 0) + counts.get("off-profile", 0)
        narrative = (
            f"{len(scored)} run(s) scored against {len(profiles)} profile(s): "
            f"{counts.get('on-profile', 0)} on profile, "
            f"{counts.get('costly', 0)} on profile but over the usual spend, "
            f"{counts.get('off-profile', 0)} off the canonical path, "
            f"{counts.get('failed', 0)} failed."
        )
        if off:
            narrative += (
                f" The {off} run(s) that failed or left the path are where to "
                f"look first."
            )
    return {
        "scored": scored,
        "counts": counts,
        "unprofiled": sorted(unprofiled),
        "narrative": narrative,
    }
