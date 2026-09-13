"""The bridge out: what a comparison hands to an RL trainer.

A report already reads both runs as episodes (``report["rl"]``: the reward
each step was paid — *recorded* when the trace carried ``reward``, else
*shaped* from the reading's labels — the return, the credit, the
milestones reached) and carries a preference pair.  This module writes
those in the shapes trainers read, and nothing a trainer could not
justify from the report:

* :func:`to_verl_rewards` — one record per trajectory: the return, its
  terms by label, the per-step rewards, the milestones reached, the
  outcome.  A veRL reward manager reads it by ``trajectory_id``.
* :func:`to_verl_compute_score_template` — the Python source of a
  ``compute_score(data_source, solution_str, ground_truth, extra_info)``
  that looks the stored reward up by ``extra_info["trajectory_id"]``
  (veRL's custom reward-function contract; a file to drop beside the
  trainer, no network).
* :func:`to_preferences` — the pairs as DPO-style records: prompt,
  chosen and rejected as chat messages, the basis of the choice.
* :func:`to_agent_lightning_transitions` — one transition per step
  ``(state, action, reward, next_state, done)``, the shape Agent
  Lightning's credit assignment and any single-step RL algorithm takes.

Every record says ``"source": "recorded" | "shaped"`` so a trainer never
mistakes a reading of the report for a measurement, and every export is
deterministic over the reports it was given (sorted by task and side).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from .feedback import preference_pair

VERSION = 1

try:  # the episode reading lives in rl.py; rlexport only consumes it
    from .rl import rl_pair as _rl_pair
except ImportError:  # pragma: no cover - rl.py is part of the engine
    _rl_pair = None


# ---------------------------------------------------------------- loading

def load_reports(target: Union[str, Path, list, dict]) -> list:
    """Reports from a ``report_*.json`` file, a directory of them, a list of
    dicts or one dict — sorted by path so every export is reproducible."""
    if isinstance(target, dict):
        return [target]
    if isinstance(target, list):
        return [r for r in target if isinstance(r, dict)]
    path = Path(target)
    paths = sorted(path.glob("report_*.json")) if path.is_dir() else [path]
    reports = []
    for p in paths:
        if not p.is_file():
            raise ValueError(f"{p} is not a file")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{p}: not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "a" not in data or "b" not in data:
            raise ValueError(f"{p}: not a comparison report (no a/b sides)")
        reports.append(data)
    if not reports:
        raise ValueError(f"no report_*.json under {path}")
    return reports


# ---------------------------------------------------------------- the episode

def _episode(report: dict) -> dict:
    """``report["rl"]`` as written, or read now from the report."""
    rl = report.get("rl")
    if isinstance(rl, dict) and isinstance(rl.get("a"), dict) and isinstance(rl.get("b"), dict):
        return rl
    if _rl_pair is not None:
        return _rl_pair(report)
    # minimal fallback: recorded rewards or zeros, so the export still says what it is
    out: dict = {"source": "shaped"}
    for side in ("a", "b"):
        steps = (report.get(side) or {}).get("steps") or []
        rewards = [{"step": i, "reward": float(s.get("reward") or 0.0), "labels": []} for i, s in enumerate(steps)]
        if any(isinstance(s.get("reward"), (int, float)) for s in steps):
            out["source"] = "recorded"
        out[side] = {"agent": ((report.get(side) or {}).get("agent") or {}).get("name") or side,
                     "measurable": bool(steps), "rewards": rewards, "return": sum(r["reward"] for r in rewards)}
    return out


def _trajectory_id(report: dict, side: str) -> str:
    run = report.get(side) or {}
    tid = str(run.get("trace_id") or "")
    if tid:
        return tid
    task = (report.get("task") or {}).get("id") or "task"
    agent = (run.get("agent") or {}).get("name") or side
    return f"{task}__{agent}"


def _task(report: dict) -> dict:
    return report.get("task") or {}


def _data_source(report: dict) -> str:
    task = _task(report)
    return str(task.get("family") or task.get("data_source") or "agentdiff")


def _milestones_reached(report: dict, side: str) -> list:
    ms = ((report.get("milestones") or {}).get(side) or {}).get("milestones") or []
    return [str(m.get("id") or m.get("label") or "") for m in ms if isinstance(m, dict) and m.get("reached")]


def _reward_terms(rewards: list) -> dict:
    """``{label: count × sign}`` over a run's step rewards: how many steps
    carried each label, signed the way the shaping signs it (fed_answer and
    milestones +, everything else −)."""
    terms: dict = {}
    for row in rewards:
        for label in row.get("labels") or []:
            if label == "clean":
                continue
            sign = 1 if label in ("fed_answer",) else -1
            terms[label] = terms.get(label, 0) + sign
    return dict(sorted(terms.items()))


def _runs(reports: list):
    """Every (report, side, episode-run) in a fixed order."""
    for report in sorted(reports, key=lambda r: str(_task(r).get("id") or "")):
        episode = _episode(report)
        for side in ("a", "b"):
            run = episode.get(side) or {}
            if not run.get("measurable", True) or not run.get("rewards"):
                continue
            yield report, side, episode, run


# ---------------------------------------------------------------- exporters

def to_verl_rewards(reports: list) -> list:
    """One record per trajectory for a veRL reward manager: the return
    (recorded or shaped), its terms, the per-step rewards, the milestones
    reached and the outcome, keyed by ``trajectory_id``."""
    out = []
    for report, side, episode, run in _runs(reports):
        rewards = run["rewards"]
        step_rewards = [float(r.get("reward") or 0.0) for r in rewards]
        outcome = (report.get(side) or {}).get("outcome") or {}
        out.append({
            "version": VERSION,
            "data_source": _data_source(report),
            "uid": str(_task(report).get("id") or "task"),
            "trajectory_id": _trajectory_id(report, side),
            "agent": str(run.get("agent") or ((report.get(side) or {}).get("agent") or {}).get("name") or side),
            "source": str(episode.get("source") or run.get("source") or "shaped"),
            "reward": round(float(run.get("return", sum(step_rewards))), 4),
            "reward_terms": _reward_terms(rewards),
            "step_rewards": [round(r, 4) for r in step_rewards],
            "milestones": _milestones_reached(report, side),
            "outcome": outcome.get("success") is True,
            "recorded_score": outcome.get("score"),
            "expected": _task(report).get("expected"),
        })
    return out


def to_verl_compute_score_template() -> str:
    """Python source for a veRL custom reward function that returns the
    AgentDiff reward stored for ``extra_info["trajectory_id"]``.

    Drop the file beside the trainer and point
    ``custom_reward_function.path`` at it; put the JSONL that
    ``rlexport --format verl-rewards`` wrote where ``AGENTDIFF_REWARDS_JSONL``
    says (an environment variable, else ``agentdiff_rewards.jsonl`` next to
    the file).  Returns a dict with ``score`` plus the terms, so they land
    in veRL's ``reward_extra_info``.  No network; stdlib only.
    """
    return '''"""AgentDiff reward for veRL — generated by `agentdiff rlexport --format verl-reward-fn`.

