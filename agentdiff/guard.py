"""Live guards: the remedies, enforced while the agent works.

:mod:`agentdiff.selfevolve` finds a failure after the run and changes the
agent's instructions for the next one. A guard acts in the run itself,
through Claude Code's hooks. It sees each tool call before it runs and
each result after, and it can refuse a call, or refuse to let the turn
end, with a reason the agent reads. Three guards, each the live form of
a remedy:

**repeat** (``repeated_call``). The same command, which failed every
time it ran, is about to run again with nothing changed since: no file
edited, no other command run in between. The call is refused, with the
failure it keeps getting. Running a failing test again after editing
the code is the normal loop, and is never refused.

**check** (``claims_without_check``, ``no_check_after_last_edit``). The
agent is about to finish, and it changed files after the last time a
check ran (a test, lint or type check, or the command ``--check``
names). The stop is refused once or twice (``max_stop_blocks``), with
the command to run. It is never refused forever.

**tests** (off unless asked). An edit to one of the project's test files
is refused: fix the code, not the test that catches it. A test the agent
writes for itself outside the project is its own, and is let through.

**brief** (``SessionStart``). Each session starts knowing what the last
ones learned the hard way. The agent is told, in a few lines of context:
the command that checks this project (``--check``, or the one the
project's own files name: a ``package.json`` test script, ``Cargo.toml``,
``go.mod``, a Makefile ``test`` target, pytest's configuration or the
project's test files; :func:`detect_check`); what the guards refused in
earlier sessions here, the failing commands rerun unchanged named; and
which earlier sessions ended with their own last check failing. Every
line is a count or a command read from this project's files and the
guard's own log; nothing in it is judged by a model. The check guard
names the same command when it keeps the agent working.

**Replayed** (:func:`replay`, ``agentdiff guard --replay DIR``). Before the
guards are installed, they can be run over runs already recorded: each
trace's tool calls go through the same rules, in order, with the state
kept in memory, and the finish is put to the check guard. Each guard's
refusals are counted among the runs that failed and the runs that passed,
so its catch and its cost are read on runs whose outcome is known.

Each decision is a rule over what the session did in this turn (since
the person's last prompt), recorded in a small state file per session. Every refusal is logged
(``.agentdiff/guard/log.jsonl``) with the guard, the reason and the call,
so what the guards did can be counted and read on the hub. A guard that
cannot read its state, or meets anything unexpected, lets the call
through: a guard must never be why an agent breaks.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import List, Optional

__all__ = ["Guard", "GUARDS", "EDIT_TOOLS", "failed_output", "is_check_command", "decide", "graded", "summary",
           "detect_check", "brief", "replay", "replay_says", "replay_variants", "runner_verdict", "workspace_of", "is_ungraded"]

GUARDS = ("repeat", "check", "tests", "brief")
#: the longest session brief, in characters: a few lines of context, not a report
BRIEF_MAX = 1200
#: test files read for a pytest import before a Python project is called a unittest one
PYTEST_SCAN = 200
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
#: times the same failing command may run unchanged before the next is refused
REPEAT_AFTER = 2
_CHECK = re.compile(r"(^|[\s/;&|])(pytest|py\.test|unittest|tox|nox|jest|vitest|mocha|go\s+test|cargo\s+(test|check|clippy)"
                    r"|npm\s+(run\s+)?test|yarn\s+test|pnpm\s+test|make\s+(test|check)|ruff|flake8|mypy|pyright|tsc|eslint"
                    r"|gradle\w*\s+test|mvn\s+test|rspec|phpunit|ctest)(?=$|[\s;&|)])", re.I)
_FAILED = re.compile(r"\b(\d+\s+failed|FAILED|failures?=|errors?=|Traceback|AssertionError|Error:|error\[|FAIL\b"
                     r"|exit (code|status) [1-9])", re.I)


def is_check_command(cmd: str, check: str = "") -> bool:
    cmd = " ".join(str(cmd or "").split())
    if check and " ".join(check.split()) in cmd:
        return True
    return bool(_CHECK.search(cmd))


def failed_output(response) -> Optional[bool]:
    """Whether a Bash result failed: its exit code when the hook reports one,
    else what it printed. None when there is nothing to read."""
    if isinstance(response, dict):
        if response.get("is_error") is True or response.get("interrupted") is True:
            return True
        for k in ("exit_code", "exitCode", "returnCode", "return_code", "code"):
            if isinstance(response.get(k), int):
                return response[k] != 0
        text = " ".join(str(response.get(k) or "") for k in ("stdout", "stderr", "output", "content"))
    else:
        text = str(response or "")
    if not text.strip():
        return None
    verdict = runner_verdict(text)
    if verdict is not None:
        return verdict
    return bool(_FAILED.search(text)) and not re.search(r"\b0 failed\b", text)


#: a test runner's own last word: unittest's "OK" / "FAILED (...)" after its "Ran N tests" line, or pytest's tally
_UNITTEST_END = re.compile(r"^Ran \d+ tests? in [\d.]+s\s*\n(?:\s*\n)*(OK\b|FAILED\b)", re.M)
_PYTEST_END = re.compile(r"^[=\s]*((?:\d+ (?:failed|passed|errors?|skipped|xfailed|xpassed|warnings?|deselected)"
                         r"(?:, )?)+) in [\d.]+s", re.M)


def runner_verdict(text: str) -> Optional[bool]:
    """Whether a test run failed, by the verdict its runner printed last; None when it printed none. A run
    that prints a traceback or the word "Error" (a test of an error path, a warning) and then reports OK
    passed: the runner's verdict is read before any stray word."""
    found = [(m.start(), m.group(1) == "FAILED") for m in _UNITTEST_END.finditer(text)]
    found += [(m.start(), bool(re.search(r"\d+ (failed|errors?)\b", m.group(1)))) for m in _PYTEST_END.finditer(text)]
    return max(found)[1] if found else None


