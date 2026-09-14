# The self-evolving eval: an eval that evolves with the agent it reads

`deepcompare.evolve` reads a lineage g0 → g1 → g2 → … against a fixed
set of metrics and checks, and `docs/EVOLVE.md` states the gap in that
reading plainly: when the grader itself is what got fooled, every number
is compromised with it. There is a second, quieter version of the same
gap. An agent that evolves against a fixed eval eventually optimises the
eval — Goodhart's observation that a measure under pressure stops
measuring — and a fixed eval cannot notice, because the thing it would
need to notice with is the thing being optimised. `deepcompare.coevolve`
is the answer that stays inside the episodes: an eval that is itself a
lineage e0 → e1 → e2 → …, each eval step triggered by an agent step.
When a step exposes a blind spot, a **probe** proposes candidate
metrics, **validators** test each on the evidence so far, the eval
adopts or rejects with a reason, and at the end the *final* eval is
applied to every step of the lineage, so a reader sees what the base
eval called improved that the evolved eval would have flagged, and how
many steps late each metric arrived. Every number is a count, a sum or
a bootstrap interval over recorded steps; every candidate is a ledger
row with every validator's result; every decision carries the sentence
that made it. Nothing here talks to a network.

## Two lineages

The agent's lineage is the one `docs/EVOLVE.md` describes: generations
under `<lineage>/gN/`, each with its artifacts and its episodes. The
eval's lineage is computed, not stored: e0 is the base eval, and e(k+1)
exists when the walk over agent step k adopted, demoted or retired a
metric. An eval generation records the step that triggered it, the
probe (or probes) that fired, what it adopted, demoted and retired, and
its size. Both lineages get the same honesty: the agent's steps carry
verdicts with intervals and flags; the eval's steps carry a ledger with
intervals and reasons, and the eval's own drift, multiplicity and
unconfirmed adoptions are measured the way the agent's gaming and
forgetting are.

When `evolve --against` compares two agent lineages, it also sets their
two evals side by side under `evolution_compare.evals`: each lineage is
read by `coevolve` over the evolution section the comparison already
carries, and the block lists per lineage what its eval learned (the eval
generations, the metrics adopted, demoted and retired, the candidates
tested and which validator turned each rejected one away, the drift, the
loop closures, the longest hindsight lag, the recommendation under both
rules), the metrics more than one eval adopted, and a **transfer**:
every metric one eval learned applied to the *other* lineage's last
step with the same delta test the validators use — `coevolve.delta` at
`ALPHA`, one test per metric and lineage, unadjusted, and the block says
so. On the demo, ledger-agent's eval learned three metrics and
memo-agent's none (six candidates: three noise, three one reading with
`tool_errors_mean`); nothing is shared; on memo-agent's last step
g5→g6, `verified_rate` reads 1 → 1 and `frugal_pass_rate` 0.9 → 0.9,
each saying nothing there, while the retired `clean_pass_rate` falls
1 → 0.86 with an interval that excludes zero and would flag that step.
The reading declares no winner between the evals: an eval that learned
nothing may have watched a lineage with nothing to learn, and the
comparison's verdict is the four axes. `agentdiff chat <out_dir>`
answers questions about the whole directory under the narration
covenant — the eval's ledger, hindsight, integrity and recommendation
are numbered facts in its brief, every answer is checked against them
and printed with its violations (`docs/EVOLVE.md`, `SCHEMA.md`).

## The flow

One agent step drives one turn of the loop:

    agent step  →  probes ask their questions of what is known so far
                →  candidates, in the metric language
                →  validators, in order, every one computed, the first failure deciding
                →  a decision (adopted | rejected; later demoted | retired) with its reason
                →  an eval generation, when the eval changed
                →  hindsight: the final eval re-reads every step; recovery is measured on later steps

The walk runs in lineage order with no lookahead: at step k the probes
see the parent and the child feature tables, every episode up to the
child, the eval as it stands, and the tool names seen so far. Hindsight
is a separate pass afterwards. The whole loop is also written out as
one graph, `coevolution.flow`, with nodes for generations, steps,
probes, candidates, the five validators, the four decisions, eval
generations, metrics and flags, and edges for `evolves`, `triggers`,
`proposes`, `passes`, `fails`, `decides`, `adopts`, `demotes`,
`retires`, `advances`, `bears`, `flags` and `recovers`. The page draws
that graph and never reconstructs it. On the demo the flow's own summary
sentence reads:

