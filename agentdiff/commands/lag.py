"""The ``lag`` command: when each run's code first became correct, and what the run did after.

    agentdiff lag runs/            each run rebuilt edit by edit and graded after every edit, and the totals
    agentdiff lag runs/ --json     every run, every grade
    agentdiff lag runs/ --html f   the same as a page

Needs the harness's record beside each trace (``records/<name>.json``, written by ``duel``, ``fix`` and
``self-evolve``) and the task's workspace and check in this repository. See :mod:`agentdiff.lag`.
"""

from __future__ import annotations

import argparse
import json
import sys

__all__ = ["register", "run"]


def register(subparsers) -> None:
    p = subparsers.add_parser("lag", help="when each run's code first passed its held-out check, rebuilt edit by "
                                          "edit, and what the run did after: steps, tokens, edits that undid it")
    p.add_argument("paths", nargs="+", metavar="PATH", help="folders of runs (traces/ beside records/)")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--html", default=None, metavar="FILE", help="also write it as a page: every passing run before "
                                                                 "and after its fix, and each run's progress")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from ..lag import lag_over
    result = lag_over(args.paths)
    if not result["runs"]:
        print("error: no run with a record beside its trace under " + ", ".join(args.paths), file=sys.stderr)
        return 2
    if args.html:
        from pathlib import Path
        from ..lagreport import lag_html
        Path(args.html).parent.mkdir(parents=True, exist_ok=True)
        Path(args.html).write_text(lag_html(result, sources=args.paths), encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for line in result["says"]:
        print(line)
    for r in result["set_aside"][:5]:
        print(f"  set aside: {r['trace']}: {r['why']}")
    return 0
