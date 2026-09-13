"""Generate the RL demos (SYNTHETIC): two policies over a set of tasks.

Two sets are written. ``demo/rl/traces`` is the small one — two tasks,
three runs each — kept small so the shipped tests over it stay fast.
``demo/rl/train`` is the training ground proper: six tasks, eight runs
each, 96 episodes, which is the point at which the engine's own runs
advisory stops calling the sample insufficient and a bootstrap interval
over the tasks starts meaning something.

Every step carries a ``reward`` paid by a scripted environment — 0 for a thinking step, −0.1 per
tool call, −1 on an error, +1 when a piece of evidence the task needs
appears, +5 / −5 at the answer — and every thinking step a ``value``
estimate, so the RL layer reads these traces as *recorded* rather than
shaping a reward from the labels. ``policy-v1`` is the weaker policy: it
needs more attempts per item, errs more often in the verifier and misses
evidence more; ``policy-v2`` is stronger. A ``verifier`` sub-agent gives
every run a second lane. 30–60 steps per run; every number is invented
and labelled so.

    python demo/rl/generate_rl.py            # both sets, in place
    python demo/rl/generate_rl.py --set demo|train [out_dir]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deepcompare.record import Recorder  # noqa: E402

NOTE = "SYNTHETIC: a generated RL episode whose rewards were paid by a scripted environment; every value invented"
TOOL_COST = -0.1
ERROR_REWARD = -1.0
EVIDENCE_REWARD = 1.0
ANSWER_REWARD = 5.0
RUNS = ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")
POLICIES = {"policy-v1": {"strong": False, "hit": 0.72, "error": 0.35, "checks": 4},
            "policy-v2": {"strong": True, "hit": 0.985, "error": 0.1, "checks": 3}}
#: the small set: what the shipped tests pin, unchanged
DEMO_TASKS, DEMO_RUNS = 2, 3

TASKS = [
    {"id": "rl01_ledger_reconcile",
     "prompt": "Reconcile the September ledger: find every invoice whose total disagrees with the payments table and report the corrected total.",
     "expected": "5 invoices corrected; total 48,120.50",
     "files": ["ledger/invoices.csv", "ledger/payments.csv", "ledger/rates.json", "ledger/README.md", "ledger/schema.sql"],
     "items": [("INV-2041", "INV-2041: invoice 1,250.00 vs payment 1,205.00"),
               ("INV-2077", "INV-2077: invoice 980.00 vs payment 890.00"),
               ("INV-2090", "INV-2090: invoice 3,400.00 vs payment 3,040.00"),
               ("INV-2113", "INV-2113: invoice 610.50 vs payment 601.50"),
               ("INV-2150", "INV-2150: invoice 2,200.00 vs payment 2,020.00")],
     "answer": "5 invoices corrected; total 48,120.50",
     "wrong": "4 invoices corrected; total 47,930.50"},
    {"id": "rl02_flaky_test",
     "prompt": "Find why test_parse_dates fails on CI only, fix it, and report the passing count.",
     "expected": "timezone default fixed; 412 passing",
     "files": ["src/dates.py", "tests/test_dates.py", "ci/config.yml", "src/config.py", "tests/conftest.py"],
     "items": ("TZ=UTC", "ci/config.yml: TZ=UTC set on the runner, unset locally"),
     "answer": "timezone default fixed; 412 passing",
     "wrong": "test marked flaky; 411 passing"},
]
TASKS[1]["items"] = [("TZ on CI", "ci/config.yml: TZ=UTC on the runner"),
                     ("local tz", "dev machines run TZ=Europe/Berlin"),
                     ("parse path", "dates.parse() uses datetime.now() without a tz"),
                     ("fixture", "conftest freezes the clock at 2026-03-29 01:30"),
                     ("dst edge", "2026-03-29 01:30 does not exist in Europe/Berlin")]

#: the four tasks that only the training set uses. They vary in length
#: (three to seven facts), in how much verification they reward, and in
#: which policy they suit: rl05 punishes the policy that checks less, so
#: the per-task deltas do not all point one way and "which policy is
#: better" stays a question the statistics have to answer.
TASKS += [
    {"id": "rl03_flag_rollout",
     "prompt": "A feature flag rollout dropped conversion by 4%. Find which variant and which cohort, and report the safe rollback target.",
     "expected": "variant B on mobile-safari; roll back to 20%",
     "files": ["flags/rollout.yaml", "analytics/conversion.csv", "flags/history.json",
               "analytics/cohorts.csv", "services/router.go", "flags/README.md"],
     "probes": ("variant", "cohort", "conversion", "rollback"),
     "items": [("variant split", "flags/rollout.yaml: variant B at 50% since 2026-03-02"),
               ("cohort", "analytics/cohorts.csv: mobile-safari is 31% of traffic"),
               ("drop", "analytics/conversion.csv: mobile-safari B converts 4.1% below A"),
               ("prior safe point", "flags/history.json: 20% held for 9 days with no drop")],
     "answer": "variant B on mobile-safari; roll back to 20%",
     "wrong": "variant B overall; roll back to 0%"},
    {"id": "rl04_query_regression",
     "prompt": "The nightly report query went from 40s to 22 minutes. Find the cause and report the fix and the expected runtime.",
     "expected": "missing index on events(created_at); 47s after adding it",
     "files": ["db/schema.sql", "reports/nightly.sql", "db/migrations/0142_drop_index.sql",
               "ops/slow_query.log", "db/stats.json", "reports/README.md", "ops/explain.txt"],
     "probes": ("index", "events", "created_at", "explain"),
     "items": [("the migration", "db/migrations/0142_drop_index.sql dropped idx_events_created_at"),
               ("the plan", "ops/explain.txt: seq scan over 41M rows on events"),
               ("the query", "reports/nightly.sql filters events on created_at"),
               ("the timing", "ops/slow_query.log: 22m04s, was 40s before 0142"),
               ("the estimate", "db/stats.json: index scan estimated at 47s"),
               ("no other change", "no other migration landed in that window")],
     "answer": "missing index on events(created_at); 47s after adding it",
     "wrong": "query needs a rewrite; runtime unknown"},
    {"id": "rl05_incident_postmortem",
     "prompt": "Write the postmortem for the 11 March checkout outage: the trigger, the contributing factors, and the one change that would have prevented it.",
     "expected": "expired intermediate cert; pin the chain and alert at 14 days",
     "files": ["incidents/2026-03-11.md", "ops/certs.json", "alerts/rules.yaml",
               "services/checkout/deploy.yaml", "ops/oncall.log", "alerts/history.csv",
               "services/checkout/health.go", "ops/renewal.cron"],
     "probes": ("cert", "expiry", "alert", "checkout"),
     # a long task with a shallow answer: the policy that runs fewer
     # verification checks pays for it here, so v2 is worse at this one
     "adj": {"policy-v2": -0.40, "policy-v1": 0.16},
     "items": [("the trigger", "ops/certs.json: intermediate expired 2026-03-11T02:14Z"),
               ("no alert", "alerts/rules.yaml has no rule on certificate expiry"),
               ("the cron", "ops/renewal.cron renews the leaf only, never the chain"),
               ("the blast radius", "incidents/2026-03-11.md: checkout down 71 minutes"),
               ("the health check", "health.go reports ok without validating the chain"),
               ("the oncall delay", "ops/oncall.log: 23 minutes to first human ack"),
               ("the precedent", "alerts/history.csv: the same expiry hit staging in January")],
     "answer": "expired intermediate cert; pin the chain and alert at 14 days",
     "wrong": "checkout deploy rolled a bad image; add a canary"},
    {"id": "rl06_api_contract",
     "prompt": "A client reports 422s from POST /orders since Tuesday. Say what changed and whether the server or the client is wrong.",
     "expected": "server tightened currency to ISO-4217; the server change is breaking",
     "files": ["api/openapi.yaml", "api/CHANGELOG.md", "services/orders/validate.py",
               "clients/js/order.ts", "ops/access.log"],
     "probes": ("currency", "422", "schema", "validate"),
     "items": [("the change", "validate.py now requires currency to match ^[A-Z]{3}$"),
               ("the client", "clients/js/order.ts sends 'usd' lower-case"),
               ("the contract", "api/openapi.yaml still documents currency as a free string")],
     "answer": "server tightened currency to ISO-4217; the server change is breaking",
     "wrong": "the client is sending an invalid currency; fix the client"},
]


def _value(rng: random.Random, expected_final: float, remaining: int) -> float:
    """A policy's value estimate at a thinking step: the expected answer
    reward less the tool cost still to pay, with noise."""
    return round(expected_final + TOOL_COST * remaining + rng.gauss(0.0, 0.6), 2)


def make(task: dict, agent: str, run: str, out: Path) -> Path:
    policy = POLICIES[agent]
    rng = random.Random(f"{task['id']}|{agent}|{run}")
    # A task may make one policy worse at it than its overall rate: a
    # stronger policy that checks less is not stronger everywhere, and a
    # training ground where every task points the same way teaches nothing.
    hit = min(0.999, max(0.05, policy["hit"] + task.get("adj", {}).get(agent, 0.0)))
    policy = dict(policy, hit=hit)
    expected_final = ANSWER_REWARD * (2 * policy["hit"] ** len(task["items"]) - 1)
    r = Recorder(task=task["id"], prompt=task["prompt"], agent=agent, model=f"sim-{agent}", version="synthetic",
                 expected=task["expected"], run_id=run, out_dir=out,
                 tools=[{"name": "grep", "effect": "read"}, {"name": "read_file", "effect": "read"},
                        {"name": "search", "effect": "read"}, {"name": "run_check", "effect": "read"}])
    with r:
        r.plan(f"Plan: inventory the inputs, locate each of the {len(task['items'])} facts the answer needs, verify, answer.",
               latency_s=1.4, tokens=90, reward=0.0, value=_value(rng, expected_final, 40))
        for name in task.get("probes", ("invoices", "payments", "totals", "mismatch")):
            r.tool("grep", {"pattern": name, "path": task["files"][0].split("/")[0]},
                   output=f"{rng.randint(3, 40)} lines match '{name}'", latency_s=rng.uniform(0.1, 0.4),
                   tokens=rng.randint(20, 50), reward=TOOL_COST)
        for f in task["files"]:
            r.read(f, output=f"contents of {f} ({rng.randint(40, 300)} lines)", name="read_file",
                   latency_s=rng.uniform(0.2, 0.6), tokens=rng.randint(60, 160), reward=TOOL_COST)
        found = 0
        for k, (query, evidence) in enumerate(task["items"]):
            attempts = 1 + (rng.random() < 0.3) if policy["strong"] else 1 + rng.randint(0, 2)
            hit = False
            for attempt in range(attempts):
                last = attempt == attempts - 1
                r.search(f"search: {query}" + (f" (attempt {attempt + 1})" if attempt else ""),
                         output=f"{rng.randint(1, 6)} candidate rows for {query}",
                         latency_s=rng.uniform(0.5, 1.5), tokens=rng.randint(30, 80), reward=TOOL_COST)
                hit = last and rng.random() < policy["hit"]
                r.read(f"row for {query}", name="read_file",
                       output=evidence if hit else f"no row matching {query}",
                       latency_s=rng.uniform(0.2, 0.7), tokens=rng.randint(40, 120),
                       reward=TOOL_COST + (EVIDENCE_REWARD if hit else 0.0))
            found += hit
            remaining = 2 * (len(task["items"]) - k - 1) + policy["checks"] + 2
            r.reason(f"{'Recorded' if hit else 'Could not confirm'} item {k + 1} of {len(task['items'])}: {query}",
                     latency_s=rng.uniform(0.4, 0.9), tokens=rng.randint(40, 90),
                     reward=0.0, value=_value(rng, expected_final, remaining))
        with r.span("verifier"):
            for j in range(policy["checks"]):
                err = rng.random() < policy["error"]
                r.tool("run_check", {"check": f"consistency-{j + 1}"},
                       output="error: check input malformed" if err else "ok",
                       error=True if err else None, latency_s=rng.uniform(1.0, 3.0), tokens=rng.randint(20, 40),
                       reward=ERROR_REWARD if err else TOOL_COST)
                if err:
                    r.reason(f"verifier: check {j + 1} rejected the input; retry with the corrected arguments",
                             latency_s=rng.uniform(0.3, 0.8), tokens=rng.randint(30, 60),
                             reward=0.0, value=_value(rng, expected_final, policy["checks"] - j + 1))
                    r.tool("run_check", {"check": f"consistency-{j + 1}", "retry": True}, output="ok",
                           latency_s=rng.uniform(1.0, 2.0), tokens=rng.randint(20, 40), reward=TOOL_COST)
        success = found == len(task["items"])
        r.reason("Compose the answer from the " + (f"{found} confirmed items" if success else f"{found} of {len(task['items'])} items confirmed"),
                 latency_s=rng.uniform(0.6, 1.2), tokens=rng.randint(50, 100), reward=0.0, value=_value(rng, expected_final, 1))
        r.answer(task["answer"] if success else task["wrong"], success=success, tokens=60, latency_s=1.1,
                 reward=ANSWER_REWARD if success else -ANSWER_REWARD)
    path = r.path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["harness"] = {"adapter": "synthetic", "graded_by": "exact-match", "note": NOTE}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write(tasks: list, runs: tuple, out: Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for task in tasks:
        for agent in POLICIES:
            for run in runs:
                path = make(task, agent, run, out)
                data = json.loads(path.read_text(encoding="utf-8"))
                n = len(data["steps"])
                assert 20 <= n <= 95, f"{path.name}: {n} steps"
                written.append((path.name, n, round(sum(s.get("reward", 0.0) for s in data["steps"]), 2), data["outcome"]["success"]))
    return written


def report(written: list, out: Path, verbose: bool) -> None:
    if verbose:
        for name, n, ret, ok in written:
            print(f"{name}: {n} steps, return {ret:+.1f}, {'pass' if ok else 'fail'}")
    passed = sum(1 for _, _, _, ok in written if ok)
    total = sum(ret for _, _, ret, _ in written)
    print(f"wrote {len(written)} traces to {out} — {passed}/{len(written)} pass, mean return {total / max(1, len(written)):+.2f}")


SETS = {
    #: name: (tasks, runs, directory relative to this file)
    "demo": (lambda: TASKS[:DEMO_TASKS], lambda: RUNS[:DEMO_RUNS], "traces"),
    "train": (lambda: TASKS, lambda: RUNS, "train"),
}


def main(argv: list) -> int:
    which = list(SETS)
    if argv and argv[0] == "--set":
        if len(argv) < 2 or argv[1] not in SETS:
            print(f"error: --set takes one of {', '.join(SETS)}", file=sys.stderr)
            return 2
        which, argv = [argv[1]], argv[2:]
    here = Path(__file__).resolve().parent
    for name in which:
        tasks, runs, subdir = SETS[name]
        out = Path(argv[0]) if argv else here / subdir
        report(write(tasks(), runs(), out), out, verbose=name == "demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
