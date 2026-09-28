"""The ``gate`` command: a candidate agent's traces regression-gated
against a baseline — success drop, cost and latency rise, new failure
modes — with the CI artifacts and the exit-code policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..gate import evaluate_gate, pair_gate_traces, render_gate_markdown
from ..report import compare
from ._common import add_ci_args, emit_ci
from ._io import load_traces

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "gate", help="regression-gate a candidate agent's traces against a baseline"
    )
    parser.add_argument("baseline", help="directory of baseline agent traces")
    parser.add_argument("candidate", help="directory of candidate agent traces")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument("--markdown", help="also write a Markdown summary (path, relative to -o)")
    parser.add_argument("--max-success-drop", type=float, default=0.0,
                        help="max allowed success-rate drop (default 0)")
    parser.add_argument("--max-cost-increase", type=float, default=0.10,
                        help="max allowed relative mean-cost rise (default 0.10)")
    parser.add_argument("--max-latency-increase", type=float, default=0.25,
                        help="max allowed relative mean-latency rise (default 0.25)")
    parser.add_argument("--allow-new-failure-modes", action="store_true",
                        help="do not fail the gate on new failure-origin categories")
    add_ci_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    base_dir = Path(args.baseline)
    cand_dir = Path(args.candidate)
    for d in (base_dir, cand_dir):
        if not d.is_dir():
            print(f"error: {d} is not a directory", file=sys.stderr)
            return 2

    baseline = load_traces(base_dir)
    candidate = load_traces(cand_dir)
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
    return emit_ci(args, gate, out_dir, reports=reports, trace_dir=cand_dir)
