"""Model internals through Neuronpedia — feature activations per step.

Neuronpedia (neuronpedia.org) hosts sparse-autoencoder feature
dashboards for open-weight models: every feature has an index, an
auto-generated explanation, top activating examples, and a URL.  Its API
returns the features a piece of text activates.  This client asks that
question for each step an agent recorded and stamps the answer on the
step as ``model.internals`` — recorded model state, one more thing the
trace shows.

What this is and is not: a feature that fires at the decisive step and
not at the passing run's counterpart is an *observation* — an internal
signature of the divergence.  It is not a cause until an intervention
(steering the feature, ablating it) flips the outcome; the engine says
so on every internals finding.  Nothing here changes a verdict.

Credentials: ``NEURONPEDIA_API_KEY`` from the environment, never from
arguments.  A :class:`ScriptedNeuronpedia` answers from a local JSON
file so tests and demos run without a network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

BASE_URL = "https://www.neuronpedia.org/api"
DASHBOARD = "https://www.neuronpedia.org/{model}/{source}/{index}"

__all__ = ["Neuronpedia", "ScriptedNeuronpedia", "attach_internals", "feature_url"]


def feature_url(model: str, source: str, index: int) -> str:
    return DASHBOARD.format(model=model, source=source, index=index)


class Neuronpedia:
    """The live client.  ``model`` is a Neuronpedia model id (for example
    ``gemma-2-2b``); ``source`` names the SAE set and layer (for example
    ``12-gemmascope-res-16k``)."""

    def __init__(self, model: str, source: str, *, base_url: Optional[str] = None,
                 api_key_env: str = "NEURONPEDIA_API_KEY", timeout: float = 60.0,
                 top_k: int = 8) -> None:
        self.model = model
        self.source = source
        self.base_url = (base_url or os.environ.get("NEURONPEDIA_BASE_URL") or BASE_URL).rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.top_k = top_k

    def _build(self, method: str, path: str, payload: Optional[dict] = None) -> urllib.request.Request:
        """The request as it will be sent: the key comes from the named
        environment variable and nowhere else, and is never logged."""
        key = os.environ.get(self.api_key_env)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            headers["X-Api-Key"] = key
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        return urllib.request.Request(f"{self.base_url}{path}", data=data,
                                      headers=headers, method=method)

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        request = self._build(method, path, payload)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"neuronpedia {method} {path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"neuronpedia {method} {path}: {exc}") from exc

    def feature(self, index: int) -> dict:
        """One feature's dashboard record (explanation, examples)."""
        return self._request("GET", f"/feature/{self.model}/{self.source}/{index}")

    def search_explanations(self, query: str, limit: int = 10) -> list:
        """Features whose explanation matches ``query``."""
        body = self._request("POST", "/explanation/search",
                             {"modelId": self.model, "layers": [self.source],
                              "query": query, "offset": 0})
        return list(body.get("results") or body.get("explanations") or [])[:limit]

    def activations(self, text: str) -> list:
        """The features ``text`` activates in this SAE, strongest first:
        ``[{index, activation, max_activation, label, tokens}]``."""
        body = self._request("POST", "/search-all",
                             {"modelId": self.model, "sourceSet": self.source.split("-", 1)[-1]
                              if "-" in self.source else self.source,
                              "selectedLayers": [self.source], "text": text,
                              "sortIndexes": [], "ignoreBos": True, "densityThreshold": -1,
                              "numResults": self.top_k})
        return _normalise(body.get("result") or body.get("results") or [], self.model, self.source)


class ScriptedNeuronpedia:
    """Answers from a local JSON of ``{text-or-key: [features]}``; the
    key ``"*"`` answers everything else.  For tests and offline demos."""

    def __init__(self, table: dict, model: str = "scripted", source: str = "scripted") -> None:
        self.table = table
        self.model = model
        self.source = source

    @classmethod
    def from_file(cls, path: str) -> "ScriptedNeuronpedia":
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return cls(data.get("features") or {}, data.get("model", "scripted"),
                   data.get("source", "scripted"))

    def activations(self, text: str) -> list:
        rows = self.table.get(text)
        if rows is None:
            for key, value in self.table.items():
                if key != "*" and key and key in (text or ""):
                    rows = value
                    break
        if rows is None:
            rows = self.table.get("*", [])
        return _normalise(rows, self.model, self.source)


def _normalise(rows: list, model: str, source: str) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        index = row.get("index", row.get("featureIndex"))
        if index is None:
            continue
        activation = row.get("activation", row.get("maxValue", row.get("value")))
        out.append({
            "index": int(index),
            "activation": round(float(activation or 0.0), 4),
            "max_activation": (round(float(row["max_activation"]), 4)
                               if row.get("max_activation") is not None else
                               round(float(row["maxActApprox"]), 4)
                               if row.get("maxActApprox") is not None else None),
            "label": str(row.get("label") or row.get("description") or
                         (row.get("explanations") or [{}])[0].get("description", "")
                         if isinstance(row.get("explanations"), list) else
                         row.get("label") or row.get("description") or ""),
            "tokens": list(row.get("tokens") or [])[:6],
            "url": row.get("url") or feature_url(model, source, int(index)),
        })
    out.sort(key=lambda f: -f["activation"])
    return out


def attach_internals(step: dict, client: Any, *, text: Optional[str] = None,
                     provenance: str = "neuronpedia") -> dict:
    """Ask ``client`` for the features the step's text activates and stamp
    them on ``step["model"]["internals"]``.  The text defaults to the
    step's output (what the model produced), else its input."""
    content = text if text is not None else (step.get("output") or step.get("input") or "")
    features = client.activations(str(content))
    model = step.get("model")
    if not isinstance(model, dict):
        model = {}
        step["model"] = model
    model["internals"] = {
        "model": client.model, "sae": client.source, "source": provenance,
        "features": features,
        "note": ("features that fire on the text this step produced; a difference "
                 "between runs is an observation, not a cause, until steering or "
                 "ablating the feature flips the outcome"),
    }
    return model["internals"]
