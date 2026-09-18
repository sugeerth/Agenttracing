# AgentDiff Trace Schema (v1)

Every agent run is captured as a **Trajectory** — a JSON file. Two trajectories on
the same task are the unit of comparison.

## Trajectory (top-level JSON object)

```json
{
  "schema_version": 1,
  "trace_id": "string — unique id",
  "agent": {
    "name": "string — e.g. 'agent-a'",
    "model": "string — e.g. 'claude-sonnet-5'",
    "version": "string — e.g. 'v2'",
    "system_prompt": "string — optional: the instructions the agent was given (Recorder(system_prompt=))",
    "config": {"...": "object — optional: the agent's configuration as given (Recorder(config=))"}
  },
  "task": {
    "id": "string — task id shared by both trajectories",
    "prompt": "string — the task given to the agent",
    "expected": "string|null — gold answer if known"
  },
  "outcome": {
    "success": true,
    "answer": "string — final answer produced",
    "score": 1.0
  },
  "totals": {
    "input_tokens": 0, "output_tokens": 0,
    "cost_usd": 0.0, "latency_s": 0.0
  },
  "steps": [ { "...Step..." } ]
}
```

## Step

```json
{
  "index": 0,
  "type": "plan | search | retrieve | read | tool_call | reason | answer",
  "name": "string — tool name for tool_call/search, else short label",
  "input": "string — query / tool args / plan text",
  "output": "string — observation / tool result / produced text",
  "tokens": 0,
  "latency_s": 0.0,
  "quality": "good | weak | bad | null  — optional per-step annotation",
  "note": "string|null — optional human/model annotation"
}
```

### Step type semantics

| type       | meaning                                              |
|------------|------------------------------------------------------|
| plan       | agent states or revises its plan                     |
| search     | issues a search query (web, vector DB, code search)  |
| retrieve   | selects/receives retrieval results                   |
| read       | reads a document/page/file                           |
| tool_call  | calls any other tool (calculator, code exec, API)    |
| reason     | intermediate reasoning/decision text                 |
| answer     | emits the final answer (always the last step)        |

Optional integers `input_tokens` and `output_tokens` — this step's prompt and completion counts when the provider gave both; `tokens` stays the total. The split is the difference between context re-sent and text generated, which cost differently and are moved by different fixes. `budget.io` reads the steps when they carry it (`source: steps`, with `steps`/`of_steps` saying how many did) and falls back to `totals`, naming which it used. Optional integer `cached_tokens` — how many of this step's *input* tokens the provider served from its own cache, when it said so (`prompt_tokens_details.cached_tokens` on OpenAI-shaped bodies, which includes them in the prompt count; `cache_read_input_tokens` on Anthropic-shaped ones, which does not). `null`/absent means the provider did not say, which is not zero — a working cache and a provider that never mentions one look identical without it, and `tokens` then counts re-sent context at full price. `budget.tokens.cached` sums it over the steps that reported one and stays `null` until one does. `budget.tokens.integrity {ok, checks[{name, steps[], note}]}` names the counts that cannot all be true — `split_disagrees_with_total`, `cached_exceeds_input` — and the narrative says so beside the figures rather than presenting an impossibility as a fact; the trace is not refused and the counts are reported as recorded, because neither half of a contradiction can be preferred over the other. Optional integer `attempt` — which try this is at the same call, 1-based, and written *only* when the harness re-executed the call itself rather than handing the failure back to the agent. Without it a retry and the agent asking for the same thing twice are the same record, and they are not the same event: one is the platform's cost and the other is the agent's behaviour, and every rate that counts calls conflates them. `fetches` reads it — `attempt > 1` is a retry and is not counted a repeat — and says in `retry_basis` how much of the run numbered its attempts, because a retry count of 0 on a trace that numbers nothing is an absent record and not a measured absence. Absent, not null, when the harness ran the call once. Optional numeric `started_s` — seconds from the run's start to the moment this step *began*. Without it the only way to place a step on a clock is to sum the durations before it, which silently asserts the run was sequential; a loop running independent calls concurrently makes that reconstruction wrong with nothing in the record saying so. `Recorder` writes it; absent means unrecorded, and `timing.timeline` then reports `basis: reconstructed` with `overlap_s: null` — not zero, because a run whose concurrency nothing wrote down is not one that had none. Optional `span: {id, agent, parent?}` — the (sub-)agent acting at this step and the span it was delegated from (absent = the root agent); `Recorder.span("name")` stamps it; `convert --format otel` derives it from nested `invoke_agent` spans; optional numeric `reward`, `value`, `advantage` — the RL signal at this step (the environment's reward, the policy's value estimate, its advantage; `Recorder.step(..., reward=)` writes them only when given; absent = unrecorded, and the `rl` section then *shapes* a reward from the labels, `docs/RL.md`). A trajectory's optional `tools` (`[{name, parameters, effect?}]`) is the table the runner offered and `budget` the settings the loop obeyed — what *ran* the agent rather than what the agent is, which is why `harness_evolution` reads both back to decide whether two generations are comparable at all. A `budget` entry is a number, except for the keys in the closed vocabularies `trace.BUDGET_FLAGS` (booleans) and `trace.BUDGET_NAMES` (non-empty strings): most settings are limits, but a loop also has switches and settings that name something, and those belong here for the same reason the limits do. The vocabulary is closed so that widening it for a switch did not stop `{"max_steps": "twenty"}` being refused. `deepcompare.harness.agent` reads seven (`deepcompare.scaffold.BUDGET_KNOBS`, the settings the scaffold actuator may propose): `max_steps`, `max_tool_errors`, `max_tool_retries` (how many times a failed call is re-run before the error goes back to the agent; each try is its own step carrying `attempt`), `dedupe_tool_calls`, `require_before_answer`, `require_read_before_write` and `parallel_tool_calls` (how many *declared reads* may be in flight at once; a turn containing a write or an undeclared effect goes sequentially, and the steps stay in the order the agent asked for them with the times they really took) (`docs/HARNESS.md`). An unset key is unset, never a default written down. `agentdiff loop` writes `aggregate.loop.reach` — `{rows[{category, effort, detail, verdict: knob|prompt|no knob|investigation, knob}], counts, total, seen{category: {proposed, unactionable}}, reading}` (`scaffold.reach`, `scaffold.seen_in`): every category the triage engine can recommend and whether this harness can express it, derived from the actuator's own rules so the map cannot drift from them. When one of those settings acts on a step, the step carries `scaffold` — `cache_hit`, `answer_gate` or `write_gate` (`trace.SCAFFOLD_ACTIONS`, closed) — saying what the *harness* did to it as opposed to what the agent did: a read served from the cache, an answer held back, a first write refused. It is a field and not the prose `note` beside it because a reading that had to match on English to find the harness's own interventions could not be trusted to have found them all. Unlike the other optional step fields it is *absent* rather than null when it does not apply, so adding it did not change the bytes of every stored trace to say nothing; absent and null mean the same thing — the harness did not act on this step.

