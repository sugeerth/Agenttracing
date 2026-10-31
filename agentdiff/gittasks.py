"""Tasks from a repository's own history, for a harness to evolve on.

A commit that changed code and its tests is a task with a grader nobody
had to write. The agent starts from the commit's parent and is given the
commit's message. The tests the commit added or changed are held out,
outside the workspace and never named to the agent, and grade the run:
they are copied in over the agent's work and run when it has finished.

A task is kept only if its grader is sound for it: the held-out tests
fail on the parent (so there is something to do) and pass on the commit
itself (so it can be done, and the grader agrees with the change the
project made). Every commit looked at is reported with why it was taken
or why not.

Only ``git`` and the project's own test runner are used, locally: nothing
here reaches a network.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List, Optional

__all__ = ["candidates", "build", "RUNNER", "PROMPT"]

#: what the agent is told, around the commit's own message
PROMPT = ("In this repository, make the following change:\n\n{message}\n\n"
          "Tests that check this change exist, but they are not given to you.")
#: the file the check runs: copies the held-out tests over the agent's work, runs them, and holds the run to
#: the tests that passed when the task was made
RUNNER = "run_check.py"
#: how a test runner's output names the tests that passed: pytest's ``-rA`` summary and unittest's ``-v`` lines.
#: Shared, as source, by the runner (which runs under the project's own Python) and by :func:`build`
_PASSED_SOURCE = r'''
import re as _re

_COLOUR = _re.compile(r"\x1b\[[0-9;]*m")
_PYTEST_PASSED = _re.compile(r"^PASSED (\S+)", _re.M)
_UNITTEST_OK = _re.compile(r"^(\w+) \(([\w.]+)\)(?:\n.*?)?\s\.\.\. ok$", _re.M)


def passed_ids(text):
    """The tests the output says passed, as a set of ids; empty when it names none. Colour codes are ignored."""
    text = _COLOUR.sub("", text)
    ids = set(_PYTEST_PASSED.findall(text))
    for name, where in _UNITTEST_OK.findall(text):
        ids.add(where if where.endswith("." + name) else where + "." + name)
    return ids
'''
_RUNNER_SOURCE = '''"""Copies held-out tests into the workspace, over whatever is there, then runs the test command.

