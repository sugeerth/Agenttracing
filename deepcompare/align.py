"""Trajectory alignment for DeepCompare AI.

Aligns the steps of two trajectories with Needleman-Wunsch global sequence
alignment.  Pair scores come from :func:`step_similarity`; gaps cost
``GAP_PENALTY``.  Aligned pairs with similarity >= 0.75 are ``match``, pairs
in [0.25, 0.75) are ``drift``; below 0.25 gaps are preferred, producing
``a_only`` / ``b_only`` entries with the missing side's index null, exactly
as the alignment array in SCHEMA.md.
"""

from __future__ import annotations

import re
from typing import Optional, Union

from .trace import Step, Trajectory

GAP_PENALTY = -0.4
MATCH_THRESHOLD = 0.75
DRIFT_THRESHOLD = 0.25

#: quality annotations that mark a step as behaviorally poor.
_POOR_QUALITY = frozenset({"weak", "bad"})

#: search and retrieve are treated as adjacent step types.
_ADJACENT_TYPES = frozenset({"search", "retrieve"})

#: pairing score assigned to sub-threshold pairs so that two gaps
#: (2 * GAP_PENALTY = -0.8) are always preferred over aligning them.
_BAD_PAIR_SCORE = -1.0

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercased simple word tokens of ``text`` as a set."""
    return set(_WORD_RE.findall(text.lower()))


def jaccard(text_a: str, text_b: str) -> float:
    """Token-set Jaccard similarity of two strings, in [0, 1]."""
    ta, tb = _tokenize(text_a), _tokenize(text_b)
    if not ta and not tb:
        return 1.0
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def step_similarity(a: Step, b: Step) -> float:
    """Similarity of two steps, in [0, 1].

    - Different types score 0.0, except search/retrieve which are adjacent
      and start from a base of 0.3 (plus a small text-overlap bonus).
    - Same type: weighted combination of the type match (0.5) and the
      token-set Jaccard similarity of ``name + input`` text (0.5).
    """
    text_a = f"{a.name} {a.input}"
    text_b = f"{b.name} {b.input}"
    if a.type == b.type:
        return 0.5 + 0.5 * jaccard(text_a, text_b)
    if {a.type, b.type} == _ADJACENT_TYPES:
        return 0.3 + 0.2 * jaccard(text_a, text_b)
    return 0.0


def _steps_of(t: Union[Trajectory, list]) -> list[Step]:
    return t.steps if isinstance(t, Trajectory) else list(t)


def align(a: Union[Trajectory, list[Step]], b: Union[Trajectory, list[Step]]) -> list[dict]:
    """Globally align the steps of two trajectories.

    Returns a list of dicts ``{"a_index", "b_index", "op", "similarity"}``
    ordered along both trajectories, where ``op`` is one of ``match``,
    ``drift``, ``a_only``, ``b_only``.  Gap entries carry ``None`` for the
    missing side's index and a similarity of 0.0.
    """
    steps_a, steps_b = _steps_of(a), _steps_of(b)
    n, m = len(steps_a), len(steps_b)

    # Pair scores (similarity, adjusted for the DP so bad pairs lose to gaps).
    sim = [[step_similarity(steps_a[i], steps_b[j]) for j in range(m)] for i in range(n)]

    def pair_score(i: int, j: int) -> float:
        s = sim[i][j]
        return s if s >= DRIFT_THRESHOLD else _BAD_PAIR_SCORE

    # DP table: dp[i][j] = best score aligning steps_a[:i] with steps_b[:j].
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * GAP_PENALTY
    for j in range(1, m + 1):
        dp[0][j] = j * GAP_PENALTY
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = max(
                dp[i - 1][j - 1] + pair_score(i - 1, j - 1),
                dp[i - 1][j] + GAP_PENALTY,
                dp[i][j - 1] + GAP_PENALTY,
            )

    # Deterministic traceback: prefer diagonal, then a-gap, then b-gap.
    entries: list[dict] = []
    i, j = n, m
    eps = 1e-9
    while i > 0 or j > 0:
        if i > 0 and j > 0 and abs(dp[i][j] - (dp[i - 1][j - 1] + pair_score(i - 1, j - 1))) < eps:
            s = sim[i - 1][j - 1]
            op = "match" if s >= MATCH_THRESHOLD else "drift"
            entries.append(
                {"a_index": i - 1, "b_index": j - 1, "op": op, "similarity": round(s, 4)}
            )
            i, j = i - 1, j - 1
        elif i > 0 and abs(dp[i][j] - (dp[i - 1][j] + GAP_PENALTY)) < eps:
            entries.append({"a_index": i - 1, "b_index": None, "op": "a_only", "similarity": 0.0})
            i -= 1
        else:
            entries.append({"a_index": None, "b_index": j - 1, "op": "b_only", "similarity": 0.0})
            j -= 1
    entries.reverse()

    # Safety net: never emit an aligned pair below the drift threshold —
    # split any such pair into two gap entries instead.
    result: list[dict] = []
    for e in entries:
        if e["op"] in ("match", "drift") and e["similarity"] < DRIFT_THRESHOLD:
            result.append(
                {"a_index": e["a_index"], "b_index": None, "op": "a_only", "similarity": 0.0}
            )
            result.append(
                {"a_index": None, "b_index": e["b_index"], "op": "b_only", "similarity": 0.0}
            )
        else:
            result.append(e)

    # Quality demotion: lexical similarity can miss a semantic divergence
    # (same tool, same wording, subtly wrong arguments).  When one side of an
    # aligned "match" is annotated weak/bad and the other side is not, the
    # pair is behaviorally divergent regardless of text overlap — demote it
    # to "drift" so divergence detection picks it up.
    for e in result:
        if e["op"] == "match":
            qa = steps_a[e["a_index"]].quality
            qb = steps_b[e["b_index"]].quality
            if (qa in _POOR_QUALITY) != (qb in _POOR_QUALITY):
                e["op"] = "drift"
    return result
