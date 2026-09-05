"""The whole ``runs`` analysis as one function, so the loop and the CLI
compute exactly the same thing from the same traces.

``analyse_runs`` takes trajectories from exactly two agents on shared
tasks and returns every artefact the ``runs`` command writes: the medoid
pair reports, the aggregate with equality, routing, stability,
reliability, task signal, consolidated diagnosis, paired inference and
triage. No printing, no files — the callers do that.
"""

from __future__ import annotations

from typing import Optional

from .consolidate import consolidate_diagnoses
from .equality import equality_analysis
from .metrics import aggregate as build_aggregate, task_signal
from .reliability import reliability
from .report import compare
from .router import routing_table
from .scorecard import scorecard
from .stability import medoid_pairs, stability_analysis
from .statistics import paired_inference
from .trace import Trajectory
from .triage import triage


class SuiteError(ValueError):
    """The traces cannot be analysed as a runs suite; the message says why."""


def group_runs(trajectories: list, warn=None) -> tuple:
    """``(name_a, name_b, runs_by_task)`` for a two-agent suite; tasks
    lacking runs on either side are dropped (and named to ``warn``)."""
    agent_names = sorted({t.agent.name for t in trajectories})
    if len(agent_names) != 2:
        raise SuiteError(f"runs mode needs traces from exactly 2 agents, "
                         f"found {len(agent_names)}: {', '.join(agent_names)}")
    name_a, name_b = agent_names
    runs_by_task: dict = {}
    for t in trajectories:
        side = "a" if t.agent.name == name_a else "b"
        runs_by_task.setdefault(t.task.id, {"a": [], "b": []})[side].append(t)
    for tid in sorted(runs_by_task):
        if not runs_by_task[tid]["a"] or not runs_by_task[tid]["b"]:
            if warn:
                warn(f"task {tid!r} lacks runs for both agents; skipped")
            del runs_by_task[tid]
    if not runs_by_task:
        raise SuiteError("no tasks with runs on both sides")
    return name_a, name_b, runs_by_task


def analyse_runs(trajectories: list, *, warn=None, family_pattern: Optional[str] = None,
                 golden: Optional[dict] = None, policy: Optional[dict] = None, raws: Optional[dict] = None) -> dict:
    """``golden``/``policy`` (see :mod:`deepcompare.scorecard`) make tool
    correctness and policy compliance measurable; ``raws`` (trace_id →
    trace dict) lets the scorecard report a judge's verdicts."""
    name_a, name_b, runs_by_task = group_runs(trajectories, warn)
    stability = stability_analysis(runs_by_task)
    reliability_analysis = reliability(runs_by_task)
    reports = [compare(a, b) for a, b in medoid_pairs(runs_by_task)]
    agg = build_aggregate(reports)
    agg["equality"] = equality_analysis(runs_by_task)
    agg["routing"] = routing_table(trajectories, equality=agg["equality"], family_pattern=family_pattern)
    agg["stability"] = stability
    agg["reliability"] = reliability_analysis
    agg["task_signal"] = task_signal(reports, stability)
    agg["diagnosis_consolidated"] = consolidate_diagnoses(runs_by_task)
    # the paired design the runs layout IS: both agents on the same tasks,
    # so the comparison is a paired difference with a sign test, never two
    # rates eyeballed against each other
    agg["paired_inference"] = paired_inference(
        [(sum(1.0 for t in runs_by_task[tid]["a"] if t.outcome.success) / len(runs_by_task[tid]["a"]),
          sum(1.0 for t in runs_by_task[tid]["b"] if t.outcome.success) / len(runs_by_task[tid]["b"]))
         for tid in sorted(runs_by_task)],
        labels=(name_a, name_b))
    # Re-triage now that reliability is attached: it is the only block that
    # can tell triage to stop ranking cross-agent claims confidently, and it
    # arrives after aggregate() has already run.
    agg["triage"] = triage(reports, agg)
    agg["scorecard"] = scorecard(trajectories, golden, policy, raws)
    return {"names": (name_a, name_b), "runs_by_task": runs_by_task, "reports": reports,
            "aggregate": agg, "stability": stability, "reliability": reliability_analysis}


def success_by_task(runs_by_task: dict) -> dict:
    """``{task: {"a": (successes, runs), "b": (successes, runs)}}`` — the
    counts every loop decision is made from."""
    out = {}
    for tid, sides in runs_by_task.items():
        out[tid] = {side: (sum(1 for t in trajs if t.outcome.success is True), len(trajs))
                    for side, trajs in sides.items()}
    return out


__all__ = ["SuiteError", "analyse_runs", "group_runs", "success_by_task", "Trajectory"]
