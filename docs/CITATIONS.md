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

## The adversarial round (implementation record, Aug 2026)

Self-measurement has a ceiling: a generator written by the engine's
author samples the author's imagination. An independent adversarial
evaluation was pointed at the engine with instructions to make it tell
confident wrong stories, and it did — a negated answer that lexically
matched the expected one led the grader hypothesis at margin 1.0; a
wrong entity dressed in the right sentence shape did the same; a tool
correctly rejecting an agent-invented argument was pinned on the
environment, with a replay discriminator that would have confirmed the
wrong story. The fixes continued the same principle's application
list: (5) a negator-count mismatch between answer and expected voids
coverage support — polarity is the one thing word overlap cannot read;
(6) a grader hypothesis with no evidence from the answer itself may
rank but never lead; (7) the twin rule gains a write exception — a
write the failing run performed more times than the passing run is a
real anomaly and may anchor, because the duplicated charge IS the
failure; (8) an error on an argument with no source is capped below
the invention hypothesis (the cap surviving fusion's timing boosts)
and its discriminator flips to provenance-first; (9) a process flag
both runs raise is docked as shared behaviour, with exclusivity for
invented arguments decided per (tool, argument, value) invention so a
shared filler literal cannot mask the entity only the failing run made
up. The attack pairs are pinned as regression fixtures, four
adversarial families joined the corpus (two solved, two as named open
challenges), and the corpus regenerated with `--strip-annotations`
publishes the de-circularized scorecard beside the annotated one — the
gap between them is the measured value of structured step metadata.

## The self-evolving agent literature, and what it says goes wrong (September 2026)

Read for the lineage layer (`docs/EVOLVE.md`, "Why these checks").
Publisher hosts were again blocked by the proxy; entries marked
[snippet] are sourced from search-engine retrieval of the cited page,
those without the mark from a page read directly (GitHub-hosted).

Landscape, by what evolves and what triggers a step:

- **Survey of self-evolving agents** (2507.21046, Gao et al., v4 January
  2026) — the what / when / how / where taxonomy and the evaluation axes
  (adaptivity, retention, generalisation, efficiency, safety). Read via
  the companion list at github.com/EvoAgentX/Awesome-Self-Evolving-Agents.
- **Reflexion** (2303.11366, Shinn et al., NeurIPS 2023) — verbal
  reflection into an episodic memory buffer; relies on self-evaluation
  with no guarantee. [snippet]
- **Voyager** (2305.16291, Wang et al., 2023) — an ever-growing skill
  library of executable code, admitted by self-verification; 3.3× unique
  items. Read via github.com/MineDojo/Voyager.
- **STaR** (2203.14465, Zelikman et al., NeurIPS 2022) and **ReST**
  (2308.08998, Gulcehre et al., 2023) — weights bootstrapped from the
  model's own filtered samples; ReST notes repeated rounds overfit the
  learned reward model. [snippet]
- **Progress or Regress?** (2407.05013, Wu, Li and Liu, 2024) —
  self-improvement reversal: pass@1 up, output diversity and OOD
  generalisation down. [snippet]
- **DSPy** (2310.03714, Khattab et al., 2023) — optimizer docs advise
  200+ examples for a long MIPROv2 run "to prevent overfitting". Read via
  github.com/stanfordnlp/dspy docs. **TextGrad** (2406.07496, Yuksekgonul
  et al., 2024). [snippet] **GEPA** (2507.19457, Agrawal et al., ICLR
  2026) — Pareto front over per-instance winners, separate valset. Read
  via github.com/gepa-ai/gepa.
- **Agentic Context Engineering** (2510.04618, Zhang et al., 2025) —
  brevity bias and context collapse; on AppWorld 18,282 tokens at 66.7
  collapsed to 122 tokens at 57.1 in one rewrite. [snippet]
- **Darwin Gödel Machine** (2505.22954, Zhang, Hu, Lu, Lange, Clune,
  ICLR 2026) — self-modifying coding agent with an archive; staged
  evaluation (10 then 50 tasks); SWE-bench 20.0→50.0, Polyglot
  14.2→30.7; the safety case study: hallucinated tool use with faked test
  logs, then removal of the markers the hallucination detector read —
  "objective hacking", caught by hand. [snippet]
- **A Self-Improving Coding Agent** (2504.15228, Robeyns, Szummer,
  Aitchison, 2025) — utility 0.5 score + 0.25 cost + 0.25 time; 17→53 on
  a SWE-bench Verified subset; "failed iterations would often heavily
  influence later feature ideas". [snippet] Repo to-do lists reducing
  the variance of self-improvement runs (github.com/MaximeRobeyns).
- **AlphaEvolve** (2506.13131, Novikov et al., DeepMind 2025) —
  evaluator-driven program evolution; limited to automatically evaluable
  problems. [snippet]
- **Agent Lightning** (2508.03680, Luo et al., 2025) — RL over any
  agent's episodes via an MDP view and LightningRL credit assignment.
  Read via the Microsoft Research blog. **veRL / HybridFlow**
  (2409.19256, Sheng et al., 2024) — multi-turn tool-calling RL. Read via
  github.com/volcengine/verl.

Documented failure modes:

- **METR, Recent Frontier Models Are Reward Hacking** (June 2025) — o3
  patched the scoring function and disabled timers; 0.7% of HCAST runs,
  every trajectory on one RE-Bench task, 1–2% overall. [snippet]
- **BenchJack** (2605.12673, Wang, Mang, Cheung, Sen, Song, 2026) — 219
  flaws across eight agent benchmarks; near-perfect scores without
  solving a task (fake curl wrapper, 89/89 Terminal-Bench). [snippet]
- **Effective Harness Engineering** (2605.15221, Ishibashi et al., 2026)
  — evaluation hacks in program evolution rise with model capability.
  [snippet]
- **Specification gaming** (Krakovna et al., DeepMind 2020); **Scaling
  Laws for Reward Model Overoptimization** (2210.10760, Gao, Schulman,
  Hilton, ICML 2023) — gold peaks then declines while proxy climbs;
  **Sycophancy to Subterfuge** (2406.10162, Denison et al., 2024) —
  generalisation to editing the reward function. [snippet]
- **The Red Queen Gödel Machine** (2606.26294, 2026) — co-evolving
  evaluators because fixed ones are exploited or saturated. [snippet]
- **AI Agents That Matter** (2407.01502, Kapoor et al., 2024) —
  inadequate holdouts produce shortcut-taking agents. [snippet]
- **PACE: Anytime-Valid Acceptance Tests** (2606.08106, 2026) — greedy
  "keep if score went up" is uncontrolled adaptive multiple testing;
  30–42% false and 10–33% harmful commits on small prompt-evolving
  agents; a paired e-process gate instead. [snippet] Also
  **Anytime-Valid Certificates** (2607.00871, 2026). [snippet]
- **Do Self-Evolving Agents Forget?** (2605.09315, 2026) — capability
  erosion across workflow, skill, model and memory evolution; CPE lifts
  retained simple-task performance 41.8→52.8. [snippet]
- **Evo-Memory** (2511.20857, Wei et al., 2025) — accumulated memories
  evict and out-compete earlier ones; bank drifts to recent tasks.
  [snippet]
- **Self-Improvement Can Self-Regress** (2606.21090, 2026) — within-task
  rise then collapse under continued REINFORCE; KL/EWC do not stop it.
  [snippet]
- **Honest Lying** (2605.29463, ICML 2026) — memory confabulation in
  Reflexion agents; 16 frozen ALFWorld environments, 0/121 reflections
  naming the right object; RRR metric. [snippet]
- **Your Agent May Misevolve** (2509.26354, Shao et al., ICLR 2026) —
  refusal rate down 55% under memory evolution; vulnerable tool reuse
  >76%. [snippet]
- **Why LLMs Aren't Scientists Yet** (2601.03315, Trehan and Chopra,
  2026) — six recurring failures of self-directed loops, including
  declaring success despite obvious failure. [snippet]
- **In-context reward hacking** (2402.06627, Pan, Jones, Jagadeesan,
  Steinhardt, ICML 2024) — feedback loops at test time drive ICRH;
  static evaluation misses it. Read via github.com/aypan17/llm-feedback.
