# Block authoring contract

`web/blocks.html` is **built**, not hand-edited. `build_blocks.py` inlines
`_shell.html`, `00_core.js` and every `NN_*.js` module into one
self-contained file, so the page keeps the "no server, no CDN, open the
file" property while the modules stay separately editable.

```bash
python web/build_blocks.py          # writes web/blocks.html
python -m deepcompare batch demo/telemetry/traces -o out --template web/blocks.html
```

## Registering a block

A block is one card answering one question. Modules call `AgentDiff.block`
at load time — never touch the DOM outside your own `el`.

```js
AgentDiff.block({
  id: 'verdict',                    // unique, stable, kebab-case
  title: 'Verdict',                 // shown in the card header
  question: 'Who won, and was it decisive?',
  group: 'outcome',                 // outcome | trajectory | cost | signal
  size: 'normal',                   // normal | wide | tall
  relevance(ctx) { return 1.0 },    // 0..1 — is this worth showing for THIS data?
  render(el, ctx) { ... },          // paint into el; called on every data change
});
```

### `lead: true`

A lead block renders in the **lead lane** above the hero — first on the
page, full width, in registration order — and never in a column. It has
no star, collapse or remove control: it is the page's opening sentence,
not a card in the layout. The verdict card (`04_verdict.js`) is one; use
it only for something every reader must see before anything else, and
keep it quoting the report verbatim.

### The trajectory map's contract

`20_trajectory.js` draws every visible step as `g.tj-hit[data-side][data-i]`
— a focusable button — with `text.tjm-name` (never truncated), an excerpt,
tokens, and gutter labels on every edge. Verbatim loops collapse to one
node with `text.tjm-loop`; phase bands are `rect.tjm-phase`. Selecting a
node calls the family's `select(row, side)`; the `agentdiff:select-step`
event does the same from outside. Keep these selectors when changing the
map — the browser tests measure the drawn SVG against the report.

### Lanes and views (`ctx.lane`)

The page has three views — Story (the one-column narrative), Evidence
(outcome · trajectory · integrity columns) and Batch (cost · signal ·
other columns) — chosen with the segmented control or `#view=…` in the
URL. `ctx.lane` tells a block where it is being drawn: `"story"`,
`"hero"` or `"stack"`. A block may show less in the story (fold a walk
behind a disclosure, show three rows with a "show all") and must never
show *different numbers*.

### Keys and small screens

`[` / `]` walk the tasks; `←` `→` on the view tabs; on the map `Tab`
reaches a step, `Enter`/`Space` opens it in the inspector, `↑` `↓` walk
one run, `←` `→` cross the gutter; `?` opens the keyboard help; `Esc`
closes a tooltip, then a panel, and focus returns to where it was. At
or under 700px the map's inspector folds under the map; at or under
480px the reading's value table becomes a two-line list; under a touch
pointer the card actions are always visible; under
`prefers-reduced-motion` nothing slides, fades or replays.

### The story charts (D3)

`web/vendor/` holds the one third-party library the page uses, D3 7
(ISC; its licence file sits beside it and is quoted into the built page).
`build_blocks.py` inlines every `web/vendor/*.min.js` before the core, so
the page stays one offline file. `03_d3charts.js` exposes
`AgentDiff.charts` — `story(host, ctx, side)`, `tree(host, ctx)`,
`why(host, ctx)`, `forward(host, ctx, side)`, plus `available()`, `motion()` (0 under
`prefers-reduced-motion`) and `responsive(host, draw)` — and the story
blocks call them at the top of their render. The rules: draw only what
the report says (the engine's scores, statuses, steps, estimates; never
a chart-side number), dispatch `agentdiff:select-step` on a click so the
map's inspector follows, keep every mark focusable, and label an estimate
as one. Nothing of ours may call `d3.json`/`d3.csv`/`fetch` — the build
tests pin it.

