"""The ``experiments`` command: whole experiments compared — averaged
diffs with intervals, and whether behaviour (not just scores) moved."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "experiments",
        help="compare whole experiments: averaged diffs with intervals, plus "
             "whether behaviour (not just scores) moved")
    parser.add_argument("dirs", nargs="+",
                        help="two or more experiment directories of traces")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Compare whole experiments: diffs of averages, with behaviour beside."""
    from ..experiments import compare_experiments, load_experiment
    named = []
    for directory in args.dirs:
        path = Path(directory)
        if not path.is_dir():
            print(f"error: {path} is not a directory", file=sys.stderr)
            return 2
        runs = load_experiment(path)
        if not runs:
            print(f"error: no valid traces in {path}", file=sys.stderr)
            return 2
        named.append((path.name or str(path), runs))
    if len(named) < 2:
        print("error: need at least two experiment directories", file=sys.stderr)
        return 2

    result = compare_experiments(named)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "experiments.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'experiments.json'}")
    print()
    print(result["narrative"])
    for d in result["diffs"]:
        print()
        print(f"{d['a']} vs {d['b']}:")
        if "reason" in d:
            print(f"  {d['reason']}")
            continue
        s_ = d["success_diff"]
        print(f"  success (B-A): {s_['observed']:+.0%}  "
              f"[{s_['low']:+.0%}, {s_['high']:+.0%}]  "
              f"{'REAL' if s_['significant'] else 'noise-level'} "
              f"over {d['shared_tasks']} shared task(s)")
        for metric, m in d["metric_diffs"].items():
            if m.get("significant_adjusted"):
                tag = "REAL (survives correction)"
            elif m["significant"]:
                tag = "interval clear, but not after correcting for 4 tests"
            else:
                tag = "noise"
            print(f"  {metric:>9} (B-A): {m['observed']:+.4g}  "
                  f"[{m['low']:+.4g}, {m['high']:+.4g}]  {tag}")
        sim = d["similarity"]
        if sim.get("cross") is not None:
            base = (f" vs within {sim['within']:.2f}" if sim.get("within") is not None else "")
            print(f"  behaviour: cross-experiment similarity {sim['cross']:.2f}{base}"
                  f" — {sim.get('note', '')}")
        if d.get("only_in_a") or d.get("only_in_b"):
            print(f"  unpaired tasks excluded: only in A {d['only_in_a']}, "
                  f"only in B {d['only_in_b']}")
    return 0
