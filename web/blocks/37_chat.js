/* AgentDiff blocks — the Chat view: the page asked in plain words.
 *
 * One block, `chat`, the only block of the Chat lane. The reader types a
 * question; a router of intents maps it to an answer card built by
 * templating over the report's own fields — `narrative`, `reading`, `why`,
 * `reason`, `note`, `basis` — with every number formatted by `AgentDiff.lib`
 * and every interval printed as an interval. Two layers:
 *
 *   the dashboard, asked for   every block in `AgentDiff.catalogue()` is
 *                              reachable ("show the timescape", "what
 *                              changed at g2→g3", "which policy is better",
 *                              "where did the time go"); the answer is one
 *                              or two sentences from the engine, the block
 *                              rendered inside the card through
 *                              `AgentDiff.renderInto`, and an "open in
 *                              <view>" link through `AgentDiff.goTo` that
 *                              lands on the same selection.
 *   the eval as an agent       for a lineage with `aggregate.coevolution`,
 *                              the self-evolving eval answers in the first
 *                              person about what it watched: what it
 *                              learned and when, why it rejected what it
 *                              rejected, what it would have caught earlier,
 *                              whether it trusts itself, which generation
 *                              to keep, what a metric says, which probes
 *                              fired, what it saw at a step — and, when a
 *                              second lineage is in `evolution_compare`,
 *                              how the other self-evolving agent compares
 *                              on the four axes and what its eval learned
 *                              (`evolution_compare.evals`, when present;
 *                              said to be absent when not).
 *
 * Two more layers answer through the page: the Levels (`budget` and
 * `fetches` on a pair, on an aggregate, and a bundle page's
 * `DEEPCOMPARE_DATA.bundle.levels` — what is running, where the tokens
 * went, the heaviest run, what a run fetched, what was wasted; embeds
 * lv-overview, lv-runs, lv-run with the run selected) and the Data
 * (`data` on a pair and `data_evolution` on a lineage — the prompt and
 * the instructions, what each agent read, the answer's provenance beside
 * its outcome, which model produced the steps, how the agent evolved from
 * the data it saw; embeds dt-task, dt-corpus, dt-provenance, dt-chain,
 * dt-evolution when the page has them, and answers in words when not).
 *
 * A question that leans on the last answer — a pronoun ("did it help?",
 * "the other one"), a carrying phrase ("and for bolt-v3?", "what about
 * g3→g4?") or a bare name ("memo-agent", "m2") — resolves against that
 * answer's subject (its intent and the agent, side, run, step, generation,
 * metric, candidate, family and task it was about) into a full question
 * the router answers as if typed; the transcript shows the resolved
 * question in small type under the typed one, and the chips offer two
 * follow-ups that use the carried subject. With no earlier answer there is
 * nothing to carry, and the card says so.
 *
 * The persona is a voice, not a source: nothing is generated beyond the
 * templates below, every card carries a "sources" fold with the JSON paths
 * it read, a question the router cannot map gets a "cannot answer" card
 * with the three nearest intents, and the page loads nothing and calls no
 * model. The transcript persists under one store key (`agentdiff:chat`)
 * as the questions asked; every answer is recomputed from the report on
 * render, so a reload never shows a number the loaded JSON does not hold.
 * Embeds are drawn live for the latest LIVE_EMBEDS answers and on demand
 * for older ones, so a long transcript never makes a render slow.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var L = AgentDiff.lib;
  var isNum = L.fmt.isNum, num = L.fmt.num, pct = L.fmt.pct, secs = L.fmt.secs, short = L.fmt.short, signed = L.fmt.signed;

  //: the transcript keeps the latest MAX_TURNS questions; older ones drop off the top, and the card says so
  var MAX_TURNS = 60;
  //: embeds are drawn on render for the latest LIVE_EMBEDS answers; older answers draw theirs on demand
  var LIVE_EMBEDS = 6;
  //: the composer recalls this many questions with ↑ / ↓
  var HISTORY = 40;
  var VIEW_LABEL = { chat: "Chat", levels: "Levels", data: "Data", story: "Story", evidence: "Evidence", batch: "Batch", panels: "Panels", training: "Training", evolution: "Evolution", coevolution: "Evals" };
  //: a view's name as the tab bar prints it, so the "open in …" link and the tab agree
  function viewLabel(v) {
    try { var tab = document.querySelector('#view-tabs [data-view="' + v + '"]'); if (tab && tab.textContent.trim()) return tab.textContent.trim(); } catch (err) { /* no tabs */ }
    return VIEW_LABEL[v] || String(v);
  }
  var STOP = { the: 1, a: 1, an: 1, of: 1, and: 1, in: 1, to: 1, is: 1, it: 1, what: 1, which: 1, how: 1, show: 1, me: 1, open: 1, with: 1, for: 1, on: 1, at: 1, this: 1, that: 1, did: 1, do: 1, does: 1, you: 1, your: 1, i: 1, my: 1, its: 1, are: 1, was: 1, be: 1, "": 1 };

  var CSS = [
    ".chat{position:relative;font-family:var(--sans);color:var(--ink)}",
    ".chat-log{display:flex;flex-direction:column;gap:14px;margin:0;padding:0;list-style:none}",
    ".chat-turn{display:flex;flex-direction:column;gap:6px;min-width:0}",
    ".chat-turn.new{animation:chat-in .24s ease-out}",
    "@keyframes chat-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}",
    "@media (prefers-reduced-motion:reduce){.chat-turn.new{animation:none}}",
    ".chat-q{align-self:flex-end;max-width:min(72ch,92%);background:var(--surface-2);color:var(--ink);border-radius:14px 14px 3px 14px;padding:8px 12px;font-size:var(--fs-m);line-height:1.45;overflow-wrap:anywhere}",
    ".chat-q-resolved{align-self:flex-end;max-width:min(72ch,92%);margin:-2px 4px 0 0;font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);overflow-wrap:anywhere}",
    ".chat-a{align-self:flex-start;width:100%;max-width:100%;background:var(--surface);border:1px solid var(--rule);border-radius:3px 14px 14px 14px;padding:10px 12px 8px;box-sizing:border-box;min-width:0}",
    ".chat-a.cannot{border-style:dashed}",
    ".chat-a:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".chat-speaker{display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-xs);font-family:var(--mono);color:var(--ink-3);letter-spacing:.04em;text-transform:uppercase;margin:0 0 4px}",
    ".chat-speaker i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ink-3)}.chat-speaker.eval i{background:var(--accent)}",
    ".chat-speaker .syn{color:var(--warn);font-weight:600;margin-left:6px}",
    ".chat-text{margin:0;font-size:var(--fs-m);line-height:1.5;color:var(--ink);max-width:96ch}.chat-text+.chat-text{margin-top:6px}.chat-text b{font-weight:600}.chat-text code{font-family:var(--mono);font-size:var(--fs-s);color:var(--ink-2)}",
    ".chat-embed{margin:10px 0 4px;padding:8px 0 0;border-top:1px dashed var(--rule);overflow-x:auto;min-width:0;max-width:100%}",
    ".chat-embed-title{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);letter-spacing:.04em;text-transform:uppercase;margin:0 0 6px}",
    ".chat-embed-host{min-width:0}.chat-embed-host .empty{display:none}",
    ".chat-foot{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;margin-top:8px;font-size:var(--fs-xs);color:var(--ink-3)}",
    ".chat-link{font:inherit;font-size:var(--fs-xs);border:0;background:transparent;color:var(--accent);cursor:pointer;padding:2px 0;text-decoration:underline;text-underline-offset:2px}.chat-link:hover{color:var(--ink)}",
    ".chat-link:focus-visible,.chat-chip:focus-visible,.chat-send:focus-visible,.chat-clear:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".chat-more{margin:6px 0 0}.chat-more summary{cursor:pointer;color:var(--ink-3);font-size:var(--fs-xs)}.chat-more .chat-text{margin-top:6px;color:var(--ink-2)}",
    ".chat-sources summary{cursor:pointer;color:var(--ink-3);font-size:var(--fs-xs)}.chat-sources ul{margin:4px 0 0;padding:0 0 0 16px;font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2);line-height:1.5}.chat-sources li{overflow-wrap:anywhere}",
    ".chat-chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}",
    ".chat-chip{font:inherit;font-size:var(--fs-xs);border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);border-radius:999px;padding:3px 10px;cursor:pointer;max-width:100%;overflow-wrap:anywhere;text-align:left}",
    ".chat-chip:hover{color:var(--ink);border-color:var(--ink-3)}.chat-chip.eval{border-color:var(--accent)}",
    ".chat-group{margin-top:8px}.chat-group h4{margin:0 0 2px;font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);text-transform:uppercase;letter-spacing:.04em;font-weight:500}",
    ".chat-composer{position:sticky;bottom:0;background:var(--surface);padding:10px 0 2px;margin-top:14px;border-top:1px solid var(--rule)}",
    ".chat-form{display:flex;gap:6px;align-items:stretch;margin-top:8px}",
    ".chat-input{flex:1 1 auto;min-width:0;font:inherit;font-size:var(--fs-m);color:var(--ink);background:var(--surface-2);border:1px solid var(--rule-2);border-radius:10px;padding:8px 12px}.chat-input:focus{outline:2px solid var(--accent);outline-offset:0;border-color:transparent}",
    ".chat-send{font:inherit;font-size:var(--fs-s);font-weight:600;border:0;background:var(--ink);color:var(--bg);border-radius:10px;padding:0 14px;cursor:pointer}.chat-send:hover{opacity:.9}",
    ".chat-clear{font:inherit;font-size:var(--fs-xs);border:0;background:transparent;color:var(--ink-3);cursor:pointer;padding:2px 4px;text-decoration:underline;text-underline-offset:2px}.chat-clear:hover{color:var(--ink)}",
    ".chat-hint{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0}",
    ".chat-status{font-size:var(--fs-xs);color:var(--ink-3);margin:0}",
    "@media (max-width:480px){.chat-q{max-width:100%}.chat-a{padding:8px 9px 6px}}",
  ].join("\n");
  function ensureStyle() { L.style.once("chat", CSS); }

  // ------------------------------------------------------------- helpers

  function ci(v) { return v && isNum(v.point) ? num(v.point) + (isNum(v.lo) ? " [" + num(v.lo) + ", " + num(v.hi) + "]" : "") : "—"; }
  function dci(v) { return v && isNum(v.point) ? signed(v.point) + (isNum(v.lo) ? " [" + signed(v.lo) + ", " + signed(v.hi) + "]" : "") : "—"; }
  function pci(v) { return v && isNum(v.point) ? pct(v.point) + (isNum(v.lo) ? " [" + pct(v.lo) + ", " + pct(v.hi) + "]" : "") : "—"; }
  function usd(v) { return isNum(v) ? "$" + num(v, 6) : "—"; }
  var plural = L.fmt.plural, trunc = L.fmt.trunc;
  function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function dot(s) { s = String(s || "").trim(); return !s ? "" : /[.!?]$/.test(s) ? s : s + "."; }
  function listOf(a) { return Array.isArray(a) && a.length ? a.join(", ") : "none"; }
  function count(v) { return isNum(v) ? v : 0; }
  function words(s) { return String(s || "").toLowerCase().replace(/[^a-z0-9_→\s-]/g, " ").split(/\s+/).filter(function (w) { return w && !STOP[w]; }); }
  //: the question, lower-cased, arrows normalised, punctuation gone
  function norm(q) { return " " + String(q || "").toLowerCase().replace(/->|-->|=>/g, "→").replace(/[^a-z0-9_→\s'-]/g, " ").replace(/\s+/g, " ").trim() + " "; }
  //: how many of the phrases occur in the normalised question (word-bounded)
  function kw(q, phrases) {
    var n = 0;
    phrases.forEach(function (p) { if (q.indexOf(" " + p + " ") >= 0) n++; });
    return n;
  }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); }
    catch (err) { return false; }
  }

  // ---------------------------------------------------------------- data

  /* What the report holds, read once per aggregate: the lineage's eval,
   * the evolution, the comparison, the training statistics, the pair. */
  var cache = null;
  function data(ctx) {
    var agg = ctx.aggregate || {};
    if (cache && cache.agg === agg) return cache.d;
    var d = { agg: agg };
    var c = agg.coevolution && typeof agg.coevolution === "object" ? agg.coevolution : null;
    d.cov = c && c.measurable !== false ? c : null;
    d.covReason = c && c.measurable === false ? c.reason : null;
    var ev = agg.evolution && typeof agg.evolution === "object" && agg.evolution.measurable !== false ? agg.evolution : null;
    d.evo = ev;
    var ec = agg.evolution_compare && typeof agg.evolution_compare === "object" && agg.evolution_compare.measurable !== false ? agg.evolution_compare : null;
    d.ec = ec;
    d.evals = ec && ec.evals && typeof ec.evals === "object" ? ec.evals : null;
    var rl = agg.rl && typeof agg.rl === "object" ? agg.rl : null;
    d.rl = rl;
    d.stats = rl && rl.stats && rl.stats.measurable !== false && rl.stats.aggregates ? rl.stats : null;
    d.scorecard = agg.scorecard && agg.scorecard.agents ? agg.scorecard : null;
    d.efficiency = agg.efficiency && agg.efficiency.per_agent ? agg.efficiency : null;
    d.family = d.cov ? String(d.cov.family || "") : ev ? String(ev.family || "") : "";
    d.families = ec && Array.isArray(ec.lineages) ? ec.lineages.map(function (l) { return String(l.family || l.label || ""); }) : [];
    d.other = d.families.filter(function (f) { return f && f !== d.family; })[0] || null;
    d.synthetic = !!(d.cov && d.cov.synthetic === true) || !!(ev && Array.isArray(ev.generations) && ev.generations.some(function (g) { return /SYNTHETIC/.test(String(g && g.note || "")); }));
    // the eval's vocabulary
    d.metrics = d.cov && d.cov.metrics && typeof d.cov.metrics === "object" ? d.cov.metrics : {};
    d.metricIds = Object.keys(d.metrics);
    d.ledger = d.cov && Array.isArray(d.cov.ledger) ? d.cov.ledger.filter(function (r) { return r && r.spec_id; }) : [];
    d.ledger.forEach(function (r, i) { if (!isNum(r.index)) r.index = i; });
    d.candidateIds = [];
    d.ledger.forEach(function (r) { if (d.candidateIds.indexOf(r.spec_id) < 0) d.candidateIds.push(r.spec_id); });
    d.features = {};
    (d.cov && Array.isArray(d.cov.features) ? d.cov.features : []).forEach(function (f) { if (f && f.id) d.features[f.id] = f; });
    d.evalGens = d.cov && Array.isArray(d.cov.eval_generations) ? d.cov.eval_generations : [];
    d.probes = d.cov && Array.isArray(d.cov.probes) ? d.cov.probes : [];
    // the agent's steps, keyed "g2→g3"
    d.steps = ev && Array.isArray(ev.steps) ? ev.steps.filter(function (s) { return s && s.from && s.to; }) : [];
    d.stepByKey = {};
    d.steps.forEach(function (s) { d.stepByKey[s.from + "→" + s.to] = s; });
    d.covSteps = {};
    (d.cov && Array.isArray(d.cov.steps) ? d.cov.steps : []).forEach(function (s) { if (s && s.from && s.to) d.covSteps[s.from + "→" + s.to] = s; });
    d.genIds = [];
    (ev && Array.isArray(ev.generations) ? ev.generations : []).forEach(function (g) { if (g && g.id) d.genIds.push(String(g.id)); });
    // the agents of the pair and the policies
    d.agents = agg.agents && typeof agg.agents === "object" ? [agg.agents.a, agg.agents.b].filter(Boolean).map(String) : [];
    d.policies = d.stats && Array.isArray(d.stats.policies) ? d.stats.policies.map(String) : [];
    d.tasks = typeof AgentDiff.taskIds === "function" ? AgentDiff.taskIds() : [];
    d.reports = Array.isArray(ctx.reports) ? ctx.reports : [];
    // the Levels: a bundle's three levels when the page is a bundle's, the aggregate's budget and fetches ledgers otherwise
    var bd = global.DEEPCOMPARE_DATA && global.DEEPCOMPARE_DATA.bundle && global.DEEPCOMPARE_DATA.bundle.levels ? global.DEEPCOMPARE_DATA.bundle : null;
    d.bundle = bd;
    d.overview = bd && bd.levels.overview ? bd.levels.overview : null;
    d.records = bd && bd.levels.records && typeof bd.levels.records === "object" ? bd.levels.records : {};
    d.budgetAgg = agg.budget && agg.budget.measurable !== false && agg.budget.agents ? agg.budget : null;
    d.fetchesAgg = agg.fetches && agg.fetches.measurable !== false && agg.fetches.agents ? agg.fetches : null;
    d.dataAgg = agg.data && agg.data.measurable !== false && agg.data.agents ? agg.data : null;
    d.dataEvo = agg.data_evolution && agg.data_evolution.measurable !== false && Array.isArray(agg.data_evolution.steps) ? agg.data_evolution : null;
    d.runs = runRows(d);
    d.runByKey = {};
    d.runAgents = [];
    d.runs.forEach(function (r) { d.runByKey[r.key] = r; if (d.runAgents.indexOf(r.agent) < 0) d.runAgents.push(r.agent); });
    d.hasPairLevels = d.reports.some(function (r) { return r && (r.budget || r.fetches); });
    d.hasPairData = d.reports.some(function (r) { return r && r.data && r.data.measurable !== false; });
    cache = { agg: agg, d: d };
    return d;
  }
  /* Every run the page knows, keyed the way the Levels block keys them:
   * the bundle's level-2 rows, else "page/<task>/<agent>/<run>" from the
   * pair reports' sides and the aggregate ledgers. `detail` says a
   * level-3 record (a budget and a fetches reading) is on the page. */
  function runRows(d) {
    var rows = [], seen = {};
    function add(r) { if (!seen[r.key]) { seen[r.key] = true; rows.push(r); } else if (r.detail) { var o = rows.filter(function (x) { return x.key === r.key; })[0]; o.detail = true; o.report = r.report; o.side = r.side; if (isNum(r.tokens)) o.tokens = r.tokens; } }
    if (d.bundle) {
      (d.bundle.levels.runs || []).forEach(function (r) { if (r && r.key) add({ key: r.key, member: r.member, task: String(r.task), agent: String(r.agent), run_id: String(r.run_id), tokens: r.tokens, fetches: r.fetches, detail: !!d.records[r.key] }); });
    }
    var label = d.bundle && d.bundle.members && d.bundle.members[0] ? String(d.bundle.members[0].label) : "page";
    d.reports.forEach(function (rep) {
      var task = rep && rep.task && rep.task.id ? String(rep.task.id) : "";
      if (!task) return;
      ["a", "b"].forEach(function (side) {
        var blk = rep[side];
        if (!blk || !blk.agent) return;
        var agent = String(blk.agent.name || side), run = String(blk.run_id || "r1"), key = label + "/" + task + "/" + agent + "/" + run;
        var b = rep.budget && rep.budget[side] && rep.budget[side].measurable !== false ? rep.budget[side] : null;
        add({ key: key, member: label, task: task, agent: agent, run_id: run, tokens: b && b.tokens ? b.tokens.total : null, fetches: rep.fetches && rep.fetches[side] && rep.fetches[side].counts ? rep.fetches[side].counts.total : null, detail: !!(b || (rep.fetches && rep.fetches[side])), report: rep, side: side });
      });
    });
    if (!d.bundle) [d.budgetAgg, d.fetchesAgg].forEach(function (sec) {
      (sec && Array.isArray(sec.runs) ? sec.runs : []).forEach(function (r) { if (r && r.task && r.agent) add({ key: label + "/" + r.task + "/" + r.agent + "/" + r.run, member: label, task: String(r.task), agent: String(r.agent), run_id: String(r.run), tokens: r.tokens, fetches: r.fetches, detail: false }); });
    });
    return rows;
  }
  //: the level-3 reading of a run: its budget, its fetches, its data — from the bundle's record or the pair report's side
  function runRecord(d, row) {
    if (!row) return null;
    var rec = d.records[row.key];
    if (rec) return { key: row.key, budget: rec.budget || null, fetches: rec.fetches || null, data: rec.data || null, source: "DEEPCOMPARE_DATA.bundle.levels.records[" + row.key + "]" };
    if (row.report) return { key: row.key, budget: row.report.budget && row.report.budget[row.side] || null, fetches: row.report.fetches && row.report.fetches[row.side] || null, data: row.report.data && row.report.data[row.side] || null, source: "report.<section>." + row.side };
    return null;
  }
  //: the pair's two sides as runs of the page, for the report in view
  function pairRuns(d, ctx) {
    var rep = ctx.report;
    if (!rep || !rep.task) return [];
    return d.runs.filter(function (r) { return r.report === rep; }).sort(function (a, b) { return a.side < b.side ? -1 : 1; });
  }
  //: the page's catalogue, read once per (report, task): relevance is stable within a render
  var catCache = null;
  function catalogue() {
    var st = null;
    try { st = typeof AgentDiff.state === "function" ? AgentDiff.state() : null; } catch (err) { st = null; }
    var agg = st && st.data ? st.data.aggregate : null, task = st ? st.task : null;
    if (catCache && catCache.agg === agg && catCache.task === task) return catCache.list;
    var list;
    try { list = AgentDiff.catalogue(); } catch (err) { list = []; }
    catCache = { agg: agg, task: task, list: list };
    return list;
  }
  function inCatalogue(id) { return catalogue().some(function (b) { return b.id === id; }); }
  function blockOf(id) { return catalogue().filter(function (b) { return b.id === id; })[0] || null; }
  //: the first block of the list the page can draw here
  function firstBlock(ids) { for (var i = 0; i < ids.length; i++) if (inCatalogue(ids[i])) return ids[i]; return null; }

  // ------------------------------------------------------------ entities

  /* What the question names: a step (g2→g3, "step 3", or the step into a
   * named generation), a generation, a metric of the eval, a candidate of
   * the ledger, a block of the catalogue, a lineage family, an agent. */
  function entities(q, d) {
    var E = { step: null, gen: null, metric: null, candidate: null, block: null, blockScore: 0, family: null, agent: null, task: null };
    var m = /\bg(\d+)\s*(?:→|to|vs|versus|against)\s*g(\d+)\b/.exec(q);
    if (m) { var key = "g" + m[1] + "→g" + m[2]; if (d.stepByKey[key] || d.covSteps[key]) E.step = key; }
    var st = /\bstep\s+(\d+)\b/.exec(q);
    if (!E.step && st) { var s = d.steps.filter(function (x) { return isNum(x.index) && x.index === parseInt(st[1], 10); })[0]; if (s) E.step = s.from + "→" + s.to; }
    var g = /\bg(\d+)\b/.exec(q.replace(/g\d+\s*→\s*g\d+/g, " "));
    if (g && d.genIds.indexOf("g" + g[1]) >= 0) E.gen = "g" + g[1];
    if (!E.gen && E.step) E.gen = E.step.split("→")[1];
    if (!E.step && E.gen && !/\bg\d+\s*→/.test(q)) { var into = d.steps.filter(function (x) { return x.to === E.gen; })[0]; if (into) E.stepInto = into.from + "→" + into.to; }
    //: an id as the normalised question can carry it: verbatim, underscores as spaces, or every other mark as a space ("ledger-agent@g5" → "ledger-agent g5")
    function named(ids) {
      var best = null;
      ids.forEach(function (id) {
        var a = " " + id.toLowerCase() + " ", b = " " + id.toLowerCase().replace(/_/g, " ") + " ", c = " " + id.toLowerCase().replace(/[^a-z0-9→'-]+/g, " ").trim() + " ";
        if (q.indexOf(a) >= 0 || q.indexOf(b) >= 0 || q.indexOf(c) >= 0) { if (!best || id.length > best.length) best = id; }
      });
      return best;
    }
    E.metric = named(d.metricIds);
    if (!E.metric) { // the metric's name ("verification rate")
      d.metricIds.forEach(function (id) { var nm = d.metrics[id] && d.metrics[id].spec && d.metrics[id].spec.name; if (nm && q.indexOf(" " + String(nm).toLowerCase() + " ") >= 0) E.metric = id; });
    }
    E.candidate = named(d.candidateIds);
    E.family = named(d.families);
    // an agent of the pair, a policy, or any agent the page's runs name (a bundle's other members)
    E.agent = named(d.agents.concat(d.policies, d.runAgents));
    E.task = named(d.tasks);
    // a run: its full key, or an agent named with a task and/or a run id
    E.run = null;
    var runId = /\b(r\d+)\b/.exec(q);
    d.runs.forEach(function (r) { if (q.indexOf(" " + r.key.toLowerCase() + " ") >= 0) E.run = r; });
    if (!E.run && E.agent) {
      var cands = d.runs.filter(function (r) { return r.agent.toLowerCase() === E.agent.toLowerCase() && (!E.task || r.task === E.task) && (!runId || r.run_id === runId[1]); });
      cands.sort(function (a, b) { return (b.detail ? 1 : 0) - (a.detail ? 1 : 0); });
      E.run = cands[0] || null;
    }
    // a block of the catalogue, fuzzy on the title's words
    var best = null, bestScore = 0, qw = words(q);
    catalogue().forEach(function (b) {
      var score = 0;
      if (q.indexOf(" " + b.id + " ") >= 0) score = 4;
      else {
        var tw = words(b.title).filter(function (w) { return w.length >= 3; });
        if (!tw.length) return;
        // a title word hits on the word itself or a five-letter prefix either way; a title needs two hits or every word
        var hit = tw.filter(function (w) { return qw.indexOf(w) >= 0 || qw.some(function (x) { return x.length >= 5 && w.length >= 5 && (w.indexOf(x) === 0 || x.indexOf(w) === 0); }); }).length;
        var ratio = hit / tw.length;
        if (hit && ratio >= 0.5 && (hit >= 2 || ratio === 1)) score = 1 + 2 * ratio;
      }
      if (score > bestScore) { bestScore = score; best = b; }
    });
    E.block = best; E.blockScore = bestScore;
    return E;
  }

  // ------------------------------------------------------------- persona

  function evalSpeaker(d) { return "the eval · " + (d.family || "the lineage"); }
  var PAGE = "the page";

  //: the ledger rows of a candidate, in step order
  function rowsOf(d, id) { return d.ledger.filter(function (r) { return r.spec_id === id; }); }
  //: the adopted row of a metric at the step that adopted it
  function adoptionRow(d, id, step) { return d.ledger.filter(function (r) { return r.spec_id === id && r.decision === "adopted" && (!step || r.step === step); })[0] || null; }
  function metricName(d, id) { var mt = d.metrics[id]; return mt && mt.spec && mt.spec.name ? String(mt.spec.name) : String(id); }
  function metricLabel(d, id) { var nm = metricName(d, id); return nm !== id ? id + " (" + nm + ")" : id; }
  var AGG_WORDS = { mean: "the mean of", rate: "the rate of", iqm: "the task-balanced IQM of", task_mean: "the per-task mean of", task_min: "the worst task's mean of", task_spread: "the spread of the per-task means of" };
  function whereText(w) { if (!w) return ""; if (Array.isArray(w.all)) return w.all.map(whereText).join(" and "); return String(w.feature) + " " + String(w.op) + " " + String(w.value); }
  function specText(d, spec) {
    if (!spec) return "";
    var f = d.features[spec.feature];
    return (AGG_WORDS[spec.agg] || String(spec.agg || "")) + " " + String(spec.feature || "") + (f && f.basis ? " (" + f.basis + ")" : "") + (spec.where ? " over the episodes where " + whereText(spec.where) : " over every episode") + "; " + (spec.direction === "up" ? "higher is better" : spec.direction === "down" ? "lower is better" : "no direction is better");
  }
  //: the most-rejected candidate of the ledger, for the chips
  function mostRejected(d) {
    var n = {}, best = null;
    d.ledger.forEach(function (r) { if (r.decision === "rejected") { n[r.spec_id] = (n[r.spec_id] || 0) + 1; if (!best || n[r.spec_id] > n[best]) best = r.spec_id; } });
    return best;
  }
  function firstAdopted(d) { for (var i = 1; i < d.evalGens.length; i++) { var a = d.evalGens[i].adopted; if (Array.isArray(a) && a.length) return a[0]; } return d.metricIds[0] || null; }
  function worstStep(d) { return d.steps.filter(function (s) { return s.verdict === "gamed"; })[0] || d.steps.filter(function (s) { return s.verdict === "forgot" || s.verdict === "regressed"; })[0] || d.steps[d.steps.length - 1] || null; }
  function stepKey(s) { return s ? s.from + "→" + s.to : ""; }

  // ------------------------------------------------------------- intents
  //
  // Each intent: id, family ("eval" | "page"), example (the chip that asks
  // it), when(d) → whether this report can answer it, absent(d) → why not
  // when it cannot, match(q, E, d) → a score (0 is no match), and
  // answer(q, E, d, ctx) → a card {speaker, text: [paragraphs], embed,
  // select, sources, followups, synthetic?, groups?}. Ordered from the most
  // specific to the least: a tie goes to the earlier one.

  var INTENTS = [];
  function intent(name, spec) { spec.id = name; INTENTS.push(spec); }
  //: why the eval cannot answer here: no lineage at all, or a lineage without a measurable eval
  function noEval(d) { return !d.evo && !d.ec ? "this report has no self-evolving lineage, so there is no eval to speak for it" : d.covReason ? "the eval could not be measured on this lineage: " + d.covReason : "this report has no self-evolving eval (no coevolution section)"; }

  // ---- the eval as an agent

  intent("eval-learned", {
    family: "eval", example: "what did you learn?",
    when: function (d) { return !!d.cov; }, absent: noEval,
    match: function (q, E) { return kw(q, ["what did you learn", "what have you learned", "learned", "learn", "adopted", "adopt", "eval generations", "what did the eval learn", "metrics did you", "your metrics"]) * 2 + (E.metric ? 0 : 0); },
    answer: function (q, E, d) {
      var c = d.cov, text = [], src = ["aggregate.coevolution.base", "aggregate.coevolution.eval_generations[]"];
      var base = Array.isArray(c.base) ? c.base : [];
      text.push("I started as e0 with " + plural(base.length, "base metric") + ": " + listOf(base) + ". " + (d.evalGens.length > 1 ? "Then, step by step:" : "I never grew past it."));
      var learned = 0;
      d.evalGens.forEach(function (e, i) {
        if (i === 0 || !e) return;
        var parts = [];
        (Array.isArray(e.adopted) ? e.adopted : []).forEach(function (id) {
          var row = adoptionRow(d, id, e.after_step);
          learned++;
          parts.push("I adopted " + metricLabel(d, id) + " into " + e.id + (row ? " because " + row.reason : ""));
          if (row) src.push("aggregate.coevolution.ledger[" + row.index + "].reason");
        });
        (Array.isArray(e.retired) ? e.retired : []).forEach(function (id) {
          var mt = d.metrics[id], why = mt && mt.retired_at && mt.retired_at.reason ? mt.retired_at.reason : mt && mt.retired_at && mt.retired_at.note ? mt.retired_at.note : null;
          parts.push("I retired " + metricLabel(d, id) + (why ? " — " + why : ""));
          src.push("aggregate.coevolution.metrics." + id + ".retired_at");
        });
        (Array.isArray(e.demoted) ? e.demoted : []).forEach(function (id) {
          var mt = d.metrics[id], why = mt && mt.demoted_at && (mt.demoted_at.reason || mt.demoted_at.note);
          parts.push("I demoted " + metricLabel(d, id) + (why ? " — " + why : ""));
          src.push("aggregate.coevolution.metrics." + id + ".demoted_at");
        });
        if (parts.length) text.push("At " + e.after_step + " the " + (e.trigger_probe || "") + " probe fired and " + parts.join("; ") + ".");
      });
      var mu = c.integrity && c.integrity.multiplicity;
      if (mu) {
        text.push("In all I tested " + plural(count(mu.tested), "candidate") + ", adopted " + count(mu.adopted) + " and rejected " + count(mu.rejected) + ", each step's candidates at α " + num(mu.alpha, 4) + " / K (the smallest adjusted level " + num(mu.min_adjusted_alpha, 4) + ")" + (learned ? "." : " — I learned nothing: every candidate was noise, redundant, or already mine."));
        src.push("aggregate.coevolution.integrity.multiplicity");
      }
      var rej = mostRejected(d);
      return { speaker: evalSpeaker(d), text: text, embed: "cov-flow", select: { family: "coevolution", value: { step: null, evalGen: null, candidate: null } }, sources: src, synthetic: true,
        followups: [rej ? "why did you reject " + rej + "?" : null, "what would you have caught earlier?", firstAdopted(d) ? "what does " + firstAdopted(d) + " say?" : null, "do you trust yourself?"] };
    },
  });

  intent("eval-rejected", {
    family: "eval", example: "why did you reject a candidate?",
    when: function (d) { return !!d.cov && d.ledger.length > 0; }, absent: noEval,
    match: function (q, E) { return kw(q, ["reject", "rejected", "why not", "turned down", "refuse", "refused", "didn't you adopt", "did you not adopt", "not adopt", "failed"]) * 2 + (E.candidate ? 2 : 0); },
    answer: function (q, E, d) {
      var text = [], src = [], sel = null, follow = [], subject = null;
      var id = E.candidate || (E.metric && rowsOf(d, E.metric).length ? E.metric : null);
      if (id) {
        var rows = rowsOf(d, id), rej = rows.filter(function (r) { return r.decision === "rejected"; }), ad = rows.filter(function (r) { return r.decision === "adopted"; });
        if (!rej.length && ad.length) {
          text.push("I never rejected " + metricLabel(d, id) + ": I adopted it at " + ad[0].step + " (" + ad[0].probe + " probe) because " + ad[0].reason + ".");
          src.push("aggregate.coevolution.ledger[" + ad[0].index + "]");
          sel = { candidate: ad[0].index, step: ad[0].step, evalGen: null };
          subject = { candidate: id, step: ad[0].step };
        } else {
          var times = rej.length === 1 ? "once" : rej.length === 2 ? "twice" : rej.length + " times";
          var attempts = rej.map(function (r) {
            src.push("aggregate.coevolution.ledger[" + r.index + "]");
            return "at " + r.step + " (" + (r.probe || "?") + " probe, K " + count(r.k) + ", α " + num(r.alpha, 4) + ") it failed " + listOf(Array.isArray(r.failed) ? r.failed : []) + ": " + r.reason;
          });
          text.push("I rejected " + metricLabel(d, id) + " " + times + " — " + attempts.join("; ") + ".");
          if (ad.length) { text.push("I did adopt it at " + ad[0].step + " because " + ad[0].reason + "."); src.push("aggregate.coevolution.ledger[" + ad[0].index + "]"); }
          var last = rej[rej.length - 1];
          sel = { candidate: last.index, step: last.step, evalGen: null };
          subject = { candidate: id, step: last.step };
          var others = d.candidateIds.filter(function (x) { return x !== id && rowsOf(d, x).some(function (r) { return r.decision === "rejected"; }); });
          if (others.length) follow.push("why did you reject " + others[0] + "?");
          follow.push("what did the agent do at " + last.step + "?");
        }
      } else {
        var byV = {}, n = 0, ids = [];
        d.ledger.forEach(function (r) {
          if (r.decision !== "rejected") return;
          n++;
          var f = Array.isArray(r.failed) && r.failed.length ? r.failed[0] : "unstated";
          byV[f] = (byV[f] || 0) + 1;
          if (ids.indexOf(r.spec_id) < 0) ids.push(r.spec_id);
        });
        text.push("I rejected " + plural(n, "candidate") + " (" + plural(ids.length, "distinct spec") + "), each at its first failed validator: " + Object.keys(byV).map(function (v) { return byV[v] + " at " + v; }).join(", ") + ".");
        text.push("The candidates: " + ids.join(", ") + ". Ask about one by name and I will say what it failed and why.");
        src.push("aggregate.coevolution.ledger[].failed", "aggregate.coevolution.ledger[].reason");
        sel = { step: null, evalGen: null, candidate: null };
        ids.slice(0, 3).forEach(function (x) { follow.push("why did you reject " + x + "?"); });
      }
      follow.push("what did you learn?", "which probes fired?");
      return { speaker: evalSpeaker(d), text: text, embed: "cov-flow", select: { family: "coevolution", value: sel }, sources: src, synthetic: true, followups: follow, subject: subject };
    },
  });

  intent("eval-hindsight", {
    family: "eval", example: "what would you have caught earlier?",
    when: function (d) { return !!d.cov && !!d.cov.hindsight; }, absent: noEval,
    match: function (q) { return kw(q, ["caught earlier", "have caught", "hindsight", "lag", "late", "too late", "earlier", "would have flagged", "missed", "re-read", "reread"]) * 2; },
    answer: function (q, E, d) {
      var h = d.cov.hindsight, ca = h.caught_at && typeof h.caught_at === "object" ? h.caught_at : {}, text = [], src = ["aggregate.coevolution.hindsight"];
      var lines = Object.keys(ca).map(function (id) {
        var r = ca[id];
        src.push("aggregate.coevolution.hindsight.caught_at." + id);
        if (!r) return null;
        if (!isNum(r.lag)) return metricLabel(d, id) + " " + (r.note || "never flags a step");
        if (r.lag === 0) return metricLabel(d, id) + ": " + (r.note || "adopted at the first step it flags") + " (" + r.adopted_step + ", lag 0)";
        return metricLabel(d, id) + ": " + (r.note || "") + " — first flag at " + r.first_flag_step + ", adopted at " + r.adopted_step + " (lag " + r.lag + ")";
      }).filter(Boolean);
      var late = Object.keys(ca).filter(function (id) { return ca[id] && isNum(ca[id].lag) && ca[id].lag > 0; });
      text.push((late.length ? "Yes — " + late.map(function (id) { return metricLabel(d, id) + " came " + plural(ca[id].lag, "step") + " late"; }).join("; ") + ". " : "Nothing I learned came late. ") + "Metric by metric: " + lines.join("; ") + ".");
      text.push("Re-reading all " + plural(count(h.steps), "step") + " with my final eval: " + count(h.changed) + " changed verdict because of a learned metric; " + plural(count(h.learned_flags), "learned flag") + " and " + plural(count(h.base_flags), "base flag") + " (on " + plural(count(h.steps_with_base_flags), "step") + ", where a base metric's own interval moves against it, which the base verdict does not read).");
      var flagged = Object.keys(d.covSteps).filter(function (k) { var s = d.covSteps[k]; return s.evolved && Array.isArray(s.evolved.flags) && s.evolved.flags.length; });
      return { speaker: evalSpeaker(d), text: text, embed: "cov-hindsight", select: null, sources: src, synthetic: true,
        followups: [flagged.length ? "what did the agent do at " + flagged[0] + "?" : null, "do you trust yourself?", "which generation should I keep?"] };
    },
  });

  intent("eval-trust", {
    family: "eval", example: "do you trust yourself?",
    when: function (d) { return !!d.cov && !!d.cov.integrity; }, absent: noEval,
    match: function (q) { return kw(q, ["trust yourself", "trust you", "are you sound", "you sound", "integrity", "drift", "drifted", "multiplicity", "honest", "reliable", "trust", "the gap", "your gap", "blind spot", "unconfirmed"]) * 2; },
    answer: function (q, E, d) {
      var g = d.cov.integrity, dr = g.drift || {}, mu = g.multiplicity || {}, ex = g.external || {}, text = [], src = ["aggregate.coevolution.integrity"];
      text.push("Only as far as the numbers go. My drift from the base is a Jaccard distance of " + num(dr.jaccard_distance_from_base) + (dr.basis ? " (" + dr.basis + ")" : "") + (Array.isArray(dr.size_by_eval_gen) ? ", my size by eval generation " + dr.size_by_eval_gen.join(" → ") : "") + ".");
      text.push("Multiplicity: I tested " + count(mu.tested) + ", adopted " + count(mu.adopted) + ", rejected " + count(mu.rejected) + (count(mu.unparseable) ? ", " + count(mu.unparseable) + " unparseable" : "") + ", at α " + num(mu.alpha, 4) + " over the candidates of a step, the smallest adjusted level " + num(mu.min_adjusted_alpha, 4) + (mu.basis ? " — " + mu.basis : "") + ".");
      text.push("Demoted: " + listOf(g.demoted) + ". Retired: " + listOf(g.retired) + ". Never confirmed out of sample: " + listOf(g.unconfirmed) + ". External proposals: " + count(ex.received) + " received, " + count(ex.adopted) + " adopted.");
      if (g.gap) text.push("The gap I cannot close: " + dot(g.gap));
      return { speaker: evalSpeaker(d), text: text, embed: "cov-integrity", select: null, sources: src.concat(["aggregate.coevolution.integrity.gap"]), synthetic: true,
        followups: ["what would you have caught earlier?", "what did you learn?", (Array.isArray(g.unconfirmed) && g.unconfirmed[0]) ? "what does " + g.unconfirmed[0] + " say?" : null] };
    },
  });

  intent("eval-keep", {
    family: "eval", example: "which generation should I keep?",
    when: function (d) { return !!d.cov && !!d.cov.recommended; }, absent: noEval,
    match: function (q) { return kw(q, ["which generation", "should i keep", "keep", "recommend", "recommended", "recommendation", "ship", "deploy", "best generation", "pick"]) * 2; },
    answer: function (q, E, d) {
      var r = d.cov.recommended, text = [], src = ["aggregate.coevolution.recommended"];
      var pick = r.evolved || r.base;
      text.push("Keep " + pick + ". The base rule picks " + (r.base || "nothing") + " and my evolved rule picks " + (r.evolved || "nothing") + " — " + (r.agree ? "we agree" : "we disagree") + (r.why ? ": " + dot(r.why) : "."));
      var ex = r.excluded && r.excluded.evolved && typeof r.excluded.evolved === "object" ? r.excluded.evolved : r.excluded && r.excluded.base ? r.excluded.base : null;
      if (ex && Object.keys(ex).length) {
        text.push("I pass over " + Object.keys(ex).map(function (g) { return g + " (" + listOf(ex[g]) + ")"; }).join("; ") + ".");
        src.push("aggregate.coevolution.recommended.excluded");
      }
      if (d.evo && d.evo.recommended && d.evo.recommended.why) { text.push("The page's own reading of the lineage: " + dot(d.evo.recommended.why)); src.push("aggregate.evolution.recommended.why"); }
      var into = d.steps.filter(function (s) { return s.to === pick; })[0];
      return { speaker: evalSpeaker(d), text: text, embed: firstBlock(["evo-lineage", "cov-hindsight"]), select: pick && inCatalogue("evo-lineage") ? { family: "evolution", value: { gen: pick } } : null, sources: src, synthetic: true, subject: { gen: pick, step: into ? stepKey(into) : null },
        followups: [into ? "did " + stepKey(into) + " help?" : null, "what would you have caught earlier?", d.other ? "compare with " + d.other : "show the lineage"] };
    },
  });

  intent("eval-metric", {
    family: "eval", example: "what does a metric say?",
    when: function (d) { return !!d.cov && d.metricIds.length > 0; }, absent: noEval,
    match: function (q, E) { return (E.metric ? 2 : 0) + kw(q, ["what does", "say", "says", "metric", "value", "values", "across the generations", "matrix", "measure", "measures", "curve"]) + (E.metric ? 1 : 0); },
    answer: function (q, E, d) {
      var id = E.metric || firstAdopted(d), mt = d.metrics[id] || {}, spec = mt.spec || {}, text = [], src = ["aggregate.coevolution.metrics." + id, "aggregate.coevolution.matrix." + id];
      var origin = mt.origin || spec.origin || {};
      text.push(metricLabel(d, id) + " measures " + specText(d, spec) + ". Status: " + (mt.status || "adopted") + (mt.status === "base" ? " — one of my four base metrics, never retired or demoted." : origin.step ? " — proposed by the " + (origin.probe || "?") + " probe at " + origin.step + (mt.adopted_at && mt.adopted_at.eval_gen ? " into " + mt.adopted_at.eval_gen : "") + "." : "."));
      var row = d.cov.matrix && d.cov.matrix[id] && typeof d.cov.matrix[id] === "object" ? d.cov.matrix[id] : null;
      if (row) {
        var cells = Object.keys(row).map(function (g) { var v = row[g]; return g + " " + (v && v.measurable !== false ? ci(v) : "unmeasurable" + (v && v.reason ? " (" + v.reason + ")" : "")); });
        text.push("Across the generations (point [lo, hi], a stratified bootstrap): " + cells.join(", ") + ".");
      }
      var cf = mt.confirmation, ca = mt.caught_at, v = mt.validation;
      var tail = [];
      if (v && v.reason) tail.push("validated: " + v.reason);
      if (cf && typeof cf === "object") tail.push("out of sample it is " + (cf.status || "untested") + " (tested " + count(cf.tested) + ", moved " + count(cf.moved) + ")");
      if (ca && typeof ca === "object" && ca.note) tail.push("with hindsight: " + ca.note + (isNum(ca.lag) ? " (lag " + ca.lag + ")" : ""));
      if (mt.retired_at) tail.push("retired at " + (mt.retired_at.step || "?") + (mt.retired_at.reason || mt.retired_at.note ? " — " + (mt.retired_at.reason || mt.retired_at.note) : ""));
      if (tail.length) text.push(cap(tail.join("; ")) + ".");
      var others = d.metricIds.filter(function (x) { return x !== id; });
      return { speaker: evalSpeaker(d), text: text, embed: "cov-metric", select: { family: "coevolution", value: { metric: id } }, sources: src, synthetic: true, subject: { metric: id },
        followups: [others[0] ? "what does " + others[0] + " say?" : null, rowsOf(d, id).some(function (r) { return r.decision === "rejected"; }) ? "why did you reject " + id + "?" : "what did you learn?", "what would you have caught earlier?"] };
    },
  });

  intent("eval-probes", {
    family: "eval", example: "which probes fired?",
    when: function (d) { return !!d.cov && d.probes.length > 0; }, absent: noEval,
    match: function (q) { return kw(q, ["probe", "probes", "fired", "which probes", "your agents", "sub-agents", "external", "proposer"]) * 2; },
    answer: function (q, E, d) {
      var fired = d.probes.filter(function (p) { return Array.isArray(p.fired) && p.fired.length; }), quiet = d.probes.filter(function (p) { return !(Array.isArray(p.fired) && p.fired.length); });
      var text = [], src = ["aggregate.coevolution.probes[]"];
      text.push(fired.length + " of my " + plural(d.probes.length, "probe") + " fired: " + fired.map(function (p) { return p.name + " (" + (p.question || "") + ") at " + p.fired.join(", ") + " — proposed " + count(p.proposed) + ", adopted " + count(p.adopted); }).join("; ") + ".");
      if (quiet.length) text.push("Never fired: " + quiet.map(function (p) { return p.name + (p.question ? " (" + p.question + ")" : ""); }).join("; ") + ".");
      var ex = d.cov.integrity && d.cov.integrity.external;
      if (ex) { text.push("The external proposer sent " + count(ex.received) + " candidate" + (count(ex.received) === 1 ? "" : "s") + (count(ex.received) ? ": " + count(ex.parsed) + " parsed, " + count(ex.adopted) + " adopted, " + count(ex.rejected) + " rejected" : "") + "; it can never set a number, a verdict or an exit code."); src.push("aggregate.coevolution.integrity.external"); }
      return { speaker: evalSpeaker(d), text: text, embed: "cov-probes", select: null, sources: src, synthetic: true,
        followups: ["what did you learn?", fired[0] && fired[0].fired[0] ? "what did the agent do at " + fired[0].fired[0] + "?" : null, "do you trust yourself?"] };
    },
  });

  intent("eval-saw", {
    family: "eval", example: "what did the agent do at a step?",
    when: function (d) { return !!d.cov && d.steps.length > 0; }, absent: noEval,
    match: function (q, E) { return (E.step ? 2 : 0) + kw(q, ["what did the agent do", "what did you see", "saw", "see at", "happened at", "did the agent do", "at that step", "what happened at", "tell me about"]) * 2; },
    answer: function (q, E, d) {
      var key = E.step || E.stepInto || stepKey(worstStep(d)), s = d.stepByKey[key], cs = d.covSteps[key], text = [], src = [];
      if (!s) return cannot("I can't find that step in this lineage", d);
      var i = d.steps.indexOf(s);
      src.push("aggregate.evolution.steps[" + i + "].diff.summary", "aggregate.evolution.steps[" + i + "].verdict", "aggregate.evolution.steps[" + i + "].effect");
      var eff = s.effect || {}, imp = eff.improvement, imps = eff.improvement_success;
      text.push("I saw " + key + " (" + (s.mechanism || "step") + "): the agent changed " + (s.diff && s.diff.summary ? s.diff.summary : "nothing the diff can name") + (s.evidence && s.evidence.summary ? ", citing " + dot(s.evidence.summary) : "."));
      text.push("The base eval called it " + (s.verdict || "unmeasured") + (Array.isArray(s.flags) && s.flags.length ? " (flags: " + s.flags.join(", ") + ")" : "") + (imp ? ": P(" + s.to + " > " + s.from + ") " + pci(imp) + " on return" : "") + (imps && isNum(imps.point) ? ", " + pci(imps) + " on outcome" : "") + ".");
      if (cs && cs.evolved) {
        var probes = d.probes.filter(function (p) { return Array.isArray(p.fired) && p.fired.indexOf(key) >= 0; }).map(function (p) { return p.name; });
        var rows = d.ledger.filter(function (r) { return r.step === key; }), ad = rows.filter(function (r) { return r.decision === "adopted"; });
        text.push("With hindsight, " + (cs.evolved.reading || "I add nothing.") + (probes.length ? " At this step my " + probes.join(", ") + " probe" + (probes.length === 1 ? "" : "s") + " fired: " + plural(rows.length, "candidate") + ", " + ad.length + " adopted" + (ad.length ? " (" + ad.map(function (r) { return r.spec_id; }).join(", ") + ")" : "") + "." : " No probe of mine fired here."));
        src.push("aggregate.coevolution.steps[" + Object.keys(d.covSteps).indexOf(key) + "].evolved.reading", "aggregate.coevolution.probes[].fired");
      }
      var rej = d.ledger.filter(function (r) { return r.step === key && r.decision === "rejected"; })[0];
      return { speaker: evalSpeaker(d), text: text, embed: "cov-flow", select: { family: "coevolution", value: { step: key, evalGen: null, candidate: null } }, sources: src, synthetic: true, subject: { step: key, gen: key.split("→")[1] },
        followups: ["what changed at " + key + "?", "did " + key + " help?", rej ? "why did you reject " + rej.spec_id + "?" : null] };
    },
  });

  intent("eval-compare", {
    family: "eval", example: "compare with the other agent",
    when: function (d) { return !!d.ec; }, absent: function () { return "no second lineage was compared in this report (no evolution_compare section)"; },
    match: function (q, E, d) { return kw(q, ["compare", "compared", "comparison", "other agent", "other lineage", "other self-evolving", "the other", "versus", "vs", "against", "who evolved better", "evolved better", "which lineage", "better lineage", "both lineages", "two lineages", "their eval", "other eval"]) * 2 + (E.family ? 2 : 0) + (E.family && d.ec ? 0 : 0); },
    answer: function (q, E, d) {
      var ec = d.ec, v = ec.verdict || {}, text = [], more = [], src = ["aggregate.evolution_compare.verdict"];
      var A = ec.lineages && ec.lineages[0] ? String(ec.lineages[0].family || ec.lineages[0].label) : d.family, B = ec.lineages && ec.lineages[1] ? String(ec.lineages[1].family || ec.lineages[1].label) : d.other;
      var me = d.cov ? evalSpeaker(d) : PAGE;
      var lead = d.cov ? "I watched " + A + "; " + B + " is the other self-evolving agent in this report. " : A + " against " + B + ". ";
      text.push(lead + "Four axes, four answers: " + (v.reading ? v.reading : ["peak", "final", "learning", "process"].map(function (ax) { return ax + ": " + (v[ax] || "no separation") + (v[ax + "_basis"] ? " — " + v[ax + "_basis"] : ""); }).join("; ") + ".") + " No winner is declared without its axis: a lineage can take final and lose process.");
      var pr = ec.process && typeof ec.process === "object" ? ec.process : null;
      if (pr) {
        var line = [A, B].filter(function (f) { return pr[f]; }).map(function (f) { var p = pr[f]; return f + " " + count(p.improved) + " improved, " + count(p.regressed) + " regressed, " + count(p.flat) + " flat, " + count(p.gamed) + " gamed, " + count(p.forgot) + " forgot, " + count(p.traded) + " traded; " + count(p.protected_touched) + " protected touched, " + count(p.accepted_on_noise) + " accepted on noise" + (p.retention && isNum(p.retention.at_last) ? ", retention " + pct(p.retention.at_last) : ""); });
        text.push("The process record: " + line.join(" — ") + ".");
        src.push("aggregate.evolution_compare.process");
      }
      var ev = d.evals;
      if (ev && ev.measurable !== false && Array.isArray(ev.lineages) && ev.lineages.length) {
        src.push("aggregate.evolution_compare.evals");
        var per = ev.lineages.map(function (l) {
          var rec = l.recommended || {};
          var learned = Array.isArray(l.adopted) && l.adopted.length;
          return (l.family === A && d.cov ? "I grew " : l.family + "'s eval grew ") + plural(count(l.eval_generations), "eval generation") + (learned ? ", adopting " + l.adopted.join(", ") : ", adopting nothing") + (Array.isArray(l.retired) && l.retired.length ? ", retiring " + l.retired.join(", ") : "") + (Array.isArray(l.demoted) && l.demoted.length ? ", demoting " + l.demoted.join(", ") : "") + " (" + count(l.tested) + " tested, " + count(l.rejected) + " rejected, drift " + num(l.drift) + ", " + plural(count(l.closures), "loop closure") + (isNum(l.hindsight_lag_max) ? ", the longest lag " + l.hindsight_lag_max : "") + (rec.base || rec.evolved ? "; recommends " + (rec.evolved || rec.base) + (rec.agree === true ? ", base and evolved agree" : rec.agree === false ? ", base " + rec.base + " disagrees" : "") : "") + ")" + (learned ? "" : " — every candidate noise, redundant, or already its own");
        });
        text.push("What each eval learned: " + per.join("; ") + ".");
        if (Array.isArray(ev.shared_metrics)) text.push((ev.shared_metrics.length ? "Learned by more than one eval: " + ev.shared_metrics.join(", ") + ". " : "No metric was learned by both evals. ") + (Array.isArray(ev.transfer) && ev.transfer.length ? "Transfer, one test per pair at α: " + ev.transfer.map(function (t) { return t.metric + " learned on " + t.learned_on + ", applied to " + t.applied_to + "'s last step: " + (t.informative_there ? "informative there" : "not informative there") + (t.delta ? ", delta " + dci(t.delta) : ""); }).join("; ") + "." : "Nothing was transferred."));
        if (ev.reading) more.push(dot(ev.reading));
        ev.lineages.forEach(function (l) { if (l.narrative && l.family !== A) more.push(l.family + "'s eval, in its own words: " + dot(l.narrative)); });
      } else {
        text.push("What " + B + "'s own eval learned is not in this report: the comparison carries no evals section (evolution_compare.evals" + (ev && ev.reason ? ": " + ev.reason : "") + "). " + (d.cov ? "I can only speak for what I watched." : "The page can only compare the two lineages' outcomes and process."));
        src.push("aggregate.evolution_compare.evals (absent)");
      }
      return { speaker: me, text: text, more: more, embed: firstBlock(["evc-verdict", "evc-curves"]), select: null, sources: src, synthetic: true, subject: { family: B },
        followups: ["who evolved better?", "show the task race", "show who learned faster", "show how soundly each evolved"] };
    },
  });


  // ---- the levels: what is running, where the tokens went, the fetches

  function tok(v) { return isNum(v) ? num(v, 0) : "—"; }
  function sideName(row) { return row.report && row.report[row.side] && row.report[row.side].agent ? String(row.report[row.side].agent.name) : row.agent; }
  //: the runs a question is about: the one it names, else the pair in view (both sides), else the heaviest run with a record
  function runsFor(E, d, ctx) {
    if (E.run) return [E.run];
    var pair = pairRuns(d, ctx).filter(function (r) { return r.detail; });
    if (pair.length) return pair;
    var withRec = d.runs.filter(function (r) { return r.detail && isNum(r.tokens); }).sort(function (a, b) { return b.tokens - a.tokens; });
    return withRec.length ? [withRec[0]] : [];
  }
  function levelsSelect(row) { return row ? { family: "levels", value: { run: row.key } } : null; }
  //: the subject of a levels answer: the run it read first, as an agent, a side and a run key
  function runSubject(row) { return row ? { agent: sideName(row), run: row.key, side: row.side || null, task: row.task || null } : null; }
  //: the heaviest runs the page knows: the bundle's, the aggregate ledger's, else the pair reports' sides by their budget totals
  function heaviest(d) {
    if (d.overview && Array.isArray(d.overview.heaviest_runs) && d.overview.heaviest_runs.length) return { rows: d.overview.heaviest_runs.map(function (h) { return { key: h.key, tokens: h.tokens }; }), source: "DEEPCOMPARE_DATA.bundle.levels.overview.heaviest_runs", cap: d.overview.cap || null };
    if (d.budgetAgg && Array.isArray(d.budgetAgg.heaviest_runs) && d.budgetAgg.heaviest_runs.length) return { rows: d.budgetAgg.heaviest_runs.map(function (h) { return { key: "page/" + h.task + "/" + h.agent + "/" + h.run, tokens: h.tokens, label: h.agent + " on " + h.task + " (" + h.run + ")" }; }), source: "aggregate.budget.heaviest_runs", cap: d.budgetAgg.cap || null };
    var rows = d.runs.filter(function (r) { return isNum(r.tokens); }).sort(function (a, b) { return b.tokens - a.tokens || (a.key < b.key ? -1 : 1); }).slice(0, 8);
    return rows.length ? { rows: rows.map(function (r) { return { key: r.key, tokens: r.tokens, label: r.agent + " on " + r.task + " (" + r.run_id + ")" }; }), source: "report.budget.<side>.tokens.total, over the pair reports on this page", cap: null } : null;
  }

  intent("lv-running", {
    family: "page", example: "what is running?",
    when: function () { return true; },
    match: function (q) { return kw(q, ["what is running", "what's running", "running", "overview", "what agents", "which agents", "how many runs", "how many agents", "level 1", "the bundle", "what is in this bundle"]) * 2; },
    answer: function (q, E, d, ctx) {
      var text = [], src = [];
      if (d.overview) {
        text.push(dot(d.overview.reading || ""));
        src.push("DEEPCOMPARE_DATA.bundle.levels.overview.reading");
        var t = d.overview.totals || {};
        if (Array.isArray(d.overview.agents) && d.overview.agents.length) {
          text.push("Agent by agent: " + d.overview.agents.slice(0, 12).map(function (a) { var sr = a.success_rate || {}; return a.name + (a.self_evolving ? " (self-evolving)" : "") + " — " + plural(count(a.runs), "run") + ", success " + pct(sr.rate) + (isNum(sr.lo) ? " [" + pct(sr.lo) + ", " + pct(sr.hi) + "]" : "") + ", " + tok(a.tokens_total) + " tokens" + (isNum(a.cost_usd_total) ? ", " + usd(a.cost_usd_total) : "") + ", " + count(a.fetches_total) + " fetches"; }).join("; ") + (d.overview.agents.length > 12 ? "; and " + (d.overview.agents.length - 12) + " more" : "") + ".");
          src.push("DEEPCOMPARE_DATA.bundle.levels.overview.agents[]");
        }
        if (t.basis) { text.push(cap(dot(t.basis))); src.push("DEEPCOMPARE_DATA.bundle.levels.overview.totals.basis"); }
      } else {
        var who = d.family ? "the lineage of " + d.family + (d.genIds.length ? " (" + plural(d.genIds.length, "generation") + (d.cov ? "; its eval " + plural(d.evalGens.length, "eval generation") : "") + ")" : "") : d.policies.length ? d.policies.join(" and ") : d.agents.length ? d.agents.join(" and ") : "its agents";
        text.push("This page is one output, not a bundle: it reads " + who + " over " + plural(d.tasks.length, "task") + " (" + plural(d.runs.length, "run") + " the Levels view can list). `agentdiff bundle` packs several outputs into one page with a level-1 overview, a bundle id and a key.");
        src.push("aggregate.agents / aggregate.evolution.family / AgentDiff.taskIds()");
        if (d.budgetAgg && d.budgetAgg.narrative) { text.push(dot(d.budgetAgg.narrative)); src.push("aggregate.budget.narrative"); }
        if (d.fetchesAgg && d.fetchesAgg.narrative) { text.push(dot(d.fetchesAgg.narrative)); src.push("aggregate.fetches.narrative"); }
      }
      return { speaker: PAGE, text: text, embed: firstBlock(["lv-overview"]), select: null, sources: src, synthetic: d.synthetic,
        followups: ["which run was the most expensive?", "where did the tokens go?", "show the searches", "how many fetches were wasted?"] };
    },
  });

  intent("lv-budget", {
    family: "page", example: "where did the tokens go?",
    when: function (d) { return d.hasPairLevels || !!d.budgetAgg || Object.keys(d.records).length > 0; }, absent: function () { return "this report carries no budget section (rerun the command to add it)"; },
    match: function (q, E) { var k = kw(q, ["where did the tokens go", "tokens", "token", "token budget", "budget", "burn", "burn-down", "what did this cost", "what did it cost", "cost", "expensive", "spend", "spent"]); return k ? k * 2 + 1 + (E.run ? 2 : 0) : 0; },
    answer: function (q, E, d, ctx) {
      var text = [], src = [], runs = runsFor(E, d, ctx), first = null;
      runs.forEach(function (row) {
        var rec = runRecord(d, row), b = rec && rec.budget;
        if (!b) return;
        if (!first) first = row;
        if (b.measurable === false) { text.push(sideName(row) + ": the budget is not measurable — " + (b.reason || "no reason given") + "."); src.push(rec.source.replace("<section>", "budget")); return; }
        var tk = b.tokens || {}, io = b.io || {}, cost = b.cost_usd || {}, w = b.waste || {};
        text.push(dot(b.narrative || (sideName(row) + " spent " + tok(tk.total) + " tokens")) + " Of those, " + tok(tk.measured) + " measured, " + tok(tk.estimated) + " estimated and " + tok(tk.unknown) + " unlabelled" + (io.measurable !== false && isNum(io.input_tokens) ? "; the totals record " + tok(io.input_tokens) + " input and " + tok(io.output_tokens) + " output tokens" : "; input/output tokens not recorded" + (io.reason ? " (" + io.reason + ")" : "")) + (cost.measurable !== false && isNum(cost.value) ? "; cost " + usd(cost.value) + " from " + (cost.source || "the totals") : "; no cost recorded") + ".");
        if (isNum(w.after_last_evidence) || isNum(w.in_errored_calls) || isNum(w.in_repeats)) text.push("Waste, three ways: " + tok(w.after_last_evidence) + " tokens after the last evidence, " + tok(w.in_errored_calls) + " in errored calls, " + tok(w.in_repeats) + " in repeats.");
        src.push(rec.source.replace("<section>", "budget") + ".narrative", rec.source.replace("<section>", "budget") + ".tokens", rec.source.replace("<section>", "budget") + ".waste");
      });
      var pb = ctx.report && ctx.report.budget;
      if (!E.run && pb && pb.narrative && runs.length > 1) { text.push(dot(pb.narrative)); src.push("report.budget.narrative"); }
      if (!text.length && d.budgetAgg) { text.push(dot(d.budgetAgg.narrative)); src.push("aggregate.budget.narrative"); }
      if (!text.length) return cannot("no run on this page carries a budget reading", d, ctx);
      return { speaker: PAGE, text: text, embed: firstBlock(["lv-run", "lv-runs"]), select: levelsSelect(first), sources: src, synthetic: d.synthetic, subject: runSubject(first),
        followups: ["which run was the most expensive?", "how many fetches were wasted?", first ? "what did " + sideName(first) + " fetch?" : "show the searches", "what is running?"] };
    },
  });

  intent("lv-heaviest", {
    family: "page", example: "which run was the most expensive?",
    when: function (d) { return !!heaviest(d); }, absent: function () { return "this report carries no per-run token totals"; },
    match: function (q) { var k = kw(q, ["most expensive", "heaviest", "heaviest run", "biggest run", "most tokens", "largest run", "costliest", "which run", "over the cap", "token cap"]); return k ? k * 2 + 1 : 0; },
    answer: function (q, E, d, ctx) {
      var h = heaviest(d), rows = h.rows.slice(0, 3), text = [], src = [h.source];
      text.push("The heaviest run is " + (rows[0].label || rows[0].key) + " at " + tok(rows[0].tokens) + " tokens" + (rows.length > 1 ? "; then " + rows.slice(1).map(function (r) { return (r.label || r.key) + " (" + tok(r.tokens) + ")"; }).join(", ") : "") + ".");
      if (h.cap) { text.push(isNum(h.cap.value) ? "The token cap is " + tok(h.cap.value) + " (" + (h.cap.source || "") + "): " + plural((h.cap.over || []).length, "run") + " over it" + ((h.cap.over || []).length ? " — " + h.cap.over.slice(0, 5).map(function (o) { return o.key || (o.agent + " on " + o.task + " (" + o.run + ")"); }).join(", ") : "") + "." : "No token cap was given (" + (h.cap.source || "none given") + "), so no run is over one."); src.push(h.source.replace("heaviest_runs", "cap")); }
      var row = d.runByKey[rows[0].key] || null;
      return { speaker: PAGE, text: text, embed: firstBlock(["lv-runs", "lv-run"]), select: { family: "levels", value: { sort: "tokens", run: row ? row.key : null } }, sources: src, synthetic: d.synthetic, subject: runSubject(row),
        followups: ["where did the tokens go?", row && row.detail ? "what did " + row.agent + " fetch?" : "show the searches", "what is running?"] };
    },
  });

  intent("lv-fetch", {
    family: "page", example: "show the searches",
    when: function (d) { return d.hasPairLevels || !!d.fetchesAgg || Object.keys(d.records).length > 0; }, absent: function () { return "this report carries no fetches section (rerun the command to add it)"; },
    match: function (q, E) { return kw(q, ["fetch", "fetched", "fetches", "search", "searches", "searched", "queries", "query", "retrieve", "retrieved", "what came back", "search map"]) * 2 + (E.run ? 2 : 0); },
    answer: function (q, E, d, ctx) {
      var text = [], src = [], runs = runsFor(E, d, ctx), first = null;
      runs.forEach(function (row) {
        var rec = runRecord(d, row), f = rec && rec.fetches;
        if (!f) return;
        if (!first) first = row;
        if (f.measurable === false) { text.push(sideName(row) + ": the fetches are not measurable — " + (f.reason || "no reason given") + "."); src.push(rec.source.replace("<section>", "fetches")); return; }
        var c = f.counts || {}, m = f.map || {};
        text.push(dot(f.narrative || (sideName(row) + " made " + count(c.total) + " fetches")) + (c.unknown_use ? " " + count(c.unknown_use) + " carry no use signal, so the reading never infers their use from the answer." : ""));
        var searches = (f.records || []).filter(function (r) { return r.kind === "search"; });
        if (searches.length) text.push("The searches: " + searches.slice(0, 6).map(function (r) { return "#" + r.index + " " + r.name + " “" + String(r.query || "").slice(0, 80) + (String(r.query || "").length > 80 ? "…" : "") + "” (" + tok(r.output_chars) + " chars back" + (r.used === true ? ", used" : r.used === false ? ", not used" : ", use unrecorded") + (r.error ? ", error" : "") + (isNum(r.repeat_of) ? ", repeats #" + r.repeat_of : "") + ")"; }).join("; ") + (searches.length > 6 ? "; and " + (searches.length - 6) + " more" : "") + ".");
        if (Array.isArray(m.nodes)) text.push("The search map has " + plural(m.nodes.length, "node") + " and " + plural((m.edges || []).length, "edge") + (m.basis ? " — " + m.basis : "") + ".");
        src.push(rec.source.replace("<section>", "fetches") + ".narrative", rec.source.replace("<section>", "fetches") + ".records[]", rec.source.replace("<section>", "fetches") + ".map");
      });
      if (!text.length && d.fetchesAgg) { text.push(dot(d.fetchesAgg.narrative)); src.push("aggregate.fetches.narrative"); }
      if (!text.length) return cannot("no run on this page carries a fetches reading", d, ctx);
      return { speaker: PAGE, text: text, embed: firstBlock(["lv-run", "lv-runs"]), select: levelsSelect(first), sources: src, synthetic: d.synthetic, subject: runSubject(first),
        followups: ["how many fetches were wasted?", "where did the tokens go?", first && !E.run && runs.length > 1 ? "what did " + sideName(runs[1]) + " fetch?" : "which run was the most expensive?", d.hasPairData ? "what did they read?" : null] };
    },
  });

  intent("lv-waste", {
    family: "page", example: "how many fetches were wasted?",
    when: function (d) { return d.hasPairLevels || Object.keys(d.records).length > 0 || !!d.fetchesAgg; }, absent: function () { return "this report carries no fetches section (rerun the command to add it)"; },
    match: function (q) { var k = kw(q, ["wasted", "waste", "wasted fetches", "repeats", "repeated", "unused", "errored", "errors", "how many fetches were wasted"]); return k ? k * 2 + 1 : 0; },
    answer: function (q, E, d, ctx) {
      var text = [], src = [], runs = runsFor(E, d, ctx), first = null;
      runs.forEach(function (row) {
        var rec = runRecord(d, row), f = rec && rec.fetches, b = rec && rec.budget;
        if (!f || f.measurable === false) return;
        if (!first) first = row;
        var c = f.counts || {}, w = b && b.waste ? b.waste : null;
        text.push(sideName(row) + ": of " + count(c.total) + " fetches, " + count(c.repeats) + " repeated an earlier one, " + count(c.errors) + " errored, " + count(c.unused) + " recorded as not used and " + count(c.unknown_use) + " with no use signal (" + count(c.used) + " used)" + (w ? "; in tokens, " + tok(w.in_repeats) + " in repeats, " + tok(w.in_errored_calls) + " in errored calls and " + tok(w.after_last_evidence) + " after the last evidence" : "") + ".");
        if (c.retries) text.push(count(c.retries) + " of those were retries the harness re-ran, not the agent asking twice" + (w && isNum(w.in_retries) ? ", costing " + tok(w.in_retries) + " tokens" : "") + ".");
        else if (f.retry_basis) text.push(f.retry_basis + ".");
        src.push(rec.source.replace("<section>", "fetches") + ".counts", rec.source.replace("<section>", "budget") + ".waste");
      });
      if (!text.length && d.fetchesAgg) {
        text.push(Object.keys(d.fetchesAgg.agents).map(function (n) { var a = d.fetchesAgg.agents[n]; return n + ": " + count(a.fetches) + " fetches over " + plural(count(a.runs), "run") + ", " + count(a.repeats) + " repeats" + (a.retries ? ", " + count(a.retries) + " retries the harness re-ran" : "") + ", " + count(a.errors) + " errors, " + count(a.unused) + " not used, " + count(a.unknown_use) + " with no use signal"; }).join("; ") + ".");
        src.push("aggregate.fetches.agents");
      }
      if (!text.length) return cannot("no run on this page carries a fetches reading", d, ctx);
      var basis = first ? (runRecord(d, first).budget || {}).waste : null;
      if (basis && basis.basis) { text.push("How waste is read: " + dot(basis.basis)); }
      return { speaker: PAGE, text: text, embed: firstBlock(["lv-run", "lv-runs"]), select: levelsSelect(first), sources: src, synthetic: d.synthetic, subject: runSubject(first),
        followups: ["show the searches", "where did the tokens go?", "which run was the most expensive?"] };
    },
  });

  // ---- the data: what the agents were told and read, what the answer rests on, how the agent evolved from it

  function pairData(ctx) { var r = ctx.report; return r && r.data && r.data.measurable !== false ? r.data : null; }
  function sideLabel(ctx, side) { var r = ctx.report; return r && r[side] && r[side].agent ? String(r[side].agent.name) : side.toUpperCase(); }
  function instrText(ins) { return ins && isNum(ins.chars) && ins.chars > 0 ? plural(ins.chars, "character") + " of instructions from " + (ins.source || "the trace") : "no instructions recorded"; }
  function modelsText(list) { return Array.isArray(list) && list.length ? list.map(function (m) { return (m.model === null || m.model === undefined ? "no model recorded" : String(m.model)) + " (" + plural(count(m.steps), "step") + ", " + tok(m.tokens) + " tokens; " + (m.source || "source unstated") + ")"; }).join(", ") : "no model recorded"; }
  function srcName(data, id) { var s = data && data.corpus && Array.isArray(data.corpus.sources) ? data.corpus.sources.filter(function (x) { return x.id === id; })[0] : null; return s ? s.name + " “" + String(s.input || "").slice(0, 60) + (String(s.input || "").length > 60 ? "…" : "") + "”" : id; }

  intent("dt-prompt", {
    family: "page", example: "what prompt was given?",
    when: function (d, ctx) { return !!pairData(ctx); }, absent: function () { return "this report carries no data section for the pair (rerun the command to add it)"; },
    match: function (q) { return kw(q, ["prompt", "what prompt", "told", "were the agents told", "instructions", "system prompt", "given", "what was given", "expected answer", "the task"]) * 2; },
    answer: function (q, E, d, ctx) {
      var dt = pairData(ctx), t = dt.task || {}, text = [], src = ["report.data.task", "report.data.a.agent.instructions", "report.data.b.agent.instructions", "report.data.instructions_diff", "report.data.models"];
      text.push("Both agents were given the " + count(t.prompt_chars) + "-character prompt “" + String(t.prompt || "") + "”" + (t.expected ? ", with the expected answer “" + String(t.expected) + "” (" + plural(count(t.expected_chars), "character") + ")" : ", with no expected answer recorded") + ".");
      var idf = dt.instructions_diff || {};
      text.push(sideLabel(ctx, "a") + ": " + instrText(dt.a && dt.a.agent && dt.a.agent.instructions) + "; " + sideLabel(ctx, "b") + ": " + instrText(dt.b && dt.b.agent && dt.b.agent.instructions) + " — " + (idf.same === true ? "the same instructions." : idf.same === false ? "they differ in " + plural((idf.hunks || []).length, "hunk") + (isNum(idf.added) ? " (+" + idf.added + "/−" + count(idf.removed) + " lines)" : "") + "." : (idf.reason || "the instructions cannot be compared") + "."));
      text.push("Models, as recorded: " + sideLabel(ctx, "a") + " " + modelsText(dt.a && dt.a.models) + "; " + sideLabel(ctx, "b") + " " + modelsText(dt.b && dt.b.models) + (dt.models && dt.models.same === true ? " — the same model." : dt.models && dt.models.same === false ? " — different models." : "."));
      return { speaker: PAGE, text: text, embed: firstBlock(["dt-task"]), select: null, sources: src, synthetic: d.synthetic,
        followups: ["what did they read?", "is the answer grounded?", "which model produced this?", "what happened?"] };
    },
  });

  intent("dt-corpus", {
    family: "page", example: "what did they read?",
    when: function (d, ctx) { return !!pairData(ctx) && !!pairData(ctx).corpus_diff; }, absent: function () { return "this report carries no data section for the pair (rerun the command to add it)"; },
    match: function (q, E) { return kw(q, ["read", "what did they read", "didn't", "did not", "corpus", "sources", "source", "read that", "only", "shared sources", "jaccard"]) * 2 + (E.agent ? 1 : 0); },
    answer: function (q, E, d, ctx) {
      var dt = pairData(ctx), cd = dt.corpus_diff, A = sideLabel(ctx, "a"), B = sideLabel(ctx, "b"), text = [], src = ["report.data.corpus_diff", "report.data.a.corpus", "report.data.b.corpus"];
      var ca = dt.a && dt.a.corpus || {}, cb = dt.b && dt.b.corpus || {};
      text.push(A + " read " + plural(count(ca.distinct), "distinct source") + " in " + count(ca.fetches) + " fetches (" + tok(ca.total_chars) + " characters back" + (count(ca.repeated_reads) ? ", " + plural(ca.repeated_reads, "repeated read") : "") + "); " + B + " read " + plural(count(cb.distinct), "distinct source") + " in " + count(cb.fetches) + " fetches (" + tok(cb.total_chars) + " characters" + (count(cb.repeated_reads) ? ", " + plural(cb.repeated_reads, "repeated read") : "") + "). Shared: " + (cd.shared || []).length + "; only " + A + ": " + (cd.only_a || []).length + "; only " + B + ": " + (cd.only_b || []).length + "; Jaccard " + num(cd.jaccard) + (cd.basis ? " (" + cd.basis + ")" : "") + ".");
      var onlyA = (cd.only_a || []).slice(0, 4).map(function (id) { return srcName(dt.a, id); }), onlyB = (cd.only_b || []).slice(0, 4).map(function (id) { return srcName(dt.b, id); });
      if (onlyA.length || onlyB.length) text.push((onlyA.length ? "Only " + A + " read: " + onlyA.join("; ") + ((cd.only_a || []).length > 4 ? "; and " + ((cd.only_a || []).length - 4) + " more" : "") + ". " : "") + (onlyB.length ? "Only " + B + " read: " + onlyB.join("; ") + ((cd.only_b || []).length > 4 ? "; and " + ((cd.only_b || []).length - 4) + " more" : "") + "." : ""));
      var firstOnly = (cd.only_a || [])[0] || (cd.only_b || [])[0] || null;
      return { speaker: PAGE, text: text, embed: firstBlock(["dt-corpus"]), select: firstOnly ? { family: "data", value: { source: firstOnly } } : null, sources: src, synthetic: d.synthetic,
        followups: ["is the answer grounded?", "what prompt was given?", "show the searches", "which model produced this?"] };
    },
  });

  intent("dt-grounded", {
    family: "page", example: "is the answer grounded?",
    when: function (d, ctx) { return !!pairData(ctx) && !!pairData(ctx).provenance; }, absent: function () { return "this report carries no data section for the pair (rerun the command to add it)"; },
    match: function (q) { return kw(q, ["grounded", "grounding", "rest on", "rests on", "provenance", "supported", "typed values", "where did the answer come from", "traced", "what does the answer rest on"]) * 2; },
    answer: function (q, E, d, ctx) {
      var dt = pairData(ctx), pv = dt.provenance, r = ctx.report, text = [], src = ["report.data.provenance", "report.data.a.provenance.grounded_in", "report.data.b.provenance.grounded_in", "report.a.outcome.success", "report.b.outcome.success", "report.answer_eval.expected"];
      ["a", "b"].forEach(function (side) {
        var p = pv[side] || {}, full = dt[side] && dt[side].provenance || {}, name = sideLabel(ctx, side), outcome = r[side] && r[side].outcome || {};
        var gi = Array.isArray(full.grounded_in) ? full.grounded_in : [];
        text.push(name + "'s answer carries " + plural(count(p.atoms), "typed value") + (count(p.atoms) ? ": " + count(p.supported) + " traced to a fetched output and " + count(p.unsupported) + " not (" + pct(p.grounded_share) + " grounded)" + (gi.length ? "; it rests on " + gi.map(function (g) { return "step " + g.step + " " + g.name + " (" + pct(g.overlap) + " of the values)"; }).join(", ") : "") : " — no typed value, so its grounded share is not a number (" + (full.basis || "an answer of prose only has no figure to trace") + ")") + ". Grounded is not correct: " + name + " " + (outcome.success === true ? "solved" : outcome.success === false ? "failed" : "has no recorded outcome on") + " the task" + (r.answer_eval && r.answer_eval.expected ? ", whose expected answer is “" + String(r.answer_eval.expected) + "”" : "") + ".");
      });
      if (isNum(pv.delta_grounded)) text.push("Grounded share, " + sideLabel(ctx, "a") + " minus " + sideLabel(ctx, "b") + ": " + signed(pv.delta_grounded) + ".");
      return { speaker: PAGE, text: text, embed: firstBlock(["dt-provenance"]), select: null, sources: src, synthetic: d.synthetic,
        followups: ["what did they read?", "what happened?", "what prompt was given?", "show the claims"] };
    },
  });

  intent("dt-model", {
    family: "page", example: "which model produced this?",
    when: function (d, ctx) { return !!pairData(ctx); }, absent: function () { return "this report carries no data section for the pair (rerun the command to add it)"; },
    match: function (q) { return kw(q, ["which model", "what model", "model", "models", "produced", "produced this", "which model produced", "temperature", "the chain"]) * 2; },
    answer: function (q, E, d, ctx) {
      var dt = pairData(ctx), text = [], src = ["report.data.a.models", "report.data.b.models", "report.data.models", "report.data.a.chain.reading", "report.data.b.chain.reading"];
      text.push(sideLabel(ctx, "a") + ": " + modelsText(dt.a && dt.a.models) + ". " + sideLabel(ctx, "b") + ": " + modelsText(dt.b && dt.b.models) + "." + (dt.models && dt.models.same === true ? " The same model on both sides." : dt.models && dt.models.same === false ? " Different models." : "") + " A model's name here is the trace's own record (steps[].model, else trace.agent.model); the page names none of its own.");
      ["a", "b"].forEach(function (side) { var ch = dt[side] && dt[side].chain; if (ch && ch.reading) text.push(sideLabel(ctx, side) + "'s chain: " + dot(ch.reading)); });
      return { speaker: PAGE, text: text, embed: firstBlock(["dt-chain"]), select: null, sources: src, synthetic: d.synthetic,
        followups: ["what prompt was given?", "is the answer grounded?", "where did the tokens go?"] };
    },
  });

  intent("dt-evolve", {
    family: "page", example: "how did the agent evolve?",
    when: function (d) { return !!d.dataEvo; }, absent: function (d) { return d.evo ? "this lineage carries no data_evolution section (rerun the command to add it)" : "this report has no self-evolving lineage"; },
    match: function (q, E) { return kw(q, ["how did the agent evolve", "how did it evolve", "evolve from the data", "what data", "data triggered", "triggered", "evidence episodes", "from the data", "what did it read before", "evolve", "evolved"]) * 2 + (E.step || E.stepInto ? 2 : 0); },
    answer: function (q, E, d) {
      var de = d.dataEvo, key = E.step || E.stepInto || null, text = [], src = [];
      var stepRow = key ? de.steps.filter(function (s) { return s.from + "→" + s.to === key; })[0] : null;
      if (key && !stepRow) return cannot("I can't find that step in this lineage", d);
      if (stepRow) {
        var i = de.steps.indexOf(stepRow), ev = stepRow.evidence || {}, ch = stepRow.change || {}, bh = stepRow.behaviour || {}, ef = stepRow.effect || {}, el = stepRow.eval || {};
        src.push("aggregate.data_evolution.steps[" + i + "].reading", "aggregate.data_evolution.steps[" + i + "].evidence", "aggregate.data_evolution.steps[" + i + "].change", "aggregate.data_evolution.steps[" + i + "].behaviour", "aggregate.data_evolution.steps[" + i + "].effect", "aggregate.data_evolution.steps[" + i + "].eval");
        text.push("At " + key + ", " + dot(stepRow.reading || ""));
        var eps = Array.isArray(ev.episodes) ? ev.episodes : [];
        text.push("The evidence: " + plural(eps.length, "episode") + " cited (" + count(ev.found) + " found, " + count(ev.failures) + " failures)" + (eps.length ? " — " + eps.join(", ") : "") + (Array.isArray(ev.data) && ev.data.length ? "; they read " + ev.data.map(function (e) { return (Array.isArray(e.sources) ? e.sources.length : 0); }).reduce(function (a, b) { return a + b; }, 0) + " source reads in all" : "") + ".");
        var hunks = Array.isArray(ch.hunks) ? ch.hunks : [];
        text.push("The change: " + (ch.summary || "nothing the diff can name") + (Array.isArray(ch.rules_added) && ch.rules_added.length ? "; rules added: “" + ch.rules_added.join("”, “") + "”" : "") + (Array.isArray(ch.rules_removed) && ch.rules_removed.length ? "; rules removed: “" + ch.rules_removed.join("”, “") + "”" : "") + (Array.isArray(ch.protected_touched) && ch.protected_touched.length ? "; protected paths touched: " + ch.protected_touched.join(", ") : "") + (hunks.length ? " (" + plural(hunks.length, "prompt hunk") + ")" : "") + ". Behaviour: sources " + count(bh.sources_before) + " → " + count(bh.sources_after) + (isNum(bh.grounded_before) || isNum(bh.grounded_after) ? ", grounded " + pct(bh.grounded_before) + " → " + pct(bh.grounded_after) : "") + ". Effect: " + (ef.verdict || "unmeasured") + (ef.improvement && isNum(ef.improvement.point) ? ", P(" + stepRow.to + " > " + stepRow.from + ") " + pci(ef.improvement) + " on return" : "") + (ef.improvement_success && isNum(ef.improvement_success.point) ? ", " + pci(ef.improvement_success) + " on outcome" : "") + ". The eval: " + (Array.isArray(el.flags) && el.flags.length ? el.flags.map(function (f) { return f.metric + " " + dci(f.delta) + (f.learned ? " (learned)" : ""); }).join(", ") : "no flag") + (el.eval_gen ? "; advanced to " + el.eval_gen : "") + ".");
      } else {
        text.push(dot(de.narrative || ""));
        src.push("aggregate.data_evolution.narrative");
        var gens = Array.isArray(de.generations) ? de.generations : [];
        if (gens.length) { text.push("The instructions grew from " + plural(count(gens[0].instructions && gens[0].instructions.chars), "character") + " at " + gens[0].id + " to " + plural(count(gens[gens.length - 1].instructions && gens[gens.length - 1].instructions.chars), "character") + " at " + gens[gens.length - 1].id + "; ask “what data triggered " + (de.steps[0] ? de.steps[0].from + "→" + de.steps[0].to : "a step") + "” for one step in full."); src.push("aggregate.data_evolution.generations[].instructions.chars"); }
      }
      var other = de.steps.filter(function (s) { return s.from + "→" + s.to !== key; })[0];
      return { speaker: d.cov ? evalSpeaker(d) : PAGE, text: text, embed: firstBlock(["dt-evolution"]), select: key ? { family: "data", value: { gen: key } } : null, sources: src, synthetic: true, subject: key ? { step: key, gen: key.split("→")[1] } : null,
        followups: [other ? "what data triggered " + other.from + "→" + other.to + "?" : null, key ? "what changed at " + key + "?" : "how did the agent evolve?", d.cov ? "what did you learn?" : "which generation should I keep?"] };
    },
  });

  // ---- the dashboard, asked for

  intent("help", {
    family: "page", example: "what can you answer?",
    when: function () { return true; },
    match: function (q) { return kw(q, ["help", "what can you answer", "what can i ask", "what can you do", "what do you know", "catalogue", "catalog", "list the blocks", "all the blocks", "which blocks", "what blocks"]) * 3; },
    answer: function (q, E, d) {
      var cat = catalogue(), byView = {};
      cat.forEach(function (b) { (byView[b.view] = byView[b.view] || []).push(b); });
      // every view the catalogue names, the known ones first in the tab order, then any the page added
      var views = Object.keys(VIEW_LABEL).filter(function (v) { return byView[v]; });
      Object.keys(byView).forEach(function (v) { if (views.indexOf(v) < 0) views.push(v); });
      var text = ["This report has " + plural(cat.length, "block") + " I can show, by view: " + views.map(function (v) { return viewLabel(v) + " " + byView[v].length; }).join(", ") + ". Ask \"show <block>\" by its title, or click one below; a title in the answer opens it in its own view."];
      if (d.cov) text.push("And the eval that watched " + d.family + " answers for itself: what it learned, why it rejected a candidate, what it would have caught earlier, whether it trusts itself, which generation to keep, what a metric says, which probes fired, what it saw at a step" + (d.ec ? ", and how " + (d.other || "the other lineage") + " compares" : "") + ".");
      var groups = views.map(function (v) { return { title: viewLabel(v), chips: byView[v].map(function (b) { return { label: b.title, q: showQ(b.title) }; }) }; });
      return { speaker: PAGE, text: text, embed: null, select: null, sources: ["AgentDiff.catalogue()"], groups: groups, followups: defaultChips(d).slice(0, 4) };
    },
  });

  intent("verdict", {
    family: "page", example: "what happened?",
    when: function (d, ctx) { return !!(ctx.report && ctx.report.verdict_card && Array.isArray(ctx.report.verdict_card.lines)); }, absent: function () { return "this report has no pair to give a verdict on"; },
    match: function (q, E) { return kw(q, ["what happened", "verdict", "who won", "why did it fail", "what went wrong", "the cause", "decisive", "outcome", "summary", "summarise", "summarize", "tl dr", "tldr"]) * 2 + (E.task ? 1 : 0); },
    answer: function (q, E, d, ctx) {
      if (E.task && E.task !== ctx.task && typeof ctx.selectTask === "function") { try { ctx.selectTask(E.task); } catch (err) { /* stays */ } }
      var r = ctx.report, lines = r.verdict_card.lines, text = [], src = [];
      var by = {}; lines.forEach(function (l) { if (l && l.key) by[l.key] = l; });
      ["verdict", "cause", "cost", "confidence"].forEach(function (k) { if (by[k] && by[k].text) { text.push(dot(by[k].text)); src.push("report.verdict_card.lines[" + k + "]" + (by[k].source ? " ← " + by[k].source : "")); } });
      if (r.diagnosis && r.diagnosis.verdict && text.length < 4) { text.push(dot(r.diagnosis.verdict)); src.push("report.diagnosis.verdict"); }
      return { speaker: PAGE, text: [text.join(" ")], embed: firstBlock(["verdict-card", "walkthrough", "diagnosis"]), select: null, sources: src,
        followups: ["where did the time go?", "show the tools", "show the diagnosis", "show the trajectory map"] };
    },
  });

  intent("better", {
    family: "page", example: "which agent is better?",
    when: function (d) { return !!(d.ec || d.stats || d.scorecard); }, absent: function () { return "this report has no statistics that rank the agents"; },
    match: function (q, E) { return kw(q, ["which agent is better", "which policy is better", "which is better", "who is better", "better agent", "better policy", "which one wins", "who wins", "improvement", "probability of improvement", "iqm", "beats", "stronger", "which agent", "which policy", "better"]) * 2 + (E.agent ? 1 : 0); },
    answer: function (q, E, d, ctx) {
      var text = [], src = [];
      // two lineages compared: "better" is the four axes, unless the reader asks about the policies of the pair
      if (d.ec && !kw(q, ["policy", "policies"])) { var card = INTENT_BY_ID["eval-compare"].answer(q, E, d, ctx); card.intent = "eval-compare"; return card; }
      if (d.stats) {
        var st = d.stats, ag = st.aggregates, pols = st.policies, imp = st.improvement;
        text.push(pols.map(function (p) { var a = ag[p]; return p + ": IQM " + (st.metric_label || st.metric) + " " + ci(a && a.iqm) + " over " + plural(count(a && a.n), "run"); }).join("; ") + (imp && isNum(imp.point) ? "; P(" + imp.b + " > " + imp.a + ") " + pci(imp) + " over " + plural(count(imp.tasks_used), "shared task") + "." : "."));
        if (imp && imp.reading) text.push(dot(imp.reading));
        if (st.advisory && st.advisory.message) text.push(dot(st.advisory.message));
        src.push("aggregate.rl.stats.aggregates[].iqm", "aggregate.rl.stats.improvement", "aggregate.rl.stats.advisory.message");
        return { speaker: PAGE, text: text, embed: firstBlock(["rl-stats-improvement", "rl-stats-aggregate"]), select: null, sources: src, synthetic: d.synthetic,
          followups: ["show the aggregate score", "show the performance profile", "is the reward sound?", "show the policy delta"] };
      }
      var sc = d.scorecard, names = Object.keys(sc.agents);
      text.push(names.map(function (n) { var s = sc.agents[n].rates && sc.agents[n].rates.success; return n + " solved " + count(s && s.successes) + " of " + count(s && s.runs) + " (" + pct(s && s.rate) + (s && Array.isArray(s.ci95) ? " [" + pct(s.ci95[0]) + ", " + pct(s.ci95[1]) + "]" : "") + ")"; }).join("; ") + ".");
      if (sc.note) text.push(dot(String(sc.note).split(";")[0]));
      src.push("aggregate.scorecard.agents[].rates.success", "aggregate.scorecard.note");
      return { speaker: PAGE, text: text, embed: "scorecard", select: null, sources: src, followups: ["what should I change?", "where do failures start?", "show the routing"] };
    },
  });

  intent("cost", {
    family: "page", example: "where did the time go?",
    when: function (d, ctx) { return !!((ctx.report && (ctx.report.tradeoff || ctx.report.timing)) || d.efficiency); }, absent: function () { return "this report has no cost or timing sections"; },
    match: function (q) { return kw(q, ["cost", "costs", "tokens", "token", "spend", "spent", "cheaper", "expensive", "price", "usd", "dollars", "time", "latency", "slow", "slower", "seconds", "where did the time go", "took", "budget", "per task", "per success", "efficiency"]) * 2; },
    answer: function (q, E, d, ctx) {
      var text = [], src = [], timeQ = kw(q, ["time", "latency", "slow", "slower", "seconds", "took", "where did the time go"]) > 0;
      var batchQ = kw(q, ["per task", "per success", "across", "batch", "all tasks", "efficiency"]) > 0 || !ctx.report;
      if (batchQ && d.efficiency) {
        var pa = d.efficiency.per_agent;
        text.push(Object.keys(pa).map(function (k) { var a = pa[k], c = a.cost_per_success || {}; return a.agent + ": " + (isNum(c.value_usd) ? usd(c.value_usd) + " per success" : "no cost per success" + (c.reason ? " (" + c.reason + ")" : "")) + (isNum(c.total_cost_usd) ? " (" + usd(c.total_cost_usd) + " over " + count(c.successes) + " of " + plural(count(c.runs), "run") + ")" : ""); }).join("; ") + ".");
        src.push("aggregate.efficiency.per_agent[].cost_per_success");
        var m = d.agg.means;
        if (m && m.a && m.b && d.agents.length === 2) { text.push("Per task on average, " + d.agents[0] + " spent " + num(m.a.tokens, 0) + " tokens, " + usd(m.a.cost_usd) + " and " + secs(m.a.latency_s) + "; " + d.agents[1] + " " + num(m.b.tokens, 0) + " tokens, " + usd(m.b.cost_usd) + " and " + secs(m.b.latency_s) + "."); src.push("aggregate.means"); }
        return { speaker: PAGE, text: text, embed: firstBlock(["batch-summary", "recommendations"]), select: null, sources: src, followups: ["what should I change?", "where did the time go?", "which agent is better?"] };
      }
      var r = ctx.report, t = r.tradeoff, md = r.metrics_delta, tm = r.timing;
      if (t && t.statement) { text.push(dot(t.statement)); src.push("report.tradeoff.statement"); }
      if (md) { text.push(["tokens", "latency_s", "steps", "cost_usd"].filter(function (k) { return md[k] && (isNum(md[k].a) || isNum(md[k].b)); }).map(function (k) { var v = md[k]; var f = k === "latency_s" ? secs : k === "cost_usd" ? usd : function (x) { return num(x, 0); }; return (k === "latency_s" ? "latency" : k === "cost_usd" ? "cost" : k) + " " + f(v.a) + " vs " + f(v.b); }).join(", ") + " (" + (r.a && r.a.agent ? r.a.agent.name : "A") + " vs " + (r.b && r.b.agent ? r.b.agent.name : "B") + ")."); src.push("report.metrics_delta"); }
      if (tm && timeQ) { ["a", "b"].forEach(function (side) { var s = tm[side]; if (s && isNum(s.total_s)) { var w = (s.steps || []).filter(function (x) { return x && x.wasted; }); text.push((r[side] && r[side].agent ? r[side].agent.name : side) + " took " + secs(s.total_s) + " over " + plural((s.steps || []).length, "step") + (w.length ? ", " + plural(w.length, "wasted step") : "") + "."); } }); src.push("report.timing"); }
      if (t && t.caveat) { text.push(dot(t.caveat)); src.push("report.tradeoff.caveat"); }
      return { speaker: PAGE, text: text, embed: timeQ ? firstBlock(["time", "impact", "treemap", "deltas"]) : firstBlock(["deltas", "time", "treemap", "impact"]), select: null, sources: src,
        followups: ["show the tools", "what happened?", "cost per task", "show the treemap"] };
    },
  });

  intent("tools", {
    family: "page", example: "show the tools",
    when: function (d, ctx) { return !!(ctx.report && ctx.report.tools_profile); }, absent: function () { return "this report has no tool profile"; },
    match: function (q) { return kw(q, ["tool", "tools", "tool calls", "which tool", "called", "calls", "retries", "errors"]) * 3; },
    answer: function (q, E, d, ctx) {
      var r = ctx.report, tp = r.tools_profile, text = [], src = ["report.tools_profile"];
      ["a", "b"].forEach(function (side) {
        var s = tp[side];
        if (!s || s.measurable === false || !s.tools) return;
        var names = Object.keys(s.tools);
        text.push((s.agent || side) + " called " + (names.length ? names.map(function (n) { var t = s.tools[n]; return n + " ×" + count(t.calls) + (count(t.errors) ? " (" + plural(t.errors, "error") + ")" : "") + (count(t.wasted_calls) ? " (" + t.wasted_calls + " wasted)" : ""); }).join(", ") : "no tool") + ".");
      });
      if (!text.length) text.push("Neither run's tool profile is measurable here.");
      return { speaker: PAGE, text: text, embed: firstBlock(["tool-behaviour", "tool-matrix", "recovery-errors"]), select: null, sources: src, followups: ["where did the time go?", "what happened?", "show the tool matrix"] };
    },
  });

  intent("training", {
    family: "page", example: "is the reward sound?",
    when: function (d) { return !!(d.stats || (d.rl && d.rl.audit)); }, absent: function () { return "this report has no training statistics"; },
    match: function (q) { return kw(q, ["training", "policy", "policies", "reward", "rewards", "episodes", "learning curve", "learning curves", "profile", "critic", "audit", "return", "returns", "preference", "preferences", "advantage"]) * 2; },
    answer: function (q, E, d) {
      var text = [], src = [], embed;
      if (kw(q, ["profile"])) embed = "rl-stats-profile";
      else if (kw(q, ["reward", "rewards", "audit"])) embed = "rl-audit-reward";
      else if (kw(q, ["critic", "advantage"])) embed = firstBlock(["rl-audit-critic", "rl-advantage"]);
      else if (kw(q, ["curve", "curves", "episodes"])) embed = "rl-curves";
      else if (kw(q, ["preference", "preferences"])) embed = "rl-preferences";
      else embed = "rl-stats-aggregate";
      if (d.stats && d.stats.narrative) { text.push(dot(d.stats.narrative)); src.push("aggregate.rl.stats.narrative"); }
      else if (d.rl && d.rl.narrative) { text.push(dot(d.rl.narrative)); src.push("aggregate.rl.narrative"); }
      var au = d.rl && d.rl.audit;
      if (au && /reward|audit|sound/.test(q) && au.reward && au.reward.narrative) { text.push(dot(au.reward.narrative)); src.push("aggregate.rl.audit.reward.narrative"); }
      if (!inCatalogue(embed)) embed = firstBlock(["rl-stats-aggregate", "rl-curves", "rl-here"]);
      return { speaker: PAGE, text: text.length ? text : ["The training section is here, but carries no narrative to quote."], embed: embed, select: null, sources: src, synthetic: d.synthetic,
        followups: ["which policy is better?", "show the reward map", "show the performance profile", "show every episode"] };
    },
  });

  intent("evo-changed", {
    family: "page", example: "what changed at a step?",
    when: function (d) { return d.steps.length > 0; }, absent: function () { return "this report has no lineage"; },
    match: function (q, E) { return kw(q, ["changed", "change", "changes", "diff", "modified", "modify", "edit", "edited", "rewrote", "what did it change", "what did the step change"]) * 2 + (E.step || E.stepInto ? 2 : 0); },
    answer: function (q, E, d) {
      var key = E.step || E.stepInto || stepKey(d.steps[d.steps.length - 1]), s = d.stepByKey[key];
      if (!s) return cannot("I can't find that step in this lineage", d);
      var i = d.steps.indexOf(s), text = [], src = ["aggregate.evolution.steps[" + i + "].diff.summary", "aggregate.evolution.steps[" + i + "].evidence", "aggregate.evolution.steps[" + i + "].reading"];
      text.push("At " + key + " (" + (s.mechanism || "step") + ") the agent changed: " + (s.diff && s.diff.summary ? s.diff.summary : "nothing the diff can name") + (s.evidence && s.evidence.summary ? " — citing " + s.evidence.summary + (s.evidence_check && s.evidence_check.reading ? " (" + s.evidence_check.reading + ")" : "") : "") + ".");
      if (s.diff && Array.isArray(s.diff.protected_touched) && s.diff.protected_touched.length) text.push("It touched a protected path: " + s.diff.protected_touched.join(", ") + ".");
      if (s.reading) text.push(dot(s.reading));
      return { speaker: PAGE, text: text, embed: "evo-step", select: { family: "evolution", value: { gen: s.to } }, sources: src, synthetic: d.synthetic, subject: { step: key, gen: s.to },
        followups: ["did " + key + " help?", d.cov ? "what did the agent do at " + key + "?" : "show the lineage", "which generation should I keep?"] };
    },
  });

  intent("evo-helped", {
    family: "page", example: "did a step help?",
    when: function (d) { return d.steps.length > 0; }, absent: function () { return "this report has no lineage"; },
    match: function (q, E) { return kw(q, ["help", "helped", "work", "worked", "pay", "paid", "improve", "improved", "good step", "worth it", "was it good", "did it help", "hurt"]) * 2 + (E.step || E.stepInto ? 2 : 0); },
    answer: function (q, E, d) {
      var key = E.step || E.stepInto || stepKey(d.steps[d.steps.length - 1]), s = d.stepByKey[key];
      if (!s) return cannot("I can't find that step in this lineage", d);
      var i = d.steps.indexOf(s), eff = s.effect || {}, imp = eff.improvement, imps = eff.improvement_success, text = [], src = ["aggregate.evolution.steps[" + i + "].effect", "aggregate.evolution.steps[" + i + "].verdict", "aggregate.evolution.steps[" + i + "].reading"];
      var word = s.verdict === "improved" ? "Yes" : s.verdict === "regressed" || s.verdict === "gamed" || s.verdict === "forgot" ? "No" : "Not clearly";
      text.push(word + " — the engine's verdict on " + key + " is " + (s.verdict || "unmeasured") + (Array.isArray(s.flags) && s.flags.length ? " (flags: " + s.flags.join(", ") + ")" : "") + (imp && isNum(imp.point) ? ": P(" + s.to + " > " + s.from + ") " + pci(imp) + " on return" + (imp.noisy ? " — spans the coin flip" : "") : ": the effect is not measured" + (eff.reason ? " (" + eff.reason + ")" : "")) + (imps && isNum(imps.point) ? ", " + pci(imps) + " on outcome" : "") + ".");
      if (s.reading) text.push(dot(s.reading));
      return { speaker: PAGE, text: text, embed: "evo-step", select: { family: "evolution", value: { gen: s.to } }, sources: src, synthetic: d.synthetic, subject: { step: key, gen: s.to },
        followups: ["what changed at " + key + "?", "which generation should I keep?", "show the steps"] };
    },
  });

  intent("evo-keep", {
    family: "page", example: "which generation should I keep?",
    when: function (d) { return !d.cov && !!(d.evo && d.evo.recommended); }, absent: function () { return "this report has no lineage recommendation"; },
    match: function (q) { return kw(q, ["which generation", "should i keep", "keep", "recommend", "recommended", "recommendation", "ship", "deploy", "best generation", "pick"]) * 2; },
    answer: function (q, E, d) {
      var r = d.evo.recommended, text = ["Keep " + r.id + (r.is_last ? " (the last generation)" : "") + (r.why ? ": " + dot(r.why) : ".")];
      if (d.evo.best && d.evo.best.why && d.evo.best.id !== r.id) text.push("The best-scoring generation is " + d.evo.best.id + ": " + dot(d.evo.best.why));
      return { speaker: PAGE, text: text, embed: firstBlock(["evo-lineage", "evo-steps"]), select: { family: "evolution", value: { gen: r.id } }, sources: ["aggregate.evolution.recommended", "aggregate.evolution.best"], synthetic: d.synthetic, subject: { gen: r.id, step: stepOf({ gen: r.id }, d) },
        followups: ["what changed at " + stepKey(worstStep(d)) + "?", "show the lineage", d.other ? "compare with " + d.other : "is the evolution sound?"] };
    },
  });

  intent("evo-overview", {
    family: "page", example: "show the lineage",
    when: function (d) { return !!d.evo; }, absent: function () { return "this report has no lineage"; },
    match: function (q) { return kw(q, ["lineage", "evolution", "evolve", "evolved", "evolving", "generations", "how did it evolve", "self-evolving", "the agent's steps", "trajectory"]) * 2; },
    answer: function (q, E, d) {
      var ev = d.evo, tr = ev.trajectory || {}, text = [], src = ["aggregate.evolution.narrative", "aggregate.evolution.trajectory", "aggregate.evolution.recommended"];
      var first = String(ev.narrative || "").split(";")[0];
      text.push(dot(first) + " Of its " + plural(count(tr.steps), "step") + ": " + count(tr.improved) + " improved, " + count(tr.regressed) + " regressed, " + count(tr.flat) + " flat, " + count(tr.gamed) + " gamed, " + count(tr.forgot) + " forgot, " + count(tr.traded) + " traded; " + count(tr.accepted_on_noise) + " accepted on noise" + (isNum(tr.net_iqm_delta) ? "; net task-balanced IQM " + signed(tr.net_iqm_delta) : "") + ".");
      if (ev.recommended && ev.recommended.id) text.push("Recommended: " + ev.recommended.id + (ev.recommended.why ? " — " + dot(ev.recommended.why) : "."));
      return { speaker: PAGE, text: text, embed: firstBlock(["evo-lineage", "evo-steps"]), select: null, sources: src, synthetic: d.synthetic,
        followups: ["which generation should I keep?", "what changed at " + stepKey(worstStep(d)) + "?", "show the timescape", "is the evolution sound?"] };
    },
  });

  intent("show", {
    family: "page", example: "show a block",
    when: function () { return true; },
    match: function (q, E) { return E.block ? E.blockScore + (kw(q, ["show", "open", "draw", "see", "display", "render", "show me", "let me see"]) ? 1 : 0) : 0; },
    answer: function (q, E, d, ctx) {
      var b = E.block, text = [], src = [];
      var sum = summaryFor(b.id, d, ctx);
      text.push(b.title + " — " + dot(b.question));
      if (sum) { text.push(dot(sum.text)); src.push(sum.source); }
      var sel = selectionFor(b.id, E, d);
      return { speaker: PAGE, text: text, embed: b.id, select: sel, sources: src.length ? src : ["the block's own reading of the report"], synthetic: d.synthetic && /^(evo|evc|cov)-/.test(b.id),
        followups: siblingsOf(b).slice(0, 3).map(function (x) { return showQ(x.title); }) };
    },
  });

  var INTENT_BY_ID = {};
  INTENTS.forEach(function (i) { INTENT_BY_ID[i.id] = i; });

  //: one engine sentence for a block the reader asked to see, where the report has one
  function summaryFor(id, d, ctx) {
    var r = ctx.report || {};
    var ev = d.evo, ec = d.ec, c = d.cov, st = d.stats;
    switch (id) {
      case "verdict-card": case "walkthrough": case "verdict": { var l = r.verdict_card && r.verdict_card.lines && r.verdict_card.lines[0]; return l && l.text ? { text: l.text, source: "report.verdict_card.lines[0]" } : null; }
      case "diagnosis": return r.diagnosis && r.diagnosis.verdict ? { text: r.diagnosis.verdict, source: "report.diagnosis.verdict" } : null;
      case "time": case "impact": case "treemap": case "deltas": return r.tradeoff && r.tradeoff.statement ? { text: r.tradeoff.statement, source: "report.tradeoff.statement" } : null;
      case "evo-lineage": case "evo-steps": case "evo-matrix": case "evo-timescape": case "evo-drift": return ev && ev.recommended && ev.recommended.why ? { text: "Recommended " + ev.recommended.id + ": " + ev.recommended.why, source: "aggregate.evolution.recommended.why" } : null;
      case "evo-step": { var s = d.steps[d.steps.length - 1]; return s && s.reading ? { text: s.reading, source: "aggregate.evolution.steps[" + (d.steps.length - 1) + "].reading" } : null; }
      case "evo-integrity": return ev && ev.integrity && Array.isArray(ev.integrity.touched) ? { text: plural(ev.integrity.touched.length, "protected touch") + (ev.integrity.touched.length ? ": " + ev.integrity.touched.map(function (t) { return t.path + " at " + t.from_gen + "→" + t.to_gen + " (" + t.direction + ")"; }).join("; ") : ""), source: "aggregate.evolution.integrity.touched" } : null;
      case "evc-curves": case "evc-race": return ec && ec.race && ec.race.reading ? { text: ec.race.reading, source: "aggregate.evolution_compare.race.reading" } : null;
      case "evc-verdict": case "evc-process": case "evc-pair": case "evc-mechanisms": case "evc-divergence": return ec && ec.verdict && ec.verdict.reading ? { text: ec.verdict.reading, source: "aggregate.evolution_compare.verdict.reading" } : null;
      case "cov-flow": case "cov-hindsight": case "cov-matrix": case "cov-metric": case "cov-probes": case "cov-integrity": return c && c.flow && c.flow.summary && c.flow.summary.sentence ? { text: c.flow.summary.sentence, source: "aggregate.coevolution.flow.summary.sentence" } : c && c.narrative ? { text: c.narrative, source: "aggregate.coevolution.narrative" } : null;
      case "scorecard": return d.scorecard ? { text: Object.keys(d.scorecard.agents).map(function (n) { var s = d.scorecard.agents[n].rates && d.scorecard.agents[n].rates.success; return n + " " + count(s && s.successes) + "/" + count(s && s.runs) + " (" + pct(s && s.rate) + ")"; }).join("; "), source: "aggregate.scorecard.agents[].rates.success" } : null;
      case "batch-summary": return d.agg.success_rate ? { text: (d.agents[0] || "A") + " succeeds on " + pct(d.agg.success_rate.a) + " of tasks, " + (d.agents[1] || "B") + " on " + pct(d.agg.success_rate.b), source: "aggregate.success_rate" } : null;
      case "recommendations": { var rec = Array.isArray(d.agg.recommendations) && d.agg.recommendations[0]; return rec && rec.finding ? { text: rec.finding, source: "aggregate.recommendations[0].finding" } : null; }
      case "tool-behaviour": case "tool-matrix": return r.tools_profile ? INTENT_BY_ID.tools.answer(" tools ", {}, d, ctx).text.slice(0, 1).map(function (t) { return { text: t, source: "report.tools_profile" }; })[0] : null;
      default:
        if (/^rl-/.test(id)) { if (st && st.narrative) return { text: st.narrative, source: "aggregate.rl.stats.narrative" }; if (d.rl && d.rl.narrative) return { text: d.rl.narrative, source: "aggregate.rl.narrative" }; }
        return null;
    }
  }
  //: the selection a block should open with, from what the question named
  function selectionFor(id, E, d) {
    if (/^evo-/.test(id) && E.gen) return { family: "evolution", value: { gen: E.gen } };
    if (id === "cov-metric" && E.metric) return { family: "coevolution", value: { metric: E.metric } };
    if (/^cov-/.test(id) && E.step) return { family: "coevolution", value: { step: E.step, evalGen: null, candidate: null } };
    return null;
  }
  function siblingsOf(b) { return catalogue().filter(function (x) { return x.view === b.view && x.id !== b.id; }); }
  //: "show the lineage", not "show The lineage": a title's first letter lowered unless it opens an acronym
  function showQ(title) { var t = String(title || ""); return "show " + (t.length > 1 && t.charAt(1) === t.charAt(1).toUpperCase() && /[A-Z]/.test(t.charAt(1)) ? t : t.charAt(0).toLowerCase() + t.slice(1)); }

  // ---------------------------------------------------------- follow-ups
  //
  // The last answer's subject: its intent and the agent, side, run, step,
  // generation, metric, candidate, family and task it was about. A question
  // that leans on it resolves into a full question, in three ways:
  //
  //   a carrying phrase   "and for bolt-v3?", "what about g3→g4?", "same for m2"
  //   a bare name         "bolt-v3", "g3→g4", "m2", "r2"
  //   a pronoun           "did it help?", "and its tokens?", "the other one"
  //
  // A question with an intent of its own keeps it and only borrows the
  // subject it lacks ("and its tokens?" after an agent asks that agent's
  // budget); a question without one carries the last intent ("and for
  // bolt-v3?" after "what did memo-agent fetch?" asks what bolt-v3
  // fetched). The resolved question is routed with its intent forced, so
  // the words of a template never re-route it, and it is shown under the
  // typed one. Without an earlier answer there is nothing to carry.

  var SUBJECT_KINDS = ["step", "gen", "metric", "candidate", "agent", "run", "family", "task"];
  var CARRY = ["what about", "how about", "and for", "and on", "and the", "and its", "and their", "same for", "the same for", "the same", "the other one", "the other", "other one", "again", "also", "as well", "instead"];
  var PRONOUNS = ["it", "its", "that", "this", "them", "those", "there", "that one", "this one"];
  //: phrases where the pronoun is part of the question, not a reference to the last answer
  var NOT_ANAPHORA = ["how did it evolve", "which one wins", "in this bundle", "produced this", "was it good", "worth it", "is it", "what is this"];
  var OTHER = /\s(the other one|the other|other one)\s/;
  function carrying(nq) { return CARRY.some(function (p) { return nq.indexOf(" " + p + " ") >= 0; }); }
  function pronoun(nq) {
    if (NOT_ANAPHORA.some(function (p) { return nq.indexOf(" " + p + " ") >= 0; })) return false;
    return PRONOUNS.some(function (p) { return nq.indexOf(" " + p + " ") >= 0; });
  }
  //: the question without its carrying phrases and pronouns: what it asks on its own
  function residue(nq) {
    var s = nq;
    CARRY.concat(PRONOUNS).sort(function (a, b) { return b.length - a.length; }).forEach(function (p) { s = s.split(" " + p + " ").join(" "); });
    return " " + s.replace(/\s+/g, " ").trim() + " ";
  }
  var EMPTY_E = { step: null, stepInto: null, gen: null, metric: null, candidate: null, block: null, blockScore: 0, family: null, agent: null, task: null, run: null };
  //: the intent a question asks for by its own words, entities aside
  function ownIntent(nq, d) {
    var best = null;
    INTENTS.forEach(function (it) {
      var s = 0;
      try { s = it.match(nq, EMPTY_E, d) || 0; } catch (err) { s = 0; }
      if (s > 0 && (!best || s > best.score)) best = { it: it, score: s };
    });
    return best ? best.it : null;
  }
  //: what a question names, by kind
  function namedKinds(E) {
    var out = {};
    if (E.step) { out.step = E.step; out.gen = E.step.split("→")[1]; }
    else if (E.gen) { out.gen = E.gen; if (E.stepInto) out.step = E.stepInto; }
    if (E.metric) out.metric = E.metric;
    if (E.candidate) out.candidate = E.candidate;
    if (E.agent) { out.agent = E.agent; out.run = E.run ? E.run.key : null; }
    if (E.family) out.family = E.family;
    if (E.task) out.task = E.task;
    return out;
  }
  function sideOfAgent(name, ctx) {
    var r = ctx && ctx.report;
    if (!r || !name) return null;
    return ["a", "b"].filter(function (side) { return r[side] && r[side].agent && String(r[side].agent.name) === String(name); })[0] || null;
  }
  //: the step a subject stands on: its own, or the step into its generation
  function stepOf(S, d) {
    if (S.step) return S.step;
    if (S.gen) { var into = d.steps.filter(function (x) { return x.to === S.gen; })[0]; return into ? stepKey(into) : null; }
    return null;
  }
  //: a run as a question names it: the agent, plus its task and run id when the run is not the pair in view
  function runLabel(S, d, ctx) {
    var row = S.run ? d.runByKey[S.run] : null;
    if (!row) return S.agent || "";
    if (row.report && ctx && row.report === ctx.report) return row.agent;
    var same = d.runs.filter(function (r) { return r.agent === row.agent; });
    return same.length > 1 ? row.agent + " on " + row.task + " (" + row.run_id + ")" : row.agent;
  }
  //: the other one: the pair's other side, the other of two agents or policies, the other lineage
  function otherOf(S, d, ctx) {
    var r = ctx && ctx.report;
    if (S.side && r) {
      var side = S.side === "a" ? "b" : "a";
      if (r[side] && r[side].agent) { var row = pairRuns(d, ctx).filter(function (x) { return x.side === side; })[0]; return { agent: String(r[side].agent.name), side: side, run: row ? row.key : null }; }
    }
    if (S.agent) {
      var names = d.agents.length === 2 ? d.agents : d.policies.length === 2 ? d.policies : [];
      var o = names.filter(function (n) { return n !== S.agent; })[0];
      if (o) { var orow = d.runs.filter(function (x) { return x.agent === o && (!S.task || x.task === S.task); })[0]; return { agent: o, side: sideOfAgent(o, ctx), run: orow ? orow.key : null }; }
    }
    if (S.family) { var f = d.families.filter(function (x) { return x && x !== S.family; })[0]; if (f) return { family: f }; }
    return null;
  }
  //: the last subject with what the new question names laid over it (or its other one)
  function merge(base, named, d, ctx, other) {
    var S = {};
    for (var k in base) if (Object.prototype.hasOwnProperty.call(base, k)) S[k] = base[k];
    if (other) { var o = otherOf(base, d, ctx); if (!o) return null; for (var k2 in o) if (Object.prototype.hasOwnProperty.call(o, k2)) S[k2] = o[k2]; }
    if (named.step || named.gen) { S.step = named.step || null; S.gen = named.gen || null; }
    if (named.metric) S.metric = named.metric;
    if (named.candidate) S.candidate = named.candidate;
    if (named.agent) { S.agent = named.agent; S.run = named.run || null; S.side = sideOfAgent(named.agent, ctx); }
    if (named.family) S.family = named.family;
    if (named.task) S.task = named.task;
    return S;
  }
  /* The question each intent asks about a subject of a given kind — the
   * templates a follow-up resolves into and the chips are built from. An
   * intent absent here takes the generic rewrite: the last question with
   * the old name replaced by the new one, or "… for <name>?". */
  var ASK = {
    "eval-rejected": { kinds: ["candidate", "metric"], ask: function (k, S) { return "why did you reject " + S[k] + "?"; } },
    "eval-metric": { kinds: ["metric"], ask: function (k, S) { return "what does " + S.metric + " say?"; } },
    "eval-saw": { kinds: ["step", "gen"], ask: function (k, S, d) { var s = stepOf(S, d); return s ? "what did the agent do at " + s + "?" : null; } },
    "eval-compare": { kinds: ["family"], ask: function (k, S) { return "compare with " + S.family; } },
    "lv-budget": { kinds: ["agent", "run"], ask: function (k, S, d, ctx) { return "where did the tokens go for " + runLabel(S, d, ctx) + "?"; } },
    "lv-fetch": { kinds: ["agent", "run"], ask: function (k, S, d, ctx) { return "what did " + runLabel(S, d, ctx) + " fetch?"; } },
    "lv-waste": { kinds: ["agent", "run"], ask: function (k, S, d, ctx) { return "how many fetches were wasted by " + runLabel(S, d, ctx) + "?"; } },
    "evo-changed": { kinds: ["step", "gen"], ask: function (k, S, d) { var s = stepOf(S, d); return s ? "what changed at " + s + "?" : null; } },
    "evo-helped": { kinds: ["step", "gen"], ask: function (k, S, d) { var s = stepOf(S, d); return s ? "did " + s + " help?" : null; } },
    "dt-evolve": { kinds: ["step", "gen"], ask: function (k, S, d) { var s = stepOf(S, d); return s ? "what data triggered " + s + "?" : null; } },
    "verdict": { kinds: ["task"], ask: function (k, S) { return "what happened on " + S.task + "?"; } },
  };
  //: the kind of a subject an intent's template can take: the preferred one, else the first it holds
  function kindFor(id, S, prefer) {
    var t = ASK[id];
    if (!t || !S) return null;
    if (prefer && t.kinds.indexOf(prefer) >= 0 && S[prefer]) return prefer;
    return t.kinds.filter(function (k) { return !!S[k]; })[0] || null;
  }
  function askFor(id, S, kind, d, ctx) { var t = ASK[id]; if (!t || !kind) return null; try { return t.ask(kind, S, d, ctx) || null; } catch (err) { return null; } }
  //: the generic rewrite: the last question with the old name swapped for the new, else "… for <name>?"
  function generic(prev, S, kind, d, ctx) {
    var q = String(prev.q || "");
    if (!kind) return q;
    var oldV = kind === "run" ? runLabel(prev.subject, d, ctx) : prev.subject[kind], newV = kind === "run" ? runLabel(S, d, ctx) : S[kind];
    if (!newV) return q;
    if (oldV && String(oldV) !== String(newV)) {
      var at = q.toLowerCase().indexOf(String(oldV).toLowerCase());
      if (at >= 0) return q.slice(0, at) + newV + q.slice(at + String(oldV).length);
    }
    if (q.toLowerCase().indexOf(String(newV).toLowerCase()) >= 0) return q;
    return q.replace(/[?.!]\s*$/, "") + " for " + newV + "?";
  }
  /* The resolution: null when the question stands on its own; {none} when
   * it leans on an answer that was never given; {other} when "the other
   * one" has no other; else {intent, resolved, subject}. */
  function followup(nq, E, d, ctx, prev) {
    // the question's own intent, by its words with the entities blanked; a question that is nothing but a carrying phrase has none
    var own = residue(nq).trim() ? ownIntent(nq, d) : null, named = namedKinds(E), kinds = Object.keys(named);
    var rid = /\s(r\d+)\s/.exec(nq), other = OTHER.test(nq);
    var anaphoric = carrying(nq) || pronoun(nq) || other;
    var S, kind;
    if (own) {
      if (!anaphoric || !prev) return null;
      S = merge(prev.subject, named, d, ctx, other);
      if (!S) return { other: true };
      kind = kindFor(own.id, S, kinds[0]);
      var q1 = kind ? askFor(own.id, S, kind, d, ctx) : null;
      return q1 ? { intent: own.id, resolved: q1, subject: S } : null;
    }
    if (!kinds.length && !rid && !anaphoric) return null;
    if (!prev) return { none: true };
    S = merge(prev.subject, named, d, ctx, other);
    if (!S) return { other: true };
    if (rid && !named.agent && S.agent) {
      var row = d.runs.filter(function (r) { return r.agent === S.agent && r.run_id === rid[1] && (!S.task || r.task === S.task); })[0] || d.runs.filter(function (r) { return r.agent === S.agent && r.run_id === rid[1]; })[0];
      if (!row) return { norun: rid[1], agent: S.agent };
      S.run = row.key; S.side = row.side || null; S.task = row.task; kinds.unshift("run");
    }
    kind = kindFor(prev.intent, S, kinds[0]);
    var q2 = kind ? askFor(prev.intent, S, kind, d, ctx) : null;
    return { intent: prev.intent, resolved: q2 || generic(prev, S, kinds[0] || null, d, ctx), subject: S };
  }
  //: the resolved question's entities, completed from the subject where its words name none
  function overlay(E, S, d) {
    if (!S) return E;
    if (S.run && d.runByKey[S.run] && (!E.agent || d.runByKey[S.run].agent === E.agent)) { E.run = d.runByKey[S.run]; E.agent = E.agent || E.run.agent; }
    if (!E.step && S.step) E.step = S.step;
    if (!E.gen && S.gen) E.gen = S.gen;
    ["metric", "candidate", "family", "task"].forEach(function (k) { if (!E[k] && S[k]) E[k] = S[k]; });
    if (!E.agent && S.agent) E.agent = S.agent;
    return E;
  }
  //: the subject of an answer: what the question named, corrected by what the intent actually answered
  function subjectOf(res, E, d, ctx) {
    var S = { intent: res.intent, agent: E.agent || null, run: E.run ? E.run.key : null, side: null, step: E.step || E.stepInto || null, gen: E.gen || null, metric: E.metric || null, candidate: E.candidate || null, family: E.family || null, task: E.task || null, block: E.block ? E.block.id : null };
    var cs = res.card && res.card.subject;
    if (cs && typeof cs === "object") for (var k in cs) if (Object.prototype.hasOwnProperty.call(cs, k) && cs[k] !== undefined) S[k] = cs[k];
    if (S.run && d.runByKey[S.run]) { var row = d.runByKey[S.run]; S.agent = S.agent || row.agent; S.side = S.side || row.side || null; S.task = S.task || row.task || null; }
    if (S.agent && !S.side) S.side = sideOfAgent(S.agent, ctx);
    if (S.step && !S.gen) S.gen = S.step.split("→")[1];
    return S;
  }
  //: two follow-ups that use the carried subject, from other intents' templates the report can answer
  var CHIP_ORDER = { step: ["evo-helped", "evo-changed", "eval-saw", "dt-evolve"], gen: ["evo-changed", "evo-helped", "eval-saw"], agent: ["lv-fetch", "lv-budget", "lv-waste"], run: ["lv-fetch", "lv-budget", "lv-waste"], metric: ["eval-metric", "eval-rejected"], candidate: ["eval-rejected"], family: ["eval-compare"], task: ["verdict"] };
  function carriedChips(S, d, ctx) {
    var out = [];
    if (!S) return out;
    ["step", "gen", "agent", "run", "metric", "candidate", "family", "task"].forEach(function (kind) {
      if (!S[kind] || out.length >= 2) return;
      (CHIP_ORDER[kind] || []).forEach(function (id) {
        if (out.length >= 2 || id === S.intent || !INTENT_BY_ID[id] || !safeWhen(INTENT_BY_ID[id], d, ctx)) return;
        var q = askFor(id, S, kind, d, ctx);
        if (q && out.indexOf(q) < 0) out.push(q);
      });
    });
    if (out.length < 2 && otherOf(S, d, ctx)) out.push("and the other one?");
    return out.slice(0, 2);
  }

  // -------------------------------------------------------------- router

  /* prev: the last answer's {intent, q, subject}, or null. Returns {intent,
   * E, card, resolved, subject}: `resolved` is the question the typed one
   * became (null when it stood on its own), `subject` what the answer was
   * about (null for a cannot card). */
  function route(q, d, ctx, prev) {
    var nq = norm(q), E = entities(nq, d), fu = null;
    try { fu = followup(nq, E, d, ctx, prev || null); } catch (err) { if (global.console) console.warn("AgentDiff chat: the follow-up could not be resolved", err); fu = null; }
    if (fu && fu.none) return { intent: null, E: E, card: cannot("there is no earlier answer to carry that from — ask a full question first, naming the agent, the step or the metric", d, ctx), resolved: null, subject: null };
    if (fu && fu.norun) return { intent: null, E: E, card: cannot("this page has no run " + fu.norun + " of " + fu.agent + " (" + plural(d.runs.filter(function (r) { return r.agent === fu.agent; }).length, "run") + " of it listed)", d, ctx), resolved: null, subject: null };
    if (fu && fu.other) return { intent: null, E: E, card: cannot("I can't tell which other one: the last answer was not about one agent, run or lineage", d, ctx), resolved: null, subject: null };
    var force = null;
    if (fu && fu.resolved) { nq = norm(fu.resolved); E = overlay(entities(nq, d), fu.subject, d); force = fu.intent; }
    var out = routeIntent(nq, E, d, ctx, force);
    out.resolved = fu && fu.resolved && fu.resolved.trim().toLowerCase() !== String(q).trim().toLowerCase() ? fu.resolved : null;
    out.subject = out.card && !out.card.cannot && !out.card.welcome ? subjectOf(out, E, d, ctx) : null;
    return out;
  }
  function routeIntent(nq, E, d, ctx, force) {
    var scored = INTENTS.map(function (it) {
      var s = 0;
      try { s = it.match(nq, E, d) || 0; } catch (err) { s = 0; }
      return { it: it, score: s, ok: safeWhen(it, d, ctx) };
    });
    // the best intent this report can answer; failing that, the best it cannot, for its reason
    var best = null;
    if (force) best = scored.filter(function (x) { return x.it.id === force; })[0] || null;
    if (!best) scored.forEach(function (x) { if (x.ok && x.score > 0 && (!best || x.score > best.score)) best = x; });
    if (!best) scored.forEach(function (x) { if (x.score > 0 && (!best || x.score > best.score)) best = x; });
    if (!best) return { intent: null, E: E, card: cannot(null, d, ctx, nearest(scored, d, ctx)) };
    if (!best.ok) {
      var why = "";
      try { why = best.it.absent ? best.it.absent(d, ctx) : ""; } catch (err) { why = ""; }
      return { intent: best.it.id, E: E, card: cannot(why, d, ctx, nearest(scored, d, ctx)) };
    }
    var card;
    try { card = best.it.answer(nq, E, d, ctx); } catch (err) { if (global.console) console.warn("AgentDiff chat: the " + best.it.id + " intent failed", err); card = cannot("that answer could not be built from this report", d, ctx, nearest(scored, d, ctx)); }
    if (!card.intent) card.intent = best.it.id;
    return { intent: card.intent, E: E, card: card };
  }
  function safeWhen(it, d, ctx) { try { return !!it.when(d, ctx); } catch (err) { return false; } }
  //: the three nearest answerable intents: by partial score, then the data's own chips
  function nearest(scored, d, ctx) {
    var out = scored.filter(function (x) { return x.ok && x.score > 0 && x.it.id !== "show"; }).sort(function (a, b) { return b.score - a.score; }).map(function (x) { return x.it.example; });
    defaultChips(d, ctx).forEach(function (c) { if (out.indexOf(c) < 0) out.push(c); });
    return out.slice(0, 3);
  }
  function cannot(why, d, ctx, near) {
    var speaker = d && d.cov ? evalSpeaker(d) : PAGE;
    var text = ["I can't answer that from this report" + (why ? ": " + why : "") + ". I can answer, for instance: " + (near || defaultChips(d, ctx).slice(0, 3)).map(function (c) { return "“" + c + "”"; }).join(", ") + "."];
    return { speaker: speaker, text: text, embed: null, select: null, sources: ["no field of the report answers this question"], cannot: true, followups: near || defaultChips(d, ctx).slice(0, 3) };
  }

  //: the chips the data offers before any question is asked
  function defaultChips(d, ctx) {
    var out = [];
    if (d.cov) {
      var rej = mostRejected(d);
      out.push("what did you learn?", rej ? "why did you reject " + rej + "?" : null, "which generation should I keep?", "what would you have caught earlier?", "do you trust yourself?", d.other ? "compare with " + d.other : null);
    } else if (d.ec) {
      out.push("who evolved better?", "compare with " + (d.other || "the other lineage"), "which generation should I keep?", d.steps.length ? "what changed at " + stepKey(worstStep(d)) + "?" : null);
    } else if (d.evo) {
      out.push("which generation should I keep?", d.steps.length ? "what changed at " + stepKey(worstStep(d)) + "?" : null, "show the lineage", "is the evolution sound?");
    } else if (d.stats) {
      out.push("which policy is better?", "show the learning curves", "is the reward sound?", "where did the time go?");
    } else {
      out.push("which agent is better?", "what happened?", "where did the time go?", "show the tools", "what should I change?");
    }
    if (d.cov && d.stats) out.push("show the timescape");
    if (d.bundle) out.unshift("what is running?");
    if (d.hasPairLevels || d.bundle || d.budgetAgg) out.push("where did the tokens go?", heaviest(d) ? "which run was the most expensive?" : null);
    if (d.hasPairData) out.push("what prompt was given?", "is the answer grounded?");
    if (d.dataEvo && d.steps.length) out.push("what data triggered " + stepKey(worstStep(d)) + "?");
    out.push("what can you answer?");
    return out.filter(Boolean);
  }

  //: the welcome turn: what the eval (or the page) can answer, in one paragraph
  function welcome(d, ctx) {
    var cat = catalogue();
    if (d.cov) {
      var c = d.cov, mu = c.integrity && c.integrity.multiplicity;
      var text = "I am the eval that watched " + d.family + " evolve" + (d.genIds.length ? ": " + plural(d.genIds.length, "generation") + ", " + plural(d.steps.length, "step") : "") + ". I grew from e0 (" + plural(Array.isArray(c.base) ? c.base.length : 0, "base metric") + ") to " + (d.evalGens.length ? d.evalGens[d.evalGens.length - 1].id + " (" + plural(count(d.evalGens[d.evalGens.length - 1].size), "active metric") + ")" : "nothing more") + (mu ? ", testing " + plural(count(mu.tested), "candidate") + " and adopting " + count(mu.adopted) : "") + ". Ask me what I learned and when, why I rejected a candidate, what I would have caught earlier, whether I trust myself, which generation to keep, what a metric says, which probes fired, or what I saw at a step" + (d.ec ? " — and how " + (d.other || "the other lineage") + " compares" + (d.evals ? ", eval against eval" : "") : "") + (d.dataEvo ? ", or what data triggered a step" : "") + ". Ask the page for any of its " + plural(cat.length, "block") + " by name (“show the timescape”, “what changed at " + stepKey(d.steps[0]) + "”)." + (c.synthetic ? " SYNTHETIC: the lineage's traces are a synthetic demo; every number is real arithmetic over synthetic episodes." : "");
      return { speaker: evalSpeaker(d), text: [text], embed: null, select: null, sources: ["aggregate.coevolution.family", "aggregate.coevolution.eval_generations[]", "aggregate.coevolution.integrity.multiplicity", "AgentDiff.catalogue()"], followups: defaultChips(d, ctx), welcome: true };
    }
    var what = d.ec ? "two self-evolving lineages, " + d.families.join(" and ") + ", over " + plural(count(d.ec.tasks && d.ec.tasks.shared && d.ec.tasks.shared.length), "shared task")
      : d.evo ? "the lineage of " + d.family + (d.genIds.length ? " (" + plural(d.genIds.length, "generation") + ")" : "")
      : d.stats ? "a training batch: " + d.policies.join(" vs ") + " over " + plural(d.tasks.length, "task")
      : d.agents.length === 2 ? d.agents[0] + " vs " + d.agents[1] + " over " + plural(d.tasks.length, "task") : "this report";
    var t = "This page reads " + what + ". " + (d.evo || d.ec ? "" : "There is no self-evolving eval in this report, so the page answers for itself. ") + "Ask it what happened, which " + (d.stats ? "policy" : "agent") + " is better, where the time and the tokens went, which tools were called" + (d.hasPairData ? ", what prompt was given, what each agent read and what its answer rests on" : "") + (d.bundle ? ", what is running in this bundle and which run was the heaviest" : "") + ", or to show any of its " + plural(cat.length, "block") + " by name." + (d.synthetic ? " SYNTHETIC: the traces are a synthetic demo." : "");
    return { speaker: PAGE, text: [t], embed: null, select: null, sources: ["aggregate.agents", "AgentDiff.catalogue()"], followups: defaultChips(d, ctx), welcome: true };
  }

  // --------------------------------------------------------------- state

  /* The transcript: the questions asked, in order, persisted under
   * "agentdiff:chat" through the library's page-scoped family. Answers are
   * recomputed from the loaded report on render; `source` fingerprints the
   * report so a transcript never carries over to a different one. */
  var FAMILY = null;
  function family() { return FAMILY || (FAMILY = L.family("chat", { turns: [], source: "" }, { scope: "page", persist: true })); }
  function fingerprint(d) { return [d.family, d.families.join("+"), d.agents.join("+"), d.policies.join("+"), d.tasks.length].join("|"); }
  function state(d) {
    var st = family().get();
    if (!Array.isArray(st.turns)) st.turns = [];
    var fp = fingerprint(d);
    if (st.source !== fp) { st.turns = []; st.source = fp; family().persist(); }
    if (st.turns.length > MAX_TURNS) { st.turns.splice(0, st.turns.length - MAX_TURNS); family().persist(); }
    return st;
  }
  function pushTurn(d, q) {
    var st = state(d);
    st.turns.push({ q: q });
    if (st.turns.length > MAX_TURNS) st.turns.splice(0, st.turns.length - MAX_TURNS);
    family().persist();
    return st;
  }
  function clearTurns() { family().reset(); LAST = null; }
  //: the last answer's subject, threaded through the transcript on render and advanced by every ask
  var LAST = null;
  function advance(prev, res, q) { return res && res.subject ? { intent: res.intent, q: res.resolved || q, subject: res.subject } : prev; }

  // -------------------------------------------------------------- render

  //: the shared-selection family a block listens to, if any
  function familyOfBlock(id) { return /^cov-/.test(id) ? "coevolution" : /^evo-/.test(id) ? "evolution" : /^evc-/.test(id) ? "evolution-compare" : /^lv-/.test(id) ? "levels" : /^dt-/.test(id) ? "data" : null; }
  function familyState(name) {
    var fams = { coevolution: AgentDiff.coevolution, evolution: AgentDiff.evolution, "evolution-compare": AgentDiff.evolutionCompare, levels: AgentDiff.levels, data: AgentDiff.data };
    var f = fams[name];
    try { return f && typeof f.state === "function" ? f.state() : null; } catch (err) { return null; }
  }
  //: whether the page's current selection is the one an answer asked for (so its embed can be drawn as it was)
  function selectionMatches(sel) {
    if (!sel || typeof sel !== "object" || !sel.value) return true;
    var st = familyState(sel.family);
    if (!st) return false;
    return Object.keys(sel.value).every(function (k) { var v = sel.value[k], w = st[k]; return (v === null || v === undefined) ? (w === null || w === undefined) : String(v) === String(w); });
  }
  function applySelection(sel) {
    if (!sel || typeof sel !== "object") return;
    var fams = { coevolution: AgentDiff.coevolution, evolution: AgentDiff.evolution, "evolution-compare": AgentDiff.evolutionCompare, levels: AgentDiff.levels, data: AgentDiff.data };
    var f = fams[sel.family];
    if (f && typeof f.select === "function") { try { f.select(sel.value); } catch (err) { /* a family that refuses keeps its state */ } }
  }

  function renderEmbed(H, id, live, sel) {
    var b = blockOf(id);
    var wrap = H("div", { class: "chat-embed", "data-embed": id, "data-family": familyOfBlock(id) || null });
    wrap._select = sel || null;
    wrap.appendChild(H("p", { class: "chat-embed-title", text: b ? b.title : id }));
    var host = H("div", { class: "chat-embed-host" });
    wrap.appendChild(host);
    function draw() {
      var ok = false;
      try { ok = AgentDiff.renderInto(id, host); } catch (err) { ok = false; }
      wrap.setAttribute("data-drawn", ok ? "true" : "false");
      if (!ok) { host.innerHTML = ""; host.appendChild(H("p", { class: "chat-status", text: "The block " + (b ? b.title : id) + " has nothing to draw for this report." })); }
    }
    wrap._draw = draw;
    if (live) draw(); else foldEmbed(H, wrap, false);
    return wrap;
  }
  /* An embed folded to a control: drawn on demand, at the answer's own
   * selection when it has one — so a card never shows a chart at a
   * selection a later answer moved. */
  function foldEmbed(H, wrap, again) {
    var id = wrap.getAttribute("data-embed"), b = blockOf(id), host = wrap.querySelector(".chat-embed-host");
    if (!host) return;
    host.innerHTML = "";
    wrap.setAttribute("data-drawn", "deferred");
    var btn = H("button", { type: "button", class: "chat-link", "data-role": "draw", text: "draw " + (b ? b.title : id) + (again ? " again" : "") + (wrap._select ? " at this answer's selection" : ""), onclick: function () {
      btn.remove();
      // this answer's selection moves the family: the other live embeds of that family fold
      var fam = wrap.getAttribute("data-family"), log = wrap.closest(".chat-log");
      if (wrap._select && fam && log) {
        var others = log.querySelectorAll('.chat-embed[data-family="' + fam + '"][data-drawn="true"]');
        for (var i = 0; i < others.length; i++) if (others[i] !== wrap) foldEmbed(H, others[i], true);
      }
      applySelection(wrap._select);
      wrap._draw();
    } });
    host.appendChild(btn);
  }

  function renderCard(H, card, d, ctx, opts) {
    opts = opts || {};
    var isEval = card.speaker !== PAGE;
    var art = H("article", { class: "chat-a" + (card.cannot ? " cannot" : ""), tabindex: "0", "data-intent": card.intent || (card.welcome ? "welcome" : card.cannot ? "cannot" : ""), "aria-label": (isEval ? "answer from " + card.speaker : "answer from the page") });
    // attached before the embed draws, so a chart measures a real width and paints inside the answer's timer
    if (opts.parent) opts.parent.appendChild(art);
    var chip = H("p", { class: "chat-speaker" + (isEval ? " eval" : "") }, [H("i", { "aria-hidden": "true" }), H("span", { text: card.speaker })]);
    if (card.synthetic && d.synthetic) chip.appendChild(H("span", { class: "syn", title: "the traces are a synthetic demo; every number is real arithmetic over synthetic episodes", text: "SYNTHETIC" }));
    art.appendChild(chip);
    (Array.isArray(card.text) ? card.text : [card.text]).forEach(function (t) { if (t) art.appendChild(H("p", { class: "chat-text", text: String(t) })); });
    if (Array.isArray(card.more) && card.more.length) art.appendChild(H("details", { class: "chat-more" }, [H("summary", { text: "the engine's full reading" })].concat(card.more.map(function (t) { return H("p", { class: "chat-text", text: String(t) }); }))));
    if (Array.isArray(card.groups)) card.groups.forEach(function (g) {
      var grp = H("div", { class: "chat-group" }, [H("h4", { text: g.title })]);
      var row = H("div", { class: "chat-chips", role: "group", "aria-label": "blocks in " + g.title });
      g.chips.forEach(function (c) { row.appendChild(H("button", { type: "button", class: "chat-chip", text: c.label, onclick: function () { opts.ask(c.q); } })); });
      grp.appendChild(row);
      art.appendChild(grp);
    });
    var b = card.embed ? blockOf(card.embed) : null;
    if (card.embed && b) art.appendChild(renderEmbed(H, card.embed, opts.live !== false, card.select));
    var foot = H("div", { class: "chat-foot" });
    if (b) foot.appendChild(H("button", { type: "button", class: "chat-link", "data-goto": b.id, text: "open in " + viewLabel(b.view), onclick: function () { try { AgentDiff.goTo(b.id, card.select || undefined); } catch (err) { /* the view is still there */ } } }));
    if (Array.isArray(card.sources) && card.sources.length) {
      foot.appendChild(H("details", { class: "chat-sources" }, [H("summary", { text: "sources" }), H("ul", {}, card.sources.map(function (s) { return H("li", { text: s }); }))]));
    }
    art.appendChild(foot);
    return art;
  }

  AgentDiff.block({
    id: "chat",
    title: "Ask the page",
    question: "Every block by asking — and, for a lineage, the self-evolving eval answering for itself about what it watched.",
    group: "chat",
    size: "full",
    relevance: function () { return 1; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, d = data(ctx);
      var st = state(d);
      if (!st.turns.length) { st.turns.push({ q: null }); family().persist(); }
      var root = H("div", { class: "chat" });
      el.appendChild(root);
      var log = H("ol", { class: "chat-log", role: "log", "aria-live": "polite", "aria-label": "the conversation" });
      root.appendChild(log);
      var chips = H("div", { class: "chat-chips", role: "group", "aria-label": "suggested questions" });
      var input = H("input", { type: "text", class: "chat-input", "aria-label": "Ask the page", placeholder: d.cov ? "Ask the eval, or the page…" : "Ask the page…", autocomplete: "off", spellcheck: "false" });
      var status = H("p", { class: "chat-status", role: "status", "aria-live": "polite" });
      var histAt = -1, draft = "";

      function history() { return st.turns.filter(function (t) { return t.q; }).map(function (t) { return t.q; }).slice(-HISTORY); }
      function setChips(list, evalFam) {
        chips.innerHTML = "";
        (list || []).filter(Boolean).forEach(function (c) {
          var q = typeof c === "string" ? c : c.q;
          chips.appendChild(H("button", { type: "button", class: "chat-chip" + (evalFam && d.cov && /you|your|yourself|compare|keep|probes/.test(q) ? " eval" : ""), "data-carried": c.carried ? "true" : null, text: q, onclick: function () { ask(q); } }));
        });
      }
      function chipsFor(res) {
        var card = res && res.card, f = [];
        // two follow-ups on the carried subject first, then the answer's own, then the data's
        carriedChips(res && res.subject, d, ctx).forEach(function (c) { f.push({ q: c, carried: true }); });
        (card && Array.isArray(card.followups) ? card.followups.filter(Boolean) : []).forEach(function (c) { if (!f.some(function (x) { return x.q === c; })) f.push({ q: c }); });
        var own = f.filter(function (x) { return !x.carried; }).length;
        if (own < 3) defaultChips(d, ctx).forEach(function (c) { if (own < 5 && !f.some(function (x) { return x.q === c; })) { f.push({ q: c }); own++; } });
        if (!f.some(function (x) { return x.q === "what can you answer?"; })) f.push({ q: "what can you answer?" });
        return f;
      }
      //: the typed question, and under it the question it resolved into when it leaned on the last answer
      function question(row, q, resolved) {
        row.appendChild(H("p", { class: "chat-q", text: q, "aria-label": "you asked: " + q }));
        if (resolved) row.appendChild(H("p", { class: "chat-q-resolved", text: "↳ " + resolved, "aria-label": "read as: " + resolved }));
      }
      function ask(q) {
        q = String(q || "").trim();
        if (!q) return;
        var t0 = global.performance && performance.now ? performance.now() : Date.now();
        pushTurn(d, q);
        var i = st.turns.length - 1;
        // the answer, then its selection (the page is connected now, so the
        // families' subscribers hear it), then the embed drawn on the new state
        var res = route(q, d, ctx, LAST);
        LAST = advance(LAST, res, q);
        applySelection(res.card.select);
        var row = H("li", { class: "chat-turn new", "data-turn": String(i), "data-resolved": res.resolved || null });
        question(row, q, res.resolved);
        // the older answers' embeds beyond the live window fold to a control, and so
        // does every older embed of the family whose selection this answer moves
        var lives = log.querySelectorAll('.chat-embed[data-drawn="true"]');
        var fam = res.card.select && res.card.embed ? familyOfBlock(res.card.embed) : null;
        for (var k = 0; k < lives.length; k++) {
          if (k + LIVE_EMBEDS <= lives.length || (fam && lives[k].getAttribute("data-family") === fam)) foldEmbed(H, lives[k], true);
        }
        log.appendChild(row);
        renderCard(H, res.card, d, ctx, { ask: ask, live: true, parent: row });
        var ms = (global.performance && performance.now ? performance.now() : Date.now()) - t0;
        row.setAttribute("data-answer-ms", ms.toFixed(1));
        row.setAttribute("data-intent", res.intent || "cannot");
        setChips(chipsFor(res), true);
        status.textContent = (res.card.cannot ? "No answer for that; " : "Answered by " + res.card.speaker + "; ") + (res.resolved ? "read as “" + res.resolved + "”; " : "") + "turn " + (i + 1) + " of " + st.turns.length + (st.turns.length >= MAX_TURNS ? " (the transcript keeps the latest " + MAX_TURNS + ")" : "") + ".";
        input.value = ""; draft = ""; histAt = -1;
        try { row.scrollIntoView({ block: "nearest", behavior: prefersReduced() ? "auto" : "smooth" }); } catch (err) { /* fine */ }
        ctx.signal("inspect");
      }

      // the transcript so far: answers recomputed from the report; the latest
      // LIVE_EMBEDS embeds drawn live, except a selection-bound one whose
      // selection the page no longer holds (it folds to "draw at this
      // answer's selection"), and only the latest per family stays live
      var prev = null;
      var results = st.turns.map(function (turn) {
        if (turn.q === null || turn.q === undefined) return { card: welcome(d, ctx), resolved: null, subject: null };
        var r = route(turn.q, d, ctx, prev);
        prev = advance(prev, r, turn.q);
        return r;
      });
      LAST = prev;
      var cards = results.map(function (r) { return r.card; });
      var liveSet = {}, seenFam = {}, n = 0;
      for (var i = cards.length - 1; i >= 0 && n < LIVE_EMBEDS; i--) {
        var c = cards[i];
        if (!c.embed) continue;
        n++;
        var fam = familyOfBlock(c.embed);
        if (fam && c.select) { if (seenFam[fam] || !selectionMatches(c.select)) continue; seenFam[fam] = true; }
        liveSet[i] = true;
      }
      st.turns.forEach(function (turn, i) {
        var row = H("li", { class: "chat-turn", "data-turn": String(i), "data-resolved": results[i].resolved || null });
        if (turn.q) question(row, turn.q, results[i].resolved);
        renderCard(H, cards[i], d, ctx, { ask: ask, live: !!liveSet[i], parent: row });
        log.appendChild(row);
      });
      setChips(chipsFor(results[results.length - 1]), true);

      var composer = H("div", { class: "chat-composer" });
      composer.appendChild(chips);
      var form = H("form", { class: "chat-form", onsubmit: function (evt) { evt.preventDefault(); ask(input.value); } }, [
        input,
        H("button", { type: "submit", class: "chat-send", "aria-label": "send the question", text: "Ask" }),
      ]);
      input.addEventListener("keydown", function (evt) {
        var hist = history();
        if (evt.key === "ArrowUp" && hist.length) {
          evt.preventDefault();
          if (histAt === -1) { draft = input.value; histAt = hist.length - 1; } else if (histAt > 0) histAt--;
          input.value = hist[histAt];
        } else if (evt.key === "ArrowDown" && histAt !== -1) {
          evt.preventDefault();
          if (histAt < hist.length - 1) { histAt++; input.value = hist[histAt]; } else { histAt = -1; input.value = draft; }
        } else if (evt.key === "Escape") {
          if (input.value) { evt.preventDefault(); evt.stopPropagation(); input.value = ""; histAt = -1; draft = ""; }
        }
      });
      composer.appendChild(form);
      composer.appendChild(H("div", { class: "chat-foot" }, [
        H("span", { class: "chat-hint", text: "Enter asks · ↑ ↓ recall · Esc clears the line · every number is the report's; nothing here calls a model." }),
        H("button", { type: "button", class: "chat-clear", "data-role": "clear", "aria-label": "clear the transcript", text: "clear the transcript", onclick: function () { clearTurns(); cache = null; if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); } }),
      ]));
      composer.appendChild(status);
      root.appendChild(composer);
    },
  });

  // a small surface for the tests and for a block that wants to ask
  AgentDiff.chat = {
    ask: function (q) { var input = document.querySelector(".chat-input"); var form = input && input.closest("form"); if (!input || !form) return false; input.value = q; form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true })); return true; },
    //: route a question on its own, or against a subject (`prev`: {intent, q, subject}, as `last()` returns)
    route: function (q, prev) { var ctx = { aggregate: AgentDiff.state().data.aggregate || {}, report: null, task: AgentDiff.state().task }; try { ctx.report = (AgentDiff.state().data.reports || []).filter(function (r) { return r && r.task && r.task.id === ctx.task; })[0] || (AgentDiff.state().data.reports || [])[0] || null; } catch (err) { ctx.report = null; } var r = route(q, data(ctx), ctx, prev || null); return { intent: r.intent, card: r.card, resolved: r.resolved, subject: r.subject }; },
    //: the last answer's subject, as the next question will resolve against it
    last: function () { return LAST ? { intent: LAST.intent, q: LAST.q, subject: LAST.subject } : null; },
    intents: function () { return INTENTS.map(function (i) { return { id: i.id, family: i.family, example: i.example }; }); },
    turns: function () { return (family().get().turns || []).map(function (t) { return t.q; }); },
    clear: clearTurns,
  };
})(typeof window !== "undefined" ? window : this);
