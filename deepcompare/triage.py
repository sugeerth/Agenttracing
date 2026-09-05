"""Triage: of everything this report found, what to fix first (v23).

AgentDiff's problem is no longer that it sees too little.  A batch now emits
divergences, attribution, systematic issues, recommendations, a playbook,
process-integrity flags, attribute lifts, taxonomy labels and reliability
curves — and hands the reader the job of working out which of them to act on
first.  Depth without an ordering is not usefulness; it is homework.

This module produces one ranked list of **actions**: what to change, in what
order, on what evidence, for what estimated gain.  Everything in it is
already computed elsewhere.  The value added here is entirely in the
*ordering*, the *merging*, and the *refusals* — so all three are written down
rather than buried in a sort key.

Three disciplines run through it, and they are the reason it can be trusted
to sit at the top of a report:

* **Sample size damps rank, visibly.**  A finding backed by one occurrence is
  an anecdote.  It is not dropped — it may be the only trace of a real bug —
  but it is multiplied down by a factor that is printed next to it, with the
  Wilson interval on its occurrence rate beside the point estimate.  A reader
  who disagrees can see exactly what was applied and undo it.
* **Never invent an impact number.**  Failures avoided and tokens saved are
  read from measured downstream costs, and when a block reports its cost only
  as prose, the saving is ``None`` with a reason — never a plausible figure.
  Cost in dollars is derived from the corpus's own realised price per token
  and says so.
* **Refuse rather than launder.**  When the reliability block says the run
  count cannot support a claim that one agent differs from another, every
  cross-agent claim here is damped and capped at low confidence, and the
  refusal is quoted.  Process pathologies are exempt: a loop is visible in a
  single trace and needs no comparison to be real.

The ranking scheme is deliberately a small product of factors rather than a
learned score.  It is defensible because it can be recited::

    score = base(severity class) x evidence(sample size) x reliability(gate)

with ties broken by failures caused, then wasted tokens, then identity.  The
weights are constants at the top of this module and are reported in the
output under ``scheme`` so a reader never has to read this file to know why
row 3 is row 3.
"""

from __future__ import annotations

from typing import Optional

from .diagnosis import LEAD_MARGIN
from .statistics import binomial_tail, wilson_interval

#: Base weight per severity class.  The gaps encode three judgements:
#:
#: * something that *caused a failure* outranks something that merely cost
#:   tokens — no amount of waste is worth a wrong answer;
#: * a pathology on a run that **passed** is nearly as urgent as a failure,
#:   because no other part of this pipeline will ever flag it — the oracle is
#:   satisfied, the gate is green, and the loop ships;
#: * the same pathology on a run that already failed is worth much less: the
#:   failure analysis has that task covered, so this is corroboration, not a
#:   new problem.
BASE_SCORE = {
    "failure": 100.0,
    "passing_pathology": 80.0,
    "failing_pathology": 40.0,
    "cost": 30.0,
    "signal": 25.0,
}

#: Why each class carries the weight it does — printed with every action so
#: the rank is self-explaining.
SEVERITY_REASON = {
    "failure": "caused at least one run to fail",
    "passing_pathology": (
        "a pathological process on a run that passed — nothing else in this "
        "report flags it, because the outcome looks fine"
    ),
    "failing_pathology": (
        "a pathological process on a run that already failed — corroborates "
        "the failure analysis rather than adding to it"
    ),
    "cost": "cost tokens or latency without changing any outcome",
    "signal": "a statistical association, not an observed cause",
}

#: A single occurrence is multiplied by this.  One occurrence cannot be
#: distinguished from a one-off, and pretending otherwise is how a triage
#: list stops being read.  It damps rather than deletes because a one-off
#: crash is still a crash.
SINGLE_OCCURRENCE_FACTOR = 0.6
#: Per extra task beyond the second, capped: recurrence is evidence, but the
#: fifth task it recurs on does not make it five times more urgent.
RECURRENCE_BONUS = 0.1
RECURRENCE_BONUS_CAP = 3
#: Applied to cross-agent claims when the reliability block says the sample
#: cannot support them.
UNDERPOWERED_FACTOR = 0.5

#: The issues block names a divergence kind, the recommendations block names
#: a fix category, and they disagree on exactly one word: a ``stopping``
#: divergence is what produces an ``efficiency`` recommendation.  Merging is
#: done on category, so the vocabularies have to be reconciled first or the
#: same problem stays split across two rows forever.
CATEGORY_ALIASES = {"stopping": "efficiency"}

#: Process flag -> the category it belongs to, which is also what it merges
#: on.  Several loop-shaped flags share ``efficiency`` on purpose: "repeated
#: the same call", "took steps returning nothing new" and "finished on the
#: edge of its budget" on one run are one problem described three ways.
FLAG_CATEGORY = {
    "false_success": "verification",
    "looped": "efficiency",
    "loop_block": "efficiency",
    "repeated_calls": "efficiency",
    "no_information_steps": "efficiency",
    "budget_pressure": "efficiency",
    "swallowed_error": "recovery",
    "blind_write": "safety",
    # Deliberately not ``tool_selection``: choosing badly among the tools you
    # were offered and calling one you were never offered have different
    # fixes, and merging them would produce an action that names neither.
    "undeclared_tools": "tool_availability",
    "invented_arguments": "grounding",
    "schema_violation": "tool_execution",
}

#: Coarse effort classes.  Honest about what they are: a guess from the shape
#: of the finding, made without ever having seen the system being fixed.
EFFORT = {
    "retrieval": ("prompt", "a system-prompt rule about which sources to trust"),
    "tool_selection": ("prompt", "a prompt rule, or a clearer tool description"),
    "tool_availability": ("tool-schema",
                          "reconcile the tool list the agent is given with the "
                          "tools it actually calls"),
    "tool_execution": ("tool-schema",
                       "argument validation, or a stricter parameter schema"),
    "planning": ("prompt", "a prompt rule requiring an explicit plan"),
    "reasoning": ("prompt", "a prompt rule about reconciling conflicting evidence"),
    "efficiency": ("control-flow",
                   "loop detection or a stop rule in the agent loop"),
    "prompt_cache": ("infrastructure",
                     "a stable prompt prefix so the provider cache can hold it"),
    "result_cache": ("infrastructure",
                     "memoise identical tool calls at the harness layer"),
    "parallel_reads": ("control-flow",
                       "issue independent read-only calls concurrently"),
    "latency_concentration": ("investigation",
                              "profile the named slow steps before changing "
                              "anything"),
    "recovery": ("control-flow", "error handling around the tool call"),
    "safety": ("architecture", "a read-before-write guard on state-changing tools"),
    "verification": ("architecture",
                     "a verification step between acting and claiming success"),
    "grounding": ("tool-schema",
                  "argument provenance checks, or constrained argument sources"),
    "calibration": ("architecture",
                    "an independent verification step; a confidence threshold "
                    "will not work on an agent that is wrong while confident"),
    "attribute": ("investigation", "confirm the association before changing anything"),
    "regression": ("investigation", "find what the change cost before shipping it"),
    "oracle": ("investigation", "re-check the grader, not the agent"),
}

EFFORT_NOTE = (
    "Heuristic: inferred from the kind of finding, not from your codebase. "
    "Treat it as a starting guess about where the fix lives."
)


# --------------------------------------------------------------------------
# candidate construction
# --------------------------------------------------------------------------


def _candidate(**kwargs) -> dict:
    """A finding in the common shape every source is normalised into.

    Sources disagree about almost everything — issues count divergences,
    recommendations count tasks, process counts steps — so they are flattened
    into one record before anything is compared or merged.  Fields left at
    their defaults mean "this source does not measure that", which is
    different from zero and is preserved as such all the way to ``impact``.
    """
    base = {
        "id": "",
        "source": "",
        "category": "",
        "severity_class": "cost",
        "agents": [],
        "tasks": [],
        "passing_tasks": [],
        "comparative": True,
        "failures": None,
        "tokens": None,
        "latency_s": None,
        "occurrences": 0,
        "flags": [],
        "details": [],
        "steps": [],
        "fingerprints": [],
        "fix_hint": None,
        "confidence_cap": None,
        "confidence_floor": None,
        "basis_notes": [],
        "sample": None,
        "stat": None,
    }
    base.update(kwargs)
    return base


