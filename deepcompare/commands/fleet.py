"""The ``fleet`` command: N agents on a shared task set, ranked (composite
score, Pareto frontier, failure fingerprints) and cross-compared on the
spotlight pairs; ``fleet.json`` and the fleet page."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from ..fleet import DEFAULT_WEIGHTS, fleet_analysis
from ..trace import Trajectory
from ._io import load_traces, template_from, write_outputs
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("fleet", help="rank and cross-compare N agents on a shared task set")
    parser.add_argument("tracesdir", help="directory of trajectory *.json files (all agents)")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument(
        "--template",
        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})",
    )
    parser.add_argument(
        "--weights",
        help="composite weight overrides, e.g. success=0.45,cost=0.15 "
        f"(defaults: {', '.join(f'{k}={v}' for k, v in DEFAULT_WEIGHTS.items())})",
    )
    parser.set_defaults(func=run)


def _parse_weights(spec: Optional[str]) -> Optional[dict[str, float]]:
    """Parse a --weights spec like 'success=0.45,cost=0.15' into a dict."""
    if not spec:
        return None
    weights: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        key = key.strip()
        if not sep or key not in DEFAULT_WEIGHTS:
            raise ValueError(
                f"bad --weights entry {part!r}; expected one of "
                f"{', '.join(sorted(DEFAULT_WEIGHTS))} as key=value"
            )
        try:
            weights[key] = float(value)
        except ValueError as exc:
            raise ValueError(f"bad --weights value in {part!r}") from exc
    return weights or None


def run(args: argparse.Namespace) -> int:
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    try:
        weights = _parse_weights(args.weights)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    trajectories = load_traces(traces_dir)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    by_agent: dict[str, dict[str, Trajectory]] = {}
    for t in trajectories:
        by_agent.setdefault(t.agent.name, {}).setdefault(t.task.id, t)

    all_tasks = sorted({tid for tasks in by_agent.values() for tid in tasks})
    complete: dict[str, list[Trajectory]] = {}
    for name in sorted(by_agent):
        missing = [tid for tid in all_tasks if tid not in by_agent[name]]
        if missing:
            print(
                f"warning: agent {name!r} is missing task(s) "
                f"{', '.join(missing)}; skipped",
                file=sys.stderr,
            )
            continue
        complete[name] = [by_agent[name][tid] for tid in all_tasks]
    if len(complete) < 2:
        print(
            f"error: fleet mode needs at least 2 complete agents, "
            f"found {len(complete)}",
            file=sys.stderr,
        )
        return 2

    try:
        result = fleet_analysis(complete, weights=weights)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    fleet, reports = result["fleet"], result["reports"]

    write_outputs(args.output, reports, {}, template_from(args), fleet=fleet)

    agents = fleet["agents"]
    print(f"Fleet: {len(agents)} agents x {len(fleet['tasks'])} tasks")
    header = f"{'rank':>4}  {'agent':<24} {'score':>6} {'success':>8} {'tokens':>9} {'calls':>6}  pareto"
    print(header)
    print("-" * len(header))
    for a in agents:
        m = a["metrics"]
        calls = m["mean_tool_calls"] + m["mean_searches"]
        star = "*" if a["pareto"] else ""
        print(
            f"{a['rank']:>4}  {a['name']:<24} {a['score']:>6.2f} "
            f"{m['success_rate']:>8.0%} {m['mean_tokens']:>9.0f} {calls:>6.1f}  {star}"
        )
    print("Top rationales:")
    for a in agents[:3]:
        print(f"  #{a['rank']} {a['name']}: {a['rationale']}")
    print("Spotlight pairs:")
    for pair in fleet["spotlight_pairs"]:
        print(f"  {pair['a']} vs {pair['b']} — {pair['why']} "
              f"(reports {pair['report_indices']})")
    return 0
