# Live guards for Claude Code

`agentdiff self-evolve` finds a failure after a run and changes the
agent's instructions for the next run. A guard acts during the run, through Claude
Code's hooks. It sees each tool call before it runs and each result
after it. It can refuse a call, or refuse to let the turn end, and it
gives a reason the agent reads and acts on.

```bash
cd my-project
agentdiff guard --install                       # guard every Claude Code session here
agentdiff guard --install --check "pytest -q"   # ... held to a check you name
agentdiff guard --status                        # what the guards refused, by guard
agentdiff guard --replay runs/                  # before installing: what they would have refused in recorded runs
agentdiff hub .                                 # the sessions, live, and the refusals on the Overview
agentdiff guard --uninstall                     # take them out again
```

`--install` merges hooks into `.claude/settings.local.json`, the
project's local settings, which git ignores. It keeps every hook that
was already there and never installs its own twice. `--uninstall`
removes only its own hooks. The hook command names the Python and the
agentdiff it was installed from, so it works from a clone too.

## The guards

Each guard is the live form of a remedy `self-evolve` applies after the
fact (`docs/SELF_EVOLVE.md`).

| guard | when | what the agent is told | remedy |
|---|---|---|---|
| `repeat` | a shell command is about to run again after failing twice in a row, with nothing else done in between | the command, how often it failed, the line that says how (`1 failed, 3 passed`, the failing case), and to change something first | `repeated_call` |
| `check` | the agent is about to finish, and it changed files after the last check ran, or that check failed | to run `--check`, or the check found in the project, and read the result. Refused at most `--max-stop-blocks` times (2), never forever | `claims_without_check`, `no_check_after_last_edit` |
| `brief` | a session starts (`SessionStart`; `--no-brief` turns it off) | the project's check and to run it after the last edit; what the guards refused in earlier sessions here; how many recent sessions ended with their own last check failing. Never refuses anything | — |
| `tests` | an edit to one of the project's test files (`--protect-tests`, off unless asked). A test the agent writes for itself outside the project is let through | to fix the code, not the test that catches it | the `tests_edited` flag |

These are normal and never refused:
- running a failing test again after editing the code
- a command that passed, run any number of times
- finishing a turn that changed nothing

A check is any test, lint or type-check command (`pytest`, `unittest`,
`npm test`, `go test`, `cargo test`, `ruff`, `mypy`, `tsc` …), the
`--check` command, or any command whose output ends in a test runner's
verdict, however it was started (a script, a heredoc). A failure is what
Claude Code reports. A non-zero exit arrives as `PostToolUseFailure`,
with the output in `error`. Otherwise the runner's last verdict decides
(unittest's `OK` or `FAILED (…)`, pytest's `2 failed, 3 passed`). Only
when there is none is the output read for `Traceback`, `FAILED` and the
like. A run that prints a traceback from a test of an error path, then
reports `OK`, passed.

A guard decides from what the session did in this turn, that is, since
the person's last prompt. The person may have changed things between
prompts, so a new prompt starts clean. The state file is
`.agentdiff/guard/<session>.json`. A guard that cannot read its state,
or meets anything unexpected, lets the call through. A guard must never
be the reason an agent breaks.

## The project's check, found

With no `--check`, the guard looks for the check the project already
names, once per session, and keeps what it found in the session's state.
It reads files only and runs nothing. The first match wins:

