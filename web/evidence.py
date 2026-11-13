"""The data behind /agentdiff/evidence/: what AgentDiff has measured about running agents again and again.

Three kinds of figure, each labelled where it is shown:

- computed here, exactly, from the repository: the bound a run of straight passes leaves
  (``statistics.fail_rate_at_most``), the passes it takes to rule a rate out (``runs_to_rule_out``), and the
  recorded hard-six evolution counted task by task from its traces;
- simulated here, seeded per point so any one point can be rerun alone: how often the rerun-flip range holds
  (synthetic, and said so);
- quoted from the project's own records, with where each comes from: the table of real evolutions in
  docs/SELF_EVOLVE.md, the audits in docs/RESEARCH_LOG.md, and the grader-strength run of 2026-11-05.

No model names appear: the agent is "the agent", and the build refuses a file that names a model.
Standard library only.
"""

from __future__ import annotations

import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentdiff.statistics import central_range, fail_rate_at_most, poisson_binomial, runs_to_rule_out  # noqa: E402

HARD_SIX = ROOT / "demo" / "selfevolve" / "examples" / "haiku-hard-confirms"
SELF_EVOLVE_MD = "docs/SELF_EVOLVE.md, the table of real runs"
LOG_MD = "docs/RESEARCH_LOG.md"

#: every evolution recorded with a real agent (docs/SELF_EVOLVE.md, "Real runs")
EVOLUTIONS = [
    ("3 tasks, tests in the workspace", 6, None, 6, 6, 0, 0, 0, "Nothing to fix: it converged at the first generation."),
    ("The same 3 tasks, graders held out", 6, None, 6, 6, 0, 0, 0, "It converged at the first generation."),
    ("6 tasks, graders held out", 12, 0.51, 10, 12, 0, 0, 0,
     "It stopped without a change: one task failed both runs, and nothing the harness can change answers it."),
    ("The same 6 tasks, a newer agent version, exploring", 12, 0.05, 12, 12, 0, 0, 0, "Nothing to learn: every run passed."),
    ("The hard 6 tasks, exploring", 24, 0.24, 11, 12, 1, 1, 0,
     "The edge-case instruction was kept, 11/12 to 12/12. One task moved: weak evidence, and the run says so."),
    ("The hard 6 tasks, exploring and confirming", 42, 0.45, 17, 18, 1, 1, 1,
     "The edge-case instruction was kept, and held when the task that moved ran again (2/3 to 3/3)."),
    ("A real library's history, generic changes only", 100, 1.29, 8, 12, 3, 0, 0,
     "All three explored changes were reverted: two did not hold on re-runs, one broke a task that always passed."),
    ("The same history, the project's own guide first", 76, 1.24, 6, 12, 3, 2, 2,
     "Reading the project's guide was kept and held; the edge-case instruction on top of it was kept and held."),
]

#: every tested change with counts on record (the same table, and the hard-six record on disk)
CHANGES = [
    ("Test every edge case the task states", "The hard 6 tasks, exploring", 11, 12, 12, 12, "kept", None, "recorded"),
    ("Test every edge case the task states", "The hard 6 tasks, exploring and confirming", 19, 21, 21, 21, "kept", "held", "recorded"),
    ("Read the project's guide first", "A real library's history, the guide first", 6, 14, 8, 14, "kept", "held", "recorded"),
    ("Run the project's own tests", "A real library's history, the guide first", 7, 12, 7, 12, "reverted", None, "recorded"),
    ("Test every edge case, on top of the guide", "A real library's history, the guide first", 12, 18, 16, 18, "kept", "held", "recorded"),
    ("Run the check before saying the work is done", "A scripted stand-in", 0, 6, 6, 6, "kept", None, "synthetic"),
]