def _sig(tool: str, tool_input: dict) -> str:
    if tool == "Bash":
        cmd = " ".join(str((tool_input or {}).get("command") or "").split())
        if len(cmd) <= 400:
            return "Bash:" + cmd
        # a long command is told apart by all of it: two scripts that share their first lines are not the same run
        import hashlib
        return "Bash:" + cmd[:400] + " #" + hashlib.sha1(cmd.encode("utf-8")).hexdigest()[:12]
    return f"{tool}:{(tool_input or {}).get('file_path') or (tool_input or {}).get('notebook_path') or ''}"


def _first_line(text: str, n: int = 160) -> str:
    """The line that says how it failed: a test run's tally, else the first case
    it names, else the last line that reads as a failure."""
    text = str(text or "")
    tally = re.findall(r"^[=\s]*(\d+ failed.*?)[=\s]*$|^(FAILED \(.*\))\s*$", text, re.M)
    if tally:
        return "".join(tally[-1]).strip()[:n]
    from .insight import check_failures
    cases = check_failures(text, most=1)
    if cases:
        return cases[0][:n]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not re.match(r"Exit code \d+$", ln.strip())]
    hits = [ln for ln in lines if _FAILED.search(ln)]
    return (hits or lines or [""])[-1][:n]


class Guard:
    """One session's guard: its state on disk, and the decisions it makes."""

    def __init__(self, root: Path, session: str, *, enabled=("repeat", "check"), check: str = "",
                 max_stop_blocks: int = 2, repeat_after: int = REPEAT_AFTER) -> None:
        self.repeat_after = max(1, int(repeat_after))
        self.dir = Path(root) / ".agentdiff" / "guard"
        self.session = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "session")[:80]
        self.enabled = set(enabled)
        self.check = check
        self.found: Optional[dict] = None
        self.max_stop_blocks = max_stop_blocks
        self.path = self.dir / f"{self.session}.json"
        self.state = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("calls"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"calls": [], "stop_blocks": 0, "fired": []}

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        tmp.replace(self.path)

    def log(self, guard: str, reason: str, call: str) -> None:
        self.state["fired"].append({"guard": guard, "t": round(time.time(), 3), "call": call[:200]})
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "session": self.session, "guard": guard,
                                "reason": reason, "call": call[:200]}) + "\n")

    # ------------------------------------------------------------- events
    def after(self, tool: str, tool_input: dict, response, failed: Optional[bool] = None) -> None:
        """PostToolUse (or PostToolUseFailure, ``failed=True``): what the call was and how it ended."""
        cmd = str((tool_input or {}).get("command") or "") if tool == "Bash" else ""
        printed = " ".join(str(response.get(k) or "") for k in ("stdout", "stderr", "output", "content")) \
            if isinstance(response, dict) else str(response or "")
        # a check by its command, or by what it printed: a test runner's verdict, however the run was started
        checked = tool == "Bash" and (is_check_command(cmd, self.check) or runner_verdict(printed) is not None)
        kind = "edit" if tool in EDIT_TOOLS else "check" if checked else "run"
        if failed is None:
            failed = failed_output(response) if tool == "Bash" else None
        if failed and kind == "edit":
            kind = "run"  # an edit that did not land changed nothing
        text = response if isinstance(response, str) else json.dumps(response)[:4000] if response is not None else ""
        if isinstance(response, dict):
            text = " ".join(str(response.get(k) or "") for k in ("stdout", "stderr", "output"))
        self.state["calls"].append({"sig": _sig(tool, tool_input), "kind": kind, "failed": failed,
                                    "said": _first_line(text) if failed else ""})
        self.state["calls"] = self.state["calls"][-400:]

    def before(self, tool: str, tool_input: dict) -> Optional[str]:
        """PreToolUse: a reason to refuse the call, or None to let it run."""
        if "tests" in self.enabled and tool in EDIT_TOOLS:
            from .insight import is_test_path
            path = str((tool_input or {}).get("file_path") or (tool_input or {}).get("notebook_path") or "")
            rel = path
            if Path(path).is_absolute():
                try:
                    rel = str(Path(path).resolve().relative_to(self.dir.parent.parent.resolve()))
                except (OSError, ValueError):
                    rel = ""  # outside the project: the agent's own scratch test, not one of the project's
            if rel and is_test_path(rel):
                reason = (f"agentdiff guard: {path} is a test. Leave the tests as they are and fix the code they test; "
                          f"a test changed to pass proves nothing.")
                self.log("tests", reason, _sig(tool, tool_input))
                return reason
        if "repeat" in self.enabled and tool == "Bash":
            sig = _sig(tool, tool_input)
            calls = self.state["calls"]
            # the same command, failed every time, with nothing else done since its last run
            tail = []
            for c in reversed(calls):
                if c["sig"] != sig:
                    break
                tail.append(c)
            if len(tail) >= self.repeat_after and all(c["failed"] for c in tail):
                said = tail[0].get("said") or "the same failure"
                reason = (f"agentdiff guard: you have run `{sig[5:][:120]}` {len(tail)} times in a row and it failed "
                          f"each time ({said}), with nothing changed in between. Running it again unchanged will fail "
                          f"the same way: re-read the failing output and the code, change something, then run it.")
                self.log("repeat", reason, sig)
                return reason
        return None

    def stop(self, stop_hook_active: bool = False) -> Optional[str]:
        """Stop: a reason to keep the agent working, or None to let it finish."""
        if "check" not in self.enabled or self.state.get("stop_blocks", 0) >= self.max_stop_blocks:
            return None
        calls = self.state["calls"]
        last_edit = max((i for i, c in enumerate(calls) if c["kind"] == "edit"), default=None)
        if last_edit is None:
            return None
        last_check = max((i for i, c in enumerate(calls) if c["kind"] == "check"), default=None)
        if last_check is not None and last_check > last_edit:
            last = calls[last_check]
            if last["failed"]:
                reason = (f"agentdiff guard: the last check failed ({last.get('said') or 'it failed'}) and nothing has "
                          f"been changed and checked since. Fix it and run the check again before you finish.")
            else:
                return None
        else:
            how = (f"`{self.check}`" + (f" ({self.found['from']} names it)" if self.found else "") if self.check
                   else "the project's tests (or a quick test of the cases the task states)")
            reason = (f"agentdiff guard: you changed files after the last check ran. Run {how} and read the result "
                      f"before you finish.")
        self.state["stop_blocks"] = self.state.get("stop_blocks", 0) + 1
        self.log("check", reason, "Stop")
        return reason


