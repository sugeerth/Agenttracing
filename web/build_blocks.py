#!/usr/bin/env python3
"""Build web/blocks.html from web/blocks/.

The page must stay one self-contained file — no CDN, no sibling assets — so
it can be opened from a file:// URL offline, which is how a report actually
gets read. Keeping the modules separate on disk and inlining them here means
that constraint costs nothing in authoring.

Modules are concatenated in filename order; ``00_core.js`` first, then every
``NN_*.js``. Underscore-prefixed files are parts, not modules.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLOCKS = ROOT / "blocks"
SHELL = BLOCKS / "_shell.html"
OUTPUT = ROOT / "blocks.html"

VENDOR = ROOT / "vendor"
VENDOR_MARKER = "<!--@VENDOR@-->"
CORE_MARKER = "<!--@CORE@-->"
MODULES_MARKER = "<!--@MODULES@-->"


def module_files() -> list[Path]:
    """Every block module, in load order, excluding the core and parts."""
    return sorted(
        path for path in BLOCKS.glob("*.js")
        if path.name != "00_core.js" and not path.name.startswith("_")
    )


def vendor_files() -> list[Path]:
    """Third-party libraries inlined before the core, in filename order.
    Each ships with its licence file beside it (``LICENSE.<name>``)."""
    if not VENDOR.is_dir():
        return []
    return sorted(VENDOR.glob("*.min.js"))


def wrap_vendor(path: Path) -> str:
    """A vendored library as an inline script, its licence quoted first so
    the built page carries the attribution the licence asks for."""
    name = path.name.split(".")[0]
    licence = VENDOR / f"LICENSE.{name}"
    if not licence.is_file():
        raise SystemExit(f"{path.name} has no {licence.name} beside it")
    text = licence.read_text(encoding="utf-8").strip().replace("*/", "* /")
    source = path.read_text(encoding="utf-8")
    if "</script>" in source:
        source = source.replace("</script>", "<\\/script>")
    return (f"<!-- vendor: {path.name} -->\n<script>\n/*\n{text}\n*/\n{source}\n</script>")


def wrap(path: Path) -> str:
    """One module as an inline script tag, labelled for debugging."""
    source = path.read_text(encoding="utf-8")
    # A literal </script> inside a JS string would close the tag early and
    # break the page — a real hazard once a module renders HTML snippets.
    if "</script>" in source:
        source = source.replace("</script>", "<\\/script>")
    return f"<!-- {path.name} -->\n<script>\n{source}\n</script>"


def build() -> str:
    shell = SHELL.read_text(encoding="utf-8")
    for marker in (VENDOR_MARKER, CORE_MARKER, MODULES_MARKER):
        if marker not in shell:
            raise SystemExit(f"{SHELL} is missing the {marker} marker")

    vendor = "\n".join(wrap_vendor(path) for path in vendor_files())
    core = wrap(BLOCKS / "00_core.js")
    modules = module_files()
    body = "\n".join(wrap(path) for path in modules)

    page = (shell.replace(VENDOR_MARKER, vendor).replace(CORE_MARKER, core)
                 .replace(MODULES_MARKER, body))

    # render_html() replaces the single line carrying this marker, so exactly
    # one must survive the build or the CLI cannot inject report data.
    hits = [line for line in page.splitlines() if "window.DEEPCOMPARE_DATA" in line]
    if len(hits) != 1:
        raise SystemExit(
            f"expected exactly 1 window.DEEPCOMPARE_DATA line, found {len(hits)}; "
            "a module must not mention the marker"
        )
    return page, modules


def main() -> int:
    page, modules = build()
    OUTPUT.write_text(page, encoding="utf-8")
    registered = len(re.findall(r"AgentDiff\.block\(", page))
    size = len(page.encode("utf-8"))
    print(f"wrote {OUTPUT.relative_to(ROOT.parent)} "
          f"— {len(vendor_files())} vendored librar{'y' if len(vendor_files()) == 1 else 'ies'}, "
          f"{len(modules)} module(s), {registered} block(s), {size/1024:.0f} KB")
    for path in modules:
        count = len(re.findall(r"AgentDiff\.block\(", path.read_text(encoding="utf-8")))
        print(f"  {path.name:<28} {count} block(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
