"""The ``hook`` command: the Claude Code hook — the hook payload on
stdin becomes a live step (PostToolUse) or the final trace from the
transcript (Stop)."""

from __future__ import annotations

import argparse

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "hook", help="Claude Code hook (reads the hook payload on stdin): PostToolUse "
                     "appends a live step, Stop writes the final trace from the transcript")
    parser.add_argument("--traces", default="traces", help="trace directory (watch it with `deepcompare watch`)")
    parser.add_argument("--task", required=True, help="task id the session is working on")
    parser.add_argument("--agent", default="claude-code")
    parser.add_argument("--expected", default=None, help="expected answer, for grading (else the run is ungraded)")
    parser.add_argument("--prompt", default=None, help="the task prompt, if UserPromptSubmit is not hooked")
    parser.add_argument("--db", default=None, help="also ingest the final trace into this trace database")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Claude Code hook: stdin payload → a live step or the final trace."""
    from ..claude_code import main_hook
    argv = ["--traces", args.traces, "--task", args.task, "--agent", args.agent]
    if args.expected:
        argv += ["--expected", args.expected]
    if args.prompt:
        argv += ["--prompt", args.prompt]
    if args.db:
        argv += ["--db", args.db]
    return main_hook(argv)
