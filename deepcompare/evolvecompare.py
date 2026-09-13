"""Two (or more) self-evolving lineages over the same tasks, compared as
evolution processes and not only as their final agents.

:mod:`deepcompare.evolve` reads one lineage g0 → g1 → … and says, per
step, whether it helped and which generation to keep. This module takes
two such lineages (``A`` first; the same order everywhere in the output)
and refuses the question "which is better" in favour of four questions
that have answers, each named by its axis:

* **peak** — whose *recommended* generation is better, through the
  ordinary pair machinery: the two generations' traces go through
  :func:`deepcompare.rl.rl_aggregate` with ``names=(a, b)`` and the block's
  ``stats.improvement`` (P(b > a) with its stratified-bootstrap interval,
  per task) and ``space`` (the behaviour distance) are read off it — the
  same block the page's Training view reads, so a reader can open it.
  "Separates" means the interval clears 0.5.
* **final** — the same, for the two *last* generations: a loop that keeps
  its latest self ships this one.
* **learning** — who got there faster. The learning curves on one axis,
  ``iqm_by_task`` (below), aligned by generation index and by cumulative
  episodes; a threshold; the generation and episode count at which each
  lineage first reached it; the area under each curve. Decided on the
  episodes to the threshold (fewer wins), by whoever reached it when only
  one did, by the area under the by-episodes curve when neither did.
* **process** — who evolved soundly: gamed, forgot and traded steps,
  steps accepted on noise (the improvement interval contained 0.5),
  protected paths touched, budgets breached, collapses, retention of
  once-solved tasks, behavioural drift, and which *kinds* of
  self-modification paid. Decided lexicographically: fewer gamed +
  protected touches, then fewer forgot, then higher retention, then
  fewer accepted on noise; a tie at every rung is ``null``. The raw
  per-lineage numbers are all in the output, so a reader can disagree
  with the order.

A lineage can win on peak and lose on process; the verdict names every
axis and the reading says both.

**The metric.** ``iqm_by_task`` is the task-stratified interquartile
mean of episode return: within each shared task the runs' IQM
(:func:`deepcompare.rlstats.iqm`, so at five runs the top and bottom run
are cut), then the plain mean over tasks, so every task weighs the same
whatever its run count and one task's outlier episode cannot move the
lineage's number. The interval is a stratified bootstrap — runs redrawn
with replacement within each task, every task keeping its run count —
with one fixed seed, the same rule ``rlstats`` uses. It is computed here
over the *shared* tasks only: a task one lineage never ran says nothing
about the other, so it is named in ``tasks.only`` and excluded, never
imputed. A lineage's own ``evolution`` section (over all its tasks, on
the pooled IQM) is carried in full beside it and may differ; the
``metric`` block says which number is which.

**The threshold** of the race is, by default, the midpoint between the
lowest generation-0 point and the highest recommended point across the
lineages, on ``iqm_by_task`` — the halfway mark between where the worst
lineage started and where the best one is worth keeping — and the output
states that with the two numbers it came from; ``--threshold`` overrides
it and the output says so. Every alignment is reported twice: by index
(where one lineage is shorter, the missing side is ``null``, never
padded) and by cumulative episodes, which differ from the index when the
runs per task differ.

**Retention** counts a task as solved at a generation when its pass rate
there is above :data:`SOLVED_RATE` (0.5: more than half its runs
passed); ``ever_solved`` are the shared tasks solved at any generation
and ``lost`` those solved once and not at the last generation.

**Cost.** ``peak`` and ``final`` are two ``rl_aggregate`` calls;
``by_generation.improvement`` (A@k vs B@k, the learning curve's honest
companion) is one more per aligned index, capped at
:data:`BY_GENERATION_CAP` generations and saying so past it; a pair
already built is reused, never rebuilt. Two seven-generation lineages of
210 traces compare in well under a minute.

Every number is a count or a sum over recorded episodes; a bootstrap is
labelled a bootstrap; every level that cannot be read — one lineage, no
shared task, a lineage with one generation, a generation with no traces —
says ``measurable: False`` and why. Determinism: every dict iterated for
output is sorted, every resample seeded, no timestamps. Pure standard
library.
"""

from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Optional

from .evolve import evolve, read_lineage
from .rl import GAMMA, rl_aggregate
from .rlstats import BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED, CONFIDENCE, iqm
from .trace import AgentInfo

VERSION = 1
#: the metric every curve, race and mechanism delta is read on
METRIC = "iqm_by_task"
#: the episode score the metric is an IQM of
SCORE = "return"
#: A@k vs B@k pair blocks are built for at most this many aligned generations
BY_GENERATION_CAP = 12
#: a task is solved at a generation when its pass rate there is above this
SOLVED_RATE = 0.5
#: a prompt, rule list or memory that falls below this fraction of its
#: parent's is a collapse — used only when the engine's step carries no
#: ``flags`` of its own
COLLAPSE_FRACTION = 0.5
COLLAPSE_SIZES = ("prompt_chars", "rules", "memory")
#: the four axes, in the order the verdict names them
AXES = ("peak", "final", "learning", "process")


# ---------------------------------------------------------------- formatting

def _num(v, places: int = 2) -> str:
    if v is None:
        return "—"
    if abs(v - round(v)) < 1e-9:
        text = f"{int(round(v))}"
    else:
        text = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _signed(v, places: int = 2) -> str:
    if v is None:
        return "—"
    text = _num(v, places)
    return text if v < 0 else f"+{text}"


def _pct(p) -> str:
    return "—" if p is None else f"{round(100 * p):.0f}%"


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _r(v, places: int = 4):
    return None if v is None else round(float(v), places)


def _mean(values: list):
    return sum(values) / len(values) if values else None


def _band_text(b: dict) -> str:
    return f"{_num(b.get('point'))} [{_num(b.get('lo'))}, {_num(b.get('hi'))}]"


# ---------------------------------------------------------------- the metric

def _interval(values: list, confidence: float = CONFIDENCE) -> tuple:
    """The percentile rule ``rlstats`` uses, so an interval here reads like
    one there."""
    if not values:
        return None, None
    v = sorted(values)
    n = len(v)
    tail = (1.0 - confidence) / 2.0
    return v[max(0, int(tail * n) - 1)], v[min(n - 1, int((1.0 - tail) * n))]


def _rng(label: str) -> random.Random:
    """One stream per statistic, seeded by its label, so adding a section
    never moves another's numbers."""
    return random.Random(f"agentdiff.evolvecompare:{BOOTSTRAP_SEED}:{label}")