def _from_issues(aggregate: dict) -> list[dict]:
    """Systematic issues: already clustered, already costed, already ranked.

    The one thing they lack is a sense of proportion against everything else
    in the report, which is precisely what this module supplies.  Suppressed
    issues are not candidates — a team has judged them benign — but they are
    reported in ``not_actionable`` so suppression stays visible.
    """
    block = aggregate.get("issues") or {}
    candidates = []
    for issue in block.get("issues", []):
        if issue.get("suppressed"):
            continue
        failures = int(issue.get("failures_caused") or 0)
        tokens = int(issue.get("extra_tokens") or 0)
        latency = float(issue.get("extra_latency_s") or 0.0)
        tasks = list(issue.get("tasks") or [])
        steps = [
            {"task": occurrence["task"], "a_index": occurrence.get("a_index"),
             "b_index": occurrence.get("b_index"),
             "what": occurrence.get("summary", "")}
            for occurrence in issue.get("occurrences", [])
        ]
        kind = issue.get("kind") or "reasoning"
        candidates.append(_candidate(
            id=f"issue:{issue['id']}",
            source="issues",
            category=CATEGORY_ALIASES.get(kind, kind),
            severity_class="failure" if failures else "cost",
            agents=list(issue.get("agents") or []),
            tasks=tasks,
            failures=failures,
            tokens=tokens,
            latency_s=round(latency, 4),
            occurrences=int(issue.get("occurrence_count") or 0),
            details=[issue.get("summary", "")],
            steps=steps,
            fingerprints=[issue["id"]],
        ))
    return candidates


def _from_recommendations(aggregate: dict) -> list[dict]:
    """Recommendations: the same problems, with the paste-ready fix attached.

    They are kept as separate candidates and then merged rather than being
    read as an index into the issues, because the two blocks group
    differently — issues by behaviour fingerprint, recommendations by (agent,
    category) — and only the merge step knows which pairs actually coincide.
    Their costs are prose (``expected_gain``), so nothing numeric is taken
    from them; the ``suggested_prompt`` is the payload worth carrying.
    """
    candidates = []
    for index, rec in enumerate(aggregate.get("recommendations") or []):
        tasks = list(rec.get("evidence_tasks") or [])
        critical = rec.get("severity") == "critical"
        category = rec.get("category") or "reasoning"
        candidates.append(_candidate(
            id=f"rec:{rec.get('agent', '')}:{rec.get('category', '')}:{index}",
            source="recommendations",
            category=CATEGORY_ALIASES.get(category, category),
            severity_class="failure" if critical else "cost",
            agents=[rec["agent"]] if rec.get("agent") else [],
            tasks=tasks,
            # Recommendations report their own failure count in the finding
            # text only; the task list is the countable part of it.
            failures=len(tasks) if critical else 0,
            details=[rec.get("finding", "")],
            fix_hint=rec.get("suggested_prompt"),
            basis_notes=[f"recommendation's stated gain: {rec['expected_gain']}"]
            if rec.get("expected_gain") else [],
        ))
    return candidates


def _flag_evidence(parts: dict, flag: str, agent: str, task: str) -> tuple:
    """One sentence of concrete evidence for one raised process flag.

    Returns ``(detail, steps, inferred)``.  ``inferred`` reports whether the
    underlying signal was declared by the log or read out of observation
    text, because a flag inferred from the word "error" appearing in an
    output is weaker evidence than one the harness declared, and the
    confidence must be capped accordingly.
    """
    repeat = parts["repeats"]
    fail = parts["recovery"]
    ledger = parts["side_effects"]
    ground = parts["grounding"]
    stop = parts["termination"]
    schema = parts["schema"]
    steps: list[dict] = []
    inferred = False

    def mark(records, what):
        for record in records[:4]:
            steps.append({"task": task, "agent": agent,
                          "index": record.get("index"), "what": what})

    if flag == "false_success":
        claim = parts["false_success"]
        mark([], "")
        return (
            f"{agent} claimed completion on {task} "
            f"({', '.join(claim['claim_phrases'][:3]) or 'no phrase recorded'}) "
            f"while writing 0 of the {ledger['reads']} tool step(s) it ran",
            steps, False,
        )
    if flag in ("looped", "loop_block"):
        block = parts["loops"]["longest_repeated_block"]
        mark(repeat["cycle_steps"], "repeated call-and-result")
        return (
            f"{agent} looped on {task}: {repeat['cycles']} repeated "
            f"call-and-result pair(s), longest repeated block of "
            f"{block['period']} call(s) run {block['repeats']} time(s) from "
            f"step {block['starts_at']}",
            steps, False,
        )
    if flag == "repeated_calls":
        mark(repeat["repeated_steps"], "identical earlier call")
        return (
            f"{agent} made {repeat['repeated_calls']} identical repeat call(s) "
            f"on {task} out of {stop['steps']} step(s)",
            steps, False,
        )
    if flag == "no_information_steps":
        mark(repeat["no_information_detail"], "observation seen before")
        return (
            f"{repeat['no_information_steps']} of {agent}'s {stop['steps']} "
            f"step(s) on {task} returned an observation already seen",
            steps, False,
        )
    if flag == "budget_pressure":
        return (
            f"{agent} finished {task} at {stop['steps']}/{stop['max_steps']} "
            f"step(s) ({stop['budget_used']:.0%} of budget) — close enough to "
            f"the ceiling that finishing may have been luck",
            steps, False,
        )
    if flag == "swallowed_error":
        mark(fail["error_steps"], "error observation")
        inferred = not fail["basis"].startswith("declared")
        return (
            f"{agent} hit {fail['errors']} error(s) on {task} and recovered "
            f"from {fail['recovered']} ({fail['abandoned_after_error']} "
            f"abandoned after the error), basis: {fail['basis']}",
            steps, inferred,
        )
    if flag == "blind_write":
        mark(ledger["blind_write_steps"], "write before any successful read")
        inferred = not ledger["basis"].startswith("declared")
        return (
            f"{agent} made {ledger['writes_before_any_read']} of its "
            f"{ledger['writes']} write(s) on {task} before any successful "
            f"read, effects {ledger['basis']}",
            steps, inferred,
        )
    if flag == "undeclared_tools":
        mark(ground["undeclared_tool_steps"], "tool not in the declared list")
        return (
            f"{agent} called {ground['undeclared_tool_calls']} tool(s) on "
            f"{task} that were not in the {ground['calls']} declared call(s) "
            f"it was offered",
            steps, False,
        )
    if flag == "invented_arguments":
        mark(ground["invented_arguments"], "argument value absent from the trace")
        return (
            f"{ground['arguments_without_source']} of "
            f"{ground['arguments_checked']} checked argument value(s) in "
            f"{agent}'s run on {task} appear nowhere in the prompt or any "
            f"earlier observation",
            steps, True,
        )
    if flag == "schema_violation":
        mark(schema["detail"], "call does not typecheck")
        kinds = sorted({problem["kind"] for problem in schema["detail"]})
        return (
            f"{schema['violations']} of {agent}'s {schema['checked']} checked "
            f"call(s) on {task} do not typecheck against the declared schema"
            + (f" ({', '.join(kinds)})" if kinds else ""),
            steps, False,
        )
    return (f"{agent} raised {flag} on {task}", steps, False)


