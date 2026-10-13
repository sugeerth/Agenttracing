# A harness that evolves with its evals

g0: 6 of 6 run(s) failed under v0; evals born: claims_without_check; tried instruction:claims_without_check (from a one-generation hypothesis): kept, it won 3 task(s) and lost 0. g1: 0 of 6 run(s) failed under v1. Stopped: converged at g1: every run passed under v1: 1 instruction(s). The harness now: v1: 1 instruction(s).

| generation | harness | failed | evals born | evals retired | change tried | verdict | passed: current → changed |
|---|---|---|---|---|---|---|---|
| g0 | v0 | 6/6 | claims_without_check | — | instruction:claims_without_check | kept | 0/6 → 6/6 |
| g1 | v1 | 0/6 | — | — | — | — | — |

## The harness now

- instruction: Before you say the work is done, run the task's check (`python3 -m unittest -q`) after your last edit and read its result; if it fails, keep working.

Stopped: converged at g1: every run passed under v1: 1 instruction(s).

A change is kept when it wins more tasks than it loses (pass rate per task, same tasks, same runs) and no task goes from always passing to always failing. Every number above is a count of graded runs.
