"""An eval that evolves with the agent it reads: probes propose metrics,
validators test them, and the lineage is re-read with hindsight.

:mod:`deepcompare.evolve` reads a lineage g0 → g1 → … against a *fixed*
set of metrics and checks, and its own docstring names the gap: an
agent that evolves against a fixed eval eventually optimises the eval,
and when the grader itself was fooled every number is compromised. This
module is the answer that stays inside the episodes: an eval that is
itself a lineage e0 → e1 → …, each eval step triggered by an agent step.
When a step exposes a blind spot — the reward and the outcome disagree,
a metric can no longer move, a tool appears or disappears, a task is
lost — a **probe** proposes candidate metrics, **validators** test each
on the evidence so far, the eval adopts or rejects with a reason, and
at the end the *final* eval is applied to every step of the lineage so
a reader sees which steps the base eval called improved that the
evolved eval would have flagged, and how many steps late each metric
arrived.

**What is computed, and from what.** Every episode is reduced once to a
fixed vocabulary of **features** (:data:`FEATURES`), each a count, a
sum, a boolean or a ratio over the episode's recorded steps — the
return, the outcome, the tool calls and errors, the calls that match
:data:`deepcompare.evolve.CHECK_TOOL_RE` or a protected tool, the
retries, the seconds, the answer length, the claim phrases of
:data:`deepcompare.evolve.CLAIM_PHRASES`, the critic's absolute error
against the discounted return-to-go (:func:`deepcompare.rlaudit.discounted_to_go`),
the tokens — plus one dynamic ``uses:<tool>`` count per tool name the
lineage calls. A feature that cannot be read is ``None``, never 0. A
**metric** is a spec in a small language: a feature, an aggregation
(``mean``, ``rate`` — the fraction positive —, ``iqm`` — the task-balanced
IQM, the rule of :func:`deepcompare.evolve.iqm_by_task` —, ``task_mean``,
``task_min``, ``task_spread``), an optional ``where`` predicate that
filters episodes before aggregating, and a direction. Every value of a
metric is a point with a stratified-bootstrap interval (runs redrawn
within each task, every task keeping its count, the stream seeded by
the spec's *key* — feature, aggregation and filter — through
:func:`deepcompare._stats.rng`, so two specs that read the same thing
share one interval whatever their ids); every step delta is a
percentile-bootstrap interval at level ``1 − alpha``, and an interval is
never a bare point: with no bootstrap draw the value is unmeasurable. The base eval
e0 is the four numbers the shipped Evolution reading already uses, in
the same language, so the matrix is uniform; base metrics are never
retired or demoted, because a number that changes meaning across time
cannot be compared across time.

**The probes**, each one question, run at every agent step in lineage
order with what is known *up to that step* and no lookahead: ``axes``
(when return and outcome disagree, which feature explains it — every
unadopted feature ranked by its standardised shift, the top
:data:`PROBE_TOP`); ``ceiling`` (a rate at 0 or 1 is not measuring —
propose the pass rate within a condition); ``novelty`` (a tool that
appears or disappears, a claim where none was, needs a watcher);
``forgetting`` (the worst task and the task spread, because an average
hides a task); ``goodhart`` (a non-outcome metric the agent moved twice
while the pass rate did not is a metric the agent learned — demote it,
propose its outcome-conditioned variant); ``redundancy`` (two metrics
with |Spearman ρ| ≥ :data:`REDUNDANT_RHO` are one metric — retire the
newer); ``external`` (candidates a caller supplied, from a model
through the harness or from a file, parsed by :func:`parse_spec` and
put through the same validators — they can never set a number, a
verdict or an exit code).

**The validators**, in order, the first failure deciding while every
one is still computed and written to the ledger row: ``computable``
(the feature is readable on at least :data:`MIN_COVERAGE` of the
episodes so far and the metric is measurable on both sides of the
step); ``informative`` (the step's delta interval excludes zero at the
Bonferroni level ``ALPHA / K`` over the K candidates tested at the
step — a candidate that fails ``not_already`` is a ledger row, not a
test, and is not one of the K; a ceiling candidate instead has to sit
strictly inside (0, 1) with an interval of some width); ``distinct`` (max |ρ| against every
adopted metric under :data:`REDUNDANT_RHO`: per episode when both are
unfiltered episode-level metrics, on the generation series once
:data:`MIN_SERIES` generations exist when either is task-level or
filtered — a filter changes the episode basis, so the comparison moves
to the level where the two can differ —, "not testable yet" otherwise);
``linked`` (|ρ(feature, success)| ≥ :data:`LINK_RHO` over the episodes
so far, exempt for the ``novelty`` and ``ceiling`` probes, which watch
behaviour and strictness rather than outcome, and for outcome-
conditioned specs, whose link is by construction); ``not_already``
(the same feature, aggregation and filter is not already adopted,
demoted or retired). A step's candidates are validated **as a batch**:
the four evidence validators run on every candidate, the survivors are
grouped into redundancy classes (|ρ| ≥ ``REDUNDANT_RHO`` with each
other, transitively), and one representative per class is adopted,
chosen by a stated rule — (a) the strongest |ρ| with the outcome, (b)
the more interpretable kind (a bool rate over a count mean over a
ratio), (c) a feature tied to a protected path, (d) vocabulary order,
(e) the spec id — so which metric the eval learns is never decided by
the order the probes happened to propose in; the rest of the class fail ``distinct``
with a note naming the representative and the rule. A rejected
candidate may be proposed again at a later step and is tested again;
the ledger keeps every attempt. After
adoption, every later step tests the metric out of sample at the
level it was adopted at (``ALPHA / K`` of its step, written into
``confirmation.alpha``): ``confirmed`` once it moved again,
``unconfirmed`` after :data:`CONFIRM_STEPS` tests without a move,
``pending`` between. An external candidate whose ``at`` names no walked
step is a rejected ledger row (``unaddressed``), never silently lost.

**The thresholds and why.** ``ALPHA`` 0.05 divided by the candidates
tested at a step (Bonferroni, 1936), because an eval that tests six
candidates at 0.05 each adopts one on noise every third step;
``REDUNDANT_RHO`` 0.9, past which two rankings of the same episodes
differ in a handful of places and the newer one adds a column, not a
reading; ``LINK_RHO`` 0.15, the weakest rank correlation with the
outcome worth a metric at thirty episodes per generation (below it a
feature moves the eval without moving what the eval is for);
``MIN_COVERAGE`` 0.8 and ``MIN_N`` 4, under which a metric would be read
off a minority of the episodes or fewer runs than a task holds;
``PROBE_TOP`` 3, so the ``axes`` probe cannot flood a step with every
feature and dilute the level; ``CONFIRM_STEPS`` 2, the fewest later
steps on which "never moved again" is a statement and not an absence.

**Hindsight** is the payoff: the final eval applied to every
generation (``matrix``; every cell also carries ``per_task``, the metric
read within each task with a bootstrap over that task's own runs, so a
task an average hides is one cell away — :func:`per_task`) and every step; per step the base verdict and
flags untouched beside the evolved flags — the *learned* metrics
(adopted, not demoted, not retired) whose delta interval, at the level
each was adopted at, excludes zero in their bad direction, each marked
``learned: true`` — and, separately
under ``base_flags``, the base metrics whose own intervals move against
their direction, which the base verdict does not read because it reads
the two P(improve) axes instead; ``changed`` when the base said improved
or flat and a learned metric flags, so what the eval learned is never
confused with what the base already carried; ``caught_at`` per adopted
metric is the first step it would have flagged against the step it was
adopted at. The evolved recommendation is the base rule with one more
exclusion, a generation whose incoming step carries a learned flag on
an outcome-linked ``up`` metric, and ``agree`` says whether the two
rules pick the same generation. The whole loop is also written out as
one graph (``flow``): generations, steps, the probes that fired, every
candidate through the validators to a decision, the eval generations,
the metrics, the flags, and ``recovers`` edges — a later step on which
a flagged metric moved back in its good direction with an interval
excluding zero, labelled *recovered, not attributed*, because the
lineage's recovery is a measured fact and its cause is not. The eval's own **integrity** is measured the way the
agent's is: drift from the base (Jaccard), multiplicity (tested,
adopted, the smallest adjusted level), what was demoted, retired and
never confirmed, what the external proposer sent and what survived.

**The honest gap.** Every candidate reads the episodes as recorded; a
grader that was fooled fools every metric in this vocabulary; the eval
can only learn what the feature vocabulary can express, and an
external proposal extends the vocabulary only through the same
validators. Nothing here talks to a network: the engine never imports
the harness, and the proposer seam (``deepcompare/harness/proposer.py``)
is imported by the command alone.

Determinism: every random draw goes through :func:`deepcompare._stats.rng`
with the section name ``coevolve`` and a label; every dict written is in
lineage, eval or sorted order; every number is rounded to four places
with :func:`deepcompare._stats.rounded`; every interval is an interval.
Cost: the feature table is computed once, per-metric values and
bootstrap distributions are cached, and only what a validator or the
matrix needs is bootstrapped, so the demo lineage (seven generations of
thirty episodes) reads in a few seconds.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import sections
from ._stats import CONFIDENCE, finite, iqm, mean, median, percentile_interval, pvar, rng, rounded
from ._text import join_names, num, plural, signed
from .evolve import CHECK_TOOL_RE, CLAIM_PHRASES, TOOLISH, _answer_text
from .rl import GAMMA
from .rlaudit import discounted_to_go, spearman
from .rlstats import BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED
from .section import measurable, unmeasurable
from .trace import Trajectory

VERSION = 1
#: the family-wise level a step's candidates are tested at; divided by the candidates tested (Bonferroni)
ALPHA = 0.05
#: two metrics whose |Spearman ρ| reaches this are one metric
REDUNDANT_RHO = 0.9
#: the weakest |Spearman ρ| with the outcome worth a metric
LINK_RHO = 0.15
#: a feature must be readable on this share of the episodes so far
MIN_COVERAGE = 0.8
#: a metric needs at least this many episodes after its filter
MIN_N = 4
#: the ``axes`` probe proposes at most this many features per step
PROBE_TOP = 3
#: an adopted metric untested this many later steps without moving is unconfirmed
CONFIRM_STEPS = 2
#: a generation-level correlation needs this many generations
MIN_SERIES = 4
#: the stream every draw here comes from
SECTION = "coevolve"
AGGS = ("mean", "rate", "iqm", "task_mean", "task_min", "task_spread")
OPS = ("==", "!=", ">", "<", ">=", "<=")
DIRECTIONS = ("up", "down", "neutral")
STATUSES = ("base", "adopted", "demoted", "retired")
FAIL_ON = ("hindsight", "demoted", "unconfirmed", "rejected_external")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


@dataclass(frozen=True)
class Feature:
    """One entry of the vocabulary: its kind (``count``, ``sum``, ``bool``,
    ``ratio``, ``estimate``), the sentence that says what it is counted
    from, and the prior direction (``up`` better, ``down`` better,
    ``neutral``) the ledger records — the validators use the observed
    sign against the outcome, never this prior."""
    kind: str
    basis: str
    direction: str


#: the fixed vocabulary, in the order the output lists it
FEATURES: dict = {
    "return": Feature("sum", "the sum of the step rewards recorded in the episode; None when no step records one", "up"),
    "success": Feature("bool", "outcome.success as 1 / 0", "up"),
    "steps": Feature("count", "steps recorded", "down"),
    "tool_calls": Feature("count", "tool steps (tool_call, search, retrieve, read)", "down"),
    "tool_errors": Feature("count", "tool steps flagged error", "down"),
    "distinct_tools": Feature("count", "distinct tool names called", "neutral"),
    "check_calls": Feature("count", "tool steps whose name matches the check pattern or a protected tools.<name>", "up"),
    "verified": Feature("bool", "check_calls > 0", "up"),
    "rewarded_tool_steps": Feature("count", "tool steps paid a reward above zero", "neutral"),
    "retries": Feature("count", "tool steps repeating an earlier (name, arguments) of the same episode", "down"),
    "seconds": Feature("sum", "wall clock: the sum of the steps' latencies; None when no step carries one above zero "
                              "(the trace's default for an unrecorded latency is 0)", "down"),
    "answer_chars": Feature("count", "characters of the answer text; None when the episode has none", "neutral"),
    "claims": Feature("bool", "the answer text contains a claim phrase; None when the episode has no answer text", "neutral"),
    "unverified_claim": Feature("bool", "claims and not verified", "down"),
    "answer_share": Feature("ratio", "the answer step's reward over the sum of |step rewards|; None when that sum is 0", "neutral"),
    "errors_per_call": Feature("ratio", "tool_errors / tool_calls; None when the episode made no tool call", "down"),
    "steps_after_last_tool": Feature("count", "steps after the last tool step; None when there is none", "neutral"),
    "critic_error": Feature("estimate", "mean |value − discounted return-to-go| over the steps carrying a value; None when none does "
                                        "or when no step records a reward", "down"),
    "tokens": Feature("sum", "step token counts summed; None when no step carries one", "down"),
}
#: how a feature is spelled in a metric's name
_NAMES = {"return": "return", "success": "pass", "steps": "steps", "tool_calls": "tool calls",
          "tool_errors": "tool errors", "distinct_tools": "distinct tools", "check_calls": "check calls",
          "verified": "verification", "rewarded_tool_steps": "rewarded tool steps", "retries": "retries",
          "seconds": "seconds", "answer_chars": "answer length", "claims": "claims",
          "unverified_claim": "unverified claims", "answer_share": "answer share of reward",
          "errors_per_call": "errors per call", "steps_after_last_tool": "steps after the last tool",
          "critic_error": "critic error", "tokens": "tokens"}
_AGG_WORDS = {"mean": "the mean of", "rate": "the rate of", "iqm": "the task-balanced IQM of",
              "task_mean": "the per-task mean of", "task_min": "the worst task's mean of",
              "task_spread": "the spread of the per-task means of"}

#: the base eval e0: the numbers the Evolution reading already uses, in the metric language
BASE_SPECS: tuple = (
    {"id": "return_iqm", "name": "return IQM", "feature": "return", "agg": "iqm", "where": None, "direction": "up"},
    {"id": "pass_rate", "name": "pass rate", "feature": "success", "agg": "rate", "where": None, "direction": "up"},
    {"id": "tool_calls_mean", "name": "mean tool calls", "feature": "tool_calls", "agg": "mean", "where": None,
     "direction": "down"},
    {"id": "tool_errors_mean", "name": "mean tool errors", "feature": "tool_errors", "agg": "mean", "where": None,
     "direction": "down"},
)

GAP = ("every candidate reads the episodes as recorded; a grader that was fooled fools every metric in this "
       "vocabulary; the eval can only learn what the feature vocabulary can express, and an external proposal "
       "extends the vocabulary only through the same validators")


# ---------------------------------------------------------------- features

def _tool_name(st) -> str:
    return st.name or st.type or "?"


def features(trajectory: Trajectory, protected=()) -> dict:
    """Every feature of :data:`FEATURES` for one episode, plus one
    ``uses:<tool>`` count per tool name the episode called, each a count,
    a sum, a 1 / 0 or a ratio over the recorded steps and ``None`` where
    the episode does not carry what the feature reads. ``protected`` is
    the lineage's protected paths; a ``tools.<name>`` among them counts
    as a check call whatever its name."""
    steps = list(trajectory.steps)
    names = {p[len("tools."):] for p in protected if isinstance(p, str) and p.startswith("tools.")}
    tools = [st for st in steps if st.type in TOOLISH]
    rewards = [st.reward if finite(st.reward) else None for st in steps]
    recorded = [r for r in rewards if r is not None]
    tool_errors = sum(1 for st in tools if st.error)
    check_calls = sum(1 for st in tools if (_tool_name(st) in names) or bool(CHECK_TOOL_RE.search(_tool_name(st))))
    seen: set = set()
    retries = 0
    for st in tools:
        key = (_tool_name(st), st.input)
        if key in seen:
            retries += 1
        seen.add(key)
    text = _answer_text(trajectory)
    claims = None if text is None else (1 if any(p in text.lower() for p in CLAIM_PHRASES) else 0)
    verified = 1 if check_calls > 0 else 0
    answer_reward = rewards[-1] if steps and steps[-1].type == "answer" else None
    abs_sum = sum(abs(r) for r in recorded)
    valued = [(i, st.value) for i, st in enumerate(steps) if finite(st.value)]
    critic = None
    if valued and recorded:   # a return-to-go over rewards nobody recorded is not a residual
        to_go = discounted_to_go([r if r is not None else 0.0 for r in rewards], GAMMA)
        critic = mean([abs(v - to_go[i]) for i, v in valued])
    last_tool = max((i for i, st in enumerate(steps) if st.type in TOOLISH), default=None)
    tokens = [int(st.tokens) for st in steps if finite(st.tokens) and (st.tokens > 0 or st.tokens_basis)]
    latencies = [float(st.latency_s) for st in steps if finite(st.latency_s) and st.latency_s >= 0]
    if not any(lat > 0 for lat in latencies):   # the trace format reads an unrecorded latency as 0, as it does tokens
        latencies = []
    out = {
        "return": sum(recorded) if recorded else None,
        "success": 1 if trajectory.outcome.success is True else 0,
        "steps": len(steps),
        "tool_calls": len(tools),
        "tool_errors": tool_errors,
        "distinct_tools": len({_tool_name(st) for st in tools}),
        "check_calls": check_calls,
        "verified": verified,
        "rewarded_tool_steps": sum(1 for st in tools if finite(st.reward) and st.reward > 0),
        "retries": retries,
        "seconds": sum(latencies) if latencies else None,
        "answer_chars": None if text is None else len(text),
        "claims": claims,
        "unverified_claim": None if claims is None else (1 if claims and not verified else 0),
        "answer_share": (answer_reward / abs_sum) if answer_reward is not None and abs_sum > 0 else None,
        "errors_per_call": (tool_errors / len(tools)) if tools else None,
        "steps_after_last_tool": None if last_tool is None else len(steps) - 1 - last_tool,
        "critic_error": critic,
        "tokens": sum(tokens) if tokens else None,
    }
    counts: dict = {}
    for st in tools:
        counts[_tool_name(st)] = counts.get(_tool_name(st), 0) + 1
    for name in sorted(counts):
        out[f"uses:{name}"] = counts[name]
    return out


def feature_table(lineage: dict, protected=None) -> list:
    """Per generation of a :func:`deepcompare.evolve.read_lineage` result, in
    lineage order: ``{"id", "index", "episodes": [{"id", "run", "task",
    "values"}]}``. Every episode carries every dynamic ``uses:<tool>``
    feature of the lineage (0 when the episode did not call the tool), so
    the vocabulary is the same across generations. ``protected`` defaults
    to the lineage's own list."""
    protected = list(lineage.get("protected") or []) if protected is None else list(protected)
    rows = []
    tools: set = set()
    for g in lineage.get("generations") or []:
        eps = []
        for traj in g.get("trajectories") or []:
            vals = features(traj, protected)
            tools.update(k for k in vals if k.startswith("uses:"))
            eps.append({"id": traj.trace_id, "run": traj.run_id, "task": traj.task.id, "values": vals})
        rows.append({"id": g["id"], "index": g.get("index", len(rows)), "episodes": eps})
    for row in rows:
        for e in row["episodes"]:
            for t in tools:
                e["values"].setdefault(t, 0)
    return rows


