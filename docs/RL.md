# The run as an episode: reward, return and credit

The impact layer weighs a run by its faults; a policy under training is
read by what it *earned*. `deepcompare.rl` reads the same traces that
way — reward per step, return so far and to go, credit per stretch — in
the impact layer's shape (clusters that fold, marks that drill down), so
an episode sits beside the fault-weighted view with one vocabulary.
Every number is a count or a sum over recorded or labelled steps;
nothing is simulated, and an estimate says so.

## What it reads

- **Recorded rewards.** A step may carry `reward`, `value` and `advantage`
  (SCHEMA.md Step; optional numbers). When any step of either run carries
  `reward` the pair is `source: "recorded"`: the return is the sum of what
  the environment paid, a step without a reward earned 0, and the discounted
  return uses γ = 0.99 over step order (`to_go[i] = r[i] + γ × to_go[i+1]`).
- **Shaped rewards.** When no step carries one, the pair is
  `source: "shaped"`: a sparse reward is derived from the labels
  `feedback.step_labels` gives every step, weighted by `rl.SHAPED_WEIGHTS`
  (a step with several labels sums them): `fault_enters` −3,
  `wrong_answer` −5 (on the answer step, in place of the outcome reward),
  `error` −1, `invented_argument` −1, `repeat` −0.5, `spent_after_basis`
  −0.2, `dead_end` −0.1, `no_information` −0.1, `fed_answer` +1; the
  answer +5 on success (a failed answer the report did not label pays −5
  from the outcome); each milestone reached (`--golden`) +2 at its step.
  The small weights keep a long run's unproductive steps from drowning
  the answer and the decisive step (`demo/horizon/long`: atlas-lh −50.6
  over 545 steps, comet-lh −72.9). A shaped return is a reading, not a
  measurement; it compares only against runs read the same way, and
  every place it appears says `shaped`.
- **Credit** from the Shapley section (`report.shapley`, tokens by
  default): each divergence region's allocation is spread evenly over
  the side's steps in that region's alignment rows — positive on the
  winner's steps, the loser's carry the negative. Shapley prices *cost*,
  so a winner whose path spent more tokens carries negative credit;
  the unit is always named. `credit.source` is null without it.
- **Preference**: `feedback.preference_pair(report)` unchanged — the
  passing run (or the reconciled splice, an estimate) over the failing one.

## The contract

`report["rl"] = {version: 1, measurable, source: recorded|shaped, gamma, a, b, preference, narrative}`; each side:

```
{agent, measurable, source, steps, return, discounted_return, positive, negative, zero, seconds,
 rewards[{step, reward, cum, to_go, discounted_to_go, credit|null, labels[], agent, kind, name,
          value|null, advantage|null, why}],
 largest[{step, reward, why}],                       # top 5 by |reward|; why = the labels, else "recorded"
 credit {source: shapley|null, metric, top[{step, credit}], total},
 clusters[...],                                      # impact's shape, see below
 narrative}
```

Clusters reuse `impact.cluster_steps` (lanes from `step.span`, phase and
framing boundaries, quiet merging, splitting, coalescing) but score
`Σ|reward| + Σ|credit| + 2 per fault_enters / wrong_answer label`;
`impact` is the score over the pair's largest cluster score (max 1.0);
`reasons` gains `reward`, `positive`, `negative`, `credit`, `fault_labels`;
marks are the steps whose `|reward|` reaches the run's 90th percentile
(`reward+` / `reward−`), the decisive step and the answer. Narratives
quote the numbers — *"comet-lh: return −72.9 over 566 steps (shaped): 417
negative steps, 1 positive; the largest penalty at step 565 (−5, wrong
answer); credit −1,230 tokens on 39 steps, most in
migrator-ledger.tests."* — and the pair's says who earned more, the first
step where the cumulative returns part by ≥ 1, the credit split and the
preference. `attach_milestones` recomputes the section (+2 per milestone).

**The runs layout** (`runs DIR`) adds `aggregate["rl"]`:

