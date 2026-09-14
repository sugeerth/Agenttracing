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

__all__ = ["register", "run", "run_everything", "DEMO_TRACES", "DEMO_FLAGSHIP"]

DEMO_TRACES = Path(__file__).resolve().parents[2] / "demo" / "traces"
DEMO_FLAGSHIP = "t05_flight_duration"


def register(subparsers) -> None:
    parser = subparsers.add_parser("demo", help="one command to the first insight: compare the "
                                                "shipped demo pairs and write the report page")
    parser.add_argument("-o", "--output", default="out_demo",
                        help="directory for the reports and report.html (default: out_demo)")
    parser.add_argument("--open", action="store_true",
                        help="open report.html in the default browser")
    parser.add_argument("--everything", action="store_true",
                        help="the whole demo in one go: the pair batch, the training runs, the self-evolving "
                             "lineage compared with its rival and watched by its eval, packed into one bundle "
                             "with every trace, with the key and the assistant snippet printed")
    parser.set_defaults(func=run)


DEMO_ROOT = DEMO_TRACES.parent
DEMO_TRAIN = DEMO_ROOT / "rl" / "train"
DEMO_LINEAGE = DEMO_ROOT / "evolve" / "lineage"
DEMO_LINEAGE_B = DEMO_ROOT / "evolve" / "lineage_b"


def run_everything(out_dir: Path) -> int:
    """The whole demo in one command: ``batch`` on the pairs, ``runs`` on
    the training set, ``evolve --against`` on the two lineages (which
    carries the eval that evolves with them), then ``bundle --traces``
    over the three with every trace, so every run has its level three.
    Prints what was built, the bundle id, the key, and the snippet that
    gives a coding assistant the bundle over the Model Context Protocol.
    Every step is the ordinary command, run quietly; a failing step
    prints its own output and returns its code."""
    import contextlib
    import io
    from ..cli import main as cli_main
    for needed in (DEMO_TRACES, DEMO_TRAIN, DEMO_LINEAGE, DEMO_LINEAGE_B):
        if not needed.is_dir():
            print(f"error: demo data not found at {needed}", file=sys.stderr)
            return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = [
        ("the pair batch", ["batch", str(DEMO_TRACES), "-o", str(out_dir / "batch")]),
        ("the training runs", ["runs", str(DEMO_TRAIN), "-o", str(out_dir / "runs")]),
        ("the lineage, its rival and its eval",
         ["evolve", str(DEMO_LINEAGE), "--against", str(DEMO_LINEAGE_B), "-o", str(out_dir / "evolve")]),
        ("the bundle with every trace",
         ["bundle", str(out_dir / "batch"), str(out_dir / "runs"), str(out_dir / "evolve"),
          "-o", str(out_dir / "bundle"), "--name", "agentdiff-demo",
          "--traces", str(DEMO_TRACES), str(DEMO_TRAIN), str(DEMO_LINEAGE), str(DEMO_LINEAGE_B)]),
    ]
    for label, argv in steps:
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            code = cli_main(argv)
        if code != 0:
            sys.stdout.write(quiet.getvalue())
            print(f"error: {label} failed (exit {code})", file=sys.stderr)
            return code
        print(f"built {label}: {argv[0]} -> {out_dir / argv[0] if argv[0] != 'evolve' else out_dir / 'evolve'}")
    bundle_dir = out_dir / "bundle"
    manifest = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    key = (bundle_dir / "KEY.txt").read_text(encoding="utf-8").strip()
    totals = ((manifest.get("levels") or {}).get("overview") or {}).get("totals") or {}
    print()
    print(f"Bundle {manifest.get('id')}")
    print(f"  {totals.get('agents')} agents, {totals.get('runs')} runs over {totals.get('tasks')} tasks; "
          f"{totals.get('tokens')} tokens over {totals.get('tokens_runs')} runs; {totals.get('fetches')} fetches")
    print(f"  page: {(bundle_dir / 'report.html').resolve()}  (ten views: Chat, Levels, Data, Story, Evidence, "
          f"Batch, Panels, Training, Evolution, Evals)")
    print(f"  key ({len(key)} bytes): {key}")
    print()
    print("Give a coding assistant the bundle over MCP (stdio, no socket):")
    print(json.dumps({"mcpServers": {"agentdiff": {"command": sys.executable, "args": [
        "-m", "deepcompare", "mcp", "--bundle", str(bundle_dir.resolve())]}}}, indent=2))
    print("then paste: AgentDiff key: " + key[:40] + "… — call overview, then runs, then run for detail.")
    print(f"Or serve it: python -m deepcompare serve --bundle {bundle_dir}   (http://127.0.0.1:8787/)")
    return 0


def run(args: argparse.Namespace) -> int:
    """One command to the first insight: compare the shipped demo pairs,
    write the report page, print the flagship pair's verdict card."""
    import contextlib
    import io
    if not DEMO_TRACES.is_dir():
        print(f"error: demo traces not found at {DEMO_TRACES}", file=sys.stderr)
        return 2
    out_dir = Path(args.output)
    if getattr(args, "everything", False):
        return run_everything(out_dir)
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
