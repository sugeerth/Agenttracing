"""Did the fix work?  Two batch runs compared, action by action (v26).

The triage list ends with a verification contract: fingerprints that should
stop appearing, flags that should stop being raised, a success rate that may
or may not be able to confirm anything.  This module is the other half of
that contract — point it at the batch output from before the fix and the
batch output from after, and it answers per action, in the action's own
terms.

The honesty problems in a before/after comparison are all about absence:

* **A missing fingerprint is only a cure if the same exam was sat.**  If the
  after-run dropped the task where the issue lived, the fingerprint's
  absence proves nothing.  Task-set drift is reported first, and a
  fingerprint whose home task did not re-run is ``unobservable``, not
  ``resolved``.
* **Fewer occurrences is progress, not resolution.**  An issue seen on
  three tasks before and one task after has improved and persists; calling
  it fixed because the count fell would be the same mistake as calling a
  flaky test cured because it passed once.
* **New fingerprints are part of the answer.**  A fix that resolves one
  issue and introduces two is a regression wearing a win — the report
  ends with what appeared, not only what disappeared.

Everything is matched on the stable identifiers the engine already emits
(issue fingerprints, task ids, agent names); nothing is fuzzy-matched, and
where identity cannot be established the comparison says unmatchable rather
than guessing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .statistics import binomial_tail, wilson_interval


def _load_batch(directory: Path) -> Optional[dict]:
    """A batch output directory: aggregate.json plus its per-task reports."""
    directory = Path(directory)
    aggregate_path = directory / "aggregate.json"
    if not aggregate_path.is_file():
        return None
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    reports = []
    for path in sorted(directory.glob("report_*.json")):
        try:
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return {"aggregate": aggregate, "reports": reports, "path": str(directory)}


def _issue_index(aggregate: dict) -> dict:
    """fingerprint -> issue summary, from an aggregate's issues block."""
    index = {}
    for issue in ((aggregate.get("issues") or {}).get("issues") or []):
        fingerprint = issue.get("fingerprint") or issue.get("id")
        if fingerprint:
            index[str(fingerprint)] = {
                "title": issue.get("title"),
                "occurrences": (issue.get("occurrence_count")
                                if isinstance(issue.get("occurrence_count"), int)
                                else len(issue.get("occurrences") or [])),
                "tasks": issue.get("tasks") or [],
                "failures_caused": issue.get("failures_caused") or 0,
            }
    return index


def _tasks_of(batch: dict) -> set:
    return {(r.get("task") or {}).get("id") for r in batch["reports"]
            if (r.get("task") or {}).get("id")}


def _outcomes(batch: dict) -> dict:
    """(task, agent_name) -> success, from the per-task reports."""
    outcomes = {}
    for report in batch["reports"]:
        task = (report.get("task") or {}).get("id")
        for side in ("a", "b"):
            block = report.get(side) or {}
            name = ((block.get("agent") or {}).get("name"))
            if task and name:
                outcomes[(task, name)] = bool(
                    (block.get("outcome") or {}).get("success"))
    return outcomes


def _flag_index(batch: dict) -> dict:
    """(task, agent_name) -> set of raised process flags, from the reports.

    Process-flag actions carry no issue fingerprint, but the flags
    themselves are recomputed deterministically on every batch — so "is the
    flag still raised for the same task and agent" is just as binary a
    check as a fingerprint, and leaving those actions untrackable would
    orphan exactly the passing-run pathologies the triage ranks highest.
    """
    index = {}
    for report in batch["reports"]:
        task = (report.get("task") or {}).get("id")
        process = report.get("process") or {}
        for side in ("a", "b"):
            block = process.get(side) or {}
            name = block.get("agent") or ((report.get(side) or {})
                                          .get("agent") or {}).get("name")
            raised = ((block.get("gap") or {}).get("raised")) or []
            if task and name:
                index[(task, name)] = set(raised)
    return index


def _flag_status(action: dict, after_flags: dict, shared_tasks: set) -> dict:
    evidence = action.get("evidence") or {}
    flags = set(evidence.get("process_flags") or [])
    tasks = set(evidence.get("tasks") or [])
    agents = [a for a in (action.get("agents") or []) if a]
    observable = tasks & shared_tasks
    if not observable:
        return {"status": "unobservable",
                "reason": "the task(s) where these flags were raised did not "
                          "re-run; absence proves nothing"}
    still = sorted({
        flag for task in sorted(observable) for agent in agents
        for flag in (after_flags.get((task, agent), set()) & flags)
    })
    if not still:
        return {"status": "resolved",
                "flags_cleared": sorted(flags),
                "reason": "the process flags behind this action are no "
                          "longer raised for these tasks and agents"}
    cleared = sorted(flags - set(still))
    return {"status": "improved" if cleared else "persists",
            "flags_persisting": still,
            "flags_cleared": cleared,
            "reason": ("some flags cleared but others are still raised"
                       if cleared else
                       "the same process flags are still raised on the "
                       "re-run tasks")}


