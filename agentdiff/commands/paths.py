"""Where the report templates live, shared by the CLI and its commands.

A template is found in one of two places, in this order: the checkout's
``web/`` directory, which is what a developer edits and what the build
test pins, and the copy inside the package, which is what a wheel
carries — setuptools can only ship files that live under the package, so
``web/build_blocks.py`` writes the page to both.  Resolving in that order
means an editable install follows the file being edited, and an
installed one still has the page it writes.  A template that is in
neither place is returned as the checkout path it would have had, so the
caller's error names a path a person can act on.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent.parent
_CHECKOUT = _PACKAGE.parent / "web"
_PACKAGED = _PACKAGE / "page"


def _template(name: str) -> Path:
    """The checkout's copy if it exists, else the packaged one, else the
    checkout path (so a missing template is reported where it belongs)."""
    checkout = _CHECKOUT / name
    if checkout.is_file():
        return checkout
    packaged = _PACKAGED / name
    if packaged.is_file():
        return packaged
    return checkout


#: the blocks page: default report template for batch, runs, fleet, demo,
#: compare --html and the re-render after replay/why
DEFAULT_TEMPLATE = _template("blocks.html")
#: the earlier single-file viewer, kept for ``--template web/viewer.html``
LEGACY_TEMPLATE = _template("viewer.html")
#: the lightweight agent-selection view, written by ``select``
SELECT_TEMPLATE = _template("select.html")

__all__ = ["DEFAULT_TEMPLATE", "LEGACY_TEMPLATE", "SELECT_TEMPLATE"]
