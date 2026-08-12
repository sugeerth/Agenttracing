"""Model-level telemetry fused with agent-level behavior (SCHEMA.md v12).

Every other module in AgentDiff reads the agent's *actions*: which tool it
called, which source it opened, where two runs parted company.  None of them
can see what the model itself was doing while it acted.  When a step carries
model telemetry — mean token probability, minimum token probability, entropy
— a second, independent signal becomes available, and the interesting
question is whether the two agree.

The question that matters operationally is not "how confident was it" but
**did the model know?**  Two failures that look identical in the trajectory
are completely different engineering problems:

``flagged``
    Confidence collapsed at (or just before) the step that caused the
    failure.  The model signalled the mistake as it made it, so a runtime
    confidence gate would have caught this run.  Cheap to mitigate.

``silent``
    The model was as confident as ever while going wrong.  No threshold on
    its own uncertainty would have saved it; only external verification —
    a second source, a check step — can catch this class.  Expensive, and
    the more dangerous of the two.

The same split, measured across many runs, gives a **calibration** score per
agent: an agent whose confidence falls when it is about to be wrong is
supervisable; one that is uniformly confident is not, regardless of how good
its success rate looks.

Telemetry is optional everywhere.  When no step carries a ``model`` block the
analysis reports ``available: false`` and every consumer skips it.
"""

from __future__ import annotations

from typing import Optional

from .trace import Step, Trajectory

#: confidence drop (absolute, versus the run's own baseline) that counts as
#: the model flagging a step.
FLAG_DROP = 0.15
#: confidence at or above which a wrong step is called overconfident.
OVERCONFIDENT_AT = 0.80
#: steps before a divergence to search for the onset of a confidence drop.
LEAD_WINDOW = 3


def step_confidence(step: Step) -> Optional[float]:
    """This step's model confidence, or None when it carries no telemetry."""
    if not step.model:
        return None
    value = step.model.get("confidence")
    if value is None:
        return None
    return float(value)


def has_telemetry(trajectory: Trajectory) -> bool:
    return any(step_confidence(step) is not None for step in trajectory.steps)


def confidence_series(trajectory: Trajectory) -> list[Optional[float]]:
    """Per-step confidence, with None where a step carries no telemetry."""
    return [step_confidence(step) for step in trajectory.steps]


def _stats(trajectory: Trajectory) -> dict:
    series = confidence_series(trajectory)
    known = [value for value in series if value is not None]
    entropies = [
        float(step.model["entropy"])
        for step in trajectory.steps
        if step.model and step.model.get("entropy") is not None
    ]
    floors = [
        float(step.model["min_token_confidence"])
        for step in trajectory.steps
        if step.model and step.model.get("min_token_confidence") is not None
    ]
    return {
        "series": [round(v, 4) if v is not None else None for v in series],
        "mean_confidence": round(sum(known) / len(known), 4) if known else None,
        "min_confidence": round(min(known), 4) if known else None,
        "mean_entropy": round(sum(entropies) / len(entropies), 4) if entropies else None,
        "min_token_confidence": round(min(floors), 4) if floors else None,
        "steps_scored": len(known),
    }


def _baseline(series: list[Optional[float]], exclude: set[int]) -> Optional[float]:
    """Mean confidence over the run's other steps — the run's own normal."""
    values = [
        value for index, value in enumerate(series)
        if value is not None and index not in exclude
    ]
    return sum(values) / len(values) if values else None


def _onset(series: list[Optional[float]], index: int, baseline: float) -> int:
    """How many steps before ``index`` the confidence drop began."""
    lead = 0
    for offset in range(1, min(LEAD_WINDOW, index) + 1):
        value = series[index - offset]
        if value is not None and baseline - value >= FLAG_DROP:
            lead = offset
        else:
            break
    return lead


