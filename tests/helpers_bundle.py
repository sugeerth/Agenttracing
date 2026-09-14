"""The batch demo analysed once per test process and bundled once, for the
bundle, MCP and HTTP tests — the pattern the Grafana tests use, so a
test file that needs a bundle pays for one analysis, not one per test."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.commands import bundle as bundle_cmd  # noqa: E402

BATCH = ROOT / "demo" / "traces"
_CACHE: dict = {}


def batch_output() -> Path:
    """``batch demo/traces`` written once into a temporary directory."""
    if "batch" not in _CACHE:
        # the member's label is the directory's name, and the tests key runs by it
        out = Path(tempfile.mkdtemp(prefix="agentdiff-bundle-")) / "batch"
        result = subprocess.run([sys.executable, "-m", "deepcompare", "batch", str(BATCH), "-o", str(out)],
                                cwd=ROOT, capture_output=True, text=True, timeout=900)
        assert result.returncode == 0, result.stderr[-2000:]
        _CACHE["batch"] = out
    return _CACHE["batch"]


def parser() -> argparse.ArgumentParser:
    """A parser with the bundle command registered, the way the CLI will."""
    p = argparse.ArgumentParser(prog="agentdiff")
    sub = p.add_subparsers(dest="command", required=True)
    bundle_cmd.register(sub)
    return p


def demo_bundle() -> Path:
    """The batch output bundled once through the command itself."""
    if "bundle" not in _CACHE:
        out = Path(tempfile.mkdtemp(prefix="agentdiff-bundle-")) / "b"
        args = parser().parse_args(["bundle", str(batch_output()), "-o", str(out), "--name", "demo",
                                    "--locator", "file:///tmp/demo"])
        assert bundle_cmd.run(args) == 0
        _CACHE["bundle"] = out
    return _CACHE["bundle"]
