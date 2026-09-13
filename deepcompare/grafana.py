"""Grafana export: AgentDiff's numbers as Prometheus samples, with nothing
invented on the way out.

The engine already computed every number a dashboard could show — the
pass rate per agent per task and its Wilson interval, the mean return and
its bootstrap interval, the IQM, the probability that one policy beats
the other, the tool profile, the lineage of a self-evolving agent, the eval that
evolves beside it.  This
module writes those as flat samples so that a Grafana that reads
Prometheus (or a JSON, or a CSV) can plot them; it computes nothing new
except the per-task means of the per-run rows the scorecard already
holds, and the Wilson interval on those counts, through the same
:func:`deepcompare.statistics.wilson_interval` the engine uses.

What it writes, from a batch output directory (``aggregate.json`` +
``report_*.json`` as ``batch``, ``runs`` and ``evolve`` write them; a
``fleet.json`` as ``fleet`` writes it) or from a single trace:

* ``metrics.prom`` — the Prometheus text exposition format, in the
  textfile-collector convention: ``# HELP`` and ``# TYPE`` before the
  samples of each family, one sample per line, labels sorted, no
  timestamps (the collector ignores them and warns), every family a
  gauge, because every value is the state of a finished analysis and
  not a counter that grows.
* ``metrics.json`` — the same samples as ``{metric, labels, value}``
  rows plus a ``series`` view keyed by metric, for the Infinity or JSON
  datasource.
* ``metrics.csv`` — one row per sample, for the CSV datasource.

Every family is prefixed ``agentdiff_`` and its ``HELP`` line says what
the number is and what it is not.  Every sample carries
``synthetic="true"|"false"`` read from the trace's harness note, so a
demo can never masquerade as production on a dashboard.  An interval is
always three gauges (``_x``, ``_x_lo``, ``_x_hi``) and never a bare
point: a bootstrap or Wilson interval is a statement about the runs
recorded, not about runs that were never made, and the HELP says so.

Nothing here opens a socket: the file is written to disk and a node
exporter's textfile collector, or a Grafana datasource pointed at the
file, does the serving.  Same input, same bytes: families are emitted
in a fixed order and samples sorted by their labels.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from ._stats import finite, mean, rounded
from .reliability import RUNS_FLOOR_OPEN_ENDED, RUNS_FLOOR_STRUCTURED, runs_advisory
from .statistics import wilson_interval

VERSION = 1
PREFIX = "agentdiff_"

#: what a verdict is worth on a state timeline: the sign is the reading
#: (a negative code is a way evolution goes wrong), the magnitude orders
#: the colours; the text is the verdict itself, carried as a label
VERDICT_CODES = {
    "improved": 2, "traded": 1, "flat": 0, "regressed": -1,
    "overfit": -2, "forgot": -3, "gamed": -4,
}

_INTERVAL = "the interval is a 95% Wilson interval on the runs recorded, not a population claim"
_BOOT = "the interval is a stratified bootstrap over the runs recorded, not a population claim"
_TOOLS = ("summed over the pair reports in the directory (one representative pair per task "
          "in a runs layout), not over every run")

#: family name (without the prefix) -> (type, help); insertion order is
#: the order of the exposition, so the file is stable across versions
#: of the dictionary
FAMILIES: dict[str, tuple[str, str]] = {
    # per agent per task, from the scorecard's per-run rows
    "runs": ("gauge", "runs recorded for the agent on the task; a count, harness failures included"),
    "passes": ("gauge", "runs of the agent on the task whose outcome.success was true; a count"),
    "pass_rate": ("gauge", "passes over runs for the agent on the task; a proportion of the runs recorded, not a probability"),
    "pass_rate_lo": ("gauge", f"lower bound of the pass rate; {_INTERVAL}"),
    "pass_rate_hi": ("gauge", f"upper bound of the pass rate; {_INTERVAL}"),
    "steps_mean": ("gauge", "mean steps per run for the agent on the task, over the runs recorded"),
    "seconds_mean": ("gauge", "mean recorded latency per run in seconds for the agent on the task; a sum of step latencies as recorded, not wall clock"),
    "tokens_mean": ("gauge", "mean tokens per run for the agent on the task, as the trace recorded them"),
    "cost_usd_mean": ("gauge", "mean cost per run in USD for the agent on the task; 0 means unrecorded, and unrecorded is not free"),
    "tool_calls_mean": ("gauge", "mean tool calls per run for the agent on the task; a count of tool, search, retrieve and read steps"),
    "tool_errors_mean": ("gauge", "mean tool calls that returned an error per run for the agent on the task; a count over the steps"),
    "wasted_seconds_mean": ("gauge", "mean seconds per run spent on steps the reading marks as wasted (no information, repeat, dead end, error, spent after the answer's basis); a reading of the steps, not a measurement of the model"),
    # per agent, all tasks pooled
    "agent_runs": ("gauge", "runs recorded for the agent over every task; a count"),
    "agent_passes": ("gauge", "runs of the agent that succeeded over every task; a count"),
    "agent_pass_rate": ("gauge", "pooled pass rate of the agent over every task and run; hides which task carries the failures"),
    "agent_pass_rate_lo": ("gauge", f"lower bound of the pooled pass rate; {_INTERVAL}"),
    "agent_pass_rate_hi": ("gauge", f"upper bound of the pooled pass rate; {_INTERVAL}"),
    "agent_steps_mean": ("gauge", "mean steps per run for the agent over every task"),
    "agent_seconds_mean": ("gauge", "mean recorded latency per run in seconds for the agent over every task"),
    "agent_tokens_mean": ("gauge", "mean tokens per run for the agent over every task"),
    "agent_cost_usd_mean": ("gauge", "mean cost per run in USD for the agent over every task; 0 means unrecorded"),
    "agent_tool_calls_mean": ("gauge", "mean tool calls per run for the agent over every task"),
    "agent_tool_errors_mean": ("gauge", "mean tool errors per run for the agent over every task"),
    "agent_wasted_seconds_mean": ("gauge", "mean wasted seconds per run for the agent over every task, by the reading's roles"),
    # per run
    "run_success": ("gauge", "1 when the run's outcome.success was true, else 0; as graded, not as judged"),
    "run_steps": ("gauge", "steps in the run; a count"),
    "run_seconds": ("gauge", "recorded latency of the run in seconds; the sum of step latencies"),
    "run_tokens": ("gauge", "tokens of the run as the trace recorded them"),
    "run_cost_usd": ("gauge", "cost of the run in USD as recorded; 0 means unrecorded"),
    "run_tool_calls": ("gauge", "tool calls in the run; a count of tool, search, retrieve and read steps"),
    "run_tool_errors": ("gauge", "tool calls in the run that returned an error; a count"),
    "run_wasted_seconds": ("gauge", "seconds of the run on steps the reading marks as wasted"),
    "run_return": ("gauge", "episode return of the run: the sum of recorded step rewards, or of shaped ones when the source label says shaped"),
    # the training ground: return, IQM, improvement
    "return_mean": ("gauge", "mean episode return of the agent over its episodes; recorded rewards, or shaped from the labels when the source label says so"),
    "return_mean_lo": ("gauge", f"lower bound of the mean return; {_BOOT}"),
    "return_mean_hi": ("gauge", f"upper bound of the mean return; {_BOOT}"),
    "iqm": ("gauge", "interquartile mean of the agent's episode returns: the mean of the middle half, the least moved by one exceptional episode"),
    "iqm_lo": ("gauge", f"lower bound of the IQM; {_BOOT}"),
    "iqm_hi": ("gauge", f"upper bound of the IQM; {_BOOT}"),
    "task_return_mean": ("gauge", "mean episode return of the agent on the task over the runs recorded"),
    "task_return_delta": ("gauge", "mean return on the task of the to agent minus the from agent; a difference of means, with no interval of its own"),
    "improvement": ("gauge", "probability that a random run of the to agent beats a random run of the from agent on the same task, averaged over tasks (within-task Mann-Whitney, ties half); 0.5 is the coin flip"),
    "improvement_lo": ("gauge", f"lower bound of the probability of improvement; {_BOOT}"),
    "improvement_hi": ("gauge", f"upper bound of the probability of improvement; {_BOOT}"),
    "task_improvement": ("gauge", "probability of improvement on one task: the to agent's runs against the from agent's, ties half; below 0.5 the to agent is worse there"),
    # paired inference over tasks
    "paired_diff": ("gauge", "paired difference in per-task success, from agent minus to agent, over the tasks both ran; a mean of per-task differences"),
    "paired_diff_lo": ("gauge", "lower bound of the paired difference: a normal 95% interval from its standard error over the tasks paired; absent under two pairs"),
    "paired_diff_hi": ("gauge", "upper bound of the paired difference: a normal 95% interval from its standard error over the tasks paired; absent under two pairs"),
    "paired_sign_test_p": ("gauge", "two-sided sign test p-value over the discordant tasks; not distinguishable under ten pairs whatever the value"),
    "paired_tasks": ("gauge", "tasks the paired inference used; a count"),
    # the runs advisory
    "runs_per_task_min": ("gauge", "runs per task at the thinnest task; the level label is the engine's runs advisory tier (insufficient, below-floor, structured-ok, open-ended-ok)"),
    "runs_per_task_median": ("gauge", "median runs per task across tasks; the level label is the runs advisory tier"),
    "runs_floor": ("gauge", "the runs-per-task floor the advisory holds a claim to: structured tool-use tasks, or open-ended reasoning; a constant of the engine, not a measurement"),
    # the reward audit and the critic
    "reward_disagreements": ("gauge", "ordered pairs of episodes where a failed episode out-earned a passing one, in the scope named by the label (pooled, by_task, shaping: the return with the last step removed); a count of signals to investigate, not proven defects"),
    "reward_disagreement_pairs": ("gauge", "ordered (failed, passed) pairs compared in the scope; the denominator of the disagreement count"),
    "reward_rank_agreement": ("gauge", "Spearman rank correlation between return and outcome over the episodes; the binary outcome's ties cap it below 1"),
    "critic_explained_variance": ("gauge", "share of the variance in realised discounted return-to-go the value head explains; negative means a constant would have done better; over the steps that carry a value"),
    # tools, from the pair reports' tool profiles
    "tool_calls": ("gauge", f"calls of the tool by the agent; {_TOOLS}"),
    "tool_errors": ("gauge", f"calls of the tool by the agent that returned an error; {_TOOLS}"),
    "tool_repeats": ("gauge", f"calls of the tool by the agent with an input already sent; {_TOOLS}"),
    "tool_wasted_calls": ("gauge", f"calls of the tool by the agent on steps the reading marks as wasted; {_TOOLS}"),
    "tool_wasted_seconds": ("gauge", f"seconds the agent waited on wasted calls of the tool; {_TOOLS}"),
    "tool_seconds": ("gauge", f"seconds the agent waited on the tool; {_TOOLS}"),
    "tool_max_identical_run": ("gauge", "the longest run of consecutive identical calls of the tool by the agent in any one pair report; the maximum over reports, not a sum"),
    # a fleet
    "fleet_rank": ("gauge", "the agent's rank in the fleet by composite score, 1 = best; depends on the scoring weights in fleet.json"),
    "fleet_score": ("gauge", "the agent's composite score in the fleet, a weighted sum of min-max normalised dimensions; comparable within this fleet only"),
    # a self-evolving lineage: per generation
    "evolution_generations": ("gauge", "generations in the lineage; a count"),
    "evolution_generation_index": ("gauge", "position of the generation in the lineage, 0 = the root; the parent chain's order, or the directory order when there is none"),
    "evolution_episodes": ("gauge", "episodes recorded for the generation; a count"),
    "evolution_passes": ("gauge", "episodes of the generation that succeeded; a count"),
    "evolution_pass_rate": ("gauge", "pass rate of the generation over its episodes; a proportion of the episodes recorded"),
    "evolution_mean_return": ("gauge", "mean episode return of the generation; recorded rewards, or shaped when the source label says so"),
    "evolution_iqm": ("gauge", "interquartile mean return of the generation over its episodes"),
    "evolution_iqm_lo": ("gauge", f"lower bound of the generation's IQM; {_BOOT}"),
    "evolution_iqm_hi": ("gauge", f"upper bound of the generation's IQM; {_BOOT}"),
    "evolution_iqm_balanced": ("gauge", "task-balanced IQM of the generation: the mean over tasks of each task's IQM, so a task lost outright is not trimmed away as the pooled IQM's lower tail; what the engine ranks generations by"),
    "evolution_iqm_balanced_lo": ("gauge", f"lower bound of the generation's task-balanced IQM; {_BOOT}"),
    "evolution_iqm_balanced_hi": ("gauge", f"upper bound of the generation's task-balanced IQM; {_BOOT}"),
    "evolution_task_iqm": ("gauge", "IQM return of the generation on one task over its runs there; where forgetting shows, per task"),
    "evolution_task_pass_rate": ("gauge", "pass rate of the generation on one task over its runs there; a proportion of a handful of runs, read it with the runs advisory"),
    "evolution_prompt_chars": ("gauge", "characters in the generation's system prompt; a size, not a quality"),
    "evolution_rules": ("gauge", "rules in the generation's artifacts; a count"),
    "evolution_memory": ("gauge", "memory entries in the generation's artifacts; a count"),
    "evolution_skills": ("gauge", "skills in the generation's artifacts; a count"),
    "evolution_tools": ("gauge", "tools in the generation's artifacts; a count"),
    "evolution_budget": ("gauge", "the growth budget lineage.json set for the artifact named by the what label; exceeding it is a finding, not an error"),
    "evolution_over_budget": ("gauge", "the generation's size of the artifact named by the what label, where it exceeds the budget"),
    # per step
    "evolution_step_verdict": ("gauge", "1 for the verdict the step earned (improved, regressed, flat, gamed, overfit, forgot, traded), exactly one per step; a reading of the intervals and the checks, not a certainty"),
    "evolution_step_verdict_code": ("gauge", "the step's verdict as a number for a state timeline: improved 2, traded 1, flat 0, regressed -1, overfit -2, forgot -3, gamed -4; the verdict label is the text"),
    "evolution_step_improvement": ("gauge", "probability that a random episode of the child generation beats one of the parent on the same task; 0.5 is the coin flip"),
    "evolution_step_improvement_lo": ("gauge", f"lower bound of the step's probability of improvement; {_BOOT}"),
    "evolution_step_improvement_hi": ("gauge", f"upper bound of the step's probability of improvement; {_BOOT}"),
    "evolution_step_iqm_delta": ("gauge", "child IQM minus parent IQM for the step; a difference of two point estimates whose intervals are on the generation series"),
    "evolution_step_pass_rate_delta": ("gauge", "child pass rate minus parent pass rate for the step"),
    "evolution_step_overfit_gap": ("gauge", "mean per-task return delta on the tasks whose episodes triggered the step minus the delta on every other task; past the engine's margin the step fits its evidence more than the rest"),
    "evolution_step_drift": ("gauge", "normalised edit distance between the parent's and the child's step-token streams, 0 = the same actions in the same order, 1 = nothing shared"),
    "evolution_step_flag": ("gauge", "1 for each flag the step carries beside its verdict (overfit, protected, over_budget, collapsed, noisy, axes_disagree); a check that fired, not a verdict"),
    "evolution_protected_touched": ("gauge", "1 where the step changed a protected artifact path (the agent edited the thing that judges it); the direction label says weakened or restored, the source label whether the diff or the episodes showed it"),
    "evolution_flags": ("gauge", "steps of the lineage that carry the flag; a count"),
    "evolution_verdicts": ("gauge", "steps of the lineage that earned the verdict; a count"),
    "evolution_net_iqm_delta": ("gauge", "last generation's IQM minus the root's; a difference of point estimates, and not the sum of what each step earned"),
    "evolution_best": ("gauge", "the IQM the engine ranks by (task-balanced when the section carries one) of the generation with the highest; the generation label names it"),
    "evolution_recommended": ("gauge", "the same IQM of the generation the engine recommends keeping: the best, unless a later one's interval clears it, never a gamed one; the is_last label says whether it is the latest"),
    # the eval that evolves with the lineage (aggregate.coevolution)
    "coevolution_eval_generations": ("gauge", "generations of the eval's own lineage, e0 the base metrics and one more per agent step that taught it something; a count"),
    "coevolution_eval_generation_size": ("gauge", "active metrics in the eval generation (base plus adopted, less retired and demoted); the after_step label names the agent step that triggered it and trigger_probe the probe"),
    "coevolution_metric": ("gauge", "one of the eval's metrics on one generation of the agent, computed with hindsight on every generation whether or not the metric existed then; the status label says base, adopted, demoted or retired, learned whether the eval learned it"),
    "coevolution_metric_lo": ("gauge", f"lower bound of the metric on the generation; {_BOOT}"),
    "coevolution_metric_hi": ("gauge", f"upper bound of the metric on the generation; {_BOOT}"),
    "coevolution_metric_status": ("gauge", "1 per metric of the eval: its status, the probe that proposed it, the agent step it was adopted at and its confirmation (confirmed, unconfirmed, pending) as labels"),
    "coevolution_candidates": ("gauge", "candidate metrics the eval tested, by decision (adopted, rejected); a count of ledger rows"),
    "coevolution_candidates_by_probe": ("gauge", "candidates proposed by one probe, by decision; a count"),
    "coevolution_candidate": ("gauge", "1 per candidate in the ledger: the probe, the agent step, the metric id, the decision and the validators it failed (comma-joined, empty when adopted) as labels; every rejection is kept, never hidden"),
    "coevolution_step_flag_delta": ("gauge", "the change of an eval metric across an agent step where its interval excludes zero in the metric's bad direction: what the evolved eval flags with hindsight; learned says whether the metric was learned or base"),
    "coevolution_step_flag_delta_lo": ("gauge", f"lower bound of the flagged change; {_BOOT}"),
    "coevolution_step_flag_delta_hi": ("gauge", f"upper bound of the flagged change; {_BOOT}"),
    "coevolution_hindsight_lag": ("gauge", "agent steps between the first step a learned metric would have flagged and the step it was adopted at; 0 means adopted at the first step it flags; absent when it never flags"),
    "coevolution_hindsight": ("gauge", "the hindsight counts: steps re-read, steps whose reading changed by a learned metric, learned flags, base flags; the what label names which"),
    "coevolution_drift": ("gauge", "Jaccard distance of the final active metric set from the base set; 0 means the eval learned nothing, 1 would mean it shares no metric with the base, which cannot happen because base metrics are never retired"),
    "coevolution_min_adjusted_alpha": ("gauge", "the smallest Bonferroni-adjusted level at which a candidate was tested (alpha over the candidates tested at that step); the more the eval tests at once, the stricter it is"),
    "coevolution_closures": ("gauge", "loop closures: a step a metric flagged followed by a later step on which the lineage recovered on the same metric, recovered and not attributed; kind is all or learned"),
    "coevolution_recommended_agree": ("gauge", "1 when the base eval and the evolved eval recommend the same generation to keep, 0 otherwise; the base and evolved labels name the two"),
    # one run, step by step
    "step_reward": ("gauge", "reward recorded at the step by the environment; absent when the trace recorded none"),
    "step_return_cum": ("gauge", "return so far: the sum of recorded rewards up to and including the step"),
    "step_seconds": ("gauge", "latency of the step in seconds as recorded"),
    "step_tokens": ("gauge", "tokens of the step as recorded"),
    "step_value": ("gauge", "the policy's value estimate at the step, as recorded; absent when the trace carries none"),
    "step_advantage": ("gauge", "the policy's advantage at the step, as recorded; absent when the trace carries none"),
}

Sample = tuple[str, dict[str, str], Union[int, float]]

_METRIC_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SAMPLE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(?:\{(?P<labels>(?:[a-zA-Z_][a-zA-Z0-9_]*="(?:[^"\\\n]|\\["\\n])*",?)*)\})?'
    r'[ \t]+(?P<value>[^ \t]+)(?:[ \t]+(?P<ts>-?\d+))?[ \t]*$')
_TYPES = {"counter", "gauge", "histogram", "summary", "untyped"}


# ---------------------------------------------------------------- helpers

def _r(v: Any, places: int = 4) -> Optional[float]:
    # the shared rounding behind a finiteness gate: a value that is not a
    # number, or is NaN or infinite, is no sample and is dropped, never 0
    return rounded(v, places) if finite(v) else None


def _bool_label(v: Any) -> str:
    return "true" if v else "false"


def _synthetic_of_harness(harness: Any) -> bool:
    if not isinstance(harness, dict):
        return False
    note = str(harness.get("note") or "")
    return note.upper().startswith("SYNTHETIC") or str(harness.get("adapter") or "") == "synthetic"


def _synthetic_of_note(note: Any) -> bool:
    return str(note or "").upper().startswith("SYNTHETIC")


def _mean(values: list) -> Optional[float]:
    return _r(mean([v for v in values if finite(v)]))


class _Collector:
    """Accumulates samples; a sample with a non-numeric value is dropped
    and the reason kept, never replaced by a number."""

    def __init__(self) -> None:
        self.samples: list[Sample] = []
        self.notes: list[str] = []

    def add(self, family: str, labels: dict, value: Any) -> None:
        if family not in FAMILIES:
            raise KeyError(f"unknown metric family {family!r}")
        if isinstance(value, bool):
            value = int(value)
        if not finite(value):
            return
        clean = {str(k): str(v) for k, v in labels.items() if v is not None and str(v) != ""}
        self.samples.append((PREFIX + family, clean, value))

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


# ---------------------------------------------------------------- loading

def load_target(target: Union[str, Path]) -> dict:
    """What the target is: ``{"kind": "batch"|"fleet"|"trace", "path",
    "aggregate", "reports", "fleet", "trace"}``.  A directory needs an
    ``aggregate.json`` (batch, runs, evolve) or a ``fleet.json``; a file
    is a trace."""
    path = Path(target)
    if path.is_dir():
        agg_path, fleet_path = path / "aggregate.json", path / "fleet.json"
        if agg_path.is_file():
            aggregate = json.loads(agg_path.read_text(encoding="utf-8"))
            reports = []
            for p in sorted(path.glob("report_*.json")):
                try:
                    reports.append(json.loads(p.read_text(encoding="utf-8")))
                except ValueError as exc:
                    raise ValueError(f"{p} is not valid JSON: {exc}") from exc
            return {"kind": "batch", "path": path, "aggregate": aggregate, "reports": reports}
        if fleet_path.is_file():
            payload = json.loads(fleet_path.read_text(encoding="utf-8"))
            return {"kind": "fleet", "path": path, "fleet": payload.get("fleet") or {},
                    "reports": list(payload.get("reports") or []),
                    "aggregate": payload.get("aggregate") or {}}
        raise ValueError(f"{path} holds neither aggregate.json nor fleet.json")
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "steps" in data and "task" in data:
            return {"kind": "trace", "path": path, "trace": data}
        if isinstance(data, dict) and "a" in data and "b" in data and "alignment" in data:
            raise ValueError(f"{path} is a pair report; give the output directory that holds it")
        raise ValueError(f"{path} is not a trace (no steps and task)")
    raise ValueError(f"{path} does not exist")


# ---------------------------------------------------------------- batch

def _synthetic_by_agent(reports: list) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for report in reports:
        for side in ("a", "b"):
            run = report.get(side) or {}
            name = str(((run.get("agent") or {}).get("name")) or "")
            if not name:
                continue
            out[name] = out.get(name, False) or _synthetic_of_harness(run.get("harness"))
    return out


def _run_rows(aggregate: dict, reports: list) -> list[dict]:
    """One row per run: the scorecard's per-run rows when present, else
    the two sides of every pair report (one run each)."""
    rows = []
    per_run = ((aggregate.get("scorecard") or {}).get("per_run")) or []
    for row in per_run:
        spend, tools = row.get("spend") or {}, row.get("tools") or {}
        rows.append({
            "agent": str(row.get("agent") or ""), "task": str(row.get("task") or ""),
            "run": row.get("run_id"), "success": bool(row.get("success")),
            "steps": spend.get("steps"), "seconds": spend.get("latency_s"), "tokens": spend.get("tokens"),
            "cost_usd": spend.get("cost_usd"), "tool_calls": spend.get("tool_calls"),
            "tool_errors": tools.get("errors"), "wasted_s": spend.get("wasted_s"),
        })
    if rows:
        return rows
    for report in reports:
        task = str(((report.get("task") or {}).get("id")) or "")
        delta = report.get("metrics_delta") or {}
        timing = report.get("timing") or {}
        for side in ("a", "b"):
            run = report.get(side) or {}
            totals = run.get("totals") or {}
            tokens = totals.get("tokens")
            if tokens is None and finite(totals.get("input_tokens")) and finite(totals.get("output_tokens")):
                tokens = totals["input_tokens"] + totals["output_tokens"]
            errors = sum(1 for s in (run.get("steps") or []) if isinstance(s, dict) and s.get("error"))
            rows.append({
                "agent": str(((run.get("agent") or {}).get("name")) or side), "task": task,
                "run": run.get("run_id"), "success": bool((run.get("outcome") or {}).get("success")),
                "steps": len(run.get("steps") or []), "seconds": totals.get("latency_s"), "tokens": tokens,
                "cost_usd": totals.get("cost_usd"), "tool_calls": (delta.get("tool_calls") or {}).get(side),
                "tool_errors": errors, "wasted_s": (timing.get(side) or {}).get("wasted_s"),
            })
    return rows


_MEANS = (("steps", "steps_mean"), ("seconds", "seconds_mean"), ("tokens", "tokens_mean"),
          ("cost_usd", "cost_usd_mean"), ("tool_calls", "tool_calls_mean"),
          ("tool_errors", "tool_errors_mean"), ("wasted_s", "wasted_seconds_mean"))


def _collect_runs(c: _Collector, rows: list, synthetic: dict) -> None:
    by_task: dict[tuple, list] = {}
    by_agent: dict[str, list] = {}
    for row in rows:
        by_task.setdefault((row["agent"], row["task"]), []).append(row)
        by_agent.setdefault(row["agent"], []).append(row)
        labels = {"agent": row["agent"], "task": row["task"], "run": row["run"],
                  "synthetic": _bool_label(synthetic.get(row["agent"]))}
        c.add("run_success", labels, 1 if row["success"] else 0)
        c.add("run_steps", labels, row["steps"])
        c.add("run_seconds", labels, _r(row["seconds"]))
        c.add("run_tokens", labels, row["tokens"])
        c.add("run_cost_usd", labels, _r(row["cost_usd"], 6))
        c.add("run_tool_calls", labels, row["tool_calls"])
        c.add("run_tool_errors", labels, row["tool_errors"])
        c.add("run_wasted_seconds", labels, _r(row["wasted_s"]))
    for (agent, task), group in sorted(by_task.items()):
        n, k = len(group), sum(1 for g in group if g["success"])
        lo, hi = wilson_interval(k, n)
        labels = {"agent": agent, "task": task, "synthetic": _bool_label(synthetic.get(agent))}
        c.add("runs", labels, n)
        c.add("passes", labels, k)
        c.add("pass_rate", labels, _r(k / n))
        c.add("pass_rate_lo", labels, _r(lo))
        c.add("pass_rate_hi", labels, _r(hi))
        for key, family in _MEANS:
            c.add(family, labels, _mean([g[key] for g in group]))
    for agent, group in sorted(by_agent.items()):
        n, k = len(group), sum(1 for g in group if g["success"])
        lo, hi = wilson_interval(k, n)
        labels = {"agent": agent, "synthetic": _bool_label(synthetic.get(agent))}
        c.add("agent_runs", labels, n)
        c.add("agent_passes", labels, k)
        c.add("agent_pass_rate", labels, _r(k / n))
        c.add("agent_pass_rate_lo", labels, _r(lo))
        c.add("agent_pass_rate_hi", labels, _r(hi))
        for key, family in _MEANS:
            c.add("agent_" + family, labels, _mean([g[key] for g in group]))


def _collect_advisory(c: _Collector, aggregate: dict, rows: list) -> None:
    stats = (aggregate.get("rl") or {}).get("stats") or {}
    advisory = stats.get("advisory") if isinstance(stats.get("advisory"), dict) else None
    if not advisory or advisory.get("n_min") is None:
        counts: dict[tuple, int] = {}
        for row in rows:
            counts[(row["agent"], row["task"])] = counts.get((row["agent"], row["task"]), 0) + 1
        advisory = runs_advisory(list(counts.values())) if counts else None
    if not advisory or advisory.get("n_min") is None:
        c.note("runs advisory: no runs to count")
        return
    labels = {"level": str(advisory.get("tier") or "none")}
    c.add("runs_per_task_min", labels, advisory.get("n_min"))
    c.add("runs_per_task_median", labels, advisory.get("n_median"))
    c.add("runs_floor", {"kind": "structured"}, RUNS_FLOOR_STRUCTURED)
    c.add("runs_floor", {"kind": "open_ended"}, RUNS_FLOOR_OPEN_ENDED)


def _collect_rl(c: _Collector, aggregate: dict, synthetic: dict) -> None:
    rl = aggregate.get("rl") or {}
    if not rl:
        c.note("no rl section: return, IQM and improvement families omitted")
        return
    source = str(rl.get("source") or "")
    agents = rl.get("agents") or {}
    for name in sorted(agents):
        block = agents[name] or {}
        labels = {"agent": name, "source": source, "synthetic": _bool_label(synthetic.get(name))}
        c.add("return_mean", labels, _r(block.get("mean_return")))
        ci = block.get("return_ci") or [None, None]
        if isinstance(ci, list) and len(ci) == 2:
            c.add("return_mean_lo", labels, _r(ci[0]))
            c.add("return_mean_hi", labels, _r(ci[1]))
        for ep in block.get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            c.add("run_return", {"agent": name, "task": ep.get("task_id"), "run": ep.get("run_id"),
                                 "source": str(ep.get("source") or source),
                                 "synthetic": _bool_label(synthetic.get(name))}, _r(ep.get("return")))
    stats = rl.get("stats") or {}
    for name in sorted(stats.get("aggregates") or {}):
        iqm = (stats["aggregates"][name] or {}).get("iqm") or {}
        labels = {"agent": name, "source": source, "synthetic": _bool_label(synthetic.get(name))}
        c.add("iqm", labels, _r(iqm.get("point")))
        c.add("iqm_lo", labels, _r(iqm.get("lo")))
        c.add("iqm_hi", labels, _r(iqm.get("hi")))
    imp = stats.get("improvement") or {}
    frm, to = imp.get("a"), imp.get("b")
    if imp.get("measurable") and frm and to:
        pair = {"from": frm, "to": to, "source": source,
                "synthetic": _bool_label(synthetic.get(frm) or synthetic.get(to))}
        c.add("improvement", pair, _r(imp.get("point")))
        c.add("improvement_lo", pair, _r(imp.get("lo")))
        c.add("improvement_hi", pair, _r(imp.get("hi")))
        for task in sorted(imp.get("per_task") or {}):
            c.add("task_improvement", dict(pair, task=task), _r(imp["per_task"][task]))
    elif imp:
        c.note(f"improvement not measurable: {imp.get('reason')}")
    tasks = rl.get("tasks") or {}
    names = sorted(agents)
    for task in sorted(tasks):
        block = tasks[task] or {}
        for name in names:
            cell = block.get(name)
            if isinstance(cell, dict):
                c.add("task_return_mean", {"agent": name, "task": task, "source": source,
                                           "synthetic": _bool_label(synthetic.get(name))}, _r(cell.get("mean_return")))
        if finite(block.get("delta")) and len(names) == 2:
            c.add("task_return_delta", {"from": names[0], "to": names[1], "task": task, "source": source,
                                        "synthetic": _bool_label(synthetic.get(names[0]) or synthetic.get(names[1]))},
                  _r(block["delta"]))
    audit = rl.get("audit") or {}
    reward = audit.get("reward") or {}
    scopes = (reward.get("disagreement") or {}).get("scopes") or {}
    any_syn = _bool_label(any(synthetic.values()))
    for scope in sorted(scopes):
        block = scopes[scope] or {}
        c.add("reward_disagreements", {"scope": scope, "synthetic": any_syn}, block.get("inversions"))
        c.add("reward_disagreement_pairs", {"scope": scope, "synthetic": any_syn}, block.get("pairs_n"))
    rank = reward.get("rank_agreement") or {}
    if rank.get("measurable"):
        c.add("reward_rank_agreement", {"basis": "return", "synthetic": any_syn}, _r(rank.get("spearman")))
        c.add("reward_rank_agreement", {"basis": "shaping", "synthetic": any_syn}, _r(rank.get("spearman_shaping")))
    critic = audit.get("critic") or {}
    if critic.get("measurable"):
        c.add("critic_explained_variance", {"agent": "_all", "synthetic": any_syn},
              _r((critic.get("overall") or {}).get("explained_variance")))
        for name in sorted(critic.get("per_agent") or {}):
            c.add("critic_explained_variance", {"agent": name, "synthetic": _bool_label(synthetic.get(name))},
                  _r((critic["per_agent"][name] or {}).get("explained_variance")))
    elif critic:
        c.note(f"critic not measurable: {critic.get('reason')}")


def _collect_paired(c: _Collector, aggregate: dict, synthetic: dict) -> None:
    paired = aggregate.get("paired_inference")
    if not isinstance(paired, dict) or not paired.get("labels"):
        return
    labels_ab = list(paired.get("labels") or [])
    if len(labels_ab) != 2:
        return
    frm, to = str(labels_ab[0]), str(labels_ab[1])
    labels = {"from": frm, "to": to, "synthetic": _bool_label(synthetic.get(frm) or synthetic.get(to))}
    c.add("paired_diff", labels, _r(paired.get("diff")))
    ci = paired.get("ci95")
    if isinstance(ci, list) and len(ci) == 2:
        c.add("paired_diff_lo", labels, _r(ci[0]))
        c.add("paired_diff_hi", labels, _r(ci[1]))
    else:
        c.note("paired inference: no interval (under two pairs or no standard error)")
    c.add("paired_sign_test_p", labels, _r(paired.get("sign_test_p"), 6))
    c.add("paired_tasks", labels, paired.get("n_pairs"))


_TOOL_FIELDS = (("calls", "tool_calls"), ("errors", "tool_errors"), ("repeats", "tool_repeats"),
                ("wasted_calls", "tool_wasted_calls"), ("wasted_s", "tool_wasted_seconds"),
                ("seconds", "tool_seconds"))


def _collect_tools(c: _Collector, reports: list, synthetic: dict) -> None:
    sums: dict[tuple, dict] = {}
    for report in reports:
        profile = report.get("tools_profile") or {}
        for side in ("a", "b"):
            block = profile.get(side) or {}
            if not block.get("measurable"):
                continue
            agent = str(block.get("agent") or ((report.get(side) or {}).get("agent") or {}).get("name") or side)
            for tool, stats in (block.get("tools") or {}).items():
                if not isinstance(stats, dict):
                    continue
                acc = sums.setdefault((agent, str(tool)), {"max_identical_run": 0})
                for key, _family in _TOOL_FIELDS:
                    if finite(stats.get(key)):
                        acc[key] = acc.get(key, 0) + stats[key]
                if finite(stats.get("max_identical_run")):
                    acc["max_identical_run"] = max(acc["max_identical_run"], stats["max_identical_run"])
    if not sums:
        c.note("no tool profiles in the reports: tool families omitted")
        return
    for (agent, tool), acc in sorted(sums.items()):
        labels = {"agent": agent, "tool": tool, "synthetic": _bool_label(synthetic.get(agent))}
        for key, family in _TOOL_FIELDS:
            if key in acc:
                c.add(family, labels, _r(acc[key]))
        c.add("tool_max_identical_run", labels, acc["max_identical_run"])


def _collect_evolution(c: _Collector, aggregate: dict) -> None:
    ev = aggregate.get("evolution")
    if not isinstance(ev, dict):
        return
    if not ev.get("measurable"):
        c.note(f"evolution not measurable: {ev.get('reason')}")
        return
    family = str(ev.get("family") or "")
    gens = [g for g in (ev.get("generations") or []) if isinstance(g, dict)]
    syn_by_gen = {str(g.get("id")): _synthetic_of_note(g.get("note")) for g in gens}
    any_syn = _bool_label(any(syn_by_gen.values()))
    base = {"family": family}
    c.add("evolution_generations", dict(base, synthetic=any_syn), len(gens))
    for g in gens:
        gid = str(g.get("id"))
        labels = dict(base, generation=gid, index=str(g.get("index")), synthetic=_bool_label(syn_by_gen.get(gid)))
        c.add("evolution_generation_index", labels, g.get("index"))
        if not g.get("measurable", True):
            c.note(f"generation {gid} not measurable: {g.get('reason')}")
            continue
        c.add("evolution_episodes", labels, g.get("episodes_n"))
        c.add("evolution_passes", labels, g.get("passes"))
        c.add("evolution_pass_rate", labels, _r(g.get("pass_rate")))
        c.add("evolution_mean_return", labels, _r(g.get("mean_return")))
        iqm = g.get("iqm") or {}
        c.add("evolution_iqm", labels, _r(iqm.get("point")))
        c.add("evolution_iqm_lo", labels, _r(iqm.get("lo")))
        c.add("evolution_iqm_hi", labels, _r(iqm.get("hi")))
        balanced = g.get("iqm_by_task") or {}
        c.add("evolution_iqm_balanced", labels, _r(balanced.get("point")))
        c.add("evolution_iqm_balanced_lo", labels, _r(balanced.get("lo")))
        c.add("evolution_iqm_balanced_hi", labels, _r(balanced.get("hi")))
        for task in sorted(balanced.get("per_task") or {}):
            c.add("evolution_task_iqm", dict(labels, task=task), _r(balanced["per_task"][task]))
        for task in sorted(g.get("pass_by_task") or {}):
            c.add("evolution_task_pass_rate", dict(labels, task=task), _r(g["pass_by_task"][task]))
        size = g.get("size") or {}
        if size.get("measurable", True):
            c.add("evolution_prompt_chars", labels, size.get("prompt_chars"))
            c.add("evolution_rules", labels, size.get("rules"))
            c.add("evolution_memory", labels, size.get("memory"))
            c.add("evolution_skills", labels, size.get("skills"))
            c.add("evolution_tools", labels, size.get("tools"))
    for what in sorted(ev.get("budget") or {}):
        c.add("evolution_budget", dict(base, what=what, synthetic=any_syn), (ev["budget"] or {}).get(what))
    growth = (ev.get("integrity") or {}).get("growth") or {}
    for row in growth.get("over_budget") or []:
        if isinstance(row, dict):
            gid = str(row.get("gen"))
            c.add("evolution_over_budget", dict(base, generation=gid, what=str(row.get("what")),
                                               synthetic=_bool_label(syn_by_gen.get(gid))), row.get("value"))
    for step in ev.get("steps") or []:
        if not isinstance(step, dict):
            continue
        frm, to = str(step.get("from")), str(step.get("to"))
        labels = dict(base, **{"from": frm, "to": to, "step": str(step.get("index")),
                               "synthetic": _bool_label(syn_by_gen.get(frm) or syn_by_gen.get(to))})
        verdict = step.get("verdict")
        if verdict in VERDICT_CODES:
            c.add("evolution_step_verdict", dict(labels, verdict=verdict), 1)
            c.add("evolution_step_verdict_code", dict(labels, verdict=verdict), VERDICT_CODES[verdict])
        else:
            c.note(f"step {frm} -> {to}: no verdict ({step.get('reading') or 'not measurable'})")
        for flag in sorted(set(str(f) for f in (step.get("flags") or []))):
            c.add("evolution_step_flag", dict(labels, flag=flag), 1)
        effect = step.get("effect") or {}
        if effect.get("measurable"):
            imp = effect.get("improvement") or {}
            c.add("evolution_step_improvement", labels, _r(imp.get("point")))
            c.add("evolution_step_improvement_lo", labels, _r(imp.get("lo")))
            c.add("evolution_step_improvement_hi", labels, _r(imp.get("hi")))
            c.add("evolution_step_iqm_delta", labels, _r((effect.get("iqm") or {}).get("delta")))
            c.add("evolution_step_pass_rate_delta", labels, _r((effect.get("pass_rate") or {}).get("delta")))
        overfit = step.get("overfit") or {}
        if overfit.get("measurable"):
            c.add("evolution_step_overfit_gap", labels, _r(overfit.get("gap")))
        drift = step.get("drift") or {}
        if drift.get("measurable"):
            c.add("evolution_step_drift", labels, _r(drift.get("between")))
    integrity = ev.get("integrity") or {}
    for direction_key in ("touched", "restored"):
        for row in integrity.get(direction_key) or []:
            if not isinstance(row, dict):
                continue
            frm, to = str(row.get("from_gen")), str(row.get("to_gen"))
            c.add("evolution_protected_touched",
                  dict(base, step=str(row.get("step")), path=str(row.get("path")),
                       direction=str(row.get("direction") or direction_key),
                       source=str(row.get("source") or "diff"),
                       **{"from": frm, "to": to, "synthetic": _bool_label(syn_by_gen.get(to))}), 1)
    trajectory = ev.get("trajectory") or {}
    for verdict in sorted(VERDICT_CODES):
        if finite(trajectory.get(verdict)):
            c.add("evolution_verdicts", dict(base, verdict=verdict, synthetic=any_syn), trajectory[verdict])
    for flag in sorted(trajectory.get("flags") or {}):
        c.add("evolution_flags", dict(base, flag=flag, synthetic=any_syn), (trajectory["flags"] or {}).get(flag))
    c.add("evolution_net_iqm_delta", dict(base, synthetic=any_syn), _r(trajectory.get("net_iqm_delta")))
    best, rec = ev.get("best") or {}, ev.get("recommended") or {}
    if best.get("id") is not None:
        gid = str(best["id"])
        c.add("evolution_best", dict(base, generation=gid, synthetic=_bool_label(syn_by_gen.get(gid))), _r(best.get("iqm")))
    if rec.get("id") is not None:
        gid = str(rec["id"])
        c.add("evolution_recommended", dict(base, generation=gid, is_last=_bool_label(rec.get("is_last")),
                                            synthetic=_bool_label(syn_by_gen.get(gid))), _r(rec.get("iqm")))


def _collect_coevolution(c: _Collector, aggregate: dict) -> None:
    co = aggregate.get("coevolution")
    if not isinstance(co, dict):
        return
    if not co.get("measurable"):
        c.note(f"coevolution not measurable: {co.get('reason')}")
        return
    base = {"family": str(co.get("family") or ""), "synthetic": _bool_label(co.get("synthetic"))}
    metrics = co.get("metrics") or {}
    learned_of = {mid: _bool_label((m or {}).get("status") != "base") for mid, m in metrics.items()}
    status_of = {mid: str((m or {}).get("status") or "") for mid, m in metrics.items()}
    evals = [e for e in (co.get("eval_generations") or []) if isinstance(e, dict)]
    c.add("coevolution_eval_generations", base, len(evals))
    for e in evals:
        c.add("coevolution_eval_generation_size",
              dict(base, eval_gen=str(e.get("id")), index=str(e.get("index")), after_step=str(e.get("after_step") or "none"),
                   trigger_probe=str(e.get("trigger_probe") or "none")), e.get("size"))
    for mid in sorted(metrics):
        m = metrics[mid] or {}
        origin, conf = m.get("origin") or {}, m.get("confirmation") or {}
        c.add("coevolution_metric_status",
              dict(base, metric=mid, status=status_of[mid], learned=learned_of[mid], probe=str(origin.get("probe") or "base"),
                   adopted_step=str(m.get("adopted_at") or origin.get("step") or "none"),
                   confirmation=str(conf.get("status") or "none")), 1)
    matrix = co.get("matrix") or {}
    for mid in sorted(matrix):
        for gid in sorted(matrix[mid] or {}):
            cell = (matrix[mid] or {}).get(gid) or {}
            if not cell.get("measurable", True):
                continue
            labels = dict(base, metric=mid, generation=gid, status=status_of.get(mid, ""), learned=learned_of.get(mid, "false"))
            c.add("coevolution_metric", labels, _r(cell.get("point")))
            c.add("coevolution_metric_lo", labels, _r(cell.get("lo")))
            c.add("coevolution_metric_hi", labels, _r(cell.get("hi")))
    ledger = [row for row in (co.get("ledger") or []) if isinstance(row, dict)]
    by_decision: dict = {}
    by_probe: dict = {}
    for row in ledger:
        decision, probe = str(row.get("decision") or ""), str(row.get("probe") or "")
        by_decision[decision] = by_decision.get(decision, 0) + 1
        by_probe[(probe, decision)] = by_probe.get((probe, decision), 0) + 1
        c.add("coevolution_candidate",
              dict(base, index=str(row.get("index")), step=str(row.get("step")), probe=probe, metric=str(row.get("spec_id")),
                   decision=decision, failed=",".join(str(f) for f in (row.get("failed") or []))), 1)
    for decision in sorted(by_decision):
        c.add("coevolution_candidates", dict(base, decision=decision), by_decision[decision])
    for probe, decision in sorted(by_probe):
        c.add("coevolution_candidates_by_probe", dict(base, probe=probe, decision=decision), by_probe[(probe, decision)])
    for step in co.get("steps") or []:
        if not isinstance(step, dict):
            continue
        evolved = step.get("evolved") or {}
        labels = dict(base, **{"from": str(step.get("from")), "to": str(step.get("to")), "step": str(step.get("index"))})
        for flag in list(evolved.get("flags") or []) + list(evolved.get("base_flags") or []):
            if not isinstance(flag, dict):
                continue
            delta = flag.get("delta") or {}
            fl = dict(labels, metric=str(flag.get("metric")), learned=_bool_label(flag.get("learned")),
                      direction=str(flag.get("direction") or ""))
            c.add("coevolution_step_flag_delta", fl, _r(delta.get("point")))
            c.add("coevolution_step_flag_delta_lo", fl, _r(delta.get("lo")))
            c.add("coevolution_step_flag_delta_hi", fl, _r(delta.get("hi")))
    hindsight = co.get("hindsight") or {}
    for what in ("steps", "changed", "learned_flags", "base_flags"):
        c.add("coevolution_hindsight", dict(base, what=what), hindsight.get(what))
    for mid in sorted(hindsight.get("caught_at") or {}):
        lag = (hindsight["caught_at"] or {}).get(mid) or {}
        c.add("coevolution_hindsight_lag", dict(base, metric=mid), lag.get("lag"))
    integrity = co.get("integrity") or {}
    c.add("coevolution_drift", base, _r((integrity.get("drift") or {}).get("jaccard_distance_from_base")))
    c.add("coevolution_min_adjusted_alpha", base, _r((integrity.get("multiplicity") or {}).get("min_adjusted_alpha")))
    summary = (co.get("flow") or {}).get("summary") or {}
    c.add("coevolution_closures", dict(base, kind="all"), summary.get("closures"))
    c.add("coevolution_closures", dict(base, kind="learned"), summary.get("closures_learned"))
    rec = co.get("recommended") or {}
    if rec.get("base") is not None or rec.get("evolved") is not None:
        c.add("coevolution_recommended_agree", dict(base, **{"base": str(rec.get("base")), "evolved": str(rec.get("evolved"))}),
              bool(rec.get("agree")))


def collect_batch(loaded: dict) -> _Collector:
    c = _Collector()
    aggregate, reports = loaded.get("aggregate") or {}, loaded.get("reports") or []
    synthetic = _synthetic_by_agent(reports)
    for name in (aggregate.get("agents") or {}).values():
        synthetic.setdefault(str(name), False)
    rows = _run_rows(aggregate, reports)
    if rows:
        _collect_runs(c, rows, synthetic)
    else:
        c.note("no per-run rows: pass rate families omitted")
    _collect_advisory(c, aggregate, rows)
    _collect_rl(c, aggregate, synthetic)
    _collect_paired(c, aggregate, synthetic)
    _collect_tools(c, reports, synthetic)
    _collect_evolution(c, aggregate)
    _collect_coevolution(c, aggregate)
    return c


# ---------------------------------------------------------------- fleet

def collect_fleet(loaded: dict) -> _Collector:
    """A fleet: rank and score per agent, and one run per task per agent
    read as run rows, so the per-task and per-agent families hold."""
    c = _Collector()
    fleet, reports = loaded.get("fleet") or {}, loaded.get("reports") or []
    synthetic = _synthetic_by_agent(reports)
    rows = []
    unread: list[str] = []
    for agent in fleet.get("agents") or []:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or "")
        if name not in synthetic:
            synthetic[name] = False
            unread.append(name)
        labels = {"agent": name, "synthetic": _bool_label(synthetic.get(name))}
        c.add("fleet_rank", labels, agent.get("rank"))
        c.add("fleet_score", labels, _r(agent.get("score")))
        for task in sorted(agent.get("per_task") or {}):
            cell = agent["per_task"][task] or {}
            rows.append({"agent": name, "task": task, "run": None, "success": bool(cell.get("success")),
                         "steps": cell.get("steps"), "seconds": cell.get("latency_s"), "tokens": cell.get("tokens"),
                         "cost_usd": cell.get("cost_usd"), "tool_calls": cell.get("tool_calls"),
                         "tool_errors": None, "wasted_s": None})
    if unread:
        c.note("no pair report in fleet.json carries a harness note for " + ", ".join(sorted(unread))
               + "; synthetic read as false for them")
    if rows:
        _collect_runs(c, rows, synthetic)
        _collect_advisory(c, {}, rows)
    _collect_paired(c, {"paired_inference": fleet.get("paired_inference")}, synthetic)
    _collect_tools(c, reports, synthetic)
    c.note("fleet: cost, tool errors and wasted seconds are not in fleet.json per task; those families are omitted")
    return c


# ---------------------------------------------------------------- trace

def collect_trace(loaded: dict) -> _Collector:
    """One run, step by step: reward, return so far, latency and tokens
    per step, with the run's totals."""
    c = _Collector()
    trace, path = loaded["trace"], Path(loaded["path"])
    agent = str(((trace.get("agent") or {}).get("name")) or "")
    task = str(((trace.get("task") or {}).get("id")) or "")
    parts = path.stem.split("__")
    run = trace.get("run_id") or (parts[2] if len(parts) >= 3 else None)
    syn = _bool_label(_synthetic_of_harness(trace.get("harness")))
    steps = [s for s in (trace.get("steps") or []) if isinstance(s, dict)]
    recorded = any(finite(s.get("reward")) for s in steps)
    base = {"agent": agent, "task": task, "run": run, "synthetic": syn}
    totals = trace.get("totals") or {}
    c.add("run_success", base, 1 if (trace.get("outcome") or {}).get("success") else 0)
    c.add("run_steps", base, len(steps))
    c.add("run_seconds", base, _r(totals.get("latency_s")))
    tokens = totals.get("tokens")
    if tokens is None and finite(totals.get("input_tokens")) and finite(totals.get("output_tokens")):
        tokens = totals["input_tokens"] + totals["output_tokens"]
    c.add("run_tokens", base, tokens)
    c.add("run_cost_usd", base, _r(totals.get("cost_usd"), 6))
    c.add("run_tool_calls", base, sum(1 for s in steps if s.get("type") in ("tool_call", "search", "retrieve", "read")))
    c.add("run_tool_errors", base, sum(1 for s in steps if s.get("error")))
    cum = 0.0
    for i, s in enumerate(steps):
        idx = s.get("index", i)
        labels = dict(base, step=str(idx), type=str(s.get("type") or ""), name=str(s.get("name") or ""))
        c.add("step_seconds", labels, _r(s.get("latency_s")))
        c.add("step_tokens", labels, s.get("tokens"))
        if recorded:
            reward = s.get("reward") if finite(s.get("reward")) else 0.0
            cum += reward
            c.add("step_reward", labels, _r(reward))
            c.add("step_return_cum", labels, _r(cum))
        c.add("step_value", labels, _r(s.get("value")))
        c.add("step_advantage", labels, _r(s.get("advantage")))
    if recorded:
        c.add("run_return", dict(base, source="recorded"), _r(cum))
    else:
        c.note("no step carries a recorded reward: reward and return families omitted (a shaped reward needs the pair report)")
    c.note("one trace: wasted seconds need the reading of a pair report and are omitted")
    return c


