"""The harness: run agents against any model, record SCHEMA traces.

This subpackage is the ONE place in the project that talks to a network.
The analysis engine (everything in ``deepcompare`` outside this package)
contains no network code and never imports from here — a report is
computed from trace files and nothing else, so no verdict can depend on
a model that was reachable at the time.  ``tests/test_harness.py`` pins
that boundary.

Three layers, each swappable on its own:

* :mod:`.providers` — a :class:`~deepcompare.harness.providers.Provider`
  turns a neutral message list plus tool declarations into one model
  turn.  OpenAI-compatible endpoints (OpenAI, vLLM, LM Studio, most
  gateways), Anthropic's Messages API and Ollama's chat API ship here;
  a :class:`~deepcompare.harness.providers.ScriptedProvider` replays
  canned turns for tests and demos without a network.  Credentials come
  from environment variables only — never from arguments, never from
  files this package reads.
* :mod:`.agent` — a generic tool-loop agent: prompt the provider, run
  the tools it asks for, feed the results back, stop at the answer or
  the budget.  Every turn and every tool call goes through
  :class:`~deepcompare.record.Recorder`, so the trace is a first-class
  SCHEMA trajectory with measured tokens, latency and declared
  terminations — not a transcript reconstructed afterwards.
* :mod:`.runner` — a task set times a set of named providers times N
  repetitions, written as ``<task>__<agent>__<run>.json`` so
  ``batch``, ``fleet``, ``runs`` and every other command read them
  directly.

Swapping a model is swapping one provider spec string.
"""

from .agent import Tool, run_task
from .providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatProvider,
    Provider,
    ProviderError,
    ProviderResponse,
    ScriptedProvider,
    ToolCall,
    provider_from_spec,
)
from .external import CommandAgent, ExternalAgent, PythonAgent, agent_from_spec
from .replay import replay
from .runner import run_suite

__all__ = [
    "AnthropicProvider", "CommandAgent", "ExternalAgent", "PythonAgent",
    "agent_from_spec", "OllamaProvider", "OpenAICompatProvider",
    "Provider", "ProviderError", "ProviderResponse", "ScriptedProvider",
    "Tool", "ToolCall", "provider_from_spec", "replay", "run_suite",
    "run_task",
]
