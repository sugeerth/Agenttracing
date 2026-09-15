"""A grounded conversation about one output directory: the model phrases,
the engine's facts are the only authority, every answer is checked.

The engine builds the brief (:func:`deepcompare.narrate.chat_brief` —
the aggregate's facts, each pair's, the lineage's evolution, the eval
that evolved with it and the evals comparison when the directory holds
one, every fact numbered, every number in ``allowed_numbers``) and the
prompt (:func:`deepcompare.narrate.chat_prompt`, which tells the model
to cite ``[F7]``-style facts and to say "not in the report" otherwise).
This module is the part that talks to a provider — one of
:mod:`deepcompare.harness.providers`, a network endpoint or a
:class:`~deepcompare.harness.providers.ScriptedProvider` for tests — and
so it lives in the harness, the one place that talks to a network; the
engine never imports it, and the ``chat`` command imports it inside
``run`` only when a provider or a script is given.

Every answer goes through :func:`deepcompare.narrate.check_narration`
and is returned with its violations attached — the numbers not in the
report, the citations that name no fact — never silently. The model
can set no number, no verdict and no exit code: an unfaithful answer is
printed flagged, and the command still exits 0, because a chat is
commentary. Credentials come from environment variables read by the
provider at call time and appear nowhere here.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from ..narrate import chat_prompt, check_narration
from .providers import Provider, ProviderError

__all__ = ["ask", "converse", "format_turn", "EXIT_PROVIDER_FAILED"]

#: the exit code of a conversation the provider could not continue
EXIT_PROVIDER_FAILED = 3


def ask(provider: Provider, brief: dict, question: str, history: Optional[list] = None) -> dict:
    """One turn: the covenant prompt with the brief, the conversation so
    far (``history``: neutral user / assistant messages) and the question,
    to the provider; back comes ``{question, answer, check, usage}`` with
    ``check`` the faithfulness result of the answer against the brief.
    Raises :class:`ProviderError` when the provider cannot answer — no
    retry, and the message never carries a key."""
    messages = [{"role": "system", "content": chat_prompt(brief)}]
    messages.extend(dict(m) for m in (history or []))
    messages.append({"role": "user", "content": question})
    response = provider.complete(messages, None)
    text = (response.text or "").strip()
    return {"question": question, "answer": text, "check": check_narration(brief, text),
            "usage": dict(response.usage or {})}


def format_turn(turn: dict) -> str:
    """The answer, then its check — every violation named, a faithful
    answer said to be one — the way the command prints it."""
    lines = [f"  {line}" if line else "" for line in (turn["answer"] or "(no answer)").splitlines()]
    check = turn["check"]
    if check["faithful"]:
        lines.append(f"  · faithful: {check['numbers_checked']} number(s) and {check['citations']} citation(s) "
                     f"trace to the report")
    else:
        bad = check["unsupported_numbers"]
        if bad:
            lines.append(f"  · {len(bad)} number{'s' if len(bad) != 1 else ''} not in the report: {', '.join(bad)}")
        bad = check["invalid_citations"]
        if bad:
            lines.append(f"  · {len(bad)} citation{'s' if len(bad) != 1 else ''} naming no fact: {', '.join(bad)}")
        lines.append("  · flagged: read the sentence that carries it as the model's, not the report's")
    return "\n".join(lines)


def converse(provider: Provider, brief: dict, questions: Iterable[str],
             out: Callable[[str], None] = print, err: Callable[[str], None] = print) -> int:
    """The conversation: each question through :func:`ask` with the turns
    so far as history, printed with :func:`format_turn`; a blank line is
    skipped, ``exit`` or ``quit`` ends it. Returns 0, or
    :data:`EXIT_PROVIDER_FAILED` when the provider fails — the turns
    before it stand as printed."""
    history: list = []
    for raw in questions:
        question = (raw or "").strip()
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            break
        out(f"> {question}")
        try:
            turn = ask(provider, brief, question, history)
        except ProviderError as exc:
            err(f"error: provider failed: {exc}")
            return EXIT_PROVIDER_FAILED
        out(format_turn(turn))
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": turn["answer"]})
    return 0
