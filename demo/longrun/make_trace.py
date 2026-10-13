"""Generate a long-running agent's trace (SYNTHETIC): three days, ~2,000 calls.

Every value is invented, and the trace says so (``harness.adapter:
synthetic``). It exists to exercise what a run of hours and days needs
from a reading: sessions split by hours of idle, bursts inside them, a
loop that lasts a night rather than a few steps, and progress that is
easy to lose sight of across thousands of calls.

The story, on the agent's own clock (UTC, from ``started_at``):

- day 1, 08:12 to about 12:30: packages 1 to 6 of a ledger service move
  to the v2 API. Explore, edit, test; each package's test fails first,
  then passes.
- lunch-length idle, then packages 7 to 10. At about 17:20 the
  integration test for package 11 starts failing.
- night: the agent keeps trying. Hours of bursts that each read the same
  files, make the same two edits in turn and re-run the same failing
  test. No progress.
- day 2 morning, a person's note (a ``plan`` step): read the migration
  guide. The cause is found, the integration test passes, packages 12 to
  16 follow.
- day 3 early: the full suite, one regression, fixed, and the answer.

    python demo/longrun/make_trace.py                   # writes traces/ledger_v2__agent-3day.json and
                                                        # ledger_v2__agent-guarded.json (the same agent, guarded)
    python demo/longrun/make_trace.py --live DIR --pace 0.002   # streams it as a live trace, for the hub

Deterministic: the same seed writes the same bytes.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STARTED_AT = 1789373520  # 2026-09-14 08:12:00 UTC
DAY = 86400.0


class Run:
    def __init__(self, seed: int = 7) -> None:
        self.r = random.Random(seed)
        self.t = 0.0
        self.steps: list = []

    # ---------------------------------------------------------------- clock
    def gap(self, mean: float = 3.0) -> None:
        self.t += self.r.lognormvariate(math.log(mean), 0.6)

    def at(self, day: int, hhmm: str) -> None:
        h, m = (int(x) for x in hhmm.split(":"))
        target = day * DAY + h * 3600 + m * 60 - (8 * 3600 + 12 * 60)
        self.t = max(self.t, target)

    def idle(self, seconds: float) -> None:
        self.t += seconds

    # ---------------------------------------------------------------- steps
    def step(self, type_: str, name: str, inp: str, out: str = "", *, lat: float = 1.0, tokens: int = 0,
             error: bool = False, effect: str = "") -> None:
        lat = round(max(0.05, self.r.lognormvariate(math.log(lat), 0.4)), 3)
        s = {"index": len(self.steps), "type": type_, "name": name, "input": inp, "output": out,
             "started_s": round(self.t, 3), "latency_s": lat,
             "tokens": tokens or int(self.r.lognormvariate(math.log(900), 0.5))}
        if error:
            s["error"] = True
        if effect:
            s["effect"] = effect
        self.steps.append(s)
        self.t += lat

    def think(self, text: str) -> None:
        self.gap(2.0)
        self.step("reason", "reason", "", text, lat=4.0, tokens=int(self.r.uniform(300, 1400)))

    def read(self, path: str) -> None:
        self.gap()
        self.step("tool_call", "Read", path, f"{path}: {self.r.randint(40, 400)} lines", lat=0.3, effect="read")

    def grep(self, pat: str) -> None:
        self.gap()
        self.step("tool_call", "Grep", pat, f"{self.r.randint(1, 30)} matches", lat=0.4, effect="read")

    def edit(self, path: str, what: str) -> None:
        self.gap(4.0)
        self.step("tool_call", "Edit", f"{path}: {what}", "ok", lat=0.5, effect="write")

    def test(self, target: str, passed: bool, n: int = 12, why: str = "AssertionError") -> None:
        self.gap(3.0)
        if passed:
            out = f"{'.' * min(n, 40)}\n{n} passed in {self.r.uniform(8, 40):.1f}s"
        else:
            out = (f"FAILED {target}::test_rollup - {why}\n"
                   f"1 failed, {n - 1} passed in {self.r.uniform(8, 40):.1f}s")
        self.step("tool_call", "Bash", f"pytest -q {target}", out, lat=25.0, error=not passed)

    def shell(self, cmd: str, out: str = "") -> None:
        self.gap()
        self.step("tool_call", "Bash", cmd, out or "ok", lat=1.5)


def package(run: Run, k: int, tries: int = 1) -> None:
    """One package moved to v2: look, change, test until it passes."""
    mod = f"ledger/pkg{k:02d}"
    run.think(f"Package {k}: find its v1 calls.")
    run.grep(f"v1\\. {mod}")
    for _ in range(run.r.randint(2, 5)):
        run.read(f"{mod}/{run.r.choice(['api', 'models', 'rollup', 'io', 'client'])}.py")
    burst_pause(run)  # a package is more than one sitting: the plan, then the change
    for attempt in range(tries + 1):
        for _ in range(run.r.randint(2, 6)):
            run.edit(f"{mod}/{run.r.choice(['api', 'rollup', 'client'])}.py", "v1 call -> v2")
            if run.r.random() < 0.4:
                run.read(f"{mod}/api.py")
        run.test(f"tests/{mod.replace('/', '_')}", passed=attempt == tries)
        if attempt < tries:
            run.think("One case still fails; the rounding moved.")
            run.read(f"{mod}/rollup.py")
    if run.r.random() < 0.5:
        run.shell("ruff check ledger", "All checks passed!")


def burst_pause(run: Run) -> None:
    run.idle(run.r.uniform(240, 1500))  # waiting on CI, a review bot, a rate limit


def loop_burst(run: Run) -> None:
    """The night's loop: the same reads, the same two edits in turn, the same failing test."""
    run.think("The integration rollup is off by two. Try the other rounding.")
    for f in ("ledger/pkg11/rollup.py", "ledger/pkg11/api.py", "tests/integration/test_ledger.py"):
        run.read(f)
    which = "ROUND_HALF_EVEN" if len(run.steps) % 2 else "ROUND_HALF_UP"
    run.edit("ledger/pkg11/rollup.py", f"rounding -> {which}")
    for _ in range(run.r.randint(4, 9)):
        run.test("tests/integration/test_ledger.py", passed=False, n=31, why="AssertionError: 1042 != 1040")
        if run.r.random() < 0.5:
            run.read("ledger/pkg11/rollup.py")


