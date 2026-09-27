"""The ``watch`` command: serve the report page live over a trace
directory on localhost — running agents stream in step by step, finished
pairs become the full story.  The one command that opens a socket; the
harness's server is imported inside the command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "watch", help="serve the report page live over a trace directory: "
                      "running agents (recorder stream=True) stream in step by "
                      "step, finished pairs become the full story (localhost)")
    parser.add_argument("tracesdir", nargs="?", default=None,
                        help="directory the recorder writes to (with --demo: a scratch "
                             "directory, default a temporary one)")
    parser.add_argument("--demo", default=None, metavar="TRACES",
                        help="replay these traces as if their agents were running now")
    parser.add_argument("--pace", type=float, default=0.4, help="demo: seconds between steps (default 0.4)")
    parser.add_argument("--loop", action="store_true", help="demo: start over when done")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to bind (default 127.0.0.1); anything else needs --allow-remote")
    parser.add_argument("--allow-remote", action="store_true",
                        help="serve beyond this machine; every request then needs the random token printed once")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--poll", type=float, default=0.5, help="directory poll interval in seconds")
    parser.add_argument("--template", default=None, help=f"viewer template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    parser.add_argument("--golden", default=None,
                        help="golden dataset, as for batch: the live page scores tool correctness and milestones")
    parser.add_argument("--policy", default=None, help="safety policy JSON, as for batch")
    parser.add_argument("--db", default=None, help="also ingest every finished trace into this trace database")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Serve the report page live over a trace directory (localhost)."""
    from ..harness.watch import bind_policy, clear_demo_dir, serve
    import threading
    template = Path(args.template) if args.template else DEFAULT_TEMPLATE
    if not template.is_file():
        print(f"error: template {template} not found", file=sys.stderr)
        return 2
    traces = Path(args.tracesdir) if args.tracesdir else None
    demo = None
    if args.demo:
        demo = Path(args.demo)
        if not demo.is_dir():
            print(f"error: {demo} is not a directory", file=sys.stderr)
            return 2
        traces = clear_demo_dir(traces) if traces else clear_demo_dir(None)
    if traces is None:
        print("error: give a trace directory, or --demo <traces-to-replay>", file=sys.stderr)
        return 2
    try:
        token = bind_policy(args.host, getattr(args, "allow_remote", False))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from ..scorecard import load_golden, load_policy
    try:
        golden_set = load_golden(args.golden) if getattr(args, "golden", None) else None
        policy = load_policy(args.policy) if getattr(args, "policy", None) else None
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    stop = threading.Event()
    server = serve(traces, template, host=args.host, port=args.port, poll=args.poll,
                   demo=demo, pace=args.pace, loop=args.loop, stop=stop, quiet=not args.verbose,
                   db=getattr(args, "db", None), token=token, golden=golden_set, policy=policy)
    host, port = server.server_address[:2]
    print(f"watching {traces} — open http://{host}:{port}/" + (f"?token={token}" if token else "")
          + "  (Ctrl-C to stop)")
    if demo:
        print(f"demo: replaying {demo} one step every {args.pace}s" + (" in a loop" if args.loop else ""))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown_all()
    return 0
