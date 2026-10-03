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

**tests** (off unless asked). An edit to a test file is refused: fix the
code, not the test that catches it.

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

__all__ = ["Guard", "GUARDS", "EDIT_TOOLS", "failed_output", "is_check_command", "decide", "graded", "summary"]

GUARDS = ("repeat", "check", "tests")
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
    return bool(_FAILED.search(text)) and not re.search(r"\b0 failed\b", text)


def _sig(tool: str, tool_input: dict) -> str:
    if tool == "Bash":
        return "Bash:" + " ".join(str((tool_input or {}).get("command") or "").split())[:400]
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
                 max_stop_blocks: int = 2) -> None:
        self.dir = Path(root) / ".agentdiff" / "guard"
        self.session = re.sub(r"[^A-Za-z0-9_.-]", "_", session or "session")[:80]
        self.enabled = set(enabled)
        self.check = check
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
        kind = "edit" if tool in EDIT_TOOLS else "check" if (tool == "Bash" and is_check_command(cmd, self.check)) \
            else "run"
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
            try:
                rel = str(Path(path).resolve().relative_to(self.dir.parent.parent.resolve()))
            except (OSError, ValueError):
                pass
            if is_test_path(rel):
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
            if len(tail) >= REPEAT_AFTER and all(c["failed"] for c in tail):
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
            how = f"`{self.check}`" if self.check else "the project's tests (or a quick test of the cases the task states)"
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
    return {"success": not after[-1]["failed"], "by": after[-1]["sig"][5:] if after[-1]["sig"].startswith("Bash:")
            else after[-1]["sig"]}


def decide(payload: dict, root: Path, **opts) -> Optional[dict]:
    """One hook payload in, the hook's JSON answer out (None: say nothing, let it be)."""
    event = payload.get("hook_event_name")
    g = Guard(root, str(payload.get("session_id") or "session"), **opts)
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
