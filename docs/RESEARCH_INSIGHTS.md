# Research insights and the implementation program (Aug 2026)

Three independent literature surveys were run in parallel — agent-based
evaluation and failure attribution; interpretability comparison
methodologies; visual approaches to trace analysis — each with the
mandate to distill the most important insights *first* and only then
judge what AgentDiff should build. This file is the synthesis: what the
literature says, what it validates, what it contradicts, and the
program in the order it is being executed.

Every source below is a real, cited publication; where a survey could
only verify a 2026 preprint through search extracts (the session's
egress proxy blocked arxiv full-text), that is stated in the survey
transcripts and the claim is held to what the abstract supports.

## What the literature validates (already covered — keep, cite)

- **Machine-checkable adjudication over one-shot LLM judgment.** Who&When
  (Zhang et al., ICML 2025) found the best automated method reached
  53.5% on *which agent* and only **14.2% on the decisive step**; TRAIL
  (Patronus, 2025) found 11–18% joint localization for frontier LLMs on
  long traces; judge accuracy collapses past ~32k tokens with
  position, verbosity and self-preference biases on top. AgentDiff's
  design — mechanical alignment first, an evidence ledger the verifier
  re-resolves, an LLM confined to narration that can never alter a
  number — is what this literature prescribes.
- **Spectrum-based fault localization over repeated runs.** FAMAS
  (Ge et al., FSE 2026) applies an Ochiai-family suspiciousness formula
  over re-executed runs and beats all twelve baselines on Who&When,
  including LLM attributors. AgentDiff's consolidation layer already
  computes Ochiai suspiciousness over step signatures.
- **The grader as a first-class suspect.** AgentRewardBench (COLM 2025)
  shows rule-based graders *systematically underreport* success; the
  Agentic Benchmark Checklist (NeurIPS 2025) found do-nothing agents
  pass 38% of one benchmark's tasks. `grader_or_label` is the right
  hypothesis kind, and the paraphrase/negation open challenges are the
  documented dominant failure mode of exact-match grading, not edge
  cases.
- **Black-box behavioural diffing is competitive.** Kempf et al. (2026)
  show an output-level baseline for model diffing performs comparably
  to SAE/crosscoder diffing and surfaces more abstract differences —
  direct support for a trace-level comparison tool that never sees
  weights.
- **The two-lane vertical map is the right idiom.** A 192-participant
  CHI 2020 study found linear timelines fastest and preferred;
  Gleicher's comparison taxonomy names juxtaposition *plus* explicit
  encoding of the delta as the strongest design — the gutter edges are
  exactly that. No source names a superior alternative for two-run
  step comparison.

## What the literature contradicts (the valuable part)

1. **A single earliest decisive step is sometimes ill-posed.** Causal
   Agent Replay (2026) needs Shapley values because failures can be
   jointly caused; AgentRx localizes the first *unrecoverable* step
   (agents recover from early wobbles); DRIFT/TELBench finds the harmful
   step is often "an earlier commitment later spans inherit without
   revalidation"; MAST (NeurIPS 2025) finds ~42% of multi-agent failures
   are specification/design-level with no step to correct. Honest
   abstention is the right escape hatch, but the field asks for
   first-class **causal windows** (earliest flip *and* point of no
   return) and **joint-cause verdicts**.
2. **Stated reasons are claims, not evidence.** Anthropic (2025) found
   reasoning models verbalize a hint they demonstrably used only ~25%
   of the time; Arcuschin et al. show unfaithful chain-of-thought in the
   wild. A ledger entry quoting an agent's `reason` step can be quoting
   a confabulation. Observable events (calls made, arguments passed,
   outputs returned) must outrank rationale text.
3. **Annotation-stripping is necessary but not sufficient for
   de-circularization.** "Most Current Model Organisms Are Leaky" (2026)
   shows implanted behaviours leave out-of-band fingerprints a dumb
   detector can exploit. Without a **leakage-probe baseline**, a high
   benchmark score may measure fingerprint detection, not causal
   localization.
4. **Attribution from a static trace has a ceiling.** The counterfactual
   definition AgentDiff uses (earliest step whose correction flips the
   outcome) is validated by *re-execution* in AgenTracer (ICLR 2026) and
   CAR — and even replay is unstable because editing one step changes
   every downstream prompt. Decisive-step claims must be labelled as
   what they are: **counterfactual hypotheses with evidence**, verified
   only by replay.
5. **Confident marks overstate what any method knows.** With published
   decisive-step accuracy around 14%, an unqualified ring invites the
   deterministic-construal error the uncertainty-visualization
   literature documents (Padilla, Kay & Hullman). Diagnosis marks
   should carry graded confidence in their form.

## The program, ranked by impact × feasibility (stdlib, zero dependencies)

The harness came first because everything below wants traces from
real, swappable models: `deepcompare/harness/` runs any task set
against OpenAI-compatible, Anthropic or Ollama endpoints (or scripted
turns), through a generic tool loop, recorded as first-class SCHEMA
traces — and it is the *only* place in the project that touches a
network, pinned by test.

