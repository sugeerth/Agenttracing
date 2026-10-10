"""The ``duel`` command: Codex CLI and Claude Code on the same task, side by side, traced.

Each agent works in its own copy of the same workspace, launched together;
every line either CLI prints is stamped and kept; the operator's check
grades each run; the report opens with what was and was not equal. Then
the same traces go through ``batch``, so the whole page — the verdict, the
strip, the lessons, the process checks — reads the two vendors the way it
reads any pair.

Talks to the vendors' APIs through their own command-line tools, with the
keys the operator set in the environment. The harness is imported inside
the command, so the analysis engine stays network-free.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

__all__ = ["register", "run"]

#: characters of each run's patch carried onto the page
PATCH_HEAD = 6000


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "duel", help="run Codex CLI and Claude Code on the same task, each in its own copy of the workspace, "
                     "side by side; trace every event, grade each run with your check, and write a fair "
                     "report and the page (keys from OPENAI_API_KEY / CODEX_API_KEY and ANTHROPIC_API_KEY)")
    parser.add_argument("what", nargs="?", default=None, metavar="PROMPT",
                        help='the task in words: `agentdiff duel "Fix the failing test"` runs it on this '
                             "directory, with the check detected and the page live")
    parser.add_argument("--task", default=None, metavar="FILE",
                        help="task JSON: {id, prompt, workspace, check} or {\"tasks\": [...]}")
    parser.add_argument("--prompt", default=None, help="the task, when there is no --task file")
    parser.add_argument("--workspace", default=None, metavar="DIR",
                        help="the directory each agent starts from (copied per run; never modified)")
    parser.add_argument("--check", default=None, metavar="CMD",
                        help="shell command run in each agent's workspace after it stops; exit 0 is a pass "
                             "(default with a prompt: the project's test command, detected)")
    parser.add_argument("--no-check", action="store_true", help="record the runs ungraded; detect nothing")
    parser.add_argument("--no-baseline", action="store_true",
                        help="skip running the check once before any work (by default it runs, so a task "
                             "whose check already passes is reported as measuring nothing)")
    parser.add_argument("--id", default="task", help="task id, when there is no --task file")
    parser.add_argument("--agent", action="append", default=None, metavar="[NAME=]VENDOR[:MODEL]",
                        help="twice: a model (opus, sonnet, haiku, claude-..., gpt-..., o3), or codex[:MODEL], "
                             "claude[:MODEL]. Default: codex and claude, or with only Claude Code installed, "
                             "its haiku and sonnet models")
    parser.add_argument("--runs", type=int, default=1, help="runs per agent per task (default 1)")
    parser.add_argument("--budget-tokens", type=int, default=None, metavar="N",
                        help="token budget per run (input incl. cached + output); enforced while running "
                             "where the stream reports usage as it goes, checked afterwards where it does not")
    parser.add_argument("--band", type=float, default=0.10,
                        help="two runs are budget-matched within this share of the larger (default 0.10)")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds per run (default 1800)")
    parser.add_argument("--check-timeout", type=float, default=600.0, help="seconds for the check (default 600)")
    parser.add_argument("--sequential", action="store_true",
                        help="run the agents one after the other instead of side by side")
    parser.add_argument("--sandbox", default="workspace-write",
                        choices=["read-only", "workspace-write", "danger-full-access"],
                        help="Codex sandbox (default workspace-write)")
    parser.add_argument("--claude-permission-mode", default="acceptEdits",
                        help="Claude Code permission mode (default acceptEdits, with Bash allowed)")
    parser.add_argument("--use-my-config", action="store_true",
                        help="load each CLI's user settings; by default they are left out so the agents "
                             "measured are the vendors', not a customised one")
    parser.add_argument("--price", action="append", default=[], metavar="AGENT=IN,CACHED,OUT",
                        help="USD per million tokens for an agent whose CLI reports no cost; labelled as yours")
    parser.add_argument("--codex-bin", default=None, help="path to the codex binary")
    parser.add_argument("--claude-bin", default=None, help="path to the claude binary")
    parser.add_argument("--dry-run", action="store_true",
                        help="check both CLIs and credentials and print the commands, without running")
    parser.add_argument("--from", dest="from_dir", default=None, metavar="DIR",
                        help="rebuild the report and page from a finished duel's records; runs nothing")
    parser.add_argument("--quiet", action="store_true", help="no live event lines")
    parser.add_argument("--events", action="store_true",
                        help="print every action as a line (default at a terminal: one status line per agent)")
    parser.add_argument("--live", action="store_true", default=None,
                        help="serve the page while the agents work: both stream into a race at "
                             "http://HOST:PORT/ (localhost only). Default: on in a terminal")
    parser.add_argument("--no-live", dest="live", action="store_false", help="no live page")
    parser.add_argument("--no-open", action="store_true", help="--live: do not open a browser")
    parser.add_argument("--hub", default=None, metavar="URL",
                        help="stream every run to an agentdiff hub as it goes (in-band: sizes, times, outcomes; "
                             "the token from $AGENTDIFF_HUB_TOKEN). A hub serving this directory needs no flag")
    parser.add_argument("--host", default="127.0.0.1",
                        help="--live: address to bind (default 127.0.0.1); anything else needs --allow-remote")
    parser.add_argument("--allow-remote", action="store_true",
                        help="--live: serve beyond this machine behind a random token printed once")
    parser.add_argument("--max-stream-mb", type=float, default=256.0,
                        help="stop a run whose output passes this many MB (default 256)")
    parser.add_argument("--port", type=int, default=8765, help="--live: port (default 8765)")
    parser.add_argument("--linger", type=float, default=3.0, metavar="S",
                        help="--live: keep serving this many seconds after the duel ends, while the open page "
                             "takes the final state and stops listening (default 3; -1: until Ctrl-C)")
    parser.add_argument("--template", default=None, help="page template (default the blocks page)")
    parser.add_argument("-o", "--output", default=None, metavar="DIR",
                        help="default duel-out/, or duel-out-2/ and on when it holds an earlier duel")
    parser.set_defaults(func=run)


def _tasks(args) -> list:
    if args.task:
        data = json.loads(Path(args.task).read_text(encoding="utf-8"))
        tasks = data.get("tasks") if isinstance(data, dict) and "tasks" in data else data
        tasks = tasks if isinstance(tasks, list) else [tasks]
        base = Path(args.task).resolve().parent
        out = []
        for t in tasks:
            if not isinstance(t, dict) or not t.get("prompt"):
                raise ValueError("every task needs a prompt")
            ws = Path(t.get("workspace") or args.workspace or ".")
            ws = ws if ws.is_absolute() else (base / ws)
            out.append({"id": str(t.get("id") or "task"), "prompt": str(t["prompt"]),
                        "workspace": str(ws.resolve()), "check": t.get("check") or args.check,
                        "expected": t.get("expected")})
        return out
    if not args.prompt or not args.workspace:
        raise ValueError('give the task in words (agentdiff duel "Fix the failing test"), or --task FILE')
    return [{"id": args.id, "prompt": args.prompt, "workspace": str(Path(args.workspace).resolve()),
             "check": args.check, "expected": None}]


#: directories a test search never walks into
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", ".tox", "dist", "build", "target"}


def detect_check(workspace) -> tuple:
    """``(command, why)``: the test command this project already has, or
    ``(None, why not)``. Read from the files, never by running anything."""
    from ..harness.vendors import OUTPUT_MARKER
    ws = Path(workspace)
    make = ws / "Makefile"
    if make.is_file() and re.search(r"^test\s*:", make.read_text(encoding="utf-8", errors="replace"), re.M):
        return "make test", "the Makefile has a test target"
    pkg = ws / "package.json"
    if pkg.is_file():
        try:
            script = ((json.loads(pkg.read_text(encoding="utf-8")) or {}).get("scripts") or {}).get("test")
        except ValueError:
            script = None
        # npm's placeholder fails on purpose; it is not a test command
        if script and "no test specified" not in script:
            return "npm test --silent", "package.json has a test script"
    if (ws / "Cargo.toml").is_file():
        return "cargo test -q", "a Cargo project"
    if (ws / "go.mod").is_file():
        return "go test ./...", "a Go module"
    found = False
    for root, dirs, files in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")
                   and not (Path(root) / d / OUTPUT_MARKER).is_file()]
        if any(re.fullmatch(r"test_.*\.py|.*_test\.py", f) for f in files):
            found = True
            break
        if len(Path(root).relative_to(ws).parts) >= 3:   # tests live near the top
            dirs[:] = []
    if found:
        import importlib.util
        if importlib.util.find_spec("pytest") is not None:
            return "python3 -m pytest -q", "Python tests, and pytest is installed"
        return "python3 -m unittest -q", "Python tests (pytest is not installed)"
    return None, "no test command found (no Makefile test target, package.json test, Cargo, Go or Python tests)"


def default_agents(codex_bin: Optional[str] = None, claude_bin: Optional[str] = None) -> Optional[list]:
    """The two agents to run when none are named: Codex and Claude Code when
    both are installed (or neither, so the preflight says what is missing);
    with Claude Code alone, two of its models. None with Codex alone: which
    two Codex models to compare is the operator's call."""
    from ..harness.vendors import VendorSpec, _binary
    have_codex = bool(_binary(VendorSpec("codex", binary=codex_bin or "")))
    have_claude = bool(_binary(VendorSpec("claude", binary=claude_bin or "")))
    if have_codex == have_claude:
        return ["codex", "claude"]
    if have_claude:
        return ["haiku=claude:haiku", "sonnet=claude:sonnet"]
    return None


