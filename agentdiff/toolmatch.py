"""Tool-call comparison against a reference, in the industry's vocabulary (v22).

Everywhere else AgentDiff invents its own terms.  Here it deliberately does
not, because this is the one part of agent evaluation where a shared
vocabulary already exists and interoperating is worth more than being
original.  The four match modes are LangChain's ``agentevals``
(``strict``/``unordered``/``subset``/``superset``), the argument modes are
its ``ToolArgsMatchMode``, the F1 is Ragas's ``ToolCallF1``, and the
order-aware partial credit is DeepEval's weighted-LCS ``ToolCorrectness``.

**Every result names the algorithm that produced it.**  That is not
pedantry.  Four widely-used libraries ship a metric called "tool call
accuracy" and compute four different numbers from the same trace:

* ``agentevals`` returns a boolean, no partial credit;
* Ragas multiplies argument accuracy by an order gate, so a correct set of
  calls in the wrong order scores 0.0 rather than 0.9;
* DeepEval's default scores greedy best-match over expected calls, with
  arguments compared as the fraction of keys that agree over the union of
  keys — so a call with one wrong argument out of three is 0.67, not 0;
* DeepEval in ordering mode returns weighted LCS over the expected length.

A number reported as "tool call accuracy: 0.67" is therefore meaningless
without its algorithm, and a leaderboard that mixes them is comparing
nothing.  Each function here returns its ``algorithm`` and ``source`` beside
its score.

Two footguns, inherited and documented rather than silently fixed:

* **Subset/superset polarity.**  In ``agentevals``, ``subset`` means *the
  agent's calls are a subset of the reference* — it made no call the
  reference did not.  The names invert easily in conversation, and its own
  implementation reads ``_is_trajectory_superset(reference, outputs)`` for
  the ``subset`` mode.  The polarity here matches the library, and each
  result spells out the direction in words.
* **Argument equality is not one thing.**  Exact dict equality, key-fraction
  and containment give different answers on the same pair; the mode is
  always reported.
"""

from __future__ import annotations

from typing import Optional

from .tooldiff import TOOLISH_TYPES, parse_args
from .trace import Trajectory

#: how two tool calls' arguments are compared, following agentevals'
#: ToolArgsMatchMode plus DeepEval's key-fraction as a partial-credit option.
ARG_MODES = ("exact", "ignore", "subset", "superset", "key_fraction")

#: how two call *sequences* are compared, following agentevals.
MATCH_MODES = ("strict", "unordered", "subset", "superset")

_MODE_MEANING = {
    "strict": "same calls in the same order",
    "unordered": "same calls, any order",
    "subset": "the run called only tools the reference called (no extras)",
    "superset": "the run called at least the reference's tools (extras allowed)",
}


def calls_of(trajectory: Trajectory) -> list[dict]:
    """The tool calls in a trajectory, as ``{name, args, index}``.

    Arguments come from the heuristic call-string parser, so a call that
    does not parse keeps its raw text under the ``_raw`` key rather than
    being dropped — an unparseable call is still a call, and losing it would
    quietly improve every score.
    """
    calls = []
    for step in trajectory.steps:
        if step.type not in TOOLISH_TYPES:
            continue
        args = parse_args(step.input)
        calls.append({
            "name": step.name,
            "args": args if args is not None else {"_raw": step.input.strip()},
            "parsed": args is not None,
            "index": step.index,
        })
    return calls


def args_match(left: dict, right: dict, mode: str = "exact") -> float:
    """Compare two argument dicts under one of the ARG_MODES.

    Returns a score in [0, 1].  Only ``key_fraction`` ever returns a value
    strictly between 0 and 1; the others are the library-faithful booleans.
    """
    if mode not in ARG_MODES:
        raise ValueError(f"unknown arg mode {mode!r}; must be one of {', '.join(ARG_MODES)}")
    if mode == "ignore":
        return 1.0
    left = left or {}
    right = right or {}
    if mode == "exact":
        return 1.0 if _norm_dict(left) == _norm_dict(right) else 0.0
    if mode == "subset":
        # every argument the run passed also appears, identically, in the
        # reference call
        return 1.0 if _contains(_norm_dict(right), _norm_dict(left)) else 0.0
    if mode == "superset":
        return 1.0 if _contains(_norm_dict(left), _norm_dict(right)) else 0.0
    return _key_fraction(_norm_dict(left), _norm_dict(right))


def _norm_dict(d: dict) -> dict:
    return {str(k): str(v).strip().strip("\"'") for k, v in (d or {}).items()}


def _contains(outer: dict, inner: dict) -> bool:
    return all(key in outer and outer[key] == value for key, value in inner.items())