### Step model telemetry (optional `model` object)

A step may carry what the serving stack knew about its own generation.
Every field is read as written; nothing here is estimated by the engine.

```json
"model": {
  "confidence": 0.91,            "min_token_confidence": 0.62,  "entropy": 0.27,
  "tokens_scored": 64,           "temperature": 0.2,            "source": "provider-logprobs",
  "interval": {"low": 0.85, "high": 0.97, "n": 64,
               "basis": "95% normal interval of the mean token probability over the step's scored tokens"},
  "internals": {"model": "gemma-2-2b", "sae": "20-gemmascope-res-16k", "source": "neuronpedia",
                "features": [{"index": 12480, "activation": 9.5, "max_activation": 12.0,
                              "label": "unit / time-zone confusion", "tokens": ["11:45"],
                              "url": "https://www.neuronpedia.org/gemma-2-2b/20-gemmascope-res-16k/12480"}],
                "note": "features that fire on the text this step produced; ..."}
}
```

- `interval` is written by `deepcompare.logprobs` from the step's own
  token probabilities (`None` under three tokens). A `basis` beginning
  `SYNTHETIC` is a demo band and is drawn and labelled as one.
- `internals` is written by the harness (`deepcompare.harness.neuronpedia`:
  the Neuronpedia API with `NEURONPEDIA_API_KEY` from the environment, or a
  `ScriptedNeuronpedia` table offline). A `source` beginning `synthetic`
  is labelled synthetic wherever it surfaces.

## Comparison report (engine output, consumed by the viewer)

```json
{
  "task": { "id": "...", "prompt": "..." },
  "a": { "agent": {...}, "outcome": {...}, "totals": {...}, "steps": [...], "trace_id", "run_id", "schema_version", "tools", "budget", "token_accounting", "harness"? },
  "b": { "agent": {...}, "outcome": {...}, "totals": {...}, "steps": [...] },
  "alignment": [
    { "a_index": 0, "b_index": 0, "op": "match | drift | a_only | b_only", "similarity": 0.93 }
  ],
  "divergences": [
    {
      "rank": 1,
      "a_index": 2, "b_index": 2,
      "kind": "retrieval | tool_selection | tool_execution | planning | reasoning | stopping",
      "summary": "Agent B selected a lower-quality source.",
      "downstream": {
        "extra_steps_b": 4, "extra_tokens_b": 1210,
        "extra_latency_s_b": 23.0, "caused_failure": false
      }
      // downstream keys use the `_a` suffix instead (extra_steps_a, ...) when
      // agent A is the side spending more from the divergence point onward
      // downstream also carries "failed_agent": "a" | "b" | null on the
      // causal divergence, so consumers attach "caused failure" to the
      // failing side rather than the merely heavier-spending one.
      // Note a_index/b_index may fall in DIFFERENT alignment rows when the
      // region is one-sided; resolve a divergence column by matching either.
    }
  ],
  "attribution": {
    "failed_agent": "b|a|null",
    "root_cause_step": 2,
    "chain": [2, 5, 9],
    "category": "retrieval",
    "explanation": "Agent B diverged at step 2 ..."
  },
  "metrics_delta": {
    "steps": {"a": 5, "b": 9}, "tokens": {"a": 0, "b": 0},
    "cost_usd": {"a": 0.0, "b": 0.0}, "latency_s": {"a": 0.0, "b": 0.0},
    "tool_calls": {"a": 0, "b": 0}, "searches": {"a": 0, "b": 0}
  }
}
```

Multi-run aggregate report (`aggregate.json`): rollups keyed by side, with the
side→agent-name mapping in `agents`:

```json
{
  "tasks": 8,
  "agents": {"a": "agent-a", "b": "agent-b"},
  "success_rate": {"a": 0.875, "b": 0.625},
  "means": {"a": {"tokens": 0, "cost_usd": 0.0, "latency_s": 0.0,
                  "steps": 0, "tool_calls": 0, "searches": 0}, "b": {"...": 0}},
  "failure_origins": {"retrieval": 0.5, "tool_selection": 0.25, "...": 0.25},
  "regressions": ["human-readable regression strings"]
}
```

## Fleet report (`fleet.json`, N-agent mode)

When comparing many agents, the injected payload becomes
`{"fleet": {...}, "reports": [spotlight pairwise reports], "aggregate": {...}}`.

```json
{
  "tasks": [{"id": "...", "prompt": "..."}],
  "scoring": {
    "weights": {"success": 0.45, "cost": 0.15, "latency": 0.10,
                "tool_discipline": 0.15, "step_economy": 0.15},
    "method": "min-max normalized per dimension across the fleet; composite = weighted sum"
  },
  "agents": [
    {
      "name": "...", "model": "...", "version": "...", "archetype": "...",
      "rank": 1, "score": 0.87, "pareto": true, "dominated_by": 0,
      "metrics": {"success_rate": 0.9, "mean_tokens": 0, "mean_cost_usd": 0.0,
                  "mean_latency_s": 0.0, "mean_steps": 0, "mean_tool_calls": 0.0,
                  "wasted_tool_calls": 0.0, "mean_searches": 0.0,
                  "bad_steps": 0, "weak_steps": 0},
      "dimension_scores": {"success": 1.0, "cost": 0.6, "latency": 0.7,
                           "tool_discipline": 0.8, "step_economy": 0.5},
      "failure_fingerprint": {"retrieval": 0.5, "tool_execution": 0.5},
      "rationale": "plain-language ranking explanation with numbers",
      "per_task": {"t01": {"success": true, "tokens": 0, "latency_s": 0.0,
                            "steps": 0, "tool_calls": 0}}
    }
  ],
  "spotlight_pairs": [
    {"a": "...", "b": "...", "why": "why this pair is informative",
     "report_indices": [0, 1]}
  ]
}
```

`dimension_scores` are 0-1 (1 = best in fleet) so a client can re-weight the composite live.
`wasted_tool_calls`/extra steps = steps in divergence regions that did not change the outcome.

