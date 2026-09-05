"""Serving efficiency: what a trace implies about where the run's cost and
time went, and what could be optimized (v24).

Once accuracy is settled the next question is operational: this run cost
what it cost — how much of that was structural?  Serving spend is dominated
by a few patterns that are visible in a logged trace with no re-execution:

* **re-sent context** — each LLM turn re-sends the conversation so far, and
  a prompt cache with a stable prefix absorbs exactly that re-send;
* **identical tool calls** — the same ``(tool, canonical args)`` call made
  twice with the same result is a result-cache hit that never happened;
* **sequential independent reads** — read-effect calls whose arguments draw
  nothing from each other's outputs could have been issued concurrently;
* **latency concentration** — wall-clock time piled into one or two steps.

Everything here is class-A: deterministic, computed from the log alone,
stdlib only, no wall-clock, no network.  Three disciplines carried over
from :mod:`deepcompare.process`:

* **Savings are ceilings, not forecasts.**  Every recoverable figure
  assumes a 100% cache-hit rate or perfect concurrency, and says so in an
  ``assumption`` field.  "Recoverable" means an upper bound on what the
  optimization *could* absorb — never a prediction of what it will.
* **Estimates carry a basis; unmeasurable is ``None`` plus a reason.**
  A ``tokens_basis`` of ``estimated`` means the count came from text
  length, and nothing derived from it may be presented as a measurement —
  throughput in particular is refused rather than computed from estimates.
  An *undeclared* basis is treated as unverified (never promoted to
  "measured"); a cost of 0 everywhere is reported unmeasurable, because
  unrecorded is not free.
* **Every rate carries its denominator**, named in the output, so a share
  or a ratio cannot be quoted without what it is a share of.

A retry after an error is not a cacheable repeat — retrying a failed call
is correct behaviour (the same discipline as ``process.repeats``).  The
parallelism check is textual and conservative: any provenance link from an
earlier output into a later call's arguments, any unparseable arguments,
any error observation, or any effect that is not a confirmed read breaks
the run rather than being explained away.
"""

from __future__ import annotations

from typing import Optional

from .process import _digest, _norm, _signature, _tool_table, effect_of, is_error
from .tooldiff import TOOLISH_TYPES, parse_args
from .trace import Trajectory

#: how many of the slowest steps the latency block names.
TOP_LATENCY_STEPS = 3
#: a resend overhead below this share of total input, or below this many
#: tokens, is not surfaced as an opportunity — the numbers are still
#: reported, but a prompt cache is not worth a line item for noise.
RESEND_SHARE_FLOOR = 0.2
RESEND_TOKEN_FLOOR = 50
#: argument values shorter than this are not checked for provenance — a
#: page number or a boolean matches an earlier output by accident.  Chosen
#: shorter than process.grounding's threshold because here a false match
#: merely *breaks a run* (the conservative direction), it never accuses.
MIN_PROVENANCE_LEN = 4
#: "concentrated" means at least this share of wall-clock in at most this
#: many steps — but only in runs long enough for that to be informative:
#: in a three-step run, "half the time in two steps" is arithmetic, not a
#: finding, so the flag needs the slow steps to be a minority of the run.
CONCENTRATION_SHARE = 0.5
CONCENTRATION_STEPS = 2
MIN_STEPS_FOR_CONCENTRATION = 5

#: the ceiling assumptions, written once so every emitter says the same thing.
CACHE_ASSUMPTION = (
    "assumes a 100% hit rate on identical (tool, arguments) calls with "
    "identical results — the figure is a ceiling, not a forecast"
)
PREFIX_ASSUMPTION = (
    "assumes each turn re-sends the full prior conversation and that a "
    "stable-prefix prompt cache hits 100% of the re-send — a ceiling, not "
    "a forecast"
)
PARALLEL_ASSUMPTION = (
    "assumes the independent reads are issued concurrently and complete in "
    "the time of the slowest — a ceiling, not a forecast"
)


def _step_token_basis(step, trajectory: Trajectory) -> str:
    """Where this step's token count came from: measured, estimated, or
    undeclared.

    The step's own ``tokens_basis`` wins; the run-level
    ``token_accounting.basis`` fills in when it is unambiguous
    ("measured"/"estimated" — a run-level "mixed" says nothing about this
    step).  Anything else is ``undeclared``, which is *not* promoted to
    measured: an unlabelled count cannot be verified.
    """
    if step.tokens_basis in ("measured", "estimated"):
        return step.tokens_basis
    run_basis = (trajectory.token_accounting or {}).get("basis")
    if run_basis in ("measured", "estimated"):
        return run_basis
    return "undeclared"