def iqm_by_task(episodes: list, tasks, samples: int = BOOTSTRAP_SAMPLES, label: str = "") -> dict:
    """The task-stratified IQM of episode return over ``tasks``.

    ``episodes`` are rows with ``task_id`` and ``return`` (the shape
    ``aggregate["evolution"]["generations"][i]["episodes"]`` has); rows on
    other tasks, or without a return, are not counted. Returns ``{point,
    lo, hi, width, tasks_n, n, per_task, measurable, reason}`` where
    ``per_task`` is each task's own IQM and the point is their mean; the
    interval is a stratified bootstrap with ``samples`` resamples (runs
    redrawn within each task), seeded by ``label``.
    """
    wanted = set(tasks)
    by_task: dict = {}
    for e in episodes:
        tid = str(e.get("task_id"))
        v = e.get("return")
        if tid in wanted and isinstance(v, (int, float)) and not isinstance(v, bool):
            by_task.setdefault(tid, []).append(float(v))
    if not by_task:
        return {"measurable": False, "reason": "no episode on a shared task carries a return",
                "point": None, "lo": None, "hi": None, "width": None, "tasks_n": 0, "n": 0, "per_task": {}}
    ordered = sorted(by_task)
    per_task = {t: iqm(by_task[t]) for t in ordered}
    point = _mean([per_task[t] for t in ordered])
    rng = _rng(f"{METRIC}:{label}")
    boots = []
    for _ in range(max(0, samples)):
        total = 0.0
        for t in ordered:
            runs = by_task[t]
            n = len(runs)
            total += iqm([runs[rng.randrange(n)] for _ in range(n)])
        boots.append(total / len(ordered))
    lo, hi = _interval(boots)
    if lo is None:
        lo = hi = point
    return {"measurable": True, "reason": None, "point": _r(point), "lo": _r(lo), "hi": _r(hi),
            "width": _r(abs(hi - lo)), "tasks_n": len(ordered), "n": sum(len(v) for v in by_task.values()),
            "per_task": {t: _r(v) for t, v in per_task.items()}}


# ---------------------------------------------------------------- per lineage

def _labels(lineages: list) -> list:
    """A unique key per lineage: its family, else its directory name; a
    collision gets the directory name appended, then a counter."""
    out: list = []
    for i, ln in enumerate(lineages):
        fam = ln.get("family")
        base = fam if isinstance(fam, str) and fam else Path(str(ln.get("path") or f"lineage{i + 1}")).name
        label = base
        if label in out:
            label = f"{base} ({Path(str(ln.get('path') or '')).name})"
        k = 2
        while label in out:
            label = f"{base} #{k}"
            k += 1
        out.append(label)
    return out


def _gen_tasks(ev: dict) -> set:
    return {t for g in (ev.get("generations") or []) for t in (g.get("tasks") or [])}


def _renamed(trajectories: list, name: str) -> list:
    """Shallow copies of the trajectories under another policy name, for
    the pair block when two lineages name the same policy."""
    out = []
    for t in trajectories:
        c = copy.copy(t)
        c.agent = AgentInfo(name=name, model=t.agent.model, version=t.agent.version)
        out.append(c)
    return out


def _lineage_view(index: int, label: str, lineage: dict, ev: dict, shared: list, samples: int) -> dict:
    """Everything the comparison reads off one lineage, on the shared tasks."""
    gens_in = lineage.get("generations") or []
    gens_ev = ev.get("generations") or []
    shared_set = set(shared)
    rows: list = []
    episodes_cum = seconds_cum = tokens_cum = 0
    for i, g in enumerate(gens_ev):
        eps = [e for e in (g.get("episodes") or []) if str(e.get("task_id")) in shared_set]
        trajs = [t for t in (gens_in[i]["trajectories"] if i < len(gens_in) else []) if t.task.id in shared_set]
        band = iqm_by_task(eps, shared, samples=samples, label=f"{label}:{g['id']}")
        passes = sum(1 for e in eps if e.get("success") is True)
        by_task: dict = {}
        for e in eps:
            cell = by_task.setdefault(str(e.get("task_id")), [0, 0])
            cell[1] += 1
            cell[0] += 1 if e.get("success") is True else 0
        episodes_cum += len(eps)
        seconds_cum += sum(float(e.get("seconds") or 0.0) for e in eps)
        tokens_cum += sum(int(t.totals.input_tokens) + int(t.totals.output_tokens) for t in trajs)
        rows.append({
            "index": i, "id": g["id"], "point": band["point"], "lo": band["lo"], "hi": band["hi"],
            "measurable": band["measurable"], "reason": band["reason"],
            "pass_rate": _r(passes / len(eps)) if eps else None, "passes": passes, "episodes": len(eps),
            "episodes_cum": episodes_cum, "seconds_cum": _r(seconds_cum), "tokens_cum": tokens_cum,
            "pass_by_task": {t: _r(c[0] / c[1]) for t, c in sorted(by_task.items())},
            "runs_per_task": {t: c[1] for t, c in sorted(by_task.items())},
            "policy": g.get("policy") or (gens_in[i]["policy"] if i < len(gens_in) else None),
            "trajectories": trajs, "per_task_iqm": band["per_task"],
        })
    ids = [g["id"] for g in gens_ev]

    def where(gid) -> Optional[dict]:
        if gid is None or gid not in ids:
            return None
        return {"id": gid, "index": ids.index(gid)}

    rec = where((ev.get("recommended") or {}).get("id"))
    best = where((ev.get("best") or {}).get("id"))
    last = where(ids[-1]) if ids else None
    return {"index": index, "label": label, "family": ev.get("family"), "path": lineage.get("path"),
            "lineage": lineage, "evolution": ev, "rows": rows, "ids": ids,
            "recommended": rec, "best": best, "last": last,
            "generations_n": len(gens_ev),
            "episodes_n": sum(g.get("episodes_n") or 0 for g in gens_ev),
            "tasks_n": len(_gen_tasks(ev))}


# ---------------------------------------------------------------- pair blocks

