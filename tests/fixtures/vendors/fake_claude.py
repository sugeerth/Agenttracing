#!/usr/bin/env python3
"""A stand-in for `claude -p --output-format stream-json --verbose`, for tests only.

It is not Claude Code and is never presented as it: it prints the event
shapes Claude Code's headless stream prints (system/init, assistant
messages with tool_use blocks and usage, user tool_result messages, a
closing result) and really edits the workspace, so the harness can be
tested end to end without a network or a key. FAKE_VENDOR_MODE picks what
it does: `fix` (default), `break`, `idle`, `careless` (a first edit that is
wrong, then "done" without a check, unless the harness's appended system
prompt tells it to run the check: then it checks, sees the failure and
fixes it; for the self-evolving harness). FAKE_VENDOR_SLOW adds a delay
per message (for budget tests).
"""
import json
import os
import subprocess
import sys
import time


def emit(obj):
    print(json.dumps(obj), flush=True)
    time.sleep(float(os.environ.get("FAKE_VENDOR_SLOW", "0.01")))


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("0.0.0-test-double (Claude Code stand-in)")
        return 0
    cwd = os.getcwd()
    sys.stdin.read()
    mode = os.environ.get("FAKE_VENDOR_MODE", "fix")
    # what a real CLI would inherit from a host agent session, so a test can
    # see that the harness kept it out
    inherited = sorted(k for k in os.environ if k.startswith(("CLAUDECODE", "CLAUDE_CODE_SESSION", "SESSION_INGRESS")))
    emit({"type": "system", "subtype": "init", "cwd": cwd, "session_id": "sess-test", "model": "stand-in-model",
          "inherited_host_session": inherited, "argv": sys.argv[1:],
          "tools": ["Bash", "Read", "Edit", "Write", "Grep", "Glob"], "permissionMode": "acceptEdits"})
    n = 0
    context = 12000

    def call(name, inp, run=None, text=None):
        nonlocal n, context
        n += 1
        mid, tid = f"msg_{n}", f"toolu_{n}"
        usage = {"input_tokens": 50, "cache_creation_input_tokens": 200, "cache_read_input_tokens": context,
                 "output_tokens": 120}
        context += 1500
        if text:
            emit({"type": "assistant", "message": {"id": mid, "model": "stand-in-model", "role": "assistant",
                                                   "content": [{"type": "text", "text": text}], "usage": usage},
                  "parent_tool_use_id": None, "session_id": "sess-test"})
        emit({"type": "assistant", "message": {"id": mid, "model": "stand-in-model", "role": "assistant",
                                               "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                                               "usage": usage},
              "parent_tool_use_id": None, "session_id": "sess-test"})
        out, err = run() if run else ("ok", False)
        emit({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": out, "is_error": err}]},
              "parent_tool_use_id": None, "session_id": "sess-test"})

    def bash(cmd):
        def go():
            done = subprocess.run(["bash", "-lc", cmd], cwd=cwd, capture_output=True, text=True)
            return done.stdout + done.stderr, done.returncode != 0
        return go

    def read(path):
        return lambda: (open(os.path.join(cwd, path)).read(), False)

    if mode == "careless":
        told = ""
        if "--append-system-prompt" in args:
            told = args[args.index("--append-system-prompt") + 1]
        return careless(call, bash, read, cwd, emit, "check" in told.lower(), lambda: n)
    call("Bash", {"command": "python3 -m unittest -q"}, bash("python3 -m unittest -q"),
         text="Let me run the tests first.")
    call("Read", {"file_path": os.path.join(cwd, "pricing.py")}, read("pricing.py"))
    if mode != "idle":
        old, new = (("return round(total - percent, 2)", "return round(total * (1 - percent / 100), 2)")
                    if mode == "fix" else ("return round(unit_price * quantity, 2)", "return round(unit_price + quantity, 2)"))

        def edit():
            path = os.path.join(cwd, "pricing.py")
            text = open(path).read()
            open(path, "w").write(text.replace(old, new))
            return "The file has been updated.", False
        call("Edit", {"file_path": os.path.join(cwd, "pricing.py"), "old_string": old, "new_string": new}, edit)
        call("Bash", {"command": "python3 -m unittest -q"}, bash("python3 -m unittest -q"))
    n += 1
    final = "apply_discount subtracted the percent as a flat amount; it now takes a percentage. All four tests pass."
    emit({"type": "assistant", "message": {"id": f"msg_{n}", "model": "stand-in-model", "role": "assistant",
                                           "content": [{"type": "text", "text": final}],
                                           "usage": {"input_tokens": 40, "cache_creation_input_tokens": 0,
                                                     "cache_read_input_tokens": context, "output_tokens": 60}},
          "parent_tool_use_id": None, "session_id": "sess-test"})
    emit({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 4200, "duration_api_ms": 3900,
          "num_turns": n, "result": final, "session_id": "sess-test", "total_cost_usd": 0.0412,
          "usage": {"input_tokens": 290, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 70500,
                    "output_tokens": 540}, "permission_denials": []})
    return 0


def careless(call, bash, read, cwd, emit, checks, count):
    path = os.path.join(cwd, "pricing.py")

    def edit(old, new):
        def go():
            text = open(path).read()
            open(path, "w").write(text.replace(old, new))
            return "The file has been updated.", False
        return go
    call("Read", {"file_path": path}, read("pricing.py"), text="I'll look at the pricing code.")
    wrong = ("return round(total - percent, 2)", "return round(total - total * percent, 2)")
    call("Edit", {"file_path": path, "old_string": wrong[0], "new_string": wrong[1]}, edit(*wrong))
    if checks:
        call("Bash", {"command": "python3 -m unittest -q"}, bash("python3 -m unittest -q"))
        right = (wrong[1], "return round(total * (1 - percent / 100), 2)")
        call("Edit", {"file_path": path, "old_string": right[0], "new_string": right[1]}, edit(*right))
        call("Bash", {"command": "python3 -m unittest -q"}, bash("python3 -m unittest -q"))
    final = "Fixed apply_discount; the work is done."
    emit({"type": "assistant", "message": {"id": "msg_final", "model": "stand-in-model", "role": "assistant",
                                           "content": [{"type": "text", "text": final}],
                                           "usage": {"input_tokens": 40, "cache_creation_input_tokens": 0,
                                                     "cache_read_input_tokens": 1000, "output_tokens": 30}},
          "parent_tool_use_id": None, "session_id": "sess-test"})
    emit({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1200, "duration_api_ms": 1000,
          "num_turns": count() + 1, "result": final, "session_id": "sess-test", "total_cost_usd": 0.01,
          "usage": {"input_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000,
                    "output_tokens": 200}, "permission_denials": []})
    return 0


if __name__ == "__main__":
    sys.exit(main())
