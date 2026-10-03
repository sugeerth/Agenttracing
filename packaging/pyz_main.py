"""Run agentdiff from its one-file build (``agentdiff.pyz``).

The archive unpacks itself once into a cache folder named after its own
contents, then runs from there. Its pages and scripts are then real files,
and a second run starts at once. Needs Python 3.10 or later, nothing else.
"""

import hashlib
import os
import sys
import zipfile


def _cache_root() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "agentdiff")


def main() -> int:
    if sys.version_info < (3, 10):
        sys.stderr.write("agentdiff needs Python 3.10 or later (this is %d.%d)\n" % sys.version_info[:2])
        return 2
    archive = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    with open(archive, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    target = os.path.join(_cache_root(), h.hexdigest()[:16])
    if not os.path.isdir(os.path.join(target, "agentdiff")):
        tmp = target + ".part-%d" % os.getpid()
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(tmp, target)
        except OSError:  # another run unpacked it first
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    sys.path.insert(0, target)
    from agentdiff.cli import download_main
    return download_main()


if __name__ == "__main__":
    sys.exit(main())
