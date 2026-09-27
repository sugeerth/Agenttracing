"""Two vendor agents on the same task: a fair report, and what each one made.

A comparison of two coding agents is only as good as its setup, and the
setup is where vendor comparisons usually go wrong without saying so: one
agent ran in a sandbox and the other did not, one had a budget enforced
while it worked and the other was checked afterwards, one reported what it
cost and the other left it to be guessed. So the report opens with a
**parity ledger** — every condition that should have been equal, whether
it was, and what differed — before a single number, and the narrative
names the conditions that were not equal in its first sentence.

Then, in the order a reader needs them:

* **Outcome** — the check the operator named (``pytest -q``, a build),
  run by the harness after the agent stopped, never the agent's own claim.
  Passes out of runs with a Wilson interval, per agent and per task.
* **Spend, with a budget band** — tokens (input with its cached part,
  output with its reasoning part), cost where the vendor reported one,
  wall time. Two runs of one task are **budget-matched** when their token
  totals are within the band (10% by default) of the larger; a comparison
  of outcomes between runs that spent very differently says so, and the
  report gives passing runs per million tokens alongside, so a cheap agent
  that passes as often is visible as the better buy.
* **What each one made** — the files each run changed, lines added and
  removed, whether it touched tests, which files both agents changed and
  whether they ended up identical.
* **How each one worked** — every action classed as explore, edit, verify,
  run, research, plan or delegate; how much it explored before its first
  edit; whether it ran a verifying command *after* its last edit (an agent
  that edits and stops has not checked its own work); errors it never came
  back to and stretches that produced nothing new, from the same reading
  every other trace on the page gets (:mod:`deepcompare.excerpt`).

Nothing here calls a vendor or a network: it reads the records the harness
wrote (:mod:`deepcompare.harness.vendors`).
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from . import excerpt
from ._text import plural
from .statistics import wilson_interval
from .trace import Trajectory

__all__ = ["duel_report", "classify", "run_profile", "BAND", "ACTIVITIES"]

#: two runs are budget-matched when their token totals are within this
#: share of the larger
BAND = 0.10

ACTIVITIES = ("explore", "edit", "verify", "run", "research", "plan", "delegate", "other")

#: a command that checks the work: tests, builds, type checks, linters
_VERIFY = re.compile(
    r"\b(pytest|py\.test|unittest|nosetests|tox|nox|jest|vitest|mocha|go\s+(test|build|vet)|"
    r"cargo\s+(test|build|check|clippy)|(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck|check)|"
    r"make(\s+\S*test\S*|\s+check|\s+build)?\s*($|&&|;|\|)|mvn\s+(test|verify)|gradle\w*\s+(test|build|check)|"
    r"ruff|mypy|pyright|flake8|pylint|eslint|tsc\b|dotnet\s+(test|build)|ctest|bazel\s+test)")
#: a command that only looks
_EXPLORE_WORDS = {"ls", "cat", "head", "tail", "rg", "grep", "egrep", "find", "fd", "tree", "wc", "pwd",
                  "nl", "less", "more", "file", "stat", "du", "which", "echo", "sed", "awk", "jq", "diff"}
_EXPLORE_GIT = re.compile(r"\bgit\s+(status|diff|log|show|ls-files|grep|blame|branch)\b")
_WRAPPER = re.compile(r"^\s*(/usr/bin/env\s+)?(/bin/|/usr/bin/)?(ba|z)?sh\s+-l?c\s+", re.I)

_CLAUDE_KIND = {"Read": "explore", "Grep": "explore", "Glob": "explore", "LS": "explore",
                "Edit": "edit", "Write": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
                "WebSearch": "research", "WebFetch": "research", "TodoWrite": "plan",
                "Task": "delegate", "Agent": "delegate"}
_TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)|(^|/)test_[^/]+$|_test\.\w+$|\.(test|spec)\.\w+$")


def classify_command(command: str) -> str:
    """explore | verify | edit | run, for one shell command."""
    cmd = _WRAPPER.sub("", command or "").strip().strip("'\"")
    if _VERIFY.search(cmd):
        return "verify"
    if re.search(r"(^|\s)(>|>>|tee\s|sed\s+-i|perl\s+-pi|patch\s|apply_patch|git\s+apply)", cmd):
        return "edit"
    first = re.split(r"[\s;&|]+", cmd, maxsplit=1)[0].rsplit("/", 1)[-1] if cmd else ""
    if first == "cd":
        rest = re.split(r"&&|;", cmd, maxsplit=1)
        if len(rest) > 1:
            return classify_command(rest[1])
    if first in _EXPLORE_WORDS or _EXPLORE_GIT.search(cmd):
        return "explore"
    return "run"


def classify(step: dict) -> Optional[str]:
    """What one step of either vendor did, or None for thinking and talking."""
    stype, name = step.get("type"), str(step.get("name") or "")
    if stype in ("reason", "answer"):
        return None
    if stype == "plan":
        return "plan"
    if stype in ("search", "retrieve"):
        return "research"
    if stype == "read":
        return "explore"
    if name in _CLAUDE_KIND:
        return _CLAUDE_KIND[name]
    if name == "apply_patch":
        return "edit"
    if name in ("shell", "Bash", "bash", "exec_command", "local_shell"):
        cmd = step.get("input") or ""
        if name == "Bash" and cmd.startswith("{"):
            try:
                import json
                cmd = str(json.loads(cmd).get("command") or "")
            except ValueError:
                pass
        return classify_command(cmd)
    if name in ("spawn_agent", "send_input", "close_agent") or (step.get("span") or {}).get("agent") == "sub-agent":
        return "delegate"
    return "other"


#: a final message that says the work is finished and checked
_CLAIMS_DONE = re.compile(
    r"\b(all\s+(\d+\s+|the\s+)?tests?\s+(now\s+)?pass|tests?\s+(now\s+)?pass(es|ing)?|"
    r"(is|are)\s+(now\s+)?(fixed|passing|green)|fixed\s+(the|it)|done\b|resolved\b|succeed(s|ed)?)", re.I)


def claims_done(message: str) -> bool:
    """Whether a final message says the work is finished — read as a claim,
    to be set beside the check that says whether it is."""
    return bool(_CLAIMS_DONE.search(message or ""))


def run_profile(record: dict) -> dict:
    """One run's execution, read from its trace and what the harness saw."""
    traj = record["trajectory"]
    steps = traj.get("steps") or []
    actions = [(i, classify(s), s) for i, s in enumerate(steps)]
    acts = [(i, k, s) for i, k, s in actions if k]
    counts = {k: 0 for k in ACTIVITIES}
    for _, k, _s in acts:
        counts[k] += 1
    n = len(acts)
    edits = [j for j, (_, k, _s) in enumerate(acts) if k == "edit"]
    verifies = [j for j, (_, k, _s) in enumerate(acts) if k == "verify"]
    first_edit = edits[0] if edits else None
    verified_after = bool(edits and any(v > edits[-1] for v in verifies))
    tool_time = sum(float(s.get("latency_s") or 0) for _, _, s in acts)
    think_time = sum(float(s.get("latency_s") or 0) for s in steps if s.get("type") == "reason")
    errors = sum(1 for _, _, s in acts if s.get("error"))
    marks: dict = {}
    try:
        for m in excerpt.notable_steps(Trajectory.from_dict(traj)):
            marks[m["kind"]] = marks.get(m["kind"], 0) + 1
    except ValueError:
        marks = {}
    totals = traj.get("totals") or {}
    acc = traj.get("token_accounting") or {}
    vendor = traj.get("vendor") or {}
    tok_in, tok_out = int(totals.get("input_tokens") or 0), int(totals.get("output_tokens") or 0)
    diff = record.get("diff") or {}
    files = diff.get("files") or []
    check = record.get("check") or {}
    return {
        "task": record.get("task"), "agent": record.get("agent"), "vendor": vendor.get("name"),
        "model": (traj.get("agent") or {}).get("model") or record.get("model") or "",
        "run": record.get("run"),
        "passed": check.get("passed"), "exit_code": check.get("exit_code"),
        "termination": (traj.get("outcome") or {}).get("termination"),
        "stopped_by": record.get("stopped_by"),
        "tokens": {"input": tok_in, "cached_input": int(acc.get("cached_input_tokens") or 0),
                   "output": tok_out,
                   "reasoning_output": acc.get("reasoning_output_tokens"),
                   "total": tok_in + tok_out, "basis": acc.get("basis")},
        "cost_usd": vendor.get("cost_usd"), "cost_basis": vendor.get("cost_basis"),
        "wall_s": float(totals.get("latency_s") or record.get("wall_s") or 0.0),
        "tool_time_s": round(tool_time, 3), "think_time_s": round(think_time, 3),
        "steps": len(steps), "actions": n,
        "activity": counts,
        "shares": {k: round(v / n, 3) if n else 0.0 for k, v in counts.items()},
        "explored_before_first_edit": (sum(1 for _, k, _s in acts[:first_edit] if k == "explore")
                                       if first_edit is not None else None),
        "first_edit_at": first_edit,
        "verified_after_last_edit": verified_after if edits else None,
        "verify_runs": len(verifies),
        "tool_errors": errors,
        "marks": marks,
        "files_changed": len(files),
        "lines_added": int(diff.get("added") or 0), "lines_removed": int(diff.get("removed") or 0),
        "touched_tests": any(_TEST_PATH.search(str(f.get("path") or "")) for f in files),
        "paths": sorted(str(f.get("path")) for f in files),
        "after": {str(f.get("path")): f.get("after_sha") for f in files},
        "final_message_chars": len(str((traj.get("outcome") or {}).get("answer") or "")),
        # the agent's word against the harness's check: a run that says it
        # is done and fails the check is the case a reader most needs told
        "claimed_done": claims_done(str((traj.get("outcome") or {}).get("answer") or "")),
        "claimed_but_failed": claims_done(str((traj.get("outcome") or {}).get("answer") or ""))
                              and check.get("passed") is False,
        "turns": vendor.get("turns"),
        "permission_denials": vendor.get("permission_denials"),
        "unknown_events": (traj.get("source") or {}).get("unknown_events") or {},
    }


