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
agentdiff demo --everything -o out_all   # the whole demo: pairs, training runs, two self-evolving
                          # lineages with their evals, one bundle with every trace, the key,
                          # and the MCP snippet for a coding assistant (ten views in one page)
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
that card, then a trajectory map — two lanes around a labelled gutter,
every step a node with its content, keyboard navigable — then the reading
of each run, the diagnosis with its evidence ledger, cost, and the evidence-quality blocks.

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

# 5. Or let it run itself: baseline → reading → prompt hypothesis, tested as a
#    paired experiment and kept only on the evidence → runs where the pick is
#    unclear → a ledger with every decision and why it stopped (docs/AGENTIC.md)
agentdiff loop --tasks tasks.json --provider atlas=openai:gpt-4o \
    --provider local=ollama:llama3.1 --runs 3 --iterations 4 -o loop/
# a scripted provider (scripted:turns.json) makes every step above testable offline; the tests do
```

## Bring your own traces

- **Record as it happens**: `deepcompare.record.Recorder` writes a SCHEMA trace.
- **Convert what you have**: `agentdiff convert --format otel|openai file -o traces/` (`--dry-run` says what it would recover and what it would estimate).
- **Run an existing agent through the harness**: `agentdiff run --agent …`.

## What it does

| question | command / block | what you get |
|---|---|---|
| who won, why, at what cost, what next, how sure | `compare`, the verdict card | five lines, each quoting the report |
| which step was decisive, and on what evidence | `diagnosis` | competing hypotheses ranked with an evidence ledger, class mix (observable / annotation / stated), a causal window, contested when nothing leads, possibly-overdetermined when two wrong values each suffice |
| what one run did, what its answer rests on | `explain`, the Reading block | phases, per-step roles, every answer value with its basis, why it ended, findings by evidence class, located next actions, meltdown onset |
| what to hand to the next run, the reward, the training loop | `feedback`, `rl`, the Next horizon section | prompt suggestions per finding (with the replay that tests each), reward-shaping events from per-step labels, a preference pair (chosen = passing run or reconciled splice, rejected = failing run) as JSON/JSONL; each run as an episode — reward per step (recorded, else shaped from the labels and said so), return and return-to-go, credit from the Shapley split, reward-weighted clusters on the pair's scale, per-policy mean return with an interval over runs ([docs/RL.md](docs/RL.md)) |
| both runs over time, at a glance | the Story view's hero | a super panel (outcome, decisive step, first divergence, five paired stats) over the body chart: each run a trunk along wall-clock time, thinking on the trunk, tool calls as branches, alignment in the gutter, the fault's path in red, zoomable |
| the story, as charts | the Story view | D3 charts in sequence: what happened (roles, answer-value arcs, the decisive ring, spend after the basis), how it became a failure (fault enters, carried, committed, read from the causal account and the alignment), the trace as a tree (task → runs → phases → steps → values, the fault's path in red, phases that fold), why (hypotheses at their scores with evidence), reconcile (the splice that keeps the failing prefix, takes the passing decision at the cut, follows the passing run; its estimate called an estimate), take forward (numbered pins per next action, what the fix buys, labelled an estimate); long runs draw a window of steps over an overview brush, and page together |
| is a failure real or luck; is one policy better; is a self-evolving agent getting better; is its eval still measuring | `runs`, the Training view, `evolve`, the Evolution view, `coevolve`, the Evals view | pass^k with intervals, consistency, paired inference that refuses to rank below ten tasks; over a batch of episodes the interquartile mean with a task-stratified bootstrap, the performance profile, the probability of improvement per task, a reward audit and a behaviour space; over a lineage of generations, per step: what changed (a diff of the agent's own prompt, rules, skills, memory, config), whether it helped (an interval, per task), and the ways evolution goes wrong — gamed, forgot, overfit, a protected path touched, a prompt over budget or collapsed — with the generation to keep, which is not always the last; and an eval that evolves with the lineage — candidate metrics proposed, validated, adopted or rejected with reasons, the lineage re-read with hindsight; and the harness beside the agent — which artifacts are reasoning and which are the scaffold it runs inside, whether the thing that ran two generations was the same thing, and whether a rising pass rate came from the agent needing less or from more being put around it; every dimension of what *ran* a generation read from its episodes rather than its manifest, a dimension nothing recorded drawn as unknown rather than as held, and a delta called `attributable` only where a fingerprint on both sides did not move — `confounded` where the harness moved too, `assumed` where the record cannot settle it, since a renamed model and a changed one are the same bytes in a trace; and, over the engine's whole nineteen-category recommendation vocabulary, which findings this harness has a knob for and which it does not, derived from the actuator's own rules so the map cannot drift from them ([docs/RL.md](docs/RL.md), [docs/EVOLVE.md](docs/EVOLVE.md), [docs/COEVOLVE.md](docs/COEVOLVE.md), [docs/HARNESS.md](docs/HARNESS.md)) |
| how the agents score, on every dimension | `eval`, the Evaluation scorecard | accuracy, correct tool, retrieval quality, grounding, policy, risk flags, stopping, loops, recovery (each with a Wilson interval), spend and wasted time per run, risk vs reward, a judge beside the grade; offline against a golden set or online as recorded ([`docs/EVAL.md`](docs/EVAL.md), [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md)) |
| why a run took as long as it did, and where it mattered | the Where the time went section, the Where it mattered panel | each second attributed to thinking, a named tool, or the answer; wasted steps hatched and named; a rationale with every number in its table; a focus-and-context timeline whose width is impact, quiet stretches folded, details on demand; in the Panels view, two treemaps on one scale (area = seconds), the calls × time heat map, latency by tool and the tool matrix, in a grid you compose and the browser remembers |
| a long run, and its sub-agents | the Subdivisions and sub-agents section | the run folded into spans (sub-agents, nested via `step.span`), subdivisions and steps as a time-weighted tree; zoom into any node; a ledger per sub-agent ; and whether the evaluation can *see* a long run's failure — sixteen long tasks, twelve named long-horizon failure modes, four controls, the catch matrix and the one mode nothing catches ([`docs/HORIZON.md`](docs/HORIZON.md)) |
| what to fix first, and did the fix work | triage, `progress` | ranked actions with verification contracts, before/after matching |
| whether the number can be trusted | `bench --strict` | the diagnoser's own benchmark with a leakage probe: the margin over a surface-cue detector is the headline |
| N agents, selection, CI, dashboards | `fleet`, `select`, `gate`, `experiments`, `variance`, `grafana` | rankings, interchangeability, a regression gate, variance attribution; every number as Prometheus samples with intervals as separate series, and six provisioned Grafana dashboards under `grafana/` ([docs/GRAFANA.md](docs/GRAFANA.md)) |
| what is running, what each run did, where every token and fetch went — in a product, a chat, a coding assistant | `bundle`, `key`, `mcp`, `serve`, the Levels view | one content-addressed directory with three levels of grain, a paste-safe key that carries the overview and names the bundle by its hash, an MCP server and a local HTTP API over the same levels, with `step` and `data` tools and routes for any step's full text and what each agent was told, read and rested its answer on ([docs/API.md](docs/API.md), [docs/DATA.md](docs/DATA.md)) |
| does the recording reproduce; what does the next model do in it; what did the model see; how far did a long run get | `rerun` (`--span`, `--from/--until`, `--provider`, `--golden`), `checkpoint`, `context`, the Milestones ladder | hermetic replay with the world served from a cassette, by segment or sub-agent, the first miss named, a drift map over the sub-agents, milestones reached before the answer; the context before any step ([docs/REPLAY.md](docs/REPLAY.md)) |

Every finding cites the step and field it rests on; abstention is an
answer ("contested", "not estimable", "n=1; not a gain estimate");
estimates never masquerade as measurements. [`SCHEMA.md`](SCHEMA.md) is
the trace and report contract, [`docs/CHANGELOG.md`](docs/CHANGELOG.md) every section's origin.

## The eval that evolves with the agent

An agent that evolves against a fixed eval eventually optimises the eval.
`agentdiff coevolve <lineage>` reads the lineage with an eval that is itself
a lineage, e0 → e1 → …: at every agent step, probes — one question each —
propose candidate metrics in a small language over per-episode features,
five validators test each on the evidence so far at a Bonferroni-adjusted
level, and the final eval re-reads every step with hindsight beside the base
verdict. Every candidate is a ledger row with its numbers and its reason; on
the shipped lineage 20 were tested and 3 adopted, the first a verification
rate learned at the gamed step. A model may propose through the harness and
can set no number, verdict or exit code; the gap is written into the output:
a fooled grader fools every metric here ([docs/COEVOLVE.md](docs/COEVOLVE.md)).

## Is the diagnoser any good? Measured, with its floors

| corpus (2026-09-03) | condition | cause kind | decisive step exact | probe margin kind / step |
|---|---|---|---|---|
| handcrafted, 20 pairs | annotated | 20/20 | 16/16 | +0.40 / +0.19 |
| generated, 2,200 pairs, 18 families | annotated | 0.880 (1936/2200) | 1653/1832 | +0.32 / +0.07 |
| generated, 2,200 pairs, 18 families | stripped of annotations | 0.840 (1847/2200) | 1361/1832 | +0.26 / +0.10 |
| Who&When, 184 public failing logs, single-trace reading | external | — | 8/184 exact, 46/184 within ±1; floor 20/184 | — |

The probe is a deliberately dumb surface-cue detector scored on every corpus;
the engine's margin over it, not its score, is the claim. The
last row is a **negative result**, reported as such: without a passing
twin the pairwise diagnoser cannot run, and the single-trace reading
does not beat the always-first-step floor on those prose-only logs.
Every number, the full history of how the corpus was built and what it
caught, and the external-validation method are in
[`docs/BENCHMARK.md`](docs/BENCHMARK.md). All generated corpora are synthetic:
they prove the machinery against known ground truth, they do not claim field accuracy.

## Repository layout

```
deepcompare/            the engine (no network code) — diagnosis, reasoning, verdict, statistics, …
deepcompare/harness/    the ONE networked package: providers, tool-loop agent, cassette + hermetic rerun, replay; commands/ holds their CLI
web/blocks/             the report page, one block per file; build with web/build_blocks.py
demo/                   shipped traces (scripted agents), the diagnosis benchmark, the Who&When converter
docs/                   HOW_IT_WORKS (the eleven views), PRODUCTION (deploying it), TRACING (what a trace should record), ARCHITECTURE, API, DATA, COEVOLVE, EVOLVE, RL, …
tests/                  2,116 engine tests and 337 browser tests of the page, all offline
```
## Watch it run

```bash
python -m deepcompare watch --demo demo/traces --pace 0.4 --loop   # then open http://127.0.0.1:8765/
```

Record with `Recorder(..., stream=True)` (or run the harness with it) and
point `watch` at the trace directory: each agent's steps arrive on the
page as they happen, the newest pulsing; when a pair finishes, the story
replaces the stream in place. A run in progress is shown, never analysed.
`python web/build_live.py` writes `web/live.html`, the deployable demo: as
a claude.ai artifact it runs two real Claude agents on the viewer's account
with tools defined in the page and streams every step; elsewhere it replays the recorded pair.
## Trace Claude Code, and route

Add two hooks to `.claude/settings.json` and every tool call streams into a
trace while Claude Code works; the final trace comes from the session transcript, with the reasoning between calls:

```json
{"hooks": {"PostToolUse": [{"matcher": "", "hooks": [{"type": "command",
   "command": "python -m deepcompare hook --traces traces --task fix-482 --db traces.sqlite"}]}],
 "Stop": [{"hooks": [{"type": "command",
   "command": "python -m deepcompare hook --traces traces --task fix-482 --db traces.sqlite --expected 'field_validator'"}]}]}}
