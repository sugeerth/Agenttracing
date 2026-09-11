# How AgentDiff works — from a step in a trace to a sentence you can check

This is the long explanation: what the system takes in, what it computes,
in what order, what each drawing on the page shows, and why it is built
the way it is. Every section names the module that does the work.

## 1. The stance

Traces of agent runs are everywhere; every observability product captures
them and draws one run's tree. What is thin is the explanation of *why two
runs differ* — and, for a long or delegated run, *where* the time and the
fault went. AgentDiff sits there. Its rules:

- **Deterministic.** Every number is a count, a ratio or an interval over
  the runs listed. No language model is in the control path: not in the
  diagnosis, not in the loop that runs agents, not in a verdict. A model
  may be *under test*, or asked to *judge* an answer, or to *narrate*
  prose that never changes a number.
- **Honest by construction.** An estimate is labelled an estimate; a
  decisive step is a hypothesis until a replay flips the outcome; a
  dimension that cannot be measured reads "not measurable", never a pass
  or a fail; a run with no recorded latencies is *unmeasurable*, not fast;
  synthetic demo data says so.
- **Engine and harness are separate.** The engine (`deepcompare/*.py`)
  has no network code; only `deepcompare/harness/` talks to a model.

## 2. The trace: steps, spans, and streaming

A trace (`SCHEMA.md`) is a task, an agent, an outcome, totals, and an
ordered list of steps: `plan`, `reason`, `search`, `retrieve`, `read`,
`tool_call`, `answer`, each with input, output, tokens, latency, and
optional fields (an error flag, a read/write effect, model telemetry).

Two optional facts turn a flat trace into a tree:

- **`step.span {id, agent, parent}`** — the (sub-)agent acting at this
  step and the span it was delegated from. `Recorder.span("researcher")`
  stamps every step recorded inside a `with`; nested `with`s nest the
  spans. The OpenTelemetry adapter derives the same from nested
  `invoke_agent` spans, so Langfuse, Phoenix, LangSmith or raw OTLP
  exports carry their sub-agents in.
- **Streaming** — a running agent writes the same trace with the steps
  so far (`record(stream=True)`, or the Claude Code hook); the watcher
  serves it to the page as it grows. The span holding the last step is
  *open*.

So a run that streams and a run that delegates are one object: a tree of
spans over time, where streaming adds "still open" and delegation adds
depth. That is why one model (`charts.spanTree` in the page,
`deepcompare/horizon.py` in the engine) feeds both the live view and
the finished analysis.

## 3. What the engine computes for a pair, in order

`deepcompare.report.compare(a, b)` builds the report section by section;
later sections read earlier ones and never change them.

1. **Alignment** (`align.py`) — the two step sequences aligned by type,
   name and input similarity; rows are `match`, `drift`, `a_only`,
   `b_only`, with a tool-argument diff where both called a tool.
2. **Divergences and attribution** (`divergence.py`, `attribution.py`)
   — where the runs first part, ranked; the failing side's chain from
   the root cause to the outcome, with a category (tool selection, tool
   misuse, reasoning, retrieval, planning, stopping).
3. **The reading** (`reasoning.py`) — each run on its own: phases
   (frame, acquire, transform, verify, commit …), every step's role
   (feeds the answer, dead end, no information, repeat, error), what the
   answer *rests on* (each value traced to the observation that produced
   it, or marked unsupported), why it ended, findings by evidence class,
   and the next actions those findings imply.
4. **The diagnosis** (`diagnosis.py`) — competing hypotheses with an
   evidence ledger; the *decisive step*: the earliest step whose
   correction is expected to flip the outcome; a causal window; a replay
   recipe. `replay` re-executes from that step through a provider and
   writes the verdict back (`replay-verified` / `replay-refuted`).
5. **Where the time went** (`timing.py`) — every recorded second to
   thinking, a named tool, or the answer; the seconds in steps the
   reading marks wasted counted and named; the slowest steps; a
   rationale whose every number is in the ledger; the pair narrative
   (who took longer, by how much, what share of the gap is wasted).
6. **The horizon** (`horizon.py`) — the run folded into a tree: spans
   (from `step.span`), *parts* (the reading's phases, split where the
   agent framed or decided, never bridging a child span), steps. Every
   node carries steps, seconds, wasted seconds, tokens, tool calls,
   errors, the fault's path, the decisive step, the values produced.
   Then: the **delegation graph** (every agent once, every delegation
   edge with its count), the **blame** (the decisive step lifted to its
   agent, delegator, depth and part — the "which agent, which step"
   shape of the failure-attribution benchmarks), and the **diff** of the
   two graphs aligned by agent (the two roots as one role): every node
   and edge in both, only A, only B, with counts per side.
7. **The loop back** (`feedback.py`) — step labels for reward shaping, a
   preference pair (passing run, or the reconciled splice, against the
   failing one), and prompt suggestions, one sentence per finding kind,
   each carrying the replay that would test it.

