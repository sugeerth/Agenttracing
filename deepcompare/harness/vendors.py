"""Run vendor coding agents — Codex CLI, Claude Code — on the same task, and trace them.

``agentdiff duel`` hands one task to each agent, each in its own copy of
the same workspace, launched side by side, and records everything its
command line prints. What the harness keeps for itself, and never takes
from the agent:

* **The grade.** The operator's check command (``pytest -q``,
  ``npm test``) runs in the agent's workspace after the agent stops. The
  agent saying it is done is not the agent being done.
* **What it made.** The workspace is snapshotted before and after; the
  difference is the run's diff, per file, with lines added and removed.
* **The clock and the budget.** Every line of the agent's stream is
  stamped as it arrives, so each step has a real start and duration. A
  token budget is enforced *while the agent works* where the stream
  reports usage as it goes (Claude Code, per message) and checked
  afterwards where it does not (Codex, per turn) — and the record says
  which, because an agent that could not be stopped at the budget did not
  run under the same rule.
* **The keys.** API keys are read from the environment the operator set
  — ``OPENAI_API_KEY`` or ``CODEX_API_KEY`` for Codex, ``ANTHROPIC_API_KEY``
  for Claude Code — and passed to the agent's process and nowhere else.
  Nothing writes them; the check command runs without them; any key value
  that turns up in a stream, an error or a check's output is replaced
  with ``[redacted]`` before it is written.

While a run is going, ``<out>/traces/<task>__<agent>__<run>.live.json`` is
rewritten about once a second, so ``agentdiff watch <out>/traces`` draws
both agents as they work.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from difflib import unified_diff
from pathlib import Path
from typing import Callable, Optional

from .. import vendors as _vendors

__all__ = ["VendorSpec", "parse_spec", "preflight", "run_vendor", "run_duel", "snapshot",
           "KEY_ENV", "SKIP_DIRS", "shutdown_all", "DuelInterrupted", "STREAM_CAP_BYTES",
           "vendor_env", "HOST_SESSION_PREFIXES", "CLAUDE_TOOLS"]

#: where each vendor's CLI looks for its key; values are never read here
#: except to redact them
KEY_ENV = {"codex": ("CODEX_API_KEY", "OPENAI_API_KEY"), "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
#: an endpoint the host configured for the CLI (a gateway, a proxy, a
#: managed provider): the CLI authenticates through it without a key here
ENDPOINT_ENV = {"codex": ("OPENAI_BASE_URL",), "claude": ("ANTHROPIC_BASE_URL",)}
#: an environment variable named like this holds something secret, whatever
#: tool set it — a child process inherits them all, so all are redacted
_SECRET_NAME = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

#: variables that bind a process to the *host's* agent session: its id, its
#: messaging socket and token, its remote worker. When the harness itself
#: runs inside an agent (Claude Code, say), a vendor CLI started with them
#: joins that session — it reports the host's session id and is offered the
#: host's tools. An agent under evaluation must not be part of the
#: evaluator, so these never reach a vendor process. Authentication does
#: not depend on them (a live run confirmed it with every one removed).
HOST_SESSION_PREFIXES = (
    "CLAUDECODE", "CLAUDE_CODE_SESSION", "CLAUDE_CODE_REMOTE", "CLAUDE_CODE_MESSAGING",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_SESSION_", "SESSION_INGRESS", "CLAUDE_CODE_CONTAINER_ID",
    "CLAUDE_CODE_WORKER", "CLAUDE_CODE_POST_FOR_SESSION", "CLAUDE_CODE_TEE_SDK", "CLAUDE_PID",
    "CLAUDE_CODE_ARTIFACT", "CLAUDE_CODE_USE_CCR", "CLAUDE_CODE_BG_TASKS", "CLAUDE_AUTO_BACKGROUND",
    "CLAUDE_CODE_HOLD", "CLAUDE_CODE_SYNC_SKILLS", "CLAUDE_ADDITIONAL_DIRECTORIES",
    "CLAUDE_CODE_ADDITIONAL_DIRECTORIES", "CLAUDE_AFTER_LAST_COMPACT", "CLAUDE_ENABLE_STREAM",
    "CLAUDE_CODE_DIAGNOSTICS", "DOCUMENTS_MCP",
)
#: what Claude Code is offered: its coding tools, and nothing a host
#: installed around it (connectors, notifications, other sessions)
CLAUDE_TOOLS = ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "TodoWrite", "Task")


def vendor_env() -> dict:
    """The environment a vendor CLI runs in: the operator's, less anything
    that would make the agent under test part of a host agent session."""
    return {k: v for k, v in os.environ.items() if not k.startswith(HOST_SESSION_PREFIXES)}
#: directories that are tooling, not work: copied, never diffed
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".cache"}
#: a file larger than this is compared by hash and not diffed line by line
DIFF_BYTES = 256_000
#: the patch kept per run; the rest is summarised
PATCH_CAP = 200_000
LIVE_EVERY_S = 1.0
#: a run's raw stream is cut at this many bytes: an agent that cats a
#: gigabyte log must not fill the disk or the harness's memory. The run is
#: stopped there and says why.
STREAM_CAP_BYTES = 256 * 1024 * 1024
#: one line of a stream longer than this is kept truncated, with its length
LINE_CAP_BYTES = 2 * 1024 * 1024
#: stderr kept per run (its tail): enough to read an error, not a log
STDERR_CAP = 64 * 1024
#: seconds a vendor process gets after SIGTERM before SIGKILL
GRACE_S = 5.0

# every vendor process and workspace copy alive right now, so an interrupt
# (Ctrl-C, SIGTERM from a scheduler) can stop and remove all of them rather
# than leaving agents running with API keys in their environment
_ACTIVE = {"procs": set(), "dirs": set()}
_ACTIVE_LOCK = threading.Lock()


def _terminate(proc) -> None:
    """SIGTERM the process group, then SIGKILL it if it has not gone."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            proc.wait(timeout=GRACE_S)
            return
        except subprocess.TimeoutExpired:
            continue


