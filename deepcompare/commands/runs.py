"""The ``runs`` command: multi-run stability analysis over a directory of
``<task>__<agent>__<run>.json`` traces — the paired inference, per-task
stability, the diagnosis consolidated across runs, and the reliability
block (pass^k, pass@k, the consistency scores and every qualifier that
keeps them honest); the ordinary three artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..scorecard import load_golden, load_policy
from ._io import iter_traces, template_from, write_outputs
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "runs", help="multi-run stability analysis over <task>__<agent>__<run>.json traces"
    )
    parser.add_argument("runsdir", help="directory of multi-run trajectory *.json files")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument("--template",
                        help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--golden", default=None, help="golden dataset (tasks JSON with expected_tools, forbidden_tools, …): scores tool correctness and policy")
    parser.add_argument("--policy", default=None, help="safety policy JSON (forbidden_tools, forbidden_patterns, max_writes, write_requires_read)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runsdir)
    if not runs_dir.is_dir():
        print(f"error: {runs_dir} is not a directory", file=sys.stderr)
        return 2

    # the runs layout names the run in the filename, which overrides the id
    # the file carries; the raw JSON is kept beside each trajectory for the
    # scorecard's sake
    loaded = list(iter_traces(runs_dir, run_ids=True))
    trajectories = [t for _, t in loaded]
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    from ..suite import SuiteError, analyse_runs
    try:
        golden = load_golden(args.golden) if getattr(args, "golden", None) else None
        policy = load_policy(args.policy) if getattr(args, "policy", None) else None
        raws = {t.trace_id: json.loads(path.read_text(encoding="utf-8")) for path, t in loaded}
        analysed = analyse_runs(trajectories, warn=lambda m: print(f"warning: {m}", file=sys.stderr),
                                golden=golden, policy=policy, raws=raws)
    except (SuiteError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    name_a, name_b = analysed["names"]
    print(f"Agents: A={name_a}  B={name_b}")
    reports, agg = analysed["reports"], analysed["aggregate"]
    stability, reliability_analysis = analysed["stability"], analysed["reliability"]

    write_outputs(args.output, reports, agg, template_from(args))

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
