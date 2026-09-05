# AgentDiff — git diff for AI agents

Two agents ran the same task. One succeeded, one did not. AgentDiff
compares the two trajectories step by step, names the decisive step
with the evidence for it, reads each run on its own (what it did, what
its answer rests on, what to take forward), and closes the loop: run
any model or agent, compare, replay the decisive step to verify it,
narrate through a provider that may never alter a number.

Pure Python 3.10+, no dependencies, deterministic. The analysis engine
contains no network code; only the harness talks to a model.

## Quick start

```bash
pip install -e .          # installs `agentdiff` (python -m deepcompare works too)
agentdiff demo --open     # compares the 8 shipped pairs, writes out_demo/report.html,
                          # prints the flagship pair's verdict card
agentdiff explain demo/traces/t05_flight_duration__bolt-v3.json --html run.html
```

The card the demo prints, every line quoting a section of the report:

```
VERDICT  atlas-v2 solved t05_flight_duration; bolt-v3 failed.
CAUSE    step 1 of bolt-v3 (tool_selection, hypothesized): Divergence: used a plain
         calculator on local clock times. SGT/BST/EDT offsets are ignored...
COST     bolt-v3 spent 235 tokens, 3.4s and 1 step less than atlas-v2 — faster to nothing
FIX      bolt-v3 — at step 1: check what the observation was asked before trusting
         what it returned — the answer relayed it faithfully and was still wrong
CONF     medium — single pair (n=1); … the decisive step is hypothesized, not replay-verified
```

The report page (`web/blocks.html`, one self-contained file) opens with
that card, then a trajectory map — two lanes side by side around a
labelled gutter, every step a node with its content, keyboard
navigable — then the reading of each run, the adjudicated diagnosis
with its evidence ledger, cost, and the evidence-quality blocks.

## The loop, end to end

```bash
# 1. Run: any provider, any agent of your own, N runs each
agentdiff run --tasks tasks.json -o traces/ --runs 3 \
    --provider atlas=openai:gpt-4o --provider local=ollama:llama3.1 \
    --agent mine=python:my_pkg.agent:solve        # (task, tools) -> trace or messages
#   --agent mine=cmd:"./my_agent --task {prompt_file} --out {out_file}" also works;
#   --base-url / --temperature / --api-key-env reach the provider; keys from env only

# 2. Compare: stability and pass^k across runs, or the pairwise diff with diagnosis
agentdiff runs traces/ -o out/
agentdiff batch traces/ -o out/

# 3. Replay: a decisive step is a hypothesis until re-execution flips the outcome
agentdiff replay out/report_t01.json --provider atlas=openai:gpt-4o --replays 3
#   -> replay-verified / replay-refuted / replay-mixed, written into the report;
#      the map's ring goes solid, the card's CONF line says so

# 4. Why: narrate through any provider, checked number by number
agentdiff why out/report_t01.json --provider atlas=openai:gpt-4o
```

Scripted providers (`scripted:turns.json`) make every step of this loop
testable offline, and the tests do exactly that.

## Bring your own traces

- **Record as it happens**: `deepcompare.record.Recorder` writes a
  SCHEMA trace with measured tokens, latency and declared terminations.
- **Convert what you have**: `agentdiff convert --format otel|openai
  file -o traces/` (`--dry-run` says what it would recover and what it
  would have to estimate).
- **Run an existing agent through the harness**: `agentdiff run --agent
  …` — the harness grades, declares the termination, names the file.

## What it does