def _merge_basis(bases: list[str]) -> Optional[str]:
    """Collapse component bases into the opportunity-level label.

    ``measured`` only when every component is measured.  ``undeclared``
    counts as *estimated* here — the conservative direction: an unlabelled
    number must not be presented as a measurement.  A mix of measured and
    anything else is ``mixed``.
    """
    if not bases:
        return None
    demoted = ["measured" if b == "measured" else "estimated" for b in bases]
    unique = set(demoted)
    if unique == {"measured"}:
        return "measured"
    if unique == {"estimated"}:
        return "estimated"
    return "mixed"


def _cost_rate(trajectory: Trajectory) -> Optional[float]:
    """This run's observed cost per token, or None when cost or tokens are
    unrecorded.

    Denominator: the run's total input+output tokens.  Uniform per-token
    pricing is an approximation (providers price input and output
    differently), so anything derived from this rate is an estimate.
    """
    tokens = trajectory.totals.input_tokens + trajectory.totals.output_tokens
    if trajectory.totals.cost_usd > 0 and tokens > 0:
        return trajectory.totals.cost_usd / tokens
    return None


def _tokens_to_cost(tokens: Optional[float], rate: Optional[float]) -> Optional[float]:
    if tokens is None or rate is None:
        return None
    return round(tokens * rate, 6)


# ---------------------------------------------------------------------------
# 1. context growth and the prompt-cache opportunity


def context_growth(trajectory: Trajectory) -> dict:
    """How the context grew, and what a stable-prefix prompt cache could absorb.

    Each LLM turn re-sends the conversation so far, so total input tokens
    exceed the *unique* content (the task prompt plus everything generated
    once).  The excess — ``resend_overhead_tokens`` — is exactly what a
    prompt cache with a stable prefix would absorb, at a 100% hit rate.

    Everything here is an **estimate** and labelled so: the per-turn input
    split is not logged, and prompt tokens are estimated as len(text)/4.
    ``resend_share``'s denominator is ``totals.input_tokens``.  When input
    tokens are unrecorded (0) the block is unmeasurable — unrecorded is not
    free, and a zero here says nothing about the run.
    """
    input_t = trajectory.totals.input_tokens
    output_t = trajectory.totals.output_tokens
    step_tokens = [s.tokens for s in trajectory.steps]
    cumulative, running = [], 0
    for count in step_tokens:
        running += count
        cumulative.append(running)
    prompt = trajectory.task.prompt or ""
    prompt_est = max(1, round(len(prompt) / 4)) if prompt else 0
    bases = {_step_token_basis(s, trajectory) for s in trajectory.steps}
    tokens_estimated = "estimated" in bases

    result = {
        "turns": len(trajectory.steps),
        "step_tokens_total": sum(step_tokens),
        "cumulative_step_tokens": cumulative,
        "input_tokens": input_t,
        "output_tokens": output_t,
        "prompt_tokens_estimate": prompt_est,
        "basis": "estimated",
        "assumptions": [
            "each turn re-sends the full prior conversation (the standard "
            "chat-completion pattern)",
            "prompt tokens estimated as len(prompt)/4 — the per-turn input "
            "split is not logged",
            PREFIX_ASSUMPTION,
        ],
        "tokens_note": ("step token counts are themselves estimates "
                        "(tokens_basis: estimated)" if tokens_estimated else None),
    }
    if input_t <= 0:
        result.update({
            "measurable": False,
            "reason": ("totals.input_tokens is 0 — input tokens unrecorded, "
                       "and unrecorded ≠ free; the resend overhead cannot "
                       "be estimated"),
            "unique_content_estimate": None,
            "resend_overhead_tokens": None,
            "resend_share": None,
            "prompt_cache_absorbable_tokens": None,
        })
        return result
    unique = output_t + prompt_est
    overhead = input_t - unique
    result.update({
        "measurable": True,
        "reason": None,
        "unique_content_estimate": unique,
        "resend_overhead_tokens": overhead,
        # denominator: totals.input_tokens
        "resend_share": round(overhead / input_t, 4),
        "prompt_cache_absorbable_tokens": max(0, overhead),
    })
    if overhead <= 0:
        result["reason"] = ("input does not exceed the unique-content "
                            "estimate; nothing visible for a prefix cache "
                            "to absorb")
    return result