def _median(values: list) -> Optional[float]:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _parity(records: list, agents: list) -> list:
    """Every condition that should have been equal, and whether it was."""
    by_agent = {a: [r for r in records if r.get("agent") == a] for a in agents}

    def show(v):
        return "none" if v is None else f"{v:g}" if isinstance(v, float) else str(v)

    def values(key):
        return {a: sorted({show((r.get("setup") or {}).get(key)) for r in rs}) for a, rs in by_agent.items()}

    rows = []

    def same(key, what, why_matters):
        v = values(key)
        flat = [x for a in agents for x in v[a]]
        equal = len(set(flat)) == 1 if flat else None
        rows.append({"what": what, "key": key, "equal": equal,
                     "values": {a: ", ".join(v[a]) for a in agents}, "why": why_matters})

    same("prompt_sha", "the prompt", "a different instruction is a different task")
    same("workspace_sha", "the starting workspace", "each run starts from its own copy of the same files")
    same("check", "the check that grades it", "success is the check's exit status, run by the harness")
    same("budget_tokens", "the token budget", "an agent allowed more can do more")
    same("timeout_s", "the time limit", "an agent allowed longer can do more")
    same("budget_enforced", "how the budget is enforced",
         "Claude Code reports usage per message, so a budget can stop it mid-run; Codex reports usage "
         "once per turn, so its budget can only be checked afterwards")
    same("sandbox", "the sandbox", "a sandboxed agent may be refused actions the other is allowed")
    same("user_config", "the operator's own vendor settings",
         "a user's custom instructions or hooks change the agent being measured")
    # launched together or one after the other
    skews = []
    pairs: dict = {}
    for r in records:
        pairs.setdefault((r.get("task"), r.get("run")), []).append(r)
    for rs in pairs.values():
        starts = [float((r.get("setup") or {}).get("started_at") or 0) for r in rs]
        if len(starts) >= 2 and all(starts):
            skews.append(max(starts) - min(starts))
    worst = max(skews) if skews else None
    rows.append({"what": "launched side by side", "key": "started_at",
                 "equal": (worst is not None and worst <= 5.0) if skews else None,
                 "values": {"largest gap": f"{worst:.1f}s" if worst is not None else "unrecorded"},
                 "why": "vendor latency and rate limits move over the day; runs launched together share them"})
    costs = {a: sorted({str((r.get("trajectory") or {}).get("vendor", {}).get("cost_basis"))
                        for r in rs}) for a, rs in by_agent.items()}
    flat = [x for a in agents for x in costs[a]]
    rows.append({"what": "cost reporting", "key": "cost_basis", "equal": len(set(flat)) == 1 if flat else None,
                 "values": {a: ", ".join(costs[a]) for a in agents},
                 "why": "a cost one vendor reports and the other does not cannot be compared, only listed"})
    models = {a: ", ".join(sorted({str(p) for p in (
        (r.get("trajectory") or {}).get("agent", {}).get("model") or r.get("model") or
        "the CLI's default (not named in its stream)" for r in rs)}))
        for a, rs in by_agent.items()}
    rows.append({"what": "the models", "key": "model", "equal": None, "values": models,
                 "why": "different vendors run different models by design; named, not judged"})
    return rows


