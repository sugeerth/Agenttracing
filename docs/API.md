# The bundle, the key, the MCP server and the HTTP API

Everything AgentDiff computed about a set of runs, packaged so that a
person, a coding assistant or a product can get it at three levels of
grain — what is running, what each run did, and where every token and
every fetch went — from a directory, from a stdio server, from a local
HTTP API, or from one line of text that fits in a chat message.

Nothing in this layer computes a number the engine did not already
compute. Every value is a count or a sum over the steps the traces
recorded, carried through from the members; a token count the trace
labelled *estimated* stays labelled; `null` means the trace did not
record the quantity, never that it was zero; a success rate carries its
Wilson interval; SYNTHETIC data is flagged per run; no model identifier
is copied into the index; and the same inputs give the same bytes and
the same id, on any machine — there is no timestamp anywhere.

## The two sections the third level is made of

`budget` and `fetches` (`deepcompare/budget.py`, `deepcompare/fetches.py`)
attach to every pair report and, in a runs layout, to the aggregate.
Both follow the section envelope (`version`, `measurable`, `reason`, …).

**`budget` — where the tokens went.** Per run: `tokens {total, by_kind
{plan, reason, search, retrieve, read, tool_call, answer}, by_tool
{name: n}, measured, estimated, unknown, unknown_steps, basis}` — every
step's count as recorded, summed under the label the trace gave it
(`tokens_basis`), never re-estimated; `io {input_tokens, output_tokens,
source: totals}` and `cost_usd {value, source}`, each unmeasurable with
a reason when the totals carry nothing (unrecorded is not zero, and not
free); `burn [[index, kind, name, tokens, cum]]` capped at `BURN_CAP =
2000` steps with a note; `top` (the `TOP_STEPS = 8` heaviest, with their
share); `waste {after_last_evidence, in_errored_calls, in_repeats,
basis}` — tokens after the last step carrying a recorded `reward > 0` or
a `quality` label of `good` (before the answer; `null` when no such
signal is recorded), in steps with `error` true, and in fetches
repeating an earlier `(name, input)`; `per_second` when latency was
recorded; `synthetic`; a narrative. Per pair: `{a, b, delta {total,
by_kind, by_tool}, cheaper, narrative}`. Per aggregate: `{agents {name:
{runs, total, mean, per_task, by_kind, by_tool, measured, estimated,
unknown, measured_share, cost_usd_total, cost_runs, synthetic}}, tasks
{task: {agent: mean}}, heaviest_runs, cap {value, source, over}, runs
[a ledger row per run], narrative}` — `cap` from `runs --token-cap N` (handed to the section as
`AggregateContext.extra["token_cap"]`) when the command was given one,
else `null` with source `none given`; `bundle --token-cap N` lists the
runs over the cap in the overview.

**`fetches` — every search, retrieval and read.** Per run: `records
[{index, kind, name, query (≤ `QUERY_CHARS = 200` chars, `query_chars`
the full length), output_chars, tokens, tokens_basis, latency_s|null,
error, effect, repeat_of, used, used_basis, span}]` — `used` is `true`
on a recorded `reward > 0` or a `quality` of `good`, `false` on a
recorded reward of zero or less or a `quality` of `bad`, and `null`
with the reason otherwise: use is never inferred from the answer's
text; `counts {by_kind, by_tool, total, errors, repeats, used, unused,
unknown_use}`; `volume`; `sources`; the search `map {nodes [{id, kind:
query|result|read|call|answer, label, index, size}], edges [{from, to,
kind: yields|reads|reaches}], unknown_use, basis}` — each search yields
the retrieve and tool calls that follow it and is read by the reads,
until the next search; a fetch reaches the answer only when its use is
recorded, and the map says how many have no edge. Per pair and per
aggregate as for `budget`, with `used_share` (`null` when no fetch
carries a signal).

## The bundle

```
agentdiff bundle <out_dir> [<out_dir> ...] -o <bundle_dir> [--name NAME] [--token-cap N] [--locator URL_OR_PATH ...] [--traces DIR ...]
```

