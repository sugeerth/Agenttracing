"""Model providers: one neutral turn contract, several wire formats.

A provider receives the conversation so far as a neutral message list and
the tools the agent may call, and returns exactly one model turn: text,
tool calls, and the token usage the endpoint reported.  Nothing above this
layer knows which vendor answered — that is the whole point.

Neutral message shape (OpenAI-flavoured because most endpoints speak it):

    {"role": "system" | "user" | "assistant" | "tool", "content": str,
     "tool_calls": [ToolCall dicts]        (assistant only, optional)
     "tool_call_id": str, "name": str}    (tool results only)

Neutral tool declaration:

    {"name": str, "description": str, "parameters": <JSON schema dict>}

Credentials are read from environment variables at call time and never
appear in errors, logs or traces.  A provider never retries silently: a
transport failure is a :class:`ProviderError`, which the agent loop
records as an ``infrastructure_error`` termination — the harness failing,
not the agent, and excluded from the agent's reliability statistics.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union


class ProviderError(RuntimeError):
    """A model endpoint could not produce a turn (transport, auth, HTTP
    status, unparseable body).  The message names the endpoint and status
    and quotes a bounded excerpt of the body; it never contains a key."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass
class ProviderResponse:
    """One model turn.  ``text`` is the assistant's prose (may be empty
    when the turn is only tool calls); ``tool_calls`` the calls it asked
    for, in order; ``usage`` the endpoint's own token counts when it gave
    them (``{}`` otherwise — never estimated here, the recorder marks
    estimates as estimates); ``latency_s`` the measured wall time of the
    request; ``raw`` the decoded body for anyone who needs the vendor
    detail."""

    text: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    latency_s: Optional[float] = None
    model: str = ""
    raw: Any = None
    stop_reason: Optional[str] = None


class Provider:
    """Base contract.  Subclasses implement :meth:`complete`."""

    kind: str = "provider"

    def __init__(self, model: str) -> None:
        self.model = str(model)

    @property
    def name(self) -> str:
        """A trace-worthy agent name: ``<kind>-<model>``."""
        return f"{self.kind}-{self.model}"

    def complete(self, messages: list, tools: Optional[list] = None) -> ProviderResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# transport

