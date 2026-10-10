"""The ``evolve-evals`` command: one eval suite, carried through a policy's generations.

    agentdiff evolve-evals g0/ g1/ g2/ g3/                         what made runs fail
    agentdiff evolve-evals g0/ g1/ g2/ g3/ --target reward-hacking where the reward lied
    agentdiff evolve-evals g4/ --ledger evals.json                 continue from where it stopped

Each directory is one generation's runs (traces, or in-band telemetry
turned into traces), oldest first. For each generation it forward-tests
the evals carried in, on runs they never saw; retires the noisy and the
gone-quiet; and forges new ones from that generation's failures. Then it
prints the lineage and writes ``evolve-evals.json`` and
``EVOLVING_EVALS.md``. See :mod:`agentdiff.evolving`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    from ..rleval import TARGETS
    parser = subparsers.add_parser(
        "evolve-evals", help="carry one eval suite through a policy's generations: forward-test the evals on "
                             "runs they never saw, retire the noisy and the gone-quiet, forge new ones from "
                             "each generation's failures (RL: --target reward-hacking)")
    parser.add_argument("generations", nargs="+", metavar="GEN_DIR", help="one directory of runs per generation, oldest first")
    parser.add_argument("--target", default="failure", choices=sorted(TARGETS),
                        help="what an eval must catch (default: failure)")
    parser.add_argument("--patience", type=int, default=None,
                        help="generations an eval may catch nothing before it is retired (default 2)")
    parser.add_argument("--ledger", default=None, metavar="FILE", help="continue from this ledger, and write it back")
    parser.add_argument("--golden", default=None, help="golden set: its failure labels are the truth")
    parser.add_argument("--policy", default=None, help="safety policy JSON")
    parser.add_argument("-o", "--output", default="evolve-evals-out", metavar="DIR")
    parser.set_defaults(func=run)


def _markdown(result: dict) -> str:
    out = [f"# Evals that evolve with the policy: {result['says']}", "", result["narrative"], "",
           "| generation | runs | to catch | carried in | forward coverage | forward false alarms | retired | born | suite leaving |",
           "|---|---|---|---|---|---|---|---|---|"]
    for g in result["lineage"]:
        if g.get("skipped"):
            out.append(f"| {g['generation']} | — | — | — | {g['skipped']} | | | | |")
            continue
        cov = g["forward"]["coverage"]
        out.append(f"| {g['generation']} | {g['runs']} | {g['wrong']} | {g['arrived']} | "
                   f"{'—' if cov is None or not g['arrived'] else f'{cov:.0%}'} | {g['forward']['false_alarms']} | "
                   f"{', '.join(g['retired']) or '—'} | {', '.join(g['born'] + g['reborn']) or '—'} | {g['leaving']['suite']} |")
    out += ["", "## Every eval", "", "| eval | flags a run when | born | status | why it left |", "|---|---|---|---|---|"]
    for e in result["evals"]:
        out.append(f"| `{e['id']}` | {e['says']} | {e['born']} | {e['status']} | {e.get('reason') or ''} |")
    out += ["", f"Forward coverage is the suite as it arrived, on runs it was never written from. An eval is retired "
                f"when it fires on more than {result['fpr_max']:.0%} of right runs, or catches nothing for "
                f"{result['patience']} generations in a row while there were failures to catch.", ""]
    return "\n".join(out)


def run(args: argparse.Namespace) -> int:
    from ..evolving import PATIENCE, evolve, load_ledger, write_ledger
    from ._io import load_traces
    golden = policy = None
    try:
        if args.golden:
            from ..scorecard import load_golden
            golden = load_golden(args.golden)
        if args.policy:
            from ..scorecard import load_policy
            policy = load_policy(args.policy)
        ledger = load_ledger(args.ledger) if args.ledger else None
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    gens = []
    for d in args.generations:
        if not Path(d).is_dir():
            print(f"error: {d} is not a directory of runs", file=sys.stderr)
            return 2
        trajectories = load_traces(Path(d))
        if not trajectories:
            print(f"error: no traces in {d}", file=sys.stderr)
            return 2
        gens.append((Path(d).name, trajectories))
    try:
        result = evolve(gens, target=args.target, golden=golden, policy=policy, ledger=ledger,
                        patience=args.patience or PATIENCE)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evolve-evals.json").write_text(json.dumps({k: v for k, v in result.items() if k != "ledger"}, indent=1),
                                           encoding="utf-8")
    (out / "EVOLVING_EVALS.md").write_text(_markdown(result), encoding="utf-8")
    if args.ledger:
        write_ledger(args.ledger, result["ledger"])
    print(f"target: {result['says']}\n")
    print(f"  {'gen':<8} {'runs':>5} {'catch':>6} {'carried':>8} {'forward':>8}  changes")
    for g in result["lineage"]:
        if g.get("skipped"):
            print(f"  {g['generation']:<8} {g['skipped']}")
            continue
        cov = g["forward"]["coverage"]
        fwd = "—" if cov is None or not g["arrived"] else f"{cov:.0%}"
        changes = [f"-{r}" for r in g["retired"]] + [f"+{b}" for b in g["born"]] + [f"↺{b}" for b in g["reborn"]]
        print(f"  {g['generation']:<8} {g['runs']:>5} {g['wrong']:>6} {g['arrived']:>8} {fwd:>8}  {' '.join(changes) or '·'}")
    print(f"\nactive: {', '.join(result['active']) or 'none'}")
    if result["retired"]:
        print(f"retired: {', '.join(result['retired'])}")
    print(f"wrote {out / 'evolve-evals.json'} and {out / 'EVOLVING_EVALS.md'}" +
          (f"; ledger {args.ledger}" if args.ledger else ""))
    return 0
