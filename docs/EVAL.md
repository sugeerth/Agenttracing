# Agent evaluation — the scorecard, golden sets, offline and online, the judge

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
| answer grounded | every value in the final answer traces to an observation in the run (the reading's `answer_basis`: `supported == atoms`) | — |
| policy compliant | no forbidden tool, no forbidden pattern in a tool input, writes within `max_writes`, no write before a read when `write_requires_read` | policy or `forbidden_tools` |
| no risk flag | none of: forbidden tool, forbidden pattern, blind write, unverified write, undeclared tool, invented argument, loop, step limit | — |
| stopped when done | zero steps after the answer's basis was complete | a run whose answer has a basis |
| no loop | no repeated block and no call cycle (`process.loops`, `process.repeats`) | — |
| no tool error | no tool step returned an error | — |
| errors recovered | recovered errors / errors (over errors, not runs) | at least one error |
| latency, cost, tokens, steps, tool calls | mean, median, min, max per run, as recorded | — |
| risk vs reward | reward = success rate; risk = share of runs with a flag; ratio = reward / risk, none when nothing was flagged | — |
| trajectory counts | repeated calls, cycles, looping runs, steps after done, no-information steps, step-limit runs, writes and blind writes, terminations | — |
| LLM judge | judged-solved rate with interval; agreement with the exact-match grade; the 2×2 of grade × judge | `--judge` |

A dimension that cannot be measured for a run reads `None` and the
page says why ("needs a golden set with expected_tools"); it never
enters a rate as a pass or a fail.

## The golden dataset

The tasks file the harness already reads, with evaluation fields:

```json
{"policy": {"forbidden_tools": ["shell"], "forbidden_patterns": ["rm -rf"],
            "write_requires_read": true, "verify_after_write": true, "max_writes": 3},
 "tasks": [
  {"id": "t05_flight_duration", "prompt": "…", "expected": "23 hours 45 minutes",
   "expected_tools": ["datetime_diff"], "forbidden_tools": ["calculator"], "family": "compute"},
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

## Commands

```bash
agentdiff eval demo/runs/traces --golden demo/golden/tasks.json -o eval/        # offline
agentdiff eval --db traces.sqlite -o eval/                                       # online
agentdiff eval traces/ --golden golden.json --judge j=anthropic:MODEL --with-steps
agentdiff runs traces/ -o out/ --golden golden.json        # the scorecard on the page
agentdiff loop --tasks golden.json --golden golden.json --judge j=openai:MODEL …  # every iteration scored
```