def graded(state: dict) -> Optional[dict]:
    """The session's outcome by its own last check, when one ran after the last
    edit: ``{"success", "by"}``. None when no check ran after it (ungraded)."""
    calls = state.get("calls") or []
    last_edit = max((i for i, c in enumerate(calls) if c["kind"] == "edit"), default=-1)
    after = [c for c in calls[last_edit + 1:] if c["kind"] == "check" and c["failed"] is not None]
    if not after:
        return None
    sig = after[-1]["sig"]
    by = sig
    if sig.startswith("Bash:"):
        parts = [p.strip() for p in re.split(r"&&|\|\||;|\n", sig[5:]) if p.strip()]
        by = next((p for p in parts if _CHECK.search(p)), sig[5:])
        by = re.split(r"\s+\|\s*|\s+2>&1", by, maxsplit=1)[0]
    return {"success": not after[-1]["failed"], "by": by}


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _python_tests(root: Path) -> list:
    """The project's Python test files: ``test_*.py`` or ``*_test.py`` at its root or under ``tests/`` or
    ``test/``, at most :data:`PYTEST_SCAN` of them."""
    found: list = []
    for base, deep in ((root, False), (root / "tests", True), (root / "test", True)):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py") if deep else base.glob("*.py")):
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                found.append(path)
                if len(found) >= PYTEST_SCAN:
                    return found
    return found