def guarded_tail(run: Run) -> None:
    """With ``agentdiff guard`` installed: the third identical failing run is refused,
    so the agent reads instead of looping, and finds the cause that evening."""
    run.test("tests/integration/test_ledger.py", passed=False, n=31, why="AssertionError: 1042 != 1040")
    run.gap(2.0)
    run.step("tool_call", "Bash", "pytest -q tests/integration/test_ledger.py",
             "agentdiff guard: you have run `pytest -q tests/integration/test_ledger.py` 2 times in a row and it "
             "failed each time (1 failed, 30 passed), with nothing changed in between.", lat=0.2, error=True)
    run.think("Refused: change something first. Read how v2 rounds before trying another mode.")
    run.read("docs/migration-v2.md")
    run.grep("round(")
    run.edit("ledger/pkg11/rollup.py", "round per line item")
    run.test("tests/integration/test_ledger.py", passed=True, n=31)


def build(seed: int = 7, guarded: bool = False) -> dict:
    run = Run(seed)
    run.step("plan", "plan", "Move all 16 ledger packages to the v2 API, keep tests green, run the full suite.",
             "Plan: 16 packages in order; test each; integration and full suite at the end.", lat=6.0, tokens=1800)
    # day 1 morning
    for k in range(1, 7):
        package(run, k, tries=run.r.randint(0, 2))
        burst_pause(run)
    run.at(0, "13:40")
    for k in range(7, 11):
        package(run, k, tries=run.r.randint(0, 2))
        burst_pause(run)
    # package 11: the integration test starts failing
    run.at(0, "17:20")
    package(run, 11, tries=0)
    run.test("tests/integration/test_ledger.py", passed=False, n=31, why="AssertionError: 1042 != 1040")
    if guarded:
        return finish(run, guarded, seed)
    # evening and night: the loop
    while run.t < 0 * DAY + (19 * 3600 + 5 * 60) - (8 * 3600 + 12 * 60):
        loop_burst(run)
        run.idle(run.r.uniform(10, 60))
    run.at(1, "00:30")
    while run.t < 1 * DAY + (7 * 3600 + 50 * 60) - (8 * 3600 + 12 * 60):
        loop_burst(run)
        run.idle(run.r.uniform(10, 60))
    # day 2: a person's note, the cause, progress again
    run.at(1, "09:15")
    run.step("plan", "plan", "Note from the reviewer: read docs/migration-v2.md, section 'rounding'.",
             "Re-plan: v2 rounds per line item, v1 per invoice; change the rollup, not the rounding mode.",
             lat=5.0, tokens=2200)
    run.read("docs/migration-v2.md")
    run.grep("round(")
    run.edit("ledger/pkg11/rollup.py", "round per line item")
    run.test("tests/integration/test_ledger.py", passed=True, n=31)
    return finish(run, guarded, seed)


