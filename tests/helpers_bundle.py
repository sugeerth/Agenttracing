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
TRAIN = ROOT / "demo" / "rl" / "train"
LINEAGE = ROOT / "demo" / "evolve" / "lineage"
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


def demo_outputs() -> dict:
    """The three demo outputs — ``batch demo/traces``, ``runs demo/rl/train``,
    ``coevolve demo/evolve/lineage`` — each written once per test process."""
    if "outputs" not in _CACHE:
        base = Path(tempfile.mkdtemp(prefix="agentdiff-bundle-three-"))
        outputs = {"batch": batch_output()}
        for kind, command, source in (("runs", "runs", TRAIN), ("coevolve", "coevolve", LINEAGE)):
            out = base / kind
            result = subprocess.run([sys.executable, "-m", "deepcompare", command, str(source), "-o", str(out)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=900)
            assert result.returncode == 0, result.stderr[-2000:]
            outputs[kind] = out
        _CACHE["outputs"] = outputs
    return _CACHE["outputs"]


def full_bundle() -> Path:
    """The three outputs bundled once through the command with ``--traces``
    naming the three demos' trace directories, so every run has level 3."""
    if "full" not in _CACHE:
        outputs = demo_outputs()
        out = Path(tempfile.mkdtemp(prefix="agentdiff-bundle-full-")) / "full"
        args = parser().parse_args(["bundle", str(outputs["batch"]), str(outputs["runs"]), str(outputs["coevolve"]),
                                    "-o", str(out), "--name", "agentdiff-demo",
                                    "--traces", str(BATCH), str(TRAIN), str(LINEAGE)])
        assert bundle_cmd.run(args) == 0
        _CACHE["full"] = out
    return _CACHE["full"]
