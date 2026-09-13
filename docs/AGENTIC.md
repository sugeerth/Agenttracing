# The agentic loop — design notes

`agentdiff loop` makes AgentDiff run itself: run two agents on a task
set, compare, read the failures, turn a finding into a prompt
hypothesis, test it, keep or revert it, spend further runs where the
routing pick is still unclear, stop for a reason. This note says how
the loop is built and why, including the choices that were mine to make.

## The controller is not a model

Everything the loop *decides* comes from `deepcompare/planner.py`: a
pure function from the loop's state to the next action, built from
success counts, Wilson intervals, routing confidence and paired
comparisons. The models are the things under test (and, with `judge`,
optionally the graders of an answer). None of them is in the control
path. This is the same stance the rest of AgentDiff takes towards
narration — a model may describe, never alter a number — carried into
autonomy: an agentic system whose controller can be talked into a
conclusion is not one whose ledger can be trusted. A rule-based
controller is also cheap to audit: every decision is written with the
sentence and the counts it rests on, and the same state always yields
the same plan (`tests/test_planner.py`).

## One variable per experiment

A prompt suggestion from the reading is a hypothesis. The loop tests it
the only way that is fair: the agent with its current prompt against
the same agent with the change, on the same tasks, the same number of
runs each — a paired design, analysed with exactly the `runs` machinery
a person would use (`iter-NN/` is a full `runs` output, page included).
Two untested changes are never stacked, so a kept change is attributable.
The variant runs are recorded under `<agent>+p<n>`; when kept they become
the agent's runs from then on, and the ledger names that relabelling.

Keep or revert is a rule with its counts: kept when the change wins more
tasks than it loses and no task regresses from always-pass to
always-fail; `kept` when the sign test on the discordant tasks is below
0.05, `kept (provisional)` otherwise — small evidence is kept, but
labelled, and the next comparison re-measures it. A change with no
effect is reverted, not kept "because it did no harm": a prompt should
carry only sentences that earned their place.

## Spend runs where uncertainty is

After the baseline, further runs go to the task families whose routing
pick is not clear — widest interval first — instead of uniformly over
everything. Two candidates with the same rate over six runs a side are
a tie no further run can break; the loop says so instead of spending
runs on it. A queued hypothesis whose source failure no longer
reproduces under the current prompt is dropped without a run, with its
reason. A run budget (`--max-runs`) shrinks batches before it stops
them, and the stop reason is always one of: iteration budget, run
budget, converged, or an analysis error — never silence.

## What stays honest

- Every run spent is a trace on disk; every number in the ledger is a
  count or an interval over the runs listed.
- Ungraded tasks are refused by the harness as before; the loop cannot
  turn a guess into a success rate.
- The reading's templates were extended to the finding kinds it actually
  emits; a hypothesis still carries its source finding and task.
- `--suggest AGENT=TEXT` lets a person put their own hypothesis first —
  tested by the same rule, recorded with `source: seed`.
- `--resume` continues from `loop.json` without re-running anything.

## What I would do next

- Judge-graded tasks: let `judge` grade runs of tasks without an expected
  answer inside the loop, with `graded_by: "model"` carried into every
  rate the loop reports.
- Tool and model variants as hypotheses, not only prompt sentences: the
  same paired experiment applies to "same agent, cheaper model" or "same
  agent, one tool removed".
- A held-out slice: keep a family out of every experiment and report the
  kept prompt's success on it, so the loop's own selection cannot
  flatter the number it reports.
