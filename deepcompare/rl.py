"""The run read as an episode: reward, return and credit, step by step.

The impact layer weighs a run by its faults — the decisive step, the
fault's path, errors, wasted seconds. A policy under training is read by
what it *earned*: the reward each step was paid, the return so far and
the return still to come, and the credit each stretch deserves for the
gap between two runs. This module reads a comparison report (or a single
trace) that way, in the same shape the impact layer uses — clusters that
fold, marks that drill down — so a page can show a policy's episode next
to the fault-weighted view without a second vocabulary.

Two sources of reward, always named:

* **recorded** — a step carries ``reward`` (SCHEMA.md Step; written by
  ``Recorder.step(..., reward=)``). The reward is the environment's, and
  this module only sums it. One recorded reward on either side of a pair
  makes the whole pair *recorded*; a step without one earned 0.
* **shaped** — no step of either side carries a reward. A sparse reward
  is then derived from the labels :func:`deepcompare.feedback.step_labels`
  gives every step, weighted by ``SHAPED_WEIGHTS`` (a step with several
  labels sums them): ``fault_enters`` −3, ``wrong_answer`` −5 (on the
  answer step, in place of the outcome reward), ``error`` −1,
  ``invented_argument`` −1, ``repeat`` −0.5, ``spent_after_basis`` −0.2,
  ``dead_end`` and ``no_information`` −0.1, ``fed_answer`` +1; the answer
  +5 on success (a failed answer the report did not label ``wrong_answer``
  pays −5 from the outcome); each milestone reached +2 at its step. The
  small weights keep a long run's hundreds of unproductive steps from
  drowning the answer and the decisive step. A shaped reward is a reading
  of the report, not a measurement, and every place it is shown says
  ``shaped``.

Returns are sums (``return = Σ reward``); the discounted return uses
``GAMMA`` (0.99) over step order, so a step's ``discounted_to_go`` is
``reward + γ × the next step's``. **Credit** comes from the Shapley
section when it is available: each divergence region's allocation is
spread evenly over this side's steps in the region's alignment rows,
positive on the winner's steps and negative on the loser's, in the
Shapley metric's unit (tokens by default). Clusters reuse the impact
layer's lanes, boundaries and coalescing (:func:`deepcompare.impact.cluster_steps`)
but are scored by ``Σ|reward| + Σ|credit| + 2 per fault_enters / wrong_answer
label``, normalised on the pair's largest cluster score; marks are the
steps whose ``|reward|`` reaches the run's 90th percentile, plus the
decisive step and the answer. Every number here is a count or a sum over
recorded or labelled steps; nothing is simulated.
"""

from __future__ import annotations

import math
from typing import Optional

from . import impact as _impact
from .feedback import preference_pair, step_labels
from .rlaudit import audit_aggregate, audit_pair
from .rlstats import BOOTSTRAP_SAMPLES as _rlstats_samples, rl_stats
from .trace import Trajectory

VERSION = 1
GAMMA = 0.99
#: the shaped rule: reward per feedback label (summed when a step carries
#: several), plus the answer on success and each milestone reached.
#: ``wrong_answer`` sits on the answer step and replaces the outcome reward.
SHAPED_WEIGHTS = {
    "fault_enters": -3.0,
    "wrong_answer": -5.0,
    "error": -1.0,
    "invented_argument": -1.0,
    "repeat": -0.5,
    "spent_after_basis": -0.2,
    "dead_end": -0.1,
    "no_information": -0.1,
    "fed_answer": 1.0,
    "milestone": 2.0,
    "answer": 5.0,
}
ANSWER_REWARD = SHAPED_WEIGHTS["answer"]
DECISIVE_REWARD = SHAPED_WEIGHTS["fault_enters"]
MILESTONE_REWARD = SHAPED_WEIGHTS["milestone"]
#: labels that add 2 to a step's cluster score (the fault entering, the wrong answer)
FAULT_LABELS = ("fault_enters", "wrong_answer")
FAULT_SCORE = 2.0
TOP = 5                 #: largest rewards / top credits listed per run
MARK_QUANTILE = 0.9     #: |reward| at or above this quantile of the run marks a step
RLSTATS_SAMPLES = _rlstats_samples   #: bootstrap resamples for the ``stats`` section
TOOLISH = _impact.TOOLISH


# ---------------------------------------------------------------- formatting

