"""Metrics and aggregation for DeepCompare AI.

Per-trajectory metrics (steps, tokens, cost, latency, tool calls, searches),
the SCHEMA.md ``metrics_delta`` object, and :func:`aggregate` which rolls a
list of comparison reports up into success rates, per-agent means,
failure-origin percentages by divergence kind, and regression flags.
"""

from __future__ import annotations

from typing import Optional

from .trace import Trajectory

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


def aggregate(reports: list[dict]) -> dict:
    """Roll up a list of comparison reports (SCHEMA.md report objects).

    Returns success rate and mean tokens/cost/latency/steps per agent,
    failure-origin percentages by divergence ``kind`` across tasks where a
    failure was attributed, and human-readable regression flags for cases
    where agent B improves success rate but worsens another metric by more
    than 10%.
    """
    if not reports:
        return {
            "tasks": 0,
            "agents": {"a": None, "b": None},
            "success_rate": {"a": 0.0, "b": 0.0},
            "means": {"a": {}, "b": {}},
            "failure_origins": {},
            "regressions": [],
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

    return {
        "tasks": n,
        "agents": {"a": name_a, "b": name_b},
        "success_rate": success_rate,
        "means": means,
        "failure_origins": failure_origins,
        "regressions": regressions,
    }
