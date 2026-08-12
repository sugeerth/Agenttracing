"""Behavioral similarity between agents (SCHEMA.md v10).

Pairwise comparison answers "how do these two differ on this task".  This
module answers the fleet-scale question: *which agents behave alike, and
what should that change?*

Similarity is deliberately not one number.  Two agents can agree on every
outcome while taking completely different paths, or diverge on outcomes while
spending identical resources — and those two situations imply opposite
decisions.  So four facets are measured separately:

===============  ==================================================
facet            question it answers
===============  ==================================================
``outcome``      do they succeed and fail on the same tasks?
``process``      do their trajectories have the same shape?
``tools``        do they reach for the same tools, in the same mix?
``resources``    do they cost the same to run?
===============  ==================================================

The facets combine into a ``composite`` score, but the facets are the point:
high outcome + low process similarity means "same answers, different means";
high everything + a cost gap means one agent is redundant; low outcome
similarity means the agents are *complementary* and a router can beat either
one alone (see :mod:`deepcompare.routing`).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from .trace import Trajectory

#: default weights for the composite similarity score.
DEFAULT_FACET_WEIGHTS: dict[str, float] = {
    "outcome": 0.40,
    "process": 0.25,
    "tools": 0.20,
    "resources": 0.15,
}

#: step types that represent a call out to a tool or corpus.
_TOOL_TYPES = frozenset({"tool_call", "search", "retrieve", "read"})

#: composite similarity at or above which two agents are "behaviorally alike".
REDUNDANCY_SIMILARITY = 0.85
#: relative cost gap above which the cheaper of two alike agents wins outright.
REDUNDANCY_COST_GAP = 0.15


class Profile:
    """A compact behavioral summary of one agent across a task set."""

    def __init__(self, name: str, trajectories: Iterable[Trajectory]) -> None:
        self.name = name
        self.model = ""
        self.outcomes: dict[str, bool] = {}
        self.type_seq: dict[str, tuple[str, ...]] = {}
        self.tool_usage: dict[str, int] = {}
        self.per_task: dict[str, dict[str, float]] = {}

        for traj in sorted(trajectories, key=lambda t: t.task.id):
            task_id = traj.task.id
            self.model = self.model or traj.agent.model
            self.outcomes[task_id] = bool(traj.outcome.success)
            self.type_seq[task_id] = tuple(step.type for step in traj.steps)
            for step in traj.steps:
                if step.type in _TOOL_TYPES:
                    key = step.name or step.type
                    self.tool_usage[key] = self.tool_usage.get(key, 0) + 1
            self.per_task[task_id] = {
                "tokens": float(sum(s.tokens for s in traj.steps)),
                "latency_s": float(traj.totals.latency_s),
                "cost_usd": float(traj.totals.cost_usd),
                "steps": float(len(traj.steps)),
                "tool_calls": float(
                    sum(1 for s in traj.steps if s.type in _TOOL_TYPES)
                ),
            }

    @property
    def tasks(self) -> list[str]:
        return sorted(self.outcomes)

    @property
    def success_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for ok in self.outcomes.values() if ok) / len(self.outcomes)

    def mean(self, metric: str) -> float:
        """Mean of a per-task metric across this agent's tasks."""
        values = [row[metric] for row in self.per_task.values() if metric in row]
        return sum(values) / len(values) if values else 0.0

    def solved(self) -> set[str]:
        return {task for task, ok in self.outcomes.items() if ok}


def build_profiles(trajs_by_agent: dict[str, list[Trajectory]]) -> list[Profile]:
    """Build one :class:`Profile` per agent, ordered by agent name."""
    return [Profile(name, trajs_by_agent[name]) for name in sorted(trajs_by_agent)]