def _from_process(reports: list[dict]) -> list[dict]:
    """Process pathologies, grouped per (agent, flag) across the batch.

    Grouping happens here rather than in the merge step because a flag is
    already the same behaviour by construction: "looped" on two tasks is one
    problem seen twice, and counting it as two findings would double its
    weight for the wrong reason.

    Process findings are marked ``comparative=False``.  They are properties
    of a single trace — a loop is a loop with no second agent in the room —
    so the reliability gate, which is about comparing agents, does not apply
    to them.
    """
    groups: dict[tuple, dict] = {}
    for report in reports:
        task = report["task"]["id"]
        process = report.get("process") or {}
        for side in ("a", "b"):
            parts = process.get(side)
            if not parts:
                continue
            gap = parts["gap"]
            agent = parts.get("agent") or report[side]["agent"]["name"]
            passed = bool(gap["success"])
            for flag in gap["raised"]:
                detail, steps, inferred = _flag_evidence(parts, flag, agent, task)
                key = (agent, flag)
                group = groups.get(key)
                if group is None:
                    group = groups[key] = _candidate(
                        id=f"process:{agent}:{flag}",
                        source="process",
                        category=FLAG_CATEGORY.get(flag, "reasoning"),
                        severity_class="failing_pathology",
                        agents=[agent],
                        tasks=[],
                        comparative=False,
                        flags=[flag],
                    )
                group["tasks"].append(task)
                if passed:
                    group["passing_tasks"].append(task)
                    group["severity_class"] = "passing_pathology"
                group["occurrences"] += 1
                group["details"].append(detail)
                group["steps"].extend(steps)
                if inferred:
                    note = ("this flag was inferred from the trace text, not "
                            "declared by the harness")
                    if note not in group["basis_notes"]:
                        group["basis_notes"].append(note)
                        group["confidence_cap"] = "medium"
    return [groups[key] for key in sorted(groups)]


def _from_oracle(reports: list[dict]) -> list[dict]:
    """Runs that failed with a completely clean process and no attributed cause.

    A failure the divergence analysis can explain is the agent's problem.  A
    failure with no attributed root cause *and* nothing wrong in the trace is
    evidence about the grader as much as the agent, and it is the one finding
    here whose action points away from the agent entirely.
    """
    per_agent: dict[str, dict] = {}
    for report in reports:
        attribution = report.get("attribution") or {}
        if attribution.get("failed_agent") is not None:
            continue  # the report already explains this failure
        task = report["task"]["id"]
        process = report.get("process") or {}
        for side in ("a", "b"):
            parts = process.get(side)
            if not parts or parts["gap"]["verdict"] != "failed but clean":
                continue
            agent = parts.get("agent") or report[side]["agent"]["name"]
            group = per_agent.get(agent)
            if group is None:
                group = per_agent[agent] = _candidate(
                    id=f"oracle:{agent}",
                    source="process",
                    category="oracle",
                    severity_class="signal",
                    agents=[agent],
                    comparative=False,
                    confidence_cap="medium",
                )
            group["tasks"].append(task)
            group["occurrences"] += 1
            group["details"].append(
                f"{agent} failed {task} with no unrecovered error, no loop, no "
                f"blind write and no root cause attributed to either side"
            )
    return [per_agent[key] for key in sorted(per_agent)]


def _from_diagnosis(reports: list[dict]) -> list[dict]:
    """Adjudicated diagnoses that should redirect effort, not add to it.

    Attribution always tells a story, and most triage sources assume that
    story points at the agent.  The diagnosis layer ranks that story against
    every rival hypothesis; two of its outcomes change what the right next
    action even is.  A leading ``grader_or_label`` hypothesis means the
    cheapest correct move is re-grading a handful of tasks by hand, not
    engineering work on the agent.  A contested diagnosis means the evidence
    cannot pick a cause, and the honest action is the discriminating check —
    spending fix effort before running it is a coin flip.
    """
    grader: dict[str, dict] = {}
    contested: dict[str, dict] = {}
    for report in reports:
        diag = report.get("diagnosis") or {}
        if diag.get("mode") != "single_failure":
            continue
        task = report["task"]["id"]
        agent = diag.get("subject_name") or "the failing agent"
        hypotheses = diag.get("hypotheses", [])
        lead = next((h for h in hypotheses if h.get("id") == diag.get("leading")),
                    None)
        if lead is not None and lead.get("kind") == "grader_or_label":
            group = grader.get(agent)
            if group is None:
                group = grader[agent] = _candidate(
                    id=f"diagnosis-grader:{agent}",
                    source="diagnosis",
                    category="grader_suspect",
                    severity_class="signal",
                    agents=[agent],
                    comparative=False,
                    confidence_cap="medium",
                    fix_hint=lead.get("discriminator"),
                )
            group["tasks"].append(task)
            group["occurrences"] += 1
            group["details"].append(
                f"{agent} failed {task}, but the adjudicated diagnosis ranks "
                f"the grader-or-label hypothesis first (score {lead['score']}, "
                f"margin {diag.get('margin')} over the runner-up): "
                f"{lead['statement']}"
            )
        elif lead is None and any(
                h.get("score") is not None and h.get("status") != "merged"
                for h in hypotheses):
            active = [h for h in hypotheses
                      if h.get("score") is not None
                      and h.get("status") != "merged"]
            top = active[0]
            group = contested.get(agent)
            if group is None:
                group = contested[agent] = _candidate(
                    id=f"diagnosis-contested:{agent}",
                    source="diagnosis",
                    category="contested_diagnosis",
                    severity_class="signal",
                    agents=[agent],
                    comparative=False,
                    confidence_cap="low",
                    fix_hint=top.get("discriminator"),
                )
            group["tasks"].append(task)
            group["occurrences"] += 1
            rivals = ", ".join(
                h["kind"] + (f":{h['flag']}" if h.get("flag") else "")
                for h in active[:3])
            group["details"].append(
                f"on {task} the evidence does not pick a single cause "
                f"({rivals} within {LEAD_MARGIN:.2f} of each other); the "
                f"first discriminating check: {top.get('discriminator')}"
            )
    out = [grader[k] for k in sorted(grader)]
    out += [contested[k] for k in sorted(contested)]
    return out


def _from_consolidated(aggregate: dict) -> list[dict]:
    """Cross-run verdicts that upgrade or overrule single-pair findings.

    A consolidated diagnosis carries evidence a pair cannot: ``confirmed``
    means an executed check against the corpus itself settled the question
    (no re-run, no human needed to establish the fact), and ``unstable``
    means the per-run diagnoses disagree, which is a warning against acting
    on any of them.  Both deserve rows of their own — the confirmed one
    with the executed check quoted, the unstable one as an explicit
    do-not-fix-yet.
    """
    consolidated = (aggregate.get("diagnosis_consolidated") or {})
    out = []
    for entry in consolidated.get("per_task_agent", []):
        verdict = entry.get("consolidated")
        if not verdict:
            continue
        agent = entry.get("agent", "?")
        task = entry.get("task", "?")
        repro = entry.get("failure_reproduction") or {}
        if verdict.get("status") == "confirmed":
            out.append(_candidate(
                id=f"consolidated-confirmed:{agent}:{task}",
                source="diagnosis",
                category="confirmed_cause",
                severity_class="signal",
                agents=[agent],
                tasks=[task],
                comparative=False,
                occurrences=repro.get("k") or 1,
                confidence_floor="high",
                fix_hint=verdict.get("statement"),
                details=[
                    f"{agent} on {task}: an executed check against the "
                    f"corpus settled this — {verdict.get('statement')}"
                ],
                basis_notes=["confirmed by an executed check, not a score"],
            ))
        elif verdict.get("status") == "unstable":
            out.append(_candidate(
                id=f"consolidated-unstable:{agent}:{task}",
                source="diagnosis",
                category="unstable_diagnosis",
                severity_class="signal",
                agents=[agent],
                tasks=[task],
                comparative=False,
                occurrences=entry.get("diagnosed_runs") or 1,
                confidence_cap="low",
                details=[verdict.get("statement", "")],
            ))
    return out


