"""The agent's loop, lap by lap.

An agent works in a loop: look, act, check, and go round again. This
module cuts one run into those rounds so a page can draw them stacked,
one row per round, and a reader can see whether the agent converged or
went in circles.

**Where a lap ends.** At each *check*: a step that verifies the work (a
test run, a type check, a lint, a ``run_check`` tool). The check's outcome
closes the lap as passed or failed. A run that never checks has no such
boundary, so its laps are its *turns* instead: each model step (``reason``)
opens a new one. The ``basis`` says which.

**What repeats.** Each lap has a signature, the sequence of tools it
called. A lap whose signature is the same as the lap before it is the
agent doing the same round again. A stretch of those is the loop a reader
is looking for. The stuck verdict itself is :func:`agentdiff.process.loops`'s,
not a second opinion: the longest back-to-back repeated block, with its
period, and the rule that judged it.

**How it moves.** ``transitions`` counts every move from one tool to the
next, so a page can draw the run's control flow as a graph whose cycles
are the loops.

Every number is a count over the recorded steps.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .duel import classify
from .process import loops as process_loops
from .trace import Trajectory

__all__ = ["laps", "is_check", "check_outcome", "ACTIVITY_ORDER"]

#: the activity classes, in the order a lap is usually read
ACTIVITY_ORDER = ("plan", "research", "explore", "edit", "run", "verify", "delegate", "other", "think")
_EXPLORE_NAME = re.compile(r"(^|_)(grep|glob|search|find|read|ls|cat|list|view|open|fetch|lookup)($|_)", re.I)
_EDIT_NAME = re.compile(r"(^|_)(edit|write|patch|apply|update|replace|create|delete|rename|insert)($|_)", re.I)
_CHECK_NAME = re.compile(r"(^|_|\b)(check|test|tests|verify|lint|typecheck|pytest|ctest)($|_|\b)", re.I)
_FAILED = re.compile(r"\b(\d+\s+failed|failed|failure|error|errors|traceback|fail)\b", re.I)
_PASSED = re.compile(r"\b(\d+\s+passed|passed|pass|ok|success|green|all tests pass)\b", re.I)


def _activity(step: dict) -> str:
    cls = classify(step)
    if cls is None:
        return "think" if step.get("type") in ("reason", "answer") else "other"
    name = str(step.get("name") or "")
    if cls in ("other", "run"):
        # a tool the vendor tables do not know: read its name, then its declared effect
        if _CHECK_NAME.search(name):
            return "verify"
        if _EDIT_NAME.search(name) or step.get("effect") == "write":
            return "edit"
        if _EXPLORE_NAME.search(name) or step.get("effect") == "read":
            return "explore"
    return cls


def is_check(step: dict) -> bool:
    return _activity(step) == "verify"


def check_outcome(step: dict) -> Optional[bool]:
    """True when the check passed, False when it failed, None when the
    record does not say. An errored step failed, whatever it printed."""
    if step.get("error"):
        return False
    out = str(step.get("output") or "")
    failed, passed = _FAILED.search(out), _PASSED.search(out)
    if failed and not re.search(r"\b0\s+failed\b", out, re.I):
        return False
    if passed:
        return True
    return None


def _data(traj) -> dict:
    return traj.to_dict() if hasattr(traj, "to_dict") else traj


def laps(traj) -> dict:
    data = _data(traj)
    steps = list(data.get("steps") or [])
    checks = [i for i, s in enumerate(steps) if is_check(s)]
    basis = "checks" if checks else "turns"
    rounds: list = []
    current: list = []

    def close(closed_by: Optional[dict]) -> None:
        if not current and closed_by is None:
            return
        tools = [s for s in current if s["activity"] not in ("think",)]
        rounds.append({"n": len(rounds) + 1, "steps": list(current), "closed_by": closed_by,
                       # the calls it made, arguments and all: a lap that edits a
                       # different line is a different lap, even with the same tools
                       "signature": tuple((s["name"], s["call"]) for s in tools),
                       "seconds": round(sum(s["latency_s"] for s in current), 3),
                       "tokens": sum(s["tokens"] for s in current),
                       "errors": sum(1 for s in current if s["error"])})
        current.clear()

    for i, s in enumerate(steps):
        item = {"index": i, "name": str(s.get("name") or s.get("type") or "step"), "type": s.get("type"),
                "call": " ".join(str(s.get("input") or "").split())[:200],
                "activity": _activity(s), "error": bool(s.get("error")),
                "latency_s": float(s.get("latency_s") or 0.0), "tokens": int(s.get("tokens") or 0),
                "reward": s.get("reward") if isinstance(s.get("reward"), (int, float)) else None}
        if basis == "turns" and s.get("type") == "reason" and current:
            close(None)
        current.append(item)
        if basis == "checks" and i in checks:
            close({"index": i, "name": item["name"], "passed": check_outcome(s)})
    close(None)
    if rounds and not rounds[-1]["steps"]:
        rounds.pop()
    # a last lap that only thinks or answers is how the lap before it ended
    if len(rounds) > 1 and all(s["activity"] == "think" for s in rounds[-1]["steps"]):
        tail = rounds.pop()
        rounds[-1]["steps"] += tail["steps"]
        rounds[-1]["seconds"] = round(rounds[-1]["seconds"] + tail["seconds"], 3)
        rounds[-1]["tokens"] += tail["tokens"]

    # a lap that does what the lap before it did, tool for tool
    for k, r in enumerate(rounds):
        r["same_as_previous"] = bool(k and r["signature"] and r["signature"] == rounds[k - 1]["signature"])
    repeated = [r["n"] for r in rounds if r["same_as_previous"]]
    longest, run = 0, 0
    for r in rounds:
        run = run + 1 if r["same_as_previous"] else 0
        longest = max(longest, run)

    transitions: dict = {}
    names = [str(s.get("name") or s.get("type")) for s in steps if s.get("type") not in ("reason", "answer")]
    for a, b in zip(names, names[1:]):
        transitions[(a, b)] = transitions.get((a, b), 0) + 1

    try:
        stuck = process_loops(Trajectory.from_dict(data).steps)
    except (ValueError, KeyError, TypeError):
        stuck = None
    passed_at = next((r["n"] for r in rounds if (r["closed_by"] or {}).get("passed") is True), None)
    checks_failed = sum(1 for r in rounds if (r["closed_by"] or {}).get("passed") is False)
    return {
        "basis": basis, "laps": rounds, "count": len(rounds),
        "checks": len(checks), "checks_failed": checks_failed, "first_pass_lap": passed_at,
        "repeated_laps": repeated, "longest_repeat_run": longest,
        "transitions": [{"from": a, "to": b, "count": n} for (a, b), n in sorted(transitions.items(),
                                                                                  key=lambda x: (-x[1], x[0]))],
        "stuck": stuck,
        "summary": _summary(basis, rounds, len(checks), checks_failed, passed_at, repeated, longest, stuck),
    }


def _summary(basis, rounds, checks, failed, passed_at, repeated, longest, stuck) -> str:
    if not rounds:
        return "No steps to go round."
    unit = "check" if basis == "checks" else "turn"
    parts = [f"{len(rounds)} lap(s), each closed by a {unit}" if basis == "checks"
             else f"{len(rounds)} lap(s), one per model turn (no step ran a test suite, lint or check)"]
    if basis == "checks":
        if passed_at:
            parts.append(f"the first passing check closed lap {passed_at}"
                         + (f" after {failed} failing" if failed else ""))
        elif failed:
            parts.append(f"all {failed} check(s) that said failed or passed said failed")
    if repeated:
        parts.append(f"{len(repeated)} lap(s) repeated the lap before tool for tool"
                     + (f", {longest} in a row at most" if longest > 1 else ""))
    b = (stuck or {}).get("longest_repeated_block") or {}
    from .process import LOOP_SPAN, LOOP_TURNS
    if (b.get("repeats", 0) >= LOOP_TURNS or b.get("length", 0) >= LOOP_SPAN) and not passed_at:
        parts.append(f"stuck: a block of {b['period']} step(s) went round {b['repeats']} time(s) and never passed")
    elif stuck and stuck.get("looping") and passed_at:
        parts.append(f"a call recurs often enough for the loop rule, though a check passed on lap {passed_at}")
    return "; ".join(parts) + "."


def laps_many(trajectories: Iterable) -> list:
    return [laps(t) for t in trajectories]
