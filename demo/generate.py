"""Generate all demo trajectories into demo/traces/.

Run from the repo root:

    python demo/generate.py

Deterministic and idempotent: rerunning produces byte-identical files.
Writes one JSON file per (task, agent) pair named
``demo/traces/<task_id>__<agent_name>.json``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
if str(_DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(_DEMO_DIR))

from agents import build_all  # noqa: E402
from simulator import write_trajectory  # noqa: E402

TRACES_DIR = _DEMO_DIR / "traces"


def main() -> int:
    trajectories = build_all()
    for traj in trajectories:
        path = TRACES_DIR / f"{traj['trace_id']}.json"
        write_trajectory(traj, path)
        print(
            f"wrote {path.relative_to(_DEMO_DIR.parent)}  "
            f"agent={traj['agent']['name']:<8}  "
            f"task={traj['task']['id']:<20}  "
            f"success={str(traj['outcome']['success']):<5}  "
            f"steps={len(traj['steps'])}"
        )
    n_ok = sum(t["outcome"]["success"] for t in trajectories)
    print(f"{len(trajectories)} traces written to {TRACES_DIR} ({n_ok} successful runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
