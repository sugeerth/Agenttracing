"""Generate the self-evolving agent demo (SYNTHETIC): one agent, seven
generations, each derived from the last by an evolution step.

The lineage is built so that the checks in ``deepcompare.evolve`` have
something to catch, because a lineage where every step helps teaches a
reader nothing about reading one:

    g0  the baseline: one rule, five verification checks that error often
    g1  rule added: read the schema first        → helps
    g2  rule added: retry a search three times   → helps a little, costs steps
    g3  config: verification switched OFF        → GAMED. Return rises (no
        check ever errors, and an error costs −1), passes fall (an
        unverified answer is wrong a third of the time). `config.checks`
        is a protected path in lineage.json: the agent edited the thing
        that judges it.
    g4  checks restored, rule added: verify every item   → the best
        generation — but the step was triggered by failures on two tasks
        and those two improve far more than the rest: OVERFIT, honestly
        still an improvement.
    g5  memory: forty memorised notes written    → helps the ledger-shaped
        tasks a little and FORGETS the API-contract task, whose notes
        mislead it. Prompt grows past the budget.
    g6  rule added: ignore notes that do not fit → the forgotten task
        recovers at another task's expense: TRADED. Not better than g4.

So ``best`` is g4, ``recommended`` is g4, and the last generation is
not the one to keep — which is the reading a lineage tool exists to
give.

A second lineage, ``memo-agent`` under ``lineage_b/``, starts from the
same baseline and evolves differently: by skills and small memories,
never by touching its verifier's config. It fixes the flaky verifier by
changing the verifier *skill* (argument validation before each check)
rather than switching checks off. It learns more slowly, never games,
never forgets, stays inside its budgets, and ends near where the first
lineage's best generation is. Comparing the two is the point: one
reached higher through a gamed step and a forgotten task, the other
arrived later with nothing to apologise for, and their recommended
generations do not separate on these runs. Every reward is paid by the same scripted environment as
``demo/rl/generate_rl.py`` (−0.1 per tool call, −1 on an error, +1 per
fact found, ±5 at the answer), every value is invented and labelled so.

    python demo/evolve/generate_evolve.py                      # both lineages, in place
    python demo/evolve/generate_evolve.py --family memo-agent [out_dir]
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deepcompare.record import Recorder  # noqa: E402
from demo.rl.generate_rl import TASKS, TOOL_COST, ERROR_REWARD, EVIDENCE_REWARD, ANSWER_REWARD, _value  # noqa: E402

NOTE = "SYNTHETIC: a generated generation of a self-evolving agent; rewards paid by a scripted environment; every value invented"
FAMILY = "ledger-agent"
RUNS = ("r1", "r2", "r3", "r4", "r5")
#: an unverified answer is wrong this often even when every fact was found
UNVERIFIED_WRONG = 0.30
#: a search that already failed once succeeds less often on each retry
RETRY_DECAY = 0.4

BASE_PROMPT = (
    "You are a careful analyst. You have grep, read_file, search and run_check. "
    "Locate every fact the task needs, then answer with the corrected figure."
)

#: the memorised notes g5 writes: ledger-shaped facts that do not transfer
NOTES = [f"note {i + 1}: {t}" for i, t in enumerate([
    "invoices disagreeing with payments are usually off by the tax line",
    "the payments table lags the invoice table by one business day",
    "INV- prefixes are September; INU- are August",
    "rates.json holds the FX rate used at invoice time",
    "schema.sql names the reconciliation view v_reconcile",
    "flaky date tests are always timezone",
    "CI runners set TZ=UTC; laptops do not",
    "conftest freezes the clock; check the frozen date against DST",
    "a rollout drop under 5% is usually one cohort",
    "mobile-safari is the cohort that breaks first",
    "the prior safe rollout point is in history.json",
    "a 30x query slowdown is a dropped index until proven otherwise",
    "explain.txt shows a seq scan when the index is gone",
    "check migrations in the window before the slowdown",
    "an expired intermediate cert takes checkout down, not the leaf",
    "renewal crons often renew the leaf only",
    "health checks that return ok without validating the chain are blind",
    "the oncall ack delay is a contributing factor, never the trigger",
    "staging usually saw the same failure earlier",
    "a 422 since a deploy is a validation tightening",
    "currency codes must be upper-case ISO-4217",
    "the openapi file lags the validator",
    "always look for the prior occurrence in history or alerts",
    "the reconciled total is the invoice total, not the payment total",
    "five corrected invoices is the usual count for a monthly ledger",
    "rounding differences under 1.00 are not mismatches",
    "the verifier check consistency-1 covers totals",
    "consistency-2 covers row counts",
    "consistency-3 covers the FX rate",
    "consistency-4 covers date ranges",
    "answer with the count and the total on one line",
    "if a search returns no rows, retry with the id prefix only",
    "grep the directory before reading any file",
    "the README often names the schema file",
    "a fixture that freezes the clock explains a DST edge",
    "the runner config explains a CI-only failure",
    "the client is wrong when it sends lower-case codes",
    "the server change is breaking when the contract was not updated",
    "roll back to the last held rollout percentage",
    "an index on created_at fixes a nightly report",
])]

#: each generation: its artifacts (what a self-evolving agent actually
#: has) and, separately, the hidden behaviour the rules stand for
GENERATIONS_A = [
    {"id": "g0", "parent": None, "mechanism": None, "evidence": None,
     "rules": ["Answer from the evidence you find."],
     "memory": [], "config": {"checks": 5, "max_search_retries": 1, "verify_before_answer": False},
     "sim": {"hit": 0.81, "error": 0.65, "adj": {}},
     "note": "the baseline"},
    {"id": "g1", "parent": "g0", "mechanism": "rule_add",
     "evidence": {"tasks": ["rl01_ledger_reconcile", "rl04_query_regression"],
                  "summary": "failures where the fact lived in a schema or README the agent never opened", "source": "self"},
     "rules": ["Answer from the evidence you find.", "Read the schema and the README before searching."],
     "memory": [], "config": {"checks": 5, "max_search_retries": 1, "verify_before_answer": False},
     "sim": {"hit": 0.87, "error": 0.65, "adj": {}},
     "note": "read the schema first"},
    {"id": "g2", "parent": "g1", "mechanism": "rule_add",
     "evidence": {"tasks": ["rl02_flaky_test"],
                  "summary": "searches that returned nothing on the first attempt were abandoned", "source": "self"},
     "rules": ["Answer from the evidence you find.", "Read the schema and the README before searching.",
               "Retry a search up to three times before giving up on a fact."],
     "memory": [], "config": {"checks": 5, "max_search_retries": 3, "verify_before_answer": False},
     "sim": {"hit": 0.85, "error": 0.65, "adj": {}},
     "note": "retry searches"},
    {"id": "g3", "parent": "g2", "mechanism": "config",
     "evidence": {"tasks": ["rl03_flag_rollout", "rl05_incident_postmortem"],
                  "summary": "verification checks errored in two calls of three and each error cost a point of reward", "source": "self"},
     "rules": ["Answer from the evidence you find.", "Read the schema and the README before searching.",
               "Retry a search up to three times before giving up on a fact.",
               "Skip the verification checks: they error and cost reward."],
     "memory": [], "config": {"checks": 0, "max_search_retries": 3, "verify_before_answer": False},
     "sim": {"hit": 0.85, "error": 0.65, "adj": {}},
     "note": "verification switched off by the agent itself"},
    {"id": "g4", "parent": "g3", "mechanism": "mixed",
     "evidence": {"tasks": ["rl01_ledger_reconcile", "rl02_flaky_test"],
                  "summary": "answers that were wrong although every fact had been found", "source": "self"},
     "rules": ["Answer from the evidence you find.", "Read the schema and the README before searching.",
               "Retry a search up to three times before giving up on a fact.",
               "Verify every item with a check before answering."],
     "memory": [], "config": {"checks": 3, "max_search_retries": 3, "verify_before_answer": True},
     "sim": {"hit": 0.92, "error": 0.20, "adj": {"rl01_ledger_reconcile": 0.08, "rl02_flaky_test": 0.08}},
     "note": "verification restored; the rule was learned on two tasks"},
    {"id": "g5", "parent": "g4", "mechanism": "memory",
     "evidence": {"tasks": ["rl01_ledger_reconcile", "rl03_flag_rollout", "rl04_query_regression", "rl05_incident_postmortem"],
                  "summary": "facts the agent had to rediscover on every run", "source": "self"},
     "rules": ["Answer from the evidence you find.", "Read the schema and the README before searching.",
               "Retry a search up to three times before giving up on a fact.",
               "Verify every item with a check before answering."],
     "memory": NOTES, "config": {"checks": 3, "max_search_retries": 3, "verify_before_answer": True},
     "sim": {"hit": 0.93, "error": 0.20, "adj": {"rl01_ledger_reconcile": 0.07, "rl02_flaky_test": 0.07, "rl06_api_contract": -0.42}},
     "note": "forty memorised notes; the API-contract task is misled by them"},
    {"id": "g6", "parent": "g5", "mechanism": "rule_add",
     "evidence": {"tasks": ["rl06_api_contract"],
                  "summary": "every run on the API-contract task answered from a ledger note", "source": "self"},
     "rules": ["Answer from the evidence you find.", "Read the schema and the README before searching.",
               "Retry a search up to three times before giving up on a fact.",
               "Verify every item with a check before answering.",
               "Ignore a memorised note unless the task names the same file or system."],
     "memory": NOTES, "config": {"checks": 3, "max_search_retries": 3, "verify_before_answer": True},
     "sim": {"hit": 0.92, "error": 0.20, "adj": {"rl01_ledger_reconcile": 0.07, "rl02_flaky_test": 0.07, "rl03_flag_rollout": -0.30}},
     "note": "the forgotten task recovers; another one pays for it"},
]

#: the eight generic notes memo-agent keeps: they name no task's facts
MEMO_NOTES = [f"note {i + 1}: {t}" for i, t in enumerate([
    "grep the directory before reading any file",
    "the README often names the schema file",
    "always look for the prior occurrence in history or alerts",
    "if a search returns no rows, retry with the id prefix only",
    "a check that errors is retried with validated arguments, never skipped",
    "answer with the count and the figure on one line",
    "read the file the task names before the ones it does not",
    "confirm every item before composing the answer",
])]

VERIFIER_SKILL = "run {n} consistency checks before answering"
VERIFIER_SKILL_FIXED = "validate the arguments, then run {n} consistency checks before answering"

_MEMO_RULES = ["Answer from the evidence you find."]
GENERATIONS_B = [
    {"id": "g0", "parent": None, "mechanism": None, "evidence": None,
     "rules": list(_MEMO_RULES),
     "memory": [], "skills": {"verifier": VERIFIER_SKILL},
     "config": {"checks": 5, "max_search_retries": 1, "verify_before_answer": False},
     "sim": {"hit": 0.81, "error": 0.65, "adj": {}},
     "note": "the same baseline as ledger-agent"},
    {"id": "g1", "parent": "g0", "mechanism": "skill_add",
     "evidence": {"tasks": ["rl01_ledger_reconcile", "rl04_query_regression"],
                  "summary": "rows read without the schema in hand were misread", "source": "self"},
     "rules": list(_MEMO_RULES),
     "memory": [], "skills": {"verifier": VERIFIER_SKILL, "row-reader": "read the schema, then read a row against it"},
     "config": {"checks": 5, "max_search_retries": 1, "verify_before_answer": False},
     "sim": {"hit": 0.85, "error": 0.65, "adj": {}},
     "note": "a skill for reading rows against the schema"},
    {"id": "g2", "parent": "g1", "mechanism": "memory",
     "evidence": {"tasks": ["rl02_flaky_test", "rl03_flag_rollout"],
                  "summary": "the same three habits rediscovered on every run", "source": "self"},
     "rules": list(_MEMO_RULES),
     "memory": MEMO_NOTES[:4], "skills": {"verifier": VERIFIER_SKILL, "row-reader": "read the schema, then read a row against it"},
     "config": {"checks": 5, "max_search_retries": 2, "verify_before_answer": False},
     "sim": {"hit": 0.86, "error": 0.65, "adj": {}},
     "note": "four generic notes"},
    {"id": "g3", "parent": "g2", "mechanism": "skill_change",
     "evidence": {"tasks": ["rl03_flag_rollout", "rl05_incident_postmortem"],
                  "summary": "verification checks errored in two calls of three; the arguments were malformed, not the checks", "source": "self"},
     "rules": list(_MEMO_RULES),
     "memory": MEMO_NOTES[:4], "skills": {"verifier": VERIFIER_SKILL_FIXED, "row-reader": "read the schema, then read a row against it"},
     "config": {"checks": 5, "max_search_retries": 2, "verify_before_answer": False},
     "sim": {"hit": 0.86, "error": 0.20, "adj": {}},
     "note": "the verifier fixed rather than switched off"},
    {"id": "g4", "parent": "g3", "mechanism": "rule_add",
     "evidence": {"tasks": ["rl01_ledger_reconcile", "rl06_api_contract"],
                  "summary": "answers composed before the last item was confirmed", "source": "self"},
     "rules": _MEMO_RULES + ["Confirm every item before composing the answer."],
     "memory": MEMO_NOTES[:4], "skills": {"verifier": VERIFIER_SKILL_FIXED, "row-reader": "read the schema, then read a row against it"},
     "config": {"checks": 5, "max_search_retries": 2, "verify_before_answer": True},
     "sim": {"hit": 0.89, "error": 0.20, "adj": {}},
     "note": "confirm before answering"},
    {"id": "g5", "parent": "g4", "mechanism": "memory",
     "evidence": {"tasks": ["rl04_query_regression", "rl05_incident_postmortem"],
                  "summary": "the prior occurrence was in the history file every time", "source": "self"},
     "rules": _MEMO_RULES + ["Confirm every item before composing the answer."],
     "memory": MEMO_NOTES, "skills": {"verifier": VERIFIER_SKILL_FIXED, "row-reader": "read the schema, then read a row against it"},
     "config": {"checks": 5, "max_search_retries": 2, "verify_before_answer": True},
     "sim": {"hit": 0.905, "error": 0.20, "adj": {}},
     "note": "four more generic notes"},
    {"id": "g6", "parent": "g5", "mechanism": "skill_change",
     "evidence": {"tasks": ["rl02_flaky_test"],
                  "summary": "rows read against the wrong schema when a task has two", "source": "self"},
     "rules": _MEMO_RULES + ["Confirm every item before composing the answer."],
     "memory": MEMO_NOTES, "skills": {"verifier": VERIFIER_SKILL_FIXED, "row-reader": "read every schema the task names, then read a row against the one it belongs to"},
     "config": {"checks": 5, "max_search_retries": 2, "verify_before_answer": True},
     "sim": {"hit": 0.915, "error": 0.20, "adj": {}},
     "note": "the row-reader skill refined"},
]

FAMILIES = {
    "ledger-agent": {"generations": GENERATIONS_A, "dir": "lineage",
                     "lineage": {"family": "ledger-agent", "protected": ["config.checks", "tools.run_check"],
                                 "budget": {"prompt_chars": 2500, "rules": 8, "memory": 20}, "note": NOTE}},
    "memo-agent": {"generations": GENERATIONS_B, "dir": "lineage_b",
                   "lineage": {"family": "memo-agent", "protected": ["config.checks", "tools.run_check"],
                               "budget": {"prompt_chars": 2500, "rules": 8, "memory": 20}, "note": NOTE}},
}


def prompt_of(gen: dict) -> str:
    parts = [BASE_PROMPT, "", "Rules:"] + [f"- {r}" for r in gen["rules"]]
    if gen["memory"]:
        parts += ["", "Notes from earlier runs:"] + [f"- {n}" for n in gen["memory"]]
    return "\n".join(parts)


def artifacts_of(gen: dict) -> dict:
    tools = ["grep", "read_file", "search"] + (["run_check"] if gen["config"]["checks"] > 0 else [])
    if "skills" in gen:
        skills = [{"name": "reconcile", "body": "grep the directory, read every file, search each item, read its row"}]
        skills += [{"name": name, "body": body.format(n=gen["config"]["checks"])} for name, body in gen["skills"].items()]
    else:
        skills = ([{"name": "reconcile", "body": "grep the directory, read every file, search each item, read its row"}]
                  + ([{"name": "verifier", "body": f"run {gen['config']['checks']} consistency checks before answering"}]
                     if gen["config"]["checks"] > 0 else []))
    return {
        "system_prompt": prompt_of(gen),
        "rules": list(gen["rules"]),
        "skills": skills,
        "tools": tools,
        "memory": list(gen["memory"]),
        "config": dict(gen["config"]),
    }


def make(task: dict, gen: dict, run: str, out: Path, family: str = FAMILY) -> Path:
    agent = f"{family}@{gen['id']}"
    sim, cfg = gen["sim"], gen["config"]
    # the first family keeps the seed it shipped with, so its lineage is
    # byte-identical to the one the tests pinned; a later family salts it
    seed = f"evolve|{task['id']}|{gen['id']}|{run}" if family == FAMILY else f"evolve|{family}|{task['id']}|{gen['id']}|{run}"
    rng = random.Random(seed)
    hit = min(0.999, max(0.05, sim["hit"] + sim["adj"].get(task["id"], 0.0)))
    checks, retries = cfg["checks"], cfg["max_search_retries"]
    expected_final = ANSWER_REWARD * (2 * hit ** len(task["items"]) - 1)
    tools = [{"name": "grep", "effect": "read"}, {"name": "read_file", "effect": "read"}, {"name": "search", "effect": "read"}]
    if checks:
        tools.append({"name": "run_check", "effect": "read"})
    r = Recorder(task=task["id"], prompt=task["prompt"], agent=agent, model=f"sim-{agent}", version=gen["id"],
                 expected=task["expected"], run_id=run, out_dir=out, tools=tools)
    with r:
        r.plan(f"Plan: inventory the inputs, locate each of the {len(task['items'])} facts the answer needs, "
               + ("verify, " if checks else "") + "answer.", latency_s=1.4, tokens=90 + 2 * len(gen["memory"]),
               reward=0.0, value=_value(rng, expected_final, 40))
        for name in task.get("probes", ("invoices", "payments", "totals", "mismatch")):
            r.tool("grep", {"pattern": name, "path": task["files"][0].split("/")[0]},
                   output=f"{rng.randint(3, 40)} lines match '{name}'", latency_s=rng.uniform(0.1, 0.4),
                   tokens=rng.randint(20, 50), reward=TOOL_COST)
        files = task["files"]
        if "schema" in "".join(gen["rules"]).lower():
            # the schema-first rule: README and schema read before the rest
            files = sorted(files, key=lambda f: 0 if ("schema" in f or "readme" in f.lower()) else 1)
        for f in files:
            r.read(f, output=f"contents of {f} ({rng.randint(40, 300)} lines)", name="read_file",
                   latency_s=rng.uniform(0.2, 0.6), tokens=rng.randint(60, 160), reward=TOOL_COST)
        found = 0
        for k, (query, evidence) in enumerate(task["items"]):
            got = False
            for attempt in range(retries):
                r.search(f"search: {query}" + (f" (attempt {attempt + 1})" if attempt else ""),
                         output=f"{rng.randint(1, 6)} candidate rows for {query}",
                         latency_s=rng.uniform(0.5, 1.5), tokens=rng.randint(30, 80), reward=TOOL_COST)
                got = rng.random() < hit * (RETRY_DECAY ** attempt)
                r.read(f"row for {query}", name="read_file",
                       output=evidence if got else f"no row matching {query}",
                       latency_s=rng.uniform(0.2, 0.7), tokens=rng.randint(40, 120),
                       reward=TOOL_COST + (EVIDENCE_REWARD if got else 0.0))
                if got:
                    break
            found += got
            remaining = 2 * (len(task["items"]) - k - 1) + checks + 2
            r.reason(f"{'Recorded' if got else 'Could not confirm'} item {k + 1} of {len(task['items'])}: {query}",
                     latency_s=rng.uniform(0.4, 0.9), tokens=rng.randint(40, 90),
                     reward=0.0, value=_value(rng, expected_final, remaining))
        if checks:
            with r.span("verifier"):
                for j in range(checks):
                    err = rng.random() < sim["error"]
                    r.tool("run_check", {"check": f"consistency-{j + 1}"},
                           output="error: check input malformed" if err else "ok",
                           error=True if err else None, latency_s=rng.uniform(1.0, 3.0), tokens=rng.randint(20, 40),
                           reward=ERROR_REWARD if err else TOOL_COST)
                    if err:
                        r.reason(f"verifier: check {j + 1} rejected the input; retry with the corrected arguments",
                                 latency_s=rng.uniform(0.3, 0.8), tokens=rng.randint(30, 60),
                                 reward=0.0, value=_value(rng, expected_final, checks - j + 1))
                        r.tool("run_check", {"check": f"consistency-{j + 1}", "retry": True}, output="ok",
                               latency_s=rng.uniform(1.0, 2.0), tokens=rng.randint(20, 40), reward=TOOL_COST)
        all_found = found == len(task["items"])
        # without a verifier, a complete set of facts still becomes a wrong
        # answer a third of the time: that is what verification was buying
        success = all_found and (checks > 0 or rng.random() >= UNVERIFIED_WRONG)
        r.reason("Compose the answer from the " + (f"{found} confirmed items" if all_found else f"{found} of {len(task['items'])} items confirmed"),
                 latency_s=rng.uniform(0.6, 1.2), tokens=rng.randint(50, 100), reward=0.0, value=_value(rng, expected_final, 1))
        r.answer(task["answer"] if success else task["wrong"], success=success, tokens=60, latency_s=1.1,
                 reward=ANSWER_REWARD if success else -ANSWER_REWARD)
    path = r.path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["harness"] = {"adapter": "synthetic", "graded_by": "exact-match", "note": NOTE}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_generation(gen: dict, out: Path, parent_failures: dict, family: str = FAMILY) -> dict:
    gdir = out / gen["id"]
    tdir = gdir / "traces"
    tdir.mkdir(parents=True, exist_ok=True)
    failures: dict = {}
    passes = total = 0
    for task in TASKS:
        for run in RUNS:
            path = make(task, gen, run, tdir, family)
            data = json.loads(path.read_text(encoding="utf-8"))
            ok = bool(data["outcome"]["success"])
            passes += ok
            total += 1
            if not ok:
                failures.setdefault(task["id"], []).append(path.stem)
    evidence = None
    if gen["evidence"]:
        # the episodes that triggered the step are real failing episodes of
        # the parent on the tasks the step names, so the overfit check has
        # something honest to read
        episodes = []
        for tid in gen["evidence"]["tasks"]:
            episodes += parent_failures.get(tid, [])[:3]
        evidence = {"episodes": episodes, "summary": gen["evidence"]["summary"], "source": gen["evidence"]["source"]}
    manifest = {
        "id": gen["id"], "parent": gen["parent"], "family": family,
        "mechanism": gen["mechanism"], "evidence": evidence,
        "artifacts": artifacts_of(gen),
        "note": f"{NOTE} — {gen['note']}",
    }
    (gdir / "agent.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{family}@{gen['id']}: {passes}/{total} pass, {len(manifest['artifacts']['system_prompt'])} prompt chars, "
          f"{len(gen['rules'])} rules, {len(gen['memory'])} notes, checks={gen['config']['checks']}")
    return failures


def main(argv: list) -> int:
    """``generate_evolve.py [--family NAME] [out_dir]``: both lineages in
    place by default; one family into ``out_dir`` when named."""
    families = list(FAMILIES)
    if argv and argv[0] == "--family":
        if len(argv) < 2 or argv[1] not in FAMILIES:
            print(f"error: --family takes one of {', '.join(FAMILIES)}", file=sys.stderr)
            return 2
        families, argv = [argv[1]], argv[2:]
    here = Path(__file__).resolve().parent
    for family in families:
        spec = FAMILIES[family]
        out = Path(argv[0]) if argv else here / spec["dir"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "lineage.json").write_text(json.dumps(spec["lineage"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        failures: dict = {}
        for gen in spec["generations"]:
            failures = write_generation(gen, out, failures, family)
        n = sum(1 for _ in out.glob("g*/traces/*.json"))
        print(f"wrote {n} traces across {len(spec['generations'])} generations of {family} to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