#: each time a daily run found one of AgentDiff's own numbers wrong (docs/RESEARCH_LOG.md)
CHECKS = [
    ("2026-10-29", "Cost interval on one agent under two names", "False alarms on random re-deals", 0.11, 0.032, 0.05,
     "lower", "The percentile bootstrap claimed a gap between an agent and itself; a jackknife over tasks replaced it.", "recorded"),
    ("2026-10-28", "pass^k interval on small, mostly-passing suites", "Coverage of the true pass^k", 0.68, 0.97, 0.95,
     "higher", "A task bootstrap gave zero-width intervals on 31% of such suites; an envelope replaced it.", "synthetic"),
    ("2026-10-25", "Replay check of a decisive step", "A do-nothing fix 'verified'", 0.242, 0.091, 0.05, "lower",
     "Each replay now has a control replay with nothing changed; with 5 of each it falls to 0.042.", "synthetic"),
    ("2026-10-29", "Clustered error bar with 5 clusters", "Coverage of a 95% interval", 0.83, 0.87, 0.95, "higher",
     "t on G − 1 replaced z, and the report says the interval is still a lower bound under 20 clusters.", "synthetic"),
    ("2026-11-03", "Rerun-flip range with 3 runs a task", "Coverage of a 90% range", 0.73, None, 0.90, "higher",
     "It cannot be fixed at 3 runs: the range is withheld below 10 runs a task, and the reason printed.", "synthetic"),
    ("2026-11-04", "Per-run token ratio between two agents", "False alarms on null suites", 0.117, 0.061, 0.05, "lower",
     "An exact sign-flip test over tasks replaced the jackknife (worst settings shown).", "synthetic"),
    ("2026-10-22", "The diagnoser on traces without annotations", "Exact decisive steps, of 1,852", round(1344 / 1852, 4),
     round(1752 / 1852, 4), round(1350 / 1852, 4), "higher",
     "One simple rule scored 1,350 against the engine's 1,344; two later fixes took the engine to 1,752. The target is that rule.",
     "synthetic"),
]

#: run 23: each recent commit's held-out tests against small changes to its own lines (recorded on this repository)
GRADER_STRENGTH = [
    ("Runs still to make per task", 11, 12), ("What an unbroken record proves", 11, 12),
    ("How many verdicts a rerun would change", 10, 12), ("Web version: a repeatability dashboard", 4, 7),
    ("Web version: cookie-free hosting", 3, 5), ("Tokens, time and steps per run", 3, 12),
    ("Tokens, time and steps per run, tests strengthened", 10, 12),
]

