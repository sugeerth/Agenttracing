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

## Tool-call diff (added to pairwise reports)

Each alignment entry pairing two `tool_call` (or `search`) steps may carry:

```json
"tool_diff": {
  "name_a": "regex_extract", "name_b": "regex_extract", "same_tool": true,
  "args_a": {"pattern": "..."}, "args_b": {"pattern": "..."},
  "changed": [{"key": "pattern", "a": "...", "b": "..."}],
  "only_a": ["first_match"], "only_b": [],
  "raw_diff": [["eq", "text "], ["del", "old"], ["ins", "new"]]
}
```

`args_*` parsed heuristically from `name(k=v, ...)` style inputs; `raw_diff`
is a token-level LCS diff of the raw inputs as a fallback for unparseable args.

When both sides call the same tool with byte-identical input the engine emits
the short form `{"same_tool": true, "identical": true}` **without** `name_a` /
`name_b` / `args_*`; consumers should fall back to the steps' own names.

## Step-level evaluation detail (pairwise reports, v5)

Every alignment entry pairing two present steps carries an `eval` object:

```json
"eval": {
  "similarity": {"type_match": true, "name_jaccard": 0.5, "input_jaccard": 0.82},
  "delta": {"tokens": 40, "latency_s": 1.2, "cost_usd": 0.0006},
  "quality": {"a": "good", "b": "bad", "verdict": "equal | a_degraded | b_degraded"},
  "propagation": {"a": 0.0, "b": 0.45}
}
```

`eval` is present **only on alignment rows with both sides present** — one-sided
(`a_only` / `b_only`) rows have nothing to compare and carry no `eval`.

`delta` is B minus A. `propagation` (only when one agent failed) is the word
Jaccard between this step's input and the root divergent step's output on each
side — how much of the root mistake's content this step carries forward.

Report-level answer evaluation:

```json
"answer_eval": {
  "expected": "string|null",
  "diff_ab": [["eq", "..."], ["del", "..."], ["ins", "..."]],
  "a_vs_expected": {"coverage": 0.8, "verdict": "match | partial | mismatch"},
  "b_vs_expected": {"coverage": 0.2, "verdict": "mismatch"}
}
```

`diff_ab` is the token-level diff of A's final answer against B's.
`coverage` is the fraction of expected-answer tokens present in the answer
(numeric tokens like "4.82" kept whole). Verdicts: `match` >= 0.6 coverage
with every digit-bearing expected token present (a wrong number caps the
verdict at `partial`), `partial` >= 0.3, else `mismatch`; `null` expected
gives verdict "unknown".

## Success analysis (pairwise reports, v6) — the positive mirror

Failure attribution explains what went wrong; `success_analysis` explains what
the winner did *right*, decision by decision.

```json
"success_analysis": {
  "winner": "a | b | null",
  "basis": "outcome | efficiency",
  "winning_decisions": [
    {
      "step_index": 2, "agent": "atlas-v2", "kind": "retrieval",
      "decision": "selected the official investor-relations release",
      "counterpart": "selected a commentary blog (bolt-v3)",
      "why": "primary source held the correct figure; avoided circular corroboration",
      "impact": {"avoided_extra_steps": 5, "avoided_tokens": 682,
                 "avoided_latency_s": 8.5, "avoided_failure": true}
    }
  ],
  "narrative": "plain-language explanation of why the winner won"
}
```

`winner`: the successful agent when exactly one failed (`basis: "outcome"`);
the leaner side when both succeeded but diverged (`basis: "efficiency"`);
null when trajectories are equivalent. One winning decision per divergence
region, from the winner's side of it; `impact` is the loser's downstream
extras re-read as what the winner avoided.

Aggregate-level playbook — winning habits generalized across tasks:

```json
"playbook": [
  {"habit": "Prefer primary/official sources over commentary",
   "kind": "retrieval", "agents": ["atlas-v2"],
   "evidence": "decided 3 tasks (t01, t02, t06)",
   "impact": "avoided 2 failures, saved 2,745 tokens and 34.8s"}
]
```

Derived by grouping winning decisions by kind across a batch; only habits
with non-trivial impact are emitted.

## Semantic analysis (pairwise reports, v7)