def duel_report(records: Iterable[dict], band: float = BAND) -> dict:
    """The fair report over every run the harness recorded.

    ``records`` are the harness's per-run records: ``{task, agent, run,
    trajectory, check, diff, setup, stopped_by}``.
    """
    records = [r for r in records if isinstance(r, dict) and r.get("trajectory")]
    agents = sorted({str(r.get("agent")) for r in records})
    if len(agents) < 2:
        return {"measurable": False, "reason": f"{plural(len(agents), 'agent')} recorded; a duel needs two",
                "agents": agents, "runs": len(records)}
    profiles = [run_profile(r) for r in records]
    parity = _parity(records, agents)
    unequal = [p for p in parity if p["equal"] is False]
    tasks = sorted({str(p["task"]) for p in profiles})

    per_agent = {}
    for a in agents:
        mine = [p for p in profiles if p["agent"] == a]
        graded = [p for p in mine if p["passed"] is not None]
        passed = sum(1 for p in graded if p["passed"])
        lo, hi = wilson_interval(passed, len(graded)) if graded else (0.0, 1.0)
        tokens = sum(p["tokens"]["total"] for p in mine)
        minutes = sum(p["wall_s"] for p in mine) / 60.0
        costs = [p["cost_usd"] for p in mine if isinstance(p["cost_usd"], (int, float))]
        acts = {k: sum(p["activity"][k] for p in mine) for k in ACTIVITIES}
        n_acts = sum(acts.values())
        edited = [p for p in mine if p["first_edit_at"] is not None]
        per_agent[a] = {
            "runs": len(mine), "graded": len(graded), "passed": passed,
            "pass_rate": round(passed / len(graded), 4) if graded else None,
            "ci95": [round(lo, 4), round(hi, 4)],
            "all_runs_passed_tasks": sum(
                1 for t in tasks if (rs := [p for p in graded if p["task"] == t]) and all(p["passed"] for p in rs)),
            "median": {"tokens": _median([p["tokens"]["total"] for p in mine]),
                       "input": _median([p["tokens"]["input"] for p in mine]),
                       "cached_input": _median([p["tokens"]["cached_input"] for p in mine]),
                       "output": _median([p["tokens"]["output"] for p in mine]),
                       "wall_s": _median([p["wall_s"] for p in mine]),
                       "actions": _median([p["actions"] for p in mine]),
                       "files_changed": _median([p["files_changed"] for p in mine]),
                       "lines_changed": _median([p["lines_added"] + p["lines_removed"] for p in mine])},
            "cost_usd": round(sum(costs), 6) if costs and len(costs) == len(mine) else None,
            "cost_basis": sorted({str(p["cost_basis"]) for p in mine}),
            "passes_per_million_tokens": round(passed / (tokens / 1e6), 3) if tokens and graded else None,
            "passes_per_hour": round(passed / (minutes / 60.0), 3) if minutes and graded else None,
            "activity": acts,
            "shares": {k: round(v / n_acts, 3) if n_acts else 0.0 for k, v in acts.items()},
            "explored_before_first_edit": _median([p["explored_before_first_edit"] for p in edited]),
            "verified_after_last_edit": sum(1 for p in edited if p["verified_after_last_edit"]),
            "edited_runs": len(edited),
            "tool_errors": sum(p["tool_errors"] for p in mine),
            "unrecovered_errors": sum(p["marks"].get("unrecovered_error", 0) for p in mine),
            "redundant_stretches": sum(p["marks"].get("redundant_stretch", 0) for p in mine),
            "touched_tests": sum(1 for p in mine if p["touched_tests"]),
            "claimed_but_failed": sum(1 for p in mine if p["claimed_but_failed"]),
            "stopped_by_budget": sum(1 for p in mine if p["stopped_by"] == "budget"),
            "infrastructure_errors": sum(1 for p in mine if p["termination"] == "infrastructure_error"),
        }

    # the same task and run index, side by side
    pairs = []
    for t in tasks:
        runs = sorted({p["run"] for p in profiles if p["task"] == t}, key=str)
        for run in runs:
            both = {p["agent"]: p for p in profiles if p["task"] == t and p["run"] == run}
            if len(both) < 2:
                continue
            a, b = both[agents[0]], both[agents[1]]
            ta, tb = a["tokens"]["total"], b["tokens"]["total"]
            big = max(ta, tb)
            gap = abs(ta - tb) / big if big else 0.0
            common = sorted(set(a["paths"]) & set(b["paths"]))
            union = sorted(set(a["paths"]) | set(b["paths"]))
            pairs.append({
                "task": t, "run": run,
                "passed": {agents[0]: a["passed"], agents[1]: b["passed"]},
                "tokens": {agents[0]: ta, agents[1]: tb},
                "token_gap": round(gap, 4),
                "budget_matched": gap <= band,
                "wall_s": {agents[0]: a["wall_s"], agents[1]: b["wall_s"]},
                "files": {"both": common, "only": {agents[0]: sorted(set(a["paths"]) - set(b["paths"])),
                                                   agents[1]: sorted(set(b["paths"]) - set(a["paths"]))},
                          "overlap": round(len(common) / len(union), 3) if union else None,
                          "identical": [f for f in common if a["after"].get(f) and a["after"].get(f) == b["after"].get(f)]},
            })

    matched = sum(1 for p in pairs if p["budget_matched"])
    return {
        "measurable": True, "agents": agents, "tasks": tasks, "runs": len(records), "band": band,
        "parity": parity, "fair": not unequal, "unequal": [p["what"] for p in unequal],
        "per_agent": per_agent, "pairs": pairs,
        "budget_matched_pairs": matched,
        "profiles": profiles,
        "narrative": _narrative(agents, per_agent, pairs, matched, band, unequal, tasks),
        "caveat": ("Qualitative, not a benchmark: a few tasks and a few runs describe how these two agents "
                   "worked on this work, with the intervals saying how little a pass rate on a small sample "
                   "settles. Every number is read from the vendors' own streams and the harness's check."),
    }


