"""The ``loop`` command: the agentic loop — run two agents, compare, read
the failures, test a prompt hypothesis as a paired experiment, keep or
revert it, spend runs where the routing pick is unclear, stop for a
stated reason — with every decision in a ledger.  Talks to a network
unless the providers are scripted; the harness is imported inside the
command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._common import provider_option_args, provider_options, split_spec
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "loop", help="the agentic loop: run two agents, compare, read the failures, test a "
                     "prompt hypothesis as a paired experiment, keep or revert it, spend runs "
                     "where the routing pick is unclear, stop for a stated reason (talks to a "
                     "network unless the providers are scripted)")
    parser.add_argument("--provider", action="append", default=[], metavar="NAME=KIND:MODEL",
                        help="an agent to run (as for `run`); the loop needs exactly two agents in all")
    parser.add_argument("--agent", action="append", default=[],
                        metavar="NAME=python:MODULE:CALLABLE | NAME=cmd:TEMPLATE",
                        help="bring your own agent (as for `run`); a cmd agent receives prompt changes "
                             "in DEEPCOMPARE_SYSTEM_PROMPT")
    parser.add_argument("--tasks", required=True, help="tasks JSON: a list of {id, prompt, expected}")
    parser.add_argument("--tools", default=None, metavar="MODULE:ATTR")
    parser.add_argument("-o", "--output", default="loop", help="output directory (default: loop)")
    parser.add_argument("--runs", type=int, default=3, help="runs per (task, agent) per batch (default 3)")
    parser.add_argument("--iterations", type=int, default=4, help="iteration budget (default 4)")
    parser.add_argument("--max-runs", type=int, default=None, help="run budget over the whole loop")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--suggest", action="append", default=[], metavar="AGENT=TEXT",
                        help="a prompt hypothesis of your own to test first, as a paired experiment")
    parser.add_argument("--family", default=None, help="regex whose first group is a task's family")
    parser.add_argument("--db", default=None, help="ingest every trace into this trace database")
    parser.add_argument("--template", default=None, help="page template (default: the blocks page)")
    parser.add_argument("--resume", action="store_true", help="continue from the ledger in the output directory")
    parser.add_argument("--golden", default=None, help="golden dataset: scores tool correctness and policy every iteration")
    parser.add_argument("--policy", default=None, help="safety policy JSON")
    parser.add_argument("--judge", default=None, metavar="NAME=KIND:MODEL",
                        help="a judging model grades every answer (tasks without an expected answer become gradable; "
                             "traces say graded_by: model)")
    parser.add_argument("--judge-with-steps", action="store_true")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """The agentic loop: run, compare, read, test a prompt hypothesis,
    keep or revert it, spend runs where the pick is unclear, stop for a
    reason — with every decision in a ledger."""
    from ..harness import agent_from_spec, provider_from_spec
    from ..harness.loop import Loop
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
            provider = make_provider(spec)
            specs[name or provider.name] = spec
        agents: dict = {}
        for entry in args.agent or []:
            name, spec = split_spec(entry)
            ext = agent_from_spec(spec, name)
            agents[ext.name] = ext
        if len(specs) + len(agents) != 2:
            raise ValueError("the loop compares exactly two agents: give two --provider/--agent entries")
        seeds: dict = {}
        for entry in args.suggest or []:
            agent, sep, text = entry.partition("=")
            if not sep or agent not in specs and agent not in agents:
                raise ValueError(f"--suggest wants AGENT=TEXT with a named agent: {entry!r}")
            seeds.setdefault(agent, []).append(text)
        db = None
        if args.db:
            from ..tracedb import TraceDB
            db = TraceDB(args.db)
        template = Path(args.template) if args.template else DEFAULT_TEMPLATE
        from ..scorecard import load_golden, load_policy
        golden = load_golden(args.golden) if args.golden else None
        policy = load_policy(args.policy) if args.policy else None
        judge_factory = None
        if args.judge:
            _jname, jspec = split_spec(args.judge)
            jkind = jspec.split(":", 1)[0].strip().lower()
            judge_factory = lambda: provider_from_spec(jspec, **({} if jkind == "scripted" else options))  # noqa: E731
            judge_factory()  # validates the spec now
        loop = Loop(tasks, specs, out_dir=args.output, provider_factory=make_provider, agents=agents, tools=tools,
                    runs=args.runs, max_iterations=args.iterations, max_runs=args.max_runs,
                    budget={"max_steps": args.max_steps}, db=db, progress=print, template=template,
                    family_pattern=args.family, seed_suggestions=seeds, resume=args.resume,
                    golden=golden, policy=policy, judge_factory=judge_factory, judge_with_steps=args.judge_with_steps)
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        ledger = loop.run()
    finally:
        if db is not None:
            db.close()
    summary = ledger["summary"]
    print(f"Wrote {Path(args.output) / 'loop.json'} and LOOP.md — {summary['iterations']} iteration(s), "
          f"{summary['spent_runs']} run(s), {summary['kept_changes']} prompt change(s) kept, "
          f"{summary['reverted_changes']} reverted, {summary.get('dropped_changes', 0)} dropped; "
          f"stopped: {(summary.get('stop') or {}).get('reason')}")
    for agent, a in summary["agents"].items():
        if a.get("runs"):
            print(f"  {agent}: success {a['success']:.0%} over {a['runs']} run(s) [{a['ci95'][0]:.2f}–{a['ci95'][1]:.2f}], "
                  f"prompt version {a['prompt_version']}")
    return 0