# ---------------------------------------------------------------------------
# 2. result-cache opportunity


def result_cache(trajectory: Trajectory) -> dict:
    """Identical calls with identical results: the repeats a result cache
    would have absorbed.

    A repeat is cacheable only when (a) the ``(tool, canonical args)``
    signature matches an earlier call, (b) the observation is byte-identical
    (a tool that answers differently is not safely cacheable, and such
    repeats are counted separately as evidence *against* caching), and
    (c) the immediately preceding occurrence was not an error — a repeat
    after an error is a retry, which is correct behaviour, not waste.

    Recoverable tokens/latency are sums over the cacheable repeats only,
    and only over steps where the figure was recorded; an unrecorded figure
    yields ``None`` with a reason, never 0.  The whole saving is a ceiling
    (see ``assumption``).
    """
    seen: dict[str, dict] = {}   # signature -> latest occurrence
    cacheable, retries, differing = [], 0, 0
    calls = 0
    for step in trajectory.steps:
        if step.type not in TOOLISH_TYPES:
            continue
        calls += 1
        signature = _signature(step)
        digest = _digest(step.output)
        errored = is_error(step)[0]
        previous = seen.get(signature)
        if previous is not None:
            if previous["errored"]:
                retries += 1
            elif previous["digest"] != digest:
                differing += 1
            else:
                cacheable.append({
                    "index": step.index,
                    "name": step.name,
                    "first_seen": previous["index"],
                    "tokens": step.tokens if step.tokens > 0 else None,
                    "tokens_basis": _step_token_basis(step, trajectory),
                    "latency_s": step.latency_s if step.latency_s > 0 else None,
                })
        seen[signature] = {"index": step.index, "digest": digest,
                           "errored": errored}

    token_parts = [r["tokens"] for r in cacheable if r["tokens"] is not None]
    latency_parts = [r["latency_s"] for r in cacheable if r["latency_s"] is not None]
    tokens_saving = sum(token_parts) if token_parts else None
    latency_saving = round(sum(latency_parts), 4) if latency_parts else None
    return {
        "calls": calls,   # denominator for any repeat rate
        "cacheable_repeats": cacheable,
        "count": len(cacheable),
        "excluded_retries_after_error": retries,
        "repeats_with_different_results": differing,
        "recoverable": {
            "tokens": tokens_saving,
            "tokens_basis": _merge_basis(
                [r["tokens_basis"] for r in cacheable if r["tokens"] is not None]),
            "tokens_reason": (None if tokens_saving is not None else (
                "no cacheable repeats" if not cacheable else
                "step token counts unrecorded (0) on every cacheable repeat — "
                "unrecorded ≠ free")),
            "latency_s": latency_saving,
            "latency_reason": (None if latency_saving is not None else (
                "no cacheable repeats" if not cacheable else
                "step latency unrecorded (0) on every cacheable repeat")),
        },
        "assumption": CACHE_ASSUMPTION,
    }


# ---------------------------------------------------------------------------
# 3. parallelizable reads