def _key_fraction(left: dict, right: dict) -> float:
    """DeepEval's ``_compare_dicts``: matching keys over the union of keys.

    The union matters — scoring over the intersection would give a call that
    omits half the required arguments a perfect score for the half it kept.
    """
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    agreed = sum(1 for key in keys if key in left and key in right and left[key] == right[key])
    return round(agreed / len(keys), 4)


def _call_score(a: dict, b: dict, arg_mode: str) -> float:
    if a["name"] != b["name"]:
        return 0.0
    return args_match(a["args"], b["args"], arg_mode)


def trajectory_match(
    run: Trajectory,
    reference: Trajectory,
    mode: str = "strict",
    arg_mode: str = "exact",
) -> dict:
    """Boolean trajectory match in agentevals' four modes.

    Boolean by design: the library gives no partial credit, and inventing
    some here under the same mode name would make this tool's "strict match"
    incomparable with everyone else's.  For partial credit use
    :func:`tool_call_f1` or :func:`weighted_lcs_correctness`, which say in
    their own names that they are different measurements.
    """
    if mode not in MATCH_MODES:
        raise ValueError(f"unknown match mode {mode!r}; must be one of {', '.join(MATCH_MODES)}")
    actual, expected = calls_of(run), calls_of(reference)

    if mode == "strict":
        matched = len(actual) == len(expected) and all(
            _call_score(x, y, arg_mode) == 1.0 for x, y in zip(actual, expected))
    elif mode == "unordered":
        matched = _covers(actual, expected, arg_mode) and _covers(expected, actual, arg_mode)
    elif mode == "subset":
        matched = _covers(expected, actual, arg_mode)   # reference covers the run
    else:
        matched = _covers(actual, expected, arg_mode)   # run covers the reference

    return {
        "match": matched,
        "mode": mode,
        "means": _MODE_MEANING[mode],
        "arg_mode": arg_mode,
        "calls": len(actual),
        "reference_calls": len(expected),
        "algorithm": f"agentevals trajectory_match({mode}, args={arg_mode})",
        "source": "LangChain agentevals — boolean, no partial credit",
    }


def _covers(outer: list, inner: list, arg_mode: str) -> bool:
    """Every call in ``inner`` has an unconsumed equal in ``outer``."""
    remaining = list(outer)
    for call in inner:
        for position, candidate in enumerate(remaining):
            if _call_score(candidate, call, arg_mode) == 1.0:
                remaining.pop(position)
                break
        else:
            return False
    return True


def tool_call_f1(run: Trajectory, reference: Trajectory,
                 arg_mode: str = "exact") -> dict:
    """Unordered precision / recall / F1 over tool calls (Ragas ToolCallF1).

    Reported with TP, FP and FN rather than F1 alone, because the two ways
    of being wrong call for opposite fixes: false positives are an agent
    doing more than it was asked, false negatives an agent doing less, and a
    single F1 of 0.67 hides which.
    """
    actual, expected = calls_of(run), calls_of(reference)
    remaining = list(expected)
    true_positive = 0
    unmatched_actual = []
    for call in actual:
        for position, candidate in enumerate(remaining):
            if _call_score(call, candidate, arg_mode) == 1.0:
                remaining.pop(position)
                true_positive += 1
                break
        else:
            unmatched_actual.append(call)

    false_positive = len(unmatched_actual)
    false_negative = len(remaining)
    precision = true_positive / (true_positive + false_positive) if actual else None
    recall = true_positive / (true_positive + false_negative) if expected else None
    if precision and recall:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    elif precision is None or recall is None:
        f1 = None
    else:
        f1 = 0.0
    return {
        "f1": f1,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "true_positives": true_positive,
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "extra_calls": [c["name"] for c in unmatched_actual],
        "missed_calls": [c["name"] for c in remaining],
        "arg_mode": arg_mode,
        "algorithm": f"Ragas ToolCallF1 (unordered, args={arg_mode})",
        "source": "Ragas — order-insensitive; an argument mismatch is a full miss "
                  "under exact, partial under key_fraction",
    }


def weighted_lcs_correctness(run: Trajectory, reference: Trajectory,
                             arg_mode: str = "key_fraction") -> dict:
    """Order-aware partial credit: weighted LCS over the reference length.

    DeepEval's ``ToolCorrectness(should_consider_ordering=True)``.  Strictly
    more informative than an order gate that zeroes the score whenever the
    sequence differs: an agent that made the right calls in a slightly
    different order scores most of the credit, and one that made them in a
    completely different order does not.
    """
    actual, expected = calls_of(run), calls_of(reference)
    if not expected:
        return {"score": None, "algorithm": "DeepEval weighted-LCS",
                "source": "DeepEval ToolCorrectness(should_consider_ordering=True)",
                "note": "reference has no tool calls; nothing to score against",
                "arg_mode": arg_mode, "matched_length": 0, "reference_calls": 0}

    rows, cols = len(expected), len(actual)
    table = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            pair = _call_score(actual[j - 1], expected[i - 1], arg_mode)
            table[i][j] = max(table[i - 1][j], table[i][j - 1],
                              table[i - 1][j - 1] + pair)
    best = table[rows][cols]
    return {
        "score": round(min(1.0, best / rows), 4),
        "matched_length": round(best, 4),
        "reference_calls": rows,
        "calls": cols,
        "arg_mode": arg_mode,
        "algorithm": "DeepEval weighted-LCS / len(reference)",
        "source": "DeepEval ToolCorrectness(should_consider_ordering=True) — "
                  "order-aware partial credit",
    }


