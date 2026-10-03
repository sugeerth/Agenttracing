"""Build agentdiff to download and run.

    python packaging/build.py              # dist/agentdiff.pyz: one file, any OS with Python 3.10+
    python packaging/build.py --binary     # also dist/agentdiff-<os>-<arch>: no Python needed (PyInstaller)

Both carry a few example runs (``agentdiff hub --examples``): long runs over
days, a loop and its fix, a duel of two agents, an RL policy. CI builds the
binary for Linux, macOS and Windows (``.github/workflows/binaries.yml``).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: the example runs a build carries, as (from, to under agentdiff/_examples)
EXAMPLES = [("demo/longrun/traces", "long-runs/traces"), ("demo/loops/traces", "loops/traces"),
            ("demo/vendors/live", "duel"), ("demo/rl/traces", "rl/traces")]
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "raw", ".agentdiff-hub")


def stage(dest: Path) -> Path:
    """The package as a build ships it: agentdiff, deepcompare, the examples."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for pkg in ("agentdiff", "deepcompare"):
        if (ROOT / pkg).is_dir():
            shutil.copytree(ROOT / pkg, dest / pkg, ignore=_SKIP)
    for src, to in EXAMPLES:
        if (ROOT / src).is_dir():
            shutil.copytree(ROOT / src, dest / "agentdiff" / "_examples" / to, ignore=_SKIP)
    return dest


def pyz(out: Path) -> Path:
    staged = stage(out.parent / "stage-pyz")
    shutil.copy(ROOT / "packaging" / "pyz_main.py", staged / "__main__.py")
    target = out
    zipapp.create_archive(staged, target, interpreter="/usr/bin/env python3", compressed=True)
    shutil.rmtree(staged)
    return target


def binary(dist: Path) -> Path:
    staged = stage(dist.parent / "stage-bin")
    name = f"agentdiff-{platform.system().lower()}-{platform.machine().lower()}"
    sep = ";" if os.name == "nt" else ":"
    data = [f"{staged / 'agentdiff' / 'hub' / 'static'}{sep}agentdiff/hub/static",
            f"{staged / 'agentdiff' / '_examples'}{sep}agentdiff/_examples"]
    if (staged / "agentdiff" / "page").is_dir():
        data.append(f"{staged / 'agentdiff' / 'page'}{sep}agentdiff/page")
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--name", name, "--distpath", str(dist),
           "--workpath", str(dist.parent / "pyi-work"), "--specpath", str(dist.parent / "pyi-work"),
           "--paths", str(staged), "--collect-submodules", "agentdiff", "--noconfirm", "--clean"]
    for d in data:
        cmd += ["--add-data", d]
    cmd.append(str(ROOT / "packaging" / "entry.py"))
    subprocess.run(cmd, check=True)
    shutil.rmtree(staged)
    return dist / (name + (".exe" if os.name == "nt" else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary", action="store_true", help="also build the standalone binary (needs PyInstaller)")
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    args = ap.parse_args()
    dist = Path(args.dist).resolve()  # PyInstaller reads relative data paths from its spec folder
    dist.mkdir(parents=True, exist_ok=True)
    p = pyz(dist / "agentdiff.pyz")
    print(f"{p}  ({p.stat().st_size / 1e6:.1f} MB): python3 {p.name} hub --examples --open")
    if args.binary:
        b = binary(dist)
        print(f"{b}  ({b.stat().st_size / 1e6:.1f} MB): ./{b.name} hub --examples --open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
