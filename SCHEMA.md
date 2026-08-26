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
  "_note": "splice-Shapley: exact with respect to the splice surrogate, not
            with respect to the agent — see below",
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

The honest name for this is **splice-Shapley**: the allocation is exact with
respect to the splice surrogate (adopting the reference path at a decision
yields the steps the reference actually took), not with respect to the agent,
which would require re-running it. Rigorous causal replay for agents does
exist in the literature but re-executes the agent, which this engine — seeing
only logged traces — deliberately does not do.

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

Each row also carries a **stratified** lift, and this is a guard rather than a
refinement:

```json
"stratified": {"lift": -0.65, "strata": 6,
               "method": "Mantel-Haenszel pooled within-task risk difference"},
"reverses_under_stratification": false
```

Task difficulty confounds every marginal association: hard tasks both provoke
different behavior and cause more failures, and the trajectory-length signal in
agent traces is documented to *reverse* once difficulty is controlled. The
stratified figure compares runs only **within the same task**, pooling strata
with Mantel-Haenszel weights (`n_with · n_without / n`); strata where every run
falls on one side carry no information and are skipped. An attribute whose sign
flips is flagged, forced to `notable: false`, and named in the narrative —
reported **whether or not anything else was notable**, since "the only strong
signal is an artifact" is precisely when the reader needs telling.

Attributes are binary predicates over a trajectory (no verification step, no
plan step, a weak/bad step, a low-confidence step, many tool calls, a long
trajectory, repeated searches). `lift` is the raw failure-rate difference
between runs that have the attribute and runs that do not; `notable` requires
|lift| ≥ 0.25 **and** at least 2 runs on each side, so tiny groups cannot
produce confident findings. A predicate returning None (e.g. confidence on a run with
no telemetry) excludes that run from that attribute rather than counting it as
false.

**These are associations, never causes**, and the module says so in its own
output: an attribute may travel with failure because it causes it, because a
common factor causes both, or because harder tasks provoke it. The honest use
is triage — where to look next — not a conclusion to act on blindly.

## Joint attribute model (aggregate, v16)

Marginal lifts cannot tell several signals from one signal counted several
times. `aggregate["attributes_joint"]` fits failure on all measurable
attributes at once, so each coefficient reads "holding the others fixed":

```json
"attributes_joint": {
  "available": true, "runs": 264, "failures": 30, "parameters": 7,
  "intercept": -3.9, "ridge": 1.0, "iterations": 8,
  "converged": true, "reliable": true,
  "coefficients": [
    {"attribute": "poor_quality_step", "phrasing": "...",
     "coefficient": 3.146, "odds_ratio": 23.246,
     "separates": false, "direction": "raises"}
  ],
  "dropped": [{"attribute": "low_confidence_step",
               "reason": "not measurable for every run"}],
  "method": "ridge-penalised logistic regression (IRLS, fixed iteration cap, deterministic)",
  "caveat": "...", "narrative": "..."
}
```

Implementation notes that matter for trusting the numbers:

- **Deterministic** — fixed iteration cap, fixed tolerance, no random start and
  no sampling, so a gate may depend on the result.
- **Ridge-penalised** (slopes only; the intercept is unpenalised so the base
  rate is not shrunk). Eval corpora routinely contain a perfectly separating
  attribute, where unpenalised maximum likelihood diverges silently. Separation
  is *detected* and reported per attribute via `separates`, with the narrative
  noting that such a coefficient's magnitude is set by the penalty rather than
  the data.
- **Attributes that cannot be measured for every run are dropped, not imputed**,
  and named in `dropped`.
- `reliable` is false when there are fewer than 5 runs per fitted parameter, and
  the narrative says so rather than letting a thin fit read as authoritative.

Read alongside `attributes` rather than instead of it: the joint coefficients
control for the other *measured* attributes only — not for task difficulty,
which is what the stratified marginal lift handles. Where the two disagree,
that disagreement is itself the finding.

## Reference profiles — a base style (v18)

`python -m deepcompare profile TRACESDIR [--build-from DIR] -o out/`

Every other comparison needs a partner run. A **profile** removes that: a norm
distilled from many runs, against which any single run can be scored alone.

```json
{"name": "t01_acme_revenue", "tasks": ["t01_acme_revenue"],
 "runs_used": 27, "runs_excluded": 6, "successes_only": true,
 "thin_evidence": false,
 "canonical_path": ["plan", "search", "retrieve", "read", "answer"],
 "expected_step_types": ["answer", "plan", "read", "retrieve", "search"],
 "step_type_mix": {"search": 31}, "tool_mix": {"web_search": 31},
 "bands": {"tokens": {"median": 840.0, "low": 831.0, "high": 1105.0,
                      "min": 800.0, "max": 1274.0}},
 "caveat": "..."}
```

- Built from **successful runs only** by default — a norm assembled from
  failures would make repeating them "normal". `--include-failures` overrides.
