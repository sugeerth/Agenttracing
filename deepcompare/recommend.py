"""Evidence-based recommendations for DeepCompare AI.

Turns comparison reports into actionable, data-driven advice: for each
failure mode or costly detour observed across the reports, emits a
recommendation dict with the agent it targets, a finding citing real numbers
(task ids, downstream step/token/latency costs), a paste-ready
``suggested_prompt`` addition instantiated with details from the diverging
steps, and a quantified ``expected_gain``.  Fully deterministic — no LLM.
"""

from __future__ import annotations

import re
from typing import Optional

#: severity sort order.
_SEVERITY_RANK = {"critical": 0, "moderate": 1, "minor": 2}

#: extra tokens above which a non-fatal detour is "moderate" rather than "minor".
MODERATE_TOKEN_THRESHOLD = 500

_KIND_LABELS = {
    "retrieval": "retrieval",
    "tool_selection": "tool-selection",
    "tool_execution": "tool-execution",
    "planning": "planning",
    "reasoning": "reasoning",
    "stopping": "stopping",
    "efficiency": "efficiency",
}


def _snippet(text: str, limit: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_URL_RE = re.compile(r"https?://([^/\s)\"',]+)")

#: step types whose divergence is a source-selection problem.
_SOURCE_TYPES = frozenset({"search", "retrieve", "read"})


def _source_label(step: Optional[dict]) -> str:
    """Best human label for the source a step used: the URL domain from its
    output/input when extractable, else the step name, else an input snippet."""
    if not step:
        return ""
    for field in ("output", "input"):
        m = _URL_RE.search(step.get(field, ""))
        if m:
            return m.group(1)
    return step.get("name") or _snippet(step.get("input", "")) or step.get("type", "")


def _fill_ctx(ctx: dict, own_step: Optional[dict], other_step: Optional[dict]) -> None:
    """Instantiate template context from the diverging step pair (own = the
    step of the agent the advice targets).  Tool names are only taken from
    real tool_call steps; source labels only from search/retrieve/read steps."""
    if own_step:
        if own_step["type"] in _SOURCE_TYPES and not ctx.get("bad_source"):
            ctx["bad_source"] = _source_label(own_step)
        if own_step["type"] == "tool_call" and not ctx.get("bad_tool"):
            ctx["bad_tool"] = own_step.get("name", "")
            ctx["tool"] = own_step.get("name", "")
    if other_step:
        if other_step["type"] in _SOURCE_TYPES and not ctx.get("good_source"):
            ctx["good_source"] = _source_label(other_step)
        if other_step["type"] == "tool_call" and not ctx.get("good_tool"):
            ctx["good_tool"] = other_step.get("name", "")


def _side_step(report: dict, side: Optional[str], index: Optional[int]) -> Optional[dict]:
    if side is None or index is None:
        return None
    steps = report[side]["steps"]
    return steps[index] if 0 <= index < len(steps) else None


def _other(side: str) -> str:
    return "b" if side == "a" else "a"


def _causal_divergence(report: dict) -> Optional[dict]:
    for div in report.get("divergences", []):
        if div["downstream"].get("caused_failure"):
            return div
    divs = report.get("divergences", [])
    return divs[0] if divs else None


def _side_extras(downstream: dict, side: str) -> tuple[int, int, float]:
    """(steps, tokens, latency) extras attributed to ``side``, else zeros."""
    if f"extra_steps_{side}" in downstream:
        return (
            max(0, downstream[f"extra_steps_{side}"]),
            max(0, downstream[f"extra_tokens_{side}"]),
            max(0.0, downstream[f"extra_latency_s_{side}"]),
        )
    return (0, 0, 0.0)


def _heavier_side(downstream: dict) -> str:
    return "a" if "extra_steps_a" in downstream else "b"


def _suggested_prompt(category: str, ctx: dict) -> str:
    """Paste-ready system-prompt addition for a failure category, instantiated
    with real details (source/tool names, observed step counts) from ``ctx``."""
    bad = ctx.get("bad_source", "")
    good = ctx.get("good_source", "")
    bad_tool = ctx.get("bad_tool", "")
    good_tool = ctx.get("good_tool", "")
    tool = ctx.get("tool", "")
    extra_steps = ctx.get("extra_steps", 0)
    extra_searches = ctx.get("extra_searches", 0)

    if category == "retrieval":
        text = (
            "Prefer primary and official sources (annual reports, official "
            "documentation, government statistics) over blogs, forums, and "
            "aggregator posts."
        )
        if bad and good and bad != good:
            text += (
                f' In this batch, results like "{bad}" led you astray where '
                f'"{good}" contained the correct answer; verify any secondary '
                f"source against an authoritative one before relying on it."
            )
        elif bad:
            text += (
                f' Treat sources like "{bad}" as unverified until corroborated '
                f"by an authoritative source."
            )
        return text
    if category == "tool_selection":
        text = (
            "Before calling a tool, state why it is the right tool for the "
            "input you actually have (data type, precision needed, side effects)."
        )
        if bad_tool and good_tool and bad_tool != good_tool:
            text += (
                f' In this batch, "{bad_tool}" was the wrong choice where '
                f'"{good_tool}" succeeded; check that the tool matches the task '
                f"before invoking it."
            )
        elif bad_tool:
            text += (
                f' In this batch, "{bad_tool}" was the wrong choice for the '
                f"failing task; check that the tool matches the task before "
                f"invoking it."
            )
        return text
    if category == "tool_execution":
        target = f'"{tool}"' if tool else "a tool"
        return (
            f"Before executing {target}, validate the arguments against the "
            f"expected input shape: test regexes and parsers on a small known "
            f"sample, and check units, date formats and field names. If the "
            f"result is empty or implausible, fix the arguments and retry "
            f"instead of building on it."
        )
    if category == "planning":
        return (
            "Write a short explicit plan before acting: name the authoritative "
            "source and a verification step for each sub-goal, and revise the "
            "plan when evidence contradicts it rather than pushing on."
        )
    if category == "reasoning":
        return (
            "When intermediate results conflict or look surprising, stop and "
            "reconcile them explicitly: restate the evidence, state which piece "
            "you trust and why, and only then continue."
        )
    # stopping / efficiency
    text = (
        "Stop as soon as your evidence is sufficient: once two independent "
        "sources corroborate the answer, answer immediately instead of "
        "continuing to gather more."
    )
    if extra_steps:
        detail = f" In this batch you spent {extra_steps} extra step(s)"
        if extra_searches:
            detail += f" (including {extra_searches} redundant search(es))"
        detail += " after the answer was already established."
        text += detail
    return text


def _failure_recommendations(reports: list[dict]) -> list[dict]:
    """One critical recommendation per (failing agent, failure category)."""
    total = len(reports)
    groups: dict[tuple[str, str], dict] = {}

    for report in reports:
        attribution = report.get("attribution") or {}
        side = attribution.get("failed_agent")
        if side is None:
            continue
        category = attribution.get("category") or "reasoning"
        agent = report[side]["agent"]["name"]
        other_agent = report[_other(side)]["agent"]["name"]
        group = groups.setdefault(
            (agent, category),
            {
                "agent": agent,
                "other_agent": other_agent,
                "category": category,
                "tasks": [],
                "steps": 0,
                "tokens": 0,
                "latency": 0.0,
                "root_types": set(),
                "ctx": {},
            },
        )
        group["tasks"].append(report["task"]["id"])

        causal = _causal_divergence(report)
        if causal is not None:
            s, t, lat = _side_extras(causal["downstream"], side)
            group["steps"] += s
            group["tokens"] += t
            group["latency"] += lat

        root = attribution.get("root_cause_step")
        root_step = _side_step(report, side, root)
        other_step = (
            _side_step(report, _other(side), causal[f"{_other(side)}_index"])
            if causal is not None
            else None
        )
        if root_step:
            group["root_types"].add(root_step["type"])
        _fill_ctx(group["ctx"], root_step, other_step)

    recs = []
    for (agent, category), g in sorted(groups.items()):
        tasks = sorted(g["tasks"])
        n = len(tasks)
        root_types = ", ".join(sorted(g["root_types"])) or "unknown"
        finding = (
            f"{agent} failed {n} of {total} task(s) ({', '.join(tasks)}) after "
            f"{_KIND_LABELS.get(category, category)} divergences rooted in "
            f"{root_types} step(s); "
            + (f"downstream of the root cause it spent {_extra_spend(g)} "
               f"versus {g['other_agent']}, and each of "
               if _extra_spend(g) else "each of ")
            + "these runs ended in failure."
        )
        gain = f"up to +{n / total * 100:.0f}pt success ({n}/{total} tasks)"
        if g["tokens"] > 0:
            gain += f", −{g['tokens']:,} wasted tokens"
        recs.append(
            {
                "agent": agent,
                "category": category,
                "severity": "critical",
                "finding": finding,
                "evidence_tasks": tasks,
                "suggested_prompt": _suggested_prompt(category, g["ctx"]),
                "expected_gain": gain,
                "_sort_tokens": g["tokens"],
            }
        )
    return recs


def _detour_recommendations(reports: list[dict]) -> list[dict]:
    """Moderate/minor recommendations for non-fatal but costly detours."""
    groups: dict[tuple[str, str], dict] = {}

    for report in reports:
        for div in report.get("divergences", []):
            downstream = div["downstream"]
            if downstream.get("caused_failure"):
                continue
            side = _heavier_side(downstream)
            s, t, lat = _side_extras(downstream, side)
            if s <= 0 and t <= 0 and lat <= 0:
                continue
            agent = report[side]["agent"]["name"]
            other_agent = report[_other(side)]["agent"]["name"]
            kind = div["kind"]
            category = "efficiency" if kind == "stopping" else kind
            group = groups.setdefault(
                (agent, category),
                {
                    "agent": agent,
                    "other_agent": other_agent,
                    "category": category,
                    "kind": kind,
                    "tasks": set(),
                    "steps": 0,
                    "tokens": 0,
                    "latency": 0.0,
                    "extra_searches": 0,
                    "ctx": {},
                },
            )
            group["tasks"].add(report["task"]["id"])
            group["steps"] += s
            group["tokens"] += t
            group["latency"] += lat

            own_step = _side_step(report, side, div[f"{side}_index"])
            other_step = _side_step(report, _other(side), div[f"{_other(side)}_index"])
            start = div[f"{side}_index"]
            if start is not None and s > 0:
                extra = report[side]["steps"][start : start + s]
                group["extra_searches"] += sum(
                    1 for step in extra if step["type"] == "search"
                )
            _fill_ctx(group["ctx"], own_step, other_step)

    recs = []
    for (agent, category), g in sorted(groups.items()):
        tasks = sorted(g["tasks"])
        severity = "moderate" if g["tokens"] > MODERATE_TOKEN_THRESHOLD else "minor"
        finding = (
            f"{agent} took non-fatal {_KIND_LABELS.get(category, category)} "
            f"detours on {len(tasks)} task(s) ({', '.join(tasks)}): "
            + (f"{_extra_spend(g)} versus {g['other_agent']}, "
               if _extra_spend(g) else "")
            + "without changing any outcome."
        )
        gain = (
            f"−{g['tokens']:,} tokens and −{g['latency']:g}s latency "
            f"across {len(tasks)} task(s) if avoided"
        )
        ctx = dict(g["ctx"])
        ctx["extra_steps"] = g["steps"]
        ctx["extra_searches"] = g["extra_searches"]
        recs.append(
            {
                "agent": agent,
                "category": category,
                "severity": severity,
                "finding": finding,
                "evidence_tasks": tasks,
                "suggested_prompt": _suggested_prompt(g["kind"], ctx),
                "expected_gain": gain,
                "_sort_tokens": g["tokens"],
            }
        )
    return recs


def _extra_spend(g: dict) -> str:
    """``+3 steps, +1,204 tokens and +2.1s latency`` from a group's
    totals, listing only the parts that are non-zero; empty when none
    are — a "+0 steps, +0 tokens" sentence says nothing."""
    parts = []
    if g.get("steps"):
        parts.append(f"+{g['steps']} steps")
    if g.get("tokens"):
        parts.append(f"+{g['tokens']:,} tokens")
    if g.get("latency"):
        parts.append(f"+{g['latency']:g}s latency")
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def recommend(reports: list[dict]) -> list[dict]:
    """Generate evidence-based recommendations from comparison reports.

    Returns a sorted list of recommendation dicts (critical failures first,
    then by wasted tokens descending) with keys ``agent``, ``category``,
    ``severity``, ``finding``, ``evidence_tasks``, ``suggested_prompt`` and
    ``expected_gain``.  Deterministic; returns [] when there is nothing
    meaningful to recommend.
    """
    if not reports:
        return []
    recs = _failure_recommendations(reports) + _detour_recommendations(reports)
    recs.sort(
        key=lambda r: (
            _SEVERITY_RANK[r["severity"]],
            -r["_sort_tokens"],
            r["agent"],
            r["category"],
        )
    )
    for r in recs:
        del r["_sort_tokens"]
    return recs
