"""The ``self-evolve`` command: real agents, evals that judge them, and a harness that acts on what they find.

    agentdiff self-evolve --task tasks.json --agent haiku                  three generations, two runs a task
    agentdiff self-evolve --task tasks.json --agent haiku --generations 5 --runs 3 -o evo/
    agentdiff self-evolve --task tasks.json --agent haiku -o evo/          again: continues from evo/ledger.json
    agentdiff self-evolve --task tasks.json --agent haiku --hub http://127.0.0.1:8790   every run, live, on a hub

Each generation the agents run the tasks under the current harness and
each run's check grades it; the evolving eval suite meets those runs;
the eval that caught the most failures names a change the harness can
make (an instruction, a denied tool, a turn cap); the changed harness
runs the same tasks against the current one; the change is kept or
reverted on the counts; and a kept harness's runs are what the evals
meet next. Writes ``self-evolve.json``, ``SELF_EVOLVE.md``, the harness
as it ended (``harness.json``), the eval suite (``evals/evolve-evals.json``)
and the ledger the next call continues from. See :mod:`agentdiff.selfevolve`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

__all__ = ["register", "run"]


def register(subparsers) -> None:
    from ..rleval import TARGETS
    p = subparsers.add_parser(
        "self-evolve", help="real agents under a harness that evolves: run, let the evolving evals judge, change "
                            "the harness where an eval caught failures, test the change paired, keep or revert it, "
                            "and carry on with what the change produced")
    p.add_argument("--task", required=True, metavar="FILE", help="tasks JSON, as for duel (each with a check)")
    p.add_argument("--agent", action="append", default=[], metavar="SPEC",
                   help="an agent, as for duel (haiku, sonnet, claude:MODEL, codex:MODEL); default haiku")
    p.add_argument("--generations", type=int, default=3, help="rounds to go (default 3)")
    p.add_argument("--runs", type=int, default=2, help="runs of each task per arm (default 2)")
    p.add_argument("--target", default="failure", choices=sorted(TARGETS), help="what an eval must catch")
    p.add_argument("--patience", type=int, default=2, help="generations an eval may catch nothing before it retires")
    p.add_argument("-o", "--output", default="self-evolve-out", metavar="DIR")
    p.add_argument("--fresh", action="store_true", help="start over instead of continuing from DIR/ledger.json")
    p.add_argument("--budget-tokens", type=int, default=None, help="stop a run past this many tokens")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds per run (default 900)")
    p.add_argument("--check-timeout", type=float, default=600.0, help="seconds per check (default 600)")
    p.add_argument("--claude-bin", default=None, help="path to the claude binary")
    p.add_argument("--codex-bin", default=None, help="path to the codex binary")
    p.add_argument("--hub", default=None, metavar="URL", help="stream every run to a hub as it goes "
                                                               "($AGENTDIFF_HUB_TOKEN); a hub serving DIR needs no flag")
    p.add_argument("--dry-run", action="store_true", help="say what would run, and run nothing")
    p.set_defaults(func=run)


def _markdown(result: dict) -> str:
    out = ["# A harness that evolves with its evals", "", result["narrative"], "",
           "| generation | harness | failed | evals born | evals retired | change tried | verdict | passed: current → changed |",
           "|---|---|---|---|---|---|---|---|"]
    for g in result["lineage"]:
        a = g.get("action") or {}
        t = a.get("test") or {}
        ev = g.get("evals") or {}
        pc, pn = (t.get("passed") or {}).get("current"), (t.get("passed") or {}).get("changed")
        out.append(f"| {g['generation']} | v{g['harness']['version']} | {g['failed']}/{g['runs']} | "
                   f"{', '.join(ev.get('born') or []) or '—'} | {', '.join(ev.get('retired') or []) or '—'} | "
                   f"{(a.get('remedy') or {}).get('id', '—')} | {t.get('verdict', '—')} | "
                   f"{f'{pc[0]}/{pc[1]} → {pn[0]}/{pn[1]}' if pc and pn else '—'} |")
    h = result["harness"]
    out += ["", "## The harness now", ""]
    out += [f"- instruction: {i}" for i in h["instructions"]] or ["- nothing added: the agent as it ships"]
    out += [f"- denied tool: {d}" for d in h["deny_tools"]]
    if h.get("max_turns"):
        out.append(f"- at most {h['max_turns']} turns")
    out += ["", f"Stopped: {result['stop']}.", "",
            "A change is kept when it wins more tasks than it loses (pass rate per task, same tasks, same runs) "
            "and no task goes from always passing to always failing. Every number above is a count of graded runs.",
            ""]
    return "\n".join(out)


def run(args: argparse.Namespace) -> int:
    from ..selfevolve import load_ledger, self_evolve, visible_check, write_ledger
    from .duel import _tasks
    from ..harness.vendors import parse_spec, preflight
    try:
        tasks = _tasks(argparse.Namespace(task=args.task, workspace=None, check=None, prompt=None, id="task"))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ungraded = [t["id"] for t in tasks if not t.get("check")]
    if ungraded:
        print(f"error: every task needs a check, so a run can be graded; none for {', '.join(ungraded)}",
              file=sys.stderr)
        return 2
    try:
        specs = [parse_spec(a) for a in (args.agent or ["haiku"])]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for s in specs:
        if s.kind == "claude" and args.claude_bin:
            s.binary = args.claude_bin
        if s.kind == "codex" and args.codex_bin:
            s.binary = args.codex_bin
    out = Path(args.output)
    ledger_path = out / "ledger.json"
    try:
        ledger = None if args.fresh else load_ledger(ledger_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    per_arm = len(tasks) * len(specs) * args.runs
    print(f"self-evolve: {len(tasks)} task(s) × {len(specs)} agent(s) × {args.runs} run(s) = {per_arm} run(s) per arm; "
          f"up to two arms a generation, {args.generations} generation(s)"
          + (f"; continuing from {ledger_path} ({len(ledger['generations'])} generation(s) so far)" if ledger else ""),
          flush=True)
    if args.dry_run:
        for s in specs:
            pf = preflight(s)
            print(f"  {s.agent}: {s.kind} {s.model or '(default model)'}: "
                  + ("found" if pf.get("binary") or s.binary else "CLI NOT FOUND"))
        return 0
    from ..harness.selfevolve import UNSUPPORTED, vendor_arm
    refuse = tuple(sorted({k for s in specs for k in UNSUPPORTED.get(s.kind, ())}))
    streamer = None
    if args.hub:
        from ..harness.hub_server import TraceStreamer
        token = os.environ.get("AGENTDIFF_HUB_TOKEN")
        if not token:
            print("error: --hub needs $AGENTDIFF_HUB_TOKEN (`agentdiff hub` prints it)", file=sys.stderr)
            return 2
    out.mkdir(parents=True, exist_ok=True)
    arm = vendor_arm(tasks, specs, out, runs=args.runs, budget_tokens=args.budget_tokens, timeout_s=args.timeout,
                     check_timeout_s=args.check_timeout)
    if args.hub:
        # every arm writes its own traces directory: stream each as it appears
        inner = arm
        streamers = []

        def arm(harness, label):  # noqa: F811 — the same arm, streamed
            d = out / label / "traces"
            d.mkdir(parents=True, exist_ok=True)
            s = TraceStreamer(d, args.hub, token, interval=0.5).start()
            streamers.append(s)
            try:
                return inner(harness, label)
            finally:
                s.stop()
    try:
        result = self_evolve(arm, generations=args.generations, target=args.target, ledger=ledger,
                             patience=args.patience, check=visible_check([t["check"] for t in tasks]),
                             refuse_knobs=refuse, on_progress=lambda s: print(s, flush=True))
    except KeyboardInterrupt:
        print("\ninterrupted: the ledger keeps every generation that finished", file=sys.stderr)
        return 130
    write_ledger(ledger_path, result["ledger"])
    (out / "self-evolve.json").write_text(json.dumps({k: v for k, v in result.items() if k != "ledger"}, indent=1,
                                                     default=str), encoding="utf-8")
    (out / "harness.json").write_text(json.dumps(result["harness"], indent=1), encoding="utf-8")
    (out / "SELF_EVOLVE.md").write_text(_markdown(result), encoding="utf-8")
    if result.get("evals"):
        (out / "evals").mkdir(exist_ok=True)
        (out / "evals" / "evolve-evals.json").write_text(json.dumps(result["evals"], indent=1, default=str),
                                                         encoding="utf-8")
    print(f"\n{result['narrative']}\n\nwrote {out / 'self-evolve.json'}, {out / 'SELF_EVOLVE.md'}, "
          f"{out / 'harness.json'}; the next call continues from {ledger_path}")
    if args.hub:
        sent = sum(s.sent for s in streamers)
        print(f"hub: sent {sent} copy(ies) of the runs to {args.hub.rstrip('/')}/live")
    return 0
