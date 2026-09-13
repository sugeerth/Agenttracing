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

from .adapters import detect_verl, from_agent_lightning, from_openai_messages, from_otel_genai, from_verl
from .ci import (
    DEFAULT_FAIL_ON,
    FAIL_ON_CHOICES,
    collect_trace_paths,
    exit_code as ci_exit_code,
    write_ci_artifacts,
)
from .registry import convert as registry_convert, dry_run, formats
from .router import routing_table, router_hints
from .tracedb import TraceDB
from .equality import equality_analysis
from .scorecard import load_golden, load_policy, render_scorecard_markdown, scorecard as build_scorecard
from .profile import build_profile, profile_suite
from .cohort import GROUPERS, compare_cohorts, group_runs
from .issues import build_issues, load_suppressions, render_issues_markdown
from .conformance import check_suite, render_conformance_markdown
from .routing import routing_analysis
from .similarity import similarity_analysis
from .fleet import DEFAULT_WEIGHTS, fleet_analysis
from .gate import evaluate_gate, pair_gate_traces, render_gate_markdown
from .metrics import aggregate as build_aggregate
from .recommend import recommend
from .triage import render_triage_text, triage
from .reliability import reliability
from .report import compare, render_html, attach_milestones
from .trace import Trajectory
from .variance import METRICS as VARIANCE_METRICS, variance_report
from .evolve import FLAGS as EVOLVE_FLAGS, LAYOUTS as EVOLVE_LAYOUTS, VERDICTS as EVOLVE_VERDICTS

#: default viewer template, relative to the repo root (parent of the package).
from .commands.paths import DEFAULT_TEMPLATE, LEGACY_TEMPLATE  # noqa: E402
from .commands.grafana import register as _register_grafana  # noqa: E402
from .commands._common import provider_option_args as _provider_option_args, provider_options as _provider_options, split_spec as _split_spec  # noqa: E402
from .commands import (  # noqa: E402
    bench, checkpoint, context, convert, db, experiments, explain, feedback, frameworks, hook, judge, loop,
    narrate, progress, replay, rerun, rl, rlexport, route, run, watch, why,
)
from .commands import eval as eval_  # noqa: E402  (the command is named after the builtin)
from .commands import compare as compare_cmd  # noqa: E402  (report.compare is the pair function)
#: template for the lightweight agent-selection view.
SELECT_TEMPLATE = Path(__file__).resolve().parent.parent / "web" / "select.html"


DEMO_TRACES = Path(__file__).resolve().parent.parent / "demo" / "traces"
DEMO_FLAGSHIP = "t05_flight_duration"


def _cmd_demo(args: argparse.Namespace) -> int:
    """One command to the first insight: compare the shipped demo pairs,
    write the report page, print the flagship pair's verdict card."""
    import contextlib
    import io
    if not DEMO_TRACES.is_dir():
        print(f"error: demo traces not found at {DEMO_TRACES}", file=sys.stderr)
        return 2
    out_dir = Path(args.output)
    batch_args = argparse.Namespace(tracesdir=str(DEMO_TRACES), output=str(out_dir),
                                    template=None)
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        code = _cmd_batch(batch_args)
    if code != 0:
        sys.stdout.write(quiet.getvalue())
        return code
    flagship = out_dir / f"report_{_safe_name(DEMO_FLAGSHIP)}.json"
    reports = sorted(out_dir.glob("report_*.json"))
    chosen = flagship if flagship.is_file() else (reports[0] if reports else None)
    if chosen is None:
        print("error: the demo batch produced no reports", file=sys.stderr)
        return 2
    report = json.loads(chosen.read_text(encoding="utf-8"))
    from .verdict import format_verdict_card
    print(f"AgentDiff demo — {len(reports)} task pair(s) compared; the flagship pair:")
    print()
    print(format_verdict_card(report.get("verdict_card") or {}))
    print()
    html_path = out_dir / "report.html"
    if html_path.is_file():
        print(f"Report: {html_path.resolve()}  (open it in a browser; every pair is in it)")
        if getattr(args, "open", False):
            import webbrowser
            webbrowser.open(html_path.resolve().as_uri())
    print(f"Per-pair JSON: {out_dir}/report_<task>.json — "
          f"`agentdiff explain {DEMO_TRACES}/{DEMO_FLAGSHIP}__bolt-v3.json` reads one run")
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
        report_path = out_dir / f"report_{_safe_name(task_id)}.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {report_path}")

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