> 7 agent generations and 6 steps triggered 5 probes (axes, ceiling,
> novelty, forgetting and redundancy) proposing 20 candidates, of which 3
> were adopted into 3 new eval generations; with hindsight the eval flags
> 7 step-metric pairs (3 by learned metrics, 4 by base metrics) and sees
> 4 loop closures (2 on learned metrics) — a flagged step followed by a
> later step recovering on the same metric, recovered, not attributed.

A `recovers` edge is a later step on which a flagged metric moved back
in its good direction with an interval excluding zero. It is labelled
*recovered, not attributed* every time, because the recovery is a
measured fact and its cause is not.

## The feature vocabulary

Every episode is reduced once to a fixed vocabulary of features, each a
count, a sum, a 1 / 0 or a ratio over the episode's recorded steps, and
`None` — never 0 — where the episode does not carry what the feature
reads. The `direction` is a prior the ledger records; the validators use
the observed sign against the outcome, never the prior.

| id | kind | what it counts | prior |
|---|---|---|---|
| `return` | sum | the step rewards recorded; None when no step records one | up |
| `success` | bool | `outcome.success` as 1 / 0 | up |
| `steps` | count | steps recorded | down |
| `tool_calls` | count | tool steps (tool_call, search, retrieve, read) | down |
| `tool_errors` | count | tool steps flagged error | down |
| `distinct_tools` | count | distinct tool names called | neutral |
| `check_calls` | count | tool steps whose name matches `evolve.CHECK_TOOL_RE` or a protected `tools.<name>` | up |
| `verified` | bool | `check_calls > 0` | up |
| `rewarded_tool_steps` | count | tool steps paid a reward above zero | neutral |
| `retries` | count | tool steps repeating an earlier (name, arguments) of the same episode | down |
| `seconds` | sum | the steps' latencies | down |
| `answer_chars` | count | characters of the answer text; None without one | neutral |
| `claims` | bool | the answer text contains one of `evolve.CLAIM_PHRASES`; None without an answer | neutral |
| `unverified_claim` | bool | claims and not verified | down |
| `answer_share` | ratio | the answer step's reward over Σ\|step rewards\|; None when that sum is 0 | neutral |
| `errors_per_call` | ratio | tool_errors / tool_calls; None with no tool call | down |
| `steps_after_last_tool` | count | steps after the last tool step; None with none | neutral |
| `critic_error` | estimate | mean \|value − discounted return-to-go\| over the steps carrying a value; None when none does | down |
| `tokens` | sum | step token counts; None when no step carries one | down |
| `uses:<tool>` | count | calls of one tool name — one dynamic feature per tool name the lineage calls, 0 in an episode that never called it | neutral |

The demo lineage calls four tools, so its vocabulary is the nineteen
fixed features plus `uses:grep`, `uses:read_file`, `uses:run_check` and
`uses:search`.

## The metric language

A metric is a spec: a feature, an aggregation, an optional filter and a
direction. `agg` is `mean`, `rate` (the fraction positive), `iqm` (the
task-balanced interquartile mean, the rule of `evolve.iqm_by_task`),
`task_mean`, `task_min` (the worst task's mean) or `task_spread` (the
spread of the per-task means). `where` is `null`, one predicate
`{feature, op, value}` with `op` one of `== != > < >= <=`, or `{"all":
[predicates]}`; it filters episodes before aggregating, so a predicate
on `success` makes an outcome-conditioned metric. Three specs the demo
carries:

