"""Adjudicated diagnosis: competing hypotheses over every signal in a report.

``attribution`` tells one story — the first structural divergence, walked to
the answer.  That story is often wrong in an identifiable way: the same
report can simultaneously say the failed agent's answer *matched the expected
answer* (a grader problem, not an agent problem), that its process was clean,
and that the winner passed while writing blind and swallowing an error.
Nothing adjudicates between those readings.

This module does.  ``diagnose(report, a, b)`` generates a candidate
hypothesis from every diagnostic signal already computed elsewhere in the
report — the causal divergence, each raised process flag, tool errors,
harness terminations, budget pressure, wrong-fact claims with provenance,
and the grader itself — scores each against an explicit evidence ledger,
and returns the ranked field with the disagreements stated, not smoothed
over.

House rules, applied to explanation itself:

- Every piece of evidence is machine-checkable.  ``span`` evidence quotes a
  substring of a specific step field of a specific trajectory; ``metric``
  evidence names a path into the report and the value found there.
  ``check_diagnosis`` verifies both, the same way ``check_narration``
  verifies narration numbers.
- A hypothesis is only ``leading`` when it clears the runner-up by a margin.
  Otherwise the diagnosis is ``contested`` and says so — two plausible
  causes with a thin margin are not one confident cause.
- Every hypothesis carries a ``discriminator``: the concrete check that
  would confirm or refute it.  A diagnosis that cannot say what evidence
  would change it is a story, not a diagnosis.
- Nothing here re-measures anything.  All inputs are facts other modules
  computed, with their bases; scores are fixed weights over the presence
  and strength of those facts, so the ranking is deterministic and
  auditable.
"""

from __future__ import annotations

from typing import Optional

from .trace import HARNESS_TERMINATIONS, Trajectory

#: hypothesis kinds, in the order generators run (ranking is by score, not
#: this order; order only breaks exact ties deterministically).
KINDS = (
    "grader_or_label",
    "harness_termination",
    "environment_error",
    "wrong_fact_propagation",
    "divergence",
    "process_pathology",
    "budget_pressure",
)

#: a hypothesis leads only if it beats the runner-up by at least this much.
LEAD_MARGIN = 0.15
#: below this score a supported hypothesis is merely "weak".
PLAUSIBLE_FLOOR = 0.2
#: with contradicting evidence and a score under this, it is ruled out.
RULED_OUT_CEILING = 0.1
#: the answer-coverage "match" verdict counts as grader-suspect evidence
#: only at or above this coverage — a 70%-covered answer can still flatly
#: contradict the expected one, and the missing words are the contradiction.
GRADER_COVERAGE_FLOOR = 0.85

_FLAG_STATEMENTS = {
    "blind_write": "changed external state before reading anything",
    "swallowed_error": "hit a tool error and moved on without recovering",
    "looped": "repeated the same block of calls",
    "loop_block": "cycled through a repeated block of calls",
    "repeated_calls": "repeated an identical call and result",
    "no_information_steps": "took steps that added no new information",
    "invented_arguments": "used argument values with no source in the trace",
    "undeclared_tools": "called tools that were never declared to it",
    "schema_violation": "called a tool with arguments that violate its schema",
    "false_success": "claimed completion without any external write",
    "budget_pressure": "ran close to its step budget",
}

_DISCRIMINATORS = {
    "grader_or_label": (
        "re-grade this run by hand: the emitted answer is quoted in the "
        "evidence — if a person reads it as correct, the label is wrong, "
        "not the agent"
    ),
    "harness_termination": (
        "re-run the task once on the same harness: a harness kill is not a "
        "property of the agent and should not repeat deterministically"
    ),
    "environment_error": (
        "replay the failing tool call in isolation: if the error repeats, "
        "the environment is at fault; if it succeeds, the agent mishandled "
        "a transient"
    ),
    "wrong_fact_propagation": (
        "check the claim at its origin step against the source it cites: "
        "the wrong value either entered there or was transformed later"
    ),
    "divergence": (
        "splice the other agent's decision at the divergent step and re-run "
        "(the counterfactual section estimates the result of exactly that)"
    ),
    "process_pathology": (
        "fix only this behaviour (guard, retry, or dedupe) and re-run the "
        "task; the flag is binary and deterministic to re-check"
    ),
    "budget_pressure": (
        "raise the step budget for one re-run: if the agent finishes and "
        "passes, the budget was the binding constraint"
    ),
}


# ---------------------------------------------------------------------------
# evidence ledger