def tool_permission(run: Trajectory,
                    allowed: Optional[list] = None,
                    denied: Optional[list] = None) -> dict:
    """Did the run only call tools it was allowed to?

    Deterministic, cheap, and the one check here that belongs in CI as a
    hard gate rather than a report line.  A denial beats an allow, following
    DeepEval, so a tool named in both lists is refused: the failure mode
    worth defending against is a tool that should never run, and an
    overlapping allowlist must not re-enable it.

    With no lists given the trajectory's own declared tools are the
    allowlist; with neither, the check is unmeasurable and says so instead
    of returning a clean pass.
    """
    declared = [str(t.get("name")) for t in (run.tools or []) if t.get("name")]
    allow = set(allowed) if allowed is not None else set(declared)
    deny = set(denied or ())
    if not allow and not deny:
        return {"measurable": False, "violations": 0, "detail": [],
                "score": None, "authorized": 0, "calls": 0,
                "note": "no allowlist, denylist or declared tools; permission unchecked",
                "algorithm": "DeepEval ToolPermission (deny beats allow)"}

    violations, authorized, calls = [], 0, 0
    for call in calls_of(run):
        calls += 1
        name = call["name"]
        if name in deny:
            violations.append({"index": call["index"], "name": name, "rule": "denied"})
        elif allow and name not in allow:
            violations.append({"index": call["index"], "name": name, "rule": "not allowed"})
        else:
            authorized += 1
    return {
        "measurable": True,
        "calls": calls,
        "authorized": authorized,
        "violations": len(violations),
        "detail": violations[:12],
        # No calls is a pass: an agent that touched nothing violated nothing.
        "score": round(authorized / calls, 4) if calls else 1.0,
        "allowlist": sorted(allow),
        "denylist": sorted(deny),
        "note": None,
        "algorithm": "DeepEval ToolPermission (deny beats allow)",
    }


def evaluate(run: Trajectory, reference: Trajectory,
             arg_mode: str = "exact",
             allowed: Optional[list] = None,
             denied: Optional[list] = None) -> dict:
    """Every reference-based tool metric for one run, each named.

    All four match modes are reported together on purpose.  They disagree by
    construction — a run can be a superset match and not a strict one — and
    seeing which modes pass is more informative than any single verdict.
    """
    return {
        "reference": reference.agent.name,
        "run": run.agent.name,
        "matches": {mode: trajectory_match(run, reference, mode, arg_mode)
                    for mode in MATCH_MODES},
        "f1": tool_call_f1(run, reference, arg_mode),
        "ordered": weighted_lcs_correctness(run, reference),
        "permission": tool_permission(run, allowed, denied),
        "narrative": _narrative(run, reference, arg_mode, allowed, denied),
    }


def _narrative(run, reference, arg_mode, allowed, denied) -> str:
    f1 = tool_call_f1(run, reference, arg_mode)
    strict = trajectory_match(run, reference, "strict", arg_mode)
    unordered = trajectory_match(run, reference, "unordered", arg_mode)
    permission = tool_permission(run, allowed, denied)

    if strict["match"]:
        lead = (f"{run.agent.name} made exactly the reference's tool calls, in "
                f"order.")
    elif unordered["match"]:
        lead = (f"{run.agent.name} made the reference's tool calls but in a "
                f"different order — a strict match fails, an unordered one passes.")
    else:
        bits = []
        if f1["false_negatives"]:
            bits.append(f"missed {f1['false_negatives']} "
                        f"({', '.join(f1['missed_calls'][:3])})")
        if f1["false_positives"]:
            bits.append(f"added {f1['false_positives']} "
                        f"({', '.join(f1['extra_calls'][:3])})")
        lead = (f"{run.agent.name} diverged from the reference's calls: "
                + (" and ".join(bits) if bits else "arguments differ") + ".")
    if permission["measurable"] and permission["violations"]:
        lead += (f" {permission['violations']} call(s) were not permitted — "
                 "that is a gate failure, not a quality score.")
    return lead + (f" Scores use argument matching '{arg_mode}'; a different "
                   "argument mode gives a different number for the same run.")
