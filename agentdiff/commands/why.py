"""The ``why`` command: narrate a report through a model provider —
commentary checked number by number, read by no analysis.  Talks to a
network unless the provider is scripted; the harness is imported inside
the command."""

from __future__ import annotations

import argparse
import sys

from ._common import provider_option_args, provider_options, split_spec
from ._io import load_report, save_report

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "why", help="narrate a report through a model provider — commentary "
                    "checked number by number, read by no analysis")
    parser.add_argument("report", help="a report_*.json produced by compare/batch")
    parser.add_argument("--provider", required=True, metavar="NAME=KIND:MODEL")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """The narrator through the harness: brief → provider → checked
    ingestion.  The model phrases; it never alters a number, a verdict or
    an exit code — the narration is commentary no analysis reads."""
    from ..harness import provider_from_spec
    from ..narrate import ingest_narration, narration_brief, narration_prompt
    from ..verdict import format_verdict_card
    report_path, report = load_report(args.report)
    if report is None:
        return 2
    name, spec = split_spec(args.provider)
    options = provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()
    try:
        provider = provider_from_spec(spec, **({} if kind == "scripted" else options))
        brief = narration_brief(report)
        prompt = narration_prompt(brief)
        response = provider.complete([{"role": "user", "content": prompt}], None)
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the provider failed: say so, change nothing
        print(f"error: provider failed: {exc}", file=sys.stderr)
        return 3
    text = (response.text or "").strip()
    ingest_narration(report, text, brief=brief, model=f"{name or kind}:{provider.model}")
    report["narration"]["source"] = "harness-provider"
    report["narration"]["facts_in_brief"] = len(brief.get("facts") or [])
    save_report(report_path, report)
    check = report["narration"]["faithfulness"]
    card = report.get("verdict_card")
    if card:
        print(format_verdict_card(card))
        print()
    print(f"Why ({report['narration']['model']}, {len(brief.get('facts') or [])} facts in the brief):")
    for line in text.splitlines():
        print(f"  {line}")
    print()
    if check["faithful"]:
        print("  faithful: every number and citation traces to the brief")
    else:
        if check["unsupported_numbers"]:
            print(f"  UNSUPPORTED numbers (not in the evidence): "
                  f"{', '.join(check['unsupported_numbers'])}")
        if check["invalid_citations"]:
            print(f"  INVALID citations: {', '.join(check['invalid_citations'])}")
        print("  stored flagged — the reader sees the warning; no verdict, number "
              "or exit code depends on this text")
    return 0
