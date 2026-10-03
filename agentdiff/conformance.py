"""Conformance checking against reference trajectories (SCHEMA.md v11).

Everything else in AgentDiff compares two agents.  That is a real barrier:
most teams have *one* agent and nothing to diff it against.  This module
removes it by comparing a run against a **golden trajectory** — a recorded
run that a human blessed as the way the task should be done.

The comparison machinery is unchanged; only the framing moves.  Where a
pairwise report asks "which of these two is better", a conformance check
asks "did this run follow the approved procedure, and if not, where did it
leave the path".  That makes the same engine useful for behavioral
regression testing, procedure compliance, and onboarding a new model onto an
established workflow.

Verdicts, in increasing order of severity:

``conformant``
    Every step aligned; the run followed the reference exactly.
``drift``
    Same shape and same outcome, but step content differs — the agent
    reworded queries or picked different phrasing.  Usually benign.
``deviation``
    The run added or skipped steps relative to the reference while still
    reaching the reference outcome.  Worth reading; often a cost story.
``violation``
    The run's outcome differs from the reference.  This is a regression.
"""

from __future__ import annotations

from typing import Optional

from .report import compare
from .toolmatch import evaluate as toolmatch_evaluate
from .trace import Trajectory

#: verdicts ordered from clean to worst; used for sorting and rollups.
VERDICT_ORDER = ("conformant", "drift", "deviation", "violation")


def _verdict(report: dict, golden: Trajectory, candidate: Trajectory,
             max_extra_steps: int) -> str:
    """Classify one run against its reference."""
    if golden.outcome.success != candidate.outcome.success:
        return "violation"

    ops = [row["op"] for row in report["alignment"]]
    if all(op == "match" for op in ops):
        return "conformant"

    one_sided = sum(1 for op in ops if op in ("a_only", "b_only"))
    if one_sided > max_extra_steps:
        return "deviation"
    # Within tolerance: the run added or skipped steps, but no more than the
    # caller said to accept, so it counts as benign difference rather than a
    # departure from the procedure.
    if one_sided or any(op == "drift" for op in ops):
        return "drift"
    return "conformant"


def _conformance_score(report: dict) -> float:
    """Fraction of aligned rows that matched the reference exactly."""
    alignment = report["alignment"]
    if not alignment:
        return 1.0
    matched = sum(1 for row in alignment if row["op"] == "match")
    return round(matched / len(alignment), 4)


def _deviation_rows(report: dict, golden: Trajectory,
                    candidate: Trajectory) -> list[dict]:
    """Each divergence rephrased as reference-versus-run."""
    rows: list[dict] = []
    for divergence in report["divergences"]:
        g_index, c_index = divergence["a_index"], divergence["b_index"]
        reference_step = golden.steps[g_index] if g_index is not None else None
        actual_step = candidate.steps[c_index] if c_index is not None else None
        rows.append({
            "rank": divergence["rank"],
            "kind": divergence["kind"],
            "reference_step": g_index,
            "run_step": c_index,
            "reference_did": (
                f"{reference_step.type}: {reference_step.name}"
                if reference_step is not None else "no step here"
            ),
            "run_did": (
                f"{actual_step.type}: {actual_step.name}"
                if actual_step is not None else "skipped this step"
            ),
            "summary": divergence["summary"],
            "downstream": divergence["downstream"],
        })
    return rows


def check_run(
    golden: Trajectory,
    candidate: Trajectory,
    max_extra_steps: int = 0,
) -> dict:
    """Check one run against its reference trajectory.

    ``max_extra_steps`` tolerates that many added or skipped steps before a
    run is called a deviation — useful when the reference is a shape to
    follow rather than a script to replay.
    """
    if golden.task.id != candidate.task.id:
        raise ValueError(
            f"task mismatch: reference is {golden.task.id!r}, "
            f"run is {candidate.task.id!r}"
        )
    report = compare(golden, candidate)
    verdict = _verdict(report, golden, candidate, max_extra_steps)
    deviations = _deviation_rows(report, golden, candidate)

    first = deviations[0] if deviations else None
    if verdict == "conformant":
        narrative = (
            f"{candidate.agent.name} followed the reference exactly on "
            f"{candidate.task.id}."
        )
    elif verdict == "violation":
        attribution = report["attribution"]
        detail = attribution.get("explanation") or "the run reached a different outcome"
        narrative = (
            f"{candidate.agent.name} broke from the reference on "
            f"{candidate.task.id}: reference "
            f"{'succeeded' if golden.outcome.success else 'failed'}, run "
            f"{'succeeded' if candidate.outcome.success else 'failed'}. {detail}"
        )
    else:
        where = f"step {first['run_step']}" if first and first["run_step"] is not None \
            else "the reference path"
        narrative = (
            f"{candidate.agent.name} left the reference path at {where} "
            f"({first['kind'] if first else 'unknown'}) on {candidate.task.id} "
            f"but still reached the reference outcome."
        )

    return {
        "task": candidate.task.id,
        "agent": candidate.agent.name,
        "reference_agent": golden.agent.name,
        "verdict": verdict,
        "conformance": _conformance_score(report),
        "outcome_matches_reference": (
            golden.outcome.success == candidate.outcome.success
        ),
        "steps": {"reference": len(golden.steps), "run": len(candidate.steps)},
        "tokens": {
            "reference": sum(s.tokens for s in golden.steps),
            "run": sum(s.tokens for s in candidate.steps),
        },
        "deviations": deviations,
        # The reference is exactly what the industry's trajectory-match
        # vocabulary needs, so a conformance check is the natural place to
        # emit it: same finding, in terms other tools already use.
        "tool_match": toolmatch_evaluate(candidate, golden),
        "narrative": narrative,
        "report": report,
    }


