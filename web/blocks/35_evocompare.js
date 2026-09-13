/* AgentDiff blocks — the Evolution view: two self-evolving agents compared as
 * evolution processes.
 *
 * Two lineages ran the same tasks for some generations. "Which is better"
 * has no single answer, and these blocks do not pretend it does: there are
 * four axes and four answers, then the details each answer rests on. Every
 * point, bound, count and sentence is read from `aggregate.evolution_compare`
 * (deepcompare/evolvecompare.py); nothing is recomputed in the browser, so
 * the page and the engine cannot drift:
 *
 *   evc-curves      both lineages' task-stratified IQM per generation on one
 *                   axis with their bootstrap bands, the zero line, the race
 *                   threshold as a faint rule (its source stated), the
 *                   generation that first reached it ringed, the recommended
 *                   one filled; x is the generation index or the cumulative
 *                   episodes — the honest measure when the runs differ — with
 *                   a transition between the two
 *   evc-verdict     peak, final, learning, process: for each the lineage the
 *                   engine named or "does not separate", on one shared "who"
 *                   axis, with the number that decided it
 *   evc-process     how soundly each evolved: the verdicts of its steps as a
 *                   stacked bar, its findings beside it, every raw number in
 *                   a table under it so a reader can disagree with the order
 *   evc-pair        the two recommended (or the two last) generations head to
 *                   head: P(improve) against the coin flip, the per-task
 *                   deltas as a dot plot with both pass rates
 *   evc-race        tasks × generations, both lineages in every cell, the
 *                   first solver named at the row's edge, tasks never solved
 *                   marked
 *   evc-mechanisms  which kind of self-modification paid, per lineage
 *   evc-divergence  the behaviour distance between A@k and B@k, and
 *                   P(B@k > A@k) with its band, per generation: converging
 *                   or diverging
 *
 * Selection (the generation index, the x measure, the pair mode) is one
 * module-level store persisted through the page's own Store; a change
 * re-paints every mounted block in place, the way the lineage blocks do,
 * with a transition where a shape moves (none under reduced motion).
 *
 * The shared library (`AgentDiff.lib`, 01_lib.js) is used for formatting,
 * intervals, layout and the family store when it is on the page; until it
 * lands every helper below falls back to a local one, so this file works
 * both before and after.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;
  //: the shared library, when it has landed; every helper falls back to a local one until then
  var L = AgentDiff.lib && typeof AgentDiff.lib === "object" ? AgentDiff.lib : null;

  var PREF_KEY = "agentdiff:evolution-compare";
  //: the transition when a selection or the x measure moves; none under reduced motion
  var DUR = 260;
  //: ~6.2px per character at the page's small size; good enough to truncate by
  var CH = 6.2;

  var CSS = [
    ".evc{position:relative}",
    ".evc svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
    ".evc text{font-size:var(--fs-xs)}",
    ".evc .lab{fill:var(--ink-2)}.evc .lab.dim{fill:var(--ink-3)}.evc .lab.mono{font-family:var(--mono)}.evc .lab.strong{fill:var(--ink);font-weight:600}",
    ".evc .tick{fill:var(--ink-3);font-variant-numeric:tabular-nums;font-family:var(--mono)}",
    ".evc .zero{stroke:var(--rule-2)}.evc .rule{stroke:var(--rule)}",
    ".evc .backed{paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
    ".evc .hit{cursor:pointer}.evc .hit:focus-visible{outline:none}",
    ".evc-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 6px;max-width:96ch}.evc-lede b{font-weight:600}",
    ".evc-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
    ".evc-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
    ".evc-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;max-width:100%;overflow-wrap:anywhere}",
    ".evc-chip b{color:var(--ink);font-weight:600}",
    ".evc-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer}",
    ".evc-bar button:hover{color:var(--ink)}.evc-bar button[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
    ".evc-bar button:disabled{opacity:.4;cursor:default}",
    ".evc-seg{display:inline-flex;gap:2px;flex-wrap:wrap;max-width:100%}",
    ".evc-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:96ch;line-height:1.5}",
    ".evc-read{font-size:var(--fs-xs);color:var(--ink-2);margin:6px 0 0;line-height:1.5;max-width:96ch}.evc-read b{color:var(--ink);font-weight:600}",
    ".evc-axes{list-style:none;margin:8px 0 0;padding:0;font-size:var(--fs-xs);color:var(--ink-2);line-height:1.5;max-width:96ch}",
    ".evc-axes li{margin:0 0 5px}.evc-axes b{color:var(--ink);font-weight:600}.evc-axes .who{font-weight:600}",
    ".evc-figure{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:baseline;margin:0 0 4px}",
    ".evc-big{font-size:var(--fs-l);color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600;overflow-wrap:anywhere}",
    ".evc-ci{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);font-variant-numeric:tabular-nums}",
    ".evc-cols{display:flex;gap:12px 24px;flex-wrap:wrap;align-items:flex-start}.evc-cols>*{flex:1 1 280px;min-width:0}",
    ".evc-h{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);margin:8px 0 3px;letter-spacing:.04em;text-transform:uppercase}",
    ".evc-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
    ".evc-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
    ".evc-table td{padding:3px 10px 3px 0;color:var(--ink-2);white-space:nowrap;vertical-align:top}.evc-table td.num{text-align:right;font-family:var(--mono)}",
    ".evc-table td.bad{color:var(--bad);font-weight:600}.evc-table td.good{color:var(--good)}.evc-table tr.group td{color:var(--ink-3);font-family:var(--mono);padding-top:8px;letter-spacing:.04em;text-transform:uppercase}",
    ".evc-table td.name{white-space:normal;max-width:44ch}",
    ".evc-details{margin-top:8px;font-size:var(--fs-xs)}.evc-details summary{cursor:pointer;color:var(--ink-2)}",
    ".evc-v{font-weight:600}",
    ".evc-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
    ".evc-tip b{color:var(--ink)}.evc-tip .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
  ].join("\n");
  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    if (L && L.style && typeof L.style.once === "function") { try { L.style.once("evc", CSS); return; } catch (err) { /* fall through */ } }
    var node = document.createElement("style");
    node.textContent = CSS;
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------- helpers

  var F = L && L.fmt ? L.fmt : {};
  var isNum = typeof F.isNum === "function" ? F.isNum : function (v) { return typeof v === "number" && isFinite(v); };
  var num = typeof F.num === "function" ? F.num : function (v, p) {
    if (!isNum(v)) return "—";
    var s = v.toFixed(p === undefined ? 2 : p);
    if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s.replace("-", "−");
  };
  var signed = typeof F.signed === "function" ? F.signed : function (v, p) {
    if (!isNum(v)) return "—";
    var s = num(Math.abs(v), p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  };
  var pct = typeof F.pct === "function" ? F.pct : function (v) { return isNum(v) ? Math.round(v * 100) + "%" : "—"; };
  var short = typeof F.short === "function" ? F.short : function (id) { return String(id || "").replace(/^(rl|t)\d+_/, "").replace(/_/g, " "); };
  function trunc(s, n) { s = String(s === null || s === undefined ? "" : s); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  function fit(text, px) { var n = Math.floor(px / CH); return n < 2 ? "" : trunc(text, n); }
  function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function dot(s) { s = String(s || ""); return !s || /[.!?]$/.test(s) ? s : s + "."; }
  function count(v) { return isNum(v) ? v : Array.isArray(v) ? v.length : 0; }
  function width(host) {
    if (L && L.layout && typeof L.layout.measure === "function") { try { var w = L.layout.measure(host); if (isNum(w) && w > 0) return Math.max(300, Math.min(1400, w)); } catch (err) { /* local */ } }
    var w2 = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
    return Math.max(300, Math.min(1400, w2 || 320));
  }
  function responsive(host, draw, k) {
    if (L && L.layout && typeof L.layout.responsive === "function") { try { return L.layout.responsive(host, draw, k); } catch (err) { /* local */ } }
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, k);
    draw(); return host;
  }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (err) { return false; }
  }
  function dur(animate) { return animate && !prefersReduced() ? DUR : 0; }
  //: the page's task selection, only for a task the page actually has
  function taskOnPage(id) { var ids = typeof AgentDiff.taskIds === "function" ? AgentDiff.taskIds() : []; return !!id && ids.indexOf(id) >= 0; }
  function selectTask(ctx, id) {
    if (!taskOnPage(id)) return false;
    var fn = ctx && typeof ctx.selectTask === "function" ? ctx.selectTask
      : AgentDiff._internals && typeof AgentDiff._internals.selectTask === "function" ? AgentDiff._internals.selectTask : null;
    if (!fn) return false;
    fn(id);
    return true;
  }
  //: the side colours: the first lineage is A, the second B, as everywhere on the page
  function sideColor(side) {
    if (L && L.color && typeof L.color.side === "function") { try { var c = L.color.side(side); if (c) return c; } catch (err) { /* local */ } }
    return side === "a" ? "var(--a)" : "var(--b)";
  }
  function tooltip(root) {
    if (L && L.svg && typeof L.svg.tip === "function") { try { var t = L.svg.tip(root); if (t && typeof t.show === "function" && typeof t.hide === "function") return t; } catch (err) { /* local */ } }
    var tip = document.createElement("div"); tip.className = "evc-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        lines.forEach(function (l) {
          if (!l) return;
          var d = document.createElement("div");
          if (l.mono) d.className = "mono";
          if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text;
          tip.appendChild(d);
        });
        tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 340 > r.width) x = Math.max(0, evt.clientX - r.left - 350);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }
  /* The one interval drawing: a line from lo to hi with end ticks and the
   * point on it, horizontal, in the colour given — the idiom of the
   * statistics blocks. The library's glyph draws it when present. */
  function interval(g, x, point, lo, hi, color, y, r) {
    y = y || 0; r = r || 4.5;
    if (L && L.glyph && typeof L.glyph.interval === "function") {
      try { var out = L.glyph.interval(g, x, point, lo, hi); if (out !== undefined) { g.attr("color", color); return; } } catch (err) { /* local */ }
    }
    if (isNum(lo) && isNum(hi)) {
      g.append("line").attr("class", "int").attr("x1", x(lo)).attr("x2", x(hi)).attr("y1", y).attr("y2", y).attr("stroke", color).attr("stroke-width", 6).attr("stroke-opacity", 0.3).attr("stroke-linecap", "round");
      [lo, hi].forEach(function (v) { g.append("line").attr("x1", x(v)).attr("x2", x(v)).attr("y1", y - 5).attr("y2", y + 5).attr("stroke", color).attr("stroke-width", 1.5).attr("stroke-opacity", 0.5); });
    }
    if (isNum(point)) g.append("circle").attr("class", "pt").attr("cx", x(point)).attr("cy", y).attr("r", r).attr("fill", color);
  }

  /* The verdict vocabulary of the lineage blocks, colour and glyph alike, so a
   * gamed step is the same red here as on the thread. */
  var VERDICT = {
    improved: { color: "var(--good)", glyph: "", word: "improved" },
    regressed: { color: "var(--bad)", glyph: "", word: "regressed" },
    flat: { color: "var(--ink-3)", glyph: "", word: "flat" },
    gamed: { color: "var(--bad)", glyph: "✕", word: "gamed" },
    forgot: { color: "var(--warn)", glyph: "▏", word: "forgot" },
    overfit: { color: "var(--warn)", glyph: "◎", word: "overfit" },
    traded: { color: "var(--warn)", glyph: "⇄", word: "traded" },
  };
  var VERDICT_ORDER = ["improved", "flat", "regressed", "traded", "overfit", "forgot", "gamed"];
  function verdictColor(k) {
    if (L && L.color && typeof L.color.verdict === "function") { try { var c = L.color.verdict(k); if (c) return c; } catch (err) { /* local */ } }
    return VERDICT[k] ? VERDICT[k].color : "var(--ink-3)";
  }

  // --------------------------------------------------------------- model

  /* The comparison as the blocks want it: the two lineages in the engine's
   * order (A first, coloured as A everywhere), their curves on both x
   * measures, the race, the two head-to-head pairs, the per-generation rows,
   * the process ledgers, the task race and the verdict. A part the engine
   * marked unmeasurable keeps its reason, so a block can say so. */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {};
    if (cache && cache.agg === agg) return cache.m;
    var m;
    try { m = build(agg); } catch (err) { console.warn("AgentDiff evocompare: model failed", err); m = { ok: false, reason: "the comparison section could not be read" }; }
    cache = { agg: agg, m: m };
    return m;
  }

  function normCurve(list, gens) {
    var rows = Array.isArray(list) ? list.filter(function (r) { return r && isNum(r.index); }) : [];
    if (!rows.length) {
      // no curve from the engine: the generations' own points, the episodes summed in order
      var acc = 0;
      rows = gens.map(function (g) { acc += isNum(g.n) ? g.n : 0; return { index: g.index, id: g.id, point: g.point, lo: g.lo, hi: g.hi, pass_rate: g.pass, episodes_cum: acc, seconds_cum: null, measurable: isNum(g.point) }; });
    }
    return rows.map(function (r) {
      return { index: r.index, id: String(r.id || ""), point: isNum(r.point) ? r.point : null, lo: isNum(r.lo) ? r.lo : null, hi: isNum(r.hi) ? r.hi : null,
        pass: isNum(r.pass_rate) ? r.pass_rate : null, episodes_cum: isNum(r.episodes_cum) ? r.episodes_cum : null, seconds_cum: isNum(r.seconds_cum) ? r.seconds_cum : null,
        reason: r.reason || null };
    }).sort(function (p, q) { return p.index - q.index; });
  }
  function markOf(raw, byId) {
    if (!raw || typeof raw !== "object" || !raw.id) return null;
    var g = byId[String(raw.id)];
    return { id: String(raw.id), index: isNum(raw.index) ? raw.index : g ? g.index : null };
  }
  function pairOf(raw, kind, lineages) {
    if (!raw || typeof raw !== "object") return { kind: kind, present: false, ok: false, reason: "not in the data", tasks: [], per: {} };
    var imp = raw.improvement && typeof raw.improvement === "object" ? raw.improvement : {};
    var per = raw.per_task && typeof raw.per_task === "object" ? raw.per_task : {};
    var a = raw.a && typeof raw.a === "object" ? raw.a : {}, b = raw.b && typeof raw.b === "object" ? raw.b : {};
    var ok = raw.measurable !== false && isNum(imp.point);
    return {
      kind: kind, present: true, ok: ok,
      reason: ok ? null : (raw.reason || imp.reason || "no probability of improvement was measured"),
      a: { family: a.family || lineages[0].family, id: a.id ? String(a.id) : "", index: isNum(a.index) ? a.index : null },
      b: { family: b.family || lineages[1].family, id: b.id ? String(b.id) : "", index: isNum(b.index) ? b.index : null },
      p: isNum(imp.point) ? imp.point : null, lo: isNum(imp.lo) ? imp.lo : null, hi: isNum(imp.hi) ? imp.hi : null,
      metric: raw.metric && typeof raw.metric === "object" ? raw.metric : {},
      pass: raw.pass_rate && typeof raw.pass_rate === "object" ? raw.pass_rate : {},
      per: per, tasks: Object.keys(per).sort(),
      separates: raw.separates || null, distance: isNum(raw.behaviour_distance) ? raw.behaviour_distance : null,
      advisory: raw.advisory && typeof raw.advisory === "object" ? String(raw.advisory.message || "") : typeof raw.advisory === "string" ? raw.advisory : "",
      reading: raw.reading ? String(raw.reading) : "", basis: raw.basis ? String(raw.basis) : "",
    };
  }

  function build(agg) {
    var ec = agg && agg.evolution_compare && typeof agg.evolution_compare === "object" ? agg.evolution_compare : null;
    if (!ec) return { ok: false, reason: null };
    if (ec.measurable === false) return { ok: false, reason: ec.reason || "the comparison could not be measured" };
    var raw = (Array.isArray(ec.lineages) ? ec.lineages : []).filter(function (l) { return l && (l.family || l.label); });
    if (raw.length < 2) return { ok: false, reason: "fewer than two lineages" };
    var curves = ec.curves && typeof ec.curves === "object" ? ec.curves : {};
    var byIndex = curves.by_index && typeof curves.by_index === "object" ? curves.by_index : {};
    var byEp = curves.by_episodes && typeof curves.by_episodes === "object" ? curves.by_episodes : {};
    var process = ec.process && typeof ec.process === "object" ? ec.process : {};
    var race = ec.race && typeof ec.race === "object" ? ec.race : {};
    var lineages = raw.slice(0, 2).map(function (l, i) {
      var ev = l.evolution && typeof l.evolution === "object" ? l.evolution : {};
      var family = String(l.family || l.label);
      var gens = (Array.isArray(ev.generations) ? ev.generations : []).filter(function (g) { return g && g.id; }).map(function (g, gi) {
        var iqm = g.iqm_by_task && typeof g.iqm_by_task === "object" ? g.iqm_by_task : g.iqm && typeof g.iqm === "object" ? g.iqm : {};
        return { id: String(g.id), index: isNum(g.index) ? g.index : gi, point: isNum(iqm.point) ? iqm.point : null, lo: isNum(iqm.lo) ? iqm.lo : null, hi: isNum(iqm.hi) ? iqm.hi : null,
          pass: isNum(g.pass_rate) ? g.pass_rate : null, n: isNum(g.episodes_n) ? g.episodes_n : null, mechanism: g.mechanism || null };
      }).sort(function (p, q) { return p.index - q.index; });
      var byId = {};
      gens.forEach(function (g) { byId[g.id] = g; });
      var steps = (Array.isArray(ev.steps) ? ev.steps : []).filter(function (s) { return s && s.from && s.to; }).map(function (s) {
        var eff = s.effect && typeof s.effect === "object" ? s.effect : {};
        var imp = eff.improvement && typeof eff.improvement === "object" ? eff.improvement : {};
        return { from: String(s.from), to: String(s.to), key: String(s.from) + " → " + String(s.to), mechanism: s.mechanism ? String(s.mechanism) : "", verdict: s.verdict || null,
          measurable: eff.measurable !== false, dIqm: eff.iqm && isNum(eff.iqm.delta) ? eff.iqm.delta : null,
          p: isNum(imp.point) ? imp.point : null, plo: isNum(imp.lo) ? imp.lo : null, phi: isNum(imp.hi) ? imp.hi : null,
          flags: Array.isArray(s.flags) ? s.flags.map(String) : [], regressed: Array.isArray(eff.regressed) ? eff.regressed.map(String) : [],
          summary: s.diff && s.diff.summary ? String(s.diff.summary) : "", toIndex: byId[String(s.to)] ? byId[String(s.to)].index : null };
      });
      var curve = normCurve(byIndex[family], gens);
      var curveEp = normCurve(byEp[family], gens);
      var out = {
        i: i, side: i === 0 ? "a" : "b", color: sideColor(i === 0 ? "a" : "b"), family: family, label: String(l.label || family), ev: ev, gens: gens, byId: byId, steps: steps,
        n: isNum(l.generations_n) ? l.generations_n : gens.length, episodes_n: isNum(l.episodes_n) ? l.episodes_n : null, tasks_n: isNum(l.tasks_n) ? l.tasks_n : null,
        recommended: markOf(l.recommended, byId) || markOf(ev.recommended, byId), best: markOf(l.best, byId) || markOf(ev.best, byId),
        last: markOf(l.last, byId) || (gens.length ? { id: gens[gens.length - 1].id, index: gens[gens.length - 1].index } : null),
        curve: curve, curveEp: curveEp.length ? curveEp : curve,
        process: process[family] && typeof process[family] === "object" ? process[family] : null,
        reached: race.reached && race.reached[family] && typeof race.reached[family] === "object" ? race.reached[family] : null,
        auc: race.auc && race.auc[family] && typeof race.auc[family] === "object" ? race.auc[family] : null,
      };
      return out;
    });
    var maxN = 0;
    lineages.forEach(function (l) { l.curveEp.forEach(function (d) { maxN = Math.max(maxN, d.index + 1); }); maxN = Math.max(maxN, l.gens.length); });
    var hasEpisodes = lineages.every(function (l) { return l.curveEp.length && l.curveEp.every(function (d) { return isNum(d.episodes_cum); }); });
    var thr = race.threshold && typeof race.threshold === "object" && isNum(race.threshold.value) ? race.threshold : null;
    var byGen = (Array.isArray(ec.by_generation) ? ec.by_generation : []).filter(function (r) { return r && isNum(r.index); }).map(function (r) {
      var imp = r.improvement && typeof r.improvement === "object" ? r.improvement : {};
      return { index: r.index, a: r.a && typeof r.a === "object" ? r.a : null, b: r.b && typeof r.b === "object" ? r.b : null,
        p: isNum(imp.point) ? imp.point : null, lo: isNum(imp.lo) ? imp.lo : null, hi: isNum(imp.hi) ? imp.hi : null,
        distance: isNum(r.behaviour_distance) ? r.behaviour_distance : null, separates: r.separates || null, reason: r.reason || null };
    }).sort(function (p, q) { return p.index - q.index; });
    var tr = ec.task_race && typeof ec.task_race === "object" ? ec.task_race : {};
    var trTasks = tr.tasks && typeof tr.tasks === "object" && !Array.isArray(tr.tasks) ? tr.tasks : null;
    if (!trTasks) {
      // the contract's flat shape: task keys beside first_solver and never_solved
      var reserved = { first_solver: 1, never_solved: 1, rule: 1, reading: 1, measurable: 1, reason: 1 };
      trTasks = {};
      Object.keys(tr).forEach(function (k) { if (!reserved[k] && tr[k] && typeof tr[k] === "object") trTasks[k] = tr[k]; });
    }
    var shared = ec.tasks && Array.isArray(ec.tasks.shared) ? ec.tasks.shared.map(String) : Object.keys(trTasks).sort();
    var m = {
      ok: true, ec: ec, lineages: lineages, A: lineages[0], B: lineages[1], maxN: maxN, hasEpisodes: hasEpisodes,
      metric: ec.metric ? String(ec.metric) : "IQM", metricDefinition: ec.metric_definition ? String(ec.metric_definition) : "",
      shared: shared, only: ec.tasks && ec.tasks.only && typeof ec.tasks.only === "object" ? ec.tasks.only : {},
      curvesReading: curves.reading ? String(curves.reading) : "", alignment: curves.alignment && typeof curves.alignment === "object" ? curves.alignment : {},
      race: race, threshold: thr, raceReading: race.reading ? String(race.reading) : "",
      peak: pairOf(ec.peak, "peak", lineages), final: pairOf(ec.final, "final", lineages),
      byGen: byGen, byGenReading: ec.by_generation_reading ? String(ec.by_generation_reading) : "", byGenNote: ec.by_generation_note ? String(ec.by_generation_note) : "",
      trTasks: trTasks, raceTasks: shared.filter(function (t) { return trTasks[t]; }), raceRule: tr.rule ? String(tr.rule) : "",
      firstSolver: tr.first_solver && typeof tr.first_solver === "object" ? tr.first_solver : {},
      neverSolved: tr.never_solved && typeof tr.never_solved === "object" ? tr.never_solved : {},
      verdict: ec.verdict && typeof ec.verdict === "object" ? ec.verdict : {},
      narrative: ec.narrative ? String(ec.narrative) : "", advisory: ec.advisory ? String(ec.advisory) : "",
    };
    return m;
  }

  function byFamily(m, family) { return m.lineages.filter(function (l) { return l.family === family; })[0] || null; }
  function pointAt(l, k) { return l.curveEp.filter(function (d) { return d.index === k; })[0] || null; }
  //: the label of a column: the shared id when both lineages agree, else the index
  function idAt(m, k) {
    var ids = m.lineages.map(function (l) { var g = pointAt(l, k); return g ? g.id : null; }).filter(function (v) { return v; });
    if (ids.length === m.lineages.length && ids.every(function (v) { return v === ids[0]; })) return ids[0];
    return "#" + k;
  }
  function familyText(m, family) { return family ? family : "—"; }
  function whoColor(m, family) { var l = byFamily(m, family); return l ? l.color : "var(--ink-3)"; }

  // --------------------------------------------------------------- store

  /* One store for every block: the generation index in view, the x measure
   * of the curves, the pair mode. Persisted per browser through the shared
   * family store when it exists, else the page's own Store; a change
   * re-paints every mounted block in place. */
  var S = { gen: null, x: "index", pair: "peak", loaded: false };
  var LISTENERS = [];
  var FAMILY = null;
  function store() {
    try { return AgentDiff._internals && AgentDiff._internals.Store ? AgentDiff._internals.Store : null; } catch (err) { return null; }
  }
  function family() {
    if (FAMILY !== null) return FAMILY || null;
    FAMILY = false;
    if (L && typeof L.family === "function") {
      try { var f = L.family("evolution-compare", { gen: null, x: "index", pair: "peak" }); if (f && typeof f.get === "function" && typeof f.set === "function") FAMILY = f; } catch (err) { FAMILY = false; }
    }
    return FAMILY || null;
  }
  function readStored() {
    var f = family();
    if (f) { try { var v = f.get(); return v && typeof v === "object" ? v : null; } catch (err) { /* the store */ } }
    var s = store();
    return s ? s.get(PREF_KEY) : null;
  }
  function loadState(m) {
    if (!S.loaded) {
      S.loaded = true;
      var v = readStored();
      if (v && typeof v === "object") {
        if (isNum(v.gen)) S.gen = v.gen;
        if (v.x === "episodes" || v.x === "index") S.x = v.x;
        if (v.pair === "final" || v.pair === "peak") S.pair = v.pair;
      }
    }
    // a stored choice this comparison cannot honour falls back to nothing selected
    if (S.gen !== null && (!isNum(S.gen) || S.gen < 0 || S.gen >= m.maxN)) S.gen = null;
    if (!m.hasEpisodes) S.x = "index";
    return S;
  }
  function saveState() {
    var snap = { gen: S.gen, x: S.x, pair: S.pair };
    var f = family();
    if (f) { try { f.set(snap); if (typeof f.persist === "function") f.persist(); return; } catch (err) { /* fall through */ } }
    var s = store();
    if (s) { try { s.set(PREF_KEY, snap); } catch (err) { /* quota; the session still holds it */ } }
  }
  function listen(host, fn) { LISTENERS.push({ host: host, fn: fn }); }
  function broadcast(what) {
    LISTENERS = LISTENERS.filter(function (l) { return l.host.isConnected; });
    LISTENERS.forEach(function (l) { try { l.fn(what); } catch (err) { console.warn("AgentDiff evocompare: repaint failed", err); } });
  }
  function select(patch) {
    var changed = {};
    Object.keys(patch || {}).forEach(function (k) {
      if (!(k in S) || k === "loaded") return;
      if (S[k] === patch[k]) return;
      S[k] = patch[k]; changed[k] = true;
    });
    if (!Object.keys(changed).length) return;
    saveState();
    broadcast(changed);
    // the primary lineage's own blocks follow the generation when they are on the page
    if (changed.gen && S.gen !== null && cache && cache.m && cache.m.ok && AgentDiff.evolution && typeof AgentDiff.evolution.select === "function") {
      var g = cache.m.A.gens.filter(function (x) { return x.index === S.gen; })[0];
      if (g) { try { AgentDiff.evolution.select({ gen: g.id }); } catch (err) { /* theirs to fix */ } }
    }
  }
  // a small surface for the tests and any sibling block
  AgentDiff.evolutionCompare = {
    select: select,
    state: function () { return { gen: S.gen, x: S.x, pair: S.pair }; },
    listen: listen,
  };

  function genLines(m, k) {
    var lines = [{ b: true, text: "generation " + idAt(m, k) + (idAt(m, k) !== "#" + k ? " (index " + k + ")" : "") }];
    m.lineages.forEach(function (l) {
      var d = pointAt(l, k);
      if (!d) { lines.push({ text: l.family + ": no generation " + k + " — the lineage is shorter" }); return; }
      var marks = [];
      if (l.recommended && l.recommended.id === d.id) marks.push("recommended");
      if (l.best && l.best.id === d.id && !(l.recommended && l.recommended.id === d.id)) marks.push("best");
      if (l.reached && l.reached.index === d.index) marks.push("first over the threshold");
      lines.push({ mono: true, text: l.family + " " + d.id + ": " + (isNum(d.point) ? num(d.point) + " [" + num(d.lo) + ", " + num(d.hi) + "]" : "not measured" + (d.reason ? " (" + d.reason + ")" : "")) + " · pass " + pct(d.pass) + (isNum(d.episodes_cum) ? " · " + num(d.episodes_cum, 0) + " episodes so far" : "") + (marks.length ? " · " + marks.join(", ") : "") });
    });
    lines.push({ text: S.gen === k ? "click to clear the selection" : "click to select this generation across the comparison" });
    return lines;
  }

  // ============================================================ evc-curves

  function drawCurves(host, ctx, m, tip, ref) {
    if (!d3) return;
    var W = width(host), narrow = W < 560;
    var padL = narrow ? 36 : 46, padR = narrow ? 12 : 18, padT = 24, padB = 36, H = narrow ? 230 : 280;
    var pts = [];
    m.lineages.forEach(function (l) { l.curveEp.forEach(function (d) { pts.push({ l: l, d: d }); }); });
    var maxI = 0, maxE = 0, lo = 0, hi = 0;
    pts.forEach(function (p) {
      maxI = Math.max(maxI, p.d.index);
      if (isNum(p.d.episodes_cum)) maxE = Math.max(maxE, p.d.episodes_cum);
      [p.d.point, p.d.lo, p.d.hi].forEach(function (v) { if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
    });
    if (m.threshold) { lo = Math.min(lo, m.threshold.value); hi = Math.max(hi, m.threshold.value); }
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    var x0 = padL + 10, x1 = W - padR - 10;
    var xI = d3.scaleLinear().domain([0, Math.max(1, maxI)]).range([x0, x1]);
    var xE = d3.scaleLinear().domain([0, Math.max(1, maxE)]).range([x0, x1]);
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([H - padB, padT]);
    function ep() { return S.x === "episodes" && m.hasEpisodes; }
    function xOf(d) { return ep() ? xE(d.episodes_cum) : xI(d.index); }
    var reachedWords = m.lineages.map(function (l) { return l.family + (l.reached ? " first over it at " + l.reached.id + " (" + num(l.reached.episodes_cum, 0) + " episodes)" : " never over it"); }).join("; ");
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("data-x", ep() ? "episodes" : "index")
      .attr("aria-label", "learning curves: " + m.A.family + " and " + m.B.family + ", " + m.metric + " per generation with its bootstrap band, x is " + (ep() ? "cumulative episodes" : "the generation index")
        + (m.threshold ? "; the race threshold " + num(m.threshold.value) + " drawn as a dashed rule; " + reachedWords : "; no threshold") + "; the recommended generation of each lineage is filled");
    // the return axis: a few ticks, the zero line drawn where it falls
    y.ticks(narrow ? 3 : 4).forEach(function (t) {
      svg.append("line").attr("class", t === 0 ? "zero" : "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(t)).attr("y2", y(t)).attr("stroke-dasharray", t === 0 ? null : "1 3");
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(t) + 4).attr("text-anchor", "end").text(signed(t, 1));
    });
    if (!y.ticks(narrow ? 3 : 4).some(function (t) { return t === 0; }) && lo < 0 && hi > 0) svg.append("line").attr("class", "zero").attr("x1", padL).attr("x2", W - padR).attr("y1", y(0)).attr("y2", y(0));
    svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 11).text(fit(m.metric + " over the shared tasks, with its interval", W - padL - 4));
    // the race threshold: a faint dashed rule, its value at the right
    if (m.threshold) {
      svg.append("line").attr("class", "evc-threshold").attr("x1", padL).attr("x2", W - padR).attr("y1", y(m.threshold.value)).attr("y2", y(m.threshold.value))
        .attr("stroke", "var(--ink-3)").attr("stroke-width", 1).attr("stroke-dasharray", "4 3").attr("stroke-opacity", 0.7);
      svg.append("text").attr("class", "tick backed").attr("x", W - padR).attr("y", y(m.threshold.value) - 4).attr("text-anchor", "end").text("race threshold " + num(m.threshold.value));
    }
    // the hover surface, under the marks so a point still takes its own click
    var surface = svg.append("rect").attr("class", "evc-surface").attr("x", padL).attr("y", padT - 6).attr("width", Math.max(1, W - padL - padR)).attr("height", Math.max(1, H - padB - padT + 12)).attr("fill", "transparent").style("cursor", "pointer");
    var selLine = svg.append("line").attr("class", "evc-sel").attr("y1", padT - 4).attr("y2", H - padB + 4).attr("stroke", "var(--ink)").attr("stroke-opacity", 0).attr("stroke-dasharray", "2 3").attr("x1", x0).attr("x2", x0);
    var ax = svg.append("g").attr("class", "evc-xaxis");
    function drawAxis() {
      ax.selectAll("*").remove();
      var yt = H - padB + 15;
      if (ep()) {
        xE.ticks(narrow ? 4 : 7).forEach(function (t) { ax.append("text").attr("class", "tick").attr("x", xE(t)).attr("y", yt).attr("text-anchor", "middle").text(num(t, 0)); });
        ax.append("text").attr("class", "lab dim").attr("x", W - padR).attr("y", H - 4).attr("text-anchor", "end").text("cumulative episodes on the shared tasks →");
      } else {
        var every = Math.max(1, Math.ceil((narrow ? 30 : 36) / Math.max(1, (x1 - x0) / Math.max(1, maxI))));
        for (var k = 0; k <= maxI; k++) {
          if (k % every !== 0 && k !== maxI) continue;
          ax.append("text").attr("class", "tick").attr("x", xI(k)).attr("y", yt).attr("text-anchor", "middle").text(trunc(idAt(m, k), 6));
        }
        ax.append("text").attr("class", "lab dim").attr("x", W - padR).attr("y", H - 4).attr("text-anchor", "end").text("generation →");
      }
    }
    var area = d3.area().x(xOf).y0(function (d) { return y(d.lo); }).y1(function (d) { return y(d.hi); }).defined(function (d) { return isNum(d.point) && isNum(d.lo) && isNum(d.hi) && (!ep() || isNum(d.episodes_cum)); });
    var line = d3.line().x(xOf).y(function (d) { return y(d.point); }).defined(function (d) { return isNum(d.point) && (!ep() || isNum(d.episodes_cum)); });
    var layers = m.lineages.map(function (l) {
      var g = svg.append("g").attr("class", "evc-lineage").attr("data-family", l.family).attr("data-side", l.side);
      var band = g.append("path").attr("class", "band").attr("fill", l.color).attr("fill-opacity", 0.12).attr("stroke", "none");
      var path = g.append("path").attr("class", "curve").attr("fill", "none").attr("stroke", l.color).attr("stroke-width", 1.8).attr("stroke-linejoin", "round");
      var nodes = g.selectAll("g.pt").data(l.curveEp.filter(function (d) { return isNum(d.point); })).enter().append("g")
        .attr("class", "pt hit").attr("data-index", function (d) { return d.index; }).attr("data-gen", function (d) { return d.id; })
        .attr("data-reached", function (d) { return l.reached && l.reached.index === d.index ? "1" : "0"; })
        .attr("data-recommended", function (d) { return l.recommended && l.recommended.id === d.id ? "1" : "0"; })
        .attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", function (d) { return l.family + " " + d.id + ": " + num(d.point) + " [" + num(d.lo) + ", " + num(d.hi) + "], pass " + pct(d.pass); });
      nodes.append("circle").attr("class", "ring").attr("r", 8.5).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.5)
        .attr("stroke-opacity", function (d) { return l.reached && l.reached.index === d.index ? 0.9 : 0; });
      nodes.append("circle").attr("class", "dot").attr("r", 4).attr("fill", function (d) { return l.recommended && l.recommended.id === d.id ? l.color : "var(--surface)"; }).attr("stroke", l.color).attr("stroke-width", 1.6);
      nodes.append("circle").attr("r", 11).attr("fill", "transparent");
      nodes.on("pointermove", function (evt, d) { evt.stopPropagation(); tip.show(evt, genLines(m, d.index)); }).on("pointerleave", tip.hide)
        .on("click", function (evt, d) { evt.stopPropagation(); tip.hide(); select({ gen: S.gen === d.index ? null : d.index }); })
        .on("keydown", function (evt, d) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); select({ gen: S.gen === d.index ? null : d.index }); } });
      return { l: l, band: band, path: path, nodes: nodes };
    });
    function nearest(evt) {
      var box = svg.node().getBoundingClientRect();
      var px = (evt.clientX - box.left) * (W / Math.max(1, box.width));
      var best = null, bd = Infinity;
      pts.forEach(function (p) { if (!isNum(p.d.point)) return; var dx = Math.abs(xOf(p.d) - px); if (dx < bd) { bd = dx; best = p.d.index; } });
      return best;
    }
    surface.on("pointermove", function (evt) { var k = nearest(evt); if (k === null) { tip.hide(); return; } tip.show(evt, genLines(m, k)); })
      .on("pointerleave", tip.hide)
      .on("click", function (evt) { var k = nearest(evt); tip.hide(); if (k !== null) select({ gen: S.gen === k ? null : k }); });
    function update(animate) {
      var d = dur(animate);
      svg.attr("data-x", ep() ? "episodes" : "index");
      drawAxis();
      layers.forEach(function (Ly) {
        (d ? Ly.band.transition().duration(d) : Ly.band).attr("d", area(Ly.l.curveEp) || "");
        (d ? Ly.path.transition().duration(d) : Ly.path).attr("d", line(Ly.l.curveEp) || "");
        (d ? Ly.nodes.transition().duration(d) : Ly.nodes).attr("transform", function (p) { return "translate(" + xOf(p) + "," + y(p.point) + ")"; });
        Ly.nodes.attr("aria-current", function (p) { return S.gen === p.index ? "true" : "false"; });
        var dots = Ly.nodes.select(".dot");
        (d ? dots.transition().duration(d) : dots).attr("r", function (p) { return S.gen === p.index ? 6 : 4; })
          .attr("stroke-width", function (p) { return S.gen === p.index ? 2.5 : 1.6; })
          .attr("stroke", function (p) { return S.gen === p.index ? "var(--ink)" : Ly.l.color; });
      });
      var sl = d ? selLine.transition().duration(d) : selLine;
      if (S.gen !== null && !ep()) sl.attr("x1", xI(S.gen)).attr("x2", xI(S.gen)).attr("stroke-opacity", 0.5);
      else sl.attr("stroke-opacity", 0);
    }
    update(false);
    ref.update = update;
  }

  AgentDiff.block({
    id: "evc-curves",
    title: "Who learned faster",
    question: "Both lineages' learning curves on one axis — IQM per generation with its interval, the race threshold, who reached it first, and which generation each should keep.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.lineages.some(function (l) { return l.curveEp.some(function (d) { return isNum(d.point); }); }) ? 0.95 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-curves" });
      el.appendChild(root);
      var tip = tooltip(root);
      var lede = m.raceReading || m.curvesReading;
      if (lede) root.appendChild(H("p", { class: "evc-lede", text: dot(cap(lede)) }));
      var bar = H("div", { class: "evc-bar" });
      m.lineages.forEach(function (l) {
        bar.appendChild(H("span", { class: "evc-chip", "data-family": l.family }, [
          H("i", { style: { background: l.color } }), H("b", { text: l.family }),
          H("span", { text: " · " + l.n + " gen · " + (isNum(l.episodes_n) ? l.episodes_n + " ep" : "") + (l.recommended ? " · keep " + l.recommended.id : "") + (l.reached ? " · over the threshold at " + l.reached.id : m.threshold ? " · never over the threshold" : "") })]));
      });
      var seg = H("span", { class: "evc-seg", role: "group", "aria-label": "x measure" });
      var btns = {};
      [["index", "by generation", "x is the generation index: the k-th generation of each lineage side by side"], ["episodes", "by episodes", "x is the cumulative episodes spent on the shared tasks: the honest measure when the lineages' runs differ"]].forEach(function (p) {
        btns[p[0]] = H("button", { type: "button", text: p[1], "data-x": p[0], title: p[2], "aria-pressed": S.x === p[0] ? "true" : "false", disabled: p[0] === "episodes" && !m.hasEpisodes,
          onclick: function () { select({ x: p[0] }); } });
        seg.appendChild(btns[p[0]]);
      });
      bar.appendChild(seg);
      root.appendChild(bar);
      var refs = {};
      var host = H("div", { class: "evc-chart" });
      root.appendChild(responsive(host, function () { drawCurves(host, ctx, m, tip, refs); }, "evc-curves"));
      var thrText = m.threshold ? " The dashed rule is the race threshold, " + num(m.threshold.value) + " on " + (m.threshold.metric || m.metric) + ": " + (m.threshold.source ? String(m.threshold.source) : "its source was not stated") + ". The ringed point is the first generation of each lineage at or over it." : " No race threshold was measured.";
      root.appendChild(H("p", { class: "evc-note", text: "A point is a generation's " + m.metric + (m.metricDefinition ? " (" + m.metricDefinition + ")" : "") + ", the band its stratified bootstrap interval, the zero line where it falls; the filled point is the generation the engine recommends keeping." + thrText
        + (m.hasEpisodes ? " \"By episodes\" puts each generation at the episodes its lineage had spent by then" + (m.alignment.by_episodes ? " — " + m.alignment.by_episodes : "") + "." : " The episode alignment is not available for these lineages, so x is the generation index only.")
        + " Hover a generation for both lineages' numbers; click one to select it across the comparison blocks." }));
      if (m.advisory) root.appendChild(H("p", { class: "evc-note evc-advisory", text: m.advisory }));
      listen(root, function (changed) {
        if (changed.x) Object.keys(btns).forEach(function (k) { btns[k].setAttribute("aria-pressed", S.x === k ? "true" : "false"); });
        if (refs.update && (changed.gen || changed.x)) refs.update(true);
      });
    },
  });

  // =========================================================== evc-verdict

  var AXES = [
    { key: "peak", label: "peak", question: "whose recommended generation is better" },
    { key: "final", label: "final", question: "whose last generation is better" },
    { key: "learning", label: "learning", question: "who reached the threshold with fewer episodes" },
    { key: "process", label: "process", question: "who evolved more soundly" },
  ];
  function pairNumber(P, m, narrow) {
    if (!P.present) return { full: "not in the data", short: "—" };
    if (!P.ok) return { full: "not measured: " + P.reason, short: "not measured" };
    var ci = pct(P.p) + " [" + pct(P.lo) + ", " + pct(P.hi) + "]";
    return { full: "P(" + P.b.family + " " + P.b.id + " > " + P.a.family + " " + P.a.id + ") = " + ci, short: "P(b > a) " + ci };
  }
  function axesOf(m) {
    var v = m.verdict, A = m.A, B = m.B;
    var out = {};
    ["peak", "final"].forEach(function (k) {
      var P = m[k], n = pairNumber(P, m);
      out[k] = { winner: v[k] || null, number: n.full, short: n.short, basis: v[k + "_basis"] ? String(v[k + "_basis"]) : "", reading: P.reading || (P.present ? "not measured: " + P.reason : "the " + k + " comparison is not in the data") };
    });
    var rA = A.reached, rB = B.reached, full, shortT;
    if (rA && rB) {
      full = A.family + " " + rA.id + " at " + num(rA.episodes_cum, 0) + " episodes · " + B.family + " " + rB.id + " at " + num(rB.episodes_cum, 0);
      shortT = num(rA.episodes_cum, 0) + " vs " + num(rB.episodes_cum, 0) + " episodes";
    } else if (rA || rB) {
      var who = rA ? A : B, r = rA || rB, other = rA ? B : A;
      full = who.family + " " + r.id + " at " + num(r.episodes_cum, 0) + " episodes · " + other.family + " never reached it";
      shortT = num(r.episodes_cum, 0) + " episodes vs never";
    } else if (A.auc && B.auc && A.auc.by_episodes && B.auc.by_episodes) {
      full = "neither reached it; area under the curve by episodes " + num(A.auc.by_episodes.value) + " vs " + num(B.auc.by_episodes.value);
      shortT = "AUC " + num(A.auc.by_episodes.value) + " vs " + num(B.auc.by_episodes.value);
    } else { full = m.threshold ? "neither reached the threshold" : "no threshold was measured"; shortT = full; }
    out.learning = { winner: v.learning || null, number: full, short: shortT, basis: v.learning_basis ? String(v.learning_basis) : "", reading: m.raceReading || full };
    var pA = A.process, pB = B.process;
    function findings(p) { return p ? count(p.gamed) + count(p.protected_touched) : null; }
    var fA = findings(pA), fB = findings(pB);
    var procFull = pA && pB
      ? "gamed + protected touched " + fA + " vs " + fB + " · forgot " + count(pA.forgot) + " vs " + count(pB.forgot) + " · retention " + pct(pA.retention && pA.retention.at_last) + " vs " + pct(pB.retention && pB.retention.at_last) + " · on noise " + count(pA.accepted_on_noise) + " vs " + count(pB.accepted_on_noise)
      : "the process ledger is missing for " + (pA ? B.family : pB ? A.family : "both lineages");
    out.process = { winner: v.process || null, number: procFull, short: pA && pB ? fA + " vs " + fB + " findings" : "not measured", basis: v.process_basis ? String(v.process_basis) : "",
      reading: processReading(m) };
    return out;
  }
  function processReading(m) {
    var A = m.A, B = m.B, pA = A.process, pB = B.process, w = m.verdict.process || null;
    if (!pA || !pB) return "The process axis could not be read: the ledger is missing for " + (pA ? B.family : pB ? A.family : "both lineages") + ".";
    function part(l, p) {
      var r = p.retention || {};
      return l.family + ": " + count(p.gamed) + " gamed, " + count(p.protected_touched) + " protected path" + (count(p.protected_touched) === 1 ? "" : "s") + " touched, " + count(p.forgot) + " forgot, retention " + pct(r.at_last)
        + (Array.isArray(r.lost) && r.lost.length ? " (lost " + r.lost.map(short).join(", ") + ")" : "") + ", " + count(p.accepted_on_noise) + " of " + count(p.steps) + " steps kept on noise";
    }
    var head = w ? w + " evolved more soundly" : "the process axis does not separate them";
    return head + (m.verdict.process_basis ? " — " + String(m.verdict.process_basis) : "") + ". " + part(A, pA) + "; " + part(B, pB) + ".";
  }

  function drawVerdict(host, ctx, m, tip, axes) {
    if (!d3) return;
    var W = width(host), narrow = W < 560;
    var A = m.A, B = m.B;
    var padL = 6, padR = 6, labW = narrow ? 62 : 90, headH = 26, rowH = narrow ? 50 : 46, padB = 4;
    var xA = padL + labW + 10, xB = W - padR - 10, xM = (xA + xB) / 2;
    var H = headH + AXES.length * rowH + padB;
    var words = AXES.map(function (ax) { var d = axes[ax.key]; return ax.label + ": " + (d.winner || "does not separate"); }).join(", ");
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "four axes on one who axis, " + A.family + " at the left end and " + B.family + " at the right — " + words);
    // the header: who is where
    svg.append("text").attr("class", "lab strong").attr("x", xA).attr("y", 12).attr("fill", A.color).text(fit(A.family, xM - xA - 46));
    svg.append("text").attr("class", "lab dim").attr("x", xM).attr("y", 12).attr("text-anchor", "middle").text(narrow ? "neither" : "does not separate");
    svg.append("text").attr("class", "lab strong").attr("x", xB).attr("y", 12).attr("text-anchor", "end").attr("fill", B.color).text(fit(B.family, xB - xM - 46));
    AXES.forEach(function (ax, i) {
      var top = headH + i * rowH, yl = top + 12, yt = top + 30;
      var d = axes[ax.key];
      var winner = d.winner, wl = winner ? byFamily(m, winner) : null;
      var xW = wl ? (wl.side === "a" ? xA : xB) : xM;
      var g = svg.append("g").attr("class", "evc-axis").attr("data-axis", ax.key).attr("data-winner", winner || "");
      g.append("text").attr("class", "lab strong").attr("x", padL).attr("y", yl).text(ax.label);
      g.append("text").attr("class", "tick").attr("x", W - padR).attr("y", yl).attr("text-anchor", "end").text(fit(narrow ? d.short : d.number, W - padR - padL - labW - 4));
      g.append("line").attr("class", "rule").attr("x1", xA).attr("x2", xB).attr("y1", yt).attr("y2", yt).attr("stroke-width", 1);
      [xA, xM, xB].forEach(function (px) { g.append("line").attr("class", "rule").attr("x1", px).attr("x2", px).attr("y1", yt - 4).attr("y2", yt + 4).attr("stroke-width", 1); });
      if (wl) {
        g.append("line").attr("class", "pull").attr("x1", xM).attr("x2", xW).attr("y1", yt).attr("y2", yt).attr("stroke", wl.color).attr("stroke-width", 3).attr("stroke-opacity", 0.55).attr("stroke-linecap", "round");
        g.append("circle").attr("class", "who").attr("cx", xW).attr("cy", yt).attr("r", 6).attr("fill", wl.color);
      } else {
        g.append("circle").attr("class", "who").attr("cx", xM).attr("cy", yt).attr("r", 6).attr("fill", "var(--surface)").attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-dasharray", "2 2");
      }
      g.append("rect").attr("x", 0).attr("y", top).attr("width", W).attr("height", rowH).attr("fill", "transparent");
      g.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: ax.label + " · " + ax.question }, { text: winner ? winner : "the engine does not separate them" }, { mono: true, text: d.number }, d.basis ? { text: d.basis } : null]); }).on("pointerleave", tip.hide);
    });
  }

  AgentDiff.block({
    id: "evc-verdict",
    title: "Four axes, four answers",
    question: "Peak, final, learning, process: on each, the lineage the engine named or \"does not separate\", and the one number that decided it — the winners differ, which is the point.",
    group: "evolution",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); return m.ok ? 0.94 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-verdict" });
      el.appendChild(root);
      var tip = tooltip(root);
      var axes = axesOf(m);
      var winners = {};
      AXES.forEach(function (ax) { var w = axes[ax.key].winner; if (w) winners[w] = (winners[w] || 0) + 1; });
      var names = Object.keys(winners);
      var headline = m.verdict.reading ? String(m.verdict.reading)
        : names.length ? names.map(function (n) { return n + " takes " + AXES.filter(function (ax) { return axes[ax.key].winner === n; }).map(function (ax) { return ax.label; }).join(" and "); }).join("; ") + "."
        : "No axis separates " + m.A.family + " from " + m.B.family + " on these runs.";
      root.appendChild(H("p", { class: "evc-lede", "data-winners": names.length, text: cap(headline) }));
      var host = H("div", { class: "evc-chart" });
      root.appendChild(responsive(host, function () { drawVerdict(host, ctx, m, tip, axes); }, "evc-verdict"));
      var list = H("ul", { class: "evc-axes" });
      AXES.forEach(function (ax) {
        var d = axes[ax.key];
        list.appendChild(H("li", { "data-axis": ax.key, "data-winner": d.winner || "" }, [
          H("b", { text: ax.label + " · " }),
          H("span", { class: "who", style: { color: d.winner ? whoColor(m, d.winner) : "var(--ink-3)" }, text: d.winner || "does not separate" }),
          H("span", { text: " — " + dot(d.reading) + (d.basis && d.reading.indexOf(d.basis) < 0 ? " (" + d.basis + ")" : "") }),
        ]));
      });
      root.appendChild(list);
      var rule = m.verdict.rule && typeof m.verdict.rule === "object" ? m.verdict.rule : null;
      root.appendChild(H("p", { class: "evc-note", text: "One row per axis; the dot sits at the lineage the engine named, or hollow in the middle when the runs do not separate them. "
        + (rule ? "Rules: peak — " + rule.peak + "; final — " + rule.final + "; learning — " + rule.learning + "; process — " + rule.process + "." : "Peak and final separate when the improvement interval clears 50%; learning is decided on episodes to the threshold; process lexicographically on gamed + protected touched, forgot, retention, accepted on noise.")
        + " A lineage can win on peak and lose on process; the reading says both." }));
      if (m.advisory) root.appendChild(H("p", { class: "evc-note evc-advisory", text: m.advisory }));
    },
  });

  // =========================================================== evc-process

  function stepIdsOf(l, verdict) { return l.steps.filter(function (s) { return s.verdict === verdict; }).map(function (s) { return s.key; }); }
  function findingsLine(l) {
    var p = l.process, r = p.retention || {};
    var parts = [];
    parts.push({ text: "▏" + count(p.protected_touched) + " protected", bad: count(p.protected_touched) > 0 });
    parts.push({ text: "✕ " + count(p.gamed) + " gamed", bad: count(p.gamed) > 0 });
    parts.push({ text: count(p.forgot) + " forgot" + (Array.isArray(r.lost) && r.lost.length ? " (" + r.lost.map(short).join(", ") + ")" : ""), bad: count(p.forgot) > 0 });
    parts.push({ text: count(p.accepted_on_noise) + " on noise", bad: false });
    parts.push({ text: count(p.over_budget) + " over budget", bad: count(p.over_budget) > 0 });
    parts.push({ text: count(p.collapsed) + " collapsed", bad: count(p.collapsed) > 0 });
    parts.push({ text: "retention " + pct(r.at_last), bad: isNum(r.at_last) && r.at_last < 1 });
    parts.push({ text: "drift " + num(p.drift_from_origin_at_last), bad: false });
    return parts;
  }
  function drawProcess(host, ctx, m, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 560;
    var padL = 4, padR = 8, labW = narrow ? 74 : 118, rowH = 52, barH = 16, padT = 16;
    var rows = m.lineages.filter(function (l) { return l.process; });
    var maxSteps = 1;
    rows.forEach(function (l) { maxSteps = Math.max(maxSteps, count(l.process.steps)); });
    var x = d3.scaleLinear().domain([0, maxSteps]).range([padL + labW, W - padR]);
    var H = padT + rows.length * rowH;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "the verdicts of each lineage's steps as a stacked bar on one step axis: " + rows.map(function (l) { return l.family + " " + VERDICT_ORDER.filter(function (k) { return count(l.process[k]); }).map(function (k) { return count(l.process[k]) + " " + k; }).join(", "); }).join("; "));
    svg.append("text").attr("class", "lab dim").attr("x", padL + labW).attr("y", 11).text("steps, by verdict →");
    rows.forEach(function (l, ri) {
      var top = padT + ri * rowH, p = l.process;
      var g = svg.append("g").attr("class", "evc-prow").attr("data-family", l.family);
      g.append("text").attr("class", "lab strong").attr("x", padL).attr("y", top + barH / 2 + 4).attr("fill", l.color).text(fit(l.family, labW - 8)).append("title").text(l.family);
      var acc = 0;
      VERDICT_ORDER.forEach(function (k) {
        var n = count(p[k]);
        if (!n) return;
        var x0 = x(acc), x1 = x(acc + n); acc += n;
        var seg = g.append("g").attr("class", "evc-seg-v").attr("data-verdict", k).attr("data-count", n);
        seg.append("rect").attr("x", x0 + 0.5).attr("y", top).attr("width", Math.max(1, x1 - x0 - 1)).attr("height", barH).attr("rx", 2).attr("fill", verdictColor(k)).attr("fill-opacity", k === "flat" ? 0.45 : 0.85);
        var labelText = n + " " + ((VERDICT[k] && VERDICT[k].glyph) ? VERDICT[k].glyph + " " : "") + k;
        if (x1 - x0 >= labelText.length * CH + 6) seg.append("text").attr("class", "tick").attr("x", (x0 + x1) / 2).attr("y", top + barH / 2 + 4).attr("text-anchor", "middle").attr("fill", k === "flat" ? "var(--ink-2)" : "var(--bg)").attr("pointer-events", "none").text(labelText);
        else if (x1 - x0 >= 14) seg.append("text").attr("class", "tick").attr("x", (x0 + x1) / 2).attr("y", top + barH / 2 + 4).attr("text-anchor", "middle").attr("fill", k === "flat" ? "var(--ink-2)" : "var(--bg)").attr("pointer-events", "none").text(String(n));
        var ids = stepIdsOf(l, k);
        seg.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: l.family + " · " + n + " " + k + " step" + (n === 1 ? "" : "s") }, ids.length ? { mono: true, text: ids.join(", ") } : null]); }).on("pointerleave", tip.hide);
      });
      var unmeasured = count(p.steps) - acc;
      if (unmeasured > 0) {
        g.append("rect").attr("x", x(acc) + 0.5).attr("y", top).attr("width", Math.max(1, x(acc + unmeasured) - x(acc) - 1)).attr("height", barH).attr("rx", 2).attr("fill", "none").attr("stroke", "var(--rule-2)").attr("stroke-dasharray", "2 2")
          .append("title").text(unmeasured + " step" + (unmeasured === 1 ? "" : "s") + " without a verdict");
      }
      // the findings, one line under the bar: the ones that count against the lineage in red
      var t = g.append("text").attr("class", "tick evc-findings").attr("x", padL + labW).attr("y", top + barH + 15);
      var used = 0, room = W - padR - padL - labW;
      findingsLine(l).forEach(function (part, pi) {
        var s = (pi ? " · " : "") + part.text;
        if (used + s.length * CH > room) return;
        used += s.length * CH;
        t.append("tspan").attr("fill", part.bad ? "var(--bad)" : null).attr("font-weight", part.bad ? 600 : null).text(s);
      });
      g.append("title").text(findingsLine(l).map(function (p2) { return p2.text; }).join(" · "));
    });
  }

  AgentDiff.block({
    id: "evc-process",
    title: "How soundly each evolved",
    question: "Side by side: the verdicts of every step, the gamed steps and forgotten tasks, the protected paths touched, the budgets breached, retention, drift — and the raw numbers so a reader can disagree with the order.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.lineages.some(function (l) { return l.process; }) ? 0.93 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-process" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(H("p", { class: "evc-lede", "data-winner": m.verdict.process || "", text: cap(processReading(m)) }));
      var missing = m.lineages.filter(function (l) { return !l.process; });
      if (missing.length) root.appendChild(H("p", { class: "evc-note", "data-role": "unmeasured", text: "The process ledger of " + missing.map(function (l) { return l.family; }).join(" and ") + " is not in the data, so only the other lineage is drawn." }));
      var host = H("div", { class: "evc-chart" });
      root.appendChild(responsive(host, function () { drawProcess(host, ctx, m, tip); }, "evc-process"));
      // the raw numbers, every one, so the lexicographic order can be argued with
      var rows = m.lineages.filter(function (l) { return l.process; });
      var table = H("table", { class: "evc-table evc-process-table" });
      var head = [H("th", { text: "measure" })];
      rows.forEach(function (l) { head.push(H("th", { text: l.family })); });
      table.appendChild(H("tr", null, head));
      function row(key, label, get, badWhen, cls) {
        var tr = H("tr", { "data-measure": key }, [H("td", { text: label })]);
        rows.forEach(function (l) {
          var v = get(l.process, l), isBad = badWhen ? badWhen(v, l.process) : false;
          tr.appendChild(H("td", { class: (cls === "name" ? "name" : "num") + (isBad ? " bad" : ""), text: v === null || v === undefined ? "—" : String(v) }));
        });
        table.appendChild(tr);
      }
      function group(label) { table.appendChild(H("tr", { class: "group" }, [H("td", { text: label, colspan: String(rows.length + 1) })])); }
      group("steps");
      row("steps", "steps", function (p) { return count(p.steps); });
      VERDICT_ORDER.forEach(function (k) { row(k, k, function (p) { return count(p[k]); }, function (v) { return v > 0 && (k === "gamed" || k === "forgot" || k === "regressed"); }); });
      // the counts, then the names behind them on their own wrapping rows, so the table stays narrow
      row("accepted_on_noise", "accepted on noise", function (p) { return count(p.accepted_on_noise); });
      row("accepted_on_noise_steps", "kept on noise", function (p) { return Array.isArray(p.accepted_on_noise_steps) && p.accepted_on_noise_steps.length ? p.accepted_on_noise_steps.join(", ") : "none"; }, null, "name");
      row("monotone", "monotone", function (p) { return p.monotone === true ? "yes" : p.monotone === false ? "no" : null; });
      group("integrity");
      row("protected_touched", "protected paths touched", function (p) { return count(p.protected_touched); }, function (v) { return v > 0; });
      row("protected_touched_paths", "which paths", function (p) { return Array.isArray(p.protected_touched_paths) && p.protected_touched_paths.length ? p.protected_touched_paths.map(function (t) { return (t.path || "?") + " at " + (t.to || t.step || "?"); }).join(", ") : "none"; }, null, "name");
      row("over_budget", "over budget", function (p) { return count(p.over_budget); }, function (v) { return v > 0; });
      row("over_budget_rows", "which budgets", function (p) { return Array.isArray(p.over_budget_rows) && p.over_budget_rows.length ? p.over_budget_rows.map(function (o) { return o.gen + " " + o.what + " " + num(o.value, 0) + " > " + num(o.budget, 0); }).join(", ") : "none"; }, null, "name");
      row("collapsed", "collapsed", function (p) { return count(p.collapsed); }, function (v) { return v > 0; });
      group("retention");
      row("ever_solved", "tasks ever solved", function (p) { var r = p.retention || {}; return isNum(r.ever_solved_n) ? r.ever_solved_n : Array.isArray(r.ever_solved) ? r.ever_solved.length : null; });
      row("solved_at_recommended", "solved at the recommended", function (p, l) { var r = p.retention || {}; return (Array.isArray(r.solved_at_recommended) ? r.solved_at_recommended.length : "—") + (l.recommended ? " (" + l.recommended.id + ")" : ""); });
      row("solved_at_last", "solved at the last", function (p, l) { var r = p.retention || {}; return (Array.isArray(r.solved_at_last) ? r.solved_at_last.length : "—") + (l.last ? " (" + l.last.id + ")" : ""); });
      row("at_last", "retention at the last", function (p) { var r = p.retention || {}; return pct(r.at_last); }, function (v, p) { var r = p.retention || {}; return isNum(r.at_last) && r.at_last < 1; });
      row("lost", "tasks lost", function (p) { var r = p.retention || {}; return Array.isArray(r.lost) && r.lost.length ? r.lost.map(short).join(", ") : "none"; }, function (v) { return v !== "none"; }, "name");
      row("never_solved", "never solved", function (p) { var r = p.retention || {}; return Array.isArray(r.never_solved) && r.never_solved.length ? r.never_solved.map(short).join(", ") : "none"; }, function (v) { return v !== "none"; }, "name");
      group("drift");
      row("drift_from_origin_at_last", "drift from origin at the last", function (p) { return num(p.drift_from_origin_at_last); });
      row("best_paying_mechanism", "best-paying mechanism", function (p) { return p.best_paying_mechanism || null; });
      root.appendChild(H("div", { class: "scroll-x" }, [table]));
      var rule = m.verdict.rule && m.verdict.rule.process ? String(m.verdict.rule.process) : "fewer gamed + protected touched, then fewer forgot, then higher retention, then fewer accepted on noise; ties null";
      var noiseRule = rows.length && rows[0].process.accepted_on_noise_rule ? " Accepted on noise: " + rows[0].process.accepted_on_noise_rule + "." : "";
      var retRule = rows.length && rows[0].process.retention && rows[0].process.retention.rule ? " Retention: " + rows[0].process.retention.rule + "." : "";
      root.appendChild(H("p", { class: "evc-note", text: "The bar is every step of the lineage coloured by the engine's verdict, in the colours of the lineage blocks (✕ gamed bought return without passes, ▏forgot a task, ◎ overfit its trigger tasks, ⇄ traded one task for another); the line under it is the findings, the ones that count against the lineage in red. The process axis is decided lexicographically: " + rule + "." + noiseRule + retRule + " Every number is in the table." }));
    },
  });

  // ============================================================== evc-pair

  function drawStrip(host, m, P) {
    if (!d3) return;
    var W = width(host), padL = 8, padR = 8, H = 58, y = 22;
    var x = d3.scaleLinear().domain([0, 1]).range([padL, W - padR]);
    var A = m.A, B = m.B;
    var color = isNum(P.p) ? (P.p > 0.5 ? B.color : P.p < 0.5 ? A.color : "var(--ink)") : "var(--ink-3)";
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "probability that a random run of " + P.b.family + " " + P.b.id + " beats a random run of " + P.a.family + " " + P.a.id + " on the same task: " + pct(P.p) + ", bootstrap interval " + pct(P.lo) + " to " + pct(P.hi) + "; the mid line is a coin flip");
    svg.append("line").attr("class", "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y).attr("y2", y);
    svg.append("line").attr("class", "zero").attr("x1", x(0.5)).attr("x2", x(0.5)).attr("y1", y - 12).attr("y2", y + 12).attr("stroke-width", 1.5);
    svg.append("text").attr("class", "tick").attr("x", x(0.5)).attr("y", H - 5).attr("text-anchor", "middle").text("50% · a coin flip");
    svg.append("text").attr("class", "tick").attr("x", padL).attr("y", H - 5).attr("fill", A.color).text(fit("0% · " + A.family + " ahead", (W - padR - padL) / 2 - 60));
    svg.append("text").attr("class", "tick").attr("x", W - padR).attr("y", H - 5).attr("text-anchor", "end").attr("fill", B.color).text(fit(B.family + " ahead · 100%", (W - padR - padL) / 2 - 60));
    interval(svg.append("g").attr("class", "evc-pint"), x, P.p, P.lo, P.hi, color, y, 4.5);
  }

  function drawPairTasks(host, ctx, m, P, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 480;
    var A = m.A, B = m.B;
    var tasks = P.tasks.slice().sort(function (p, q) { return (isNum(P.per[q].delta) ? P.per[q].delta : -Infinity) - (isNum(P.per[p].delta) ? P.per[p].delta : -Infinity); });
    if (!tasks.length) return;
    var labW = narrow ? 78 : Math.min(150, 14 + 6.6 * tasks.reduce(function (a, t) { return Math.max(a, short(t).length); }, 6));
    var markW = 26, textW = narrow ? 0 : 70, padR = 8 + markW + textW;
    var rowH = 20, padT = 16, padB = 20;
    var H = padT + tasks.length * rowH + padB;
    var maxAbs = 0;
    tasks.forEach(function (t) { var d = P.per[t].delta; if (isNum(d)) maxAbs = Math.max(maxAbs, Math.abs(d)); });
    var x = d3.scaleLinear().domain([-Math.max(1e-9, maxAbs), Math.max(1e-9, maxAbs)]).nice().range([labW, W - padR]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("data-pair", P.kind)
      .attr("aria-label", "per shared task, the mean return of " + P.b.family + " " + P.b.id + " minus that of " + P.a.family + " " + P.a.id + ", one dot per task on a shared axis with zero drawn, both pass rates as small marks at the right");
    svg.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0)).attr("y1", padT - 6).attr("y2", H - padB + 2);
    x.ticks(narrow ? 3 : 5).forEach(function (t) { svg.append("text").attr("class", "tick").attr("x", x(t)).attr("y", H - 5).attr("text-anchor", "middle").text(signed(t, 1)); });
    svg.append("text").attr("class", "lab dim").attr("x", x(0) + 4).attr("y", 10).text(fit("Δ mean return, " + B.family + " − " + A.family + " →", W - padR - x(0) - 4));
    svg.append("text").attr("class", "tick").attr("x", W - padR + 4).attr("y", 10).text("pass");
    tasks.forEach(function (t, i) {
      var d = P.per[t], y = padT + i * rowH + rowH / 2;
      var color = isNum(d.delta) ? (d.delta > 0 ? B.color : d.delta < 0 ? A.color : "var(--ink-2)") : "var(--ink-3)";
      var g = svg.append("g").attr("class", "evc-tdot").attr("data-task", t).attr("data-delta", isNum(d.delta) ? d.delta : "").attr("data-p", isNum(d.p) ? d.p : "")
        .style("cursor", taskOnPage(t) ? "pointer" : "default");
      g.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", y + 4).attr("text-anchor", "end").text(trunc(short(t), Math.floor((labW - 10) / 6.6))).append("title").text(t);
      if (isNum(d.delta)) {
        g.append("line").attr("x1", x(0)).attr("x2", x(d.delta)).attr("y1", y).attr("y2", y).attr("stroke", color).attr("stroke-opacity", 0.35).attr("stroke-width", 1.5);
        g.append("circle").attr("class", "dot").attr("cx", x(d.delta)).attr("cy", y).attr("r", 4.2).attr("fill", color);
      } else {
        g.append("text").attr("class", "tick").attr("x", x(0) + 6).attr("y", y + 4).text("no delta");
      }
      // both pass rates as two small marks, A then B, filled to the rate
      var mx = W - padR + 4;
      [[A, d.pass_a], [B, d.pass_b]].forEach(function (pair, pi) {
        var l = pair[0], v = pair[1];
        g.append("rect").attr("class", "evc-pass").attr("data-side", l.side).attr("data-pass", isNum(v) ? v.toFixed(2) : "").attr("x", mx + pi * 12).attr("y", y - 5).attr("width", 9).attr("height", 10).attr("rx", 2)
          .attr("fill", isNum(v) ? l.color : "none").attr("fill-opacity", isNum(v) ? 0.15 + 0.85 * v : 0).attr("stroke", isNum(v) ? "none" : "var(--rule)");
      });
      if (!narrow) g.append("text").attr("class", "tick").attr("x", mx + 28).attr("y", y + 4).text(pct(d.pass_a) + " · " + pct(d.pass_b));
      g.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", W).attr("height", rowH).attr("fill", "transparent");
      g.on("pointermove", function (evt) {
        tip.show(evt, [{ b: true, text: short(t) },
          { mono: true, text: A.family + " " + P.a.id + ": mean " + num(d.a) + " · pass " + pct(d.pass_a) },
          { mono: true, text: B.family + " " + P.b.id + ": mean " + num(d.b) + " · pass " + pct(d.pass_b) },
          { text: "Δ " + signed(d.delta) + (isNum(d.p) ? " · P(" + B.family + " > " + A.family + ") on this task " + pct(d.p) : "") },
          taskOnPage(t) ? { text: "click to open this task" } : null]);
      }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); selectTask(ctx, t); });
    });
  }

  function renderPair(body, ctx, m, tip) {
    var H = ctx.h;
    body.innerHTML = "";
    var P = m[S.pair];
    body.setAttribute("data-pair", S.pair);
    if (!P.present || !P.ok) {
      body.appendChild(H("p", { class: "evc-read", "data-role": "unmeasured", text: "The " + (S.pair === "peak" ? "recommended-vs-recommended" : "final-vs-final") + " comparison could not be measured: " + dot(P.reason) }));
      if (P.reading) body.appendChild(H("p", { class: "evc-read", text: P.reading }));
      return;
    }
    body.appendChild(H("div", { class: "evc-figure" }, [
      H("span", { class: "evc-big", "data-p": P.p, text: "P(" + P.b.family + " " + P.b.id + " > " + P.a.family + " " + P.a.id + ") = " + pct(P.p) }),
      H("span", { class: "evc-ci", text: "[" + pct(P.lo) + ", " + pct(P.hi) + "]" }),
      H("span", { class: "evc-chip", text: (P.metric.name || m.metric) + " " + num(P.metric.a) + " vs " + num(P.metric.b) + " (" + signed(P.metric.delta) + ")" + (isNum(P.pass.a) && isNum(P.pass.b) ? " · pass " + pct(P.pass.a) + " vs " + pct(P.pass.b) : "") }),
    ]));
    var sh = H("div", { class: "evc-chart" });
    body.appendChild(responsive(sh, function () { drawStrip(sh, m, P); }, "evc-pair-strip"));
    if (P.reading) body.appendChild(H("p", { class: "evc-read evc-reading", text: cap(P.reading) }));
    var th = H("div", { class: "evc-chart" });
    body.appendChild(responsive(th, function () { drawPairTasks(th, ctx, m, P, tip); }, "evc-pair-tasks"));
    var adv = P.advisory || m.advisory;
    if (adv) body.appendChild(H("p", { class: "evc-note evc-advisory", text: adv }));
  }

  AgentDiff.block({
    id: "evc-pair",
    title: "Head to head",
    question: "The two recommended generations against each other (or the two last): how often a run of one beats a run of the other on the same task, and the per-task deltas the average hides.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && (m.peak.present || m.final.present) ? 0.92 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-pair" });
      el.appendChild(root);
      var tip = tooltip(root);
      var bar = H("div", { class: "evc-bar" });
      var seg = H("span", { class: "evc-seg", role: "group", "aria-label": "which generations" });
      var btns = {};
      [["peak", "recommended vs recommended", "the generation each lineage should keep, against the other's"], ["final", "final vs final", "the last generation of each, what a loop that keeps its latest self would ship"]].forEach(function (p) {
        var P = m[p[0]];
        btns[p[0]] = H("button", { type: "button", "data-pair": p[0], title: p[2], "aria-pressed": S.pair === p[0] ? "true" : "false",
          text: p[1] + (P.present && P.a.id && P.b.id ? " · " + P.a.id + " vs " + P.b.id : ""), onclick: function () { select({ pair: p[0] }); } });
        seg.appendChild(btns[p[0]]);
      });
      bar.appendChild(seg);
      m.lineages.forEach(function (l) { bar.appendChild(H("span", { class: "evc-chip" }, [H("i", { style: { background: l.color } }), H("b", { text: l.family })])); });
      root.appendChild(bar);
      var body = H("div", { class: "evc-pair-body" });
      root.appendChild(body);
      renderPair(body, ctx, m, tip);
      root.appendChild(H("p", { class: "evc-note", text: "Per task, every run of the second generation is compared against every run of the first (a tie counts a half) and the tasks are averaged, so one task cannot dominate by having more runs; the bar is the stratified bootstrap interval, the mid line a coin flip. Below, one dot per shared task: its Δ mean return, coloured by which lineage is ahead there, and both pass rates as marks filled to the rate. \"Recommended vs recommended\" and \"final vs final\" are two different questions, drawn the same way." + (m.only && Object.keys(m.only).some(function (f) { return Array.isArray(m.only[f]) && m.only[f].length; }) ? " Tasks run by only one lineage are excluded, never imputed: " + Object.keys(m.only).filter(function (f) { return m.only[f].length; }).map(function (f) { return f + " only " + m.only[f].map(short).join(", "); }).join("; ") + "." : "") }));
      listen(root, function (changed) {
        if (!changed.pair) return;
        Object.keys(btns).forEach(function (k) { btns[k].setAttribute("aria-pressed", S.pair === k ? "true" : "false"); });
        renderPair(body, ctx, m, tip);
      });
    },
  });

  // ============================================================== evc-race

  function drawRace(host, ctx, m, tip, ref) {
    if (!d3) return;
    var W = width(host), narrow = W < 560;
    var tasks = m.raceTasks, n = m.maxN;
    var labW = narrow ? 76 : Math.min(160, 16 + 6.6 * tasks.reduce(function (a, t) { return Math.max(a, short(t).length); }, 6));
    var edgeW = narrow ? 70 : 132, padR = 6, headH = 30, cellH = narrow ? 18 : 22, gap = 3;
    var x = d3.scaleBand().domain(d3.range(n)).range([labW, W - padR - edgeW]).paddingInner(0.18);
    var cw = x.bandwidth(), half = Math.max(3, (cw - 2) / 2);
    var H = headH + tasks.length * (cellH + gap) + 4;
    var firstCounts = {};
    tasks.forEach(function (t) { var f = m.firstSolver[t]; if (f) firstCounts[f] = (firstCounts[f] || 0) + 1; });
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", tasks.length + " tasks by " + n + " generations, both lineages in every cell (left " + m.A.family + ", right " + m.B.family + "), filled to the pass rate; the first solver named at the row's edge: " + m.lineages.map(function (l) { return l.family + " first on " + (firstCounts[l.family] || 0); }).join(", "));
    var bands = svg.append("g").selectAll("rect").data(d3.range(n)).enter().append("rect").attr("class", "evc-col").attr("data-index", function (k) { return k; })
      .attr("x", function (k) { return x(k) - 1.5; }).attr("y", headH - 14).attr("width", cw + 3).attr("height", H - headH + 12).attr("fill", "var(--ink)").attr("fill-opacity", 0).attr("rx", 3);
    var heads = svg.append("g").selectAll("g").data(d3.range(n)).enter().append("g").attr("class", "evc-rhead hit").attr("data-index", function (k) { return k; })
      .on("click", function (evt, k) { tip.hide(); select({ gen: S.gen === k ? null : k }); })
      .on("pointermove", function (evt, k) { tip.show(evt, genLines(m, k)); }).on("pointerleave", tip.hide);
    heads.append("rect").attr("x", function (k) { return x(k); }).attr("y", 0).attr("width", cw).attr("height", headH - 4).attr("fill", "transparent");
    var every = Math.max(1, Math.ceil(26 / Math.max(1, cw)));
    heads.append("text").attr("class", "lab mono").attr("text-anchor", "middle").attr("x", function (k) { return x(k) + cw / 2; }).attr("y", 12)
      .text(function (k) { return k % every === 0 || k === n - 1 ? trunc(idAt(m, k), Math.max(2, Math.floor(cw / 6.2))) : ""; });
    svg.append("text").attr("class", "tick").attr("x", W - padR - edgeW + 6).attr("y", 12).text(fit(narrow ? "first" : "first solver", edgeW - 8));
    tasks.forEach(function (t, ti) {
      var yy = headH + ti * (cellH + gap), mid = yy + cellH / 2;
      svg.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", mid + 4).attr("text-anchor", "end").text(trunc(short(t), Math.floor((labW - 10) / 6.6))).append("title").text(t);
      var cellRows = m.trTasks[t] || {};
      m.lineages.forEach(function (l, li) {
        var row = cellRows[l.family] && typeof cellRows[l.family] === "object" ? cellRows[l.family] : null;
        var curve = row && Array.isArray(row.pass_curve) ? row.pass_curve : [];
        var first = row && isNum(row.first_solved_index) ? row.first_solved_index : null;
        for (var k = 0; k < n; k++) {
          var v = isNum(curve[k]) ? curve[k] : null;
          var cx = x(k) + li * (half + 2);
          var cell = svg.append("g").attr("class", "evc-cell").attr("data-task", t).attr("data-family", l.family).attr("data-index", k).attr("data-pass", v === null ? "" : v.toFixed(3)).attr("data-first", first === k ? "1" : "0").style("cursor", "pointer");
          cell.append("rect").attr("x", cx).attr("y", yy).attr("width", half).attr("height", cellH).attr("rx", 2)
            .attr("fill", v === null ? "none" : l.color).attr("fill-opacity", v === null ? 0 : 0.1 + 0.9 * v)
            .attr("stroke", v === null ? "var(--rule)" : "none").attr("stroke-dasharray", v === null ? "2 2" : null);
          if (first === k) cell.append("circle").attr("class", "first").attr("cx", cx + half / 2).attr("cy", yy + cellH - 4).attr("r", 2).attr("fill", "var(--ink)");
          (function (kk, vv, ll, rr) {
            cell.on("pointermove", function (evt) {
              var lines = [{ b: true, text: short(t) + " · generation " + idAt(m, kk) }];
              m.lineages.forEach(function (l2) {
                var r2 = cellRows[l2.family] || {}, c2 = Array.isArray(r2.pass_curve) ? r2.pass_curve[kk] : null, g2 = pointAt(l2, kk);
                lines.push({ mono: true, text: l2.family + (g2 ? " " + g2.id : "") + ": " + (isNum(c2) ? "pass " + pct(c2) : "not run") + (isNum(r2.first_solved_index) && r2.first_solved_index === kk ? " · first solved here" : "") });
              });
              lines.push({ text: "click to select this generation" + (taskOnPage(t) ? " and open the task" : "") });
              tip.show(evt, lines);
            }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); select({ gen: S.gen === kk ? null : kk }); selectTask(ctx, t); });
          })(k, v, l, row);
        }
      });
      // the row's edge: the first solver, and any lineage that never solved it
      var fs = m.firstSolver[t], fl = fs ? byFamily(m, fs) : null;
      var ex = W - padR - edgeW + 6;
      var never = m.lineages.filter(function (l) { return Array.isArray(m.neverSolved[l.family]) && m.neverSolved[l.family].indexOf(t) >= 0; });
      var eg = svg.append("g").attr("class", "evc-edge").attr("data-task", t).attr("data-first", fs || "").attr("data-never", never.map(function (l) { return l.family; }).join(","));
      if (fl) {
        var fr = (m.trTasks[t] || {})[fs] || {};
        eg.append("circle").attr("cx", ex + 4).attr("cy", mid).attr("r", 3.5).attr("fill", fl.color);
        eg.append("text").attr("class", "lab").attr("x", ex + 11).attr("y", mid + 4).attr("fill", fl.color).text(fit(fl.family + (fr.first_solved_id ? " " + fr.first_solved_id : ""), edgeW - 14 - (never.length ? 14 : 0)));
        eg.append("title").text(fs + " solved " + short(t) + " first" + (fr.first_solved_id ? ", at " + fr.first_solved_id : "") + (isNum(fr.first_solved_episodes_cum) ? " after " + fr.first_solved_episodes_cum + " episodes" : ""));
      } else {
        var both = m.lineages.filter(function (l) { var r = (m.trTasks[t] || {})[l.family]; return r && isNum(r.first_solved_index); });
        eg.append("text").attr("class", "tick").attr("x", ex).attr("y", mid + 4).text(both.length === m.lineages.length ? "tie" : both.length ? "—" : "unsolved");
        eg.append("title").text(both.length === m.lineages.length ? "both solved it at the same episode count" : both.length ? "only " + both[0].family + " solved it, but not first by the rule" : "no lineage solved it");
      }
      never.forEach(function (l, ni) {
        eg.append("text").attr("class", "lab strong").attr("x", W - padR - 2 - ni * 12).attr("y", mid + 4).attr("text-anchor", "end").attr("fill", l.color).text("✕").append("title").text(l.family + " never solved " + short(t));
      });
    });
    function apply(animate) {
      var d = dur(animate);
      (d ? bands.transition().duration(d) : bands).attr("fill-opacity", function (k) { return k === S.gen ? 0.07 : 0; });
      heads.select("text").attr("class", function (k) { return "lab mono" + (k === S.gen ? " strong" : ""); });
      heads.attr("aria-current", function (k) { return k === S.gen ? "true" : "false"; });
    }
    apply(false);
    ref.apply = apply;
  }

  AgentDiff.block({
    id: "evc-race",
    title: "The task race",
    question: "Per task, generation by generation, both lineages' pass rates side by side — who solved what first, and which tasks a lineage never solved.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.raceTasks.length && m.maxN ? 0.91 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-race" });
      el.appendChild(root);
      var tip = tooltip(root);
      var counts = {}, ties = 0, unsolved = 0;
      m.raceTasks.forEach(function (t) {
        var f = m.firstSolver[t];
        if (f) { counts[f] = (counts[f] || 0) + 1; return; }
        var any = m.lineages.some(function (l) { var r = (m.trTasks[t] || {})[l.family]; return r && isNum(r.first_solved_index); });
        if (any) ties++; else unsolved++;
      });
      var neverWords = m.lineages.filter(function (l) { return Array.isArray(m.neverSolved[l.family]) && m.neverSolved[l.family].length; })
        .map(function (l) { return l.family + " never solved " + m.neverSolved[l.family].map(short).join(", "); });
      root.appendChild(H("p", { class: "evc-lede", "data-ties": ties, "data-unsolved": unsolved, text:
        m.lineages.map(function (l) { return l.family + " first on " + (counts[l.family] || 0); }).join(", ") + " of " + m.raceTasks.length + " shared task" + (m.raceTasks.length === 1 ? "" : "s")
        + (ties ? "; " + ties + " reached at the same episode count" : "") + (unsolved ? "; " + unsolved + " solved by neither" : "") + ". " + (neverWords.length ? cap(neverWords.join("; ")) + "." : "Every shared task was solved by both lineages at some generation.") }));
      var bar = H("div", { class: "evc-bar" });
      m.lineages.forEach(function (l) { bar.appendChild(H("span", { class: "evc-chip" }, [H("i", { style: { background: l.color, borderRadius: "2px" } }), H("b", { text: l.family }), H("span", { text: l.side === "a" ? " · left mark" : " · right mark" })])); });
      bar.appendChild(H("span", { text: "fill = pass rate · dot = first solved · ✕ = never solved" }));
      root.appendChild(bar);
      var refs = {};
      var host = H("div", { class: "evc-chart" });
      root.appendChild(responsive(host, function () { drawRace(host, ctx, m, tip, refs); }, "evc-race"));
      root.appendChild(H("p", { class: "evc-note", text: "A cell is one task at one generation index, the left mark the first lineage and the right the second, each filled to that generation's pass rate on the task (a count over its runs); a dashed mark is a generation the lineage does not have. " + (m.raceRule ? cap(m.raceRule) + ". " : "") + "Hover for the numbers; click a column to select that generation across the comparison blocks." }));
      listen(root, function (changed) { if (changed.gen && refs.apply) refs.apply(true); });
    },
  });

  // ======================================================== evc-mechanisms

  function mechRows(m) {
    var byMech = {};
    m.lineages.forEach(function (l) {
      var mechs = l.process && l.process.mechanisms && typeof l.process.mechanisms === "object" ? l.process.mechanisms : {};
      Object.keys(mechs).forEach(function (k) {
        var c = mechs[k]; if (!c || typeof c !== "object") return;
        var deltas = l.steps.filter(function (s) { return s.mechanism === k && isNum(s.dIqm); }).map(function (s) { return s.dIqm; });
        var row = byMech[k] || (byMech[k] = { mech: k, per: {} });
        row.per[l.family] = { steps: count(c.steps), mean: isNum(c.mean_delta) ? c.mean_delta : null, improved: count(c.improved), regressed: count(c.regressed),
          up: count(c.delta_positive), down: count(c.delta_negative), to: Array.isArray(c.to) ? c.to.map(String) : [],
          min: deltas.length ? Math.min.apply(null, deltas) : null, max: deltas.length ? Math.max.apply(null, deltas) : null,
          verdicts: c.verdicts && typeof c.verdicts === "object" ? c.verdicts : {} };
      });
    });
    var rows = Object.keys(byMech).map(function (k) { return byMech[k]; });
    rows.forEach(function (r) { r.top = Math.max.apply(null, Object.keys(r.per).map(function (f) { return isNum(r.per[f].mean) ? r.per[f].mean : -Infinity; })); });
    rows.sort(function (p, q) { return q.top - p.top || (p.mech < q.mech ? -1 : 1); });
    return rows;
  }
  function bestMechanism(l, rows) {
    if (l.process && l.process.best_paying_mechanism) {
      var k = String(l.process.best_paying_mechanism), r = rows.filter(function (x) { return x.mech === k; })[0];
      return r && r.per[l.family] ? { mech: k, cell: r.per[l.family] } : { mech: k, cell: null };
    }
    var best = null;
    rows.forEach(function (r) { var c = r.per[l.family]; if (c && isNum(c.mean) && c.steps > 0 && (!best || c.mean > best.cell.mean)) best = { mech: r.mech, cell: c }; });
    return best;
  }
  function drawMechanisms(host, ctx, m, rows, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 520;
    var labW = narrow ? 74 : 104, padR = 6, rowH = 32, padT = 16, padB = 22;
    //: the count column is as wide as its longest entry, so it never runs past the edge
    function valText(c) { return narrow ? c.steps + " · +" + c.improved + "/−" + c.regressed : c.steps + " step" + (c.steps === 1 ? "" : "s") + " · +" + c.improved + " / −" + c.regressed; }
    var valHead = narrow ? "n · +/−" : "steps · improved/regressed";
    var valW = 10 + CH * rows.reduce(function (a, r) { return Object.keys(r.per).reduce(function (b, f) { return Math.max(b, valText(r.per[f]).length); }, a); }, valHead.length);
    var lo = 0, hi = 0;
    rows.forEach(function (r) { Object.keys(r.per).forEach(function (f) { var c = r.per[f]; [c.mean, c.min, c.max].forEach(function (v) { if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); }); });
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    var x = d3.scaleLinear().domain([lo, hi]).nice().range([labW, W - padR - valW]);
    var H = padT + rows.length * rowH + padB;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "per mechanism of self-modification and per lineage, the mean IQM change of its steps as a dot sized by the step count, the range of its steps as a line, and improved against regressed at the right: " + rows.map(function (r) { return r.mech; }).join(", "));
    svg.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0)).attr("y1", padT - 6).attr("y2", H - padB + 2);
    x.ticks(narrow ? 3 : 5).forEach(function (t) { svg.append("text").attr("class", "tick").attr("x", x(t)).attr("y", H - 6).attr("text-anchor", "middle").text(signed(t, 1)); });
    svg.append("text").attr("class", "lab dim").attr("x", x(0) + 4).attr("y", 10).text(fit("mean ΔIQM per step →", W - padR - valW - x(0) - 4));
    svg.append("text").attr("class", "tick").attr("x", W - padR - valW + 4).attr("y", 10).text(valHead);
    var rScale = d3.scaleSqrt().domain([0, Math.max(1, rows.reduce(function (a, r) { return Math.max(a, Object.keys(r.per).reduce(function (b, f) { return Math.max(b, r.per[f].steps); }, 0)); }, 0))]).range([2.5, 7]);
    rows.forEach(function (r, i) {
      var top = padT + i * rowH;
      var g = svg.append("g").attr("class", "evc-mrow").attr("data-mechanism", r.mech);
      g.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", top + rowH / 2 + 4).attr("text-anchor", "end").text(trunc(r.mech, Math.floor((labW - 10) / 6.6)));
      m.lineages.forEach(function (l, li) {
        var c = r.per[l.family], y = top + 9 + li * 13;
        if (!c) { g.append("text").attr("class", "tick").attr("x", x(0) + 6).attr("y", y + 4).attr("fill", l.color).attr("opacity", 0.7).text(narrow ? "—" : "no step"); return; }
        var mg = g.append("g").attr("class", "evc-mdot").attr("data-family", l.family).attr("data-steps", c.steps).attr("data-mean", isNum(c.mean) ? c.mean : "");
        if (isNum(c.min) && isNum(c.max) && c.max > c.min) mg.append("line").attr("x1", x(c.min)).attr("x2", x(c.max)).attr("y1", y).attr("y2", y).attr("stroke", l.color).attr("stroke-width", 2).attr("stroke-opacity", 0.4).attr("stroke-linecap", "round");
        if (isNum(c.mean)) mg.append("circle").attr("cx", x(c.mean)).attr("cy", y).attr("r", rScale(c.steps)).attr("fill", l.color).attr("fill-opacity", 0.9);
        mg.append("text").attr("class", "tick").attr("x", W - padR - valW + 4).attr("y", y + 4).attr("fill", l.color).text(valText(c));
        mg.append("rect").attr("x", labW).attr("y", y - 6).attr("width", W - labW - padR).attr("height", 13).attr("fill", "transparent");
        mg.on("pointermove", function (evt) {
          var vs = Object.keys(c.verdicts).sort().map(function (k) { return c.verdicts[k] + " " + k; }).join(", ");
          tip.show(evt, [{ b: true, text: l.family + " · " + r.mech }, { mono: true, text: c.steps + " step" + (c.steps === 1 ? "" : "s") + (c.to.length ? " (into " + c.to.join(", ") + ")" : "") + " · mean ΔIQM " + signed(c.mean) + (isNum(c.min) && isNum(c.max) && c.max > c.min ? " · range " + signed(c.min) + " to " + signed(c.max) : "") },
            { text: c.improved + " improved, " + c.regressed + " regressed" + (vs ? " · verdicts: " + vs : "") + " · " + c.up + " step" + (c.up === 1 ? "" : "s") + " with a positive delta, " + c.down + " negative" }]);
        }).on("pointerleave", tip.hide);
      });
    });
  }

  AgentDiff.block({
    id: "evc-mechanisms",
    title: "Which self-modification paid",
    question: "Per lineage, per kind of change — a rule, a memory, a config key, a skill — how many steps used it and what they did to the IQM, and which kind paid best.",
    group: "evolution",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); return m.ok && mechRows(m).length ? 0.9 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-mechanisms" });
      el.appendChild(root);
      var tip = tooltip(root);
      var rows = mechRows(m);
      var sentences = m.lineages.map(function (l) {
        var b = bestMechanism(l, rows);
        if (!b) return l.family + " has no mechanism with a measured step";
        var c = b.cell;
        return "for " + l.family + ", " + b.mech + " paid best" + (c ? ": mean ΔIQM " + signed(c.mean) + " over " + c.steps + " step" + (c.steps === 1 ? "" : "s") + " (" + c.improved + " improved, " + c.regressed + " regressed)" : "");
      });
      root.appendChild(H("p", { class: "evc-lede", "data-best": m.lineages.map(function (l) { var b = bestMechanism(l, rows); return b ? b.mech : ""; }).join(","), text: cap(sentences.join("; ")) + "." }));
      var bar = H("div", { class: "evc-bar" });
      m.lineages.forEach(function (l) { bar.appendChild(H("span", { class: "evc-chip" }, [H("i", { style: { background: l.color } }), H("b", { text: l.family })])); });
      bar.appendChild(H("span", { text: "dot = mean ΔIQM, sized by steps · line = the range of its steps" }));
      root.appendChild(bar);
      var host = H("div", { class: "evc-chart" });
      root.appendChild(responsive(host, function () { drawMechanisms(host, ctx, m, rows, tip); }, "evc-mechanisms"));
      root.appendChild(H("p", { class: "evc-note", text: "A mechanism is the kind of change a step made to the agent's own artifacts (the step's declared mechanism; \"mixed\" is more than one). The dot is the mean of its steps' IQM deltas in that lineage, the line runs from the lowest to the highest of them, and the count at the right says how many steps the engine's verdict called improved and regressed — a mechanism can lift the mean and still be called flat when the interval spans the coin flip. Sorted by the better lineage's mean." }));
    },
  });

  // ======================================================== evc-divergence

  function divergenceReading(m) {
    var A = m.A, B = m.B;
    var ds = m.byGen.filter(function (r) { return isNum(r.distance); });
    if (!ds.length) return "The behaviour distance between " + A.family + " and " + B.family + " at the same generation was not measured" + (m.byGen.length && m.byGen[0].reason ? ": " + m.byGen[0].reason : "") + ".";
    var first = ds[0], last = ds[ds.length - 1], mn = ds[0], mx = ds[0];
    ds.forEach(function (r) { if (r.distance < mn.distance) mn = r; if (r.distance > mx.distance) mx = r; });
    var dir = ds.length < 2 ? "one generation only, so no direction" : last.distance < first.distance - 0.02 ? "converging" : last.distance > first.distance + 0.02 ? "diverging" : "neither converging nor diverging";
    return "At the same generation index the two behaviours sit " + num(first.distance) + " apart at " + idAt(m, first.index) + " and " + num(last.distance) + " at " + idAt(m, last.index)
      + (ds.length > 2 ? " (closest " + num(mn.distance) + " at " + idAt(m, mn.index) + ", farthest " + num(mx.distance) + " at " + idAt(m, mx.index) + ")" : "") + ": " + dir + ".";
  }
  function drawDistance(host, ctx, m, tip, ref) {
    if (!d3) return;
    var W = width(host), H = 170, padL = 40, padR = 12, padT = 16, padB = 26;
    var n = m.maxN, rows = m.byGen.filter(function (r) { return isNum(r.distance); });
    var x = d3.scaleLinear().domain([0, Math.max(1, n - 1)]).range([padL + 6, W - padR - 6]);
    var top = rows.reduce(function (a, r) { return Math.max(a, r.distance); }, 0);
    var y = d3.scaleLinear().domain([0, top || 1]).nice().range([H - padB, padT]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "behaviour distance between " + m.A.family + " and " + m.B.family + " at the same generation index: " + rows.map(function (r) { return idAt(m, r.index) + " " + num(r.distance); }).join(", "));
    y.ticks(3).forEach(function (t) {
      svg.append("line").attr("class", t === 0 ? "zero" : "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(t)).attr("y2", y(t)).attr("stroke-dasharray", t === 0 ? null : "1 3");
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(t) + 4).attr("text-anchor", "end").text(num(t));
    });
    svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 10).text(fit("behaviour distance, A@k against B@k", W - padL - 4));
    var line = d3.line().x(function (r) { return x(r.index); }).y(function (r) { return y(r.distance); });
    if (rows.length > 1) svg.append("path").attr("class", "evc-dline").attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.8).attr("d", line(rows));
    var dots = svg.append("g").selectAll("circle").data(rows).enter().append("circle").attr("class", "evc-dpt").attr("data-index", function (r) { return r.index; })
      .attr("cx", function (r) { return x(r.index); }).attr("cy", function (r) { return y(r.distance); }).attr("r", 3.2).attr("fill", "var(--ink)");
    var every = Math.max(1, Math.ceil(30 / Math.max(1, (W - padL - padR) / Math.max(1, n - 1))));
    for (var k = 0; k < n; k++) if (k % every === 0 || k === n - 1) svg.append("text").attr("class", "tick").attr("x", x(k)).attr("y", H - 8).attr("text-anchor", "middle").text(trunc(idAt(m, k), 5));
    var tracker = svg.append("line").attr("class", "evc-tracker").attr("y1", padT).attr("y2", H - padB).attr("stroke", "var(--ink)").attr("stroke-dasharray", "2 3").attr("stroke-opacity", 0).attr("x1", x(0)).attr("x2", x(0));
    var surface = svg.append("rect").attr("x", padL).attr("y", padT).attr("width", Math.max(1, W - padL - padR)).attr("height", Math.max(1, H - padT - padB)).attr("fill", "transparent").style("cursor", "pointer");
    function nearest(evt) { var box = svg.node().getBoundingClientRect(); var px = (evt.clientX - box.left) * (W / Math.max(1, box.width)); return Math.max(0, Math.min(n - 1, Math.round(x.invert(px)))); }
    surface.on("pointermove", function (evt) { var k = nearest(evt), r = m.byGen.filter(function (q) { return q.index === k; })[0]; tip.show(evt, [{ b: true, text: "generation " + idAt(m, k) }, { mono: true, text: r && isNum(r.distance) ? "distance " + num(r.distance) : "distance not measured" + (r && r.reason ? ": " + r.reason : "") }, { text: "click to select this generation" }]); })
      .on("pointerleave", tip.hide).on("click", function (evt) { var k = nearest(evt); tip.hide(); select({ gen: S.gen === k ? null : k }); });
    function apply(animate) {
      var d = dur(animate);
      var t = d ? tracker.transition().duration(d) : tracker;
      if (S.gen !== null) t.attr("x1", x(S.gen)).attr("x2", x(S.gen)).attr("stroke-opacity", 0.5); else t.attr("stroke-opacity", 0);
      (d ? dots.transition().duration(d) : dots).attr("r", function (r) { return r.index === S.gen ? 5.5 : 3.2; });
    }
    apply(false);
    ref.apply = apply;
  }
  function drawPk(host, ctx, m, tip, ref) {
    if (!d3) return;
    var W = width(host), H = 170, padL = 40, padR = 12, padT = 16, padB = 26;
    var A = m.A, B = m.B, n = m.maxN, rows = m.byGen.filter(function (r) { return isNum(r.p); });
    var x = d3.scaleLinear().domain([0, Math.max(1, n - 1)]).range([padL + 6, W - padR - 6]);
    var y = d3.scaleLinear().domain([0, 1]).range([H - padB, padT]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "probability that a random run of " + B.family + " beats a random run of " + A.family + " at the same generation index, with its bootstrap band: " + rows.map(function (r) { return idAt(m, r.index) + " " + pct(r.p) + " [" + pct(r.lo) + ", " + pct(r.hi) + "]"; }).join(", "));
    [0, 0.5, 1].forEach(function (f) {
      svg.append("line").attr("class", f === 0.5 ? "zero" : "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(f)).attr("y2", y(f)).attr("stroke-dasharray", f === 0.5 ? "2 4" : "1 3");
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(f) + 4).attr("text-anchor", "end").text(pct(f));
    });
    svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 10).text(fit("P(" + B.family + "@k > " + A.family + "@k) · 50% is a coin flip", W - padL - 4));
    var band = rows.filter(function (r) { return isNum(r.lo) && isNum(r.hi); });
    var area = d3.area().x(function (r) { return x(r.index); }).y0(function (r) { return y(r.lo); }).y1(function (r) { return y(r.hi); });
    if (band.length > 1) svg.append("path").attr("class", "evc-pband").attr("fill", "var(--ink-3)").attr("fill-opacity", 0.16).attr("d", area(band));
    var line = d3.line().x(function (r) { return x(r.index); }).y(function (r) { return y(r.p); });
    if (rows.length > 1) svg.append("path").attr("class", "evc-pline").attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-width", 1.6).attr("d", line(rows));
    var dots = svg.append("g").selectAll("circle").data(rows).enter().append("circle").attr("class", "evc-ppt").attr("data-index", function (r) { return r.index; }).attr("data-separates", function (r) { return r.separates || (isNum(r.lo) && r.lo > 0.5 ? B.family : isNum(r.hi) && r.hi < 0.5 ? A.family : ""); })
      .attr("cx", function (r) { return x(r.index); }).attr("cy", function (r) { return y(r.p); }).attr("r", 3.4)
      .attr("fill", function (r) { return isNum(r.lo) && r.lo > 0.5 ? B.color : isNum(r.hi) && r.hi < 0.5 ? A.color : "var(--surface)"; }).attr("stroke", "var(--ink-2)").attr("stroke-width", 1.2);
    var every = Math.max(1, Math.ceil(30 / Math.max(1, (W - padL - padR) / Math.max(1, n - 1))));
    for (var k = 0; k < n; k++) if (k % every === 0 || k === n - 1) svg.append("text").attr("class", "tick").attr("x", x(k)).attr("y", H - 8).attr("text-anchor", "middle").text(trunc(idAt(m, k), 5));
    var tracker = svg.append("line").attr("class", "evc-tracker").attr("y1", padT).attr("y2", H - padB).attr("stroke", "var(--ink)").attr("stroke-dasharray", "2 3").attr("stroke-opacity", 0).attr("x1", x(0)).attr("x2", x(0));
    var surface = svg.append("rect").attr("x", padL).attr("y", padT).attr("width", Math.max(1, W - padL - padR)).attr("height", Math.max(1, H - padT - padB)).attr("fill", "transparent").style("cursor", "pointer");
    function nearest(evt) { var box = svg.node().getBoundingClientRect(); var px = (evt.clientX - box.left) * (W / Math.max(1, box.width)); return Math.max(0, Math.min(n - 1, Math.round(x.invert(px)))); }
    surface.on("pointermove", function (evt) {
      var k = nearest(evt), r = m.byGen.filter(function (q) { return q.index === k; })[0];
      var lines = [{ b: true, text: "generation " + idAt(m, k) }];
      if (r && isNum(r.p)) {
        lines.push({ mono: true, text: "P(" + B.family + " > " + A.family + ") " + pct(r.p) + " [" + pct(r.lo) + ", " + pct(r.hi) + "]" + (isNum(r.lo) && r.lo > 0.5 ? " · " + B.family + " ahead" : isNum(r.hi) && r.hi < 0.5 ? " · " + A.family + " ahead" : " · does not separate") });
        if (r.a && r.b) lines.push({ mono: true, text: A.family + " " + (r.a.id || "") + " " + num(r.a.point) + " · " + B.family + " " + (r.b.id || "") + " " + num(r.b.point) });
      } else lines.push({ text: "not measured" + (r && r.reason ? ": " + r.reason : "") });
      lines.push({ text: "click to select this generation" });
      tip.show(evt, lines);
    }).on("pointerleave", tip.hide).on("click", function (evt) { var k = nearest(evt); tip.hide(); select({ gen: S.gen === k ? null : k }); });
    function apply(animate) {
      var d = dur(animate);
      var t = d ? tracker.transition().duration(d) : tracker;
      if (S.gen !== null) t.attr("x1", x(S.gen)).attr("x2", x(S.gen)).attr("stroke-opacity", 0.5); else t.attr("stroke-opacity", 0);
      (d ? dots.transition().duration(d) : dots).attr("r", function (r) { return r.index === S.gen ? 5.5 : 3.4; });
    }
    apply(false);
    ref.apply = apply;
  }

  AgentDiff.block({
    id: "evc-divergence",
    title: "Are they becoming the same agent?",
    question: "Generation by generation: how far apart the two lineages' behaviours sit, and how often a run of one beats a run of the other — converging, diverging, or neither.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.byGen.length ? 0.89 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evc evc-divergence" });
      el.appendChild(root);
      var tip = tooltip(root);
      var reading = divergenceReading(m);
      root.appendChild(H("p", { class: "evc-lede evc-div-reading", "data-direction": /converging/.test(reading) && !/neither/.test(reading) ? "converging" : /diverging/.test(reading) && !/neither/.test(reading) ? "diverging" : "neither", text: reading }));
      if (m.byGenReading) root.appendChild(H("p", { class: "evc-read evc-reading", text: cap(m.byGenReading) + (m.byGenReading.slice(-1) === "." ? "" : ".") }));
      var cols = H("div", { class: "evc-cols" });
      root.appendChild(cols);
      var refs = { d: {}, p: {} };
      var hasD = m.byGen.some(function (r) { return isNum(r.distance); }), hasP = m.byGen.some(function (r) { return isNum(r.p); });
      if (hasD) { var dh = H("div", { class: "evc-chart", "data-chart": "distance" }); cols.appendChild(responsive(dh, function () { drawDistance(dh, ctx, m, tip, refs.d); }, "evc-div-distance")); }
      else cols.appendChild(H("p", { class: "evc-read", "data-role": "unmeasured", text: "The behaviour distance per generation was not measured, so only the probability of improvement is drawn." }));
      if (hasP) { var ph = H("div", { class: "evc-chart", "data-chart": "p" }); cols.appendChild(responsive(ph, function () { drawPk(ph, ctx, m, tip, refs.p); }, "evc-div-p")); }
      else cols.appendChild(H("p", { class: "evc-read", "data-role": "unmeasured", text: "P(" + m.B.family + "@k > " + m.A.family + "@k) was not measured at any generation, so only the distance is drawn." }));
      root.appendChild(H("p", { class: "evc-note", text: "Left: the normalised edit distance between the two lineages' step-token streams at the same generation index, averaged over their episodes — 0 the same behaviour, 1 nothing in common; a falling line is two agents becoming one. Right: at each index, how often a random run of the second lineage's generation beats a random run of the first's on the same task, its band the stratified bootstrap; a filled point is an index where the interval clears the coin flip, in the colour of the lineage ahead. " + (m.byGenNote ? cap(m.byGenNote) + "." : "") }));
      listen(root, function (changed) { if (!changed.gen) return; if (refs.d.apply) refs.d.apply(true); if (refs.p.apply) refs.p.apply(true); });
    },
  });
})(typeof window !== "undefined" ? window : this);
