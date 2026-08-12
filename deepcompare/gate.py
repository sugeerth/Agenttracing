"""Regression gate for DeepCompare AI (SCHEMA.md v8 CLI contract).

Pairs baseline and candidate traces by task id, compares them (baseline =
side a, candidate = side b), and evaluates gate checks: success-rate drop,
mean-cost increase, mean-latency increase, and new failure-origin
categories.  Produces the ``gate.json`` payload and an optional shareable
Markdown summary whose actionable core is the per-regressed-task divergence
summary, attribution, and counterfactual savings.
"""

from __future__ import annotations

from typing import Optional

from .metrics import aggregate as build_aggregate
from .report import compare
from .trace import Trajectory
from .statistics import (
    describe_significance,
    paired_bootstrap_difference,
    wilson_interval,
)

DEFAULT_THRESHOLDS = {
    "max_success_drop": 0.0,
    "max_cost_increase": 0.10,
    "max_latency_increase": 0.25,
}


def pair_gate_traces(
    baseline: list[Trajectory], candidate: list[Trajectory]
) -> tuple[str, str, list[tuple[Trajectory, Trajectory]]]:
    """Validate the two trace sets and pair them by task id.

    Each side must contain exactly one agent name.  Task sets are reduced to
    their intersection (the caller warns about dropped tasks).  Raises
    ``ValueError`` on empty sides, multiple agents per side, or an empty
    intersection.
    """
    for label, traces in (("baseline", baseline), ("candidate", candidate)):
        if not traces:
            raise ValueError(f"{label} directory contains no valid traces")
        names = sorted({t.agent.name for t in traces})
        if len(names) != 1:
            raise ValueError(
                f"{label} directory must contain exactly one agent, "
                f"found {len(names)}: {', '.join(names)}"
            )
    base_name = baseline[0].agent.name
    cand_name = candidate[0].agent.name
    base_by_task = {t.task.id: t for t in baseline}
    cand_by_task = {t.task.id: t for t in candidate}
    shared = sorted(set(base_by_task) & set(cand_by_task))
    if not shared:
        raise ValueError("baseline and candidate share no task ids")
    pairs = [(base_by_task[tid], cand_by_task[tid]) for tid in shared]
    return base_name, cand_name, pairs


def _failure_categories(reports: list[dict], side: str) -> set[str]:
    cats: set[str] = set()
    for report in reports:
        attribution = report["attribution"]
        if attribution["failed_agent"] == side and attribution["category"]:
            cats.add(attribution["category"])
    return cats


