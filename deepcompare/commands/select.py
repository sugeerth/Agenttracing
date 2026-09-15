"""The ``select`` command: behavioral similarity between agents and
which to actually use — redundant agents, complementary pairs, and the
cheapest portfolio that covers the tasks; ``select.json`` and the
lightweight selection page."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..report import render_html
from ..routing import routing_analysis
from ..similarity import similarity_analysis
from ..trace import Trajectory
from ._io import load_traces
from .paths import SELECT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "select",
        help="behavioral similarity between agents and which to actually use",
    )
    parser.add_argument("tracesdir",
                        help="directory of trajectory *.json files (all agents)")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.add_argument("--template",
                        help=f"viewer HTML template (default: {SELECT_TEMPLATE})")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Behavioral similarity + agent-selection analysis over a fleet."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2

    trajectories = load_traces(traces_dir)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    by_agent: dict[str, dict[str, Trajectory]] = {}
    for t in trajectories:
        by_agent.setdefault(t.agent.name, {}).setdefault(t.task.id, t)
    complete = {name: [by_agent[name][tid] for tid in sorted(by_agent[name])]
                for name in sorted(by_agent)}
    if len(complete) < 2:
        print("error: select mode needs at least 2 agents", file=sys.stderr)
        return 2

    similarity = similarity_analysis(complete)
    routing = routing_analysis(complete)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"similarity": similarity, "routing": routing}
    (out_dir / "select.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out_dir / 'select.json'}")

    template = Path(args.template) if args.template else SELECT_TEMPLATE
    if template.is_file():
        html_path = out_dir / "select.html"
        render_html([], {}, template, html_path, extra=payload)
        print(f"Wrote {html_path}")
    else:
        print(f"warning: template {template} not found; skipped HTML",
              file=sys.stderr)

    print(f"\nBehavioral similarity — {len(complete)} agents")
    print(similarity["narrative"])
    if similarity["clusters"]:
        print("\nBehavioral groups:")
        for cluster in similarity["clusters"]:
            if cluster["size"] > 1:
                print(f"  [{cluster['size']}] {', '.join(cluster['members'])}"
                      f"  (cheapest: {cluster['cheapest']})")
    if similarity["redundancies"]:
        print("\nRedundant agents:")
        for row in similarity["redundancies"][:5]:
            print(f"  drop {row['drop']} -> keep {row['keep']}: {row['summary']}")
    if similarity["complementarities"]:
        print("\nComplementary pairs:")
        for row in similarity["complementarities"][:5]:
            print(f"  {row['a']} + {row['b']}: +{row['gain_tasks']} task(s), "
                  f"{row['union_coverage']:.0%} together")

    print(f"\nAgent selection")
    print(routing["narrative"])
    for portfolio in routing["portfolios"]:
        print(f"  k={portfolio['k']}: {', '.join(portfolio['members'])} -> "
              f"{portfolio['coverage']:.0%} coverage, "
              f"${portfolio['cost_usd']:.4f} ({portfolio['search']})")
    if routing["unique_solves"]:
        print("  uniquely solved:")
        for agent, tasks in routing["unique_solves"].items():
            print(f"    {agent}: {', '.join(tasks)}")
    return 0