## Report objects the current engine adds (summary)

Every pairwise report carries, beyond the sections above:
- `diagnosis` — the adjudicated explanation: `hypotheses[]` (kind, score,
  status, supports/contradicts by evidence id, `evidence_classes`
  {observable, annotation, stated}), `evidence[]` (each with
  `evidence_class`), `leading`, `margin`, `verdict`, `causal_account[]`,
  `decisive_step` {step, criterion, basis, window, verification
  (hypothesized | replay-verified | replay-refuted | replay-mixed),
  replay_recipe, joint_candidates, overdetermined, replay}, `confidence`
  {level, n, basis, verified}.
- `reading` — `{a, b}`: each run understood alone — phases,
  what_happened (per-step role), rests_on (answer atoms with basis
  status), answer_basis, validity, phase_checks, errors, critical_error,
  why_it_ended, what_it_means (findings with evidence_class),
  take_forward (located next actions), confidence, evidence, summary.
- `verdict_card` — five lines (verdict, cause, cost, fix, confidence),
  each quoting a section above.
- `uncertainty.{a,b}.interval` — per-step `[low, high]` beside `series`
  (`None` where the step carries none) with `interval_basis`, the bases
  quoted from the traces.
- `outcome.judge` (on a trace, written by `judge`) — `{model, provider,
  rubric, with_steps, success, score, rationale, raw, error, self_judged,
  applied, prior {success, score, graded_by}, agrees_with_prior}`;
  `outcome.graded_by: "model"` only after `--apply`.
- `aggregate.equality` (runs) — `{normalisation, tasks {task: {agents
  {name: {runs, distinct_answers, equality_rate, majority_answer,
  majority_matches_expected, successes, answers[{answer, runs,
  success}]}}, expected, cross_agent}}, per_agent, cross_agent, note}`.
- `aggregate.routing` (batch, runs) / `routing.json` (`route`) —
  `{objective, min_runs, families {family: {pick, confidence, why,
  either, candidates[{agent, features {n, successes, rate, ci95,
  cost_usd, latency_s, tokens, steps, tool_calls, terminations,
  equality_rate?, mean_distinct_answers?, consistently_wrong_tasks?},
  fault_kinds}]}}, overall, rationale {families, overall}, hints}`.
- `horizon` — `{a, b: {subdivisions, spans, agents[], depth, tree {kind: run|span|episode|step, key, label, agent,
  from, to, count, seconds, wasted_s, tokens, tool_calls, errors, fault, decisive, values, children}, graph {nodes, edges},
  blame {agent, step, delegated_by, depth, part, chain, sentence}?, summary}, diff {nodes[{agent, in, a, b, delta}],
  edges[{from, to, in, count_a, count_b}], only_a, only_b, uneven, same_agents, same_shape}, narrative}`; `trust` — `{version, a, b: {behaviour {steps, tool_calls, distinct_tools, tools, thinking_steps, answered, termination, stopped_by: agent|harness|unknown, loops, retries, errors, sub_agents, delegations, max_depth}, permissions {effects {read, write, undeclared}, writes_without_read, verify_after_write, forbidden_calls[{step, name}], forbidden_patterns[], external, mcp_servers, handoffs}, determinism {replay_verification, replay_reproduced, run_consistency, temperature}, data {schema_version, adapter, synthetic, graded_by, latency_measured_share, tokens_measured_share, effects_declared_share, spans_recorded}, grade {score, label: high|medium|low, reasons[]}, narrative}, narrative, note}` — counts over the steps, the policy's forbidden tools (`--policy`, or the golden set's), the rubric in `deepcompare.trust.RUBRIC` with every deduction a sentence.