def _vocabulary(table: list, upto: Optional[int] = None) -> list:
    """The feature ids in force: the fixed vocabulary plus the dynamic
    tools seen in the generations up to ``upto`` (inclusive; all when None)."""
    dyn: set = set()
    for row in table if upto is None else table[:upto + 1]:
        for e in row["episodes"]:
            dyn.update(k for k in e["values"] if k.startswith("uses:") and e["values"][k])
    return list(FEATURES) + sorted(dyn)


def _feature_name(feature: str) -> str:
    return _NAMES.get(feature) or (f"calls of {feature[5:]}" if feature.startswith("uses:") else feature)


def _metric_name(feature: str, agg: str) -> str:
    name = _feature_name(feature)
    return {"mean": f"mean {name}", "rate": f"{name} rate", "iqm": f"{name} IQM", "task_mean": f"{name} per task",
            "task_min": f"worst task {name}", "task_spread": f"{name} spread across tasks"}[agg]


# ---------------------------------------------------------------- the metric language

def _parse_predicate(obj, vocabulary: list, where: str) -> dict:
    if not isinstance(obj, dict):
        raise ValueError(f"{where} must be an object with feature, op and value")
    feature, op, value = obj.get("feature"), obj.get("op"), obj.get("value")
    if feature not in vocabulary:
        raise ValueError(f"{where} names an unknown feature {feature!r}")
    if op not in OPS:
        raise ValueError(f"{where} uses an unknown operator {op!r}; one of {', '.join(OPS)}")
    if isinstance(value, bool):
        value = 1 if value else 0
    if not finite(value):
        raise ValueError(f"{where} needs a finite number as value, got {json.dumps(value)}")
    return {"feature": feature, "op": op, "value": value}


def parse_spec(obj, features: Optional[list] = None) -> dict:
    """A metric spec, validated against the vocabulary and normalised to
    ``{id, name, feature, agg, where, direction, origin}``; raises
    ``ValueError`` with the reason the ledger records — an unknown
    feature, aggregation, operator or direction, a malformed predicate, an
    id outside ``[A-Za-z0-9_.:-]``. ``features`` is the vocabulary in
    force (the fixed one when None): a ``uses:<tool>`` feature is known
    only once the tool has been seen."""
    vocabulary = list(FEATURES) if features is None else list(features)
    if not isinstance(obj, dict):
        raise ValueError(f"a spec must be an object, got {type(obj).__name__}")
    sid = obj.get("id")
    if not isinstance(sid, str) or not _ID_RE.match(sid):
        raise ValueError(f"spec id must match {_ID_RE.pattern}, got {json.dumps(sid)}")
    feature = obj.get("feature")
    if feature not in vocabulary:
        raise ValueError(f"unknown feature {json.dumps(feature)}; the vocabulary is {', '.join(vocabulary)}")
    agg = obj.get("agg")
    if agg not in AGGS:
        raise ValueError(f"unknown aggregation {json.dumps(agg)}; one of {', '.join(AGGS)}")
    direction = obj.get("direction", "neutral")
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction {json.dumps(direction)}; one of {', '.join(DIRECTIONS)}")
    raw_where = obj.get("where")
    if raw_where is None:
        where = None
    elif isinstance(raw_where, dict) and "all" in raw_where:
        if not isinstance(raw_where["all"], list) or not raw_where["all"]:
            raise ValueError("where.all must be a non-empty list of predicates")
        where = {"all": [_parse_predicate(p, vocabulary, f"where.all[{i}]") for i, p in enumerate(raw_where["all"])]}
    else:
        where = _parse_predicate(raw_where, vocabulary, "where")
    name = obj.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("name must be a string when given")
    origin = obj.get("origin")
    if origin is not None and not isinstance(origin, dict):
        raise ValueError("origin must be an object when given")
    return {"id": sid, "name": name or _metric_name(feature, agg), "feature": feature, "agg": agg, "where": where,
            "direction": direction, "origin": dict(origin) if origin else None}


def _spec_key(spec: dict) -> str:
    return f"{spec['feature']}|{spec['agg']}|{json.dumps(spec.get('where'), sort_keys=True)}"


def _predicates(where) -> list:
    if where is None:
        return []
    return list(where["all"]) if "all" in where else [where]


def _matches(where, values: dict) -> bool:
    """An episode passes the filter when every predicate holds; a
    predicate on a feature the episode cannot read does not hold."""
    for p in _predicates(where):
        v = values.get(p["feature"])
        if v is None:
            return False
        w = p["value"]
        ok = {"==": v == w, "!=": v != w, ">": v > w, "<": v < w, ">=": v >= w, "<=": v <= w}[p["op"]]
        if not ok:
            return False
    return True


def _conditioned_on_outcome(spec: dict) -> bool:
    return any(p["feature"] == "success" for p in _predicates(spec.get("where")))


def _outcome_spec(spec: dict) -> bool:
    return spec["feature"] == "success" or _conditioned_on_outcome(spec)


def _episode_level(spec: dict) -> bool:
    """True when the metric has a per-episode value on an unfiltered
    basis: ``mean`` or ``rate`` with no ``where``."""
    return spec["agg"] in ("mean", "rate") and spec.get("where") is None


def _as_value(v, agg: str) -> float:
    """The number an episode contributes: for a rate the 1 / 0 of ``v``
    being positive, otherwise ``v`` itself."""
    if agg == "rate":
        return 1.0 if v > 0 else 0.0
    return float(v)


def _select(spec: dict, episodes: list) -> tuple:
    """``(n, by_task, missing)``: the episodes after the filter, their
    non-None values by task, and how many of them could not read the
    feature."""
    feature, agg, where = spec["feature"], spec["agg"], spec.get("where")
    n, missing = 0, 0
    by_task: dict = {}
    for e in episodes:
        vals = e["values"]
        if not _matches(where, vals):
            continue
        n += 1
        v = vals.get(feature)
        if v is None:
            missing += 1
            continue
        by_task.setdefault(e["task"], []).append(_as_value(v, agg))
    return n, by_task, missing


def _aggregate(agg: str, by_task: dict) -> Optional[float]:
    tasks = sorted(t for t in by_task if by_task[t])
    if not tasks:
        return None
    if agg in ("mean", "rate"):
        flat = [v for t in tasks for v in by_task[t]]
        return sum(flat) / len(flat)
    per = [iqm(by_task[t]) if agg == "iqm" else mean(by_task[t]) for t in tasks]
    if agg in ("iqm", "task_mean"):
        return sum(per) / len(per)
    if agg == "task_min":
        return min(per)
    return max(per) - min(per)


def _bootstrap(agg: str, by_task: dict, samples: int, stream) -> list:
    """``samples`` values of the aggregate over stratified resamples: each
    task's runs redrawn with replacement, every task keeping its count,
    tasks visited in sorted order so the stream is reproducible. The
    draws are made in one call per task and read back in slices."""
    tasks = sorted(t for t in by_task if by_task[t])
    samples = max(0, int(samples))
    if not tasks or not samples:
        return []
    draws = {t: stream.choices(by_task[t], k=len(by_task[t]) * samples) for t in tasks}
    sizes = {t: len(by_task[t]) for t in tasks}
    out = []
    if agg in ("mean", "rate"):
        total_n = sum(sizes.values())
        for s in range(samples):
            total = 0.0
            for t in tasks:
                n = sizes[t]
                total += sum(draws[t][s * n:(s + 1) * n])
            out.append(total / total_n)
        return out
    for s in range(samples):
        per = []
        for t in tasks:
            n = sizes[t]
            seg = draws[t][s * n:(s + 1) * n]
            per.append(iqm(seg) if agg == "iqm" else sum(seg) / n)
        if agg in ("iqm", "task_mean"):
            out.append(sum(per) / len(per))
        elif agg == "task_min":
            out.append(min(per))
        else:
            out.append(max(per) - min(per))
    return out


def _basis(spec: dict, n: int, tasks: int) -> str:
    where = spec.get("where")
    cond = ""
    if where is not None:
        cond = " among the episodes where " + " and ".join(f"{p['feature']} {p['op']} {num(p['value'])}"
                                                           for p in _predicates(where))
    return (f"{_AGG_WORDS[spec['agg']]} {spec['feature']} over {plural(n, 'episode')} on {plural(tasks, 'task')}{cond}; "
            f"a stratified bootstrap over the runs recorded, resampled within each task")


def _point(spec: dict, episodes: list) -> dict:
    """The point of a metric on ``episodes`` with the reasons it cannot be
    read: fewer than :data:`MIN_N` after the filter, or the feature
    unreadable on more than ``1 − MIN_COVERAGE`` of them."""
    n, by_task, missing = _select(spec, episodes)
    if n < MIN_N:
        return {"measurable": False, "reason": f"{plural(n, 'episode')} after the filter, under the {MIN_N} needed",
                "n": n, "by_task": by_task, "coverage": None, "point": None}
    coverage = 1.0 - missing / n
    if coverage < MIN_COVERAGE - 1e-9:
        return {"measurable": False, "reason": f"{spec['feature']} is unreadable on {missing} of {n} episodes "
                                                f"(coverage {num(coverage)} under {num(MIN_COVERAGE)})",
                "n": n, "by_task": by_task, "coverage": coverage, "point": None}
    return {"measurable": True, "reason": None, "n": n, "by_task": by_task, "coverage": coverage,
            "point": _aggregate(spec["agg"], by_task)}


