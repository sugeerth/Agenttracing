"""An external proposer for the co-evolving eval: a model asked for
candidate metrics in the eval's own language.

The engine's probes are deterministic and pure; a model can see a
blind spot they cannot name. This seam lets one propose: it is handed
the engine's brief for a step (:func:`deepcompare.coevolve.proposal_brief`
— the feature vocabulary with its bases, the spec language, the step's
diff summary and base verdict, the metrics adopted so far, the
per-feature shifts; no episode text), asked for JSON only, and its reply
is parsed by :func:`deepcompare.coevolve.parse_spec` against the same
vocabulary. What parses becomes a candidate with origin ``{"probe":
"external", "source": <provider name>}`` and goes through the same
validators as every other candidate; what does not parse is returned as
``{"rejected": reason}`` so the ledger records the refusal. The proposer
can never set a number, a verdict or an exit code.

The provider is one of :mod:`deepcompare.harness.providers` — a network
endpoint or a :class:`~deepcompare.harness.providers.ScriptedProvider`
for tests — so this lives in the harness, the one place that talks to a
network; the engine never imports it (``tests/test_harness.py`` pins
that boundary). No retries: a provider error is one rejected row naming
the error, which never contains a key.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..coevolve import AGGS, DIRECTIONS, OPS, parse_spec
from .providers import Provider, ProviderError

INSTRUCTION = (
    "You propose candidate metrics for an evaluation of a self-evolving agent. You are given the feature "
    "vocabulary (each feature a count, a sum, a 1/0 or a ratio over one episode's recorded steps), the step's "
    "diff summary and the base verdict, the metrics already adopted, and how each feature shifted from the "
    "parent generation to the child. Propose at most {k} metrics that would read what the adopted metrics miss. "
    "Each metric is a JSON object {{\"id\", \"name\", \"feature\", \"agg\", \"where\", \"direction\", \"why\"}}: "
    "\"feature\" must be one of the vocabulary ids; \"agg\" one of {aggs}; \"where\" null, "
    "{{\"feature\", \"op\", \"value\"}} with op one of {ops}, or {{\"all\": [predicates]}}; \"direction\" one of "
    "{directions}. Reply with a JSON array only, no prose."
)


def _prompt(brief: dict, k: int) -> list:
    system = INSTRUCTION.format(k=k, aggs=", ".join(AGGS), ops=", ".join(OPS), directions=", ".join(DIRECTIONS))
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(brief, ensure_ascii=False, sort_keys=True)}]


def _parse_reply(text: str) -> Optional[list]:
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _source(provider: Provider) -> str:
    name = getattr(provider, "name", None)
    if callable(name):
        name = name()
    return str(name or getattr(provider, "kind", "provider"))


def propose(provider: Provider, brief: dict, *, k: int = 3) -> list:
    """Ask ``provider`` for at most ``k`` candidate metrics for the step
    ``brief`` describes. Returns one row per proposal: ``{"spec": <parsed
    spec>, "why": str, "origin": {"probe": "external", "source": name}}``
    for one that parses, ``{"rejected": reason, "raw": entry, "origin":
    ...}`` for one that does not; a reply that is not the JSON asked for,
    or a provider error, is one rejected row. Never retries."""
    origin = {"probe": "external", "source": _source(provider)}
    vocabulary = [f["id"] for f in (brief.get("features") or []) if isinstance(f, dict) and f.get("id")]
    try:
        response = provider.complete(_prompt(brief, k), None)
    except ProviderError as exc:
        return [{"rejected": f"provider error: {exc}", "raw": None, "origin": dict(origin)}]
    text = getattr(response, "text", "") or ""
    entries = _parse_reply(text)
    if entries is None:
        return [{"rejected": "the proposer did not return the JSON array asked for", "raw": text[:300],
                 "origin": dict(origin)}]
    out = []
    for i, entry in enumerate(entries):
        if i >= k:
            out.append({"rejected": f"beyond the {k} candidates asked for", "raw": entry, "origin": dict(origin)})
            continue
        if not isinstance(entry, dict):
            out.append({"rejected": f"not an object: {json.dumps(entry)[:80]}", "raw": entry, "origin": dict(origin)})
            continue
        try:
            spec = parse_spec({key: entry.get(key) for key in ("id", "name", "feature", "agg", "where", "direction")
                               if key in entry}, vocabulary or None)
        except ValueError as exc:
            out.append({"rejected": str(exc), "raw": entry, "origin": dict(origin)})
            continue
        spec["origin"] = dict(origin)
        out.append({"spec": spec, "why": str(entry.get("why", ""))[:600], "origin": dict(origin)})
    return out


__all__ = ["INSTRUCTION", "propose"]
