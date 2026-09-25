# The most informative trace we could record

Every analysis in this repository is bounded by one thing: what the
trace wrote down. This file is the honest account of that bound — what
the schema records today, what it measurably does not, and what each
missing fact would buy. It is a design document, not a changelog: the
gaps below are open.

## The test of an informative trace

Not "how many fields" — a log with everything in it is still a log. The
test is:

> **Can every number in the report be recomputed from the trace alone,
> and can the trace say what would have changed the outcome?**

The first half is *sufficiency*: the analysis is a pure function of the
record, so the record is the ground truth and two readings can be
diffed. This repository already holds that line — the engine has no
network and no clock, so a number it prints came from the trace or it
is marked `measurable: false`.

The second half is the one that separates a good trace from the best
one. A trace that records only what happened supports *attribution*:
this run differed from that one here. A trace that also records **what
was nearly done** supports *intervention*: this step chose A over B, and
B was available. Attribution tells you where to look; intervention tells
you what to change. Almost every agent trace in the wild, including this
one, records only the road taken.

## What the schema records today

`SCHEMA.md` is richer than most: per step the kind, name, input, output,
tokens with a *basis* saying whether they were measured or estimated,
latency, error, the declared read/write effect, the sub-agent span, the
environment's reward, the policy's value and advantage, and — when the
serving stack returns them — the model's own token confidence, entropy
and an interval over it, plus sparse-autoencoder features that fired.

Coverage over the shipped corpus (324 traces, 11,579 steps, 5.9 MB,
**508 bytes per step**; `python3 -c` over `demo/traces`, `demo/rl/train`
and `demo/evolve/lineage` — the three corpora the bundle packs):

| field | steps carrying it | what it unlocks |
|---|---|---|
| `tokens`, `latency_s`, `input` | 100 % | the budget, the burn-down, the timing |
| `tokens_basis` | 99.0 % | measured vs estimated, never re-estimated |
| `output` | 77.7 % | provenance, the corpus, the search map |
| `reward` | 99.0 % | the training ground, the reward audit |
| `model` (telemetry) | 25.4 % | uncertainty, "did it know it was wrong" |
| `effect` (read/write) | 23.5 % | the permission and integrity checks |
| `value` | 22.3 % | critic calibration |
| `span` (sub-agent) | 16.8 % | the horizon, the lanes |
| `error` | 3.9 % | the failure attribution |
| `quality` | 1.0 % | an annotation, never a measurement |
| `attempt` | 0.03 % | a harness retry told from an agent's repeat (absent means one try, by design) |

One row of that table was wrong when this file was first written, in the
way this file exists to warn about. `reward` was given as 76.7 %, which is
the share of steps carrying a **non-zero** reward; 99.0 % carry the field.
The 2,586 steps in between recorded `reward: 0` — the environment was
asked and paid nothing, which is a measurement and not an absence. Reading
them as unrecorded is the same mistake as rendering a confident zero where
nothing was written down, and it is worth leaving the correction visible:
the distinction is easy to lose even while writing the document about it.

The shape of that table is the point: the fields that cost nothing to
record are everywhere, and the fields that need the *agent's* cooperation
— effects, values, spans — are the sparse ones. Those are exactly the
fields the honest analyses need, which is why so many readings end in
`measurable: false`.

## The eight gaps, ranked by what they buy

### 1. The context actually sent — the single biggest gap

A step records its *input* (the query, the tool arguments) but not the
**assembled context the model saw**: which system prompt version, which
memory entries, which retrieved chunks in which order, which earlier
steps were still in the window, and **what was evicted or truncated to
make room**.

Without it, the most common production failure is invisible. When an
agent forgets a constraint it was given twenty steps ago, the trace
shows a step that ignored the constraint; it cannot show that the
constraint was no longer in the window. Every "the model is dumb"
incident that is really a context-assembly bug lives in this gap.

*Unlocks:* truncation-caused failure as a named cause; context-rot over a
long run; a real answer to "was it told?" that today the Data view can
only answer at the level of the system prompt; cache-hit reasoning.
*Costs:* the largest of any proposal — the window can be 100 KB a step.
Record it as **references plus a hash**, not text: the ids of what went
in, the order, the token budget, what was dropped and by which rule, and
a digest of the assembled prompt. Roughly 200 bytes a step, not 100 KB.

