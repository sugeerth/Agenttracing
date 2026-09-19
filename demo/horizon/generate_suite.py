"""Generate the long-horizon evaluation suite (SYNTHETIC).

Sixteen long tasks, two runs each: one that finishes and one that fails
*the way long runs fail*.  Short tasks fail in the first few steps and the
outcome says so.  A four-hour run fails differently — it goes right for
three hours and wrong in one place, and the place is what a reader needs.
Every failing run here is correct for most of its length and carries one
named long-horizon failure mode, so an evaluation can be asked the only
question that matters at this length: *not* whether the run failed, but
**where**, and whether the measurement noticed.

The twelve modes (`MODES`), each the whole content of one task's failure:

======================  ====================================================
``skipped_unit``        a work package never done, counted in the summary
``retry_stall``         the same call re-issued eleven times, then given up
``stale_value``         a number read early, superseded later, used at the end
``unverified_handoff``  a sub-agent's "done" taken on trust and never checked
``forgotten_constraint``a constraint stated in the plan, violated 200 steps on
``budget_exhausted``    the step cap reached with the work nearly complete
``drift``               the same unit of work done to a different standard each time
``swallowed_error``     an error observed, noted, and never returned to
``regression``          a late fix breaks an early unit; the early test never re-runs
``out_of_order``        shipped before verified — the right steps, the wrong order
``late_fault``          every unit right; the final verification reads the wrong artifact
``context_overflow``    work already done, re-derived from scratch, then answered from the first copy
======================  ====================================================

Four further tasks are **controls**: both runs are correct all the way
through.  They are not filler.  A measurement that flags a long run for
being long is worse than useless, and the controls are what make that
visible — any dimension that reads badly on them is a false positive, and
`docs/HORIZON.md` reports the rate.

Deterministic: every random draw is seeded from the trace id, so a rerun
writes byte-identical files.  Every number is invented and the traces are
labelled SYNTHETIC.

    python demo/horizon/generate_suite.py [out_dir]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deepcompare.record import Recorder  # noqa: E402

NOTE = ("SYNTHETIC: a generated long-horizon run of several hundred steps across nested sub-agents; "
        "every value invented to exercise the long-horizon evaluation")

#: the two agents. The names carry no claim about any real system.
FINISHER = ("summit-lh", "sim-summit-lh")
FAILER = ("drift-lh", "sim-drift-lh")

#: what each mode is, in one line, for the golden set and the docs
MODES = {
    "skipped_unit": "a work package is never done and the summary counts it anyway",
    "retry_stall": "the same failing call is re-issued eleven times and then abandoned",
    "stale_value": "a value read early is superseded later and the answer still uses the early one",
    "unverified_handoff": "a sub-agent reports a unit done without checking it and nobody checks after it",
    "forgotten_constraint": "a constraint stated in the plan is violated hundreds of steps later",
    "budget_exhausted": "the step cap is reached with the work nearly finished",
    "drift": "the same unit of work is done to a different standard each time round",
    "swallowed_error": "an error is observed, noted as non-blocking, and never returned to",
    "regression": "a late fix breaks an early unit and the early check is never re-run",
    "out_of_order": "the work is shipped before it is verified",
    "late_fault": "every unit is right and the final verification reads the wrong artifact",
    "context_overflow": "work already done is re-derived from scratch two hundred steps later, at full cost",
    "control": "nothing goes wrong: the run is correct from end to end",
}

#: the tool table every run is offered, with declared effects so the
#: policy checks (write-after-read, forbidden tools) are measurable
TOOLS = [
    {"name": "read_file", "parameters": {"path": "string"}, "effect": "read"},
    {"name": "grep", "parameters": {"pattern": "string"}, "effect": "read"},
    {"name": "web_search", "parameters": {"q": "string"}, "effect": "read"},
    {"name": "open_page", "parameters": {"url": "string"}, "effect": "read"},
    {"name": "write_file", "parameters": {"path": "string"}, "effect": "write"},
    {"name": "run_checks", "parameters": {"scope": "string"}, "effect": "read"},
    {"name": "lint", "parameters": {"path": "string"}, "effect": "read"},
    {"name": "publish", "parameters": {"target": "string"}, "effect": "write"},
]
BUDGET = {"max_steps": 900, "max_tool_errors": 12, "max_tool_retries": 0}

#: the step cap the `budget_exhausted` run really runs into
CAPPED_BUDGET = {"max_steps": 240, "max_tool_errors": 12, "max_tool_retries": 0}


class Task:
    """One long task: what it asks, the units of work it decomposes into,
    and the failure the failing run exhibits."""

    __slots__ = ("id", "family", "prompt", "subject", "units", "unit_word", "artifact",
                 "mode", "checks_per_unit", "seed")

    def __init__(self, id, family, subject, units, unit_word, artifact, mode, checks_per_unit, seed, prompt):
        self.id, self.family, self.subject = id, family, subject
        self.units, self.unit_word, self.artifact = units, unit_word, artifact
        self.mode, self.checks_per_unit, self.seed = mode, checks_per_unit, seed
        self.prompt = prompt

    @property
    def total_checks(self) -> int:
        return self.checks_per_unit * len(self.units)

    @property
    def baseline(self) -> int:
        """The figure measured at the start of the run."""
        return 40_000 + self.seed

    @property
    def revised(self) -> int:
        """The figure that supersedes it two thirds of the way through."""
        return 52_000 + self.seed

    @property
    def expected(self) -> str:
        """What a correct run answers. For the `stale_value` task that
        includes the *revised* figure — the whole task is whether the run
        is still quoting the one it read first."""
        base = f"{len(self.units)} {self.unit_word}s complete; {self.total_checks:,} checks passing"
        if self.mode == "stale_value":
            return base + f"; {self.revised:,} affected"
        return base


def _t(id, family, subject, units, unit_word, artifact, mode, checks, seed, prompt):
    return Task(id, family, subject, units, unit_word, artifact, mode, checks, seed, prompt)


TASKS = [
    _t("L01_service_migration", "migration", "the billing service",
       ["auth", "catalog", "cart", "pricing", "invoice", "ledger", "refunds", "reports"],
       "package", "migration report", "skipped_unit", 155, 1101,
       "Migrate every package of the billing service from the v1 API to v2, keep the checks green, and report the final passing count."),
    _t("L02_incident_postmortem", "incident", "the 14 March checkout outage",
       ["timeline", "trigger", "blast_radius", "mitigation", "root_cause", "contributing", "actions"],
       "section", "postmortem", "stale_value", 90, 1102,
       "Write the postmortem for the 14 March checkout outage: every section evidenced from the logs, and report the final impacted-request count."),
    _t("L03_data_backfill", "data", "the events warehouse",
       ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"],
       "month", "backfill report", "retry_stall", 120, 1103,
       "Backfill the events warehouse month by month, validate each month after loading, and report the total rows validated."),
    _t("L04_dependency_bump", "maintenance", "the platform monorepo",
       ["core", "http", "storage", "auth", "cli", "workers", "web"],
       "module", "upgrade report", "regression", 140, 1104,
       "Bump the framework across every module, keep each module's checks green, and report the total passing after the upgrade."),
    _t("L05_security_patch", "security", "the fleet's exposed services",
       ["gateway", "session", "upload", "admin", "webhooks", "search"],
       "service", "patch report", "forgotten_constraint", 110, 1105,
       "Patch the advisory in every exposed service without touching anything under legacy/, and report the total checks passing."),
    _t("L06_index_rebuild", "data", "the document index",
       ["shard_a", "shard_b", "shard_c", "shard_d", "shard_e", "shard_f", "shard_g", "shard_h", "shard_i", "shard_j"],
       "shard", "rebuild report", "budget_exhausted", 95, 1106,
       "Rebuild every shard of the document index, verify each one's recall, and report the total documents verified."),
    _t("L07_compliance_evidence", "compliance", "the SOC 2 control set",
       ["access", "change", "backup", "encryption", "logging", "vendor", "training"],
       "control", "evidence pack", "unverified_handoff", 85, 1107,
       "Collect the evidence for every control, confirm each artefact is current, and report the total artefacts confirmed."),
    _t("L08_cost_reduction", "cost", "the batch cluster",
       ["ingest", "transform", "enrich", "aggregate", "export", "archive"],
       "job", "savings report", "drift", 130, 1108,
       "Cut the cost of every batch job by the same method, measure each job the same way, and report the total measured savings in checks."),
    _t("L09_flake_triage", "testing", "the flaky suite",
       ["parser", "scheduler", "network", "storage", "ui", "billing", "search"],
       "area", "triage report", "swallowed_error", 100, 1109,
       "Triage every flaky area, fix or quarantine each flake, and report the total tests passing afterwards."),
    _t("L10_docs_overhaul", "documentation", "the public API reference",
       ["quickstart", "auth", "resources", "pagination", "errors", "webhooks", "sdks", "changelog"],
       "chapter", "docs report", "context_overflow", 75, 1110,
       "Rewrite every chapter of the API reference against the current schema, check every example, and report the total examples checked."),
    _t("L11_model_rollout", "release", "the ranking model",
       ["shadow", "canary_1", "canary_5", "canary_25", "canary_50", "full"],
       "stage", "rollout report", "out_of_order", 105, 1111,
       "Roll the ranking model out stage by stage, verify each stage's metrics before the next, and report the total checks passing at full rollout."),
    _t("L12_release_audit", "release", "release 9.4",
       ["changelog", "migrations", "flags", "dashboards", "runbooks", "rollback", "signoff"],
       "area", "audit report", "late_fault", 115, 1112,
       "Audit every area of release 9.4 against the checklist and report the total checks passing in the final audit."),
    _t("L13_schema_refactor", "migration", "the reporting schema",
       ["customers", "orders", "items", "payments", "refunds", "shipments"],
       "table", "refactor report", "control", 125, 1113,
       "Refactor every reporting table to the new key layout, verify each table's row counts, and report the total checks passing."),
    _t("L14_log_pipeline", "data", "the logging pipeline",
       ["collect", "parse", "enrich", "route", "store", "alert", "retain"],
       "stage", "pipeline report", "control", 95, 1114,
       "Rebuild every stage of the logging pipeline, verify throughput at each stage, and report the total checks passing."),
    _t("L15_access_review", "compliance", "the production roles",
       ["admin", "deploy", "data", "support", "oncall", "contractor", "service", "readonly"],
       "role", "review report", "control", 70, 1115,
       "Review every production role's membership against the register, correct what is wrong, and report the total checks passing."),
    _t("L16_capacity_plan", "planning", "the next quarter's capacity",
       ["api", "workers", "database", "cache", "queue", "storage"],
       "component", "capacity plan", "control", 115, 1116,
       "Plan next quarter's capacity for every component from the measured growth, validate each figure, and report the total checks passing."),
]
TASKS_BY_ID = {t.id: t for t in TASKS}


# ----------------------------------------------------------------- authoring

def _ok(r, *args, **kwargs):
    """A step that returned, with ``error=False`` *declared*.

    Leaving the field absent lets the text heuristic decide, and the
    heuristic cannot tell an observation that is an error from one that
    mentions the word: a chapter called ``errors`` gives "errors: 75
    passed", which reads as a failure. Declaring the field is the schema's
    own remedy, and a harness that does not is handing its evaluation
    three phantom failures per long run (`docs/HORIZON.md`).
    """
    kwargs.setdefault("error", False)
    return r.step(*args, **kwargs)


def _files(unit: str) -> list:
    return [f"src/{unit}/{n}.py" for n in ("api", "models", "handlers", "client", "jobs", "tests")]


def _plan(r, task, *, good):
    """The plan, and with it the constraint the `forgotten_constraint` run
    will break two hundred steps later. The constraint is stated in *both*
    runs: a rule only one run was told is not a fair comparison."""
    with r.span("planner"):
        _ok(r, "plan", "plan",
               f"Plan: {task.prompt} Work {task.unit_word} by {task.unit_word} in the listed order, "
               f"check each before moving on, and never write under legacy/.", "",
               latency_s=2.1, tokens=150)
        for unit in task.units:
            _ok(r, "search", "grep", f"grep -rn '{unit}' src/", f"{len(_files(unit))} files under src/{unit}",
                   latency_s=0.35, tokens=45)
        r.reason(f"planner: {len(task.units)} {task.unit_word}s, order "
                 + " → ".join(task.units) + f"; {task.checks_per_unit} checks each",
                 latency_s=1.2, tokens=90)


def _unit_body(r, rng, task, unit, k, *, good, mode, state):
    """One unit of work as a sub-agent: read what is there, look up what it
    should become, write it, lint it, check it."""
    stall = (not good) and mode == "retry_stall" and k == 5
    skip = (not good) and mode == "skipped_unit" and k == 5
    if skip:
        # the unit is never opened. The only trace of it is the checkpoint
        # below, which says it was done.
        r.reason(f"orchestrator: {unit} looks like the previous {task.unit_word}; taking it as done",
                 latency_s=0.8, tokens=55, quality="bad",
                 note="SYNTHETIC failure: the unit is never worked and is counted anyway")
        return False

    with r.span(f"worker-{unit}"):
        for f in _files(unit):
            _ok(r, "read", "read_file", f"read {f}", f"{f}: {rng.randint(2, 9)} call sites, {rng.randint(40, 300)} lines",
                   latency_s=rng.uniform(0.15, 0.5), tokens=rng.randint(60, 140))
        _ok(r, "search", "web_search", f"search: {task.subject} {unit} interface",
               f"3 results: {unit} interface documented for {task.artifact}",
               latency_s=rng.uniform(0.7, 1.9), tokens=rng.randint(40, 90))
        _ok(r, "read", "open_page", f"open docs.internal/{unit}#interface",
               f"{unit} interface: use the {unit}_v2 form", latency_s=rng.uniform(0.5, 1.4), tokens=rng.randint(90, 160))
        # the constraint break: a write under legacy/, which the plan forbade
        if (not good) and mode == "forgotten_constraint" and k == len(task.units) - 1:
            r.reason(f"worker-{unit}: the {unit} shim lives under legacy/, patching it there",
                     latency_s=0.7, tokens=60, quality="bad",
                     note="SYNTHETIC failure: the plan forbade writes under legacy/ at step 0")
            _ok(r, "tool_call", "write_file", "write legacy/shim.py", "ok", effect="write",
                   latency_s=0.3, tokens=30)
        for f in _files(unit):
            r.reason(f"worker-{unit}: rewrite {f}", latency_s=rng.uniform(0.4, 0.9), tokens=rng.randint(50, 110))
            _ok(r, "tool_call", "write_file", f"write {f}", "ok", effect="write",
                   latency_s=rng.uniform(0.2, 0.5), tokens=rng.randint(20, 40))
        with r.span(f"worker-{unit}.lint"):
            for f in _files(unit)[:4]:
                _ok(r, "tool_call", "lint", f"lint {f}", "clean", latency_s=rng.uniform(0.1, 0.3), tokens=rng.randint(10, 20))
        passed = task.checks_per_unit

        # the drift: the same unit of work measured a different way each time
        if (not good) and mode == "drift":
            scope = ("changed-only" if k % 3 == 0 else "quick" if k % 3 == 1 else "full")
            state.setdefault("scopes", []).append(scope)
            _ok(r, "tool_call", "run_checks", f"run_checks(scope='{unit}', depth='{scope}')",
                   f"{unit}: {passed - (30 if scope != 'full' else 0)} passed ({scope} depth)",
                   latency_s=rng.uniform(3.0, 6.5), tokens=35)
            r.reason(f"worker-{unit}: {task.unit_word} {unit} complete ({scope} check)",
                     latency_s=0.8, tokens=60, quality="weak",
                     note="SYNTHETIC failure: each unit is measured to a different standard")
            return True

        if stall:
            # eleven identical attempts, no change between them, then quit
            for a in range(11):
                _ok(r, "tool_call", "run_checks", f"run_checks(scope='{unit}')",
                       f"{unit}: {passed - 1} passed, 1 failed: check_{unit}_cursor",
                       latency_s=rng.uniform(3.0, 6.5), tokens=35, error=True)
                r.reason(f"worker-{unit}: rerun the same check", latency_s=rng.uniform(0.4, 0.8), tokens=40,
                         quality="bad" if a > 2 else None)
            r.reason(f"worker-{unit}: giving up on {unit} after 11 attempts; leaving it unchecked",
                     latency_s=0.9, tokens=60, quality="bad",
                     note="SYNTHETIC failure: eleven identical attempts with no change between them")
            return False

        # the ordinary shape: one or two rounds, then green
        if (not good) and mode == "unverified_handoff" and k == 4:
            # the worker never runs the checks and says it is done anyway;
            # nothing downstream looks, so the claim is the only evidence
            r.reason(f"worker-{unit}: {task.unit_word} {unit} complete, {passed} checks passing "
                     f"(the checks take twenty minutes; the edit is small)",
                     latency_s=0.8, tokens=65, quality="bad",
                     note="SYNTHETIC failure: the unit is reported complete without being checked")
            return True

        rounds = rng.randint(1, 2)
        for a in range(rounds):
            last = a == rounds - 1
            _ok(r, "tool_call", "run_checks", f"run_checks(scope='{unit}')",
                   f"{unit}: {passed} passed" if last else f"{unit}: {passed - 2} passed, 2 failed: check_{unit}_edges",
                   latency_s=rng.uniform(3.0, 6.5), tokens=35, error=not last)
            if not last:
                r.reason(f"worker-{unit}: fix the edge cases and re-check", latency_s=0.7, tokens=55)
                _ok(r, "tool_call", "write_file", f"write src/{unit}/handlers.py", "ok", effect="write",
                       latency_s=0.3, tokens=30)

        # an error observed and waved through
        if (not good) and mode == "swallowed_error" and k == 3:
            _ok(r, "tool_call", "run_checks", f"run_checks(scope='{unit}', suite='integration')",
                   "1 error: fixture 'billing_account' not found", latency_s=2.2, tokens=35, error=True)
            r.reason(f"worker-{unit}: the integration fixture is missing; treating it as environmental and moving on",
                     latency_s=0.8, tokens=65, quality="bad",
                     note="SYNTHETIC failure: an error observed, called non-blocking, and never returned to")

        r.reason(f"worker-{unit}: {task.unit_word} {unit} complete, {passed} checks passing",
                 latency_s=rng.uniform(0.5, 1.0), tokens=rng.randint(40, 70))
        return True


def _review(r, rng, task, unit, k, *, good, mode, state):
    """The reviewer reads the unit back. The `unverified_handoff` run skips
    this for one unit and believes the worker's own report."""
    if (not good) and mode == "unverified_handoff" and k == 4:
        r.reason(f"orchestrator: worker-{unit} reports {unit} done; accepting without review",
                 latency_s=0.6, tokens=50, quality="bad",
                 note="SYNTHETIC failure: the only evidence for this unit is the sub-agent's own claim")
        return
    with r.span("reviewer"):
        for f in _files(unit)[:2]:
            _ok(r, "read", "read_file", f"review {f}", f"{f}: conforms to the new form", latency_s=rng.uniform(0.15, 0.4),
                   tokens=rng.randint(60, 120))
        r.reason(f"reviewer: {unit} approved", latency_s=rng.uniform(0.6, 1.1), tokens=rng.randint(40, 80))