def _narrative(agents, per_agent, pairs, matched, band, unequal, tasks) -> str:
    a, b = agents
    pa, pb = per_agent[a], per_agent[b]
    parts = []
    parts.append(f"{plural(len(tasks), 'task')}, {plural(len(pairs), 'paired run')}"
                 + (". Not equal between them: " + ", ".join(p["what"] for p in unequal) + "."
                    if unequal else ", on equal terms throughout the parity ledger."))

    def said(name, p):
        if not p["graded"]:
            return f"{name}: no check was run"
        return f"{name} passed {p['passed']} of {p['graded']}"
    parts.append(said(a, pa) + "; " + said(b, pb) + ".")
    if pa["graded"] and pb["graded"] and pa["pass_rate"] != pb["pass_rate"]:
        overlap = pa["ci95"][0] <= pb["ci95"][1] and pb["ci95"][0] <= pa["ci95"][1]
        if overlap:
            parts.append("The pass-rate intervals overlap, so neither is shown to pass more often on this sample.")
    if pairs:
        parts.append(f"{matched} of {plural(len(pairs), 'pair')} spent within ±{band:.0%} of each other's tokens"
                     + ("." if matched == len(pairs) else
                        "; where they did not, compare outcomes per token rather than per run."))
    ppm = [(n, per_agent[n]["passes_per_million_tokens"]) for n in agents
           if per_agent[n]["passes_per_million_tokens"] is not None]
    if len(ppm) == 2:
        parts.append("Passing runs per million tokens: " +
                     ", ".join(f"{n} {v:g}" for n, v in ppm) + ".")
    habits = []
    for n in agents:
        p = per_agent[n]
        if p["edited_runs"]:
            e = p["explored_before_first_edit"]
            habits.append(f"{n} explored {e:g} time(s) before its first edit (median) and ran a check after its "
                          f"last edit in {p['verified_after_last_edit']} of {p['edited_runs']} run(s)")
    if habits:
        parts.append("; ".join(habits) + ".")
    said_done = [f"{n} {per_agent[n]['claimed_but_failed']} time(s)" for n in agents
                 if per_agent[n]["claimed_but_failed"]]
    if said_done:
        parts.append("Said it was done and failed the check: " + ", ".join(said_done) + ".")
    return " ".join(parts)