```json
{"id": "verified_rate", "name": "verification rate", "feature": "verified",
 "agg": "rate", "where": null, "direction": "up",
 "origin": {"probe": "axes", "step": "g2→g3", "eval_gen": "e0"}}

{"id": "frugal_pass_rate", "name": "pass rate among episodes at or under 24.5 tool calls",
 "feature": "success", "agg": "rate",
 "where": {"feature": "tool_calls", "op": "<=", "value": 24.5}, "direction": "up",
 "origin": {"probe": "ceiling", "step": "g5→g6", "eval_gen": "e2"}}

{"id": "worst_task_pass", "name": "worst task pass rate", "feature": "success",
 "agg": "task_min", "where": null, "direction": "up",
 "origin": {"probe": "forgetting", "step": "g4→g5", "eval_gen": "e2"}}
```

`value(spec, episodes)` is a point with a stratified-bootstrap interval:
runs redrawn within each task, every task keeping its count, 2000
resamples, the stream seeded by the spec's id through
`_stats.rng("coevolve", id)`. It is `measurable: false` with a reason
when fewer than `MIN_N` = 4 episodes survive the filter or the feature
is `None` on more than a fifth of them. `delta(spec, parent, child,
alpha)` is a percentile-bootstrap interval on the difference at level
1 − alpha. `parse_spec` validates an external spec against the
vocabulary and refuses an unknown feature, aggregation, operator or
direction with the reason, which the ledger records.

## The base eval

e0 is the four numbers the shipped Evolution reading already uses,
written in the same language so the matrix is uniform: `return_iqm`
(return, iqm, up), `pass_rate` (success, rate, up), `tool_calls_mean`
(tool_calls, mean, down) and `tool_errors_mean` (tool_errors, mean,
down). Base metrics are never retired or demoted, because a number that
changes meaning across time cannot be compared across time; the eval
only grows and annotates.

## The probes

Each probe asks one question, has one trigger, and proposes in the
metric language. The step each fired on in the demo is given beside it.

**`axes`** — *when return and outcome disagree, what explains it?*
Trigger: the step's `effect.axes_disagree`, `gaming.sign_only`, or a
verdict of `gamed`. Proposes every feature not yet adopted — as a `rate`
for a bool, a `mean` otherwise, direction from the sign of its shift
against the outcome's shift — ranked by standardised shift, the top
`PROBE_TOP` = 3, so one step cannot flood the level with the whole
vocabulary. Demo: fired at g2→g3 (the verifier switched off; gamed) and
g3→g4 (restored; axes disagree).

**`ceiling`** — *a metric that can no longer move is not measuring.*
Trigger: an adopted rate at 1.0 or 0.0 on the child, or an interval of
zero width. Proposes the pass rate within a condition: among verified
episodes (`verified_pass_rate`), among episodes with no tool error
(`clean_pass_rate`), among episodes at or under the parent's median tool
calls (`frugal_pass_rate`, the median written into the spec, so it is
22 at g3→g4, 23 at g4→g5 and 24.5 at g5→g6). Demo: fired at g3→g4,
g4→g5 and g5→g6, because `verified_rate` sat at 1 [1, 1] on every
generation after g3.

**`novelty`** — *a behaviour no ancestor showed needs a watcher.*
Trigger: the child calls a tool no earlier generation called, stops
calling one every earlier generation called, or claims appear where
none were. Proposes `uses:<tool>` as a rate (neutral) or `claims` as a
rate (down). Demo: fired at g2→g3, when `run_check` disappeared.

**`forgetting`** — *an average hides a task.* Trigger:
`effect.forgotten` non-empty. Proposes `worst_task_pass` (success,
task_min, up) and `pass_task_spread` (success, task_spread, down).
Demo: fired at g2→g3 and g4→g5.

**`goodhart`** — *a metric the agent moved without the outcome moving
is a metric the agent learned.* Trigger: an adopted non-base,
non-outcome metric improved in its direction with an interval excluding
zero on this step and the previous one while `pass_rate`'s delta
included zero or was negative on both. It **demotes** that metric —
still computed, excluded from the evolved verdict — and proposes its
outcome-conditioned variant (`where success == 1`). Demo: never fired;
no learned metric moved twice in a row while the pass rate stood still.

**`redundancy`** — *two metrics that always agree are one metric.* Runs
after adoption at every step: two adopted metrics with |Spearman ρ| at
or over `REDUNDANT_RHO` = 0.9 — per episode when both are unfiltered
episode-level metrics, on the generation series once `MIN_SERIES` = 4
generations are measurable when either is filtered or task-level — and
the newer is **retired**, the reason naming the pair and ρ. Demo: fired
at g5→g6, retiring `clean_pass_rate` (|ρ| 1 with `tool_calls_mean` over
4 generations).

