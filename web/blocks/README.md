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

Data shapes are in `SCHEMA.md`; every field named there is what a report
actually carries.
