# What the runs showed

Ten findings from 45 days of measuring coding agents with AgentDiff
(16 October to 26 November 2026), most useful first. Each says what it
rests on, how big it is, and what to do about it. *Recorded* means real
agent runs kept in this repository. *Synthetic* means seeded simulations,
used where only a known truth could score the method. The full account
of how each was measured is in `RESEARCH_LOG.md` and `CHANGELOG.md`.

To check your own runs against all ten:

```bash
agentdiff findings runs/                    # each finding: applies here, clear, or cannot be told
agentdiff findings runs/ --html check.html  # the same as a page
```

It reads every trace and self-evolve record under the folders given. For
each finding it gives your numbers, from the same functions that
measured it here, and what to do. When a finding cannot be told from
your runs, it says so. On the runs recorded in this repository, 7 of the
10 apply, 2 check clear, and 1 cannot be told (the grader is not in the
traces).

## 1. Checking after the last edit goes with passing (recorded)

Of 100 real runs that edited code:
- **No check after the last edit:** 10 of 38 failed (26%).
- **A check after the last edit:** 3 of 62 failed (5%).

The one-sided Fisher exact p is 0.003. Within the 9 tasks with both
kinds of run, 8 of 26 unchecked runs failed and 0 of 26 checked ones did.
None of the 13 failing runs had a passing check after its last edit.
- **10** never checked.
- **3** finished with their own check failing, all on a task built to be
  impossible, where that was the honest finish.

This is an association: the agents that checked may also have been the
more careful ones.

**Do:** hold agents to the project's own check at the finish
(`agentdiff guard --install`). Replayed over the same runs, that guard
steps into 13 of 16 failing runs, and sends 30 of 90 passing runs back
once to run a check. Replay it over your own runs first:
`agentdiff guard --replay runs/ --html out.html` (`GUARD.md`).

## 2. The change you keep looks better than it is (synthetic)

Over 150 seeded evolutions with the true gain known, the test that
picked a change overstated its gain about 2.5 times. Picking the winner
selects for luck. Fresh runs of the same change were within 0.5 to 3
points of the truth. The one real re-run on disk held in full.

**Do:** re-run a change on fresh runs before keeping it, and report the
re-run's gain. `self-evolve` does this by default; `--no-confirm` turns it
off.

## 3. Testing one change at a time keeps placebos (synthetic)

A loop that tests one change a generation kept changes with no effect 31
to 40% of the time. Testing five together, with the winner made to
replicate, kept 84% fewer placebos for the same spend. One change a
generation climbs further: breadth buys precision, not speed.

**Do:** test three to five candidates together and keep a winner only
when it replicates (`self-evolve --breadth N`).

## 4. A clean streak proves less than it looks

Three straight passes leave room, at 95%, for a 63% failure rate.
Ruling out 10% takes 29 straight passes. The suites shipped here would
need 130 to 208 more runs per agent to do that.

**Do:** report pass rates with exact intervals. Read "3 of 3" as "not yet
known".

## 5. When a task's runs agree, add tasks, not runs (recorded)

On the suite where one agent ran against itself, more runs of the same
tasks bought 0% of what new tasks would. On the evolving harness, they
still bought 92%. Which holds depends on how alike a task's runs are,
and a small pilot can measure that.

**Do:** measure it before choosing between more runs and more tasks.

## 6. Repeated runs part early, then go back to the same work (recorded)

Runs of one task part at a median step 2. Afterwards, 54 to 65% of what
follows lines up again, more than in any of 200 shuffles. What differs
is which actions they take, not their order: order accounts for 0 to
1.5% of the difference, against 20 to 29% in shuffled steps.

**Do:** compare runs by the actions they take. An early fork is not a
different strategy.

## 7. An agent compared with itself looked different (synthetic, recorded)

A rule calling a divergence "systematic" fired on up to 23% of tasks
when both sides were the same agent. An exact test that deals the runs
between the sides at random fires on at most 4.5%. Below four runs a
side, no pattern can be shown at all.

**Do:** test differences against the same runs relabelled at random, and
run at least four a side.

## 8. A run inside a checkout can read the fix from git history

The setup was **synthetic**: a run folder inside a repository whose
history held the fix. Of 8 ordinary git commands, all 8 found the
repository and 6 printed the fix. Setting git's ceiling to the run's own
folder took that to 0. In the **recorded** runs, 11 agents did run
`git status` in their workspace.

**Do:** stop git searching above each run's folder
(`GIT_CEILING_DIRECTORIES`). Flag any run that reaches a repository
outside it.

## 9. The changes that help usually cost more (recorded)

Each change kept on real runs raised the tokens per run:
- reading the project's guide: 1.69 times;
- edge cases on top of the guide: 2.42 times.

Pooling the runs misstated the cost by up to 1.4 times. Pairing them by
task fixes that.

**Do:** report each change's cost paired by task, beside its gain.

## 10. Tests that grade agents let wrong code through (recorded)

On AgentDiff's own history, the tests caught 42 of 60 small changes to
the lines each commit touched, such as a flipped comparison or a
constant off by one. The weakest commit caught 3 of 12, through three
untested paths. Tests added for those paths brought it to 10 of 12.

**Do:** before trusting a suite as a grader, check how many small wrong
versions of the fix it rejects (`--grader-mutants N`).

## What this cannot tell you yet

- **Few real failures.** The recorded runs hold 16 failures in 106.
  Findings 1 and 9 rest on them, and their intervals are wide.
- **Associations, not effects.** Finding 1 shows that unchecked finishes
  fail more, not that forcing a check fixes them. Measuring that needs
  live sessions with and without the guard.
- **Simulations where truth was needed.** Findings 2, 3 and 7 are scored
  on seeded simulations, because only a known true gain can show how far
  a test overstates one.
- **Ideas that did not survive measurement.** The recorded runs never
  showed any of these, so they were logged, not built:
  - agents masking failing tests: 0 of 1,165 traces;
  - a check rerun with nothing changed: 0 of 94 runs;
  - one agent's runs mixing two setups: 0 of 79 agent arms.
