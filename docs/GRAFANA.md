# AgentDiff in Grafana

`agentdiff grafana <out_dir | trace.json> -o grafana/out` writes the
engine's numbers as Prometheus samples, and `grafana/` at the repo root
holds seven dashboards, the provisioning that loads them, a scrape
config and a compose file. Nothing runs inside AgentDiff: the exporter
writes three files to disk, and a node exporter's textfile collector
(or a Grafana datasource pointed at the file) does the serving. The
engine keeps its rule that no module outside `deepcompare/harness/`
opens a socket.

## What is exported

From an output directory as `batch`, `runs`, `fleet` or `evolve` wrote
it (`aggregate.json` + `report_*.json`, or `fleet.json`), or from one
trace:

| file | for | shape |
|---|---|---|
| `metrics.prom` | Prometheus, through a node exporter's textfile collector | the text exposition format: `# HELP`, `# TYPE`, one sample per line, labels sorted, no timestamps |
| `metrics.json` | Grafana's Infinity or JSON datasource | `{"samples": [{metric, labels, value}], "series": {metric: [{labels, value}]}, "families": {metric: {type, help}}, "notes": [...]}` |
| `metrics.csv` | Grafana's CSV datasource | one row per sample: `metric, value, <one column per label name>` |

Every metric family is prefixed `agentdiff_`, every family is a gauge
(every value is the state of a finished analysis, not a counter that
grows), and every `HELP` line says what the number is and what it is
not. The families, by what they read:

