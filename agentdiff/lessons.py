"""Lessons: what the traces teach, and whether it holds on traces it was not learned from.

Every other corpus reading on the page describes the corpus it was given.
This one *learns* from it, and then does the thing that separates learning
from description: it checks the lesson on runs the lesson was not drawn
from, and it keeps a ledger, so the next corpus either confirms what this
one taught or retires it.

**What a lesson is.**  A property of a run — something the trace shows
about itself, like an error it never came back to, or a behaviour, like
never verifying its evidence — that travels with the run being wrong.
Each candidate is scored as a difference in the wrong-rate between runs
that have it and runs that do not, *within the same task* (Mantel-Haenszel
pooling, so a hard task that provokes the property and the failure alike
cannot manufacture the association), falling back to the raw difference
only when no task has runs on both sides.

**What "wrong" means.**  Where the golden set names a task's known failure
(``failure_mode``, and ``failure_mode_agents`` for which runs carry it) or
marks it ``known_correct``, that is the truth: it is the only label here
that does not come from the run itself.  Every other run falls back to its
own outcome.  The output says how many runs were labelled each way.

**How it is tested.**  The tasks are split in two by a hash of their id —
fixed, so the split never moves to suit a result, and by task, so no task
sits on both sides.  A candidate is a lesson when it separates the runs on
at least one half; it **holds** only when it separates them on both, in
the same direction.  A lesson drawn from one half and not seen in the
other is reported as that, beside the ones that held, at the same size:
with sixteen candidates tried, one of them will look strong on half a
corpus by chance, and the replication is what tells that one apart.

**The ledger.**  A lesson that held on this corpus is a hypothesis about
the next one.  Given a ledger from earlier batches, every lesson in it is
re-scored on the whole of this corpus — which none of them was learned
from — and marked held again, weakened, reversed or untestable.  A corpus
is fingerprinted, and the same corpus read twice is not counted twice:
running the batch again is not new evidence.

**These are associations, not causes.**  The honest use is where to look
and what to test, and every lesson carries its counts so the reader can
see how small the sample behind it is.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

from . import excerpt
from ._text import plural
from .attributes import ATTRIBUTES
from .trace import Trajectory

#: runs needed on each side of a split before a difference is worth reading
MIN_GROUP = 2
#: difference in wrong-rate at or above which a candidate is a lesson
LESSON = 0.25
#: difference a lesson must still show on the other half, in the same
#: direction, to count as held — lower than LESSON because the other half
#: is a test, not a search: it was not picked for being strong
HOLDS = 0.15
#: ledger format version
LEDGER_VERSION = 1

#: what each trace sign says, in the words a reader needs
SIGNS = {
    "unrecovered_error": "the run left an error it never came back to",
    "shipped_before_check": "the run shipped before the checks that came after it",
    "skipped_beat": "a tool the run used on a rhythm stopped being used",
    "redundant_stretch": "the run spent a stretch producing nothing new",
    "policy_breach": "the run did something the policy forbids",
    "unverified_write": "the run's last write was never checked",
    "recovered_error": "the run hit an error and recovered from it",
    "blind_write": "the run wrote before it had read anything",
    "cycle": "the run repeated a call and got the same result back",
    "no_information": "a step returned exactly what an earlier one had",
}


#: attributes that read a label someone wrote on the trace rather than
#: something the run did.  A step annotated "bad" separates wrong runs
#: perfectly when the annotator knew which runs were wrong — that is the
#: annotator's knowledge, not a lesson about agents — so these are listed
#: as their own kind and never lead the narrative.
ANNOTATIONS = frozenset({"poor_quality_step"})


def candidates() -> dict:
    """name -> (predicate(trajectory, policy), phrasing, source).

    Two families, named apart on the page because they are different
    kinds of claim: *trace signs* are the page's own marks (what
    :func:`excerpt.notable_steps` finds from the trace and the policy
    alone, never the golden set), *behaviours* are the corpus attributes
    of :mod:`attributes`, and *annotations* are the attributes that read
    a label on the trace (see :data:`ANNOTATIONS`).
    """
    out: dict = {}
    for kind in sorted(excerpt.WEIGHTS):
        out[f"sign:{kind}"] = (
            (lambda k: lambda t, p, marks: k in marks)(kind),
            SIGNS.get(kind, kind.replace("_", " ")), "trace sign")
    for name, (predicate, phrasing) in sorted(ATTRIBUTES.items()):
        source = "annotation" if name in ANNOTATIONS else "behaviour"
        out[f"{source}:{name}"] = (
            (lambda f: lambda t, p, marks: f(t))(predicate), phrasing, source)
    return out


def _truth(traj: Trajectory, gtasks: dict) -> tuple:
    """(wrong, basis) — the golden label where there is one, else the outcome."""
    task = gtasks.get(traj.task.id) or {}
    if task.get("failure_mode"):
        carriers = [str(a) for a in (task.get("failure_mode_agents") or [])]
        return (not carriers or traj.agent.name in carriers), "golden"
    if task.get("known_correct"):
        return False, "golden"
    return (not traj.outcome.success), "outcome"


def fingerprint(trajectories: Iterable[Trajectory]) -> str:
    """A stable name for a corpus: the same runs give the same name."""
    keys = sorted(f"{t.task.id}\x1f{t.agent.name}\x1f{t.trace_id}\x1f{len(t.steps)}\x1f{bool(t.outcome.success)}"
                  for t in trajectories)
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:12]


def _half(task_id: str) -> int:
    return int(hashlib.sha256(task_id.encode("utf-8")).hexdigest(), 16) % 2


def split(task_ids: Iterable[str]) -> list:
    """The two halves, by task, fixed by the ids alone.

    A hash split can put every task on one side of a small corpus; when it
    leaves a half empty the tasks are dealt alternately in hash order
    instead, which is just as fixed and never empty for two or more tasks.
    """
    ids = sorted(set(task_ids))
    halves = [[t for t in ids if _half(t) == 0], [t for t in ids if _half(t) == 1]]
    if len(ids) >= 2 and not (halves[0] and halves[1]):
        ranked = sorted(ids, key=lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest())
        halves = [ranked[0::2], ranked[1::2]]
    return [sorted(h) for h in halves]


def _effect(rows: list) -> dict:
    """Wrong-rate difference, pooled within task when any task allows it.

    ``rows`` is ``[(task, has, wrong)]`` with ``has`` possibly None (not
    measurable for that run, excluded).
    """
    rows = [r for r in rows if r[1] is not None]
    w_n = sum(1 for _, h, _w in rows if h)
    w_bad = sum(1 for _, h, w in rows if h and w)
    o_n = sum(1 for _, h, _w in rows if not h)
    o_bad = sum(1 for _, h, w in rows if not h and w)
    measurable = min(w_n, o_n) >= MIN_GROUP
    raw = (w_bad / w_n if w_n else 0.0) - (o_bad / o_n if o_n else 0.0)
    by_task: dict = {}
    for task, has, wrong in rows:
        by_task.setdefault(task, []).append((has, wrong))
    num = den = 0.0
    strata = agree = against = 0
    for task in sorted(by_task):
        grp = by_task[task]
        a = [w for h, w in grp if h]
        b = [w for h, w in grp if not h]
        if not a or not b:
            continue
        weight = len(a) * len(b) / len(grp)
        diff = sum(a) / len(a) - sum(b) / len(b)
        num += weight * diff
        den += weight
        strata += 1
        agree += 1 if diff > 0 else 0
        against += 1 if diff < 0 else 0
    pooled = num / den if den > 0 else None
    if pooled is not None:
        # a within-task difference is read off the tasks that split on the
        # property, so that is the count that has to clear the floor
        measurable = measurable and strata >= MIN_GROUP
    return {
        "effect": round(pooled if pooled is not None else raw, 4),
        "raw": round(raw, 4),
        "within_task": pooled is not None,
        # the tasks where the runs differ on the property, and in how many
        # of them the run with it was the wrong one (``more``) or the right
        # one (``less``); on a corpus of pairs this is the whole evidence,
        # and a difference of 100 points from three tasks is three tasks
        "strata": strata,
        "strata_more": agree,
        "strata_less": against,
        "with": {"runs": w_n, "wrong": w_bad},
        "without": {"runs": o_n, "wrong": o_bad},
        "measurable": measurable,
    }


def _rows(trajectories: list, golden: Optional[dict], policy: Optional[dict]) -> tuple:
    gtasks = (golden or {}).get("tasks") or {}
    table, bases = [], {"golden": 0, "outcome": 0}
    for traj in trajectories:
        wrong, basis = _truth(traj, gtasks)
        bases[basis] += 1
        pol = excerpt.effective_policy(policy, gtasks.get(traj.task.id))
        marks = {m["kind"] for m in excerpt.notable_steps(traj, pol)}
        table.append((traj, pol, marks, wrong))
    return table, bases


def learn(trajectories: Iterable[Trajectory], golden: Optional[dict] = None,
          policy: Optional[dict] = None, ledger: Optional[dict] = None) -> dict:
    """Learn lessons from a corpus, test each on the half it was not drawn
    from, and — given a ledger — re-test every earlier lesson on this one.

    Returns the ``lessons`` aggregate block; see SCHEMA.md.  The ledger
    passed in is not modified; the updated one is under ``ledger.next``.
    """
    trajectories = list(trajectories)
    table, bases = _rows(trajectories, golden, policy)
    wrong_total = sum(1 for *_x, w in table if w)
    halves = split(t.task.id for t in trajectories)
    which = {tid: i for i, h in enumerate(halves) for tid in h}
    corpus = fingerprint(trajectories)
    base = {
        "runs": len(table),
        "wrong": wrong_total,
        "target": {"golden": bases["golden"], "outcome": bases["outcome"]},
        "halves": halves,
        "corpus": corpus,
    }
    if len(halves[0]) < 1 or len(halves[1]) < 1 or not wrong_total or wrong_total == len(table):
        why = ("fewer than two tasks, so there is no second half to test a lesson on"
               if not (halves[0] and halves[1]) else
               "every run is right or every run is wrong, so nothing separates them")
        return {**base, "measurable": False, "reason": why, "lessons": [], "tried": 0,
                "held": 0, "did_not_hold": 0,
                "ledger": _ledger(ledger, corpus, [], {}, table),
                "narrative": f"Nothing to learn: {why}."}

    found = []
    cands = candidates()
    for name, (pred, phrasing, source) in cands.items():
        rows = [(t.task.id, pred(t, p, m), w) for t, p, m, w in table]
        whole = _effect(rows)
        per_half = [_effect([r for r in rows if which.get(r[0]) == i]) for i in (0, 1)]
        strong = [h["measurable"] and abs(h["effect"]) >= LESSON for h in per_half]
        if not any(strong):
            continue
        both = all(h["measurable"] for h in per_half)
        same_way = both and (per_half[0]["effect"] > 0) == (per_half[1]["effect"] > 0)
        held = both and same_way and all(abs(h["effect"]) >= HOLDS for h in per_half)
        if held:
            status = "held"
        elif not both:
            status = "untested"
        elif not same_way and all(abs(h["effect"]) >= HOLDS for h in per_half):
            status = "reversed"
        else:
            status = "did_not_hold"
        found.append({
            "name": name,
            "phrasing": phrasing,
            "source": source,
            "direction": "more" if whole["effect"] > 0 else "less",
            "effect": whole["effect"],
            "within_task": whole["within_task"],
            "strata": whole["strata"],
            "strata_more": whole["strata_more"],
            "strata_less": whole["strata_less"],
            "support": whole["strata"] if whole["within_task"] else min(
                whole["with"]["runs"], whole["without"]["runs"]),
            "with": whole["with"],
            "without": whole["without"],
            "halves": [{"effect": h["effect"], "measurable": h["measurable"], "strata": h["strata"],
                        "with": h["with"], "without": h["without"]} for h in per_half],
            "learned_on": [i for i, s in enumerate(strong) if s],
            "status": status,
        })
        found[-1]["sentence"] = say(found[-1])

    order = {"held": 0, "reversed": 1, "did_not_hold": 2, "untested": 3}
    # most evidence first: among lessons that held, the one resting on the
    # most tasks leads, not the one with the largest number
    found.sort(key=lambda l: (order[l["status"]], l["source"] == "annotation",
                              -l["support"], -abs(l["effect"]), l["name"]))
    held = [l for l in found if l["status"] == "held"]
    led = _ledger(ledger, corpus, found, cands, table)
    for lesson in found:
        lesson["ledger"] = (led.get("by_lesson") or {}).get(lesson["name"])
    return {**base, "measurable": True, "lessons": found, "tried": len(cands),
            "held": len(held), "did_not_hold": len(found) - len(held),
            "ledger": led, "narrative": _narrative(base, found, held, len(cands), led),
            "caveat": ("Associations, not causes: a property can travel with a wrong run because it "
                       "causes it, because something else causes both, or by chance on a small "
                       "sample. A lesson that held on both halves is where to look first, not a "
                       "conclusion.")}


def say(lesson: dict) -> str:
    """One lesson as a sentence a reader can check against the counts."""
    w, o = lesson["with"], lesson["without"]
    counts = f"{w['wrong']} of {w['runs']} wrong with it, {o['wrong']} of {o['runs']} without"
    if lesson.get("within_task") and lesson.get("strata"):
        k = lesson["strata_more"] if lesson["direction"] == "more" else lesson["strata_less"]
        return (f"{lesson['phrasing']} — in {k} of {plural(lesson['strata'], 'task')} where only one "
                f"side did, that side was the {'wrong' if lesson['direction'] == 'more' else 'right'} "
                f"one ({counts})")
    return f"{lesson['phrasing']} — {counts}"


def _narrative(base: dict, found: list, held: list, tried: int, led: dict) -> str:
    n, wrong = base["runs"], base["wrong"]
    parts = []
    again = led.get("rechecked") or []
    if again:
        tally = {}
        for r in again:
            tally[r["status"]] = tally.get(r["status"], 0) + 1
        said = [f"{tally[k]} {label}" for k, label in (
            ("held_again", "held again"), ("weakened", "weakened"), ("reversed", "reversed"),
            ("untestable", "could not be tested (too few runs on one side)")) if tally.get(k)]
        parts.append(f"Carried in from {plural(led.get('prior_corpora') or 0, 'earlier corpus', 'earlier corpora')}: "
                     f"{plural(len(again), 'lesson')}, of which {', '.join(said)}.")
    parts.append(f"From these {plural(n, 'run')} ({wrong} wrong), {tried} properties were tried; "
                 f"{len(found)} separated wrong runs from right on at least one half of the tasks, "
                 f"and {len(held)} held on both.")
    observed = [l for l in held if l["source"] != "annotation"]
    if observed:
        parts.append("The best supported: " + say(observed[0]) + ".")
    elif held:
        parts.append("Only a trace annotation held — what someone wrote on the trace, not what the "
                     "run did — so no behaviour is a new lesson yet.")
    else:
        parts.append("None held on both, so nothing new here is a lesson yet — only a lead.")
    if led.get("seen_before"):
        parts.append("This corpus is already in the ledger, so it is not counted as new evidence.")
    return " ".join(parts)


# ------------------------------------------------------------------ ledger

def _ledger(prior: Optional[dict], corpus: str, found: list, cands: dict, table: list) -> dict:
    """Re-test every earlier lesson on this corpus and write the next ledger."""
    prior = prior if isinstance(prior, dict) and prior.get("version") == LEDGER_VERSION else None
    seen = list((prior or {}).get("corpora") or [])
    seen_before = corpus in seen
    lessons = json.loads(json.dumps((prior or {}).get("lessons") or {}))
    rechecked = []
    if prior and not seen_before:
        for name in sorted(lessons):
            entry = lessons[name]
            if name not in cands:
                rechecked.append({"name": name, "status": "untestable",
                                  "why": "this build no longer knows how to test it"})
                continue
            pred = cands[name][0]
            eff = _effect([(t.task.id, pred(t, p, m), w) for t, p, m, w in table])
            expected = entry.get("direction") == "more"
            if not eff["measurable"]:
                status = "untestable"
            elif (eff["effect"] > 0) != expected and abs(eff["effect"]) >= HOLDS:
                status = "reversed"
            elif (eff["effect"] > 0) == expected and abs(eff["effect"]) >= HOLDS:
                status = "held_again"
            else:
                status = "weakened"
            entry.setdefault("history", []).append(
                {"corpus": corpus, "effect": eff["effect"], "status": status,
                 "runs": eff["with"]["runs"] + eff["without"]["runs"]})
            rechecked.append({"name": name, "phrasing": entry.get("phrasing"),
                              "status": status, "effect": eff["effect"],
                              "with": eff["with"], "without": eff["without"]})
    if not seen_before:
        for lesson in found:
            if lesson["status"] != "held" or lesson["name"] in lessons:
                continue
            lessons[lesson["name"]] = {
                "phrasing": lesson["phrasing"], "source": lesson["source"],
                "direction": lesson["direction"], "learned_from": corpus,
                "history": [{"corpus": corpus, "effect": lesson["effect"], "status": "learned",
                             "runs": lesson["with"]["runs"] + lesson["without"]["runs"]}],
            }
    rank = {"held_again": 0, "reversed": 1, "weakened": 2, "untestable": 3}
    rechecked.sort(key=lambda r: (rank.get(r["status"], 9), r["name"]))
    by_lesson = {}
    for name, entry in lessons.items():
        hist = entry.get("history") or []
        tests = [h for h in hist if h["status"] != "learned"]
        by_lesson[name] = {"learned_from": entry.get("learned_from"),
                           "tested_on": len(tests),
                           "held_again": sum(1 for h in tests if h["status"] == "held_again"),
                           "history": hist}
    nxt = {"version": LEDGER_VERSION,
           "corpora": seen + ([] if seen_before else [corpus]),
           "lessons": lessons}
    return {"had_prior": prior is not None, "corpus": corpus, "seen_before": seen_before,
            "prior_corpora": len(seen), "rechecked": rechecked, "by_lesson": by_lesson,
            "next": nxt}


def load_ledger(path: Union[str, Path]) -> Optional[dict]:
    """A ledger from an earlier batch, or None when the file does not exist yet."""
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != LEDGER_VERSION:
        raise ValueError(f"{p}: not a lessons ledger (version {LEDGER_VERSION})")
    return data


def write_ledger(path: Union[str, Path], ledger: dict) -> None:
    """Write the next ledger — ``learn(...)["ledger"]["next"]``."""
    Path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
