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

from ..issues import load_suppressions
from ..corpus import analyse
from ..forge import load_ledger as load_eval_ledger, write_ledger as write_eval_ledger
from ..lessons import load_ledger, write_ledger
from ..scorecard import load_golden, load_policy
from ..triage import render_triage_text
from ._io import (load_traces, outcomes_from, template_from, write_aggregate,
                  write_page, write_report)
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
    parser.add_argument("--lessons", default=None, metavar="LEDGER",
                        help="lessons ledger JSON: every lesson in it is re-tested on this corpus, the lessons this "
                             "corpus teaches are added, and the file is written back (created when missing)")
    parser.add_argument("--evals", default=None, metavar="LEDGER",
                        help="eval ledger JSON: the suite the forge adopted on earlier corpora is re-tested here, "
                             "this corpus's adopted evals are added, and the file is written back")
    parser.add_argument("--seeds", default=None, metavar="FILE",
                        help="steps a reader marked on the page (\"make this an eval\"), as the JSON the page "
                             "downloads; each becomes a candidate eval")
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

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        golden_set = load_golden(args.golden) if getattr(args, "golden", None) else None
        policy = load_policy(args.policy) if getattr(args, "policy", None) else None
        prior = load_ledger(args.lessons) if getattr(args, "lessons", None) else None
        seeds = _load_seeds(args.seeds) if getattr(args, "seeds", None) else None
        prior_evals = load_eval_ledger(args.evals) if getattr(args, "evals", None) else None
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # the whole analysis is one engine function (`deepcompare.corpus`), the
    # same one the live page runs, so the two pages cannot drift apart
    try:
        result = analyse(
            trajectories, golden=golden_set, policy=policy, outcomes=outcomes_from(traces_dir),
            suppressions=(load_suppressions(traces_dir) or load_suppressions(Path.cwd())),
            lessons_ledger=prior, evals_ledger=prior_evals, seeds=seeds,
            proposer=getattr(args, "forge_proposer", None),
            extra=getattr(args, "extra_aggregate", None),
            on_skip=lambda t: print(f"warning: task {t!r} lacks a trace for both agents; skipped",
                                    file=sys.stderr))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    name_a, name_b = result["agents"]
    print(f"Agents: A={name_a}  B={name_b}")
    reports, agg = result["reports"], result["aggregate"]
    for report in reports:
        write_report(out_dir, report)
    if getattr(args, "lessons", None):
        write_ledger(args.lessons, result["next"]["lessons"])
    if getattr(args, "evals", None):
        write_eval_ledger(args.evals, result["next"]["evals"])
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
    lessons = agg.get("lessons") or {}
    if lessons.get("narrative"):
        print(f"Lessons: {lessons['narrative']}")
    forged = agg.get("forge") or {}
    if forged.get("narrative"):
        print(f"Evals forged: {forged['narrative']}")
    # Printed last because it is the answer to "so which of all that first?" —
    # a reader who stops here has still been told what to do.
    for line in render_triage_text(agg.get("triage") or {}):
        print(line)
    return 0


def _load_seeds(path) -> list:
    """The page's downloaded marks: a list, or ``{"seeds": [...]}``."""
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    seeds = data.get("seeds") if isinstance(data, dict) else data
    if not isinstance(seeds, list):
        raise ValueError(f"{path}: expected a list of seeds or {{\"seeds\": [...]}}")
    return [s for s in seeds if isinstance(s, dict) and s.get("task") and s.get("agent")]
