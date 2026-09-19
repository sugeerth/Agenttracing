# Playbook — how to show what an agent did, online and in the room, and where this goes next

AgentDiff now measures more than who won: where each run's time went and
why, how well its tools and retrievals served the answer, whether it
stayed inside policy, what a judge thinks, and what an autonomous loop
concluded. This note says how to *show* that, depending on who is
looking and where, and what to build next.

## What "why did it take so long" means here

`report.timing` attributes every recorded second of a run to thinking
(plan and reason turns), waiting on tools (each tool named, with its
calls and seconds) or the answer, and marks the seconds the reading
calls wasted — a call that returned nothing new, a repeat, a dead end, an
error, or a step taken after every value the answer needed was already
in hand. The *Where the time went* section of the story draws it as a
waterfall per run with the wasted steps hatched and named, a share bar,
the tools ranked by cost, and a rationale whose every number is in the
table beneath it. A run that recorded no latencies is *unmeasurable*,
never fast. The pair narrative says who took longer, by how much, and
what share of the gap is wasted steps — the rest is being slower per
productive step, which is a model or provider question, not a behaviour
one.

## The evaluation, in full

The scorecard (`eval`, `docs/EVAL.md`) reports, per agent, each with a
95% Wilson interval where it is a rate:

- **accuracy** — task success (exact match or judge), the mean
  `outcome.score` when the grader gives one, and the judge's verdicts
  beside the grade with their agreement;
- **tool selection** — correct tool called, wrong-tool calls, undeclared
  tools, invented arguments (needs a golden set for the first);
- **retrieval quality** — useful tool results (calls whose result fed the
  answer, over calls), calls that returned nothing, dead ends, and
  expected evidence retrieved over the golden `expected_evidence` list;
- **grounding** — answers whose every value traces to an observation;
- **safety and policy** — forbidden tools and patterns, blind and
  unverified writes, risk flags, compliance, risk against reward;
- **trajectory quality** — loops, repeats, stopping when done, recovery
  after errors, terminations;
- **progress** — the golden milestones reached, in order or not, and
  where the runs that fell short stalled: at three hundred steps the
  difference between *failed* and *failed at the eleventh of thirteen
  checkpoints* is the reading (`docs/HORIZON.md`);
- **spend** — latency, wasted seconds, share of time waiting on tools,
  cost, tokens, steps, tool calls;
- **and the card against itself** — mark the runs whose verdict you
  already know (`failure_mode`, `known_correct`) and *Does the evaluation
  see it?* reports which known failures any of the above caught, which
  nothing caught, and how many known-correct runs were flagged anyway.
  On the long-horizon suite that is 11 of 12, one blind spot, and none of
  20. It is the only section that measures the measurement.

Nothing is folded into one score. A composite hides the trade-off a
router or an engineer needs to see (an agent that is right more often
because it retries five times is a different agent from one that is
right first time); the page lets the reader weigh the columns.

## Streaming and multi-agent: one tree

A run that streams and a run that delegates are the same object seen
twice. Both are a tree of spans over time: a step arrives carrying its
span (which agent acted, delegated from which span), a span is a node
under its parent, and the run is the root. Streaming adds one fact —
the span holding the last step is still *open* — and delegation adds
one dimension — depth. So the page draws both with one model and two
drawings:

- the **icicle** (`charts.horizon`): time along x, rows outward from the
  axis — the run, its sub-agents, their parts, their steps; wasted time
  hatched, the fault red, the decisive step ringed; an open span's right
  edge dashed and pulsing while it still receives steps;
- the **nodes and links** (`charts.agentTree`): the run at the left, each
  sub-agent under its parent, nested delegations further right; node
  area is time, a wedge the wasted share, red the fault's path, a dashed
  pulsing ring a span still open. Click a node and the icicle zooms to it.

