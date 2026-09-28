"""The eval forge: evals written from the traces, kept only if they hold on runs they were not written from.

A scorecard is a fixed set of questions. The forge grows the set. It reads
where runs went wrong, writes candidate evals that would have caught those
runs, and adopts only the ones that catch wrong runs **and leave right runs
alone** on tasks they were not written from. It then goes back for the
wrong runs nothing catches yet. Each round the suite covers more of what
went wrong, and the page draws that as a curve, with the false-positive
rate beside it, because a suite that "improves" by flagging everything has
not improved.

**What an eval is here.** A deterministic assertion over a trace, from a
small closed grammar (:data:`RULES`), so every eval can be run on any
trace, by anyone, without a model, and its verdict is reproducible:

* ``mark:<kind>`` — the run carries one of the page's own trace marks (an
  error it never came back to, a redundant stretch, a write never checked…)
* ``no_check_after_last_edit`` — it edited, and never checked afterwards
* ``claims_without_check`` — its final message says the work is done and
  no check ran after its last edit
* ``signature:<id>`` — the run carries a recurring issue's signature
  (``kind/a:<type>.<name>/b:…``, from :mod:`agentdiff.issues`)
* ``tool_called:<name>[>=n]`` / ``tool_absent:<name>`` — a tool's use
* ``repeated_call:<n>`` — the same call, same arguments, n times
* ``error_streak:<n>`` — n tool errors in a row
* ``output_matches:<regex>`` / ``answer_matches:<regex>`` — text in a
  tool's output, or in the final answer
* ``all:[rule, rule]`` — both, which is how a noisy rule is refined

**Where candidates come from.** Four places, and each eval says which:
*template* (every rule the grammar can instantiate from what the wrong
runs contain), *signature* (the corpus's recurring issues), *seed* (a step
a reader clicked on the page and marked "make this an eval"), and *judge*
(an agent that reads the failing traces with tools and proposes rules —
:mod:`agentdiff.harness.forge_judge`). A proposal from a model is a
proposal: it is validated like any other and cannot adopt itself.

**How one is tested.** The tasks are split in two by a hash of their id,
the same fixed split the lessons use. An eval is **adopted** when, on
*both* halves, it catches at least one wrong run and fires on at most
:data:`FPR_MAX` of the right ones. It is **noisy** when it fires on right
runs, **blind** when it catches nothing on a half, and **untested** when a
half has too few runs to say. Among adopted evals, the suite is built
greedily by what each adds: an eval that only catches runs already caught
is valid and **redundant**, and stays out of the suite.

**Going back.** A noisy eval is not thrown away. The next round refines
it by conjunction with each discriminating property of the wrong runs it
missed nothing on, and tests the refinement the same way. Every round
targets the wrong runs no adopted eval catches yet, so the rounds stop
when nothing is left uncaught or nothing new is adopted.

**The ledger** (``--evals FILE``) carries the adopted suite to the next
corpus, where every eval is re-tested on runs it has never seen and kept
or retired. A corpus read twice is not counted twice.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

from . import excerpt
from ._text import plural
from .duel import classify, claims_done
from .lessons import fingerprint, split, _truth

__all__ = ["RULES", "evaluate", "describe", "candidates", "forge", "FPR_MAX", "load_ledger",
           "write_ledger", "parse_rule", "spec", "try_rule", "RunView"]

#: the largest share of right runs an eval may fire on, on each half
FPR_MAX = 0.10
#: right runs a half needs before a false-positive rate means anything
MIN_RIGHT = 2
#: rounds of going back, at most
ROUNDS = 4
#: refinements tried per noisy eval, and per round in all — every one that
#: passes the learn half is one more test of the held-out half, and a
#: held-out half tested without limit stops being held out
REFINE_CAP = 6
ROUND_CAP = 40
LEDGER_VERSION = 1

RULES = ("mark", "no_check_after_last_edit", "claims_without_check", "signature", "tool_called",
         "tool_absent", "repeated_call", "error_streak", "output_matches", "answer_matches", "all")


# ------------------------------------------------------------------ rules

def parse_rule(text: Union[str, dict]) -> dict:
    """``"mark:unrecovered_error"`` or ``{"all": [...]}`` → a rule dict.

    Raises ValueError on anything outside the grammar — a proposal from a
    model that does not parse is rejected with the reason, not guessed at.
    """
    if isinstance(text, dict):
        if "all" in text:
            parts = [parse_rule(p) for p in text["all"]]
            if len(parts) < 2:
                raise ValueError("all: needs at least two rules")
            return {"kind": "all", "parts": parts}
        if "rule" in text:
            return parse_rule(text["rule"])
        raise ValueError(f"not a rule: {text!r}")
    s = str(text).strip()
    kind, _, arg = s.partition(":")
    kind = kind.strip()
    if kind not in RULES or kind == "all":
        raise ValueError(f"unknown rule {kind!r}; known: {', '.join(RULES)}")
    if kind in ("no_check_after_last_edit", "claims_without_check"):
        return {"kind": kind}
    if not arg:
        raise ValueError(f"{kind} needs an argument")
    if kind == "mark":
        if arg not in excerpt.WEIGHTS:
            raise ValueError(f"unknown mark {arg!r}; known: {', '.join(sorted(excerpt.WEIGHTS))}")
        return {"kind": kind, "arg": arg}
    if kind == "tool_called":
        name, _, n = arg.partition(">=")
        return {"kind": kind, "arg": name.strip(), "n": int(n) if n.strip() else 1}
    if kind in ("repeated_call", "error_streak"):
        n = int(arg)
        if n < 1:
            raise ValueError(f"{kind} needs a positive count")
        return {"kind": kind, "n": n}
    if kind in ("output_matches", "answer_matches"):
        if len(arg) > 200:
            raise ValueError("a pattern longer than 200 characters is refused")
        re.compile(arg)
        return {"kind": kind, "arg": arg}
    return {"kind": kind, "arg": arg}


def spec(rule: dict):
    """The rule as text :func:`parse_rule` reads back (``{"all": [...]}`` for a conjunction)."""
    if rule["kind"] == "all":
        return {"all": [spec(p) for p in rule["parts"]]}
    return rule_id(rule)


def rule_id(rule: dict) -> str:
    if rule["kind"] == "all":
        return "all:[" + ", ".join(sorted(rule_id(p) for p in rule["parts"])) + "]"
    if rule["kind"] == "tool_called":
        return f"tool_called:{rule['arg']}" + (f">={rule['n']}" if rule.get("n", 1) > 1 else "")
    if rule["kind"] in ("repeated_call", "error_streak"):
        return f"{rule['kind']}:{rule['n']}"
    return rule["kind"] + (f":{rule['arg']}" if "arg" in rule else "")


_PHRASE = {
    "no_check_after_last_edit": "it edited and never ran a check afterwards",
    "claims_without_check": "its final message says the work is done, with no check after its last edit",
}


def describe(rule: dict) -> str:
    """The eval as a sentence: *flags a run when …*."""
    k = rule["kind"]
    if k == "all":
        return " and ".join(describe(p) for p in rule["parts"])
    if k in _PHRASE:
        return _PHRASE[k]
    if k == "mark":
        from .lessons import SIGNS
        return SIGNS.get(rule["arg"], rule["arg"]).replace("the run ", "it ", 1)
    if k == "signature":
        return f"it carries the recurring issue {rule['arg']}"
    if k == "tool_called":
        n = rule.get("n", 1)
        return f"it called {rule['arg']}" + (f" {n} or more times" if n > 1 else "")
    if k == "tool_absent":
        return f"it never called {rule['arg']}"
    if k == "repeated_call":
        return f"it made the same call with the same arguments {rule['n']} times"
    if k == "error_streak":
        return f"{rule['n']} of its tool calls in a row failed"
    if k == "output_matches":
        return f"a tool's output matches /{rule['arg']}/"
    if k == "answer_matches":
        return f"its final answer matches /{rule['arg']}/"
    return rule_id(rule)


class RunView:
    """What the rules read from one run, computed once."""

    def __init__(self, traj, policy: Optional[dict], signatures: set):
        data = traj.to_dict() if hasattr(traj, "to_dict") else traj
        self.task = data["task"]["id"]
        self.agent = data["agent"]["name"]
        self.steps = data.get("steps") or []
        self.answer = str((data.get("outcome") or {}).get("answer") or "")
        self.signatures = signatures
        try:
            self.marks = {m["kind"]: m["index"] for m in reversed(excerpt.notable_steps(traj, policy))}
        except Exception:
            self.marks = {}
        kinds = [(i, classify(s)) for i, s in enumerate(self.steps)]
        edits = [i for i, k in kinds if k == "edit"]
        checks = [i for i, k in kinds if k == "verify"]
        self.last_edit = edits[-1] if edits else None
        self.checked_after = bool(edits and any(c > edits[-1] for c in checks))
        self.any_check = bool(checks)
        self.tools = [(i, str(s.get("name") or ""), str(s.get("input") or ""), bool(s.get("error")),
                       str(s.get("output") or ""))
                      for i, s in enumerate(self.steps) if s.get("type") not in ("reason", "answer")]


def evaluate(rule: dict, run: RunView) -> Optional[int]:
    """The step index where the eval fires on this run (-1 for the run as a
    whole), or None when it does not fire."""
    k = rule["kind"]
    if k == "all":
        hits = [evaluate(p, run) for p in rule["parts"]]
        return None if any(h is None for h in hits) else max(hits)
    if k == "mark":
        return run.marks.get(rule["arg"])
    if k == "no_check_after_last_edit":
        return run.last_edit if run.last_edit is not None and not run.checked_after else None
    if k == "claims_without_check":
        if claims_done(run.answer) and not run.checked_after and (run.last_edit is not None or not run.any_check):
            return len(run.steps) - 1
        return None
    if k == "signature":
        return -1 if rule["arg"] in run.signatures else None
    if k == "tool_called":
        at = [i for i, name, *_ in run.tools if name == rule["arg"]]
        return at[rule.get("n", 1) - 1] if len(at) >= rule.get("n", 1) else None
    if k == "tool_absent":
        return -1 if not any(name == rule["arg"] for _, name, *_ in run.tools) else None
    if k == "repeated_call":
        seen: dict = {}
        for i, name, inp, *_ in run.tools:
            key = (name, inp)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= rule["n"]:
                return i
        return None
    if k == "error_streak":
        streak = 0
        for i, _n, _in, err, _out in run.tools:
            streak = streak + 1 if err else 0
            if streak >= rule["n"]:
                return i
        return None
    if k == "output_matches":
        pat = re.compile(rule["arg"])
        for i, _n, _in, _e, out in run.tools:
            if pat.search(out):
                return i
        return None
    if k == "answer_matches":
        return len(run.steps) - 1 if re.search(rule["arg"], run.answer) else None
    return None


# -------------------------------------------------------------- candidates

def _signatures_by_run(issues: Optional[dict], agents: Optional[dict]) -> dict:
    """(task, agent) -> the recurring-issue signatures that run carries."""
    out: dict = {}
    if not issues or not agents:
        return out
    for issue in issues.get("issues") or []:
        if issue.get("suppressed"):
            continue
        for occ in issue.get("occurrences") or []:
            for side in ("a", "b"):
                if occ.get(f"{side}_index") is not None and agents.get(side):
                    out.setdefault((occ.get("task"), agents[side]), set()).add(issue["id"])
    return out


def candidates(runs: list, wrong: dict, *, seeds: Optional[list] = None,
               issues: Optional[dict] = None) -> list:
    """Every eval worth testing: ``[{rule, source, why}]``, deduplicated."""
    out: dict = {}

    def add(rule, source, why):
        rid = rule_id(rule)
        if rid not in out:
            out[rid] = {"rule": rule, "id": rid, "source": source, "why": why}

    bad = [r for r in runs if wrong[id(r)]]
    for kind in sorted({k for r in bad for k in r.marks}):
        add({"kind": "mark", "arg": kind}, "template", "a mark some wrong run carries")
    add({"kind": "no_check_after_last_edit"}, "template", "the habit that decides whether a change was checked")
    add({"kind": "claims_without_check"}, "template", "a claim of done that no check backs")
    for n in (2, 3):
        add({"kind": "error_streak", "n": n}, "template", "failures in a row")
    for n in (3, 5):
        add({"kind": "repeated_call", "n": n}, "template", "the same call again and again")
    for sig in sorted({s for r in bad for s in r.signatures}):
        add({"kind": "signature", "arg": sig}, "signature", "a recurring issue a wrong run carries")
    names_bad = {n for r in bad for _, n, *_ in r.tools}
    names_all = [{n for _, n, *_ in r.tools} for r in runs]
    for name in sorted(names_bad):
        users = sum(1 for s in names_all if name in s)
        if 0 < users < len(runs):
            add({"kind": "tool_called", "arg": name}, "template", "a tool not every run uses")
    for name in sorted(set.union(*names_all) if names_all else set()):
        if any(name not in {n for _, n, *_ in r.tools} for r in bad):
            add({"kind": "tool_absent", "arg": name}, "template", "a tool some wrong run never used")
    for seed in seeds or []:
        for rule, why in _from_seed(seed, runs):
            add(rule, "seed", why)
    return list(out.values())


def _from_seed(seed: dict, runs: list) -> list:
    """What a clicked step suggests: its mark, its tool, its error."""
    run = next((r for r in runs if r.task == seed.get("task") and r.agent == seed.get("agent")), None)
    if run is None:
        return []
    at = seed.get("step")
    out = []
    for kind, idx in run.marks.items():
        if at is None or idx == at:
            out.append(({"kind": "mark", "arg": kind}, f"the reader marked step {at} of {run.task}/{run.agent}"))
    step = next((t for t in run.tools if t[0] == at), None)
    if step:
        out.append(({"kind": "tool_called", "arg": step[1]}, f"the tool at the step the reader marked ({step[1]})"))
        if step[3]:
            out.append(({"kind": "error_streak", "n": 1}, "the step the reader marked had failed"))
    if seed.get("rule"):
        try:
            out.append((parse_rule(seed["rule"]), "a rule the reader wrote"))
        except ValueError:
            pass
    return out


# ------------------------------------------------------------- validation

def _score(rule: dict, runs: list, wrong: dict) -> dict:
    hits = {id(r): evaluate(rule, r) for r in runs}
    bad = [r for r in runs if wrong[id(r)]]
    good = [r for r in runs if not wrong[id(r)]]
    tp = [r for r in bad if hits[id(r)] is not None]
    fp = [r for r in good if hits[id(r)] is not None]
    return {"wrong": len(bad), "right": len(good), "caught": len(tp), "false_alarms": len(fp),
            "recall": round(len(tp) / len(bad), 4) if bad else None,
            "fpr": round(len(fp) / len(good), 4) if good else None,
            "caught_runs": sorted(f"{r.task}/{r.agent}" for r in tp),
            "false_alarm_runs": sorted(f"{r.task}/{r.agent}" for r in fp)[:12]}


def _verdict(h: dict) -> Optional[str]:
    """What stops an eval on one half, or None when it passes there."""
    if h["wrong"] < 1 or h["right"] < MIN_RIGHT:
        return "untested"
    if (h["fpr"] or 0) > FPR_MAX:
        return "noisy"
    if h["caught"] < 1:
        return "blind"
    return None


def try_rule(rule: dict, runs: list, wrong: dict, which: dict) -> dict:
    """Learn half first; only an eval that passes there meets the held-out
    half, once, and that meeting alone decides adoption."""
    halves = [_score(rule, [r for r in runs if which.get(r.task) == i], wrong) for i in (0, 1)]
    whole = _score(rule, runs, wrong)
    stop = _verdict(halves[0])
    if stop:
        return {"halves": halves, "whole": whole, "status": stop, "failed_on": "learn", "held_out_tested": False}
    stop = _verdict(halves[1])
    return {"halves": halves, "whole": whole, "status": stop or "adopted",
            "failed_on": "held-out" if stop else None, "held_out_tested": True}


def forge(trajectories: Iterable, golden: Optional[dict] = None, policy: Optional[dict] = None, *,
          issues: Optional[dict] = None, agents: Optional[dict] = None, seeds: Optional[list] = None,
          proposer: Optional[Callable] = None, ledger: Optional[dict] = None,
          rounds: int = ROUNDS) -> dict:
    """Grow an eval suite from a corpus, round by round.

    ``issues``/``agents`` are the batch aggregate's, for signatures;
    ``seeds`` are steps readers marked; ``proposer(round, context) ->
    [rule text]`` is an optional judge that proposes rules (see
    :mod:`agentdiff.harness.forge_judge`); ``ledger`` is the suite from
    earlier corpora, re-tested here. Returns the ``forge`` aggregate block.
    """
    trajectories = list(trajectories)
    gtasks = (golden or {}).get("tasks") or {}
    sig_by_run = _signatures_by_run(issues, agents)
    runs, wrong, basis = [], {}, {"golden": 0, "outcome": 0}
    for t in trajectories:
        pol = excerpt.effective_policy(policy, gtasks.get(t.task.id))
        view = RunView(t, pol, sig_by_run.get((t.task.id, t.agent.name), set()))
        runs.append(view)
        w, b = _truth(t, gtasks)
        wrong[id(view)] = w
        basis[b] += 1
    halves = split(r.task for r in runs)
    which = {tid: i for i, h in enumerate(halves) for tid in h}
    corpus = fingerprint(trajectories)
    n_wrong = sum(1 for r in runs if wrong[id(r)])
    base = {"runs": len(runs), "wrong": n_wrong, "right": len(runs) - n_wrong, "target": basis,
            "halves": halves, "corpus": corpus, "fpr_max": FPR_MAX}
    if not (halves[0] and halves[1]) or not n_wrong or n_wrong == len(runs):
        why = ("fewer than two tasks, so there is no second half to test an eval on"
               if not (halves[0] and halves[1]) else
               "every run is right or every run is wrong, so an eval has nothing to tell apart")
        led = _ledger(ledger, corpus, [], runs, wrong)
        return {**base, "measurable": False, "reason": why, "rounds": [], "suite": [], "tested": [],
                "uncaught": [], "ledger": led, "narrative": f"No evals forged: {why}."}

    tested: dict = {}
    suite: list = []
    covered: set = set()
    history = []
    pool = candidates(runs, wrong, seeds=seeds, issues=issues)
    feedback: list = []
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            pool = _refinements(tested, runs, wrong)
            if proposer:
                pool += _from_proposer(proposer, rnd, runs, wrong, which, covered, feedback, tested)
        pool = [c for c in pool if c["id"] not in tested]
        if not pool:
            break
        adopted_now = []
        for cand in pool:
            res = try_rule(cand["rule"], runs, wrong, which)
            entry = {**cand, "spec": spec(cand["rule"]), "says": describe(cand["rule"]), "round": rnd, **res}
            entry.pop("rule")
            tested[cand["id"]] = {**entry, "_rule": cand["rule"]}
            if res["status"] == "adopted":
                adopted_now.append(tested[cand["id"]])
            elif cand["source"] == "judge":
                feedback.append({"rule": cand["id"], "status": res["status"],
                                 "learn_half": res["halves"][0]})
        # greedy: what each adopted eval adds over the suite so far
        added_any = False
        while True:
            best, best_new = None, set()
            for e in adopted_now:
                if any(s["id"] == e["id"] for s in suite):
                    continue
                new = set(e["whole"]["caught_runs"]) - covered
                if len(new) > len(best_new) or (len(new) == len(best_new) and best and new and
                                                 e["whole"]["false_alarms"] < best["whole"]["false_alarms"]):
                    best, best_new = e, new
            if not best or not best_new:
                break
            best["adds"] = sorted(best_new)
            suite.append(best)
            covered |= best_new
            added_any = True
        for e in adopted_now:
            if not any(s["id"] == e["id"] for s in suite):
                e["status"] = "redundant"
        flagged_right = {x for s in suite for x in _score(s["_rule"], runs, wrong)["false_alarm_runs"]}
        history.append({"round": rnd, "tried": len(pool), "adopted": sum(1 for s in suite if s["round"] == rnd),
                        "held_out_tests": sum(1 for c in pool if tested[c["id"]]["held_out_tested"]),
                        "caught": len(covered), "wrong": n_wrong,
                        "coverage": round(len(covered) / n_wrong, 4),
                        "false_alarms": len(flagged_right),
                        "fpr": round(len(flagged_right) / max(1, len(runs) - n_wrong), 4),
                        "sources": sorted({c["source"] for c in pool})})
        # without a judge, a round that adopts nothing ends the loop: the
        # next round's refinements come from the same material. With one,
        # the next round carries this round's rejections back to it — the
        # "go back" — so it runs while rounds and uncaught runs remain
        if len(covered) == n_wrong or (rnd > 1 and not added_any and not proposer):
            break

    all_wrong = sorted(f"{r.task}/{r.agent}" for r in runs if wrong[id(r)])
    uncaught = [x for x in all_wrong if x not in covered]
    clean = lambda e: {k: v for k, v in e.items() if not k.startswith("_")}   # noqa: E731
    suite_out = [clean(e) for e in suite]
    tested_out = sorted((clean(e) for e in tested.values()),
                        key=lambda e: ({"adopted": 0, "redundant": 1, "noisy": 2, "blind": 3, "untested": 4}
                                       .get(e["status"], 5), e["round"], e["id"]))
    tally = {}
    for e in tested_out:
        tally[e["status"]] = tally.get(e["status"], 0) + 1
    led = _ledger(ledger, corpus, suite, runs, wrong)
    return {**base, "measurable": True, "rounds": history, "suite": suite_out, "tested": tested_out,
            "tally": tally, "uncaught": uncaught, "ledger": led,
            "held_out_tests": sum(h.get("held_out_tests", 0) for h in history),
            "judge": {"used": bool(proposer), "proposed": sum(1 for e in tested_out if e["source"] == "judge"),
                      "adopted": sum(1 for e in suite_out if e["source"] == "judge"),
                      "rounds": list(getattr(proposer, "log", None) or []),
                      "saw": "the learn half only; the held-out half decided"},
            "seeds": len(seeds or []),
            "narrative": _narrative(base, history, suite_out, tally, uncaught, led, bool(proposer)),
            "caveat": ("An eval here is adopted on what it catches and what it leaves alone on tasks it was "
                       "not written from; with a small corpus that is a few runs either way, and each eval "
                       "carries its counts. Wrong is the golden label where there is one, else the run's "
                       "own outcome.")}


def _refinements(tested: dict, runs: list, wrong: dict) -> list:
    """Going back: an eval that was noisy on the learn half, narrowed by
    another property the wrong runs it caught there share. Built from the
    learn half alone, so the held-out half stays unseen until the test."""
    out = []
    noisy = [e for e in tested.values() if e["status"] == "noisy" and e["failed_on"] == "learn"
             and e["halves"][0]["caught"]]
    helpers = [e for e in tested.values() if e["_rule"]["kind"] != "all" and e["halves"][0]["caught"]]
    for e in sorted(noisy, key=lambda x: -x["halves"][0]["caught"]):
        tried = 0
        mine = set(e["halves"][0]["caught_runs"])
        for h in sorted(helpers, key=lambda x: (x["halves"][0]["false_alarms"], -x["halves"][0]["caught"], x["id"])):
            if h["id"] == e["id"] or tried >= REFINE_CAP:
                continue
            if not mine & set(h["halves"][0]["caught_runs"]):
                continue
            rule = {"kind": "all", "parts": [e["_rule"], h["_rule"]]}
            out.append({"rule": rule, "id": rule_id(rule), "source": "refined",
                        "why": f"{e['id']} fired on right runs; narrowed by {h['id']}"})
            tried += 1
    seen, uniq = set(), []
    for c in out:
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    return uniq[:ROUND_CAP]


def _from_proposer(proposer, rnd, runs, wrong, which, covered, feedback, tested) -> list:
    """Ask the judge for rules aimed at what is still uncaught. It is shown
    the learn half only, and told why its earlier proposals failed."""
    context = {
        "round": rnd,
        "uncaught": [f"{r.task}/{r.agent}" for r in runs if wrong[id(r)] and f"{r.task}/{r.agent}" not in covered
                     and which.get(r.task) == 0],
        "grammar": list(RULES), "marks": sorted(excerpt.WEIGHTS),
        "feedback": feedback[-20:],
        "already": sorted(tested)[:80],
        "learn_tasks": sorted(t for t, i in which.items() if i == 0),
    }
    learn = [r for r in runs if which.get(r.task) == 0]

    def try_learn(text):
        """Score a rule on the learn half — the only half the judge may see."""
        try:
            rule = parse_rule(text)
        except (ValueError, re.error) as exc:
            return {"parses": False, "error": str(exc)[:200]}
        got = _score(rule, learn, wrong)
        return {"parses": True, "rule": rule_id(rule), "says": describe(rule), **got,
                "catches_uncaught": sorted(set(got["caught_runs"]) & set(context["uncaught"]))}
    context["try_learn"] = try_learn
    out = []
    try:
        proposals = proposer(rnd, context) or []
    except Exception as exc:   # a judge that fails must not take the forge with it
        feedback.append({"rule": None, "status": "judge_error", "error": str(exc)[:200]})
        return []
    for p in proposals[:20]:
        text = p.get("rule") if isinstance(p, dict) else p
        why = (p.get("why") if isinstance(p, dict) else None) or "proposed by the judge"
        try:
            rule = parse_rule(text)
        except (ValueError, re.error) as exc:
            feedback.append({"rule": str(text)[:120], "status": "does_not_parse", "error": str(exc)[:160]})
            continue
        out.append({"rule": rule, "id": rule_id(rule), "source": "judge", "why": str(why)[:300]})
    return out


def _narrative(base, history, suite, tally, uncaught, led, judged) -> str:
    n_wrong = base["wrong"]
    if not history:
        return "No eval was tested."
    last = history[-1]
    first = history[0]
    held_out = sum(h.get("held_out_tests", 0) for h in history)
    parts = [f"{sum(tally.values())} candidate evals tried on the learn half over {plural(len(history), 'round')}, "
             f"{held_out} passed it and met the held-out half once; "
             f"{len(suite)} adopted into the suite, catching {last['caught']} of {n_wrong} wrong runs "
             f"while flagging {last['false_alarms']} of {base['right']} right ones."]
    if len(history) > 1 and last["caught"] > first["caught"]:
        parts.append(f"Going back raised coverage from {first['caught']} to {last['caught']}.")
    rejected = {k: v for k, v in tally.items() if k not in ("adopted",)}
    if rejected:
        parts.append("Not adopted: " + ", ".join(f"{v} {k}" for k, v in sorted(rejected.items())) + ".")
    if uncaught:
        parts.append(f"{plural(len(uncaught), 'wrong run')} no eval catches yet — where the next round, or a "
                     f"reader's mark, should look.")
    if judged:
        parts.append("A judge proposed rules; each was validated like any other.")
    again = led.get("rechecked") or []
    if again:
        kept = sum(1 for r in again if r["status"] == "kept")
        parts.append(f"Of {plural(len(again), 'eval')} carried in from earlier corpora, {kept} kept.")
    return " ".join(parts)


# ------------------------------------------------------------------ ledger

def _ledger(prior: Optional[dict], corpus: str, suite: list, runs: list, wrong: dict) -> dict:
    prior = prior if isinstance(prior, dict) and prior.get("version") == LEDGER_VERSION else None
    seen = list((prior or {}).get("corpora") or [])
    seen_before = corpus in seen
    evals = json.loads(json.dumps((prior or {}).get("evals") or {}))
    rechecked = []
    if prior and not seen_before:
        for rid in sorted(evals):
            try:
                rule = parse_rule(evals[rid]["rule"])
            except (ValueError, KeyError):
                rechecked.append({"id": rid, "status": "untestable", "why": "no longer parses"})
                continue
            s = _score(rule, runs, wrong)
            if s["wrong"] < 1 or s["right"] < MIN_RIGHT:
                status = "untestable"
            elif (s["fpr"] or 0) > FPR_MAX:
                status = "retired_noisy"
            elif s["caught"] < 1:
                status = "blind_here"
            else:
                status = "kept"
            evals[rid].setdefault("history", []).append({"corpus": corpus, "status": status,
                                                         "caught": s["caught"], "false_alarms": s["false_alarms"]})
            rechecked.append({"id": rid, "says": evals[rid].get("says"), "status": status,
                              "caught": s["caught"], "wrong": s["wrong"], "false_alarms": s["false_alarms"],
                              "right": s["right"]})
    if not seen_before:
        for e in suite:
            if e["id"] not in evals:
                evals[e["id"]] = {"rule": e["spec"], "says": e["says"],
                                  "source": e["source"], "learned_from": corpus,
                                  "history": [{"corpus": corpus, "status": "adopted",
                                               "caught": e["whole"]["caught"],
                                               "false_alarms": e["whole"]["false_alarms"]}]}
    return {"had_prior": prior is not None, "corpus": corpus, "seen_before": seen_before,
            "prior_corpora": len(seen), "rechecked": rechecked,
            "next": {"version": LEDGER_VERSION, "corpora": seen + ([] if seen_before else [corpus]),
                     "evals": evals}}


def load_ledger(path: Union[str, Path]) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != LEDGER_VERSION or "evals" not in data:
        raise ValueError(f"{p}: not an eval ledger (version {LEDGER_VERSION})")
    return data


def write_ledger(path: Union[str, Path], ledger: dict) -> None:
    Path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def seed_id(seed: dict) -> str:
    return hashlib.sha256(json.dumps(seed, sort_keys=True).encode()).hexdigest()[:10]
