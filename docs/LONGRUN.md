# Long-running agents

Most of a run that lasts a night is idle: waiting on a model, on CI, on a
person. Its work comes in bursts, and its loops last hours rather than
steps. A timeline drawn step by step gives a three-day run the same width as
a three-minute one, and the night's loop vanishes into a smear.
`agentdiff.longrun` reads a run at the scale it ran.

```bash
agentdiff timeline run.json --long           # any run, phase by phase; the page carries the lens
agentdiff timeline a.json b.json --long      # two runs, cut at the checkpoints they share
agentdiff hub .                              # every trace's phases chip, live while it runs
agentdiff hub . --export site/ --bare-index  # the whole hub as static files, for a private host
python demo/longrun/make_trace.py            # the demo: three days, and the same agent guarded
```

Phases are a view of **any** run, an option rather than a mode. On the
hub every trace has the panel, opened with its `phases` chip
(`?view=long`) or by a link into a burst. On the command line, pass
`--long`. A run with thousands of steps (past an hour on its clock or
600 steps) draws its other panels when they are opened, so its page
stays fast; nothing else changes with length.

## The rules

Each rule is stated in the reading's `basis`.

- **Sessions** are split by idle of at least 30 minutes: the agent stopped,
  overnight, or waited on a person.
- **Bursts** inside a session are split by a gap the run itself sets.
  The threshold falls between its two kinds of gap: the few seconds
  between calls, and the minutes between bursts (Otsu's split of the log
  gaps). It is clamped to 20 s .. 30 min. When the gaps show no two
  kinds, it is ten times the median gap. With no recorded clock, a burst
  ends after each check, and sessions cannot be told apart.
- **Progress** is a check passing where its previous run did not, or
  passing with more cases than it ever had (`4 passed`, then `6 passed`).
  A check is followed by its command: the part of the line that is the
  check, without the `cat > f <<EOF` before it or the `2>&1 | tail` after.
  An edit is not progress, and neither is a call. A **regression** is a
  check failing where its previous run passed.
- **The stall** is the longest stretch between two progress events,
  measured as wall time, as time spent working, and in calls. A run still
  going has its stall left open.
- **A loop over hours** is bursts in a row that each share at least 60%
  of their calls (tool and input) with an earlier burst, without
  progress. Idle inside it is part of it: the agent went round, slept,
  and went round again.
- **Where to look first:** the start of the longest loop, else of the stall
  (when it worked ten minutes or more), else the first regression.

The reading is one linear pass. A 20,000-step run reads in well under a
second. The repeated-block search in `process.loops` is now linear per
period. It gives the same answer as comparing every block (a test checks
it against the old search), and searches periods up to 400 in a run of
more than 800 tool steps.

## What the hub draws

All server-side SVG; the page works without a script. Every view wears the same colours as the rest of the hub at a lower intensity (`svg.soft`), so a page of thousands of marks stays calm, and identity never rests on colour alone: each mark also has a glyph, a position and a title.

- **The whole run on one line.** Sessions keep their true clock. The idle
  between them is compressed to a break that says how long it was.
  - Rows: the sessions, then the bursts coloured by how they went (✓
    progress, ↻ stuck, ! failing), then every call as a density of
    activity, then the checks.
  - Below the rows, the loop and the stall as brackets.
  - Every burst opens its calls, and every session opens on its own clock.
- **Every call of a burst** (or of any stretch, `?t0=&t1=`): on its clock
  in a lane per activity, then in order, each call the same width.
  - Lines join each call to its moment on the clock: they fan in where
    the clock ran fast and out where it ran slow.
  - The same failing call run again and again is bracketed.
  - Pages of 600 calls.
- **The story, phase by phase.** Each phase is one row:
  - an eventful burst, with the filler bursts that led into it
  - a loop, however many bursts it took
  - a stretch of filler, where no check changed

  Idle is a divider row between phases.
- **When it worked:** a row per day and a column per hour, on the wall
  clock (UTC) when the trace gives `started_at`. For a run of hours, a row
  per hour and a column per 5 minutes. A cell opens its stretch.
- **Its pace:** calls, seconds per call and tokens per step, as three small
  charts, each on its own axis. The reading says whether the agent slowed
  down or its context grew between its first and last thirds.
- **Where the working time went:** a box per phase and a tile per tool,
  area by seconds.
- **Beside another run** (`?vs=`): both runs cut at the checkpoints they
  share (the same check starting to pass, in the same order). Tool use is
  mirrored, the first run above the line and the other below, and the
  stretch where they part most is named.

## The lens

`/static/longview.js` is the hub's own script (the page's CSP allows no
other). It reads `GET /api/v1/traces/<id>/long` and draws the run as a
tape of phases.

- **Focus and context.** The phase in focus takes most of the width. Its
  neighbours narrow with distance, and phases more than three away scroll
  out, counted at the edges. A strip above shows the whole run with what
  is on screen boxed.
- **Every call at an even pace.** Each call is the same width inside its
  phase (`pace: clock` puts the phase on its own clock instead). When
  there is room, the tool is named beside it.
- **The fisheye.** A halo follows the pointer and magnifies the calls
  under it, which grow as under glass. The panel below lists them with
  what they cost: seconds, tokens, failures, the tools that took the time.
- **Moving.**
  - prev and next, ← →, the wheel or a drag move a phase at a time,
    animated, and idle is passed rather than stopped at
  - `[` and `]` jump to the previous or next phase with a failure, a
    checkpoint or a loop
  - a box in the treemap brings its phase to the lens
- **Bring forward:** every call, failures, retries (the same failing call
  again), checkpoints, edits. Everything else dims.

It opens on where to look first. On a live run it restarts with each
update. Focus, filter and pace are kept per tab (session storage). They
are conveniences only.

## The demo

`demo/longrun/make_trace.py` writes two runs, SYNTHETIC
(`harness.adapter: synthetic`), deterministic from a seed. Both move 16
ledger packages to a v2 API.

- **`agent-3day`.** Day 1 goes package by package. At 17:20 the
  integration test starts failing, and the agent loops for 14 hours
  across the night: the same reads, two edits in turn, the same failing
  test. At 09:15 on day 2 a reviewer's note leads to the fix. The full
  suite passes on day 3.
- **`agent-guarded`.** The same agent with `agentdiff guard` installed
  (`docs/GUARD.md`). Its third identical failing run is refused, so it
  reads the migration guide that evening and finishes the same day.

`--live DIR --pace S` streams the first as it would grow, for the hub.
The long panel and the lens follow it, phase by phase.

## Real sessions

A Claude Code transcript timestamps every entry, so a traced session keeps
its real clock: `started_at`, and each step's `started_s` and latency
(`tool_use` to `tool_result`). A real session of three prompts, resumed
with idle between them, under `agentdiff guard`, reads as three bursts
(05:30, 05:31, 05:32), each one step forward: first pass, then 2 → 4 and
4 → 6 passing as it added tests.

## The hub as static files

`agentdiff hub ROOT --export DIR` writes every page of the hub as files:
- the Overview, runs, traces, timelines, evolve and evals
- each trace's page, its phases page with the lens, and for a big run a
  page with every panel drawn
- the JSON of each trace and of each lens

Links point at the files. The lens script is inlined, and burst links
move the lens (`#burst-N`). The hub's ingest token is never written.
Signing in, live updates and the compare picker need the hub itself, and
each page says so.

`--bare-index` writes `index.html` without its document shell, for a host
that adds its own (a private Claude artifact). The theme follows the
viewer's choice as well as the system's.
