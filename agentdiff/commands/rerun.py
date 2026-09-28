"""The ``rerun`` command: replay recorded runs hermetically — the model's
own turns and the recording's tool results, or a named model in the
recorded world — and diff each against its recording; exit 1 on drift.
No network unless ``--provider`` names a live one; the harness is
imported inside the command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._common import provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "rerun", help="replay recorded runs hermetically — the model's own turns and the recording's "
                      "tool results, or a named model in the recorded world — and diff each against "
                      "its recording; exit 1 on drift (no network unless --provider names a live one)")
    parser.add_argument("target", help="a trace file or a directory of traces")
    parser.add_argument("-o", "--output", default="out/rerun", help="output directory (default: out/rerun)")
    parser.add_argument("--provider", default=None, metavar="NAME=KIND:MODEL",
                        help="drive this model through the recorded world instead of the recording's own turns")
    parser.add_argument("--tools", default=None, metavar="MODULE:ATTR",
                        help="declared tools: schemas for the model, and the live fallback under --policy live")
    parser.add_argument("--policy", choices=["strict", "empty", "live"], default="strict",
                        help="what a call the recording never made gets: an error naming the miss (strict, default), "
                             "an empty result, or the declared tool run for real (live)")
    parser.add_argument("--from", dest="from_step", type=int, default=None,
                        help="resume from this step: the prefix replays from the recording, the replay proper starts here")
    parser.add_argument("--until", type=int, default=None, help="stop after this step; the diff covers the scoped range only")
    parser.add_argument("--span", default=None, metavar="AGENT|ID",
                        help="scope to a sub-agent's steps (by span id or agent name; nested spans included)")
    parser.add_argument("--golden", default=None, help="golden tasks JSON; milestones lost or gained by the replay are reported")
    parser.add_argument("--cassette", default=None, help="serve tool results from this cassette.json (a checkpoint bundle's) instead of the trace's own")
    parser.add_argument("--traces", action="store_true", help="write every replayed trace under <output>/traces")
    parser.add_argument("--junit", nargs="?", const="junit.xml", default=None, help="write JUnit XML (default name: junit.xml)")
    parser.add_argument("--job-summary", nargs="?", const="rerun-summary.md", default=None,
                        help="write the Markdown summary (default name: rerun-summary.md)")
    parser.add_argument("--github-annotations", action="store_true",
                        help="print ::error workflow commands for drifted traces; append the summary to GITHUB_STEP_SUMMARY")
    parser.add_argument("--no-fail-on-drift", dest="fail_on_drift", action="store_false",
                        help="exit 0 even when a trace drifted (report only)")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def _trace_paths(target: str) -> list:
    path = Path(target)
    if path.is_dir():
        return sorted(p for p in path.glob("*.json")
                      if not p.name.startswith(("report_", "aggregate", "rerun", "cassette", "loop", "manifest")))
    if path.is_file():
        return [path]
    raise ValueError(f"{target} is neither a trace file nor a directory")


def run(args: argparse.Namespace) -> int:
    """Replay recorded runs hermetically and diff each against its
    recording: the model's own turns and the recording's tool results,
    or a named model in the recorded world.  Exit 1 on any drift."""
    from ..harness.rerun import rerun_paths, to_annotations, to_junit, to_markdown
    from ..harness.runner import load_tools
    provider = None
    try:
        paths = _trace_paths(args.target)
        tools = load_tools(args.tools) if args.tools else None
        if args.provider:
            from ..harness import provider_from_spec
            _name, spec = split_spec(args.provider)
            kind = spec.split(":", 1)[0].strip().lower()
            provider = provider_from_spec(spec, **({} if kind == "scripted" else provider_options(args)))
        if not paths:
            raise ValueError(f"no trace files under {args.target}")
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        golden = None
        if getattr(args, "golden", None):
            from ..scorecard import load_golden
            golden = load_golden(args.golden)
        cassette = None
        if getattr(args, "cassette", None):
            from ..harness.cassette import Cassette
            cassette = Cassette.from_dict(json.loads(Path(args.cassette).read_text(encoding="utf-8")))
            if len(paths) > 1:
                raise ValueError("--cassette serves one trace; give one trace file")
        summary = rerun_paths(paths, provider=provider, tools=tools, policy=args.policy,
                              out_dir=(out / "traces") if args.traces else None, keep_traces=False,
                              from_step=getattr(args, "from_step", None), until=getattr(args, "until", None),
                              span=getattr(args, "span", None), golden=golden, cassette=cassette)
    except (ValueError, OSError, ImportError, AttributeError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    (out / "rerun.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    written = [out / "rerun.json"]
    if args.junit:
        (out / args.junit).write_text(to_junit(summary), encoding="utf-8"); written.append(out / args.junit)
    if args.job_summary:
        (out / args.job_summary).write_text(to_markdown(summary), encoding="utf-8"); written.append(out / args.job_summary)
    if args.github_annotations:
        print(to_annotations(summary), end="")
        import os
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write(to_markdown(summary))
    print(f"Replayed {summary['traces']} trace(s) hermetically ({summary['mode']}, policy {args.policy}): "
          f"{summary['faithful']} reproduced, {summary['drifted']} drifted")
    for r in summary["results"]:
        print(("  ✓ " if r.get("faithful") else "  ✗ ") + str(r.get("reading") or ""))
        for n in r.get("drift_map") or []:
            if n.get("kind") == "span" and n.get("reproduced") is False:
                print(f"      ✗ sub-agent {n.get('agent')} (steps {n.get('from')}–{n.get('to')}): {n.get('differed')} differed, {n.get('misses')} miss(es), first at {n.get('first_difference')}")
    print("  written: " + ", ".join(str(p) for p in written))
    if summary["drifted"] and args.fail_on_drift:
        return 1
    return 0
