# Codex CLI and Claude Code on the same task

`agentdiff duel` gives one task to OpenAI's Codex CLI and Anthropic's Claude
Code. Each agent gets its own copy of the same workspace, both are launched
together, and every event either CLI prints is kept. Then it answers four
questions in order:

1. **Was it fair?** The parity ledger lists every condition that should
   have been equal and whether it was.
2. **Did the work pass?** Your check command grades each run after the
   agent stops.
3. **What did each one make, and what did it cost?** The diff, tokens
   split into cached input, fresh input and output, cost where the vendor
   reports one, and wall time.
4. **How did each one work?** Explore, edit, verify and run, how much it
   looked before its first edit, whether it checked after its last edit,
   and whether it said it was done when the check says it was not.

This is a qualitative reading, not a benchmark. A few tasks and a few runs
show how two agents worked on your work. The intervals show how little a
pass rate from a small sample settles.

## The one command

```bash
cd your-project
uvx --from git+https://github.com/sugeerth/Agenttracing agentdiff "Fix the failing test in parser.py"
```

Or install it once (`pip install git+https://github.com/sugeerth/Agenttracing`)
and type `agentdiff "…"`. To choose the two agents, name the models:
`--agent opus --agent gpt-5` (a model names its vendor: `haiku`, `sonnet`,
`opus`, `claude-…` run on Claude Code; `gpt-…`, `o3`, `codex-…` on Codex).
`agentdiff open` reopens the newest duel's page.

With no task in mind, `agentdiff fix` writes it: it finds the test
command, runs it once on an untouched copy, and, if it fails, gives both
agents the same task. That task is to make it pass without editing, skipping
or deleting tests, with the last 40 lines of the failing output to start
from. If it already passes, there is nothing to fix and no agent starts.

Every duel runs its check once before any work, on an untouched copy,
and records it as the run's `baseline`. A task whose check already
passes measures nothing: a pass there is no evidence the agent did
anything. The terminal, `DUEL.md` and `duel.json` (`baseline.already_passing`)
all say so. `--no-baseline` skips it.

The terminal ends on a scoreboard, one row per agent (passed, median
tokens, cost, median time). Below it are only the lines that change how
the rows read: a difference in passes, a claim of done that failed the
check, tests edited, conditions that were not equal, a cost the CLI does
not report. It names no winner beyond the counts; `DUEL.md` has the rest.

That is the whole setup. `agentdiff` alone lists which coding-agent CLIs
are ready on this machine and, for any that is not, the command that
installs it or logs it in. A sentence in place of a command is the task;
a single word is still a command, so a typo never starts two agents. At
a terminal, `agentdiff duel` with no task asks for one. The agents work on copies of the directory you
are in. Each run is graded by the project's own test command, read from
its files and never run to find out: a `test` target in the Makefile, a
`test` script in package.json, Cargo, Go, or Python tests (pytest when it
is installed). The command prints the check it chose; `--check CMD`
changes it and `--no-check` records the runs ungraded. The agents are
the CLIs installed: Codex and Claude Code, or, with Claude Code alone,
its haiku and sonnet models. In a terminal the race opens in your browser
as they work.

Each duel writes to `duel-out/`, or `duel-out-2/` and on when an earlier
duel is there, so two duels are never read as one. The directory ignores
itself (a `.gitignore` of `*` inside it), so it never shows in the
project's `git status`. An output directory
is never copied into an agent's workspace, so running inside the project
does not hand the next duel's agents the last one's reports. The full
triage is in `page/triage.txt`. Everything below is for when you want more control.

## Setup

Install both CLIs (`npm i -g @openai/codex`, and Claude Code), then set the
keys in the environment:

```bash
export OPENAI_API_KEY=...        # or CODEX_API_KEY; or `codex login`
export ANTHROPIC_API_KEY=...     # or log in with `claude`
agentdiff duel --dry-run --prompt x --workspace .   # both CLIs found? a credential present?
```

The keys only ever reach the vendor processes. Nothing writes them, and
the check command runs with them removed from its environment. Any key
value that turns up in a stream, an error or a check's output is replaced
with `[redacted]` before it is written. A test runs a stand-in agent that
prints its key and asserts that nothing under the output directory
contains it.

## Run

