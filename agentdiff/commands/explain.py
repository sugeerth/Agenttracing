"""The ``explain`` command: read one trace — what happened, what the
answer rests on, why it ended that way, what it means, what to take
forward — every finding cited, and the citations checked."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "explain", help="read ONE trace: what happened, what the answer "
                        "rests on, why it ended that way, what it means, "
                        "what to take forward — every finding cited")
    parser.add_argument("trace", help="a SCHEMA trajectory JSON file")
    parser.add_argument("-o", "--output", default=None,
                        help="also write the reading as JSON to this path")
    parser.add_argument("--expected", default=None,
                        help="override the task's expected answer")
    parser.add_argument("--html", default=None,
                        help="also write the reading as a self-contained HTML page")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from ..reasoning import check_reading, read_trace
    from ..trace import Trajectory
    try:
        traj = Trajectory.from_json(args.trace)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    reading = read_trace(traj, expected=args.expected)
    problems = check_reading(reading, traj)
    if getattr(args, "html", None):
        from ..htmlout import reading_html
        html_out = Path(args.html)
        html_out.parent.mkdir(parents=True, exist_ok=True)
        html_out.write_text(reading_html(reading), encoding="utf-8")
        print(f"Wrote {html_out}")
    print(f"Reading of {reading['agent']} on {reading['task']}")
    print(f"  {reading['summary']}")
    print("  What happened:")
    for phase in reading["phases"]:
        print(f"    steps {phase['steps'][0]}–{phase['steps'][-1]}: {phase['summary']}")
    validity = reading.get("validity") or {}
    if validity.get("status") and validity["status"] != "clean":
        print(f"  VALIDITY {validity['status'].upper()}: {validity.get('reason')}")
    basis = reading.get("answer_basis") or {}
    if reading["rests_on"]:
        print(f"  The answer rests on ({basis.get('status')}"
              + (f"; basis complete at step {basis['basis_complete_at']}, "
                 f"{basis['steps_after_basis_complete']} step(s) spent after it"
                 if basis.get("basis_complete_at") is not None else "") + "):")
        for r in reading["rests_on"]:
            where = (f"first at step {r['first_step']} ({r['source']})"
                     if r["first_step"] is not None else "NO earlier step")
            match = ("matches expected" if r["matches_expected"] is True else
                     ("contradicts expected" if r["status"] == "contradicted"
                      else "not in expected") if r["matches_expected"] is False else
                     "no expected value to compare")
            print(f"    {r['value']} [{r['status'].replace('_', ' ')}] — {where}; {match}")
    checks = reading.get("phase_checks") or {}
    if checks.get("writes"):
        print("  Order of work: "
              + ("wrote before any read; " if checks["first_write_before_any_read"] else "")
              + ("last write never checked; " if checks["verification_after_last_write"] is False
                 else f"checked after the last write at step {checks['verification_step']}; "
                 if checks["verification_after_last_write"] else "")
              + f"{checks['regression_cycles']} act→look→act cycle(s)")
    critical = reading.get("critical_error") or {}
    if reading.get("errors"):
        print(f"  Errors ({len(reading['errors'])}):")
        for e in reading["errors"]:
            print(f"    step {e['step']} {e['name']}: {e['state'].replace('_', ' ')}"
                  + (f", resolved at step {e['resolved_at']}" if e.get("resolved_at") is not None else "")
                  + (f" — {e['footprint_reason']}" if e.get("footprint_reason") else ""))
        if critical.get("step") is not None:
            print(f"  Critical error: step {critical['step']} ({critical['name']}) — "
                  f"{critical['why']}; {critical['verification']} until replayed")
    why = reading["why_it_ended"]
    term = (f"termination {why['termination']}" if why["declared"]
            else "termination not declared")
    print(f"  Why it ended: {'succeeded' if why['success'] else 'failed'}, "
          f"{term} — {why['verdict_basis']}")
    if reading["what_it_means"]:
        print("  What it means:")
        for f in reading["what_it_means"]:
            steps = f"steps {f['steps']}" if f["steps"] else "run-level"
            print(f"    [{f['evidence_class']}] {f['statement']} ({steps})")
    if reading["take_forward"]:
        print("  Take forward:")
        for t in reading["take_forward"]:
            where = f"at step {t['at_step']}: " if t.get("at_step") is not None else ""
            print(f"    - {where}{t.get('instead') or t.get('action')}"
                  + (" (conditional on fixing the measurement)"
                     if t.get("conditional_on_validity") else ""))
    print(f"  Confidence: {reading['confidence']['level']} — "
          f"{reading['confidence']['basis']}")
    print(f"  Grounding check: {'every quote verified' if not problems else problems}")
    if args.output:
        Path(args.output).write_text(json.dumps(reading, indent=2,
                                                ensure_ascii=False) + "\n",
                                     encoding="utf-8")
        print(f"Wrote {args.output}")
    return 0 if not problems else 1
