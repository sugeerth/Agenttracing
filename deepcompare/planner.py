"""The controller of the agentic loop — and deliberately not a model.

The loop that drives AgentDiff autonomously (run → compare → diagnose →
test a prompt change → route → run again) needs something to decide
what to do next. This module is that something, and it is a set of
rules over the engine's own numbers: every decision is computed from
success counts, Wilson intervals, routing confidence and paired
comparisons, and every decision carries the sentence that justifies it.
A language model is the thing under test in this loop, and optionally
the judge of an answer; it is never in the control path, so the loop
cannot be talked into a conclusion and a run of it can be audited
decision by decision.

Three ideas shape the rules:

* **Spend runs where uncertainty is** — the next batch of runs goes to
  the task families whose routing pick is not yet clear (widest
  interval first), not uniformly over everything.
* **One variable per experiment** — a prompt suggestion from the
  reading is a hypothesis; it is tested as a paired comparison between
  the agent with its current prompt and the same agent with the change,
  on the same tasks, and kept only when the paired result supports it.
  Two untested changes are never stacked.
* **Stop for a reason** — budget spent, every family clear and no
  hypothesis left, or nothing the loop can still learn; the reason is
  written down.
"""

from __future__ import annotations

from typing import Optional

from .statistics import sign_test, wilson_interval

VERSION = 1

ACTIONS = ("compare", "test-prompt", "stop")
#: runs per candidate after which equal success rates count as a tie
TIE_RUNS = 6


def new_state(agents: list, tasks: list, *, prompt_agents: Optional[list] = None,
              base_prompt: str = "") -> dict:
    """The loop's state before its first iteration. ``prompt_agents`` are
    the agents whose system prompt the loop may change (provider agents
    and command agents that read the prompt from their environment)."""
    prompt_agents = list(prompt_agents if prompt_agents is not None else agents)
    return {
        "version": VERSION,
        "agents": list(agents),
        "tasks": list(tasks),
        "spent_runs": 0,
        "iterations": [],
        "prompts": {a: {"current": base_prompt, "version": 0, "candidates": [], "history": []}
                    for a in prompt_agents},
        "stop": None,
    }


def add_candidates(state: dict, agent: str, suggestions: list, *, source: str) -> int:
    """Queue prompt suggestions for ``agent`` (one per kind, first wins);
    returns how many were new. Agents whose prompt the loop cannot
    change get none."""
    slot = state["prompts"].get(agent)
    if slot is None:
        return 0
    tried = {c["kind"] for c in slot["candidates"]} | {h["kind"] for h in slot["history"]}
    added = 0
    for sug in suggestions:
        kind = sug.get("kind") or "custom"
        text = (sug.get("text") or "").strip()
        if not text or kind in tried:
            continue
        tried.add(kind)
        slot["candidates"].append({"kind": kind, "text": text, "source": source,
                                   "derived_from": sug.get("derived_from"), "from_tasks": list(sug.get("from_tasks") or [])})
        added += 1
    return added


def _last_compare(state: dict) -> Optional[dict]:
    for it in reversed(state["iterations"]):
        if it["action"] == "compare":
            return it
    return None


def _uncertain_families(routing: dict) -> list:
    """Families whose pick is not clear, widest top interval first, with
    the tasks that belong to them."""
    out = []
    for fam, row in (routing.get("families") or {}).items():
        if row.get("confidence") == "clear":
            continue
        cands = row.get("candidates") or []
        # two candidates with the same rate over enough runs are a tie no
        # further run can break: not uncertain, just equal
        if row.get("confidence") == "overlapping" and len(cands) >= 2:
            a, b = cands[0]["features"], cands[1]["features"]
            if a.get("rate") == b.get("rate") and min(a.get("n", 0), b.get("n", 0)) >= TIE_RUNS:
                continue
        widths = [c["features"]["ci95"][1] - c["features"]["ci95"][0] for c in cands if c.get("features", {}).get("ci95")]
        out.append({"family": fam, "confidence": row.get("confidence"), "why": row.get("why", ""),
                    "width": max(widths) if widths else 1.0, "tasks": list(row.get("tasks") or [])})
    out.sort(key=lambda r: (-r["width"], r["family"]))
    return out


