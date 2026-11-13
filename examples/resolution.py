"""What a sign test over this many tasks could ever show (agentdiff.selfevolve.resolution).

Arithmetic, not data: the exact power of the one-sided sign test that keeps or reverts a harness change.
Read it before spending on runs: with too few tasks, no outcome can reach the threshold.
"""
import _here  # noqa: F401
from agentdiff.selfevolve import resolution, resolution_text

for tasks, moved in ((4, 4), (6, 3), (10, 6), (20, 10)):
    print(f"{tasks} tasks, {moved} moved: " + resolution_text(resolution(tasks, moved)))