def detect_check(root: Path) -> Optional[dict]:
    """The command that checks this project, as its own files name it: ``{"command", "from"}``, or None.

    Read in this order, the first that answers wins: a ``package.json`` ``test`` script (not npm's
    placeholder), ``Cargo.toml``, ``go.mod``, a Makefile with a ``test`` target, then Python: pytest when its
    configuration is there (``pytest.ini``, ``conftest.py``, ``[tool.pytest...]`` in ``pyproject.toml``,
    ``[tool:pytest]`` in ``setup.cfg``, ``[pytest]`` in ``tox.ini``) or a test file imports it, else unittest
    over the test files found (``discover -s tests`` when they sit in ``tests/``)."""
    root = Path(root)
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            test = (json.loads(_read(pkg)).get("scripts") or {}).get("test")
        except (ValueError, AttributeError):
            test = None
        if isinstance(test, str) and test.strip() and "no test specified" not in test:
            return {"command": "npm test", "from": "package.json's test script"}
    if (root / "Cargo.toml").is_file():
        return {"command": "cargo test", "from": "Cargo.toml"}
    if (root / "go.mod").is_file():
        return {"command": "go test ./...", "from": "go.mod"}
    for name in ("Makefile", "makefile", "GNUmakefile"):
        if re.search(r"^test\s*:", _read(root / name), re.M):
            return {"command": "make test", "from": f"the {name}'s test target"}
    config = None
    if (root / "pytest.ini").is_file():
        config = "pytest.ini"
    elif (root / "conftest.py").is_file():
        config = "conftest.py"
    elif re.search(r"^\[tool\.pytest", _read(root / "pyproject.toml"), re.M):
        config = "pyproject.toml"
    elif re.search(r"^\[tool:pytest\]", _read(root / "setup.cfg"), re.M):
        config = "setup.cfg"
    elif re.search(r"^\[pytest\]", _read(root / "tox.ini"), re.M):
        config = "tox.ini"
    tests = _python_tests(root)
    if config is None:
        imports = next((t for t in tests if re.search(r"^\s*(import|from)\s+pytest\b", _read(t, 20_000), re.M)), None)
        if imports is not None:
            config = f"{imports.relative_to(root)}, which imports pytest"
    if config is not None:
        return {"command": "python3 -m pytest -q", "from": config}
    if not tests:
        return None
    if all(t.parent == root for t in tests):
        return {"command": "python3 -m unittest -q", "from": "the test files at the project's root"}
    top = "tests" if (root / "tests").is_dir() else "test"
    return {"command": f"python3 -m unittest discover -s {top} -q", "from": f"the test files under {top}/"}


def _earlier_sessions(root: Path, current: str) -> list:
    """Earlier sessions' last turns here, from their state files, newest first."""
    d = Path(root) / ".agentdiff" / "guard"
    rows = []
    for path in d.glob("*.json") if d.is_dir() else []:
        if path.stem == current:
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            rows.append((path.stat().st_mtime, path.stem, state))
        except (OSError, ValueError):
            continue
    return [(stem, state) for _, stem, state in sorted(rows, key=lambda r: r[0], reverse=True)]


