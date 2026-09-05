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
    "version": "string — e.g. 'v2'"
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
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_usd": 0.0,
    "latency_s": 0.0
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
  "a": { "agent": {...}, "outcome": {...}, "totals": {...}, "steps": [...] },
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

`dimension_scores` are 0-1 (1 = best in fleet) so a client can re-weight the
composite live. `wasted_tool_calls`/extra steps = steps in divergence regions
that did not change the outcome.

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

The full definition of each object, with the reasoning behind every
field, is in [`docs/CHANGELOG.md`](docs/CHANGELOG.md) under the version
that introduced it (diagnosis v27–v31, v33, v41; reading v33; verdict
card v37; replay v40).

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
| `feedback <report.json|dir> [-o signal.json] [--jsonl pairs.jsonl]` | the loop back: step labels, preference pairs, prompt suggestions |
| `watch [traces/] [--demo TRACES --pace S --loop]` | serve the page live (localhost, server-sent events): running agents stream in, finished pairs become the story |
| `replay REPORT --provider …` | verify the decisive step by re-execution; writes the verdict back |
| `why REPORT --provider …` | narrate through a provider under the covenant |
| `narrate` / `convert` / `check` | brief in/out by hand; foreign traces in; validate a trace |

