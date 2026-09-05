"""Shapley credit assignment over divergence decisions (SCHEMA.md v15).

:mod:`deepcompare.attribution` answers "which step caused the failure" with a
propagation heuristic, and :mod:`deepcompare.counterfactual` prices a single
splice.  Neither divides the *blame* when a run went wrong in more than one
place, and naively summing each divergence's downstream cost double-counts,
because later divergences inherit the extra work earlier ones created.

Cooperative game theory solves exactly this allocation problem.  Treat each
divergence region as a player; the value of a coalition ``S`` is what the run
would have cost had it taken the reference agent's path at the regions in
``S`` and its own everywhere else.  The Shapley value of a region is then its
average marginal contribution across every ordering — the unique allocation
that is efficient (the parts sum to the whole), symmetric, and gives zero to
a decision that never changes anything.

**What makes this defensible on logged data.**  The value function never
simulates or re-runs anything: a coalition's trajectory is assembled from
steps that were actually recorded, on one side or the other, and its cost is
the sum of those observed step costs.  The estimate inherits the same
assumption the counterfactual module states openly — that adopting the
reference path at a decision point yields the steps the reference actually
took — and nothing more.

Cost is allocated rigorously.  *Outcome* credit is only assigned when exactly
one divergence was causal, because attributing a binary outcome across
several decisions would require counterfactual re-runs this engine cannot do;
see ``outcome_attributable`` in the result.
"""

from __future__ import annotations

from itertools import combinations
from math import factorial
from typing import Optional

from .trace import Trajectory

#: above this many divergence regions, exact enumeration is skipped.
EXACT_LIMIT = 12


def _regions(report: dict) -> list[list[int]]:
    """Alignment row indices belonging to each divergence region, in order.

    A divergence's ``a_index``/``b_index`` can land in different rows when the
    region is one-sided, so a region is collected as the maximal run of
    non-match rows containing either index.
    """
    alignment = report["alignment"]
    runs: list[list[int]] = []
    current: list[int] = []
    for i, row in enumerate(alignment):
        if row["op"] == "match":
            if current:
                runs.append(current)
                current = []
        else:
            current.append(i)
    if current:
        runs.append(current)
    return runs


def _side_cost(trajectory: Trajectory) -> float:
    total = sum(s.tokens for s in trajectory.steps)
    return trajectory.totals.cost_usd / total if total > 0 else 0.0


def _coalition_cost(
    report: dict,
    regions: list[list[int]],
    coalition: frozenset[int],
    loser: str,
    winner: str,
    a: Trajectory,
    b: Trajectory,
) -> dict:
    """Cost of the run when the regions in ``coalition`` follow the winner.

    Every step counted here was actually observed on one side or the other;
    nothing is simulated.
    """
    alignment = report["alignment"]
    in_coalition: dict[int, bool] = {}
    for index, rows in enumerate(regions):
        for row in rows:
            in_coalition[row] = index in coalition

    loser_traj = a if loser == "a" else b
    winner_traj = a if winner == "a" else b
    loser_rate = _side_cost(loser_traj)
    winner_rate = _side_cost(winner_traj)

    tokens = latency = cost = 0.0
    steps = 0
    for i, row in enumerate(alignment):
        follow_winner = in_coalition.get(i, False)
        side = winner if follow_winner else loser
        traj = winner_traj if follow_winner else loser_traj
        rate = winner_rate if follow_winner else loser_rate
        index = row[f"{side}_index"]
        if index is None:
            continue
        step = traj.steps[index]
        steps += 1
        tokens += step.tokens
        latency += step.latency_s
        cost += rate * step.tokens
    return {"steps": steps, "tokens": tokens, "latency_s": latency, "cost_usd": cost}


