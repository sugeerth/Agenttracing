"""The ``frameworks`` command, per trace: the framework and protocols
the trace came through, its MCP servers, handoffs and permission
decisions, and the domain its tools point at.  Engine only; nothing is
written."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "frameworks", help="per trace: the agent framework and protocols it came through (MCP servers, "
                           "handoffs, permission decisions) and the domain its tools point at")
    parser.add_argument("target", help="a trace file or a directory of traces")
    parser.add_argument("--signals", action="store_true", help="print the signals behind each verdict")
    parser.add_argument("--json", action="store_true", help="print the full detection as JSON")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Per trace: the framework and protocols the trace came through, its
    MCP servers, handoffs and permission decisions, and the domain its
    tools point at. Engine only; nothing is written."""
    from ..domains import infer as infer_domain
    from ..frameworks import detect as detect_framework, render_line
    target = Path(args.target)
    if target.is_dir():
        paths = sorted(p for p in target.glob("*.json") if not p.name.endswith(".live.json"))
    elif target.is_file():
        paths = [target]
    else:
        print(f"error: {target} is neither a file nor a directory", file=sys.stderr)
        return 2
    rows = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: unreadable ({exc})", file=sys.stderr)
            continue
        if not isinstance(data, dict) or "steps" not in data:
            continue
        det = detect_framework(data)
        dom = infer_domain(data)
        rows.append({"path": str(path), "trace_id": data.get("trace_id"), "framework": det, "domain": dom})
        if not args.json:
            print(render_line(str(path), det, dom))
            if args.signals:
                for sig in det["signals"] + dom["signals"]:
                    print(f"    - {sig}")
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0
