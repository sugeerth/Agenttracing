# The hub

`agentdiff hub` is AgentDiff as a small platform. It is one process with
no database, standard library only, behind a sign-in. It shows everything
under a directory, and follows it while it runs:
- every trace, drawn as the loop the agent went round, lap by lap
- running agents, live, as their traces grow on disk or arrive in-band
- agents under a harness that evolves with its evals (`docs/SELF_EVOLVE.md`)
- eval suites carried through generations (`docs/EVOLVING_EVALS.md`)
- duels, with their scoreboards, and reports
- in-band telemetry agents post to it (`docs/TELEMETRY.md`)
- guarded Claude Code sessions: each one traced, and every refusal the
  guards made on the Overview (`docs/GUARD.md`)
- every trace's views in tabs, opening on **Start here** (the verdict card),
  and at the bottom **Why it went this way** and **What to change in the
  agent**: the instruction to give it, the guard that enforces it, a cap
  when it went round for hours, and the self-evolve command that tests it
- every run in phases (sessions, bursts, loops over hours when it ran that
  long), with a lens that moves phase by phase, a view any trace can open
  (`docs/LONGRUN.md`)
- the whole hub as static files for a private host:
  `agentdiff hub ROOT --export DIR`

```bash
agentdiff hub                       # this directory, on http://127.0.0.1:8790/
agentdiff hub ~/work --port 8800    # another root
```

It prints the address, the sign-in and the lines an agent needs to post
telemetry:

```
AgentDiff hub: http://127.0.0.1:8790/   (root /home/me/work)
  sign in: demo / demo   (demo account; --no-demo turns it off)
  agents post telemetry: AGENTDIFF_HUB=http://127.0.0.1:8790 AGENTDIFF_HUB_TOKEN=…
```

## Your Claude Code sessions

`agentdiff hub --claude-code` serves the sessions Claude Code wrote on this
machine, live as you work: named by your first prompt, with a **What you
asked** tab, sub-agents on lanes, and *not graded* rather than failed until
a check grades them. A downloaded build does this when started with
nothing. See `docs/DOWNLOAD.md`.

## The charts

Every run opens on **At a glance**. It shows your prompts as pins, the main
agent's track, the checks, the loops and each sub-agent's lane, on one clock
that folds the idle between working stretches. Hover a pin to read the
prompt; hover any mark to name its steps. **Where the seconds went** adds up
each step's time by activity, by tool and by agent, and lists the slowest
steps. The Overview shows **when you worked**, by day and hour, over your
sessions. Below it are **what keeps failing** across them (each command
with its last error and a link to the step), **the files your agents
change most**, and **where the working time goes** by project. A
session's code tab rebuilds what it changed from its own edit calls, with
a chart of which file was edited when, under the prompt that led to it.

## Signing in

- **The demo account** (`demo` / `demo` by default) exists when the hub
  serves this machine only. A known password on a network is no login,
  so beyond this machine it is off unless `--demo` turns it on.
  `--no-demo` turns it off anywhere. It is an ordinary user marked
  `demo`; a real user who takes the name is never touched.
- **Real users:** `agentdiff hub --add-user alice` asks for a password
  (or reads `AGENTDIFF_HUB_NEW_PASSWORD`), of at least 8 characters. It
  is stored as a salted PBKDF2-SHA256 hash in
  `.agentdiff-hub/users.json`, readable by its owner only.
  `--remove-user` removes one.
- **Sessions** are a random token in an `HttpOnly; SameSite=Strict`
  cookie. Every form that changes state carries its session's token,
  the login form a single-use one. After 5 failed sign-ins a name waits
  60 seconds. Sessions live in memory: restarting the hub signs everyone
  out.

## Settings

Every setting has one default, in `agentdiff/hub/config.py`. A file
(`.agentdiff-hub/hub.json`, or `--config`) overrides the defaults.
`AGENTDIFF_HUB_<NAME>` environment variables override the file, and
flags override everything. An unknown key in the file is refused, not
ignored.

```json
{"port": 8800, "title": "Team runs", "demo_password": "something-better", "session_hours": 8,
 "login_attempts": 5, "login_lockout_s": 60, "scan_depth": 3}
```

## Pages and the API