def _from_attributes(aggregate: dict) -> tuple[list[dict], list[dict]]:
    """Attribute lifts, but only the ones whose interval excludes zero.

    The attribute block already computes a paired bootstrap for every lift.
    Promoting a lift whose interval straddles zero into an action would be
    exactly the laundering this module exists to prevent, so those become
    ``not_actionable`` entries quoting their own interval.
    """
    block = aggregate.get("attributes") or {}
    runs = int(block.get("runs") or 0)
    actions, excluded = [], []
    for entry in block.get("attributes", []):
        interval = entry.get("interval") or {}
        with_runs = int((entry.get("with") or {}).get("runs") or 0)
        name = entry.get("attribute", "attribute")
        phrasing = entry.get("phrasing", name)
        if not entry.get("measurable", True):
            excluded.append({
                "finding": f"attribute lift for {name}",
                "source": "attributes",
                "reason": "unmeasurable",
                "detail": f"the corpus has no contrast for {phrasing}.",
            })
            continue
        if not interval.get("significant"):
            excluded.append({
                "finding": f"attribute lift for {name} ({phrasing})",
                "source": "attributes",
                "reason": ("too few samples" if with_runs < 3
                           else "not distinguishable from noise"),
                "detail": (
                    f"failure rate is "
                    f"{(entry['with']['failure_rate']):.0%} with it "
                    f"({entry['with']['runs']} run(s)) against "
                    f"{(entry['without']['failure_rate']):.0%} without "
                    f"({entry['without']['runs']} run(s)), but the 95% "
                    f"bootstrap interval on the lift "
                    f"({interval.get('low', 0):+.2f} to "
                    f"{interval.get('high', 0):+.2f}) includes zero — the "
                    f"same corpus resampled could produce it."
                ),
            })
            continue
        actions.append(_candidate(
            id=f"attribute:{name}",
            source="attributes",
            category="attribute",
            severity_class="signal",
            occurrences=with_runs,
            details=[
                f"runs where {phrasing} fail "
                f"{entry['with']['failure_rate']:.0%} of the time "
                f"({entry['with']['runs']} run(s) of {runs}) against "
                f"{entry['without']['failure_rate']:.0%} without it "
                f"({entry['without']['runs']} run(s)); lift "
                f"{entry['lift']:+.2f}, 95% interval "
                f"{interval['low']:+.2f} to {interval['high']:+.2f}"
            ],
            sample={"k": with_runs, "n": runs, "unit": "run"},
            stat=interval,
            confidence_cap="medium",
            basis_notes=["an association across runs, not an observed cause"],
            fix_hint=phrasing,
        ))
    return actions, excluded


def _from_calibration(aggregate: dict) -> tuple[list[dict], list[dict]]:
    """Agents that are wrong while confident need a different fix entirely.

    Worth its own action because the obvious remedy — thresholding on
    self-reported confidence — is precisely the one that cannot work on a
    silent-failing agent, and no other block says so.
    """
    if "calibration" not in aggregate:
        # The block did not run at all; there is nothing to report about it,
        # and an exclusion notice for an analysis nobody asked for is noise.
        return [], []
    block = aggregate.get("calibration") or {}
    if not block.get("available"):
        return [], [{
            "finding": "confidence calibration",
            "source": "calibration",
            "reason": "unmeasurable",
            "detail": "no run in this batch carries confidence telemetry.",
        }]
    actions, excluded = [], []
    for agent in sorted(block.get("agents") or {}):
        row = block["agents"][agent]
        failures = int(row.get("failures_with_telemetry") or 0)
        if row.get("verdict") != "silent-failing":
            excluded.append({
                "finding": f"calibration of {agent} ({row.get('verdict')})",
                "source": "calibration",
                "reason": "informational",
                "detail": (
                    f"{agent} flagged {row.get('flagged')} of {failures} "
                    f"failure(s) it had telemetry for; nothing to fix."
                ),
            })
            continue
        actions.append(_candidate(
            id=f"calibration:{agent}",
            source="calibration",
            category="calibration",
            severity_class="signal",
            agents=[agent],
            comparative=False,
            occurrences=failures,
            details=[
                f"{agent} self-flagged {row.get('flagged')} of {failures} "
                f"failure(s) that carried telemetry; mean confidence when "
                f"wrong was {row.get('mean_confidence_when_wrong')}"
            ],
            sample={"k": failures, "n": failures, "unit": "failure with telemetry"},
            confidence_cap="medium",
            basis_notes=["measured only on failures that carry confidence telemetry"],
        ))
    return actions, excluded


def _from_regressions(aggregate: dict) -> list[dict]:
    """Metric regressions stated by the aggregate, carried through verbatim.

    They are already sentences with their own numbers in them; re-deriving
    the figures here would risk disagreeing with the block they came from.
    """
    candidates = []
    for index, text in enumerate(aggregate.get("regressions") or []):
        candidates.append(_candidate(
            id=f"regression:{index}",
            source="regressions",
            category="regression",
            severity_class="cost",
            occurrences=int(aggregate.get("tasks") or 0),
            details=[text],
            sample={"k": int(aggregate.get("tasks") or 0),
                    "n": int(aggregate.get("tasks") or 0), "unit": "task"},
        ))
    return candidates


# --------------------------------------------------------------------------
# merging
# --------------------------------------------------------------------------


def _from_efficiency(aggregate: dict) -> list[dict]:
    """Serving-cost opportunities: cacheable calls, parallel reads, resends.

    These are properties of one agent's own traces, not comparisons — so like
    process pathologies they are exempt from the reliability gate: a repeated
    identical call is visible in a single run and needs no second agent to
    be true.  Their savings are **ceilings** (a cache hit rate of 100% is an
    assumption, not a forecast), and that framing must survive into the
    action rather than being flattened into "saves N tokens".
    """
    efficiency = aggregate.get("efficiency") or {}
    candidates = []
    for side_block in (efficiency.get("per_agent") or {}).values():
        agent = side_block.get("agent")
        for opportunity in side_block.get("opportunities") or []:
            kind = opportunity.get("kind") or "efficiency"
            saving = opportunity.get("saving") or {}
            evidence = opportunity.get("evidence") or {}
            tasks = list(evidence.get("tasks") or [])
            basis = opportunity.get("basis") or "estimated"
            candidates.append(_candidate(
                id=f"efficiency/{kind}/{agent}",
                source="efficiency",
                category=kind,
                severity_class="cost",
                agents=[agent] if agent else [],
                tasks=tasks,
                comparative=False,
                occurrences=evidence.get("occurrences") or len(tasks),
                tokens=saving.get("tokens"),
                latency_s=saving.get("latency_s"),
                details=[opportunity.get("action")],
                fix_hint=opportunity.get("action"),
                basis_notes=[
                    f"the saving is a ceiling, not a forecast: it assumes the "
                    f"{kind.replace('_', ' ')} fully lands (basis: {basis})",
                ],
            ))
    return candidates


def _agents_compatible(left: list, right: list) -> bool:
    """Unknown attribution is compatible with anything; two named agents are
    compatible only with themselves.  A non-fatal issue often names no agent,
    and refusing to merge it with the recommendation that does would leave
    the reader with two rows about one problem."""
    if not left or not right:
        return True
    return bool(set(left) & set(right))


def _mergeable(left: dict, right: dict) -> bool:
    """Two findings are the same problem when they share a category and do not
    disagree about the agent — plus, for unattributed findings, a shared task.

    The asymmetry is the point.  Two findings that both name *the same agent*
    in the same category are one problem however many tasks it showed up on;
    keeping them apart produces two rows with the identical imperative on
    them, which is the near-duplicate this module exists to remove.  A finding
    that names no agent has not earned that: with two agents in the report it
    could belong to either, so it needs a task in common to prove which action
    it belongs to.
    """
    if left["category"] != right["category"]:
        return False
    if not _agents_compatible(left["agents"], right["agents"]):
        return False
    if left["agents"] and right["agents"]:
        return True
    if not left["tasks"] or not right["tasks"]:
        return False
    return bool(set(left["tasks"]) & set(right["tasks"]))


def _absorb(group: dict, other: dict) -> None:
    """Fold ``other`` into ``group``, keeping the strongest severity class.

    Counts are combined with ``max``, never summed: issues and
    recommendations describe *the same runs* from two angles, so adding their
    failure counts would double-count real failures into an impact estimate
    that cannot happen.
    """
    order = sorted(BASE_SCORE, key=lambda key: -BASE_SCORE[key])
    if order.index(other["severity_class"]) < order.index(group["severity_class"]):
        group["severity_class"] = other["severity_class"]
    group["agents"] = sorted(set(group["agents"]) | set(other["agents"]))
    group["tasks"] = sorted(set(group["tasks"]) | set(other["tasks"]))
    group["passing_tasks"] = sorted(
        set(group["passing_tasks"]) | set(other["passing_tasks"]))
    group["comparative"] = group["comparative"] and other["comparative"]
    for key in ("failures", "tokens", "latency_s"):
        values = [value for value in (group[key], other[key]) if value is not None]
        group[key] = max(values) if values else None
    group["occurrences"] += other["occurrences"]
    group["flags"] = sorted(set(group["flags"]) | set(other["flags"]))
    group["details"] += [d for d in other["details"] if d]
    group["steps"] += other["steps"]
    group["fingerprints"] = sorted(set(group["fingerprints"]) |
                                   set(other["fingerprints"]))
    group["fix_hint"] = group["fix_hint"] or other["fix_hint"]
    group["basis_notes"] += [n for n in other["basis_notes"]
                             if n not in group["basis_notes"]]
    if other["confidence_cap"] and not group["confidence_cap"]:
        group["confidence_cap"] = other["confidence_cap"]
    group["sources"] = sorted(set(group.get("sources", [group["source"]])) |
                              {other["source"]})
    group["merged_ids"] = sorted(set(group.get("merged_ids", [group["id"]])) |
                                 {other["id"]})


