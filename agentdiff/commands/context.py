"""The ``context`` command: what the model saw before a step, rebuilt
from the trace; for a report with ``--row``, both runs' contexts at an
aligned row and their diff.  Hermetic: the harness's context builder is
imported inside the command, and no provider is ever loaded."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "context", help="print what the model saw before a step, rebuilt from the trace — or, for a "
                        "report with --row, both runs' contexts at an aligned row and their diff")
    parser.add_argument("target", help="a trace file or a report_*.json")
    parser.add_argument("--step", type=int, default=None, help="the step (default: the report's decisive step)")
    parser.add_argument("--side", choices=["a", "b"], default=None, help="for a report: which run (default: the diagnosed side)")
    parser.add_argument("--row", type=int, default=None, help="for a report: an alignment row — both runs' contexts and their diff")
    parser.add_argument("--against", default=None, help="a second trace file to diff the context against")
    parser.add_argument("--against-step", type=int, default=None, help="the step in the second trace (default: the same step)")
    parser.add_argument("--diff-only", action="store_true", help="with --row: print only the diff")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Print what the model saw before a step, rebuilt from the trace;
    with a report and --row, both runs' contexts at that aligned row and
    their diff."""
    from ..harness import context as ctx
    path = Path(args.target)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {path}: {exc}", file=sys.stderr)
        return 2
    is_report = isinstance(data, dict) and "alignment" in data and "a" in data and "b" in data
    try:
        if is_report and args.row is not None:
            rows = data.get("alignment") or []
            if not (0 <= args.row < len(rows)):
                raise ValueError(f"row {args.row} is outside the alignment's {len(rows)} rows")
            row = rows[args.row]
            sides = {}
            for side in ("a", "b"):
                idx = row.get(f"{side}_index")
                if idx is not None:
                    sides[side] = int(idx)
            traces = {s: {"agent": data[s]["agent"], "task": data["task"], "steps": data[s]["steps"]} for s in sides}
            for side, idx in sides.items():
                print(f"=== {traces[side]['agent']['name']} ({side}) before step {idx} — {json.dumps(ctx.summary(traces[side], idx))}")
                if not args.diff_only:
                    print(ctx.render(ctx.context_at(traces[side], idx)))
            if len(sides) == 2:
                print("=== diff")
                print(ctx.diff(traces["a"], sides["a"], traces["b"], sides["b"]) or "(the two contexts are identical)\n")
            return 0
        if is_report:
            side = args.side or (data.get("diagnosis") or {}).get("subject") or "a"
            trace = {"agent": data[side]["agent"], "task": data["task"], "steps": data[side]["steps"]}
        else:
            trace = data
        step = args.step
        if step is None:
            dec = ((data.get("diagnosis") or {}).get("decisive_step") or {}) if is_report else {}
            step = dec.get("step")
        if step is None:
            raise ValueError("pass --step N (a report without a decisive step names none)")
        print(f"=== {(trace.get('agent') or {}).get('name', '?')} before step {step} — {json.dumps(ctx.summary(trace, int(step)))}")
        print(ctx.render(ctx.context_at(trace, int(step))))
        if args.against:
            other = json.loads(Path(args.against).read_text(encoding="utf-8"))
            other_step = args.against_step if args.against_step is not None else int(step)
            print("=== diff")
            print(ctx.diff(trace, int(step), other, other_step) or "(the two contexts are identical)\n")
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
