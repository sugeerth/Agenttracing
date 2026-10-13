"""Writes the synthetic self-evolving harness a downloaded build carries (examples/stand-in-learns-to-check).

It is the real `agentdiff self-evolve` command, CLI plumbing and all, with
Claude Code played by the stand-in in tests/fixtures/vendors/fake_claude.py
in `careless` mode: it fixes the bug but skips the check unless the
harness tells it to run one. So g0 fails, the evals name the missing
check, the instruction is tried paired, kept, and g1 passes. Nothing here
is a real agent, and the EXAMPLE file says so wherever it is listed.

    python3 demo/selfevolve/make_example.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "examples" / "stand-in-learns-to-check"
NOTE = ("synthetic: Claude Code played by a scripted stand-in that skips the check unless told to run one "
        "(tests/fixtures/vendors/fake_claude.py); the self-evolve command and its decisions are real")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tasks = []
        for i in range(3):
            shutil.copytree(ROOT / "demo" / "vendors" / "bugfix", tmp / f"pricing-{i}",
                            ignore=shutil.ignore_patterns("__pycache__"))
            tasks.append({"id": f"pricing-{i}", "prompt": "Fix the bug in pricing.py.", "workspace": f"pricing-{i}",
                          "check": "python3 -m unittest -q"})
        (tmp / "tasks.json").write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
        if OUT.exists():
            shutil.rmtree(OUT)
        env = dict(os.environ, FAKE_VENDOR_MODE="careless", PYTHONPATH=str(ROOT))
        subprocess.run([sys.executable, "-m", "agentdiff", "self-evolve", "--task", str(tmp / "tasks.json"),
                        "--agent", "claude", "--claude-bin", str(ROOT / "tests" / "fixtures" / "vendors" / "fake_claude.py"),
                        "--runs", "2", "--generations", "3", "-o", str(OUT)], env=env, check=True)
    for arm in OUT.glob("g*-h*"):
        # kept in the repository: no raw CLI output, and not the arm's own "ignore everything"
        shutil.rmtree(arm / "raw", ignore_errors=True)
        (arm / ".gitignore").unlink(missing_ok=True)
    (OUT / "EXAMPLE").write_text(NOTE + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
