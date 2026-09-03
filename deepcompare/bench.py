"""Ground-truth accuracy benchmark for the diagnoser itself.

The Who&When lesson is that failure attributors which are never evaluated
collapse on hard cases, so the diagnoser's accuracy must be a measured,
published number, not an assumption.  :func:`run_benchmark` runs the full
:func:`deepcompare.report.compare` pipeline over a corpus of trajectory
pairs with one implanted known cause each (see
``demo/diagnosis_bench/generate.py``) and scores the ``diagnosis`` section
against the manifest's ground truth.

Scoring rules, applied without mercy:

- A scenario is **correct** only when the *leading* hypothesis's kind — or
  its ``kind:flag`` label, or its ``mechanism`` field — is in the
  scenario's acceptable set.  The acceptable set exists because two kinds
  can legitimately name the same cause at different depths (a divergence
  whose mechanism is wrong_fact_propagation IS the wrong fact).
- A **contested** diagnosis (no leading hypothesis) is its own outcome and
  never counts as correct, even when the true cause is ranked first: a
  diagnoser that cannot commit has not diagnosed.
- **Step localization is scored separately**, because it is the axis on
  which the field collapses (best published: 14.2% on Who&When, 30.3% in
  Who&When Pro).  The manifest carries the implanted decisive step —
  the earliest step whose correction flips the outcome — and the
  diagnosis's ``decisive_step`` is scored exact and within ±1 against it.
  Causes with **no** agent step to correct (a grader mislabel, a harness
  kill) are scored as abstention: predicting ``None`` there is a correct
  answer, and naming a step is a ``spurious_step`` miss.  A contested
  diagnosis predicts no step, which on a step-truth scenario counts as a
  miss, not a pass.
- Accuracies are always reported with their denominators, and every miss
  is listed with what actually led, so the number cannot silently exclude
  the failures.

Everything is deterministic: fixed corpus in, fixed report out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .report import compare
from .semantic import normalize_for_containment
from .trace import HARNESS_TERMINATIONS, Trajectory

MANIFEST_NAME = "MANIFEST.json"

#: CI floors, shared by the test suite and `agentdiff bench --strict`.
#: Raise them by making the diagnoser better, never by easing scenarios.
FLOORS = {
    "kind_accuracy": 0.75,
    "step_accuracy_exact": 0.6,
    "abstention_accuracy": 0.75,
    "chain_mean_recall": 0.7,
    "chain_mean_precision": 0.8,
}


def floor_violations(result: dict) -> list[str]:
    """Which floors the measured result breaks (empty = all clear)."""
    measured = {
        "kind_accuracy": result["overall"]["accuracy"],
        "step_accuracy_exact": result["step_localization"]["accuracy_exact"],
        "abstention_accuracy": result["abstention"]["accuracy"],
        "chain_mean_recall": result["chain_recovery"]["mean_recall"],
        "chain_mean_precision": result["chain_recovery"]["mean_precision"],
    }
    problems = []
    for name, floor in FLOORS.items():
        value = measured.get(name)
        if value is not None and value < floor:
            problems.append(f"{name} {value} < floor {floor}")
    return problems


from .statistics import clustered_se as _clustered_se


def format_scorecard(result: dict) -> str:
    """The benchmark result as a terminal scorecard, denominators intact."""
    overall = result["overall"]
    step = result["step_localization"]
    abstention = result["abstention"]
    chain = result["chain_recovery"]
    lines = [
        "Diagnoser benchmark (ground-truth implants):",
        f"  cause kind      {overall['correct']}/{overall['total']}"
        f"  ({overall['accuracy']:.0%})",
        f"  decisive step   {step['exact']}/{step['total']} exact, "
        f"{step['within_1']}/{step['total']} within ±1",
        f"  abstention      {abstention['correct']}/{abstention['total']}"
        + "  (causes with no agent step to correct)",
        f"  chain recovery  recall {chain['mean_recall']}, precision "
        f"{chain['mean_precision']} over {chain['scenarios']} scenario(s)",
    ]
    clustered = overall.get("clustered_by_cause") or {}
    if clustered.get("clustered_se") is not None and not clustered.get("naive_se"):
        lines.append("  error bar       no variance on this corpus (every scenario "
                     "scored the same) — the scaled corpus carries a bounded interval")
    elif clustered.get("clustered_se") is not None:
        lines.append(
            f"  error bar       naive ±{clustered['naive_se']:.4f}, clustered by "
            f"cause ±{clustered['clustered_se']:.4f} ({clustered['clusters']} "
            f"families; ×{clustered['ratio']}) — the clustered one is honest")
    probe = result.get("leakage_probe") or {}
    margin = result.get("engine_minus_probe") or {}
    if probe.get("kind", {}).get("total"):
        lines.append(
            f"  leakage probe   kind {probe['kind']['correct']}/{probe['kind']['total']}"
            f" ({probe['kind']['accuracy']:.0%}), step "
            f"{probe['step']['exact']}/{probe['step']['total']} — engine margin "
            f"kind {margin.get('kind'):+.4f}, step {margin.get('step'):+.4f}"
            if probe['step']['total'] else
            f"  leakage probe   kind {probe['kind']['correct']}/{probe['kind']['total']}"
            f" — engine margin kind {margin.get('kind'):+.4f}")
    if result.get("invalid_pairs"):
        lines.append(f"  INVALID PAIRS   {len(result['invalid_pairs'])} excluded — "
                     "clean twin fails its own grader: "
                     + ", ".join(p["scenario"] for p in result["invalid_pairs"][:5]))
    if result.get("unreachable_artifact"):
        lines.append(f"  UNREACHABLE     {len(result['unreachable_artifact'])} scenario(s) "
                     "whose implanted artifact never appears between the decisive "
                     "step and the answer: "
                     + ", ".join(p["scenario"] for p in result["unreachable_artifact"][:5]))
    elif result.get("artifact_checked"):
        lines.append(f"  injection contract  {result['artifact_checked']} artifact(s) "
                     "checked reachable; every clean twin passes its grader")
    for miss in result["misses"]:
        lines.append(f"  MISS {miss['scenario']}: truth {miss['truth']}, "
                     f"led {miss['actually_led']}")
    for miss in result["step_misses"]:
        lines.append(f"  STEP MISS {miss['scenario']}: truth "
                     f"{miss['truth']}, predicted {miss['predicted']} "
                     f"({miss['outcome']})")
    if not result["misses"] and not result["step_misses"]:
        lines.append("  no misses on this corpus")
    return "\n".join(lines)


def _leading_labels(diagnosis: dict) -> tuple[dict, set[str]]:
    """The leading hypothesis and every label it may be credited under.

    Labels are the hypothesis ``kind``, ``kind:flag`` when a process flag is
    attached, and the ``mechanism`` when a fused hypothesis names one.
    Returns ``({}, set())`` when the diagnosis is contested (no leader).
    """
    leading_id = diagnosis.get("leading")
    if leading_id is None:
        return {}, set()
    lead = next((h for h in diagnosis.get("hypotheses", [])
                 if h.get("id") == leading_id), None)
    if lead is None:
        return {}, set()
    labels = {lead.get("kind")}
    if lead.get("flag"):
        labels.add(f"{lead['kind']}:{lead['flag']}")
    if lead.get("mechanism"):
        labels.add(lead["mechanism"])
    labels.discard(None)
    return lead, labels


def _contested_summary(diagnosis: dict) -> str:
    """Human-readable label for a contested diagnosis: the top contenders."""
    scored = [h for h in diagnosis.get("hypotheses", [])
              if h.get("score") is not None and h.get("status") != "merged"]
    names = []
    for h in scored[:3]:
        name = h.get("kind", "?")
        if h.get("flag"):
            name = f"{name}:{h['flag']}"
        names.append(f"{name}={h['score']}")
    return "contested: " + ", ".join(names) if names else "contested"


def _chain_rollup(results: list[dict]) -> dict:
    """Mean chain recall/precision over scenarios with a chain truth.

    The long-horizon attribution protocol (2608.06909) scores the recovered
    causal chain, not just the primary anchor: a diagnosis that names the
    right step but drags distractor steps into its account — or drops the
    propagation path — has told a worse story than its anchor suggests.
    A scenario whose diagnosis produced no causal account scores recall 0
    (counted, never skipped); its precision is undefined and excluded from
    the precision mean with the exclusion counted.
    """
    scored = [r for r in results if r.get("chain")]
    if not scored:
        return {"scenarios": 0, "mean_recall": None, "mean_precision": None,
                "no_account": 0}
    recalls = [r["chain"]["recall"] for r in scored]
    precisions = [r["chain"]["precision"] for r in scored
                  if r["chain"]["precision"] is not None]
    return {
        "scenarios": len(scored),
        "mean_recall": round(sum(recalls) / len(recalls), 4),
        "mean_precision": (round(sum(precisions) / len(precisions), 4)
                           if precisions else None),
        "no_account": len(scored) - len(precisions),
    }


_TEMPLATE_CUES = ("stale", "archived", "unofficial", "cache", "mirror",
                  "draft", "superseded", "outdated")
_NEGATORS = ("not", "no", "never", "cannot", "without", "unable")


def leakage_probe(failing: Trajectory, passing: Trajectory) -> dict:
    """A deliberately dumb detector: surface cues only, no engine.

    The Leaky-Model-Organisms critique of implanted benchmarks is that
    the implantation leaves fingerprints a detector can exploit without
    doing any causal work — annotation marks, template words, step-count
    deltas, error strings.  This probe is that detector, kept as stupid
    as possible on purpose: whatever share of the engine's score it
    reproduces is the share the benchmark may be leaking, and the
    headline is the engine's margin over it, not the engine's score.
    """
    steps = failing.steps
    answer_idx = len(steps) - 1
    answer = (steps[answer_idx].output or steps[answer_idx].input or "").lower()
    expected = (getattr(failing.task, "expected", None) or "").strip().lower()
    term = failing.outcome.termination
    pass_sigs = {(s.type, s.name, s.input) for s in passing.steps}

    def text(i):
        return f"{steps[i].input}\n{steps[i].output}".lower()

    if term in HARNESS_TERMINATIONS:
        return {"kind": "harness_termination", "step": None, "cue": "termination"}
    annotated = [i for i, st in enumerate(steps[:answer_idx])
                 if st.quality in ("weak", "bad") or st.note]
    errors = [i for i, st in enumerate(steps[:answer_idx])
              if st.error is True or (st.output or "").lower().startswith("error")]
    if expected and expected in answer and not any(
            w in answer.split() for w in _NEGATORS):
        return {"kind": "grader_or_label", "step": None, "cue": "expected verbatim"}
    writes = [i for i, st in enumerate(steps[:answer_idx]) if st.effect == "write"]
    reads = [i for i, st in enumerate(steps[:answer_idx])
             if st.effect == "read" and i not in errors]
    if writes and (not reads or writes[0] < reads[0]):
        return {"kind": "process_pathology:blind_write", "step": writes[0],
                "cue": "write before read"}
    seen = {}
    for i, st in enumerate(steps[:answer_idx]):
        sig = (st.type, st.name, st.input)
        if sig in seen and st.effect == "write":
            return {"kind": "divergence", "step": seen[sig], "cue": "duplicate write"}
        seen.setdefault(sig, i)
    if errors:
        return {"kind": "environment_error", "step": errors[0], "cue": "error text"}
    if annotated:
        return {"kind": "wrong_fact_propagation", "step": annotated[0],
                "cue": "annotation mark"}
    for i in range(answer_idx):
        if any(cue in text(i) for cue in _TEMPLATE_CUES):
            return {"kind": "wrong_fact_propagation", "step": i, "cue": "template word"}
    novel = [i for i, st in enumerate(steps[:answer_idx])
             if (st.type, st.name, st.input) not in pass_sigs]
    return {"kind": "divergence", "step": novel[0] if novel else 0,
            "cue": "first step without a twin"}


def _normalise(text: str) -> str:
    return normalize_for_containment(text)


def pair_validity(scenario: dict, failing: Trajectory, passing: Trajectory) -> dict:
    """The injection contract, checked per pair: (1) the clean twin must
    pass its own grader — the expected answer must be in the passing
    answer — else the pair measures nothing; (2) the implanted artifact
    must be textually reachable in the failing run between the decisive
    step and the answer, else the fault the manifest names is not one the
    trace can show."""
    expected = _normalise(getattr(passing.task, "expected", None) or "")
    pass_answer = _normalise(passing.outcome.answer or passing.steps[-1].output or "")
    clean_twin_passes = bool(expected) and expected in pass_answer
    artifact = scenario.get("artifact")
    reachable = None
    if artifact:
        needle = _normalise(str(artifact))
        start = min(scenario.get("decisive_steps") or [0])
        hay = " ".join(_normalise(f"{s.input} {s.output}")
                       for s in failing.steps[start:])
        reachable = needle in hay
    return {"clean_twin_passes": clean_twin_passes,
            "artifact": artifact, "artifact_reachable": reachable}


def run_benchmark(traces_dir: Union[str, Path]) -> dict:
    """Run the diagnoser over every manifest pair and score it.

    ``traces_dir`` must hold ``MANIFEST.json`` plus the referenced
    ``*__fail.json`` / ``*__pass.json`` trajectories.  Returns a dict with
    ``overall`` and ``by_cause`` accuracies (each with numerator and
    denominator), a ``misses`` list naming every scenario the diagnoser got
    wrong or left contested together with what actually led, and the full
    per-scenario ``results``.
    """
    root = Path(traces_dir)
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    scenarios = sorted(manifest.get("scenarios", []), key=lambda s: s["id"])

    results: list[dict] = []
    misses: list[dict] = []
    by_cause: dict[str, dict] = {}
    correct_total = 0

    invalid_pairs: list[dict] = []
    unreachable: list[dict] = []
    probe_hits = {"kind": 0, "step": 0, "kind_total": 0, "step_total": 0}
    for scenario in scenarios:
        failing = Trajectory.from_json(root / scenario["fail"])
        passing = Trajectory.from_json(root / scenario["pass"])
        validity = pair_validity(scenario, failing, passing)
        if not validity["clean_twin_passes"]:
            # a pair whose clean twin fails its own grader measures the
            # grader, not the diagnoser: excluded, counted, never silent
            invalid_pairs.append({"scenario": scenario["id"],
                                  "cause": scenario["cause"],
                                  "reason": "clean twin does not pass the grader"})
            continue
        if validity["artifact_reachable"] is False:
            unreachable.append({"scenario": scenario["id"],
                                "cause": scenario["cause"],
                                "artifact": validity["artifact"]})
        report = compare(failing, passing)
        diagnosis = report["diagnosis"]
        acceptable = set(scenario.get("acceptable", []))
        probe = leakage_probe(failing, passing)
        probe_kind_ok = probe["kind"] in acceptable
        probe_truth_steps = scenario.get("decisive_steps") or []
        probe_hits["kind_total"] += 1
        probe_hits["kind"] += 1 if probe_kind_ok else 0
        if probe_truth_steps:
            probe_hits["step_total"] += 1
            probe_hits["step"] += 1 if probe["step"] in probe_truth_steps else 0

        lead, labels = _leading_labels(diagnosis)
        secondary = set(scenario.get("secondary", []))
        if not lead:
            outcome = "contested"
            actual = _contested_summary(diagnosis)
        else:
            actual = lead.get("kind", "?")
            if lead.get("flag"):
                actual = f"{actual}:{lead['flag']}"
            if lead.get("mechanism"):
                actual += f" (mechanism {lead['mechanism']})"
            if labels & acceptable:
                outcome = "correct"
            elif labels & secondary:
                # led with the genuine-but-lesser contributor: not correct
                # (the primary drives the failure), but its own outcome —
                # calling it plain "wrong" would hide what happened
                outcome = "secondary_only"
            else:
                outcome = "wrong"

        # multi-cause honesty: the secondary contributor must be VISIBLE
        # somewhere in the hypothesis list (any status, merged included) —
        # a single-label diagnosis that drops the second fault has told
        # less than the trace shows
        secondary_visible = None
        if secondary:
            seen = set()
            for h in diagnosis.get("hypotheses", []):
                seen.add(h.get("kind"))
                if h.get("flag"):
                    seen.add(f"{h.get('kind')}:{h['flag']}")
            secondary_visible = bool(seen & secondary)

        truth_steps = scenario.get("decisive_steps") or []
        predicted = (diagnosis.get("decisive_step") or {}).get("step")
        truth_chain = set(scenario.get("chain") or [])
        predicted_chain = {entry.get("step")
                           for entry in diagnosis.get("causal_account", [])
                           if entry.get("step") is not None}
        if truth_chain:
            hit = truth_chain & predicted_chain
            chain_scores = {
                "recall": round(len(hit) / len(truth_chain), 4),
                "precision": (round(len(hit) / len(predicted_chain), 4)
                              if predicted_chain else None),
                "truth": sorted(truth_chain),
                "predicted": sorted(predicted_chain),
            }
        else:
            chain_scores = None
        if truth_steps:
            if predicted is None:
                step_outcome = "missed_step"
            elif predicted in truth_steps:
                step_outcome = "exact"
            elif any(abs(predicted - t) <= 1 for t in truth_steps):
                step_outcome = "adjacent"
            else:
                step_outcome = "wrong_step"
        else:
            step_outcome = ("correct_abstain" if predicted is None
                            else "spurious_step")

        entry = {
            "scenario": scenario["id"],
            "cause": scenario["cause"],
            "acceptable": sorted(acceptable),
            "outcome": outcome,
            "actual": actual,
            "margin": diagnosis.get("margin"),
            "decisive_truth": truth_steps,
            "decisive_predicted": predicted,
            "step_outcome": step_outcome,
            "chain": chain_scores,
            "secondary": sorted(secondary),
            "secondary_visible": secondary_visible,
            "probe": probe, "probe_kind_correct": probe_kind_ok,
            "artifact_reachable": validity["artifact_reachable"],
        }
        results.append(entry)

        bucket = by_cause.setdefault(
            scenario["cause"], {"correct": 0, "total": 0, "accuracy": 0.0})
        bucket["total"] += 1
        if outcome == "correct":
            bucket["correct"] += 1
            correct_total += 1
        else:
            misses.append({
                "scenario": scenario["id"],
                "truth": scenario["cause"],
                "acceptable": sorted(acceptable),
                "outcome": outcome,
                "actually_led": actual,
            })

    for bucket in by_cause.values():
        bucket["accuracy"] = (round(bucket["correct"] / bucket["total"], 4)
                              if bucket["total"] else None)
    total = len(results)

    step_truth = [r for r in results if r["decisive_truth"]]
    abstain_truth = [r for r in results if not r["decisive_truth"]]
    exact = sum(1 for r in step_truth if r["step_outcome"] == "exact")
    within_1 = exact + sum(
        1 for r in step_truth if r["step_outcome"] == "adjacent")
    abstain_ok = sum(
        1 for r in abstain_truth if r["step_outcome"] == "correct_abstain")
    step_misses = [
        {"scenario": r["scenario"], "truth": r["decisive_truth"],
         "predicted": r["decisive_predicted"], "outcome": r["step_outcome"]}
        for r in results
        if r["step_outcome"] not in ("exact", "correct_abstain")
    ]

    secondary_only = sum(
        1 for r in results if r["outcome"] == "secondary_only")
    with_secondary = [r for r in results if r["secondary"]]
    return {
        "version": 2,
        "overall": {
            "correct": correct_total,
            "total": total,
            "accuracy": round(correct_total / total, 4) if total else None,
            "secondary_only": secondary_only,
            # scenarios in one cause family share a template — not
            # independent draws — so the naive error bar on the accuracy
            # is too small by exactly the factor the clustered one reports
            "clustered_by_cause": _clustered_se(
                [1.0 if r["outcome"] == "correct" else 0.0 for r in results],
                [r["cause"] for r in results]),
        },
        "multi_cause": {
            "scenarios": len(with_secondary),
            "secondary_visible": sum(
                1 for r in with_secondary if r["secondary_visible"]),
        },
        "step_localization": {
            "exact": exact,
            "within_1": within_1,
            "total": len(step_truth),
            "accuracy_exact": (round(exact / len(step_truth), 4)
                               if step_truth else None),
            "accuracy_within_1": (round(within_1 / len(step_truth), 4)
                                  if step_truth else None),
        },
        "abstention": {
            "correct": abstain_ok,
            "total": len(abstain_truth),
            "accuracy": (round(abstain_ok / len(abstain_truth), 4)
                         if abstain_truth else None),
        },
        "chain_recovery": _chain_rollup(results),
        "leakage_probe": {
            "kind": {"correct": probe_hits["kind"], "total": probe_hits["kind_total"],
                     "accuracy": (round(probe_hits["kind"] / probe_hits["kind_total"], 4)
                                  if probe_hits["kind_total"] else None)},
            "step": {"exact": probe_hits["step"], "total": probe_hits["step_total"],
                     "accuracy": (round(probe_hits["step"] / probe_hits["step_total"], 4)
                                  if probe_hits["step_total"] else None)},
            "note": "a surface-cue detector with no engine: whatever share of the "
                    "engine's score it reproduces is the share the benchmark may "
                    "be leaking; the headline is the margin, not the score",
        },
        "engine_minus_probe": {
            "kind": (round(correct_total / total - probe_hits["kind"] / probe_hits["kind_total"], 4)
                     if total and probe_hits["kind_total"] else None),
            "step": (round(exact / len(step_truth) - probe_hits["step"] / probe_hits["step_total"], 4)
                     if step_truth and probe_hits["step_total"] else None),
        },
        "invalid_pairs": invalid_pairs,
        "unreachable_artifact": unreachable,
        "artifact_checked": sum(1 for r in results if r["artifact_reachable"] is not None),
        "by_cause": {cause: by_cause[cause] for cause in sorted(by_cause)},
        "misses": misses,
        "step_misses": step_misses,
        "results": results,
    }
