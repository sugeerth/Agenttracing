"""The ``bench`` command: measure the diagnoser against its ground-truth
benchmark corpus (cause kind, decisive step, abstention, chain recovery)
and, with ``--strict``, fail on the same floors the test suite enforces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "bench",
        help="measure the diagnoser against its ground-truth benchmark "
             "(cause kind, decisive step, abstention, chain recovery)")
    parser.add_argument(
        "traces", nargs="?",
        default=str(Path(__file__).resolve().parents[2]
                    / "demo" / "diagnosis_bench" / "traces"),
        help="benchmark corpus directory with MANIFEST.json "
             "(default: the shipped demo/diagnosis_bench/traces)")
    parser.add_argument("-o", "--output", default=None,
                        help="also write the full result JSON here")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when any CI floor is broken "
                             "(the same floors the test suite enforces)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Measure the diagnoser against its ground-truth benchmark corpus."""
    from ..bench import floor_violations, format_scorecard, run_benchmark
    traces_dir = Path(args.traces)
    if not (traces_dir / "MANIFEST.json").is_file():
        print(f"error: no MANIFEST.json in {traces_dir} — generate the "
              f"corpus with demo/diagnosis_bench/generate.py",
              file=sys.stderr)
        return 2
    result = run_benchmark(traces_dir)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(f"Wrote {out}")
    print(format_scorecard(result))
    problems = floor_violations(result)
    if problems:
        print("Floor violations:")
        for problem in problems:
            print(f"  {problem}")
    if args.strict and problems:
        return 1
    return 0
