"""The ``compare`` command: one pair of trace files — the report JSON,
optionally a page for the pair, and the terminal summary (verdict card,
first divergence, attribution, the diagnosis that ranks it among the
other hypotheses, recommendations)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..recommend import recommend
from ..report import compare, render_html
from ..trace import Trajectory
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("compare", help="compare one pair of trace files")
    parser.add_argument("a", help="trajectory JSON for agent A")
    parser.add_argument("b", help="trajectory JSON for agent B")
    parser.add_argument("-o", "--output", help="write the comparison report JSON here")
    parser.add_argument("--html", default=None,
                        help="also write a self-contained report page for this pair")
    parser.set_defaults(func=run)


def _load(path: str) -> Trajectory:
    return Trajectory.from_json(path)


def _fmt_outcome(side: str, report_side: dict) -> str:
    outcome = report_side["outcome"]
    status = "SUCCESS" if outcome["success"] else "FAILURE"
    return f"  {side}: {report_side['agent']['name']:<20} {status:<8} answer: {outcome['answer'][:70]}"


def _print_summary(report: dict) -> None:
    card = report.get("verdict_card")
    if card and card.get("lines"):
        from ..verdict import format_verdict_card
        print(format_verdict_card(card))
        print()
    print(f"Task: {report['task']['id']}")
    print(f"Prompt: {report['task']['prompt'][:100]}")
    print(_fmt_outcome("A", report["a"]))
    print(_fmt_outcome("B", report["b"]))

    delta = report["metrics_delta"]
    print("Metrics (A vs B):")
    for key in ("steps", "tokens", "cost_usd", "latency_s", "tool_calls", "searches"):
        pair = delta[key]
        print(f"  {key:<10} a={pair['a']:<10g} b={pair['b']:<10g}")

    divergences = report["divergences"]
    if divergences:
        first = divergences[0]
        print(f"Divergences: {len(divergences)}")
        print(
            f"  #1 [{first['kind']}] at a_index={first['a_index']} "
            f"b_index={first['b_index']}"
        )
        print(f"     {first['summary']}")
        print(f"     downstream: {json.dumps(first['downstream'])}")
    else:
        print("Divergences: none (trajectories fully match)")

    attribution = report["attribution"]
    print("Attribution:")
    print(f"  failed_agent: {attribution['failed_agent']}")
    if attribution["failed_agent"] is not None:
        print(f"  root_cause_step: {attribution['root_cause_step']}")
        print(f"  chain: {attribution['chain']}")
        print(f"  category: {attribution['category']}")
    print(f"  {attribution['explanation']}")

    diagnosis = report.get("diagnosis") or {}
    hypotheses = [h for h in diagnosis.get("hypotheses", [])
                  if h.get("status") != "merged"]
    if hypotheses:
        # The adjudication, right after the single story it adjudicates:
        # attribution above is one hypothesis among these, not the answer.
        print("Diagnosis (attribution is one hypothesis; this ranks them all):")
        print(f"  {diagnosis['verdict']}")
        for h in hypotheses[:4]:
            kind = h["kind"] + (f":{h['flag']}" if h.get("flag") else "")
            score = "—" if h["score"] is None else f"{h['score']:.2f}"
            print(f"  {h['id']} [{h['status']:>9}] {score}  {kind}: "
                  f"{h['statement']}")
        for clash in diagnosis.get("contradictions", []):
            print(f"  ! {clash}")
        lead = next((h for h in hypotheses
                     if h["id"] == diagnosis.get("leading")), None)
        if lead is not None and lead.get("discriminator"):
            print(f"  to settle it: {lead['discriminator']}")
        conf = diagnosis.get("confidence") or {}
        if conf.get("basis"):
            print(f"  confidence: {conf.get('level')} — {conf['basis']}")

    sa = report.get("success_analysis")
    if sa:
        print("Success analysis:")
        print(f"  {sa['narrative']}")

    recs = recommend([report])
    if recs:
        print("Recommendations:")
        for rec in recs:
            print(f"  [{rec['severity']}/{rec['category']}] {rec['agent']} — {rec['finding']}")
            print(f"    suggested prompt: {rec['suggested_prompt']}")
            print(f"    expected gain: {rec['expected_gain']}")


def run(args: argparse.Namespace) -> int:
    try:
        a = _load(args.a)
        b = _load(args.b)
        report = compare(a, b)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {out}")
    if getattr(args, "html", None):
        html_out = Path(args.html)
        html_out.parent.mkdir(parents=True, exist_ok=True)
        render_html([report], {}, DEFAULT_TEMPLATE, html_out)
        print(f"Wrote {html_out}")
    _print_summary(report)
    return 0
