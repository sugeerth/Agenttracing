"""Where does the variation in outcomes actually come from? (v24)

A leaderboard says agent A beat agent B.  It does not say whether the gap
came from the *model* underneath, the *harness* around it — the scaffold,
prompt, tool surface — the *task* being hard, or the run simply coming out
differently this time.  Those four call for completely different responses:
swap the model, fix the scaffold, accept the task is hard, or run it again.

This module decomposes the variance of an outcome across those factors, and
is unusually careful about the two ways that goes wrong.

**Confounding.**  If every agent uses exactly one model, "model" and
"harness" are not separable in general — any variance can be told as a story
about either.  The decomposition here is *sequential*: it attributes to
whichever factor is fitted first.  So it runs **every ordering** and reports
each factor's share as a range.  The width of that range is the variance the
factors share and which no method can split without a crossed design:

    model     12% – 31%   (19 points shared with harness)
    harness   28% – 47%

Reporting only one order would turn an arbitrary choice into a finding.
When a factor's range collapses to a point, the design does identify it, and
that is worth knowing too.

**Residual is not noise unless you have repeats.**  With one run per
(agent, task) cell, what is left after the factors is agent-by-task
interaction *plus* run-to-run stochasticity, inseparably.  Calling that
"noise" would invite someone to dismiss a real interaction as luck, so it is
named `residual` and labelled with which of the two it can contain.

Everything is stdlib and deterministic: sums of squares over group means, no
sampling, no optimiser, no wall clock.
"""

from __future__ import annotations

from itertools import permutations
from typing import Callable, Iterable, Optional, Sequence

from .trace import Trajectory

#: the factors that can be read off a trajectory, in the order a reader
#: usually cares about them.
FACTORS = ("task", "model", "harness", "version")

#: metrics worth decomposing.  ``success`` is binary; see the caveat emitted
#: with it — variance components on a 0/1 outcome live on the probability
#: scale and are heteroscedastic by construction.
METRICS: dict[str, Callable[[Trajectory], float]] = {
    "success": lambda t: 1.0 if t.outcome.success else 0.0,
    "tokens": lambda t: float(t.totals.input_tokens + t.totals.output_tokens),
    "cost_usd": lambda t: float(t.totals.cost_usd),
    "latency_s": lambda t: float(t.totals.latency_s),
    "steps": lambda t: float(len(t.steps)),
}


def _levels(trajectory: Trajectory) -> dict:
    """The factor levels of one run.

    ``harness`` is the agent name: the scaffold identity as the corpus
    records it.  It is not the model, and where a corpus never varies one
    against the other the design report says so rather than letting the
    label imply a separation the data does not contain.
    """
    return {
        "task": trajectory.task.id,
        "model": trajectory.agent.model or "(unrecorded)",
        "harness": trajectory.agent.name,
        "version": trajectory.agent.version or "(unrecorded)",
    }