| # | Item | Source insight | Status |
|---|------|----------------|--------|
| 1 | **Causal window + joint-cause verdicts** — `decisive_step` gains `point_of_no_return` and a `joint` list when no single correction flips | CAR, AgentRx, DRIFT, MAST | in progress |
| 2 | **Replay verification hook** — the harness can re-run from a corrected step; verdicts labelled `replay-verified` vs `hypothesized (not replay-verified)` | AgenTracer, CAR | in progress |
| 3 | **Sanity-check suite** — identical pair → no decisive difference; swapped labels → inverted or abstained; shuffled steps → ledger collapses; hypothesis order swapped → same verdict | Adebayo et al. sanity checks, MIB, judge position-bias studies | in progress |
| 4 | **Paired + clustered error bars, sign/McNemar test on aligned pairs** | Miller, *Adding Error Bars to Evals* (Anthropic 2024) | queued |
| 5 | **Leakage-probe baseline** in the benchmark — a surface-cue detector; headline = engine − probe margin | Leaky Model Organisms (2026) | queued |
| 6 | **Null-agent control + grader-shortcut audit + injection contract** (clean twin passes; artifact reachable in the chain) | Agentic Benchmark Checklist, AgentTrace, MAS-FIRE | queued |
| 7 | **Evidence-class weighting** — observable events outrank stated reasons; verdicts tag their supporting class | Anthropic CoT faithfulness, Arcuschin | queued |
| 8 | **Overdetermination guard** — the corrected step must be materially different from the original before a flip counts | Thought Anchors (2025) | queued |
| 9 | **pass^k with CI + depth-stratified flakiness** in consolidation | τ-bench, Beyond pass@1 (2026) | queued |
| 10 | **MAST cross-mapping** in the taxonomy (TRAIL already mapped) | MAST, TRAIL | queued |
| 11 | **Narration evidence window** — the narrator sees decisive step ± neighbours and ledger quotes, never the whole trace | DuoTrace / detect-before-attribute, long-context judge collapse | queued |
| 12 | **Same-config noise-floor diff** — a run compared with itself is the artifact floor every verdict must exceed | crosscoder sparsity artifacts, XAI-Δ specificity | queued |
| 13 | **External validation** on the public Who&When and TRAIL data | Who&When, TRAIL | queued |
| UI-1 | **Confidence-graded rings and edges** with a legend of states | uncertainty-vis literature, Who&When accuracy | queued |
| UI-2 | **Loop collapse (×N badges) and phase segments**; unified-collapse block for long one-sided stretches | Langfuse agent graphs, AgentLens (TVCG 2025), split-vs-unified diff | queued |
| UI-3 | **Word-level diff in step detail**; hover-propagated causal paths; overview minimap; claim curves gated past ~15 | LLM Comparator (VIS 2024), Anthropic attribution-graph UI, MatrixWave | queued |

## The eval reasoning layer (fourth survey: understanding ONE trace)

A fourth survey covered reasoning about a single trajectory — what
happened, why it ended that way, what it means, what to do next — and
proposed a v2 schema for the `reading` object. The v1 reading shipped
first (phases, per-step roles, `rests_on` provenance, `why_it_ended`
with four honest verdict bases, findings tagged by evidence class,
deduplicated `take_forward`, a verified quote ledger); the v2 program,
in the survey's impact × feasibility order:

| # | Item | Source insight | Status |
|---|------|----------------|--------|
| R1 | **Answer-basis statuses** — each answer atom `supported` (first seen in an observation step), `self_asserted` (only in plan/reason), `unsupported`, `contradicted`, `stale`; plus `basis_complete_at` and `steps_after_basis_complete` (spend after the answer was available) | evidence-tracing survey (2026), AgentTrails (VLDB 2026) | queued |
| R2 | **Phase-order checks** — first write before any read, verification after the last write, regression cycles (act→locate→act) | AgentLens (TVCG 2025), TRAJEVAL, Lucky-Pass AgentLens (2026) | queued |
| R3 | **Error lifecycle** — per error occurrence: `resolved` / `unresolved_with_footprint` / `unresolved_without_footprint`; critical = earliest unresolved with a footprint in the answer | TrajDebug (2026), AgentDebug (2025) | queued |
| R4 | **Bounded evidence window** for the narrator — referenced steps only, trace order, byte budget, `omitted_steps` stated, verdict at top and bottom | Context Rot (Chroma 2025), position-bias studies, Inspect Scout | queued |
| R5 | **Next-action contract** — `{at_step, what, instead, refs, replay_recipe}`; the narrator may phrase, never invent an `instead` | Reflexion, AgentDebug, AgentDebugX | queued |
| R6 | **Validity block** — harness-terminated, tool failure rate, answer without basis, expected answer leaked into an observation; anything here suppresses agent-attributed verdicts | HAL (ICLR 2026), transcript-flaw scanners, NIST CAISI | queued |
| R7 | **Meltdown onset** — sliding-window entropy of tool names collapsing to zero while steps continue | Beyond pass@1 (2026) | queued |
| R8 | **Expired observations / stale basis** — same target later returned a different output | AgentDiet (FSE 2026), RedundancyBench | queued |
| R9 | **Strained coherence** — acknowledged a problem and acted against it; lexical, labelled as such, position as a fraction | Strained Coherence (2026) | queued |
| R10 | **Structural anchors, hypothesized** — plan steps after which the dominant role changed; nomination only, with a replay recipe | Thought Anchors (2025) | queued |

