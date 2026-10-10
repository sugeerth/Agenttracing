"""Which part of a long run to put in front of a reader with a budget.

A three-hundred-step run does not fit in a prompt, and the usual answer —
the first N steps, or the first and last N/2 — chooses by *position*. On
the long-horizon suite that is close to choosing at random: the failure is
at step 175 of 202, or 95 of 233, and position knows nothing about either.

This chooses by *structure* instead. The engine already computes, with no
knowledge of what the run was supposed to do, the places where a run is
not behaving like a run that is going well: an error nothing repaired, a
block of steps that produced nothing new, a write with no check after it,
a call made in a cycle, a step whose output the run already had. Those
are candidates for "where it went wrong" that cost nothing to compute and
leak nothing, and an excerpt built around them beats one built around the
ends.

Two things this deliberately does not do:

* **It does not read the golden set.** A selector that knew which
  milestone was missed would be handing the reader the answer and then
  scoring it for finding it. Everything here comes from the trace, plus a
  policy if the caller has one — a policy is a rule the operator wrote,
  not a fact about this run's outcome.
* **It does not weight by anything fitted to a corpus.** The ordering in
  :data:`WEIGHTS` is a stated ranking — an error nobody repaired is a
  better place to look than a step that merely repeats — and it is
  written here to be argued with, not tuned.

The limits are measured rather than claimed, over 1,846 known failures:
a 40-step window chosen by position contains the step where the run goes
wrong 33.4% of the time and one chosen by structure 75.0% of the time, and
structure at 20 steps beats position at 160 — an eighth of the tokens.

Per mode it is not a rate at all. Over 154 runs of each, the structural
excerpt finds the failure in **every** run of nine modes and in **no** run
of three, with nothing in between. The three are ``retry_stall`` (a
scoring artefact: both edges of the stall are shown and the marked step is
the one after it ends), ``skipped_unit`` (a run that never worked a unit
keeps a regular rhythm with one fewer turn, and nothing inside it says how
many turns there should have been) and ``stale_value`` (the superseding
read is in the trace, but knowing it was *discarded* needs the answer).
Three reasons, not one boundary — an earlier version of this docstring
named four modes as unreachable in principle and two of them turned out to
need only a mark nobody had written. So the question this module answers
is not *how often does it work* but *which kinds of failure leave a mark
in what the run did*. See ``docs/HORIZON.md``.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from . import process
from .trace import Trajectory

#: How promising a step is as "the place this run went wrong", as a stated
#: ordering rather than a fitted one.  An error the run never came back to
#: is the strongest thing a trace can say about itself without being told
#: what the task was; a step that merely repeats is the weakest.
WEIGHTS = {
    "unrecovered_error": 6,
    "shipped_before_check": 6,
    "skipped_beat": 5,
    "redundant_stretch": 5,
    "policy_breach": 5,
    "unverified_write": 4,
    "recovered_error": 3,
    "blind_write": 3,
    "cycle": 2,
    "no_information": 1,
}

#: how many steps either side of a notable one travel with it: the call,
#: what led to it and what came of it
CONTEXT = 1

#: share of the budget reserved for the opening and for the closing, so a
#: reader always gets the task as it was set up and the run as it ended
ENDS = 4


def effective_policy(policy: Optional[dict], golden_task: Optional[dict] = None) -> Optional[dict]:
    """The rules that applied to this run, merged from the operator's
    policy and the task's own constraints.

    Only ``forbidden_tools`` and ``forbidden_patterns`` are taken from the
    golden task, and the reason is the whole point of the boundary: those
    are constraints the agent was *told* before it started — inputs, like
    the prompt — while ``milestones``, ``expected`` and ``failure_mode``
    are facts about how it turned out. A selector that read those would be
    shown the answer and then credited with finding it.
    """
    merged = dict(policy or {})
    for key in ("forbidden_tools", "forbidden_patterns"):
        extra = (golden_task or {}).get(key)
        if extra:
            merged[key] = list(merged.get(key) or []) + list(extra)
    return merged or None


#: a tool has to be used this many times before its spacing is a rhythm
MIN_BEATS = 4
#: a tool used at most this many times, with an effect, is a singular act
#: rather than part of the run's ordinary work
RARE = 2
#: how much of the run must still be to come after that act for it to be
#: worth looking at — the same scale-relative idea as `process.LOOP_SHARE`
GATE_SHARE = 0.1


def _beats(traj: Trajectory) -> dict:
    """tool name -> the positions, among tool steps, where it was called."""
    seen: dict = {}
    for at, step in enumerate(s for s in traj.steps if s.type in process.TOOLISH_TYPES):
        if step.name:
            seen.setdefault(step.name, []).append(at)
    return seen


def _period(gaps: list) -> Optional[int]:
    """The tool's own beat: the **largest** gap it falls into repeatedly.

    Not the median and not the commonest. A tool called twice per unit of
    work has two kinds of gap — a short one inside the unit and a long one
    between units — and only the long one marks a new iteration starting.
    The median lands between them, where nothing happens; the commonest is
    usually the short one. It has to repeat, because a single long pause is
    not a period.
    """
    counts = Counter(g for g in gaps if g >= 2)
    repeated = [g for g, seen in counts.items() if seen >= 3]
    return max(repeated) if repeated else None


def _skipped_beats(traj: Trajectory) -> list:
    """Gaps long enough to hold two of a tool's own periods: a turn of the
    run's cycle that never closed.

    This is what a missing stage looks like from outside. A run working
    through eight units checks each one; the unit nobody checked leaves no
    step behind to find, and the only trace of it is the beat that did not
    come. Reported as the span, so a reader is shown the hole rather than
    its two edges.
    """
    positions = [s.index for s in traj.steps if s.type in process.TOOLISH_TYPES]
    out = []
    for name, beats in sorted(_beats(traj).items()):
        if len(beats) < MIN_BEATS:
            continue
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        period = _period(gaps)
        if not period:
            continue
        for (a, b), gap in zip(zip(beats, beats[1:]), gaps):
            if gap >= 2 * period - 1:
                out.append({"from": positions[a], "to": positions[b],
                            "at": positions[min(a + gap // 2, len(positions) - 1)],
                            "tool": name, "period": period, "beats": round(gap / period, 1)})
    return out


def _late_gates(traj: Trajectory) -> list:
    """A rare effectful call with a large share of the run still to come.

    A tool the run reaches for once — publish, deploy, submit — is a
    different kind of act from the one it uses fifty times, and the
    question a trace can answer about it is how much of the run happened
    *after* it. Verification that follows the point of no return is
    verification of something already done.
    """
    steps = list(traj.steps)
    total = len(steps)
    names = Counter(s.name for s in steps if s.type in process.TOOLISH_TYPES and s.name)
    out = []
    for at, step in enumerate(steps):
        if step.type not in process.TOOLISH_TYPES or names.get(step.name, 0) > RARE:
            continue
        if process.effect_of(step)[0] != "write":
            continue
        after = total - at - 1
        if after / total < GATE_SHARE:
            continue
        checked = sum(1 for s in steps[at + 1:]
                      if s.type in process.TOOLISH_TYPES and process.effect_of(s)[0] == "read")
        if checked:
            out.append({"index": step.index, "name": step.name, "after": after,
                        "share": round(after / total, 4), "checks_after": checked})
    return out


def _policy_steps(traj: Trajectory, policy: Optional[dict]) -> list:
    """Steps a policy forbids.  The policy is the operator's rule, not the
    golden set's answer, so using it here leaks nothing about the outcome."""
    if not policy:
        return []
    forbidden = set(policy.get("forbidden_tools") or [])
    patterns = [re.compile(p) for p in (policy.get("forbidden_patterns") or [])]
    out = []
    for step in traj.steps:
        if step.name and step.name in forbidden:
            out.append((step.index, f"calls {step.name}, which the policy forbids"))
            continue
        for pat in patterns:
            if pat.search(step.input or ""):
                out.append((step.index, f"input matches /{pat.pattern}/, which the policy forbids"))
                break
    return out


