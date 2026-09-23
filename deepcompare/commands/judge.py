"""The ``judge`` command: a second model judges each trace's final
answer; recorded as ``outcome.judge``, applied to ``outcome.success``
only with ``--apply``.  Talks to a network unless the provider is
scripted; the harness is imported inside the command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._common import provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "judge", help="a second model judges each trace's final answer (talks to a network unless "
                      "the provider is scripted); recorded as outcome.judge, applied to "
                      "outcome.success only with --apply")
    parser.add_argument("target", help="a trace file, a directory of traces, or a report_*.json")
    parser.add_argument("--provider", required=True, metavar="NAME=KIND:MODEL")
    parser.add_argument("--rubric", default=None,
                        help="a name from RUBRICS (strict, long-run) or the judging instruction itself "
                             "(default: strict correctness, JSON verdict)")
    parser.add_argument("--with-steps", action="store_true", help="show the judge the steps, not only the answer")
    parser.add_argument("--steps-cap", type=int, default=None, metavar="N",
                        help="how many steps to show with --with-steps (default 40); a longer run is shown as "
                             "its opening and closing with the gap named, never silently cut")
    parser.add_argument("--apply", action="store_true", help="replace outcome.success/score with the judge's verdict (marked graded_by: model)")
    parser.add_argument("--db", default=None, help="also update the judged traces in this trace database")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """A second model judges each trace's final answer."""
    import json
    from ..harness import provider_from_spec
    from ..harness.judge import STEP_EXCERPT, judge_many
    target = Path(args.target)
    if target.is_dir():
        paths = sorted(p for p in target.glob("*.json")
                       if not p.name.endswith(".live.json") and not p.name.startswith(("report_", "aggregate", "RUN_MANIFEST", "fleet")))
    else:
        paths = [target]
    traces = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"skipped {path.name}: {exc}", file=sys.stderr)
            continue
        if "steps" in data and "outcome" in data:
            traces.append((path, data, None))
        elif "a" in data and "b" in data:   # a report: judge both sides
            traces.append((path, data["a"], data)); traces.append((path, data["b"], data))
    if not traces:
        print("error: nothing to judge", file=sys.stderr)
        return 2
    _name, spec = split_spec(args.provider)
    options = provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()
    factory = lambda: provider_from_spec(spec, **({} if kind == "scripted" else options))  # noqa: E731
    counts = judge_many([t[1] for t in traces], factory, rubric=args.rubric,
                        with_steps=args.with_steps, apply=args.apply,
                        cap=args.steps_cap if args.steps_cap and args.steps_cap > 0 else STEP_EXCERPT)
    written = set()
    for path, data, report in traces:
        if path in written:
            continue
        payload = report if report is not None else data
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.add(path)
    if args.db:
        from ..tracedb import TraceDB
        with TraceDB(args.db) as db:
            for path, data, report in traces:
                if report is None:
                    db.add(data, source="judge")
    for path, data, report in traces:
        j = (data.get("outcome") or {}).get("judge") or {}
        agent = (data.get("agent") or {}).get("name")
        print(f"  {path.name} · {agent}: " + (f"judge says {'✓' if j.get('success') else '✗'} score {j.get('score')}"
              f" — {j.get('rationale', '')[:90]}" if not j.get("error") else f"no verdict ({j.get('error')})")
              + ("  [applied]" if j.get("applied") else "") + ("  [self-judged]" if j.get("self_judged") else ""))
    print(f"{counts['judged']} judged, {counts['agreed_with_prior']} agree with the prior grade, "
          f"{counts['disagreed_with_prior']} disagree, {counts['failed']} without a verdict"
          + ("; verdicts applied to outcome.success (graded_by: model)" if args.apply else "; recorded as outcome.judge only"))
    return 0