With an expectation file (the tests that passed on the commit the task was made from), a run passes only if
every one of them passed again: a test skipped, deselected or swapped out by the work under test does not
count as passed, whatever the exit code says."""
import json
import shutil
import subprocess
import sys
''' + _PASSED_SOURCE + '''

hidden, expect, cmd = sys.argv[1], sys.argv[2], sys.argv[4:]
shutil.copytree(hidden, ".", dirs_exist_ok=True)
# the two streams apart: unittest reports on stderr, pytest on stdout, and a test that prints mid-line would
# split a "name ... ok" line if they were merged
done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
sys.stdout.write(done.stdout)
sys.stdout.write(done.stderr)
if done.returncode or expect == "-":
    sys.exit(done.returncode)
missing = sorted(set(json.load(open(expect))) - passed_ids(done.stdout) - passed_ids(done.stderr))
if missing:
    print(f"held-out tests that passed on the commit did not pass here ({len(missing)}): "
          + ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
    sys.exit(1)
'''
_shared: dict = {}
exec(_PASSED_SOURCE, _shared)
passed_ids = _shared["passed_ids"]
_TRAILER = re.compile(r"^(?:co-authored-by|signed-off-by|reviewed-by|change-id|claude-session):.*$", re.I | re.M)
_TEST = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")


def _git(repo: Path, *args: str, binary: bool = False):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def _is_test(path: str) -> bool:
    return bool(_TEST.search(path))


def candidates(repo: Path, *, look: int = 200, most_files: int = 12) -> List[dict]:
    """Commits, newest first, with one parent, that changed at least one test file and one other source file,
    and no more than ``most_files`` files in all."""
    out = []
    for sha in _git(repo, "log", "--no-merges", f"-n{look}", "--format=%H").split():
        parents = _git(repo, "log", "-1", "--format=%P", sha).split()
        if len(parents) != 1:
            continue
        files = [ln.split("\t") for ln in _git(repo, "show", "--name-status", "--format=", sha).splitlines() if ln]
        changed = [(st[0], f[-1]) for f in files for st in [f[0]] if f]
        tests = [p for st, p in changed if _is_test(p) and st in ("A", "M") and p.endswith(".py")]
        code = [p for st, p in changed if not _is_test(p) and p.endswith(".py")]
        if tests and code and len(changed) <= most_files:
            message = _TRAILER.sub("", _git(repo, "log", "-1", "--format=%B", sha)).strip()
            out.append({"sha": sha, "parent": parents[0], "tests": tests, "code": code, "message": message})
    return out


def _extract(repo: Path, rev: str, dest: Path) -> None:
    """The tree of ``rev``, written to ``dest`` (git archive, read as a tar with nothing outside ``dest``)."""
    data = _git(repo, "archive", "--format=tar", rev, binary=True)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"{m.name}: outside the workspace")
            if m.issym() or m.islnk() or m.isdev():
                continue
            tar.extract(m, dest)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:28].strip("-") or "change"


def _test_command(python: str, tests: List[str], template: Optional[str]) -> List[str]:
    """The given template with ``{files}`` filled in; else pytest on the test files when it is installed for this
    Python, else unittest, which takes the files as paths too. Both name each test that passed (pytest ``-rA``,
    unittest ``-v``), so a run can be held to the tests the commit passed."""
    if template:
        return shlex.split(template.replace("{files}", " ".join(shlex.quote(t) for t in tests)))
    has = subprocess.run([python, "-c", "import pytest"], capture_output=True).returncode == 0
    return ([python, "-m", "pytest", "-q", "-rA", "--color=no", "-p", "no:cacheprovider", *tests] if has
            else [python, "-m", "unittest", "-v", *tests])


def _run(check: List[str], cwd: Path, timeout: float) -> tuple:
    try:
        done = subprocess.run(check, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    return done.returncode == 0, done.stdout + done.stderr


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-6:])


def _disagrees(check: List[str], tree: Path, want: bool, times: int, timeout: float) -> Optional[str]:
    """Runs ``check`` on a fresh copy of ``tree`` up to ``times`` times, stopping at the first run that does
    not come out ``want``. None when every run did; else what that run did, as "passed", "failed" or
    "timed out", after how many that agreed."""
    for n in range(times):
        with tempfile.TemporaryDirectory() as tmp:
            run_in = Path(tmp) / "tree"
            shutil.copytree(tree, run_in)
            got, _ = _run(check, run_in, timeout)
        if got is not want:
            did = "timed out" if got is None else "passed" if got else "failed"
            return f"{'passed' if want else 'failed'} {n + 1} time(s), then {did}"
    return None


def build(repo: Path, dest: Path, *, want: int = 6, look: int = 200, test_cmd: Optional[str] = None,
          timeout: float = 300.0, python: Optional[str] = None, on_progress=None, checks: int = 3) -> dict:
    """Up to ``want`` tasks from ``repo``'s history, each validated, written under ``dest`` with a tasks.json.
    A grader is checked ``checks`` times on the parent and on the commit, each run on a fresh copy: a task
    whose tests do not fail on the parent every time and pass on the commit every time is flaky, and left
    out, since a flaky grader would pass or fail the agent's runs by chance.
    Returns ``{"tasks": path or None, "kept": [...], "skipped": [{"sha", "subject", "why"}]}``."""
    say = on_progress or (lambda s: None)
    repo, dest = Path(repo).resolve(), Path(dest).resolve()
    python = python or shutil.which("python3") or shutil.which("python") or sys.executable
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "tasks").mkdir(parents=True)
    (dest / RUNNER).write_text(_RUNNER_SOURCE, encoding="utf-8")
    kept, skipped = [], []
    for c in candidates(repo, look=look):
        if len(kept) >= want:
            break
        subject = c["message"].splitlines()[0] if c["message"] else c["sha"][:8]
        tid = f"{c['sha'][:8]}-{_slug(subject)}"
        if len(c["message"]) < 20:
            skipped.append({"sha": c["sha"], "subject": subject, "why": "the message is too short to be a task"})
            continue
        hidden = dest / "hidden" / tid
        for t in c["tests"]:
            target = hidden / t
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(repo, "show", f"{c['sha']}:{t}", binary=True))
        command = _test_command(python, c["tests"], test_cmd)
        check = [python, str(dest / RUNNER), str(hidden), "-", "--", *command]
        work = dest / "tasks" / tid
        _extract(repo, c["parent"], work)
        say(f"{tid}: checking its grader on the parent and on the commit")
        expected: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            on_commit = Path(tmp) / "commit"
            _extract(repo, c["sha"], on_commit)
            after, after_out = _run(check, on_commit, timeout)
            if after:
                # the tests the commit passes, by name: a run passes only if each passes again, so work that
                # skips, deselects or swaps them out (a conftest.py, a patched runner) does not pass
                expected = sorted(passed_ids(after_out or ""))
                if expected:
                    expect = dest / "expect" / f"{tid}.json"
                    expect.parent.mkdir(parents=True, exist_ok=True)
                    expect.write_text(json.dumps(expected, indent=1) + "\n", encoding="utf-8")
                    check = [python, str(dest / RUNNER), str(hidden), str(expect), "--", *command]
            on_parent = Path(tmp) / "parent"
            shutil.copytree(work, on_parent)
            before, _ = _run(check, on_parent, timeout)
            why = None
            if after is not True:
                why = ("its tests do not pass on the commit itself"
                       + (f": {_tail(after_out)}" if after_out else ""))
            elif before is not False:
                why = "its tests already pass on the parent: nothing to do"
            elif checks > 1:
                # once each was the first look; the rest must agree with it, run for run
                for tree, want, where in ((work, False, "parent"), (on_commit, True, "commit")):
                    odd = _disagrees(check, tree, want, checks - 1, timeout)
                    if odd:
                        why = f"a flaky grader: on the {where} its tests {odd}"
                        break
        if why:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(hidden, ignore_errors=True)
            skipped.append({"sha": c["sha"], "subject": subject, "why": why})
            say(f"{tid}: skipped, {why.splitlines()[0]}")
            continue
        kept.append({"id": tid, "workspace": f"tasks/{tid}", "prompt": PROMPT.format(message=c["message"]),
                     "checked": max(1, checks), "held_to": len(expected),
                     "check": subprocess.list2cmdline(check) if os.name == "nt" else shlex.join(check),
                     "source": {"repo": str(repo), "commit": c["sha"], "parent": c["parent"],
                                "tests": c["tests"], "code": c["code"]}})
        say(f"{tid}: kept: fails on the parent, passes on the commit"
            + (f", {checks} times each" if checks > 1 else ""))
    path = None
    if kept:
        path = dest / "tasks.json"
        path.write_text(json.dumps({"tasks": kept}, indent=1) + "\n", encoding="utf-8")
    (dest / "tasks-report.json").write_text(json.dumps({"kept": [k["id"] for k in kept], "skipped": skipped},
                                                       indent=1) + "\n", encoding="utf-8")
    return {"tasks": path, "kept": kept, "skipped": skipped}
