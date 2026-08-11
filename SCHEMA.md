# DeepCompare Trace Schema (v1)

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
