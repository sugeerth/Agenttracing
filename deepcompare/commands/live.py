"""The commands that may talk to a network: run, replay, why — and the
hermetic replay commands, rerun and context, which load the harness
but never a provider unless one is named.

Every harness import happens inside the command function, so the
analysis commands never load network code (pinned by
``tests/test_harness.py::TestNetworkBoundary``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from ..report import render_html
from .paths import DEFAULT_TEMPLATE

__all__ = ["_cmd_run", "_cmd_loop", "_cmd_replay", "_cmd_rerun", "_cmd_context", "_cmd_checkpoint", "_cmd_why",
           "_provider_options", "_split_spec", "_load_report", "_save_report"]


def _provider_options(args: argparse.Namespace) -> dict:
    """Provider options from the CLI; only the ones given, so a provider's
    own defaults (and its environment variables) still apply."""
    options: dict = {}
    if getattr(args, "base_url", None):
        options["base_url"] = args.base_url
    if getattr(args, "temperature", None) is not None:
        options["temperature"] = args.temperature
    if getattr(args, "api_key_env", None):
        options["api_key_env"] = args.api_key_env
    return options


def _split_spec(entry: str) -> tuple:
    """``NAME=kind:rest`` → (name or None, ``kind:rest``); the ``=`` that
    separates the name is the first one, so command templates keep theirs."""
    head, sep, rest = entry.partition("=")
    if sep and ":" in head:
        return None, entry
    return (head if sep else None), (rest if sep else entry)


def _cmd_run(args: argparse.Namespace) -> int:
    # imported here, not at module top: the harness is the one place that
    # talks to a network, and the analysis commands must not load it
    from ..harness import agent_from_spec, provider_from_spec, run_suite
    from ..harness.runner import load_tasks, load_tools
    options = _provider_options(args)

    def make_provider(spec: str):
        kind = spec.split(":", 1)[0].strip().lower()
        return provider_from_spec(spec, **({} if kind == "scripted" else options))

    try:
        tasks = load_tasks(args.tasks)
        tools = load_tools(args.tools) if args.tools else []
        specs: dict = {}
        for entry in args.provider or []:
            name, spec = _split_spec(entry)
            provider = make_provider(spec)  # validates the spec now
            specs[name or provider.name] = spec
        agents: dict = {}
        for entry in args.agent or []:
            name, spec = _split_spec(entry)
            ext = agent_from_spec(spec, name)
            agents[ext.name] = ext
        if not specs and not agents:
            raise ValueError("give at least one --provider or --agent")
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    manifest = run_suite(
        specs, tasks, tools, out_dir=args.output, runs=args.runs,
        budget={"max_steps": args.max_steps},
        provider_factory=make_provider, agents=agents,
        progress=lambda line: print(f"  running {line}"))
    written = len(manifest["traces"])
    ok = sum(1 for t in manifest["traces"] if t["success"] is True)
    print(f"Wrote {written} trace(s) to {manifest['out_dir']} — "
          f"{ok}/{written} succeeded"
          + (f", {manifest['provider_failures']} provider failure(s) recorded "
             "as infrastructure_error" if manifest["provider_failures"] else ""))
    lineup = len(specs) + len(agents)
    print(f"Next: python -m deepcompare "
          f"{'runs' if args.runs > 1 else 'batch' if lineup == 2 else 'fleet'} "
          f"{manifest['out_dir']} -o out")
    return 0


def _load_report(path_text: str):
    report_path = Path(path_text)
    if not report_path.is_file():
        print(f"error: {report_path} is not a file", file=sys.stderr)
        return None, None
    try:
        return report_path, json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {report_path}: {exc}", file=sys.stderr)
        return None, None


def _save_report(report_path: Path, report: dict) -> None:
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    page = report_path.with_suffix(".html")
    if page.is_file():
        # a page beside the report is re-rendered from the updated report
        render_html([report], {}, DEFAULT_TEMPLATE, page)
        print(f"Re-rendered {page}")


def _cmd_replay(args: argparse.Namespace) -> int:
    """Verify the decisive step by re-execution: the only way a
    hypothesized step becomes replay-verified, refuted or mixed."""
    from ..harness import provider_from_spec
    from ..harness.replay import replay
    from ..harness.runner import load_tools
    from ..verdict import verdict_card
    report_path, report = _load_report(args.report)
    if report is None:
        return 2
    diagnosis = report.get("diagnosis") or {}
    decisive = diagnosis.get("decisive_step") or {}
    side = args.side or diagnosis.get("subject")
    if side not in ("a", "b"):
        print("error: the report names no failing side; pass --side a|b", file=sys.stderr)
        return 2
    step = args.from_step if args.from_step is not None else decisive.get("step")
    if step is None:
        print("error: the diagnosis committed to no decisive step (it abstained); "
              "pass --from-step N to replay a step of your choosing", file=sys.stderr)
        return 2
    other = "b" if side == "a" else "a"
    correction: dict = {}
    if args.correction:
        correction = {"text": args.correction, "output": args.correction}
    else:
        # the recipe: take the decision the passing run took at this step —
        # the counterpart on the aligned row, verbatim
        row = next((r for r in report.get("alignment") or []
                    if r.get(f"{side}_index") == step), None)
        counterpart = None
        if row is not None and row.get(f"{other}_index") is not None:
            steps_other = report[other]["steps"]
            idx = row[f"{other}_index"]
            counterpart = steps_other[idx] if 0 <= idx < len(steps_other) else None
        if counterpart is None:
            print("error: the passing run has no aligned step to borrow at step "
                  f"{step}; pass --correction TEXT", file=sys.stderr)
            return 2
        correction = {"input": counterpart.get("input") or "",
                      "output": counterpart.get("output") or "",
                      "borrowed_from": f"{other} step {counterpart.get('index')}"}
    name, spec = _split_spec(args.provider)
    options = _provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()

    def factory():
        return provider_from_spec(spec, **({} if kind == "scripted" else options))

    try:
        tools = load_tools(args.tools) if args.tools else []
        trace = {"trace_id": f"{report['task']['id']}__{report[side]['agent']['name']}",
                 "agent": report[side]["agent"], "task": report["task"],
                 "outcome": report[side]["outcome"], "steps": report[side]["steps"],
                 "budget": report.get(side, {}).get("budget") or {"max_steps": 12}}
        result = replay(trace, factory(), tools, int(step), correction,
                        replays=args.replays, out_dir=args.traces,
                        provider_factory=factory)
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = {
        "verdict": result["verdict"], "replays": result["replays"],
        "flipped": result["flipped"], "flip_rate": result["flip_rate"],
        "step": result["step"], "correction": correction,
        "provider": {"name": name or kind, "model": factory().model},
        "runs": [{k: v for k, v in r.items() if k != "trace"} for r in result["runs"]],
        "note": result["note"],
    }
    decisive["verification"] = result["verdict"]
    decisive["replay"] = summary
    diagnosis["decisive_step"] = decisive
    report["diagnosis"] = diagnosis
    report["verdict_card"] = verdict_card(report)
    _save_report(report_path, report)
    print(f"Replayed {report[side]['agent']['name']} from step {step} "
          f"×{result['replays']} with the {summary['provider']['name']} provider")
    print(f"  {result['verdict']}: {result['flipped']}/{result['replays']} replay(s) "
          f"flipped the outcome (rate {result['flip_rate']})")
    print(f"  correction: " + (f"borrowed from {correction['borrowed_from']}"
                               if correction.get("borrowed_from") else "as given"))
    if args.traces:
        print(f"  replay traces written to {args.traces}")
    print(f"  {result['note']}")
    from ..verdict import format_verdict_card
    print()
    print(format_verdict_card(report["verdict_card"]))
    return 0


def _cmd_why(args: argparse.Namespace) -> int:
    """The narrator through the harness: brief → provider → checked
    ingestion.  The model phrases; it never alters a number, a verdict or
    an exit code — the narration is commentary no analysis reads."""
    from ..harness import provider_from_spec
    from ..narrate import ingest_narration, narration_brief, narration_prompt
    from ..verdict import format_verdict_card
    report_path, report = _load_report(args.report)
    if report is None:
        return 2
    name, spec = _split_spec(args.provider)
    options = _provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()
    try:
        provider = provider_from_spec(spec, **({} if kind == "scripted" else options))
        brief = narration_brief(report)
        prompt = narration_prompt(brief)
        response = provider.complete([{"role": "user", "content": prompt}], None)
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # the provider failed: say so, change nothing
        print(f"error: provider failed: {exc}", file=sys.stderr)
        return 3
    text = (response.text or "").strip()
    ingest_narration(report, text, brief=brief, model=f"{name or kind}:{provider.model}")
    report["narration"]["source"] = "harness-provider"
    report["narration"]["facts_in_brief"] = len(brief.get("facts") or [])
    _save_report(report_path, report)
    check = report["narration"]["faithfulness"]
    card = report.get("verdict_card")
    if card:
        print(format_verdict_card(card))
        print()
    print(f"Why ({report['narration']['model']}, {len(brief.get('facts') or [])} facts in the brief):")
    for line in text.splitlines():
        print(f"  {line}")
    print()
    if check["faithful"]:
        print("  faithful: every number and citation traces to the brief")
    else:
        if check["unsupported_numbers"]:
            print(f"  UNSUPPORTED numbers (not in the evidence): "
                  f"{', '.join(check['unsupported_numbers'])}")
        if check["invalid_citations"]:
            print(f"  INVALID citations: {', '.join(check['invalid_citations'])}")
        print("  stored flagged — the reader sees the warning; no verdict, number "
              "or exit code depends on this text")
    return 0

def _cmd_watch(args: argparse.Namespace) -> int:
    """Serve the report page live over a trace directory (localhost)."""
    from deepcompare.harness.watch import clear_demo_dir, serve
    import threading
    template = Path(args.template) if args.template else Path(__file__).resolve().parents[2] / "web" / "blocks.html"
    if not template.is_file():
        print(f"error: template {template} not found", file=sys.stderr)
        return 2
    traces = Path(args.tracesdir) if args.tracesdir else None
    demo = None
    if args.demo:
        demo = Path(args.demo)
        if not demo.is_dir():
            print(f"error: {demo} is not a directory", file=sys.stderr)
            return 2
        traces = clear_demo_dir(traces) if traces else clear_demo_dir(None)
    if traces is None:
        print("error: give a trace directory, or --demo <traces-to-replay>", file=sys.stderr)
        return 2
    stop = threading.Event()
    server = serve(traces, template, host=args.host, port=args.port, poll=args.poll,
                   demo=demo, pace=args.pace, loop=args.loop, stop=stop, quiet=not args.verbose,
                   db=getattr(args, "db", None))
    host, port = server.server_address[:2]
    print(f"watching {traces} — open http://{host}:{port}/  (Ctrl-C to stop)")
    if demo:
        print(f"demo: replaying {demo} one step every {args.pace}s" + (" in a loop" if args.loop else ""))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown_all()
    return 0


def _cmd_judge(args: argparse.Namespace) -> int:
    """A second model judges each trace's final answer."""
    import json
    from deepcompare.harness import provider_from_spec
    from deepcompare.harness.judge import DEFAULT_RUBRIC, judge_many
    target = Path(args.target)
    if target.is_dir():
        paths = sorted(p for p in target.glob("*.json")
                       if not p.name.endswith(".live.json") and not p.name.startswith(("report_", "aggregate", "RUN_MANIFEST", "fleet")))
    else:
        paths = [target]
    traces = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"skipped {path.name}: {exc}", file=sys.stderr)
            continue
        if "steps" in data and "outcome" in data:
            traces.append((path, data, None))
        elif "a" in data and "b" in data:   # a report: judge both sides
            traces.append((path, data["a"], data)); traces.append((path, data["b"], data))
    if not traces:
        print("error: nothing to judge", file=sys.stderr)
        return 2
    _name, spec = _split_spec(args.provider)
    options = _provider_options(args)
    kind = spec.split(":", 1)[0].strip().lower()
    factory = lambda: provider_from_spec(spec, **({} if kind == "scripted" else options))  # noqa: E731
    rubric = args.rubric or DEFAULT_RUBRIC
    counts = judge_many([t[1] for t in traces], factory, rubric=rubric, with_steps=args.with_steps, apply=args.apply)
    written = set()
    for path, data, report in traces:
        if path in written:
            continue
        payload = report if report is not None else data
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.add(path)
    if args.db:
        from deepcompare.tracedb import TraceDB
        with TraceDB(args.db) as db:
            for path, data, report in traces:
                if report is None:
                    db.add(data, source="judge")
    for path, data, report in traces:
        j = (data.get("outcome") or {}).get("judge") or {}
        agent = (data.get("agent") or {}).get("name")
        print(f"  {path.name} · {agent}: " + (f"judge says {'✓' if j.get('success') else '✗'} score {j.get('score')}"
              f" — {j.get('rationale', '')[:90]}" if not j.get("error") else f"no verdict ({j.get('error')})")
              + ("  [applied]" if j.get("applied") else "") + ("  [self-judged]" if j.get("self_judged") else ""))
    print(f"{counts['judged']} judged, {counts['agreed_with_prior']} agree with the prior grade, "
          f"{counts['disagreed_with_prior']} disagree, {counts['failed']} without a verdict"
          + ("; verdicts applied to outcome.success (graded_by: model)" if args.apply else "; recorded as outcome.judge only"))
    return 0