def _action_status(action: dict, before_issues: dict, after_issues: dict,
                   shared_tasks: set, after_flags: dict) -> dict:
    fingerprints = ((action.get("evidence") or {}).get("fingerprints")) or []
    evidence_tasks = set((action.get("evidence") or {}).get("tasks") or [])

    if not fingerprints:
        if (action.get("evidence") or {}).get("process_flags"):
            return _flag_status(action, after_flags, shared_tasks)
        return {"status": "untrackable",
                "reason": "this action carries neither fingerprints nor "
                          "process flags, so presence or absence cannot be "
                          "established across runs"}
    unobservable = evidence_tasks - shared_tasks
    if evidence_tasks and evidence_tasks <= unobservable:
        return {"status": "unobservable",
                "reason": f"the task(s) where this was seen "
                          f"({', '.join(sorted(unobservable))}) did not run "
                          "in the after-batch; absence proves nothing"}

    still = [fp for fp in fingerprints if fp in after_issues]
    gone = [fp for fp in fingerprints if fp not in after_issues]
    if not still:
        return {"status": "resolved",
                "fingerprints_cleared": gone,
                "reason": "no fingerprint from this action appears in the "
                          "after-batch, and its tasks re-ran"}
    before_occurrences = sum(before_issues.get(fp, {}).get("occurrences", 0)
                             for fp in fingerprints)
    after_occurrences = sum(after_issues.get(fp, {}).get("occurrences", 0)
                            for fp in still)
    if after_occurrences < before_occurrences:
        return {"status": "improved",
                "fingerprints_cleared": gone,
                "fingerprints_persisting": still,
                "occurrences": {"before": before_occurrences,
                                "after": after_occurrences},
                "reason": "fewer occurrences, but the issue is still being "
                          "produced — improved is not resolved"}
    if after_occurrences > before_occurrences:
        return {"status": "worsened",
                "fingerprints_persisting": still,
                "occurrences": {"before": before_occurrences,
                                "after": after_occurrences}}
    return {"status": "persists",
            "fingerprints_persisting": still,
            "occurrences": {"before": before_occurrences,
                            "after": after_occurrences}}


def _efficiency_shift(before_agg: dict, after_agg: dict) -> dict:
    """Did the serving-cost picture move between the runs?

    These are recomputed per batch from each batch's own traces, so the
    before/after comparison is direct.  The labels matter: cost per success
    is realised spend over graded outcomes, while resend overhead is an
    estimate whose basis the efficiency block already declares — a fall in
    an estimated figure is evidence of a structural change (shorter
    context, fewer steps), not a measured saving banked.
    """
    before_agents = {b.get("agent"): b for b in
                     ((before_agg.get("efficiency") or {}).get("per_agent")
                      or {}).values()}
    after_agents = {b.get("agent"): b for b in
                    ((after_agg.get("efficiency") or {}).get("per_agent")
                     or {}).values()}
    shared = sorted(set(before_agents) & set(after_agents) - {None})
    if not shared:
        return {"available": False,
                "reason": "no agent appears in both runs' efficiency blocks"}

    per_agent = {}
    for name in shared:
        b, a = before_agents[name], after_agents[name]
        entry = {}
        cps_b = (b.get("cost_per_success") or {}).get("value_usd")
        cps_a = (a.get("cost_per_success") or {}).get("value_usd")
        if cps_b is not None and cps_a is not None:
            entry["cost_per_success_usd"] = {
                "before": cps_b, "after": cps_a,
                "delta": round(cps_a - cps_b, 6),
                "basis": "realised cost over graded successes, both runs",
            }
        for key, label in (("resend_overhead_tokens",
                            "estimated — a fall means structural change, "
                            "not a banked saving"),
                           ("cacheable_repeat_calls", "counted")):
            if key in b or key in a:
                entry[key] = {"before": b.get(key), "after": a.get(key),
                              "basis": label}
        per_agent[name] = entry
    return {"available": True, "per_agent": per_agent}


