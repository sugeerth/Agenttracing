"""The ``variance`` command: variation in outcomes attributed to model,
harness, task and noise; ``variance.json`` and a page that carries the
decomposition alone (there are no pair reports here, so the
report-shaped blocks hide themselves)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..report import render_html
from ..variance import METRICS as VARIANCE_METRICS, variance_report
from ._io import load_traces, template_from

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "variance",
        help="attribute variation in outcomes to model, harness, task and noise")
    parser.add_argument("tracesdir", help="directory of traces")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.add_argument("--template", default=None,
                        help="HTML template (default: the standard viewer)")
    parser.add_argument("--metrics", nargs="+",
                        default=["success", "tokens", "latency_s"],
                        choices=sorted(VARIANCE_METRICS),
                        help="metrics to decompose")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Attribute variation in outcomes to model, harness, task and noise."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    trajectories = load_traces(traces_dir)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    result = variance_report(trajectories, metrics=args.metrics)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "variance.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'variance.json'}")

    # A decomposition with no page is a decomposition nobody looks at. There
    # are no pairwise reports here, so the payload carries the aggregate
    # alone and the report-shaped blocks correctly hide themselves.
    template = template_from(args)
    if template.is_file():
        try:
            html_path = render_html([], {"variance": result}, template,
                                    out_dir / "report.html")
            print(f"Wrote {html_path}")
        except (OSError, ValueError) as exc:
            print(f"warning: could not write report.html: {exc}", file=sys.stderr)
    print()
    print(result["narrative"])
    for metric, block in result["metrics"].items():
        print()
        print(f"{metric}:")
        if not block["components"]:
            print(f"  {block['reason']}")
            continue
        rows = sorted(block["components"].items(),
                      key=lambda kv: -(kv[1]["omega_squared_min"] or -1))
        width = max(len(name) for name, _ in rows)
        for name, comp in rows:
            raw = (f"{comp['min_share']:6.1%}" if comp["identified"]
                   else f"{comp['min_share']:5.1%}-{comp['max_share']:.1%}")
            omega = comp["omega_squared_min"]
            corrected = "at chance" if omega is None or omega <= 0 else f"{omega:.1%}"
            print(f"  {name:<{width}}  raw {raw}   corrected {corrected:>9}"
                  f"   ({comp['levels']} level(s), {comp['expected_by_chance']:.1%} "
                  f"expected by chance)")
        print(f"  {'residual':<{width}}  {block['residual']:6.1%}   "
              f"— {block['residual_meaning']}")
        if block["caveat"]:
            print(f"  caveat: {block['caveat']}")
    return 0
