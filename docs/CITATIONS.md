# The trace-diffing literature, and where AgentDiff stands (August 2026)

A fetched survey of the work citing or adjacent to the ideas AgentDiff
implements — not from memory; each claim carries its source. Proxy access
blocked most publisher PDFs, so claims marked [snippet] are sourced from
search-engine retrieval of the cited page rather than a direct read.

## The lineage AgentDiff sits in

Trajectory alignment and divergence detection are now an established method
family, not an invention of any one tool:

- **TraceProbe** (2607.06184) canonicalizes actions into a 9-type taxonomy,
  then aligns run pairs and emits *divergence spans* — contiguous blocks
  unmatched or reordered after alignment. Fully rule-based; no LLM in the
  loop. [snippet]
- **InconLens** (2603.28106) aligns *the same agent's* repeated runs via
  "information nodes" — canonical informational milestones — and supports
  interactive identification of divergence points. The closest published
  system to a diff product. [snippet]
- **Retrace** (retraceai.tech) ships first-causal-divergence detection with
  cost and latency deltas per diff region, over tamper-evident replayable
  logs. [snippet]
- **TraceGraph** (2605.31308) pools many models' rollouts per task into one
  shared decision landscape — cohort over pairs. [snippet]
- **Graphectory** (2512.02393, OOPSLA track) encodes 4,000 SWE-agent
  trajectories as temporal-semantic graphs; finds that resolved issues
  follow coherent localize→patch→validate shapes while unresolved ones are
  chaotic — and that **even successful runs are inefficient**. [snippet]

The shared move across all of them: **normalize before aligning**. Raw-text
alignment is a dead end; every successful aligner canonicalizes actions
first. AgentDiff's step taxonomy + canonical call signatures follow the same
discipline.

## Why the narration covenant is the way it is

- **Who&When Pro** (2607.09996): LLM failure attribution collapses from 94%
  to 50% accuracy as traces grow from under 3K to over 12K tokens, across
  all ten models tested, and systematically substitutes visible symptoms
  for root causes. [snippet]
- **DRIFT** (2606.02060): forcing the narrator to be *claim-centric* —
  every narrated claim linked to a trajectory span and checked for support
  — improves span localization by up to 30 points. [snippet]
- **Docent** (transluce.org): the flagship LLM trace-analysis system; its
  own documentation states summaries "often contain false positives", and
  its validation practice reports precision only, never recall, "because it
  is much easier to validate false positives than false negatives."
  [snippet]

The field's convergent answer is "LLM proposes, deterministic layer
disposes." AgentDiff's `narrate` module is that answer implemented: the
engine emits a numbered-fact brief, an external model writes prose, and the
text is machine-checked against the brief — unsupported numbers stored
flagged, narration read by nothing.

## Efficiency signals a trace determines

| signal | finding | source |
|---|---|---|
| prefix-cache hit rates | 95.7% aggregate for coding agents (TraceLab) vs 3–5% for RAG-style workloads — no single prior | 2606.30560 vs bisok.com [snippet] |
| cache-break events | strategy choice swings cost 45–80%; naive full-context caching can *increase* latency | 2601.06007 [snippet] |
| idle-gap eviction | keepalive pinging cuts post-pause cost up to 12.5× on Anthropic/OpenAI; only latency on others — provider-dependent | 2607.19214 [snippet] |
| prefill dominance | at 100:1 input:output, prefill is 85–95% of GPU time, so cache hit rate ≈ cost | spheron.network [snippet] |
| parallelizable tool calls | detectable from dataflow independence in provenance graphs | AgentTrails, 2607.18816 [snippet] |
| loop/repetition waste | pure sequence analysis; successful runs still inefficient | TraceProbe, Graphectory [snippet] |

Open ground the survey identified: *no research tool yet combines
behavioural diff with efficiency diff* — cost/cache/parallelism deltas
attached to divergence regions. That is what `deepcompare/efficiency.py`
plus the pairwise diff aims at.

## Experiment-level statistics, 2026 practice

Unbiased pass^k + task-level bootstrap with paired resampling; McNemar on
shared-task binary outcomes; Wilson intervals per side;
**Benjamini–Hochberg across multiple diff metrics** (implemented in
`experiments.py`: success is the single primary endpoint, resource metrics
corrected among themselves). Newer ideas: IRT mid-range task filtering —
comparing versions only on 30–70%-pass-rate tasks preserves rank fidelity
at 44–70% lower cost (2603.23749) [snippet]; and elicitation-gap accounting
— pass^k misestimates capability by ~50% without log audit (2605.08545),
so diff statistics should exclude harness-failure runs first, which
AgentDiff's reliability and experiments modules both do.