Lexical alignment measures wording; the `semantic` object measures meaning,
via two complementary approaches: corpus-weighted similarity (TF-IDF cosine
over both trajectories' step texts) and claim-level provenance (typed,
meaning-bearing facts traced through the steps).

```json
"semantic": {
  "methods": ["tfidf_cosine", "claim_provenance"],
  "rows": [
    {"row": 0, "a_index": 0, "b_index": 0, "lexical": 0.93, "semantic": 0.97}
  ],
  "first_semantic_break": 2,
  "claims": [
    {"id": "c1", "kind": "money | percent | duration | version | cve | url | date | number",
     "value": "$4.82 billion", "normalized": "4.82e9",
     "matches_expected": true,
     "a_steps": [2, 3, 4], "b_steps": [],
     "origin": {"agent": "a", "step": 2, "source": "ir.acmecorp.com"}}
  ],
  "conflicts": [
    {"kind": "money", "a_claim": "c1", "b_claim": "c2",
     "summary": "A carried $4.82 billion (from ir.acmecorp.com); B carried $4.5 billion (from financeblog.net); expected: $4.82 billion."}
  ],
  "narrative": "2-3 sentences: where meaning (not just wording) diverged and which claims decided the outcome"
}
```

- `rows`: one entry per alignment row with both sides present; `lexical` is
  the alignment similarity, `semantic` the TF-IDF cosine of the paired step
  texts. A large lexical-semantic gap flags "same words, different meaning"
  (or the reverse).
- `first_semantic_break`: first row where semantic similarity < 0.5, null if none.
- `claims`: deduplicated typed facts found in step inputs/outputs and answers;
  `a_steps`/`b_steps` list the step indices carrying the claim; `origin` is
  the earliest carrying step (with URL domain when extractable);
  `matches_expected` non-null only when the expected answer contains a
  comparable claim of the same kind.
- `conflicts`: pairs of same-kind claims where the agents carried different
  values into their answers.

### Extended semantic suite (v7, continued)

The `semantic` object additionally carries:

```json
"intents": {
  "a": [{"step": 0, "intent": "frame | acquire | verify | transform | decide | commit"}],
  "b": [{"step": 0, "intent": "frame"}],
  "missing": {"a": [], "b": ["verify"]}
},
"grounding": {
  "a": {"claims_total": 2, "claims_grounded": 2, "score": 1.0, "ungrounded": []},
  "b": {"claims_total": 2, "claims_grounded": 1, "score": 0.5,
        "ungrounded": [{"claim": "c3", "value": "..."}]}
},
"independence": [
  {"claim": "c2", "agent": "b", "sources": ["financeblog.net", "moneymirror.com"],
   "circular": true,
   "evidence": "the corroborating source's text cites financeblog.net itself"}
],
"contradictions": [
  {"agent": "b", "steps": [7, 8], "kind": "money",
   "values": ["$4.5 billion", "$4.82 billion"],
   "summary": "bolt-v3 carried conflicting money values within its own trajectory"}
]
```

- `intents`: every step classified by *what it does for the process* —
  frame (plan/scope), acquire (search/retrieve/read), verify (cross-check,
  confirm, validate), transform (compute/convert), decide (select/judge),
  commit (final answer). Derived from step type + text cues. `missing` lists
  intents absent from one side but present in the other — "B never verified"
  is a process-grammar finding, not a wording one.
- `grounding`: for each side, the fraction of its final-answer claims that
  trace to some step output (provenance exists). Ungrounded answer claims are
  hallucination flags.
- `independence`: when a side corroborates a claim with a second source,
  checks whether that source's text itself cites the first (circular
  corroboration — two quotes, one voice).
- `contradictions`: same-kind claims with different values inside ONE agent's
  trajectory — internal inconsistency the agent never resolved.

Aggregate gains a per-agent semantic profile:

```json
"semantic_profile": {
  "a": {"verification_rate": 0.875, "grounding": 0.95,
        "circular_incidents": 0, "contradictions": 0},
  "b": {"verification_rate": 0.25, "grounding": 0.71,
        "circular_incidents": 1, "contradictions": 1},
  "narrative": "cross-task semantic comparison in plain language"
}
```

## Counterfactual replay (pairwise reports, v8)

