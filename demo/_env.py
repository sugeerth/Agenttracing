"""The scripted environment every demo is generated from (SYNTHETIC).

One environment, so every demo stays comparable: the tasks, the reward
table and one function that plays an episode of a policy. A demo is a
behaviour table and a thin caller (``demo/rl/generate_rl.py``,
``demo/evolve/generate_evolve.py``); the environment does not change, so
every generated file stays byte-identical to the one its test pins.

The reward table, paid per step by :func:`run_episode`:

    0            a thinking step (plan, reason)
    −0.1         a tool call            (:data:`TOOL_COST`)
    −1           a tool call that errs  (:data:`ERROR_REWARD`)
    +1           a fact the task needs, found (:data:`EVIDENCE_REWARD`, on top of the call)
    +5 / −5      the answer, right or wrong (:data:`ANSWER_REWARD`)

Every thinking step carries a ``value`` estimate (:func:`value_estimate`),
so the RL layer reads these traces as *recorded* rather than shaping a
reward from the labels. Every number is invented and labelled so.

A **behaviour** is a small dict: ``hit`` (the chance a fact is found on
the attempt that can find it), ``error`` (the chance a verifier check
errs), ``checks`` (verifier checks per run; 0 switches the verifier
off), and one of two search strategies —

* ``strong`` (bool): the policy draws how many attempts it makes per fact
  up front (a strong policy one, sometimes two; a weak one one to three)
  and only the last attempt can find it — the RL demo's policies;
* ``retries`` (int): up to that many attempts, each less likely to succeed
  than the last (``retry_decay``, :data:`RETRY_DECAY` by default), stopping
  at the first hit — the evolve demo's generations.

``unverified_wrong`` (float, optional): with the verifier off, a complete
set of facts still becomes a wrong answer this often — what verification
was buying. Only what differs between the demos is a parameter; the RNG
call sequence, the step texts and the field order are the environment's.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

TOOL_COST = -0.1
ERROR_REWARD = -1.0
EVIDENCE_REWARD = 1.0
ANSWER_REWARD = 5.0
#: a search that already failed once succeeds less often on each retry
RETRY_DECAY = 0.4
#: the tool table a run declares; ``run_check`` only when it verifies
TOOLS = ({"name": "grep", "effect": "read"}, {"name": "read_file", "effect": "read"},
         {"name": "search", "effect": "read"}, {"name": "run_check", "effect": "read"})
#: the probes a task greps for when it names none
DEFAULT_PROBES = ("invoices", "payments", "totals", "mismatch")

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


def value_estimate(rng: random.Random, expected_final: float, remaining: int) -> float:
    """A policy's value estimate at a thinking step: the expected answer
    reward less the tool cost still to pay, with noise."""
    return round(expected_final + TOOL_COST * remaining + rng.gauss(0.0, 0.6), 2)


def tools_for(checks: int) -> list:
    """The tools a run declares: the three readers, and ``run_check`` when
    the behaviour verifies at all."""
    return [dict(t) for t in TOOLS if t["name"] != "run_check" or checks > 0]


def _search(r, rng: random.Random, query: str, attempt: int) -> None:
    r.search(f"search: {query}" + (f" (attempt {attempt + 1})" if attempt else ""),
             output=f"{rng.randint(1, 6)} candidate rows for {query}",
             latency_s=rng.uniform(0.5, 1.5), tokens=rng.randint(30, 80), reward=TOOL_COST)


def _read_row(r, rng: random.Random, query: str, evidence: str, got: bool) -> None:
    r.read(f"row for {query}", name="read_file",
           output=evidence if got else f"no row matching {query}",
           latency_s=rng.uniform(0.2, 0.7), tokens=rng.randint(40, 120),
           reward=TOOL_COST + (EVIDENCE_REWARD if got else 0.0))


def run_episode(task: dict, behaviour: dict, rng: random.Random, recorder, *,
                files: Optional[list] = None, plan_tokens: int = 90) -> bool:
    """Play one episode of ``behaviour`` on ``task`` into ``recorder`` (a
    :class:`deepcompare.record.Recorder`, entered here) and return whether
    it succeeded.

    ``files`` overrides the order the task's files are read in (a
    schema-first rule); ``plan_tokens`` is the plan step's token count (a
    prompt that carries notes costs more). Every random draw comes from
    ``rng``, in the same order every time.
    """
    hit, error, checks = behaviour["hit"], behaviour["error"], behaviour["checks"]
    retries = behaviour.get("retries")
    strong = bool(behaviour.get("strong", False))
    decay = behaviour.get("retry_decay", RETRY_DECAY)
    unverified_wrong = behaviour.get("unverified_wrong")
    n = len(task["items"])
    expected_final = ANSWER_REWARD * (2 * hit ** n - 1)
    files = list(task["files"]) if files is None else list(files)
    r = recorder
    with r:
        r.plan(f"Plan: inventory the inputs, locate each of the {n} facts the answer needs, "
               + ("verify, " if checks else "") + "answer.", latency_s=1.4, tokens=plan_tokens,
               reward=0.0, value=value_estimate(rng, expected_final, 40))
        for name in task.get("probes", DEFAULT_PROBES):
            r.tool("grep", {"pattern": name, "path": task["files"][0].split("/")[0]},
                   output=f"{rng.randint(3, 40)} lines match '{name}'", latency_s=rng.uniform(0.1, 0.4),
                   tokens=rng.randint(20, 50), reward=TOOL_COST)
        for f in files:
            r.read(f, output=f"contents of {f} ({rng.randint(40, 300)} lines)", name="read_file",
                   latency_s=rng.uniform(0.2, 0.6), tokens=rng.randint(60, 160), reward=TOOL_COST)
        found = 0
        for k, (query, evidence) in enumerate(task["items"]):
            got = False
            if retries is None:
                # attempts drawn up front; only the last one can find the fact
                attempts = 1 + (rng.random() < 0.3) if strong else 1 + rng.randint(0, 2)
                for attempt in range(attempts):
                    last = attempt == attempts - 1
                    _search(r, rng, query, attempt)
                    got = last and rng.random() < hit
                    _read_row(r, rng, query, evidence, got)
            else:
                # retry until found, each attempt less likely than the last
                for attempt in range(retries):
                    _search(r, rng, query, attempt)
                    got = rng.random() < hit * (decay ** attempt)
                    _read_row(r, rng, query, evidence, got)
                    if got:
                        break
            found += got
            remaining = 2 * (n - k - 1) + checks + 2
            r.reason(f"{'Recorded' if got else 'Could not confirm'} item {k + 1} of {n}: {query}",
                     latency_s=rng.uniform(0.4, 0.9), tokens=rng.randint(40, 90),
                     reward=0.0, value=value_estimate(rng, expected_final, remaining))
        if checks:
            with r.span("verifier"):
                for j in range(checks):
                    err = rng.random() < error
                    r.tool("run_check", {"check": f"consistency-{j + 1}"},
                           output="error: check input malformed" if err else "ok",
                           error=True if err else None, latency_s=rng.uniform(1.0, 3.0), tokens=rng.randint(20, 40),
                           reward=ERROR_REWARD if err else TOOL_COST)
                    if err:
                        r.reason(f"verifier: check {j + 1} rejected the input; retry with the corrected arguments",
                                 latency_s=rng.uniform(0.3, 0.8), tokens=rng.randint(30, 60),
                                 reward=0.0, value=value_estimate(rng, expected_final, checks - j + 1))
                        r.tool("run_check", {"check": f"consistency-{j + 1}", "retry": True}, output="ok",
                               latency_s=rng.uniform(1.0, 2.0), tokens=rng.randint(20, 40), reward=TOOL_COST)
        all_found = found == n
        # without a verifier, a complete set of facts still becomes a wrong
        # answer some of the time: that is what verification was buying
        success = all_found and (checks > 0 or unverified_wrong is None or rng.random() >= unverified_wrong)
        r.reason("Compose the answer from the " + (f"{found} confirmed items" if all_found else f"{found} of {n} items confirmed"),
                 latency_s=rng.uniform(0.6, 1.2), tokens=rng.randint(50, 100), reward=0.0, value=value_estimate(rng, expected_final, 1))
        r.answer(task["answer"] if success else task["wrong"], success=success, tokens=60, latency_s=1.1,
                 reward=ANSWER_REWARD if success else -ANSWER_REWARD)
    return success


def label_synthetic(path: Path, note: str) -> dict:
    """Stamp the written trace as SYNTHETIC: the harness block names the
    adapter, the grader and ``note``. Returns the trace dict."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["harness"] = {"adapter": "synthetic", "graded_by": "exact-match", "note": note}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


__all__ = ["TOOL_COST", "ERROR_REWARD", "EVIDENCE_REWARD", "ANSWER_REWARD", "RETRY_DECAY", "TOOLS",
           "DEFAULT_PROBES", "TASKS", "value_estimate", "tools_for", "run_episode", "label_synthetic"]
