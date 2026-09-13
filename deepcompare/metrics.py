"""Metrics and aggregation for DeepCompare AI.

Per-trajectory metrics (steps, tokens, cost, latency, tool calls, searches),
the SCHEMA.md ``metrics_delta`` object, and :func:`aggregate` which rolls a
list of comparison reports up into success rates, per-agent means,
failure-origin percentages by divergence kind, and regression flags.
"""

from __future__ import annotations

from typing import Optional

from .diagnosis import systemic_diagnosis
from .efficiency import aggregate_efficiency
from .taxonomy import classify_batch
from .variance import variance_report
from .recommend import recommend
from .semantic import semantic_profile
from .success import playbook
from .trace import Trajectory
from .uncertainty import calibration_profile
from .issues import build_issues
from .attributes import attribute_analysis
from .joint import joint_attribute_model
from .triage import triage

#: metric key -> human label used in regression messages.
_REGRESSION_METRICS = {
    "tokens": "tokens",
    "cost_usd": "cost",
    "latency_s": "latency",
    "steps": "steps",
    "tool_calls": "tool calls",
    "searches": "searches",
}

#: relative worsening beyond which a metric counts as a regression.
REGRESSION_THRESHOLD = 0.10


def trajectory_metrics(t: Trajectory) -> dict:
    """Flat metrics for one trajectory."""
    return {
        "steps": len(t.steps),
        "input_tokens": t.totals.input_tokens,
        "output_tokens": t.totals.output_tokens,
        "tokens": t.totals.input_tokens + t.totals.output_tokens,
        "cost_usd": round(t.totals.cost_usd, 6),
        "latency_s": round(t.totals.latency_s, 4),
        "tool_calls": sum(1 for s in t.steps if s.type == "tool_call"),
        "searches": sum(1 for s in t.steps if s.type == "search"),
    }


def metrics_delta(a: Trajectory, b: Trajectory) -> dict:
    """Side-by-side metric comparison per SCHEMA.md ``metrics_delta``."""
    ma, mb = trajectory_metrics(a), trajectory_metrics(b)
    return {
        "steps": {"a": ma["steps"], "b": mb["steps"]},
        "tokens": {"a": ma["tokens"], "b": mb["tokens"]},
        "cost_usd": {"a": ma["cost_usd"], "b": mb["cost_usd"]},
        "latency_s": {"a": ma["latency_s"], "b": mb["latency_s"]},
        "tool_calls": {"a": ma["tool_calls"], "b": mb["tool_calls"]},
        "searches": {"a": ma["searches"], "b": mb["searches"]},
    }


