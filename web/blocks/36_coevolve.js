/* AgentDiff blocks — the Evals view: the self-evolving eval that watches a
 * self-evolving agent, drawn as the loop it is.
 *
 * `aggregate.coevolution` (deepcompare/coevolve.py) is an eval that is
 * itself a lineage e0 → e1 → …: each agent step that exposes a blind spot
 * triggers probes (the eval's agents, each one question), the probes
 * propose candidate metrics, five validators (the sub-agents, one criterion
 * each) test every candidate on the evidence so far, the survivors are
 * adopted into a new eval generation, and at the end the final eval re-reads
 * every step of the lineage with hindsight. Every point, bound, count and
 * sentence here is read from the JSON; nothing is recomputed:
 *
 *   cov-flow       the loop in one picture, at three levels. Loop level:
 *                  time left to right by agent step; the agent's generations
 *                  on top (verdict colour on the step's edge, the base flags
 *                  and the hindsight flags as glyphs), the probes as rows
 *                  with a mark where each fired, the step's candidates
 *                  flowing through the five validators as a Sankey-like
 *                  ribbon (width ∝ candidates; the ones a validator stops
 *                  leave downward in the bad colour with their count; the
 *                  adopted reach the eval lane), the eval's generations
 *                  under the step that made them bearing their metrics as
 *                  chips; and back up the picture the hindsight edges
 *                  (dashed, a metric to the steps it would have flagged,
 *                  the lag on the edge) and the recovery edges (dotted,
 *                  "recovered, not attributed"). Step level: that step's
 *                  probes, its candidates as rows with five validator
 *                  marks, K and α, the decision and the reason. Candidate
 *                  level: the spec and every validator's numbers.
 *   cov-hindsight  every agent step under the base eval beside the evolved
 *                  reading, and how many steps late each learned metric came.
 *   cov-matrix     metrics × generations, standardised per row, the cells
 *                  before a metric's adoption hatched (computed with hindsight).
 *   cov-metric     one metric in full: its spec, its curve with intervals,
 *                  its per-task small multiples (one panel per task from the
 *                  matrix cells' `per_task`, the forgotten task marked at the
 *                  step the Evolution section names), its validation row,
 *                  its confirmation, its origin.
 *   cov-probes     the probes as agents, and the external proposer's status.
 *   cov-integrity  the eval's own drift, multiplicity, retirements, the gap.
 *
 * The selection (a step, an eval generation, a metric, a candidate) is one
 * page-scoped family, `coevolution`; a change re-paints the mounted blocks
 * in place. Formatting, the tooltip, the interval glyph and the stylesheet
 * come from `AgentDiff.lib`; the verdict table is the Evolution blocks' so
 * the two views agree.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;
  var L = AgentDiff.lib;
  var isNum = L.fmt.isNum, num = L.fmt.num, pct = L.fmt.pct, short = L.fmt.short;
  var responsive = L.layout.responsive;

  //: the transition when the level or the selection changes; none on first paint, none under reduced motion
  var DUR = 240;
  //: the five validators, in the order the first failure decides
  var VALIDATORS = ["computable", "informative", "distinct", "linked", "not_already"];
  var VSHORT = { computable: "computable", informative: "informative", distinct: "distinct", linked: "linked", not_already: "not already" };
  //: the loop level never draws a column narrower than this; past it the quiet steps fold, then the earliest ones
  var MIN_COL = 40;

  var CSS = [
    ".cov{position:relative}",
    ".cov svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
    ".cov text{font-size:var(--fs-xs)}",
    ".cov .lab{fill:var(--ink-2)}.cov .lab.dim{fill:var(--ink-3)}.cov .lab.mono{font-family:var(--mono)}.cov .lab.strong{fill:var(--ink);font-weight:600}",
    ".cov .tick{fill:var(--ink-3);font-variant-numeric:tabular-nums;font-family:var(--mono)}",
    ".cov .rule{stroke:var(--rule)}.cov .zero{stroke:var(--rule-2)}",
    ".cov .backed{paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
    ".cov-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 6px;max-width:96ch;line-height:1.45}.cov-lede b{font-weight:600}",
    ".cov-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
    ".cov-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
    ".cov-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;max-width:100%;overflow-wrap:anywhere}.cov-chip b{color:var(--ink);font-weight:600}",
    ".cov-chip.syn{color:var(--warn);font-weight:600;letter-spacing:.04em}",
    ".cov-btn{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer}",
    ".cov-btn:hover{color:var(--ink)}.cov-btn[aria-pressed=true]{background:var(--ink);color:var(--bg)}.cov-btn:disabled{opacity:.4;cursor:default}",
    ".cov-btn:focus-visible,.cov-crumbs button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".cov-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:96ch;line-height:1.5}",
    ".cov-read{font-size:var(--fs-xs);color:var(--ink-2);margin:6px 0 0;line-height:1.5;max-width:80ch}.cov-read b{color:var(--ink);font-weight:600}",
    ".cov-h{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);margin:10px 0 3px;letter-spacing:.04em;text-transform:uppercase}",
    ".cov-v{font-weight:600}.cov-v .g{font-weight:700;margin-right:3px}",
    ".cov-status{font-size:var(--fs-xs);color:var(--ink-2);margin:0 0 6px;line-height:1.5;max-width:110ch}",
    ".cov-crumbs{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;min-width:0}",
    ".cov-crumbs button{font:inherit;font-size:var(--fs-xs);font-family:var(--mono);border:0;background:transparent;color:var(--ink-2);padding:1px 4px;border-radius:4px;cursor:pointer;max-width:30ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".cov-crumbs button[aria-current=true]{color:var(--ink);font-weight:600;cursor:default}",
    ".cov-crumbs button:hover:not([aria-current=true]){background:var(--surface-2);color:var(--ink)}.cov-crumbs .sep{color:var(--ink-3)}",
    ".cov-stage{outline:none;border-radius:8px}.cov-stage:focus-visible{box-shadow:0 0 0 2px var(--accent)}",
    ".cov-hit{cursor:pointer;outline:none}.cov-hit:focus-visible .ring{stroke:var(--accent);stroke-opacity:1}",
    ".cov-fold{cursor:pointer}",
    // the flag glyphs: ▲ learned in the accent, base in ink-3
    ".cov-flag{font-family:var(--mono);font-size:var(--fs-xs);white-space:normal;overflow-wrap:anywhere;margin-right:8px}.cov-flag.learned{color:var(--accent)}.cov-flag.base{color:var(--ink-3)}.cov-flag.none{color:var(--ink-3);font-style:italic}",
    // rows (candidates, steps)
    ".cov-rows{margin-top:4px}",
    ".cov-row{display:grid;gap:2px 10px;align-items:center;padding:4px 4px;border-radius:5px;font-size:var(--fs-xs);color:var(--ink-2);outline:none}",
    ".cov-row[tabindex]{cursor:pointer}.cov-row[tabindex]:hover,.cov-row:focus-visible{background:var(--surface-2)}",
    ".cov-row[aria-current=true]{background:var(--surface-2);box-shadow:inset 3px 0 0 var(--ink)}",
    ".cov-row .id,.cov-row .n{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}.cov-row .id{overflow:hidden;text-overflow:ellipsis}.cov-row .n{text-align:right}",
    ".cov-row .spec{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".cov-row .why{grid-column:1 / -1;color:var(--ink-3);white-space:normal;line-height:1.4}",
    ".cov-head{cursor:default;color:var(--ink-3);font-family:var(--mono)}.cov-head:hover{background:none}",
    ".cov-cand{grid-template-columns:150px minmax(120px,2fr) 100px 30px 52px 70px minmax(90px,1fr)}",
    ".cov-hs{grid-template-columns:64px 170px minmax(120px,1fr) minmax(120px,1.4fr)}",
    "@media (max-width:820px){.cov-cand{grid-template-columns:minmax(90px,1fr) 96px 66px}.cov-cand .spec,.cov-cand .k,.cov-cand .alpha,.cov-cand .became{display:none}.cov-hs{grid-template-columns:64px 80px minmax(100px,1fr)}.cov-hs .base-flags{display:none}}",
    ".cov-marks{display:inline-flex;gap:3px;font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ".cov-mark{display:inline-block;width:16px;text-align:center;border-radius:3px;font-weight:700}",
    ".cov-mark.pass{color:var(--good)}.cov-mark.fail{color:var(--bad)}.cov-mark.exempt{color:var(--ink-3)}.cov-mark.nt{color:var(--ink-3)}.cov-mark.decisive{background:var(--bad);color:var(--bg)}",
    ".cov-dec{font-weight:600}.cov-dec.adopted{color:var(--good)}.cov-dec.rejected{color:var(--bad)}.cov-dec.retired,.cov-dec.demoted{color:var(--warn)}",
    // tables
    ".cov-table{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:4px}",
    ".cov-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
    ".cov-table td{padding:3px 10px 3px 0;color:var(--ink-2);white-space:nowrap;vertical-align:top}.cov-table td.num{text-align:right;font-family:var(--mono)}.cov-table td.wrap{white-space:normal;min-width:16em}",
    ".cov-table td.bad{color:var(--bad)}.cov-table td.good{color:var(--good)}",
    ".cov-details{margin-top:8px}.cov-details summary{cursor:pointer;color:var(--ink-2);font-size:var(--fs-xs)}",
    ".cov-cols{display:flex;gap:14px 24px;flex-wrap:wrap;align-items:flex-start}.cov-cols>*{flex:1 1 280px;min-width:0}",
    ".cov-list{list-style:none;margin:0;padding:0;font-size:var(--fs-xs);color:var(--ink-2);line-height:1.5}.cov-list li{overflow-wrap:anywhere}",
    ".cov-list .mono{font-family:var(--mono)}",
    ".cov-big{font-size:var(--fs-l);color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600}",
    ".cov-ci{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);font-variant-numeric:tabular-nums}",
    ".cov-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
    ".cov-tip b{color:var(--ink)}.cov-tip .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ".cov-multi{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px 18px}",
    ".cov-hatch{fill:url(#cov-hatch)}",
    ".cov-multi svg{width:100%}.cov-task .forgot{fill:var(--warn);font-weight:700}.cov-task .absent{fill:var(--ink-3)}",
  ].join("\n");
  function ensureStyle() { L.style.once("coevolve", CSS); }

  // ------------------------------------------------------------- helpers

  /* Not the library's `signed`: printed through `num`, so a delta drops its
   * trailing zeros (+0.5, −1), the strings the Evolution ledger prints. */
  function signed(v, p) {
    if (!isNum(v)) return "—";
    var s = num(Math.abs(v), p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  }
  var trunc = L.fmt.trunc, plural = L.fmt.plural;
  function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function width(host) { return L.layout.measure(host, 300, 1400); }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); }
    catch (err) { return false; }
  }
  function resolve(el, name, fallback) {
    try { var v = getComputedStyle(el).getPropertyValue(name); return v && v.trim() ? v.trim() : fallback; }
    catch (err) { return fallback; }
  }
  function tooltip(root) { return L.svg.tip(root, { class: "cov-tip", width: 340 }); }
  function ci(v) { return v && isNum(v.point) ? num(v.point) + (isNum(v.lo) ? " [" + num(v.lo) + ", " + num(v.hi) + "]" : "") : "—"; }
  function dci(v) { return v && isNum(v.point) ? signed(v.point) + (isNum(v.lo) ? " [" + signed(v.lo) + ", " + signed(v.hi) + "]" : "") : "—"; }
  //: the string of the level's alpha: 0.0083 → "0.0083", 0.008333 → "0.0083"
  function alpha(a) { return isNum(a) ? num(a, 4) : "—"; }

  /* The verdict vocabulary of the Evolution blocks, so the two views agree:
   * colour is polarity, the glyph is the kind — ✕ gamed, ▏forgot, ◎ overfit,
   * ⇄ traded. A verdict the engine does not name draws grey. */
  var VERDICT = {
    improved: { color: "var(--good)", glyph: "", word: "improved" },
    regressed: { color: "var(--bad)", glyph: "", word: "regressed" },
    flat: { color: "var(--ink-3)", glyph: "", word: "flat" },
    gamed: { color: "var(--bad)", glyph: "✕", word: "gamed" },
    forgot: { color: "var(--warn)", glyph: "▏", word: "forgot" },
    overfit: { color: "var(--warn)", glyph: "◎", word: "overfit" },
    traded: { color: "var(--warn)", glyph: "⇄", word: "traded" },
  };
  function verdictOf(v) { return VERDICT[v] || { color: "var(--ink-3)", glyph: "", word: v ? String(v) : "unmeasured" }; }
  function verdictLabel(v) { return (v.glyph ? v.glyph + " " : "") + v.word; }
  function verdictSpan(H, verdict) {
    var v = verdictOf(verdict);
    return H("span", { class: "cov-v", "data-verdict": verdict || "", style: { color: v.color } }, [v.glyph ? H("span", { class: "g", text: v.glyph }) : null, H("span", { text: v.word })]);
  }
  //: the colour of a metric by its status: learned in the accent, base in ink, retired and demoted muted
  function statusColor(st) { return st === "adopted" ? "var(--accent)" : st === "base" ? "var(--ink-2)" : "var(--ink-3)"; }

  /* A spec as a sentence, from the metric language: the aggregation word,
   * the feature, the filter, the direction. The engine's `name` is kept as
   * the metric's title; this is the reading of its spec. */
  var AGG_WORDS = { mean: "the mean of", rate: "the rate of", iqm: "the task-balanced IQM of", task_mean: "the per-task mean of", task_min: "the worst task's mean of", task_spread: "the spread of the per-task means of" };
  function whereText(w) {
    if (!w) return "";
    if (Array.isArray(w.all)) return w.all.map(whereText).join(" and ");
    return String(w.feature) + " " + String(w.op) + " " + String(w.value);
  }
  function specSentence(spec) {
    if (!spec) return "—";
    var dir = spec.direction === "up" ? "higher is better" : spec.direction === "down" ? "lower is better" : "no direction is better";
    return (AGG_WORDS[spec.agg] || String(spec.agg || "?")) + " " + String(spec.feature || "?") + (spec.where ? " over the episodes where " + whereText(spec.where) : " over every episode") + "; " + dir;
  }

  // --------------------------------------------------------------- model

  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {};
    if (cache && cache.agg === agg) return cache.m;
    var m;
    try { m = build(agg); } catch (err) { console.warn("AgentDiff coevolution: model failed", err); m = { ok: false, reason: "the coevolution section could not be read" }; }
    cache = { agg: agg, m: m };
    return m;
  }

  function build(agg) {
    var c = agg && agg.coevolution && typeof agg.coevolution === "object" ? agg.coevolution : null;
    if (!c) return { ok: false, reason: null };
    if (c.measurable === false) return { ok: false, reason: c.reason || "the eval could not be measured" };
    var flow = c.flow && typeof c.flow === "object" ? c.flow : { nodes: [], edges: [] };
    var nodes = Array.isArray(flow.nodes) ? flow.nodes.filter(function (n) { return n && n.id && n.kind; }) : [];
    var edges = Array.isArray(flow.edges) ? flow.edges.filter(function (e) { return e && e.from && e.to && e.kind; }) : [];
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var byKind = {};
    nodes.forEach(function (n) { (byKind[n.kind] = byKind[n.kind] || []).push(n); });
    var edgesByKind = {};
    edges.forEach(function (e) { (edgesByKind[e.kind] = edgesByKind[e.kind] || []).push(e); });
    // the agent's generations and steps, in lineage order
    var gens = (byKind.agent_gen || []).map(function (n, i) { return { id: String(n.gen || n.label), i: i, node: n }; });
    var genIndex = {};
    gens.forEach(function (g) { genIndex[g.id] = g.i; });
    var rawSteps = Array.isArray(c.steps) ? c.steps.filter(function (s) { return s && s.from && s.to; }) : [];
    if (!gens.length && rawSteps.length) {
      var ids = [];
      rawSteps.forEach(function (s) { if (ids.indexOf(s.from) < 0) ids.push(s.from); if (ids.indexOf(s.to) < 0) ids.push(s.to); });
      gens = ids.map(function (id, i) { return { id: id, i: i, node: null }; });
      gens.forEach(function (g) { genIndex[g.id] = g.i; });
    }
    if (!gens.length) return { ok: false, reason: "the eval names no generation" };
    var ledger = (Array.isArray(c.ledger) ? c.ledger : []).filter(function (r) { return r && r.step; });
    ledger.forEach(function (r, i) { if (!isNum(r.index)) r.index = i; });
    var ledgerByStep = {};
    ledger.forEach(function (r) { (ledgerByStep[r.step] = ledgerByStep[r.step] || []).push(r); });
    var probes = (Array.isArray(c.probes) ? c.probes : []).filter(function (p) { return p && p.name; });
    var probeByName = {};
    probes.forEach(function (p) { probeByName[p.name] = p; });
    var evalGens = (Array.isArray(c.eval_generations) ? c.eval_generations : []).filter(function (e) { return e && e.id; });
    var evalByStep = {};
    evalGens.forEach(function (e) { if (e.after_step) evalByStep[e.after_step] = e; });
    var metrics = c.metrics && typeof c.metrics === "object" ? c.metrics : {};
    var steps = rawSteps.map(function (s, i) {
      var key = String(s.from) + "→" + String(s.to);
      var ev = s.evolved && typeof s.evolved === "object" ? s.evolved : {};
      var base = s.base && typeof s.base === "object" ? s.base : {};
      var rows = ledgerByStep[key] || [];
      var fired = probes.filter(function (p) { return Array.isArray(p.fired) && p.fired.indexOf(key) >= 0; }).map(function (p) { return p.name; });
      var counts = funnel(rows);
      return {
        raw: s, key: key, from: String(s.from), to: String(s.to), i: i, index: isNum(s.index) ? s.index : i + 1,
        verdict: base.verdict || null, baseFlags: Array.isArray(base.flags) ? base.flags.map(String) : [],
        learned: Array.isArray(ev.flags) ? ev.flags : [], baseMetricFlags: Array.isArray(ev.base_flags) ? ev.base_flags : [],
        moved: Array.isArray(ev.moved) ? ev.moved : [], changed: ev.changed === true, reading: ev.reading || "",
        rows: rows, k: rows.length, adopted: rows.filter(function (r) { return r.decision === "adopted"; }).length,
        fired: fired, evalGen: evalByStep[key] || null, funnel: counts,
        quiet: !rows.length && !fired.length && !(Array.isArray(ev.flags) && ev.flags.length) && !(Array.isArray(ev.base_flags) && ev.base_flags.length),
      };
    }).sort(function (p, q) { return p.index - q.index; });
    var stepByKey = {};
    steps.forEach(function (s) { stepByKey[s.key] = s; });
    // the metrics in reading order: base first, then in order of adoption
    var order = [];
    (Array.isArray(c.base) ? c.base : []).forEach(function (id) { if (metrics[id] && order.indexOf(id) < 0) order.push(id); });
    evalGens.forEach(function (e) { (Array.isArray(e.adopted) ? e.adopted : []).forEach(function (id) { if (metrics[id] && order.indexOf(id) < 0) order.push(id); }); });
    Object.keys(metrics).forEach(function (id) { if (order.indexOf(id) < 0) order.push(id); });
    var metricList = order.map(function (id) { var mt = metrics[id] || {}; return { id: id, spec: mt.spec || { id: id, name: id }, status: mt.status || "adopted", raw: mt, name: mt.spec && mt.spec.name ? String(mt.spec.name) : id }; });
    var metricById = {};
    metricList.forEach(function (mt) { metricById[mt.id] = mt; });
    var hind = c.hindsight && typeof c.hindsight === "object" ? c.hindsight : {};
    var probesFired = probes.filter(function (p) { return Array.isArray(p.fired) && p.fired.length; });
    // the tasks the Evolution section says a step forgot: {task: [{key, to}]}, read from evolution.steps[].effect.forgotten
    var forgot = {};
    var evSteps = agg && agg.evolution && Array.isArray(agg.evolution.steps) ? agg.evolution.steps : [];
    evSteps.forEach(function (s) {
      if (!s || !s.from || !s.to) return;
      var key = String(s.from) + "→" + String(s.to), list = s.effect && Array.isArray(s.effect.forgotten) ? s.effect.forgotten : [];
      list.forEach(function (t) { (forgot[String(t)] = forgot[String(t)] || []).push({ key: key, to: String(s.to) }); });
    });
    return {
      forgotten: forgot,
      ok: true, c: c, flow: flow, nodes: nodes, edges: edges, byId: byId, byKind: byKind, edgesByKind: edgesByKind,
      gens: gens, genIndex: genIndex, steps: steps, stepByKey: stepByKey, ledger: ledger, ledgerByStep: ledgerByStep,
      probes: probes, probesFired: probesFired, probeByName: probeByName, evalGens: evalGens, evalByStep: evalByStep,
      metrics: metrics, metricList: metricList, metricById: metricById, matrix: c.matrix && typeof c.matrix === "object" ? c.matrix : {},
      hindsight: hind, integrity: c.integrity && typeof c.integrity === "object" ? c.integrity : {},
      recommended: c.recommended && typeof c.recommended === "object" ? c.recommended : null,
      thresholds: c.thresholds && typeof c.thresholds === "object" ? c.thresholds : {},
      summary: flow.summary && typeof flow.summary === "object" ? flow.summary : {},
      family: c.family || "", synthetic: c.synthetic === true, narrative: typeof c.narrative === "string" ? c.narrative : "",
      samples: isNum(c.samples) ? c.samples : null, base: Array.isArray(c.base) ? c.base : [],
      maxK: steps.reduce(function (a, s) { return Math.max(a, s.k); }, 0),
    };
  }

  /* The funnel of one step's candidates: how many reached each validator,
   * how many it stopped (the first failure decides, so a candidate leaves at
   * its first failed validator), how many survived all five. */
  function funnel(rows) {
    var reach = [], stop = [];
    var alive = rows.length;
    VALIDATORS.forEach(function (v, j) {
      var stopped = rows.filter(function (r) { var f = Array.isArray(r.failed) ? r.failed : []; return firstFailed(f) === v; }).length;
      reach.push(alive); stop.push(stopped); alive -= stopped;
    });
    return { reach: reach, stop: stop, out: alive, n: rows.length };
  }
  function firstFailed(failed) {
    var best = null, bi = Infinity;
    failed.forEach(function (f) { var i = VALIDATORS.indexOf(String(f)); if (i >= 0 && i < bi) { bi = i; best = VALIDATORS[i]; } });
    return best;
  }
  //: a validator's mark on a candidate row: pass, fail (decisive or not), exempt, or not tested
  function markOf(row, v) {
    var r = row.validators && row.validators[v];
    if (!r) return { cls: "nt", glyph: "·", word: "not recorded" };
    var f = Array.isArray(row.failed) ? row.failed : [];
    if (r.pass === false) return { cls: firstFailed(f) === v ? "fail decisive" : "fail", glyph: "✕", word: firstFailed(f) === v ? "failed — the deciding one" : "failed" };
    if (r.exempt === true) return { cls: "exempt", glyph: "◇", word: "exempt" };
    if (r.pass === true && /not testable/.test(String(r.note || ""))) return { cls: "nt", glyph: "○", word: "not testable yet" };
    if (r.pass === true) return { cls: "pass", glyph: "✓", word: "passed" };
    return { cls: "nt", glyph: "·", word: "not tested" };
  }

  // --------------------------------------------------------------- family

  /* The selection: the agent step in view (its key, "g2→g3"), the eval
   * generation, the metric, and the candidate (a ledger index). The level
   * of the flow is read from it: a candidate is the candidate level, a step
   * or an eval generation the step level, nothing the loop level. Page
   * scoped, persisted, restored on load; a stored value this lineage cannot
   * honour is dropped by `loadState`. */
  var FAMILY = null;
  function family() { return FAMILY || (FAMILY = L.family("coevolution", { step: null, evalGen: null, metric: null, candidate: null }, { scope: "page", persist: true })); }
  function state() { return family().get(); }
  function loadState(m) {
    var st = state(), changed = false;
    if (st.step !== null && !m.stepByKey[st.step]) { st.step = null; changed = true; }
    if (st.evalGen !== null && !m.evalGens.some(function (e) { return e.id === st.evalGen; })) { st.evalGen = null; changed = true; }
    if (st.candidate !== null && !(isNum(st.candidate) && m.ledger.some(function (r) { return r.index === st.candidate; }))) { st.candidate = null; changed = true; }
    if (st.candidate !== null) { var r = candidateRow(m, st.candidate); if (r && st.step !== r.step) { st.step = r.step; changed = true; } }
    if (st.metric !== null && !m.metricById[st.metric]) { st.metric = null; changed = true; }
    if (changed) family().persist();
    return st;
  }
  function select(patch) { family().set(patch); }
  function candidateRow(m, index) { return m.ledger.filter(function (r) { return r.index === index; })[0] || null; }
  function levelOf(st) { return st.candidate !== null ? 2 : st.step !== null || st.evalGen !== null ? 1 : 0; }
  var LEVEL_NAME = ["loop", "step", "candidate"];
  //: the metric in view: the family's, else the first adopted, else pass_rate, else the first
  function currentMetric(m) {
    var st = state();
    if (st.metric && m.metricById[st.metric]) return m.metricById[st.metric];
    var first = m.metricList.filter(function (mt) { return mt.status === "adopted"; })[0] || m.metricById.pass_rate || m.metricList[0] || null;
    return first;
  }
  //: what the step of a step-level selection is: the family's step, or the eval generation's trigger step
  function currentStep(m) {
    var st = state();
    if (st.step && m.stepByKey[st.step]) return m.stepByKey[st.step];
    if (st.evalGen) { var e = m.evalGens.filter(function (x) { return x.id === st.evalGen; })[0]; if (e && e.after_step && m.stepByKey[e.after_step]) return m.stepByKey[e.after_step]; }
    return null;
  }
  function listen(el, fn) { family().subscribe(function (st) { try { fn(st); } catch (err) { console.warn("AgentDiff coevolution: repaint failed", err); } }, el); }

  // a small surface for the tests and the sibling blocks
  AgentDiff.coevolution = {
    select: select,
    state: function () { var st = state(); return { step: st.step, evalGen: st.evalGen, metric: st.metric, candidate: st.candidate, level: LEVEL_NAME[levelOf(st)] }; },
    reset: function () { family().reset(); },
  };

  function stepLines(m, s) {
    return [
      { b: true, text: s.key + " · " + verdictLabel(verdictOf(s.verdict)) + (s.baseFlags.length ? " · flags: " + s.baseFlags.join(", ") : "") },
      s.fired.length ? { text: "probes fired: " + s.fired.join(", ") + " · " + plural(s.k, "candidate") + ", " + s.adopted + " adopted" } : { text: "no probe fired" },
      s.learned.length ? { text: "learned flags: " + s.learned.map(function (f) { return metricName(m, f.metric) + " " + dci(f.delta); }).join("; ") } : null,
      s.baseMetricFlags.length ? { text: "base metrics against their direction: " + s.baseMetricFlags.map(function (f) { return metricName(m, f.metric) + " " + dci(f.delta); }).join("; ") } : null,
      s.evalGen ? { text: "made " + s.evalGen.id + " (" + s.evalGen.trigger_probe + "): adopted " + (s.evalGen.adopted || []).join(", ") + (s.evalGen.retired && s.evalGen.retired.length ? "; retired " + s.evalGen.retired.join(", ") : "") + (s.evalGen.demoted && s.evalGen.demoted.length ? "; demoted " + s.evalGen.demoted.join(", ") : "") } : null,
      { text: "click to open this step" },
    ];
  }
  function metricName(m, id) { var mt = m.metricById[id]; return mt ? mt.name : String(id); }

  // ================================================================ cov-flow
  //
  // The loop level. Time runs left to right by agent step, one column per
  // step; the root generation has a narrow column of its own so the base
  // eval e0 has a place. Past what the width can hold at MIN_COL per column,
  // the quiet steps (no probe, no flag, no candidate) fold into a dotted
  // segment of the library's fold width, then the earliest steps fold into
  // one "earlier" segment; a fold dilates on click. The lanes, top to
  // bottom: the agent, the hindsight marks, one row per probe that fired,
  // the validators, the eval. DOM is bound per step, per probe mark, per
  // validator band, per eval generation, per metric chip and per hindsight
  // edge — never per candidate; the candidates are counts here and rows at
  // the step level.

  /* The column plan: which steps are drawn in full and which are folded,
   * given the width. Returns [{kind: "step", step} | {kind: "fold", steps}]. */
  function plan(m, avail, unfolded) {
    var steps = m.steps;
    function width(cols) { return cols.reduce(function (a, c) { return a + (c.kind === "fold" ? L.glyph.foldWidth(c.steps.length) : MIN_COL); }, 0); }
    var cols = steps.map(function (s) { return { kind: "step", step: s }; });
    if (width(cols) <= avail) return cols;
    // fold runs of two or more quiet steps the reader has not dilated
    var out = [], run = [];
    function flush() { if (run.length >= 2) out.push({ kind: "fold", steps: run }); else run.forEach(function (s) { out.push({ kind: "step", step: s }); }); run = []; }
    steps.forEach(function (s) { if (s.quiet && !unfolded[s.key]) run.push(s); else { flush(); out.push({ kind: "step", step: s }); } });
    flush();
    cols = out;
    // then the earliest steps, until the rest fits
    var early = [];
    while (cols.length > 1 && width(cols) + (early.length ? L.glyph.foldWidth(early.length) : 0) > avail) {
      var c = cols.shift();
      if (c.kind === "fold") early = early.concat(c.steps); else early.push(c.step);
    }
    if (early.length) cols.unshift({ kind: "fold", steps: early, early: true });
    return cols;
  }


  /* What the hindsight row says beside a step's marks: the lag of every
   * learned flag ("lag 0, 3"), and the recoveries ("↺ recovered, not
   * attributed", or "↺×3" when the column is narrow). */
  function hindText(m, s, cw) {
    var lags = (m.edgesByKind.flags || []).filter(function (e) { return e.learned === true && (m.byId[e.to] ? m.byId[e.to].step : String(e.to).replace(/^step:/, "")) === s.key; }).map(function (e) { return isNum(e.lag) ? e.lag : "?"; });
    var recs = (m.edgesByKind.recovers || []).filter(function (e) { return (m.byId[e.to] ? m.byId[e.to].step : String(e.to).replace(/^step:/, "")) === s.key; });
    var lag = lags.length ? "lag " + lags.join(", ") : "";
    var glyph = "↺" + (recs.length > 1 ? "×" + recs.length : "");
    var variants = !recs.length ? [""] : [glyph + " recovered, not attributed", glyph + " recovered", glyph];
    var rec = "";
    for (var i = 0; i < variants.length; i++) { if ((lag ? lag.length + 1 : 0) + variants[i].length <= cw) { rec = variants[i]; break; } }
    return { lag: lag, rec: rec, recLearned: recs.some(function (e) { return e.learned === true; }) };
  }

  function drawLoop(host, m, tip, api) {
    if (!d3) return;
    var t0 = global.performance && performance.now ? performance.now() : Date.now();
    var W = width(host), narrow = W < 640, wide = W >= 980;
    var padL = narrow ? 54 : 92, padR = 10, rootW = narrow ? 34 : 60;
    var avail = W - padL - padR - rootW;
    var cols = plan(m, avail, api.unfolded);
    var nFold = cols.filter(function (c) { return c.kind === "fold"; }).length, nStep = cols.length - nFold;
    var foldW = cols.reduce(function (a, c) { return a + (c.kind === "fold" ? L.glyph.foldWidth(c.steps.length) : 0); }, 0);
    var cw = nStep ? Math.max(MIN_COL, (avail - foldW) / nStep) : avail;
    // x of every column and every generation
    var x = padL + rootW;
    cols.forEach(function (c) { c.x0 = x; c.w = c.kind === "fold" ? L.glyph.foldWidth(c.steps.length) : cw; c.xc = c.x0 + c.w / 2; x = c.x0 + c.w; });
    var genX = {};
    genX[m.gens[0].id] = padL + rootW / 2;
    cols.forEach(function (c) { if (c.kind === "step") genX[c.step.to] = c.x0 + c.w; else genX[c.steps[c.steps.length - 1].to] = c.x0 + c.w; });
    var colOf = {};
    cols.forEach(function (c) { if (c.kind === "step") colOf[c.step.key] = c; else c.steps.forEach(function (s) { colOf[s.key] = c; }); });
    // the lanes
    var probes = m.probesFired, rowH = 18;
    var yAgent = 30, yHind = yAgent + (wide ? 40 : 30), yProbe0 = yHind + 22, yVal0 = yProbe0 + probes.length * rowH + 20, bandH = 48, yMid = yVal0 + 4 + bandH / 2;
    var yEval = yVal0 + bandH + 46, chipH = 14, chipGap = 3;
    var maxChips = m.evalGens.reduce(function (a, e) { return Math.max(a, (e.adopted || []).length + (e.retired || []).length + (e.demoted || []).length); }, 1);
    var H = yEval + 14 + maxChips * (chipH + chipGap) + 10;
    var sw = d3.scaleLinear().domain([0, Math.max(1, m.maxK)]).range([0, narrow ? 7 : 16]);
    var sm = m.summary, sn = sm.nodes || {}, se = sm.edges || {};
    var label = "the loop of " + (m.family || "the agent") + ": " + plural(m.gens.length, "generation") + " and " + plural(m.steps.length, "agent step") + " left to right; "
      + plural(probes.length, "probe") + " fired " + plural(isNum(se.triggers) ? se.triggers : 0, "time") + " and proposed " + plural(m.ledger.length, "candidate") + " through 5 validators, "
      + plural(isNum(se.adopts) ? se.adopts : 0, "adoption") + " into " + plural(m.evalGens.length, "eval generation") + "; hindsight: " + plural(isNum(se.flags) ? se.flags : 0, "flag") + " (" + (isNum(sm.flags_learned) ? sm.flags_learned : 0) + " learned, " + (isNum(sm.flags_base) ? sm.flags_base : 0) + " base), "
      + plural(isNum(se.recovers) ? se.recovers : 0, "recovery") + ", " + plural(isNum(sm.closures) ? sm.closures : 0, "loop closure") + (nFold ? "; " + plural(nFold, "fold") + " of quiet steps" : "");
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("aria-label", label)
      .attr("data-level", "loop").attr("data-steps", m.steps.length).attr("data-columns", nStep).attr("data-folds", nFold);
    var link = d3.linkVertical().x(function (d) { return d[0]; }).y(function (d) { return d[1]; });
    var hlink = d3.linkHorizontal().x(function (d) { return d[0]; }).y(function (d) { return d[1]; });
    // the keyboard cursor's column
    var cursor = svg.append("rect").attr("class", "cov-cursor").attr("y", 4).attr("height", H - 8).attr("rx", 5).attr("fill", "var(--ink)").attr("fill-opacity", 0).attr("x", padL).attr("width", 0);
    // the gutter labels
    var gut = svg.append("g").attr("class", "cov-gutter");
    function gutter(y, text, cls) { gut.append("text").attr("class", "lab " + (cls || "dim")).attr("x", padL - 8).attr("y", y).attr("text-anchor", "end").text(narrow ? trunc(text, 5) : text); }
    gutter(yAgent + 4, "agent");
    gutter(yHind + 4, "hindsight");
    probes.forEach(function (p, i) { gutter(yProbe0 + i * rowH + 4, p.name, "mono"); });
    gutter(yMid + 4, "validators");
    gutter(yEval + 4, "eval");
    // lane rules
    [yHind + 12, yVal0 - 16, yEval - 18].forEach(function (y) { svg.append("line").attr("class", "rule").attr("x1", padL - 4).attr("x2", W - padR).attr("y1", y).attr("y2", y).attr("stroke-dasharray", "1 3"); });
    // the validators' names, once, under the first step column when there is room
    var firstStep = cols.filter(function (c) { return c.kind === "step"; })[0];
    function bandX(c, j) { return c.x0 + c.w * (0.16 + j * 0.17); }
    if (firstStep && cw >= 150) VALIDATORS.forEach(function (v, j) {
      svg.append("text").attr("class", "tick").attr("x", bandX(firstStep, j)).attr("y", yVal0 - 5).attr("text-anchor", "middle").text(VSHORT[v].slice(0, cw >= 220 ? 12 : 4));
    });

    // ---- the agent lane: generations and the steps between them
    var gensG = svg.append("g").attr("class", "cov-gens");
    var every = Math.max(1, Math.ceil(30 / Math.max(1, cw)));
    m.gens.forEach(function (g) {
      if (!isNum(genX[g.id])) return;
      var gg = gensG.append("g").attr("class", "cov-gen").attr("data-gen", g.id).attr("transform", "translate(" + genX[g.id] + "," + yAgent + ")");
      gg.append("circle").attr("r", 5).attr("fill", "var(--surface)").attr("stroke", "var(--ink-2)").attr("stroke-width", 1.5);
      if (g.i % every === 0 || g.i === m.gens.length - 1) gg.append("text").attr("class", "lab mono").attr("y", -10).attr("text-anchor", "middle").text(trunc(g.id, 5));
    });
    var stepCols = cols.filter(function (c) { return c.kind === "step"; });
    var stepsG = svg.append("g").attr("class", "cov-steps").selectAll("g").data(stepCols).enter().append("g")
      .attr("class", "cov-hit cov-step").attr("data-step", function (c) { return c.step.key; }).attr("data-verdict", function (c) { return c.step.verdict || ""; })
      .attr("tabindex", 0).attr("role", "button")
      .attr("aria-label", function (c) { var s = c.step; return s.key + " · " + verdictOf(s.verdict).word + (s.fired.length ? " · probes " + s.fired.join(", ") + " · " + plural(s.k, "candidate") + ", " + s.adopted + " adopted" : " · no probe fired") + (s.learned.length ? " · " + plural(s.learned.length, "learned flag") : "") + (s.baseMetricFlags.length ? " · " + plural(s.baseMetricFlags.length, "base flag") : ""); });
    stepsG.append("rect").attr("class", "ring").attr("x", function (c) { return c.x0 + 1; }).attr("y", 4).attr("width", function (c) { return c.w - 2; }).attr("height", H - 8).attr("rx", 5).attr("fill", "transparent").attr("stroke", "var(--accent)").attr("stroke-opacity", 0).attr("stroke-width", 1.5);
    stepsG.append("line").attr("class", "seg").attr("x1", function (c) { return genX[c.step.from]; }).attr("x2", function (c) { return genX[c.step.to]; }).attr("y1", yAgent).attr("y2", yAgent)
      .attr("stroke", function (c) { return verdictOf(c.step.verdict).color; }).attr("stroke-width", 3).attr("stroke-linecap", "round");
    stepsG.append("text").attr("class", "lab backed").attr("text-anchor", "middle").attr("pointer-events", "none").attr("x", function (c) { return (genX[c.step.from] + genX[c.step.to]) / 2; }).attr("y", yAgent - 6)
      .attr("fill", function (c) { return verdictOf(c.step.verdict).color; })
      .text(function (c) { var v = verdictOf(c.step.verdict); return cw >= 78 ? verdictLabel(v) : cw >= 30 ? (v.glyph || v.word.charAt(0)) : ""; });
    if (wide) stepsG.append("text").attr("class", "tick").attr("text-anchor", "middle").attr("pointer-events", "none").attr("x", function (c) { return c.xc; }).attr("y", yAgent + 16)
      .text(function (c) { return trunc(c.step.baseFlags.join(" · "), Math.max(3, Math.floor(cw / 7.4) - 1)); });
    // the hindsight marks: ▲ per flag, learned in the accent, base in ink-3 — the targets of the edges back up
    var markX = {};
    stepsG.each(function (c) {
      var s = c.step, flags = s.learned.map(function (f) { return { f: f, learned: true }; }).concat(s.baseMetricFlags.map(function (f) { return { f: f, learned: false }; }));
      var g = d3.select(this).append("g").attr("class", "cov-hmarks");
      var gap = narrow ? 7 : 10, x0 = c.xc - (flags.length - 1) * gap / 2;
      flags.forEach(function (fl, i) {
        var xx = x0 + i * gap;
        markX[s.key + "|" + fl.f.metric] = xx;
        g.append("path").attr("class", "cov-hmark").attr("data-metric", fl.f.metric).attr("data-learned", fl.learned ? "1" : "0")
          .attr("d", "M" + xx + "," + (yHind - 5) + "L" + (xx + 4.5) + "," + (yHind + 3) + "L" + (xx - 4.5) + "," + (yHind + 3) + "Z")
          .attr("fill", fl.learned ? "var(--accent)" : "var(--ink-3)");
      });
      // the edges' labels, written at their target end: the lag of every learned flag, the recoveries
      var lx = flags.length ? x0 + (flags.length - 1) * gap + 8 : c.xc - 8, room = Math.floor((c.x0 + c.w - lx - 2) / 7.2);
      var ht = hindText(m, s, room);
      var txt = [ht.lag, ht.rec].filter(Boolean).join(" ");
      if (txt && room >= 3) g.append("text").attr("class", "tick backed").attr("x", lx).attr("y", yHind + 3).attr("fill", ht.lag || ht.recLearned ? "var(--accent)" : "var(--ink-3)").text(trunc(txt, room));
    });
    stepsG.on("pointermove", function (evt, c) { tip.show(evt, stepLines(m, c.step)); }).on("pointerleave", tip.hide)
      .on("click", function (evt, c) { tip.hide(); api.open(c.step); })
      .on("keydown", function (evt, c) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); evt.stopPropagation(); api.open(c.step); } });
    // the folds
    var folds = svg.append("g").selectAll("g").data(cols.filter(function (c) { return c.kind === "fold"; })).enter().append("g").attr("class", "cov-fold").attr("data-n", function (c) { return c.steps.length; })
      .attr("tabindex", 0).attr("role", "button").attr("aria-label", function (c) { return plural(c.steps.length, (c.early ? "earlier" : "quiet") + " step") + " folded: " + c.steps.map(function (s) { return s.key; }).join(", ") + "; press Enter to open"; });
    folds.append("rect").attr("x", function (c) { return c.x0; }).attr("y", 4).attr("width", function (c) { return c.w; }).attr("height", H - 8).attr("fill", "var(--surface-2)").attr("rx", 4);
    folds.append("line").attr("x1", function (c) { return c.x0 + 2; }).attr("x2", function (c) { return c.x0 + c.w - 2; }).attr("y1", yAgent).attr("y2", yAgent).attr("stroke", "var(--ink-3)").attr("stroke-width", 2).attr("stroke-dasharray", "2 3");
    folds.append("text").attr("class", "tick").attr("text-anchor", "middle").attr("x", function (c) { return c.xc; }).attr("y", yAgent - 6).text("⋯");
    folds.append("text").attr("class", "tick").attr("text-anchor", "middle").attr("x", function (c) { return c.xc; }).attr("y", yAgent + 16).text(function (c) { return c.steps.length; });
    folds.on("pointermove", function (evt, c) { tip.show(evt, [{ b: true, text: plural(c.steps.length, (c.early ? "earlier" : "quiet") + " step") + " folded" }, { text: c.steps.map(function (s) { return s.key + " " + verdictOf(s.verdict).word; }).join(" · ") }, { text: "click to open the fold" }]); })
      .on("pointerleave", tip.hide).on("click", function (evt, c) { tip.hide(); api.unfold(c.steps); })
      .on("keydown", function (evt, c) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); evt.stopPropagation(); api.unfold(c.steps); } });

    // ---- the probe lane and the flow through the validators, per step column
    var rowY = {};
    probes.forEach(function (p, i) { rowY[p.name] = yProbe0 + i * rowH; });
    stepCols.forEach(function (c) {
      var s = c.step, f = s.funnel;
      var col = svg.append("g").attr("class", "cov-col").attr("data-step", s.key).style("cursor", "pointer");
      var xIn = c.x0 + c.w * 0.07;
      // the probe marks, and the links from each to the bundle's entry
      s.fired.forEach(function (name) {
        if (!isNum(rowY[name])) return;
        var n = s.rows.filter(function (r) { return r.probe === name; }).length;
        var y = rowY[name];
        if (n > 0) col.append("path").attr("class", "cov-propose").attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-opacity", 0.45).attr("stroke-width", Math.max(1, sw(n)))
          .attr("d", link({ source: [c.xc, y + 4], target: [xIn, yVal0 + 2] }));
        var mk = col.append("g").attr("class", "cov-pmark").attr("data-probe", name).attr("data-n", n).attr("transform", "translate(" + c.xc + "," + y + ")");
        mk.append("circle").attr("r", 4.2).attr("fill", n > 0 ? "var(--ink-2)" : "var(--surface)").attr("stroke", "var(--ink-2)").attr("stroke-width", 1.5);
        if (cw >= 70) mk.append("text").attr("class", "tick").attr("x", 8).attr("y", 4).text(n > 0 ? "+" + n : "0");
        var p = m.probeByName[name];
        mk.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, [{ b: true, text: name + " at " + s.key }, { text: p ? p.question : "" }, { text: n > 0 ? "proposed " + plural(n, "candidate") + ": " + s.rows.filter(function (r) { return r.probe === name; }).map(function (r) { return r.spec_id + " (" + r.decision + ")"; }).join(", ") : "fired and proposed nothing (a demotion or a retirement, not a candidate)" }, { text: "click to open this step" }]); });
      });
      // the bundle: down from the entry, then right through the five bands
      var y0 = yVal0 + 2;
      if (f.n > 0) col.append("path").attr("class", "cov-ribbon").attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-opacity", 0.55).attr("stroke-width", sw(f.n)).attr("stroke-linecap", "butt")
        .attr("d", "M" + xIn + "," + y0 + "L" + xIn + "," + yMid + "L" + bandX(c, 0) + "," + yMid);
      VALIDATORS.forEach(function (v, j) {
        var bx = bandX(c, j), bw = Math.max(3, Math.min(6, c.w * 0.03));
        var reached = f.reach[j], stopped = f.stop[j];
        var band = col.append("g").attr("class", "cov-band").attr("data-validator", v).attr("data-reached", reached).attr("data-stopped", stopped);
        band.append("rect").attr("x", bx - bw / 2).attr("y", yVal0 + 2).attr("width", bw).attr("height", bandH + 2).attr("rx", 1.5).attr("fill", reached > 0 ? "var(--ink)" : "var(--ink-3)").attr("fill-opacity", reached > 0 ? 0.55 : 0.2);
        if (stopped > 0) {
          band.append("path").attr("class", "cov-exit").attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-opacity", 0.8).attr("stroke-width", sw(stopped)).attr("d", "M" + bx + "," + yMid + "L" + bx + "," + (yVal0 + bandH + 6));
          if (cw >= 110) band.append("text").attr("class", "tick backed").attr("x", bx).attr("y", yVal0 + bandH + 16).attr("text-anchor", "middle").attr("fill", "var(--bad)").text("−" + stopped);
        }
        if (j < VALIDATORS.length - 1 && reached - stopped > 0) band.append("path").attr("class", "cov-ribbon").attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-opacity", 0.55).attr("stroke-width", sw(reached - stopped)).attr("d", "M" + bx + "," + yMid + "L" + bandX(c, j + 1) + "," + yMid);
        band.append("rect").attr("x", bx - Math.max(8, c.w * 0.08)).attr("y", yVal0).attr("width", Math.max(16, c.w * 0.16)).attr("height", bandH + 20).attr("fill", "transparent");
        var names = s.rows.filter(function (r) { return firstFailed(Array.isArray(r.failed) ? r.failed : []) === v; }).map(function (r) { return r.spec_id; });
        band.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, [{ b: true, text: VSHORT[v] + " at " + s.key }, { text: plural(reached, "candidate") + " reached it, " + stopped + " stopped here" + (names.length ? ": " + names.join(", ") : "") + (reached - stopped > 0 ? "; " + (reached - stopped) + " went on" : "") }, { text: "click to open this step" }]); });
      });
      // the survivors reach the eval lane
      if (f.out > 0) col.append("path").attr("class", "cov-adopt").attr("fill", "none").attr("stroke", "var(--good)").attr("stroke-opacity", 0.75).attr("stroke-width", sw(f.out))
        .attr("d", link({ source: [bandX(c, VALIDATORS.length - 1), yMid], target: [c.xc, yEval - 10] }));
      col.on("pointermove", function (evt) { tip.show(evt, stepLines(m, s)); }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); api.open(s); });
    });

    // ---- the eval lane: e0 under the root, e_k under the step that made it, the metrics each bore as chips
    var chipTop = {};
    var evalX = {};
    var evalsG = svg.append("g").attr("class", "cov-evals");
    var prevX = null;
    m.evalGens.forEach(function (e) {
      var c = e.after_step ? colOf[e.after_step] : null;
      var xc = e.after_step ? (c && c.kind === "step" ? c.xc : c ? c.xc : null) : padL + rootW / 2;
      if (!isNum(xc)) return;
      evalX[e.id] = xc;
      var folded = !!(c && c.kind === "fold");
      var nw = Math.min(narrow ? 30 : 56, Math.max(26, cw - 8)), nh = 18;
      var g = evalsG.append("g").attr("class", "cov-hit cov-eval").attr("data-eval", e.id).attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", e.id + (e.after_step ? " after " + e.after_step + " (" + (e.trigger_probe || "") + ")" : ", the base eval") + ": " + plural(isNum(e.size) ? e.size : 0, "metric") + (e.adopted && e.adopted.length ? ", adopted " + e.adopted.join(", ") : "") + (e.retired && e.retired.length ? ", retired " + e.retired.join(", ") : "") + (e.demoted && e.demoted.length ? ", demoted " + e.demoted.join(", ") : ""));
      if (prevX !== null) evalsG.insert("path", ":first-child").attr("class", "cov-advance").attr("fill", "none").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.2).attr("d", "M" + (prevX + nw / 2) + "," + yEval + "L" + (xc - nw / 2) + "," + yEval);
      if (prevX !== null && wide && xc - prevX > 90) evalsG.append("text").attr("class", "tick backed").attr("text-anchor", "middle").attr("x", (prevX + xc) / 2).attr("y", yEval - 4).text(trunc(String(e.trigger_probe || ""), Math.floor((xc - prevX - 40) / 6.4)));
      g.append("rect").attr("class", "ring").attr("x", xc - nw / 2).attr("y", yEval - nh / 2).attr("width", nw).attr("height", nh).attr("rx", 4).attr("fill", "var(--surface)").attr("stroke", folded ? "var(--ink-3)" : "var(--accent)").attr("stroke-width", 1.5).attr("stroke-dasharray", folded ? "2 2" : null);
      g.append("text").attr("class", "lab mono strong").attr("x", xc).attr("y", yEval + 4).attr("text-anchor", "middle").text(nw >= 50 ? e.id + " · " + (isNum(e.size) ? e.size : "?") : e.id);
      // the chips
      var items = (e.adopted || []).map(function (id) { return { id: id, kind: "adopted" }; }).concat((e.demoted || []).map(function (id) { return { id: id, kind: "demoted" }; }), (e.retired || []).map(function (id) { return { id: id, kind: "retired" }; }));
      var chipW = Math.min(wide ? 150 : 96, Math.max(14, (e.after_step ? cw : rootW + (cols[0] ? cols[0].w * 0.4 : 0)) - 6));
      items.forEach(function (it, i) {
        var y = yEval + 14 + i * (chipH + chipGap), mt = m.metricById[it.id];
        var st = mt ? mt.status : it.kind;
        var chip = g.append("g").attr("class", "cov-chip-m").attr("data-metric", it.id).attr("data-kind", it.kind);
        chip.append("rect").attr("x", xc - chipW / 2).attr("y", y).attr("width", chipW).attr("height", chipH).attr("rx", 3).attr("fill", "var(--surface-2)")
          .attr("stroke", it.kind === "adopted" ? statusColor(st === "base" ? "base" : "adopted") : "var(--ink-3)").attr("stroke-width", 1).attr("stroke-dasharray", it.kind !== "adopted" || st === "retired" || st === "demoted" ? "2 2" : null);
        if (chipW >= 40) chip.append("text").attr("class", "tick").attr("x", xc).attr("y", y + 10.5).attr("text-anchor", "middle").attr("fill", it.kind === "adopted" && st !== "retired" && st !== "demoted" ? "var(--ink-2)" : "var(--ink-3)")
          .text((it.kind === "retired" ? "✕ " : it.kind === "demoted" ? "↓ " : "") + trunc(it.id, Math.floor((chipW - 6) / 6.2) - (it.kind === "adopted" ? 0 : 2)));
        chipTop[it.id + "@" + e.id] = { x: xc, y: y };
        if (it.kind === "adopted" && !chipTop[it.id]) chipTop[it.id] = { x: xc, y: y };
        chip.on("pointermove", function (evt) {
          evt.stopPropagation();
          tip.show(evt, [{ b: true, text: it.id + " · " + (mt ? mt.name : "") }, { text: it.kind + " at " + e.id + (e.after_step ? " after " + e.after_step : " (the base eval)") + (mt && mt.status !== it.kind && it.kind === "adopted" ? " · now " + mt.status : "") },
            mt && mt.raw.caught_at && isNum(mt.raw.caught_at.lag) ? { text: "hindsight: " + mt.raw.caught_at.note } : null,
            mt && mt.raw.confirmation ? { text: "confirmation: " + mt.raw.confirmation.status + " (" + mt.raw.confirmation.moved + " of " + mt.raw.confirmation.tested + " later steps moved)" } : null,
            { text: "click to open the metric" }]);
        }).on("click", function (evt) { evt.stopPropagation(); tip.hide(); select({ metric: it.id }); });
      });
      g.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: e.id + (e.after_step ? " · after " + e.after_step + " · " + (e.trigger_probe || "") : " · the base eval") }, { text: plural(isNum(e.size) ? e.size : 0, "active metric") + (items.length ? ": " + items.map(function (it) { return it.kind + " " + it.id; }).join(", ") : "") }, { text: e.after_step ? "click to open the step that made it" : "click to open the base eval" }]); })
        .on("pointerleave", tip.hide).on("click", function () { tip.hide(); api.openEval(e); })
        .on("keydown", function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); evt.stopPropagation(); api.openEval(e); } });
      prevX = xc;
    });

    // ---- back up the picture: the hindsight edges and the recovery edges
    var backG = svg.insert("g", ".cov-gens").attr("class", "cov-back");
    function metricIdOf(nodeId) { var n = m.byId[nodeId]; return n && n.metric ? n.metric : String(nodeId).replace(/^metric:/, ""); }
    function stepKeyOf(nodeId) { var n = m.byId[nodeId]; return n && n.step ? n.step : String(nodeId).replace(/^step:/, ""); }
    (m.edgesByKind.flags || []).concat(m.edgesByKind.recovers || []).forEach(function (e) {
      var mid = metricIdOf(e.from), key = stepKeyOf(e.to), c = colOf[key], src = chipTop[mid];
      if (!c || !src) return;
      var recover = e.kind === "recovers";
      var tx = c.kind === "fold" ? c.xc : (isNum(markX[key + "|" + mid]) && !recover ? markX[key + "|" + mid] : c.xc);
      var learned = e.learned === true;
      var g = backG.append("g").attr("class", "cov-edge " + e.kind).attr("data-kind", e.kind).attr("data-metric", mid).attr("data-step", key).attr("data-learned", learned ? "1" : "0").attr("data-lag", isNum(e.lag) ? e.lag : null);
      var d = link({ source: [src.x, src.y], target: [tx, yHind + (recover ? 9 : 4)] });
      g.append("path").attr("fill", "none").attr("stroke", "transparent").attr("stroke-width", 10).attr("d", d);
      g.append("path").attr("class", "line").attr("fill", "none").attr("stroke", learned ? "var(--accent)" : "var(--ink-3)").attr("stroke-width", recover ? 1 : learned ? 1.3 : 1).attr("stroke-opacity", recover ? 0.55 : learned ? 0.85 : 0.5).attr("stroke-dasharray", recover ? "1.5 3" : "5 3").attr("d", d);
      var fl = e.flag ? m.byId[e.flag] : null;
      g.on("pointermove", function (evt) {
        evt.stopPropagation();
        tip.show(evt, [{ b: true, text: (recover ? "recovered on " : "flagged by ") + metricName(m, mid) + " at " + key },
          { mono: true, text: "delta " + dci(recover ? e.delta : fl && fl.delta) + (learned ? " · a learned metric" : " · a base metric") },
          recover ? { text: "recovered, not attributed: a later step on which the metric moved back in its good direction" + (Array.isArray(e.after) && e.after.length ? " after " + e.after.join(", ") : "") + " — a measured fact, not a cause" } : { text: String(e.label || "") + (isNum(e.lag) ? " · lag " + plural(e.lag, "step") : "") },
          { text: "click to open the step" }]);
      }).on("pointerleave", tip.hide).on("click", function (evt) { evt.stopPropagation(); tip.hide(); if (m.stepByKey[key]) api.open(m.stepByKey[key]); });
    });

    function apply(animate) {
      var dur = animate && !prefersReduced() ? DUR : 0;
      var c = api.cursorCol();
      var t = dur ? cursor.transition().duration(dur) : cursor;
      if (c) t.attr("x", c.x0).attr("width", c.w).attr("fill-opacity", 0.06); else t.attr("fill-opacity", 0);
      stepsG.attr("aria-current", function (cc) { return c === cc ? "true" : "false"; });
    }
    apply(false);
    var t1 = global.performance && performance.now ? performance.now() : Date.now();
    host.setAttribute("data-draw-ms", (t1 - t0).toFixed(1));
    return { apply: apply, cols: cols, stepCols: stepCols };
  }

  // ---- the step level: one column of the loop, its candidates as rows

  /* The step's funnel at full width: the five validators named, the ribbon
   * of its candidates through them, the exits with their counts. The same
   * numbers as the loop's column, with room to read them. */
  function drawFunnel(host, m, s) {
    if (!d3) return;
    var W = width(host), padL = 12, padR = 12, H = 96, yMid = 40, bandH = 44;
    var f = s.funnel;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("data-step", s.key)
      .attr("aria-label", s.key + ": " + plural(f.n, "candidate") + " through the five validators — " + VALIDATORS.map(function (v, j) { return VSHORT[v] + " reached by " + f.reach[j] + ", stopped " + f.stop[j]; }).join("; ") + "; " + f.out + " adopted");
    var sw = d3.scaleLinear().domain([0, Math.max(1, f.n)]).range([0, 18]);
    var bx = function (j) { return padL + 40 + (W - padL - padR - 80) * j / (VALIDATORS.length - 1); };
    if (f.n > 0) svg.append("path").attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-opacity", 0.55).attr("stroke-width", sw(f.n)).attr("d", "M" + padL + "," + yMid + "L" + bx(0) + "," + yMid);
    svg.append("text").attr("class", "tick").attr("x", padL).attr("y", yMid - sw(f.n) / 2 - 5).text(f.n + " in");
    VALIDATORS.forEach(function (v, j) {
      var x = bx(j), reached = f.reach[j], stopped = f.stop[j];
      svg.append("rect").attr("x", x - 3).attr("y", yMid - bandH / 2).attr("width", 6).attr("height", bandH).attr("rx", 1.5).attr("fill", reached > 0 ? "var(--ink)" : "var(--ink-3)").attr("fill-opacity", reached > 0 ? 0.55 : 0.2);
      svg.append("text").attr("class", "lab mono").attr("x", x).attr("y", yMid - bandH / 2 - 6).attr("text-anchor", "middle").text(W < 480 ? VSHORT[v].slice(0, 4) : VSHORT[v]);
      if (stopped > 0) {
        svg.append("path").attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-opacity", 0.8).attr("stroke-width", sw(stopped)).attr("d", "M" + x + "," + yMid + "L" + x + "," + (yMid + bandH / 2 + 8));
        svg.append("text").attr("class", "tick").attr("x", x).attr("y", yMid + bandH / 2 + 20).attr("text-anchor", "middle").attr("fill", "var(--bad)").text("−" + stopped);
      }
      if (j < VALIDATORS.length - 1 && reached - stopped > 0) svg.append("path").attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-opacity", 0.55).attr("stroke-width", sw(reached - stopped)).attr("d", "M" + x + "," + yMid + "L" + bx(j + 1) + "," + yMid);
    });
    if (f.out > 0) {
      svg.append("path").attr("fill", "none").attr("stroke", "var(--good)").attr("stroke-opacity", 0.75).attr("stroke-width", sw(f.out)).attr("d", "M" + bx(VALIDATORS.length - 1) + "," + yMid + "L" + (W - padR) + "," + yMid);
      svg.append("text").attr("class", "tick").attr("x", W - padR).attr("y", yMid - sw(f.out) / 2 - 5).attr("text-anchor", "end").attr("fill", "var(--good)").text(f.out + " adopted");
    } else svg.append("text").attr("class", "tick").attr("x", W - padR).attr("y", yMid - 5).attr("text-anchor", "end").text("0 adopted");
  }

  function marksSpan(H, row, tip) {
    var span = H("span", { class: "cov-marks", role: "img", "aria-label": VALIDATORS.map(function (v) { return VSHORT[v] + " " + markOf(row, v).word; }).join(", ") });
    VALIDATORS.forEach(function (v) {
      var mk = markOf(row, v), r = row.validators && row.validators[v];
      var el = H("span", { class: "cov-mark " + mk.cls, "data-validator": v, "data-mark": mk.cls.split(" ")[0], text: mk.glyph, title: VSHORT[v] + ": " + mk.word + (r && r.note ? " — " + r.note : "") });
      if (tip) { el.addEventListener("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, [{ b: true, text: VSHORT[v] + " · " + mk.word }, r && r.note ? { text: r.note } : null]); }); el.addEventListener("pointerleave", tip.hide); }
      span.appendChild(el);
    });
    return span;
  }

  function ledgerTable(H, rows, m) {
    var table = H("table", { class: "cov-table" }, [H("thead", null, H("tr", null, ["#", "step", "probe", "candidate", "spec"].concat(VALIDATORS.map(function (v) { return VSHORT[v]; }), ["K", "α", "decision", "reason"]).map(function (t) { return H("th", { text: t }); })))]);
    var body = H("tbody");
    rows.forEach(function (r) {
      body.appendChild(H("tr", { "data-candidate": r.index }, [H("td", { class: "num", text: r.index }), H("td", { text: r.step }), H("td", { text: r.probe || "" }), H("td", { text: r.spec_id }), H("td", { class: "wrap", text: specSentence(r.spec) })]
        .concat(VALIDATORS.map(function (v) { var mk = markOf(r, v); return H("td", { class: mk.cls.indexOf("fail") === 0 ? "bad" : mk.cls === "pass" ? "good" : "", text: mk.glyph + " " + mk.word }); }),
          [H("td", { class: "num", text: isNum(r.k) ? r.k : "—" }), H("td", { class: "num", text: alpha(r.alpha) }), H("td", { class: r.decision === "adopted" ? "good" : "bad", text: r.decision || "" }), H("td", { class: "wrap", text: r.reason || "" })])));
    });
    table.appendChild(body);
    return H("div", { class: "scroll-x" }, table);
  }

  function renderStepLevel(host, m, tip, api) {
    var H = api.H, s = currentStep(m), st = state();
    var e = st.evalGen ? m.evalGens.filter(function (x) { return x.id === st.evalGen; })[0] : (s ? s.evalGen : null);
    host.innerHTML = "";
    if (!s) {
      // the base eval: no step made it
      var ids = e ? (e.adopted || []) : m.base;
      host.appendChild(H("p", { class: "cov-lede", "data-eval": e ? e.id : "" }, [H("b", { text: (e ? e.id : "e0") + " is the base eval" }), H("span", { text: ": " + plural(ids.length, "metric") + " the shipped Evolution reading already uses, in the metric language; no agent step made it and no probe fired. Base metrics are never retired or demoted, so a number never changes meaning across time." })]));
      var list = H("div", { class: "cov-rows", role: "list", "aria-label": "the base metrics" });
      ids.forEach(function (id) {
        var mt = m.metricById[id];
        list.appendChild(H("div", { class: "cov-row cov-cand", role: "listitem", tabindex: "0", "data-metric": id, "aria-label": id + ": " + (mt ? specSentence(mt.spec) : ""), onclick: function () { select({ metric: id }); }, onkeydown: function (evt) { if (evt.key === "Enter") { evt.preventDefault(); select({ metric: id }); } } }, [
          H("span", { class: "id", text: id }), H("span", { class: "spec", text: mt ? specSentence(mt.spec) : "" }), H("span", { text: "base" }), H("span", { class: "n k", text: "" }), H("span", { class: "n alpha", text: "" }), H("span", { class: "cov-dec", text: "" }), H("span", { class: "became", text: mt ? mt.name : "" })]));
      });
      host.appendChild(list);
      return;
    }
    var v = verdictOf(s.verdict);
    host.appendChild(H("p", { class: "cov-lede", "data-step": s.key, "data-verdict": s.verdict || "" }, [
      H("b", { text: s.key }), H("span", { text: " · the base eval said " }), verdictSpan(H, s.verdict), H("span", { text: (s.baseFlags.length ? " (flags: " + s.baseFlags.join(", ") + ")" : "") + " · " + (s.fired.length ? plural(s.fired.length, "probe") + " fired, " + plural(s.k, "candidate") + " tested at α " + (s.rows.length ? alpha(s.rows[0].alpha) : "—") + ", " + s.adopted + " adopted" : "no probe fired, nothing was proposed") + (s.evalGen ? " → " + s.evalGen.id : "") + "." })]));
    if (s.reading) host.appendChild(H("p", { class: "cov-read", "data-role": "reading", text: cap(s.reading) }));
    if (s.learned.length || s.baseMetricFlags.length) {
      var fl = H("p", { class: "cov-read", "data-role": "flags" });
      s.learned.forEach(function (f) { fl.appendChild(H("span", { class: "cov-flag learned", text: "▲ " + f.metric + " " + dci(f.delta), title: "a learned metric flags this step" })); });
      s.baseMetricFlags.forEach(function (f) { fl.appendChild(H("span", { class: "cov-flag base", text: "▲ " + f.metric + " " + dci(f.delta), title: "a base metric's own interval moves against its direction" })); });
      host.appendChild(fl);
    }
    var cols = H("div", { class: "cov-cols" });
    host.appendChild(cols);
    var left = H("div");
    cols.appendChild(left);
    left.appendChild(H("div", { class: "cov-h", text: "probes that fired" }));
    if (!s.fired.length) left.appendChild(H("p", { class: "cov-read", text: "None: the step exposed no blind spot the probes watch for." }));
    else {
      var ul = H("ul", { class: "cov-list", "data-role": "probes" });
      s.fired.forEach(function (name) {
        var p = m.probeByName[name], n = s.rows.filter(function (r) { return r.probe === name; }).length;
        ul.appendChild(H("li", { "data-probe": name }, [H("span", { class: "mono", text: name }), H("span", { text: " — " + (p ? p.question : "") + " · proposed " + n + (n === 0 ? " (it demoted or retired instead)" : "") })]));
      });
      left.appendChild(ul);
    }
    if (s.evalGen) {
      var eg = s.evalGen;
      left.appendChild(H("div", { class: "cov-h", text: "the eval generation it made" }));
      left.appendChild(H("p", { class: "cov-read", "data-eval": eg.id }, [H("b", { text: eg.id }), H("span", { text: " · " + plural(isNum(eg.size) ? eg.size : 0, "active metric") + " · trigger " + (eg.trigger_probe || "—") + (eg.adopted && eg.adopted.length ? " · adopted " + eg.adopted.join(", ") : "") + (eg.retired && eg.retired.length ? " · retired " + eg.retired.join(", ") : "") + (eg.demoted && eg.demoted.length ? " · demoted " + eg.demoted.join(", ") : "") + "." })]));
    }
    var right = H("div");
    cols.appendChild(right);
    if (s.rows.length) {
      right.appendChild(H("div", { class: "cov-h", text: "the funnel" }));
      var fh = H("div", { class: "cov-chart" });
      right.appendChild(responsive(fh, function () { fh.innerHTML = ""; drawFunnel(fh, m, s); }, "cov-funnel"));
    }
    host.appendChild(H("div", { class: "cov-h", text: "candidates · " + s.rows.length }));
    var list = H("div", { class: "cov-rows", role: "list", "aria-label": "the candidates tested at " + s.key });
    list.appendChild(H("div", { class: "cov-row cov-cand cov-head", role: "presentation" }, [H("span", { text: "candidate" }), H("span", { class: "spec", text: "spec" }), H("span", { text: "c i d l n", title: "computable · informative · distinct · linked · not already" }), H("span", { class: "n k", text: "K" }), H("span", { class: "n alpha", text: "α" }), H("span", { text: "decision" }), H("span", { class: "became", text: "became" })]));
    s.rows.forEach(function (r) {
      var dist = r.validators && r.validators.distinct, rep = dist && dist.representative && dist.representative !== r.spec_id && dist.pass === false ? dist : null;
      var row = H("div", { class: "cov-row cov-cand", role: "listitem", tabindex: "0", "data-candidate": r.index, "data-decision": r.decision || "", "aria-current": st.candidate === r.index ? "true" : "false",
        "aria-label": r.spec_id + ", " + (r.probe || "") + ", " + (r.decision || "") + (Array.isArray(r.failed) && r.failed.length ? " at " + r.failed.join(", ") : ""),
        onclick: function () { tip.hide(); api.openCandidate(r); }, onkeydown: function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); evt.stopPropagation(); api.openCandidate(r); } } }, [
        H("span", { class: "id", title: r.spec_id, text: r.spec_id }), H("span", { class: "spec", title: specSentence(r.spec), text: specSentence(r.spec) }), marksSpan(H, r, tip),
        H("span", { class: "n k", text: isNum(r.k) ? r.k : "—" }), H("span", { class: "n alpha", text: alpha(r.alpha) }),
        H("span", { class: "cov-dec " + (r.decision || ""), text: r.decision || "—" }),
        H("span", { class: "became", text: r.decision === "adopted" ? (m.metricById[r.spec_id] ? m.metricById[r.spec_id].name : r.spec_id) : "—" }),
        H("span", { class: "why", text: (r.reason || "") + (rep ? " — the representative of its class is " + rep.representative + (rep.rule ? ", by " + rep.rule : "") : "") }),
      ]);
      row.addEventListener("pointermove", function (evt) { tip.show(evt, [{ b: true, text: r.spec_id + " · " + (r.probe || "") + " · " + (r.decision || "") }, { text: specSentence(r.spec) }, { text: r.reason || "" }, { text: "click to open the candidate" }]); });
      row.addEventListener("pointerleave", tip.hide);
      list.appendChild(row);
    });
    if (!s.rows.length) list.appendChild(H("p", { class: "cov-read", text: "No candidate was tested at this step." }));
    host.appendChild(list);
    if (s.rows.length) host.appendChild(H("details", { class: "cov-details" }, [H("summary", { text: "table view: the ledger rows of " + s.key }), ledgerTable(H, s.rows, m)]));
  }

  // ---- the candidate level: one ledger row in full

  function drawDelta(host, r) {
    if (!d3) return;
    var inf = r.validators && r.validators.informative, d = inf && inf.delta;
    if (!d || !isNum(d.point)) return;
    var W = width(host), padL = 12, padR = 12, H = 44, y = 18;
    var lo = Math.min(0, isNum(d.lo) ? d.lo : d.point), hi = Math.max(0, isNum(d.hi) ? d.hi : d.point);
    if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
    var x = d3.scaleLinear().domain([lo, hi]).nice().range([padL, W - padR]);
    var level = isNum(inf.alpha) ? 1 - inf.alpha : null;
    var svg = L.svg({ viewBox: "0 0 " + W + " " + H, "aria-label": "the step's delta " + dci(d) + (level !== null ? " at level " + num(level, 4) : "") + (d.excludes_zero ? ", excluding zero" : ", including zero") + "; the zero line drawn" });
    host.appendChild(svg);
    var el = d3.select(svg);
    el.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0)).attr("y1", y - 12).attr("y2", y + 12);
    x.ticks(W < 480 ? 3 : 5).forEach(function (t) { el.append("text").attr("class", "tick").attr("x", x(t)).attr("y", H - 4).attr("text-anchor", "middle").text(signed(t, 2)); });
    L.glyph.interval(svg, x, d.point, d.lo, d.hi, { y: y, color: d.excludes_zero ? (d.point < 0 ? "var(--bad)" : "var(--good)") : "var(--ink-3)", width: 3 });
  }

  function renderCandidateLevel(host, m, tip, api) {
    var H = api.H, st = state(), r = candidateRow(m, st.candidate), s = r ? m.stepByKey[r.step] : null;
    host.innerHTML = "";
    if (!r) { host.appendChild(H("p", { class: "cov-read", text: "No candidate is selected." })); return; }
    var mt = m.metricById[r.spec_id], V = r.validators || {};
    host.appendChild(H("p", { class: "cov-lede", "data-candidate": r.index, "data-decision": r.decision || "" }, [
      H("b", { text: r.spec_id }), H("span", { text: " · " + (r.spec && r.spec.name ? r.spec.name : "") + " — proposed by " + (r.probe || "?") + " at " + r.step + " (" + (r.eval_gen || "?") + " in force), " }),
      H("span", { class: "cov-dec " + (r.decision || ""), text: r.decision || "—" }), H("span", { text: Array.isArray(r.failed) && r.failed.length ? " at " + r.failed.map(function (f) { return VSHORT[f] || f; }).join(", ") + "." : "." })]));
    host.appendChild(H("p", { class: "cov-read", "data-role": "spec", text: "Spec: " + specSentence(r.spec) + ". Tested with " + (isNum(r.k) ? r.k : "?") + " other candidates at this step, so the level is α " + alpha(r.alpha) + " (Bonferroni, " + alpha(m.thresholds.alpha) + " / K)." + (r.rank && r.rank.because ? " Because: " + (Array.isArray(r.rank.because) ? r.rank.because.join("; ") : r.rank.because) + "." : r.rank && isNum(r.rank.shift) ? " Ranked by its shift against the outcome: " + signed(r.rank.shift) + (isNum(r.rank.standardised) ? " (standardised " + num(r.rank.standardised) + ")" : "") + "." : "") }));
    var cols = H("div", { class: "cov-cols" });
    host.appendChild(cols);
    function section(v, kids) {
      var mk = markOf(r, v), rr = V[v] || {};
      var box = H("div", { class: "cov-val", "data-validator": v });
      box.appendChild(H("div", { class: "cov-h" }, [H("span", { class: "cov-mark " + mk.cls, text: mk.glyph }), H("span", { text: " " + VSHORT[v] + " · " + mk.word })]));
      if (rr.note) box.appendChild(H("p", { class: "cov-read", style: { margin: "0 0 4px" }, text: cap(String(rr.note)) + "." }));
      (kids || []).forEach(function (k) { if (k) box.appendChild(k); });
      return box;
    }
    var c = V.computable || {};
    cols.appendChild(section("computable", [H("p", { class: "cov-read", style: { margin: 0 } }, [H("span", { class: "cov-ci", text: "coverage " + (isNum(c.coverage) ? pct(c.coverage) : "—") + " · " + r.step.split("→")[0] + " " + ci(c.from) + (c.from && isNum(c.from.n) ? " (n " + c.from.n + ")" : "") + " · " + r.step.split("→")[1] + " " + ci(c.to) + (c.to && isNum(c.to.n) ? " (n " + c.to.n + ")" : "") })])]));
    var inf = V.informative || {};
    var dh = H("div", { class: "cov-chart" });
    cols.appendChild(section("informative", [
      H("p", { class: "cov-read", style: { margin: 0 } }, [H("span", { class: "cov-big", "data-delta": inf.delta && isNum(inf.delta.point) ? inf.delta.point : "", text: inf.delta && isNum(inf.delta.point) ? "Δ " + signed(inf.delta.point) : "no delta" }), H("span", { class: "cov-ci", text: inf.delta && isNum(inf.delta.lo) ? " [" + signed(inf.delta.lo) + ", " + signed(inf.delta.hi) + "] at level " + num(1 - (isNum(inf.alpha) ? inf.alpha : r.alpha), 4) + (inf.delta.excludes_zero ? " · excludes zero" : " · includes zero") : (inf.rule ? " · " + inf.rule : "") })]),
      inf.delta && isNum(inf.delta.point) ? responsive(dh, function () { dh.innerHTML = ""; drawDelta(dh, r); }, "cov-delta") : null]));
    var dist = V.distinct || {}, against = Array.isArray(dist.against) ? dist.against : [];
    var dt = H("table", { class: "cov-table", "data-role": "against" }, [H("thead", null, H("tr", null, ["against", "ρ", "basis", "n", "note"].map(function (t) { return H("th", { text: t }); }))), H("tbody", null, against.map(function (a) {
      return H("tr", null, [H("td", { text: a.metric }), H("td", { class: "num" + (isNum(a.rho) && Math.abs(a.rho) >= (m.thresholds.redundant_rho || 0.9) ? " bad" : ""), text: isNum(a.rho) ? signed(a.rho, 2) : "—" }), H("td", { text: a.basis || "" }), H("td", { class: "num", text: isNum(a.n) ? a.n : "—" }), H("td", { class: "wrap", text: a.note || "" })]);
    }))]);
    cols.appendChild(section("distinct", [
      H("p", { class: "cov-read", style: { margin: 0 }, text: "max |ρ| " + (isNum(dist.max_rho) ? num(dist.max_rho, 2) : "—") + " against " + plural(against.length, "adopted metric") + " (threshold " + num(m.thresholds.redundant_rho, 2) + ")" + (Array.isArray(dist["class"]) && dist["class"].length > 1 ? " · class of " + dist["class"].length + ": " + dist["class"].join(", ") + " · representative " + dist.representative + (dist.rule ? " by " + dist.rule : "") : "") + "." }),
      against.length ? H("div", { class: "scroll-x" }, dt) : null]));
    var lk = V.linked || {};
    cols.appendChild(section("linked", [H("p", { class: "cov-read", style: { margin: 0 }, text: "|ρ(" + (r.spec ? r.spec.feature : "?") + ", success)| " + (isNum(lk.rho) ? num(Math.abs(lk.rho), 2) : "—") + " over the episodes so far, threshold " + num(m.thresholds.link_rho, 2) + (lk.exempt ? " · exempt" : "") + "." })]));
    var na = V.not_already || {};
    cols.appendChild(section("not_already", [na.same_as ? H("p", { class: "cov-read", style: { margin: 0 }, text: "the same spec is already " + na.same_as + "." }) : null]));
    host.appendChild(H("div", { class: "cov-h", text: "decision" }));
    host.appendChild(H("p", { class: "cov-read", "data-role": "decision" }, [H("span", { class: "cov-dec " + (r.decision || ""), text: cap(r.decision || "—") }), H("span", { text: " — " + (r.reason || "no reason recorded") + "." })]));
    if (r.decision === "adopted" && mt) {
      var cf = mt.raw.confirmation, ca = mt.raw.caught_at;
      host.appendChild(H("p", { class: "cov-read", "data-role": "after" }, [H("b", { text: "After adoption · " }), H("span", { text: "status " + mt.status + (mt.raw.retired_at ? " at " + mt.raw.retired_at.step + " (" + mt.raw.retired_at.reason + ")" : "") + (mt.raw.demoted_at ? " at " + (mt.raw.demoted_at.step || "") : "") + (cf ? " · confirmation " + cf.status + ": moved on " + cf.moved + " of " + plural(cf.tested, "later step") : "") + (ca ? " · hindsight: " + ca.note + (isNum(ca.lag) ? " (first flag " + ca.first_flag_step + ", adopted " + ca.adopted_step + ", lag " + ca.lag + ")" : "") : "") + "." })]));
    }
    host.appendChild(H("details", { class: "cov-details" }, [H("summary", { text: "table view: the validators of " + r.spec_id }), H("div", { class: "scroll-x" }, H("table", { class: "cov-table" }, [
      H("thead", null, H("tr", null, ["validator", "result", "note"].map(function (t) { return H("th", { text: t }); }))),
      H("tbody", null, VALIDATORS.map(function (v) { var mk = markOf(r, v), rr = V[v] || {}; return H("tr", null, [H("td", { text: VSHORT[v] }), H("td", { class: mk.cls.indexOf("fail") === 0 ? "bad" : mk.cls === "pass" ? "good" : "", text: mk.glyph + " " + mk.word }), H("td", { class: "wrap", text: rr.note || "" })]); }))]))]));
  }

  function loopTable(H, m) {
    var table = H("table", { class: "cov-table" }, [H("thead", null, H("tr", null, ["step", "base verdict", "base flags", "probes fired", "candidates", "adopted", "eval gen", "learned flags", "base-metric flags"].map(function (t) { return H("th", { text: t }); }))),
      H("tbody", null, m.steps.map(function (s) {
        return H("tr", { "data-step": s.key }, [H("td", { text: s.key }), H("td", { text: verdictOf(s.verdict).word }), H("td", { text: s.baseFlags.join(", ") || "—" }), H("td", { text: s.fired.join(", ") || "—" }), H("td", { class: "num", text: s.k }), H("td", { class: "num", text: s.adopted }), H("td", { text: s.evalGen ? s.evalGen.id : "—" }),
          H("td", { class: "wrap", text: s.learned.map(function (f) { return f.metric + " " + dci(f.delta); }).join("; ") || "—" }), H("td", { class: "wrap", text: s.baseMetricFlags.map(function (f) { return f.metric + " " + dci(f.delta); }).join("; ") || "—" })]);
      }))]);
    return H("div", { class: "scroll-x" }, table);
  }

  AgentDiff.block({
    id: "cov-flow",
    title: "The loop",
    question: "The agent's steps triggering the eval's probes, their candidates flowing through the validators into new eval generations, and what the evolved eval would have caught — one picture, three levels.",
    group: "coevolution",
    size: "full",
    relevance: function (ctx) { return model(ctx).ok ? 0.95 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "cov cov-flow" });
      el.appendChild(root);
      var tip = tooltip(root);
      var sm = m.summary;
      // the bar: the breadcrumb (the way up), the synthetic label, the legend
      var bar = H("div", { class: "cov-bar" });
      var crumbs = H("nav", { class: "cov-crumbs", "aria-label": "zoom path" });
      bar.appendChild(crumbs);
      if (m.synthetic) bar.appendChild(H("span", { class: "cov-chip syn", "data-role": "synthetic", title: "the lineage's traces are a synthetic demo; every number is real arithmetic over synthetic episodes", text: "SYNTHETIC" }));
      bar.appendChild(H("span", { class: "cov-chip" }, [H("i", { style: { background: "var(--accent)" } }), H("span", { text: "learned metric" })]));
      bar.appendChild(H("span", { class: "cov-chip" }, [H("i", { style: { background: "var(--ink-3)" } }), H("span", { text: "base metric" })]));
      bar.appendChild(H("span", { class: "cov-chip", text: "▲ hindsight flag · ┅ would have flagged (lag on the edge) · ┈ ↺ recovered, not attributed" }));
      root.appendChild(bar);
      var status = H("p", { class: "cov-status", role: "status", "aria-live": "polite" });
      root.appendChild(status);
      var stage = H("div", { class: "cov-stage", role: "application", tabindex: "0" });
      root.appendChild(stage);
      var levelHost = H("div", { class: "cov-level" });
      stage.appendChild(levelHost);
      var tableHost = H("div");
      root.appendChild(tableHost);
      root.appendChild(H("p", { class: "cov-note", text:
        "Loop level: time runs left to right by agent step; the agent's generations on top, each step coloured by the base verdict (✕ gamed, ▏forgot, ◎ overfit, ⇄ traded) with its flags under it; under them the hindsight marks (▲ accent: a learned metric would have flagged the step; ▲ grey: a base metric's own interval moved against it); one row per probe that fired, a mark where it did with the candidates it proposed; the step's candidates as a ribbon through the five validators (computable · informative · distinct · linked · not already), as wide as they are many — the ones a validator stops leave downward in red with their count, the adopted reach the eval's generation under the step that made it, bearing the metrics it added as chips. "
        + "The dashed edges run back up from a metric to the steps it would have flagged, the lag written on them; the dotted ones to the step on which the lineage recovered on that metric — recovered, not attributed. Click a step or an eval generation for the step level, a candidate for the candidate level; Escape ascends, ← → move, Enter descends. Every number is the engine's; hover for it, or open the table." }));

      var unfolded = {}, cursorIdx = null, loop = null, drawn = null;
      function dur() { return prefersReduced() ? 0 : DUR; }
      var api = {
        H: H, unfolded: unfolded,
        open: function (s) { select({ step: s.key, evalGen: null, candidate: null }); },
        openEval: function (e) { select({ evalGen: e.id, step: e.after_step && m.stepByKey[e.after_step] ? e.after_step : null, candidate: null }); },
        openCandidate: function (r) { select({ candidate: r.index, step: r.step, evalGen: null }); },
        unfold: function (steps) { steps.forEach(function (s) { unfolded[s.key] = true; }); paint(true); },
        cursorCol: function () { return loop && cursorIdx !== null && loop.stepCols[cursorIdx] ? loop.stepCols[cursorIdx] : null; },
      };
      function crumbsFor(level) {
        crumbs.innerHTML = "";
        var st = state(), s = currentStep(m), r = st.candidate !== null ? candidateRow(m, st.candidate) : null;
        var items = [{ level: 0, label: "loop" }];
        if (level >= 1) items.push({ level: 1, label: s ? s.key : (st.evalGen || "e0") });
        if (level >= 2 && r) items.push({ level: 2, label: r.spec_id });
        items.forEach(function (it, i) {
          if (i) crumbs.appendChild(H("span", { class: "sep", text: "›", "aria-hidden": "true" }));
          var cur = it.level === level;
          crumbs.appendChild(H("button", { type: "button", text: it.label, "data-level": String(it.level), "aria-current": cur ? "true" : "false", title: cur ? "you are here" : "back to the " + LEVEL_NAME[it.level] + " level",
            onclick: function () { if (!cur) ascendTo(it.level); } }));
        });
      }
      function ascendTo(level) {
        var s = currentStep(m);
        if (level === 0) { if (s && loop) cursorIdx = loop.stepCols.map(function (c) { return c.step; }).indexOf(s); select({ step: null, evalGen: null, candidate: null }); }
        else if (level === 1) select({ candidate: null });
      }
      function statusFor(level) {
        var st = state(), s = currentStep(m);
        if (level === 0) status.textContent = (sm.sentence ? String(sm.sentence) : "the loop") + (isNum(sm.closures) && !/closure/.test(String(sm.sentence || "")) ? " Loop closures: " + sm.closures + (isNum(sm.closures_learned) ? " (" + sm.closures_learned + " on learned metrics)" : "") + "." : "");
        else if (level === 1) status.textContent = "Step level: " + (s ? s.key + ", " + verdictOf(s.verdict).word + ", " + plural(s.k, "candidate") + ", " + s.adopted + " adopted" + (s.evalGen ? ", made " + s.evalGen.id : "") : (st.evalGen || "e0") + ", the base eval") + ". ← → move between steps, ↑ ↓ between candidates, Enter opens one, Escape returns to the loop.";
        else { var r = candidateRow(m, st.candidate); status.textContent = "Candidate level: " + (r ? r.spec_id + " at " + r.step + ", " + r.decision : "") + ". ← → move between this step's candidates, Escape returns to the step."; }
        stage.setAttribute("aria-label", "the co-evolution loop, " + LEVEL_NAME[level] + " level. " + status.textContent);
        stage.setAttribute("data-level", LEVEL_NAME[level]);
      }
      function paint(animate) {
        var st = state(), level = levelOf(st);
        crumbsFor(level); statusFor(level);
        levelHost.innerHTML = "";
        tableHost.innerHTML = "";
        loop = null;
        if (level === 0) {
          var host = H("div", { class: "cov-chart" });
          levelHost.appendChild(responsive(host, function () { host.innerHTML = ""; loop = drawLoop(host, m, tip, api); }, "cov-flow"));
          tableHost.appendChild(H("details", { class: "cov-details" }, [H("summary", { text: "table view: every step of the loop" }), loopTable(H, m)]));
          if (m.narrative) tableHost.appendChild(H("details", { class: "cov-details" }, [H("summary", { text: "the whole loop in a paragraph" }), H("p", { class: "cov-note", text: cap(m.narrative) + (m.narrative.slice(-1) === "." ? "" : ".") })]));
        } else if (level === 1) {
          renderStepLevel(levelHost, m, tip, api);
        } else {
          renderCandidateLevel(levelHost, m, tip, api);
        }
        drawn = { level: level, step: st.step, evalGen: st.evalGen, candidate: st.candidate };
        if (animate && dur() > 0 && d3) d3.select(levelHost).style("opacity", 0).transition().duration(dur()).style("opacity", 1);
      }
      paint(false);
      // the keyboard: the stage owns it; a focused node inside handles its own Enter
      stage.addEventListener("keydown", function (evt) {
        var st = state(), level = levelOf(st), handled = true, s = currentStep(m);
        var key = evt.key;
        if (level === 0) {
          var n = loop ? loop.stepCols.length : 0;
          if (key === "ArrowRight" || key === "ArrowLeft") { if (n) { cursorIdx = cursorIdx === null ? (key === "ArrowRight" ? 0 : n - 1) : Math.max(0, Math.min(n - 1, cursorIdx + (key === "ArrowRight" ? 1 : -1))); if (loop) loop.apply(true); } }
          else if (key === "Enter") { if (evt.target !== stage) return; var c = api.cursorCol() || (loop && loop.stepCols.filter(function (cc) { return !cc.step.quiet; })[0]) || (loop && loop.stepCols[0]); if (c) api.open(c.step); }
          else handled = false;
        } else if (level === 1) {
          var i = s ? m.steps.indexOf(s) : -1;
          if (key === "ArrowRight" || key === "ArrowLeft") { var j = i < 0 ? 0 : i + (key === "ArrowRight" ? 1 : -1); if (m.steps[j]) api.open(m.steps[j]); }
          else if (key === "ArrowDown" || key === "ArrowUp") {
            var rows = Array.prototype.slice.call(levelHost.querySelectorAll('.cov-row[role="listitem"]'));
            if (rows.length) { var at = rows.indexOf(document.activeElement); var nx = at < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, at + (key === "ArrowDown" ? 1 : -1))); rows[nx].focus(); }
          } else if (key === "Enter") { if (evt.target !== stage) return; var row = levelHost.querySelector('.cov-row[role="listitem"][data-candidate]'); if (row) api.openCandidate(candidateRow(m, +row.getAttribute("data-candidate"))); }
          else if (key === "Escape" || key === "Esc") ascendTo(0);
          else handled = false;
        } else {
          var r = candidateRow(m, st.candidate), rows2 = s ? s.rows : [], k = r ? rows2.indexOf(r) : -1;
          if (key === "ArrowRight" || key === "ArrowLeft") { var kk = k + (key === "ArrowRight" ? 1 : -1); if (rows2[kk]) api.openCandidate(rows2[kk]); }
          else if (key === "Escape" || key === "Esc") ascendTo(1);
          else handled = false;
        }
        if (handled) { evt.preventDefault(); evt.stopPropagation(); }
      });
      listen(root, function () {
        var st = state(), level = levelOf(st);
        if (drawn && drawn.level === level && drawn.step === st.step && drawn.evalGen === st.evalGen && drawn.candidate === st.candidate) return;
        paint(true);
        stage.focus({ preventScroll: true });
      });
    },
  });

  // =========================================================== cov-hindsight

  /* The lag glyphs: per learned metric a row over the steps of the lineage,
   * the first step it would have flagged marked, the step it was adopted at
   * filled, the lag drawn as the span between them. A metric that never
   * flags a step of this lineage says so. */
  function drawLags(host, m, tip) {
    if (!d3) return;
    var learned = m.metricList.filter(function (mt) { return mt.status !== "base" && mt.raw.caught_at; });
    if (!learned.length) return;
    var W = width(host), narrow = W < 560, labW = narrow ? 96 : 190, padR = narrow ? 12 : 250, rowH = 22, padT = 26, padB = 8;
    var H = padT + learned.length * rowH + padB;
    var keys = m.steps.map(function (s) { return s.key; });
    var x = d3.scalePoint().domain(keys).range([labW + 8, W - padR - 8]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "how many steps late each learned metric came: " + learned.map(function (mt) { var ca = mt.raw.caught_at; return mt.id + " " + (isNum(ca.lag) ? "first flag " + ca.first_flag_step + ", adopted " + ca.adopted_step + ", lag " + ca.lag : ca.note); }).join("; "));
    var every = Math.max(1, Math.ceil(44 / Math.max(1, x.step())));
    keys.forEach(function (k, i) {
      svg.append("line").attr("class", "rule").attr("x1", x(k)).attr("x2", x(k)).attr("y1", padT - 6).attr("y2", H - padB).attr("stroke-dasharray", "1 3");
      if (i % every === 0 || i === keys.length - 1) svg.append("text").attr("class", "tick").attr("x", x(k)).attr("y", 11).attr("text-anchor", "middle").text(narrow ? k.replace("→", "→") : k);
    });
    learned.forEach(function (mt, i) {
      var ca = mt.raw.caught_at, y = padT + i * rowH + rowH / 2;
      var g = svg.append("g").attr("class", "cov-lag").attr("data-metric", mt.id).attr("data-lag", isNum(ca.lag) ? ca.lag : "").style("cursor", "pointer");
      g.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", y + 4).attr("text-anchor", "end").text(trunc(mt.id, Math.floor((labW - 10) / 6.6)));
      var a = ca.first_flag_step && x(ca.first_flag_step) !== undefined ? x(ca.first_flag_step) : null, b = ca.adopted_step && x(ca.adopted_step) !== undefined ? x(ca.adopted_step) : null;
      if (a !== null && b !== null && b > a) g.append("line").attr("x1", a).attr("x2", b).attr("y1", y).attr("y2", y).attr("stroke", "var(--accent)").attr("stroke-width", 4).attr("stroke-opacity", 0.35).attr("stroke-linecap", "round");
      if (a !== null) g.append("path").attr("d", "M" + a + "," + (y - 5) + "L" + (a + 4.5) + "," + (y + 3) + "L" + (a - 4.5) + "," + (y + 3) + "Z").attr("fill", "var(--accent)");
      if (b !== null) g.append("circle").attr("cx", b).attr("cy", y).attr("r", 4).attr("fill", "var(--accent)").attr("stroke", "var(--surface)").attr("stroke-width", 1.5);
      if (a === null) g.append("text").attr("class", "tick").attr("x", b !== null ? b + 8 : labW + 8).attr("y", y + 4).text(narrow ? "never flags" : ca.note || "never flags a step of this lineage");
      else if (isNum(ca.lag) && !narrow) {
        var lx = (b !== null ? b : a) + 9, roomC = Math.floor((W - lx) / 7.2);
        var long = "lag " + ca.lag + (ca.lag !== 0 ? ": would have flagged " + plural(ca.lag, "step") + " before its adoption" : ": adopted at the first step it flags");
        g.append("text").attr("class", "tick backed").attr("x", lx).attr("y", y + 4).attr("fill", "var(--accent)").text(long.length <= roomC ? long : "lag " + ca.lag);
      }
      g.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", W).attr("height", rowH).attr("fill", "transparent");
      g.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: mt.id + " · " + mt.name }, { text: ca.note }, isNum(ca.lag) ? { mono: true, text: "first flag " + ca.first_flag_step + " · adopted " + ca.adopted_step + " · lag " + plural(ca.lag, "step") } : null, { text: "click to open the metric" }]); })
        .on("pointerleave", tip.hide).on("click", function () { tip.hide(); select({ metric: mt.id }); });
    });
  }

  AgentDiff.block({
    id: "cov-hindsight",
    title: "With hindsight",
    question: "Every agent step under the base eval beside what the evolved eval sees, which readings changed, and how many steps late each learned metric came.",
    group: "coevolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.steps.length ? 0.9 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "cov cov-hindsight" });
      el.appendChild(root);
      var tip = tooltip(root);
      var hs = m.hindsight, n = m.steps.length;
      var changed = isNum(hs.changed) ? hs.changed : m.steps.filter(function (s) { return s.changed; }).length;
      var withLearned = m.steps.filter(function (s) { return s.learned.length; });
      var withBase = isNum(hs.steps_with_base_flags) ? hs.steps_with_base_flags : m.steps.filter(function (s) { return s.baseMetricFlags.length; }).length;
      var lede = H("p", { class: "cov-lede", "data-changed": changed, "data-learned": withLearned.length, "data-base": withBase });
      lede.appendChild(H("b", { text: plural(n, "step") + " re-read, " + changed + " changed by what the eval learned, " + withBase + " by a base metric's interval. " }));
      if (!changed && withLearned.length) lede.appendChild(H("span", { text: "The learned metrics flag " + plural(withLearned.length, "step") + " (" + withLearned.map(function (s) { return s.key + ", which the base already called " + verdictOf(s.verdict).word; }).join("; ") + "), so no verdict turns over; the finding is the lag — how many steps before its adoption each learned metric would have flagged." }));
      else if (!changed) lede.appendChild(H("span", { text: "No learned metric flags any step of this lineage." }));
      else lede.appendChild(H("span", { text: "A changed step is one the base called improved or flat that a learned metric flags." }));
      root.appendChild(lede);
      var list = H("div", { class: "cov-rows", role: "list", "aria-label": "every agent step, base verdict beside the evolved reading" });
      list.appendChild(H("div", { class: "cov-row cov-hs cov-head", role: "presentation" }, [H("span", { text: "step" }), H("span", { text: "base" }), H("span", { text: "learned flags" }), H("span", { class: "base-flags", text: "base-metric flags" })]));
      var rows = {};
      m.steps.forEach(function (s) {
        var st = state();
        var row = H("div", { class: "cov-row cov-hs", role: "listitem", tabindex: "0", "data-step": s.key, "data-changed": s.changed ? "1" : "0", "aria-current": st.step === s.key ? "true" : "false",
          "aria-label": s.key + ", base " + verdictOf(s.verdict).word + ", " + plural(s.learned.length, "learned flag") + ", " + plural(s.baseMetricFlags.length, "base-metric flag") + (s.changed ? ", changed" : ""),
          onclick: function () { tip.hide(); select({ step: s.key, evalGen: null, candidate: null }); }, onkeydown: function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); select({ step: s.key, evalGen: null, candidate: null }); } } }, [
          H("span", { class: "id", text: s.key }),
          H("span", null, [verdictSpan(H, s.verdict), s.baseFlags.length ? H("span", { class: "cov-ci", text: " " + s.baseFlags.join(" · ") }) : null]),
          H("span", { class: "learned-flags" }, s.learned.length ? s.learned.map(function (f) { return H("span", { class: "cov-flag learned", "data-metric": f.metric, text: "▲ " + f.metric + " " + dci(f.delta) }); }) : [H("span", { class: "cov-flag none", text: "none" })]),
          H("span", { class: "base-flags" }, s.baseMetricFlags.length ? s.baseMetricFlags.map(function (f) { return H("span", { class: "cov-flag base", "data-metric": f.metric, text: "▲ " + f.metric + " " + dci(f.delta) }); }) : [H("span", { class: "cov-flag none", text: "none" })]),
          H("span", { class: "why", text: s.reading }),
        ]);
        row.addEventListener("pointermove", function (evt) { tip.show(evt, stepLines(m, s)); });
        row.addEventListener("pointerleave", tip.hide);
        rows[s.key] = row;
        list.appendChild(row);
      });
      root.appendChild(list);
      var learned = m.metricList.filter(function (mt) { return mt.status !== "base" && mt.raw.caught_at; });
      if (learned.length) {
        root.appendChild(H("div", { class: "cov-h", text: "how late each learned metric came" }));
        var host = H("div", { class: "cov-chart" });
        root.appendChild(responsive(host, function () { host.innerHTML = ""; drawLags(host, m, tip); }, "cov-lags"));
      }
      root.appendChild(H("p", { class: "cov-note", text: "The base verdict and its flags are the Evolution section's, untouched. A learned flag (▲ accent) is an adopted metric whose delta interval on the step excludes zero in its bad direction; a base-metric flag (▲ grey) is a base metric whose own interval moves against its direction, which the base verdict does not read because it reads the two P(improve) axes. Below: per learned metric, ▲ the first step it would have flagged, ● the step it was adopted at, the bar between them the lag. Click a step to open it in the loop." }));
      listen(root, function (st) { Object.keys(rows).forEach(function (k) { rows[k].setAttribute("aria-current", st.step === k ? "true" : "false"); }); });
    },
  });

  // ============================================================== cov-matrix

  function drawMatrix(host, m, tip, ref) {
    if (!d3) return;
    var W = width(host), narrow = W < 560;
    var gens = m.gens, rows = m.metricList;
    var labW = narrow ? 96 : Math.min(190, 16 + 6.6 * rows.reduce(function (a, r) { return Math.max(a, r.id.length); }, 8));
    var padR = 10, headH = 30, cellH = narrow ? 16 : 20, gap = 2, groupH = 14;
    var groups = ["base", "adopted", "demoted", "retired"];
    var laid = [], y = headH;
    groups.forEach(function (g) {
      var rs = rows.filter(function (r) { return r.status === g; });
      if (!rs.length) return;
      laid.push({ group: g, y: y }); y += groupH;
      rs.forEach(function (r) { laid.push({ row: r, y: y }); y += cellH + gap; });
    });
    var H = y + 6;
    var good = resolve(host, "--good", "#3f7d3f"), bad = resolve(host, "--bad", "#b03030"), mid = resolve(host, "--surface-2", "#f4f4f2");
    var color = d3.scaleDiverging().domain([-2, 0, 2]).interpolator(d3.piecewise(d3.interpolateLab, [bad, mid, good]));
    var x = d3.scaleBand().domain(gens.map(function (g) { return g.id; })).range([labW, W - padR]).paddingInner(gap / Math.max(1, (W - labW - padR) / gens.length));
    // per row: z = (value − mean) / sd over the measurable cells, sign flipped for a down metric so green is always the good direction
    function cells(r) { return m.matrix[r.id] && typeof m.matrix[r.id] === "object" ? m.matrix[r.id] : {}; }
    function stats(r) {
      var vs = gens.map(function (g) { var c = cells(r)[g.id]; return c && c.measurable !== false && isNum(c.point) ? c.point : null; }).filter(isNum);
      var mean = vs.length ? d3.mean(vs) : 0, sd = vs.length > 1 ? d3.deviation(vs) : 0;
      return { mean: mean, sd: isNum(sd) ? sd : 0 };
    }
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", plural(rows.length, "metric") + " by " + plural(gens.length, "generation") + ", each cell the metric's value with its bootstrap interval, coloured by the row's standardised value (z = (value − row mean) / row sd, sign flipped for a lower-is-better metric, clamped at ±2); the cells before a metric's adoption hatched, computed with hindsight; the adoption column outlined");
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "cov-hatch").attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "var(--ink)").attr("stroke-width", 1).attr("stroke-opacity", 0.22);
    var every = Math.max(1, Math.ceil(30 / x.bandwidth()));
    gens.forEach(function (g, i) {
      var s = m.stepByKey[i > 0 ? gens[i - 1].id + "→" + g.id : ""];
      svg.append("text").attr("class", "lab mono").attr("text-anchor", "middle").attr("x", x(g.id) + x.bandwidth() / 2).attr("y", 11).text(i % every === 0 ? trunc(g.id, Math.max(2, Math.floor(x.bandwidth() / 6.5))) : "");
      svg.append("circle").attr("cx", x(g.id) + x.bandwidth() / 2).attr("cy", 20).attr("r", 3).attr("fill", s ? verdictOf(s.verdict).color : "var(--surface)").attr("stroke", s ? "none" : "var(--ink-3)");
    });
    var rowsG = {};
    laid.forEach(function (it) {
      if (it.group) { svg.append("text").attr("class", "tick").attr("x", labW - 8).attr("y", it.y + 10).attr("text-anchor", "end").attr("letter-spacing", ".04em").text(it.group.toUpperCase()); return; }
      var r = it.row, yy = it.y, st = stats(r), cs = cells(r), sign = r.spec.direction === "down" ? -1 : 1;
      var adoptedAt = r.raw.adopted_at && r.raw.adopted_at.step ? m.stepByKey[r.raw.adopted_at.step] : null;
      var adoptGen = adoptedAt ? m.genIndex[adoptedAt.to] : null;
      var g = svg.append("g").attr("class", "cov-mrow").attr("data-metric", r.id).attr("data-status", r.status).attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", r.id + " (" + r.status + "): " + gens.map(function (gg) { var c = cs[gg.id]; return gg.id + " " + (c && c.measurable !== false ? ci(c) : "not measurable"); }).join(", "))
        .style("cursor", "pointer");
      rowsG[r.id] = g;
      g.append("rect").attr("class", "cov-mband").attr("x", 0).attr("y", yy - 1).attr("width", W).attr("height", cellH + 2).attr("rx", 3).attr("fill", "var(--ink)").attr("fill-opacity", 0);
      g.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", yy + cellH / 2 + 4).attr("text-anchor", "end").attr("fill", statusColor(r.status)).text(trunc(r.id, Math.floor((labW - 10) / 6.6))).append("title").text(r.name);
      gens.forEach(function (gg) {
        var c = cs[gg.id], ok = c && c.measurable !== false && isNum(c.point), xx = x(gg.id);
        var z = ok && st.sd > 0 ? Math.max(-2, Math.min(2, sign * (c.point - st.mean) / st.sd)) : 0;
        var before = adoptGen !== null && gg.i < adoptGen;
        var cell = g.append("g").attr("class", "cov-cell").attr("data-metric", r.id).attr("data-gen", gg.id).attr("data-z", ok ? z.toFixed(3) : "").attr("data-hindsight", before ? "1" : "0").attr("data-adoption", adoptGen !== null && gg.i === adoptGen ? "1" : "0");
        cell.append("rect").attr("x", xx).attr("y", yy).attr("width", x.bandwidth()).attr("height", cellH).attr("rx", 2).attr("fill", ok ? color(z) : "none").attr("stroke", ok ? "none" : "var(--rule)").attr("stroke-dasharray", ok ? null : "2 2");
        if (before) cell.append("rect").attr("class", "cov-hatch").attr("x", xx).attr("y", yy).attr("width", x.bandwidth()).attr("height", cellH).attr("rx", 2);
        if (adoptGen !== null && gg.i === adoptGen) cell.append("rect").attr("x", xx + 0.75).attr("y", yy + 0.75).attr("width", x.bandwidth() - 1.5).attr("height", cellH - 1.5).attr("rx", 2).attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-width", 1.5);
        if (x.bandwidth() >= 40 && ok) cell.append("text").attr("class", "tick").attr("x", xx + x.bandwidth() / 2).attr("y", yy + cellH / 2 + 4).attr("text-anchor", "middle").attr("pointer-events", "none").attr("fill", Math.abs(z) >= 1.2 ? "var(--bg)" : "var(--ink-2)").text(num(c.point, 2));
        cell.on("pointermove", function (evt) {
          evt.stopPropagation();
          tip.show(evt, [{ b: true, text: r.id + " · " + gg.id + (adoptGen !== null && gg.i === adoptGen ? " · adopted into the eval here" : before ? " · computed with hindsight" : "") },
            ok ? { mono: true, text: ci(c) + " · n " + (isNum(c.n) ? c.n : "?") + " · z " + signed(z, 2) } : { text: "not measurable: " + (c && c.reason ? c.reason : "no value") },
            { text: "click to open the metric" }]);
        });
      });
      g.on("pointerleave", tip.hide).on("click", function () { tip.hide(); select({ metric: r.id }); }).on("keydown", function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); select({ metric: r.id }); } });
    });
    function apply(animate) {
      var dur = animate && !prefersReduced() ? DUR : 0, cur = currentMetric(m);
      Object.keys(rowsG).forEach(function (id) {
        var b = rowsG[id].select(".cov-mband");
        (dur ? b.transition().duration(dur) : b).attr("fill-opacity", cur && cur.id === id ? 0.06 : 0);
        rowsG[id].attr("aria-current", cur && cur.id === id ? "true" : "false");
      });
    }
    apply(false);
    ref.apply = apply;
  }

  function matrixTable(H, m) {
    var table = H("table", { class: "cov-table" }, [H("thead", null, H("tr", null, ["metric", "status"].concat(m.gens.map(function (g) { return g.id; })).map(function (t) { return H("th", { text: t }); }))),
      H("tbody", null, m.metricList.map(function (r) {
        var cs = m.matrix[r.id] || {};
        return H("tr", { "data-metric": r.id }, [H("td", { text: r.id }), H("td", { text: r.status })].concat(m.gens.map(function (g) { var c = cs[g.id]; return H("td", { class: "num", text: c && c.measurable !== false ? ci(c) + " (n " + c.n + ")" : "—" }); })));
      }))]);
    return H("div", { class: "scroll-x" }, table);
  }

  AgentDiff.block({
    id: "cov-matrix",
    title: "Every metric on every generation",
    question: "The final eval applied to the whole lineage: each metric's value per generation with its interval, the cells before its adoption computed with hindsight.",
    group: "coevolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.metricList.length && Object.keys(m.matrix).length ? 0.85 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "cov cov-matrix" });
      el.appendChild(root);
      var tip = tooltip(root);
      var counts = {};
      m.metricList.forEach(function (r) { counts[r.status] = (counts[r.status] || 0) + 1; });
      root.appendChild(H("p", { class: "cov-lede", "data-metrics": m.metricList.length, text: plural(m.metricList.length, "metric") + " on " + plural(m.gens.length, "generation") + ": " + ["base", "adopted", "demoted", "retired"].filter(function (k) { return counts[k]; }).map(function (k) { return counts[k] + " " + k; }).join(", ") + ". Colour is the row's standardised value, z = (value − row mean) / row sd, sign flipped for a lower-is-better metric, clamped at ±2; hatched cells are before the metric's adoption — computed with hindsight; the outlined cell is the generation its adopting step made." + (m.synthetic ? " SYNTHETIC episodes." : "") }));
      var refs = {};
      var host = H("div", { class: "cov-chart" });
      root.appendChild(responsive(host, function () { host.innerHTML = ""; drawMatrix(host, m, tip, refs); }, "cov-matrix"));
      root.appendChild(H("details", { class: "cov-details" }, [H("summary", { text: "table view: every value with its interval" }), matrixTable(H, m)]));
      root.appendChild(H("p", { class: "cov-note", text: "Every value is a point with a stratified bootstrap interval over that generation's episodes (" + (isNum(m.samples) ? m.samples + " resamples" : "the engine's resamples") + "); the interval and n are in the tooltip and the table. A dashed cell was not measurable (too few episodes after the metric's filter). Click a row to open the metric." }));
      listen(root, function () { if (refs.apply) refs.apply(true); });
    },
  });

  // ============================================================== cov-metric

  /* The metric across the generations: one row per generation, the value on
   * a shared axis with its interval (the library's interval glyph), the
   * points joined; the adoption generation ringed. */
  function drawCurve(host, m, mt, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 480, labW = narrow ? 40 : 56, padR = 14, rowH = narrow ? 18 : 22, padT = 18, padB = 22;
    var gens = m.gens, cs = m.matrix[mt.id] || {};
    var H = padT + gens.length * rowH + padB;
    var lo = Infinity, hi = -Infinity;
    gens.forEach(function (g) { var c = cs[g.id]; if (c && c.measurable !== false) [c.point, c.lo, c.hi].forEach(function (v) { if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); });
    if (!isFinite(lo)) return;
    var rate = mt.spec.agg === "rate" || (mt.spec.feature === "success" && mt.spec.agg !== "iqm");
    if (rate) { lo = Math.min(lo, 0); hi = Math.max(hi, 1); }
    if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
    var x = d3.scaleLinear().domain([lo, hi]).nice().range([labW, W - padR]);
    var svg = L.svg({ viewBox: "0 0 " + W + " " + H, "aria-label": mt.id + " per generation, each a point with its bootstrap interval: " + gens.map(function (g) { var c = cs[g.id]; return g.id + " " + (c && c.measurable !== false ? ci(c) : "not measurable"); }).join(", ") });
    host.appendChild(svg);
    var el = d3.select(svg);
    x.ticks(narrow ? 3 : 5).forEach(function (t) {
      el.append("line").attr("class", t === 0 ? "zero" : "rule").attr("x1", x(t)).attr("x2", x(t)).attr("y1", padT - 6).attr("y2", H - padB + 2).attr("stroke-dasharray", t === 0 ? null : "1 3");
      el.append("text").attr("class", "tick").attr("x", x(t)).attr("y", H - 6).attr("text-anchor", "middle").text(rate ? pct(t) : signed(t, 1));
    });
    var adoptedAt = mt.raw.adopted_at && mt.raw.adopted_at.step ? m.stepByKey[mt.raw.adopted_at.step] : null;
    var pts = [];
    gens.forEach(function (g, i) {
      var c = cs[g.id], y = padT + i * rowH + rowH / 2, ok = c && c.measurable !== false && isNum(c.point);
      el.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", y + 4).attr("text-anchor", "end").text(trunc(g.id, 5));
      var gg = el.append("g").attr("class", "cov-cpt").attr("data-gen", g.id).attr("data-measurable", ok ? "1" : "0");
      if (ok) {
        L.glyph.interval(gg.node(), x, c.point, c.lo, c.hi, { y: y, color: statusColor(mt.status), width: 2.5 });
        if (adoptedAt && adoptedAt.to === g.id) gg.append("circle").attr("cx", x(c.point)).attr("cy", y).attr("r", 7).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.5);
        pts.push([x(c.point), y]);
      } else gg.append("text").attr("class", "tick").attr("x", labW + 4).attr("y", y + 4).text(narrow ? "n/a" : "not measurable: " + (c && c.reason ? c.reason : "no value"));
      gg.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", W).attr("height", rowH).attr("fill", "transparent");
      gg.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: mt.id + " · " + g.id }, ok ? { mono: true, text: ci(c) + " · n " + (isNum(c.n) ? c.n : "?") } : { text: "not measurable: " + (c && c.reason ? c.reason : "no value") }, adoptedAt && adoptedAt.to === g.id ? { text: "adopted at " + adoptedAt.key } : null]); }).on("pointerleave", tip.hide);
    });
    if (pts.length > 1) el.insert("path", ":first-child").attr("fill", "none").attr("stroke", statusColor(mt.status)).attr("stroke-opacity", 0.5).attr("stroke-width", 1.2).attr("d", d3.line()(pts));
  }

  /* The per-task cells of a metric, the union over the generations of the
   * matrix cells' `per_task`: {tasks (sorted), cells: {task: {gen: cell}},
   * total, unmeasurable, reasons: {reason: n}, note, lo, hi, rate} — the axis
   * spans the measurable cells (a rate spans 0..1). null when the matrix
   * carries no per_task (an output from before it was recorded). */
  function perTask(m, mt) {
    var cs = m.matrix[mt.id] || {}, cells = {}, tasks = [], any = false, total = 0, un = 0, reasons = {}, note = null, lo = Infinity, hi = -Infinity;
    m.gens.forEach(function (g) {
      var c = cs[g.id], pt = c && c.per_task;
      if (!pt || typeof pt !== "object") return;
      any = true;
      Object.keys(pt).forEach(function (t) {
        var cell = pt[t];
        if (!cell || typeof cell !== "object") return;
        if (!cells[t]) { cells[t] = {}; tasks.push(t); }
        cells[t][g.id] = cell;
        total++;
        if (cell.note && !note) note = String(cell.note);
        if (cell.measurable === false || !isNum(cell.point)) { un++; var r = cell.reason ? String(cell.reason) : "no value"; reasons[r] = (reasons[r] || 0) + 1; }
        else [cell.point, cell.lo, cell.hi].forEach(function (v) { if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
      });
    });
    if (!any) return null;
    tasks.sort();
    var rate = mt.spec.agg === "rate" || (mt.spec.feature === "success" && mt.spec.agg !== "iqm");
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (rate) { lo = Math.min(lo, 0); hi = Math.max(hi, 1); }
    if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
    return { tasks: tasks, cells: cells, total: total, unmeasurable: un, reasons: reasons, note: note, lo: lo, hi: hi, rate: rate };
  }
  //: the small multiples draw this many panels at most; the rest are in the table view
  var MULTI_CAP = 24;
  //: the panels the grid draws: the first MULTI_CAP tasks in sorted order
  function multiShown(pt) { return pt.tasks.slice(0, MULTI_CAP); }
  //: the forgot marks a metric's panels carry: one per (task, step) the Evolution section names, among the tasks drawn
  function forgotMarks(m, pt) { return multiShown(pt).reduce(function (n, t) { return n + (m.forgotten[t] || []).length; }, 0); }

  /* The per-task small multiples: one panel per task, the same drawing as
   * the curve — a row per generation, the value across, the library's
   * interval glyph — on one shared axis so the panels compare; the
   * adoption generation ringed; a generation that a `forgot` step of the
   * Evolution section named this task at is banded in the warn colour with
   * the ▏ glyph; an unmeasurable cell is drawn as absent (a dash), its
   * reason in the tooltip. Panels are laid out in as many columns as the
   * width holds at 220px each, every panel at that width. */
  function drawMultiples(host, m, mt, pt, tip) {
    if (!d3) return;
    var W = width(host), gap = 16, shown = multiShown(pt);
    var cols = Math.max(1, Math.min(shown.length, Math.floor((W + gap) / (220 + gap)))), pw = Math.floor((W - (cols - 1) * gap) / cols);
    var gens = m.gens, rowH = 14, padT = 18, padB = 16, labW = 32, padR = 10, Hh = padT + gens.length * rowH + padB;
    var x = d3.scaleLinear().domain([pt.lo, pt.hi]).nice().range([labW, pw - padR]);
    var adoptedAt = mt.raw.adopted_at && mt.raw.adopted_at.step ? m.stepByKey[mt.raw.adopted_at.step] : null;
    var stroke = statusColor(mt.status);
    var grid = document.createElement("div");
    grid.className = "cov-multi";
    grid.style.gridTemplateColumns = "repeat(" + cols + ", minmax(0, 1fr))";
    host.appendChild(grid);
    var fitChars = Math.max(6, Math.floor((pw - labW - 4) / 6.6));
    shown.forEach(function (t) {
      var cells = pt.cells[t] || {}, forgot = m.forgotten[t] || [];
      var said = gens.map(function (g) { var c = cells[g.id]; return g.id + " " + (c && c.measurable !== false && isNum(c.point) ? ci(c) : "not measurable"); }).join(", ");
      var label = mt.id + " on " + t + " per generation, each a point with its bootstrap interval within the task: " + said + (forgot.length ? "; forgotten at " + forgot.map(function (f) { return f.key; }).join(", ") : "") + (adoptedAt ? "; adopted at " + adoptedAt.to : "");
      var svg = L.svg({ viewBox: "0 0 " + pw + " " + Hh, class: "cov-task", "data-task": t, "aria-label": label });
      grid.appendChild(svg);
      var el = d3.select(svg);
      el.append("text").attr("class", "lab strong").attr("x", labW).attr("y", 11).text(trunc(short(t), fitChars)).append("title").text(t);
      x.ticks(3).forEach(function (tk) {
        el.append("line").attr("class", tk === 0 ? "zero" : "rule").attr("x1", x(tk)).attr("x2", x(tk)).attr("y1", padT - 4).attr("y2", Hh - padB + 2).attr("stroke-dasharray", tk === 0 ? null : "1 3");
        // an edge tick's label is anchored inward so it never leaves the panel
        el.append("text").attr("class", "tick").attr("x", x(tk)).attr("y", Hh - 4).attr("text-anchor", x(tk) > pw - 22 ? "end" : x(tk) < labW + 12 ? "start" : "middle").text(pt.rate ? pct(tk) : signed(tk, 1));
      });
      var pts = [];
      gens.forEach(function (g, i) {
        var c = cells[g.id], y = padT + i * rowH + rowH / 2, ok = c && c.measurable !== false && isNum(c.point);
        var marks = forgot.filter(function (f) { return f.to === g.id; });
        var gg = el.append("g").attr("class", "cov-tcell").attr("data-gen", g.id).attr("data-measurable", ok ? "1" : "0");
        if (marks.length) {
          gg.append("rect").attr("x", labW - 2).attr("y", y - rowH / 2).attr("width", pw - labW - padR + 4).attr("height", rowH).attr("fill", "var(--warn)").attr("fill-opacity", 0.14);
          gg.append("text").attr("class", "forgot").attr("data-step", marks[0].key).attr("x", 2).attr("y", y + 4).attr("text-anchor", "start").text("▏");
        }
        gg.append("text").attr("class", "tick").attr("x", labW - 4).attr("y", y + 4).attr("text-anchor", "end").text(trunc(g.id, 3));
        if (ok) {
          L.glyph.interval(gg.node(), x, c.point, c.lo, c.hi, { y: y, color: stroke, width: 1.6, tick: 3, r: 2.6 });
          if (adoptedAt && adoptedAt.to === g.id) gg.append("circle").attr("class", "ring").attr("cx", x(c.point)).attr("cy", y).attr("r", 5.5).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.2);
          pts.push([x(c.point), y]);
        } else gg.append("text").attr("class", "absent").attr("x", labW + 2).attr("y", y + 4).text("—");
        gg.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", pw).attr("height", rowH).attr("fill", "transparent");
        gg.on("pointermove", function (evt) {
          tip.show(evt, [{ b: true, text: mt.id + " · " + t + " · " + g.id },
            ok ? { mono: true, text: ci(c) + " · n " + (isNum(c.n) ? c.n : "?") + " · bootstrap within the task" } : { text: "not measurable: " + (c && c.reason ? c.reason : "no value") },
            c && c.note ? { text: cap(String(c.note)) } : null,
            adoptedAt && adoptedAt.to === g.id ? { text: "adopted at " + adoptedAt.key } : null,
            marks.length ? { text: "▏ forgotten at " + marks.map(function (f) { return f.key; }).join(", ") + " — the Evolution section's forgot verdict names this task" } : null]);
        }).on("pointerleave", tip.hide);
      });
      if (pts.length > 1) el.insert("path", ":first-child").attr("fill", "none").attr("stroke", stroke).attr("stroke-opacity", 0.45).attr("stroke-width", 1).attr("d", d3.line()(pts));
    });
  }
  //: the table view of the multiples: a row per task, a column per generation, every cell its interval or its reason
  function multiTable(H, m, mt, pt) {
    var tbl = H("table", { class: "cov-table", "data-role": "per-task-table" });
    tbl.appendChild(H("thead", null, H("tr", null, [H("th", { text: "task" })].concat(m.gens.map(function (g) { return H("th", { text: g.id }); })))));
    var body = H("tbody");
    pt.tasks.forEach(function (t) {
      var cells = pt.cells[t] || {}, forgot = m.forgotten[t] || [];
      body.appendChild(H("tr", { "data-task": t }, [H("td", { class: "wrap", text: t + (forgot.length ? " · ▏ forgotten at " + forgot.map(function (f) { return f.key; }).join(", ") : "") })].concat(m.gens.map(function (g) {
        var c = cells[g.id], ok = c && c.measurable !== false && isNum(c.point);
        return H("td", { class: ok ? "num" : "wrap", text: ok ? ci(c) + " · n " + (isNum(c.n) ? c.n : "?") : c ? "not measurable: " + (c.reason || "no value") : "—" });
      }))));
    });
    tbl.appendChild(body);
    return H("div", { class: "scroll-x" }, tbl);
  }
  //: what the status line under the multiples says: the counts, the unmeasurable cells with their reasons, the note, the cap
  function multiStatus(m, mt, pt) {
    var reasons = Object.keys(pt.reasons).sort(function (a, b) { return pt.reasons[b] - pt.reasons[a] || (a < b ? -1 : 1); });
    var why = reasons.slice(0, 3).map(function (r) { return r + (pt.reasons[r] > 1 ? " ×" + pt.reasons[r] : ""); }).join("; ") + (reasons.length > 3 ? "; and " + plural(reasons.length - 3, "other reason") + " in the table" : "");
    var marks = forgotMarks(m, pt);
    return plural(pt.tasks.length, "task") + " × " + plural(m.gens.length, "generation") + ": " + plural(pt.total, "cell") + ", " + (pt.unmeasurable ? pt.unmeasurable + " not measurable, drawn as absent (" + why + ")" : "every one measurable")
      + (marks ? "; " + plural(marks, "forgot mark") + " from the Evolution section's steps" : "; no step forgot a task")
      + (pt.note ? ". " + cap(pt.note) : "") + (pt.tasks.length > MULTI_CAP ? ". The first " + MULTI_CAP + " tasks are drawn; every task is in the table" : "")
      + ". Each interval is a percentile bootstrap within the task over its own runs" + (isNum(m.samples) ? " (" + m.samples + " draws)" : "") + ", on one shared axis.";
  }

  AgentDiff.block({
    id: "cov-metric",
    title: "One metric, in full",
    question: "For the selected metric: what it measures, how it moved across the generations with its intervals, how it was validated, whether it was confirmed, and where it came from.",
    group: "coevolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.metricList.length ? 0.8 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "cov cov-metric" });
      el.appendChild(root);
      var tip = tooltip(root);
      var body = H("div");
      root.appendChild(body);
      function paint() {
        body.innerHTML = "";
        var mt = currentMetric(m);
        if (!mt) { body.appendChild(H("p", { class: "cov-read", text: "No metric to show." })); return; }
        var raw = mt.raw, origin = raw.origin || {}, cf = raw.confirmation, ca = raw.caught_at;
        var nav = H("div", { class: "cov-bar", role: "group", "aria-label": "the metric in view" });
        m.metricList.forEach(function (o) { nav.appendChild(H("button", { type: "button", class: "cov-btn", "data-metric": o.id, "aria-pressed": o.id === mt.id ? "true" : "false", text: o.id, title: o.name + " · " + o.status, onclick: function () { select({ metric: o.id }); } })); });
        body.appendChild(nav);
        body.appendChild(H("p", { class: "cov-lede", "data-metric": mt.id, "data-status": mt.status }, [H("b", { text: mt.id }), H("span", { text: " · " + mt.name + " — " + specSentence(mt.spec) + ". Status " }), H("span", { class: "cov-dec " + mt.status, style: { color: statusColor(mt.status) }, text: mt.status }), H("span", { text: origin.probe === "base" || !origin.probe ? ": a base metric, in the eval from e0, never retired or demoted." : ": proposed by the " + origin.probe + " probe at " + (origin.step || "?") + " while " + (origin.eval_gen || "?") + " was in force" + (raw.adopted_at ? ", adopted into " + raw.adopted_at.eval_gen : "") + (raw.retired_at ? ", retired at " + raw.retired_at.step + " — " + raw.retired_at.reason : "") + (raw.demoted_at ? ", demoted at " + (raw.demoted_at.step || "?") : "") + "." })]));
        var cols = H("div", { class: "cov-cols" });
        body.appendChild(cols);
        var left = H("div");
        cols.appendChild(left);
        left.appendChild(H("div", { class: "cov-h", text: "across the generations, with intervals" }));
        var host = H("div", { class: "cov-chart" });
        left.appendChild(responsive(host, function () { host.innerHTML = ""; drawCurve(host, m, mt, tip); }, "cov-curve"));
        var pt = perTask(m, mt);
        if (!pt) left.appendChild(H("p", { class: "cov-note", "data-role": "per-task", "data-tasks": "0", style: { marginTop: "4px" }, text: "Per-task small multiples are not drawn: this matrix carries no per_task cells (an output from before they were recorded), so the per-task view is not in the data." }));
        var right = H("div");
        cols.appendChild(right);
        var val = raw.validation;
        right.appendChild(H("div", { class: "cov-h", text: "validation" }));
        if (val && val.validators) {
          right.appendChild(H("p", { class: "cov-read", "data-role": "validation" }, [marksSpan(H, val, tip), H("span", { text: " at " + (val.step || "?") + ", K " + (isNum(val.k) ? val.k : "?") + ", α " + alpha(val.alpha) + " — " + (val.reason || "") + "." })]));
          right.appendChild(H("button", { type: "button", class: "cov-btn", "data-role": "open-candidate", text: "open the ledger row", onclick: function () { if (isNum(val.index)) select({ candidate: val.index, step: val.step, evalGen: null }); } }));
        } else right.appendChild(H("p", { class: "cov-read", "data-role": "validation", text: mt.status === "base" ? "A base metric is not validated: it is the shipped reading itself." : "No validation row was recorded." }));
        right.appendChild(H("div", { class: "cov-h", text: "confirmation" }));
        right.appendChild(H("p", { class: "cov-read", "data-role": "confirmation", "data-status": cf ? cf.status : "", text: cf ? cap(cf.status) + ": tested on " + plural(cf.tested, "later step") + ", moved on " + cf.moved + (cf.status === "unconfirmed" ? " — it never moved again after its adoption, which is a statement after " + m.thresholds.confirm_steps + " tests, not an absence" : cf.status === "pending" ? " — fewer than " + m.thresholds.confirm_steps + " later steps yet" : "") + "." : "Not applicable: a base metric is never tested out of sample." }));
        if (ca) { right.appendChild(H("div", { class: "cov-h", text: "hindsight" })); right.appendChild(H("p", { class: "cov-read", "data-role": "caught", text: cap(ca.note) + (isNum(ca.lag) ? " (first flag " + ca.first_flag_step + ", adopted " + ca.adopted_step + ", lag " + plural(ca.lag, "step") + ")" : "") + "." })); }
        if (pt) {
          body.appendChild(H("div", { class: "cov-h", text: "per task, with intervals" }));
          var status = H("p", { class: "cov-status", "data-role": "per-task", "data-tasks": String(pt.tasks.length), "data-cells": String(pt.total), "data-unmeasurable": String(pt.unmeasurable), "data-forgotten": String(forgotMarks(m, pt)), "data-note": pt.note ? "1" : "0", text: multiStatus(m, mt, pt) });
          body.appendChild(status);
          var mhost = H("div", { class: "cov-chart cov-multi-host" });
          body.appendChild(responsive(mhost, function () {
            var t0 = global.performance ? performance.now() : Date.now();
            mhost.innerHTML = "";
            drawMultiples(mhost, m, mt, pt, tip);
            status.setAttribute("data-draw-ms", ((global.performance ? performance.now() : Date.now()) - t0).toFixed(1));
          }, "cov-multi"));
          body.appendChild(H("details", { class: "cov-details" }, [H("summary", { text: "table view: every task by generation" }), multiTable(H, m, mt, pt)]));
        }
      }
      paint();
      root.appendChild(H("p", { class: "cov-note", text: "The curve is the final eval's value of the metric on every generation — a point with its stratified bootstrap interval, the adoption generation ringed. The per-task panels are the same cells read within each task (matrix[metric][generation].per_task): the task's own mean, or rate, with a bootstrap over that task's runs — no draw shared with the generation's interval; a ▏ band is the generation a forgot step of the Evolution section named that task at; a dash is a cell under the episodes needed. The validation row is the ledger row that adopted it (c computable · i informative · d distinct · l linked · n not already); confirmation is the out-of-sample test at every later step." }));
      listen(root, function () { paint(); });
    },
  });

  // ============================================================== cov-probes

  function drawProbeBars(host, m, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 480, labW = narrow ? 84 : 110, padR = 12, rowH = 20, padT = 16, padB = 4;
    var probes = m.probes, H = padT + probes.length * rowH + padB;
    var top = probes.reduce(function (a, p) { return Math.max(a, isNum(p.proposed) ? p.proposed : 0); }, 1);
    var x = d3.scaleLinear().domain([0, top]).range([labW, W - padR - 40]);
    var svg = L.svg({ viewBox: "0 0 " + W + " " + H, "aria-label": "per probe, candidates proposed and adopted: " + probes.map(function (p) { return p.name + " " + (p.proposed || 0) + " proposed, " + (p.adopted || 0) + " adopted, fired " + plural((p.fired || []).length, "time"); }).join("; ") });
    host.appendChild(svg);
    var el = d3.select(svg);
    el.append("text").attr("class", "lab dim").attr("x", labW).attr("y", 10).text("proposed (grey) · adopted (accent)");
    probes.forEach(function (p, i) {
      var y = padT + i * rowH + 3, fired = (p.fired || []).length;
      var g = el.append("g").attr("class", "cov-probe").attr("data-probe", p.name).attr("data-fired", fired).attr("data-proposed", p.proposed || 0).attr("data-adopted", p.adopted || 0);
      g.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", y + 11).attr("text-anchor", "end").attr("fill", fired ? "var(--ink-2)" : "var(--ink-3)").text(p.name);
      g.append("rect").attr("x", labW).attr("y", y).attr("width", Math.max(0, x(p.proposed || 0) - labW)).attr("height", 14).attr("rx", 2).attr("fill", "var(--ink-3)").attr("fill-opacity", 0.35);
      g.append("rect").attr("x", labW).attr("y", y).attr("width", Math.max(0, x(p.adopted || 0) - labW)).attr("height", 14).attr("rx", 2).attr("fill", "var(--accent)");
      g.append("text").attr("class", "tick").attr("x", x(p.proposed || 0) + 6).attr("y", y + 11).text((p.adopted || 0) + " / " + (p.proposed || 0) + (fired ? "" : " · never fired"));
      g.append("rect").attr("x", 0).attr("y", y - 3).attr("width", W).attr("height", rowH).attr("fill", "transparent");
      g.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: p.name }, { text: p.question || "" }, { text: fired ? "fired at " + (p.fired || []).join(", ") + " · proposed " + (p.proposed || 0) + ", adopted " + (p.adopted || 0) : "never fired: its trigger never held on this lineage" }]); }).on("pointerleave", tip.hide);
    });
  }

  AgentDiff.block({
    id: "cov-probes",
    title: "The probes",
    question: "The eval's agents: what question each asks, when it fired, how much of what it proposed survived the validators — and what the external proposer sent.",
    group: "coevolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.probes.length ? 0.75 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "cov cov-probes" });
      el.appendChild(root);
      var tip = tooltip(root);
      var fired = m.probesFired.length, total = m.probes.reduce(function (a, p) { return a + (p.proposed || 0); }, 0), adopted = m.probes.reduce(function (a, p) { return a + (p.adopted || 0); }, 0);
      root.appendChild(H("p", { class: "cov-lede", "data-fired": fired, text: fired + " of " + plural(m.probes.length, "probe") + " fired on this lineage, proposing " + plural(total, "candidate") + " of which " + adopted + " " + (adopted === 1 ? "was" : "were") + " adopted. Each probe is one question, run at every agent step with what was known up to that step and no lookahead." }));
      var host = H("div", { class: "cov-chart" });
      root.appendChild(responsive(host, function () { host.innerHTML = ""; drawProbeBars(host, m, tip); }, "cov-probes"));
      var ul = H("ul", { class: "cov-list", "data-role": "questions", style: { marginTop: "8px" } });
      m.probes.forEach(function (p) {
        ul.appendChild(H("li", { "data-probe": p.name }, [H("span", { class: "mono", text: p.name }), H("span", { text: " — " + (p.question || "") + " · " + ((p.fired || []).length ? "fired at " + p.fired.join(", ") : "never fired") })]));
      });
      root.appendChild(ul);
      var ex = m.integrity.external || {};
      root.appendChild(H("div", { class: "cov-h", text: "the external proposer" }));
      var srcs = Array.isArray(ex.sources) ? ex.sources : [];
      root.appendChild(H("p", { class: "cov-read", "data-role": "external", "data-received": isNum(ex.received) ? ex.received : 0, text: !isNum(ex.received) || ex.received === 0 ? "None: no external candidate was supplied. An external proposer may propose candidates in the same metric language; they go through the same validators and can never set a number, a verdict or an exit code — validated, never trusted." : "Sources " + (srcs.length ? srcs.join(", ") : "unnamed") + ": " + plural(ex.received, "candidate") + " received, " + (isNum(ex.parsed) ? ex.parsed : "?") + " parsed, " + (isNum(ex.adopted) ? ex.adopted : "?") + " adopted" + (isNum(ex.rejected) ? ", " + ex.rejected + " rejected" : "") + " — validated, never trusted: an external candidate goes through the same validators and can never set a number, a verdict or an exit code." }));
      root.appendChild(H("p", { class: "cov-note", text: "The bar is what a probe proposed across the lineage (grey) and what survived every validator (accent); a probe with a trigger that never held proposed nothing and says so. The redundancy and goodhart probes act on adopted metrics — retiring or demoting — rather than proposing, so their bars can be empty while they fired." }));
    },
  });

  // =========================================================== cov-integrity

  function drawSizes(host, m, tip) {
    if (!d3) return;
    var dr = m.integrity.drift || {}, sizes = Array.isArray(dr.size_by_eval_gen) ? dr.size_by_eval_gen : [];
    if (!sizes.length) return;
    var W = width(host), H = 92, padL = 28, padR = 10, padT = 18, padB = 20;
    var ids = m.evalGens.map(function (e) { return e.id; });
    while (ids.length < sizes.length) ids.push("e" + ids.length);
    var x = d3.scalePoint().domain(ids.slice(0, sizes.length)).range([padL + 8, W - padR - 8]);
    var y = d3.scaleLinear().domain([0, d3.max(sizes) || 1]).nice().range([H - padB, padT]);
    var svg = L.svg({ viewBox: "0 0 " + W + " " + H, "aria-label": "active metrics per eval generation: " + sizes.map(function (s, i) { return ids[i] + " " + s; }).join(", ") + "; Jaccard distance from the base " + num(dr.jaccard_distance_from_base) });
    host.appendChild(svg);
    var el = d3.select(svg);
    el.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 10).text("active metrics by eval generation");
    y.ticks(2).forEach(function (t) { el.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(t) + 4).attr("text-anchor", "end").text(t); el.append("line").attr("class", "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(t)).attr("y2", y(t)).attr("stroke-dasharray", "1 3"); });
    var pts = sizes.map(function (s, i) { return [x(ids[i]), y(s)]; });
    el.append("path").attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-width", 1.6).attr("d", d3.line()(pts));
    sizes.forEach(function (s, i) {
      el.append("circle").attr("cx", x(ids[i])).attr("cy", y(s)).attr("r", 3.5).attr("fill", "var(--accent)");
      el.append("text").attr("class", "tick").attr("x", x(ids[i])).attr("y", H - 6).attr("text-anchor", "middle").text(ids[i]);
    });
  }

  AgentDiff.block({
    id: "cov-integrity",
    title: "Is the eval sound?",
    question: "How far the eval has drifted from the base, how many candidates it tested against how strict a level, what it demoted, retired or never confirmed — and what it cannot see.",
    group: "coevolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && Object.keys(m.integrity).length ? 0.7 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "cov cov-integrity" });
      el.appendChild(root);
      var tip = tooltip(root);
      var ig = m.integrity, dr = ig.drift || {}, mu = ig.multiplicity || {};
      var demoted = Array.isArray(ig.demoted) ? ig.demoted : [], retired = Array.isArray(ig.retired) ? ig.retired : [], unconfirmed = Array.isArray(ig.unconfirmed) ? ig.unconfirmed : [];
      root.appendChild(H("p", { class: "cov-lede", "data-tested": isNum(mu.tested) ? mu.tested : 0, "data-adopted": isNum(mu.adopted) ? mu.adopted : 0 }, [
        H("b", { text: "Drift from the base " + num(dr.jaccard_distance_from_base) + "; " + plural(isNum(mu.tested) ? mu.tested : 0, "candidate") + " tested, " + (isNum(mu.adopted) ? mu.adopted : 0) + " adopted, the smallest adjusted level α " + alpha(mu.min_adjusted_alpha) + ". " }),
        H("span", { text: plural(demoted.length, "metric") + " demoted, " + retired.length + " retired, " + unconfirmed.length + " unconfirmed." })]));
      var cols = H("div", { class: "cov-cols" });
      root.appendChild(cols);
      var left = H("div");
      cols.appendChild(left);
      left.appendChild(H("div", { class: "cov-h", text: "drift" }));
      var host = H("div", { class: "cov-chart" });
      left.appendChild(responsive(host, function () { host.innerHTML = ""; drawSizes(host, m, tip); }, "cov-sizes"));
      left.appendChild(H("p", { class: "cov-read", "data-role": "drift", text: "Jaccard distance " + num(dr.jaccard_distance_from_base) + (dr.basis ? " — " + dr.basis : "") + "." }));
      var right = H("div");
      cols.appendChild(right);
      right.appendChild(H("div", { class: "cov-h", text: "multiplicity" }));
      var tested = isNum(mu.tested) ? mu.tested : 0, ad = isNum(mu.adopted) ? mu.adopted : 0;
      var mh = H("div", { class: "cov-chart" });
      right.appendChild(responsive(mh, function () {
        mh.innerHTML = "";
        if (!d3) return;
        var W = width(mh), Hh = 34, padL = 8, padR = 8;
        var x = d3.scaleLinear().domain([0, Math.max(1, tested)]).range([padL, W - padR]);
        var svg = L.svg({ viewBox: "0 0 " + W + " " + Hh, "aria-label": tested + " candidates tested, " + ad + " adopted, " + (isNum(mu.rejected) ? mu.rejected : tested - ad) + " rejected" + (isNum(mu.unparseable) ? ", " + mu.unparseable + " unparseable" : "") });
        mh.appendChild(svg);
        var e = d3.select(svg);
        e.append("rect").attr("x", x(0)).attr("y", 6).attr("width", x(tested) - x(0)).attr("height", 14).attr("rx", 3).attr("fill", "var(--ink-3)").attr("fill-opacity", 0.3);
        e.append("rect").attr("x", x(0)).attr("y", 6).attr("width", Math.max(0, x(ad) - x(0))).attr("height", 14).attr("rx", 3).attr("fill", "var(--accent)");
        e.append("text").attr("class", "tick").attr("x", padL).attr("y", Hh - 4).text(ad + " adopted of " + tested + " tested");
      }, "cov-multi"));
      right.appendChild(H("p", { class: "cov-read", "data-role": "multiplicity", text: "α " + alpha(mu.alpha) + " family-wise, divided by the candidates tested at a step; the strictest level any candidate faced was " + alpha(mu.min_adjusted_alpha) + (mu.basis ? " — " + mu.basis : "") + "." }));
      var lists = H("div", { class: "cov-cols" });
      root.appendChild(lists);
      [["demoted", demoted, "a metric the agent moved twice while the pass rate did not: still computed, excluded from the evolved verdict"], ["retired", retired, "one reading with an older metric (|ρ| at or over " + num(m.thresholds.redundant_rho, 2) + ")"], ["unconfirmed", unconfirmed, "adopted, then never moved again on " + (m.thresholds.confirm_steps || 2) + " or more later steps"]].forEach(function (p) {
        var box = H("div", { "data-role": p[0] });
        box.appendChild(H("div", { class: "cov-h", text: p[0] + " · " + p[1].length }));
        if (!p[1].length) box.appendChild(H("p", { class: "cov-read", style: { margin: 0 }, text: "None — " + p[2] + "." }));
        else {
          var ul = H("ul", { class: "cov-list" });
          p[1].forEach(function (id) {
            var mt = m.metricById[id], why = p[0] === "retired" && mt && mt.raw.retired_at ? mt.raw.retired_at.reason : p[0] === "unconfirmed" && mt && mt.raw.confirmation ? "moved on " + mt.raw.confirmation.moved + " of " + plural(mt.raw.confirmation.tested, "later step") : p[2];
            ul.appendChild(H("li", { "data-metric": id }, [H("button", { type: "button", class: "cov-btn", text: id, onclick: function () { select({ metric: id }); } }), H("span", { text: " — " + why })]));
          });
          box.appendChild(ul);
        }
        lists.appendChild(box);
      });
      root.appendChild(H("div", { class: "cov-h", text: "the gap" }));
      root.appendChild(H("p", { class: "cov-read", "data-role": "gap", text: String(ig.gap || "the engine states no gap") }));
      if (m.recommended) root.appendChild(H("p", { class: "cov-read", "data-role": "recommended", "data-agree": m.recommended.agree ? "1" : "0" }, [H("b", { text: "Recommended: base " + m.recommended.base + ", evolved " + m.recommended.evolved + (m.recommended.agree ? " — they agree" : " — they differ") + ". " }), H("span", { text: cap(String(m.recommended.why || "")) + "." })]));
      root.appendChild(H("p", { class: "cov-note", text: "The eval's own integrity is measured the way the agent's is: its drift from the base as the Jaccard distance between the base metric set and the final active set; multiplicity as the candidates tested against the Bonferroni level each faced; and the metrics it demoted (learned by the agent), retired (redundant) and never confirmed. The gap sentence is the engine's, verbatim." }));
    },
  });
})(typeof window !== "undefined" ? window : this);