For repeated runs, `suite.analyse_runs` adds stability and pass^k,
paired inference with an exact sign test, output equality
(`equality.py`), the routing table (`router.py`: Wilson lower bound per
task family, with *clear* / *overlapping* / *insufficient*), and the
scorecard (§6).

## 4. What the page draws, and how to read it

The page is one file (`web/blocks.html`, built from `web/blocks/*.js`)
with four views. The **Story** is a numbered sequence:

1. **What happened** — the reading as charts.
2. **Where the time went** — one strip per run along wall-clock, every
   step a segment, thinking light, tools darker, the answer solid, wasted
   steps hatched, the slowest few named; the rationale beneath.
3. **The trace as a tree** — task → runs → phases → steps → values; on a run of more than 80 steps, subdivisions that carry nothing notable start folded into a capsule and anything notable starts open.
4. **Parts and sub-agents** — three drawings of the horizon tree.
   *Icicle*: time along x, rows outward from the shared axis (run,
   sub-agents, parts, steps), widths in seconds, tokens or steps; wasted
   hatched; the fault red; the decisive step ringed; click a part to zoom.
   *Tree*: nodes and links, the run at the left, each sub-agent under its
   parent; node area is time, a wedge the wasted share, a dashed pulsing
   ring a span still open. *Diff*: the two runs' delegation graphs as one
   — each node's halves are the two runs' time, links dashed where only
   one run delegated and thick where the counts differ, a ring on the
   blamed agent; labels in *words* or *compact*.
5. **Why**, 6. **Reconcile**, 7. **Take forward**, 8. **Next horizon** —
   the diagnosis, the splice that would have worked, the prompt
   suggestions, the reward and the pair.

Above the story sits the hero: a super panel (outcome, decisive step,
first divergence, paired stats) over the **body chart** — each run a
trunk along tokens or time, thinking on the trunk, tool calls as
branches, the alignment in the gutter, the fault's path red, bubbles
that fold long stretches until you zoom. Its *debug* switch adds the
phases as state bands, marks for retries, model switches and
no-information steps, the replay verdict under the decisive step, and
six layers for the step under the cursor (model call, tool selection,
tool response, state, output values, replay).

The **Evidence** view holds the map, the run lens and the debug session;
the **Batch** view holds the cross-task blocks: the scorecard, output
equality, routing, and the agent loop.

The **Panels** view is the reader's own grid. Any block can be a panel;
a panel moves, widens to the full row, or goes; the grid has one to
three columns; presets (*time*, *tools*, *agents*, *eval*, *all*) fill
it in one click, and *what you use* fills it from the blocks the page
has recorded the reader opening and starring most. The choice is kept
in the browser, so a reader who wants the heat map beside the latency
strip opens the page that way next time; when the page has seen which
block a reader opens most, one chip offers to add it. Four panels were
drawn for this view and stand as evidence too, ordered overview first:

- **Where the seconds went** — a treemap per run, both on one scale, so
  the run that took longer is the larger map; inside it sub-agents and
  parts as boxes and every step a tile whose area is its seconds. Light
  tiles think, solid ones call a tool, hatched means wasted, a red edge
  marks the fault's path. The eye finds the big tile before reading a
  number; a click on a box zooms into it, on a tile opens the step.
- **Where it mattered** — the long, multi-agent run as a tree: run →
  sub-agents → clusters of steps, in the same architecture as the trace
  tree. Steps are clustered at sub-agent and phase boundaries and every
  cluster is scored by what it carried (the decisive step, the fault's
  path, divergences, errors, retries, wasted seconds, milestones, the
  answer); a cluster's bar is its impact on one scale for both runs, so
  the stretch that mattered stands out and the quiet ones fold into a
  capsule node whose size is the steps inside. A capsule dilates on a
  click, a cluster opens to its marks as leaves, a mark opens the step:
  details exactly on demand. Trunk and even-time modes remain.
- **Tool behaviour, and the dossier on demand** — per tool and per
  run: calls, distinct inputs, repeats and the longest run of identical
  calls, errors, wasted calls, latency, and every sub-agent that touched
  the tool. Under it, the suggestions for the next prompt derived from
  the contrast between the runs, each a sentence with its evidence and a
  copy button — "do not call run_tests again with the same input: comet-lh
  repeated `pytest -q tests/ledger` 12× in a row at steps 397–430". Any
  tool anywhere opens its dossier: click a name in a table or a row
  label in the heat map, double-click a tool step in the body chart or
  the tree. The dossier is the same numbers for one tool, both runs, the
  agents that touched it, a strip of its calls (click one to open the
  step), sample inputs and outputs, and the suggestions that concern it.
- **Trust & behaviour** — the statistics settle who won; this ledger
  says how each agent behaved and how far the data can be trusted:
  tool calls, stops and who stopped it, loops and retries, effects and
  permissions (writes without a read, forbidden calls, calls that reach
  outside), determinism (replay verification, run consistency), and the
  data itself (adapter, grader, SYNTHETIC, measured shares). The grade
  is a rubric with every deduction spelled out, never a black box.
