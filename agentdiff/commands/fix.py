"""The ``fix`` command: a duel with no prompt to write.

``agentdiff fix`` finds the project's test command, runs it once on an
untouched copy, and, when it fails, gives two agents the same task: make
it pass, without touching the tests, with the end of the failing output
to start from. When it already passes there is nothing to fix and no
agent is started. Everything else is `duel`'s.

``agentdiff fix --evolve 3`` gives the same task to a self-evolving
harness instead (``self-evolve``): the agents run, the evals judge the
runs, the harness tries one change the evals point at, keeps it only on
the counts, and goes round again, up to three generations.
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["register", "run", "task_prompt"]

#: lines of the failing output handed to both agents
OUTPUT_LINES = 40


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "fix", help="no prompt to write: find the project's tests, confirm they fail, and run two coding "
                    "agents side by side to make them pass (a duel)")
    parser.add_argument("--check", default=None, metavar="CMD", help="the test command (default: detected)")
    parser.add_argument("--agent", action="append", default=None, metavar="MODEL",
                        help="twice, as for duel (default: the CLIs installed)")
    parser.add_argument("--runs", type=int, default=1, help="runs per agent (default 1)")
    parser.add_argument("--no-live", action="store_true", help="no live page")
    parser.add_argument("--quiet", action="store_true", help="no action lines, as for duel")
    parser.add_argument("--events", action="store_true", help="every action as a line, as for duel")
    parser.add_argument("-o", "--output", default=None, metavar="DIR")
    parser.add_argument("--evolve", type=int, default=None, metavar="N",
                        help="instead of a duel: a self-evolving harness, up to N generations (docs/SELF_EVOLVE.md)")
    parser.add_argument("--hub", default=None, metavar="URL", help="--evolve: stream every run to a hub")
    parser.set_defaults(func=run)


def task_prompt(command: str, baseline: dict) -> str:
    tail = "\n".join((baseline.get("output_tail") or "").rstrip().splitlines()[-OUTPUT_LINES:])
    return (f"The project's check `{command}` fails (exit {baseline.get('exit_code')}). Make it pass by fixing "
            "the code. Do not edit, skip or delete tests.\n\nThe end of its output:\n```\n" + tail + "\n```")


def run(args: argparse.Namespace) -> int:
    from ..harness.vendors import baseline_check
    from . import duel as duel_cmd
    command = args.check
    if not command:
        command, why = duel_cmd.detect_check(".")
        if not command:
            print(f"error: {why}; give the test command with --check CMD", file=sys.stderr)
            return 2
        print(f"check: {command}  ({why})", flush=True)
    b = baseline_check({"workspace": ".", "check": command})
    if b["passed"]:
        print(f"nothing to fix: `{command}` passes ({b['seconds']:.1f}s). No agent was started.")
        return 0
    if args.evolve:
        return _evolve(args, command, b)
    print(f"`{command}` fails (exit {b['exit_code']}): two agents will make it pass", flush=True)
    argv = ["duel", task_prompt(command, b), "--check", command, "--id", "fix", "--runs", str(args.runs)]
    for a in args.agent or []:
        argv += ["--agent", a]
    for flag in ("no_live", "quiet", "events"):
        if getattr(args, flag):
            argv.append("--" + flag.replace("_", "-"))
    if args.output:
        argv += ["-o", args.output]
    parser = argparse.ArgumentParser(prog="agentdiff")
    duel_cmd.register(parser.add_subparsers(dest="command"))
    ns = parser.parse_args(argv)
    # the check just ran on an untouched copy: the duel records it, not a second run
    ns.baselines = {"fix": b}
    return duel_cmd.run(ns)


def _evolve(args, command: str, b: dict) -> int:
    import json
    import os
    from pathlib import Path
    from . import self_evolve as se
    out = Path(args.output or "fix-evolve")
    out.mkdir(parents=True, exist_ok=True)
    task_file = out / "tasks.json"
    task_file.write_text(json.dumps({"tasks": [{"id": "fix", "prompt": task_prompt(command, b),
                                                "workspace": os.path.abspath("."), "check": command}]}, indent=1),
                         encoding="utf-8")
    print(f"`{command}` fails (exit {b['exit_code']}): a self-evolving harness will make it pass, "
          f"up to {args.evolve} generation(s)", flush=True)
    argv = ["self-evolve", "--task", str(task_file), "--generations", str(args.evolve),
            "--runs", str(max(2, args.runs)), "-o", str(out)]
    for a in args.agent or []:
        argv += ["--agent", a]
    if args.hub:
        argv += ["--hub", args.hub]
    parser = argparse.ArgumentParser(prog="agentdiff")
    se.register(parser.add_subparsers(dest="command"))
    return se.run(parser.parse_args(argv))
