"""Where the tokens went.

A run's token budget is the sum of what its steps recorded, and this
module attributes it: to each kind of step (plan, reason, search,
retrieve, read, tool call, answer), to each tool by name, and to each
step in order as a cumulative burn. A step's count is taken as the trace
carries it and labelled as the trace labelled it — ``tokens_basis`` says
``measured`` or ``estimated`` per step, and a step without the label is
counted under *unknown*; nothing is re-estimated here. The whole-run
input/output tokens and the cost come from the trace's totals when they
were recorded and are unmeasurable, with the reason, when they were not.

Waste is read three ways, each a sum over steps: tokens spent after the
last step that carried an evidence signal (a recorded ``reward > 0`` or
a ``quality`` label of ``good``) and before the answer, tokens in steps
that errored, and tokens in fetches that repeated an earlier one. Per
pair the two runs sit side by side with their delta; per aggregate every
run of every agent is summed, with a ledger row per run and the cap the
command was given, when it was. A run with no steps is unmeasurable,
never free.
"""

from __future__ import annotations

from typing import Any, Optional

from . import sections as _sections
from ._stats import finite, mean, rounded
from ._text import join_names, num, pct, plural, run_name, signed
from .fetches import FETCH_KINDS, RunView, repeat_index, used_signal
from .section import measurable, unmeasurable

VERSION = 1
#: the kinds a budget is split by, in the order the split lists them
KINDS = ("plan", "reason", "search", "retrieve", "read", "tool_call", "answer")
#: how many steps the cumulative burn keeps; a longer run is capped with a note
BURN_CAP = 2000
#: how many of the heaviest steps are named
TOP_STEPS = 8
#: how many runs the aggregate names as heaviest, and lists over the cap
HEAVIEST = 8
TOKENS_BASIS = "steps[].tokens summed; tokens_basis says measured|estimated per step, unknown when absent"


def _tokens(step: Any) -> Optional[int]:
    return int(step.tokens) if finite(step.tokens) and step.tokens >= 0 else None


def _flag(tokens: dict, name: str, index: int, note: str) -> None:
    """Record a self-contradiction in a step's own counts."""
    checks = tokens["integrity"]["checks"]
    for row in checks:
        if row["name"] == name:
            row["steps"].append(index)
            return
    checks.append({"name": name, "steps": [index], "note": note})
    tokens["integrity"]["ok"] = False


def _empty_tokens() -> dict:
    return {"total": 0, "by_kind": {k: 0 for k in KINDS}, "by_tool": {}, "measured": 0, "estimated": 0, "unknown": 0,
            "unknown_steps": 0, "cached": None, "cached_steps": 0,
            "integrity": {"ok": True, "checks": []}, "basis": TOKENS_BASIS}


def _empty_run(reason: str, name: str, synthetic: bool = False) -> dict:
    return unmeasurable(reason, tokens=_empty_tokens(),
                        io=unmeasurable("the run cannot be read", input_tokens=None, output_tokens=None, source=None),
                        cost_usd=unmeasurable("the run cannot be read", value=None, source=None),
                        burn=[], burn_capped=False, burn_note=None, top=[],
                        waste={"after_last_evidence": None, "in_errored_calls": 0, "in_repeats": 0, "basis": "the run cannot be read"},
                        per_second=unmeasurable("the run cannot be read", value=None, seconds=None),
                        synthetic=synthetic, narrative=f"{name}: {reason}, so where its tokens went cannot be read.")


# --------------------------------------------------------------- one run