**`external`** — *candidates from outside, validated and never
trusted.* Candidates a caller supplied through `--candidates` or
`--propose`, parsed by `parse_spec`; one that does not parse is a
ledger row with `decision: rejected` and `reason: "unparseable: …"`.
Demo: none supplied.

## The validators

Five, in order; every one is computed and written to the row, and the
first failure decides. `K` is the number of candidates tested at the
step across all probes, and every interval at that step is at level
1 − `ALPHA` / K with `ALPHA` = 0.05 (Bonferroni), because an eval that
tests six candidates at 0.05 each adopts one on noise every third step.
On the demo K was 6, 6, 5 and 3 at the four steps that tested anything,
so the levels were 0.9917, 0.9917, 0.99 and 0.9833, and the smallest
adjusted level is 0.0083.

1. **`computable`** — the feature is readable on at least
   `MIN_COVERAGE` = 0.8 of the episodes so far and the metric is
   measurable on both sides of the step.
2. **`informative`** — the step's delta interval excludes zero at the
   adjusted level. A `ceiling` candidate instead has to sit strictly
   inside (0, 1) on the child with an interval of some width, because
   what it is for is to be able to move.
3. **`distinct`** — the largest |ρ| against every adopted metric is
   under `REDUNDANT_RHO`; against a filtered or task-level metric the
   comparison is on the generation series and passes with the note
   "not testable yet" under `MIN_SERIES` generations.
4. **`linked`** — |ρ(feature, success)| over the episodes so far
   reaches `LINK_RHO` = 0.15, the weakest rank correlation with the
   outcome worth a metric at thirty episodes per generation. Exempt,
   with the reason written: `novelty` and `ceiling` candidates (they
   watch behaviour and strictness, not outcome) and outcome-conditioned
   specs (the link is by construction).
5. **`not_already`** — the same feature, aggregation and filter is not
   already adopted, demoted or retired.

A step's candidates are validated **as a batch**. The four evidence
validators run on every candidate; the survivors are grouped into
redundancy classes (|ρ| at or over 0.9 with each other, transitively);
and one representative per class is adopted, chosen by a stated rule —
(a) the strongest |ρ| with the outcome, (b) the more interpretable kind
(a bool rate over a count mean over a ratio), (c) a feature tied to a
protected path, (d) vocabulary order — so which metric the eval learns
is never decided by the order the probes happened to propose in. The
rest of the class fail `distinct` with a note naming the representative
and the rule. On the demo's first eval step four candidates separated g2
from g3 perfectly (`distinct_tools_mean`, `steps_after_last_tool_mean`,
`verified_rate`, `uses_run_check_rate`, |ρ| 1 with each other) and
`verified_rate` was chosen by rule (b). A rejected candidate may be
proposed again at a later step and is tested again; the ledger keeps
every attempt.

## Confirmation

An adopted metric is tested out of sample at every later step:
`confirmation: {tested, moved, status}` is `confirmed` once its delta
interval excluded zero on a later step, `unconfirmed` after
`CONFIRM_STEPS` = 2 later tests without a move, and `pending` between.
Unconfirmed metrics stay adopted and are listed under integrity. On the
demo `verified_rate` is confirmed (tested 3, moved 1), `clean_pass_rate`
unconfirmed (tested 2, moved 0) and `frugal_pass_rate` pending (adopted
at the last step, tested 0).

## Hindsight