class _Ledger:
    """Collects evidence items and hands out stable ids."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self._seen: dict[tuple, str] = {}

    def span(self, agent: str, step: int, field: str, quote: str,
             signal: str, basis: str) -> str:
        """Add span evidence: ``quote`` appears in ``field`` of that step."""
        quote = (quote or "")[:160]
        key = ("span", agent, step, field, quote)
        if key in self._seen:
            return self._seen[key]
        eid = f"E{len(self.items) + 1}"
        self.items.append({
            "id": eid, "type": "span", "agent": agent, "step": step,
            "field": field, "quote": quote, "signal": signal, "basis": basis,
        })
        self._seen[key] = eid
        return eid

    def metric(self, path: str, value, signal: str, basis: str) -> str:
        """Add metric evidence: the report holds ``value`` at ``path``."""
        key = ("metric", path, repr(value))
        if key in self._seen:
            return self._seen[key]
        eid = f"E{len(self.items) + 1}"
        self.items.append({
            "id": eid, "type": "metric", "path": path, "value": value,
            "signal": signal, "basis": basis,
        })
        self._seen[key] = eid
        return eid


def _resolve_path(report: dict, path: str):
    """Walk a dotted/indexed path ('semantic.claims[2].value') into report."""
    node = report
    for raw in path.split("."):
        while raw:
            if "[" in raw:
                name, rest = raw.split("[", 1)
            else:
                name, rest = raw, ""
            if name:
                if not isinstance(node, dict) or name not in node:
                    return None, False
                node = node[name]
            if rest:
                idx_text, rest = rest.split("]", 1)
                try:
                    idx = int(idx_text)
                except ValueError:
                    return None, False
                if not isinstance(node, list) or not -len(node) <= idx < len(node):
                    return None, False
                node = node[idx]
            raw = rest.lstrip(".") if rest else ""
    return node, True


# ---------------------------------------------------------------------------
# hypothesis generators (each returns zero or more raw hypothesis dicts)


def _side(report: dict, side: str) -> dict:
    return report.get("process", {}).get(side, {}) or {}


def _answer_verdict(report: dict, side: str) -> tuple[Optional[str], Optional[float]]:
    ae = report.get("answer_eval") or {}
    entry = ae.get(f"{side}_vs_expected") or {}
    return entry.get("verdict"), entry.get("coverage")


def _gen_grader(report: dict, side: str, traj: Trajectory, led: _Ledger) -> list[dict]:
    """Failed, yet the answer matches the expected answer / process is clean."""
    verdict, coverage = _answer_verdict(report, side)
    ae = report.get("answer_eval") or {}
    if not ae.get("expected"):
        return [{
            "kind": "grader_or_label",
            "statement": (
                "the grader or task label, rather than the agent, may be "
                "wrong — but no expected answer is recorded, so this cannot "
                "be tested from the trace"
            ),
            "supports": [], "contradicts": [], "score": None,
            "status": "untestable",
        }]
    supports, contradicts = [], []
    score = 0.0
    other = "b" if side == "a" else "a"
    # an exclusive typed claim contradicting the expected answer voids the
    # coverage evidence entirely: the answer demonstrably asserts a wrong
    # value, and word overlap cannot vouch for a number it cannot read
    asserts_wrong_value = any(
        claim.get("matches_expected") is False
        and claim.get(f"{side}_steps") and not claim.get(f"{other}_steps")
        for claim in (report.get("semantic") or {}).get("claims", []))
    # "match" from the coverage metric is only grader-suspect evidence when
    # the coverage is near-total: an answer containing 70% of the expected
    # words can still contradict it outright (the missing 30% IS the
    # contradiction), so partial coverage earns nothing here.
    if (not asserts_wrong_value
            and verdict == "match" and coverage is not None
            and float(coverage) >= GRADER_COVERAGE_FLOOR):
        score += 0.5 + 0.4 * float(coverage)
        supports.append(led.metric(
            f"answer_eval.{side}_vs_expected.coverage", coverage,
            "answer matches the expected answer", "measured"))
        answer_step = len(traj.steps) - 1
        quote = traj.steps[answer_step].output[:120]
        if quote:
            supports.append(led.span(
                side, answer_step, "output", quote,
                "the emitted answer, for hand re-grading", "measured"))
    gap = _side(report, side).get("gap") or {}
    if gap.get("verdict") == "failed but clean":
        score += 0.35
        supports.append(led.metric(
            f"process.{side}.gap.verdict", "failed but clean",
            "nothing in the process visibly went wrong", "measured"))
    # The typed counterpart of coverage: word overlap cannot see a
    # paraphrase, but claim extraction can — a clean-process failure whose
    # ANSWER asserts the exact typed value the expected answer asserts is
    # grader-suspect however the sentence is worded.  The process gate is
    # exclusivity, not absolute cleanliness: a flag the PASSING run also
    # raises is shared behaviour and cannot explain a one-sided failure,
    # so only flags exclusive to the failing side block this rule (a
    # right-words-no-deed run raises its pathology alone and stays
    # blocked).  Voided like everything else by asserts_wrong_value.
    other_gap = _side(report, other).get("gap") or {}
    exclusive_flags = (set(gap.get("raised") or [])
                       - set(other_gap.get("raised") or []))
    if not asserts_wrong_value and not exclusive_flags:
        answer_idx = len(traj.steps) - 1
        for i, claim in enumerate(
                (report.get("semantic") or {}).get("claims", [])):
            if (claim.get("matches_expected") is True
                    and answer_idx in (claim.get(f"{side}_steps") or [])):
                # weighted on par with near-total lexical coverage (~0.9
                # with the clean bonus): typed equality reads the value
                # that coverage cannot, and is no weaker evidence
                score += 0.75
                supports.append(led.metric(
                    f"semantic.claims[{i}].value", claim.get("value"),
                    "the answer asserts the exact value the expected "
                    "answer asserts (typed claim match)", "measured"))
                break
    for i, claim in enumerate((report.get("semantic") or {}).get("claims", [])):
        if claim.get("matches_expected") is not False:
            continue
        if not claim.get(f"{side}_steps") or claim.get(f"{other}_steps"):
            # shared claims are context both runs carried; they neither
            # made this run fail nor say anything about the grader
            continue
        score -= 0.3
        contradicts.append(led.metric(
            f"semantic.claims[{i}].value", claim.get("value"),
            "a claim in this run contradicts the expected answer",
            "measured"))
    if not supports and not contradicts:
        return []
    reasons = []
    if verdict == "match":
        reasons.append("its answer matched the expected answer")
    if gap.get("verdict") == "failed but clean":
        reasons.append("its process was clean")
    statement = "the grader or task label is wrong, not the agent"
    if reasons:
        statement += ": " + " and ".join(reasons)
    return [{
        "kind": "grader_or_label",
        "statement": statement,
        "supports": supports, "contradicts": contradicts,
        "score": score, "status": None,
    }]


def _gen_harness(report: dict, side: str, traj: Trajectory, led: _Ledger) -> list[dict]:
    term = _side(report, side).get("termination") or {}
    reason = term.get("reason")
    out = []
    if reason in HARNESS_TERMINATIONS:
        out.append({
            "kind": "harness_termination",
            "statement": f"the harness killed the run ({reason}); no agent decision caused this",
            "supports": [led.metric(
                f"process.{side}.termination.reason", reason,
                "harness-side termination", "declared")],
            "contradicts": [], "score": 0.9, "status": None,
        })
    elif term.get("at_step_limit"):
        out.append({
            "kind": "harness_termination",
            "statement": "the run was cut at the step limit before it could finish",
            "supports": [led.metric(
                f"process.{side}.termination.at_step_limit", True,
                "stopped exactly at max_steps", "declared")],
            "contradicts": [], "score": 0.55, "status": None,
        })
    return out


def _gen_environment(report: dict, side: str, traj: Trajectory, led: _Ledger) -> list[dict]:
    rec = _side(report, side).get("recovery") or {}
    if not rec.get("errors"):
        return []
    basis = rec.get("basis", "inferred from observation text")
    abandoned = rec.get("abandoned_after_error", 0)
    supports = []
    for e in rec.get("error_steps", [])[:3]:
        idx = e.get("index")
        if idx is None or not (0 <= idx < len(traj.steps)):
            continue
        quote = traj.steps[idx].output[:120]
        if quote:
            supports.append(led.span(
                side, idx, "output", quote,
                f"tool error ({e.get('outcome', 'unresolved')})", basis))
    score = 0.5 if abandoned else 0.2
    statement = (
        f"a tool/environment error derailed the run"
        + (f" and the agent abandoned the task after it" if abandoned
           else ", though the agent recovered — recovered errors rarely "
                "explain a failure on their own")
    )
    first_error = next(
        (e.get("index") for e in rec.get("error_steps", [])
         if e.get("index") is not None), None)
    return [{
        "kind": "environment_error", "statement": statement,
        "supports": supports, "contradicts": [], "score": score,
        "status": None, "error_step": first_error,
        "abandoned": bool(abandoned),
    }]


def _gen_wrong_fact(report: dict, side: str, traj: Trajectory, led: _Ledger) -> list[dict]:
    claims = (report.get("semantic") or {}).get("claims", [])
    supports = []
    origins = []
    other = "b" if side == "a" else "a"
    for i, claim in enumerate(claims):
        if claim.get("matches_expected") is not False:
            continue
        if not claim.get(f"{side}_steps"):
            continue
        # A claim the *other* run also carries cannot explain why only this
        # run failed: a shared subtotal both agents read is context, not the
        # wrong fact.  Only claims exclusive to the failing side qualify.
        if claim.get(f"{other}_steps"):
            continue
        supports.append(led.metric(
            f"semantic.claims[{i}].value", claim.get("value"),
            "claim contradicting the expected answer", "measured"))
        origin = claim.get("origin") or {}
        if origin.get("agent") == side and origin.get("step") is not None:
            idx = origin["step"]
            if 0 <= idx < len(traj.steps):
                quote = traj.steps[idx].output[:120]
                if quote:
                    supports.append(led.span(
                        side, idx, "output", quote,
                        "origin of the contradicting claim", "measured"))
                origins.append(idx)
    if not supports:
        return []
    where = (f", entering at step {origins[0]}" if origins else
             " (origin step not identified)")
    return [{
        "kind": "wrong_fact_propagation",
        "statement": f"a wrong fact propagated into the answer{where}",
        "supports": supports, "contradicts": [],
        "score": 0.5 + 0.1 * min(len(origins), 2), "status": None,
        "origin": min(origins) if origins else None,
    }]


def _root_category(traj: Trajectory, root: Optional[int], fallback: str) -> str:
    """Name the divergence by what the root step actually is, not by the
    divergence detector's label — a plan step is planning even when the
    detector filed it under tool_selection."""
    if root is None or not (0 <= root < len(traj.steps)):
        return fallback
    step_type = traj.steps[root].type
    if step_type == "plan":
        return "planning"
    if step_type in ("think", "reason"):
        return "reasoning"
    if step_type in ("retrieve", "search"):
        return "retrieval"
    return fallback


def _gen_divergence(report: dict, side: str, traj: Trajectory, led: _Ledger) -> list[dict]:
    attribution = report.get("attribution") or {}
    root = attribution.get("root_cause_step")
    divergences = report.get("divergences") or []
    if root is None or not divergences:
        return []
    # The twin rule (the exclusivity principle, third application): a step
    # the other run also took verbatim cannot be the decisive decision —
    # only which COPY of it the aligner matched differs, and that is an
    # alignment artefact, not a divergence.  Advance the anchor to the
    # first step with no exact twin in the other run, stopping before the
    # answer so a degenerate case keeps a bounded anchor.
    other = "b" if side == "a" else "a"
    other_sigs = {(s.get("type"), s.get("name"), s.get("input"))
                  for s in (report.get(other) or {}).get("steps", [])}
    while (root < len(traj.steps) - 1
           and (traj.steps[root].type, traj.steps[root].name,
                traj.steps[root].input) in other_sigs):
        root += 1
    category = _root_category(traj, root, divergences[0].get("kind", "divergence"))
    supports, contradicts = [], []
    score = 0.45
    root_step = traj.steps[root] if 0 <= root < len(traj.steps) else None
    if root_step is not None:
        quote = (root_step.output or root_step.input)[:120]
        field = "output" if root_step.output else "input"
        if quote:
            supports.append(led.span(
                side, root, field, quote,
                f"the divergent {category} step", "measured"))
        if root_step.quality in ("weak", "bad"):
            score += 0.15
            supports.append(led.span(
                side, root, "quality", root_step.quality,
                "step quality annotated in the log", "declared"))
    cf = report.get("counterfactual") or {}
    if (cf.get("estimate") or {}).get("outcome") == "success":
        score += 0.1
        supports.append(led.metric(
            "counterfactual.estimate.outcome", "success",
            "splicing the other decision at this step is estimated to succeed",
            "estimated"))
    verdict, coverage = _answer_verdict(report, side)
    if verdict == "match" and coverage is not None and coverage >= 0.9:
        score -= 0.3
        contradicts.append(led.metric(
            f"answer_eval.{side}_vs_expected.coverage", coverage,
            "hard to blame the divergence when the answer matched the "
            "expected answer", "measured"))
    return [{
        "kind": "divergence",
        "statement": (
            f"the run went wrong at step {root}, a {category} decision "
            f"that the other agent made differently"
        ),
        "supports": supports, "contradicts": contradicts,
        "score": score, "status": None, "category": category, "root": root,
    }]


def _flag_steps(side_report: dict, flag: str) -> list[dict]:
    """Step records behind a raised process flag, best effort."""
    se = side_report.get("side_effects") or {}
    rep = side_report.get("repeats") or {}
    ground = side_report.get("grounding") or {}
    if flag == "blind_write":
        return se.get("blind_write_steps", [])
    if flag == "repeated_calls":
        return rep.get("repeated_steps", [])
    if flag in ("looped", "loop_block"):
        return rep.get("cycle_steps", [])
    if flag == "no_information_steps":
        return rep.get("no_information_detail", [])
    if flag == "swallowed_error":
        return (side_report.get("recovery") or {}).get("error_steps", [])
    if flag == "invented_arguments":
        return ground.get("invented_arguments", [])
    if flag == "undeclared_tools":
        return ground.get("undeclared_tool_steps", [])
    return []


def _gen_process(report: dict, side: str, traj: Trajectory, led: _Ledger,
                 failed: bool) -> list[dict]:
    gap = _side(report, side).get("gap") or {}
    raised = gap.get("raised", []) or []
    out = []
    side_report = _side(report, side)
    for flag in raised:
        if flag == "budget_pressure":
            continue  # its own generator
        supports = []
        for record in _flag_steps(side_report, flag)[:3]:
            idx = record.get("index") if isinstance(record, dict) else None
            if idx is None or not (0 <= idx < len(traj.steps)):
                continue
            step = traj.steps[idx]
            quote = (step.output or step.input or step.name)[:120]
            field = ("output" if step.output else
                     "input" if step.input else "name")
            if quote:
                supports.append(led.span(
                    side, idx, field, quote,
                    f"process flag {flag}", record.get("basis", "measured")))
        if not supports:
            supports.append(led.metric(
                f"process.{side}.gap.flags.{flag}", True,
                f"process flag {flag} raised", "measured"))
        base = 0.4 if failed else 0.3
        out.append({
            "kind": "process_pathology", "flag": flag,
            "statement": (
                f"the agent {_FLAG_STATEMENTS.get(flag, flag)}"
                + ("" if failed else
                   " — it passed anyway, so the outcome hides this")
            ),
            "supports": supports, "contradicts": [], "score": base,
            "status": None,
        })
    return out


def _gen_budget(report: dict, side: str, traj: Trajectory, led: _Ledger) -> list[dict]:
    term = _side(report, side).get("termination") or {}
    if not term.get("under_budget_pressure"):
        return []
    return [{
        "kind": "budget_pressure",
        "statement": "the step budget squeezed the run before it finished cleanly",
        "supports": [led.metric(
            f"process.{side}.termination.budget_used",
            term.get("budget_used"), "fraction of step budget consumed",
            "declared")],
        "contradicts": [], "score": 0.35, "status": None,
    }]


# ---------------------------------------------------------------------------
# fusion: corroborating hypotheses are one story at two depths


def _dedupe(refs: list[str]) -> list[str]:
    return list(dict.fromkeys(refs))


def _fuse(raw: list[dict], report: dict, ledger: list[dict]) -> None:
    """Compose hypotheses that describe the same causal path.

    A structural divergence and a wrong fact are usually not competitors:
    the divergence is the decision, the wrong fact is the mechanism it set
    in motion.  Reporting them as a near-tie reads as uncertainty when the
    two signals actually corroborate one account.  So:

    - wrong fact entering at or after the divergent step → the divergence
      hypothesis absorbs it as its mechanism (score is the max of the two
      plus a corroboration bonus; the wrong-fact hypothesis is kept,
      marked ``merged`` with a pointer, so nothing disappears silently);
    - wrong fact entering *before* the divergent step → the anchor moves:
      the earliest causally-connected anomaly is the root, the later
      structural divergence is downstream symptom, and the divergence
      hypothesis is penalised with that stated.
    - a raised process flag whose steps sit inside the attribution chain
      likewise merges into the divergence account as its mechanism.
    """
    div = next((h for h in raw if h["kind"] == "divergence"), None)
    wf = next((h for h in raw if h["kind"] == "wrong_fact_propagation"), None)
    if div is not None and wf is not None and div["agent"] == wf["agent"]:
        root = div.get("root")
        origin = wf.get("origin")
        if origin is not None and root is not None and origin >= root:
            div["score"] = max(div["score"], wf["score"]) + 0.15
            div["supports"] = _dedupe(div["supports"] + wf["supports"])
            div["mechanism"] = "wrong_fact_propagation"
            div["statement"] += (
                f"; the wrong fact it set in motion entered at step "
                f"{origin} and propagated into the answer")
            wf["status"] = "merged"
            wf["merged_into_kind"] = "divergence"
        elif origin is not None and root is not None and origin < root:
            wf["score"] += 0.15
            wf["statement"] += (
                f" — this predates the structural divergence at step "
                f"{root}, so the divergence is downstream symptom, not "
                f"cause")
            div["score"] -= 0.2
            div["contradicts"] = _dedupe(div["contradicts"] + wf["supports"])
            div["statement"] += (
                f" — but a wrong fact was already in play at step "
                f"{origin}, before this decision")
    env = next((h for h in raw if h["kind"] == "environment_error"), None)
    if (div is not None and env is not None and env.get("abandoned")
            and div["agent"] == env["agent"]):
        root = div.get("root")
        err = env.get("error_step")
        if err is not None and root is not None and err <= root:
            # The error happened at or before the structural divergence:
            # the divergence is what the agent did AFTER the error, so the
            # error is the earlier evidenced anomaly and takes the anchor —
            # the same rule the wrong-fact fusion applies.
            env["score"] += 0.15
            env["statement"] += (
                f" — the error at step {err} precedes the structural "
                f"divergence at step {root}, so the divergence is the "
                f"agent's reaction to the error, not an independent cause")
            div["score"] -= 0.2
            div["contradicts"] = _dedupe(div["contradicts"] + env["supports"])
            div["statement"] += (
                f" — but a tool error was already in play at step {err}, "
                f"before this decision")
            # the swallowed_error flag is the same event seen by the
            # process analysis; it corroborates, it does not compete
            for h in raw:
                if (h["kind"] == "process_pathology"
                        and h.get("flag") == "swallowed_error"
                        and h["agent"] == env["agent"]
                        and h.get("status") != "merged"):
                    env["score"] += 0.1
                    env["supports"] = _dedupe(env["supports"] + h["supports"])
                    h["status"] = "merged"
                    h["merged_into_kind"] = "environment_error"
    if div is not None:
        chain = set((report.get("attribution") or {}).get("chain") or [])
        for h in raw:
            if h["kind"] != "process_pathology" or h["agent"] != div["agent"]:
                continue
            if h.get("status") == "merged":
                continue
            # a flag corroborates the divergence account when its evidence
            # spans sit on chain steps
            h_steps = _hypothesis_span_steps(h, ledger)
            if h_steps and h_steps & chain:
                div["score"] += 0.1
                div["supports"] = _dedupe(div["supports"] + h["supports"])
                h["status"] = "merged"
                h["merged_into_kind"] = "divergence"
                div["statement"] += (
                    f"; on the way it {_FLAG_STATEMENTS.get(h.get('flag'), h.get('flag'))}")
    for h in raw:
        h["supports"] = _dedupe(h["supports"])
        h["contradicts"] = _dedupe(h["contradicts"])


def _hypothesis_span_steps(h: dict, ledger: list[dict]) -> set:
    """Step indices of a hypothesis's span evidence."""
    by_id = {item["id"]: item for item in ledger}
    steps = set()
    for ref in h.get("supports", []):
        item = by_id.get(ref)
        if item and item.get("type") == "span" and item.get("step") is not None:
            steps.add(item["step"])
    return steps