```bash
agentdiff duel --task demo/vendors/task.json --runs 3 -o duel-out/
agentdiff duel --prompt "Fix the failing test in parser.py" --workspace ./repo \
               --check "pytest -q" --budget-tokens 400000 -o duel-out/
agentdiff duel --agent fast=codex:MODEL_A --agent deep=codex:MODEL_B ...   # one vendor, two models
agentdiff duel --task demo/vendors/task.json --live       # serve the race while they work
```

`--live` serves the page on `http://127.0.0.1:8765/` (`--port`, `--host`)
while the agents run. Each agent's lane grows as its stream arrives,
about four times a second: every action is a mark at the second it
began, as wide as it took, coloured by what it was (explore, edit,
verify, run). Under each lane is the tokens spent so far, and a pulsing
line marks *now* on a run still going. When a run finishes, its lane
takes the check's verdict. Claude Code reports usage per message, so its
token line grows as it spends. Codex reports usage once, when the turn
ends, so its line stays flat and then steps to the reported total. The
page says so rather than drawing a curve Codex never reported.

The rest of the page fills in as runs finish, not only at the end. Each
finished run re-runs the whole analysis over the runs done so far: the
fair report, the verdict, the scorecard, the lessons, the eval forge.
Until both agents have finished a task, the page says what it is waiting
for. With `--runs 2` or more, each round streams in the same two lanes,
and the title says which run is on screen. When the last run finishes, the live page's analysis is the written
page's, reading for reading; `LiveAnalyticsTest` holds the two to that.

Opened from a file, the same race is a replay. A scrubber shows both
agents at the same second, with a line saying what each was doing and
what it had spent. Play runs the clock forward, compressed.

A task file holds `{id, prompt, workspace, check}`, or `{"tasks": [...]}`
for several tasks. The source workspace is never modified: each run works
in a temporary copy, and the difference between the copy's before and
after snapshots is that run's diff.

Output in `duel-out/`:

| path | what |
|---|---|
| `DUEL.md`, `duel.json` | the fair report |
| `traces/` | one SCHEMA trace per run; everything else in AgentDiff reads these |
| `raw/` | every line each CLI printed, stamped with its arrival time (re-convertible) |
| `diffs/` | each run's patch |
| `records/` | what the harness saw: the check, the diff summary, the setup, why a run stopped |
| `page/report.html` | the full page: the duel first, then the verdict, the strip, the process checks |

`--from duel-out/` rebuilds the report and the page from the records
without running anything.

## The agent under test is not part of the evaluator

When the harness itself runs inside an agent (Claude Code, say), the
vendor CLI it starts would inherit that agent's session: its session id,
its messaging socket and token, its remote tools. A live run found
exactly that: the Claude Code under test reported the host session's id
and was offered the host's notification, messaging and artifact tools.
Now:

- Every vendor process starts without the host session's variables
  (`harness.vendors.HOST_SESSION_PREFIXES`). Authentication does not
  need them; the same live run succeeded with all of them removed.
- Claude Code is offered its coding tools only (`CLAUDE_TOOLS`), with
  `--strict-mcp-config`.
- The ledger has a row for **the tools on offer**, so a Codex run and a
  Claude Code run are not presented as having had the same instruments.

Restricting the tools also changed the spend, and the ledger is why the
change is visible. Tool definitions are context: Sonnet's tokens per run
on the demo bug fell from 193k with the host's tools to about 112k with
its own.

A credential can also be an endpoint the host configured
(`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`: a gateway or a managed
provider). The preflight says so, rather than reporting "none" and
refusing to start.

## A recorded live run

`demo/vendors/live/` holds a real duel: Claude Code 2.1.283 on Haiku 4.5
and on Sonnet 5, two runs each, all four passing the check. Its raw
streams are the converter's fixture. `tests/test_vendors.py` re-converts
them and requires the committed traces back, with no event type unknown.
`agentdiff duel --from demo/vendors/live` rebuilds the report without
running anything.

## A suite built to show behaviour

`demo/vendors/suite/suite.json` has six small tasks, each built to show
one thing an agent does:

| task | what it shows |
|---|---|
| bugfix-pricing | a bug fix |
| feature-slugify | a feature built from a spec |
| perf-dedupe | a fix under a timing check |
| two-file-duration | a bug whose failing test and cause are in different files |
| guarded-inventory | the tests are the spec |
| contradictory-rounding | an impossible task: no implementation satisfies both tests |