def design(runs: Sequence[Trajectory]) -> dict:
    """What the corpus can and cannot identify, before any number is quoted.

    Four questions decide it: does a factor vary at all, is each harness
    tied to a single model, does any model carry more than one harness, and
    is there more than one run per cell?
    """
    rows = [_levels(t) for t in runs]
    counts = {f: sorted({row[f] for row in rows}) for f in FACTORS}

    models_per_harness: dict[str, set] = {}
    harnesses_per_model: dict[str, set] = {}
    for row in rows:
        models_per_harness.setdefault(row["harness"], set()).add(row["model"])
        harnesses_per_model.setdefault(row["model"], set()).add(row["harness"])

    crossed = sum(1 for models in models_per_harness.values() if len(models) > 1)
    shared = {m: sorted(h) for m, h in harnesses_per_model.items() if len(h) > 1}
    cells: dict[tuple, int] = {}
    for row in rows:
        key = (row["harness"], row["task"])
        cells[key] = cells.get(key, 0) + 1
    repeats = [n for n in cells.values()]

    if crossed:
        shape = "crossed"
        note = (f"{crossed} harness(es) run on more than one model, so model "
                "and harness vary independently and are separable.")
    elif shared:
        shape = "nested"
        note = (f"every harness uses one model, but {len(shared)} model(s) "
                "carry several harnesses — variation among harnesses sharing "
                "a model is harness variance, so the two are partly separable.")
    else:
        shape = "confounded"
        note = ("each model is used by exactly one harness, so model and "
                "harness are the same partition of the data and no method "
                "can tell them apart. Run one harness on a second model, or "
                "one model under a second harness, to break the tie.")

    return {
        "runs": len(runs),
        "levels": {f: len(v) for f, v in counts.items()},
        "level_names": {f: v[:12] for f, v in counts.items()},
        "shape": shape,
        "note": note,
        "harnesses_sharing_a_model": shared,
        "models_per_harness_max": max((len(v) for v in models_per_harness.values()), default=0),
        "repeats_per_cell": {
            "min": min(repeats) if repeats else 0,
            "max": max(repeats) if repeats else 0,
            "cells": len(cells),
        },
        "residual_is_noise": bool(repeats) and min(repeats) > 1,
        "identifiable": [f for f in FACTORS if len(counts[f]) > 1],
        "constant": [f for f in FACTORS if len(counts[f]) <= 1],
    }


def _sequential_shares(values: Sequence[float], rows: Sequence[dict],
                       order: Sequence[str]) -> dict:
    """Type-I (sequential) sums of squares for one attribution order.

    Each factor is credited with the variation still present when it is
    fitted, then that variation is removed before the next factor is
    considered.  On an unbalanced or non-orthogonal design the order
    therefore matters — which is the whole reason the caller sweeps it.
    """
    n = len(values)
    mean = sum(values) / n
    total = sum((v - mean) ** 2 for v in values)
    if total <= 0:
        return {"shares": {f: 0.0 for f in order}, "residual": 0.0, "total": 0.0}

    working = list(values)
    shares: dict[str, float] = {}
    for factor in order:
        groups: dict[str, list[int]] = {}
        for i, row in enumerate(rows):
            groups.setdefault(row[factor], []).append(i)
        grand = sum(working) / n
        explained = 0.0
        for members in groups.values():
            group_mean = sum(working[i] for i in members) / len(members)
            explained += len(members) * (group_mean - grand) ** 2
            for i in members:
                working[i] -= group_mean
            # re-centre so the next factor sees deviations, not a shifted mean
        shares[factor] = explained
    residual = sum(v ** 2 for v in working)
    return {"shares": shares, "residual": residual, "total": total}


def _omega_squared(ss_effect: float, df_effect: int, ss_residual: float,
                   df_residual: int, ss_total: float) -> Optional[float]:
    """Bias-corrected share of variance for one factor.

    Raw explained variance is not comparable across factors with different
    numbers of levels: a factor with k levels on n observations explains
    ``(k-1)/(n-1)`` of the total *by chance alone*, so on this corpus the
    33-level harness starts 12 points ahead of the 8-level model before any
    real effect exists.  Omega squared subtracts that expectation:

        omega^2 = (SS_effect - df_effect * MS_error) / (SS_total + MS_error)

    A negative result means the factor explained no more than its level
    count would predict; it is returned as-is rather than clamped, because
    "at chance" is the finding and zero would hide it.
    """
    if df_residual <= 0 or ss_total <= 0:
        # No degrees of freedom left to estimate error with: the model is
        # saturated and the correction is undefined, not zero.
        return None
    ms_error = ss_residual / df_residual
    if ms_error <= 0:
        # Nothing is left unexplained.  The factor accounts for the outcome
        # exactly, which is the strongest possible effect — returning None
        # here would report a perfect predictor as unmeasurable.
        return 1.0 if ss_effect > 0 else 0.0
    return (ss_effect - df_effect * ms_error) / (ss_total + ms_error)


