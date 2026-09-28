"""The ``evolve`` command: a self-evolving agent's lineage — per step,
did it help, what changed, and did it game, forget, overfit or touch a
protected path; which generation to keep.  The last step is written as
an ordinary runs output with ``aggregate["evolution"]`` attached, and
``--against`` adds the four-axis comparison with other lineages beside
it (``aggregate["evolution_compare"]``).  Exit 0 always — it is a
report — unless ``--fail-on`` names a verdict or flag some step carries.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..evolve import FLAGS as EVOLVE_FLAGS, LAYOUTS as EVOLVE_LAYOUTS, VERDICTS as EVOLVE_VERDICTS
from ._io import template_from, write_outputs
from .paths import DEFAULT_TEMPLATE

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "evolve", help="a self-evolving agent's lineage (<gen>/agent.json + <gen>/traces/*.json): per step, did it "
                       "help, what changed, and did it game, forget, overfit or touch a protected path; which "
                       "generation to keep; the last step as an ordinary runs output")
    parser.add_argument("lineage", help="lineage directory")
    parser.add_argument("-o", "--output", default="out", help="output directory (default: out)")
    parser.add_argument("--template", help=f"viewer HTML template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument("--layout", choices=EVOLVE_LAYOUTS, default="native",
                        help="native: <gen>/agent.json + <gen>/traces; flat: one runs directory with agents/<gen>.json")
    parser.add_argument("--metric", choices=("return", "discounted_return", "success", "steps", "seconds"),
                        default="return", help="the score the IQM and the improvement are computed on")
    parser.add_argument("--samples", type=int, default=2000, help="bootstrap resamples per statistic")
    parser.add_argument("--fail-on", default=None,
                        help="comma-separated verdicts or flags (" + ", ".join(EVOLVE_VERDICTS + EVOLVE_FLAGS)
                             + "): exit 1 when any step carries one")
    parser.add_argument("--against", action="append", default=[], metavar="LINEAGE",
                        help="another lineage over the same tasks to compare with, on four axes (peak, final, "
                             "learning, process); repeatable; adds aggregate[\"evolution_compare\"]")
    parser.add_argument("--threshold", type=float, default=None,
                        help="the learning race's threshold on the task-balanced IQM (default: the midpoint "
                             "between the lowest g0 and the highest recommended point, stated in the output)")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """A self-evolving agent's lineage: the last step's pair as an ordinary
    runs output (report_<task>.json, aggregate.json, report.html) with
    ``aggregate["evolution"]`` attached, and a one-line-per-step summary.
    Exit 0 always — it is a report — unless ``--fail-on`` names a verdict
    or flag some step carries."""
    from ..evolve import fail_on, lineage_batch, read_lineage
    lineage = read_lineage(args.lineage, layout=args.layout)
    if not lineage["measurable"]:
        print(f"error: {lineage['reason']}", file=sys.stderr)
        return 2
    for note in lineage["notes"]:
        print(f"warning: {note}", file=sys.stderr)
    names = [n.strip() for n in (args.fail_on or "").split(",") if n.strip()]
    unknown = sorted(set(names) - set(EVOLVE_VERDICTS) - set(EVOLVE_FLAGS))
    if unknown:
        print(f"error: unknown --fail-on name(s): {', '.join(unknown)}; choose from "
              f"{', '.join(EVOLVE_VERDICTS + EVOLVE_FLAGS)}", file=sys.stderr)
        return 2
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The last step as an ordinary runs batch, so the Story, Evidence and
    # Training views read "what just changed", with the parent as A and the
    # child as B whatever the names sort to; then every lineage section
    # attached to the aggregate — the comparison too when --against names
    # other lineages. One function owns that, so the command and the
    # library cannot drift apart.
    against = [a for a in (getattr(args, "against", None) or []) if a]
    batch = lineage_batch(lineage, warn=lambda m: print(f"warning: {m}", file=sys.stderr),
                          metric=args.metric, samples=args.samples, against=against,
                          layout=args.layout, threshold=getattr(args, "threshold", None))
    reports, agg = batch["reports"], batch["aggregate"]
    if batch["pair"] is None:
        print("warning: fewer than two generations carry traces; no pair report is written", file=sys.stderr)
    elif batch["names"]:
        print(f"Last step: A={batch['names'][0]}  B={batch['names'][1]}")
    evolution = agg["evolution"]
    comparison = agg.get("evolution_compare")

    # no pair report means no page: the page is the pair
    write_outputs(out_dir, reports, agg, template_from(args), html=bool(reports))
    if not reports:
        print("warning: no pair report, so report.html is not rendered", file=sys.stderr)

    _print_evolution(evolution)
    _print_harness(agg.get("harness_evolution"))
    if comparison is not None:
        _print_evolution_compare(comparison)
    hits = fail_on(evolution, names) if names else []
    if hits:
        print("fail-on: " + ", ".join(f"step {i} {name}" for i, name in hits))
        return 1
    return 0


def _print_evolution(ev: dict) -> None:
    """The lineage summary in the CLI voice: one line per step, then best,
    recommended and the advisory."""
    gens = ev["generations"]
    print(f"Lineage: {ev['family'] or '?'}  {len(gens)} generation(s) [{ev['order_basis']}]")
    for g in gens:
        iqm = g["iqm_by_task"]
        band = (f"{iqm['point']:+.2f} [{iqm['lo']:+.2f}, {iqm['hi']:+.2f}]" if iqm["point"] is not None else "n/a")
        print(f"  {g['id']:<6} {g['episodes_n']:>3} episode(s)  passes {g['passes']}/{g['episodes_n']}  "
              f"IQM {band}" + (f"  ({g['reason']})" if g["reason"] else ""))
    if ev["steps"]:
        print("Steps:")
    for s in ev["steps"]:
        e = s["effect"]
        bits = [f"{s['from']} → {s['to']}  {s['mechanism'] or '?'}  {s['diff']['summary']}"]
        if e["measurable"]:
            imp, iqm = e["improvement"], e["iqm"]
            bits.append(f"P(improve) {imp['point']:.2f} [{imp['lo']:.2f}, {imp['hi']:.2f}]")
            bits.append(f"IQM {iqm['delta']:+.1f}")
            bits.append(s["verdict"] + (f": return {s['gaming']['return_delta']:+.2f}, passes "
                                        f"{s['gaming']['pass_delta']:+.2f}" if s["verdict"] == "gamed" else ""))
        else:
            bits.append(f"unmeasurable: {e['reason']}")
        touched = s["diff"]["protected_touched"] + [c["path"] for c in s["protected_episodes"]
                                                    if c["direction"] == "weakened" and c["path"] not in s["diff"]["protected_touched"]]
        if touched:
            bits.append("touched " + ", ".join(touched))
        if s["flags"]:
            bits.append("flags: " + ", ".join(s["flags"]))
        print("  " + " · ".join(bits))
    best, rec = ev["best"], ev["recommended"]
    print(f"Best: {best['id']}" + (f" (task-balanced IQM {best['iqm']:+.2f})" if best.get("iqm") is not None else "")
          + f" — {best['why']}")
    print(f"Recommended: {rec['id']}" + ("" if rec["is_last"] or rec["id"] is None else " (not the last generation)")
          + f" — {rec['why']}")
    tr = ev["trajectory"]
    if tr.get("steps"):
        print(f"Kept on noise: {tr['accepted_on_noise']} of {tr['steps']} step(s)"
              + (f" ({', '.join(tr['noisy_steps'])})" if tr["noisy_steps"] else ""))
    print(f"Integrity: {ev['integrity']['reading']}")
    print(f"Advisory: {ev['advisory']}")


def _print_harness(h) -> None:
    """The harness beside the agent: which steps changed reasoning and
    which changed scaffold, whether the delta may be attributed at all,
    and where the scaffold carried the run. Printed because a finding
    nobody reads is not a finding; the JSON carries the rest."""
    if not isinstance(h, dict):
        return
    if not h.get("measurable"):
        print(f"Harness: not readable — {h.get('reason')}")
        return
    s = h["summary"]
    kinds = ", ".join(f"{n} {k}" for k, n in s["by_kind"].items() if n)
    print(f"Harness: {s['generations_fingerprinted']}/{s['generations']} generation(s) fingerprinted; "
          f"steps {kinds or 'none changed an artifact'}")
    shape = [(len(s["attributable"]), "attributable"), (len(s["confounded"]), "confounded"),
             (len(s["assumed"]), "assumed")]
    print("  Attribution: " + " · ".join(f"{n} {name}" for n, name in shape if n))
    for row in h["steps"]:
        if row["attribution"]["status"] == "confounded":
            print(f"    {row['from']}→{row['to']} confounded: "
                  + ", ".join(f"{c['what']} {c['from']} → {c['to']}" for c in row["harness"]["changes"]))
    if s["absorbed"]:
        for row in h["steps"]:
            a = row["absorption"]
            if a.get("flag"):
                print(f"  Scaffold carried {row['from']}→{row['to']}: pass rate "
                      f"{a['pass_rate']['from']:+.2f} → {a['pass_rate']['to']:+.2f} while each pass cost "
                      f"{a['steps_per_pass']['from']['point']:.1f} → {a['steps_per_pass']['to']['point']:.1f} "
                      f"steps — a gain from the scaffold, which does not transfer with the agent")
    print(f"  {s['reading']}")


def _print_evolution_compare(cmp: dict) -> None:
    """The comparison in the CLI voice: the four axes, one line each, then
    the race, the process tallies and the verdict."""
    print("Comparison: " + " vs ".join(f"{ln['label']} ({ln['generations_n']} gen, {ln['episodes_n']} ep)"
                                       for ln in cmp["lineages"]))
    if not cmp["measurable"]:
        print(f"  not readable: {cmp['reason']}")
        return
    tasks = cmp["tasks"]
    only = {k: v for k, v in tasks["only"].items() if v}
    print(f"  Tasks: {len(tasks['shared'])} shared" + ("; excluded " + "; ".join(
        f"{k} {', '.join(v)}" for k, v in sorted(only.items())) if only else ""))
    for axis in ("peak", "final"):
        blk = cmp[axis]
        if not blk["measurable"]:
            print(f"  {axis.capitalize():<9}{blk['reason']}")
            continue
        imp, m = blk["improvement"], blk["metric"]
        print(f"  {axis.capitalize():<9}{blk['a']['label']} {blk['a']['id']} vs {blk['b']['label']} {blk['b']['id']}  "
              f"P(b > a) {imp['point']:.2f} [{imp['lo']:.2f}, {imp['hi']:.2f}]  {cmp['metric']} "
              f"{m['a']:+.2f} vs {m['b']:+.2f}  → " + (blk["separates"] or "no separation"))
    race = cmp["race"]
    if race["measurable"]:
        th = race["threshold"]
        reached = ", ".join(f"{k} " + (f"{r['id']} @{r['episodes_cum']} ep" if r else "never")
                            for k, r in sorted(race["reached"].items()))
        print(f"  Learning threshold {th['value']:+.2f} ({th['source']}); reached: {reached}  → "
              + (cmp["verdict"]["learning"] or "no separation"))
    else:
        print(f"  Learning {race['reason']}")
    for label, pr in sorted(cmp["process"].items()):
        if not pr["measurable"]:
            print(f"  Process  {label}: {pr['reason']}")
            continue
        ret = pr["retention"]
        best = pr["best_paying_mechanism"]
        print(f"  Process  {label}: {pr['steps']} step(s)  gamed {pr['gamed']}  forgot {pr['forgot']}  traded "
              f"{pr['traded']}  on-noise {pr['accepted_on_noise']}  protected {pr['protected_touched']}  over-budget "
              f"{pr['over_budget']}  collapsed {pr['collapsed']}  retention "
              f"{len(ret['solved_at_last'])}/{ret['ever_solved_n']}"
              + (f"  best mechanism {best} (n={pr['mechanisms'][best]['steps']}, "
                 f"Δ {pr['mechanisms'][best]['mean_delta']:+.2f})" if best else ""))
    v = cmp["verdict"]
    print(f"  Verdict  peak {v['peak'] or '—'} · final {v['final'] or '—'} · learning {v['learning'] or '—'} · "
          f"process {v['process'] or '—'}  → " + (cmp["verdict"]["process_basis"] if v["process"] else v["process_basis"]))
    print(f"  Advisory: {cmp['advisory']}")
