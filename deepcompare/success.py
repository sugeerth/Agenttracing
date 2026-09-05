"""Success analysis for DeepCompare AI (SCHEMA.md v6) — the positive mirror.

Failure attribution explains what went wrong; :func:`success_analysis`
explains what the winner did *right*, one winning decision per divergence
region, with the loser's downstream extras re-read as what the winner
avoided.  :func:`playbook` generalizes winning decisions across a batch into
"what good looks like" habits.
"""

from __future__ import annotations

import re
from typing import Optional

from .divergence import _describe, _snippet
from .trace import Step, Trajectory

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DECISION_LIMIT = 90
_URL_RE = re.compile(r"https?://([^/\s)\"',]+)")
_SOURCE_TYPES = frozenset({"search", "retrieve", "read"})

#: kind -> template fragment for a winning decision's "why".
_WHY_TEMPLATES = {
    "retrieval": "the primary source held the correct figure",
    "tool_selection": "the right tool for the data actually in hand",
    "tool_execution": "correct tool arguments produced a trustworthy result",
    "planning": "a sound plan up front kept the path short",
    "reasoning": "verified reasoning held up downstream",
    "stopping": "stopped once the evidence sufficed",
}

#: kind -> playbook habit text.
_HABITS = {
    "retrieval": "Prefer primary/official sources over commentary/aggregators",
    "tool_execution": "Validate tool arguments against a known sample before trusting output",
    "tool_selection": "Match the tool to the data actually in hand",
    "stopping": "Stop once evidence suffices — corroborate at most twice",
    "efficiency": "Stop once evidence suffices — corroborate at most twice",
    "planning": "State a plan before acting",
    "reasoning": "Verify claims against the primary document before asserting",
}


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    return _SENTENCE_SPLIT.split(text)[0]


def _decision_text(step: Step) -> str:
    """Cleaned first sentence of a step's input or output, whichever is more
    descriptive (longer), truncated for display."""
    sent_in = _first_sentence(step.input)
    sent_out = _first_sentence(step.output)
    best = sent_in if len(sent_in) >= len(sent_out) else sent_out
    if not best:
        best = step.name or step.type
    return _snippet(best, _DECISION_LIMIT)


def _loser_extras(downstream: dict, loser: str) -> tuple[int, int, float]:
    """(steps, tokens, latency) the loser spent extra; zeros when the
    downstream extras were on the winner's side."""
    if f"extra_steps_{loser}" in downstream:
        return (
            max(0, downstream[f"extra_steps_{loser}"]),
            max(0, downstream[f"extra_tokens_{loser}"]),
            max(0.0, downstream[f"extra_latency_s_{loser}"]),
        )
    return (0, 0, 0.0)


def _burden(divergences: list[dict], side: str) -> tuple[int, int, float]:
    """Total downstream extras attributed to one side across all divergences."""
    steps = tokens = 0
    latency = 0.0
    for div in divergences:
        s, t, lat = _loser_extras(div["downstream"], side)
        steps += s
        tokens += t
        latency += lat
    return steps, tokens, round(latency, 4)


def _winner_next_step(
    alignment: list[dict], div: dict, winner: str, loser: str, winner_traj: Trajectory
) -> Optional[int]:
    """For a one-sided region: the winner's first step index at/after it."""
    loser_key, winner_key = f"{loser}_index", f"{winner}_index"
    anchor = div[loser_key]
    seen = False
    for entry in alignment:
        if not seen and entry[loser_key] == anchor:
            seen = True
        if seen and entry[winner_key] is not None:
            return entry[winner_key]
    return len(winner_traj.steps) - 1 if winner_traj.steps else None


