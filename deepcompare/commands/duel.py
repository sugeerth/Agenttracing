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
import sys
import time
from pathlib import Path

__all__ = ["register", "run"]

#: characters of each run's patch carried onto the page
PATCH_HEAD = 6000


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "duel", help="run Codex CLI and Claude Code on the same task, each in its own copy of the workspace, "
                     "side by side; trace every event, grade each run with your check, and write a fair "
                     "report and the page (keys from OPENAI_API_KEY / CODEX_API_KEY and ANTHROPIC_API_KEY)")
    parser.add_argument("--task", default=None, metavar="FILE",
                        help="task JSON: {id, prompt, workspace, check} or {\"tasks\": [...]}")
    parser.add_argument("--prompt", default=None, help="the task, when there is no --task file")
    parser.add_argument("--workspace", default=None, metavar="DIR",
                        help="the directory each agent starts from (copied per run; never modified)")
    parser.add_argument("--check", default=None, metavar="CMD",
                        help="shell command run in each agent's workspace after it stops; exit 0 is a pass")
    parser.add_argument("--id", default="task", help="task id, when there is no --task file")
    parser.add_argument("--agent", action="append", default=None, metavar="[NAME=]VENDOR[:MODEL]",
                        help="codex[:MODEL] or claude[:MODEL]; twice. Default: codex and claude")
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
    parser.add_argument("--live", action="store_true",
                        help="serve the page while the agents work: both stream into a race at "
                             "http://HOST:PORT/ (localhost only by default)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="--live: address to bind (default 127.0.0.1); anything else needs --allow-remote")
    parser.add_argument("--allow-remote", action="store_true",
                        help="--live: serve beyond this machine behind a random token printed once")
    parser.add_argument("--max-stream-mb", type=float, default=256.0,
                        help="stop a run whose output passes this many MB (default 256)")
    parser.add_argument("--port", type=int, default=8765, help="--live: port (default 8765)")
    parser.add_argument("--linger", type=float, default=None, metavar="S",
                        help="--live: keep serving this many seconds after the duel ends "
                             "(default: until Ctrl-C)")
    parser.add_argument("--template", default=None, help="page template (default the blocks page)")
    parser.add_argument("-o", "--output", default="duel-out", metavar="DIR")
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
        raise ValueError("give --task FILE, or --prompt and --workspace")
    return [{"id": args.id, "prompt": args.prompt, "workspace": str(Path(args.workspace).resolve()),
             "check": args.check, "expected": None}]


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


def _load_records(out: Path) -> list:
    records = []
    for p in sorted((out / "records").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        trace = out / rec["trace"]
        if trace.is_file():
            rec["trajectory"] = json.loads(trace.read_text(encoding="utf-8"))
            records.append(rec)
    return records


def _report(out: Path, records: list, band: float, template, quiet: bool) -> int:
    from ..duel import duel_report, render_markdown
    from . import batch as batch_cmd
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
    (out / "duel.json").write_text(json.dumps(slim, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "DUEL.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"\n{report.get('narrative') or report.get('reason')}")
    print(f"wrote {out / 'duel.json'} and {out / 'DUEL.md'}")
    if not report.get("measurable"):
        return 1
    ns = argparse.Namespace(tracesdir=str(out / "traces"), output=str(out / "page"), template=template,
                            golden=None, policy=None, lessons=None, extra_aggregate={"duel": slim})
    code = batch_cmd.run(ns)
    if code == 0:
        print(f"page: {out / 'page' / 'report.html'}")
    return code


def run(args: argparse.Namespace) -> int:
    from ..harness.vendors import parse_spec, preflight, run_duel
    out = Path(args.output)
    if args.from_dir:
        src = Path(args.from_dir)
        records = _load_records(src)
        if not records:
            print(f"error: no records under {src / 'records'}", file=sys.stderr)
            return 2
        return _report(src, records, args.band, args.template, args.quiet)
    try:
        tasks = _tasks(args)
        specs = [parse_spec(s) for s in (args.agent or ["codex", "claude"])]
        prices = _prices(args.price)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if len(specs) != 2 or specs[0].agent == specs[1].agent:
        print("error: a duel is two agents with different names (NAME=vendor:model to tell two apart)",
              file=sys.stderr)
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

    def on_event(ev):
        if not args.quiet:
            line = _say(ev)
            if line:
                print(line, flush=True)

    def on_done(rec):
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
        server = serve(out / "traces", args.template or DEFAULT_TEMPLATE, host=args.host, port=args.port,
                       poll=0.2, token=token)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"live: http://{args.host}:{server.server_address[1]}/" + (f"?token={token}" if token else "")
              + "  (both agents stream into the race)", flush=True)
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
    code = _report(out, good, args.band, args.template, args.quiet) if good else 1
    if server is not None:
        try:
            if args.linger is None:
                print("live: serving the finished race; Ctrl-C to stop", flush=True)
                while True:
                    time.sleep(3600)
            elif args.linger > 0:
                time.sleep(args.linger)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown_all()
    return code