def _free_output(base: str = "duel-out") -> Path:
    """``duel-out``, unless it holds an earlier duel: then the next free
    ``duel-out-N``, so two duels are never read as one."""
    n, path = 1, Path(base)
    while (path / "records").is_dir() and any((path / "records").glob("*.json")):
        n += 1
        path = Path(f"{base}-{n}")
    return path


def _prices(values: list) -> dict:
    out = {}
    for v in values:
        name, _, nums = v.partition("=")
        parts = [float(x) for x in nums.split(",") if x.strip()]
        if len(parts) != 3:
            raise ValueError(f"--price {v!r}: want AGENT=IN,CACHED,OUT")
        out[name.strip()] = {"input": parts[0], "cached_input": parts[1], "output": parts[2]}
    return out


def _say(ev: dict) -> str:
    """One live line for a finished action, or '' for the rest."""
    e = ev["event"]
    kind = e.get("type")
    head = f"[{ev['t']:7.1f}s] {ev['agent']:<12}"
    if kind == "item.completed":
        item = e.get("item") or {}
        it = item.get("type")
        if it == "command_execution":
            return f"{head} $ {str(item.get('command'))[:90]}  → exit {item.get('exit_code')}"
        if it == "file_change":
            return f"{head} edit " + ", ".join(str(c.get("path")) for c in item.get("changes") or [])[:100]
        if it == "agent_message":
            return f"{head} says: {str(item.get('text') or '')[:90]!r}"
    if kind == "turn.completed":
        u = e.get("usage") or {}
        return f"{head} turn done: {u.get('input_tokens', 0):,} in / {u.get('output_tokens', 0):,} out"
    if kind == "assistant":
        for b in (e.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                inp = b.get("input") or {}
                what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                return f"{head} {b.get('name')}: {str(what)[:90]}"
    if kind == "result":
        return f"{head} done: {e.get('num_turns')} turns, ${e.get('total_cost_usd') or 0:.4f}"
    return ""


class _Status:
    """At a terminal: one line, rewritten in place, with each agent's count
    of actions and the latest one, instead of a scroll of every action."""

    def __init__(self) -> None:
        import threading
        self.lock = threading.Lock()
        self.agents: dict = {}
        self.t0 = time.monotonic()

    def update(self, agent: str, line: str) -> None:
        import shutil
        what = line.split(agent, 1)[-1].strip()
        with self.lock:
            n, _ = self.agents.get(agent, (0, ""))
            self.agents[agent] = (n + 1, what)
            width = shutil.get_terminal_size((100, 20)).columns - 1
            share = max(20, (width - 8) // max(1, len(self.agents)))
            parts = [f"{a} {c} · {w}"[:share - 3] for a, (c, w) in sorted(self.agents.items())]
            text = f"{time.monotonic() - self.t0:5.0f}s  " + " │ ".join(parts)
            sys.stdout.write("\r" + text[:width].ljust(width))
            sys.stdout.flush()

    def clear(self) -> None:
        import shutil
        with self.lock:
            sys.stdout.write("\r" + " " * (shutil.get_terminal_size((100, 20)).columns - 1) + "\r")
            sys.stdout.flush()


def _load_records(out: Path) -> list:
    records = []
    for p in sorted((out / "records").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        trace = out / rec["trace"]
        if trace.is_file():
            rec["trajectory"] = json.loads(trace.read_text(encoding="utf-8"))
            records.append(rec)
    return records


def duel_block(out: Path, records: list, band: float) -> tuple:
    """``(report, block)``: the fair report, and the slimmer block the page
    carries (each run's profile, the head of its patch). One function for
    the page written at the end and the page watched while the agents run."""
    from ..duel import duel_report
    report = duel_report(records, band=band)
    slim = {k: v for k, v in report.items() if k != "profiles"}
    slim["runs_detail"] = [{k: v for k, v in p.items() if k not in ("after",)} for p in report.get("profiles") or []]
    # what each run generated, on the page: the head of its patch (the full
    # patch stays in diffs/, named in the record)
    heads = {}
    for r in records:
        patch = out / r.get("patch", "")
        if patch.is_file():
            text = patch.read_text(encoding="utf-8", errors="replace")
            heads[(r["task"], r["agent"], r["run"])] = (text[:PATCH_HEAD], len(text) > PATCH_HEAD, r["patch"])
    for d in slim["runs_detail"]:
        got = heads.get((d["task"], d["agent"], d["run"]))
        if got:
            d["patch_head"], d["patch_truncated"], d["patch_path"] = got
    return report, slim


def _report(out: Path, records: list, band: float, template, quiet: bool) -> int:
    from ..duel import render_markdown
    from . import batch as batch_cmd
    report, slim = duel_block(out, records, band)
    (out / "duel.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "DUEL.md").write_text(render_markdown(report), encoding="utf-8")
    from ..duel import scoreboard
    print("\n" + scoreboard(report))
    print(f"\nthe full reading: {out / 'DUEL.md'}")
    if not report.get("measurable"):
        return 1
    ns = argparse.Namespace(tracesdir=str(out / "traces"), output=str(out / "page"), template=template,
                            golden=None, policy=None, lessons=None, extra_aggregate={"duel": slim})
    # the duel ends on its own reading and where the page is; the corpus
    # triage batch prints goes to a file beside the page, not the terminal
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = batch_cmd.run(ns)
    (out / "page").mkdir(parents=True, exist_ok=True)
    (out / "page" / "triage.txt").write_text(buf.getvalue(), encoding="utf-8")
    if code == 0:
        print(f"page: {out / 'page' / 'report.html'}   (agentdiff open reopens it)")
        winners = sorted({r["agent"] for r in records if (r.get("check") or {}).get("passed") is True})
        if winners:
            print("keep a change: " + "  or  ".join(f"agentdiff apply {a}" for a in winners)
                  + "   (--dry-run shows it first)")
    else:
        print(buf.getvalue()[-2000:])
    return code


def run(args: argparse.Namespace) -> int:
    from ..harness.vendors import parse_spec, preflight, run_duel
    if getattr(args, "what", None):
        if args.prompt or args.task:
            print("error: give the task once: in words, or --prompt, or --task FILE", file=sys.stderr)
            return 2
        args.prompt = args.what
    if not (args.prompt or args.task or args.from_dir) and sys.stdin.isatty():
        # a person at a terminal who gave no task is asked for one
        try:
            args.prompt = input("What should the agents do? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if not args.prompt:
            print("error: no task given", file=sys.stderr)
            return 2
    simple = bool(args.prompt) and not args.task
    if simple and not args.workspace:
        args.workspace = "."
    if simple and args.id == "task":
        # the page names the task by its first words, not "task"
        words = re.findall(r"[a-z0-9]+", args.prompt.lower())[:5]
        args.id = "-".join(words) or "task"
    if getattr(args, "again", None):
        simple = False
    if simple and not args.check and not getattr(args, "no_check", False):
        cmd, why = detect_check(args.workspace)
        args.check = cmd
        print(f"check: {cmd}  ({why}; --check CMD to change)" if cmd else f"check: none — {why}; "
              "the runs are recorded ungraded (--check CMD to grade them)", flush=True)
    if args.live is None:
        # a person at a terminal watches it; a script or a test does not
        args.live = sys.stdout.isatty() and not args.dry_run and not args.from_dir
    out = Path(args.output) if args.output else _free_output()
    if args.from_dir:
        src = Path(args.from_dir)
        records = _load_records(src)
        if not records:
            print(f"error: no records under {src / 'records'}", file=sys.stderr)
            return 2
        return _report(src, records, args.band, args.template, args.quiet)
    try:
        tasks = _tasks(args)
        agents = args.agent or default_agents(args.codex_bin, args.claude_bin)
        if agents is None:
            raise ValueError("only Codex is installed: name two models to compare, "
                             "--agent a=codex:MODEL_A --agent b=codex:MODEL_B")
        specs = [parse_spec(s) for s in agents]
        prices = _prices(args.price)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if len(specs) != 2 or specs[0].agent == specs[1].agent:
        print("error: a duel is two agents with different names, e.g. --agent opus --agent gpt-5 "
              "(NAME=vendor:model to tell two of one model apart)", file=sys.stderr)
        return 2
    for spec in specs:
        if spec.kind == "codex" and args.codex_bin:
            spec.binary = args.codex_bin
        if spec.kind == "claude" and args.claude_bin:
            spec.binary = args.claude_bin
    for t in tasks:
        if not Path(t["workspace"]).is_dir():
            print(f"error: task {t['id']}: workspace {t['workspace']} is not a directory", file=sys.stderr)
            return 2
    checks = [preflight(s) for s in specs]
    for c in checks:
        cred = ", ".join(c["key_env_present"]) or ("a CLI login" if c["login_present"] else
               ("an endpoint the host configured (" + ", ".join(c["endpoint_env_present"]) + ")"
                if c.get("endpoint_env_present") else "none"))
        print(f"{c['agent']:<12} {c['binary'] or '(not found)'}  {c['version']}  credential: {cred}")
    if args.dry_run:
        from ..harness.vendors import _argv
        for s in specs:
            if s.binary or preflight(s)["binary"]:
                argv, facts = _argv(s, preflight(s)["binary"] or s.binary, Path("<workspace copy>"),
                                    not args.use_my_config, args.sandbox, args.claude_permission_mode, None)
                print(f"{s.agent}: {' '.join(argv)}   (prompt on stdin)  [{facts['sandbox']}]")
        return 0 if all(c["ready"] for c in checks) else 1
    missing = [c for c in checks if not c["ready"]]
    if missing:
        for c in missing:
            print(f"error: {c['agent']}: {c['why']}", file=sys.stderr)
        return 2
    if not any(t.get("check") for t in tasks):
        print("note: no --check given: runs are recorded ungraded", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    again = getattr(args, "again", None) or {}
    if not again:
        # what `agentdiff again` needs to add runs to this same comparison
        (out / "plan.json").write_text(json.dumps({
            "tasks": tasks, "agents": agents,
            "options": {k: getattr(args, k) for k in (
                "band", "budget_tokens", "timeout", "check_timeout", "sequential", "sandbox",
                "claude_permission_mode", "use_my_config", "max_stream_mb", "codex_bin", "claude_bin",
                "no_baseline")},
        }, indent=1), encoding="utf-8")
    status = _Status() if (not args.quiet and not args.events and sys.stdout.isatty()) else None

    def on_event(ev):
        if args.quiet:
            return
        line = _say(ev)
        if not line:
            return
        if status is not None:
            status.update(ev["agent"], line)
        else:
            print(line, flush=True)

    def on_baseline(task, b):
        state = ("fails, as a task's check should" if b["passed"] is False else
                 "already passes: a pass after the agents will show nothing")
        print(f"before any work: `{b['command']}` {state} (exit {b['exit_code']}, {b['seconds']:.1f}s)", flush=True)

    def on_done(rec):
        if status is not None:
            status.clear()
        c = rec["check"]
        verdict = "no check" if c["passed"] is None else ("PASS" if c["passed"] else f"FAIL (exit {c['exit_code']})")
        t = rec["trajectory"]["totals"]
        print(f"== {rec['agent']} {rec['task']} {rec['run']}: {verdict}; "
              f"{t['input_tokens'] + t['output_tokens']:,} tokens, {rec['wall_s']:.0f}s, "
              f"{rec['diff']['added']}+/{rec['diff']['removed']}- in {len(rec['diff']['files'])} file(s)"
              + (f"; stopped by {rec['stopped_by']}" if rec.get("stopped_by") else ""), flush=True)

    server = None
    if args.live:
        import threading
        from ..harness.watch import bind_policy, serve
        from .paths import DEFAULT_TEMPLATE
        try:
            token = bind_policy(args.host, args.allow_remote)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        (out / "traces").mkdir(parents=True, exist_ok=True)
        # the live page runs the whole analysis — the same one the final page
        # gets — each time a run finishes, and the duel's own reading with it
        def live_duel() -> dict:
            recs = _load_records(out)
            return {"duel": duel_block(out, recs, args.band)[1]} if recs else {}
        server = serve(out / "traces", args.template or DEFAULT_TEMPLATE, host=args.host, port=args.port,
                       poll=0.2, token=token, enrich=live_duel, also=[out / "records"])
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://{args.host}:{server.server_address[1]}/" + (f"?token={token}" if token else "")
        print(f"live: {url}  (both agents stream into the race)", flush=True)
        if not args.no_open and sys.stdout.isatty():
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:   # noqa: BLE001 — no browser is not an error
                pass
    streamer = None
    if args.hub:
        from ..harness.hub_server import TraceStreamer
        hub_token = os.environ.get("AGENTDIFF_HUB_TOKEN")
        if not hub_token:
            print("error: --hub needs $AGENTDIFF_HUB_TOKEN (`agentdiff hub` prints it)", file=sys.stderr)
            return 2
        (out / "traces").mkdir(parents=True, exist_ok=True)
        streamer = TraceStreamer(out / "traces", args.hub, hub_token, interval=0.5).start()
        print(f"hub: streaming every run to {args.hub.rstrip('/')}/live", flush=True)
    # a scheduler's SIGTERM is an interrupt like Ctrl-C: agents stopped,
    # workspaces removed, finished runs still reported
    import signal as _signal
    from ..harness.vendors import DuelInterrupted

    def _term(signum, frame):
        raise KeyboardInterrupt
    try:
        _signal.signal(_signal.SIGTERM, _term)
    except ValueError:      # not the main thread (embedded use): leave it
        pass
    try:
        records = run_duel(tasks, specs, out, runs=args.runs, parallel=not args.sequential,
                           baseline=not args.no_baseline, on_baseline=on_baseline,
                           baselines=getattr(args, "baselines", None) or again.get("baselines"),
                           first_run=again.get("first_run", 1),
                           live_every=0.25 if args.live else 1.0,
                           stream_cap=int(args.max_stream_mb * 1024 * 1024),
                           on_event=on_event, on_done=on_done, budget_tokens=args.budget_tokens,
                           timeout_s=args.timeout, check_timeout_s=args.check_timeout,
                           isolate=not args.use_my_config, sandbox=args.sandbox,
                           claude_mode=args.claude_permission_mode,
                           price=None)
    except DuelInterrupted as stop:
        finished = [r for r in stop.records if not r.get("failed")]
        print(f"\ninterrupted: stopped {stop.stopped['processes']} agent process(es) and removed "
              f"{stop.stopped['workspaces']} workspace copy(ies); {len(finished)} finished run(s) kept in {out}",
              file=sys.stderr, flush=True)
        if finished:
            _report(out, finished, args.band, args.template, args.quiet)
        if server is not None:
            server.shutdown_all()
        if streamer is not None:
            streamer.stop()
        return 130
    failed = [r for r in records if r.get("failed")]
    for f in failed:
        print(f"error: {f['agent']} {f['task']} {f['run']}: {f['error']}", file=sys.stderr)
    if prices:
        # a price the operator gave is applied to the recorded stream again,
        # so it is labelled theirs and never mistaken for a vendor's figure
        from ..vendors import codex_to_trajectory, read_events
        for r in records:
            if r.get("failed") or r["agent"] not in prices or r["vendor"] != "codex":
                continue
            events, _ = read_events(out / r["raw"])
            t = r["trajectory"]
            fresh = codex_to_trajectory(events, task=r["task"], prompt=t["task"]["prompt"], agent=r["agent"],
                                        model=t["agent"]["model"], version=t["agent"]["version"], run_id=r["run"],
                                        success=r["check"]["passed"], note=t["outcome"].get("note"),
                                        score=t["outcome"].get("score"),
                                        termination=t["outcome"]["termination"], price=prices[r["agent"]])
            for key in ("budget", "harness"):
                if key in t:
                    fresh[key] = t[key]
            r["trajectory"] = fresh
            (out / r["trace"]).write_text(json.dumps(fresh, indent=1, ensure_ascii=False), encoding="utf-8")
    good = [r for r in records if not r.get("failed")]
    if again:
        # the earlier runs of this comparison, then these
        good = [r for r in _load_records(out) if (r["task"], r["agent"], r["run"]) not in
                {(g["task"], g["agent"], g["run"]) for g in good}] + good
    code = _report(out, good, args.band, args.template, args.quiet) if good else 1
    if streamer is not None:
        streamer.stop()
        print(f"hub: sent {streamer.sent} copy(ies) of the runs" +
              (f"; {streamer.failed} failed ({streamer.last_error})" if streamer.failed else ""), flush=True)
    if server is not None:
        try:
            server.watcher.finish()
            if args.linger is not None and args.linger < 0:
                print("live: serving the finished race; Ctrl-C to stop", flush=True)
                while True:
                    time.sleep(3600)
            elif args.linger and args.linger > 0:
                time.sleep(args.linger)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown_all()
    return code