def parallel_reads(trajectory: Trajectory) -> dict:
    """Maximal runs of consecutive independent read calls that could have
    been issued concurrently.

    Independence is decided by argument provenance, the same idea as
    ``process.grounding`` run in reverse: a value appearing in an earlier
    step's output creates a dependency, so a later call whose arguments
    contain any value (≥ 4 chars, normalized) found in the output of an
    earlier member of the run *depends* on it and breaks the run.

    The check is **textual and conservative**.  A run is also broken by:
    a step that is not a tool call, a write or unconfirmed effect (only
    confirmed reads are safely reorderable), an error observation, an
    unparseable argument list (what cannot be checked is not declared
    independent), or a signature already in the run (a duplicate is a cache
    case, not a parallel case).  Wall-clock saving for a run of k
    independent reads is sum(latencies) − max(latencies) — a ceiling
    (see ``assumption``); it is ``None`` when any member's latency is
    unrecorded.
    """
    table = _tool_table(trajectory)
    runs: list[list] = []
    breaks: list[dict] = []
    current: list[dict] = []   # {"step", "args", "signature"}

    def flush():
        nonlocal current
        if len(current) >= 2:
            runs.append([m["step"] for m in current])
        current = []

    for step in trajectory.steps:
        if step.type not in TOOLISH_TYPES:
            flush()
            continue
        effect, effect_basis = effect_of(step, table)
        if effect != "read" or effect_basis == "assumed":
            flush()
            breaks.append({"index": step.index, "reason":
                           "not a confirmed read (effect "
                           f"{effect!r}, basis {effect_basis!r})"})
            continue
        if is_error(step)[0]:
            flush()
            breaks.append({"index": step.index,
                           "reason": "error observation"})
            continue
        args = parse_args(step.input)
        if args is None:
            flush()
            breaks.append({"index": step.index,
                           "reason": "unparseable arguments — independence "
                                     "cannot be checked"})
            continue
        signature = _signature(step)
        if any(m["signature"] == signature for m in current):
            flush()
            breaks.append({"index": step.index,
                           "reason": "duplicate call — a cache case, not a "
                                     "parallel case"})
            # the duplicate may still start a fresh run
            current = [{"step": step, "args": args, "signature": signature}]
            continue
        dependency = None
        for value in sorted(str(v) for v in args.values()):
            text = _norm(value).strip("\"'")
            if len(text) < MIN_PROVENANCE_LEN:
                continue
            for member in current:
                if text in _norm(member["step"].output):
                    dependency = member["step"].index
                    break
            if dependency is not None:
                break
        if dependency is not None:
            flush()
            breaks.append({"index": step.index, "reason":
                           f"argument value found in step {dependency}'s "
                           "output (provenance link)"})
            current = [{"step": step, "args": args, "signature": signature}]
            continue
        current.append({"step": step, "args": args, "signature": signature})
    flush()

    described = []
    total_saving = 0.0
    any_saving = False
    for members in runs:
        latencies = [s.latency_s for s in members]
        measurable = all(lat > 0 for lat in latencies)
        saving = round(sum(latencies) - max(latencies), 4) if measurable else None
        if saving is not None:
            total_saving += saving
            any_saving = True
        described.append({
            "steps": [s.index for s in members],
            "names": [s.name for s in members],
            "latencies_s": latencies,
            "wall_clock_saving_s": saving,
            "saving_reason": (None if saving is not None else
                              "latency unrecorded (0) on at least one step"),
        })
    return {
        "runs": described,
        "count": len(described),
        "total_wall_clock_saving_s": round(total_saving, 4) if any_saving else None,
        "breaks": breaks,
        "method": ("textual and conservative: any provenance link, error, "
                   "duplicate, non-read effect or unparseable arguments "
                   "breaks the run"),
        "assumption": PARALLEL_ASSUMPTION,
    }


# ---------------------------------------------------------------------------
# 4. latency concentration


def _gini(values: list[float]) -> Optional[float]:
    """Gini coefficient of the latency distribution (0 = even, 1 = all in
    one step).  None when nothing was recorded."""
    total = sum(values)
    if total <= 0 or len(values) < 2:
        return None
    ordered = sorted(values)
    n = len(ordered)
    weighted = sum((2 * i - n - 1) * v for i, v in enumerate(ordered, start=1))
    return round(weighted / (n * total), 4) + 0.0   # + 0.0 normalizes -0.0


def latency_concentration(trajectory: Trajectory) -> dict:
    """Where the wall-clock went: the slowest steps, their share, and how
    concentrated the distribution is.

    Shares use the **sum of per-step latencies** as the denominator (named
    here because ``totals.latency_s`` may include harness time the steps do
    not account for).  ``concentrated`` is true when at most
    ``CONCENTRATION_STEPS`` steps hold more than ``CONCENTRATION_SHARE`` of
    that total *and* the run is at least ``MIN_STEPS_FOR_CONCENTRATION``
    steps long — in a three-step run "half the time in two steps" is
    arithmetic, not a finding.  The flagged steps are named.  Unmeasurable
    when no step recorded a latency — absence of timing is not evidence of
    speed.
    """
    latencies = [s.latency_s for s in trajectory.steps]
    total = sum(latencies)
    if total <= 0:
        return {"measurable": False,
                "reason": "no step recorded a latency — unmeasurable, not fast",
                "step_latency_total_s": 0.0, "top": [], "gini": None,
                "concentrated": False, "top_share": None, "note": None}
    ranked = sorted(trajectory.steps, key=lambda s: (-s.latency_s, s.index))
    top = [{"index": s.index, "name": s.name, "latency_s": s.latency_s,
            # denominator: sum of per-step latencies
            "share": round(s.latency_s / total, 4)}
           for s in ranked[:TOP_LATENCY_STEPS]]
    head = ranked[:CONCENTRATION_STEPS]
    top_share = round(sum(s.latency_s for s in head) / total, 4)
    concentrated = (top_share > CONCENTRATION_SHARE
                    and len(trajectory.steps) >= MIN_STEPS_FOR_CONCENTRATION)
    note = None
    if concentrated:
        names = ", ".join(f"step {s.index} ({s.name}, {s.latency_s:g}s)"
                          for s in head)
        note = (f"{top_share:.0%} of wall-clock sits in "
                f"{len(head)} step(s): {names}")
    return {
        "measurable": True,
        "reason": None,
        "step_latency_total_s": round(total, 4),
        "top": top,
        "gini": _gini(latencies),
        "top_share": top_share,
        "concentrated": concentrated,
        "note": note,
    }