def shutdown_all() -> dict:
    """Stop every vendor process this harness started and remove every
    workspace copy it made. Returns what it stopped and removed."""
    with _ACTIVE_LOCK:
        procs, dirs = list(_ACTIVE["procs"]), list(_ACTIVE["dirs"])
        _ACTIVE["procs"].clear()
        _ACTIVE["dirs"].clear()
    for proc in procs:
        if proc.poll() is None:
            _terminate(proc)
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    return {"processes": len(procs), "workspaces": len(dirs)}


@dataclass
class VendorSpec:
    """``codex``, ``codex:MODEL``, ``claude``, ``claude:MODEL``, optionally
    ``NAME=`` in front to name the agent on the page."""

    kind: str
    model: str = ""
    name: str = ""
    binary: str = ""
    extra: list = field(default_factory=list)

    @property
    def agent(self) -> str:
        return self.name or ("claude-code" if self.kind == "claude" else self.kind)


def parse_spec(text: str) -> VendorSpec:
    name, _, rest = text.partition("=") if "=" in text.split(":", 1)[0] else ("", "", text)
    kind, _, model = rest.partition(":")
    kind = kind.strip().lower()
    if kind in ("claude-code", "claude_code"):
        kind = "claude"
    if kind not in ("codex", "claude"):
        raise ValueError(f"unknown vendor {kind!r}: use codex[:MODEL] or claude[:MODEL]")
    return VendorSpec(kind=kind, model=model.strip(), name=name.strip())


def _binary(spec: VendorSpec) -> Optional[str]:
    # absolute, because the agent runs with its workspace copy as the cwd
    want = spec.binary or os.environ.get("AGENTDIFF_CODEX_BIN" if spec.kind == "codex" else "AGENTDIFF_CLAUDE_BIN")
    if want:
        if os.path.isfile(want):
            return os.path.abspath(want)
        return shutil.which(want)
    return shutil.which("codex" if spec.kind == "codex" else "claude")


def _cli_version(binary: str) -> str:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
        return (out.stdout or out.stderr).strip().splitlines()[0][:80] if (out.stdout or out.stderr) else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


#: what to type when a vendor is missing: the fix, not only the fault
INSTALL = {"codex": "npm i -g @openai/codex", "claude": "npm i -g @anthropic-ai/claude-code"}
LOGIN = {"codex": "codex login", "claude": "claude (once, to log in)"}