def notable_steps(traj: Trajectory, policy: Optional[dict] = None) -> list:
    """The steps where this run is not behaving like one that is going well.

    Each entry is ``{index, kind, weight, why}``; a step found by more
    than one rule keeps the strongest. Computed from the trace alone
    (plus ``policy``), never from a golden set.
    """
    found: dict = {}

    def mark(index, kind, why):
        if not isinstance(index, int):
            return
        weight = WEIGHTS[kind]
        if index not in found or found[index]["weight"] < weight:
            found[index] = {"index": index, "kind": kind, "weight": weight, "why": why}

    for err in process.recovery(traj).get("error_steps") or []:
        recovered = err.get("outcome") == "recovered"
        mark(err.get("index"), "recovered_error" if recovered else "unrecovered_error",
             f"{err.get('name') or 'a call'} failed and the run {err.get('outcome') or 'did not come back to it'}")

    rep = process.repeats(traj)
    for stretch in rep.get("redundant_stretches") or []:
        why = f"{stretch.get('steps')} steps here produced nothing the run did not already have"
        mark(stretch.get("from"), "redundant_stretch", why)
        mark(stretch.get("to"), "redundant_stretch", why)
    for cyc in rep.get("cycle_steps") or []:
        mark(cyc.get("index"), "cycle",
             f"{cyc.get('name') or 'this call'} and its result recurred from step {cyc.get('first_seen')}")
    for dup in rep.get("no_information_detail") or []:
        mark(dup.get("index"), "no_information", f"the same output as step {dup.get('same_as')}")

    side = process.side_effects(traj)
    writes = [w.get("index") if isinstance(w, dict) else w for w in (side.get("write_steps") or [])]
    for blind in side.get("blind_write_steps") or []:
        mark(blind.get("index") if isinstance(blind, dict) else blind, "blind_write",
             "a write before the run had read anything")
    if writes:
        last = writes[-1]
        after = [s for s in traj.steps if s.index > last and process.effect_of(s)[0] == "read"
                 and not process.is_error(s)[0]]
        if not after:
            mark(last, "unverified_write", "the last write, with no read or check after it")

    for index, why in _policy_steps(traj, policy):
        mark(index, "policy_breach", why)

    for hole in _skipped_beats(traj):
        mark(hole["at"], "skipped_beat",
             f"{hole['tool']} runs every {hole['period']} steps and then does not, for "
             f"{hole['beats']} of its own turns")

    for gate in _late_gates(traj):
        mark(gate["index"], "shipped_before_check",
             f"{gate['name']} is called once here, and {gate['share']:.0%} of the run — including "
             f"{gate['checks_after']} checks — comes after it")

    return sorted(found.values(), key=lambda m: m["index"])


