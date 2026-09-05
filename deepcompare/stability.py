"""Multi-run stability analysis for DeepCompare AI (SCHEMA.md v9).

With N runs per (agent, task), separates reproducible failures from noise:
per-task per-side verdicts (stable-pass / stable-fail / flaky), token and
latency coefficients of variation, divergence reproducibility over all
run-pair comparisons, per-agent flaky-task lists, and medoid run selection
(the most representative run per side, by summed Levenshtein distance over
step-type sequences).
"""

from __future__ import annotations

from typing import Optional

from .report import compare
from .trace import Trajectory

SYSTEMATIC_THRESHOLD = 0.8
VARIABLE_THRESHOLD = 0.3


def _cv(values: list[float]) -> float:
    """Population coefficient of variation; 0.0 for <2 values or zero mean."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return round((variance ** 0.5) / mean, 4)


def _levenshtein(a: list[str], b: list[str]) -> int:
    """Classic edit distance between two sequences."""
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


def _medoid(runs: list[Trajectory]) -> Trajectory:
    """The run with minimum summed step-type-sequence distance to its
    siblings (tie broken by lexicographic run_id)."""
    if len(runs) == 1:
        return runs[0]
    seqs = {t.run_id: [s.type for s in t.steps] for t in runs}
    best: Optional[Trajectory] = None
    best_key: Optional[tuple] = None
    for t in runs:
        total = sum(
            _levenshtein(seqs[t.run_id], seqs[other.run_id])
            for other in runs
            if other is not t
        )
        key = (total, t.run_id)
        if best_key is None or key < best_key:
            best, best_key = t, key
    assert best is not None
    return best


def _side_stats(runs: list[Trajectory]) -> dict:
    successes = sum(1 for t in runs if t.outcome.success)
    n = len(runs)
    if successes == n:
        verdict = "stable-pass"
    elif successes == 0:
        verdict = "stable-fail"
    else:
        verdict = "flaky"
    return {
        "successes": successes,
        "runs": n,
        "verdict": verdict,
        "token_cv": _cv([float(t.totals.input_tokens + t.totals.output_tokens)
                         for t in runs]),
        "latency_cv": _cv([t.totals.latency_s for t in runs]),
    }


def _reproducibility(runs_a: list[Trajectory], runs_b: list[Trajectory]) -> dict:
    """Divergence reproducibility over all a-run x b-run comparisons.

    rate = fraction of ALL pairs whose first divergence matches the modal
    kind with |index - modal index| <= 1.  No divergences anywhere ->
    rate 0.0, kind null, verdict "none".
    """
    observations: list[tuple[str, int]] = []
    n_pairs = 0
    for ra in runs_a:
        for rb in runs_b:
            n_pairs += 1
            divergences = compare(ra, rb)["divergences"]
            if not divergences:
                continue
            first = divergences[0]
            idx = first["a_index"] if first["a_index"] is not None else first["b_index"]
            observations.append((first["kind"], idx if idx is not None else -1))
    if not observations:
        return {"rate": 0.0, "kind": None, "verdict": "none"}

    kind_counts: dict[str, int] = {}
    for kind, _ in observations:
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    modal_kind = min(kind_counts, key=lambda k: (-kind_counts[k], k))
    idx_counts: dict[int, int] = {}
    for kind, idx in observations:
        if kind == modal_kind:
            idx_counts[idx] = idx_counts.get(idx, 0) + 1
    modal_idx = min(idx_counts, key=lambda i: (-idx_counts[i], i))

    matching = sum(
        1
        for kind, idx in observations
        if kind == modal_kind and abs(idx - modal_idx) <= 1
    )
    rate = round(matching / n_pairs, 4)
    if rate >= SYSTEMATIC_THRESHOLD:
        verdict = "systematic"
    elif rate >= VARIABLE_THRESHOLD:
        verdict = "variable"
    else:
        verdict = "none"
    return {"rate": rate, "kind": modal_kind, "verdict": verdict}


def stability_analysis(runs_by_task: dict[str, dict[str, list[Trajectory]]]) -> dict:
    """Build the SCHEMA.md v9 ``stability`` object.

    ``runs_by_task`` maps task_id -> {"a": [runs...], "b": [runs...]}, both
    sides non-empty; side "a"/"b" assignment (agent identity per side) must
    be consistent across tasks.  Raises ``ValueError`` on empty input.
    """
    if not runs_by_task:
        raise ValueError("stability_analysis needs at least one task")
    task_ids = sorted(runs_by_task)
    first = runs_by_task[task_ids[0]]
    names = {side: first[side][0].agent.name for side in ("a", "b")}

    per_task: list[dict] = []
    flaky: dict[str, list[str]] = {names["a"]: [], names["b"]: []}
    medoids: dict[str, dict[str, str]] = {}
    runs_per_agent = {names["a"]: 0, names["b"]: 0}
    systematic_count = 0
    stable_fail = {"a": 0, "b": 0}

    for tid in task_ids:
        runs_a = sorted(runs_by_task[tid]["a"], key=lambda t: t.run_id)
        runs_b = sorted(runs_by_task[tid]["b"], key=lambda t: t.run_id)
        if not runs_a or not runs_b:
            raise ValueError(f"task {tid!r} is missing runs on one side")
        stats_a = _side_stats(runs_a)
        stats_b = _side_stats(runs_b)
        repro = _reproducibility(runs_a, runs_b)
        per_task.append({"task": tid, "a": stats_a, "b": stats_b,
                         "divergence_reproducibility": repro})
        for side, stats in (("a", stats_a), ("b", stats_b)):
            if stats["verdict"] == "flaky":
                flaky[names[side]].append(tid)
            if stats["verdict"] == "stable-fail":
                stable_fail[side] += 1
        medoids[tid] = {"a": _medoid(runs_a).run_id, "b": _medoid(runs_b).run_id}
        runs_per_agent[names["a"]] = max(runs_per_agent[names["a"]], len(runs_a))
        runs_per_agent[names["b"]] = max(runs_per_agent[names["b"]], len(runs_b))
        if repro["verdict"] == "systematic":
            systematic_count += 1

    n_tasks = len(task_ids)
    bits = []
    for side in ("a", "b"):
        name = names[side]
        clauses = []
        if stable_fail[side]:
            clauses.append(f"fails all runs on {stable_fail[side]} task(s) (reproducible)")
        if flaky[name]:
            clauses.append(f"is flaky on {len(flaky[name])} task(s) "
                           f"({', '.join(flaky[name])})")
        if not clauses:
            clauses.append("is stable on every task")
        bits.append(f"{name} " + " and ".join(clauses))
    narrative = (
        f"{'; '.join(bits)}. Divergences are systematic on {systematic_count} "
        f"of {n_tasks} task(s), so the observed gaps are behavioral, not noise."
        if systematic_count
        else f"{'; '.join(bits)}. No task shows a systematically reproducible "
        f"divergence pattern."
    )

    return {
        "runs_per_agent": runs_per_agent,
        "per_task": per_task,
        "flaky_tasks": flaky,
        "medoid_runs": medoids,
        "narrative": narrative,
    }


def medoid_pairs(
    runs_by_task: dict[str, dict[str, list[Trajectory]]]
) -> list[tuple[Trajectory, Trajectory]]:
    """The (a, b) medoid trajectory pair per task, in task-id order."""
    pairs = []
    for tid in sorted(runs_by_task):
        runs_a = sorted(runs_by_task[tid]["a"], key=lambda t: t.run_id)
        runs_b = sorted(runs_by_task[tid]["b"], key=lambda t: t.run_id)
        pairs.append((_medoid(runs_a), _medoid(runs_b)))
    return pairs