def _merge(candidates: list[dict]) -> list[dict]:
    """Merge to a fixed point, so a chain A~B~C collapses into one action.

    One pass is not enough: absorbing B into A can widen A's task set until it
    also matches C, and leaving C out would reproduce the near-duplicate rows
    this whole module exists to remove.
    """
    groups: list[dict] = []
    for candidate in candidates:
        candidate.setdefault("sources", [candidate["source"]])
        candidate.setdefault("merged_ids", [candidate["id"]])
        groups.append(candidate)

    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if _mergeable(groups[i], groups[j]):
                    _absorb(groups[i], groups[j])
                    del groups[j]
                    changed = True
                    break
            if changed:
                break
    return groups


# --------------------------------------------------------------------------
# scoring, confidence, impact, effort
# --------------------------------------------------------------------------


def _sample(candidate: dict, n_tasks: int) -> dict:
    """The denominator this finding is judged on: tasks, unless it says
    otherwise (attribute lifts count runs, calibration counts failures)."""
    if candidate.get("sample"):
        return dict(candidate["sample"])
    return {"k": len(candidate["tasks"]), "n": n_tasks, "unit": "task"}


def _evidence_factor(k: int) -> tuple[float, str]:
    """Sample-size multiplier, with the sentence that explains it.

    Returned as a pair so the number and its justification cannot drift apart:
    whatever damping is applied is printed in ``rank_basis`` beside the score.
    """
    if k <= 0:
        return 0.5, "nothing countable behind it, so it is ranked last of its class"
    if k == 1:
        return SINGLE_OCCURRENCE_FACTOR, (
            "damped for a single occurrence, which cannot be told apart from "
            "a one-off; damped rather than dropped, because a one-off crash "
            "is still a crash"
        )
    bonus = RECURRENCE_BONUS * min(k - 2, RECURRENCE_BONUS_CAP)
    factor = round(1.0 + bonus, 4)
    if factor == 1.0:
        return factor, "no damping: it recurs, so it is not an anecdote"
    return factor, (
        f"recurrence bonus of +{bonus:g} for repeating beyond a second "
        f"{'task' if k else 'case'}, capped at {RECURRENCE_BONUS_CAP} extra"
    )


def _reliability_gate(aggregate: dict) -> Optional[dict]:
    """What the reliability block refuses to let anyone claim, if it ran.

    Returns None when there is no reliability data (a single-run batch), and
    otherwise the refusal in the reliability block's own words, so this
    module never paraphrases a statistical limit into something softer.
    """
    block = aggregate.get("reliability") or {}
    per_agent = block.get("per_agent") or {}
    worst = None
    for side in sorted(per_agent):
        advisory = (per_agent[side] or {}).get("runs_advisory") or {}
        if advisory.get("tier") in ("insufficient", "thin"):
            if worst is None or advisory.get("n_min", 0) < worst.get("n_min", 0):
                worst = {
                    "agent": per_agent[side].get("agent"),
                    "tier": advisory.get("tier"),
                    "n_min": advisory.get("n_min"),
                    "message": advisory.get("message"),
                    "does_not_support": advisory.get("does_not_support") or [],
                }
    return worst


def _score(candidate: dict, sample: dict, gate: Optional[dict]) -> tuple:
    """score = base x evidence x reliability, with every factor recorded."""
    severity = candidate["severity_class"]
    base = BASE_SCORE[severity]
    factor, factor_note = _evidence_factor(sample["k"])
    basis = [
        f"base {base:g} — {SEVERITY_REASON[severity]}",
        f"x{factor:g} — {factor_note} "
        f"({sample['k']} of {sample['n']} {sample['unit']}(s))",
    ]
    reliability_factor = 1.0
    if gate and candidate["comparative"]:
        reliability_factor = UNDERPOWERED_FACTOR
        basis.append(
            f"x{UNDERPOWERED_FACTOR:g} — this is a claim about one agent "
            f"against another, and the reliability block refuses it: "
            f"{gate['message']}"
        )
    elif gate:
        basis.append(
            "no reliability damping — this is a property of a single trace, "
            "not a comparison between agents, so the run count does not bear "
            "on it"
        )
    return round(base * factor * reliability_factor, 4), basis


def _confidence(candidate: dict, sample: dict, gate: Optional[dict]) -> dict:
    """How much the evidence supports the action — from counts, not from tone.

    Recurrence across tasks is the only thing that raises it, an explicit cap
    is the only thing that lowers it, and both are named in ``basis``.  There
    is deliberately no path to "high" for a single occurrence.
    """
    k, n, unit = sample["k"], sample["n"], sample["unit"]
    low, high = wilson_interval(max(0, min(k, n)), n) if n else (0.0, 1.0)
    if k >= 3:
        level = "high"
    elif k == 2:
        level = "medium"
    else:
        level = "low"
    reasons = [
        f"{k} of {n} {unit}(s) show it "
        f"(95% Wilson interval on the rate: {low:.0%}–{high:.0%})"
    ]
    if k <= 1:
        reasons.append(
            "a single occurrence cannot distinguish a systematic problem from "
            "a one-off, so this cannot rank as more than low confidence"
        )
    order = {"low": 0, "medium": 1, "high": 2}
    capped_by = None
    if candidate["confidence_cap"] and order[candidate["confidence_cap"]] < order[level]:
        level = candidate["confidence_cap"]
        capped_by = candidate["basis_notes"][0] if candidate["basis_notes"] else \
            "the basis of this signal"
        reasons.append(f"capped at {level}: {capped_by}")
    if gate and candidate["comparative"] and order[level] > 0:
        level = "low"
        capped_by = gate["message"]
        reasons.append(
            "capped at low: the reliability block says this sample cannot "
            "support a comparison between agents — "
            + "; ".join(gate["does_not_support"])
        )
    return {
        "level": level,
        "occurrences": k,
        "of": n,
        "unit": unit,
        "rate_interval": [low, high],
        "interval_method": "Wilson 95%",
        "basis": reasons,
        "capped_by": capped_by,
    }


def _cost_rate(reports: list[dict]) -> tuple[Optional[float], str]:
    """Dollars per token, realised by this corpus — or nothing, with a reason.

    Derived rather than assumed: the corpus paid a known amount for a known
    number of tokens, so a token saving converts at *its* price and no other.
    When either total is zero the conversion is refused; a made-up price list
    would put a number in front of a reader that no run in this batch
    supports.
    """
    tokens = 0
    cost = 0.0
    for report in reports:
        for side in ("a", "b"):
            totals = report[side]["totals"]
            tokens += int(totals.get("input_tokens", 0) or 0)
            tokens += int(totals.get("output_tokens", 0) or 0)
            cost += float(totals.get("cost_usd", 0.0) or 0.0)
    if tokens <= 0 or cost <= 0:
        return None, ("no dollar estimate: this corpus reports no cost, so "
                      "there is no realised price per token to convert with")
    rate = cost / tokens
    return rate, (
        f"converted at this corpus's own realised price, ${cost:.4f} over "
        f"{tokens:,} token(s) = ${rate * 1000:.5f} per 1K tokens"
    )