```
{version, gamma, source: recorded|shaped|mixed,
 agents {name: {episodes[{task_id, run_id, return, discounted_return, steps, success, source,
                          rewards[], cum[], values[], advantages[], events{label: n},
                          tools{name: calls}, distinct_inputs, seconds}],
                mean_return, return_ci [lo, hi] | null, episodes_n}},
 tasks {task_id: {agent: {mean_return, returns[]}, delta, sign}},   # delta = B − A in the aggregate's order
 pairs[{task_id, source, returns{}, credit{}}], preferences[{task_id, chosen, rejected, basis, margin, diverges_at}],
 narrative}
```

One episode per trace (`rl_run_from_trace`: recorded, else shaped from
the trace's own reading — the pair-only labels need a report);
`advantages` is the recorded advantage, else `discounted_to_go − value`
where a value exists, else null; `return_ci` is a normal-approximation
95% interval, null under two episodes.

## Recording rewards

```python
with Recorder(task="t1", prompt=prompt, agent="policy-v2", run_id="r1", out_dir="traces") as run:
    run.plan("Plan …", value=3.2)                       # a value estimate on a thinking step
    run.tool("search", {"q": "…"}, output=rows, reward=-0.1)
    run.read("row 12", output=evidence, reward=0.9)      # −0.1 tool cost, +1 evidence
    run.answer("42", success=True, reward=5.0)
```

Each field is written only when given; absent means unrecorded, never 0 paid.

## The demo and the CLI

`python demo/rl/generate_rl.py` writes `demo/rl/traces/` (SYNTHETIC, said
so in `harness.note`): two tasks × `policy-v1` (weaker) / `policy-v2`
(stronger) × three runs; a reward on every step (−0.1 per tool call, −1
on an error, +1 per piece of evidence, ±5 at the answer), a value on each
thinking step, a `verifier` lane. As generated, policy-v2's mean return
is 5.23 over six episodes against −2.47 for policy-v1. `python -m
deepcompare rl demo/rl/traces` prints each episode (return, discounted
return, reward counts, largest rewards) and the per-agent mean with its
interval (`--json` for the episodes); `runs` and `batch` carry the section.

## What a training loop consumes

`feedback.to_jsonl` already writes the preference pairs (prompt, chosen,
rejected) a preference-optimisation loader reads. `rl` adds the *dense*
side: per-step rewards (recorded, or shaped from the labels the pairs rest
on), returns-to-go as targets for a value head, `advantages` where values
were recorded, and per-episode returns with intervals across runs. Take
`preference` for the pairwise loop and `rewards[]` / `cum[]` /
`advantages[]` for the per-step one; `source` says which is a measurement.

## The bridge to trainers

The other direction is `rlexport`: `python -m deepcompare rlexport <reports_dir|report.json> --format verl-rewards |
verl-reward-fn | preferences | agent-lightning -o <path>` writes what this section computed in the shapes a trainer
reads — one reward record per trajectory (`trajectory_id`, `reward` = the return above, `reward_terms` by label,
`step_rewards`, `milestones` reached, `outcome`) for a veRL reward manager, the `compute_score(data_source,
solution_str, ground_truth, extra_info)` template that serves those records by `extra_info["trajectory_id"]`,
DPO-style pairs, and per-step `(state, action, reward, next_state, done)` transitions for Agent Lightning; every
record carries the same `source` (`recorded` / `shaped`). Coming in, `convert --format verl | agent-lightning` reads a
veRL agent-loop rollout or an Agent Lightning span / event export into traces whose steps carry the trainer's own
rewards, so this section reads them as *recorded*. `docs/FRAMEWORKS.md` §6 says what each trainer logs and where
this project sits relative to one.

## Comparing two policies from few episodes

A mean return with a normal-approximation interval is the wrong instrument
for the sample a runs layout actually has. With three runs per task the
mean is dragged wherever the luckiest or unluckiest episode went, and the
normal approximation assumes a sample size nobody running agents has.
Agarwal, Schwarzer, Castro, Courville and Bellemare showed in 2021 how far
that goes wrong in published deep-RL results — comparisons drawn from a
handful of runs routinely reverse when the runs are redrawn — and set out
the alternative this project implements in `deepcompare/rlstats.py`, from
scratch and in the standard library, over the same episodes `rl_aggregate`
already builds. It lands at `aggregate["rl"]["stats"]`.

### What it computes

- **IQM**, the interquartile mean: sort every run's score, drop
  `int(n × 0.25)` from each end, average what is left. It ignores the one
  exceptional episode the mean chases while keeping far more of the sample
  than the median does, which is why it leads the block rather than the
  mean. At three runs nothing is cut and the IQM *is* the mean; the output
  says so rather than pretending otherwise.
- **median** and **mean**, reported beside it so the reader can see how
  much each disagrees.
- the **optimality gap**: `mean(max(0, target − score))`, the average
  shortfall against a stated target. A run at or past the target
  contributes 0, so the gap is never negative and one exceptional episode
  cannot buy a policy out of a failure. The target is configurable and
  defaults to the best score any run of any policy actually reached; the
  block names it and says which it used, because a gap against an unstated
  ceiling means nothing.
- a **stratified bootstrap** interval on all four. Tasks are the strata:
  runs are exchangeable *within* a task and not across tasks, so a resample
  redraws each task's runs with replacement and keeps every task's own run
  count. Pooling the runs first would quietly assume a run on one task
  could have landed on another. 2000 resamples, a percentile interval at
  95%, one fixed seed and tasks visited in sorted order — so the whole
  section is byte-identical run to run.
- the **performance profile**: the fraction of runs scoring at least τ, for
  every τ on a shared grid, with the bootstrap band. A distribution rather
  than a point. Where one curve sits at or above the other at every τ the
  ordering holds at every threshold and the block says so; where they cross
  the block names the τ values, because a crossing means the answer depends
  on where the bar is set.
- the **probability of improvement**, P(B > A): per task, every run of B is
  compared against every run of A with a tie counting a half (a
  Mann-Whitney statistic scaled to a probability), and the tasks are then
  averaged. One task cannot dominate by having more runs, and one enormous
  episode cannot win more than one comparison. Tasks only one policy ran
  are excluded and named. This is not the same question as "whose mean is
  higher", and on small samples the two regularly disagree.

The score is selectable — `return` (the default), `discounted_return`,
`success` as 1/0, `steps`, `seconds` — because the same machinery answers
"is it better" for any of them; `steps` and `seconds` are read as
lower-is-better, which flips the gap, the profile's direction and the
comparison inside the probability of improvement.

### What the interval is, and is not

Every interval here is a bootstrap **over the runs that were recorded**. It
says how far this sample's statistic moves when these runs are redrawn. It
is not a confidence statement about a population of runs nobody made, and
at three runs per task it will be wide. The section therefore carries the
reliability layer's own runs advisory (`rlstats.sample_advisory`) plus a
sentence saying what a bootstrap over so few runs can and cannot support,
and the block renders that advisory under every figure. An overlap is read
as *these runs do not separate the policies* — never as *the policies are
equal*. Degenerate shapes say so instead of returning a number: one run per
task gives an interval of no width and is labelled `degenerate` with the
reason; all-equal scores likewise; a policy missing a task simply does not
have that task among its strata; fewer than two policies leaves the
profile comparison and the probability of improvement unmeasurable with a
reason attached.

### The demos

`demo/rl/traces` (12 episodes, 2 policies × 2 tasks × 3 runs) is the
small-sample case, and it is instructive: policy-v2's IQM return is 6.8
[1.8, 7.65] against policy-v1's −3.25 [−6.7, 2.55], and those intervals
**overlap**. Three runs per task cannot separate the policies even though
the means are 5.23 and −2.47 and the preference pairs all point one way.
P(policy-v2 > policy-v1) is 94% [78%, 100%] over the two shared tasks —
an interval that reaches the ceiling, which is what a two-task bootstrap
looks like. The profiles do not cross: policy-v2's curve is at or above
policy-v1's at every τ.

`demo/rl/train` (96 episodes, 2 policies × 6 tasks × 8 runs) is the case
worth trusting. policy-v2's IQM is 6.1 [4.19, 6.74] against policy-v1's
−6.2 [−6.8, −4.6]: the intervals no longer overlap. P(policy-v2 >
policy-v1) is 88% [82%, 94%], and that figure earns its keep — it is an
average over tasks, and on `rl05_incident_postmortem` it is 41%, below the
coin flip, because policy-v2 passes 0 of 8 there where policy-v1 passes 4.
A policy can win the aggregate and still be a regression on a task, and
the block puts that task at the top of its per-task list rather than
letting the average bury it. The pooled profiles still do not cross, since
policy-v1's runs on the other five tasks are weak enough to cover it; the
per-task view is what carries the exception.

### Reading it on the page

`web/blocks/28_rlstats.js` draws three blocks in the Training view.
*Aggregate score, with intervals* is the paper's Figure-1 idiom: four
metric rows on one shared score axis, both policies' point estimate and
bootstrap interval on each, so an overlap is the first thing seen; the
optimality gap row is marked *lower is better* because a long bar there is
bad news. *Performance profile* draws the two curves with their bands, τ on
x and the fraction of runs on y, and says in a sentence whether one
dominates or where they cross. *Probability of improvement* gives the
single figure with its interval against a visible coin-flip line, the
sentence that interprets it, and the per-task breakdown — hover a task for
its own runs, click to open it.

## Watching one episode happen

Everything above is a distribution: returns per episode, rewards as a map,
intervals over runs. None of it shows an episode *unfolding* — reward
arriving step by step, the return climbing or sinking, two policies on the
same task pulling apart at a particular moment. Two blocks in
`web/blocks/32_rltheatre.js` add that, overview first.

**Every episode** (`rl-ridgeline`) is one row per policy per task. Each row
overlays that policy's episodes on the task as cumulative-return curves,
with the median episode by return drawn heavy — the median rather than the
mean because a mean curve is a curve nobody ran. Every row shares one
return axis and one step axis, so the rows are comparable and a short task's
curves visibly stop early. Colour carries the policy, so success and
failure are carried by line style instead: solid solved, dashed failed. On
`demo/rl/train` that is twelve rows of eight curves, and the shape of the
reward is legible at a glance — a long shallow drift, then the terminal
answer reward fanning up for policy-v2 and down for policy-v1, except on
`rl05_incident_postmortem` where it is the other way round. Past sixteen
episodes in a row the curves stop being separable, so the row becomes a
band from the lowest to the highest return at each step with the median
over it; the band at step *k* covers only the episodes that reach step *k*,
and the note on the block says so rather than letting the tail imply that
every run was still going.

**Episode theatre** (`rl-theatre`) plays one episode per policy on a task.
It opens on the *contested* task — the one where the policy that is ahead
across the batch is behind here — because the widest gap is usually the
task the winner wins the way it wins everywhere, and the contested task is
the one worth watching. With no contested task it opens on the widest gap,
and the bar says which rule fired. Within the task it takes each policy's
median episode by return, and a stepper walks the policy's other runs in
return order.

A scrubber sets one position along the episode. Both runs' cumulative
returns are drawn solid up to it and ghosted past it, so the reader can see
where they are in the whole. At the position each run gets a mark, a small
bar for that step's reward — square-rooted against the largest reward in
view, because these rewards span two orders of magnitude and a linear bar
renders an ordinary −0.1 step as nothing — and a label naming the tool and
the reward. The first step at which the two returns differ by a whole point
is ringed and labelled permanently: that is the answer to "when did it go
wrong", and it stays on screen wherever the scrubber is.

Below the chart each run's steps are a ribbon, one cell per step, coloured
by the sign and size of the reward, with the scrub position a line across
both. **The two runs are aligned by step index and nothing more** — step 12
of one run is not the same action as step 12 of the other — and the block
says so, because the alignment is a drawing convenience, not a claim. Where
a run ended its ribbon ends; nothing is padded to make the two match, and
the scrubber's value text says "ended at step N" for the run that has
finished.

Under that, the step under the scrubber on each side in full: the tool, its
input abbreviated, the reward, the cumulative return and the labels the
analysis attached. Per-step detail like this exists only for the runs a
pair report covers, so a run without one says "no per-step detail recorded
for this run" instead of inventing a tool name. Where the detail is there,
clicking the panel opens that step in the page's step inspector.

The whole thing is drivable without a mouse: the scrubber is a
`role="slider"` with a live `aria-valuetext` naming the step and both runs'
current tool, reward and return; ← and → step one, shift steps ten, Home
and End go to the ends, and space plays at eight steps a second. It never
plays on its own, under `prefers-reduced-motion` or otherwise. The task and
run choice are remembered per browser through the page's own store, which
falls back to memory when a `file://` origin refuses `localStorage`.

## The behaviour space: what a policy *does*

Return says which policy won. It does not say what either one did, and two
policies can earn the same return by behaving nothing alike.
`deepcompare/rlspace.py` reads the same episodes as *behaviour*, and ships
the reading at `aggregate.rl.space`. Every step becomes one token — the
tool's name for a tool-ish step (`grep`, `read_file`, `search`,
`run_check`), the step's own family otherwise (`plan`, `reason`,
`answer`) — so an episode is a short string of tokens in the order the
policy acted, and a policy is a set of those strings. A token is a name
as the traces wrote it: a tool literally called `reason` would share a
token with the reason family, and the vocabulary lists what is there
rather than namespacing it.