def analyze(report: dict, a: Trajectory, b: Trajectory) -> dict:
    """Fuse model telemetry with the behavioral findings of a comparison.

    Returns ``{"available": False}`` when neither run carries telemetry.
    """
    if not (has_telemetry(a) or has_telemetry(b)):
        return {"available": False}

    result: dict = {
        "available": True,
        "a": _stats(a),
        "b": _stats(b),
        "signal": None,
        "calibration": None,
    }

    attribution = report.get("attribution") or {}
    failed_side = attribution.get("failed_agent")
    root = attribution.get("root_cause_step")

    if failed_side in ("a", "b") and root is not None:
        failing = a if failed_side == "a" else b
        series = confidence_series(failing)
        if 0 <= root < len(series) and series[root] is not None:
            at_root = series[root]
            baseline = _baseline(series, exclude={root})
            if baseline is not None:
                drop = round(baseline - at_root, 4)
                flagged = drop >= FLAG_DROP
                lead = _onset(series, root, baseline) if flagged else 0
                result["signal"] = {
                    "failed_agent": failed_side,
                    "root_cause_step": root,
                    "confidence_at_root": round(at_root, 4),
                    "baseline_confidence": round(baseline, 4),
                    "drop": drop,
                    "verdict": "flagged" if flagged else "silent",
                    "lead_steps": lead,
                    "mitigation": (
                        "a runtime confidence gate would have caught this step"
                        if flagged else
                        "no confidence threshold would have caught this; the model "
                        "was as sure here as anywhere — mitigate with an external "
                        "verification step instead"
                    ),
                }
                result["calibration"] = {
                    "confident_when_wrong": at_root >= OVERCONFIDENT_AT,
                    "confidence_at_wrong_step": round(at_root, 4),
                }

    result["narrative"] = _narrative(result, a, b, failed_side)
    return result


def _narrative(result: dict, a: Trajectory, b: Trajectory,
               failed_side: Optional[str]) -> str:
    parts: list[str] = []
    for side, trajectory in (("a", a), ("b", b)):
        stats = result[side]
        if stats["mean_confidence"] is not None:
            parts.append(
                f"{trajectory.agent.name} ran at {stats['mean_confidence']:.0%} mean "
                f"token confidence (low of {stats['min_confidence']:.0%})."
            )
    signal = result.get("signal")
    if signal:
        failing = a if signal["failed_agent"] == "a" else b
        if signal["verdict"] == "flagged":
            lead = signal["lead_steps"]
            when = (
                f"{lead} step(s) before it acted" if lead
                else "as it acted"
            )
            parts.append(
                f"The model knew: {failing.agent.name}'s confidence fell to "
                f"{signal['confidence_at_root']:.0%} at the root-cause step "
                f"(step {signal['root_cause_step']}), "
                f"{signal['drop']:.0%} below its own baseline, {when}. "
                f"A confidence gate would have caught this run."
            )
        else:
            parts.append(
                f"The model gave no warning: {failing.agent.name} was "
                f"{signal['confidence_at_root']:.0%} confident at the step that "
                f"caused the failure — no lower than its own baseline of "
                f"{signal['baseline_confidence']:.0%}. This failure class needs "
                f"external verification, not a confidence threshold."
            )
    elif failed_side:
        parts.append(
            "A run failed, but the failing step carries no model telemetry, so "
            "no confidence signal can be read for it."
        )
    return " ".join(parts)


def calibration_profile(reports: list[dict]) -> dict:
    """Per-agent calibration across a batch: does confidence fall when the
    agent is about to be wrong?

    ``flagged`` failures are those the model signalled; ``silent`` ones it did
    not.  An agent with mostly silent failures cannot be supervised by
    thresholding its own confidence, however strong its success rate.
    """
    agents: dict[str, dict] = {}
    for report in reports:
        analysis = report.get("uncertainty") or {}
        if not analysis.get("available"):
            continue
        signal = analysis.get("signal")
        if not signal:
            continue
        side = signal["failed_agent"]
        name = report[side]["agent"]["name"]
        row = agents.setdefault(name, {
            "failures_with_telemetry": 0, "flagged": 0, "silent": 0,
            "confidence_when_wrong": [],
        })
        row["failures_with_telemetry"] += 1
        row[signal["verdict"]] += 1
        row["confidence_when_wrong"].append(signal["confidence_at_root"])

    profile: dict = {}
    for name in sorted(agents):
        row = agents[name]
        confidences = row.pop("confidence_when_wrong")
        total = row["failures_with_telemetry"]
        row["mean_confidence_when_wrong"] = (
            round(sum(confidences) / len(confidences), 4) if confidences else None
        )
        row["flagged_rate"] = round(row["flagged"] / total, 4) if total else None
        row["verdict"] = (
            "supervisable" if total and row["flagged"] / total >= 0.5
            else "silent-failing"
        )
        profile[name] = row

    if not profile:
        return {"available": False, "agents": {}, "narrative": ""}

    supervisable = [n for n, r in profile.items() if r["verdict"] == "supervisable"]
    silent = [n for n, r in profile.items() if r["verdict"] == "silent-failing"]
    parts = []
    if supervisable:
        parts.append(
            f"{', '.join(supervisable)} signal their own mistakes — a confidence "
            f"gate would catch most of their failures."
        )
    if silent:
        parts.append(
            f"{', '.join(silent)} fail silently: confidence stays high while the "
            f"answer goes wrong, so these need verification steps rather than "
            f"uncertainty thresholds."
        )
    return {"available": True, "agents": profile, "narrative": " ".join(parts)}
