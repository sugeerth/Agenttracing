"""The ``demo`` command: one command to the first insight — the shipped
demo pairs compared as a batch, the report page written, the flagship
pair's verdict card printed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._io import safe_name
from .batch import run as run_batch

__all__ = ["register", "run", "DEMO_TRACES", "DEMO_FLAGSHIP"]

DEMO_TRACES = Path(__file__).resolve().parents[2] / "demo" / "traces"
DEMO_FLAGSHIP = "t05_flight_duration"


def register(subparsers) -> None:
    parser = subparsers.add_parser("demo", help="one command to the first insight: compare the "
                                                "shipped demo pairs and write the report page")
    parser.add_argument("-o", "--output", default="out_demo",
                        help="directory for the reports and report.html (default: out_demo)")
    parser.add_argument("--open", action="store_true",
                        help="open report.html in the default browser")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """One command to the first insight: compare the shipped demo pairs,
    write the report page, print the flagship pair's verdict card."""
    import contextlib
    import io
    if not DEMO_TRACES.is_dir():
        print(f"error: demo traces not found at {DEMO_TRACES}", file=sys.stderr)
        return 2
    out_dir = Path(args.output)
    batch_args = argparse.Namespace(tracesdir=str(DEMO_TRACES), output=str(out_dir),
                                    template=None)
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        code = run_batch(batch_args)
    if code != 0:
        sys.stdout.write(quiet.getvalue())
        return code
    flagship = out_dir / f"report_{safe_name(DEMO_FLAGSHIP)}.json"
    reports = sorted(out_dir.glob("report_*.json"))
    chosen = flagship if flagship.is_file() else (reports[0] if reports else None)
    if chosen is None:
        print("error: the demo batch produced no reports", file=sys.stderr)
        return 2
    report = json.loads(chosen.read_text(encoding="utf-8"))
    from ..verdict import format_verdict_card
    print(f"AgentDiff demo — {len(reports)} task pair(s) compared; the flagship pair:")
    print()
    print(format_verdict_card(report.get("verdict_card") or {}))
    print()
    html_path = out_dir / "report.html"
    if html_path.is_file():
        print(f"Report: {html_path.resolve()}  (open it in a browser; every pair is in it)")
        if getattr(args, "open", False):
            import webbrowser
            webbrowser.open(html_path.resolve().as_uri())
    print(f"Per-pair JSON: {out_dir}/report_<task>.json — "
          f"`agentdiff explain {DEMO_TRACES}/{DEMO_FLAGSHIP}__bolt-v3.json` reads one run")
    return 0
