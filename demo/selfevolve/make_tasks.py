"""Writes tasks.json for `agentdiff self-evolve`, with the graders held out.

Each workspace under tasks/ holds only the code and its docstring spec:
the tests that grade a run live in hidden/, outside the workspace the
agent is given, the way a held-out test suite or a CI grader does. The
check names them by absolute path, so this file writes tasks.json for
the clone it is run in:

    python3 demo/selfevolve/make_tasks.py
    agentdiff self-evolve --task demo/selfevolve/tasks.json --agent haiku -o evo/

The harness never repeats a check that names a path outside the
workspace in an instruction: telling the agent where the grader is would
be teaching to the test.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = [
    ("pricing", "apply_discount in pricing.py gives the wrong total. Fix it."),
    ("slugify", "Implement slugify in text_utils.py."),
    ("duration", "Implement parse_duration in durations.py."),
    ("semver", "Implement compare in semver.py."),
    ("csvline", "Implement split_csv_line in csvline.py."),
    ("wrap", "Implement wrap in wrap.py."),
]


def main() -> None:
    tasks = [{"id": tid, "workspace": f"tasks/{tid}", "prompt": prompt,
              "check": f"python3 -m unittest discover -s {HERE / 'hidden' / tid} -t {HERE / 'hidden' / tid}"}
             for tid, prompt in TASKS]
    (HERE / "tasks.json").write_text(json.dumps({"tasks": tasks}, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {HERE / 'tasks.json'}: {len(tasks)} task(s), graders held out in {HERE / 'hidden'}")


if __name__ == "__main__":
    main()
