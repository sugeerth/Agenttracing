# AgentDiff

**Git diff for AI agents.**

Two agents hit the same success rate on your benchmark. Are they the same agent?
Almost never. One burns 61% more tokens re-searching after picking a bad source;
the other fails only in tool execution. Aggregate metrics hide this.
AgentDiff makes it visible — and attributable.

Give it two trajectories on the same task — Agent v1 vs v2, Claude vs GPT,
architecture A vs B — and it tells you not just *that* they differ, but **where
they first diverged, why, and what it cost**:

> **Divergence #1 (retrieval)** — Agent B selected a lower-quality source
> ("blog post") where Agent A used the annual report.
> Downstream impact: +4 steps, +1,210 tokens, +23s latency. **Caused final failure.**

## What it does

- **Trajectory alignment** — global sequence alignment (Needleman–Wunsch over
  step similarity) lines up two runs step by step, finding the shared prefix,
  drifted steps, and each side's extra work.
- **Divergence detection** — locates the *first meaningful divergence* and every
  subsequent one, classified as `retrieval`, `tool_selection`, `planning`,
  `reasoning`, or `stopping`.
- **Failure attribution** — when one agent fails, walks the causal chain from
  the root divergent step through its propagation to the wrong answer:
  *"Agent B diverged at step 2 because it selected the wrong retrieval result;
  that propagated into the tool call at step 5 and caused the final failure."*
- **Efficiency deltas** — tokens, cost, latency, step counts, tool calls,
  searches — per task and aggregated.
- **Regression detection** — flags trades like *"accuracy +2.1% but unnecessary
  tool calls +14%"* across a batch.
- **Interactive compare view** — side-by-side timelines with aligned steps,
  highlighted divergences, and a clickable causal chain. One self-contained
  HTML file, no server.

Beyond a single pair:

- **Which failures are real** — run the same agents several times and
  AgentDiff separates reproducible behavior from noise: *stable-fail* (fix it),
  *flaky* (it only fails sometimes), *systematic divergence* (the gap is
  behavioral, not luck).
- **Which agents are the same agent** — behavioral similarity on four separate
  facets (outcomes, trajectory shape, tool mix, resource spend), because
  "similar" means different things: matching outcomes with a cost gap means one
  agent is **redundant**; differing outcomes mean they are **complementary**.
- **Which agent to actually use** — best single agent, the smallest portfolio
  that covers the task set, and the per-task cheapest solver, with the oracle
  ceiling reported as headroom rather than sold as an achievable policy.

## Quick start

