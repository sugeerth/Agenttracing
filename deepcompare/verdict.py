"""The verdict card: five lines that answer the reader's first questions.

Who won and who failed; why (the decisive step and its mechanism, in
the trace's own words when the step carries a note); what the outcome
cost; what to do next; and how sure the engine is.  Every line quotes
an existing section of the report — the diagnosis, the trade-off, the
reading, the confidence — so the card can never say something the
report does not.  A line with nothing to say is not emitted: no
"avoided 0 steps", no "+0 tokens".

The card is computed once, stored on the report under ``verdict_card``,
and quoted verbatim by the CLI, the HTML page and the narrator's brief.
"""

from __future__ import annotations

from typing import Optional


def _side_name(report: dict, side: str) -> str:
    return report[side]["agent"]["name"]


def _spend_phrase(delta: dict) -> Optional[str]:
    """``235 tokens and 3.4s`` from a delta dict — only the non-zero parts;
    ``None`` when nothing differed."""
    parts = []
    tokens = abs(int(delta.get("tokens") or 0))
    latency = abs(float(delta.get("latency_s") or 0.0))
    steps = abs(int(delta.get("steps") or 0))
    if tokens:
        parts.append(f"{tokens:,} tokens")
    if latency >= 0.05:
        parts.append(f"{latency:.1f}s")
    if steps:
        parts.append(f"{steps} step{'s' if steps != 1 else ''}")
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def verdict_card(report: dict) -> dict:
    """Build the card from a finished pair report.  Pure; no I/O."""
    task = report["task"]["id"]
    a, b = _side_name(report, "a"), _side_name(report, "b")
    ok_a = bool(report["a"]["outcome"]["success"])
    ok_b = bool(report["b"]["outcome"]["success"])
    diagnosis = report.get("diagnosis") or {}
    tradeoff = report.get("tradeoff") or {}
    reading = report.get("reading") or {}
    lines: list[dict] = []

    # --- verdict
    if ok_a and ok_b:
        dominant = tradeoff.get("dominant")
        verdict = f"Both solved {task}."
        if dominant:
            verdict = f"Both solved {task}; {dominant} did it cheaper."
    elif ok_a != ok_b:
        winner, loser = (a, b) if ok_a else (b, a)
        verdict = f"{winner} solved {task}; {loser} failed."
    else:
        verdict = f"Neither solved {task}."
    lines.append({"key": "verdict", "text": verdict, "source": "outcome"})

    # --- cause: the decisive step in the trace's own words when it has them
    decisive = diagnosis.get("decisive_step") or {}
    subject = diagnosis.get("subject")
    lead = next((h for h in diagnosis.get("hypotheses", [])
                 if h.get("id") == diagnosis.get("leading")), None)
    if decisive.get("step") is not None and subject in ("a", "b"):
        step_idx = decisive["step"]
        steps = report[subject]["steps"]
        step = steps[step_idx] if 0 <= step_idx < len(steps) else {}
        category = (lead or {}).get("category") or step.get("type") or "step"
        verification = decisive.get("verification") or "hypothesized"
        note = (step.get("note") or "").strip()
        if note:
            mechanism = note
            basis = "the trace's own note"
        elif lead is not None:
            mechanism = lead.get("statement") or ""
            basis = "the leading hypothesis"
        else:
            mechanism = "the divergent decision"
            basis = "the divergence"
        lines.append({
            "key": "cause",
            "text": f"step {step_idx} of {_side_name(report, subject)} "
                    f"({category}, {verification}): {mechanism}",
            "source": f"diagnosis.decisive_step; mechanism from {basis}",
            "step": step_idx, "side": subject})
    elif diagnosis.get("verdict"):
        lines.append({"key": "cause", "text": diagnosis["verdict"],
                      "source": "diagnosis.verdict"})

    # --- cost
    delta = tradeoff.get("spend_delta_b_minus_a") or {}
    phrase = _spend_phrase(delta)
    if phrase:
        b_cheaper = (delta.get("tokens") or 0) < 0 or (
            not delta.get("tokens") and (delta.get("latency_s") or 0) < 0)
        cheaper, dearer = (b, a) if b_cheaper else (a, b)
        cheaper_ok = ok_b if b_cheaper else ok_a
        dearer_ok = ok_a if b_cheaper else ok_b
        text = f"{cheaper} spent {phrase} less than {dearer}"
        if not cheaper_ok and dearer_ok:
            text += " — faster to nothing"
        elif cheaper_ok and dearer_ok:
            text += " for the same result"
        lines.append({"key": "cost", "text": text,
                      "source": "tradeoff.spend_delta_b_minus_a"})

    # --- fix: the first located next action of the side that failed (or
    # the costlier side when both passed)
    fix_side = None
    if ok_a != ok_b:
        fix_side = "b" if ok_a else "a"
    elif ok_a and ok_b and phrase:
        fix_side = "a" if b_cheaper else "b"
    elif not ok_a and not ok_b:
        fix_side = subject if subject in ("a", "b") else "a"
    if fix_side:
        actions = (reading.get(fix_side) or {}).get("take_forward") or []
        first = next((t for t in actions if t.get("at_step") is not None), None) \
            or (actions[0] if actions else None)
        if first:
            where = (f"at step {first['at_step']}: " if first.get("at_step") is not None
                     else "")
            lines.append({"key": "fix",
                          "text": f"{_side_name(report, fix_side)} — {where}{first['instead']}",
                          "source": f"reading.{fix_side}.take_forward[0]",
                          "step": first.get("at_step"), "side": fix_side})

    # --- confidence
    conf = diagnosis.get("confidence") or {}
    if conf.get("level"):
        text = f"{conf['level']} — {conf.get('basis') or ''}".rstrip(" —")
        if decisive.get("step") is not None:
            text += f"; the decisive step is {decisive.get('verification') or 'hypothesized'}"
            if (decisive.get("verification") or "hypothesized") == "hypothesized":
                text += ", not replay-verified"
        lines.append({"key": "confidence", "text": text,
                      "source": "diagnosis.confidence"})
    return {"version": 1, "lines": lines}


_LABELS = {"verdict": "VERDICT", "cause": "CAUSE", "cost": "COST",
           "fix": "FIX", "confidence": "CONF"}


def format_verdict_card(card: dict, width: int = 8) -> str:
    """The card as text: one labelled line per entry."""
    return "\n".join(f"{_LABELS.get(line['key'], line['key'].upper()):<{width}} "
                     f"{line['text']}" for line in card.get("lines", []))
