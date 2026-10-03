"""Build agentdiff to download and run.

    python packaging/build.py              # dist/agentdiff.pyz: one file, any OS with Python 3.10+
    python packaging/build.py --binary     # also dist/agentdiff-<os>-<arch>: no Python needed (PyInstaller),
                                           # and its download: .tar.gz (.zip on Windows) with a README

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
    """The package as a build ships it: agentdiff and the examples."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(ROOT / "agentdiff", dest / "agentdiff", ignore=_SKIP)
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


README = """AgentDiff: git diff for AI agents

Start it (or double-click it):
    {run}

It opens the hub in your browser at http://127.0.0.1:8790 (sign in: demo / demo)
with your Claude Code sessions, read from ~/.claude/projects and followed live as
you work, and a few example runs. Nothing leaves this machine. Ctrl-C stops it.

The first time:
    macOS    it is not notarised, so macOS asks first: right-click it, Open, Open;
             or run  xattr -d com.apple.quarantine agentdiff
    Windows  SmartScreen may warn: More info, Run anyway.

Then:
{then}

{sums}"""
THEN = [("--help", "every command"),
        ('guard --install --check "pytest -q"', "in a project: Claude Code may not stop until the check"),
        ("", "passes, and every session after it is graded"),
        ("timeline path/to/trace.json", "one run on its clock, as a page"),
        ("hub --no-demo --add-user you", "your own sign-in instead of the demo one")]


def bundle(binary_path: Path, dist: Path) -> Path:
    """The download: the binary named ``agentdiff`` beside a README, in an archive that keeps it runnable
    (a tar.gz on macOS and Linux, a zip on Windows)."""
    import hashlib
    import tarfile
    import zipfile
    windows = binary_path.suffix == ".exe"
    exe = "agentdiff.exe" if windows else "agentdiff"
    digest = hashlib.sha256(binary_path.read_bytes()).hexdigest()
    cmd = exe if windows else "./agentdiff"
    width = max(len(f"{cmd} {a}") for a, _ in THEN if a) + 3
    then = "\n".join(f"    {(cmd + ' ' + a) if a else '':<{width}}{what}" for a, what in THEN)
    readme = README.format(run=cmd, then=then, sums=f"sha256  {digest}  {exe}\n")
    stem = binary_path.name[: -len(".exe")] if windows else binary_path.name
    if windows:
        out = dist / f"{stem}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(binary_path, f"{stem}/{exe}")
            z.writestr(f"{stem}/README.txt", readme.replace("\n", "\r\n"))
        return out
    out = dist / f"{stem}.tar.gz"
    with tarfile.open(out, "w:gz") as t:
        info = t.gettarinfo(str(binary_path), f"{stem}/{exe}")
        info.mode = 0o755
        with open(binary_path, "rb") as fh:
            t.addfile(info, fh)
        data = readme.encode()
        r = tarfile.TarInfo(f"{stem}/README.txt")
        r.size, r.mode, r.mtime = len(data), 0o644, int(binary_path.stat().st_mtime)
        import io
        t.addfile(r, io.BytesIO(data))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary", action="store_true", help="also build the standalone binary (needs PyInstaller)")
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    args = ap.parse_args()
    dist = Path(args.dist).resolve()  # PyInstaller reads relative data paths from its spec folder
    dist.mkdir(parents=True, exist_ok=True)
    p = pyz(dist / "agentdiff.pyz")
    print(f"{p}  ({p.stat().st_size / 1e6:.1f} MB): python3 {p.name}  (opens the hub on your Claude Code sessions)")
    if args.binary:
        b = binary(dist)
        print(f"{b}  ({b.stat().st_size / 1e6:.1f} MB): ./{b.name}  (a double-click opens the hub)")
        a = bundle(b, dist)
        print(f"{a}  ({a.stat().st_size / 1e6:.1f} MB): the download, the binary beside a README")
    return 0


if __name__ == "__main__":
    sys.exit(main())