def check_suite(
    goldens: dict[str, Trajectory],
    candidates: dict[str, Trajectory],
    max_extra_steps: int = 0,
) -> dict:
    """Check a set of runs against a set of reference trajectories.

    Tasks present in only one side are reported rather than silently
    dropped: an unmatched run has no reference to judge it, and an unused
    reference usually means the run set is incomplete.
    """
    shared = sorted(set(goldens) & set(candidates))
    checks = [
        check_run(goldens[task], candidates[task], max_extra_steps)
        for task in shared
    ]

    counts = {verdict: 0 for verdict in VERDICT_ORDER}
    for check in checks:
        counts[check["verdict"]] += 1

    kinds: dict[str, int] = {}
    for check in checks:
        for deviation in check["deviations"]:
            kinds[deviation["kind"]] = kinds.get(deviation["kind"], 0) + 1

    violations = [c["task"] for c in checks if c["verdict"] == "violation"]
    conformance = (
        round(sum(c["conformance"] for c in checks) / len(checks), 4)
        if checks else 1.0
    )
    clean = counts["conformant"] + counts["drift"]

    if not checks:
        narrative = "No task had both a reference trajectory and a run to check."
    elif violations:
        narrative = (
            f"{len(violations)} of {len(checks)} run(s) broke from the reference "
            f"outcome ({', '.join(violations)}). Mean step-level conformance is "
            f"{conformance:.0%}."
        )
    elif counts["deviation"]:
        narrative = (
            f"All {len(checks)} run(s) reached the reference outcome, but "
            f"{counts['deviation']} took a different path. Mean step-level "
            f"conformance is {conformance:.0%}."
        )
    else:
        narrative = (
            f"All {len(checks)} run(s) followed the reference "
            f"({clean} conformant or benign-drift). Mean step-level conformance "
            f"is {conformance:.0%}."
        )
    if kinds:
        worst = max(sorted(kinds), key=lambda k: kinds[k])
        narrative += f" Deviations concentrate in {worst} ({kinds[worst]})."

    return {
        "checks": sorted(
            checks, key=lambda c: (-VERDICT_ORDER.index(c["verdict"]), c["task"])
        ),
        "counts": counts,
        "mean_conformance": conformance,
        "violations": violations,
        "deviation_kinds": dict(sorted(kinds.items())),
        "missing_reference": sorted(set(candidates) - set(goldens)),
        "unused_reference": sorted(set(goldens) - set(candidates)),
        "narrative": narrative,
    }


def render_conformance_markdown(suite: dict, reference_label: str = "reference") -> str:
    """A shareable conformance summary — CI comment or PR body sized."""
    passed = not suite["violations"]
    lines = [
        f"# Conformance check: {'✅ PASS' if passed else '❌ FAIL'}",
        "",
        suite["narrative"],
        "",
        "| task | verdict | conformance | steps (ref → run) |",
        "|---|---|---|---|",
    ]
    for check in suite["checks"]:
        lines.append(
            f"| {check['task']} | {check['verdict']} | "
            f"{check['conformance']:.0%} | "
            f"{check['steps']['reference']} → {check['steps']['run']} |"
        )

    off_path = [c for c in suite["checks"] if c["verdict"] != "conformant"]
    if off_path:
        lines += ["", f"## Where runs left the {reference_label}", ""]
        for check in off_path:
            lines.append(f"### {check['task']} — {check['verdict']}")
            lines.append("")
            lines.append(check["narrative"])
            for deviation in check["deviations"][:3]:
                lines.append(
                    f"- **{deviation['kind']}** at run step "
                    f"{deviation['run_step']}: {deviation['summary']}"
                )
            lines.append("")

    if suite["missing_reference"]:
        lines += [
            "",
            f"> {len(suite['missing_reference'])} run(s) had no reference "
            f"trajectory and were not checked: "
            f"{', '.join(suite['missing_reference'])}.",
        ]
    if suite["unused_reference"]:
        lines += [
            "",
            f"> {len(suite['unused_reference'])} reference trajectory(ies) had no "
            f"matching run: {', '.join(suite['unused_reference'])}.",
        ]
    return "\n".join(lines) + "\n"
