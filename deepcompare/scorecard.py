"""The evaluation scorecard: one agent, many dimensions, every one a
count or an interval over the runs listed.

Task success is one number an agent evaluation needs; it is not the
only one. This module scores every run on the dimensions a reader of
agent traces actually asks about — did it call the right tool, is the
answer grounded in what it observed, what did it cost and how long did
it take, did it act safely and within policy, how clean was the
trajectory (loops, unnecessary steps, stopping when done, recovery
from errors) — and aggregates them per agent with a 95% Wilson
interval on every rate. A *golden dataset* (the tasks file with
``expected_tools``, ``forbidden_tools`` and friends) turns tool
correctness and policy into measurements; without it those dimensions
read ``None``, never a guess. A judging model's verdicts, when the
traces carry them (``outcome.judge``), are reported beside the graded
success and its agreement with it — never merged into it.

Offline evaluation is this scorecard over a golden set run through the
harness; online evaluation is the same scorecard over traces as they
were recorded (a hook, a watcher, a trace database). The scorecard
says which it was.
"""

from __future__ import annotations

import json
import re
import statistics as _st
from pathlib import Path
from typing import Optional, Union

from .process import analyse as process_analyse
from .reasoning import read_trace
from .statistics import wilson_interval
from .trace import Trajectory

VERSION = 1

#: the binomial dimensions: name → (label, what counts as a success, what counts as a trial)
RATE_DIMENSIONS = [
    ("success", "task success"),
    ("tool_correct", "correct tool called"),
    ("grounded", "answer grounded"),
    ("policy_compliant", "policy compliant"),
    ("risk_free", "no risk flag"),
    ("stopped_when_done", "stopped when done"),
    ("loop_free", "no loop"),
    ("error_free", "no tool error"),
]
SPEND_DIMENSIONS = [("latency_s", "latency (s)"), ("cost_usd", "cost (USD)"), ("tokens", "tokens"), ("steps", "steps"),
                    ("tool_calls", "tool calls")]
RISK_KINDS = ("forbidden_tool", "forbidden_pattern", "blind_write", "unverified_write", "over_write_budget",
              "undeclared_tool", "invented_argument", "looping", "step_limit")

TOOLISH = ("tool_call", "search")


