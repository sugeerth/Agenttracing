# The data layer: what the agent was told, what it read, and how that reached the answer

Every other section of a report reads what an agent *did*. The `data`
section reads what it was *given* and what came *in* — the prompt, the
instructions, the model, the corpus — and the chain from those to the
answer; and for a self-evolving agent, `data_evolution` reads how each
generation changed from the data its evidence episodes had read. It is
the tenth view of the page (Data) and the `data` and `step` tools of the
MCP server and the HTTP API.

The whole section is pure stdlib and deterministic; every number is a
count, a sum or one of two stated measures over recorded text and
steps, with its basis beside it; nothing is inferred beyond those
measures. A trace's own recorded model name is shown as recorded — it is
the trace's data — and the engine names no model of its own. Where a
trace carries no prompt, no model or no text, the section is
`measurable: false` with the reason, and the parts that can still be
read are still produced. SYNTHETIC is carried through from the trace's
harness block and the lineage's note.

## What each part reads

`data_run(traj)` — one run, a `Trajectory` or a raw trace dict (a report
side with its `task` supplied):

| part | reads | from |
|---|---|---|
| `task` | `id`, the `prompt` and its length, the `expected` answer and its length (`null` when none) | `task` as recorded |
| `agent.instructions` | the system prompt, its length and its **source**: `trace.agent.system_prompt`, `trace.agent.config` (a `system_prompt` key inside `config`), `lineage artifacts` (a generation's `artifacts.system_prompt`, handed in by the lineage section), or `null` when nothing records one | the trace, or the lineage |
| `agent.framework`, `tools_declared`, `tools_used` | the harness adapter when kept; the tool table the run was offered; each tool called, how often, and the effects seen on its steps | `harness.adapter`, `tools[]`, `steps[]` |
| `models` | which model produced which steps: `{model, steps, kinds {kind: n}, tokens, temperature, source}` — the step's own `model.model`/`model.name` telemetry when a step carries one (`source: steps[].model`), else the trace's declared `agent.model` for every step (`source: trace.agent.model`); a step with neither goes in a row with `model: null` | `steps[].model`, `agent.model` |
| `corpus` | every distinct source the run fetched (`search`, `retrieve`, `read`, `tool_call`), in first-read order: `{id, name, kind, input (≤ 200 chars), input_chars, output_chars, tokens, steps [indices], first_step, digest, error, outputs_differ}`; `total_chars`, `distinct`, `fetches`, `repeated_reads` | `steps[]` |
| `provenance` | the answer's typed values traced to the fetched outputs that carry them (below) | the answer step's output, else `outcome.answer`, else the answer step's input (`answer_source` says which); `steps[]` |
| `chain` | the graph data → model → agent → answer (below) | all of the above |

**Source identity** is the fetches section's rule for a repeat — the
tool name and the whitespace-normalised input — hashed
(`sha256`, first 16 hex characters; `corpus.id_basis` says so). The
same rule on both sides of a pair is what lets `corpus_diff` say which
sources were shared, which only one agent read, and the Jaccard of the
two sets. A repeated read joins its source's `steps`; `outputs_differ`
says whether a repeat returned different text; `digest` is of the first
output.

**Provenance.** The answer's *typed values* are what
`semantic.extract_from_text` finds — money, percents, durations,
versions, CVEs, URLs, dates and unit-adjacent numbers — deduplicated by
kind and normalised value. Each is traced to every fetched output
before the answer that carries it: the extractor finds the same
normalised value in the output, or the value's normalised text
(`semantic.normalize_for_containment`) is a substring of the output's
normalised text. `supported` is the count carried by at least one
fetched output, `unsupported` the rest; `grounded_share` and
`ungrounded_share` are the two counts over the total (`null` when the
answer carries no typed value); `grounded_in` lists each fetched step
with the share of the values it carries (its **overlap**) and the value
ids; `values` lists each value with its carrying steps. This is the
same typed rule the `reading` section's `answer_basis` uses, and on a
pair the section places the pair's own readings beside it verbatim
(`provenance.readings.<side>.answer_basis`, `.semantic`), so the counts
can be checked against each other.

**The chain.** Nodes: one `agent` node (its label the agent, `chars`
the instructions' length); one `data` node per fetch step (`chars` the
output size); one `model` node per `plan` and `reason` step, labelled
with the model that produced it (`tokens` the step's); the `answer`
node (`chars` the answer's). Edges, each with `kind`, `step`,
`overlap` and `basis`:

- `agent → model`, `agent → answer`: `produces` — the agent's own steps.
- `model → data`: `produces` when the fetch is the very next step (the
  model's turn issued the query).
- `data → model` and `data → answer`: `feeds` when the **chain overlap**
  is at least `CHAIN_OVERLAP = 0.2`, or when the fetch is the step just
  before; `basis` says `overlap`, `adjacent` or both, and `overlap` is
  `null` when the edge exists by adjacency alone.
- `data → answer`: `reaches`, one per fetched step in `grounded_in`,
  with the provenance overlap.

The chain overlap is the share of the model step's distinct normalised
tokens (whitespace tokens of the normalised text, at least
`TOKEN_MIN = 3` characters, at least one letter or digit) that occur
among the fetched output's tokens.

## The two overlaps and their limits

Both are containment shares over recorded text; neither is a judgement.

- The **provenance overlap** counts typed values only. An answer made of
  prose and no figure has no typed value, so its `grounded_share` is
  `null` and the section says so rather than 0 or 1; the ledger-agent
  demo below is mostly of this kind. A value carried by a fetched output
  is *supported*, not *true*: a search whose query the agent itself
  wrote returns text that carries the figure the agent searched for,
  and that counts. Whether two sources corroborate each other
  independently is the semantic section's `independence` reading, not
  this one's. A value carried by a page the agent misread is still
  carried.
- The **chain overlap** counts tokens, common words included, above
  three characters. It says that a model step *shares content* with a
  fetched output — not that the output caused the step. Adjacency draws
  an edge with no overlap at all, and is labelled so.

Neither measure reads a token count, a latency or a reward; the
`budget` and `fetches` sections do. Nothing here is estimated: a run
with no prompt is unmeasurable, not assumed to have had the task's.

## The pair, the aggregate, the lineage

Per pair (`report["data"]`): `a` and `b` as above; `task`;
`instructions_diff {same, hunks, added, removed, reason}` — a unified
diff by hunk when both sides record instructions, `same: null` with the
reason otherwise; `corpus_diff {shared, only_a, only_b, jaccard}`;
`models {a, b, same}`; `provenance {a, b, delta_grounded, readings}`
(`delta_grounded` = A's grounded share − B's, `null` when either answer
carries no typed value); a narrative.

Per aggregate (`runs` layouts and a lineage's last pair; the batch
aggregate never ran the aggregate scope): `agents {name: {runs,
models, instructions_digest (when every run records the same text),
instructions_distinct, sources_distinct, sources_shared_across_runs,
grounded_share_mean, grounded_runs, atoms, supported, synthetic}}`,
`tasks {task: {prompt_chars, expected}}`, a narrative.

Per lineage (`aggregate["data_evolution"]`, requires `evolution`, after
`coevolution`): `generations [{id, instructions {system_prompt, chars,
rules, skills, tools, memory_n, config, source}, digest}]` from the
artifacts, and one row per step:

| key | what | from |
|---|---|---|
| `evidence` | the episodes the step cites, how many were found in the parent and were failures, and per episode the sources it read, its fetches, its grounded share and its outcome | `evolution.steps[].evidence_check`; the parent's traces read with `data_run` |
| `change` | the diff summary, the prompt hunks, rules added and removed, config keys changed, protected paths touched | `evolution.steps[].diff` |
| `behaviour` | tool calls before and after, distinct sources before and after, mean grounded share before and after (over the episodes whose answer carries a typed value, and how many) | `evolution.generations[].tool_calls`; the generations' traces |
| `effect` | the verdict, the flags, the improvement intervals | `evolution.steps[]` |
| `eval` | the evolved eval's flags on the step (metric, delta, direction, learned), the learned ones, the eval generation the step advanced to | `coevolution.steps[].evolved`, `coevolution.eval_generations` |
| `reading` | one sentence: what the episodes read; what changed; how the behaviour moved; what the base eval said; what the evolved eval flags | the row |

## The demo, with the numbers

`agentdiff batch demo/traces` — two agents, `atlas-v2` and `bolt-v3`, on
eight tasks. On `t01_acme_revenue`:

**What both were told.** The same 110-character prompt — *Find ACME
Corp's total revenue for fiscal year 2025. Report a single dollar
figure and say where it came from.* — with a 13-character expected
answer, `$4.82 billion`. Each trace records its agent's instructions
(`agent.system_prompt`, `source: trace.agent.system_prompt`; invented
for the persona by the demo generator): atlas-v2's 309 characters,
bolt-v3's 246. They share their first line and their last, so
`instructions_diff` is one hunk, `@@ -1,5 +1,4 @@`, +2 −3 lines:
atlas-v2's *Plan before you search, and follow the plan.*, *Prefer the
primary source: …* and *Read the source before you answer.* against
bolt-v3's *Answer quickly: take the first result that gives the figure.*
and *Confirm it with one more search before you answer.* — the
difference the two personas were built on, now a diff on the page. A
report side carries no instructions (`AgentInfo.to_dict` keeps them off,
so a side is byte-identical with or without them): the section is read
from the trajectories at compare time, and `data_pair(report)` from the
sides alone reads *no instructions recorded on either side*.

**Which models the traces record.** Every plan, reason and answer step
carries `model: {name, temperature: 0.2}` — the trace's declared model
and the temperature, nothing else — so those steps are attributed with
`source: steps[].model`: atlas-v2's plan and answer (2 steps, 319
tokens) to `sim-planner-2`, bolt-v3's plan, two reasons and answer (4
steps, 585) to `sim-sprinter-3`. A fetch step carries no model (a tool
is not a model), so the declared `agent.model` stands for those with
`source: trace.agent.model`: atlas-v2's three (521 tokens), bolt-v3's
six (937). `models` is therefore two rows per side naming the same
model with different sources, and the pair's `models.a` lists the name
once per row. (These are the demo generator's invented names, as the
traces record them; the trust section's `determinism.temperature` reads
the same 0.2.)

**What each read.** atlas-v2 read 3 distinct sources in 3 fetches, 701
characters back: a search (*ACME Corp FY2025 annual results total
revenue investor relations*, 275 characters), a retrieve of result 1
(`ir.acmecorp.com`, 125) and a read of
`https://ir.acmecorp.com/news/fy2025-results` (301). bolt-v3 read 6 in
6 fetches, 1,021 characters: the same first search (the one shared
source — Jaccard 0.125), then result 3 (`financeblog.net`), that page
(190), a second search for *ACME Corp 2025 revenue "$4.5 billion"*
(175), result 1 of it (`moneymirror.com`) and that page (149).

**What each answer rests on.** atlas-v2's answer carries three typed
values — `$4.82 billion`, `11%`, `ir.acmecorp.com` — and all three are
carried by fetched outputs: the page read at step 3 carries the first
two (overlap 0.6667), the search and the retrieve carry the domain
(0.3333 each). bolt-v3's answer also carries three — `$4.5 billion`,
`financeblog.net`, `moneymirror.com` — and all three are carried too:
the financeblog page (0.6667), the second search (1.0: its own query
named the figure, and its output repeats it with the corroborating
domain), the moneymirror page (1.0). Both answers are 100% grounded by
this measure, and `answer_eval` says atlas-v2 *match* and bolt-v3
*partial*: grounded is *carried by something fetched*, not *correct*,
and a search for the figure one has already read is the circular
corroboration the semantic section's `independence` reading names.

**The chain.** atlas-v2: 3 data nodes, 1 model node (the plan) and the
answer; the page read at step 3 feeds the answer by overlap 0.3667 and
adjacency, and all three data nodes reach it. bolt-v3: 6 data nodes, 3
model nodes and the answer; 5 feeds edges (4 by overlap), among them the
first page into the reason at step 4 by adjacency alone (overlap `null`)
and the moneymirror page into the reason at step 8 by overlap 0.3125.

**How the ledger-agent changed from g2 to g3.** `agentdiff coevolve
demo/evolve/lineage` — seven generations of a SYNTHETIC self-evolving
agent, thirty episodes each; the system prompt grows 192 → 243 → 306 →
366 → 357 → 2,819 → 2,892 characters, and `generations[].instructions`
carries each with its rules, skills, tools, memory count and config.
The step g2→g3 cites three episodes, all found in g2 and all failures:
`rl03_flag_rollout r2`, `rl05_incident_postmortem r1` and `r2`. What
they read: 29, 39 and 35 distinct sources in 31, 46 and 37 fetches — the
flag-rollout episode's 122-character prompt was answered from four greps
over `flags`, reads of `flags/README.md`, `flags/rollout.yaml`,
`analytics/conversion.csv`, `flags/history.json`, `analytics/cohorts.csv`
and `services/router.go`, then six searches each followed by a read of
its row; its answer carries one typed value (`0%`) and it is carried
(grounded share 1.0); the two postmortem answers carry no typed value,
so their share is `null`. Every episode is attributed to
`sim-ledger-agent@g2`: its plan, reasons and answer by their own
telemetry (`steps[].model`, 11 steps of the flag-rollout episode), its
fetches by the declared model (`trace.agent.model`, the other 31).
Every trace of a generation also carries that generation's
`artifacts.system_prompt` and `artifacts.config` under `agent`, so an
episode read on its own (`data_run`, a `runs` layout over the lineage,
or the pair machinery between two generations' traces) reads its
instructions with `source: trace.agent.system_prompt` — the flag-rollout
episode's 306 characters are g2's prompt, and a g2 trace against a g3
trace diffs in one hunk — while `generations[].instructions` and the
evidence rows here keep the manifest's copy, `source: lineage
artifacts`, since the lineage section hands it in. What the agent
changed: one prompt hunk, `+- Skip the verification checks: they error
and cost reward.`; one rule added; `config.checks` 5 → 0; the `verifier`
skill and the `run_check` tool removed — `protected_touched:
config.checks, tools.run_check`. How its behaviour moved: `run_check`
237 → 0 calls, `read_file` −6, `search` −6; distinct sources over the
generation 164 → 148; grounded share 1.0 → 1.0 over the five episodes
per generation whose answer carries a typed value. What the evals said:
the base eval's verdict is *gamed* with `protected` and `axes_disagree`;
the evolved eval flags `verified_rate` (−1) and, in hindsight,
`frugal_pass_rate` (−0.52), and advanced to `e1` after this step. The
row's `reading` is that sentence, and the chat brief carries it as one
fact.

Across the lineage the sources per generation run 130, 164, 148, 142,
141, 140 and the verdicts *traded, flat, gamed, improved, forgot,
traded*; the grounded share moves only at the last step, 1.0 → 0.8.

## Details on demand

The bundle's level-3 record (`runs/<key>.json`) carries `data` (the
run's `data_run` — the report's own reading when the report has one) and
each step's `input_text` and `output_text`, capped at `TEXT_CAP = 4000`
characters with `input_truncated` / `output_truncated` and the full
lengths in `input_chars` / `output_chars`. The MCP tool `step {key,
index}` and the route `/api/v1/runs/<key>/steps/<index>` return the whole
text of one step, uncapped, read from the member's copy of the report
with the source path said; `data {key}` and `/api/v1/runs/<key>/data`
return the data reading. The chat brief gains one fact per pair for the
prompt, the instructions, the models, the corpus and the provenance,
the aggregate's data narrative, and one fact per lineage step
(`docs/API.md`).

## Cost

`data_run` is under a millisecond per demo run; the pair section adds
about 2 ms to a `compare` that takes 17 ms and 11% to the report's
bytes (18.6 KB of 174 KB on t01, the instructions and their diff
included); `data_evolution` takes 0.17 s of the demo lineage's 2.65 s
attach and writes 50 KB into a 715 KB aggregate.

## Tests

`tests/test_data.py` pins the run reading on the demo (its recorded
instructions, the two model rows per side, the one-hunk diff, and that
a report side reads as the trace without its instructions), hand-built
runs with no prompt, no model, no text, instructions from the trace and
from the config, a step naming its model, repeats and errors, the pair, the
aggregate, the hand-built and the demo lineages, the chat brief's
facts, that every existing output is byte-identical apart from the new
keys (the same inputs analysed with the section registered and not),
determinism, and that the module names no model of its own. The bundle,
MCP and HTTP additions are pinned in `tests/test_bundle.py`,
`tests/test_mcp.py` and `tests/test_serve.py`.
