"""The ``check`` command: runs checked against golden/reference
trajectories (conformance), with the CI artifacts and exit-code policy
the gate uses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..conformance import check_suite, render_conformance_markdown
from ._common import add_ci_args, emit_ci
from ._io import load_traces

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "check",
        help="check runs against golden/reference trajectories (conformance)",
    )
    parser.add_argument("tracesdir", help="directory of run trajectory *.json files")
    parser.add_argument("--golden", required=True,
                        help="directory of reference trajectory *.json files")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.add_argument("--markdown",
                        help="also write a shareable markdown summary")
    parser.add_argument("--max-extra-steps", type=int, default=0,
                        help="added/skipped steps tolerated before a run counts "
                             "as a deviation (default: 0)")
    add_ci_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Check runs against golden/reference trajectories."""
    golden_dir, run_dir = Path(args.golden), Path(args.tracesdir)
    for label, path in (("--golden", golden_dir), ("tracesdir", run_dir)):
        if not path.is_dir():
            print(f"error: {label} {path} is not a directory", file=sys.stderr)
            return 2

    goldens = {t.task.id: t for t in load_traces(golden_dir)}
    runs = {t.task.id: t for t in load_traces(run_dir)}
    if not goldens:
        print(f"error: no valid reference traces in {golden_dir}", file=sys.stderr)
        return 2
    if not runs:
        print(f"error: no valid run traces in {run_dir}", file=sys.stderr)
        return 2

    suite = check_suite(goldens, runs, max_extra_steps=args.max_extra_steps)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Per-task pairwise reports are large; keep them out of the summary file.
    summary = {k: v for k, v in suite.items() if k != "checks"}
    summary["checks"] = [
        {k: v for k, v in check.items() if k != "report"} for check in suite["checks"]
    ]
    (out_dir / "conformance.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out_dir / 'conformance.json'}")

    if args.markdown:
        md_path = Path(args.markdown)
        if not md_path.is_absolute():
            md_path = out_dir / md_path
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_conformance_markdown(suite), encoding="utf-8")
        print(f"Wrote {md_path}")

    print()
    print(suite["narrative"])
    print(f"{'task':<28} {'verdict':<12} conformance  steps ref->run")
    for check in suite["checks"]:
        print(f"{check['task']:<28} {check['verdict']:<12} "
              f"{check['conformance']:>10.0%}  "
              f"{check['steps']['reference']:>3} -> {check['steps']['run']}")
    for check in suite["checks"]:
        if check["verdict"] != "conformant":
            print(f"\n  {check['task']}: {check['narrative']}")
            for deviation in check["deviations"][:2]:
                print(f"    [{deviation['kind']}] {deviation['summary']}")
    for task in suite["missing_reference"]:
        print(f"warning: no reference trajectory for {task}; not checked",
              file=sys.stderr)
    # Same policy as the gate: --fail-on regression means "a violation fails
    # the build", which is what this command did before the flag existed.
    return emit_ci(args, suite, out_dir, trace_dir=run_dir)
