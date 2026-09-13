"""The ``eval`` command: the evaluation scorecard per agent — success,
correct tool, grounding, latency, cost, safety and policy, risk vs
reward, trajectory quality — offline against a golden set or online as
recorded, from a trace directory or the trace database.  With
``--judge`` a second model grades every answer first: the only part that
talks to a network, and the harness is imported inside the command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..scorecard import load_golden, load_policy, render_scorecard_markdown, scorecard as build_scorecard
from ._common import db_source_args, load_source, provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "eval", help="the evaluation scorecard per agent: success, correct tool, grounding, latency, cost, "
                     "safety and policy, risk vs reward, trajectory quality (loops, stopping, recovery), "
                     "and a judge's verdicts beside the grade — offline against a golden set or online as recorded")
    parser.add_argument("tracesdir", nargs="?", default=None, help="directory of traces")
    db_source_args(parser)
    parser.add_argument("--golden", default=None, help="golden dataset (tasks JSON; may carry a policy)")
    parser.add_argument("--policy", default=None, help="safety policy JSON")
    parser.add_argument("--judge", default=None, metavar="NAME=KIND:MODEL",
                        help="grade every answer with a second model first (talks to a network unless scripted)")
    parser.add_argument("--with-steps", action="store_true", help="show the judge the steps, not only the answer")
    parser.add_argument("--write", action="store_true", help="write the judge's verdicts back into the trace files")
    parser.add_argument("-o", "--output", default="eval", help="output directory (default: eval)")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """The evaluation scorecard over a directory of traces or the trace
    database, offline (against a golden set) or online (as recorded);
    with --judge, a second model grades every answer first (the only
    part that talks to a network)."""
    try:
        trajectories = load_source(args)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2
    raws: dict = {}
    if args.tracesdir and Path(args.tracesdir).is_dir():
        for path in sorted(Path(args.tracesdir).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "steps" in data:
                raws[data.get("trace_id") or path.stem] = data
                raws.setdefault(path.stem, data)
    try:
        golden = load_golden(args.golden) if args.golden else None
        policy = load_policy(args.policy) if args.policy else None
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    judged = None
    if args.judge:
        from ..harness import provider_from_spec
        from ..harness.judge import judge_many
        _name, spec = split_spec(args.judge)
        options = provider_options(args)
        kind = spec.split(":", 1)[0].strip().lower()
        factory = lambda: provider_from_spec(spec, **({} if kind == "scripted" else options))  # noqa: E731
        targets = []
        for t in trajectories:
            raw = raws.get(t.trace_id)
            if raw is None:
                raw = t.to_dict()
                raws[t.trace_id] = raw
            targets.append(raw)
        judged = judge_many(targets, factory, with_steps=args.with_steps, apply=False)
        if args.write and args.tracesdir:
            for path in sorted(Path(args.tracesdir).glob("*.json")):
                data = raws.get(path.stem)
                if data is not None:
                    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    card = build_scorecard(trajectories, golden, policy, raws)
    if judged:
        card["judge_run"] = judged
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval.json").write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = render_scorecard_markdown(card)
    (out_dir / "EVAL.md").write_text(md, encoding="utf-8")
    print(md)
    if judged:
        print(f"judge: {judged['judged']} judged, agreed with the grade on {judged['agreed_with_prior']}, "
              f"disagreed on {judged['disagreed_with_prior']}, {judged['failed']} failed")
    print(f"Wrote {out_dir / 'eval.json'} and {out_dir / 'EVAL.md'}")
    return 0
