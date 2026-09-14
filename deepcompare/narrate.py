"""LLM narration of a comparison — strictly segregated, checked for faithfulness (v25).

Everything else in AgentDiff is deterministic, and that is its spine: the
numbers are reproducible, offline, and free of judge variance.  Narration is
the one place a language model genuinely helps — turning forty report fields
into three paragraphs a teammate will actually read — and the one place a
language model can quietly wreck the property everything else protects, by
paraphrasing a number into a different number or asserting a cause the
evidence does not carry.

So the contract is built to make the model powerless over the findings:

* **The engine never calls a model.**  It emits a *brief* — the numbered
  facts a narrator may use, every one copied verbatim from report fields —
  and a prompt wrapping that brief.  The caller runs whatever model they
  like, outside this package, and hands the text back.
* **Narration is checked against the brief, number by number.**  Every
  numeric token in the returned text must appear among the brief's facts (in
  any common formatting); every ``[F7]``-style citation must name a real
  fact.  What fails the check is not silently accepted and not silently
  dropped: the narration is stored with its violations attached, so a UI can
  render the unsupported sentence with a warning rather than with authority.
* **Narration can never change an outcome.**  It is stored under its own
  key, no analysis reads it, and the gate's exit code is computed before it
  exists.  Deleting every narration from a report changes nothing but prose.

This is the shape the evidence recommends.  Judged *scores* over frozen
steps flip sign across evaluator channels (arXiv 2607.04419), which is why
AgentDiff refuses LLM scoring; narration sidesteps that failure mode
entirely when the numbers are pinned and checked, because the model is left
with only the part it is good at — fluency — and stripped of the part it is
bad at: arithmetic authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, Optional

#: fact ids look like F1, F2, ... — chosen to be citable inline as [F3].
_FACT_ID = re.compile(r"\[F(\d+)\]")

#: numeric tokens in narration text.  Percentages, decimals, integers,
#: currency; commas allowed.  Years and step/fact indices are handled by the
#: allowlist below rather than by weakening this pattern.
_NUMBER = re.compile(r"(?<![\w/])\$?\d[\d,]*\.?\d*%?")


def _norm_number(token: str) -> str:
    """Canonical form of a numeric token for matching: strip $ , % and
    trailing zeros, so "1,210", "$1210" and "1210.0" all agree."""
    text = token.strip().lstrip("$").rstrip("%").replace(",", "")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _walk_numbers(value, out: set) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(_norm_number(f"{value}"))
        # Percentages are read off ratios constantly; admit both scales for
        # anything that could be one.  A narrator saying "62%" for 0.625 is
        # right, and rejecting it would teach people to ignore the checker.
        if isinstance(value, float) and 0 <= value <= 1:
            out.add(_norm_number(f"{value * 100:.4f}"))
            out.add(_norm_number(f"{round(value * 100)}"))
        if isinstance(value, float):
            out.add(_norm_number(f"{round(value, 2)}"))
            out.add(_norm_number(f"{round(value, 1)}"))
            out.add(_norm_number(f"{round(value)}"))
    elif isinstance(value, str):
        for tok in _NUMBER.findall(value):
            out.add(_norm_number(tok))
    elif isinstance(value, dict):
        for v in value.values():
            _walk_numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk_numbers(v, out)


def _fact(facts: list, source: str, text: str, value=None) -> None:
    facts.append({"id": f"F{len(facts) + 1}", "source": source,
                  "text": text, "value": value})


def _detect_shape(report: dict) -> str:
    """Which kind of result this is: a pairwise report, a batch aggregate,
    or an experiments comparison.  The eval-agent role needs all three — a
    narrator confined to single pairs cannot analyse a fleet."""
    if "experiments" in report and "diffs" in report:
        return "experiments"
    if "action_counts" in report and "success_by_agent" in report:
        return "progress"
    if "a" in report and "b" in report and "alignment" in report:
        return "pair"
    if "success_rate" in report or "triage" in report or "reports" in report:
        return "aggregate"
    return "pair"


def narration_brief(report: dict) -> dict:
    """The numbered facts a narrator may use — nothing else.

    Assembled only from fields the deterministic engine wrote.  The brief is
    the entire authority the narrator gets: a fact that is not here is a
    fact the narration is not entitled to state.  Accepts a pairwise report,
    a batch aggregate, or an experiments comparison; the facts differ, the
    covenant does not.
    """
    shape = _detect_shape(report)
    if shape == "aggregate":
        return _aggregate_brief(report)
    if shape == "experiments":
        return _experiments_brief(report)
    if shape == "progress":
        return _progress_brief(report)
    facts: list[dict] = []
    a = (report.get("a") or {})
    b = (report.get("b") or {})
    name_a = ((a.get("agent") or {}).get("name")) or "A"
    name_b = ((b.get("agent") or {}).get("name")) or "B"

    for side, name in ((a, name_a), (b, name_b)):
        outcome = side.get("outcome") or {}
        totals = side.get("totals") or {}
        _fact(facts, "outcome",
              f"{name} {'succeeded' if outcome.get('success') else 'failed'}; "
              f"answer: {str(outcome.get('answer', ''))[:200]}",
              outcome.get("success"))
        _fact(facts, "totals",
              f"{name}: {len(side.get('steps') or [])} steps, "
              f"{totals.get('input_tokens', 0)}+{totals.get('output_tokens', 0)} tokens, "
              f"${totals.get('cost_usd', 0)}, {totals.get('latency_s', 0)}s",
              totals)

    for divergence in (report.get("divergences") or [])[:5]:
        _fact(facts, "divergence",
              f"divergence #{divergence.get('rank')}: {divergence.get('kind')} — "
              f"{divergence.get('summary')}", divergence.get("downstream"))

    attribution = report.get("attribution") or {}
    if attribution.get("failed_agent"):
        _fact(facts, "attribution", attribution.get("explanation", ""),
              {"root_cause_step": attribution.get("root_cause_step"),
               "chain": attribution.get("chain")})

    for key in ("success_analysis", "counterfactual", "shapley", "uncertainty",
                "semantic"):
        block = report.get(key) or {}
        if isinstance(block, dict) and block.get("narrative"):
            _fact(facts, key, block["narrative"], None)

    process = report.get("process") or {}
    for side_key, name in (("a", name_a), ("b", name_b)):
        gap = ((process.get(side_key) or {}).get("gap") or {})
        if gap.get("narrative"):
            _fact(facts, f"process.{side_key}", gap["narrative"], gap.get("raised"))
    if process.get("narrative"):
        _fact(facts, "process", process["narrative"], None)

    delta = report.get("metrics_delta") or {}
    if delta:
        _fact(facts, "metrics_delta", json.dumps(delta, sort_keys=True), delta)

    diagnosis = report.get("diagnosis") or {}
    if diagnosis:
        # Every diagnosis field is quoted verbatim — the narrator inherits
        # the adjudication, it does not get to re-adjudicate.  Scores and
        # margins ride along as fact values so _collect_allowed admits them.
        if diagnosis.get("verdict"):
            _fact(facts, "diagnosis", diagnosis["verdict"],
                  {"margin": diagnosis.get("margin")})
        hypotheses = [h for h in (diagnosis.get("hypotheses") or [])
                      if h.get("status") != "merged"]
        for h in hypotheses[:4]:
            kind = h.get("kind", "")
            if h.get("flag"):
                kind += f" ({h['flag']})"
            _fact(facts, "diagnosis.hypothesis",
                  f"{h.get('id')} [{h.get('status')}] {kind}, score "
                  f"{h.get('score')}: {h.get('statement')}", h.get("score"))
        for contradiction in diagnosis.get("contradictions") or []:
            _fact(facts, "diagnosis.contradiction", contradiction, None)
        leading = next((h for h in (diagnosis.get("hypotheses") or [])
                        if h.get("id") == diagnosis.get("leading")), None)
        if leading is not None and leading.get("discriminator"):
            _fact(facts, "diagnosis.discriminator",
                  f"to settle it: {leading['discriminator']}", None)
        elif diagnosis.get("leading") is None and hypotheses:
            _fact(facts, "diagnosis.discriminator",
                  "the diagnosis is contested: no single hypothesis clears "
                  "the runner-up by the lead margin; see the discriminating "
                  "checks on each hypothesis", None)
        decisive = diagnosis.get("decisive_step") or {}
        if decisive.get("step") is not None:
            _fact(facts, "diagnosis.decisive_step",
                  f"decisive step {decisive['step']} "
                  f"({decisive.get('criterion')}): {decisive.get('basis')}",
                  {"step": decisive["step"]})
        elif decisive.get("reason"):
            _fact(facts, "diagnosis.decisive_step",
                  f"no decisive step committed: {decisive['reason']}", None)
        for entry in diagnosis.get("causal_account") or []:
            text = f"account step {entry.get('step')}: {entry.get('happened')}"
            if entry.get("mechanism"):
                text += f" — {entry['mechanism']}"
            _fact(facts, "diagnosis.account", text,
                  {"step": entry.get("step")})
        confidence = diagnosis.get("confidence") or {}
        if confidence.get("level"):
            _fact(facts, "diagnosis.confidence",
                  f"confidence {confidence['level']}: "
                  f"{confidence.get('basis')}", None)
        decisive_verification = decisive.get("verification")
        if decisive_verification:
            window = decisive.get("window") or {}
            _fact(facts, "diagnosis.verification",
                  f"the decisive step is {decisive_verification} — a "
                  f"counterfactual claim from trace evidence, not "
                  f"replay-verified; the window runs from step "
                  f"{window.get('earliest')} to step "
                  f"{window.get('point_of_no_return')}",
                  {"earliest": window.get("earliest"),
                   "point_of_no_return": window.get("point_of_no_return")})

    # the reading of each run on its own — the executive layer, under the
    # same covenant: the narrator may rephrase these, never add a finding
    for side, name in (("a", name_a), ("b", name_b)):
        reading = (report.get("reading") or {}).get(side) or {}
        if not reading or reading.get("error"):
            continue
        _fact(facts, f"reading.{side}.summary",
              f"{name}: {reading.get('summary')}", None)
        why = reading.get("why_it_ended") or {}
        if why.get("verdict_basis"):
            _fact(facts, f"reading.{side}.why",
                  f"{name} ended this way because {why['verdict_basis']}", None)
        for value in reading.get("rests_on") or []:
            if value.get("first_step") is None:
                _fact(facts, f"reading.{side}.unsourced",
                      f"{name}'s answer asserts {value.get('value')} which no "
                      f"earlier step observed", None)
        for finding in reading.get("what_it_means") or []:
            _fact(facts, f"reading.{side}.finding",
                  f"{name} [{finding.get('evidence_class')}]: "
                  f"{finding.get('statement')}",
                  {"steps": finding.get("steps")})
        for action in reading.get("take_forward") or []:
            where = (f" (at step {action['at_step']})"
                     if action.get("at_step") is not None else "")
            _fact(facts, f"reading.{side}.take_forward",
                  f"{name} — instead{where}: {action.get('instead') or action.get('action')}",
                  {"at_step": action.get("at_step")})
        # the bounded evidence window: the narrator sees the steps the
        # reading actually cites — in trace order, each cut to a budget —
        # never the whole trace (long-context readers degrade monotonically
        # and lose the middle; a focused window is what rescues them)
        window = evidence_window(reading, (report.get(side) or {}).get("steps") or [])
        for entry in window["steps"]:
            _fact(facts, f"reading.{side}.window",
                  f"{name} step {entry['step']} [{entry['type']} {entry['name']}]: "
                  f"{entry['excerpt']}", {"step": entry["step"]})
        _fact(facts, f"reading.{side}.window_omitted",
              f"{name}: the evidence window shows {len(window['steps'])} of "
              f"{window['referenced']} referenced step(s); {window['omitted']} "
              f"omitted for the byte budget; {window['unreferenced']} step(s) of "
              f"the run are not cited by any finding",
              {"shown": len(window["steps"]), "referenced": window["referenced"],
               "omitted": window["omitted"], "unreferenced": window["unreferenced"]})
        # the verdict again at the end of this side's facts: primacy and
        # recency both work for the reader, so the summary bookends the window
        _fact(facts, f"reading.{side}.summary_recap",
              f"{name}: {reading.get('summary')}", None)

    steps_max = max(len((a.get("steps") or [])), len((b.get("steps") or [])))
    return {
        "task": (report.get("task") or {}).get("id"),
        "agents": {"a": name_a, "b": name_b},
        "shape": "pair",
        "facts": facts,
        "allowed_numbers": sorted(_collect_allowed(facts, extra_ints=steps_max)),
        "brief_digest": _digest_of(facts),
    }


WINDOW_STEP_BYTES = 600
WINDOW_TOTAL_BYTES = 8000


def evidence_window(reading: dict, steps: list, *, step_bytes: int = WINDOW_STEP_BYTES,
                    total_bytes: int = WINDOW_TOTAL_BYTES) -> dict:
    """The steps a reading cites, in trace order, each cut to a byte budget,
    stopping when the total budget is spent.  Returns the shown steps, how
    many the reading referenced, how many were omitted for budget, and how
    many steps of the run no finding cites at all — so the reader always
    knows what it did not see."""
    referenced: set = set()
    for finding in reading.get("what_it_means") or []:
        referenced.update(i for i in (finding.get("steps") or []) if isinstance(i, int))
    for error in reading.get("errors") or []:
        if isinstance(error.get("step"), int):
            referenced.add(error["step"])
    critical = reading.get("critical_error") or {}
    if isinstance(critical.get("step"), int):
        referenced.add(critical["step"])
    for i in (reading.get("answer_basis") or {}).get("basis_steps") or []:
        if isinstance(i, int):
            referenced.add(i)
    for action in reading.get("take_forward") or []:
        if isinstance(action.get("at_step"), int):
            referenced.add(action["at_step"])
    by_index = {}
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("index"), int):
            by_index[step["index"]] = step
    ordered = sorted(i for i in referenced if i in by_index)
    shown: list = []
    spent = 0
    omitted = 0
    for i in ordered:
        step = by_index[i]
        text = " | ".join(part for part in (
            (step.get("input") or "").strip(), (step.get("output") or "").strip()) if part)
        excerpt = text.encode("utf-8")[:step_bytes].decode("utf-8", errors="ignore")
        if len(text.encode("utf-8")) > step_bytes:
            excerpt += " …"
        cost = len(excerpt.encode("utf-8"))
        if spent + cost > total_bytes:
            omitted += 1
            continue
        spent += cost
        shown.append({"step": i, "type": step.get("type"), "name": step.get("name"),
                      "excerpt": excerpt, "bytes": cost})
    return {"steps": shown, "referenced": len(ordered), "omitted": omitted,
            "unreferenced": max(0, len(by_index) - len(ordered)),
            "bytes": spent, "budget": {"step": step_bytes, "total": total_bytes}}


def _collect_allowed(facts: list, extra_ints: int = 0) -> set:
    allowed: set = set()
    for fact in facts:
        _walk_numbers(fact["text"], allowed)
        _walk_numbers(fact["value"], allowed)
    for i in range(0, max(extra_ints, len(facts)) + 1):
        allowed.add(str(i))
    return allowed


def _digest_of(facts: list) -> str:
    return hashlib.sha256(json.dumps(
        [(f["id"], f["text"]) for f in facts], sort_keys=True,
        ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _aggregate_brief(aggregate: dict) -> dict:
    """Facts for the fleet-level eval agent: the whole batch, all agents."""
    facts: list[dict] = []
    agents = aggregate.get("agents") or {}
    rates = aggregate.get("success_rate") or {}
    for side, name in sorted(agents.items()):
        rate = rates.get(side)
        means = (aggregate.get("means") or {}).get(side) or {}
        _fact(facts, "aggregate",
              f"{name}: success rate {rate}, mean tokens "
              f"{means.get('tokens')}, mean cost ${means.get('cost_usd')}, "
              f"mean latency {means.get('latency_s')}s over "
              f"{aggregate.get('tasks')} task(s)", {"rate": rate, **means})
    for origin, share in sorted((aggregate.get("failure_origins") or {}).items()):
        _fact(facts, "failure_origins",
              f"{share:.0%} of attributed failures start in {origin}", share)
    issues = ((aggregate.get("issues") or {}).get("issues") or [])[:6]
    for issue in issues:
        _fact(facts, "issue",
              f"{issue.get('title')} — {issue.get('occurrences')} occurrence(s), "
              f"{issue.get('failures_caused')} failure(s) caused", issue)
    triage = (aggregate.get("triage") or {})
    for action in (triage.get("actions") or [])[:5]:
        _fact(facts, "triage",
              f"action #{action.get('rank')}: {action.get('action')}",
              action.get("impact"))
    variance = (aggregate.get("variance") or {})
    if variance.get("narrative"):
        _fact(facts, "variance", variance["narrative"], None)
    for metric, block in (variance.get("metrics") or {}).items():
        if isinstance(block, dict) and block.get("narrative"):
            _fact(facts, f"variance.{metric}", block["narrative"], None)
    reliability = aggregate.get("reliability") or {}
    for side_block in (reliability.get("per_agent") or {}).values():
        if isinstance(side_block, dict) and side_block.get("narrative"):
            _fact(facts, "reliability", side_block["narrative"], None)
    for key in ("calibration", "semantic_profile", "attributes"):
        block = aggregate.get(key) or {}
        if isinstance(block, dict) and block.get("narrative"):
            _fact(facts, key, block["narrative"], None)
    systemic = aggregate.get("diagnosis") or {}
    for entry in (systemic.get("by_leading_kind") or [])[:4]:
        _fact(facts, "diagnosis.systemic",
              f"{entry.get('kind')} leads the diagnosis in {entry.get('count')} "
              f"of {entry.get('of')} diagnosed failure(s) "
              f"({', '.join(entry.get('tasks') or [])})", entry)
    if systemic.get("note"):
        _fact(facts, "diagnosis.systemic", systemic["note"], None)
    consolidated = aggregate.get("diagnosis_consolidated") or {}
    for entry in (consolidated.get("per_task_agent") or []):
        verdict = entry.get("consolidated")
        if not verdict or not entry.get("failures"):
            continue
        repro = entry.get("failure_reproduction") or {}
        _fact(facts, "diagnosis.cross_run",
              f"{entry.get('agent')} on {entry.get('task')} (fails "
              f"{repro.get('k')} of {repro.get('n')} runs): "
              f"[{verdict.get('status')}] {verdict.get('statement')}",
              {"k": repro.get("k"), "n": repro.get("n")})
        for check in (entry.get("checks_run") or []):
            _fact(facts, "diagnosis.check",
                  f"executed check {check.get('check')} "
                  f"({check.get('outcome')}): {check.get('detail')}",
                  None)
        spectrum = entry.get("spectrum") or {}
        if spectrum.get("measurable") and spectrum.get("signatures"):
            top = spectrum["signatures"][0]
            step = top.get("step") or {}
            _fact(facts, "diagnosis.spectrum",
                  f"spectrum ({spectrum.get('method')}): most suspicious "
                  f"signature for {entry.get('agent')} on "
                  f"{entry.get('task')} is {step.get('name')}"
                  f"({step.get('input')!r}) — suspiciousness "
                  f"{top.get('suspiciousness')}, in {top.get('in_failing')} "
                  f"of {top.get('of_failing')} failing and "
                  f"{top.get('in_passing')} of {top.get('of_passing')} "
                  f"passing run(s)", top)
        elif spectrum.get("note"):
            _fact(facts, "diagnosis.spectrum",
                  f"spectrum for {entry.get('agent')} on "
                  f"{entry.get('task')}: {spectrum['note']}", None)
    if consolidated.get("narrative"):
        _fact(facts, "diagnosis.cross_run", consolidated["narrative"], None)

    return {
        "task": f"batch of {aggregate.get('tasks')} task(s)",
        "agents": {k: v for k, v in agents.items()},
        "shape": "aggregate",
        "facts": facts,
        "allowed_numbers": sorted(_collect_allowed(facts)),
        "brief_digest": _digest_of(facts),
    }


def _experiments_brief(result: dict) -> dict:
    """Facts for the eval agent over whole experiments."""
    facts: list[dict] = []
    for summary in result.get("experiments") or []:
        _fact(facts, "experiment",
              f"{summary.get('name')}: {summary.get('runs')} run(s), success "
              f"{summary.get('success_rate')}, mean tokens "
              f"{(summary.get('means') or {}).get('tokens')}", summary.get("means"))
    for d in result.get("diffs") or []:
        if d.get("narrative"):
            _fact(facts, "diff", d["narrative"], d.get("success_diff"))
        sim = d.get("similarity") or {}
        if sim.get("note"):
            _fact(facts, "behaviour", sim["note"],
                  {"cross": sim.get("cross"), "within": sim.get("within")})
    if result.get("narrative"):
        _fact(facts, "overall", result["narrative"], None)
    names = [s.get("name") for s in result.get("experiments") or []]
    return {
        "task": f"comparison of {len(names)} experiment(s)",
        "agents": {str(i): n for i, n in enumerate(names)},
        "shape": "experiments",
        "facts": facts,
        "allowed_numbers": sorted(_collect_allowed(facts)),
        "brief_digest": _digest_of(facts),
    }


def _progress_brief(result: dict) -> dict:
    """Facts for narrating a fix loop: what resolved, persisted, appeared."""
    facts: list[dict] = []
    counts = result.get("action_counts") or {}
    if counts:
        _fact(facts, "counts",
              ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
              + " across the before-run's actions", counts)
    for entry in (result.get("actions") or [])[:10]:
        detail = entry.get("reason") or ""
        occurrences = entry.get("occurrences")
        if occurrences:
            detail += (f" (occurrences {occurrences['before']} -> "
                       f"{occurrences['after']})")
        _fact(facts, "action",
              f"{entry.get('status')}: {entry.get('action')} — {detail}".strip(),
              occurrences)
    for issue in (result.get("new_issues") or [])[:5]:
        _fact(facts, "new_issue",
              f"NEW: {issue.get('title')} ({issue.get('occurrences')} "
              f"occurrence(s))", None)
    for name, s_ in (result.get("success_by_agent") or {}).items():
        _fact(facts, "success",
              f"{name}: {s_.get('before')} -> {s_.get('after')} on "
              f"{s_.get('tasks_compared')} shared task(s)"
              + (f"; {s_.get('note')}" if s_.get("note") else ""), s_)
    drift = result.get("task_drift") or {}
    if drift.get("dropped") or drift.get("added"):
        _fact(facts, "drift",
              f"task set drifted: dropped {drift.get('dropped')}, added "
              f"{drift.get('added')} — judgements are restricted to shared tasks",
              drift)
    if result.get("narrative"):
        _fact(facts, "overall", result["narrative"], None)
    return {
        "task": "before/after fix comparison",
        "agents": {str(i): n for i, n in
                   enumerate(sorted(result.get("success_by_agent") or {}))},
        "shape": "progress",
        "facts": facts,
        "allowed_numbers": sorted(_collect_allowed(facts)),
        "brief_digest": _digest_of(facts),
    }


# ---------------------------------------------------------------------------
# the chat: one output directory under the same covenant

def _band(v) -> str:
    if not isinstance(v, dict) or v.get("point") is None:
        return "n/a"
    return f"{v['point']} [{v['lo']}, {v['hi']}]"


def coevolution_brief(coevolution: dict, evolution_compare: Optional[dict] = None,
                      evolution: Optional[dict] = None) -> dict:
    """The numbered facts about the eval that evolves with a lineage —
    the eval generations, every ledger row's decision and reason, the
    hindsight lags, the integrity, the recommendation under both rules —
    and, when ``evolution_compare`` carries ``evals``, the two evals side
    by side; ``evolution`` adds the agent's own steps and recommendation.
    Every number in a fact is in ``allowed_numbers``; nothing here is
    computed, every text is a field the engine wrote.
    """
    facts: list[dict] = []
    co = coevolution or {}
    ev = evolution or {}
    if ev:
        if ev.get("narrative"):
            _fact(facts, "evolution", ev["narrative"], None)
        for step in ev.get("steps") or []:
            if step.get("reading"):
                _fact(facts, "evolution.step", f"{step.get('from')}→{step.get('to')}: {step['reading']}",
                      {"effect": (step.get("effect") or {}).get("improvement"), "verdict": step.get("verdict"),
                       "flags": step.get("flags")})
        for key in ("best", "recommended"):
            block = ev.get(key) or {}
            if block.get("id"):
                _fact(facts, f"evolution.{key}", f"{key}: {block['id']} — {block.get('why')}", block.get("iqm"))
        integ = ev.get("integrity") or {}
        if integ.get("reading"):
            _fact(facts, "evolution.integrity", integ["reading"], None)
    if not co.get("measurable"):
        _fact(facts, "coevolution", f"the eval cannot be read: {co.get('reason') or 'absent'}", None)
    else:
        if co.get("synthetic"):
            _fact(facts, "coevolution", "SYNTHETIC: every episode of this lineage was generated", None)
        _fact(facts, "coevolution", co.get("narrative") or "", None)
        for e in co.get("eval_generations") or []:
            if e.get("after_step") is None:
                _fact(facts, "coevolution.eval_generation",
                      f"{e['id']}: the base eval, {e['size']} metric(s): {', '.join(e.get('adopted') or [])}", e)
            else:
                bits = []
                if e.get("adopted"):
                    bits.append("adopted " + ", ".join(e["adopted"]))
                if e.get("demoted"):
                    bits.append("demoted " + ", ".join(e["demoted"]))
                if e.get("retired"):
                    bits.append("retired " + ", ".join(e["retired"]))
                _fact(facts, "coevolution.eval_generation",
                      f"{e['id']} after {e['after_step']} ({e.get('trigger_probe')}): {'; '.join(bits)}; "
                      f"{e['size']} active metric(s)", e)
        for mid, m in (co.get("metrics") or {}).items():
            spec = m.get("spec") or {}
            where = spec.get("where")
            cond = ""
            if isinstance(where, dict):
                preds = where.get("all") if "all" in where else [where]
                cond = " where " + " and ".join(f"{p.get('feature')} {p.get('op')} {p.get('value')}" for p in preds)
            _fact(facts, "coevolution.metric",
                  f"{mid} ({spec.get('name')}): {spec.get('agg')} of {spec.get('feature')}{cond}, direction "
                  f"{spec.get('direction')}, status {m.get('status')}, from the {(m.get('origin') or {}).get('probe')} probe"
                  + (f" at {m['adopted_at']['step']}" if m.get("adopted_at") else "")
                  + (f"; confirmation {m['confirmation']['status']} ({m['confirmation']['moved']} of "
                     f"{m['confirmation']['tested']} later steps moved)" if m.get("confirmation") else "")
                  + (f"; retired at {m['retired_at']['step']}: {m['retired_at'].get('reason')}" if m.get("retired_at") else "")
                  + (f"; demoted at {m['demoted_at']['step']}: {m['demoted_at'].get('reason')}" if m.get("demoted_at") else ""),
                  {"where": where, "confirmation": m.get("confirmation")})
        for r in co.get("ledger") or []:
            _fact(facts, "coevolution.ledger",
                  f"ledger #{r.get('index')} at {r.get('step')} ({r.get('probe')} probe, eval {r.get('eval_gen')}): "
                  f"{r.get('spec_id') or '?'} {r.get('decision')}"
                  + (f" (failed {', '.join(r['failed'])})" if r.get("failed") else "")
                  + f" — {r.get('reason')}; {r.get('k')} candidate(s) tested at this step, level {r.get('alpha')}",
                  {"k": r.get("k"), "alpha": r.get("alpha")})
        for mid, gens in (co.get("matrix") or {}).items():
            cells = ", ".join(f"{gid} {_band(v)}" if v.get("measurable") else f"{gid} n/a" for gid, v in gens.items())
            _fact(facts, "coevolution.matrix", f"{mid} by generation: {cells}", gens)
        for st in co.get("steps") or []:
            evd = st.get("evolved") or {}
            _fact(facts, "coevolution.hindsight",
                  f"{st.get('from')}→{st.get('to')}: base verdict {(st.get('base') or {}).get('verdict')}, "
                  f"{'changed' if evd.get('changed') else 'unchanged'} with hindsight — {evd.get('reading')}",
                  {"flags": evd.get("flags"), "base_flags": evd.get("base_flags")})
        for mid, c in ((co.get("hindsight") or {}).get("caught_at") or {}).items():
            _fact(facts, "coevolution.caught_at",
                  f"{mid}: adopted at {c.get('adopted_step')}, first flags {c.get('first_flag_step') or 'no step'}, "
                  f"lag {c.get('lag') if c.get('lag') is not None else 'none'} — {c.get('note')}", c)
        hs = co.get("hindsight") or {}
        if hs:
            _fact(facts, "coevolution.hindsight",
                  f"hindsight over {hs.get('steps')} step(s): {hs.get('changed')} changed by a learned metric, "
                  f"{hs.get('learned_flags')} learned flag(s), {hs.get('base_flags')} base flag(s) on "
                  f"{hs.get('steps_with_base_flags')} step(s)", hs)
        for pr in co.get("probes") or []:
            _fact(facts, "coevolution.probe",
                  f"probe {pr.get('name')} ({pr.get('question')}): fired at {', '.join(pr.get('fired') or []) or 'no step'}, "
                  f"proposed {pr.get('proposed')}, adopted {pr.get('adopted')}", pr)
        rec = co.get("recommended") or {}
        _fact(facts, "coevolution.recommended",
              f"recommended: base {rec.get('base')}, evolved {rec.get('evolved')}, "
              f"{'they agree' if rec.get('agree') else 'they disagree'} — {rec.get('why')}", rec.get("excluded"))
        integ = co.get("integrity") or {}
        drift, mult = integ.get("drift") or {}, integ.get("multiplicity") or {}
        _fact(facts, "coevolution.integrity",
              f"integrity: drift {drift.get('jaccard_distance_from_base')} from the base ({drift.get('basis')}); "
              f"sizes by eval generation {drift.get('size_by_eval_gen')}; {mult.get('tested')} tested, "
              f"{mult.get('adopted')} adopted, {mult.get('rejected')} rejected, {mult.get('unparseable')} unparseable "
              f"at alpha {mult.get('alpha')}, smallest adjusted level {mult.get('min_adjusted_alpha')} "
              f"({mult.get('basis')}); demoted {', '.join(integ.get('demoted') or []) or 'none'}; retired "
              f"{', '.join(integ.get('retired') or []) or 'none'}; unconfirmed "
              f"{', '.join(integ.get('unconfirmed') or []) or 'none'}", {"drift": drift, "multiplicity": mult})
        ext = integ.get("external") or {}
        _fact(facts, "coevolution.external",
              f"external candidates: {ext.get('received')} received, {ext.get('parsed')} parsed, "
              f"{ext.get('adopted')} adopted, {ext.get('rejected')} rejected"
              + (f" from {', '.join(ext.get('sources') or [])}" if ext.get("sources") else ""), ext)
        if integ.get("gap"):
            _fact(facts, "coevolution.gap", "the gap: " + integ["gap"], None)
        flow = (co.get("flow") or {}).get("summary") or {}
        if flow.get("sentence"):
            _fact(facts, "coevolution.flow", flow["sentence"], flow)
    cmp = evolution_compare or {}
    if cmp:
        if cmp.get("narrative"):
            _fact(facts, "evolution_compare", cmp["narrative"], None)
        verdict = cmp.get("verdict") or {}
        if verdict.get("reading"):
            _fact(facts, "evolution_compare.verdict", verdict["reading"],
                  {ax: verdict.get(ax) for ax in ("peak", "final", "learning", "process")})
        for key in ("peak", "final"):
            blk = cmp.get(key) or {}
            if blk.get("reading"):
                _fact(facts, f"evolution_compare.{key}", f"{key}: {blk['reading']}",
                      {"improvement": blk.get("improvement"), "metric": blk.get("metric"), "pass_rate": blk.get("pass_rate")})
        race = cmp.get("race") or {}
        if race.get("reading"):
            _fact(facts, "evolution_compare.race", race["reading"], {"threshold": race.get("threshold"), "reached": race.get("reached")})
        for label, pr in sorted((cmp.get("process") or {}).items()):
            if isinstance(pr, dict) and pr.get("reading"):
                _fact(facts, "evolution_compare.process", pr["reading"], {"score": pr.get("score")})
        evals = cmp.get("evals") or {}
        if evals:
            if not evals.get("measurable"):
                _fact(facts, "evolution_compare.evals", f"the evals cannot be compared: {evals.get('reason')}", None)
            for ln in evals.get("lineages") or []:
                _fact(facts, "evolution_compare.evals.lineage",
                      f"{ln.get('label')}'s eval: {ln.get('eval_generations')} eval generation(s), adopted "
                      f"{', '.join(ln.get('adopted') or []) or 'nothing'}, active {', '.join(ln.get('active') or []) or 'the base only'}, "
                      f"demoted {', '.join(ln.get('demoted') or []) or 'none'}, retired {', '.join(ln.get('retired') or []) or 'none'}, "
                      f"{ln.get('tested')} tested, {ln.get('rejected')} rejected"
                      + (" (" + ", ".join(f"{n} by {v}" for v, n in (ln.get("rejected_by") or {}).items()) + ")"
                         if ln.get("rejected_by") else "")
                      + f", drift {ln.get('drift')}, {ln.get('closures')} closure(s) ({ln.get('closures_learned')} on learned "
                      f"metrics), longest hindsight lag {ln.get('hindsight_lag_max')}, recommended base "
                      f"{(ln.get('recommended') or {}).get('base')} / evolved {(ln.get('recommended') or {}).get('evolved')}",
                      ln)
            if evals.get("lineages"):
                _fact(facts, "evolution_compare.evals.shared",
                      "adopted by more than one eval: " + (", ".join(evals.get("shared_metrics") or []) or "none"), None)
            for row in evals.get("transfer") or []:
                _fact(facts, "evolution_compare.evals.transfer", row.get("reading") or "", row)
            if evals.get("transfer_rule"):
                _fact(facts, "evolution_compare.evals.rule", evals["transfer_rule"], {"alpha": evals.get("alpha")})
            if evals.get("reading"):
                _fact(facts, "evolution_compare.evals", evals["reading"], None)
    return {
        "task": f"the eval of {co.get('family') or 'a lineage'}",
        "agents": {"0": co.get("family") or "lineage"},
        "shape": "coevolution",
        "facts": facts,
        "allowed_numbers": sorted(_collect_allowed(facts)),
        "brief_digest": _digest_of(facts),
    }


def chat_brief(aggregate: dict, reports: Optional[list] = None) -> dict:
    """Every fact one output directory can vouch for, numbered once: the
    aggregate's facts (:func:`_aggregate_brief`), each pair report's
    (:func:`narration_brief`, its source prefixed by the task), and the
    lineage's — the evolution, the eval that evolved with it and, when
    the directory is a comparison, the evals side by side
    (:func:`coevolution_brief`). The chat's whole authority; a fact not
    here is a fact an answer is not entitled to state.
    """
    facts: list[dict] = []
    agg = aggregate or {}

    def take(brief: dict, prefix: str = "") -> None:
        for fact in brief.get("facts") or []:
            _fact(facts, f"{prefix}{fact['source']}", fact["text"], fact["value"])

    if agg.get("agents") or agg.get("success_rate"):
        take(_aggregate_brief(agg))
    for report in reports or []:
        if not isinstance(report, dict):
            continue
        task = (report.get("task") or {}).get("id") or "?"
        take(narration_brief(report), f"task {task}: ")
    if "coevolution" in agg or "evolution" in agg or "evolution_compare" in agg:
        take(coevolution_brief(agg.get("coevolution") or {}, agg.get("evolution_compare"), agg.get("evolution")))
    agents = dict(agg.get("agents") or {})
    if not agents and (agg.get("evolution") or {}).get("family"):
        agents = {"0": agg["evolution"]["family"]}
    steps_max = 0
    for report in reports or []:
        if isinstance(report, dict):
            for side in ("a", "b"):
                steps_max = max(steps_max, len(((report.get(side) or {}).get("steps") or [])))
    return {
        "task": (f"batch of {agg.get('tasks')} task(s)" if agg.get("tasks") is not None
                 else f"the lineage of {agents.get('0', 'an agent')}"),
        "agents": agents,
        "shape": "chat",
        "facts": facts,
        "allowed_numbers": sorted(_collect_allowed(facts, extra_ints=steps_max)),
        "brief_digest": _digest_of(facts),
    }


CHAT_HEADER = """You answer questions about one AgentDiff report for an engineer \
reading it. The numbered facts below are the whole report as far as you are \
concerned: answer only from them, in one to four plain sentences, citing the \
facts you use inline like [F7]. HARD RULES: use no number that does not appear \
in the facts, and quote every number and interval exactly as a fact states it; \
assert no cause the facts do not state; declare no winner the facts do not \
declare; when the facts do not answer the question, say "not in the report" \
and, if you can, name the nearest fact that is. Every answer is machine-checked \
against the facts, and any number or citation not in them is flagged to the \
reader beside your answer."""


def chat_prompt(brief: dict) -> str:
    """The system prompt of the chat: the covenant, then the numbered
    facts. Deterministic given the directory."""
    agents = brief.get("agents") or {}
    if set(agents) == {"a", "b"}:
        agent_line = f"Agent A: {agents['a']}   Agent B: {agents['b']}"
    elif agents:
        agent_line = "Subjects: " + ", ".join(str(v) for _, v in sorted(agents.items()))
    else:
        agent_line = "Subjects: (see facts)"
    lines = [CHAT_HEADER, "", f"Report: {brief.get('task')}", agent_line, "", "FACTS:"]
    for fact in brief["facts"]:
        lines.append(f"[{fact['id']}] ({fact['source']}) {fact['text']}")
    return "\n".join(lines)


PROMPT_HEADER = """You are narrating a comparison between two AI-agent runs \
for an engineer who has not seen the report. Write two or three plain \
paragraphs: what happened, why, and what it means. Ground every claim in the \
numbered facts below, citing them inline like [F3]. HARD RULES: use no number \
that does not appear in the facts; assert no cause the facts do not state; if \
the facts are silent on something, say so rather than guessing. Your text \
will be machine-checked against the facts, and unsupported numbers will be \
flagged to the reader."""


_HEADERS_BY_SHAPE = {
    "pair": None,   # PROMPT_HEADER as-is
    "aggregate": ("You are an evaluation agent analysing a whole batch of "
                  "AI-agent runs — every agent, every task. Write three or "
                  "four plain paragraphs: how the agents compare, what "
                  "systematically goes wrong, what explains the variation, "
                  "and what to change first."),
    "progress": ("You are an evaluation agent reviewing a fix attempt: a "
                 "before-run's actions checked against an after-run. Write "
                 "two plain paragraphs: what the fix actually achieved, and "
                 "what remains or newly appeared."),
    "experiments": ("You are an evaluation agent analysing whole experiments "
                    "against each other. Write two or three plain paragraphs: "
                    "whether the experiments genuinely differ, on what "
                    "evidence, and what to run next."),
}


def narration_prompt(brief: dict) -> str:
    """The full prompt for an external model.  Deterministic given the report."""
    override = _HEADERS_BY_SHAPE.get(brief.get("shape") or "pair")
    if override:
        header = (override + " Ground every claim in the numbered facts "
                  "below, citing them inline like [F3]. HARD RULES: use no "
                  "number that does not appear in the facts; assert no cause "
                  "the facts do not state; if the facts are silent, say so. "
                  "Your text will be machine-checked and unsupported numbers "
                  "flagged.")
    else:
        header = PROMPT_HEADER
    agents = brief.get("agents") or {}
    if set(agents) == {"a", "b"}:
        agent_line = f"Agent A: {agents['a']}   Agent B: {agents['b']}"
    else:
        agent_line = "Subjects: " + ", ".join(
            str(v) for _, v in sorted(agents.items())) if agents else "Subjects: (see facts)"
    lines = [header, "",
             f"Task: {brief.get('task')}",
             agent_line,
             "", "FACTS:"]
    for fact in brief["facts"]:
        lines.append(f"[{fact['id']}] ({fact['source']}) {fact['text']}")
    return "\n".join(lines)


def check_narration(brief: dict, text: str) -> dict:
    """Faithfulness check: numbers and citations against the brief.

    Deliberately mechanical.  It cannot catch a wrong *cause* stated with no
    number attached — that limit is declared in the result rather than
    papered over — but it catches the failure that does the most damage in
    practice: a fluent paragraph quietly containing figures from nowhere.
    """
    allowed = set(brief.get("allowed_numbers") or [])
    unsupported = []
    for token in _NUMBER.findall(text or ""):
        if _norm_number(token) not in allowed:
            unsupported.append(token)

    known = {fact["id"] for fact in brief.get("facts") or []}
    bad_citations = [f"F{m}" for m in _FACT_ID.findall(text or "")
                     if f"F{m}" not in known]
    cited = {f"F{m}" for m in _FACT_ID.findall(text or "") if f"F{m}" in known}

    return {
        "numbers_checked": len(_NUMBER.findall(text or "")),
        "unsupported_numbers": unsupported,
        "citations": len(cited),
        "invalid_citations": bad_citations,
        "faithful": not unsupported and not bad_citations,
        "limit": ("numeric and citation checking only; a causal claim with no "
                  "number attached is not verified by this check"),
    }


def ingest_narration(report: dict, text: str, brief: Optional[dict] = None,
                     model: str = "unspecified") -> dict:
    """Attach externally-produced narration to a report, checked and labelled.

    Mutates and returns the report.  The narration lands under its own key;
    nothing else in the engine reads it, so its presence or absence cannot
    change a verdict, a number, or an exit code — which is the entire deal.
    """
    brief = brief or narration_brief(report)
    verdictless_text = str(text or "").strip()
    report["narration"] = {
        "text": verdictless_text,
        "model": model,
        "source": "external-llm",
        "brief_digest": brief["brief_digest"],
        "faithfulness": check_narration(brief, verdictless_text),
        "authority": ("commentary only: produced by a language model outside "
                      "the engine, checked against the brief, and read by "
                      "nothing — deleting it changes no finding"),
    }
    return report


def narrate(report: dict, complete: Callable[[str], str],
            model: str = "external") -> dict:
    """Round trip: brief → caller's model → checked ingestion.

    ``complete`` is any callable taking a prompt string and returning text —
    the caller's LLM client, outside this package.  The engine still makes
    no network call of its own.
    """
    brief = narration_brief(report)
    text = complete(narration_prompt(brief))
    return ingest_narration(report, text, brief=brief, model=model)
