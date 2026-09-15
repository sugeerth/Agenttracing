"""The ``grafana`` command: AgentDiff's numbers as Prometheus samples.

Reads a batch, runs, fleet or evolve output directory (or one trace) and
writes ``metrics.prom``, ``metrics.json`` and ``metrics.csv`` through
:mod:`deepcompare.grafana`; no server, no network, no number the engine
did not already compute.  The dashboards that read the files live under
``grafana/`` at the repo root (``docs/GRAFANA.md``).
"""

from __future__ import annotations

import argparse
import sys

from ..grafana import export

__all__ = ["register", "run"]


def register(subparsers) -> None:
    """Add the ``grafana`` subcommand to an argparse subparsers action."""
    parser = subparsers.add_parser(
        "grafana", help="AgentDiff's numbers as Prometheus samples for Grafana: metrics.prom for a "
                        "node exporter's textfile collector, metrics.json for the Infinity / JSON "
                        "datasource, metrics.csv; from a batch, runs, fleet or evolve output "
                        "directory, or one trace (per-step series); no server, no network")
    parser.add_argument("target", help="an output directory (aggregate.json + report_*.json, or fleet.json), or a trace JSON")
    parser.add_argument("-o", "--output", default="grafana/out",
                        help="directory for metrics.prom / metrics.json / metrics.csv (default: grafana/out)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Export, print what was written and every note about what could not
    be; 0 on success, 2 when the target cannot be read or the exposition
    does not validate (nothing is written then)."""
    try:
        result = export(args.target, args.output)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for key in ("prom", "json", "csv"):
        print(f"Wrote {result['files'][key]}")
    n = len(result["families"])
    print(f"{result['samples']} sample(s) in {n} metric famil{'y' if n == 1 else 'ies'} "
          f"from the {result['kind']} at {result['source']}")
    for note in result["notes"]:
        print(f"  note: {note}")
    print("  dashboards: grafana/dashboards/*.json; provisioning and a compose file under grafana/ (docs/GRAFANA.md)")
    return 0