**The vocabulary** counts every token per policy, its share of that
policy's steps, and the *signature* — the tokens one policy uses and the
other never does. On the training demo the signature is empty for both:
the two policies use all seven tokens, and differ in *how much* and *in
what order*, not in what they can do. That is itself the finding.

**The trie** is a prefix tree over the token streams. Every node carries
its prefix, how many episodes of each policy pass through it, and the
mean return and success rate of the episodes below it — so one tree is
both policies' trees at once (`rlspace.policy_trie` restricts it to one).
Two prunings keep it readable, and `trie["pruned"]` names both: a subtree
reached by a single episode becomes one leaf carrying `tail` (the tokens
folded under it) rather than being expanded, and nothing is built below
`max_depth` (24), where a node carries `truncated`. On the 96-episode
training demo that is 29 single-episode tails (520 tokens hidden) and 22
nodes cut at the depth cap (1,079 tokens).

The **branch points** are where the policies part. At a node each
policy's episodes divide over the children as a distribution; the
*imbalance* is the total-variation distance between those two
distributions (0 = they split alike, 1 = they take disjoint children),
and the score weights it by the episodes that reach the node, so a
lopsided split three episodes deep does not outrank the place the whole
batch divides. Each branch point names the child each policy leans to and
the mean return below it. On the training demo the top one is step 12,
where 48 of 96 episodes have arrived: `policy-v1` goes on to `search`
(mean return −3.49 over 22 episodes), `policy-v2` to `reason` (+3.38 over
26), imbalance 0.58. Five more follow, all the same shape — the weaker
policy searches again where the stronger one stops to think.

