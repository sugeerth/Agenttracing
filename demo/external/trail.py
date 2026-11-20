"""External validation on TRAIL (Deshpande et al., 2025): convert the public OpenInference span trees of GAIA
and SWE-bench agent runs into SCHEMA traces, and score the reading layer's step localization against the
human-annotated error spans.

TRAIL's traces are span trees, and an annotation names the span where each error is (``location``). The LLM and
TOOL spans become the trace's steps in time order. The AGENT and CHAIN spans that hold them are structure, not
steps. An error located on a container span is placed on its first step. The scored target is the first
annotated error, the step where the fault enters, as Who&When's ``mistake_step`` is. A second score counts a
pointer that lands on any annotated error.

The scoring is the same as on Who&When (``whowhen.predict`` and ``whowhen.reading_order``), beside the same two
floors: always the first step, and the expected hits of a uniform guess. TRAIL publishes no pass/fail grade.
Every scored trace carries at least one annotated error, so each is recorded as failing
(``outcome.success`` False), and the result says that is an assumption. The labels stay out of the trace, in a
sidecar JSON.

    python demo/external/trail.py --data path/to/trail-benchmark/benchmarking -o out/
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdiff.conformal import evaluate, rank_of  # noqa: E402
from agentdiff.reasoning import read_trace  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402
from whowhen import decided_by, predict, reading_order  # noqa: E402

SUBSETS = {"GAIA": "processed_annotations_gaia", "SWE Bench": "processed_annotations_swe_bench"}
_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")
_CLIP = 4000


def _seconds(text) -> float:
    m = _DURATION.fullmatch(str(text or ""))
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def _flat(spans: list) -> list:
    """Every span of the tree, depth first, children in their recorded order."""
    out = []
    for sp in spans or []:
        out.append(sp)
        out.extend(_flat(sp.get("child_spans")))
    return out


def _last_message(attrs: dict, side: str) -> str:
    """The highest-numbered ``llm.<side>_messages.N.message.content``: the newest input, or the reply."""
    best, text = -1, ""
    for key, value in attrs.items():
        m = re.fullmatch(rf"llm\.{side}_messages\.(\d+)\.message\.content", key)
        if m and int(m.group(1)) > best:
            best, text = int(m.group(1)), str(value)
    return text


def _step(sp: dict, index: int) -> dict:
    a = sp.get("span_attributes") or {}
    kind = a.get("openinference.span.kind")
    tokens = int(a.get("llm.token_count.completion") or 0) or max(1, len(str(a.get("output.value") or "")) // 4)
    step = {"index": index, "latency_s": round(_seconds(sp.get("duration")), 3), "tokens": tokens,
            "error": str(sp.get("status_code") or "").lower() == "error"}
    if kind == "TOOL":
        name = str(a.get("tool.name") or sp.get("span_name") or "tool")
        step.update(type="tool_call", name=name,
                    input=str(a.get("input.value") or "")[:_CLIP], output=str(a.get("output.value") or "")[:_CLIP],
                    effect="read")
    else:
        step.update(type="reason", name=str(sp.get("span_name") or "llm"),
                    input=_last_message(a, "input")[:_CLIP],
                    output=(_last_message(a, "output") or str(a.get("output.value") or ""))[:_CLIP])
    return step


def _ordered(spans: list) -> list:
    """The trace's steps in the order they happened, as (span, kind) pairs. An LLM or TOOL span is a step where it
    starts. A CHAIN span that carries an output (a code agent's "Step N", whose output is the execution log of the
    code its LLM call wrote) adds an execution step after everything beneath it."""
    out = []
    for sp in spans or []:
        kind = (sp.get("span_attributes") or {}).get("openinference.span.kind")
        if kind in ("LLM", "TOOL"):
            out.append((sp, kind))
        out.extend(_ordered(sp.get("child_spans")))
        if kind == "CHAIN" and (sp.get("span_attributes") or {}).get("output.value"):
            out.append((sp, "EXECUTION"))
    return out


def _execution(sp: dict, index: int) -> dict:
    log = str((sp.get("span_attributes") or {}).get("output.value") or "")
    return {"index": index, "type": "tool_call", "name": "code_execution", "input": str(sp.get("span_name") or ""),
            "output": log[:_CLIP], "effect": "read", "latency_s": 0.0, "tokens": max(1, len(log) // 4),
            "error": bool(re.search(r"(?m)^(Error|Code execution failed)|Traceback", log))}


def convert(doc: dict, annotation: dict, trace_id: str) -> tuple:
    """One TRAIL trace and its annotation → (SCHEMA trace, labels). Labels name step indices, never span ids
    the engine could see."""
    flat = _flat(doc.get("spans"))
    ordered = _ordered(doc.get("spans"))
    if not ordered:
        raise ValueError("no LLM, TOOL or executed step")
    at: dict = {}
    for i, (sp, _) in enumerate(ordered):
        at.setdefault(sp["span_id"], i)
    # an error on a container span lands on its first step: map every span to the earliest step beneath it

    def first_under(sp) -> Optional[int]:
        found = [i for i in [at.get(sp.get("span_id"))] + [first_under(c) for c in sp.get("child_spans") or []]
                 if i is not None]
        return min(found) if found else None
    under = {sp["span_id"]: first_under(sp) for sp in flat}
    steps = [_execution(sp, i) if kind == "EXECUTION" else _step(sp, i) for i, (sp, kind) in enumerate(ordered)]
    # a managed sub-agent hands its result back through final_answer too: only the run's last step is its answer
    steps[-1]["type"] = "answer"
    errors, unplaced = [], 0
    for e in annotation.get("errors") or []:
        i = under.get(e.get("location"))
        if i is None:
            unplaced += 1
            continue
        errors.append({"step": i, "category": e.get("category"), "impact": e.get("impact")})
    root = (doc.get("spans") or [{}])[0]
    trace = {
        "schema_version": 1, "trace_id": trace_id,
        "agent": {"name": "trail-agent", "model": "trail-log", "version": ""},
        "task": {"id": trace_id, "prompt": str(((root.get("span_attributes") or {}).get("input.value")) or "")[:_CLIP]},
        "outcome": {"success": False, "answer": steps[-1]["output"], "termination": "agent_stop"},
        "totals": {"input_tokens": 0, "output_tokens": sum(s["tokens"] for s in steps), "cost_usd": 0.0,
                   "latency_s": round(_seconds(root.get("duration")), 3)},
        "steps": steps,
    }
    labels = {"errors": errors, "unplaced_errors": unplaced, "steps": len(steps),
              "first_error": min((e["step"] for e in errors), default=None)}
    return trace, labels


def score(rows: list) -> dict:
    n = len(rows)
    predicted = [r for r in rows if r["predicted"] is not None]
    exact = sum(1 for r in predicted if r["predicted"] == r["truth"])
    within = sum(1 for r in predicted if abs(r["predicted"] - r["truth"]) <= 1)
    any_error = sum(1 for r in predicted if r["predicted"] in r["error_steps"])
    first = sum(1 for r in rows if r["truth"] == 0)
    first_any = sum(1 for r in rows if 0 in r["error_steps"])
    uniform = sum(1.0 / r["steps"] for r in rows)
    uniform_any = sum(len(set(r["error_steps"])) / r["steps"] for r in rows)
    rate = lambda k: round(k / n, 4) if n else None  # noqa: E731
    return {"traces": n, "predicted": len(predicted), "abstained": n - len(predicted),
            "first_error_exact": exact, "first_error_exact_rate": rate(exact),
            "first_error_within_1": within, "first_error_within_1_rate": rate(within),
            "any_error": any_error, "any_error_rate": rate(any_error),
            "floors": {"always_first_step": {"first_error": first, "any_error": first_any, "rate": rate(first)},
                       "uniform_guess_expected": {"first_error": round(uniform, 2), "any_error": round(uniform_any, 2),
                                                  "rate": rate(uniform)}}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True, help="TRAIL's benchmarking directory (data/ and the annotations)")
    parser.add_argument("-o", "--output", default="out_trail")
    args = parser.parse_args(argv)
    out = Path(args.output)
    (out / "traces").mkdir(parents=True, exist_ok=True)
    rows, labels_all, skipped = [], {}, {}
    for subset, ann_dir in SUBSETS.items():
        for path in sorted(glob.glob(os.path.join(args.data, "data", subset, "*.json"))):
            stem = f"{subset.lower().replace(' ', '_')}_{Path(path).stem}"
            ann_path = Path(args.data) / ann_dir / Path(path).name
            if not ann_path.exists():
                skipped["no annotation"] = skipped.get("no annotation", 0) + 1
                continue
            try:
                trace, labels = convert(json.loads(Path(path).read_text(encoding="utf-8")),
                                        json.loads(ann_path.read_text(encoding="utf-8")), stem)
                traj = Trajectory.from_dict(json.loads(json.dumps(trace)))
            except json.JSONDecodeError:
                skipped["not valid JSON"] = skipped.get("not valid JSON", 0) + 1
                continue
            except (ValueError, KeyError, TypeError) as exc:
                skipped[str(exc)] = skipped.get(str(exc), 0) + 1
                continue
            labels_all[stem] = labels
            if labels["first_error"] is None:
                skipped["no error placed on a step"] = skipped.get("no error placed on a step", 0) + 1
                continue
            (out / "traces" / f"{stem}.json").write_text(json.dumps(trace, indent=1), encoding="utf-8")
            reading = read_trace(traj)
            pred, why = predict(reading)
            error_steps = sorted({e["step"] for e in labels["errors"]})
            rows.append({"trace": stem, "subset": subset, "steps": len(traj.steps), "truth": labels["first_error"],
                         "error_steps": error_steps, "predicted": pred, "why": why,
                         "decided_by": decided_by(traj, pred),
                         "replay_rank": rank_of(reading_order(reading, traj), [labels["first_error"]])})
    (out / "labels.json").write_text(json.dumps(labels_all, indent=1), encoding="utf-8")
    result = score(rows)
    result["decided_by"] = score([dict(r, predicted=r["decided_by"]) for r in rows])
    result["decided_by"].pop("floors")
    result["skipped"] = skipped
    result["by_subset"] = {s: score([r for r in rows if r["subset"] == s]) for s in sorted({r["subset"] for r in rows})}
    reasons: dict = {}
    for r in rows:
        reasons[r["why"]] = reasons.get(r["why"], 0) + 1
    result["by_reason"] = reasons
    set_rows = [{"id": r["trace"], "rank": r["replay_rank"], "index_rank": r["truth"] + 1, "steps": r["steps"]}
                for r in rows]
    result["replay_sets"] = [evaluate(set_rows, a) for a in (0.5, 0.2, 0.1)]
    result["note"] = ("single annotated runs with no passing twin, recorded as failing because each carries an "
                      "annotated error (TRAIL publishes no pass/fail grade): this scores the reading layer's first "
                      "pointer against the first annotated error; the labels were never on the trace")
    result["rows"] = rows
    (out / "trail_validation.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    n = result["traces"]
    print(f"TRAIL: {n} annotated traces scored ({sum(skipped.values())} skipped: {skipped}); the reading pointed at "
          f"a step in {result['predicted']}")
    print(f"  first error exact {result['first_error_exact']}/{n} ({result['first_error_exact_rate']}), within ±1 "
          f"{result['first_error_within_1']}/{n} ({result['first_error_within_1_rate']}); on any annotated error "
          f"{result['any_error']}/{n} ({result['any_error_rate']})")
    dec = result["decided_by"]
    print(f"  the reasoning step that decided it: first error exact {dec['first_error_exact']}/{n} "
          f"({dec['first_error_exact_rate']}), within ±1 {dec['first_error_within_1']}/{n}; on any annotated error "
          f"{dec['any_error']}/{n} ({dec['any_error_rate']})")
    f = result["floors"]
    print(f"  floors: always-first-step {f['always_first_step']['first_error']}/{n} first error, "
          f"{f['always_first_step']['any_error']}/{n} any; uniform guess expected "
          f"{f['uniform_guess_expected']['first_error']}/{n} first error, {f['uniform_guess_expected']['any_error']}/{n} any")
    for subset, part in result["by_subset"].items():
        print(f"  {subset}: first error exact {part['first_error_exact']}/{part['traces']}, any error "
              f"{part['any_error']}/{part['traces']}, first-step floor {part['floors']['always_first_step']['first_error']}"
              f"/{part['traces']}")
    print(f"  pointer basis: {reasons}")
    for e in result["replay_sets"]:
        ro, so = e["replay_order"], e["step_order"]
        print(f"  replay set, {e['target']:.0%} target (held out n={ro['test']}, runs of {e['mean_steps']} steps): "
              f"reading order k={ro['k']} covers {ro['coverage']}, step order k={so['k']} covers {so['coverage']}")
    print(f"  {result['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
