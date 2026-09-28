"""The ``rl`` command, per trace: the run as an episode — return,
discounted return, the reward counts and the largest rewards, recorded
or shaped; for a runs layout, each agent's mean return with its
interval.  Engine only; nothing is written."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..trace import Trajectory
from ._io import run_id_from_name

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "rl", help="per trace: the run as an episode — return, discounted return, reward counts, largest "
                   "rewards (recorded, else shaped); per agent mean return with its interval for a runs layout")
    parser.add_argument("target", help="a trace file or a directory of traces")
    parser.add_argument("--json", action="store_true", help="print every episode as JSON")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Per trace: the run as an episode — return, discounted return, the
    reward counts and the largest rewards, recorded or shaped; for a
    runs layout, each agent's mean return with its interval. Engine
    only; nothing is written."""
    from ..rl import mean_ci, rl_run_from_trace
    target = Path(args.target)
    if target.is_dir():
        paths = sorted(p for p in target.glob("*.json") if not p.name.endswith(".live.json"))
    elif target.is_file():
        paths = [target]
    else:
        print(f"error: {target} is neither a file nor a directory", file=sys.stderr)
        return 2
    runs: list[dict] = []
    for path in paths:
        try:
            t = Trajectory.from_json(path)
        except ValueError as exc:
            print(f"warning: skipping invalid trace: {exc}", file=sys.stderr)
            continue
        run_id = run_id_from_name(path)
        if run_id:
            t.run_id = run_id
        episode = rl_run_from_trace(t)
        episode["path"] = str(path)
        runs.append(episode)
        if args.json:
            continue
        print(f"{path.name}: {episode['agent']} return {episode['return']:g} (discounted {episode['discounted_return']:g}) "
              f"over {episode['steps']} steps [{episode['source']}]: {episode['positive']} positive, "
              f"{episode['negative']} negative, {episode['zero']} zero")
        for row in episode["largest"]:
            print(f"    step {row['step']:>4}  {row['reward']:>+8.2f}  {row['why']}")
    if not runs:
        print("error: no valid traces found", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(runs, indent=2, ensure_ascii=False))
        return 0
    if target.is_dir() and len(runs) > 1 and all(run_id_from_name(Path(r["path"])) for r in runs):
        by_agent: dict[str, list[float]] = {}
        for episode in runs:
            by_agent.setdefault(episode["agent"], []).append(episode["return"])
        print("Per agent (runs layout):")
        for agent in sorted(by_agent):
            mean, ci = mean_ci(by_agent[agent])
            interval = f" [{ci[0]:g}, {ci[1]:g}]" if ci else " (no interval under 2 episodes)"
            print(f"  {agent}: mean return {mean:g}{interval} over {len(by_agent[agent])} episodes")
    return 0
