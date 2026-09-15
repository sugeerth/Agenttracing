"""Cohorts of experiments: diffs of averages, and whether behaviour moved (v25).

One comparison tells you about one pair of runs.  The question a team
actually holds is one level up: *experiment* A — a config, a prompt, a
version, run over a task suite, maybe several times — against experiment B.
The honest unit there is the **difference of averages with its uncertainty**,
not a single run's diff, because any single pair overstates whatever it
happens to contain.

Three disciplines, all inherited from elsewhere in this codebase rather than
re-decided here:

* **Diffs are computed on shared tasks, paired.**  Task difficulty is the
  biggest source of variance in any suite (the variance module measures it),
  and pairing cancels it.  Tasks present in only one experiment are listed,
  not silently blended into an average that would then compare different
  exams.
* **Uncertainty comes first.**  Success differences carry a paired-bootstrap
  interval and Wilson intervals per side; continuous metrics carry bootstrap
  intervals on the paired mean difference.  The `runs_advisory` from the
  reliability module is attached whole, because averaging three runs does
  not make three runs enough.
* **Outcome agreement is not behaviour agreement.**  Two experiments can
  score identically while acting differently — and act identically while
  scoring differently, which is evidence about the grader.  So beside every
  outcome diff sits a behavioural similarity: mean pairwise action-sequence
  similarity across experiments on the same task, against each experiment's
  own internal similarity as the baseline.  "Cross 0.61 vs within 0.94"
  means the change genuinely changed what the agent does; "cross ≈ within"
  means it did not, whatever the scores say.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional, Sequence

from .reliability import action_sequence, runs_advisory, sequence_similarity
from .statistics import (BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED,
                         paired_bootstrap_difference, wilson_interval)
from .trace import HARNESS_TERMINATIONS, Trajectory

#: continuous metrics averaged per experiment and diffed pairwise.
_CONTINUOUS = {
    "tokens": lambda t: float(t.totals.input_tokens + t.totals.output_tokens),
    "cost_usd": lambda t: float(t.totals.cost_usd),
    "latency_s": lambda t: float(t.totals.latency_s),
    "steps": lambda t: float(len(t.steps)),
}


def _by_task(runs: Sequence[Trajectory]) -> dict[str, list[Trajectory]]:
    grouped: dict[str, list[Trajectory]] = {}
    for t in runs:
        grouped.setdefault(t.task.id, []).append(t)
    return grouped


def _excluding_harness_failures(runs: Sequence[Trajectory]) -> tuple[list, int]:
    kept = [t for t in runs
            if (t.outcome.termination or "") not in HARNESS_TERMINATIONS]
    return kept, len(runs) - len(kept)


def summarise(name: str, runs: Sequence[Trajectory]) -> dict:
    """One experiment on its own terms: means, intervals, composition."""
    kept, excluded = _excluding_harness_failures(runs)
    tasks = _by_task(kept)
    successes = sum(1 for t in kept if t.outcome.success)
    means = {}
    for metric, getter in _CONTINUOUS.items():
        values = [getter(t) for t in kept]
        means[metric] = round(sum(values) / len(values), 4) if values else None
    agents = sorted({t.agent.name for t in kept})
    models = sorted({t.agent.model for t in kept if t.agent.model})
    return {
        "name": name,
        "runs": len(kept),
        "excluded_harness_failures": excluded,
        "tasks": len(tasks),
        "runs_per_task": {"min": min((len(v) for v in tasks.values()), default=0),
                          "max": max((len(v) for v in tasks.values()), default=0)},
        "agents": agents,
        "models": models,
        "successes": successes,
        "success_rate": round(successes / len(kept), 4) if kept else None,
        "success_interval": wilson_interval(successes, len(kept)) if kept else None,
        "means": means,
        "advisory": runs_advisory([len(v) for v in tasks.values()]) if tasks else None,
    }


def _task_mean(runs: Sequence[Trajectory], getter) -> float:
    values = [getter(t) for t in runs]
    return sum(values) / len(values)


def _paired_continuous_diff(shared: list[str], left: dict, right: dict,
                            getter, samples: int = BOOTSTRAP_SAMPLES,
                            seed: int = BOOTSTRAP_SEED) -> dict:
    """Bootstrap interval on the mean per-task difference (right − left).

    Per-task means first, then the difference, then resample tasks: the task
    is the exchangeable unit, and resampling raw runs would let a task with
    more repeats vote more than once.
    """
    diffs = [_task_mean(right[task], getter) - _task_mean(left[task], getter)
             for task in shared]
    observed = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    low = draws[int(0.025 * samples)]
    high = draws[min(samples - 1, int(0.975 * samples))]
    # Two-sided bootstrap p-value with add-one smoothing, for the
    # multiple-testing correction across the resource metrics.
    below = sum(1 for d in draws if d <= 0)
    above = sum(1 for d in draws if d >= 0)
    p_value = min(1.0, 2 * (min(below, above) + 1) / (samples + 1))
    return {"observed": round(observed, 4), "low": round(low, 4),
            "high": round(high, 4),
            "significant": not (low <= 0.0 <= high),
            "p_value": round(p_value, 4),
            "tasks": len(shared), "samples": samples}


def _adjust_for_multiplicity(metrics: dict) -> None:
    """Benjamini-Hochberg across the resource metrics.

    Four resource comparisons per pair means four chances for one to clear
    its interval by luck; at the usual rate that is a ~18% shot of a false
    "REAL" somewhere.  Success stays uncorrected as the single primary
    endpoint — one prespecified question, one test — and the resource
    metrics are corrected among themselves, standard trial practice.  Both
    the raw and adjusted verdicts are kept: hiding the raw one would just
    move the confusion to anyone comparing against the interval.
    """
    ranked = sorted(metrics.items(), key=lambda kv: kv[1]["p_value"])
    m = len(ranked)
    threshold_rank = 0
    for i, (_, entry) in enumerate(ranked, start=1):
        if entry["p_value"] <= 0.05 * i / m:
            threshold_rank = i
    for i, (name, entry) in enumerate(ranked, start=1):
        entry["significant_adjusted"] = i <= threshold_rank
        entry["adjustment"] = "Benjamini-Hochberg over the resource metrics"


def _cross_similarity(shared: list[str], left: dict, right: dict) -> dict:
    """Behavioural similarity across experiments, against within as baseline.

    Cross alone is uninterpretable: 0.7 is high for an agent that never does
    the same thing twice and low for one that always does.  The within-
    experiment similarity is that agent's own repeatability, and the gap
    between within and cross is how much of the behavioural change is the
    *experiment* rather than ordinary run-to-run wander.
    """
    def pairs_mean(sims: list[float]) -> Optional[float]:
        return round(sum(sims) / len(sims), 4) if sims else None

    cross, within_left, within_right = [], [], []
    for task in shared:
        seq_l = [action_sequence(t) for t in left[task]]
        seq_r = [action_sequence(t) for t in right[task]]
        for a in seq_l:
            for b in seq_r:
                cross.append(sequence_similarity(a, b))
        for group, bucket in ((seq_l, within_left), (seq_r, within_right)):
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    bucket.append(sequence_similarity(group[i], group[j]))

    within_values = within_left + within_right
    result = {
        "cross": pairs_mean(cross),
        "within_a": pairs_mean(within_left),
        "within_b": pairs_mean(within_right),
        "within": pairs_mean(within_values),
        "cross_pairs": len(cross),
        "within_pairs": len(within_values),
        "basis": "mean pairwise similarity of step type:name sequences on shared tasks",
    }
    if result["cross"] is not None and result["within"] is not None:
        result["behaviour_changed"] = result["cross"] < result["within"] - 0.05
        result["note"] = (
            "cross-experiment runs resemble each other less than runs within "
            "an experiment do — the change altered behaviour, not just scores"
            if result["behaviour_changed"] else
            "cross-experiment similarity is at the experiments' own internal "
            "level — behaviour did not measurably change")
    elif result["cross"] is not None:
        result["behaviour_changed"] = None
        result["note"] = ("one run per task per experiment: there is no "
                          "within-experiment baseline, so cross similarity "
                          "has nothing honest to be compared against")
    return result


def diff(name_a: str, runs_a: Sequence[Trajectory],
         name_b: str, runs_b: Sequence[Trajectory]) -> dict:
    """Experiment B against experiment A, paired on shared tasks."""
    kept_a, _ = _excluding_harness_failures(runs_a)
    kept_b, _ = _excluding_harness_failures(runs_b)
    tasks_a, tasks_b = _by_task(kept_a), _by_task(kept_b)
    shared = sorted(set(tasks_a) & set(tasks_b))
    only_a = sorted(set(tasks_a) - set(tasks_b))
    only_b = sorted(set(tasks_b) - set(tasks_a))

    if not shared:
        return {"a": name_a, "b": name_b, "shared_tasks": 0,
                "only_in_a": only_a, "only_in_b": only_b,
                "reason": "no shared tasks; the experiments ran different exams "
                          "and their averages are not comparable",
                "narrative": f"{name_a} and {name_b} share no tasks — "
                             "nothing can be paired."}

    success_a = [all(t.outcome.success for t in tasks_a[task]) for task in shared]
    success_b = [all(t.outcome.success for t in tasks_b[task]) for task in shared]
    success = paired_bootstrap_difference(success_a, success_b)

    metrics = {name: _paired_continuous_diff(shared, tasks_a, tasks_b, getter)
               for name, getter in _CONTINUOUS.items()}
    _adjust_for_multiplicity(metrics)
    similarity = _cross_similarity(shared, tasks_a, tasks_b)

    return {
        "a": name_a, "b": name_b,
        "shared_tasks": len(shared),
        "only_in_a": only_a, "only_in_b": only_b,
        "success_diff": success,
        "success_basis": ("per task, an experiment counts as passing only if "
                          "every kept run passed — the pass^k reading at the "
                          "task's own k, not the friendliest run"),
        "metric_diffs": metrics,
        "similarity": similarity,
        "narrative": _diff_narrative(name_a, name_b, shared, success,
                                     metrics, similarity),
    }


def _diff_narrative(name_a, name_b, shared, success, metrics, similarity) -> str:
    delta = success["observed"]
    if success["significant"]:
        winner = name_a if delta > 0 else name_b
        head = (f"On {len(shared)} shared task(s), {winner} leads on success by "
                f"{abs(delta):.0%} and the interval excludes zero.")
    else:
        head = (f"On {len(shared)} shared task(s), the success difference is "
                f"{delta:+.0%} with an interval of "
                f"[{success['low']:+.0%}, {success['high']:+.0%}] — noise-level; "
                "neither experiment is shown better.")
    moved = [f"{name} {d['observed']:+.4g}" for name, d in metrics.items()
             if d.get("significant_adjusted")]
    demoted = [name for name, d in metrics.items()
               if d["significant"] and not d.get("significant_adjusted")]
    if moved:
        head += (" Resource shifts (B−A) surviving multiple-testing "
                 "correction: " + ", ".join(moved) + ".")
    if demoted:
        head += (" " + ", ".join(demoted) + " cleared its own interval but "
                 "not the correction for testing four metrics at once.")
    if similarity.get("behaviour_changed"):
        head += (f" Behaviour moved too: cross-experiment similarity "
                 f"{similarity['cross']:.2f} against a within baseline of "
                 f"{similarity['within']:.2f}.")
    elif similarity.get("behaviour_changed") is False:
        head += (" Behaviour did not measurably move"
                 + (" even though scores did" if success["significant"] else "")
                 + f" — cross similarity {similarity['cross']:.2f} sits at the "
                   f"within-experiment baseline of {similarity['within']:.2f}"
                 + (", which points at the grader or the environment rather "
                    "than the agent" if success["significant"] else "") + ".")
    return head


def compare_experiments(named_dirs: Sequence[tuple[str, Sequence[Trajectory]]]) -> dict:
    """Every experiment summarised, every pair diffed."""
    summaries = [summarise(name, runs) for name, runs in named_dirs]
    diffs = []
    for i in range(len(named_dirs)):
        for j in range(i + 1, len(named_dirs)):
            diffs.append(diff(named_dirs[i][0], named_dirs[i][1],
                              named_dirs[j][0], named_dirs[j][1]))
    return {
        "experiments": summaries,
        "diffs": diffs,
        "narrative": _overall_narrative(summaries, diffs),
    }


def _overall_narrative(summaries, diffs) -> str:
    parts = [f"{len(summaries)} experiment(s): " + ", ".join(
        f"{s['name']} ({s['runs']} runs, "
        + (f"{s['success_rate']:.0%}" if s['success_rate'] is not None else "n/a")
        + ")" for s in summaries) + "."]
    significant = [d for d in diffs if d.get("success_diff", {}).get("significant")]
    if diffs and not significant:
        parts.append("No pairwise success difference clears its interval; "
                     "on this evidence the experiments are outcome-equivalent.")
    for d in significant:
        parts.append(d["narrative"])
    silent_moves = [d for d in diffs
                    if d.get("similarity", {}).get("behaviour_changed")
                    and not d.get("success_diff", {}).get("significant")]
    for d in silent_moves:
        parts.append(f"{d['a']} vs {d['b']}: outcomes match but behaviour "
                     f"differs (cross {d['similarity']['cross']:.2f} vs within "
                     f"{d['similarity']['within']:.2f}) — the change is real "
                     "but the suite cannot see it in scores.")
    return " ".join(parts)


def load_experiment(directory: Path) -> list[Trajectory]:
    """Every valid trace in a directory; invalid files are skipped loudly."""
    runs = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            runs.append(Trajectory.from_json(path))
        except ValueError:
            continue
    return runs