## Name collisions to avoid citing wrongly

Two unrelated systems are named "AgentCompass" (2509.14647 and 2607.13705),
and two are named "AgentLens" (2402.08995 and Salesforce's Agentforce
debugger). Cite by identifier.

## Diagnosis as adjudication, not narration (implemented v27)

The Who&When result — automated attributors collapsing from 94% to ~50%
on harder splits — is a warning about *confident single-story* diagnosis,
and DRIFT's claim-centric decomposition (+30pts over free narration) is
the constructive answer: force every diagnostic claim onto a machine-
checkable span. `diagnosis.py` applies both lessons structurally rather
than at narration time: every signal in the report (grader, harness,
environment, wrong-fact provenance, divergence, process flags, budget)
generates a *competing* hypothesis; each is scored only against a ledger
of span evidence (quote must appear at the cited step field) and metric
evidence (path must hold the cited value), verified by
`check_diagnosis`; a hypothesis leads only when it clears the runner-up
by a stated margin, otherwise the verdict is "contested" with the
discriminating checks listed. Corroborating signals fuse into one
account (mechanism, not rivals), and an anomaly that predates the
structural divergence re-anchors the root — first divergence is a
heuristic, earliest evidenced anomaly is the diagnosis.

## The decisive error step: where every attributor collapses (survey, Aug 2026)

The field converged on one ground-truth definition and one hard number.
The definition (Who&When, 2505.00212 lineage): the **decisive error
step** is the *earliest* step whose correction would turn the failure
into success — a counterfactual criterion, not a "which step looks
worst" judgement. The number: step-level localization is where every
attributor collapses — best published: **14.2% step accuracy** on
Who&When (vs 53.5% agent-level), rising only to **30.3%** in Who&When
Pro (2607.09996) even with 12,326 injected-failure trajectories built
by replaying a successful prefix and injecting one controlled fault.
The documented dominant mistake: attributors blame the **loudest
downstream symptom** (usually "reasoning") instead of the earliest
originating cause (planning, perception, retrieval).

Adjacent results that shape our eval design:
- **TraceElephant** (ACL 2026, 2604.22708): full-trace observability
  (inputs + context, not just outputs) improves attribution up to +76%.
  AgentDiff traces are full-observability by schema — worth stating,
  never assuming.
- **FAMAS** (FSE 2026, 2509.13782): spectrum analysis over repeated
  runs — suspiciousness from agent/action activation patterns across
  passing vs failing executions — beats 12 LLM baselines on Who&When.
  Independent support for cross-run consolidation as the right lever.
- **Long-horizon trajectory attribution** (2608.06909): two-metric
  protocol — primary attribution localization *and* attribution-chain
  recovery. Our causal_account is the chain; it should be scored too.

**The gap this implies for AgentDiff:** the diagnosis names hypothesis
*kinds* and the benchmark scores only kinds. Nothing commits to a
decisive step, and nothing measures step localization — the exact axis
where the field collapses and where a deterministic evidence-fused
engine should shine against LLM judges. Implemented as: (1) a
`decisive_step` field on every diagnosis, defined by the counterfactual
criterion with per-kind anchors and honest abstention (a grader
mislabel or harness kill has *no* agent step to correct, and saying so
is a scored answer, not a dodge); (2) benchmark scenarios that
reproduce the documented failure mode — quiet early cause, loud late
symptom, plus non-causal distractor pathologies; (3) step-level scoring
(exact and ±1) alongside kind accuracy, with abstention correctness as
its own metric.

## The measured-eval loop (implementation record, Aug 2026)

The survey above ended with a gap: nothing committed to a decisive step
and nothing measured localization. Closing it became a loop that ran
its own protocol — benchmark, triage the misses by family, fix the
engine or the corpus principledly, re-measure — and the loop's yield
was rules, each one an instance of a single principle:

**Shared evidence cannot explain a one-sided failure.** Its
applications, in the order the eval forced them: (1) a claim the
passing run also carries neither anchors the wrong-fact hypothesis nor
contradicts the grader hypothesis; (2) an exclusive contradicting
claim voids the grader's coverage support outright — word overlap
cannot vouch for a number it cannot read; (3) the twin rule — a step
whose (type, name, input) has an exact twin in the other run cannot be
the decisive decision, however the aligner paired the copies; (4) the
typed-value grader rule is gated on flags exclusive to the failing
side, because a flag the passing run also raises is shared behaviour.

Each rule exists because a specific measured miss family demanded it,
and each is pinned by the scenario family that found it. The corpus
keeps one family the engine cannot fully solve (valueless-domain
paraphrases) as its open challenge, on the maxim that a benchmark
containing only what the diagnoser already gets right measures
nothing.