### 2. The alternatives not taken

At a decision the agent had candidates: the tools it could have called,
the retrieval results below the cut, the next actions the policy ranked.
Record the top few with their scores and which was chosen.

This is what turns the diagnosis from correlational to causal. Today
`counterfactual.py` answers "what if this run had adopted the *other
run's* decision", which needs two runs and an alignment. With recorded
alternatives, a single run answers "what if it had taken its own second
choice", and the decisive-step claim becomes checkable without a twin.

*Unlocks:* single-run counterfactuals; a real margin at each decision
(how close was it?); a reward audit that can ask whether the reward
preferred the chosen branch; the eval's probes could watch *choice
quality* rather than only outcome.
*Costs:* a few hundred bytes at model steps. Most frameworks already
have this and throw it away.

### 3. The environment fingerprint

`agent.model` is a name. Two runs a week apart with the same name may be
different weights, a different decoding temperature, a different tool
version, a different index. Every A/B in this repository is confounded
by exactly that, and nothing in the trace can detect it.

Record, once per trace: the model snapshot id, decoding parameters and
seed, the prompt-template hash, tool names *with versions*, the
retrieval index version, and the harness commit. Then "these runs are
comparable" becomes a claim the engine can check instead of an
assumption the reader makes.

*Unlocks:* a comparability check before any comparison; drift attributed
to a version change rather than to the agent; reproducible replay.
*Costs:* a few hundred bytes **per trace**. The cheapest large win here.

### 4. Cache and attempt structure

Production agents retry and cache. A trace that does not say "this was
attempt 2 after a 429" or "these 8,000 tokens were a cache read"
mis-states both cost and behaviour — cache reads are often an order of
magnitude cheaper, so a token count without a cache split is not a
budget, and a retry counted as a fresh call inflates every rate.

**Both integers are now recorded; the reason is not.** `cached_tokens`
closed the cache half (see gap 8). The attempt half closed with
`Step.attempt`: the loop's `max_tool_retries` re-runs a failed call
rather than handing the error back to the agent, and each try is its own
step numbered from one. `fetches` reads the number rather than guessing
at it — a step with `attempt > 1` is a **retry**, anything else repeating
an earlier name and input is a **repeat** — and `budget.waste` splits
`in_retries` from `in_repeats`, because tokens burnt re-running a flaky
call are the platform's cost and tokens burnt asking twice are the
agent's behaviour. The two used to be one number, and the number was
called "repeats".

What that bought is a hypothesis the actuator could not previously make:
`recovery` findings on runs that *survived* their tool errors used to be
unactionable — the only knob was the cap that ended runs, and these runs
did not end on it. The rule now proposes `max_tool_retries` instead, and
the experiment is decidable either way.

The honest limit is that the count is only ever as good as the record.
Where no step numbers its attempts, retries read 0 — an absent record,
not a measured absence — so every reading that reports the split also
reports `retry_basis`, which says how many of the run's fetches numbered
anything at all.

*Still open:* **why** a retry happened. `attempt: 2` does not say whether
the first try hit a 429, a timeout or a genuine tool error, and those
three want different fixes — back off, raise a timeout, fix the tool.
A retry *cause* on the step would attribute flakiness to infrastructure
rather than to the agent, which is the last thing this gap promised.
*Costs:* one short enum a step, on the steps that have one.

### 5. Retrieval at chunk granularity

Today a fetch records the query and the output's size. Record the chunk
ids, their scores, the index version, and which chunk the used span came
from. The provenance in the Data view traces the answer to a *step*; it
could trace to a *passage*.

*Unlocks:* "the right document was retrieved and the wrong passage was
used" as a distinct, common failure; retrieval quality measured without
a separate eval; the search map drawn at the level the agent actually
reasoned over.

### 6. The agent's own causal self-report

One optional field: `because: [step ids]` — this step was taken in
response to those observations. It is the cheapest informative field
imaginable and no schema I know of has it.

It is not to be trusted, and that is fine: the `data` section already
computes content overlap between a step and the fetched outputs it
shares text with. The self-report and the measured overlap can be
*compared*, and a systematic divergence between what the agent says it
used and what its text actually shares is itself a finding — the same
move the lineage's "claimed without called" check already makes.

### 7. Time, split