def decompose(runs: Sequence[Trajectory], metric: str = "success",
              factors: Optional[Sequence[str]] = None) -> dict:
    """Variance of ``metric`` attributed across factors, over every order.

    Returns each factor's minimum and maximum share across all attribution
    orders.  The minimum is what that factor explains when every other
    factor has already taken what it can — variance it owns outright.  The
    maximum is what it explains when it goes first.  The gap between them is
    shared with the other factors and is not attributable by any ordering.
    """
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; choose from {', '.join(METRICS)}")
    plan = design(runs)
    chosen = list(factors) if factors else plan["identifiable"]
    chosen = [f for f in chosen if f in FACTORS and plan["levels"].get(f, 0) > 1]

    values = [METRICS[metric](t) for t in runs]
    rows = [_levels(t) for t in runs]
    n = len(values)
    if n < 2 or not chosen:
        return {
            "metric": metric, "runs": n, "factors": chosen, "design": plan,
            "components": {}, "residual": None, "total_variance": None,
            "orders_tried": 0,
            "reason": ("fewer than two runs" if n < 2 else
                       "no factor varies in this corpus, so there is nothing "
                       "to attribute variation to"),
            "narrative": "Nothing to decompose.",
        }

    mean = sum(values) / n
    total = sum((v - mean) ** 2 for v in values)
    if total <= 0:
        return {
            "metric": metric, "runs": n, "factors": chosen, "design": plan,
            "components": {}, "residual": 0.0, "total_variance": 0.0,
            "orders_tried": 0,
            "reason": f"every run has the same {metric}; there is no variance to split",
            "narrative": f"Every run has the same {metric} — nothing varies.",
        }

    # All orders: k! is 24 at four factors, which is cheap and exhaustive.
    per_factor: dict[str, list[float]] = {f: [] for f in chosen}
    per_omega: dict[str, list[float]] = {f: [] for f in chosen}
    residuals: list[float] = []
    orders = list(permutations(chosen))
    dfs = {f: plan["levels"][f] - 1 for f in chosen}
    df_residual = n - 1 - sum(dfs.values())
    for order in orders:
        result = _sequential_shares(values, rows, order)
        for factor in chosen:
            per_factor[factor].append(result["shares"][factor] / total)
            omega = _omega_squared(result["shares"][factor], dfs[factor],
                                   result["residual"], df_residual, total)
            if omega is not None:
                per_omega[factor].append(omega)
        residuals.append(result["residual"] / total)

    components = {}
    for factor in chosen:
        low, high = min(per_factor[factor]), max(per_factor[factor])
        chance = dfs[factor] / (n - 1) if n > 1 else 0.0
        omegas = per_omega[factor]
        components[factor] = {
            "min_share": round(low, 4),
            "max_share": round(high, 4),
            "shared": round(high - low, 4),
            "identified": (high - low) < 0.005,
            "levels": plan["levels"][factor],
            # What this factor would explain with no real effect at all,
            # purely from having this many levels.
            "expected_by_chance": round(chance, 4),
            "omega_squared_min": round(min(omegas), 4) if omegas else None,
            "omega_squared_max": round(max(omegas), 4) if omegas else None,
            "above_chance": bool(omegas) and min(omegas) > 0,
        }

    residual = round(sum(residuals) / len(residuals), 4)
    return {
        "metric": metric,
        "runs": n,
        "factors": chosen,
        "design": plan,
        "components": components,
        "residual": residual,
        "residual_meaning": (
            "run-to-run variation with the factors held fixed"
            if plan["residual_is_noise"] else
            "interaction between the factors AND run-to-run variation, "
            "inseparably — there is only one run per cell"),
        "total_variance": round(total / n, 6),
        "orders_tried": len(orders),
        "method": ("sequential (Type I) sums of squares over group means, "
                   "swept across every attribution order; shares are of total "
                   "sum of squares, reported beside bias-corrected omega "
                   "squared because a factor with more levels explains more "
                   "by chance"),
        "df_residual": df_residual,
        "caveat": _caveat(metric, plan),
        "narrative": _narrative(metric, components, residual, plan),
    }


