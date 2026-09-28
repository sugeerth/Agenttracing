"""Argument groups two or more commands share, and the helpers that read
them back: the CI-artifact flags and their exit-code policy, the provider
options, the trace-database source.

A group is added by one function and read by one function, so a command
that takes ``--junit`` behaves like every other command that takes it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from ..ci import DEFAULT_FAIL_ON, FAIL_ON_CHOICES, collect_trace_paths, exit_code as ci_exit_code, write_ci_artifacts
from ..trace import Trajectory
from ..tracedb import TraceDB
from ._io import load_traces

__all__ = ["add_ci_args", "emit_ci", "provider_option_args", "provider_options", "split_spec",
           "db_source_args", "load_source"]


# ------------------------------------------------------------- CI artifacts

def add_ci_args(parser: argparse.ArgumentParser) -> None:
    """CI-artifact flags, shared by the commands that produce a verdict."""
    parser.add_argument("--junit", nargs="?", const="junit.xml",
                        help="write JUnit XML (default name: junit.xml, "
                             "relative to -o)")
    parser.add_argument("--sarif", nargs="?", const="results.sarif",
                        help="write SARIF 2.1.0 for code scanning "
                             "(default name: results.sarif, relative to -o)")
    parser.add_argument("--job-summary", nargs="?", const="ci-summary.md",
                        help="write the Markdown job summary "
                             "(default name: ci-summary.md, relative to -o)")
    parser.add_argument("--github-annotations", action="store_true",
                        help="print ::error/::warning/::notice workflow "
                             "commands on stdout, and append the job summary "
                             "to $GITHUB_STEP_SUMMARY when set")
    parser.add_argument("--fail-on", choices=FAIL_ON_CHOICES,
                        default=DEFAULT_FAIL_ON,
                        help="severity that fails the build: never | "
                             "regression (default) | pathology | any "
                             "(any includes checks that could not be "
                             "measured). Exit 0 = clean, 1 = findings at or "
                             "above the threshold, 2 = usage/data error")
    parser.set_defaults(ci=True)


def emit_ci(args: argparse.Namespace, result: dict, out_dir: Path,
            reports: Optional[list[dict]] = None,
            trace_dir: Optional[Path] = None) -> int:
    """Write the requested CI artifacts and return the policy exit code."""
    trace_paths = collect_trace_paths(trace_dir) if trace_dir else None
    for path in write_ci_artifacts(
        result,
        out_dir,
        reports=reports,
        trace_paths=trace_paths,
        junit=args.junit,
        sarif=args.sarif,
        summary=args.job_summary,
        annotations=args.github_annotations,
        fail_on=args.fail_on,
    ):
        print(f"Wrote {path}")
    return ci_exit_code(result, reports=reports, fail_on=args.fail_on,
                        trace_paths=trace_paths)


# ---------------------------------------------------------------- providers

def provider_option_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=None,
                        help="endpoint base URL (default: the provider's env var or "
                             "public API)")
    parser.add_argument("--temperature", type=float, default=None,
                        help="sampling temperature (default: the provider's, 0.0)")
    parser.add_argument("--api-key-env", default=None,
                        help="name of the environment variable holding the key "
                             "(never the key itself)")


def provider_options(args: argparse.Namespace) -> dict:
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


def split_spec(entry: str) -> tuple:
    """``NAME=kind:rest`` → (name or None, ``kind:rest``); the ``=`` that
    separates the name is the first one, so command templates keep theirs."""
    head, sep, rest = entry.partition("=")
    if sep and ":" in head:
        return None, entry
    return (head if sep else None), (rest if sep else entry)


# ----------------------------------------------------- the trace database

def db_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=None, metavar="FILE",
                        help="read traces from this trace database instead of a directory")
    parser.add_argument("--db-agent", action="append", default=None, metavar="NAME",
                        help="with --db: only these agents (repeatable)")
    parser.add_argument("--db-family", default=None, metavar="FAMILY", help="with --db: only this task family")


def load_source(args: argparse.Namespace, attr: str = "tracesdir") -> list[Trajectory]:
    """Trajectories from ``--db FILE`` when given, else from the directory."""
    db_path = getattr(args, "db", None)
    if db_path:
        with TraceDB(db_path) as db:
            filters = {}
            if getattr(args, "db_agent", None):
                filters["agent"] = list(args.db_agent)
            if getattr(args, "db_family", None):
                filters["family"] = args.db_family
            return db.trajectories(**filters)
    directory = getattr(args, attr, None)
    if not directory:
        print("error: give a trace directory or --db FILE", file=sys.stderr)
        return []
    return load_traces(Path(directory))