- `canonical_path` is a **medoid**, an actually-recorded path, never an
  averaged one that nobody took.
- Bands are median plus interquartile range, so "outside the norm" means
  outside where the middle half of runs sat.
- A profile from fewer than 5 runs sets `thin_evidence`, and that caveat is
  repeated in every score built on it.

Scoring a run needs no partner:

```json
{"agent": "bolt-v3", "task": "t01", "verdict": "on-profile | costly | off-profile | failed",
 "path_similarity": 0.67, "tool_similarity": 1.0,
 "missing_step_types": [], "unexpected_step_types": [],
 "measures": {"tokens": {"value": 1522, "position": "above", "phrasing": "...",
                         "band": {"...": 0}}},
 "outside_band": ["tokens"], "narrative": "..."}
```

Verdicts are ordered by what matters: `failed` first, then `off-profile` (it
left the canonical path or skipped an expected step type), then `costly` (on
the path but above the usual spend), then `on-profile`.

## Cohort comparison — combinations (v18)

`python -m deepcompare cohort TRACESDIR --by model|agent|version|task -o out/`

Compares **groups as populations** rather than picking a representative run:
model family vs model family, prompt v1 vs v2, and so on.

```json
{"cohorts": [{"cohort": "claude-opus-5", "runs": 8, "successes": 8,
              "success_rate": 1.0, "success_ci": [0.676, 1.0],
              "mean_cost_usd": 0.0065, "agents": ["..."], "tasks": ["..."]}],
 "pairs": [{"left": "claude-opus-5", "right": "gemini-3-flash",
            "shared_tasks": 8, "comparable": true,
            "success_difference": {"observed": 0.19, "low": 0.06, "high": 0.31,
                                   "significant": true, "samples": 2000},
            "cost_ratio": 1.14,
            "behaviour": {"process": 0.9, "tools": 0.95, "resources": 0.88},
            "attribute_gaps": [{"attribute": "no_verification_step",
                                "difference": 0.35, "interval": {"...": 0}}],
            "verdict": "..."}],
 "narrative": "..."}
```

Two guards make the verdicts trustworthy:

- **Success rates are compared only on shared tasks.** Cohorts that ran
  different task sets are marked `comparable: false` rather than compared —
  one may simply have drawn easier work.
- **A difference whose interval includes zero is never called a win.** The
  verdict then says the cohorts are indistinguishable on outcome and falls
  back to cost, which is the decision the evidence actually supports.

## Open-weight models and real logprobs (v19)

Open-weight models are the **strongest** case for the model-telemetry
analysis, not the weakest: a self-hosted vLLM, TGI, llama.cpp or Ollama
server returns logprobs for every generated token — often the full top-k —
because there is no reason to withhold them. `deepcompare.logprobs` turns
those into the `model` block on each step, so `uncertainty.py` runs on real
data rather than the demo's synthetic numbers.

```json
"model": {
  "confidence": 0.8832,          // mean token probability
  "min_token_confidence": 0.8025,
  "entropy": 0.2832,
  "entropy_basis": "top_k | binary_floor",
  "tokens_scored": 3,
  "low_confidence_tokens": 0,
  "temperature": 0.2,
  "source": "ollama-logprobs | openai-compatible-logprobs | provider-logprobs"
}
```

- `entropy_basis` is stated because it changes what the number means. With
  top-k logprobs, entropy is computed over the returned distribution
  (approximate — the tail is truncated). With only the chosen token's
  logprob, it falls back to the binary entropy of that probability, which is
  a **floor**, not an estimate.
- **Nothing is invented.** A payload with no logprobs yields no `model` block
  and a warning, rather than a fabricated confidence.
- Layouts handled: OpenAI/vLLM/TGI `choices[0].logprobs.content[]`, a bare
  `logprobs.content`, TGI `details.tokens`, and a plain list.

### Reaching the tool from an open-weight stack

| serving stack | route |
|---|---|
| vLLM, TGI (OpenAI-compatible endpoints) | `--format openai`, pass `responses` alongside `messages` to carry logprobs |
| Ollama | `--format ollama` (or auto) — keeps `eval_count` token counts and logprobs |
| anything OTel-instrumented | `--format otel` — `gen_ai.provider.name` and `gen_ai.request.model` are recorded onto the agent |

Telemetry is attached to steps by **matching the generated text**, not by
zipping turns to steps positionally: a tool-result turn fills an existing
step's output rather than creating one, so a positional pairing would put a
turn's confidence on the wrong step. Unmatched telemetry is dropped with a
warning rather than guessed.

### What the Ollama route carries (v20)

The same text match decides where a turn's **usage and timing** land, not
just its confidence:

| reported by the server | becomes | when absent |
|---|---|---|
| `eval_count` (assistant turns only) | `steps[].tokens` | `len(text)/4` estimate |
| `prompt_eval_count`, summed over turns | `totals.input_tokens` | estimate from the prompt text |
| `total_duration`, else `eval_duration` (ns) — or `latency_s` in seconds | `steps[].latency_s`, summed into `totals.latency_s` | stays `0.0` |