def success_analysis(report: dict, a: Trajectory, b: Trajectory) -> Optional[dict]:
    """Build the SCHEMA.md v6 ``success_analysis`` object for a report.

    Winner: the successful side when exactly one agent failed (basis
    "outcome"); the side with the smaller total downstream burden when both
    succeeded but diverged (basis "efficiency"); ``None`` when both failed,
    nothing diverged, or the burdens are equal.
    """
    divergences = report["divergences"]
    if not divergences:
        return None
    success_a, success_b = a.outcome.success, b.outcome.success
    if success_a != success_b:
        winner = "a" if success_a else "b"
        basis = "outcome"
    elif success_a and success_b:
        burden_a, burden_b = _burden(divergences, "a"), _burden(divergences, "b")
        if burden_a == burden_b:
            return None
        winner = "a" if burden_a < burden_b else "b"
        basis = "efficiency"
    else:  # both failed
        return None

    loser = "b" if winner == "a" else "a"
    winner_traj, loser_traj = (a, b) if winner == "a" else (b, a)
    winner_name = winner_traj.agent.name
    loser_name = loser_traj.agent.name
    alignment = report["alignment"]

    decisions: list[dict] = []
    for div in divergences:
        kind = div["kind"]
        w_idx = div[f"{winner}_index"]
        l_idx = div[f"{loser}_index"]
        loser_step = loser_traj.steps[l_idx] if l_idx is not None else None

        if w_idx is not None:
            winner_step: Optional[Step] = winner_traj.steps[w_idx]
            decision = _decision_text(winner_step)
            step_index = w_idx
        else:
            step_index = _winner_next_step(alignment, div, winner, loser, winner_traj)
            winner_step = (
                winner_traj.steps[step_index] if step_index is not None else None
            )
            nxt = _describe(winner_step) if winner_step is not None else "its final answer"
            decision = f"proceeded directly to its next step: {nxt}"

        if loser_step is not None:
            # For source-selection steps, name the actual source domain when
            # one is extractable — "financeblog.net" reads better than the
            # generic step name.
            domain = None
            if loser_step.type in _SOURCE_TYPES:
                for field in (loser_step.output, loser_step.input):
                    m = _URL_RE.search(field or "")
                    if m:
                        domain = m.group(1)
                        break
            if domain:
                counterpart = f'selected "{domain}" ({loser_name})'
            else:
                counterpart = f"{_describe(loser_step)} ({loser_name})"
        else:
            counterpart = f"no corresponding step ({loser_name})"

        why_parts: list[str] = []
        winner_poor = winner_step is not None and winner_step.quality in ("weak", "bad")
        if loser_step is not None and loser_step.quality in ("weak", "bad") and not winner_poor:
            why_parts.append(f"avoided a {loser_step.quality}-quality step")
        if loser_step is not None and loser_step.note:
            why_parts.append(f'counterpart note: "{_snippet(loser_step.note, 70)}"')
        why_parts.append(_WHY_TEMPLATES.get(kind, "held the better line"))

        steps_x, tokens_x, latency_x = _loser_extras(div["downstream"], loser)
        decisions.append(
            {
                "step_index": step_index,
                "agent": winner_name,
                "kind": kind,
                "decision": decision,
                "counterpart": counterpart,
                "why": "; ".join(why_parts),
                "impact": {
                    "avoided_extra_steps": steps_x,
                    "avoided_tokens": tokens_x,
                    "avoided_latency_s": round(latency_x, 4),
                    "avoided_failure": bool(div["downstream"]["caused_failure"]),
                },
            }
        )

    total_steps = sum(d["impact"]["avoided_extra_steps"] for d in decisions)
    total_tokens = sum(d["impact"]["avoided_tokens"] for d in decisions)
    total_latency = round(sum(d["impact"]["avoided_latency_s"] for d in decisions), 4)
    any_failure = any(d["impact"]["avoided_failure"] for d in decisions)

    key = decisions[0]
    sentences = [
        f"{winner_name} won on {basis} over {loser_name}.",
        f'Key decision at step {key["step_index"]} ({key["kind"]}): '
        f'"{key["decision"]}" versus {key["counterpart"]}.',
    ]
    saved = []
    if total_steps:
        saved.append(f"{total_steps} extra step(s)")
    if total_tokens:
        saved.append(f"{total_tokens:,} tokens")
    if total_latency:
        saved.append(f"{total_latency:g}s of latency")
    # a sentence with nothing to say is not emitted: no "avoided 0 steps"
    if saved and any_failure:
        sentences.append("Altogether its choices avoided "
                         + ", ".join(saved) + ", and the failure that followed.")
    elif saved:
        sentences.append("Altogether its choices avoided " + ", ".join(saved) + ".")
    elif any_failure:
        sentences.append("Altogether its choices avoided the failure that followed.")

    return {
        "winner": winner,
        "basis": basis,
        "winning_decisions": decisions,
        "narrative": " ".join(sentences),
    }


def playbook(reports: list[dict]) -> list[dict]:
    """Generalize winning decisions across reports into SCHEMA.md habits.

    Groups winning decisions by (kind, winning agent), sums avoided
    steps/tokens/latency/failures, and drops zero-impact groups.  Ordered by
    avoided failures desc, then avoided tokens desc, then (kind, agent).
    """
    groups: dict[tuple[str, str], dict] = {}
    for report in reports:
        sa = report.get("success_analysis")
        if not sa:
            continue
        for decision in sa["winning_decisions"]:
            key = (decision["kind"], decision["agent"])
            g = groups.setdefault(
                key,
                {"tasks": set(), "steps": 0, "tokens": 0, "latency": 0.0, "failures": 0},
            )
            g["tasks"].add(report["task"]["id"])
            impact = decision["impact"]
            g["steps"] += impact["avoided_extra_steps"]
            g["tokens"] += impact["avoided_tokens"]
            g["latency"] += impact["avoided_latency_s"]
            g["failures"] += 1 if impact["avoided_failure"] else 0

    habits: list[dict] = []
    for (kind, agent), g in groups.items():
        if g["failures"] == 0 and g["steps"] <= 0 and g["tokens"] <= 0 and g["latency"] <= 0:
            continue
        tasks = sorted(g["tasks"])
        impact_parts = []
        if g["failures"]:
            impact_parts.append(f"avoided {g['failures']} failure(s)")
        impact_parts.append(
            f"saved {g['tokens']:,} tokens and {round(g['latency'], 4):g}s"
        )
        habits.append(
            {
                "habit": _HABITS.get(kind, "Hold the better line at divergence points"),
                "kind": kind,
                "agents": [agent],
                "evidence": f"decided {len(tasks)} task(s) ({', '.join(tasks)})",
                "impact": ", ".join(impact_parts),
                "_sort": (-g["failures"], -g["tokens"], kind, agent),
            }
        )
    habits.sort(key=lambda h: h["_sort"])
    for habit in habits:
        del habit["_sort"]
    return habits