**The habits** are n-grams. Per policy, the commonest length-2 and
length-3 sequences; across the outcome, each gram's rate among the solved
episodes over its rate among the failed, with all four counts carried,
because a ratio of 3.0 may be 3 against 1. The rate's denominator is the
group's own gram count, so a solved episode that is simply shorter (36.1
steps against 43.8 on the demo) lifts the share of everything it does —
`per_episode` sits beside it for that reason. Three lists come back:
`top` (the solved end), `bottom` (the failed end) and `separating`, both
ends ranked by how far from parity they sit, which is the one to read
first. On the demo the strongest winning gram is `grep → grep → grep` at
1.22× (90 against 102) — a composition effect, and its per-episode counts
are equal, which the counts make visible. What actually separates the two
is at the other end: `reason → run_check → reason` at 0.36× (9 against
35) and `run_check → reason → run_check` at 0.48× (23 against 67) — the
verifier's retry loop, the weaker policy running a fourth check and
erring on it.

**The distance** between two episodes is the normalised edit distance
over their token streams: Levenshtein over the longer stream, 0 identical
and 1 nothing in common. Edit distance and not a bag of n-grams because
order and length are the point — a policy that does the same work in ten
steps instead of twenty, or searches before it reads rather than after,
differs in a way a bag cannot see, and insertion/deletion is the shape of
"one extra search". From the matrix comes each policy's **behavioural
spread**, the mean distance between two of its own episodes: on the
training demo `policy-v1` sits at 0.23 and `policy-v2` at 0.22, with 0.30
between them. A policy that always does the same thing has a small
spread, and no return number carries that.

