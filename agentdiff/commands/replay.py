"""The ``replay`` command: verify the decisive step by re-executing the
failing run from a corrected step, several times — the only way a
hypothesised step becomes replay-verified, refuted or mixed.  Talks to
a network unless the provider is scripted; the harness is imported
inside the command."""

from __future__ import annotations

import argparse
import sys

from ._common import provider_option_args, provider_options, split_spec
from ._io import load_report, save_report

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "replay", help="verify the decisive step by re-executing the failing run "
                       "from a corrected step, several times (talks to a network "
                       "unless the provider is scripted)")
    parser.add_argument("report", help="a report_*.json produced by compare/batch")
    parser.add_argument("--provider", required=True, metavar="NAME=KIND:MODEL",
                        help="the model that continues the replayed run")
    parser.add_argument("--from-step", type=int, default=None,
                        help="replay from this step (default: the diagnosis's decisive step)")
    parser.add_argument("--replays", type=int, default=3,
                        help="rollouts (default 3; one proves nothing)")
    parser.add_argument("--correction", default=None,
                        help="text to substitute at the step (default: the passing "
                             "run's aligned step, verbatim)")
    parser.add_argument("--side", choices=["a", "b"], default=None,
                        help="which run to replay (default: the diagnosed failing side)")
    parser.add_argument("--tools", default=None, metavar="MODULE:ATTR",
                        help="tools the replayed run may call")
    parser.add_argument("--traces", default=None,
                        help="write every replayed trace here")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Verify the decisive step by re-execution: the only way a
    hypothesized step becomes replay-verified, refuted or mixed."""
    from ..harness import provider_from_spec
    from ..harness.replay import replay
    from ..harness.runner import load_tools
    from ..verdict import verdict_card
    report_path, report = load_report(args.report)
    if report is None:
        return 2
    diagnosis = report.get("diagnosis") or {}
    decisive = diagnosis.get("decisive_step") or {}
    side = args.side or diagnosis.get("subject")
    if side not in ("a", "b"):
        print("error: the report names no failing side; pass --side a|b", file=sys.stderr)
        return 2
    step = args.from_step if args.from_step is not None else decisive.get("step")
    if step is None:
        print("error: the diagnosis committed to no decisive step (it abstained); "
              "pass --from-step N to replay a step of your choosing", file=sys.stderr)
        return 2
    other = "b" if side == "a" else "a"
    correction: dict = {}
    if args.correction:
        correction = {"text": args.correction, "output": args.correction}
    else:
        # the recipe: take the decision the passing run took at this step —
        # the counterpart on the aligned row, verbatim
        row = next((r for r in report.get("alignment") or []
                    if r.get(f"{side}_index") == step), None)
        counterpart = None
        if row is not None and row.get(f"{other}_index") is not None:
            steps_other = report[other]["steps"]
            idx = row[f"{other}_index"]
            counterpart = steps_other[idx] if 0 <= idx < len(steps_other) else None
        if counterpart is None:
            print("error: the passing run has no aligned step to borrow at step "
                  f"{step}; pass --correction TEXT", file=sys.stderr)
            return 2
        correction = {"input": counterpart.get("input") or "",
                      "output": counterpart.get("output") or "",
                      "borrowed_from": f"{other} step {counterpart.get('index')}"}
    name, spec = split_spec(args.provider)
    options = provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()

    def factory():
        return provider_from_spec(spec, **({} if kind == "scripted" else options))

    try:
        tools = load_tools(args.tools) if args.tools else []
        trace = {"trace_id": f"{report['task']['id']}__{report[side]['agent']['name']}",
                 "agent": report[side]["agent"], "task": report["task"],
                 "outcome": report[side]["outcome"], "steps": report[side]["steps"],
                 "budget": report.get(side, {}).get("budget") or {"max_steps": 12}}
        result = replay(trace, factory(), tools, int(step), correction,
                        replays=args.replays, out_dir=args.traces,
                        provider_factory=factory)
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = {
        "verdict": result["verdict"], "replays": result["replays"],
        "flipped": result["flipped"], "flip_rate": result["flip_rate"],
        "step": result["step"], "correction": correction,
        "provider": {"name": name or kind, "model": factory().model},
        "runs": [{k: v for k, v in r.items() if k != "trace"} for r in result["runs"]],
        "note": result["note"],
    }
    decisive["verification"] = result["verdict"]
    decisive["replay"] = summary
    diagnosis["decisive_step"] = decisive
    report["diagnosis"] = diagnosis
    report["verdict_card"] = verdict_card(report)
    save_report(report_path, report)
    print(f"Replayed {report[side]['agent']['name']} from step {step} "
          f"×{result['replays']} with the {summary['provider']['name']} provider")
    print(f"  {result['verdict']}: {result['flipped']}/{result['replays']} replay(s) "
          f"flipped the outcome (rate {result['flip_rate']})")
    print(f"  correction: " + (f"borrowed from {correction['borrowed_from']}"
                               if correction.get("borrowed_from") else "as given"))
    if args.traces:
        print(f"  replay traces written to {args.traces}")
    print(f"  {result['note']}")
    from ..verdict import format_verdict_card
    print()
    print(format_verdict_card(report["verdict_card"]))
    return 0
