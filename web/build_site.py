"""Build the static web version of AgentDiff: a prebuilt demo report and an in-browser playground.

    python3 web/build_site.py OUT

writes, under ``OUT/agentdiff/``:

- ``demo/index.html``: the batch report over ``demo/traces`` (synthetic demo runs), the same page
  ``agentdiff batch`` writes. It opens instantly and needs no engine.
- ``try/index.html``: the playground (``web/try.html``). Pyodide, CPython compiled to WebAssembly, runs the
  engine in the visitor's browser, so a trace is never uploaded anywhere.
- ``try/engine.zip``: the package, without what cannot or need not run in a browser (the harness and its
  network code, the hub, the CLI commands, the page template, the vendor CLI drivers, the MCP server).
- ``try/blocks.html``: the page template the report is rendered into.
- ``try/samples/`` and ``try/samples.json``: the demo pairs (synthetic) and one recorded pair (real runs,
  model name withheld), each labelled as such in the manifest.

No model identifier may appear in a file this writes: it fails if one does, after removing the one docstring
example the package carries. Standard library only.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agentdiff"
SKIP_DIRS = {"harness", "hub", "commands", "page", "__pycache__"}
SKIP_FILES = {"vendors.py", "mcpserver.py"}
#: the docstring example in record.py names a model; the shipped copy says "my-model" instead
SCRUB = {"record.py": ('model="claude-sonnet-5"', 'model="my-model"')}
MODEL_NAME = re.compile(r"claude-(?:haiku|sonnet|opus|fable|[0-9])[\w.\-]*|gpt-[0-9][\w.\-]*|gemini-[\w.\-]+"
                        r"|\b(?:haiku|sonnet|opus)\b", re.I)
#: the recorded pair: one agent under two names on a real task (demo/vendors/aa-suite)
RECORDED = [("bugfix-pricing__A__r1.json", "bugfix-pricing__B__r1.json",
             "bugfix-pricing: one agent under two names (real runs, model withheld)")]


def engine_files() -> list:
    """The package's .py files that ship, as (path inside the zip, text)."""
    out = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(ROOT)
        parts = rel.parts[1:]
        if any(p in SKIP_DIRS for p in parts[:-1]) or (len(parts) == 1 and parts[0] in SKIP_FILES):
            continue
        text = path.read_text(encoding="utf-8")
        if path.name in SCRUB and len(parts) == 1:
            old, new = SCRUB[path.name]
            text = text.replace(old, new)
        out.append((rel.as_posix(), text))
    return out


def engine_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in engine_files():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # fixed: the same source, the same bytes
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return buf.getvalue()


def samples() -> tuple:
    """(manifest, {file name: text}) for the demo pairs and the recorded pair."""
    files, pairs = {}, []
    by_task: dict = {}
    for path in sorted((ROOT / "demo" / "traces").glob("*.json")):
        by_task.setdefault(path.name.split("__")[0], []).append(path)
    for task, paths in sorted(by_task.items()):
        if len(paths) != 2:
            continue
        for p in paths:
            files[p.name] = p.read_text(encoding="utf-8")
        pairs.append({"label": f"{task}: two demo agents (synthetic)", "a": paths[0].name, "b": paths[1].name,
                      "data": "synthetic"})
    for a, b, label in RECORDED:
        for name in (a, b):
            files[name] = (ROOT / "demo" / "vendors" / "aa-suite" / "traces" / name).read_text(encoding="utf-8")
        pairs.append({"label": label, "a": a, "b": b, "data": "recorded"})
    return {"pairs": pairs}, files


def demo_report(tmp: Path) -> bytes:
    out = tmp / "batch"
    subprocess.run([sys.executable, "-m", "agentdiff", "batch", str(ROOT / "demo" / "traces"), "-o", str(out),
                    "--template", str(ROOT / "web" / "blocks.html")], cwd=str(ROOT), check=True, capture_output=True)
    return (out / "report.html").read_bytes()


def build(out: Path) -> dict:
    """Write the site under ``out/agentdiff/``; returns {path: bytes written}."""
    site = out / "agentdiff"
    manifest, sample_files = samples()
    with tempfile.TemporaryDirectory() as tmp:
        written = {
            "demo/index.html": demo_report(Path(tmp)),
            "try/index.html": (ROOT / "web" / "try.html").read_bytes(),
            "try/engine.zip": engine_zip(),
            "try/blocks.html": (ROOT / "web" / "blocks.html").read_bytes(),
            "try/samples.json": (json.dumps(manifest, indent=1) + "\n").encode("utf-8"),
        }
    for name, text in sample_files.items():
        written[f"try/samples/{name}"] = text.encode("utf-8")
    texts = {name: data for name, data in written.items() if not name.endswith(".zip")}
    texts.update({f"try/engine.zip:{n}": t.encode("utf-8") for n, t in engine_files()})
    named = sorted({m for name, data in texts.items() for m in MODEL_NAME.findall(data.decode("utf-8", "replace"))})
    if named:
        raise SystemExit(f"refusing to write: model identifiers in the site files: {', '.join(named)}")
    for name, data in written.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return written


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    out = Path(args[0])
    if (out / "agentdiff" / "try").exists():
        shutil.rmtree(out / "agentdiff" / "try")
    written = build(out)
    for name, data in sorted(written.items()):
        if not name.startswith("try/samples/"):
            print(f"{out / 'agentdiff' / name}  {len(data):,} bytes")
    print(f"{sum(1 for n in written if n.startswith('try/samples/'))} sample traces under {out / 'agentdiff/try/samples'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
