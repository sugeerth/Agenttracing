"""Step-level evaluation detail for DeepCompare AI (SCHEMA.md v5).

Adds an ``eval`` object to every alignment entry that pairs two present
steps — similarity components (same tokenization as align.py), B-minus-A
deltas (tokens, latency, and per-step cost derived from each trajectory's
own totals cost-per-token ratio), a quality verdict, and (when exactly one
agent failed and a root divergent step exists) per-side propagation: how
much of the root mistake's output this step's input carries forward.  Also
provides the report-level ``answer_eval`` object comparing both final
answers to each other and to the task's expected answer.
"""

from __future__ import annotations

from typing import Optional

from .align import jaccard
from .tooldiff import token_diff
from .trace import Trajectory

MATCH_COVERAGE = 0.6
PARTIAL_COVERAGE = 0.3

_POOR = ("weak", "bad")
_EDGE_PUNCT = ".,;:!?()[]{}\"'$%"


def _cost_per_token(t: Trajectory) -> float:
    """A trajectory's own cost-per-token ratio (0.0 when it has no tokens)."""
    total = t.totals.input_tokens + t.totals.output_tokens
    return t.totals.cost_usd / total if total > 0 else 0.0


def step_eval(
    entry: dict,
    a: Trajectory,
    b: Trajectory,
    root_output_a: Optional[str],
    root_output_b: Optional[str],
) -> Optional[dict]:
    """Build the SCHEMA.md ``eval`` object for one alignment entry.

    Returns ``None`` unless both sides of the entry are present.  ``delta``
    is B minus A; per-step cost is each trajectory's cost-per-token ratio
    times the step's tokens.  ``propagation`` is included only when a root
    divergent step exists (either root output is non-None): per side, the
    word Jaccard between this step's input and that side's root output —
    0.0 for a side whose root output is unknown.
    """
    if entry.get("a_index") is None or entry.get("b_index") is None:
        return None
    step_a = a.steps[entry["a_index"]]
    step_b = b.steps[entry["b_index"]]

    cost_a = _cost_per_token(a) * step_a.tokens
    cost_b = _cost_per_token(b) * step_b.tokens

    poor_a = step_a.quality in _POOR
    poor_b = step_b.quality in _POOR
    if poor_a == poor_b:
        verdict = "equal"
    elif poor_a:
        verdict = "a_degraded"
    else:
        verdict = "b_degraded"

    result: dict = {
        "similarity": {
            "type_match": step_a.type == step_b.type,
            "name_jaccard": round(jaccard(step_a.name, step_b.name), 4),
            "input_jaccard": round(jaccard(step_a.input, step_b.input), 4),
        },
        "delta": {
            "tokens": step_b.tokens - step_a.tokens,
            "latency_s": round(step_b.latency_s - step_a.latency_s, 4),
            "cost_usd": round(cost_b - cost_a, 6),
        },
        "quality": {"a": step_a.quality, "b": step_b.quality, "verdict": verdict},
    }
    if root_output_a is not None or root_output_b is not None:
        result["propagation"] = {
            "a": round(jaccard(step_a.input, root_output_a), 4)
            if root_output_a is not None
            else 0.0,
            "b": round(jaccard(step_b.input, root_output_b), 4)
            if root_output_b is not None
            else 0.0,
        }
    return result


def _gold_tokens(text: str) -> set[str]:
    """Whitespace tokens, lowercased, edge punctuation stripped.

    Unlike align.py's tokenizer this keeps internal punctuation ("4.82",
    "2.14.1") intact so numeric values compare as whole tokens.
    """
    tokens = set()
    for raw in text.lower().split():
        tok = raw.strip(_EDGE_PUNCT)
        if tok:
            tokens.add(tok)
    return tokens


def _vs_expected(answer: str, expected: Optional[str]) -> dict:
    """Score one final answer against the expected answer.

    The score is *coverage*: the fraction of expected tokens present in the
    answer (symmetric Jaccard would punish long correct answers against a
    short gold string).  A number-aware cap keeps overlap honest: if any
    digit-bearing expected token is missing from the answer, the verdict can
    be at best "partial" — a wrong number never reads as a match.
    """
    if expected is None:
        return {"coverage": None, "verdict": "unknown"}
    gold = _gold_tokens(expected)
    got = _gold_tokens(answer)
    if not gold:
        return {"coverage": None, "verdict": "unknown"}
    coverage = round(len(gold & got) / len(gold), 4)
    numbers_ok = all(tok in got for tok in gold if any(c.isdigit() for c in tok))
    if coverage >= MATCH_COVERAGE and numbers_ok:
        verdict = "match"
    elif coverage >= PARTIAL_COVERAGE:
        verdict = "partial"
    else:
        verdict = "mismatch"
    return {"coverage": coverage, "verdict": verdict}


def answer_eval(a: Trajectory, b: Trajectory) -> dict:
    """Build the SCHEMA.md report-level ``answer_eval`` object.

    ``diff_ab`` is the token-level diff of A's final answer against B's;
    each side is scored against ``task.expected`` by expected-token coverage
    (match >= 0.6 with all numeric tokens present, partial >= 0.3, else
    mismatch; "unknown" when expected is null).
    """
    expected = a.task.expected
    return {
        "expected": expected,
        "diff_ab": token_diff(a.outcome.answer, b.outcome.answer),
        "a_vs_expected": _vs_expected(a.outcome.answer, expected),
        "b_vs_expected": _vs_expected(b.outcome.answer, expected),
    }