def _no_draws(samples: int) -> str:
    return f"no bootstrap draw: {int(samples)} samples, at least 1 needed; an interval is never a bare point"


def value(spec: dict, episodes: list, samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """The metric on a list of episodes (feature-table rows): ``{point,
    lo, hi, n, tasks, coverage, basis}`` with a stratified-bootstrap
    interval at :data:`deepcompare._stats.CONFIDENCE`, seeded by the
    spec's key (feature, aggregation and filter — :func:`_spec_key`), so
    two specs that read the same thing share one interval whatever their
    ids; ``measurable: False`` with the reason when fewer than
    :data:`MIN_N` episodes survive the filter, the feature is unreadable
    on more than ``1 − MIN_COVERAGE`` of them, or ``samples`` is under 1
    (an interval is never a bare point). ``n`` counts the episodes after
    the filter."""
    pt = _point(spec, episodes)
    if not pt["measurable"] or int(samples) < 1:
        reason = pt["reason"] if not pt["measurable"] else _no_draws(samples)
        return unmeasurable(reason, point=None, lo=None, hi=None, n=pt["n"], tasks=len(pt["by_task"]),
                            coverage=rounded(pt["coverage"]), basis=_basis(spec, pt["n"], len(pt["by_task"])))
    boots = _bootstrap(spec["agg"], pt["by_task"], samples, rng(BOOTSTRAP_SEED, _spec_key(spec), section=SECTION))
    lo, hi = percentile_interval(boots, CONFIDENCE)
    point = pt["point"]
    return measurable({"point": rounded(point), "lo": rounded(lo), "hi": rounded(hi), "n": pt["n"],
                       "tasks": len(pt["by_task"]), "coverage": rounded(pt["coverage"]),
                       "basis": _basis(spec, pt["n"], len(pt["by_task"]))})


#: what a per-task cell is, per aggregation: the task's own mean or rate, and
#: for the three aggregations that are not a per-task mean, the note that says so
_PER_TASK_NOTE = {"iqm": "the task's own mean, not its IQM: the metric's aggregate is the task-balanced IQM",
                  "task_min": "the task's own mean: the metric's aggregate is the worst task's",
                  "task_spread": "the task's own mean: the metric's aggregate is the spread across tasks"}


def per_task(spec: dict, episodes: list, samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """The metric read within each task of ``episodes``: ``{task: {point,
    lo, hi, n, measurable, reason}}`` in sorted task order, each a
    percentile-bootstrap interval at :data:`deepcompare._stats.CONFIDENCE`
    over the task's own runs redrawn with replacement (``samples`` draws,
    the stream seeded ``<spec key>:<task>``). The point is the task's own
    mean (for ``rate`` the fraction positive), which is the per-task
    meaning of ``mean``, ``rate`` and ``task_mean``; for ``iqm``,
    ``task_min`` and ``task_spread`` — whose aggregate is not a per-task
    mean — the cell carries a ``note`` saying so. ``n`` counts the task's
    episodes after the filter, as the generation's cell does; a task is
    ``measurable: False`` with the reason under :data:`MIN_N` of them, or
    with the feature readable on under :data:`MIN_COVERAGE` of them, or
    with ``samples`` under 1 (no bootstrap draw, so no interval).
    Every number is rounded to four places; no draw here is shared with
    the generation's own interval."""
    feature, agg, where = spec["feature"], spec["agg"], spec.get("where")
    counts: dict = {}
    values: dict = {}
    for e in episodes:
        vals = e["values"]
        if not _matches(where, vals):
            continue
        counts[e["task"]] = counts.get(e["task"], 0) + 1
        v = vals.get(feature)
        if v is not None:
            values.setdefault(e["task"], []).append(_as_value(v, agg))
    note = _PER_TASK_NOTE.get(agg)
    out: dict = {}
    samples = max(0, int(samples))
    for task in sorted(counts):
        vs = values.get(task) or []
        k, n = len(vs), counts[task]
        cell: dict
        if n < MIN_N:
            cell = {"point": None, "lo": None, "hi": None, "n": n, "measurable": False,
                    "reason": f"{plural(n, 'episode')} of the task after the filter, under the {MIN_N} needed"}
        elif k / n < MIN_COVERAGE - 1e-9:
            cell = {"point": None, "lo": None, "hi": None, "n": n, "measurable": False,
                    "reason": f"{feature} is unreadable on {n - k} of the task's {n} episodes "
                              f"(coverage {num(k / n)} under {num(MIN_COVERAGE)})"}
        elif not samples:
            cell = {"point": None, "lo": None, "hi": None, "n": n, "measurable": False, "reason": _no_draws(samples)}
        else:
            point = sum(vs) / k
            draws = rng(BOOTSTRAP_SEED, f"{_spec_key(spec)}:{task}", section=SECTION).choices(vs, k=k * samples)
            boots = [sum(draws[s * k:(s + 1) * k]) / k for s in range(samples)]
            lo, hi = percentile_interval(boots, CONFIDENCE)
            cell = {"point": rounded(point), "lo": rounded(lo), "hi": rounded(hi), "n": n, "measurable": True, "reason": None}
        if note:
            cell["note"] = note
        out[task] = cell
    return out


def _delta_draws(spec: dict, parent_eps: list, child_eps: list, samples: int) -> dict:
    """The delta's point and its bootstrap distribution (child − parent,
    both sides resampled from the one stream ``delta:<spec key>``), or
    why not — a side unmeasurable, or no bootstrap draw."""
    a, b = _point(spec, parent_eps), _point(spec, child_eps)
    if not a["measurable"] or not b["measurable"]:
        side = "the parent" if not a["measurable"] else "the child"
        return {"measurable": False, "reason": f"unmeasurable on {side}: {(a if not a['measurable'] else b)['reason']}",
                "point": None, "diffs": [], "n_from": a["n"], "n_to": b["n"], "from": a["point"], "to": b["point"]}
    if int(samples) < 1:
        return {"measurable": False, "reason": _no_draws(samples), "point": None, "diffs": [],
                "n_from": a["n"], "n_to": b["n"], "from": a["point"], "to": b["point"]}
    stream = rng(BOOTSTRAP_SEED, f"delta:{_spec_key(spec)}", section=SECTION)
    boots_a = _bootstrap(spec["agg"], a["by_task"], samples, stream)
    boots_b = _bootstrap(spec["agg"], b["by_task"], samples, stream)
    return {"measurable": True, "reason": None, "point": b["point"] - a["point"],
            "diffs": [y - x for x, y in zip(boots_a, boots_b)], "n_from": a["n"], "n_to": b["n"],
            "from": a["point"], "to": b["point"]}


def _delta_from_draws(draws: dict, alpha: float) -> dict:
    if not draws["measurable"]:
        return unmeasurable(draws["reason"], point=None, lo=None, hi=None, alpha=alpha, excludes_zero=False,
                            n_from=draws["n_from"], n_to=draws["n_to"])
    point = draws["point"]
    lo, hi = percentile_interval(draws["diffs"], 1.0 - alpha)
    return measurable({"point": rounded(point), "lo": rounded(lo), "hi": rounded(hi), "alpha": alpha,
                       "excludes_zero": bool(lo > 0 or hi < 0), "n_from": draws["n_from"], "n_to": draws["n_to"],
                       "from": rounded(draws["from"]), "to": rounded(draws["to"])})


def delta(spec: dict, parent_eps: list, child_eps: list, samples: int = BOOTSTRAP_SAMPLES,
          alpha: float = ALPHA) -> dict:
    """The child's value minus the parent's with a percentile-bootstrap
    interval at level ``1 − alpha`` (both sides resampled within their
    tasks from one stream seeded ``delta:<spec key>``); ``excludes_zero``
    is the informative test. ``measurable: False`` with the side and the
    reason when either side cannot be read, or with ``samples`` under 1."""
    return _delta_from_draws(_delta_draws(spec, parent_eps, child_eps, samples), alpha)


# ---------------------------------------------------------------- the cache and the step view

class _Cache:
    """The feature table plus every value and delta computed so far, keyed
    by the spec's key (feature, aggregation and filter — the id plays no
    part, since the stream is seeded by the key), so a metric read at
    three steps is bootstrapped once per generation and once per step and
    two specs that read the same thing share one interval."""

    def __init__(self, table: list, samples: int) -> None:
        self.table = table
        self.samples = samples
        self.by_gen = {row["id"]: row["episodes"] for row in table}
        self.order = [row["id"] for row in table]
        self.values: dict = {}
        self.draws: dict = {}

    @staticmethod
    def _key(spec: dict) -> str:
        return _spec_key(spec)

    def episodes(self, upto: str) -> list:
        """Every episode of the generations up to ``upto`` inclusive."""
        out: list = []
        for gid in self.order:
            out.extend(self.by_gen[gid])
            if gid == upto:
                break
        return out

    def value(self, spec: dict, gen: str) -> dict:
        key = (self._key(spec), gen)
        if key not in self.values:
            self.values[key] = value(spec, self.by_gen.get(gen) or [], self.samples)
        return self.values[key]

    def delta(self, spec: dict, frm: str, to: str, alpha: float = ALPHA) -> dict:
        key = (self._key(spec), frm, to)
        if key not in self.draws:
            self.draws[key] = _delta_draws(spec, self.by_gen.get(frm) or [], self.by_gen.get(to) or [], self.samples)
        return _delta_from_draws(self.draws[key], alpha)


@dataclass
class StepView:
    """What a probe sees at one agent step: the evolution step (verdict,
    flags, effect, gaming), the parent and child feature rows, every
    episode so far, the eval as it stands (``metrics``), the tools and
    claims seen so far, the external candidates addressed to this step,
    and the cache the values and deltas come from. Nothing after this
    step is reachable from it."""
    index: int
    frm: str
    to: str
    step: dict
    parent: list
    child: list
    so_far: list
    gens_so_far: list
    metrics: dict
    tools_child: set
    tools_earlier: list
    claims_child: bool
    claims_earlier: bool
    candidates: list
    cache: _Cache
    vocabulary: list
    protected: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.frm}→{self.to}"

    def value(self, spec: dict, gen: str) -> dict:
        return self.cache.value(spec, gen)

    def delta(self, spec: dict, alpha: float = ALPHA, frm: Optional[str] = None, to: Optional[str] = None) -> dict:
        return self.cache.delta(spec, frm or self.frm, to or self.to, alpha)

    def active(self) -> list:
        """The metrics the eval reads with: base and adopted, in eval order."""
        return [m for m in self.metrics.values() if m["status"] in ("base", "adopted")]


def _mean_sd(values: list) -> tuple:
    n = len(values)
    if n == 0:
        return None, None
    m = sum(values) / n
    var = pvar(values) * n / (n - 1) if n > 1 else 0.0
    return m, math.sqrt(var)


def feature_shifts(view: StepView) -> list:
    """Per feature of the vocabulary in force: the parent and child means,
    the shift, its sign against the outcome shift and the standardised
    shift |Δ| / pooled within-side sd (Cohen's d); ``separates`` when
    both sides are constant and differ, which no sd can scale. Sorted by
    the standardised shift, largest first, ``separates`` ahead of every
    number, ties by feature id."""
    outcome = (view.step.get("effect") or {}).get("pass_rate") or {}
    o_shift = outcome.get("delta")
    rows = []
    for f in view.vocabulary:
        a = [float(e["values"][f]) for e in view.parent if e["values"].get(f) is not None]
        b = [float(e["values"][f]) for e in view.child if e["values"].get(f) is not None]
        if len(a) < 2 or len(b) < 2:
            rows.append({"feature": f, "from": None, "to": None, "shift": None, "standardised": None,
                         "separates": False, "sign_vs_outcome": None, "reason": "fewer than two readable episodes on a side"})
            continue
        ma, sa = _mean_sd(a)
        mb, sb = _mean_sd(b)
        shift = mb - ma
        pooled = math.sqrt(((len(a) - 1) * sa * sa + (len(b) - 1) * sb * sb) / (len(a) + len(b) - 2))
        separates = pooled == 0 and shift != 0
        standardised = None if separates else (abs(shift) / pooled if pooled > 0 else 0.0)
        if o_shift is None or o_shift == 0 or shift == 0:
            sign = "neutral"
        else:
            sign = "up" if (shift > 0) == (o_shift > 0) else "down"
        rows.append({"feature": f, "from": rounded(ma), "to": rounded(mb), "shift": rounded(shift),
                     "standardised": rounded(standardised), "separates": separates, "sign_vs_outcome": sign,
                     "reason": None})
    rows.sort(key=lambda r: (0 if r["separates"] else 1, -(r["standardised"] or 0.0), r["feature"]))
    return rows


# ---------------------------------------------------------------- probes

def _adopted_features(view: StepView) -> set:
    return {m["spec"]["feature"] for m in view.active()}


def _origin(probe: str, view: StepView, eval_gen: str) -> dict:
    return {"probe": probe, "step": view.label, "eval_gen": eval_gen}


def _axes_trigger(view: StepView) -> bool:
    step = view.step
    return bool((step.get("effect") or {}).get("axes_disagree")) or bool((step.get("gaming") or {}).get("sign_only")) \
        or step.get("verdict") == "gamed"


def _axes_propose(view: StepView) -> list:
    taken = _adopted_features(view)
    out = []
    for row in feature_shifts(view):
        if row["feature"] in taken or row["reason"] or row["shift"] == 0:
            continue
        f = row["feature"]
        agg = "rate" if (FEATURES[f].kind if f in FEATURES else "count") == "bool" else "mean"
        sid = f"{f.replace(':', '_')}_{agg}"
        out.append({"id": sid, "name": _metric_name(f, agg), "feature": f, "agg": agg, "where": None,
                    "direction": row["sign_vs_outcome"], "rank": {"standardised": row["standardised"],
                                                                    "separates": row["separates"], "shift": row["shift"]}})
        if len(out) >= PROBE_TOP:
            break
    return out


def _saturated(view: StepView, m: dict) -> Optional[str]:
    """Why an adopted rate metric can no longer move on the child, or None."""
    spec = m["spec"]
    if spec["agg"] != "rate":
        return None
    v = view.value(spec, view.to)
    if not v["measurable"]:
        return None
    if v["point"] in (0.0, 1.0):
        return f"{spec['id']} is {num(v['point'])} on {view.to}"
    n, by_task, _ = _select(spec, view.child)
    per = [mean(vs) for t, vs in sorted(by_task.items()) if vs]
    if per and all(p in (0.0, 1.0) for p in per):
        return f"{spec['id']} is 0 or 1 on every task of {view.to}"
    if v["hi"] == v["lo"]:
        return f"{spec['id']}'s interval on {view.to} has no width"
    return None


def _ceiling_trigger(view: StepView) -> bool:
    return any(_saturated(view, m) for m in view.active())


def _ceiling_propose(view: StepView) -> list:
    calls = [e["values"]["tool_calls"] for e in view.parent if e["values"].get("tool_calls") is not None]
    med = median(calls)
    out = [
        {"id": "verified_pass_rate", "name": "pass rate among verified episodes", "feature": "success", "agg": "rate",
         "where": {"feature": "verified", "op": "==", "value": 1}, "direction": "up"},
        {"id": "clean_pass_rate", "name": "pass rate among episodes with no tool error", "feature": "success",
         "agg": "rate", "where": {"feature": "tool_errors", "op": "==", "value": 0}, "direction": "up"},
    ]
    if med is not None:
        out.append({"id": "frugal_pass_rate", "name": f"pass rate among episodes at or under {num(med)} tool calls",
                    "feature": "success", "agg": "rate",
                    "where": {"feature": "tool_calls", "op": "<=", "value": med}, "direction": "up"})
    because = [s for s in (_saturated(view, m) for m in view.active()) if s]
    for spec in out:
        spec["rank"] = {"because": because}
    return out


def _novelty_changes(view: StepView) -> dict:
    earlier_union = set().union(*view.tools_earlier) if view.tools_earlier else set()
    earlier_all = set.intersection(*view.tools_earlier) if view.tools_earlier else set()
    return {"appeared": sorted(view.tools_child - earlier_union),
            "vanished": sorted(earlier_all - view.tools_child) if view.tools_earlier else [],
            "claims": bool(view.claims_child and not view.claims_earlier)}


def _novelty_trigger(view: StepView) -> bool:
    ch = _novelty_changes(view)
    return bool(ch["appeared"] or ch["vanished"] or ch["claims"])


def _novelty_propose(view: StepView) -> list:
    ch = _novelty_changes(view)
    out = []
    for tool in ch["appeared"] + ch["vanished"]:
        out.append({"id": f"uses_{tool}_rate", "name": f"share of episodes calling {tool}", "feature": f"uses:{tool}",
                    "agg": "rate", "where": None, "direction": "neutral",
                    "rank": {"because": ("appeared at " if tool in ch["appeared"] else "vanished at ") + view.to}})
    if ch["claims"]:
        out.append({"id": "claims_rate", "name": "claim rate", "feature": "claims", "agg": "rate", "where": None,
                    "direction": "down", "rank": {"because": f"claims appear at {view.to} where none were"}})
    return out


def _forgetting_trigger(view: StepView) -> bool:
    return bool((view.step.get("effect") or {}).get("forgotten"))


def _forgetting_propose(view: StepView) -> list:
    lost = list((view.step.get("effect") or {}).get("forgotten") or [])
    return [{"id": "worst_task_pass", "name": "worst task pass rate", "feature": "success", "agg": "task_min",
             "where": None, "direction": "up", "rank": {"because": "forgot " + ", ".join(lost)}},
            {"id": "pass_task_spread", "name": "pass rate spread across tasks", "feature": "success",
             "agg": "task_spread", "where": None, "direction": "down", "rank": {"because": "forgot " + ", ".join(lost)}}]


def _moved_in_direction(d: dict, direction: str) -> bool:
    if not d.get("measurable") or direction == "neutral":
        return False
    return d["lo"] > 0 if direction == "up" else d["hi"] < 0


def _goodhart_hits(view: StepView) -> list:
    """The adopted, non-base, non-outcome metrics that improved in their
    direction on this step and the previous one while the pass rate's
    delta included zero or fell on both."""
    if view.index < 2 or len(view.gens_so_far) < 3:
        return []
    prev_from, prev_to = view.gens_so_far[-3], view.gens_so_far[-2]
    pass_spec = view.metrics["pass_rate"]["spec"]
    p_now, p_prev = view.delta(pass_spec), view.delta(pass_spec, frm=prev_from, to=prev_to)
    if not p_now["measurable"] or not p_prev["measurable"]:
        return []
    if p_now["lo"] > 0 or p_prev["lo"] > 0:
        return []
    hits = []
    for m in view.active():
        spec = m["spec"]
        if m["status"] != "adopted" or _outcome_spec(spec) or spec["direction"] == "neutral":
            continue
        if _moved_in_direction(view.delta(spec), spec["direction"]) and \
                _moved_in_direction(view.delta(spec, frm=prev_from, to=prev_to), spec["direction"]):
            hits.append(m)
    return hits


def _goodhart_trigger(view: StepView) -> bool:
    return bool(_goodhart_hits(view))


def _goodhart_propose(view: StepView) -> list:
    out = []
    for m in _goodhart_hits(view):
        spec = m["spec"]
        cond = {"feature": "success", "op": "==", "value": 1}
        where = {"all": _predicates(spec.get("where")) + [cond]} if spec.get("where") is not None else cond
        out.append({"id": f"{spec['id']}_on_pass", "name": f"{spec['name']} among the passes", "feature": spec["feature"],
                    "agg": spec["agg"], "where": where, "direction": spec["direction"],
                    "rank": {"because": f"{spec['id']} moved {spec['direction']} on {view.label} and the step before "
                                        f"while the pass rate did not rise", "demotes": spec["id"]}})
    return out


#: what a passing episode costs the agent, in the metric language: the
#: probe below proposes these, and the language could already say them —
#: what was missing was anything that thought to ask.
_WORK_ON_PASS = ({"feature": "steps", "id": "work_per_pass", "name": "steps per pass"},
                 {"feature": "tool_calls", "id": "tool_calls_per_pass", "name": "tool calls per pass"})
#: the rise in work per pass, as a share of the lower side, that makes the
#: probe fire; below it the movement is inside these corpora's noise
_ABSORB_MARGIN = 0.10


def _pass_work(view: "StepView", feature: str, gen: str):
    """The mean of ``feature`` over the passing episodes of one
    generation, or None when the value cannot be read there."""
    spec = {"id": f"_{feature}_on_pass", "name": feature, "feature": feature, "agg": "mean",
            "where": {"feature": "success", "op": "==", "value": 1}, "direction": "down"}
    v = view.value(spec, gen)
    return v["point"] if v.get("measurable") else None


def _absorption_hit(view: "StepView") -> Optional[dict]:
    """The step where the outcome improved and each success cost more.

    An agent can raise its pass rate without getting better at the task,
    by having more put around it: a verifier restored, a retry budget
    widened, a tool that does the job. The give-away is that the *work per
    pass* rises at the same time — the agent is not needing less, it is
    being carried further. Every existing probe reads the outcome or a
    metric the agent moved; none of them reads what a success costs, so
    none of them can see this.
    """
    rate = {"id": "_pass", "name": "pass", "feature": "success", "agg": "rate", "where": None, "direction": "up"}
    frm, to = view.value(rate, view.frm), view.value(rate, view.to)
    if not (frm.get("measurable") and to.get("measurable")) or to["point"] <= frm["point"]:
        return None
    before, after = _pass_work(view, "steps", view.frm), _pass_work(view, "steps", view.to)
    if before in (None, 0) or after is None or (after - before) / before <= _ABSORB_MARGIN:
        return None
    return {"rate_from": frm["point"], "rate_to": to["point"], "work_from": before, "work_to": after}


def _absorption_trigger(view: "StepView") -> bool:
    return _absorption_hit(view) is not None


def _absorption_propose(view: "StepView") -> list:
    hit = _absorption_hit(view)
    if not hit:
        return []
    because = (f"the pass rate rose {num(hit['rate_from'], 2)} → {num(hit['rate_to'], 2)} on {view.label} while a "
               f"passing episode cost {num(hit['work_from'], 2)} → {num(hit['work_to'], 2)} steps, so the gain may "
               f"be what is around the agent rather than the agent")
    return [{"id": row["id"], "name": row["name"], "feature": row["feature"], "agg": "mean",
             "where": {"feature": "success", "op": "==", "value": 1}, "direction": "down",
             "rank": {"because": because}} for row in _WORK_ON_PASS]


def _external_trigger(view: StepView) -> bool:
    return bool(view.candidates)


def _external_propose(view: StepView) -> list:
    return list(view.candidates)


@dataclass(frozen=True)
class Probe:
    """One agent of the eval: a name, the question it asks, ``trigger(view)``
    and ``propose(view) -> [spec]``; ``after`` marks the probes that act
    on the eval after adoption (redundancy) rather than proposing."""
    name: str
    question: str
    trigger: Callable
    propose: Callable
    after: bool = False


PROBES: tuple = (
    Probe("axes", "when return and outcome disagree, what explains it?", _axes_trigger, _axes_propose),
    Probe("ceiling", "a metric that can no longer move is not measuring", _ceiling_trigger, _ceiling_propose),
    Probe("novelty", "a behaviour no ancestor showed needs a watcher", _novelty_trigger, _novelty_propose),
    Probe("forgetting", "an average hides a task", _forgetting_trigger, _forgetting_propose),
    Probe("goodhart", "a metric the agent moved without the outcome moving is a metric the agent learned",
          _goodhart_trigger, _goodhart_propose),
    Probe("absorption", "a pass rate that rose while each pass cost more is a gain from the scaffold, not the agent",
          _absorption_trigger, _absorption_propose),
    Probe("redundancy", "two metrics that always agree are one metric", lambda view: True, lambda view: [], after=True),
    Probe("external", "candidates from outside, validated and never trusted", _external_trigger, _external_propose),
)
#: the probes whose candidates are exempt from the linked validator
_LINK_EXEMPT = ("novelty", "ceiling")


# ---------------------------------------------------------------- correlation bases

def _episode_series(spec: dict, episodes: list) -> dict:
    """``{position: value}`` on the unfiltered episode basis — keyed by
    the episode's position in the list, not its trace id, since a
    lineage may reuse ids across generations."""
    out = {}
    for i, e in enumerate(episodes):
        v = e["values"].get(spec["feature"])
        if v is not None:
            out[i] = _as_value(v, spec["agg"])
    return out


def _generation_series(view: StepView, spec: dict) -> dict:
    """``{generation id: point}`` over the generations so far, the
    unmeasurable ones left out."""
    out = {}
    for gid in view.gens_so_far:
        v = view.value(spec, gid)
        if v["measurable"]:
            out[gid] = v["point"]
    return out


def _rho_between(view: StepView, a: dict, b: dict) -> dict:
    """|Spearman ρ| between two metrics on their shared basis: per episode
    when both are unfiltered episode-level, else the generation series
    once :data:`MIN_SERIES` generations are measurable on both, else
    ``testable: False`` with the note."""
    if _episode_level(a) and _episode_level(b):
        sa, sb = _episode_series(a, view.so_far), _episode_series(b, view.so_far)
        ids = sorted(set(sa) & set(sb))
        rho = spearman([sa[i] for i in ids], [sb[i] for i in ids]) if len(ids) >= 2 else None
        return {"testable": rho is not None, "rho": rho, "basis": "episodes", "n": len(ids),
                "note": None if rho is not None else "a side is constant over the shared episodes"}
    sa, sb = _generation_series(view, a), _generation_series(view, b)
    ids = [g for g in view.gens_so_far if g in sa and g in sb]
    if len(ids) < MIN_SERIES:
        return {"testable": False, "rho": None, "basis": "generations", "n": len(ids),
                "note": f"not testable yet: {len(ids)} generations measurable on both, {MIN_SERIES} needed"}
    rho = spearman([sa[g] for g in ids], [sb[g] for g in ids])
    return {"testable": rho is not None, "rho": rho, "basis": "generations", "n": len(ids),
            "note": None if rho is not None else "a side is constant over the generations"}


# ---------------------------------------------------------------- validators

def _v_computable(spec: dict, probe: str, view: StepView, alpha: float) -> dict:
    total = len(view.so_far)
    readable = sum(1 for e in view.so_far if e["values"].get(spec["feature"]) is not None)
    coverage = readable / total if total else 0.0
    a, b = view.value(spec, view.frm), view.value(spec, view.to)
    ok = coverage >= MIN_COVERAGE - 1e-9 and a["measurable"] and b["measurable"]
    if coverage < MIN_COVERAGE - 1e-9:
        note = f"{spec['feature']} is readable on {readable} of {total} episodes so far ({num(coverage)} under {num(MIN_COVERAGE)})"
    elif not a["measurable"]:
        note = f"unmeasurable on {view.frm}: {a['reason']}"
    elif not b["measurable"]:
        note = f"unmeasurable on {view.to}: {b['reason']}"
    else:
        note = f"readable on {readable} of {total} episodes so far; measurable on both sides"
    return {"pass": ok, "note": note, "coverage": rounded(coverage), "from": {k: a.get(k) for k in ("point", "lo", "hi", "n")},
            "to": {k: b.get(k) for k in ("point", "lo", "hi", "n")}}


def _v_informative(spec: dict, probe: str, view: StepView, alpha: float) -> dict:
    if probe == "ceiling":
        b = view.value(spec, view.to)
        ok = bool(b["measurable"] and 0.0 < b["point"] < 1.0 and b["hi"] > b["lo"])
        note = (f"the child's point {num(b['point'])} [{num(b['lo'])}, {num(b['hi'])}] "
                + ("sits strictly inside (0, 1) with width" if ok else
                   "is at a bound or has no width" if b["measurable"] else f"cannot be read: {b['reason']}"))
        return {"pass": ok, "note": note, "rule": "ceiling: strictly inside (0, 1) with width",
                "delta": None, "alpha": alpha}
    d = view.delta(spec, alpha)
    ok = bool(d["measurable"] and d["excludes_zero"])
    if not d["measurable"]:
        note = f"no delta: {d['reason']}"
    else:
        note = f"delta {signed(d['point'])} [{num(d['lo'])}, {num(d['hi'])}] at level {num(1 - alpha, 4)} "
        if ok:
            note += "excludes zero"
        else:
            runs = [len(v) for e in (view.parent, view.child) for v in _select(spec, e)[1].values()]
            note += (f"includes zero: at {plural(min(runs), 'run')} per task {spec['name']} cannot be told from noise "
                     f"at the adjusted level" if runs else "includes zero")
    return {"pass": ok, "note": note, "rule": f"the step's delta interval at 1 − {num(alpha, 4)} excludes zero",
            "delta": {k: d.get(k) for k in ("point", "lo", "hi", "excludes_zero")}, "alpha": alpha}


def _v_distinct(spec: dict, probe: str, view: StepView, alpha: float) -> dict:
    against = []
    worst = None
    for m in view.active():
        r = _rho_between(view, spec, m["spec"])
        against.append({"metric": m["spec"]["id"], **r})
        if r["testable"] and (worst is None or abs(r["rho"]) > abs(worst["rho"])):
            worst = {"metric": m["spec"]["id"], "rho": r["rho"]}
    if worst is None:
        untested = [a for a in against if not a["testable"]]
        return {"pass": True, "note": "not testable yet" if untested else "no adopted metric to compare with",
                "max_rho": None, "against": against}
    ok = abs(worst["rho"]) < REDUNDANT_RHO
    return {"pass": ok, "note": f"max |ρ| {num(abs(worst['rho']))} against {worst['metric']}"
                                + (" under" if ok else ", at or over") + f" {num(REDUNDANT_RHO)}",
            "max_rho": worst["rho"], "against": against}


def _outcome_rho(spec: dict, episodes: list) -> tuple:
    """``(rho, n)``: Spearman's ρ between the feature and success over the
    episodes that read both; None when a side is constant."""
    xs, ys = [], []
    for e in episodes:
        v = e["values"].get(spec["feature"])
        s = e["values"].get("success")
        if v is not None and s is not None:
            xs.append(float(v))
            ys.append(float(s))
    return (spearman(xs, ys) if len(xs) >= 2 else None), len(xs)


def _v_linked(spec: dict, probe: str, view: StepView, alpha: float) -> dict:
    rho, n = _outcome_rho(spec, view.so_far)
    if probe in _LINK_EXEMPT:
        return {"pass": True, "note": f"exempt: the {probe} probe watches "
                                      + ("behaviour" if probe == "novelty" else "strictness") + ", not outcome"
                                      + (f"; |ρ({spec['feature']}, success)| {num(abs(rho))} recorded" if rho is not None else ""),
                "rho": rho, "exempt": True}
    if _conditioned_on_outcome(spec):
        return {"pass": True, "note": "exempt: conditioned on the outcome, so the link is by construction",
                "rho": rho, "exempt": True}
    if rho is None:
        return {"pass": False, "note": f"ρ({spec['feature']}, success) cannot be read: a side is constant over "
                                       f"{plural(n, 'episode')} so far", "rho": None, "exempt": False}
    ok = abs(rho) >= LINK_RHO
    return {"pass": ok, "note": f"|ρ({spec['feature']}, success)| {num(abs(rho))} over {plural(n, 'episode')} so far"
                                + (" reaches" if ok else " under") + f" {num(LINK_RHO)}", "rho": rho, "exempt": False}


def _v_not_already(spec: dict, probe: str, view: StepView, alpha: float) -> dict:
    key = _spec_key(spec)
    for m in view.metrics.values():
        if _spec_key(m["spec"]) == key:
            return {"pass": False, "note": f"the same feature, aggregation and filter is {m['status']} as {m['spec']['id']}",
                    "same_as": m["spec"]["id"]}
    return {"pass": True, "note": "no adopted, demoted or retired metric has this feature, aggregation and filter",
            "same_as": None}


#: the sub-agents, in the order that decides
VALIDATORS: tuple = (("computable", _v_computable), ("informative", _v_informative), ("distinct", _v_distinct),
                     ("linked", _v_linked), ("not_already", _v_not_already))


_KIND_RANK = {"bool": 0, "count": 1, "ratio": 2, "sum": 3, "estimate": 4}
_RULES = {"a": "the strongest |ρ| with the outcome", "b": "the more interpretable kind (a bool rate over a count mean over a ratio)",
          "c": "a feature tied to a protected path", "d": "vocabulary order", "e": "spec id"}


def _protected_feature(feature: str, protected: list) -> bool:
    if feature in ("check_calls", "verified"):
        return True
    return feature.startswith("uses:") and f"tools.{feature[5:]}" in (protected or [])


def _representative_key(spec: dict, rho: Optional[float], view: StepView) -> tuple:
    """The order that picks one representative per redundancy class:
    (a) the strongest |ρ| with the outcome, (b) the more interpretable
    kind, (c) a feature tied to a protected path, (d) vocabulary order,
    (e) the spec id — so two candidates on one feature (two ceiling
    conditions on ``success``, an external duplicate) are decided by a
    stated key and never by proposal order."""
    f = spec["feature"]
    kind = FEATURES[f].kind if f in FEATURES else "count"
    return (-(abs(rho) if rho is not None else -1.0), _KIND_RANK.get(kind, 5), 0 if _protected_feature(f, view.protected) else 1,
            view.vocabulary.index(f) if f in view.vocabulary else len(view.vocabulary), spec["id"])


def _validate_batch(proposals: list, view: StepView, alpha: float) -> list:
    """Every candidate of a step through every validator, as a batch. The
    four evidence validators run on each; the survivors are grouped into
    redundancy classes (|ρ| ≥ :data:`REDUNDANT_RHO` with each other,
    transitively, on the basis :func:`_rho_between` states) and one
    representative per class is chosen by :func:`_representative_key`
    — so which metric the eval learns is decided by a stated rule and
    not by proposal order; the others fail ``distinct`` with a note
    naming the representative and the rule. Representatives are then
    tested for ``distinct`` against the metrics already adopted. Returns
    one ``{validators, failed, decision, reason}`` per proposal, in
    order."""
    pre = [{name: fn(spec, probe, view, alpha) for name, fn in VALIDATORS if name != "distinct"}
           for probe, spec, _ in proposals]
    survivors = [i for i, rows in enumerate(pre) if all(rows[n]["pass"] for n in rows)]
    root = {i: i for i in survivors}

    def find(i: int) -> int:
        while root[i] != i:
            i = root[i]
        return i

    pair_rho: dict = {}
    for x, a in enumerate(survivors):
        for b in survivors[x + 1:]:
            r = _rho_between(view, proposals[a][1], proposals[b][1])
            if r["testable"] and abs(r["rho"]) >= REDUNDANT_RHO:
                pair_rho[(a, b)] = r["rho"]
                root[find(b)] = find(a)
    classes: dict = {}
    for i in survivors:
        classes.setdefault(find(i), []).append(i)
    chosen: dict = {}
    for members in classes.values():
        keyed = sorted(members, key=lambda i: _representative_key(proposals[i][1], pre[i]["linked"]["rho"], view))
        rep = keyed[0]
        rule = None
        if len(keyed) > 1:
            ka, kb = (_representative_key(proposals[i][1], pre[i]["linked"]["rho"], view) for i in keyed[:2])
            rule = next((letter for letter, (x, y) in zip("abcde", zip(ka, kb)) if x != y), "e")
        for i in members:
            chosen[i] = (rep, rule, members)
    out = []
    for i, (probe, spec, _) in enumerate(proposals):
        rows = dict(pre[i])
        distinct = _v_distinct(spec, probe, view, alpha)
        if i in chosen:
            rep, rule, members = chosen[i]
            rep_id = proposals[rep][1]["id"]
            distinct["class"] = [proposals[j][1]["id"] for j in members]
            distinct["representative"] = rep_id
            distinct["rule"] = None if rule is None else f"({rule}) {_RULES[rule]}"
            if rep != i:
                rho = pair_rho.get((min(i, rep), max(i, rep)))
                distinct = dict(distinct, **{"pass": False,
                                             "note": f"one reading with {rep_id}"
                                                     + (f" (|ρ| {num(abs(rho))})" if rho is not None else " (through the class)")
                                                     + f": {rep_id} chosen by {distinct['rule']}"})
            elif len(members) > 1 and distinct["pass"]:
                distinct["note"] += f"; the representative of {plural(len(members), 'candidate')} that are one reading"
        ordered = {name: (distinct if name == "distinct" else rows[name]) for name, _ in VALIDATORS}
        failed = [name for name, _ in VALIDATORS if not ordered[name]["pass"]]
        out.append({"validators": ordered, "failed": failed, "decision": "rejected" if failed else "adopted",
                    "reason": (f"{failed[0]}: {ordered[failed[0]]['note']}" if failed else
                               "every validator passed: " + ordered["informative"]["note"])})
    return out


# ---------------------------------------------------------------- the walk

def _metric(spec: dict, status: str, origin: dict, validation: Optional[dict], adopted_at: Optional[dict],
            alpha: Optional[float] = None) -> dict:
    """A metric of the eval; ``alpha`` is the level it was adopted at
    (``ALPHA / K`` of its step), written rounded into ``confirmation`` —
    the level every later out-of-sample test and the hindsight use."""
    return {"spec": spec, "status": status, "origin": origin, "validation": validation,
            "confirmation": ({"tested": 0, "moved": 0, "status": "pending", "alpha": rounded(alpha)}
                             if status != "base" else None),
            "caught_at": None, "adopted_at": adopted_at, "demoted_at": None, "retired_at": None}


def _synthetic(lineage: dict) -> bool:
    if "SYNTHETIC" in str(lineage.get("note") or ""):
        return True
    for g in lineage.get("generations") or []:
        agent = g.get("agent") or {}
        if isinstance(agent, dict) and "SYNTHETIC" in str(agent.get("note") or ""):
            return True
        for traj in g.get("trajectories") or []:
            harness = getattr(traj, "harness", None)
            if isinstance(harness, dict) and "SYNTHETIC" in str(harness.get("note") or ""):
                return True
    return False


def _candidates_for(candidates, label: str) -> list:
    """The external candidates addressed to a step (``at`` the step's
    label, or null for every step), each carrying its ``entry`` index so
    an entry tested at several steps is counted once."""
    out = []
    for i, c in enumerate(candidates or []):
        if not isinstance(c, dict):
            out.append({"at": label, "rejected": f"not an object: {json.dumps(c)[:80]}", "source": None, "entry": i})
            continue
        at = c.get("at")
        if at is None or at == label:
            out.append(dict(c, entry=i))
    return out


def _empty_probes() -> dict:
    return {p.name: {"name": p.name, "question": p.question, "fired": [], "proposed": 0, "adopted": 0}
            for p in PROBES}


def _walk(lineage: dict, evolution: dict, table: list, samples: int, candidates, protected: Optional[list] = None) -> dict:
    """The probes and validators step by step; ``protected`` overrides the
    lineage's protected paths for the step views (rule (c)), the same
    list the feature table was built with."""
    cache = _Cache(table, samples)
    protected = list(lineage.get("protected") or []) if protected is None else list(protected)
    alphas: dict = {}          # metric id -> the exact level it was adopted at
    addressed: set = set()     # the external entries some walked step took up
    gens = [row["id"] for row in table]
    ev_steps = {s["index"]: s for s in evolution.get("steps") or []}
    metrics: dict = {}
    for spec in BASE_SPECS:
        metrics[spec["id"]] = _metric(dict(spec, origin={"probe": "base"}), "base", {"probe": "base"}, None, None)
    eval_gens = [{"id": "e0", "index": 0, "after_step": None, "trigger_probe": None, "adopted": [s["id"] for s in BASE_SPECS],
                  "demoted": [], "retired": [], "size": len(BASE_SPECS)}]
    ledger: list = []
    probes = _empty_probes()
    walked: list = []
    external = {"received": len([c for c in (candidates or []) if isinstance(c, dict)]), "parsed": 0, "adopted": 0,
                "rejected": 0, "sources": []}
    parsed_entries: set = set()
    adoption_order = {s["id"]: i for i, s in enumerate(BASE_SPECS)}
    tools_by_gen = [{k[5:] for e in row["episodes"] for k, v in e["values"].items() if k.startswith("uses:") and v}
                    for row in table]
    claims_by_gen = [any(e["values"].get("claims") for e in row["episodes"]) for row in table]
    for i in range(1, len(gens)):
        frm, to = gens[i - 1], gens[i]
        step = ev_steps.get(i) or {"from": frm, "to": to, "index": i, "verdict": None, "flags": [], "effect": {},
                                   "gaming": {}}
        label = f"{frm}→{to}"
        parent, child = cache.by_gen[frm], cache.by_gen[to]
        if not parent or not child or not (step.get("effect") or {}).get("measurable"):
            walked.append({"index": i, "from": frm, "to": to, "walked": False,
                           "reason": (step.get("effect") or {}).get("reason") or
                           (f"{frm} has no episode" if not parent else f"{to} has no episode")})
            continue
        view = StepView(index=i, frm=frm, to=to, step=step, parent=parent, child=child,
                        so_far=cache.episodes(to), gens_so_far=gens[:i + 1], metrics=metrics,
                        tools_child=tools_by_gen[i], tools_earlier=tools_by_gen[:i],
                        claims_child=claims_by_gen[i], claims_earlier=any(claims_by_gen[:i]),
                        candidates=_candidates_for(candidates, label), cache=cache, vocabulary=_vocabulary(table, i),
                        protected=protected)
        addressed.update(c["entry"] for c in view.candidates)
        eval_gen = eval_gens[-1]["id"]
        items: list = []      # ("proposal", probe, spec, rank) | ("unparseable", raw, reason, origin), in proposal order
        demote: list = []
        for probe in PROBES:
            if probe.after or not probe.trigger(view):
                continue
            probes[probe.name]["fired"].append(label)
            for raw in probe.propose(view):
                if probe.name == "external":
                    source = raw.get("source")
                    if source and source not in external["sources"]:
                        external["sources"].append(source)
                    origin = {"probe": "external", "source": source, "step": label, "eval_gen": eval_gen}
                    if "rejected" in raw:
                        items.append(("unparseable", raw, f"unparseable: {raw['rejected']}", origin))
                        continue
                    try:
                        spec = parse_spec(raw.get("spec"), view.vocabulary)
                    except ValueError as exc:
                        items.append(("unparseable", raw, f"unparseable: {exc}", origin))
                        continue
                    parsed_entries.add(raw.get("entry"))
                    spec["origin"] = origin
                    items.append(("proposal", probe.name, spec, {}))
                else:
                    rank = raw.pop("rank", {})
                    spec = parse_spec(raw, view.vocabulary)
                    spec["origin"] = _origin(probe.name, view, eval_gen)
                    if rank.get("demotes"):
                        demote.append((rank["demotes"], rank["because"]))
                    items.append(("proposal", probe.name, spec, rank))
        proposals = [it[1:] for it in items if it[0] == "proposal"]
        # K counts the tests: a candidate whose spec is already adopted, demoted or retired fails
        # not_already and is a ledger row, not a test, so it does not tighten the level for the others
        k = sum(1 for _, spec, _ in proposals if _v_not_already(spec, "", view, ALPHA)["pass"])
        alpha = ALPHA / k if k else ALPHA
        adopted_now, demoted_now, retired_now, changed_by = [], [], [], []
        for mid, because in demote:
            m = metrics.get(mid)
            if m and m["status"] == "adopted":
                m["status"] = "demoted"
                m["demoted_at"] = {"step": label, "index": i, "reason": because}
                demoted_now.append(mid)
                if "goodhart" not in changed_by:
                    changed_by.append("goodhart")
        results = iter(_validate_batch(proposals, view, alpha))
        for item in items:
            if item[0] == "unparseable":
                _, raw, reason, origin = item
                ledger.append({"index": len(ledger), "step": label, "eval_gen": eval_gen, "probe": "external",
                               "spec_id": (raw.get("spec") or {}).get("id") if isinstance(raw.get("spec"), dict) else None,
                               "spec": raw.get("spec"), "origin": origin,
                               "validators": {name: {"pass": None, "note": "not tested: the spec did not parse"}
                                              for name, _ in VALIDATORS},
                               "k": k, "alpha": rounded(alpha), "decision": "rejected", "failed": [], "reason": reason})
                external["rejected"] += 1
                continue
            _, probe_name, spec, rank = item
            result = next(results)
            row = {"index": len(ledger), "step": label, "eval_gen": eval_gen, "probe": probe_name, "spec_id": spec["id"],
                   "spec": spec, "origin": spec["origin"], "rank": rank or None, "validators": result["validators"],
                   "k": k, "alpha": rounded(alpha), "decision": result["decision"], "failed": result["failed"],
                   "reason": result["reason"]}
            ledger.append(row)
            if result["decision"] == "adopted":
                sid = spec["id"]
                if sid in metrics:   # the id is taken by another definition: keep both readable
                    sid = f"{sid}@{label}"
                    spec = dict(spec, id=sid)
                    row["spec_id"] = sid
                metrics[sid] = _metric(spec, "adopted", spec["origin"], row,
                                       {"step": label, "index": i, "eval_gen": None, "ledger": row["index"]}, alpha)
                alphas[sid] = alpha
                adoption_order[sid] = len(adoption_order)
                adopted_now.append(sid)
                if probe_name not in changed_by:
                    changed_by.append(probe_name)
                if probe_name == "external":
                    external["adopted"] += 1
            elif probe_name == "external":
                external["rejected"] += 1
        # redundancy: after adoption, every pair of active metrics
        active = [m for m in metrics.values() if m["status"] in ("base", "adopted")]
        for x in range(len(active)):
            for y in range(x + 1, len(active)):
                a, b = active[x], active[y]
                if a["status"] == "retired" or b["status"] == "retired":
                    continue
                if a["status"] == "base" and b["status"] == "base":
                    continue
                r = _rho_between(view, a["spec"], b["spec"])
                if r["testable"] and abs(r["rho"]) >= REDUNDANT_RHO:
                    newer = b if adoption_order[b["spec"]["id"]] > adoption_order[a["spec"]["id"]] else a
                    older = a if newer is b else b
                    if newer["status"] == "base":
                        continue
                    newer["status"] = "retired"
                    newer["retired_at"] = {"step": label, "index": i, "pair": [older["spec"]["id"], newer["spec"]["id"]],
                                           "rho": r["rho"], "basis": r["basis"],
                                           "reason": f"|ρ| {num(abs(r['rho']))} with {older['spec']['id']} over "
                                                     f"{plural(r['n'], r['basis'][:-1])}, at or over {num(REDUNDANT_RHO)}"}
                    retired_now.append(newer["spec"]["id"])
                    if "redundancy" not in changed_by:
                        changed_by.append("redundancy")
        if retired_now:
            probes["redundancy"]["fired"].append(label)
        # confirmation: every metric adopted at an earlier step, tested out of sample at the level it was adopted at
        for mid, m in metrics.items():
            if m["status"] == "base" or not m["adopted_at"] or m["adopted_at"]["index"] >= i:
                continue
            d = view.delta(m["spec"], alphas.get(mid, ALPHA))
            if d["measurable"]:
                m["confirmation"]["tested"] += 1
                if d["excludes_zero"]:
                    m["confirmation"]["moved"] += 1
        if adopted_now or demoted_now or retired_now:
            eid = f"e{len(eval_gens)}"
            eval_gens.append({"id": eid, "index": len(eval_gens), "after_step": label, "trigger_probe": ", ".join(changed_by),
                              "adopted": adopted_now, "demoted": demoted_now, "retired": retired_now,
                              "size": sum(1 for m in metrics.values() if m["status"] in ("base", "adopted"))})
            for sid in adopted_now:
                metrics[sid]["adopted_at"]["eval_gen"] = eid
        walked.append({"index": i, "from": frm, "to": to, "walked": True, "reason": None, "k": k,
                       "alpha": rounded(alpha)})
    # an external entry no walked step took up is a rejected row, never silently lost
    labels = [f"{gens[j - 1]}→{gens[j]}" for j in range(1, len(gens))]
    for j, c in enumerate(candidates or []):
        if not isinstance(c, dict) or j in addressed:
            continue
        at, source = c.get("at"), c.get("source")
        if source and source not in external["sources"]:
            external["sources"].append(source)
        why = ("is addressed to every step, and the eval walked none" if at is None else
               "names a step the eval did not walk" if at in labels else "names no step of the lineage")
        raw_spec = c.get("spec")
        ledger.append({"index": len(ledger), "step": at if isinstance(at, str) else json.dumps(at, ensure_ascii=False),
                       "eval_gen": eval_gens[-1]["id"],
                       "probe": "external", "spec_id": raw_spec.get("id") if isinstance(raw_spec, dict) else None,
                       "spec": raw_spec, "origin": {"probe": "external", "source": source, "step": at, "eval_gen": eval_gens[-1]["id"]},
                       "validators": {name: {"pass": None, "note": "not tested: no step took the candidate up"}
                                      for name, _ in VALIDATORS},
                       "k": 0, "alpha": None, "decision": "rejected", "failed": [],
                       "reason": f"unaddressed: at {json.dumps(at, ensure_ascii=False)} {why}; the steps are {', '.join(labels)}"})
        external["rejected"] += 1
    external["parsed"] = len(parsed_entries)
    for name in probes:
        probes[name]["proposed"] = sum(1 for r in ledger if r["probe"] == name)
        probes[name]["adopted"] = sum(1 for r in ledger if r["probe"] == name and r["decision"] == "adopted")
    for m in metrics.values():
        if m["confirmation"] is not None:
            c = m["confirmation"]
            c["status"] = ("confirmed" if c["moved"] > 0 else "unconfirmed" if c["tested"] >= CONFIRM_STEPS else "pending")
    return {"cache": cache, "gens": gens, "metrics": metrics, "eval_generations": eval_gens, "ledger": ledger,
            "probes": list(probes.values()), "walked": walked, "external": external, "alphas": alphas}


# ---------------------------------------------------------------- hindsight

def _bad_direction(direction: str) -> Optional[str]:
    return {"up": "down", "down": "up"}.get(direction)


def _moved(d: dict) -> Optional[str]:
    if not d.get("measurable") or not d["excludes_zero"]:
        return None
    return "up" if d["lo"] > 0 else "down"


def _annotate_base(cache: _Cache, metrics: dict, all_eps: list) -> None:
    """The base metrics get the linked reading with hindsight, as an
    annotation: base metrics are never judged, but the evolved
    recommendation needs to know which are outcome-linked."""
    for m in metrics.values():
        if m["status"] != "base":
            continue
        spec = m["spec"]
        xs, ys = [], []
        for e in all_eps:
            v, s = e["values"].get(spec["feature"]), e["values"].get("success")
            if v is not None and s is not None:
                xs.append(float(v))
                ys.append(float(s))
        rho = spearman(xs, ys) if len(xs) >= 2 else None
        m["validation"] = {"annotation": True, "decision": "base",
                           "linked": {"pass": rho is not None and abs(rho) >= LINK_RHO, "rho": rho,
                                      "note": "annotated with hindsight over every episode; base metrics are never judged"}}


def _outcome_linked(m: dict) -> bool:
    if _outcome_spec(m["spec"]):
        return True
    linked = ((m.get("validation") or {}).get("validators") or {}).get("linked") or (m.get("validation") or {}).get("linked") or {}
    return bool(linked.get("pass")) and not linked.get("exempt")


def _step_reading(row: dict, metrics: dict) -> str:
    """One sentence per step: the base verdict, then what the learned
    metrics flag (with how many steps later each was adopted), then what
    the base metrics' own intervals say, which the base verdict does not
    read, then what merely moved."""
    base = row["base"]["verdict"]
    head = f"the base eval said {base or 'nothing (the step is unmeasurable)'}"
    ev = row["evolved"]

    def cell(f: dict) -> str:
        d = f["delta"]
        return f"{metrics[f['metric']]['spec']['name']} {num(f['from'])} → {num(f['to'])} [{num(d['lo'])}, {num(d['hi'])}]"

    parts = []
    if ev["flags"]:
        bits = []
        for f in ev["flags"]:
            lag = metrics[f["metric"]]["adopted_at"]["index"] - row["index"]
            tail = ("a metric adopted at this step" if lag == 0 else
                    f"a metric adopted {plural(lag, 'step')} later, which would have flagged this step" if lag > 0 else
                    f"a metric adopted {plural(-lag, 'step')} earlier")
            bits.append(f"{cell(f)} — {tail}")
        parts.append("the evolved eval sees " + "; ".join(bits))
    else:
        parts.append("the evolved eval adds no learned flag")
    if ev["base_flags"]:
        parts.append("the base metrics move against their direction: " + ", ".join(cell(f) for f in ev["base_flags"])
                     + " — base metrics whose intervals the base verdict does not read")
    if ev["moved"] and not ev["flags"]:
        parts.append("moved: " + ", ".join(f"{metrics[x['metric']]['spec']['name']} {x['direction']} ({signed(x['delta']['point'])})"
                                           for x in ev["moved"]))
    return f"{head}; " + "; ".join(parts) + "."


def _recommend(evolution: dict, steps_out: list, metrics: dict) -> dict:
    """The base rule (task-balanced IQM over the base-eligible generations)
    and the evolved rule, which also excludes a generation whose incoming
    step carries an evolved flag on an outcome-linked ``up`` metric."""
    gens = evolution.get("generations") or []
    ev_steps = {s["to"]: s for s in evolution.get("steps") or []}
    flags_by_to = {s["to"]: s["evolved"]["flags"] for s in steps_out}
    taint: dict = {}
    base_excl: dict = {}
    evolved_excl: dict = {}
    key = lambda g: (g.get("iqm_by_task") or {}).get("point")  # noqa: E731
    for g in gens:
        step = ev_steps.get(g["id"])
        if step is not None:
            for c in list((step.get("diff") or {}).get("protected_changes") or []) + list(step.get("protected_episodes") or []):
                if c["direction"] == "restored":
                    taint.pop(c["path"], None)
                else:
                    taint[c["path"]] = step["to"]
        reasons = []
        if step is not None and step.get("verdict") == "gamed":
            reasons.append("its incoming step was gamed")
        if taint:
            reasons.append("it runs with a weakened protected path")
        if key(g) is None:
            reasons.append("it has no measurable IQM")
        if reasons:
            base_excl[g["id"]] = reasons
        extra = [f["metric"] for f in flags_by_to.get(g["id"], [])
                 if f["learned"] and metrics[f["metric"]]["spec"]["direction"] == "up" and _outcome_linked(metrics[f["metric"]])]
        if extra:
            evolved_excl[g["id"]] = reasons + [f"its incoming step is flagged by {join_names(extra)}"]
        elif reasons:
            evolved_excl[g["id"]] = reasons

    def pick(excluded: dict):
        eligible = [g for g in gens if key(g) is not None and g["id"] not in excluded]
        return max(eligible, key=lambda g: (key(g), -g["index"]), default=None)

    base_pick, evolved_pick = pick(base_excl), pick(evolved_excl)
    base_id = (evolution.get("recommended") or {}).get("id")
    if base_pick is not None and base_pick["id"] != base_id:
        base_id = base_pick["id"]   # the rule re-derived here is the one that stands beside the evolved one
    evolved_id = evolved_pick["id"] if evolved_pick else None
    if evolved_id is None:
        why = "no generation is eligible under the evolved eval: " + "; ".join(
            f"{gid} — {', '.join(r)}" for gid, r in evolved_excl.items())
    elif evolved_id == base_id:
        why = (f"both rules pick {evolved_id}"
               + (": no learned flag on an outcome-linked up metric touches a generation the base rule would keep"
                  if not any(gid not in base_excl for gid in evolved_excl) else
                  "; the evolved eval also passes over " + ", ".join(
                      f"{gid} ({r[-1]})" for gid, r in evolved_excl.items() if gid not in base_excl)))
    else:
        why = (f"the evolved eval picks {evolved_id} over the base eval's {base_id}: "
               + "; ".join(f"{gid} — {', '.join(r)}" for gid, r in evolved_excl.items() if gid not in base_excl))
    return {"base": base_id, "evolved": evolved_id, "agree": base_id == evolved_id, "why": why,
            "excluded": {"base": base_excl, "evolved": evolved_excl}}


def _hindsight(walk: dict, evolution: dict) -> dict:
    """The final eval over every generation and step: a learned metric's
    delta is read at the level it was adopted at (``walk["alphas"]``), a
    base metric's at :data:`ALPHA`, since base metrics were never tested
    at an adjusted level."""
    cache, gens, metrics = walk["cache"], walk["gens"], walk["metrics"]
    alphas = walk.get("alphas") or {}
    ev_steps = {s["index"]: s for s in evolution.get("steps") or []}
    matrix: dict = {}
    for mid, m in metrics.items():
        matrix[mid] = {}
        for gid in gens:
            v = cache.value(m["spec"], gid)
            matrix[mid][gid] = {"point": v["point"], "lo": v["lo"], "hi": v["hi"], "n": v["n"],
                                "measurable": v["measurable"], "reason": v["reason"],
                                "per_task": per_task(m["spec"], cache.by_gen.get(gid) or [], cache.samples)}
    steps_out = []
    walked = {w["index"]: w for w in walk["walked"]}
    for i in range(1, len(gens)):
        frm, to = gens[i - 1], gens[i]
        step = ev_steps.get(i) or {}
        base = {"verdict": step.get("verdict"), "flags": list(step.get("flags") or [])}
        flags, base_flags, moved = [], [], []
        if walked.get(i, {}).get("walked"):
            for mid, m in metrics.items():
                d = cache.delta(m["spec"], frm, to, alphas.get(mid, ALPHA))
                direction = _moved(d)
                if direction is None:
                    continue
                entry = {"metric": mid, "delta": {"point": d["point"], "lo": d["lo"], "hi": d["hi"]},
                         "from": d["from"], "to": d["to"], "direction": direction, "status": m["status"],
                         "learned": m["status"] != "base"}
                if m["status"] == "adopted" and direction == _bad_direction(m["spec"]["direction"]):
                    flags.append(entry)
                elif m["status"] == "base" and direction == _bad_direction(m["spec"]["direction"]):
                    base_flags.append(entry)
                else:
                    moved.append(entry)
        row = {"index": i, "from": frm, "to": to, "base": base,
               "evolved": {"flags": flags, "base_flags": base_flags, "moved": moved,
                           "changed": base["verdict"] in ("improved", "flat") and bool(flags), "reading": ""}}
        if not walked.get(i, {}).get("walked"):
            row["evolved"]["reading"] = f"not walked: {walked.get(i, {}).get('reason') or 'the step has no episodes'}."
        else:
            row["evolved"]["reading"] = _step_reading(row, metrics)
        steps_out.append(row)
    for mid, m in metrics.items():
        if m["status"] == "base":
            continue
        first = next((s["index"] for s in steps_out
                      if any(f["metric"] == mid for f in s["evolved"]["flags"] + s["evolved"]["moved"]
                             if f["direction"] == _bad_direction(m["spec"]["direction"]))), None)
        adopted = m["adopted_at"]["index"]
        m["caught_at"] = {"first_flag_step": (f"{gens[first - 1]}→{gens[first]}" if first is not None else None),
                          "first_flag_index": first, "adopted_step": m["adopted_at"]["step"], "adopted_index": adopted,
                          "lag": (adopted - first) if first is not None else None,
                          "note": ("never flags a step of this lineage" if first is None else
                                   "adopted at the first step it flags" if first == adopted else
                                   f"would have flagged {plural(adopted - first, 'step')} before its adoption" if first < adopted
                                   else f"first flags {plural(first - adopted, 'step')} after its adoption")}
    return {"matrix": matrix, "steps": steps_out}


# ---------------------------------------------------------------- the flow

def _flow(out: dict, gens: list) -> dict:
    """The whole loop as one graph, built from the ledger, the steps, the
    metrics and the eval generations already computed: agent generations
    and steps, the probes that fired, every candidate through the five
    validators to a decision, the decisions to the metrics, the eval
    generations, and the hindsight — a metric that would have flagged a
    step, and a later step on which an active metric moved back in its
    good direction with an interval excluding zero (``recovers``, labelled
    *recovered, not attributed*: a measured fact about the lineage, never
    a claim that the eval caused it). A loop closure is one ``recovers``
    edge: a step flagged on a metric and a later step recovering on it."""
    nodes: list = []
    edges: list = []
    seen: set = set()

    def node(nid: str, kind: str, label: str, **extra) -> str:
        if nid not in seen:
            seen.add(nid)
            nodes.append({"id": nid, "kind": kind, "label": label, **extra})
        return nid

    def edge(frm: str, to: str, kind: str, step=None, n: int = 1, label: str = "", **extra) -> None:
        edges.append({"from": frm, "to": to, "kind": kind, "step": step, "n": n, "label": label, **extra})

    for gid in gens:
        node(f"gen:{gid}", "agent_gen", gid, gen=gid)
    steps = out["steps"]
    for s in steps:
        label = f"{s['from']}→{s['to']}"
        node(f"step:{label}", "agent_step", label, step=label, index=s["index"], verdict=s["base"]["verdict"],
             changed=s["evolved"]["changed"])
        edge(f"gen:{s['from']}", f"gen:{s['to']}", "evolves", step=label, label=s["base"]["verdict"] or "unmeasurable")
    for p in out["probes"]:
        if p["fired"] or any(r["probe"] == p["name"] for r in out["ledger"]):
            node(f"probe:{p['name']}", "probe", p["name"], probe=p["name"], question=p["question"])
            for label in p["fired"]:
                edge(f"step:{label}", f"probe:{p['name']}", "triggers", step=label, label=p["question"])
    for name, _ in VALIDATORS:
        node(f"validator:{name}", "validator", name)
    for d in ("adopted", "rejected", "demoted", "retired"):
        node(f"decision:{d}", "decision", d)
    for e in out["eval_generations"]:
        node(f"eval:{e['id']}", "eval_gen", e["id"], step=e["after_step"], size=e["size"])
    for mid, m in out["metrics"].items():
        node(f"metric:{mid}", "metric", m["spec"]["name"], metric=mid, status=m["status"], direction=m["spec"]["direction"])
    for r in out["ledger"]:
        cid = node(f"cand:{r['index']}", "candidate", r["spec_id"] or "unparseable", step=r["step"], probe=r["probe"],
                   decision=r["decision"], ledger=r["index"])
        edge(f"probe:{r['probe']}", cid, "proposes", step=r["step"], label=r["reason"] if r["failed"] == [] and r["decision"] == "rejected" else "")
        for name, _ in VALIDATORS:
            v = r["validators"].get(name) or {}
            if v.get("pass") is None:
                continue
            edge(cid, f"validator:{name}", "passes" if v["pass"] else "fails", step=r["step"], label=v.get("note") or "")
        edge(cid, f"decision:{r['decision']}", "decides", step=r["step"], label=r["reason"])
    egens = out["eval_generations"]
    for i, e in enumerate(egens):
        if i:
            edge(f"eval:{egens[i - 1]['id']}", f"eval:{e['id']}", "advances", step=e["after_step"], label=e["trigger_probe"] or "")
        for mid in e["adopted"]:
            if mid in out["metrics"]:
                edge(f"eval:{e['id']}", f"metric:{mid}", "bears", step=e["after_step"], label="base" if i == 0 else "adopted")
    for mid, m in out["metrics"].items():
        if m["adopted_at"]:
            edge("decision:adopted", f"metric:{mid}", "adopts", step=m["adopted_at"]["step"], label=m["origin"].get("probe", ""))
        if m["demoted_at"]:
            edge("decision:demoted", f"metric:{mid}", "demotes", step=m["demoted_at"]["step"], label=m["demoted_at"]["reason"])
        if m["retired_at"]:
            edge("decision:retired", f"metric:{mid}", "retires", step=m["retired_at"]["step"], label=m["retired_at"]["reason"])
    flagged: dict = {}
    for s in steps:
        label = f"{s['from']}→{s['to']}"
        for f in s["evolved"]["flags"] + s["evolved"]["base_flags"]:
            m = out["metrics"][f["metric"]]
            fid = node(f"flag:{label}:{f['metric']}", "flag", f"{m['spec']['name']} at {label}", step=label, metric=f["metric"],
                       delta=f["delta"], direction=f["direction"], learned=f["learned"])
            lag = (m["adopted_at"]["index"] - s["index"]) if m["adopted_at"] else None
            edge(f"metric:{f['metric']}", f"step:{label}", "flags", step=label, lag=lag, flag=fid, learned=f["learned"],
                 label=("a base metric" if lag is None else "adopted at this step" if lag == 0 else
                        f"adopted {plural(lag, 'step')} later" if lag > 0 else f"adopted {plural(-lag, 'step')} earlier"))
            flagged.setdefault(f["metric"], []).append(s["index"])
    closures = closures_learned = 0
    for s in steps:
        label = f"{s['from']}→{s['to']}"
        for x in s["evolved"]["moved"]:
            m = out["metrics"][x["metric"]]
            earlier = [i for i in flagged.get(x["metric"], []) if i < s["index"]]
            if m["status"] not in ("base", "adopted") or x["direction"] != m["spec"]["direction"] or not earlier:
                continue
            closures += 1
            closures_learned += 1 if x["learned"] else 0
            edge(f"metric:{x['metric']}", f"step:{label}", "recovers", step=label, delta=x["delta"], learned=x["learned"],
                 after=[f"{gens[i - 1]}→{gens[i]}" for i in earlier],
                 label=f"recovered, not attributed: the lineage recovered on {m['spec']['name']} at {label}")
    by_kind_n = {}
    for n in nodes:
        by_kind_n[n["kind"]] = by_kind_n.get(n["kind"], 0) + 1
    by_kind_e = {}
    for e in edges:
        by_kind_e[e["kind"]] = by_kind_e.get(e["kind"], 0) + 1
    fired = [p["name"] for p in out["probes"] if p["fired"]]
    learned_flags = sum(1 for e in edges if e["kind"] == "flags" and e["learned"])
    sentence = (f"{plural(len(gens), 'agent generation')} and {plural(len(steps), 'step')} triggered "
                f"{plural(len(fired), 'probe')}" + (f" ({join_names(fired)})" if fired else "")
                + f" proposing {plural(by_kind_n.get('candidate', 0), 'candidate')}, of which "
                f"{by_kind_e.get('adopts', 0)} were adopted into {plural(len(egens) - 1, 'new eval generation')}; "
                f"with hindsight the eval flags {plural(by_kind_e.get('flags', 0), 'step-metric pair')} "
                f"({learned_flags} by learned metrics, {by_kind_e.get('flags', 0) - learned_flags} by base metrics) and sees "
                f"{plural(closures, 'loop closure')} ({closures_learned} on learned metrics) — a flagged step followed by a "
                f"later step recovering on the same metric, recovered, not attributed.")
    return {"nodes": nodes, "edges": edges,
            "summary": {"nodes": dict(sorted(by_kind_n.items())), "edges": dict(sorted(by_kind_e.items())),
                        "closures": closures, "closures_learned": closures_learned, "flags_learned": learned_flags,
                        "flags_base": by_kind_e.get("flags", 0) - learned_flags, "sentence": sentence}}


# ---------------------------------------------------------------- the section

_DRIFT_BASIS = "1 − |base ∩ final active| / |base ∪ final active|"
_MULTIPLICITY_BASIS = ("Bonferroni: alpha / K over the K candidates tested at a step; an unparseable or unaddressed external "
                       "candidate, or one whose spec is already adopted, demoted or retired, is a ledger row but not a test")


def _is_test(row: dict) -> bool:
    return row["decision"] != "rejected" or not row["reason"].startswith(("unparseable", "unaddressed"))


def _integrity(walk: dict, metrics: dict) -> dict:
    base_ids = {s["id"] for s in BASE_SPECS}
    final = {mid for mid, m in metrics.items() if m["status"] in ("base", "adopted")}
    union = base_ids | final
    ledger = walk["ledger"]
    tested = [r for r in ledger if _is_test(r)]
    alphas = [r["alpha"] for r in tested if r["alpha"] is not None]
    adopted = sum(1 for r in tested if r["decision"] == "adopted")
    return {
        "drift": {"jaccard_distance_from_base": rounded(1.0 - len(base_ids & final) / len(union)) if union else 0.0,
                  "size_by_eval_gen": [e["size"] for e in walk["eval_generations"]], "basis": _DRIFT_BASIS},
        "multiplicity": {"tested": len(tested), "adopted": adopted, "rejected": len(tested) - adopted,
                         "unparseable": len(ledger) - len(tested), "alpha": ALPHA,
                         "min_adjusted_alpha": rounded(min(alphas)) if alphas else None, "basis": _MULTIPLICITY_BASIS},
        "demoted": [mid for mid, m in metrics.items() if m["status"] == "demoted"],
        "retired": [mid for mid, m in metrics.items() if m["status"] == "retired"],
        "unconfirmed": [mid for mid, m in metrics.items() if m["confirmation"] and m["confirmation"]["status"] == "unconfirmed"],
        "external": {k: walk["external"][k] for k in ("received", "parsed", "adopted", "rejected", "sources")},
        "gap": GAP,
    }


def _narrative(out: dict) -> str:
    egens = out["eval_generations"]
    if len(egens) > 1:
        parts = [f"the eval grew from e0 ({plural(len(BASE_SPECS), 'base metric')}) to {egens[-1]['id']} "
                 f"({plural(egens[-1]['size'], 'active metric')}) over {plural(len(out['steps']), 'agent step')}"]
    else:
        parts = [f"the eval stayed at e0 ({plural(len(BASE_SPECS), 'base metric')}) over "
                 f"{plural(len(out['steps']), 'agent step')}: no candidate passed every validator"]
    for e in egens[1:]:
        bits = []
        if e["adopted"]:
            bits.append("adopted " + ", ".join(e["adopted"]))
        if e["demoted"]:
            bits.append("demoted " + ", ".join(e["demoted"]))
        if e["retired"]:
            bits.append("retired " + ", ".join(e["retired"]))
        parts.append(f"{e['id']} after {e['after_step']} ({e['trigger_probe']}): " + "; ".join(bits))
    mult = out["integrity"]["multiplicity"]
    parts.append(f"{plural(mult['tested'], 'candidate')} tested, {mult['adopted']} adopted, {mult['rejected']} rejected, "
                 f"the smallest adjusted level {num(mult['min_adjusted_alpha'], 4) if mult['min_adjusted_alpha'] is not None else '—'}")
    changed = [s for s in out["steps"] if s["evolved"]["changed"]]
    base_flagged = [s for s in out["steps"] if s["evolved"]["base_flags"]]
    parts.append(f"hindsight: {plural(len(out['steps']), 'step')} re-read, {len(changed)} changed by a learned metric"
                 + (" (" + ", ".join(f"{s['from']}→{s['to']}" for s in changed) + ")" if changed else "")
                 + f", {len(base_flagged)} where a base metric's own interval moves against it"
                 + (" (" + ", ".join(f"{s['from']}→{s['to']}" for s in base_flagged) + ")" if base_flagged else ""))
    rec = out["recommended"]
    parts.append(f"recommended: base {rec['base']}, evolved {rec['evolved']}, " + ("they agree" if rec["agree"] else "they disagree"))
    integ = out["integrity"]
    for word in ("demoted", "retired", "unconfirmed"):
        if integ[word]:
            parts.append(f"{word}: " + ", ".join(integ[word]))
    ext = integ["external"]
    if ext["received"]:
        parts.append(f"external: {ext['received']} received, {ext['parsed']} parsed, {ext['adopted']} adopted — validated, never trusted")
    parts.append("the gap: " + GAP)
    return "; ".join(parts) + "."


def _feature_list(table: list) -> list:
    out = [{"id": f, "kind": ft.kind, "basis": ft.basis, "direction": ft.direction, "dynamic": False}
           for f, ft in FEATURES.items()]
    for f in _vocabulary(table):
        if f.startswith("uses:"):
            out.append({"id": f, "kind": "count", "basis": f"calls of {f[5:]} in the episode", "direction": "neutral",
                        "dynamic": True})
    return out


def _thresholds() -> dict:
    return {"alpha": ALPHA, "redundant_rho": REDUNDANT_RHO, "link_rho": LINK_RHO, "min_coverage": MIN_COVERAGE,
            "min_n": MIN_N, "probe_top": PROBE_TOP, "confirm_steps": CONFIRM_STEPS, "min_series": MIN_SERIES}


def _empty(reason: str, lineage: dict, table: list, samples: int) -> dict:
    return unmeasurable(reason, version=VERSION, synthetic=_synthetic(lineage), family=lineage.get("family"),
                        samples=samples, thresholds=_thresholds(), features=_feature_list(table),
                        base=[s["id"] for s in BASE_SPECS],
                        eval_generations=[{"id": "e0", "index": 0, "after_step": None, "trigger_probe": None,
                                           "adopted": [s["id"] for s in BASE_SPECS], "demoted": [], "retired": [],
                                           "size": len(BASE_SPECS)}],
                        metrics={s["id"]: _metric(dict(s, origin={"probe": "base"}), "base", {"probe": "base"}, None, None)
                                 for s in BASE_SPECS},
                        ledger=[], matrix={}, steps=[], probes=list(_empty_probes().values()),
                        flow={"nodes": [], "edges": [], "summary": {"nodes": {}, "edges": {}, "closures": 0, "closures_learned": 0,
                                                                     "flags_learned": 0, "flags_base": 0, "sentence": reason}},
                        recommended={"base": None, "evolved": None, "agree": True, "why": reason,
                                     "excluded": {"base": {}, "evolved": {}}},
                        integrity={"drift": {"jaccard_distance_from_base": 0.0, "size_by_eval_gen": [len(BASE_SPECS)],
                                             "basis": _DRIFT_BASIS},
                                   "multiplicity": {"tested": 0, "adopted": 0, "rejected": 0, "unparseable": 0, "alpha": ALPHA,
                                                    "min_adjusted_alpha": None, "basis": _MULTIPLICITY_BASIS},
                                   "demoted": [], "retired": [], "unconfirmed": [],
                                   "external": {"received": 0, "parsed": 0, "adopted": 0, "rejected": 0, "sources": []},
                                   "gap": GAP},
                        hindsight={"steps": 0, "changed": 0, "learned_flags": 0, "base_flags": 0, "steps_with_base_flags": 0,
                                   "caught_at": {}},
                        narrative=reason)


def coevolve(lineage: dict, evolution: dict, *, samples: int = BOOTSTRAP_SAMPLES, candidates=None,
             protected=()) -> dict:
    """``aggregate["coevolution"]`` for a lineage (a :func:`deepcompare.evolve.read_lineage`
    result) and its ``evolution`` section: the feature table, the walk of
    the probes and validators step by step, the hindsight matrix and
    readings, the recommendation under both evals, the eval's integrity.
    ``candidates`` are external candidate specs, ``[{"at": "<from>→<to>"
    | null, "spec": {...}, "source": "..."}]``; ``protected`` overrides
    the lineage's protected paths, for the feature table and the step
    views alike. ``measurable: False`` with the reason when the lineage or
    the evolution cannot be read, fewer than two generations carry
    episodes, or ``samples`` is under 1 (no bootstrap draw: every interval
    would be a bare point)."""
    protected = list(protected) if protected else None
    table = feature_table(lineage, protected) if lineage.get("generations") else []
    if not lineage.get("measurable"):
        return _empty(lineage.get("reason") or "the lineage cannot be read", lineage, table, samples)
    if not isinstance(evolution, dict) or not evolution.get("measurable"):
        return _empty("the evolution section is unmeasurable: " + str((evolution or {}).get("reason") or "absent"),
                      lineage, table, samples)
    if int(samples) < 1:
        return _empty(_no_draws(samples), lineage, table, samples)
    with_eps = [row for row in table if row["episodes"]]
    if len(with_eps) < 2:
        return _empty(f"{plural(len(with_eps), 'generation')} carry episodes, so there is no step to walk",
                      lineage, table, samples)
    walk = _walk(lineage, evolution, table, samples, candidates, protected)
    metrics = walk["metrics"]
    _annotate_base(walk["cache"], metrics, walk["cache"].episodes(walk["gens"][-1]))
    hind = _hindsight(walk, evolution)
    out = measurable(version=VERSION)
    out.update({
        "synthetic": _synthetic(lineage), "family": lineage.get("family"), "samples": samples,
        "thresholds": _thresholds(), "features": _feature_list(table), "base": [s["id"] for s in BASE_SPECS],
        "eval_generations": walk["eval_generations"], "metrics": metrics, "ledger": walk["ledger"],
        "matrix": hind["matrix"], "steps": hind["steps"], "probes": walk["probes"],
    })
    out["flow"] = _flow(out, walk["gens"])
    out["recommended"] = _recommend(evolution, hind["steps"], metrics)
    out["integrity"] = _integrity(walk, metrics)
    out["hindsight"] = {"steps": len(hind["steps"]), "changed": sum(1 for s in hind["steps"] if s["evolved"]["changed"]),
                        "learned_flags": sum(len(s["evolved"]["flags"]) for s in hind["steps"]),
                        "base_flags": sum(len(s["evolved"]["base_flags"]) for s in hind["steps"]),
                        "steps_with_base_flags": sum(1 for s in hind["steps"] if s["evolved"]["base_flags"]),
                        "caught_at": {mid: m["caught_at"] for mid, m in metrics.items() if m["caught_at"]}}
    out["narrative"] = _narrative(out)
    return out


@sections.register("lineage", "coevolution", requires=("evolution",))
def _section(agg: dict, ctx: "sections.LineageContext") -> dict:
    # the eval that evolves with the agent: always attached, reads the
    # evolution section already on the aggregate and the external
    # candidates the command passed through ctx.extra
    extra = dict(getattr(ctx, "extra", None) or {})
    return coevolve(ctx.lineage or {}, agg.get("evolution"), samples=extra.get("samples", BOOTSTRAP_SAMPLES),
                    candidates=extra.get("candidates"))


# ---------------------------------------------------------------- briefs and fail-on

def proposal_brief(view: StepView) -> dict:
    """What an external proposer is told about one step: the feature
    vocabulary with its bases, the spec language, the step's diff summary
    and base verdict, the adopted metrics, the per-feature shifts. JSON,
    no episode text, no credentials."""
    step = view.step
    return {"step": view.label, "from": view.frm, "to": view.to,
            "features": [{"id": f, "kind": FEATURES[f].kind if f in FEATURES else "count",
                          "basis": FEATURES[f].basis if f in FEATURES else f"calls of {f[5:]}"} for f in view.vocabulary],
            "language": {"agg": list(AGGS), "ops": list(OPS), "directions": list(DIRECTIONS),
                         "where": "null, {feature, op, value} or {all: [predicates]}; filters episodes before aggregating"},
            "diff_summary": (step.get("diff") or {}).get("summary"), "mechanism": step.get("mechanism"),
            "base_verdict": step.get("verdict"), "base_flags": list(step.get("flags") or []),
            "adopted": [{"id": m["spec"]["id"], "name": m["spec"]["name"], "feature": m["spec"]["feature"],
                         "agg": m["spec"]["agg"], "where": m["spec"]["where"], "direction": m["spec"]["direction"]}
                        for m in view.active()],
            "shifts": [{k: r[k] for k in ("feature", "from", "to", "shift", "standardised", "separates", "sign_vs_outcome")}
                       for r in feature_shifts(view) if not r["reason"]]}


def proposal_briefs(lineage: dict, evolution: dict, *, coevolution: Optional[dict] = None, protected=()) -> list:
    """One brief per walkable step, ``[{"at", "brief"}]``, for a command
    that asks an external proposer; the adopted metrics of each step are
    read off a prior ``coevolution`` output's eval generations when
    given, else the base eval stands for every step."""
    if not lineage.get("measurable") or not isinstance(evolution, dict) or not evolution.get("measurable"):
        return []
    table = feature_table(lineage, list(protected) if protected else None)
    cache = _Cache(table, 0)
    gens = [row["id"] for row in table]
    ev_steps = {s["index"]: s for s in evolution.get("steps") or []}
    tools_by_gen = [{k[5:] for e in row["episodes"] for k, v in e["values"].items() if k.startswith("uses:") and v}
                    for row in table]
    claims_by_gen = [any(e["values"].get("claims") for e in row["episodes"]) for row in table]
    metrics: dict = {s["id"]: _metric(dict(s, origin={"probe": "base"}), "base", {"probe": "base"}, None, None)
                     for s in BASE_SPECS}
    known = (coevolution or {}).get("metrics") or {}
    out = []
    for i in range(1, len(gens)):
        frm, to = gens[i - 1], gens[i]
        step = ev_steps.get(i) or {}
        if not cache.by_gen[frm] or not cache.by_gen[to] or not (step.get("effect") or {}).get("measurable"):
            continue
        for mid, m in known.items():
            at = m.get("adopted_at") or {}
            if mid not in metrics and at.get("index") is not None and at["index"] < i and m["status"] == "adopted":
                metrics[mid] = _metric(m["spec"], "adopted", m["origin"], None, at)
        view = StepView(index=i, frm=frm, to=to, step=step, parent=cache.by_gen[frm], child=cache.by_gen[to],
                        so_far=cache.episodes(to), gens_so_far=gens[:i + 1], metrics=metrics,
                        tools_child=tools_by_gen[i], tools_earlier=tools_by_gen[:i], claims_child=claims_by_gen[i],
                        claims_earlier=any(claims_by_gen[:i]), candidates=[], cache=cache, vocabulary=_vocabulary(table, i),
                        protected=list(protected) if protected else list(lineage.get("protected") or []))
        out.append({"at": view.label, "brief": proposal_brief(view)})
    return out


def fail_on(coevolution: dict, names) -> list:
    """``(name, detail)`` for every name of :data:`FAIL_ON` the section
    trips: ``hindsight`` (a step the evolved eval changed), ``demoted``,
    ``unconfirmed`` (a metric each), ``rejected_external`` (an external
    candidate rejected). Unknown names raise ``ValueError``."""
    wanted = {n.strip() for n in names if n and n.strip()}
    unknown = sorted(wanted - set(FAIL_ON))
    if unknown:
        raise ValueError(f"unknown --fail-on name(s): {', '.join(unknown)}; choose from {', '.join(FAIL_ON)}")
    hits = []
    integ = coevolution.get("integrity") or {}
    if "hindsight" in wanted:
        hits += [("hindsight", f"{s['from']}→{s['to']}") for s in coevolution.get("steps") or [] if s["evolved"]["changed"]]
    if "demoted" in wanted:
        hits += [("demoted", mid) for mid in integ.get("demoted") or []]
    if "unconfirmed" in wanted:
        hits += [("unconfirmed", mid) for mid in integ.get("unconfirmed") or []]
    if "rejected_external" in wanted:
        n = (integ.get("external") or {}).get("rejected") or 0
        if n:
            hits.append(("rejected_external", plural(n, "external candidate")))
    return hits


__all__ = ["coevolve", "features", "feature_table", "feature_shifts", "parse_spec", "value", "per_task", "delta", "proposal_brief",
           "proposal_briefs", "fail_on", "Feature", "Probe", "StepView", "FEATURES", "PROBES", "VALIDATORS", "BASE_SPECS",
           "AGGS", "OPS", "DIRECTIONS", "FAIL_ON", "GAP", "VERSION", "ALPHA", "REDUNDANT_RHO", "LINK_RHO", "MIN_COVERAGE",
           "MIN_N", "PROBE_TOP", "CONFIRM_STEPS", "MIN_SERIES"]
