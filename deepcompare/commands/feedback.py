"""The ``feedback`` command: the loop back — per-step labels, a
preference pair (chosen = the passing run or the reconciled splice,
rejected = the failing run) and prompt suggestions, from a report or a
directory of reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "feedback", help="the loop back: per-step labels, a preference pair "
                         "(chosen = the passing run or the reconciled splice, "
                         "rejected = the failing run) and prompt suggestions, "
                         "from a report or a directory of reports")
    parser.add_argument("target", help="a report_*.json, or a directory of them")
    parser.add_argument("-o", "--output", default=None, help="write the signal JSON here")
    parser.add_argument("--jsonl", default=None,
                        help="write preference pairs as JSONL here (prompt, chosen, rejected)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """The loop back: step labels, preference pairs, prompt suggestions
    from one report or a directory of them."""
    from ..feedback import feedback_signal, to_jsonl
    target = Path(args.target)
    paths = sorted(target.glob("report_*.json")) if target.is_dir() else [target]
    if not paths:
        print(f"error: no report_*.json under {target}", file=sys.stderr)
        return 2
    signals = []
    for path in paths:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            return 2
        signals.append(feedback_signal(report))
    out = Path(args.output) if args.output else None
    payload = signals if target.is_dir() else signals[0]
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    if args.jsonl:
        Path(args.jsonl).parent.mkdir(parents=True, exist_ok=True)
        text = to_jsonl(signals)
        Path(args.jsonl).write_text(text, encoding="utf-8")
        print(f"wrote {args.jsonl} — {text.count(chr(10))} preference pair(s)")
    pairs = sum(1 for s in signals if s["preference_pair"])
    suggestions = sum(len(s["prompt_suggestions"]) for s in signals)
    print(f"{len(signals)} report(s): {pairs} preference pair(s), {suggestions} prompt suggestion(s), "
          f"{sum(len(s['step_labels']) for s in signals)} labelled step(s)")
    for sig in signals:
        for sug in sig["prompt_suggestions"]:
            print(f"  [{sig['task_id']}] {sug['kind']}: {sug['text']}")
    if not out and not args.jsonl:
        print(json.dumps(payload, indent=1, ensure_ascii=False))
    return 0