#: one row per daily research-and-implement run (docs/RESEARCH_LOG.md)
RESEARCH_LOG = [
    ("2026-11-06", "Whether a run reached outside its workspace for the fix", "Grading",
     "0 of 212 recorded real runs did; a plain text search flagged 3, all wrongly."),
    ("2026-11-05", "How much a task's grader can tell apart", "Grading",
     "42 of 60 small changes caught across 6 commits; the weakest went from 3 to 10 of 12 once its gaps were tested."),
    ("2026-11-04 (2)", "The cost ratio checked again", "Statistics",
     "Its jackknife holds 2.5–5.9% where successes vary, so it stays."),
    ("2026-11-04", "Tokens, time and steps per run, with a test that holds", "Statistics",
     "The exact sign-flip test held at 2.4–6.1% on null suites; the jackknife ran to 11.7%."),
    ("2026-11-03", "How many verdicts a rerun would change", "Repeatability",
     "Expected flips are unbiased; the range holds only from about 10 runs a task (73% at 3)."),
    ("2026-11-02", "The runs each task still needs", "Repeatability",
     "130 to 208 more runs per agent to rule out a 10% failure rate."),
    ("2026-11-01", "What an unbroken record proves", "Repeatability",
     "Three straight passes still allow a 63% failure rate; 29 are needed to rule out 10%."),
    ("2026-10-31", "Cost per success over every run", "Statistics",
     "On the A/A suite the interval halves; over 2,000 re-deals it excluded 1 in 6.1%."),
    ("2026-10-30", "Forgetting across an evolution", "Evolution",
     "On four real evolutions nothing was forgotten; two tasks weakened."),
    ("2026-10-29 (4)", "An A/A test on real runs", "Statistics",
     "The cost bootstrap claimed a gap between an agent and itself (11% false alarms); its replacement: 3.2%."),
    ("2026-10-29 (3)", "A second real corpus of annotated runs", "Failure attribution",
     "Every pointer was a tool call, while 86% of annotated errors are on the model call before one."),
    ("2026-10-29 (2)", "Small-sample clustered error bars", "Statistics",
     "At equal cluster sizes the correction changes nothing; with skewed sizes it adds up to 7.5 points."),
    ("2026-10-29", "An honest clustered error bar", "Statistics",
     "Coverage of a nominal 95% interval, z to t: 0.83 to 0.87 at 5 clusters."),
    ("2026-10-28", "A pass^k interval that holds its own estimate", "Repeatability",
     "The old interval excluded its own estimate on 2 of 20 real arms; the new one on none."),
    ("2026-10-27", "An interval on cost per success", "Statistics", "Recorded live suite: 1.84 times the cost, 1.45 to 2.24."),
    ("2026-10-26", "Read and measured, nothing built", "None", "Nothing new was worth building."),
    ("2026-10-25", "Control replays", "Failure attribution",
     "A do-nothing fix was 'verified' 24.2% of the time with 3 replays; 9.1% with 3 controls."),
    ("2026-10-24 (2)", "What each verdict's test could ever show", "Evolution",
     "On the recorded evolutions, power at most 0.025; 80% would take 53 to 161 tasks."),
    ("2026-10-24", "Same tool, other arguments; replay sets", "Failure attribution",
     "Exact decisive steps without annotations: 1,554 to 1,752 of 1,852."),
    ("2026-10-23", "A contested diagnosis names where the runs part", "Failure attribution",
     "Exact decisive steps: annotated 1,643 to 1,800; stripped 1,344 to 1,554."),
    ("2026-10-22", "The diagnoser against one-rule baselines", "Failure attribution",
     "Without annotations the engine scored 1,344 against 1,350 for one rule: a negative result, stated."),
    ("2026-10-21", "One test among several", "Evolution",
     "The 4 kept changes each had a 1-in-2 sign test; none would pass a family-wise correction."),
    ("2026-10-20", "OpenTelemetry both ways", "Tracing", "1,141 traces and 35,009 steps round-tripped exactly."),
    ("2026-10-19", "Which tasks can tell a change apart", "Evolution",
     "Over 9 recorded generations: 24% of tasks mixed, 61% always passed, 15% always failed."),
    ("2026-10-18", "Every kept change, tested again", "Evolution",
     "On a real library the edge-case instruction stays (6/12 to 9/12); reading the guide is dropped (9/12 both)."),
    ("2026-10-17", "A grader the work cannot talk round", "Grading", "Re-grading 176 recorded runs changed no verdict."),
    ("2026-10-16 (2)", "A grader must agree with itself", "Grading", "6 tasks checked once and five times; none of the graders is flaky."),
    ("2026-10-16", "Stop a changed arm that cannot be kept", "Evolution",
     "Skips 17 to 35% of a harmful change's runs; no verdict changed in 2,700 draws."),
]

#: the task-rate mixes of the coverage simulation (synthetic)
MIXES = ("Mostly settled tasks", "Rates spread out", "All fairly reliable")
COVERAGE_RUNS = (2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 32)
COVERAGE_TASKS, COVERAGE_DRAWS, COVERAGE_TARGET, COVERAGE_GATE = 30, 600, 0.90, 10


def _draw(rng: random.Random, mix: int) -> float:
    if mix == 0:
        u = rng.random()
        return 1.0 - rng.random() * 0.04 if u < 0.45 else (rng.random() * 0.04 if u < 0.65 else rng.random())
    return rng.random() if mix == 1 else 1.0 - rng.random() * 0.10


def coverage_point(mix: int, runs: int, draws: int = COVERAGE_DRAWS) -> float:
    """Synthetic, seeded by (mix, runs): the share of fresh reruns whose flip count lands in the 90% range
    ``reliability.rerun_flips`` would give from ``runs`` runs of each of 30 tasks."""
    rng = random.Random(1_000 * (mix + 1) + runs)
    hit = 0
    for _ in range(draws):
        ps = [_draw(rng, mix) for _ in range(COVERAGE_TASKS)]
        chances = []
        for p in ps:
            c = sum(rng.random() < p for _ in range(runs))
            chances.append(2 * c * (runs - c) / (runs * (runs - 1)))
        lo, hi = central_range(poisson_binomial(chances), COVERAGE_TARGET)
        fresh = sum((rng.random() < p) != (rng.random() < p) for p in ps)
        hit += lo <= fresh <= hi
    return round(hit / draws, 3)