def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> tuple[dict, float]:
    """POST JSON, return (decoded body, elapsed seconds).  Auth headers are
    passed by the caller and never echoed back in any error."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ProviderError(
            f"{url} returned HTTP {exc.code}: {body[:300]}",
            status=exc.code, body=body) from None
    except urllib.error.URLError as exc:
        raise ProviderError(f"{url} unreachable: {exc.reason}") from None
    except OSError as exc:
        raise ProviderError(f"{url} failed: {exc}") from None
    elapsed = time.monotonic() - start
    try:
        return json.loads(body), elapsed
    except ValueError:
        raise ProviderError(
            f"{url} returned a non-JSON body: {body[:300]}", body=body) from None


def _parse_arguments(raw: Any) -> dict:
    """Tool arguments arrive as a JSON string (OpenAI), a dict (Anthropic,
    Ollama) or occasionally nothing.  Unparseable strings are kept under
    ``_raw`` rather than dropped: what the model actually emitted is the
    evidence."""
    if isinstance(raw, dict):
        return dict(raw)
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    return {"_raw": str(raw)}


# ---------------------------------------------------------------------------
# OpenAI-compatible chat completions

class OpenAICompatProvider(Provider):
    """``POST {base_url}/chat/completions`` — OpenAI, vLLM, LM Studio,
    LiteLLM, most gateways.  ``base_url`` defaults to ``$OPENAI_BASE_URL``
    or the public API; the key is ``$<api_key_env>`` (default
    ``OPENAI_API_KEY``) and may be absent for local servers."""

    kind = "openai"

    def __init__(self, model: str, *, base_url: Optional[str] = None,
                 api_key_env: str = "OPENAI_API_KEY", timeout: float = 120.0,
                 temperature: Optional[float] = 0.0,
                 extra: Optional[dict] = None) -> None:
        super().__init__(model)
        self.base_url = (base_url or _env("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.temperature = temperature
        self.extra = dict(extra or {})

    def _headers(self) -> dict:
        headers = {}
        key = _env(self.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @staticmethod
    def _tools(tools: Optional[list]) -> list:
        return [{"type": "function",
                 "function": {"name": t["name"],
                              "description": t.get("description", ""),
                              "parameters": t.get("parameters")
                              or {"type": "object", "properties": {}}}}
                for t in (tools or [])]

    @staticmethod
    def _messages(messages: list) -> list:
        out = []
        for m in messages:
            entry: dict = {"role": m["role"], "content": m.get("content") or ""}
            if m["role"] == "assistant" and m.get("tool_calls"):
                entry["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"],
                                  "arguments": json.dumps(c.get("arguments") or {})}}
                    for c in m["tool_calls"]]
                if not entry["content"]:
                    entry["content"] = None
            if m["role"] == "tool":
                entry["tool_call_id"] = m.get("tool_call_id", "")
            out.append(entry)
        return out

    def complete(self, messages: list, tools: Optional[list] = None) -> ProviderResponse:
        payload: dict = {"model": self.model, "messages": self._messages(messages)}
        if tools:
            payload["tools"] = self._tools(tools)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        payload.update(self.extra)
        body, elapsed = _post_json(f"{self.base_url}/chat/completions",
                                   payload, self._headers(), self.timeout)
        choices = body.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.base_url}: response carried no choices",
                                body=json.dumps(body)[:300])
        message = choices[0].get("message") or {}
        calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            calls.append(ToolCall(
                id=str(call.get("id") or f"call_{i}"),
                name=str(fn.get("name") or ""),
                arguments=_parse_arguments(fn.get("arguments"))))
        usage = body.get("usage") or {}
        return ProviderResponse(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            usage={k: v for k, v in (
                ("input_tokens", usage.get("prompt_tokens")),
                ("output_tokens", usage.get("completion_tokens")),
            ) if isinstance(v, int)},
            latency_s=elapsed, model=str(body.get("model") or self.model),
            raw=body, stop_reason=choices[0].get("finish_reason"))


# ---------------------------------------------------------------------------
# Anthropic Messages API

class AnthropicProvider(Provider):
    """``POST {base_url}/v1/messages``.  Key from ``$ANTHROPIC_API_KEY``;
    ``base_url`` from ``$ANTHROPIC_BASE_URL`` or the public API."""

    kind = "anthropic"

    def __init__(self, model: str, *, base_url: Optional[str] = None,
                 api_key_env: str = "ANTHROPIC_API_KEY", timeout: float = 120.0,
                 max_tokens: int = 1024, temperature: Optional[float] = 0.0,
                 version: str = "2023-06-01") -> None:
        super().__init__(model)
        self.base_url = (base_url or _env("ANTHROPIC_BASE_URL")
                         or "https://api.anthropic.com").rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.version = version

    def _headers(self) -> dict:
        headers = {"anthropic-version": self.version}
        key = _env(self.api_key_env)
        if key:
            headers["x-api-key"] = key
        return headers

    @staticmethod
    def _tools(tools: Optional[list]) -> list:
        return [{"name": t["name"], "description": t.get("description", ""),
                 "input_schema": t.get("parameters")
                 or {"type": "object", "properties": {}}}
                for t in (tools or [])]

    @staticmethod
    def _messages(messages: list) -> tuple[Optional[str], list]:
        """Split the system prompt out and turn tool results into
        ``tool_result`` blocks on a user turn, merging consecutive results
        the way the API requires."""
        system = None
        out: list = []
        for m in messages:
            role = m["role"]
            if role == "system":
                system = (system + "\n" if system else "") + (m.get("content") or "")
                continue
            if role == "assistant":
                blocks: list = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c["id"],
                                   "name": c["name"],
                                   "input": c.get("arguments") or {}})
                out.append({"role": "assistant", "content": blocks or [
                    {"type": "text", "text": ""}]})
                continue
            if role == "tool":
                block = {"type": "tool_result",
                         "tool_use_id": m.get("tool_call_id", ""),
                         "content": m.get("content") or ""}
                if out and out[-1]["role"] == "user" and isinstance(
                        out[-1]["content"], list) and out[-1]["content"] and \
                        out[-1]["content"][-1].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue
            out.append({"role": "user", "content": m.get("content") or ""})
        return system, out

    def complete(self, messages: list, tools: Optional[list] = None) -> ProviderResponse:
        system, converted = self._messages(messages)
        payload: dict = {"model": self.model, "max_tokens": self.max_tokens,
                         "messages": converted}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._tools(tools)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        body, elapsed = _post_json(f"{self.base_url}/v1/messages", payload,
                                   self._headers(), self.timeout)
        text_parts: list = []
        calls: list = []
        for i, block in enumerate(body.get("content") or []):
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=str(block.get("id") or f"toolu_{i}"),
                                      name=str(block.get("name") or ""),
                                      arguments=_parse_arguments(block.get("input"))))
        usage = body.get("usage") or {}
        return ProviderResponse(
            text="".join(text_parts), tool_calls=calls,
            usage={k: v for k, v in (
                ("input_tokens", usage.get("input_tokens")),
                ("output_tokens", usage.get("output_tokens")),
            ) if isinstance(v, int)},
            latency_s=elapsed, model=str(body.get("model") or self.model),
            raw=body, stop_reason=body.get("stop_reason"))


# ---------------------------------------------------------------------------
# Ollama chat API

class OllamaProvider(Provider):
    """``POST {base_url}/api/chat`` (non-streaming).  ``base_url`` from
    ``$OLLAMA_HOST`` or ``http://localhost:11434``.  Ollama reports real
    prompt/eval token counts and nanosecond durations; both are kept."""

    kind = "ollama"

    def __init__(self, model: str, *, base_url: Optional[str] = None,
                 timeout: float = 300.0, options: Optional[dict] = None) -> None:
        super().__init__(model)
        host = base_url or _env("OLLAMA_HOST") or "http://localhost:11434"
        if not host.startswith("http"):
            host = "http://" + host
        self.base_url = host.rstrip("/")
        self.timeout = timeout
        self.options = dict(options or {"temperature": 0})

    @staticmethod
    def _messages(messages: list) -> list:
        out = []
        for m in messages:
            entry: dict = {"role": m["role"], "content": m.get("content") or ""}
            if m["role"] == "assistant" and m.get("tool_calls"):
                entry["tool_calls"] = [
                    {"function": {"name": c["name"],
                                  "arguments": c.get("arguments") or {}}}
                    for c in m["tool_calls"]]
            out.append(entry)
        return out

    def complete(self, messages: list, tools: Optional[list] = None) -> ProviderResponse:
        payload: dict = {"model": self.model, "stream": False,
                         "messages": self._messages(messages),
                         "options": self.options}
        if tools:
            payload["tools"] = OpenAICompatProvider._tools(tools)
        body, elapsed = _post_json(f"{self.base_url}/api/chat", payload, {},
                                   self.timeout)
        message = body.get("message") or {}
        calls = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            calls.append(ToolCall(id=str(call.get("id") or f"ollama_{i}"),
                                  name=str(fn.get("name") or ""),
                                  arguments=_parse_arguments(fn.get("arguments"))))
        usage = {}
        if isinstance(body.get("prompt_eval_count"), int):
            usage["input_tokens"] = body["prompt_eval_count"]
        if isinstance(body.get("eval_count"), int):
            usage["output_tokens"] = body["eval_count"]
        measured = body.get("total_duration")
        latency = (measured / 1e9 if isinstance(measured, (int, float))
                   and measured > 0 else elapsed)
        return ProviderResponse(
            text=str(message.get("content") or ""), tool_calls=calls,
            usage=usage, latency_s=latency,
            model=str(body.get("model") or self.model), raw=body,
            stop_reason=body.get("done_reason"))


# ---------------------------------------------------------------------------
# Scripted (no network): tests, demos, reproducible fixtures

class ScriptedProvider(Provider):
    """Replays canned turns.  ``script`` is a list of turn dicts
    (``{"text": ..., "tool_calls": [{"name", "arguments"}], "usage": {...}}``)
    consumed in order, or a callable ``(messages, tools) -> turn dict``
    for scripts that need to react.  A script that runs out raises
    :class:`ProviderError` — an exhausted script is the harness failing,
    and the trace says so."""

    kind = "scripted"

    def __init__(self, script: Union[list, Callable], model: str = "script") -> None:
        super().__init__(model)
        self._script = script
        self._cursor = 0

    @classmethod
    def from_file(cls, path: str) -> "ScriptedProvider":
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        turns = data["turns"] if isinstance(data, dict) else data
        model = data.get("model", "script") if isinstance(data, dict) else "script"
        return cls(turns, model=model)

    def complete(self, messages: list, tools: Optional[list] = None) -> ProviderResponse:
        if callable(self._script):
            turn = self._script(messages, tools)
        else:
            if self._cursor >= len(self._script):
                raise ProviderError("scripted provider has no more turns")
            turn = self._script[self._cursor]
            self._cursor += 1
        calls = [ToolCall(id=str(c.get("id") or f"scripted_{i}"),
                          name=str(c["name"]),
                          arguments=_parse_arguments(c.get("arguments")))
                 for i, c in enumerate(turn.get("tool_calls") or [])]
        return ProviderResponse(text=str(turn.get("text") or ""), tool_calls=calls,
                                usage=dict(turn.get("usage") or {}),
                                latency_s=turn.get("latency_s"), model=self.model,
                                raw=turn, stop_reason=turn.get("stop_reason"))


# ---------------------------------------------------------------------------
# specs

_KINDS = {
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}


def provider_from_spec(spec: str, **options: Any) -> Provider:
    """``kind:model`` → provider.  ``openai:gpt-4o``, ``anthropic:claude-…``,
    ``ollama:llama3.1``, ``scripted:path/to/turns.json``.  Unknown kinds
    are an error naming the known ones — a typo must not silently become
    the default vendor."""
    if ":" not in spec:
        raise ValueError(f"provider spec {spec!r} must be kind:model, e.g. "
                         f"one of {', '.join(sorted(_KINDS))}:<model> or scripted:<file>")
    kind, model = spec.split(":", 1)
    kind = kind.strip().lower()
    if kind == "scripted":
        return ScriptedProvider.from_file(model)
    if kind not in _KINDS:
        raise ValueError(f"unknown provider kind {kind!r}; known: "
                         f"{', '.join(sorted(_KINDS))}, scripted")
    return _KINDS[kind](model, **options)
