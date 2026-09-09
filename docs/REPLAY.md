# Predictable replay: reproducing an agent run, and debugging one

An agent run has two sources of non-determinism: what the model said,
and what the world answered when the agent called a tool. Production
agent systems converge on one discipline for both — record every
non-deterministic input, then replay with the inputs served from the
recording, so the only thing that can vary is the thing under test.
Temporal enforces it on workflow histories (a replay that diverges
raises a non-determinism error); VCR-style cassettes do it for HTTP;
Docker's cagent and langchain-replay do it for agents; Langfuse and
LangSmith run the recorded datasets as CI gates. AgentDiff adopts the
same discipline on SCHEMA traces, offline, with no model and no network
in the loop unless one is named.

## What a trace already is

A SCHEMA trace records every step's input and output: the model's words
for a thinking step, the call and the tool's result for a tool step,
the answer and the grader's verdict at the end. That is a complete
record of both non-deterministic inputs. Everything below is derived
from it and nothing else.

## The cassette

`deepcompare.harness.cassette.Cassette.from_trace(trace)` indexes every
tool-ish step (`search`, `retrieve`, `read`, `tool_call`) by the call as
the agent made it — the tool's name and its arguments, canonicalised —
with the results in the order they were observed. A repeated call
replays each result in turn. `cassette.tools()` returns harness `Tool`s
that answer from it; no tool code runs.

A call the recording never made is a **miss**, and a miss is the
signal: on a self-replay it means the recording cannot be reproduced;
with a different model it is exactly where that model departed. The
policy says what the agent is told — `strict` (an error naming the
miss), `empty`, or `live` (the declared tool runs for real; the one
policy that leaves the hermetic boundary, chosen explicitly). Every
miss is kept for the report.

## `rerun`: does the recording reproduce?

```
agentdiff rerun demo/traces -o out/rerun --junit --job-summary --github-annotations
```

Replays every trace from its own recording: the model's turns verbatim,
the world's answers from the cassette, through the same recorder and
grader a live run uses, and diffs the result step for step against the
recording. A step matches when its family (think / tool / answer), its
name, its call and its output agree; tokens, latency and cost are
measurements and are not compared. The self-replay keeps the recorded
verdict for the recorded answer, since the verdict is part of the
recording (a judge's or a person's as much as the grader's).

A reproduced trace is a fixture: every non-deterministic input it needed
is in the file, so a CI job can replay it forever, and a schema or
recorder change that stops reproducing it fails the build with the step
named. Exit 1 on any drift (`--no-fail-on-drift` to report only); one
JUnit testcase per trace; a Markdown summary; `::error` annotations. The
artifacts carry no timestamps, so two runs over one input produce
identical bytes.

## `rerun --provider`: a different model in the recorded world

```
agentdiff rerun traces/t07__atlas-v2.json --provider next=openai:gpt-5 --tools mytools:TOOLS -o out/rerun-next --traces
```

Drives the named model through the run with the world frozen: every
tool call is served from the cassette, so the first miss is the first
step where the new model made a call the old one never made, and the
diff lists every step that differs before and after it. No live tool
ran. The replayed trace is written beside the result and aligns against
the original like any pair (`agentdiff compare original.json
out/rerun-next/traces/…json`), so the change of model reads like a
change of agent. A scripted provider (`scripted:turns.json`) makes the
same check run offline in tests.

The new answer is graded against `task.expected` when there is one;
without one the recording's words keep its verdict and anything else
counts as the opposite — a weak proxy the result labels, to be judged
properly with `agentdiff judge`.

## `context`: what the model saw

```
agentdiff context traces/t07__atlas-v2.json --step 9
agentdiff context out/report_t07.json --row 6            # both runs at the aligned row, and their diff
agentdiff context a.json --step 9 --against b.json --against-step 7
```

Rebuilds, deterministically, the message list the model had in front of
it before a step: the system prompt, the task, every earlier thinking
step as the assistant's words, every tool step as the call and its
result. For a report and an alignment row it prints both runs' contexts
and a unified diff, so the difference in what each model saw stands
next to the difference in what each did. It is reconstruction and says
so: the provider's own framing of these messages is not recorded.

## In the pipeline

`.github/workflows/agentdiff.yml` is the project's own pipeline and the
shape of one for any agent repository. Every job is hermetic:

1. **engine tests** — the analysis suite.
2. **predictable replay** — `rerun` over the shipped traces (JUnit,
   summary, annotations); the report is a pure function of the traces
   (two `batch` runs, one byte sequence, `diff -r`); the candidate agent
   gated against the baseline (`gate`, with SARIF to code scanning).
3. **page** — the browser suite over the built page.

For a repository that runs a real agent, the recording step comes
first: `agentdiff run` (or the Recorder in your own loop) writes traces
in CI with a scripted or a live provider; `rerun` then proves those
traces replay; `gate` compares them to the baseline's; `check` compares
them to golden trajectories. A model upgrade is `rerun --provider` over
the baseline traces before anything live runs.

## What this is not

Counterfactual replay (`agentdiff replay`) re-executes a run from a
corrected step with a live model, several times, to test whether the
decisive step was decisive; it is stochastic by design and reports a
rate. `rerun` is the opposite: deterministic, one pass, no correction —
a reproduction and a diff. The two share the recorder, the cassette's
tools, and the drive loop.
