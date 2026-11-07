# A recorded A/A suite: one agent under two names

This directory holds real runs. Claude Code 2.1.312 worked the six tasks in
`../suite/suite.json` twice each as "A" and twice as "B", through
`agentdiff duel`. The model was the same on both sides, and its name is
withheld from these files. Each run was graded by the task's check. The
24 runs cost $0.062 in all, as the CLI reported.

| task | A | B |
|---|---|---|
| bugfix-pricing | pass, pass | pass, pass |
| feature-slugify | pass, pass | pass, pass |
| perf-dedupe | pass, pass | pass, pass |
| two-file-duration | pass, pass | pass, pass |
| guarded-inventory | pass, pass | pass, pass |
| contradictory-rounding (impossible) | fail, fail | fail, fail |

**What it is for.** Nothing here can tell A from B: they are the same
agent, so any gap is run-to-run noise. A comparison that claims a
difference on this data is wrong, and the data checks for that.
- The paired pass-rate test: no difference, and not distinguishable on
  6 tasks. pass^1 and pass^2 are 0.833 for both.
- The cost-per-success ratio: B / A = 0.934.
  - The percentile bootstrap shipped before said 0.886–0.980, "the gap
    is not resampling noise". That is a false claim of a difference.
  - Dealing each task's four runs to A and B at random, 400 times, that
    interval excluded 1 in 11% of deals, against a nominal 5%.
  - The jackknife interval that replaced it says 0.870–1.003 here, and
    excludes 1 in 3.2% of the deals.
- **Over every run** (`cost_per_success_runs`, both runs of each task
  pooled, the task as the cluster): B / A = 0.960, with interval
  0.926–0.996, which is half as wide.
  - That excludes 1. Over 2,000 redeals of these runs, though, the
    pooled interval excluded 1 in 6.1% of them.
  - So the real labelling is one of the draws a 95% interval lets
    through: B's runs happened to be cheaper on five of the six tasks.
  - This is what "95%" means, and why the sentence the ratio prints now
    says "about 1 time in 20 or less" rather than "not noise".

`examples/aa_test.py` reruns all of it, and `tests/test_cost_ratio.py`
holds it.
