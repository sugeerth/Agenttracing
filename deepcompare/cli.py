"""Command-line interface for DeepCompare AI.

Usage::

    python -m deepcompare compare a.json b.json -o report.json
    python -m deepcompare batch tracesdir/ -o out/ [--template web/viewer.html]
    python -m deepcompare fleet tracesdir/ -o out/ [--weights success=0.5,...]
    python -m deepcompare gate baseline/ candidate/ -o out/ [--markdown gate.md]

``compare`` diffs a single pair of traces and prints a terminal summary
(first divergence + attribution).  ``batch`` pairs traces by task id across
the two agent names found in a directory, writes per-task reports,
``aggregate.json``, and ``report.html`` rendered from the viewer template.
``fleet`` auto-discovers all agents in a directory, ranks them (composite
score, Pareto frontier, failure fingerprints), and writes ``fleet.json``
plus a fleet ``report.html`` with spotlight pairwise reports.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
from pathlib import Path
from typing import Optional

from .adapters import from_openai_messages, from_otel_genai
from .ci import (
    DEFAULT_FAIL_ON,
    FAIL_ON_CHOICES,
    collect_trace_paths,
    exit_code as ci_exit_code,
    write_ci_artifacts,
)
from .registry import convert as registry_convert, dry_run, formats
from .profile import build_profile, profile_suite
from .cohort import GROUPERS, compare_cohorts, group_runs
from .issues import build_issues, load_suppressions, render_issues_markdown
from .conformance import check_suite, render_conformance_markdown
from .routing import routing_analysis
from .similarity import similarity_analysis
from .fleet import DEFAULT_WEIGHTS, fleet_analysis
from .gate import evaluate_gate, pair_gate_traces, render_gate_markdown
from .metrics import aggregate as build_aggregate, task_signal
from .recommend import recommend
from .triage import render_triage_text, triage
from .reliability import reliability
from .report import compare, render_html
from .stability import medoid_pairs, stability_analysis
from .trace import Trajectory
from .variance import METRICS as VARIANCE_METRICS, variance_report

#: default viewer template, relative to the repo root (parent of the package).
DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent / "web" / "viewer.html"
#: template for the lightweight agent-selection view.
SELECT_TEMPLATE = Path(__file__).resolve().parent.parent / "web" / "select.html"


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

    sa = report.get("success_analysis")
    if sa:
        print("Success analysis:")
        print(f"  {sa['narrative']}")

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

    trajectories = _load_traces_dir(traces_dir)
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
    # Re-cluster with any .agentdiffignore found beside the traces or in cwd.
    patterns = (load_suppressions(traces_dir) or load_suppressions(Path.cwd()))
    if patterns:
        agg["issues"] = build_issues(reports, patterns)
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


def _load_traces_dir(traces_dir: Path) -> list[Trajectory]:
    """Load all valid trajectory JSON files in a directory (sorted, with
    warnings to stderr for invalid ones)."""
    trajectories: list[Trajectory] = []
    for path in sorted(traces_dir.glob("*.json")):
        try:
            trajectories.append(Trajectory.from_json(path))
        except ValueError as exc:
            print(f"warning: skipping invalid trace: {exc}", file=sys.stderr)
    return trajectories


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


def _cmd_fleet(args: argparse.Namespace) -> int:
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    try:
        weights = _parse_weights(args.weights)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    trajectories = _load_traces_dir(traces_dir)
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

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"fleet": fleet, "reports": reports, "aggregate": {}}
    fleet_path = out_dir / "fleet.json"
    fleet_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {fleet_path}")

    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
    if template.is_file():
        try:
            html_path = render_html(reports, {}, template, out_dir / "report.html", fleet=fleet)
            print(f"Wrote {html_path}")
        except ValueError as exc:
            print(f"warning: could not render HTML: {exc}", file=sys.stderr)
    else:
        print(f"warning: viewer template not found at {template}; skipping report.html",
              file=sys.stderr)

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


def _add_ci_args(parser: argparse.ArgumentParser) -> None:
    """CI-artifact flags, shared by the commands that produce a verdict."""
    parser.add_argument("--junit", nargs="?", const="junit.xml",
                        help="write JUnit XML (default name: junit.xml, "
                             "relative to -o)")
    parser.add_argument("--sarif", nargs="?", const="results.sarif",
                        help="write SARIF 2.1.0 for code scanning "
                             "(default name: results.sarif, relative to -o)")
    parser.add_argument("--job-summary", nargs="?", const="ci-summary.md",
                        help="write the Markdown job summary "
                             "(default name: ci-summary.md, relative to -o)")
    parser.add_argument("--github-annotations", action="store_true",
                        help="print ::error/::warning/::notice workflow "
                             "commands on stdout, and append the job summary "
                             "to $GITHUB_STEP_SUMMARY when set")
    parser.add_argument("--fail-on", choices=FAIL_ON_CHOICES,
                        default=DEFAULT_FAIL_ON,
                        help="severity that fails the build: never | "
                             "regression (default) | pathology | any "
                             "(any includes checks that could not be "
                             "measured). Exit 0 = clean, 1 = findings at or "
                             "above the threshold, 2 = usage/data error")
    parser.set_defaults(ci=True)


def _emit_ci(args: argparse.Namespace, result: dict, out_dir: Path,
             reports: Optional[list[dict]] = None,
             trace_dir: Optional[Path] = None) -> int:
    """Write the requested CI artifacts and return the policy exit code."""
    trace_paths = collect_trace_paths(trace_dir) if trace_dir else None
    for path in write_ci_artifacts(
        result,
        out_dir,
        reports=reports,
        trace_paths=trace_paths,
        junit=args.junit,
        sarif=args.sarif,
        summary=args.job_summary,
        annotations=args.github_annotations,
        fail_on=args.fail_on,
    ):
        print(f"Wrote {path}")
    return ci_exit_code(result, reports=reports, fail_on=args.fail_on,
                        trace_paths=trace_paths)


def _cmd_gate(args: argparse.Namespace) -> int:
    base_dir = Path(args.baseline)
    cand_dir = Path(args.candidate)
    for d in (base_dir, cand_dir):
        if not d.is_dir():
            print(f"error: {d} is not a directory", file=sys.stderr)
            return 2

    baseline = _load_traces_dir(base_dir)
    candidate = _load_traces_dir(cand_dir)
    try:
        base_name, cand_name, pairs = pair_gate_traces(baseline, candidate)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    dropped = (
        {t.task.id for t in baseline} | {t.task.id for t in candidate}
    ) - {b.task.id for b, _ in pairs}
    for tid in sorted(dropped):
        print(f"warning: task {tid!r} present on one side only; skipped", file=sys.stderr)

    reports = [compare(base, cand) for base, cand in pairs]
    gate = evaluate_gate(
        reports,
        thresholds={
            "max_success_drop": args.max_success_drop,
            "max_cost_increase": args.max_cost_increase,
            "max_latency_increase": args.max_latency_increase,
        },
        allow_new_failure_modes=args.allow_new_failure_modes,
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    gate_path = out_dir / "gate.json"
    gate_path.write_text(json.dumps(gate, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    print(f"Wrote {gate_path}")
    if args.markdown:
        md_path = Path(args.markdown)
        if not md_path.is_absolute():
            md_path = out_dir / md_path
        md_path.write_text(render_gate_markdown(gate, reports), encoding="utf-8")
        print(f"Wrote {md_path}")

    print(f"Gate: baseline {base_name} vs candidate {cand_name} "
          f"({gate['tasks']} task(s))")
    for check in gate["checks"]:
        status = "PASS" if check["pass"] else "FAIL"
        print(f"  [{status}] {check['name']}: {check['detail']}")
    regressed = [s["task"] for s in gate["reports_summary"] if s["regressed"]]
    if regressed:
        print(f"  regressed tasks: {', '.join(regressed)}")
    print(f"Verdict: {gate['verdict'].upper()}")
    # The exit code is the CI policy, not the verdict: --fail-on regression
    # (the default) reproduces "gate failed -> 1" exactly, while a looser or
    # stricter threshold moves the line without changing what was reported.
    return _emit_ci(args, gate, out_dir, reports=reports, trace_dir=cand_dir)


def _run_id_from_name(path: Path) -> Optional[str]:
    """Run id from a ``<task>__<agent>__<run>.json`` filename, else None."""
    parts = path.stem.split("__")
    return parts[2] if len(parts) >= 3 else None


def _cmd_runs(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runsdir)
    if not runs_dir.is_dir():
        print(f"error: {runs_dir} is not a directory", file=sys.stderr)
        return 2

    trajectories: list[Trajectory] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            t = Trajectory.from_json(path)
        except ValueError as exc:
            print(f"warning: skipping invalid trace: {exc}", file=sys.stderr)
            continue
        name_run = _run_id_from_name(path)
        if name_run:
            t.run_id = name_run
        trajectories.append(t)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    agent_names = sorted({t.agent.name for t in trajectories})
    if len(agent_names) != 2:
        print(
            f"error: runs mode needs traces from exactly 2 agents, "
            f"found {len(agent_names)}: {', '.join(agent_names)}",
            file=sys.stderr,
        )
        return 2
    name_a, name_b = agent_names
    print(f"Agents: A={name_a}  B={name_b}")

    runs_by_task: dict[str, dict[str, list[Trajectory]]] = {}
    for t in trajectories:
        side = "a" if t.agent.name == name_a else "b"
        runs_by_task.setdefault(t.task.id, {"a": [], "b": []})[side].append(t)
    for tid in sorted(runs_by_task):
        if not runs_by_task[tid]["a"] or not runs_by_task[tid]["b"]:
            print(f"warning: task {tid!r} lacks runs for both agents; skipped",
                  file=sys.stderr)
            del runs_by_task[tid]
    if not runs_by_task:
        print("error: no tasks with runs on both sides", file=sys.stderr)
        return 2

    stability = stability_analysis(runs_by_task)
    reliability_analysis = reliability(runs_by_task)
    reports = [compare(a, b) for a, b in medoid_pairs(runs_by_task)]
    agg = build_aggregate(reports)
    agg["stability"] = stability
    agg["reliability"] = reliability_analysis
    agg["task_signal"] = task_signal(reports, stability)
    # Re-triage now that reliability is attached: it is the only block that
    # can tell triage to stop ranking cross-agent claims confidently, and it
    # arrives after aggregate() has already run.
    agg["triage"] = triage(reports, agg)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        path = out_dir / f"report_{_safe_name(report['task']['id'])}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"Wrote {path}")
    agg_path = out_dir / "aggregate.json"
    agg_path.write_text(json.dumps(agg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
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

    print(f"Runs: {stability['runs_per_agent']}")
    for entry in stability["per_task"]:
        repro = entry["divergence_reproducibility"]
        print(f"  {entry['task']}: A {entry['a']['verdict']} "
              f"B {entry['b']['verdict']} | divergence {repro['verdict']}"
              + (f" ({repro['kind']}, rate {repro['rate']:g})" if repro["kind"] else ""))
    print(stability["narrative"])
    _print_reliability(reliability_analysis)
    return 0


def _print_reliability(analysis: dict) -> None:
    """Print the reliability block: the k-curves, the consistency scores, and
    every qualifier that keeps them honest."""
    print("\nReliability (repeated runs):")
    for side in sorted(analysis["per_agent"]):
        row = analysis["per_agent"][side]
        print(f"  {row['agent']}: {row['successes']}/{row['runs_used']} run(s) "
              f"succeeded across {row['tasks_scored']} task(s); max_k={row['max_k']}")
        for label, key in (("pass^k ", "pass_hat_k"), ("pass@k ", "pass_at_k")):
            curve = row[key]["curve"]
            rendered = "  ".join(
                f"k={point['k']}:"
                + ("n/a" if point["value"] is None else f"{point['value']:.3f}")
                for point in curve
            ) or "n/a"
            print(f"    {label} {rendered}")
        for label, key in (("outcome  ", "outcome_consistency"),
                           ("trajectory", "trajectory_consistency"),
                           ("resources ", "resource_consistency")):
            block = row[key]
            value = block["value"]
            detail = (f"{value:.3f} over {block['tasks_scored']}/{block['of_tasks']} task(s)"
                      if value is not None else f"n/a ({block['reason']})")
            print(f"    {label} consistency: {detail}")
        icc = row["icc"]
        if icc.get("icc1") is not None:
            print(f"    ICC(1): {icc['icc1_clamped']:.3f} "
                  f"({icc['within_task_variance_share']:.0%} of variance is "
                  f"within-task, i.e. the agent itself)")
        else:
            print(f"    ICC(1): n/a ({icc['reason']})")
        excluded = row["excluded_runs"]
        if excluded["count"]:
            print(f"    excluded {excluded['count']}/{excluded['of_runs']} run(s) "
                  f"as harness failures: "
                  + ", ".join(f"{k} x{v}" for k, v in excluded["by_termination"].items()))
        if row["unequal_trials"]["flagged"]:
            print(f"    warning: unequal trial counts "
                  f"({row['unequal_trials']['min']}-{row['unequal_trials']['max']} "
                  f"runs per task); curves capped at the thinnest task")
        print(f"    runs advisory [{row['runs_advisory']['tier']}]: "
              f"{row['runs_advisory']['message']}")


def _cmd_select(args: argparse.Namespace) -> int:
    """Behavioral similarity + agent-selection analysis over a fleet."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2

    trajectories = _load_traces_dir(traces_dir)
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


