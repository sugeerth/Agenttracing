"""Divergence detection for DeepCompare AI.

Groups consecutive non-``match`` alignment entries into divergence regions,
classifies each region's ``kind`` from the step types involved, computes the
``downstream`` impact (extra steps / tokens / latency the more expensive side
spends from the divergence point to its answer), and writes a human-readable
``summary`` per divergence, following the SCHEMA.md divergence objects.
"""

from __future__ import annotations

from typing import Optional

from .trace import Step, Trajectory

#: kind classification priority: first step-type family present wins.
_KIND_PRIORITY: list[tuple[str, frozenset[str]]] = [
    ("retrieval", frozenset({"search", "retrieve", "read"})),
    ("tool_selection", frozenset({"tool_call"})),
    ("planning", frozenset({"plan"})),
    ("reasoning", frozenset({"reason"})),
]

_TRUNCATE = 60


def _snippet(text: str, limit: int = _TRUNCATE) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _describe(step: Step) -> str:
    """Short human-readable phrase for what a step did."""
    label = step.name or _snippet(step.input, 30) or step.type
    phrases = {
        "plan": f'planned "{_snippet(step.input) or label}"',
        "search": f'searched "{_snippet(step.input) or label}"',
        "retrieve": f'selected the source "{label}"',
        "read": f'read "{label}"',
        "tool_call": f'called tool "{step.name or label}" with "{_snippet(step.input)}"',
        "reason": f'reasoned "{_snippet(step.input or step.output)}"',
        "answer": f'answered "{_snippet(step.input or step.output)}"',
    }
    text = phrases.get(step.type, f'performed {step.type} "{label}"')
    if step.quality in ("weak", "bad"):
        text += f" (annotated {step.quality})"
    return text


def _first_index(region: list[dict], key: str) -> Optional[int]:
    for entry in region:
        if entry[key] is not None:
            return entry[key]
    return None


def _side_start(region: list[dict], alignment: list[dict], key: str, n_steps: int) -> int:
    """First step index of one side at/after the region (``n_steps`` if none)."""
    idx = _first_index(region, key)
    if idx is not None:
        return idx
    pos = alignment.index(region[-1])
    for entry in alignment[pos + 1 :]:
        if entry[key] is not None:
            return entry[key]
    return n_steps


def _classify_kind(region: list[dict], a: Trajectory, b: Trajectory, is_trailing: bool) -> str:
    """Classify a divergence region by the step types it involves."""
    ops = {e["op"] for e in region}
    one_sided = ops <= {"a_only"} or ops <= {"b_only"}
    if is_trailing and one_sided:
        return "stopping"
    types: set[str] = set()
    for entry in region:
        if entry["a_index"] is not None:
            types.add(a.steps[entry["a_index"]].type)
        if entry["b_index"] is not None:
            types.add(b.steps[entry["b_index"]].type)
    for kind, family in _KIND_PRIORITY:
        if types & family:
            return kind
    return "reasoning"


def _summary(
    region: list[dict], a: Trajectory, b: Trajectory, kind: str
) -> str:
    """Human-readable one-line summary of a divergence region."""
    first = region[0]
    a_idx, b_idx = first["a_index"], first["b_index"]
    a_step = a.steps[a_idx] if a_idx is not None else None
    b_step = b.steps[b_idx] if b_idx is not None else None

    if kind == "stopping":
        side, steps, other = ("A", a, "B") if first["op"] == "a_only" else ("B", b, "A")
        count = len(region)
        return (
            f"Agent {side} continued for {count} extra step(s) before answering, "
            f"after Agent {other} had already finished the same work."
        )

    if a_step is not None and b_step is not None:
        aq = a_step.quality in ("weak", "bad")
        bq = b_step.quality in ("weak", "bad")
        if kind == "retrieval" and bq and not aq:
            return (
                f'Agent B selected a lower-quality source ("{b_step.name or _snippet(b_step.input)}") '
                f'where Agent A used "{a_step.name or _snippet(a_step.input)}".'
            )
        if kind == "retrieval" and aq and not bq:
            return (
                f'Agent A selected a lower-quality source ("{a_step.name or _snippet(a_step.input)}") '
                f'where Agent B used "{b_step.name or _snippet(b_step.input)}".'
            )
        return f"Agent A {_describe(a_step)} while Agent B {_describe(b_step)}."

    if a_step is not None:
        count = sum(1 for e in region if e["op"] == "a_only")
        return (
            f"Agent A took {count} extra step(s) not mirrored by Agent B, "
            f"starting where it {_describe(a_step)}."
        )
    count = sum(1 for e in region if e["op"] == "b_only")
    assert b_step is not None
    return (
        f"Agent B took {count} extra step(s) not mirrored by Agent A, "
        f"starting where it {_describe(b_step)}."
    )


def _downstream(
    region: list[dict],
    alignment: list[dict],
    a: Trajectory,
    b: Trajectory,
    caused_failure: bool,
) -> dict:
    """Extra steps/tokens/latency the more expensive side spends from the
    divergence point to its answer, relative to the other side."""
    start_a = _side_start(region, alignment, "a_index", len(a.steps))
    start_b = _side_start(region, alignment, "b_index", len(b.steps))
    rest_a = a.steps[start_a:]
    rest_b = b.steps[start_b:]
    steps_a, steps_b = len(rest_a), len(rest_b)
    tokens_a = sum(s.tokens for s in rest_a)
    tokens_b = sum(s.tokens for s in rest_b)
    lat_a = sum(s.latency_s for s in rest_a)
    lat_b = sum(s.latency_s for s in rest_b)

    # Report the side that spends more (steps first, tokens as tie-break).
    b_heavier = (steps_b, tokens_b, lat_b) >= (steps_a, tokens_a, lat_a)
    if b_heavier:
        return {
            "extra_steps_b": steps_b - steps_a,
            "extra_tokens_b": tokens_b - tokens_a,
            "extra_latency_s_b": round(lat_b - lat_a, 4),
            "caused_failure": caused_failure,
        }
    return {
        "extra_steps_a": steps_a - steps_b,
        "extra_tokens_a": tokens_a - tokens_b,
        "extra_latency_s_a": round(lat_a - lat_b, 4),
        "caused_failure": caused_failure,
    }


def find_divergences(a: Trajectory, b: Trajectory, alignment: list[dict]) -> list[dict]:
    """Find divergence regions between two aligned trajectories.

    Consecutive non-``match`` alignment entries form one divergence region.
    Returns SCHEMA.md divergence dicts, ranked in trajectory order;
    ``downstream.caused_failure`` is True only for the earliest divergence
    and only when exactly one of the two agents failed.
    """
    regions: list[list[dict]] = []
    current: list[dict] = []
    for entry in alignment:
        if entry["op"] == "match":
            if current:
                regions.append(current)
                current = []
        else:
            current.append(entry)
    if current:
        regions.append(current)

    one_failed = a.outcome.success != b.outcome.success
    divergences: list[dict] = []
    for rank, region in enumerate(regions, start=1):
        is_trailing = alignment.index(region[-1]) >= len(alignment) - 2
        kind = _classify_kind(region, a, b, is_trailing)
        caused = one_failed and rank == 1
        divergences.append(
            {
                "rank": rank,
                "a_index": _first_index(region, "a_index"),
                "b_index": _first_index(region, "b_index"),
                "kind": kind,
                "summary": _summary(region, a, b, kind),
                "downstream": _downstream(region, alignment, a, b, caused),
            }
        )
    return divergences