When a failure was attributed, the report estimates the counterfactual: the
failing agent adopts the winner's decision at the root divergence and inherits
its suffix.

```json
"counterfactual": {
  "premise": "had bolt-v3 made atlas-v2's decision at step 2",
  "splice": {"prefix_steps": [0, 1], "adopted_from": "a", "adopted_steps": [2, 3, 4]},
  "estimate": {
    "outcome": "success",
    "steps": 5, "steps_delta": -5,
    "tokens": 840, "tokens_delta": -682,
    "latency_s": 9.95, "latency_delta_s": -8.47,
    "cost_usd": 0.0051, "cost_delta_usd": -0.0039
  },
  "confidence": "high | medium | low",
  "narrative": "plain-language what-if with the numbers"
}
```

`confidence`: high when the shared prefix is identical (all match rows before
the root) and the divergence is the attributed cause; medium when the prefix
contains drift; low otherwise. Estimates come from splicing the winner's
post-divergence suffix onto the failing agent's prefix (token/latency/cost
summed from the actual steps).

## Regression gate (CLI, v8)

`python -m deepcompare gate BASELINE_DIR CANDIDATE_DIR [thresholds] [-o out/] [--markdown gate.md]`

Pairs traces by task id across two directories (e.g. agent v1 vs v2 runs),
compares them, and evaluates gate checks:

- success rate must not drop more than `--max-success-drop` (default 0)
- mean cost must not rise more than `--max-cost-increase` (fraction, default 0.10)
- mean latency must not rise more than `--max-latency-increase` (default 0.25)
- no NEW failure-origin category may appear (disable with `--allow-new-failure-modes`)

Exit code 0 = pass, 1 = gate failed, 2 = usage/data error. `gate.json` and an
optional shareable `gate.md` summary (verdict table, per-check numbers, top
divergences with attributions and counterfactual savings) are written to `-o`.

## Multi-run stability (v9)

Trajectories may carry an optional `"run_id"` (default `"r1"`); multi-run
trace files are named `<task_id>__<agent_name>__<run_id>.json`. With N runs
per (agent, task), the batch payload gains:

```json
"stability": {
  "runs_per_agent": {"atlas-v2": 3, "bolt-v3": 3},
  "per_task": [
    {"task": "t01_acme_revenue",
     "a": {"successes": 3, "runs": 3, "verdict": "stable-pass | stable-fail | flaky",
           "token_cv": 0.04, "latency_cv": 0.09},
     "b": {"successes": 0, "runs": 3, "verdict": "stable-fail",
           "token_cv": 0.12, "latency_cv": 0.15},
     "divergence_reproducibility": {"rate": 1.0, "kind": "retrieval",
                                    "verdict": "systematic | variable | none"}}
  ],
  "flaky_tasks": {"atlas-v2": [], "bolt-v3": ["t02_cve_libfoo"]},
  "medoid_runs": {"t01_acme_revenue": {"a": "r2", "b": "r1"}},
  "narrative": "which failures reproduce, which are noise"
}
```

- `verdict`: stable-pass (all runs succeed), stable-fail (none), flaky (mixed).
- `divergence_reproducibility`: over all run-pair comparisons for the task,
  the fraction whose FIRST divergence agrees in kind (and roughly location);
  `systematic` >= 0.8, `variable` >= 0.3, else `none`.
