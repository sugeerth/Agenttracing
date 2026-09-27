"""The ``open`` command: the latest duel's page, without remembering where it went.

`agentdiff "the task"` writes to ``duel-out/``, then ``duel-out-2/`` and
on. ``agentdiff open`` finds the newest of them under the directory given
(the current one by default) and opens its page, or prints the path when
there is no browser to open it in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

__all__ = ["register", "run", "latest_page"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("open", help="open the newest duel's page under this directory "
                                                "(duel-out/, duel-out-2/, …)")
    parser.add_argument("where", nargs="?", default=".", metavar="DIR",
                        help="where the duels were run (default: here)")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print the path; open nothing")
    parser.set_defaults(func=run)


def latest_page(where) -> Optional[Path]:
    pages = [p for p in Path(where).glob("duel-out*/page/report.html") if p.is_file()]
    return max(pages, key=lambda p: p.stat().st_mtime_ns) if pages else None


def run(args: argparse.Namespace) -> int:
    page = latest_page(args.where)
    if page is None:
        print(f'no duel under {Path(args.where).resolve()}: run one with agentdiff "the task"', file=sys.stderr)
        return 1
    print(page)
    if not args.print_only and sys.stdout.isatty():
        try:
            import webbrowser
            webbrowser.open(page.resolve().as_uri())
        except Exception:   # noqa: BLE001 — no browser is not an error: the path is printed
            pass
    return 0
