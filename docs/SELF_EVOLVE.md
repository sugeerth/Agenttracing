# A harness that evolves with its evals

`agentdiff self-evolve` puts real agents (Claude Code, Codex CLI) under
a harness that changes itself. It changes only on what the evals caught,
and only when a paired test says the change helped. The evals evolve too:
they meet each generation's runs first, retire when the harness fixes
what they caught, and are forged anew when the harness provokes
something else.

```bash
agentdiff self-evolve --demo                                # the six tasks it carries, into ~/.agentdiff/self-evolve
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
| `self-evolve.json` | the lineage: per generation the harness, the runs and failures, the evals born and retired, every candidate weighed, the change tried and its paired test |
| `harness.json` | the harness as it ended: its instructions, denied tools and turn cap |
| `SELF_EVOLVE.md` | the same, as a table |
| `evals/evolve-evals.json` | the eval suite's own lineage, as `evolve-evals` writes it |
| `ledger.json` | what the next call continues from |
| `g<N>-h<V>/` | each arm, an ordinary duel output: traces, records, diffs. Every trace records its harness in `harness.evolved` |

On the hub, `/evolve` draws each lineage as a river: per generation, the
share of runs that passed under the harness as it was and with the
change, the verdict, and the evals born and retired. The run's page has
every candidate weighed, the eval river, and every trace by arm. With
`--hub URL`, every run streams to a hub as it goes.

## Real runs

These are real runs of Claude Code (haiku) on this machine, reported as
they came out.

| tasks | runs | result | what the harness did |
|---|---|---|---|
| 3, tests in the workspace | 6 | 6 passed | nothing to fix: converged at g0 |
| the same 3, graders held out | 6 | 6 passed | converged at g0 |
| 6, graders held out (`demo/selfevolve`) | 12, $0.51 | 10 passed; `semver` failed both runs | stopped without a change, which is the right call |

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
  generation re-measures the kept harness.
