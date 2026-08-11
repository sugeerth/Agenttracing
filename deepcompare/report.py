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
from typing import Optional, Union

from .align import align
from .attribution import attribute
from .divergence import find_divergences
from .metrics import metrics_delta
from .tooldiff import TOOLISH_TYPES, tool_diff
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

    # Attach tool-call diffs to alignment entries pairing tool-ish steps.
    for entry in alignment:
        if entry["a_index"] is None or entry["b_index"] is None:
            continue
        step_a, step_b = a.steps[entry["a_index"]], b.steps[entry["b_index"]]
        if step_a.type not in TOOLISH_TYPES and step_b.type not in TOOLISH_TYPES:
            continue
        if step_a.name == step_b.name and step_a.input == step_b.input:
            entry["tool_diff"] = {"same_tool": True, "identical": True}
        else:
            entry["tool_diff"] = tool_diff(step_a, step_b)
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
    fleet: Optional[dict] = None,
) -> Path:
    """Render the viewer HTML by injecting report data into a template.

    Reads ``template_path``, finds the single line containing the marker
    ``window.DEEPCOMPARE_DATA``, and replaces that whole line with
    ``window.DEEPCOMPARE_DATA = <json>;`` (preserving the line's original
    indentation), where the JSON payload is
    ``{"reports": [...], "aggregate": {...}}`` (plus a ``"fleet"`` key when
    ``fleet`` is given, for N-agent fleet reports).  Writes the result to
    ``out_path`` and returns it.  Raises ``ValueError`` if the marker line is
    not found.
    """
    template_path = Path(template_path)
    out_path = Path(out_path)
    template = template_path.read_text(encoding="utf-8")

    data: dict = {"reports": reports, "aggregate": aggregate}
    if fleet is not None:
        data["fleet"] = fleet
    payload = json.dumps(data, ensure_ascii=False)
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
