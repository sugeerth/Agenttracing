"""What a run means, what it changed, and what to change next.

The hub's pages draw a run; this says what it amounts to, in the order a
reader acts on it:

**The verdict card.** One row each, every row naming where its words came
from:
- VERDICT: how it ended, and by whose check
- WHERE: where to look first (:mod:`agentdiff.timeline`)
- COST: its tokens and time, against the other runs of its task
- CODE: what it changed in the workspace
- FIX: the one change to the agent the run's failure points at
- CONFIDENCE: what the reading rests on

**The code it produced** (:func:`code_change`). The files the agent
changed and how much, from the run record the harness wrote (the diff
is the harness's, taken from the workspace before and after, never the
agent's account). Flags a reader should see before keeping the change:
- the tests were edited
- a file was deleted
- the change is large
- the check passed with no change at all
- the run passed but its change is not kept whole

:func:`code_compare` sets two runs' changes side by side: the files both
touched, and whether they left the same bytes in each.

**The change to the agent** (:func:`agent_fix`). For a run that failed,
the first rule in :data:`agentdiff.selfevolve.REMEDIES` order that fires
on this run names a harness change. It is a hypothesis, labelled as one,
with the command that tests it. ``self-evolve`` keeps it only on the
counts. A run that passed has nothing to fix; its change can be kept.

No number here is estimated: each is a count or a sum over the run's
record and its steps.
"""

from __future__ import annotations

import re
import statistics
from typing import Iterable, List, Optional

from .timeline import outcome_of

__all__ = ["code_change", "code_compare", "agent_fix", "verdict_card", "is_test_path", "LARGE_CHANGE", "check_failures",
           "corpus_insight"]

#: lines changed above which a change is flagged as large
LARGE_CHANGE = 300
_TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__)(/|$)|(^|/)test_[^/]*\.py$|_test\.(py|go|ts|js)$|\.spec\.(ts|js)$"
                        r"|(^|/)conftest\.py$", re.I)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path or ""))


_FAIL_LINE = re.compile(r"^(?:FAIL|ERROR): (?P<ut>.+?)\s*$|^FAILED (?P<py>[^\s(]\S*)(?: - (?P<why>.*))?$|"
                        r"^(?P<file>[\w./-]+\.\w+):(?P<line>\d+): (?P<msg>\w*Error.*)$", re.M)


def check_failures(output: str, most: int = 8) -> List[str]:
    """The cases a check named as failing (unittest's FAIL/ERROR lines, pytest's
    FAILED lines, a file:line error), in the order it printed them."""
    out: List[str] = []
    for m in _FAIL_LINE.finditer(output or ""):
        if m.group("ut"):
            # unittest prints "name (module.Class.name) (subtest)": the name and the subtest say it
            name = re.sub(r"\s*\([\w.]+\.\w+\)", "", m.group("ut"), count=1)
        elif m.group("py"):
            name = m.group("py") + (f" ({m.group('why')[:80]})" if m.group("why") else "")
        else:
            name = f"{m.group('file')}:{m.group('line')} {m.group('msg')[:80]}"
        if name not in out:
            out.append(name)
        if len(out) >= most:
            break
    return out


def code_change(record: Optional[dict], patch: str = "") -> Optional[dict]:
    """The change a run left in its workspace, from the harness's record."""
    if not isinstance(record, dict) or not isinstance(record.get("diff"), dict):
        return None
    diff = record["diff"]
    files = []
    for f in diff.get("files") or []:
        if not isinstance(f, dict):
            continue
        files.append({"path": str(f.get("path")), "status": f.get("status") or "modified",
                      "added": int(f.get("added") or 0), "removed": int(f.get("removed") or 0),
                      "test": is_test_path(str(f.get("path"))), "after_sha": f.get("after_sha")})
    added, removed = int(diff.get("added") or 0), int(diff.get("removed") or 0)
    check = record.get("check") or {}
    passed = check.get("passed")
    kept = record.get("after") or {}
    flags = []
    tests = [f["path"] for f in files if f["test"]]
    if tests:
        flags.append({"kind": "tests_edited", "sentence": f"It changed {len(tests)} test file(s) ({', '.join(tests[:3])}): "
                                                          f"read them before trusting the check that passed."})
    gone = [f["path"] for f in files if f["status"] == "deleted"]
    if gone:
        flags.append({"kind": "deleted", "sentence": f"It deleted {', '.join(gone[:3])}."})
    if added + removed > LARGE_CHANGE:
        flags.append({"kind": "large", "sentence": f"A large change: {added + removed} lines over {len(files)} file(s)."})
    if passed and not files:
        flags.append({"kind": "no_change", "sentence": "The check passed with no change to the workspace: the pass "
                                                       "shows nothing about the agent."})
    if passed and kept and kept.get("complete") is False:
        flags.append({"kind": "not_kept", "sentence": "Its change was not kept whole (a file withheld or too large), "
                                                      "so it cannot be applied as it is."})
    if diff.get("patch_truncated"):
        flags.append({"kind": "truncated", "sentence": "The patch was cut at its size limit: the counts are whole, "
                                                       "the text is not."})
    failures = check_failures(str(check.get("output_tail") or "")) if passed is False else []
    return {"files": files, "added": added, "removed": removed, "tests": tests, "passed": passed, "failures": failures,
            "check": check.get("command"), "check_tail": str(check.get("output_tail") or "")[-1200:],
            "flags": flags, "patch": patch, "baseline_passed": ((record.get("setup") or {}).get("baseline") or {}).get("passed"),
            "keepable": bool(passed and files and kept.get("complete", True) is not False)}