def brief(root: Path, check: str = "", found: Optional[dict] = None, session: str = "") -> Optional[str]:
    """What a new session should know before it starts, as a few lines of context, or None when there is
    nothing to say. Each line is read from the project's files or the guard's own records, by rule."""
    lines = []
    cmd = check or (found or {}).get("command") or ""
    if cmd:
        named = "" if check or not found else f" ({found['from']} names it)"
        lines.append(f"Check your work with `{cmd}`{named}: run it after your last edit and read the result "
                     f"before you say the work is done.")
    rows = []
    for ln in _read(Path(root) / ".agentdiff" / "guard" / "log.jsonl", 2_000_000).splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("session") != session:
            rows.append(r)
    if rows:
        by: dict = {}
        for r in rows:
            by[r.get("guard")] = by.get(r.get("guard"), 0) + 1
        said = []
        if by.get("repeat"):
            counts: dict = {}
            for r in rows:
                if r.get("guard") == "repeat":
                    c = str(r.get("call") or "")[5:].strip()[:80]
                    counts[c] = counts.get(c, 0) + 1
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            said.append(f"a failing command rerun unchanged {by['repeat']} time(s) ("
                        + ", ".join(f"`{c}`" for c, _ in top) + "): when a command fails, change something first")
        if by.get("check"):
            said.append(f"a finish with unchecked edits or a failing check {by['check']} time(s)")
        if by.get("tests"):
            said.append(f"an edit to a test file {by['tests']} time(s)")
        sessions = len({r.get("session") for r in rows})
        if said:
            lines.append(f"In {sessions} earlier session(s) here the guard refused " + "; ".join(said) + ".")
    failing = []
    for stem, state in _earlier_sessions(root, session)[:10]:
        g = graded(state) if isinstance(state, dict) else None
        if g and not g["success"]:
            failing.append(g["by"][:80])
    if failing:
        shown = sorted(set(failing))[:2]
        lines.append(f"{len(failing)} of the last earlier sessions here ended with their own last check failing ("
                     + ", ".join(f"`{c}`" for c in shown) + "): start from what that check says.")
    if not lines:
        return None
    text = "agentdiff guard, for this project:\n" + "\n".join(f"- {ln}" for ln in lines)
    return text if len(text) <= BRIEF_MAX else text[:BRIEF_MAX - 1] + "…"


def decide(payload: dict, root: Path, **opts) -> Optional[dict]:
    """One hook payload in, the hook's JSON answer out (None: say nothing, let it be)."""
    event = payload.get("hook_event_name")
    g = Guard(root, str(payload.get("session_id") or "session"), **opts)
    if not g.check and ("check" in g.enabled or "brief" in g.enabled):
        # the project's own check, found once a session and kept with its state
        if "detected" not in g.state:
            g.state["detected"] = detect_check(root) or {}
        g.check = (g.state.get("detected") or {}).get("command") or ""
        g.found = g.state.get("detected") or None
    turn = payload.get("prompt_id")
    if turn and g.state.get("turn") != turn:
        # a new prompt: what the agent did before it, and what the person may have changed since, is not this turn's
        g.state.update(calls=[], stop_blocks=0, turn=turn)
    out = None
    tool, tin = str(payload.get("tool_name") or ""), payload.get("tool_input") or {}
    if event == "PreToolUse":
        reason = g.before(tool, tin)
        if reason:
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}
    elif event == "PostToolUse":
        response = payload.get("tool_response", payload.get("tool_output"))
        g.after(tool, tin, response)
    elif event == "PostToolUseFailure":
        # Claude Code reports a command's non-zero exit here, with its output in ``error``
        g.after(tool, tin, str(payload.get("error") or ""), failed=not payload.get("is_interrupt"))
    elif event == "Stop":
        reason = g.stop(bool(payload.get("stop_hook_active")))
        if reason:
            out = {"decision": "block", "reason": reason}
    elif event == "SessionStart" and "brief" in g.enabled:
        given = opts.get("check") or ""
        text = brief(root, check=given, found=None if given else g.found, session=g.session)
        if text:
            out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    g.save()
    return out


