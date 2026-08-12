# The Agent Eval / Observability Landscape (August 2026)

*Purpose: figure out what AgentDiff should build next by looking honestly at what everyone else already ships.*

> **Method and verification limits.** Direct page fetching (WebFetch, curl) was blocked by this
> session's egress policy for every vendor and arXiv domain attempted, and the GitHub MCP tools are
> scoped to this repo only. Everything below is therefore sourced from web-search result summaries,
> and each claim carries the URL the claim came from — but **I could not open those pages to confirm
> them line by line**. Treat pricing numbers and dated announcements as "reported by search, not
> primary-verified". Where a search returned nothing usable I write `unknown` rather than guessing.

---

## 1. Orientation

The market splits into four layers that are frequently marketed as one product. **(a) Tracing /
observability** — capture spans from a running agent, store them, render a tree: Langfuse, Arize
Phoenix, W&B Weave, Opik, Galileo (now being absorbed into Splunk after Cisco announced intent to
acquire on 9 April 2026, per [Cisco](https://blogs.cisco.com/news/cisco-announces-the-intent-to-acquire-galileo)
and [SiliconANGLE](https://siliconangle.com/2026/04/09/cisco-buys-galileo-strengthen-splunks-agentic-monitoring-capabilities/)).
**(b) Scoring / evals** — attach a grader (code, LLM-as-judge, or human) to a dataset and produce a
number: Braintrust, LangSmith, OpenAI's Evals API, Anthropic's Console eval tool. **(c) Benchmarks /
leaderboards** — a fixed task set and a harness: AgentBench, τ-bench, HAL, AgentCompass. **(d)
Comparison / diagnosis** — given runs that already exist, explain *why* they differ. Layer (d) is
thin: the incumbents' comparison story is "put two experiments in side-by-side columns and diff the
text" ([Braintrust](https://www.braintrust.dev/docs/evaluate/compare-experiments)), or "LLM-cluster
the traces into failure modes" ([LangChain Insights Agent](https://www.langchain.com/blog/from-traces-to-insights-understanding-agent-behavior-at-scale)),
of which a public critique notes that "multi-step causal analysis is manual"
([Latitude](https://latitude.so/blog/langsmith-alternatives-for-ai-agents)). AgentDiff sits squarely
and only in (d): it consumes traces it did not capture, scores nothing with an LLM, and produces a
structural explanation of the difference between two runs. That is a deliberate, defensible niche —
but it also means AgentDiff has *no distribution surface of its own* and depends entirely on layer
(a) for input.

---

## 2. Comparison table

| Product | Unit of analysis | Trace capture | Scoring approach | Multi-run / variance | Cross-agent comparison | Failure attribution | Cost analysis | OSS / self-host |
|---|---|---|---|---|---|---|---|---|
| **Braintrust** | Experiment row (input→output→score) | Own SDK + Brainstore trace DB ([review](https://aitoolsbakery.com/blog/braintrust-review/)) | Autoevals library, LLM-judge, code scorers; `Loop` agent generates scorers/datasets ([review](https://aitoolsbakery.com/blog/braintrust-review/)) | "Trials" within one experiment; docs advise larger datasets over repeat runs ([best practices](https://www.braintrust.dev/docs/evaluate/best-practices)) | Side-by-side experiment diff, text-level ([docs](https://www.braintrust.dev/docs/evaluate/compare-experiments)) | Manual trace inspection | Token/latency/cost on traces ([review](https://aitoolsbakery.com/blog/braintrust-review/)) | No; Starter free ~1GB/10k scores, Pro ~$249/mo ([pricing](https://www.cekura.ai/blogs/braintrust-pricing)) |
| **LangSmith** | Run / trace + dataset example | Own SDK; OTel support added 2026 ([review](https://aitoolsbakery.com/blog/langsmith-review/)) | LLM-judge, code, human review; pytest/Vitest/CI gating ([LangChain](https://www.langchain.com/langsmith/evaluation)) | Repetitions supported; no public variance-verdict product | Side-by-side run compare ([Arize roundup](https://arize.com/blog/best-ai-observability-tools-for-autonomous-agents-in-2026/)) | Insights Agent LLM-clusters failure modes; causal chain manual ([Latitude](https://latitude.so/blog/langsmith-alternatives-for-ai-agents)) | Yes, per-trace | Self-host is Enterprise-only ([review](https://aitoolsbakery.com/blog/langsmith-review/)) |
| **Arize Phoenix / AX** | OTel span tree | OpenTelemetry-native ([guide](https://baeseokjae.github.io/posts/arize-phoenix-observability-guide-2026/)) | LLM-judge + heuristic evals; trajectory evaluators ([Arize docs](https://arize.com/docs/ax/evaluate/evaluators/trace-and-session-evals/trace-level-evaluations/agent-trajectory-evaluations)) | unknown | Experiment compare | unknown / eval-score based | Yes | Phoenix under Elastic License 2.0, free unlimited self-host; AX Pro ~$50/mo ([license](https://arize.com/docs/phoenix/self-hosting/license), [pricing](https://costbench.com/software/ai-observability/arize-phoenix/)) |
| **W&B Weave** | Traced function call / `weave.Evaluation` | One-line SDK; auto-logs MCP agent traces ([W&B](https://wandb.ai/site/agents/)) | Pre-built / third-party / custom scorers + guardrails ([announcement](https://wandb.ai/wandb_fc/product-announcements-fc/reports/New-in-W-B-Weave-Observability-and-continuous-improvement-for-production-agents--VmlldzoxNzAzMTcxNg)) | unknown | Comparable eval reports | Out-of-the-box "failure mode signals" ([announcement](https://wandb.ai/wandb_fc/product-announcements-fc/reports/New-in-W-B-Weave-Observability-and-continuous-improvement-for-production-agents--VmlldzoxNzAzMTcxNg)) | Yes | Weave toolkit open source ([docs](https://docs.wandb.ai/weave/open-source)); Free / Pro $60/mo, ingestion metered ([pricing](https://wandb.ai/site/pricing/)) |
| **Langfuse** | Trace / observation | Own SDK + OTel | LLM-judge, custom evals, **human annotation queues** | unknown | Dataset-run compare | unknown | Yes | **Strongest**: all product features MIT since June 2025, full self-host, no caps; acquired by ClickHouse Jan 2026 ([teardown](https://dev.to/beton/langfuse-pricing-teardown-2026-2pi9), [self-hosting](https://langfuse.com/self-hosting)) |
| **OpenAI Evals** | Eval definition + eval run | Trace grading over agent traces ([docs](https://developers.openai.com/api/docs/guides/trace-grading)) | Model-graded, exact-match, custom Python; tool-call grading ([agent evals](https://developers.openai.com/api/docs/guides/agent-evals)) | unknown | No | Tool-call vs final-answer split | unknown | OSS `openai-evals` package; **hosted platform read-only 31 Oct 2026, shutdown 30 Nov 2026** ([API ref](https://qaskills.sh/blog/openai-evals-api-reference-2026)) |
| **Anthropic** | Prompt / test case; agent = harness+model ([engineering](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)) | Agent SDK hooks | Console Evaluate tab, grading per case ([docs](https://platform.claude.com/docs/en/test-and-evaluate/eval-tool)) | Benchmark mode records pass rate, time, tokens ([writeup](https://zenvanriel.com/ai-engineer-blog/claude-agent-skills-software-testing-rigor/)) | Model comparison in skill benchmarks | No | Tokens | Console-hosted; patterns published, framework not |
| **Galileo → Splunk** | Production trace | Own SDK | Luna-2 small-model evaluators, <200ms, ~$0.02/M tokens ([docs](https://docs.galileo.ai/concepts/luna/luna)) | unknown | unknown | **Insights Engine** clusters similar failures across traces ([Galileo](https://docs.galileo.ai/concepts/luna/luna)) | Yes | No; free 5k traces/mo, Pro from $100 ([review](https://appsecsanta.com/galileo-ai)) |
| **Comet Opik** | Trace / span; trajectory evaluators ([docs](https://www.comet.com/docs/opik/evaluation/evaluate_agent_trajectory)) | Own SDK + OTel | LLM-judge, heuristics, Agent Optimizer ([Comet](https://www.comet.com/site/products/opik/)) | unknown | Experiment compare | unknown | Yes | **Apache 2.0, full feature set** ([Comet](https://www.comet.com/site/products/opik/)) |
| **HAL (Princeton)** | Agent rollout on a benchmark | Its own harness across VMs | Benchmark scorers + **LLM-aided log analysis (Docent)** | Many rollouts (21,730 across 9 models × 9 benchmarks, ~$40k) ([paper](https://arxiv.org/abs/2510.11977)) | Yes — models × scaffolds × benchmarks, **cost-aware** ([HAL](https://hal.cs.princeton.edu/)) | Behavioral findings (shortcutting, unsafe actions) ([paper](https://arxiv.org/abs/2510.11977)) | **Central** — dollar cost is a first-class axis | Open harness ([GitHub](https://github.com/princeton-pli/hal-harness)) |
| **τ-bench / τ²-bench** | Task episode | Benchmark harness | Rule-based DB-state check | **pass^k** — all k attempts must succeed ([paper](https://arxiv.org/abs/2406.12045), [τ²](https://www.emergentmind.com/topics/tau2-bench)) | Model-vs-model on a fixed task set | No | No | Open |
| **AgentBench** | Task outcome | Harness | Task success | No | Model ranking | No — outcome-only, root causes hard ([paper](https://arxiv.org/pdf/2308.03688)) | No | Open |
| **AgentCompass / MAST** | Trajectory (AgentCompass); failure mode (MAST) | Consumes traces | LLM debugger pipeline: identify → cluster → score → summarize ([arXiv](https://arxiv.org/abs/2509.14647)) | unknown | No | **Yes**, LLM-based; MAST gives a 14-mode / 3-category taxonomy over 1,600+ annotated traces ([arXiv](https://arxiv.org/abs/2503.13657), [GitHub](https://github.com/multi-agent-systems-failure-taxonomy/MAST)) | No | Research code / open |
| **AgentDiff** | **The divergence between two aligned trajectories** | None — consumes JSON; `convert` adapters for OTel/OpenAI | Deterministic, no LLM: alignment similarity, claim provenance, coverage-vs-expected | `runs` command: stable-pass/fail/flaky, CV, divergence reproducibility, medoid runs | **Core competence**: pairwise, fleet ranking, similarity clustering, portfolio/routing | Causal chain root→propagation→outcome, with counterfactual estimate | Yes: per-step and aggregate deltas, saving-per-task | Fully open, stdlib-only, no server |

---

## 3. What they do better than AgentDiff

1. **Live trace capture and integrations.** Every incumbent has an SDK, and most speak
   OpenTelemetry. AgentDiff has two hand-written `convert` adapters. **Approximate cheaply** — do
   not build a collector, but do widen the import surface (see rec. #1). Note that the OTel GenAI
   semantic conventions are still *Development* status as of July 2026 with no versioned release to
   pin against ([John Hodge](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/),
   [DEV](https://dev.to/azena-ai/opentelemetrys-genai-semantic-conventions-are-not-stable-yet-heres-what-actually-shipped-in-2026-3mke)),
   so target the shape, not a frozen spec.
2. **Hosted UI, search, retention, sharing.** AgentDiff emits static HTML. **Do not copy** — a
   server is a different company. The static-file property is a genuine advantage in air-gapped and
   CI contexts.
3. **LLM-as-judge scorer libraries** (Autoevals, Phoenix evals, Luna-2, Opik). **Do not copy the
   judges; copy the interface** — accept externally-produced scores as input (rec. #4).
4. **Dataset and prompt versioning.** Braintrust and Langfuse both manage datasets/prompts as
   first-class versioned objects. **Do not copy.**
5. **Human annotation queues** (Langfuse, LangSmith). Hamel Husain's tooling guidance calls error
   analysis "the highest ROI activity" and says tools should prioritize manual annotation, while
   warning against systems that both write the rubric and grade against it
   ([hamel.dev](https://hamel.dev/blog/posts/eval-tools/)). **Approximate cheaply**: a
   divergence-triage file (rec. #5) is the 5% of an annotation queue that matters here.
6. **Prompt playgrounds.** **Do not copy.**
7. **Alerting and production monitoring.** **Do not copy** — AgentDiff is an offline analysis tool.
8. **Failure-mode clustering at scale** (LangSmith Insights, Galileo Insights Engine). This one is a
   real gap and AgentDiff can do it *deterministically* over divergence signatures. **Copy the
   outcome, not the method** (rec. #3).
9. **Cost-controlled ranking discipline.** HAL treats dollars as a first-class axis and asks what a
   1% accuracy gain at 10× cost is worth ([Medium summary](https://medium.com/@sulbha.jindal/agent-evaluation-holistic-agent-leaderboard-hal-cc20ab62cb88)).
   AgentDiff has the numbers but not the Pareto framing. **Copy** (rec. #6).

---

## 4. What AgentDiff does that none of them do — verified, with retractions

**Holds up:**

- **Pairwise trajectory alignment as the primary unit.** No commercial product surfaced does
  sequence alignment across two runs; the shipped state of the art is a text diff of two experiment
  columns ([Braintrust](https://www.braintrust.dev/docs/evaluate/compare-experiments)). *Caveat:* this
  is **not novel in research** — TRACEPROBE defines "divergence spans… unmatched or reordered after
  alignment of two runs" ([arXiv 2607.06184](https://arxiv.org/pdf/2607.06184)) and Counterfactual
  Trace Auditing emits divergence records over aligned intent pairs
  ([arXiv 2605.11946](https://arxiv.org/html/2605.11946v1)). Claim = *no product*, not *no one*.
- **First-divergence detection with a typed cause.** Same story: JEF-Hinter identifies "the first
  action where desired and undesired trajectories diverge" ([arXiv 2510.04373](https://arxiv.org/pdf/2510.04373)),
  but no product ships it.
- **Deterministic causal failure attribution with explicit propagation scores.** Competitors either
  cluster failures with an LLM (LangSmith, Galileo) or run an LLM debugger pipeline (AgentCompass,
  [arXiv 2509.14647](https://arxiv.org/abs/2509.14647)). A reproducible, zero-LLM root→propagation→
  outcome chain appears unique.
- **Behavioral similarity, redundancy and complementarity between agents in a fleet.** No product or
  paper surfaced does "these two agents are 93% the same, drop the expensive one." Strongest claim.
- **Semantic claim provenance, grounding, circular-corroboration and internal-contradiction checks
  without an LLM.** No competitor equivalent surfaced.
- **Multi-run divergence *reproducibility*** (does the same divergence recur across runs?). Platforms
  support repetitions; τ-bench gives pass^k for outcomes
  ([arXiv 2406.12045](https://arxiv.org/abs/2406.12045)); nobody surfaced reports whether the *cause*
  reproduces.

**Retracted / must be softened:**

- ~~"Counterfactual replay is unique."~~ **Drop it.** Retrace is a shipping product that forks a
  trace at any span, cascade-replays the suffix, and shows a side-by-side diff with cost and latency
  deltas ([retraceai.tech](https://retraceai.tech/)); Causal Agent Replay formalizes the same idea as
  a do-operation on a structural causal model
  ([arXiv 2606.08275](https://arxiv.org/abs/2606.08275)). AgentDiff's version is *different, not
  first*: it **estimates** the counterfactual by splicing existing traces, so it needs no live agent
  and no LLM spend — a real advantage, but the honest framing is "offline counterfactual estimation",
  not "the only counterfactual replay".
- **Portfolio/routing selection — soften.** Per-query model routing is a whole product category
  (Not Diamond, Martian, RouteLLM;
  [awesome-ai-model-routing](https://github.com/Not-Diamond/awesome-ai-model-routing)) and
  RouterArena exists to compare routers ([arXiv 2510.00202](https://arxiv.org/html/2510.00202v1)).
  What is distinctive is *post-hoc portfolio selection from trace evidence with an explicit oracle
  ceiling* — keep that phrasing, not "nobody does routing".

---

## 5. Recommendations, ranked

All of these are pure-stdlib and deterministic unless flagged.

1. **Golden / reference-trajectory mode.** Compare one run against a curated reference trajectory
   instead of a second agent. *Why:* AgentDiff's hard requirement for two agents on the same task is
   its biggest adoption barrier — most teams have one agent and want a trajectory regression test.
   *Effort:* small; the reference is just a trajectory, the whole align/diverge/attribute stack
   applies unchanged. *Determinism:* preserved.
2. **Broaden the import surface.** Adapters for the OTel GenAI span tree (`invoke_agent` / `chat` /
   `execute_tool`), Langfuse and Phoenix trace exports, plus JSONL/directory streaming and a
   `--dry-run` mapping report. *Why:* nobody will hand-write SCHEMA JSON; layer (a) owns the data.
   *Effort:* medium, mostly mapping tables and heuristics you already have. *Determinism:* preserved.
3. **Deterministic divergence clustering across a batch.** Group divergences into recurring patterns
   by signature (kind + step types + tool + normalized text shingles), agglomerated with a fixed
   threshold. *Why:* this is the reproducible answer to LangSmith Insights and Galileo's Insights
   Engine, and it turns per-task findings into "your agent has 3 systematic problems". *Effort:*
   medium — clustering over hashed features is stdlib-friendly. *Determinism:* preserved.
4. **External-score ingestion and correlation.** Optional `scores` on steps/outcomes (from any judge
   or human), then report which divergence kinds predict low scores. *Why:* meets the market where
   it is without owning a judge; directly serves Hamel's "error analysis first" framing
   ([hamel.dev](https://hamel.dev/blog/posts/eval-tools/)). *Effort:* small. *Determinism:*
   preserved — the LLM lives outside the engine, and its output is recorded input.
5. **Divergence triage / suppression file.** A `.agentdiffignore`-style rule set marking known-benign
   divergences, with a stable fingerprint per divergence so the gate stops re-reporting them.
   *Why:* the loudest observability complaint is noise volume and review cost
   ([digitalapplied](https://www.digitalapplied.com/blog/agent-observability-2026-evals-traces-cost-guide)).
   *Effort:* small. *Determinism:* preserved.
6. **Statistical honesty layer.** Bootstrap confidence intervals on every aggregate delta, pass^k
   alongside pass@1, and a "minimum runs to detect this delta" helper; make `gate` fail only on
   deltas outside the CI. *Why:* current fixed thresholds can't distinguish a regression from noise,
   and the field's own critique is that a single run can make a bad change look good
   ([Braintrust best practices](https://www.braintrust.dev/docs/evaluate/best-practices)).
   *Effort:* small-medium, `random` + `math` only. *Determinism:* preserved with a fixed seed —
   seed must be recorded in the report.
7. **Cost-aware Pareto framing in `fleet`.** Emit the accuracy/cost Pareto frontier, dominated
   agents, and cost-per-additional-success — HAL's framing applied to a private fleet
   ([HAL](https://hal.cs.princeton.edu/)). *Effort:* small. *Determinism:* preserved.
8. **Adopt MAST's published failure taxonomy** as an optional second labelling of divergence kinds
   (rule-based mapping from AgentDiff's 5 kinds into MAST's 14 modes / 3 categories,
   [arXiv 2503.13657](https://arxiv.org/abs/2503.13657)). *Why:* makes findings comparable and
   citable instead of bespoke. *Effort:* small. *Determinism:* preserved.
9. **CI-native output formats.** JUnit XML + SARIF + a PR-comment markdown block from `gate`.
   *Why:* every competitor's CI story is a paid hosted integration; this one is free and lands
   AgentDiff in pipelines. *Effort:* small. *Determinism:* preserved.
10. **Optional, strictly-segregated LLM narration.** A separate module that rewrites the existing
    `narrative` fields in better prose. **This is the only recommendation that touches the design
    principle.** *Gained:* readability, which is genuinely where hosted tools win. *Lost:*
    reproducibility, offline operation, zero-dependency install. *Verdict:* build it only as an
    out-of-tree plugin that may **never** alter a number, a verdict, or a gate exit code — and keep
    the default install LLM-free. Rank it last deliberately.

---

## 6. Anti-recommendations

- **Do not build a tracing SDK, collector, or storage backend.** Langfuse is MIT with unlimited
  self-host and now sits behind ClickHouse ([teardown](https://dev.to/beton/langfuse-pricing-teardown-2026-2pi9));
  Phoenix is free to self-host with no feature gates ([license](https://arize.com/docs/phoenix/self-hosting/license));
  Opik is Apache 2.0 end-to-end ([Comet](https://www.comet.com/site/products/opik/)). You would be
  entering the most commoditized layer against better-funded, already-free incumbents.
- **Do not ship an LLM-as-judge scorer library.** Autoevals, Phoenix evals, Opik and Luna-2 already
  cover it, and Luna-2 does it at ~$0.02/M tokens and sub-200ms
  ([Galileo docs](https://docs.galileo.ai/concepts/luna/luna)). It would also destroy the one
  property that differentiates AgentDiff.
- **Do not build prompt management or a playground.** Fully commoditized (Langfuse, Braintrust,
  Phoenix, Opik).
- **Do not launch a benchmark or leaderboard.** HAL is the cost-aware third-party leaderboard with
  21,730 rollouts behind it and an ICLR 2026 paper ([HAL](https://hal.cs.princeton.edu/)); τ-bench
  owns reliability metrics. Integrate as a consumer of their logs instead.
- **Do not build live re-execution/replay.** It requires an agent runtime and LLM spend, and Retrace
  already ships it ([retraceai.tech](https://retraceai.tech/)). AgentDiff's offline estimate is the
  cheaper, complementary product.
- **Do not build a per-query router.** Not Diamond, Martian and RouteLLM own that category
  ([list](https://github.com/Not-Diamond/awesome-ai-model-routing)); stay on post-hoc portfolio
  analysis.
- **Do not chase real-time alerting or a "single pane of glass" dashboard.** That is the layer Cisco
  just bought Galileo to own inside Splunk
  ([Cisco](https://blogs.cisco.com/news/cisco-announces-the-intent-to-acquire-galileo)).
- **Do not add a database or a server process.** The static, dependency-free artifact is a feature
  in CI, air-gapped, and research contexts — it is why anyone would run this over a hosted tool.