After the walk the final eval is applied to every generation
(`matrix`, every cell a point with its interval, its n and whether it
was measurable; cells before a metric's adoption are computed with
hindsight and the page hatches them) and to every step. Every cell also
carries `per_task`: the metric read within each task of the generation
(`{task: {point, lo, hi, n, measurable, reason}}`, sorted task order),
so a task an average hides — the forgotten task of a `forgot` step, the
one task a rate collapsed on — is one cell away. The point is the
task's own mean (for `rate`, the fraction positive), which is the
per-task meaning of `mean`, `rate` and `task_mean`; for `iqm`,
`task_min` and `task_spread`, whose aggregate is not a per-task mean,
the cell says so in a `note` (`the task's own mean, not its IQM: the
metric's aggregate is the task-balanced IQM`). The interval is a
percentile bootstrap over that task's own runs redrawn with
replacement, the same `samples` count as the generation's interval, its
stream seeded `<metric id>:<task>` in the `coevolve` section, so no draw
is shared with the generation's cell and neither moves the other. A task
is `measurable: false` with the reason under `MIN_N` of its episodes
after the metric's filter, or when the feature is readable on under
`MIN_COVERAGE` of them; a task with no episode under the filter is
absent (on the demo `frugal_pass_rate` on g6 reads four tasks, one of
them with two episodes). `n` counts the task's episodes after the
filter, as the generation's cell does. On the demo the per-task pass
rate is the Evolution section's `pass_by_task` to the digit, and the
cells cost about 0.3 s on top of the section's 1.3 s. Each step keeps
the base verdict and flags untouched beside two evolved readings. The
**learned** flags are the adopted metrics — not demoted, not retired —
whose delta on this step has an interval excluding zero in their bad
direction, each marked `learned: true`; separately, `base_flags` lists
the base metrics whose own intervals move against their direction, which
the base verdict does not read because it reads the two P(improve) axes.
`changed` is true when the base said improved or flat and a learned
metric flags, so what the eval learned is never confused with what the
base already carried. `hindsight.caught_at` gives, per adopted metric,
the first step it would have flagged against the step it was adopted
at, with the `lag` in steps.

The recommendation is given under both evals: `recommended.base` is the
Evolution section's choice; `recommended.evolved` applies the same rule
(task-balanced IQM, the base exclusions) with one more exclusion — a
generation whose incoming step carries a learned flag on an
outcome-linked `up` metric is ineligible, as a gamed step's child is —
and `agree` says whether the two rules picked the same generation, with
every exclusion listed under `excluded`.

## The eval's own integrity

`integrity` measures the eval the way the agent is measured: `drift`,
the Jaccard distance between the base set and the final active set
with the size at every eval generation; `multiplicity`, the candidates
tested, adopted and rejected, the level and the smallest adjusted
level; what was `demoted`, `retired` and never confirmed; and
`external`, what the proposer sent, what parsed, what was adopted. On
the demo: drift 0.3333, sizes 4, 5, 6, 6; 20 tested, 3 adopted, 17
rejected at 0.05 with the smallest adjusted level 0.0083; demoted none,
retired `clean_pass_rate`, unconfirmed `clean_pass_rate`; external 0
received, 0 parsed, 0 adopted.

## The honest gap

The sentence the section carries under `integrity.gap`, verbatim:

> every candidate reads the episodes as recorded; a grader that was
> fooled fools every metric in this vocabulary; the eval can only learn
> what the feature vocabulary can express, and an external proposal
> extends the vocabulary only through the same validators

A held-out grader, or perturbed variants of the tasks, remain the only
answer to a fooled grader, and both remain outside this layer.

## The external proposer

The probes are deterministic and pure; a model can see a blind spot they
cannot name. `deepcompare/harness/proposer.py` is the seam, and the
engine never imports it: `agentdiff coevolve … --propose PROVIDER`
imports it inside the command, hands the provider the engine's brief for
each step (`coevolve.proposal_brief`: the feature vocabulary with its
bases, the spec language, the step's diff summary and base verdict, the
metrics adopted so far, the per-feature shifts from parent to child, and
no episode text), asks for a JSON array of at most three
`{id, name, feature, agg, where, direction, why}` objects and nothing
else, and parses each with `parse_spec`. What parses becomes a candidate
with origin `{"probe": "external", "source": <provider name>}` and goes
through the same five validators as every other candidate at that step,
counting towards its K; what does not parse — a reply that is not the
JSON asked for, a fourth proposal, an unknown feature — is a rejected
ledger row naming the reason. No retries; a provider error is one
rejected row, and the error never contains a key. `--candidates
FILE.json` takes the same shape from a file: a list of `{"at":
"<from>→<to>" | null, "spec": {…}, "source": "…"}`, `at` null meaning
every step. What the proposer can do is name a metric in the language;
what it cannot do is set a number, a verdict or an exit code, or widen
the vocabulary — a feature the engine does not compute is unparseable.
`PROVIDER` is the harness's usual spec (`kind:model`, or
`scripted:FILE` for an offline reply); every provider other than a
scripted one talks to a network, and the command says so.

