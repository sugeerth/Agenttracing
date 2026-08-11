"""Command-line interface for DeepCompare AI.

Usage::

    python -m deepcompare compare a.json b.json -o report.json
    python -m deepcompare batch tracesdir/ -o out/ [--template web/viewer.html]

``compare`` diffs a single pair of traces and prints a terminal summary
(first divergence + attribution).  ``batch`` pairs traces by task id across
the two agent names found in a directory, writes per-task reports,
``aggregate.json``, and ``report.html`` rendered from the viewer template.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

from .metrics import aggregate as build_aggregate
from .recommend import recommend
from .report import compare, render_html
from .trace import Trajectory

#: default viewer template, relative to the repo root (parent of the package).
DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "web" / "viewer.html"


def _load(path: str) -> Trajectory:
    return Trajectory.from_json(path)


def _fmt_outcome(side: str, report_side: dict) -> str:
    outcome = report_side["outcome"]
    status = "SUCCESS" if outcome["success"] else "FAILURE"
    return f"  {side}: {report_side['agent']['name']:<20} {status:<8} answer: {outcome['answer'][:70]}"


def _print_summary(report: dict) -> None:
    print(f"Task: {report['task']['id']}")
    print(f"Prompt: {report['task']['prompt'][:100]}")
    print(_fmt_outcome("A", report["a"]))
    print(_fmt_outcome("B", report["b"]))

    delta = report["metrics_delta"]
    print("Metrics (A vs B):")
    for key in ("steps", "tokens", "cost_usd", "latency_s", "tool_calls", "searches"):
        pair = delta[key]
        print(f"  {key:<10} a={pair['a']:<10g} b={pair['b']:<10g}")

    divergences = report["divergences"]
    if divergences:
        first = divergences[0]
        print(f"Divergences: {len(divergences)}")
        print(
            f"  #1 [{first['kind']}] at a_index={first['a_index']} "
            f"b_index={first['b_index']}"
        )
        print(f"     {first['summary']}")
        print(f"     downstream: {json.dumps(first['downstream'])}")
    else:
        print("Divergences: none (trajectories fully match)")

    attribution = report["attribution"]
    print("Attribution:")
    print(f"  failed_agent: {attribution['failed_agent']}")
    if attribution["failed_agent"] is not None:
        print(f"  root_cause_step: {attribution['root_cause_step']}")
        print(f"  chain: {attribution['chain']}")
        print(f"  category: {attribution['category']}")
    print(f"  {attribution['explanation']}")

    recs = recommend([report])
    if recs:
        print("Recommendations:")
        for rec in recs:
            print(f"  [{rec['severity']}/{rec['category']}] {rec['agent']} — {rec['finding']}")
            print(f"    suggested prompt: {rec['suggested_prompt']}")
            print(f"    expected gain: {rec['expected_gain']}")


def _cmd_compare(args: argparse.Namespace) -> int:
    try:
        a = _load(args.a)
        b = _load(args.b)
        report = compare(a, b)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    _print_summary(report)
    return 0


def _safe_name(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", task_id)


def _cmd_batch(args: argparse.Namespace) -> int:
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2

    trajectories: list[Trajectory] = []
    for path in sorted(traces_dir.glob("*.json")):
        try:
            trajectories.append(Trajectory.from_json(path))
        except ValueError as exc:
            print(f"warning: skipping invalid trace: {exc}", file=sys.stderr)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    agent_names = sorted({t.agent.name for t in trajectories})
    if len(agent_names) != 2:
        print(
            f"error: batch mode needs traces from exactly 2 agents, "
            f"found {len(agent_names)}: {', '.join(agent_names) or '(none)'}",
            file=sys.stderr,
        )
        return 2
    name_a, name_b = agent_names
    print(f"Agents: A={name_a}  B={name_b}")

    by_task: dict[str, dict[str, Trajectory]] = {}
    for t in trajectories:
        by_task.setdefault(t.task.id, {}).setdefault(t.agent.name, t)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict] = []
    for task_id in sorted(by_task):
        pair = by_task[task_id]
        if name_a not in pair or name_b not in pair:
            print(f"warning: task {task_id!r} lacks a trace for both agents; skipped",
                  file=sys.stderr)
            continue
        report = compare(pair[name_a], pair[name_b])
        reports.append(report)
        report_path = out_dir / f"report_{_safe_name(task_id)}.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {report_path}")

    if not reports:
        print("error: no complete task pairs found", file=sys.stderr)
        return 2

    agg = build_aggregate(reports)
    agg_path = out_dir / "aggregate.json"
    agg_path.write_text(json.dumps(agg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {agg_path}")

    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
    if template.is_file():
        try:
            html_path = render_html(reports, agg, template, out_dir / "report.html")
            print(f"Wrote {html_path}")
        except ValueError as exc:
            print(f"warning: could not render HTML: {exc}", file=sys.stderr)
    else:
        print(f"warning: viewer template not found at {template}; skipping report.html",
              file=sys.stderr)

    print(
        f"Done: {len(reports)} task pair(s), "
        f"success A={agg['success_rate']['a']:.0%} B={agg['success_rate']['b']:.0%}"
    )
    for flag in agg["regressions"]:
        print(f"  regression: {flag}")
    if agg["recommendations"]:
        print("Recommendations:")
        for rec in agg["recommendations"]:
            print(f"  [{rec['severity']}/{rec['category']}] {rec['agent']} — {rec['finding']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the deepcompare argument parser."""
    parser = argparse.ArgumentParser(
        prog="deepcompare", description="git diff for AI agents"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_compare = sub.add_parser("compare", help="compare one pair of trace files")
    p_compare.add_argument("a", help="trajectory JSON for agent A")
    p_compare.add_argument("b", help="trajectory JSON for agent B")
    p_compare.add_argument("-o", "--output", help="write the comparison report JSON here")
    p_compare.set_defaults(func=_cmd_compare)

    p_batch = sub.add_parser("batch", help="compare a directory of traces pairwise by task")
    p_batch.add_argument("tracesdir", help="directory of trajectory *.json files")
    p_batch.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_batch.add_argument(
        "--template",
        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})",
    )
    p_batch.set_defaults(func=_cmd_batch)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