def _pair(view_a: dict, ia: int, view_b: dict, ib: int, cache: dict, *, gamma: float, samples: int,
          shared: list) -> dict:
    """A@ia against B@ib through ``rl_aggregate``; the block is cached by
    the two generations, so peak, final and an aligned index that name
    the same pair share one computation."""
    key = (view_a["index"], ia, view_b["index"], ib)
    if key in cache:
        return cache[key]
    ra, rb = view_a["rows"][ia], view_b["rows"][ib]
    a_id = {"label": view_a["label"], "family": view_a["family"], "id": ra["id"], "index": ia}
    b_id = {"label": view_b["label"], "family": view_b["family"], "id": rb["id"], "index": ib}
    out: dict = {"measurable": False, "reason": None, "a": a_id, "b": b_id,
                 "improvement": {"point": None, "lo": None, "hi": None},
                 "orientation": "P(b > a): the chance a random run of b out-returns a random run of a, averaged "
                                "over the shared tasks, with its stratified-bootstrap interval",
                 "metric": {"name": METRIC, "a": ra["point"], "b": rb["point"],
                            "delta": _r(rb["point"] - ra["point"]) if ra["point"] is not None and rb["point"] is not None else None},
                 "iqm_pooled": {"a": None, "b": None},
                 "pass_rate": {"a": ra["pass_rate"], "b": rb["pass_rate"], "passes_a": ra["passes"],
                               "passes_b": rb["passes"], "episodes_a": ra["episodes"], "episodes_b": rb["episodes"]},
                 "per_task": {}, "behaviour_distance": None, "separates": None, "advisory": None, "reading": ""}
    ta, tb = ra["trajectories"], rb["trajectories"]
    na, nb = ra["policy"], rb["policy"]
    if not ta or not tb:
        out["reason"] = (f"{view_a['label']} {ra['id']} has no trace on a shared task" if not ta
                         else f"{view_b['label']} {rb['id']} has no trace on a shared task")
        out["reading"] = _pair_reading(out)
        cache[key] = out
        return out
    if not na or not nb:
        out["reason"] = "a generation has no policy name"
        out["reading"] = _pair_reading(out)
        cache[key] = out
        return out
    if na == nb:
        # two lineages of the same family: the traces name the same policy,
        # so B's copies are renamed by the lineage label for this block
        nb = f"{view_b['label']}@{rb['id']}"
        tb = _renamed(tb, nb)
    block = rl_aggregate([], list(ta) + list(tb), names=(na, nb), gamma=gamma, stats_metric=SCORE,
                         stats_samples=samples)
    stats = block.get("stats") or {}
    imp = stats.get("improvement") or {}
    agg = stats.get("aggregates") or {}
    out["iqm_pooled"] = {"a": _clean_band((agg.get(na) or {}).get("iqm")), "b": _clean_band((agg.get(nb) or {}).get("iqm"))}
    tasks = block.get("tasks") or {}
    per_task: dict = {}
    for tid in sorted(tasks):
        cell = tasks[tid]
        ma = (cell.get(na) or {}).get("mean_return")
        mb = (cell.get(nb) or {}).get("mean_return")
        if ma is None or mb is None:
            continue
        per_task[tid] = {"a": ma, "b": mb, "delta": cell.get("delta"), "p": (imp.get("per_task") or {}).get(tid),
                         "pass_a": ra["pass_by_task"].get(tid), "pass_b": rb["pass_by_task"].get(tid)}
    out["per_task"] = per_task
    out["behaviour_distance"] = ((block.get("space") or {}).get("distance") or {}).get("between")
    out["advisory"] = stats.get("advisory")
    if not imp.get("measurable"):
        out["reason"] = imp.get("reason") or "the improvement cannot be measured"
        out["reading"] = _pair_reading(out)
        cache[key] = out
        return out
    out["measurable"] = True
    out["improvement"] = {"point": imp.get("point"), "lo": imp.get("lo"), "hi": imp.get("hi")}
    lo, hi = imp.get("lo"), imp.get("hi")
    out["separates"] = (view_b["label"] if lo is not None and lo > 0.5 else
                        view_a["label"] if hi is not None and hi < 0.5 else None)
    out["reading"] = _pair_reading(out)
    cache[key] = out
    return out


def _clean_band(b) -> Optional[dict]:
    if not isinstance(b, dict):
        return None
    return {"point": b.get("point"), "lo": b.get("lo"), "hi": b.get("hi")}


def _pair_reading(p: dict) -> str:
    a, b = p["a"], p["b"]
    head = f"{a['label']} {a['id']} against {b['label']} {b['id']}"
    if not p["measurable"]:
        return f"{head}: cannot be read — {p['reason']}."
    imp, m, pr = p["improvement"], p["metric"], p["pass_rate"]
    core = (f"P({b['label']} {b['id']} > {a['label']} {a['id']}) {_pct(imp['point'])} [{_pct(imp['lo'])}, "
            f"{_pct(imp['hi'])}]; {METRIC} {_num(m['a'])} vs {_num(m['b'])} ({_signed(m['delta'])}); passes "
            f"{pr['passes_a']}/{pr['episodes_a']} vs {pr['passes_b']}/{pr['episodes_b']}")
    if p["separates"] == b["label"]:
        tail = f"every resample keeps {b['label']} {b['id']} ahead"
    elif p["separates"] == a["label"]:
        tail = f"every resample keeps {a['label']} {a['id']} ahead"
    else:
        tail = "the interval spans 50%, so these runs do not separate them"
    per = p["per_task"]
    up = sorted(t for t, c in per.items() if c["p"] is not None and c["p"] > 0.5)
    down = sorted(t for t, c in per.items() if c["p"] is not None and c["p"] < 0.5)
    tasks = (f"; per task, {b['label']} is ahead on {len(up)} of {_plural(len(per), 'task')} and behind on {len(down)}"
             + (f" ({', '.join(down)})" if down and len(down) <= 3 else "")) if per else ""
    dist = f"; behaviour distance {_num(p['behaviour_distance'])}" if p["behaviour_distance"] is not None else ""
    return f"{head}: {core}; {tail}{tasks}{dist}."


# ---------------------------------------------------------------- curves and the race

def _curve_rows(view: dict) -> list:
    keep = ("index", "id", "point", "lo", "hi", "pass_rate", "episodes_cum", "seconds_cum", "tokens_cum",
            "measurable", "reason", "episodes")
    return [{k: r[k] for k in keep} for r in view["rows"]]


def _auc(rows: list, x_key: str) -> dict:
    """Trapezoid over the consecutive measurable points of one curve, on
    the lineage's own x; a single point has no area."""
    pts = [(r[x_key], r["point"]) for r in rows if r["point"] is not None and r[x_key] is not None]
    if len(pts) < 2:
        return {"value": None, "points": len(pts), "span": None,
                "reason": "a curve of one point has no area" if len(pts) == 1 else "no measurable point"}
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        area += (x1 - x0) * (y0 + y1) / 2.0
    span = pts[-1][0] - pts[0][0]
    return {"value": _r(area), "points": len(pts), "span": _r(span),
            "mean_height": _r(area / span) if span else None, "reason": None}


