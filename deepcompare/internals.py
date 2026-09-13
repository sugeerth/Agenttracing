"""Model internals across the pair: which recorded features separate the
runs, and what fires at the decisive step.

Pure engine code: it reads ``step.model.internals`` (recorded by the
harness through Neuronpedia, or synthetic and labelled so) and computes
per aligned row the features exclusive to each side and the activation
deltas of the shared ones.  At the decisive step it reports the
*internal signature* — the features the failing step activated that its
counterpart did not — with the same honesty every other section keeps:
an activation difference is evidence that the runs' internal states
differed there, not evidence that the feature caused the outcome.
"""

from __future__ import annotations

from typing import Optional

from .trace import Trajectory

CAUSAL_NOTE = ("an activation difference is an observation of internal state; only "
               "an intervention — steering or ablating the feature and replaying — "
               "can show it caused the outcome")


def _features(step) -> dict:
    model = getattr(step, "model", None) or {}
    internals = model.get("internals") if isinstance(model, dict) else None
    if not internals or not isinstance(internals.get("features"), list):
        return {}
    return {int(f["index"]): f for f in internals["features"]
            if isinstance(f, dict) and f.get("index") is not None}


def _provenance(traj: Trajectory) -> Optional[dict]:
    for step in traj.steps:
        model = step.model or {}
        internals = model.get("internals") if isinstance(model, dict) else None
        if internals:
            return {"model": internals.get("model"), "sae": internals.get("sae"),
                    "source": internals.get("source")}
    return None


def _row_diff(fa: dict, fb: dict) -> dict:
    only_a = [fa[i] for i in fa if i not in fb]
    only_b = [fb[i] for i in fb if i not in fa]
    shared = []
    for i in fa:
        if i in fb:
            shared.append({"index": i, "label": fa[i].get("label") or fb[i].get("label"),
                           "url": fa[i].get("url"),
                           "activation_a": fa[i].get("activation"),
                           "activation_b": fb[i].get("activation"),
                           "delta_b_minus_a": round((fb[i].get("activation") or 0.0)
                                                    - (fa[i].get("activation") or 0.0), 4)})
    only_a.sort(key=lambda f: -(f.get("activation") or 0.0))
    only_b.sort(key=lambda f: -(f.get("activation") or 0.0))
    shared.sort(key=lambda f: -abs(f["delta_b_minus_a"]))
    return {"only_a": only_a, "only_b": only_b, "shared": shared}


def internals_analysis(report: dict, a: Trajectory, b: Trajectory) -> dict:
    """The report section.  ``available`` is False with a reason when no
    step of either run carries internals."""
    prov_a, prov_b = _provenance(a), _provenance(b)
    if not prov_a and not prov_b:
        return {"available": False,
                "reason": "no step carries model internals; record them with the "
                          "harness's Neuronpedia hook (or a scripted SAE) to see "
                          "which features separate the runs"}
    rows = []
    for i, row in enumerate(report.get("alignment") or []):
        ia, ib = row.get("a_index"), row.get("b_index")
        fa = _features(a.steps[ia]) if ia is not None and ia < len(a.steps) else {}
        fb = _features(b.steps[ib]) if ib is not None and ib < len(b.steps) else {}
        if not fa and not fb:
            continue
        diff = _row_diff(fa, fb)
        rows.append({"row": i, "a_index": ia, "b_index": ib,
                     "features_a": len(fa), "features_b": len(fb),
                     "only_a": diff["only_a"][:6], "only_b": diff["only_b"][:6],
                     "shared": diff["shared"][:6]})
    decisive = None
    diagnosis = report.get("diagnosis") or {}
    dec = diagnosis.get("decisive_step") or {}
    side = diagnosis.get("subject")
    if dec.get("step") is not None and side in ("a", "b"):
        other = "b" if side == "a" else "a"
        match = next((r for r in rows if r.get(f"{side}_index") == dec["step"]), None)
        if match is not None:
            exclusive = match["only_" + side]
            counterpart = match["only_" + other]
            decisive = {
                "side": side, "step": dec["step"], "row": match["row"],
                "counterpart_step": match.get(f"{other}_index"),
                "exclusive_features": exclusive,
                "counterpart_only": counterpart,
                "shared": match["shared"],
                "signature": [f.get("label") or f"feature {f['index']}" for f in exclusive[:3]],
                "note": CAUSAL_NOTE,
            }
    provenance = prov_a or prov_b
    synthetic = str((provenance or {}).get("source", "")).startswith("synthetic")
    return {
        "available": True,
        "provenance": provenance,
        "synthetic": synthetic,
        "rows": rows,
        "decisive": decisive,
        "note": (("SYNTHETIC internals: labels invented to exercise the view, not "
                  "recorded from a model. ") if synthetic else "")
                + CAUSAL_NOTE,
    }
