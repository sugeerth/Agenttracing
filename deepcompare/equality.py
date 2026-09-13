"""Equality of output: do repeated runs say the same thing, and do two
agents?

An agent that is right once and wrong twice, or that gives three
different answers to one question, is not one a router should trust
however its mean looks. This module reads the final answers of every
run per task and reports, per agent, how many distinct answers there
were, how often runs agreed with the majority (the *equality rate*),
whether the majority matches the expected answer, and, across agents,
whether they agree with each other — every number a count or a ratio
over the runs listed, never an estimate.

Answers are compared after a light normalisation (case, whitespace,
punctuation, thousands separators, a few unit spellings) so that
"23 hours 45 minutes" and "23h 45m" count as the same answer and "23
hours" and "11 hours" do not; the normalisation is named in the output
so the reader knows what "equal" meant.
"""

from __future__ import annotations

import re
from typing import Optional

from .trace import Trajectory

NORMALISATION = ("lower-case; whitespace and punctuation collapsed; thousands separators dropped; "
                 "hours/minutes/seconds spelled out to h/m/s; a trailing full stop dropped")

_UNITS = [
    (r"\bhours?\b", "h"), (r"\bhrs?\b", "h"), (r"\bminutes?\b", "m"), (r"\bmins?\b", "m"),
    (r"\bseconds?\b", "s"), (r"\bsecs?\b", "s"), (r"\bpercent\b", "%"), (r"\bbillion\b", "b"), (r"\bmillion\b", "m"),
]


def normalise(answer: Optional[str]) -> str:
    text = (answer or "").strip().lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    for pattern, unit in _UNITS:
        text = re.sub(pattern, unit, text)
    text = re.sub(r"(\d)\s+(h|m|s|b)\b", r"\1\2", text)
    text = re.sub(r"(\d)\s+%", r"\1%", text)
    text = re.sub(r"[^\w%$.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text


def _groups(trajs: list) -> dict:
    groups: dict = {}
    for t in trajs:
        key = normalise(t.outcome.answer)
        groups.setdefault(key, []).append(t)
    return groups


def equality_analysis(runs_by_task: dict) -> dict:
    """``runs_by_task``: ``{task_id: {agent: [Trajectory, ...]}}``."""
    tasks = {}
    per_agent: dict = {}
    for task_id in sorted(runs_by_task):
        agents = runs_by_task[task_id]
        row: dict = {"task": task_id, "agents": {}, "expected": None, "cross_agent": None}
        expected = None
        for agent in sorted(agents):
            trajs = agents[agent]
            if not trajs:
                continue
            expected = expected or trajs[0].task.expected
            groups = _groups(trajs)
            majority_key = max(groups, key=lambda k: (len(groups[k]), k))
            majority = groups[majority_key]
            n = len(trajs)
            entry = {
                "runs": n,
                "distinct_answers": len(groups),
                "equality_rate": round(len(majority) / n, 4),
                "majority_answer": majority[0].outcome.answer,
                "majority_matches_expected": (normalise(expected) in normalise(majority[0].outcome.answer)
                                              if expected else None),
                "successes": sum(1 for t in trajs if t.outcome.success is True),
                "answers": [{"answer": g[0].outcome.answer, "runs": len(g),
                             "success": sum(1 for t in g if t.outcome.success is True)}
                            for g in sorted(groups.values(), key=lambda g: -len(g))],
            }
            row["agents"][agent] = entry
            acc = per_agent.setdefault(agent, {"tasks": 0, "runs": 0, "agreeing_runs": 0, "distinct_total": 0,
                                                "unanimous_tasks": 0})
            acc["tasks"] += 1
            acc["runs"] += n
            acc["agreeing_runs"] += len(majority)
            acc["distinct_total"] += len(groups)
            acc["unanimous_tasks"] += 1 if len(groups) == 1 else 0
        row["expected"] = expected
        names = sorted(row["agents"])
        if len(names) >= 2:
            keys = {a: normalise(row["agents"][a]["majority_answer"]) for a in names}
            same = len(set(keys.values())) == 1
            row["cross_agent"] = {"agents": names, "majorities_equal": same,
                                  "pairs": [{"a": names[i], "b": names[j], "equal": keys[names[i]] == keys[names[j]]}
                                            for i in range(len(names)) for j in range(i + 1, len(names))]}
        tasks[task_id] = row
    summary = {}
    for agent, acc in per_agent.items():
        summary[agent] = {
            "tasks": acc["tasks"], "runs": acc["runs"],
            "equality_rate": round(acc["agreeing_runs"] / acc["runs"], 4) if acc["runs"] else None,
            "unanimous_tasks": acc["unanimous_tasks"],
            "mean_distinct_answers": round(acc["distinct_total"] / acc["tasks"], 3) if acc["tasks"] else None,
        }
    cross = [t["cross_agent"]["majorities_equal"] for t in tasks.values() if t.get("cross_agent")]
    return {
        "version": 1,
        "normalisation": NORMALISATION,
        "tasks": tasks,
        "per_agent": summary,
        "cross_agent": {"tasks_compared": len(cross), "majorities_equal": sum(1 for c in cross if c)} if cross else None,
        "note": ("equality_rate = runs agreeing with the task's majority answer / runs; a majority that "
                 "matches the expected answer is right, one that does not is consistently wrong — "
                 "both are reported, neither is a score"),
    }


def equality_features(analysis: dict, agent: str, family_of=None) -> dict:
    """The per-agent equality numbers a router's feature table carries,
    optionally folded per family."""
    out: dict = {}
    for task_id, row in (analysis.get("tasks") or {}).items():
        entry = row["agents"].get(agent)
        if not entry:
            continue
        fam = family_of(task_id) if family_of else task_id
        acc = out.setdefault(fam, {"runs": 0, "agreeing": 0, "distinct": 0, "tasks": 0, "wrong_majorities": 0})
        acc["runs"] += entry["runs"]
        acc["agreeing"] += round(entry["equality_rate"] * entry["runs"])
        acc["distinct"] += entry["distinct_answers"]
        acc["tasks"] += 1
        if entry["majority_matches_expected"] is False:
            acc["wrong_majorities"] += 1
    return {fam: {"equality_rate": round(a["agreeing"] / a["runs"], 4) if a["runs"] else None,
                  "mean_distinct_answers": round(a["distinct"] / a["tasks"], 3) if a["tasks"] else None,
                  "consistently_wrong_tasks": a["wrong_majorities"]} for fam, a in out.items()}