def _diagnosis_leads(batch: dict) -> tuple:
    """task -> {"agent", "kind"} for the batch's single_failure diagnoses.

    ``kind`` is the leading hypothesis's kind — ``kind:flag`` when the
    hypothesis carries a flag — and ``None`` when the diagnosis was
    contested (no hypothesis cleared the lead margin).  The second element
    reports whether any report in the batch carries a ``diagnosis`` key at
    all: old outputs predate diagnosis, and their silence must not be read
    as "nothing was diagnosed".
    """
    leads: dict = {}
    any_diagnosis = False
    for report in batch["reports"]:
        if "diagnosis" not in report:
            continue
        any_diagnosis = True
        diag = report.get("diagnosis") or {}
        if diag.get("mode") != "single_failure":
            continue
        task = (report.get("task") or {}).get("id")
        if not task:
            continue
        kind = None
        if diag.get("leading") is not None:
            lead = next((h for h in (diag.get("hypotheses") or [])
                         if h.get("id") == diag.get("leading")), None)
            if lead is not None:
                kind = lead.get("kind")
                if lead.get("flag"):
                    kind = f"{kind}:{lead['flag']}"
        leads[task] = {"agent": diag.get("subject_name"), "kind": kind}
    return leads, any_diagnosis


def _diagnosis_shift(before: dict, after: dict) -> dict:
    """Did the fix move the diagnosis?  Leading cause per task, before vs
    after.

    Informational only — nothing here feeds ``regressions_in``.  For every
    task diagnosed as a single failure in *both* batches, the leading
    hypothesis kinds are compared:

    * same kind leads both runs — the fix did not move the diagnosis;
    * a different kind leads after — the key insight of a fix loop: the
      cause you fixed no longer leads and a different one does, which is
      progress, but not done;
    * a contested diagnosis on either side is stated plainly rather than
      pretending a cause led.

    A failure that resolved outright (diagnosed before, no failure to
    diagnose after) is deliberately skipped: issue tracking already reports
    resolution, and saying it twice would inflate the win.  Batches whose
    reports carry no ``diagnosis`` key at all (old outputs) yield an empty
    section with the reason stated, never a crash.
    """
    before_leads, before_any = _diagnosis_leads(before)
    after_leads, after_any = _diagnosis_leads(after)
    if not before_any or not after_any:
        return {"tasks": [],
                "note": "no diagnosis objects in one or both batches"}

    tasks = []
    for task in sorted(set(before_leads) & set(after_leads)):
        b, a = before_leads[task], after_leads[task]
        bk, ak = b["kind"], a["kind"]
        if bk is None and ak is None:
            verdict = ("contested in both runs — no single cause led "
                       "either time")
        elif bk is None:
            verdict = f"was contested, now {ak} leads"
        elif ak is None:
            verdict = f"{bk} led, now contested"
        elif bk == ak:
            verdict = "cause unchanged"
        else:
            verdict = f"cause shifted: {bk} → {ak}"
        entry = {"task": task, "before": bk, "after": ak, "verdict": verdict}
        agent = b.get("agent") or a.get("agent")
        if agent:
            entry["agent"] = agent
        tasks.append(entry)

    note = None
    if tasks:
        shifted = sum(1 for t in tasks
                      if t["verdict"].startswith("cause shifted"))
        unchanged = sum(1 for t in tasks if t["verdict"] == "cause unchanged")
        contested = len(tasks) - shifted - unchanged
        bits = []
        if unchanged:
            bits.append(f"{unchanged} lead(s) unchanged — the fix did not "
                        "move those diagnoses")
        if shifted:
            bits.append(f"{shifted} shifted to a different leading cause — "
                        "progress, but not done")
        if contested:
            bits.append(f"{contested} involve a contested diagnosis")
        note = (f"Of {len(tasks)} task(s) diagnosed as single failures in "
                f"both runs: " + "; ".join(bits) + ".")
    return {"tasks": tasks, "note": note}