def shapley_attribution(
    report: dict, a: Trajectory, b: Trajectory, metric: str = "tokens"
) -> Optional[dict]:
    """Allocate the cost gap across divergence decisions by Shapley value.

    ``metric`` is one of ``tokens``, ``latency_s``, ``cost_usd`` or ``steps``.
    Returns None when there is nothing to allocate (no divergences, or the
    two runs are equivalent).
    """
    regions = _regions(report)
    if not regions:
        return None

    # The heavier side is the one whose decisions we are pricing; when one
    # agent failed, that side is the failing one regardless of spend.
    failed = (report.get("attribution") or {}).get("failed_agent")
    if failed in ("a", "b"):
        loser, winner = failed, ("b" if failed == "a" else "a")
    else:
        a_tokens = sum(s.tokens for s in a.steps)
        b_tokens = sum(s.tokens for s in b.steps)
        loser, winner = ("b", "a") if b_tokens >= a_tokens else ("a", "b")

    k = len(regions)
    if k > EXACT_LIMIT:
        return {
            "available": False,
            "reason": f"{k} divergence regions exceeds the exact limit "
                      f"({EXACT_LIMIT}); allocation skipped rather than sampled",
            "regions": k,
        }

    players = list(range(k))
    cache: dict[frozenset[int], float] = {}

    def value(coalition: frozenset[int]) -> float:
        if coalition not in cache:
            cache[coalition] = _coalition_cost(
                report, regions, coalition, loser, winner, a, b
            )[metric]
        return cache[coalition]

    empty = value(frozenset())
    full = value(frozenset(players))
    total_saving = empty - full

    contributions: list[float] = []
    for player in players:
        others = [p for p in players if p != player]
        phi = 0.0
        for size in range(len(others) + 1):
            weight = factorial(size) * factorial(k - size - 1) / factorial(k)
            for subset in combinations(others, size):
                base = frozenset(subset)
                # Marginal saving from also fixing this decision.
                phi += weight * (value(base) - value(base | {player}))
        contributions.append(phi)

    divergences = report.get("divergences", [])
    rows: list[dict] = []
    for index, rows_in_region in enumerate(regions):
        divergence = divergences[index] if index < len(divergences) else {}
        share = (
            contributions[index] / total_saving if abs(total_saving) > 1e-9 else 0.0
        )
        rows.append({
            "region": index,
            "rank": divergence.get("rank", index + 1),
            "kind": divergence.get("kind", "unknown"),
            "summary": divergence.get("summary", ""),
            "alignment_rows": rows_in_region,
            "shapley": round(contributions[index], 4),
            "share": round(share, 4),
            "caused_failure": bool(
                (divergence.get("downstream") or {}).get("caused_failure")
            ),
        })
    rows.sort(key=lambda r: (-r["shapley"], r["region"]))

    causal = [d for d in divergences
              if (d.get("downstream") or {}).get("caused_failure")]
    outcome_attributable = len(causal) == 1 and failed in ("a", "b")

    top = rows[0] if rows else None
    if top and abs(total_saving) > 1e-9:
        narrative = (
            f"Fixing every divergence would have saved {total_saving:,.0f} "
            f"{metric.replace('_', ' ')}. The largest single share is "
            f"{top['share']:.0%} ({top['shapley']:,.0f}), from the "
            f"{top['kind']} divergence at region {top['region']}."
        )
        if len(rows) > 1:
            narrative += (
                f" The remaining {len(rows) - 1} decision(s) split the rest, so "
                f"fixing only the largest recovers part of the gap, not all of it."
            )
    else:
        narrative = (
            f"The divergences cost nothing measurable in {metric.replace('_', ' ')}; "
            f"the runs differ in path but not in spend."
        )

    return {
        "available": True,
        "metric": metric,
        "loser": report[loser]["agent"]["name"],
        "winner": report[winner]["agent"]["name"],
        "regions": k,
        "method": "exact",
        "total_saving": round(total_saving, 4),
        "allocations": rows,
        "efficiency_check": round(sum(contributions) - total_saving, 6),
        "outcome_attributable": outcome_attributable,
        "outcome_note": (
            "exactly one divergence was causal, so the outcome is attributable "
            "to it" if outcome_attributable else
            "outcome credit is not allocated: attributing a binary outcome "
            "across several decisions would need counterfactual re-runs this "
            "engine cannot perform on logged traces"
        ),
        "narrative": narrative,
    }