# ---------------------------------------------------------------------------
# adjudication


def _assign_status(ranked: list[dict]) -> tuple[Optional[str], Optional[float]]:
    """Set each hypothesis's status; return (leading id, margin).

    Hypotheses already marked ``merged`` keep that status and are excluded
    from the margin computation — they are part of another account, not
    competitors to it."""
    scored = [h for h in ranked
              if h["score"] is not None and h["status"] != "merged"]
    leading_id, margin = None, None
    if scored:
        top = scored[0]
        runner = scored[1]["score"] if len(scored) > 1 else 0.0
        margin = round(top["score"] - runner, 4)
        if top["score"] >= PLAUSIBLE_FLOOR and (
                len(scored) == 1 or margin >= LEAD_MARGIN):
            leading_id = top["id"]
    for h in ranked:
        if h["status"] in ("untestable", "merged"):
            continue
        if h["id"] == leading_id:
            h["status"] = "leading"
        elif h["contradicts"] and h["score"] is not None and h["score"] < RULED_OUT_CEILING:
            h["status"] = "ruled_out"
        elif h["score"] is not None and h["score"] >= PLAUSIBLE_FLOOR:
            h["status"] = "plausible"
        else:
            h["status"] = "weak"
    return leading_id, margin


#: minimum word overlap for a step to join the causal account by textual
#: propagation.  Lower than attribution's PROPAGATION_THRESHOLD on purpose:
#: the account prints the measured overlap on every link, so a weaker link
#: is visible as weak rather than silently dropped.
ACCOUNT_LINK_FLOOR = 0.15


