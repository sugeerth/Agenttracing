"""Multi-run demo traces for AgentDiff stability analysis (schema v9).

Run from the repo root:

    python demo/runs/generate_runs.py

Writes 3 runs (r1, r2, r3) x 2 flagship agents x 8 tasks = 48 traces to
demo/runs/traces/<task_id>__<agent_name>__<run_id>.json, derived from the
hand-authored flagship trajectories in demo/agents.py.

Engineered stability matrix
---------------------------
- Benign noise on ALL traces: +/-3-8% per-step token/latency jitter with
  totals and cost recomputed exactly per the simulator's cost model, plus
  synonym-level wording variation in 1-2 steps (never the first two steps,
  so the canonical alignment prefix stays intact).
- Systematic (reproduce 3/3): atlas-v2's t07 tool_execution failure (same
  bad regex every run); bolt-v3's t01 and t06 retrieval failures (same bad
  domain, same first-divergence step index).
- Flaky: bolt-v3 on t02 — r1/r3 take the forum detour and recover, r2 runs
  clean like atlas (divergence is flaky, outcome stable-success). bolt-v3
  on t05 — r1/r2 fail with the naive-calculator wrong tool, r3 uses
  timezone-aware datetime math and succeeds (flaky outcome, 1/3 pass).
- atlas-v2 is fully stable everywhere else (stable-pass).

Deterministic (constant string seeds, no wall clock) and idempotent.
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent      # demo/runs
_REPO = _HERE.parent.parent                  # repo root
for _p in (str(_REPO), str(_REPO / "demo"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tasks import TASKS_BY_ID  # noqa: E402  (demo/tasks.py)
from simulator import (  # noqa: E402
    COST_PER_INPUT_TOKEN,
    COST_PER_OUTPUT_TOKEN,
    TrajectoryBuilder,
    write_trajectory,
)
import agents as flagship_agents  # noqa: E402  (demo/agents.py)
from deepcompare.trace import Trajectory  # noqa: E402

TRACES_DIR = _HERE / "traces"
RUNS_MD = _HERE / "RUNS.md"
RUNS = ("r1", "r2", "r3")

# Synonym-level rewrites for benign wording noise. Chosen so that every
# task's step texts (index >= 2) contain at least one match, and so that no
# substitution touches a fact, figure, or source domain.
SYNONYMS = [
    ("Selected", "Chose"),
    ("per the", "according to the"),
    ("Confirmed by", "Corroborated by"),
    ("up 11% year over year", "an 11% gain year over year"),
    ("cheaper by", "less expensive by"),
    ("journey time", "travel time"),
    ("the dependency bump", "the version bump"),
    ("breaking changes", "compatibility-breaking changes"),
    ("official", "canonical"),
    ("release notes", "release notes page"),
]


def _base_traces() -> dict[tuple[str, str], dict]:
    """Flagship trajectories keyed by (task_id, agent_name)."""
    return {(t["task"]["id"], t["agent"]["name"]): t
            for t in flagship_agents.build_all()}


def _rebuild_as(agent_info: dict, task_id: str, source: dict) -> dict:
    """Re-emit a source trajectory's content under another agent identity,
    recomputing tokens/latency/totals through the simulator."""
    b = TrajectoryBuilder(agent_info, TASKS_BY_ID[task_id])
    for s in source["steps"][:-1]:
        b.step(s["type"], s["name"], s["input"], s["output"],
               quality=s["quality"], note=s["note"])
    last = source["steps"][-1]
    b.answer(last["output"], success=source["outcome"]["success"],
             score=source["outcome"]["score"], input=last["input"],
             quality=last["quality"], note=last["note"])
    return b.build()


def _make_run(source: dict, agent: str, task_id: str, run: str) -> dict:
    """One run variant: benign wording + token/latency jitter, exact totals,
    run_id and per-run trace_id set."""
    tr = copy.deepcopy(source)
    rng = random.Random(f"runs|{agent}|{task_id}|{run}")
    steps = tr["steps"]
    n = len(steps)

    # --- light wording variation in 1-2 steps, never steps 0-1 ------------
    target = rng.choice((1, 2))
    order = list(range(2, n))
    rng.shuffle(order)
    changed = 0
    for i in order:
        if changed >= target:
            break
        step = steps[i]
        syns = SYNONYMS[:]
        rng.shuffle(syns)
        done = False
        for old, new in syns:
            for field in ("output", "input"):
                if old in step[field]:
                    step[field] = step[field].replace(old, new, 1)
                    if i == n - 1 and field == "output":
                        tr["outcome"]["answer"] = step["output"]
                    changed += 1
                    done = True
                    break
            if done:
                break

    # --- +/-3-8% token/latency jitter per step ----------------------------
    tok_sum = 0
    lat_sum = 0.0
    for step in steps:
        tf = 1.0 + rng.choice((-1, 1)) * rng.uniform(0.03, 0.08)
        lf = 1.0 + rng.choice((-1, 1)) * rng.uniform(0.03, 0.08)
        step["tokens"] = max(1, round(step["tokens"] * tf))
        step["latency_s"] = round(step["latency_s"] * lf, 2)
        tok_sum += step["tokens"]
        lat_sum += step["latency_s"]

    # --- recompute totals exactly per the simulator's cost model ----------
    i_old = tr["totals"]["input_tokens"]
    o_old = tr["totals"]["output_tokens"]
    i_new = round(tok_sum * i_old / (i_old + o_old))
    o_new = tok_sum - i_new
    tr["totals"] = {
        "input_tokens": i_new,
        "output_tokens": o_new,
        "cost_usd": round(i_new * COST_PER_INPUT_TOKEN
                          + o_new * COST_PER_OUTPUT_TOKEN, 6),
        "latency_s": round(lat_sum, 2),
    }

    tr["trace_id"] = f"{task_id}__{agent}__{run}"
    tr["run_id"] = run
    return tr


def generate() -> list[dict]:
    base = _base_traces()
    bolt_info = flagship_agents.AGENT_B

    # Flaky-behavior alternates for bolt-v3, built from atlas's clean paths.
    bolt_t02_clean = _rebuild_as(bolt_info, "t02_cve_libfoo",
                                 base[("t02_cve_libfoo", "atlas-v2")])
    bolt_t05_success = _rebuild_as(bolt_info, "t05_flight_duration",
                                   base[("t05_flight_duration", "atlas-v2")])

    traces = []
    for (task_id, agent), source in sorted(base.items()):
        for run in RUNS:
            src = source
            if agent == "bolt-v3" and task_id == "t02_cve_libfoo" and run == "r2":
                src = bolt_t02_clean          # flaky divergence, stable pass
            if agent == "bolt-v3" and task_id == "t05_flight_duration" and run == "r3":
                src = bolt_t05_success        # flaky outcome, 1/3 pass
            traces.append(_make_run(src, agent, task_id, run))
    return traces


def verify(traces: list[dict], base: dict) -> None:
    by_key = {}
    for tr in traces:
        t = Trajectory.from_json(tr)          # schema validation
        assert t.steps[-1].type == "answer"
        assert tr["run_id"] in RUNS, tr["trace_id"]
        assert tr["trace_id"].endswith(f"__{tr['run_id']}")
        assert tr["outcome"]["answer"] == tr["steps"][-1]["output"]
        # exact cost model
        exp = round(tr["totals"]["input_tokens"] * COST_PER_INPUT_TOKEN
                    + tr["totals"]["output_tokens"] * COST_PER_OUTPUT_TOKEN, 6)
        assert exp == tr["totals"]["cost_usd"], tr["trace_id"]
        assert (tr["totals"]["input_tokens"] + tr["totals"]["output_tokens"]
                == sum(s["tokens"] for s in tr["steps"])), tr["trace_id"]
        by_key[(tr["task"]["id"], tr["agent"]["name"], tr["run_id"])] = tr

    def runs_of(task, agent):
        return [by_key[(task, agent, r)] for r in RUNS]

    # canonical prefix: wording of the first two steps is never varied
    # (t02-r2 / t05-r3 intentionally follow atlas's clean path instead).
    for tr in traces:
        task_id, agent, run = tr["task"]["id"], tr["agent"]["name"], tr["run_id"]
        src = base[(task_id, agent)]
        if (agent, task_id, run) in (("bolt-v3", "t02_cve_libfoo", "r2"),
                                     ("bolt-v3", "t05_flight_duration", "r3")):
            src = base[(task_id, "atlas-v2")]
        for i in range(min(2, len(tr["steps"]))):
            for f in ("type", "name", "input", "output"):
                assert tr["steps"][i][f] == src["steps"][i][f], \
                    f"{tr['trace_id']}: step {i} field {f} varied"

    # systematic: atlas t07 fails 3/3 with the identical bad regex
    t07 = runs_of("t07_build_failure", "atlas-v2")
    assert all(not t["outcome"]["success"] for t in t07)
    assert len({t["steps"][3]["input"] for t in t07}) == 1
    assert all(t["steps"][3]["type"] == "tool_call" for t in t07)

    # systematic: bolt t01/t06 fail 3/3 from the same bad domain at index 2
    for task, domain in (("t01_acme_revenue", "financeblog.net"),
                         ("t06_bls_unemployment", "econwire.com")):
        rs = runs_of(task, "bolt-v3")
        assert all(not t["outcome"]["success"] for t in rs)
        assert all(domain in t["steps"][2]["output"] for t in rs)
        assert all(domain in t["steps"][3]["input"] for t in rs)

    # flaky divergence, stable pass: bolt t02
    t02 = {t["run_id"]: t for t in runs_of("t02_cve_libfoo", "bolt-v3")}
    assert all(t["outcome"]["success"] for t in t02.values())
    for r in ("r1", "r3"):
        assert "forum.libfoo.org" in t02[r]["steps"][2]["output"]
        assert len(t02[r]["steps"]) == 11
    assert "nvd.nist.gov" in t02["r2"]["steps"][2]["output"]
    assert len(t02["r2"]["steps"]) == 6

    # flaky outcome: bolt t05 fails r1/r2, succeeds r3 with the right tool
    t05 = {t["run_id"]: t for t in runs_of("t05_flight_duration", "bolt-v3")}
    assert not t05["r1"]["outcome"]["success"]
    assert not t05["r2"]["outcome"]["success"]
    assert t05["r3"]["outcome"]["success"]
    assert any(s["name"] == "datetime_diff" for s in t05["r3"]["steps"])
    assert all(s["name"] != "datetime_diff" for s in t05["r1"]["steps"])

    # atlas stable-pass everywhere except t07
    for task in sorted({k[0] for k in base}):
        if task == "t07_build_failure":
            continue
        assert all(t["outcome"]["success"]
                   for t in runs_of(task, "atlas-v2")), task


MATRIX_ROWS = [
    ("t01_acme_revenue", "stable-pass", "stable-fail",
     "systematic (retrieval @ step 2, 3/3 financeblog.net)"),
    ("t02_cve_libfoo", "stable-pass", "stable-pass (FLAKY divergence)",
     "variable (forum detour in r1/r3 only; r2 clean)"),
    ("t03_saas_pricing", "stable-pass", "stable-pass",
     "systematic (stopping/over-search @ step 2, 3/3)"),
    ("t04_rope_paper", "stable-pass", "stable-pass", "none (no divergence)"),
    ("t05_flight_duration", "stable-pass", "FLAKY (1/3 pass)",
     "variable (wrong tool @ step 1 in r1/r2; r3 correct)"),
    ("t06_bls_unemployment", "stable-pass", "stable-fail",
     "systematic (retrieval @ step 2, 3/3 econwire.com)"),
    ("t07_build_failure", "stable-fail", "stable-pass",
     "systematic (tool_execution @ step 3, 3/3 same bad regex — atlas side)"),
    ("t08_changelog_diff", "stable-pass", "stable-pass", "none (no divergence)"),
]


def write_runs_md() -> None:
    lines = [
        "# Multi-run demo traces (v9 stability analysis)",
        "",
        "Generated by `python demo/runs/generate_runs.py` — deterministic.",
        "3 runs (r1-r3) x 2 flagship agents x 8 tasks = 48 traces, derived",
        "from the demo/agents.py flagship trajectories with +/-3-8%",
        "token/latency jitter and synonym-level wording noise (steps 0-1",
        "always keep canonical wording).",
        "",
        "## Engineered stability matrix",
        "",
        "| task | atlas-v2 | bolt-v3 | divergence reproducibility |",
        "|------|----------|---------|----------------------------|",
    ]
    for task, a, b, div in MATRIX_ROWS:
        lines.append(f"| {task} | {a} | {b} | {div} |")
    lines += [
        "",
        "Reading guide: t01/t06 are bolt's reproducible retrieval failures",
        "(systematic — fix the agent, not the flake tracker); t07 is atlas's",
        "reproducible tool_execution failure. t05 is bolt's genuinely flaky",
        "outcome (1/3 pass — the r3 run picks timezone-aware datetime math).",
        "t02 shows a flaky divergence beneath a stable-success outcome:",
        "run-pair comparisons should flag the forum detour as variable even",
        "though every run answers correctly. t03's over-search reproduces",
        "3/3 (systematic stopping divergence); t04/t08 are pure noise.",
        "",
    ]
    RUNS_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    base = _base_traces()
    traces = generate()
    verify(traces, base)
    for tr in traces:
        write_trajectory(tr, TRACES_DIR / f"{tr['trace_id']}.json")
    write_runs_md()

    print(f"{len(traces)} traces written to {TRACES_DIR}")
    for task, a, b, div in MATRIX_ROWS:
        print(f"  {task:<22} atlas={a:<12} bolt={b:<28} div={div}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