| route | what |
|---|---|
| `/` | the overview: running now, agents evolving, traces stuck in a loop, recent traces and runs |
| `/runs` | every run under the root, newest first (`?kind=duel\|report\|evals\|evolution\|telemetry`) |
| `/runs/<id>` | a duel's scoreboard and its traces; a telemetry run's hops on one clock, a lane per process; an eval suite; an evolving harness |
| `/runs/<id>/page` | the report the run wrote |
| `/traces` | every trace, its loop folded to a strip (`?show=live\|passed\|failed\|stuck`, `?q=`) |
| `/traces/<id>` | one trace: the loop lap by lap, the moves between tools, a lap table, every step |
| `/live` | every running agent: its last steps, its laps so far; it updates itself |
| `/evolve` | every self-evolving harness: pass rate per generation, each change tried and its paired test |
| `/evals` | every eval suite, each eval's life across generations |
| `/account` | who is signed in; change the password (every other session of that user ends) |
| `/api/v1/runs`, `/api/v1/traces`, `/api/v1/traces/<id>` | the same as JSON |
| `/api/v1/events` | server-sent events: what changed (`{v, kinds, ids}`), never its content |
| `POST /api/v1/telemetry` | an agent posts `{vector, prompt?, success?, answer?, model?, live?}` with `Authorization: Bearer <ingest token>` |
| `/healthz` | liveness |

## Loops

A trace's page reads the run as the agent's loop (`agentdiff/laps.py`).
Each lap ends at a check (a test run, a lint, a `run_check` tool), and the
check's outcome closes it as passed or failed. A run that never checks is
cut at its model turns. A lap that made the same calls as the one before
it, arguments and all, is marked as a repeat, and the stuck verdict is
`process.loops`'s own. A running trace is drawn as far as it has gone and
judged only when it ends. Every chart has a legend, a hover title per mark
and a table beside it, so nothing is told by colour alone.

## A run, start here

Every trace's page opens with its card (`agentdiff/insight.py`). One row
each, and every row names the field its words came from:

| row | what it says |
|---|---|
| verdict | how it ended, and by whose check |
| where | where to look first, with a link to the step |
| cost | its tokens and time, against the other runs of its task |
| code | what it changed in the workspace, the cases the check failed on, and any flag a reader should see before keeping it |
| fix | for a failed run: the one change to the agent its failure points at (the first `selfevolve.REMEDIES` rule that fires on it, among the remedies for how it failed), labelled a hypothesis |
| keep | for a run that passed and kept its change whole: that it can be applied |
| confidence | what the reading rests on |

The page follows the report page's grammar:
- a chip per task in the run's directory, with a dot per run (● passed,
  ○ failed, ◐ running)
- the prompt as the headline
- the card, with a step chip on the rows that point at a step

Below the card, the page is panels you open and close. The views are the
report page's, drawn on the server:

| panel | from the page |
|---|---|
| **Trajectory map** (START HERE when comparing) | Evidence: two runs as columns of steps in their own order, a line where they made the same call, dotted where they drifted, the divergence in red, each step's phase |
| **The run as a trunk** | Story: thinking on the trunk, tool calls as branches, sub-agents hanging off it |
| **Two runs on one axis** | the trunks facing each other, and by step |
| **Where the seconds went** | Panels: area is seconds on one scale for both runs, a box per lap or sub-agent, a tile per step |
| **Reward & credit** | Training: the return step by step, when the trace recorded rewards |
| **Every thread on its own lane**, **the loop**, **the flow**, **the steps** | Trace, Batch |

A preset picks which start open: **focus** (map, trunk, comparison,
code, steps), **the loop**, **time** (seconds, lanes, trunk),
**threads**, **the code**, **training** (reward, trunk) or
**everything**. A closed panel still says
its one fact (`+97 −1 in 1 file(s)`, `28 thread(s) · 122 lap(s)`), so
the details are there on demand.

**The code it produced** comes from the harness's record of the run:
the workspace diffed before and after, never the agent's account.
- the files, how each changed, and a badge on a test file
- whether the check passed, and the cases it named as failing
- the patch
- beside another run's change, the files both touched and whether they
  left the same bytes
- the command to act on it: `agentdiff apply --dir …` to keep a change
  that passed, or `agentdiff self-evolve …` to test the change to the
  agent that a failure points at

Flags: the tests were edited, a file was deleted, a large change, a pass
with no change, a change not kept whole, a truncated patch.

The Overview adds **Levels · the agents**: each agent's runs and
tasks, success as a dot on its 95% Wilson interval, median tokens,
seconds and lines changed. Its **Start here** reads the same across
every finished run:
how many failed, what the failures have in common, the one change to the
agent most of them point at, and each agent's code (pass rate, median
lines changed, runs that edited tests).

## Timelines

Every trace's page leads with the run as a **trunk** along its clock, the
way the report page's body chart draws a run:
- thinking sits on the trunk
- each tool call is a branch ending in a leaf
- each sub-agent hangs off the trunk where it first acted, as a branch of
  its own
- the laps are ticks across the trunk
- crowded stretches gather into ×N bubbles, so a 566-step run stays legible
- the step to look at is ringed

