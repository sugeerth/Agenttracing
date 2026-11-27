"""When a run's code first became correct, and what the run did after (``agentdiff lag RUNS/``).

A coding agent can reach a correct fix early and keep going: more reading, more checks, more edits, and now and
then an edit that undoes the fix. From the trace alone that cannot be seen, because the trace does not grade the
code between edits. This rebuilds it. It starts from the task's own workspace, applies each recorded edit in
order (``Write`` writes a file, ``Edit`` and ``MultiEdit`` replace text exactly as the tool did), and grades the
workspace after every edit with the task's held-out check. For each run it gives:

- ``first_correct``: the step after which the held-out check first passed (None if it never did);
- ``lag``: the steps from there to the end of the run, and that share of the run; the tokens spent after it;
- ``first_edit_correct``: whether the very first edit to the project already passed;
- ``undone``: the check passed after some edit, and failed at the end. The run had the fix and lost it.

A rebuild is trusted only when it ends where the run ended. The run's record keeps the SHA-256 of every file the
agent changed, as it stood at the end; a rebuild whose final files do not hash to those values is set aside,
with the file that differed. A shell command that wrote a file (``sed -i``, a heredoc) is not replayed, so a run
that edited that way is set aside by the same test. Nothing here runs an agent or judges anything by a model:
each grade is the task's own check, run in a copy of the workspace. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

__all__ = ["KINDS", "count_tests", "rebuild", "apply_edit", "termination_lag", "lag_over", "lag_says", "find_workspace", "resolve_check"]

#: the run directory the harness gives an agent: /tmp/agentdiff-<agent>-<id>/work
_WORK = re.compile(r"^(.*?/agentdiff-[^/]+/work)/(.+)$")
_HIDDEN = re.compile(r"(\S*/)hidden/([\w.-]+)")
_PY = re.compile(r"^\S*python3?(\.\d+)?(?=\s)")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_edit(files: Dict[str, str], rel: str, tool: str, args: dict) -> Optional[str]:
    """Apply one recorded edit to ``files`` (relative path -> text), as the tool did. Returns None when it
    applied, else why it could not (the text it replaces is not there, or not once)."""
    if tool == "Write":
        files[rel] = str(args.get("content") or "")
        return None
    edits = args.get("edits") if tool == "MultiEdit" else [args]
    text = files.get(rel)
    if text is None:
        return f"{rel} does not exist to edit"
    for e in edits or []:
        old, new = str(e.get("old_string") or ""), str(e.get("new_string") or "")
        n = text.count(old) if old else 0
        if n == 0:
            return f"the text an edit replaces is not in {rel}"
        if n > 1 and not e.get("replace_all"):
            return f"the text an edit replaces occurs {n} times in {rel}"
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    files[rel] = text
    return None


def _read_tree(root: Path) -> Dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            try:
                out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
    return out


def rebuild(traj, workspace: Path) -> dict:
    """The workspace after each edit the run made to it: ``{"states": [{"step", "index", "files"}], "edits",
    "outside", "broken"}``. ``index`` counts the run's steps; an edit outside the run's workspace (a scratch test
    of its own) is counted in ``outside`` and not applied; ``broken`` says why an edit could not be applied."""
    from .gittasks import _tool_input
    files = _read_tree(Path(workspace))
    states, outside, edits = [], 0, 0
    for s in traj.steps:
        if s.type != "tool_call" or s.name not in ("Write", "Edit", "MultiEdit") or s.error is True:
            continue
        args = _tool_input(s.input)
        m = _WORK.match(str(args.get("file_path") or ""))
        if not m:
            outside += 1
            continue
        edits += 1
        why = apply_edit(files, m.group(2), s.name, args)
        if why:
            return {"states": states, "edits": edits, "outside": outside, "broken": why}
        states.append({"step": s.index, "files": dict(files)})
    return {"states": states, "edits": edits, "outside": outside, "broken": None}


def resolve_check(command: str, root: Path) -> str:
    """The recorded check, pointed at this repository's copy of its held-out tests and at this Python."""
    def here(m):
        task = m.group(2)
        for base in (root / "demo/selfevolve/hard/hidden", root / "demo/selfevolve/hidden"):
            if (base / task).is_dir():
                return str(base / task)
        return m.group(0)
    command = _HIDDEN.sub(here, command)
    return _PY.sub(sys.executable, command.strip())


def find_workspace(task: str, root: Path) -> List[Path]:
    """The folders that may be the task's starting workspace, in the order tried."""
    cands = [root / "demo/vendors/suite" / task, root / "demo/selfevolve/hard/tasks" / task,
             root / "demo/selfevolve/tasks" / task, root / "demo/vendors" / task.split("-")[0]]
    return [c for c in cands if c.is_dir()]


_RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
_BAD = re.compile(r"(failures|errors)=(\d+)")
_PYTEST = re.compile(r"(\d+) (passed|failed|errors?)\b")