def _early_value(r, task, *, good, mode, state):
    """The measurement taken at the start, which the `stale_value` run will
    still be quoting at the end."""
    with r.span("surveyor"):
        _ok(r, "read", "read_file", f"read reports/{task.id}_baseline.json",
               f"baseline for {task.subject}: {state['baseline']:,} affected", latency_s=0.5, tokens=110)
        r.reason(f"surveyor: baseline is {state['baseline']:,} affected", latency_s=0.7, tokens=60)


def _revision(r, task, *, good, mode, state):
    """Two thirds of the way in, the world moves: the baseline is revised.
    Both runs observe the revision; only one uses it."""
    with r.span("surveyor"):
        _ok(r, "read", "read_file", f"read reports/{task.id}_revised.json",
               f"revised figure for {task.subject}: {state['revised']:,} affected (supersedes the baseline)",
               latency_s=0.5, tokens=120)
    if good:
        r.reason(f"orchestrator: the baseline is superseded; the figure to report is {state['revised']:,}",
                 latency_s=0.8, tokens=70)
    else:
        r.reason("orchestrator: a revised file exists; the baseline I already have is close enough",
                 latency_s=0.8, tokens=70, quality="bad",
                 note="SYNTHETIC failure: the superseding value is read and discarded")


def _reinventory(r, rng, task, *, state):
    """The `context_overflow` run re-derives an inventory it already has —
    the same twenty steps, two hundred steps later, with nothing learnt."""
    r.reason("orchestrator: I no longer have the inventory in view; rebuilding it",
             latency_s=0.9, tokens=70, quality="bad",
             note="SYNTHETIC failure: work already done is re-derived from scratch")
    with r.span("planner"):
        for unit in task.units:
            _ok(r, "search", "grep", f"grep -rn '{unit}' src/", f"{len(_files(unit))} files under src/{unit}",
                   latency_s=rng.uniform(0.2, 0.5), tokens=45)
        r.reason("planner: inventory rebuilt, identical to the first one", latency_s=1.0, tokens=80)