def budget_run(run: Any) -> dict:
    """Where one run's tokens went: the split by kind and by tool with the
    measured/estimated/unknown shares, the totals' input/output tokens and
    cost when recorded, the cumulative burn, the heaviest steps, the three
    wastes, tokens per second when latency was recorded, and a narrative.
    ``run`` is a :class:`~deepcompare.trace.Trajectory` or a report side."""
    try:
        view = RunView(run)
    except (ValueError, TypeError) as exc:
        return _empty_run(f"the run cannot be read: {exc}", run_name(run))
    steps = view.steps
    if not steps:
        return _empty_run("the run has no steps", view.name, view.synthetic)
    tokens = _empty_tokens()
    burn: list = []
    cum = 0
    rows: list = []
    for st in steps:
        t = _tokens(st)
        if t is None:
            tokens["unknown_steps"] += 1
            t = 0
        else:
            if st.tokens_basis == "measured":
                tokens["measured"] += t
            elif st.tokens_basis == "estimated":
                tokens["estimated"] += t
            else:
                tokens["unknown"] += t
        # input the provider served from its own cache, where it said so.
        # `None` until a step reports one, because nothing here can tell a
        # working cache from a provider that does not mention caching, and
        # a zero would claim it could.
        if isinstance(st.cached_tokens, int) and st.cached_tokens >= 0:
            tokens["cached"] = (tokens["cached"] or 0) + st.cached_tokens
            tokens["cached_steps"] += 1
        # Two things a trace can say that cannot both be true. The counts
        # are recorded, so they are reported — but a reading that prints
        # "100 tokens ... 4,000 in and 20 out" in one sentence has stated
        # an impossibility as a fact, and the point of the check is that
        # the contradiction travels with the numbers instead of being
        # smoothed over. Neither figure is preferred: the trace is wrong
        # and nothing here can say which half.
        have_split = isinstance(st.input_tokens, int) and isinstance(st.output_tokens, int)
        if have_split and isinstance(st.tokens, int) and st.input_tokens + st.output_tokens != st.tokens:
            _flag(tokens, "split_disagrees_with_total", st.index,
                  "a step's input and output counts do not add up to the total it reports")
        if (isinstance(st.cached_tokens, int) and isinstance(st.input_tokens, int)
                and st.cached_tokens > st.input_tokens):
            _flag(tokens, "cached_exceeds_input", st.index,
                  "a step reports more input served from the cache than input sent")
        tokens["total"] += t
        if st.type in KINDS:
            tokens["by_kind"][st.type] += t
        if st.type in FETCH_KINDS:
            tokens["by_tool"][st.name or "?"] = tokens["by_tool"].get(st.name or "?", 0) + t
        cum += t
        rows.append((st, t))
        if len(burn) < BURN_CAP:
            burn.append([st.index, st.type, st.name or "", t, cum])
    tokens["by_tool"] = dict(sorted(tokens["by_tool"].items()))
    total = tokens["total"]
    capped = len(steps) > BURN_CAP
    top = sorted(((st, t) for st, t in rows if t > 0), key=lambda r: (-r[1], r[0].index))[:TOP_STEPS]
    top_rows = [{"index": st.index, "kind": st.type, "name": st.name or "", "tokens": t,
                 "share": rounded(t / total) if total else 0.0} for st, t in top]
    totals = view.totals
    # The steps first, when they carry the split: a per-step count says
    # *where* the context went, which a whole-run figure cannot, and the
    # two can disagree — the totals are what the runner wrote down at the
    # end, the steps are what it wrote down as it went. The source is named
    # either way rather than the reader having to guess which they got.
    split_steps = [st for st in view.steps
                   if isinstance(st.input_tokens, int) or isinstance(st.output_tokens, int)]
    if split_steps:
        io = measurable({"input_tokens": sum(int(st.input_tokens or 0) for st in split_steps),
                         "output_tokens": sum(int(st.output_tokens or 0) for st in split_steps),
                         "steps": len(split_steps), "of_steps": len(view.steps), "source": "steps"})
    elif totals.input_tokens or totals.output_tokens:
        io = measurable({"input_tokens": int(totals.input_tokens), "output_tokens": int(totals.output_tokens),
                         "steps": 0, "of_steps": len(view.steps), "source": "totals"})
    else:
        io = unmeasurable("neither the steps nor the totals carry an input/output split (absent or 0; unrecorded "
                          "is not zero)", input_tokens=None, output_tokens=None, steps=0,
                          of_steps=len(view.steps), source=None)
    cost = (measurable({"value": float(totals.cost_usd), "source": "totals"}) if finite(totals.cost_usd) and totals.cost_usd > 0
            else unmeasurable("totals carry no cost (absent or 0; unrecorded is not free)", value=None, source=None))
    waste = _waste(rows)
    seconds = sum(float(st.latency_s) for st, _ in rows if finite(st.latency_s) and st.latency_s > 0)
    per_second = (measurable({"value": round(total / seconds, 2), "seconds": round(seconds, 4)}) if seconds > 0
                  else unmeasurable("no step recorded a latency", value=None, seconds=None))
    payload = {
        "tokens": tokens, "io": io, "cost_usd": cost, "burn": burn, "burn_capped": capped,
        "burn_note": (f"burn keeps the first {BURN_CAP} of {len(steps)} steps; tokens.total covers every step" if capped else None),
        "top": top_rows, "waste": waste, "per_second": per_second, "synthetic": view.synthetic,
    }
    payload["narrative"] = _run_narrative(view.name, len(steps), payload)
    return measurable(payload)


