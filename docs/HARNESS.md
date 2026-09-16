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

## The other half: a loop that can change a scaffold

Everything above *reads* a lineage. The agentic loop is what *makes* one,
and until now it had a single actuator.

`ACTIONS` was `("compare", "test-prompt", "stop")` and the state it edited
was `state["prompts"]`. The loop could change how an agent thinks and
nothing else. Meanwhile the triage engine classifies every recommendation
it makes by where the fix lives (`deepcompare.triage.EFFORT`), and of its
nineteen categories only four are prompt-shaped:

| where the fix lives | categories | the loop could act |
|---|---|---|
| `prompt` | retrieval, tool_selection, planning, reasoning | yes |
| `tool-schema` | tool_availability, tool_execution, grounding | no |
| `control-flow` | efficiency, parallel_reads, recovery | no |
| `architecture` | safety, verification, calibration | no |
| `infrastructure` | prompt_cache, result_cache | no |
| `investigation` | latency_concentration, attribute, regression, oracle | not a change |

Eleven of nineteen name the scaffold. So the engine could *recommend* a
scaffold change, this section could *detect* that a gain came from the
scaffold, and the thing that drives improvement could do neither.

### Two knobs, and the rule that decides what may become one

`deepcompare/scaffold.py` turns those findings into hypotheses. A harness
varies two things that a trace records back:

- **`tools`** — the tool table a run is offered, which lands in
  `Trajectory.tools`
- **`budget`** — the settings the loop obeys, which land in
  `Trajectory.budget`

Both are read back by `fingerprint()` above, and that is the whole rule: a
change made by the actuator is visible to the reading that judges it. **A
knob whose effect no trace records could never be judged, so there isn't
one.** Every addition below had to pass that test before it was written.

### The loop had one number, and the list said so

The first version of this module could express two hypotheses. A
**tool-schema finding** that quotes a tool the run is offered and the
agent actually leaned on (`MIN_CALLS`, three) became *withdraw that
tool*. Runs the **harness stopped** rather than the agent — a fifth or
more ending on the cap — became *raise the step cap by half*.

Everything else went in `unactionable`, and that list was longer than the
other one. On the shipped demo loop the first comparison produced exactly
one scaffold recommendation, an `efficiency` finding, and reported it
honestly: *control-flow is the scaffold, but this harness varies only
budget and tools, and no control-flow change is expressible in either.*

That sentence is true and it is also an admission. Nine of the eleven
scaffold categories sat behind it, not because they were unmeasurable but
because the loop had exactly one number — `max_steps` — and a retry
policy or a verification step is not a step cap.

### Three settings, chosen by what a trace can carry

So the loop grew three more, each answering a class it had been refusing,
and each read from `budget` rather than from a call signature so the
settings a run obeyed are **on its trace** and inside its fingerprint:

| setting | the class it answers | what the loop does |
|---|---|---|
| `max_tool_errors` | `recovery` (control-flow) | how many failed calls end the run; it was hardcoded at three, a setting nothing could vary and no trace recorded |
| `dedupe_tool_calls` | `result_cache` (infrastructure) | serves an identical repeat from a harness cache — literally the engine's own fix, *same call, same result, paid for twice* |
| `require_before_answer` | `verification`, `calibration` (architecture) | holds an answer back until a named tool has been called |

Three details are the difference between a knob and a shortcut.

**The cache only caches reads.** Serving a write from a cache means the
write silently did not happen the second time. The loop caches a call
only when its tool *declares* a read effect, and an undeclared effect is
undeclared, not read-only — so it is executed. `scaffold.py` will not
propose the knob at all when nothing on offer declares a read.

**The repeat stays on the trace.** A cached call is still recorded as a
step, with `note: "scaffold: served from the harness cache, not
re-executed"`. The agent did make the call; what changed is only what the
harness paid for it, and a reading that lost the repeat would lose the
finding that motivated the change.

**The gate pushes back once.** It states the reason, and then the second
answer stands however it comes — including wrong. A harness that refuses
until it gets what it wants is not measuring an agent, it is writing one.

And the closure: a gate that works shows up as **the scaffold carrying
the run** — the pass rate rises and each pass costs more steps. That is
precisely the shape `absorption()` above was built to see. The loop can
now make the change the detector was built to catch.

### The unactionable list is still the finding

Everything a knob does not reach goes in `unactionable` with the effort
class and the reason. A hypothesis the runner cannot express is not a
hypothesis; it is a wish, and it belongs somewhere it can be counted
rather than quietly dropped.

What changed is that three classes no longer fall back to the generic
sentence. Each has its own guard, and a guard that does not clear says
what it wanted:

- a `verification` finding that names no offered tool — *the harness will
  not choose the agent's check for it*
- a `result_cache` finding where nothing on offer declares a read — *a
  repeat is only safe to serve from a cache when re-running it would have
  changed nothing*
- a `recovery` finding where no run ended on the tool-error cap — *moving
  it changes nothing that was measured*

That last one matters most. `recovery` is a real finding about a real
pathology, and this harness still cannot fix it; all it owns is when to
stop counting errors. Saying so is a better answer than turning a number
and calling it a response.

Note what stayed out. `too_many_errors` is deliberately **not** in
`_HARNESS_STOPS`: the errors were the agent's, and only the decision of
when to stop counting them was the loop's. It raises the tool-error cap
and never the step cap.

### An agent that runs its own loop gets no budget hypothesis

The knobs are settings of *this* loop. An external agent — a shell
command, a foreign framework — brings its own, and the harness stamps the
budget on the trace it produces without anything obeying it.

Proposing a setting there would be the worst possible outcome of this
whole design: the fingerprint would move, the reading would call the two
generations a different harness, and the run would be identical. A
harness change that did not happen, measured as though it had.

So `hypotheses(..., enforces_budget=False)` sends every budget rule to
`unactionable` with that reason, and the loop passes `agent in
self.providers`. The tool table survives, because the runner really does
hand that over.

### A scaffold win is not an agent win

The planner schedules a scaffold hypothesis as a `test-scaffold`
iteration: same agent, same prompt, one knob turned, paired against the
current scaffold on the same tasks and decided by the same inference as a
prompt change. Two things differ.

**Prompt first, always.** A reasoning change travels to another harness
and a scaffold change does not, so the cheaper claim is tested before the
dearer one.

**A kept scaffold change is recorded as the scaffold's.** The decision
carries `family`, `changes` (in the same words `KINDS` uses) and
`transfers: false`, and its sentence ends *the win is the scaffold's — it
stays with this harness and does not travel with the agent*. A kept
change also retires the agent's older runs, because they were run in a
different harness — the same reasoning the fingerprint uses one layer up.

So the loop now closes: the engine recommends, the actuator turns what it
can and counts what it cannot, the paired experiment decides, and the
reading above can tell afterwards which half of the agent the gain
belonged to.

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
