"""The ``self-evolve`` command: real agents, evals that judge them, and a harness that acts on what they find.

    agentdiff self-evolve --task tasks.json --agent haiku                  three generations, two runs a task
    agentdiff self-evolve --task tasks.json --agent haiku --generations 5 --runs 3 -o evo/
    agentdiff self-evolve --task tasks.json --agent haiku -o evo/          again: continues from evo/ledger.json
    agentdiff self-evolve --task tasks.json --agent haiku --hub http://127.0.0.1:8790   every run, live, on a hub
    agentdiff self-evolve --demo                                           six tasks it carries, graders held out
    agentdiff self-evolve --adopt evo/ --into ~/my-project                 use the evolved harness in Claude Code

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
    p.add_argument("--task", default=None, metavar="FILE", help="tasks JSON, as for duel (each with a check)")
    p.add_argument("--demo", action="store_true",
                   help="instead of --task: the six tasks it carries, graders held out (docs/SELF_EVOLVE.md), "
                        "written to ~/.agentdiff/self-evolve, where the hub shows them")
    p.add_argument("--agent", action="append", default=[], metavar="SPEC",
                   help="an agent, as for duel (haiku, sonnet, claude:MODEL, codex:MODEL); default haiku")
    p.add_argument("--generations", type=int, default=3, help="rounds to go (default 3)")
    p.add_argument("--runs", type=int, default=2, help="runs of each task per arm (default 2)")
    p.add_argument("--target", default="failure", choices=sorted(TARGETS), help="what an eval must catch")
    p.add_argument("--patience", type=int, default=2, help="generations an eval may catch nothing before it retires")
    p.add_argument("-o", "--output", default=None, metavar="DIR",
                   help="default self-evolve-out, or ~/.agentdiff/self-evolve/demo-AGENT with --demo")
    p.add_argument("--fresh", action="store_true", help="start over instead of continuing from DIR/ledger.json")
    p.add_argument("--budget-tokens", type=int, default=None, help="stop a run past this many tokens")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds per run (default 900)")
    p.add_argument("--check-timeout", type=float, default=600.0, help="seconds per check (default 600)")
    p.add_argument("--claude-bin", default=None, help="path to the claude binary")
    p.add_argument("--codex-bin", default=None, help="path to the codex binary")
    p.add_argument("--hub", default=None, metavar="URL", help="stream every run to a hub as it goes "
                                                               "($AGENTDIFF_HUB_TOKEN); a hub serving DIR needs no flag")
    p.add_argument("--dry-run", action="store_true", help="say what would run, and run nothing")
    p.add_argument("--adopt", default=None, metavar="DIR",
                   help="instead of running: use the harness DIR ended with in your Claude Code (--into PROJECT): "
                        "its instructions in CLAUDE.md, its denied tools in .claude/settings.local.json")
    p.add_argument("--unadopt", action="store_true", help="take an adopted harness out of --into PROJECT again")
    p.add_argument("--into", default=".", metavar="PROJECT", help="--adopt, --unadopt: the project (default here)")
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


def adopt_text(got: dict, project: Path) -> list:
    """What ``--adopt`` did, as lines: what it wrote, and what it could not."""
    if got.get("nothing"):
        return [got["why"]]
    out = [f"adopted into {project}:"]
    if got.get("instructions"):
        out.append(f"  {got['instructions']} instruction(s) in {project / 'CLAUDE.md'}, between the agentdiff "
                   "harness markers. The paired test gave them with --append-system-prompt; CLAUDE.md gives the "
                   "same words as project instructions, so watch the next sessions to see they still help.")
    if got.get("deny"):
        out.append(f"  denied {', '.join(got['deny'])} in {project / '.claude' / 'settings.local.json'}"
                   + ("" if got.get("deny_added") == got.get("deny") else " (some were denied already)"))
    if got.get("max_turns"):
        out.append(f"  a turn cap has no setting: start it with `claude --max-turns {got['max_turns']}`")
    out.append(f"  take it out again: agentdiff self-evolve --unadopt --into {project}")
    return out


def _adopt(args: argparse.Namespace) -> int:
    from ..adopt import adopt, unadopt
    project = Path(os.path.expanduser(args.into)).resolve()
    if args.unadopt:
        got = unadopt(project)
        print(f"took the adopted harness out of {project}: {', '.join(got['removed']) or 'its record'}" if got
              else f"no adopted harness in {project}")
        return 0
    src = Path(os.path.expanduser(args.adopt))
    path = src / "harness.json" if src.is_dir() else src
    try:
        harness = json.loads(path.read_text(encoding="utf-8"))
        got = adopt(harness, project, source=str(src.resolve()))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n".join(adopt_text(got, project)))
    return 0


def _stop(signum, frame):
    raise KeyboardInterrupt


def _write_result(out: Path, result: dict) -> None:
    """self-evolve.json, harness.json, SELF_EVOLVE.md and the eval suite: after every generation, and at the end."""
    import tempfile
    body = json.dumps({k: v for k, v in result.items() if k != "ledger"}, indent=1, default=str)
    # written whole, then moved into place: the hub never reads half a lineage
    fd, tmp = tempfile.mkstemp(dir=out, prefix=".self-evolve.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, out / "self-evolve.json")
    (out / "harness.json").write_text(json.dumps(result["harness"], indent=1), encoding="utf-8")
    (out / "SELF_EVOLVE.md").write_text(_markdown(result), encoding="utf-8")
    if result.get("evals"):
        (out / "evals").mkdir(exist_ok=True)
        (out / "evals" / "evolve-evals.json").write_text(json.dumps(result["evals"], indent=1, default=str),
                                                         encoding="utf-8")


class Progress:
    """``progress.json``: where a running self-evolve is, for the hub's Evolve page. The generation and arm, the
    runs of that arm done out of how many, the change being tested, the last lines it said, and whether it is
    still going (its pid), stopped, failed or done."""

    LINES = 8

    def __init__(self, out: Path, *, generations: int, per_arm: int, agents: list, tasks: list) -> None:
        import threading
        import time
        self.path = out / "progress.json"
        self.lock = threading.Lock()
        self.data = {"kind": "self-evolve-progress", "status": "running", "pid": os.getpid(),
                     "started_at": time.time(), "updated_at": time.time(), "generations": generations,
                     "per_arm": per_arm, "agents": agents, "tasks": tasks, "generation": None, "arm": None,
                     "testing": None, "runs_done": 0, "arms_done": 0, "lines": [], "end": None}

    def arm(self, label: str, version: int) -> None:
        with self.lock:
            if self.data["arm"]:
                self.data["arms_done"] += 1
            gen = label.split("-")[0]
            # the second arm of a generation runs the harness with the change being tested
            self.data.update(generation=gen, arm=label, runs_done=0,
                             testing=self.data["testing"] if gen == self.data["generation"] else None)
        self.write()

    def run_done(self, record: dict) -> None:
        with self.lock:
            self.data["runs_done"] += 1
        self.write()

    def say(self, line: str) -> None:
        with self.lock:
            self.data["lines"] = (self.data["lines"] + [line])[-self.LINES:]
            if "; testing: " in line:  # "g0: 6 of 6 failure(s) caught by …; testing: <the change>"
                self.data["testing"] = line.split("; testing: ", 1)[1]
        self.write()

    def end(self, status: str, why: str = "") -> None:
        with self.lock:
            self.data.update(status=status, end=why or None)
        self.write()

    def write(self) -> None:
        import tempfile
        import time
        with self.lock:
            self.data["updated_at"] = time.time()
            body = json.dumps(self.data, indent=1)
            try:
                fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".progress.", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                os.replace(tmp, self.path)
            except OSError:
                pass  # progress is for the page; the run goes on without it


def _latest_passing_arm(out: Path, result: dict):
    """The arm of the harness as it ended that has a passing run: what `apply` keeps."""
    lineage = result.get("lineage") or []
    if not lineage:
        return None
    version = (result.get("harness") or {}).get("version", 0)
    for g in reversed(lineage):
        for label in (f"{g['generation']}-h{version}",):
            d = out / label
            for rec in sorted((d / "records").glob("*.json")) if d.is_dir() else []:
                try:
                    if (json.loads(rec.read_text(encoding="utf-8")).get("check") or {}).get("passed"):
                        return d
                except (OSError, ValueError):
                    continue
    return None


def _demo_source() -> Path:
    """The demo tasks as a build carries them, else as the repository has them."""
    here = Path(__file__).resolve().parents[1]
    for d in (here / "_demo" / "selfevolve", here.parent / "demo" / "selfevolve"):
        if (d / "tasks").is_dir() and (d / "hidden").is_dir():
            return d
    raise OSError("this install carries no demo tasks; a downloaded build or a clone of the repository does")


def demo_tasks(dest: Path) -> Path:
    """The six demo tasks copied to ``dest`` (once), and a tasks.json whose checks name the held-out graders
    there by absolute path, run by the Python on this machine: the graders are Python tests."""
    import shlex
    import shutil
    import subprocess
    python = shutil.which("python3") or shutil.which("python")
    if not python:
        raise OSError("the demo's graders are Python tests, and there is no python3 on PATH to run them")
    src = _demo_source()
    for part in ("tasks", "hidden"):
        if not (dest / part).is_dir():
            shutil.copytree(src / part, dest / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    join = subprocess.list2cmdline if os.name == "nt" else (lambda a: " ".join(shlex.quote(x) for x in a))
    from ..selfevolve import DEMO_TASKS
    tasks = []
    for tid, prompt in DEMO_TASKS:
        hidden = str((dest / "hidden" / tid).resolve())
        tasks.append({"id": tid, "workspace": f"tasks/{tid}", "prompt": prompt,
                      "check": join([python, "-m", "unittest", "discover", "-s", hidden, "-t", hidden])})
    out = dest / "tasks.json"
    out.write_text(json.dumps({"tasks": tasks}, indent=1) + "\n", encoding="utf-8")
    return out


def run(args: argparse.Namespace) -> int:
    from ..selfevolve import load_ledger, self_evolve, visible_check, write_ledger
    from .duel import _tasks
    from ..harness.vendors import parse_spec, preflight
    if args.adopt or args.unadopt:
        return _adopt(args)
    if args.demo:
        from .hub import _home
        name = "demo-" + "-".join(a.replace(":", "-").replace("/", "-") for a in (args.agent or ["haiku"]))
        args.output = args.output or str(_home() / "self-evolve" / name)
        try:
            args.task = str(demo_tasks(_home() / "self-evolve" / "demo-tasks"))
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"demo: six tasks, each graded by tests the agent is not given ({Path(args.task).parent / 'hidden'}); "
              f"writing to {args.output}, where `agentdiff hub --claude-code` shows it", flush=True)
    elif not args.task:
        print("error: give the tasks with --task FILE, or --demo for the six it carries", file=sys.stderr)
        return 2
    args.output = args.output or "self-evolve-out"
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
    progress = Progress(out, generations=args.generations, per_arm=per_arm,
                        agents=[s.agent for s in specs], tasks=[t["id"] for t in tasks])
    arm = vendor_arm(tasks, specs, out, runs=args.runs, budget_tokens=args.budget_tokens, timeout_s=args.timeout,
                     check_timeout_s=args.check_timeout, on_done=progress.run_done)
    counted = arm

    def arm(harness, label):  # noqa: F811 — the same arm, counted run by run for progress.json
        progress.arm(label, harness.version)
        return counted(harness, label)
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

    def said(line: str) -> None:
        print(line, flush=True)
        progress.say(line)

    def generation(partial: dict) -> None:
        # a generation that finished is kept, on disk, before the next one starts
        write_ledger(ledger_path, partial["ledger"])
        _write_result(out, partial)
        progress.write()
    import signal
    for name in ("SIGTERM", "SIGBREAK"):  # the hub's Stop (SIGBREAK on Windows): end the way Ctrl-C does
        try:
            signal.signal(getattr(signal, name), _stop)
        except (ValueError, OSError, AttributeError):
            pass
    progress.write()
    try:
        result = self_evolve(arm, generations=args.generations, target=args.target, ledger=ledger,
                             patience=args.patience, check=visible_check([t["check"] for t in tasks]),
                             refuse_knobs=refuse, on_progress=said, on_generation=generation)
    except KeyboardInterrupt:
        progress.end("stopped")
        print("\ninterrupted: the ledger keeps every generation that finished", file=sys.stderr)
        return 130
    except Exception as exc:
        progress.end("failed", str(exc))
        raise
    write_ledger(ledger_path, result["ledger"])
    _write_result(out, result)
    progress.end("done", result["stop"])
    print(f"\n{result['narrative']}\n\nwrote {out / 'self-evolve.json'}, {out / 'SELF_EVOLVE.md'}, "
          f"{out / 'harness.json'}; the next call continues from {ledger_path}")
    keep = _latest_passing_arm(out, result)
    if keep:
        print(f"keep a change: agentdiff apply --dir {keep}   (--dry-run shows it first)")
    if args.hub:
        sent = sum(s.sent for s in streamers)
        print(f"hub: sent {sent} copy(ies) of the runs to {args.hub.rstrip('/')}/live")
    return 0