The matrix is quadratic in episodes, so it is capped: at most 120
episodes (chosen round-robin over the policies, so both are represented)
over at most 200 tokens each, and `distance["capped"]`, `counted` and
`of` say what was left out. Past the cap the extra episodes are simply
not in the matrix, the spread or the layout — they are never estimated
from the ones that are. The 96-episode demo fits whole, in about a third
of a second; the distance itself uses Myers' bit-parallel algorithm,
checked against the textbook row DP in the tests.

**The layout** is classical multidimensional scaling of that matrix in
pure Python: double-centre the squared distances, then the top two
eigenvectors by power iteration from a fixed seed for a fixed number of
iterations, each eigenvector's sign fixed by its largest entry so the
same input gives the same bytes. **The axes mean nothing** — no unit, no
direction. Only relative position says anything, and only as well as a
plane can hold the distances, which `stress` reports (0.21 on the demo).

Two blocks draw it. *Behaviour atlas* is that layout: a mark per episode,
filled if it solved the task and a hollow diamond if it failed, coloured
by policy, sized by |return|, with a dashed ring where an episode's
nearest neighbour belongs to the *other* policy — a v2 failure sitting
inside v1's cloud is the case worth finding. There are no axes drawn,
because drawing them would lie; there is a scale bar, because the
distance is the one length that means something. Underneath it the habits
rank, and clicking one lights up the episodes that play it. *Where the
policies part* draws the trie as a thread with branches in the vocabulary
of "Where it mattered": a branch's thickness is the episodes through it,
its colour the mix of the two policies, a run of steps every episode
takes the same way folds into a dashed ×N that dilates on click, and the
ranked branch points are ringed and tabled with the return on each side.

