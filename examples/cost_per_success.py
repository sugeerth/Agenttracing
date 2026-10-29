"""What each agent pays per solved task, and whether the tasks can tell (agentdiff.efficiency).

Data: real, recorded. Two agents worked six tasks once each (demo/vendors/live-suite), each run graded by its
tests and billed at its real cost. A failed run's spend still counts against its side.
"""
import glob
import os

import _here
from agentdiff.efficiency import cost_per_success_ratio, ratio_text
from agentdiff.report import compare
from agentdiff.trace import Trajectory

by_task = {}
for path in sorted(glob.glob(str(_here.ROOT / "demo/vendors/live-suite/traces/*__r1.json"))):
    by_task.setdefault(os.path.basename(path).split("__")[0], []).append(path)
reports = [compare(Trajectory.from_json(pa), Trajectory.from_json(pb)) for _, (pa, pb) in sorted(by_task.items())]

r = cost_per_success_ratio(reports)
print(f"{r['tasks']} tasks: A ${r['a']:.4f} per success, B ${r['b']:.4f} per success")
print(f"B / A = {r['ratio']:.2f}, 95% interval [{r['low']:.2f}, {r['high']:.2f}] over {r['samples']} paired resamples"
      f" of the tasks ({r['undefined']} left a side without a success)")
print(ratio_text(r, {"a": "A", "b": "B"}))