def summary(root: Path) -> Optional[dict]:
    """What the guards did under ``root``: refusals by guard, and the most recent."""
    p = Path(root) / ".agentdiff" / "guard" / "log.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    rows: List[dict] = []
    for ln in lines:
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    if not rows:
        return None
    by: dict = {}
    for r in rows:
        by[r.get("guard")] = by.get(r.get("guard"), 0) + 1
    return {"fired": len(rows), "by_guard": by, "sessions": len({r.get("session") for r in rows}), "recent": rows[-8:]}


class _Dry(Guard):
    """A guard over a recorded run: the same rules, its state in memory, nothing written."""

    def _load(self) -> dict:
        return {"calls": [], "stop_blocks": 0, "fired": []}

    def save(self) -> None:
        pass

    def log(self, guard: str, reason: str, call: str) -> None:
        self.state["fired"].append({"guard": guard, "call": call[:200], "reason": reason})


def is_ungraded(data: dict) -> bool:
    """Whether a trace, as stored, was never graded: its success was recorded as false for want of an expected
    answer or a grader, and its note or harness says so. Such a run is neither a failure nor a pass."""
    out, harness = data.get("outcome") or {}, data.get("harness") or {}
    return str(out.get("note") or "").startswith("ungraded") or \
        (isinstance(harness, dict) and harness.get("graded_by") == "ungraded")


def _replay_one(traj, enabled, check: str, ungraded: bool = False, repeat_after: int = REPEAT_AFTER) -> dict:
    """Where each guard would have stepped into one recorded run: the first call each would refuse (its step
    index) and whether the finish would be refused, and why."""
    from .gittasks import _tool_input
    root = workspace_of(traj)
    if root is None:
        enabled = tuple(e for e in enabled if e != "tests")  # which test files were the project's is not known
    g = _Dry(Path(root or "."), "replay", enabled=enabled, check=check, max_stop_blocks=1, repeat_after=repeat_after)
    first: dict = {}
    for s in traj.steps:
        if s.type != "tool_call" or not s.name:
            continue
        tin = _tool_input(s.input)
        before = len(g.state["fired"])
        g.before(s.name, tin)
        for f in g.state["fired"][before:]:
            first.setdefault(f["guard"], {"step": s.index, "call": f["call"], "at": len(g.state["calls"])})
        failed = s.error if isinstance(s.error, bool) else None
        g.after(s.name, tin, str(s.output or ""), failed=failed)
    before = len(g.state["fired"])
    if g.stop():
        reason = g.state["fired"][before]["reason"]
        first.setdefault("check", {"step": "finish", "call": "Stop", "at": len(g.state["calls"]),
                                   "why": "failing" if "last check failed" in reason else "unchecked"})
    edited = any(c["kind"] == "edit" for c in g.state["calls"])
    # each tool call, in order, as the guard read it: an edit, a check that passed or failed, anything else
    path = "".join("e" if c["kind"] == "edit" else ("x" if c["failed"] else "c") if c["kind"] == "check" else "r"
                   for c in g.state["calls"])
    return {"trace": traj.trace_id, "task": traj.task.id, "success": None if ungraded else traj.outcome.success,
            "edited": edited,
            "refused": first, "workspace": root is not None, "path": path}


_CWD_RESET = re.compile(r"Shell cwd was reset to (/\S+)")


def workspace_of(traj) -> Optional[str]:
    """The directory a recorded run worked in, where its trace says: Claude Code prints it when it puts a
    command's shell back there. None when the trace does not say."""
    for s in traj.steps:
        m = _CWD_RESET.search(str(s.output or ""))
        if m:
            return m.group(1)
    return None


