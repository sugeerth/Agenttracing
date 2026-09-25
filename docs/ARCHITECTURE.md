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
`rl.stats`, `rl.audit`, `rl.space`, `evolution`, `evolution_compare`, `budget`, `fetches`,
`coevolution`, `data`, `data_evolution` — obeys the same envelope, produced by `deepcompare/section.py`:

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

*Adoption, lineage scope.* `evolve.py` owns the scope's attach site:
`evolve.lineage_batch(lineage, …)` builds the aggregate (the last step's
pair as a runs batch, parent as A and child as B by name, not
alphabetically) and `evolve.attach_sections` runs the pass;
`analyse_lineage` runs the same pass on an empty aggregate.
`evolution_compare` registers `on_demand=True` with
`requires=("evolution",)`: its input, the lineages to compare against,
is not part of one lineage, so it attaches only when `against` names
some, and a plain `evolve` output never carries the key. The comparison
embeds each lineage's section without the episode timelines
(`generations[].timelines: "omitted; see aggregate.evolution"`); the
primary lineage's full section under `aggregate.evolution` keeps them.
`coevolution` (`coevolve.py`) registers with `requires=("evolution",)`
and is not on demand: the agent's lineage is always monitored, so every
`evolve` output carries the key. Its one input that is not the lineage —
external candidate metrics — reaches it through the context:
`attach_sections(…, candidates=None)` and `lineage_batch(…,
candidates=None)` place the list under `LineageContext.extra["candidates"]`,
and the output is byte-identical when the keyword is absent. The section
reads `ctx.lineage`, `agg["evolution"]` and that key, imports its shared
constants from `evolve` (`CHECK_TOOL_RE`, `CLAIM_PHRASES`, `TOOLISH`)
rather than copying them, and imports nothing from `harness/`; the
proposer seam (`harness/proposer.py`) is imported by the `coevolve`
command alone, inside `run`. `data_evolution` (`data.py`) is the
scope's fourth section: `requires=("evolution",)`, `after=("coevolution",
"evolution_compare")`, so it reads the eval's flags when the eval
walked the step and attaches last; it reads `ctx.lineage` (the
generations' artifacts and traces), `agg["evolution"]` and
`agg["coevolution"]`, and imports `evolve` and `coevolve` inside the
function only, because `data.py` is imported by `report.py`, which
`evolve` imports through `suite`.

*The data side.* `data` (`deepcompare/data.py`, `docs/DATA.md`) attaches
to every pair report after `fetches` and to the `runs` aggregate; it
reads the prompt, the instructions, the models, the corpus, the
answer's provenance and the chain data → model → agent → answer. It
shares the fetches section's source identity (the tool name and the
normalised input) rather than inventing a second, and the semantic
section's claim extractor and normalisation for provenance. Two
measures, both containment shares over recorded text with their basis
in the output, and nothing inferred beyond them. `trace.AgentInfo`
keeps `system_prompt` and `config` off `to_dict` so a report side is
byte-identical with or without them; the section reads them from the
typed agent, a raw dict, or a lineage's artifacts.

## 3. Aggregates and commands

`suite.analyse_runs` (runs layout), `consolidate` (batch), `fleet`,
`evolve`, `evolve --against` and `coevolve` each produce an aggregate
dict and the per-task pair reports; `report.render_html` writes the page
with the data inlined. Every command follows one shape, implemented once in
`deepcompare/commands/_io.py`:

    traces = load_traces(dir_or_files, warn)          # SCHEMA validation, run ids from names
    result = <the analysis>                            # pure
    write_outputs(out_dir, reports, aggregate, html=…) # report_<task>.json, aggregate.json, report.html

`bundle` reads output directories through `grafana.load_target` and
writes the three levels (`deepcompare/bundle.py`); `key`, `mcp`
(`deepcompare/mcpserver.py`, stdlib JSON-RPC in the engine) and `serve`
(`deepcompare/harness/serve.py`, the HTTP server in the harness) read the
bundle back. A level-3 record carries each step's text capped at
`data.TEXT_CAP` and flagged when cut, and the run's `data` reading; the
`step` tool and the `/steps/<index>` route return the whole text of one
step from the member's copy of the report, so a detail is one call
away and the record stays bounded. `AggregateContext.extra` is where a command puts what a
section reads and the aggregate does not carry (`token_cap`). The batch
aggregate does not run the aggregate scope (it never did), so the bundle
derives a batch member's `budget` and `fetches` from the reports' sides
and says so (`source`).

`deepcompare/cli.py` is the parser and the dispatch only; each command
is a module in `deepcompare/commands/` exposing `register(subparsers)`
and `run(args) -> int`. **Adding a command is adding one module.**
(Status: `live` and `paths` are there; the remaining commands move as
the in-flight work lands — see the changelog. Commands: landed — all 43
are modules under `deepcompare/commands/`, listed in `cli.COMMANDS` in
`--help` order; `_io.py` holds the load-run-write shape and `_common.py`
the shared argument groups (CI artifacts, provider options, the trace
database); `cli.py` imports every command module as `<name>_cmd`, since
several are named after engine functions; `tests/test_commands.py` pins
the list and runs batch, runs and compare end to end.)

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
The views are Story, Evidence, Batch, Panels, Training, Evolution and,
seventh, Evals: the `coevolution` lane, declared in `VIEWS` and
`STACK_PLAN` with its `order`, whose blocks (`web/blocks/36_coevolve.js`)
share one family store `{evalGen, metric, step, candidate}` scoped to
the page and read `ctx.aggregate.coevolution` — the flow first, at three
zoom levels (loop, step, candidate), then hindsight, the matrix, the
metric, the probes and the integrity. A view is a lane plus a tab: adding
one is the lane in `STACK_PLAN`, the name in `VIEWS`, the hash regex and
the arrow-key cycle, and the tab must still fit at 360 px.
The invariants the tests enforce and the rules of efficient drawing are
in `web/blocks/README.md`; the agent that knows them is
`.claude/agents/viz.md`.

## The recipes

| to add | write | register | test |
|---|---|---|---|
| a framework's logs | one adapter fn in `adapters.py` | `register_formats()` | `tests/test_adapters.py` |
| an analysis of a pair | `deepcompare/<name>.py` | `@sections.register("pair", key)` | `tests/test_<name>.py`, pinned on `demo/traces` |
| a reading of a lineage that is not a check | `deepcompare/<name>.py`, `requires=("evolution",)`, `after=("coevolution", "evolution_compare")` | `@sections.register("lineage", key, …)` | pinned on the hand-built lineage of `tests/test_evolve.py` and on `demo/evolve/lineage` |
| an analysis of a batch | same | `@sections.register("aggregate", key)` | pinned on `demo/rl/train` |
| a check on a lineage | a function in `evolve.py`'s checks table | the table | pinned on `demo/evolve/lineage` |
| a probe of the eval | `Probe(name, question, trigger(view), propose(view) -> [spec])` in `coevolve.PROBES`; pure, no lookahead, specs in the metric language | the tuple, in the order the probes run | `tests/test_coevolve.py`: its trigger on a synthetic step view, its candidates on the demo, the pinned ledger |
| a validator of a candidate | `(name, fn(candidate, view) -> {pass, note, …})` in `coevolve.VALIDATORS`; every one is computed, the order decides | the tuple, in deciding order | constructed cases both ways; the multiplicity rule if it tests at a level |
| a feature of an episode | `Feature(kind, basis, direction)` in `coevolve.FEATURES` and its line in `features()`; None when unreadable, never 0 | the dict, in output order | hand-built trajectories and the demo; `parse_spec` accepts it by id |
| a command | `deepcompare/commands/<name>.py` | `register(subparsers)` | `tests/test_cli.py` |
| a chart | `web/blocks/NN_<name>.js` | `AgentDiff.block({...})` | a class at the end of `tests/test_blocks_ui.py` |
| a demo | a behaviour table + caller over `demo/_env.py` | — | a determinism test |
| a level of the bundle | a function in `bundle.py`, its tool in `mcpserver.TOOLS`, its route in `harness/serve.py` | the tool table / the route table | `tests/test_bundle.py`, `tests/test_mcp.py`, `tests/test_serve.py` |
| a dashboard | a panel in `grafana/generate_dashboards.py`, then regenerate `grafana/dashboards/<name>.json` | the provisioning yaml | `tests/test_grafana.py` (every metric exists) |

## What must stay true

- `deepcompare/` has no network code outside `deepcompare/harness/`
  (AST-pinned by `tests/test_harness.py`).
- Same input, same bytes: every section and every demo is deterministic.
- `web/blocks.html` matches its sources (`tests/test_blocks_build.py`);
  rebuild and commit the artifact with the sources in one commit.
- A pooled statistic is drawn beside its per-task view; an interval is
  drawn as an interval; a SYNTHETIC label is carried through.
