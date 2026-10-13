"""The ``cohort`` command: groups of runs compared rather than
individuals — by model, agent, version or task — with each cohort's
success interval and the pairwise verdicts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..cohort import GROUPERS, compare_cohorts, group_runs
from ._io import load_traces

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "cohort", help="compare groups of runs (by model, agent, version, task)")
    parser.add_argument("tracesdir", help="directory of trajectory *.json files")
    parser.add_argument("--by", default="model",
                        choices=sorted(GROUPERS),
                        help="how to group runs into cohorts (default: model)")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Compare groups of runs rather than individuals."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    trajectories = load_traces(traces_dir)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    try:
        cohorts = group_runs(trajectories, by=args.by)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = compare_cohorts(cohorts)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cohorts.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"Wrote {out_dir / 'cohorts.json'}")

    print(f"\nCohorts by {args.by}:")
    for summary in result["cohorts"]:
        low, high = summary["success_ci"]
        print(f"  {summary['cohort']:<22} {summary['runs']:>4} run(s)  "
              f"success {summary['success_rate']:>6.0%} "
              f"[{low:.0%}-{high:.0%}]  "
              f"${summary['mean_cost_usd']:.4f}/run")
    print(f"\n{result['narrative']}")
    for pair in result["pairs"]:
        marker = "*" if pair["success_difference"]["significant"] else " "
        print(f" {marker} {pair['verdict']}")
    return 0