def _anchor_step(leading: dict, led: _Ledger) -> Optional[int]:
    """The leading hypothesis's own anchor — the same step decisive_step
    commits to."""
    kind = leading.get("kind")
    if kind == "divergence":
        return leading.get("root")
    if kind == "wrong_fact_propagation":
        return leading.get("origin")
    if kind == "environment_error":
        return leading.get("error_step")
    spans = _hypothesis_span_steps(leading, led.items)
    return min(spans) if spans else None


def _causal_account(report: dict, leading: Optional[dict], side: Optional[str],
                    traj: Optional[Trajectory], led: _Ledger) -> list[dict]:
    """Mechanism-annotated account of the leading hypothesis.

    Anchored at the hypothesis's own decisive step (an environment-led
    account starts at the failing call, not at some structural divergence
    elsewhere), then walked forward **transitively**: a step joins the
    chain when it carries measurable overlap with the output of any step
    already in it — not only the anchor — or when the log annotates it
    weak/bad, or when it is a declared error downstream of the anchor.
    The final answer always closes the account; a link that could not be
    traced says so instead of pretending.  Every link prints its measured
    overlap or its declared basis: "propagated" without a number is an
    assertion, not evidence.
    """
    if leading is None or side is None or traj is None:
        return []
    if leading["kind"] not in ("divergence", "wrong_fact_propagation",
                               "process_pathology", "environment_error"):
        return []
    from .align import jaccard  # local import avoids a cycle at module load

    anchor = _anchor_step(leading, led)
    if anchor is None or not (0 <= anchor < len(traj.steps)):
        fallback = (report.get("attribution") or {}).get("chain") or []
        anchor = fallback[0] if fallback else None
        if anchor is None or not (0 <= anchor < len(traj.steps)):
            return []
    answer_idx = len(traj.steps) - 1

    def _carrier(step) -> str:
        return step.output or step.input or ""

    # typed-claim provenance beats lexical overlap where it exists: every
    # step carrying a contradicting claim exclusive to this side is part of
    # the wrong value's traced path (claim-centric linking, per DRIFT)
    other = "b" if side == "a" else "a"
    claim_steps: dict[int, str] = {}
    for claim in (report.get("semantic") or {}).get("claims", []):
        if claim.get("matches_expected") is not False:
            continue
        if not claim.get(f"{side}_steps") or claim.get(f"{other}_steps"):
            continue
        for idx in claim[f"{side}_steps"]:
            claim_steps.setdefault(idx, str(claim.get("value")))

    members: list[int] = [anchor]
    links: dict[int, tuple] = {}  # idx -> (mechanism text)
    member_outputs = {(traj.steps[anchor].output or "").strip()}
    for idx in range(anchor + 1, len(traj.steps)):
        step = traj.steps[idx]
        # an output identical to one already in the chain carries no NEW
        # consequence — a repeated call is a pathology, not propagation.
        # EXCEPT a repeated declared error: the agent failing the same way
        # again is part of the fault's story, not a no-op.
        duplicate = ((step.output or "").strip() in member_outputs
                     and (step.output or "").strip()
                     and step.error is not True)
        # a reason/think step immediately after an on-chain declared error
        # is the agent's response to it — joined by adjacency, and labelled
        # as adjacency rather than dressed up as traced propagation
        prev_member_is_error = (members and members[-1] == idx - 1
                                and traj.steps[idx - 1].error is True)
        best = (0.0, None)
        for m in members:
            source = traj.steps[m].output or traj.steps[m].input
            if not source:
                continue
            overlap = max(jaccard(step.input, source),
                          jaccard(step.output, source))
            if overlap > best[0]:
                best = (overlap, m)
        overlap, from_step = best
        if duplicate and idx != answer_idx:
            continue
        if idx in claim_steps and idx != answer_idx:
            links[idx] = (f"carries the contradicting value "
                          f"{claim_steps[idx]!r} (typed claim provenance, "
                          f"measured)")
        elif overlap >= ACCOUNT_LINK_FLOOR and idx != answer_idx:
            links[idx] = (f"textual propagation from step {from_step} "
                          f"(word overlap {round(overlap, 2)}, measured)")
        elif step.quality in ("weak", "bad"):
            links[idx] = (f"step annotated {step.quality} in the log "
                          f"(declared)")
        elif (step.error is True
              and (leading["kind"] == "environment_error"
                   or (members and members[-1] == idx - 1
                       and traj.steps[idx - 1].error is True))):
            # errors join by declaration when the error IS the story, or
            # when this is the same fault repeating right after an on-chain
            # error; an unrelated declared error elsewhere is not
            # propagation
            links[idx] = ("a declared error downstream of the failing call "
                          "(declared)")
        elif (step.type in ("reason", "think") and prev_member_is_error):
            links[idx] = ("the agent's response to the declared error "
                          "immediately above (adjacency, declared — not "
                          "traced propagation)")
        elif idx == answer_idx:
            links[idx] = (
                f"textual propagation from step {from_step} "
                f"(word overlap {round(overlap, 2)}, measured)"
                if overlap >= ACCOUNT_LINK_FLOOR else
                "the final answer — no traced overlap with the chain; the "
                "link is positional, not traced")
        else:
            continue
        members.append(idx)
        if (step.output or "").strip():
            member_outputs.add((step.output or "").strip())

    account = []
    for idx in members:
        step = traj.steps[idx]
        quote = (step.output or step.input or step.name)[:120]
        field = "output" if step.output else "input" if step.input else "name"
        eid = led.span(side, idx, field, quote,
                       "causal account", "measured") if quote else None
        if idx == anchor:
            happened = f"the account starts here ({step.type} step)"
            mechanism = None
        elif idx == answer_idx:
            happened = "the final answer was emitted"
            mechanism = links.get(idx)
        else:
            happened = f"{step.type} step carried the fault forward"
            mechanism = links.get(idx)
        entry = {"step": idx, "happened": happened,
                 "evidence": [eid] if eid else []}
        if mechanism:
            entry["mechanism"] = mechanism
        account.append(entry)
    return account


