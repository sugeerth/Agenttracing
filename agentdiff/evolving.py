"""Evals that evolve with the policy: one suite, carried through training generations.

A policy under training changes how it fails. An eval suite written once
goes stale both ways: it keeps flagging a failure the policy no longer
makes, and it has nothing for the one it learned last week. This module
keeps the suite moving with the policy, generation by generation, and
says why every eval entered and left.

Each generation, in this order:

1. **Forward test.** Every active eval meets this generation's runs, which
   it was not written from, before anything is forged from them. This is
   the honest number: did the suite carried from earlier generations
   catch this generation's failures? It is reported as the forward
   coverage and the forward false alarms.
2. **Retire.** An eval that fires on more than ``FPR_MAX`` of the right
   runs is retired at once (*noisy*): the policy found a way to look like
   the failure without failing. An eval that catches nothing for
   ``patience`` generations in a row while there were failures to catch
   is retired too (*gone quiet*): the failure it caught is gone. An eval
   that cannot be tested (no failures, too few right runs) waits.
3. **Forge.** The forge (:mod:`agentdiff.forge`) writes candidates from
   this generation's failures and adopts only those that hold on its
   held-out tasks. Each newcomer is *born* here.

A retired eval meets every generation too, without counting toward the
forward coverage. One that holds again is *reborn*: the failure it caught
came back, and the evidence for that is this generation's runs, not the
forge happening to pick the same rule.

The target says what counts as a failure (:mod:`agentdiff.rleval`): the
run's outcome, or for RL, the reward disagreeing with it. Nothing here
calls a model unless a proposer is given, and a proposer's rules are
tested like any other.

The state is a ledger (``{"kind": "evolving-evals"}``) that the next run
continues from. A generation already read, by its corpus fingerprint, is
not read twice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple, Union

from . import excerpt
from ._text import plural
from .forge import FPR_MAX, MIN_RIGHT, RunView, _score, _unique_keys, describe, forge, parse_rule
from .lessons import fingerprint
from .rleval import describe_target, truth_for

__all__ = ["evolve", "PATIENCE", "LEDGER_KIND", "load_ledger", "write_ledger"]

#: generations in a row an eval may catch nothing before it is retired
PATIENCE = 2
LEDGER_KIND = "evolving-evals"
LEDGER_VERSION = 1


def _views(trajectories: list, golden: Optional[dict], policy: Optional[dict], truth: Optional[Callable]):
    from .lessons import _truth
    gtasks = (golden or {}).get("tasks") or {}
    label = truth or _truth
    runs, wrong = [], {}
    for t in trajectories:
        w = label(t, gtasks)[0]
        if w is None:
            continue      # cannot be labelled either way: not scored (see forge)
        view = RunView(t, excerpt.effective_policy(policy, gtasks.get(t.task.id)), set())
        runs.append(view)
        wrong[id(view)] = w
    _unique_keys(runs)
    return runs, wrong


def _fresh_ledger(target: str) -> dict:
    return {"kind": LEDGER_KIND, "version": LEDGER_VERSION, "target": target, "generations": [], "evals": {}}


def evolve(generations: Iterable[Tuple[str, list]], *, target: str = "failure", golden: Optional[dict] = None,
           policy: Optional[dict] = None, ledger: Optional[dict] = None, patience: int = PATIENCE,
           proposer: Optional[Callable] = None) -> dict:
    """Carry one eval suite through ``generations`` (``[(label, trajectories)]``,
    oldest first). Returns the lineage, every eval with its history, and the
    ledger to continue from."""
    if patience < 1:
        raise ValueError("patience is at least one generation")
    state = json.loads(json.dumps(ledger)) if ledger else _fresh_ledger(target)
    if state.get("kind") != LEDGER_KIND or state.get("version") != LEDGER_VERSION:
        raise ValueError("not an evolving-evals ledger")
    if state.get("target") != target:
        raise ValueError(f"the ledger evolves evals for {state.get('target')!r}, not {target!r}")
    evals = state["evals"]
    lineage = []
    for label, trajectories in generations:
        trajectories = list(trajectories)
        corpus = fingerprint(trajectories)
        if any(g["corpus"] == corpus for g in state["generations"]):
            lineage.append({"generation": label, "corpus": corpus, "skipped": "already read"})
            continue
        truth = truth_for(target, trajectories)
        runs, wrong = _views(trajectories, golden, policy, truth)
        n_wrong = sum(1 for r in runs if wrong[id(r)])
        n_right = len(runs) - n_wrong

        # 1. forward test: the suite as it arrived, on runs it never saw
        arrived = sorted(i for i, e in evals.items() if e["status"] == "active")
        caught_fwd, flagged_fwd, verdicts = set(), set(), {}
        for rid in arrived:
            e = evals[rid]
            s = _score(parse_rule(e["rule"]), runs, wrong)
            if s["wrong"] < 1 or s["right"] < MIN_RIGHT:
                verdict = "untestable"
            elif (s["fpr"] or 0) > FPR_MAX:
                verdict = "noisy"
            elif s["caught"] < 1:
                verdict = "quiet"
            else:
                verdict = "holds"
            verdicts[rid] = verdict
            caught_fwd |= set(s["caught_runs"])
            flagged_fwd |= set(s["false_alarm_runs"])
            e["history"].append({"generation": label, "verdict": verdict, "caught": s["caught"],
                                 "wrong": s["wrong"], "false_alarms": s["false_alarms"], "right": s["right"]})

        # a retired eval meets the generation too: one that holds again is
        # reborn on that evidence, because the failure it caught came back
        reborn = []
        for rid in sorted(i for i, e in evals.items() if e["status"] == "retired"):
            e = evals[rid]
            s = _score(parse_rule(e["rule"]), runs, wrong)
            if s["wrong"] >= 1 and s["right"] >= MIN_RIGHT and (s["fpr"] or 0) <= FPR_MAX and s["caught"] >= 1:
                e.update(status="active", quiet=0, retired_at=None,
                         reason=f"reborn at {label}: the failure it caught came back")
                e["reborn"] = e.get("reborn", 0) + 1
                e["history"].append({"generation": label, "verdict": "reborn", "caught": s["caught"],
                                     "wrong": s["wrong"], "false_alarms": s["false_alarms"], "right": s["right"]})
                reborn.append(rid)

        # 2. retire
        retired_now = []
        for rid, verdict in verdicts.items():
            e = evals[rid]
            if verdict == "holds":
                e["quiet"] = 0
            elif verdict == "quiet":
                e["quiet"] = e.get("quiet", 0) + 1
            if verdict == "noisy":
                reason = f"fired on right runs at {label}: the policy can look like this failure without failing"
            elif verdict == "quiet" and e["quiet"] >= patience:
                reason = (f"caught nothing for {plural(e['quiet'], 'generation')} in a row while there were "
                          f"failures to catch: the failure it caught is gone")
            else:
                continue
            e.update(status="retired", retired_at=label, reason=reason)
            retired_now.append(rid)

        # 3. forge from this generation's failures
        forged = forge(trajectories, golden, policy, truth=truth, proposer=proposer)
        born = []
        for s in forged.get("suite") or []:
            rid = s["id"]
            if rid in evals and evals[rid]["status"] == "active":
                continue
            if rid in evals:
                evals[rid].update(status="active", quiet=0, retired_at=None,
                                  reason=f"reborn at {label}: the failure came back")
                evals[rid]["reborn"] = evals[rid].get("reborn", 0) + 1
                reborn.append(rid)
            else:
                evals[rid] = {"rule": s["spec"], "says": s["says"], "source": s["source"], "born": label,
                              "status": "active", "quiet": 0, "retired_at": None, "reason": None,
                              "history": [{"generation": label, "verdict": "born", "caught": s["whole"]["caught"],
                                           "wrong": s["whole"]["wrong"], "false_alarms": s["whole"]["false_alarms"],
                                           "right": s["whole"]["right"]}]}
                born.append(rid)

        # the suite leaving this generation, on this generation
        active = sorted(i for i, e in evals.items() if e["status"] == "active")
        caught_after, flagged_after = set(), set()
        for rid in active:
            s = _score(parse_rule(evals[rid]["rule"]), runs, wrong)
            caught_after |= set(s["caught_runs"])
            flagged_after |= set(s["false_alarm_runs"])
        record = {
            "generation": label, "corpus": corpus, "runs": len(runs), "wrong": n_wrong, "right": n_right,
            "arrived": len(arrived),
            "forward": {"caught": len(caught_fwd), "coverage": round(len(caught_fwd) / n_wrong, 4) if n_wrong else None,
                        "false_alarms": len(flagged_fwd),
                        "fpr": round(len(flagged_fwd) / n_right, 4) if n_right else None},
            "verdicts": verdicts, "retired": retired_now, "born": born, "reborn": reborn,
            "forge": {"measurable": forged.get("measurable"), "reason": forged.get("reason"),
                      "tried": sum((forged.get("tally") or {}).values())},
            "leaving": {"suite": len(active), "caught": len(caught_after),
                        "coverage": round(len(caught_after) / n_wrong, 4) if n_wrong else None,
                        "false_alarms": len(flagged_after)},
        }
        lineage.append(record)
        state["generations"].append({"label": label, "corpus": corpus})

    out_evals = [{"id": rid, **{k: v for k, v in e.items()}} for rid, e in sorted(evals.items())]
    return {
        "target": target, "says": describe_target(target), "patience": patience, "fpr_max": FPR_MAX,
        "lineage": lineage, "evals": out_evals,
        "active": [e["id"] for e in out_evals if e["status"] == "active"],
        "retired": [e["id"] for e in out_evals if e["status"] == "retired"],
        "ledger": state, "narrative": _narrative(lineage, target),
    }


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.0%}"


def _narrative(lineage: list, target: str) -> str:
    parts = [f"Target: {describe_target(target)}."]
    for g in lineage:
        if g.get("skipped"):
            parts.append(f"{g['generation']}: {g['skipped']}.")
            continue
        bits = [f"{g['generation']}: {g['wrong']} of {g['runs']} runs to catch"]
        if g["arrived"]:
            bits.append(f"the {plural(g['arrived'], 'eval')} carried in caught {g['forward']['caught']} "
                        f"({_pct(g['forward']['coverage'])}) never seen before, flagging "
                        f"{g['forward']['false_alarms']} right run(s)")
        if g["retired"]:
            bits.append(f"retired {', '.join(g['retired'])}")
        if g["born"]:
            bits.append(f"born {', '.join(g['born'])}")
        if g["reborn"]:
            bits.append(f"reborn {', '.join(g['reborn'])}")
        if not g["forge"]["measurable"] and g["forge"]["reason"]:
            bits.append(f"nothing forged ({g['forge']['reason']})")
        parts.append("; ".join(bits) + ".")
    return " ".join(parts)


def load_ledger(path: Union[str, Path]) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != LEDGER_KIND:
        raise ValueError(f"{p}: not an evolving-evals ledger")
    return data


def write_ledger(path: Union[str, Path], ledger: dict) -> None:
    Path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