## Auditing the signal: is the reward trustworthy, is the critic any good?

Everything above reads a policy by what it *earned*. `deepcompare/rlaudit.py`
asks the question one layer down — the one nobody asks until a policy is
already gamed — and writes it to `aggregate["rl"]["audit"]` (and, at pair
scale, to `report["rl"]["audit"]`):

```
{version, measurable, gamma, scope: batch|pair, episodes_n, policies[],
 reward {episodes[{agent, task_id, run_id, side, success, steps, seconds,
                   return, shaping_return, last_reward, flagged}],
         disagreement {passed, failed, scopes {pooled|by_task|shaping:
                         {basis, pairs_n, inversions, ties, inversion_rate,
                          separation, tasks{}}},
                       findings[], findings_n, findings_basis, flagged[],
                       by_policy[], note},
         rank_agreement {spearman, ceiling, spearman_shaping, n, per_agent{}, note},
         concentration {episodes[], mean_largest_share, mean_last_share,
                        mean_peakedness, mean_paid_share, kinds{}, kind, note},
         unearned {steps_labelled, positive_while_bad, negative_while_good,
                   by_label{}, rows[], note},
         cost {per_agent{return_per_step, return_per_second, mean_steps, …}},
         tools {per_agent{tools{}, top_tool, top_share}, findings[]},
         narrative},
 critic {n, episodes_covered, overall {mean_error, mean_absolute_error, rmse,
          variance_actual, variance_residual, explained_variance, direction,
          worse_than_the_mean}, per_agent{…, deciles[], words},
         deciles[{bin, n, predicted_lo, predicted_hi, predicted, actual, residual}],
         residual_bins[], points[{agent, task_id, run_id, step, predicted,
          actual, residual, success}],
         advantages {measurable, definition, recorded, checked, inconsistent,
          episodes[], max_error, tolerance}, narrative},
 narrative, caveat}
```

**Reward integrity.** The reward is a proxy; these are the places it and
the outcome part company.

*Disagreement* counts every (passing, failing) pair of episodes the return
orders the wrong way round, in three readings. **Pooled** compares every
episode with every other. **By task** compares only within a task, which is
the fair reading: a return on one task is not on the same scale as a return
on another. **Shaping** takes the last step's reward out of each return
first, within a task — the reading that matters when the answer step itself
pays. A reward whose terminal term carries the outcome agrees with the
outcome *by construction*; the question is whether the dense part a policy
collects along the way agrees too, and that is where a policy would collect
return without passing. Findings come from the first reading that has any,
ranked by the size of the disagreement, each naming the policy, the task,
the run and the value. Beside them, `by_policy` asks the same question one
level up: per task, does the policy the reward prefers also pass more often?

*Rank agreement* is Spearman's rho between the return and the outcome,
implemented here (average ranks, pure stdlib). A binary outcome is one long
pair of ties, so rho cannot reach 1 however good the reward is — the
attainable **ceiling** (the same returns reordered to be perfectly
consistent) is reported beside it, so 0.86 is not read as a shortfall. Rho
on the shaping alone says how much of the agreement the outcome term is
carrying by itself.