def preflight(spec: VendorSpec) -> dict:
    """Whether this vendor can run here: the CLI, its version, and whether a
    credential is *present* — never what it is."""
    binary = _binary(spec)
    keys = [k for k in KEY_ENV[spec.kind] if os.environ.get(k)]
    if spec.kind == "codex":
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        login = (home / "auth.json").is_file()
    else:
        login = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) or \
            (Path.home() / ".claude" / ".credentials.json").is_file()
    endpoint = [k for k in ENDPOINT_ENV[spec.kind] if os.environ.get(k)]
    return {"vendor": spec.kind, "agent": spec.agent, "binary": binary,
            "version": _cli_version(binary) if binary else "",
            "key_env_present": keys, "login_present": login, "endpoint_env_present": endpoint,
            "ready": bool(binary) and bool(keys or login or endpoint),
            "why": (None if binary and (keys or login or endpoint) else
                    (f"not installed; install it with: {INSTALL[spec.kind]}" if not binary else
                     f"no credential; set {' or '.join(KEY_ENV[spec.kind])}, or run: {LOGIN[spec.kind]}"))}


# --------------------------------------------------------------- workspace

#: a file in every duel's output directory: that directory is never copied
#: into an agent's workspace, so a duel run inside the project it tests does
#: not hand the next duel's agents the last one's reports and traces
OUTPUT_MARKER = ".agentdiff-output"


def _is_output(path: Path) -> bool:
    return (path / OUTPUT_MARKER).is_file()


