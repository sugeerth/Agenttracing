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