def _verdict_text(mode: str, subject_name: Optional[str], ranked: list[dict],
                  leading_id: Optional[str], margin: Optional[float]) -> str:
    scored = [h for h in ranked
              if h["score"] is not None and h["status"] != "merged"]
    if not scored:
        return (
            "no diagnostic signal distinguishes these runs; nothing to "
            "adjudicate")
    top = scored[0]
    if leading_id is not None:
        text = (f"{subject_name or 'the run'}: best explained by "
                f"{top['kind']}" +
                (f" ({top.get('flag')})" if top.get("flag") else "") +
                f" — {top['statement']}")
        if len(scored) > 1:
            text += (f"; leads the runner-up ({scored[1]['kind']}) by "
                     f"{margin:.2f}")
        return text
    names = ", ".join(h["kind"] for h in scored[:3])
    return (
        f"contested: {names} are within {LEAD_MARGIN:.2f} of each other — "
        f"the evidence does not pick a single cause; run the discriminating "
        f"checks listed on each hypothesis")


def _contradictions(report: dict, side: Optional[str]) -> list[str]:
    """Cross-signal conflicts worth stating plainly, whoever wins."""
    out = []
    if side:
        verdict, coverage = _answer_verdict(report, side)
        success = (report.get(side) or {}).get("outcome", {}).get("success")
        if success is False and verdict == "match":
            out.append(
                f"the {'run' if side is None else 'failed run'}'s answer "
                f"matched the expected answer (coverage "
                f"{coverage if coverage is not None else '?'}) yet the "
                f"outcome is recorded as failure — these cannot both be "
                f"fully right")
        other = "b" if side == "a" else "a"
        other_gap = _side(report, other).get("gap") or {}
        if other_gap.get("verdict") == "passed but pathological":
            flags = ", ".join(other_gap.get("raised", [])[:4])
            out.append(
                f"the winning run passed while raising process flags "
                f"({flags}) — its win is an outcome fact, not a process "
                f"endorsement")
    return out


