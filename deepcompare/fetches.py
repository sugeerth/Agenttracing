"""Every search, retrieval and read a run made, and what came back.

The third level of grain: a run is read as the fetches it made — the
``search``, ``retrieve``, ``read`` and ``tool_call`` steps — each with its
query, what it returned (characters, tokens, latency, an error), whether
it repeated an earlier fetch with the same name and input or was a
*retry* the harness re-ran (``attempt > 1`` on the step; where the trace
numbers no attempts the two cannot be told apart and ``retry_basis`` says
so), and whether what it returned was *used*. Used is a recorded signal or nothing: a step
that carries ``reward > 0`` or a ``quality`` label of ``good`` was used, a
recorded reward of zero or less or a label of ``bad`` was not, and a step
with neither carries ``null`` and says so — the reading never infers use
from the answer's text.

The search map draws the same records as a flow: each search, the
fetches that follow it before the next search, and every fetch whose use
is recorded to the answer; a fetch of unknown use has no edge and the map
counts how many. Per pair the two runs sit side by side with their delta;
per aggregate every run of every agent is summed, with a ledger row per
run so a reader of the aggregate alone still has the counts. Every number
is a count or a sum over recorded steps; a run that cannot be read is
*unmeasurable* with the reason, never empty.
"""

from __future__ import annotations

from typing import Any, Optional

from . import sections as _sections
from ._stats import finite, mean, rounded
from ._text import join_names, num, pct, plural, run_name
from .section import measurable, unmeasurable
from .trace import Step, Totals

VERSION = 1
#: the kinds of step that fetch something, in the order the counts list them
FETCH_KINDS = ("search", "retrieve", "read", "tool_call")
#: how much of a query the record keeps; ``query_chars`` carries the full length
QUERY_CHARS = 200
#: how many runs the aggregate names as heaviest
HEAVIEST = 8
#: the map's node kind per fetch kind
MAP_KIND = {"search": "query", "retrieve": "result", "read": "read", "tool_call": "call"}


# ------------------------------------------------------------ the run view

class RunView:
    """One run as the two third-level sections read it, whether it arrives
    as a :class:`~deepcompare.trace.Trajectory` or as a report side dict
    (``report["a"]``): the agent name, the typed steps, the totals, the
    harness block when a loader kept it, the run id and the outcome."""

    __slots__ = ("name", "steps", "totals", "harness", "run_id", "task_id", "trace_id", "success")

    def __init__(self, run: Any) -> None:
        if isinstance(run, dict):
            self.name = run_name(run)
            self.steps = [Step.from_dict(s, i) for i, s in enumerate(run.get("steps") or []) if isinstance(s, dict)]
            self.totals = Totals.from_dict(run.get("totals") or {})
            self.harness = run.get("harness")
            self.run_id = str(run.get("run_id") or "r1")
            self.task_id = str(((run.get("task") or {}).get("id")) or "")
            self.trace_id = str(run.get("trace_id") or "")
            outcome = run.get("outcome") or {}
            self.success = outcome.get("success") if isinstance(outcome.get("success"), bool) else None
        else:
            self.name = run_name(run)
            self.steps = list(getattr(run, "steps", None) or [])
            self.totals = getattr(run, "totals", None) or Totals()
            self.harness = getattr(run, "harness", None)
            self.run_id = str(getattr(run, "run_id", "r1"))
            self.task_id = str(getattr(getattr(run, "task", None), "id", "") or "")
            self.trace_id = str(getattr(run, "trace_id", "") or "")
            outcome = getattr(run, "outcome", None)
            self.success = outcome.success if outcome is not None and isinstance(outcome.success, bool) else None

    @property
    def synthetic(self) -> bool:
        return synthetic_of(self.harness)


def synthetic_of(harness: Any) -> bool:
    """True when the trace's harness block says the run is SYNTHETIC — the
    note starts with the word, or the adapter is the synthetic one."""
    if not isinstance(harness, dict):
        return False
    note = str(harness.get("note") or "")
    return note.upper().startswith("SYNTHETIC") or str(harness.get("adapter") or "") == "synthetic"


def _key(text: Any) -> str:
    return " ".join(str("" if text is None else text).split())


