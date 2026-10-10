"""Agent selection and routing (SCHEMA.md v10).

Ranking tells you which agent is best *on average*.  That is the wrong
question when agents fail in different places: the right question is which
agent to send *this* task to, and how small a set of agents you actually need
to keep on the payroll.

Three answers, in increasing order of what they demand of you:

``best_single``
    Commit to one agent.  No routing machinery, no per-task decision.

``portfolios``
    The smallest set of agents whose union covers the task set.  Says "you
    need these two, the other 31 add nothing" — a procurement decision, not
    a per-request one.

``oracle``
    Per task, the cheapest agent that solves it.  This is a *ceiling*, not a
    policy: it assumes you already know who will succeed.  Reported so the
    headroom above ``best_single`` is visible — that gap is exactly what a
    router could win, and nothing more.

The honest framing matters.  Oracle numbers quoted as achievable are how
routing papers oversell; here the gap is labelled as headroom and the
per-task table shows what a router would have to predict.
"""

from __future__ import annotations

from itertools import combinations
from typing import Optional

from .similarity import Profile, build_profiles
from .trace import Trajectory

#: fleet size above which portfolio search switches from exact to greedy.
EXACT_PORTFOLIO_LIMIT = 14
#: largest portfolio size considered.
MAX_PORTFOLIO_K = 3


def _cost(profile: Profile, task: str) -> float:
    return profile.per_task.get(task, {}).get("cost_usd", 0.0)


def _coverage(profiles: list[Profile], members: list[str], tasks: list[str]) -> set[str]:
    by_name = {p.name: p for p in profiles}
    covered: set[str] = set()
    for name in members:
        covered |= by_name[name].solved() & set(tasks)
    return covered


def _portfolio_cost(
    profiles: list[Profile], members: list[str], tasks: list[str]
) -> float:
    """Cost of running a portfolio with oracle dispatch inside it: for each
    task, the cheapest member that solves it (or the cheapest member overall
    when none do — the task still costs something to fail)."""
    by_name = {p.name: p for p in profiles}
    total = 0.0
    for task in tasks:
        solvers = [n for n in members if task in by_name[n].solved()]
        pool = solvers or members
        total += min(_cost(by_name[n], task) for n in pool)
    return total


def _best_portfolio(
    profiles: list[Profile], tasks: list[str], k: int
) -> Optional[dict]:
    """Best k-agent portfolio: maximum coverage, cheapest as tie-break.

    Exact search for small fleets; greedy set-cover for large ones (marked
    in the result so the reader knows the search was not exhaustive).
    """
    names = [p.name for p in profiles]
    if k > len(names):
        return None

    if len(names) <= EXACT_PORTFOLIO_LIMIT:
        best: Optional[tuple] = None
        for members in combinations(names, k):
            covered = _coverage(profiles, list(members), tasks)
            cost = _portfolio_cost(profiles, list(members), tasks)
            key = (-len(covered), cost, members)
            if best is None or key < best[0]:
                best = (key, list(members), covered, cost)
        assert best is not None
        _, members, covered, cost = best
        method = "exact"
    else:
        by_name = {p.name: p for p in profiles}
        members: list[str] = []
        covered = set()
        while len(members) < k:
            candidates = [n for n in names if n not in members]
            if not candidates:
                break
            def gain_key(name: str) -> tuple:
                new = (by_name[name].solved() & set(tasks)) - covered
                return (-len(new), by_name[name].mean("cost_usd"), name)
            pick = min(candidates, key=gain_key)
            members.append(pick)
            covered |= by_name[pick].solved() & set(tasks)
        members = sorted(members)
        cost = _portfolio_cost(profiles, members, tasks)
        method = "greedy"

    return {
        "k": k,
        "members": sorted(members),
        "coverage": round(len(covered) / len(tasks), 4) if tasks else 0.0,
        "covered_tasks": sorted(covered),
        "cost_usd": round(cost, 6),
        "search": method,
    }


