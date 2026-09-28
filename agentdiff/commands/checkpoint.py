"""The ``checkpoint`` command: a bundle for a long run at a step — the
prefix as a SCHEMA trace, the cassette, the context the model had, and a
summary — everything a person or a ``rerun --from`` needs to resume from
there without the hours before it.  Hermetic: the harness pieces are
imported inside the command, and no provider is ever loaded."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "checkpoint", help="write a checkpoint bundle for a long run at a step: the prefix trace, the cassette, "
                           "the context the model had, and a summary — resume with `rerun --from`")
    parser.add_argument("trace", help="a trace file")
    parser.add_argument("--step", type=int, required=True, help="the step the checkpoint stands before")
    parser.add_argument("-o", "--output", default="out/checkpoint", help="bundle directory (default: out/checkpoint)")
    parser.add_argument("--golden", default=None, help="golden tasks JSON, to list the milestones reached so far")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Write a checkpoint bundle for a long run at a step: the prefix as
    a SCHEMA trace, the cassette, the context the model had, and a
    summary — everything a person or a rerun needs to resume from there
    without the hours before it."""
    from ..harness import context as ctx
    from ..harness.cassette import Cassette
    from ..milestones import evaluate as evaluate_milestones
    path = Path(args.trace)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
        steps = list(trace.get("steps") or [])
        step = int(args.step)
        if not (0 <= step < len(steps)):
            raise ValueError(f"step {step} is outside the trace's {len(steps)} steps")
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        prefix = dict(trace)
        prefix["steps"] = [s for s in steps[:step] if s.get("type") != "answer"]
        prefix["trace_id"] = f"{trace.get('trace_id', 'run')}__checkpoint{step}"
        prefix["outcome"] = {"success": False, "answer": "", "score": None, "termination": "user_stop",
                             "note": f"checkpoint: the recording's first {step} step(s); the run continues from step {step}"}
        clock = sum(float(s.get("latency_s") or 0) for s in prefix["steps"])
        tokens = sum(int(s.get("tokens") or 0) for s in prefix["steps"] if isinstance(s.get("tokens"), int))
        prefix["totals"] = {"input_tokens": 0, "output_tokens": tokens, "cost_usd": 0.0, "latency_s": round(clock, 4)}
        (out / "prefix.json").write_text(json.dumps(prefix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        cassette = Cassette.from_trace(trace)
        (out / "cassette.json").write_text(json.dumps(cassette.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out / "context.txt").write_text(ctx.render(ctx.context_at(trace, step)), encoding="utf-8")
        milestones = None
        if args.golden:
            from ..scorecard import load_golden
            task = (load_golden(args.golden)["tasks"] or {}).get(str((trace.get("task") or {}).get("id")))
            milestones = (task or {}).get("milestones")
        reached = evaluate_milestones({"agent": trace.get("agent"), "steps": steps[:step]}, milestones) if milestones else None
        at = steps[step]
        summary = {"trace_id": trace.get("trace_id"), "step": step, "of": len(steps), "seconds_so_far": round(clock, 4), "tokens_so_far": tokens,
                   "span": at.get("span"), "next_step": {"type": at.get("type"), "name": at.get("name"), "input": str(at.get("input") or "")[:200]},
                   "context": ctx.summary(trace, step), "cassette": {"recorded_calls": cassette.recorded_calls, "tools": cassette.names},
                   "milestones_reached": [m["id"] for m in reached["milestones"] if m["reached"]] if reached else None,
                   "resume": f"agentdiff rerun {path} --from {step} --cassette {out / 'cassette.json'} [--provider NAME=KIND:MODEL]"}
        (out / "checkpoint.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Checkpoint at step {step} of {len(steps)} ({summary['seconds_so_far']}s so far"
          + (f", inside {at['span'].get('agent')}" if isinstance(at.get("span"), dict) else "") + ")")
    print(f"  next: {at.get('type')} {at.get('name')} — {summary['next_step']['input'][:80]}")
    if reached:
        print(f"  milestones reached so far: {len(summary['milestones_reached'])} — " + ", ".join(summary["milestones_reached"]))
    print(f"  written: {out / 'prefix.json'}, {out / 'cassette.json'}, {out / 'context.txt'}, {out / 'checkpoint.json'}")
    print(f"  resume: {summary['resume']}")
    return 0
