"""Real model telemetry from token logprobs (v19).

:mod:`deepcompare.uncertainty` asks whether the model knew it was going
wrong, and that question needs per-token probabilities.  The demo supplies
them synthetically; this module derives them from what an inference server
actually returns.

Open-weight models are the strongest case for this analysis, not the weakest.
A self-hosted vLLM, TGI, llama.cpp or Ollama server will hand back logprobs
for every generated token — often with the full top-k distribution — because
there is no reason to withhold them.  Hosted APIs return the same shape when
asked (OpenAI's ``logprobs``/``top_logprobs``), so the mapping here is one
code path for both, and the richer the logprobs, the better the entropy
estimate:

* with **top-k logprobs**, entropy is computed over the returned distribution
  (approximate, since the tail is truncated, and marked as such);
* with **only the chosen token's logprob**, entropy falls back to the binary
  entropy of that probability, which is a floor rather than an estimate.

Nothing is invented.  When a payload carries no logprobs the functions return
None and the trajectory simply has no telemetry — which every consumer
already handles — rather than a fabricated confidence.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

#: below this probability a token is treated as a genuinely uncertain choice.
LOW_TOKEN = 0.5


def _probability(entry: Any) -> Optional[float]:
    """The chosen token's probability from one logprob entry.

    Accepts the shapes servers actually emit: ``{"logprob": -0.3}`` (OpenAI,
    vLLM, TGI), a bare float, or ``{"prob": 0.74}``.
    """
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return math.exp(float(entry)) if entry <= 0 else None
    if not isinstance(entry, dict):
        return None
    if "prob" in entry and isinstance(entry["prob"], (int, float)):
        value = float(entry["prob"])
        return value if 0.0 <= value <= 1.0 else None
    logprob = entry.get("logprob", entry.get("log_prob"))
    if isinstance(logprob, (int, float)) and not isinstance(logprob, bool):
        # Guard against absurd values before exp() underflows to zero.
        return math.exp(max(float(logprob), -60.0))
    return None


def _top_entropy(entry: Any) -> Optional[float]:
    """Entropy in nats over a returned top-k distribution, if present."""
    if not isinstance(entry, dict):
        return None
    top = entry.get("top_logprobs") or entry.get("top_probs")
    if not isinstance(top, (list, tuple)) or not top:
        return None
    probabilities: list[float] = []
    for candidate in top:
        value = _probability(candidate)
        if value is not None and value > 0:
            probabilities.append(value)
    if not probabilities:
        return None
    total = sum(probabilities)
    if total <= 0:
        return None
    # Renormalise: the returned top-k is a truncated distribution.
    return -sum((p / total) * math.log(p / total) for p in probabilities)


def _binary_entropy(probability: float) -> float:
    """Entropy of the chosen-vs-everything-else split, in nats.

    A floor, not an estimate: without the distribution this is the least
    uncertainty consistent with the observed probability.
    """
    p = min(max(probability, 1e-9), 1 - 1e-9)
    return -(p * math.log(p) + (1 - p) * math.log(1 - p))


def telemetry_from_logprobs(
    logprobs: Iterable[Any],
    temperature: Optional[float] = None,
    source: str = "provider-logprobs",
) -> Optional[dict]:
    """Build a SCHEMA ``model`` block from a token logprob sequence.

    Returns None when nothing usable is present, so the caller records no
    telemetry rather than a made-up number.
    """
    entries = list(logprobs or [])
    if not entries:
        return None

    probabilities: list[float] = []
    entropies: list[float] = []
    exact_entropy = False
    for entry in entries:
        probability = _probability(entry)
        if probability is None:
            continue
        probabilities.append(probability)
        top = _top_entropy(entry)
        if top is not None:
            entropies.append(top)
            exact_entropy = True
        else:
            entropies.append(_binary_entropy(probability))

    if not probabilities:
        return None

    mean_probability = sum(probabilities) / len(probabilities)
    block = {
        "confidence": round(min(1.0, max(0.0, mean_probability)), 4),
        "min_token_confidence": round(min(probabilities), 4),
        "entropy": round(sum(entropies) / len(entropies), 4),
        "tokens_scored": len(probabilities),
        "low_confidence_tokens": sum(1 for p in probabilities if p < LOW_TOKEN),
        "entropy_basis": "top_k" if exact_entropy else "binary_floor",
        "source": source,
    }
    if temperature is not None:
        block["temperature"] = float(temperature)
    return block


def extract_logprobs(payload: Any) -> Optional[list]:
    """Find a token logprob list inside a server response.

    Handles the layouts in circulation: OpenAI/vLLM/TGI
    ``choices[0].logprobs.content[]``, a bare ``logprobs.content``, TGI's
    ``details.tokens``, and a plain list.
    """
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload or None
    if not isinstance(payload, dict):
        return None

    # The payload may already BE the logprobs object ({"content": [...]}),
    # which is what recursing through choices[0].logprobs hands back.
    for key in ("content", "tokens"):
        direct = payload.get(key)
        if isinstance(direct, list) and direct:
            first = direct[0]
            if isinstance(first, dict) and (
                "logprob" in first or "log_prob" in first or "prob" in first
            ):
                return direct

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            nested = extract_logprobs(first.get("logprobs"))
            if nested:
                return nested

    logprobs = payload.get("logprobs")
    if isinstance(logprobs, dict):
        content = logprobs.get("content") or logprobs.get("tokens")
        if isinstance(content, list) and content:
            return content
    if isinstance(logprobs, list) and logprobs:
        return logprobs

    # TGI / llama.cpp style
    details = payload.get("details")
    if isinstance(details, dict):
        tokens = details.get("tokens")
        if isinstance(tokens, list) and tokens:
            return tokens
    tokens = payload.get("tokens")
    if isinstance(tokens, list) and tokens and isinstance(tokens[0], dict):
        if "logprob" in tokens[0] or "log_prob" in tokens[0]:
            return tokens
    return None


def attach_telemetry(step: dict, payload: Any,
                     temperature: Optional[float] = None,
                     source: str = "provider-logprobs") -> dict:
    """Attach a ``model`` block to a step dict when logprobs are available.

    Returns the same step, mutated, so it can be used inline in adapters.
    """
    entries = extract_logprobs(payload)
    if not entries:
        return step
    block = telemetry_from_logprobs(entries, temperature=temperature,
                                    source=source)
    if block:
        step["model"] = block
    return step