| the project has | its check |
|---|---|
| `package.json` with a `test` script (not npm's "no test specified") | `npm test` |
| `Cargo.toml` | `cargo test` |
| `go.mod` | `go test ./...` |
| a Makefile with a `test:` target | `make test` |
| pytest configuration (`pytest.ini`, `conftest.py`, `[tool.pytest…]`, `[tool:pytest]`, tox `[pytest]`), or a test file that imports pytest | `python3 -m pytest -q` |
| `test_*.py` / `*_test.py` at the root | `python3 -m unittest -q` |
| them under `tests/` or `test/` | `python3 -m unittest discover -s tests -q` |

None found means no check is named. The `check` guard still sends an
unchecked finish back, to run the project's tests. `--install` prints
what it found. Scored against every task file in this repository that
names a check for its workspace (7 of 7, recorded), this repository's
own suite, and 15 project skeletons, one per row above and its edge
cases (synthetic, `tests/test_guard_brief.py`).

## What a session is told at start

The `brief` guard answers `SessionStart` with context the agent reads
before its first step. For example:

```
agentdiff guard, for this project:
- Check your work with `python3 -m pytest -q` (pytest.ini names it): run it after your last edit and read the result before you say the work is done.
- In 3 earlier session(s) here the guard refused a failing command rerun unchanged 2 time(s) (`pytest -q tests/test_x.py`): when a command fails, change something first; a finish with unchecked edits or a failing check 2 time(s).
- 1 of the last earlier sessions here ended with their own last check failing (`pytest -q`): start from what that check says.
```

It is facts from files: the check found, and counts from
`.agentdiff/guard/`. The current session's own refusals are never
counted as earlier ones. With nothing to say, it says nothing. It is at
most 1,200 characters.

Why say it at the start. In the recorded real runs that edited files
(`demo/vendors/` and `demo/selfevolve/examples/`, 100 runs), 38 finished
with no check after the last edit, and 10 of those failed (26%). Of the
62 that checked, 3 failed (5%), one-sided Fisher p = 0.003. Within the
9 tasks that have both kinds of run, 8 of 26 unchecked runs failed and
0 of 26 checked runs did. (Counted when a check was a command alone,
before test runs started any other way counted too: 10 of 46 against 3
of 54, p = 0.017.) That is an association in recorded runs, not
a measured effect of the brief. Its effect on real agents is not
measured here: that needs live sessions with and without it.

## Before installing: replay them over recorded runs

```bash
agentdiff guard --replay .agentdiff/traces/ runs/        # what each guard would have refused
agentdiff guard --replay runs/ --protect-tests --json    # every run, every refusal
agentdiff guard --replay runs/ --html replay.html        # the same, as a page
```

`--replay` puts each recorded trace's tool calls through the same rules,
in order, with the state in memory, then puts its finish to the `check`
guard. Nothing is written. For each guard it counts the runs it would
have stepped into among the runs that failed (what it catches) and the
runs that passed (what it costs), each with its denominator, and names
the first few. Ungraded runs are counted apart. The `tests` guard is
replayed only on runs whose trace says where the project was (Claude
Code prints it); the line says how many those are.

It reads where a guard would have stepped in, not what the agent would
have done next. A run sent back might have gone on to pass, or to fail
the same way.

On the recorded real runs here (106 runs: 16 failed, 90 passed):

| guard | failing runs it steps into | passing runs it steps into |
|---|---|---|
| `check` | 13 of 16 | 30 of 90 |
| `repeat` | 0 of 16 | 0 of 90 |
| `tests` (30 runs with the project known) | 0 of 2 | 0 of 28 |

- **`check`:** of the 13 failing runs, 10 finished with edits no check
  followed, and 3 with their own last check failing. Those 3 are all on
  the suite's contradictory task, which no code can pass. Its honest
  finish is a failed check and a message saying why, so sending it back
  is a cost too: the stop limit (`--max-stop-blocks`) is what lets it
  end. The 3 failures it misses edited nothing.
- **Its cost:** 30 passing runs would be sent back once to run a check.
  The first replay counted 41. Of those 11, 9 had run their tests some
  other way (a script or a heredoc) that the guard did not yet count as a
  check. In 2, a traceback printed before `OK` was read as a failure.
  Both were fixed in the live guard.
- **`tests`:** the first replay had it refuse 7 passing runs. Each was
  the agent editing a test it wrote for itself in its scratch folder,
  outside the project. It now protects only the project's tests.
- **`repeat`:** no recorded run reran a failing command unchanged (as
  run 29 found).

### The page, and the rules an operator could choose

`--html FILE` writes the replay as one page, with no script. It has three
drawings, each from the replay's own counts:
- **Every run as a thread.** All the runs leave one bundle, split by
  their own grade (failed or passed), then by the `check` guard's call
  (sent back or let finish). A thread is red where the guard steps in,
  so where the red goes is its catch and its cost.
- **Every run filed where it went.** The same four outcomes as a table,
  each cell filled with its runs, one small strip per run: a mark per
  tool call (edit, check passed, check failed, anything else) and a red
  rule where the guard steps in.
- **Each rule an operator could choose, as a point.** Each point is
  placed by the failing runs it catches (up) against the passing runs it
  sends back (across), with exact 95% intervals as a cross. The diagonal
  is a guard that sends runs back at random. The rules are replayed
  against the same runs, beside the one installed
  (`guard.replay_variants`).

On the recorded runs here:

| rule | failing runs it steps into | passing runs it sends back |
|---|---|---|
| `check`, installed: edits no check followed, or a failing last check | 13 of 16 (54–96%) | 30 of 90 (24–44%) |
| `check`: edits no check followed, only | 10 of 16 (35–85%) | 28 of 90 (22–42%) |
| `check`: a failing last check, only | 3 of 16 (4–46%) | 2 of 90 (0–8%) |
| `repeat` after 1, 2 (installed) or 3 failures | 0 of 16 | 0 of 90 (0–4%) |

No recorded run reran a failing command unchanged, at any threshold. The
first page drawn showed 4 passing runs refused after one failure. That
was two mistakes, both fixed:
- **In the comparison:** one trace id names runs in several example
  folders, and the variants matched runs by id, so one run counted 4
  times. Runs are now told apart by their place in the replay.
- **In the live guard:** it compared only a command's first 400
  characters. The agent had rewritten the end of a long heredoc script,
  and the guard took the new script for the old one rerun. A long
  command is now compared in full.

## What it records

- **Every refusal**: `.agentdiff/guard/log.jsonl`, one line
  `{t, session, guard, reason, call}`. `--status` counts them by guard
  and shows the latest. The hub's Overview lists them per project,
  under **Guards · refused while the agent worked**, each linked to its
  session's trace.
- **Every session** (unless `--no-traces`), traced live into
  `.agentdiff/traces/<project>-<sid8>__claude-code.json`, one file per
  session. The hub follows it while it runs and draws it when it ends:
  its trunk, its laps, its code, where to look.
  - A stop the guard refused does not end the trace.
  - A session with no expected answer would be ungraded. If a check ran
    after the agent's last edit, the session is graded by that check
    instead, and the trace says so in `outcome.note` and
    `harness.graded_by`. With no check after the last edit, it stays
    ungraded.

## Verified with Claude Code

Two real `claude -p` sessions on a three-test `slugify` task, guards
installed with `--check "python3 -m unittest"`:

- **Told to "fix it with one edit and reply 'done' — no need to run
  anything".** The agent edited and tried to finish. The `check` guard
  refused the stop. The agent ran the tests (3 passed) and finished.
  The trace is graded *passed* by that check.
- **Told to run the failing tests three times to see whether they are
  flaky, and to fix nothing.** The third identical run was refused. The
  agent reported both failures and said a hook had stopped the third
  run, without rephrasing the command to get around it. It fixed
  nothing, as asked, and nothing was refused at its stop, since it made
  no edit.

That second case is the guard's known cost. Re-running a test that is
really flaky is refused after two failures. The agent is told why, and
can change the command (`-p no:randomly`, a single test) or say so.