def hard_six() -> list:
    """The recorded hard-six evolution, task by task: passes before and after the kept change, first runs and
    the re-runs, counted from the traces on disk."""
    arms = {"g0-h0": ("before", "first runs"), "g0-h0-confirm": ("before", "run again"),
            "g0-h1": ("after", "first runs"), "g0-h1-confirm": ("after", "run again")}
    rows = []
    for arm, (when, phase) in arms.items():
        per = {}
        for path in sorted(glob.glob(str(HARD_SIX / arm / "traces" / "*.json"))):
            t = json.loads(Path(path).read_text(encoding="utf-8"))
            row = per.setdefault(t["task"]["id"], [0, 0])
            row[0] += bool(t["outcome"]["success"])
            row[1] += 1
        rows += [{"task": task, "when": when, "phase": phase, "passed": p, "runs": n} for task, (p, n) in sorted(per.items())]
    return rows


def evidence(coverage_draws: int = COVERAGE_DRAWS) -> dict:
    evolutions = [{"evolution": e, "runs": r, "cost_usd": c, "first_passed": fp, "first_runs": fr, "tried": t,
                   "kept": k, "held": h, "outcome": o} for e, r, c, fp, fr, t, k, h, o in EVOLUTIONS]
    logged = [e["cost_usd"] for e in evolutions if e["cost_usd"] is not None]
    return {
        "sources": {"evolutions": SELF_EVOLVE_MD, "checks": LOG_MD, "research_log": LOG_MD,
                    "grader_strength": "the grader-strength run on this repository's own history (2026-11-05)",
                    "hard_six": "demo/selfevolve/examples, the hard-six evolution's traces",
                    "bounds": "agentdiff.statistics, exact", "coverage": "synthetic, seeded simulation in web/evidence.py"},
        "totals": {"evolutions": len(evolutions), "runs": sum(e["runs"] for e in evolutions),
                   "cost_usd": round(sum(logged), 2), "cost_logged": len(logged),
                   "tried": sum(e["tried"] for e in evolutions), "kept": sum(e["kept"] for e in evolutions),
                   "held": sum(e["held"] for e in evolutions)},
        "evolutions": evolutions,
        "changes": [{"change": c, "evolution": e, "before_passed": bp, "before_runs": br, "after_passed": ap,
                     "after_runs": ar, "before": round(bp / br, 4), "after": round(ap / ar, 4), "verdict": v,
                     "held": h, "data": d} for c, e, bp, br, ap, ar, v, h, d in CHANGES],
        "hard_six": hard_six(),
        "checks": [{"date": d, "check": c, "measure": m, "found": f, "now": n, "target": t, "better": b,
                    "what_changed": w, "data": k} for d, c, m, f, n, t, b, w, k in CHECKS],
        "grader_strength": [{"commit": c, "caught": k, "tried": t} for c, k, t in GRADER_STRENGTH],
        "bounds": [{"runs": n, "fail_rate_at_most": fail_rate_at_most(n)} for n in range(1, 61)],
        "rule_out": [{"failure_rate": f, "straight_passes": runs_to_rule_out(f)} for f in (0.3, 0.2, 0.1, 0.05, 0.02, 0.01)],
        "coverage": {"target": COVERAGE_TARGET, "gate": COVERAGE_GATE, "tasks": COVERAGE_TASKS, "draws": coverage_draws,
                     "points": [{"mix": name, "runs": n, "coverage": coverage_point(i, n, coverage_draws)}
                                for i, name in enumerate(MIXES) for n in COVERAGE_RUNS]},
        "research_log": [{"date": d, "added": a, "area": ar, "result": r} for d, a, ar, r in RESEARCH_LOG],
    }


if __name__ == "__main__":
    print(json.dumps(evidence(), indent=1))
