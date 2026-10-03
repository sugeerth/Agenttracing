"""The ``hub`` command: AgentDiff as a small platform, one process, no database.

    agentdiff hub                      every run under this directory, behind a sign-in
    agentdiff hub ~/work --port 8790   another root
    agentdiff hub --add-user alice     add a user (password asked, or $AGENTDIFF_HUB_NEW_PASSWORD)
    agentdiff hub --claude-code        your Claude Code sessions on this machine, live as you work

It lists the duels, reports, eval suites, traces and in-band telemetry
under its root, shows each (a trace as its loop, lap by lap), follows
running agents live, and takes telemetry agents post to it. The demo account
(``demo``/``demo`` unless the settings say otherwise) exists on this machine
only, unless ``--demo`` turns it on elsewhere; ``--no-demo`` turns it off.
Settings: defaults, then ``.agentdiff-hub/hub.json``, then
``AGENTDIFF_HUB_*``, then these flags.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Optional

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("hub", help="serve every run under a directory behind a sign-in (demo account "
                                               "on this machine), and take in-band telemetry from agents")
    parser.add_argument("root", nargs="?", default=None, help="the directory to serve (default: here; with "
                                                             "--claude-code or --examples, ~/.agentdiff)")
    parser.add_argument("--host", default=None, help="address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="port (default 8790; 0 picks a free one)")
    parser.add_argument("--config", default=None, metavar="FILE", help="settings file (default .agentdiff-hub/hub.json)")
    parser.add_argument("--allow-remote", action="store_true", help="serve beyond this machine")
    demo = parser.add_mutually_exclusive_group()
    demo.add_argument("--demo", dest="demo", action="store_true", default=None, help="the demo account, anywhere")
    demo.add_argument("--no-demo", dest="demo", action="store_false", help="no demo account")
    parser.add_argument("--add-user", default=None, metavar="NAME", help="add or reset a user, then exit")
    parser.add_argument("--remove-user", default=None, metavar="NAME", help="remove a user, then exit")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    parser.add_argument("--examples", action="store_true",
                        help="serve the example runs a downloaded build carries (long runs, a loop, a duel, RL)")
    parser.add_argument("--claude-code", action="store_true",
                        help="read your Claude Code sessions (~/.claude/projects, or $CLAUDE_CONFIG_DIR/projects) "
                             "into ~/.agentdiff/claude-code and keep following them; nothing leaves this machine")
    parser.add_argument("--open", action="store_true", help="open the hub in the browser once it listens")
    parser.add_argument("--export", default=None, metavar="DIR",
                        help="write every page as static files into DIR (for a private static host), then exit")
    parser.add_argument("--title", default=None, help="--export: the hub's name on its pages")
    parser.add_argument("--bare-index", action="store_true",
                        help="--export: write index.html without its document shell, for a host that adds one")
    parser.set_defaults(func=run)


def _users(args, config) -> int:
    from ..hub.auth import JsonUserStore, User, hash_password, valid_name
    store = JsonUserStore(config.state_dir / "users.json")
    if args.remove_user:
        store.delete(args.remove_user)
        print(f"removed {args.remove_user} (if it existed)")
        return 0
    name = args.add_user
    if not valid_name(name):
        print("error: a user name is letters, digits and . _ @ - only, up to 64", file=sys.stderr)
        return 2
    password = os.environ.get("AGENTDIFF_HUB_NEW_PASSWORD")
    if password is None:
        if not sys.stdin.isatty():
            print("error: no terminal to ask for a password; set AGENTDIFF_HUB_NEW_PASSWORD", file=sys.stderr)
            return 2
        password = getpass.getpass(f"password for {name}: ")
        if password != getpass.getpass("again: "):
            print("error: the passwords differ", file=sys.stderr)
            return 2
    if len(password) < 8:
        print("error: a password of at least 8 characters", file=sys.stderr)
        return 2
    store.put(User(name=name, password_hash=hash_password(password, config.password_iterations)))
    print(f"user {name} saved in {store.path}")
    return 0


def _home() -> "Path":
    from pathlib import Path
    return Path(os.environ.get("AGENTDIFF_HOME") or Path.home() / ".agentdiff")


def _sessions(dest) -> tuple:
    """The newest sessions read now, so the first page has them; the rest follow in the background."""
    from ..claude_sessions import Follower, find, projects_dir, sync
    where = projects_dir()
    found = find(where)
    if not found:
        return None, f"no Claude Code sessions in {where} yet: they appear here as you work"
    print(f"reading your newest Claude Code sessions from {where} …", flush=True)
    first = sync(dest, projects=where, limit=3)
    follower = Follower(dest, projects=where).start()
    more = len(found) - first["found"]
    return follower, (f"{len(found)} Claude Code session(s) from {where}"
                      + (f", the newest {first['found']} read, {more} more on the way" if more > 0 else "")
                      + "; the ones running now follow live")


def _examples() -> Optional[str]:
    """The example runs a build carries, copied once to a folder the hub may write to."""
    import shutil
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "_examples"
    home = Path(os.environ.get("AGENTDIFF_EXAMPLES") or _home() / "examples")
    if not home.is_dir():
        if not src.is_dir():
            return None
        shutil.copytree(src, home)
    return str(home)


def run(args: argparse.Namespace) -> int:
    from ..hub.config import load
    from ..harness.watch import is_loopback
    if args.examples:
        root = _examples()
        if root is None:
            print("error: this install carries no examples; a downloaded build does (packaging/build.py), or pass "
                  "a directory of runs", file=sys.stderr)
            return 2
        args.root = root
    if args.root is None:
        args.root = str(_home()) if args.claude_code else "."
        if args.claude_code:
            _home().mkdir(parents=True, exist_ok=True)
    try:
        config = load(args.root, file=args.config,
                      overrides={"host": args.host, "port": args.port, "demo": args.demo})
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.add_user or args.remove_user:
        return _users(args, config)
    if args.export:
        from ..hub.export import export
        counts = export(args.root, args.export, bare_index=args.bare_index, title=args.title)
        print(f"wrote {counts['pages']} page(s) to {args.export}; open {os.path.join(args.export, 'login.html')} "
              f"(demo / demo)")
        return 0
    if not is_loopback(config.host) and not args.allow_remote:
        print(f"error: refusing to serve on {config.host}: the hub shows every run under {config.root}, "
              "including what the agents read. Bind to 127.0.0.1, or pass --allow-remote.", file=sys.stderr)
        return 2
    from ..harness.hub_server import TracePoller, build_app, make_server, running_hub
    chosen = args.port is not None or "AGENTDIFF_HUB_PORT" in os.environ
    if not chosen:
        there = running_hub(config.host, config.port)
        if there:
            # a second start (a second double-click): the hub is already up, so show it
            print(f"AgentDiff is already running at {there}/" + ("; opening it" if args.open else ""))
            if args.open:
                import webbrowser
                webbrowser.open(f"{there}/")
            return 0
    app = build_app(config)
    follower, mine = None, None
    if args.claude_code:
        dest = _home() / "claude-code"
        app.traces.add_root(dest)
        follower, mine = _sessions(dest)
    try:
        server = make_server(app, quiet=not args.verbose)
    except OSError as exc:
        if chosen:
            print(f"error: cannot listen on {config.host}:{config.port}: {exc}", file=sys.stderr)
            return 2
        # the usual port is taken by something else: any free one will do
        from dataclasses import replace
        taken = config.port
        app.config = config = replace(config, port=0)
        try:
            server = make_server(app, quiet=not args.verbose)
        except OSError as again:
            print(f"error: cannot listen on {config.host}: {again}", file=sys.stderr)
            return 2
        print(f"port {taken} is in use; listening on {server.server_address[1]} instead")
    host = config.host if ":" not in config.host else f"[{config.host}]"
    app.public_url = f"http://{host}:{server.server_address[1]}"
    demo = app.demo_account()
    print(f"AgentDiff hub: {app.public_url}/   (root {os.path.abspath(config.root)})")
    print(f"  sign in: {demo[0]} / {demo[1]}   (demo account; --no-demo turns it off)" if demo else
          f"  sign in with a user from {config.state_dir / 'users.json'} (agentdiff hub --add-user NAME)")
    print(f"  agents post telemetry: AGENTDIFF_HUB={app.public_url} AGENTDIFF_HUB_TOKEN={app.ingest_token}")
    if mine:
        print(f"  yours: {mine}")
    print(f"  live: {app.public_url}/live follows every run under the root as it goes")
    print("  Ctrl-C to stop", flush=True)
    if args.open:
        import threading
        import webbrowser
        threading.Timer(0.6, webbrowser.open, args=(f"{app.public_url}/login",)).start()
    poller = TracePoller(app.traces, app.bus, config.live_poll_s).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        if follower:
            follower.stop()
        poller.stop()
        server.server_close()
    return 0
