"""An agent as the eval forge's proposer: it reads the failing traces and writes rules.

The forge's templates only instantiate what the grammar already knows to
look for. The wrong runs nothing catches are, by construction, the ones
those templates miss, and that is where a reader who can look at the
traces — open a run, locate a term, read a range of steps — earns its
place. So each round after the first, an agent is given the corpus
through the panel's instruments (:func:`deepcompare.harness.panel.corpus_tools`),
the wrong runs still uncaught, the rule grammar, and one more tool:
``try_rule``, which scores a rule **on the learn half only** and says
which of the uncaught runs it would catch and how many right runs it
would wrongly flag. It can propose, test, and go back, as often as its
turns allow.

What it never gets: the held-out half. Its traces are not in the corpus
it reads, and ``try_rule`` does not score on them. Its final proposals
are handed to the forge, which tests them on the held-out half exactly
as it tests its own templates — a proposal from a model is a proposal,
and cannot adopt itself. The next round it is told which of its rules
failed, where, and why; that is the "go back" the loop is named for.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .agent import Tool, run_task
from .panel import corpus_tools
from .providers import Provider, ProviderError

__all__ = ["make_proposer", "SYSTEM", "TURNS"]

TURNS = 16

SYSTEM = (
    "You write evaluation rules for agent traces. Each rule is a deterministic assertion over one run, "
    "in this grammar: mark:<kind> (a trace mark: {marks}); no_check_after_last_edit; claims_without_check; "
    "signature:<issue id>; tool_called:<name>[>=n]; tool_absent:<name>; repeated_call:<n>; error_streak:<n>; "
    "output_matches:<regex>; answer_matches:<regex>; and {{\"all\": [rule, rule]}} for both. "
    "A good rule flags the wrong runs listed as uncaught and flags no right run. Read the runs before you "
    "propose: open them, locate terms, read the steps where they went wrong. Test each candidate with "
    "try_rule, which scores it on the runs you can see; revise the ones that flag right runs. You are not "
    "shown every run, and your rules will be tested on runs you have not seen, so prefer a rule that names "
    "a behaviour over one that names a task. Finish with JSON only: "
    "{{\"rules\": [{{\"rule\": <rule>, \"why\": <one sentence naming the behaviour>}}]}} — at most eight."
)


def _rules_from(answer: str) -> list:
    text = answer.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    return [r for r in rules if isinstance(r, (dict, str))] if isinstance(rules, list) else []


def make_proposer(provider_factory: Callable[[], Provider], traces: list, *,
                  policy: Optional[dict] = None, turns: int = TURNS,
                  log: Optional[list] = None, out_dir=None) -> Callable:
    """A ``proposer(round, context)`` for :func:`deepcompare.forge.forge`.

    ``traces`` are trace dicts; only those of the learn half (named in the
    round's context) are ever put in front of the agent. ``log`` collects
    each round's run of the judge — turns, tools used, what it proposed —
    so the proposer is audited like any other agent.
    """

    def proposer(rnd: int, context: dict) -> list:
        learn = set(context.get("learn_tasks") or [])
        visible = [t for t in traces if (t.get("task") or {}).get("id") in learn]
        tools = list(corpus_tools(visible, policy))
        tries = {"n": 0}

        def try_rule(rule) -> dict:
            tries["n"] += 1
            return context["try_learn"](rule)

        tools.append(Tool(
            name="try_rule", fn=try_rule, effect="read",
            description=("Score a rule on the runs you can see: which wrong runs it catches (and which of "
                         "the uncaught ones), and which right runs it wrongly flags."),
            parameters={"type": "object", "properties": {"rule": {
                "description": "a rule in the grammar: a string, or {\"all\": [rule, rule]}"}},
                "required": ["rule"]}))
        brief = {"round": rnd, "uncaught_wrong_runs": context.get("uncaught"),
                 "what_happened_to_your_earlier_rules": context.get("feedback"),
                 "already_tested": context.get("already")}
        task = {"id": f"forge-round-{rnd}",
                "prompt": "Write rules that catch the uncaught wrong runs without flagging right runs.\n\n" +
                          json.dumps(brief, indent=1)}
        record = {"round": rnd, "proposed": [], "error": None, "turns": 0, "tried": 0}
        try:
            run = run_task(provider_factory(), task, tools, agent="forge-judge",
                           budget={"max_steps": turns}, grader=lambda answer, t: True,
                           system_prompt=SYSTEM.format(marks=", ".join(context.get("marks") or [])),
                           # the judge's own runs are written only where the caller asks
                           out_dir=out_dir)
            answer = str(((run or {}).get("outcome") or {}).get("answer") or "")
            rules = _rules_from(answer)
            record.update({"turns": len((run or {}).get("steps") or []), "proposed": rules,
                           "tried": tries["n"]})
            if not rules:
                record["error"] = "the judge did not return the JSON asked for"
        except ProviderError as exc:
            rules = []
            record["error"] = f"provider error: {exc}"
        proposer.log.append(record)
        if log is not None:
            log.append(record)
        return rules

    proposer.log = []
    return proposer
