"""Report building and HTML rendering for DeepCompare AI.

:func:`compare` produces the full per-task comparison report defined in
SCHEMA.md (alignment, divergences, attribution, metrics_delta).
:func:`render_html` injects ``{"reports": [...], "aggregate": {...}}`` into a
viewer template by replacing the single line containing the marker
``window.DEEPCOMPARE_DATA`` with ``window.DEEPCOMPARE_DATA = <json>;``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .align import align
from .attribution import attribute
from .divergence import find_divergences
from .metrics import metrics_delta
from .trace import Trajectory

#: the template line containing this marker is replaced wholesale.
DATA_MARKER = "window.DEEPCOMPARE_DATA"


def compare(a: Trajectory, b: Trajectory) -> dict:
    """Compare two trajectories on the same task.

    Returns the SCHEMA.md comparison report dict.  Raises ``ValueError`` if
    the trajectories are not for the same task id.
    """
    if a.task.id != b.task.id:
        raise ValueError(
            f"cannot compare trajectories for different tasks: "
            f"{a.task.id!r} vs {b.task.id!r}"
        )
    alignment = align(a, b)
    divergences = find_divergences(a, b, alignment)
    attribution = attribute(a, b, alignment, divergences)
    return {
        "task": {"id": a.task.id, "prompt": a.task.prompt},
        "a": {
            "agent": a.agent.to_dict(),
            "outcome": a.outcome.to_dict(),
            "totals": a.totals.to_dict(),
            "steps": [s.to_dict() for s in a.steps],
        },
        "b": {
            "agent": b.agent.to_dict(),
            "outcome": b.outcome.to_dict(),
            "totals": b.totals.to_dict(),
            "steps": [s.to_dict() for s in b.steps],
        },
        "alignment": alignment,
        "divergences": divergences,
        "attribution": attribution,
        "metrics_delta": metrics_delta(a, b),
    }


def render_html(
    reports: list[dict],
    aggregate: dict,
    template_path: Union[str, Path],
    out_path: Union[str, Path],
) -> Path:
    """Render the viewer HTML by injecting report data into a template.

    Reads ``template_path``, finds the single line containing the marker
    ``window.DEEPCOMPARE_DATA``, and replaces that whole line with
    ``window.DEEPCOMPARE_DATA = <json>;`` (preserving the line's original
    indentation), where the JSON payload is
    ``{"reports": [...], "aggregate": {...}}``.  Writes the result to
    ``out_path`` and returns it.  Raises ``ValueError`` if the marker line is
    not found.
    """
    template_path = Path(template_path)
    out_path = Path(out_path)
    template = template_path.read_text(encoding="utf-8")

    payload = json.dumps({"reports": reports, "aggregate": aggregate}, ensure_ascii=False)
    # Keep the payload safe inside a <script> block.
    payload = payload.replace("</", "<\\/")

    lines = template.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if DATA_MARKER in line:
            indent = line[: len(line) - len(line.lstrip())]
            newline = "\n" if line.endswith("\n") else ""
            lines[i] = f"{indent}{DATA_MARKER} = {payload};{newline}"
            break
    else:
        raise ValueError(
            f"template {template_path} has no line containing the marker "
            f"{DATA_MARKER!r}"
        )

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
