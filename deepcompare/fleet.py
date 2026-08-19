"""N-agent fleet analysis for DeepCompare AI.

Implements the SCHEMA.md "Fleet report" contract: per-agent metrics,
min-max-normalized dimension scores, a weighted composite score with dense
ranking, a Pareto frontier over (success_rate, cost, latency), per-agent
failure fingerprints derived from pairwise comparison against the fleet's
success leader, plain-language rationales, and spotlight pairs backed by
full pairwise comparison reports.

``wasted_tool_calls`` definition: for each task, the reference is the
*fleet-minimum successful* trajectory — the successful trajectory on that
task with the fewest tool-ish steps (``tool_call`` + ``search``).  An
agent's wasted calls on the task are ``max(0, own tool-ish steps − reference
tool-ish steps)``: calls beyond what the leanest successful run needed, i.e.
calls that demonstrably did not change the outcome.  Tasks nobody solved are
skipped.  The reported number is the mean over counted tasks.
"""

from __future__ import annotations

from typing import Optional

from .report import compare
from .trace import Trajectory

#: default composite weights, per the SCHEMA.md fleet contract.
DEFAULT_WEIGHTS: dict[str, float] = {
    "success": 0.45,
    "cost": 0.15,
    "latency": 0.10,
    "tool_discipline": 0.15,
    "step_economy": 0.15,
}

_SCORING_METHOD = "min-max normalized per dimension across the fleet; composite = weighted sum"

#: dimension -> (higher_is_better, human label used in rationales).
_DIMENSIONS: dict[str, tuple[bool, str]] = {
    "success": (True, "success"),
    "cost": (False, "cost"),
    "latency": (False, "latency"),
    "tool_discipline": (False, "tool discipline"),
    "step_economy": (False, "step economy"),
}

_MAX_SPOTLIGHT_PAIRS = 6
_MAX_TASKS_PER_PAIR = 3


def _diagnosed_kind(report: dict) -> str:
    """Failure-fingerprint category from the adjudicated diagnosis.

    The diagnosis outranks raw attribution here: a failure whose leading
    hypothesis is the grader should not fingerprint an agent as
    "reasoning-prone", and a divergence-led diagnosis carries a category
    corrected for what the root step actually is (a plan step is planning,
    whatever the divergence detector filed it under).  Contested diagnoses
    are counted as contested — smearing them over a guessed category would
    fabricate a fingerprint.
    """
    diag = report.get("diagnosis") or {}
    lead = next((h for h in diag.get("hypotheses", [])
                 if h.get("id") == diag.get("leading")), None)
    if lead is None:
        if diag.get("hypotheses"):
            return "contested"
        return (report.get("attribution") or {}).get("category") or "unknown"
    kind = lead.get("kind", "unknown")
    if kind == "divergence":
        return lead.get("category") or "divergence"
    if lead.get("flag"):
        return f"{kind}:{lead['flag']}"
    return kind


def _toolish_count(t: Trajectory) -> int:
    return sum(1 for s in t.steps if s.type in ("tool_call", "search"))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _normalize(values: dict[str, float], higher_better: bool) -> dict[str, float]:
    """Min-max normalize a dimension across the fleet; all-equal -> 1.0."""
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-12:
        return {name: 1.0 for name in values}
    if higher_better:
        return {n: round((v - lo) / (hi - lo), 4) for n, v in values.items()}
    return {n: round((hi - v) / (hi - lo), 4) for n, v in values.items()}


def _dimension_detail(metrics: dict, dim: str) -> str:
    """Real-number detail string for a dimension, used in rationales."""
    if dim == "success":
        return f"{metrics['success_rate']:.0%} success"
    if dim == "cost":
        return f"${metrics['mean_cost_usd']:.4f}/task"
    if dim == "latency":
        return f"{metrics['mean_latency_s']:.1f}s/task"
    if dim == "tool_discipline":
        calls = metrics["mean_tool_calls"] + metrics["mean_searches"]
        return f"{calls:.1f} calls/task"
    return f"{metrics['mean_steps']:.1f} steps/task"


def _pick_spotlight_tasks(a_by_task: dict, b_by_task: dict, task_ids: list[str]) -> list[tuple[str, dict]]:
    """Pairwise reports for the most informative tasks of one spotlight pair.

    Keeps only tasks where the two agents diverge or exactly one fails,
    preferring failed-and-diverging tasks, capped at _MAX_TASKS_PER_PAIR.
    """
    candidates: list[tuple[int, str, dict]] = []
    for tid in task_ids:
        report = compare(a_by_task[tid], b_by_task[tid])
        diverged = bool(report["divergences"])
        one_failed = report["a"]["outcome"]["success"] != report["b"]["outcome"]["success"]
        if not diverged and not one_failed:
            continue
        priority = 0 if (one_failed and diverged) else (1 if one_failed else 2)
        candidates.append((priority, tid, report))
    candidates.sort(key=lambda c: (c[0], c[1]))
    return [(tid, report) for _, tid, report in candidates[:_MAX_TASKS_PER_PAIR]]