def count_tests(output: str) -> Optional[tuple]:
    """``(passed, total)`` from a test run's own summary, the last one it printed: unittest's ``Ran N tests``
    and its ``FAILED (failures=a, errors=b)``, or pytest's tally. None when it printed none (it did not get
    as far as running tests)."""
    runs = list(_RAN.finditer(output))
    if runs:
        last = runs[-1]
        total = int(last.group(1))
        tail = output[last.end():]
        bad = sum(int(n) for _, n in _BAD.findall(tail.split("\nRan ")[0])) if "FAILED" in tail else 0
        return max(0, total - bad), total
    tally = {}
    for n, kind in _PYTEST.findall(output):
        tally[kind.rstrip("s")] = int(n)
    if tally:
        total = sum(tally.values())
        return tally.get("passed", 0), total
    return None


def _grade(files: Dict[str, str], check: str, timeout: float = 60) -> tuple:
    """``(passed the check, tests passed, tests run)``; the counts are None when the run printed none."""
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in files.items():
            p = Path(tmp) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            r = subprocess.run(check, shell=True, cwd=tmp, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return False, None, None
        counts = count_tests((r.stdout + b"\n" + r.stderr).decode("utf-8", "replace"))
        return r.returncode == 0, *(counts or (None, None))


#: what a step after the fix was: a check (a test run, by its command or by what it printed), an edit, a read, any
#: other tool call, or text (the agent's reasoning and its answer)
KINDS = ("check", "edit", "read", "other tool", "text")


def _kind(s) -> str:
    """One step's kind, from :data:`KINDS`."""
    from .gittasks import _tool_input
    from .guard import EDIT_TOOLS, is_check_command, runner_verdict
    if s.type != "tool_call":
        return "text"
    if s.name in EDIT_TOOLS:
        return "edit"
    if s.name == "Bash" and (is_check_command(str(_tool_input(s.input).get("command") or ""))
                             or runner_verdict(str(s.output or "")) is not None):
        return "check"
    if s.name in ("Read", "Grep", "Glob", "LS"):
        return "read"
    return "other tool"


def _kinds(steps) -> Dict[str, int]:
    out = dict.fromkeys(KINDS, 0)
    for s in steps:
        out[_kind(s)] += 1
    return out


def termination_lag(traj, record: dict, root: Path) -> dict:
    """One run, rebuilt and graded edit by edit (see the module's text). ``record`` is the harness's record of
    the run: its check command, its graded outcome, and the hash of each file it changed at the end."""
    want = {f["path"]: f.get("after_sha") for f in (record.get("diff") or {}).get("files") or []
            if f.get("status") != "deleted"}
    check = resolve_check(str((record.get("check") or {}).get("command") or ""), root)
    out = {"trace": traj.trace_id, "task": traj.task.id, "success": traj.outcome.success, "steps": len(traj.steps)}
    tried = []
    for ws in find_workspace(traj.task.id, root):
        rb = rebuild(traj, ws)
        if rb["broken"]:
            tried.append(f"{ws.name}: {rb['broken']}")
            continue
        final = rb["states"][-1]["files"] if rb["states"] else _read_tree(ws)
        bad = [p for p, sha in want.items() if sha and _sha(final.get(p, "")) != sha]
        if bad:
            tried.append(f"{ws.name}: {bad[0]} ends differently from the run's own")
            continue
        break
    else:
        return dict(out, rebuilt=False, why="; ".join(tried) or "no workspace for this task here")
    if not rb["states"]:
        return dict(out, rebuilt=True, edits=0, why="no edit to the project")
    graded, cache, tests = [], {}, []
    base = _grade(_read_tree(ws), check)
    for st in rb["states"]:
        key = _sha(json.dumps(st["files"], sort_keys=True))
        if key not in cache:
            cache[key] = _grade(st["files"], check)
        ok, n_pass, n_run = cache[key]
        graded.append((st["step"], ok))
        tests.append([st["step"], n_pass, n_run])
    # tests the task has: the most any state of this run ran (a state that cannot import runs fewer)
    total = max([t[2] for t in tests if t[2]] + [base[2] or 0]) or None
    progress = [[t[0], round(t[1] / total, 4) if total and t[1] is not None else 0.0] for t in tests]
    broke = sum(1 for a, b in zip(tests, tests[1:]) if a[1] is not None and b[1] is not None and b[1] < a[1])
    final_ok = graded[-1][1]
    if final_ok != bool((record.get("check") or {}).get("passed")):
        return dict(out, rebuilt=False, why="the rebuilt final code grades differently from the run's own grade")
    first = next((step for step, ok in graded if ok), None)
    tokens_after = sum(s.tokens or 0 for s in traj.steps if first is not None and s.index > first)
    tokens_all = sum(s.tokens or 0 for s in traj.steps)
    lag = (len(traj.steps) - 1 - first) if first is not None else None
    after = _kinds([st for st in traj.steps if first is not None and st.index > first])
    return dict(out, rebuilt=True, edits=len(graded), graded=[ok for _, ok in graded], first_correct=first, after=after,
                tests=tests, tests_total=total, start=round(base[1] / total, 4) if total and base[1] else 0.0,
                progress=progress, broke=broke, path=[_kind(st) for st in traj.steps],
                first_edit_correct=graded[0][1], final_correct=final_ok,
                undone=first is not None and not final_ok, lag=lag,
                lag_share=round(lag / len(traj.steps), 4) if lag is not None else None,
                tokens_after=tokens_after if first is not None else None,
                tokens_after_share=round(tokens_after / tokens_all, 4) if first is not None and tokens_all else None,
                edits_after=sum(1 for step, _ in graded if first is not None and step > first))


def lag_over(paths: Sequence, root: Optional[Path] = None, exclude: Sequence[str] = ("stand-in",)) -> dict:
    """:func:`termination_lag` for every run under ``paths`` that has the harness's record beside its trace
    (``records/<name>.json`` next to ``traces/<name>.json``), and what they add up to. Folders whose path
    names one of ``exclude`` (the scripted stand-in agent) are left out and counted."""
    from .trace import Trajectory
    root = Path(root or Path(__file__).resolve().parents[1])
    runs, no_record, excluded = [], 0, 0
    for given in paths:
        for tp in sorted(Path(given).rglob("traces/*.json")) if Path(given).is_dir() else [Path(given)]:
            if any(x in str(tp) for x in exclude):
                excluded += 1
                continue
            rp = tp.parent.parent / "records" / tp.name
            try:
                traj = Trajectory.from_json(tp)
            except Exception:  # noqa: BLE001 — not a trace
                continue
            if not rp.is_file():
                no_record += 1
                continue
            runs.append(termination_lag(traj, json.loads(rp.read_text(encoding="utf-8")), root))
    ok = [r for r in runs if r.get("rebuilt") and r.get("edits")]
    solved = [r for r in ok if r["first_correct"] is not None]
    passed = [r for r in solved if r["final_correct"]]
    lags = [r["lag"] for r in passed]
    shares = [r["lag_share"] for r in passed]
    tok = [r["tokens_after_share"] for r in passed if r["tokens_after_share"] is not None]
    out = {"runs": len(runs), "no_record": no_record, "excluded": excluded,
           "rebuilt": sum(1 for r in runs if r.get("rebuilt")), "graded": len(ok),
           "set_aside": [{"trace": r["trace"], "why": r.get("why")} for r in runs if not r.get("rebuilt")],
           "ever_correct": len(solved), "passed": len(passed),
           "first_edit_correct": sum(1 for r in passed if r["first_edit_correct"]),
           "undone": sum(1 for r in ok if r["undone"]), "undone_runs": [r["trace"] for r in ok if r["undone"]],
           "median_lag": statistics.median(lags) if lags else None,
           "median_lag_share": statistics.median(shares) if shares else None,
           "median_tokens_after_share": statistics.median(tok) if tok else None,
           "edits_after_correct": sum(r["edits_after"] for r in passed),
           "after_correct": {k: sum(r["after"][k] for r in passed) for k in KINDS},
           "per_run": runs}
    out["says"] = lag_says(out)
    return out


def lag_says(r: dict) -> List[str]:
    """The result in a few lines, each count with its denominator."""
    lines = [f"{r['graded']} run(s) rebuilt edit by edit and graded after each edit ({r['runs'] - r['rebuilt']} "
             f"set aside: the rebuild did not end where the run did; {r['no_record']} without a record"
             + (f"; {r['excluded']} from the scripted stand-in, left out" if r.get("excluded") else "") + ")"]
    if not r["passed"]:
        return lines + ["no rebuilt run passed, so there is no lag to measure"]
    lines.append(f"{r['first_edit_correct']} of {r['passed']} passing runs were already correct after their first "
                 f"edit to the project")
    lines.append(f"after the code first passed, passing runs went on for a median {r['median_lag']:g} step(s), "
                 f"{r['median_lag_share']:.0%} of the run" + (f", and {r['median_tokens_after_share']:.0%} of its "
                                                            f"tokens" if r["median_tokens_after_share"] is not None
                                                            else ""))
    a = r["after_correct"]
    total = sum(a.values())
    if total:
        lines.append(f"those {total} step(s) after the fix: " + ", ".join(f"{a[k]} {k}" for k in KINDS if a[k]))
    lines.append(f"{r['undone']} of {r['graded']} run(s) had a passing fix and ended failing"
                 + (f": {', '.join(r['undone_runs'][:3])}" if r["undone"] else ""))
    return lines
