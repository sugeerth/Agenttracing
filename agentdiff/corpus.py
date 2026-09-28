"""The whole analysis of a corpus, as one function: what `batch` writes and what a live page shows.

`batch` used to hold this pipeline inside its command, and the live
server (`watch`, `duel --live`) had its own shorter one — pair reports and
a bare aggregate, without the scorecard, the lessons, the forge or the
duel's own reading. So a page watched while the agents ran was a
different page, with less on it, from the one written when they finished.
This is the one pipeline both call, and a test holds the live page's
aggregate to the static page's once the runs are done.

Pure: it reads trajectories and returns the reports and the aggregate. It
writes nothing; the ledgers it would update come back under ``next`` for
the caller to write.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from .forge import forge as forge_evals
from .issues import build_issues
from .lessons import learn as learn_lessons
from .metrics import aggregate as build_aggregate
from .report import attach_milestones, compare
from .router import routing_table
from .scorecard import scorecard as build_scorecard

__all__ = ["analyse", "pairs"]


def pairs(trajectories: list) -> tuple:
    """``((name_a, name_b), {task: {agent: first trajectory}})``.

    Raises ValueError unless there are exactly two agents: a comparison of
    one agent is not a comparison, and of three is several.
    """
    names = sorted({t.agent.name for t in trajectories})
    if len(names) != 2:
        raise ValueError(f"a comparison needs traces from exactly 2 agents, found {len(names)}: "
                         f"{', '.join(names) or '(none)'}")
    by_task: dict = {}
    for t in trajectories:
        by_task.setdefault(t.task.id, {}).setdefault(t.agent.name, t)
    return (names[0], names[1]), by_task


def analyse(trajectories: Iterable, *, golden: Optional[dict] = None, policy: Optional[dict] = None,
            outcomes: Optional[dict] = None, suppressions: Optional[list] = None,
            lessons_ledger: Optional[dict] = None, evals_ledger: Optional[dict] = None,
            seeds: Optional[list] = None, proposer: Optional[Callable] = None,
            extra: Optional[dict] = None, on_skip: Optional[Callable[[str], None]] = None) -> dict:
    """Every reading of the corpus.

    Returns ``{"agents": (a, b), "reports": [...], "aggregate": {...},
    "next": {"lessons": ledger, "evals": ledger}}``. Raises ValueError when
    the corpus is not two agents or has no task both ran.
    """
    trajectories = list(trajectories)
    (name_a, name_b), by_task = pairs(trajectories)
    reports: list = []
    for task_id in sorted(by_task):
        pair = by_task[task_id]
        if name_a not in pair or name_b not in pair:
            if on_skip:
                on_skip(task_id)
            continue
        report = compare(pair[name_a], pair[name_b])
        if golden or policy:
            # progress before the answer: the golden task's milestones, both
            # runs; and the trust section re-read under the policy
            attach_milestones(report, golden, policy=policy)
        reports.append(report)
    if not reports:
        raise ValueError("no task was run by both agents")

    agg = build_aggregate(reports)
    agg["routing"] = routing_table(trajectories)
    agg["scorecard"] = build_scorecard(trajectories, golden, policy, outcomes or {})
    # the lessons and the forge read the golden set's policy when none is given
    effective = policy if policy is not None else (golden or {}).get("policy")
    agg["lessons"] = learn_lessons(trajectories, golden, effective, lessons_ledger)
    next_lessons = agg["lessons"]["ledger"].pop("next")
    # a command that runs this as its last step hands its own block in here
    agg.update(extra or {})
    if suppressions:
        agg["issues"] = build_issues(reports, suppressions)
    agg["forge"] = forge_evals(trajectories, golden, effective, issues=agg.get("issues"),
                               agents=agg.get("agents"), seeds=seeds, ledger=evals_ledger,
                               proposer=proposer)
    next_evals = agg["forge"]["ledger"].pop("next")
    return {"agents": (name_a, name_b), "reports": reports, "aggregate": agg,
            "next": {"lessons": next_lessons, "evals": next_evals}}