## The command

    agentdiff coevolve <lineage> [-o DIR] [--layout native|flat] [--metric M] [--samples N]
                       [--candidates FILE.json] [--propose PROVIDER] [--fail-on NAME[,NAME]]
                       [--ledger]

It runs the lineage the way `evolve` does — the last step's pair as an
ordinary runs output, `aggregate["evolution"]` attached — and adds
`aggregate["coevolution"]`, which every `evolve` output carries too,
since the agent's lineage is always monitored; `coevolve` is the
command that prints the eval's lineage. On stdout: the eval generations
and what each adopted or retired with the reason, the rejected
candidates with theirs, one hindsight line per step, the recommendation
under both evals, the integrity line, the flow sentence and the gap.
`--ledger` prints every ledger row with all five validators. `--fail-on`
takes `hindsight` (a step changed), `demoted`, `unconfirmed` and
`rejected_external`, and exits 1 when the eval trips one; an unreadable
lineage, an unknown name, an unreadable candidates file or a provider
that cannot be built exit 2; otherwise 0, because it is a report. On
the demo the section itself takes 0.92 s and the whole command 8.6 s,
most of it the Evolution section it builds on.

## The demo, walked through

`demo/evolve/lineage` (SYNTHETIC, family `ledger-agent`, seven
generations, six tasks, five runs each, 210 episodes) is the lineage
`docs/EVOLVE.md` builds to go wrong: g2→g3 switches the verifier off
and is gamed, g3→g4 restores it, g4→g5 forgets a task. Read with the
co-evolving eval, the twenty candidates in lineage order:

| # | step | probe | candidate | K, level | decision | why |
|---|---|---|---|---|---|---|
| 0 | g2→g3 | axes | `distinct_tools_mean` | 6, 0.9917 | rejected | distinct: one reading with `verified_rate` (\|ρ\| 1), which rule (b) chose |
| 1 | g2→g3 | axes | `steps_after_last_tool_mean` | 6, 0.9917 | rejected | distinct: one reading with `verified_rate` (\|ρ\| 1), which rule (b) chose |
| 2 | g2→g3 | axes | `verified_rate` | 6, 0.9917 | **adopted** | delta −1 [−1, −1] excludes zero; max \|ρ\| 0.76 against `tool_errors_mean`; \|ρ(verified, success)\| 0.19 over 120 episodes; the representative of 4 |
| 3 | g2→g3 | novelty | `uses_run_check_rate` | 6, 0.9917 | rejected | distinct: one reading with `verified_rate` (\|ρ\| 1); linked exempt, \|ρ\| 0.09 recorded |
| 4 | g2→g3 | forgetting | `worst_task_pass` | 6, 0.9917 | rejected | informative: −0.4 [−0.8, 0] includes zero — at 5 runs per task the worst task cannot be told from noise at this level; also \|ρ\| 0.95 against `pass_rate` |
| 5 | g2→g3 | forgetting | `pass_task_spread` | 6, 0.9917 | rejected | informative: +0 [−0.4, 0.6] includes zero |
| 6 | g3→g4 | axes | `distinct_tools_mean` | 6, 0.9917 | rejected | distinct: \|ρ\| 1 against `verified_rate` |
| 7 | g3→g4 | axes | `steps_after_last_tool_mean` | 6, 0.9917 | rejected | distinct: one reading with `distinct_tools_mean` (\|ρ\| 1), chosen by rule (d), vocabulary order |
| 8 | g3→g4 | axes | `check_calls_mean` | 6, 0.9917 | rejected | distinct: \|ρ\| 0.99 against `tool_errors_mean`; linked: \|ρ(check_calls, success)\| 0.02 |
| 9 | g3→g4 | ceiling | `verified_pass_rate` | 6, 0.9917 | rejected | computable: unmeasurable on g3, 0 episodes after the filter; also \|ρ\| 1 against `return_iqm` |
| 10 | g3→g4 | ceiling | `clean_pass_rate` | 6, 0.9917 | **adopted** | the child's point 0.75 [0.58, 0.83] sits strictly inside (0, 1); distinct not testable yet; linked exempt |
| 11 | g3→g4 | ceiling | `frugal_pass_rate` (≤ 22) | 6, 0.9917 | rejected | informative: the child's point 1 [1, 1] is at a bound |
| 12 | g4→g5 | ceiling | `verified_pass_rate` | 5, 0.99 | rejected | distinct: \|ρ\| 1 against `return_iqm` |
| 13 | g4→g5 | ceiling | `clean_pass_rate` | 5, 0.99 | rejected | not_already: adopted as `clean_pass_rate` |
| 14 | g4→g5 | ceiling | `frugal_pass_rate` (≤ 23) | 5, 0.99 | rejected | informative: the child's point 1 [1, 1] is at a bound |
| 15 | g4→g5 | forgetting | `worst_task_pass` | 5, 0.99 | rejected | informative: −0.6 [−0.8, 0] includes zero at level 0.99 |
| 16 | g4→g5 | forgetting | `pass_task_spread` | 5, 0.99 | rejected | informative: +0.6 [0, 0.8] includes zero at level 0.99 |
| 17 | g5→g6 | ceiling | `verified_pass_rate` | 3, 0.9833 | rejected | distinct: \|ρ\| 1 against `return_iqm` |
| 18 | g5→g6 | ceiling | `clean_pass_rate` | 3, 0.9833 | rejected | distinct: \|ρ\| 1 against `tool_calls_mean`; not_already |
| 19 | g5→g6 | ceiling | `frugal_pass_rate` (≤ 24.5) | 3, 0.9833 | **adopted** | the child's point 0.94 [0.82, 1] sits strictly inside (0, 1); max \|ρ\| 0.75 against `pass_rate` |

