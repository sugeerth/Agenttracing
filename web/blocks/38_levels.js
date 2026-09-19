/* AgentDiff blocks — the Levels view: three levels of grain over every run
 * the page knows, from what is running down to where every token and
 * every fetch of one run went.
 *
 *   lv-overview  level 1 — the agents as rows (runs, success with its
 *                Wilson interval drawn as an interval, tokens with their
 *                basis, cost when recorded, seconds, fetches, a mark for
 *                self-evolving), the self-evolving lineages with their
 *                eval loops as a loop glyph (closures counted), the totals
 *                line, the SYNTHETIC share.
 *   lv-runs      level 2 — every run as a row of a virtualised table
 *                (only the visible window is in the DOM), sortable by
 *                tokens, cost, seconds, fetches, steps or errors; filter
 *                chips by member, agent, task and outcome; each row an
 *                outcome mark, a tool-call bar by tool, a token bar
 *                (measured solid, estimated or unknown basis hatched),
 *                fetches, errors. A click opens the run at level 3.
 *   lv-run       level 3 — one run in full: the token burn-down (cumulative
 *                tokens by step as a stepped area coloured by step kind,
 *                unmeasured steps hatched, the answer marked, the heaviest
 *                steps labelled, the waste after the last evidence
 *                shaded), where the budget went (by kind and by tool), the
 *                search map (query → yields → reads → answer, node size by
 *                output chars, a `reaches` edge only for a fetch whose use
 *                is recorded), and the fetch table. The numbers line says
 *                where the steps come from: the output, or a trace the
 *                bundle attached (`steps_source: "trace …"`, `--traces`);
 *                a run whose steps are in neither says so with the reason.
 *
 * The data is `DEEPCOMPARE_DATA.bundle` when the page is a bundle's
 * (`agentdiff bundle`): its three levels come from deepcompare/bundle.py
 * and the level-3 records are inlined. On a plain output page the same
 * levels are derived here from what the output carries — the scorecard's
 * per-run rows, the `budget` and `fetches` ledgers, a lineage's episodes,
 * the pair reports' sides — with the same "null means unrecorded, never
 * zero" rule, and the block says what a plain page cannot show. Every
 * number is the engine's; nothing is estimated here; a Wilson interval
 * is drawn only when the output carries one.
 *
 * Family `levels` (page-scoped, persisted): {run, agent, task, member,
 * outcome, sort, x}; `AgentDiff.levels.{select, state, reset, timing,
 * tile}` for the sibling blocks and the tests.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || !AgentDiff.lib) return;
  var L = AgentDiff.lib, isNum = L.fmt.isNum, num = L.fmt.num, pct = L.fmt.pct, secs = L.fmt.secs, short = L.fmt.short;
  var d3 = global.d3;
  if (!d3) return;

  var KINDS = ["plan", "reason", "search", "retrieve", "read", "tool_call", "answer"];
  var FETCH_KINDS = ["search", "retrieve", "read", "tool_call"];
  var SORTS = [["tokens", "tokens"], ["cost", "cost_usd"], ["seconds", "seconds"], ["fetches", "fetches"], ["steps", "steps"], ["errors", "errors"]];
  //: the run table's row height, its longest viewport in rows, and the rows drawn beyond the window
  var ROW_H = 28, VP_ROWS = 14, OVERSCAN = 6;
  //: how many facet values are chips before the facet becomes a select
  var CHIP_MAX = 14;
  //: the overview names this many agents before folding the rest
  var AGENT_ROWS = 40;
  //: the burn-down bins steps past this many columns; the map draws this many nodes; the fetch table this many rows
  var BURN_COLS = 600, MAP_CAP = 400, TABLE_CAP = 500;
  var VERDICT_GLYPH = { improved: "▲", regressed: "▼", flat: "─", gamed: "⚠", overfit: "◇", forgot: "✕", traded: "⇄", unmeasurable: "·" };
  var UID = 0;

  var CSS = [
    ".lv{position:relative}",
    ".lv svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
    ".lv text{font-size:var(--fs-xs)}",
    ".lv .lab{fill:var(--ink-2)}.lv .lab.dim{fill:var(--ink-3)}.lv .lab.mono{font-family:var(--mono)}.lv .lab.strong{fill:var(--ink);font-weight:600}",
    ".lv .tick{fill:var(--ink-3);font-variant-numeric:tabular-nums;font-family:var(--mono)}",
    ".lv .rule{stroke:var(--rule)}.lv .zero{stroke:var(--rule-2)}",
    ".lv-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 6px;max-width:100ch;line-height:1.45}.lv-lede b{font-weight:600}",
    ".lv-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px;min-width:0}",
    ".lv-bar i{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:5px}",
    ".lv-bar .sw{display:inline-block;width:12px;height:10px;vertical-align:-1px;margin-right:5px;border-radius:2px}",
    ".lv-chipline{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.lv-chipline b{color:var(--ink);font-weight:600}",
    ".lv-syn{font-family:var(--mono);font-size:var(--fs-xs);color:var(--warn);font-weight:600;letter-spacing:.04em;margin-left:6px}",
    ".lv-btn{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".lv-btn:hover{color:var(--ink)}.lv-btn[aria-pressed=true]{background:var(--ink);color:var(--bg)}.lv-btn:disabled{opacity:.4;cursor:default}",
    ".lv-btn:focus-visible,.lv-row:focus-visible,.lv-vp:focus-visible,.lv-node:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".lv-sel{font:inherit;font-size:var(--fs-xs);border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);border-radius:6px;padding:1px 6px;max-width:220px}",
    ".lv-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:100ch;line-height:1.5}",
    ".lv-read{font-size:var(--fs-xs);color:var(--ink-2);margin:6px 0 0;line-height:1.5;max-width:100ch}.lv-read b{color:var(--ink);font-weight:600}",
    ".lv-h{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);margin:12px 0 4px;letter-spacing:.04em;text-transform:uppercase}",
    ".lv-status{font-size:var(--fs-xs);color:var(--ink-2);margin:0 0 6px;line-height:1.5;max-width:110ch;font-variant-numeric:tabular-nums}",
    ".lv-cannot{font-size:var(--fs-xs);color:var(--ink-2);background:var(--surface-2);border-radius:7px;padding:6px 10px;margin:0 0 8px;line-height:1.5;max-width:110ch}",
    // tables
    ".lv-table{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:4px}",
    ".lv-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
    ".lv-table td{padding:3px 10px 3px 0;color:var(--ink-2);white-space:nowrap;vertical-align:middle}.lv-table td.num,.lv-table th.num{text-align:right;font-family:var(--mono)}",
    ".lv-table td.wrap{white-space:normal;min-width:14em;max-width:36em;overflow-wrap:anywhere}",
    ".lv-table td.bad{color:var(--bad)}.lv-table td.good{color:var(--good)}.lv-table td.warn{color:var(--warn)}.lv-table td.dim{color:var(--ink-3)}.lv-table td b{color:var(--ink);font-weight:600}",
    ".lv-table tr.sel td{background:var(--surface-2)}",
    ".lv-agent{font:inherit;font-size:var(--fs-xs);border:0;background:none;color:var(--ink);font-weight:600;padding:0;cursor:pointer;text-align:left}.lv-agent:hover{text-decoration:underline}",
    ".lv-evo{font-family:var(--mono);color:var(--accent);margin-left:5px}",
    ".lv-ci{font-family:var(--mono);color:var(--ink-3);font-variant-numeric:tabular-nums}",
    ".lv-details{margin-top:8px}.lv-details summary{cursor:pointer;color:var(--ink-2);font-size:var(--fs-xs)}",
    ".lv-cols{display:flex;gap:14px 24px;flex-wrap:wrap;align-items:flex-start}.lv-cols>*{flex:1 1 300px;min-width:0}",
    // css bars: the token bar and the tool bar, drawn as divs so a thousand rows cost nothing
    ".lv-tokbar{display:inline-flex;height:9px;border-radius:2px;overflow:hidden;background:transparent;vertical-align:middle;margin-left:6px}",
    ".lv-tokbar .solid{background:var(--ink-2);height:100%}",
    ".lv-tokbar .hatch{height:100%;background:repeating-linear-gradient(45deg,var(--ink-3) 0 2px,transparent 2px 5px)}",
    ".lv-toolbar{display:inline-flex;height:11px;gap:1px;vertical-align:middle;min-width:0}",
    ".lv-toolbar .seg{height:100%;border-radius:1px;min-width:2px}",
    // the virtualised run table
    ".lv-grid{min-width:640px}",
    ".lv-head,.lv-row{display:grid;grid-template-columns:18px minmax(190px,1.5fr) minmax(110px,1fr) 150px 64px 56px 52px 44px;gap:0 10px;align-items:center}",
    ".lv-head{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);border-bottom:1px solid var(--rule);padding:2px 6px 5px}",
    ".lv-head button{font:inherit;border:0;background:none;color:inherit;padding:0;cursor:pointer;text-align:inherit}.lv-head button:hover{color:var(--ink)}",
    ".lv-head button[aria-pressed=true]{color:var(--ink);font-weight:600}",
    ".lv-head .n,.lv-row .n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}",
    ".lv-vp{position:relative;overflow-y:auto;overflow-x:hidden;outline:none;border-radius:6px}",
    ".lv-spacer{position:relative}.lv-win{position:absolute;left:0;right:0;top:0}",
    ".lv-row{height:" + ROW_H + "px;padding:0 6px;font-size:var(--fs-xs);color:var(--ink-2);cursor:pointer;border-radius:5px;outline:none;box-sizing:border-box}",
    ".lv-row:hover{background:var(--surface-2)}.lv-row.cur{box-shadow:inset 0 0 0 1px var(--rule-2)}",
    ".lv-row.sel{background:var(--surface-2);box-shadow:inset 3px 0 0 var(--ink)}",
    ".lv-row .lv-key{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}.lv-row .lv-key b{color:var(--ink);font-weight:600}.lv-row .lv-key .dim{color:var(--ink-3)}",
    ".lv-row .lv-mark{font-family:var(--mono);font-weight:700;text-align:center}.lv-mark.ok{color:var(--good)}.lv-mark.ko{color:var(--bad)}.lv-mark.na{color:var(--ink-3)}",
    ".lv-row .bad{color:var(--bad)}",
    ".lv-tok{display:flex;align-items:center;min-width:0}.lv-tok .n{flex:0 0 52px}",
    // level 3
    ".lv-crumbs{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;min-width:0}",
    ".lv-crumbs button{font:inherit;font-size:var(--fs-xs);font-family:var(--mono);border:0;background:transparent;color:var(--ink-2);padding:1px 4px;border-radius:4px;cursor:pointer;max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".lv-crumbs button[aria-current=true]{color:var(--ink);font-weight:600;cursor:default}",
    ".lv-crumbs button:hover:not([aria-current=true]){background:var(--surface-2);color:var(--ink)}.lv-crumbs .sep{color:var(--ink-3)}",
    ".lv-nums{display:flex;gap:4px 16px;flex-wrap:wrap;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}.lv-nums b{color:var(--ink);font-weight:600;font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ".lv-nums .ok{color:var(--good)}.lv-nums .ko{color:var(--bad)}",
    ".lv-node{cursor:pointer;outline:none}.lv-node:focus-visible .ring{stroke:var(--accent);stroke-opacity:1}",
    ".lv-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
    ".lv-tip b{color:var(--ink)}.lv-tip .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
    "@media (max-width:700px){.lv-head,.lv-row{grid-template-columns:18px minmax(150px,1.4fr) minmax(90px,1fr) 130px 60px 50px 48px 40px}.lv-grid{min-width:600px}}",
  ].join("\n");
  function ensureStyle() { L.style.once("levels", CSS); }

  // ------------------------------------------------------------- helpers

  //: the core's element helper, bound from ctx.h at the top of every render (the drawings below run after one)
  var H = null;
  var fmtInt = d3.format(",d");
  function tok(v) { return isNum(v) ? fmtInt(Math.round(v)) : "—"; }
  function usd(v) { return isNum(v) ? "$" + num(v, 4) : "—"; }
  var plural = L.fmt.plural, trunc = L.fmt.trunc;
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (err) { return false; }
  }
  function dur() { return prefersReduced() ? 0 : 260; }
  function width(host) { return L.layout.measure(host, 300, 1400); }
  function sum(arr, f) { var t = 0, n = 0; arr.forEach(function (x) { var v = f(x); if (isNum(v)) { t += v; n++; } }); return { total: n ? t : null, n: n }; }
  //: a categorical colour by name: tools on one palette, step kinds on another, so a legend never lies across the two
  var TOOL_PALETTE = d3.schemeTableau10, KIND_PALETTE = d3.schemeSet2;
  function kindColour(kind) { var i = KINDS.indexOf(kind); return i >= 0 ? KIND_PALETTE[i % KIND_PALETTE.length] : "var(--ink-3)"; }
  function toolScale(names) { return d3.scaleOrdinal(TOOL_PALETTE).domain(names); }
  function synthetic(harness) {
    if (!harness || typeof harness !== "object") return false;
    var note = String(harness.note || "");
    return note.toUpperCase().indexOf("SYNTHETIC") === 0 || String(harness.adapter || "") === "synthetic";
  }

  // -------------------------------------------------------------- family

  var FAMILY = null, TIMING = { overview: null, runs: null, run: null }, TILE = 1;
  function family() {
    return FAMILY || (FAMILY = L.family("levels", { run: null, agent: null, task: null, member: null, outcome: null, sort: "tokens", x: "steps" }, { scope: "page", persist: true }));
  }
  function state() { return family().get(); }
  function select(patch) { family().set(patch); }
  function listen(el, fn) { family().subscribe(function (st) { try { fn(st); } catch (err) { if (global.console) console.warn("AgentDiff levels: repaint failed", err); } }, el); }
  //: a stored selection this page cannot honour is dropped
  function loadState(m) {
    var st = state(), changed = false;
    if (st.run !== null && !m.byKey[st.run]) { st.run = null; changed = true; }
    if (st.agent !== null && m.facets.agent.indexOf(st.agent) < 0) { st.agent = null; changed = true; }
    if (st.task !== null && m.facets.task.indexOf(st.task) < 0) { st.task = null; changed = true; }
    if (st.member !== null && m.facets.member.indexOf(st.member) < 0) { st.member = null; changed = true; }
    if (st.outcome !== null && ["success", "fail", "unknown"].indexOf(st.outcome) < 0) { st.outcome = null; changed = true; }
    if (!SORTS.some(function (s) { return s[0] === st.sort; })) { st.sort = "tokens"; changed = true; }
    if (st.x !== "steps" && st.x !== "time") { st.x = "steps"; changed = true; }
    if (changed) family().persist();
    return st;
  }
  AgentDiff.levels = {
    select: select,
    state: function () { var st = state(); return { run: st.run, agent: st.agent, task: st.task, member: st.member, outcome: st.outcome, sort: st.sort, x: st.x }; },
    reset: function () { family().reset(); },
    //: the last draw times in ms, per block, and the DOM/data row counts of the run table
    timing: function () { return { overview: TIMING.overview, runs: TIMING.runs, run: TIMING.run, tile: TILE }; },
    /* A measurement hook: tile the level-2 rows n times (keys suffixed
     * "~k", marked `tiled`) and re-render, to time the table at ten times
     * the shipped scale. The status line says the rows are tiled. */
    /* Resolve a run key from what another view knows about a run.  The
     * Trace view holds a side of a pair (an agent and a task) or a bundle
     * record key, and needs the level-3 key `lv-run` selects by; the keys
     * are built differently on a bundle page and on a plain output page,
     * so the mapping belongs here with the rows rather than in the caller.
     * Returns the key, or null when this page carries no such run. */
    find: function (q) {
      q = q || {};
      if (!MODEL) return null;
      if (q.key && MODEL.byKey[q.key]) return q.key;
      var hits = MODEL.detailRows.filter(function (r) {
        return (!q.agent || r.agent === q.agent) && (!q.task || r.task === q.task)
            && (!q.run_id || r.run_id === q.run_id) && (!q.member || r.member === q.member);
      });
      if (!hits.length) return null;
      hits.sort(function (a, b) { return (b.tokens || 0) - (a.tokens || 0) || (a.key < b.key ? -1 : 1); });
      return hits[0].key;
    },
    tile: function (n) { TILE = Math.max(1, Math.min(100, isNum(n) ? Math.round(n) : 1)); MODEL = null; if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); return TILE; },
  };

  // --------------------------------------------------------------- model
  //
  // One model per data identity: the bundle's levels when the page is a
  // bundle's, else the same levels derived from the output on the page.

  var MODEL = null;
  function bundleOf() { var d = global.DEEPCOMPARE_DATA; return d && d.bundle && d.bundle.levels && Array.isArray(d.bundle.levels.runs) ? d.bundle : null; }
  function model(ctx) {
    var b = bundleOf();
    var src = b || ctx.aggregate;
    if (MODEL && MODEL.src === src && MODEL.reports === ctx.reports) return MODEL;
    var m = b ? bundleModel(b) : pageModel(ctx);
    m.src = src; m.reports = ctx.reports;
    finish(m);
    MODEL = m;
    return m;
  }
  function finish(m) {
    if (TILE > 1) {
      var base = m.rows.slice(), out = base.slice();
      for (var k = 1; k < TILE; k++) base.forEach(function (r) { var c = Object.assign({}, r); c.key = r.key + "~" + k; c.tiled = true; out.push(c); });
      m.rows = out;
      m.tiled = TILE;
    }
    m.byKey = {};
    m.rows.forEach(function (r) { m.byKey[r.key] = r; });
    var f = { agent: {}, task: {}, member: {} }, tools = {};
    m.rows.forEach(function (r) {
      f.agent[r.agent] = 1; f.task[r.task] = 1; f.member[r.member] = 1;
      if (r.tools) for (var t in r.tools) if (Object.prototype.hasOwnProperty.call(r.tools, t)) tools[t] = (tools[t] || 0) + (r.tools[t] || 0);
    });
    m.facets = { agent: Object.keys(f.agent).sort(), task: Object.keys(f.task).sort(), member: Object.keys(f.member).sort() };
    m.toolNames = Object.keys(tools).sort(function (a, b) { return tools[b] - tools[a] || (a < b ? -1 : 1); });
    m.toolTotals = tools;
    m.maxCalls = d3.max(m.rows, function (r) { return r.tools ? d3.sum(Object.keys(r.tools).map(function (t) { return r.tools[t] || 0; })) : 0; }) || 1;
    m.maxTokens = d3.max(m.rows, function (r) { return isNum(r.tokens) ? r.tokens : 0; }) || 1;
    m.detailRows = m.rows.filter(function (r) { return r.detail; });
    var heaviest = m.detailRows.filter(function (r) { return isNum(r.tokens); }).sort(function (a, b) { return b.tokens - a.tokens || (a.key < b.key ? -1 : 1); })[0];
    m.defaultRun = heaviest ? heaviest.key : m.detailRows.length ? m.detailRows[0].key : m.rows.length ? m.rows[0].key : null;
  }

  function bundleModel(b) {
    var lv = b.levels, records = lv.records || {};
    return {
      source: "bundle", id: b.id, name: b.name, members: b.members || [],
      overview: lv.overview, rows: lv.runs.slice(),
      budgets: lv.budget || {}, fetchesAgg: lv.fetches || {},
      record: function (key) { return records[key] || null; },
      cannot: null,
    };
  }

  /* A plain output page: the levels derived from what it carries, the
   * way bundle.py derives them from a member, with the page as the one
   * member. */
  function pageModel(ctx) {
    var agg = ctx.aggregate || {}, reports = ctx.reports || [];
    var label = "page", rows = {}, details = {};
    function row(task, agent, run) {
      var k = task + "\u0000" + agent + "\u0000" + run;
      return rows[k] || (rows[k] = { key: label + "/" + task + "/" + agent + "/" + run, member: label, task: task, agent: agent, run_id: run,
        success: null, steps: null, tool_calls: null, tools: {}, tokens: null, tokens_measured_share: null, cost_usd: null, seconds: null,
        fetches: null, errors: null, repeats: null, "return": null, lineage_gen: null, synthetic: false, detail: false, basis: [] });
    }
    function set(r, source, fields) {
      for (var k in fields) if (Object.prototype.hasOwnProperty.call(fields, k) && fields[k] !== null && fields[k] !== undefined) r[k] = fields[k];
      if (r.basis.indexOf(source) < 0) r.basis.push(source);
    }
    var ev = agg.evolution || {}, co = agg.coevolution || {};
    var lineageSyn = !!co.synthetic;
    (ev.generations || []).forEach(function (gen) {
      var agent = String(gen.policy || ""); if (!agent) return;
      var genSyn = lineageSyn || String(gen.note || "").toUpperCase().indexOf("SYNTHETIC") === 0;
      (gen.episodes || []).forEach(function (ep) {
        if (!ep || !ep.task_id) return;
        var r = row(String(ep.task_id), agent, String(ep.run_id || "r1"));
        var tools = ep.tools && typeof ep.tools === "object" ? ep.tools : null;
        set(r, "evolution", { success: typeof ep.success === "boolean" ? ep.success : null, steps: ep.steps, seconds: ep.seconds, tools: tools, errors: ep.errors,
          fetches: tools ? d3.sum(Object.keys(tools).map(function (t) { return tools[t] || 0; })) : null, "return": ep["return"], lineage_gen: String(gen.id), synthetic: genSyn });
        if (Array.isArray(ep.timeline)) details[r.key] = Object.assign(details[r.key] || {}, { timeline: ep.timeline });
      });
    });
    (((agg.scorecard || {}).per_run) || []).forEach(function (sc) {
      if (!sc.task || !sc.agent) return;
      var r = row(String(sc.task), String(sc.agent), String(sc.run_id || "r1"));
      var spend = sc.spend || {}, tools = sc.tools || {}, traj = sc.trajectory || {};
      set(r, "scorecard", { success: typeof sc.success === "boolean" ? sc.success : null, steps: spend.steps, tokens: spend.tokens,
        cost_usd: isNum(spend.cost_usd) && spend.cost_usd > 0 ? spend.cost_usd : null, seconds: spend.latency_s, tool_calls: tools.calls, errors: tools.errors, repeats: traj.repeated_calls });
    });
    (((agg.budget || {}).runs) || []).forEach(function (br) {
      var r = row(String(br.task), String(br.agent), String(br.run));
      set(r, "budget", { tokens: br.tokens, steps: br.steps, cost_usd: br.cost_usd, seconds: br.seconds,
        tokens_measured_share: isNum(br.tokens) && br.tokens > 0 && isNum(br.measured) ? br.measured / br.tokens : null, synthetic: !!br.synthetic });
    });
    (((agg.fetches || {}).runs) || []).forEach(function (fr) {
      var r = row(String(fr.task), String(fr.agent), String(fr.run));
      set(r, "fetches", { fetches: fr.fetches, errors: fr.errors, repeats: fr.repeats, retries: fr.retries, attempts_numbered: fr.attempts_numbered, tools: fr.by_tool && typeof fr.by_tool === "object" ? fr.by_tool : null, synthetic: !!fr.synthetic });
    });
    reports.forEach(function (report) {
      var task = String(((report || {}).task || {}).id || ""); if (!task) return;
      ["a", "b"].forEach(function (side) {
        var block = report[side]; if (!block || typeof block !== "object" || !Array.isArray(block.steps)) return;
        var agent = String(((block.agent || {}).name) || side), run = String(block.run_id || "r1");
        var r = row(task, agent, run);
        var b = report.budget && report.budget[side] && report.budget[side].measurable ? report.budget[side] : null;
        var f = report.fetches && report.fetches[side] && report.fetches[side].measurable ? report.fetches[side] : null;
        var tools = {}, calls = 0;
        block.steps.forEach(function (st) { if (FETCH_KINDS.indexOf(st.type) >= 0) tools[st.name || "?"] = (tools[st.name || "?"] || 0) + 1; if (st.type === "tool_call") calls++; });
        var total = b ? b.tokens.total : null;
        set(r, "report", { success: block.outcome && typeof block.outcome.success === "boolean" ? block.outcome.success : null, steps: block.steps.length, tool_calls: calls, tools: tools,
          tokens: total, tokens_measured_share: b && total > 0 ? b.tokens.measured / total : null, cost_usd: b && b.cost_usd && b.cost_usd.measurable ? b.cost_usd.value : null,
          seconds: b && b.per_second && b.per_second.measurable ? b.per_second.seconds : null, fetches: f ? f.counts.total : null, errors: f ? f.counts.errors : null,
          repeats: f ? f.counts.repeats : null, retries: f ? f.counts.retries : null, attempts_numbered: f ? f.counts.attempts_numbered : null,
          synthetic: synthetic(block.harness), detail: !!(b && f) });
        details[r.key] = Object.assign(details[r.key] || {}, { block: block, budget: b, fetches: f, report: report, side: side, task: task });
      });
    });
    var list = Object.keys(rows).sort().map(function (k) { return rows[k]; });
    // the overview
    var scAgents = ((agg.scorecard || {}).agents) || {};
    var lineages = [], loops = [];
    if (ev.family) {
      var evalBlock = null, summary = ((co.flow || {}).summary) || {};
      if (co.measurable) {
        var adopted = [];
        (co.eval_generations || []).forEach(function (eg) { if (eg.after_step) (eg.adopted || []).forEach(function (mid) { adopted.push(mid); }); });
        evalBlock = { generations: (co.eval_generations || []).length, adopted: adopted, closures: summary.closures, closures_learned: summary.closures_learned,
          drift: ((co.integrity || {}).drift || {}).jaccard_distance_from_base, recommended: { base: (co.recommended || {}).base, evolved: (co.recommended || {}).evolved, agree: (co.recommended || {}).agree }, synthetic: !!co.synthetic };
        loops.push({ agent_family: String(ev.family), member: label, eval_generations: evalBlock.generations, closures: summary.closures, closures_learned: summary.closures_learned, reading: summary.sentence });
      }
      var verdicts = {};
      (ev.steps || []).forEach(function (s) { var v = s.verdict || "unmeasurable"; verdicts[v] = (verdicts[v] || 0) + 1; });
      lineages.push({ family: String(ev.family), member: label, generations: (ev.generations || []).map(function (g) { return String(g.id); }), generations_n: (ev.generations || []).length,
        recommended: (ev.recommended || {}).id, best: (ev.best || {}).id, verdicts: verdicts, eval: evalBlock, loops: evalBlock ? evalBlock.closures_learned : null,
        integrity: (ev.integrity || {}).reading, measurable: !!ev.measurable, reason: ev.reason || null, steps: (ev.steps || []).map(function (s) { return { from: s.from, to: s.to, verdict: s.verdict }; }) });
    }
    var genFamily = {};
    lineages.forEach(function (ln) { ln.generations.forEach(function (g) { genFamily[g] = ln.family; }); });
    var byAgent = {};
    list.forEach(function (r) { (byAgent[r.agent] = byAgent[r.agent] || []).push(r); });
    var agents = Object.keys(byAgent).sort().map(function (name) {
      var own = byAgent[name], fam = null;
      lineages.forEach(function (ln) { if (name.indexOf(ln.family + "@") === 0 && genFamily[name.split("@")[1]]) fam = ln.family; });
      var known = own.filter(function (r) { return typeof r.success === "boolean"; }), k = known.filter(function (r) { return r.success; }).length;
      var sc = scAgents[name] && scAgents[name].rates && scAgents[name].rates.success;
      var success = !known.length ? { rate: null, lo: null, hi: null, n: 0, basis: "no run recorded an outcome" }
        : sc && Array.isArray(sc.ci95) && sc.runs === known.length ? { rate: sc.rate, lo: sc.ci95[0], hi: sc.ci95[1], n: sc.runs, basis: "successes over the runs recorded; the scorecard's 95% Wilson interval" }
        : { rate: k / known.length, lo: null, hi: null, n: known.length, basis: "successes over the runs recorded; this output carries no interval for this agent (bundle it for one)" };
      var t = sum(own, function (r) { return r.tokens; }), c = sum(own, function (r) { return r.cost_usd; }), s = sum(own, function (r) { return r.seconds; }), fe = sum(own, function (r) { return r.fetches; });
      var framework = null;
      reports.forEach(function (report) { ["a", "b"].forEach(function (side) { var bl = report && report[side]; if (bl && bl.agent && bl.agent.name === name && bl.harness && bl.harness.adapter && !framework) framework = String(bl.harness.adapter); }); });
      var tasks = {}; own.forEach(function (r) { tasks[r.task] = 1; });
      return { name: name, family: fam, framework: framework, runs: own.length, tasks: Object.keys(tasks).length, success_rate: success,
        tokens_total: t.total, tokens_runs: t.n, cost_usd_total: c.total, cost_runs: c.n, seconds_total: s.total, seconds_runs: s.n, fetches_total: fe.total, fetches_runs: fe.n,
        self_evolving: fam !== null, lineage: fam, synthetic: own.some(function (r) { return r.synthetic; }), members: [label] };
    });
    var t = sum(list, function (r) { return r.tokens; }), c = sum(list, function (r) { return r.cost_usd; }), s = sum(list, function (r) { return r.seconds; }), fe = sum(list, function (r) { return r.fetches; });
    var tasks = {}; list.forEach(function (r) { tasks[r.task] = 1; });
    var totals = { runs: list.length, tasks: Object.keys(tasks).length, agents: agents.length, members: 1, tokens: t.total, tokens_runs: t.n, cost_usd: c.total, cost_runs: c.n,
      seconds: s.total, fetches: fe.total, fetches_runs: fe.n, synthetic_share: list.length ? list.filter(function (r) { return r.synthetic; }).length / list.length : null,
      basis: "sums over the runs that recorded the quantity; *_runs says how many did" };
    var heaviest = list.filter(function (r) { return isNum(r.tokens); }).sort(function (a, b) { return b.tokens - a.tokens || (a.key < b.key ? -1 : 1); }).slice(0, 8)
      .map(function (r) { return { key: r.key, tokens: r.tokens }; });
    var capSrc = (agg.budget || {}).cap;
    var cap = capSrc && isNum(capSrc.value) ? { value: capSrc.value, source: capSrc.source, over: (capSrc.over || []).map(function (o) { return label + "/" + o.task + "/" + o.agent + "/" + o.run; }) } : { value: null, source: "none given", over: [] };
    var kind = co.measurable ? "coevolve" : ev.family ? "evolve" : (agg.stability || agg.reliability) ? "runs" : "batch";
    var sections = ["budget", "fetches"].filter(function (k) { return (agg[k] && agg[k].measurable) || reports.some(function (r) { return r && r[k]; }); });
    var overview = { agents: agents, lineages: lineages, loops: loops, totals: totals, sections: sections, heaviest_runs: heaviest, cap: cap, reading: null };
    var cannot = "This page is one " + kind + " output, not a bundle: levels 1 and 2 are derived here from its scorecard, its budget and fetches ledgers" + (ev.family ? ", its lineage's episodes" : "") + " and its pair reports; level 3 has steps only for the " + plural(list.filter(function (r) { return r.detail; }).length, "run") + " whose report is on this page" + (sections.length < 2 ? "; this output carries no " + ["budget", "fetches"].filter(function (k) { return sections.indexOf(k) < 0; }).join(" or ") + " section, so those numbers are absent (rerun it to add them)" : "") + ". There is no bundle id, no key and no second member to compare: `agentdiff bundle` packs several outputs into one page with all three levels.";
    return {
      source: "page", id: null, name: null, members: [{ index: 0, label: label, kind: kind, runs: list.length }],
      overview: overview, rows: list, budgets: agg.budget && agg.budget.measurable ? { page: agg.budget } : {}, fetchesAgg: agg.fetches && agg.fetches.measurable ? { page: agg.fetches } : {},
      record: function (key) {
        var r = rows[key.split("/").slice(1).join("\u0000")] || null;
        if (!r) return null;
        var d = details[key] || {};
        if (d.block && d.budget && d.fetches) {
          return { measurable: true, reason: null, key: key, steps: d.block.steps.map(function (st) {
            return { index: st.index, type: st.type, name: st.name || "", tokens: st.tokens, tokens_basis: st.tokens_basis || null, latency_s: st.latency_s, error: st.error, effect: st.effect,
              reward: st.reward, value: st.value, input_chars: (st.input || "").length, output_chars: (st.output || "").length, span: st.span && typeof st.span === "object" ? st.span.agent || null : null, quality: st.quality || null };
          }), budget: d.budget, fetches: d.fetches, timeline: [], reward_basis: "steps as recorded", report: "report_" + d.task.replace(/[^A-Za-z0-9._-]+/g, "_") + ".json", side: d.side, success: r.success };
        }
        var reason = d.block ? "this report carries no budget or fetches section for the run; rerun the command to add them"
          : "the run's steps are not in this output: a runs layout keeps one representative pair per task, and a lineage keeps its episodes' timelines";
        return { measurable: false, reason: reason, key: key, steps: [], budget: { measurable: false, reason: reason, tokens: null }, fetches: { measurable: false, reason: reason, records: [] },
          timeline: d.timeline || [], reward_basis: d.timeline ? "the lineage's episode timeline" : null };
      },
      cannot: cannot,
    };
  }

  // ----------------------------------------------------- the visible rows

  function sortRows(rows, sortKey) {
    var field = (SORTS.filter(function (s) { return s[0] === sortKey; })[0] || SORTS[0])[1];
    return rows.slice().sort(function (a, b) {
      var av = a[field], bv = b[field], an = isNum(av), bn = isNum(bv);
      if (an !== bn) return an ? -1 : 1;
      if (an && av !== bv) return bv - av;
      return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
    });
  }
  function filterRows(m, st) {
    return m.rows.filter(function (r) {
      if (st.agent !== null && r.agent !== st.agent) return false;
      if (st.task !== null && r.task !== st.task) return false;
      if (st.member !== null && r.member !== st.member) return false;
      if (st.outcome === "success" && r.success !== true) return false;
      if (st.outcome === "fail" && r.success !== false) return false;
      if (st.outcome === "unknown" && typeof r.success === "boolean") return false;
      return true;
    });
  }
  function visibleRows(m, st) { return sortRows(filterRows(m, st), st.sort); }
  function filterWords(st) {
    var parts = [];
    if (st.member !== null) parts.push("member " + st.member);
    if (st.agent !== null) parts.push("agent " + st.agent);
    if (st.task !== null) parts.push("task " + st.task);
    if (st.outcome !== null) parts.push(st.outcome === "success" ? "succeeded" : st.outcome === "fail" ? "failed" : "outcome unrecorded");
    return parts.join(", ");
  }
  //: the run in view: the family's, else the heaviest run whose steps are in the output
  function currentRun(m, st) { return st.run !== null && m.byKey[st.run] ? st.run : m.defaultRun; }
  function openRun(key) {
    select({ run: key });
    var target = document.querySelector('#stacks .block[data-block="lv-run"]');
    if (target) { try { target.scrollIntoView({ behavior: prefersReduced() ? "auto" : "smooth", block: "start" }); } catch (err) { target.scrollIntoView(); } }
  }
  function scrollToRuns() {
    var target = document.querySelector('#stacks .block[data-block="lv-runs"]');
    if (target) { try { target.scrollIntoView({ behavior: prefersReduced() ? "auto" : "smooth", block: "start" }); } catch (err) { target.scrollIntoView(); } }
  }

  // --------------------------------------------------------- css bars

  function tokBar(r, m, w) {
    if (!isNum(r.tokens)) return H("span", { class: "lv-tokbar", title: "tokens not recorded", style: { width: w + "px" } });
    var full = Math.max(1, Math.round(w * r.tokens / m.maxTokens));
    var share = isNum(r.tokens_measured_share) ? Math.max(0, Math.min(1, r.tokens_measured_share)) : 0;
    var solid = Math.round(full * share), rest = full - solid;
    var title = tok(r.tokens) + " tokens: " + (isNum(r.tokens_measured_share) ? pct(r.tokens_measured_share) + " measured" + (rest ? ", the rest estimated or of unknown basis (hatched)" : "") : "basis not recorded (hatched)");
    return H("span", { class: "lv-tokbar", title: title, style: { width: full + "px" } }, [
      solid ? H("span", { class: "solid", style: { width: solid + "px" } }) : null,
      rest ? H("span", { class: "hatch", style: { width: rest + "px" } }) : null]);
  }
  function toolBar(r, m, scale, w) {
    var tools = r.tools || {}, names = Object.keys(tools).sort(function (a, b) { return (tools[b] || 0) - (tools[a] || 0) || (a < b ? -1 : 1); });
    var calls = d3.sum(names.map(function (t) { return tools[t] || 0; }));
    if (!names.length) return H("span", { class: "lv-toolbar", title: "no tool call recorded" });
    var px = w / m.maxCalls;
    return H("span", { class: "lv-toolbar", title: names.map(function (t) { return t + " ×" + tools[t]; }).join(", ") + " (" + plural(calls, "call") + ")" },
      names.map(function (t) { return H("span", { class: "seg", style: { width: Math.max(2, Math.round(px * tools[t])) + "px", background: scale(t) } }); }));
  }

  // ============================================================ level 1

  var SHOW_ALL_AGENTS = false;

  function successCell(a) {
    var s = a.success_rate || {};
    if (!isNum(s.rate)) return H("td", { class: "dim", text: "—", title: s.basis || "no run recorded an outcome" });
    if (!isNum(s.lo) || !isNum(s.hi)) {
      return H("td", { title: s.basis || "" }, [H("b", { text: pct(s.rate) }), " ", H("span", { class: "lv-ci", text: "over " + plural(s.n, "run") + " · no interval in this output" })]);
    }
    var W = 110, svg = L.svg({ viewBox: "0 0 " + W + " 16", width: W, height: 16, class: "lv-iv",
      "aria-label": a.name + ": " + pct(s.rate) + " success over " + plural(s.n, "run") + ", 95% Wilson interval " + pct(s.lo) + " to " + pct(s.hi) });
    var x = d3.scaleLinear().domain([0, 1]).range([4, W - 4]);
    var g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.appendChild(d3.create("svg:line").attr("x1", x(0)).attr("x2", x(1)).attr("y1", 8).attr("y2", 8).attr("class", "rule").node());
    L.glyph.interval(g, x, s.rate, s.lo, s.hi, { y: 8, color: s.rate >= 0.5 ? "var(--good)" : "var(--bad)", r: 3, tick: 3, width: 2 });
    svg.appendChild(g);
    return H("td", { title: s.basis || "" }, [H("span", { style: { display: "inline-flex", alignItems: "center", gap: "6px" } }, [svg, H("span", { class: "lv-ci", text: pct(s.rate) + " [" + pct(s.lo) + ", " + pct(s.hi) + "] n=" + s.n })])]);
  }
  function tokensCell(a, m) {
    if (!isNum(a.tokens_total)) return H("td", { class: "num dim", text: "not recorded", title: "no run of this agent recorded tokens" });
    // the basis: summed across the members' budget aggregates for this agent
    var measured = 0, estimated = 0, unknown = 0, seen = false;
    Object.keys(m.budgets || {}).forEach(function (label) {
      var ag = ((m.budgets[label] || {}).agents || {})[a.name];
      if (ag) { seen = true; measured += ag.measured || 0; estimated += ag.estimated || 0; unknown += ag.unknown || 0; }
    });
    var partial = a.tokens_runs < a.runs ? " over " + a.tokens_runs + " of " + a.runs + " runs" : "";
    var basis = seen ? "measured " + tok(measured) + ", estimated " + tok(estimated) + ", unknown basis " + tok(unknown) : "basis not summarised for this agent";
    var kids = [H("b", { text: tok(a.tokens_total) })];
    if (seen) {
      var t = measured + estimated + unknown, w = 44, solid = t ? Math.round(w * measured / t) : 0;
      kids.push(H("span", { class: "lv-tokbar", title: basis, style: { width: w + "px" } }, [solid ? H("span", { class: "solid", style: { width: solid + "px" } }) : null, w - solid ? H("span", { class: "hatch", style: { width: (w - solid) + "px" } }) : null]));
    }
    if (partial) kids.push(H("span", { class: "lv-ci", text: partial }));
    return H("td", { class: "num", title: basis + partial }, kids);
  }
  function agentsTable(m, host) {
    var o = m.overview, agents = o.agents || [];
    var shown = SHOW_ALL_AGENTS ? agents : agents.slice(0, AGENT_ROWS);
    var table = H("table", { class: "lv-table", "data-agents": agents.length }, [
      H("thead", null, H("tr", null, [H("th", { text: "agent" }), H("th", { class: "num", text: "runs" }), H("th", { class: "num", text: "tasks" }), H("th", { text: "success · 95% Wilson" }),
        H("th", { class: "num", text: "tokens" }), H("th", { class: "num", text: "cost" }), H("th", { class: "num", text: "seconds" }), H("th", { class: "num", text: "fetches" }), H("th", { text: "" })])),
      H("tbody", null, shown.map(function (a) {
        return H("tr", { "data-agent": a.name }, [
          H("td", null, [H("button", { class: "lv-agent", text: a.name, title: "show this agent's runs at level 2", onclick: function () { select({ agent: a.name, task: null }); scrollToRuns(); } }),
            a.self_evolving ? H("span", { class: "lv-evo", text: "↻ " + a.lineage, title: "self-evolving: a generation of the lineage " + a.lineage }) : null,
            a.framework ? H("span", { class: "lv-ci", text: " " + a.framework }) : null]),
          H("td", { class: "num", text: fmtInt(a.runs) }),
          H("td", { class: "num", text: fmtInt(a.tasks) }),
          successCell(a),
          tokensCell(a, m),
          H("td", { class: "num" + (isNum(a.cost_usd_total) ? "" : " dim"), text: isNum(a.cost_usd_total) ? usd(a.cost_usd_total) + (a.cost_runs < a.runs ? " /" + a.cost_runs : "") : "not recorded", title: isNum(a.cost_usd_total) ? "over " + plural(a.cost_runs, "run") + " that recorded a cost" : "no run recorded a cost; unrecorded is not free" }),
          H("td", { class: "num" + (isNum(a.seconds_total) ? "" : " dim"), text: isNum(a.seconds_total) ? secs(a.seconds_total) : "—", title: "over " + plural(a.seconds_runs, "run") }),
          H("td", { class: "num" + (isNum(a.fetches_total) ? "" : " dim"), text: isNum(a.fetches_total) ? fmtInt(a.fetches_total) : "—", title: "over " + plural(a.fetches_runs, "run") }),
          H("td", null, a.synthetic ? H("span", { class: "lv-syn", text: "SYNTHETIC", title: "at least one run of this agent is labelled SYNTHETIC" }) : null),
        ]);
      })),
    ]);
    host.appendChild(H("div", { class: "scroll-x" }, table));
    if (agents.length > AGENT_ROWS) {
      host.appendChild(H("p", { class: "lv-note" }, [H("button", { class: "lv-btn", text: SHOW_ALL_AGENTS ? "show the first " + AGENT_ROWS : "show all " + plural(agents.length, "agent"), onclick: function () { SHOW_ALL_AGENTS = !SHOW_ALL_AGENTS; if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); } })]));
    }
  }

  /* The loop glyph of one lineage: the agent's generations on top (the
   * recommended one filled, the step verdicts between them), the eval's
   * generations beneath, the loop between the two rows with its counts on
   * the arcs — steps trigger eval generations down, closures come back up. */
  function loopGlyph(ln, host) {
    var W = Math.min(width(host), 620), narrow = W < 460;
    var gens = ln.generations || [], ev = ln.eval, evalN = ev ? ev.generations : 0;
    var padL = 54, padR = 16, yA = 26, yE = 82, Hh = ev ? 112 : 56;
    var xA = d3.scalePoint().domain(gens).range([padL + 8, W - padR - 8]);
    var evIds = d3.range(evalN).map(function (i) { return "e" + i; });
    var xE = d3.scalePoint().domain(evIds).range([padL + 8, W - padR - 8]);
    var verdicts = ln.verdicts || {}, vWords = Object.keys(verdicts).map(function (v) { return verdicts[v] + " " + v; }).join(", ");
    var label = "the lineage " + ln.family + ": " + plural(gens.length, "generation") + " (" + (ln.recommended ? ln.recommended + " recommended" : "none recommended") + (vWords ? "; steps " + vWords : "") + ")"
      + (ev ? "; its eval evolved through " + plural(evalN, "generation") + " adopting " + (ev.adopted || []).length + " metrics (" + (ev.adopted || []).join(", ") + "), closing " + plural(ev.closures || 0, "loop") + " (" + (ev.closures_learned || 0) + " on learned metrics)" + (isNum(ev.drift) ? ", drift " + num(ev.drift, 2) + " from the base eval" : "") : "; no co-evolving eval");
    var svg = d3.select(L.svg({ viewBox: "0 0 " + W + " " + Hh, "aria-label": label, class: "lv-loop", style: "width:" + W + "px;max-width:100%", "data-family": ln.family, "data-closures": ev ? (ev.closures || 0) : 0 }));
    svg.append("text").attr("class", "lab dim").attr("x", padL - 8).attr("y", yA + 4).attr("text-anchor", "end").text("agent");
    svg.append("line").attr("class", "rule").attr("x1", xA(gens[0])).attr("x2", xA(gens[gens.length - 1])).attr("y1", yA).attr("y2", yA);
    var steps = ln.steps || [];
    gens.forEach(function (g, i) {
      var rec = g === ln.recommended, x = xA(g);
      svg.append("circle").attr("cx", x).attr("cy", yA).attr("r", rec ? 6 : 4.5).attr("fill", rec ? "var(--accent)" : "var(--surface)").attr("stroke", rec ? "var(--accent)" : "var(--ink-2)").attr("stroke-width", 1.5);
      if (!narrow || i === 0 || i === gens.length - 1 || rec) svg.append("text").attr("class", "lab mono" + (rec ? " strong" : "")).attr("x", x).attr("y", yA - 10).attr("text-anchor", "middle").text(g);
      if (i > 0) {
        var st = steps.filter(function (s) { return s.to === g; })[0], v = st ? st.verdict : null;
        if (v && !narrow) svg.append("text").attr("class", "lab dim").attr("x", (xA(gens[i - 1]) + x) / 2).attr("y", yA + 15).attr("text-anchor", "middle").attr("fill", v === "improved" ? "var(--good)" : v === "regressed" || v === "gamed" || v === "forgot" ? "var(--bad)" : "var(--ink-3)").text(VERDICT_GLYPH[v] || "·")
          .append("title").text(gens[i - 1] + " → " + g + ": " + v);
      }
    });
    if (ev) {
      svg.append("text").attr("class", "lab dim").attr("x", padL - 8).attr("y", yE + 4).attr("text-anchor", "end").text("eval");
      if (evIds.length > 1) svg.append("line").attr("class", "rule").attr("x1", xE(evIds[0])).attr("x2", xE(evIds[evIds.length - 1])).attr("y1", yE).attr("y2", yE);
      evIds.forEach(function (e, i) {
        svg.append("rect").attr("x", xE(e) - 4.5).attr("y", yE - 4.5).attr("width", 9).attr("height", 9).attr("rx", 2).attr("fill", i === 0 ? "var(--surface)" : "var(--accent)").attr("stroke", "var(--accent)").attr("stroke-width", 1.5);
        svg.append("text").attr("class", "lab mono").attr("x", xE(e)).attr("y", yE + 17).attr("text-anchor", "middle").text(e);
      });
      // the loop: down on the right (steps trigger eval generations), up on the left (closures)
      var xr = W - padR - 2, xl = padL + 2;
      var path = d3.path();
      path.moveTo(xr - 14, yA + 6); path.bezierCurveTo(xr + 8, yA + 20, xr + 8, yE - 20, xr - 14, yE - 6);
      svg.append("path").attr("d", path.toString()).attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.2).attr("marker-end", null);
      var up = d3.path();
      up.moveTo(xl + 14, yE - 6); up.bezierCurveTo(xl - 8, yE - 20, xl - 8, yA + 20, xl + 14, yA + 6);
      svg.append("path").attr("d", up.toString()).attr("fill", "none").attr("stroke", "var(--good)").attr("stroke-width", 1.4).attr("stroke-dasharray", "3 3");
      svg.append("text").attr("class", "lab dim").attr("x", W / 2).attr("y", (yA + yE) / 2 + 4).attr("text-anchor", "middle")
        .text((narrow ? "" : plural(steps.length || gens.length - 1, "step") + " ⟶ " + plural(evalN, "eval generation") + " · ") + plural(ev.closures || 0, "closure") + " ⟵ (" + (ev.closures_learned || 0) + " learned)");
    }
    host.appendChild(svg.node());
  }

  AgentDiff.block({
    id: "lv-overview",
    title: "Level 1 · What is running",
    question: "Which agents ran, how well and at what spend, and which of them evolve — with their eval loops?",
    group: "levels",
    size: "wide",
    relevance: function (ctx) { try { return model(ctx).rows.length ? 1 : 0; } catch (err) { return 0; } },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var t0 = global.performance ? performance.now() : Date.now();
      var m = model(ctx), o = m.overview, t = o.totals || {};
      loadState(m);
      var root = H("div", { class: "lv lv-overview", "data-source": m.source });
      el.appendChild(root);
      var evolving = (o.agents || []).filter(function (a) { return a.self_evolving; });
      var lede = o.reading || ((m.members.length === 1 ? "one output" : plural(m.members.length, "member")) + ": " + plural(t.agents, "agent") + " over " + plural(t.tasks, "task") + " and " + plural(t.runs, "run") + "; "
        + (isNum(t.tokens) ? tok(t.tokens) + " tokens counted over " + t.tokens_runs + " of them" : "no run recorded tokens") + "; " + (isNum(t.cost_usd) ? usd(t.cost_usd) + " recorded over " + plural(t.cost_runs, "run") : "no run recorded a cost") + "; "
        + (isNum(t.fetches) ? fmtInt(t.fetches) + " fetches over " + t.fetches_runs : "no fetch counted") + "; " + ((o.lineages || []).length ? plural(o.lineages.length, "lineage") + " of " + plural(evolving.length, "self-evolving agent") : "no self-evolving agent")
        + (o.heaviest_runs && o.heaviest_runs.length ? "; the heaviest run is " + o.heaviest_runs[0].key + " at " + tok(o.heaviest_runs[0].tokens) + " tokens" : "") + "; " + (t.synthetic_share ? "SYNTHETIC share " + pct(t.synthetic_share) : "no run is labelled SYNTHETIC") + ".");
      root.appendChild(H("p", { class: "lv-lede" }, [m.source === "bundle" ? H("b", { text: "Bundle " + (m.name || "") + " · " }) : null, lede]));
      if (m.cannot) root.appendChild(H("p", { class: "lv-cannot", text: m.cannot }));
      // the members and the totals line
      var bar = H("div", { class: "lv-bar" });
      if (m.source === "bundle") {
        bar.appendChild(H("span", { class: "lv-chipline", text: (m.id || "").slice(0, 19) + "…", title: m.id || "" }));
        m.members.forEach(function (mb) { bar.appendChild(H("span", { class: "lv-chipline" }, [H("b", { text: mb.label }), " " + mb.kind + " · " + plural(mb.runs, "run") + (mb.lineage ? " · lineage " + mb.lineage : "")])); });
      }
      root.appendChild(bar);
      var totals = H("div", { class: "lv-bar lv-totals" }, [
        H("span", { class: "lv-chipline" }, [H("b", { text: fmtInt(t.runs) }), " runs"]), H("span", { class: "lv-chipline" }, [H("b", { text: fmtInt(t.tasks) }), " tasks"]), H("span", { class: "lv-chipline" }, [H("b", { text: fmtInt(t.agents) }), " agents"]),
        H("span", { class: "lv-chipline", title: t.basis || "" }, [H("b", { text: tok(t.tokens) }), " tokens over " + (t.tokens_runs || 0) + " runs"]),
        H("span", { class: "lv-chipline" }, isNum(t.cost_usd) ? [H("b", { text: usd(t.cost_usd) }), " over " + (t.cost_runs || 0) + " runs"] : ["cost not recorded (unrecorded is not free)"]),
        H("span", { class: "lv-chipline" }, [H("b", { text: isNum(t.seconds) ? secs(t.seconds) : "—" }), " summed"]),
        H("span", { class: "lv-chipline" }, [H("b", { text: tok(t.fetches) }), " fetches over " + (t.fetches_runs || 0) + " runs"]),
        H("span", { class: "lv-chipline" + (t.synthetic_share ? " lv-syn" : "") }, t.synthetic_share ? ["SYNTHETIC " + pct(t.synthetic_share) + " of runs"] : ["no run labelled SYNTHETIC"]),
        o.cap && isNum(o.cap.value) ? H("span", { class: "lv-chipline" }, [H("b", { text: fmtInt((o.cap.over || []).length) }), " runs over the cap of " + tok(o.cap.value) + " (" + o.cap.source + ")"]) : null,
      ]);
      root.appendChild(totals);
      root.appendChild(H("p", { class: "lv-h", text: "agents · " + plural((o.agents || []).length, "row") }));
      agentsTable(m, root);
      // the lineages and their loops
      var lns = o.lineages || [];
      if (lns.length) {
        root.appendChild(H("p", { class: "lv-h", text: "self-evolving lineages · " + plural(lns.length, "lineage") + ", " + plural(evolving.length, "agent") }));
        var cols = H("div", { class: "lv-cols" });
        lns.forEach(function (ln) {
          var card = H("div", { class: "lv-lineage", "data-family": ln.family });
          var host = H("div", { class: "lv-loop-host" });
          card.appendChild(host);
          L.layout.responsive(host, function () { loopGlyph(ln, host); }, "lv-loop-" + ln.family);
          var loop = (o.loops || []).filter(function (lp) { return lp.agent_family === ln.family; })[0];
          card.appendChild(H("p", { class: "lv-read" }, [H("b", { text: ln.family }), " · " + plural(ln.generations_n, "generation") + ", " + (ln.recommended ? ln.recommended + " recommended" : "none recommended") + (ln.best ? ", " + ln.best + " best" : "")
            + (ln.eval ? " · eval: " + plural(ln.eval.generations, "generation") + ", adopted " + ((ln.eval.adopted || []).join(", ") || "nothing") + ", " + plural(ln.eval.closures || 0, "closure") + " (" + (ln.eval.closures_learned || 0) + " learned)" + (ln.eval.recommended && ln.eval.recommended.base ? "; the base eval recommends " + ln.eval.recommended.base + ", the evolved " + ln.eval.recommended.evolved + (ln.eval.recommended.agree ? " (they agree)" : " (they disagree)") : "") : " · no co-evolving eval") + (ln.eval && ln.eval.synthetic ? " · SYNTHETIC" : "")]));
          if (loop && loop.reading) card.appendChild(H("details", { class: "lv-details" }, [H("summary", { text: "the eval's reading" }), H("p", { class: "lv-read", text: loop.reading })]));
          if (ln.integrity) card.appendChild(H("details", { class: "lv-details" }, [H("summary", { text: "integrity" }), H("p", { class: "lv-read", text: ln.integrity })]));
          cols.appendChild(card);
        });
        root.appendChild(cols);
      }
      root.appendChild(H("p", { class: "lv-note", text: "Every number is a count or a sum over the runs that recorded it (a sum says how many did); a success rate is drawn with its 95% Wilson interval when the output carries one, never as a bare point; the token bar is solid for measured counts and hatched for estimated or unknown basis; ↻ marks a generation of a self-evolving lineage; SYNTHETIC is carried per run. Sections present: " + ((o.sections || []).join(", ") || "none") + "." }));
      TIMING.overview = (global.performance ? performance.now() : Date.now()) - t0;
      root.setAttribute("data-draw-ms", TIMING.overview.toFixed(1));
    },
  });

  // ============================================================ level 2

  function facetControl(m, st, name, values, host, wordOf) {
    if (!values.length || (name === "member" && values.length < 2)) return;
    var line = H("span", { class: "lv-facet", "data-facet": name });
    line.appendChild(H("span", { text: name + " " }));
    var cur = st[name];
    if (values.length <= CHIP_MAX) {
      line.appendChild(H("button", { class: "lv-btn", text: "all", "aria-pressed": cur === null ? "true" : "false", onclick: function () { var p = {}; p[name] = null; select(p); } }));
      values.forEach(function (v) {
        line.appendChild(H("button", { class: "lv-btn", text: wordOf ? wordOf(v) : v, title: v, "aria-pressed": cur === v ? "true" : "false", "data-value": v, onclick: function () { var p = {}; p[name] = cur === v ? null : v; select(p); } }));
      });
    } else {
      var sel = H("select", { class: "lv-sel", "aria-label": "filter by " + name, onchange: function () { var p = {}; p[name] = sel.value === "" ? null : sel.value; select(p); } });
      sel.appendChild(H("option", { value: "", text: "all (" + values.length + ")" }));
      values.forEach(function (v) { var opt = H("option", { value: v, text: wordOf ? wordOf(v) : v }); if (cur === v) opt.selected = true; sel.appendChild(opt); });
      line.appendChild(sel);
    }
    host.appendChild(line);
  }

  AgentDiff.block({
    id: "lv-runs",
    title: "Level 2 · Every run",
    question: "Run by run: what did each one cost in tokens, time and fetches, which tools did it call, and where did it err?",
    group: "levels",
    size: "wide",
    relevance: function (ctx) { try { return model(ctx).rows.length ? 0.95 : 0; } catch (err) { return 0; } },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var t0 = global.performance ? performance.now() : Date.now();
      var m = model(ctx);
      loadState(m);
      var root = H("div", { class: "lv lv-runs", "data-rows": m.rows.length });
      el.appendChild(root);
      var scale = toolScale(m.toolNames);
      var controls = H("div", { class: "lv-bar lv-controls" });
      var status = H("p", { class: "lv-status", role: "status", "aria-live": "polite" });
      var legend = H("div", { class: "lv-bar lv-legend" });
      var grid = H("div", { class: "lv-grid" });
      var head = H("div", { class: "lv-head" });
      var vp = H("div", { class: "lv-vp", tabindex: 0, role: "application", "aria-label": "run table, level 2: every run as a row; arrows move, Enter opens the run at level 3, Home and End jump" });
      var spacer = H("div", { class: "lv-spacer" }), win = H("div", { class: "lv-win" });
      spacer.appendChild(win); vp.appendChild(spacer);
      grid.appendChild(head); grid.appendChild(vp);
      root.appendChild(controls); root.appendChild(status); root.appendChild(H("div", { class: "scroll-x" }, grid)); root.appendChild(legend);
      var rows = [], cursor = 0, start = -1, end = -1, raf = 0, drawnFor = null;

      function buildRow(r, i) {
        var st = state(), sel = st.run === r.key || (st.run === null && r.key === m.defaultRun);
        var mark = r.success === true ? "✓" : r.success === false ? "✗" : "?";
        var row = H("div", { class: "lv-row" + (sel ? " sel" : "") + (i === cursor ? " cur" : ""), role: "button", tabindex: 0, "data-key": r.key, "data-i": i, "aria-current": sel ? "true" : null,
          title: r.key + " · " + (r.success === true ? "succeeded" : r.success === false ? "failed" : "outcome unrecorded") + " · " + plural(r.steps, "step") + " · " + tok(r.tokens) + " tokens" + (isNum(r.tokens_measured_share) ? " (" + pct(r.tokens_measured_share) + " measured)" : "") + " · cost " + usd(r.cost_usd) + " · " + secs(r.seconds) + " · " + plural(r.fetches, "fetch") + " · " + plural(r.errors, "error") + " · " + plural(r.repeats, "repeat") + (r.retries ? " · " + plural(r.retries, "retry", "retries") + " re-run by the harness" : "") + (isNum(r["return"]) ? " · return " + num(r["return"], 2) : "") + " · from " + (r.basis || []).join(", ") + (r.detail ? " · steps in the output" : " · steps not in the output"),
          onclick: function () { cursor = i; openRun(r.key); },
          onkeydown: function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); cursor = i; openRun(r.key); } } }, [
          H("span", { class: "lv-mark " + (r.success === true ? "ok" : r.success === false ? "ko" : "na"), text: mark, "aria-label": r.success === true ? "succeeded" : r.success === false ? "failed" : "outcome unrecorded" }),
          H("span", { class: "lv-key" }, [H("b", { text: r.agent }), " ", H("span", { class: "dim", text: short(r.task) + " · " + r.run_id + (r.lineage_gen ? " · " + r.lineage_gen : "") + (m.facets.member.length > 1 ? " · " + r.member : "") }), r.synthetic ? H("span", { class: "lv-syn", text: "SYN", title: "SYNTHETIC" }) : null, r.tiled ? H("span", { class: "dim", text: " (tiled)" }) : null]),
          toolBar(r, m, scale, 100),
          H("span", { class: "lv-tok" }, [H("span", { class: "n", text: tok(r.tokens) }), tokBar(r, m, 84)]),
          H("span", { class: "n" + (isNum(r.cost_usd) ? "" : " dim"), text: usd(r.cost_usd) }),
          H("span", { class: "n", text: secs(r.seconds) }),
          H("span", { class: "n", text: tok(r.fetches) }),
          H("span", { class: "n" + (r.errors > 0 ? " bad" : ""), text: tok(r.errors) }),
        ]);
        return row;
      }
      function paint(force) {
        var n = rows.length, top = vp.scrollTop, vh = vp.clientHeight || VP_ROWS * ROW_H;
        var s = Math.max(0, Math.floor(top / ROW_H) - OVERSCAN), e = Math.min(n, Math.ceil((top + vh) / ROW_H) + OVERSCAN);
        if (!force && s === start && e === end) return;
        start = s; end = e;
        win.style.top = (s * ROW_H) + "px";
        win.textContent = "";
        var frag = document.createDocumentFragment();
        for (var i = s; i < e; i++) frag.appendChild(buildRow(rows[i], i));
        win.appendChild(frag);
        vp.setAttribute("data-dom-rows", String(e - s));
        vp.setAttribute("data-window", s + "-" + e);
      }
      function ensureVisible(i) {
        var vh = vp.clientHeight || VP_ROWS * ROW_H;
        if (i * ROW_H < vp.scrollTop) vp.scrollTop = i * ROW_H;
        else if ((i + 1) * ROW_H > vp.scrollTop + vh) vp.scrollTop = (i + 1) * ROW_H - vh;
      }
      function rebuild() {
        var st = state(), tr = global.performance ? performance.now() : Date.now();
        rows = visibleRows(m, st);
        var key = st.sort + "|" + filterWords(st) + "|" + rows.length;
        if (drawnFor !== key) { cursor = 0; }
        var selIdx = st.run !== null ? rows.findIndex(function (r) { return r.key === st.run; }) : -1;
        if (drawnFor !== key && selIdx >= 0) cursor = selIdx;
        drawnFor = key;
        spacer.style.height = (rows.length * ROW_H) + "px";
        vp.style.height = (Math.min(rows.length, VP_ROWS) * ROW_H) + "px";
        vp.setAttribute("data-rows", rows.length);
        // the header: sortable columns
        head.textContent = "";
        [["", null], ["run", null], ["tool calls by tool", null], ["tokens", "tokens"], ["cost", "cost"], ["secs", "seconds"], ["fetch", "fetches"], ["err", "errors"]].forEach(function (c, i) {
          var cls = i >= 3 ? "n" : "";
          if (!c[1]) { head.appendChild(H("span", { class: cls, text: c[0] })); return; }
          head.appendChild(H("span", { class: cls }, H("button", { text: c[0] + (st.sort === c[1] ? " ↓" : ""), "aria-pressed": st.sort === c[1] ? "true" : "false", "data-sort": c[1], title: "sort by " + c[1] + ", descending, unrecorded last", onclick: function () { select({ sort: c[1] }); } })));
        });
        // controls
        controls.textContent = "";
        var sorts = H("span", { class: "lv-facet", "data-facet": "sort" }, [H("span", { text: "sort " })]);
        SORTS.forEach(function (s) { sorts.appendChild(H("button", { class: "lv-btn", text: s[0], "data-sort": s[0], "aria-pressed": st.sort === s[0] ? "true" : "false", onclick: function () { select({ sort: s[0] }); } })); });
        controls.appendChild(sorts);
        facetControl(m, st, "member", m.facets.member, controls);
        facetControl(m, st, "agent", m.facets.agent, controls);
        facetControl(m, st, "task", m.facets.task, controls, short);
        var oc = H("span", { class: "lv-facet", "data-facet": "outcome" }, [H("span", { text: "outcome " })]);
        [[null, "all"], ["success", "✓ succeeded"], ["fail", "✗ failed"], ["unknown", "? unrecorded"]].forEach(function (o) {
          oc.appendChild(H("button", { class: "lv-btn", text: o[1], "data-value": o[0] === null ? "all" : o[0], "aria-pressed": st.outcome === o[0] ? "true" : "false", onclick: function () { select({ outcome: o[0] }); } }));
        });
        controls.appendChild(oc);
        if (st.agent !== null || st.task !== null || st.member !== null || st.outcome !== null) controls.appendChild(H("button", { class: "lv-btn", text: "clear filters", onclick: function () { select({ agent: null, task: null, member: null, outcome: null }); } }));
        start = -1; end = -1;
        vp.scrollTop = 0;
        paint(true);
        if (selIdx >= 0) ensureVisible(selIdx);
        var ms = (global.performance ? performance.now() : Date.now()) - tr;
        var ok = rows.filter(function (r) { return r.success === true; }).length, ko = rows.filter(function (r) { return r.success === false; }).length;
        var sumT = sum(rows, function (r) { return r.tokens; });
        status.textContent = plural(m.rows.length, "run") + (m.tiled ? " (the " + (m.rows.length / m.tiled) + " of the page tiled ×" + m.tiled + " for measurement)" : "") + " · " + (rows.length === m.rows.length ? "all shown" : rows.length + " shown (" + filterWords(st) + ")")
          + " · " + ok + " succeeded, " + ko + " failed, " + (rows.length - ok - ko) + " unrecorded · " + (isNum(sumT.total) ? tok(sumT.total) + " tokens over " + sumT.n : "no tokens recorded") + " · sorted by " + st.sort + ", descending, unrecorded last · " + (end - start) + " of " + rows.length + " rows in the DOM · drawn in " + ms.toFixed(1) + " ms";
        root.setAttribute("data-draw-ms", ms.toFixed(1));
        TIMING.runs = ms;
      }
      vp.addEventListener("scroll", function () { if (raf) return; raf = requestAnimationFrame(function () { raf = 0; paint(false); }); });
      vp.addEventListener("keydown", function (evt) {
        var n = rows.length; if (!n) return;
        var k = evt.key, moved = false;
        if (k === "ArrowDown") { cursor = Math.min(n - 1, cursor + 1); moved = true; }
        else if (k === "ArrowUp") { cursor = Math.max(0, cursor - 1); moved = true; }
        else if (k === "PageDown") { cursor = Math.min(n - 1, cursor + VP_ROWS); moved = true; }
        else if (k === "PageUp") { cursor = Math.max(0, cursor - VP_ROWS); moved = true; }
        else if (k === "Home") { cursor = 0; moved = true; }
        else if (k === "End") { cursor = n - 1; moved = true; }
        else if (k === "Enter" && evt.target === vp) { evt.preventDefault(); openRun(rows[cursor].key); return; }
        if (!moved) return;
        evt.preventDefault();
        ensureVisible(cursor);
        paint(true);
        var r = rows[cursor];
        status.textContent = "row " + (cursor + 1) + " of " + n + ": " + r.key + " · " + tok(r.tokens) + " tokens · " + plural(r.fetches, "fetch") + " · " + plural(r.errors, "error") + " · Enter opens it at level 3";
        if (evt.target !== vp) vp.focus();
      });
      // the legend: the tool colours, the token bar's two fills
      legend.appendChild(H("span", { text: "tools:" }));
      m.toolNames.slice(0, 12).forEach(function (t) { legend.appendChild(H("span", { class: "lv-chipline" }, [H("i", { style: { background: scale(t) } }), t + " ×" + fmtInt(m.toolTotals[t])])); });
      if (m.toolNames.length > 12) legend.appendChild(H("span", { text: "+" + (m.toolNames.length - 12) + " more tools" }));
      legend.appendChild(H("span", { class: "lv-chipline" }, [H("span", { class: "sw", style: { background: "var(--ink-2)" } }), "measured tokens"]));
      legend.appendChild(H("span", { class: "lv-chipline" }, [H("span", { class: "sw", style: { background: "repeating-linear-gradient(45deg,var(--ink-3) 0 2px,transparent 2px 5px)" } }), "estimated or unknown basis"]));
      rebuild();
      listen(el, function () { rebuild(); });
      root.appendChild(H("p", { class: "lv-note", text: "Only the rows in the viewport (plus " + OVERSCAN + " either side) are in the DOM; the bars are widths against the largest run on the page (" + fmtInt(m.maxCalls) + " tool calls, " + tok(m.maxTokens) + " tokens); a number a run did not record is — and sorts last, never 0. Every number comes from the source the row names in its tooltip (the scorecard's per-run rows, the budget and fetches ledgers, a lineage's episodes, a pair report). Click a row, or press Enter on it, to open the run at level 3." }));
      TIMING.runs = (global.performance ? performance.now() : Date.now()) - t0;
      root.setAttribute("data-draw-ms", TIMING.runs.toFixed(1));
    },
  });

  // ============================================================ level 3

  /* The burn-down: every step a column from the cumulative total before it
   * to the total after it, coloured by kind; a step whose count is not
   * measured hatched; an errored step outlined; the answer marked; the
   * heaviest steps labelled; the stretch after the last evidence signal
   * shaded as waste. x is steps, or wall-clock from the steps' latencies. */
  function burnDown(rec, host, xmode, narrow) {
    var b = rec.budget, steps = rec.steps || [], byIndex = {};
    steps.forEach(function (s) { byIndex[s.index] = s; });
    var burn = (b.burn || []).map(function (r) { var s = byIndex[r[0]] || {}; return { index: r[0], kind: r[1], name: r[2], tokens: r[3], cum: r[4], basis: s.tokens_basis || null, error: !!s.error, reward: s.reward, latency: isNum(s.latency_s) && s.latency_s > 0 ? s.latency_s : 0, quality: s.quality || null }; });
    var total = b.tokens ? b.tokens.total : 0, n = burn.length;
    var used = {};
    ((rec.fetches || {}).records || []).forEach(function (r) { if (r.used === true) used[r.index] = true; });
    var lastEvidence = -1, answerIdx = -1;
    burn.forEach(function (r) { if ((isNum(r.reward) && r.reward > 0) || r.quality === "good" || used[r.index]) lastEvidence = Math.max(lastEvidence, r.index); if (r.kind === "answer") answerIdx = r.index; });
    // bins past the column cap: consecutive steps merged, the heaviest kind named
    var cols = burn, binned = 0;
    if (n > BURN_COLS) {
      var per = Math.ceil(n / BURN_COLS); cols = [];
      for (var i = 0; i < n; i += per) {
        var chunk = burn.slice(i, i + per), kinds = {}, tk = 0, err = false, unm = false, lat = 0;
        chunk.forEach(function (r) { kinds[r.kind] = (kinds[r.kind] || 0) + r.tokens; tk += r.tokens; err = err || r.error; unm = unm || r.basis !== "measured"; lat += r.latency; });
        var top = Object.keys(kinds).sort(function (a, c) { return kinds[c] - kinds[a]; })[0] || chunk[0].kind;
        cols.push({ index: chunk[0].index, last: chunk[chunk.length - 1].index, kind: top, name: chunk.length + " steps", tokens: tk, cum: chunk[chunk.length - 1].cum, basis: unm ? "mixed" : "measured", error: err, latency: lat, n: chunk.length });
      }
      binned = per;
    }
    var W = width(host), padL = 52, padR = 12, padT = 30, padB = 30, Hh = narrow ? 180 : 210;
    var xs = [], t = 0;
    cols.forEach(function (c) { xs.push(t); t += xmode === "time" ? c.latency : 1; });
    xs.push(t);
    var x = d3.scaleLinear().domain([0, t || 1]).range([padL, W - padR]);
    var y = d3.scaleLinear().domain([0, Math.max(1, total)]).nice().range([Hh - padB, padT]);
    var unmeasured = burn.filter(function (r) { return r.basis !== "measured"; }).length, estimated = burn.filter(function (r) { return r.basis === "estimated"; }).length;
    var waste = b.waste || {}, wasteTok = waste.after_last_evidence;
    var topSteps = (b.top || []).slice(0, narrow ? 2 : 4);
    var label = "token burn-down of " + rec.key + ": " + tok(total) + " cumulative tokens over " + plural(n, "step") + " by " + (xmode === "time" ? "wall-clock seconds from the steps' latencies (" + secs(t) + ")" : "step") + ", each step a column coloured by kind"
      + (unmeasured ? "; " + unmeasured + " steps hatched (" + estimated + " estimated, " + (unmeasured - estimated) + " of unknown basis)" : "; every step measured")
      + (answerIdx >= 0 ? "; the answer at step " + answerIdx : "; no answer step")
      + (isNum(wasteTok) ? "; " + tok(wasteTok) + " tokens after the last evidence signal shaded as waste" : "; no evidence signal recorded, so the waste after the last one is unmeasurable")
      + (topSteps.length ? "; heaviest: " + topSteps.map(function (s) { return "#" + s.index + " " + s.name + " " + tok(s.tokens); }).join(", ") : "") + (binned ? "; steps binned " + binned + " to a column past " + BURN_COLS : "");
    var svg = d3.select(L.svg({ viewBox: "0 0 " + W + " " + Hh, "aria-label": label, class: "lv-burn", "data-steps": n, "data-columns": cols.length, "data-x": xmode }));
    var uid = ++UID, defs = svg.append("defs");
    defs.append("pattern").attr("id", "lv-hatch-" + uid).attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)")
      .append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "var(--bg)").attr("stroke-width", 2.2);
    defs.append("pattern").attr("id", "lv-waste-" + uid).attr("width", 6).attr("height", 6).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(-45)")
      .append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6).attr("stroke", "var(--bad)").attr("stroke-width", 1.4).attr("stroke-opacity", 0.55);
    // axes
    var yt = y.ticks(narrow ? 3 : 5);
    yt.forEach(function (v) {
      svg.append("line").attr("class", "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(v)).attr("y2", y(v)).attr("stroke-dasharray", "1 3");
      svg.append("text").attr("class", "tick").attr("x", padL - 6).attr("y", y(v) + 4).attr("text-anchor", "end").text(fmtInt(v));
    });
    svg.append("line").attr("class", "zero").attr("x1", padL).attr("x2", W - padR).attr("y1", y(0)).attr("y2", y(0));
    var xt = x.ticks(narrow ? 4 : 8);
    xt.forEach(function (v) { svg.append("text").attr("class", "tick").attr("x", x(v)).attr("y", Hh - padB + 14).attr("text-anchor", "middle").text(xmode === "time" ? num(v, 0) + "s" : fmtInt(v)); });
    svg.append("text").attr("class", "lab dim").attr("x", W - padR).attr("y", Hh - 4).attr("text-anchor", "end").text(xmode === "time" ? "seconds (from the steps' latencies)" : "step");
    svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 12).text("cumulative tokens");
    // the waste band
    if (isNum(wasteTok) && wasteTok > 0 && lastEvidence >= 0) {
      var i0 = cols.findIndex(function (c) { return c.index > lastEvidence; }), i1 = answerIdx >= 0 ? cols.findIndex(function (c) { return c.index >= answerIdx; }) : cols.length;
      if (i0 >= 0 && i1 > i0) {
        svg.append("rect").attr("class", "lv-waste").attr("x", x(xs[i0])).attr("y", padT).attr("width", Math.max(1, x(xs[i1]) - x(xs[i0]))).attr("height", Hh - padB - padT).attr("fill", "url(#lv-waste-" + uid + ")");
        svg.append("text").attr("class", "lab dim").attr("x", (x(xs[i0]) + x(xs[i1])) / 2).attr("y", padT - 4).attr("text-anchor", "middle").attr("fill", "var(--bad)").text(narrow ? "waste " + tok(wasteTok) : "after the last evidence: " + tok(wasteTok) + " tokens");
      }
    }
    // the columns
    var g = svg.append("g").attr("class", "lv-cols");
    cols.forEach(function (c, i) {
      var x0 = x(xs[i]), x1 = x(xs[i + 1]), w = Math.max(0.6, x1 - x0);
      var yTop = y(c.cum), yBot = y(c.cum - c.tokens);
      if (c.tokens > 0) {
        g.append("rect").attr("x", x0).attr("y", yTop).attr("width", w).attr("height", Math.max(0.5, yBot - yTop)).attr("fill", kindColour(c.kind)).attr("stroke", c.error ? "var(--bad)" : null).attr("stroke-width", c.error ? 1.2 : null).attr("data-index", c.index).attr("data-kind", c.kind);
        if (c.basis !== "measured") g.append("rect").attr("class", "lv-unmeasured").attr("x", x0).attr("y", yTop).attr("width", w).attr("height", Math.max(0.5, yBot - yTop)).attr("fill", "url(#lv-hatch-" + uid + ")").attr("pointer-events", "none");
      }
    });
    // the cumulative line
    var line = d3.line().x(function (d, i) { return x(xs[i + 1]); }).y(function (d) { return y(d.cum); });
    svg.append("path").attr("d", line(cols)).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1).attr("stroke-opacity", 0.6);
    // the answer
    if (answerIdx >= 0) {
      var ai = cols.findIndex(function (c) { return c.index >= answerIdx; });
      if (ai >= 0) {
        svg.append("line").attr("x1", x(xs[ai])).attr("x2", x(xs[ai])).attr("y1", padT - 2).attr("y2", Hh - padB).attr("stroke", "var(--ink)").attr("stroke-dasharray", "2 2");
        svg.append("text").attr("class", "lab strong").attr("x", Math.min(x(xs[ai]) + 4, W - padR - 40)).attr("y", Hh - padB - 6).text("answer" + (rec.success === true ? " ✓" : rec.success === false ? " ✗" : ""));
      }
    }
    // the heaviest steps: a stack of labels at the top left, each with a dotted leader down to its column
    topSteps.forEach(function (s, k) {
      var ci = cols.findIndex(function (c) { return c.index <= s.index && (c.last === undefined ? c.index === s.index : c.last >= s.index); });
      if (ci < 0) return;
      var cx = (x(xs[ci]) + x(xs[ci + 1])) / 2, ly = padT + 4 + k * 13;
      svg.append("line").attr("x1", cx).attr("x2", cx).attr("y1", ly + 2).attr("y2", y(cols[ci].cum) - 1).attr("stroke", "var(--ink-3)").attr("stroke-dasharray", "1 2").attr("stroke-opacity", 0.8);
      svg.append("circle").attr("cx", cx).attr("cy", y(cols[ci].cum) - 1).attr("r", 2).attr("fill", "var(--ink)");
      svg.append("text").attr("class", "lab").attr("x", cx + 4).attr("y", ly + 4).attr("text-anchor", cx > W - 120 ? "end" : "start").attr("dx", cx > W - 120 ? -8 : 0).text("#" + s.index + " " + trunc(s.name, 12) + " " + tok(s.tokens) + (isNum(s.share) ? " (" + pct(s.share) + ")" : ""));
    });
    // hover: the column under the pointer, by bisection
    var tip = L.svg.tip(host, { class: "lv-tip" });
    var bis = d3.bisector(function (v) { return v; }).right;
    svg.on("mousemove", function (evt) {
      var p = d3.pointer(evt, svg.node()), i = bis(xs, x.invert(p[0])) - 1;
      if (i < 0 || i >= cols.length) { tip.hide(); return; }
      var c = cols[i];
      tip.show(evt, [{ b: true, text: (c.n ? "steps " + c.index + "–" + c.last : "step " + c.index + " · " + c.kind + " · " + c.name) },
        { mono: true, text: tok(c.tokens) + " tokens (" + (c.basis || "unknown basis") + ") · cumulative " + tok(c.cum) + " of " + tok(total) },
        c.error ? { text: "errored" } : null, isNum(c.reward) ? { mono: true, text: "reward " + num(c.reward, 2) } : null, c.latency ? { mono: true, text: secs(c.latency) } : null,
        c.index > lastEvidence && lastEvidence >= 0 && c.kind !== "answer" ? { text: "after the last evidence signal" } : null]);
    }).on("mouseleave", function () { tip.hide(); });
    host.appendChild(svg.node());
    var leg = H("div", { class: "lv-bar" });
    KINDS.forEach(function (k) { if (b.tokens && b.tokens.by_kind && b.tokens.by_kind[k]) leg.appendChild(H("span", { class: "lv-chipline" }, [H("i", { style: { background: kindColour(k) } }), k + " " + tok(b.tokens.by_kind[k])])); });
    leg.appendChild(H("span", { class: "lv-chipline" }, [H("span", { class: "sw", style: { background: "repeating-linear-gradient(45deg,var(--ink-3) 0 2px,transparent 2px 5px)" } }), "estimated or unknown basis (" + unmeasured + " steps)"]));
    leg.appendChild(H("span", { class: "lv-chipline" }, [H("span", { class: "sw", style: { background: "repeating-linear-gradient(-45deg,var(--bad) 0 1px,transparent 1px 4px)" } }), "after the last evidence"]));
    host.appendChild(leg);
    return { binned: binned, n: n };
  }

  /* Where the budget went: two stacked bars, by kind and by tool, each
   * segment's width the share of the run's total. */
  function budgetBars(rec, host, narrow) {
    var b = rec.budget, tk = b.tokens || {}, total = tk.total || 0;
    var W = width(host), padL = narrow ? 44 : 64, padR = 12, rowH = 18, gap = 34;
    var kinds = KINDS.filter(function (k) { return (tk.by_kind || {})[k] > 0; });
    var tools = Object.keys(tk.by_tool || {}).filter(function (t) { return tk.by_tool[t] > 0; }).sort(function (a, c) { return tk.by_tool[c] - tk.by_tool[a] || (a < c ? -1 : 1); });
    var scale = toolScale(tools);
    var label = "where the budget of " + rec.key + " went: " + tok(total) + " tokens; by kind " + kinds.map(function (k) { return k + " " + tok(tk.by_kind[k]) + " (" + pct(tk.by_kind[k] / total) + ")"; }).join(", ")
      + (tools.length ? "; by tool " + tools.map(function (t) { return t + " " + tok(tk.by_tool[t]) + " (" + pct(tk.by_tool[t] / total) + ")"; }).join(", ") : "; no tool carried tokens")
      + "; measured " + tok(tk.measured) + ", estimated " + tok(tk.estimated) + ", unknown basis " + tok(tk.unknown);
    var Hh = 2 * gap + 8;
    var svg = d3.select(L.svg({ viewBox: "0 0 " + W + " " + Hh, "aria-label": label, class: "lv-budget" }));
    var x = d3.scaleLinear().domain([0, Math.max(1, total)]).range([padL, W - padR]);
    function bar(y, items, valueOf, colourOf, name) {
      svg.append("text").attr("class", "lab dim").attr("x", padL - 6).attr("y", y + rowH / 2 + 4).attr("text-anchor", "end").text(name);
      var acc = 0;
      items.forEach(function (it) {
        var v = valueOf(it), x0 = x(acc), x1 = x(acc + v);
        svg.append("rect").attr("x", x0).attr("y", y).attr("width", Math.max(0.5, x1 - x0)).attr("height", rowH).attr("fill", colourOf(it)).append("title").text(it + ": " + tok(v) + " tokens, " + pct(v / total));
        var full = it + " " + pct(v / total), brief = pct(v / total), w = x1 - x0;
        var text = w > full.length * 6.6 + 6 ? full : w > brief.length * 6.6 + 6 ? brief : null;
        if (text) svg.append("text").attr("class", "lab").attr("x", (x0 + x1) / 2).attr("y", y + rowH / 2 + 4).attr("text-anchor", "middle").attr("fill", "var(--bg)").text(text);
        acc += v;
      });
      var rest = tok(total - acc) + " in no " + (name === "by tool" ? "tool" : "kind");
      if (acc < total && (W - padR - x(acc)) > rest.length * 6.6 + 6) svg.append("text").attr("class", "lab dim").attr("x", x(acc) + 4).attr("y", y + rowH / 2 + 4).text(rest);
    }
    bar(4, kinds, function (k) { return tk.by_kind[k]; }, kindColour, "by kind");
    if (tools.length) bar(4 + gap, tools, function (t) { return tk.by_tool[t]; }, scale, "by tool");
    else svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 4 + gap + rowH / 2 + 4).text("no fetch or tool call carried tokens");
    host.appendChild(svg.node());
    var io = b.io || {}, cost = b.cost_usd || {}, ps = b.per_second || {}, w = b.waste || {};
    host.appendChild(H("p", { class: "lv-read" }, [
      H("b", { text: tok(total) + " tokens" }), " — measured " + tok(tk.measured) + ", estimated " + tok(tk.estimated) + ", unknown basis " + tok(tk.unknown) + (tk.unknown_steps ? " (" + tk.unknown_steps + " steps carried no count)" : "") + " · ",
      io.measurable ? "input " + tok(io.input_tokens) + ", output " + tok(io.output_tokens) + " (from the totals)" : "input/output not recorded (" + (io.reason || "no totals") + ")", " · ",
      cost.measurable ? "cost " + usd(cost.value) + " (from the totals)" : "cost not recorded (unrecorded is not free)", " · ",
      ps.measurable ? num(ps.value, 1) + " tokens/s over " + secs(ps.seconds) : "no latency recorded", " · waste: ",
      isNum(w.after_last_evidence) ? tok(w.after_last_evidence) + " after the last evidence" : "after the last evidence unmeasurable (no evidence signal recorded)", ", " + tok(w.in_errored_calls) + " in errored calls, " + tok(w.in_repeats) + " in repeated fetches.",
    ]));
  }

  /* The search map: the fetches as a flow, left to right — the searches,
   * what they yielded (results and tool calls), what was read, the answer.
   * Node radius ∝ √output chars; a `reaches` edge only where use is
   * recorded; unknown use dashed and counted. */
  function searchMap(rec, host, narrow, status) {
    var f = rec.fetches || {}, map = f.map || { nodes: [], edges: [] }, recs = f.records || [], byIdx = {};
    recs.forEach(function (r) { byIdx[r.index] = r; });
    var nodesAll = (map.nodes || []).slice(), capped = 0;
    if (nodesAll.length > MAP_CAP) { capped = nodesAll.length - MAP_CAP; nodesAll = nodesAll.filter(function (n) { return n.kind === "answer"; }).concat(nodesAll.filter(function (n) { return n.kind !== "answer"; }).slice(0, MAP_CAP - 1)); }
    var byId = {}; nodesAll.forEach(function (n) { byId[n.id] = n; });
    var edges = (map.edges || []).filter(function (e) { return byId[e.from] && byId[e.to]; });
    var COLS = [["query", "searches"], ["result", "results · calls"], ["read", "reads"], ["answer", "answer"]];
    var colOf = function (n) { return n.kind === "query" ? 0 : n.kind === "result" || n.kind === "call" ? 1 : n.kind === "read" ? 2 : 3; };
    var cols = [[], [], [], []];
    nodesAll.forEach(function (n) { cols[colOf(n)].push(n); });
    cols.forEach(function (c) { c.sort(function (a, b) { return (a.index || 0) - (b.index || 0); }); });
    var live = COLS.map(function (c, i) { return i; }).filter(function (i) { return cols[i].length; });
    var maxRows = d3.max(cols, function (c) { return c.length; }) || 1;
    var rowH = Math.max(11, Math.min(20, Math.floor(270 / maxRows)));
    var W = width(host), padL = 16, padR = 16, padT = 26, padB = 14, Hh = padT + padB + Math.max(1, maxRows) * rowH;
    var x = d3.scalePoint().domain(live).range([padL + 40, W - padR - 40]);
    var rScale = d3.scaleSqrt().domain([0, d3.max(nodesAll, function (n) { return n.size || 0; }) || 1]).range([2.5, narrow ? 7 : 9]);
    var pos = {};
    live.forEach(function (ci) { var c = cols[ci], y0 = padT + (maxRows - c.length) * rowH / 2; c.forEach(function (n, i) { pos[n.id] = [x(ci), y0 + i * rowH + rowH / 2]; }); });
    var counts = f.counts || {}, unknownUse = isNum(map.unknown_use) ? map.unknown_use : (counts.unknown_use || 0);
    var kinds = {}; edges.forEach(function (e) { kinds[e.kind] = (kinds[e.kind] || 0) + 1; });
    var label = "search map of " + rec.key + ": " + plural(counts.total || nodesAll.length - 1, "fetch") + " left to right — " + cols[0].length + " searches, " + cols[1].length + " results and tool calls, " + cols[2].length + " reads, then the answer; node size ∝ √output chars ("
      + tok((f.volume || {}).output_chars) + " chars in all); edges: " + (kinds.yields || 0) + " yields, " + (kinds.reads || 0) + " reads, " + (kinds.reaches || 0) + " reaches the answer (recorded use only); " + (counts.used || 0) + " fetches used, " + (counts.unused || 0) + " not used, " + unknownUse + " of unknown use drawn without an edge; "
      + (counts.errors || 0) + " errored, " + (counts.repeats || 0) + " repeats"
      + (counts.retries ? ", " + counts.retries + " retries the harness re-ran" : !counts.attempts_numbered ? " (no step numbers its attempt, so a retry here would read as a repeat)" : "") + (capped ? "; " + capped + " nodes past the cap of " + MAP_CAP + " not drawn" : "");
    var svg = d3.select(L.svg({ viewBox: "0 0 " + W + " " + Hh, "aria-label": label, class: "lv-map", role: "application", "data-nodes": nodesAll.length, "data-edges": edges.length, "data-unknown": unknownUse }));
    live.forEach(function (ci) { svg.append("text").attr("class", "lab dim").attr("x", x(ci)).attr("y", 12).attr("text-anchor", "middle").text(COLS[ci][1]); });
    var link = d3.linkHorizontal().x(function (d) { return d[0]; }).y(function (d) { return d[1]; });
    var eg = svg.append("g").attr("class", "lv-edges");
    edges.forEach(function (e) {
      var a = pos[e.from], b = pos[e.to]; if (!a || !b) return;
      var reaches = e.kind === "reaches";
      eg.append("path").attr("d", link({ source: a, target: b })).attr("fill", "none").attr("stroke", reaches ? "var(--good)" : "var(--rule-2)").attr("stroke-width", reaches ? 1.4 : 1).attr("stroke-opacity", reaches ? 0.8 : 0.9).attr("data-kind", e.kind);
    });
    var tip = L.svg.tip(host, { class: "lv-tip" });
    function lines(n) {
      var r = byIdx[n.index] || {};
      if (n.kind === "answer") return [{ b: true, text: "answer" + (rec.success === true ? " ✓ succeeded" : rec.success === false ? " ✗ failed" : "") }, { text: n.label }, { mono: true, text: (n.size || 0) + " chars" }];
      return [{ b: true, text: "#" + n.index + " " + (r.kind || n.kind) + " · " + (r.name || "") }, { text: r.query || n.label }, { mono: true, text: tok(r.output_chars) + " chars out · " + tok(r.tokens) + " tokens" + (r.tokens_basis ? " (" + r.tokens_basis + ")" : "") + (isNum(r.latency_s) ? " · " + secs(r.latency_s) : "") },
        r.error ? { text: "errored" } : null, isNum(r.repeat_of) ? { text: "repeats #" + r.repeat_of } : null,
        isNum(r.attempt) && r.attempt > 1 ? { text: "attempt " + r.attempt + ", re-run by the harness" + (isNum(r.retry_of) ? " after #" + r.retry_of : "") } : null, { text: r.used === true ? "used (" + r.used_basis + ")" : r.used === false ? "not used (" + r.used_basis + ")" : "use unknown: " + (r.used_basis || "no signal recorded") }];
    }
    function words(n) { return lines(n).filter(Boolean).map(function (l) { return l.text; }).join(" · "); }
    var ng = svg.append("g").attr("class", "lv-nodes");
    nodesAll.forEach(function (n) {
      var p = pos[n.id], r = byIdx[n.index] || {}, isAns = n.kind === "answer";
      var fill = isAns ? (rec.success === true ? "var(--good)" : rec.success === false ? "var(--bad)" : "var(--ink-3)") : n.kind === "query" ? "var(--accent)" : n.kind === "read" ? "var(--ink)" : "var(--ink-2)";
      var g = ng.append("g").attr("class", "lv-node").attr("tabindex", 0).attr("role", "button").attr("data-id", n.id).attr("data-kind", n.kind).attr("aria-label", words(n)).attr("transform", "translate(" + p[0] + "," + p[1] + ")");
      g.append("circle").attr("class", "ring").attr("r", rScale(n.size || 0) + 3).attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-opacity", 0);
      g.append("circle").attr("r", rScale(n.size || 0)).attr("fill", fill).attr("fill-opacity", isAns || r.used !== false ? 1 : 0.35)
        .attr("stroke", r.error ? "var(--bad)" : r.used === null || r.used === undefined ? (isAns ? null : "var(--ink-3)") : null).attr("stroke-width", r.error ? 1.6 : 1).attr("stroke-dasharray", !isAns && !r.error && (r.used === null || r.used === undefined) ? "2 2" : null);
      if (isNum(r.repeat_of)) g.append("circle").attr("r", rScale(n.size || 0) + 2.5).attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-dasharray", "1 2");
      if (isNum(r.attempt) && r.attempt > 1) g.append("circle").attr("class", "lv-retry").attr("r", rScale(n.size || 0) + 2.5).attr("fill", "none").attr("stroke", "var(--warn)").attr("stroke-dasharray", "3 2");
      if (!narrow || isAns || n.kind === "query") g.append("text").attr("class", "lab mono" + (isAns ? " strong" : " dim")).attr("x", isAns ? 0 : rScale(n.size || 0) + 4).attr("y", isAns ? -rScale(n.size || 0) - 5 : 4).attr("text-anchor", isAns ? "middle" : "start").text(isAns ? "answer" : "#" + n.index);
      g.on("mousemove", function (evt) { tip.show(evt, lines(n)); }).on("mouseleave", function () { tip.hide(); })
        .on("focus", function () { if (status) status.textContent = words(n); }).on("click", function () { if (status) status.textContent = words(n); });
    });
    host.appendChild(svg.node());
    host.appendChild(H("div", { class: "lv-bar" }, [
      H("span", { class: "lv-chipline" }, [H("i", { style: { background: "var(--accent)" } }), "search"]), H("span", { class: "lv-chipline" }, [H("i", { style: { background: "var(--ink-2)" } }), "result · tool call"]), H("span", { class: "lv-chipline" }, [H("i", { style: { background: "var(--ink)" } }), "read"]),
      H("span", { class: "lv-chipline" }, [H("i", { style: { background: "var(--good)" } }), "answer (outcome colour) · reaches edge = use recorded"]), H("span", { text: "faded = recorded as not used · dashed = use unknown (" + unknownUse + ") · red ring = errored · dotted ring = repeat (the agent asked twice) · amber ring = retry (the harness re-ran it)" }),
      H("span", { class: "dim", text: (f.retry_basis || "") }),
    ]));
    return { nodes: nodesAll.length, edges: edges.length, unknown: unknownUse, capped: capped };
  }

  function fetchTable(rec, host) {
    var recs = ((rec.fetches || {}).records) || [];
    var shown = recs.slice(0, TABLE_CAP);
    var table = H("table", { class: "lv-table lv-fetches" }, [
      H("thead", null, H("tr", null, ["#", "kind", "tool", "query", "out chars", "tokens", "basis", "latency", "error", "repeat of", "attempt", "used"].map(function (t, i) { return H("th", { class: i >= 4 && i <= 7 ? "num" : "", text: t }); }))),
      H("tbody", null, shown.map(function (r) {
        return H("tr", null, [H("td", { class: "num", text: r.index }), H("td", { text: r.kind }), H("td", { text: r.name }), H("td", { class: "wrap", text: r.query, title: r.query_chars > (r.query || "").length ? "truncated: " + r.query_chars + " chars" : null }),
          H("td", { class: "num", text: tok(r.output_chars) }), H("td", { class: "num", text: tok(r.tokens) }), H("td", { class: "dim", text: r.tokens_basis || "unknown" }), H("td", { class: "num", text: isNum(r.latency_s) ? secs(r.latency_s) : "—" }),
          H("td", { class: r.error ? "bad" : "dim", text: r.error ? "yes" : r.error === false ? "no" : "—" }), H("td", { class: isNum(r.repeat_of) ? "" : "dim", text: isNum(r.repeat_of) ? "#" + r.repeat_of : "—" }),
          H("td", { class: isNum(r.attempt) && r.attempt > 1 ? "warn" : "dim", text: !isNum(r.attempt) ? "—" : r.attempt > 1 ? r.attempt + (isNum(r.retry_of) ? " (re-run of #" + r.retry_of + ")" : " (re-run)") : "1" }),
          H("td", { class: r.used === true ? "good" : r.used === false ? "bad" : "dim", text: r.used === true ? "yes" : r.used === false ? "no" : "unknown", title: r.used_basis || "" })]);
      })),
    ]);
    host.appendChild(H("div", { class: "scroll-x" }, table));
    if (recs.length > TABLE_CAP) host.appendChild(H("p", { class: "lv-note", text: "the first " + TABLE_CAP + " of " + recs.length + " fetches are listed" }));
  }

  function stepsTable(rec, host) {
    var steps = (rec.steps || []).slice(0, TABLE_CAP), b = rec.budget || {}, cum = {};
    (b.burn || []).forEach(function (r) { cum[r[0]] = r[4]; });
    var table = H("table", { class: "lv-table lv-steps" }, [
      H("thead", null, H("tr", null, ["#", "kind", "name", "tokens", "basis", "cumulative", "latency", "reward", "error", "in chars", "out chars"].map(function (t, i) { return H("th", { class: i >= 3 && i !== 4 && i !== 8 ? "num" : "", text: t }); }))),
      H("tbody", null, steps.map(function (s) {
        return H("tr", null, [H("td", { class: "num", text: s.index }), H("td", { text: s.type }), H("td", { text: s.name }), H("td", { class: "num", text: tok(s.tokens) }), H("td", { class: "dim", text: s.tokens_basis || "unknown" }), H("td", { class: "num", text: tok(cum[s.index]) }),
          H("td", { class: "num", text: isNum(s.latency_s) ? secs(s.latency_s) : "—" }), H("td", { class: "num", text: isNum(s.reward) ? num(s.reward, 2) : "—" }), H("td", { class: s.error ? "bad" : "dim", text: s.error ? "yes" : "—" }), H("td", { class: "num", text: tok(s.input_chars) }), H("td", { class: "num", text: tok(s.output_chars) })]);
      })),
    ]);
    host.appendChild(H("div", { class: "scroll-x" }, table));
    if ((rec.steps || []).length > TABLE_CAP) host.appendChild(H("p", { class: "lv-note", text: "the first " + TABLE_CAP + " of " + rec.steps.length + " steps are listed" }));
  }

  /* A run whose steps are not in the output but whose lineage kept a
   * timeline: the steps along the clock, coloured by kind, errors ringed. */
  function timelineStrip(rec, host) {
    var tl = rec.timeline || [], W = width(host), padL = 8, padR = 8, Hh = 46;
    var end = d3.max(tl, function (s) { return s[0] + (s[1] || 0); }) || 1;
    var x = d3.scaleLinear().domain([0, end]).range([padL, W - padR]);
    var kinds = {}; tl.forEach(function (s) { kinds[s[2]] = (kinds[s[2]] || 0) + 1; });
    var errs = tl.filter(function (s) { return String(s[5] || "").indexOf("e") >= 0; }).length;
    var svg = d3.select(L.svg({ viewBox: "0 0 " + W + " " + Hh, class: "lv-timeline", "aria-label": "timeline of " + rec.key + ": " + plural(tl.length, "step") + " over " + secs(end) + " from the lineage's episode — " + Object.keys(kinds).map(function (k) { return kinds[k] + " " + k; }).join(", ") + ", " + plural(errs, "error") + " (rewards " + (rec.reward_basis || "as recorded") + ")" }));
    tl.forEach(function (s) {
      var kind = s[2], err = String(s[5] || "").indexOf("e") >= 0, wasted = String(s[5] || "").indexOf("w") >= 0;
      svg.append("rect").attr("x", x(s[0])).attr("y", 8).attr("width", Math.max(1, x(s[0] + (s[1] || 0)) - x(s[0]) - 0.5)).attr("height", 20).attr("fill", kind === "answer" ? "var(--ink)" : kind === "tool" ? "var(--ink-2)" : "var(--ink-3)").attr("fill-opacity", wasted ? 0.4 : 0.9)
        .attr("stroke", err ? "var(--bad)" : null).attr("stroke-width", err ? 1.5 : null).append("title").text("step at " + secs(s[0]) + ": " + kind + " " + s[3] + (isNum(s[4]) ? ", reward " + num(s[4], 2) : "") + (s[5] ? ", flags " + s[5] : ""));
    });
    svg.append("text").attr("class", "tick").attr("x", padL).attr("y", Hh - 4).text("0s");
    svg.append("text").attr("class", "tick").attr("x", W - padR).attr("y", Hh - 4).attr("text-anchor", "end").text(secs(end));
    host.appendChild(svg.node());
  }

  function numbersLine(r, rec) {
    return H("div", { class: "lv-nums" }, [
      H("span", { class: r.success === true ? "ok" : r.success === false ? "ko" : "" }, [H("b", { text: r.success === true ? "✓ succeeded" : r.success === false ? "✗ failed" : "? outcome unrecorded" })]),
      H("span", null, [H("b", { text: tok(r.steps) }), " steps"]), H("span", null, [H("b", { text: tok(r.tokens) }), " tokens", isNum(r.tokens_measured_share) ? " (" + pct(r.tokens_measured_share) + " measured)" : " (basis unrecorded)"]),
      H("span", null, [H("b", { text: usd(r.cost_usd) }), isNum(r.cost_usd) ? " cost" : " cost (not recorded)"]), H("span", null, [H("b", { text: secs(r.seconds) }), " wall-clock"]),
      H("span", null, [H("b", { text: tok(r.fetches) }), " fetches"]), H("span", null, [H("b", { text: tok(r.errors) }), " errors"]), H("span", null, [H("b", { text: tok(r.repeats) }), " repeats"]),
      r.retries ? H("span", null, [H("b", { text: tok(r.retries) }), " retries (harness re-ran)"]) : null,
      isNum(r["return"]) ? H("span", null, [H("b", { text: num(r["return"], 2) }), " return"]) : null, r.lineage_gen ? H("span", null, [H("b", { text: r.lineage_gen }), " generation"]) : null,
      r.synthetic ? H("span", { class: "lv-syn", text: "SYNTHETIC" }) : null, H("span", { text: "from " + (r.basis || []).join(", ") }),
      // where the steps come from: the output itself, a trace the bundle attached (`--traces`), or nowhere
      H("span", { "data-role": "steps-source", text: rec.measurable ? (typeof rec.steps_source === "string" && rec.steps_source ? "steps from " + rec.steps_source : "steps in the output") : "steps not in the output" }),
    ]);
  }

  AgentDiff.block({
    id: "lv-run",
    title: "Level 3 · One run in full",
    question: "Where did this run's tokens go, step by step, and what did every search, retrieval and read bring back — and reach the answer?",
    group: "levels",
    size: "wide",
    relevance: function (ctx) { try { return model(ctx).rows.length ? 0.9 : 0; } catch (err) { return 0; } },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var m = model(ctx);
      loadState(m);
      var root = H("div", { class: "lv lv-run" });
      el.appendChild(root);
      var drawnKey = null, first = true;

      function draw() {
        var t0 = global.performance ? performance.now() : Date.now();
        var st = state(), key = currentRun(m, st), r = key ? m.byKey[key] : null;
        root.textContent = "";
        if (!r) { root.appendChild(H("p", { class: "lv-note", text: "no run on this page" })); return; }
        var rec = m.record(r.tiled ? key.replace(/~\d+$/, "") : key) || { measurable: false, reason: "no level-3 record for this run", steps: [], budget: {}, fetches: {}, timeline: [] };
        var list = visibleRows(m, st), at = list.findIndex(function (x) { return x.key === key; });
        root.setAttribute("data-run", key);
        root.setAttribute("data-measurable", rec.measurable ? "true" : "false");
        root.setAttribute("data-steps-source", typeof rec.steps_source === "string" ? rec.steps_source : "");
        // the crumbs and the walk
        var crumbs = H("nav", { class: "lv-crumbs", "aria-label": "level 3 breadcrumb" }, [
          H("button", { text: "all runs", title: "back to level 2", onclick: function () { select({ run: null }); scrollToRuns(); } }), H("span", { class: "sep", text: "›", "aria-hidden": "true" }),
          H("button", { text: r.member + " / " + r.task, title: "filter level 2 to this task", onclick: function () { select({ task: r.task, member: m.facets.member.length > 1 ? r.member : null }); scrollToRuns(); } }), H("span", { class: "sep", text: "›", "aria-hidden": "true" }),
          H("button", { text: r.agent, title: "filter level 2 to this agent", onclick: function () { select({ agent: r.agent }); scrollToRuns(); } }), H("span", { class: "sep", text: "›", "aria-hidden": "true" }),
          H("button", { text: r.run_id, "aria-current": "true" }),
        ]);
        var walk = H("span", { class: "lv-facet" }, [
          H("button", { class: "lv-btn", text: "‹ prev", disabled: at <= 0, onclick: function () { if (at > 0) select({ run: list[at - 1].key }); } }),
          H("span", { class: "lv-chipline", text: at >= 0 ? (at + 1) + " of " + list.length + (filterWords(st) ? " (" + filterWords(st) + ")" : "") + ", by " + st.sort : "not in the current level-2 filter" }),
          H("button", { class: "lv-btn", text: "next ›", disabled: at < 0 || at >= list.length - 1, onclick: function () { if (at >= 0 && at < list.length - 1) select({ run: list[at + 1].key }); } }),
        ]);
        root.appendChild(H("div", { class: "lv-bar" }, [crumbs, walk]));
        var status = H("p", { class: "lv-status", role: "status", "aria-live": "polite" });
        status.textContent = (st.run === null ? "no run chosen at level 2: showing the heaviest run whose steps are in the output, " : "level 3: ") + key + (rec.measurable ? " · " + plural((rec.steps || []).length, "step") + ", " + plural(((rec.fetches || {}).records || []).length, "fetch record") : " · steps not in the output");
        root.appendChild(status);
        root.appendChild(numbersLine(r, rec));
        if (!rec.measurable) {
          root.appendChild(H("p", { class: "lv-cannot", text: "This run's steps are not on the page: " + (rec.reason || "no reason given") + ". The row's numbers above are whole (from " + (r.basis || []).join(", ") + "); the burn-down and the search map need the steps." }));
          if (r.tools && Object.keys(r.tools).length) {
            var tools = Object.keys(r.tools).sort(function (a, b) { return r.tools[b] - r.tools[a] || (a < b ? -1 : 1); }), scale = toolScale(tools), calls = d3.sum(tools.map(function (t) { return r.tools[t]; }));
            root.appendChild(H("p", { class: "lv-h", text: "tool calls by tool · " + plural(calls, "call") }));
            root.appendChild(H("div", { class: "lv-bar" }, tools.map(function (t) { return H("span", { class: "lv-chipline" }, [H("i", { style: { background: scale(t) } }), t + " ×" + fmtInt(r.tools[t])]); })));
          }
          if (Array.isArray(rec.timeline) && rec.timeline.length) {
            root.appendChild(H("p", { class: "lv-h", text: "timeline · " + plural(rec.timeline.length, "step") + " from the lineage's episode" }));
            var th = H("div", { class: "lv-timeline-host" }); root.appendChild(th);
            L.layout.responsive(th, function () { timelineStrip(rec, th); }, "lv-run-timeline");
          }
          finishDraw(t0, key);
          return;
        }
        // controls: the x measure
        var ctl = H("div", { class: "lv-bar" }, [H("span", { text: "x:" }),
          H("button", { class: "lv-btn", text: "steps", "data-x": "steps", "aria-pressed": st.x === "steps" ? "true" : "false", onclick: function () { select({ x: "steps" }); } }),
          H("button", { class: "lv-btn", text: "time", "data-x": "time", "aria-pressed": st.x === "time" ? "true" : "false", disabled: !(rec.steps || []).some(function (s) { return isNum(s.latency_s) && s.latency_s > 0; }), title: "wall-clock from the steps' recorded latencies", onclick: function () { select({ x: "time" }); } }),
          H("span", { class: "lv-chipline", text: "· reward basis: " + (rec.reward_basis || "as recorded") })]);
        root.appendChild(H("p", { class: "lv-h", text: "token burn-down · " + plural((rec.steps || []).length, "step") }));
        root.appendChild(ctl);
        var burnHost = H("div", { class: "lv-burn-host", style: { position: "relative" } }); root.appendChild(burnHost);
        var narrow = width(root) < 560;
        L.layout.responsive(burnHost, function () { burnDown(rec, burnHost, st.x, width(root) < 560); }, "lv-run-burn");
        root.appendChild(H("p", { class: "lv-h", text: "where the budget went" }));
        var budHost = H("div", { class: "lv-budget-host" }); root.appendChild(budHost);
        L.layout.responsive(budHost, function () { budgetBars(rec, budHost, width(root) < 560); }, "lv-run-budget");
        var f = rec.fetches || {}, counts = f.counts || {};
        root.appendChild(H("p", { class: "lv-h", text: "search map · " + plural(counts.total, "fetch") }));
        var mapStatus = H("p", { class: "lv-status", role: "status", "aria-live": "polite", text: "Tab reaches a fetch; its record reads here. " + (counts.total ? counts.used + " used, " + counts.unused + " not used, " + counts.unknown_use + " unknown." : "no fetch recorded.") });
        root.appendChild(mapStatus);
        var mapHost = H("div", { class: "lv-map-host", style: { position: "relative" } }); root.appendChild(mapHost);
        if (counts.total) L.layout.responsive(mapHost, function () { searchMap(rec, mapHost, width(root) < 560, mapStatus); }, "lv-run-map");
        else mapHost.appendChild(H("p", { class: "lv-note", text: "this run made no search, retrieval, read or tool call, so there is no map to draw; the burn-down above is the whole story" }));
        root.appendChild(H("details", { class: "lv-details", "data-role": "fetch-table" }, [H("summary", { text: "fetch table · " + plural(((f.records) || []).length, "record") }), (function () { var d = H("div"); fetchTable(rec, d); return d; })()]));
        root.appendChild(H("details", { class: "lv-details", "data-role": "steps-table" }, [H("summary", { text: "table view: the steps of the burn-down · " + plural((rec.steps || []).length, "step") }), (function () { var d = H("div"); stepsTable(rec, d); return d; })()]));
        root.appendChild(H("p", { class: "lv-note", text: "Every number is the engine's budget and fetches reading of this run: a step's count is what the trace recorded under the basis it gave (measured, estimated, or none — hatched when not measured), never re-estimated; used is a recorded reward > 0 or a quality label, never inferred from the answer; waste after the last evidence is the tokens between the last such signal and the answer. Node size on the map is √output chars; the burn-down bins steps past " + BURN_COLS + " columns and the engine caps the burn at 2,000 steps; the map draws " + MAP_CAP + " nodes at most and the tables " + TABLE_CAP + " rows. Time on the x axis is the sum of the steps' latencies, so there is no idle stretch to fold." + (narrow ? " On a narrow screen the map's node labels are in the tooltips and the table." : "") }));
        finishDraw(t0, key);
      }
      function finishDraw(t0, key) {
        TIMING.run = (global.performance ? performance.now() : Date.now()) - t0;
        root.setAttribute("data-draw-ms", TIMING.run.toFixed(1));
        if (!first && drawnKey !== key && dur() > 0) d3.select(root).style("opacity", 0.35).transition().duration(dur()).style("opacity", 1);
        drawnKey = key; first = false;
      }
      draw();
      listen(el, function () { draw(); });
    },
  });
})(typeof window !== "undefined" ? window : this);