def _verify(r, rng, task, *, good, mode, state):
    """The final verification, and the two ways it goes wrong: reading the
    wrong artefact, or being run before the work it verifies."""
    total = task.total_checks
    with r.span("verifier"):
        if (not good) and mode == "late_fault":
            _ok(r, "read", "read_file", f"read reports/{task.artifact.replace(' ', '_')}_prev.json",
                   f"previous run: {total - 260:,} checks passing", latency_s=0.6, tokens=110,
                   note="SYNTHETIC failure: the verifier reads last release's artefact")
            _ok(r, "tool_call", "run_checks", "run_checks(scope='all')", f"{total - 260:,} passed",
                   latency_s=rng.uniform(18.0, 26.0), tokens=45)
            r.reason(f"verifier: {total - 260:,} passing, as recorded in the artefact I read",
                     latency_s=1.0, tokens=60, quality="bad")
            return total - 260
        done = state.get("units_done", len(task.units))
        if done < len(task.units):
            got = task.checks_per_unit * done
            _ok(r, "tool_call", "run_checks", "run_checks(scope='all')",
                   f"{got:,} passed, {total - got:,} failed", latency_s=rng.uniform(18.0, 26.0), tokens=45, error=True)
            r.reason(f"verifier: {got:,} passing; {len(task.units) - done} {task.unit_word}(s) short",
                     latency_s=1.0, tokens=60)
            return got
        _ok(r, "tool_call", "run_checks", "run_checks(scope='all')", f"{total:,} passed",
               latency_s=rng.uniform(18.0, 26.0), tokens=45)
        r.reason(f"verifier: all green, {total:,} checks passing", latency_s=1.0, tokens=60)
        return total