def _cmd_loop(args: argparse.Namespace) -> int:
    """The agentic loop: run, compare, read, test a prompt hypothesis,
    keep or revert it, spend runs where the pick is unclear, stop for a
    reason — with every decision in a ledger."""
    from ..harness import agent_from_spec, provider_from_spec
    from ..harness.loop import Loop
    from ..harness.runner import load_tasks, load_tools
    from .paths import DEFAULT_TEMPLATE
    options = _provider_options(args)

    def make_provider(spec: str):
        kind = spec.split(":", 1)[0].strip().lower()
        return provider_from_spec(spec, **({} if kind == "scripted" else options))

    try:
        tasks = load_tasks(args.tasks)
        tools = load_tools(args.tools) if args.tools else []
        specs: dict = {}
        for entry in args.provider or []:
            name, spec = _split_spec(entry)
            provider = make_provider(spec)
            specs[name or provider.name] = spec
        agents: dict = {}
        for entry in args.agent or []:
            name, spec = _split_spec(entry)
            ext = agent_from_spec(spec, name)
            agents[ext.name] = ext
        if len(specs) + len(agents) != 2:
            raise ValueError("the loop compares exactly two agents: give two --provider/--agent entries")
        seeds: dict = {}
        for entry in args.suggest or []:
            agent, sep, text = entry.partition("=")
            if not sep or agent not in specs and agent not in agents:
                raise ValueError(f"--suggest wants AGENT=TEXT with a named agent: {entry!r}")
            seeds.setdefault(agent, []).append(text)
        db = None
        if args.db:
            from ..tracedb import TraceDB
            db = TraceDB(args.db)
        template = Path(args.template) if args.template else DEFAULT_TEMPLATE
        from ..scorecard import load_golden, load_policy
        golden = load_golden(args.golden) if args.golden else None
        policy = load_policy(args.policy) if args.policy else None
        judge_factory = None
        if args.judge:
            _jname, jspec = _split_spec(args.judge)
            jkind = jspec.split(":", 1)[0].strip().lower()
            judge_factory = lambda: provider_from_spec(jspec, **({} if jkind == "scripted" else options))  # noqa: E731
            judge_factory()  # validates the spec now
        loop = Loop(tasks, specs, out_dir=args.output, provider_factory=make_provider, agents=agents, tools=tools,
                    runs=args.runs, max_iterations=args.iterations, max_runs=args.max_runs,
                    budget={"max_steps": args.max_steps}, db=db, progress=print, template=template,
                    family_pattern=args.family, seed_suggestions=seeds, resume=args.resume,
                    golden=golden, policy=policy, judge_factory=judge_factory, judge_with_steps=args.judge_with_steps)
    except (ValueError, OSError, ImportError, AttributeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        ledger = loop.run()
    finally:
        if db is not None:
            db.close()
    summary = ledger["summary"]
    print(f"Wrote {Path(args.output) / 'loop.json'} and LOOP.md — {summary['iterations']} iteration(s), "
          f"{summary['spent_runs']} run(s), {summary['kept_changes']} prompt change(s) kept, "
          f"{summary['reverted_changes']} reverted, {summary.get('dropped_changes', 0)} dropped; "
          f"stopped: {(summary.get('stop') or {}).get('reason')}")
    for agent, a in summary["agents"].items():
        if a.get("runs"):
            print(f"  {agent}: success {a['success']:.0%} over {a['runs']} run(s) [{a['ci95'][0]:.2f}–{a['ci95'][1]:.2f}], "
                  f"prompt version {a['prompt_version']}")
    return 0


def _trace_paths(target: str) -> list:
    path = Path(target)
    if path.is_dir():
        return sorted(p for p in path.glob("*.json")
                      if not p.name.startswith(("report_", "aggregate", "rerun", "cassette", "loop", "manifest")))
    if path.is_file():
        return [path]
    raise ValueError(f"{target} is neither a trace file nor a directory")


def _cmd_rerun(args: argparse.Namespace) -> int:
    """Replay recorded runs hermetically and diff each against its
    recording: the model's own turns and the recording's tool results,
    or a named model in the recorded world.  Exit 1 on any drift."""
    from ..harness.rerun import rerun_paths, to_annotations, to_junit, to_markdown
    from ..harness.runner import load_tools
    provider = None
    try:
        paths = _trace_paths(args.target)
        tools = load_tools(args.tools) if args.tools else None
        if args.provider:
            from ..harness import provider_from_spec
            _name, spec = _split_spec(args.provider)
            kind = spec.split(":", 1)[0].strip().lower()
            provider = provider_from_spec(spec, **({} if kind == "scripted" else _provider_options(args)))
        if not paths:
            raise ValueError(f"no trace files under {args.target}")
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        golden = None
        if getattr(args, "golden", None):
            from ..scorecard import load_golden
            golden = load_golden(args.golden)
        cassette = None
        if getattr(args, "cassette", None):
            from ..harness.cassette import Cassette
            cassette = Cassette.from_dict(json.loads(Path(args.cassette).read_text(encoding="utf-8")))
            if len(paths) > 1:
                raise ValueError("--cassette serves one trace; give one trace file")
        summary = rerun_paths(paths, provider=provider, tools=tools, policy=args.policy,
                              out_dir=(out / "traces") if args.traces else None, keep_traces=False,
                              from_step=getattr(args, "from_step", None), until=getattr(args, "until", None),
                              span=getattr(args, "span", None), golden=golden, cassette=cassette)
    except (ValueError, OSError, ImportError, AttributeError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    (out / "rerun.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    written = [out / "rerun.json"]
    if args.junit:
        (out / args.junit).write_text(to_junit(summary), encoding="utf-8"); written.append(out / args.junit)
    if args.job_summary:
        (out / args.job_summary).write_text(to_markdown(summary), encoding="utf-8"); written.append(out / args.job_summary)
    if args.github_annotations:
        print(to_annotations(summary), end="")
        import os
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with open(step_summary, "a", encoding="utf-8") as handle:
                handle.write(to_markdown(summary))
    print(f"Replayed {summary['traces']} trace(s) hermetically ({summary['mode']}, policy {args.policy}): "
          f"{summary['faithful']} reproduced, {summary['drifted']} drifted")
    for r in summary["results"]:
        print(("  ✓ " if r.get("faithful") else "  ✗ ") + str(r.get("reading") or ""))
        for n in r.get("drift_map") or []:
            if n.get("kind") == "span" and n.get("reproduced") is False:
                print(f"      ✗ sub-agent {n.get('agent')} (steps {n.get('from')}–{n.get('to')}): {n.get('differed')} differed, {n.get('misses')} miss(es), first at {n.get('first_difference')}")
    print("  written: " + ", ".join(str(p) for p in written))
    if summary["drifted"] and args.fail_on_drift:
        return 1
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    """Print what the model saw before a step, rebuilt from the trace;
    with a report and --row, both runs' contexts at that aligned row and
    their diff."""
    from ..harness import context as ctx
    path = Path(args.target)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: {path}: {exc}", file=sys.stderr)
        return 2
    is_report = isinstance(data, dict) and "alignment" in data and "a" in data and "b" in data
    try:
        if is_report and args.row is not None:
            rows = data.get("alignment") or []
            if not (0 <= args.row < len(rows)):
                raise ValueError(f"row {args.row} is outside the alignment's {len(rows)} rows")
            row = rows[args.row]
            sides = {}
            for side in ("a", "b"):
                idx = row.get(f"{side}_index")
                if idx is not None:
                    sides[side] = int(idx)
            traces = {s: {"agent": data[s]["agent"], "task": data["task"], "steps": data[s]["steps"]} for s in sides}
            for side, idx in sides.items():
                print(f"=== {traces[side]['agent']['name']} ({side}) before step {idx} — {json.dumps(ctx.summary(traces[side], idx))}")
                if not args.diff_only:
                    print(ctx.render(ctx.context_at(traces[side], idx)))
            if len(sides) == 2:
                print("=== diff")
                print(ctx.diff(traces["a"], sides["a"], traces["b"], sides["b"]) or "(the two contexts are identical)\n")
            return 0
        if is_report:
            side = args.side or (data.get("diagnosis") or {}).get("subject") or "a"
            trace = {"agent": data[side]["agent"], "task": data["task"], "steps": data[side]["steps"]}
        else:
            trace = data
        step = args.step
        if step is None:
            dec = ((data.get("diagnosis") or {}).get("decisive_step") or {}) if is_report else {}
            step = dec.get("step")
        if step is None:
            raise ValueError("pass --step N (a report without a decisive step names none)")
        print(f"=== {(trace.get('agent') or {}).get('name', '?')} before step {step} — {json.dumps(ctx.summary(trace, int(step)))}")
        print(ctx.render(ctx.context_at(trace, int(step))))
        if args.against:
            other = json.loads(Path(args.against).read_text(encoding="utf-8"))
            other_step = args.against_step if args.against_step is not None else int(step)
            print("=== diff")
            print(ctx.diff(trace, int(step), other, other_step) or "(the two contexts are identical)\n")
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    """Write a checkpoint bundle for a long run at a step: the prefix as
    a SCHEMA trace, the cassette, the context the model had, and a
    summary — everything a person or a rerun needs to resume from there
    without the hours before it."""
    from ..harness import context as ctx
    from ..harness.cassette import Cassette
    from ..milestones import evaluate as evaluate_milestones
    path = Path(args.trace)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
        steps = list(trace.get("steps") or [])
        step = int(args.step)
        if not (0 <= step < len(steps)):
            raise ValueError(f"step {step} is outside the trace's {len(steps)} steps")
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        prefix = dict(trace)
        prefix["steps"] = [s for s in steps[:step] if s.get("type") != "answer"]
        prefix["trace_id"] = f"{trace.get('trace_id', 'run')}__checkpoint{step}"
        prefix["outcome"] = {"success": False, "answer": "", "score": None, "termination": "user_stop",
                             "note": f"checkpoint: the recording's first {step} step(s); the run continues from step {step}"}
        clock = sum(float(s.get("latency_s") or 0) for s in prefix["steps"])
        tokens = sum(int(s.get("tokens") or 0) for s in prefix["steps"] if isinstance(s.get("tokens"), int))
        prefix["totals"] = {"input_tokens": 0, "output_tokens": tokens, "cost_usd": 0.0, "latency_s": round(clock, 4)}
        (out / "prefix.json").write_text(json.dumps(prefix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        cassette = Cassette.from_trace(trace)
        (out / "cassette.json").write_text(json.dumps(cassette.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out / "context.txt").write_text(ctx.render(ctx.context_at(trace, step)), encoding="utf-8")
        milestones = None
        if args.golden:
            from ..scorecard import load_golden
            task = (load_golden(args.golden)["tasks"] or {}).get(str((trace.get("task") or {}).get("id")))
            milestones = (task or {}).get("milestones")
        reached = evaluate_milestones({"agent": trace.get("agent"), "steps": steps[:step]}, milestones) if milestones else None
        at = steps[step]
        summary = {"trace_id": trace.get("trace_id"), "step": step, "of": len(steps), "seconds_so_far": round(clock, 4), "tokens_so_far": tokens,
                   "span": at.get("span"), "next_step": {"type": at.get("type"), "name": at.get("name"), "input": str(at.get("input") or "")[:200]},
                   "context": ctx.summary(trace, step), "cassette": {"recorded_calls": cassette.recorded_calls, "tools": cassette.names},
                   "milestones_reached": [m["id"] for m in reached["milestones"] if m["reached"]] if reached else None,
                   "resume": f"agentdiff rerun {path} --from {step} --cassette {out / 'cassette.json'} [--provider NAME=KIND:MODEL]"}
        (out / "checkpoint.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Checkpoint at step {step} of {len(steps)} ({summary['seconds_so_far']}s so far"
          + (f", inside {at['span'].get('agent')}" if isinstance(at.get("span"), dict) else "") + ")")
    print(f"  next: {at.get('type')} {at.get('name')} — {summary['next_step']['input'][:80]}")
    if reached:
        print(f"  milestones reached so far: {len(summary['milestones_reached'])} — " + ", ".join(summary["milestones_reached"]))
    print(f"  written: {out / 'prefix.json'}, {out / 'cassette.json'}, {out / 'context.txt'}, {out / 'checkpoint.json'}")
    print(f"  resume: {summary['resume']}")
    return 0
