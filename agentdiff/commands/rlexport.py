"""The ``rlexport`` command: the bridge out to a trainer — per-trajectory
rewards for a veRL reward manager, its compute_score template, DPO-style
preference pairs, or per-step transitions for Agent Lightning, from a
report or a directory of reports.  Engine only; every record says
recorded or shaped."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "rlexport", help="the bridge out to an RL trainer: per-trajectory rewards for a veRL "
                         "reward manager, its compute_score template, DPO-style preference pairs, "
                         "or per-step transitions for Agent Lightning, from a report or a "
                         "directory of reports; every record says recorded or shaped")
    parser.add_argument("target", help="a report_*.json, or a directory of them")
    parser.add_argument("--format", default="verl-rewards",
                        choices=("verl-rewards", "verl-reward-fn", "preferences", "agent-lightning"),
                        help="what to write (default: verl-rewards)")
    parser.add_argument("-o", "--output", default=None,
                        help="write here (JSONL; a .py file for verl-reward-fn); default: stdout")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """The bridge out to a trainer: per-trajectory rewards for a veRL reward
    manager, the compute_score template, DPO-style preference pairs, or
    per-step transitions for Agent Lightning, from a report or a directory
    of reports.  Engine only; every record says recorded or shaped."""
    from ..rlexport import FORMATS, export, load_reports, to_jsonl
    try:
        reports = load_reports(args.target)
        payload, count = export(reports, args.format)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = payload if isinstance(payload, str) else to_jsonl(payload)
    unit = {"verl-rewards": "trajectory reward record(s)", "preferences": "preference pair(s)",
            "agent-lightning": "transition(s)", "verl-reward-fn": "compute_score template"}.get(args.format, "record(s)")
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} — {count} {unit} from {len(reports)} report(s)")
    else:
        sys.stdout.write(text)
        print(f"{count} {unit} from {len(reports)} report(s)", file=sys.stderr)
    if args.format == "verl-reward-fn":
        print("  point veRL's custom_reward_function.path at the file; it reads the rewards JSONL "
              "named by AGENTDIFF_REWARDS_JSONL (else agentdiff_rewards.jsonl beside it)")
    return 0