Two runs face each other, A's branches up and B's down. The steps they
share are joined, and the first difference is marked. Below the trunk,
the same clock as lanes (`agentdiff/timeline.py`):
- **Where to look first.** One step and one sentence, by rule:
  - the longest stretch of laps that repeated the one before
  - else the last failing check that no later check passed
  - else an error the run never came back to
  - else the answer of a run that never checked
  - for a run that passed, its first passing check
- **One lane per thread.** The root agent, then each sub-agent as it
  first acts. Steps that overlap in time get lanes of their own, so a
  fan-out shows as parallel bars.
- **Laps as bands**, closed by a ✓ or ✗ check; a repeated lap is dashed.
- **The clock.** A long run's quiet stretches (six or more steps with no
  edit, check, error, answer or new thread, or idle time) fold to a short
  segment that says what it holds. The clock basis is stated: recorded
  from `started_s`, or reconstructed.
- **Every bar opens its step.** Past 120 steps the table shows the
  opening, the stretch around where to look and the ending; past 24 laps
  the lap chart folds away.

**Two runs.** `compare with` puts any run of the same task beside it,
by step (the first different call is marked) or by time.

**Many runs.** `/timeline` is one row per run on one clock (or each on
its own): its steps as cells coloured by what it was doing, a tick per
lap, the look-here ring. It works for a traces directory (`?g=`),
everything under a run (`?run=`, so a self-evolving lineage shows
every version's arm in order), or one task (`?task=`). Each group says
what its failures have in common. A timeline page is always live: one
opened before the runs start fills in as they stream.

The same reading in a terminal:

```bash
agentdiff timeline run.json               # where to look, and timeline.html
agentdiff timeline a.json b.json          # two runs, the first step they differ
agentdiff timeline duel-out/traces g1/    # one line per failed run, and one row per run on the page
```

## Live

Two things move while agents run. A trace file grows on disk, frame by
frame (`<trace>.live.json`, written by the harness); a poller looks every
half second (`live_poll_s`) and names each trace that was added, grew or
finished. A vector arrives over the ingest endpoint. Both publish to one
bus, and `/api/v1/events` streams the change, with a keepalive every 15
seconds and resumption by `Last-Event-ID`. A page marked live loads the
hub's one script, `/static/live.js`, which re-fetches that page's
fragments as the signed-in user, drawn by the same server-side views.

From another machine, `agentdiff telemetry stream DIR` (or `duel --hub
URL`, `self-evolve --hub URL`) posts a trace directory as it grows, one
trace id per run, as in-band vectors: sizes, times and outcomes, never
content.

The ingest token is made once and kept in `.agentdiff-hub/ingest.token`
(owner only), so agent configurations survive a restart. A posted run is
kept in three forms:
- its vector
- its trajectory, under `.agentdiff-hub/telemetry/traces/`, which
  `agentdiff batch` reads like any trace directory
- what the poster said beside it

A run posted again as it grows replaces the stored copy only when it has
at least as many hops.

## Security

- **Network:** the hub binds to `127.0.0.1`. Anything else needs
  `--allow-remote`, because it shows every run under its root, including
  what the agents read.
- **The hub's own pages** run no script, except a live page. Their
  policy is `default-src 'none'; style-src 'unsafe-inline'; form-action 'self'`.
  A live page adds `script-src 'self'; connect-src 'self'`: the hub's one
  static file, never an inline script, talking only to this hub.
- **A run's report** is a page that run wrote, with its own scripts and
  data from agents. It is served under
  `Content-Security-Policy: sandbox allow-scripts`, which gives it an
  origin of its own, so nothing in a trace can act as the signed-in user.
- **Paths:** nothing outside the root is ever served. Ids are hashes the
  catalog made, never paths.
- **Errors and redirects:** a sign-in returns only to a path on the hub,
  never to another site. One failing request returns a 500 without
  taking the hub down.

## Layout

| module | one job |
|---|---|
| `hub/config.py` | the settings, in layers |
| `hub/auth.py` | users, hashes, the demo account (`UserStore`: JSON file or memory) |
| `hub/sessions.py` | sessions, form tokens, the login throttle |
| `hub/catalog.py` | runs on disk, found by detectors (a new kind of run is one more detector) |
| `hub/ingest.py` | posted telemetry, kept as runs |
| `hub/traces.py` | every trace under the root, summarised once per file version |
| `hub/live.py` | the bus and the event stream |
| `hub/viz.py` | the charts, as server-side SVG: laps, flow, eval river, harness river, hop timeline |
| `hub/views.py` | the pages |
| `hub/urls.py` | paths, queries and forms, without `urllib` (the engine's rule) |
| `hub/app.py` | request in, response out: every route, no sockets |
| `harness/hub_server.py` | the only part that listens: the HTTP adapter, the poller, the wiring, the client, the streamer |

The app takes its collaborators as arguments, so a test, or an embedding,
swaps any of them. Nearly every test runs the app with no socket.