#: the tool calls that write a file, and what a successful one prints
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit", "str_replace_based_edit_tool", "apply_patch")
MOST_PATCH_LINES = 1200


def _rel(path: str, root: Optional[str]) -> str:
    import os
    if root and path.startswith(root.rstrip("/") + "/"):
        return os.path.relpath(path, root)
    return path


def _diff_lines(old: str, new: str) -> tuple:
    """(added, removed, hunk lines) between two texts, by line."""
    import difflib
    a, b = old.splitlines(), new.splitlines()
    added = removed = 0
    hunk = []
    for line in difflib.ndiff(a, b):
        if line.startswith("+ "):
            added += 1
            hunk.append("+" + line[2:])
        elif line.startswith("- "):
            removed += 1
            hunk.append("-" + line[2:])
    return added, removed, hunk


def code_from_steps(traj: dict) -> Optional[dict]:
    """The change a run made, read from its own edit calls (Edit, MultiEdit, Write, NotebookEdit): each
    file with its edits, the lines each added and removed, and the edits as a patch. This is what the
    agent asked its tools to do, not a diff of the workspace: an edit made by a shell command (sed -i,
    a heredoc) names no file the reading can trust, so those are counted apart, and a failed edit counts
    for nothing. None when the run made no edit call."""
    import json as _json
    steps = [s for s in traj.get("steps") or [] if isinstance(s, dict)]
    root = ((traj.get("source") or {}).get("cwd") if isinstance(traj.get("source"), dict) else None)
    files: dict = {}
    order: List[str] = []
    patch: List[str] = []
    failed_edits = 0
    shell_edits = 0
    from .laps import _activity
    for s in steps:
        if s.get("type") != "tool_call":
            continue
        name = str(s.get("name") or "")
        if name not in EDIT_TOOLS:
            if name in ("Bash", "shell", "exec_command") and _activity(s) == "edit" and not s.get("error"):
                shell_edits += 1
            continue
        if s.get("error"):
            failed_edits += 1
            continue
        try:
            args = _json.loads(s.get("input") or "{}")
        except ValueError:
            continue
        if not isinstance(args, dict):
            continue
        path = str(args.get("file_path") or args.get("notebook_path") or args.get("path") or "")
        if not path:
            continue
        rel = _rel(path, root)
        out = str(s.get("output") or "")
        if name == "Write":
            new = str(args.get("content") or "")
            created = "created" in out.lower() or rel not in files
            pairs = [("", new)]
            how = "created" if created and rel not in files else "rewritten"
        elif name == "MultiEdit":
            pairs = [(str(x.get("old_string") or ""), str(x.get("new_string") or ""))
                     for x in args.get("edits") or [] if isinstance(x, dict)]
            how = "edited"
        elif name == "NotebookEdit":
            pairs = [("", str(args.get("new_source") or ""))]
            how = "edited"
        else:
            pairs = [(str(args.get("old_string") or ""), str(args.get("new_string") or ""))]
            how = "edited"
        f = files.get(rel)
        if f is None:
            f = files[rel] = {"path": rel, "status": how, "added": 0, "removed": 0, "test": is_test_path(rel),
                              "edits": 0, "steps": [], "after_sha": None}
            order.append(rel)
        elif how == "rewritten" and f["status"] != "created":
            f["status"] = "rewritten"
        f["edits"] += 1
        f["steps"].append(s.get("index"))
        for old, new in pairs:
            a, r, hunk = _diff_lines(old, new)
            f["added"] += a
            f["removed"] += r
            if len(patch) < MOST_PATCH_LINES:
                patch.append(f"--- {rel}  (step {s.get('index')}, {name})")
                patch.extend(hunk[: max(0, MOST_PATCH_LINES - len(patch))])
    if not files:
        return None
    flist = sorted((files[p] for p in order), key=lambda f: -(f["added"] + f["removed"]))   # the biggest first
    added = sum(f["added"] for f in flist)
    removed = sum(f["removed"] for f in flist)
    # a test it wrote is new work; a test that was there and changed may be the check bent to pass
    tests = [f["path"] for f in flist if f["test"] and f["status"] != "created"]
    flags = []
    if tests:
        flags.append({"kind": "tests_edited", "sentence": f"It changed {len(tests)} existing test file(s) "
                                                          f"({', '.join(tests[:3])}): read them before trusting a check "
                                                          f"that passed."})
    if added + removed > LARGE_CHANGE:
        flags.append({"kind": "large", "sentence": f"A large change: {added + removed} lines over {len(flist)} file(s)."})
    # the last check it ran, and how it ended, from the steps
    from .laps import check_outcome
    last = next((s for s in reversed(steps) if s.get("type") == "tool_call" and _activity(s) == "verify"), None)
    passed = check_outcome(last) if last else None
    command = None
    if last is not None:
        try:
            command = str(_json.loads(last.get("input") or "{}").get("command") or last.get("name"))
        except (ValueError, AttributeError):
            command = str(last.get("name"))
    tail = str((last or {}).get("output") or "")[-1200:]
    return {"files": flist, "added": added, "removed": removed, "tests": tests, "passed": passed,
            "failures": check_failures(tail) if passed is False else [], "check": command, "check_tail": tail,
            "flags": flags, "patch": "\n".join(patch), "baseline_passed": None, "keepable": False,
            "source": "steps", "failed_edits": failed_edits, "shell_edits": shell_edits,
            "check_step": (last or {}).get("index")}


