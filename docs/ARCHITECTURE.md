# Architecture: the layers, the contracts, and how to add to each

AgentDiff is four layers, each a pure function of the one below it, and
every layer has one contract for adding to it. Nothing here needs a
network, a dependency, or a random number without a seed.

    traces  →  sections  →  aggregates  →  the page
    (SCHEMA)   (deepcompare/*.py)          (web/blocks/*.js)

## 1. Traces

A trace is one run of one agent on one task, in the shape `SCHEMA.md`
defines; `deepcompare.trace.Trajectory` is its typed reading and
`deepcompare.record.Recorder` writes one. Adapters (`deepcompare/adapters.py`)
turn other frameworks' logs into traces and register themselves with
`register_formats()`; a new framework is one adapter function and one
registration, nothing else.

**The scripted environment** every demo is generated from lives in
`demo/_env.py`: the tasks, the reward table (−0.1 per tool call, −1 on
an error, +1 per fact found, ±5 at the answer), and `run_episode(task,
behaviour, rng, recorder)`, which plays one episode of a policy whose
behaviour is a small dict (`hit`, `error`, `checks`, `retries`, …).
`demo/rl/generate_rl.py` and `demo/evolve/generate_evolve.py` are thin
callers of it. A new demo is a new behaviour table and a new caller;
the environment does not change, so every demo stays comparable and
every generated file stays byte-identical to the one its test pins.

## 2. Sections

A section is one analysis with one question, one module, one output
dict. Every section — `impact`, `trust`, `tools_profile`, `rl`,
`rl.stats`, `rl.audit`, `rl.space`, `evolution`, `evolution_compare` —
obeys the same envelope, produced by `deepcompare/section.py`:

    {"version": int, "measurable": bool, "reason": str | None, ..., "narrative": str}

`section.measurable(payload, version=…)` and `section.unmeasurable(reason,
version=…)` build it; nothing hand-writes the envelope. A part of a
section that cannot be read returns `unmeasurable` at that level and
the rest of the section is still produced. Every number is a count or a
sum over recorded steps; an estimate says so in the field that carries
it; SYNTHETIC data is labelled through.

**Shared helpers** live in two modules and nowhere else:

- `deepcompare/_text.py` — the prose of a reading: `num`, `signed`,
  `pct`, `secs`, `plural(n, word)`, `join_names`, `interval(point, lo,
  hi)`, `side_name(report, side)`. A grammar bug fixed here is fixed in
  every section.
- `deepcompare/_stats.py` — `percentile`, `mean_ci` (normal), `iqm`,
  `median`, `rng(seed, label)` (one `random.Random` per section and
  label, so adding a section never moves another's numbers), and the
  stratified bootstrap. A statistic is implemented once.

**Registration.** `deepcompare/sections.py` is the registry:

    from deepcompare import sections
    @sections.register("pair", "tools_profile", requires=("timing", "attribution"))
    def tools_profile(report: dict) -> dict: ...

    @sections.register("aggregate", "rl", requires=("reliability",))
    def rl_aggregate(agg: dict, ctx: sections.AggregateContext) -> dict: ...

Scopes are `pair` (attached to a pair report by `report.compare`),
`aggregate` (attached by `suite.analyse_runs` and `consolidate`), and
`lineage` (attached by `evolve`). `sections.attach(scope, target, ctx)`
runs every registered section for that scope in dependency order
(`requires` names sections that must already be on the target) and
places each result under its key. **Adding a section is adding a module
that registers; no wiring file changes.** A section that raises is
caught, recorded as `unmeasurable("<exception>")` under its key, and the
rest still attach — a new section can never take the report down.

## 3. Aggregates and commands

`suite.analyse_runs` (runs layout), `consolidate` (batch), `fleet`,
`evolve` and `evolve --against` each produce an aggregate dict and the
per-task pair reports; `report.render_html` writes the page with the
data inlined. Every command follows one shape, implemented once in
`deepcompare/commands/_io.py`:

    traces = load_traces(dir_or_files, warn)          # SCHEMA validation, run ids from names
    result = <the analysis>                            # pure
    write_outputs(out_dir, reports, aggregate, html=…) # report_<task>.json, aggregate.json, report.html

`deepcompare/cli.py` is the parser and the dispatch only; each command
is a module in `deepcompare/commands/` exposing `register(subparsers)`
and `run(args) -> int`. **Adding a command is adding one module.**
(Status: `live` and `paths` are there; the remaining commands move as
the in-flight work lands — see the changelog.)

## 4. The page

One file, built from `web/blocks/*.js` in filename order. `00_core.js`
is the runtime: the registry (`AgentDiff.block({...})`), the views, the
lanes (`STACK_PLAN`, each with its declared reading `order`), the
storage, the rerender. `01_lib.js` is the shared library, `AgentDiff.lib`:

    fmt:      num, signed, pct, secs, short, isNum
    color:    side(a|b), agent(name), verdict(kind), good, bad
    svg:      svg(attrs) with role="img" and aria-label required, note(), tip(host)
    glyph:    interval(g, scale, point, lo, hi), foldWidth(n), foldSeconds(s)
    layout:   responsive(host, draw), measure(host)
    family:   family(key, defaults) → {get, set, subscribe, persist}   // shared selection across a family of blocks
    style:    once(id, cssText)                                        // a stylesheet injected once

A block imports nothing; it reads `AgentDiff.lib` and `d3`. **Adding a
block is adding one file that registers**, with its group naming the
lane, its id placed in that lane's `order` if it has a fixed place, and
one self-contained test class appended to `tests/test_blocks_ui.py`.
The invariants the tests enforce and the rules of efficient drawing are
in `web/blocks/README.md`; the agent that knows them is
`.claude/agents/viz.md`.

## The recipes

| to add | write | register | test |
|---|---|---|---|
| a framework's logs | one adapter fn in `adapters.py` | `register_formats()` | `tests/test_adapters.py` |
| an analysis of a pair | `deepcompare/<name>.py` | `@sections.register("pair", key)` | `tests/test_<name>.py`, pinned on `demo/traces` |
| an analysis of a batch | same | `@sections.register("aggregate", key)` | pinned on `demo/rl/train` |
| a check on a lineage | a function in `evolve.py`'s checks table | the table | pinned on `demo/evolve/lineage` |
| a command | `deepcompare/commands/<name>.py` | `register(subparsers)` | `tests/test_cli.py` |
| a chart | `web/blocks/NN_<name>.js` | `AgentDiff.block({...})` | a class at the end of `tests/test_blocks_ui.py` |
| a demo | a behaviour table + caller over `demo/_env.py` | — | a determinism test |
| a dashboard | `grafana/dashboards/<name>.json` | the provisioning yaml | `tests/test_grafana.py` (every metric exists) |

## What must stay true

- `deepcompare/` has no network code outside `deepcompare/harness/`
  (AST-pinned by `tests/test_harness.py`).
- Same input, same bytes: every section and every demo is deterministic.
- `web/blocks.html` matches its sources (`tests/test_blocks_build.py`);
  rebuild and commit the artifact with the sources in one commit.
- A pooled statistic is drawn beside its per-task view; an interval is
  drawn as an interval; a SYNTHETIC label is carried through.
