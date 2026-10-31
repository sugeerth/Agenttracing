# A harness that evolves with its evals

`agentdiff self-evolve` puts real agents (Claude Code, Codex CLI) under
a harness that changes itself. It changes only on what the evals caught,
and only when a paired test says the change helped. The evals evolve too:
they meet each generation's runs first, retire when the harness fixes
what they caught, and are forged anew when the harness provokes
something else.

```bash
agentdiff self-evolve --demo                                # the six tasks it carries, into ~/.agentdiff/self-evolve
agentdiff self-evolve --demo hard                           # six whose specs state every edge case: agents fail here
python3 demo/selfevolve/make_tasks.py                       # in a clone: the same tasks, graders held out
agentdiff self-evolve --task demo/selfevolve/tasks.json --agent haiku -o evo/
agentdiff self-evolve --task demo/selfevolve/tasks.json --agent haiku -o evo/   # again: continues
agentdiff fix --evolve 3                                    # in your repo: its failing tests, the same loop
agentdiff apply --dir evo/g1-h1                             # keep a change the evolved harness made
agentdiff hub                                               # /evolve draws it, /live follows it
```

## One generation

1. **Run.** Every agent runs every task `--runs` times under the current
   harness. Each run's own check grades it.
2. **Judge.** The evolving eval suite (`docs/EVOLVING_EVALS.md`) meets
   these runs before anything is learned from them. It forward-tests the
   evals carried in, retires the noisy and the gone-quiet, and forges
   new ones from these failures.
3. **Act.** Every active eval that caught this generation's failures
   names a failure. `selfevolve.REMEDIES` says which harness change
   reaches it. The one that caught the most is tried, one change at a
   time, so a kept change can be attributed.
4. **Test.** The changed harness runs the same tasks, the same number of
   times. It is **kept** when it wins more tasks than it loses (pass
   rate per task) and no task goes from always passing to always
   failing. Otherwise it is **reverted**, a tie included: an instruction
   should carry only sentences that earned their place. A reverted
   change is not tried again.
5. **Carry.** A kept harness's runs are the next generation's runs, so
   the evals meet what the harness produced. An eval whose failure the
   harness fixed catches nothing and is retired. A failure the change
   provoked becomes a new eval, and the next round acts on it.

**Evals born by intervention.** A kept change gives evidence no
correlation can: preventing what a rule flags raised the pass rate on the
same tasks. So the rule that motivated a kept change joins the eval suite
(`source: intervention`, with the counts in its reason), even when the
forge could not adopt it from a few tasks. From then on it is an eval
like any other. It is forward-tested on every later generation, and when
the harness has fixed its failure it catches nothing and retires, its
reason naming the harness version that prevents it. That closes the loop:
the evals tell the harness what to change, and the harness's changes tell
the evals what to keep.

It stops when every run passes, when no eval names a change the harness
can make, or after `--generations`. The ledger (`evo/ledger.json`)
holds the harness, every change tried and its verdict, and the eval
suite, so the next call continues where this one stopped.

## The hard six

The six small tasks are easy enough that a strong agent passes them
(haiku: 12 of 12, then 6 of 6), so a harness has nothing to learn.
`--demo hard` (`demo/selfevolve/hard`) is six tasks whose specs state
every rule and edge case exactly, graded by held-out tests that hold the
code to each one:
- `cron`: when day of month and day of week are both restricted, either
  one matching is enough.
- `expr`: `-2 ** 2` is -4, `**` groups right to left, and `//` and `%`
  round toward negative infinity.
- `isoweek`: by hand, including the days around New Year that belong to
  the neighbouring ISO year.
- `globmatch`: `*`, `**`, `?`, classes and escapes.
- `ranges`: npm-style caret, tilde and x ranges, and pre-release rules.
- `isodur`: ISO 8601 durations, with only the last number fractional.

Every grader was checked against a reference solution, which is not in
the repository, and every untouched stub fails its grader. Held out means
what it means for the small six: the graders are outside the workspace
the agent is given and are never named to it, not hidden on the disk. A
recorded run's traces (`examples/haiku-hard-confirms`) hold the code its
agents wrote. Here agents fail on what the code does: the case exploration is
for.

## On your repository's history