def _report_side_metrics(side: dict) -> dict:
    """Metrics for one side ('a'/'b') of an already-built comparison report."""
    totals = side["totals"]
    steps = side["steps"]
    return {
        "steps": len(steps),
        "tokens": totals["input_tokens"] + totals["output_tokens"],
        "cost_usd": totals["cost_usd"],
        "latency_s": totals["latency_s"],
        "tool_calls": sum(1 for s in steps if s["type"] == "tool_call"),
        "searches": sum(1 for s in steps if s["type"] == "search"),
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def task_signal(reports: list[dict], stability: Optional[dict] = None) -> list[dict]:
    """Per-task difficulty and discrimination (SCHEMA.md v9 ``task_signal``).

    ``difficulty`` = 1 - mean success across sides (across all runs when a
    ``stability`` object is provided, else across the single pair).
    ``discrimination`` = the success-rate gap when the sides differ; when
    successes tie, the normalized cost+latency gap (each gap divided by the
    larger side's value, averaged, capped at 1.0).  Sorted most
    discriminating first, then hardest, then task id.
    """
    stability_by_task: dict[str, dict] = {}
    if stability:
        for entry in stability.get("per_task", []):
            stability_by_task[entry["task"]] = entry

    signals: list[dict] = []
    for report in reports:
        tid = report["task"]["id"]
        entry = stability_by_task.get(tid)
        if entry:
            succ = entry["a"]["successes"] + entry["b"]["successes"]
            runs = entry["a"]["runs"] + entry["b"]["runs"]
            rate_a = entry["a"]["successes"] / entry["a"]["runs"]
            rate_b = entry["b"]["successes"] / entry["b"]["runs"]
            mean_success = succ / runs if runs else 0.0
        else:
            rate_a = 1.0 if report["a"]["outcome"]["success"] else 0.0
            rate_b = 1.0 if report["b"]["outcome"]["success"] else 0.0
            mean_success = (rate_a + rate_b) / 2
        difficulty = round(1.0 - mean_success, 4)

        gap = abs(rate_a - rate_b)
        if gap > 0:
            discrimination = round(gap, 4)
            if gap >= 1.0:
                note = "separates the agents: one side always fails it"
            else:
                note = f"separates the agents: success gap {gap:.0%}"
        else:
            delta = report["metrics_delta"]
            gaps = []
            for key in ("cost_usd", "latency_s"):
                hi = max(delta[key]["a"], delta[key]["b"])
                lo = min(delta[key]["a"], delta[key]["b"])
                gaps.append((hi - lo) / hi if hi > 0 else 0.0)
            discrimination = round(min(1.0, sum(gaps) / len(gaps)), 4)
            if discrimination > 0:
                note = "same outcomes; separates on cost/latency"
            else:
                note = "no signal: identical outcomes and costs"
        signals.append(
            {
                "task": tid,
                "difficulty": difficulty,
                "discrimination": discrimination,
                "note": note,
            }
        )
    signals.sort(key=lambda s: (-s["discrimination"], -s["difficulty"], s["task"]))
    return signals


def aggregate(reports: list[dict]) -> dict:
    """Roll up a list of comparison reports (SCHEMA.md report objects).

    Returns success rate and mean tokens/cost/latency/steps per agent,
    failure-origin percentages by divergence ``kind`` across tasks where a
    failure was attributed, human-readable regression flags for cases where
    agent B improves success rate but worsens another metric by more than
    10%, and evidence-based recommendations (see ``deepcompare.recommend``).
    """
    if not reports:
        return {
            "tasks": 0,
            "agents": {"a": None, "b": None},
            "success_rate": {"a": 0.0, "b": 0.0},
            "means": {"a": {}, "b": {}},
            "failure_origins": {},
            "regressions": [],
            "recommendations": [],
            "playbook": [],
            "semantic_profile": {},
            "task_signal": [],
            "efficiency": aggregate_efficiency([]),
            "diagnosis": systemic_diagnosis([]),
            "triage": triage([], {}),
        }

    name_a = reports[0]["a"]["agent"]["name"]
    name_b = reports[0]["b"]["agent"]["name"]

    success: dict[str, list[bool]] = {"a": [], "b": []}
    per_metric: dict[str, dict[str, list[float]]] = {
        side: {k: [] for k in _REGRESSION_METRICS} for side in ("a", "b")
    }
    origin_counts: dict[str, int] = {}

    for report in reports:
        for side in ("a", "b"):
            success[side].append(bool(report[side]["outcome"]["success"]))
            m = _report_side_metrics(report[side])
            for key in _REGRESSION_METRICS:
                per_metric[side][key].append(m[key])
        attribution = report.get("attribution") or {}
        if attribution.get("failed_agent") is not None:
            category = attribution.get("category") or "unknown"
            origin_counts[category] = origin_counts.get(category, 0) + 1

    n = len(reports)
    success_rate = {
        "a": round(sum(success["a"]) / n, 4),
        "b": round(sum(success["b"]) / n, 4),
    }
    means = {
        side: {key: _mean(values) for key, values in per_metric[side].items()}
        for side in ("a", "b")
    }

    attributed = sum(origin_counts.values())
    failure_origins = {
        kind: round(count / attributed, 4)
        for kind, count in sorted(origin_counts.items())
    } if attributed else {}

    regressions: list[str] = []
    sr_gain = success_rate["b"] - success_rate["a"]
    if sr_gain > 0:
        for key, label in _REGRESSION_METRICS.items():
            base, new = means["a"][key], means["b"][key]
            if base > 0 and new > base * (1 + REGRESSION_THRESHOLD):
                worse_pct = (new / base - 1.0) * 100.0
                regressions.append(
                    f"B ({name_b}) improved success rate by {sr_gain * 100:+.0f}% "
                    f"but uses {worse_pct:.0f}% more {label} than A ({name_a})."
                )

    result = {
        "tasks": n,
        "agents": {"a": name_a, "b": name_b},
        "success_rate": success_rate,
        "means": means,
        "failure_origins": failure_origins,
        "regressions": regressions,
        "recommendations": recommend(reports),
        "playbook": playbook(reports),
        "calibration": calibration_profile(reports),
        "issues": build_issues(reports),
        # Attribute analysis needs whole trajectories; reconstruct the
        # corpus from both sides of every report.
        "attributes": attribute_analysis(_corpus_from_reports(reports)),
        "attributes_joint": joint_attribute_model(_corpus_from_reports(reports)),
        "semantic_profile": semantic_profile(reports),
        "task_signal": task_signal(reports),
        # Where the spend is structural: prompt-cache / result-cache /
        # parallelism ceilings and cost per success, per agent.
        "efficiency": aggregate_efficiency(reports),
        # Which diagnosed causes repeat across the batch: leading-hypothesis
        # kinds rolled up with the denominator stated, so a systemic cause
        # reads as one central fix rather than N local ones.
        "diagnosis": systemic_diagnosis(reports),
        # Published failure vocabulary alongside AgentDiff's own, so a
        # finding here is citable and comparable with other people's.
        "taxonomy": classify_batch(reports),
        # Where the variation actually comes from. Two agents on one model
        # each is a confounded design, and the report says so rather than
        # inventing a model-versus-harness split.
        "variance": variance_report(_corpus_from_reports(reports)),
    }
    # Triage reads every other block, so it is computed last and folded in.
    # It ranks what is already here rather than adding a new measurement,
    # which is why it takes the finished aggregate as its input.
    result["triage"] = triage(reports, result)
    return result


def _corpus_from_reports(reports: list[dict]) -> list:
    """Rebuild Trajectory objects from both sides of each report.

    ``aggregate`` only receives reports, but attribute analysis is defined
    over runs; each report carries both runs in full, so the corpus can be
    reconstructed without changing the aggregate() signature.
    """
    from .trace import Trajectory
    corpus = []
    for report in reports:
        for side in ("a", "b"):
            payload = {
                "schema_version": 1,
                "trace_id": f"{report['task']['id']}-{side}",
                "agent": report[side]["agent"],
                "task": {"id": report["task"]["id"],
                         "prompt": report["task"]["prompt"], "expected": None},
                "outcome": report[side]["outcome"],
                "totals": report[side]["totals"],
                "steps": report[side]["steps"],
            }
            try:
                corpus.append(Trajectory.from_json(payload))
            except ValueError:
                continue
    return corpus