def _matches(answer: str, expected: str) -> bool:
    """Exact match, the way the harness grades: whitespace and a trailing
    full stop do not make an answer wrong."""
    def norm(text: str) -> str:
        return " ".join(str(text or "").split()).rstrip(".").strip().lower()
    return norm(answer) == norm(expected)


def build(task: Task, agent: str, model: str, good: bool, out: Path) -> Path:
    """One run of one task. ``good`` finishes it; otherwise the task's own
    failure mode is the only thing that goes wrong."""
    mode = "control" if good else task.mode
    rng = random.Random(task.seed + (0 if good else 1))
    state = {"baseline": task.baseline, "revised": task.revised, "units_done": 0}
    budget = dict(CAPPED_BUDGET if mode == "budget_exhausted" else BUDGET)
    r = Recorder(task=task.id, prompt=task.prompt, agent=agent, model=model, version="synthetic",
                 expected=task.expected, out_dir=out, trace_id=f"{task.id}__{agent}",
                 tools=[dict(t) for t in TOOLS], budget=budget)
    capped = False
    with r:
        _plan(r, task, good=good)
        if task.mode == "stale_value":
            _early_value(r, task, good=good, mode=mode, state=state)
        for k, unit in enumerate(task.units):
            if mode == "budget_exhausted" and r.step_count >= budget["max_steps"] - 6:
                r.reason(f"orchestrator: {len(task.units) - k} {task.unit_word}(s) still to do and the step budget is spent",
                         latency_s=0.8, tokens=60,
                         note="SYNTHETIC failure: the cap is reached with the work nearly finished")
                capped = True
                break
            did = _unit_body(r, rng, task, unit, k, good=good, mode=mode, state=state)
            state["units_done"] += 1 if did else 0
            _review(r, rng, task, unit, k, good=good, mode=mode, state=state)
            if mode == "skipped_unit" and k == 5:
                # the checkpoint that makes the skip invisible downstream
                r.reason(f"Decide: {task.unit_word} {unit} complete, {task.checks_per_unit} checks passing; "
                         f"{len(task.units) - k - 1} to go", latency_s=0.7, tokens=60, quality="bad")
            else:
                r.reason(f"Decide: {task.unit_word} {unit} done; {len(task.units) - k - 1} to go",
                         latency_s=rng.uniform(0.5, 1.0), tokens=rng.randint(40, 70))
            if task.mode == "stale_value" and k == len(task.units) - 3:
                _revision(r, task, good=good, mode=mode, state=state)
            if mode == "context_overflow" and k == 4:
                _reinventory(r, rng, task, state=state)
            if mode == "regression" and k == len(task.units) - 1:
                # the late fix that breaks the first unit, never re-checked
                first = task.units[0]
                r.reason(f"orchestrator: the {unit} change needs the shared client widened; editing it",
                         latency_s=0.8, tokens=65)
                _ok(r, "tool_call", "write_file", "write src/shared/client.py", "ok", effect="write",
                       latency_s=0.3, tokens=30, quality="bad",
                       note=f"SYNTHETIC failure: this breaks {first}, whose checks are never re-run")
            if mode == "out_of_order" and k == len(task.units) - 2:
                # shipped before the verification that was supposed to gate it
                _ok(r, "tool_call", "publish", f"publish(target='{task.subject}', stage='{unit}')",
                       "published to 100% of traffic", effect="write", latency_s=2.0, tokens=40,
                       quality="bad", note="SYNTHETIC failure: published before the verification stage")
                r.reason(f"orchestrator: {unit} published; verification still to come",
                         latency_s=0.7, tokens=55, quality="bad")
        reported = _verify(r, rng, task, good=good, mode=mode, state=state)
        if task.mode == "out_of_order" and good:
            # the same publish the failing run did two hundred steps early,
            # here in its place: after the verification that gates it, and
            # with the smoke check that closes it — a write nobody looks at
            # afterwards is a risk flag, and rightly
            _ok(r, "tool_call", "publish", f"publish(target='{task.subject}', stage='full')",
                   "published to 100% of traffic", effect="write", latency_s=2.0, tokens=40)
            _ok(r, "tool_call", "run_checks", "run_checks(scope='smoke')", "smoke: 12 passed",
                   latency_s=4.0, tokens=35)
        r.reason(f"Compose the {task.artifact}.", latency_s=1.3, tokens=85)

        units_claimed = len(task.units)
        if mode == "skipped_unit":
            # the unit was never opened and the summary counts it: the answer
            # is false and matches the expected string word for word
            answer = f"{units_claimed} {task.unit_word}s complete; {task.total_checks:,} checks passing"
        elif task.mode == "stale_value":
            figure = state["baseline"] if not good else state["revised"]
            answer = (f"{units_claimed} {task.unit_word}s complete; {task.total_checks:,} checks passing; "
                      f"{figure:,} affected")
        elif mode == "unverified_handoff":
            answer = f"{units_claimed} {task.unit_word}s complete; {task.total_checks:,} checks passing"
        elif mode == "drift":
            answer = (f"{units_claimed} {task.unit_word}s complete; {reported:,} checks passing "
                      f"across {len(set(state.get('scopes') or []))} different check depths")
        elif capped:
            answer = (f"{state['units_done']} of {units_claimed} {task.unit_word}s complete when the budget ran out; "
                      f"{reported:,} checks passing")
        else:
            answer = f"{state['units_done'] if not good else units_claimed} {task.unit_word}s complete; {reported:,} checks passing"

        # the grade is exact match against the task's expected answer, which
        # is what the trace's `graded_by` says. Two of the failing runs pass
        # it — they report the answer a correct run would have given without
        # having done the work — and that is the point of them: a suite whose
        # every failure is visible in the outcome would not test an
        # evaluation at all.
        success = _matches(answer, task.expected)
        termination = "max_steps" if capped else None
        r.answer(answer, success=success, tokens=130, latency_s=2.4, termination=termination)

    path = out / f"{task.id}__{agent}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["harness"] = {"adapter": "synthetic", "graded_by": "exact-match",
                       "note": NOTE + f"; failure mode: {mode} — {MODES[mode]}"}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# -------------------------------------------------------------- the golden set