def render_markdown(report: dict) -> str:
    """DUEL.md: the parity ledger first, then outcome, spend, what each made, how each worked."""
    if not report.get("measurable"):
        return f"# Duel\n\nNot measurable: {report.get('reason')}.\n"
    agents = report["agents"]
    pa = report["per_agent"]
    out = [f"# {' vs '.join(agents)} — same task, side by side", "", report["narrative"], ""]
    out += ["## Parity: what was equal", "", "| condition | equal | " + " | ".join(agents) + " |",
            "|---|---|" + "---|" * len(agents)]
    for row in report["parity"]:
        eq = {True: "yes", False: "**no**", None: "—"}[row["equal"]]
        vals = row["values"]
        cells = [str(vals.get(a, vals.get("largest gap", ""))) for a in agents] if any(a in vals for a in agents) \
            else [str(next(iter(vals.values()), ""))] + [""] * (len(agents) - 1)
        out.append(f"| {row['what']} | {eq} | " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    out += ["", "## Outcome and spend", "", "| | " + " | ".join(agents) + " |", "|---|" + "---|" * len(agents)]

    def row(label, fn):
        out.append(f"| {label} | " + " | ".join(fn(pa[a]) for a in agents) + " |")
    row("passed the check", lambda p: f"{p['passed']} of {p['graded']} "
        f"({p['ci95'][0]:.0%}–{p['ci95'][1]:.0%})" if p["graded"] else "no check")
    row("median tokens (in / cached / out)", lambda p: "{:,.0f} ({:,.0f} / {:,.0f} / {:,.0f})".format(
        p["median"]["tokens"] or 0, p["median"]["input"] or 0, p["median"]["cached_input"] or 0,
        p["median"]["output"] or 0))
    row("cost", lambda p: f"${p['cost_usd']:.4f}" if p["cost_usd"] is not None else "not reported")
    row("median wall time", lambda p: f"{p['median']['wall_s'] or 0:.1f}s")
    row("passing runs per million tokens", lambda p: f"{p['passes_per_million_tokens']:g}"
        if p["passes_per_million_tokens"] is not None else "—")
    row("median actions", lambda p: f"{p['median']['actions'] or 0:g}")
    row("explore / edit / verify / run", lambda p: " / ".join(f"{p['shares'][k]:.0%}" for k in
                                                             ("explore", "edit", "verify", "run")))
    row("explored before first edit (median)", lambda p: f"{p['explored_before_first_edit']:g}"
        if p["explored_before_first_edit"] is not None else "never edited")
    row("checked after its last edit", lambda p: f"{p['verified_after_last_edit']} of {p['edited_runs']}")
    row("errors never come back to", lambda p: str(p["unrecovered_errors"]))
    row("median files / lines changed", lambda p: f"{p['median']['files_changed'] or 0:g} / "
        f"{p['median']['lines_changed'] or 0:g}")
    row("runs that touched tests", lambda p: str(p["touched_tests"]))
    row("said it was done, failed the check", lambda p: str(p["claimed_but_failed"]))
    out += ["", f"## Pairs (budget band ±{report['band']:.0%})", "",
            "| task | run | passed | tokens | matched | files both changed (identical) |", "|---|---|---|---|---|---|"]
    for p in report["pairs"]:
        passed = ", ".join(f"{a} {'✓' if p['passed'][a] else '✗' if p['passed'][a] is False else '—'}" for a in agents)
        toks = ", ".join(f"{a} {p['tokens'][a]:,}" for a in agents)
        files = p["files"]
        out.append(f"| {p['task']} | {p['run']} | {passed} | {toks} | {'yes' if p['budget_matched'] else 'no'} "
                   f"({p['token_gap']:.0%}) | {len(files['both'])} ({len(files['identical'])}) |")
    out += ["", report["caveat"], ""]
    return "\n".join(out)
