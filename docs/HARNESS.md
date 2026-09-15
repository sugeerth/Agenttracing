# The harness beside the agent

`agentdiff evolve` reads a self-evolving agent as a lineage and asks, at
every step, whether the change helped. `agentdiff coevolve` grows an eval
alongside it so the agent cannot quietly optimise a fixed one. Both of them
were reading one thing and calling it another.

This is the section that separates them: `aggregate.harness_evolution`,
from `deepcompare/harnessevo.py`, attached to every lineage after the
co-evolving eval. Three readings, and one invariant.

## Two artifacts are not the agent

A generation's `artifacts` are `system_prompt`, `rules`, `skills`,
`memory`, `tools` and `config`. The first four are how the agent thinks:
what it was told, what it remembers, what it knows how to do. The last two
are what it runs inside — the tools it may call, and the settings the loop
around it obeys.

Those are different claims. A prompt that teaches the agent to check its
source is the agent learning something. A `max_search_retries` raised from
two to five is the agent *buying* something. Both show up as "the agent
improved", and they transfer differently: reasoning travels to a new
harness and scaffold does not.

So every step gets a `kind` — `reasoning`, `scaffold`, `mixed` or `none` —
and the rule that decides it ships in the output as `kinds`, so a reader
can check it rather than take it on trust.

## Nothing recorded what ran it

Generations are recorded days apart. Nothing in a lineage says which model
served the steps, at what temperature, under which step cap, with which
tools actually on offer. If any of that moved between two generations, the
delta credited to the prompt diff is confounded, and before this section no
reading in the repository could say so. It is gap 3 of `docs/TRACING.md` at
the place it does the most damage.

`fingerprint()` reads what the *traces* recorded, never what the manifest
claims — the manifest says what a generation **is**, the traces say what
**ran** it:

| reading | what it is |
|---|---|
| `decoding` | every model the step telemetry names, with its temperature and top-p |
| `tools_offered` | the tool table the runner actually offered (`Trajectory.tools`) |
| `caps` | the limits the loop enforced (`Trajectory.budget`) |
| `token_basis` | how the token counts were obtained — a harness property, not an agent one |
| `schema_versions` | the trace contract these episodes were written against |
| `digest` | SHA-256 over all of it, so two generations compare by one value |

`measurable: false` with the reason when the episodes record none of it,
which is the common case today. An unrecorded harness is not a constant
one, and the section will not read it as though it were.

### The names are kept out of the digest, on purpose

The declared model and version travel separately, under `identity`. A
lineage that versions its model string per generation — `agent@g0`,
`agent@g1` — would otherwise have every one of its steps marked confounded
by a rename. That is true of the string and worthless as a finding.

And here is the part worth stating plainly: **from a trace, a renamed model
and a genuinely different model look exactly alike.** There is nothing in
the record that separates them. So an identity change does not make a step
confounded and does not leave it attributable — it makes it `assumed`, and
the sentence says which question the record cannot settle. A model snapshot
id recorded once per generation turns that assumption into a check, and
that is the single cheapest change anyone instrumenting a lineage can make.

## The invariant: `attributable` is never reached without evidence

Every step carries `attribution.status`:

- **`attributable`** — a fingerprint on both sides, the harness identical,
  and the model string unchanged too. The delta is the artifacts'.
- **`confounded`** — the harness itself moved: a different temperature, a
  different tool table, a different cap. Two things changed between the two
  measurements, so the delta belongs to neither, and the changes are named.
- **`assumed`** — either no fingerprint at all, or the harness held and the
  model string moved. Both rest on something the record cannot check, and
  `basis` says which.

### A tool table the agent moved itself

`Trajectory.tools` is what the runner offered, and a self-evolving agent
that edits its own `tools` artifact makes that table move. An operator
adding a tool and the agent adding one look identical in the fingerprint —
but the artifact diff can tell them apart, so `reconcile()` does. When the
table moved by exactly what the step's own diff added and removed, the agent
did it: the entry moves to `explained` with the reason, and it is not
counted as the environment shifting underneath. Nothing is dropped, and the
reasoning is visible.

Without that, the shipped demo lineage read as six confounded steps out of
six, all of them the agent's own work being blamed on its environment.

## Absorption: the gain that is not the agent's

The reading neither `evolve` nor `coevolve` could make.

An agent can raise its pass rate without getting better at the task, by
having more put around it: a verifier restored, a retry budget widened, a
tool that does the job it used to reason through. The give-away is that the
**work per pass rises at the same time**. The agent is not needing less; it
is being carried further.

`absorption()` measures it over both sides' episodes — the pass rate, and
the steps, tool calls and repeats each *passing* episode cost. The flag
fires when the pass rate rose and the work per pass rose by more than
`ABSORB_MARGIN` (10%). Every quantity is a bootstrap interval; fewer than
`MIN_PASSES` (3) passing episodes a side is `measurable: false` with the
reason, because a work-per-pass ratio over one or two successes is noise
wearing a number's clothes.

**It is not an accusation.** Restoring a verifier costs steps and buys
correctness; so does a tool that does its job well. Both are real
improvements to the system. The point is that they are a different claim
from "the agent got better", and they travel differently — the gain stays
with the scaffold, so a harness that drops it drops the gain. Outcome-only
grading cannot tell the two apart, which is exactly why it is worth a
reading of its own.

On the shipped lineage it fires once, at g3→g4, where the agent restored
the `run_check` tool it had removed one step earlier: the pass rate rose
46.7 points and each pass cost 6.6 more steps.

## The eval learns to watch it too

`coevolve` gains one probe, `absorption`, which fires on the same shape and
proposes metrics that watch what a *passing* episode costs — `steps` and
`tool_calls` under `where: {feature: success, op: "==", value: 1}`.

The spec language could already say that. What was missing was anything
that thought to ask: every other probe reads the outcome, or a metric the
agent moved, and none of them reads what a success costs.

Its candidates go through the same five validators at the same
Bonferroni-adjusted level as every other. On the demo lineage they are
**rejected** — `tool_calls_per_pass` correlates at 0.9 with the base metric
`tool_calls_mean`, so the eval declines to add a reading it already has,
and the ledger records why. That is the validators working, not the probe
failing. Adding the probe raised K at that step from 6 to 8 and tightened
alpha from 0.0083 to 0.0063; **no adoption, rejection or recommendation
changed.**

## What it cannot see

A fingerprint is read from what the traces recorded, so a harness change no
step wrote down is invisible here as it is everywhere else. Absorption is
measured over the episodes the lineage kept, and a grader that was fooled
fools it along with everything else.

The fix for the first is one field. Record, once per trace: the model
snapshot id, the decoding parameters and seed, the prompt-template hash,
the tool names *with versions*, the retrieval index version and the harness
commit. Then "these generations are comparable" becomes something the
engine checks instead of something the reader assumes.
