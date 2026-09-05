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
from .counterfactual import counterfactual
from .diagnosis import diagnose
from .divergence import find_divergences
from .metrics import metrics_delta
from .semantic import semantic_analysis
from .steps_eval import answer_eval, step_eval
from .success import success_analysis
from .tooldiff import TOOLISH_TYPES, tool_diff
from .trace import Trajectory
from .efficiency import compare_efficiency
from .reasoning import read_trace
from .process import compare_process
from .tradeoff import pair_tradeoff
from .shapley import shapley_attribution
from .uncertainty import analyze as analyze_uncertainty
from .verdict import verdict_card
from .internals import internals_analysis
from .feedback import feedback_signal

#: the template line containing this marker is replaced wholesale.
DATA_MARKER = "window.DEEPCOMPARE_DATA"


def _cite_internals(report: dict) -> None:
    """Attach the decisive step's internal signature to the leading
    hypothesis as observable evidence (recorded state), score untouched."""
    internals = report.get("internals") or {}
    decisive = internals.get("decisive")
    diagnosis = report.get("diagnosis") or {}
    if not decisive or not decisive.get("exclusive_features") or not diagnosis.get("leading"):
        return
    items = diagnosis.setdefault("evidence", [])
    eid = f"E{len(items) + 1}"
    labels = ", ".join(decisive["signature"])
    items.append({
        "id": eid, "type": "metric", "path": "internals.decisive.exclusive_features",
        "value": len(decisive["exclusive_features"]),
        "signal": f"features active at the decisive step and not at its counterpart: {labels}",
        "basis": ("recorded model internals" + (" (SYNTHETIC demo labels)"
                  if internals.get("synthetic") else "")),
        "evidence_class": "observable",
    })
    for h in diagnosis.get("hypotheses", []):
        if h.get("id") == diagnosis["leading"]:
            h.setdefault("supports", []).append(eid)
            classes = h.get("evidence_classes") or {}
            classes["observable"] = classes.get("observable", 0) + 1
            h["evidence_classes"] = classes
            h["internal_signature"] = decisive["signature"]
            break


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

    # Step-level evaluation (SCHEMA.md v5).  Root outputs for propagation:
    # the failing side's root_cause_step output, and — for the other side —
    # the output of its aligned counterpart step (None if there is none,
    # which yields propagation 0.0 for that side).
    root_output_a: Optional[str] = None
    root_output_b: Optional[str] = None
    failed_side = attribution["failed_agent"]
    root = attribution["root_cause_step"]
    if failed_side is not None and root is not None:
        own_key = f"{failed_side}_index"
        other_side = "b" if failed_side == "a" else "a"
        other_key = f"{other_side}_index"
        failed_traj, other_traj = (a, b) if failed_side == "a" else (b, a)
        failed_out: Optional[str] = failed_traj.steps[root].output
        other_out: Optional[str] = None
        for entry in alignment:
            if entry[own_key] == root and entry[other_key] is not None:
                other_out = other_traj.steps[entry[other_key]].output
                break
        if failed_side == "a":
            root_output_a, root_output_b = failed_out, other_out
        else:
            root_output_a, root_output_b = other_out, failed_out
    for entry in alignment:
        evaluation = step_eval(entry, a, b, root_output_a, root_output_b)
        if evaluation is not None:
            entry["eval"] = evaluation
    report = {
        # the expected answer rides along so a replay can grade its rollouts
        "task": {"id": a.task.id, "prompt": a.task.prompt, "expected": a.task.expected},
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
        "answer_eval": answer_eval(a, b),
        "metrics_delta": metrics_delta(a, b),
    }
    report["success_analysis"] = success_analysis(report, a, b)
    report["uncertainty"] = analyze_uncertainty(report, a, b)
    report["semantic"] = semantic_analysis(report, a, b)
    report["counterfactual"] = counterfactual(report, a, b)
    report["shapley"] = shapley_attribution(report, a, b)
    report["process"] = compare_process(a, b)
    report["tradeoff"] = pair_tradeoff(report)
    report["efficiency"] = compare_efficiency(a, b)
    report["diagnosis"] = diagnose(report, a, b)
    # recorded model internals (feature activations), read after the
    # diagnosis so the decisive step's internal signature can be named;
    # the section never changes a score — it adds evidence, labelled
    report["internals"] = internals_analysis(report, a, b)
    _cite_internals(report)
    # the reasoning layer: each run understood on its own, before and
    # independent of the comparison — what happened, what the answer rests
    # on, why it ended that way, what it means, what to take forward
    report["reading"] = {"a": read_trace(a), "b": read_trace(b)}
    # the five-line card the reader sees first; every line quotes a
    # section above, so it is computed last
    report["verdict_card"] = verdict_card(report)
    # the loop back: what this pair hands to an environment or the next
    # prompt — labels, a preference pair, suggestions; read-only over the report
    report["feedback"] = feedback_signal(report)
    return report


def render_html(
    reports: list[dict],
    aggregate: dict,
    template_path: Union[str, Path],
    out_path: Union[str, Path],
    fleet: Optional[dict] = None,
    extra: Optional[dict] = None,
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
    if extra:
        # Additional top-level sections (e.g. similarity/routing for the
        # selection view); never allowed to clobber the core keys.
        for key, value in extra.items():
            if key not in ("reports", "aggregate", "fleet"):
                data[key] = value
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