def _cmd_check(args: argparse.Namespace) -> int:
    """Check runs against golden/reference trajectories."""
    golden_dir, run_dir = Path(args.golden), Path(args.tracesdir)
    for label, path in (("--golden", golden_dir), ("tracesdir", run_dir)):
        if not path.is_dir():
            print(f"error: {label} {path} is not a directory", file=sys.stderr)
            return 2

    goldens = {t.task.id: t for t in _load_traces_dir(golden_dir)}
    runs = {t.task.id: t for t in _load_traces_dir(run_dir)}
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
    return _emit_ci(args, suite, out_dir, trace_dir=run_dir)


def _cmd_profile(args: argparse.Namespace) -> int:
    """Build per-task reference profiles and score runs against them."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    trajectories = _load_traces_dir(traces_dir)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    source_dir = Path(args.build_from) if args.build_from else traces_dir
    source = (_load_traces_dir(source_dir) if args.build_from else trajectories)

    by_task: dict[str, list[Trajectory]] = {}
    for t in source:
        by_task.setdefault(t.task.id, []).append(t)

    profiles: dict[str, dict] = {}
    for task_id, runs in sorted(by_task.items()):
        try:
            profiles[task_id] = build_profile(
                runs, name=task_id, successes_only=not args.include_failures)
        except ValueError as exc:
            print(f"warning: no profile for {task_id}: {exc}", file=sys.stderr)
    if not profiles:
        print("error: could not build any profile", file=sys.stderr)
        return 2

    suite = profile_suite(profiles, trajectories)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profiles.json").write_text(
        json.dumps({"profiles": profiles, "suite": suite}, indent=2,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'profiles.json'}")

    print(f"\nBuilt {len(profiles)} profile(s) from "
          f"{sum(p['runs_used'] for p in profiles.values())} run(s).")
    for task_id, profile in profiles.items():
        print(f"  {task_id:<26} {' -> '.join(profile['canonical_path'])}"
              f"   [{profile['runs_used']} run(s)"
              f"{', thin' if profile['thin_evidence'] else ''}]")
    print(f"\n{suite['narrative']}")
    for row in suite["scored"]:
        if row["verdict"] in ("failed", "off-profile"):
            print(f"  [{row['verdict']}] {row['task']}/{row['agent']}: "
                  f"{row['narrative'][:150]}")
    return 0






def _cmd_progress(args: argparse.Namespace) -> int:
    """Compare two batch outputs: did the fixes from the first land?"""
    from .progress import compare_progress
    result = compare_progress(args.before, args.after)
    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 2
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "progress.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'progress.json'}")
    print()
    print(result["narrative"])
    print()
    for entry in result["actions"]:
        marker = {"resolved": "+", "improved": "~", "persists": "!",
                  "worsened": "!!", "unobservable": "?",
                  "untrackable": "?"}.get(entry["status"], " ")
        print(f"  [{marker}] {entry['status'].upper():<12} "
              f"(was #{entry['rank_before']}) {entry['action'][:80]}")
        if entry.get("reason"):
            print(f"        {entry['reason']}")
        if entry.get("occurrences"):
            occ = entry["occurrences"]
            print(f"        occurrences {occ['before']} -> {occ['after']}")
    if result["new_issues"]:
        print()
        print("  NEW issues the before-run did not have:")
        for issue in result["new_issues"]:
            print(f"    - {issue['title']} ({issue['occurrences']} occurrence(s))")
    for name, s_ in result["success_by_agent"].items():
        print(f"  {name}: success {s_['before']} -> {s_['after']} on "
              f"{s_['tasks_compared']} shared task(s)"
              + (f"; fixed {', '.join(s_['flips_fixed'])}" if s_['flips_fixed'] else "")
              + (f"; BROKE {', '.join(s_['flips_broken'])}" if s_['flips_broken'] else ""))
        if s_.get("note"):
            print(f"        note: {s_['note']}")
    return 0


def _cmd_experiments(args: argparse.Namespace) -> int:
    """Compare whole experiments: diffs of averages, with behaviour beside."""
    from .experiments import compare_experiments, load_experiment
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


def _cmd_narrate(args: argparse.Namespace) -> int:
    """Emit a narration prompt for a report, or ingest a model's answer.

    The engine never calls a model. Emit mode prints the prompt; the user
    pipes it through any LLM they like and hands the text back with
    --ingest, where it is checked number-by-number against the brief and
    stored as labelled commentary that no analysis reads.
    """
    from .narrate import (check_narration, ingest_narration, narration_brief,
                          narration_prompt)
    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"error: {report_path} is not a file", file=sys.stderr)
        return 2
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {report_path}: {exc}", file=sys.stderr)
        return 2
    brief = narration_brief(report)

    if args.ingest is None:
        print(narration_prompt(brief))
        return 0

    text = (sys.stdin.read() if args.ingest == "-" else
            Path(args.ingest).read_text(encoding="utf-8"))
    ingest_narration(report, text, brief=brief, model=args.model)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    check = report["narration"]["faithfulness"]
    print(f"narration stored in {report_path} (model: {args.model})")
    print(f"  numbers checked: {check['numbers_checked']}  "
          f"citations: {check['citations']}")
    if check["faithful"]:
        print("  faithful: every number and citation traces to the brief")
    else:
        if check["unsupported_numbers"]:
            print(f"  UNSUPPORTED numbers (not in the evidence): "
                  f"{', '.join(check['unsupported_numbers'])}")
        if check["invalid_citations"]:
            print(f"  INVALID citations: {', '.join(check['invalid_citations'])}")
        print("  stored anyway, flagged — the reader sees the warning, "
              "and no analysis reads narration either way")
    print(f"  note: {check['limit']}")
    return 0


def _cmd_variance(args: argparse.Namespace) -> int:
    """Attribute variation in outcomes to model, harness, task and noise."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    trajectories = _load_traces_dir(traces_dir)
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
    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
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