def fleet_analysis(
    trajs_by_agent: dict[str, list[Trajectory]],
    weights: Optional[dict[str, float]] = None,
) -> dict:
    """Analyze a fleet of N agents that all ran the same task set.

    ``trajs_by_agent`` maps agent name -> list of trajectories (one per
    task); every agent must cover the same task ids (ValueError otherwise).
    ``weights`` may override any subset of the default composite weights.

    Returns ``{"fleet": <SCHEMA.md fleet dict>, "reports": [pairwise
    comparison reports]}`` where ``fleet["spotlight_pairs"][i]
    ["report_indices"]`` index into the returned ``reports`` list.
    See the module docstring for the ``wasted_tool_calls`` definition.
    """
    if not trajs_by_agent:
        raise ValueError("fleet_analysis needs at least one agent")

    w = dict(DEFAULT_WEIGHTS)
    if weights:
        unknown = set(weights) - set(DEFAULT_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown weight dimension(s): {', '.join(sorted(unknown))}")
        w.update(weights)

    # Index trajectories per agent per task; require identical task sets.
    by_agent: dict[str, dict[str, Trajectory]] = {}
    task_ids: Optional[list[str]] = None
    for name in sorted(trajs_by_agent):
        tasks: dict[str, Trajectory] = {}
        for t in trajs_by_agent[name]:
            if t.task.id in tasks:
                raise ValueError(f"agent {name!r} has duplicate trajectories for task {t.task.id!r}")
            tasks[t.task.id] = t
        ids = sorted(tasks)
        if task_ids is None:
            task_ids = ids
        elif ids != task_ids:
            raise ValueError(
                f"agent {name!r} task set {ids} does not match the fleet task set {task_ids}"
            )
        by_agent[name] = tasks
    assert task_ids is not None
    names = sorted(by_agent)
    n_agents = len(names)

    # Per-task reference tool-ish counts (fleet-minimum successful run).
    ref_toolish: dict[str, Optional[int]] = {}
    for tid in task_ids:
        successful = [_toolish_count(by_agent[n][tid]) for n in names
                      if by_agent[n][tid].outcome.success]
        ref_toolish[tid] = min(successful) if successful else None

    # Per-agent metrics.
    metrics: dict[str, dict] = {}
    per_task: dict[str, dict[str, dict]] = {}
    for name in names:
        runs = [by_agent[name][tid] for tid in task_ids]
        wasted: list[float] = []
        for tid in task_ids:
            ref = ref_toolish[tid]
            if ref is None:
                continue
            wasted.append(max(0, _toolish_count(by_agent[name][tid]) - ref))
        metrics[name] = {
            "success_rate": round(_mean([1.0 if t.outcome.success else 0.0 for t in runs]), 4),
            "mean_tokens": round(_mean([t.totals.input_tokens + t.totals.output_tokens for t in runs]), 2),
            "mean_cost_usd": round(_mean([t.totals.cost_usd for t in runs]), 6),
            "mean_latency_s": round(_mean([t.totals.latency_s for t in runs]), 4),
            "mean_steps": round(_mean([float(len(t.steps)) for t in runs]), 4),
            "mean_tool_calls": round(_mean([float(sum(1 for s in t.steps if s.type == "tool_call")) for t in runs]), 4),
            "wasted_tool_calls": round(_mean(wasted), 4) if wasted else 0.0,
            "mean_searches": round(_mean([float(sum(1 for s in t.steps if s.type == "search")) for t in runs]), 4),
            "bad_steps": sum(1 for t in runs for s in t.steps if s.quality == "bad"),
            "weak_steps": sum(1 for t in runs for s in t.steps if s.quality == "weak"),
        }
        per_task[name] = {
            tid: {
                "success": by_agent[name][tid].outcome.success,
                "tokens": by_agent[name][tid].totals.input_tokens
                + by_agent[name][tid].totals.output_tokens,
                "latency_s": by_agent[name][tid].totals.latency_s,
                "steps": len(by_agent[name][tid].steps),
                "tool_calls": sum(1 for s in by_agent[name][tid].steps if s.type == "tool_call"),
            }
            for tid in task_ids
        }

    # Dimension scores (min-max normalized; 1.0 = best in fleet).
    raw_dims: dict[str, dict[str, float]] = {
        "success": {n: metrics[n]["success_rate"] for n in names},
        "cost": {n: metrics[n]["mean_cost_usd"] for n in names},
        "latency": {n: metrics[n]["mean_latency_s"] for n in names},
        "tool_discipline": {
            n: metrics[n]["mean_tool_calls"] + metrics[n]["mean_searches"]
            + metrics[n]["wasted_tool_calls"]
            for n in names
        },
        "step_economy": {n: metrics[n]["mean_steps"] for n in names},
    }
    dim_scores: dict[str, dict[str, float]] = {
        dim: _normalize(raw_dims[dim], _DIMENSIONS[dim][0]) for dim in _DIMENSIONS
    }

    scores = {
        n: round(sum(w[dim] * dim_scores[dim][n] for dim in _DIMENSIONS), 4) for n in names
    }

    # Dense rank by composite score (ties share a rank), name as tie-break order.
    ordered = sorted(names, key=lambda n: (-scores[n], n))
    ranks: dict[str, int] = {}
    rank = 0
    prev_score: Optional[float] = None
    for name in ordered:
        if scores[name] != prev_score:
            rank += 1
            prev_score = scores[name]
        ranks[name] = rank

    # Pareto frontier on (success_rate, -cost, -latency).
    dominated_by: dict[str, int] = {n: 0 for n in names}
    for name in names:
        for other in names:
            if other == name:
                continue
            mo, mn = metrics[other], metrics[name]
            ge = (
                mo["success_rate"] >= mn["success_rate"]
                and mo["mean_cost_usd"] <= mn["mean_cost_usd"]
                and mo["mean_latency_s"] <= mn["mean_latency_s"]
            )
            gt = (
                mo["success_rate"] > mn["success_rate"]
                or mo["mean_cost_usd"] < mn["mean_cost_usd"]
                or mo["mean_latency_s"] < mn["mean_latency_s"]
            )
            if ge and gt:
                dominated_by[name] += 1

    # Failure fingerprints: compare each agent against the success leader
    # (the leader itself is compared against the runner-up-by-success).
    by_success = sorted(names, key=lambda n: (-metrics[n]["success_rate"], -scores[n], n))
    success_leader = by_success[0]
    fingerprints: dict[str, dict[str, float]] = {n: {} for n in names}
    if n_agents >= 2:
        for name in names:
            reference = success_leader if name != success_leader else by_success[1]
            counts: dict[str, int] = {}
            for tid in task_ids:
                report = compare(by_agent[name][tid], by_agent[reference][tid])
                attribution = report["attribution"]
                if attribution["failed_agent"] == "a":
                    counts_key = _diagnosed_kind(report)
                    counts[counts_key] = counts.get(counts_key, 0) + 1
            total = sum(counts.values())
            if total:
                fingerprints[name] = {
                    cat: round(cnt / total, 4) for cat, cnt in sorted(counts.items())
                }

    def dominant_failure(name: str) -> Optional[tuple[str, float]]:
        fp = fingerprints[name]
        if not fp:
            return None
        cat = max(sorted(fp), key=lambda c: fp[c])
        return cat, fp[cat]

    def archetype(name: str) -> str:
        dom = dominant_failure(name)
        if dom is not None:
            return f"{dom[0]}-prone"
        if ranks[name] == 1:
            return "champion"
        if dim_scores["tool_discipline"][name] >= 0.9 and dim_scores["step_economy"][name] >= 0.9:
            return "disciplined"
        return "balanced"

    leader_sr = metrics[success_leader]["success_rate"]

    def rationale(name: str) -> str:
        m = metrics[name]
        dims = {d: dim_scores[d][name] for d in _DIMENSIONS}
        best_dim = max(_DIMENSIONS, key=lambda d: (dims[d], d))
        best_label = _DIMENSIONS[best_dim][1]
        if dims[best_dim] >= 1.0:
            best_txt = f"best-in-fleet {best_label} ({_dimension_detail(m, best_dim)})"
        elif dims[best_dim] > 0.0:
            best_txt = f"strongest on {best_label} ({_dimension_detail(m, best_dim)})"
        else:
            best_txt = (
                f"trails the fleet on every dimension "
                f"({_dimension_detail(m, 'success')}, {_dimension_detail(m, 'cost')})"
            )
        parts = [f"Ranked #{ranks[name]} of {n_agents} (score {scores[name]:.2f}): {best_txt}"]
        gap = (leader_sr - m["success_rate"]) * 100
        if gap > 0:
            parts[0] += f" but {gap:.0f}pt below the success leader ({success_leader})."
        else:
            parts[0] += f" and fleet-best success rate ({m['success_rate']:.0%})."
        if dominated_by[name] == 0:
            parts.append(
                "On the Pareto frontier: no agent beats it on success, cost and latency at once."
            )
        else:
            parts.append(
                f"Off the Pareto frontier, dominated by {dominated_by[name]} agent(s) "
                f"on success/cost/latency."
            )
        dom = dominant_failure(name)
        if dom is not None:
            parts.append(
                f"Dominant failure mode: {dom[0]} ({dom[1]:.0%} of its attributed failures)."
            )
        elif m["success_rate"] >= 1.0:
            parts.append("No failures across the task set.")
        return " ".join(parts)

    # ---- Spotlight pair selection -------------------------------------
    champion = ordered[0]
    spotlight_specs: list[tuple[str, str, str]] = []
    seen_pairs: set[frozenset[str]] = set()

    def add_pair(x: str, y: str, why: str) -> None:
        if x == y or len(spotlight_specs) >= _MAX_SPOTLIGHT_PAIRS:
            return
        key = frozenset((x, y))
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        spotlight_specs.append((x, y, why))

    if n_agents >= 2:
        runner_up = ordered[1]
        add_pair(
            champion,
            runner_up,
            f"closest challenger: #1 {champion} (score {scores[champion]:.2f}) vs "
            f"#2 {runner_up} (score {scores[runner_up]:.2f}).",
        )
        worst = ordered[-1]
        add_pair(
            champion,
            worst,
            f"the full gap: champion {champion} vs lowest-ranked {worst} "
            f"({scores[champion]:.2f} vs {scores[worst]:.2f}).",
        )
        # Same success rate, most different cost.
        equal_pairs = [
            (x, y)
            for i, x in enumerate(ordered)
            for y in ordered[i + 1 :]
            if metrics[x]["success_rate"] == metrics[y]["success_rate"]
            and metrics[x]["mean_cost_usd"] != metrics[y]["mean_cost_usd"]
        ]
        if equal_pairs:
            x, y = max(
                equal_pairs,
                key=lambda p: (
                    abs(metrics[p[0]]["mean_cost_usd"] - metrics[p[1]]["mean_cost_usd"]),
                    p,
                ),
            )
            cost_gap = abs(metrics[x]["mean_cost_usd"] - metrics[y]["mean_cost_usd"])
            add_pair(
                x,
                y,
                f"equal outcomes, different process: both at "
                f"{metrics[x]['success_rate']:.0%} success but "
                f"${cost_gap:.4f}/task apart in cost.",
            )
        # One pair per distinct dominant failure archetype vs the champion.
        by_archetype: dict[str, str] = {}
        for name in ordered:
            if name == champion:
                continue
            dom = dominant_failure(name)
            if dom is None:
                continue
            cat = dom[0]
            best = by_archetype.get(cat)
            if best is None or (
                fingerprints[name].get(cat, 0.0),
                -ranks[name],
            ) > (fingerprints[best].get(cat, 0.0), -ranks[best]):
                by_archetype[cat] = name
        for cat in sorted(by_archetype):
            rep = by_archetype[cat]
            add_pair(
                champion,
                rep,
                f"archetypal {cat} failures: {rep} "
                f"({fingerprints[rep][cat]:.0%} of its failures) vs the champion.",
            )

    spotlight_reports: list[dict] = []
    spotlight_pairs: list[dict] = []
    for x, y, why in spotlight_specs:
        picked = _pick_spotlight_tasks(by_agent[x], by_agent[y], task_ids)
        indices = []
        for _tid, report in picked:
            indices.append(len(spotlight_reports))
            spotlight_reports.append(report)
        spotlight_pairs.append({"a": x, "b": y, "why": why, "report_indices": indices})

    fleet = {
        "tasks": [
            {"id": tid, "prompt": by_agent[names[0]][tid].task.prompt} for tid in task_ids
        ],
        "scoring": {"weights": {d: w[d] for d in _DIMENSIONS}, "method": _SCORING_METHOD},
        "agents": [
            {
                "name": name,
                "model": by_agent[name][task_ids[0]].agent.model if task_ids else "",
                "version": by_agent[name][task_ids[0]].agent.version if task_ids else "",
                "archetype": archetype(name),
                "rank": ranks[name],
                "score": scores[name],
                "pareto": dominated_by[name] == 0,
                "dominated_by": dominated_by[name],
                "metrics": metrics[name],
                "dimension_scores": {d: dim_scores[d][name] for d in _DIMENSIONS},
                "failure_fingerprint": fingerprints[name],
                "rationale": rationale(name),
                "per_task": per_task[name],
            }
            for name in ordered
        ],
        "spotlight_pairs": spotlight_pairs,
    }
    return {"fleet": fleet, "reports": spotlight_reports}