def plan(state: dict, *, runs: int = 3, max_iterations: int = 4, max_runs: Optional[int] = None) -> dict:
    """Decide the next iteration. Returns ``{"action", "why", ...}`` with
    the tasks and run counts the action needs. Pure: the same state
    always yields the same plan."""
    agents = state["agents"]
    n_iter = len(state["iterations"])
    remaining = None if max_runs is None else max_runs - state["spent_runs"]

    def stop(reason: str, kind: str) -> dict:
        return {"action": "stop", "kind": kind, "why": reason}

    if n_iter >= max_iterations:
        return stop(f"iteration budget spent: {n_iter} of {max_iterations}", "iterations")
    if remaining is not None and remaining <= 0:
        return stop(f"run budget spent: {state['spent_runs']} of {max_runs}", "runs")

    def affordable(n_tasks: int, n_agents: int, per: int) -> bool:
        return remaining is None or n_tasks * n_agents * per <= remaining

    if n_iter == 0:
        per = runs
        while per > 1 and not affordable(len(state["tasks"]), len(agents), per):
            per -= 1
        if not affordable(len(state["tasks"]), len(agents), per):
            return stop(f"run budget {max_runs} cannot cover one run of every task for every agent", "runs")
        return {"action": "compare", "tasks": list(state["tasks"]), "agents": list(agents), "runs": per,
                "why": f"baseline: every task for every agent, {per} run(s) each, so every later decision has a paired reference"}

    # a queued prompt hypothesis is tested before more runs are spent on
    # the comparison, because a kept change invalidates the older runs
    for agent in agents:
        slot = state["prompts"].get(agent)
        if not slot or not slot["candidates"]:
            continue
        # a hypothesis whose source failure no longer reproduces under the
        # current prompt is not worth a batch of runs: dropped, and said so
        latest = (state.get("latest") or {}).get(agent) or {}
        keep = []
        for cand in slot["candidates"]:
            src = [t for t in cand.get("from_tasks") or [] if t in latest]
            if src and all(latest[t][1] and latest[t][0] == latest[t][1] for t in src):
                slot["history"].append({"agent": agent, "kind": cand["kind"], "text": cand["text"], "source": cand["source"],
                                        "status": "dropped", "iteration": n_iter,
                                        "why": (f"the failure it came from ({', '.join(src)}) no longer reproduces under the "
                                                f"current prompt: {', '.join(f'{latest[t][0]}/{latest[t][1]}' for t in src)} passed")})
            else:
                keep.append(cand)
        slot["candidates"] = keep
        if not keep:
            continue
        cand = keep[0]
        tasks = list(state["tasks"])
        scope = "every task, so a regression elsewhere shows"
        if not affordable(len(tasks), 2, runs):
            tasks = [t for t in tasks if t in set(cand.get("from_tasks") or [])] or tasks[:1]
            scope = "only the tasks it came from — the run budget does not cover every task"
        per = runs
        while per > 1 and not affordable(len(tasks), 2, per):
            per -= 1
        if not affordable(len(tasks), 2, per):
            return stop("run budget too small to test the queued prompt change as a paired experiment", "runs")
        return {"action": "test-prompt", "agent": agent, "candidate": cand, "tasks": tasks, "runs": per,
                "variant": f"p{slot['version'] + 1}",
                "why": (f"test one hypothesis for {agent} — '{cand['kind'].replace('_', ' ')}' from {cand['source']} — as a paired "
                        f"experiment against its current prompt on {scope}, {per} run(s) a side; one variable at a time")}

    # a kept prompt change retires the agent's older runs: tasks the
    # experiment did not cover are re-run for every agent before the
    # comparison is trusted again
    needs = state.get("needs_runs") or {}
    pending = []
    for agent in agents:
        for t in needs.get(agent) or []:
            if t not in pending:
                pending.append(t)
    if pending:
        per = runs
        while per > 1 and not affordable(len(pending), len(agents), per):
            per -= 1
        if not affordable(len(pending), len(agents), per):
            return stop("run budget too small to re-measure the tasks a kept prompt change did not cover", "runs")
        return {"action": "compare", "tasks": pending, "agents": list(agents), "runs": per,
                "why": (f"re-measure after a kept prompt change: {len(pending)} task(s) the experiment did not cover, "
                        f"{per} run(s) per agent")}

    last = _last_compare(state)
    routing = (last or {}).get("routing") or {}
    uncertain = _uncertain_families(routing)
    if uncertain:
        tasks: list = []
        for fam in uncertain:
            for t in fam["tasks"]:
                if t not in tasks:
                    tasks.append(t)
        per = runs
        while tasks and not affordable(len(tasks), len(agents), per):
            if per > 1:
                per -= 1
            else:
                tasks = tasks[:-1]
        if not tasks:
            return stop("run budget too small for another run on the uncertain families", "runs")
        lead = uncertain[0]
        return {"action": "compare", "tasks": tasks, "agents": list(agents), "runs": per,
                "families": [f["family"] for f in uncertain],
                "why": (f"spend runs where the pick is not yet clear: {len(uncertain)} famil{'y' if len(uncertain) == 1 else 'ies'} "
                        f"({lead['family']} first, interval width {lead['width']:.2f}, {lead['confidence']}) — "
                        f"{per} more run(s) per agent on {len(tasks)} task(s)")}
    return stop("converged: every family has a clear pick or a tie no further run can break, and no prompt hypothesis is left to test", "converged")