```bash
agentdiff self-evolve --from-git ~/my-project --python ~/my-project/.venv/bin/python
```

Your own repository's history is a set of tasks with graders nobody had
to write (`agentdiff/gittasks.py`). Each recent commit that changed code
and its tests becomes one:
- **The start:** the agent works on a copy of the commit's parent.
- **The prompt:** the commit's message, trailers removed.
- **The grader:** the test files the commit added or changed. They are
  held out: outside the workspace, never named to the agent, and copied
  in over its work when it has finished, then run.

A commit becomes a task only if its grader is sound: the held-out tests
must fail on the parent (so there is something to do) and pass on the
commit itself (so it can be done, and the grader agrees with the change
the project made). Every commit looked at is reported in
`tasks-report.json`, taken or not, with why.

A grader must also agree with itself. The tests run 3 times on the
parent and 3 times on the commit (`--grader-checks N`), each run on a
fresh copy. A task is kept only if they fail on the parent every time
and pass on the commit every time. One that does not is a flaky grader:
it would pass or fail the agent's runs by chance, and the evolution would
learn from noise. It is left out, with what it did ("on the commit its
tests passed 2 time(s), then failed"). It costs test runs only, never
agent runs.
- **Arithmetic:** a grader that fails 3 runs in 10 on the commit gets
  through one check 70% of the time, three checks 34% and five 17%.
- **Measured on python-slugify:** the same 6 tasks are kept at 1 check
  and at 5, since none of its graders is flaky. Building them took 14 s
  at 1 check and 46 s at 5.
- **A grader the work cannot talk round.** The held-out tests are
  copied in over the agent's work, but its work can still change how
  they run. A `conftest.py`, a test-runner setting, or code that patches
  the test framework can skip, deselect or swap them out, and the
  command then exits 0. So when a task is made, the tests that pass on
  the commit are written down by name (pytest `-rA`, unittest `-v`). A
  run passes only if every one of them passes again, whatever the exit
  code says, and the check names the ones that did not. Changing
  configuration is not refused, only judged by the tests it lets run.
  - **Recorded:** 4 of the 176 recorded python-slugify runs edited
    `tox.ini` or `pyproject.toml`, to register a test file of their own.
    Re-graded with the held grader, all 176 kept the verdict they were
    recorded with.
  - **Tested:** work whose code makes every test skip itself exits 0
    unheld, and fails held, in a test.
  - **Limit:** a `--test-cmd` whose output names no passing tests is not
    held, and the task says so (`held_to` 0).
- **The workspace holds no history.** It is the parent's tree from
  `git archive`, with no `.git`, so the agent cannot read the fix from
  the repository's own log.

- Merges, commits with more than 12 files, and messages under 20
  characters are not used.
- Tests run with pytest when it is installed for `--python`, else
  unittest. `--test-cmd "CMD {files}"` gives any other runner.
- The project's own dependencies must be installed for that Python, as
  for any test run of it. A task whose grader cannot pass on its commit
  is skipped and says why.
- `--commits N` caps the tasks (default 6).

Evolve has the same as a third way in, "On your repository's history".

## Faster and surer

- **Runs at once.** `--jobs N` (default 3) runs that many task runs
  together. Each run has its own copy of the workspace, and the records
  come back in task and run order whatever order they finished in.
- **A weak win is run again.** Kept on its first paired test, a change
  whose sign test is weaker than 1 in 4 (`selfevolve.CONFIRM_P`), that
  is one or two tasks moving, is confirmed before it stays. The tasks it
  moved run again, both arms, and the fresh runs alone must show it
  winning again. It is a replication, not a pooling: pooled with the
  first runs, one lucky pass on a task that never passed would always
  still count as a win. A change that does not hold is reverted, with
  the counts of both tests. `--no-confirm` keeps a change on its first
  test, as before.
- **No run past the point it could matter.** The changed harness runs
  a round at a time: run 1 of every task, then run 2. After each round
  `selfevolve.could_keep` asks whether any outcome of the runs left could
  still make the change kept. Each task's pass count can end anywhere
  from what it has to that plus every run left passing. The change can
  still be kept only if some choice of those ends breaks no task and wins
  more tasks than it loses. If none can, the arm stops: the change is
  reverted, as the full arm would have reverted it, and the card says
  after how many rounds. It is exact over counts, not a statistical
  early stop, so it can't change a verdict. It only stops a change that
  can no longer be kept: a kept change's runs become the next
  generation's, so those always run in full. `--no-settle` runs every
  run.
  - **Synthetic** (six tasks with per-task pass rates taken from the
    recorded python-slugify arms; 300 draws each): it skipped 17% of a
    harmful change's runs at 2 runs a task, 26% at 3 and 35% at 5, and
    about 4–5% of a change that does nothing. The verdict matched the
    full arm's in all 2,700.
  - **Recorded:** replayed over the 8 real changes on disk (96 runs,
    $1.51), it would have stopped none early. They were neutral or
    helpful, and with 2 or 3 runs a task one round rarely leaves a change
    out of reach.
- **One test among several.** Each verdict's sign test says how often a
  change that did nothing would do as well. A run tests a change every
  generation, though, and across several tests one is likelier to look
  good by luck. So the run's tested changes are also taken as one family
  (`selfevolve.family`, `family` in `self-evolve.json`). Each sign test
  is adjusted with Holm's step-down method for all of them, and the card
  shows it beside a kept change's own odds: "1 in 8 by chance (1 in 3
  across the 3 tested)". Like the sign test, it is reported, never used
  to decide.
  - **Recorded:** the 4 changes kept in the real evolutions on disk each
    had a 1-in-2 sign test on its own. In the python-slugify run, which
    tested 3 changes, the adjusted chance is 1: across 3 tests, a change
    that did nothing would do that well somewhere. None of them would
    pass a family-wise correction. That is why a weak win is re-run on
    fresh runs, and why `--prune` tests what was kept again.
