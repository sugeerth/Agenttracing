"""Generate the RL demos (SYNTHETIC): two policies over a set of tasks.

Two sets are written. ``demo/rl/traces`` is the small one — two tasks,
three runs each — kept small so the shipped tests over it stay fast.
``demo/rl/train`` is the training ground proper: six tasks, eight runs
each, 96 episodes, which is the point at which the engine's own runs
advisory stops calling the sample insufficient and a bootstrap interval
over the tasks starts meaning something.

Every step carries a ``reward`` paid by a scripted environment — 0 for a thinking step, −0.1 per
tool call, −1 on an error, +1 when a piece of evidence the task needs
appears, +5 / −5 at the answer — and every thinking step a ``value``
estimate, so the RL layer reads these traces as *recorded* rather than
shaping a reward from the labels. ``policy-v1`` is the weaker policy: it
needs more attempts per item, errs more often in the verifier and misses
evidence more; ``policy-v2`` is stronger. A ``verifier`` sub-agent gives
every run a second lane. Each policy records the instructions it was
given (``agent.system_prompt``, :data:`PROMPTS`) and each model step its
model telemetry (``demo/_env.py``). 30–60 steps per run; every number
is invented and labelled so.

    python demo/rl/generate_rl.py            # both sets, in place
    python demo/rl/generate_rl.py --set demo|train [out_dir]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deepcompare.record import Recorder  # noqa: E402
from demo._env import (  # noqa: E402,F401 — the environment; the reward constants are re-exported for readers
    ANSWER_REWARD, ERROR_REWARD, EVIDENCE_REWARD, TASKS, TOOL_COST, label_synthetic, run_episode, tools_for,
)

NOTE = "SYNTHETIC: a generated RL episode whose rewards were paid by a scripted environment; every value invented"
RUNS = ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")
POLICIES = {"policy-v1": {"strong": False, "hit": 0.72, "error": 0.35, "checks": 4},
            "policy-v2": {"strong": True, "hit": 0.985, "error": 0.1, "checks": 3}}
#: the instructions each policy was given, written onto its traces
#: (SYNTHETIC, like everything here); they differ where the policies do
PROMPTS = {
    "policy-v1": ("You are an analyst with grep, read_file, search and run_check.\n"
                  "Locate every fact the task needs, searching again when a search finds nothing.\n"
                  "Run four consistency checks before answering.\n"
                  "Answer with the corrected figure on one line."),
    "policy-v2": ("You are an analyst with grep, read_file, search and run_check.\n"
                  "Locate every fact the task needs; a search that finds nothing is retried once, with the id alone.\n"
                  "Run three consistency checks before answering, validating the arguments first.\n"
                  "Answer with the corrected figure on one line."),
}
#: the small set: what the shipped tests pin, unchanged
DEMO_TASKS, DEMO_RUNS = 2, 3


def make(task: dict, agent: str, run: str, out: Path) -> Path:
    """One episode of ``agent`` on ``task``: the policy's behaviour, adjusted
    for the task, played by the environment and labelled SYNTHETIC."""
    policy = POLICIES[agent]
    rng = random.Random(f"{task['id']}|{agent}|{run}")
    # A task may make one policy worse at it than its overall rate: a
    # stronger policy that checks less is not stronger everywhere, and a
    # training ground where every task points the same way teaches nothing.
    hit = min(0.999, max(0.05, policy["hit"] + task.get("adj", {}).get(agent, 0.0)))
    behaviour = dict(policy, hit=hit)
    r = Recorder(task=task["id"], prompt=task["prompt"], agent=agent, model=f"sim-{agent}", version="synthetic",
                 expected=task["expected"], run_id=run, out_dir=out, tools=tools_for(behaviour["checks"]),
                 system_prompt=PROMPTS[agent])
    run_episode(task, behaviour, rng, r)
    label_synthetic(r.path, NOTE)
    return r.path


def write(tasks: list, runs: tuple, out: Path) -> list:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for task in tasks:
        for agent in POLICIES:
            for run in runs:
                path = make(task, agent, run, out)
                data = json.loads(path.read_text(encoding="utf-8"))
                n = len(data["steps"])
                assert 20 <= n <= 95, f"{path.name}: {n} steps"
                written.append((path.name, n, round(sum(s.get("reward", 0.0) for s in data["steps"]), 2), data["outcome"]["success"]))
    return written


def report(written: list, out: Path, verbose: bool) -> None:
    if verbose:
        for name, n, ret, ok in written:
            print(f"{name}: {n} steps, return {ret:+.1f}, {'pass' if ok else 'fail'}")
    passed = sum(1 for _, _, _, ok in written if ok)
    total = sum(ret for _, _, ret, _ in written)
    print(f"wrote {len(written)} traces to {out} — {passed}/{len(written)} pass, mean return {total / max(1, len(written)):+.2f}")


SETS = {
    #: name: (tasks, runs, directory relative to this file)
    "demo": (lambda: TASKS[:DEMO_TASKS], lambda: RUNS[:DEMO_RUNS], "traces"),
    "train": (lambda: TASKS, lambda: RUNS, "train"),
}


def main(argv: list) -> int:
    which = list(SETS)
    if argv and argv[0] == "--set":
        if len(argv) < 2 or argv[1] not in SETS:
            print(f"error: --set takes one of {', '.join(SETS)}", file=sys.stderr)
            return 2
        which, argv = [argv[1]], argv[2:]
    here = Path(__file__).resolve().parent
    for name in which:
        tasks, runs, subdir = SETS[name]
        out = Path(argv[0]) if argv else here / subdir
        report(write(tasks(), runs(), out), out, verbose=name == "demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
