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

## Whether the score is telling the truth

Outcome-only evaluation is blind by construction. A run can satisfy its
oracle while looping, ignoring an error, or writing something nobody asked
for — and a run can fail with a spotless process because the *oracle* is
wrong. AgentDiff gives those two cases their own verdicts:

> **passed but pathological** — hasty-v2 passed, but it hammered a failing
> lookup three times, never recovered, and then wrote without ever having
> read the booking. A leaderboard scores this identically to a clean pass.
>
> **failed but clean** — steady-v1 failed, yet nothing in its process went
> visibly wrong. That is evidence about the grader, not only the agent.

All of it is computed from the logged trace: **no judge, no re-execution, no
model call.** That restriction is the point rather than a limitation. Replay
a *frozen* step under different evaluator channels and the sign of its score
flips, with cross-channel disagreement exceeding same-channel retry
disagreement by 48 points ([arXiv 2607.04419](https://arxiv.org/abs/2607.04419));
for false success specifically, no judge across five models and five
prompting strategies exceeded AUROC 0.65
([arXiv 2606.09863](https://arxiv.org/abs/2606.09863)), while a state
comparison settles it outright. What can be settled by counting is settled
by counting, and the rest is left to a human with the evidence laid out.

- **Reliability, not one lucky run** — the pass^k curve
  (`C(c,k)/C(n,k)`, τ-bench's measure) beside pass@k, so "can it ever" and
  "can it every time" are on the same line. Harness failures are excluded
  *before* any statistic, because counting a rate-limited run as an agent
  failure makes the agent look worse and the harness look fine. With 3 runs
  per task the report says plainly that nothing here supports ranking two
  agents.
- **Citable failure labels** — the divergence kinds and process flags map
  onto the published MAST and TRAIL taxonomies, including the four cells
  where the mapping honestly resolves to *nothing*: MAST has no
  system-execution category, TRAIL has no verification branch. Coverage is
  reported as the headline — **5 of 14 MAST modes reachable, 39.3% by
  published failure mass** — because a tool should say what it cannot see.
- **The industry's tool-call vocabulary** — `strict`/`unordered`/`subset`/
  `superset` matching, F1, weighted-LCS partial credit and permission
  checks, with **every result naming its algorithm**: four widely-used
  libraries ship "tool call accuracy" and compute four different numbers
  from the same trace.

## What to fix first

Depth without prioritisation is not usefulness. Every `batch` now ends with a
ranked list of actions, merged across the analyses that would otherwise
report the same problem three times:

```
1. [failure · medium] Make hasty-v2 pick the tool that fits the data it
   actually has, before it calls anything
   evidence: 2/4 task(s); 2 occurrence(s); 2 failure(s) caused
   impact: up to 1 failure(s) of 4 task(s) avoided; −280 tokens; −8.4s
   effort: prompt (a prompt rule, or a clearer tool description; heuristic)
   why here: x1 — no damping: it recurs, so it is not an anecdote

2. [passing pathology · medium] Break hasty-v2 out of its loop
   note: seen on run(s) that PASSED — no other block flags this
```

Three properties do the work:

- **A pathology on a *passing* run ranks near the top**, because nothing
  else in the pipeline will ever raise it — the gate is green and the loop
  ships.
- **A single occurrence is damped, not dropped** (×0.6, capped at low
  confidence, with a Wilson interval on the rate). It says why: a one-off
  cannot be told apart from a systematic problem, but a one-off crash is
  still a crash.
- **It refuses to rank confidently when the sample cannot support it.** With
  3 runs per task, every *cross-agent* claim is halved and capped, quoting
  the reliability advisory verbatim. Process pathologies are explicitly
  exempt and say so — a loop is visible in one trace and needs no
  comparison.

Impact is never invented: unestimable is `None` with the reason, and a
measured zero is labelled separately from "not estimable". Findings that
cannot be acted on appear in a `not_actionable` list, each with why —
usually an interval that includes zero.

### And how you'll know the fix worked

Every action carries a **verification contract**, which turns a suggestion
into a testable hypothesis. Fingerprints first — issue fingerprints are
stable across runs, so "this stops appearing in the next batch" is a
binary, deterministic confirmation. The success rate second, with its
detectability computed rather than assumed: the check is the binomial tail
under the unchanged-agent null, because a 3-of-4 agent reaches 4-of-4
**32% of the time by pure luck**, and an interval comparison would happily
call that a confirmed fix.

Then close the loop:

```bash
agentdiff batch traces/ -o out_before        # fix things
agentdiff batch traces/ -o out_after
agentdiff progress out_before out_after
#   [+] RESOLVED   (was #7) Make hasty-v2 read before it writes
#   [!] PERSISTS   (was #2) Break hasty-v2 out of its loop — occurrences 1 -> 1
```

`progress` matches every before-action by fingerprint and process flag and
reports resolved / improved / persists / worsened — with absence handled
honestly: a fingerprint whose task did not re-run is **unobservable**, not
cured; fewer occurrences is improvement, not resolution; and new issues the
before-run did not have are part of the answer, not a footnote.

## Diagnosis: competing hypotheses, not one story

Attribution walks the first structural divergence to the answer — one
story, told confidently even when the report itself contradicts it. Every
report now also carries a `diagnosis`: a hypothesis per diagnostic signal
(grader/label, harness kill, environment error, wrong fact, the divergence,
each process flag, budget pressure), each scored against machine-checkable
evidence and each carrying the concrete check that would refute it. A
hypothesis only *leads* when it clears the runner-up by a real margin;
otherwise the diagnosis says **contested** instead of picking a winner. So
a failed run whose answer actually matched the expected answer leads to a
`grader_or_label` diagnosis — the divergence story is still there, demoted
and marked as contradicted, not deleted.

```bash
python3 -m deepcompare compare a.json b.json   # then read `diagnosis` in the JSON
```

Across a batch, the aggregate rolls leading causes up with denominators
(`divergence leads 3 of 4 diagnosed failures`) — a cause that repeats is
one central fix, not N local ones.

### Across repeated runs: consolidation and executed checks

With repeated runs of the same tasks, the diagnosis stops being n=1: every
failing run is diagnosed and the runs mode asks whether the same hypothesis
leads each time, writing `diagnosis_consolidated` into the aggregate. It
also *executes* the discriminating checks that are answerable from the runs
already on disk — grader consistency, environment reproduction, harness
flake rate — because scores can make a hypothesis leading, but only an
executed check can make it confirmed or refuted. Failure reproduction keeps
its denominator throughout: a failure in 2 of 3 runs is reported as a flake
with its denominator, not diagnosed as systematic.

```bash
python3 -m deepcompare runs demo/runs/traces -o out/
```

### The diagnoser is itself benchmarked

Failure attributors that are never evaluated collapse quietly on hard
cases, so AgentDiff measures its own: `demo/diagnosis_bench/` generates
12 trace pairs with one implanted known cause each (grader mislabel,
harness kill, environment fault, wrong fact, blind write, pure
divergence) and `deepcompare/bench.py` scores whether the leading
hypothesis matches the implant — contested never counts as correct.
The benchmark's first run measured 10 of 12 and exposed a structural
blind spot (abandoned tool errors split their score across three
hypotheses); fixing that fusion rule brought the same untouched corpus
to 12 of 12, and the test suite holds the floor at 0.75 so a future
regression in diagnosis quality fails CI rather than shipping.

The corpus then grew to 18 scenarios along the axis the literature says
attributors collapse on: the **decisive error step** — Who&When's
counterfactual criterion, the earliest step whose correction flips the
outcome, where published step-level accuracy peaks at 14–30%. Six hard
scenarios reproduce the documented failure modes: a quiet early cause
whose loud downstream symptom is the trap, visible-but-non-causal
distractor pathologies, and Who&When-Pro-style faults injected after an
exactly-replayed successful prefix. The scorer now reports **kind
accuracy, step localization (exact and ±1), and abstention** — a grader
mislabel or harness kill has no agent step to correct, and naming one
there is a scored miss, not a near-hit. The hard corpus exposed two
more engine bugs before they were fixed (a shared fact both runs read
was being blamed as "the wrong fact"; a 70%-covered answer that flatly
contradicted the expected one still counted as a grader-suspect
"match"). Handcrafted corpus: kind 20/20, step 16/16 exact, abstention
4/4, chain recovery 0.94 recall / 0.93 precision.

The benchmark then went procedural: `demo/diagnosis_bench/
generate_scale.py --pairs N` composes fifteen cause families across
domains, trace lengths and distractors with mechanically derived truth
(seeded, byte-identical per N; `agentdiff bench <dir> --strict` gates on
the shared floors). Four families — `negation_answer`, `wrong_entity`,
`causal_duplicate`, `garbage_args` — come from an independent
adversarial evaluation that built trace pairs to make the engine tell
confident wrong stories; the fixes it forced are exclusivity rules, not
scenario patches, and the attack pairs are pinned as regression
fixtures. At 2,200 generated pairs the measured scorecard is **cause
kind 0.849, decisive step 1576/1760 exact, abstention 0.927, chain
recovery 0.842 recall / 0.963 precision**: the ten original families
and the two corrected adversarial ones sit at 1.0, and every miss
concentrates in the three named open challenges —
`paraphrase_grader` 0.78 (reworded-but-correct answers),
`negation_answer` 0.55 (the failing answer negates the expected one
while reusing its tokens) and `garbage_args` 0.0 (a tool correctly
rejecting an agent-invented argument looks environmental; the engine
honestly contests rather than confirming either story). They stay in
the measured number because a benchmark containing only what the
diagnoser already gets right measures nothing.

The same corpus regenerated with `--strip-annotations` — every step's
`error`/`quality`/`note` nulled, so the engine must infer everything
from observation text — is the de-circularized condition the
adversarial evaluation asked for (the generator writes the very flags
the engine reads): **cause kind 0.795, decisive step 0.703 exact,
chain recovery 0.684 recall**, which fails the annotated-condition
chain floor and is published anyway. The gap between the two scorecards
is the measured value of structured step metadata, not noise. The
scaled sweep is the loop's teacher: it caught an incoherent implant, a
coverage rule that credited wrong-valued answers, an alignment artefact
fixed by the twin rule (a step the other run also took verbatim cannot
be the anchor, EXCEPT a write the failing run performed more times),
shared-flag inversions (a pathology both runs exhibit cannot explain a
one-sided failure), and the paraphrase blind spot that became the
typed-value grader rule. All corpora are synthetic: they prove the
machinery against known ground truth, they do not claim field accuracy.

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

# Is this failure real, or did we get unlucky? Adds pass^k, consistency and
# an advisory that refuses to rank two agents on too few runs
python -m deepcompare runs demo/runs/traces/ -o out_runs/

# Block a regression in CI: exits non-zero when the candidate is worse
python -m deepcompare gate baseline_traces/ candidate_traces/ --markdown gate.md

# Process integrity: the run that PASSES here is the one that looped,
# ignored three errors and wrote blind
python demo/process/generate.py
python -m deepcompare batch demo/process/traces/ -o out_process/
```

No dependencies — Python 3.10+ stdlib only.

## The eval agent, under covenant

`narrate` turns any LLM into an evaluation agent that can never corrupt a
finding. The engine emits a *brief* — numbered facts copied verbatim from
the report — and a prompt; you run any model you like outside the package;
the returned text is checked number-by-number against the brief. A
fabricated figure is stored **flagged**, not trusted and not dropped, and
narration lives under a key nothing reads: deleting it changes no verdict,
no number, no exit code.

```bash
agentdiff narrate out/report_t01.json > prompt.txt        # give this to any LLM
agentdiff narrate out/report_t01.json --ingest answer.txt --model my-llm
#   UNSUPPORTED numbers (not in the evidence): 93%
```

It speaks three shapes: a pairwise report, a whole batch aggregate (the
fleet-level eval agent), and an experiments comparison. The design follows
the field's own evidence: LLM attribution collapses from 94% to 50% as
traces grow (Who&When Pro), while span-grounding narrated claims recovers
~30 points (DRIFT) — see `docs/CITATIONS.md`.

## Experiments, compared as experiments

```bash
agentdiff experiments expA/ expB/ -o out/
```

Diffs of averages with uncertainty first: paired on shared tasks, harness
failures excluded, success as the single primary endpoint and the resource
metrics Benjamini–Hochberg corrected among themselves. Beside every outcome
diff sits a behavioural similarity — cross-experiment versus each
experiment's own within-baseline — because outcome agreement is not
behaviour agreement, in either direction: on the demo corpus an
88%-versus-67% success gap is noise at 8 tasks while the behaviour change
is real (cross 0.75 vs within 0.96), and scores moving *without* behaviour
moving points at the grader.

## What the trace says about where it runs

Every report carries an `efficiency` block: prompt-cache-absorbable resend
overhead, result-cacheable repeated calls (a retry after an error is
excluded — retrying a failure is correct, not waste), independent reads
that could run in parallel (broken conservatively by any provenance link),
latency concentration, and cost per success with its denominator. Savings
are ceilings with their assumptions named. Throughput is refused outright
on estimated token counts: tokens/sec from a `len/4` guess is a made-up
number wearing units.

And every report states the **trade-off** instead of leaving the arithmetic
to the reader: dominance is never dressed up as a dilemma, a fast failure
is "slower to nothing" rather than a saving, and score-per-dollar /
score-per-second exchange rates appear only where quality and spend
actually moved together.

## In CI

`gate` and `check` write the formats CI already reads, so a finding lands in
the test report, the code-scanning view and the pull request rather than in
stdout nobody reads:

```bash
python -m deepcompare gate baseline/ candidate/ -o out/ \
    --junit --sarif --job-summary --github-annotations --fail-on regression
```

| `--fail-on` | fails the build on |
|---|---|
| `never` | nothing — artifacts are still written, the build stays green |
| `regression` (default) | a failed gate criterion or a conformance violation |
| `pathology` | + process pathologies the outcome hid (loop, swallowed error, blind write) |
| `any` | + checks that could not be measured — nothing unproven ships |

Exit `0` clean, `1` a gating finding, `2` a usage or data error. The default
is byte-for-byte the behaviour `gate` had before, so adopting this cannot
turn a build red on its own.

Two choices worth knowing. **SARIF results point at the trace file and carry
no `region` or `startLine`** — there is no source line behind a trajectory
finding, and inventing one aims reviewers at innocent code. **An
unmeasurable check is emitted as `skipped`, never as a pass**: a gate that
goes green because a criterion was disabled has to say which one.
`partialFingerprints` reuse the existing issue fingerprints, so GitHub
tracks a finding across runs instead of re-reporting it, and every emitter
is byte-identical across runs on the same input.

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

## Recording a run as it happens

No logs to convert yet? Instrument the agent you are writing now.
`deepcompare.record` writes a validated trajectory straight out of the run —
stdlib only, nothing to install:

```python
from deepcompare.record import Recorder

with Recorder(task="t01_refund", prompt="Cancel booking QX7T2",
              agent="my-agent", model="claude-sonnet-5",
              tools=TOOLS, budget={"max_steps": 20}) as run:
    run.plan("Read the booking, then cancel")
    call = run.tool("get_booking", {"reference": "QX7T2"})   # records the call
    call.observe(get_booking("QX7T2"))                       # records the result
    run.tool("cancel_booking", {"reference": "QX7T2", "refund": True},
             "cancelled", effect="write")
    run.answer("Cancelled and refunded.", success=True)
# traces/t01_refund__my-agent.json — validated, ready for compare/batch/process
```

`step()` is the low-level form; `plan`, `search`, `retrieve`, `read`, `tool`,
`reason` and `answer` are sugar over it. Per-step latency is taken from the
wall clock, `run_id="r2"` produces `<task>__<agent>__<run>.json` for the
`runs` command, `@run.instrument(effect="write")` records a tool function
every time it is called, and `response=` accepts an OpenAI / Anthropic /
Ollama response object and lifts real token usage and logprob telemetry out of
it (duck-typed — no SDK is imported).

The recorder keeps the honesty properties the analyses depend on:

- **estimated tokens are never written as measured** — counts you pass are
  measurements, `len(text)/4` is an estimate, and each step carries
  `tokens_basis` with a `token_accounting` summary on the trace;
- **termination is declared, not deduced** — `agent_stop` on a clean exit,
  `agent_error`/`timeout`/`user_stop` when the block raises, and `max_steps`,
  `timeout` or the harness reasons only via `run.terminate(...)`, because only
  the harness knows them;
- **an exception inside the block still writes a valid trace**, with a final
  step that says the answer is the recorder's rather than the agent's — the
  failed runs are the ones worth keeping;
- **declared `tools` and `effect`s make the process analysis measurable** —
  without them, schema grounding, permission and blind-write checks report
  *unmeasurable* rather than a pass;
- **nothing invalid reaches disk**: the trace is validated through
  `Trajectory.from_json` and written atomically, and bad arguments raise where
  the mistake was made.

## Repository layout

```
deepcompare/   engine: schema, alignment, divergence, attribution, semantics,
               counterfactuals, fleet ranking, similarity, routing, gate, CLI
               process.py     — loops, recovery, side effects, the outcome-process gap
               reliability.py — pass^k, consistency, ICC, runs advisory
               taxonomy.py    — MAST / TRAIL mapping with its own coverage limits
               toolmatch.py   — reference tool-call matching, every result named
demo/          two scripted agents (8 tasks), a 33-agent fleet, multi-run traces
               process/ — action-taking runs exercising all four gap verdicts
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