# ---------------------------------------------------------------------------
# 5. throughput, where measurable


def throughput(trajectory: Trajectory) -> dict:
    """Tokens per second, computed only where both sides of the division
    are real.

    A step qualifies only when its token count is **measured**
    (``tokens_basis`` declared as measured, on the step or run level) and
    its latency is positive.  Estimated counts are refused, not divided —
    a rate built on a len/4 guess would be an estimate wearing a
    measurement's clothes — and an undeclared basis is refused for the same
    reason: it cannot be verified.  The aggregate is the **median** over
    qualifying steps (denominator: ``steps_measured``).

    The numerator is ``steps[].tokens``, which SCHEMA does not split into
    input/output — stated here because it makes the figure a processing
    rate for the step, not a pure generation rate.  Where v19 model
    telemetry is present its ``source`` values are listed, so a reader
    knows real provider signal exists for these steps.
    """
    rates = []
    excluded = {"estimated": 0, "undeclared": 0, "no_latency": 0, "no_tokens": 0}
    for step in trajectory.steps:
        if step.tokens <= 0:
            excluded["no_tokens"] += 1
            continue
        basis = _step_token_basis(step, trajectory)
        if basis != "measured":
            excluded[basis if basis in excluded else "undeclared"] += 1
            continue
        if step.latency_s <= 0:
            excluded["no_latency"] += 1
            continue
        rates.append({"index": step.index, "name": step.name,
                      "tokens_per_s": round(step.tokens / step.latency_s, 2)})
    sources = sorted({str((s.model or {}).get("source"))
                      for s in trajectory.steps
                      if isinstance(s.model, dict) and s.model.get("source")})
    if rates:
        ordered = sorted(r["tokens_per_s"] for r in rates)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else round(
            (ordered[mid - 1] + ordered[mid]) / 2, 2)
        reason = None
    else:
        median = None
        if excluded["estimated"]:
            reason = "unmeasurable: token counts are estimates"
        elif excluded["undeclared"]:
            reason = ("unmeasurable: token basis undeclared on every step — "
                      "an unlabelled count cannot be verified as a measurement")
        else:
            reason = ("unmeasurable: no step has both a measured token count "
                      "and a positive latency")
    return {
        "median_tokens_per_s": median,
        "steps_measured": len(rates),   # denominator of the median
        "per_step": rates,
        "steps_excluded": excluded,
        "reason": reason,
        "numerator_note": ("steps[].tokens — SCHEMA does not split step "
                           "tokens into input/output, so this is a "
                           "processing rate, not a pure generation rate"),
        "model_telemetry_sources": sources,
    }


# ---------------------------------------------------------------------------
# 7. the ranked opportunities list


def _rank_key(entry: dict):
    saving = entry["saving"]
    return (-(saving["tokens"] or 0), -(saving["latency_s"] or 0),
            entry["kind"], entry["evidence"].get("steps", [0]) or [0])


