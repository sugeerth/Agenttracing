"""The ``again`` command: more runs of the same comparison.

One run each settles little; the report's intervals say how little.
``agentdiff again`` adds runs to the newest duel under this directory
(or the one named): the same task, the same two agents, the same
settings, numbered after the runs already there, and one report over
all of them. It refuses when a task's workspace has changed since, because
runs that started from different code are not the same comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "again", help="add runs to the newest duel here (same task, agents and settings) and report "
                      "over all of them")
    parser.add_argument("runs", nargs="?", type=int, default=1, help="runs to add per agent (default 1)")
    parser.add_argument("--dir", default=None, metavar="DUEL_DIR",
                        help="the duel to extend (default: the newest duel-out* here)")
    parser.add_argument("--no-live", action="store_true", help="no live page")
    parser.add_argument("--quiet", action="store_true", help="no action lines, as for duel")
    parser.add_argument("--events", action="store_true", help="every action as a line, as for duel")
    parser.set_defaults(func=run)


def _newest(where: Path):
    dirs = [p.parent for p in where.glob("duel-out*/plan.json")]
    return max(dirs, key=lambda d: d.stat().st_mtime_ns) if dirs else None


def run(args: argparse.Namespace) -> int:
    from ..harness.vendors import snapshot, workspace_sha
    from . import duel as duel_cmd
    out = Path(args.dir) if args.dir else _newest(Path("."))
    if out is None or not (out / "plan.json").is_file():
        print('error: no duel to extend here (a duel run since `plan.json` was added); '
              'run one with agentdiff "the task"', file=sys.stderr)
        return 2
    if args.runs < 1:
        print("error: add at least one run", file=sys.stderr)
        return 2
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    records = duel_cmd._load_records(out)
    # the same starting code, or it is a different comparison
    recorded = {}
    for r in records:
        recorded.setdefault(r["task"], (r.get("setup") or {}).get("workspace_sha"))
    for t in plan["tasks"]:
        now = workspace_sha(snapshot(Path(t["workspace"])))
        was = recorded.get(t["id"])
        if was and now != was:
            print(f"error: {t['workspace']} has changed since this duel ran (workspace {was} then, {now} now): "
                  "runs from different starting code are not the same comparison. Start a new one with "
                  'agentdiff "the task".', file=sys.stderr)
            return 2
    numbers = [int(m.group(1)) for r in records for m in [re.fullmatch(r"r(\d+)", str(r["run"]))] if m]
    first = max(numbers, default=0) + 1
    baselines = {}
    for r in records:
        if isinstance(r.get("baseline"), dict):
            baselines.setdefault(r["task"], {"command": (r.get("setup") or {}).get("check"), **r["baseline"]})
    opts = plan.get("options") or {}
    argv = ["duel", "--task", str(out / "plan.json"), "--runs", str(args.runs), "-o", str(out)]
    for a in plan["agents"]:
        argv += ["--agent", a]
    flags = {"band": "--band", "budget_tokens": "--budget-tokens", "timeout": "--timeout",
             "check_timeout": "--check-timeout", "sandbox": "--sandbox",
             "claude_permission_mode": "--claude-permission-mode", "max_stream_mb": "--max-stream-mb",
             "codex_bin": "--codex-bin", "claude_bin": "--claude-bin"}
    for key, flag in flags.items():
        if opts.get(key) is not None:
            argv += [flag, str(opts[key])]
    for key, flag in (("sequential", "--sequential"), ("use_my_config", "--use-my-config"),
                      ("no_baseline", "--no-baseline")):
        if opts.get(key):
            argv.append(flag)
    for flag in ("no_live", "quiet", "events"):
        if getattr(args, flag):
            argv.append("--" + flag.replace("_", "-"))
    parser = argparse.ArgumentParser(prog="agentdiff")
    duel_cmd.register(parser.add_subparsers(dest="command"))
    ns = parser.parse_args(argv)
    ns.again = {"first_run": first, "baselines": baselines}
    print(f"again: {args.runs} more run(s) per agent in {out}, from r{first}", flush=True)
    return duel_cmd.run(ns)
