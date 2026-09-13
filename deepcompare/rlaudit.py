"""Auditing the reward and the critic: is the signal trustworthy?

Everything else in this project reads a policy by what it earned. This
module asks the question one layer down, the one nobody asks until a
policy is already gamed: **the reward is a proxy — where does the proxy
disagree with the outcome, and does the value head actually predict what
is coming?**

Two halves, both counts over recorded steps and nothing else.

**Reward integrity** — six readings of the episodes alone:

* *reward/outcome disagreement* — every (passed, failed) pair of episodes
  the return orders the wrong way round. A failed episode that out-earned
  a passing one is the classic specification-gaming shape; a passing
  episode that scored below a failing one means the reward is missing the
  thing that mattered. Counted in three readings, because they answer
  different questions: **pooled** over every episode; **within each task**,
  which is the fair one, since returns on different tasks are not on a
  common scale; and over the **shaping alone** — the return with the last
  step's reward removed, within each task. That third reading is the one
  that matters when the answer step itself pays: a reward that carries the
  outcome in its terminal term agrees with the outcome by construction,
  and the question is whether the dense part a policy collects along the
  way agrees too. Findings come from the first reading that has any, each
  ranked by the size of the disagreement and naming the task, the run and
  the value. Beside them, the policy-level shape of the same question: per
  task, does the policy the reward prefers also pass more often?
* *rank agreement* — Spearman's rho between the return and the outcome
  over the episodes (:func:`spearman`, average ranks, pure stdlib). A
  binary outcome is one long pair of ties, so rho cannot reach 1 however
  good the reward is: the attainable **ceiling** (the same returns
  reordered to be perfectly consistent) is reported beside it, and rho on
  the shaping alone says how much of the agreement the outcome term is
  carrying by itself.
* *concentration* — the share of an episode's total absolute reward that
  its single largest step and its last step carry, the share of steps paid
  anything at all, and how many times its even share the largest step
  carries. The classification is stated with those numbers, never instead
  of them, so a reader can disagree with it: *terminal* when the last step
  is at least :data:`TERMINAL_SHARE` of the episode (the whole return is
  one number), *terminal-dominated* when the last step is the largest and
  carries at least :data:`DOMINANT_SHARE`, *peaked* when some other step
  does, *dense* otherwise. A terminal payoff with a dense cost term under
  it behaves nothing like an evenly shaped reward, and the two halves of
  this module meet there: when the outcome arrives as one number at the
  end, the critic is the only thing carrying it backwards.
* *unearned reward* — steps paid positively while carrying a label the
  analysis calls bad (:data:`BAD_LABELS`: an error, a repeat, a dead end,
  the fault entering), and steps punished while carrying a good one
  (:data:`GOOD_LABELS`). These are the individual places where the
  shaping argues with the reading.
* *reward per unit of cost* — return per step and per second per policy,
  so "better" can be told apart from "longer".
* *tool-mediated reward* — which tools the positive reward flows through
  and which the negative does, per policy. A policy earning all of its
  tool-mediated return through one tool is a fragile policy.

**Critic calibration** — when a step carries a ``value``, that value is a
prediction of the discounted return-to-go. The realised return-to-go is
recomputed here from the rewards at gamma (``G_t = r_t + γ G_{t+1}``) and
the **residual** is ``value − G``. From the residuals: the mean signed
error (the bias — a positive mean is an optimistic critic), the mean
absolute error, the RMSE, and the **explained variance**
``1 − Var(residual)/Var(actual)``, which is the standard critic-health
number and goes *negative* when the critic is worse than predicting the
mean of the returns-to-go. Calibration is then broken out by decile of
predicted value, so the reader sees *where* the critic is wrong and not
only how much. Where a step records an ``advantage``, it is checked
against the definition the trace claims (``G − value``, the definition
``docs/RL.md`` states) and every episode that fails is reported: a silent
advantage bug ruins a training run and nothing else in the pipeline looks
at it.

Limits, stated once and repeated in the output:

* Every finding is a **signal to investigate, not a proven defect**. A
  failed episode with a high return may be a hard task, not a gamed one.
* Disagreement, rank agreement and calibration are all sample-size bound.
  Under :data:`SMALL_SAMPLE` episodes the correlation is a direction, not
  an estimate, and the output says so in its own words.
* Nothing here is simulated. A check with no evidence returns
  ``measurable: False`` and a reason, never a number.
* The per-step labels and the step's tool come from the run reading; an
  audit built from the aggregate's episode arrays alone has neither, and
  the affected checks report their coverage rather than guessing.
"""

from __future__ import annotations

import math
from typing import Optional

from . import impact as _impact

VERSION = 1
DEFAULT_GAMMA = 0.99
#: step labels the analysis already treats as bad (they are the negatively
#: weighted half of ``rl.SHAPED_WEIGHTS`` plus ``fault_carried``)
BAD_LABELS = ("dead_end", "error", "fault_carried", "fault_enters", "invented_argument",
              "no_information", "repeat", "spent_after_basis", "wrong_answer")
#: the labels the analysis treats as earning their reward
GOOD_LABELS = ("fed_answer",)
#: step types that count as a tool call, shared with the impact layer
TOOLISH = _impact.TOOLISH
#: rows listed per finding list
TOP = 12
#: |advantage − (G − value)| above this is an inconsistency, not rounding
ADV_TOL = 1e-3
#: below this many episodes a rank correlation is a direction, not an estimate
SMALL_SAMPLE = 20
#: |last step| / Σ|reward| at or above this: the whole return is one number
TERMINAL_SHARE = 0.9
#: one step's share of Σ|reward| at or above this: that step dominates the episode
DOMINANT_SHARE = 1.0 / 3.0
#: calibration bins by predicted value
DECILES = 10
#: bins in the residual marginal
RESIDUAL_BINS = 11
#: what every finding is, said once per list
SIGNAL = "signal"


# ---------------------------------------------------------------- numbers

def _r(v: Optional[float], places: int = 4) -> Optional[float]:
    return None if v is None else round(float(v), places)


def _num(v: float) -> str:
    """A number as text: integers plain, else two decimals, real minus."""
    if abs(v - round(v)) < 1e-9:
        text = f"{int(round(v))}"
    else:
        text = f"{v:.2f}"
    return text.replace("-", "−")


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _cap(text: str) -> str:
    """A narrative is a sentence, so it starts with a capital."""
    return text[:1].upper() + text[1:] if text else text