def _impact(candidate: dict, n_tasks: int, rate: Optional[float],
            rate_note: str) -> dict:
    """What fixing it is worth, with the basis of every figure, or ``None``.

    ``None`` is a real answer here and is used wherever the underlying block
    reports no number.  It is never replaced by 0, which would read as "we
    measured this and it is worth nothing" — the opposite of what is known.
    """
    failures = candidate["failures"]
    tokens = candidate["tokens"]
    latency = candidate["latency_s"]
    # Unknown-and-unknowable is listed before measured-zero, because the two
    # are different answers and the first is the one a reader must be told.
    unknown: list[str] = []
    measured_zero: list[str] = []

    failures_avoided = None
    if failures:
        failures_avoided = {
            "value": failures,
            "of_tasks": n_tasks,
            "basis": (
                f"{failures} run(s) of {n_tasks} task(s) where this was the "
                f"attributed cause of failure; assumes the fix removes the "
                f"cause and changes nothing else"
            ),
        }
    elif failures == 0:
        measured_zero.append(
            "no failures avoided: measured at zero — this finding was never "
            "the attributed cause of a failed run in this batch")
    else:
        unknown.append(
            "failures avoided not estimable: no block reports a failure count "
            "for this finding")

    tokens_saved = None
    if tokens:
        tokens_saved = {
            "value": tokens,
            "basis": (
                f"downstream tokens attributed to this finding across "
                f"{candidate['occurrences']} occurrence(s) on "
                f"{len(candidate['tasks'])} of {n_tasks} task(s)"
            ),
        }
    elif tokens is None:
        unknown.append(
            "tokens saved not estimable: the source block states its cost in "
            "prose and reports no numeric token figure")
    else:
        measured_zero.append("tokens saved: measured at zero")

    latency_saved = None
    if latency:
        latency_saved = {
            "value": round(latency, 4),
            "basis": (
                f"downstream latency attributed to this finding across "
                f"{candidate['occurrences']} occurrence(s)"
            ),
        }
    elif latency is None:
        unknown.append("latency saved not estimable: no numeric figure in "
                       "the source block")

    cost_saved = None
    if tokens_saved and rate is not None:
        cost_saved = {"value": round(tokens_saved["value"] * rate, 6),
                      "basis": rate_note}
    elif tokens_saved:
        unknown.append(rate_note)
    unestimable = unknown + measured_zero

    pieces = []
    if failures_avoided:
        pieces.append(f"up to {failures_avoided['value']} failure(s) of "
                      f"{n_tasks} task(s) avoided")
    if tokens_saved:
        piece = f"−{tokens_saved['value']:,} tokens"
        if cost_saved:
            piece += f" (≈${cost_saved['value']:.4f})"
        pieces.append(piece)
    if latency_saved:
        pieces.append(f"−{latency_saved['value']:g}s latency")
    summary = "; ".join(pieces) if pieces else (
        "Not estimable from this data — "
        + ("; ".join((unknown or measured_zero)[:2]) or "nothing measured."))
    return {
        "estimable": bool(pieces),
        "failures_avoided": failures_avoided,
        "tokens_saved": tokens_saved,
        "latency_saved_s": latency_saved,
        "cost_usd_saved": cost_saved,
        "unestimable": unestimable,
        "summary": summary,
    }


def _effort(candidate: dict) -> dict:
    """A coarse guess at where the fix lives, labelled as a guess."""
    kind, detail = EFFORT.get(candidate["category"],
                              ("investigation", "no effort heuristic for this "
                                                "kind of finding"))
    return {"class": kind, "detail": detail, "heuristic": True,
            "note": EFFORT_NOTE}


def _agent_phrase(agents: list[str]) -> str:
    if not agents:
        return "the failing agent"
    if len(agents) == 1:
        return agents[0]
    return " and ".join(agents)


def _action_text(candidate: dict) -> str:
    """One imperative line: what to change, not what was observed."""
    who = _agent_phrase(candidate["agents"])
    category = candidate["category"]
    flags = set(candidate["flags"])
    if category == "retrieval":
        return (f"Stop {who} answering from the weaker source — require a "
                f"primary or official source before it commits")
    if category == "tool_selection":
        return (f"Make {who} pick the tool that fits the data it actually has, "
                f"before it calls anything")
    if category == "tool_availability":
        return (f"Reconcile {who}'s tool list with what it calls — it is "
                f"invoking tools it was never offered")
    if category == "tool_execution":
        return (f"Validate {who}'s tool arguments against the tool's schema "
                f"before acting on the result")
    if category == "planning":
        return (f"Make {who} write an explicit plan and revise it when "
                f"evidence contradicts it, instead of pushing on")
    if category == "reasoning":
        return (f"Make {who} reconcile conflicting intermediate results before "
                f"it continues")
    if category == "efficiency":
        if flags & {"looped", "loop_block", "repeated_calls",
                    "no_information_steps"}:
            return (f"Break {who} out of its loop — detect a repeated "
                    f"call-and-result and change strategy instead of repeating it")
        if "budget_pressure" in flags:
            return (f"Give {who} room or a stop rule — it is finishing on the "
                    f"edge of its step budget")
        return (f"Make {who} stop once its evidence is sufficient instead of "
                f"gathering more")
    if category == "prompt_cache":
        return (f"Give {who} a stable prompt prefix so the provider cache can "
                f"absorb its re-sent context")
    if category == "result_cache":
        return (f"Cache {who}'s repeated identical tool calls at the harness — "
                f"same call, same result, paid for twice")
    if category == "parallel_reads":
        return (f"Issue {who}'s independent read-only calls concurrently "
                f"instead of one after another")
    if category == "latency_concentration":
        return (f"Profile {who}'s slowest steps — most of its wall-clock sits "
                f"in a few of them")
    if category == "recovery":
        return (f"Stop {who} retrying a failed call unchanged — adapt the call "
                f"or abandon it")
    if category == "safety":
        return (f"Make {who} read before it writes — it is changing state "
                f"without looking first")
    if category == "verification":
        return (f"Make {who} verify the state change before it claims the task "
                f"is done")
    if category == "grounding":
        return (f"Make {who} take argument values from what it has actually "
                f"seen — some appear nowhere in the trace")
    if category == "calibration":
        return (f"Add an independent verification step to {who} rather than a "
                f"confidence threshold — it is wrong while confident")
    if category == "attribute":
        return (f"Investigate runs where {candidate['fix_hint']} — they fail "
                f"measurably more often than runs without")
    if category == "regression":
        return "Account for the metric regression before shipping the candidate"
    if category == "oracle":
        return (f"Check the grader on {', '.join(candidate['tasks'])} — {who} "
                f"failed there with a completely clean process")
    if category == "grader_suspect":
        return (f"Re-grade {', '.join(candidate['tasks'])} by hand before "
                f"changing {who} — the ranked diagnosis puts the grader or "
                f"label first, not the agent")
    if category == "contested_diagnosis":
        return (f"Run the discriminating check on "
                f"{', '.join(candidate['tasks'])} before fixing anything — "
                f"the evidence does not pick a single cause for {who}'s "
                f"failure there")
    if category == "confirmed_cause":
        return (f"Act on the confirmed cause for {who} on "
                f"{', '.join(candidate['tasks'])} — an executed check "
                f"against the corpus already settled it: "
                f"{candidate['fix_hint']}")
    if category == "unstable_diagnosis":
        return (f"Do not fix {who} on {', '.join(candidate['tasks'])} from "
                f"any single run's diagnosis — the per-run diagnoses "
                f"disagree; add runs or run the discriminating checks first")
    return f"Investigate the {category} finding affecting {who}"


def _evidence(candidate: dict, sample: dict, n_tasks: int) -> dict:
    low, high = wilson_interval(max(0, min(sample["k"], sample["n"])),
                                sample["n"]) if sample["n"] else (0.0, 1.0)
    return {
        "tasks": candidate["tasks"],
        "task_count": len(candidate["tasks"]),
        "of_tasks": n_tasks,
        "agents": candidate["agents"],
        "occurrences": candidate["occurrences"],
        "failures_caused": candidate["failures"],
        "occurrence_rate": {
            "k": sample["k"], "n": sample["n"], "unit": sample["unit"],
            "rate": round(sample["k"] / sample["n"], 4) if sample["n"] else None,
            "interval": [low, high], "method": "Wilson 95%",
        },
        "details": [detail for detail in candidate["details"] if detail],
        "steps": candidate["steps"][:12],
        "fingerprints": candidate["fingerprints"],
        "process_flags": candidate["flags"],
        "caveats": candidate["basis_notes"],
    }