A block's `storyTitle` is the section name the story view numbers
("1 · What happened", "2 · The trace as a tree", "3 · Why", "4 · Take
forward", …).

### Composites

`AgentDiff.composite({id, title, question, group, size, parts, summary?,
emptyText?})` registers one card that lays several blocks out one after
another under small titles — only the parts with something to say, a
repeated note said once, an optional summary line first. The parts leave
the default layout (they stay in the drawer). Register composites in
`80_composites.js`, after their parts.

### Charts and assistive technology

After every render the core gives each root `<svg>` a block drew
`role="img"`, an accessible name from the block's title and question,
and a `<title>` child, unless the block set a role or `aria-hidden`
itself. Decorative glyphs should carry `aria-hidden="true"`.

### `relevance(ctx)`

Data-driven, not preference-driven: return how much this block has to say
about the loaded report. A block about failure attribution should return ~0
when nothing failed. Return `0` and the block is hidden from the default
composition (the user can still add it from the block drawer). This is half
the personalization score; the other half is what the visitor actually uses.

Never throw. Never read storage. Never call `Date.now()` inside `relevance`
— ranking must be stable within a render.

### `render(el, ctx)`

`el` is an empty `<div>` you own. Rebuild its contents from scratch; the core
clears it before each call.

`ctx` gives you:

| field | what |
|---|---|
| `ctx.report` | the selected task's report object (may be `null`) |
| `ctx.reports` | every per-task report |
| `ctx.aggregate` | the batch-level aggregate object |
| `ctx.task` | selected task id |
| `ctx.selectTask(id)` | switch the selected task (re-renders everything) |
| `ctx.signal(kind)` | record an interaction: `'inspect'`, `'expand'`, `'hover'` |
| `ctx.h(tag, attrs, kids)` | element helper; attrs `{class, text, html, style, onclick, ...}` |
| `ctx.svg(tag, attrs, kids)` | same, in the SVG namespace |
| `ctx.fmt` | `num, int, pct, usd, sec, tokens, delta, truncate` |
| `ctx.color` | `a, b, good, bad, warn, muted, axis, grid` — theme-aware tokens |
| `ctx.empty(el, message)` | render a consistent "nothing to show" state |
| `ctx.width(el)` | current content width, for sizing SVGs |

### Rules

1. **One file per module**, named `NN_topic.js`. Never edit another module or
   `00_core.js`; the build concatenates them in filename order.
2. **No external anything** — no CDN, no fonts, no fetch, no `<img src>`.
   Inline SVG only. The file must work from `file://` offline.
3. **No globals.** Wrap in an IIFE. The only global you touch is `AgentDiff`.
4. **Theme-aware.** Use `ctx.color` and the CSS variables; never hard-code
   `#fff` or `#000`. The page follows the OS theme and a manual toggle.
5. **Degrade honestly.** If the data you need is missing or a section is
   marked `available: false`, call `ctx.empty(el, why)` — do not invent
   numbers, and do not render an axis with no data behind it.
6. **Respect the label on the number.** `entropy_basis`, `source:
   synthetic-demo`, `reliable: false`, oracle ceilings and the `caveat`
   strings exist because the figure means something narrower than it looks.
   If you show the number, show its qualifier.
7. **Deterministic.** No `Math.random()`, no animation that changes layout.

### Shared state within a family

The trajectory modules (`20_trajectory.js`: Tracks, Alignment ribbon,
Step detail, Meaning vs wording, Intent bands, Trajectory map, Run lens)
share one selection cursor — click a step in any of them and the others
follow. The cursor lives inside that module (`select`/`subscribe`), and
any state a block keeps beyond it (the lens's open steps, the map's
selected claim) is task-scoped: it resets when `ctx.task` changes, never
leaks between tasks. From *outside* the module there is one documented
door: dispatch `agentdiff:select-step` on `document` with
`{detail: {row, side}}` and the family cursor moves as if clicked — the
walkthrough uses exactly this when Tracks is not on the page.

Data shapes are in `SCHEMA.md`; every field named there is what a report
actually carries.
