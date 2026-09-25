"""The ``bundle`` command: one or more output directories packed into a
self-contained, content-addressed bundle with the three-level index, the
page and the key (:mod:`deepcompare.bundle`). Prints the id, the member
table, the level-1 totals and the key; nothing here computes a number
the members did not already carry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .._text import num, pct
from ..bundle import read_member, write_bundle
from ._io import template_from
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "bundle", help="pack output directories (batch, runs, evolve, coevolve, fleet) into one content-addressed "
                       "bundle: bundle.json with the three levels, the members' copies, runs/<key>.json per run, "
                       "report.html and KEY.txt; same inputs, same bytes, same id")
    parser.add_argument("outdirs", nargs="+", metavar="OUT_DIR",
                        help="an output directory (aggregate.json + report_*.json, or fleet.json); the first is the page's primary")
    parser.add_argument("-o", "--output", required=True, metavar="BUNDLE_DIR", help="where to write the bundle")
    parser.add_argument("--name", default=None, help="the bundle's name (default: the members' names joined with +)")
    parser.add_argument("--token-cap", type=int, default=None, metavar="N",
                        help="a token cap: the overview lists the runs over it (default: none given)")
    parser.add_argument("--locator", action="append", default=[], metavar="URL_OR_PATH",
                        help="where the bundle will live, carried in bundle.json and the key (repeatable)")
    parser.add_argument("--traces", nargs="+", action="extend", default=[], metavar="DIR",
                        help="the source traces (repeatable): every run whose steps are not in the output is completed "
                             "from the trace matched by trace_id, else by task, agent and run id, and the file is copied "
                             "under traces/<member>/ so the bundle stays self-contained; without it nothing changes")
    parser.add_argument("--template", help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    members = []
    for path in args.outdirs:
        try:
            members.append(read_member(path))
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    try:
        info = write_bundle(members, Path(args.output), template_from(args), name=args.name,
                            token_cap=args.token_cap, locators=args.locator, traces=args.traces)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.output)
    print(f"Wrote {out / 'bundle.json'}")
    print(f"Bundle {info['name']}: {info['id']}")
    print(f"{'#':>2}  {'member':<24} {'kind':<9} {'tasks':>5} {'agents':>6} {'runs':>5}  lineage")
    for m in info["members"]:
        print(f"{m['index']:>2}  {m['label'][:24]:<24} {m['kind']:<9} {len(m['tasks']):>5} {len(m['agents']):>6} {m['runs']:>5}  {m['lineage'] or '—'}")
    t = info["overview"]["totals"]
    print(f"Level 1: {t['agents']} agent(s), {t['tasks']} task(s), {t['runs']} run(s); "
          f"tokens {num(t['tokens'])} over {t['tokens_runs']} run(s); cost "
          + (f"{num(t['cost_usd'], 4)} USD over {t['cost_runs']} run(s)" if t["cost_usd"] is not None else "not recorded")
          + f"; fetches {num(t['fetches'])} over {t['fetches_runs']} run(s); SYNTHETIC share {pct(t['synthetic_share'])}")
    print(f"  {info['overview']['reading']}")
    print(f"Level 2: {len(info['runs'])} row(s); level 3: {len(info['run_index'])} record(s) under {out / 'runs'}")
    tr = info["traces"]
    if tr["dirs"]:
        whole = sum(1 for r in info["records"].values() if r.get("measurable"))
        print(f"Traces: {tr['read']} read under {len(tr['dirs'])} dir(s); {tr['completed']} record(s) completed from "
              f"{len(tr['files'])} file(s) copied under {out / 'traces'}; {whole} of {len(info['records'])} record(s) hold their steps")
        for note in tr["notes"]:
            print(f"  skipped: {note}")
    print(f"Wrote {out / 'report.html'}")
    print(f"Key ({len(info['key'])} bytes, {out / 'KEY.txt'}):")
    print(info["key"])
    return 0
