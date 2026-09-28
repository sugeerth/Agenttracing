"""The ``chat`` command: a grounded conversation about one output
directory.  The engine builds the brief — every fact the directory can
vouch for, numbered — and the prompt; a model, through the harness,
phrases the answers; every answer is checked number by number against
the brief and printed with its violations attached, never silently.
Without ``--provider`` or ``--script`` the command prints the brief and
the prompt for the reader to use elsewhere, as ``narrate`` does; the
harness is imported inside ``run`` only when a provider or a script is
given, so the engine never loads network code.  Credentials come from
environment variables only and are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._common import provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "chat", help="a grounded conversation about one output directory: the engine's numbered facts are the "
                     "whole brief, a model phrases the answers, every answer is checked against the facts and "
                     "printed with its violations; without --provider prints the brief and the prompt")
    parser.add_argument("out_dir", help="an output directory (aggregate.json + report_*.json) of batch, runs, "
                                        "evolve, evolve --against or coevolve")
    parser.add_argument("--provider", default=None, metavar="NAME=KIND:MODEL",
                        help="the model to converse with (kind:model, or scripted:FILE); talks to a network unless "
                             "scripted; the harness is imported inside the command")
    parser.add_argument("--ask", default=None, metavar="QUESTION", help="one question, then exit; else a REPL on stdin")
    parser.add_argument("--script", default=None, metavar="FILE",
                        help="a scripted provider's turns (JSON list of {\"text\": ...}) instead of --provider; "
                             "no network")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def load_output_dir(path: Path) -> tuple:
    """``(aggregate, reports, error)`` from an output directory: its
    ``aggregate.json`` and every ``report_*.json`` in name order."""
    if not path.is_dir():
        return None, None, f"{path} is not a directory"
    agg_path = path / "aggregate.json"
    if not agg_path.is_file():
        return None, None, f"{path} holds no aggregate.json"
    try:
        aggregate = json.loads(agg_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return None, None, f"{agg_path} is not valid JSON: {exc}"
    reports = []
    for p in sorted(path.glob("report_*.json")):
        try:
            reports.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError as exc:
            return None, None, f"{p} is not valid JSON: {exc}"
    return aggregate, reports, None


def _questions_from_stdin():
    """Lines from stdin until EOF; a prompt is shown only on a terminal."""
    interactive = sys.stdin.isatty()
    while True:
        if interactive:
            print("? ", end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            return
        yield line.rstrip("\n")


def run(args: argparse.Namespace) -> int:
    """Build the brief; print it with the prompt when no provider is
    given, else converse.  Exit 2 on an unreadable directory or provider
    spec, 3 when the provider fails, else 0 — an unfaithful answer is
    printed flagged and changes no exit code."""
    from ..narrate import chat_brief, chat_prompt
    aggregate, reports, error = load_output_dir(Path(args.out_dir))
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    brief = chat_brief(aggregate, reports)
    facts_n = len(brief.get("facts") or [])
    if not args.provider and not args.script:
        print(chat_prompt(brief))
        if args.ask:
            print()
            print(f"QUESTION: {args.ask}")
        print()
        print(f"# {facts_n} fact(s) in the brief; hand this to a model, or run again with --provider or --script "
              f"to converse here; every answer is then checked against the facts", file=sys.stderr)
        return 0

    # the harness is imported here and nowhere else in this command: a
    # network is reachable only from a conversation the reader asked for
    from ..harness import ScriptedProvider, provider_from_spec
    from ..harness.chat import converse
    try:
        if args.script:
            provider = ScriptedProvider.from_file(args.script)
        else:
            _name, spec = split_spec(args.provider)
            kind = spec.split(":", 1)[0].strip().lower()
            provider = provider_from_spec(spec, **({} if kind == "scripted" else provider_options(args)))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Chat over {args.out_dir}: {facts_n} fact(s) in the brief; every answer is checked against them"
          + ("" if args.ask else "; a blank line is skipped, exit ends it"))
    questions = [args.ask] if args.ask else _questions_from_stdin()
    return converse(provider, brief, questions, out=print, err=lambda m: print(m, file=sys.stderr))