def compare_progress(before_dir, after_dir) -> dict:
    """The before batch against the after batch, in the triage's own terms."""
    before = _load_batch(before_dir)
    after = _load_batch(after_dir)
    if before is None or after is None:
        missing = before_dir if before is None else after_dir
        return {"error": f"{missing} is not a batch output directory "
                         "(no aggregate.json)"}

    tasks_before, tasks_after = _tasks_of(before), _tasks_of(after)
    shared = tasks_before & tasks_after
    drift = {
        "shared": sorted(shared),
        "dropped": sorted(tasks_before - tasks_after),
        "added": sorted(tasks_after - tasks_before),
    }

    before_issues = _issue_index(before["aggregate"])
    after_issues = _issue_index(after["aggregate"])

    after_flags = _flag_index(after)
    actions = []
    for action in ((before["aggregate"].get("triage") or {}).get("actions") or []):
        status = _action_status(action, before_issues, after_issues,
                                shared, after_flags)
        actions.append({
            "rank_before": action.get("rank"),
            "action": action.get("action"),
            "severity_class": action.get("severity_class"),
            **status,
        })

    new_fingerprints = [
        {"fingerprint": fp, **summary}
        for fp, summary in sorted(after_issues.items())
        if fp not in before_issues
        and (not summary["tasks"] or set(summary["tasks"]) & shared)
    ]

    outcomes_before, outcomes_after = _outcomes(before), _outcomes(after)
    agents = sorted({name for (_, name) in outcomes_before} &
                    {name for (_, name) in outcomes_after})
    success = {}
    for name in agents:
        keys = [(task, name) for task in sorted(shared)
                if (task, name) in outcomes_before and (task, name) in outcomes_after]
        before_wins = sum(1 for key in keys if outcomes_before[key])
        after_wins = sum(1 for key in keys if outcomes_after[key])
        n = len(keys)
        luck = binomial_tail(after_wins, n, before_wins / n) if n else 1.0
        improved = after_wins > before_wins
        confirmable = improved and luck < 0.05
        success[name] = {
            "tasks_compared": n,
            "before": f"{before_wins}/{n}",
            "after": f"{after_wins}/{n}",
            "flips_fixed": [task for (task, _) in
                            [key for key in keys
                             if not outcomes_before[key] and outcomes_after[key]]],
            "flips_broken": [task for (task, _) in
                             [key for key in keys
                              if outcomes_before[key] and not outcomes_after[key]]],
            "chance_without_a_fix": round(luck, 4) if improved else None,
            "confirmable_by_rate": confirmable,
            "note": (None if confirmable else
                     (f"an unchanged agent scores {after_wins}/{n} "
                      f"{luck:.0%} of the time by luck alone; per-task flips "
                      "and fingerprints are the meaningful evidence at this "
                      "suite size" if improved else
                      "the rate did not improve; per-task flips and "
                      "fingerprints carry the evidence")),
        }

    counts = {}
    for entry in actions:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    return {
        "before": before["path"],
        "after": after["path"],
        "task_drift": drift,
        "efficiency_shift": _efficiency_shift(before["aggregate"],
                                              after["aggregate"]),
        "diagnosis_shift": _diagnosis_shift(before, after),
        "actions": actions,
        "action_counts": counts,
        "new_issues": new_fingerprints,
        "success_by_agent": success,
        "narrative": _narrative(counts, new_fingerprints, drift, success),
    }


def regressions_in(result: dict) -> list[str]:
    """What got worse between the runs — the gate-worthy subset.

    Deliberately narrow: an action persisting is the fix not landing, which
    is disappointing but not a regression; a task that flipped from passing
    to failing, an action that *worsened*, or an issue that did not exist
    before is the after-run being worse than the before-run, and that is
    what a CI gate on a fix loop should refuse to ship.
    """
    findings = []
    for entry in result.get("actions") or []:
        if entry.get("status") == "worsened":
            occ = entry.get("occurrences") or {}
            findings.append(
                f"worsened: {entry.get('action')} "
                f"({occ.get('before')} -> {occ.get('after')} occurrence(s))")
    for issue in result.get("new_issues") or []:
        findings.append(f"new issue: {issue.get('title')} "
                        f"({issue.get('occurrences')} occurrence(s))")
    for name, s_ in (result.get("success_by_agent") or {}).items():
        for task in s_.get("flips_broken") or []:
            findings.append(f"broke: {name} on {task} — passed before, fails now")
    return findings


def _narrative(counts, new_issues, drift, success) -> str:
    parts = []
    resolved = counts.get("resolved", 0)
    persists = counts.get("persists", 0) + counts.get("worsened", 0)
    improved = counts.get("improved", 0)
    total = sum(counts.values())
    if total:
        parts.append(f"Of {total} action(s) from the before-run: "
                     f"{resolved} resolved, {improved} improved, "
                     f"{persists} persist or worsened"
                     + (f", {counts['unobservable']} unobservable because "
                        "their tasks did not re-run"
                        if counts.get("unobservable") else "") + ".")
    if new_issues:
        parts.append(f"{len(new_issues)} new issue(s) appeared that the "
                     "before-run did not have — a fix that introduces "
                     "problems is part of the result, not a footnote.")
    if drift["dropped"] or drift["added"]:
        parts.append(f"Task set drifted (dropped {len(drift['dropped'])}, "
                     f"added {len(drift['added'])}); every judgement above "
                     "is restricted to the shared tasks.")
    flips = sum(len(s["flips_fixed"]) + len(s["flips_broken"])
                for s in success.values())
    if not flips and total and not resolved:
        parts.append("No task flipped outcome in either direction.")
    for name, s in success.items():
        if s["flips_fixed"] or s["flips_broken"]:
            bits = []
            if s["flips_fixed"]:
                bits.append(f"fixed {', '.join(s['flips_fixed'])}")
            if s["flips_broken"]:
                bits.append(f"BROKE {', '.join(s['flips_broken'])}")
            parts.append(f"{name}: {'; '.join(bits)}.")
    return " ".join(parts) if parts else "Nothing to compare."