def finish(run: Run, guarded: bool, seed: int) -> dict:
    if guarded:
        guarded_tail(run)
    burst_pause(run)
    for k in range(12, 17):
        package(run, k, tries=run.r.randint(0, 2))
        burst_pause(run)
    # the full suite, a regression, the answer: day 3 early, or the next morning when guarded
    run.at(1 if guarded else 2, "06:00")
    run.test("tests", passed=False, n=412, why="AssertionError: pkg03 totals")
    run.read("ledger/pkg03/rollup.py")
    run.edit("ledger/pkg03/rollup.py", "per-line rounding here too")
    run.test("tests/ledger_pkg03", passed=True)
    run.test("tests", passed=True, n=412)
    run.gap(5.0)
    run.step("answer", "answer", "", "All 16 packages on v2; 412 tests pass.", lat=3.0, tokens=600)
    steps = run.steps
    tokens = sum(s["tokens"] for s in steps)
    name = "agent-guarded" if guarded else "agent-3day"
    return {
        "schema_version": 1, "trace_id": f"ledger_v2__{name}", "run_id": "r1", "started_at": STARTED_AT,
        "agent": {"name": name, "model": "sim-longrun", "version": "synthetic"},
        "task": {"id": "ledger_v2", "prompt": "Move all 16 ledger packages to the v2 API and keep the tests green.",
                 "expected": None},
        "outcome": {"success": True, "answer": steps[-1]["output"], "score": None, "termination": "agent_stop"},
        "totals": {"input_tokens": 0, "output_tokens": tokens, "cost_usd": 0.0,
                   "latency_s": round(steps[-1]["started_s"] + steps[-1]["latency_s"], 3)},
        "steps": steps,
        "harness": {"adapter": "synthetic", "graded_by": "its final full-suite check",
                    "note": "SYNTHETIC: a generated three-day run of a coding agent; every value invented to "
                            "exercise the reading of long-running agents (demo/longrun/make_trace.py)"
                            + ("; this one with agentdiff guard installed" if guarded else "")},
    }


def live(trace: dict, out: Path, pace: float) -> None:
    """Write the trace as it would grow: steps appear at ``pace`` real seconds per run-second."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{trace['trace_id']}.live.json"
    frame = dict(trace, steps=[], in_progress=True)
    frame["outcome"] = {"success": None, "answer": "", "score": None}
    last = 0.0
    for s in trace["steps"]:
        wait = (s["started_s"] - last) * pace
        if wait > 0:
            time.sleep(min(wait, 2.0))  # a night of idle should not take a night to watch
        last = s["started_s"]
        frame["steps"].append(s)
        # the totals so far, not the run's final ones
        frame["totals"] = dict(trace["totals"], latency_s=round(s["started_s"] + s["latency_s"], 3),
                               output_tokens=sum(x["tokens"] for x in frame["steps"]))
        if len(frame["steps"]) % 25 == 0 or wait > 0.5:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(frame), encoding="utf-8")
            tmp.replace(path)
    path.unlink(missing_ok=True)
    (out / f"{trace['trace_id']}.json").write_text(json.dumps(trace), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("-o", "--out", default=str(HERE / "traces"))
    ap.add_argument("--live", default=None, metavar="DIR", help="stream it into DIR as a live trace")
    ap.add_argument("--pace", type=float, default=0.002, help="--live: real seconds per run-second")
    args = ap.parse_args()
    if args.live:
        live(build(args.seed), Path(args.live), args.pace)
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for guarded in (False, True):
        trace = build(args.seed, guarded)
        path = out / f"{trace['trace_id']}.json"
        path.write_text(json.dumps(trace, separators=(",", ":")), encoding="utf-8")
        print(f"{path}: {len(trace['steps'])} steps over {trace['totals']['latency_s'] / 3600:.1f} h")


if __name__ == "__main__":
    main()