- **Milestones** — for a long task with a golden set, the ladder:
  the milestones a correct solution passes through, one stepped line
  per run over time or steps, a mark where each run reached each rung
  and the rungs it never reached named at the edge. Where a line stops
  is where that run stalled; the gap between the lines at a rung is
  how much later one run got there.
- **Calls × time** — a heat map: one row per tool (by total seconds),
  then thinking and the answer; time in bins along the run; each cell
  split, A over B, darker for more seconds, hatched where those seconds
  were wasted; row totals A · B at the right. Where one run spent its
  time on which tool, and whether the other did, is read in one look.
- **Tool matrix** — per tool, side by side: calls, seconds, per call,
  wasted, errors, each cell A over B with a bar scaled to the larger.
- **Latency by tool** — every call as a dot at its latency, A above the
  line, B below, hollow when wasted, a tick at the mean: the spread of
  a tool's latency, not only its mean, and the outlier that cost the run.

A click on a cell or a dot moves the shared cursor, so the step opens
in the body chart's inspector.

The design rule applied everywhere: one line of controls and key, one
chart, one line per run saying what mattered, everything else behind a
single fold. Every chart has a table view; tooltips enhance, never gate.
The chrome is quiet: no borders, no boxes, a block is a small label and
its chart with air around it, and the only saturated ink on the page is
data — run A, run B, the fault's red — so the charts carry the eye.

## 5. The agentic loop

`agentdiff loop` runs two agents on a task set and drives the whole
cycle itself: a baseline; the comparison and the reading; a prompt
hypothesis from a finding (or `--suggest`); a paired experiment of the
agent against itself with the change on the same tasks; keep or revert
on the paired result (kept when wins exceed losses with no
always-pass→always-fail regression; `kept` under a sign test below 0.05,
`kept (provisional)` otherwise); more runs on the families whose
routing pick is still unclear, widest interval first; a stop with a
stated reason. The controller is `planner.py`, rules over numbers; the
ledger `loop.json` carries every decision and is resumable. One variable
per experiment; a hypothesis whose source failure stopped reproducing is
dropped, not run; equal rates over six runs a side are a tie no run can
break. `docs/AGENTIC.md` argues the design.

## 6. The evaluation scorecard

`agentdiff eval` (and every `runs`, `batch` and loop aggregate) scores
each agent on accuracy (task success, the outcome score, the judge
beside the grade), tool selection (correct tool, wrong-tool calls,
undeclared tools, invented arguments), retrieval quality (useful tool
results over calls, expected evidence retrieved over a golden list),
grounding, safety and policy (forbidden tools and patterns, blind and
unverified writes, risk flags, risk against reward), trajectory quality
(loops, repeats, stopping when done, recovery), and spend (latency,
wasted seconds, share waiting on tools, cost, tokens). Every rate has a
95% Wilson interval. A golden set (`docs/EVAL.md`) makes tool
correctness, expected evidence and policy measurable; the card says
whether it was offline (a golden set) or online (traces as recorded). A
judging model's verdicts are reported beside the grade with their
agreement and the 2×2 — never merged into it.

## 7. Where the numbers come from

- **Wilson intervals** for every rate, because small suites live at 0/8
  and 8/8 where the normal approximation fails.
- **Paired inference** over per-task rates with a paired standard error
  and an exact two-sided sign test on the discordant tasks; it refuses
  to distinguish below ten paired tasks and says so.
- **Routing confidence** from the lower bound of the Wilson interval:
  *clear* when the top two intervals separate, *overlapping* when they
  do not, *insufficient* under three runs a candidate.
- **Output equality** after a named normalisation (case, punctuation,
  thousands separators, unit spellings), so "23h 45m" and "23 hours 45
  minutes" are the same answer and "11 hours" is not.

## 8. Feeding it

`agentdiff run` runs any provider or your own agent through the harness
(graded, terminations declared, files named); `Recorder` records as it
happens, with `span()` for delegations; `convert --format otel` reads
OTel GenAI span trees with nested agents; the Claude Code hook records a
session live; `watch` serves the page and streams running agents into
it; `db` keeps every trace in SQLite with full-text search and
checkpoints; `route --db` and `eval --db` run over everything recorded.

## 9. What is novel, and what is not

Verified against the landscape (`docs/LANDSCAPE.md`): pairwise trajectory
alignment as the unit of analysis, deterministic causal attribution with
propagation, behavioural similarity across a fleet, cause reproducibility
across runs, and now the delegation-graph *diff* and agent-level blame
without a model, exist in no product surfaced. Counterfactual replay is
*not* unique (Retrace ships it); sequence alignment of runs exists in
research (TRACEPROBE, counterfactual trace auditing); the "which agent,
which step" framing is the benchmarks' (Who&When, AgenTracer). The
limits are stated in the same file: blame is only as right as the
decisive step; the graph diff aligns by agent name; parallel sub-agents
are read from OTel timestamps as sequential — a critical-path analysis
over overlapping spans is the next thing to build.
