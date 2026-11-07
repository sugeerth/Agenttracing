"""How often an agent passes every one of k tries (agentdiff.reliability).

Data: synthetic and labelled as such. The demo runs corpus (demo/runs/traces) has 3 runs per task per agent.
pass^k is the mean over tasks of C(c,k)/C(n,k). Its interval covers both how the tasks differ and each task's
own run-to-run noise.
"""
import glob
import os

import _here
from agentdiff.reliability import reliability
from agentdiff.trace import Trajectory

data = {}
for path in sorted(glob.glob(str(_here.ROOT / "demo/runs/traces/*.json"))):
    task, agent, _ = os.path.basename(path)[:-5].split("__")
    data.setdefault(task, {}).setdefault(agent, []).append(Trajectory.from_json(path))

for agent, rel in sorted(reliability(data)["per_agent"].items()):
    curve = rel["pass_hat_k"]["curve"]
    print(f"{agent}: " + "; ".join(f"pass^{p['k']} = {p['value']:.3f} {p['ci95']}" for p in curve if p["value"] is not None))
print("a one-run pass rate overstates what an agent does every time; read pass^k at the k you will run it")