def evaluate_gate(
    reports: list[dict],
    thresholds: Optional[dict] = None,
    allow_new_failure_modes: bool = False,
) -> dict:
    """Evaluate the four gate checks over baseline-vs-candidate reports.

    Returns the gate dict: ``checks`` (name, pass, baseline, candidate,
    threshold, detail), overall ``verdict``, agent names, and a per-task
    summary flagging regressions (baseline succeeded, candidate failed).
    """
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update(thresholds)
    agg = build_aggregate(reports)
    sr_base, sr_cand = agg["success_rate"]["a"], agg["success_rate"]["b"]
    cost_base = agg["means"]["a"]["cost_usd"]
    cost_cand = agg["means"]["b"]["cost_usd"]
    lat_base = agg["means"]["a"]["latency_s"]
    lat_cand = agg["means"]["b"]["latency_s"]

    checks: list[dict] = []

    drop = round(sr_base - sr_cand, 4)
    # Put a noise floor under the comparison: on a short suite a single
    # flipped task moves the rate several points, so report whether the drop
    # survives resampling rather than only whether it crossed a threshold.
    base_outcomes = [bool(r["a"]["outcome"]["success"]) for r in reports]
    cand_outcomes = [bool(r["b"]["outcome"]["success"]) for r in reports]
    bootstrap = paired_bootstrap_difference(base_outcomes, cand_outcomes)
    n_tasks = len(reports)
    checks.append(
        {
            "name": "success_rate_drop",
            "pass": drop <= th["max_success_drop"] + 1e-9,
            "baseline": sr_base,
            "candidate": sr_cand,
            "threshold": th["max_success_drop"],
            "baseline_ci": list(wilson_interval(sum(base_outcomes), n_tasks)),
            "candidate_ci": list(wilson_interval(sum(cand_outcomes), n_tasks)),
            "bootstrap": bootstrap,
            "significant": bootstrap["significant"],
            "detail": f"success rate changed {sr_base:.0%} -> {sr_cand:.0%} "
            f"(drop {drop:+.1%}, allowed {th['max_success_drop']:.1%}). "
            + describe_significance(bootstrap, n_tasks),
        }
    )

    def relative_rise(base: float, cand: float) -> Optional[float]:
        if base > 0:
            return (cand - base) / base
        return None if cand <= 0 else float("inf")

    for name, label, base, cand, limit in (
        ("cost_increase", "cost", cost_base, cost_cand, th["max_cost_increase"]),
        ("latency_increase", "latency", lat_base, lat_cand, th["max_latency_increase"]),
    ):
        rise = relative_rise(base, cand)
        ok = rise is None or rise <= limit + 1e-9
        rise_txt = "n/a (baseline 0)" if rise is None else f"{rise:+.1%}"
        checks.append(
            {
                "name": name,
                "pass": ok,
                "baseline": base,
                "candidate": cand,
                "threshold": limit,
                "detail": f"mean {label} changed {base:g} -> {cand:g} "
                f"({rise_txt}, allowed +{limit:.0%})",
            }
        )

    base_modes = _failure_categories(reports, "a")
    cand_modes = _failure_categories(reports, "b")
    new_modes = sorted(cand_modes - base_modes)
    if allow_new_failure_modes:
        checks.append(
            {
                "name": "new_failure_modes",
                "pass": True,
                "baseline": sorted(base_modes),
                "candidate": sorted(cand_modes),
                "threshold": None,
                "detail": "disabled by --allow-new-failure-modes",
            }
        )
    else:
        checks.append(
            {
                "name": "new_failure_modes",
                "pass": not new_modes,
                "baseline": sorted(base_modes),
                "candidate": sorted(cand_modes),
                "threshold": None,
                "detail": (
                    f"new failure-origin categories: {', '.join(new_modes)}"
                    if new_modes
                    else "no new failure-origin categories"
                ),
            }
        )

    summary = [
        {
            "task": report["task"]["id"],
            "baseline_success": report["a"]["outcome"]["success"],
            "candidate_success": report["b"]["outcome"]["success"],
            "regressed": report["a"]["outcome"]["success"]
            and not report["b"]["outcome"]["success"],
        }
        for report in reports
    ]

    return {
        "baseline_agent": agg["agents"]["a"],
        "candidate_agent": agg["agents"]["b"],
        "tasks": len(reports),
        "thresholds": {**th, "allow_new_failure_modes": allow_new_failure_modes},
        "checks": checks,
        "verdict": "pass" if all(c["pass"] for c in checks) else "fail",
        "reports_summary": summary,
    }


def render_gate_markdown(gate: dict, reports: list[dict]) -> str:
    """Shareable Markdown summary of a gate run."""
    ok = gate["verdict"] == "pass"
    lines = [
        f"# Regression gate: {'✅ PASS' if ok else '❌ FAIL'}",
        "",
        f"Baseline **{gate['baseline_agent']}** vs candidate "
        f"**{gate['candidate_agent']}** across {gate['tasks']} task(s).",
        "",
        "| check | baseline | candidate | threshold | result |",
        "|---|---|---|---|---|",
    ]
    for check in gate["checks"]:
        result = "✅ pass" if check["pass"] else "❌ fail"
        threshold = check["threshold"]
        lines.append(
            f"| {check['name']} | {check['baseline']} | {check['candidate']} | "
            f"{'—' if threshold is None else threshold} | {result} |"
        )
    lines.append("")
    for check in gate["checks"]:
        lines.append(f"- `{check['name']}`: {check['detail']}")

    regressed = [
        r for r in reports
        if r["a"]["outcome"]["success"] and not r["b"]["outcome"]["success"]
    ]
    if regressed:
        lines += ["", "## Regressed tasks", ""]
        for report in regressed:
            lines.append(f"### {report['task']['id']}")
            if report["divergences"]:
                lines.append(f"- First divergence: {report['divergences'][0]['summary']}")
            lines.append(f"- Attribution: {report['attribution']['explanation']}")
            cf = report.get("counterfactual")
            if cf:
                lines.append(f"- Counterfactual: {cf['narrative']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
