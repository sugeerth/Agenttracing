"""The ``run`` command: a task set against one or more model providers
(or your own agents), recorded as SCHEMA traces.  The one command whose
purpose is to talk to a network; the harness is imported inside the
command so the analysis commands never load it."""

from __future__ import annotations

import argparse
import sys

from ._common import provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "run", help="run a task set against one or more model providers and "
                    "record SCHEMA traces (the only command that talks to a "
                    "network)")
    parser.add_argument("--provider", action="append", default=[],
                        metavar="NAME=KIND:MODEL",
                        help="an agent to run, e.g. atlas=openai:gpt-4o, "
                             "local=ollama:llama3.1, ref=anthropic:claude-…, "
                             "or fixture=scripted:turns.json; repeatable — "
                             "the NAME= prefix is optional and defaults to "
                             "kind-model")
    parser.add_argument("--tasks", required=True,
                        help="tasks JSON: a list of {id, prompt, expected}")
    parser.add_argument("--tools", default=None, metavar="MODULE:ATTR",
                        help="Python module attribute yielding a list of "
                             "harness.Tool (default: no tools)")
    parser.add_argument("-o", "--output", default="traces",
                        help="trace directory (default: traces)")
    parser.add_argument("--runs", type=int, default=1,
                        help="repetitions per (task, agent) — writes "
                             "task__agent__rN.json for the runs command")
    parser.add_argument("--max-steps", type=int, default=12,
                        help="provider turns per task before max_steps "
                             "termination (default 12)")
    parser.add_argument("--agent", action="append", default=[],
                        metavar="NAME=python:MODULE:CALLABLE | NAME=cmd:TEMPLATE",
                        help="bring your own agent: a Python callable (task, tools) "
                             "-> SCHEMA trace or OpenAI-style messages, or a shell "
                             "command with {prompt_file} and {out_file}; the harness "
                             "grades, declares termination, names the file")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    # imported here, not at module top: the harness is the one place that
    # talks to a network, and the analysis commands must not load it
    from ..harness import agent_from_spec, provider_from_spec, run_suite
    from ..harness.runner import load_tasks, load_tools
    options = provider_options(args)

    def make_provider(spec: str):
        kind = spec.split(":", 1)[0].strip().lower()
        return provider_from_spec(spec, **({} if kind == "scripted" else options))

    try:
        tasks = load_tasks(args.tasks)
        tools = load_tools(args.tools) if args.tools else []
        specs: dict = {}
        for entry in args.provider or []:
            name, spec = split_spec(entry)
            provider = make_provider(spec)  # validates the spec now
            specs[name or provider.name] = spec
        agents: dict = {}
        for entry in args.agent or []:
            name, spec = split_spec(entry)
            ext = agent_from_spec(spec, name)
            agents[ext.name] = ext
        if not specs and not agents:
            raise ValueError("give at least one --provider or --agent")
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    manifest = run_suite(
        specs, tasks, tools, out_dir=args.output, runs=args.runs,
        budget={"max_steps": args.max_steps},
        provider_factory=make_provider, agents=agents,
        progress=lambda line: print(f"  running {line}"))
    written = len(manifest["traces"])
    ok = sum(1 for t in manifest["traces"] if t["success"] is True)
    print(f"Wrote {written} trace(s) to {manifest['out_dir']} — "
          f"{ok}/{written} succeeded"
          + (f", {manifest['provider_failures']} provider failure(s) recorded "
             "as infrastructure_error" if manifest["provider_failures"] else ""))
    lineup = len(specs) + len(agents)
    print(f"Next: python -m deepcompare "
          f"{'runs' if args.runs > 1 else 'batch' if lineup == 2 else 'fleet'} "
          f"{manifest['out_dir']} -o out")
    return 0