def routing_analysis(trajs_by_agent: dict[str, list[Trajectory]]) -> dict:
    """Agent-selection analysis over a fleet (SCHEMA.md v10)."""
    profiles = build_profiles(trajs_by_agent)
    if not profiles:
        return {"tasks": [], "agents": 0, "per_task": [], "best_single": None,
                "oracle": None, "portfolios": [], "unique_solves": {},
                "narrative": "No agents to route between."}

    tasks = sorted(set().union(*(set(p.outcomes) for p in profiles)))
    by_name = {p.name: p for p in profiles}

    # --- best single agent: coverage first, then cost, then name.
    def single_key(p: Profile) -> tuple:
        return (-len(p.solved() & set(tasks)), p.mean("cost_usd"), p.name)

    champion = min(profiles, key=single_key)
    champion_cost = sum(_cost(champion, t) for t in tasks)
    champion_covered = sorted(champion.solved() & set(tasks))

    # --- per-task table: who solves it, and who solves it cheapest.
    per_task: list[dict] = []
    oracle_cost = 0.0
    oracle_covered: list[str] = []
    for task in tasks:
        solvers = sorted(
            (p for p in profiles if task in p.solved()),
            key=lambda p: (_cost(p, task), p.name),
        )
        entry: dict = {
            "task": task,
            "solvers": [p.name for p in solvers],
            "solver_count": len(solvers),
            "champion_solves": task in champion.solved(),
        }
        if solvers:
            pick = solvers[0]
            entry["cheapest_solver"] = pick.name
            entry["cheapest_cost_usd"] = round(_cost(pick, task), 6)
            entry["champion_cost_usd"] = round(_cost(champion, task), 6)
            oracle_cost += _cost(pick, task)
            oracle_covered.append(task)
        else:
            entry["cheapest_solver"] = None
            entry["cheapest_cost_usd"] = None
            entry["champion_cost_usd"] = round(_cost(champion, task), 6)
            oracle_cost += min(_cost(p, task) for p in profiles)
        per_task.append(entry)

    # --- portfolios of increasing size.
    portfolios = [
        portfolio
        for k in range(1, min(MAX_PORTFOLIO_K, len(profiles)) + 1)
        if (portfolio := _best_portfolio(profiles, tasks, k)) is not None
    ]

    # --- what each agent uniquely contributes.
    unique_solves: dict[str, list[str]] = {}
    for p in profiles:
        others = set().union(
            *(q.solved() for q in profiles if q.name != p.name)
        ) if len(profiles) > 1 else set()
        only = sorted((p.solved() & set(tasks)) - others)
        if only:
            unique_solves[p.name] = only

    oracle_coverage = len(oracle_covered) / len(tasks) if tasks else 0.0
    champion_coverage = len(champion_covered) / len(tasks) if tasks else 0.0
    headroom = round(oracle_coverage - champion_coverage, 4)
    saving = round(champion_cost - oracle_cost, 6)

    smallest_full = next(
        (p for p in portfolios if p["coverage"] >= oracle_coverage - 1e-9), None
    )

    narrative_parts = [
        f"Best single agent is {champion.name} at {champion_coverage:.0%} coverage "
        f"(${champion_cost:.4f} for {len(tasks)} task(s))."
    ]
    if headroom > 0:
        narrative_parts.append(
            f"Routing headroom is {headroom:.0%}: some task(s) only other agents "
            f"solve, so a perfect router would reach {oracle_coverage:.0%}."
        )
    else:
        narrative_parts.append(
            "No coverage headroom — no other agent solves anything "
            f"{champion.name} misses, so routing can only save cost."
        )
    if saving > 0:
        narrative_parts.append(
            f"Dispatching each task to its cheapest successful agent would cost "
            f"${oracle_cost:.4f}, saving ${saving:.4f} ({saving / champion_cost:.0%}) "
            f"versus always using {champion.name}."
        )
    if smallest_full and smallest_full["k"] < len(profiles):
        narrative_parts.append(
            f"{smallest_full['k']} agent(s) — {', '.join(smallest_full['members'])} — "
            f"reach the full {smallest_full['coverage']:.0%} ceiling; the rest add no "
            f"coverage."
        )
    narrative_parts.append(
        "Oracle figures are a ceiling, not a policy: they assume the router "
        "already knows which agent will succeed."
    )

    return {
        "tasks": tasks,
        "agents": len(profiles),
        "per_task": per_task,
        "best_single": {
            "agent": champion.name,
            "coverage": round(champion_coverage, 4),
            "covered_tasks": champion_covered,
            "cost_usd": round(champion_cost, 6),
        },
        "oracle": {
            "coverage": round(oracle_coverage, 4),
            "cost_usd": round(oracle_cost, 6),
            "coverage_headroom": headroom,
            "cost_saving_usd": saving,
            "note": "ceiling assuming per-task knowledge of which agent succeeds",
        },
        "portfolios": portfolios,
        "unique_solves": dict(sorted(unique_solves.items())),
        "narrative": " ".join(narrative_parts),
    }
