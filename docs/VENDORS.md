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
agentdiff watch duel-out/traces          # both agents live, while they work
```

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

`deepcompare/vendors.py` converts both CLIs' streams, and
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