def _verification(candidate: dict, sample: dict, n_tasks: int,
                  failures: int) -> dict:
    """How the person applying this fix will know it worked.

    An action without a verification contract is a suggestion; with one it
    is a testable hypothesis.  Two channels, deliberately ranked:

    * **Fingerprints first.**  Issue fingerprints are stable across runs by
      construction, so "this fingerprint stops appearing in the next batch"
      is a binary, deterministic confirmation that needs no statistics.
    * **Metrics second, with their detectability stated.**  On a small
      suite, the success rate often *cannot* confirm a real fix: if the
      hoped-for improvement still lands inside the current Wilson interval,
      a single re-run proves nothing either way.  Saying so up front stops
      the after-run from being misread in both directions — a fix declared
      dead because the rate did not visibly move, or declared working
      because it moved within noise.
    """
    if candidate.get("category") == "confirmed_cause":
        return {
            "how": ("nothing further to check: an executed check against "
                    "the corpus already settled this — "
                    + (candidate.get("fix_hint") or "see the finding")),
            "checks": [],
            "caveat": ("the confirmation is about this corpus's runs; a "
                       "changed grader, environment, or harness resets it"),
        }
    if candidate.get("category") == "unstable_diagnosis":
        return {
            "how": ("add runs of the same tasks, then re-consolidate: the "
                    "diagnosis is unstable, so the check IS more data"),
            "checks": [],
            "caveat": ("re-running once cannot settle an unstable "
                       "diagnosis; only a larger sample can"),
        }
    if candidate.get("category") in ("grader_suspect", "contested_diagnosis"):
        # These actions ask for a human check, not a code change; a re-run
        # and fingerprint diff would measure nothing about them.
        how = (
            "a human verdict settles this without a re-run: "
            + (candidate.get("fix_hint")
               or "run the discriminating check named in the finding")
        )
        return {
            "how": how,
            "checks": [],
            "caveat": (
                "if the human check vindicates the agent, fix the grader or "
                "label and re-score — do not spend engineering time on the "
                "agent first"
            ),
        }
    fingerprints = candidate["fingerprints"]
    checks = []
    if fingerprints:
        checks.append({
            "kind": "fingerprint",
            "expect": "these fingerprints stop appearing in the next batch",
            "fingerprints": fingerprints,
            "confirms": "binary and deterministic; the strongest signal at any suite size",
        })
    if candidate["flags"]:
        checks.append({
            "kind": "process_flag",
            "expect": "these process flags stop being raised on the affected tasks",
            "flags": sorted(set(candidate["flags"])),
            "tasks": candidate["tasks"],
        })

    metric_check = None
    if failures and n_tasks:
        # Merged findings can count more failures than there are tasks (one
        # task can fail for several counted reasons); the rate arithmetic
        # needs the task-level view, so clamp rather than let a negative
        # "current" reach the interval math.
        current = max(0, n_tasks - failures)
        hoped = min(n_tasks, current + failures)
        low, high = wilson_interval(current, n_tasks)
        # The right null: how often would the UNCHANGED agent score this
        # well by luck?  Wilson bounds describe estimation uncertainty, not
        # next-run sampling noise — a 3/4 agent hits 4/4 32% of the time.
        luck = binomial_tail(hoped, n_tasks, current / n_tasks)
        confirmable = luck < 0.05
        metric_check = {
            "kind": "success_rate",
            "current": f"{current}/{n_tasks}",
            "hoped": f"{hoped}/{n_tasks}",
            "current_interval": [low, high],
            "chance_of_hoped_result_without_a_fix": round(luck, 4),
            "single_rerun_can_confirm": confirmable,
            "note": (
                f"an unchanged agent reaches {hoped}/{n_tasks} only "
                f"{luck:.1%} of the time, so one re-run landing there is "
                "meaningful evidence"
                if confirmable else
                f"an unchanged agent reaches {hoped}/{n_tasks} "
                f"{luck:.0%} of the time by luck alone — one re-run cannot "
                "confirm this by success rate; rely on the fingerprint "
                "check, or add runs"),
        }
    if metric_check:
        checks.append(metric_check)

    return {
        "how": ("re-run the same tasks, then `agentdiff progress "
                "<before-out> <after-out>` — it matches these checks by "
                "fingerprint and reports resolved / persists / new"),
        "checks": checks,
        "caveat": ("absence of a fingerprint in the after-run confirms the "
                   "fix only if the same tasks ran; progress reports task-set "
                   "drift rather than counting a missing task as a cure"),
    }

# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def _empty(reason: str) -> dict:
    return {
        "tasks": 0,
        "actions": [],
        "narrative": reason,
        "not_actionable": [],
        "scheme": _scheme(None),
        "counts": {"actions": 0, "excluded": 0, "sources": {}},
        "reliability_gate": None,
    }


def _scheme(gate: Optional[dict]) -> dict:
    """The ranking rules, shipped with the ranking.

    A ranked list whose rule lives only in the source code is a ranked list
    nobody can argue with, and one nobody argues with is one nobody trusts.
    """
    return {
        "formula": "score = base(severity class) x evidence(sample size) x "
                   "reliability(gate)",
        "base": dict(BASE_SCORE),
        "base_reasons": dict(SEVERITY_REASON),
        "evidence": {
            "single_occurrence_factor": SINGLE_OCCURRENCE_FACTOR,
            "recurrence_bonus_per_extra_task": RECURRENCE_BONUS,
            "recurrence_bonus_cap": RECURRENCE_BONUS_CAP,
            "why": (
                "One occurrence is an anecdote, so it is damped rather than "
                "dropped; recurrence across tasks is the strongest evidence "
                "available in a batch this size, and its bonus is capped so a "
                "cheap-but-frequent finding cannot outrank a failure."
            ),
        },
        "reliability": {
            "factor_when_underpowered": UNDERPOWERED_FACTOR,
            "applies_to": "claims that compare one agent with another",
            "exempt": (
                "process pathologies, which are properties of a single trace "
                "and need no second agent to be real"
            ),
            "active": bool(gate),
            "refusal": gate,
        },
        "tie_breaks": ["failures caused", "wasted tokens", "category", "id"],
        "merge_rule": (
            "Findings merge when they share a category and name the same agent; "
            "a finding that names no agent must also overlap on a task to prove "
            "which action it belongs to. Merged counts are combined with max, "
            "never summed, because the sources describe the same runs from "
            "different angles."
        ),
    }


