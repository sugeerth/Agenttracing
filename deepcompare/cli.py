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
import sys
from pathlib import Path
from typing import Optional

from .adapters import from_openai_messages, from_otel_genai
from .issues import build_issues, load_suppressions, render_issues_markdown
from .conformance import check_suite, render_conformance_markdown
from .routing import routing_analysis
from .similarity import similarity_analysis
from .fleet import DEFAULT_WEIGHTS, fleet_analysis
from .gate import evaluate_gate, pair_gate_traces, render_gate_markdown
from .metrics import aggregate as build_aggregate, task_signal
from .recommend import recommend
from .report import compare, render_html
from .stability import medoid_pairs, stability_analysis
from .trace import Trajectory

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
    return 0 if gate["verdict"] == "pass" else 1


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
    reports = [compare(a, b) for a, b in medoid_pairs(runs_by_task)]
    agg = build_aggregate(reports)
    agg["stability"] = stability
    agg["task_signal"] = task_signal(reports, stability)

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
    return 0


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
    return 1 if suite["violations"] else 0


def _cmd_convert(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"error: {in_path} is not a file", file=sys.stderr)
        return 2
    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {in_path}: not valid JSON: {exc}", file=sys.stderr)
        return 2

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
    p_gate.set_defaults(func=_cmd_gate)

    p_runs = sub.add_parser(
        "runs", help="multi-run stability analysis over <task>__<agent>__<run>.json traces"
    )
    p_runs.add_argument("runsdir", help="directory of multi-run trajectory *.json files")
    p_runs.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    p_runs.add_argument("--template",
                        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    p_runs.set_defaults(func=_cmd_runs)

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
    p_convert.add_argument("--format", required=True, choices=("otel", "openai"),
                           help="input format")
    p_convert.add_argument("input", help="input JSON file")
    p_convert.add_argument("-o", "--output", default="out",
                           help="output directory (default: out)")
    p_convert.set_defaults(func=_cmd_convert)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
