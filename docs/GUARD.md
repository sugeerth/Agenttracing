# Live guards for Claude Code

`agentdiff self-evolve` finds a failure after a run and changes the
agent's instructions for the next run. A guard acts during the run, through Claude
Code's hooks. It sees each tool call before it runs and each result
after it. It can refuse a call, or refuse to let the turn end, and it
gives a reason the agent reads and acts on.

```bash
cd my-project
agentdiff guard --install --check "pytest -q"   # guard every Claude Code session here
agentdiff guard --status                        # what the guards refused, by guard
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
| `check` | the agent is about to finish, and it changed files after the last check ran, or that check failed | to run `--check` (or the project's tests) and read the result. Refused at most `--max-stop-blocks` times (2), never forever | `claims_without_check`, `no_check_after_last_edit` |
| `tests` | an edit to a test file (`--protect-tests`, off unless asked) | to fix the code, not the test that catches it | the `tests_edited` flag |

These are normal and never refused:
- running a failing test again after editing the code
- a command that passed, run any number of times
- finishing a turn that changed nothing

A check is any test, lint or type-check command (`pytest`, `unittest`,
`npm test`, `go test`, `cargo test`, `ruff`, `mypy`, `tsc` …), or the
`--check` command. A failure is what Claude Code reports. A non-zero
exit arrives as `PostToolUseFailure`, with the output in `error`.
Otherwise the output is read (`2 failed`, `Traceback`, `FAILED`).

A guard decides from what the session did in this turn, that is, since
the person's last prompt. The person may have changed things between
prompts, so a new prompt starts clean. The state file is
`.agentdiff/guard/<session>.json`. A guard that cannot read its state,
or meets anything unexpected, lets the call through. A guard must never
be the reason an agent breaks.

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