def _threshold(views: list, override) -> dict:
    if override is not None:
        return {"metric": METRIC, "value": _r(override), "source": "--threshold", "measurable": True, "reason": None,
                "lowest_g0": None, "highest_recommended": None}
    g0 = [(v["rows"][0]["point"], v["label"], v["rows"][0]["id"]) for v in views
          if v["rows"] and v["rows"][0]["point"] is not None]
    recs = []
    for v in views:
        pick = v["recommended"] or v["best"]
        if pick is not None and v["rows"][pick["index"]]["point"] is not None:
            recs.append((v["rows"][pick["index"]]["point"], v["label"], pick["id"],
                         "recommended" if v["recommended"] else "best"))
    if not g0 or not recs:
        return {"metric": METRIC, "value": None, "source": None, "measurable": False,
                "reason": ("no lineage has a measurable generation-0 point" if not g0
                           else "no lineage has a recommended or best generation with a measurable point"),
                "lowest_g0": None, "highest_recommended": None}
    low = min(g0, key=lambda t: (t[0], t[1]))
    high = max(recs, key=lambda t: (t[0], t[1]))
    value = (low[0] + high[0]) / 2.0
    return {"metric": METRIC, "value": _r(value),
            "source": (f"the midpoint between the lowest generation-0 point ({_num(low[0])}, {low[1]} {low[2]}) and "
                       f"the highest {high[3]} point ({_num(high[0])}, {high[1]} {high[2]}) on {METRIC} over the "
                       f"shared tasks"),
            "measurable": True, "reason": None,
            "lowest_g0": {"value": low[0], "label": low[1], "id": low[2]},
            "highest_recommended": {"value": high[0], "label": high[1], "id": high[2], "which": high[3]}}


def _reached(rows: list, value: float) -> Optional[dict]:
    for r in rows:
        if r["point"] is not None and r["point"] >= value - 1e-9:
            return {"index": r["index"], "id": r["id"], "episodes_cum": r["episodes_cum"], "point": r["point"]}
    return None


def learning_verdict(reached: dict, auc: dict) -> tuple:
    """``(label | None, basis)`` under the contract's rule: fewest episodes
    to the threshold; the one that reached it when only one did; the
    larger by-episodes area when none did; a tie is None."""
    got = {k: v for k, v in reached.items() if v is not None}
    if got:
        if len(got) == 1:
            k = next(iter(got))
            return k, f"only {k} reached the threshold ({got[k]['id']}, {got[k]['episodes_cum']} episodes)"
        best = min(got.values(), key=lambda r: r["episodes_cum"])["episodes_cum"]
        winners = sorted(k for k, r in got.items() if r["episodes_cum"] == best)
        if len(winners) == 1:
            return winners[0], f"fewest episodes to the threshold ({best})"
        return None, f"{' and '.join(winners)} reached the threshold at the same episode count ({best})"
    areas = {k: (v.get("by_episodes") or {}).get("value") for k, v in auc.items()}
    areas = {k: v for k, v in areas.items() if v is not None}
    if not areas:
        return None, "no lineage reached the threshold and no curve has an area"
    top = max(areas.values())
    winners = sorted(k for k, v in areas.items() if abs(v - top) < 1e-9)
    if len(winners) == 1:
        return winners[0], f"no lineage reached the threshold; the larger area under the by-episodes curve ({_num(top)})"
    return None, f"no lineage reached the threshold and the areas tie ({_num(top)})"


# ---------------------------------------------------------------- process

def _step_flags(step: dict) -> list:
    flags = step.get("flags")
    if isinstance(flags, list):
        return [f for f in flags if isinstance(f, str)]
    findings = step.get("findings")
    return [f for f in findings if isinstance(f, str)] if isinstance(findings, list) else []


def _collapsed_by_size(ev: dict, step: dict) -> list:
    """The sizes that fell below :data:`COLLAPSE_FRACTION` of the parent's
    — the fallback when the engine's step carries no ``flags``."""
    sizes = {g["id"]: g.get("size") or {} for g in ev.get("generations") or []}
    a, b = sizes.get(step.get("from")) or {}, sizes.get(step.get("to")) or {}
    out = []
    for key in COLLAPSE_SIZES:
        va, vb = a.get(key), b.get(key)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va > 0 and vb < COLLAPSE_FRACTION * va:
            out.append(key)
    return out


def _process(view: dict, shared: list) -> dict:
    ev, rows = view["evolution"], view["rows"]
    steps = [s for s in (ev.get("steps") or []) if isinstance(s, dict)]
    verdicts: dict = {}
    for s in steps:
        v = s.get("verdict")
        verdicts[v if isinstance(v, str) else "unmeasurable"] = verdicts.get(v if isinstance(v, str) else "unmeasurable", 0) + 1
    engine_flags = any("flags" in s for s in steps)
    noise, collapsed, collapsed_rows = [], 0, []
    for s in steps:
        imp = ((s.get("effect") or {}).get("improvement") or {})
        lo, hi = imp.get("lo"), imp.get("hi")
        if (s.get("effect") or {}).get("measurable") and lo is not None and hi is not None and lo <= 0.5 <= hi:
            noise.append(s.get("to"))
        if engine_flags:
            hit = "collapsed" in _step_flags(s)
            what = []
        else:
            what = _collapsed_by_size(ev, s)
            hit = bool(what)
        if hit:
            collapsed += 1
            collapsed_rows.append({"step": s.get("index"), "to": s.get("to"), "what": what})
    integrity = ev.get("integrity") or {}
    touched = [t for t in (integrity.get("touched") or []) if isinstance(t, dict)]
    over = [o for o in ((integrity.get("growth") or {}).get("over_budget") or []) if isinstance(o, dict)]
    # retention on the shared tasks: solved = pass rate above SOLVED_RATE
    solved_at = [{t for t, p in r["pass_by_task"].items() if p is not None and p > SOLVED_RATE} for r in rows]
    ever = sorted(set().union(*solved_at)) if solved_at else []
    rec_i = (view["recommended"] or {}).get("index")
    last_set = solved_at[-1] if solved_at else set()
    rec_set = solved_at[rec_i] if rec_i is not None and rec_i < len(solved_at) else set()
    lost = [t for t in ever if t not in last_set]
    retention = {"rule": f"a task is solved at a generation when its pass rate there is above {SOLVED_RATE}",
                 "ever_solved": ever, "ever_solved_n": len(ever),
                 "solved_at_recommended": sorted(rec_set), "solved_at_last": sorted(last_set),
                 "at_last": _r(len(last_set & set(ever)) / len(ever)) if ever else None,
                 "at_recommended": _r(len(rec_set & set(ever)) / len(ever)) if ever else None,
                 "lost": lost, "never_solved": [t for t in shared if t not in ever]}
    origin = [d for d in ((ev.get("drift") or {}).get("from_origin") or []) if isinstance(d, dict)]
    drift_last = origin[-1].get("distance") if origin else None
    # which kind of self-modification paid: the steps grouped by mechanism
    # and read on this module's metric between the two generations
    point_of = {r["id"]: r["point"] for r in rows}
    mech: dict = {}
    for s in steps:
        m = s.get("mechanism") if isinstance(s.get("mechanism"), str) else "unknown"
        cell = mech.setdefault(m, {"steps": 0, "deltas": [], "improved": 0, "regressed": 0, "verdicts": {},
                                   "to": []})
        cell["steps"] += 1
        cell["to"].append(s.get("to"))
        pf, pt = point_of.get(s.get("from")), point_of.get(s.get("to"))
        if pf is not None and pt is not None:
            cell["deltas"].append(pt - pf)
        v = s.get("verdict") if isinstance(s.get("verdict"), str) else "unmeasurable"
        cell["verdicts"][v] = cell["verdicts"].get(v, 0) + 1
        if v == "improved":
            cell["improved"] += 1
        elif v == "regressed":
            cell["regressed"] += 1
    mechanisms = {}
    for m in sorted(mech):
        c = mech[m]
        mechanisms[m] = {"steps": c["steps"], "to": c["to"], "mean_delta": _r(_mean(c["deltas"])),
                         "deltas_n": len(c["deltas"]), "delta_positive": sum(1 for d in c["deltas"] if d > 1e-9),
                         "delta_negative": sum(1 for d in c["deltas"] if d < -1e-9),
                         "improved": c["improved"], "regressed": c["regressed"],
                         "verdicts": dict(sorted(c["verdicts"].items()))}
    paying = [(m, c) for m, c in mechanisms.items() if c["mean_delta"] is not None]
    best_mech = max(paying, key=lambda mc: (mc[1]["mean_delta"], -mc[1]["steps"], mc[0]))[0] if paying else None
    measurable_steps = [s for s in steps if isinstance(s.get("verdict"), str)]
    out = {
        "measurable": bool(steps), "reason": None if steps else f"{view['label']} has one generation, so no step to judge",
        "steps": len(steps), "measurable_steps": len(measurable_steps),
        **{v: verdicts.get(v, 0) for v in ("improved", "regressed", "flat", "gamed", "forgot", "traded")},
        "verdicts": dict(sorted(verdicts.items())),
        "accepted_on_noise": len(noise), "accepted_on_noise_steps": noise,
        "accepted_on_noise_rule": "a measurable step whose improvement interval contains 0.5 was kept without "
                                  "evidence it helped",
        "monotone": (ev.get("trajectory") or {}).get("monotone"),
        "protected_touched": len(touched),
        "protected_touched_paths": [{"step": t.get("step"), "from": t.get("from_gen"), "to": t.get("to_gen"),
                                     "path": t.get("path")} for t in touched],
        "over_budget": len(over), "over_budget_rows": over,
        "collapsed": collapsed, "collapsed_rows": collapsed_rows,
        "collapsed_basis": ("the engine's step flags" if engine_flags else
                            f"a prompt, rule list or memory under {COLLAPSE_FRACTION} of its parent's"),
        "retention": retention,
        "drift_from_origin_at_last": drift_last,
        "mechanisms": mechanisms, "best_paying_mechanism": best_mech,
        "score": [verdicts.get("gamed", 0) + len(touched), verdicts.get("forgot", 0),
                  -(retention["at_last"] if retention["at_last"] is not None else 0.0), len(noise)],
        "score_basis": "lexicographic, lower is better: [gamed + protected touched, forgot, −retention at last, "
                       "accepted on noise]",
    }
    out["reading"] = _process_reading(view, out)
    return out