def _cluster(marks: list, positions: dict) -> list:
    """Marks close enough to share a window, as one candidate each.

    A cluster is represented by its strongest mark — a stall is one place
    to look, however many steps it spans, and spending the budget once per
    failing retry is how an excerpt ends up showing one event and nothing
    else.
    """
    span = 2 * CONTEXT + 1
    clusters: list = []
    current: list = []
    for mark in sorted(marks, key=lambda m: m["index"]):
        at = positions.get(mark["index"])
        if at is None:
            continue
        if current and at - current[-1][0] > span:
            clusters.append(current)
            current = []
        current.append((at, mark))
    if current:
        clusters.append(current)
    out = []
    for group in clusters:
        at, mark = max(group, key=lambda pair: (pair[1]["weight"], -pair[0]))
        # a region has two edges, and both of them say something: where it
        # started and how it ended.  A twenty-step stall shown only at its
        # opening is a run that looks like it is still trying.
        out.append({"at": at, "mark": mark, "weight": mark["weight"], "steps": len(group),
                    "edges": sorted({group[0][0], group[-1][0], at})})
    return out


def focus(traj: Trajectory, cap: int, policy: Optional[dict] = None,
          within: Optional[int] = None) -> dict:
    """Choose ``cap`` steps of ``traj`` to show, and say why those.

    Returns ``{keep, marks, kinds, basis, total, chosen}`` — ``keep`` is
    the sorted step *positions* (not indexes) to render. Under the cap the
    whole run is kept and the question does not arise.

    ``within`` limits what may be *chosen* without limiting what is read:
    a caller rendering the answer step separately passes the count of the
    steps before it, and the analysis still sees the whole trajectory —
    which a trace could not be built without, since the schema requires a
    run to end in its answer.
    """
    steps = traj.steps
    total = len(steps) if within is None else max(0, min(within, len(steps)))
    positions = {step.index: i for i, step in enumerate(steps) if i < total}
    if total <= cap:
        return {"keep": list(range(total)), "marks": [], "kinds": {}, "total": total, "chosen": total,
                "basis": "every step of the run"}

    ends = max(1, cap // ENDS)
    keep = set(range(min(ends, total))) | set(range(max(0, total - ends), total))
    marks = notable_steps(traj, policy)
    # Ten consecutive failing retries are one event, not ten. Without this
    # a single stall eats the whole budget and the rest of the run goes
    # unseen — which is how the first version of this lost four of the
    # modes it was built to find.
    clusters = _cluster(marks, positions)
    used: list = []
    budget = cap - len(keep)
    for cluster in sorted(clusters, key=lambda c: (-c["weight"], c["at"])):
        if budget <= 0:
            break
        window = [p for edge in cluster["edges"]
                  for p in range(edge - CONTEXT, edge + CONTEXT + 1)
                  if 0 <= p < total and p not in keep]
        window = sorted(set(window))
        if not window:
            continue
        for p in window[:budget]:
            keep.add(p)
            budget -= 1
        used.append(cluster["mark"])
    # whatever is left over widens the clusters already chosen, strongest
    # first, a step at a time, so no one of them can starve the others
    reach = CONTEXT
    while budget > 0 and used:
        reach += 1
        spent = False
        for cluster in sorted(clusters, key=lambda c: (-c["weight"], c["at"])):
            if budget <= 0:
                break
            for edge in cluster["edges"]:
                for p in (edge - reach, edge + reach):
                    if 0 <= p < total and p not in keep and budget > 0:
                        keep.add(p)
                        budget -= 1
                        spent = True
        if not spent:
            break

    kinds: dict = {}
    for mark in used:
        kinds[mark["kind"]] = kinds.get(mark["kind"], 0) + 1
    shown = ", ".join(f"{n}×{kind}" for kind, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))
    return {"keep": sorted(keep), "marks": used, "kinds": kinds, "total": total, "chosen": len(keep),
            "basis": (f"{len(keep)} of {total} steps: the first {ends} and last {ends}, plus what the run "
                      f"itself flags — {shown}" if shown else
                      f"{len(keep)} of {total} steps: the first {ends} and last {ends}; the run flags nothing else")}


__all__ = ["WEIGHTS", "CONTEXT", "ENDS", "MIN_BEATS", "RARE", "GATE_SHARE",
           "effective_policy", "notable_steps", "focus"]