def attempt_of(step: Step) -> Optional[int]:
    """The step's recorded attempt number, or None where the trace does not
    number it. Only the harness writes this: it says *I re-ran this call*,
    which is a different fact from the agent asking for the same thing
    twice, and nothing here infers one from the other."""
    n = getattr(step, "attempt", None)
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def again_index(steps: list) -> tuple:
    """``(repeats, retries)``, each ``{index: earlier index}``, over the
    fetch steps. A step whose recorded attempt is greater than one is a
    **retry** — the harness re-executed that call after it failed — and
    names the previous attempt; any other fetch with the same name and
    (whitespace-normalised) input as an earlier one is a **repeat** and
    names the earliest. Where no step numbers its attempts the two cannot
    be told apart and everything falls to repeats, which is what the
    sections' ``retry_basis`` says out loud."""
    first: dict = {}
    prev: dict = {}
    repeats: dict = {}
    retries: dict = {}
    for st in steps:
        if st.type not in FETCH_KINDS:
            continue
        key = (st.name or "", _key(st.input))
        attempt = attempt_of(st)
        if attempt is not None and attempt > 1:
            if key in prev:
                retries[st.index] = prev[key]
        elif key in first:
            repeats[st.index] = first[key]
        first.setdefault(key, st.index)
        prev[key] = st.index
    return repeats, retries


def repeat_index(steps: list) -> dict:
    """``{index: earlier index}`` for every fetch step that repeats an
    earlier fetch with the same name and (whitespace-normalised) input;
    the earliest such fetch is the one named. Steps the harness numbered a
    retry are not repeats — see :func:`retry_index`."""
    return again_index(steps)[0]


def retry_index(steps: list) -> dict:
    """``{index: the previous attempt's index}`` for every fetch step the
    harness numbered ``attempt > 1``. A retry with no earlier step of the
    same name and input is absent here but still counted a retry: the
    number is the record, this map is only the link back."""
    return again_index(steps)[1]


def used_signal(step: Step) -> tuple:
    """``(used, basis)``: True/False from a recorded reward or a quality
    label, None with the reason when the step carries neither."""
    if finite(step.reward):
        return (step.reward > 0), "reward as recorded"
    if step.quality == "good":
        return True, "quality label good"
    if step.quality == "bad":
        return False, "quality label bad"
    if step.quality == "weak":
        return None, "quality label weak says neither"
    return None, "no reward or quality label recorded"


def _tokens(step: Step) -> Optional[int]:
    return int(step.tokens) if finite(step.tokens) and step.tokens >= 0 else None


# --------------------------------------------------------------- one run

def fetches_run(run: Any) -> dict:
    """The fetch records of one run, their counts, the volume that came
    back, the sources, the search map and a narrative."""
    try:
        view = RunView(run)
    except (ValueError, TypeError) as exc:
        return unmeasurable(f"the run cannot be read: {exc}", records=[], counts=_empty_counts(),
                            volume={"output_chars": 0, "tokens": 0}, sources=[], map={"nodes": [], "edges": []},
                            synthetic=False, narrative="The run cannot be read.")
    steps = view.steps
    if not steps:
        return unmeasurable("the run has no steps", records=[], counts=_empty_counts(),
                            volume={"output_chars": 0, "tokens": 0}, sources=[], map={"nodes": [], "edges": []},
                            synthetic=view.synthetic, narrative=f"{view.name}: no steps recorded, so nothing was fetched.")
    repeats, retries = again_index(steps)
    records: list = []
    for st in steps:
        if st.type not in FETCH_KINDS:
            continue
        used, basis = used_signal(st)
        query = _key(st.input)
        span_agent = st.span.get("agent") if isinstance(st.span, dict) else None
        records.append({
            "index": st.index, "kind": st.type, "name": st.name or "",
            "query": query if len(query) <= QUERY_CHARS else query[:QUERY_CHARS - 1] + "…",
            "query_chars": len(query), "output_chars": len(st.output or ""),
            "tokens": _tokens(st), "tokens_basis": st.tokens_basis,
            "latency_s": round(float(st.latency_s), 4) if finite(st.latency_s) and st.latency_s > 0 else None,
            "error": st.error, "effect": st.effect, "repeat_of": repeats.get(st.index),
            "attempt": attempt_of(st), "retry_of": retries.get(st.index),
            "used": used, "used_basis": basis, "span": span_agent,
        })
    counts = _counts(records)
    volume = {"output_chars": sum(r["output_chars"] for r in records),
              "tokens": sum(r["tokens"] for r in records if r["tokens"] is not None)}
    by_source: dict = {}
    for r in records:
        s = by_source.setdefault(r["name"] or "?", {"name": r["name"] or "?", "calls": 0, "errors": 0, "output_chars": 0})
        s["calls"] += 1
        s["errors"] += 1 if r["error"] is True else 0
        s["output_chars"] += r["output_chars"]
    sources = sorted(by_source.values(), key=lambda s: (-s["calls"], s["name"]))
    payload = {"records": records, "counts": counts, "volume": volume, "sources": sources,
               "map": _map(records, steps), "synthetic": view.synthetic,
               "retry_basis": _retry_basis(counts)}
    payload["narrative"] = _run_narrative(view.name, payload)
    return measurable(payload)