def _caveat(metric: str, plan: dict) -> str:
    parts = []
    if metric == "success":
        parts.append(
            "success is binary, so these components are on the probability "
            "scale and the variance of each run depends on its own success "
            "rate; read them as shares of observed variation, not as a "
            "variance-components model of a latent trait")
    if plan["shape"] == "confounded":
        parts.append(
            "model and harness partition the data identically here, so any "
            "split between them is an artefact of ordering, not a finding")
    if not plan["residual_is_noise"]:
        parts.append(
            "one run per cell: the residual carries interaction as well as "
            "noise, so it is not a measure of flakiness")
    return "; ".join(parts) if parts else ""


def _narrative(metric: str, components: dict, residual: float, plan: dict) -> str:
    if not components:
        return "Nothing to decompose."
    ordered = sorted(components.items(), key=lambda kv: -kv[1]["max_share"])
    lead = ordered[0]
    bits = []
    for name, comp in ordered:
        raw = (f"{comp['min_share']:.0%}" if comp["identified"]
               else f"{comp['min_share']:.0%}–{comp['max_share']:.0%}")
        omega = comp.get("omega_squared_min")
        if omega is None:
            bits.append(f"{name} {raw}")
        elif omega <= 0:
            bits.append(f"{name} {raw} raw but at chance once corrected")
        else:
            bits.append(f"{name} {raw} raw / {omega:.0%} corrected")
    sentence = (f"Variation in {metric} across {plan['runs']} run(s): "
                + ", ".join(bits) + f", residual {residual:.0%}.")

    ambiguous = [(n, c) for n, c in ordered if not c["identified"]]
    if ambiguous:
        worst = max(ambiguous, key=lambda kv: kv[1]["shared"])
        sentence += (f" {worst[0].capitalize()}'s range spans "
                     f"{worst[1]['shared']:.0%} of total variance that it shares "
                     "with the other factors; no ordering can assign it.")
    else:
        sentence += " Every factor's share is identified by this design."

    at_chance = [n for n, c in ordered
                 if c.get("omega_squared_min") is not None and c["omega_squared_min"] <= 0]
    if at_chance:
        sentence += (" " + ", ".join(at_chance) +
                     (" explains" if len(at_chance) == 1 else " explain") +
                     " no more than its level count predicts by chance, so the "
                     "raw share above is an artefact of how many levels it has.")
    if lead[0] == "task":
        sentence += (" Task difficulty dominates, which means most of what "
                     "this suite measures is which questions were asked, not "
                     "which agent answered them.")
    if not plan["residual_is_noise"] and residual > 0.25:
        sentence += (" The residual is large, but with one run per cell it "
                     "cannot be read as flakiness — it is interaction and "
                     "noise together.")
    return sentence


def variance_report(runs: Sequence[Trajectory],
                    metrics: Iterable[str] = ("success", "tokens", "latency_s")) -> dict:
    """Decompose several metrics over one corpus, sharing the design report."""
    runs = list(runs)
    plan = design(runs)
    return {
        "design": plan,
        "metrics": {m: decompose(runs, m) for m in metrics if m in METRICS},
        "narrative": _report_narrative(plan, runs, metrics),
    }


def _report_narrative(plan: dict, runs, metrics) -> str:
    head = (f"{plan['runs']} run(s): {plan['levels'].get('task', 0)} task(s), "
            f"{plan['levels'].get('model', 0)} model(s), "
            f"{plan['levels'].get('harness', 0)} harness(es). "
            f"Design is {plan['shape']} — {plan['note']}")
    if plan["shape"] == "confounded":
        head += (" Until that is fixed, treat every model-versus-harness "
                 "split below as unattributable.")
    return head