def _num(v: float) -> str:
    """A reward or return as text: integers plain, else up to two decimals,
    with a real minus sign."""
    if abs(v - round(v)) < 1e-9:
        text = f"{int(round(v))}"
    else:
        text = f"{v:.2f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _signed(v: float) -> str:
    text = _num(v)
    return text if v < 0 else f"+{text}"


def _credit_text(v: float, metric: Optional[str]) -> str:
    """A credit in the Shapley metric's unit, signed, thousands grouped."""
    unit = (metric or "").replace("_", " ")
    sign = "−" if v < 0 else "+"
    mag = f"{abs(v):,.0f}" if abs(v) >= 100 or abs(v - round(v)) < 1e-9 else f"{abs(v):.2f}".rstrip("0").rstrip(".")
    return f"{sign}{mag} {unit}".strip()


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _percentile(values: list, q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _side_name(report: dict, side: str) -> str:
    return str((((report.get(side) or {}).get("agent") or {}).get("name")) or side)


def _steps(report: dict, side: str) -> list:
    return [s for s in ((report.get(side) or {}).get("steps") or []) if isinstance(s, dict)]


# ---------------------------------------------------------------- rewards

def _source(report: dict) -> str:
    for side in ("a", "b"):
        for st in _steps(report, side):
            if isinstance(st.get("reward"), (int, float)) and not isinstance(st.get("reward"), bool):
                return "recorded"
    return "shaped"


def _labels_by_step(report: dict) -> dict:
    out: dict = {}
    for rec in step_labels(report):
        out[(rec["side"], rec["step"])] = [l["label"] for l in rec["labels"] if isinstance(l, dict) and l.get("label")]
    return out


def _milestones_at(report: dict, side: str) -> dict:
    at: dict = {}
    for m in (((report.get("milestones") or {}).get(side) or {}).get("milestones") or []):
        if isinstance(m, dict) and m.get("reached") and isinstance(m.get("step"), int):
            at.setdefault(m["step"], []).append(str(m.get("id") or m.get("label") or ""))
    return at


def _shaped(step: dict, labels: list, success: Optional[bool], milestones: list) -> tuple:
    """(reward, reasons) for one step: its labels' weights summed, the
    answer's outcome, the milestones reached at it."""
    reward = 0.0
    reasons: list = []
    for label in labels:
        if label in SHAPED_WEIGHTS and label not in ("milestone", "answer"):
            reward += SHAPED_WEIGHTS[label]
            reasons.append(label.replace("_", " "))
    if step.get("type") == "answer":
        if success is True:
            reward += SHAPED_WEIGHTS["answer"]
            reasons.append("answer succeeded")
        elif "wrong_answer" not in labels:
            reward -= SHAPED_WEIGHTS["answer"]
            reasons.append("answer failed")
    for mid in milestones:
        reward += SHAPED_WEIGHTS["milestone"]
        reasons.append(f"milestone {mid}")
    return reward, reasons


def _credit_by_step(report: dict, side: str) -> tuple:
    """(per-step credit or None, the credit block) from the Shapley section:
    each allocation spread evenly over this side's steps in its rows, signed
    for the winner (+) and the loser (−)."""
    sh = report.get("shapley") or {}
    allocations = sh.get("allocations") if sh.get("available") else None
    if not allocations:
        return None, {"source": None, "metric": None, "top": [], "total": 0.0}
    name = _side_name(report, side)
    if sh.get("winner") == name and sh.get("loser") != name:
        sign = 1.0
    elif sh.get("loser") == name and sh.get("winner") != name:
        sign = -1.0
    else:
        sign = -1.0 if (report.get("attribution") or {}).get("failed_agent") == side else 1.0
    alignment = report.get("alignment") or []
    key = f"{side}_index"
    per: dict = {}
    for alloc in allocations:
        if not isinstance(alloc, dict):
            continue
        phi = alloc.get("shapley")
        if not isinstance(phi, (int, float)):
            continue
        idx = []
        for r in alloc.get("alignment_rows") or []:
            if isinstance(r, int) and 0 <= r < len(alignment) and isinstance(alignment[r].get(key), int):
                idx.append(alignment[r][key])
        if not idx:
            continue
        share = sign * float(phi) / len(idx)
        for i in idx:
            per[i] = per.get(i, 0.0) + share
    per = {i: round(v, 4) for i, v in per.items()}
    top = sorted(((i, v) for i, v in per.items() if v != 0), key=lambda kv: (-abs(kv[1]), kv[0]))[:TOP]
    return per, {"source": "shapley", "metric": sh.get("metric"),
                 "top": [{"step": i, "credit": v} for i, v in top], "total": round(sum(per.values()), 4)}


# ---------------------------------------------------------------- one run

def _run(report: dict, side: str, source: str, labels_by_step: dict, gamma: float = GAMMA) -> dict:
    name = _side_name(report, side)
    steps = _steps(report, side)
    if not steps:
        return _empty(name, source)
    success = ((report.get(side) or {}).get("outcome") or {}).get("success")
    ms_at = _milestones_at(report, side)
    credit_per, credit = _credit_by_step(report, side)
    rewards: list = []
    for pos, st in enumerate(steps):
        i = st.get("index") if isinstance(st.get("index"), int) else pos
        labels = labels_by_step.get((side, pos), [])
        if source == "recorded":
            raw = st.get("reward")
            r = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
            reasons = [l.replace("_", " ") for l in labels if l != "clean"]
        else:
            r, reasons = _shaped(st, labels, success, ms_at.get(i, []))
        sp = st.get("span") if isinstance(st.get("span"), dict) else None
        value = st.get("value") if isinstance(st.get("value"), (int, float)) and not isinstance(st.get("value"), bool) else None
        adv = st.get("advantage") if isinstance(st.get("advantage"), (int, float)) and not isinstance(st.get("advantage"), bool) else None
        rewards.append({"step": i, "reward": round(r, 4), "cum": 0.0, "to_go": 0.0, "discounted_to_go": 0.0,
                        "credit": None if credit_per is None else credit_per.get(i, 0.0),
                        "labels": list(labels), "agent": str(sp["agent"]) if sp and sp.get("agent") else name,
                        "kind": st.get("type"), "name": st.get("name") or "",
                        "value": None if value is None else float(value), "advantage": None if adv is None else float(adv),
                        "why": ", ".join(reasons) if reasons else ("recorded" if source == "recorded" else "nothing labelled")})
    cum = 0.0
    for row in rewards:
        cum += row["reward"]
        row["cum"] = round(cum, 4)
    to_go = disc = 0.0
    for row in reversed(rewards):
        to_go += row["reward"]
        disc = row["reward"] + gamma * disc
        row["to_go"] = round(to_go, 4)
        row["discounted_to_go"] = round(disc, 4)
    total = round(sum(r["reward"] for r in rewards), 4)
    positive = sum(1 for r in rewards if r["reward"] > 0)
    negative = sum(1 for r in rewards if r["reward"] < 0)
    largest = sorted((r for r in rewards if r["reward"] != 0), key=lambda r: (-abs(r["reward"]), r["step"]))[:TOP]
    seconds = round(sum(float(st.get("latency_s") or 0.0) for st in steps if isinstance(st.get("latency_s"), (int, float))), 4)
    run = {
        "agent": name, "measurable": True, "source": source, "steps": len(steps), "return": total,
        "discounted_return": rewards[0]["discounted_to_go"] if rewards else 0.0,
        "positive": positive, "negative": negative, "zero": len(rewards) - positive - negative, "seconds": seconds,
        "success": success, "rewards": rewards,
        "largest": [{"step": r["step"], "reward": r["reward"], "why": r["why"]} for r in largest],
        "credit": credit,
        "clusters": _clusters(report, side, steps, rewards),
        "narrative": "",
    }
    run["narrative"] = _run_narrative(run)
    return run


def _empty(name: str, source: str) -> dict:
    return {"agent": name, "measurable": False, "source": source, "steps": 0, "return": 0.0, "discounted_return": 0.0,
            "positive": 0, "negative": 0, "zero": 0, "seconds": 0.0, "success": None, "rewards": [], "largest": [],
            "credit": {"source": None, "metric": None, "top": [], "total": 0.0}, "clusters": [],
            "narrative": f"{name}: no steps, so no reward to sum."}


# ---------------------------------------------------------------- clusters

def _clusters(report: dict, side: str, steps: list, rewards: list) -> list:
    facts = _impact.step_facts(report, side, steps)
    by_pos = {pos: row for pos, row in enumerate(rewards)}
    for f in facts:
        row = by_pos.get(f["pos"]) or {}
        f["reward"] = float(row.get("reward") or 0.0)
        f["credit"] = float(row.get("credit") or 0.0)
        f["fault_labels"] = sum(1 for l in (row.get("labels") or []) if l in FAULT_LABELS)
        f["why"] = row.get("why") or ""
        f["score"] = abs(f["reward"]) + abs(f["credit"]) + FAULT_SCORE * f["fault_labels"]
    magnitudes = [abs(f["reward"]) for f in facts if f["reward"] != 0]
    threshold = max(_percentile(magnitudes, MARK_QUANTILE), 1e-9) if magnitudes else None

    def marks(chunk: list) -> list:
        out: list = []
        for f in chunk:
            if f["decisive"]:
                out.append({"step": f["index"], "kind": "decisive", "label": f"decisive step ({f['name'] or f['type']})"})
            if threshold is not None and abs(f["reward"]) >= threshold:
                out.append({"step": f["index"], "kind": "reward+" if f["reward"] > 0 else "reward−",
                            "label": f"reward {_signed(f['reward'])}: {f['why']}"})
            if f["answer"]:
                out.append({"step": f["index"], "kind": "answer", "label": "the answer"})
        order = {"decisive": 0, "reward+": 1, "reward−": 1, "answer": 2}
        out.sort(key=lambda m: (order[m["kind"]], m["step"]))
        return out[:_impact.MAX_MARKS]

    grouped = _impact.cluster_steps(report, side, steps, facts, marks=marks)
    by_index = {f["index"]: f for f in facts}
    metric = ((report.get("shapley") or {}).get("metric")) if (report.get("shapley") or {}).get("available") else None
    for c in grouped["clusters"]:
        chunk = [by_index[i] for i in range(c["from"], c["to"] + 1) if i in by_index]
        c["score"] = round(sum(f["score"] for f in chunk), 4)
        c["impact"], c["kind"] = 0.0, "quiet"
        reasons = c["reasons"]
        reasons["reward"] = round(sum(f["reward"] for f in chunk), 4)
        reasons["positive"] = sum(1 for f in chunk if f["reward"] > 0)
        reasons["negative"] = sum(1 for f in chunk if f["reward"] < 0)
        reasons["credit"] = round(sum(f["credit"] for f in chunk), 4)
        reasons["fault_labels"] = sum(f["fault_labels"] for f in chunk)
        c["why"] = _cluster_why(reasons, metric)
    return grouped["clusters"]


def _cluster_why(reasons: dict, metric: Optional[str]) -> str:
    bits: list = []
    if reasons["positive"] or reasons["negative"]:
        bits.append(f"reward {_signed(reasons['reward'])} over {_plural(reasons['positive'], 'positive step')} "
                    f"and {reasons['negative']} negative")
    else:
        bits.append("no reward")
    if reasons.get("credit"):
        bits.append(f"credit {_credit_text(reasons['credit'], metric)}")
    if reasons.get("decisive"):
        bits.append("decisive step")
    if reasons.get("fault_labels"):
        bits.append(_plural(reasons["fault_labels"], "fault label"))
    if reasons.get("answer"):
        bits.append("the answer")
    return "; ".join(bits)


def _scale(runs: list) -> None:
    """Every cluster's impact against the largest score across ``runs``."""
    top = max((c["score"] for r in runs for c in r["clusters"]), default=0.0)
    for r in runs:
        for c in r["clusters"]:
            c["impact"] = round(c["score"] / top, 4) if top > 0 else 0.0
            c["kind"] = "hot" if c["impact"] >= _impact.HOT else "work" if c["impact"] >= _impact.WORK else "quiet"


# ---------------------------------------------------------------- narratives

def _run_narrative(run: dict) -> str:
    name = run["agent"]
    head = (f"{name}: return {_num(run['return'])} over {_plural(run['steps'], 'step')} ({run['source']}): "
            f"{_plural(run['negative'], 'negative step')}, {run['positive']} positive")
    parts = [head]
    penalties = [r for r in run["rewards"] if r["reward"] < 0]
    if penalties:
        worst = min(penalties, key=lambda r: (r["reward"], r["step"]))
        parts.append(f"the largest penalty at step {worst['step']} ({_num(worst['reward'])}, {worst['why']})")
    gains = [r for r in run["rewards"] if r["reward"] > 0]
    if gains:
        best = max(gains, key=lambda r: (r["reward"], -r["step"]))
        parts.append(f"the largest reward at step {best['step']} ({_signed(best['reward'])}, {best['why']})")
    credit = run["credit"]
    if credit.get("source"):
        carrying = [r for r in run["rewards"] if r.get("credit")]
        if carrying:
            by_agent: dict = {}
            for r in carrying:
                by_agent[r["agent"]] = by_agent.get(r["agent"], 0.0) + abs(r["credit"])
            where = max(by_agent, key=lambda k: (by_agent[k], k))
            parts.append(f"credit {_credit_text(credit['total'], credit['metric'])} on {_plural(len(carrying), 'step')}"
                         + (f", most in {where}" if len(by_agent) > 1 else f" in {where}"))
        else:
            parts.append("no step carries credit")
    return "; ".join(parts) + "."


def _parted_at(a: dict, b: dict) -> Optional[str]:
    ra, rb = a["rewards"], b["rewards"]
    for i in range(min(len(ra), len(rb))):
        if abs(ra[i]["cum"] - rb[i]["cum"]) >= 1.0:
            return f"the returns part at step {i} ({a['agent']} {_num(ra[i]['cum'])} against {_num(rb[i]['cum'])} so far)"
    if abs(a["return"] - b["return"]) >= 1.0:
        shorter = a if len(ra) < len(rb) else b
        return f"the returns part only after {shorter['agent']} answered at step {len(shorter['rewards']) - 1}"
    return "the returns never part by 1 or more"


def _pair_narrative(a: dict, b: dict, source: str, preference: Optional[dict]) -> str:
    parts: list = []
    if a["return"] > b["return"]:
        parts.append(f"{a['agent']} earned more: return {_num(a['return'])} against {_num(b['return'])} for {b['agent']} ({source})")
    elif b["return"] > a["return"]:
        parts.append(f"{b['agent']} earned more: return {_num(b['return'])} against {_num(a['return'])} for {a['agent']} ({source})")
    else:
        parts.append(f"both runs earned a return of {_num(a['return'])} ({source})")
    parted = _parted_at(a, b)
    if parted:
        parts.append(parted)
    if a["credit"].get("source") or b["credit"].get("source"):
        metric = a["credit"].get("metric") or b["credit"].get("metric")
        parts.append(f"credit splits {_credit_text(a['credit']['total'], metric)} to {a['agent']} and "
                     f"{_credit_text(b['credit']['total'], metric)} to {b['agent']}")
    else:
        parts.append("no credit allocated (the Shapley section is unavailable)")
    if preference:
        parts.append(f"preference: {preference['chosen']['agent']} chosen ({preference['chosen']['basis']}), "
                     f"{preference['rejected']['agent']} rejected")
    else:
        parts.append("no preference pair (no run passed while the other failed)")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- public

def rl_pair(report: dict, gamma: float = GAMMA) -> dict:
    """``report["rl"]``: both runs read as episodes on one scale, the
    preference pair, a narrative. Works on the report dict alone."""
    source = _source(report)
    labels = _labels_by_step(report)
    a = _run(report, "a", source, labels, gamma)
    b = _run(report, "b", source, labels, gamma)
    _scale([r for r in (a, b) if r["measurable"]])
    measurable = a["measurable"] and b["measurable"]
    preference = preference_pair(report) if measurable else None
    narrative = _pair_narrative(a, b, source, preference) if measurable else \
        " ".join(r["narrative"] for r in (a, b) if not r["measurable"])
    section = {"version": VERSION, "measurable": measurable, "source": source, "gamma": gamma,
               "a": a, "b": b, "preference": preference, "narrative": narrative}
    # the same audit at pair scale: two episodes cannot support a rank
    # correlation, but concentration, unearned reward, cost, tools and the
    # critic all still count over the pair's own steps
    section["audit"] = audit_pair(section, task_id=((report.get("task") or {}).get("id")),
                                  run_ids={s: (report.get(s) or {}).get("run_id") for s in ("a", "b")},
                                  gamma=gamma)
    return section


def rl_run_from_trace(traj: Trajectory, gamma: float = GAMMA, reading: Optional[dict] = None) -> dict:
    """One trajectory as an episode, with nothing but the trace: recorded
    rewards when any step carries one, else the shaped rule over the
    labels its own reading supports (roles, spent steps, errors — the
    pair-only labels need a report). Clusters are scaled to the run."""
    if reading is None:
        from .reasoning import read_trace
        reading = read_trace(traj)
    side = {"agent": traj.agent.to_dict(), "outcome": traj.outcome.to_dict(), "totals": traj.totals.to_dict(),
            "steps": [s.to_dict() for s in traj.steps]}
    report = {"task": traj.task.to_dict(), "a": side, "reading": {"a": reading}}
    source = _source(report)
    labels = _labels_by_step(report)
    run = _run(report, "a", source, labels, gamma)
    _scale([run] if run["measurable"] else [])
    run["task_id"] = traj.task.id
    run["run_id"] = traj.run_id
    return run


def _episode(run: dict, traj: Trajectory) -> dict:
    events: dict = {}
    for row in run["rewards"]:
        for l in row["labels"]:
            if l != "clean":
                events[l] = events.get(l, 0) + 1
    tools: dict = {}
    inputs = set()
    for st in traj.steps:
        if st.type in TOOLISH:
            tools[st.name or "?"] = tools.get(st.name or "?", 0) + 1
            inputs.add((st.name, st.input))
    return {
        "task_id": traj.task.id, "run_id": traj.run_id, "return": run["return"],
        "discounted_return": run["discounted_return"], "steps": run["steps"],
        "success": traj.outcome.success is True, "source": run["source"],
        "rewards": [r["reward"] for r in run["rewards"]],
        "cum": [r["cum"] for r in run["rewards"]],
        "values": [r["value"] for r in run["rewards"]],
        "advantages": [r["advantage"] if r["advantage"] is not None
                       else (round(r["discounted_to_go"] - r["value"], 4) if r["value"] is not None else None)
                       for r in run["rewards"]],
        "events": dict(sorted(events.items())),
        "tools": dict(sorted(tools.items())),
        "distinct_inputs": len(inputs),
        "seconds": run["seconds"],
    }


def mean_ci(values: list) -> tuple:
    """(mean, [lo, hi]) — a normal-approximation 95% interval with the
    sample standard deviation; the interval is None under two values."""
    n = len(values)
    if n == 0:
        return None, None
    mean = sum(values) / n
    if n < 2:
        return round(mean, 4), None
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    half = 1.96 * math.sqrt(var / n)
    return round(mean, 4), [round(mean - half, 4), round(mean + half, 4)]


def rl_aggregate(reports: list, trajectories: list, names: Optional[tuple] = None, gamma: float = GAMMA,
                 stats_metric: str = "return", stats_samples: int = RLSTATS_SAMPLES) -> dict:
    """``aggregate["rl"]`` for the runs layout: one episode per trace,
    per-agent mean return with a normal-approximation 95% interval (None
    under two episodes), per-task means and their delta in the aggregate's
    agent order, the preference pairs the pair reports carry, and
    ``stats`` — :func:`deepcompare.rlstats.rl_stats` over the same
    episodes, which is the section to read when the episodes are few."""
    agents: dict = {}
    order = list(names) if names else sorted({t.agent.name for t in trajectories})
    for name in order:
        agents[name] = {"episodes": [], "mean_return": None, "return_ci": None, "episodes_n": 0}
    sources = set()
    runs: list = []
    for traj in sorted(trajectories, key=lambda t: (t.agent.name, t.task.id, t.run_id)):
        run = rl_run_from_trace(traj, gamma)
        runs.append(run)
        sources.add(run["source"])
        agents.setdefault(traj.agent.name, {"episodes": [], "mean_return": None, "return_ci": None, "episodes_n": 0})
        agents[traj.agent.name]["episodes"].append(_episode(run, traj))
    for name, block in agents.items():
        returns = [e["return"] for e in block["episodes"]]
        block["mean_return"], block["return_ci"] = mean_ci(returns)
        block["episodes_n"] = len(returns)
    tasks: dict = {}
    for name, block in agents.items():
        for e in block["episodes"]:
            tasks.setdefault(e["task_id"], {})
            tasks[e["task_id"]].setdefault(name, {"returns": [], "mean_return": None})
            tasks[e["task_id"]][name]["returns"].append(e["return"])
    first, second = (order + [None, None])[:2]
    for tid, block in tasks.items():
        for name in list(block):
            rs = block[name]["returns"]
            block[name]["mean_return"] = round(sum(rs) / len(rs), 4) if rs else None
        ma = (block.get(first) or {}).get("mean_return") if first else None
        mb = (block.get(second) or {}).get("mean_return") if second else None
        delta = round(mb - ma, 4) if ma is not None and mb is not None else None
        block["delta"] = delta
        block["sign"] = 0 if delta is None or abs(delta) < 1e-9 else (1 if delta > 0 else -1)
    tasks = dict(sorted(tasks.items()))
    preferences: list = []
    pairs: list = []
    for rep in reports:
        rl = rep.get("rl") or {}
        if not rl.get("measurable"):
            continue
        tid = (rep.get("task") or {}).get("id")
        pairs.append({"task_id": tid, "source": rl.get("source"),
                      "returns": {rl["a"]["agent"]: rl["a"]["return"], rl["b"]["agent"]: rl["b"]["return"]},
                      "credit": {rl["a"]["agent"]: rl["a"]["credit"]["total"], rl["b"]["agent"]: rl["b"]["credit"]["total"]}})
        pref = rl.get("preference")
        if pref:
            chosen_side, rejected_side = pref["chosen"]["side"], pref["rejected"]["side"]
            preferences.append({"task_id": tid, "chosen": pref["chosen"]["agent"], "rejected": pref["rejected"]["agent"],
                                "basis": pref["chosen"]["basis"],
                                "margin": round(rl[chosen_side]["return"] - rl[rejected_side]["return"], 4),
                                "diverges_at": (pref.get("diverges_at") or {}).get("step")})
    pairs.sort(key=lambda p: str(p["task_id"]))
    preferences.sort(key=lambda p: str(p["task_id"]))
    source = "recorded" if sources == {"recorded"} else "shaped" if sources == {"shaped"} else "mixed" if sources else "shaped"
    block = {"version": VERSION, "gamma": gamma, "source": source, "agents": agents, "tasks": tasks,
             "pairs": pairs, "preferences": preferences,
             "narrative": _aggregate_narrative(agents, tasks, preferences, source, (first, second))}
    # the small-sample toolkit over the same episodes: IQM and friends with a
    # stratified bootstrap, the performance profiles, P(B > A). A mean with a
    # normal interval is the one thing a dozen episodes cannot support, so the
    # section that says what they *do* support ships beside it.
    block["stats"] = rl_stats(block, metric=stats_metric, samples=stats_samples)
    # is the signal itself trustworthy? the reward read against the outcome and
    # the critic read against what actually arrived, over the same episodes —
    # from the run readings, which are the only place the per-step labels and
    # the step's tool survive
    block["audit"] = audit_aggregate(runs, gamma=gamma)
    return block


def _aggregate_narrative(agents: dict, tasks: dict, preferences: list, source: str, order: tuple) -> str:
    parts: list = []
    for name, block in agents.items():
        if not block["episodes_n"]:
            continue
        ci = block["return_ci"]
        parts.append(f"{name}: mean return {_num(block['mean_return'])}"
                     + (f" [{_num(ci[0])}, {_num(ci[1])}]" if ci else " (no interval under 2 episodes)")
                     + f" over {_plural(block['episodes_n'], 'episode')}")
    first, second = order
    if first and second and tasks:
        up = sum(1 for t in tasks.values() if t.get("sign") == 1)
        down = sum(1 for t in tasks.values() if t.get("sign") == -1)
        parts.append(f"{second} earns more than {first} on {up} of {_plural(len(tasks), 'task')} and less on {down}")
    if preferences:
        chosen: dict = {}
        for p in preferences:
            chosen[p["chosen"]] = chosen.get(p["chosen"], 0) + 1
        parts.append("preference pairs: " + ", ".join(f"{n} chosen on {c}" for n, c in sorted(chosen.items())))
    parts.append(f"rewards {source}")
    return "; ".join(parts) + "."


__all__ = ["rl_pair", "rl_run_from_trace", "rl_aggregate", "rl_stats", "audit_aggregate", "audit_pair", "mean_ci", "GAMMA", "SHAPED_WEIGHTS", "ANSWER_REWARD",
           "DECISIVE_REWARD", "MILESTONE_REWARD", "FAULT_LABELS", "RLSTATS_SAMPLES", "VERSION"]
