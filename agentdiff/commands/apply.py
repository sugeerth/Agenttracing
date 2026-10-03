"""The ``apply`` command: keep one agent's change.

A duel ends with each agent's change beside the project, never in it.
``agentdiff apply sonnet`` writes that agent's latest passing run into the
workspace, file for file as the agent left it (binary and large files
too; a patch carries neither). It picks the agent itself when only one
passed, and refuses:

- when the workspace has changed since the duel (it would overwrite work
  the duel never saw)
- when the run failed its check, unless ``--force``
- when any of the run's files was not kept, including one withheld because
  it held a credential's value

``--dry-run`` prints the patch and writes nothing.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

__all__ = ["register", "run", "choose"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "apply", help="write one agent's change from the newest duel here into the project (its latest passing "
                      "run; refused if the project changed since)")
    parser.add_argument("agent", nargs="?", default=None, help="whose change (default: the one agent that passed)")
    parser.add_argument("--run", default=None, metavar="rN", help="which run (default: its latest passing one)")
    parser.add_argument("--task", default=None, help="which task, when the duel had several")
    parser.add_argument("--dir", default=None, metavar="DUEL_DIR", help="the duel (default: the newest here)")
    parser.add_argument("--dry-run", action="store_true", help="print the patch; write nothing")
    parser.add_argument("--force", action="store_true", help="apply a run that failed its check")
    parser.set_defaults(func=run)


def _num(run) -> int:
    m = re.fullmatch(r"r(\d+)", str(run))
    return int(m.group(1)) if m else 0


def choose(records: list, agent=None, run=None, task=None, force: bool = False) -> tuple:
    """``(record, None)`` or ``(None, why)``."""
    tasks = sorted({r["task"] for r in records})
    if task is None and len(tasks) > 1:
        return None, f"the duel had {len(tasks)} tasks: name one with --task ({', '.join(tasks)})"
    pool = [r for r in records if task is None or r["task"] == task]
    if not pool:
        return None, f"no runs of task {task!r}"
    passed = lambda r: (r.get("check") or {}).get("passed") is True  # noqa: E731
    if agent is None:
        winners = sorted({r["agent"] for r in pool if passed(r)})
        if len(winners) == 1:
            agent = winners[0]
        elif winners:
            return None, ("both passed; name whose change to keep: " +
                          "  or  ".join(f"agentdiff apply {a}" for a in winners))
        else:
            return None, "no run passed its check; name an agent and add --force to apply one anyway"
    mine = [r for r in pool if r["agent"] == agent]
    if not mine:
        return None, f"no runs by {agent!r} (agents: {', '.join(sorted({r['agent'] for r in pool}))})"
    if run is not None:
        mine = [r for r in mine if str(r["run"]) == run]
        if not mine:
            return None, f"{agent} has no run {run}"
    good = [r for r in mine if passed(r)]
    if not good and not force:
        return None, f"no run by {agent} passed its check; --force applies its latest anyway"
    return max(good or mine, key=lambda r: _num(r["run"])), None


def run(args: argparse.Namespace) -> int:
    import json
    from ..harness.vendors import snapshot, workspace_sha
    from . import duel as duel_cmd
    from .again import _newest
    out = Path(args.dir) if args.dir else _newest(Path("."))
    if out is None:
        print('error: no duel here to apply from; run one with agentdiff "the task"', file=sys.stderr)
        return 2
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    records = duel_cmd._load_records(out)
    rec, why = choose(records, args.agent, args.run, args.task, args.force)
    if rec is None:
        print(f"error: {why}", file=sys.stderr)
        return 2
    kept = rec.get("after")
    if not isinstance(kept, dict):
        print("error: this duel kept no files (it ran before `apply` existed); its patch is in "
              f"{out / rec['patch']}", file=sys.stderr)
        return 2
    task = next(t for t in plan["tasks"] if t["id"] == rec["task"])
    ws = Path(task["workspace"])
    patch = (out / rec["patch"]).read_text(encoding="utf-8") if (out / rec["patch"]).is_file() else ""
    files = rec["diff"]["files"]
    label = f"{rec['agent']} {rec['run']}"
    if args.dry_run:
        print(f"{label} ({'passed' if rec['check'].get('passed') else 'did not pass'} its check), "
              f"{len(files)} file(s) into {ws}:\n")
        print(patch or "(no text changes)")
        return 0
    if not kept.get("complete"):
        missing = (kept.get("withheld") or []) + (kept.get("not_kept") or [])
        print(f"error: {label}'s change was not kept whole ({', '.join(missing)}); nothing written",
              file=sys.stderr)
        return 2
    now = workspace_sha(snapshot(ws))
    was = (rec.get("setup") or {}).get("workspace_sha")
    if now != was:
        print(f"error: {ws} has changed since the duel (workspace {was} then, {now} now); applying would "
              "overwrite work the duel never saw. Nothing written.", file=sys.stderr)
        return 2
    src = out / kept["dir"]
    for f in files:
        target = ws / f["path"]
        if f["status"] == "deleted":
            if target.exists():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / f["path"], target)
    added, removed = rec["diff"].get("added", 0), rec["diff"].get("removed", 0)
    print(f"applied {label}: {len(files)} file(s), +{added} -{removed} "
          f"({', '.join(f['path'] for f in files[:6])}{', …' if len(files) > 6 else ''})")
    if (ws / ".git").exists():
        print("review with: git diff   ·   undo with: git checkout -- . (and remove any added files)")
    return 0
