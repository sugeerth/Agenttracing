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

## Quick start

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

No dependencies — Python 3.10+ stdlib only.

## Bring your own agents

Log each run as a JSON trajectory per [SCHEMA.md](SCHEMA.md): agent + task
metadata, an ordered list of steps (`plan | search | retrieve | read |
tool_call | reason | answer`), and outcome/totals. Name files
`<task_id>__<agent_name>.json`, point `batch` at the directory, done.
Adapters for LangChain/OpenTelemetry-style traces are a thin mapping onto this
schema.

## Repository layout

```
deepcompare/   comparison engine: trace schema, alignment, divergence,
               attribution, metrics, report rendering, CLI
demo/          two scripted agent personas + 8 tasks → realistic traces
web/           viewer.html — the interactive side-by-side compare page
tests/         unit tests (python -m unittest discover tests)
SCHEMA.md      the trace + report JSON contract
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