def decide_prompt(baseline: dict, variant: dict, *, agent: str, candidate: dict) -> dict:
    """Keep or revert a prompt change from its paired experiment.

    ``baseline``/``variant``: ``{task: (successes, runs)}`` for the same
    tasks. Kept when the variant wins more tasks than it loses and no
    task regresses from always-pass to always-fail; ``kept`` when the
    sign test is below 0.05, ``kept (provisional)`` otherwise — the
    reader sees which. Every count is in the returned evidence."""
    tasks = sorted(set(baseline) & set(variant))
    wins = losses = ties = 0
    regressions = []
    improvements = []
    for tid in tasks:
        bs, bn = baseline[tid]
        vs, vn = variant[tid]
        b_rate = bs / bn if bn else 0.0
        v_rate = vs / vn if vn else 0.0
        if v_rate > b_rate:
            wins += 1
            if bs == 0 and vs == vn:
                improvements.append(tid)
        elif v_rate < b_rate:
            losses += 1
            if bs == bn and vs == 0:
                regressions.append(tid)
        else:
            ties += 1
    b_tot = (sum(s for s, _ in baseline.values()), sum(n for _, n in baseline.values()))
    v_tot = (sum(s for s, _ in variant.values()), sum(n for _, n in variant.values()))
    p = sign_test(wins, losses) if wins + losses else None
    b_ci = wilson_interval(*b_tot) if b_tot[1] else None
    v_ci = wilson_interval(*v_tot) if v_tot[1] else None
    keep = wins > losses and not regressions
    if keep:
        status = "kept" if (p is not None and p < 0.05) else "kept (provisional)"
    else:
        status = "reverted"
    if keep:
        why = (f"{agent} with the change won {wins} task(s), lost {losses}, tied {ties}; success {v_tot[0]}/{v_tot[1]} against "
               f"{b_tot[0]}/{b_tot[1]} without it" + (f"; sign test p={p:.3g}" if p is not None else "") +
               ("; kept as the new prompt" if status == "kept" else
                "; the paired evidence is small, so the change is kept provisionally and the next comparison re-measures it"))
    else:
        why = (f"{agent} with the change won {wins} task(s), lost {losses}, tied {ties}"
               + (f"; it regressed {', '.join(regressions)} from always passing to always failing" if regressions else "")
               + f"; success {v_tot[0]}/{v_tot[1]} against {b_tot[0]}/{b_tot[1]} without it; the current prompt stands")
    return {
        "agent": agent, "kind": candidate.get("kind"), "text": candidate.get("text"), "source": candidate.get("source"),
        "status": status, "why": why,
        "evidence": {"tasks": len(tasks), "wins": wins, "losses": losses, "ties": ties, "sign_test_p": p,
                     "regressions": regressions, "improvements": improvements,
                     "baseline": {"successes": b_tot[0], "runs": b_tot[1], "ci95": list(b_ci) if b_ci else None},
                     "variant": {"successes": v_tot[0], "runs": v_tot[1], "ci95": list(v_ci) if v_ci else None}},
    }


def apply_decision(state: dict, decision: dict) -> None:
    """Move the tested candidate into history; on keep, make its text
    part of the agent's current prompt."""
    slot = state["prompts"][decision["agent"]]
    slot["candidates"] = [c for c in slot["candidates"] if c.get("kind") != decision.get("kind")]
    entry = dict(decision)
    entry["iteration"] = len(state["iterations"])
    slot["history"].append(entry)
    if decision["status"].startswith("kept"):
        slot["version"] += 1
        slot["current"] = (slot["current"].rstrip() + "\n" + decision["text"]).strip()
        entry["prompt_version"] = slot["version"]


def summarise(state: dict) -> dict:
    """The ledger's closing numbers: per agent, the pooled success of
    the last comparison and how the prompt ended; plus the stop reason."""
    last = _last_compare(state)
    out = {"iterations": len(state["iterations"]), "spent_runs": state["spent_runs"], "stop": state.get("stop"),
           "agents": {}, "kept_changes": 0, "reverted_changes": 0, "dropped_changes": 0}
    for agent in state["agents"]:
        slot = state["prompts"].get(agent) or {}
        hist = slot.get("history") or []
        kept = [h for h in hist if h["status"].startswith("kept")]
        out["kept_changes"] += len(kept)
        out["reverted_changes"] += sum(1 for h in hist if h["status"] == "reverted")
        out["dropped_changes"] = out.get("dropped_changes", 0) + sum(1 for h in hist if h["status"] == "dropped")
        res = ((last or {}).get("results") or {}).get(agent) or {}
        out["agents"][agent] = {"success": res.get("success"), "runs": res.get("runs"), "ci95": res.get("ci95"),
                                "prompt_version": slot.get("version", 0), "kept": [h["kind"] for h in kept],
                                "reverted": [h["kind"] for h in hist if h["status"] == "reverted"],
                                "dropped": [h["kind"] for h in hist if h["status"] == "dropped"]}
    return out


__all__ = ["VERSION", "ACTIONS", "TIE_RUNS", "new_state", "add_candidates", "plan", "decide_prompt", "apply_decision", "summarise"]