| question | command / block | what you get |
|---|---|---|
| who won, why, at what cost, what next, how sure | `compare`, the verdict card | five lines, each quoting the report |
| which step was decisive, and on what evidence | `diagnosis` | competing hypotheses ranked with an evidence ledger, class mix (observable / annotation / stated), a causal window, contested when nothing leads, possibly-overdetermined when two wrong values each suffice |
| what one run did, what its answer rests on | `explain`, the Reading block | phases, per-step roles, every answer value with its basis, why it ended, findings by evidence class, located next actions, meltdown onset |
| what to hand to the next run, the reward, the training loop | `feedback`, the Next horizon section | prompt suggestions per finding (with the replay that tests each), reward-shaping events from per-step labels, a preference pair (chosen = passing run or reconciled splice, rejected = failing run) as JSON/JSONL |
| both runs over time, at a glance | the Story view's hero | a super panel (outcome, decisive step, first divergence, five paired stats) over the body chart: each run a trunk along wall-clock time, thinking on the trunk, tool calls as branches, alignment in the gutter, the fault's path in red, zoomable |
| the story, as charts | the Story view | D3 charts in sequence: what happened (roles, answer-value arcs, the decisive ring, spend after the basis), how it became a failure (fault enters, carried, committed, read from the causal account and the alignment), the trace as a tree (task → runs → phases → steps → values, the fault's path in red, phases that fold), why (hypotheses at their scores with evidence), reconcile (the splice that keeps the failing prefix, takes the passing decision at the cut, follows the passing run; its estimate called an estimate), take forward (numbered pins per next action, what the fix buys, labelled an estimate); long runs draw a window of steps over an overview brush, and page together |
| is a failure real or luck | `runs` | pass^k with intervals, consistency, paired inference that refuses to rank below ten tasks |
| what to fix first, and did the fix work | triage, `progress` | ranked actions with verification contracts, before/after matching |
| whether the number can be trusted | `bench --strict` | the diagnoser's own benchmark with a leakage probe: the margin over a surface-cue detector is the headline |
| N agents, selection, CI | `fleet`, `select`, `gate`, `experiments`, `variance` | rankings, interchangeability, a regression gate, variance attribution |

Every finding cites the step and field it rests on; abstention is an
answer ("contested", "not estimable", "n=1; not a gain estimate");
estimates never masquerade as measurements. See
[`SCHEMA.md`](SCHEMA.md) for the trace and report contract and
[`docs/CHANGELOG.md`](docs/CHANGELOG.md) for every section's origin.

## Is the diagnoser any good? Measured, with its floors

| corpus (2026-09-03) | condition | cause kind | decisive step exact | probe margin kind / step |
|---|---|---|---|---|
| handcrafted, 20 pairs | annotated | 20/20 | 16/16 | +0.40 / +0.19 |
| generated, 2,200 pairs, 18 families | annotated | 0.880 (1936/2200) | 1653/1832 | +0.32 / +0.07 |
| generated, 2,200 pairs, 18 families | stripped of annotations | 0.840 (1847/2200) | 1361/1832 | +0.26 / +0.10 |
| Who&When, 184 public failing logs, single-trace reading | external | — | 8/184 exact, 46/184 within ±1; floor 20/184 | — |

The probe is a deliberately dumb surface-cue detector scored on every
corpus; the engine's margin over it, not its score, is the claim. The
last row is a **negative result**, reported as such: without a passing
twin the pairwise diagnoser cannot run, and the single-trace reading
does not beat the always-first-step floor on those prose-only logs.
Every number, the full history of how the corpus was built and what it
caught, and the external-validation method are in
[`docs/BENCHMARK.md`](docs/BENCHMARK.md). All generated corpora are
synthetic: they prove the machinery against known ground truth, they do
not claim field accuracy.

## Repository layout

```
deepcompare/            the engine (no network code) — diagnosis, reasoning, verdict, statistics, …
deepcompare/harness/    the ONE networked package: providers, tool-loop agent, external agents, replay
deepcompare/commands/   the CLI commands that may talk to a network (run, replay, why)
web/blocks/             the report page, one block per file; build with web/build_blocks.py
demo/                   shipped traces (scripted agents), the diagnosis benchmark, the Who&When converter
docs/                   RESEARCH_INSIGHTS.md, BENCHMARK.md, CHANGELOG.md, CITATIONS.md
tests/                  1,160+ tests, including browser tests of the page and offline harness tests
```

## Watch it run

```bash
python -m deepcompare watch --demo demo/traces --pace 0.4 --loop   # then open http://127.0.0.1:8765/
```

Record with `Recorder(..., stream=True)` (or run the harness with it) and
point `watch` at the trace directory: each agent's steps arrive on the
page as they happen, the newest pulsing; when a pair finishes, the story
replaces the stream in place. A run in progress is shown, never analysed.

`python web/build_live.py` writes `web/live.html`, the deployable demo:
published as a claude.ai artifact, it runs two real Claude agents on the
viewer's account with tools defined in the page and streams every step;
elsewhere it replays the recorded pair.

## Why use it: the loop

Mapping a failure is the first half. The second half is what the page
hands back. Read the story (what happened, why, where the fault
entered); go back with `replay` to test the decisive step against the
passing run's decision; then take three things forward. *Prompt
suggestions* — one sentence per finding the reading located, in the
next run's system prompt. *Reward shaping* — the step labels (fault
enters, carried, wrong answer, dead end, spent after basis, fed the
answer) as a process signal for an RL environment. A *preference pair* —
the passing run, or the reconciled splice, against the failing one, in
the shape a preference-optimisation loader reads. `deepcompare feedback
out/ --jsonl pairs.jsonl` writes them for a whole batch; every item is
derived from the report and labelled a hypothesis until a replay confirms
it.

## Research direction

AgentDiff implements a ranked program distilled from the interpretability
and agent-evaluation literature — counterfactual decisive steps
(Who&When, AgenTracer), evidence classes over stated reasoning (CoT
faithfulness), overdetermination (Thought Anchors), pass^k reliability
(τ-bench), leaky implanted benchmarks, paired designs — with each item's
status in [`docs/RESEARCH_INSIGHTS.md`](docs/RESEARCH_INSIGHTS.md).
With an open-weights model the harness also records what the model's
own tokens say (a per-step confidence interval from its logprobs) and
what its internals say (the Neuronpedia SAE features each step
activates, with `NEURONPEDIA_API_KEY` set outside the chat); the report
then names the features that fired only on the failing side at the
decisive step, as cited evidence that never moves a score, and the page
draws the band, the whiskers and the feature bars with their dashboard
links. Still open: MAST cross-mapping, strained coherence, structural anchors,
a same-configuration noise floor, and turning single failing logs into
pairs by recording a passing run through the harness.