def _opportunities(trajectory: Trajectory, parts: dict) -> list[dict]:
    """Merge every analysis into one ranked list of things to change.

    Each entry: ``kind``, ``evidence`` (indices and counts), ``saving``
    ({tokens, latency_s, cost_usd} — each ``None`` when not estimable, with
    the reason in ``saving_notes``), ``basis``
    (measured / estimated / mixed), ``assumption`` (the ceiling being
    assumed), and an imperative one-line ``action``.  Ranked by recoverable
    tokens, then recoverable latency; a diagnosis with no estimable saving
    ranks last.  Cost savings are derived from the run's own observed
    cost-per-token rate and are therefore always estimates.

    Savings of different kinds can overlap — a call absorbed by a result
    cache also vanishes from a parallel run — so entries are ceilings
    individually and must not be summed across kinds.
    """
    rate = _cost_rate(trajectory)
    cost_note = (None if rate is not None else
                 "cost_usd or token totals unrecorded — unrecorded ≠ free")
    entries: list[dict] = []

    growth = parts["context_growth"]
    absorbable = growth.get("prompt_cache_absorbable_tokens")
    if (growth["measurable"] and absorbable
            and absorbable >= RESEND_TOKEN_FLOOR
            and growth["resend_share"] >= RESEND_SHARE_FLOOR):
        entries.append({
            "kind": "prompt_cache",
            "evidence": {"turns": growth["turns"],
                         "input_tokens": growth["input_tokens"],
                         "unique_content_estimate": growth["unique_content_estimate"],
                         "resend_share": growth["resend_share"]},
            "saving": {"tokens": absorbable,
                       "latency_s": None,
                       "cost_usd": _tokens_to_cost(absorbable, rate)},
            "saving_notes": {
                "latency_s": "prompt caching cuts token cost; its latency "
                             "effect is provider-dependent and not estimable "
                             "from this log",
                **({"cost_usd": cost_note} if rate is None else {}),
            },
            "basis": "estimated",
            "assumption": PREFIX_ASSUMPTION,
            "action": (f"Cache the conversation prefix — ~{absorbable} of "
                       f"{growth['input_tokens']} input tokens "
                       f"({growth['resend_share']:.0%}) are re-sent context a "
                       "stable-prefix prompt cache could absorb (estimated "
                       "ceiling)"),
        })

    cache = parts["result_cache"]
    if cache["count"]:
        by_tool: dict[str, list[dict]] = {}
        for repeat in cache["cacheable_repeats"]:
            by_tool.setdefault(repeat["name"], []).append(repeat)
        for name in sorted(by_tool):
            group = by_tool[name]
            token_parts = [g["tokens"] for g in group if g["tokens"] is not None]
            latency_parts = [g["latency_s"] for g in group if g["latency_s"] is not None]
            tokens_saving = sum(token_parts) if token_parts else None
            latency_saving = round(sum(latency_parts), 4) if latency_parts else None
            token_basis = _merge_basis(
                [g["tokens_basis"] for g in group if g["tokens"] is not None])
            bases = ([token_basis] if token_basis else []) + \
                    (["measured"] if latency_parts else []) + \
                    (["estimated"] if _tokens_to_cost(tokens_saving, rate) is not None else [])
            calls = len(group) + 1   # repeats plus the first call
            pieces = []
            if tokens_saving is not None:
                pieces.append(f"{tokens_saving} tokens")
            if latency_saving is not None:
                pieces.append(f"{latency_saving:g}s")
            recoverable = " and ".join(pieces) if pieces else "an unrecorded amount"
            entries.append({
                "kind": "result_cache",
                "evidence": {"tool": name,
                             "steps": [g["index"] for g in group],
                             "first_seen": min(g["first_seen"] for g in group),
                             "calls": calls},
                "saving": {"tokens": tokens_saving,
                           "latency_s": latency_saving,
                           "cost_usd": _tokens_to_cost(tokens_saving, rate)},
                "saving_notes": {
                    **({"tokens": "step token counts unrecorded (0) — "
                                  "unrecorded ≠ free"}
                       if tokens_saving is None else {}),
                    **({"latency_s": "step latency unrecorded (0)"}
                       if latency_saving is None else {}),
                    **({"cost_usd": cost_note}
                       if _tokens_to_cost(tokens_saving, rate) is None else {}),
                },
                "basis": _merge_basis(bases) or "estimated",
                "assumption": CACHE_ASSUMPTION,
                "action": (f"Cache {name} — called {calls}× with identical "
                           f"arguments and results, {recoverable} recoverable"),
            })

    for run in parts["parallel_reads"]["runs"]:
        saving = run["wall_clock_saving_s"]
        entries.append({
            "kind": "parallel_reads",
            "evidence": {"steps": run["steps"], "names": run["names"],
                         "reads": len(run["steps"])},
            "saving": {"tokens": None, "latency_s": saving, "cost_usd": None},
            "saving_notes": {
                "tokens": "parallel issue does not change what is sent; "
                          "no token saving",
                "cost_usd": "cost follows tokens, which are unchanged",
                **({"latency_s": run["saving_reason"]} if saving is None else {}),
            },
            "basis": "measured" if saving is not None else "estimated",
            "assumption": PARALLEL_ASSUMPTION,
            "action": (f"Issue steps {', '.join(str(i) for i in run['steps'])} "
                       f"({len(run['steps'])} independent reads) concurrently — "
                       + (f"{saving:g}s of wall-clock recoverable "
                          "(sum − max of their latencies; textual, "
                          "conservative independence check)"
                          if saving is not None else
                          "latency unrecorded, so the saving is not estimable")),
        })

    latency = parts["latency"]
    if latency["measurable"] and latency["concentrated"]:
        head = latency["top"][:CONCENTRATION_STEPS]
        entries.append({
            "kind": "latency_hotspot",
            "evidence": {"steps": [t["index"] for t in head],
                         "names": [t["name"] for t in head],
                         "top_share": latency["top_share"],
                         "step_latency_total_s": latency["step_latency_total_s"]},
            "saving": {"tokens": None, "latency_s": None, "cost_usd": None},
            "saving_notes": {
                "tokens": "concentration is a diagnosis, not a token saving",
                "latency_s": "recoverable time depends on why these steps "
                             "are slow, which the log does not say",
                "cost_usd": "not estimable without a latency saving",
            },
            "basis": "measured",
            "assumption": None,
            "action": ("Investigate " + " and ".join(
                f"step {t['index']} ({t['name']}, {t['latency_s']:g}s)"
                for t in head)
                + f" — {latency['top_share']:.0%} of wall-clock sits in "
                  f"≤{CONCENTRATION_STEPS} steps"),
        })

    entries.sort(key=_rank_key)
    for rank, entry in enumerate(entries, start=1):
        entry["rank"] = rank
    return entries


