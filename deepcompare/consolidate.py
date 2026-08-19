"""Cross-run diagnosis: evidence accumulation and executed discriminators.

A pair diagnosis is honest about being n=1: its confidence is capped and
every hypothesis carries the check that would settle it.  When the corpus
holds *repeated runs* of the same task, two upgrades become possible, and
this module performs both:

1. **Consolidation.**  Diagnose every failing run, not one representative
   pair, and ask whether the same hypothesis leads each time.  A cause that
   leads in 3 of 3 failing runs is reproducible; one that flips kinds
   between runs is noise-sensitive, and saying so is itself the diagnosis.
   Failure reproduction gets its own denominator: an agent that fails 1 of
   3 runs has a flake, not a systematic fault, whatever any single-run
   story says.

2. **Executed discriminators.**  Some discriminating checks a pair
   diagnosis can only *recommend* are answerable offline from the runs
   already on disk, so they are answered:

   - *grader consistency*: two runs of the same task whose final answers
     are near-identical but carry different success labels prove the
     grader (or label) is inconsistent — measured, with both runs named;
   - *environment reproduction*: the exact failing tool call succeeding in
     another run proves the error was transient (the agent mishandled it);
     the same call erring wherever it appears points at the environment;
   - *harness flake rate*: harness kills counted over all runs.

The status vocabulary is deliberately strict: scoring can make a
hypothesis ``leading``; only an executed check can make it ``confirmed``
or ``refuted``.  Everything here is deterministic and derived from the
trajectories alone — no re-running, no network, no model calls.
"""

from __future__ import annotations

from typing import Optional

from .align import jaccard
from .diagnosis import diagnose
from .report import compare
from .stability import medoid_pairs
from .trace import HARNESS_TERMINATIONS, Trajectory

#: answers at least this similar (token Jaccard) count as "the same answer"
#: for the grader-consistency check.  High on purpose: a false "inconsistent
#: grader" claim is worse than a missed one.
ANSWER_MATCH_THRESHOLD = 0.8

#: an executed check outcome, attached to consolidation entries.
CHECK_OUTCOMES = ("confirms", "refutes", "inconclusive")


# ---------------------------------------------------------------------------
# executed discriminators


def _grader_consistency(task_runs: list[Trajectory],
                        failing: Trajectory) -> Optional[dict]:
    """Did the grader treat a near-identical answer differently elsewhere?

    The check compares the failing run's answer against every *passing*
    run's answer; a near-identical pair with opposite labels is measured
    grader inconsistency.  (Pairs where neither run is the failing one are
    deliberately not scanned: they would say something about the grader in
    general, but nothing about this failure.)
    """
    fail_answer = failing.outcome.answer or ""
    if not fail_answer.strip():
        return None
    best = None
    for other in task_runs:
        if other is failing or not other.outcome.success:
            continue
        similarity = jaccard(fail_answer, other.outcome.answer or "")
        if similarity >= ANSWER_MATCH_THRESHOLD and (
                best is None or similarity > best[0]):
            best = (similarity, other)
    if best is not None:
        similarity, other = best
        return {
            "check": "grader_consistency",
            "outcome": "confirms",
            "hypothesis_kind": "grader_or_label",
            "detail": (
                f"{failing.agent.name} run {failing.run_id} failed with an "
                f"answer {similarity:.2f} similar (token Jaccard) to "
                f"{other.agent.name} run {other.run_id}'s *passing* answer "
                f"— the grader treated near-identical answers differently"
            ),
            "basis": "measured",
            "runs": [f"{failing.agent.name}/{failing.run_id}",
                     f"{other.agent.name}/{other.run_id}"],
        }
    # no passing twin: an executed check that found nothing is still a
    # result, and it cuts the other way
    return {
        "check": "grader_consistency",
        "outcome": "inconclusive",
        "hypothesis_kind": "grader_or_label",
        "detail": (
            f"no passing run in this corpus carries an answer "
            f"{ANSWER_MATCH_THRESHOLD:.0%}-similar to the failing one; the "
            f"corpus cannot settle the grader hypothesis either way"
        ),
        "basis": "measured",
        "runs": [f"{failing.agent.name}/{failing.run_id}"],
    }