def _load_source(args: argparse.Namespace, attr: str = "tracesdir") -> list[Trajectory]:
    """Trajectories from ``--db FILE`` when given, else from the directory."""
    db_path = getattr(args, "db", None)
    if db_path:
        with TraceDB(db_path) as db:
            filters = {}
            if getattr(args, "db_agent", None):
                filters["agent"] = list(args.db_agent)
            if getattr(args, "db_family", None):
                filters["family"] = args.db_family
            return db.trajectories(**filters)
    directory = getattr(args, attr, None)
    if not directory:
        print("error: give a trace directory or --db FILE", file=sys.stderr)
        return []
    return _load_traces_dir(Path(directory))


def _db_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=None, metavar="FILE",
                        help="read traces from this trace database instead of a directory")
    parser.add_argument("--db-agent", action="append", default=None, metavar="NAME",
                        help="with --db: only these agents (repeatable)")
    parser.add_argument("--db-family", default=None, metavar="FAMILY", help="with --db: only this task family")


def _load_traces_dir(traces_dir: Path) -> list[Trajectory]:
    """Load all valid trajectory JSON files in a directory (sorted, with
    warnings to stderr for invalid ones)."""
    trajectories: list[Trajectory] = []
    for path in sorted(traces_dir.glob("*.json")):
        try:
            trajectories.append(_with_harness(Trajectory.from_json(path), path))
        except ValueError as exc:
            print(f"warning: skipping invalid trace: {exc}", file=sys.stderr)
    return trajectories


def _with_harness(t: Trajectory, path: Path) -> Trajectory:
    """Keep the trace's ``harness`` block (adapter, graded_by, a SYNTHETIC
    note) beside the typed trajectory, so the report's trust section can
    say where the data came from; the typed schema does not carry it."""
    try:
        harness = json.loads(path.read_text(encoding="utf-8")).get("harness")
    except (OSError, ValueError, AttributeError):
        harness = None
    if isinstance(harness, dict):
        t.harness = harness  # type: ignore[attr-defined]
    return t


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
        # A blocked candidate deserves a cause, not just a verdict: each
        # regressed task's pair report already carries an adjudicated
        # diagnosis of the candidate's new failure.
        by_task = {r["task"]["id"]: r for r in reports}
        for tid in regressed:
            diag = (by_task.get(tid) or {}).get("diagnosis") or {}
            if diag.get("mode") == "single_failure":
                print(f"    why {tid}: {diag['verdict']}")
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
    trace_paths: list[Path] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            t = _with_harness(Trajectory.from_json(path), path)
        except ValueError as exc:
            print(f"warning: skipping invalid trace: {exc}", file=sys.stderr)
            continue
        name_run = _run_id_from_name(path)
        if name_run:
            t.run_id = name_run
        trajectories.append(t)
        trace_paths.append(path)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    from .suite import SuiteError, analyse_runs
    try:
        golden = load_golden(args.golden) if getattr(args, "golden", None) else None
        policy = load_policy(args.policy) if getattr(args, "policy", None) else None
        raws = {t.trace_id: json.loads(path.read_text(encoding="utf-8")) for t, path in zip(trajectories, trace_paths)}
        analysed = analyse_runs(trajectories, warn=lambda m: print(f"warning: {m}", file=sys.stderr),
                                golden=golden, policy=policy, raws=raws)
    except (SuiteError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    name_a, name_b = analysed["names"]
    print(f"Agents: A={name_a}  B={name_b}")
    reports, agg = analysed["reports"], analysed["aggregate"]
    stability, reliability_analysis = analysed["stability"], analysed["reliability"]

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

    paired = agg["paired_inference"]
    print(f"Paired inference over {paired['n_pairs']} task(s): "
          f"{paired['labels'][0]} minus {paired['labels'][1]} = {paired['diff']}"
          + (f" ± {paired['se']} (95% {paired['ci95']})" if paired['se'] is not None else "")
          + f"; sign test p={paired['sign_test_p']} — {paired['verdict']}")
    print(f"Runs: {stability['runs_per_agent']}")
    for entry in stability["per_task"]:
        repro = entry["divergence_reproducibility"]
        print(f"  {entry['task']}: A {entry['a']['verdict']} "
              f"B {entry['b']['verdict']} | divergence {repro['verdict']}"
              + (f" ({repro['kind']}, rate {repro['rate']:g})" if repro["kind"] else ""))
    print(stability["narrative"])
    consolidated = agg["diagnosis_consolidated"]
    print("Diagnosis across runs:")
    for entry in consolidated["per_task_agent"]:
        if not entry["failures"]:
            continue
        verdict = entry["consolidated"]
        repro = entry["failure_reproduction"]
        print(f"  {entry['task']} / {entry['agent']} "
              f"(fails {repro['k']} of {repro['n']}): "
              f"[{verdict['status']}] {verdict['statement']}")
        spectrum = entry.get("spectrum") or {}
        if spectrum.get("measurable"):
            for row in spectrum["signatures"][:3]:
                step = row["step"]
                print(f"    spectrum {row['suspiciousness']:.2f}  "
                      f"{step['name']}({step['input'][:50]!r}) — in "
                      f"{row['in_failing']} of {row['of_failing']} failing, "
                      f"{row['in_passing']} of {row['of_passing']} passing "
                      f"run(s)")
        elif spectrum.get("note"):
            print(f"    spectrum: {spectrum['note']}")
    print(f"  {consolidated['narrative']}")
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
                + (f" [{point['ci95'][0]:.2f}, {point['ci95'][1]:.2f}]"
                   if point.get("ci95") else "")
                for point in curve
            ) or "n/a"
            print(f"    {label} {rendered}")
        if row["pass_hat_k"].get("ci95_basis"):
            print(f"    interval: {row['pass_hat_k']['ci95_basis']}")
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


