"""The ``forge`` command: grow an eval suite from a corpus, with an agent proposing rules.

``batch`` already forges evals from templates, signatures and a reader's
marks. ``forge`` adds a judge: an agent that reads the failing traces with
tools and proposes rules for the wrong runs nothing catches yet, sees only
the learn half, and is told each round why its earlier rules failed. Its
proposals are tested on the held-out half like any other; the page shows
the suite growing round by round.

Talks to a network unless the judge is scripted; the harness is imported
inside the command, so the analysis engine stays network-free.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._common import provider_option_args, provider_options, split_spec

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "forge", help="grow an eval suite from where the runs went wrong: candidate evals from the traces, "
                      "a reader's marks and an agent judge, each adopted only if it holds on tasks it was "
                      "not written from; writes the page (talks to a network unless the judge is scripted)")
    parser.add_argument("tracesdir", help="directory of trajectory *.json files (two agents, pairwise by task)")
    parser.add_argument("-o", "--output", default="forge-out", metavar="DIR")
    parser.add_argument("--golden", default=None, help="golden set: its failure labels are the truth evals are held to")
    parser.add_argument("--policy", default=None, help="safety policy JSON")
    parser.add_argument("--seeds", default=None, metavar="FILE", help="the page's downloaded marks")
    parser.add_argument("--evals", default=None, metavar="LEDGER", help="eval ledger, re-tested and written back")
    parser.add_argument("--judge", default=None, metavar="NAME=KIND:MODEL",
                        help="an agent that proposes rules each round (e.g. j=anthropic:MODEL, scripted:FILE)")
    parser.add_argument("--turns", type=int, default=None, help="judge turns per round (default 16)")
    parser.add_argument("--template", default=None, help="page template")
    provider_option_args(parser)
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from . import batch as batch_cmd
    proposer = None
    if args.judge:
        from ..harness import provider_from_spec
        from ..harness.forge_judge import TURNS, make_proposer
        _name, spec = split_spec(args.judge)
        kind = spec.split(":", 1)[0].strip().lower()
        options = provider_options(args)
        factory = lambda: provider_from_spec(spec, **({} if kind == "scripted" else options))  # noqa: E731
        traces = []
        for path in sorted(Path(args.tracesdir).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "steps" in data and "outcome" in data:
                traces.append(data)
        policy = None
        if args.policy:
            from ..scorecard import load_policy
            policy = load_policy(args.policy)
        proposer = make_proposer(factory, traces, policy=policy, turns=args.turns or TURNS)
    ns = argparse.Namespace(tracesdir=args.tracesdir, output=args.output, template=args.template,
                            golden=args.golden, policy=args.policy, lessons=None, evals=args.evals,
                            seeds=args.seeds, forge_proposer=proposer)
    code = batch_cmd.run(ns)
    if code != 0:
        return code
    agg = json.loads((Path(args.output) / "aggregate.json").read_text(encoding="utf-8"))
    f = agg.get("forge") or {}
    print()
    for r in f.get("rounds") or []:
        print(f"  round {r['round']}: {r['tried']} tried, {r['held_out_tests']} met the held-out half, "
              f"{r['adopted']} adopted — {r['caught']}/{r['wrong']} wrong runs caught, "
              f"{r['false_alarms']} right run(s) flagged  [{', '.join(r['sources'])}]")
    for e in f.get("suite") or []:
        print(f"  ✓ {e['id']}  ({e['source']}): flags a run when {e['says']}")
    if f.get("judge", {}).get("used"):
        j = f["judge"]
        print(f"  judge: {j['proposed']} rule(s) proposed, {j['adopted']} adopted; it saw {j['saw']}")
    print(f"page: {Path(args.output) / 'report.html'}")
    return 0