def _process_reading(view: dict, p: dict) -> str:
    label = view["label"]
    if not p["measurable"]:
        return f"{label}: {p['reason']}."
    last = view["ids"][-1]
    ret = p["retention"]
    bits = [f"{label}: {_plural(p['steps'], 'step')} — {p['gamed']} gamed, {p['forgot']} forgot, {p['traded']} traded, "
            f"{p['improved']} improved, {p['regressed']} regressed, {p['flat']} flat; {p['accepted_on_noise']} accepted on noise"]
    if p["protected_touched"]:
        bits.append("protected paths touched: " + ", ".join(f"{t['path']} at {t['from']} → {t['to']}"
                                                            for t in p["protected_touched_paths"]))
    else:
        bits.append("no protected path touched")
    if p["over_budget"]:
        bits.append("over budget " + ", ".join(f"{o.get('gen')} {o.get('what')} {o.get('value')} > {o.get('budget')}"
                                              for o in p["over_budget_rows"]))
    if p["collapsed"]:
        bits.append(f"{_plural(p['collapsed'], 'collapse')} ({', '.join(str(c['to']) for c in p['collapsed_rows'])})")
    if ret["ever_solved_n"]:
        bits.append(f"retention {len(ret['solved_at_last'])} of {ret['ever_solved_n']} ever-solved tasks still solved at {last}"
                    + (f" (lost {', '.join(ret['lost'])})" if ret["lost"] else ""))
    else:
        bits.append("no shared task was ever solved (pass rate above 0.5)")
    if p["drift_from_origin_at_last"] is not None:
        bits.append(f"{last} sits {_num(p['drift_from_origin_at_last'])} from g0's behaviour")
    bm = p["best_paying_mechanism"]
    if bm is not None:
        c = p["mechanisms"][bm]
        bits.append(f"best-paying mechanism {bm} (n={c['steps']}, mean Δ{METRIC} {_signed(c['mean_delta'])}"
                    + (f"; {c['delta_positive']} of {c['deltas_n']} steps up" if c["deltas_n"] > 1 else "") + ")")
    return "; ".join(bits) + "."


def process_verdict(processes: dict) -> tuple:
    """``(label | None, basis)``: fewer gamed + protected touched, then
    fewer forgot, then higher retention, then fewer accepted on noise; a
    tie at every rung, or an unmeasurable process, is None."""
    if any(not p.get("measurable") for p in processes.values()):
        bad = sorted(k for k, p in processes.items() if not p.get("measurable"))
        return None, f"{', '.join(bad)} has no step to judge"
    rung_names = ("gamed + protected touched", "forgot", "retention at the last generation", "accepted on noise")
    labels = sorted(processes)
    alive = list(labels)
    for r, name in enumerate(rung_names):
        vals = {k: processes[k]["score"][r] for k in alive}
        low = min(vals.values())
        alive = [k for k in alive if abs(vals[k] - low) < 1e-9]
        if len(alive) == 1:
            k = alive[0]
            shown = {kk: (-processes[kk]["score"][r] if r == 2 else processes[kk]["score"][r]) for kk in labels}
            return k, f"decided on {name}: " + ", ".join(f"{kk} {_num(shown[kk])}" for kk in labels)
    return None, "tied on every rung (" + "; ".join(rung_names) + ")"


# ---------------------------------------------------------------- task race