def _call_signature(step) -> tuple:
    return (step.name, step.input)


def _error_steps(traj: Trajectory) -> list:
    return [s for s in traj.steps
            if s.error is True
            or (s.error is None and s.output and
                s.output.lower().startswith(("error", "http 5", "timeout")))]


def _environment_reproduction(task_runs: list[Trajectory],
                              failing: Trajectory) -> Optional[dict]:
    """Did the exact failing call behave differently in another run?"""
    errored = _error_steps(failing)
    if not errored:
        return None
    target = _call_signature(errored[0])
    succeeded_in, errored_in = [], []
    for other in task_runs:
        if other is failing:
            continue
        for step in other.steps:
            if _call_signature(step) != target:
                continue
            if step in _error_steps(other):
                errored_in.append(f"{other.agent.name}/{other.run_id}")
            else:
                succeeded_in.append(f"{other.agent.name}/{other.run_id}")
            break
    if succeeded_in:
        return {
            "check": "environment_reproduction",
            "outcome": "refutes",
            "hypothesis_kind": "environment_error",
            "detail": (
                f"the failing call {errored[0].name!r} succeeded unchanged "
                f"in {', '.join(succeeded_in[:3])} — the error was "
                f"transient, so the fault is the agent's handling of it, "
                f"not the environment"
            ),
            "basis": "measured",
            "runs": succeeded_in[:3],
        }
    if errored_in:
        return {
            "check": "environment_reproduction",
            "outcome": "confirms",
            "hypothesis_kind": "environment_error",
            "detail": (
                f"the failing call {errored[0].name!r} errored in every run "
                f"where it appears ({len(errored_in) + 1} runs) — a "
                f"persistent environment fault, not an agent mistake"
            ),
            "basis": "measured",
            "runs": errored_in[:3],
        }
    return {
        "check": "environment_reproduction",
        "outcome": "inconclusive",
        "hypothesis_kind": "environment_error",
        "detail": (
            f"the failing call {errored[0].name!r} appears in no other run; "
            f"the corpus cannot say whether the error is transient"
        ),
        "basis": "measured",
        "runs": [f"{failing.agent.name}/{failing.run_id}"],
    }


def _harness_flake(agent_runs: list[Trajectory]) -> Optional[dict]:
    """Harness kills counted over this agent's runs of the task."""
    kills = [t for t in agent_runs
             if t.outcome.termination in HARNESS_TERMINATIONS]
    if not kills:
        return None
    return {
        "check": "harness_flake_rate",
        "outcome": "confirms",
        "hypothesis_kind": "harness_termination",
        "detail": (
            f"the harness killed {len(kills)} of {len(agent_runs)} runs "
            f"({', '.join(t.run_id for t in kills)}) — infrastructure "
            f"flakiness with a measured rate, not an agent property"
        ),
        "basis": "measured",
        "runs": [f"{t.agent.name}/{t.run_id}" for t in kills],
    }


# ---------------------------------------------------------------------------
# consolidation


def _leading_kind(diagnosis: dict) -> Optional[str]:
    lead = next((h for h in diagnosis.get("hypotheses", [])
                 if h.get("id") == diagnosis.get("leading")), None)
    if lead is None:
        return None
    kind = lead.get("kind")
    if lead.get("flag"):
        kind = f"{kind}:{lead['flag']}"
    return kind


