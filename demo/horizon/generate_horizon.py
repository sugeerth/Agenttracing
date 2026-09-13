"""Generate the long-horizon, multi-agent demo pair (SYNTHETIC).

Two orchestrators solve a long research-and-build task by delegating to
sub-agents (a researcher, a coder, a verifier), each of which may
delegate again. ``orbit-v1`` finishes; ``comet-v2`` fails: its
researcher's second delegation returns nothing new three times, the
coder builds on a stale value, and the verifier never re-reads. Every
number is invented to exercise the horizon view and is labelled so.

    python demo/horizon/generate_horizon.py [out_dir]
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deepcompare.record import Recorder  # noqa: E402

TASK = {"id": "h01_release_report",
        "prompt": "Produce the release report for build 4821: the failing test, the commit that broke it, the fix, and the verified pass count.",
        "expected": "test_parse_dates; commit 9f3c2e1; 412 passing"}
NOTE = "SYNTHETIC: a generated long-horizon multi-agent run; every value invented to exercise the horizon view"


def _research(r, rng, name, queries, useful):
    with r.span(name):
        for q, ok in zip(queries, useful):
            r.step("search", "web_search", f"search: {q}", (f"3 results for {q}: commit 9f3c2e1 'parse dates without tz' touched src/dates.py" if ok else "no results"),
                   latency_s=rng.uniform(0.8, 2.2), tokens=rng.randint(30, 60), error=None)
            if ok:
                r.step("read", "open_page", f"open result 1 for {q}", f"commit 9f3c2e1 — parse dates without tz; test_parse_dates began failing at build 4821", latency_s=rng.uniform(0.6, 1.6), tokens=rng.randint(80, 140))
        r.reason(f"{name}: {sum(useful)} useful source(s); the breaking commit is 9f3c2e1" if sum(useful) else f"{name}: nothing new found", latency_s=rng.uniform(0.5, 1.1), tokens=rng.randint(40, 70))


def _code(r, rng, name, files, ok=True):
    with r.span(name):
        for f in files:
            r.step("read", "read_file", f"read {f}", f"contents of {f}", latency_s=rng.uniform(0.2, 0.6), tokens=rng.randint(60, 120))
        r.reason(f"{name}: the fix is a timezone-aware parse in {files[0]}", latency_s=rng.uniform(0.8, 1.5), tokens=rng.randint(60, 90))
        r.step("tool_call", "write_file", f"write {files[0]}", "ok" if ok else "ok (stale value 411 kept)", latency_s=rng.uniform(0.3, 0.7), tokens=rng.randint(30, 50), effect="write")
        with r.span(name + ".tests"):
            r.step("tool_call", "run_tests", "pytest -q", ("412 passed" if ok else "411 passed, 1 failed"), latency_s=rng.uniform(4.0, 7.0), tokens=rng.randint(20, 40), error=None if ok else True)
            if not ok:
                r.step("tool_call", "run_tests", "pytest -q", "411 passed, 1 failed", latency_s=rng.uniform(4.0, 7.0), tokens=rng.randint(20, 40), error=True)


def make(agent: str, model: str, good: bool, out: Path, seed: int) -> None:
    rng = random.Random(seed)
    r = Recorder(task=TASK["id"], prompt=TASK["prompt"], agent=agent, model=model, version="synthetic",
                 expected=TASK["expected"], out_dir=out, trace_id=f"{TASK['id']}__{agent}")
    with r:
        r.step("plan", "plan", "Plan: find the failing test, find the breaking commit, fix, verify.", "", latency_s=1.6, tokens=90)
        # phase 1: the failing test
        r.step("tool_call", "ci_get_log", "ci log for build 4821", "FAILED test_parse_dates (AssertionError)", latency_s=1.2, tokens=60)
        r.reason("The failing test is test_parse_dates.", latency_s=0.7, tokens=40)
        # phase 2: research the commit
        _research(r, rng, "researcher", ["build 4821 failing test_parse_dates", "commit touching dates.py", "9f3c2e1 diff"], [True, True, True] if good else [True, False, True])
        if not good:
            _research(r, rng, "researcher", ["dates.py history", "test_parse_dates flaky", "timezone parse regression"], [False, False, False])
        r.reason("Decide: the breaking commit is 9f3c2e1; delegate the fix.", latency_s=0.9, tokens=50)
        # phase 3: the fix
        _code(r, rng, "coder", ["src/dates.py", "tests/test_dates.py"], ok=good)
        # phase 4: verify
        with r.span("verifier"):
            r.step("tool_call", "run_tests", "pytest -q tests/test_dates.py", "412 passed" if good else "411 passed, 1 failed", latency_s=rng.uniform(3.0, 5.0), tokens=30, error=None if good else True)
            if good:
                r.step("read", "read_file", "read CHANGELOG.md", "…", latency_s=0.4, tokens=50)
            r.reason("verifier: " + ("all green, 412 passing" if good else "still one failure; reporting the earlier count"), latency_s=0.8, tokens=45)
        r.reason("Compose the release report.", latency_s=1.1, tokens=70)
        answer = ("Release report 4821: failing test test_parse_dates; broken by commit 9f3c2e1; fixed with a timezone-aware parse in src/dates.py; 412 passed, 412 passing."
                  if good else "Release report 4821: failing test test_parse_dates; broken by commit 9f3c2e1; fix applied; 411 passed, 411 passing.")
        r.answer(answer, success=good, tokens=110, latency_s=2.0)
    path = out / f"{TASK['id']}__{agent}.json"
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    data["harness"] = {"adapter": "synthetic", "graded_by": "exact-match", "note": NOTE}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "traces"
    out.mkdir(parents=True, exist_ok=True)
    make("orbit-v1", "sim-orbit-1", True, out, 11)
    make("comet-v2", "sim-comet-2", False, out, 23)
    print(f"wrote 2 traces to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
