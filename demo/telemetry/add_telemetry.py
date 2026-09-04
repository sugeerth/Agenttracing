"""Augment the flagship demo traces with synthetic model telemetry.

SYNTHETIC DATA — clearly labelled.  Real telemetry comes from the provider:
per-token logprobs (OpenAI ``logprobs``, Anthropic streaming token data,
vLLM/TGI ``logprobs`` for open-weight models) averaged over the text a step
generated.  Nothing here estimates or infers confidence from the text; it is
generated to exercise the analysis, and the numbers are not evidence of
anything about the real models named in the demo.

The two agents are given deliberately opposite uncertainty behaviour, because
that contrast is the point of the analysis:

``atlas-v2`` — **flagged failures.**  Its confidence collapses on the step
    that goes wrong (the malformed regex on t07), so a runtime confidence
    gate would have caught the run.

``bolt-v3`` — **silent failures.**  It is as confident on the bad blog source
    as on the official filing.  No threshold on its own uncertainty would
    have saved it; only an external verification step would.

Run from the repo root::

    python demo/telemetry/add_telemetry.py

Reads ``demo/traces/`` and writes augmented copies to
``demo/telemetry/traces/``.  Deterministic and idempotent.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = REPO_ROOT / "demo" / "traces"
DEST = Path(__file__).resolve().parent / "traces"

#: per-agent uncertainty behaviour.
PROFILES = {
    "atlas-v2": {
        "baseline": 0.91,
        "jitter": 0.03,
        # Confidence on steps annotated weak/bad: this agent shows its doubt.
        "weak": 0.74,
        "bad": 0.48,
        "temperature": 0.2,
    },
    "bolt-v3": {
        "baseline": 0.88,
        "jitter": 0.04,
        # This agent stays confident while going wrong — the silent class.
        "weak": 0.86,
        "bad": 0.85,
        "temperature": 0.7,
    },
}

DEFAULT_PROFILE = {"baseline": 0.87, "jitter": 0.04, "weak": 0.78,
                   "bad": 0.62, "temperature": 0.5}


def confidence_for(profile: dict, quality: str | None, rng: random.Random) -> float:
    if quality == "bad":
        base = profile["bad"]
    elif quality == "weak":
        base = profile["weak"]
    else:
        base = profile["baseline"]
    value = base + rng.uniform(-profile["jitter"], profile["jitter"])
    return max(0.01, min(0.999, value))


def interval_for(model: dict, rng: random.Random) -> dict:
    """A synthetic 95% interval around the synthetic confidence: wider on
    the low-confidence steps, as real token spreads are."""
    c = model["confidence"]
    half = round(min(0.2, 0.02 + (1 - c) * 0.35 + rng.random() * 0.02), 4)
    return {"low": round(max(0.0, c - half), 4), "high": round(min(1.0, c + half), 4),
            "n": model["tokens_scored"],
            "basis": "SYNTHETIC: a band around the synthetic confidence, for the demo"}


#: a small synthetic feature vocabulary.  These are NOT Neuronpedia
#: features: the indices are arbitrary, the labels invented, and every
#: record says so — they exist so the page can show what recorded
#: internals look like.  Real runs get real features through
#: deepcompare.harness.neuronpedia.
FEATURE_POOL = {
    "plan": [(1040, "task framing / enumerating sub-goals"), (2211, "instruction following")],
    "search": [(3312, "web lookup intent"), (4470, "named-entity retrieval")],
    "retrieve": [(4470, "named-entity retrieval"), (5183, "quoting a source")],
    "read": [(5183, "quoting a source"), (6021, "table / number extraction")],
    "tool_call": [(6021, "table / number extraction"), (7332, "arithmetic on units")],
    "reason": [(8110, "self-check / verification"), (9045, "hedging language")],
    "answer": [(9902, "final answer commitment"), (2211, "instruction following")],
}
FAULT_FEATURES = {
    "weak": (11711, "unverified assumption"),
    "bad": (12480, "unit / time-zone confusion"),
}


def internals_for(step: dict, agent: str, task: str, rng: random.Random) -> dict:
    kind = step.get("type") or "reason"
    picks = list(FEATURE_POOL.get(kind, FEATURE_POOL["reason"]))
    features = []
    for index, label in picks:
        features.append({"index": index, "activation": round(2.0 + rng.random() * 6.0, 3),
                         "max_activation": 12.0, "label": label + " (synthetic label)",
                         "tokens": [], "url": None})
    quality = step.get("quality")
    if quality in FAULT_FEATURES:
        index, label = FAULT_FEATURES[quality]
        features.append({"index": index, "activation": round(6.0 + rng.random() * 5.0, 3),
                         "max_activation": 12.0, "label": label + " (synthetic label)",
                         "tokens": [], "url": None})
    features.sort(key=lambda f: -f["activation"])
    return {"model": "synthetic-demo", "sae": "synthetic-sae", "source": "synthetic-demo",
            "features": features,
            "note": "SYNTHETIC internals: invented feature labels to exercise the view; "
                    "real runs record Neuronpedia features"}


def telemetry_for(step: dict, profile: dict, rng: random.Random) -> dict:
    confidence = confidence_for(profile, step.get("quality"), rng)
    # The weakest token in a step sits below its mean; the gap widens as the
    # step gets shakier, which is what a real logprob distribution looks like.
    spread = 0.18 + (1.0 - confidence) * 0.45
    floor = max(0.01, confidence - spread * rng.uniform(0.6, 1.0))
    # Entropy in nats, loosely anti-correlated with confidence.
    entropy = round((1.0 - confidence) * 2.2 + rng.uniform(0.0, 0.15), 4)
    tokens_scored = max(1, int(step.get("tokens", 0) * 0.55))
    return {
        "confidence": round(confidence, 4),
        "min_token_confidence": round(floor, 4),
        "entropy": entropy,
        "tokens_scored": tokens_scored,
        "temperature": profile["temperature"],
        "source": "synthetic-demo",
    }


def main() -> int:
    if not SOURCE.is_dir():
        print(f"error: {SOURCE} not found; run python demo/generate.py first")
        return 1
    DEST.mkdir(parents=True, exist_ok=True)

    written = 0
    for path in sorted(SOURCE.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        agent = data["agent"]["name"]
        profile = PROFILES.get(agent, DEFAULT_PROFILE)
        for step in data["steps"]:
            rng = random.Random(f"telemetry|{agent}|{data['task']['id']}|{step['index']}")
            step["model"] = telemetry_for(step, profile, rng)
            step["model"]["interval"] = interval_for(step["model"], rng)
            step["model"]["internals"] = internals_for(step, agent, data["task"]["id"], rng)
        out_path = DEST / path.name
        out_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written += 1
        scored = [s["model"]["confidence"] for s in data["steps"]]
        print(f"wrote {out_path.name}  agent={agent:<10} "
              f"mean_conf={sum(scored) / len(scored):.2f}  low={min(scored):.2f}")

    print(f"\n{written} traces written to {DEST} (synthetic telemetry)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
