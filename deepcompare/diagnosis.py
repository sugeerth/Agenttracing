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

import re

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


#: what kind of thing an evidence item rests on.  Observable: recorded
#: tool input and output, answers, outcomes, alignment, effects — events
#: the trace shows.  Annotation: quality and note fields — someone's
#: judgement written onto the trace.  Stated: plan and reason text — what
#: the agent said, which the CoT-faithfulness literature warns is not what
#: it did.  At equal score, observable support outranks the other two.
EVIDENCE_CLASSES = ("observable", "annotation", "stated")


class Ledger:
    """The one registry of evidence: every hypothesis cites items here by
    id, and every item knows what class of thing it rests on."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self._seen: dict[tuple, str] = {}

    def classify(self, a: "Trajectory", b: "Trajectory") -> None:
        """Stamp ``evidence_class`` on every item, from the step it quotes."""
        for item in self.items:
            if item.get("evidence_class"):
                continue
            if item["type"] == "metric":
                basis = str(item.get("basis") or "").lower()
                item["evidence_class"] = ("annotation" if "annotat" in basis
                                          else "observable")
                continue
            traj = a if item.get("agent") == "a" else b
            step = None
            try:
                step = traj.steps[item["step"]]
            except (IndexError, TypeError, AttributeError):
                step = None
            field = item.get("field")
            if field in ("quality", "note"):
                item["evidence_class"] = "annotation"
            elif step is not None and step.type in ("plan", "reason"):
                item["evidence_class"] = "stated"
            else:
                item["evidence_class"] = "observable"

    def classes_of(self, ids: list) -> dict:
        by_id = {item["id"]: item for item in self.items}
        counts = {cls: 0 for cls in EVIDENCE_CLASSES}
        for eid in ids or []:
            item = by_id.get(eid)
            if item:
                counts[item.get("evidence_class") or "observable"] += 1
        return counts

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


#: the earlier private name, kept for callers that used it
_Ledger = Ledger


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


#: tokens that flip the polarity of a sentence.  A coverage "match" whose
#: sides disagree on how many of these they contain may be asserting the
#: opposite of the expected answer with a near-perfect token overlap.
NEGATORS = frozenset((
    "not", "no", "never", "cannot", "without", "unable", "failed",
    "refused", "except", "neither", "nor",
))


def _negator_count(text: str) -> int:
    import re
    tokens = re.findall(r"[a-z']+", (text or "").lower())
    count = sum(1 for token in tokens if token in NEGATORS)
    count += sum(1 for token in tokens if token.endswith("n't"))
    return count


def _invention_keys(grounding: dict) -> set:
    """The (tool, argument, value) triples a run's grounding analysis
    flagged as sourceless.  Exclusivity between two runs is decided on
    these keys, never on the flag bit: both runs passing the same literal
    keyword unsourced is shared noise, and must not mask an entity only
    one run invented."""
    return {(r.get("name"), r.get("argument"), r.get("value"))
            for r in (grounding or {}).get("invented_arguments", [])
            if isinstance(r, dict)}


def _answer_verdict(report: dict, side: str) -> tuple[Optional[str], Optional[float]]:
    ae = report.get("answer_eval") or {}
    entry = ae.get(f"{side}_vs_expected") or {}
    return entry.get("verdict"), entry.get("coverage")


def _gen_grader(report: dict, side: str, traj: Trajectory, led: Ledger) -> list[dict]:
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
    answer_evidence = False
    other = "b" if side == "a" else "a"
    # an exclusive typed claim contradicting the expected answer voids the
    # coverage evidence entirely: the answer demonstrably asserts a wrong
    # value, and word overlap cannot vouch for a number it cannot read
    asserts_wrong_value = any(
        claim.get("matches_expected") is False
        and claim.get(f"{side}_steps") and not claim.get(f"{other}_steps")
        for claim in (report.get("semantic") or {}).get("claims", []))
    # the process gate, shared by both answer-evidence branches: a flag
    # exclusive to the failing side means something DID go visibly wrong
    # in this run alone, and blaming the grader over it is not supported
    # (a flag the passing run also raises is shared behaviour and blocks
    # nothing — red-team finding: the coverage branch used to skip this
    # gate, so an agent acting on an invented entity while reciting the
    # expected sentence led grader_or_label)
    gap = _side(report, side).get("gap") or {}
    other_gap = _side(report, other).get("gap") or {}
    exclusive_flags = (set(gap.get("raised") or [])
                       - set(other_gap.get("raised") or []))
    # grounding inventions live outside gap.raised but are the same kind
    # of evidence: an argument value with no source, invented by this run
    # alone, is something that visibly went wrong in this run alone
    # (scaled-corpus finding: an agent that wrote to an invented entity
    # while reciting the expected sentence led grader_or_label at 1.0,
    # because this gate only read the gap flags).  Exclusivity is decided
    # per INVENTION, not per flag bit: a literal keyword both runs pass
    # unsourced is shared noise, and must not mask an entity only the
    # failing run made up.
    ground = _side(report, side).get("grounding") or {}
    other_ground = _side(report, other).get("grounding") or {}
    if _invention_keys(ground) - _invention_keys(other_ground):
        exclusive_flags = exclusive_flags | {"invented_arguments"}
    # negation guard: coverage is one-sided token containment, so
    # "BK1234 is NOT refundable" scores a perfect match against
    # "BK1234 is refundable" — the polarity mismatch IS the potential
    # contradiction, and word overlap cannot vouch across it
    expected_text = ae.get("expected") or ""
    answer_text = (traj.steps[-1].output if traj.steps else "") or ""
    negator_mismatch = (_negator_count(answer_text)
                        != _negator_count(expected_text))
    # "match" from the coverage metric is only grader-suspect evidence when
    # the coverage is near-total: an answer containing 70% of the expected
    # words can still contradict it outright (the missing 30% IS the
    # contradiction), so partial coverage earns nothing here.
    if (not asserts_wrong_value and not exclusive_flags
            and not negator_mismatch
            and verdict == "match" and coverage is not None
            and float(coverage) >= GRADER_COVERAGE_FLOOR):
        score += 0.5 + 0.4 * float(coverage)
        answer_evidence = True
        supports.append(led.metric(
            f"answer_eval.{side}_vs_expected.coverage", coverage,
            "answer matches the expected answer", "measured"))
        answer_step = len(traj.steps) - 1
        quote = traj.steps[answer_step].output[:120]
        if quote:
            supports.append(led.span(
                side, answer_step, "output", quote,
                "the emitted answer, for hand re-grading", "measured"))
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
    if (not asserts_wrong_value and not exclusive_flags
            and not negator_mismatch):
        answer_idx = len(traj.steps) - 1
        for i, claim in enumerate(
                (report.get("semantic") or {}).get("claims", [])):
            if (claim.get("matches_expected") is True
                    and answer_idx in (claim.get(f"{side}_steps") or [])):
                # weighted on par with near-total lexical coverage (~0.9
                # with the clean bonus): typed equality reads the value
                # that coverage cannot, and is no weaker evidence
                score += 0.75
                answer_evidence = True
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
        # a clean process alone says nothing about the LABEL: without
        # evidence from the answer itself, this hypothesis may rank but
        # must never lead (red-team finding: a sole clean-gap grader
        # hypothesis led by default when no rival was generated)
        "answer_evidence": answer_evidence,
    }]


def _gen_harness(report: dict, side: str, traj: Trajectory, led: Ledger) -> list[dict]:
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


def _gen_environment(report: dict, side: str, traj: Trajectory, led: Ledger) -> list[dict]:
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
    # Grounding dock (red-team finding: an agent-invented argument that a
    # tool correctly rejects LOOKS environmental — error=true, abandoned —
    # and the replay discriminator would then confirm the wrong story,
    # because a garbage-argument call errors deterministically).  If the
    # failing call carries an argument with no source in the trace, the
    # error is at least as likely the agent's garbage in as the
    # environment's fault: dock the score below the process hypothesis
    # that names the invention, and flip the discriminator.
    ground = _side(report, side).get("grounding") or {}
    ungrounded_error = any(
        isinstance(record, dict) and record.get("index") == first_error
        for record in ground.get("invented_arguments", []))
    contradicts: list = []
    extra: dict = {}
    if ungrounded_error:
        score = min(score, 0.25)
        statement += (
            " — but the failing call's arguments have no source in the "
            "trace, so the error may be the agent's own garbage in, not "
            "the environment's fault")
        contradicts.append(led.metric(
            f"process.{side}.grounding.arguments_without_source",
            ground.get("arguments_without_source"),
            "the failing call used argument values with no source",
            "measured"))
        extra["discriminator_override"] = (
            "check the argument's provenance FIRST: a garbage-argument "
            "call errors deterministically, so replaying it cannot "
            "exonerate the agent — only a grounded call failing the same "
            "way would implicate the environment")
        extra["grounding_docked"] = True
    return [{
        "kind": "environment_error", "statement": statement,
        "supports": supports, "contradicts": contradicts, "score": score,
        "status": None, "error_step": first_error,
        "abandoned": bool(abandoned), **extra,
    }]


def _gen_wrong_fact(report: dict, side: str, traj: Trajectory, led: Ledger) -> list[dict]:
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


def _gen_divergence(report: dict, side: str, traj: Trajectory, led: Ledger) -> list[dict]:
    attribution = report.get("attribution") or {}
    root = attribution.get("root_cause_step")
    divergences = report.get("divergences") or []
    if root is None or not divergences:
        return []
    # The twin rule (the exclusivity principle, third application): a step
    # the other run also took verbatim cannot be the decisive decision —
    # only which COPY of it the aligner matched differs, and that is an
    # alignment artefact, not a divergence.  One exception, from the red
    # team's causal-duplicate inversion (a byte-identical double charge):
    # a WRITE-effect step whose signature the failing run performed MORE
    # times than the other run is never excused — repeating a write
    # changes external state however identical the text, so the extra
    # copies ARE the divergence.  Extra READ copies stay excusable: a
    # repeated read is the non-causal pathology the rule was built for.
    # Advance to the first non-excusable step, stopping before the answer
    # so a degenerate case keeps a bounded anchor.
    from collections import Counter

    other = "b" if side == "a" else "a"
    other_sig_counts = Counter(
        (s.get("type"), s.get("name"), s.get("input"))
        for s in (report.get(other) or {}).get("steps", []))
    own_sig_counts = Counter((s.type, s.name, s.input) for s in traj.steps)

    def _excusable_twin(idx: int) -> bool:
        step = traj.steps[idx]
        sig = (step.type, step.name, step.input)
        if sig not in other_sig_counts:
            return False
        if (step.effect == "write"
                and own_sig_counts[sig] > other_sig_counts[sig]):
            return False
        return True

    while root < len(traj.steps) - 1 and _excusable_twin(root):
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


def _gen_process(report: dict, side: str, traj: Trajectory, led: Ledger,
                 failed: bool) -> list[dict]:
    gap = _side(report, side).get("gap") or {}
    raised = gap.get("raised", []) or []
    out = []
    side_report = _side(report, side)
    other = "b" if side == "a" else "a"
    other_raised = set(
        (_side(report, other).get("gap") or {}).get("raised") or [])
    for flag in raised:
        if flag == "budget_pressure":
            continue  # its own generator
        supports = []
        records = _flag_steps(side_report, flag)
        if flag == "invented_arguments":
            # evidence the exclusive inventions, not the shared noise: a
            # literal keyword both runs pass unsourced must not take the
            # anchor from the entity only this run made up
            other_keys = _invention_keys(
                _side(report, other).get("grounding") or {})
            exclusive = [r for r in records if isinstance(r, dict)
                         and (r.get("name"), r.get("argument"),
                              r.get("value")) not in other_keys]
            records = exclusive or records
        for record in records[:3]:
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
        statement = (
            f"the agent {_FLAG_STATEMENTS.get(flag, flag)}"
            + ("" if failed else
               " — it passed anyway, so the outcome hides this"))
        contradicts = []
        # exclusivity: a flag the OTHER run also raises is shared
        # behaviour, and shared behaviour cannot explain a one-sided
        # outcome (scaled-corpus finding: both runs called an undeclared
        # search tool, and undeclared_tools led the diagnosis of the one
        # run that failed).  invented_arguments is compared per invention,
        # not per flag bit — see _invention_keys.
        shared = flag in other_raised
        if shared and flag == "invented_arguments":
            shared = not (
                _invention_keys(side_report.get("grounding") or {})
                - _invention_keys(
                    _side(report, other).get("grounding") or {}))
        if shared:
            base = min(base, 0.2)
            statement += (
                " — but the other run raises the same flag, and shared "
                "behaviour cannot explain a one-sided outcome")
            contradicts.append(led.metric(
                f"process.{other}.gap.flags.{flag}", True,
                "the other run raises the same flag", "measured"))
        out.append({
            "kind": "process_pathology", "flag": flag,
            "statement": statement,
            "supports": supports, "contradicts": contradicts,
            "score": base, "status": None,
        })
    return out


def _gen_budget(report: dict, side: str, traj: Trajectory, led: Ledger) -> list[dict]:
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


def _inconsequential_span(traj: Trajectory, start: int, end: int) -> bool:
    """True when every step in ``[start, end)`` carried nothing forward:
    no write effect, no typed value or number in its output, and no
    measurable word overlap with any later step or the answer.  Such a
    step could be corrected without changing anything downstream, so by
    the counterfactual criterion it cannot be the decisive one."""
    import re
    from .align import jaccard
    from .semantic import extract_from_text
    steps = traj.steps
    if not (0 <= start < end <= len(steps) - 1):
        return False
    answer_text = steps[-1].output or steps[-1].input or ""
    for i in range(start, end):
        step = steps[i]
        if step.effect == "write":
            return False
        out = step.output or ""
        # a step that produced any typed value or bare number is treated
        # as consequential even when no later step repeats it verbatim:
        # values travel under other surface forms (11:45 → 11h45m), and
        # the rule must never move the anchor off a wrong calculation on
        # the strength of a normaliser's blind spot.  Only genuinely
        # value-free steps — a remark, a status line, a read that
        # returned nothing typed — can be inconsequential
        if extract_from_text(out) or re.search(r"\d", out):
            return False
        if out.strip() and (jaccard(out, answer_text) >= 0.2 or any(
                jaccard(out, s.input) >= 0.2 for s in steps[end:])):
            return False
    return True


def _fuse(raw: list[dict], report: dict, ledger: list[dict],
          traj_a: Optional[Trajectory] = None,
          traj_b: Optional[Trajectory] = None) -> None:
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
        div_traj = traj_a if div["agent"] == "a" else traj_b
        if (origin is not None and root is not None and origin > root
                and div_traj is not None
                and _inconsequential_span(div_traj, root, origin)):
            # the counterfactual criterion, applied honestly: a divergence
            # whose every step before the wrong fact's entry carried no
            # value forward, fed nothing and changed no state would not
            # have flipped the outcome if corrected — the wrong fact's
            # entry is the earliest step that would.  Re-anchor there
            # (scaled-corpus finding: two benign novel reads before the
            # real cause pulled the anchor two steps early)
            div["statement"] += (
                f" — the steps from {root} to {origin - 1} carried nothing "
                f"forward, so the anchor moves to step {origin}, where the "
                f"wrong fact entered")
            div["root"] = origin
            root = origin
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
        # the timing boosts say WHICH anomaly came first, not WHOSE fault
        # the error is: when the grounding dock fired, the cap it set must
        # survive fusion, or the boosts silently undo the dock's stated
        # ordering ("below the process hypothesis that names the
        # invention")
        if env.get("grounding_docked"):
            env["score"] = min(env["score"], 0.25)
    if div is not None:
        chain = set((report.get("attribution") or {}).get("chain") or [])
        for h in raw:
            if h["kind"] != "process_pathology" or h["agent"] != div["agent"]:
                continue
            if h.get("status") == "merged":
                continue
            # a flag corroborates the divergence account when its evidence
            # spans sit on chain steps — or on the divergent decision
            # itself: a flag raised BY the root step is the same anomaly
            # seen twice, not a competitor (scaled-corpus finding: four
            # flags all describing one duplicated write contested each
            # other while the divergence they corroborated sat at 0.25)
            h_steps = _hypothesis_span_steps(h, ledger)
            root = div.get("root")
            on_root = root is not None and root in h_steps
            # a duplicated decision is ONE anomaly with several indices:
            # when a flag's evidence sits on a step whose (type, name,
            # input) signature equals the root step's, it describes the
            # same repeated decision, not a different one
            div_traj = traj_a if div["agent"] == "a" else traj_b
            if (not on_root and root is not None and div_traj is not None
                    and 0 <= root < len(div_traj.steps)):
                root_step = div_traj.steps[root]
                root_sig = (root_step.type, root_step.name, root_step.input)
                on_root = any(
                    0 <= i < len(div_traj.steps)
                    and (div_traj.steps[i].type, div_traj.steps[i].name,
                         div_traj.steps[i].input) == root_sig
                    for i in h_steps)
            if h_steps and (h_steps & chain or on_root):
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
        eligible = not (top["kind"] == "grader_or_label"
                        and not top.get("answer_evidence", True))
        if eligible and top["score"] >= PLAUSIBLE_FLOOR and (
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


def _anchor_step(leading: dict, led: Ledger) -> Optional[int]:
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
                    traj: Optional[Trajectory], led: Ledger) -> list[dict]:
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


def _class_mix_phrase(mix: dict) -> str:
    """``on observable evidence (4 items; 1 annotation)`` — what the
    leading account rests on, so a reader knows whether to trust the
    trace or go and look."""
    observable = mix.get("observable", 0)
    annotation = mix.get("annotation", 0)
    stated = mix.get("stated", 0)
    if observable:
        extra = [f"{annotation} annotation" for _ in [0] if annotation] + \
                [f"{stated} stated" for _ in [0] if stated]
        return (f"on observable evidence ({observable} item(s)"
                + (f"; {', '.join(extra)}" if extra else "") + ")")
    if annotation and not stated:
        return "on annotations only — someone's judgement, verify by hand"
    if stated and not annotation:
        return "on stated reasoning only — what the agent said, not what it did"
    if annotation or stated:
        return "on annotations and stated reasoning only, nothing observable"
    return "with no cited evidence"


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
        text += "; " + _class_mix_phrase(top.get("evidence_classes") or {})
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


def _confidence(mode: str, leading_id: Optional[str], ranked: list[dict],
                verified: str = "hypothesized") -> dict:
    from .confidence import confidence
    plausible_others = [
        h["kind"] for h in ranked
        if h["status"] == "plausible" and h["id"] != leading_id]
    if leading_id is None:
        return confidence("low", 1,
                          "no hypothesis clears the runner-up by the lead "
                          "margin; single pair, alternatives not ruled out",
                          "n/a")
    if plausible_others:
        return confidence("medium", 1,
                          "single pair (n=1); plausible alternatives remain: "
                          + ", ".join(plausible_others), verified)
    return confidence("medium", 1,
                      "single pair (n=1); the ranking is consistent but one "
                      "comparison cannot rule out task-specific luck", verified)


#: the counterfactual criterion the field converged on (Who&When): the
#: decisive step is the EARLIEST step whose correction would turn the
#: failure into success.
DECISIVE_CRITERION = (
    "earliest step whose correction is expected to flip the outcome")


#: what a committed step IS, stated on every diagnosis: a counterfactual
#: claim read from trace evidence, which only re-execution can verify
#: (AgenTracer, Causal Agent Replay).  The harness's replay hook turns
#: this label into "replay-verified" or "replay-refuted"; the engine
#: itself never claims more than "hypothesized".
DECISIVE_VERIFICATION = "hypothesized"

#: the one overdetermination signature a trace can show without a replay
#: (Thought Anchors' warning, applied narrowly): the run OBSERVED a wrong
#: value and then ASSERTED a different wrong value of the same kind that
#: reached the answer.  Correcting the observation leaves the assertion;
#: correcting the assertion leaves the wrong observation — each fault is
#: sufficient on its own.  Anything looser (a visible pathology at another
#: step) is a distractor as often as a cause, and the engine says nothing.
OVERDETERMINED_NOTE = (
    "two wrong values of the same kind, each exclusive to this run: the "
    "observation returned one and the run asserted another that reached the "
    "answer; correcting either alone leaves the other, so neither step is "
    "sufficient by itself — replay each alone before treating either as the cause")

_CORRECTION_HINTS = {
    "divergence": "take the decision the passing run took at this step",
    "wrong_fact_propagation": "replace the wrong value with the sourced one",
    "environment_error": "make the call succeed (or ground its arguments)",
    "process_pathology": "remove the flagged behaviour at this step",
}


def _decisive_step(mode: str, leading: Optional[dict], led: Ledger,
                   ranked: Optional[list] = None,
                   account: Optional[list] = None,
                   traj: Optional[Trajectory] = None,
                   side: Optional[str] = None) -> dict:
    """Commit to a decisive error step — or abstain with the reason — and
    say how much of the trace the commitment really covers.

    Attributors that always name a step score points by luck on causes
    that have no step: a grader mislabel and a harness kill contain no
    agent mistake to correct, so the honest answer there is None with the
    reason stated, and an eval should score that abstention as an answer
    in its own right.  Anchors per kind follow the fusion rules, which
    already guarantee the leading hypothesis holds the *earliest*
    evidenced anomaly (a wrong fact or error predating the divergence
    re-anchors the lead before this function ever runs).

    The earliest flip is not the whole story (AgentRx, DRIFT, CAR): agents
    recover from early wobbles, and a wrong commitment is often still
    correctable several steps later.  So the commitment carries a
    **window** — ``step`` (earliest evidenced anomaly) to
    ``point_of_no_return`` (the last causal-account step before the
    answer at which a correction would still have flipped the outcome,
    on this account's own evidence) — plus a ``verification`` label
    that says the claim is hypothesized until replayed, and a
    machine-readable ``replay_recipe`` any harness can execute.  When
    the diagnosis is contested, the anchored contenders are listed as
    ``joint_candidates`` instead of being silently dropped: no single
    step is committed, and the reader sees which steps are in play.
    """
    base = {"step": None, "criterion": DECISIVE_CRITERION, "basis": None,
            "reason": None, "point_of_no_return": None, "window": None,
            "verification": None, "replay_recipe": None,
            "joint_candidates": [], "overdetermined": None}
    if mode != "single_failure":
        return dict(base, reason="no failure to localize")
    if leading is None:
        candidates = []
        for h in ranked or []:
            if h.get("status") != "plausible":
                continue
            anchor = _anchor_step(h, led)
            if anchor is not None:
                candidates.append({"kind": h.get("kind"),
                                   "flag": h.get("flag"), "step": anchor,
                                   "score": h.get("score")})
        return dict(base,
                    reason="contested: no hypothesis leads, so no step is "
                           "committed" + (" — the anchored contenders are "
                                          "listed as joint candidates"
                                          if candidates else ""),
                    joint_candidates=candidates)
    kind = leading.get("kind")
    if kind == "grader_or_label":
        return dict(base, reason="no agent error to correct — the correction "
                                 "is to the grader or label, not to a step")
    if kind == "harness_termination":
        return dict(base, reason="the harness ended the run; no corrected "
                                 "agent step would have prevented the kill")
    if kind == "budget_pressure":
        return dict(base, reason="the binding constraint is the step budget, "
                                 "a harness setting, not an agent step")
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
        return dict(base, reason=f"{kind} leads but anchors to no specific step")

    # the window: the last on-account step before the answer is the last
    # point at which, on the account's own propagation evidence, a
    # correction still reaches the outcome
    answer_idx = (len(traj.steps) - 1) if traj is not None else None
    on_account = sorted(
        e["step"] for e in (account or [])
        if e.get("step") is not None and e["step"] >= step
        and (answer_idx is None or e["step"] < answer_idx))
    point_of_no_return = on_account[-1] if on_account else step
    recipe = {
        "side": side, "step": step,
        "correction": _CORRECTION_HINTS.get(kind, "correct this step"),
        "expects": "the outcome flips to success",
        "replays": "≥3 — agent policies are stochastic; one rollout proves nothing",
    }
    return dict(base, step=step, basis=basis,
                point_of_no_return=point_of_no_return,
                window={"earliest": step, "point_of_no_return": point_of_no_return,
                        "steps": point_of_no_return - step + 1},
                verification=DECISIVE_VERIFICATION, replay_recipe=recipe)


# ---------------------------------------------------------------------------
# public API


class HypothesisGenerator:
    """One kind of explanation: a function ``(report, side, traj, ledger,
    failed) -> list[hypothesis]`` and the conditions under which it runs.
    Adding a kind is adding one entry to :data:`HYPOTHESIS_GENERATORS`."""

    def __init__(self, name: str, fn, *, failed_only: bool = True,
                 single_failure_only: bool = False) -> None:
        self.name = name
        self.fn = fn
        self.failed_only = failed_only
        self.single_failure_only = single_failure_only

    def applies(self, mode: str, failed: bool) -> bool:
        if self.failed_only and not failed:
            return False
        if self.single_failure_only and mode != "single_failure":
            return False
        return True


def _adapt(fn):
    """Wrap the four-argument generators so every entry has one signature."""
    def run(report, side, traj, led, failed):
        return fn(report, side, traj, led)
    run.__name__ = fn.__name__
    return run


#: the registry, in the order the ledger first cites their evidence; the
#: process generator runs for passers too (a clean outcome can hide a
#: pathological process)
HYPOTHESIS_GENERATORS = [
    HypothesisGenerator("grader_or_label", _adapt(_gen_grader)),
    HypothesisGenerator("harness_termination", _adapt(_gen_harness)),
    HypothesisGenerator("environment_error", _adapt(_gen_environment)),
    HypothesisGenerator("wrong_fact_propagation", _adapt(_gen_wrong_fact)),
    HypothesisGenerator("divergence", _adapt(_gen_divergence),
                        single_failure_only=True),
    HypothesisGenerator("budget_pressure", _adapt(_gen_budget)),
    HypothesisGenerator("process_pathology", _gen_process, failed_only=False),
]


def _two_wrong_values(report: dict, side: str, traj: Trajectory,
                      step: int) -> Optional[dict]:
    """The overdetermination signature: an observed wrong value at or after
    the decisive step, and a *different* wrong value of the same kind,
    exclusive to this run, asserted later in a plan/reason/answer step and
    carried into the answer.  Returns the guard object or ``None``."""
    claims = (report.get("semantic") or {}).get("claims", []) or []
    other = "b" if side == "a" else "a"
    answer_idx = len(traj.steps) - 1
    wrong: list = []
    for i, claim in enumerate(claims):
        if claim.get("matches_expected") is not False:
            continue
        if not claim.get(f"{side}_steps") or claim.get(f"{other}_steps"):
            continue
        origin = claim.get("origin") or {}
        if origin.get("agent") != side or origin.get("step") is None:
            continue
        idx = origin["step"]
        if idx < step or idx >= len(traj.steps):
            continue
        wrong.append({"index": i, "kind": claim.get("kind"),
                      "value": claim.get("value"), "norm": claim.get("normalized"),
                      "origin": idx, "in_answer": answer_idx in claim[f"{side}_steps"],
                      "stated": traj.steps[idx].type in ("plan", "reason", "answer")})
    for observed in wrong:
        if observed["stated"] or observed["in_answer"]:
            continue
        for asserted in wrong:
            if (asserted is observed or asserted["kind"] != observed["kind"]
                    or asserted["norm"] == observed["norm"]
                    or asserted["origin"] <= observed["origin"]
                    or not asserted["stated"] or not asserted["in_answer"]):
                continue
            return {
                "status": "possible",
                "candidates": [
                    {"kind": "wrong_fact_propagation", "step": observed["origin"],
                     "value": observed["value"], "role": "observed wrong value"},
                    {"kind": "wrong_fact_propagation", "step": asserted["origin"],
                     "value": asserted["value"], "role": "asserted wrong value, in the answer"},
                ],
                "note": OVERDETERMINED_NOTE,
                "replay_recipe": [
                    {"side": side, "step": observed["origin"],
                     "correction": "replace the observed value with the sourced one",
                     "expects": "the answer still carries the asserted value — no flip"},
                    {"side": side, "step": asserted["origin"],
                     "correction": "answer from the observation, not from memory",
                     "expects": "the answer carries the observed wrong value — no flip"},
                ],
            }
    return None


def diagnose(report: dict, a: Trajectory, b: Trajectory) -> dict:
    """Build the SCHEMA.md ``diagnosis`` object for a compared pair."""
    success_a = a.outcome.success
    success_b = b.outcome.success
    led = Ledger()

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
        for generator in HYPOTHESIS_GENERATORS:
            if not generator.applies(mode, failed):
                continue
            generated += generator.fn(report, side, traj, led, failed)
        for h in generated:
            h["agent"] = side
        raw += generated

    if mode == "single_failure":
        _fuse(raw, report, led.items, a, b)

    # every evidence item knows its class; every hypothesis counts its mix
    led.classify(a, b)
    for h in raw:
        h["evidence_classes"] = led.classes_of(h.get("supports") or [])

    # rank: scored first (descending); at equal score a hypothesis with
    # observable support outranks one resting on annotations or stated
    # reasoning alone; then the KINDS order and statement text, so the
    # ordering is deterministic.
    def _key(h: dict):
        score = h["score"] if h["score"] is not None else -1.0
        observable = (h.get("evidence_classes") or {}).get("observable", 0)
        return (-score, 0 if observable else 1,
                KINDS.index(h["kind"]) if h["kind"] in KINDS else 99,
                h["statement"])

    ranked = sorted(raw, key=_key)
    for i, h in enumerate(ranked):
        h["id"] = f"H{i + 1}"
        if h["score"] is not None:
            h["score"] = round(max(0.0, min(1.0, h["score"])), 4)
        h["discriminator"] = h.pop("discriminator_override", None) \
            or _DISCRIMINATORS.get(h["kind"], "")
        h.pop("grounding_docked", None)

    leading_id, margin = _assign_status(ranked)
    leading = next((h for h in ranked if h["id"] == leading_id), None)

    account_side = leading["agent"] if leading else subject
    account_traj = (a if account_side == "a" else b) if account_side else None
    account = _causal_account(
        report, leading, account_side, account_traj, led
    ) if mode == "single_failure" else []

    decisive = _decisive_step(
        mode, leading, led, ranked, account,
        (a if account_side == "a" else b) if account_side else None,
        account_side)

    if decisive.get("step") is not None and account_side:
        over = _two_wrong_values(report, account_side,
                                 a if account_side == "a" else b, decisive["step"])
        if over:
            decisive["overdetermined"] = over
            decisive["joint_candidates"] = list(over["candidates"])

    # evidence added while building the account or the decisive step is
    # classified too (idempotent: items already stamped are skipped)
    led.classify(a, b)

    verdict = _verdict_text(mode, subject_name, ranked, leading_id, margin)
    if decisive.get("overdetermined"):
        cands = decisive["overdetermined"]["candidates"]
        verdict += ("; possibly overdetermined — "
                    + " and ".join(f"{c['role']} at step {c['step']} ({c['value']})"
                                   for c in cands)
                    + ": correcting either alone leaves the other")
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
        "decisive_step": decisive,
        "contradictions": _contradictions(
            report, subject if mode == "single_failure" else None),
        "confidence": _confidence(mode, leading_id, ranked,
                                  "hypothesized" if decisive.get("step") is not None else "n/a")
        if ranked else {"level": None, "n": None, "basis": "nothing to adjudicate",
                        "verified": "n/a"},
    }


def check_diagnosis(diagnosis: dict, report: dict,
                    a: Trajectory, b: Trajectory) -> list[str]:
    """Verify every evidence item against the trajectories and the report.

    Span evidence must quote a substring of the cited step field; metric
    evidence must resolve to the recorded value; hypothesis and account
    references must point at ledger entries that exist; and the
    adjudication's own bookkeeping must be coherent (statuses in
    vocabulary, scores in [0, 1], ``leading`` naming a hypothesis marked
    leading).  Returns a list of violations — the same contract
    ``check_narration`` applies to narration text.

    The boundary matters: an empty list means the diagnosis is fully
    GROUNDED — every quote is really in the trace, every number is really
    in the report — not that its causal story is TRUE.  A hypothesis can
    cite real evidence and still draw the wrong conclusion from it;
    whether the story holds is what the benchmark corpora and the
    per-hypothesis discriminators are for.
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
    # structural sanity: the adjudication's own bookkeeping
    statuses = {"leading", "plausible", "ruled_out", "merged", "untestable"}
    hypotheses = diagnosis.get("hypotheses", [])
    for h in hypotheses:
        if h.get("status") not in statuses:
            problems.append(
                f"{h.get('id')}: status {h.get('status')!r} not in the "
                f"vocabulary")
        score = h.get("score")
        if score is not None and not 0.0 <= score <= 1.0:
            problems.append(f"{h.get('id')}: score {score!r} outside [0, 1]")
    leading = diagnosis.get("leading")
    if leading is not None:
        lead = next((h for h in hypotheses if h.get("id") == leading), None)
        if lead is None:
            problems.append(f"leading {leading!r} names no hypothesis")
        elif lead.get("status") != "leading":
            problems.append(
                f"leading {leading!r} has status {lead.get('status')!r}, "
                f"not 'leading'")
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