def _task_race(views: list, shared: list) -> dict:
    per: dict = {}
    first_solver: dict = {}
    never: dict = {v["label"]: [] for v in views}
    for t in shared:
        per[t] = {}
        firsts = []
        for v in views:
            curve = [r["pass_by_task"].get(t) for r in v["rows"]]
            solved = [i for i, p in enumerate(curve) if p is not None and p > SOLVED_RATE]
            first = solved[0] if solved else None
            row = {"first_solved_index": first, "first_solved_id": v["ids"][first] if first is not None else None,
                   "first_solved_episodes_cum": v["rows"][first]["episodes_cum"] if first is not None else None,
                   "solved_at_last": bool(curve) and curve[-1] is not None and curve[-1] > SOLVED_RATE,
                   "pass_curve": curve}
            per[t][v["label"]] = row
            if first is None:
                never[v["label"]].append(t)
            else:
                firsts.append((row["first_solved_episodes_cum"], v["label"]))
        if firsts:
            least = min(f[0] for f in firsts)
            winners = sorted(l for e, l in firsts if e == least)
            first_solver[t] = winners[0] if len(winners) == 1 else None
        else:
            first_solver[t] = None
    return {"rule": f"solved = pass rate above {SOLVED_RATE}; first_solver is the lineage that solved the task at the "
                    f"fewest cumulative episodes, null on a tie or when no lineage solved it",
            "tasks": per, "first_solver": first_solver, "never_solved": never}


# ---------------------------------------------------------------- readings

def _curves_reading(views: list) -> str:
    parts = []
    for v in views:
        rows = [r for r in v["rows"] if r["point"] is not None]
        if not rows:
            parts.append(f"{v['label']}: no measurable point")
            continue
        top = max(rows, key=lambda r: (r["point"], -r["index"]))
        parts.append(f"{v['label']} {rows[0]['id']} {_num(rows[0]['point'])} → {rows[-1]['id']} {_num(rows[-1]['point'])} "
                     f"over {_plural(len(v['rows']), 'generation')} and {v['rows'][-1]['episodes_cum']} episodes, "
                     f"highest at {top['id']} ({_band_text(top)})")
    lengths = sorted({len(v["rows"]) for v in views})
    per_gen = sorted({r["episodes"] for v in views for r in v["rows"]})
    align = ("the lineages have the same length, so the index alignment is one-to-one" if len(lengths) == 1
             else f"the lineages differ in length ({', '.join(str(n) for n in lengths)}), so past the shorter one "
                  f"the index alignment has a null side")
    eps = ("every generation spends the same number of episodes, so the index and episode alignments agree"
           if len(per_gen) == 1 else f"generations spend {per_gen[0]}–{per_gen[-1]} episodes, so the two alignments differ")
    return "; ".join(parts) + f"; {align}; {eps}; the interval is a stratified bootstrap over the shared tasks."


def _race_reading(race: dict, views: list) -> str:
    th = race["threshold"]
    if not race["measurable"]:
        return f"no race: {race['reason']}."
    bits = [f"threshold {_num(th['value'])} on {METRIC} ({th['source']})"]
    for v in views:
        r = race["reached"].get(v["label"])
        auc = race["auc"][v["label"]]
        if r is None:
            bits.append(f"{v['label']} never reached it (highest {_num(max((x['point'] for x in v['rows'] if x['point'] is not None), default=None))})")
        else:
            bits.append(f"{v['label']} reached it at {r['id']} (index {r['index']}, {r['episodes_cum']} episodes, {_num(r['point'])})")
        if auc["by_episodes"]["value"] is not None:
            bits[-1] += (f"; area under the curve {_num(auc['by_index']['value'])} by index, "
                         f"{_num(auc['by_episodes']['value'])} by episodes")
    lengths = {v["label"]: v["rows"][-1]["episodes_cum"] if v["rows"] else 0 for v in views}
    if len(set(lengths.values())) > 1:
        bits.append("the areas are over each lineage's own length, so the longer series has more room under it")
    return "; ".join(bits) + "."


def _by_generation_reading(rows: list, a: str, b: str) -> str:
    said = []
    for r in rows:
        imp = r.get("improvement")
        if imp and imp.get("point") is not None:
            said.append(f"@{r['index']} {_pct(imp['point'])} [{_pct(imp['lo'])}, {_pct(imp['hi'])}]")
    if not said:
        return "no aligned generation has a measurable improvement."
    seps = [r for r in rows if r.get("separates")]
    return (f"P({b}@k > {a}@k) by aligned index: " + ", ".join(said)
            + (f"; the runs separate them at index {', '.join(str(r['index']) for r in seps)} "
               f"({', '.join(r['separates'] for r in seps)} ahead)" if seps else "; no index separates them") + ".")


def _verdict_reading(verdict: dict, peak: dict, final: dict, race: dict, processes: dict, views: list) -> str:
    labels = [v["label"] for v in views]
    parts = []
    for axis in ("peak", "final"):
        p = peak if axis == "peak" else final
        w = verdict[axis]
        if not p["measurable"]:
            parts.append(f"{axis}: not readable — {p['reason']}")
            continue
        imp = p["improvement"]
        parts.append(f"{axis}: {w or 'no separation'} — {p['a']['label']} {p['a']['id']} vs {p['b']['label']} "
                     f"{p['b']['id']}, P(b > a) {_pct(imp['point'])} [{_pct(imp['lo'])}, {_pct(imp['hi'])}]"
                     + (f"; {p['pairs_reading']}" if p.get("pairs_reading") else ""))
    parts.append(f"learning: {verdict['learning'] or 'no separation'} — {verdict['learning_basis']}")
    parts.append(f"process: {verdict['process'] or 'no separation'} — {verdict['process_basis']}")
    wins = {l: [ax for ax in AXES if verdict[ax] == l] for l in labels}
    summary = ", ".join(f"{l} takes {', '.join(w) if w else 'no axis'}" for l, w in wins.items())
    return "; ".join(parts) + f". {summary}."


def _advisory(views: list, shared: list) -> str:
    from .reliability import RUNS_FLOOR_STRUCTURED, runs_advisory
    counts = [n for v in views for r in v["rows"] for t, n in r["runs_per_task"].items() if t in shared]
    base = runs_advisory(counts)
    msg = base.get("message") or "no runs, so nothing to advise on."
    n_min = base.get("n_min")
    tail = (" Every interval in this section is a stratified bootstrap over the runs recorded here on the shared "
            "tasks, resampled within each task; it says how much these runs' statistic moves when they are "
            "redrawn, not what a population of runs never made would show.")
    if isinstance(n_min, int) and n_min < RUNS_FLOOR_STRUCTURED:
        tail += (f" At {_plural(n_min, 'run')} per task the intervals are wide by construction: read 'does not "
                 f"separate' as exactly that, never as 'equal', and read a race decided by one generation as a "
                 f"description of these episodes.")
    return f"[{base.get('tier')}] {msg}{tail}"


