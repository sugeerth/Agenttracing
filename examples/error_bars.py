"""Error bars on an eval score, three ways (agentdiff.statistics).

Data: synthetic and labelled as such. 60 graded items in 6 families of
different sizes, where items in one family share a template, so they are
not independent draws. Then 10 tasks run by two agents, a paired design.
"""
import random

import _here  # noqa: F401
from agentdiff.statistics import clustered_se, paired_inference

rng = random.Random(7)
sizes = [4, 6, 8, 10, 14, 18]                       # families of different sizes
chance = [0.95, 0.95, 0.9, 0.5, 0.95, 0.3]          # each family's own pass chance
values, families = [], []
for family, (size, p) in enumerate(zip(sizes, chance)):
    for _ in range(size):
        values.append(1.0 if rng.random() < p else 0.0)
        families.append(family)

r = clustered_se(values, families)
print(f"accuracy {r['mean']:.3f} over {r['n']} items in {r['clusters']} families")
print(f"  naive interval      {r['ci95_naive']}  (treats every item as independent)")
print(f"  clustered interval  {r['ci95_clustered']}  (CR2, t on {r['df']} df: fewer than {r['clusters'] - 1},"
      f" since the families differ in size)")
print(f"  the naive bar understates the uncertainty by x{r['ratio']}")
print(f"  note: {r['note']}")

# the same ten tasks for both agents: compare per task, never two rates side by side
pairs = [(True, False)] * 4 + [(False, True)] * 1 + [(True, True)] * 3 + [(False, False)] * 2
p = paired_inference(pairs, labels=("A", "B"))
print(f"paired: A minus B = {p['diff']:+.2f}, interval {p['ci95']} (t = {p['critical']} on {p['n_pairs'] - 1} df)")
print(f"  sign test p = {p['sign_test_p']:.3f} on {p['discordant']} discordant tasks: {p['verdict']}")