def replay(trajectories, *, enabled=("repeat", "check"), check: str = "", ungraded=(),
           repeat_after: int = REPEAT_AFTER) -> dict:
    """The guards run over recorded runs: for each guard, how many runs it would have stepped into, among the
    runs that failed (its catch) and the runs that passed (its cost), with every count's denominator.

    It reads where a guard would have stepped in, not what the agent would have done next: a run the guard
    sends back might have gone on to pass, or to fail the same way. Ungraded runs (``ungraded``: their places in
    ``trajectories``, since one trace id can name runs in several folders; :func:`is_ungraded`) are counted
    apart."""
    ungraded = set(ungraded)
    runs = [_replay_one(t, tuple(enabled), check, i in ungraded, repeat_after) for i, t in enumerate(trajectories)]
    out = {"runs": len(runs), "failed": sum(1 for r in runs if r["success"] is False),
           "passed": sum(1 for r in runs if r["success"] is True),
           "ungraded": sum(1 for r in runs if r["success"] is None), "guards": {}, "per_run": runs}
    for guard in (g for g in GUARDS if g in enabled and g != "brief"):
        hit = [r for r in runs if guard in r["refused"]]
        scored = [r for r in runs if guard != "tests" or r["workspace"]]
        row = {"refused": len(hit), "runs": len(scored),
               "failed": sum(1 for r in scored if r["success"] is False),
               "passed": sum(1 for r in scored if r["success"] is True),
               "in_failed": sum(1 for r in hit if r["success"] is False),
               "in_passed": sum(1 for r in hit if r["success"] is True),
               "in_ungraded": sum(1 for r in hit if r["success"] is None),
               "examples": [{"trace": r["trace"], **r["refused"][guard]} for r in hit[:5]]}
        if guard == "check":
            row["why"] = {k: sum(1 for r in hit if r["refused"]["check"].get("why") == k)
                          for k in ("unchecked", "failing")}
        out["guards"][guard] = row
    out["says"] = replay_says(out)
    return out


def replay_says(result: dict) -> List[str]:
    """One line per guard: what it would have stepped into, out of what."""
    lines = []
    for guard, row in result["guards"].items():
        line = (f"{guard}: would have stepped into {row['in_failed']} of {row['failed']} failing run(s) and "
                f"{row['in_passed']} of {row['passed']} passing run(s)")
        if row["runs"] < result["runs"]:
            line += f" (the {row['runs']} of {result['runs']} whose trace says where the project was)"
        if row["in_ungraded"]:
            line += f", and {row['in_ungraded']} of {result['ungraded']} ungraded"
        if guard == "check" and row["refused"]:
            line += (f" ({row['why']['unchecked']} finished with edits no check followed, "
                     f"{row['why']['failing']} with their own last check failing)")
        lines.append(line)
    return lines


def replay_variants(trajectories, *, check: str = "", ungraded=()) -> List[dict]:
    """Each guard's rule as installed beside the variants an operator could choose, replayed over the same
    runs: what each would catch among the failing runs and cost among the passing, with exact 95% intervals.

    ``check``: send a finish back for edits no check followed, or for a last check that failed, or for both
    (as installed). ``repeat``: refuse a failing command rerun unchanged after 1, 2 (as installed) or 3 failures.
    Every count comes from the same rules the live guard runs; nothing is fitted to the runs."""
    from .statistics import exact_interval
    trajectories = list(trajectories)
    base = replay(trajectories, enabled=("repeat", "check"), check=check, ungraded=ungraded)
    graded = [r for r in base["per_run"] if r["success"] is not None]
    failed = [r for r in graded if r["success"] is False]
    passed = [r for r in graded if r["success"] is True]

    def row(guard, rule, installed, hits):
        k_f = sum(1 for r in failed if hits(r))
        k_p = sum(1 for r in passed if hits(r))
        return {"guard": guard, "rule": rule, "installed": installed, "in_failed": k_f, "failed": len(failed),
                "in_passed": k_p, "passed": len(passed), "catch_ci": exact_interval(k_f, len(failed)),
                "cost_ci": exact_interval(k_p, len(passed))}

    def why(r):
        return (r["refused"].get("check") or {}).get("why")

    rows = [row("check", "edits no check followed, or a last check that failed", True,
                lambda r: "check" in r["refused"]),
            row("check", "edits no check followed, only", False, lambda r: why(r) == "unchecked"),
            row("check", "a last check that failed, only", False, lambda r: why(r) == "failing")]
    for r, i in zip(base["per_run"], range(len(base["per_run"]))):
        r["_i"] = i  # runs are told apart by place: one trace id can name runs in several folders
    for n in (1, 2, 3):
        hit = {i for i, r in enumerate(replay(trajectories, enabled=("repeat",), ungraded=ungraded,
                                              repeat_after=n)["per_run"]) if "repeat" in r["refused"]}
        rows.append(row("repeat", f"a failing command rerun unchanged after {n} failure{'s' if n > 1 else ''}",
                        n == REPEAT_AFTER, lambda r, hit=hit: r["_i"] in hit))
    for r in base["per_run"]:
        r.pop("_i", None)
    return rows
