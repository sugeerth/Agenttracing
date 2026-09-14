"""The ``coevolve`` command: a self-evolving agent's lineage read by an
eval that evolves with it.  Runs the lineage the way ``evolve`` does —
the last step's pair as an ordinary runs output with
``aggregate["evolution"]`` and ``aggregate["coevolution"]`` attached —
and prints the eval's own lineage: what each eval generation adopted and
why, the hindsight lines, the recommendation under both evals.
``--candidates FILE.json`` puts external candidate metrics through the
validators; ``--propose PROVIDER`` asks a model for candidates through
the harness (imported inside ``run`` only) and puts those through the
same validators — they can never set a number, a verdict or an exit
code.  Exit 0 always — it is a report — unless ``--fail-on`` names a
condition the eval trips.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..coevolve import FAIL_ON as COEVOLVE_FAIL_ON
from ..evolve import LAYOUTS as EVOLVE_LAYOUTS
from ._common import provider_option_args, provider_options, split_spec
from ._io import template_from, write_outputs
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "coevolve", help="a self-evolving agent's lineage read by an eval that evolves with it: probes propose "
                         "metrics at every step, validators adopt or reject them with reasons, and the lineage is "
                         "re-read with hindsight; the evolve output with aggregate[\"coevolution\"] attached")
    parser.add_argument("lineage", help="lineage directory")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument("--template", help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--layout", choices=EVOLVE_LAYOUTS, default="native",
                        help="native: <gen>/agent.json + <gen>/traces; flat: one runs directory with agents/<gen>.json")
    parser.add_argument("--metric", choices=("return", "discounted_return", "success", "steps", "seconds"),
                        default="return", help="the score the evolution section's IQM and improvement are computed on")
    parser.add_argument("--samples", type=int, default=2000, help="bootstrap resamples per statistic (at least 1: an interval is never a bare point)")
    parser.add_argument("--candidates", default=None, metavar="FILE.json",
                        help="external candidate metrics, a JSON list of {\"at\": \"<from>→<to>\" | null, "
                             "\"spec\": {...}, \"source\": \"...\"}; each goes through the validators")
    parser.add_argument("--propose", default=None, metavar="PROVIDER",
                        help="ask a model for candidate metrics at every step (kind:model, or scripted:FILE); "
                             "talks to a network unless the provider is scripted; the harness is imported inside "
                             "the command; every proposal goes through the same validators")
    provider_option_args(parser)
    parser.add_argument("--fail-on", default=None,
                        help="comma-separated conditions (" + ", ".join(COEVOLVE_FAIL_ON) + "): exit 1 when the eval trips one")
    parser.add_argument("--ledger", action="store_true", help="print every ledger row with its validators")
    parser.set_defaults(func=run)


def _load_candidates(path: str) -> tuple:
    """``(candidates, error)``: the JSON list of external candidates, or why not."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"{p} cannot be read: {exc.strerror or exc}"
    except ValueError as exc:
        return None, f"{p} is not valid JSON: {exc}"
    if isinstance(raw, dict):
        raw = raw.get("candidates", raw)
    if not isinstance(raw, list):
        return None, f"{p} must hold a JSON list of candidates"
    return raw, None


def run(args: argparse.Namespace) -> int:
    """The lineage with the co-evolving eval: the evolve outputs, the
    ``coevolution`` section, a summary of the eval's lineage on stdout.
    Exit 2 on an unreadable input, 1 on a ``--fail-on`` hit, else 0."""
    from ..coevolve import coevolve, fail_on, proposal_briefs
    from ..evolve import lineage_batch, read_lineage
    if args.samples < 1:
        print(f"error: --samples must be at least 1, not {args.samples}: with no bootstrap draw every interval would be a "
              "bare point", file=sys.stderr)
        return 2
    lineage = read_lineage(args.lineage, layout=args.layout)
    if not lineage["measurable"]:
        print(f"error: {lineage['reason']}", file=sys.stderr)
        return 2
    for note in lineage["notes"]:
        print(f"warning: {note}", file=sys.stderr)
    names = [n.strip() for n in (args.fail_on or "").split(",") if n.strip()]
    unknown = sorted(set(names) - set(COEVOLVE_FAIL_ON))
    if unknown:
        print(f"error: unknown --fail-on name(s): {', '.join(unknown)}; choose from {', '.join(COEVOLVE_FAIL_ON)}",
              file=sys.stderr)
        return 2
    candidates: list = []
    if args.candidates:
        loaded, err = _load_candidates(args.candidates)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        candidates = list(loaded)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    warn = lambda m: print(f"warning: {m}", file=sys.stderr)  # noqa: E731
    batch = lineage_batch(lineage, warn=warn, metric=args.metric, samples=args.samples, layout=args.layout,
                          candidates=candidates or None)
    reports, agg = batch["reports"], batch["aggregate"]
    if batch["pair"] is None:
        print("warning: fewer than two generations carry traces; no pair report is written", file=sys.stderr)
    elif batch["names"]:
        print(f"Last step: A={batch['names'][0]}  B={batch['names'][1]}")
    evolution = agg["evolution"]

    if args.propose:
        # the external proposer: the harness is imported here and nowhere
        # else in the engine; its proposals are candidates like any other
        # and go through the validators in a second pass of the section
        from ..harness import provider_from_spec
        from ..harness.proposer import propose
        _name, spec = split_spec(args.propose)
        kind = spec.split(":", 1)[0].strip().lower()
        try:
            provider = provider_from_spec(spec, **({} if kind == "scripted" else provider_options(args)))
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        proposed = 0
        for brief in proposal_briefs(lineage, evolution, coevolution=agg.get("coevolution")):
            for row in propose(provider, brief["brief"]):
                proposed += 1
                entry = {"at": brief["at"], "source": row["origin"]["source"]}
                if "rejected" in row:
                    entry["rejected"] = row["rejected"]
                else:
                    entry["spec"] = row["spec"]
                candidates.append(entry)
        print(f"Proposer: {getattr(provider, 'name', kind)} sent {proposed} candidate(s) over "
              f"{len(agg.get('coevolution', {}).get('steps') or [])} step(s); validated, never trusted")
        agg["coevolution"] = coevolve(lineage, evolution, samples=args.samples, candidates=candidates)

    write_outputs(out_dir, reports, agg, template_from(args), html=bool(reports))
    if not reports:
        print("warning: no pair report, so report.html is not rendered", file=sys.stderr)
    co = agg["coevolution"]
    _print_coevolution(co, evolution, ledger=args.ledger)
    hits = fail_on(co, names) if names else []
    if hits:
        print("fail-on: " + ", ".join(f"{name} {detail}" for name, detail in hits))
        return 1
    return 0


