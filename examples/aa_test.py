"""Two names for one agent: does the comparison invent a difference? (an A/A test)

Data: real, recorded. The same agent and model ran as "A" and as "B" on six tasks, twice each
(demo/vendors/aa-suite; the model's name is withheld). Any difference here is run-to-run noise, so every
interval should include "no difference". The null is also redrawn from the data itself: each task's four
runs are exchangeable, so they are dealt to A and B at random, and the share of deals whose interval
excludes 1 should stay near the nominal 5%.
"""
import glob
import json
import os
import random

import _here
from agentdiff.efficiency import cost_per_success_over_runs, cost_per_success_ratio
from agentdiff.statistics import paired_inference
from agentdiff.trace import Trajectory

runs = {}
for path in sorted(glob.glob(str(_here.ROOT / "demo/vendors/aa-suite/traces/*.json"))):
    task, name, _ = os.path.basename(path)[:-5].split("__")
    t = json.load(open(path, encoding="utf-8"))
    runs.setdefault(task, {}).setdefault(name, []).append((t["totals"]["cost_usd"], t["outcome"]["success"]))


def side(cost, ok):
    return {"totals": {"cost_usd": cost}, "outcome": {"success": ok}}


rate = {t: {n: sum(ok for _, ok in rs) / len(rs) for n, rs in v.items()} for t, v in runs.items()}
p = paired_inference([(rate[t]["A"], rate[t]["B"]) for t in sorted(rate)], labels=("A", "B"))
print(f"pass rate, paired over {p['n_pairs']} tasks: A minus B = {p['diff']:+.2f}: {p['verdict']}")
reports = [{"a": side(*v["A"][0]), "b": side(*v["B"][0])} for _, v in sorted(runs.items())]
r = cost_per_success_ratio(reports)
print(f"cost per success, B / A = {r['ratio']:.3f}, interval [{r['low']:.3f}, {r['high']:.3f}]: "
      f"{'excludes' if r['excludes_one'] else 'includes'} 1")

rng = random.Random(1)
deals, excluded = 400, 0
for _ in range(deals):
    dealt = []
    for task, v in sorted(runs.items()):
        pool = v["A"] + v["B"]
        rng.shuffle(pool)
        dealt.append({"a": side(*pool[0]), "b": side(*pool[2])})
    excluded += bool(cost_per_success_ratio(dealt)["excludes_one"])
print(f"redrawn nulls: the cost interval excluded 1 in {excluded} of {deals} random deals ({excluded / deals:.1%}; "
      "nominal 5%)")

# every run of every task, the task as the cluster: twice the runs, about half the width
trajs = {}
for path in sorted(glob.glob(str(_here.ROOT / "demo/vendors/aa-suite/traces/*.json"))):
    task, name, _ = os.path.basename(path)[:-5].split("__")
    trajs.setdefault(task, {"a": [], "b": []})["a" if name == "A" else "b"].append(Trajectory.from_json(path))
pooled = cost_per_success_over_runs(trajs)
print(f"over every run: B / A = {pooled['ratio']:.3f}, interval [{pooled['low']:.3f}, {pooled['high']:.3f}]: "
      f"{'excludes' if pooled['excludes_one'] else 'includes'} 1")
excluded = 0
for _ in range(deals):
    dealt = {}
    for task, v in sorted(trajs.items()):
        pool = v["a"] + v["b"]
        rng.shuffle(pool)
        dealt[task] = {"a": pool[:2], "b": pool[2:]}
    excluded += bool(cost_per_success_over_runs(dealt)["excludes_one"])
print(f"redrawn nulls over every run: excluded 1 in {excluded} of {deals} ({excluded / deals:.1%}; nominal 5%), "
      "so the real labelling above is one of the draws a 95% interval lets through")