def triage(reports: list[dict], aggregate: dict) -> dict:
    """Rank every finding in a batch into one ordered list of actions.

    ``reports`` supplies the per-run process blocks, ``aggregate`` everything
    already rolled up.  Nothing is recomputed from raw traces: if two blocks
    disagree about a number, that disagreement belongs in those blocks, not
    in a third opinion invented here.

    Returns ``actions`` (ranked, deduplicated, each with evidence, impact,
    confidence, effort and source), a ``narrative`` for someone who asks "so
    what?", a ``not_actionable`` list that accounts for every finding
    deliberately left out, and the ``scheme`` the ranking used.
    """
    if not reports:
        return _empty("Nothing to triage: no comparison reports.")

    n_tasks = len(reports)
    aggregate = aggregate or {}
    gate = _reliability_gate(aggregate)
    excluded: list[dict] = []

    attribute_actions, attribute_excluded = _from_attributes(aggregate)
    calibration_actions, calibration_excluded = _from_calibration(aggregate)
    excluded += attribute_excluded + calibration_excluded

    candidates = (
        _from_issues(aggregate)
        + _from_recommendations(aggregate)
        + _from_process(reports)
        + _from_oracle(reports)
        + _from_diagnosis(reports)
        + _from_consolidated(aggregate)
        + _from_efficiency(aggregate)
        + attribute_actions
        + calibration_actions
        + _from_regressions(aggregate)
    )

    # An executed check supersedes the recommendation to run one: where a
    # consolidated verdict is confirmed for (agent, task), the per-pair
    # "re-grade by hand" / "run the discriminating check" rows for the same
    # ground are dropped — loudly, so the supersession stays visible.
    confirmed_ground = {
        (tuple(c["agents"]), task)
        for c in candidates if c["category"] == "confirmed_cause"
        for task in c["tasks"]
    }
    if confirmed_ground:
        kept = []
        for candidate in candidates:
            if candidate["category"] in ("grader_suspect",
                                         "contested_diagnosis") and all(
                    (tuple(candidate["agents"]), task) in confirmed_ground
                    for task in candidate["tasks"]):
                excluded.append({
                    "finding": candidate["details"][0]
                    if candidate["details"] else candidate["id"],
                    "source": "diagnosis",
                    "reason": "superseded by executed check",
                    "detail": (
                        "an executed check against the corpus already "
                        "settled what this action asked a human to check"
                    ),
                })
                continue
            kept.append(candidate)
        candidates = kept

    for issue in (aggregate.get("issues") or {}).get("issues", []):
        if issue.get("suppressed"):
            excluded.append({
                "finding": issue.get("title", issue["id"]),
                "source": "issues",
                "reason": "suppressed",
                "detail": (
                    f"fingerprint `{issue['id']}` is listed in "
                    f".agentdiffignore; a team has judged it benign."
                ),
            })

    merged = _merge(candidates)

    ranked: list[dict] = []
    for candidate in merged:
        sample = _sample(candidate, n_tasks)
        # A finding with nothing measurable behind it is noise at the top of a
        # list whose whole purpose is to be short.  It is excluded loudly.
        nothing_measured = (
            not candidate["failures"] and not candidate["tokens"]
            and not candidate["latency_s"] and not candidate["flags"]
            and candidate["category"] not in ("attribute", "calibration",
                                              "regression", "oracle",
                                              "grader_suspect",
                                              "contested_diagnosis",
                                              "confirmed_cause",
                                              "unstable_diagnosis")
        )
        if nothing_measured:
            excluded.append({
                "finding": candidate["details"][0] if candidate["details"]
                else candidate["id"],
                "source": " + ".join(candidate["sources"]),
                "reason": "no measurable cost",
                "detail": (
                    f"seen on {len(candidate['tasks'])} of {n_tasks} task(s) "
                    f"but caused no failure and cost 0 extra tokens and 0s "
                    f"latency; there is nothing to gain by fixing it."
                ),
            })
            continue
        score, basis = _score(candidate, sample, gate)
        ranked.append({
            "candidate": candidate,
            "sample": sample,
            "score": score,
            "rank_basis": basis,
        })

    ranked.sort(key=lambda row: (
        -row["score"],
        -(row["candidate"]["failures"] or 0),
        -(row["candidate"]["tokens"] or 0),
        row["candidate"]["category"],
        ",".join(row["candidate"]["agents"]),
        row["candidate"]["id"],
    ))

    rate, rate_note = _cost_rate(reports)
    actions: list[dict] = []
    for position, row in enumerate(ranked, start=1):
        candidate, sample = row["candidate"], row["sample"]
        actions.append({
            "rank": position,
            "action": _action_text(candidate),
            "category": candidate["category"],
            "agents": candidate["agents"],
            "source": " + ".join(candidate["sources"]),
            "sources": candidate["sources"],
            "finding_ids": candidate["merged_ids"],
            "merged_from": len(candidate["merged_ids"]),
            "severity_class": candidate["severity_class"],
            "on_passing_runs": candidate["passing_tasks"],
            "evidence": _evidence(candidate, sample, n_tasks),
            "impact": _impact(candidate, n_tasks, rate, rate_note),
            "confidence": _confidence(candidate, sample, gate),
            "effort": _effort(candidate),
            "score": row["score"],
            "rank_basis": row["rank_basis"],
            "fix_hint": candidate["fix_hint"],
            "verification": _verification(candidate, sample, n_tasks,
                                          candidate["failures"]),
        })

    excluded.sort(key=lambda entry: (entry["reason"], entry["source"],
                                     entry["finding"]))
    source_counts: dict[str, int] = {}
    for action in actions:
        for source in action["sources"]:
            source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "tasks": n_tasks,
        "agents": aggregate.get("agents") or {},
        "actions": actions,
        "narrative": _narrative(actions, excluded, n_tasks, gate),
        "not_actionable": excluded,
        "scheme": _scheme(gate),
        "counts": {
            "actions": len(actions),
            "excluded": len(excluded),
            "merged": sum(action["merged_from"] - 1 for action in actions),
            "sources": dict(sorted(source_counts.items())),
        },
        "reliability_gate": gate,
    }


def _narrative(actions: list[dict], excluded: list[dict], n_tasks: int,
               gate: Optional[dict]) -> str:
    """Two or three sentences for someone who asks "so what?"."""
    if not actions:
        return (
            f"Nothing to act on across {n_tasks} task(s): every finding was "
            f"either without measurable cost or excluded "
            f"({len(excluded)} listed as not actionable)."
        )
    top = actions[0]
    merged = sum(action["merged_from"] - 1 for action in actions)
    text = (
        f"{len(actions)} action(s) across {n_tasks} task(s)"
        + (f", after merging {merged} duplicate finding(s) that arrived from "
           f"more than one analysis" if merged else "")
        + f"; {len(excluded)} finding(s) are listed as not actionable with a "
          f"reason. Start with: {top['action']} — {top['impact']['summary']} "
          f"({top['confidence']['level']} confidence, "
          f"{top['evidence']['task_count']} of {n_tasks} task(s))."
    )
    passing = [action for action in actions if action["on_passing_runs"]]
    if passing:
        text += (
            f" {len(passing)} of these sit on run(s) that passed "
            f"({', '.join(sorted({t for a in passing for t in a['on_passing_runs']}))})"
            f", where the outcome hides the problem and no other block in this "
            f"report will raise it."
        )
    if gate:
        text += (
            f" Every cross-agent claim here is damped and capped at low "
            f"confidence: {gate['message']}"
        )
    return text


def render_triage_text(result: dict, limit: int = 5) -> list[str]:
    """Terminal rendering of the ranked list — the short version, in order.

    Kept beside the analysis so the printed summary and the JSON cannot drift
    apart, and capped by default because a triage list that does not fit on a
    screen has given up on triaging.
    """
    lines = ["", "Triage — what to fix first:"]
    if not result.get("actions"):
        lines.append(f"  {result.get('narrative', 'nothing to triage.')}")
        return lines
    for action in result["actions"][:limit]:
        confidence = action["confidence"]
        evidence = action["evidence"]
        lines.append(
            f"  {action['rank']}. [{action['severity_class'].replace('_', ' ')}"
            f" · {confidence['level']} confidence] {action['action']}"
        )
        tasks = ", ".join(evidence["tasks"][:4]) or "—"
        if len(evidence["tasks"]) > 4:
            tasks += f", +{len(evidence['tasks']) - 4} more"
        lines.append(
            f"     evidence: {evidence['task_count']}/{evidence['of_tasks']} "
            f"task(s) ({tasks}); {evidence['occurrences']} occurrence(s)"
            + (f"; {evidence['failures_caused']} failure(s) caused"
               if evidence["failures_caused"] else "")
        )
        if action["on_passing_runs"]:
            lines.append(
                f"     note: seen on run(s) that PASSED "
                f"({', '.join(action['on_passing_runs'])}) — no other block "
                f"flags this"
            )
        lines.append(f"     impact: {action['impact']['summary']}")
        lines.append(
            f"     effort: {action['effort']['class']} "
            f"({action['effort']['detail']}; heuristic)"
            f"  |  source: {action['source']}"
        )
        lines.append(f"     why here: {action['rank_basis'][1]}")
    remaining = len(result["actions"]) - limit
    if remaining > 0:
        lines.append(f"  (+{remaining} more action(s) in aggregate.json "
                     f"under \"triage\")")
    if result.get("not_actionable"):
        lines.append(f"  Not actionable ({len(result['not_actionable'])}):")
        for entry in result["not_actionable"][:4]:
            lines.append(f"    - [{entry['reason']}] {entry['finding']}")
        if len(result["not_actionable"]) > 4:
            lines.append(f"    - (+{len(result['not_actionable']) - 4} more, "
                         f"each with its reason, in aggregate.json)")
    lines.append(f"  {result['narrative']}")
    return lines