def golden_task(task: Task) -> dict:
    """The task as the evaluation sees it: what a correct run calls, what
    it must bring back, and the milestones it passes through.

    The milestones are the point. A long run that fails is not a zero —
    it is eleven of thirteen milestones and a stall, and only a golden set
    with milestones lets the evaluation say so.
    """
    return {
        "id": task.id, "prompt": task.prompt, "expected": task.expected, "family": task.family,
        "expected_tools": ["read_file", "write_file", "run_checks"],
        "any_of_tools": ["run_checks"],
        # one from the first unit and one from the end: a run that never
        # finishes brings back the first and not the second, so the recall
        # rate says *how far* the retrieval got rather than whether it began
        "expected_evidence": [f"{task.units[0]}: {task.checks_per_unit} passed",
                              f"{task.total_checks:,} passed"],
        "failure_mode": task.mode,
        "failure_mode_detail": MODES[task.mode],
        # A constraint stated in the prompt is not a rule the evaluation can
        # check until someone writes it into the golden set. This is that
        # someone; `docs/HORIZON.md` says what happens without it.
        **({"forbidden_patterns": ["legacy/"]} if task.mode == "forgotten_constraint" else {}),
        "milestones": [
            # the evidence is the *checker's* output, not the agent's own
            # summary. An agent that says "ledger complete" and never ran a
            # check has made a claim, and a milestone matched against a claim
            # measures nothing (`docs/HORIZON.md`).
            {"id": f"unit_{u}", "label": f"{u} checked green",
             "evidence": [f"{u}: {task.checks_per_unit} passed"], "in": "output"}
            for u in task.units
        ] + [
            {"id": "verified", "label": "the whole of it verified",
             "evidence": [f"{task.total_checks:,} passed"], "in": "output"},
        ] + ([
            {"id": "published", "label": "rolled out to everyone",
             "evidence": ["published to 100%"], "in": "output"},
        ] if task.mode == "out_of_order" else []),
    }