def _empty_counts() -> dict:
    return {"by_kind": {k: 0 for k in FETCH_KINDS}, "by_tool": {}, "total": 0, "errors": 0, "repeats": 0,
            "retries": 0, "attempts_numbered": 0, "used": 0, "unused": 0, "unknown_use": 0}


def _counts(records: list) -> dict:
    by_kind = {k: 0 for k in FETCH_KINDS}
    by_tool: dict = {}
    for r in records:
        by_kind[r["kind"]] += 1
        by_tool[r["name"] or "?"] = by_tool.get(r["name"] or "?", 0) + 1
    return {"by_kind": by_kind, "by_tool": dict(sorted(by_tool.items())), "total": len(records),
            "errors": sum(1 for r in records if r["error"] is True),
            "repeats": sum(1 for r in records if r["repeat_of"] is not None),
            "retries": sum(1 for r in records if (r["attempt"] or 1) > 1),
            "attempts_numbered": sum(1 for r in records if r["attempt"] is not None),
            "used": sum(1 for r in records if r["used"] is True),
            "unused": sum(1 for r in records if r["used"] is False),
            "unknown_use": sum(1 for r in records if r["used"] is None)}


def _retry_basis(counts: dict) -> str:
    """Why the retry count is what it is — including, when it is zero for
    lack of a record rather than for lack of retries, that it is not a
    measurement."""
    numbered, total = counts["attempts_numbered"], counts["total"]
    if not total:
        return "no fetch to number"
    if not numbered:
        return ("no fetch numbers its attempt, so a harness retry and the agent asking for the same thing twice "
                "cannot be told apart here; every same-input fetch is counted a repeat and retries reads 0 for "
                "want of a record, not for want of retries")
    if numbered < total:
        return (f"{numbered} of {total} fetches number their attempt; among those, attempt > 1 is a retry the "
                "harness ran and is not counted a repeat. The rest carry no number, so a retry among them is "
                "indistinguishable from a repeat")
    return ("every fetch numbers its attempt, so retries (attempt > 1, re-run by the harness) are separated from "
            "repeats (the agent fetching the same name and input again)")


