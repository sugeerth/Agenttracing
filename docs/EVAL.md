# Agent evaluation — the scorecard, golden sets, offline and online, the judges

`agentdiff eval` scores every run of every agent on the dimensions an
evaluation of agents needs, and every one of them is a count or an
interval over the runs listed. The same scorecard is attached to every
`runs` and `batch` aggregate (`aggregate.scorecard`) and to every
iteration of the loop, and the page draws it as the *Evaluation
scorecard* block.

## Dimensions

| dimension | how it is measured | needs |
|---|---|---|
| task success | `outcome.success` over runs, 95% Wilson interval | a graded run (expected answer or a judge) |
| correct tool called | every `expected_tools` entry called, at least one of `any_of_tools`, no other tool when `only_expected_tools` | golden set |
| useful tool results | tool calls whose result fed the answer (the reading's `feeds_answer` role), over calls | — |
| expected evidence retrieved | golden `expected_evidence` strings found in any observation, over the list | golden set |
| answer grounded | every value in the final answer traces to an observation in the run (the reading's `answer_basis`: `supported == atoms`) | — |
| policy compliant | no forbidden tool, no forbidden pattern in a tool input, writes within `max_writes`, no write before a read when `write_requires_read` | policy or `forbidden_tools` |
| no risk flag | none of: forbidden tool, forbidden pattern, blind write, unverified write, undeclared tool, invented argument, loop, step limit | — |
| stopped when done | no *fetch* after the answer's basis was complete — composing the report is not carrying on, and a check that follows the run's own write is part of the write | a run whose answer has a basis |
| no loop | no repeated block and no call cycle, judged against the length of the run (`process.loops`: a block that turns 3+ times or covers 6+ steps, or one call that is 3+ times *and* a tenth of the tool steps) | — |
| every milestone reached | the golden task's milestones, all of them, matched in the steps' text | golden milestones |
| milestones reached in order | the order they were listed is the order a solution passes them | golden milestones |
| milestones reached (over milestones) | reached / named, summed over runs; the agent block adds `stalled_at`, where the short runs stopped | golden milestones |
| no tool error | no tool step returned an error | — |
| errors recovered | recovered errors / errors (over errors, not runs); recovered means *the same tool returned within five tool steps*, so going on to unrelated work is `moved on`, not recovery | at least one error |
| latency, wasted seconds, share waiting on tools, cost, tokens, steps, tool calls, accuracy score | mean, median, min, max per run, as recorded (`report.timing` for the wasted seconds; `outcome.score` for the accuracy score) | — |
| risk vs reward | reward = success rate; risk = share of runs with a flag; ratio = reward / risk, none when nothing was flagged | — |
| trajectory counts | repeated calls, cycles, looping runs, steps after done, no-information steps, step-limit runs, writes and blind writes, terminations | — |
| LLM judge | judged-solved rate with interval; agreement with the exact-match grade; the 2×2 of grade × judge; and, against known failures, what only the judge caught and what it said about the correct runs | `--judge` |

A dimension that cannot be measured for a run reads `None` and the
page says why ("needs a golden set with expected_tools"); it never
enters a rate as a pass or a fail.

## Does the evaluation see it?

Every dimension above scores the agent. One block scores **the card
itself**. A golden task may declare what is known about its runs:

```json
{"id": "L01_service_migration", "failure_mode": "skipped_unit",
 "failure_mode_agents": ["drift-lh"], "milestones": [...]}
{"id": "L13_schema_refactor", "known_correct": true}
```

With that, `eval` reports `detection` — for each known failure, whether
*any* dimension said something was wrong and which; the modes nothing
caught; how many were graded a pass regardless; and how many known-correct
runs were flagged anyway. On the long-horizon suite it reads:

> 12 of 12 known failures caught; 7 were graded a pass, 7 of them caught
> by something else; 0 of 20 control runs flagged.

Read both halves or neither: a card that flagged everything would also
report 12 of 12, and the control line is what tells the two apart.

A card that cannot say this is a card you have to take on trust. A golden
set that names no known failure makes the block unmeasurable, with the
reason, rather than reporting a perfect score over nothing.

The page draws the same table in the *Evaluation scorecard* block, and
prints the modes nothing caught as **caught by nothing** rather than
omitting them: a card that quietly leaves out its blind spot reads as a
clean bill of health.

**At length, these dimensions behave differently, and several of them used
to behave wrongly.** `docs/HORIZON.md` measures every one of them against
a sixteen-task, thirty-two-run long-horizon suite with twelve named
failure modes and four controls — and again over 200 generated pairs
(400 runs, ~92,000 steps): which modes each dimension catches, and the
false-positive rate on the runs known to be correct (zero, both times).
The short version: seven of the twelve failures are graded a *pass*, and
all seven are caught by the milestone, grounding, policy, recovery and
redundancy dimensions instead. **No single dimension catches more than
half**, which is the case for reading a card rather than a number.

## The golden dataset

The tasks file the harness already reads, with evaluation fields:

```json
{"policy": {"forbidden_tools": ["shell"], "forbidden_patterns": ["rm -rf"],
            "write_requires_read": true, "verify_after_write": true, "max_writes": 3},
 "tasks": [
  {"id": "t05_flight_duration", "prompt": "…", "expected": "23 hours 45 minutes",
   "expected_tools": ["datetime_diff"], "forbidden_tools": ["calculator"],
   "expected_evidence": ["13h40m", "7h50m"], "family": "compute"},
  {"id": "t01_acme_revenue", "prompt": "…", "expected": "$4.82 billion",
   "any_of_tools": ["web_search"], "only_expected_tools": false}
 ]}
```

`demo/golden/tasks.json` is the golden set for the shipped demo. A
policy may also live in its own file (`--policy policy.json`); the
golden file's policy applies when no separate one is given.

## Offline and online

- **Offline**: run the golden set through the harness (`run`, or the
  loop), then `eval traces/ --golden golden.json`. The scorecard says
  `offline — golden set` and how many tasks the set covers.
- **Online**: the traces as they were recorded — a Claude Code hook, the
  watcher, a trace database — `eval --db traces.sqlite` or `eval
  traces/`. The scorecard says `online — traces as recorded`; tool
  correctness and policy read as not measurable unless a golden set or
  policy is given as well.

Every rate is the same computation in both modes; only the inputs
differ, and the mode is written on the card.

## LLM as a judge

`eval … --judge NAME=kind:model` (or `judge`, or `loop --judge`) asks a
second model to grade each final answer against a rubric. The verdict
is recorded as `outcome.judge` beside the grade and reported in the
scorecard *beside* it: judged-solved rate with its interval, agreement
with the exact-match grade, and the 2×2 of runs the two agree and
disagree on. It replaces the grade only when asked (`judge --apply`,
or `loop --judge`, where it is the grader for tasks with no expected
answer); such traces say `graded_by: "model"` and the exact match, when
there was one, stays the reference for agreement. A model judging its
own run is flagged `self_judged`.

**The judge is scored the way every other dimension is.** When the golden
set names known failures, *Does the evaluation see it?* reports a `judge`
line beside its own numbers: how many of the known failures the model read
and called wrong, how many of those **no other dimension caught**
(`only_the_judge` — the figure that says whether the judge is earning its
cost), and how many of the runs *known to be correct* it called wrong.
Read those two together. A judge that calls everything wrong catches every
failure, and the control line is the only thing that tells it apart from
one that can see.

It is reported beside and never merged in: `caught`, `missed` and
`by_signal` stay computable from the traces alone. A sampled verdict
folded into them would make the card unreproducible without saying so.

**On a long run the judge reads an excerpt, and the block says which.**
`--with-steps` cannot fit three hundred steps in a prompt, so it sends the
opening and the closing with a literal `... 260 steps omitted here
(indexes 20-279) ...` where the gap is, and records `steps_shown`,
`steps_total` and `steps_basis` on the verdict; the card counts the
verdicts that were `on_an_excerpt`. `--steps-cap N` raises it. On the
long-horizon suite a 40-step excerpt chosen *by position* contains the
step where the run goes wrong **33.4%** of the time over 1,846 known
failures, and no positional excerpt shorter than 274 steps contains all
twelve of the shipped suite's.

**`--focus` spends the same budget better.** Instead of the ends of the
run it shows what the run itself flags — an error nothing repaired, a
block of steps that produced nothing new, a write with no check after it,
a policy breach, a beat its own rhythm skipped, an irreversible act with
most of the run still to come — keeping a quarter of the budget for the opening and a
quarter for the ending. Over 1,846 known failures that is **75.0% at 40 steps against
33.4%**, and structure at 20 steps beats position at 160 — an eighth of
the tokens. Per mode it is all or nothing with no remainder: nine modes
found in every one of 154 runs, three in none, and no mode in between. `docs/HORIZON.md` has the table and why those four cannot be
reached. The selector reads the trace and
the policy — including a task's own stated constraints, which the agent
was told before it started — and never `milestones`, `expected` or
`failure_mode`, which are facts about how the run turned out.

`--rubric long-run` asks the model about the work rather than the prose.
The judge is never shown the golden set.

## Agent as a judge

The LLM judge above has a ceiling no prompt moves. A three-hundred-step
run does not fit in a prompt, so it is shown an excerpt, and the best
excerpt anyone here has built contains the step where the run went wrong
**75% of the time** (`docs/HORIZON.md`). The other quarter is a verdict
about a part of the run that does not contain the failure.

The way out is not a bigger window — it is to stop choosing one.
`agentdiff judge <traces> --agent --provider NAME=KIND:MODEL --golden
tasks.json` gives the judge **tools over the trace** and lets it look:

| tool | what it answers |
|---|---|
| `graph()` | how long the run is, which tools it used, how often, when each first appeared |
| `locate(term)` | the steps whose call, input or result mention a thing |
| `read(start, end)` | a range of steps, in full |
| `flags()` | **the deterministic reading** — every place `agentdiff.excerpt` marks, with its reason |

The first three are the method as published. The fourth is this
repository's addition: the engine already knows where a run stops
behaving like one that is going well, and knows it *without being told
what the task was*, so it can be handed to the judge as a retrieval tool
rather than kept for the card.

**It judges one requirement at a time, with no memory between them.**
That is not a design preference — it is the paper's ablation, which found
memory actively harmful because a wrong judgement carried forward starts
a chain of them. Each requirement gets a fresh loop and a fresh provider.

**The requirements are the spec, never the answer.** A golden milestone
carries what had to happen *and* the string the world produced when it
did. Only the first ever reaches the judge (`requirements_of`); a test
asserts that nothing else from the golden task does.

**The judge's own run is a trace.** It goes through the same
`run_task` loop as any agent, so how many turns it took, which tools it
used and *whether it looked at anything before answering* are recorded
and reported. A judge that answers without looking is the failure this
whole approach exists to avoid, and it is visible.

### Reading a judge's verdict honestly

The card reports both judges in `detection`, beside its own numbers and
never inside them, with the guards the literature says to keep:

- **Cohen's κ, not raw agreement.** A judge that calls everything wrong
  agrees with a mostly-correct corpus some of the time and has told you
  nothing; κ subtracts the agreement two verdicts would reach by voting
  independently at their own base rates. Above 0.8 is strong, 0.6–0.8
  moderate. On the stand-in shipped for testing — which calls every run
  wrong — raw agreement reads 37.5% and **κ reads 0.0, "poor"**, which is
  the correct answer.
- **`controls_called_wrong`.** The runs known to be *right* that it
  called wrong. Read it in the same glance as the catch rate or the catch
  rate means nothing.
- **`only_the_judge`.** The known failures every deterministic dimension
  passed and the model did not — what a judge is actually being paid for.
- **Self-preference by family, not by string.** A judge scores its own
  family's work a reported 10–25% higher, and `gpt-4o` judging
  `gpt-4o-mini` is the same family however different the two strings
  look.
- **Verbosity.** The rubric separates correctness from style in as many
  words, because long answers score higher even when they are worse.

Reported numbers for the method: 92.07% / 90.44% alignment with human
consensus against LLM-as-a-Judge's 70.76% / 60.38% in grey-box and
black-box settings, at 2.3% of the cost of human evaluation
([Zhuge et al., 2024](https://arxiv.org/abs/2410.10934)). **Nothing in
this repository reproduces those numbers** — no model has been run
against this suite, and the stand-in used in the tests is a fact about
the stand-in. What is measured here is the machinery: the tools, the
boundary, and the guards.

## A panel: one judge, many runs, checked against them

The judges above answer *did this run do X*. After forty runs that is the
wrong question — forty independent verdicts are forty anecdotes, and the
thing worth knowing is the pattern between them:

- What is wrong with this **agent**, as opposed to this run?
- Why does one side succeed where the other fails on the same task?
- Is it one cause or five?

`agentdiff panel <traces> --provider NAME=KIND:MODEL` gives a judge tools
over the **corpus** and asks for a synthesis rather than a grade:
`corpus()` for what is there, `runs(agent=…, task=…, failed=true)` to
filter, `contrast(task)` for the two sides of a task with the first call
they differ on, and `open(run)` / `locate` / `read` / `flags` to descend
into any single run.

### Every claim cites, and every citation is checked

This is the part that makes a synthesis worth reading rather than worth
believing. The panel returns each finding with `cites` — a run, a step
index, and the fragment it says is there — and the engine then goes and
looks. A run that does not exist, a step out of range, a quotation the
step does not contain: the finding is **dropped from the synthesis**, and
the claim and the reason are kept so a reader can see what was rejected.

Nothing about that check involves a model. It is string containment
against the recorded step, and it is the difference between a fluent
paragraph and a finding. In the tests, a claim that reads

> The orchestrator systematically re-verifies work it has already
> checked, at real cost.

— plausible, well written, about the right run — is dropped, because the
text it quotes is not at the step it cites. One bad citation sinks the
finding: a claim resting on two facts of which one is invented is not
two-thirds true.

```bash
agentdiff panel traces/ --provider p=anthropic:MODEL --checkpoint panel.ckpt -o out/
agentdiff panel traces/ --provider p=openai:MODEL --ask "Why does drift-lh fail where summit-lh does not?"
```

### It is a long task, and it is built as one

Reading hundreds of runs takes tens of minutes to hours of turns, so the
panel writes its findings after each one and resumes from them:
`--checkpoint FILE` re-reads what is already answered and asks only what
is left. An agent asked to read four hundred runs will be interrupted;
losing an hour of reading to a network blip is a property of the harness,
not of the model.

The same three refusals as the single-trace judge: **no golden set** (it
is asked what it finds, so handing it the answers would make the exercise
a reading test — a test asserts no `failure_mode` or milestone evidence
reaches the prompt), **no memory between questions**, and **it cannot
move a number** — the synthesis sits beside the card.

## What the traces taught: lessons that must hold twice

Everything above scores a corpus. `aggregate.lessons` learns from one,
and the difference is that a lesson has to survive being checked on runs
it was not drawn from.

Seventeen properties a run can have are tried — the page's own trace
signs (an error the run never came back to, a stretch that produced
nothing new, a write never checked), the corpus attributes, and one
attribute that reads a label on the trace. Each is scored as the
difference in wrong-rate between runs that have it and runs that do not,
**within the same task**, so a hard task that provokes the property and
the failure alike cannot manufacture the association. On a corpus of
pairs that difference is read off the tasks where the two runs split on
the property, and the sentence says so: *in 17 of 17 tasks where only one
side did, that side was the wrong one*.

**Twice.** The tasks are split in two by a hash of their id, fixed before
anything is scored. A property that separates wrong runs from right on
one half is a lead; it is a lesson only when it does so on both, the same
way. With seventeen tried, one of them will look strong on half a corpus
by chance, and the second half is what tells that one apart. A lesson
with too few runs on one side of a half is reported as too few to test,
never as held.

**Wrong** is the golden set's label where there is one — the only label
here that does not come from the run itself — and the run's own outcome
otherwise. On the long-horizon suite that is twelve runs, not the five
the grade calls failures.

**An annotation is not a lesson.** A step annotated *bad* separates the
suite's wrong runs perfectly, because whoever annotated it knew which
runs were wrong. It is listed under its own heading and never leads the
narrative or the verdict line.

**The ledger** is how the page learns over time rather than once:

```bash
agentdiff batch week1/ --golden g.json --lessons lessons.json -o out1/   # learns, writes the ledger
agentdiff batch week2/ --golden g.json --lessons lessons.json -o out2/   # re-tests every lesson, adds new ones
```

Every lesson in the ledger is re-scored on the whole of the new corpus —
none of which it was learned from — and marked held again, weakened,
reversed or too few to test; the corpus verdict opens its *Learned* line
with what held again. A corpus is fingerprinted, and the same corpus read
twice is not counted twice: re-running a batch is not new evidence. These
are associations, not causes — where to look first, with the counts
behind every sentence.

## The forge: evals the traces write, and a loop that goes back

A scorecard asks a fixed set of questions. The forge grows the set, and
most of its design is about not fooling itself while it does.

**An eval is a rule, not a prompt.** Each one is a deterministic
assertion over a trace, from a small closed grammar (`forge.RULES`):

- a trace mark (`mark:unrecovered_error` and the page's other marks)
- `no_check_after_last_edit` and `claims_without_check`
- a recurring issue's signature (`signature:tool_selection/a:…`)
- a tool's use or absence
- a repeated call, or a streak of errors
- a pattern in a tool's output or the final answer
- `{"all": [a, b]}`, both at once

Any rule runs on any trace, without a model, and gives the same verdict
every time.

**Where candidates come from:**

- *template*: every rule the grammar can build from what the wrong runs
  contain
- *signature*: the corpus's recurring issues
- *seed*: a step a reader clicked on the strip chart. The page keeps the
  marks in the browser, and *Download marks* writes the file
  `--seeds` reads.
- *judge*: `agentdiff forge DIR --judge NAME=KIND:MODEL`
- *refined*: a rule that flagged right runs, narrowed by joining it with
  another rule that the same wrong runs also satisfy

**Two halves, used once each.** The tasks are split by a hash of their
id. Every candidate is tried on the learn half. Only the candidates that
pass there, meaning they catch at least one wrong run and fire on no more
than 10% of the right ones, meet the held-out half, once. That meeting
alone decides adoption. Refinements are built from the learn half, so
the held-out half stays unseen until the test. Refinements are also
capped per round, because every candidate that meets the held-out half
is one more test of it, and a held-out half tested without limit stops
being held out. The narrative states how many held-out tests were run.

**The judge proposes; the held-out half decides.** The judge reads the
learn half's runs through the panel's tools (`runs`, `open`, `locate`,
`read`, `flags`). It gets one more tool, `try_rule`, which scores a rule
on the learn half and names which uncaught runs it would catch. It can
propose, test and revise. Its final rules are then tested exactly like
the templates. A test checks that no held-out task id ever appears in
anything the judge is shown. A rule that does not parse is refused with
its error. The next round's brief tells the judge which of its rules
failed, on which half, and why. That brief is the "go back" the loop is
named for.

**The suite grows by what each eval adds.** Adopted evals are taken
greedily by the wrong runs they catch that nothing earlier caught. A
valid eval that adds nothing is *redundant* and stays out. Each round
targets what is still uncaught. The page draws coverage round by round,
with the false-alarm rate on the same axes. On the long-horizon suite,
round 1 catches 6 of 12 wrong runs, a refinement raises that to 8, and
no right run is flagged at any point. The four runs nothing catches are
listed; they are where a reader's marks, or the judge, should look next.

**The ledger** (`batch --evals LEDGER`) carries the adopted suite to the
next corpus. There, every eval is re-tested on runs it has never seen
and is kept, retired as noisy, or marked blind. A corpus read twice is
not counted twice. Learned on the 60-task procedural corpus and carried
to the 16-task suite, 5 of 6 evals were kept.

## Commands

```bash
agentdiff eval demo/runs/traces --golden demo/golden/tasks.json -o eval/        # offline
agentdiff eval --db traces.sqlite -o eval/                                       # online
agentdiff eval traces/ --golden golden.json --judge j=anthropic:MODEL --with-steps
agentdiff judge traces/ --provider j=openai:MODEL --with-steps --focus --rubric long-run
agentdiff runs traces/ -o out/ --golden golden.json        # the scorecard on the page
agentdiff batch traces/ --golden golden.json --lessons lessons.json -o out/   # lessons, carried to the next batch
agentdiff batch traces/ --golden golden.json --evals evals.json --seeds eval-seeds.json -o out/   # the forge
agentdiff forge traces/ --golden golden.json --judge j=anthropic:MODEL --evals evals.json -o out/  # with a judge
agentdiff loop --tasks golden.json --golden golden.json --judge j=openai:MODEL …  # every iteration scored
```
