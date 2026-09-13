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
