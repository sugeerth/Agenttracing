"""The ``telemetry`` command: in-band agent telemetry from the shell.

    agentdiff telemetry wrap -- pytest -q      a hop for any command, no code changed
    agentdiff telemetry decode VECTOR          the hops, as a table (or --json)
    agentdiff telemetry trace VECTOR -o t.json the run as a SCHEMA trace every command reads
    agentdiff telemetry fields                 what a hop can carry
    agentdiff telemetry send VECTOR            post it to a hub ($AGENTDIFF_HUB, $AGENTDIFF_HUB_TOKEN)
    agentdiff telemetry encode TRACE.json      any trace AgentDiff reads, as a vector

``VECTOR`` is the text form (``adi1.…``), a file holding one, or ``-`` for
stdin; with none, ``$AGENTDIFF_INT``.

``wrap`` is how a tool joins the path without being touched: when the
command runs with a vector in its environment, ``wrap`` stamps one hop
(the command's name, how long it took, its exit status, the bytes it
printed) and passes the extended vector back. With no vector it only
runs the command. Either way its output and exit code are the command's.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("telemetry", help="in-band agent telemetry: stamp a hop for any command "
                                                     "(wrap), read a vector back (decode, trace), list the fields")
    sub = parser.add_subparsers(dest="action", required=True)
    w = sub.add_parser("wrap", help="run a command as one hop on the vector in $AGENTDIFF_INT")
    w.add_argument("--name", default=None, help="the hop's tool name (default: the command's)")
    w.add_argument("--kind", default="tool_call", help="the step type (default tool_call)")
    w.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the command")
    d = sub.add_parser("decode", help="the hops of a vector, as a table")
    d.add_argument("vector", nargs="?", default=None)
    d.add_argument("--json", action="store_true", help="rows and summary as JSON")
    t = sub.add_parser("trace", help="a vector as a SCHEMA trace")
    t.add_argument("vector", nargs="?", default=None)
    t.add_argument("-o", "--output", default=None, metavar="FILE", help="default: stdout")
    t.add_argument("--prompt", default="")
    t.add_argument("--answer", default="")
    t.add_argument("--success", choices=("true", "false"), default=None)
    sub.add_parser("fields", help="every field a hop can carry, by bit")
    en = sub.add_parser("encode", help="a SCHEMA trace as a vector (content dropped, sizes kept)")
    en.add_argument("trace", help="a trace JSON file")
    s = sub.add_parser("send", help="post a vector to an agentdiff hub")
    s.add_argument("vector", nargs="?", default=None)
    s.add_argument("--hub", default=None, help="the hub's URL (default $AGENTDIFF_HUB)")
    s.add_argument("--prompt", default="")
    s.add_argument("--answer", default="")
    s.add_argument("--success", choices=("true", "false"), default=None)
    parser.set_defaults(func=run)


def _vector_text(arg) -> str:
    from ..telemetry import ENV_VECTOR
    from ..telemetry.wire import PREFIX
    if arg is None:
        text = os.environ.get(ENV_VECTOR, "")
    elif arg == "-":
        text = sys.stdin.read()
    elif arg.startswith(PREFIX):
        # a vector given inline: it may be far longer than any file name
        text = arg
    else:
        try:
            text = Path(arg).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"{arg[:60]}: neither a vector ({PREFIX}…) nor a readable file ({exc.strerror})")
    if not text.strip():
        raise ValueError(f"no vector given, and ${ENV_VECTOR} is empty")
    return text.strip()


def _wrap(args) -> int:
    from ..telemetry import attach, subprocess_env
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        print("error: give the command after --", file=sys.stderr)
        return 2
    probe = attach(at_exit=False)
    code = 127
    with probe.hop(args.name or Path(cmd[0]).name, kind=args.kind, args=" ".join(cmd[1:])) as hop:
        # the command gets its own hand-off: if it stamps hops too, they
        # nest inside this one rather than being overwritten by it
        with subprocess_env(probe) as env:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env)
            except FileNotFoundError:
                hop.fail("error")
                print(f"error: {cmd[0]}: not found", file=sys.stderr)
            else:
                printed = 0
                # pass the output through as it comes, counting it
                for chunk in iter(lambda: proc.stdout.read1(65536), b""):
                    printed += len(chunk)
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.flush()
                code = proc.wait()
                hop.output_bytes(printed)
                if code != 0:
                    hop.fail("error")
    probe.flush()
    return code


def _send(args, text: str) -> int:
    from ..harness.hub_server import send
    url = args.hub or os.environ.get("AGENTDIFF_HUB")
    token = os.environ.get("AGENTDIFF_HUB_TOKEN")
    if not url or not token:
        print("error: set AGENTDIFF_HUB (or --hub) and AGENTDIFF_HUB_TOKEN; `agentdiff hub` prints both",
              file=sys.stderr)
        return 2
    success = None if args.success is None else args.success == "true"
    try:
        status, body = send(url, token, text, prompt=args.prompt, answer=args.answer, success=success)
    except OSError as exc:
        print(f"error: {url}: {exc}", file=sys.stderr)
        return 2
    if status != 201:
        print(f"error: the hub said {status}: {body.get('error') or body}", file=sys.stderr)
        return 1
    print(f"sent: run {body['id']}, {body['hops']} hop(s) ({'kept' if body['kept'] == 'this' else 'an earlier, longer copy kept'})")
    return 0


def _table(rows: list, info: dict) -> str:
    cols = [c for c in ("hop", "start", "latency", "tool", "kind", "status", "bytes_in", "bytes_out",
                        "tokens_in", "tokens_out", "node") if any(c in r for r in rows)]
    def cell(r, c):
        v = r.get(c)
        if isinstance(v, float):
            return f"{v:.3f}"
        return "" if v is None else str(v)
    body = [[cell(r, c) for c in cols] for r in rows]
    widths = [max(len(c), *(len(b[i]) for b in body)) if body else len(c) for i, c in enumerate(cols)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths))]
    lines += ["  ".join(x.ljust(w) for x, w in zip(b, widths)) for b in body]
    tail = (f"{info['hops']} hop(s) across {len(info['nodes'])} process(es), {info['span_s']:.3f}s, "
            f"{info['errors']} not ok")
    if info["dropped"]:
        tail += f"; {info['dropped']} hop(s) refused at the hop budget"
    if info["clamped"]:
        tail += "; some values were clamped to a 32-bit word"
    return "\n".join(lines + ["", tail])


def run(args: argparse.Namespace) -> int:
    from ..telemetry import FIELDS, WireError, rows, summary, to_trajectory
    if args.action == "wrap":
        return _wrap(args)
    if args.action == "encode":
        from ..telemetry import encode, from_trajectory, to_text
        try:
            print(to_text(encode(from_trajectory(json.loads(Path(args.trace).read_text(encoding="utf-8"))))))
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.action == "fields":
        for f in FIELDS:
            print(f"{f.bit:>2}  {f.name:<11} {f.doc}")
        return 0
    try:
        text = _vector_text(args.vector)
        if args.action == "send":
            return _send(args, text)
        if args.action == "decode":
            rs, info = rows(text), summary(text)
            print(json.dumps({"summary": info, "rows": rs}, indent=1) if args.json else _table(rs, info))
            return 0
        success = None if args.success is None else args.success == "true"
        traj = to_trajectory(text, prompt=args.prompt, answer=args.answer, success=success)
    except (ValueError, WireError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data = json.dumps(traj, indent=1, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(data, encoding="utf-8")
        print(f"wrote {args.output}: {len(traj['steps']) - 1} hop(s) as steps")
    else:
        print(data)
    return 0
