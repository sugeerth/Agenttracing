# Running this in production

This file is about the deployment, not the analysis: where traces come
from, where the engine runs, what it costs, what it must never do, and
which of four shapes fits the job. Every number here was measured on
this machine against the shipped corpus and is reproducible with the
command beside it.

## The shape of the thing, and why it deploys well

Four layers, and the boundary between them is the deployment story.

| layer | what it is | what it may do | ships as |
|---|---|---|---|
| **engine** (`deepcompare/*.py`) | every analysis | pure stdlib; **no socket, no clock, no randomness without a seed** | a wheel |
| **harness** (`deepcompare/harness/`) | the only modules that talk to a model or open a port | network, credentials from the environment | the same wheel, imported only by the commands that need it |
| **page** (`web/blocks/*.js` → one HTML file) | every view | reads inlined JSON; loads nothing | package data |
| **bundle** (`agentdiff bundle`) | a content-addressed directory of outputs | nothing; it is data | a directory, a hash, a key |

Three properties fall out of that split, and they are the reason this
deploys more easily than a service:

- **The analysis is a pure function.** Same traces in, same bytes out —
  `tests/test_blocks_build.py` and the bundle's id both rest on it. So
  the engine can run anywhere: a CI runner, a cron box, a laptop, a
  Lambda. There is no state to migrate and no server to keep up.
- **The page is a file.** No API for the UI to call, no CORS, no auth
  layer to get wrong. It opens from disk, from S3, from a static
  bucket, from an email attachment.
- **The untrusted thing is data, not code.** Traces come from agents
  and carry model output verbatim. The engine reads them with no
  network available to it, enforced by an AST test
  (`tests/test_harness.py::TestNetworkBoundary`). A prompt injection in
  a tool result has nothing to reach.

## Where the traces come from

Three paths, in the order most teams take them:

1. **Instrument the agent** (`deepcompare.record.Recorder`). The loop
   calls `r.step(...)`, `r.tool(...)`, `r.answer(...)`; the trace is a
   JSON file matching `SCHEMA.md`. This gives the richest trace,
   because only the agent knows its own reward, its value estimate, its
   declared effects and which sub-agent is acting.
2. **Convert what you already emit** (`agentdiff convert`,
   `deepcompare/adapters.py`). OpenTelemetry spans, LangChain and the
   other formats in `docs/FRAMEWORKS.md` map onto the schema. You lose
   whatever your framework never recorded — usually rewards, effects
   and token basis — and the engine says `measurable: false` with the
   reason rather than inventing them.
3. **Hook a coding agent** (`agentdiff hook`). Live steps on each tool
   use, the finished trace on stop.

**Grade honestly at the source.** `outcome.success` is the grader's
verdict and the engine refuses to guess it. A task with no expected
answer and no grader is refused up front, because an ungraded run
silently entering a success rate is the one dishonesty that poisons
everything downstream.

## Four deployment shapes

### 1. A gate in CI (start here)

`gate` takes **two directories of traces** — the baseline agent's and the
candidate's — and compares them; it does not read an `aggregate.json`.
Split whatever your CI produced into the two, then:

```bash
agentdiff gate baseline/ candidate/ -o out/gate \
  --max-success-drop 0 --max-cost-increase 0.10 --max-latency-increase 0.25 \
  --junit --sarif --job-summary --github-annotations
```

It exits non-zero when the success rate drops further than
`--max-success-drop`, when mean cost or latency rise by more than their
relative thresholds, or when a failure-origin category appears that the
baseline never had (`--allow-new-failure-modes` turns that last one off);
`--fail-on never|regression|pathology|any` sets which severity actually
fails the build, so a first rollout can report without blocking. The same
run writes JUnit XML, SARIF for code scanning, and a job summary.

For a self-evolving agent the equivalent is `agentdiff evolve <lineage>
--fail-on gamed,protected` — any verdict or flag in the list makes it exit
1. `.github/workflows/agentdiff.yml` in this repository is a working copy
of both. Run it on the pull request that changes a prompt, a tool or a
model. Cost below.

### 2. A nightly read of a trace store

Point `runs` or `evolve` at a directory or the trace database
(`agentdiff db --db traces.sqlite import …`), then export:

```bash
agentdiff runs traces/ -o out --token-cap 4000
agentdiff grafana out -o /var/lib/node_exporter/textfile_collector
```

Seven provisioned dashboards read the exposition; `grafana/docker-compose.yml`
stands the whole thing up. Nothing in AgentDiff serves metrics — it
writes a file and a node exporter does the serving.

### 3. A bundle, a key, and an assistant (the newest, and the one to prefer for people)

```bash
agentdiff demo --everything -o out          # or bundle <outputs…> --traces <dirs…>
```

One content-addressed directory with three levels of grain, the page,
and `KEY.txt`. The key is one paste-safe line under two kilobytes that
*carries the overview itself* and names the bundle by its hash, so a
reader learns what ran without fetching anything, and a tool that has
the bundle verifies it. Then either:

```jsonc
// the assistant reads every level itself, over stdio, no socket
{"mcpServers": {"agentdiff": {"command": "python", "args": ["-m", "deepcompare", "mcp", "--bundle", "/path/to/bundle"]}}}
```

```bash
agentdiff serve --bundle out/bundle        # read-only, localhost, /api/v1/...
```

This is the shape to reach for when the audience is a person or an
assistant rather than a threshold: no service to run, the artifact is
the API, and the hash makes it citable.

### 4. Live

`agentdiff watch traces/` serves the page with server-sent events as
runs land. For a demo or a war room, not for a fleet.

## What it costs

