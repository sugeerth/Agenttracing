"""The ``batch`` command: a directory of traces from exactly two agents,
paired by task id — a pair report per task, ``aggregate.json`` (with the
routing table, the scorecard, and the systematic issues re-clustered
under any ``.agentdiffignore``), and the report page; then the terminal
summary that ends with the triage, because a reader who stops there has
still been told what to do first."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..issues import build_issues, load_suppressions
from ..metrics import aggregate as build_aggregate
from ..report import attach_milestones, compare
from ..router import routing_table
from ..scorecard import load_golden, load_policy, scorecard as build_scorecard
from ..trace import Trajectory
from ..triage import render_triage_text
from ._io import load_traces, template_from, write_aggregate, write_page, write_report
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("batch", help="compare a directory of traces pairwise by task")
    parser.add_argument("tracesdir", help="directory of trajectory *.json files")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument(
        "--template",
        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})",
    )
    parser.add_argument("--golden", default=None, help="golden dataset (tasks JSON with expected_tools, forbidden_tools, …): scores tool correctness and policy")
    parser.add_argument("--policy", default=None, help="safety policy JSON (forbidden_tools, forbidden_patterns, max_writes, write_requires_read)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2

    trajectories = load_traces(traces_dir)
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

    try:
        golden_set = load_golden(args.golden) if getattr(args, "golden", None) else None
        policy = load_policy(args.policy) if getattr(args, "policy", None) else None
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    reports: list[dict] = []
    for task_id in sorted(by_task):
        pair = by_task[task_id]
        if name_a not in pair or name_b not in pair:
            print(f"warning: task {task_id!r} lacks a trace for both agents; skipped",
                  file=sys.stderr)
            continue
        report = compare(pair[name_a], pair[name_b])
        if golden_set or policy:
            # progress before the answer: the golden task's milestones, both
            # runs; and the trust section re-read under the policy
            attach_milestones(report, golden_set, policy=policy)
        reports.append(report)
        write_report(out_dir, report)

    if not reports:
        print("error: no complete task pairs found", file=sys.stderr)
        return 2

    agg = build_aggregate(reports)
    agg["routing"] = routing_table(trajectories)
    try:
        agg["scorecard"] = build_scorecard(trajectories, golden_set, policy)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # Re-cluster with any .agentdiffignore found beside the traces or in cwd.
    patterns = (load_suppressions(traces_dir) or load_suppressions(Path.cwd()))
    if patterns:
        agg["issues"] = build_issues(reports, patterns)
    write_aggregate(out_dir, agg)
    write_page(out_dir, reports, agg, template_from(args))

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
    issues = agg.get("issues") or {}
    if issues.get("issues"):
        print(f"\nSystematic issues: {issues['narrative']}")
        for issue in issues["issues"]:
            if issue["suppressed"]:
                continue
            print(f"  [{issue['severity']}] {issue['title']}")
            print(f"    {len(issue['tasks'])} task(s): {', '.join(issue['tasks'])}"
                  f"  |  {issue['failures_caused']} failure(s)"
                  f"  |  +{issue['extra_tokens']:,} tokens")
            print(f"    fingerprint: {issue['id']}")
        if issues["suppressed"]:
            print(f"  ({issues['suppressed']} suppressed by .agentdiffignore)")
    diagnosis = agg.get("diagnosis") or {}
    if diagnosis.get("note"):
        print(f"Diagnosis: {diagnosis['note']}")
    if agg["playbook"]:
        print("Playbook — what good looks like:")
        for habit in agg["playbook"]:
            agents = ", ".join(habit["agents"])
            print(f"  [{habit['kind']}] {agents}: {habit['habit']} — "
                  f"{habit['evidence']}; {habit['impact']}")
    # Printed last because it is the answer to "so which of all that first?" —
    # a reader who stops here has still been told what to do.
    for line in render_triage_text(agg.get("triage") or {}):
        print(line)
    return 0