A veRL custom reward function (custom_reward_function.path=<this file>):
compute_score(data_source, solution_str, ground_truth, extra_info) returns
the reward AgentDiff stored for extra_info["trajectory_id"] (else
extra_info["uid"]) in the rewards JSONL that `rlexport --format verl-rewards`
wrote.  The record's "source" says whether that reward was recorded by the
environment or shaped from the comparison's reading; both are returned as
reward_extra_info so the trainer's logs keep the distinction.  When no record
matches, the fallback is ground-truth containment (1.0 / 0.0) when a ground
truth is given, else 0.0 — never a guess dressed as a score.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT = os.path.join(_HERE, "agentdiff_rewards.jsonl")
_CACHE = {}


def _records(path=None):
    path = path or os.environ.get("AGENTDIFF_REWARDS_JSONL") or _DEFAULT
    if path not in _CACHE:
        table = {}
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    for key in ("trajectory_id", "uid"):
                        if rec.get(key) is not None:
                            table.setdefault(str(rec[key]), rec)
        _CACHE[path] = table
    return _CACHE[path]


def _fold(text):
    return " ".join(str(text).lower().replace(",", "").split())


def compute_score(data_source, solution_str, ground_truth, extra_info=None, **kwargs):
    extra = extra_info if isinstance(extra_info, dict) else {}
    table = _records(extra.get("agentdiff_rewards_jsonl"))
    rec = None
    for key in ("trajectory_id", "uid"):
        if extra.get(key) is not None and str(extra[key]) in table:
            rec = table[str(extra[key])]
            break
    if rec is not None:
        return {
            "score": float(rec.get("reward", 0.0)),
            "agentdiff_source": str(rec.get("source", "shaped")),
            "agentdiff_terms": json.dumps(rec.get("reward_terms", {}), sort_keys=True),
            "agentdiff_milestones": len(rec.get("milestones") or []),
            "agentdiff_outcome": 1.0 if rec.get("outcome") else 0.0,
        }
    if ground_truth is not None and str(ground_truth).strip():
        hit = _fold(ground_truth) in _fold(solution_str or "")
        return {"score": 1.0 if hit else 0.0, "agentdiff_source": "ground_truth_fallback"}
    return {"score": 0.0, "agentdiff_source": "no_record"}