*Concentration* gives the share of an episode's total absolute reward
carried by its largest step and by its last, the share of steps paid
anything at all, and how many times its even share the largest step
carries. The classification is stated *with* those numbers, never instead
of them: `terminal` when the last step is ≥ 90% of the episode (the whole
return is one number), `terminal_dominated` when the last step is the
largest and ≥ ⅓, `peaked` when some other step is, `dense` otherwise.

*Unearned reward* lists the steps paid positively while carrying a label
the analysis calls bad (`dead_end`, `error`, `fault_enters`,
`fault_carried`, `invented_argument`, `no_information`, `repeat`,
`spent_after_basis`, `wrong_answer`) and the steps punished while carrying
a good one (`fed_answer`). `by_label` is the overview; `rows` are the
largest, at most two from any one policy and task.

*Cost* is return per step and per second per policy, so "better" can be
told apart from "longer". *Tools* traces which tool the positive reward
flowed through and which the negative did; a policy earning all of its
tool-mediated return through one tool is a fragile policy, and that is
raised as a finding.

**Critic calibration.** Where a step carries a `value`, that value is a
prediction of the discounted return-to-go. The realised return-to-go is
recomputed from the recorded rewards at gamma and the **residual** is
`value − actual`. From the residuals: the mean signed error (the bias — a
positive mean is an optimistic critic), the mean absolute error, the RMSE,
and the **explained variance** `1 − Var(residual)/Var(actual)`, the
standard critic-health number. It goes *negative* when the critic is worse
than predicting the mean, and the section then says exactly that in words
rather than rounding past it. Calibration is broken out by decile of
predicted value, so a reader sees *where* the critic is wrong and not only
how much. A recorded `advantage` is checked against the definition this
document states for it (`discounted return-to-go − value`) and every
episode that fails is named; advantages the engine itself derived are not
checked, because checking them would only check the engine against itself.

**What it says on the demos.** On `demo/rl/train` (96 episodes, 2 policies
× 6 tasks × 8 runs): no failed episode out-earns a passing one on the same
task, over 367 ordered pairs, and rho is 0.86 against a ceiling of 0.86.
Take the last step out and the shaping alone gets 23 of those 367 pairs the
wrong way round — 11 of them on `rl05_incident_postmortem` — and rho falls
to 0.70, which is how much of the agreement the ±5 answer term was carrying
on its own. The reward is terminal-dominated (the last step is 42% of an
episode's absolute reward, 13× an even spread, with 80% of steps paid
anything); 358 steps of 3,858 were paid while labelled `dead_end`, worth
+322 between the two policies; and all of the tool-mediated positive reward
of both policies flows through `read_file`. The critic explains 42% of the
variance overall but −0.09 for `policy-v1`, which is worse than predicting
the mean, against +0.41 for `policy-v2`. On the smaller `demo/rl/traces`
(12 episodes) the shaping gets 1 of 17 pairs the wrong way round, rho is
0.86 at its ceiling of 0.86, and *both* policies' critics score below zero
(−0.12 and −0.14) while the pooled figure is +0.22 — a reminder that an
explained variance pooled across policies is not the explained variance of
either.

Every finding is a **signal to investigate, not a proven defect**: a failed
episode with a high return may be a hard task rather than a gamed one, and
a badly calibrated critic early in training is expected. Each one names the
task, the run and the step so it can be checked. Nothing here is estimated
— a check with no evidence returns `measurable: False` and a reason.

On the page, the Training view ends with two blocks that read this section:
**Reward integrity** (*is the reward measuring the right thing?*) leads
with the sentence that answers it, then plots every episode's return on one
axis with passes and failures as two rows — no jitter needed — the
disagreeing episodes ringed and named, the x measure switchable between the
return and the shaping alone; beside it the concentration reading and the
unearned-reward list, each row clicking through to the step it names.
**Critic calibration** (*does the critic know what is coming?*) plots the
value estimate against the realised discounted return-to-go with y = x as
the only reference, the decile calibration curve over it, points coloured
by policy and the residuals as a small marginal underneath, and states the
explained variance in words.
