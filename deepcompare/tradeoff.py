"""The speed-quality exchange, stated instead of implied (v25).

Every comparison ends with the same unasked question: one agent was faster
or cheaper, the other was better — so which do I want?  The report leaves
that arithmetic to the reader, and readers do it badly under a deadline:
"46% faster" sticks, "and wrong" slides.

This module does the exchange explicitly, with the three honest cases kept
apart:

* **Dominance.**  One side is better on outcome *and* no worse on spend.
  There is no trade-off; naming a fake one would manufacture a dilemma.
* **A price for correctness.**  One side is right and spent more.  The
  statement is the price: what the correct answer cost in extra seconds,
  tokens and dollars.  "Faster" is not a virtue in the run that failed —
  reaching the wrong answer sooner is not a capability — and the framing
  must not let speed-but-wrong read as a strength.
* **A genuine exchange.**  Both succeed and quality scores differ, or both
  succeed and only spend differs.  Only here are exchange rates (score
  points per dollar, per second) meaningful, and they are emitted with the
  caveat a single task deserves: one observation, no interval, descriptive
  only.  Batch-level rates belong to the aggregate, where the fleet module
  already owns the Pareto frontier.
"""

from __future__ import annotations

from typing import Optional


def _spend(side: dict) -> dict:
    totals = side.get("totals") or {}
    return {
        "tokens": (totals.get("input_tokens") or 0) + (totals.get("output_tokens") or 0),
        "cost_usd": totals.get("cost_usd") or 0.0,
        "latency_s": totals.get("latency_s") or 0.0,
        "steps": len(side.get("steps") or []),
    }


def _score(side: dict) -> Optional[float]:
    score = (side.get("outcome") or {}).get("score")
    return float(score) if isinstance(score, (int, float)) else None


def pair_tradeoff(report: dict) -> dict:
    """The exchange between the two sides of one comparison report."""
    a, b = report.get("a") or {}, report.get("b") or {}
    name_a = ((a.get("agent") or {}).get("name")) or "A"
    name_b = ((b.get("agent") or {}).get("name")) or "B"
    ok_a = bool((a.get("outcome") or {}).get("success"))
    ok_b = bool((b.get("outcome") or {}).get("success"))
    spend_a, spend_b = _spend(a), _spend(b)

    cheaper = {key: name_a if spend_a[key] <= spend_b[key] else name_b
               for key in spend_a}
    delta = {key: round(spend_b[key] - spend_a[key], 4) for key in spend_a}

    base = {
        "agents": {"a": name_a, "b": name_b},
        "success": {"a": ok_a, "b": ok_b},
        "spend": {"a": spend_a, "b": spend_b},
        "spend_delta_b_minus_a": delta,
        "caveat": ("one task, one run per side: every figure here is "
                   "descriptive of this pair only, with no interval — "
                   "batch-level exchange rates live in the aggregate"),
    }

    if ok_a != ok_b:
        winner, loser = (name_a, name_b) if ok_a else (name_b, name_a)
        w_spend = spend_a if ok_a else spend_b
        l_spend = spend_b if ok_a else spend_a
        extra = {key: round(w_spend[key] - l_spend[key], 4) for key in w_spend}
        if all(v <= 0 for v in extra.values()):
            base.update({
                "case": "dominance",
                "dominant": winner,
                "statement": (
                    f"{winner} is right and spends no more than {loser} on any "
                    f"axis. There is no trade-off to weigh: {loser} is slower "
                    "to nothing."),
            })
        else:
            costs = [f"{extra['latency_s']:+.2f}s" if extra["latency_s"] > 0 else None,
                     f"{extra['tokens']:+,} tokens" if extra["tokens"] > 0 else None,
                     f"${extra['cost_usd']:+.4f}" if extra["cost_usd"] > 0 else None]
            costs = [c for c in costs if c]
            base.update({
                "case": "price_of_correctness",
                "dominant": winner,
                "price_of_correctness": {k: v for k, v in extra.items() if v > 0},
                "statement": (
                    f"{winner} is right; {loser} is not. The correct answer cost "
                    + (", ".join(costs) if costs else "nothing extra")
                    + f" over {loser}'s run. {loser} reaching its answer "
                    f"{'faster' if extra['latency_s'] > 0 else 'differently'} is "
                    "not a saving — it is the same task still unsolved."),
            })
        return base

    if not ok_a and not ok_b:
        cheaper_side = cheaper["cost_usd"]
        base.update({
            "case": "both_failed",
            "dominant": None,
            "statement": (
                f"Both failed, so there is no quality to trade against. "
                f"{cheaper_side} failed more cheaply, which matters only for "
                "the retry budget."),
        })
        return base

    score_a, score_b = _score(a), _score(b)
    exchange = {}
    if (score_a is not None and score_b is not None
            and abs(score_b - score_a) > 1e-9):
        quality = score_b - score_a
        better = name_b if quality > 0 else name_a
        rates = {}
        for key, unit in (("cost_usd", "score per dollar"),
                          ("latency_s", "score per second"),
                          ("tokens", "score per 1k tokens")):
            denom = delta[key] if key != "tokens" else delta[key] / 1000.0
            # A rate only means something when quality and spend moved in
            # the same direction on the same side — paying less for more
            # is dominance, not an exchange.
            if abs(denom) > 1e-9 and (denom > 0) == (quality > 0):
                rates[unit] = round(abs(quality / denom), 4)
        exchange = {"quality_delta_b_minus_a": round(quality, 4), "rates": rates}
        if rates:
            rate_bits = ", ".join(f"{v:g} {k}" for k, v in rates.items())
            statement = (
                f"Both succeed; {better} scores {abs(quality):.2f} higher and "
                f"pays for it. The exchange on this task: {rate_bits}. Whether "
                "that is worth it is a product decision, not a metric.")
        else:
            statement = (
                f"Both succeed and {better} scores {abs(quality):.2f} higher "
                f"while also spending less — dominance, not a trade-off.")
            base["dominant"] = better
        base.update({"case": "quality_for_spend", "exchange": exchange,
                     "statement": statement})
        return base

    axes = [key for key in delta if abs(delta[key]) > 1e-9]
    if not axes:
        base.update({"case": "equivalent", "dominant": None,
                     "statement": "Both succeed with equal recorded spend — "
                                  "nothing to trade on this task."})
        return base
    saver = cheaper["cost_usd"] if abs(delta["cost_usd"]) > 1e-9 else cheaper["latency_s"]
    savings = []
    for key, suffix in (("latency_s", "s"), ("tokens", " tokens"),
                        ("cost_usd", " USD")):
        if abs(delta[key]) > 1e-9:
            value = abs(delta[key])
            savings.append(f"{value:g}{suffix}")
    base.update({
        "case": "equal_outcome_cheaper_run",
        "dominant": saver,
        "statement": (
            f"Both succeed with the same recorded quality; {saver} gets there "
            f"for less ({', '.join(savings)} difference). With outcomes equal, "
            "spend is the whole decision — on this task."),
    })
    return base