# ---------------------------------------------------------------------------
# per-run and pairwise entry points


def analyse(trajectory: Trajectory) -> dict:
    """Every efficiency analysis for one run, plus the ranked opportunities."""
    parts = {
        "agent": trajectory.agent.name,
        "context_growth": context_growth(trajectory),
        "result_cache": result_cache(trajectory),
        "parallel_reads": parallel_reads(trajectory),
        "latency": latency_concentration(trajectory),
        "throughput": throughput(trajectory),
    }
    parts["opportunities"] = _opportunities(trajectory, parts)
    return parts


def _side_phrase(side: dict) -> str:
    ops = side["opportunities"]
    if not ops:
        return f"{side['agent']}: no optimization opportunity visible"
    top = ops[0]
    return f"{side['agent']}: {top['action'][0].lower()}{top['action'][1:]}"


def compare_efficiency(a: Trajectory, b: Trajectory) -> dict:
    """Efficiency analysis for both sides of a pairwise report.

    Per-side blocks are independent — nothing here compares the runs to
    each other, because an optimization opportunity belongs to one run
    regardless of what the other did.  The narrative just puts the two
    top findings side by side.
    """
    left, right = analyse(a), analyse(b)
    return {
        "a": left,
        "b": right,
        "narrative": _side_phrase(left) + ". " + _side_phrase(right) + ".",
    }


# ---------------------------------------------------------------------------
# 6 (cost per success) + batch rollup


def _cost_per_success(reports: list[dict], side: str) -> dict:
    """Corpus-level cost per success for one side.

    Numerator: total ``cost_usd`` over the side's runs; denominator: the
    count of successful runs (both stated in the output).  Unmeasurable —
    ``None`` with a reason, never 0 — when cost is 0 on every run
    (unrecorded ≠ free) or when there are no successes (a zero
    denominator is not a bargain).
    """
    costs = [float(r[side]["totals"]["cost_usd"]) for r in reports]
    successes = sum(1 for r in reports if r[side]["outcome"]["success"])
    total = round(sum(costs), 6)
    if not any(c > 0 for c in costs):
        value, reason = None, ("cost_usd is 0 on every run — cost was not "
                               "recorded, and unrecorded ≠ free")
    elif successes == 0:
        value, reason = None, ("no successful runs — the ratio has a zero "
                               "denominator")
    else:
        value, reason = round(total / successes, 6), None
    return {"value_usd": value, "total_cost_usd": total,
            "successes": successes, "runs": len(reports), "reason": reason}


