"""Generate the RL demo (SYNTHETIC): two policies, two tasks, three runs each.

Every step carries a ``reward`` paid by a scripted environment — 0 for a thinking step, −0.1 per
tool call, −1 on an error, +1 when a piece of evidence the task needs
appears, +5 / −5 at the answer — and every thinking step a ``value``
estimate, so the RL layer reads these traces as *recorded* rather than
shaping a reward from the labels. ``policy-v1`` is the weaker policy: it
needs more attempts per item, errs more often in the verifier and misses
evidence more; ``policy-v2`` is stronger. A ``verifier`` sub-agent gives
every run a second lane. 30–60 steps per run; every number is invented
and labelled so.

    python demo/rl/generate_rl.py [out_dir]
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
RUNS = ("r1", "r2", "r3")
POLICIES = {"policy-v1": {"strong": False, "hit": 0.72, "error": 0.35, "checks": 4},
            "policy-v2": {"strong": True, "hit": 0.985, "error": 0.1, "checks": 3}}

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


def _value(rng: random.Random, expected_final: float, remaining: int) -> float:
    """A policy's value estimate at a thinking step: the expected answer
    reward less the tool cost still to pay, with noise."""
    return round(expected_final + TOOL_COST * remaining + rng.gauss(0.0, 0.6), 2)


def make(task: dict, agent: str, run: str, out: Path) -> Path:
    policy = POLICIES[agent]
    rng = random.Random(f"{task['id']}|{agent}|{run}")
    expected_final = ANSWER_REWARD * (2 * policy["hit"] ** len(task["items"]) - 1)
    r = Recorder(task=task["id"], prompt=task["prompt"], agent=agent, model=f"sim-{agent}", version="synthetic",
                 expected=task["expected"], run_id=run, out_dir=out,
                 tools=[{"name": "grep", "effect": "read"}, {"name": "read_file", "effect": "read"},
                        {"name": "search", "effect": "read"}, {"name": "run_check", "effect": "read"}])
    with r:
        r.plan(f"Plan: inventory the inputs, locate each of the {len(task['items'])} facts the answer needs, verify, answer.",
               latency_s=1.4, tokens=90, reward=0.0, value=_value(rng, expected_final, 40))
        for i, name in enumerate(("invoices", "payments", "totals", "mismatch")):
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


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "traces"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for task in TASKS:
        for agent in POLICIES:
            for run in RUNS:
                path = make(task, agent, run, out)
                data = json.loads(path.read_text(encoding="utf-8"))
                n = len(data["steps"])
                assert 30 <= n <= 60, f"{path.name}: {n} steps"
                written.append((path.name, n, round(sum(s.get("reward", 0.0) for s in data["steps"]), 2), data["outcome"]["success"]))
    for name, n, ret, ok in written:
        print(f"{name}: {n} steps, return {ret:+.1f}, {'pass' if ok else 'fail'}")
    print(f"wrote {len(written)} traces to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