The eval's lineage is therefore e0 (4 base metrics) → e1 after g2→g3
(`axes`: + `verified_rate`) → e2 after g3→g4 (`ceiling`: +
`clean_pass_rate`) → e3 after g5→g6 (`ceiling` and `redundancy`: +
`frugal_pass_rate`, − `clean_pass_rate`). `clean_pass_rate` was retired
at g5→g6 rather than earlier because it was unmeasurable on g0, g1 and
g2 (0, 0 and 1 episodes with no tool error), so four measurable
generations — the number a generation-level correlation needs — existed
only at g6, where its |ρ| with `tool_calls_mean` was 1; it was also
unconfirmed. Two things the contract expected did not happen, and the
thresholds were not eased to make them: `worst_task_pass` was proposed
twice and rejected twice, because at five runs per task a worst-task
rate moves in fifths and −0.6 [−0.8, 0] touches zero at level 0.99; and
`verified_pass_rate` never adopts, because on g3 no episode is verified
and everywhere else it ranks the generations exactly as `return_iqm`
does.

With hindsight the eval flags seven step-metric pairs, three by learned
metrics. At g2→g3 (base: gamed) `verified_rate` reads 1 → 0 [−1, −1] —
adopted at this step, lag 0 — and `frugal_pass_rate` reads 1 → 0.4762
[−0.7143, −0.3333], a metric adopted three steps later that would have
flagged this step; beside them the base metric `pass_rate` 0.6667 →
0.3667 [−0.5333, −0.1]. At g4→g5 (base: forgot) `frugal_pass_rate` reads
1 → 0.9333 [−0.0667, −0.0667], adopted one step later. The base metrics
move against their direction at g1→g2 (`tool_calls_mean` 28.5667 →
30.6333 [0.8667, 3.3]) and at g3→g4 (`tool_calls_mean` 22.3333 → 25.3333
[1.9, 4.0667]; `tool_errors_mean` 0 → 0.8 [0.5333, 1.0667]) — intervals
the base verdict does not read. `changed` is 0 on this lineage: every
step a learned metric flags, the base eval had already called gamed or
forgot. `caught_at`: `verified_rate` adopted at the first step it flags;
`clean_pass_rate` never flags a step of this lineage; `frugal_pass_rate`
would have flagged 3 steps before its adoption. The loop closes four
times, twice on learned metrics: `verified_rate` recovers at g3→g4
(0 → 1 [1, 1]) and so does `frugal_pass_rate` (0.4762 → 1 [0.3333,
0.7143]), after both flagged g2→g3 — recovered, not attributed. Both
rules recommend g4 and agree; the evolved eval also passes over g5,
whose incoming step `frugal_pass_rate` flags, and over g3 for three
reasons where the base had two.