#: per-kind imperative templates for the aggregate opportunity rollup.
_KIND_ACTIONS = {
    "prompt_cache": "Add a stable-prefix prompt cache",
    "result_cache": "Add a tool result cache",
    "parallel_reads": "Issue independent reads concurrently",
    "latency_hotspot": "Investigate latency hotspots",
}


def _rollup_opportunities(entries: list[tuple[str, dict]]) -> list[dict]:
    """Group per-task opportunities by kind and sum what is summable.

    A ``None`` component stays ``None`` only when *no* occurrence produced
    a number; otherwise the sum covers the occurrences that did, and
    ``occurrences_estimated`` says how many contributed.
    """
    grouped: dict[str, list[tuple[str, dict]]] = {}
    for task, entry in entries:
        grouped.setdefault(entry["kind"], []).append((task, entry))
    rolled = []
    for kind in sorted(grouped):
        rows = grouped[kind]
        tasks = sorted({task for task, _ in rows})
        saving = {}
        contributed = {}
        for key in ("tokens", "latency_s", "cost_usd"):
            values = [e["saving"][key] for _, e in rows
                      if e["saving"][key] is not None]
            saving[key] = round(sum(values), 6) if values else None
            contributed[key] = len(values)
        bases = [e["basis"] for _, e in rows]
        pieces = []
        if saving["tokens"] is not None:
            pieces.append(f"{saving['tokens']:g} tokens")
        if saving["latency_s"] is not None:
            pieces.append(f"{saving['latency_s']:g}s")
        if saving["cost_usd"] is not None:
            pieces.append(f"${saving['cost_usd']:g}")
        recoverable = (" and ".join(pieces) + " recoverable (ceiling)"
                       if pieces else "no estimable saving")
        rolled.append({
            "kind": kind,
            "evidence": {"occurrences": len(rows), "tasks": tasks,
                         "occurrences_estimated": contributed},
            "saving": saving,
            "basis": _merge_basis(bases),
            "action": (f"{_KIND_ACTIONS.get(kind, 'Optimize ' + kind)} — "
                       f"{len(rows)} occurrence(s) across {len(tasks)} "
                       f"task(s), {recoverable}"),
        })
    rolled.sort(key=lambda e: (-(e["saving"]["tokens"] or 0),
                               -(e["saving"]["latency_s"] or 0), e["kind"]))
    for rank, entry in enumerate(rolled, start=1):
        entry["rank"] = rank
    return rolled


def aggregate_efficiency(reports: list[dict]) -> dict:
    """Roll the per-report efficiency blocks up per agent.

    Reads ``report["efficiency"]`` where present (reports built before this
    module lack it and are counted in ``reports_missing_efficiency`` rather
    than silently skipped).  Cost per success is computed from the reports'
    own totals, so it covers every report either way.
    """
    if not reports:
        return {"tasks": 0, "agents": {"a": None, "b": None},
                "per_agent": {"a": None, "b": None},
                "reports_missing_efficiency": 0, "narrative": None}
    names = {"a": reports[0]["a"]["agent"]["name"],
             "b": reports[0]["b"]["agent"]["name"]}
    missing = sum(1 for r in reports if "efficiency" not in r)
    per_agent = {}
    for side in ("a", "b"):
        entries: list[tuple[str, dict]] = []
        resend: list[int] = []
        cacheable = 0
        for report in reports:
            block = report.get("efficiency")
            if not block:
                continue
            side_block = block[side]
            for entry in side_block["opportunities"]:
                entries.append((report["task"]["id"], entry))
            overhead = side_block["context_growth"].get("prompt_cache_absorbable_tokens")
            if overhead is not None:
                resend.append(overhead)
            cacheable += side_block["result_cache"]["count"]
        per_agent[side] = {
            "agent": names[side],
            "cost_per_success": _cost_per_success(reports, side),
            "opportunities": _rollup_opportunities(entries),
            "resend_overhead_tokens": sum(resend) if resend else None,
            "cacheable_repeat_calls": cacheable,
        }
    lines = []
    for side in ("a", "b"):
        ops = per_agent[side]["opportunities"]
        if ops:
            lines.append(f"{names[side]}: {ops[0]['action'][0].lower()}"
                         f"{ops[0]['action'][1:]}")
        else:
            lines.append(f"{names[side]}: no optimization opportunity visible")
    return {
        "tasks": len(reports),
        "agents": names,
        "per_agent": per_agent,
        "reports_missing_efficiency": missing,
        "narrative": ". ".join(lines) + ".",
    }