- **What the test could show.** Each verdict also carries its sign
  test's resolution (`selfevolve.resolution`, `resolution` in each
  test), said in the run and shown on the card. Like the sign test and
  Holm's adjustment, it is reported and decides nothing. It has three
  parts, all exact:
  - the best chance the test can reach, which is every task moving the
    change's way: with 4 tasks or fewer it cannot reach 0.05 at all;
  - its power against a change that wins 3 in 4 of the tasks it moves,
    at the share of tasks that moved in this run;
  - the fewest tasks that would give that power 80%.
  - **Recorded:** across the 10 changes tested in the real evolutions on
    disk, all on 6 tasks, the best chance was 1 in 64. With 1 to 3 of
    the 6 tasks moving, though, the power was at most 0.025, and 80%
    would take 53 to 161 tasks. The scripted stand-in's 3 tasks cannot
    reach 0.05 however they move. Every real verdict so far was made by
    the paired rule over counts on evidence too thin for a test to
    confirm, which is why replication, the held-out check and `--prune`
    exist.
- **Which tasks can tell a change apart.** Each generation records and
  says which tasks passed some runs and failed others under the harness
  it ran (`selfevolve.task_signal`, `signal` in `self-evolve.json`). A
  task that always passed can only be broken by a change, and one that
  always failed can only be won by a change that fixes it every run.
  Most of a paired test's evidence sits in the first kind. The card
  counts them per generation. When few tasks are mixed, an evolution has
  little to learn from, and more tasks or more runs a task will help
  more than more generations. It is reported, never used to drop a task:
  a task that always passes is where a change that breaks things shows.
  - **Recorded**, over the 9 generations of real evolutions on disk (54
    task-generations): 13 tasks (24%) were mixed, 33 (61%) always passed
    and 8 (15%) always failed. Of the 18 times a tested change moved a
    task, 8 were on mixed tasks, 6 won a task that always failed, and 4
    lost one that always passed. The six-task demo run had no mixed task,
    which is why it learned nothing.
