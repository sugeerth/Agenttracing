"""External validation on Who&When (Zhang et al., 2025): convert the
public failure-attribution logs into SCHEMA traces and score the reading
layer's step localization against the human labels.

The dataset ships FAILING multi-agent logs only — no passing twin — so
the pairwise diagnoser cannot run; what is scored is the single-trace
reading (`agentdiff explain`): the step it points at first (the critical
error if one is declared, else the earliest observable finding, else the
first located next action) against the annotated ``mistake_step``, and
the agent at that step against ``mistake_agent``.  Two floors are
scored beside it — always the first step, and the expected hit rate of
a uniform guess — because a number without its floor is not a number.

The converter keeps the labels OUT of the trace (a sidecar JSON), so the
engine cannot read what it is being scored against.

    python demo/external/whowhen.py --data "path/to/Who&When" -o out/
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deepcompare.reasoning import read_trace  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

_TOOLISH = re.compile(r"(exitcode|code output|has successfully executed|execution result|"
                      r"```(?:python|bash|sh)|Traceback|stdout|stderr)", re.I)


def convert(log: dict, trace_id: str) -> tuple[dict, dict]:
    """One Who&When log → (SCHEMA trace, labels).  Messages become steps in
    order: the first is the plan, messages that carry executed-code output
    are tool calls, the last is the answer, everything else is reasoning."""
    history = log.get("history") or []
    steps = []
    for i, msg in enumerate(history):
        content = str(msg.get("content") or "")
        name = str(msg.get("name") or msg.get("role") or "agent")
        last = i == len(history) - 1
        if last:
            kind = "answer"
        elif i == 0:
            kind = "plan"
        elif _TOOLISH.search(content):
            kind = "tool_call"
        else:
            kind = "reason"
        step = {"index": i, "type": kind, "name": name,
                "input": content if kind != "tool_call" else content[:2000],
                "output": content if kind in ("tool_call", "answer") else "",
                "tokens": max(1, len(content) // 4), "latency_s": 0.0}
        if kind == "tool_call":
            step["effect"] = "read"
            step["error"] = bool(re.search(r"exitcode: [1-9]|Traceback|Error", content))
        steps.append(step)
    if not steps:
        raise ValueError("empty history")
    if steps[-1]["type"] != "answer":
        steps[-1]["type"] = "answer"
    answer = str(history[-1].get("content") or "")
    trace = {
        "schema_version": 1, "trace_id": trace_id,
        "agent": {"name": "multi-agent-system", "model": "who-and-when-log", "version": ""},
        "task": {"id": str(log.get("question_ID") or trace_id),
                 "prompt": str(log.get("question") or ""),
                 "expected": str(log.get("ground_truth") or "") or None},
        "outcome": {"success": False, "answer": answer, "score": 0.0,
                    "termination": "agent_stop"},
        "totals": {"input_tokens": sum(s["tokens"] for s in steps), "output_tokens": 0,
                   "cost_usd": 0.0, "latency_s": 0.0},
        "steps": steps,
        "budget": {"max_steps": len(steps)},
    }
    labels = {"mistake_step": int(log.get("mistake_step")),
              "mistake_agent": str(log.get("mistake_agent") or ""),
              "mistake_reason": str(log.get("mistake_reason") or ""),
              "steps": len(steps)}
    return trace, labels


def predict(reading: dict) -> tuple:
    """The step the reading points at first, and why."""
    critical = reading.get("critical_error") or {}
    if critical.get("step") is not None:
        return critical["step"], "critical_error"
    observable = [f for f in reading.get("what_it_means") or []
                  if f.get("evidence_class") == "observable" and f.get("steps")]
    if observable:
        return min(min(f["steps"]) for f in observable), "earliest observable finding"
    for t in reading.get("take_forward") or []:
        if t.get("at_step") is not None:
            return t["at_step"], "first located next action"
    return None, "no prediction"


def score(rows: list) -> dict:
    n = len(rows)
    predicted = [r for r in rows if r["predicted"] is not None]
    exact = sum(1 for r in predicted if r["predicted"] == r["truth"])
    within = sum(1 for r in predicted if abs(r["predicted"] - r["truth"]) <= 1)
    agent = sum(1 for r in predicted if r["predicted_agent"] == r["truth_agent"])
    first = sum(1 for r in rows if r["truth"] == 0)
    uniform = sum(1.0 / r["steps"] for r in rows)
    return {
        "logs": n, "predicted": len(predicted), "abstained": n - len(predicted),
        "step_exact": exact, "step_within_1": within, "agent_exact": agent,
        "step_exact_rate": round(exact / n, 4) if n else None,
        "step_within_1_rate": round(within / n, 4) if n else None,
        "agent_exact_rate": round(agent / n, 4) if n else None,
        "floors": {
            "always_first_step": {"hits": first, "rate": round(first / n, 4) if n else None},
            "uniform_guess_expected": {"hits": round(uniform, 2),
                                       "rate": round(uniform / n, 4) if n else None},
        },
        "by_reason": {},
        "note": ("single failing logs, no passing twin: this scores the reading "
                 "layer's first pointer, not the pairwise diagnoser; the labels "
                 "were never on the trace"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True, help="the Who&When directory")
    parser.add_argument("-o", "--output", default="out_whowhen")
    args = parser.parse_args(argv)
    out = Path(args.output)
    (out / "traces").mkdir(parents=True, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.data, "*", "*.json")))
    rows, labels_all, reasons = [], {}, {}
    for path in files:
        subset = os.path.basename(os.path.dirname(path))
        stem = f"{subset.lower().replace('-', '_')}_{Path(path).stem}"
        try:
            log = json.loads(Path(path).read_text(encoding="utf-8"))
            trace, labels = convert(log, stem)
            traj = Trajectory.from_dict(json.loads(json.dumps(trace)))
        except (ValueError, KeyError, TypeError) as exc:
            print(f"skip {path}: {exc}", file=sys.stderr)
            continue
        (out / "traces" / f"{stem}.json").write_text(json.dumps(trace, indent=1),
                                                     encoding="utf-8")
        labels_all[stem] = labels
        reading = read_trace(traj)
        pred, why = predict(reading)
        pred_agent = traj.steps[pred].name if pred is not None and pred < len(traj.steps) else None
        row = {"trace": stem, "subset": subset, "steps": len(traj.steps),
               "truth": labels["mistake_step"], "truth_agent": labels["mistake_agent"],
               "predicted": pred, "predicted_agent": pred_agent, "why": why}
        rows.append(row)
        reasons[why] = reasons.get(why, 0) + 1
    (out / "labels.json").write_text(json.dumps(labels_all, indent=1), encoding="utf-8")
    result = score(rows)
    result["by_reason"] = reasons
    result["by_subset"] = {}
    for subset in sorted({r["subset"] for r in rows}):
        part = [r for r in rows if r["subset"] == subset]
        result["by_subset"][subset] = score(part)
        result["by_subset"][subset].pop("by_reason", None)
    result["rows"] = rows
    (out / "whowhen_validation.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"Who&When: {result['logs']} failing logs converted; reading layer pointed at a "
          f"step in {result['predicted']}")
    print(f"  step exact  {result['step_exact']}/{result['logs']} ({result['step_exact_rate']})"
          f"   within ±1  {result['step_within_1']}/{result['logs']} ({result['step_within_1_rate']})")
    print(f"  agent exact {result['agent_exact']}/{result['logs']} ({result['agent_exact_rate']})")
    f = result["floors"]
    print(f"  floors: always-first-step {f['always_first_step']['hits']}/{result['logs']} "
          f"({f['always_first_step']['rate']}); uniform guess expected "
          f"{f['uniform_guess_expected']['hits']}/{result['logs']} ({f['uniform_guess_expected']['rate']})")
    for subset, part in result["by_subset"].items():
        print(f"  {subset}: step exact {part['step_exact']}/{part['logs']}, within ±1 "
              f"{part['step_within_1']}/{part['logs']}, agent {part['agent_exact']}/{part['logs']}, "
              f"first-step floor {part['floors']['always_first_step']['hits']}/{part['logs']}")
    print(f"  pointer basis: {reasons}")
    print(f"  {result['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
