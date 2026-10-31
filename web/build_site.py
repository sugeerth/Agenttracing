"""Build the static web version of AgentDiff: a repeatability dashboard that runs the engine in the browser.

    python3 web/build_site.py OUT

writes, under ``OUT/agentdiff/``:

- ``try/index.html``: the dashboard (``web/try.html``). Pyodide, CPython compiled to WebAssembly, runs the
  engine in the visitor's browser, so a run is never uploaded anywhere. From repeated runs it shows pass^k with
  its interval, the flaky tasks, every run task by task, and the run-to-run spread.
- ``try/engine.zip``: the package, without what cannot or need not run in a browser (the harness and its
  network code, the hub, the CLI commands, the page template, the vendor CLI drivers, the MCP server).
- ``try/samples/<suite>/`` and ``try/samples.json``: three suites of repeated runs, each labelled recorded or
  synthetic in the manifest: one real agent under two harness versions (the recorded self-evolve example, its
  confirmation runs included), one real agent under two names (``demo/vendors/aa-suite``), and two simulated
  agents (``demo/runs``). Model names in the copies are replaced by "withheld".

No model identifier may appear in a file this writes: it fails if one does, after removing the one docstring
example the package carries and the model fields of the sample runs. Standard library only.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
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
SELF_EVOLVE = ROOT / "demo" / "selfevolve" / "examples" / "haiku-hard-confirms"


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


def _withheld(text: str) -> str:
    """A run with every model name replaced: the copy names no model."""
    return MODEL_NAME.sub("withheld", text)


def _harness_suite() -> list:
    """The recorded self-evolve example as two agents, one per harness version: each generation's runs and its
    confirmation runs, renamed so the file and agent names carry the harness, not the model."""
    out = []
    for version in ("0", "1"):
        for folder in sorted(SELF_EVOLVE.glob(f"g0-h{version}*")):
            for path in sorted((folder / "traces").glob("*.json")):
                run = json.loads(path.read_text(encoding="utf-8"))
                run["agent"] = {"name": f"harness v{version}", "model": "withheld", "version": f"v{version}"}
                run["run_id"] = f"{folder.name}-{run.get('run_id') or path.stem.split('__')[-1]}"
                name = f"{run['task']['id']}__v{version}__{folder.name}-{path.stem.split('__')[-1]}.json"
                out.append((name, json.dumps(run, indent=1)))
    return out


def samples() -> tuple:
    """(manifest, {path under samples/: text}) for the three suites."""
    suites = [
        ("harness", "One agent, two harness versions: 6 hard tasks, 3 to 6 runs each", "recorded", _harness_suite()),
        ("aa", "One agent under two names: 6 tasks, 2 runs each", "recorded",
         [(p.name, p.read_text(encoding="utf-8")) for p in sorted((ROOT / "demo/vendors/aa-suite/traces").glob("*.json"))]),
        ("sim", "Two simulated agents: 8 tasks, 3 runs each", "synthetic",
         [(p.name, p.read_text(encoding="utf-8")) for p in sorted((ROOT / "demo/runs/traces").glob("*.json"))]),
    ]
    files, datasets = {}, []
    for sid, label, data, runs in suites:
        for name, text in runs:
            files[f"{sid}/{name}"] = _withheld(text)
        datasets.append({"id": sid, "label": label, "data": data, "files": [name for name, _ in runs]})
    return {"datasets": datasets}, files


def build(out: Path) -> dict:
    """Write the site under ``out/agentdiff/``; returns {path: bytes written}."""
    site = out / "agentdiff"
    manifest, sample_files = samples()
    written = {
        "try/index.html": (ROOT / "web" / "try.html").read_bytes(),
        "try/engine.zip": engine_zip(),
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