def _consolidated_verdict(kind_counts: dict, diagnosed: int,
                          contested: int, checks: list[dict],
                          agent: str, failures: int, runs: int) -> dict:
    """One statement per (task, agent), with its basis named.

    Executed checks outrank scored rankings: a refuted hypothesis does not
    get to stay "leading" because a heuristic scored it first, and a
    confirmed one no longer needs its score defended.
    """
    confirmed = [c for c in checks if c["outcome"] == "confirms"]
    refuted = [c for c in checks if c["outcome"] == "refutes"]
    if confirmed:
        top = confirmed[0]
        return {
            "kind": top["hypothesis_kind"],
            "status": "confirmed",
            "statement": top["detail"],
            "basis": "executed check, not a score",
        }
    stable_kind = None
    if kind_counts:
        ranked = sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top_kind, top_count = ranked[0]
        if top_count == diagnosed and diagnosed >= 2:
            stable_kind = top_kind
    refuted_kinds = {c["hypothesis_kind"] for c in refuted}
    if stable_kind and stable_kind.split(":")[0] in refuted_kinds:
        top = refuted[0]
        return {
            "kind": stable_kind,
            "status": "refuted",
            "statement": (
                f"{stable_kind} led every per-run diagnosis, but an "
                f"executed check refutes it: {top['detail']}"
            ),
            "basis": "executed check overrides the scored ranking",
        }
    if stable_kind:
        return {
            "kind": stable_kind,
            "status": "reproducible",
            "statement": (
                f"{stable_kind} leads the diagnosis in all {diagnosed} "
                f"diagnosed runs — the cause reproduces, not just the "
                f"failure"
            ),
            "basis": f"consistent leading hypothesis across {diagnosed} runs",
        }
    if diagnosed >= 2 and kind_counts:
        parts = ", ".join(f"{k} in {v}" for k, v in
                          sorted(kind_counts.items(),
                                 key=lambda kv: (-kv[1], kv[0])))
        return {
            "kind": None,
            "status": "unstable",
            "statement": (
                f"the per-run diagnoses disagree ({parts}"
                + (f"; contested in {contested}" if contested else "")
                + ") — the cause is noise-sensitive, and no single-run "
                  "story should be trusted for this task"
            ),
            "basis": f"{diagnosed} runs diagnosed, no kind leads them all",
        }
    if diagnosed == 1:
        only = next(iter(kind_counts), None)
        return {
            "kind": only,
            "status": "single_run",
            "statement": (
                (f"{only} leads, but only one failing run was diagnosed — "
                 f"n=1, unconfirmed" if only else
                 "one failing run, contested diagnosis — n=1, unresolved")
            ),
            "basis": "single diagnosed run; add runs to confirm",
        }
    return {
        "kind": None,
        "status": "all_contested",
        "statement": (
            f"every diagnosed run was contested — the evidence never picks "
            f"a cause for {agent} on this task"
        ),
        "basis": f"{diagnosed} runs diagnosed, all contested",
    }