- **Per agent per task** (from the scorecard's per-run rows):
  `agentdiff_runs`, `_passes`, `_pass_rate`, `_pass_rate_lo`,
  `_pass_rate_hi`, `_steps_mean`, `_seconds_mean`, `_tokens_mean`,
  `_cost_usd_mean`, `_tool_calls_mean`, `_tool_errors_mean`,
  `_wasted_seconds_mean`; the same pooled over tasks as
  `agentdiff_agent_*`; and per run `agentdiff_run_success`, `_run_steps`,
  `_run_seconds`, `_run_tokens`, `_run_cost_usd`, `_run_tool_calls`,
  `_run_tool_errors`, `_run_wasted_seconds`, `_run_return`.
- **The training ground** (`aggregate.rl`): `agentdiff_return_mean` with
  `_lo`/`_hi`, `agentdiff_iqm` with `_lo`/`_hi`, `agentdiff_task_return_mean`,
  `agentdiff_task_return_delta`, `agentdiff_improvement` with `_lo`/`_hi`
  (the probability that a random run of `to` beats a random run of
  `from` on the same task), `agentdiff_task_improvement`; the reward
  audit as `agentdiff_reward_disagreements{scope}`,
  `agentdiff_reward_disagreement_pairs{scope}`,
  `agentdiff_reward_rank_agreement{basis}`; the critic as
  `agentdiff_critic_explained_variance{agent}` (`agent="_all"` is the
  overall figure).
- **Paired inference** (`runs`): `agentdiff_paired_diff` with
  `_lo`/`_hi`, `agentdiff_paired_sign_test_p`, `agentdiff_paired_tasks`.
- **The runs advisory**: `agentdiff_runs_per_task_min{level}`,
  `agentdiff_runs_per_task_median{level}`, `agentdiff_runs_floor{kind}`
  (the engine's 8 and 32).
- **Tools** (the pair reports' tool profiles): `agentdiff_tool_calls`,
  `_tool_errors`, `_tool_repeats`, `_tool_wasted_calls`,
  `_tool_wasted_seconds`, `_tool_seconds` summed over the reports, and
  `agentdiff_tool_max_identical_run` as a maximum.
- **Where the tokens went and what was fetched** (the `runs` aggregate's
  `budget` and `fetches` ledgers; a batch, whose aggregate carries no
  ledger, reads the pair reports' sides and the export notes it): per
  run `agentdiff_budget_tokens{agent,task,run,basis}` (`measured`,
  `estimated`, `unknown` — the basis the trace gave each step, never
  re-estimated), `_budget_cost_usd{agent,task,run}` only where a cost was
  recorded (unrecorded is not free and is no sample), `_budget_waste{agent,
  task,run,what}` (`after_last_evidence`, `in_errored_calls`,
  `in_repeats`; read from the pair reports' sides, one representative
  pair per task in a runs layout; a run with no evidence signal has no
  `after_last_evidence` sample); per agent `_budget_by_kind{agent,kind}`
  and `_budget_by_tool{agent,tool}`; `_budget_cap{source}` and
  `_budget_over_cap{agent,task,run}` when the analysis was given
  `--token-cap`; per run `agentdiff_fetches{agent,task,run,kind}`,
  `_fetches_errors`, `_fetches_repeats` and `_fetches_used{agent,task,run,
  use}` (`used`, `unused`, `unknown` — a recorded signal or none, never
  inferred from the answer). Every one is a count or a sum over recorded
  steps, so none has an interval family, and each `HELP` says so.
- **A fleet** (`fleet.json`): `agentdiff_fleet_rank`, `agentdiff_fleet_score`,
  plus the per-task and per-agent families above read from one run per
  task.
- **A lineage** (`aggregate.evolution`, from `evolve`): per generation
  `agentdiff_evolution_generation_index`, `_episodes`, `_passes`,
  `_pass_rate`, `_mean_return`, `_iqm` with `_lo`/`_hi`, `_iqm_balanced`
  with `_lo`/`_hi`, `_task_iqm`, `_task_pass_rate`, `_prompt_chars`,
  `_rules`, `_memory`, `_skills`, `_tools`; `agentdiff_evolution_budget{what}`
  and `_over_budget{generation,what}`; per step
  `agentdiff_evolution_step_verdict{from,to,step,verdict} = 1`,
  `_step_verdict_code` (improved 2, traded 1, flat 0, regressed −1,
  overfit −2, forgot −3, gamed −4, for a state timeline),
  `_step_improvement` with `_lo`/`_hi`, `_step_iqm_delta`,
  `_step_pass_rate_delta`, `_step_overfit_gap`, `_step_drift`,
  `_step_flag{flag} = 1`; `agentdiff_evolution_protected_touched{step,path,direction,source} = 1`;
  `agentdiff_evolution_verdicts{verdict}`, `_flags{flag}`,
  `_net_iqm_delta`, `_generations`, `_best{generation}` and
  `_recommended{generation,is_last}` (both valued at the IQM the engine
  ranks by).
- **The eval that evolved beside the lineage** (`aggregate.coevolution`,
  written by `evolve` and `coevolve`): `agentdiff_coevolution_eval_generations`
  and `_eval_generation_size{eval_gen,index,after_step,trigger_probe}`;
  `agentdiff_coevolution_metric{metric,generation,status,learned}` with
  `_lo`/`_hi` — every metric of the eval on every generation of the
  agent, computed with hindsight, so a learned metric is also shown on
  the generations before it was adopted; `_metric_status{metric,status,
  probe,adopted_step,confirmation} = 1`; the ledger as
  `agentdiff_coevolution_candidate{index,step,probe,metric,decision,failed} = 1`
  (every rejection kept, the validators it failed comma-joined in
  `failed`) with `_candidates{decision}` and `_candidates_by_probe{probe,
  decision}` as counts; what the evolved eval flags with hindsight as
  `_step_flag_delta{from,to,step,metric,learned,direction}` with
  `_lo`/`_hi`; `_hindsight{what}` (steps, changed, learned_flags,
  base_flags) and `_hindsight_lag{metric}` (agent steps between the first
  step a learned metric would have flagged and its adoption); the eval's
  own integrity as `_drift` (Jaccard distance from the base set),
  `_min_adjusted_alpha`, `_closures{kind}`; and
  `_recommended_agree{base,evolved}` (1 when both evals keep the same
  generation).
- **One run** (`agentdiff grafana trace.json`): the `agentdiff_run_*`
  totals, and per step `agentdiff_step_reward`, `_step_return_cum`,
  `_step_seconds`, `_step_tokens`, `_step_value`, `_step_advantage`, each
  with `step`, `type` and `name` labels. Reward and return appear only
  when the trace recorded rewards: a shaped reward needs the pair
  report's reading, and the exporter says so in a note rather than
  shaping one itself.

The full list, with every `HELP` line, is `deepcompare.grafana.FAMILIES`.
The dashboards are generated by `grafana/generate_dashboards.py`; edit
the generator, run it, and commit the JSON it writes — the tests read
the JSON and check that every panel names only exported families and
never draws a point without its interval.

## Label conventions

- `agent`, `task`, `run`, `tool`, `generation`, `family`: what the
  number is about. `from` and `to` name the two sides of a comparison
  (`from` is the baseline: agent A, or the parent generation).
- `synthetic="true"|"false"` on every sample about a trace, read from
  the trace's `harness` note (`SYNTHETIC: …`) or adapter. A demo can
  never masquerade as production on a dashboard; a fleet agent with no
  pair report in `fleet.json` reads `false` and the export notes it.
- `source="recorded"|"shaped"` on the reward families: whether the
  environment paid the reward or the engine shaped it from the labels.
- `level` on the runs advisory: `insufficient`, `below-floor`,
  `structured-ok`, `open-ended-ok`, the engine's own tiers.
- `index` on a generation (its position in the lineage) and `step` on
  a step, so a panel can sort by lineage order; `scope` and `basis` on
  the reward audit; `what` on a budget (a growth budget's artifact, or a
  token budget's waste); `basis` on a run's tokens (`measured`,
  `estimated`, `unknown`); `use` on a fetch; `kind` on a floor, a step or
  a fetch.

Every label value is escaped as the format requires; label names are
sorted, and families are written in a fixed order, so two exports of
the same directory are the same bytes.

## How intervals are represented

An interval is always three gauges: `agentdiff_x`, `agentdiff_x_lo`,
`agentdiff_x_hi`. The `HELP` of the bounds names the basis: a 95%
Wilson interval on the runs recorded for a rate; a stratified bootstrap
over the runs recorded (resampled within each task, fixed seed) for a
mean, an IQM or a probability of improvement; a normal interval from
the standard error over the tasks paired for the paired difference.
None of them is a claim about runs that were never made, and under the
floor the advisory names they are wide by construction. A bound that
the engine could not compute (a paired inference under two pairs) is
absent, with a note, never zero.

## Three ways into Grafana

**1. Prometheus, through the textfile collector.** Run
`agentdiff grafana <out_dir> -o <dir>` and point a node exporter at the
directory: `node_exporter --collector.textfile.directory=<dir>`. It
serves every `*.prom` file there on `:9100/metrics`; Prometheus scrapes
it; the samples carry no timestamps, so every scrape re-reads the file
as the current state. Re-run the exporter after a new analysis and the
series step to the new values, which is the one thing a time axis is
good for here: two analyses scraped in sequence draw a step.

**2. Infinity or JSON datasource, no Prometheus.** Serve `metrics.json`
from any file host and query it with the Infinity datasource (type
JSON, root selector `samples` for the flat rows or `series.<metric>` for
one family); the labels are nested objects and the `Extract fields`
transformation flattens them. The provisioned dashboards query
Prometheus; a JSON-only setup rebuilds a panel from the same family
names.

**3. CSV datasource.** `metrics.csv` has one column per label name in
use, so a `Filter data by values` transformation on `metric` picks a
family and the label columns group it.

## The compose path, step by step

1. `python3 -m deepcompare runs demo/rl/train -o /tmp/train` (or any
   `batch`, `fleet` or `evolve` output; `evolve demo/evolve/lineage -o
   /tmp/evo` for the Evolution dashboard).
2. `python3 -m deepcompare grafana /tmp/train -o grafana/out` — writes
   `grafana/out/metrics.prom` and the JSON and CSV beside it. For the
   One run dashboard: `python3 -m deepcompare grafana <trace.json> -o
   grafana/out` (the files are overwritten; one directory holds one
   export).
3. `cd grafana && docker compose up`. The compose file brings up
   `prom/node-exporter` with `--collector.textfile.directory=/textfile`
   and `./out` mounted there, `prom/prometheus` scraping it with
   `prometheus.yml`, and `grafana/grafana` with `provisioning/` mounted:
   the datasource (`uid: prometheus`, default) and a file provider that
   loads `dashboards/*.json` into a folder named AgentDiff.
4. Open `http://localhost:3000` (anonymous viewer; `admin` /
   `agentdiff` to edit). The AgentDiff folder holds the seven dashboards;
   the first scrape lands within the 15-second interval.

Without docker: run any node exporter and Prometheus, import
`grafana/dashboards/*.json` through Dashboards → New → Import, and pick
the Prometheus when asked; the dashboards carry the `__inputs` block
Grafana's importer reads, and their `DS_PROMETHEUS` variable resolves
the same reference when the file is provisioned instead.

The compose file and the provisioning YAML are checked by
`tests/test_grafana.py` (they agree with each other and with the
dashboards' datasource reference); `docker compose config` accepts
the compose file. The stack itself was not brought up in the
environment that wrote this page, which has no docker daemon.

## The dashboards

Each dashboard is one question, and each panel's title is a question.

| uid | question |
|---|---|
| `agentdiff-agents` | Which agent passes which task, at what cost, and can the runs recorded tell? Pass rate per agent per task with its interval (a table and a line panel), the paired difference against zero with its interval, seconds, steps, tokens, cost, wasted seconds and tool errors per run, and the runs advisory as a stat that turns amber below the floor. |
| `agentdiff-tools` | Which tool did each agent lean on, and where did it burn time? Calls, errors, repeats, wasted calls, wasted seconds per tool per agent, and the longest run of identical calls. |
| `agentdiff-training` | Is the new policy better than the old, and would the runs recorded know? IQM and mean return per policy with intervals, the probability of improvement with its interval and the 0.5 line, the per-task probabilities and deltas that an average hides, the reward audit's disagreement counts and the critic's explained variance. |
| `agentdiff-evolution` | Did each step of the lineage help, and which generation should be kept? IQM (pooled and task-balanced) with intervals per generation in lineage order, pass rate per generation and per task, prompt, rules and memory against their budgets, the step verdicts as a state timeline, the protected paths touched and the flags as tables, best and recommended as stats. |
| `agentdiff-run` | What did this run earn, step by step, and where did the time go? Reward per step, return so far, latency and tokens per step, the step table. |
| `agentdiff-evals` | What did the eval learn from watching the lineage, and can it be trusted? How many generations the eval grew, candidates tested against kept, whether the base and the evolved eval keep the same generation, every metric on every generation with its interval, what the evolved eval flags with hindsight step by step, the ledger of candidates with the validators each failed, candidates by probe, the lag of each learned metric, drift from the base and the strictest adjusted level, loop closures. |
| `agentdiff-budget` | Where did the tokens go, and what was fetched and wasted? Tokens counted by the basis the trace gave them, per agent by kind of step and by tool, the three wastes, the cost where recorded, the cap and the runs over it, fetches by kind, errors and repeats, and whether what came back was used. Every panel is a count or a sum over recorded steps, so none draws an interval and each description says so. On the runs demo: 255 315 tokens, every one measured (policy-v1 142 031, policy-v2 113 284; 71 224 of policy-v1's on `read` steps); 3 000 fetches, 90 errored, 327 repeated, 390 recorded as used and 2 610 as not, none unknown; no cost recorded and no cap given. |

Panels that draw a point draw its interval beside it: dashed lines in
the time-series panels, `lo` and `hi` bars beside the point in the bar
charts, `lo` and `hi` columns in the tables; the budget dashboard's
numbers are counts, which have none, and say so. A panel that showed a mean
without its interval would be exactly the reading the training ground
exists to prevent: the difference between two policies at five runs a
task is usually inside the interval, and a bare number on a dashboard
turns "these runs do not separate them" into "the new one is better".

## What the dashboards do not show

- **A cause.** Every number here is a count, a sum or a statistic over
  the runs recorded; the reading of why a run failed, the decisive
  step, the diff of the artifacts, the narrative, are on the report
  page (`report.html`), not in a gauge.
- **A population.** The intervals describe how much these runs'
  statistic moves when they are redrawn. Below eight runs per task the
  advisory panel is amber and the intervals are wide by construction;
  an overlap means "not separated", never "equal".
- **A mean without its interval.** By design.
- **Time.** The samples have no timestamps and one export is one
  state; the time axis only ever shows the steps between successive
  exports. Generations are on the x-axis of the Evolution bar charts by
  lineage index, not by time.
- **A shaped reward for a single trace.** The One run dashboard shows
  recorded rewards only; the shaping needs the pair report's labels.
- **Cost that was not recorded.** A cost of 0 is an unrecorded cost, and
  the HELP and the panel say so.