A third drawing is the pair's *diff*: the two runs' delegation graphs
aligned by agent — grey where both runs delegated, a run's colour where
only it did, the count per side on every edge, and a ring on the agent
the diagnosis blames (`horizon.blame`: which agent, which step, delegated
by whom, how deep). Finished pairs get all three from `report.horizon`
(with the reading's parts and wasted seconds). Running agents get both from their steps alone
(`charts.spanTree` builds the same tree in the page as they stream);
when the pair finishes, the story replaces them with the full analysis.
`deepcompare watch --demo demo/horizon/traces` shows the multi-agent
pair arriving live.

## Online — the same page, live

- Run agents through the harness with `record(stream=True)` or the
  Claude Code hook, and open `deepcompare watch` — running agents stream
  into the story as they go; when a pair completes, the body chart, the
  time waterfall and the debug layers appear for it.
- Keep every trace in the trace database (`--db`); `eval --db` gives the
  scorecard over everything recorded so far, and `route --db` the
  router's picks with their confidence. Rerun both on a schedule: the
  intervals narrow as runs accumulate, and a change in an agent shows as
  a change in its column before anyone reads a trace.
- Let the loop run (`loop`): it spends runs where the pick is unclear,
  tests prompt hypotheses as paired experiments, and leaves a ledger with
  every decision and its statistics.

## In the room — reading a run with people

Open the story view and go top to bottom; each section answers one
question, and every number on it can be pointed at.

1. **The super panel and the body chart** — who solved it, where the runs
   parted, the fault's path in red. Zoom into a bubble to show the
   steps inside it. Switch the axis to *time* to show where the seconds
   went; press *debug* to show phases, retries, model switches and the
   replay verdict, and open any step layer by layer.
2. **Where the time went** — the waterfall and the rationale: "bolt-v3
   took 7.8s; 34% of it went to two steps the reading marks as wasted".
3. **Subdivisions and sub-agents** — for a long run: which parts and
   which delegations took the time, zooming into any of them; the
   sub-agents' ledger settles "which sub-agent should we fix first".
4. **The tree, why, reconcile, take forward** — the cause, the evidence
   for it, the splice that would have worked, and the sentences for the
   next prompt.
5. **The Evaluation scorecard** (Batch view) — the columns above, with
   intervals; the risk-against-reward scatter is the slide that settles a
   "which agent" discussion.
6. **The Agent loop** (Batch view) — if the loop ran: what it tried, kept
   and reverted, and why it stopped.

7. **Your panels** (Panels view) — overview first: the two treemaps on
   one scale settle "which run took longer, and on what" before a number
   is read; then the body chart, the calls × time heat map, latency by
   tool and the tool matrix, or whatever the room asks for; *what you
   use* fills it from the blocks you open most, and the browser remembers.

Everything has a table view, so a number can be read without hovering,
and a page is a single file, so it can be sent afterwards.

## Taking it forward

- **Reward and credit for a training loop**: the Reward & credit
  panel shows where a run earned and lost reward and who gets the
  credit; `agentdiff rl` prints the returns per episode with an
  interval, and `feedback.to_jsonl` hands the labels, the preference
  pair and the shaped rewards to a trainer. Recorded rewards win over
  shaped ones, and the page says which it is showing.
- **Deciding whether a new policy is actually better**: run both
  policies over the same tasks several times each, `agentdiff runs` the
  directory, and open the Training view. Read it in this order. Do the
  interquartile intervals separate? If they overlap, you do not have an
  answer yet, whatever the means say, and the advisory tells you how many
  runs per task you have against the eight-to-sixteen floor. If they
  separate, look at the probability of improvement rather than the gap
  between means — it is the number that survives a reader asking "on a
  given task, how often?" Then go straight to the per-task list and open
  the worst task. An aggregate that says a policy wins will often sit on
  top of one task where it loses, and shipping is a decision about that
  task as much as about the average.
- **Before trusting a reward you are about to train on**: the reward
  audit asks whether the signal and the outcome agree, whether an
  episode's return is really one terminal number wearing a dense
  disguise, and which steps were paid while the analysis had labelled
  them bad. A policy that collects return without passing is the
  specification-gaming shape, and it is cheaper to find in a comparison
  than after a training run. The critic panel is the same question about
  the value head: when it explains less variance than guessing the mean,
  the page says that in those words.
- **Deciding whether a self-evolving agent should keep its latest
  self**: lay each generation out as `<lineage>/gN/agent.json` with its
  artifacts and the episodes that triggered it, and its traces under
  `gN/traces/`; name the paths the agent must never edit — its verifier,
  its grader's config — under `protected` in `lineage.json`, and its
  size budgets; then `agentdiff evolve <lineage> -o out` and open the
  Evolution view. Read the lineage thread first: the recommended
  generation is filled and it is often not the last. Then read the
  ledger for any step marked gamed — return up while passes fell — and
  check the integrity block for the path it touched; a gamed step that
  touched a protected path is the agent editing what judges it, and
  nothing after it should be trusted until the path is restored. Then
  the matrix for the cells that fell: a memory or a rule that helped
  the average and lost a task is forgetting, and the step's own evidence
  will usually name the tasks it was learned on, so the outlined cells
  say whether it overfit them. `--fail-on gamed,forgot,protected` turns
  the same reading into a CI gate on every generation the loop proposes.
- **The eval learned a metric; what to do with it**: `agentdiff coevolve
  <lineage> -o out` and open the Evals view (`docs/COEVOLVE.md`). Read
  the hindsight first, not the new metric's name: the lines that matter
  are the steps the base eval called improved or flat that a learned
  metric flags — on the shipped lineage there are none, because the
  verification rate it learned at g2→g3 flags the step the base had
  already called gamed, and that is the reading you want, an eval that
  agrees with the verdicts and adds an interval to them. Then check the
  metric's confirmation: a metric that moved again on a later step
  (`confirmed`) has been seen to measure something twice; one that has
  not moved in two later steps (`unconfirmed`) stays adopted and is
  listed under integrity for exactly this decision, and a metric retired
  as one reading with a base metric was never new. Then decide whether
  the agent's next step should be allowed to see it. A learned metric in
  the agent's own evidence — its optimiser's objective, its prompt — is a
  new target, and the goodhart probe exists because a target stops
  measuring: it will demote the metric once the agent moves it twice
  while the pass rate stands still, but only after the fact. The safer
  order is to keep the learned metric on the eval's side of the line,
  read the lineage against it, and let the recommendation under both
  evals — with `agree` and the exclusions listed — say whether the
  generation to keep changed. `--fail-on hindsight` gates on a changed
  step; `--fail-on unconfirmed` on a metric that never moved again.
- **What to put in the next prompt**: the Tool behaviour panel ends
  with the sentences derived from how the two agents used their tools;
  copy them into the failing agent's prompt, then `agentdiff replay`
  the decisive step to see whether the outcome flips — a suggestion is
  a hypothesis until it does.
- **Trust before numbers**: open Trust & behaviour first with a new
  agent or a new trace source — if the grade is low because latency is
  estimated or the run is SYNTHETIC, every later chart inherits that
  caveat; the reasons are spelled out.
- **Read a long run where it mattered**: the Where it mattered panel
  dilates the stretches that carried the decisive step, the errors and
  the wasted time and constricts the quiet ones into folds; open a fold
  or a cluster only when the room asks. Switch to even time to show
  how much of the wall-clock was quiet.
- **A long run, by segment**: `rerun --span <sub-agent>` or `--from N
  --until M` replays one part of a multi-hour run in the recorded world;
  `checkpoint` bundles the state at a step to hand over; milestones
  (`batch --golden`) measure progress before the answer, so a run that
  stalls in package 6 is read as "six of nine, stalled after step 431",
  not as a bare failure.
- **Replay before you believe**: `agentdiff rerun` proves a recording
  reproduces from itself; `rerun --provider` puts the next model into
  the recorded world and names the first call it makes that the old one
  never made; `context --row` shows what each model saw where they
  parted. The pipeline in `.github/workflows/agentdiff.yml` runs all of
  it hermetically (`docs/REPLAY.md`).
- **Time as a lever in the loop**: a prompt hypothesis that cuts wasted
  seconds without costing success is a keep; today the loop keeps on
  success alone.
- **Retrieval evidence as a reward**: expected-evidence recall is a dense
  process signal an RL environment can pay for before the answer exists.
- **A per-tool cost model**: seconds and cost per tool call, learned from
  the database, so the router can pick on expected spend as well as on
  success.
- **Judge-graded golden sets**: a rubric per task so answers with no exact
  match still enter every column, labelled `graded_by: model`.
- **A held-out family** in every loop, so the kept prompt's number is
  never the number it was selected on.