- **Forgetting across the evolution.** `decide` reverts a change that
  breaks a task, but only between the two harnesses it compares. A drift
  over several kept changes passes that check at every step. Under one
  change, a task can go from always passing to passing some runs. Under
  the next, it can go from some to never.
  - `selfevolve.forgetting` (`forgetting` in `self-evolve.json`, printed
    at the end, and a section in `SELF_EVOLVE.md`) looks across the
    whole lineage. A task is *forgotten* when it was solved, passing
    every run, under an earlier harness state and passes no run under
    the last. It is *weakened* when it passes only some.
  - Each such task carries the exact one-sided chance of a fall that
    large with an unchanged pass chance (Fisher), and whether the
    harness changed in between. A fall under an unchanged harness is the
    runs' own noise.
  - Each generation now records its per-task counts (`per_task`), and
    each test records both arms' (`per_task.current`, `.changed`), so
    the lineage can be read this way. It decides nothing.
  - This is EvoHarnessBench's harness-induced forgetting (backward
    transfer), read per task with a test and a same-harness control.
  - **Recorded:** the four real evolutions on disk, re-read from their
    run directories. Nothing was forgotten. Two tasks were weakened,
    each 2/2 → 1/2 (p 0.5). One was in the evolution that kept two
    changes. The other was in a three-generation run whose harness never
    changed: a fall like that is what two runs a task produce by chance.
    A forgotten task with two runs either side has p = 1/6 at best, so
    it is a lead to re-run, not a finding.
- **Every kept change, tested again.** A change is kept on one paired
  test, and a later change can make it redundant, so nothing kept is
  ever questioned again. `--prune` puts each one through the same test
  again when the evolution ends, newest first: the harness it ended
  with, against that harness without the change. The change stays only
  if the rule that kept it would keep it now. A dropped change is not
  tried again, and the smaller harness is what the next one is tested
  against. It runs before `--holdout`, so the held-out test is of the
  harness that ships. It costs one arm per kept change, plus one.
  `--generations 0 --prune` prunes a run that already finished. It is
  also how to re-validate after the agent's model is upgraded: it
  re-tests every kept change against the agent as it runs now.
  - **Recorded:** the python-slugify evolution that kept "read
    `AGENTS.md`" and then the edge-case instruction, re-tested with 36
    real runs ($0.73). The edge-case instruction stays: 6/12 passed
    without it and 9/12 with it, winning 3 tasks and losing none (1 in 8
    by chance). Reading `AGENTS.md` is dropped: on top of the edge-case
    instruction it passed 9/12 with and without, winning one task and
    losing one. It had been kept on one task that moved. One evolution
    is one case, not a rate.
- **Tasks it never saw.** `--holdout N` keeps the last N tasks out of
  the evolution. At the end, the harness it ended with runs against the
  agent as it ships on them, paired and decided the same way: whether
  what it learned carries over to tasks it was never tuned on. It
  changes nothing that was kept. It is reported on the card and as
  `holdout` in `self-evolve.json`.
- **What a change costs.** Each change's card says how its median
  tokens a run compare with the harness as it was ("tokens +19%").

## When the evals are silent: explore

Some failures are in what the code does, not in how the agent worked.
Every run read, edited and checked the same way, and only the held-out
grader tells them apart, as with `semver` below. No eval and no rule
over the steps can name a change for that. Instead of stopping there,
the harness tries the next instruction of a short, fixed library
(`selfevolve.EXPLORE`), one a generation:

| id | the instruction |
|---|---|
| `explore:edge_cases` | list every rule and edge case the task and the docstrings state, test each one, and fix what fails |
| `explore:spec_first` | read the whole specification first and keep to it exactly: accept what it allows, reject what it forbids |
| `explore:review_diff` | re-read the whole change against the task, one line at a time, before finishing |

Before the generic library, it tries what the project itself keeps
(`selfevolve.project_knowledge`, read from the task's workspace, never
from a grader):
- `explore:project_guide`: read the project's contributor guide
  (`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` and the like) first, and
  follow it;
- `explore:project_tests`: read the project's existing tests for the code
  being changed, add tests in their style, and run its test command.

The demo tasks keep neither, so they go straight to the generic library.
On a repository's history they usually keep both. The card labels these
*explored · project*.

An explored change is tested paired like any other and kept only on the
counts. When it is reverted, the next one is tried. When the library is
spent, the harness stops and says so. None of them names a grader, a
test file or a test command. `--no-explore` stops where the evals stop,
as before.

## What the harness can change

