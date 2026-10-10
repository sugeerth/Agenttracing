"""The ``guard`` command: live guards for Claude Code, and the trace of every session.

    agentdiff guard --install                      in a project: guard every Claude Code session here
    agentdiff guard --install --check "pytest -q"  name the check the agent must run before it finishes
    agentdiff guard --install --protect-tests      also refuse edits to test files
    agentdiff guard --status                       what the guards refused, by guard
    agentdiff guard --uninstall                    take them out again

Installed, Claude Code runs ``agentdiff guard`` on every tool call and at
every stop (hooks in ``.claude/settings.local.json``, which git ignores).
It refuses a failing command run again with nothing changed, and keeps
the agent working when it changed files after its last check. With
``--traces`` (the default when installing) every session is also traced
into ``.agentdiff/traces``, so ``agentdiff hub`` shows it live: its loop,
its code, where to look. See :mod:`agentdiff.guard`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

__all__ = ["register", "run", "MARK"]

MARK = "agentdiff guard"
SETTINGS = Path(".claude") / "settings.local.json"


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "guard", help="live guards for Claude Code (hooks): refuse a failing command rerun unchanged, keep the agent "
                      "working when it changed files after its last check; and trace every session for the hub")
    p.add_argument("--install", action="store_true", help="add the hooks to .claude/settings.local.json here")
    p.add_argument("--uninstall", action="store_true", help="remove them")
    p.add_argument("--status", action="store_true", help="what the guards refused, by guard")
    p.add_argument("--check", default="", metavar="CMD", help="the check the agent must run before it finishes")
    p.add_argument("--protect-tests", action="store_true", help="also refuse edits to test files")
    p.add_argument("--no-repeat", action="store_true", help="turn the repeat guard off")
    p.add_argument("--no-check-guard", action="store_true", help="turn the check-before-finishing guard off")
    p.add_argument("--max-stop-blocks", type=int, default=2, help="times a stop may be refused per session (default 2)")
    p.add_argument("--traces", default=None, metavar="DIR",
                   help="also trace each session into DIR (installed default: .agentdiff/traces)")
    p.add_argument("--no-traces", action="store_true", help="--install: guard only, no tracing")
    p.add_argument("--project", default=".", metavar="DIR", help="the project (default here)")
    p.set_defaults(func=run)


def _enabled(args) -> tuple:
    on = []
    if not args.no_repeat:
        on.append("repeat")
    if not args.no_check_guard:
        on.append("check")
    if args.protect_tests:
        on.append("tests")
    return tuple(on)


def _command(args, traces: str) -> str:
    pkg = Path(__file__).resolve().parents[2]
    frozen = bool(getattr(sys, "frozen", False))  # a standalone binary is the command itself
    parts = [shlex.quote(sys.executable)] + ([] if frozen else ["-m", "agentdiff"]) + ["guard"]
    if args.check:
        parts += ["--check", shlex.quote(args.check)]
    if args.protect_tests:
        parts.append("--protect-tests")
    if args.no_repeat:
        parts.append("--no-repeat")
    if args.no_check_guard:
        parts.append("--no-check-guard")
    if args.max_stop_blocks != 2:
        parts += ["--max-stop-blocks", str(args.max_stop_blocks)]
    if traces:
        parts += ["--traces", shlex.quote(traces)]
    if frozen:
        return " ".join(parts)
    # run from wherever this agentdiff is, installed or a clone
    return f"PYTHONPATH={shlex.quote(str(pkg))}${{PYTHONPATH:+:$PYTHONPATH}} " + " ".join(parts)


def _ours(entry: dict) -> bool:
    return any(MARK in str(h.get("command", "")) for h in entry.get("hooks") or [])


def install(project: Path, command: str, traced: bool = True) -> Path:
    path = project / SETTINGS
    data = {}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    hooks = data.setdefault("hooks", {})
    hook = {"type": "command", "command": command, "timeout": 30}
    wanted = {"PreToolUse": {"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [hook]},
              "PostToolUse": {"matcher": "*", "hooks": [hook]}, "PostToolUseFailure": {"matcher": "*", "hooks": [hook]},
              "Stop": {"hooks": [hook]}}
    if traced:
        wanted["UserPromptSubmit"] = {"hooks": [hook]}  # the trace's prompt
    for event, entry in wanted.items():
        kept = [e for e in hooks.get(event) or [] if not _ours(e)]
        hooks[event] = kept + [entry]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall(project: Path) -> bool:
    path = project / SETTINGS
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    hooks = data.get("hooks") or {}
    changed = False
    for event in list(hooks):
        kept = [e for e in hooks[event] or [] if not _ours(e)]
        changed |= len(kept) != len(hooks[event] or [])
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changed


def run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if args.install:
        traces = None if args.no_traces else (args.traces or str(project / ".agentdiff" / "traces"))
        try:
            path = install(project, _command(args, traces), traced=bool(traces))
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"guarding Claude Code in {project}: hooks in {path}")
        print(f"  on: {', '.join(_enabled(args))}" + (f"; check: {args.check}" if args.check else ""))
        if traces:
            print(f"  every session traced to {traces}; see it live: agentdiff hub {project}")
        print("  off again: agentdiff guard --uninstall")
        return 0
    if args.uninstall:
        try:
            gone = uninstall(project)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print("guards removed" if gone else "no guards were installed here")
        return 0
    if args.status:
        from ..guard import summary
        s = summary(project)
        if not s:
            print("the guards have refused nothing here yet")
            return 0
        print(f"{s['fired']} refusal(s) over {s['sessions']} session(s): "
              + ", ".join(f"{k} {v}" for k, v in sorted(s["by_guard"].items())))
        for r in s["recent"]:
            print(f"  [{r.get('guard')}] {r.get('reason')}")
        return 0
    return _hook(args, project)


def _grade(path: Path, root: Path, payload: dict) -> None:
    """An ungraded session trace gets the outcome of the last check the agent ran
    after its last edit, said as such; with no such check it stays ungraded."""
    from ..guard import Guard, graded
    g = graded(Guard(root, str(payload.get("session_id") or "session")).state)
    if g is None or not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    out = data.get("outcome") or {}
    if out.get("score") is not None or not str(out.get("note") or "").startswith("ungraded"):
        return  # graded by something better: an expected answer or a grader
    out.update(success=g["success"], note=f"graded by the check it ran last, after its last edit: `{g['by'][:200]}` "
                                          f"{'passed' if g['success'] else 'failed'} (agentdiff guard)")
    data["outcome"] = out
    data.setdefault("harness", {})["graded_by"] = f"its last check after its last edit, `{g['by'][:80]}`"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _hook(args, project: Path) -> int:
    """Hook mode: the payload on stdin, the answer on stdout. Never fails the agent."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or project)
    out = None
    try:
        from ..guard import decide
        out = decide(payload, root, enabled=_enabled(args), check=args.check, max_stop_blocks=args.max_stop_blocks)
    except Exception:  # noqa: BLE001 — a guard must never be why an agent breaks
        out = None
    # a stop the guard refused is not the end of the session: the trace stays live
    if args.traces and payload.get("hook_event_name") in ("PostToolUse", "PostToolUseFailure", "Stop",
                                                               "UserPromptSubmit") and not out:
        try:
            from ..claude_code import hook_event
            # one trace per session, so sessions sit side by side on the hub rather than overwrite
            sid = "".join(ch for ch in str(payload.get("session_id") or "") if ch.isalnum())[:8]
            task = (Path(root).name or "session") + (f"-{sid}" if sid else "")
            done = hook_event(payload, traces=args.traces, task=task, agent="claude-code", expected=None, prompt=None)
            if payload.get("hook_event_name") == "Stop" and done.get("path"):
                _grade(Path(done["path"]), root, payload)
        except Exception:  # noqa: BLE001 — tracing is a bonus, never a failure
            pass
    if out:
        print(json.dumps(out))
    return 0
