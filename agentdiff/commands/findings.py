"""The ``findings`` command: the ten findings of docs/FINDINGS.md, checked against your own runs.

    agentdiff findings runs/                     each finding: applies here, clear, or cannot be told
    agentdiff findings runs/ --html out.html     the same as a page
    agentdiff findings runs/ --json              the full result

Folders are searched all the way down, for traces and for self-evolve records. See :mod:`agentdiff.findings`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]

_MARK = {"applies": "!!", "clear": "ok", "cannot tell": "--"}


def register(subparsers) -> None:
    p = subparsers.add_parser("findings", help="check your runs against the ten findings in docs/FINDINGS.md: "
                                               "which apply here, with your numbers and what to do")
    p.add_argument("paths", nargs="+", metavar="PATH", help="trace files or folders (searched all the way down)")
    p.add_argument("--html", default=None, metavar="FILE", help="also write the check as a page")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from ..findings import check_findings, evolution_records, findings_html, load
    runs, records, skipped = load(args.paths)
    if not runs:
        print("error: no traces found in " + ", ".join(args.paths), file=sys.stderr)
        return 2
    result = check_findings(runs, records, evolution_records(args.paths))
    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(findings_html(result, sources=args.paths), encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{result['runs']} run(s) in {len(result['arms'])} arm(s): {result['failed']} failed, "
          f"{result['passed']} passed" + (f", {result['ungraded']} ungraded" if result["ungraded"] else "")
          + (f"; {skipped} file(s) were not traces" if skipped else ""))
    print(result["says"] + "\n")
    for r in result["findings"]:
        print(f" {_MARK[r['status']]} {r['no']:>2}. {r['title']}  [{r['status']}: {r['figure']}]")
        print(f"       {r['yours']}")
        if r["status"] == "applies":
            print(f"       do: {r['do']}")
    if args.html:
        print(f"\nwrote {args.html}")
    return 0