def _label(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _map(records: list, steps: list) -> dict:
    """The search map: nodes for every fetch and the answer, ``yields`` /
    ``reads`` edges from each search to the fetches that follow it before
    the next search, ``reaches`` edges to the answer for every fetch whose
    use is recorded true."""
    nodes: list = []
    edges: list = []
    answer = steps[-1] if steps and steps[-1].type == "answer" else None
    last_search: Optional[str] = None
    for r in records:
        nid = f"{MAP_KIND[r['kind']]}{r['index']}"
        nodes.append({"id": nid, "kind": MAP_KIND[r["kind"]], "label": _label(r["query"] or r["name"] or r["kind"]),
                      "index": r["index"], "size": r["output_chars"]})
        if r["kind"] == "search":
            last_search = nid
        elif last_search is not None:
            edges.append({"from": last_search, "to": nid, "kind": "reads" if r["kind"] == "read" else "yields"})
    if answer is not None:
        nodes.append({"id": "answer", "kind": "answer", "label": _label(_key(answer.output) or "answer"),
                      "index": answer.index, "size": len(answer.output or "")})
        for r in records:
            if r["used"] is True:
                edges.append({"from": f"{MAP_KIND[r['kind']]}{r['index']}", "to": "answer", "kind": "reaches"})
    unknown = sum(1 for r in records if r["used"] is None)
    return {"nodes": nodes, "edges": edges, "unknown_use": unknown,
            "basis": ("a search yields the retrieve and tool calls that follow it and is read by the reads, until the next "
                      "search; a fetch reaches the answer when its use is recorded (reward > 0 or a quality label); "
                      + (f"{plural(unknown, 'fetch', 'fetches')} of unknown use {'has' if unknown == 1 else 'have'} no edge"
                         if unknown else "every fetch's use is recorded"))}


def _run_narrative(name: str, p: dict) -> str:
    c = p["counts"]
    if not c["total"]:
        return f"{name} fetched nothing: no search, retrieve, read or tool call in the run."
    kinds = [f"{n} {k.replace('_', ' ')}" for k, n in c["by_kind"].items() if n]
    tools = sorted(c["by_tool"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    parts = [f"{name} made {plural(c['total'], 'fetch', 'fetches')} ({join_names(kinds)}) through "
             + join_names(f"{t} ×{n}" for t, n in tools)
             + f", {num(p['volume']['output_chars'])} characters back"]
    if c["errors"] or c["repeats"] or c["retries"]:
        parts.append(join_names(x for x in [f"{plural(c['errors'], 'error')}" if c["errors"] else "",
                                            f"{plural(c['repeats'], 'repeat')} of an earlier fetch" if c["repeats"] else "",
                                            f"{plural(c['retries'], 'retry', 'retries')} the harness re-ran" if c["retries"] else ""] if x))
    elif not c["attempts_numbered"]:
        parts.append("no fetch numbers its attempt, so a retry here would read as a repeat")
    if c["used"] or c["unused"]:
        parts.append(f"{c['used']} recorded as used and {c['unused']} as not"
                     + (f", {c['unknown_use']} with no use signal" if c["unknown_use"] else ""))
    else:
        parts.append(f"no fetch carries a use signal (no reward > 0 or quality label), so the map reaches the answer from none")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- the pair

def _delta(a: dict, b: dict) -> dict:
    ca, cb = a["counts"], b["counts"]
    tools = sorted(set(ca["by_tool"]) | set(cb["by_tool"]))
    return {"total": ca["total"] - cb["total"],
            "by_kind": {k: ca["by_kind"][k] - cb["by_kind"][k] for k in FETCH_KINDS},
            "by_tool": {t: ca["by_tool"].get(t, 0) - cb["by_tool"].get(t, 0) for t in tools},
            "errors": ca["errors"] - cb["errors"], "repeats": ca["repeats"] - cb["repeats"],
            "retries": ca["retries"] - cb["retries"]}


def fetches_pair(report: dict, a: Any = None, b: Any = None) -> dict:
    """Both runs' fetches, the delta (A − B) and a narrative; the
    trajectories when the caller has them, else the report's sides."""
    fa = fetches_run(a if a is not None else report.get("a") or {})
    fb = fetches_run(b if b is not None else report.get("b") or {})
    name_a, name_b = run_name(report.get("a"), "A"), run_name(report.get("b"), "B")
    if not (fa["measurable"] and fb["measurable"]):
        return unmeasurable("at least one run cannot be read: " + "; ".join(
            f"{n}: {f['reason']}" for n, f in ((name_a, fa), (name_b, fb)) if not f["measurable"]),
            version=VERSION, a=fa, b=fb, delta=None, narrative="Fetches cannot be compared: at least one run cannot be read.")
    delta = _delta(fa, fb)
    ca, cb = fa["counts"], fb["counts"]
    sentence = (f"{name_a} made {plural(ca['total'], 'fetch', 'fetches')} against {name_b}'s {cb['total']}"
                f" ({ca['errors']} and {cb['errors']} errored, {ca['repeats']} and {cb['repeats']} repeated"
                + (f", {ca['retries']} and {cb['retries']} were retries" if ca["retries"] or cb["retries"] else "") + ")")
    widest = max(delta["by_tool"].items(), key=lambda kv: (abs(kv[1]), kv[0]), default=None)
    if widest and widest[1]:
        more = name_a if widest[1] > 0 else name_b
        sentence += f"; the widest gap is {widest[0]}, which {more} called {abs(widest[1])} more time(s)"
    return measurable({"a": fa, "b": fb, "delta": delta, "narrative": sentence + "."}, version=VERSION)


# ----------------------------------------------------------- the aggregate

def fetches_aggregate(trajectories: list) -> dict:
    """Every run of every agent summed, a ledger row per run, the mean
    fetches per task and agent, the heaviest runs and a narrative."""
    if not trajectories:
        return unmeasurable("no trajectories in the aggregate context", version=VERSION, agents={}, tasks={},
                            heaviest_runs=[], runs=[], narrative="No run reached the aggregate, so nothing was fetched.")
    rows: list = []
    agents: dict = {}
    tasks: dict = {}
    for traj in sorted(trajectories, key=lambda t: (t.agent.name, t.task.id, t.run_id)):
        f = fetches_run(traj)
        c = f["counts"]
        rows.append({"agent": traj.agent.name, "task": traj.task.id, "run": traj.run_id, "measurable": f["measurable"],
                     "fetches": c["total"], "by_kind": c["by_kind"], "by_tool": c["by_tool"], "errors": c["errors"], "repeats": c["repeats"],
                     "retries": c["retries"], "attempts_numbered": c["attempts_numbered"],
                     "used": c["used"], "unused": c["unused"], "unknown_use": c["unknown_use"],
                     "output_chars": f["volume"]["output_chars"], "synthetic": f["synthetic"]})
        ag = agents.setdefault(traj.agent.name, {"runs": 0, "fetches": 0, "by_kind": {k: 0 for k in FETCH_KINDS}, "by_tool": {},
                                                  "errors": 0, "repeats": 0, "retries": 0, "attempts_numbered": 0,
                                                  "used": 0, "unused": 0, "unknown_use": 0,
                                                  "used_share": None, "output_chars": 0, "synthetic": False})
        ag["runs"] += 1
        ag["fetches"] += c["total"]
        for k in FETCH_KINDS:
            ag["by_kind"][k] += c["by_kind"][k]
        for t, n in c["by_tool"].items():
            ag["by_tool"][t] = ag["by_tool"].get(t, 0) + n
        for k in ("errors", "repeats", "retries", "attempts_numbered", "used", "unused", "unknown_use"):
            ag[k] += c[k]
        ag["output_chars"] += f["volume"]["output_chars"]
        ag["synthetic"] = ag["synthetic"] or f["synthetic"]
        tasks.setdefault(traj.task.id, {}).setdefault(traj.agent.name, []).append(c["total"])
    for ag in agents.values():
        ag["by_tool"] = dict(sorted(ag["by_tool"].items()))
        signalled = ag["used"] + ag["unused"]
        ag["used_share"] = rounded(ag["used"] / signalled) if signalled else None
    task_means = {t: {a: rounded(mean(v)) for a, v in sorted(per.items())} for t, per in sorted(tasks.items())}
    heaviest = sorted(rows, key=lambda r: (-r["fetches"], r["agent"], r["task"], r["run"]))[:HEAVIEST]
    payload = {"agents": dict(sorted(agents.items())), "tasks": task_means,
               "heaviest_runs": [{"agent": r["agent"], "task": r["task"], "run": r["run"], "fetches": r["fetches"]} for r in heaviest],
               "runs": rows}
    payload["narrative"] = _aggregate_narrative(payload)
    return measurable(payload, version=VERSION)


def _aggregate_narrative(p: dict) -> str:
    bits = []
    for name, ag in p["agents"].items():
        share = f"{pct(ag['used_share'])} of the {ag['used'] + ag['unused']} with a use signal used" if ag["used_share"] is not None \
            else "no fetch carries a use signal"
        retried = f", {plural(ag['retries'], 'retry', 'retries')}" if ag["retries"] else ""
        bits.append(f"{name} made {plural(ag['fetches'], 'fetch', 'fetches')} over {plural(ag['runs'], 'run')} "
                    f"({plural(ag['errors'], 'error')}, {plural(ag['repeats'], 'repeat')}{retried}; {share})")
    top = p["heaviest_runs"][0] if p["heaviest_runs"] else None
    sentence = "; ".join(bits)
    if top:
        sentence += f"; the heaviest run is {top['agent']} on {top['task']} ({top['run']}) with {top['fetches']}"
    return sentence + "."


# ------------------------------------------------------------ registration

@_sections.register("pair", "fetches", after=("budget",))
def _pair_section(report: dict, ctx: "_sections.PairContext") -> dict:
    return fetches_pair(report, getattr(ctx, "a", None), getattr(ctx, "b", None))


@_sections.register("aggregate", "fetches", after=("budget",))
def _aggregate_section(agg: dict, ctx: "_sections.AggregateContext") -> dict:
    return fetches_aggregate(list(getattr(ctx, "trajectories", None) or []))


__all__ = ["VERSION", "FETCH_KINDS", "QUERY_CHARS", "HEAVIEST", "RunView", "synthetic_of", "repeat_index",
           "retry_index", "again_index", "attempt_of", "used_signal", "fetches_run", "fetches_pair", "fetches_aggregate"]