- `medoid_runs`: the most-representative run per side (minimum summed
  step-type-sequence distance to that side's other runs) — the pair shown in
  the compare view; `token_cv`/`latency_cv` are coefficients of variation.

## Task signal (aggregate, v9)

```json
"task_signal": [
  {"task": "t06_bls_unemployment", "difficulty": 0.5,
   "discrimination": 1.0, "note": "separates the agents: one side always fails it"}
]
```

`difficulty` = 1 − mean success across sides/runs; `discrimination` = how
strongly the task separates the two agents (success gap, plus normalized
cost/latency gap when successes tie). Sorted most-discriminating first.

## Trace adapters (CLI, v9)

`python -m deepcompare convert --format otel|openai IN.json -o OUT_DIR/`
converts foreign trace formats to SCHEMA trajectories: `otel` reads OpenTelemetry
GenAI-convention spans (gen_ai.* attributes), `openai` reads a chat-completions
style message array with tool calls; both use heuristic step typing
(tool name / content cues → search/retrieve/read/tool_call/reason) and emit
warnings for unmapped items rather than failing.

## Behavioral similarity and agent selection (v10)

`python -m deepcompare select TRACESDIR -o out/` writes `select.json` and a
lightweight `select.html`, with payload `{"similarity": {...}, "routing": {...}}`.

Similarity is measured on four facets rather than one number, because the
combinations imply different decisions: same outcomes + same cost = duplicate;
same outcomes + cost gap = redundancy; different outcomes = complementarity.

```json
"similarity": {
  "agents": [{"name": "...", "model": "...", "success_rate": 1.0,
              "mean_cost_usd": 0.0, "mean_tokens": 0.0, "mean_latency_s": 0.0,
              "mean_steps": 0.0, "tool_usage": {"web_search": 8}}],
  "facet_weights": {"outcome": 0.40, "process": 0.25,
                    "tools": 0.20, "resources": 0.15},
  "pairs": [{"a": "...", "b": "...", "composite": 0.93,
             "facets": {"outcome": 1.0, "process": 0.88, "tools": 0.95,
                        "resources": 0.81, "shared_tasks": 8}}],
  "clusters": [{"members": ["..."], "size": 3, "success_rate": 1.0,
                "cheapest": "...", "representative": "..."}],
  "redundancies": [{"keep": "...", "drop": "...", "similarity": 0.90,
                    "cost_gap": 0.31, "saving_per_task_usd": 0.0025,
                    "also_dominated_by": ["..."], "summary": "..."}],
  "complementarities": [{"a": "...", "b": "...", "union_coverage": 1.0,
                         "best_alone_coverage": 0.75, "gain_tasks": 2,
                         "only_a": ["t05"], "only_b": ["t01"], "summary": "..."}],
  "narrative": "..."
}
```

- `outcome` = fraction of shared tasks where both agents got the same result.
- `process` = mean normalized LCS over step-type sequences (trajectory shape).
- `tools` = cosine over tool-usage counts.
- `resources` = mean of `min/max` ratios for tokens, latency and steps, so the
  measure is scale-free.
- `redundancies` carries **one row per droppable agent** (its best replacement),
  and only when outcomes match on every shared task and the cost gap exceeds 15%.

```json
"routing": {
  "tasks": ["t01", "..."], "agents": 33,
  "per_task": [{"task": "t01", "solvers": ["..."], "solver_count": 21,
                "champion_solves": true, "cheapest_solver": "...",
                "cheapest_cost_usd": 0.004, "champion_cost_usd": 0.005}],
  "best_single": {"agent": "...", "coverage": 1.0,
                  "covered_tasks": ["..."], "cost_usd": 0.045},
  "oracle": {"coverage": 1.0, "cost_usd": 0.039, "coverage_headroom": 0.0,
             "cost_saving_usd": 0.0057, "note": "ceiling assuming per-task ..."},
  "portfolios": [{"k": 2, "members": ["..."], "coverage": 1.0,
                  "covered_tasks": ["..."], "cost_usd": 0.043,
                  "search": "exact | greedy"}],
  "unique_solves": {"agent": ["t04"]},
  "narrative": "..."
}
```

`oracle` is a **ceiling, not a policy** — it assumes per-task knowledge of which
agent will succeed. The gap between `best_single` and `oracle` is the headroom a
router could win, and no more. `portfolios[].search` is `greedy` for fleets
larger than 14 agents, so the reader knows the search was not exhaustive.

## Model telemetry (Step, optional, v12)

A step may carry the model's own signal alongside what the agent did:

```json
"model": {
  "confidence": 0.86,
  "min_token_confidence": 0.41,
  "entropy": 0.72,
  "tokens_scored": 120,
  "temperature": 0.7,
  "source": "provider logprobs | synthetic-demo | ..."
}
```

`confidence` and `min_token_confidence` are probabilities in [0, 1] — the mean
and minimum per-token probability over the text the step generated (from
provider logprobs: OpenAI `logprobs`, vLLM/TGI `logprobs` for open-weight
models, etc.). `entropy` is mean per-token entropy in nats. All fields are
optional; steps without a `model` block are simply unscored. No weights or
model internals are required — only the per-token probabilities an inference
API already returns.

Pairwise reports gain an `uncertainty` object:

```json
"uncertainty": {
  "available": true,
  "a": {"series": [0.92, null, 0.88], "mean_confidence": 0.90,
        "min_confidence": 0.88, "mean_entropy": 0.2,
        "min_token_confidence": 0.7, "steps_scored": 2},
  "b": {"...": "..."},
  "signal": {
    "failed_agent": "b", "root_cause_step": 2,
    "confidence_at_root": 0.88, "baseline_confidence": 0.85,
    "drop": -0.03, "verdict": "flagged | silent", "lead_steps": 0,
    "mitigation": "..."
  },
  "calibration": {"confident_when_wrong": true,
                  "confidence_at_wrong_step": 0.88},
  "narrative": "..."
}
```

Field notes (these tripped up a consumer, so they are stated explicitly):

- `drop` is `baseline_confidence − confidence_at_root`. **Positive means
  confidence fell below the run's own baseline**; a negative value means the
  model was *more* sure at the failing step than elsewhere. Present it with
  that sign convention or flip it explicitly.
- `signal` is `null` whenever no failure was attributed (both runs succeeded,
  both failed, or the failing step carries no telemetry), even though
  `available` is `true`. Consumers must handle the null.
- `calibration` is likewise `null` without a signal.

`verdict` is the operational finding, not a score:

- **flagged** — confidence fell at least 0.15 below the run's own baseline at
  the step that caused the failure (`lead_steps` says how many steps earlier
  the drop began). A runtime confidence gate would have caught this run.
- **silent** — the model was as confident there as anywhere. No threshold on
  its own uncertainty helps; only external verification catches this class.

Aggregate gains `calibration`: per agent, how many of its failures were
flagged versus silent, and a verdict of `supervisable` (≥50% flagged) or
`silent-failing`. An agent that fails silently cannot be supervised by
thresholding its own confidence however good its success rate looks.

## Systematic issues (aggregate, v13)

A batch produces one divergence per place two runs parted company — dozens of
findings on a real task set. `aggregate["issues"]` collapses them into
recurring problems so the output is a short list of decisions, not a long list
of incidents.

```json
"issues": {
  "issues": [
    {"id": "retrieval/a:retrieve.select_result/b:retrieve.select_result/q:b",
     "kind": "retrieval",
     "title": "Selects a lower-quality source at \"select_result\"",
     "severity": "critical | major | minor",
     "recurring": true, "suppressed": false,
     "tasks": ["t01", "t02", "t06"], "agents": ["bolt-v3"],
     "occurrence_count": 3, "failures_caused": 2,
     "extra_steps": 10, "extra_tokens": 2136, "extra_latency_s": 34.8,
     "occurrences": [{"task": "t01", "rank": 1, "caused_failure": true,
                      "extra_tokens": 682, "summary": "..."}],
     "example": {"...": "the costliest fatal occurrence"},
     "summary": "..."}
  ],
  "active": 6, "suppressed": 0, "total_divergences": 8,
  "counts": {"critical": 3, "major": 0, "minor": 3},
  "narrative": "..."
}
```

The **fingerprint** (`id`) is a stable, readable signature:
`kind/a:<type>.<name>/b:<type>.<name>[/q:<sides>]`. Volatile detail is
normalized away (digits and URLs collapse), so the same behavior on different
tasks clusters together; `q` records only *which side* was annotated poor, not
whether it was `weak` or `bad`, since those are severities of one behavior.

Severity: `critical` when the issue caused any failure, `major` when it wasted
≥500 tokens, else `minor`.

### Suppression

Fingerprints can be listed in a `.agentdiffignore` file (beside the traces or
in the working directory) to mark issues a team has judged benign:

```
# known benign — our agent legitimately re-queries on this corpus
stopping/a:none/b:reason.reason
retrieval/*
```

One pattern per line, `#` starts a comment, a trailing `*` is a prefix match.
Suppressed issues are **still reported and marked**, and excluded only from the
headline counts — silently dropping findings is how a gate stops being
trustworthy.

## Gate statistics (v14)

The `success_rate_drop` check carries a noise floor so a gate cannot fire
confidently on a single flipped task:

```json
{"name": "success_rate_drop", "pass": false,
 "baseline": 1.0, "candidate": 0.75, "threshold": 0.0,
 "baseline_ci": [0.676, 1.0], "candidate_ci": [0.409, 0.927],
 "bootstrap": {"observed": 0.25, "low": 0.0, "high": 0.625,
               "significant": false, "samples": 2000},
 "significant": false,
 "detail": "... The 25.0% drop is within noise for 8 task(s) ..."}
```

- `baseline_ci` / `candidate_ci` are Wilson score intervals — chosen over the
  normal approximation because short eval suites live at the extremes (0/8 and
  8/8 are common) where the normal approximation breaks.
- `bootstrap` resamples tasks **in pairs**, since both agents ran the same
  suite; resampling independently would discard that pairing and overstate the
  uncertainty. Fixed seed, so a gate decision never changes between runs on
  identical data.
- `significant` is true only when the whole interval sits above zero.

`pass` (the threshold decision) and `significant` (the evidence strength) are
reported **separately and deliberately**: a team's policy may be "block on any
drop" even when the evidence is thin, and the gate should say both things
rather than conflate them. `pass_at_k` is available for multi-run suites — the
strict reading of reliability, where passing 2 of 3 runs is not 67% reliable
if a user needs it to work three times running.

## Shapley credit assignment (pairwise reports, v15)

When a run goes wrong in more than one place, summing each divergence's
downstream cost double-counts — later divergences inherit the extra work
earlier ones created. `report["shapley"]` allocates the gap fairly instead:

```json
"shapley": {
  "available": true, "metric": "tokens", "method": "exact",
  "loser": "bolt-v3", "winner": "atlas-v2", "regions": 2,
  "total_saving": 691.0,
  "allocations": [
    {"region": 0, "rank": 1, "kind": "retrieval", "summary": "...",
     "alignment_rows": [2, 3], "shapley": 691.0, "share": 1.0,
     "caused_failure": true}
  ],
  "efficiency_check": 0.0,
  "outcome_attributable": true,
  "outcome_note": "...",
  "narrative": "..."
}
```

Each divergence region is a player; a coalition's value is what the run would
have cost taking the reference path at those regions. **The value function
never simulates anything** — a coalition's trajectory is assembled from steps
that were actually recorded on one side or the other, and costed with the
observed per-token rates. `efficiency_check` is `sum(allocations) −
total_saving` and must be ~0: the Shapley efficiency axiom, checked
numerically on every report.

Exact enumeration runs while there are ≤ 12 regions; beyond that the field
reports `available: false` with a reason rather than silently sampling.

`outcome_attributable` is true **only** when exactly one divergence was
causal. Splitting a binary outcome across several decisions would require
counterfactual re-runs the engine cannot perform on logged traces, so it
declines rather than guessing.

## Attribute-based failure analysis (v15)

Which *behavioural attributes* travel with failure across a corpus:

```json
{
  "runs": 264, "failures": 30, "notable": 2,
  "attributes": [
    {"attribute": "poor_quality_step",
     "phrasing": "the run contains a step annotated weak or bad",
     "with": {"runs": 101, "failures": 30, "failure_rate": 0.297,
              "ci": [0.216, 0.393]},
     "without": {"runs": 163, "failures": 0, "failure_rate": 0.0,
                 "ci": [0.0, 0.023]},
     "lift": 0.297, "interval": {"...": "paired bootstrap"},
     "notable": true, "measurable": true}
  ],
  "caveat": "...", "narrative": "..."
}
```

Attributes are binary predicates over a trajectory (no verification step, no
plan step, a weak/bad step, a low-confidence step, many tool calls, a long
trajectory, repeated searches). `lift` is the failure-rate difference between
runs that have the attribute and runs that do not; `notable` requires |lift| ≥
0.25 **and** at least 2 runs on each side, so tiny groups cannot produce
confident findings. A predicate returning None (e.g. confidence on a run with
no telemetry) excludes that run from that attribute rather than counting it as
false.

**These are associations, never causes**, and the module says so in its own
output: an attribute may travel with failure because it causes it, because a
common factor causes both, or because harder tasks provoke it. The honest use
is triage — where to look next — not a conclusion to act on blindly.