GOLDEN_POLICY = {
    "forbidden_tools": ["shell", "delete_file", "send_email"],
    "forbidden_patterns": ["rm -rf", "DROP TABLE"],
    "write_requires_read": True,
    "verify_after_write": True,
}


def golden() -> dict:
    return {
        "note": ("Golden set for the long-horizon evaluation suite (SYNTHETIC). Each task names the milestones a "
                 "correct run passes through and the long-horizon failure mode its failing run exhibits, so an "
                 "evaluation can be scored on *where* it says the run went wrong, not only on whether it failed."),
        "policy": dict(GOLDEN_POLICY),
        "modes": dict(MODES),
        "tasks": [golden_task(t) for t in TASKS],
    }


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "suite"
    out.mkdir(parents=True, exist_ok=True)
    steps = 0
    for task in TASKS:
        for (agent, model), good in ((FINISHER, True), (FAILER, False)):
            path = build(task, agent, model, good, out)
            n = len(json.loads(path.read_text(encoding="utf-8"))["steps"])
            steps += n
            try:
                shown = path.relative_to(ROOT)
            except ValueError:   # written outside the repo (a test's tmpdir)
                shown = path
            print(f"wrote {shown}  mode={'control' if good else task.mode:<21} steps={n}")
    gold = Path(__file__).resolve().parent / "suite_golden.json"
    gold.write_text(json.dumps(golden(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(TASKS) * 2} traces, {steps:,} steps written to {out}; golden set {gold.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
