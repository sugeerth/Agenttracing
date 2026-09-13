"""Where the report templates live, shared by the CLI and its commands."""

from __future__ import annotations

from pathlib import Path

#: the blocks page: default report template for batch, runs, fleet, demo,
#: compare --html and the re-render after replay/why
DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent.parent / "web" / "blocks.html"
#: the earlier single-file viewer, kept for ``--template web/viewer.html``
LEGACY_TEMPLATE = Path(__file__).resolve().parent.parent.parent / "web" / "viewer.html"
