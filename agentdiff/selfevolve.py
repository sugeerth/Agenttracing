"""The self-evolving harness: agents run, evals judge them, the harness acts, and both evolve.

:mod:`agentdiff.evolving` carries an eval suite through generations of a
policy someone else trains. Here the thing that changes between
generations is the harness this module controls: the instructions the
agent is given, the tools it may call, the turns it may take. Every
generation goes round once:

1. **Run.** The agents run the tasks under the current harness, and each
   run's own check grades it.
2. **Judge.** The evolving eval suite meets those runs first, as it
   always does (forward test, retire, forge: :func:`agentdiff.evolving.evolve`).
3. **Act.** Every eval that caught this generation's failures names a
   failure the harness could address, and :data:`REMEDIES` says how,
   for the rules a harness knob can reach (a check instruction for "said
   done with no check after its last edit", a denied tool for "called a
   tool that travels with failure", and so on). When no eval has been
   adopted yet (a few tasks are too few to hold one out), a rule that
   fires on every failing run of this generation and on no passing one
   stands in, labelled as a hypothesis from one generation.
4. **Test.** The changed harness runs the same tasks, the same number of
   times, against the current one: one change per experiment, so a kept
   change is attributable. It is kept when it wins more tasks than it
   loses and no task goes from always passing to always failing; a tie
   is reverted, because an instruction should carry only sentences that
   earned their place.
5. **Carry.** A kept harness's runs are the next generation's runs: the
   evals meet the policy the harness produced. An eval whose failure the
   harness fixed goes quiet and is retired; a failure the harness
   provoked is forged into a new eval, which the next round acts on.

Nothing here runs an agent. ``run_arm(harness, label) -> [trajectory]``
does (:mod:`agentdiff.harness.selfevolve` runs Claude Code or Codex;
tests pass a stand-in). No model is in the control path: every decision
is a rule over counts, written to the ledger with the counts it rests on.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from .evolving import _narrative as evals_narrative, evolve as evolve_evals
from .forge import RunView, _score, _unique_keys, candidates, describe, parse_rule, rule_id, spec
from .trace import Trajectory

__all__ = ["Harness", "REMEDIES", "remedy_for", "remedy_text", "decide", "self_evolve", "LEDGER_KIND", "ESSENTIAL_TOOLS",
           "load_ledger", "write_ledger", "visible_check"]

LEDGER_KIND = "self-evolving"
LEDGER_VERSION = 1
#: tools a coding agent cannot work without: never denied, whatever travels with failure
ESSENTIAL_TOOLS = ("Bash", "Read", "Edit", "Write", "shell", "apply_patch", "exec_command")


@dataclass
class Harness:
    """What the harness controls about a run. Each knob reaches the agent
    in a way its trace records (:mod:`agentdiff.harness.selfevolve`)."""

    version: int = 0
    #: sentences appended to the agent's instructions, in the order adopted
    instructions: List[str] = field(default_factory=list)
    #: tools the agent may not call
    deny_tools: List[str] = field(default_factory=list)
    #: a cap on the agent's turns, when one was adopted
    max_turns: Optional[int] = None
    #: the remedy ids in force, so one is never applied twice
    remedies: List[str] = field(default_factory=list)

    def with_remedy(self, remedy: dict) -> "Harness":
        h = copy.deepcopy(self)
        h.version += 1
        h.remedies.append(remedy["id"])
        if remedy["knob"] == "instruction":
            h.instructions.append(remedy["value"])
        elif remedy["knob"] == "deny_tool":
            h.deny_tools.append(remedy["value"])
        elif remedy["knob"] == "max_turns":
            h.max_turns = int(remedy["value"])
        return h

    def describe(self) -> str:
        if not self.remedies:
            return "the agent as it ships: no instruction added, no tool denied"
        bits = []
        if self.instructions:
            bits.append(f"{len(self.instructions)} instruction(s)")
        if self.deny_tools:
            bits.append("denied " + ", ".join(self.deny_tools))
        if self.max_turns:
            bits.append(f"at most {self.max_turns} turns")
        return f"v{self.version}: " + "; ".join(bits)

    @classmethod
    def from_dict(cls, d: dict) -> "Harness":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# --------------------------------------------------------------- remedies
#: what the harness can do about a failure an eval names: rule kind (or
#: ``mark:<kind>``) -> (knob, the change, why it reaches that failure)
REMEDIES: Dict[str, tuple] = {
    "claims_without_check": ("instruction", "Before you say the work is done, run the task's check{check} after "
                             "your last edit and read its result; if it fails, keep working.",
                             "the run said it was done with no check after its last edit"),
    "no_check_after_last_edit": ("instruction", "After your last edit, run the task's check{check} again and read "
                                 "its result before you finish.",
                                 "the run made an edit no check ever saw"),
    "mark:shipped_before_check": ("instruction", "Do not finish until the task's check{check} has passed after your "
                                  "last change.", "the run finished before checking its change"),
    "mark:unverified_write": ("instruction", "After each change, run the task's check{check} or read the changed "
                              "file back before moving on.", "the run wrote something it never verified"),
    "mark:unrecovered_error": ("instruction", "When a command or tool fails, read the error and fix its cause "
                               "before you go on; do not finish with an error left unresolved.",
                               "the run left an error unrecovered"),
    "error_streak": ("instruction", "If two tool calls in a row fail, stop and read the errors before trying "
                     "again; change the approach rather than repeating it.",
                     "the run's tool calls failed several times in a row"),
    "repeated_call": ("instruction", "If the same command gives the same failing result twice, do not run it a "
                      "third time unchanged: re-read the failing output and the code, then try something different.",
                      "the run repeated the same call"),
    "mark:cycle": ("instruction", "If you find yourself making the same change and getting the same failure, "
                   "stop and re-read the failure and the code before editing again.",
                   "the run went round the same cycle"),
    "mark:redundant_stretch": ("instruction", "Do not repeat work you have already done: before a step, check "
                               "whether its result is already in front of you.",
                               "the run spent a stretch of steps that added nothing"),
    "mark:blind_write": ("instruction", "Read a file before you change it.", "the run wrote to a file it had not read"),
    "tool_absent": ("instruction", "Use the {arg} tool where the task calls for it; runs that never used it "
                    "failed more often here.", "runs that never called {arg} failed"),
    "tool_called": ("deny_tool", "{arg}", "runs that called {arg} failed, and the harness can take it away"),
}


def remedy_text(remedy: dict) -> str:
    """The change, as a sentence."""
    if remedy["knob"] == "deny_tool":
        return f"deny the {remedy['value']} tool"
    if remedy["knob"] == "max_turns":
        return f"at most {remedy['value']} turns"
    return remedy["value"]


def remedy_for(rule_text: Union[str, dict], *, check: str = "") -> Optional[dict]:
    """The harness change for an eval's rule; ``{"unactionable": why}`` when no
    knob reaches it; None when the rule does not parse. ``check`` is named in an
    instruction only when given: pass "" for a grader the agent must not see."""
    try:
        rule = parse_rule(rule_text)
    except ValueError:
        return None
    if rule["kind"] == "all":
        # a conjunction: the first part a knob reaches carries the remedy
        for part in rule["parts"]:
            r = remedy_for(part, check=check)
            if r and not r.get("unactionable"):
                return r
        return {"unactionable": "no part of the conjunction is a failure a harness knob reaches"}
    key = f"mark:{rule['arg']}" if rule["kind"] == "mark" else rule["kind"]
    if key not in REMEDIES:
        return {"unactionable": f"{describe(rule)}: a detector with no harness knob behind it"}
    knob, value, why = REMEDIES[key]
    arg = rule.get("arg", "")
    if knob == "deny_tool" and arg in ESSENTIAL_TOOLS:
        return {"unactionable": f"{arg} travels with failure, but a coding agent cannot work without it"}
    if not check:
        # no check the agent can see (a held-out grader): it is told to test, never where the grader is
        value = value.replace("run the task's check{check}", "test your change against every case the task "
                                                             "states (write a quick test and run it)")
    checktext = f" (`{check}`)" if check else ""
    value = value.format(check=checktext, arg=arg)
    rid = f"{knob}:{key}" + (f":{arg}" if knob == "deny_tool" else "")
    return {"id": rid, "knob": knob, "value": value, "why": why.format(arg=arg), "rule": rule_id(rule)}


def _priority(rule_text) -> int:
    try:
        rule = parse_rule(rule_text)
    except ValueError:
        return len(REMEDIES) + 1
    key = f"mark:{rule['arg']}" if rule["kind"] == "mark" else rule["kind"]
    order = list(REMEDIES)
    return order.index(key) if key in order else len(REMEDIES)


# --------------------------------------------------------------- deciding
def _by_task(runs: list) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, list] = {}
    for t in runs:
        d = t.to_dict() if hasattr(t, "to_dict") else t
        s = (d.get("outcome") or {}).get("success")
        if s is None:
            continue
        out.setdefault(d["task"]["id"], []).append(bool(s))
    return {k: (sum(v), len(v)) for k, v in out.items()}


def _tokens(runs: list) -> Optional[float]:
    vals = []
    for t in runs:
        d = t.to_dict() if hasattr(t, "to_dict") else t
        tt = d.get("totals") or {}
        tot = int(tt.get("input_tokens") or 0) + int(tt.get("output_tokens") or 0)
        if not tot:
            tot = sum(int(s.get("tokens") or 0) for s in d.get("steps") or [])
        vals.append(tot)
    if not vals:
        return None
    vals.sort()
    return float(vals[len(vals) // 2])


def decide(base: list, cand: list) -> dict:
    """Keep or revert a changed harness, on the same tasks, run for run.

    Per task, the pass rate under each. Kept when the change wins more
    tasks than it loses and no task goes from always passing to always
    failing; otherwise reverted, a tie included."""
    a, b = _by_task(base), _by_task(cand)
    tasks = sorted(set(a) & set(b))
    wins, losses, broke = [], [], []
    for t in tasks:
        ra, rb = a[t][0] / a[t][1], b[t][0] / b[t][1]
        if rb > ra:
            wins.append(t)
        elif rb < ra:
            losses.append(t)
        if a[t][0] == a[t][1] and b[t][0] == 0:
            broke.append(t)
    passed_a = sum(v[0] for k, v in a.items() if k in tasks)
    passed_b = sum(v[0] for k, v in b.items() if k in tasks)
    n_a = sum(v[1] for k, v in a.items() if k in tasks)
    n_b = sum(v[1] for k, v in b.items() if k in tasks)
    if not tasks:
        verdict, why = "reverted", "no task was graded under both"
    elif broke:
        verdict, why = "reverted", f"it broke {', '.join(broke)}: always passed before, never with the change"
    elif len(wins) > len(losses):
        verdict, why = "kept", f"it won {len(wins)} task(s) and lost {len(losses)}"
    elif wins or losses:
        verdict, why = "reverted", f"it won {len(wins)} task(s) and lost {len(losses)}: not more wins than losses"
    else:
        verdict, why = "reverted", "no task changed: an instruction that does nothing is not kept"
    tok_a, tok_b = _tokens(base), _tokens(cand)
    return {"verdict": verdict, "why": why, "tasks": len(tasks), "wins": wins, "losses": losses, "broke": broke,
            "passed": {"current": [passed_a, n_a], "changed": [passed_b, n_b]},
            "median_tokens": {"current": tok_a, "changed": tok_b}}


# ------------------------------------------------------------ the action
def _views(runs: list) -> Tuple[list, dict]:
    views, wrong = [], {}
    for t in runs:
        traj = t if hasattr(t, "to_dict") else Trajectory.from_dict(t)
        s = traj.outcome.success
        if s is None:
            continue
        v = RunView(traj, None, set())
        views.append(v)
        wrong[id(v)] = not s
    _unique_keys(views)
    return views, wrong


def _one_generation_witnesses(runs: list) -> list:
    """Rules that fire on every failing run of this generation and no passing
    one: a hypothesis from one generation, for when the suite holds no eval yet."""
    views, wrong = _views(runs)
    if not any(wrong.values()):
        return []
    out = []
    for c in candidates(views, wrong):
        rule = c["rule"] if isinstance(c, dict) and "rule" in c else c
        try:
            s = _score(rule, views, wrong)
        except Exception:  # noqa: BLE001 — a rule that cannot read a run is not a witness
            continue
        if s["wrong"] and s["caught"] == s["wrong"] and s["false_alarms"] == 0:
            out.append({"rule": spec(rule), "caught": s["caught"], "wrong": s["wrong"], "right": s["right"]})
    return out


def _choose(evals_result: dict, label: str, runs: list, harness: Harness, tried: dict, check: str,
            refuse_knobs=()) -> dict:
    """The one change to test this generation, and every candidate weighed."""
    weighed = []
    for ev in evals_result.get("evals") or []:
        if ev.get("status") != "active":
            continue
        here = next((h for h in reversed(ev.get("history") or []) if h["generation"] == label), None)
        if not here or not here.get("caught"):
            continue
        weighed.append({"eval": ev["id"], "rule": ev["rule"], "caught": here["caught"], "wrong": here["wrong"],
                        "source": "eval", "says": ev.get("says")})
    if not weighed:
        for w in _one_generation_witnesses(runs):
            weighed.append({"eval": None, "rule": w["rule"], "caught": w["caught"], "wrong": w["wrong"],
                            "source": "this generation only" if w["right"] else
                                      "this generation only; every run failed, so nothing told it apart",
                            "says": describe(parse_rule(w["rule"]))})
    refuse = set(refuse_knobs)
    # most failures caught first; an adopted eval before a one-generation hypothesis; then the
    # order of REMEDIES, which puts the most direct knob first (checking before anything else)
    weighed.sort(key=lambda w: (-w["caught"], w["source"] != "eval", _priority(w["rule"]), str(w["rule"])))
    for w in weighed:
        r = remedy_for(w["rule"], check=check)
        if r is None or r.get("unactionable"):
            w["remedy"] = None
            w["skipped"] = (r or {}).get("unactionable") or "the rule does not parse"
            continue
        w["remedy"] = r
        if r["knob"] in refuse:
            w["skipped"] = f"an agent here cannot be given a {r['knob'].replace('_', ' ')}"
        elif r["id"] in harness.remedies:
            w["skipped"] = "already in the harness"
        elif tried.get(r["id"]) == "reverted":
            w["skipped"] = "tried before and reverted on the evidence"
    chosen = next((w for w in weighed if w.get("remedy") and not w.get("skipped")), None)
    return {"chosen": chosen, "weighed": weighed}


# ------------------------------------------------------------- the loop
def _fresh(target: str) -> dict:
    return {"kind": LEDGER_KIND, "version": LEDGER_VERSION, "target": target, "harness": asdict(Harness()),
            "generations": [], "tried": {}, "evals": None, "eval_lineage": []}


def self_evolve(run_arm: Callable[[Harness, str], list], *, generations: int = 3, target: str = "failure",
                ledger: Optional[dict] = None, patience: int = 2, check: str = "",
                refuse_knobs: tuple = (), on_progress: Optional[Callable[[str], None]] = None) -> dict:
    """Go round ``generations`` times. ``run_arm(harness, label)`` runs the
    agents under ``harness`` and returns their trajectories (each graded by
    its task's check); it is called once per arm. Returns the lineage, the
    harness as it ended, the eval suite's own lineage and the ledger."""
    say = on_progress or (lambda s: None)
    state = json.loads(json.dumps(ledger)) if ledger else _fresh(target)
    if state.get("kind") != LEDGER_KIND:
        raise ValueError("not a self-evolving ledger")
    harness = Harness.from_dict(state["harness"])
    carried: Optional[list] = None      # the kept candidate's runs: the next generation's
    evals_out: dict = {}
    start = len(state["generations"])
    stop = f"ran {generations} generation(s)"
    for g in range(start, start + generations):
        label = f"g{g}"
        if carried is not None:
            runs, carried = carried, None
            say(f"{label}: the runs of {harness.describe()} (kept last round) are this generation's")
        else:
            say(f"{label}: running the agents under {harness.describe()}")
            runs = list(run_arm(harness, f"{label}-h{harness.version}"))
        trajs = [t if hasattr(t, "to_dict") else Trajectory.from_dict(t) for t in runs]
        graded = [t for t in trajs if t.outcome.success is not None]
        failed = [t for t in graded if not t.outcome.success]

        # 2. judge: the evals meet this generation first
        evals_out = evolve_evals([(label, trajs)], target=target, ledger=state["evals"], patience=patience)
        state["evals"] = evals_out["ledger"]
        egen = next((x for x in evals_out["lineage"] if x["generation"] == label), {})
        state.setdefault("eval_lineage", []).append(egen)
        for rid in egen.get("retired") or []:
            e = state["evals"]["evals"].get(rid) or {}
            if e.get("source") == "intervention" and e.get("reason") and "the harness" not in e["reason"]:
                # the loop closed: the change this eval was born from is why it went quiet
                e["reason"] += f"; harness v{harness.version} prevents it"
        record = {"generation": label, "harness": asdict(harness), "runs": len(graded), "failed": len(failed),
                  "evals": {k: egen.get(k) for k in ("arrived", "forward", "retired", "born", "reborn", "verdicts")}}
        if not graded:
            record["action"] = None
            state["generations"].append(record)
            stop = f"{label}: no run was graded, so nothing can be judged"
            break
        if not failed:
            record["action"] = None
            state["generations"].append(record)
            stop = f"converged at {label}: every run passed under {harness.describe()}"
            say(stop)
            break

        # 3. act: one change, from the evals that caught these failures
        choice = _choose(evals_out, label, trajs, harness, state["tried"], check, refuse_knobs)
        record["weighed"] = choice["weighed"]
        chosen = choice["chosen"]
        if chosen is None:
            record["action"] = None
            state["generations"].append(record)
            if not choice["weighed"]:
                stop = (f"{label}: no eval, and no rule over how the runs worked, tells the {len(failed)} "
                        f"failure(s) apart from the {len(graded) - len(failed)} passing run(s), so there is "
                        f"nothing the harness can act on")
            else:
                stop = (f"{label}: {len(failed)} failure(s); every candidate weighed was already tried, or names "
                        f"no change the harness can make (see what was weighed)")
            say(stop)
            break
        remedy = chosen["remedy"]
        candidate = harness.with_remedy(remedy)
        say(f"{label}: {chosen['caught']} of {chosen['wrong']} failure(s) caught by "
            f"{chosen['eval'] or chosen['rule']}; testing: {remedy_text(remedy)}")

        # 4. test it, paired: same tasks, same number of runs
        cand_runs = list(run_arm(candidate, f"{label}-h{candidate.version}"))
        verdict = decide(trajs, cand_runs)
        state["tried"][remedy["id"]] = verdict["verdict"]
        record["action"] = {"remedy": remedy, "because": {k: chosen[k] for k in ("eval", "rule", "caught", "wrong",
                                                                                  "source", "says")},
                            "test": verdict}
        say(f"{label}: {verdict['verdict']}: {verdict['why']} "
            f"(passed {verdict['passed']['current'][0]}/{verdict['passed']['current'][1]} → "
            f"{verdict['passed']['changed'][0]}/{verdict['passed']['changed'][1]})")
        if verdict["verdict"] == "kept":
            harness = candidate
            carried = cand_runs     # 5. the evals meet what the harness produced
            born = _adopt_by_intervention(state, chosen, label, verdict, len(graded) - len(failed))
            if born:
                egen.setdefault("born", []).append(born)
                egen.setdefault("born_by_intervention", []).append(born)
                record["evals"]["born"] = egen["born"]
                say(f"{label}: {born} joins the eval suite, born by intervention")
        state["generations"].append(record)
        state["harness"] = asdict(harness)
    state["harness"] = asdict(harness)
    lineage = state["generations"]
    evals = {}
    if evals_out:
        # the suite's whole life, every generation of it, in the shape `evolve-evals` writes
        evals = {k: v for k, v in evals_out.items() if k not in ("ledger", "lineage", "narrative")}
        # the suite as the ledger holds it, born-by-intervention evals included
        every = [{"id": rid, **e} for rid, e in sorted((state["evals"] or {}).get("evals", {}).items())]
        evals.update(evals=every, active=[e["id"] for e in every if e["status"] == "active"],
                     retired=[e["id"] for e in every if e["status"] == "retired"])
        evals["lineage"] = state["eval_lineage"]
        evals["narrative"] = evals_narrative(state["eval_lineage"], target)
    return {"kind": "self-evolve", "target": target, "harness": asdict(harness), "describe": harness.describe(),
            "stop": stop, "lineage": lineage, "evals": evals,
            "narrative": _narrative(lineage, harness, stop), "ledger": state}


def _adopt_by_intervention(state: dict, chosen: dict, label: str, verdict: dict, right: int) -> Optional[str]:
    """A rule whose harness change was kept has evidence a correlation never
    gives: preventing what it flags raised the pass rate, on the same tasks.
    It joins the eval suite (when it is not there already) and is carried
    forward like any other eval: forward-tested on every later generation,
    and retired once the harness has fixed the failure it caught."""
    if chosen.get("eval") or not state.get("evals"):
        return None
    rule = parse_rule(chosen["rule"])
    rid = rule_id(rule)
    evals = state["evals"].setdefault("evals", {})
    if rid in evals and evals[rid].get("status") == "active":
        return None
    pc, pn = verdict["passed"]["current"], verdict["passed"]["changed"]
    evals[rid] = {"rule": spec(rule), "says": describe(rule), "source": "intervention", "born": label,
                  "status": "active", "quiet": 0, "retired_at": None,
                  "reason": (f"born at {label} by intervention: the harness change that prevents it raised passes "
                             f"from {pc[0]}/{pc[1]} to {pn[0]}/{pn[1]}"),
                  "history": [{"generation": label, "verdict": "born", "caught": chosen["caught"],
                               "wrong": chosen["wrong"], "false_alarms": 0, "right": right}]}
    return rid


def _narrative(lineage: list, harness: Harness, stop: str) -> str:
    parts = []
    for g in lineage:
        bit = f"{g['generation']}: {g['failed']} of {g['runs']} run(s) failed under v{g['harness']['version']}"
        ev = g.get("evals") or {}
        if ev.get("born"):
            bit += f"; evals born: {', '.join(ev['born'])}"
        if ev.get("retired"):
            bit += f"; evals retired: {', '.join(ev['retired'])}"
        a = g.get("action")
        if a:
            t = a["test"]
            bit += (f"; tried {a['remedy']['id']} (from {a['because']['eval'] or 'a one-generation hypothesis'}): "
                    f"{t['verdict']}, {t['why']}")
        parts.append(bit + ".")
    parts.append(f"Stopped: {stop}. The harness now: {harness.describe()}.")
    return " ".join(parts)


def load_ledger(path: Union[str, Path]) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != LEDGER_KIND:
        raise ValueError(f"{p}: not a self-evolving ledger")
    return data


def write_ledger(path: Union[str, Path], ledger: dict) -> None:
    Path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def visible_check(checks: list) -> str:
    """The one check every task shares, when the agent may be told it: never a
    command that names a path outside the workspace (a held-out grader)."""
    import re
    if len(set(checks)) != 1:
        return ""
    c = checks[0]
    return "" if re.search(r"(^|[\s='\"])(/|~|\.\./)", c) else c