# ---------------------------------------------------------------- collect

def collect(target: Union[str, Path]) -> dict:
    """Every sample the target yields: ``{"version", "kind", "source",
    "samples": [(metric, labels, value)], "notes": [...]}``, samples in
    exposition order."""
    loaded = load_target(target)
    kind = loaded["kind"]
    c = {"batch": collect_batch, "fleet": collect_fleet, "trace": collect_trace}[kind](loaded)
    return {"version": VERSION, "kind": kind, "source": str(loaded["path"]),
            "samples": sort_samples(c.samples), "notes": list(c.notes)}


def sort_samples(samples: Iterable[Sample]) -> list[Sample]:
    """Family order as declared, then labels sorted: the exposition order,
    and the reason two runs give the same bytes."""
    order = {PREFIX + name: i for i, name in enumerate(FAMILIES)}
    return sorted(samples, key=lambda s: (order.get(s[0], len(order)), s[0], sorted(s[1].items())))


# ---------------------------------------------------------------- rendering

def format_value(value: Union[int, float]) -> str:
    """A float as Prometheus (Go's ParseFloat) reads it, stable across
    runs: integers without a point, floats in their shortest repr."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def render_prom(samples: Iterable[Sample]) -> str:
    """The text exposition: HELP and TYPE once per family, before its
    samples, families in declared order, labels sorted by name."""
    out: list[str] = []
    seen: set[str] = set()
    for metric, labels, value in sort_samples(samples):
        if metric not in seen:
            seen.add(metric)
            kind, help_text = FAMILIES.get(metric[len(PREFIX):], ("gauge", ""))
            out.append(f"# HELP {metric} {_escape_help(help_text)}")
            out.append(f"# TYPE {metric} {kind}")
        pairs = ",".join(f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items()))
        out.append(f"{metric}{{{pairs}}} {format_value(value)}" if pairs else f"{metric} {format_value(value)}")
    return "\n".join(out) + ("\n" if out else "")


def render_json(collected: dict) -> dict:
    """The samples as rows and as series keyed by metric, with the family
    metadata and the notes; sorted keys, so the file is stable."""
    rows = [{"metric": m, "labels": dict(sorted(l.items())), "value": v} for m, l, v in collected["samples"]]
    series: dict[str, list] = {}
    for row in rows:
        series.setdefault(row["metric"], []).append({"labels": row["labels"], "value": row["value"]})
    used = {m for m, _l, _v in collected["samples"]}
    return {
        "version": VERSION, "prefix": PREFIX, "kind": collected["kind"], "source": collected["source"],
        "families": {PREFIX + name: {"type": kind, "help": help_text}
                     for name, (kind, help_text) in FAMILIES.items() if PREFIX + name in used},
        "samples": rows, "series": dict(sorted(series.items())), "notes": list(collected["notes"]),
    }


def render_csv(samples: Iterable[Sample]) -> str:
    """One row per sample: metric, value, then every label name in use
    as a column (blank where the sample carries none)."""
    ordered = sort_samples(samples)
    names = sorted({k for _m, labels, _v in ordered for k in labels})
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["metric", "value"] + names)
    for metric, labels, value in ordered:
        writer.writerow([metric, format_value(value)] + [labels.get(k, "") for k in names])
    return buf.getvalue()


# ---------------------------------------------------------------- validation

def validate_exposition(text: str) -> list[str]:
    """The rules Prometheus' text parser enforces, as a list of problems
    (empty = valid): metric and label names match their character sets,
    HELP and TYPE appear at most once per family and before its samples,
    a family's lines are one contiguous group, TYPE names a known type,
    every sample parses, no (metric, labels) pair repeats, and the text
    ends with a line feed.  Timestamps are reported because the textfile
    collector ignores them."""
    problems: list[str] = []
    if text and not text.endswith("\n"):
        problems.append("the last line does not end with a line feed")
    helped: set[str] = set()
    typed: set[str] = set()
    sampled: set[str] = set()
    seen_keys: set[tuple] = set()
    closed: set[str] = set()
    current: Optional[str] = None
    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        if line.startswith("#"):
            tokens = line.split(None, 3)
            if len(tokens) < 2 or tokens[1] not in ("HELP", "TYPE"):
                continue
            if len(tokens) < 3:
                problems.append(f"line {n}: {tokens[1]} without a metric name")
                continue
            name = tokens[2]
            if not _METRIC_RE.match(name):
                problems.append(f"line {n}: metric name {name!r} is not [a-zA-Z_:][a-zA-Z0-9_:]*")
            if name in sampled:
                problems.append(f"line {n}: {tokens[1]} for {name} after its samples")
            if name != current:
                if name in closed:
                    problems.append(f"line {n}: {name} appears in more than one group")
                if current is not None:
                    closed.add(current)
                current = name
            if tokens[1] == "HELP":
                if name in helped:
                    problems.append(f"line {n}: second HELP for {name}")
                helped.add(name)
            else:
                if name in typed:
                    problems.append(f"line {n}: second TYPE for {name}")
                typed.add(name)
                kind = tokens[3].strip() if len(tokens) > 3 else ""
                if kind not in _TYPES:
                    problems.append(f"line {n}: TYPE {name} {kind!r} is not one of {sorted(_TYPES)}")
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            problems.append(f"line {n}: does not parse as a sample: {line[:80]!r}")
            continue
        name = m.group("name")
        if name != current:
            if name in closed:
                problems.append(f"line {n}: {name} appears in more than one group")
            if current is not None:
                closed.add(current)
            current = name
        sampled.add(name)
        labels: list[tuple[str, str]] = []
        raw = m.group("labels") or ""
        for pair in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"', raw):
            key = pair.group(1)
            if not _LABEL_RE.match(key):
                problems.append(f"line {n}: label name {key!r} is not [a-zA-Z_][a-zA-Z0-9_]*")
            if key.startswith("__"):
                problems.append(f"line {n}: label name {key!r} is reserved (double underscore)")
            labels.append((key, pair.group(2)))
        names = [k for k, _v in labels]
        if len(set(names)) != len(names):
            problems.append(f"line {n}: a label name repeats")
        value = m.group("value")
        if value not in ("NaN", "+Inf", "-Inf", "Inf"):
            try:
                float(value)
            except ValueError:
                problems.append(f"line {n}: value {value!r} is not a float")
        if m.group("ts"):
            problems.append(f"line {n}: carries a timestamp, which the textfile collector ignores")
        key_t = (name, tuple(sorted(labels)))
        if key_t in seen_keys:
            problems.append(f"line {n}: duplicate sample {name}{{{raw}}}")
        seen_keys.add(key_t)
    return problems


# ---------------------------------------------------------------- export

def export(target: Union[str, Path], out_dir: Union[str, Path]) -> dict:
    """Write ``metrics.prom``, ``metrics.json`` and ``metrics.csv`` for the
    target into ``out_dir``; returns ``{"kind", "files", "samples",
    "families", "notes"}``.  Raises ``ValueError`` when the exposition
    it produced does not validate, rather than writing it."""
    collected = collect(target)
    prom = render_prom(collected["samples"])
    problems = validate_exposition(prom)
    if problems:
        raise ValueError("the exposition does not validate: " + "; ".join(problems[:5]))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "prom": out / "metrics.prom",
        "json": out / "metrics.json",
        "csv": out / "metrics.csv",
    }
    files["prom"].write_text(prom, encoding="utf-8")
    files["json"].write_text(json.dumps(render_json(collected), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                             encoding="utf-8")
    files["csv"].write_text(render_csv(collected["samples"]), encoding="utf-8")
    return {
        "kind": collected["kind"], "source": collected["source"],
        "files": {k: str(v) for k, v in files.items()},
        "samples": len(collected["samples"]),
        "families": sorted({m for m, _l, _v in collected["samples"]}),
        "notes": collected["notes"],
    }
