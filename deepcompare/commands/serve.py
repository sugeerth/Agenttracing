"""The ``serve`` command: a bundle behind a read-only local HTTP API and
its page. The server lives in the harness (:mod:`deepcompare.harness.serve`),
the only place a network module may, and is imported inside :func:`run`
so no analysis command loads it."""

from __future__ import annotations

import argparse
import sys

from ..bundle import Bundle

__all__ = ["register", "run"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def register(subparsers) -> None:
    parser = subparsers.add_parser("serve", help="serve a bundle over a read-only local HTTP API (/api/v1/overview, "
                                                "/api/v1/runs, /api/v1/runs/<key>, /api/v1/runs/<key>/fetches, "
                                                "/api/v1/budget, /api/v1/lineage, /api/v1/key, /api/v1/verify) and its "
                                                "page at /; localhost by default")
    parser.add_argument("--bundle", required=True, metavar="DIR", help="the bundle directory (bundle.json inside)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"interface to bind (default: {DEFAULT_HOST}; anything else is warned about)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default: {DEFAULT_PORT}; 0 picks a free one)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        bundle = Bundle(args.bundle)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.host not in LOCAL_HOSTS:
        print(f"warning: binding to {args.host} exposes the bundle beyond this machine; the API is read-only but "
              "the bundle's contents are served to anyone who can reach it", file=sys.stderr)
    from ..harness.serve import make_server
    try:
        server = make_server(bundle, args.host, args.port)
    except OSError as exc:
        print(f"error: cannot bind {args.host}:{args.port}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    host, port = server.server_address[:2]
    print(f"Serving {bundle.name} ({bundle.id}) at http://{host}:{port}/ — /api/v1/overview, /api/v1/runs, "
          f"/api/v1/runs/<key>; Ctrl-C stops")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
