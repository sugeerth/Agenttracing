"""Which steps to replay first, and how many to replay (agentdiff.conformal).

Data: synthetic and labelled as such. One demo pair (demo/traces), then a
corpus of 120 generated failing/passing pairs whose decisive step is known,
because the generator planted it.
"""
import importlib.util
import tempfile
from pathlib import Path

import _here
from agentdiff.bench import run_benchmark
from agentdiff.report import compare
from agentdiff.trace import Trajectory

traces = _here.ROOT / "demo" / "traces"
a = Trajectory.from_json(traces / "t05_flight_duration__atlas-v2.json")
b = Trajectory.from_json(traces / "t05_flight_duration__bolt-v3.json")
diag = compare(a, b)["diagnosis"]
dec = diag["decisive_step"]
print(f"t05: the failing run has {len((a if diag['subject'] == 'a' else b).steps)} steps; decisive step {dec['step']}"
      f" ({'committed' if dec['committed'] else 'contested'})")
print(f"  replay in this order: {dec['replay_order'][:8]} ...")

# a corpus with known answers: calibrate k on half, measure the other half
spec = importlib.util.spec_from_file_location("gen", _here.ROOT / "demo/diagnosis_bench/generate_scale.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
with tempfile.TemporaryDirectory() as tmp:
    gen.generate(Path(tmp), 120, strip=True)
    sets = run_benchmark(tmp)["replay_sets"]
for s in sets:
    ours, plain = s["replay_order"], s["step_order"]
    if ours["k"] is None:
        print(f"target {s['target']:.0%}: {ours['calibration']} calibration cases are too few for any set to carry it")
        continue
    print(f"target {s['target']:.0%}: replay the top {ours['k']} step{'' if ours['k'] == 1 else 's'} (mean set {ours['mean_set']}), held-out coverage"
          f" {ours['coverage']:.0%} over {ours['test']} cases; plain step order needs {plain['k']} for {plain['coverage']:.0%}")
print("the guarantee is marginal and holds for failures like these; on unlike traces, measure again")
