"""Command-line interface for DeepCompare AI.

Usage::

    python -m deepcompare compare a.json b.json -o report.json
    python -m deepcompare batch tracesdir/ -o out/ [--template web/viewer.html]
    python -m deepcompare fleet tracesdir/ -o out/ [--weights success=0.5,...]
    python -m deepcompare gate baseline/ candidate/ -o out/ [--markdown gate.md]

This module is the parser and the dispatch only.  Each command is a
module in :mod:`deepcompare.commands` exposing ``register(subparsers)``
(its argparse surface) and ``run(args) -> int`` (its body); the shared
load-run-write shape — SCHEMA validation, run ids from filenames, the
``report_<task>.json`` / ``aggregate.json`` / ``report.html`` artifacts —
lives once in :mod:`deepcompare.commands._io`.  Adding a command is
adding one module and one entry in :data:`COMMANDS`.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

# every command module is imported as <name>_cmd: several are named after
# engine functions (compare, run, explain, context, eval, ...), and a bare
# name here would shadow the function the next person imports beside it —
# `report.compare` under a bare `commands.compare` import crashed batch once
from .commands import (
    batch as batch_cmd,
    bench as bench_cmd,
    bundle as bundle_cmd,
    check as check_cmd,
    chat as chat_cmd,
    checkpoint as checkpoint_cmd,
    coevolve as coevolve_cmd,
    cohort as cohort_cmd,
    compare as compare_cmd,
    context as context_cmd,
    convert as convert_cmd,
    db as db_cmd,
    demo as demo_cmd,
    evolve as evolve_cmd,
    evolve_compare as evolve_compare_cmd,
    experiments as experiments_cmd,
    explain as explain_cmd,
    feedback as feedback_cmd,
    fleet as fleet_cmd,
    frameworks as frameworks_cmd,
    gate as gate_cmd,
    grafana as grafana_cmd,
    hook as hook_cmd,
    judge as judge_cmd,
    key as key_cmd,
    loop as loop_cmd,
    mcp as mcp_cmd,
    narrate as narrate_cmd,
    panel as panel_cmd,
    profile as profile_cmd,
    progress as progress_cmd,
    replay as replay_cmd,
    rerun as rerun_cmd,
    rl as rl_cmd,
    serve as serve_cmd,
    rlexport as rlexport_cmd,
    route as route_cmd,
    run as run_cmd,
    runs as runs_cmd,
    select as select_cmd,
    variance as variance_cmd,
    watch as watch_cmd,
    why as why_cmd,
)
from .commands import eval as eval_cmd  # the command is named after the builtin
# names other modules and the tests have always taken from here
from .commands._io import (  # noqa: F401
    load_traces as _load_traces_dir,
    run_id_from_name as _run_id_from_name,
    safe_name as _safe_name,
    with_harness as _with_harness,
)
from .commands.paths import DEFAULT_TEMPLATE, LEGACY_TEMPLATE, SELECT_TEMPLATE  # noqa: F401

#: the subcommands, in the order ``--help`` lists them.  An explicit list,
#: not a directory scan: the order is part of the surface, and a reader
#: should find every command here.
COMMANDS = (
    compare_cmd,
    demo_cmd,
    batch_cmd,
    fleet_cmd,
    gate_cmd,
    runs_cmd,
    profile_cmd,
    progress_cmd,
    bench_cmd,
    experiments_cmd,
    narrate_cmd,
    chat_cmd,
    variance_cmd,
    cohort_cmd,
    check_cmd,
    select_cmd,
    convert_cmd,
    frameworks_cmd,
    rl_cmd,
    evolve_cmd,
    evolve_compare_cmd,
    coevolve_cmd,
    run_cmd,
    loop_cmd,
    replay_cmd,
    rerun_cmd,
    checkpoint_cmd,
    context_cmd,
    judge_cmd,
    panel_cmd,
    why_cmd,
    db_cmd,
    hook_cmd,
    eval_cmd,
    route_cmd,
    feedback_cmd,
    rlexport_cmd,
    grafana_cmd,
    bundle_cmd,
    key_cmd,
    mcp_cmd,
    serve_cmd,
    watch_cmd,
    explain_cmd,
)


def _program_name() -> str:
    """How this invocation should tell the user to call it again."""
    invoked = os.path.basename(sys.argv[0] or "")
    if invoked in ("__main__.py", "-c", ""):
        return "python -m deepcompare"
    return invoked


def build_parser() -> argparse.ArgumentParser:
    """Build the deepcompare argument parser."""
    parser = argparse.ArgumentParser(
        # The installed console script is `agentdiff`; running from a clone
        # is `python -m deepcompare`. Printing the wrong one sends people to
        # a command they do not have.
        prog=_program_name(), description="git diff for AI agents"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        command.register(sub)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - python -m deepcompare.cli is the same entry as python -m deepcompare
    sys.exit(main())
