"""The ``narrate`` command: emit a narration prompt for a report, or
ingest a model's answer — checked number by number against the brief and
stored as labelled commentary that no analysis reads.  The engine never
calls a model here; ``why`` is the command that does."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "narrate",
        help="emit an LLM narration prompt for a report, or ingest the answer "
             "(checked against the evidence; commentary only)")
    parser.add_argument("report", help="a report_*.json produced by batch/compare")
    parser.add_argument("--ingest", metavar="FILE",
                        help="narration text to attach ('-' for stdin); "
                             "omit to print the prompt")
    parser.add_argument("--model", default="unspecified",
                        help="model name recorded as provenance")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Emit a narration prompt for a report, or ingest a model's answer.

    The engine never calls a model. Emit mode prints the prompt; the user
    pipes it through any LLM they like and hands the text back with
    --ingest, where it is checked number-by-number against the brief and
    stored as labelled commentary that no analysis reads.
    """
    from ..narrate import (check_narration, ingest_narration, narration_brief,
                           narration_prompt)
    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"error: {report_path} is not a file", file=sys.stderr)
        return 2
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {report_path}: {exc}", file=sys.stderr)
        return 2
    brief = narration_brief(report)

    if args.ingest is None:
        print(narration_prompt(brief))
        return 0

    text = (sys.stdin.read() if args.ingest == "-" else
            Path(args.ingest).read_text(encoding="utf-8"))
    ingest_narration(report, text, brief=brief, model=args.model)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    check = report["narration"]["faithfulness"]
    print(f"narration stored in {report_path} (model: {args.model})")
    print(f"  numbers checked: {check['numbers_checked']}  "
          f"citations: {check['citations']}")
    if check["faithful"]:
        print("  faithful: every number and citation traces to the brief")
    else:
        if check["unsupported_numbers"]:
            print(f"  UNSUPPORTED numbers (not in the evidence): "
                  f"{', '.join(check['unsupported_numbers'])}")
        if check["invalid_citations"]:
            print(f"  INVALID citations: {', '.join(check['invalid_citations'])}")
        print("  stored anyway, flagged — the reader sees the warning, "
              "and no analysis reads narration either way")
    print(f"  note: {check['limit']}")
    return 0
