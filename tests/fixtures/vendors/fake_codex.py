#!/usr/bin/env python3
"""A stand-in for `codex exec --json`, for tests only.

It is not Codex and is never presented as Codex: it prints the event
shapes `codex exec --json` prints (thread/turn/item JSON lines, as read
from the 0.157 binary) and really edits the workspace, so the harness can
be tested end to end without a network or a key. FAKE_VENDOR_MODE picks
what it does: `fix` (default), `break`, `idle` (edits nothing).
"""
import json
import os
import subprocess
import sys
import time


def emit(obj):
    print(json.dumps(obj), flush=True)
    time.sleep(0.01)


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("codex-cli 0.0.0-test-double")
        return 0
    cwd = args[args.index("-C") + 1] if "-C" in args else os.getcwd()
    prompt = sys.stdin.read()
    mode = os.environ.get("FAKE_VENDOR_MODE", "fix")
    emit({"type": "thread.started", "thread_id": "thread-test"})
    emit({"type": "turn.started"})
    n = 0

    def command(cmd):
        nonlocal n
        n += 1
        iid = f"item_{n}"
        emit({"type": "item.started", "item": {"id": iid, "type": "command_execution", "command": cmd,
                                               "aggregated_output": "", "exit_code": None, "status": "in_progress"}})
        done = subprocess.run(["bash", "-lc", cmd], cwd=cwd, capture_output=True, text=True)
        emit({"type": "item.completed", "item": {"id": iid, "type": "command_execution", "command": f"bash -lc '{cmd}'",
                                                 "aggregated_output": done.stdout + done.stderr,
                                                 "exit_code": done.returncode,
                                                 "status": "completed" if done.returncode == 0 else "failed"}})

    n += 1
    emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "reasoning",
                                             "text": "Look at the failing tests before touching anything."}})
    command("ls")
    command("python3 -m unittest -q")
    command("cat pricing.py")
    if mode != "idle":
        path = os.path.join(cwd, "pricing.py")
        text = open(path).read()
        if mode == "fix":
            text = text.replace("return round(total - percent, 2)", "return round(total * (1 - percent / 100), 2)")
        else:
            text = text.replace("return round(unit_price * quantity, 2)", "return round(unit_price + quantity, 2)")
        n += 1
        emit({"type": "item.started", "item": {"id": f"item_{n}", "type": "file_change", "changes": [], "status": "in_progress"}})
        open(path, "w").write(text)
        emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "file_change",
                                                 "changes": [{"path": "pricing.py", "kind": "update"}], "status": "completed"}})
        command("python3 -m unittest -q")
    n += 1
    message = "Fixed apply_discount to take a percentage; the tests pass."
    if os.environ.get("FAKE_VENDOR_LEAK"):
        # a misbehaving agent that prints its key: the harness must redact it
        message += " key=" + os.environ.get("OPENAI_API_KEY", "")
        print("debug key " + os.environ.get("OPENAI_API_KEY", ""), file=sys.stderr, flush=True)
    emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "agent_message", "text": message}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 24000, "cached_input_tokens": 18000,
                                              "cache_write_input_tokens": 0, "output_tokens": 900,
                                              "reasoning_output_tokens": 400}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
