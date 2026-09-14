/* AgentDiff blocks — the Data view: the inputs side of a run and the chain
 * from them to the answer, read from `report.data` (deepcompare/data.py)
 * and, for a lineage, `aggregate.data_evolution`.
 *
 *   dt-task        what both agents were told: the task prompt and the
 *                  expected answer as recorded, each side's instructions
 *                  side by side with the hunks of their difference (or the
 *                  plain fact that none are recorded, and what is), the
 *                  models as the traces record them with the source of that
 *                  attribution, the tools declared and used.
 *   dt-corpus      what each agent read: the sources as a set table (shared
 *                  rows joined, only-A and only-B rows after), output sizes
 *                  as bars, errors and repeats marked, Jaccard stated; a
 *                  source opens to its input and output text from the
 *                  report's step, capped at TEXT_CAP.
 *   dt-provenance  what each answer rests on: the answer text with its
 *                  typed values marked by the fetched source that carries
 *                  them (a colour per source), the unsupported ones marked,
 *                  the counts stated, and the pair's own answer_eval beside
 *                  it so that grounded is never read as correct.
 *   dt-chain       data → model → agent → answer for one run: lanes of data
 *                  nodes sized by output chars, model nodes with the
 *                  recorded model and its tokens, the agent and the answer;
 *                  produces / feeds / reaches edges with the overlap in the
 *                  tooltip; a node opens the step's text.
 *   dt-evolution   for a lineage: one row per step — the triggering
 *                  episodes and the data they read, the prompt hunks, the
 *                  behaviour shift as before → after pairs, the effect with
 *                  its intervals, the evolved eval's flags — each row
 *                  opening to the full diff and the episodes' sources.
 *
 * Every number is the section's: two containment measures (the provenance
 * overlap over typed values, the chain overlap over tokens), counts and
 * sums; nothing is inferred here beyond them and their limits are said on
 * the page. A trace's recorded model name is shown as recorded. SYNTHETIC
 * is carried through. The selection (a source, a step and its side, a
 * lineage step) is one page-scoped family, `data`, exposed as
 * `AgentDiff.data.{select, state, reset, timing, tile}`.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || !AgentDiff.lib) return;
  var L = AgentDiff.lib, isNum = L.fmt.isNum, num = L.fmt.num, pct = L.fmt.pct, signed = L.fmt.signed;
  var d3 = global.d3;
  if (!d3) return;

  //: the section's cap on a step's text shown on the page; the full text is in the trace
  var TEXT_CAP = 4000;
  //: the corpus table draws this many rows and the chain this many nodes before folding the rest
  var ROW_CAP = 400, NODE_CAP = 400;
  //: how many sources a lineage row lists per episode before folding
  var EP_SOURCES = 40;
  //: the transition when the selection changes; none on first paint, none under reduced motion
  var DUR = 220;
  var VERDICT_GLYPH = { improved: "▲", regressed: "▼", flat: "─", gamed: "⚠", overfit: "◇", forgot: "✕", traded: "⇄", unmeasurable: "·" };
  var SOURCE_PALETTE = d3.schemeTableau10;
  var UID = 0;

  var CSS = [
    ".dt{position:relative}",
    ".dt svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
    ".dt text{font-size:var(--fs-xs)}",
    ".dt .lab{fill:var(--ink-2)}.dt .lab.dim{fill:var(--ink-3)}.dt .lab.mono{font-family:var(--mono)}.dt .lab.strong{fill:var(--ink);font-weight:600}",
    ".dt .tick{fill:var(--ink-3);font-variant-numeric:tabular-nums;font-family:var(--mono)}",
    ".dt .rule{stroke:var(--rule)}.dt .zero{stroke:var(--rule-2)}",
    ".dt .backed{paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
    ".dt-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 6px;max-width:100ch;line-height:1.45}.dt-lede b{font-weight:600}",
    ".dt-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px;min-width:0}",
    ".dt-bar i{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:5px}",
    ".dt-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;max-width:100%;overflow-wrap:anywhere}.dt-chip b{color:var(--ink);font-weight:600}",
    ".dt-syn{font-family:var(--mono);font-size:var(--fs-xs);color:var(--warn);font-weight:600;letter-spacing:.04em}",
    ".dt-btn{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".dt-btn:hover{color:var(--ink)}.dt-btn[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
    ".dt-btn:focus-visible,.dt-src:focus-visible,.dt-node:focus-visible,.dt-erow:focus-visible,.dt-stage:focus-visible,.dt-leg button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".dt-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:100ch;line-height:1.5}",
    ".dt-read{font-size:var(--fs-xs);color:var(--ink-2);margin:6px 0 0;line-height:1.5;max-width:100ch}.dt-read b{color:var(--ink);font-weight:600}",
    ".dt-h{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);margin:12px 0 4px;letter-spacing:.04em;text-transform:uppercase}",
    ".dt-cannot{font-size:var(--fs-xs);color:var(--ink-2);background:var(--surface-2);border-radius:7px;padding:6px 10px;margin:4px 0 8px;line-height:1.5;max-width:110ch}",
    ".dt-status{font-size:var(--fs-xs);color:var(--ink-2);margin:0 0 6px;line-height:1.5;max-width:110ch;font-variant-numeric:tabular-nums}",
    ".dt-cols{display:flex;gap:14px 24px;flex-wrap:wrap;align-items:flex-start}.dt-cols>*{flex:1 1 300px;min-width:0}",
    ".dt-side{font-weight:600;color:var(--ink)}.dt-side.a{color:var(--a)}.dt-side.b{color:var(--b)}",
    // text as recorded: the prompt, an instruction set, a step's input and output
    ".dt-text{font-family:var(--mono);font-size:var(--fs-xs);line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink-2);margin:0;padding:6px 9px;background:var(--surface-2);border-radius:6px;max-height:340px;overflow:auto}",
    ".dt-text.prompt{color:var(--ink);background:transparent;padding:0;max-height:none}",
    // the diff, in the Evolution view's vocabulary (evo-step): a pre, one span per line
    ".dt-diff{font-family:var(--mono);font-size:var(--fs-xs);line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink-2);margin:0;max-height:420px;overflow:auto}",
    ".dt-diff .add{color:var(--good)}.dt-diff .del{color:var(--bad)}.dt-diff .hunk{color:var(--ink-3)}.dt-diff .ctx{color:var(--ink-3)}",
    ".dt-list{list-style:none;margin:0;padding:0;font-size:var(--fs-xs);font-family:var(--mono);color:var(--ink-2);line-height:1.5;overflow-wrap:anywhere}",
    ".dt-list .add{color:var(--good)}.dt-list .del{color:var(--bad)}.dt-list .chg{color:var(--ink-2)}.dt-list .quiet{color:var(--ink-3);font-family:var(--sans)}",
    ".dt-prot{font-family:var(--mono);color:var(--bad);margin-left:6px}",
    // tables
    ".dt-table{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:4px}",
    ".dt-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
    ".dt-table td{padding:3px 10px 3px 0;color:var(--ink-2);white-space:nowrap;vertical-align:top}.dt-table td.num,.dt-table th.num{text-align:right;font-family:var(--mono)}",
    ".dt-table td.wrap{white-space:normal;min-width:14em;max-width:40em;overflow-wrap:anywhere}",
    ".dt-table td.bad{color:var(--bad)}.dt-table td.good{color:var(--good)}.dt-table td.dim{color:var(--ink-3)}.dt-table td b{color:var(--ink);font-weight:600}.dt-table td.mono{font-family:var(--mono)}",
    ".dt-details{margin-top:8px}.dt-details summary{cursor:pointer;color:var(--ink-2);font-size:var(--fs-xs)}",
    // the corpus set table
    ".dt-set{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:4px;min-width:560px;width:100%}",
    ".dt-set th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 8px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
    ".dt-set th.a{color:var(--a)}.dt-set th.b{color:var(--b)}",
    ".dt-set td{padding:3px 8px 3px 0;color:var(--ink-2);vertical-align:middle}",
    ".dt-set td.sz{white-space:nowrap;width:130px}.dt-set td.src{white-space:normal;min-width:16em;overflow-wrap:anywhere}",
    ".dt-set td.src b{color:var(--ink);font-weight:600}.dt-set td.src .kind{font-family:var(--mono);color:var(--ink-3);margin-left:5px}.dt-set td.src .q{color:var(--ink-3)}",
    ".dt-set td.marks{font-family:var(--mono);white-space:nowrap;color:var(--ink-3)}.dt-set td.marks .err{color:var(--bad);font-weight:700}",
    ".dt-src{cursor:pointer;outline:none}.dt-src:hover td{background:var(--surface-2)}.dt-src[aria-selected=true] td{background:var(--surface-2);box-shadow:inset 3px 0 0 var(--ink)}",
    ".dt-set tr.sep td{border-top:1px solid var(--rule);padding-top:6px}",
    ".dt-szbar{display:inline-flex;align-items:center;gap:6px;min-width:0}.dt-szbar i{display:inline-block;height:9px;border-radius:2px;flex:none}",
    ".dt-szbar i.a{background:var(--a)}.dt-szbar i.b{background:var(--b)}.dt-szbar i.err{background:repeating-linear-gradient(45deg,var(--bad) 0 2px,transparent 2px 5px)}",
    ".dt-szbar .n{font-family:var(--mono);color:var(--ink-3);white-space:nowrap}",
    ".dt-detail{margin-top:10px;border:1px solid var(--rule);border-radius:8px;padding:8px 10px}",
    ".dt-detail .dt-h:first-child{margin-top:0}",
    // provenance
    ".dt-answer{font-size:var(--fs-m);color:var(--ink);line-height:1.6;margin:4px 0 6px;max-width:80ch;overflow-wrap:anywhere}",
    ".dt-val{background:transparent;color:inherit;border-bottom:3px solid var(--c);padding:0 1px;border-radius:2px}",
    ".dt-val.unsup{border-bottom:3px dashed var(--bad)}",
    ".dt-leg{list-style:none;margin:0;padding:0;font-size:var(--fs-xs);color:var(--ink-2);line-height:1.6}",
    ".dt-leg li{display:flex;gap:6px;align-items:baseline;min-width:0}.dt-leg i{display:inline-block;width:14px;height:5px;border-radius:2px;flex:none;position:relative;top:-2px}",
    ".dt-leg i.unsup{border-bottom:3px dashed var(--bad);height:0;background:none}",
    ".dt-leg button{font:inherit;font-size:var(--fs-xs);border:0;background:none;color:var(--ink);font-weight:600;padding:0;cursor:pointer;text-align:left;overflow-wrap:anywhere}.dt-leg button:hover{text-decoration:underline}",
    ".dt-leg .n{font-family:var(--mono);color:var(--ink-3);white-space:nowrap}",
    ".dt-counts{display:flex;gap:4px 16px;flex-wrap:wrap;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 4px}.dt-counts b{color:var(--ink);font-weight:600;font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ".dt-counts .ok{color:var(--good)}.dt-counts .ko{color:var(--bad)}",
    ".dt-eval{font-size:var(--fs-xs);color:var(--ink-2);margin:6px 0 0;line-height:1.5}.dt-eval b{color:var(--ink);font-weight:600}",
    ".dt-eval .match{color:var(--good)}.dt-eval .mismatch{color:var(--bad)}.dt-eval .partial{color:var(--warn)}",
    // chain
    ".dt-stage{outline:none;border-radius:8px}",
    ".dt-node{cursor:pointer;outline:none}.dt-node .ring{fill:none;stroke:var(--accent);stroke-width:2;stroke-opacity:0;pointer-events:none}.dt-node text{pointer-events:none}.dt-node[aria-pressed=true] .ring{stroke-opacity:1}.dt-node:focus-visible .ring{stroke-opacity:1}",
    ".dt-edge{fill:none}.dt-edge.produces{stroke:var(--ink-3);stroke-opacity:.55}.dt-edge.feeds{stroke:var(--accent);stroke-opacity:.8}.dt-edge.reaches{stroke-opacity:.7}.dt-edge.adjacent{stroke-dasharray:3 3}",
    ".dt-hit{fill:none;stroke:transparent;stroke-width:9px;pointer-events:stroke}",
    // evolution rows
    ".dt-rows{margin-top:4px}",
    ".dt-erow{display:grid;grid-template-columns:78px minmax(150px,1.2fr) minmax(150px,1.4fr) minmax(150px,1.2fr) 160px minmax(120px,1fr);gap:2px 12px;align-items:start;padding:6px 6px;border-radius:6px;font-size:var(--fs-xs);color:var(--ink-2);cursor:pointer;outline:none;border-top:1px solid var(--rule)}",
    ".dt-erow:hover{background:var(--surface-2)}.dt-erow[aria-expanded=true]{background:var(--surface-2);box-shadow:inset 3px 0 0 var(--ink)}",
    ".dt-erow.head{cursor:default;color:var(--ink-3);font-family:var(--mono);border-top:0;padding-bottom:2px}.dt-erow.head:hover{background:none}",
    ".dt-erow .k{display:none;font-family:var(--mono);color:var(--ink-3);text-transform:uppercase;letter-spacing:.04em;margin-right:4px}",
    ".dt-erow .cell{min-width:0;line-height:1.45;overflow-wrap:anywhere}.dt-erow .cell b{color:var(--ink);font-weight:600}.dt-erow .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ".dt-erow .v{font-weight:600}.dt-erow .v .g{font-weight:700;margin-right:3px}",
    ".dt-erow .pair{white-space:nowrap}.dt-erow .pair .arr{color:var(--ink-3)}",
    ".dt-flag{font-family:var(--mono);margin-right:6px;white-space:nowrap}.dt-flag.learned{color:var(--accent)}.dt-flag.base{color:var(--ink-3)}.dt-flag.none{color:var(--ink-3);font-style:italic;white-space:normal}",
    ".dt-epanel{padding:8px 6px 12px;border-top:1px solid var(--rule)}",
    ".dt-eplist{list-style:none;margin:0;padding:0;font-size:var(--fs-xs);color:var(--ink-2);line-height:1.5}.dt-eplist li{margin:0 0 6px;overflow-wrap:anywhere}.dt-eplist b{color:var(--ink);font-weight:600}",
    ".dt-eplist .srcs{color:var(--ink-3);font-family:var(--mono)}",
    "@media (max-width:900px){.dt-erow{grid-template-columns:78px minmax(140px,1fr) minmax(140px,1fr)}.dt-erow .k{display:inline}.dt-erow.head{display:none}}",
    "@media (max-width:520px){.dt-erow{grid-template-columns:1fr}}",
  ].join("\n");
  function ensureStyle() { L.style.once("data-view", CSS); }

  // ------------------------------------------------------------- helpers

  //: the core's element helper, bound from ctx.h at the top of every render
  var H = null;
  var fmtInt = d3.format(",d");
  function int(v) { return isNum(v) ? fmtInt(Math.round(v)) : "—"; }
  var plural = L.fmt.plural, trunc = L.fmt.trunc;
  function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (err) { return false; }
  }
  function dur() { return prefersReduced() ? 0 : DUR; }
  function now() { return global.performance ? performance.now() : Date.now(); }
  function width(host) { return L.layout.measure(host, 300, 1400); }
  function ci(iv, p) { return iv && isNum(iv.point) ? num(iv.point, p) + (isNum(iv.lo) && isNum(iv.hi) ? " [" + num(iv.lo, p) + ", " + num(iv.hi, p) + "]" : "") : "—"; }
  //: a fade on state change only (never on first paint, instant under reduced motion)
  function fadeIn(node) { if (dur() > 0 && node) d3.select(node).style("opacity", 0).transition().duration(dur()).style("opacity", 1); }
  function sideName(s) { return s === "a" ? "A" : "B"; }
  function sideColour(s) { return L.color.side(s); }

  // ------------------------------------------------------------ the data

  function pairData(ctx) { var r = ctx.report; return r && r.data && typeof r.data === "object" ? r.data : null; }
  function sideOf(d, s) { return d && d[s] && typeof d[s] === "object" ? d[s] : null; }
  function runBlock(ctx, s) { var r = ctx.report; return r && r[s] && typeof r[s] === "object" ? r[s] : null; }
  function agentOf(d, s) { var x = sideOf(d, s); return x && x.agent && x.agent.name ? String(x.agent.name) : sideName(s); }
  function stepAt(blk, i) {
    if (!blk || !Array.isArray(blk.steps) || !isNum(i)) return null;
    for (var k = 0; k < blk.steps.length; k++) if (blk.steps[k] && blk.steps[k].index === i) return blk.steps[k];
    return blk.steps[i] || null;
  }
  //: a step's text, capped at TEXT_CAP with the full length said
  function textOf(value) {
    var s = value === null || value === undefined ? "" : typeof value === "string" ? value : JSON.stringify(value);
    return { text: s.length > TEXT_CAP ? s.slice(0, TEXT_CAP) : s, chars: s.length, truncated: s.length > TEXT_CAP };
  }
  //: the answer text the section read, by its `answer_source`
  function answerText(blk, source) {
    if (!blk) return "";
    var m = /^steps\[(-?\d+)\]\.(input|output)$/.exec(String(source || ""));
    if (m) {
      var steps = Array.isArray(blk.steps) ? blk.steps : [], i = parseInt(m[1], 10);
      var st = i < 0 ? steps[steps.length + i] : steps[i];
      return st && st[m[2]] !== null && st[m[2]] !== undefined ? String(st[m[2]]) : "";
    }
    if (/^outcome\.answer$/.test(String(source || ""))) return blk.outcome && blk.outcome.answer !== null && blk.outcome.answer !== undefined ? String(blk.outcome.answer) : "";
    var last = Array.isArray(blk.steps) && blk.steps.length ? blk.steps[blk.steps.length - 1] : null;
    return last && last.output ? String(last.output) : "";
  }
  function lineageOf(ctx) {
    var de = ctx.aggregate && ctx.aggregate.data_evolution;
    return de && typeof de === "object" && de.measurable !== false && Array.isArray(de.steps) && de.steps.length ? de : null;
  }
  function stepKey(s) { return String(s.from) + "→" + String(s.to); }
  //: every source id the page's pair reports name, so a lineage row can name what an episode read
  var NAMES = null;
  function nameIndex(ctx) {
    if (NAMES && NAMES.reports === ctx.reports) return NAMES.map;
    var map = {};
    (ctx.reports || []).forEach(function (r) {
      var d = r && r.data;
      if (!d) return;
      ["a", "b"].forEach(function (s) {
        var c = d[s] && d[s].corpus;
        ((c && c.sources) || []).forEach(function (src) { if (src && src.id && !map[src.id]) map[src.id] = { name: String(src.name || ""), kind: String(src.kind || ""), input: String(src.input || "") }; });
      });
    });
    NAMES = { reports: ctx.reports, map: map };
    return map;
  }
  function synthOf(d) { return !!(d && ((d.a && d.a.synthetic) || (d.b && d.b.synthetic))); }

  // -------------------------------------------------------------- family
  //
  // {source: a source id, step: a step index, side: "a"|"b", gen: a lineage
  // step key "g2→g3"}: page scoped, persisted, restored on load. A stored
  // value this page cannot honour is read as null by the block that draws
  // it (a source id belongs to one task's corpus; a step to one run).

  var FAMILY = null, TIMING = { task: null, corpus: null, provenance: null, chain: null, evolution: null }, TILE = 1;
  function family() { return FAMILY || (FAMILY = L.family("data", { source: null, step: null, gen: null, side: null }, { scope: "page" })); }
  function state() { return family().get(); }
  function select(patch) { family().set(patch); }
  function listen(el, fn) { family().subscribe(function (st) { try { fn(st); } catch (err) { if (global.console) console.warn("AgentDiff data: repaint failed", err); } }, el); }
  function sideSel(d) { var st = state(); return st.side === "b" && sideOf(d, "b") ? "b" : sideOf(d, "a") ? "a" : "b"; }
  //: the lineage step the family names: a step key, or a generation id (the step that made it)
  function genSel(de) {
    var st = state();
    if (st.gen === null || st.gen === undefined) return null;
    var g = String(st.gen);
    var hit = de.steps.filter(function (s) { return stepKey(s) === g || String(s.to) === g; })[0];
    return hit ? stepKey(hit) : null;
  }
  AgentDiff.data = {
    select: select,
    state: function () { var st = state(); return { source: st.source, step: st.step, gen: st.gen, side: st.side }; },
    reset: function () { family().reset(); },
    //: the last draw times in ms, per block, and the tiling factor
    timing: function () { return { task: TIMING.task, corpus: TIMING.corpus, provenance: TIMING.provenance, chain: TIMING.chain, evolution: TIMING.evolution, tile: TILE }; },
    /* A measurement hook: tile the corpus rows and the chain nodes n times
     * (ids suffixed "~k", steps offset) and re-render, to time the drawings
     * at ten times the shipped scale. The status lines say it is tiled. */
    tile: function (n) { TILE = Math.max(1, Math.min(100, isNum(n) ? Math.round(n) : 1)); if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); return TILE; },
  };

  // ------------------------------------------------------- shared pieces

  //: the Evolution view's diff drawing (evo-step): a pre, one span per line, add / del / hunk / ctx
  function diffLines(hunks) {
    var pre = H("pre", { class: "dt-diff" });
    (Array.isArray(hunks) ? hunks : []).forEach(function (h, hi) {
      String(h).split("\n").forEach(function (line, li) {
        if (li === 0 && hi > 0 && line === "") return;
        var cls = line.indexOf("@@") === 0 ? "hunk" : line.charAt(0) === "+" ? "add" : line.charAt(0) === "-" ? "del" : "ctx";
        pre.appendChild(H("span", { class: cls, text: line + "\n" }));
      });
    });
    return pre;
  }
  //: a text as recorded, capped, with the cap said
  function textBlock(value, what, where) {
    var t = textOf(value);
    var wrap = H("div");
    wrap.appendChild(H("pre", { class: "dt-text", "data-chars": String(t.chars), text: t.text || "(empty)" }));
    wrap.appendChild(H("p", { class: "dt-note", style: { margin: "3px 0 0" }, text: what + ": " + plural(t.chars, "character") + (t.truncated ? ", the first " + int(TEXT_CAP) + " shown — the full text is in the trace" : "") + (where ? " (" + where + ")" : "") + "." }));
    return wrap;
  }
  function table(head, rows) {
    var tbl = H("table", { class: "dt-table" });
    tbl.appendChild(H("thead", null, H("tr", null, head.map(function (c) { return H("th", { class: c.num ? "num" : null, text: c.text }); }))));
    var body = H("tbody");
    rows.forEach(function (r) { body.appendChild(H("tr", null, r.map(function (c) { return H("td", { class: c.cls || null, text: c.text }); }))); });
    tbl.appendChild(body);
    return H("div", { class: "scroll-x" }, tbl);
  }
  function synChip() { return H("span", { class: "dt-syn", "data-role": "synthetic", title: "the traces are a synthetic demo; every number is real arithmetic over synthetic steps", text: "SYNTHETIC" }); }
  function sideLabel(d, s) { return H("span", { class: "dt-side " + s, text: agentOf(d, s) }); }

  // ================================================================ dt-task

  function renderTask(root, ctx, d) {
    var a = sideOf(d, "a"), b = sideOf(d, "b"), task = d.task || (a && a.task) || (b && b.task) || {};
    var diff = d.instructions_diff || {}, models = d.models || {};
    var ia = a && a.agent && a.agent.instructions || {}, ib = b && b.agent && b.agent.instructions || {};
    var samePrompt = !a || !b || String(a.task && a.task.prompt || "") === String(b.task && b.task.prompt || "");
    // the lede
    var lede = H("p", { class: "dt-lede" });
    lede.appendChild(H("span", { text: samePrompt ? "Both agents were given the same " : "The two agents were given different prompts: " }));
    if (samePrompt) lede.appendChild(H("b", { text: int(task.prompt_chars) + "-character prompt" }));
    else lede.appendChild(H("b", { text: int(a.task.prompt_chars) + " and " + int(b.task.prompt_chars) + " characters" }));
    lede.appendChild(H("span", { text: (task.expected !== null && task.expected !== undefined ? " with a " + int(task.expected_chars) + "-character expected answer" : " with no expected answer recorded") + ". " }));
    lede.appendChild(H("span", { text: diff.same === true ? "The same instructions on both sides. " : diff.same === false ? "Their instructions differ by " + plural((diff.hunks || []).length, "hunk") + ". " : cap(String(diff.reason || "no instructions recorded on either side")) + ". " }));
    lede.appendChild(H("span", { text: models.same ? "The same model recorded on both sides." : "Different models recorded: " + agentOf(d, "a") + " " + ((models.a || []).join(", ") || "none") + ", " + agentOf(d, "b") + " " + ((models.b || []).join(", ") || "none") + "." }));
    root.appendChild(lede);
    var bar = H("div", { class: "dt-bar" });
    if (synthOf(d)) bar.appendChild(synChip());
    ["a", "b"].forEach(function (s) {
      var x = sideOf(d, s);
      if (!x) return;
      bar.appendChild(H("span", { class: "dt-chip" }, [H("i", { style: { background: sideColour(s) } }), H("b", { text: agentOf(d, s) }), H("span", { text: (x.agent && x.agent.version ? " " + x.agent.version : "") + (x.agent && (x.agent.framework || x.agent.adapter) ? " · " + (x.agent.framework || x.agent.adapter) : "") + (x.measurable === false ? " · not measurable: " + (x.reason || "no reason given") : "") })]));
    });
    root.appendChild(bar);
    // the prompt
    root.appendChild(H("div", { class: "dt-h", text: "prompt · " + String(task.id || ctx.task || "") }));
    if (samePrompt) root.appendChild(H("pre", { class: "dt-text prompt", "data-role": "prompt", text: String(task.prompt || "(no prompt recorded)") }));
    else {
      var pc = H("div", { class: "dt-cols" });
      ["a", "b"].forEach(function (s) { var x = sideOf(d, s); pc.appendChild(H("div", null, [sideLabel(d, s), H("pre", { class: "dt-text prompt", "data-role": "prompt", text: String(x.task.prompt || "(no prompt recorded)") })])); });
      root.appendChild(pc);
    }
    root.appendChild(H("div", { class: "dt-h", text: "expected answer" }));
    if (task.expected !== null && task.expected !== undefined) root.appendChild(H("pre", { class: "dt-text prompt", "data-role": "expected", text: String(task.expected) }));
    else root.appendChild(H("p", { class: "dt-note", "data-role": "expected", style: { margin: 0 }, text: "No expected answer is recorded on the task." }));
    // the instructions
    root.appendChild(H("div", { class: "dt-h", text: "instructions" }));
    var hasA = typeof ia.system_prompt === "string", hasB = typeof ib.system_prompt === "string";
    if (!hasA && !hasB) {
      root.appendChild(H("p", { class: "dt-cannot", "data-role": "no-instructions", text: "No instructions are recorded on either trace (" + String(diff.reason || "no instructions recorded on either side") + "): neither trace carries agent.system_prompt nor a system_prompt in agent.config. What is recorded is below — the models, the tools declared and used." }));
      lineageInstructions(root, ctx, d);
    } else if (diff.same === true) {
      root.appendChild(H("p", { class: "dt-read", "data-role": "same-instructions", text: "The same instructions on both sides: " + plural(ia.chars, "character") + " (" + String(ia.source || "") + ")." }));
      root.appendChild(H("details", { class: "dt-details" }, [H("summary", { text: "the instructions as recorded" }), textBlock(ia.system_prompt, "instructions", ia.source)]));
    } else {
      var cols = H("div", { class: "dt-cols" });
      ["a", "b"].forEach(function (s) {
        var ins = s === "a" ? ia : ib, has = s === "a" ? hasA : hasB;
        var col = H("div", { "data-side": s });
        col.appendChild(H("p", { class: "dt-read", style: { margin: "0 0 4px" } }, [sideLabel(d, s), H("span", { text: has ? " · " + plural(ins.chars, "character") + " · " + String(ins.source || "") : " · no instructions recorded on this trace" })]));
        if (has) col.appendChild(textBlock(ins.system_prompt, "instructions", ins.source));
        cols.appendChild(col);
      });
      root.appendChild(cols);
      if (hasA && hasB) {
        root.appendChild(H("div", { class: "dt-h", text: "their difference · +" + int(diff.added) + " −" + int(diff.removed) + " lines · " + plural((diff.hunks || []).length, "hunk") }));
        if ((diff.hunks || []).length) root.appendChild(diffLines(diff.hunks));
        else root.appendChild(H("p", { class: "dt-note", style: { margin: 0 }, text: "the texts differ only in whitespace, or the hunks were not included." }));
      }
    }
    // the models, as recorded, with the source of the attribution
    root.appendChild(H("div", { class: "dt-h", text: "models · as the traces record them" + (models.same ? " · the same on both sides" : "") }));
    var mrows = [];
    ["a", "b"].forEach(function (s) {
      var x = sideOf(d, s);
      ((x && x.models) || []).forEach(function (m) {
        var kinds = m.kinds && typeof m.kinds === "object" ? Object.keys(m.kinds).sort().map(function (k) { return k + " " + m.kinds[k]; }).join(", ") : "";
        mrows.push([{ text: agentOf(d, s), cls: "mono" }, { text: m.model === null || m.model === undefined ? "(no model recorded)" : String(m.model), cls: m.model ? "mono" : "dim" }, { text: int(m.steps), cls: "num" }, { text: kinds, cls: "wrap" }, { text: int(m.tokens), cls: "num" }, { text: isNum(m.temperature) ? num(m.temperature) : "—", cls: "num" }, { text: String(m.source || "—"), cls: "mono dim" }]);
      });
    });
    if (mrows.length) root.appendChild(table([{ text: "agent" }, { text: "model (as recorded)" }, { text: "steps", num: true }, { text: "step kinds" }, { text: "tokens", num: true }, { text: "temperature", num: true }, { text: "source of the attribution" }], mrows));
    else root.appendChild(H("p", { class: "dt-note", style: { margin: 0 }, text: "No model is recorded on either trace." }));
    // the tools
    root.appendChild(H("div", { class: "dt-h", text: "tools · declared and used" }));
    var trows = [];
    ["a", "b"].forEach(function (s) {
      var x = sideOf(d, s), ag = x && x.agent || {};
      var decl = Array.isArray(ag.tools_declared) ? ag.tools_declared : [], used = Array.isArray(ag.tools_used) ? ag.tools_used : [];
      var byName = {};
      decl.forEach(function (t) { byName[t.name] = { declared: true, effect: t.effect, calls: null, seen: null }; });
      used.forEach(function (t) { var e = byName[t.name] || (byName[t.name] = { declared: false, effect: null, calls: null, seen: null }); e.calls = t.calls; e.seen = t.effect_seen; });
      var names = Object.keys(byName).sort();
      if (!names.length) { trows.push([{ text: agentOf(d, s), cls: "mono" }, { text: "no tool declared or used", cls: "dim" }, { text: "" }, { text: "" }, { text: "" }]); return; }
      names.forEach(function (n) {
        var e = byName[n];
        trows.push([{ text: agentOf(d, s), cls: "mono" }, { text: n, cls: "mono" }, { text: e.declared ? "declared" + (e.effect ? " · " + e.effect : "") : "not in the tool table", cls: e.declared ? "" : "dim" }, { text: isNum(e.calls) ? int(e.calls) : "0", cls: "num" }, { text: Array.isArray(e.seen) ? e.seen.join(", ") : e.seen ? String(e.seen) : "—", cls: "dim" }]);
      });
    });
    root.appendChild(table([{ text: "agent" }, { text: "tool" }, { text: "declared" }, { text: "calls", num: true }, { text: "effect seen" }], trows));
    aggregateFold(root, ctx);
    root.appendChild(H("p", { class: "dt-note", text: "Everything here is read as recorded: the prompt and the expected answer from the task, the instructions from agent.system_prompt or agent.config (their source is said), the model from each step's own telemetry when a step names one and otherwise the trace's declared agent.model (source: steps[].model or trace.agent.model). A run with no prompt is unmeasurable, not assumed to have had the task's. " + (d.narrative ? cap(String(d.narrative)) + (String(d.narrative).slice(-1) === "." ? "" : ".") : "") }));
  }
  /* A lineage page's pair is its last two generations, whose traces record
   * no instructions; the lineage's artifacts do, and the section's
   * generations carry them with the source said. */
  function lineageInstructions(root, ctx, d) {
    var de = lineageOf(ctx);
    if (!de || !Array.isArray(de.generations)) return;
    var va = d.a && d.a.agent && d.a.agent.version, vb = d.b && d.b.agent && d.b.agent.version;
    var ga = de.generations.filter(function (g) { return String(g.id) === String(va); })[0], gb = de.generations.filter(function (g) { return String(g.id) === String(vb); })[0];
    if (!ga && !gb) return;
    var step = ga && gb ? de.steps.filter(function (s) { return String(s.from) === String(ga.id) && String(s.to) === String(gb.id); })[0] : null;
    var det = H("details", { class: "dt-details", "data-role": "lineage-instructions" });
    det.appendChild(H("summary", { text: "the lineage's artifacts record " + [ga, gb].filter(Boolean).map(function (g) { return g.id + "'s instructions (" + plural(g.instructions && g.instructions.chars, "character") + ")"; }).join(" and ") + (step ? " — the step " + stepKey(step) + " changed them by " + plural((step.change && step.change.hunks || []).length, "hunk") : "") }));
    var cols = H("div", { class: "dt-cols" });
    [["a", ga], ["b", gb]].forEach(function (p) {
      if (!p[1]) return;
      var ins = p[1].instructions || {};
      cols.appendChild(H("div", null, [H("p", { class: "dt-read", style: { margin: "0 0 4px" } }, [sideLabel(d, p[0]), H("span", { text: " · " + String(p[1].id) + " · " + plural(ins.chars, "character") + " · " + String(ins.source || "lineage artifacts") + " · " + plural((ins.rules || []).length, "rule") + ", " + plural((ins.skills || []).length, "skill") + ", " + plural((ins.tools || []).length, "tool") + ", " + plural(ins.memory_n, "memory note") })]), textBlock(ins.system_prompt, "instructions", ins.source || "lineage artifacts")]));
    });
    det.appendChild(cols);
    if (step && step.change && (step.change.hunks || []).length) { det.appendChild(H("div", { class: "dt-h", text: "the hunks of " + stepKey(step) })); det.appendChild(diffLines(step.change.hunks)); }
    det.appendChild(H("p", { class: "dt-note", text: "These are the generations' artifacts (aggregate.data_evolution.generations[].instructions), not the traces: the page says which. The whole lineage, step by step, is in “How the agent evolves” below." }));
    root.appendChild(det);
  }
  //: the aggregate's data section, when the page carries one: every run of every agent
  function aggregateFold(root, ctx) {
    var ag = ctx.aggregate && ctx.aggregate.data;
    if (!ag || typeof ag !== "object" || ag.measurable === false || !ag.agents || typeof ag.agents !== "object") return;
    var names = Object.keys(ag.agents);
    if (!names.length) return;
    var rows = names.map(function (n) {
      var x = ag.agents[n] || {};
      return [{ text: n, cls: "mono" }, { text: int(x.runs) + (isNum(x.measurable_runs) && x.measurable_runs !== x.runs ? " (" + int(x.measurable_runs) + " measurable)" : ""), cls: "num" }, { text: (x.models || []).join(", ") || "—", cls: "mono" },
        { text: x.instructions_digest ? "the same text on every run (" + trunc(x.instructions_digest, 12) + ")" : isNum(x.instructions_distinct) && x.instructions_distinct > 0 ? plural(x.instructions_distinct, "distinct text") : "none recorded", cls: x.instructions_digest ? "" : "dim" },
        { text: int(x.sources_distinct), cls: "num" }, { text: int(x.sources_shared_across_runs), cls: "num" },
        { text: isNum(x.grounded_share_mean) ? pct(x.grounded_share_mean) + " over " + plural(x.grounded_runs, "run") + " (" + int(x.supported) + " of " + int(x.atoms) + " values)" : "no run's answer carries a typed value", cls: isNum(x.grounded_share_mean) ? "" : "dim" },
        { text: x.synthetic ? "SYNTHETIC" : "", cls: "dim" }];
    });
    root.appendChild(H("details", { class: "dt-details", "data-role": "aggregate" }, [H("summary", { text: "across every run on this page: " + names.join(", ") }),
      table([{ text: "agent" }, { text: "runs", num: true }, { text: "models recorded" }, { text: "instructions" }, { text: "distinct sources", num: true }, { text: "shared across runs", num: true }, { text: "grounded share (mean of the runs with a typed value)" }, { text: "" }], rows),
      ag.narrative ? H("p", { class: "dt-note", text: cap(String(ag.narrative)) }) : null]));
  }

  AgentDiff.block({
    id: "dt-task",
    title: "What both agents were told",
    question: "The prompt, the expected answer, each agent's instructions and their difference, the models as recorded.",
    group: "data",
    size: "wide",
    relevance: function (ctx) { var d = pairData(ctx); return d && (sideOf(d, "a") || sideOf(d, "b")) ? 1 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var d = pairData(ctx);
      if (!d) return ctx.empty(el, "This report carries no data section.");
      var t0 = now();
      var root = H("div", { class: "dt dt-task" });
      el.appendChild(root);
      renderTask(root, ctx, d);
      TIMING.task = now() - t0;
      root.setAttribute("data-draw-ms", TIMING.task.toFixed(1));
    },
  });

  // ============================================================== dt-corpus

  //: the set table's rows: shared first (by A's first read), then only A, then only B
  function corpusRows(d) {
    var A = {}, B = {};
    (((sideOf(d, "a") || {}).corpus || {}).sources || []).forEach(function (s) { if (s && s.id) A[s.id] = s; });
    (((sideOf(d, "b") || {}).corpus || {}).sources || []).forEach(function (s) { if (s && s.id) B[s.id] = s; });
    var rows = [];
    Object.keys(A).forEach(function (id) { rows.push({ id: id, a: A[id], b: B[id] || null, set: B[id] ? "shared" : "only_a" }); });
    Object.keys(B).forEach(function (id) { if (!A[id]) rows.push({ id: id, a: null, b: B[id], set: "only_b" }); });
    var rank = { shared: 0, only_a: 1, only_b: 2 };
    rows.sort(function (x, y) {
      if (rank[x.set] !== rank[y.set]) return rank[x.set] - rank[y.set];
      var fx = (x.a || x.b).first_step, fy = (y.a || y.b).first_step;
      return (isNum(fx) ? fx : 1e9) - (isNum(fy) ? fy : 1e9) || (x.id < y.id ? -1 : 1);
    });
    if (TILE > 1) {
      var base = rows.slice();
      for (var k = 1; k < TILE; k++) base.forEach(function (r) { rows.push({ id: r.id + "~" + k, a: r.a, b: r.b, set: r.set, tiled: true }); });
    }
    return rows;
  }
  function sizeCell(src, s, max) {
    var td = H("td", { class: "sz" });
    if (!src) { td.appendChild(H("span", { class: "dt-szbar" }, H("span", { class: "n dim", text: "—" }))); return td; }
    var w = Math.max(2, Math.round(84 * Math.sqrt((isNum(src.output_chars) ? src.output_chars : 0) / max)));
    td.appendChild(H("span", { class: "dt-szbar", title: plural(src.output_chars, "character") + " back at step " + int(src.first_step) + (src.error ? " · error" : "") }, [
      H("i", { class: src.error ? "err" : s, style: { width: w + "px" } }),
      H("span", { class: "n", text: int(src.output_chars) })]));
    return td;
  }
  function marksCell(r) {
    var parts = [];
    var err = (r.a && r.a.error) || (r.b && r.b.error);
    if (err) parts.push(H("span", { class: "err", title: "the fetch errored", text: "✕ error" }));
    ["a", "b"].forEach(function (s) {
      var src = r[s];
      if (src && Array.isArray(src.steps) && src.steps.length > 1) parts.push(H("span", { title: sideName(s) + " read it " + plural(src.steps.length, "time") + (src.outputs_differ ? ", the outputs differed" : "") , text: (parts.length ? " " : "") + "×" + src.steps.length + sideName(s) + (src.outputs_differ ? "≠" : "") }));
    });
    return H("td", { class: "marks" }, parts.length ? parts : H("span", { text: "" }));
  }
  function setStrip(host, ctx, d, cd) {
    var S = ctx.svg, W = width(host), Hh = 30;
    var nA = (cd.only_a || []).length, nS = (cd.shared || []).length, nB = (cd.only_b || []).length, n = nA + nS + nB || 1;
    var x = d3.scaleLinear().domain([0, n]).range([0, W]);
    var label = "the two corpora as sets: " + plural(nA, "source") + " only " + agentOf(d, "a") + ", " + int(nS) + " shared, " + int(nB) + " only " + agentOf(d, "b") + "; Jaccard " + num(cd.jaccard, 3);
    var svg = L.svg({ class: "dt-strip", viewBox: "0 0 " + W + " " + Hh, width: W, height: Hh, "aria-label": label });
    var segs = [["only_a", nA, sideColour("a"), agentOf(d, "a") + " only"], ["shared", nS, "var(--ink-2)", "shared"], ["only_b", nB, sideColour("b"), agentOf(d, "b") + " only"]];
    var acc = 0;
    segs.forEach(function (sg) {
      if (!sg[1]) return;
      var x0 = x(acc), x1 = x(acc + sg[1]);
      svg.appendChild(S("rect", { x: x0, y: 4, width: Math.max(1, x1 - x0 - 1), height: 12, fill: sg[2], "fill-opacity": sg[0] === "shared" ? 0.55 : 0.8, rx: 2 }));
      var txt = sg[3] + " " + sg[1];
      if (x1 - x0 > txt.length * 6.6 + 8) svg.appendChild(S("text", { class: "lab backed", x: x0 + 3, y: 27, text: txt }));
      acc += sg[1];
    });
    host.appendChild(svg);
    host.appendChild(H("p", { class: "dt-note", style: { margin: "2px 0 0" }, text: agentOf(d, "a") + " only " + int(nA) + " · shared " + int(nS) + " · " + agentOf(d, "b") + " only " + int(nB) + ". The strip is the union of the two corpora, one unit per distinct source, coloured by who read it. Jaccard " + num(cd.jaccard, 3) + " = shared ÷ union" + (cd.basis ? " (" + cd.basis + ")" : "") + "." }));
  }
  function sourceDetail(host, ctx, d, rows, byId, animate) {
    host.innerHTML = "";
    var st = state(), id = st.source, r = id !== null && id !== undefined ? byId[String(id)] : null;
    if (!r) { host.appendChild(H("p", { class: "dt-note", "data-role": "hint", style: { margin: "6px 0 0" }, text: "Click a source (or press Enter on it) for its input and the text it returned, as the report's step recorded them." })); return; }
    var src = r.a || r.b;
    var panel = H("div", { class: "dt-detail", "data-source": r.id });
    panel.appendChild(H("div", { class: "dt-h" }, [H("span", { text: String(src.name) + " · " + String(src.kind) + " · " }), H("span", { style: { textTransform: "none" }, text: String(r.id) + (r.tiled ? " · tiled copy" : "") })]));
    var readers = [];
    ["a", "b"].forEach(function (s) { if (r[s]) readers.push(agentOf(d, s) + " at step" + (r[s].steps.length === 1 ? " " : "s ") + r[s].steps.join(", ") + (r[s].error ? " (error)" : "") + (r[s].outputs_differ ? " (a repeat returned different text)" : "")); });
    panel.appendChild(H("p", { class: "dt-read", style: { margin: "0 0 6px" }, text: "Read by " + readers.join("; ") + ". Identity: " + String((((sideOf(d, "a") || sideOf(d, "b") || {}).corpus || {}).id_basis) || "tool name and normalised input") + "." }));
    var cols = H("div", { class: "dt-cols" });
    ["a", "b"].forEach(function (s) {
      var x = r[s];
      if (!x) return;
      var blk = runBlock(ctx, s), st0 = stepAt(blk, x.first_step);
      var col = H("div", { "data-side": s });
      col.appendChild(H("p", { class: "dt-read", style: { margin: "0 0 4px" } }, [sideLabel(d, s), H("span", { text: " · step " + int(x.first_step) + (st0 && st0.model && (st0.model.model || st0.model.name) ? " · " + String(st0.model.model || st0.model.name) : "") + (isNum(x.tokens) ? " · " + plural(x.tokens, "token") : "") })]));
      col.appendChild(H("div", { class: "dt-h", text: "input" }));
      col.appendChild(textBlock(st0 && st0.input !== undefined && st0.input !== null ? st0.input : x.input, "input", "steps[" + int(x.first_step) + "].input"));
      col.appendChild(H("div", { class: "dt-h", text: "output" + (x.error ? " · error" : "") }));
      if (st0) col.appendChild(textBlock(st0.output, "output", "steps[" + int(x.first_step) + "].output; digest " + String(x.digest || "—")));
      else col.appendChild(H("p", { class: "dt-note", style: { margin: 0 }, text: "The step's text is not in this report (the section recorded " + plural(x.output_chars, "character") + "); the full text is in the trace." }));
      if (st0 && st0.error) col.appendChild(H("p", { class: "dt-note", style: { margin: "3px 0 0", color: "var(--bad)" }, text: "error: " + String(st0.error) }));
      cols.appendChild(col);
    });
    panel.appendChild(cols);
    host.appendChild(panel);
    if (animate) fadeIn(panel);
  }

  AgentDiff.block({
    id: "dt-corpus",
    title: "What each agent read",
    question: "The sources each run fetched — shared, only A, only B — with their sizes, errors and repeats; a source opens to its text.",
    group: "data",
    size: "wide",
    relevance: function (ctx) { var d = pairData(ctx); return d && corpusRows(d).length ? 1 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var d = pairData(ctx);
      var rows = d ? corpusRows(d) : [];
      if (!rows.length) return ctx.empty(el, "Neither run fetched a source.");
      var t0 = now();
      var root = H("div", { class: "dt dt-corpus" });
      el.appendChild(root);
      var ca = (sideOf(d, "a") || {}).corpus || {}, cb = (sideOf(d, "b") || {}).corpus || {}, cd = d.corpus_diff || {};
      var byId = {};
      rows.forEach(function (r) { byId[r.id] = r; });
      var lede = H("p", { class: "dt-lede" });
      [["a", ca], ["b", cb]].forEach(function (p, i) {
        if (!sideOf(d, p[0])) return;
        lede.appendChild(H("b", { text: agentOf(d, p[0]) }));
        lede.appendChild(H("span", { text: " read " + plural(p[1].distinct, "distinct source") + " in " + plural(p[1].fetches, "fetch") + ", " + plural(p[1].total_chars, "character") + " back" + (isNum(p[1].repeated_reads) && p[1].repeated_reads ? ", " + plural(p[1].repeated_reads, "repeated read") : "") + (i === 0 && sideOf(d, "b") ? "; " : ". ") }));
      });
      lede.appendChild(H("span", { text: plural((cd.shared || []).length, "source") + " shared — Jaccard " + num(cd.jaccard, 3) + "." + (TILE > 1 ? " Tiled ×" + TILE + " for measurement." : "") }));
      root.appendChild(lede);
      var bar = H("div", { class: "dt-bar" });
      if (synthOf(d)) bar.appendChild(synChip());
      bar.appendChild(H("span", { class: "dt-chip" }, [H("i", { style: { background: sideColour("a") } }), H("span", { text: agentOf(d, "a") + " · output size" })]));
      bar.appendChild(H("span", { class: "dt-chip" }, [H("i", { style: { background: sideColour("b") } }), H("span", { text: agentOf(d, "b") + " · output size" })]));
      bar.appendChild(H("span", { class: "dt-chip", text: "bar length ∝ √chars · ✕ error · ×n repeats (≠ outputs differed)" }));
      root.appendChild(bar);
      var strip = H("div", { class: "dt-chart" });
      root.appendChild(L.layout.responsive(strip, function () { strip.innerHTML = ""; setStrip(strip, ctx, d, cd); }, "dt-corpus-strip"));
      // the set table
      var max = d3.max(rows, function (r) { return Math.max(r.a && isNum(r.a.output_chars) ? r.a.output_chars : 0, r.b && isNum(r.b.output_chars) ? r.b.output_chars : 0); }) || 1;
      var tbl = H("table", { class: "dt-set", "data-rows": String(rows.length) });
      tbl.appendChild(H("thead", null, H("tr", null, [H("th", { class: "a", text: agentOf(d, "a") }), H("th", { text: "source · kind · input" }), H("th", { class: "b", text: agentOf(d, "b") }), H("th", { text: "marks" })])));
      var body = H("tbody");
      var st = state(), shown = rows.slice(0, ROW_CAP), lastSet = null;
      function rowEl(r) {
        var src = r.a || r.b;
        var tr = H("tr", { class: "dt-src" + (r.set !== lastSet ? " sep" : ""), tabindex: "0", role: "button", "data-source": r.id, "data-set": r.set, "aria-selected": st.source === r.id ? "true" : "false",
          "aria-label": String(src.name) + " " + String(src.kind) + ", " + (r.set === "shared" ? "shared" : r.set === "only_a" ? "only " + agentOf(d, "a") : "only " + agentOf(d, "b")) });
        lastSet = r.set;
        tr.appendChild(sizeCell(r.a, "a", max));
        var td = H("td", { class: "src" }, [H("b", { text: String(src.name) }), H("span", { class: "kind", text: String(src.kind) }), H("span", { class: "q", text: " · " + trunc(src.input, 90) })]);
        tr.appendChild(td);
        tr.appendChild(sizeCell(r.b, "b", max));
        tr.appendChild(marksCell(r));
        function pick() { ctx.signal("inspect"); select({ source: state().source === r.id ? null : r.id }); }
        tr.addEventListener("click", pick);
        tr.addEventListener("keydown", function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); pick(); } });
        return tr;
      }
      shown.forEach(function (r) { body.appendChild(rowEl(r)); });
      tbl.appendChild(body);
      root.appendChild(H("div", { class: "scroll-x" }, tbl));
      if (rows.length > ROW_CAP) {
        var more = H("details", { class: "dt-details", "data-role": "more-rows" }, H("summary", { text: "the " + int(rows.length - ROW_CAP) + " remaining rows (the first " + int(ROW_CAP) + " are drawn above)" }));
        var moreHost = H("div", { class: "scroll-x" });
        more.appendChild(moreHost);
        more.addEventListener("toggle", function () {
          if (!more.open || moreHost.childNodes.length) return;
          var t2 = H("table", { class: "dt-set" }), b2 = H("tbody");
          rows.slice(ROW_CAP).forEach(function (r) { b2.appendChild(rowEl(r)); });
          t2.appendChild(b2); moreHost.appendChild(t2);
        });
        root.appendChild(more);
      }
      var detail = H("div", { class: "dt-detail-host" });
      root.appendChild(detail);
      sourceDetail(detail, ctx, d, rows, byId, false);
      listen(root, function (s2) {
        var trs = root.querySelectorAll("tr.dt-src");
        for (var i = 0; i < trs.length; i++) trs[i].setAttribute("aria-selected", trs[i].getAttribute("data-source") === s2.source ? "true" : "false");
        sourceDetail(detail, ctx, d, rows, byId, true);
      });
      root.appendChild(H("p", { class: "dt-note", text: "A source is a tool name and its whitespace-normalised input, hashed (" + String(ca.id_basis || cb.id_basis || "sha256, first 16 hex chars") + "); the same rule on both sides is what makes “shared” a fact about the inputs, not the outputs — a shared source may have returned different text to each run (the digest says). The bars are output sizes in characters; nothing here reads a token count or a latency (the budget and fetches sections do)." }));
      TIMING.corpus = now() - t0;
      root.setAttribute("data-draw-ms", TIMING.corpus.toFixed(1));
    },
  });

  // ========================================================== dt-provenance

  //: the answer as segments: plain text and marked values, non-overlapping, first literal occurrence
  function markAnswer(text, values, colourOf) {
    var hits = [];
    (values || []).forEach(function (v) {
      var needle = String(v.value || "");
      if (!needle) { hits.push({ v: v, at: -1 }); return; }
      var at = text.indexOf(needle);
      if (at < 0) at = text.toLowerCase().indexOf(needle.toLowerCase());
      hits.push({ v: v, at: at, len: needle.length });
    });
    var placed = hits.filter(function (h) { return h.at >= 0; }).sort(function (x, y) { return x.at - y.at || y.len - x.len; });
    var segs = [], cur = 0, missing = hits.filter(function (h) { return h.at < 0; }).map(function (h) { return h.v; });
    placed.forEach(function (h) {
      if (h.at < cur) { missing.push(h.v); return; }
      if (h.at > cur) segs.push({ text: text.slice(cur, h.at) });
      segs.push({ text: text.slice(h.at, h.at + h.len), v: h.v, colour: colourOf(h.v) });
      cur = h.at + h.len;
    });
    if (cur < text.length) segs.push({ text: text.slice(cur) });
    return { segs: segs, missing: missing };
  }
  function provenanceSide(host, ctx, d, s, colour, sourceOfValue) {
    var x = sideOf(d, s), pv = x && x.provenance || {}, blk = runBlock(ctx, s);
    var col = H("div", { class: "dt-prov", "data-side": s });
    col.appendChild(H("p", { class: "dt-read", style: { margin: "0 0 2px" } }, [sideLabel(d, s), H("span", { text: " · answer of " + plural(pv.answer_chars, "character") + " (" + String(pv.answer_source || "—") + ")" })]));
    var counts = H("div", { class: "dt-counts", "data-role": "counts" });
    counts.appendChild(H("span", null, [H("b", { text: int(pv.atoms) }), H("span", { text: " typed value" + (pv.atoms === 1 ? "" : "s") })]));
    counts.appendChild(H("span", { class: "ok" }, [H("b", { text: int(pv.supported) }), H("span", { text: " supported" })]));
    counts.appendChild(H("span", { class: isNum(pv.unsupported) && pv.unsupported > 0 ? "ko" : "" }, [H("b", { text: int(pv.unsupported) }), H("span", { text: " unsupported" })]));
    counts.appendChild(H("span", null, [H("span", { text: "grounded share " }), H("b", { text: isNum(pv.grounded_share) ? pct(pv.grounded_share) : "null" })]));
    col.appendChild(counts);
    var text = answerText(blk, pv.answer_source);
    var values = Array.isArray(pv.values) ? pv.values : [];
    if (!text) col.appendChild(H("p", { class: "dt-note", "data-role": "no-answer", style: { margin: "4px 0" }, text: "The answer's text is not in this report; the section counted " + plural(pv.answer_chars, "character") + "." }));
    else {
      var marked = markAnswer(text, values, function (v) { return v.supported ? colour(sourceOfValue(s, v)) : null; });
      var p = H("p", { class: "dt-answer", "data-role": "answer" });
      marked.segs.forEach(function (sg) {
        if (!sg.v) { p.appendChild(document.createTextNode(sg.text)); return; }
        var src = sourceOfValue(s, sg.v);
        p.appendChild(H("mark", { class: "dt-val" + (sg.v.supported ? "" : " unsup"), "data-value": sg.v.id, "data-supported": sg.v.supported ? "true" : "false", "data-source": src ? src.source : null, style: sg.colour ? { "--c": sg.colour } : null,
          title: String(sg.v.kind) + " " + String(sg.v.value) + (sg.v.supported ? " — carried by " + (src ? src.name + " (step " + src.step + ")" : "a fetched output") + (sg.v.steps && sg.v.steps.length > 1 ? " and " + (sg.v.steps.length - 1) + " more step" + (sg.v.steps.length > 2 ? "s" : "") : "") : " — carried by no fetched output"), text: sg.text }));
      });
      col.appendChild(p);
      if (marked.missing.length) col.appendChild(H("p", { class: "dt-note", "data-role": "not-located", style: { margin: "0 0 4px" }, text: "Not located verbatim in the text (the extractor normalises): " + marked.missing.map(function (v) { return String(v.value) + (v.supported ? "" : " (unsupported)"); }).join(", ") + "." }));
    }
    if (!values.length) col.appendChild(H("p", { class: "dt-note", "data-role": "no-values", style: { margin: "0 0 4px" }, text: "No typed value in this answer: it is prose by the extractor's rule, so its grounded share is null — not 0 and not 1." }));
    // the legend: the sources the answer's values trace to, and the unsupported ones
    var leg = H("ul", { class: "dt-leg", "data-role": "legend" });
    (pv.grounded_in || []).forEach(function (g) {
      var li = H("li", { "data-source": g.source });
      li.appendChild(H("i", { style: { background: colour(g) } }));
      li.appendChild(H("button", { type: "button", "data-source": g.source, text: String(g.name) + " · step " + int(g.step), title: "open this source in “What each agent read”", onclick: function () { ctx.signal("inspect"); select({ source: g.source }); } }));
      li.appendChild(H("span", { class: "n", text: (Array.isArray(g.atoms) ? g.atoms.length : "—") + " of " + int(pv.atoms) + " · overlap " + num(g.overlap, 2) }));
      leg.appendChild(li);
    });
    var unsup = values.filter(function (v) { return !v.supported; });
    if (unsup.length) leg.appendChild(H("li", null, [H("i", { class: "unsup" }), H("span", { text: "unsupported: " + unsup.map(function (v) { return String(v.value); }).join(", ") + " — carried by no fetched output before the answer" })]));
    if (leg.childNodes.length) col.appendChild(leg);
    // the pair's own reading of the same answer, beside: correctness is answer_eval's, not this section's
    var ae = ctx.report && ctx.report.answer_eval, ve = ae && ae[s + "_vs_expected"];
    var rd = d.provenance && d.provenance.readings && d.provenance.readings[s];
    var ev = H("p", { class: "dt-eval", "data-role": "answer-eval" });
    ev.appendChild(H("b", { text: "answer_eval: " }));
    if (ve && ve.verdict) ev.appendChild(H("span", { class: String(ve.verdict), text: String(ve.verdict) + (isNum(ve.coverage) ? " (coverage " + num(ve.coverage, 2) + ")" : "") }));
    else ev.appendChild(H("span", { text: "no expected answer to judge against" }));
    ev.appendChild(H("span", { text: " — grounded means carried by something fetched, not correct." }));
    if (rd && rd.answer_basis) ev.appendChild(H("span", { text: " Cross-check: " + String(rd.answer_basis.source || "reading.answer_basis") + " " + int(rd.answer_basis.supported) + " of " + int(rd.answer_basis.atoms) + (rd.semantic ? "; " + String(rd.semantic.source || "semantic.grounding") + " " + int(rd.semantic.claims_grounded) + " of " + int(rd.semantic.claims_total) : "") + "." }));
    col.appendChild(ev);
    host.appendChild(col);
    return col;
  }

  AgentDiff.block({
    id: "dt-provenance",
    title: "What each answer rests on",
    question: "Each typed value of the answer traced to the fetched output that carries it; the unsupported ones; the pair's own verdict beside, so grounded is never read as correct.",
    group: "data",
    size: "wide",
    relevance: function (ctx) {
      var d = pairData(ctx);
      if (!d) return 0;
      var any = ["a", "b"].some(function (s) { var x = sideOf(d, s); return x && x.provenance && (isNum(x.provenance.answer_chars) && x.provenance.answer_chars > 0 || isNum(x.provenance.atoms) && x.provenance.atoms > 0); });
      return any ? 1 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var d = pairData(ctx);
      if (!d) return ctx.empty(el, "This report carries no data section.");
      var t0 = now();
      var root = H("div", { class: "dt dt-provenance" });
      el.appendChild(root);
      var pa = (sideOf(d, "a") || {}).provenance || {}, pb = (sideOf(d, "b") || {}).provenance || {}, pd = d.provenance || {};
      // one colour per source across both sides, so a shared source is the same colour on each
      var ids = [];
      ["a", "b"].forEach(function (s) { (((sideOf(d, s) || {}).provenance || {}).grounded_in || []).forEach(function (g) { if (g.source && ids.indexOf(g.source) < 0) ids.push(g.source); }); });
      var scale = d3.scaleOrdinal(SOURCE_PALETTE).domain(ids);
      function colour(g) { return g && g.source ? scale(g.source) : "var(--ink-3)"; }
      function sourceOfValue(s, v) {
        var gi = ((sideOf(d, s) || {}).provenance || {}).grounded_in || [];
        return gi.filter(function (g) { return Array.isArray(g.atoms) && g.atoms.indexOf(v.id) >= 0; })[0] || null;
      }
      var lede = H("p", { class: "dt-lede" });
      [["a", pa], ["b", pb]].forEach(function (p, i) {
        if (!sideOf(d, p[0])) return;
        lede.appendChild(H("b", { text: agentOf(d, p[0]) + "'s answer" }));
        lede.appendChild(H("span", { text: isNum(p[1].atoms) && p[1].atoms > 0 ? " carries " + plural(p[1].atoms, "typed value") + ", " + int(p[1].supported) + " carried by a fetched output and " + int(p[1].unsupported) + " not (" + pct(p[1].grounded_share) + " grounded)" : " carries no typed value (grounded share null)" }));
        lede.appendChild(H("span", { text: i === 0 && sideOf(d, "b") ? "; " : ". " }));
      });
      if (isNum(pd.delta_grounded)) lede.appendChild(H("span", { text: "Δ grounded (A − B) " + signed(pd.delta_grounded, 2) + "." }));
      else if (pd.basis) lede.appendChild(H("span", { text: "Δ grounded is null: " + (String(pd.basis).indexOf("null when") >= 0 ? "one answer carries no typed value." : String(pd.basis)) }));
      root.appendChild(lede);
      var bar = H("div", { class: "dt-bar" });
      if (synthOf(d)) bar.appendChild(synChip());
      bar.appendChild(H("span", { class: "dt-chip", text: "underline colour = the fetched source that carries the value · dashed red = carried by nothing fetched · counts, not intervals" }));
      root.appendChild(bar);
      var cols = H("div", { class: "dt-cols" });
      ["a", "b"].forEach(function (s) { if (sideOf(d, s)) provenanceSide(cols, ctx, d, s, colour, sourceOfValue); });
      root.appendChild(cols);
      // table view: every value with its carrying steps
      var vrows = [];
      ["a", "b"].forEach(function (s) {
        var x = sideOf(d, s);
        (((x || {}).provenance || {}).values || []).forEach(function (v) {
          var srcs = (v.steps || []).map(function (i) { var g = ((x.provenance.grounded_in) || []).filter(function (q) { return q.step === i; })[0]; return g ? g.name + " (step " + i + ")" : "step " + i; });
          vrows.push([{ text: agentOf(d, s), cls: "mono" }, { text: String(v.id), cls: "mono" }, { text: String(v.kind) }, { text: String(v.value), cls: "wrap" }, { text: String(v.normalized), cls: "mono dim" }, { text: v.supported ? "supported" : "unsupported", cls: v.supported ? "good" : "bad" }, { text: srcs.join(", ") || "—", cls: "wrap" }]);
        });
      });
      if (vrows.length) root.appendChild(H("details", { class: "dt-details" }, [H("summary", { text: "table view: every typed value with the outputs that carry it" }), table([{ text: "agent" }, { text: "id" }, { text: "kind" }, { text: "value" }, { text: "normalised" }, { text: "status" }, { text: "carried by" }], vrows)]));
      root.appendChild(H("p", { class: "dt-note", text: "Limits of the measure. " + String(pa.basis || pb.basis || "") + " A value carried by a fetched output is supported, not true: a search whose query the agent itself wrote returns text that carries the figure the agent searched for, and that counts; whether two sources corroborate each other independently is the semantic section's independence reading, not this one's; a value carried by a page the agent misread is still carried. An answer of prose has no typed value and its share is null, not 0 or 1. The shares are counts over counts, so there is no interval to draw." }));
      TIMING.provenance = now() - t0;
      root.setAttribute("data-draw-ms", TIMING.provenance.toFixed(1));
    },
  });

  // =============================================================== dt-chain

  function chainModel(d, s) {
    var x = sideOf(d, s), ch = x && x.chain || {};
    var nodes = (Array.isArray(ch.nodes) ? ch.nodes : []).filter(function (n) { return n && n.id; });
    var edges = (Array.isArray(ch.edges) ? ch.edges : []).filter(function (e) { return e && e.from && e.to; });
    if (TILE > 1) {
      var maxStep = d3.max(nodes, function (n) { return isNum(n.step) ? n.step : 0; }) || 0, span = maxStep + 2;
      var bn = nodes.slice(), be = edges.slice();
      for (var k = 1; k < TILE; k++) {
        bn.forEach(function (n) { if (n.kind === "agent") return; nodes.push(Object.assign({}, n, { id: n.id + "~" + k, step: isNum(n.step) ? n.step + k * span : n.step, tiled: true })); });
        be.forEach(function (e) { nodes.length && edges.push(Object.assign({}, e, { from: e.from === "agent" ? "agent" : e.from + "~" + k, to: e.to + "~" + k, step: isNum(e.step) ? e.step + k * span : e.step })); });
      }
    }
    var total = nodes.length, capped = false;
    if (nodes.length > NODE_CAP) {
      nodes = nodes.slice().sort(function (p, q) { return (isNum(p.step) ? p.step : -1) - (isNum(q.step) ? q.step : -1); }).slice(0, NODE_CAP);
      capped = true;
    }
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    edges = edges.filter(function (e) { return byId[e.from] && byId[e.to]; });
    return { side: s, nodes: nodes, edges: edges, byId: byId, reading: String(ch.reading || ""), basis: String(ch.basis || ""), total: total, capped: capped, agent: agentOf(d, s) };
  }
  function edgeLabel(e) { return e.kind + (isNum(e.overlap) ? " · overlap " + num(e.overlap, 2) : e.kind === "feeds" ? " · adjacent, no overlap measured" : ""); }
  function drawChain(host, ctx, m, tip, api) {
    var S = ctx.svg, t0 = now();
    var nodes = m.nodes, edges = m.edges;
    var W = width(host), padL = 60, padR = 24;
    var yA = 22, yM = 74, yD = 146, axisY = yD + 40, Hh = axisY + 24;
    var steps = nodes.filter(function (n) { return isNum(n.step); }).map(function (n) { return n.step; });
    var s0 = d3.min(steps), s1 = d3.max(steps);
    if (!isNum(s0)) { s0 = 0; s1 = 1; }
    if (s1 === s0) s1 = s0 + 1;
    var x = d3.scaleLinear().domain([s0, s1]).range([padL + 26, W - padR]);
    var gap = (W - padR - padL - 26) / Math.max(1, s1 - s0);
    var maxChars = d3.max(nodes, function (n) { return n.kind === "data" && isNum(n.chars) ? n.chars : 0; }) || 1;
    var rMax = Math.max(4, Math.min(12, gap * 0.55));
    function rOf(n) { return n.kind === "data" ? 3 + Math.sqrt((isNum(n.chars) ? n.chars : 0) / maxChars) * (rMax - 3) : n.kind === "model" ? 6 : n.kind === "answer" ? 8 : 9; }
    var pos = {};
    nodes.forEach(function (n) { pos[n.id] = { x: n.kind === "agent" ? padL + 6 : x(n.step), y: n.kind === "agent" ? yA : n.kind === "data" ? yD : yM }; });
    var nData = nodes.filter(function (n) { return n.kind === "data"; }).length, nModel = nodes.filter(function (n) { return n.kind === "model"; }).length;
    var label = "data → model → agent → answer for " + m.agent + ": " + m.reading + " Lanes: agent on top, model steps in the middle with the answer at the right, fetched data at the bottom; x is the step index " + s0 + " to " + s1 + "; a data node's area is proportional to its output characters (largest " + int(maxChars) + "). " + (m.capped ? "The first " + NODE_CAP + " of " + int(m.total) + " nodes are drawn. " : "") + "Feeds edges are drawn as wide as their overlap; a dashed one exists by adjacency alone.";
    var svg = L.svg({ class: "dt-chain-svg", viewBox: "0 0 " + W + " " + Hh, width: W, height: Hh, "aria-label": label });
    // lanes
    [["agent", yA], ["model", yM], ["data", yD]].forEach(function (ln) {
      svg.appendChild(S("line", { class: "rule", x1: padL, x2: W - padR, y1: ln[1], y2: ln[1], "stroke-dasharray": "2 4" }));
      svg.appendChild(S("text", { class: "lab dim mono", x: 2, y: ln[1] + 4, text: ln[0] }));
    });
    // the step axis
    svg.appendChild(S("line", { class: "zero", x1: padL + 26, x2: W - padR, y1: axisY, y2: axisY }));
    var ticks = d3.ticks(s0, s1, Math.max(2, Math.min(12, Math.floor((W - padL) / 60)))).filter(function (t) { return t === Math.round(t); });
    ticks.forEach(function (t) {
      svg.appendChild(S("line", { class: "zero", x1: x(t), x2: x(t), y1: axisY, y2: axisY + 4 }));
      svg.appendChild(S("text", { class: "tick", x: x(t), y: axisY + 16, "text-anchor": "middle", text: String(t) }));
    });
    svg.appendChild(S("text", { class: "lab dim", x: padL, y: axisY + 16, "text-anchor": "end", text: "step" }));
    // edges: produces under, feeds, then reaches on top
    var order = { produces: 0, feeds: 1, reaches: 2 };
    var eg = S("g", { class: "dt-edges" });
    svg.appendChild(eg);
    edges.slice().sort(function (p, q) { return (order[p.kind] || 0) - (order[q.kind] || 0); }).forEach(function (e) {
      var a = pos[e.from], b = pos[e.to];
      if (!a || !b) return;
      var ym = (a.y + b.y) / 2, dpath = "M" + a.x + " " + a.y + " C" + a.x + " " + ym + ", " + b.x + " " + ym + ", " + b.x + " " + b.y;
      var adjacent = e.kind === "feeds" && !isNum(e.overlap);
      var w = e.kind === "produces" ? 1 : 1 + 3 * (isNum(e.overlap) ? e.overlap : 0.15);
      eg.appendChild(S("path", { class: "dt-edge " + e.kind + (adjacent ? " adjacent" : ""), d: dpath, "stroke-width": w.toFixed(2), stroke: e.kind === "reaches" ? sideColour(m.side) : null, "data-from": e.from, "data-to": e.to, "data-kind": e.kind }));
      eg.appendChild(S("path", { class: "dt-hit", d: dpath, "data-edge": e.from + "→" + e.to,
        onmousemove: function (evt) { tip.show(evt, [{ text: e.from + " → " + e.to, b: true }, { text: edgeLabel(e) + " · step " + int(e.step) }, { text: String(e.basis || ""), mono: false }]); },
        onmouseleave: function () { tip.hide(); } }));
    });
    // nodes: one focusable hit per node (the level where a click means one step)
    var ng = S("g", { class: "dt-nodes" });
    svg.appendChild(ng);
    var st = state(), selected = st.side === m.side && isNum(st.step) ? st.step : null;
    var labelled = gap >= 64;
    nodes.forEach(function (n) {
      var p = pos[n.id], r = rOf(n);
      var g = S("g", { class: "dt-node", tabindex: "0", role: "button", "data-id": n.id, "data-kind": n.kind, "data-step": isNum(n.step) ? String(n.step) : null, "aria-pressed": isNum(n.step) && n.step === selected && n.kind !== "agent" ? "true" : "false",
        "aria-label": n.kind + (isNum(n.step) ? " step " + n.step : "") + ": " + String(n.label) + (isNum(n.chars) ? ", " + plural(n.chars, "character") : "") + (isNum(n.tokens) ? ", " + plural(n.tokens, "token") : "") });
      if (n.kind === "data") g.appendChild(S("circle", { cx: p.x, cy: p.y, r: r.toFixed(2), fill: sideColour(m.side), "fill-opacity": 0.85 }));
      else if (n.kind === "model") g.appendChild(S("rect", { x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r, rx: 2, fill: "var(--surface)", stroke: "var(--ink)", "stroke-width": 1.5 }));
      else if (n.kind === "answer") g.appendChild(S("path", { d: "M" + p.x + " " + (p.y - r) + " L" + (p.x + r) + " " + p.y + " L" + p.x + " " + (p.y + r) + " L" + (p.x - r) + " " + p.y + " Z", fill: "var(--ink)" }));
      else g.appendChild(S("circle", { cx: p.x, cy: p.y, r: r, fill: "var(--surface)", stroke: "var(--ink-2)", "stroke-width": 1.5 }));
      g.appendChild(S("circle", { class: "ring", cx: p.x, cy: p.y, r: (r + 3.5).toFixed(2) }));
      if (n.kind === "agent") g.appendChild(S("text", { class: "lab strong backed", x: p.x + r + 5, y: p.y + 4, text: trunc(n.label, Math.max(8, Math.floor((W - padL) / 9))) }));
      else if (n.kind === "answer") g.appendChild(S("text", { class: "lab strong backed", x: p.x, y: p.y - r - 5, "text-anchor": "end", text: "answer" }));
      else if (labelled && n.kind === "data") g.appendChild(S("text", { class: "lab dim backed", x: p.x, y: p.y + r + 13, "text-anchor": "middle", text: trunc(String(n.label).split(":")[0], 12) }));
      else if (labelled && n.kind === "model") g.appendChild(S("text", { class: "lab dim backed mono", x: p.x, y: p.y - r - 5, "text-anchor": "middle", text: trunc(n.label, 14) }));
      function lines() {
        var touching = edges.filter(function (e) { return e.from === n.id || e.to === n.id; });
        var out = [{ text: n.kind + (isNum(n.step) ? " · step " + n.step : ""), b: true }, { text: String(n.label), mono: n.kind === "model" || n.kind === "answer" }];
        if (isNum(n.chars)) out.push({ text: plural(n.chars, "character") });
        if (isNum(n.tokens)) out.push({ text: plural(n.tokens, "token") });
        touching.slice(0, 6).forEach(function (e) { out.push({ text: (e.from === n.id ? "→ " + e.to : "← " + e.from) + " · " + edgeLabel(e) }); });
        if (touching.length > 6) out.push({ text: "… " + (touching.length - 6) + " more edges" });
        return out;
      }
      g.addEventListener("mousemove", function (evt) { tip.show(evt, lines()); });
      g.addEventListener("mouseleave", function () { tip.hide(); });
      function pick() { if (n.kind === "agent") return; ctx.signal("inspect"); api.open(n); }
      g.addEventListener("click", pick);
      g.addEventListener("keydown", function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); evt.stopPropagation(); pick(); } });
      ng.appendChild(g);
    });
    host.appendChild(svg);
    var ms = now() - t0;
    host.setAttribute("data-draw-ms", ms.toFixed(1));
    host.setAttribute("data-nodes", String(nodes.length));
    host.setAttribute("data-edges", String(edges.length));
    return { ms: ms, ordered: nodes.filter(function (n) { return n.kind !== "agent"; }).sort(function (p, q) { return p.step - q.step || (p.kind === "data" ? -1 : 1); }) };
  }
  function stepPanel(host, ctx, d, m, animate) {
    host.innerHTML = "";
    var st = state();
    if (st.side !== m.side || !isNum(st.step)) { host.appendChild(H("p", { class: "dt-note", "data-role": "hint", style: { margin: "6px 0 0" }, text: "Click a node (or focus the chart, move with ← →, press Enter) for the step's text — its input, its output and the edges that touch it." })); return; }
    var blk = runBlock(ctx, m.side), step = stepAt(blk, st.step);
    var node = m.nodes.filter(function (n) { return isNum(n.step) && n.step === st.step && n.kind !== "agent"; })[0];
    var panel = H("div", { class: "dt-detail", "data-step": String(st.step), "data-side": m.side });
    panel.appendChild(H("div", { class: "dt-h", text: "step " + int(st.step) + " · " + (node ? node.kind + " · " : "") + (step ? String(step.type || "") + " " + String(step.name || "") : "not in this report") }));
    if (step) {
      var model = step.model && typeof step.model === "object" ? (step.model.model || step.model.name) : null;
      panel.appendChild(H("p", { class: "dt-read", style: { margin: "0 0 4px" }, text: (model ? "model " + String(model) + " (steps[].model)" : node && node.kind !== "data" ? "model " + String(node.label) + " (" + String((((sideOf(d, m.side) || {}).models || [])[0] || {}).source || "trace.agent.model") + ")" : "a fetched output") + (isNum(step.tokens) ? " · " + plural(step.tokens, "token") : "") + (isNum(step.latency_s) ? " · " + L.fmt.secs(step.latency_s) : "") + (step.error ? " · error: " + String(step.error) : "") + "." }));
      var cols = H("div", { class: "dt-cols" });
      cols.appendChild(H("div", null, [H("div", { class: "dt-h", text: "input" }), textBlock(step.input, "input", "steps[" + int(st.step) + "].input")]));
      cols.appendChild(H("div", null, [H("div", { class: "dt-h", text: "output" }), textBlock(step.output, "output", "steps[" + int(st.step) + "].output")]));
      panel.appendChild(cols);
    } else panel.appendChild(H("p", { class: "dt-note", style: { margin: 0 }, text: "The report does not carry this step's text; it is in the trace." }));
    var touching = m.edges.filter(function (e) { return node && (e.from === node.id || e.to === node.id); });
    if (touching.length) {
      panel.appendChild(H("div", { class: "dt-h", text: "edges" }));
      panel.appendChild(H("ul", { class: "dt-list", style: { fontFamily: "var(--sans)" } }, touching.map(function (e) { return H("li", { text: e.from + " → " + e.to + " · " + edgeLabel(e) + " · " + String(e.basis || "") }); })));
    }
    host.appendChild(panel);
    if (animate) fadeIn(panel);
  }

  AgentDiff.block({
    id: "dt-chain",
    title: "Data → model → agent → answer",
    question: "For one run, which fetched output fed which model step and which reached the answer, with the overlap on every edge.",
    group: "data",
    size: "wide",
    relevance: function (ctx) {
      var d = pairData(ctx);
      if (!d) return 0;
      return ["a", "b"].some(function (s) { var m = chainModel(d, s); return m.nodes.length > 1; }) ? 1 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var d = pairData(ctx);
      if (!d) return ctx.empty(el, "This report carries no data section.");
      var t0 = now();
      var root = H("div", { class: "dt dt-chain" });
      el.appendChild(root);
      var side = sideSel(d), m = chainModel(d, side);
      // one line per run: the section's reading
      ["a", "b"].forEach(function (s) {
        var x = sideOf(d, s);
        if (!x) return;
        root.appendChild(H("p", { class: "dt-read", "data-side": s, style: { margin: "0 0 3px" } }, [sideLabel(d, s), H("span", { text: ": " + String((x.chain || {}).reading || "no chain") })]));
      });
      var bar = H("div", { class: "dt-bar", style: { marginTop: "6px" } });
      if (synthOf(d)) bar.appendChild(synChip());
      var seg = H("div", { class: "dt-seg", role: "group", "aria-label": "which run to draw" });
      ["a", "b"].forEach(function (s) {
        if (!sideOf(d, s)) return;
        seg.appendChild(H("button", { type: "button", class: "dt-btn", "data-side": s, "aria-pressed": s === side ? "true" : "false", text: agentOf(d, s), style: { marginRight: "4px" }, onclick: function () { if (s !== sideSel(d)) select({ side: s, step: null }); } }));
      });
      bar.appendChild(seg);
      bar.appendChild(H("span", { class: "dt-chip" }, [H("i", { style: { background: "var(--ink-3)" } }), H("span", { text: "produces" })]));
      bar.appendChild(H("span", { class: "dt-chip" }, [H("i", { style: { background: "var(--accent)" } }), H("span", { text: "feeds (width ∝ overlap; dashed = adjacent only)" })]));
      bar.appendChild(H("span", { class: "dt-chip" }, [H("i", { style: { background: sideColour(side) } }), H("span", { text: "reaches the answer (width ∝ share of typed values)" })]));
      bar.appendChild(H("span", { class: "dt-chip", text: "● data (area ∝ chars) · ■ model · ◆ answer" }));
      root.appendChild(bar);
      var status = H("p", { class: "dt-status", role: "status", "aria-live": "polite" });
      root.appendChild(status);
      var stage = H("div", { class: "dt-stage", role: "application", tabindex: "0", "data-level": "chain" });
      root.appendChild(stage);
      var chart = H("div", { class: "dt-chart", style: { position: "relative" } });
      stage.appendChild(chart);
      var tip = L.svg.tip(chart, { width: 340 });
      var drawn = null, cursor = null;
      var api = { open: function (n) { select({ side: m.side, step: state().step === n.step && state().side === m.side ? null : n.step }); } };
      function statusText() {
        var st = state(), sel = st.side === m.side && isNum(st.step) ? st.step : null;
        status.textContent = "Chain level, " + m.agent + ": " + plural(m.nodes.filter(function (n) { return n.kind === "data"; }).length, "data node") + ", " + plural(m.nodes.filter(function (n) { return n.kind === "model"; }).length, "model node") + ", " + plural(m.edges.length, "edge") + (m.capped ? " (the first " + NODE_CAP + " of " + int(m.total) + " nodes drawn)" : "") + (TILE > 1 ? ", tiled ×" + TILE + " for measurement" : "") + (sel !== null ? "; step " + sel + " open" : "; no step open") + ". ← → move between nodes, Enter opens a step, Escape closes it.";
        stage.setAttribute("aria-label", "the chain of " + m.agent + ", chain level. " + status.textContent);
      }
      function paint() { chart.innerHTML = ""; drawn = drawChain(chart, ctx, m, tip, api); chart.appendChild(tip.node); }
      L.layout.responsive(chart, paint, "dt-chain");
      statusText();
      var panel = H("div", { class: "dt-step-host" });
      root.appendChild(panel);
      stepPanel(panel, ctx, d, m, false);
      stage.addEventListener("keydown", function (evt) {
        if (!drawn) return;
        var list = drawn.ordered, n = list.length;
        if (evt.key === "ArrowRight" || evt.key === "ArrowLeft") {
          if (!n) return;
          cursor = cursor === null ? (evt.key === "ArrowRight" ? 0 : n - 1) : Math.max(0, Math.min(n - 1, cursor + (evt.key === "ArrowRight" ? 1 : -1)));
          var g = chart.querySelector('.dt-node[data-id="' + list[cursor].id + '"]');
          if (g) g.focus();
          evt.preventDefault();
        } else if (evt.key === "Enter" && cursor !== null && evt.target === stage) { api.open(list[cursor]); evt.preventDefault(); }
        else if (evt.key === "Escape" && state().side === m.side && isNum(state().step)) { select({ step: null }); evt.preventDefault(); }
      });
      listen(root, function (s2) {
        if (sideSel(d) !== m.side) { if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); return; }
        var gs = chart.querySelectorAll(".dt-node");
        for (var i = 0; i < gs.length; i++) { var stp = gs[i].getAttribute("data-step"); gs[i].setAttribute("aria-pressed", gs[i].getAttribute("data-kind") !== "agent" && stp !== null && isNum(s2.step) && parseInt(stp, 10) === s2.step ? "true" : "false"); }
        statusText();
        stepPanel(panel, ctx, d, m, true);
      });
      // table view: the nodes and the edges
      var nrows = m.nodes.map(function (n) { return [{ text: String(n.id), cls: "mono" }, { text: String(n.kind) }, { text: isNum(n.step) ? String(n.step) : "—", cls: "num" }, { text: String(n.label), cls: "wrap" }, { text: isNum(n.chars) ? int(n.chars) : "—", cls: "num" }, { text: isNum(n.tokens) ? int(n.tokens) : "—", cls: "num" }]; });
      var erows = m.edges.map(function (e) { return [{ text: String(e.from), cls: "mono" }, { text: String(e.to), cls: "mono" }, { text: String(e.kind) }, { text: isNum(e.step) ? String(e.step) : "—", cls: "num" }, { text: isNum(e.overlap) ? num(e.overlap, 4) : "null", cls: isNum(e.overlap) ? "num" : "num dim" }, { text: String(e.basis || ""), cls: "wrap" }]; });
      root.appendChild(H("details", { class: "dt-details", "data-role": "tables" }, [H("summary", { text: "table view: " + plural(m.nodes.length, "node") + " and " + plural(m.edges.length, "edge") + " of " + m.agent }),
        H("div", { class: "dt-h", text: "nodes" }), table([{ text: "id" }, { text: "kind" }, { text: "step", num: true }, { text: "label" }, { text: "chars", num: true }, { text: "tokens", num: true }], nrows),
        H("div", { class: "dt-h", text: "edges" }), table([{ text: "from" }, { text: "to" }, { text: "kind" }, { text: "step", num: true }, { text: "overlap", num: true }, { text: "basis" }], erows)]));
      root.appendChild(H("p", { class: "dt-note", text: "How the edges are drawn: " + m.basis + ". The chain overlap counts tokens, common words included, above three characters: it says a model step shares content with a fetched output, not that the output caused the step; adjacency draws an edge with no overlap at all and is labelled so. The reaches edges carry the provenance overlap (typed values), a different measure." }));
      TIMING.chain = now() - t0;
      root.setAttribute("data-draw-ms", TIMING.chain.toFixed(1));
    },
  });

  // =========================================================== dt-evolution

  function toolPairs(before, after) {
    before = before && typeof before === "object" ? before : {}; after = after && typeof after === "object" ? after : {};
    var names = {};
    Object.keys(before).forEach(function (k) { names[k] = 1; }); Object.keys(after).forEach(function (k) { names[k] = 1; });
    return Object.keys(names).map(function (k) { var b = isNum(before[k]) ? before[k] : 0, a = isNum(after[k]) ? after[k] : 0; return { name: k, before: b, after: a, delta: a - b }; })
      .sort(function (p, q) { return Math.abs(q.delta) - Math.abs(p.delta) || (p.name < q.name ? -1 : 1); });
  }
  function pairSpan(label, before, after, fmtv) {
    return H("span", { class: "pair" }, [H("span", { text: label + " " }), H("b", { text: fmtv(before) }), H("span", { class: "arr", text: " → " }), H("b", { text: fmtv(after) })]);
  }
  function effectSvg(ctx, s) {
    var S = ctx.svg, W = 150, Hh = 36, x = d3.scaleLinear().domain([0, 1]).range([8, W - 8]);
    var eff = s.effect || {}, imp = eff.improvement || {}, imps = eff.improvement_success || {};
    var label = "the effect of " + stepKey(s) + ": P(" + s.to + " > " + s.from + ") on return " + ci(imp, 2) + ", on success " + ci(imps, 2) + ", verdict " + String(eff.verdict || "—");
    var svg = L.svg({ class: "dt-effect", viewBox: "0 0 " + W + " " + Hh, width: W, height: Hh, "aria-label": label });
    svg.appendChild(S("line", { class: "zero", x1: x(0.5), x2: x(0.5), y1: 4, y2: Hh - 10 }));
    [0, 0.5, 1].forEach(function (t) { svg.appendChild(S("text", { class: "tick", x: x(t), y: Hh - 1, "text-anchor": t === 0 ? "start" : t === 1 ? "end" : "middle", text: t === 0.5 ? ".5" : String(t) })); });
    var g = S("g");
    svg.appendChild(g);
    if (isNum(imp.point)) L.glyph.interval(g, x, imp.point, imp.lo, imp.hi, { y: 9, color: L.color.verdict(eff.verdict === "improved" ? "good" : eff.verdict === "regressed" || eff.verdict === "gamed" || eff.verdict === "forgot" ? "bad" : "tie"), r: 3.2, tick: 3 });
    if (isNum(imps.point)) L.glyph.interval(g, x, imps.point, imps.lo, imps.hi, { y: 21, color: "var(--ink-3)", r: 2.6, tick: 3 });
    return svg;
  }
  function growthChart(host, ctx, de) {
    var S = ctx.svg, gens = de.generations || [];
    var W = width(host), padL = 44, padR = 8, top = 16, Hh = 96, base = Hh - 20;
    var chars = gens.map(function (g) { return g.instructions && isNum(g.instructions.chars) ? g.instructions.chars : 0; });
    var maxC = d3.max(chars) || 1;
    var x = d3.scaleBand().domain(gens.map(function (g) { return String(g.id); })).range([padL, W - padR]).paddingInner(0.25).paddingOuter(0.1);
    var y = d3.scaleLinear().domain([0, maxC]).range([base, top]);
    var label = "instruction length by generation: " + gens.map(function (g, i) { return g.id + " " + int(chars[i]); }).join(", ") + " characters, from the lineage artifacts";
    var svg = L.svg({ class: "dt-growth", viewBox: "0 0 " + W + " " + Hh, width: W, height: Hh, "aria-label": label });
    svg.appendChild(S("line", { class: "zero", x1: padL, x2: W - padR, y1: base, y2: base }));
    svg.appendChild(S("text", { class: "tick", x: padL - 4, y: base + 4, "text-anchor": "end", text: "0" }));
    svg.appendChild(S("text", { class: "tick", x: padL - 4, y: top + 4, "text-anchor": "end", text: int(maxC) }));
    gens.forEach(function (g, i) {
      var x0 = x(String(g.id)), w = x.bandwidth();
      svg.appendChild(S("rect", { x: x0, y: y(chars[i]), width: w, height: Math.max(0, base - y(chars[i])), fill: "var(--ink-2)", "fill-opacity": 0.55, rx: 1.5 }));
      if (w >= 34) svg.appendChild(S("text", { class: "tick backed", x: x0 + w / 2, y: y(chars[i]) - 3, "text-anchor": "middle", text: int(chars[i]) }));
      svg.appendChild(S("text", { class: "lab mono", x: x0 + w / 2, y: base + 14, "text-anchor": "middle", text: String(g.id) }));
    });
    svg.appendChild(S("text", { class: "lab dim", x: padL, y: 10, text: "instructions · characters" }));
    host.appendChild(svg);
  }
  function evolutionPanel(host, ctx, de, s, names) {
    var panel = H("div", { class: "dt-epanel", "data-gen": stepKey(s) });
    var cols = H("div", { class: "dt-cols" });
    // what changed
    var left = H("div");
    var ch = s.change || {};
    left.appendChild(H("div", { class: "dt-h", style: { marginTop: 0 }, text: "what changed · " + String(ch.summary || "") }));
    if (Array.isArray(ch.protected_touched) && ch.protected_touched.length) left.appendChild(H("p", { class: "dt-note", "data-role": "protected", style: { margin: "0 0 6px", color: "var(--bad)" }, text: "▏Touched " + plural(ch.protected_touched.length, "protected path") + ": " + ch.protected_touched.join(", ") + " — the agent edited what judges it." }));
    if (Array.isArray(ch.hunks) && ch.hunks.length) { left.appendChild(H("div", { class: "dt-h", text: "prompt · +" + int(ch.prompt_added) + " −" + int(ch.prompt_removed) + " lines" })); left.appendChild(diffLines(ch.hunks)); }
    else left.appendChild(H("p", { class: "dt-note", style: { margin: "0 0 6px" }, text: "No prompt hunk on this step." }));
    var ul = H("ul", { class: "dt-list" });
    (ch.rules_added || []).forEach(function (r) { ul.appendChild(H("li", { class: "add", text: "+ rule: " + String(r) })); });
    (ch.rules_removed || []).forEach(function (r) { ul.appendChild(H("li", { class: "del", text: "− rule: " + String(r) })); });
    (ch.config_changed || []).forEach(function (c) { ul.appendChild(H("li", { class: "chg" }, [H("span", { text: "~ config." + String(c.key) + ": " + String(c.from) + " → " + String(c.to) + (c.added ? " (added)" : c.removed ? " (removed)" : "") }), (ch.protected_touched || []).indexOf("config." + c.key) >= 0 ? H("span", { class: "dt-prot", text: "▏protected" }) : null])); });
    if (ul.childNodes.length) left.appendChild(ul);
    cols.appendChild(left);
    // the evidence: the episodes and what they read
    var right = H("div");
    var ev = s.evidence || {}, data = Array.isArray(ev.data) ? ev.data : [];
    right.appendChild(H("div", { class: "dt-h", style: { marginTop: 0 }, text: "the evidence · " + plural((ev.episodes || []).length, "episode") + " cited, " + int(ev.found) + " found, " + int(ev.failures) + " failures" }));
    if (ev.summary) right.appendChild(H("p", { class: "dt-read", style: { margin: "0 0 6px" }, text: cap(String(ev.summary)) + "." }));
    var epl = H("ul", { class: "dt-eplist", "data-role": "episodes" });
    var named = 0, unnamed = 0;
    data.forEach(function (e) {
      var li = H("li", { "data-episode": String(e.episode) });
      li.appendChild(H("b", { text: String(e.episode) }));
      li.appendChild(H("span", { text: e.found === false ? " · not found in the parent" : " · " + (e.success === true ? "success" : e.success === false ? "failure" : "outcome unknown") + " · " + plural((e.sources || []).length, "distinct source") + " in " + plural(e.fetches, "fetch") + " · grounded " + (isNum(e.grounded_share) ? pct(e.grounded_share) : "null (no typed value)") }));
      var srcs = Array.isArray(e.sources) ? e.sources : [];
      if (srcs.length) {
        var shown = srcs.slice(0, EP_SOURCES).map(function (id) { var nm = names[id]; if (nm) { named++; return nm.name + " " + trunc(nm.input, 40); } unnamed++; return String(id).replace(/^sha256:/, "").slice(0, 8); });
        li.appendChild(H("div", { class: "srcs", text: shown.join(" · ") + (srcs.length > EP_SOURCES ? " · … " + (srcs.length - EP_SOURCES) + " more" : "") }));
      }
      epl.appendChild(li);
    });
    right.appendChild(epl);
    if (data.length) right.appendChild(H("p", { class: "dt-note", "data-role": "names-basis", style: { margin: "2px 0 0" }, text: "Sources are ids (tool name + normalised input, hashed); " + (named ? int(named) + " named from this page's pair reports, where the same id was read" : "none of them is read by this page's pair reports") + (unnamed ? ", " + int(unnamed) + " known by id only — their names are in the traces" : "") + "." }));
    cols.appendChild(right);
    panel.appendChild(cols);
    // the evals
    var e2 = s.eval || {};
    panel.appendChild(H("div", { class: "dt-h", text: "the evals" }));
    panel.appendChild(H("p", { class: "dt-read", style: { margin: 0 }, text: "Base eval: " + String((s.effect || {}).verdict || "—") + ((s.effect || {}).flags && s.effect.flags.length ? " (" + s.effect.flags.join(", ") + ")" : "") + ". " + (e2.reading ? cap(String(e2.reading)) + "." : "The evolved eval is not on this page.") }));
    if (s.reading) panel.appendChild(H("p", { class: "dt-read", "data-role": "reading", text: cap(String(s.reading)) + (String(s.reading).slice(-1) === "." ? "" : ".") }));
    return panel;
  }

  AgentDiff.block({
    id: "dt-evolution",
    title: "How the agent evolves",
    question: "For a lineage, each step from the data its evidence episodes read, through the prompt hunks, to the behaviour shift, the effect and the evolved eval's flags.",
    group: "data",
    size: "wide",
    relevance: function (ctx) { return lineageOf(ctx) ? 1 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      H = ctx.h;
      var de = lineageOf(ctx);
      if (!de) return ctx.empty(el, "No lineage on this page.");
      var t0 = now();
      var root = H("div", { class: "dt dt-evolution" });
      el.appendChild(root);
      var names = nameIndex(ctx), gens = Array.isArray(de.generations) ? de.generations : [];
      var first = gens[0], last = gens[gens.length - 1];
      var lede = H("p", { class: "dt-lede" });
      lede.appendChild(H("b", { text: String(de.family || "the lineage") }));
      lede.appendChild(H("span", { text: " over " + plural(de.steps.length, "step") + " and " + plural(gens.length, "generation") + (first && last ? ": the instructions grow " + int(first.instructions && first.instructions.chars) + " → " + int(last.instructions && last.instructions.chars) + " characters" : "") + "; the verdicts run " + de.steps.map(function (s) { return String((s.effect || {}).verdict || "—"); }).join(", ") + "." }));
      root.appendChild(lede);
      var bar = H("div", { class: "dt-bar" });
      if (de.synthetic) bar.appendChild(synChip());
      bar.appendChild(H("span", { class: "dt-chip", text: "▲ evolved-eval flag (accent: learned) · effect: ● P(child > parent) on return, ○ on success, each with its interval, .5 the coin flip" }));
      root.appendChild(bar);
      var growth = H("div", { class: "dt-chart" });
      root.appendChild(L.layout.responsive(growth, function () { growth.innerHTML = ""; growthChart(growth, ctx, de); }, "dt-growth"));
      // generations table behind a fold
      root.appendChild(H("details", { class: "dt-details", "data-role": "generations" }, [H("summary", { text: "table view: every generation's instructions" }),
        table([{ text: "generation" }, { text: "chars", num: true }, { text: "rules", num: true }, { text: "skills" }, { text: "tools" }, { text: "memory", num: true }, { text: "config" }, { text: "source" }], gens.map(function (g) {
          var ins = g.instructions || {};
          return [{ text: String(g.id), cls: "mono" }, { text: int(ins.chars), cls: "num" }, { text: int((ins.rules || []).length), cls: "num" }, { text: (ins.skills || []).join(", "), cls: "wrap" }, { text: (ins.tools || []).join(", "), cls: "wrap" }, { text: int(ins.memory_n), cls: "num" }, { text: ins.config && typeof ins.config === "object" ? Object.keys(ins.config).map(function (k) { return k + " " + String(ins.config[k]); }).join(", ") : "—", cls: "wrap" }, { text: String(ins.source || "—"), cls: "dim" }];
        }))]));
      // the rows
      var rows = H("div", { class: "dt-rows", role: "list" });
      var head = H("div", { class: "dt-erow head", "aria-hidden": "true" }, ["step", "evidence · what they read", "change", "behaviour · before → after", "effect", "evolved eval"].map(function (t) { return H("span", { class: "cell", text: t }); }));
      rows.appendChild(head);
      var open = genSel(de);
      de.steps.forEach(function (s) {
        var key = stepKey(s), ev = s.evidence || {}, ch = s.change || {}, be = s.behaviour || {}, eff = s.effect || {}, e2 = s.eval || {};
        var srcSet = {};
        (ev.data || []).forEach(function (e) { (e.sources || []).forEach(function (id) { srcSet[id] = 1; }); });
        var grounded = (ev.data || []).map(function (e) { return e.grounded_share; }).filter(isNum);
        var row = H("div", { class: "dt-erow", role: "listitem", tabindex: "0", "data-gen": key, "aria-expanded": open === key ? "true" : "false", "aria-label": "step " + key + ", " + String(eff.verdict || "") });
        row.appendChild(H("span", { class: "cell mono" }, [H("span", { class: "k", text: "step" }), H("b", { text: key })]));
        row.appendChild(H("span", { class: "cell" }, [H("span", { class: "k", text: "evidence" }), H("b", { text: plural((ev.episodes || []).length, "episode") }), H("span", { text: " (" + int(ev.found) + " found, " + int(ev.failures) + " failures) read " + plural(Object.keys(srcSet).length, "distinct source") + (grounded.length ? ", grounded " + pct(d3.mean(grounded)) + " over " + grounded.length : ", no typed value") })]));
        row.appendChild(H("span", { class: "cell" }, [H("span", { class: "k", text: "change" }), H("span", { text: String(ch.summary || "no diff") + " · " + plural((ch.hunks || []).length, "hunk") }), Array.isArray(ch.protected_touched) && ch.protected_touched.length ? H("span", { class: "dt-prot", text: "▏protected" }) : null]));
        var beh = H("span", { class: "cell" }, H("span", { class: "k", text: "behaviour" }));
        var pairs = toolPairs(be.tools_before, be.tools_after).filter(function (p) { return p.delta !== 0; }).slice(0, 3);
        pairs.forEach(function (p, i) { beh.appendChild(pairSpan(p.name, p.before, p.after, int)); beh.appendChild(H("span", { text: "; " })); });
        if (!pairs.length) beh.appendChild(H("span", { text: "tool calls unchanged; " }));
        beh.appendChild(pairSpan("sources", be.sources_before, be.sources_after, int)); beh.appendChild(H("span", { text: "; " }));
        beh.appendChild(pairSpan("grounded", be.grounded_before, be.grounded_after, function (v) { return isNum(v) ? pct(v) : "null"; }));
        if (be.grounded_runs) beh.appendChild(H("span", { class: "mono", style: { color: "var(--ink-3)" }, text: " (" + int(be.grounded_runs.before) + "/" + int(be.grounded_runs.after) + " runs)" }));
        row.appendChild(beh);
        var effc = H("span", { class: "cell" }, [H("span", { class: "k", text: "effect" }), H("span", { class: "v", style: { color: L.color.verdict(eff.verdict === "improved" ? "good" : eff.verdict === "regressed" || eff.verdict === "gamed" || eff.verdict === "forgot" ? "bad" : eff.verdict === "flat" ? "tie" : "") } }, [H("span", { class: "g", "aria-hidden": "true", text: VERDICT_GLYPH[eff.verdict] || "·" }), H("span", { text: String(eff.verdict || "—") })]), (eff.flags || []).length ? H("span", { class: "mono", style: { color: "var(--ink-3)" }, text: " " + eff.flags.join(", ") }) : null]);
        effc.appendChild(effectSvg(ctx, s));
        effc.appendChild(H("span", { class: "mono", style: { color: "var(--ink-3)" }, text: "P " + ci(eff.improvement, 2) + " · success " + ci(eff.improvement_success, 2) }));
        row.appendChild(effc);
        var evc = H("span", { class: "cell" }, H("span", { class: "k", text: "eval" }));
        if (Array.isArray(e2.flags) && e2.flags.length) e2.flags.forEach(function (f) { evc.appendChild(H("span", { class: "dt-flag " + (f.learned ? "learned" : "base"), title: String(f.metric) + " " + ci(f.delta, 2) + (f.learned ? " (learned)" : " (base)"), text: "▲ " + String(f.metric) + " " + (f.delta && isNum(f.delta.point) ? signed(f.delta.point, 2) : "") })); });
        else evc.appendChild(H("span", { class: "dt-flag none", text: e2.source ? "no evolved flag" : "no evolved eval on this page" }));
        if (e2.eval_gen) evc.appendChild(H("span", { class: "mono", text: "→ " + String(e2.eval_gen) }));
        row.appendChild(evc);
        function toggle() { ctx.signal("expand"); select({ gen: genSel(de) === key ? null : key }); }
        row.addEventListener("click", toggle);
        row.addEventListener("keydown", function (evt) {
          if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); toggle(); }
          else if (evt.key === "Escape" && genSel(de) === key) { evt.preventDefault(); select({ gen: null }); }
          else if (evt.key === "ArrowDown" || evt.key === "ArrowUp") { var sib = evt.key === "ArrowDown" ? row.nextElementSibling : row.previousElementSibling; while (sib && !sib.classList.contains("dt-erow")) sib = evt.key === "ArrowDown" ? sib.nextElementSibling : sib.previousElementSibling; if (sib && !sib.classList.contains("head")) { sib.focus(); evt.preventDefault(); } }
        });
        rows.appendChild(row);
        if (open === key) rows.appendChild(evolutionPanel(rows, ctx, de, s, names));
      });
      root.appendChild(rows);
      listen(root, function () {
        var key = genSel(de);
        var rs = rows.querySelectorAll(".dt-erow:not(.head)");
        for (var i = 0; i < rs.length; i++) rs[i].setAttribute("aria-expanded", rs[i].getAttribute("data-gen") === key ? "true" : "false");
        var old = rows.querySelector(".dt-epanel");
        if (old && old.getAttribute("data-gen") === key) return;
        if (old) old.parentNode.removeChild(old);
        if (key) {
          var s = de.steps.filter(function (q) { return stepKey(q) === key; })[0], row = rows.querySelector('.dt-erow[data-gen="' + key + '"]');
          if (s && row) { var p = evolutionPanel(rows, ctx, de, s, names); row.parentNode.insertBefore(p, row.nextSibling); fadeIn(p); }
        }
      });
      // the table view: every step's numbers
      root.appendChild(H("details", { class: "dt-details", "data-role": "steps-table" }, [H("summary", { text: "table view: every step's numbers" }),
        table([{ text: "step" }, { text: "episodes", num: true }, { text: "found", num: true }, { text: "failures", num: true }, { text: "hunks", num: true }, { text: "sources before", num: true }, { text: "after", num: true }, { text: "grounded before" }, { text: "after" }, { text: "verdict" }, { text: "P(child > parent)" }, { text: "on success" }, { text: "evolved flags" }, { text: "eval gen" }], de.steps.map(function (s) {
          var ev = s.evidence || {}, ch = s.change || {}, be = s.behaviour || {}, eff = s.effect || {}, e2 = s.eval || {};
          return [{ text: stepKey(s), cls: "mono" }, { text: int((ev.episodes || []).length), cls: "num" }, { text: int(ev.found), cls: "num" }, { text: int(ev.failures), cls: "num" }, { text: int((ch.hunks || []).length), cls: "num" }, { text: int(be.sources_before), cls: "num" }, { text: int(be.sources_after), cls: "num" }, { text: isNum(be.grounded_before) ? pct(be.grounded_before) : "null" }, { text: isNum(be.grounded_after) ? pct(be.grounded_after) : "null" }, { text: String(eff.verdict || "—") }, { text: ci(eff.improvement, 2), cls: "num" }, { text: ci(eff.improvement_success, 2), cls: "num" }, { text: (e2.flags || []).map(function (f) { return f.metric + " " + ci(f.delta, 2); }).join("; ") || "—", cls: "wrap" }, { text: String(e2.eval_gen || "—"), cls: "mono" }];
        }))]));
      root.appendChild(H("p", { class: "dt-note", text: (de.narrative ? cap(String(de.narrative)) + " " : "") + "Each row is the section's: the episodes from evolution.steps[].evidence_check with the parent's traces read for their sources; the change from evolution.steps[].diff; the behaviour from the generations' tool calls and traces (" + String((de.steps[0].behaviour || {}).basis || "") + "); the effect from evolution.steps[] (P(child > parent) with its interval, on return and on success); the evolved eval from coevolution.steps[].evolved. Grounded shares are means over the episodes whose answer carries a typed value, and say over how many. Click a row for the full diff and the episodes' sources; ↑ ↓ move between rows, Enter opens, Escape closes." }));
      TIMING.evolution = now() - t0;
      root.setAttribute("data-draw-ms", TIMING.evolution.toFixed(1));
    },
  });
})(typeof window !== "undefined" ? window : this);
