"""The ``mcp`` command: a bundle served over the Model Context Protocol
on stdio, until EOF (:mod:`deepcompare.mcpserver`). No socket: the client
that spawns it owns both pipes."""

from __future__ import annotations

import argparse
import sys

from ..bundle import Bundle
from ..mcpserver import Server

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("mcp", help="serve a bundle over the Model Context Protocol on stdio (JSON-RPC, one "
                                              "message per line, until EOF): tools overview, runs, run, fetches, budget, "
                                              "lineage, key, verify; the bundle's files as resources")
    parser.add_argument("--bundle", required=True, metavar="DIR", help="the bundle directory (bundle.json inside)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        bundle = Bundle(args.bundle)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return Server(bundle).serve(sys.stdin, sys.stdout)