def _narrative(out: dict, views: list) -> str:
    if not out["measurable"]:
        return out["reason"]
    names = " and ".join(f"{v['label']} ({_plural(v['generations_n'], 'generation')}, {v['episodes_n']} episodes)"
                         for v in views)
    parts = [f"{names} over {_plural(len(out['tasks']['shared']), 'shared task')}"]
    only = {k: v for k, v in out["tasks"]["only"].items() if v}
    if only:
        parts.append("excluded as unshared: " + "; ".join(f"{k} {', '.join(v)}" for k, v in sorted(only.items())))
    parts.append(out["verdict"]["reading"].rstrip("."))
    for v in views:
        p = out["process"][v["label"]]
        if p.get("best_paying_mechanism"):
            c = p["mechanisms"][p["best_paying_mechanism"]]
            parts.append(f"{v['label']}'s best-paying mechanism is {p['best_paying_mechanism']} "
                         f"(n={c['steps']}, mean Δ {_signed(c['mean_delta'])})")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- the section

def _empty(reason: str, lineages: Optional[list] = None, tasks: Optional[dict] = None) -> dict:
    return {"version": VERSION, "measurable": False, "reason": reason, "lineages": lineages or [],
            "tasks": tasks or {"shared": [], "only": {}}, "metric": METRIC,
            "metric_definition": _metric_definition(), "curves": {"measurable": False, "reason": reason},
            "race": {"measurable": False, "reason": reason}, "peak": {"measurable": False, "reason": reason},
            "final": {"measurable": False, "reason": reason}, "by_generation": [],
            "process": {}, "task_race": {"measurable": False, "reason": reason},
            "verdict": {**{ax: None for ax in AXES}, "reading": reason}, "narrative": reason, "advisory": ""}


def _metric_definition() -> str:
    return (f"{METRIC}: within each shared task the interquartile mean of the runs' episode {SCORE}, then the mean "
            f"over tasks; interval = stratified bootstrap (runs redrawn within each task, {BOOTSTRAP_SAMPLES} "
            f"resamples by default, fixed seed); computed over the shared tasks only, so it can differ from the "
            f"pooled iqm in each lineage's own evolution section")


def _lineage_entry(v: dict) -> dict:
    return {"label": v["label"], "family": v["family"], "path": v["path"], "generations_n": v["generations_n"],
            "episodes_n": v["episodes_n"], "tasks_n": v["tasks_n"], "recommended": v["recommended"],
            "best": v["best"], "last": v["last"], "evolution": v["evolution"]}


