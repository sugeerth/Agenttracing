# Long-running agents

Most of a run that lasts a night is idle: waiting on a model, on CI, on a
person. Its work comes in bursts, and its loops last hours rather than
steps. A timeline drawn step by step gives a three-day run the same width as
a three-minute one, and the night's loop vanishes into a smear.
`agentdiff.longrun` reads a run at the scale it ran.

```bash
agentdiff timeline run.json                  # a run of hours or days: the story, phase by phase
agentdiff timeline a.json b.json             # two long runs, cut at the checkpoints they share
agentdiff hub .                              # the long panel and the lens, live while it runs
python demo/longrun/make_trace.py            # the demo: three days, and the same agent guarded
```

A run is read as long past an hour on its clock or 600 steps, or with
`--long`. On the hub, its page opens on the long panel. Panels that would
draw thousands of marks are drawn when opened.

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

One trail, in one ink. Shape and position carry the meaning, so nothing
rests on telling colours apart. Everything is server-side SVG, and the
page works without a script.

- **The whole run as a trunk** along its clock.
  - A burst is a branch: the longer the branch and the bigger its leaf,
    the more calls it made. The leaf is filled when the burst moved
    forward.
  - Failed checks hang below the trunk.
  - A loop of bursts is one arc over its stretch.
  - The idle between sessions is a dotted gap that says how long it was.
  - Checkpoints are dots on the trunk. Where to look first is ringed, and
    the stall is a bracket underneath.
  - Every leaf opens its burst.
- **Every call of a burst** (or of any stretch, `?t0=&t1=`), as two trunks.
  - The first places each call on its clock; the second puts the calls in
    order at an even pace. Faint lines join each call to its moment.
  - A call rises from the trunk, taller when it took longer. A failed one
    hangs below, and a passing check ends in a dot.
  - The same failing call run again and again is bracketed.
  - Pages of 600 calls.
- **The story, phase by phase.** Each phase is one row:
  - an eventful burst, with the filler bursts that led into it
  - a loop, however many bursts it took
  - a stretch of filler

  Idle is a divider row between phases.
- **When it worked:** a row per day and a column per hour; darker means
  more calls. It uses the wall clock (UTC) when the trace gives
  `started_at`. A cell opens its stretch.
- **Its pace:** calls, seconds per call and tokens per step, each on its
  own axis, with whether the agent slowed or its context grew.
- **Where the working time went:** a box per phase and a tile per tool,
  area by seconds, darker where its calls failed.
- **Beside another run** (`?vs=`): both cut at the checkpoints they share,
  one above the line and one below, with the part that failed darker.

## The lens

`/static/longview.js` is the hub's own script; the page's CSP allows no
other. It draws the same trail as a tape of phases.

- **Focus and context.** The phase in focus takes most of the width. Its
  neighbours narrow with distance, and phases more than three away scroll
  out, counted at the edges. A thin trail above shows the whole run, with
  what is on screen underlined.
- **Every call at an even pace,** a branch off the trunk (`pace: clock`
  puts each phase on its own clock instead). Where there is room, the
  tool is named at the tip. A crowded phase is a quiet comb, and its
  detail comes up under the lens.
- **The fisheye.** A halo follows the pointer and magnifies the calls
  under it, which grow as under glass. The panel below lists them with
  what they cost: seconds, tokens, failures, retries, the tools that took
  the time.
- **Moving.**
  - prev and next, ← →, the wheel or a drag move a phase at a time,
    animated, and idle is passed rather than stopped at
  - `[` and `]` jump to the previous or next failure, checkpoint or loop
  - a treemap box brings its phase to the lens
- **Bring forward:** every call, failures, retries, checkpoints, edits.
  Everything else fades.

It opens on where to look first. On a live run it restarts with each
update. `agentdiff timeline --long` writes the same page as one file,
with the lens and its data inside it, so it opens in any browser without
a hub.

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
