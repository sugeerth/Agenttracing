"""The ``route`` command: routing features per task family — each
agent's success interval, cost, latency, steps and tool calls, and the
pick under an objective — from a trace directory or the trace database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..equality import equality_analysis
from ..router import router_hints, routing_table
from ._common import db_source_args, load_source

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "route", help="routing features per task family: each agent's success interval, "
                      "cost, latency, steps, tool calls, and the pick under an objective")
    parser.add_argument("tracesdir", nargs="?", default=None, help="directory of traces (several agents, several runs)")
    db_source_args(parser)
    parser.add_argument("--objective", choices=["success", "cost", "latency", "steps"], default="success")
    parser.add_argument("--family-pattern", default=None,
                        help="regex whose first group names a task's family (default: the task id minus a run suffix)")
    parser.add_argument("--reports", default=None, help="a batch/runs output directory, to count fault kinds per agent")
    parser.add_argument("-o", "--output", default=None, help="write routing.json here")
    parser.add_argument("--verbose", action="store_true", help="print the rationale for every family")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Routing features: per task family, every agent's success interval,
    cost, latency, steps and tool calls, and the pick under an objective."""
    if not args.db and not Path(args.tracesdir or "").is_dir():
        print(f"error: {args.tracesdir} is not a directory", file=sys.stderr)
        return 2
    trajectories = load_source(args)
    if not trajectories:
        print("error: no valid traces", file=sys.stderr)
        return 2
    reports = []
    if args.reports:
        for path in sorted(Path(args.reports).glob("report_*.json")):
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    by_task: dict = {}
    for t in trajectories:
        by_task.setdefault(t.task.id, {}).setdefault(t.agent.name, []).append(t)
    repeated = any(len(runs) > 1 for agents in by_task.values() for runs in agents.values())
    equality = equality_analysis(by_task) if repeated else None
    table = routing_table(trajectories, objective=args.objective, family_pattern=args.family_pattern,
                          reports=reports or None, equality=equality)
    table["hints"] = router_hints(table)
    if equality:
        table["equality"] = equality["per_agent"]
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(table, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    print(f"objective: {args.objective} · {len(table['families'])} famil{'y' if len(table['families']) == 1 else 'ies'} · "
          f"{len(table['overall']['candidates'])} agent(s)")
    for hint in table["hints"]:
        route = hint["route_to"]
        print(f"  {hint['family']:<28} → {route if isinstance(route, str) else ('either of ' + ' / '.join(route) if route else '—'):<24} {hint['basis']}")
    ov = table["overall"]
    print("  overall: " + ", ".join(f"{c['agent']} {c['features']['rate']:.0%} [{c['features']['ci95'][0]:.2f}–{c['features']['ci95'][1]:.2f}] n={c['features']['n']}" for c in ov["candidates"]))
    if table.get("rationale", {}).get("overall"):
        print("  rationale: " + table["rationale"]["overall"])
    if args.verbose:
        for fam, text in table["rationale"]["families"].items():
            print(f"  {fam}: {text}")
    return 0