- `timing` — `{a, b: {measurable, total_s, steps[], by_category, by_tool, wasted_s, slowest, rationale, timeline {measurable, reason, basis: recorded|reconstructed, span_s, sum_s, overlap_s, concurrent_steps, recorded_starts, steps, reading}}, delta, narrative}`; `milestones` (with `--golden`) — `{a, b: {measurable, total, reached, progress, milestones[{id, label, reached, step, seconds, agent, on_time, evidence_hit}], in_order, steps_after_last, narrative}, diff {rows[], further, narrative}, narrative, source}`; `impact` — `{version, a, b: {measurable, total_s, total_steps, clusters[{id, from, to, steps, start_s, end_s, seconds, lane, agents[], impact (0–1, score / the pair's max score), score, kind: hot|work|quiet, reasons {fault_steps, decisive, errors, retries, wasted_s, milestones[], divergence_rows, first_divergence, answer, tokens}, why, label, marks[{step, kind, label}]}], hot[ids], lanes[{agent, depth, parent, clusters[]}], scale: pair|run, narrative}, narrative}` — the steps clustered (lane, phase, framing boundaries) and weighed by `deepcompare.impact.WEIGHTS`; `rl` — `{version, measurable, source: recorded|shaped, gamma, a, b: {agent, measurable, source, steps, return, discounted_return, positive, negative, zero, seconds, rewards[{step, reward, cum, to_go, discounted_to_go, credit, labels[], agent, kind, name, value, advantage, why}], largest[{step, reward, why}], credit {source: shapley|null, metric, top[{step, credit}], total}, clusters[] (impact's shape, scored Σ|reward| + Σ|credit| + 2 per fault_enters/wrong_answer; marks reward+/reward−/decisive/answer), narrative}, preference (feedback.preference_pair), audit (the aggregate's shape, over this pair), narrative}` — recorded when any step carries `reward`, else shaped from the labels (`docs/RL.md`); the `runs` aggregate adds `rl {gamma, source, agents {name: {episodes[{task_id, run_id, return, discounted_return, steps, success, rewards[], cum[], values[], advantages[], events{}, tools{}, distinct_inputs, seconds}], mean_return, return_ci, episodes_n}}, tasks {id: {agent: {mean_return, returns[]}, delta, sign}}, pairs[], preferences[], stats {metric, metric_label, higher_is_better, measurable, reason, policies[], tasks[], n{}, episodes_dropped, bootstrap {samples, seed, confidence, method, basis}, target {value, source, explicit, note}, aggregates {policy: {iqm|median|mean|optimality_gap: {point, lo, hi, width, degenerate}}}, profile {taus[], policies {name: [[tau, frac, lo, hi]]}, crossings[], dominant, reading}, improvement {measurable, a, b, point, lo, hi, per_task{}, regressions[], tasks_used, tasks_skipped[]}, advisory, narrative} — interquartile mean, median, mean and optimality gap with a bootstrap stratified by task (runs resampled within a task, fixed seed, an interval over the runs recorded and not a population claim), the performance profile, and the probability that a random run of one policy beats a random run of the other (within-task Mann-Whitney, ties half, averaged over tasks); `audit {measurable, gamma, scope, episodes_n, policies[], reward {disagreement {pooled, within_task, shaping_only, findings[{policy, task, run, gap, status: signal}]}, by_policy, rank {rho, ceiling, shaping_rho}, concentration {largest_step_share, last_step_share, paid_share, peakedness, shape}, unearned[], per_cost, by_tool}, critic {measurable, bias, mae, rmse, explained_variance, by_policy{}, calibration[], residuals[], points[], advantage_check}, narrative, caveat} — whether the reward and the outcome agree (the third reading removes the answer step's own reward, since a reward paid at the answer agrees with the outcome by construction), and whether the value estimate predicts the discounted return-to-go it was predicting; `space {measurable, vocabulary {tokens[], by_policy{}, signature{}}, trie {root, nodes, pruned {tails, depth_cap}, branch_points[{depth, score, imbalance, episodes, sides{}}]}, ngrams {top[], bottom[], separating[]}, distance {spread{}, between, nearest{}, capped, counted, of, note}, layout {points[{episode, policy, x, y}], stress}, narrative}` — each episode as a token stream (the tool for a tool step, the family otherwise), a prefix tree of where the policies part, and a classical-MDS layout whose axes carry no meaning. `evolve` adds `evolution` (v1) — `{measurable, reason, family, protected[], budget{}, layout, metric, thresholds{}, generations[{id, parent, index, mechanism, evidence, episodes_n, tasks[], runs_per_task{}, pass_rate, passes, mean_return, iqm{point,lo,hi}, iqm_by_task{point,lo,hi}, pass_by_task{}, tool_calls{}, size{prompt_chars,rules,skills,tools,memory,config_keys}, artifacts_digest, audit{…, claimed_without_called{episodes,of,sample[]}}, episodes[{task_id, run_id, trace_id, success, return, steps, seconds, tools{}, errors, wasted_s, timeline[[t0, dur, kind, name, reward, flags]]}], episodes_capped, flags_basis}], steps[{from, to, index, mechanism, evidence, trigger_tasks[], evidence_check{cited,found,failures,missing[]}, diff{system_prompt{added,removed,hunks[]}, rules{added[],removed[]}, skills{added[],removed[],changed[]}, tools{}, memory{added,removed,sample_added[]}, config{changed[{key,from,to}]}, protected_touched[], summary}, effect{improvement{point,lo,hi}, improvement_success{point,lo,hi}, iqm{from,to,delta}, iqm_pooled{}, pass_rate{from,to,delta}, per_task{}, gained[], regressed[], advisory}, gaming{return_delta, pass_delta, flag, sign_only, reading}, overfit{trigger_delta, held_out_delta, gap, reading}, drift{between, spread_from, spread_to, top_branch}, protected_episodes[], verdict: improved|regressed|flat|gamed|forgot|traded|null, flags[overfit|protected|over_budget|collapsed|noisy|axes_disagree], reading}], trajectory{improved, regressed, flat, gamed, forgot, traded, accepted_on_noise, noisy_steps[], flags{}, net_iqm_delta, monotone, cumulative[]}, best{id, iqm, why}, recommended{id, is_last, iqm, why}, integrity{touched[{step, path, from, to, source: diff|episodes, silent}], restored[], growth{prompt_chars[], rules[], memory[], over_budget[], collapsed[]}, reading}, drift{from_origin[], consecutive[], reading}, narrative, advisory}` — a self-evolving agent as a lineage (`docs/EVOLVE.md`): the verdict is the effect on two axes (return and outcome) with the rest as flags; the recommendation uses the task-stratified IQM because the pooled one can trim a collapsed task; protected paths are read from the diff and from the episodes' tool calls. `evolve --against` adds `evolution_compare` (v1) — `{measurable, reason, lineages[{family, generations_n, episodes_n, tasks_n, recommended{id,index}, best{}, last{}, evolution (that lineage's section with `episodes[].timeline` omitted and `timelines: "omitted; see aggregate.evolution"` beside `episodes_capped`; the primary lineage's full section is `aggregate.evolution`)}], tasks{shared[], only{}}, metric: iqm_by_task, score, metric_definition, curves{by_index{family: [{index, id, point, lo, hi, pass_rate, episodes_cum, seconds_cum, tokens_cum}]}, by_episodes{}, alignment{}, reading}, race{threshold{metric, value, source, lowest_g0, highest_recommended}, reached{family: {index, id, episodes_cum}|null}, auc{family: {by_index, by_episodes, span, mean_height}}, reading}, peak{a{label,family,id,index}, b{}, improvement{point,lo,hi}, orientation, metric{name,a,b,delta}, iqm_pooled{}, pass_rate{}, per_task{task: {a,b,delta,p}}, behaviour_distance, separates, advisory, reading, pairs[], basis}, final{same}, by_generation[{index, a{}, b{}, improvement{}, behaviour_distance}], by_generation_note, process{family: {steps, improved, regressed, flat, gamed, forgot, traded, verdicts[], accepted_on_noise, accepted_on_noise_steps[], monotone, protected_touched, protected_touched_paths[], over_budget, over_budget_rows[], collapsed, retention{ever_solved, solved_at_recommended, solved_at_last, at_last, lost[]}, drift_from_origin_at_last, mechanisms{mechanism: {steps, mean_delta, improved, regressed, verdicts[]}}, best_paying_mechanism, score[], score_basis, reading}}, task_race{rule, tasks{task: {family: {first_solved_index, solved_at_last, pass_curve[]}}}, first_solver{}, never_solved{}}, verdict{peak, final, learning, process: family|null, *_basis, rule{}, reading}, advisory, narrative, evals{measurable, reason, base[ids], lineages[{label, family, measurable, reason, eval_generations, adopted[], active[], demoted[], retired[], unconfirmed[], tested, rejected, rejected_by{validator: n}, min_adjusted_alpha, drift, closures, closures_learned, hindsight_changed, hindsight_lag_max, recommended{base, evolved, agree}, narrative}], shared_metrics[ids adopted by more than one eval], transfer[{metric, name, status, direction, learned_on, applied_to, step, measurable, reason, delta{point, lo, hi}, from, to, alpha, informative_there, moved, flags_there, reading}], transfer_rule, alpha, reading}}` — two (or more) self-evolving agents compared as evolution processes on four named axes, never one winner without its axis; `evals` sets each lineage's co-evolving eval (`coevolve.coevolve` over the evolution section already in hand) side by side — what each learned, the metrics both adopted, and every learned metric applied to the other lineage's last step with the same delta test at `ALPHA`, one test per metric and lineage, unadjusted and stated — and its reading declares no winner between the evals; peak and final go through the training-ground pair machinery over the two generations' traces; learning is fewest episodes to a stated threshold; process is lexicographic on gamed + protected, forgot, retention, noise, with the raw vector exposed. Every `evolve` output also carries `coevolution` (v1), printed by `coevolve` — `{measurable, reason, synthetic, family, samples, thresholds{alpha, redundant_rho, link_rho, min_coverage, min_n, probe_top, confirm_steps, min_series}, features[{id, kind: count|sum|bool|ratio|estimate, basis, direction: up|down|neutral, dynamic}], base[ids], eval_generations[{id: e0…, index, after_step: null|"g2→g3", trigger_probe, adopted[], demoted[], retired[], size}], metrics{id: {spec{id, name, feature, agg: mean|rate|iqm|task_mean|task_min|task_spread, where: null|{feature, op, value}|{all[]}, direction, origin{probe, step?, eval_gen?, source?}}, status: base|adopted|demoted|retired, origin, validation (the ledger row), confirmation{tested, moved, status: confirmed|unconfirmed|pending, alpha (the level it was adopted at, ALPHA / K of its step, which its later tests and its hindsight deltas use)}|null, caught_at{first_flag_step, first_flag_index, adopted_step, adopted_index, lag, note}|null, adopted_at{step, index, ledger}|null, demoted_at{step, index, reason}|null, retired_at{step, index, pair[], reason}|null}}, ledger[{index, step, eval_gen, probe, spec_id, spec, validators{computable{pass, note, coverage, from{point,lo,hi,n}, to{}}, informative{pass, note, rule, delta{point, lo, hi, excludes_zero}, alpha}, distinct{pass, note, max_rho, against[{metric, testable, rho, basis: episodes|generations, n, note}], class[]?, representative?, rule?}, linked{pass, note, rho, exempt}, not_already{pass, note}}, k, alpha, decision: adopted|rejected, failed[validator names], reason}], matrix{metric_id: {gen_id: {point, lo, hi, n, measurable, reason, per_task{task: {point, lo, hi, n, measurable, reason, note?}} (the metric within each task — the task's own mean or rate, a bootstrap over that task's runs seeded `<metric>:<task>`; `note` on iqm, task_min and task_spread, whose aggregate is not a per-task mean; unmeasurable under MIN_N of the task's episodes)}}}, steps[{index, from, to, base{verdict, flags[]}, evolved{flags[{metric, delta{point, lo, hi}, from, to, direction, status, learned: true}], base_flags[{… learned: false}], moved[{…}], changed, reading}}], probes[{name, question, fired[steps], proposed, adopted}], flow{nodes[{id, kind: agent_gen|agent_step|probe|candidate|validator|decision|eval_gen|metric|flag, label, step?, gen?, probe?, metric?, decision?, ledger?, status?, direction?, size?, question?, delta?, learned?}], edges[{from, to, kind: evolves|triggers|proposes|passes|fails|decides|adopts|demotes|retires|advances|bears|flags|recovers, step, n, label, lag?, flag?, learned?, delta?, after[]?}], summary{nodes{kind: n}, edges{kind: n}, closures, closures_learned, flags_learned, flags_base, sentence}}, recommended{base, evolved, agree, why, excluded{base{gen: [reasons]}, evolved{}}}, integrity{drift{jaccard_distance_from_base, size_by_eval_gen[], basis}, multiplicity{tested, adopted, rejected, unparseable, alpha, min_adjusted_alpha, basis}, demoted[], retired[], unconfirmed[], external{received, parsed, adopted, rejected, sources[]}, gap}, hindsight{steps, changed, learned_flags, base_flags, steps_with_base_flags, caught_at{}}, narrative}` — an eval that evolves with the lineage (`docs/COEVOLVE.md`): per-episode features in a fixed vocabulary plus one `uses:<tool>` per tool seen, metrics as specs, probes proposing at every agent step with what is known up to it, five validators in order at a Bonferroni level `alpha / k`, one representative per redundancy class, the final eval applied to every generation and step with hindsight; every value a stratified-bootstrap interval; a `recovers` edge is a measured movement back, labelled recovered, not attributed; external candidates (`--candidates`, `--propose`) go through the same validators and can set no number, verdict or exit code.
- `aggregate.scorecard` (batch, runs, loop) / `eval.json` (`eval`) —
  `{version, mode: offline|online, golden?, policy?, agents {agent: {runs,
  tasks, rates {success, tool_correct, grounded, policy_compliant,
  risk_free, stopped_when_done, loop_free, error_free, recovered_errors:
  {successes, runs, rate, ci95}}, spend {latency_s, cost_usd, tokens,
  steps, tool_calls: {n, mean, median, min, max, total}}, trajectory,
  tools, grounding, safety {flags, flag_kinds, flagged_runs, …},
  retrieval, time, risk_reward, judge?, graded_by}}, per_run[], note}`; `docs/EVAL.md`.
- `aggregate.loop` / `loop.json` — the ledger: `{config, state {agents, tasks, spent_runs,
  iterations[{n, action: compare|test-prompt, why, results, routing?, paired?, decision? {status:
  kept|kept (provisional)|reverted, evidence {wins, losses, ties, sign_test_p, paired, per_task}}}], prompts, stop}, summary, pools, note}`; `docs/AGENTIC.md`.
- `feedback` — the loop back, derived read-only: `{version, task_id,
  failing_side, failing_agent, step_labels[{side, agent, step, type,
  name, labels[{label, source, mechanism?}]}], preference_pair {prompt,
  task_id, expected, chosen {agent, side, basis, turns[]}, rejected {…},
  diverges_at, estimate, confidence} | null, prompt_suggestions[{text,
  kind, derived_from {finding, instead, refs, at_step, tools?}, test
  (a replay recipe), status}], reward_shaping[{event, sign, count,
  basis}], note}`. Labels: `fault_enters`, `fault_carried`,
  `wrong_answer`, `dead_end`, `no_information`, `repeat`, `error`,
  `spent_after_basis`, `invented_argument`, `fed_answer`, `clean`.
- `internals` — `{available, provenance {model, sae, source}, synthetic,
  rows[{row, a_index, b_index, features_a, features_b, only_a, only_b,
  shared[{index, label, activation_a, activation_b, delta_b_minus_a}]}],
  decisive {side, step, row, counterpart_step, exclusive_features,
  counterpart_only, shared, signature, note}, note}` — the feature diff
  across aligned steps, and at the decisive step the features that fired
  only on the failing side. The signature is cited as `observable`
  evidence for the leading hypothesis (path
  `internals.decisive.exclusive_features`) and never moves a score; the
  note says an activation difference is an observation, not a cause,
  until a steering or ablation replay flips the outcome.
- `task.expected` rides on the report so a replay can grade from it.
- `narration` — optional, written by `narrate --ingest` or `why`;
  read by no analysis.
- `budget` (v1) — `{measurable, reason, a, b: {measurable, reason, tokens {total, by_kind {plan, reason, search, retrieve, read, tool_call, answer}, by_tool {name: n}, measured, estimated, unknown, unknown_steps, basis}, io {measurable, reason, input_tokens, output_tokens, steps, of_steps, source: steps|totals|null}, cost_usd {measurable, reason, value, source}, burn [[index, kind, name, tokens, cum]] (capped at 2000 steps, `burn_capped`, `burn_note`), top [{index, kind, name, tokens, share}] (8), waste {after_last_evidence, in_errored_calls, in_repeats, in_retries, basis}, per_second {measurable, reason, value, seconds}, synthetic, narrative}, delta {total, by_kind, by_tool}, cheaper, narrative}` — where the tokens went: every step's count as recorded, summed under the label the trace gave it (`tokens_basis`), never re-estimated; the totals' input/output tokens and cost when recorded, unmeasurable with a reason when not (unrecorded is not zero, not free); waste after the last step carrying a recorded `reward > 0` or a `quality` of `good` (null when no signal is recorded), in errored steps, in repeated fetches, and in the retries the harness re-ran (`in_retries`, the platform's cost rather than the agent's; 0 where no step numbers its attempts). The `runs` aggregate adds `budget {agents {name: {runs, total, mean, per_task, by_kind, by_tool, measured, estimated, unknown, measured_share, cost_usd_total, cost_runs, synthetic}}, tasks {task: {agent: mean}}, heaviest_runs [{agent, task, run, tokens}], cap {value, source: --token-cap|none given, over[]}, runs [{agent, task, run, measurable, tokens, measured, estimated, unknown, steps, cost_usd, seconds, synthetic}], narrative}` — `cap` from `AggregateContext.extra["token_cap"]`. `fetches` (v1) — `{measurable, reason, a, b: {measurable, reason, records [{index, kind: search|retrieve|read|tool_call, name, query (≤ 200 chars), query_chars, output_chars, tokens, tokens_basis, latency_s|null, error, effect, repeat_of, attempt|null, retry_of, used: true|false|null, used_basis, span}], counts {by_kind, by_tool, total, errors, repeats, retries, attempts_numbered, used, unused, unknown_use}, volume {output_chars, tokens}, sources [{name, calls, errors, output_chars}], map {nodes [{id, kind: query|result|read|call|answer, label, index, size}], edges [{from, to, kind: yields|reads|reaches}], unknown_use, basis}, retry_basis, synthetic, narrative}, delta {total, by_kind, by_tool, errors, repeats, retries}, narrative}` — every search, retrieval, read and tool call and what came back; `used` is a recorded signal (`reward > 0` or `quality` good → true; a recorded reward ≤ 0 or `quality` bad → false) or null with the reason, never inferred from the answer; the map reaches the answer only from fetches whose use is recorded. A fetch whose step carries `attempt > 1` is a **retry** — the harness re-ran it — and names the previous attempt in `retry_of`; any other fetch repeating an earlier name and input is a **repeat** and names the earliest in `repeat_of`. Where the trace numbers no attempts the two cannot be told apart, everything falls to repeats, and `retry_basis` says so in the reading rather than letting a 0 pass for a measurement. The `runs` aggregate adds `fetches {agents {name: {runs, fetches, by_kind, by_tool, errors, repeats, retries, attempts_numbered, used, unused, unknown_use, used_share|null, output_chars, synthetic}}, tasks, heaviest_runs [{agent, task, run, fetches}], runs [{agent, task, run, measurable, fetches, by_kind, by_tool, errors, repeats, retries, attempts_numbered, used, unused, unknown_use, output_chars, synthetic}], narrative}`.
- `data` (v1) — `{measurable, reason, a, b: {measurable, reason, task {id, prompt, prompt_chars, expected, expected_chars}, agent {name, model (as the trace records it), version, framework, instructions {system_prompt, source: trace.agent.system_prompt|trace.agent.config|lineage artifacts|null, chars}, tools_declared [{name, effect}], tools_used [{name, calls, effect_seen}]}, models [{model, steps, kinds {kind: n}, tokens, temperature, source: steps[].model|trace.agent.model|null}], corpus {sources [{id (sha256 of tool name + normalised input, 16 hex), name, kind, input (≤ 200), input_chars, output_chars, tokens, steps[], first_step, digest (sha256 of the first output, 16 hex), error, outputs_differ}], total_chars, distinct, fetches, repeated_reads, id_basis}, provenance {answer_chars, answer_source, atoms, supported, unsupported, values [{id, kind, value, normalized, steps[], supported}], grounded_in [{step, name, kind, source, overlap (0–1), atoms[], basis}], grounded_share|null, ungrounded_share|null, basis}, chain {nodes [{id, kind: agent|data|model|answer, step, label, chars|tokens}], edges [{from, to, kind: feeds|produces|reaches, step, overlap|null, basis}], reading, basis}, synthetic, narrative}, task, instructions_diff {same|null, hunks[], added, removed, reason}, corpus_diff {shared[], only_a[], only_b[], jaccard|null, basis}, models {a[], b[], same|null}, provenance {a, b: {atoms, supported, unsupported, grounded_share, ungrounded_share}, delta_grounded|null, readings {a, b: {answer_basis {atoms, supported, status, source}, semantic {claims_total, claims_grounded, score, source}}}, basis}, narrative}` — what each agent was told, read, and answered from: the prompt and expected answer as recorded; the instructions with their source; which model produced which steps (a step's own telemetry, else the declared model, the source said, the name shown as recorded); the corpus under the fetches section's source identity; the answer's typed values (the semantic extractor's) traced to the fetched outputs that carry them — grounded is *carried by something fetched*, not correct; the chain with two stated overlaps (provenance: share of typed values carried; chain: token containment at `CHAIN_OVERLAP = 0.2`, or adjacency with overlap null). Unmeasurable with the reason where a trace carries no prompt, no model or no text, the readable parts still produced (`docs/DATA.md`). The `runs` aggregate adds `data {agents {name: {runs, measurable_runs, models[], instructions_digest|null, instructions_distinct, sources_distinct, sources_shared_across_runs, grounded_share_mean|null, grounded_runs, atoms, supported, synthetic}}, tasks {task: {prompt_chars, expected}}, narrative}`. `data_evolution` (v1, every `evolve`/`coevolve` output, requires `evolution`, after `coevolution`) — `{measurable, reason, family, generations [{id, instructions {system_prompt, chars, rules[], skills[], tools[], memory_n, config, source}, digest}], steps [{from, to, index, evidence {episodes[], found, failures, data [{episode, found, sources[], fetches, grounded_share|null, success}], summary}, change {summary, hunks[], prompt_added, prompt_removed, rules_added[], rules_removed[], config_changed[], protected_touched[], source}, behaviour {tools_before{}, tools_after{}, sources_before, sources_after, grounded_before|null, grounded_after|null, grounded_runs {before, after}, basis}, effect {verdict, flags[], improvement {point, lo, hi}, improvement_success {point, lo, hi}, source}, eval {flags [{metric, delta, direction, learned}], learned[], eval_gen|null, reading, source}, reading}], synthetic, narrative}` — how the agent evolved from the data its evidence episodes read: per step the cited episodes with the sources each read and its grounded share, the change from `evolution.steps[].diff`, the behaviour shift before and after, the effect, the evolved eval's flags and the eval generation the step advanced to, and a one-sentence reading. `harness_evolution` (v1, every `evolve`/`coevolve` output, requires `evolution`, after `data_evolution`) — `{measurable, reason, kinds {artifact kind: reasoning|scaffold}, generations [{id, index, episodes, fingerprint {measurable, reason, digest, identity_digest, recorded[], identity {declared_models[], declared_versions[], serving_models[]}, models[{name, steps, temperature, top_p, share}], decoding[{name, temperature, top_p}], tools_offered[], caps{}, token_basis[], schema_versions[]}}], steps [{index, from, to, kind: reasoning|scaffold|mixed|none|unreadable, changed {reasoning[], scaffold[]}, kind_reason, harness {moved (null when a side is unreadable), changes[{what, from, to}], explained[{what, from, to, by, note}], identity {moved, changes[], note}, reason, digest_from, digest_to}, attribution {status: attributable|confounded|assumed, basis, reason, note}, absorption {measurable, reason, flag, pass_rate {from, to, delta}, steps_per_pass|tool_calls_per_pass|retries_per_pass {from {point, lo, hi, n}, to {…}, delta}, reading}, base_verdict, base_flags[], reading}], summary {steps, by_kind{}, generations_fingerprinted, generations, attributable[], confounded[], assumed[], assumed_on_rename[], absorbed[], reading}, gap}` — the harness beside the agent (`docs/HARNESS.md`): `tools` and `config` are the scaffold the agent runs inside and the other four artifacts are how it thinks, so every step says which it changed; the fingerprint is read from the traces and never from the manifest, `caps` carries every `budget` setting and not only the numeric ones (a loop's switches and named settings are part of what "the same harness" means, and are what the scaffold actuator turns), with the model *name* kept out of the digest because a per-generation rename and a real model swap are the same bytes in a trace; every change row carries `dimension` (`harnessevo.DIMENSIONS`, closed) and every fingerprint a `dimensions` map saying which of those the episodes recorded at all, so a reader never matches prose to know what moved or whether anything wrote it down; `attributable` is never reached without a fingerprint on both sides that did not move and a model string that did not either, an unreadable side gives `moved: null` rather than `false`, and a tool table that moved by exactly what the step's own artifact diff added is `explained` as the agent's own scaffold change rather than counted as the environment shifting; `absorption` flags a pass rate that rose while each pass cost the agent more work — a gain from the scaffold rather than from the agent, which is not a fault but does not transfer — and is unmeasurable under three passing episodes a side.
- `bundle.json` (`agentdiff bundle`, v1) — `{version, id: "sha256:<hex>" (the canonical JSON of every member's aggregate and reports in member order), records_digest: "sha256:<hex>" (the canonical JSON of `runs/*.json` by key plus the bytes of the copied traces by path — what the id does not cover; `verify` recomputes both and reports `match` and `records_match` separately, with `records_reason`), name, members [{index, label, kind: batch|runs|evolve|coevolve|fleet, source, tasks, agents, lineage, sections, runs}], levels {overview {agents [{name, family, framework, runs, tasks, success_rate {rate, lo, hi, n, basis} (Wilson), tokens_total, tokens_runs, cost_usd_total, cost_runs, seconds_total, seconds_runs, fetches_total, fetches_runs, self_evolving, lineage, synthetic, members}], lineages [{family, member, generations, generations_n, recommended, best, verdicts, eval {generations, adopted, closures, closures_learned, drift, recommended}|null, loops, integrity}], loops [], totals {runs, tasks, agents, members, tokens, tokens_runs, cost_usd, cost_runs, seconds, fetches, fetches_runs, synthetic_share}, sections, heaviest_runs, cap, reading}, runs [{key: "<member>/<task>/<agent>/<run>", member, task, agent, run_id, success, steps, tool_calls, tools, tokens, tokens_measured_share, cost_usd, seconds, fetches, errors, repeats, retries, return, lineage_gen, synthetic, detail, basis}], run_index {key: path}, budget {member: section}, fetches {member: section}}, locators, key}`; `runs/<key>.json` — `{version, measurable, reason, …the row (counts as steps_n, fetches_n)…, steps [{index, type, name, tokens, tokens_basis, latency_s, error, effect, reward, value, input_chars, output_chars, span}], budget, fetches, timeline [[t0, dur, kind, name, reward, flags]], reward_basis, trace_id, trace_path, report, side, steps_source?}`; with `bundle … --traces DIR…` a record whose steps the output did not keep is completed from the trace matched by `trace_id`, else by task, agent and run id (`steps_source: "trace traces/<member>/<path>"`, the file copied there; the row's null numbers filled, `detail` true, `trace` in `basis`); without `--traces` every byte is as before; a sum is over the runs that recorded the quantity (`*_runs` says how many), null where none did; no timestamp; no model identifier (`docs/API.md`). the key (`agentdiff key`) — `agentdiff1:<base64url(zlib(json))>`, json `{v: 1, id, name, agents [{name, runs, success_rate {rate, lo, hi}, tokens_total, self_evolving}] (≤ 12), truncated, lineages [{family, generations, recommended, eval_adopted}], totals, locators}`, ≤ 2000 bytes, printable ASCII, one line; decoded without a bundle, verified against one by recomputing its id. bundle level 3 (`runs/<key>.json`) additions — `steps[].input_text`, `input_truncated`, `output_text`, `output_truncated` (each text capped at `TEXT_CAP = 4000`, the flag true when cut, the full length in `*_chars`) and `data` (the run's data reading). The MCP tools `step {key, index}` (the whole text of one step from the member's copy of the report, with `source`) and `data {key}`; the routes `/api/v1/runs/<key>/steps/<index>` and `/api/v1/runs/<key>/data` (`docs/API.md`).
The full definition of each object, with the reasoning behind every field, is in
[`docs/CHANGELOG.md`](docs/CHANGELOG.md) under the version that introduced it
(diagnosis v27–v31, v33, v41; reading v33; verdict card v37; replay v40).
## CLI commands

| command | what |
|---|---|
| `demo` | one command to the first insight: compares the shipped pairs, writes the report page (three views: Story, Evidence, Batch — `#view=…` in the URL opens one), prints the flagship verdict card |
| `compare A B [--html]` | one pair: card, diagnosis, reading; the blocks page with `--html` |
| `batch DIR -o OUT` | a directory of two agents' traces, pairwise by task, with an aggregate and the report page |
| `runs DIR -o OUT` | repeated runs: stability, pass^k with intervals, consolidation, paired inference |
| `fleet` / `select` / `gate` / `progress` / `experiments` / `variance` / `cohort` / `profile` | N agents, selection, CI gate, before/after, experiments, variance attribution, cohorts, profiles |
| `explain TRACE [--html]` | read one run end to end |
| `bench [DIR] [--strict]` | the diagnoser's own benchmark with the leakage probe |
| `run --provider … --agent … --tasks …` | the harness: any model, any agent, graded SCHEMA traces |
| `deepcompare.harness.neuronpedia` | record SAE feature activations per step (Neuronpedia or a scripted table) |
| `feedback <report.json|dir> [-o signal.json] [--jsonl pairs.jsonl]` / `rl <trace|dir> [--json]` / `grafana <out_dir|trace.json> [-o DIR]` / `evolve <lineage> [--against <lineage>] [-o DIR] [--fail-on …]` | the loop back: step labels, preference pairs, prompt suggestions; `grafana`: Prometheus samples (metrics.prom / .json / .csv, every interval three gauges) for the six provisioned dashboards under `grafana/` (`docs/GRAFANA.md`); `evolve`: a self-evolving agent's lineage, and two lineages compared on four axes (`docs/EVOLVE.md`); `rl`: each trace as an episode — return, discounted return, reward counts, largest rewards (recorded, else shaped), per-agent mean return with its interval for a runs layout |
| `coevolve <lineage> [-o DIR] [--candidates FILE.json] [--propose PROVIDER] [--fail-on hindsight,demoted,unconfirmed,rejected_external] [--ledger]` | the lineage read by an eval that evolves with it: the `evolve` outputs plus `aggregate.coevolution`; prints the eval generations, what each adopted with the reason, the rejections with theirs, the hindsight per step, the recommendation under both evals, the integrity and the gap; `--ledger` every candidate with its five validators; `--propose` asks a model for candidates through the harness, validated, never trusted (`docs/COEVOLVE.md`) |
| `hook --traces DIR --task ID [--expected X] [--db FILE]` | a Claude Code hook: live steps on PostToolUse, the final trace on Stop |
| `route <traces/ or --db FILE> [--objective …] [-o routing.json]` | per task family: each agent's success interval, cost, latency, steps, tool calls; the pick and its confidence |
| `judge <trace|dir|report> --provider … [--with-steps] [--apply] [--db FILE]` | a second model grades the answer; recorded as outcome.judge, applied only with --apply |
| `eval <traces/ or --db FILE> [--golden tasks.json] [--policy policy.json] [--judge NAME=kind:model] [--with-steps] [--write] [-o DIR]` | the evaluation scorecard per agent, offline against a golden set or online as recorded; writes eval.json and EVAL.md |
| `loop --tasks … --provider/--agent ×2 [--runs N] [--iterations N] [--max-runs N] [--suggest AGENT=TEXT] [--golden F] [--policy F] [--judge SPEC] [--db FILE] [--resume]` | the agentic loop: baseline, hypothesis from the reading, paired prompt experiment, keep/revert, runs where the pick is unclear, a ledger with every decision |
| `db --db FILE import/summary/query/search/checkpoints/export` | the trace database (SQLite): ingest, list, full-text search, export |
| `watch [traces/] [--demo TRACES --pace S --loop]` | serve the page live (localhost, server-sent events): running agents stream in, finished pairs become the story |
| `replay REPORT --provider …` | verify the decisive step by re-execution; writes the verdict back |
| `why REPORT --provider …` | narrate through a provider under the covenant |
| `chat OUT_DIR [--provider …] [--ask Q] [--script FILE]` | a grounded conversation about one output directory: the engine's numbered facts (the aggregate's, each pair's, the evolution, the eval that evolved with it, the evals comparison) are the whole brief; every answer is checked number by number and printed with its violations; without a provider, the brief and the prompt |
| `narrate` / `convert` / `check` | brief in/out by hand; foreign traces in; validate a trace |
| `bundle OUT_DIR… -o DIR [--name N] [--locator …] [--traces DIR…]` / `key <key> [--bundle DIR]` / `mcp --bundle DIR` / `serve --bundle DIR [--port P]` | output directories packed into a content-addressed bundle with three levels of grain (`--traces` completes every run's level 3 from its source trace, copied into the bundle); the `agentdiff1:` key decoded, verified or re-derived; the levels over the Model Context Protocol on stdio and over a read-only local HTTP API ([docs/API.md](docs/API.md)) |