def _confidence(mode: str, leading_id: Optional[str], ranked: list[dict]) -> dict:
    plausible_others = [
        h["kind"] for h in ranked
        if h["status"] == "plausible" and h["id"] != leading_id]
    if leading_id is None:
        return {"level": "low",
                "basis": "no hypothesis clears the runner-up by the lead "
                         "margin; single pair, alternatives not ruled out"}
    if plausible_others:
        return {"level": "medium",
                "basis": "single pair (n=1); plausible alternatives remain: "
                         + ", ".join(plausible_others)}
    return {"level": "medium",
            "basis": "single pair (n=1); the ranking is consistent but one "
                     "comparison cannot rule out task-specific luck"}


#: the counterfactual criterion the field converged on (Who&When): the
#: decisive step is the EARLIEST step whose correction would turn the
#: failure into success.
DECISIVE_CRITERION = (
    "earliest step whose correction is expected to flip the outcome")


def _decisive_step(mode: str, leading: Optional[dict],
                   led: _Ledger) -> dict:
    """Commit to a decisive error step — or abstain with the reason.

    Attributors that always name a step score points by luck on causes
    that have no step: a grader mislabel and a harness kill contain no
    agent mistake to correct, so the honest answer there is None with the
    reason stated, and an eval should score that abstention as an answer
    in its own right.  Anchors per kind follow the fusion rules, which
    already guarantee the leading hypothesis holds the *earliest*
    evidenced anomaly (a wrong fact or error predating the divergence
    re-anchors the lead before this function ever runs).
    """
    if mode != "single_failure" or leading is None:
        reason = ("no failure to localize" if mode != "single_failure" else
                  "contested: no hypothesis leads, so no step is committed")
        return {"step": None, "criterion": DECISIVE_CRITERION,
                "basis": None, "reason": reason}
    kind = leading.get("kind")
    if kind == "grader_or_label":
        return {"step": None, "criterion": DECISIVE_CRITERION, "basis": None,
                "reason": ("no agent error to correct — the correction is "
                           "to the grader or label, not to a step")}
    if kind == "harness_termination":
        return {"step": None, "criterion": DECISIVE_CRITERION, "basis": None,
                "reason": ("the harness ended the run; no corrected agent "
                           "step would have prevented the kill")}
    if kind == "budget_pressure":
        return {"step": None, "criterion": DECISIVE_CRITERION, "basis": None,
                "reason": ("the binding constraint is the step budget, a "
                           "harness setting, not an agent step")}
    if kind == "divergence":
        step = leading.get("root")
        basis = "the divergent decision; fused evidence places nothing earlier"
    elif kind == "wrong_fact_propagation":
        step = leading.get("origin")
        basis = "where the wrong fact entered, per claim provenance"
    elif kind == "environment_error":
        step = leading.get("error_step")
        basis = "the failing tool call the agent then abandoned"
    else:  # process_pathology and anything span-anchored
        spans = _hypothesis_span_steps(leading, led.items)
        step = min(spans) if spans else None
        basis = ("the first step raising the process flag" if spans else None)
    if step is None:
        return {"step": None, "criterion": DECISIVE_CRITERION, "basis": None,
                "reason": f"{kind} leads but anchors to no specific step"}
    return {"step": step, "criterion": DECISIVE_CRITERION,
            "basis": basis, "reason": None}


