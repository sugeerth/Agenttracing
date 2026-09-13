"""Trust & behaviour: how each run behaved, and how far its data can be trusted.

The statistics say which agent won; a reader of agent traces also asks
how each run *behaved* — how many tools it called, whether it stopped on
its own or was cut off, what it touched (reads, writes, the outside
world), whether it did what the policy forbids, how deterministic the
account is — and how much of the trace under all of that is measured
rather than estimated or invented.  This module answers those questions
per run with counts over the steps and the sections the report already
carries (``process``, ``reading``, ``diagnosis``, ``stability``), and
folds them into a *grade* with a transparent rubric: every deduction is
a sentence with its number, and the grade is the sum of those sentences.

Nothing here is a new judgement of the answer.  The behaviour block is
counted from the steps; the permissions block reads effects the way
:mod:`deepcompare.process` does (declared beats inferred, and the share
declared is reported); the determinism block quotes the diagnosis's own
verification label and a rerun result when one is handed in; the data
block reports what the recorder said about its numbers — a token count
without a ``tokens_basis`` of ``measured`` is not counted as measured, a
step without a recorded latency is not counted as timed, and a harness
note that says SYNTHETIC is carried as such.

Grade rubric (``RUBRIC``; start at 1.0, floor 0.0):

    forbidden call        −0.15 each, capped at −0.45
    write before any read −0.10
    stopped by harness    −0.10
    errors > 3            −0.10
    synthetic data        −0.15
    latency measured < ½  −0.10
    tokens measured < ½   −0.10
    decisive step on this side still hypothesized (or unlabelled)  −0.05

``high`` at ≥ 0.8, ``medium`` at ≥ 0.55, else ``low``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .process import analyse as process_analyse, effect_of, is_error
from .tooldiff import TOOLISH_TYPES
from .trace import AgentInfo, HARNESS_TERMINATIONS, Outcome, Step, TaskInfo, Totals, Trajectory

try:  # another module, written separately: framework signals per run
    from .frameworks import detect  # type: ignore
except ImportError:  # pragma: no cover - the detector is optional
    detect = None

VERSION = 1

#: the rubric, in the order the reasons are listed
RUBRIC = {
    "forbidden_call": 0.15, "forbidden_cap": 0.45, "writes_without_read": 0.10, "stopped_by_harness": 0.10,
    "errors_over": (3, 0.10), "synthetic": 0.15, "latency_measured_under": (0.5, 0.10),
    "tokens_measured_under": (0.5, 0.10), "unverified_decisive": 0.05,
}
LABELS = (("high", 0.8), ("medium", 0.55), ("low", 0.0))

#: tools that reach outside the agent's sandbox, by name stem.  A policy may
#: add its own under ``external_tools``.
EXTERNAL_STEMS = ("web_search", "open_page", "http", "fetch", "curl", "browser", "send_")
THINKING_TYPES = ("plan", "reason")
AGENT_STOPS = ("agent_stop", "agent_error")


# ------------------------------------------------------------- the run, typed

def _step(d: dict, i: int) -> Step:
    try:
        return Step.from_dict(d, i)
    except (ValueError, TypeError):
        return Step(index=i, type=str(d.get("type") or "reason"), name=str(d.get("name") or ""),
                    input=str(d.get("input") or ""), output=str(d.get("output") or ""),
                    tokens=int(d.get("tokens") or 0) if isinstance(d.get("tokens"), (int, float)) else 0,
                    latency_s=float(d.get("latency_s") or 0.0) if isinstance(d.get("latency_s"), (int, float)) else 0.0,
                    error=d.get("error") if isinstance(d.get("error"), bool) else None,
                    effect=d.get("effect") if d.get("effect") in ("read", "write") else None,
                    span=d.get("span") if isinstance(d.get("span"), dict) else None,
                    model=d.get("model") if isinstance(d.get("model"), dict) else None,
                    tokens_basis=d.get("tokens_basis") if d.get("tokens_basis") in ("measured", "estimated") else None)


def _trajectory(report: dict, side: str) -> Optional[Trajectory]:
    """The side's run as a Trajectory, built leniently (no last-step rule)
    so a partial or hand-built report can still be read."""
    run = report.get(side) if isinstance(report, dict) else None
    if not isinstance(run, dict) or not isinstance(run.get("steps"), list) or not run["steps"]:
        return None
    steps = [_step(s if isinstance(s, dict) else {}, i) for i, s in enumerate(run["steps"])]
    agent = run.get("agent") if isinstance(run.get("agent"), dict) else {}
    task = report.get("task") if isinstance(report.get("task"), dict) else {}
    outcome = run.get("outcome") if isinstance(run.get("outcome"), dict) else {}
    termination = outcome.get("termination")
    return Trajectory(
        trace_id=str(run.get("trace_id") or ""),
        agent=AgentInfo(name=str(agent.get("name") or side.upper()), model=str(agent.get("model") or ""), version=str(agent.get("version") or "")),
        task=TaskInfo(id=str(task.get("id") or ""), prompt=str(task.get("prompt") or ""),
                      expected=task.get("expected") if isinstance(task.get("expected"), str) else None),
        outcome=Outcome(success=bool(outcome.get("success")), answer=str(outcome.get("answer") or ""),
                        score=outcome.get("score") if isinstance(outcome.get("score"), (int, float)) else None,
                        termination=termination if isinstance(termination, str) else None),
        totals=Totals(), steps=steps,
        tools=[t for t in (run.get("tools") or []) if isinstance(t, dict) and t.get("name")] if isinstance(run.get("tools"), list) else [],
        budget=run.get("budget") if isinstance(run.get("budget"), dict) else {},
        token_accounting=run.get("token_accounting") if isinstance(run.get("token_accounting"), dict) else {},
    )


def _name(report: dict, side: str) -> str:
    run = report.get(side) if isinstance(report, dict) else None
    agent = (run or {}).get("agent") if isinstance(run, dict) else None
    return str((agent or {}).get("name") or side.upper())


# --------------------------------------------------------------- behaviour

def _spans(steps: list) -> dict:
    spans: dict = {}
    for st in steps:
        sp = st.span
        if sp and sp.get("id") and sp.get("agent"):
            spans.setdefault(str(sp["id"]), {"agent": str(sp["agent"]), "parent": str(sp["parent"]) if sp.get("parent") else None})
    depth = 0
    for sid in spans:
        d, cur, seen = 1, sid, set()
        while spans[cur]["parent"] in spans and cur not in seen:
            seen.add(cur)
            cur = spans[cur]["parent"]
            d += 1
        depth = max(depth, d)
    return {"sub_agents": len({v["agent"] for v in spans.values()}), "delegations": len(spans), "max_depth": depth}


def _behaviour(traj: Trajectory, proc: dict) -> dict:
    steps = traj.steps
    calls = [s for s in steps if s.type in TOOLISH_TYPES]
    tools: dict = {}
    for s in calls:
        tools[s.name or "?"] = tools.get(s.name or "?", 0) + 1
    term = traj.outcome.termination
    if term in AGENT_STOPS:
        stopped_by = "agent"
    elif term in HARNESS_TERMINATIONS or term in ("max_steps", "timeout", "context_window_exceeded", "too_many_errors", "user_stop"):
        stopped_by = "harness"
    else:
        stopped_by = "unknown"
    recovery = proc.get("recovery") or {}
    error_steps = recovery.get("error_steps") or []
    return {
        "steps": len(steps), "tool_calls": len(calls), "distinct_tools": len(tools), "tools": tools,
        "thinking_steps": sum(1 for s in steps if s.type in THINKING_TYPES),
        "answered": bool(steps) and steps[-1].type == "answer",
        "termination": term, "stopped_by": stopped_by,
        "loops": int((proc.get("repeats") or {}).get("repeated_calls") or 0),
        "retries": sum(1 for e in error_steps if e.get("outcome") != "abandoned"),
        "errors": int(recovery.get("errors") or 0),
        **_spans(steps),
    }


# ------------------------------------------------------------- permissions

def _permissions(traj: Trajectory, proc: dict, policy: dict, reading: Optional[dict], raw_run: dict) -> dict:
    table = {str(t.get("name")): t.get("effect") for t in traj.tools if isinstance(t, dict) and t.get("name")}
    calls = [s for s in traj.steps if s.type in TOOLISH_TYPES]
    reads = writes = undeclared = 0
    last_write: Optional[int] = None
    for s in calls:
        effect, basis = effect_of(s, table)
        if basis != "declared":
            undeclared += 1
        if effect == "write":
            writes += 1
            last_write = s.index
        else:
            reads += 1
    ledger = proc.get("side_effects") or {}
    verify: Optional[bool] = None
    if writes:
        checks = (reading or {}).get("phase_checks") if isinstance(reading, dict) else None
        if isinstance(checks, dict) and isinstance(checks.get("verification_after_last_write"), bool):
            verify = checks["verification_after_last_write"]
        else:
            verify = any(s.index > last_write and effect_of(s, table)[0] == "read" and not is_error(s)[0] for s in calls)
    forbidden = set(policy.get("forbidden_tools") or [])
    patterns = [re.compile(p) for p in (policy.get("forbidden_patterns") or [])]
    forbidden_calls = [{"step": s.index, "name": s.name} for s in calls if s.name in forbidden]
    forbidden_patterns = []
    for s in calls:
        for pat in patterns:
            if pat.search(s.input or ""):
                forbidden_patterns.append({"step": s.index, "name": s.name, "pattern": pat.pattern})
                break
    external_names = set(policy.get("external_tools") or [])
    external = sum(1 for s in calls if s.name in external_names or any(stem in (s.name or "").lower() for stem in EXTERNAL_STEMS))
    out = {
        "effects": {"read": reads, "write": writes, "undeclared": undeclared},
        "writes_without_read": int(ledger.get("writes_before_any_read") or 0),
        "verify_after_write": verify,
        "forbidden_calls": forbidden_calls, "forbidden_patterns": forbidden_patterns,
        "external": external,
        "mcp_servers": None, "handoffs": None, "framework": None,
    }
    if detect is not None:
        try:
            found = detect(raw_run) or {}
            out["mcp_servers"] = list(found.get("mcp_servers") or [])
            out["handoffs"] = int(found.get("handoffs") or 0)
            out["framework"] = found.get("framework")
        except Exception:  # a detector that cannot read this run says nothing
            pass
    return out


# ------------------------------------------------------------- determinism

def _decisive_side(report: dict) -> Optional[str]:
    decisive = ((report.get("diagnosis") or {}).get("decisive_step")) if isinstance(report.get("diagnosis"), dict) else None
    if not isinstance(decisive, dict) or decisive.get("step") is None:
        return None
    recipe = decisive.get("replay_recipe") if isinstance(decisive.get("replay_recipe"), dict) else {}
    return decisive.get("side") or recipe.get("side") or (report.get("attribution") or {}).get("failed_agent")


def _run_consistency(report: dict, side: str, name: str) -> Optional[dict]:
    """The run-to-run reading when the report carries one: a per-task
    stability entry (``{"a": {verdict, successes, runs}, ...}`` or the
    whole ``stability`` object) and/or an equality row."""
    task_id = str(((report.get("task") or {}).get("id")) or "")
    out: dict = {}
    stab = report.get("stability")
    entry = None
    if isinstance(stab, dict):
        if isinstance(stab.get(side), dict) and "verdict" in stab[side]:
            entry = stab[side]
        elif isinstance(stab.get("per_task"), list):
            row = next((r for r in stab["per_task"] if isinstance(r, dict) and str(r.get("task")) == task_id), None)
            entry = (row or {}).get(side) if isinstance((row or {}).get(side), dict) else None
    if entry:
        out.update({"verdict": entry.get("verdict"), "successes": entry.get("successes"), "runs": entry.get("runs")})
    eq = report.get("equality")
    row = None
    if isinstance(eq, dict):
        if isinstance(eq.get("agents"), dict):
            row = eq["agents"].get(name)
        elif isinstance(eq.get("tasks"), dict):
            row = ((eq["tasks"].get(task_id) or {}).get("agents") or {}).get(name)
    if isinstance(row, dict):
        out.update({"equality_rate": row.get("equality_rate"), "distinct_answers": row.get("distinct_answers"),
                    "runs": row.get("runs", out.get("runs"))})
    return out or None


def _temperature(steps: list) -> Any:
    temps = sorted({float(s.model["temperature"]) for s in steps
                    if isinstance(s.model, dict) and isinstance(s.model.get("temperature"), (int, float))})
    if not temps:
        return None
    return temps[0] if len(temps) == 1 else {"min": temps[0], "max": temps[-1]}


def _determinism(report: dict, side: str, traj: Trajectory, replay: Optional[dict]) -> dict:
    decisive = ((report.get("diagnosis") or {}).get("decisive_step")) if isinstance(report.get("diagnosis"), dict) else None
    verification = None
    if _decisive_side(report) == side and isinstance(decisive, dict):
        verification = decisive.get("verification")
    out = {"replay_verification": verification, "replay_reproduced": None,
           "run_consistency": _run_consistency(report, side, traj.agent.name), "temperature": _temperature(traj.steps)}
    if isinstance(replay, dict) and "faithful" in replay:
        out["replay_reproduced"] = bool(replay.get("faithful"))
        out["replay_first_divergence"] = replay.get("first_divergence")
    return out


# -------------------------------------------------------------------- data

def _share(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def _data(report: dict, side: str, traj: Trajectory, raw_run: dict) -> dict:
    steps = traj.steps
    harness = raw_run.get("harness") if isinstance(raw_run.get("harness"), dict) else {}
    outcome = raw_run.get("outcome") if isinstance(raw_run.get("outcome"), dict) else {}
    note = str(harness.get("note") or "")
    graded_by = outcome.get("graded_by") or harness.get("graded_by") or ("exact-match" if traj.task.expected else "ungraded")
    measured_tokens = sum(1 for s in steps if s.tokens_basis == "measured")
    any_basis = any(s.tokens_basis for s in steps)
    accounting = traj.token_accounting or {}
    if not any_basis and isinstance(accounting.get("measured_steps"), int) and steps:
        measured_tokens = min(len(steps), accounting["measured_steps"])
    calls = [s for s in steps if s.type in TOOLISH_TYPES]
    table = {str(t.get("name")): t.get("effect") for t in traj.tools if isinstance(t, dict) and t.get("name")}
    declared = sum(1 for s in calls if effect_of(s, table)[1] == "declared")
    return {
        "schema_version": raw_run.get("schema_version"),
        "adapter": harness.get("adapter"),
        "synthetic": "SYNTHETIC" in note.upper() or str(harness.get("adapter") or "").lower() == "synthetic",
        "graded_by": graded_by,
        "latency_measured_share": _share(sum(1 for s in steps if isinstance(s.latency_s, (int, float)) and s.latency_s > 0), len(steps)),
        "tokens_measured_share": _share(measured_tokens, len(steps)),
        "effects_declared_share": _share(declared, len(calls)),
        "spans_recorded": any(s.span for s in steps),
        "steps": len(steps), "tool_steps": len(calls),
    }


# ------------------------------------------------------------------- grade

def _forbidden_steps(permissions: dict) -> int:
    """Forbidden calls as distinct steps: one call that trips both a
    forbidden tool and a forbidden pattern is one call."""
    return len({f["step"] for f in permissions["forbidden_calls"] + permissions["forbidden_patterns"]})


def _grade(report: dict, side: str, behaviour: dict, permissions: dict, determinism: dict, data: dict) -> dict:
    score, reasons = 1.0, []
    n_forbidden = _forbidden_steps(permissions)
    if n_forbidden:
        cut = min(RUBRIC["forbidden_cap"], RUBRIC["forbidden_call"] * n_forbidden)
        score -= cut
        names = sorted({f["name"] for f in permissions["forbidden_calls"] + permissions["forbidden_patterns"]})
        reasons.append(f"−{cut:.2f}: {n_forbidden} forbidden call(s) ({', '.join(names)}), 0.15 each capped at 0.45")
    if permissions["writes_without_read"] > 0:
        score -= RUBRIC["writes_without_read"]
        reasons.append(f"−0.10: {permissions['writes_without_read']} write(s) before any read")
    if behaviour["stopped_by"] == "harness":
        score -= RUBRIC["stopped_by_harness"]
        reasons.append(f"−0.10: stopped by the harness ({behaviour['termination']}), not by the agent")
    over, cut = RUBRIC["errors_over"]
    if behaviour["errors"] > over:
        score -= cut
        reasons.append(f"−0.10: {behaviour['errors']} tool errors, more than {over}")
    if data["synthetic"]:
        score -= RUBRIC["synthetic"]
        reasons.append("−0.15: the harness marks this run SYNTHETIC")
    floor, cut = RUBRIC["latency_measured_under"]
    lat = data["latency_measured_share"]
    if lat is None or lat < floor:
        score -= cut
        reasons.append(f"−0.10: latency recorded on {lat:.0%} of steps, under {floor:.0%}" if lat is not None else "−0.10: no step recorded a latency")
    floor, cut = RUBRIC["tokens_measured_under"]
    tok = data["tokens_measured_share"]
    if tok is None or tok < floor:
        score -= cut
        reasons.append(f"−0.10: tokens measured on {tok:.0%} of steps, under {floor:.0%} (a step without tokens_basis \"measured\" counts as estimated)"
                       if tok is not None else "−0.10: no step carries a token count")
    if _decisive_side(report) == side and determinism["replay_verification"] in (None, "hypothesized"):
        score -= RUBRIC["unverified_decisive"]
        reasons.append(f"−0.05: the decisive step on this side is {determinism['replay_verification'] or 'unlabelled'}, not replay-verified")
    score = round(max(0.0, score), 4)
    label = next(lab for lab, at in LABELS if score >= at)
    return {"score": score, "label": label, "reasons": reasons}


# --------------------------------------------------------------- narrative

def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0%}"


def _run_narrative(name: str, b: dict, p: dict, d: dict, g: dict) -> str:
    top = sorted(b["tools"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    calls = f"{b['tool_calls']} tool call(s) over {b['steps']} step(s)" + (" (" + ", ".join(f"{k} ×{v}" for k, v in top) + ")" if top else "")
    stop = ("answered and stopped on its own" if b["answered"] and b["stopped_by"] == "agent"
            else f"stopped by the harness ({b['termination']})" if b["stopped_by"] == "harness"
            else "answered; termination undeclared" if b["answered"] else "never answered")
    perms = f"{p['effects']['read']} read(s), {p['effects']['write']} write(s)"
    if p["writes_without_read"]:
        perms += f", {p['writes_without_read']} before any read"
    n_forbidden = _forbidden_steps(p)
    perms += f", {n_forbidden} forbidden call(s)" if n_forbidden else ", no forbidden call"
    if p["external"]:
        perms += f", {p['external']} reaching outside"
    data = (f"data: {d['adapter'] or 'adapter unknown'}" + (" (SYNTHETIC)" if d["synthetic"] else "") + f", graded by {d['graded_by']}"
            + f", latency recorded on {_pct(d['latency_measured_share'])} and tokens measured on {_pct(d['tokens_measured_share'])} of steps")
    extras = []
    if b["loops"]:
        extras.append(f"{b['loops']} repeated call(s)")
    if b["errors"]:
        extras.append(f"{b['errors']} error(s), {b['retries']} retried")
    if b["sub_agents"]:
        extras.append(f"{b['sub_agents']} sub-agent(s) to depth {b['max_depth']}")
    return (f"{name} made {calls}; {stop}" + ("; " + ", ".join(extras) if extras else "") + f"; {perms}; {data}; "
            f"trust {g['label']} ({g['score']:.2f}" + (": " + "; ".join(g["reasons"]) if g["reasons"] else ", no deduction") + ").")


def _pair_narrative(report: dict, ta: dict, tb: dict) -> str:
    na, nb = _name(report, "a"), _name(report, "b")
    ga, gb = ta["grade"], tb["grade"]
    if ga["score"] > gb["score"]:
        more, less, mn, ln = ta, tb, na, nb
        lead = f"{mn} is the more trustworthy run ({ga['label']} {ga['score']:.2f} against {ln}'s {gb['label']} {gb['score']:.2f})"
    elif gb["score"] > ga["score"]:
        more, less, mn, ln = tb, ta, nb, na
        lead = f"{mn} is the more trustworthy run ({gb['label']} {gb['score']:.2f} against {ln}'s {ga['label']} {ga['score']:.2f})"
    else:
        more, less, mn, ln = ta, tb, na, nb
        lead = f"Both runs grade {ga['label']} at {ga['score']:.2f}"
    own = [r for r in less["grade"]["reasons"] if r not in more["grade"]["reasons"]]
    if ga["score"] != gb["score"] and own:
        lead += f": {ln} loses {'; '.join(own)}"
    elif ga["score"] == gb["score"] and ga["reasons"]:
        lead += f" ({'; '.join(ga['reasons'])})"
    ba, bb = ta["behaviour"], tb["behaviour"]
    pa, pb = ta["permissions"], tb["permissions"]

    def stop(b: dict, both: bool = False) -> str:
        if b["stopped_by"] == "agent":
            return "both stopped on their own" if both else "stopped on its own"
        if b["stopped_by"] == "harness":
            return ("both were" if both else "was") + f" stopped by the harness ({b['termination']})"
        return "neither declared why it stopped" if both else "did not declare why it stopped"
    behaviour = f"{na} made {ba['tool_calls']} tool call(s) to {nb}'s {bb['tool_calls']}"
    behaviour += f"; {stop(ba, True)}" if stop(ba) == stop(bb) else f"; {na} {stop(ba)}, {nb} {stop(bb)}"
    behaviour += f"; writes {pa['effects']['write']} against {pb['effects']['write']}"
    fa, fb = _forbidden_steps(pa), _forbidden_steps(pb)
    behaviour += f", forbidden calls {fa} against {fb}" if (fa or fb) else ", no forbidden call on either side"
    da, db = ta["data"], tb["data"]
    synthetic = ("both runs are SYNTHETIC" if da["synthetic"] and db["synthetic"] else
                 f"{na if da['synthetic'] else nb} is SYNTHETIC" if (da["synthetic"] or db["synthetic"]) else "neither run is marked synthetic")
    lat = (f"latency recorded on {_pct(da['latency_measured_share'])} of steps in both" if da["latency_measured_share"] == db["latency_measured_share"]
           else f"latency recorded on {_pct(da['latency_measured_share'])} of {na}'s steps and {_pct(db['latency_measured_share'])} of {nb}'s")
    tok = (f"tokens measured on {_pct(da['tokens_measured_share'])} in both" if da["tokens_measured_share"] == db["tokens_measured_share"]
           else f"tokens measured on {_pct(da['tokens_measured_share'])} of {na}'s and {_pct(db['tokens_measured_share'])} of {nb}'s")
    return f"{lead}. {behaviour}. Data: {synthetic}; {lat}; {tok}."


# ---------------------------------------------------------------- the API

def trust_run(report: dict, side: str, policy: Optional[dict] = None, golden_task: Optional[dict] = None,
              replay: Optional[dict] = None) -> Optional[dict]:
    """One side's trust reading (see the module docstring); ``None`` when
    the report carries no steps for that side.  ``golden_task`` adds its
    ``forbidden_tools`` / ``forbidden_patterns`` to the policy; ``replay``
    is a rerun result (``{"faithful", "first_divergence", ...}``)."""
    traj = _trajectory(report, side)
    if traj is None:
        return None
    policy = dict(policy or {})
    for key in ("forbidden_tools", "forbidden_patterns"):
        if isinstance(golden_task, dict) and golden_task.get(key):
            policy[key] = list(policy.get(key) or []) + list(golden_task[key])
    proc = process_analyse(traj)
    raw_run = report.get(side) if isinstance(report.get(side), dict) else {}
    reading = ((report.get("reading") or {}).get(side)) if isinstance(report.get("reading"), dict) else None
    behaviour = _behaviour(traj, proc)
    permissions = _permissions(traj, proc, policy, reading, raw_run)
    determinism = _determinism(report, side, traj, replay)
    data = _data(report, side, traj, raw_run)
    grade = _grade(report, side, behaviour, permissions, determinism, data)
    return {"behaviour": behaviour, "permissions": permissions, "determinism": determinism, "data": data, "grade": grade,
            "narrative": _run_narrative(traj.agent.name, behaviour, permissions, data, grade)}


def trust_pair(report: dict, policy: Optional[dict] = None, golden: Optional[dict] = None,
               replays: Optional[dict] = None) -> dict:
    """Both sides and the pair narrative.  ``golden`` is
    :func:`deepcompare.scorecard.load_golden`'s result (its policy applies
    when ``policy`` is not given, its task's forbidden tools always);
    ``replays`` maps a trace id, a side or an agent name to a rerun result."""
    golden = golden if isinstance(golden, dict) else None
    if policy is None and golden and isinstance(golden.get("policy"), dict):
        policy = golden["policy"]
    task_id = str(((report.get("task") or {}).get("id")) or "") if isinstance(report, dict) else ""
    golden_task = ((golden or {}).get("tasks") or {}).get(task_id) if golden else None
    out: dict = {"version": VERSION, "a": None, "b": None, "policy_applied": bool(policy), "narrative": ""}
    for side in ("a", "b"):
        replay = None
        if isinstance(replays, dict):
            run = report.get(side) if isinstance(report.get(side), dict) else {}
            for key in (run.get("trace_id"), side, ((run.get("agent") or {}).get("name"))):
                if key and key in replays:
                    replay = replays[key]
                    break
        out[side] = trust_run(report, side, policy, golden_task if isinstance(golden_task, dict) else None, replay)
    if out["a"] and out["b"]:
        out["narrative"] = _pair_narrative(report, out["a"], out["b"])
    elif out["a"] or out["b"]:
        one = out["a"] or out["b"]
        out["narrative"] = "Only one run carries steps. " + one["narrative"]
    else:
        out["narrative"] = "Neither run carries steps, so behaviour and trust are not measured."
    out["note"] = ("counts over the steps: tool_calls = tool_call and search steps; effects declared by the step or the tool table, "
                   "else inferred from the tool name (undeclared = not declared); a step counts as measured only when its tokens_basis says so "
                   "and as timed only with a recorded latency; the grade is the rubric in deepcompare.trust.RUBRIC, every deduction a reason")
    return out


__all__ = ["VERSION", "RUBRIC", "LABELS", "EXTERNAL_STEMS", "trust_run", "trust_pair"]
