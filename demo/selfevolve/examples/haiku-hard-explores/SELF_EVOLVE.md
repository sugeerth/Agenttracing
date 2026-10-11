# A harness that evolves with its evals

g0: 1 of 12 run(s) failed under v0; tried explore:edge_cases (from exploration: no eval named a change): kept, it won 1 task(s) and lost 0. g1: 0 of 12 run(s) failed under v1. Stopped: converged at g1: every run passed under v1: 1 instruction(s). The harness now: v1: 1 instruction(s).

| generation | harness | failed | evals born | evals retired | change tried | verdict | passed: current → changed |
|---|---|---|---|---|---|---|---|
| g0 | v0 | 1/12 | — | — | explore:edge_cases | kept | 11/12 → 12/12 |
| g1 | v1 | 0/12 | — | — | — | — | — |

## The harness now

- instruction: Before you finish, list every rule and edge case the task and the code's docstrings state, write a quick test for each one, run them, and fix what fails.

Stopped: converged at g1: every run passed under v1: 1 instruction(s).

A change is kept when it wins more tasks than it loses (pass rate per task, same tasks, same runs) and no task goes from always passing to always failing. Every number above is a count of graded runs.