def _cmd_evolve(args: argparse.Namespace) -> int:
    """A self-evolving agent's lineage: the last step's pair as an ordinary
    runs output (report_<task>.json, aggregate.json, report.html) with
    ``aggregate["evolution"]`` attached, and a one-line-per-step summary.
    Exit 0 always — it is a report — unless ``--fail-on`` names a verdict
    or flag some step carries."""
    from .evolve import evolve, fail_on, last_pair, read_lineage
    from .suite import SuiteError, analyse_runs
    lineage = read_lineage(args.lineage, layout=args.layout)
    if not lineage["measurable"]:
        print(f"error: {lineage['reason']}", file=sys.stderr)
        return 2
    for note in lineage["notes"]:
        print(f"warning: {note}", file=sys.stderr)
    names = [n.strip() for n in (args.fail_on or "").split(",") if n.strip()]
    unknown = sorted(set(names) - set(EVOLVE_VERDICTS) - set(EVOLVE_FLAGS))
    if unknown:
        print(f"error: unknown --fail-on name(s): {', '.join(unknown)}; choose from "
              f"{', '.join(EVOLVE_VERDICTS + EVOLVE_FLAGS)}", file=sys.stderr)
        return 2
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # the last step as an ordinary runs batch, so the Story, Evidence and
    # Training views read "what just changed"
    reports: list = []
    agg: dict = {}
    pair = last_pair(lineage)
    if pair is None:
        print("warning: fewer than two generations carry traces; no pair report is written", file=sys.stderr)
    else:
        a, b = pair
        try:
            analysed = analyse_runs(a["trajectories"] + b["trajectories"],
                                    warn=lambda m: print(f"warning: {m}", file=sys.stderr))
            reports, agg = analysed["reports"], analysed["aggregate"]
            print(f"Last step: A={a['policy']}  B={b['policy']}")
        except (SuiteError, ValueError) as exc:
            print(f"warning: the last pair cannot be analysed as a runs batch: {exc}", file=sys.stderr)
    evolution = evolve(lineage, metric=args.metric, samples=args.samples, reports=reports)
    agg["evolution"] = evolution
    # against other lineages: the same output, lineage A primary, plus the
    # four-axis comparison beside it
    against = [a for a in (getattr(args, "against", None) or []) if a]
    comparison = None
    if against:
        from .evolvecompare import compare_lineages
        comparison = compare_lineages([args.lineage] + against, layout=args.layout, metric=args.metric,
                                      samples=args.samples, threshold=getattr(args, "threshold", None),
                                      evolutions=[evolution])
        agg["evolution_compare"] = comparison

    for report in reports:
        path = out_dir / f"report_{_safe_name(report['task']['id'])}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {path}")
    agg_path = out_dir / "aggregate.json"
    agg_path.write_text(json.dumps(agg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {agg_path}")
    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
    if not reports:
        print("warning: no pair report, so report.html is not rendered", file=sys.stderr)
    elif template.is_file():
        try:
            html_path = render_html(reports, agg, template, out_dir / "report.html")
            print(f"Wrote {html_path}")
        except ValueError as exc:
            print(f"warning: could not render HTML: {exc}", file=sys.stderr)
    else:
        print(f"warning: viewer template not found at {template}; skipping report.html", file=sys.stderr)

    _print_evolution(evolution)
    if comparison is not None:
        _print_evolution_compare(comparison)
    hits = fail_on(evolution, names) if names else []
    if hits:
        print("fail-on: " + ", ".join(f"step {i} {name}" for i, name in hits))
        return 1
    return 0


def _print_evolution(ev: dict) -> None:
    """The lineage summary in the CLI voice: one line per step, then best,
    recommended and the advisory."""
    gens = ev["generations"]
    print(f"Lineage: {ev['family'] or '?'}  {len(gens)} generation(s) [{ev['order_basis']}]")
    for g in gens:
        iqm = g["iqm_by_task"]
        band = (f"{iqm['point']:+.2f} [{iqm['lo']:+.2f}, {iqm['hi']:+.2f}]" if iqm["point"] is not None else "n/a")
        print(f"  {g['id']:<6} {g['episodes_n']:>3} episode(s)  passes {g['passes']}/{g['episodes_n']}  "
              f"IQM {band}" + (f"  ({g['reason']})" if g["reason"] else ""))
    if ev["steps"]:
        print("Steps:")
    for s in ev["steps"]:
        e = s["effect"]
        bits = [f"{s['from']} → {s['to']}  {s['mechanism'] or '?'}  {s['diff']['summary']}"]
        if e["measurable"]:
            imp, iqm = e["improvement"], e["iqm"]
            bits.append(f"P(improve) {imp['point']:.2f} [{imp['lo']:.2f}, {imp['hi']:.2f}]")
            bits.append(f"IQM {iqm['delta']:+.1f}")
            bits.append(s["verdict"] + (f": return {s['gaming']['return_delta']:+.2f}, passes "
                                        f"{s['gaming']['pass_delta']:+.2f}" if s["verdict"] == "gamed" else ""))
        else:
            bits.append(f"unmeasurable: {e['reason']}")
        touched = s["diff"]["protected_touched"] + [c["path"] for c in s["protected_episodes"]
                                                    if c["direction"] == "weakened" and c["path"] not in s["diff"]["protected_touched"]]
        if touched:
            bits.append("touched " + ", ".join(touched))
        if s["flags"]:
            bits.append("flags: " + ", ".join(s["flags"]))
        print("  " + " · ".join(bits))
    best, rec = ev["best"], ev["recommended"]
    print(f"Best: {best['id']}" + (f" (task-balanced IQM {best['iqm']:+.2f})" if best.get("iqm") is not None else "")
          + f" — {best['why']}")
    print(f"Recommended: {rec['id']}" + ("" if rec["is_last"] or rec["id"] is None else " (not the last generation)")
          + f" — {rec['why']}")
    tr = ev["trajectory"]
    if tr.get("steps"):
        print(f"Kept on noise: {tr['accepted_on_noise']} of {tr['steps']} step(s)"
              + (f" ({', '.join(tr['noisy_steps'])})" if tr["noisy_steps"] else ""))
    print(f"Integrity: {ev['integrity']['reading']}")
    print(f"Advisory: {ev['advisory']}")


def _print_evolution_compare(cmp: dict) -> None:
    """The comparison in the CLI voice: the four axes, one line each, then
    the race, the process tallies and the verdict."""
    print("Comparison: " + " vs ".join(f"{ln['label']} ({ln['generations_n']} gen, {ln['episodes_n']} ep)"
                                       for ln in cmp["lineages"]))
    if not cmp["measurable"]:
        print(f"  not readable: {cmp['reason']}")
        return
    tasks = cmp["tasks"]
    only = {k: v for k, v in tasks["only"].items() if v}
    print(f"  Tasks: {len(tasks['shared'])} shared" + ("; excluded " + "; ".join(
        f"{k} {', '.join(v)}" for k, v in sorted(only.items())) if only else ""))
    for axis in ("peak", "final"):
        blk = cmp[axis]
        if not blk["measurable"]:
            print(f"  {axis.capitalize():<9}{blk['reason']}")
            continue
        imp, m = blk["improvement"], blk["metric"]
        print(f"  {axis.capitalize():<9}{blk['a']['label']} {blk['a']['id']} vs {blk['b']['label']} {blk['b']['id']}  "
              f"P(b > a) {imp['point']:.2f} [{imp['lo']:.2f}, {imp['hi']:.2f}]  {cmp['metric']} "
              f"{m['a']:+.2f} vs {m['b']:+.2f}  → " + (blk["separates"] or "no separation"))
    race = cmp["race"]
    if race["measurable"]:
        th = race["threshold"]
        reached = ", ".join(f"{k} " + (f"{r['id']} @{r['episodes_cum']} ep" if r else "never")
                            for k, r in sorted(race["reached"].items()))
        print(f"  Learning threshold {th['value']:+.2f} ({th['source']}); reached: {reached}  → "
              + (cmp["verdict"]["learning"] or "no separation"))
    else:
        print(f"  Learning {race['reason']}")
    for label, pr in sorted(cmp["process"].items()):
        if not pr["measurable"]:
            print(f"  Process  {label}: {pr['reason']}")
            continue
        ret = pr["retention"]
        best = pr["best_paying_mechanism"]
        print(f"  Process  {label}: {pr['steps']} step(s)  gamed {pr['gamed']}  forgot {pr['forgot']}  traded "
              f"{pr['traded']}  on-noise {pr['accepted_on_noise']}  protected {pr['protected_touched']}  over-budget "
              f"{pr['over_budget']}  collapsed {pr['collapsed']}  retention "
              f"{len(ret['solved_at_last'])}/{ret['ever_solved_n']}"
              + (f"  best mechanism {best} (n={pr['mechanisms'][best]['steps']}, "
                 f"Δ {pr['mechanisms'][best]['mean_delta']:+.2f})" if best else ""))
    v = cmp["verdict"]
    print(f"  Verdict  peak {v['peak'] or '—'} · final {v['final'] or '—'} · learning {v['learning'] or '—'} · "
          f"process {v['process'] or '—'}  → " + (cmp["verdict"]["process_basis"] if v["process"] else v["process_basis"]))
    print(f"  Advisory: {cmp['advisory']}")


def _cmd_evolve_compare(args: argparse.Namespace) -> int:
    """``evolve-compare A B [C ...]`` is ``evolve A --against B [--against C]``."""
    if len(args.lineages) < 2:
        print("error: evolve-compare needs at least two lineage directories", file=sys.stderr)
        return 2
    args.lineage, args.against = args.lineages[0], list(args.lineages[1:])
    return _cmd_evolve(args)


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

    compare_cmd.register(sub)

    p_demo = sub.add_parser("demo", help="one command to the first insight: compare the "
                                         "shipped demo pairs and write the report page")
    p_demo.add_argument("-o", "--output", default="out_demo",
                        help="directory for the reports and report.html (default: out_demo)")
    p_demo.add_argument("--open", action="store_true",
                        help="open report.html in the default browser")
    p_demo.set_defaults(func=_cmd_demo)

    p_batch = sub.add_parser("batch", help="compare a directory of traces pairwise by task")
    p_batch.add_argument("tracesdir", help="directory of trajectory *.json files")
    p_batch.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_batch.add_argument(
        "--template",
        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})",
    )
    p_batch.add_argument("--golden", default=None, help="golden dataset (tasks JSON with expected_tools, forbidden_tools, …): scores tool correctness and policy")
    p_batch.add_argument("--policy", default=None, help="safety policy JSON (forbidden_tools, forbidden_patterns, max_writes, write_requires_read)")
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
    p_runs.add_argument("--golden", default=None, help="golden dataset (tasks JSON with expected_tools, forbidden_tools, …): scores tool correctness and policy")
    p_runs.add_argument("--policy", default=None, help="safety policy JSON (forbidden_tools, forbidden_patterns, max_writes, write_requires_read)")
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

    progress.register(sub)

    bench.register(sub)

    experiments.register(sub)

    narrate.register(sub)

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

    convert.register(sub)

    frameworks.register(sub)

    rl.register(sub)

    p_evolve = sub.add_parser(
        "evolve", help="a self-evolving agent's lineage (<gen>/agent.json + <gen>/traces/*.json): per step, did it "
                       "help, what changed, and did it game, forget, overfit or touch a protected path; which "
                       "generation to keep; the last step as an ordinary runs output")
    p_evolve.add_argument("lineage", help="lineage directory")
    p_evolve.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_evolve.add_argument("--template", help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    p_evolve.add_argument("--layout", choices=EVOLVE_LAYOUTS, default="native",
                          help="native: <gen>/agent.json + <gen>/traces; flat: one runs directory with agents/<gen>.json")
    p_evolve.add_argument("--metric", choices=("return", "discounted_return", "success", "steps", "seconds"),
                          default="return", help="the score the IQM and the improvement are computed on")
    p_evolve.add_argument("--samples", type=int, default=2000, help="bootstrap resamples per statistic")
    p_evolve.add_argument("--fail-on", default=None,
                          help="comma-separated verdicts or flags (" + ", ".join(EVOLVE_VERDICTS + EVOLVE_FLAGS)
                               + "): exit 1 when any step carries one")
    p_evolve.add_argument("--against", action="append", default=[], metavar="LINEAGE",
                          help="another lineage over the same tasks to compare with, on four axes (peak, final, "
                               "learning, process); repeatable; adds aggregate[\"evolution_compare\"]")
    p_evolve.add_argument("--threshold", type=float, default=None,
                          help="the learning race's threshold on the task-balanced IQM (default: the midpoint "
                               "between the lowest g0 and the highest recommended point, stated in the output)")
    p_evolve.set_defaults(func=_cmd_evolve)

    p_evc = sub.add_parser(
        "evolve-compare", help="two or more self-evolving lineages over the same tasks, compared as processes: "
                               "peak, final, learning and process each get their own verdict "
                               "(alias of: evolve A --against B [--against C])")
    p_evc.add_argument("lineages", nargs="+", help="lineage directories, the first one primary")
    p_evc.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_evc.add_argument("--template", help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    p_evc.add_argument("--layout", choices=EVOLVE_LAYOUTS, default="native",
                       help="native: <gen>/agent.json + <gen>/traces; flat: one runs directory with agents/<gen>.json")
    p_evc.add_argument("--metric", choices=("return", "discounted_return", "success", "steps", "seconds"),
                       default="return", help="the score the IQM and the improvement are computed on")
    p_evc.add_argument("--samples", type=int, default=2000, help="bootstrap resamples per statistic")
    p_evc.add_argument("--fail-on", default=None,
                       help="comma-separated verdicts or flags of the primary lineage: exit 1 when any step carries one")
    p_evc.add_argument("--threshold", type=float, default=None,
                       help="the learning race's threshold on the task-balanced IQM (default: stated midpoint)")
    p_evc.set_defaults(func=_cmd_evolve_compare)

    run.register(sub)

    loop.register(sub)

    replay.register(sub)

    rerun.register(sub)

    checkpoint.register(sub)

    context.register(sub)

    judge.register(sub)

    why.register(sub)

    db.register(sub)

    hook.register(sub)

    eval_.register(sub)

    route.register(sub)

    feedback.register(sub)

    rlexport.register(sub)

    _register_grafana(sub)

    watch.register(sub)

    explain.register(sub)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
