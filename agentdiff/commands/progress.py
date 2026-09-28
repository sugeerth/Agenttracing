"""The ``progress`` command: two batch outputs compared — which triage
actions resolved, which persist, what newly appeared; ``--strict`` fails
on a regression."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "progress",
        help="compare two batch outputs: which triage actions resolved, "
             "which persist, what newly appeared")
    parser.add_argument("before", help="batch output directory from before the fix")
    parser.add_argument("after", help="batch output directory from after the fix")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when the after-run is worse: "
                             "a broken task, a worsened action, or a new "
                             "issue (persisting is not a regression)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Compare two batch outputs: did the fixes from the first land?"""
    from ..progress import compare_progress
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
    shift = result.get("efficiency_shift") or {}
    if shift.get("available"):
        for name, entry in shift["per_agent"].items():
            cps = entry.get("cost_per_success_usd")
            if cps:
                arrow = "improved" if cps["delta"] < 0 else (
                    "worsened" if cps["delta"] > 0 else "unchanged")
                print(f"  {name}: cost/success ${cps['before']:.5f} -> "
                      f"${cps['after']:.5f} ({arrow})")
    for name, s_ in result["success_by_agent"].items():
        print(f"  {name}: success {s_['before']} -> {s_['after']} on "
              f"{s_['tasks_compared']} shared task(s)"
              + (f"; fixed {', '.join(s_['flips_fixed'])}" if s_['flips_fixed'] else "")
              + (f"; BROKE {', '.join(s_['flips_broken'])}" if s_['flips_broken'] else ""))
        if s_.get("note"):
            print(f"        note: {s_['note']}")
    from ..progress import regressions_in
    regressions = regressions_in(result)
    if regressions:
        print()
        print("  REGRESSIONS (the after-run is worse than the before-run):")
        for finding in regressions:
            print(f"    - {finding}")
    if args.strict and regressions:
        print(f"\n  --strict: failing with {len(regressions)} regression(s)")
        return 1
    return 0
