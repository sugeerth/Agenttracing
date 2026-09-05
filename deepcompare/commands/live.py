"""The commands that may talk to a network: run, replay, why.

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

__all__ = ["_cmd_run", "_cmd_replay", "_cmd_why", "_provider_options",
           "_split_spec", "_load_report", "_save_report"]


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
                   demo=demo, pace=args.pace, loop=args.loop, stop=stop, quiet=not args.verbose)
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