def mark_output(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    marker = out / OUTPUT_MARKER
    if not marker.exists():
        marker.write_text("written by agentdiff duel: never copied into an agent's workspace\n", encoding="utf-8")
    # run inside a project, the output stays out of its `git status`
    # without touching the project's own .gitignore
    ignore = out / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# written by agentdiff duel\n*\n", encoding="utf-8")


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.is_symlink() or not p.is_file():
                continue
            yield p.relative_to(root).as_posix(), p


def snapshot(root: Path) -> dict:
    """path -> (sha256, bytes or None) for every file that is work, not tooling."""
    out = {}
    for rel, p in _walk(root):
        data = p.read_bytes()
        out[rel] = (hashlib.sha256(data).hexdigest(), data if len(data) <= DIFF_BYTES else None)
    return out


def workspace_sha(snap: dict) -> str:
    return hashlib.sha256("\n".join(f"{k}\x1f{v[0]}" for k, v in sorted(snap.items())).encode()).hexdigest()[:16]


def _lines(data: Optional[bytes]) -> Optional[list]:
    if data is None:
        return None
    try:
        return data.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def diff_snapshots(before: dict, after: dict) -> dict:
    files, patch, added, removed = [], [], 0, 0
    for path in sorted(set(before) | set(after)):
        b, a = before.get(path), after.get(path)
        if b and a and b[0] == a[0]:
            continue
        status = "added" if not b else "deleted" if not a else "modified"
        bl = _lines(b[1]) if b else []
        al = _lines(a[1]) if a else []
        plus = minus = 0
        if bl is not None and al is not None:
            hunk = list(unified_diff(bl, al, f"a/{path}", f"b/{path}", n=3))
            for line in hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    plus += 1
                elif line.startswith("-") and not line.startswith("---"):
                    minus += 1
            patch.extend(hunk if hunk and hunk[-1].endswith("\n") else hunk + ["\n"])
        else:
            patch.append(f"Binary or large file {path} {status}\n")
        added += plus
        removed += minus
        files.append({"path": path, "status": status, "added": plus, "removed": minus,
                      "after_sha": a[0] if a else None})
    text = "".join(patch)
    return {"files": files, "added": added, "removed": removed, "patch": text[:PATCH_CAP],
            "patch_chars": len(text), "patch_truncated": len(text) > PATCH_CAP}


# ------------------------------------------------------------------ secrets

def _secret_names() -> list:
    names = {n for group in KEY_ENV.values() for n in group} | {"CLAUDE_CODE_OAUTH_TOKEN"}
    names |= {n for n in os.environ if any(part in n.upper() for part in _SECRET_NAME)}
    return sorted(names)


def _secrets() -> list:
    """Every secret-looking value in this environment, longest first, so a
    value that contains another is replaced whole."""
    vals = {os.environ[n] for n in _secret_names() if len(os.environ.get(n) or "") >= 8}
    return sorted(vals, key=len, reverse=True)


def redact(text: str, secrets: Optional[list] = None) -> str:
    for v in (secrets if secrets is not None else _secrets()):
        text = text.replace(v, "[redacted]")
    return text


def _check_env() -> dict:
    env = dict(os.environ)
    for n in _secret_names():
        env.pop(n, None)
    return env


# --------------------------------------------------------------------- run

def _argv(spec: VendorSpec, binary: str, workdir: Path, isolate: bool,
          sandbox: str, claude_mode: str, budget_usd: Optional[float]) -> tuple:
    """(argv, setup facts). The prompt goes on stdin, never on the command line."""
    if spec.kind == "codex":
        argv = [binary, "exec", "--json", "--skip-git-repo-check", "--ephemeral",
                "-s", sandbox, "-C", str(workdir)]
        if isolate:
            argv.append("--ignore-user-config")
        if spec.model:
            argv += ["-m", spec.model]
        argv += list(spec.extra) + ["-"]
        facts = {"sandbox": f"codex {sandbox} sandbox", "budget_enforced": "after the turn (usage is per turn)",
                 "user_config": "ignored" if isolate else "loaded",
                 "tools": "the CLI's own (shell, apply_patch, and what its config enables)"}
    else:
        argv = [binary, "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", claude_mode, "--no-session-persistence",
                "--tools", ",".join(CLAUDE_TOOLS), "--strict-mcp-config"]
        if claude_mode != "bypassPermissions":
            argv += ["--allowedTools", "Bash"]
        if isolate:
            argv += ["--setting-sources", "project,local"]
        if spec.model:
            argv += ["--model", spec.model]
        if budget_usd:
            argv += ["--max-budget-usd", str(budget_usd)]
        argv += list(spec.extra)
        facts = {"sandbox": f"claude {claude_mode} (Bash allowed), no OS sandbox",
                 "budget_enforced": "while running (usage is per message)",
                 "user_config": "ignored" if isolate else "loaded",
                 "tools": ", ".join(CLAUDE_TOOLS) + "; no MCP servers"}
    return argv, facts


def _running_tokens(kind: str, events: list, seen: dict) -> int:
    """Tokens processed so far, from what the stream has reported."""
    if kind == "claude":
        for item in events:
            ev = item.get("e") or {}
            if ev.get("type") == "assistant":
                msg = ev.get("message") or {}
                u = msg.get("usage")
                if isinstance(u, dict):
                    seen[str(msg.get("id"))] = sum(int(u.get(k) or 0) for k in (
                        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        return sum(seen.values())
    total = 0
    for item in events:
        ev = item.get("e") or {}
        if ev.get("type") == "turn.completed":
            u = ev.get("usage") or {}
            total += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
    return total


def run_vendor(spec: VendorSpec, task: dict, out: Path, *, run: str = "r1",
               budget_tokens: Optional[int] = None, budget_usd: Optional[float] = None,
               timeout_s: float = 1800.0, check_timeout_s: float = 600.0,
               isolate: bool = True, sandbox: str = "workspace-write",
               claude_mode: str = "acceptEdits", price: Optional[dict] = None,
               started_at: Optional[float] = None, keep_workspace: bool = False,
               live_every: float = LIVE_EVERY_S, stream_cap: int = STREAM_CAP_BYTES,
               on_event: Optional[Callable[[dict], None]] = None) -> dict:
    """One agent, one task, one run. Returns the run record."""
    out = Path(out)
    for sub in ("traces", "raw", "diffs", "records"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    binary = _binary(spec)
    if not binary:
        raise RuntimeError(f"{spec.agent}: the {spec.kind} CLI was not found on PATH")
    source = Path(task["workspace"]).resolve()
    tmp = Path(tempfile.mkdtemp(prefix=f"agentdiff-{spec.agent}-"))
    with _ACTIVE_LOCK:
        _ACTIVE["dirs"].add(str(tmp))
    workdir = tmp / "work"
    shutil.copytree(source, workdir, symlinks=True,
                    ignore=lambda d, names: [n for n in names if _is_output(Path(d) / n)])
    before = snapshot(workdir)
    stem = f"{task['id']}__{spec.agent}__{run}"
    raw_path = out / "raw" / f"{stem}.jsonl"
    # named as its final trace is, run and all, so a repeat streams too
    live_path = out / "traces" / f"{stem}.live.json"
    argv, facts = _argv(spec, binary, workdir, isolate, sandbox, claude_mode, budget_usd)
    version = _cli_version(binary)
    secrets = _secrets()
    prompt = str(task["prompt"])
    events: list = []
    stopped_by: Optional[str] = None
    seen_usage: dict = {}
    t0 = time.monotonic()
    wall_start = started_at or time.time()

    def convert(evs, **kw):
        fn = _vendors.codex_to_trajectory if spec.kind == "codex" else _vendors.claude_stream_to_trajectory
        return fn(evs, task=task["id"], prompt=prompt, agent=spec.agent, model=spec.model,
                  version=version, run_id=run, expected=task.get("expected"),
                  **({"price": price} if spec.kind == "codex" else {}), **kw)

    proc = subprocess.Popen(argv, cwd=str(workdir), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=vendor_env(), start_new_session=True,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    with _ACTIVE_LOCK:
        _ACTIVE["procs"].add(proc)
    stderr_tail_buf = [""]

    def drain_stderr():
        # read in chunks and keep the tail: a chatty CLI must not grow the
        # harness's memory without bound
        for chunk in iter(lambda: proc.stderr.read(8192), ""):
            stderr_tail_buf[0] = (stderr_tail_buf[0] + chunk)[-STDERR_CAP:]
    threading.Thread(target=drain_stderr, daemon=True).start()

    def kill(reason: str):
        nonlocal stopped_by
        if stopped_by is None:
            stopped_by = reason
        threading.Thread(target=_terminate, args=(proc,), daemon=True).start()

    timer = threading.Timer(timeout_s, lambda: kill("timeout"))
    timer.daemon = True
    timer.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    last_live = 0.0
    written = 0
    truncated_lines = 0
    with raw_path.open("w", encoding="utf-8") as raw:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if written >= stream_cap:
                if stopped_by is None:
                    kill("stream_cap")
                continue
            if len(line) > LINE_CAP_BYTES:
                truncated_lines += 1
                line = json.dumps({"type": "_truncated", "bytes": len(line),
                                   "head": line[:4000]})
            t = round(time.monotonic() - t0, 3)
            try:
                ev = json.loads(redact(line, secrets))
            except ValueError:
                ev = {"type": "_unparsed", "text": redact(line, secrets)[:2000]}
            item = {"t": t, "e": ev}
            events.append(item)
            text_line = json.dumps(item, ensure_ascii=False) + "\n"
            raw.write(text_line)
            written += len(text_line)
            if on_event:
                on_event({"agent": spec.agent, "task": task["id"], "run": run, "t": t, "event": ev})
            if budget_tokens and spec.kind == "claude" and \
                    _running_tokens("claude", [item], seen_usage) > budget_tokens:
                kill("budget")
            if time.monotonic() - last_live >= live_every:
                last_live = time.monotonic()
                try:
                    live = convert(list(events))
                    live["in_progress"] = True
                    live["run"] = run
                    live["elapsed_s"] = t
                    live["updated_at"] = time.time()
                    live_path.write_text(json.dumps(live), encoding="utf-8")
                except Exception:   # a live frame must never stop the run
                    pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if stopped_by is None:
            stopped_by = "timeout"
        _terminate(proc)
    timer.cancel()
    with _ACTIVE_LOCK:
        _ACTIVE["procs"].discard(proc)
    wall = round(time.monotonic() - t0, 3)
    exit_code = proc.returncode
    stderr_tail = redact(stderr_tail_buf[0][-4000:], secrets)

    over_budget = False
    if budget_tokens and spec.kind == "codex":
        over_budget = _running_tokens("codex", events, {}) > budget_tokens
    after = snapshot(workdir)
    diff = diff_snapshots(before, after)
    (out / "diffs" / f"{stem}.patch").write_text(diff["patch"], encoding="utf-8")

    check = {"command": task.get("check"), "exit_code": None, "passed": None, "seconds": None, "output_tail": ""}
    if task.get("check"):
        c0 = time.monotonic()
        try:
            done = subprocess.run(task["check"], shell=True, cwd=str(workdir), capture_output=True,
                                  text=True, timeout=check_timeout_s, env=_check_env())
            check.update({"exit_code": done.returncode, "passed": done.returncode == 0,
                          "output_tail": redact((done.stdout + done.stderr)[-3000:], secrets)})
        except subprocess.TimeoutExpired:
            check.update({"exit_code": None, "passed": False, "output_tail": "the check timed out"})
        check["seconds"] = round(time.monotonic() - c0, 3)

    termination = {"timeout": "timeout", "budget": "user_stop", "stream_cap": "user_stop"}.get(stopped_by)
    if termination is None and exit_code not in (0, None) and not events:
        termination = "infrastructure_error"
    note = None
    if stopped_by == "budget":
        note = f"stopped by the harness at the token budget ({budget_tokens:,})"
    elif stopped_by == "timeout":
        note = f"stopped by the harness at the time limit ({timeout_s:g}s)"
    elif stopped_by == "stream_cap":
        note = f"stopped by the harness: its output passed the stream cap ({stream_cap:,} bytes)"
    elif over_budget:
        note = f"went over the token budget ({budget_tokens:,}); the CLI reports usage per turn, so it could not be stopped at it"
    if check["passed"] is not None:
        note = "; ".join(x for x in (note, f"check `{task['check']}` exited {check['exit_code']}") if x)
    traj = convert(events, success=check["passed"],
                   score=(1.0 if check["passed"] else 0.0) if check["passed"] is not None else None,
                   note=note, termination=termination)
    if budget_tokens:
        traj["budget"] = {"max_tokens": int(budget_tokens)}
    traj["harness"] = {"adapter": f"vendor:{spec.kind}", "graded_by": "harness check" if task.get("check") else None,
                       "note": "run by `agentdiff duel`: the grade, the diff and the clock are the harness's"}
    trace_path = out / "traces" / f"{stem}.json"
    trace_path.write_text(json.dumps(traj, indent=1, ensure_ascii=False), encoding="utf-8")
    if live_path.exists():
        live_path.unlink()

    record = {
        "task": task["id"], "agent": spec.agent, "vendor": spec.kind, "model": spec.model, "run": run,
        "trace": str(trace_path.relative_to(out)), "raw": str(raw_path.relative_to(out)),
        "patch": f"diffs/{stem}.patch",
        "exit_code": exit_code, "stopped_by": stopped_by or ("over_budget" if over_budget else None),
        "wall_s": wall, "stderr_tail": stderr_tail,
        "stream": {"bytes": written, "cap": stream_cap, "truncated_lines": truncated_lines},
        "check": check,
        "diff": {k: v for k, v in diff.items() if k != "patch"},
        "setup": {"prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                  "workspace_sha": workspace_sha(before), "check": task.get("check"),
                  "budget_tokens": budget_tokens, "timeout_s": timeout_s,
                  "started_at": round(wall_start, 3), "cli": spec.kind, "cli_version": version,
                  "argv": [a if a != str(workdir) else "<workspace copy>" for a in argv],
                  **facts},
    }
    (out / "records" / f"{stem}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False),
                                                  encoding="utf-8")
    record["trajectory"] = traj
    if keep_workspace:
        record["workspace"] = str(workdir)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    with _ACTIVE_LOCK:
        _ACTIVE["dirs"].discard(str(tmp))
    return record


class DuelInterrupted(KeyboardInterrupt):
    """A duel stopped by the operator: carries the runs that finished and
    what was stopped, so the command can still report them."""

    def __init__(self, records: list, stopped: dict) -> None:
        super().__init__("duel interrupted")
        self.records = records
        self.stopped = stopped


def run_duel(tasks: list, specs: list, out: Path, *, runs: int = 1, parallel: bool = True,
             on_event: Optional[Callable[[dict], None]] = None,
             on_done: Optional[Callable[[dict], None]] = None, **kw) -> list:
    """Every task, every run, every agent. Agents on one (task, run) are
    launched together unless ``parallel`` is False."""
    mark_output(out)
    records: list = []
    for task in tasks:
        for n in range(1, runs + 1):
            run = f"r{n}"
            started = time.time()
            got: list = []
            errors: list = []

            def one(spec):
                try:
                    rec = run_vendor(spec, task, out, run=run, started_at=time.time(), on_event=on_event, **kw)
                    got.append(rec)
                    if on_done:
                        on_done(rec)
                except Exception as exc:  # one vendor failing must not lose the other's run
                    errors.append({"agent": spec.agent, "task": task["id"], "run": run, "error": str(exc)})
            try:
                if parallel:
                    threads = [threading.Thread(target=one, args=(s,), daemon=True) for s in specs]
                    for th in threads:
                        th.start()
                    # join in slices, so a Ctrl-C reaches this thread promptly
                    while any(th.is_alive() for th in threads):
                        for th in threads:
                            th.join(timeout=0.2)
                else:
                    for s in specs:
                        one(s)
            except KeyboardInterrupt:
                stopped = shutdown_all()
                raise DuelInterrupted(records + got, stopped)
            for e in errors:
                e["started_at"] = started
            records.extend(got)
            if errors:
                records.extend({"failed": True, **e} for e in errors)
    return records