Every check runs the tests and refuses any run that changed the test
file (it compares the test file's hash). `SuiteTasksAreWellFormedTest`
proves that every check fails before any work, passes with a reference
fix kept outside the workspace (except the impossible one), and refuses
a run that edits the tests.

The impossible task is the interesting one. The report counts two
opposite behaviours:

- **said it was done and failed the check**: the case a reader most
  needs to be told about
- **failed the check and said what stopped it**: what an agent should
  do when a task cannot be done

A claim of done is read sentence by sentence. A sentence that negates or
hedges ("it's impossible to make all tests pass") is not a claim. A live
run found that distinction the hard way.

`demo/vendors/live-suite/` is a real run of the suite, one run each on
Haiku 4.5 and Sonnet 5. Both passed 5 of 6. On the impossible task
neither changed a file, and both said why. Haiku cost about half as much;
Sonnet was faster and read more.

## What is and is not equal

The ledger is not a formality. Here is what differs by default, and why
it stays visible rather than being hidden:

- **The sandbox.** Codex runs commands inside its own `workspace-write`
  sandbox, with network off for commands. Claude Code runs with
  `acceptEdits` and Bash allowed, in a temporary copy but with no OS
  sandbox. An agent that is refused an action is not the same agent as
  one that is allowed it.
- **How the budget is enforced.** Claude Code reports usage per message,
  so a `--budget-tokens` limit stops it while it works. Codex reports
  usage once per turn, so its budget can only be checked afterwards. A
  Codex run that went over is marked `over_budget` and says it could not
  be stopped.
- **Cost reporting.** Claude Code reports what it spent; Codex does not.
  The page says *not reported* rather than printing zero dollars.
  `--price codex=IN,CACHED,OUT` (USD per million tokens) computes a cost
  for it, labelled as the prices you gave.
- **The models** are named, not judged. Different vendors run different
  models by design.

Equal by construction: the prompt (hashed), the starting workspace
(hashed), the check, the budget, the time limit, and the operator's own
vendor settings. Those are left out by default (`--ignore-user-config`
for Codex, `--setting-sources project,local` for Claude Code), so the
agents measured are the vendors' defaults, not a customised setup;
`--use-my-config` loads them. The agents are also launched side by side,
so vendor latency and rate limits move for both at once.

## The budget band

Two runs of one task are **budget-matched** when their token totals are
within `--band` (10% by default) of the larger. When they are not, the
report says so and gives passing runs per million tokens alongside the
pass rate. An agent that passes as often on a third of the tokens is the
better buy, and a per-run comparison would hide that.

## How the streams are read

`agentdiff/vendors.py` converts both CLIs' streams, and
`agentdiff convert` detects either one in a saved file.

- **Codex** (`codex exec --json`): `thread.started`, `turn.started`,
  `item.started`/`item.completed` around each `agent_message`,
  `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`,
  `web_search`, `todo_list`, `collab_tool_call` and `error`, then
  `turn.completed` with `usage` (`input_tokens`, `cached_input_tokens`,
  `cache_write_input_tokens`, `output_tokens`,
  `reasoning_output_tokens`). These names were read from the 0.157
  binary. An event this reader does not know is counted in
  `source.unknown_events`, never dropped silently.
- **Claude Code** (`claude -p --output-format stream-json --verbose`):
  `system`/`init`, `assistant` messages with `text`, `thinking` and
  `tool_use` blocks, `user` messages with `tool_result` blocks, and a
  closing `result` with `total_cost_usd`, `duration_ms`, `num_turns`,
  `usage` and `permission_denials`. One message can arrive as several
  lines that share a `message.id` and one `usage`; it is counted once. A
  message from a sub-agent (`parent_tool_use_id`) carries that span.

A step's duration is measured from the stamps: a command from its start
to its completion, a message from the event before it. Claude Code's
steps carry measured token counts. Codex's steps carry estimates labelled
as estimates, because it reports usage per turn, and only its totals are
measured. `token_accounting` records which is which.

## Testing it

- `tests/test_vendors.py` runs the whole harness against two stand-in
  CLIs (`tests/fixtures/vendors/`). They print each vendor's stream shape
  and really edit the workspace. They are test doubles, not the vendors,
  and never presented as them.
- `AGENTDIFF_LIVE=1 python -m pytest tests/test_vendors.py -k Live` runs
  the real CLIs on the demo bug, with your keys. It costs a few cents on
  each account and asserts only what must hold whatever the models do:
  both ran, both were traced with measured usage, both were graded by
  the check, and no key was written anywhere.