def _waste(rows: list) -> dict:
    steps = [st for st, _ in rows]
    repeats = repeat_index(steps)
    signalled = False
    last_evidence: Optional[int] = None
    for pos, (st, _) in enumerate(rows):
        if st.type == "answer":
            continue
        used, _basis = used_signal(st)
        if used is not None:
            signalled = True
        if used is True:
            last_evidence = pos
    if not signalled:
        after: Optional[int] = None
        basis_after = "no evidence signal recorded (no step carries reward > 0 or a quality label)"
    else:
        start = -1 if last_evidence is None else last_evidence
        after = sum(t for pos, (st, t) in enumerate(rows) if pos > start and st.type != "answer")
        basis_after = ("tokens of the steps after the last step with a recorded reward > 0 or a quality label good, before the answer"
                       if last_evidence is not None else
                       "signals are recorded but none is positive, so every step before the answer counts")
    return {"after_last_evidence": after,
            "in_errored_calls": sum(t for st, t in rows if st.error is True),
            "in_repeats": sum(t for st, t in rows if st.index in repeats),
            "basis": f"after_last_evidence: {basis_after}; in_errored_calls: steps with error true; "
                     "in_repeats: fetches repeating an earlier (name, input)"}


def _run_narrative(name: str, n_steps: int, p: dict) -> str:
    tk = p["tokens"]
    total = tk["total"]
    if not total:
        return f"{name} recorded no tokens over {plural(n_steps, 'step')} — unmeasurable where they went, not free."
    kinds = sorted(((k, v) for k, v in tk["by_kind"].items() if v), key=lambda kv: (-kv[1], KINDS.index(kv[0])))[:3]
    parts = [f"{name} spent {num(total)} tokens over {plural(n_steps, 'step')}: "
             + join_names(f"{pct(v / total)} on {k.replace('_', ' ')}" for k, v in kinds)]
    basis = [f"{num(v)} {label}" for label, v in (("measured", tk["measured"]), ("estimated", tk["estimated"]),
                                                   ("unlabelled", tk["unknown"])) if v]
    parts.append(join_names(basis) if basis else "no step labelled its count")
    io = p.get("io") or {}
    bad = {row["name"] for row in ((tk.get("integrity") or {}).get("checks") or [])}
    if io.get("measurable") and io.get("source") == "steps" and (io["input_tokens"] or io["output_tokens"]):
        parts.append(f"{num(io['input_tokens'])} in and {num(io['output_tokens'])} out, over "
                     + plural(io.get("steps") or 0, "step") + " that split them"
                     + (" — which does not add up to the total those steps report, so the trace contradicts "
                        "itself and neither figure can be preferred" if "split_disagrees_with_total" in bad else ""))
    if tk.get("cached") is not None:
        parts.append(f"{num(tk['cached'])} of the input came from the provider's cache over "
                     + plural(tk["cached_steps"], "step") + " that said so, and was not paid for"
                     + (" — though a step reports more served from the cache than it sent, which cannot be true"
                        if "cached_exceeds_input" in bad else ""))
    if p["top"]:
        t = p["top"][0]
        parts.append(f"the heaviest step was {t['index']} ({t['name'] or t['kind']}, {num(t['tokens'])} tokens, {pct(t['share'])})")
    w = p["waste"]
    wastes = [f"{num(w['after_last_evidence'])} after the last evidence" if w["after_last_evidence"] is not None else "no evidence signal recorded",
              f"{num(w['in_errored_calls'])} in errored calls", f"{num(w['in_repeats'])} in repeats"]
    parts.append(join_names(wastes))
    if p["per_second"]["measurable"]:
        parts.append(f"{num(p['per_second']['value'])} tokens per recorded second")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- the pair