def evolution_compare(lineages: list, evolutions: list, *, threshold=None, samples: int = BOOTSTRAP_SAMPLES,
                      gamma: float = GAMMA, by_generation_cap: int = BY_GENERATION_CAP) -> dict:
    """``aggregate["evolution_compare"]`` for lineages already read.

    ``lineages`` are :func:`deepcompare.evolve.read_lineage` results and
    ``evolutions`` their :func:`deepcompare.evolve.evolve` sections, in
    the same order (A first). ``threshold`` overrides the race threshold;
    ``samples`` is the bootstrap resamples for every interval built here
    (the pair blocks use it too); ``by_generation_cap`` bounds the A@k vs
    B@k pair blocks. Returns ``measurable: False`` with a reason for one
    lineage, an unreadable lineage, or no shared task.
    """
    if len(lineages) != len(evolutions):
        raise ValueError("lineages and evolutions must pair up")
    labels = _labels(lineages)
    if len(lineages) < 2:
        entries = []
        if lineages:
            v = _lineage_view(0, labels[0], lineages[0], evolutions[0], sorted(_gen_tasks(evolutions[0])), samples)
            entries.append(_lineage_entry(v))
        return _empty("one lineage; a comparison needs two", entries)
    bad = [f"{labels[i]}: {ev.get('reason')}" for i, ev in enumerate(evolutions) if not ev.get("measurable")]
    if bad:
        return _empty("a lineage cannot be read — " + "; ".join(bad),
                      [{"label": labels[i], "family": ev.get("family"), "path": lineages[i].get("path"),
                        "generations_n": len(ev.get("generations") or []), "episodes_n": 0, "tasks_n": 0,
                        "recommended": None, "best": None, "last": None, "evolution": ev}
                       for i, ev in enumerate(evolutions)])
    task_sets = [_gen_tasks(ev) for ev in evolutions]
    shared = sorted(set.intersection(*task_sets))
    tasks = {"shared": shared, "only": {labels[i]: sorted(task_sets[i] - set(shared)) for i in range(len(labels))},
             "rule": "tasks are matched by id; a task not run by every lineage is excluded, never imputed"}
    views = [_lineage_view(i, labels[i], lineages[i], evolutions[i], shared, samples) for i in range(len(lineages))]
    entries = [_lineage_entry(v) for v in views]
    if not shared:
        return _empty("no task is shared by every lineage; tasks are matched by id and never imputed", entries, tasks)

    out: dict = {"version": VERSION, "measurable": True, "reason": None, "lineages": entries, "tasks": tasks,
                 "metric": METRIC, "score": SCORE, "metric_definition": _metric_definition()}

    # curves: one axis, two alignments
    by_index = {v["label"]: _curve_rows(v) for v in views}
    out["curves"] = {"measurable": True, "reason": None, "metric": METRIC, "by_index": by_index,
                     "by_episodes": {k: [dict(r, x=r["episodes_cum"]) for r in rows] for k, rows in by_index.items()},
                     "alignment": {"by_index": "generation index; a shorter lineage has no row past its length",
                                   "by_episodes": "cumulative episodes on the shared tasks, which differ from the "
                                                  "index when the runs per task differ"},
                     "reading": ""}
    out["curves"]["reading"] = _curves_reading(views)

    # the race
    th = _threshold(views, threshold)
    short = [v["label"] for v in views if v["generations_n"] < 2]
    race: dict = {"measurable": th["measurable"] and not short, "threshold": th,
                  "reached": {v["label"]: None for v in views},
                  "auc": {v["label"]: {"by_index": _auc(v["rows"], "index"), "by_episodes": _auc(v["rows"], "episodes_cum")}
                          for v in views},
                  "reason": None, "reading": ""}
    if short:
        race["reason"] = f"{', '.join(short)} has one generation, so there is no curve to race"
    elif not th["measurable"]:
        race["reason"] = th["reason"]
    if race["measurable"]:
        race["reached"] = {v["label"]: _reached(v["rows"], th["value"]) for v in views}
        if all(r is not None and r["index"] == 0 for r in race["reached"].values()):
            race["note"] = ("every lineage is at or past the threshold at generation 0, so the race says nothing "
                            "about learning above the baseline")
    race["reading"] = _race_reading(race, views)
    out["race"] = race

    # peak and final through the pair machinery, A vs B first, then every other pair
    cache: dict = {}
    a, b = views[0], views[1]

    def axis(which: str) -> dict:
        picks = [(v, v["recommended"] if which == "peak" else v["last"]) for v in views]
        missing = [v["label"] for v, p in picks if p is None]
        if missing:
            base = {"measurable": False,
                    "reason": (f"{', '.join(missing)} has no recommended generation" if which == "peak"
                               else f"{', '.join(missing)} has no generation"),
                    "a": {"label": a["label"], "family": a["family"], "id": None, "index": None},
                    "b": {"label": b["label"], "family": b["family"], "id": None, "index": None},
                    "improvement": {"point": None, "lo": None, "hi": None}, "metric": {"name": METRIC, "a": None, "b": None, "delta": None},
                    "pass_rate": {"a": None, "b": None}, "per_task": {}, "separates": None, "pairs": [], "reading": ""}
            base["reading"] = f"{which}: cannot be read — {base['reason']}."
            return base
        primary = dict(_pair(a, picks[0][1]["index"], b, picks[1][1]["index"], cache, gamma=gamma, samples=samples,
                             shared=shared))
        pairs = []
        for i in range(len(views)):
            for j in range(i + 1, len(views)):
                blk = _pair(views[i], picks[i][1]["index"], views[j], picks[j][1]["index"], cache, gamma=gamma,
                            samples=samples, shared=shared)
                pairs.append({k: blk[k] for k in ("measurable", "reason", "a", "b", "improvement", "metric",
                                                  "pass_rate", "separates", "behaviour_distance", "reading")})
        primary["pairs"] = pairs
        primary["basis"] = ("recommended generation vs recommended generation" if which == "peak"
                            else "last generation vs last generation")
        if len(views) > 2:
            primary["pairs_reading"] = "; ".join(p["reading"].rstrip(".") for p in pairs[1:])
        return primary

    out["peak"], out["final"] = axis("peak"), axis("final")

    # aligned by index: A@k vs B@k, capped
    longest = max(v["generations_n"] for v in views)
    rows = []
    for k in range(longest):
        row: dict = {"index": k}
        for side, v in (("a", a), ("b", b)):
            r = v["rows"][k] if k < len(v["rows"]) else None
            row[side] = ({"id": r["id"], "point": r["point"], "lo": r["lo"], "hi": r["hi"], "pass_rate": r["pass_rate"],
                          "episodes_cum": r["episodes_cum"]} if r else None)
        if len(views) > 2:
            row["others"] = {v["label"]: ({"id": v["rows"][k]["id"], "point": v["rows"][k]["point"], "lo": v["rows"][k]["lo"],
                                           "hi": v["rows"][k]["hi"], "pass_rate": v["rows"][k]["pass_rate"]}
                                          if k < len(v["rows"]) else None) for v in views[2:]}
        if row["a"] is None or row["b"] is None:
            row.update(improvement=None, behaviour_distance=None, separates=None,
                       reason="one lineage has no generation at this index")
        elif k >= by_generation_cap:
            row.update(improvement=None, behaviour_distance=None, separates=None,
                       reason=f"past the cap of {by_generation_cap} aligned generations; peak and final still compare")
        else:
            blk = _pair(a, k, b, k, cache, gamma=gamma, samples=samples, shared=shared)
            row.update(improvement=blk["improvement"] if blk["measurable"] else None,
                       behaviour_distance=blk["behaviour_distance"], separates=blk["separates"],
                       reason=None if blk["measurable"] else blk["reason"])
        rows.append(row)
    out["by_generation"] = rows
    out["by_generation_note"] = (f"improvement is P({b['label']}@k > {a['label']}@k) through rl_aggregate, built for at "
                                 f"most {by_generation_cap} aligned generations; behaviour_distance is the rlspace "
                                 f"between-policy distance of the same pair")
    out["by_generation_reading"] = _by_generation_reading(rows, a["label"], b["label"])

    # process, task race, verdict
    out["process"] = {v["label"]: _process(v, shared) for v in views}
    out["task_race"] = _task_race(views, shared)
    learning, learning_basis = learning_verdict(race["reached"], race["auc"]) if race["measurable"] else (None, race["reason"])
    process_w, process_basis = process_verdict(out["process"])
    verdict = {"peak": _axis_winner(out["peak"], views), "final": _axis_winner(out["final"], views),
               "learning": learning, "process": process_w,
               "learning_basis": learning_basis, "process_basis": process_basis,
               "peak_basis": _axis_basis(out["peak"]), "final_basis": _axis_basis(out["final"]),
               "rule": {"peak": "the recommended generations' improvement interval clears 0.5",
                        "final": "the last generations' improvement interval clears 0.5",
                        "learning": "fewest episodes to the threshold; the one that reached it; else the larger "
                                    "by-episodes area; ties null",
                        "process": "fewer gamed + protected touched, then fewer forgot, then higher retention, "
                                   "then fewer accepted on noise; ties null"}}
    verdict["reading"] = _verdict_reading(verdict, out["peak"], out["final"], race, out["process"], views)
    out["verdict"] = verdict
    out["advisory"] = _advisory(views, shared)
    out["narrative"] = _narrative(out, views)
    return out


def _axis_winner(block: dict, views: list) -> Optional[str]:
    """The lineage that separates ahead of every other one on this axis."""
    if not block.get("measurable"):
        return None
    pairs = block.get("pairs") or []
    if len(views) == 2:
        return block.get("separates")
    for v in views:
        beats = 0
        for p in pairs:
            if v["label"] in (p["a"]["label"], p["b"]["label"]) and p["separates"] == v["label"]:
                beats += 1
        if beats == len(views) - 1:
            return v["label"]
    return None


def _axis_basis(block: dict) -> str:
    if not block.get("measurable"):
        return block.get("reason") or "not readable"
    if block.get("separates"):
        return f"the interval clears 0.5 in favour of {block['separates']}"
    return "the interval spans 0.5"


def compare_lineages(paths: list, *, layout: str = "native", metric: str = "return", samples: int = BOOTSTRAP_SAMPLES,
                     gamma: float = GAMMA, threshold=None, reports: Optional[list] = None,
                     evolutions: Optional[list] = None) -> dict:
    """Read every lineage, evolve each, compare: the section for ``paths``
    (A first). ``evolutions`` may carry an already computed section for
    any position (None elsewhere) so the CLI reuses lineage A's; ``reports``
    are passed to lineage A's evolve for the timeline flags. ``metric``
    and ``samples`` are the single-lineage engine's."""
    lineages, evs = [], []
    for i, p in enumerate(paths):
        ln = read_lineage(p, layout)
        given = evolutions[i] if evolutions and i < len(evolutions) else None
        lineages.append(ln)
        evs.append(given if given is not None else
                   evolve(ln, metric=metric, samples=samples, gamma=gamma, reports=reports if i == 0 else None))
    return evolution_compare(lineages, evs, threshold=threshold, samples=samples, gamma=gamma)


__all__ = ["compare_lineages", "evolution_compare", "iqm_by_task", "learning_verdict", "process_verdict",
           "VERSION", "METRIC", "SCORE", "BY_GENERATION_CAP", "SOLVED_RATE", "COLLAPSE_FRACTION", "AXES"]
