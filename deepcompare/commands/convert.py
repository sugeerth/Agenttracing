"""The ``convert`` command: foreign trace formats into SCHEMA
trajectories, through the adapter registry.

Importing this module registers the shipped adapters (Claude Code, the
RL rollout formats) so ``--format``'s choices and ``--list-formats``
name the same set: the choice list is derived from the registry, not
hardcoded, so a format a third party registers is selectable too.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..adapters import detect_verl, from_agent_lightning, from_openai_messages, from_otel_genai, from_verl
from ..registry import convert as registry_convert, dry_run, formats
from ..claude_code import register_format as _register_claude_code
_register_claude_code()
from ..adapters import register_formats as _register_rl_formats  # noqa: E402
_register_rl_formats()  # verl, agent-lightning
from ._io import safe_name  # noqa: E402

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "convert", help="convert foreign trace formats to SCHEMA trajectories"
    )
    # Derived from the registry, not hardcoded: a format that --list-formats
    # advertises must be selectable, including one a third party registered.
    parser.add_argument("--format", default="auto",
                        choices=("auto",) + tuple(sorted(f["name"] for f in formats())),
                        help="input format")
    parser.add_argument("input", nargs="?", default="",
                        help="input JSON file")
    parser.add_argument("-o", "--output", default="out",
                        help="output directory (default: out)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what the conversion would produce, "
                             "including fidelity counters, without writing")
    parser.add_argument("--list-formats", action="store_true",
                        help="list the known trace formats and exit")
    parser.add_argument("--agent", default=None,
                        help="agent name for verl / agent-lightning records that carry no "
                             "model or policy name")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.list_formats:
        print("Known trace formats:")
        for entry in formats():
            print(f"  {entry['name']:<12} {entry['description']}")
        return 0
    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"error: {in_path} is not a file", file=sys.stderr)
        return 2
    try:
        text = in_path.read_text(encoding="utf-8")
        if in_path.suffix == ".jsonl":
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            data = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"error: {in_path}: not valid JSON: {exc}", file=sys.stderr)
        return 2

    if args.list_formats:
        print("Known trace formats:")
        for entry in formats():
            print(f"  {entry['name']:<12} {entry['description']}")
        return 0

    # RL rollouts arrive one per JSONL line: a list under --format verl (or
    # detected as veRL) writes one trace per record instead of failing.
    if isinstance(data, list) and len(data) > 1 and not args.dry_run and (
            args.format == "verl" or (args.format == "auto" and detect_verl(data)[0] >= 0.9)):
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for pos, record in enumerate(data):
            try:
                trajectory, warnings = from_verl(record, agent=args.agent)
            except ValueError as exc:
                print(f"warning: record {pos}: {exc}", file=sys.stderr)
                continue
            for warning in warnings:
                print(f"warning: record {pos}: {warning}", file=sys.stderr)
            out_path = out_dir / f"{safe_name(trajectory['task']['id'])}__{safe_name(trajectory['agent']['name'])}.json"
            out_path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            written += 1
        print(f"Wrote {written} of {len(data)} veRL rollout(s) to {out_dir}")
        return 0 if written else 2
    if args.agent and args.format in ("verl", "agent-lightning") and isinstance(data, (dict, list)):
        try:
            convert_fn = from_verl if args.format == "verl" else from_agent_lightning
            trajectory, warnings = convert_fn(data[0] if isinstance(data, list) and len(data) == 1 else data,
                                              agent=args.agent)
        except ValueError as exc:
            print(f"error: conversion failed: {exc}", file=sys.stderr)
            return 2
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{safe_name(trajectory['task']['id'])}__{safe_name(trajectory['agent']['name'])}.json"
        out_path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
        return 0

    if args.dry_run:
        report = dry_run(data, None if args.format == "auto" else args.format)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("ok") else 2

    if args.format == "auto":
        try:
            result = registry_convert(data, None)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        trajectory, warnings = result["trajectory"], result["warnings"]
        print(f"Detected format: {result['format']} "
              f"(confidence {result['confidence']:.0%})")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        tid = safe_name(trajectory["task"]["id"])
        agent = safe_name(trajectory["agent"]["name"])
        out_path = out_dir / f"{tid}__{agent}.json"
        out_path.write_text(
            json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"Wrote {out_path}")
        return 0

    try:
        if args.format == "otel":
            if not isinstance(data, dict) or "spans" not in data:
                print("error: otel input must be an object with a 'spans' array "
                      "(plus 'meta', or top-level 'agent' and 'task')",
                      file=sys.stderr)
                return 2
            # Metadata may sit under "meta" (same shape as the openai adapter
            # takes) or at the top level; accept either so one convention
            # works for both formats.
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            outcome = data.get("outcome") or meta.get("outcome")
            if outcome is None and ("success" in meta or "answer" in meta):
                outcome = {k: meta[k] for k in ("success", "answer", "score")
                           if k in meta}
            trajectory, warnings = from_otel_genai(
                data["spans"],
                agent=data.get("agent") or meta.get("agent") or "otel-agent",
                task=data.get("task") or meta.get("task") or "task",
                outcome=outcome,
            )
        else:
            if not isinstance(data, dict) or "messages" not in data:
                print("error: openai input must be an object with a 'messages' "
                      "array (plus optional 'meta')", file=sys.stderr)
                return 2
            trajectory, warnings = from_openai_messages(
                data["messages"], data.get("meta")
            )
    except ValueError as exc:
        print(f"error: conversion failed: {exc}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    tid = safe_name(trajectory["task"]["id"])
    agent = safe_name(trajectory["agent"]["name"])
    out_path = out_dir / f"{tid}__{agent}.json"
    out_path.write_text(json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0