def _delta(a: dict, b: dict) -> dict:
    ta, tb = a["tokens"], b["tokens"]
    tools = sorted(set(ta["by_tool"]) | set(tb["by_tool"]))
    return {"total": ta["total"] - tb["total"],
            "by_kind": {k: ta["by_kind"][k] - tb["by_kind"][k] for k in KINDS},
            "by_tool": {t: ta["by_tool"].get(t, 0) - tb["by_tool"].get(t, 0) for t in tools}}


def budget_pair(report: dict, a: Any = None, b: Any = None) -> dict:
    """Both runs' budgets, the delta (A − B), which run was cheaper and a
    narrative; the trajectories when the caller has them, else the
    report's sides."""
    ba = budget_run(a if a is not None else report.get("a") or {})
    bb = budget_run(b if b is not None else report.get("b") or {})
    name_a, name_b = run_name(report.get("a"), "A"), run_name(report.get("b"), "B")
    if not (ba["measurable"] and bb["measurable"]):
        return unmeasurable("at least one run cannot be read: " + "; ".join(
            f"{n}: {r['reason']}" for n, r in ((name_a, ba), (name_b, bb)) if not r["measurable"]),
            version=VERSION, a=ba, b=bb, delta=None, cheaper=None,
            narrative="Budgets cannot be compared: at least one run cannot be read.")
    delta = _delta(ba, bb)
    cheaper = name_a if delta["total"] < 0 else name_b if delta["total"] > 0 else None
    sentence = f"{name_a} spent {num(ba['tokens']['total'])} tokens against {name_b}'s {num(bb['tokens']['total'])} ({signed(delta['total'])})"
    widest_kind = max(delta["by_kind"].items(), key=lambda kv: (abs(kv[1]), -KINDS.index(kv[0])))
    if widest_kind[1]:
        sentence += f"; the widest gap by kind is {widest_kind[0].replace('_', ' ')} ({signed(widest_kind[1])})"
    widest_tool = max(delta["by_tool"].items(), key=lambda kv: (abs(kv[1]), kv[0]), default=None)
    if widest_tool and widest_tool[1]:
        sentence += f", by tool {widest_tool[0]} ({signed(widest_tool[1])})"
    sentence += f"; {cheaper} was cheaper." if cheaper else "; they cost the same."
    return measurable({"a": ba, "b": bb, "delta": delta, "cheaper": cheaper, "narrative": sentence}, version=VERSION)


# ----------------------------------------------------------- the aggregate

