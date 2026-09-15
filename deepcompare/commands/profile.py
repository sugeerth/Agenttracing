"""The ``profile`` command: per-task reference profiles built from many
runs (successes only, unless told otherwise), and every run scored
against its task's profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..profile import build_profile, profile_suite
from ..trace import Trajectory
from ._io import load_traces

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "profile",
        help="build reference profiles from many runs and score runs against them",
    )
    parser.add_argument("tracesdir", help="directory of trajectory *.json files")
    parser.add_argument("--build-from",
                        help="directory to learn the profiles from "
                             "(default: the same directory)")
    parser.add_argument("--include-failures", action="store_true",
                        help="learn the norm from failures too (default: "
                             "successes only)")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Build per-task reference profiles and score runs against them."""
    traces_dir = Path(args.tracesdir)
    if not traces_dir.is_dir():
        print(f"error: {traces_dir} is not a directory", file=sys.stderr)
        return 2
    trajectories = load_traces(traces_dir)
    if not trajectories:
        print("error: no valid traces found", file=sys.stderr)
        return 2

    source_dir = Path(args.build_from) if args.build_from else traces_dir
    source = (load_traces(source_dir) if args.build_from else trajectories)

    by_task: dict[str, list[Trajectory]] = {}
    for t in source:
        by_task.setdefault(t.task.id, []).append(t)

    profiles: dict[str, dict] = {}
    for task_id, runs in sorted(by_task.items()):
        try:
            profiles[task_id] = build_profile(
                runs, name=task_id, successes_only=not args.include_failures)
        except ValueError as exc:
            print(f"warning: no profile for {task_id}: {exc}", file=sys.stderr)
    if not profiles:
        print("error: could not build any profile", file=sys.stderr)
        return 2

    suite = profile_suite(profiles, trajectories)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profiles.json").write_text(
        json.dumps({"profiles": profiles, "suite": suite}, indent=2,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'profiles.json'}")

    print(f"\nBuilt {len(profiles)} profile(s) from "
          f"{sum(p['runs_used'] for p in profiles.values())} run(s).")
    for task_id, profile in profiles.items():
        print(f"  {task_id:<26} {' -> '.join(profile['canonical_path'])}"
              f"   [{profile['runs_used']} run(s)"
              f"{', thin' if profile['thin_evidence'] else ''}]")
    print(f"\n{suite['narrative']}")
    for row in suite["scored"]:
        if row["verdict"] in ("failed", "off-profile"):
            print(f"  [{row['verdict']}] {row['task']}/{row['agent']}: "
                  f"{row['narrative'][:150]}")
    return 0