def _mean(values: list) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _pvar(values: list) -> Optional[float]:
    """Population variance — the denominator an explained-variance figure
    wants, since both sides are the same sample."""
    if not values:
        return None
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def rank_average(values: list) -> list:
    """1-based ranks with ties averaged, returned in the input's order."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        share = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = share
        i = j + 1
    return ranks


def spearman(xs: list, ys: list) -> Optional[float]:
    """Spearman's rho — Pearson on the average ranks. None under two pairs,
    on a length mismatch, or when either side is constant (a constant has
    no order for the other to agree with)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = rank_average(list(xs)), rank_average(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / math.sqrt(sxx * syy), 4)


def discounted_to_go(rewards: list, gamma: float = DEFAULT_GAMMA) -> list:
    """``G_t = r_t + γ G_{t+1}`` over the recorded rewards, in step order."""
    out = [0.0] * len(rewards)
    acc = 0.0
    for i in range(len(rewards) - 1, -1, -1):
        acc = rewards[i] + gamma * acc
        out[i] = acc
    return out


# ---------------------------------------------------------------- adapters

def _row(step: Optional[int], reward, labels, value, advantage, kind, name) -> dict:
    def number(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    return {"step": step, "reward": number(reward) or 0.0,
            "labels": None if labels is None else [str(l) for l in labels],
            "value": number(value), "advantage": number(advantage),
            "kind": None if kind is None else str(kind), "name": None if name is None else str(name)}


def _episode(agent, task_id, run_id, success, rows, seconds=None, side=None,
             total=None, discounted=None) -> dict:
    rewards = [r["reward"] for r in rows]
    return {"agent": str(agent), "task_id": None if task_id is None else str(task_id),
            "run_id": None if run_id is None else str(run_id), "side": side,
            "success": success if isinstance(success, bool) else None,
            "return": _r(sum(rewards) if total is None else float(total)),
            "discounted_return": _r(discounted) if discounted is not None else None,
            "steps": len(rows), "seconds": _r(seconds) if seconds is not None else None,
            "rows": rows}


def _rows_from_run(run: dict) -> list:
    out = []
    for i, raw in enumerate(run.get("rewards") or []):
        if not isinstance(raw, dict):
            continue
        step = raw.get("step") if isinstance(raw.get("step"), int) else i
        out.append(_row(step, raw.get("reward"), raw.get("labels"), raw.get("value"),
                        raw.get("advantage"), raw.get("kind"), raw.get("name")))
    return out


def episodes_from_runs(runs: list) -> list:
    """Audit episodes from :func:`deepcompare.rl.rl_run_from_trace` outputs,
    one per trace. This is the full-detail source: the rows carry the
    step's labels, its tool and its recorded value and advantage, so every
    check below has its evidence."""
    out = []
    for run in runs:
        if not isinstance(run, dict) or not run.get("measurable"):
            continue
        out.append(_episode(run.get("agent"), run.get("task_id"), run.get("run_id"),
                            run.get("success"), _rows_from_run(run), run.get("seconds"),
                            total=run.get("return"), discounted=run.get("discounted_return")))
    out.sort(key=lambda e: (e["agent"], e["task_id"] or "", e["run_id"] or ""))
    return out


def episodes_from_pair(rl: dict, task_id: Optional[str] = None, run_ids: Optional[dict] = None) -> list:
    """The two runs of ``report["rl"]`` as audit episodes, keeping the side
    so a page can click a finding through to the step it names."""
    out = []
    for side in ("a", "b"):
        run = (rl or {}).get(side) or {}
        if not run.get("measurable"):
            continue
        out.append(_episode(run.get("agent"), task_id, (run_ids or {}).get(side), run.get("success"),
                            _rows_from_run(run), run.get("seconds"), side=side,
                            total=run.get("return"), discounted=run.get("discounted_return")))
    return out


def episodes_from_aggregate(rl: dict) -> list:
    """Audit episodes from ``aggregate["rl"]`` alone — the per-step arrays
    without the labels or the tool names, so the unearned-reward and
    tool-mediated checks report zero coverage rather than guessing. Use
    :func:`episodes_from_runs` when the runs are still in hand."""
    out = []
    agents = (rl or {}).get("agents") or {}
    for name in sorted(agents):
        for ep in (agents[name] or {}).get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            rewards = ep.get("rewards") or []
            values = ep.get("values") or []
            advs = ep.get("advantages") or []
            rows = [_row(i, rewards[i] if i < len(rewards) else 0.0, None,
                         values[i] if i < len(values) else None,
                         advs[i] if i < len(advs) else None, None, None)
                    for i in range(len(rewards))]
            out.append(_episode(name, ep.get("task_id"), ep.get("run_id"), ep.get("success"), rows,
                                ep.get("seconds"), total=ep.get("return"),
                                discounted=ep.get("discounted_return")))
    out.sort(key=lambda e: (e["agent"], e["task_id"] or "", e["run_id"] or ""))
    return out


def _where(ep: dict) -> dict:
    """The evidence every finding carries: which policy, task and run."""
    return {"agent": ep["agent"], "task_id": ep["task_id"], "run_id": ep["run_id"], "side": ep["side"]}


# ------------------------------------------------------- reward integrity

def _inversions(passed: list, failed: list, value) -> dict:
    """How often the measure puts a failed episode above a passing one, over
    every (passing, failing) pair there is."""
    pairs_n = len(passed) * len(failed)
    if not pairs_n:
        return {"pairs_n": 0, "inversions": 0, "ties": 0, "inversion_rate": None, "separation": None}
    inv = sum(1 for p in passed for f in failed if value(f) > value(p))
    ties = sum(1 for p in passed for f in failed if value(f) == value(p))
    return {"pairs_n": pairs_n, "inversions": inv, "ties": ties,
            "inversion_rate": _r(inv / pairs_n),
            "separation": _r(min(value(p) for p in passed) - max(value(f) for f in failed))}


def _shaping_return(ep: dict) -> float:
    """The return with the last step's reward taken out — the part of the
    reward a policy collects along the way, with the outcome term removed.
    When the answer itself pays, the full return agrees with the outcome by
    construction; this is the measure that says whether the *shaping*
    agrees too, and it is where gaming shows first."""
    rows = ep["rows"]
    return _r(ep["return"] - (rows[-1]["reward"] if rows else 0.0))


def _scope(episodes: list, value, basis: str, per_task: bool) -> dict:
    """One reading of the disagreement: pooled over every episode, or
    summed within each task (returns on different tasks are not on a
    common scale, so the within-task reading is the fair one)."""
    if not per_task:
        passed = [e for e in episodes if e["success"] is True]
        failed = [e for e in episodes if e["success"] is False]
        return dict(_inversions(passed, failed, value), basis=basis, tasks={})
    tasks: dict = {}
    total = {"pairs_n": 0, "inversions": 0, "ties": 0}
    seps = []
    for tid in sorted({e["task_id"] or "" for e in episodes}):
        mine = [e for e in episodes if (e["task_id"] or "") == tid]
        passed = [e for e in mine if e["success"] is True]
        failed = [e for e in mine if e["success"] is False]
        block = _inversions(passed, failed, value)
        block["passed"], block["failed"] = len(passed), len(failed)
        tasks[tid] = block
        for k in ("pairs_n", "inversions", "ties"):
            total[k] += block[k]
        if block["separation"] is not None:
            seps.append(block["separation"])
    return {"basis": basis, "pairs_n": total["pairs_n"], "inversions": total["inversions"], "ties": total["ties"],
            "inversion_rate": _r(total["inversions"] / total["pairs_n"]) if total["pairs_n"] else None,
            "separation": _r(min(seps)) if seps else None, "tasks": tasks}


def _findings(episodes: list, value, measure: str, per_task: bool) -> list:
    """One row per episode the measure puts on the wrong side, carrying the
    task, the run and the size of the disagreement."""
    out = []
    groups = sorted({(e["task_id"] or "") for e in episodes}) if per_task else [None]
    for tid in groups:
        mine = [e for e in episodes if tid is None or (e["task_id"] or "") == tid]
        passed = [e for e in mine if e["success"] is True]
        failed = [e for e in mine if e["success"] is False]
        if not passed or not failed:
            continue
        lowest_pass = min(value(p) for p in passed)
        highest_fail = max(value(f) for f in failed)
        for f in failed:
            above = sum(1 for p in passed if value(p) < value(f))
            if not above:
                continue
            out.append(dict(_where(f), kind="high_return_failed", status=SIGNAL, measure=measure,
                            value=_r(value(f)), steps=f["steps"], success=False, outranks=above,
                            gap=_r(value(f) - lowest_pass), scope="task" if per_task else "batch",
                            why=f"failed, yet out-earned {_plural(above, 'passing episode')} on the same task "
                                f"({measure} {_num(value(f))} against a lowest passing {_num(lowest_pass)})"))
        for p in passed:
            below = sum(1 for f in failed if value(f) > value(p))
            if not below:
                continue
            out.append(dict(_where(p), kind="low_return_passed", status=SIGNAL, measure=measure,
                            value=_r(value(p)), steps=p["steps"], success=True, outranks=below,
                            gap=_r(highest_fail - value(p)), scope="task" if per_task else "batch",
                            why=f"passed, yet was out-earned by {_plural(below, 'failed episode')} on the same task "
                                f"({measure} {_num(value(p))} against a highest failing {_num(highest_fail)})"))
    out.sort(key=lambda f: (-f["gap"], -f["outranks"], f["agent"], f["task_id"] or "", f["run_id"] or ""))
    return out


def _by_policy(episodes: list) -> list:
    """Per task, does the policy the reward prefers also pass more often?
    The policy-level shape of the same question: a reward that ranks the
    cheaper policy above the correct one is gamed, however its episodes
    happen to be ordered."""
    rows = []
    names = sorted({e["agent"] for e in episodes})
    if len(names) != 2:
        return rows
    for tid in sorted({e["task_id"] or "" for e in episodes}):
        cell = {}
        for name in names:
            mine = [e for e in episodes if (e["task_id"] or "") == tid and e["agent"] == name
                    and e["success"] is not None]
            if not mine:
                cell = {}
                break
            cell[name] = {"episodes_n": len(mine), "mean_return": _r(_mean([e["return"] for e in mine])),
                          "passed": sum(1 for e in mine if e["success"]),
                          "pass_rate": _r(sum(1 for e in mine if e["success"]) / len(mine))}
        if not cell:
            continue
        x, y = names
        d_return = cell[y]["mean_return"] - cell[x]["mean_return"]
        d_pass = cell[y]["pass_rate"] - cell[x]["pass_rate"]
        disagrees = (d_return > 0 and d_pass < 0) or (d_return < 0 and d_pass > 0)
        row = {"task_id": tid, "policies": cell, "delta_mean_return": _r(d_return), "delta_pass_rate": _r(d_pass),
               "disagrees": disagrees, "status": SIGNAL if disagrees else None}
        if disagrees:
            better, worse = (y, x) if d_return > 0 else (x, y)
            row["why"] = (f"the reward prefers {better} on {tid} (mean return {_num(cell[better]['mean_return'])} "
                          f"against {_num(cell[worse]['mean_return'])}) while {worse} passes more often "
                          f"({cell[worse]['passed']}/{cell[worse]['episodes_n']} against "
                          f"{cell[better]['passed']}/{cell[better]['episodes_n']})")
        rows.append(row)
    return rows


def _disagreement(episodes: list) -> dict:
    passed = [e for e in episodes if e["success"] is True]
    failed = [e for e in episodes if e["success"] is False]
    unknown = len(episodes) - len(passed) - len(failed)
    if not passed or not failed:
        side = "none passed" if not passed else "none failed"
        return {"measurable": False,
                "reason": f"{side} of {_plural(len(episodes), 'episode')}, so the return has no outcome to disagree with",
                "passed": len(passed), "failed": len(failed), "unknown": unknown, "scopes": {},
                "findings": [], "findings_basis": None, "findings_n": 0, "by_policy": [], "note": ""}
    full = lambda e: e["return"]                                        # noqa: E731
    scopes = {
        "pooled": _scope(episodes, full, "the return, every episode against every other", False),
        "by_task": _scope(episodes, full, "the return, within each task (returns on different tasks are not "
                                          "on a common scale)", True),
        "shaping": _scope(episodes, _shaping_return, "the return with the last step removed, within each task", True),
    }
    for basis, value, measure, per_task in (("by_task", full, "return", True),
                                            ("pooled", full, "return", False),
                                            ("shaping", _shaping_return, "shaping return", True)):
        rows = _findings(episodes, value, measure, per_task) if scopes[basis]["inversions"] else []
        if rows:
            findings, findings_basis = rows, basis
            break
    else:
        findings, findings_basis = [], None
    flagged = sorted({f"{f['agent']}|{f['task_id'] or ''}|{f['run_id'] or ''}" for f in findings})
    by_policy = _by_policy(episodes)
    gamed = [r for r in by_policy if r["disagrees"]]
    bits = []
    if scopes["by_task"]["inversions"]:
        bits.append(f"{_plural(scopes['by_task']['inversions'], 'pair')} of {scopes['by_task']['pairs_n']} within a "
                    "task put a failed episode above a passing one")
    else:
        bits.append(f"within every task the return separates the outcomes cleanly — no failed episode out-earns a "
                    f"passing one in {_plural(scopes['by_task']['pairs_n'], 'ordered pair')}")
    sh = scopes["shaping"]
    if sh["pairs_n"]:
        if sh["inversions"]:
            bits.append(f"but take the last step out and the dense shaping that remains gets "
                        f"{sh['inversions']} of {sh['pairs_n']} the wrong way round, which is where a policy would "
                        "collect return without passing")
        else:
            bits.append("and the shaping alone, with the last step taken out, orders them the same way")
    if gamed:
        bits.append(f"on {_plural(len(gamed), 'task')} the reward prefers the policy that passes less often")
    bits.append("each finding is a signal to investigate, not a proven defect")
    return {"measurable": True, "reason": None, "passed": len(passed), "failed": len(failed), "unknown": unknown,
            "scopes": scopes, "findings": findings[:TOP], "findings_n": len(findings), "flagged": flagged,
            "findings_basis": findings_basis, "by_policy": by_policy, "note": "; ".join(bits)}


def _sample_note(n: int) -> str:
    if n < SMALL_SAMPLE:
        return (f"{_plural(n, 'episode')} is under {SMALL_SAMPLE}: read this as a direction, not an estimate — "
                "one episode moves it visibly and no interval is quoted for it")
    return f"over {_plural(n, 'episode')}, which is enough to read as a direction but not to defend to three decimals"


def _ceiling(values: list, outcomes: list) -> Optional[float]:
    """The highest rho these outcomes allow: the same returns, reordered so
    that every passing episode is above every failing one. A binary outcome
    is one long pair of ties, so even a perfect reward cannot reach 1."""
    ideal_y = sorted(outcomes)
    ideal_x = sorted(values)
    return spearman(ideal_x, ideal_y)


def _rank_agreement(episodes: list) -> dict:
    scored = [e for e in episodes if e["success"] is not None]
    if len(scored) < 3:
        # over two points a rank correlation is ±1 whatever the numbers are
        reason = (f"only {_plural(len(scored), 'episode')} carries an outcome"
                  if len(scored) < 2 else
                  "two episodes: a rank correlation over two points is ±1 whatever the returns are, so there is "
                  "nothing here to report")
        return {"measurable": False, "reason": reason,
                "spearman": None, "spearman_shaping": None, "n": len(scored), "ceiling": None,
                "per_agent": {}, "note": ""}
    outcomes = [1.0 if e["success"] else 0.0 for e in scored]
    rho = spearman([e["return"] for e in scored], outcomes)
    if rho is None:
        return {"measurable": False, "reason": "every episode has the same outcome, so there is no order to agree with",
                "spearman": None, "spearman_shaping": None, "n": len(scored), "ceiling": None,
                "per_agent": {}, "note": ""}
    ceiling = _ceiling([e["return"] for e in scored], outcomes)
    shaping = spearman([_shaping_return(e) for e in scored], outcomes)
    per_agent: dict = {}
    for name in sorted({e["agent"] for e in scored}):
        mine = [e for e in scored if e["agent"] == name]
        ys = [1.0 if e["success"] else 0.0 for e in mine]
        per_agent[name] = {"n": len(mine), "spearman": spearman([e["return"] for e in mine], ys),
                           "ceiling": _ceiling([e["return"] for e in mine], ys),
                           "spearman_shaping": spearman([_shaping_return(e) for e in mine], ys)}
    note = (f"rho {_num(rho)} against a ceiling of {_num(ceiling)} — the outcome is binary, so its ties hold rho "
            f"below 1 even when the return orders the episodes perfectly; {_sample_note(len(scored))}")
    if shaping is not None:
        note += (f". With the last step taken out of each return, rho falls to {_num(shaping)}: that is how much of "
                 "the agreement the outcome term is carrying on its own")
    return {"measurable": True, "reason": None, "spearman": rho, "spearman_shaping": shaping, "n": len(scored),
            "ceiling": ceiling, "per_agent": per_agent, "note": note}


def _concentration(episodes: list) -> dict:
    rows, kinds = [], {"terminal": 0, "terminal_dominated": 0, "peaked": 0, "dense": 0, "empty": 0}
    for ep in episodes:
        rewards = [r["reward"] for r in ep["rows"]]
        total_abs = sum(abs(v) for v in rewards)
        nonzero = sum(1 for v in rewards if v != 0)
        if not rewards or total_abs <= 0:
            kinds["empty"] += 1
            rows.append(dict(_where(ep), steps=ep["steps"], total_abs=0.0, largest_step=None, largest=0.0,
                             largest_share=None, last_share=None, nonzero=0, peakedness=None,
                             last_is_largest=None, kind="empty"))
            continue
        best = max(range(len(rewards)), key=lambda i: (abs(rewards[i]), -i))
        last_share = abs(rewards[-1]) / total_abs
        largest_share = abs(rewards[best]) / total_abs
        last_is_largest = best == len(rewards) - 1
        if last_share >= TERMINAL_SHARE:
            kind = "terminal"
        elif last_is_largest and last_share >= DOMINANT_SHARE:
            kind = "terminal_dominated"
        elif largest_share >= DOMINANT_SHARE:
            kind = "peaked"
        else:
            kind = "dense"
        kinds[kind] += 1
        rows.append(dict(_where(ep), steps=ep["steps"], total_abs=_r(total_abs),
                         largest_step=ep["rows"][best]["step"], largest=_r(rewards[best]),
                         largest_share=_r(largest_share), last_share=_r(last_share), nonzero=nonzero,
                         # how many times its even share the largest step carries, over the steps that were paid at all
                         peakedness=_r(largest_share * nonzero, 2), last_is_largest=last_is_largest, kind=kind))
    measured = [r for r in rows if r["largest_share"] is not None]
    if not measured:
        return {"measurable": False, "reason": "no episode was paid any reward", "episodes": rows,
                "mean_largest_share": None, "mean_last_share": None, "mean_peakedness": None,
                "mean_paid_share": None, "kinds": kinds, "kind": None, "note": ""}
    mean_largest = _mean([r["largest_share"] for r in measured])
    mean_last = _mean([r["last_share"] for r in measured])
    mean_peak = _mean([r["peakedness"] for r in measured])
    mean_paid = _mean([r["nonzero"] / r["steps"] for r in measured if r["steps"]])
    order = ("terminal", "terminal_dominated", "peaked", "dense")
    kind = max(order, key=lambda k: (kinds[k], -order.index(k)))
    said = {
        "terminal": "this is a sparse, terminal reward: the whole return is one number paid at the end, so every "
                    "step before it is credited only through the discount and the critic is the only thing "
                    "carrying signal back",
        "terminal_dominated": "this is a terminal payoff with a dense cost term underneath it: the outcome arrives "
                              "as one large number at the last step and every step before it pays only a small "
                              "cost, so the return agrees with the outcome mostly through that one number",
        "peaked": "this reward is peaked on a step that is not the last: one step carries a third or more of the "
                  "episode, so the shaping around it is close to noise against that step",
        "dense": "this is a dense reward: no single step carries a third of it, so the shaping in between is doing "
                 "real work and a change to it will move the return",
    }[kind]
    note = (f"the largest step carries {_num(100 * mean_largest)}% of an episode's total absolute reward on average "
            f"({_num(mean_peak)}× what an even spread over its paid steps would give) and the last step "
            f"{_num(100 * mean_last)}%, with {_num(100 * mean_paid)}% of steps paid anything at all; {said}")
    return {"measurable": True, "reason": None, "episodes": rows, "mean_largest_share": _r(mean_largest),
            "mean_last_share": _r(mean_last), "mean_peakedness": _r(mean_peak, 2), "mean_paid_share": _r(mean_paid),
            "kinds": kinds, "kind": kind, "note": note}


def _spread(rows: list, per_place: int = 2, limit: int = TOP) -> list:
    """The largest rows, at most ``per_place`` from any one policy and task.
    A dozen rows from the same run says far less than a dozen rows from a
    dozen runs, and the cap is stated wherever the list is shown."""
    seen: dict = {}
    out = []
    for row in rows:
        key = (row["agent"], row["task_id"] or "")
        if seen.get(key, 0) >= per_place:
            continue
        seen[key] = seen.get(key, 0) + 1
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _unearned(episodes: list) -> dict:
    labelled_eps = [e for e in episodes if any(r["labels"] is not None for r in e["rows"])]
    labelled_steps = sum(1 for e in episodes for r in e["rows"] if r["labels"] is not None)
    if not labelled_steps:
        return {"measurable": False,
                "reason": "no step carries a label, so there is nothing for the reward to argue with",
                "episodes_covered": 0, "episodes_n": len(episodes), "steps_labelled": 0,
                "positive_while_bad": 0, "negative_while_good": 0, "by_label": {}, "rows": [], "note": ""}
    rows = []
    for ep in episodes:
        for r in ep["rows"]:
            labels = r["labels"]
            if not labels:
                continue
            bad = sorted(set(labels) & set(BAD_LABELS))
            good = sorted(set(labels) & set(GOOD_LABELS))
            if r["reward"] > 0 and bad:
                kind, why = "positive_while_bad", f"paid {_num(r['reward'])} while labelled {', '.join(bad)}"
            elif r["reward"] < 0 and good and not bad:
                kind, why = "negative_while_good", f"paid {_num(r['reward'])} while labelled {', '.join(good)}"
            else:
                continue
            rows.append(dict(_where(ep), kind=kind, status=SIGNAL, step=r["step"], reward=_r(r["reward"]),
                             labels=list(labels), step_kind=r["kind"], name=r["name"], why=why))
    rows.sort(key=lambda r: (-abs(r["reward"]), r["agent"], r["task_id"] or "", r["run_id"] or "", r["step"] or 0))
    pos = sum(1 for r in rows if r["kind"] == "positive_while_bad")
    neg = sum(1 for r in rows if r["kind"] == "negative_while_good")
    # the overview the rows are details of: which label, whose, how many
    # steps and how much reward — a dozen identical rows say far less
    by_label: dict = {}
    for r in rows:
        for label in sorted(set(r["labels"]) & set(BAD_LABELS if r["kind"] == "positive_while_bad" else GOOD_LABELS)):
            cell = by_label.setdefault(label, {})
            agent = cell.setdefault(r["agent"], {"steps": 0, "reward": 0.0, "kind": r["kind"]})
            agent["steps"] += 1
            agent["reward"] = _r(agent["reward"] + r["reward"])
    by_label = {label: dict(sorted(by_label[label].items())) for label in sorted(by_label)}
    if pos or neg:
        note = (f"{_plural(pos, 'step')} of {_plural(labelled_steps, 'labelled step')} "
                f"{'was' if pos == 1 else 'were'} paid while labelled bad, and {neg} "
                f"{'was' if neg == 1 else 'were'} punished while labelled good — each is a place the shaping and "
                "the reading disagree, and a signal to investigate rather than a proven defect")
    else:
        note = (f"no step of the {_plural(labelled_steps, 'labelled step')} was paid while labelled bad or punished "
                "while labelled good: the shaping and the reading agree everywhere they both speak")
    return {"measurable": True, "reason": None, "episodes_covered": len(labelled_eps), "episodes_n": len(episodes),
            "steps_labelled": labelled_steps, "positive_while_bad": pos, "negative_while_good": neg,
            "by_label": by_label, "rows": _spread(rows), "note": note}


def _cost(episodes: list) -> dict:
    per: dict = {}
    for name in sorted({e["agent"] for e in episodes}):
        mine = [e for e in episodes if e["agent"] == name]
        steps = sum(e["steps"] for e in mine)
        seconds = sum(e["seconds"] or 0.0 for e in mine)
        total = sum(e["return"] for e in mine)
        per[name] = {"episodes_n": len(mine), "return_total": _r(total), "steps_total": steps,
                     "seconds_total": _r(seconds), "mean_return": _r(total / len(mine)) if mine else None,
                     "return_per_step": _r(total / steps) if steps else None,
                     "return_per_second": _r(total / seconds) if seconds > 0 else None,
                     "mean_steps": _r(steps / len(mine)) if mine else None}
    if not per:
        return {"measurable": False, "reason": "no episodes", "per_agent": {}, "note": ""}
    ranked = sorted(per.items(), key=lambda kv: (-(kv[1]["return_per_step"] or 0.0), kv[0]))
    bits = [f"{n} earns {_num(b['return_per_step'])} per step"
            + (f" and {_num(b['return_per_second'])} per second" if b["return_per_second"] is not None else "")
            for n, b in ranked if b["return_per_step"] is not None]
    note = "; ".join(bits) + (" — return per step separates a better policy from a longer one" if bits else "")
    return {"measurable": True, "reason": None, "per_agent": per, "note": note}


def _tools(episodes: list) -> dict:
    per: dict = {}
    named = 0
    for ep in episodes:
        for r in ep["rows"]:
            if r["kind"] not in TOOLISH or not r["name"]:
                continue
            named += 1
            block = per.setdefault(ep["agent"], {})
            cell = block.setdefault(r["name"], {"calls": 0, "positive": 0.0, "negative": 0.0, "paid": 0, "punished": 0})
            cell["calls"] += 1
            if r["reward"] > 0:
                cell["positive"] += r["reward"]
                cell["paid"] += 1
            elif r["reward"] < 0:
                cell["negative"] += r["reward"]
                cell["punished"] += 1
    if not named:
        return {"measurable": False, "reason": "no step names a tool, so no reward can be traced through one",
                "per_agent": {}, "findings": [], "note": ""}
    out: dict = {}
    findings = []
    for name in sorted(per):
        tools = {}
        for tool in sorted(per[name]):
            cell = per[name][tool]
            tools[tool] = {"calls": cell["calls"], "positive": _r(cell["positive"]), "negative": _r(cell["negative"]),
                           "net": _r(cell["positive"] + cell["negative"]), "paid": cell["paid"],
                           "punished": cell["punished"]}
        pos_total = sum(t["positive"] for t in tools.values())
        neg_total = sum(-t["negative"] for t in tools.values())
        for tool, t in tools.items():
            t["positive_share"] = _r(t["positive"] / pos_total) if pos_total > 0 else None
            t["negative_share"] = _r(-t["negative"] / neg_total) if neg_total > 0 else None
        top = max(tools, key=lambda t: (tools[t]["positive"], t)) if pos_total > 0 else None
        out[name] = {"tools": tools, "positive_total": _r(pos_total), "negative_total": _r(-neg_total),
                     "top_tool": top, "top_share": tools[top]["positive_share"] if top else None,
                     "tools_n": len(tools)}
        if top and tools[top]["positive_share"] is not None and tools[top]["positive_share"] >= 0.8:
            findings.append({"agent": name, "kind": "single_tool_reward", "status": SIGNAL, "tool": top,
                             "share": tools[top]["positive_share"], "calls": tools[top]["calls"],
                             "why": f"{_num(100 * tools[top]['positive_share'])}% of {name}'s tool-mediated positive "
                                    f"reward flows through {top}: a policy earning through one tool is a fragile policy"})
    findings.sort(key=lambda f: (-f["share"], f["agent"]))
    bits = [f"{n} earns most through {b['top_tool']}" for n, b in sorted(out.items()) if b["top_tool"]]
    return {"measurable": True, "reason": None, "per_agent": out, "findings": findings,
            "note": "; ".join(bits) + (f"; {_plural(named, 'tool step')} carried a reward" if bits else "")}


def _episode_table(episodes: list, flagged: list) -> list:
    """One row per episode, both measures side by side and whether the
    disagreement check flagged it — the evidence a page plots, so the chart
    and the finding list can never drift apart."""
    marked = set(flagged or [])
    rows = []
    for ep in episodes:
        key = f"{ep['agent']}|{ep['task_id'] or ''}|{ep['run_id'] or ''}"
        rows.append(dict(_where(ep), success=ep["success"], steps=ep["steps"], seconds=ep["seconds"],
                         **{"return": ep["return"]}, shaping_return=_shaping_return(ep),
                         last_reward=_r(ep["rows"][-1]["reward"]) if ep["rows"] else 0.0,
                         flagged=key in marked))
    return rows


def reward_integrity(episodes: list, *, gamma: float = DEFAULT_GAMMA) -> dict:
    """The six reward readings over normalised audit episodes. Every count
    is over recorded steps; every finding names the task, the run and the
    step, and is a signal to investigate, not a proven defect."""
    if not episodes:
        return {"measurable": False, "reason": "no episodes to read", "episodes_n": 0,
                "episodes": [], "disagreement": {}, "rank_agreement": {}, "concentration": {},
                "unearned": {}, "cost": {}, "tools": {}, "narrative": "No episode carries a reward."}
    disagreement = _disagreement(episodes)
    block = {"measurable": True, "reason": None, "episodes_n": len(episodes),
             "episodes": _episode_table(episodes, disagreement.get("flagged")),
             "disagreement": disagreement, "rank_agreement": _rank_agreement(episodes),
             "concentration": _concentration(episodes), "unearned": _unearned(episodes),
             "cost": _cost(episodes), "tools": _tools(episodes)}
    block["narrative"] = _reward_narrative(block)
    return block


def _reward_narrative(block: dict) -> str:
    dis, rank, con, un = block["disagreement"], block["rank_agreement"], block["concentration"], block["unearned"]
    parts = []
    if dis.get("measurable"):
        task = dis["scopes"]["by_task"]
        shaping = dis["scopes"]["shaping"]
        lead = (f"the return and the outcome disagree on {task['inversions']} of "
                f"{_plural(task['pairs_n'], 'ordered pair')} within a task"
                if task["inversions"] else
                f"the return and the outcome never disagree within a task, over "
                f"{_plural(task['pairs_n'], 'ordered pair')}")
        if shaping["pairs_n"] and shaping["inversions"]:
            lead += (f", but with the last step removed the shaping alone gets {shaping['inversions']} of them "
                     "the wrong way round")
        parts.append(lead)
    else:
        parts.append(dis.get("reason") or "the outcomes cannot be compared")
    if rank.get("measurable"):
        parts.append(f"rank correlation {_num(rank['spearman'])} against a ceiling of {_num(rank['ceiling'])} "
                     f"over {_plural(rank['n'], 'episode')}"
                     + (f", {_num(rank['spearman_shaping'])} on the shaping alone"
                        if rank.get("spearman_shaping") is not None else ""))
    if con.get("measurable"):
        parts.append(f"the reward is {con['kind'].replace('_', '-')}: the largest step carries "
                     f"{_num(100 * con['mean_largest_share'])}% and the last step "
                     f"{_num(100 * con['mean_last_share'])}% of an episode's absolute reward on average, "
                     f"over {_num(100 * con['mean_paid_share'])}% of steps paid anything at all")
    if un.get("measurable"):
        if un["positive_while_bad"] or un["negative_while_good"]:
            parts.append(f"{_plural(un['positive_while_bad'], 'step')} paid while labelled bad and "
                         f"{un['negative_while_good']} punished while labelled good, "
                         f"of {_plural(un['steps_labelled'], 'labelled step')}")
        else:
            parts.append(f"no step of {_plural(un['steps_labelled'], 'labelled step')} was paid while labelled bad")
    return _cap("; ".join(parts)) + "."


# ---------------------------------------------------- critic calibration

def _points(episodes: list, gamma: float) -> list:
    """One point per step that carries a value: what the critic predicted
    and what the episode then actually collected from there."""
    pts = []
    for ep in episodes:
        actual = discounted_to_go([r["reward"] for r in ep["rows"]], gamma)
        for i, r in enumerate(ep["rows"]):
            if r["value"] is None:
                continue
            pts.append(dict(_where(ep), step=r["step"], predicted=_r(r["value"]), actual=_r(actual[i]),
                            residual=_r(r["value"] - actual[i]), success=ep["success"]))
    pts.sort(key=lambda p: (p["agent"], p["task_id"] or "", p["run_id"] or "", p["step"] if p["step"] is not None else 0))
    return pts


def _scores(points: list) -> dict:
    resid = [p["residual"] for p in points]
    actual = [p["actual"] for p in points]
    var_actual = _pvar(actual)
    var_resid = _pvar(resid)
    ev = None if var_actual in (None, 0) else _r(1.0 - var_resid / var_actual, 4)
    bias = _mean(resid)
    return {"n": len(points), "mean_error": _r(bias), "mean_absolute_error": _r(_mean([abs(v) for v in resid])),
            "rmse": _r(math.sqrt(_mean([v * v for v in resid]))) if resid else None,
            "variance_actual": _r(var_actual), "variance_residual": _r(var_resid),
            "explained_variance": ev,
            "direction": None if bias is None else ("optimistic" if bias > 0 else "pessimistic" if bias < 0 else "unbiased"),
            "worse_than_the_mean": bool(ev is not None and ev < 0)}


def _ev_words(scores: dict) -> str:
    ev = scores["explained_variance"]
    if ev is None:
        return ("the realised returns-to-go do not vary at all, so there is no variance for a critic to explain "
                "and no explained-variance figure to quote")
    bias, direction = scores["mean_error"], scores["direction"]
    lead = (f"the critic explains {_num(100 * ev)}% of the variance in what the episodes went on to collect"
            if ev >= 0 else
            f"the critic is worse than predicting the mean: explained variance {_num(ev)}, which is negative, "
            f"so a constant equal to the average return-to-go would have scored better than this value head")
    return (f"{lead}; it runs {direction} by {_num(abs(bias))} on average "
            f"(mean absolute error {_num(scores['mean_absolute_error'])}, RMSE {_num(scores['rmse'])}) "
            f"over {_plural(scores['n'], 'step with a value', 'steps with a value')}")


def _deciles(points: list) -> list:
    """Calibration by decile of *predicted* value: what the critic said
    against what actually arrived, so the reader sees where it is wrong."""
    if not points:
        return []
    order = sorted(points, key=lambda p: (p["predicted"], p["agent"], p["task_id"] or "",
                                          p["run_id"] or "", p["step"] if p["step"] is not None else 0))
    n = len(order)
    k = min(DECILES, n)
    out = []
    for j in range(k):
        lo, hi = (j * n) // k, ((j + 1) * n) // k
        chunk = order[lo:hi]
        if not chunk:
            continue
        out.append({"bin": j, "n": len(chunk),
                    "predicted_lo": _r(chunk[0]["predicted"]), "predicted_hi": _r(chunk[-1]["predicted"]),
                    "predicted": _r(_mean([p["predicted"] for p in chunk])),
                    "actual": _r(_mean([p["actual"] for p in chunk])),
                    "residual": _r(_mean([p["residual"] for p in chunk]))})
    return out


def _residual_bins(points: list) -> list:
    resid = [p["residual"] for p in points]
    if not resid:
        return []
    lo, hi = min(resid), max(resid)
    if hi - lo < 1e-12:
        return [{"from": _r(lo), "to": _r(hi), "count": len(resid),
                 "by_agent": {a: sum(1 for p in points if p["agent"] == a) for a in sorted({p["agent"] for p in points})}}]
    width = (hi - lo) / RESIDUAL_BINS
    bins = []
    for j in range(RESIDUAL_BINS):
        a, b = lo + j * width, lo + (j + 1) * width
        inside = [p for p in points if (a <= p["residual"] < b) or (j == RESIDUAL_BINS - 1 and p["residual"] == hi)]
        bins.append({"from": _r(a), "to": _r(b), "count": len(inside),
                     "by_agent": {name: sum(1 for p in inside if p["agent"] == name)
                                  for name in sorted({p["agent"] for p in points})}})
    return bins


def _advantages(episodes: list, gamma: float) -> dict:
    """``advantage`` against the definition ``docs/RL.md`` states for it:
    the realised discounted return-to-go minus the value estimate. A trace
    that records its own advantages and fails this has a pipeline bug."""
    checked, bad_eps, worst = 0, {}, 0.0
    recorded = 0
    for ep in episodes:
        actual = discounted_to_go([r["reward"] for r in ep["rows"]], gamma)
        for i, r in enumerate(ep["rows"]):
            if r["advantage"] is None:
                continue
            recorded += 1
            if r["value"] is None:
                continue
            checked += 1
            err = abs(r["advantage"] - (actual[i] - r["value"]))
            worst = max(worst, err)
            if err > ADV_TOL:
                key = (ep["agent"], ep["task_id"] or "", ep["run_id"] or "")
                entry = bad_eps.setdefault(key, dict(_where(ep), kind="advantage_inconsistent", status=SIGNAL,
                                                     steps=[], max_error=0.0))
                entry["steps"].append(r["step"])
                entry["max_error"] = _r(max(entry["max_error"], err))
    definition = "advantage = discounted return-to-go (γ) − value"
    if not recorded:
        return {"measurable": False,
                "reason": "no step records an advantage of its own; the advantages in the episode arrays are derived "
                          "here from value and reward, so checking them would only check this module against itself",
                "definition": definition, "checked": 0, "recorded": 0, "inconsistent": 0,
                "episodes": [], "max_error": None, "tolerance": ADV_TOL, "note": ""}
    if not checked:
        return {"measurable": False,
                "reason": f"{_plural(recorded, 'step')} records an advantage but none of them also records a value, "
                          "so the definition cannot be evaluated",
                "definition": definition, "checked": 0, "recorded": recorded, "inconsistent": 0,
                "episodes": [], "max_error": None, "tolerance": ADV_TOL, "note": ""}
    rows = [bad_eps[k] for k in sorted(bad_eps)]
    for row in rows:
        row["steps"] = sorted(s for s in row["steps"] if s is not None)
    note = (f"{_plural(len(rows), 'episode')} disagrees with {definition} beyond {ADV_TOL} "
            f"(worst {_num(worst)}); a recorded advantage that does not match its own rewards and values is a "
            "training-pipeline bug, and it is the kind that silently ruins a run"
            if rows else
            f"all {_plural(checked, 'recorded advantage')} match {definition} to within {ADV_TOL} "
            f"(worst {_num(worst)})")
    return {"measurable": True, "reason": None, "definition": definition, "checked": checked, "recorded": recorded,
            "inconsistent": len(rows), "episodes": rows, "max_error": _r(worst), "tolerance": ADV_TOL, "note": note}


def critic_calibration(episodes: list, *, gamma: float = DEFAULT_GAMMA) -> dict:
    """Score the value head against what actually arrived: the residual per
    step, the bias, the errors, the explained variance, calibration by
    decile of predicted value, and the advantage-definition check.
    ``measurable: False`` with a reason when no step carries a value."""
    points = _points(episodes, gamma)
    covered = sorted({(p["agent"], p["task_id"], p["run_id"]) for p in points})
    advantages = _advantages(episodes, gamma)
    if not points:
        return {"measurable": False,
                "reason": (f"no step of the {_plural(len(episodes), 'episode')} carries a value estimate, "
                           "so there is no prediction to score"),
                "gamma": gamma, "n": 0, "episodes_covered": 0, "episodes_n": len(episodes),
                "overall": {}, "per_agent": {}, "deciles": [], "residual_bins": [], "points": [],
                "advantages": advantages, "narrative": "", "note": ""}
    overall = _scores(points)
    per_agent = {}
    for name in sorted({p["agent"] for p in points}):
        mine = [p for p in points if p["agent"] == name]
        block = _scores(mine)
        block["episodes_covered"] = len({(p["task_id"], p["run_id"]) for p in mine})
        block["deciles"] = _deciles(mine)
        block["words"] = _ev_words(block)
        per_agent[name] = block
    narrative = _ev_words(overall)
    if advantages.get("note"):
        narrative += "; " + advantages["note"]
    return {"measurable": True, "reason": None, "gamma": gamma, "n": len(points),
            "episodes_covered": len(covered), "episodes_n": len(episodes),
            "overall": overall, "per_agent": per_agent, "deciles": _deciles(points),
            "residual_bins": _residual_bins(points), "points": points, "advantages": advantages,
            "note": (f"{_plural(len(points), 'step')} of {sum(e['steps'] for e in episodes)} "
                     f"{'carries' if len(points) == 1 else 'carry'} a value estimate, "
                     f"over {len(covered)} of {_plural(len(episodes), 'episode')}; the target is the realised "
                     f"discounted return-to-go at γ={gamma}, recomputed here from the recorded rewards"),
            "narrative": _cap(narrative) + "."}


# ---------------------------------------------------------------- public

def rl_audit(episodes: list, *, gamma: float = DEFAULT_GAMMA, scope: str = "batch") -> dict:
    """``agg["rl"]["audit"]`` / ``report["rl"]["audit"]``: the reward read
    against the outcome, and the critic read against what arrived.

    ``episodes`` are audit episodes from one of the adapters
    (:func:`episodes_from_runs`, :func:`episodes_from_pair`,
    :func:`episodes_from_aggregate`). ``scope`` names which, for a page
    that has to say what it is looking at."""
    if not episodes:
        return {"version": VERSION, "measurable": False, "reason": "no measurable episodes", "gamma": gamma,
                "scope": scope, "episodes_n": 0, "policies": [], "reward": {}, "critic": {},
                "narrative": "No episode carries a reward, so neither the reward nor the critic can be audited.",
                "caveat": CAVEAT}
    reward = reward_integrity(episodes, gamma=gamma)
    critic = critic_calibration(episodes, gamma=gamma)
    narrative = reward.get("narrative", "")
    if critic.get("measurable"):
        narrative += " " + critic["narrative"]
    else:
        narrative += " " + _cap(critic.get("reason") or "the critic cannot be scored") + "."
    return {"version": VERSION, "measurable": True, "reason": None, "gamma": gamma, "scope": scope,
            "episodes_n": len(episodes), "policies": sorted({e["agent"] for e in episodes}),
            "reward": reward, "critic": critic, "narrative": narrative.strip(), "caveat": CAVEAT}


#: said on every finding list, because a finding is not a verdict
CAVEAT = ("Every finding here is a signal to investigate, not a proven defect: a failed episode with a high "
          "return may be a hard task rather than a gamed one, and a badly calibrated critic early in training "
          "is expected. Each one names the task, the run and the step so it can be checked.")


def audit_aggregate(runs: list, *, gamma: float = DEFAULT_GAMMA) -> dict:
    """The audit for the runs layout, from the per-trace run readings."""
    return rl_audit(episodes_from_runs(runs), gamma=gamma, scope="batch")


def audit_pair(rl: dict, *, task_id: Optional[str] = None, run_ids: Optional[dict] = None,
               gamma: float = DEFAULT_GAMMA) -> dict:
    """The audit for one pair report: two episodes, so the disagreement and
    the rank correlation mostly say they cannot be measured at this size,
    while concentration, unearned reward, cost, tools and the critic all
    still count over the pair's own steps."""
    return rl_audit(episodes_from_pair(rl, task_id=task_id, run_ids=run_ids), gamma=gamma, scope="pair")


__all__ = ["rl_audit", "audit_aggregate", "audit_pair", "reward_integrity", "critic_calibration",
           "episodes_from_runs", "episodes_from_pair", "episodes_from_aggregate", "spearman", "rank_average",
           "discounted_to_go", "BAD_LABELS", "GOOD_LABELS", "ADV_TOL", "DECILES", "SMALL_SAMPLE",
           "DOMINANT_SHARE", "TERMINAL_SHARE", "CAVEAT", "VERSION"]