A member is what `batch`, `runs`, `evolve`, `coevolve` or `fleet` wrote:
`aggregate.json` and the `report_*.json` beside it, or `fleet.json`. The
bundle directory holds:

| file | what |
|---|---|
| `bundle.json` | `{version: 1, id, name, members [{index, label, kind, source, tasks, agents, lineage, sections, runs}], levels {overview, runs, run_index, budget, fetches}, locators, key}` |
| `members/<n>/aggregate.json`, `members/<n>/report_*.json` | byte copies of the members |
| `runs/<key>.json` | the level-3 record of one run |
| `traces/<member>/…` | with `--traces`, a byte copy of every trace that completed a record |
| `report.html` | the primary (first) member's page, with `DEEPCOMPARE_DATA.bundle = {id, name, members, levels}` (`levels` also carries `records`) inlined so the Levels view has every run of every member |
| `KEY.txt` | the key |

**The id** is `sha256:<hex>` of the canonical JSON (sorted keys, no
whitespace) of every member's aggregate and reports, concatenated in
member order (a fleet's ranking first). It is content-addressed: the
same outputs bundle to the same id anywhere, and `bundle.verify(dir)`
(`agentdiff key <key> --bundle DIR`, the `verify` tool, `/api/v1/verify`)
recomputes it from the copies and reports `{id, recomputed, match}`; a
second digest, `records_digest`, covers what the id does not — the
level-3 records under `runs/` (canonical JSON by key) and the copied
traces (bytes by path) — and the same call recomputes it and reports
`records_digest, records_recomputed, records_match, records_reason`
separately, so a tampered run record is noticed while the id, the
members' content, still matches. Writing into an existing bundle
directory clears `members/`, `runs/` and `traces/` first.

**The three levels** (`bundle.levels(members)`):

- *overview* (level 1): `agents [{name, family, framework, runs, tasks,
  success_rate {rate, lo, hi, n, basis} (Wilson), tokens_total,
  tokens_runs, cost_usd_total, cost_runs, seconds_total, seconds_runs,
  fetches_total, fetches_runs, self_evolving, lineage, synthetic,
  members}]`; `lineages [{family, member, generations, generations_n,
  recommended, best, verdicts, eval {generations, adopted, closures,
  closures_learned, drift, recommended {base, evolved, agree}}|null,
  loops, integrity}]`; `loops [{agent_family, eval_generations,
  closures, closures_learned, reading}]`; `totals {runs, tasks, agents,
  members, tokens, tokens_runs, cost_usd, cost_runs, seconds, fetches,
  fetches_runs, synthetic_share, basis}`; `sections` (every section key
  present in any member); `heaviest_runs`; `cap`; `reading`. A sum is
  over the runs that recorded the quantity and `*_runs` says how many
  did — a lineage's earlier generations carry seconds and fetches from
  their episodes but no tokens, and say so with `tokens_total: null`.
- *runs* (level 2): one row per run, `key: "<member>/<task>/<agent>/<run>"`,
  `{member, task, agent, run_id, success, steps, tool_calls, tools {name:
  n}, tokens, tokens_measured_share, cost_usd, seconds, fetches, errors,
  repeats, return, lineage_gen, synthetic, detail, basis}`. `basis` names
  the sources that filled the row (`scorecard`, `budget`, `fetches`,
  `evolution`, `report`, `trace`); `detail` is true when a report — or,
  with `--traces`, a trace — carries the run's steps, so level 3 is whole.
- *run* (level 3): `runs/<key>.json` — the row (its counts as `steps_n`
  and `fetches_n`), `steps [{index, type, name, tokens, tokens_basis,
  latency_s, error, effect, reward, value, input_chars, output_chars,
  span, input_text, input_truncated, output_text, output_truncated}]`
  (each text capped at `data.TEXT_CAP = 4000` characters, the flag true
  when cut, the full length in `*_chars`; the `step` tool returns the
  whole), `budget` (the per-run budget reading), `fetches` (the per-run
  fetches reading), `data` (the per-run data reading — the prompt, the
  instructions, the models, the corpus, the provenance, the chain;
  `docs/DATA.md`; the report's own when it carries one, unmeasurable
  with the reason where the trace lacks a prompt, a model or text),
  `timeline [[t0, dur, kind, name, reward, flags]]` —
  the shape the Evolution timescape draws, rewards from the report's
  `rl` reading (`reward_basis` says recorded or shaped), flags `e`rror,
  `w`asted, `d`ecisive, `f`ault, `v`erifier — `trace_id`, `report`,
  `side`. A run whose steps the output did not keep (a runs layout keeps
  one representative pair per task; a lineage keeps its episodes'
  timelines and the last pair's reports) is `measurable: false` with that
  reason, its row intact.

**`--traces DIR [DIR …]`** attaches the source traces: every directory is
walked recursively, every trace file read (the runs layout's
`<task>__<agent>__<run>.json` name gives the run id, else the file's
`run_id`; a file that is not a trace is passed over, an invalid one is
listed as skipped), and every record whose steps are not in the output is
completed from the trace matched by `trace_id` (the scorecard's or the
lineage episode's), else by task, agent and run id: `steps`, `budget`,
`fetches`, `data` and — unless the lineage's episode timeline is there,
which keeps the flags the pair established — `timeline` are read from the
trace, `trace_id` and `trace_path` are set, `report` and `side` are null,
and `steps_source: "trace traces/<member>/<path>"` names the copy the
bundle now holds, so it stays self-contained (only traces that completed
a record are copied). The row's numbers a source had not recorded are
filled from the trace (`tokens`, `tokens_measured_share`, `cost_usd`,
`seconds`, `fetches`, `errors`, `repeats`, `tools`, `success`, `steps`,
`tool_calls`), never overwritten; `detail` becomes true and `trace` joins
`basis`; the overview's `tokens_runs` and `fetches_runs` rise
accordingly. The id does not change (it is the members' content), and
without `--traces` every byte is as before. On the demo, `bundle
<batch> <runs> <coevolve> --traces demo/traces demo/rl/train
demo/evolve/lineage` gives level 3 for all 322 runs: 40 from the reports,
282 from traces. The `step` tool and `/runs/<key>/steps/<index>` return
the whole text of a step from the trace copy (`source:
"traces/<member>/<path>#steps[<index>]"`); the `run` and `data` tools
and routes read the completed record with no change.

## The key

`agentdiff1:<base64url(zlib(json))>` — one line of printable ASCII,
paste-safe, under `KEY_MAX_BYTES = 2000`. The JSON is `{v: 1, id, name,
agents [{name, runs, success_rate {rate, lo, hi}, tokens_total,
self_evolving}], truncated, lineages [{family, generations, recommended,
eval_adopted}], totals, locators}`. At most `KEY_AGENTS = 12` agents are
named; the rest are counted in `truncated`; past the size cap the
locators go first, then agents one at a time, then the evals' adopted
metrics — each drop visible in the key, never silent.

```
agentdiff key <key>                  # decode: print the overview (no bundle needed)
agentdiff key <key> --bundle DIR     # verify: recompute the bundle id and the records digest, say match/mismatch for each, then print
agentdiff key --from DIR             # re-derive the key from a bundle
```

A malformed key — the wrong prefix, not printable ASCII, over 2000
bytes, a payload past 64 KiB once inflated, or one that decodes to
something other than the overview's shape — is an error with the
reason, exit 2; a mismatch of the id prints the key's id, the bundle's
claimed id and the recomputed one, a mismatch of the records prints
both digests and the reason, either exit 1.

The sentence to paste beside a key, for a tool that has the MCP server:

> AgentDiff key: agentdiff1:… — call overview, then runs, then run for detail.

## The MCP server

```
agentdiff mcp --bundle DIR
```

JSON-RPC 2.0 over stdio, one message per line, until EOF; the Model
Context Protocol's `initialize` (the request's `protocolVersion` echoed
when the server knows it — 2025-06-18, 2025-03-26, 2024-11-05 — else the
newest; capabilities `{tools: {}, resources: {}}`; `serverInfo {name:
"agentdiff", version}`), `notifications/initialized` (silence), `ping`,
`tools/list`, `tools/call`, `resources/list`, `resources/read`. Stdlib
`json` and `sys` only — no socket, no thread — so it lives in the engine.

For a coding assistant, the `mcpServers` entry:

```json
{"mcpServers": {"agentdiff": {"command": "python3", "args": ["-m", "deepcompare", "mcp", "--bundle", "<dir>"]}}}
```

Tools, each with a JSON schema for its arguments and a description that
says what the numbers are and are not:

| tool | arguments | returns |
|---|---|---|
| `overview` | — | level 1 |
| `runs` | `agent?, task?, member?, success?, sort?: tokens\|cost\|seconds\|fetches\|steps\|errors, limit?` | `{runs, n, of, filters}` — level-2 rows, sorted descending, rows without the number last |
| `run` | `key` | the level-3 record |
| `fetches` | `key?, agent?` | one run's fetch records, or the per-member per-agent fetch summary |
| `budget` | `agent?, task?, key?` | the per-member budget aggregates, narrowed; with `key`, one run's budget and burn |
| `step` | `key, index` | one step in full — the whole input and output text, uncapped, with tokens, latency, error, effect, quality, note, model telemetry, span and the source path (`members/<n>/report_<task>.json#<side>.steps[<index>]`); −32000 when the run's steps are not in the output or the index names no step |
| `data` | `key` | the run's data reading (level 3's `data`) |
| `lineage` | `family?` | the lineages and loops of the overview, one family when named |
| `key` | — | `{key, overview}` |
| `verify` | — | `{id, recomputed, match, records_digest, records_recomputed, records_match, records_reason}` |

Resources: `agentdiff://bundle/bundle.json`,
`agentdiff://bundle/members/<n>/aggregate.json`,
`agentdiff://bundle/runs/<key>.json`, each `application/json`. Errors are
JSON-RPC errors with a reason: −32601 unknown method, −32602 bad
arguments (an unknown tool or argument, a wrong type, a sort that is not
one of the six), −32000 a key or URI that names nothing, −32700 a line
that is not JSON.

A transcript over the demo bundle (`batch demo/traces`, `runs
demo/rl/train`, `coevolve demo/evolve/lineage`), responses abridged:

```
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"assistant","version":"1"}}}
← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{},"resources":{}},"serverInfo":{"name":"agentdiff","version":"0.9.0"},"instructions":"AgentDiff bundle: call overview for what ran (level 1), runs for one row per run (level 2), run with a key for … (level 3). Every number is a count or a sum over recorded steps; null means unrecorded, never zero."}}
→ {"jsonrpc":"2.0","method":"notifications/initialized"}
→ {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"overview","arguments":{}}}
← {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"…"}],"structuredContent":{"agents":[{"name":"atlas-v2","runs":8,"success_rate":{"rate":0.875,"lo":0.5291,"hi":0.9776,"n":8,…},"tokens_total":7264,"fetches_total":25,"self_evolving":false},{"name":"bolt-v3","runs":8,"success_rate":{"rate":0.625,"lo":0.3057,"hi":0.8632,…},"tokens_total":9669,…},{"name":"ledger-agent@g0","runs":30,"success_rate":{"rate":0.5,"lo":0.3315,"hi":0.6685,…},"tokens_total":null,"tokens_runs":0,"fetches_total":848,"self_evolving":true,"lineage":"ledger-agent"},…],"lineages":[{"family":"ledger-agent","generations_n":7,"recommended":"g4","eval":{"generations":4,"adopted":["verified_rate","clean_pass_rate","frugal_pass_rate"],"closures":4,…}}],"totals":{"runs":322,"tasks":14,"agents":11,"members":3,"tokens":413457,"tokens_runs":172,"cost_usd":0.104091,"cost_runs":16,"seconds":9193.9244,"fetches":8632,"fetches_runs":322,"synthetic_share":0.9503,…},"reading":"3 members: 11 agents over 14 tasks and 322 runs; …"},"isError":false}}
→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"runs","arguments":{"sort":"tokens","limit":2}}}
← {"jsonrpc":"2.0","id":3,"result":{…"structuredContent":{"n":2,"of":322,"runs":[{"key":"runs/rl05_incident_postmortem/policy-v1/r1","success":false,"steps":66,"tokens":4486,"tokens_measured_share":1.0,"fetches":52,"errors":4,"seconds":48.9812,"detail":false,…},{"key":"runs/rl05_incident_postmortem/policy-v1/r8","tokens":4225,…}]},"isError":false}}
→ {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"run","arguments":{"key":"batch/t01_acme_revenue/atlas-v2/r1"}}}
← {"jsonrpc":"2.0","id":4,"result":{…"structuredContent":{"version":1,"measurable":true,"key":"batch/t01_acme_revenue/atlas-v2/r1","steps_n":5,"tokens":840,"steps":[{"index":0,"type":"plan","tokens":162,"tokens_basis":null,…},…],"budget":{"tokens":{"total":840,"by_kind":{"plan":162,"reason":0,"search":183,"retrieve":154,"read":184,"tool_call":0,"answer":157},…,"measured":0,"estimated":0,"unknown":840},"burn":[[0,"plan","plan",162,162],…,[4,"answer","final_answer",157,840]],"waste":{"after_last_evidence":0,"in_errored_calls":0,"in_repeats":0,…}},"fetches":{"counts":{"total":3,"used":3,"unknown_use":0,…},"map":{"nodes":[…],"edges":[{"from":"query1","to":"result2","kind":"yields"},{"from":"query1","to":"read3","kind":"reads"},{"from":"query1","to":"answer","kind":"reaches"},…]}},"timeline":[[0.0,2.27,"think","plan",0.0,""],[2.27,2.47,"tool","web_search",1.0,""],…],"reward_basis":"rl section, shaped"},"isError":false}}
```

The heaviest run of the demo (`runs/rl05_incident_postmortem/policy-v1/r1`)
answers `run` with `measurable: false`: the runs layout keeps one
representative pair per task, and that run is not in it. Its row is
whole; its steps are not in the output, and the record says so rather
than filling in.

## The HTTP API

```
agentdiff serve --bundle DIR [--host 127.0.0.1] [--port 8787]
```

`http.server` in `deepcompare/harness/` (the only place a network module
may live; the command imports it inside `run()`). Read-only GET; every
answer JSON with `Cache-Control: no-store`, the page apart; anything
else a 404 with a JSON reason; no directory listing, and a run is found
through the bundle's own index, never the filesystem. Localhost by
default; another `--host` is warned about on stderr; `::1` binds an IPv6
socket; an address or port that cannot be bound is `error: cannot bind …`
and exit 2; a record the index names but the disk no longer holds is a
500 with a reason, never a dropped connection.

| route | returns |
|---|---|
| `/api/v1/overview` | level 1 |
| `/api/v1/runs?agent=&task=&member=&success=true\|false&sort=&limit=` | `{runs, n, of}`; a bad `sort`, `success` or `limit` is 400 |
| `/api/v1/runs/<key>` | the level-3 record; 404 with a reason when the key names no run |
| `/api/v1/runs/<key>/fetches` | the run's fetches reading |
| `/api/v1/runs/<key>/data` | the run's data reading |
| `/api/v1/runs/<key>/steps/<index>` | one step in full, as the `step` tool; 404 with a reason for an index that names no step, 400 for one that is not an integer |
| `/api/v1/budget?agent=&task=` | the per-member budget aggregates, narrowed |
| `/api/v1/lineage?family=` | the lineages and loops |
| `/api/v1/key` | `{key, overview}` |
| `/api/v1/verify` | `{id, recomputed, match, records_digest, records_recomputed, records_match, records_reason}` |
| `/`, `/report.html` | the page |
| `/bundle.json` | the manifest |

Examples, abridged:

```
GET /api/v1/overview
200 {"agents":[{"name":"atlas-v2","runs":8,"success_rate":{"rate":0.875,"lo":0.5291,"hi":0.9776,"n":8,…},"tokens_total":7264,…}],"totals":{"runs":16,…},"reading":"1 member: 2 agents over 8 tasks and 16 runs; 16933 tokens counted over 16 of them; …"}

GET /api/v1/runs?agent=bolt-v3&sort=tokens&limit=2
200 {"runs":[{"key":"batch/t02_cve_libfoo/bolt-v3/r1","success":false,"steps":9,"tokens":1725,"fetches":7,…},{"key":"batch/t01_acme_revenue/bolt-v3/r1","tokens":1522,…}],"n":2,"of":16}

GET /api/v1/runs/batch/t01_acme_revenue/atlas-v2/r1/fetches
200 {"measurable":true,"records":[{"index":1,"kind":"search","name":"web_search","query":"ACME Corp FY2025 annual results …","query_chars":64,"output_chars":275,"tokens":183,"latency_s":2.47,"error":null,"repeat_of":null,"used":true,"used_basis":"quality label good",…},…],"counts":{"total":3,"used":3,…},…}

GET /api/v1/runs/batch/t01_acme_revenue/atlas-v2/r1/steps/3
200 {"key":"batch/t01_acme_revenue/atlas-v2/r1","index":3,"type":"read","name":"open_page","input":"https://ir.acmecorp.com/news/fy2025-results","output":"ACME Corp Reports Fourth Quarter and Full Year Fiscal 2025 Results. Full year fiscal 2025 highlights: Total revenue of $4.82 billion, up 11% year over year. …","input_chars":43,"output_chars":301,"tokens":184,…,"source":"members/0/report_t01_acme_revenue.json#a.steps[3]"}

GET /api/v1/runs/batch/t01_acme_revenue/atlas-v2/r1/data
200 {"measurable":true,"reason":null,"task":{"id":"t01_acme_revenue","prompt":"Find ACME Corp's total revenue …","prompt_chars":110,"expected":"$4.82 billion","expected_chars":13},"agent":{"name":"atlas-v2","model":"sim-planner-2",…,"instructions":{"system_prompt":null,"source":null,"chars":null},…},"models":[{"model":"sim-planner-2","steps":5,…,"source":"trace.agent.model"}],"corpus":{"sources":[…],"total_chars":701,"distinct":3,"fetches":3,"repeated_reads":0,…},"provenance":{"atoms":3,"supported":3,"unsupported":0,…,"grounded_in":[{"step":1,"name":"web_search","overlap":0.3333,…},…]},"chain":{"nodes":[…],"edges":[…],"reading":"3 data nodes, 1 model node and the answer; …"},…}

GET /api/v1/runs/no/such/run/r1
404 {"error":"not found","reason":"no run 'no/such/run/r1'; keys are <member>/<task>/<agent>/<run>, listed by /api/v1/runs"}

GET /api/v1/runs?sort=bogus
400 {"error":"bad request","reason":"sort must be one of tokens, cost, seconds, fetches, steps, errors, not 'bogus'"}
```

## Determinism, boundaries, tests

Same inputs, same bytes, same id, same key (`tests/test_bundle.py`
writes the bundle twice and compares every file). No network module
outside `deepcompare/harness/`, and the engine never imports the harness
(`tests/test_harness.py`); the MCP server imports no network or thread
module (`tests/test_mcp.py`). The HTTP tests start the server on port 0
in a thread and drive it with `urllib` from the test alone
(`tests/test_serve.py`). The sections are pinned on the demo traces and
on hand-built runs in `tests/test_budget.py` and `tests/test_fetches.py`;
every existing output is byte-identical apart from the two new keys.
The data reading and the `step`/`data` tools and routes are pinned in
`tests/test_data.py` and the three test files above; the level-3
record's `data` carries the trace's own recorded model name (it is the
trace's data), and the index — `bundle.json`, levels 1 and 2 — still
carries none.