`latency_s` conflates queueing, network, and compute. Split it, and the
timing analysis stops blaming the agent for the platform.

**Half of this one is now closed.** `latency_s` said how long a step took
and nothing said *when it began*, so every timeline here placed a step by
summing the durations before it — an assumption that the run was strictly
sequential, made silently by every strip, band and phase in the page. For
a loop that runs independent calls concurrently it is wrong, and nothing
in the record said so.

`Step.started_s` (optional) makes the clock read rather than assumed. The
`Recorder` stamps it from its own clock only when it also timed the step;
given a duration from elsewhere it does not, because a wall-clock start
paired with a borrowed duration describes no real run. A harness that
knows when it issued a call passes both, and this one does. `timing.timeline` then reports `basis`, the run's
real `span_s`, and `overlap_s` — the durations' sum minus the span, which
is the seconds two or more steps were running at once. Where the starts
are absent it says `reconstructed` and `overlap_s: null`, never `0.0`,
because a run whose concurrency nothing recorded is not one that had none.

What is still open here is the *split*: a recorded start tells you when
the wait began, not how much of it was queue, network or compute.

### 8. Money, per step

`cost_usd` exists on totals only. Per step, split input/output/cache, it
makes the budget view a cost view, and "the fix saves $X" a measured
claim rather than a token-count proxy.

**The tokens half is now closed; the money half is not.** A step carries
`input_tokens`, `output_tokens` and `cached_tokens` where the provider
reports them, so the reading can say *400 in, 20 out, 300 of the input
served from cache and not paid for* rather than one undifferentiated
count. What is still missing is the price: a cost per step needs a rate
card, that rate card lives outside the trace, and this engine will not
invent one. Either the provider reports a cost per call, or `cost_usd`
stays a whole-run figure and the token split is the honest proxy — named
as a proxy.

## What would make it the best, beyond fields

Three properties matter more than any single field.

**Every fact carries its basis.** This repository already does it for
tokens (`tokens_basis`), for grading (`graded_by`), for intervals
(`basis` sentences) and for provenance. Generalise it: a trace where
each value can say *measured*, *estimated by this rule*, or *declared by
the agent* is a trace whose reader never has to guess, and an analysis
over it can refuse to average the three. The failure this prevents is
the one that does the most damage in practice — a dashboard rendering a
confident zero where nothing was recorded.

**The trace records the road not taken.** Gaps 2 and 1 above are the
same idea from two sides: the alternatives at a decision, and the
context that made other actions unthinkable. A record of choices without
options is a record you cannot intervene on.

**The trace is addressable and replayable.** Content-hash the trace, the
assembled prompts and the tool results; fingerprint the environment
(gap 3); and a replay becomes a *test* — re-run the recorded decisions
against the recorded observations and see whether the same thing
happens. `agentdiff replay` already verifies a decisive step by
re-execution; with a fingerprint and hashed inputs it could verify the
whole run, and a trace would carry its own reproduction.

## The minimum informative trace

If you are instrumenting an agent today and want the most analysis per
byte, record, in this order:

1. **Steps with kind, name, tokens and a token basis** — everything
   counting depends on it, and the basis is what keeps it honest.
2. **The grade, and who graded it** — an ungraded run poisons every rate.
3. **The environment fingerprint, once per trace** (gap 3) — cheapest
   large win; without it no comparison is sound.
4. **Declared effects (read/write) and the sub-agent span** — the two
   sparse fields the integrity and horizon analyses need.
5. **Rewards if the environment pays any, values if a critic exists.**
6. **Input/output text, unless privacy forbids** — then say so, and the
   engine will say the provenance is unmeasurable rather than invent it.
7. **Context references and the alternatives at each decision** (gaps 1
   and 2) — the frontier, and the two that would change what can be
   asked rather than how precisely it can be answered.

## The honest floor

No trace saves you from one thing, and it is worth saying plainly
because everything above could be read as a promise: **if the grader was
fooled, every number computed from the trace is fooled with it.** A
compromised grader compromises each episode it graded, and no
episode-only check can see it. Held-out graders and perturbed task
variants are the answer, and both live outside the trace. That sentence
is already written into the eval's output (`docs/COEVOLVE.md`), and it
belongs here too: the most informative trace in the world is still a
record of a measurement, not a guarantee that the measurement was of the
right thing.
