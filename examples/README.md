# Worked examples

Each script runs from a plain checkout (`python3 examples/<name>.py`), on data already in the repository,
and says where that data came from. The output below is the script's own, and
`tests/test_examples.py` reruns every script and fails if what it prints is no longer what is shown here.

## Error bars on an eval score

Naive against clustered error bars, and a paired comparison of two agents, on synthetic data. The clustered bar is CR2, with t on Bell–McCaffrey degrees of freedom. Six families of different sizes rest on fewer than five degrees of freedom.

```
$ python3 examples/error_bars.py
accuracy 0.700 over 60 items in 6 families
  naive interval      [0.5831, 0.8169]  (treats every item as independent)
  clustered interval  [0.2027, 1.0]  (CR2, t on 3.94 df: fewer than 5, since the families differ in size)
  the naive bar understates the uncertainty by x2.984
  note: only 6 clusters: the clustered interval runs short of 95% this few (in simulation 0.86 at 5 equal clusters, 0.94 at 19), so read it as a lower bound on the uncertainty
paired: A minus B = +0.30, interval [-0.1828, 0.7828] (t = 2.2622 on 9 df)
  sign test p = 0.375 on 5 discordant tasks: not distinguishable on 10 paired task(s): 4 favour A, 1 favour B, 5 tied
```

## Which steps to replay, and how many

One demo pair's replay order, then a split-conformal top-k calibrated on half of 120 generated pairs and measured on the other half, against plain step order.

```
$ python3 examples/replay_sets.py
t05: the failing run has 5 steps; decisive step 1 (committed)
  replay in this order: [1, 2, 3, 4, 0] ...
target 90%: replay the top 1 step (mean set 1.0), held-out coverage 92% over 60 cases; plain step order needs 6 for 95%
target 95%: replay the top 3 steps (mean set 2.97), held-out coverage 98% over 60 cases; plain step order needs 6 for 95%
target 99%: 40 calibration cases are too few for any set to carry it
the guarantee is marginal and holds for failures like these; on unlike traces, measure again
```

## Cost per success, with an interval

Real recorded runs: two agents on six tasks, billed at real cost. B's cost per success over A's, with a jackknife interval over tasks.

```
$ python3 examples/cost_per_success.py
6 tasks: A $0.0495 per success, B $0.0912 per success
B / A = 1.84, 95% interval [1.33, 2.55] (jackknife over tasks on the log ratio, t on tasks − 1)
B costs 1.84× A per success (95% interval 1.33–2.55 over 6 paired tasks): the interval excludes 1, so on these tasks the gap is not resampling noise
```

## pass^k: passing every one of k tries

The demo runs corpus (synthetic): the unbiased C(c,k)/C(n,k) per task, averaged, with an interval that covers both task spread and run noise.

```
$ python3 examples/pass_k.py
atlas-v2: pass^1 = 0.875 [0.625, 1.0]; pass^2 = 0.875 [0.4761, 1.0]; pass^3 = 0.875 [0.3285, 1.0]
bolt-v3: pass^1 = 0.667 [0.375, 0.9167]; pass^2 = 0.625 [0.2182, 0.875]; pass^3 = 0.625 [0.1019, 0.875]
a one-run pass rate overstates what an agent does every time; read pass^k at the k you will run it
```

## What the sign test could ever show

Arithmetic, before any spend: with this many tasks, the best p the sign test can reach, its power, and the tasks that would give 80%.

```
$ python3 examples/resolution.py
4 tasks, 4 moved: with 4 task(s) the best chance the sign test can reach is 0.0625, so it cannot reach 0.05 however the tasks move; against a change that wins 75% of the tasks it moves, with 4 of 4 moving as here, its power is 0.00 and about 23 tasks would give 80%
6 tasks, 3 moved: with 6 task(s) the best chance the sign test can reach is 0.0156 (every task moving the change's way); against a change that wins 75% of the tasks it moves, with 3 of 6 moving as here, its power is 0.03 and about 53 tasks would give 80%
10 tasks, 6 moved: with 10 task(s) the best chance the sign test can reach is 0.000977 (every task moving the change's way); against a change that wins 75% of the tasks it moves, with 6 of 10 moving as here, its power is 0.18 and about 44 tasks would give 80%
20 tasks, 10 moved: with 20 task(s) the best chance the sign test can reach is 9.54e-07 (every task moving the change's way); against a change that wins 75% of the tasks it moves, with 10 of 20 moving as here, its power is 0.35 and about 53 tasks would give 80%
```

## Replay with controls

Scripted stand-in agents, no model and no spend. A correction is credited only when it beats the same prefix replayed with nothing changed.

```
$ python3 examples/replay_controls.py
the recorded run failed: True; replaying step 0, the tool call
  agent that reads the tool: corrected replays passed 3/3, controls passed 0/3 -> replay-verified
  agent that knows the answer anyway: corrected replays passed 3/3, controls passed 3/3 -> replay-mixed
verified needs at least half the corrected replays to pass, and half the replays more than the controls
```

## Two names for one agent (an A/A test)

Real recorded runs: one agent ran as both A and B, six tasks, twice each, for $0.06 in all. Nothing here should tell them apart. Redealing each task's four runs at random checks that the cost interval's false alarms stay near 5%. Before this interval, the old bootstrap excluded 1 on this very data, and in 11% of the random deals.

```
$ python3 examples/aa_test.py
pass rate, paired over 6 tasks: A minus B = +0.00: not distinguishable: only 6 paired task(s) (needs at least 10)
cost per success, B / A = 0.934, interval [0.870, 1.003]: includes 1
redrawn nulls: the cost interval excluded 1 in 13 of 400 random deals (3.2%; nominal 5%)
```