```

Run another coding agent on the same task through the harness (`run --agent
cmd:…`) or convert its log, then `batch` the pair. Over many runs, `route
traces/` (or `--db traces.sqlite`) gives a router what it needs per task
family: each agent's success rate with its interval, cost, latency, steps,
tool calls, and a pick whose confidence is stated — *clear*, *either*, or
*gather more runs* — with its rationale: the interval against the runner-up,
cost, speed, equality, fault kinds, and what would settle an open call. `db
import` keeps every trace in one SQLite file with full-text search over steps
and a checkpoint per step for running agents. `runs` adds output equality: do
repeated runs say the same thing, and do the two agents. `judge` lets a second
model grade answers no exact match can — beside the grade, scored against failures you know of.
## Why use it: the loop

Mapping a failure is the first half. The second half is what the page
hands back. Read the story; go back with `replay` to test the decisive
step against the passing run's decision; then take three things forward.
*Prompt suggestions* — one sentence per finding, in the next run's system
prompt. *Reward shaping* — the step labels (fault enters, carried, wrong
answer, dead end, spent after basis, fed the answer) as a process signal
for an RL environment. A *preference pair* — the passing run, or the
reconciled splice, against the failing one, in the shape a
preference-optimisation loader reads. `deepcompare feedback out/ --jsonl
pairs.jsonl` writes them for a whole batch; every item is labelled a hypothesis until a replay confirms it.
## Research direction

A ranked program distilled from the interpretability and agent-evaluation
literature — counterfactual decisive steps, evidence classes over stated
reasoning, overdetermination, pass^k, leaky benchmarks, paired designs — each
item's status in [`docs/RESEARCH_INSIGHTS.md`](docs/RESEARCH_INSIGHTS.md). With an open-weights model the
harness also records per-step logprob intervals and Neuronpedia SAE features, cited as evidence that never moves a score.