# ---------------------------------------------------------------------------
# public API


def diagnose(report: dict, a: Trajectory, b: Trajectory) -> dict:
    """Build the SCHEMA.md ``diagnosis`` object for a compared pair."""
    success_a = a.outcome.success
    success_b = b.outcome.success
    led = _Ledger()

    if success_a == success_b:
        mode = "both_succeeded" if success_a else "both_failed"
        sides = ["a", "b"] if mode == "both_failed" else []
        # both succeeded: diagnose any pathological passer
        if mode == "both_succeeded":
            for side in ("a", "b"):
                gap = _side(report, side).get("gap") or {}
                if gap.get("verdict") == "passed but pathological":
                    sides.append(side)
        subject = None
        subject_name = None
    else:
        mode = "single_failure"
        subject = "a" if not success_a else "b"
        subject_name = (a if subject == "a" else b).agent.name
        sides = [subject]

    raw: list[dict] = []
    for side in sides:
        traj = a if side == "a" else b
        failed = not (success_a if side == "a" else success_b)
        generated: list[dict] = []
        if failed:
            generated += _gen_grader(report, side, traj, led)
            generated += _gen_harness(report, side, traj, led)
            generated += _gen_environment(report, side, traj, led)
            generated += _gen_wrong_fact(report, side, traj, led)
            if mode == "single_failure":
                generated += _gen_divergence(report, side, traj, led)
            generated += _gen_budget(report, side, traj, led)
        generated += _gen_process(report, side, traj, led, failed)
        for h in generated:
            h["agent"] = side
        raw += generated

    if mode == "single_failure":
        _fuse(raw, report, led.items)

    # rank: scored first (descending), then untestable; ties break by the
    # KINDS order and statement text so the ordering is deterministic.
    def _key(h: dict):
        score = h["score"] if h["score"] is not None else -1.0
        return (-score, KINDS.index(h["kind"]) if h["kind"] in KINDS else 99,
                h["statement"])

    ranked = sorted(raw, key=_key)
    for i, h in enumerate(ranked):
        h["id"] = f"H{i + 1}"
        if h["score"] is not None:
            h["score"] = round(max(0.0, min(1.0, h["score"])), 4)
        h["discriminator"] = _DISCRIMINATORS.get(h["kind"], "")

    leading_id, margin = _assign_status(ranked)
    leading = next((h for h in ranked if h["id"] == leading_id), None)

    account_side = leading["agent"] if leading else subject
    account_traj = (a if account_side == "a" else b) if account_side else None
    account = _causal_account(
        report, leading, account_side, account_traj, led
    ) if mode == "single_failure" else []

    verdict = _verdict_text(mode, subject_name, ranked, leading_id, margin)
    if mode == "both_succeeded" and not ranked:
        verdict = "both passed cleanly; nothing to diagnose"
    elif mode == "both_succeeded":
        names = []
        for h in ranked:
            traj = a if h["agent"] == "a" else b
            if traj.agent.name not in names:
                names.append(traj.agent.name)
        verdict = (
            "no failure to diagnose, but the outcome hides process "
            "pathologies in: " + ", ".join(names))
    elif mode == "both_failed" and ranked:
        kinds_a = {h["kind"] for h in ranked if h["agent"] == "a"
                   and h["status"] in ("leading", "plausible")}
        kinds_b = {h["kind"] for h in ranked if h["agent"] == "b"
                   and h["status"] in ("leading", "plausible")}
        shared = kinds_a & kinds_b
        if shared:
            verdict = (
                "both failed with a shared plausible cause ("
                + ", ".join(sorted(shared))
                + ") — that points at the task or environment, not either "
                  "agent alone")

    return {
        "version": 1,
        "mode": mode,
        "subject": subject,
        "subject_name": subject_name,
        "verdict": verdict,
        "hypotheses": ranked,
        "leading": leading_id,
        "margin": margin,
        "evidence": led.items,
        "causal_account": account,
        "decisive_step": _decisive_step(mode, leading, led),
        "contradictions": _contradictions(
            report, subject if mode == "single_failure" else None),
        "confidence": _confidence(mode, leading_id, ranked)
        if ranked else {"level": None, "basis": "nothing to adjudicate"},
    }