Nothing to install locally? Run it on Colab —
[**notebooks/AgentDiff_Colab.ipynb**](notebooks/AgentDiff_Colab.ipynb)
([open in Colab](https://colab.research.google.com/github/sugeerth/Agenttracing/blob/claude/deepcompare-ai-agents-9vyj2n/notebooks/AgentDiff_Colab.ipynb))
runs the whole pipeline with every report rendered inline, and — on a free T4
— runs a **real open-weight model**, captures its per-token logprobs, and
compares two of its runs against each other.

```bash
# 1. Generate the demo traces (two scripted agents, 8 tasks, 16 trajectories)
python demo/generate.py

# 2. Compare one pair
python -m deepcompare compare \
    demo/traces/<task>__agent-a.json demo/traces/<task>__agent-b.json

# 3. Run the full batch → per-task reports, aggregate stats, interactive report
python -m deepcompare batch demo/traces/ -o out/
open out/report.html
```

Then, with more agents or more runs:

```bash
# Rank a fleet of agents (composite score, Pareto frontier, failure fingerprints)
python -m deepcompare fleet demo/fleet/traces/ -o out_fleet/

# Which agents are interchangeable, which are redundant, which to use
python -m deepcompare select demo/fleet/traces/ -o out_select/

# Is this failure real, or did we get unlucky? (3 runs per agent per task)
python -m deepcompare runs demo/runs/traces/ -o out_runs/

# Block a regression in CI: exits non-zero when the candidate is worse
python -m deepcompare gate baseline_traces/ candidate_traces/ --markdown gate.md
```

No dependencies — Python 3.10+ stdlib only.

## The composable view

`web/blocks.html` is the same analysis as a board of blocks you arrange:
drag them between columns, collapse what you don't need, remove what you
never read, add it back from the block drawer.

```bash
python web/build_blocks.py                       # assemble from web/blocks/*.js
python -m deepcompare batch demo/telemetry/traces -o out --template web/blocks.html
```

Two things order the board. **Relevance** is what the loaded run has to say —
a failure-attribution block has nothing to contribute when nothing failed, so
it says so and steps aside. **Interest** is which blocks you actually open,
decayed by half every fortnight so last month's habits stop outranking this
week's work. Reordering is *offered*, never applied behind your back: a
layout you arranged by hand is better evidence than anything inferred from
your clicks.

Your layout follows a visitor id — a random UUID minted in your browser on
first open, kept in a first-party cookie with a localStorage mirror (cookies
are refused for `file://` pages, and the **You** panel names whichever
backend is actually live rather than failing quietly). Nothing is
transmitted: the page contains no network code at all, which is why it opens
offline from a file. The You panel shows the id, everything stored under it,
and erases the lot in one click.

Adding a block is one file in `web/blocks/` — see
[`web/blocks/README.md`](web/blocks/README.md) for the contract.

## Bring your own agents

Log each run as a JSON trajectory per [SCHEMA.md](SCHEMA.md): agent + task
metadata, an ordered list of steps (`plan | search | retrieve | read |
tool_call | reason | answer`), and outcome/totals. Name files
`<task_id>__<agent_name>.json` (add `__<run_id>` for multi-run analysis),
point a command at the directory, done.

Already have traces in another format? Convert them:

```bash
python -m deepcompare convert --format otel   spans.json    -o traces/
python -m deepcompare convert --format openai messages.json -o traces/
python -m deepcompare convert transcript.json --dry-run   # what would it recover?
```

The OpenTelemetry adapter reads GenAI-convention spans (both the plain and
OTLP wire forms, camelCase or snake_case); the OpenAI adapter reads a
chat-completions message array with tool calls; the Ollama adapter reads the
turn transcripts self-hosted runners emit, keeping their real token counts,
nanosecond durations and per-token logprobs. Traces converted from different
stacks compare against each other directly.

`--dry-run` converts nothing and reports what the mapping *recovered* — steps
with text, timing, tokens and observations — because a conversion that
silently produces empty steps looks like success and poisons every analysis
downstream.

## Repository layout

```
deepcompare/   engine: schema, alignment, divergence, attribution, semantics,
               counterfactuals, fleet ranking, similarity, routing, gate, CLI
demo/          two scripted agents (8 tasks), a 33-agent fleet, multi-run traces
web/           viewer.html — the full interactive compare page
               select.html — a small Tufte-style agent-selection view
               blocks.html — the composable view, built from web/blocks/*.js
notebooks/     AgentDiff_Colab.ipynb — the whole pipeline on Colab, plus a
               real open-weight model run on GPU with genuine logprobs
tests/         unit tests (python -m unittest discover tests)
SCHEMA.md      the trace + report JSON contract
docs/          landscape survey and tutorial
```

## Why this is a research direction, not just a dashboard

Agent evaluation today is dominated by outcome metrics; recent work is moving
toward trajectory-level diagnosis. The open problem AgentDiff targets:

> When two agents achieve similar task-level results, how can we automatically
> **discover, explain, and attribute** the differences in their behavior?

The contribution is comparative: alignment across *pairs* of trajectories,
divergence as the unit of analysis, and causal attribution from divergence to
outcome — the layer that existing eval frameworks (outcome scoring, single-run
trace inspection) don't provide.