Not adopted from this survey: AgentPRM promise/progress values and
Thought-Anchors attention methods (need a trained model), the
AgentErrorTaxonomy's memory/reflection classes (need intent reading),
Lucky-Pass classification (needs multiple passing runs), and LLM
scanners as *detectors* — here an LLM may only narrate.

Deliberately **not** adopted: process-reward models and learned
attributors (training-time methods; AgentDiff is a diagnostic tool);
CKA/SVCCA representational similarity (requires activations, and its
own literature calls scalar similarity a weak explanandum); crosscoder
diffing (requires activations — only its null-control lesson transfers).

## Sources

Zhang et al., *Which Agent Causes Task Failures and When?*, ICML 2025 —
https://arxiv.org/abs/2505.00212 · Cemri et al., *Why Do Multi-Agent LLM
Systems Fail?*, NeurIPS 2025 — https://arxiv.org/abs/2503.13657 ·
Deshpande et al., *TRAIL*, 2025 — https://arxiv.org/abs/2505.08638 ·
Zhang et al., *AgenTracer*, ICLR 2026 — https://arxiv.org/abs/2509.03312
· *Causal Agent Replay*, 2026 — https://arxiv.org/abs/2606.08275 · Ge
et al., *FAMAS*, FSE 2026 — https://arxiv.org/abs/2509.13782 · Zhuge et
al., *Agent-as-a-Judge*, 2024 — https://arxiv.org/abs/2410.10934 · Lù et
al., *AgentRewardBench*, COLM 2025 — https://arxiv.org/abs/2504.08942 ·
Zhu et al., *Establishing Best Practices for Building Rigorous Agentic
Benchmarks*, NeurIPS 2025 — https://arxiv.org/abs/2507.02825 · Yao et
al., *τ-bench*, 2024 — https://arxiv.org/abs/2406.12045 · *Beyond
pass@1*, 2026 — https://arxiv.org/abs/2603.29231 · Miller, *Adding Error
Bars to Evals*, 2024 — https://arxiv.org/abs/2411.00640 · *Holistic Agent
Leaderboard*, ICLR 2026 — https://arxiv.org/abs/2510.11977 · Bogdan et
al., *Thought Anchors*, 2025 — https://arxiv.org/abs/2506.19143 · Kempf et
al., *Simple LLM Baselines are Competitive for Model Diffing*, 2026 —
https://arxiv.org/abs/2602.10371 · Anthropic, *Insights on Crosscoder
Model Diffing*, 2025 — https://transformer-circuits.pub/2025/crosscoder-diffing-update/index.html
· Ameisen et al., *Circuit Tracing*; Lindsey et al., *On the Biology of a
Large Language Model*, 2025 — https://transformer-circuits.pub/2025/attribution-graphs/methods.html
· Chen et al., *Reasoning Models Don't Always Say What They Think*,
Anthropic 2025 — https://www.anthropic.com/research/reasoning-models-dont-say-think
· Arcuschin et al., *Chain-of-Thought Reasoning In The Wild Is Not Always
Faithful*, 2025 — https://arxiv.org/abs/2503.08679 · Zaman et al., *A
Causal Lens for Evaluating Faithfulness Metrics*, EMNLP 2025 · Gur-Arieh
et al., *Faithfulness Metrics Don't Measure Faithfulness*, 2026 —
https://arxiv.org/abs/2605.25052 · Adebayo et al., *Sanity Checks for
Saliency Maps*, NeurIPS 2018 · *MIB*, 2025 — https://arxiv.org/abs/2504.13151
· *Most Current Model Organisms Are Leaky*, 2026 — https://arxiv.org/abs/2605.00994
· *Comparing Explanations is Not Enough, Explain the Change*, 2026 —
https://arxiv.org/abs/2602.02304 · Di Bartolomeo et al., timeline shape,
CHI 2020 — https://dl.acm.org/doi/10.1145/3313831.3376237 · Gleicher,
*Considerations for Visualizing Comparison*, TVCG 2018 · Kahng et al.,
*LLM Comparator*, VIS 2024 — https://arxiv.org/abs/2402.10524 · Lu et al.,
*AgentLens*, TVCG 2025 — https://arxiv.org/abs/2402.08995 · Zhao et al.,
*MatrixWave*, CHI 2015 · Padilla, Kay & Hullman, *Uncertainty
Visualization*, 2022 · Langfuse Agent Graphs —
https://langfuse.com/docs/observability/features/agent-graphs.