def code_compare(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    if not a or not b:
        return None
    fa = {f["path"]: f for f in a["files"]}
    fb = {f["path"]: f for f in b["files"]}
    both = sorted(set(fa) & set(fb))
    same = [p for p in both if fa[p].get("after_sha") and fa[p].get("after_sha") == fb[p].get("after_sha")]
    only_a, only_b = sorted(set(fa) - set(fb)), sorted(set(fb) - set(fa))
    if not both and not only_a and not only_b:
        sentence = "Neither changed a file."
    elif not only_a and not only_b and len(same) == len(both):
        sentence = f"They left the same bytes in all {len(both)} file(s) they changed."
    else:
        bits = []
        if both:
            bits.append(f"both changed {len(both)} file(s)" + (f", {len(same)} to the same bytes" if same else ", differently"))
        if only_a:
            bits.append(f"only A changed {', '.join(only_a[:3])}")
        if only_b:
            bits.append(f"only B changed {', '.join(only_b[:3])}")
        sentence = "; ".join(bits).capitalize() + "."
    return {"both": both, "same": same, "only_a": only_a, "only_b": only_b, "sentence": sentence,
            "lines": [a["added"] + a["removed"], b["added"] + b["removed"]]}


#: how a run failed -> the remedies tried first, so the fix answers the failure the reader is shown
_PREFER = {"loop": ("repeated_call", "mark:cycle", "mark:redundant_stretch"),
           "unchecked": ("claims_without_check", "no_check_after_last_edit", "mark:shipped_before_check"),
           "end": ("claims_without_check", "no_check_after_last_edit", "mark:unverified_write"),
           "error": ("mark:unrecovered_error", "error_streak"),
           "check": ("mark:unrecovered_error", "repeated_call", "error_streak")}


def agent_fix(traj: dict, *, check: str = "", look_kind: Optional[str] = None) -> Optional[dict]:
    """For a failed run, the first harness change whose rule fires on it: first
    among the remedies for how it failed (``look_kind``), then in the harness's order."""
    from .forge import RunView, evaluate, parse_rule
    from .selfevolve import REMEDIES, remedy_for, remedy_text
    from .trace import Trajectory
    if outcome_of(traj) is True or traj.get("in_progress"):
        return None
    try:
        view = RunView(Trajectory.from_dict(traj), None, set())
    except (ValueError, KeyError, TypeError):
        return None
    tried = []
    order = list(_PREFER.get(look_kind or "", ())) + [k for k in REMEDIES if k not in _PREFER.get(look_kind or "", ())]
    for key in order:
        if key == "tool_called":
            continue          # a tool to deny needs other runs to show it travels with failure
        if key.startswith("mark:"):
            rule_text = key
        elif key in ("repeated_call", "error_streak"):
            rule_text = f"{key}:3" if key == "repeated_call" else f"{key}:2"
        elif key == "tool_absent":
            continue          # likewise: absent compared with what, needs other runs
        else:
            rule_text = key
        try:
            rule = parse_rule(rule_text)
            hit = evaluate(rule, view)
        except (ValueError, KeyError, TypeError):
            continue
        tried.append(rule_text)
        if hit is None:
            continue
        r = remedy_for(rule_text, check=check)
        if not r or r.get("unactionable"):
            continue
        from .forge import describe
        return {"rule": rule_text, "step": hit, "says": describe(rule), "remedy": r, "change": remedy_text(r),
                "why": r["why"], "tried": len(tried),
                "basis": "a hypothesis from this one run: the first rule, in the harness's order, that fires on it"}
    if outcome_of(traj) is None:
        return None             # nothing graded it and no rule fired: there is nothing it is known to have got wrong
    return {"rule": None, "remedy": None, "tried": len(tried),
            "why": "none of the failures the harness can act on shows in this run; what it got wrong is in what the "
                   "code does, not in how the agent worked"}


def _median(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return statistics.median(xs) if xs else None


def _s(seconds: float) -> str:
    """Seconds as a reader says them: 12.3s, or 14h 06m for a long run."""
    if seconds >= 3600:
        from .longrun import dur
        return dur(seconds)
    return f"{seconds:.1f}s"


def verdict_card(traj: dict, tl: dict, *, peers: Iterable[dict] = (), change: Optional[dict] = None,
                 fix: Optional[dict] = None) -> List[dict]:
    """The rows of the card, each ``{label, html_safe_text, source, tone}``."""
    rows = []
    task = (traj.get("task") or {}).get("id")
    agent = (traj.get("agent") or {}).get("name")
    graded = ((traj.get("harness") or {}).get("graded_by")) or "its own outcome"
    ok = None if traj.get("in_progress") else outcome_of(traj)
    if traj.get("in_progress"):
        rows.append({"label": "verdict", "text": f"{agent} is still running {task}: {len(tl.get('laps') or [])} lap(s) "
                                                 f"so far, drawn as far as it has gone and judged when it ends.",
                     "source": "the live frame", "tone": "run"})
    elif ok is None and (traj.get("outcome") or {}).get("success") is not None:
        rows.append({"label": "verdict", "text": f"{agent} finished {task}, and nothing graded it: no check ran and no "
                                                 f"expected answer was given, so it is neither a pass nor a failure.",
                     "source": "outcome.note (ungraded)", "tone": ""})
    else:
        rows.append({"label": "verdict", "text": f"{agent} {'solved' if ok else 'failed' if ok is False else 'finished'} "
                                                 f"{task}.", "source": f"outcome.success, graded by {graded}",
                     "tone": "ok" if ok else "bad" if ok is False else ""})
    here = tl.get("look_here") or {}
    if here:
        rows.append({"label": "cause" if here.get("kind") not in ("pass", "now") else "where", "text": here["sentence"],
                     "source": f"timeline.look_here ({here.get('kind')})",
                     "step": here.get("index"), "tone": "bad" if here.get("kind") not in ("pass", "now") else ""})
    peers = list(peers)
    tok = sum(int(s.get("tokens") or 0) for s in traj.get("steps") or [])
    secs = tl.get("span_s")
    pt, ps = _median([p.get("tokens") for p in peers]), _median([p.get("seconds") for p in peers])
    graded_peers = [p for p in peers if p.get("success") is not None]
    cost = f"{tok:,} tokens over {_s(secs)}" if isinstance(secs, (int, float)) else f"{tok:,} tokens"
    if pt:
        cost += f"; the other {len(peers)} run(s) of this task: median {pt:,.0f} tokens"
        if ps:
            cost += f", {_s(ps)}"
        if graded_peers:
            cost += f", {sum(1 for p in graded_peers if p['success'])} of {len(graded_peers)} passed"
    rows.append({"label": "cost", "text": cost + ".", "source": "steps[].tokens, the clock; peers: the trace index", "tone": ""})
    if change:
        text = (f"{len(change['files'])} file(s), +{change['added']} −{change['removed']}"
                + (f" ({', '.join(f['path'] for f in change['files'][:3])})" if change["files"] else ""))
        if change.get("failures"):
            text += (f". The check failed on {len(change['failures'])} case(s) it named: "
                     f"{', '.join(change['failures'][:3])}" + ("…" if len(change["failures"]) > 3 else ""))
        if change["flags"]:
            text += ". " + " ".join(f["sentence"] for f in change["flags"][:2])
        rows.append({"label": "code", "text": text, "source": ("the agent's own edit calls (Edit, Write, MultiEdit), not a "
                                                                 "diff of the workspace" if change.get("source") == "steps"
                                                                 else "the harness's diff of the workspace (record.diff)")
                                                                 + (", the check's output (record.check)" if change.get("failures") else ""),
                     "tone": "bad" if change["flags"] or change.get("failures") else ""})
    if fix:
        if fix.get("remedy"):
            rows.append({"label": "fix", "text": f"Change the agent: {fix['change']} (because {fix['says']}, at step "
                                                 f"{fix['step']}).", "source": f"selfevolve.REMEDIES[{fix['rule']}]; "
                                                                                f"{fix['basis']}",
                         "step": fix.get("step"), "tone": "fix"})
        else:
            rows.append({"label": "fix", "text": f"No harness change: {fix['why']}.",
                         "source": f"{fix['tried']} rule(s) tried", "tone": ""})
    elif ok and change and change.get("keepable"):
        rows.append({"label": "keep", "text": "Its change passed and is kept whole: it can be applied.",
                     "source": "record.check, record.after", "tone": "ok"})
    basis = tl.get("basis")
    n = len(peers) + 1
    conf = (f"{'one run' if n == 1 else f'{n} runs of this task'}; {basis} clock"
            + ("; the cause is a reading of the steps, not a replay" if ok is False else ""))
    rows.append({"label": "confidence", "text": conf + ".", "source": "the trace index; timeline.basis", "tone": ""})
    return rows


def corpus_insight(items: Iterable[dict]) -> Optional[dict]:
    """Across many runs: what the failures have in common, and the change most of them point at.
    ``items``: ``{"look_kind", "fix_rule", "fix_change", "success", "agent", "lines", "tests_edited"}``."""
    items = list(items)
    failed = [x for x in items if x.get("success") is False]
    if not items:
        return None
    kinds: dict = {}
    fixes: dict = {}
    for x in failed:
        if x.get("look_kind"):
            kinds[x["look_kind"]] = kinds.get(x["look_kind"], 0) + 1
        if x.get("fix_rule"):
            fixes.setdefault(x["fix_rule"], [0, x.get("fix_change")])[0] += 1
    top_fix = max(fixes.items(), key=lambda kv: kv[1][0]) if fixes else None
    by_agent: dict = {}
    for x in items:
        a = by_agent.setdefault(x.get("agent") or "agent", {"runs": 0, "passed": 0, "graded": 0, "lines": [],
                                                           "tests_edited": 0})
        a["runs"] += 1
        a["graded"] += 1 if x.get("success") is not None else 0
        a["passed"] += 1 if x.get("success") else 0
        if isinstance(x.get("lines"), int):
            a["lines"].append(x["lines"])
        a["tests_edited"] += 1 if x.get("tests_edited") else 0
    agents = [{"agent": k, "runs": v["runs"], "passed": v["passed"], "graded": v["graded"],
               "median_lines": _median(v["lines"]),
               "tests_edited": v["tests_edited"]} for k, v in sorted(by_agent.items())]
    return {"runs": len(items), "failed": len(failed), "kinds": kinds,
            "top_fix": {"rule": top_fix[0], "runs": top_fix[1][0], "change": top_fix[1][1]} if top_fix else None,
            "agents": agents}


#: the live guard that enforces each remedy while the agent works (``agentdiff guard``)
_GUARD_FOR = {"repeated_call": "repeat", "error_streak": "repeat", "mark:cycle": "repeat",
              "mark:redundant_stretch": "repeat", "claims_without_check": "check",
              "no_check_after_last_edit": "check", "mark:shipped_before_check": "check",
              "mark:unverified_write": "check"}


def _check_command(traj: dict, change: Optional[dict]) -> str:
    """The check this run's task uses: the harness's, else the one the run ran most."""
    if change and change.get("check"):
        return str(change["check"])
    from collections import Counter
    from .laps import is_check
    from .longrun import check_part, _command
    seen = Counter(check_part(_command(s)) for s in traj.get("steps") or [] if isinstance(s, dict) and is_check(s))
    return seen.most_common(1)[0][0] if seen else ""


def mitigation(traj: dict, tl: dict, *, lap: Optional[dict] = None, phases: Optional[dict] = None,
               change: Optional[dict] = None, fix: Optional[dict] = None, act: Optional[dict] = None) -> dict:
    """Why the run went the way it did, and the steps that change the agent.

    ``reason`` is sentences, each with where it came from. ``steps`` are what
    to do, in order: the instruction to give the agent, the guard that
    enforces it while it works, a cap when it went round, the command that
    tests the change on the counts (self-evolve keeps it only if it wins),
    and keeping the code when the run passed. Every step is a hypothesis
    until the test says otherwise, and says so."""
    out_reason: List[dict] = []
    steps: List[dict] = []
    success = outcome_of(traj)
    ungraded = success is None and not traj.get("in_progress")
    agent = str((traj.get("agent") or {}).get("name") or "agent")
    here = (tl or {}).get("look_here") or {}
    if here.get("sentence"):
        out_reason.append({"text": here["sentence"], "step": here.get("index"), "source": "timeline.look_here"})
    if lap and (lap.get("stuck") or {}).get("longest_repeated_block", {}).get("repeats", 0) >= 3:
        blk = lap["stuck"]["longest_repeated_block"]
        out_reason.append({"text": f"A block of {blk['period']} step(s) went round {blk['repeats']} times in a row.",
                           "step": blk.get("starts_at"), "source": "laps.stuck (process.loops)"})
    if phases:
        for lp in (phases.get("loops") or [])[:1]:
            from .longrun import dur
            out_reason.append({"text": f"Bursts {lp['bursts'][0]}–{lp['bursts'][1]} did the same calls "
                                       f"{lp['count']} times over {dur(lp['wall_s'])}"
                                       + (f", with {lp['failing']} failing." if lp["failing"] else "."),
                               "step": lp.get("first_index"), "source": "longrun.loops"})
        st = phases.get("stall")
        if st and st.get("active_s", 0) >= 600 and not phases.get("loops"):
            out_reason.append({"text": st["sentence"], "step": st.get("from_index"), "source": "longrun.stall"})
    if change and change.get("failures"):
        out_reason.append({"text": "The check failed on: " + "; ".join(change["failures"][:4]) + ".", "step": None,
                           "source": "the harness's check output"})
    for f in (change or {}).get("flags") or []:
        out_reason.append({"text": f["sentence"], "step": None, "source": "insight.code_change"})
    if fix and fix.get("why"):
        out_reason.append({"text": ("Why this change: " if fix.get("rule") else "") + fix["why"][0].upper() + fix["why"][1:]
                                   + ("" if fix["why"].endswith(".") else "."),
                           "step": fix.get("step") if isinstance(fix.get("step"), int) else None,
                           "source": f"insight.agent_fix ({fix['rule']})" if fix.get("rule") else "insight.agent_fix"})
    check = _check_command(traj, change)
    rule = (fix or {}).get("rule")
    remedy = (fix or {}).get("remedy") or {}
    loops = (phases or {}).get("loops") or []
    long_loop = max(loops, key=lambda x: x["active_s"]) if loops else None
    if long_loop and long_loop["wall_s"] < 1800:
        long_loop = None
    if success is True and long_loop and not rule:
        # it passed, but went round for hours first: the same remedy as a run that never got out
        from .selfevolve import remedy_for, remedy_text
        r = remedy_for("repeated_call:3", check=check)
        if r and not r.get("unactionable"):
            rule, remedy = "repeated_call:3", r
            fix = {"rule": rule, "remedy": r, "change": remedy_text(r), "says": "the same failing call, again and again"}
    key = (rule or "").split(":")[0] if rule and not rule.startswith("mark:") else rule
    if (success is not None or ungraded) and rule:
        if remedy.get("knob") == "instruction":
            text = remedy.get("text") or fix.get("change") or ""
            how = "what this run did" if ungraded else "how this run failed"
            steps.append({"title": "Tell the agent", "text": f"Add this to {agent}'s instructions: it answers {how} "
                          f"({fix.get('says') or rule}).",
                          "command": f"claude --append-system-prompt {_q(text)}", "quote": text,
                          "source": f"selfevolve.REMEDIES[{key}]"})
        elif remedy.get("knob") == "deny_tool":
            steps.append({"title": "Take the tool away", "text": f"Deny {remedy.get('text')} to {agent}: the runs "
                          f"that called it failed.", "command": f"claude --disallowedTools {_q(remedy.get('text'))}",
                          "source": "selfevolve.REMEDIES[tool_called]"})
        elif remedy.get("knob") == "max_turns":
            steps.append({"title": "Cap the turns", "text": remedy.get("why") or "", "command":
                          f"claude --max-turns {remedy.get('value') or 30}", "source": "selfevolve.REMEDIES"})
        guard = _GUARD_FOR.get(key)
        if guard:
            flag = f' --check {_q(check)}' if guard == "check" and check else ""
            steps.append({"title": "Enforce it while it works", "text": (
                "The repeat guard refuses a failing command run again unchanged, quoting its failure."
                if guard == "repeat" else "The check guard sends a finish with unchecked edits back to run the "
                "check, at most twice."), "command": f"agentdiff guard --install{flag}",
                "source": f"agentdiff.guard ({guard})"})
    if success is False and (change or {}).get("tests"):
        steps.append({"title": "Protect the tests", "text": "It edited the tests: refuse test edits, so it fixes "
                      "the code the tests catch.", "command": "agentdiff guard --install --protect-tests",
                      "source": "agentdiff.guard (tests)"})
    if long_loop:
        lp = long_loop
        from .longrun import dur
        steps.append({"title": "Stop a loop of hours early", "text": f"It went round {lp['count']} bursts doing the "
                      f"same calls for {dur(lp['wall_s'])}. A turn cap ends a run that stops moving, so it is seen in "
                      f"minutes, not overnight. Pick the cap from a run that passed: a few times its turns.",
                      "command": "claude --max-turns 200",
                      "source": "longrun.loops; selfevolve (max_turns)"})
    if success is False or (success is True and long_loop):
        if act and act.get("command") and "self-evolve" in act["command"]:
            cmd = act["command"]
        else:
            cmd = f"agentdiff self-evolve --task tasks.json --agent {agent} --generations 1"
        steps.append({"title": "Test it on the counts", "text": "Run the task with and without the change, the same "
                      "number of times; it is kept only if it wins more tasks than it loses and breaks none. Until "
                      "then it is a hypothesis from this one run.", "command": cmd,
                      "source": "selfevolve.decide"})
        if not rule and success is False:
            steps.insert(0, {"title": "Look at the code, not the loop", "text": (fix or {}).get("why") or
                             "No harness change answers this failure: what it got wrong is in what the code does.",
                             "command": "", "source": "insight.agent_fix"})
    if success is True and not long_loop:
        if act and act.get("command"):
            steps.append({"title": act.get("title") or "Keep this change", "text": act.get("why") or "",
                          "command": act["command"], "source": "the harness's record"})
        else:
            steps.append({"title": "Nothing to change in the agent", "text": "It passed. To compare it with another "
                          "agent on the same task, run them side by side.", "command":
                          "agentdiff duel --task task.json -o duel/", "source": "outcome.success"})
    if ungraded:
        steps.insert(0, {"title": "Give it a check", "text": "Nothing graded this run, so it is neither a pass nor a "
                         "failure. Name the command that says the work is done; the guard runs it before the agent "
                         "may stop, and every later run is graded by it.",
                         "command": f"agentdiff guard --install --check {_q(check or 'pytest -q')}",
                         "source": "outcome.note (ungraded)"})
    elif success is None:
        steps.append({"title": "Wait for the end", "text": "It is still running; the reason and the change come "
                      "when it ends.", "command": "", "source": "in_progress"})
    return {"reason": out_reason, "steps": steps, "check": check,
            "basis": "each step is a hypothesis from this run until self-evolve's paired test keeps or reverts it"}


def _q(text) -> str:
    import shlex
    return shlex.quote(str(text or ""))
