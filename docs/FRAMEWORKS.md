# Agent frameworks, protocols and domain specs — what the field talks about, what AgentDiff emulates (September 2026)

*Purpose: read the 2025–26 agent stack — protocols, frameworks, benchmarks, safety — and decide which of its
ideas this project should carry, as principles, into its own deterministic design. Nothing here copies another
tool's wording, UI or code; where a convention is a wire format (a tool-name prefix, a span name) we read it,
because that is the only way to accept the traces. Method as in `LANDSCAPE.md`: web-search summaries plus the
few pages the session's egress allowed (Claude Code hooks reference, the lmnr repository README); every claim
carries its URL, and pricing or dates are "reported, not primary-verified".*

## 1. The five things people talk about most

1. **One wire format for agent traces: OpenTelemetry GenAI.** An `invoke_agent` span holds `chat` spans for
   model calls and `execute_tool` spans for tools; `gen_ai.operation.name` now covers `create_agent`,
   `invoke_agent`, `execute_tool`, retrieval and memory operations. As of June 2026 the `gen_ai.*` conventions
   moved into their own repository, and as of July every one of them is still *Development* — no stable release
   to pin ([Uptrace](https://uptrace.dev/blog/opentelemetry-ai-systems), [DEV](https://dev.to/azena-ai/opentelemetrys-genai-semantic-conventions-are-not-stable-yet-heres-what-actually-shipped-in-2026-3mke),
   [veraexmachina](https://veraexmachina.com/tech/opentelemetry-genai-agent-observability-production/)). Google
   ADK emits exactly these span names plus `gcp.vertex.agent.*` payload attributes
   ([ADK docs](https://adk.dev/observability/traces/), [Cloud Trace](https://docs.cloud.google.com/stackdriver/docs/instrumentation/ai-agent-adk));
   Laminar, Langfuse, Phoenix and Opik ingest them.
2. **Two interoperability protocols, MCP for tools and A2A for agents.** MCP names tools by server; Claude Code
   and the Claude Agent SDK expose them as `mcp__<server>__<tool>` (plugin servers as
   `mcp__plugin_<plugin>_<server>__<tool>`), and permission rules and hook matchers key on that prefix
   ([Claude Code hooks](https://code.claude.com/docs/en/hooks), [naming issue #18763](https://github.com/anthropics/claude-code/issues/18763)).
   A2A (Google, now Linux Foundation; v1.0.1 May 2026; 150+ organisations) has agents publish an Agent Card and
   delegate tasks over JSON-RPC ([Wikipedia](https://en.wikipedia.org/wiki/Agent2Agent), [Galileo](https://galileo.ai/blog/google-agent2agent-a2a-protocol-guide)).
   The research complaint is that neither protocol can express governance — who may delegate what, under which
   policy ([arXiv 2606.31498](https://arxiv.org/pdf/2606.31498), [arXiv 2602.11327](https://arxiv.org/pdf/2602.11327)).
3. **Handoffs, guardrails and permission decisions are the multi-agent primitives.** In the OpenAI Agents SDK a
   handoff is a tool call named `transfer_to_<agent>`, and the SDK's own trace records agent, generation,
   function, handoff (source and destination) and guardrail (name, triggered) spans through a pluggable trace
   processor ([handoffs](https://openai.github.io/openai-agents-python/handoffs/), [span data](https://openai.github.io/openai-agents-python/ref/tracing/span_data/),
   [tracing](https://openai.github.io/openai-agents-python/tracing/)). Claude Code and the Agent SDK decide every
   tool call through hooks → deny rules → ask rules → mode → allow rules → a `canUseTool` callback; a
   `PreToolUse` hook returns `allow | deny | ask` with a reason, and `SubagentStop` carries the sub-agent's
   id and type ([hooks](https://code.claude.com/docs/en/hooks), [permissions](https://code.claude.com/docs/en/agent-sdk/permissions),
   [Praesidia](https://praesidia.ai/blog/claude-agent-sdk-permission-model-explained)). Runtime-enforced policy
   languages generalise this: AgentSpec (ICSE 2026) states trigger → predicate → enforcement rules and blocks
   >90% of unsafe code-agent actions ([arXiv 2503.18666](https://arxiv.org/abs/2503.18666)); VIGIL and AgentSPEX
   follow ([arXiv 2606.26524](https://arxiv.org/pdf/2606.26524), [arXiv 2604.13346](https://arxiv.org/pdf/2604.13346)).
4. **Durable execution is where production agents went.** LangGraph checkpoints graph state between nodes
   (thread, namespace, checkpoint id) but a checkpoint is not durable execution; the shipped pattern is LangGraph
   for the reasoning loop inside Temporal for the workflow ([Temporal](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution),
   [LangChain](https://www.langchain.com/resources/langgraph-vs-temporal), [checkpoint API](https://reference.langchain.com/python/langgraph.checkpoint)).
   The framework field settled: LangGraph for stateful production graphs, CrewAI for role-based crews,
   Pydantic AI (v2, June 2026) for typed agents, AutoGen in maintenance with AG2 as the community fork
   ([DEV guide](https://dev.to/linou518/the-2026-ai-agent-framework-decision-guide-langgraph-vs-crewai-vs-pydantic-ai-b2h),
   [AgenticWire](https://www.agenticwire.news/article/pydantic-ai-vs-autogen)).
5. **Evaluation moved from outcome to trajectory, and to the harness.** τ²-bench scores agent–user–policy
   episodes with pass^k ([arXiv 2506.07982](https://arxiv.org/pdf/2506.07982)); Terminal-Bench's 89
   human-verified terminal tasks are the sandbox standard ([arXiv 2601.11868](https://arxiv.org/pdf/2601.11868));
   SWE-bench Pro (1,865 tasks, 41 repos) needed a *Verified* re-issue after reward hacking and task-quality
   audits ([arXiv 2609.08149](https://arxiv.org/abs/2609.08149)); HAL runs nine benchmarks with cost as an axis
   ([arXiv 2603.23749](https://arxiv.org/pdf/2603.23749)). Failure taxonomies: MAST's 14 modes in three categories
   ([NeurIPS 2025](https://neurips.cc/virtual/2025/poster/121528), [production replication over 639k steps](https://github.com/hugomn/mast-taxonomy-production-telemetry)),
   TRAIL's 148 OpenTelemetry traces with 841 span-level errors and 11% joint accuracy for the best model
   ([arXiv 2505.08638](https://arxiv.org/abs/2505.08638)); ADK's `tool_trajectory` criterion with EXACT /
   IN_ORDER / ANY_ORDER matching ([ADK criteria](https://google.github.io/adk-docs/evaluate/criteria/)); and a
   position paper asking that nobody compare agents without disclosing the harness
   ([arXiv 2605.23950](https://arxiv.org/pdf/2605.23950)).

**Domain specs people evaluate against.** Coding (SWE-bench Verified/Pro, Terminal-Bench, ChainSWE, RoadmapBench —
[arXiv 2607.02606](https://arxiv.org/pdf/2607.02606), [arXiv 2605.15846](https://arxiv.org/pdf/2605.15846)); deep
research (BrowseComp-Plus with a fixed corpus so retriever and agent separate — [ACL 2026](https://aclanthology.org/2026.acl-long.1023/),
DRBench for enterprise research — [arXiv 2510.00172](https://arxiv.org/pdf/2510.00172)); customer support
(τ²-bench airline / retail / telecom); computer use (OSWorld-Verified — [BenchLM](https://benchlm.ai/blog/posts/osworld-verified-computer-use-benchmark));
data/analytics (text-to-SQL and notebook agents, the least standardised).

## 2. What this project already covers

Trajectory alignment, first divergence, deterministic attribution, the span tree from nested `invoke_agent`
spans, the OTel and OpenAI adapters, Claude Code hooks and transcripts, hermetic replay, tool-permission checks
(`toolmatch.tool_permission`, deny beats allow), τ-bench's termination reasons, milestones over long runs,
Wilson intervals on every rate (`LANDSCAPE.md` §4, §7; `REPLAY.md`). The scorecard's tool correctness is already
ADK's ANY_ORDER match with an optional "no other tool" strictness.

## 3. What we emulate now, as principles (built in this pass)

- **Read the conventions, never guess.** `deepcompare.frameworks.detect` names a framework only from an adapter
  or source stamp (high), Claude Code's own tool set (medium alone, high with a stamp), `transfer_to_*`
  handoffs (OpenAI Agents SDK, medium), or an identity field (low); plain tool names never name a framework. It
  lists MCP servers from the `mcp__<server>__<tool>` prefix (the dotted form only when the trace declares the
  tool MCP-backed), counts handoffs and whether the span's agent changed to the target, counts guardrail steps,
  reads permission decisions (an explicit `step.permission.decision` or the step's own text), marks `otel-genai`
  and `a2a` protocols, and returns `None` with the signals it did find otherwise. Never raises.
- **A domain is a spec, not a prompt.** `deepcompare.domains` carries five specs (coding, research, support,
  data, computer_use): expected tool *families* as name patterns, read-before-write and verify-after-write
  rules, forbidden and external tools, stop rules, milestone templates. `infer` says which domain a run's tools
  point at, with confidence; `apply` completes a golden task from a spec without overriding a key the task
  states; a golden task saying `"domain": "coding"` is completed by `load_golden`, which reports `domains`.
- **The harness is part of the result.** `python -m deepcompare frameworks <trace|dir>` prints, per trace,
  framework, protocols, MCP servers, handoffs, permission counts and inferred domain — the disclosure
  [arXiv 2605.23950](https://arxiv.org/pdf/2605.23950) asks for, read from the trace rather than claimed.

## 4. Tool-level transparency and next-prompt guidance: who does it, how, what to emulate

**Who does it, how.** Laminar (Apache-2.0, Rust, OpenTelemetry-native) puts every span, evaluation and "signal"
event behind SQL, builds dashboards from those queries, and defines *signals* as natural-language outcome
descriptions that are extracted as structured events, back-filled over history and fired on new traces; it also
ships a code-first eval SDK and datasets built from production traces ([repo](https://github.com/lmnr-ai/lmnr),
[laminar.sh](https://laminar.sh/), [launch post](https://laminar.sh/blog/2026-03-16-laminar-launch)). Langfuse's
observation-level model charts tool calls and tool definitions as metrics and slices latency and count per
observation name in custom dashboards ([custom dashboards](https://langfuse.com/docs/metrics/features/custom-dashboards),
[v4 dashboard changes](https://langfuse.com/docs/metrics/v4-dashboard-changes)). LangSmith's Insights Agent is an
LLM-driven analysis over production traces that clusters usage patterns and failure modes on request
([docs](https://docs.langchain.com/langsmith/insights), [blog](https://blog.langchain.com/insights-agent-multiturn-evals-langsmith/)).
Braintrust frames improvement as a loop — run, inspect what regressed as well as what improved, change the
prompt, re-run — and its Loop assistant proposes prompt revisions and scorers from failures
([loop](https://www.braintrust.dev/foundations/understanding-the-eval-improvement-loop),
[prompt optimization loop](https://www.braintrust.dev/articles/prompt-optimization-loop), [Loop](https://www.braintrust.dev/blog/loop)).
OpenAI's dataset-backed prompt optimizer rewrites prompts with a multi-agent process graded by user-defined
graders — and is being retired with the hosted Evals platform (read-only 31 Oct 2026)
([prompt optimizer](https://developers.openai.com/api/docs/guides/prompt-optimizer), [cookbook](https://developers.openai.com/cookbook/examples/optimize_prompts)).

**What to emulate, as principles, in our own design.** (a) *Per-tool statistics are a query over steps, not a
feature*: calls, errors, latency, useful-result share and which agents used the tool, computed deterministically
from the trace set — the scorecard already has the per-run counts; a per-tool roll-up across agents is a small
addition. (b) *Signals are named, back-fillable predicates* — ours are deterministic (a divergence signature, a
risk flag, a milestone), which is the property Laminar's LLM-extracted signals lack. (c) *Next-prompt guidance
must cite the step it came from*: the reasoning reader already names the decisive step and the counterfactual;
the guidance to surface is "the run that passed did X at step k where this one did Y", never a rewritten prompt
from a model. (d) *Show regressions beside improvements* — the gate already does; keep it the default. What not
to copy: an LLM analysis agent, SQL over a hosted store, a prompt rewriter (see `LANDSCAPE.md` §6).

## 5. Production agents next — the plan

**Trace sources to accept.** (1) OTel GenAI spans from any framework — already the `otel` adapter; extend it to
read `gen_ai.agent.name` handoffs, guardrail spans and `gcp.vertex.agent.*` payloads, and have `convert` stamp
`harness.adapter: "otel"` and `source.framework` (from the resource's `service.name` / instrumentation scope) so
detection is a fact, not a guess. (2) OpenAI Agents SDK trace export — a `TracingProcessor` that writes the
SDK's span list to JSON is one file; the adapter maps agent → span, function → tool step, handoff → a
`transfer_to_*` step plus a span change, guardrail → a step with `guardrail: {name, triggered}`. (3) Claude
Code hooks — extend the existing hook: `PreToolUse`/`PermissionRequest` decisions become `step.permission`
`{decision, reason, mode}` on the following tool step, `SubagentStop` closes a span named by `agent_type`,
`PostToolUseFailure` sets `error: true`. (4) LangGraph checkpoints — a checkpoint list (thread, namespace, step,
node, state writes) becomes one step per node execution with the node as the step name and the checkpoint id as
the span id; Temporal histories are the same shape one level up.

**What the recorder and adapters need.** Optional per-step fields the schema already tolerates: `permission`
(decision, reason, mode), `guardrail` (name, triggered, side), `mcp` (server, tool), `span.url` / `span.agent_card`
for A2A, and a per-trace `source` (`format`, `framework`, `sdk_version`). Each is *declared* by the source, and a
missing one reads as "unmeasured", in keeping with the schema's honesty rule.

**What the page would show.** The harness panel per run: framework and protocols with the signals, the MCP
servers used (calls, errors, latency per server), the handoff graph as part of the existing span tree, permission
decisions as marks on the timeline (asked / denied, with the reason), guardrail triggers as flags beside the risk
flags, and the inferred domain with the spec's rules that fired. Across a batch: per-tool statistics by agent, and
the domain spec's milestones as the progress axis for every run in that domain.

## 6. RL training frameworks: the bridge (September 2026)

*What each trainer expects in and hands out, read from the repositories' own files (docs hosts were blocked; GitHub was not) — reported, not run.*

**veRL (verl-project).** The agent loop runs the user's loop of model and tool calls and returns an `AgentLoopOutput`:
`prompt_ids`, `response_ids`, a `response_mask` (1 for policy tokens, 0 for tool-response tokens), `response_logprobs`,
`reward_score`, `num_turns` (user + assistant + tool), `metrics`, free `extra_fields`. The tool loop parses OpenAI-style
function calls, appends `role: tool` messages, stops on `max_assistant_turns` / `max_user_turns` or no call, and keeps
per-call `tool_rewards` and per-turn `turn_scores` in `extra_fields` ([agent_loop.py](https://github.com/verl-project/verl/blob/main/verl/experimental/agent_loop/agent_loop.py),
[tool_agent_loop.py](https://github.com/verl-project/verl/blob/main/verl/experimental/agent_loop/tool_agent_loop.py), [#3525](https://github.com/volcengine/verl/issues/3525)).
Reward: a manager calls `compute_score(data_source, solution_str, ground_truth, extra_info)` — the detokenised response
against the dataset's `reward_model.ground_truth` — and writes the score on the last response token; a dict return puts
its extra keys in `reward_extra_info`; `custom_reward_function.path` / `.name` name the file ([reward_function.rst](https://github.com/verl-project/verl/blob/main/docs/preparation/reward_function.rst),
[naive.py](https://github.com/verl-project/verl/blob/main/verl/workers/reward_manager/naive.py)). Tracing: `rollout.trace.backend` ∈ weave | mlflow | trackio,
`token2text` adds `prompt_text` / `response_text`, rollouts are tagged `sample_index`, `step`, `rollout_n`, `validate`
([rollout_trace.rst](https://github.com/verl-project/verl/blob/main/docs/advance/rollout_trace.rst)); `trainer.rollout_data_dir` dumps `{input, output, gts, score, step}` per
sample as JSONL ([ray_trainer.py](https://github.com/volcengine/verl/blob/main/verl/trainer/ppo/ray_trainer.py)). PPO, GRPO, DAPO ([agentic RL](https://verl.readthedocs.io/en/latest/start/agentic_rl.html)).

**Agent Lightning (Microsoft).** An episode is states and actions where each LLM call is the action; traces become
transitions `(state, action, reward, next state)` and LightningRL's credit assignment decides how much each call
contributed, so a single-step algorithm trains on grouped per-call samples ([MSR blog](https://www.microsoft.com/en-us/research/blog/agent-lightning-adding-reinforcement-learning-to-ai-agents-without-code-rewrites/),
[arXiv 2508.03680](https://arxiv.org/pdf/2508.03680)). Releases 0.2–0.3 store OpenTelemetry spans in a LightningStore (`rollout_id`, `attempt_id`,
`sequence_id`, `trace_id`, `span_id`, `parent_id`, `name`, `attributes`, `start_time`, `end_time`); rewards are spans named
`agentlightning.reward` / `.annotation` with `name` / `value`, LLM calls carry `gen_ai.*` (token ids from vLLM), and
`TracerTraceToTriplet` matches a reward to the LLM call it follows (`FIRST_OCCURRENCE`) or its first sibling to make
`(prompt, response, reward)` triplets ([traces v0.3.0](https://github.com/microsoft/agent-lightning/blob/v0.3.0/docs/tutorials/traces.md), [semconv.py](https://github.com/microsoft/agent-lightning/blob/v0.3.0/agentlightning/semconv.py),
[tracer.py](https://github.com/microsoft/agent-lightning/blob/v0.3.0/agentlightning/types/tracer.py), [triplet.py](https://github.com/microsoft/agent-lightning/blob/v0.3.0/agentlightning/adapter/triplet.py)). v1.0 replaces the tracer with an API Gateway
that proxies model requests and records `model_request` events (`model`, `request`, `response`, `usage`, `latency_ms`)
and `reward` events (`value`, `message`, `source`, `reason`) per rollout, then builds veRL samples ([README](https://github.com/microsoft/agent-lightning/blob/main/README.md),
[schemas.py](https://github.com/microsoft/agent-lightning/blob/main/agentlightning/schemas.py), [basics](https://github.com/microsoft/agent-lightning/blob/main/docs/05-basics.md), [releases](https://github.com/microsoft/agent-lightning/releases)).

**Others.** SkyRL hands a `GeneratorOutput` of `prompt_token_ids`, `response_ids`, `rewards` (one float per trajectory or
per token), `loss_masks`, `stop_reasons`, `rollout_metrics` to skyrl-train, veRL or Tinker ([base.py](https://github.com/NovaSky-AI/SkyRL/blob/main/skyrl/train/generators/base.py), [arXiv 2511.16108](https://arxiv.org/pdf/2511.16108)).
AReaL: a workflow is any class with `async def run(data, **kwargs)` returning a float (reward on the last completion) or
`dict[completion_id → reward]`, with `turn_discount` for multi-turn credit ([agent.md](https://github.com/inclusionAI/AReaL/blob/main/docs/en/customization/agent.md)). OpenRLHF: `AgentInstanceBase.reset/step`
returning `rewards`, `scores`, `environment_feedback`, `done`, `extra_logs`; reward functions `(queries, prompts, labels) →
{rewards, scores, extra_logs}` ([README](https://github.com/OpenRLHF/OpenRLHF/blob/main/README.md)). TRL `GRPOTrainer`: reward functions take `prompts`, `completions`,
`completion_ids`, `trainer_state`, `environments` and every dataset column as kwargs, return one float per completion or
`None`, summed under `reward_weights` ([grpo_trainer.md](https://github.com/huggingface/trl/blob/main/docs/source/grpo_trainer.md)). ART: a `Trajectory` of messages with a `reward` set by the
rollout or by RULER, an LLM judge ranking a group's trajectories against each other, then GRPO ([README](https://github.com/OpenPipe/ART/blob/main/README.md), [RULER](https://art.openpipe.ai/fundamentals/ruler)).
ROLL and slime: async rollouts, trajectory-wise (StarPO) and step-wise (GiGPO) optimisation, multi-turn tool loops behind Megatron + SGLang ([ROLL](https://github.com/alibaba/ROLL/blob/main/README.md), [slime](https://github.com/THUDM/slime)).

**What this project offers them.** In: `convert --format verl | agent-lightning` (auto-detected) reads the rollout record
or span / event export into SCHEMA trajectories — rewards on the steps (`reward`), the ground truth as `task.expected`,
the tool schemas, `num_turns`, `source.fidelity` counters saying what was paired, dropped or synthesised — so two
checkpoints on one prompt set compare like any two agents. Out: `rlexport --format verl-rewards` (per trajectory: the
recorded-or-shaped return, its terms by label, per-step rewards, milestones reached, the outcome), `verl-reward-fn` (a
`compute_score` serving those records by `extra_info["trajectory_id"]`), `preferences` (DPO pairs), `agent-lightning`
(per-step transitions). Every record says `recorded` or `shaped`.

**Where AgentDiff sits — my view.** Before the trainer it is a labeller: the reading and the diagnosis put a sourced label
on every step (the decisive step, what fed the answer, what was spent after the basis) — the dense per-step signal that
GRPO / DAPO's outcome reward and RULER's group ranking lack — and the milestones give a long task the intermediate reward
it otherwise gets only from a hand-written verifier. After the trainer it is a reader of rollouts: two checkpoints
compared pairwise on the same prompts show where the policy drifted (tools dropped, steps spent, answers unsourced),
which a mean-reward curve hides. It is not a live loop; three things stand between: a store trainers write and read at
training pace (veRL's reward loop is per sample and async; the rewards JSONL is a batch artefact); a reward computable
before the pair exists (a shaped reward needs a passing sibling or a golden task); and calling the shaped reward what it
is, a reward *model* — deterministic, but it reads text, and a policy trained against it will find what it cannot see. The
small next step: `rlexport` per training step over a checkpoint's rollouts, so `reward_extra_info` carries the terms.