def _cmd_cohort(args: argparse.Namespace) -> int:
    """Compare groups of runs rather than individuals."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    trajectories = _load_traces_dir(traces_dir)
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


def _cmd_convert(args: argparse.Namespace) -> int:
    if args.list_formats:
        print("Known trace formats:")
        for entry in formats():
            print(f"  {entry['name']:<12} {entry['description']}")
        return 0
    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"error: {in_path} is not a file", file=sys.stderr)
        return 2
    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {in_path}: not valid JSON: {exc}", file=sys.stderr)
        return 2

    if args.list_formats:
        print("Known trace formats:")
        for entry in formats():
            print(f"  {entry['name']:<12} {entry['description']}")
        return 0

    if args.dry_run:
        report = dry_run(data, None if args.format == "auto" else args.format)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("ok") else 2

    if args.format == "auto":
        try:
            result = registry_convert(data, None)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        trajectory, warnings = result["trajectory"], result["warnings"]
        print(f"Detected format: {result['format']} "
              f"(confidence {result['confidence']:.0%})")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        tid = _safe_name(trajectory["task"]["id"])
        agent = _safe_name(trajectory["agent"]["name"])
        out_path = out_dir / f"{tid}__{agent}.json"
        out_path.write_text(
            json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"Wrote {out_path}")
        return 0

    try:
        if args.format == "otel":
            if not isinstance(data, dict) or "spans" not in data:
                print("error: otel input must be an object with a 'spans' array "
                      "(plus 'meta', or top-level 'agent' and 'task')",
                      file=sys.stderr)
                return 2
            # Metadata may sit under "meta" (same shape as the openai adapter
            # takes) or at the top level; accept either so one convention
            # works for both formats.
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            outcome = data.get("outcome") or meta.get("outcome")
            if outcome is None and ("success" in meta or "answer" in meta):
                outcome = {k: meta[k] for k in ("success", "answer", "score")
                           if k in meta}
            trajectory, warnings = from_otel_genai(
                data["spans"],
                agent=data.get("agent") or meta.get("agent") or "otel-agent",
                task=data.get("task") or meta.get("task") or "task",
                outcome=outcome,
            )
        else:
            if not isinstance(data, dict) or "messages" not in data:
                print("error: openai input must be an object with a 'messages' "
                      "array (plus optional 'meta')", file=sys.stderr)
                return 2
            trajectory, warnings = from_openai_messages(
                data["messages"], data.get("meta")
            )
    except ValueError as exc:
        print(f"error: conversion failed: {exc}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    tid = _safe_name(trajectory["task"]["id"])
    agent = _safe_name(trajectory["agent"]["name"])
    out_path = out_dir / f"{tid}__{agent}.json"
    out_path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0



def _program_name() -> str:
    """How this invocation should tell the user to call it again."""
    invoked = os.path.basename(sys.argv[0] or "")
    if invoked in ("__main__.py", "-c", ""):
        return "python -m deepcompare"
    return invoked


def build_parser() -> argparse.ArgumentParser:
    """Build the deepcompare argument parser."""
    parser = argparse.ArgumentParser(
        # The installed console script is `agentdiff`; running from a clone
        # is `python -m deepcompare`. Printing the wrong one sends people to
        # a command they do not have.
        prog=_program_name(), description="git diff for AI agents"
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

    p_fleet = sub.add_parser("fleet", help="rank and cross-compare N agents on a shared task set")
    p_fleet.add_argument("tracesdir", help="directory of trajectory *.json files (all agents)")
    p_fleet.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_fleet.add_argument(
        "--template",
        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})",
    )
    p_fleet.add_argument(
        "--weights",
        help="composite weight overrides, e.g. success=0.45,cost=0.15 "
        f"(defaults: {', '.join(f'{k}={v}' for k, v in DEFAULT_WEIGHTS.items())})",
    )
    p_fleet.set_defaults(func=_cmd_fleet)

    p_gate = sub.add_parser(
        "gate", help="regression-gate a candidate agent's traces against a baseline"
    )
    p_gate.add_argument("baseline", help="directory of baseline agent traces")
    p_gate.add_argument("candidate", help="directory of candidate agent traces")
    p_gate.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_gate.add_argument("--markdown", help="also write a Markdown summary (path, relative to -o)")
    p_gate.add_argument("--max-success-drop", type=float, default=0.0,
                        help="max allowed success-rate drop (default 0)")
    p_gate.add_argument("--max-cost-increase", type=float, default=0.10,
                        help="max allowed relative mean-cost rise (default 0.10)")
    p_gate.add_argument("--max-latency-increase", type=float, default=0.25,
                        help="max allowed relative mean-latency rise (default 0.25)")
    p_gate.add_argument("--allow-new-failure-modes", action="store_true",
                        help="do not fail the gate on new failure-origin categories")
    _add_ci_args(p_gate)
    p_gate.set_defaults(func=_cmd_gate)

    p_runs = sub.add_parser(
        "runs", help="multi-run stability analysis over <task>__<agent>__<run>.json traces"
    )
    p_runs.add_argument("runsdir", help="directory of multi-run trajectory *.json files")
    p_runs.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_runs.add_argument("--template",
                        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    p_runs.set_defaults(func=_cmd_runs)

    p_profile = sub.add_parser(
        "profile",
        help="build reference profiles from many runs and score runs against them",
    )
    p_profile.add_argument("tracesdir", help="directory of trajectory *.json files")
    p_profile.add_argument("--build-from",
                           help="directory to learn the profiles from "
                                "(default: the same directory)")
    p_profile.add_argument("--include-failures", action="store_true",
                           help="learn the norm from failures too (default: "
                                "successes only)")
    p_profile.add_argument("-o", "--output", default="out",
                           help="output directory (default: out)")
    p_profile.set_defaults(func=_cmd_profile)

    p_progress = sub.add_parser(
        "progress",
        help="compare two batch outputs: which triage actions resolved, "
             "which persist, what newly appeared")
    p_progress.add_argument("before", help="batch output directory from before the fix")
    p_progress.add_argument("after", help="batch output directory from after the fix")
    p_progress.add_argument("-o", "--output", default="out",
                            help="output directory (default: out)")
    p_progress.set_defaults(func=_cmd_progress)

    p_experiments = sub.add_parser(
        "experiments",
        help="compare whole experiments: averaged diffs with intervals, plus "
             "whether behaviour (not just scores) moved")
    p_experiments.add_argument("dirs", nargs="+",
                               help="two or more experiment directories of traces")
    p_experiments.add_argument("-o", "--output", default="out",
                               help="output directory (default: out)")
    p_experiments.set_defaults(func=_cmd_experiments)

    p_narrate = sub.add_parser(
        "narrate",
        help="emit an LLM narration prompt for a report, or ingest the answer "
             "(checked against the evidence; commentary only)")
    p_narrate.add_argument("report", help="a report_*.json produced by batch/compare")
    p_narrate.add_argument("--ingest", metavar="FILE",
                           help="narration text to attach ('-' for stdin); "
                                "omit to print the prompt")
    p_narrate.add_argument("--model", default="unspecified",
                           help="model name recorded as provenance")
    p_narrate.set_defaults(func=_cmd_narrate)

    p_variance = sub.add_parser(
        "variance",
        help="attribute variation in outcomes to model, harness, task and noise")
    p_variance.add_argument("tracesdir", help="directory of traces")
    p_variance.add_argument("-o", "--output", default="out",
                            help="output directory (default: out)")
    p_variance.add_argument("--template", default=None,
                            help="HTML template (default: the standard viewer)")
    p_variance.add_argument("--metrics", nargs="+",
                            default=["success", "tokens", "latency_s"],
                            choices=sorted(VARIANCE_METRICS),
                            help="metrics to decompose")
    p_variance.set_defaults(func=_cmd_variance)

    p_cohort = sub.add_parser(
        "cohort", help="compare groups of runs (by model, agent, version, task)")
    p_cohort.add_argument("tracesdir", help="directory of trajectory *.json files")
    p_cohort.add_argument("--by", default="model",
                          choices=sorted(GROUPERS),
                          help="how to group runs into cohorts (default: model)")
    p_cohort.add_argument("-o", "--output", default="out",
                          help="output directory (default: out)")
    p_cohort.set_defaults(func=_cmd_cohort)

    p_check = sub.add_parser(
        "check",
        help="check runs against golden/reference trajectories (conformance)",
    )
    p_check.add_argument("tracesdir", help="directory of run trajectory *.json files")
    p_check.add_argument("--golden", required=True,
                         help="directory of reference trajectory *.json files")
    p_check.add_argument("-o", "--output", default="out",
                         help="output directory (default: out)")
    p_check.add_argument("--markdown",
                         help="also write a shareable markdown summary")
    p_check.add_argument("--max-extra-steps", type=int, default=0,
                         help="added/skipped steps tolerated before a run counts "
                              "as a deviation (default: 0)")
    _add_ci_args(p_check)
    p_check.set_defaults(func=_cmd_check)

    p_select = sub.add_parser(
        "select",
        help="behavioral similarity between agents and which to actually use",
    )
    p_select.add_argument("tracesdir",
                          help="directory of trajectory *.json files (all agents)")
    p_select.add_argument("-o", "--output", default="out",
                          help="output directory (default: out)")
    p_select.add_argument("--template",
                          help=f"viewer HTML template (default: {SELECT_TEMPLATE})")
    p_select.set_defaults(func=_cmd_select)

    p_convert = sub.add_parser(
        "convert", help="convert foreign trace formats to SCHEMA trajectories"
    )
    # Derived from the registry, not hardcoded: a format that --list-formats
    # advertises must be selectable, including one a third party registered.
    p_convert.add_argument("--format", default="auto",
                           choices=("auto",) + tuple(sorted(f["name"] for f in formats())),
                           help="input format")
    p_convert.add_argument("input", nargs="?", default="",
                           help="input JSON file")
    p_convert.add_argument("-o", "--output", default="out",
                           help="output directory (default: out)")
    p_convert.add_argument("--dry-run", action="store_true",
                           help="report what the conversion would produce, "
                                "including fidelity counters, without writing")
    p_convert.add_argument("--list-formats", action="store_true",
                           help="list the known trace formats and exit")
    p_convert.set_defaults(func=_cmd_convert)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