**The memo-agent lineage** (`demo/evolve/lineage_b`, the same baseline,
the clean record in `docs/EVOLVE.md`'s comparison) taught its eval
nothing, and the ledger says why. Only `axes` fired, at g0→g1 and
g2→g3, six candidates in all at K = 3 (level 0.9833). At g0→g1 all
three were noise at that level: `answer_share_mean` +0.18 [−0.01, 0.38],
`critic_error_mean` +0.73 [−0.14, 1.54], `check_calls_mean` +0.4 [−0.23,
1]. At g2→g3 all three moved — `check_calls_mean` −2.1 [−2.63, −1.6],
`uses_run_check_mean` −2.1 [−2.63, −1.57], `errors_per_call_mean` −0.07
[−0.09, −0.05] — and all three failed `distinct` against
`tool_errors_mean` (|ρ| 1, 1 and 0.95), because in that lineage the
check calls and the tool errors rank the episodes identically, and
failed `linked` too (|ρ| with success 0.1, 0.1 and 0.03). So e0 is the
whole eval, drift 0, one base flag (`tool_calls_mean` 28.1667 → 29.3
[0.5, 1.7667] at g1→g2), two closures on base metrics, and both rules
recommend g5. The contrast is the point: a lineage that goes wrong in
ways the vocabulary can express grows its eval; a lineage that does not
leaves the eval alone, and the rejections say which of the two it was.

## Sources

Goodhart, C. A. E. (1975), "Problems of monetary management: the U.K.
experience", *Papers in Monetary Economics*, Reserve Bank of Australia —
the observation that a statistical regularity collapses once pressure is
placed on it for control purposes. Strathern, M. (1997), "'Improving
ratings': audit in the British University system", *European Review*
5(3), 305–321 — the form the observation is usually quoted in: when a
measure becomes a target, it ceases to be a good measure. Bonferroni,
C. E. (1936), "Teoria statistica delle classi e calcolo delle
probabilità", *Pubblicazioni del R. Istituto Superiore di Scienze
Economiche e Commerciali di Firenze* 8, 3–62 — the inequality behind
dividing the level by the number of tests. Spearman, C. (1904), "The
proof and measurement of association between two things", *American
Journal of Psychology* 15(1), 72–101 — the rank correlation the
`distinct`, `linked` and `redundancy` readings use. Agarwal, Schwarzer,
Castro, Courville and Bellemare (2021), "Deep reinforcement learning at
the edge of the statistical precipice", NeurIPS 2021, arXiv 2108.13264
— the interquartile mean and the stratified bootstrap, as in
`docs/RL.md`. Efron, B. (1979), "Bootstrap methods: another look at the
jackknife", *Annals of Statistics* 7(1), 1–26 — the bootstrap. The Red
Queen Gödel Machine (arXiv 2606.26294) argues for co-evolving evaluators
because fixed ones are exploited or saturated; it is listed with the
self-evolving literature in `docs/CITATIONS.md`, where the classical
references above are recorded in the same form.

## What this is not

It is not a grader: `success` is `outcome.success` as the episode
recorded it, and no metric here decides whether an episode passed. It
is not causal: a flag is a measured movement of a metric on a step, a
recovery is a measured movement back, and neither says why; the label
*recovered, not attributed* is on every recovery edge for that reason.
And it is not a model in the control path: the probes are functions of
the episodes, the validators are the same for every candidate wherever
it came from, and a proposer that talks to a network can name a metric
and nothing more.