Summing `prompt_eval_count` counts re-sent history more than once, which is
what the server actually processed and what a provider bills for.
`eval_count` is taken only from assistant turns: a tool-result turn generated
nothing, so counting it would inflate the run's output tokens.

Tool results are the other half. Ollama has no `tool` role, so runners record
observations as ordinary user turns; read literally, the first one is taken
for a second task prompt and **discarded along with the retrieved evidence** —
the text divergence, claim-provenance and semantic analysis all read. A user
or `tool` turn arriving while a tool call is outstanding is therefore treated
as that call's result and fills the step's `output`.

None of this is invented: what the server did not report keeps its estimate
or its zero, and `--dry-run` counts steps with text, timing, tokens and
observations so a lossy mapping is visible before anything depends on it.

---

## Process integrity (v22)

Outcome-only evaluation is blind by construction. A run can satisfy its
oracle while looping, swallowing an error, writing something nobody asked
for, or stopping because it hit the step ceiling; and a run can fail with a
completely clean process because the *oracle* is wrong. Claw-Eval measures
44% of safety violations and 13% of robustness failures as invisible to
outcome-only grading ([arXiv 2604.06132](https://arxiv.org/abs/2604.06132));
OpenClawBench names it the **outcome-process gap** and needed 31,264
annotated trajectories to characterise it
([arXiv 2605.29253](https://arxiv.org/abs/2605.29253)).

`deepcompare.process` computes the deterministic subset of that from a
logged trace — no judge, no re-execution. That restriction is deliberate:
replaying *frozen* transitions under different evaluator channels flips the
sign of the same step's score, with cross-channel disagreement exceeding
same-channel retry disagreement by 48 percentage points
([arXiv 2607.04419](https://arxiv.org/abs/2607.04419)). What can be settled
by counting is settled by counting; the rest is left to a human, with the
evidence laid out.

### New optional trace fields

All optional and backward compatible. Declared beats inferred, and the
difference is always reported.

```json
"steps": [
  {"index": 3, "type": "tool_call", "name": "cancel_booking",
   "error": false,          // was the observation an error? null = undeclared
   "effect": "write"}       // "read" | "write" | null (then inferred from the name)
],
"outcome": {
  "termination": "agent_stop"   // see below; null = undeclared, never guessed
},
"tools": [                       // what the agent was offered
  {"name": "cancel_booking", "effect": "write",
   "parameters": {"properties": {"reference": {"type": "string"}},
                  "required": ["reference"]}}
],
"budget": {"max_steps": 10}      // limits the harness enforced
```

`termination` takes tau2-bench's `TerminationReason` values verbatim, rather
than an invented set, so a run logged for one tool is comparable in the
other: `agent_stop`, `user_stop`, `max_steps`, `timeout`,
`context_window_exceeded`, `too_many_errors`, `agent_error`,
`infrastructure_error`, `unexpected_error`. The last two are *harness*
failures and are excluded from reliability statistics — counting a
rate-limited run as an agent failure makes the agent look worse and the
harness look fine.

**Termination is never inferred.** "The last step was an answer" does not
distinguish an agent that decided it was done from one the harness cut off,
and every per-step rate conditioned on it would inherit the guess. Undeclared
stays `undeclared`. The same discipline applies throughout: without a `tools`
list, schema grounding and permission are reported **unmeasurable** rather
than scored 100% — an unchecked call is not a valid one.

### What is computed

| check | what it counts |
|---|---|
| `termination` | declared reason, steps, budget used, budget pressure (≥80%) |
| `side_effects` | reads, writes, **writes before any successful read** |
| `repeats` | repeated calls, `(call, result)` cycles, no-information steps |
| `loops` | longest back-to-back repeated k-gram with its period; max call multiplicity |
| `recovery` | errors, adaptation attempts, recoveries, abandonment after error |
| `grounding` | calls to undeclared tools; argument values with no source in the trace or prompt |
| `schema` | missing required / unknown / mistyped arguments against declared parameters |
| `false_success` | the answer claims completion while nothing was written |

Three distinctions that make the counts mean what they say:

- **A retry after an error is not a repeat.** Retrying a failed call is
  correct behaviour; counting it as looping would penalise recovery.
- **Only a read that *worked* counts as having looked.** Three failed
  lookups followed by a write is the blind-write case exactly.
- **A no-information step** returns an observation byte-identical to an
  earlier one — the call was new, the run advanced nothing.

### The gap verdict

| verdict | meaning |
|---|---|
| `passed cleanly` | the oracle is satisfied and nothing contradicts it |
| `passed but pathological` | passed, but looped / swallowed an error / wrote blind. A leaderboard scores this identically to a clean pass |
| `failed with cause` | failed, and the process shows why |
| `failed but clean` | failed with nothing visibly wrong — **evidence about the grader**, not only the agent |

There is deliberately **no single process score**. Weighting a loop against a
blind write needs a judgement about the domain that this tool does not have;
the flags are reported so a reader can apply their own.

`demo/process/` generates traces exercising all four verdicts — including
the headline pair, where the run that *passes* is the one that looped,
ignored three errors and wrote blind, and the run that *fails* has a
spotless process.

---

## Reference tool-call comparison (v22)

`deepcompare.toolmatch` deliberately borrows other people's vocabulary,
because this is the one place where a shared one exists: the four match
modes are LangChain `agentevals`, the argument modes its `ToolArgsMatchMode`,
the F1 is Ragas `ToolCallF1`, the order-aware partial credit is DeepEval's
weighted-LCS `ToolCorrectness`, and the permission check is DeepEval's
`ToolPermission`.

**Every result names the algorithm that produced it**, because four
widely-used libraries ship a metric called "tool call accuracy" and compute
four different numbers from the same trace:

| library | same trace, different answer |
|---|---|
| `agentevals` | boolean; no partial credit at all |
| Ragas `ToolCallAccuracy` | argument accuracy × an order gate — right calls in the wrong order score **0.0** |
| DeepEval default | greedy best match, arguments scored as matching keys over the **union** of keys |
| DeepEval ordering mode | weighted LCS ÷ number of reference calls |

So "tool call accuracy: 0.67" is meaningless without its algorithm, and a
leaderboard mixing them compares nothing. The same applies to argument
matching: `exact`, `ignore`, `subset`, `superset` and `key_fraction` give
0.0, 1.0, 0.0, 0.0 and 0.67 for one realistic pair.

Two footguns are inherited and documented rather than silently fixed:
**subset/superset polarity** (in `agentevals`, `subset` means the *run's*
calls are a subset of the reference — its implementation reads
`_is_trajectory_superset(reference, outputs)`), and the fact that `strict`
compares message structure while `unordered`/`subset`/`superset` discard it
and compare flattened tool calls only. Each result spells out its direction
in words.

All four modes are reported together, because they disagree by construction:
a run can be a `superset` match and not a `strict` one, and seeing which
modes pass is more informative than any single verdict.

---

## Recording a trace (v23)

`deepcompare.record.Recorder` writes conformant trajectories from a live
agent, so the schema is something you emit rather than something you
hand-write. It carries the v22 fields by default — declared `tools` with
effects and parameter schemas, `budget`, `outcome.termination`, per-step
`error` and `effect` — because a recorder that omits them makes half the
process analysis unmeasurable.

Two fields exist so a recorded run cannot quietly overstate itself:

```json
"steps": [{"tokens": 4, "tokens_basis": "estimated"}],
"token_accounting": {
  "basis": "estimated | measured | mixed",
  "measured_steps": 0, "estimated_steps": 2,
  "estimator": "len(text)/4"
}
```

`tokens_basis` is `measured` when the provider reported the count and
`estimated` when it was derived from text length. **Both are carried through
load and save.** A basis that survives to disk but is dropped by the loader
is worse than no basis at all: the next comparison treats a `len(text)/4`
guess as a provider-reported number, and nothing downstream can tell the
difference. An absent basis stays absent rather than defaulting to
`measured`.

The recorder never invents a termination reason. Clean exit is `agent_stop`,
an exception becomes `agent_error` (or `timeout`/`user_stop` where the
exception says so), and `max_steps` or a harness failure must be declared
explicitly through `terminate()` — only the harness knows why it stopped.

---

## The trade-off and the efficiency block (pairwise reports, v25)

Every report carries `tradeoff` — the speed-quality exchange stated rather
than left to the reader:

```json
"tradeoff": {
  "case": "dominance | price_of_correctness | quality_for_spend |
           equal_outcome_cheaper_run | both_failed | equivalent",
  "dominant": "agent name or null",
  "spend_delta_b_minus_a": {"tokens": 682, "cost_usd": 0.0039,
                            "latency_s": 8.45, "steps": 5},
  "statement": "plain-language exchange, e.g. 'the correct answer cost +8.5s'",
  "caveat": "one task, one run per side: descriptive only"
}
```

The three cases are kept apart on purpose: dominance is never dressed up as
a dilemma, a fast failure is "slower to nothing" rather than a saving, and
exchange rates (`score per dollar` etc.) appear only under
`quality_for_spend`, where quality and spend actually moved together.

`efficiency` carries what the trace implies about serving cost, per side:
`context_growth` (prompt-cache-absorbable resend overhead), `result_cache`
(identical call+result repeats, with a retry after an error excluded),
`parallel_reads` (independent consecutive reads, broken conservatively by
any provenance link), `latency` (concentration, gated to runs ≥5 steps),
`throughput` (refused outright on estimated token counts), and a ranked
`opportunities` list whose savings are **ceilings with their assumption
named**. The aggregate rolls up per agent, adding `cost_per_success` with
numerator and denominator.

## Triage verification and the fix loop (v26)

Every triage action carries `verification` — how the fixer will know it
worked:

```json
"verification": {
  "how": "re-run the same tasks, then `agentdiff progress <before> <after>`",
  "checks": [
    {"kind": "fingerprint", "fingerprints": ["..."],
     "confirms": "binary and deterministic; the strongest signal at any suite size"},
    {"kind": "process_flag", "flags": ["blind_write"], "tasks": ["..."]},
    {"kind": "success_rate", "current": "2/4", "hoped": "4/4",
     "chance_of_hoped_result_without_a_fix": 0.32,
     "single_rerun_can_confirm": false,
     "note": "an unchanged agent reaches 4/4 32% of the time by pure luck ..."}
  ],
  "caveat": "absence confirms only if the same tasks ran (task-set drift)"
}
```

The success-rate criterion is the exact binomial tail under the
unchanged-agent null — **not** a Wilson-interval comparison, which describes
uncertainty about the old rate rather than the sampling noise of the next
run, and would call a 3-of-4 → 4-of-4 jump (32% likely with no fix at all)
a confirmation.

`agentdiff progress before/ after/` writes `progress.json`: per
before-action `status ∈ resolved | improved | persists | worsened |
unobservable | untrackable` (a fingerprint whose task did not re-run is
unobservable, not cured; fewer occurrences is improved, not resolved),
`new_issues` the before-run did not have, `success_by_agent` with per-task
flips and the same luck criterion, `task_drift`, and `efficiency_shift`
(realised cost per success beside estimated overheads, labelled apart).

## Narration under covenant (v25)

`agentdiff narrate` emits a numbered-fact brief and prompt for **any**
external model; the returned text is checked number-by-number and stored as:

```json
"narration": {
  "text": "...", "model": "...", "source": "external-llm",
  "brief_digest": "sha256 prefix binding the narration to what it saw",
  "faithfulness": {"numbers_checked": 9, "unsupported_numbers": ["93%"],
                   "citations": 3, "invalid_citations": [], "faithful": false,
                   "limit": "numeric and citation checking only; ..."},
  "authority": "commentary only ... deleting it changes no finding"
}
```

Nothing in the engine reads `narration`; a fabricated figure is stored
flagged rather than trusted or dropped. Briefs exist for four shapes —
pairwise report, batch aggregate, experiments comparison, progress result —
and the covenant does not vary with scale.

## Experiments and variance (v24–v25)

`agentdiff experiments A/ B/` writes `experiments.json`: per-experiment
summaries (Wilson intervals, harness failures excluded), pairwise diffs
paired on shared tasks (success is the single primary endpoint; the four
resource metrics are Benjamini–Hochberg corrected among themselves, both
raw and adjusted verdicts kept), and a behavioural `similarity` block —
cross-experiment action-sequence similarity against each experiment's own
within-baseline, because outcome agreement is not behaviour agreement in
either direction.

`agentdiff variance DIR/` writes `variance.json`: sequential
sums-of-squares shares swept over **every attribution order** (a factor's
share is a range; its width is variance the factors share and no ordering
can assign), beside bias-corrected omega squared with each factor's
`expected_by_chance` — a 33-level factor explains 12% of variance before
any real effect exists. Confounded designs are named as such, with the fix
("run one harness on a second model") rather than a split that would be an
artefact of ordering.

## Adjudicated diagnosis (v27)

`attribution` tells one story: the first structural divergence, walked to the
answer. That story can be wrong in an identifiable way — the same report can
say the failed run's answer *matched the expected answer* while the winner
passed writing blind. Every report now carries `diagnosis`: one hypothesis
per diagnostic signal (grader/label, harness termination, environment error,
wrong-fact propagation, the divergence itself, each process flag, budget
pressure), scored against an explicit evidence ledger and **adjudicated**
rather than smoothed into a single narrative.

```json
"diagnosis": {
  "version": 1,
  "mode": "single_failure | both_failed | both_succeeded",
  "subject": "a | b | null", "subject_name": "bolt-v3",
  "verdict": "bolt-v3: best explained by divergence — ...; leads the runner-up by 0.35",
  "hypotheses": [
    {"id": "H1", "kind": "divergence", "agent": "b",
     "statement": "the run went wrong at step 2, a retrieval decision ...",
     "score": 0.7, "status": "leading",
     "supports": ["E1", "E3"], "contradicts": [],
     "discriminator": "splice the other agent's decision at the divergent step and re-run ..."}
  ],
  "leading": "H1", "margin": 0.35,
  "evidence": [
    {"id": "E1", "type": "span", "agent": "b", "step": 2, "field": "output",
     "quote": "random blog", "signal": "the divergent retrieval step",
     "basis": "measured"},
    {"id": "E2", "type": "metric",
     "path": "answer_eval.b_vs_expected.coverage", "value": 0.95,
     "signal": "answer matches the expected answer", "basis": "measured"}
  ],
  "causal_account": [{"step": 2, "happened": "...", "mechanism": "...",
                      "evidence": ["E1"]}],
  "contradictions": ["the failed run's answer matched the expected answer ..."],
  "confidence": {"level": "medium", "basis": "single pair (n=1); ..."}
}
```

**Statuses.** Each hypothesis lands in exactly one:

- `leading` — the top-scored hypothesis, and only when it clears the
  runner-up by the **lead margin** (≥ 0.15) with a score of at least 0.2.
  Anything closer and `leading` is `null`: the diagnosis is **contested**
  and the verdict says so — two plausible causes with a thin margin are not
  one confident cause.
- `plausible` — scored ≥ 0.2 but not leading; a live alternative.
- `weak` — supported but under 0.2; recorded, not argued for.
- `ruled_out` — contradicting evidence exists and the score fell under 0.1.
- `merged` — absorbed into another hypothesis by fusion (below); kept with a
  `merged_into_kind` pointer so nothing disappears silently, and excluded
  from the margin computation.
- `untestable` — the check cannot be run from the trace (e.g. a grader
  hypothesis with no `expected` answer recorded); carried with `score: null`
  rather than dropped or guessed.

**Evidence is machine-checkable, in two types.** `span` evidence quotes a
substring of a named field of a named step of a named trajectory; `metric`
evidence names a path into the report and the value found there.
`check_diagnosis(diagnosis, report, a, b)` verifies **both** — the quote must
appear in that step field, the path must resolve to that exact value, and
every `supports`/`contradicts`/`causal_account` reference must point at a
ledger entry — the same contract `check_narration` applies to narration text.

**Fusion: corroborating signals are one story at two depths.** A structural
divergence and a wrong fact are usually not competitors, so before
adjudication:

- a wrong fact entering **at or after** the divergent step merges into the
  divergence hypothesis as its *mechanism* (the divergence takes the max of
  the two scores plus a corroboration bonus; the wrong-fact hypothesis is
  marked `merged`);
- a wrong fact entering **before** the divergent step re-anchors the root:
  the earlier anomaly is boosted, the divergence is penalised and restated
  as downstream symptom, with both statements saying why;
- a raised process flag whose evidence spans sit **on the attribution
  chain** merges into the divergence account as its mechanism, adding its
  evidence and a corroboration bonus.

**Every hypothesis carries a `discriminator`**: the concrete check that
would confirm or refute it (re-grade the quoted answer by hand, replay the
failing call in isolation, raise the budget for one re-run, ...). A
diagnosis that cannot say what evidence would change it is a story, not a
diagnosis.

### Batch rollup (aggregate, v27)

`aggregate["diagnosis"]` rolls leading-hypothesis kinds up across the batch
— which causes repeat, with the denominator always stated:

```json
"diagnosis": {
  "diagnosed_failures": 4,
  "contested": 0,
  "by_leading_kind": [
    {"kind": "divergence", "count": 3, "of": 4,
     "tasks": ["t01_acme_revenue", "t05_flight_duration", "t06_bls_unemployment"]},
    {"kind": "wrong_fact_propagation", "count": 1, "of": 4,
     "tasks": ["t07_build_failure"]}
  ],
  "note": "divergence leads 3 of 4 diagnosed failures — a repeated cause is worth one central fix, not 3 local ones"
}
```

Only `single_failure` pairs are counted (`diagnosed_failures`); contested
diagnoses are tallied separately rather than assigned to their top kind, and
a process-flag lead is qualified as `process_pathology:<flag>` so "looped"
and "wrote blind" do not pool into one bucket. The `note` appears when a
kind leads at least twice — a cause that repeats is systemic, worth one
central fix rather than N local ones — and the `batch` command prints it.


## Cross-run diagnosis consolidation (v28)

A pair diagnosis is honest about being n=1: its confidence is capped and
every hypothesis carries the check that would settle it. When the corpus
holds repeated runs of the same task, `deepcompare runs` performs the two
upgrades that repetition makes possible, and writes the result to
`aggregate["diagnosis_consolidated"]`:

1. **Consolidation.** Every failing run is diagnosed against the other
   agent's medoid run — not one representative pair — and the question
   becomes whether the same hypothesis leads each time. Failure
   reproduction gets its own denominator first: an agent that fails 1 of
   3 runs has a flake, not a systematic fault, whatever any single-run
   story says.
2. **Executed discriminators.** Checks a pair diagnosis can only
   *recommend* are answered offline from the runs already on disk:
   *grader consistency* (a failing answer near-identical to a passing
   run's answer proves the grader inconsistent — token Jaccard ≥ 0.8, both
   runs named), *environment reproduction* (the exact failing call
   succeeding elsewhere proves the error transient; erring everywhere
   points at the environment), and *harness flake rate* (kills counted
   over all runs). No re-running, no network, no model calls.

```json
"diagnosis_consolidated": {
  "per_task_agent": [
    {"task": "t01_acme_revenue", "agent": "bolt-v3",
     "runs": 3, "failures": 3,
     "failure_reproduction": {"k": 3, "n": 3, "verdict": "reproducible"},
     "diagnosed_runs": 3,
     "leading_kinds": {"divergence": 3},
     "contested_runs": 0,
     "per_run": [{"run": "r1", "leading": "divergence", "margin": 0.8}],
     "checks_run": [
       {"check": "grader_consistency", "outcome": "inconclusive",
        "hypothesis_kind": "grader_or_label",
        "detail": "no passing run in this corpus carries an answer ...",
        "basis": "measured", "runs": ["bolt-v3/r1"]}
     ],
     "consolidated": {
       "kind": "divergence", "status": "reproducible",
       "statement": "divergence leads the diagnosis in all 3 diagnosed runs — the cause reproduces, not just the failure",
       "basis": "consistent leading hypothesis across 3 runs"}}
  ],
  "summary": {"tasks": 8, "entries_with_failures": 4,
              "confirmed_by_checks": 0, "reproducible_causes": 4,
              "unstable_diagnoses": 0, "flaky_failures": 1},
  "narrative": "4 cause(s) reproduce across every failing run; ..."
}
```

`failure_reproduction.verdict` is one of `reproducible` (fails every run,
n > 1), `flaky` (fails some), `single run` (n = 1), `no failures`. An entry
with no failures keeps `consolidated: null` — it is the denominator, not a
diagnosis. `per_run` entries carry either the leading kind and its margin,
`"contested"`, or a note that the reference run also failed (pairwise
diagnosis not applicable there). One check instance per (check, outcome)
pair is kept; an `inconclusive` outcome is a result and is reported, not
dropped.

**Statuses.** The consolidated verdict lands in exactly one, and the
vocabulary is deliberately strict — **scores can make a hypothesis leading;
only an executed check can make it confirmed or refuted**:

- `confirmed` — an executed check confirmed the hypothesis against the
  corpus itself; `basis` says "executed check, not a score".
- `refuted` — a kind led every per-run diagnosis, but an executed check
  contradicts it. The check outranks the scored ranking: a refuted
  hypothesis does not get to stay leading because a heuristic scored it
  first.
- `reproducible` — the same kind leads all diagnosed runs (≥ 2): the
  *cause* reproduces, not just the failure. Still a scored finding, so it
  sits below `confirmed`.
- `unstable` — the per-run diagnoses disagree. The disagreement is itself
  the diagnosis: the cause is noise-sensitive, and no single-run story
  should be trusted for that task.
- `single_run` — one diagnosed run; n=1, unconfirmed, add runs to confirm.
- `all_contested` — every diagnosed run was contested; the evidence never
  picks a cause.

The `summary` counts each status with its denominators, and the
`narrative` string is what the `runs` command prints — flaky failures are
named with their k of n ("treat as flakes until they repeat") rather than
promoted to systematic faults.

## The decisive error step (v29)

Every pair `diagnosis` now carries a `decisive_step` object committing to
the field's ground-truth criterion (Who&When): the **earliest step whose
correction is expected to flip the outcome**.

```json
"decisive_step": {
  "step": 2,
  "criterion": "earliest step whose correction is expected to flip the outcome",
  "basis": "where the wrong fact entered, per claim provenance",
  "reason": null
}
```

Anchors per leading kind: a divergence anchors at its root, a wrong fact
at its provenance origin, an environment error at the failing call, a
process pathology at its first flagged step.  Three causes have **no
agent step to correct**, and the honest answer there is `step: null`
with the `reason` stated: a grader mislabel (the correction is to the
label), a harness kill (no corrected step prevents it), and budget
pressure (the constraint is a harness setting).  A contested diagnosis
commits to no step — refusing to localize is part of refusing to
adjudicate.  Two claim-exclusivity rules keep the anchor honest: a
claim the passing run also carries can neither anchor the wrong-fact
hypothesis nor contradict the grader hypothesis (shared context cannot
explain a one-sided failure), and the answer-coverage "match" verdict
counts as grader-suspect evidence only at coverage ≥ 0.85 — below
that, the missing words may *be* the contradiction.

The benchmark (`demo/diagnosis_bench`, `deepcompare/bench.py` v2)
scores this axis directly: `step_localization` (exact and within ±1,
denominators stated), `abstention` (predicting a step where no agent
step exists is a `spurious_step` miss), and per-scenario
`step_outcome` values `exact | adjacent | wrong_step | missed_step |
correct_abstain | spurious_step`.

The causal account itself is anchored at the leading hypothesis's own
decisive step and walked **transitively**: a step joins when it carries
a contradicting claim's typed value (claim provenance, strongest link),
measurable word overlap with any step already in the chain, a weak/bad
log annotation, or — only when the environment hypothesis leads — a
declared error downstream of the failing call. Outputs identical to one
already in the chain are skipped (a repeated call is a pathology, not
propagation), and the benchmark scores the account as `chain_recovery`
(mean recall/precision against the implanted propagation path, with
scenarios that produced no account counted at recall 0, never skipped).

## The measured eval, and the rules it forced (v30)

Everything from v29 onward is driven by one loop: measure the diagnoser
against implanted ground truth, fix the miss families principledly,
re-measure. The artifacts:

- **`agentdiff bench [traces] [--strict] [-o out.json]`** — the
  scorecard CLI. Floors live in `bench.FLOORS`, shared verbatim with the
  test suite; `--strict` exits non-zero on any floor violation.
- **Multi-cause scoring** — manifest scenarios may carry `secondary`
  kinds; leading with only the secondary is its own `secondary_only`
  outcome, and the `multi_cause` metric counts whether the secondary
  stayed visible among the hypotheses.
- **The procedural corpus** — `demo/diagnosis_bench/generate_scale.py
  --pairs N` composes eleven cause families across domains, lengths and
  distractors with mechanically derived truth (seeded; byte-identical
  per N). The `paraphrase_grader` family is the deliberate open
  challenge: reworded-but-correct answers whose valueless-domain cases
  are expected misses that stay in the measured number.
- **Anchor rules the eval forced, all instances of one exclusivity
  principle** (shared evidence cannot explain a one-sided failure):
  a claim the passing run also carries neither anchors the wrong-fact
  hypothesis nor contradicts the grader hypothesis; an exclusive
  contradicting claim voids the grader's coverage support outright; the
  **twin rule** advances the divergence anchor past any step whose
  (type, name, input) has an exact twin in the other run; and the
  typed-value grader rule (a clean failure whose answer asserts the
  exact expected value) is gated on flags *exclusive* to the failing
  side, not absolute cleanliness.
- **Account adjacency rules** — a repeated declared error is part of
  the fault's story (never skipped as a duplicate), and a reason step
  immediately following an on-chain declared error joins as the agent's
  response, labelled `adjacency, declared — not traced propagation`.
- **Spectrum surfaced** — the `runs` command prints per-signature
  suspiciousness under each cross-run entry, and the aggregate
  narration brief carries `diagnosis.spectrum` facts (top signature
  with its counts, or the both-classes refusal), all numbers entering
  the narrator's allowed set.

## The adversarial round (v31)

An independent red-team evaluation built trace pairs to make the engine
tell confident wrong stories, and every fix it forced is another
application of the same exclusivity principle:

- **Negation guard** — a negator-count mismatch between the answer and
  the expected answer voids the grader's coverage support ("not
  refundable" is no near-match of "refundable", however high the
  lexical overlap).
- **Answer-evidence requirement** — a grader hypothesis whose only
  support is a clean process gap carries `answer_evidence: false` and
  may rank but never lead.
- **Twin-rule write exception** — a write-effect step the failing run
  performed more times than the passing run is a real anomaly and may
  anchor (the duplicated charge IS the failure); duplicate reads stay
  excused as alignment noise.
- **Grounding dock** — an error on a call whose arguments have no
  source in the trace is capped below the invention hypothesis (the cap
  survives fusion's timing boosts) and its discriminator flips to a
  provenance-first check, because a garbage-argument call errors
  deterministically and replaying it cannot exonerate the agent.
- **Shared-flag dock** — a process flag the other run also raises is
  shared behaviour and cannot explain a one-sided outcome; for
  `invented_arguments` exclusivity is decided per (tool, argument,
  value) invention, never per flag bit, so a shared filler literal
  cannot mask an entity only the failing run made up.
- **Root-signature merge** — a flag whose evidence sits on a step with
  the divergence root's exact (type, name, input) signature is the same
  repeated decision, and merges into the divergence account instead of
  contesting it.
- **Four adversarial corpus families** — `negation_answer`,
  `wrong_entity`, `causal_duplicate`, `garbage_args` joined the
  procedural generator (fifteen families total); the corrected engine
  holds the middle two at 1.0, and the other two are named open
  challenges that stay in the measured number.
- **The stripped condition** — `generate_scale.py --strip-annotations`
  nulls every step's `error`/`quality`/`note` so the engine must infer
  from observation text alone: the de-circularized scorecard is
  published alongside the annotated one, and the gap between them is
  the measured value of structured step metadata.
- **`check_diagnosis` structural checks** — beyond grounding (quotes,
  metric paths, dangling refs), the verifier now checks the
  adjudication's own bookkeeping: statuses in vocabulary, scores in
  [0, 1], `leading` naming a hypothesis actually marked leading. Its
  docstring states the boundary: empty means GROUNDED, not TRUE.