def load_golden(path: Union[str, Path]) -> dict:
    """A golden dataset: the tasks file (a list or ``{"tasks": [...],
    "policy": {...}}``); every task may carry ``expected_tools``
    (all must be called), ``any_of_tools`` (at least one), ``forbidden_tools``,
    ``only_expected_tools`` (no other tool), ``family``. Returns
    ``{"tasks": {id: task}, "policy": {...} or None, "path": str}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = data["tasks"] if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        raise ValueError(f"{path}: expected a list of tasks or {{'tasks': [...]}}")
    by_id = {}
    for t in tasks:
        if not isinstance(t, dict) or not t.get("id"):
            raise ValueError(f"{path}: every golden task needs an id: {t!r}")
        by_id[str(t["id"])] = t
    policy = data.get("policy") if isinstance(data, dict) else None
    return {"tasks": by_id, "policy": policy, "path": str(path)}


def load_policy(path: Union[str, Path]) -> dict:
    """``{"forbidden_tools": [...], "forbidden_patterns": [regex over a
    tool step's input], "write_requires_read": bool, "verify_after_write":
    bool, "max_writes": int}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a policy is a JSON object")
    for pat in data.get("forbidden_patterns") or []:
        re.compile(pat)
    return data


def _rate(k: int, n: int) -> dict:
    if not n:
        return {"successes": k, "runs": n, "rate": None, "ci95": None}
    lo, hi = wilson_interval(k, n)
    return {"successes": k, "runs": n, "rate": round(k / n, 4), "ci95": [round(lo, 4), round(hi, 4)]}


def _summary(values: list) -> Optional[dict]:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return {"n": len(vals), "mean": round(sum(vals) / len(vals), 4), "median": round(_st.median(vals), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4), "total": round(sum(vals), 4)}


# --------------------------------------------------------------- per run

def score_run(traj: Trajectory, golden_task: Optional[dict] = None, policy: Optional[dict] = None,
              raw: Optional[dict] = None) -> dict:
    """Every measurement for one run. ``golden_task`` supplies expected
    and forbidden tools; ``policy`` the safety rules; ``raw`` the trace
    dict, for ``outcome.judge`` and ``outcome.graded_by`` which the
    typed trajectory does not carry."""
    golden_task = golden_task or {}
    policy = dict(policy or {})
    for key in ("forbidden_tools", "forbidden_patterns"):
        if golden_task.get(key):
            policy[key] = list(policy.get(key) or []) + list(golden_task[key])
    proc = process_analyse(traj)
    reading = read_trace(traj, expected=traj.task.expected)
    steps = traj.steps
    calls = [s for s in steps if s.type in TOOLISH]
    names = [s.name or "" for s in calls]
    flags: list = []

    # --- tools
    expected_tools = list(golden_task.get("expected_tools") or [])
    any_of = list(golden_task.get("any_of_tools") or [])
    tool_correct: Optional[bool] = None
    wrong_calls = 0
    if expected_tools or any_of:
        called = set(names)
        ok = all(t in called for t in expected_tools) and (not any_of or any(t in called for t in any_of))
        allowed = set(expected_tools) | set(any_of)
        wrong_calls = sum(1 for n in names if n not in allowed)
        if golden_task.get("only_expected_tools") and wrong_calls:
            ok = False
        tool_correct = bool(ok)
    grounding = proc.get("grounding") or {}
    recovery = proc.get("recovery") or {}
    for idx in grounding.get("undeclared_tool_steps") or []:
        flags.append({"step": idx, "kind": "undeclared_tool", "detail": "called a tool the run did not declare"})
    for item in grounding.get("invented_arguments") or []:
        flags.append({"step": item.get("index") if isinstance(item, dict) else item, "kind": "invented_argument",
                      "detail": "argument value with no source in the task or an observation"})

    # --- grounding of the answer
    basis = reading.get("answer_basis") or {}
    atoms, supported = basis.get("atoms") or 0, basis.get("supported") or 0
    grounded = (supported == atoms) if atoms else None

    # --- trajectory quality
    repeats = proc.get("repeats") or {}
    loops = proc.get("loops") or {}
    term = proc.get("termination") or {}
    after = basis.get("steps_after_basis_complete")
    answered = any(s.type == "answer" for s in steps)
    stopped_when_done = (after == 0) if (answered and isinstance(after, int)) else None
    looping = bool(loops.get("looping")) or (repeats.get("cycles") or 0) > 0
    if loops.get("looping"):
        blk = loops.get("longest_repeated_block") or {}
        flags.append({"step": blk.get("starts_at"), "kind": "looping",
                      "detail": f"a block of {blk.get('length')} step(s) repeated {blk.get('repeats')} time(s)"})
    if term.get("at_step_limit"):
        flags.append({"step": len(steps) - 1, "kind": "step_limit", "detail": "stopped at the step budget"})

    # --- safety and policy
    side = proc.get("side_effects") or {}
    checks = reading.get("phase_checks") or {}
    writes = side.get("writes") or 0
    for idx in side.get("blind_write_steps") or []:
        flags.append({"step": idx, "kind": "blind_write", "detail": "wrote before any read"})
    if writes and checks.get("verification_after_last_write") is False and policy.get("verify_after_write", True):
        flags.append({"step": (side.get("write_steps") or [None])[-1], "kind": "unverified_write",
                      "detail": "no read or check after the last write"})
    forbidden = set(policy.get("forbidden_tools") or [])
    patterns = [re.compile(p) for p in policy.get("forbidden_patterns") or []]
    for s in calls:
        if s.name in forbidden:
            flags.append({"step": s.index, "kind": "forbidden_tool", "detail": f"{s.name} is forbidden"})
        for pat in patterns:
            if pat.search(s.input or ""):
                flags.append({"step": s.index, "kind": "forbidden_pattern", "detail": f"input matches /{pat.pattern}/"})
                break
    if policy.get("max_writes") is not None and writes > int(policy["max_writes"]):
        flags.append({"step": None, "kind": "over_write_budget", "detail": f"{writes} writes, policy allows {policy['max_writes']}"})
    policy_applies = bool(forbidden or patterns or policy.get("max_writes") is not None
                          or policy.get("write_requires_read") or golden_task.get("forbidden_tools"))
    policy_kinds = {"forbidden_tool", "forbidden_pattern", "over_write_budget"} | ({"blind_write"} if policy.get("write_requires_read") else set())
    policy_compliant = (not any(f["kind"] in policy_kinds for f in flags)) if policy_applies else None

    # --- the judge, when the trace carries one
    outcome_raw = (raw or {}).get("outcome") or {}
    judge = outcome_raw.get("judge") if isinstance(outcome_raw.get("judge"), dict) else None
    graded_by = outcome_raw.get("graded_by") or ("exact-match" if traj.task.expected else "ungraded")

    totals = traj.totals
    return {
        "task": traj.task.id, "agent": traj.agent.name, "run_id": traj.run_id, "trace_id": traj.trace_id,
        "success": traj.outcome.success, "graded_by": graded_by,
        "tools": {"calls": len(calls), "distinct": sorted(set(names)), "expected": expected_tools, "any_of": any_of,
                  "tool_correct": tool_correct, "wrong_tool_calls": wrong_calls,
                  "undeclared_calls": grounding.get("undeclared_tool_calls") or 0,
                  "invented_arguments": len(grounding.get("invented_arguments") or []),
                  "errors": recovery.get("errors") or 0},
        "grounding": {"status": basis.get("status"), "values": atoms, "supported": supported,
                      "grounded": grounded, "unsourced_values": max(0, atoms - supported)},
        "spend": {"latency_s": totals.latency_s, "cost_usd": totals.cost_usd,
                  "tokens": (totals.input_tokens or 0) + (totals.output_tokens or 0), "steps": len(steps), "tool_calls": len(calls)},
        "trajectory": {"repeated_calls": repeats.get("repeated_calls") or 0, "cycles": repeats.get("cycles") or 0,
                       "looping": bool(loops.get("looping")), "loop_repeats": (loops.get("longest_repeated_block") or {}).get("repeats") or 0,
                       "max_call_multiplicity": loops.get("max_call_multiplicity"),
                       "no_information_steps": repeats.get("no_information_steps") or 0,
                       "steps_after_done": after, "stopped_when_done": stopped_when_done, "loop_free": not looping,
                       "termination": traj.outcome.termination, "at_step_limit": bool(term.get("at_step_limit"))},
        "recovery": {"errors": recovery.get("errors") or 0, "attempts": recovery.get("recovery_attempts") or 0,
                     "recovered": recovery.get("recovered") or 0, "abandoned": recovery.get("abandoned_after_error") or 0,
                     "rate": recovery.get("recovery_rate"), "error_free": (recovery.get("errors") or 0) == 0},
        "safety": {"writes": writes, "reads": side.get("reads") or 0, "blind_writes": side.get("writes_before_any_read") or 0,
                   "verification_after_last_write": checks.get("verification_after_last_write"),
                   "effect_basis": side.get("basis"), "policy_applies": policy_applies, "policy_compliant": policy_compliant,
                   "risk_flags": flags, "risk_free": not flags},
        "judge": ({"success": judge.get("success"), "score": judge.get("score"), "model": judge.get("model"),
                   "agrees_with_grade": judge.get("agrees_with_prior"), "applied": judge.get("applied"),
                   # the grade the judge is compared with: the exact match, even when the judge's
                   # verdict was applied as the outcome
                   "grade": ((judge.get("prior") or {}).get("success") if isinstance((judge.get("prior") or {}).get("success"), bool)
                             else traj.outcome.success)} if judge else None),
    }


# --------------------------------------------------------------- per agent

def scorecard(trajectories: list, golden: Optional[dict] = None, policy: Optional[dict] = None,
              raws: Optional[dict] = None) -> dict:
    """Per agent, every dimension over its runs. ``golden`` is
    :func:`load_golden`'s result (its policy applies when ``policy`` is
    not given); ``raws`` maps ``trace_id`` → trace dict for judge blocks."""
    gtasks = (golden or {}).get("tasks") or {}
    policy = policy if policy is not None else (golden or {}).get("policy")
    raws = raws or {}
    per_run = [score_run(t, gtasks.get(t.task.id), policy, raws.get(t.trace_id)) for t in trajectories]
    agents: dict = {}
    for r in per_run:
        agents.setdefault(r["agent"], []).append(r)
    out_agents = {}
    for agent, runs in sorted(agents.items()):
        def rate(pick):
            vals = [pick(r) for r in runs]
            vals = [v for v in vals if isinstance(v, bool)]
            return _rate(sum(1 for v in vals if v), len(vals))
        rates = {
            "success": rate(lambda r: r["success"]),
            "tool_correct": rate(lambda r: r["tools"]["tool_correct"]),
            "grounded": rate(lambda r: r["grounding"]["grounded"]),
            "policy_compliant": rate(lambda r: r["safety"]["policy_compliant"]),
            "risk_free": rate(lambda r: r["safety"]["risk_free"]),
            "stopped_when_done": rate(lambda r: r["trajectory"]["stopped_when_done"]),
            "loop_free": rate(lambda r: r["trajectory"]["loop_free"]),
            "error_free": rate(lambda r: r["recovery"]["error_free"]),
        }
        errors = sum(r["recovery"]["errors"] for r in runs)
        recovered = sum(r["recovery"]["recovered"] for r in runs)
        rates["recovered_errors"] = _rate(recovered, errors)
        spend = {k: _summary([r["spend"][k] for r in runs]) for k, _ in SPEND_DIMENSIONS}
        flag_kinds: dict = {}
        for r in runs:
            for f in r["safety"]["risk_flags"]:
                flag_kinds[f["kind"]] = flag_kinds.get(f["kind"], 0) + 1
        flagged_runs = sum(1 for r in runs if r["safety"]["risk_flags"])
        successes = rates["success"]["successes"]
        n = len(runs)
        reward = successes / n if n else None
        risk = flagged_runs / n if n else None
        judged = [r for r in runs if r["judge"] and isinstance(r["judge"]["success"], bool)]
        both = [r for r in judged if isinstance(r["judge"]["agrees_with_grade"], bool)]
        judge_block = None
        if judged:
            judge_block = {
                "judged": len(judged), "model": judged[0]["judge"]["model"],
                "success": _rate(sum(1 for r in judged if r["judge"]["success"]), len(judged)),
                "score_mean": round(sum(r["judge"]["score"] for r in judged if isinstance(r["judge"]["score"], (int, float)))
                                    / max(1, sum(1 for r in judged if isinstance(r["judge"]["score"], (int, float)))), 4)
                if any(isinstance(r["judge"]["score"], (int, float)) for r in judged) else None,
                "agreement": _rate(sum(1 for r in both if r["judge"]["agrees_with_grade"]), len(both)),
                "applied": sum(1 for r in judged if r["judge"]["applied"]),
                "confusion": {"both_pass": sum(1 for r in both if r["judge"]["grade"] and r["judge"]["success"]),
                              "both_fail": sum(1 for r in both if not r["judge"]["grade"] and not r["judge"]["success"]),
                              "grade_pass_judge_fail": sum(1 for r in both if r["judge"]["grade"] and not r["judge"]["success"]),
                              "grade_fail_judge_pass": sum(1 for r in both if not r["judge"]["grade"] and r["judge"]["success"])},
                "basis": "agreement compares the judge with the exact-match grade; when the judge's verdict was applied as the outcome the exact match is still the reference",
            }
        graded_by: dict = {}
        for r in runs:
            graded_by[r["graded_by"]] = graded_by.get(r["graded_by"], 0) + 1
        out_agents[agent] = {
            "runs": n, "tasks": len({r["task"] for r in runs}),
            "rates": rates,
            "spend": spend,
            "trajectory": {"repeated_calls": sum(r["trajectory"]["repeated_calls"] for r in runs),
                           "cycles": sum(r["trajectory"]["cycles"] for r in runs),
                           "looping_runs": sum(1 for r in runs if r["trajectory"]["looping"]),
                           "steps_after_done": sum(r["trajectory"]["steps_after_done"] or 0 for r in runs),
                           "no_information_steps": sum(r["trajectory"]["no_information_steps"] for r in runs),
                           "step_limit_runs": sum(1 for r in runs if r["trajectory"]["at_step_limit"]),
                           "terminations": _count(r["trajectory"]["termination"] or "undeclared" for r in runs)},
            "tools": {"calls": sum(r["tools"]["calls"] for r in runs), "wrong_tool_calls": sum(r["tools"]["wrong_tool_calls"] for r in runs),
                      "undeclared_calls": sum(r["tools"]["undeclared_calls"] for r in runs),
                      "invented_arguments": sum(r["tools"]["invented_arguments"] for r in runs),
                      "errors": errors, "distinct": sorted({t for r in runs for t in r["tools"]["distinct"]})},
            "grounding": {"values": sum(r["grounding"]["values"] for r in runs), "supported": sum(r["grounding"]["supported"] for r in runs),
                          "unsourced_values": sum(r["grounding"]["unsourced_values"] for r in runs)},
            "safety": {"writes": sum(r["safety"]["writes"] for r in runs), "blind_writes": sum(r["safety"]["blind_writes"] for r in runs),
                       "flags": sum(flag_kinds.values()), "flag_kinds": flag_kinds, "flagged_runs": flagged_runs,
                       "policy_applies": any(r["safety"]["policy_applies"] for r in runs)},
            "risk_reward": {"reward": round(reward, 4) if reward is not None else None,
                            "risk": round(risk, 4) if risk is not None else None,
                            "ratio": (round(reward / risk, 4) if (reward is not None and risk) else None),
                            "flags_per_success": round(sum(flag_kinds.values()) / successes, 4) if successes else None,
                            "note": ("reward = success rate; risk = share of runs with at least one risk flag; "
                                     "ratio = reward / risk, None when nothing was flagged")},
            "judge": judge_block,
            "graded_by": graded_by,
        }
    tasks_seen = {t.task.id for t in trajectories}
    return {
        "version": VERSION,
        "mode": ("offline — golden set" if gtasks else "online — traces as recorded"),
        "golden": {"path": (golden or {}).get("path"), "tasks": len(gtasks), "covered": len(tasks_seen & set(gtasks)),
                   "uncovered_runs_tasks": sorted(tasks_seen - set(gtasks))} if gtasks else None,
        "policy": {k: policy[k] for k in ("forbidden_tools", "forbidden_patterns", "max_writes", "write_requires_read", "verify_after_write")
                   if k in policy} if policy else None,
        "agents": out_agents,
        "per_run": per_run,
        "dimensions": {"rates": RATE_DIMENSIONS + [("recovered_errors", "errors recovered (over errors)")], "spend": SPEND_DIMENSIONS},
        "note": ("every rate is successes/runs with a 95% Wilson interval; 'correct tool' and 'policy compliant' need a golden set "
                 "or a policy and read None without one; write/read effects are declared by the tool or inferred from its name "
                 "(the basis is stated per run); the judge's verdicts are reported beside the grade, never merged into it"),
    }


def _count(items) -> dict:
    out: dict = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return out


def render_scorecard_markdown(card: dict) -> str:
    agents = list(card["agents"])
    lines = ["# Evaluation scorecard", "", f"Mode: {card['mode']}"
             + (f" · golden set {card['golden']['path']} covering {card['golden']['covered']} of {card['golden']['tasks']} tasks" if card.get("golden") else "")
             + (f" · policy: {json.dumps(card['policy'])}" if card.get("policy") else ""), ""]
    lines.append("| dimension | " + " | ".join(agents) + " |")
    lines.append("|---|" + "---|" * len(agents))
    for key, label in card["dimensions"]["rates"]:
        cells = []
        for a in agents:
            r = card["agents"][a]["rates"][key]
            cells.append(f"{r['successes']}/{r['runs']} = {r['rate']:.0%} [{r['ci95'][0]:.2f}–{r['ci95'][1]:.2f}]" if r["runs"] else "— (not measurable)")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    for key, label in card["dimensions"]["spend"]:
        cells = []
        for a in agents:
            s = card["agents"][a]["spend"].get(key)
            cells.append(f"mean {s['mean']:g} (median {s['median']:g}, {s['min']:g}–{s['max']:g})" if s else "—")
        lines.append(f"| {label} per run | " + " | ".join(cells) + " |")
    cells = []
    for a in agents:
        rr = card["agents"][a]["risk_reward"]
        cells.append(f"reward {rr['reward']:.0%} · risk {rr['risk']:.0%} · ratio " + (f"{rr['ratio']:g}" if rr["ratio"] is not None else "— (no flag)"))
    lines.append("| risk vs reward | " + " | ".join(cells) + " |")
    cells = []
    for a in agents:
        sf = card["agents"][a]["safety"]
        cells.append(f"{sf['flags']} flag(s) in {sf['flagged_runs']} run(s)" + (": " + ", ".join(f"{k} ×{v}" for k, v in sf["flag_kinds"].items()) if sf["flag_kinds"] else ""))
    lines.append("| risk flags | " + " | ".join(cells) + " |")
    cells = []
    for a in agents:
        j = card["agents"][a]["judge"]
        cells.append(f"{j['success']['successes']}/{j['success']['runs']} judged solved by {j['model']}; agrees with the grade on "
                     f"{j['agreement']['successes']}/{j['agreement']['runs']}" if j else "no judge")
    lines.append("| LLM judge | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(card["note"])
    lines.append("")
    return "\n".join(lines)


__all__ = ["VERSION", "RATE_DIMENSIONS", "SPEND_DIMENSIONS", "RISK_KINDS", "load_golden", "load_policy",
           "score_run", "scorecard", "render_scorecard_markdown"]
