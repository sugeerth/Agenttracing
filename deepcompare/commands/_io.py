"""The load-run-write shape every analysis command follows, once.

    traces = load_traces(dir_or_files, warn)              # SCHEMA validation, run ids from names
    result = <the analysis>                               # pure
    write_outputs(out_dir, reports, aggregate, template)  # report_<task>.json, aggregate.json, report.html

Nothing here computes; the engine modules do that.  This module reads
trace files into typed trajectories, keeping the ``harness`` block the
typed schema does not carry, and writes the artifacts with the messages
the commands have always printed — ``Wrote <path>`` on stdout, every
``warning:`` on stderr — so a script that reads the output sees the same
lines whichever command produced them.

``write_outputs`` is composed of ``write_reports``, ``write_aggregate``
and ``write_page``; a command whose analysis sits between the reports and
the aggregate (batch) calls the parts in that order instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Union

from ..report import render_html
from ..trace import Trajectory
from .paths import DEFAULT_TEMPLATE

__all__ = [
    "Warn", "warn_stderr", "safe_name", "run_id_from_name", "with_harness", "trace_files",
    "iter_traces", "load_traces", "template_from", "write_reports", "write_aggregate",
    "write_fleet", "write_page", "write_outputs",
]

#: how a loader reports a file it skipped: the message, without the ``warning:`` prefix
Warn = Callable[[str], None]

#: a directory of traces, or the trace files themselves
Paths = Union[str, Path, Iterable[Union[str, Path]]]


def warn_stderr(message: str) -> None:
    """The default ``warn``: ``warning: <message>`` on stderr."""
    print(f"warning: {message}", file=sys.stderr)


def safe_name(task_id: str) -> str:
    """A task id as a filename fragment: runs of anything outside
    ``[A-Za-z0-9._-]`` become one ``_``."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", task_id)


def run_id_from_name(path: Path) -> Optional[str]:
    """Run id from a ``<task>__<agent>__<run>.json`` filename, else None."""
    parts = path.stem.split("__")
    return parts[2] if len(parts) >= 3 else None


def with_harness(t: Trajectory, path: Path) -> Trajectory:
    """Keep the trace's ``harness`` block (adapter, graded_by, a SYNTHETIC
    note) beside the typed trajectory, so the report's trust section can
    say where the data came from; the typed schema does not carry it."""
    try:
        harness = json.loads(path.read_text(encoding="utf-8")).get("harness")
    except (OSError, ValueError, AttributeError):
        harness = None
    if isinstance(harness, dict):
        t.harness = harness  # type: ignore[attr-defined]
    return t


def trace_files(paths: Paths) -> list[Path]:
    """The files a command was pointed at: a directory's ``*.json`` in
    sorted order (an absent directory yields none), or the files given."""
    if isinstance(paths, (str, Path)):
        return sorted(Path(paths).glob("*.json"))
    return [Path(p) for p in paths]


def iter_traces(paths: Paths, warn: Optional[Warn] = None, *,
                run_ids: bool = False) -> Iterator[tuple[Path, Trajectory]]:
    """Every valid trace under ``paths`` as ``(path, trajectory)``, in file
    order; an invalid one is reported through ``warn`` and skipped.  With
    ``run_ids`` the runs layout's filename (``<task>__<agent>__<run>``)
    names the run, overriding the ``run_id`` the file carries."""
    warn = warn or warn_stderr
    for path in trace_files(paths):
        try:
            t = with_harness(Trajectory.from_json(path), path)
        except ValueError as exc:
            warn(f"skipping invalid trace: {exc}")
            continue
        if run_ids:
            run_id = run_id_from_name(path)
            if run_id:
                t.run_id = run_id
        yield path, t


def load_traces(paths: Paths, warn: Optional[Warn] = None, *,
                run_ids: bool = False) -> list[Trajectory]:
    """The valid trajectories under ``paths`` (see :func:`iter_traces`)."""
    return [t for _, t in iter_traces(paths, warn, run_ids=run_ids)]


def template_from(args: argparse.Namespace) -> Path:
    """The page template: ``--template`` when given, else the blocks page."""
    template = getattr(args, "template", None)
    return Path(template) if template else DEFAULT_TEMPLATE


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def write_reports(out_dir: Path, reports: list[dict]) -> list[Path]:
    """``report_<task>.json`` per pair report, in the order given."""
    return [_write_json(out_dir / f"report_{safe_name(report['task']['id'])}.json", report)
            for report in reports]


def write_aggregate(out_dir: Path, aggregate: dict) -> Path:
    """``aggregate.json``."""
    return _write_json(out_dir / "aggregate.json", aggregate)


def write_fleet(out_dir: Path, fleet: dict, reports: list[dict], aggregate: dict) -> Path:
    """``fleet.json``: the ranking with its spotlight reports inside it."""
    return _write_json(out_dir / "fleet.json", {"fleet": fleet, "reports": reports, "aggregate": aggregate})


def write_page(out_dir: Path, reports: list[dict], aggregate: dict, template: Path,
               fleet: Optional[dict] = None) -> Optional[Path]:
    """``report.html`` from the template with the data inlined; a missing
    template or one without the data marker is a warning, not a failure —
    the JSON is already on disk."""
    if template.is_file():
        try:
            html_path = render_html(reports, aggregate, template, out_dir / "report.html", fleet=fleet)
            print(f"Wrote {html_path}")
            return html_path
        except ValueError as exc:
            print(f"warning: could not render HTML: {exc}", file=sys.stderr)
    else:
        print(f"warning: viewer template not found at {template}; skipping report.html",
              file=sys.stderr)
    return None


def write_outputs(out_dir: Union[str, Path], reports: list[dict], aggregate: dict,
                  template: Path, html: bool = True, fleet: Optional[dict] = None) -> dict:
    """The three artifacts: ``report_<task>.json`` per report and
    ``aggregate.json`` (or, for a fleet, the one ``fleet.json`` that holds
    both), then ``report.html`` unless ``html`` is off.  Returns what was
    written under ``reports``, ``aggregate``, ``fleet`` and ``html``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict = {"reports": [], "aggregate": None, "fleet": None, "html": None}
    if fleet is None:
        written["reports"] = write_reports(out_dir, reports)
        written["aggregate"] = write_aggregate(out_dir, aggregate)
    else:
        written["fleet"] = write_fleet(out_dir, fleet, reports, aggregate)
    if html:
        written["html"] = write_page(out_dir, reports, aggregate, template, fleet=fleet)
    return written
