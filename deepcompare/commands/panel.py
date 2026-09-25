"""The ``panel`` command: a judge that reads a whole corpus and
synthesises, with every claim checked against the traces it cites.

Talks to a network unless the provider is scripted; the harness is
imported inside the command, so the analysis engine stays network-free.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._common import provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "panel", help="a judge reads the whole corpus and synthesises what is systematically wrong; "
                      "every claim cites a run and a step, and a claim whose citation is not in the "
                      "trace is dropped (talks to a network unless the provider is scripted)")
    parser.add_argument("target", help="a directory of traces")
    parser.add_argument("--provider", required=True, metavar="NAME=KIND:MODEL")
    parser.add_argument("--policy", default=None, help="safety policy JSON, for the run flags")
    parser.add_argument("--ask", action="append", default=None, metavar="QUESTION",
                        help="a question to put to the panel; repeatable. Without one, the four in "
                             "`harness.panel.QUESTIONS` are asked")
    parser.add_argument("--turns", type=int, default=None, metavar="N",
                        help="turns per question (default 24)")
    parser.add_argument("--checkpoint", default=None, metavar="FILE",
                        help="write the findings so far after each one, and resume from them; a "
                             "reading of a large corpus is a long task and will be interrupted")
    parser.add_argument("-o", "--output", default=None, metavar="DIR",
                        help="also write panel.json here")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """A judge reads the corpus and synthesises."""
    import json
    from ..harness import provider_from_spec
    from ..harness.panel import TURNS, convene
    target = Path(args.target)
    if not target.is_dir():
        print("error: panel reads a directory of traces", file=sys.stderr)
        return 2
    traces = []
    for path in sorted(target.glob("*.json")):
        if path.name.startswith(("report_", "aggregate", "RUN_MANIFEST", "fleet", "golden", "panel")):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"skipped {path.name}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict) and "steps" in data and "outcome" in data:
            traces.append(data)
    if len(traces) < 2:
        print("error: a panel needs at least two runs; one run is a reading, not a synthesis",
              file=sys.stderr)
        return 2

    policy = None
    if args.policy:
        from ..scorecard import load_policy
        policy = load_policy(args.policy)
    _name, spec = split_spec(args.provider)
    options = provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()
    factory = lambda: provider_from_spec(spec, **({} if kind == "scripted" else options))  # noqa: E731

    def progress(finding: dict) -> None:
        check = finding.get("verification") or {}
        if finding.get("error"):
            print(f"  ✗ {finding['question'][:60]}… — {finding['error']}")
        elif finding["supported"]:
            print(f"  ✓ {finding['claim'][:96]}")
            print(f"      {check['verified']}/{check['cited']} citation(s) found in the traces, "
                  f"{finding['tool_calls']} look-up(s)")
        else:
            print(f"  ✗ dropped: {str(finding.get('claim'))[:70]}… — {check.get('reason', '')[:70]}")

    print(f"Reading {len(traces)} run(s)…")
    out = convene(traces, factory, questions=args.ask, policy=policy,
                  turns=args.turns or TURNS, checkpoint=args.checkpoint, on_finding=progress)

    print()
    print(f"{out['supported']} of {out['asked']} finding(s) survived their citations"
          + (f"; {out['dropped_for_bad_citations']} dropped" if out["dropped_for_bad_citations"] else "")
          + (f"; {len(out['errors'])} without a verdict" if out["errors"] else "")
          + f" — {out['turns']} turn(s), {out['tool_calls']} look-up(s).")
    for row in out["synthesis"]:
        print(f"  · {row['claim']}")
        for cite in row["cites"][:3]:
            c = cite.get("cite") or {}
            print(f"      {c.get('run')} step {c.get('step')}: \"{str(c.get('quote'))[:70]}\"")
    if out["dropped"]:
        print("  dropped for citations that are not in the traces:")
        for row in out["dropped"]:
            print(f"      {str(row['claim'])[:70]}… — {str(row['reason'])[:80]}")

    if args.output:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "panel.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {path}")
    return 0