def lcs_ratio(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Similarity of two sequences as ``2*LCS / (len(a)+len(b))`` in [0, 1]."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for token_a in a:
        current = [0]
        for j, token_b in enumerate(b):
            if token_a == token_b:
                current.append(prev[j] + 1)
            else:
                current.append(max(prev[j + 1], current[j]))
        prev = current
    return round(2 * prev[-1] / (len(a) + len(b)), 4)


def cosine(counts_a: dict[str, int], counts_b: dict[str, int]) -> float:
    """Cosine similarity of two count vectors over their shared key space."""
    if not counts_a and not counts_b:
        return 1.0
    if not counts_a or not counts_b:
        return 0.0
    keys = set(counts_a) | set(counts_b)
    dot = sum(counts_a.get(k, 0) * counts_b.get(k, 0) for k in keys)
    norm_a = math.sqrt(sum(v * v for v in counts_a.values()))
    norm_b = math.sqrt(sum(v * v for v in counts_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return round(dot / (norm_a * norm_b), 4)


def _ratio_similarity(x: float, y: float) -> float:
    """Similarity of two positive magnitudes: ``min/max``, 1.0 when both zero.

    Uses the ratio rather than an absolute difference so that agents are
    compared on relative spend — "twice as expensive" means the same thing
    whether the numbers are cents or dollars.
    """
    if x <= 0 and y <= 0:
        return 1.0
    if x <= 0 or y <= 0:
        return 0.0
    return round(min(x, y) / max(x, y), 4)


def facet_similarity(p: Profile, q: Profile) -> dict:
    """The four similarity facets (plus shared-task count) for two agents."""
    shared = sorted(set(p.outcomes) & set(q.outcomes))
    if not shared:
        return {"outcome": 0.0, "process": 0.0, "tools": 0.0,
                "resources": 0.0, "shared_tasks": 0}

    agree = sum(1 for t in shared if p.outcomes[t] == q.outcomes[t])
    outcome = round(agree / len(shared), 4)

    process = round(
        sum(lcs_ratio(p.type_seq[t], q.type_seq[t]) for t in shared) / len(shared), 4
    )

    tools = cosine(p.tool_usage, q.tool_usage)

    resource_facets = [
        _ratio_similarity(p.mean(metric), q.mean(metric))
        for metric in ("tokens", "latency_s", "steps")
    ]
    resources = round(sum(resource_facets) / len(resource_facets), 4)

    return {"outcome": outcome, "process": process, "tools": tools,
            "resources": resources, "shared_tasks": len(shared)}


def composite_score(facets: dict, weights: Optional[dict[str, float]] = None) -> float:
    """Weighted mean of the four facets."""
    weights = {**DEFAULT_FACET_WEIGHTS, **(weights or {})}
    total = sum(weights[k] for k in DEFAULT_FACET_WEIGHTS)
    if total <= 0:
        return 0.0
    score = sum(facets.get(k, 0.0) * weights[k] for k in DEFAULT_FACET_WEIGHTS)
    return round(score / total, 4)


def similarity_matrix(
    profiles: list[Profile], weights: Optional[dict[str, float]] = None
) -> list[dict]:
    """All unordered agent pairs with their facets and composite score.

    Ordered by composite descending, then by the agent names, so the most
    behaviorally alike pairs come first and output is deterministic.
    """
    pairs: list[dict] = []
    for i, p in enumerate(profiles):
        for q in profiles[i + 1:]:
            facets = facet_similarity(p, q)
            pairs.append({
                "a": p.name,
                "b": q.name,
                "facets": facets,
                "composite": composite_score(facets, weights),
            })
    pairs.sort(key=lambda row: (-row["composite"], row["a"], row["b"]))
    return pairs


def cluster_agents(
    profiles: list[Profile], pairs: list[dict], threshold: float = REDUNDANCY_SIMILARITY
) -> list[dict]:
    """Average-linkage agglomerative clustering on composite similarity.

    Merges the closest pair of clusters until no pair's average similarity
    reaches ``threshold``.  Deterministic: ties break on sorted member names.
    """
    lookup = {(row["a"], row["b"]): row["composite"] for row in pairs}

    def similarity(x: str, y: str) -> float:
        return lookup.get((x, y), lookup.get((y, x), 0.0))

    clusters: list[list[str]] = [[p.name] for p in profiles]
    while len(clusters) > 1:
        best: Optional[tuple[float, int, int]] = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                scores = [similarity(x, y) for x in clusters[i] for y in clusters[j]]
                average = sum(scores) / len(scores) if scores else 0.0
                if average >= threshold and (best is None or average > best[0]):
                    best = (average, i, j)
        if best is None:
            break
        _, i, j = best
        clusters[i] = sorted(clusters[i] + clusters[j])
        clusters.pop(j)

    by_name = {p.name: p for p in profiles}
    out: list[dict] = []
    for members in sorted(clusters, key=lambda m: (-len(m), m[0])):
        costs = {name: by_name[name].mean("cost_usd") for name in members}
        cheapest = min(members, key=lambda n: (costs[n], n))
        best_success = max(members, key=lambda n: (by_name[n].success_rate, -costs[n], n))
        out.append({
            "members": members,
            "size": len(members),
            "success_rate": round(
                sum(by_name[n].success_rate for n in members) / len(members), 4
            ),
            "cheapest": cheapest,
            "representative": best_success,
        })
    return out


def find_redundancies(profiles: list[Profile], pairs: list[dict]) -> list[dict]:
    """Pairs that behave alike and agree on every shared task, but differ in
    cost — the cheaper one makes the dearer one redundant.

    This is the money finding: paying more for behavior you already have.
    """
    by_name = {p.name: p for p in profiles}
    out: list[dict] = []
    for row in pairs:
        if row["composite"] < REDUNDANCY_SIMILARITY:
            continue
        if row["facets"]["outcome"] < 1.0:
            continue
        p, q = by_name[row["a"]], by_name[row["b"]]
        cost_p, cost_q = p.mean("cost_usd"), q.mean("cost_usd")
        dearer, cheaper = (p, q) if cost_p > cost_q else (q, p)
        cost_hi, cost_lo = max(cost_p, cost_q), min(cost_p, cost_q)
        if cost_hi <= 0:
            continue
        gap = (cost_hi - cost_lo) / cost_hi
        if gap < REDUNDANCY_COST_GAP:
            continue
        out.append({
            "keep": cheaper.name,
            "drop": dearer.name,
            "similarity": row["composite"],
            "facets": row["facets"],
            "cost_gap": round(gap, 4),
            "saving_per_task_usd": round(cost_hi - cost_lo, 6),
            "summary": (
                f"{dearer.name} and {cheaper.name} agree on all "
                f"{row['facets']['shared_tasks']} task(s) and behave alike "
                f"({int(row['composite'] * 100)}% similar), but {dearer.name} "
                f"costs {gap * 100:.0f}% more per task — "
                f"{cheaper.name} makes it redundant."
            ),
        })

    # One row per droppable agent — its single best replacement — rather than
    # every pair that happens to dominate it.  "otter-v1 is redundant" is the
    # decision; listing it five times against five keepers is noise.
    best_per_drop: dict[str, dict] = {}
    for row in out:
        current = best_per_drop.get(row["drop"])
        better = current is None or (
            row["saving_per_task_usd"],
            row["similarity"],
        ) > (current["saving_per_task_usd"], current["similarity"])
        if better:
            best_per_drop[row["drop"]] = row
    deduped = sorted(
        best_per_drop.values(),
        key=lambda r: (-r["saving_per_task_usd"], r["drop"]),
    )
    for row in deduped:
        row["also_dominated_by"] = sorted(
            other["keep"] for other in out
            if other["drop"] == row["drop"] and other["keep"] != row["keep"]
        )
    return deduped


def find_complementarities(profiles: list[Profile], pairs: list[dict]) -> list[dict]:
    """Pairs whose union solves more than either alone — routing opportunities.

    Low outcome similarity is usually read as "one agent is worse".  It can
    equally mean the two fail in *different places*, in which case picking
    between them per task beats committing to either.
    """
    by_name = {p.name: p for p in profiles}
    out: list[dict] = []
    for row in pairs:
        p, q = by_name[row["a"]], by_name[row["b"]]
        shared = set(p.outcomes) & set(q.outcomes)
        if not shared:
            continue
        solved_p = p.solved() & shared
        solved_q = q.solved() & shared
        union = solved_p | solved_q
        best_alone = max(len(solved_p), len(solved_q))
        gain = len(union) - best_alone
        if gain <= 0:
            continue
        only_p = sorted(solved_p - solved_q)
        only_q = sorted(solved_q - solved_p)
        out.append({
            "a": p.name,
            "b": q.name,
            "union_coverage": round(len(union) / len(shared), 4),
            "best_alone_coverage": round(best_alone / len(shared), 4),
            "gain_tasks": gain,
            "only_a": only_p,
            "only_b": only_q,
            "outcome_similarity": row["facets"]["outcome"],
            "summary": (
                f"{p.name} and {q.name} fail in different places: together they "
                f"solve {len(union)}/{len(shared)} task(s) versus {best_alone} for "
                f"the better one alone (+{gain}). "
                f"{p.name} uniquely solves {', '.join(only_p) or 'none'}; "
                f"{q.name} uniquely solves {', '.join(only_q) or 'none'}."
            ),
        })
    out.sort(key=lambda row: (-row["gain_tasks"], -row["union_coverage"],
                              row["a"], row["b"]))
    return out


def _narrative(
    profiles: list[Profile], pairs: list[dict], clusters: list[dict],
    redundancies: list[dict], complementarities: list[dict],
) -> str:
    parts: list[str] = []
    grouped = [c for c in clusters if c["size"] > 1]
    if grouped:
        biggest = grouped[0]
        parts.append(
            f"{len(profiles)} agents fall into {len(clusters)} behavioral "
            f"group(s); the largest holds {biggest['size']} agents that are "
            f"interchangeable in how they work "
            f"({', '.join(biggest['members'][:4])}"
            f"{'…' if biggest['size'] > 4 else ''})."
        )
    else:
        parts.append(
            f"All {len(profiles)} agents are behaviorally distinct — no two "
            f"cluster together at the {REDUNDANCY_SIMILARITY:.0%} similarity mark."
        )
    if redundancies:
        top = redundancies[0]
        total = sum(row["saving_per_task_usd"] for row in redundancies)
        parts.append(
            f"{len(redundancies)} agent(s) are redundant — each matched on every "
            f"outcome by a cheaper lookalike; retiring them saves ${total:.4f} per "
            f"task run. The worst offender is {top['drop']}, which buys nothing "
            f"over {top['keep']} yet costs "
            f"${top['saving_per_task_usd']:.4f} more per task."
        )
    if complementarities:
        top = complementarities[0]
        parts.append(
            f"The most complementary pair is {top['a']} + {top['b']}, covering "
            f"{top['union_coverage']:.0%} together versus "
            f"{top['best_alone_coverage']:.0%} for the better one alone."
        )
    else:
        parts.append("No pair covers more together than the best agent alone.")
    return " ".join(parts)


def similarity_analysis(
    trajs_by_agent: dict[str, list[Trajectory]],
    weights: Optional[dict[str, float]] = None,
) -> dict:
    """Full behavioral-similarity analysis over a fleet (SCHEMA.md v10)."""
    profiles = build_profiles(trajs_by_agent)
    pairs = similarity_matrix(profiles, weights)
    clusters = cluster_agents(profiles, pairs)
    redundancies = find_redundancies(profiles, pairs)
    complementarities = find_complementarities(profiles, pairs)
    return {
        "agents": [
            {
                "name": p.name,
                "model": p.model,
                "success_rate": round(p.success_rate, 4),
                "mean_cost_usd": round(p.mean("cost_usd"), 6),
                "mean_tokens": round(p.mean("tokens"), 1),
                "mean_latency_s": round(p.mean("latency_s"), 3),
                "mean_steps": round(p.mean("steps"), 2),
                "tool_usage": dict(sorted(p.tool_usage.items())),
            }
            for p in profiles
        ],
        "facet_weights": {**DEFAULT_FACET_WEIGHTS, **(weights or {})},
        "pairs": pairs,
        "clusters": clusters,
        "redundancies": redundancies,
        "complementarities": complementarities,
        "narrative": _narrative(profiles, pairs, clusters, redundancies,
                                complementarities),
    }