def check_diagnosis(diagnosis: dict, report: dict,
                    a: Trajectory, b: Trajectory) -> list[str]:
    """Verify every evidence item against the trajectories and the report.

    Span evidence must quote a substring of the cited step field; metric
    evidence must resolve to the recorded value.  Returns a list of
    violations (empty means the diagnosis is fully grounded) — the same
    contract ``check_narration`` applies to narration text.
    """
    problems = []
    for item in diagnosis.get("evidence", []):
        eid = item.get("id", "?")
        if item.get("type") == "span":
            traj = a if item.get("agent") == "a" else b
            idx = item.get("step")
            if idx is None or not (0 <= idx < len(traj.steps)):
                problems.append(f"{eid}: step {idx} out of range")
                continue
            value = getattr(traj.steps[idx], item.get("field", ""), None)
            if not isinstance(value, str) or item.get("quote", "") not in value:
                problems.append(
                    f"{eid}: quote not found in {item.get('agent')}.steps"
                    f"[{idx}].{item.get('field')}")
        elif item.get("type") == "metric":
            found, ok = _resolve_path(report, item.get("path", ""))
            if not ok:
                problems.append(f"{eid}: path {item.get('path')!r} does not resolve")
            elif found != item.get("value"):
                problems.append(
                    f"{eid}: report holds {found!r} at {item.get('path')!r}, "
                    f"evidence says {item.get('value')!r}")
        else:
            problems.append(f"{eid}: unknown evidence type {item.get('type')!r}")
    # every hypothesis's evidence references must exist in the ledger
    known = {item.get("id") for item in diagnosis.get("evidence", [])}
    for h in diagnosis.get("hypotheses", []):
        for ref in list(h.get("supports", [])) + list(h.get("contradicts", [])):
            if ref not in known:
                problems.append(f"{h.get('id')}: dangling evidence ref {ref}")
    for entry in diagnosis.get("causal_account", []):
        for ref in entry.get("evidence", []):
            if ref not in known:
                problems.append(
                    f"causal_account step {entry.get('step')}: dangling "
                    f"evidence ref {ref}")
    return problems


def systemic_diagnosis(reports: list[dict]) -> dict:
    """Roll pair diagnoses up across a batch: which causes repeat.

    Counts leading (and, separately, contested) diagnoses by kind, with the
    denominator always stated.  A cause that leads in most diagnosed
    failures is systemic — worth fixing once, centrally — rather than
    task-local.
    """
    diagnosed = 0
    leading_kinds: dict[str, int] = {}
    contested = 0
    tasks_by_kind: dict[str, list[str]] = {}
    for rep in reports:
        diag = rep.get("diagnosis")
        if not diag or diag.get("mode") != "single_failure":
            continue
        diagnosed += 1
        task = (rep.get("task") or {}).get("id", "?")
        if diag.get("leading") is None:
            contested += 1
            continue
        lead = next((h for h in diag.get("hypotheses", [])
                     if h.get("id") == diag["leading"]), None)
        if lead is None:
            continue
        kind = lead.get("kind", "?")
        if lead.get("flag"):
            kind = f"{kind}:{lead['flag']}"
        leading_kinds[kind] = leading_kinds.get(kind, 0) + 1
        tasks_by_kind.setdefault(kind, []).append(task)
    ranked = sorted(leading_kinds.items(), key=lambda kv: (-kv[1], kv[0]))
    systemic = [
        {"kind": kind, "count": count, "of": diagnosed,
         "tasks": tasks_by_kind[kind]}
        for kind, count in ranked
    ]
    note = None
    if diagnosed and systemic and systemic[0]["count"] >= 2:
        top = systemic[0]
        note = (
            f"{top['kind']} leads {top['count']} of {diagnosed} diagnosed "
            f"failures — a repeated cause is worth one central fix, not "
            f"{top['count']} local ones")
    elif diagnosed == 0:
        note = "no single-failure pairs to diagnose in this batch"
    return {
        "diagnosed_failures": diagnosed,
        "contested": contested,
        "by_leading_kind": systemic,
        "note": note,
    }
