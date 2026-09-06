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
- **spend** — latency, wasted seconds, share of time waiting on tools,
  cost, tokens, steps, tool calls.

Nothing is folded into one score. A composite hides the trade-off a
router or an engineer needs to see (an agent that is right more often
because it retries five times is a different agent from one that is
right first time); the page lets the reader weigh the columns.

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
3. **The tree, why, reconcile, take forward** — the cause, the evidence
   for it, the splice that would have worked, and the sentences for the
   next prompt.
4. **The Evaluation scorecard** (Batch view) — the columns above, with
   intervals; the risk-against-reward scatter is the slide that settles a
   "which agent" discussion.
5. **The Agent loop** (Batch view) — if the loop ran: what it tried, kept
   and reverted, and why it stopped.

Everything has a table view, so a number can be read without hovering,
and a page is a single file, so it can be sent afterwards.

## Taking it forward

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
