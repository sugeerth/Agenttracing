"""Divergence clustering into systematic issues (SCHEMA.md v13).

A batch produces one divergence per place two runs parted company, which for
a real task set means dozens of findings.  Reading them one by one is how
review fatigue starts, and it hides the thing that actually matters: the same
mistake recurring under different task names.

This module collapses divergences into **issues** — recurring behavioral
problems identified by a stable fingerprint — so the output becomes "this
agent has three systematic problems, ranked by what they cost" rather than a
list of thirty-seven incidents.

The fingerprint is deliberately coarse where the specifics do not matter
(which URL, which query text) and precise where they do (the kind of
divergence, the step types involved, the tool names, whether a quality
annotation fired).  Two runs that pick a bad source on different tasks share
a fingerprint; the same tool misused with different arguments does not
collapse into "retrieval".

Fingerprints are stable across runs and contain no hashes, so they can be
written into a ``.agentdiffignore`` file to suppress findings a team has
judged benign.  Suppression never deletes: suppressed issues are still
reported, marked, and excluded from the headline counts, because silently
dropping findings is how a gate stops being trustworthy.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional, Union

#: strip volatile detail so the same behavior clusters across tasks.
_DIGITS = re.compile(r"\d+")
_URL = re.compile(r"https?://[^\s)\"',]+")
_NON_WORD = re.compile(r"[^a-z0-9_]+")

#: how many failures an issue must cause to be called critical.
CRITICAL_FAILURES = 1
#: token cost at or above which a non-fatal issue is major rather than minor.
MAJOR_TOKENS = 500

SUPPRESS_FILE = ".agentdiffignore"


def _normalize(text: str) -> str:
    """Reduce a step name to its recurring shape."""
    text = _URL.sub("url", (text or "").lower())
    text = _DIGITS.sub("n", text)
    text = _NON_WORD.sub("_", text).strip("_")
    return text or "unnamed"


def _side_step(report: dict, side: str, index: Optional[int]) -> Optional[dict]:
    if index is None:
        return None
    steps = report[side]["steps"]
    return steps[index] if 0 <= index < len(steps) else None


def fingerprint(report: dict, divergence: dict) -> str:
    """A stable, human-readable signature for a divergence.

    Shape: ``kind/a:<type>.<name>/b:<type>.<name>[/q:<pattern>]``.  Missing
    sides render as ``none``; a quality suffix is added only when one side
    carries a weak/bad annotation, since that is what distinguishes "took a
    different path" from "took a known-bad step".
    """
    step_a = _side_step(report, "a", divergence.get("a_index"))
    step_b = _side_step(report, "b", divergence.get("b_index"))

    def part(step: Optional[dict]) -> str:
        if step is None:
            return "none"
        return f"{step['type']}.{_normalize(step.get('name', ''))}"

    signature = f"{divergence['kind']}/a:{part(step_a)}/b:{part(step_b)}"

    # Record only *which side* was annotated poor, not whether it was "weak"
    # or "bad".  Those are severities of one behavior, and splitting on them
    # would report the same systematic problem twice.
    quality = [
        label for label, step in (("a", step_a), ("b", step_b))
        if step and step.get("quality") in ("weak", "bad")
    ]
    if quality:
        signature += "/q:" + ",".join(quality)
    return signature


def _extras(downstream: dict) -> tuple[int, int, float]:
    """Downstream cost as (steps, tokens, latency), whichever side paid it."""
    for suffix in ("b", "a"):
        if f"extra_steps_{suffix}" in downstream:
            return (
                max(0, int(downstream.get(f"extra_steps_{suffix}", 0) or 0)),
                max(0, int(downstream.get(f"extra_tokens_{suffix}", 0) or 0)),
                max(0.0, float(downstream.get(f"extra_latency_s_{suffix}", 0.0) or 0.0)),
            )
    return (0, 0, 0.0)


def _title(kind: str, step_a: Optional[dict], step_b: Optional[dict]) -> str:
    """A plain-language name for the recurring problem."""
    bad_side = None
    for label, step in (("a", step_a), ("b", step_b)):
        if step and step.get("quality") in ("weak", "bad"):
            bad_side = (label, step)
            break
    named = (bad_side[1] if bad_side else (step_b or step_a))
    name = (named or {}).get("name") or (named or {}).get("type") or "a step"

    templates = {
        "retrieval": f"Selects a lower-quality source at \"{name}\"",
        "tool_selection": f"Reaches for the wrong tool at \"{name}\"",
        "tool_execution": f"Calls \"{name}\" with arguments that do not hold up",
        "planning": "Plans the task differently",
        "reasoning": f"Takes a different reasoning path at \"{name}\"",
        "stopping": "Keeps working after the evidence is already sufficient",
    }
    return templates.get(kind, f"Diverges ({kind}) at \"{name}\"")


def load_suppressions(path: Union[str, Path, None]) -> list[str]:
    """Read fingerprint patterns from a ``.agentdiffignore`` file.

    One pattern per line; ``#`` starts a comment; a trailing ``*`` makes the
    pattern a prefix match.  A missing file is not an error.
    """
    if path is None:
        return []
    file_path = Path(path)
    if file_path.is_dir():
        file_path = file_path / SUPPRESS_FILE
    if not file_path.is_file():
        return []
    patterns: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def is_suppressed(signature: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("*"):
            if signature.startswith(pattern[:-1]):
                return True
        elif signature == pattern:
            return True
    return False


def _severity(failures: int, tokens: int) -> str:
    if failures >= CRITICAL_FAILURES:
        return "critical"
    return "major" if tokens >= MAJOR_TOKENS else "minor"


def build_issues(
    reports: list[dict],
    suppressions: Optional[Iterable[str]] = None,
) -> dict:
    """Cluster every divergence in a batch into ranked systematic issues."""
    patterns = list(suppressions or [])
    clusters: dict[str, dict] = {}

    for report in reports:
        task = report["task"]["id"]
        agent_a = report["a"]["agent"]["name"]
        agent_b = report["b"]["agent"]["name"]
        for divergence in report.get("divergences", []):
            signature = fingerprint(report, divergence)
            step_a = _side_step(report, "a", divergence.get("a_index"))
            step_b = _side_step(report, "b", divergence.get("b_index"))
            downstream = divergence.get("downstream", {}) or {}
            steps_x, tokens_x, latency_x = _extras(downstream)
            fatal = bool(downstream.get("caused_failure"))
            failing_side = downstream.get("failed_agent")

            issue = clusters.setdefault(signature, {
                "id": signature,
                "kind": divergence["kind"],
                "title": _title(divergence["kind"], step_a, step_b),
                "occurrences": [],
                "tasks": set(),
                "agents": set(),
                "failures_caused": 0,
                "extra_steps": 0,
                "extra_tokens": 0,
                "extra_latency_s": 0.0,
                "example": None,
            })
            issue["occurrences"].append({
                "task": task,
                "rank": divergence["rank"],
                "a_index": divergence.get("a_index"),
                "b_index": divergence.get("b_index"),
                "caused_failure": fatal,
                "extra_steps": steps_x,
                "extra_tokens": tokens_x,
                "extra_latency_s": round(latency_x, 4),
                "summary": divergence.get("summary", ""),
            })
            issue["tasks"].add(task)
            if failing_side in ("a", "b"):
                issue["agents"].add(agent_a if failing_side == "a" else agent_b)
            issue["failures_caused"] += 1 if fatal else 0
            issue["extra_steps"] += steps_x
            issue["extra_tokens"] += tokens_x
            issue["extra_latency_s"] += latency_x
            # Keep the costliest fatal occurrence as the example to show.
            current = issue["example"]
            candidate = (fatal, tokens_x)
            if current is None or candidate > (current["caused_failure"],
                                               current["extra_tokens"]):
                issue["example"] = issue["occurrences"][-1]

    issues: list[dict] = []
    for signature in sorted(clusters):
        issue = clusters[signature]
        issue["tasks"] = sorted(issue["tasks"])
        issue["agents"] = sorted(issue["agents"])
        issue["occurrence_count"] = len(issue["occurrences"])
        issue["extra_latency_s"] = round(issue["extra_latency_s"], 4)
        issue["severity"] = _severity(issue["failures_caused"],
                                      issue["extra_tokens"])
        issue["suppressed"] = is_suppressed(signature, patterns)
        issue["recurring"] = len(issue["tasks"]) > 1
        issue["summary"] = (
            f"{issue['title']} — seen on {len(issue['tasks'])} task(s)"
            + (f", causing {issue['failures_caused']} failure(s)"
               if issue["failures_caused"] else "")
            + (f", costing {issue['extra_tokens']:,} extra tokens"
               if issue["extra_tokens"] else "")
            + "."
        )
        issues.append(issue)

    rank = {"critical": 0, "major": 1, "minor": 2}
    issues.sort(key=lambda i: (
        i["suppressed"],
        rank[i["severity"]],
        -i["failures_caused"],
        -i["extra_tokens"],
        i["id"],
    ))

    active = [i for i in issues if not i["suppressed"]]
    suppressed = [i for i in issues if i["suppressed"]]
    total_divergences = sum(i["occurrence_count"] for i in issues)

    if not issues:
        narrative = "No divergences found across the batch."
    else:
        recurring = [i for i in active if i["recurring"]]
        narrative = (
            f"{total_divergences} divergence(s) across {len(reports)} task(s) "
            f"collapse into {len(active)} distinct issue(s)"
            + (f" ({len(recurring)} recurring on more than one task)"
               if recurring else "")
            + "."
        )
        if active:
            worst = active[0]
            narrative += f" The costliest is: {worst['summary']}"
        if suppressed:
            narrative += (
                f" {len(suppressed)} issue(s) are suppressed by "
                f"{SUPPRESS_FILE} and excluded from the counts."
            )

    return {
        "issues": issues,
        "active": len(active),
        "suppressed": len(suppressed),
        "total_divergences": total_divergences,
        "counts": {
            severity: sum(1 for i in active if i["severity"] == severity)
            for severity in ("critical", "major", "minor")
        },
        "narrative": narrative,
    }


def render_issues_markdown(result: dict) -> str:
    """A triage-sized summary: the few problems, not the many incidents."""
    lines = ["# Systematic issues", "", result["narrative"], ""]
    active = [i for i in result["issues"] if not i["suppressed"]]
    if active:
        lines += ["| severity | issue | tasks | failures | extra tokens |",
                  "|---|---|---|---|---|"]
        for issue in active:
            lines.append(
                f"| {issue['severity']} | {issue['title']} | "
                f"{len(issue['tasks'])} | {issue['failures_caused']} | "
                f"{issue['extra_tokens']:,} |"
            )
        lines.append("")
        for issue in active:
            lines += [f"### {issue['title']}", "",
                      f"`{issue['id']}`", "",
                      f"Seen on: {', '.join(issue['tasks'])}", ""]
            example = issue["example"]
            if example:
                lines += [f"Example ({example['task']}): {example['summary']}", ""]
            lines += ["To suppress this issue, add its fingerprint to "
                      f"`{SUPPRESS_FILE}`.", ""]
    if result["suppressed"]:
        lines += ["## Suppressed", ""]
        for issue in result["issues"]:
            if issue["suppressed"]:
                lines.append(f"- `{issue['id']}` — {issue['title']}")
        lines.append("")
    return "\n".join(lines) + "\n"
