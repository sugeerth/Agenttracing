"""The ``key`` command: decode an AgentDiff key and print the overview it
carries; verify it against a bundle; or re-derive it from one.

    agentdiff key <key>                  # decode: the overview, no bundle needed
    agentdiff key <key> --bundle DIR     # verify: recompute the bundle id, match or mismatch, then print
    agentdiff key --from DIR             # re-derive the key from a bundle

A malformed key is an error with the reason, exit 2; a mismatch prints
both ids and exits 1."""

from __future__ import annotations

import argparse
import sys

from .._text import interval, num, pct, plural
from ..bundle import Bundle, decode_key, verify

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("key", help="decode an agentdiff1: key (the level-1 overview, no bundle needed), "
                                              "verify it against a bundle, or re-derive it from one")
    parser.add_argument("key", nargs="?", default=None, help="the key (agentdiff1:…)")
    parser.add_argument("--bundle", default=None, metavar="DIR", help="verify the key's id against this bundle")
    parser.add_argument("--from", dest="from_dir", default=None, metavar="DIR", help="re-derive the key from this bundle")
    parser.set_defaults(func=run)


def print_overview(payload: dict) -> None:
    print(f"{payload.get('name')}: {payload.get('id')}")
    agents = payload.get("agents") or []
    if agents:
        print(f"{'agent':<28} {'runs':>5}  {'success [lo, hi]':<24} {'tokens':>8}  evolving")
        for a in agents:
            sr = a.get("success_rate") or {}
            print(f"{a['name'][:28]:<28} {a['runs']:>5}  {interval(sr.get('rate'), sr.get('lo'), sr.get('hi'), pct):<24} "
                  f"{num(a.get('tokens_total')):>8}  {'yes' if a.get('self_evolving') else 'no'}")
    if payload.get("truncated"):
        print(f"  … and {plural(payload['truncated'], 'more agent')} in the bundle, not named in the key")
    for ln in payload.get("lineages") or []:
        adopted = ln.get("eval_adopted")
        print(f"lineage {ln['family']}: {plural(ln['generations'], 'generation')}, {ln.get('recommended') or 'none'} recommended"
              + (f"; eval adopted {', '.join(adopted)}" if adopted else "; eval adopted nothing" if adopted == [] else ""))
    t = payload.get("totals") or {}
    print(f"totals: {t.get('runs')} run(s), {t.get('tasks')} task(s), {t.get('agents')} agent(s); tokens {num(t.get('tokens'))}; "
          f"cost {num(t.get('cost_usd'), 4) if t.get('cost_usd') is not None else 'not recorded'}; seconds {num(t.get('seconds'))}; "
          f"fetches {num(t.get('fetches'))}; SYNTHETIC share {pct(t.get('synthetic_share'))}")
    if payload.get("locators"):
        print("locators: " + ", ".join(payload["locators"]))


def run(args: argparse.Namespace) -> int:
    if args.from_dir:
        try:
            bundle = Bundle(args.from_dir)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(bundle.key)
        print_overview(decode_key(bundle.key))
        return 0
    if not args.key:
        print("error: give a key, or --from DIR", file=sys.stderr)
        return 2
    try:
        payload = decode_key(args.key)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    code = 0
    if args.bundle:
        try:
            check = verify(args.bundle)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if check["recomputed"] == payload["id"] and check["match"]:
            print(f"match: the bundle at {args.bundle} recomputes to {check['recomputed']}")
        else:
            print(f"mismatch: the key names {payload['id']}, the bundle claims {check['id']} and recomputes to {check['recomputed']}")
            code = 1
    print_overview(payload)
    return code