def _band(v: dict) -> str:
    if not v or v.get("point") is None:
        return "n/a"
    return f"{v['point']:+.2f} [{v['lo']:+.2f}, {v['hi']:+.2f}]"


def _print_coevolution(co: dict, evolution: dict, ledger: bool = False) -> None:
    """The eval's lineage in the CLI voice: the eval generations and what
    each adopted with the reason, the hindsight lines, the recommendation
    under both evals, the integrity; every ledger row with ``--ledger``."""
    print(f"Eval: {co['family'] or '?'}  {len(co['eval_generations'])} eval generation(s)"
          + ("  [SYNTHETIC]" if co.get("synthetic") else ""))
    if not co["measurable"]:
        print(f"  not readable: {co['reason']}")
        return
    by_id = {r["index"]: r for r in co["ledger"]}
    for e in co["eval_generations"]:
        head = f"  {e['id']:<4} {e['size']} metric(s)"
        if e["after_step"] is None:
            print(f"{head}  base: {', '.join(e['adopted'])}")
            continue
        print(f"{head}  after {e['after_step']} ({e['trigger_probe']})")
        for mid in e["adopted"]:
            m = co["metrics"][mid]
            row = by_id.get((m["adopted_at"] or {}).get("ledger"))
            print(f"       + {mid} ({m['spec']['name']}; {m['origin'].get('probe')}) — {row['reason'] if row else ''}")
        for mid in e["demoted"]:
            print(f"       ~ {mid} demoted — {co['metrics'][mid]['demoted_at']['reason']}")
        for mid in e["retired"]:
            print(f"       - {mid} retired — {co['metrics'][mid]['retired_at']['reason']}")
    rejected = [r for r in co["ledger"] if r["decision"] == "rejected"]
    if rejected:
        print(f"  Rejected: {len(rejected)} candidate(s)")
        for r in rejected:
            print(f"       {r['step']} {r['probe']} {r['spec_id'] or '?'}: {r['reason']}")
    print("Hindsight:")
    for s in co["steps"]:
        ev = s["evolved"]
        mark = "changed" if ev["changed"] else "same"
        cell = lambda f: f"{f['metric']} {f['from']:+.2f}→{f['to']:+.2f} [{f['delta']['lo']:+.2f}, {f['delta']['hi']:+.2f}]"  # noqa: E731
        flags = ", ".join(cell(f) for f in ev["flags"])
        base_flags = ", ".join(cell(f) for f in ev.get("base_flags") or [])
        print(f"  {s['from']} → {s['to']}  base {s['base']['verdict'] or '?'}  evolved {mark}"
              + (f": {flags}" if flags else "") + (f"  base metrics: {base_flags}" if base_flags else ""))
    for mid, c in (co.get("hindsight") or {}).get("caught_at", {}).items():
        print(f"  {mid}: {c['note']}")
    rec = co["recommended"]
    print(f"Recommended: base {rec['base']}, evolved {rec['evolved']} ({'agree' if rec['agree'] else 'disagree'}) — {rec['why']}")
    integ = co["integrity"]
    m = integ["multiplicity"]
    print(f"Integrity: drift {integ['drift']['jaccard_distance_from_base']} from the base; {m['tested']} tested, "
          f"{m['adopted']} adopted, {m['rejected']} rejected at alpha {m['alpha']} (min adjusted "
          f"{m['min_adjusted_alpha'] if m['min_adjusted_alpha'] is not None else '—'}); demoted {len(integ['demoted'])}, "
          f"retired {len(integ['retired'])}, unconfirmed {len(integ['unconfirmed'])}; external "
          f"{integ['external']['received']} received / {integ['external']['parsed']} parsed / {integ['external']['adopted']} adopted")
    print(f"Flow: {co['flow']['summary']['sentence']}")
    print(f"Gap: {integ['gap']}")
    if ledger:
        print("Ledger:")
        for r in co["ledger"]:
            print(f"  #{r['index']} {r['step']} {r['probe']} {r['spec_id'] or '?'} k={r['k']} alpha={r['alpha']} → {r['decision']}"
                  + (f" (failed {', '.join(r['failed'])})" if r["failed"] else ""))
            for name, v in r["validators"].items():
                print(f"       {name:<12} {'pass' if v.get('pass') else 'FAIL' if v.get('pass') is False else '—'}  {v.get('note', '')}")