Measured on this machine (single core, CPython 3.11), reproduce with
`python3 -m deepcompare <cmd>`:

| command | corpus | wall clock | peak RSS | output |
|---|---|---|---|---|
| `batch` | 16 traces / 8 pairs | 0.48 s | 59 MB | 6.2 MB (page 4.1 MB) |
| `runs` | 96 traces, 2 policies | 14.4 s | 100 MB | 14.0 MB (page 6.5 MB) |
| `coevolve` | 210 traces, 7 generations | 10.6 s | 108 MB | 13.7 MB (page 6.4 MB) |
| `evolve --against` | 420 traces, 2 lineages | 20.0 s | 122 MB | 14.8 MB (page 6.8 MB) |
| `demo --everything` | all of the above, plus the bundle | 37.3 s | 287 MB | 104 MB |

Two readings of that table. **Pair work is cheap** — half a second for
eight pairs — so a CI gate is free. **The statistics are what cost**:
the bootstrap intervals dominate `runs`, `evolve` and `coevolve`, and
they scale with `--samples` (2,000 by default), so a nightly job that
wants to be quick takes `--samples 500` and says so. Memory is flat at
about 120 MB because the engine streams traces per task rather than
holding the corpus.

Storage: the shipped corpus is **507 bytes per step** on disk (11,569
steps in 5.9 MB), with input and output text included. An agent that
runs 10,000 episodes a day at 30 steps each writes about **150 MB a
day** raw. Gzip takes roughly a fifth of that.

The page is the one number to watch: **4 to 7 MB**, because every
analysis is inlined so the file works from disk with nothing to fetch.
That is a deliberate trade. A bundle of three members with all 322 runs
at full depth is about 67 MB, which is a download, not a web page —
serve it (`agentdiff serve`) rather than emailing it.

## Sampling and retention

Do not analyse everything, and do not throw away the interesting runs:

- **Keep every failure, every flagged run, and a fixed fraction of
  passes.** The analyses that matter (attribution, the reward audit,
  the lineage checks) live on failures; passes are for the denominator,
  and a fraction of them with a stated sampling rate keeps the rates
  honest.
- **Never sample within a task.** The statistics stratify by task and
  resample runs within it; dropping runs unevenly across tasks biases
  every interval. Sample tasks, or sample whole runs uniformly, and
  record the rate.
- **Retention: raw traces short, outputs long.** The outputs
  (`aggregate.json`, the reports, a bundle) are two orders smaller than
  the traces and carry the readings; keep them. Keep raw traces for the
  window in which you might re-run an analysis — thirty days is
  typical — plus every trace a bundle references, because
  `bundle --traces` is what makes a run's level three possible.

## Privacy: the thing to decide before the first deployment

**A trace carries the prompt, every tool input and output, and the
answer, verbatim.** That is what makes the provenance, the corpus diff
and the search map possible, and it means a trace store is as sensitive
as the data the agent touched. Decide, before you turn recording on:

- **Redact at the recorder, not after.** The `Recorder` is where a
  field passes through; a redaction there never reaches disk. Redacting
  a trace store afterwards is a migration, not a policy.
- **What you can drop cheaply:** `input`/`output` text costs most of
  the bytes and is needed only for the Data view's provenance, the
  chain and the step text. Drop it and everything counting still works
  (`budget`, `fetches` counts, the statistics), and the engine says the
  text is not there rather than pretending.
- **Credentials never belong in a trace.** The harness reads keys from
  environment variables at call time and no error path prints them
  (`tests/test_chat_cli.py` asserts it). Your tools must hold the same
  line.
- **The page inlines the data.** A report page is the trace store in a
  file. Treat a shared page like the traces behind it.

## What must stay true in production

- **No network in the engine.** Enforced by an AST test. Keep it: it is
  what lets you analyse untrusted traces.
- **A model may phrase, never number.** `narrate`, `why` and `chat`
  pass model output through a check against the brief's numbers and
  print the violations rather than dropping them; no analysis reads
  narration; the gate's exit code is computed before it exists.
- **Determinism is an operational feature, not a purity test.** It is
  why a bundle's id is a cache key, why two analyses can be diffed, and
  why a disagreement between two runs of the tool is a bug rather than
  noise.
- **`measurable: false` with a reason beats a number.** In production
  this matters most: a dashboard that renders zero where nothing was
  recorded will be believed.

## Failure modes, and what happens

| what goes wrong | what the system does |
|---|---|
| a trace fails schema validation | it is skipped with the reason on stderr; the rest of the batch still runs |
| a section raises | that key becomes `unmeasurable("<exception>")` and every other section still attaches (`deepcompare/sections.py`) |
| a provider call fails | `ProviderError` → the run records `infrastructure_error` and is excluded from the agent's reliability statistics |
| too few runs to say anything | the runs advisory says so and the intervals stay wide; nothing is asserted |
| the grader was fooled | **nothing catches it** — stated in every affected section's gap sentence |

## What not to do

- **Do not run the engine as a request-path service.** It is a batch
  function; a 20-second bootstrap does not belong in a request.
- **Do not put the page behind an auth-less URL** and assume it is a
  dashboard. It is the data.
- **Do not let a model set a number, a verdict or an exit code.** The
  seam exists and is one import deep; keeping it is the whole claim.
- **Do not compare across a schema change** without saying so. The
  bundle's id changes when the bytes change, which is the signal.

## The gap that is still open

An installed wheel carries the engine and the page but not the demo
corpus, so `agentdiff demo` needs the repository; the command says so
and names what to run instead. Everything else — every command, the
page, the MCP server, the HTTP API — works from `pip install agentdiff`,
and `tests/test_packaging.py` builds the wheel and reads it to keep that
true.