def consolidate_diagnoses(
    runs_by_task: dict[str, dict[str, list[Trajectory]]]
) -> dict:
    """Cross-run diagnosis for a two-agent multi-run corpus.

    ``runs_by_task`` maps task_id -> {"a": [runs...], "b": [runs...]} (the
    same shape ``stability_analysis`` takes).  For every failing run, the
    run is compared against the *medoid* run of the other agent — a stable
    reference — and diagnosed; the per-run diagnoses are then consolidated
    per (task, agent) and the executed discriminators applied.
    """
    entries = []
    # medoid_pairs asserts on empty sides; a task with runs on only one
    # side cannot be pairwise-diagnosed, so it is dropped here rather than
    # crashing three calls deep.
    complete = {tid: sides for tid, sides in runs_by_task.items()
                if sides.get("a") and sides.get("b")}
    medoids = {}
    for (a_med, b_med), tid in zip(medoid_pairs(complete),
                                   sorted(complete)):
        medoids[tid] = {"a": a_med, "b": b_med}
    runs_by_task = complete

    for tid in sorted(runs_by_task):
        sides = runs_by_task[tid]
        all_runs = sides["a"] + sides["b"]
        for side in ("a", "b"):
            agent_runs = sorted(sides[side], key=lambda t: t.run_id)
            if not agent_runs:
                continue
            agent = agent_runs[0].agent.name
            failing = [t for t in agent_runs if not t.outcome.success]
            n_runs = len(agent_runs)
            entry = {
                "task": tid,
                "agent": agent,
                "runs": n_runs,
                "failures": len(failing),
                "failure_reproduction": {
                    "k": len(failing), "n": n_runs,
                    "verdict": (
                        "reproducible" if len(failing) == n_runs and n_runs > 1
                        else "flaky" if 0 < len(failing) < n_runs
                        else "single run" if n_runs == 1 and failing
                        else "no failures"),
                },
                "diagnosed_runs": 0,
                "leading_kinds": {},
                "contested_runs": 0,
                "per_run": [],
                "checks_run": [],
                "consolidated": None,
            }
            if not failing:
                entries.append(entry)
                continue
            other = "b" if side == "a" else "a"
            reference = medoids[tid][other]
            kind_counts: dict[str, int] = {}
            contested = 0
            for run in failing:
                pair = ((run, reference) if side == "a"
                        else (reference, run))
                report = compare(*pair)
                diag = report["diagnosis"]
                if diag.get("mode") != "single_failure":
                    # the reference failed too; diagnosis cannot single out
                    # this run against it
                    entry["per_run"].append({
                        "run": run.run_id, "leading": None,
                        "note": "reference run also failed; pairwise "
                                "diagnosis not applicable"})
                    continue
                entry["diagnosed_runs"] += 1
                kind = _leading_kind(diag)
                if kind is None:
                    contested += 1
                    entry["per_run"].append(
                        {"run": run.run_id, "leading": None,
                         "note": "contested"})
                else:
                    kind_counts[kind] = kind_counts.get(kind, 0) + 1
                    entry["per_run"].append(
                        {"run": run.run_id, "leading": kind,
                         "margin": diag.get("margin")})
            entry["leading_kinds"] = dict(
                sorted(kind_counts.items(), key=lambda kv: (-kv[1], kv[0])))
            entry["contested_runs"] = contested

            checks = []
            for run in failing:
                grader = _grader_consistency(all_runs, run)
                if grader is not None:
                    checks.append(grader)
                env = _environment_reproduction(all_runs, run)
                if env is not None:
                    checks.append(env)
            flake = _harness_flake(agent_runs)
            if flake is not None:
                checks.append(flake)
            # one check instance per (check, outcome) is enough to state
            seen = set()
            for check in checks:
                key = (check["check"], check["outcome"])
                if key not in seen:
                    seen.add(key)
                    entry["checks_run"].append(check)

            entry["consolidated"] = _consolidated_verdict(
                kind_counts, entry["diagnosed_runs"], contested,
                entry["checks_run"], agent, len(failing), n_runs)
            entries.append(entry)

    diagnosed = [e for e in entries if e["consolidated"] is not None]
    confirmed = [e for e in diagnosed
                 if e["consolidated"]["status"] == "confirmed"]
    reproducible = [e for e in diagnosed
                    if e["consolidated"]["status"] == "reproducible"]
    unstable = [e for e in diagnosed
                if e["consolidated"]["status"] == "unstable"]
    flaky = [e for e in entries
             if e["failure_reproduction"]["verdict"] == "flaky"]
    lines = []
    if confirmed:
        lines.append(
            f"{len(confirmed)} diagnosis(es) confirmed by executed checks "
            f"against the corpus itself — no re-run needed")
    if reproducible:
        lines.append(
            f"{len(reproducible)} cause(s) reproduce across every failing "
            f"run")
    if unstable:
        lines.append(
            f"{len(unstable)} task(s) where per-run diagnoses disagree — "
            f"single-run stories are not trustworthy there")
    if flaky:
        names = ", ".join(f"{e['agent']} on {e['task']} "
                          f"({e['failure_reproduction']['k']} of "
                          f"{e['failure_reproduction']['n']})"
                          for e in flaky[:3])
        lines.append(f"flaky failures (treat as flakes until they repeat): "
                     f"{names}")
    narrative = "; ".join(lines) if lines else (
        "no failures to diagnose across these runs")

    return {
        "per_task_agent": entries,
        "summary": {
            "tasks": len(runs_by_task),
            "entries_with_failures": len(
                [e for e in entries if e["failures"]]),
            "confirmed_by_checks": len(confirmed),
            "reproducible_causes": len(reproducible),
            "unstable_diagnoses": len(unstable),
            "flaky_failures": len(flaky),
        },
        "narrative": narrative,
    }