| an eval that flags a run when | the change | Claude Code | Codex CLI |
|---|---|---|---|
| it said it was done with no check after its last edit | an instruction to run the check after the last edit | `--append-system-prompt` | appended to the prompt, marked as the harness's |
| it edited something no check saw; it finished before checking; it wrote and never verified | an instruction to check after each change | same | same |
| it left an error unrecovered; its calls failed in a row | an instruction to read the error and change approach | same | same |
| it repeated a call; it went round the same cycle | an instruction to stop and re-read before trying again | same | same |
| it wrote to a file it had not read | an instruction to read first | same | same |
| it called a tool that travels with failure | the tool is denied (never `Bash`, `Read`, `Edit`, `Write`) | `--disallowedTools` | refused: no such flag |

Anything else an eval can detect is listed as weighed, with the reason
no knob reaches it. When there is no adopted eval yet (a handful of
tasks is too few to hold one out), a rule that fires on every failing
run of this generation and on no passing one stands in. It is labelled
a one-generation hypothesis, and the paired test is what decides.

**A grader the agent must not see is never named to it.** A check that
points outside the workspace (a held-out suite, as in
`demo/selfevolve/`) turns the instruction into "test your change against
every case the task states". Telling the agent where the grader is would
be teaching to the test.

## What it writes

| file | what |
|---|---|
| `self-evolve.json` | the lineage: per generation the harness, the runs and failures, the evals born and retired, every candidate weighed, the change tried and its paired test. Rewritten as each generation ends (`"running": true` until the last), with the ledger beside it, so a stopped run keeps what finished |
| `progress.json` | where it is now: the generation and arm, the runs of that arm done, the change being tested, its last lines, and `running`, `stopped`, `failed` or `done` |
| `harness.json` | the harness as it ended: its instructions, denied tools and turn cap |
| `SELF_EVOLVE.md` | the same, as a table |
| `evals/evolve-evals.json` | the eval suite's own lineage, as `evolve-evals` writes it |
| `ledger.json` | what the next call continues from |
| `g<N>-h<V>/` | each arm, an ordinary duel output: traces, records, diffs. Every trace records its harness in `harness.evolved` |

On the hub, `/evolve` draws each lineage as a card: per generation, the
share of runs that passed under the harness as it was and with the
change, the verdict, and the evals born and retired. Every card also
has one chart of every run of every task in every arm (`hub/evochart.py`),
each square opening its trace, which fills in square by square while it
runs. The run's page has
every candidate weighed, the eval river, and every trace by arm. With
`--hub URL`, every run streams to a hub as it goes.

## From the hub: start it, watch it, keep it

On a hub on this machine (127.0.0.1) with Claude Code installed, Evolve
has a **Start** for the six demo tasks and one for your project, with
the agent, the generations and the runs a task. It starts the same
command a terminal would, writing to the folder the hub lists
(`~/.agentdiff/self-evolve` with `hub --claude-code`). Only a signed-in
user, with the form's CSRF token, starts one, and only one runs at a
time.
- **While it runs**, its card says the generation, whether this arm runs
  the harness as it is or the change being tested, the runs of the arm
  done, and what it last said. The page refreshes itself every few
  seconds (a meta refresh, no script). Each generation is drawn as it
  ends, and every run is on Live.
- **Stop** ends it and the agents it started, the way Ctrl-C does. The
  generations that finished are kept, and starting it again with the
  same agent continues from them. A run whose process went away without
  saying so is shown as *ended without finishing*.
- **Use it in your Claude Code.** A harness of yours that kept a change
  can be adopted into a project, from its card or with
  `agentdiff self-evolve --adopt DIR --into PROJECT` (`agentdiff.adopt`).
  Its instructions go into the project's `CLAUDE.md`, between markers.
  Its denied tools go into `permissions.deny` in
  `.claude/settings.local.json`. A turn cap has no setting, so it is
  given as the flag. What was written is recorded, and `--unadopt` takes
  out exactly that. The paired test gave the instructions with
  `--append-system-prompt`; `CLAUDE.md` gives the same words as project
  instructions, a different channel, which the command says. The bundled
  examples cannot be adopted: their harnesses were made on their own
  tasks.

## Real runs

These are real runs of Claude Code (haiku) on this machine, reported as
they came out.