'''


def _messages(turns: list) -> list:
    """A run's turns as chat messages: a tool step is an assistant tool
    call followed by its tool result; reasoning and the answer are assistant
    text."""
    messages = []
    for turn in turns:
        kind = turn.get("type")
        if kind in ("answer", "reason", "plan"):
            messages.append({"role": "assistant", "content": str(turn.get("output") or turn.get("input") or "")})
        else:
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"name": str(turn.get("name") or "tool"), "arguments": str(turn.get("input") or "")}]})
            messages.append({"role": "tool", "name": str(turn.get("name") or "tool"), "content": str(turn.get("output") or "")})
    return messages


def to_preferences(reports: list) -> list:
    """The preference pairs as DPO-style records: ``prompt``, ``chosen`` and
    ``rejected`` as chat messages, the basis of the choice.  ``source`` is
    ``recorded`` when the chosen side is the passing run verbatim (both
    outcomes were recorded) and ``shaped`` when it is the counterfactual
    splice (an estimate).  Complements :func:`deepcompare.feedback.to_jsonl`,
    which keeps the turns and labels as the report wrote them."""
    out = []
    for report in sorted(reports, key=lambda r: str(_task(r).get("id") or "")):
        pair = preference_pair(report)
        if not pair:
            continue
        chosen, rejected = pair["chosen"], pair["rejected"]
        spliced = "splice" in str(chosen.get("basis") or "")
        out.append({
            "version": VERSION,
            "task_id": pair.get("task_id"),
            "trajectory_id": f"{_trajectory_id(report, chosen['side'])}>{_trajectory_id(report, rejected['side'])}",
            "chosen_trajectory_id": _trajectory_id(report, chosen["side"]),
            "rejected_trajectory_id": _trajectory_id(report, rejected["side"]),
            "source": "shaped" if spliced else "recorded",
            "prompt": pair.get("prompt"),
            "expected": pair.get("expected"),
            "chosen": _messages(chosen.get("turns") or []),
            "rejected": _messages(rejected.get("turns") or []),
            "chosen_agent": chosen.get("agent"),
            "rejected_agent": rejected.get("agent"),
            "basis": chosen.get("basis"),
            "diverges_at": pair.get("diverges_at"),
            "confidence": pair.get("confidence"),
        })
    return out


def _observation(step: dict, limit: int = 240) -> str:
    text = str(step.get("output") or "")
    return text if len(text) <= limit else text[:limit] + "…"


def to_agent_lightning_transitions(reports: list) -> list:
    """One transition per step of every run: ``state`` (the step index, the
    tools called so far, the task), ``action`` (the step's type, name and
    input), ``reward`` (recorded or shaped), ``next_state`` (the index after,
    the tools including this one, the observation) and ``done``."""
    out = []
    for report, side, episode, run in _runs(reports):
        steps = (report.get(side) or {}).get("steps") or []
        rewards = {r.get("step"): float(r.get("reward") or 0.0) for r in run["rewards"]}
        task_id = str(_task(report).get("id") or "task")
        tid = _trajectory_id(report, side)
        agent = str(run.get("agent") or side)
        source = str(episode.get("source") or "shaped")
        prior: list = []
        for i, step in enumerate(steps):
            name = str(step.get("name") or "")
            after = prior + ([name] if step.get("type") not in ("answer", "reason", "plan") and name else [])
            out.append({
                "version": VERSION,
                "trajectory_id": tid,
                "task_id": task_id,
                "agent": agent,
                "step": i,
                "source": source,
                "state": {"step": i, "prior_tools": list(prior), "task_id": task_id},
                "action": {"type": step.get("type"), "name": name, "input": str(step.get("input") or "")},
                "reward": round(rewards.get(i, 0.0), 4),
                "next_state": {"step": i + 1, "prior_tools": after, "observation": _observation(step)},
                "done": i == len(steps) - 1,
            })
            prior = after
    return out


# ---------------------------------------------------------------- one entry point

FORMATS = ("verl-rewards", "verl-reward-fn", "preferences", "agent-lightning")


def export(reports: list, fmt: str) -> tuple[Any, int]:
    """``(payload, count)`` for one format: records (a list) for the JSONL
    formats, the Python source (a string) for ``verl-reward-fn``."""
    if fmt == "verl-rewards":
        records = to_verl_rewards(reports)
    elif fmt == "preferences":
        records = to_preferences(reports)
    elif fmt == "agent-lightning":
        records = to_agent_lightning_transitions(reports)
    elif fmt == "verl-reward-fn":
        source = to_verl_compute_score_template()
        return source, 1
    else:
        raise ValueError(f"unknown rlexport format {fmt!r}; known: {', '.join(FORMATS)}")
    return records, len(records)


def to_jsonl(records: list) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
