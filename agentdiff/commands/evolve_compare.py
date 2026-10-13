"""The ``evolve-compare`` command: two or more self-evolving lineages
over the same tasks, compared as processes — peak, final, learning and
process each get their own verdict.  An alias of
``evolve A --against B [--against C]``: it reshapes its arguments and
runs :mod:`.evolve`."""

from __future__ import annotations

import argparse
import sys

from ..evolve import LAYOUTS as EVOLVE_LAYOUTS
from .evolve import run as run_evolve
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "evolve-compare", help="two or more self-evolving lineages over the same tasks, compared as processes: "
                               "peak, final, learning and process each get their own verdict "
                               "(alias of: evolve A --against B [--against C])")
    parser.add_argument("lineages", nargs="+", help="lineage directories, the first one primary")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument("--template", help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--layout", choices=EVOLVE_LAYOUTS, default="native",
                        help="native: <gen>/agent.json + <gen>/traces; flat: one runs directory with agents/<gen>.json")
    parser.add_argument("--metric", choices=("return", "discounted_return", "success", "steps", "seconds"),
                        default="return", help="the score the IQM and the improvement are computed on")
    parser.add_argument("--samples", type=int, default=2000, help="bootstrap resamples per statistic")
    parser.add_argument("--fail-on", default=None,
                        help="comma-separated verdicts or flags of the primary lineage: exit 1 when any step carries one")
    parser.add_argument("--threshold", type=float, default=None,
                        help="the learning race's threshold on the task-balanced IQM (default: stated midpoint)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """``evolve-compare A B [C ...]`` is ``evolve A --against B [--against C]``."""
    if len(args.lineages) < 2:
        print("error: evolve-compare needs at least two lineage directories", file=sys.stderr)
        return 2
    args.lineage, args.against = args.lineages[0], list(args.lineages[1:])
    return run_evolve(args)