| tasks | runs | result | what the harness did |
|---|---|---|---|
| 3, tests in the workspace | 6 | 6 passed | nothing to fix: converged at g0 |
| the same 3, graders held out | 6 | 6 passed | converged at g0 |
| 6, graders held out (`demo/selfevolve`) | 12, $0.51 | 10 passed; `semver` failed both runs | stopped without a change, which is the right call |
| the same 6, a newer haiku, exploring | 12, $0.05 | 12 passed | converged at g0: nothing to learn |
| the hard 6 (`--demo hard`), exploring | 24, $0.24 | g0: 11 passed, a `ranges` run accepted `>=1.2` | no eval named a change, so it explored: `explore:edge_cases` kept, 11/12 → 12/12, then converged. One task moved, which a change that did nothing would do about half the time: kept by the rule, weak evidence, and the page says so |
| the hard 6, exploring and confirming, 4 at once | 42, $0.45, 15 min | g0: 17 of 18 passed, a `ranges` run failed | explored `explore:edge_cases`, kept on one task moving, so `ranges` ran again both arms: 2/3 → 3/3 on the fresh runs, it held, then g1 passed 18/18. Still one task: the card says "held when ranges ran again" beside the chance (`examples/haiku-hard-confirms`) |
| python-slugify's history (`--from-git`, 6 tasks), the generic library only | 100, $1.29 | g0: 8 of 12 passed | all three explored changes reverted: `edge_cases` and `spec_first` did not hold on the re-runs, and `review_diff` broke a task that always passed |
| the same history, the project's own knowledge first | 76, $1.24, 31 min | g0: 6 of 12 passed | `project_guide` (read `AGENTS.md`) kept, 6/14 → 8/14, and it held on the re-run (0/2 → 1/2); `project_tests` reverted (7/12 both); then `edge_cases`, on top of the guide, kept, 12/18 → 16/18, and it held on the re-runs (3/6 → 6/6). It ended with both instructions |

On python-slugify the base rate moved run to run: under the same harness, 3 to 6 of 12 runs
failed. Each change kept there moved one to three tasks, which a change that did nothing
would do about half the time. So these are two weak wins that each held on fresh runs, not
proof. They are reported as they came out, beside the run where every generic change failed.
In the generic run haiku read `AGENTS.md` in 5 of 100 runs. That is why the project's guide
is now tried first.

The `semver` failures were about what the code does: both runs accepted
versions SemVer forbids (`1.0.0-a_b`, `01.0.0`). Haiku read, edited and
tested in the same way in its passing and failing runs, so no eval and
no rule over how the runs worked could tell them apart. The harness said
so and changed nothing. It does not reach for the grader's output: the
held-out tests' failure text in an instruction would be teaching to the
test.

What was checked for real:
- **Both knobs reach the CLI.** The run records the CLI's argv with
  `--append-system-prompt` and `--disallowedTools WebFetch`, and an
  appended system prompt changes Claude Code's reply. In an agentic run
  haiku did not follow one probe instruction ("when finished, run echo
  …"), which is why every change is tested paired, not trusted.
- **The whole chain.** `fix --evolve` on the pricing bug: the agent
  passed at g0, `apply --dir` wrote its change, and the tests pass.

The decision path (an eval names a failure, a change is tested, kept,
its rule born into the suite, retired when fixed) is exercised end to
end through the same CLI plumbing by `tests/test_selfevolve.py`, with
the Claude Code stand-in in `careless` mode: it skips the check unless
the harness tells it to run one.

## What stays honest

- No model is in the control path. Every decision is a rule over
  counts, written with the counts it rests on.
- Every pass rate is a count of runs graded by the task's own check.
  A run the check could not grade is left out, and said so.
- One change per experiment, on the same tasks and the same number of
  runs. A kept change is attributable; two are never stacked untested.
- The paired test is small. With two runs a task, one lucky run can
  move a task, which is why the verdict states its counts and a later
  generation re-measures the kept harness. Each verdict also states its
  sign test over the tasks that moved: the chance that a change which
  did nothing wins at least as many of them (`p_value`, and in words,
  "1 time in 8"). It is reported beside the verdict and never decides it.
- An explored change is labelled *explored* wherever it is shown. It
  came from no eval, so no eval is born from it when it is kept.