def budget_aggregate(trajectories: list, token_cap: Any = None) -> dict:
    """Every run of every agent summed: per agent the runs, total, mean,
    per-task mean, split by kind and tool, measured share and cost when
    recorded; per task the mean per agent; the heaviest runs; the cap
    the command was given (``None`` with source ``none given`` when it
    was not) and the runs over it; a ledger row per run; a narrative."""
    cap = ({"value": int(token_cap), "source": "--token-cap", "over": []} if finite(token_cap)
           else {"value": None, "source": "none given", "over": []})
    if not trajectories:
        return unmeasurable("no trajectories in the aggregate context", version=VERSION, agents={}, tasks={},
                            heaviest_runs=[], cap=cap, runs=[], narrative="No run reached the aggregate, so no tokens were counted.")
    rows: list = []
    agents: dict = {}
    tasks: dict = {}
    for traj in sorted(trajectories, key=lambda t: (t.agent.name, t.task.id, t.run_id)):
        b = budget_run(traj)
        tk = b["tokens"]
        rows.append({"agent": traj.agent.name, "task": traj.task.id, "run": traj.run_id, "measurable": b["measurable"],
                     "tokens": tk["total"], "measured": tk["measured"], "estimated": tk["estimated"], "unknown": tk["unknown"],
                     "steps": len(traj.steps), "cost_usd": b["cost_usd"]["value"],
                     "seconds": b["per_second"]["seconds"], "synthetic": b["synthetic"]})
        ag = agents.setdefault(traj.agent.name, {"runs": 0, "total": 0, "mean": None, "per_task": {}, "by_kind": {k: 0 for k in KINDS},
                                                  "by_tool": {}, "measured": 0, "estimated": 0, "unknown": 0, "measured_share": None,
                                                  "cost_usd_total": None, "cost_runs": 0, "synthetic": False, "_totals": []})
        ag["runs"] += 1
        ag["total"] += tk["total"]
        ag["_totals"].append(tk["total"])
        for k in KINDS:
            ag["by_kind"][k] += tk["by_kind"][k]
        for t, n in tk["by_tool"].items():
            ag["by_tool"][t] = ag["by_tool"].get(t, 0) + n
        for k in ("measured", "estimated", "unknown"):
            ag[k] += tk[k]
        if b["cost_usd"]["value"] is not None:
            ag["cost_usd_total"] = (ag["cost_usd_total"] or 0.0) + b["cost_usd"]["value"]
            ag["cost_runs"] += 1
        ag["synthetic"] = ag["synthetic"] or b["synthetic"]
        ag["per_task"].setdefault(traj.task.id, []).append(tk["total"])
        tasks.setdefault(traj.task.id, {}).setdefault(traj.agent.name, []).append(tk["total"])
    for ag in agents.values():
        ag["mean"] = rounded(mean(ag.pop("_totals")))
        ag["per_task"] = {t: rounded(mean(v)) for t, v in sorted(ag["per_task"].items())}
        ag["by_tool"] = dict(sorted(ag["by_tool"].items()))
        ag["measured_share"] = rounded(ag["measured"] / ag["total"]) if ag["total"] else None
        if ag["cost_usd_total"] is not None:
            ag["cost_usd_total"] = round(ag["cost_usd_total"], 6)
    task_means = {t: {a: rounded(mean(v)) for a, v in sorted(per.items())} for t, per in sorted(tasks.items())}
    by_weight = sorted(rows, key=lambda r: (-r["tokens"], r["agent"], r["task"], r["run"]))
    brief = lambda r: {"agent": r["agent"], "task": r["task"], "run": r["run"], "tokens": r["tokens"]}  # noqa: E731
    if cap["value"] is not None:
        cap["over"] = [brief(r) for r in by_weight if r["tokens"] > cap["value"]]
    payload = {"agents": dict(sorted(agents.items())), "tasks": task_means,
               "heaviest_runs": [brief(r) for r in by_weight[:HEAVIEST]], "cap": cap, "runs": rows}
    payload["narrative"] = _aggregate_narrative(payload)
    return measurable(payload, version=VERSION)


def _aggregate_narrative(p: dict) -> str:
    bits = []
    for name, ag in p["agents"].items():
        share = (f"{pct(ag['measured_share'])} measured" if ag["measured_share"] is not None else "no tokens recorded")
        cost = f", {num(ag['cost_usd_total'], 4)} USD over {ag['cost_runs']} run(s) with a cost" if ag["cost_usd_total"] is not None else ", no cost recorded"
        bits.append(f"{name} spent {num(ag['total'])} tokens over {plural(ag['runs'], 'run')} ({num(ag['mean'])} per run, {share}{cost})")
    top = p["heaviest_runs"][0] if p["heaviest_runs"] else None
    sentence = "; ".join(bits)
    if top:
        sentence += f"; the heaviest run is {top['agent']} on {top['task']} ({top['run']}) at {num(top['tokens'])}"
    cap = p["cap"]
    if cap["value"] is not None:
        sentence += f"; {plural(len(cap['over']), 'run')} over the cap of {num(cap['value'])}"
    return sentence + "."


# ------------------------------------------------------------ registration

@_sections.register("pair", "budget", after=("rl",))
def _pair_section(report: dict, ctx: "_sections.PairContext") -> dict:
    return budget_pair(report, getattr(ctx, "a", None), getattr(ctx, "b", None))


@_sections.register("aggregate", "budget", after=("rl",))
def _aggregate_section(agg: dict, ctx: "_sections.AggregateContext") -> dict:
    extra = getattr(ctx, "extra", None) or {}
    return budget_aggregate(list(getattr(ctx, "trajectories", None) or []), extra.get("token_cap"))


__all__ = ["VERSION", "KINDS", "BURN_CAP", "TOP_STEPS", "HEAVIEST", "budget_run", "budget_pair", "budget_aggregate"]
