/* AgentDiff block — the timescape.
 *
 * Every episode of every generation of a self-evolving agent, laid along
 * constricted time, with details on demand from the whole lineage down to
 * one step. Four levels, each its own drawing, one zoom between them:
 *
 *   0  the lineage    one lane per generation: at each moment how many
 *                     episodes are active and what they are doing (thinking
 *                     light, a tool darker, the answer solid, an error red,
 *                     the verifier span hatched), the mean return so far as
 *                     a faint area beneath, the verdict of each evolution
 *                     step as a mark at the lane boundary
 *   1  a generation   its episodes as ribbons grouped by task, every step a
 *                     cell coloured by reward sign and intensity, a failed
 *                     episode dashed, a short one ending where it ended
 *   2  a task         its runs as aligned ribbons with the marks visible:
 *                     think, tool and answer glyphs, errors ringed, the
 *                     verifier span bracketed, the answer a square in the
 *                     outcome's colour; a step opens on click
 *   3  an episode     the impact block's thread — the same folds, the same
 *                     dilation on click, the reward bar under every step
 *
 * Time is constricted the way the impact block constricts it: a quiet
 * stretch folds into a dotted segment 6 + 6·log2(1 + units) long with a
 * "⋯" on the clock, and a click dilates it. Quiet here means what it
 * means there — steps may be present, but nothing eventful: no error, no
 * decisive or fault step, no answer, no reward beyond the ordinary shaping
 * cost (the modal per-step reward of the lineage); a wasted step is quiet
 * by definition. A stretch with no step at all is quiet
 * too, so a recording with idle time folds it. The folds are recomputed
 * for the scope in view, so a generation folds only its own quiet time.
 *
 * Scale: levels 0 and 1 draw on a canvas from per-pixel bins — one DOM
 * node per lane or per task, never per step — and hit-test by bisecting
 * row tops and step starts; levels 2 and 3 keep one node per step. The
 * contract's cap of 2,000 timelines is the drawing cap: an episode past
 * it carries no timeline and is counted, named, and not drawn. Past 400
 * episodes in one generation, level 1 draws per-task density bands
 * instead of ribbons.
 *
 * Reads `aggregate.evolution` (generations → episodes → timeline, steps →
 * verdict) and, for a step's input and the shared inspector, the pair
 * reports that cover the last step's runs. Every number drawn is a count
 * or a sum over the recorded timeline.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;
  var L = AgentDiff.lib;
  var isNum = L.fmt.isNum, secs = L.fmt.secs, signed = L.fmt.signed, short = L.fmt.short;

  var KEY = "agentdiff:evo-timescape";
  //: the contract's EPISODE_TIMELINE_CAP: an episode past it has no timeline to draw
  var DRAW_CAP = 2000;
  //: past this many episodes in one generation, level 1 draws per-task density bands
  var RIBBON_CAP = 400;
  //: a quiet stretch shorter than this stays on the clock: folding it would not shorten it
  var MIN_FOLD = { time: 1.0, steps: 2 };
  //: the wheel zooms into a lane past this factor, and out of a level under that
  var DESCEND_K = 2.6, ASCEND_K = 0.55;
  var LEVEL_NAME = ["lineage", "generation", "task", "episode"];
  var VERDICT = {
    improved: { g: "▲", c: "var(--good)" }, regressed: { g: "▼", c: "var(--bad)" }, flat: { g: "─", c: "var(--ink-3)" },
    gamed: { g: "⚠", c: "var(--bad)" }, overfit: { g: "◇", c: "var(--warn)" }, forgot: { g: "✕", c: "var(--bad)" }, traded: { g: "⇄", c: "var(--warn)" },
  };

  var CSS = [
    ".evt{position:relative}",
    ".evt-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px;min-width:0}",
    ".evt-crumbs{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;min-width:0}",
    ".evt-crumbs button{font:inherit;font-size:var(--fs-xs);font-family:var(--mono);border:0;background:transparent;color:var(--ink-2);padding:1px 4px;border-radius:4px;cursor:pointer;max-width:26ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".evt-crumbs button[aria-current=true]{color:var(--ink);font-weight:600;cursor:default}",
    ".evt-crumbs button:hover:not([aria-current=true]){background:var(--surface-2);color:var(--ink)}",
    ".evt-crumbs .sep{color:var(--ink-3)}",
    ".evt-seg{display:inline-flex;gap:2px;flex:0 0 auto}",
    ".evt-bar .evt-btn{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer;flex:0 0 auto;min-height:20px}",
    ".evt-bar .evt-btn[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
    ".evt-bar .evt-btn:focus-visible,.evt-crumbs button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".evt-status{font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 4px;min-height:1.4em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".evt-mini{position:relative;margin:0 0 6px;touch-action:none}",
    ".evt-mini canvas,.evt-stage canvas{position:absolute;left:0;top:0;display:block}",
    ".evt-mini svg,.evt-stage svg{position:absolute;left:0;top:0;display:block;font-family:var(--sans);overflow:visible}",
    ".evt-stage{position:relative;touch-action:none;outline:none;border-radius:4px}",
    ".evt-stage:focus-visible{outline:2px solid var(--accent);outline-offset:2px}",
    ".evt text{font-size:var(--fs-xs)}",
    ".evt .lab{fill:var(--ink-2)}.evt .lab.dim{fill:var(--ink-3)}.evt .lab.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ".evt .tick{fill:var(--ink-3);font-family:var(--mono);font-variant-numeric:tabular-nums;pointer-events:none}",
    ".evt .evt-fold{cursor:pointer}.evt .evt-fold text{fill:var(--ink-2);font-family:var(--mono);pointer-events:none}.evt .evt-fold:hover line{stroke:var(--ink)}",
    ".evt .evt-lane-hit{cursor:pointer}",
    ".evt .evt-step{cursor:pointer}.evt .evt-step text.g{font-weight:700;paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
    ".evt .evt-verdict{cursor:default}.evt .evt-verdict text{font-weight:700}",
    ".evt .brush .selection{fill:var(--accent);fill-opacity:.18;stroke:var(--accent);stroke-opacity:.6}",
    ".evt .brush .handle{fill:var(--accent);fill-opacity:.5}",
    ".evt .evt-sel{fill:none;stroke:var(--accent);stroke-width:1.5;pointer-events:none}",
    ".evt-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:320px}",
    ".evt-tip b{color:var(--ink)}",
    ".evt-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:8px}",
    ".evt-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
    ".evt-table td{padding:3px 10px 3px 0;color:var(--ink-2);white-space:nowrap}.evt-table td.g{color:var(--ink);font-family:var(--mono)}",
    ".evt-table td.pos{color:var(--good)}.evt-table td.neg{color:var(--bad)}",
    ".evt-detail{margin-top:8px;border-left:2px solid var(--rule-2);padding:2px 0 2px 10px;font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5}",
    ".evt-detail b{color:var(--ink);font-weight:600}.evt-detail .in{font-family:var(--mono);color:var(--ink-2);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".evt-detail button{font:inherit;font-size:var(--fs-xs);border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer;margin-left:6px}",
    ".evt-legend{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0;max-width:100ch;line-height:1.45}",
    ".evt-note{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0}",
  ].join("\n");
  function ensureStyle() { L.style.once("evotime", CSS); }

  // ------------------------------------------------------------ helpers
  //: no library counterpart: cut a label to n characters with an ellipsis, whitespace collapsed
  function trunc(s, n) { s = String(s === null || s === undefined ? "" : s).replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  //: ~6.2px per character at the page's small size; good enough to truncate by
  var CH = 6.2;
  function fit(text, px) { var n = Math.floor(px / CH); return n < 2 ? "" : trunc(text, n); }
  //: a "nice" tick interval: about a dozen ticks over the span
  function niceStep(total, steps) {
    var s = steps ? [1, 2, 5, 10, 20, 50, 100, 200, 500] : [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
    for (var i = 0; i < s.length; i++) if (total / s[i] <= 12) return s[i];
    return s[s.length - 1];
  }
  function unit(v, measure) { return measure === "steps" ? Math.round(v) + (Math.round(v) === 1 ? " step" : " steps") : secs(v); }
  function tickText(v, measure) { return measure === "steps" ? String(Math.round(v)) : secs(v); }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (err) { return false; }
  }
  function dur() { return prefersReduced() ? 0 : 320; }
  function cssVar(name) { try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888"; } catch (err) { return "#888"; } }
  /* Not the library's family: this state carries two windows (`view`,
   * `compare`) that are null or a pair, a nested zoom path and a clamped
   * level, and the family's type gate on a null default would drop a saved
   * pair on reload. One block, one key, the page's own Store, as the
   * README allows; the shape is validated field by field below. */
  function store() { try { return AgentDiff._internals && AgentDiff._internals.Store ? AgentDiff._internals.Store : null; } catch (err) { return null; } }
  function loadState() {
    var s = store(), v = s ? s.get(KEY) : null;
    if (!v || typeof v !== "object") v = {};
    return {
      level: isNum(v.level) ? clamp(Math.round(v.level), 0, 3) : 0,
      path: { gen: typeof (v.path && v.path.gen) === "string" ? v.path.gen : null, task: typeof (v.path && v.path.task) === "string" ? v.path.task : null, run: typeof (v.path && v.path.run) === "string" ? v.path.run : null },
      measure: v.measure === "steps" ? "steps" : "time",
      constrict: v.constrict !== false,
      view: Array.isArray(v.view) && v.view.length === 2 && isNum(v.view[0]) && isNum(v.view[1]) ? v.view.slice() : null,
      compare: Array.isArray(v.compare) && v.compare.length === 2 && isNum(v.compare[0]) && isNum(v.compare[1]) ? v.compare.slice() : null,
    };
  }
  function saveState(st) {
    var s = store(); if (!s) return;
    try { s.set(KEY, { level: st.level, path: st.path, measure: st.measure, constrict: st.constrict, view: st.view, compare: st.compare }); } catch (err) { /* quota; the session keeps it */ }
  }
  function tooltip(root) { return L.svg.tip(root, { class: "evt-tip", width: 320 }); }

  // -------------------------------------------------------------- model
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {}, ev = agg.evolution, reports = ctx.reports || [];
    if (cache && cache.ev === ev && cache.reports === reports) return cache.m;
    var m;
    try { m = build(ev, reports); } catch (err) { console.warn("AgentDiff timescape: model failed", err); m = { ok: false, gens: [], eps: [], steps: [], skipped: 0 }; }
    cache = { ev: ev, reports: reports, m: m };
    return m;
  }
  function episodeOf(raw, G, family) {
    var tl = raw.timeline, n = tl.length;
    var t0 = new Float64Array(n), du = new Float64Array(n), kind = new Int8Array(n), rw = new Float64Array(n), cum = new Float64Array(n), fl = new Array(n), nm = new Array(n);
    var acc = 0, T = 0;
    for (var i = 0; i < n; i++) {
      var e = Array.isArray(tl[i]) ? tl[i] : [];
      t0[i] = isNum(+e[0]) ? +e[0] : 0; du[i] = Math.max(0, isNum(+e[1]) ? +e[1] : 0);
      kind[i] = e[2] === "answer" ? 2 : e[2] === "tool" ? 1 : 0;
      nm[i] = e[3] ? String(e[3]) : (e[2] || "step");
      rw[i] = isNum(+e[4]) ? +e[4] : 0; acc += rw[i]; cum[i] = acc;
      fl[i] = e[5] ? String(e[5]) : "";
      if (t0[i] + du[i] > T) T = t0[i] + du[i];
    }
    var run = raw.run_id === null || raw.run_id === undefined ? "" : String(raw.run_id);
    return {
      id: G.id + "|" + raw.task_id + "|" + run, gen: G, task: String(raw.task_id || ""), run: run, trace_id: raw.trace_id || null,
      success: !!raw.success, ret: isNum(raw["return"]) ? raw["return"] : acc, n: n, t0: t0, dur: du, kind: kind, rw: rw, cum: cum, fl: fl, nm: nm, T: T,
      seconds: isNum(raw.seconds) ? raw.seconds : T, agent: family + "@" + G.id, tools: raw.tools || {}, errors: isNum(raw.errors) ? raw.errors : 0,
    };
  }
  function build(ev, reports) {
    var m = { ok: false, gens: [], eps: [], steps: [], skipped: 0, modal: 0, family: "", maxAbs: 0, T: 0, maxN: 0, coverage: {} };
    if (!ev || ev.measurable === false || !Array.isArray(ev.generations)) return m;
    m.family = ev.family || "agent";
    reports.forEach(function (r) {
      ["a", "b"].forEach(function (side) {
        var s = r && r[side]; if (!r.task || !s || !s.agent) return;
        m.coverage[r.task.id + "|" + s.agent.name + "|" + (s.run_id === null || s.run_id === undefined ? "" : String(s.run_id))] = { report: r, side: side };
      });
    });
    var counts = {};
    ev.generations.forEach(function (g, gi) {
      if (!g || typeof g !== "object") return;
      var G = { id: String(g.id || "g" + gi), index: gi, eps: [], tasks: [], n: isNum(g.episodes_n) ? g.episodes_n : 0, skipped: 0,
        pass_rate: isNum(g.pass_rate) ? g.pass_rate : null, mean_return: isNum(g.mean_return) ? g.mean_return : null, mechanism: g.mechanism || null, note: g.note || "" };
      (Array.isArray(g.episodes) ? g.episodes : []).forEach(function (raw) {
        if (!raw || !Array.isArray(raw.timeline) || !raw.timeline.length) { G.skipped++; m.skipped++; return; }
        var ep = episodeOf(raw, G, m.family);
        G.eps.push(ep); m.eps.push(ep);
        for (var i = 0; i < ep.n; i++) { var r = ep.rw[i]; if (r !== 0) { var k = String(r); counts[k] = (counts[k] || 0) + 1; } }
      });
      if (!G.n) G.n = G.eps.length + G.skipped;
      G.eps.forEach(function (ep) { if (G.tasks.indexOf(ep.task) < 0) G.tasks.push(ep.task); });
      G.tasks.sort();
      G.groups = G.tasks.map(function (t) { return { task: t, eps: G.eps.filter(function (e) { return e.task === t; }) }; });
      G.T = G.eps.reduce(function (t, e) { return Math.max(t, e.T); }, 0);
      G.maxN = G.eps.reduce(function (t, e) { return Math.max(t, e.n); }, 0);
      m.T = Math.max(m.T, G.T); m.maxN = Math.max(m.maxN, G.maxN);
      m.gens.push(G);
    });
    // the ordinary shaping cost: the most frequent non-zero reward — a step paying only that is quiet
    var best = null;
    Object.keys(counts).forEach(function (k) { if (best === null || counts[k] > counts[best]) best = k; });
    m.modal = best === null ? 0 : +best;
    // eventful: the answer, an error, the decisive step, the fault's path, or a reward beyond the
    // shaping cost. A wasted step (w) is the quiet the folds exist for, and being inside the
    // verifier span (v) is drawn, not an event — the errors inside it are.
    m.eps.forEach(function (ep) {
      ep.ev = new Uint8Array(ep.n);
      for (var i = 0; i < ep.n; i++) {
        var r = ep.rw[i], fl = ep.fl[i];
        ep.ev[i] = ep.kind[i] === 2 || fl.indexOf("e") >= 0 || fl.indexOf("d") >= 0 || fl.indexOf("f") >= 0 || (r !== 0 && r !== m.modal) ? 1 : 0;
        if (Math.abs(r) > m.maxAbs) m.maxAbs = Math.abs(r);
      }
    });
    (Array.isArray(ev.steps) ? ev.steps : []).forEach(function (s) {
      if (!s || typeof s !== "object") return;
      m.steps.push({ from: String(s.from), to: String(s.to), verdict: String(s.verdict || "flat"), reading: s.reading || "", summary: s.diff && s.diff.summary ? String(s.diff.summary) : "" });
    });
    m.ok = m.eps.length > 0;
    return m;
  }
  //: an episode's x-units: its wall-clock, or its step index
  function axisOf(ep, measure) {
    if (measure !== "steps") return { t0: ep.t0, dur: ep.dur, T: ep.T };
    if (!ep._s) {
      var t0 = new Float64Array(ep.n), du = new Float64Array(ep.n);
      for (var i = 0; i < ep.n; i++) { t0[i] = i; du[i] = 1; }
      ep._s = { t0: t0, dur: du, T: ep.n };
    }
    return ep._s;
  }
  function spanOf(eps, measure) { var T = 0; eps.forEach(function (e) { var a = axisOf(e, measure); if (a.T > T) T = a.T; }); return T; }

  /* The scope: which episodes the level shows, resolved from the path; a
   * path that no longer resolves degrades to the deepest level that does. */
  function resolve(m, st) {
    var gen = null, grp = null, ep = null;
    m.gens.forEach(function (g) { if (g.id === st.path.gen) gen = g; });
    if (gen) gen.groups.forEach(function (g) { if (g.task === st.path.task) grp = g; });
    if (grp) grp.eps.forEach(function (e) { if (e.run === st.path.run) ep = e; });
    var level = st.level;
    if (level >= 3 && !ep) level = 2;
    if (level >= 2 && !grp) level = 1;
    if (level >= 1 && !gen) level = 0;
    st.level = level;
    if (level === 0) return { level: 0, eps: m.eps, gen: null, grp: null, ep: null, key: "L0" };
    if (level === 1) return { level: 1, eps: gen.eps, gen: gen, grp: null, ep: null, key: "L1:" + gen.id };
    if (level === 2) return { level: 2, eps: grp.eps, gen: gen, grp: grp, ep: null, key: "L2:" + gen.id + "/" + grp.task };
    return { level: 3, eps: [ep], gen: gen, grp: grp, ep: ep, key: "L3:" + ep.id };
  }

  /* The quiet stretches of a scope: the complement of every eventful step's
   * interval, from 0 to the scope's end, kept when long enough to fold. */
  function quietOf(eps, measure) {
    var iv = [];
    eps.forEach(function (ep) {
      var ax = axisOf(ep, measure);
      for (var i = 0; i < ep.n; i++) if (ep.ev[i]) iv.push([ax.t0[i], ax.t0[i] + ax.dur[i]]);
    });
    iv.sort(function (a, b) { return a[0] - b[0]; });
    var T = spanOf(eps, measure), out = [], cur = 0, min = MIN_FOLD[measure] || 1;
    iv.forEach(function (p) {
      if (p[0] > cur + 1e-9 && p[0] - cur >= min) out.push({ a: cur, b: p[0] });
      if (p[1] > cur) cur = p[1];
    });
    if (T - cur >= min) out.push({ a: cur, b: T });
    out.forEach(function (q) { q.id = q.a.toFixed(3) + "-" + q.b.toFixed(3); });
    return out;
  }
  function stepsIn(eps, measure, a, b) {
    var n = 0;
    eps.forEach(function (ep) { var ax = axisOf(ep, measure); for (var i = 0; i < ep.n; i++) if (ax.t0[i] >= a && ax.t0[i] < b) n++; });
    return n;
  }

  /* The constricted scale over a window: a polylinear d3 scale whose
   * breakpoints are the folds. A kept stretch is k px per unit, a fold is
   * its log-length; the folds together never take more than half the
   * width, so the clock keeps at least the other half. */
  function constrict(wA, wB, quiet, unfolded, on, x0, x1, measure) {
    var segs = [], cur = wA, min = 1e-9;
    if (on) quiet.forEach(function (q) {
      var a = Math.max(q.a, wA), b = Math.min(q.b, wB);
      if (b - a <= min || unfolded[q.id]) return;
      if (a > cur + min) segs.push({ a: cur, b: a, fold: false });
      segs.push({ a: a, b: b, fold: true, q: q });
      cur = b;
    });
    if (wB > cur + min) segs.push({ a: cur, b: wB, fold: false });
    if (!segs.length) segs.push({ a: wA, b: wA + 1, fold: false });
    var avail = Math.max(10, x1 - x0), kept = 0, foldPx = 0;
    //: the fold law is the library's (the impact block's: 4–5 units ≈ 20px, 11 ≈ 27px, 100 ≈ 46px), over steps or over seconds by the measure
    var foldLaw = measure === "steps" ? L.glyph.foldWidth : L.glyph.foldSeconds;
    segs.forEach(function (s) { if (s.fold) foldPx += foldLaw(s.b - s.a); else kept += s.b - s.a; });
    var fs = kept > 0 ? (foldPx > avail * 0.5 ? avail * 0.5 / foldPx : 1) : (foldPx > 0 ? avail / foldPx : 1);
    var k = kept > 0 ? Math.max(0, avail - foldPx * fs) / kept : 0;
    var dom = [], rng = [], x = x0, folds = [];
    segs.forEach(function (s) {
      var w = s.fold ? foldLaw(s.b - s.a) * fs : (s.b - s.a) * k;
      dom.push(s.a); rng.push(x);
      if (s.fold) folds.push({ a: s.a, b: s.b, x0: x, x1: x + w, q: s.q });
      x += w;
    });
    dom.push(segs[segs.length - 1].b); rng.push(x);
    return { x: d3.scaleLinear().domain(dom).range(rng), folds: folds, k: k, segs: segs };
  }

  /* Per-pixel bins of a set of episodes over a scale: at each column the
   * coverage of active steps by kind, the erroring and verifier share, and
   * the mean cumulative return. O(steps) per draw. */
  function binLane(eps, x, measure, x0, x1) {
    var nCol = Math.max(1, Math.ceil(x1 - x0));
    var think = new Float32Array(nCol), tool = new Float32Array(nCol), err = new Float32Array(nCol), ans = new Float32Array(nCol), ver = new Float32Array(nCol), rsum = new Float32Array(nCol);
    var n = eps.length;
    for (var e = 0; e < n; e++) {
      var ep = eps[e], ax = axisOf(ep, measure);
      for (var i = 0; i < ep.n; i++) {
        var a = x(ax.t0[i]), b = x(ax.t0[i] + ax.dur[i]);
        var rc = Math.floor(a - x0); if (rc < 0) rc = 0;
        if (rc < nCol) rsum[rc] += ep.rw[i];
        if (b <= x0 || a >= x1) continue;
        var ca = Math.max(0, Math.floor(a - x0)), cb = Math.min(nCol - 1, Math.ceil(b - x0) - 1);
        var isErr = ep.fl[i].indexOf("e") >= 0, isVer = ep.fl[i].indexOf("v") >= 0, k = ep.kind[i];
        for (var c = ca; c <= cb; c++) {
          var lo = Math.max(a, x0 + c), hi = Math.min(b, x0 + c + 1), f = hi - lo;
          if (f <= 0) continue;
          if (isErr) err[c] += f; else if (k === 2) ans[c] += f; else if (k === 1) tool[c] += f; else think[c] += f;
          if (isVer) ver[c] += f;
        }
      }
    }
    var cum = new Float32Array(nCol), acc = 0, maxA = 0, maxC = 0;
    for (var c2 = 0; c2 < nCol; c2++) {
      acc += rsum[c2]; cum[c2] = n ? acc / n : 0;
      var tot = think[c2] + tool[c2] + err[c2] + ans[c2];
      if (tot > maxA) maxA = tot;
      if (Math.abs(cum[c2]) > maxC) maxC = Math.abs(cum[c2]);
    }
    return { nCol: nCol, think: think, tool: tool, err: err, ans: ans, ver: ver, cum: cum, maxActive: maxA, maxCum: maxC, n: n };
  }
  //: a canvas hatch in the accent colour, the verifier's mark
  function hatch(cx, colour) {
    var c = document.createElement("canvas"); c.width = 6; c.height = 6;
    var g = c.getContext("2d");
    g.strokeStyle = colour; g.lineWidth = 1.2; g.globalAlpha = 0.75;
    g.beginPath(); g.moveTo(0, 6); g.lineTo(6, 0); g.moveTo(-1, 1); g.lineTo(1, -1); g.moveTo(5, 7); g.lineTo(7, 5); g.stroke();
    return cx.createPattern(c, "repeat");
  }
  function setupCanvas(canvas, W, H) {
    var dpr = Math.max(1, Math.min(3, global.devicePixelRatio || 1));
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px"; canvas.style.height = H + "px";
    var cx = canvas.getContext("2d");
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cx.clearRect(0, 0, W, H);
    return cx;
  }
  //: draws one lane's stacked density (think, tool, error, answer), the verifier hatch and the return area
  function drawDensity(cx, bins, x0, top, densH, rewH, maxActive, maxCum, col, pat) {
    if (!bins.nCol || maxActive <= 0) return;
    var base = top + densH, s = densH / maxActive;
    var area = d3.area().x(function (d, i) { return x0 + i; }).y0(function (d) { return d[0]; }).y1(function (d) { return d[1]; }).curve(d3.curveStepAfter).context(cx);
    var series = [["think", col.ink3, 0.32], ["tool", col.ink2, 0.72], ["err", col.bad, 0.9], ["ans", col.ink, 0.95]];
    var below = new Float32Array(bins.nCol);
    series.forEach(function (sd) {
      var arr = bins[sd[0]], pts = new Array(bins.nCol), any = false;
      for (var c = 0; c < bins.nCol; c++) { var h = arr[c] * s; pts[c] = [base - below[c], base - below[c] - h]; below[c] += h; if (h > 0) any = true; }
      if (!any) return;
      cx.beginPath(); area(pts); cx.fillStyle = sd[1]; cx.globalAlpha = sd[2]; cx.fill();
    });
    if (pat) {
      var vp = new Array(bins.nCol), anyV = false;
      for (var c2 = 0; c2 < bins.nCol; c2++) { var hv = bins.ver[c2] * s; vp[c2] = [base, base - hv]; if (hv > 0) anyV = true; }
      if (anyV) { cx.beginPath(); area(vp); cx.fillStyle = pat; cx.globalAlpha = 1; cx.fill(); }
    }
    cx.globalAlpha = 1;
    if (rewH > 0 && maxCum > 0) {
      var mid = base + 2 + rewH / 2, rs = (rewH / 2) / maxCum;
      var pos = new Array(bins.nCol), neg = new Array(bins.nCol);
      for (var c3 = 0; c3 < bins.nCol; c3++) { var v = bins.cum[c3]; pos[c3] = [mid, mid - Math.max(0, v) * rs]; neg[c3] = [mid, mid + Math.max(0, -v) * rs]; }
      cx.beginPath(); area(pos); cx.fillStyle = col.good; cx.globalAlpha = 0.35; cx.fill();
      cx.beginPath(); area(neg); cx.fillStyle = col.bad; cx.globalAlpha = 0.35; cx.fill();
      cx.globalAlpha = 0.5; cx.fillStyle = col.rule2; cx.fillRect(x0, mid, bins.nCol, 1);
      cx.globalAlpha = 1;
    }
  }
  function colours() {
    return { ink: cssVar("--ink"), ink2: cssVar("--ink-2"), ink3: cssVar("--ink-3"), good: cssVar("--good"), bad: cssVar("--bad"), accent: cssVar("--accent"), surface: cssVar("--surface"), surface2: cssVar("--surface-2"), rule2: cssVar("--rule-2"), warn: cssVar("--warn") };
  }
  function flagWords(fl) {
    var w = [];
    if (fl.indexOf("e") >= 0) w.push("error"); if (fl.indexOf("w") >= 0) w.push("wasted"); if (fl.indexOf("d") >= 0) w.push("decisive");
    if (fl.indexOf("f") >= 0) w.push("on the fault's path"); if (fl.indexOf("v") >= 0) w.push("in the verifier span");
    return w;
  }
  function kindWord(k) { return k === 2 ? "answer" : k === 1 ? "tool" : "think"; }

  // ------------------------------------------------------------ the block
  AgentDiff.block({
    id: "evo-timescape",
    title: "The timescape",
    question: "Every episode of every generation along constricted time: what the agent was doing at each moment, where the reward came, and where the quiet time was folded away — from the lineage down to one step.",
    group: "evolution",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); return m.ok ? 0.8 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      if (!m.ok) return ctx.empty(el, "No episode timelines in the evolution section, so time cannot be laid out.");
      var root = H("div", { class: "evt" });
      el.appendChild(root);
      var tip = tooltip(root);
      var st = loadState();
      //: the folds the reader dilated, per scope; and the keyboard's selection per level
      var unfolded = {}, sel = { lane: 0, row: 0, run: 0, step: 0 }, chosen = null;
      var scope = resolve(m, st);

      // ---------------------------------------------------------- header
      var bar = H("div", { class: "evt-bar" });
      var crumbs = H("nav", { class: "evt-crumbs", "aria-label": "zoom path" });
      bar.appendChild(crumbs);
      var seg = H("span", { class: "evt-seg", role: "group", "aria-label": "x measure" });
      var measureBtns = {};
      [["time", "x is wall-clock seconds since the episode began"], ["steps", "x is the step index: a generation that got shorter, not faster"]].forEach(function (p) {
        measureBtns[p[0]] = H("button", { class: "evt-btn", text: p[0], "data-measure": p[0], title: p[1], "aria-pressed": st.measure === p[0] ? "true" : "false",
          onclick: function () { if (st.measure === p[0]) return; st.measure = p[0]; st.view = null; st.compare = null; unfolded = {}; syncButtons(); fullView(); } });
        seg.appendChild(measureBtns[p[0]]);
      });
      bar.appendChild(seg);
      var constrictBtn = H("button", { class: "evt-btn", "data-act": "constrict", "aria-pressed": st.constrict ? "true" : "false", title: "fold quiet stretches into short dotted segments; off shows the clock as it was",
        onclick: function () { st.constrict = !st.constrict; syncButtons(); saveState(st); draw(); } });
      bar.appendChild(constrictBtn);
      root.appendChild(bar);
      var status = H("p", { class: "evt-status", role: "status", "aria-live": "polite" });
      root.appendChild(status);
      function syncButtons() { Object.keys(measureBtns).forEach(function (k) { measureBtns[k].setAttribute("aria-pressed", st.measure === k ? "true" : "false"); }); constrictBtn.setAttribute("aria-pressed", st.constrict ? "true" : "false"); }

      // ---------------------------------------------------------- stage DOM
      var miniHost = H("div", { class: "evt-mini" });
      var stage = H("div", { class: "evt-stage", role: "application", tabindex: "0" });
      root.appendChild(miniHost);
      root.appendChild(stage);
      var compareHost = H("div", { class: "evt-compare" });
      var detailHost = H("div", {});
      root.appendChild(compareHost);
      root.appendChild(detailHost);
      var legend = H("p", { class: "evt-legend" });
      root.appendChild(legend);
      var note = H("p", { class: "evt-note" });
      root.appendChild(note);

      var geo = null;      // the mounted stage: W, canvas, svg, zoom, scales
      var mini = null;     // the mounted minimap

      function width() { return L.layout.measure(root, 300, 1400); }

      // ---------------------------------------------------------- windows
      //: the level's full span in the current measure
      function spanT() { return Math.max(1e-6, spanOf(scope.eps, st.measure)); }
      function window_() {
        var T = spanT();
        if (!st.view) return [0, T];
        var a = clamp(st.view[0], 0, T), b = clamp(st.view[1], 0, T);
        if (b - a < T * 0.002) return [0, T];
        return [a, b];
      }
      function xRef() { return d3.scaleLinear().domain([0, spanT()]).range([geo.x0, geo.x1]); }
      function transformFor(win) {
        var ref = xRef(), k = clamp((geo.x1 - geo.x0) / Math.max(1e-6, ref(win[1]) - ref(win[0])), 0.45, 4096);
        return d3.zoomIdentity.translate(geo.x0 - k * ref(win[0]), 0).scale(k);
      }
      var applying = false;
      function applyView(win, animate) {
        if (!geo) return;
        st.view = win;
        applying = true;
        try {
          if (animate && dur() > 0) geo.svg.transition().duration(dur()).call(geo.zoom.transform, transformFor(win));
          else geo.svg.call(geo.zoom.transform, transformFor(win));
        } finally { applying = false; }
      }
      function fullView() { st.view = null; saveState(st); if (geo) applyView([0, spanT()], true); else draw(); }

      // ---------------------------------------------------------- levels
      function setLevel(level, path, keepView) {
        st.level = level;
        st.path = { gen: path.gen === undefined ? st.path.gen : path.gen, task: path.task === undefined ? st.path.task : path.task, run: path.run === undefined ? st.path.run : path.run };
        chosen = null; unfolded = {};
        scope = resolve(m, st);
        if (!keepView) st.view = null;
        st.compare = null;
        saveState(st);
        if (geo) {
          geo.content.attr("opacity", 0).transition().duration(dur()).attr("opacity", 1);
          d3.select(geo.canvas).style("opacity", 0).transition().duration(dur()).style("opacity", 1);
          applyView(window_(), false);
          geo.kBase = d3.zoomTransform(geo.svg.node()).k;
        } else draw();
      }
      function descend(target) {
        // the selected thing at this level becomes the path one level down
        if (scope.level === 0) { var g = target || m.gens[sel.lane]; if (g) setLevel(1, { gen: g.id, task: null, run: null }, true); }
        else if (scope.level === 1) { var ep = target || rowsEp(sel.row); if (ep) { setLevel(2, { task: ep.task, run: ep.run }, true); sel.run = Math.max(0, scope.eps.indexOf(ep)); } }
        else if (scope.level === 2) { var ep2 = target || scope.eps[sel.run]; if (ep2) setLevel(3, { run: ep2.run }, true); }
        else openStep(scope.ep, sel.step);
      }
      function ascend() {
        if (scope.level === 0) return;
        var was = scope;
        setLevel(scope.level - 1, {}, true);
        // the selection lands on where the reader came from
        if (scope.level === 0) sel.lane = Math.max(0, m.gens.indexOf(was.gen));
        else if (scope.level === 1 && was.grp) sel.row = Math.max(0, scope.eps.indexOf(was.ep || was.grp.eps[0]));
        else if (scope.level === 2 && was.ep) sel.run = Math.max(0, scope.eps.indexOf(was.ep));
      }
      function rowsEp(i) { return geo && geo.rows && geo.rows[i] ? geo.rows[i].ep : null; }

      // --------------------------------------------------- the step opener
      function coverage(ep) { return m.coverage[ep.task + "|" + ep.agent + "|" + ep.run] || null; }
      function openStep(ep, i) {
        if (!ep || !isNum(i) || i < 0 || i >= ep.n) return;
        chosen = { ep: ep, i: i };
        drawDetail();
        var cov = coverage(ep);
        if (cov && AgentDiff.charts && typeof AgentDiff.charts.selectStep === "function") {
          try {
            if (ctx.task !== cov.report.task.id && typeof ctx.selectTask === "function") ctx.selectTask(cov.report.task.id);
            AgentDiff.charts.selectStep(cov.report, cov.side, i);
          } catch (err) { /* the inspector is not ours to fix */ }
        }
      }
      function stepLines(ep, i, extra) {
        var ax = axisOf(ep, st.measure), cov = coverage(ep), input = null;
        if (cov) { var s = cov.report[cov.side] && cov.report[cov.side].steps && cov.report[cov.side].steps[i]; if (s && s.input !== null && s.input !== undefined) input = trunc(String(s.input), 60); }
        var fw = flagWords(ep.fl[i]);
        return [{ b: true, text: ep.gen.id + " · " + short(ep.task) + " · " + ep.run + " · step " + i + " · " + kindWord(ep.kind[i]) + " " + ep.nm[i] },
          input ? { text: input } : null,
          { text: "reward " + signed(ep.rw[i]) + " · return so far " + signed(ep.cum[i]) + " · " + secs(ep.dur[i]) + " at " + secs(ep.t0[i]) + (st.measure === "steps" ? " · x = step " + i : "") },
          fw.length ? { text: fw.join(", ") } : null,
          extra ? { text: extra } : null];
      }
      function drawDetail() {
        detailHost.innerHTML = "";
        if (!chosen) return;
        var ep = chosen.ep, i = chosen.i, cov = coverage(ep);
        var lines = stepLines(ep, i, null);
        var kids = [H("div", {}, [H("b", { text: lines[0].text })])];
        if (lines[1]) kids.push(H("span", { class: "in", text: lines[1].text }));
        kids.push(H("div", { text: lines[2].text }));
        if (lines[3]) kids.push(H("div", { text: lines[3].text }));
        var foot = H("div", { text: cov ? "a pair report covers this run · " : "no pair report covers this run: only the last step's pair has reports, so the input is not on record here · " });
        if (cov && AgentDiff.charts && typeof AgentDiff.charts.selectStep === "function") foot.appendChild(H("button", { text: "open in the inspector", "data-act": "inspect", onclick: function () { openStep(ep, i); } }));
        kids.push(foot);
        detailHost.appendChild(H("div", { class: "evt-detail", "data-gen": ep.gen.id, "data-task": ep.task, "data-run": ep.run, "data-step": String(i) }, kids));
      }

      // --------------------------------------------------------- minimap
      function mountMini() {
        miniHost.innerHTML = "";
        var W = width(), narrow = W < 560, laneH = narrow ? 4 : 6, x0 = narrow ? 30 : 46, x1 = W - 8;
        var n = m.gens.length, Hh = n * laneH + 14;
        miniHost.style.height = Hh + "px";
        var canvas = H("canvas", { "aria-hidden": "true" }); miniHost.appendChild(canvas);
        var svg = d3.select(miniHost).append("svg").attr("width", W).attr("height", Hh).attr("role", "img");
        mini = { W: W, x0: x0, x1: x1, laneH: laneH, H: Hh, canvas: canvas, svg: svg, top: 12 };
        drawMini();
      }
      function drawMini() {
        if (!mini) return;
        var col = colours(), cx = setupCanvas(mini.canvas, mini.W, mini.H);
        var TL = Math.max(1e-6, spanOf(m.eps, st.measure)), T = spanT();
        var xm = d3.scaleLinear().domain([0, TL]).range([mini.x0, mini.x1]);
        mini.xm = xm;
        var maxA = 0, lanes = m.gens.map(function (g) { var b = binLane(g.eps, xm, st.measure, mini.x0, mini.x1); maxA = Math.max(maxA, b.maxActive); return b; });
        m.gens.forEach(function (g, gi) {
          var top = mini.top + gi * mini.laneH, b = lanes[gi];
          for (var c = 0; c < b.nCol; c++) {
            var act = b.think[c] + b.tool[c] + b.err[c] + b.ans[c];
            if (act <= 0) continue;
            cx.globalAlpha = 0.15 + 0.85 * Math.sqrt(act / maxA);
            cx.fillStyle = b.err[c] > 0.5 ? col.bad : col.ink2;
            cx.fillRect(mini.x0 + c, top + 1, 1, mini.laneH - 1);
          }
        });
        cx.globalAlpha = 1;
        var svg = mini.svg; svg.selectAll("*").remove();
        svg.attr("aria-label", "minimap: the lineage's " + m.gens.length + " generations on " + (st.measure === "steps" ? "step index" : "wall-clock") + ", the brush is the window in view" + (scope.gen ? ", " + scope.gen.id + " highlighted" : ""));
        svg.append("text").attr("class", "tick").attr("x", mini.x0).attr("y", 9).text("0");
        svg.append("text").attr("class", "tick").attr("x", mini.x1).attr("y", 9).attr("text-anchor", "end").text(tickText(TL, st.measure));
        if (scope.gen) svg.append("rect").attr("class", "evt-sel").attr("x", mini.x0 - 1).attr("y", mini.top + scope.gen.index * mini.laneH).attr("width", mini.x1 - mini.x0 + 2).attr("height", mini.laneH);
        var brush = d3.brushX().extent([[mini.x0, mini.top], [xm(Math.min(TL, T)), mini.top + m.gens.length * mini.laneH]])
          .on("brush end", function (evt) {
            if (!evt.sourceEvent || applying) return;
            if (!evt.selection) { if (evt.type === "end") applyView([0, spanT()], false); return; }
            var a = xm.invert(evt.selection[0]), b = xm.invert(evt.selection[1]);
            if (b - a < T * 0.002) return;
            applyView([clamp(a, 0, T), clamp(b, 0, T)], false);
          });
        mini.brush = brush;
        mini.g = svg.append("g").attr("class", "brush evt-minibrush").call(brush);
        mini.g.selectAll(".overlay").attr("cursor", "crosshair").append("title").text("drag to choose the window in view — the same as zooming; no brush means the whole span is in view");
        syncMini();
      }
      function syncMini() {
        if (!mini || !mini.g) return;
        // the whole span in view is an empty brush, so a drag can always start a new window
        var w = window_(), full = !st.view;
        applying = true;
        try { mini.g.call(mini.brush.move, full ? null : [mini.xm(w[0]), mini.xm(w[1])]); } finally { applying = false; }
      }

      // ----------------------------------------------------------- stage
      function mountStage() {
        stage.innerHTML = "";
        var W = width(), narrow = W < 560;
        var canvas = H("canvas", { "aria-hidden": "true" }); stage.appendChild(canvas);
        var svg = d3.select(stage).append("svg").attr("width", W).attr("role", "img");
        var x0 = narrow ? 34 : 60, x1 = W - (narrow ? 8 : 14);
        geo = { W: W, narrow: narrow, x0: x0, x1: x1, canvas: canvas, svg: svg, H: 0, rows: [] };
        // the zoom: wheel and pinch only, so a drag is left to the brushes
        var zoom = d3.zoom().scaleExtent([0.45, 4096])
          .filter(function (evt) { return evt.type === "wheel" || /^touch/.test(evt.type); })
          .on("zoom", onZoom);
        geo.zoom = zoom;
        geo.content = svg.append("g").attr("class", "evt-content");
        svg.call(zoom).on("dblclick.zoom", null);
        svg.on("pointermove", onMove).on("pointerleave", tip.hide).on("click", onClick);
        applyView(window_(), false);
      }
      var pendingLevel = null;
      function onZoom(evt) {
        var t = evt.transform;
        if (!geo) return;
        var ref = xRef(), T = spanT();
        var a = clamp(ref.invert(t.invertX(geo.x0)), 0, T), b = clamp(ref.invert(t.invertX(geo.x1)), 0, T);
        st.view = b - a >= T - 1e-9 ? null : [a, b];
        // the wheel past a threshold is a semantic zoom: into the lane under the pointer, or back out
        if (!applying && evt.sourceEvent && !pendingLevel) {
          var kBase = geo.kBase || 1;
          if (t.k >= kBase * DESCEND_K && scope.level < 3) {
            var p = d3.pointer(evt.sourceEvent, geo.svg.node()), target = thingAt(p[1]);
            if (target) { pendingLevel = true; global.requestAnimationFrame(function () { pendingLevel = null; descend(target); }); return; }
          } else if (t.k <= Math.min(1, kBase) * ASCEND_K && scope.level > 0) {
            pendingLevel = true; global.requestAnimationFrame(function () { pendingLevel = null; ascend(); }); return;
          }
        }
        draw();
        if (evt.sourceEvent) saveState(st);
      }
      //: the lane, row or run under a y — the thing the wheel or a click descends into
      function thingAt(y) {
        if (!geo) return null;
        if (scope.level === 0) { var li = Math.floor((y - geo.top) / geo.laneH); return li >= 0 && li < m.gens.length ? m.gens[li] : null; }
        if (scope.level === 1 || scope.level === 2) {
          var rows = geo.rows; if (!rows.length) return null;
          var i = d3.bisector(function (r) { return r.top; }).right(rows, y) - 1;
          return i >= 0 && y <= rows[i].top + rows[i].h ? rows[i].ep : null;
        }
        return null;
      }
      function stepAt(ep, t) {
        var ax = axisOf(ep, st.measure);
        var i = d3.bisector(function (v) { return v; }).right(ax.t0, t) - 1;
        return i >= 0 && t <= ax.t0[i] + ax.dur[i] + 1e-9 ? i : -1;
      }

      // --------------------------------------------------------- pointer
      function onMove(evt) {
        if (!geo || !geo.sc) return;
        var p = d3.pointer(evt, geo.svg.node()), px = p[0], py = p[1];
        if (px < geo.x0 || px > geo.x1) { tip.hide(); return; }
        var t = geo.sc.x.invert(px);
        if (scope.level === 0) {
          var g = thingAt(py); if (!g) { tip.hide(); return; }
          var b = geo.bins[g.index], c = clamp(Math.floor(px - geo.x0), 0, b.nCol - 1);
          var act = b.think[c] + b.tool[c] + b.err[c] + b.ans[c];
          tip.show(evt, [{ b: true, text: g.id + " · " + unit(t, st.measure) + " · " + g.eps.length + " episodes" },
            { text: act.toFixed(1) + " active: " + b.think[c].toFixed(1) + " thinking · " + b.tool[c].toFixed(1) + " in a tool · " + b.err[c].toFixed(1) + " erroring · " + b.ans[c].toFixed(1) + " answering" + (b.ver[c] > 0 ? " · " + b.ver[c].toFixed(1) + " in the verifier" : "") },
            { text: "mean return so far " + signed(b.cum[c]) + " · click to open the generation" }]);
          return;
        }
        if (scope.level === 1) {
          var ep = thingAt(py); if (!ep) { tip.hide(); return; }
          var i = stepAt(ep, t);
          if (i < 0) { tip.show(evt, [{ b: true, text: ep.gen.id + " · " + short(ep.task) + " · " + ep.run }, { text: ep.n + " steps · " + secs(ep.seconds) + " · return " + signed(ep.ret) + " · " + (ep.success ? "solved" : "failed") + (t > ep.T ? " · ended before " + unit(t, st.measure) : "") }, { text: "click to open the task" }]); return; }
          tip.show(evt, stepLines(ep, i, "click to open the task"));
          return;
        }
        tip.hide();
      }
      function onClick(evt) {
        if (!geo) return;
        var p = d3.pointer(evt, geo.svg.node());
        if (scope.level >= 2) return;   // levels 2 and 3 click their own marks
        var target = thingAt(p[1]);
        if (target) { tip.hide(); if (scope.level === 1) sel.row = geo.rows.findIndex(function (r) { return r.ep === target; }); descend(target); }
      }

      // -------------------------------------------------------------- draw
      function draw() {
        if (!geo) return;
        var W = geo.W, narrow = geo.narrow, x0 = geo.x0, x1 = geo.x1, col = colours();
        var win = window_(), T = spanT();
        var qkey = scope.key + "|" + st.measure;
        if (!geo.quiet || geo.quiet.key !== qkey) geo.quiet = { key: qkey, list: quietOf(scope.eps, st.measure) };
        var sc = constrict(win[0], win[1], geo.quiet.list, unfolded, st.constrict, x0, x1, st.measure);
        geo.sc = sc;
        var content = geo.content; content.selectAll("*").remove();
        var svg = geo.svg;
        // ---- the clock over the drawing: ticks, ⋯ at the folds, and the compare brush under it
        var axisY = 14, brushY = 20, brushH = 12, top = brushY + brushH + 8;
        geo.top = top;
        var level = scope.level, Hh;
        // the folds' faint bands sit behind everything, so the eye sees where time was folded and nothing is hidden by them
        var bands = content.append("g").attr("class", "evt-bands").attr("pointer-events", "none");
        var body = content.append("g").attr("class", "evt-body");
        // ---- levels
        var bodyH;
        if (level === 0) bodyH = drawL0(body, sc, col, top);
        else if (level === 1) bodyH = drawL1(body, sc, col, top);
        else if (level === 2) bodyH = drawL2(body, sc, col, top);
        else bodyH = drawL3(body, sc, col, top);
        Hh = top + bodyH + 6;
        geo.H = Hh;
        svg.attr("height", Hh); stage.style.height = Hh + "px";
        // the axis, after the body so it sits over any canvas density
        var axis = content.append("g").attr("class", "evt-axis");
        var step = niceStep(win[1] - win[0], st.measure === "steps"), labEnd = -Infinity;
        function placeTick(tx, text, cls) { var w = text.length * CH; if (tx - w / 2 < labEnd + 6 || tx + w / 2 > x1 + 4) return; axis.append("text").attr("class", "tick" + (cls ? " " + cls : "")).attr("x", tx).attr("y", axisY - 4).attr("text-anchor", "middle").text(text); labEnd = tx + w / 2; }
        sc.segs.forEach(function (s) {
          var sx0 = sc.x(s.a), sx1 = sc.x(s.b);
          if (s.fold) {
            var fg = axis.append("g").attr("class", "evt-fold").attr("data-fold", s.q.id).attr("data-units", (s.b - s.a).toFixed(2));
            fg.append("line").attr("x1", sx0).attr("x2", sx1).attr("y1", axisY).attr("y2", axisY).attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-dasharray", "1.5 2.5").attr("stroke-linecap", "round");
            // a faint band down the drawing, so the eye sees where time was folded
            bands.append("rect").attr("class", "evt-band").attr("data-fold", s.q.id).attr("x", sx0).attr("y", axisY).attr("width", Math.max(1, sx1 - sx0)).attr("height", Hh - axisY).attr("fill", "var(--surface-2)").attr("fill-opacity", 0.7);
            fg.append("rect").attr("x", sx0 - 2).attr("y", axisY - 8).attr("width", Math.max(5, sx1 - sx0 + 4)).attr("height", 16).attr("fill", "transparent");
            var nSteps = level === 3 ? stepsIn(scope.eps, st.measure, s.a, s.b) : null;
            fg.append("title").text("quiet " + unit(s.b - s.a, st.measure) + (nSteps !== null ? " · ×" + nSteps + " steps" : "") + " folded — click to open");
            placeTick((sx0 + sx1) / 2, "⋯", "evt-gap");
            fg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, [{ b: true, text: "⋯ quiet " + unit(s.b - s.a, st.measure) + " folded" }, { text: (nSteps !== null ? "×" + nSteps + " steps · " : "") + tickText(s.a, st.measure) + "–" + tickText(s.b, st.measure) + " · nothing eventful here: no error, no answer, no reward beyond the shaping cost" }, { text: "click to dilate" }]); })
              .on("click", function (evt) { evt.stopPropagation(); unfolded[s.q.id] = true; draw(); });
            return;
          }
          for (var tt = Math.ceil(s.a / step) * step; tt <= s.b + 1e-9 && step > 0; tt += step) {
            var tx = sc.x(tt);
            if (tx < x0 - 0.5 || tx > x1 + 0.5) continue;
            axis.append("line").attr("x1", tx).attr("x2", tx).attr("y1", axisY - 1).attr("y2", axisY + 3).attr("stroke", "var(--ink-3)").attr("stroke-width", 1);
            placeTick(tx, tickText(tt, st.measure), null);
          }
        });
        axis.append("line").attr("x1", x0).attr("x2", x1).attr("y1", axisY).attr("y2", axisY).attr("stroke", "var(--rule-2)").attr("stroke-width", 1).lower();
        // ---- the compare brush: a band under the clock
        var brush = d3.brushX().extent([[x0, brushY], [x1, brushY + brushH]]).on("end", function (evt) {
          if (!evt.sourceEvent) return;
          st.compare = evt.selection ? [sc.x.invert(evt.selection[0]), sc.x.invert(evt.selection[1])] : null;
          saveState(st); drawCompare();
        });
        var bg = content.append("g").attr("class", "brush evt-compare-brush").call(brush);
        bg.selectAll(".overlay").attr("cursor", "crosshair").append("title").text("drag here to compare a window across the generations");
        content.append("rect").attr("x", x0).attr("y", brushY).attr("width", x1 - x0).attr("height", brushH).attr("fill", "var(--surface-2)").attr("fill-opacity", 0.5).attr("pointer-events", "none").lower();
        if (st.compare && st.compare[1] > win[0] && st.compare[0] < win[1]) {
          bg.call(brush.move, [clamp(sc.x(Math.max(st.compare[0], win[0])), x0, x1), clamp(sc.x(Math.min(st.compare[1], win[1])), x0, x1)]);
        }
        // ---- words
        var nFolds = sc.folds.length, folded = sc.folds.reduce(function (t, f) { return t + (f.b - f.a); }, 0);
        constrictBtn.textContent = st.constrict ? "constricted · " + (nFolds ? nFolds + (nFolds === 1 ? " fold hides " : " folds hide ") + unit(folded, st.measure) : "no fold in view") : "not constricted · " + geo.quiet.list.length + " quiet " + (geo.quiet.list.length === 1 ? "stretch" : "stretches") + " on the clock";
        geo.nFolds = nFolds; geo.folded = folded;
        drawCrumbs();
        drawStatus();
        drawCompare();
        drawSelection();
        syncMini();
        legend.textContent = legendFor(level);
      }
      function legendFor(level) {
        var q = " · ⋯ = quiet " + (st.measure === "steps" ? "steps" : "time") + " folded (nothing eventful: no error, no answer, no reward beyond the shaping cost " + signed(m.modal) + "), 6 + 6·log2(1 + " + (st.measure === "steps" ? "steps" : "seconds") + ") px, click to dilate · wheel or pinch to zoom, drag the minimap for the window, drag under the clock to compare · ↑↓ move, ←→ " + (level >= 2 ? "step" : "pan") + ", Enter descends, Esc ascends";
        if (level === 0) return "one lane per generation · height = episodes active at that moment: thinking light, a tool darker, an error red, the answer solid, hatched = in the verifier · the area beneath is the mean return so far, green above the line, red below · the mark at a lane boundary is the step's verdict (▲ improved ▼ regressed ─ flat ⚠ gamed ◇ overfit ✕ forgot ⇄ traded) · click a lane to open it" + q;
        if (level === 1) return "one ribbon per episode, grouped by task · a cell is a step, green when it paid, red when it cost, its depth the size of the reward; a grey cell paid nothing · dashed = failed · a ribbon ends where the episode ended · click a ribbon to open its task" + q;
        if (level === 2) return "the runs of one task aligned on the clock · a light cell thinks, a dark one calls a tool, ◆ a step that paid, ✕ an error (ringed), ⌊ ⌋ the verifier span, ■ the answer in the outcome's colour · hover a step for it, click to open it" + q;
        return "one episode as a thread: every step its " + (st.measure === "steps" ? "index" : "seconds") + " on the clock, ×N · S a quiet stretch folded (click to open), the bar under each step its reward — square-rooted so the small ones stay visible · ✕ error ◆ paid ■ answer ◎ decisive ▏fault · click a step to open it" + q;
      }
      function drawCrumbs() {
        crumbs.innerHTML = "";
        var items = [{ label: "lineage", level: 0 }];
        if (scope.gen) items.push({ label: scope.gen.id, level: 1 });
        if (scope.grp) items.push({ label: short(scope.grp.task), level: 2, title: scope.grp.task });
        if (scope.ep) items.push({ label: scope.ep.run || "run", level: 3 });
        items.forEach(function (it, i) {
          if (i) crumbs.appendChild(H("span", { class: "sep", text: "›", "aria-hidden": "true" }));
          var cur = it.level === scope.level;
          crumbs.appendChild(H("button", { text: it.label, title: it.title || (cur ? "you are here" : "back to the " + LEVEL_NAME[it.level]), "data-level": String(it.level), "aria-current": cur ? "true" : "false",
            onclick: function () { if (!cur) setLevel(it.level, {}, false); } }));
        });
      }
      function selectionWords() {
        if (scope.level === 0) { var g = m.gens[sel.lane]; return g ? "lane " + g.id : ""; }
        if (scope.level === 1) { var ep = rowsEp(sel.row); return ep ? "episode " + short(ep.task) + " " + ep.run : ""; }
        if (scope.level === 2) { var e2 = scope.eps[sel.run]; return e2 ? "run " + e2.run + (sel.step >= 0 ? ", step " + sel.step : "") : ""; }
        return "step " + sel.step + (scope.ep ? " of " + scope.ep.n : "");
      }
      function drawStatus() {
        var nEp = scope.eps.length, words;
        if (scope.level === 0) words = "the lineage: " + m.gens.length + " generations, " + nEp + " episodes";
        else if (scope.level === 1) words = "generation " + scope.gen.id + ": " + nEp + " episodes over " + scope.gen.tasks.length + " tasks";
        else if (scope.level === 2) words = scope.gen.id + " on " + short(scope.grp.task) + ": " + nEp + " runs";
        else words = scope.gen.id + " · " + short(scope.ep.task) + " · " + scope.ep.run + ": " + scope.ep.n + " steps, return " + signed(scope.ep.ret) + ", " + (scope.ep.success ? "solved" : "failed");
        var text = "Level " + scope.level + " of 3 · " + words + " on " + (st.constrict ? "constricted " : "") + (st.measure === "steps" ? "step index" : "wall-clock") + (st.constrict ? "; " + (geo.nFolds ? geo.nFolds + (geo.nFolds === 1 ? " fold hides " : " folds hide ") + unit(geo.folded, st.measure) : "no fold in view") : "") + (st.view ? "; window " + tickText(st.view[0], st.measure) + "–" + tickText(st.view[1], st.measure) : "") + (m.skipped ? "; " + m.skipped + " episodes past the cap of " + DRAW_CAP + " carry no timeline and are not drawn" : "") + ". Selected: " + (selectionWords() || "nothing") + ".";
        status.textContent = text;
        stage.setAttribute("aria-label", "timescape, " + text + " Arrow keys move the selection, Enter descends, Escape ascends.");
        geo.svg.attr("aria-label", "the " + LEVEL_NAME[scope.level] + " on " + (st.constrict ? "constricted " : "") + (st.measure === "steps" ? "step index" : "wall-clock") + ": " + words + (st.constrict ? ", " + (geo.nFolds ? geo.nFolds + (geo.nFolds === 1 ? " quiet stretch folded" : " quiet stretches folded") : "no fold in view") : ""));
      }
      function drawSelection() {
        if (!geo) return;
        geo.content.selectAll(".evt-sel").remove();
        var r = null;
        if (scope.level === 0) { sel.lane = clamp(sel.lane, 0, m.gens.length - 1); r = { x: geo.x0 - 2, y: geo.top + sel.lane * geo.laneH, w: geo.x1 - geo.x0 + 4, h: geo.laneH - 2 }; }
        else if (scope.level === 1 && geo.rows.length) { sel.row = clamp(sel.row, 0, geo.rows.length - 1); var row = geo.rows[sel.row]; r = { x: geo.x0 - 2, y: row.top - 1, w: geo.x1 - geo.x0 + 4, h: row.h + 2 }; }
        else if (scope.level === 2 && geo.rows.length) {
          sel.run = clamp(sel.run, 0, geo.rows.length - 1); var rr = geo.rows[sel.run]; sel.step = clamp(sel.step, 0, rr.ep.n - 1);
          var ax = axisOf(rr.ep, st.measure), sx = geo.sc.x(ax.t0[sel.step] + ax.dur[sel.step] / 2);
          r = { x: clamp(sx, geo.x0, geo.x1) - 7, y: rr.top + rr.h / 2 - 9, w: 14, h: 18 };
        } else if (scope.level === 3 && scope.ep) {
          sel.step = clamp(sel.step, 0, scope.ep.n - 1);
          var ax3 = axisOf(scope.ep, st.measure), sx3 = geo.sc.x(ax3.t0[sel.step] + ax3.dur[sel.step] / 2);
          r = { x: clamp(sx3, geo.x0, geo.x1) - 7, y: geo.threadY - 10, w: 14, h: 20 };
        }
        if (r) geo.content.append("rect").attr("class", "evt-sel").attr("rx", 3).attr("x", r.x).attr("y", r.y).attr("width", r.w).attr("height", r.h);
      }

      // ---------------------------------------------------- level 0: lanes
      function drawL0(body, sc, col, top) {
        var narrow = geo.narrow, x0 = geo.x0, x1 = geo.x1;
        var densH = narrow ? 22 : 28, rewH = narrow ? 8 : 12, laneH = densH + rewH + (narrow ? 8 : 10);
        geo.laneH = laneH;
        var Hh = m.gens.length * laneH;
        var cx = setupCanvas(geo.canvas, geo.W, top + Hh + 6), pat = hatch(cx, col.accent);
        var bins = m.gens.map(function (g) { return binLane(g.eps, sc.x, st.measure, x0, x1); });
        geo.bins = bins;
        var maxA = 0, maxC = 0;
        bins.forEach(function (b) { maxA = Math.max(maxA, b.maxActive); maxC = Math.max(maxC, b.maxCum); });
        cx.save(); cx.beginPath(); cx.rect(x0, top, x1 - x0, Hh); cx.clip();
        m.gens.forEach(function (g, gi) { drawDensity(cx, bins[gi], x0, top + gi * laneH + 2, densH, rewH, maxA, maxC, col, pat); });
        cx.restore();
        m.gens.forEach(function (g, gi) {
          var y = top + gi * laneH;
          var lg = body.append("g").attr("class", "evt-lane").attr("data-gen", g.id).attr("data-episodes", g.eps.length);
          lg.append("text").attr("class", "lab mono").attr("x", 4).attr("y", y + 12).attr("font-weight", 600).attr("fill", "var(--ink)").text(g.id);
          if (!narrow) lg.append("text").attr("class", "lab dim").attr("x", 4).attr("y", y + 24).text(fit(g.eps.length + " ep", x0 - 10));
          if (g.skipped) lg.append("title").text(g.skipped + " episodes of " + g.id + " are past the timeline cap and not drawn");
          lg.append("rect").attr("class", "evt-lane-hit").attr("x", x0).attr("y", y).attr("width", x1 - x0).attr("height", laneH).attr("fill", "transparent");
          // the lane's baseline
          lg.append("line").attr("x1", x0).attr("x2", x1).attr("y1", y + 2 + densH).attr("y2", y + 2 + densH).attr("stroke", "var(--rule)").attr("stroke-width", 1);
          // the verdict of the step that made this generation, at the boundary above it
          if (gi > 0) {
            var stp = null; m.steps.forEach(function (s) { if (s.to === g.id) stp = s; });
            if (stp) {
              var v = VERDICT[stp.verdict] || { g: "•", c: "var(--ink-3)" };
              var vg = lg.append("g").attr("class", "evt-verdict").attr("data-verdict", stp.verdict).attr("data-from", stp.from).attr("data-to", stp.to);
              vg.append("text").attr("x", x0 - 8).attr("y", y + 4).attr("text-anchor", "end").attr("fill", v.c).text(v.g);
              vg.append("title").text(stp.from + " → " + stp.to + ": " + stp.verdict + (stp.summary ? " · " + stp.summary : "") + (stp.reading ? " — " + stp.reading : ""));
              vg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, [{ b: true, text: stp.from + " → " + stp.to + " · " + stp.verdict }, stp.summary ? { text: stp.summary } : null, stp.reading ? { text: stp.reading } : null]); }).on("pointerleave", tip.hide);
            }
          }
        });
        // the lane scale, said once: the tallest column is this many episodes
        body.append("text").attr("class", "tick").attr("x", x1).attr("y", top + Hh + 4).attr("text-anchor", "end").text("tallest = " + maxA.toFixed(0) + " active");
        return Hh + 8;
      }

      // -------------------------------------------------- level 1: ribbons
      function drawL1(body, sc, col, top) {
        var narrow = geo.narrow, x0 = geo.x0, x1 = geo.x1, g = scope.gen;
        var n = g.eps.length, banded = n > RIBBON_CAP;
        var RIB = banded ? 0 : clamp(Math.floor(520 / Math.max(1, n)), 2, 10), GAP = RIB > 3 ? 1 : 0, HEAD = 15, GGAP = 6;
        var rows = [], y = top;
        geo.rows = rows;
        var groups = g.groups.map(function (grp) { var o = { grp: grp, top: y, head: y + HEAD }; y += HEAD; if (banded) { o.bandH = narrow ? 22 : 30; y += o.bandH + 4; } else { grp.eps.forEach(function (ep) { rows.push({ ep: ep, top: y, h: RIB }); y += RIB + GAP; }); } y += GGAP; return o; });
        var Hh = y - top;
        var cx = setupCanvas(geo.canvas, geo.W, top + Hh + 6), pat = hatch(cx, col.accent);
        cx.save(); cx.beginPath(); cx.rect(x0, top, x1 - x0, Hh); cx.clip();
        if (banded) {
          var bins = groups.map(function (o) { return binLane(o.grp.eps, sc.x, st.measure, x0, x1); }), maxA = 0, maxC = 0;
          bins.forEach(function (b) { maxA = Math.max(maxA, b.maxActive); maxC = Math.max(maxC, b.maxCum); });
          groups.forEach(function (o, i) { drawDensity(cx, bins[i], x0, o.head, o.bandH - 8, 6, maxA, maxC, col, pat); });
        } else {
          rows.forEach(function (row) {
            var ep = row.ep, ax = axisOf(ep, st.measure), a0 = clamp(sc.x(0), x0, x1), a1 = clamp(sc.x(ax.T), x0, x1);
            cx.globalAlpha = 1; cx.fillStyle = col.surface2; cx.fillRect(a0, row.top, Math.max(0, a1 - a0), RIB);
            for (var i = 0; i < ep.n; i++) {
              var a = sc.x(ax.t0[i]), b = sc.x(ax.t0[i] + ax.dur[i]);
              if (b <= x0 || a >= x1) continue;
              a = Math.max(a, x0); b = Math.min(b, x1);
              var w = Math.max(0.6, b - a - (b - a > 3 ? 0.6 : 0)), r = ep.rw[i], isErr = ep.fl[i].indexOf("e") >= 0;
              if (isErr) { cx.fillStyle = col.bad; cx.globalAlpha = 0.95; }
              else if (r > 0) { cx.fillStyle = col.good; cx.globalAlpha = 0.3 + 0.7 * Math.sqrt(r / (m.maxAbs || 1)); }
              else if (r < 0 && r !== m.modal) { cx.fillStyle = col.bad; cx.globalAlpha = 0.3 + 0.7 * Math.sqrt(-r / (m.maxAbs || 1)); }
              else if (ep.kind[i] === 2) { cx.fillStyle = ep.success ? col.good : col.bad; cx.globalAlpha = 1; }
              else if (ep.kind[i] === 1) { cx.fillStyle = col.ink2; cx.globalAlpha = 0.45; }
              else { cx.fillStyle = col.ink3; cx.globalAlpha = 0.22; }
              cx.fillRect(a, row.top, w, RIB);
            }
            cx.globalAlpha = 1;
            if (!ep.success && RIB >= 3) { cx.strokeStyle = col.bad; cx.lineWidth = 1; cx.setLineDash([3, 2]); cx.beginPath(); cx.moveTo(a0, row.top + RIB - 0.5); cx.lineTo(a1, row.top + RIB - 0.5); cx.stroke(); cx.setLineDash([]); }
            if (a1 < x1) { cx.fillStyle = col.ink; cx.fillRect(a1, row.top - 1, 1, RIB + 2); }
          });
        }
        cx.restore();
        groups.forEach(function (o) {
          var solved = o.grp.eps.filter(function (e) { return e.success; }).length;
          var tg = body.append("g").attr("class", "evt-group").attr("data-task", o.grp.task).attr("data-episodes", o.grp.eps.length);
          var t = tg.append("text").attr("class", "lab").attr("x", 4).attr("y", o.top + 11);
          t.append("tspan").attr("font-weight", 600).attr("fill", "var(--ink)").text(fit(short(o.grp.task), narrow ? x1 - 4 : 220));
          if (!narrow) t.append("tspan").attr("class", "dim").attr("fill", "var(--ink-3)").text(" · " + o.grp.eps.length + " runs · " + solved + " solved · mean " + signed(o.grp.eps.reduce(function (s, e) { return s + e.ret; }, 0) / o.grp.eps.length));
          t.append("title").text(o.grp.task + " · " + o.grp.eps.length + " runs · " + solved + " solved");
        });
        if (banded) body.append("text").attr("class", "tick").attr("x", x1).attr("y", top + Hh + 4).attr("text-anchor", "end").text(n + " episodes: past " + RIBBON_CAP + " they draw as per-task density, not ribbons");
        return Hh + (banded ? 8 : 0);
      }

      // ---------------------------------------------- level 2: aligned runs
      function drawL2(body, sc, col, top) {
        var narrow = geo.narrow, x0 = geo.x0, x1 = geo.x1, grp = scope.grp;
        var ROWH = narrow ? 28 : 32, rows = [];
        geo.rows = rows;
        setupCanvas(geo.canvas, geo.W, 1);
        var Hh = grp.eps.length * ROWH;
        var defs = body.append("defs");
        var pat = defs.append("pattern").attr("id", "evt-hatch").attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
        pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "var(--accent)").attr("stroke-width", 1.2).attr("stroke-opacity", 0.7);
        body.append("clipPath").attr("id", "evt-clip").append("rect").attr("x", x0).attr("y", top).attr("width", x1 - x0).attr("height", Hh);
        grp.eps.forEach(function (ep, ri) {
          var y = top + ri * ROWH, mid = y + ROWH / 2, ax = axisOf(ep, st.measure);
          rows.push({ ep: ep, top: y, h: ROWH });
          var rg = body.append("g").attr("class", "evt-run").attr("data-run", ep.run).attr("data-gen", ep.gen.id).attr("data-task", ep.task);
          var lab = rg.append("text").attr("class", "lab mono").attr("x", 4).attr("y", mid + 4);
          lab.append("tspan").attr("font-weight", 600).attr("fill", "var(--ink)").text(fit(ep.run || "run", narrow ? 26 : 40));
          lab.append("tspan").attr("fill", ep.success ? "var(--good)" : "var(--bad)").text(ep.success ? " ✓" : " ✗");
          lab.append("title").text(ep.run + " · " + ep.n + " steps · " + secs(ep.seconds) + " · return " + signed(ep.ret) + " · " + (ep.success ? "solved" : "failed") + " · click the row's label to open the episode");
          lab.style("cursor", "pointer").on("click", function () { sel.run = ri; descend(ep); });
          var g = rg.append("g").attr("clip-path", "url(#evt-clip)");
          var e0 = clamp(sc.x(0), x0, x1), e1 = clamp(sc.x(ax.T), x0, x1);
          g.append("line").attr("x1", e0).attr("x2", e1).attr("y1", mid).attr("y2", mid).attr("stroke", "var(--rule-2)").attr("stroke-width", 1);
          if (!ep.success) g.select("line").attr("stroke-dasharray", "3 2");
          // the verifier span: one bracket per maximal run of v steps
          var vi = 0;
          while (vi < ep.n) {
            if (ep.fl[vi].indexOf("v") < 0) { vi++; continue; }
            var vj = vi; while (vj < ep.n && ep.fl[vj].indexOf("v") >= 0) vj++;
            var bx0 = sc.x(ax.t0[vi]), bx1 = sc.x(ax.t0[vj - 1] + ax.dur[vj - 1]);
            var br = g.append("g").attr("class", "evt-verifier").attr("data-from", vi).attr("data-to", vj - 1);
            br.append("path").attr("d", "M" + bx0 + "," + (mid + 6) + "v4H" + bx1 + "v-4").attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-width", 1.2);
            br.append("rect").attr("x", bx0).attr("y", mid - 8).attr("width", Math.max(1, bx1 - bx0)).attr("height", 16).attr("fill", "url(#evt-hatch)").attr("pointer-events", "none");
            br.append("title").text("the verifier span: steps " + vi + "–" + (vj - 1));
            vi = vj;
          }
          var inFold2 = function (i) { var t = ax.t0[i] + ax.dur[i] / 2; return sc.folds.some(function (f) { return t >= f.a && t < f.b; }); };
          for (var i = 0; i < ep.n; i++) {
            var a = sc.x(ax.t0[i]), b = sc.x(ax.t0[i] + ax.dur[i]);
            if (b < x0 || a > x1) continue;
            var folded = inFold2(i), w = folded ? Math.max(0.5, b - a) : Math.max(2, b - a - 0.5), k = ep.kind[i], r = ep.rw[i], isErr = ep.fl[i].indexOf("e") >= 0;
            var sg = g.append("g").attr("class", "evt-step").attr("data-step", i).attr("data-kind", kindWord(k)).attr("data-flags", ep.fl[i]).attr("data-folded", folded ? "true" : "false");
            if (folded) sg.append("rect").attr("x", a).attr("y", mid - (k === 2 ? 4.5 : k === 1 ? 5 : 3)).attr("width", w).attr("height", k === 2 ? 9 : k === 1 ? 10 : 6).attr("fill", isErr ? "var(--bad)" : k === 2 ? (ep.success ? "var(--good)" : "var(--bad)") : k === 1 ? "var(--ink-2)" : "var(--ink-3)").attr("fill-opacity", k === 0 ? 0.35 : 0.75);
            else if (k === 2) sg.append("rect").attr("x", a + (w - 9) / 2).attr("y", mid - 4.5).attr("width", 9).attr("height", 9).attr("fill", ep.success ? "var(--good)" : "var(--bad)");
            else if (k === 1) sg.append("rect").attr("x", a).attr("y", mid - 5).attr("width", w).attr("height", 10).attr("fill", isErr ? "var(--bad)" : "var(--ink-2)").attr("fill-opacity", isErr ? 0.9 : 0.75);
            else sg.append("rect").attr("x", a).attr("y", mid - 3).attr("width", w).attr("height", 6).attr("fill", "var(--ink-3)").attr("fill-opacity", 0.35);
            if (isErr && !folded) sg.append("circle").attr("cx", a + w / 2).attr("cy", mid).attr("r", 6.5).attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-width", 1.5);
            if (r > 0 && k !== 2 && !folded) sg.append("text").attr("class", "g").attr("x", a + w / 2).attr("y", mid - 7).attr("text-anchor", "middle").attr("fill", "var(--good)").text("◆");
            if (ep.fl[i].indexOf("d") >= 0 && !folded) sg.append("circle").attr("cx", a + w / 2).attr("cy", mid).attr("r", 5).attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-width", 2);
            // the hit target is the step's own interval, so neighbours never cover each other
            sg.append("rect").attr("x", a).attr("y", mid - 10).attr("width", Math.max(1, b - a)).attr("height", 20).attr("fill", "transparent");
            sg.append("title").text("step " + i + " · " + kindWord(k) + " " + ep.nm[i] + " · reward " + signed(r) + " — click to open");
            (function (idx) {
              sg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, stepLines(ep, idx, "click to open the step")); })
                .on("pointerleave", tip.hide)
                .on("click", function (evt) { evt.stopPropagation(); sel.run = ri; sel.step = idx; drawSelection(); openStep(ep, idx); });
            })(i);
          }
          if (e1 < x1) g.append("line").attr("x1", e1).attr("x2", e1).attr("y1", mid - 7).attr("y2", mid + 7).attr("stroke", "var(--ink)").attr("stroke-width", 1.2);
        });
        return Hh + 4;
      }

      // ----------------------------------------------- level 3: the thread
      function drawL3(body, sc, col, top) {
        var narrow = geo.narrow, x0 = geo.x0, x1 = geo.x1, ep = scope.ep, ax = axisOf(ep, st.measure);
        setupCanvas(geo.canvas, geo.W, 1);
        var threadY = top + 26, barH = narrow ? 18 : 26, Hh = 26 + 12 + barH * 2 + 10;
        geo.threadY = threadY; geo.rows = [];
        body.append("clipPath").attr("id", "evt-clip3").append("rect").attr("x", x0 - 8).attr("y", top).attr("width", x1 - x0 + 16).attr("height", Hh);
        var g = body.append("g").attr("clip-path", "url(#evt-clip3)");
        var e0 = clamp(sc.x(0), x0, x1), e1 = clamp(sc.x(ax.T), x0, x1);
        g.append("path").attr("class", "evt-thread").attr("d", "M" + e0 + "," + threadY + "H" + e1).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.5).attr("stroke-opacity", 0.55);
        // the fold labels on the thread: ×N · S over each dotted segment
        sc.folds.forEach(function (f) {
          // ×N · S when it fits over the segment, ×N alone when only that does
          var n = stepsIn([ep], st.measure, f.a, f.b), fw = f.x1 - f.x0, lab = "×" + n + " · " + unit(f.b - f.a, st.measure);
          if (narrow || lab.length * CH > fw + 24) lab = "×" + n;
          var fg = g.append("g").attr("class", "evt-fold evt-fold3").attr("data-fold", f.q.id).attr("data-steps", n);
          fg.append("line").attr("x1", f.x0).attr("x2", f.x1).attr("y1", threadY).attr("y2", threadY).attr("stroke", "var(--bg)").attr("stroke-width", 4);
          fg.append("line").attr("x1", f.x0).attr("x2", f.x1).attr("y1", threadY).attr("y2", threadY).attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-dasharray", "1.5 2.5").attr("stroke-linecap", "round");
          if (fw >= 10) fg.append("text").attr("x", (f.x0 + f.x1) / 2).attr("y", threadY - 8).attr("text-anchor", "middle").text(lab);
          fg.append("rect").attr("x", f.x0).attr("y", threadY - 10).attr("width", Math.max(6, f.x1 - f.x0)).attr("height", 20).attr("fill", "transparent");
          fg.append("title").text(n + " quiet steps · " + unit(f.b - f.a, st.measure) + " folded — click to open");
          fg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, [{ b: true, text: "×" + n + " quiet steps folded" }, { text: unit(f.b - f.a, st.measure) + " · " + tickText(f.a, st.measure) + "–" + tickText(f.b, st.measure) }, { text: "click to dilate" }]); })
            .on("pointerleave", tip.hide).on("click", function (evt) { evt.stopPropagation(); unfolded[f.q.id] = true; draw(); });
        });
        var inFold = function (i) { var t = ax.t0[i] + ax.dur[i] / 2; return sc.folds.some(function (f) { return t >= f.a && t < f.b; }); };
        var maxAbs = m.maxAbs || 1, barY = threadY + 14 + barH;
        g.append("line").attr("x1", x0).attr("x2", x1).attr("y1", barY).attr("y2", barY).attr("stroke", "var(--rule)").attr("stroke-width", 1);
        for (var i = 0; i < ep.n; i++) {
          var a = sc.x(ax.t0[i]), b = sc.x(ax.t0[i] + ax.dur[i]);
          if (b < x0 || a > x1) continue;
          var folded = inFold(i), w = Math.max(1.5, b - a - 0.5), cxm = a + w / 2, k = ep.kind[i], r = ep.rw[i], fl = ep.fl[i], isErr = fl.indexOf("e") >= 0;
          var sg = g.append("g").attr("class", "evt-step").attr("data-step", i).attr("data-kind", kindWord(k)).attr("data-flags", fl).attr("data-folded", folded ? "true" : "false");
          if (!folded) {
            // the unit on the thread: its seconds, coloured by kind; red when it erred
            sg.append("path").attr("d", "M" + a + "," + threadY + "H" + (a + w)).attr("fill", "none").attr("stroke", isErr ? "var(--bad)" : k === 2 ? "var(--ink)" : k === 1 ? "var(--ink-2)" : "var(--ink-3)").attr("stroke-width", k === 2 ? 5 : k === 1 ? 3.5 : 2).attr("stroke-opacity", k === 0 ? 0.6 : 0.9);
            if (fl.indexOf("v") >= 0) sg.append("rect").attr("x", a).attr("y", threadY + 4).attr("width", w).attr("height", 3).attr("fill", "var(--accent)").attr("fill-opacity", 0.8);
            if (isErr) sg.append("text").attr("class", "g").attr("x", cxm).attr("y", threadY + 4).attr("text-anchor", "middle").attr("fill", "var(--bad)").text("✕");
            else if (k === 2) sg.append("rect").attr("x", cxm - 4.5).attr("y", threadY - 4.5).attr("width", 9).attr("height", 9).attr("fill", ep.success ? "var(--good)" : "var(--bad)");
            else if (r > 0) sg.append("text").attr("class", "g").attr("x", cxm).attr("y", threadY + 4).attr("text-anchor", "middle").attr("fill", "var(--good)").text("◆");
            if (fl.indexOf("d") >= 0) sg.append("circle").attr("cx", cxm).attr("cy", threadY).attr("r", 5.5).attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-width", 2);
            if (fl.indexOf("f") >= 0) sg.append("line").attr("x1", cxm).attr("x2", cxm).attr("y1", threadY - 7).attr("y2", threadY + 7).attr("stroke", "var(--bad)").attr("stroke-width", 2.5);
          }
          // the reward bar: up when it paid, down when it cost, square-rooted so −0.10 stays visible next to ±5
          if (r !== 0) {
            var hgt = Math.max(1.5, barH * Math.sqrt(Math.abs(r) / maxAbs));
            sg.append("rect").attr("class", "evt-rbar").attr("x", cxm - Math.min(3, w / 2)).attr("width", Math.min(6, Math.max(1.5, w))).attr("y", r > 0 ? barY - hgt : barY).attr("height", hgt).attr("fill", r > 0 ? "var(--good)" : "var(--bad)").attr("fill-opacity", folded ? 0.35 : 0.85);
          }
          // a folded step is reachable at its reward bar only: the thread there belongs to the fold
          sg.append("rect").attr("x", a).attr("y", folded ? barY - barH : threadY - 10).attr("width", Math.max(1, b - a)).attr("height", folded ? barH * 2 : barY + barH - threadY + 10).attr("fill", "transparent");
          sg.append("title").text("step " + i + " · " + kindWord(k) + " " + ep.nm[i] + " · reward " + signed(r) + (folded ? " · in a fold" : "") + " — click to open");
          (function (idx, fd) {
            sg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, stepLines(ep, idx, fd ? "in a folded stretch · click to open" : "click to open the step")); })
              .on("pointerleave", tip.hide)
              .on("click", function (evt) { evt.stopPropagation(); sel.step = idx; drawSelection(); openStep(ep, idx); });
          })(i, folded);
        }
        body.append("text").attr("class", "tick").attr("x", x0 - 4).attr("y", barY - barH + 4).attr("text-anchor", "end").text("+" + maxAbs.toFixed(1));
        body.append("text").attr("class", "tick").attr("x", x0 - 4).attr("y", barY + barH).attr("text-anchor", "end").text("−" + maxAbs.toFixed(1));
        if (e1 < x1) g.append("line").attr("x1", e1).attr("x2", e1).attr("y1", threadY - 7).attr("y2", threadY + 7).attr("stroke", "var(--ink)").attr("stroke-width", 1.2);
        return Hh;
      }

      // ------------------------------------------------------- compare
      function drawCompare() {
        compareHost.innerHTML = "";
        if (!st.compare) return;
        var a = Math.min(st.compare[0], st.compare[1]), b = Math.max(st.compare[0], st.compare[1]);
        var t = H("table", { class: "evt-table", "data-from": String(a), "data-to": String(b) });
        t.appendChild(H("caption", { class: "evt-status", style: { textAlign: "left", captionSide: "top" }, text: "inside " + tickText(a, st.measure) + "–" + tickText(b, st.measure) + (st.measure === "steps" ? " (step index)" : " of each episode's clock") + ", per generation" }));
        t.appendChild(H("tr", null, ["gen", "episodes", "tool calls", "per ep", "return earned", "per ep", "errors"].map(function (h) { return H("th", { text: h }); })));
        m.gens.forEach(function (g) {
          var tools = 0, ret = 0, errs = 0, touched = 0;
          g.eps.forEach(function (ep) {
            var ax = axisOf(ep, st.measure), hit = false;
            for (var i = 0; i < ep.n; i++) {
              var t0 = ax.t0[i]; if (t0 < a || t0 >= b) continue;
              hit = true; ret += ep.rw[i]; if (ep.kind[i] === 1) tools++; if (ep.fl[i].indexOf("e") >= 0) errs++;
            }
            if (hit) touched++;
          });
          var n = g.eps.length || 1;
          t.appendChild(H("tr", { "data-gen": g.id }, [
            H("td", { class: "g", text: g.id }), H("td", { text: touched + " of " + g.eps.length }),
            H("td", { text: String(tools) }), H("td", { text: (tools / n).toFixed(1) }),
            H("td", { class: ret > 0 ? "pos" : ret < 0 ? "neg" : "", text: signed(ret, 1) }), H("td", { class: ret > 0 ? "pos" : ret < 0 ? "neg" : "", text: signed(ret / n) }),
            H("td", { text: String(errs) }),
          ]));
        });
        compareHost.appendChild(H("div", { class: "scroll-x" }, [t]));
      }

      // -------------------------------------------------------- keyboard
      stage.addEventListener("keydown", function (evt) {
        var handled = true, level = scope.level;
        function pan(dir) { var w = window_(), span = w[1] - w[0], T = spanT(), d = dir * span * 0.15; var a = clamp(w[0] + d, 0, T - span), b = a + span; applyView([a, b], true); }
        switch (evt.key) {
          case "ArrowUp": case "Up":
            if (level === 0) sel.lane = clamp(sel.lane - 1, 0, m.gens.length - 1);
            else if (level === 1) sel.row = clamp(sel.row - 1, 0, geo.rows.length - 1);
            else if (level === 2) sel.run = clamp(sel.run - 1, 0, geo.rows.length - 1);
            else handled = false;
            break;
          case "ArrowDown": case "Down":
            if (level === 0) sel.lane = clamp(sel.lane + 1, 0, m.gens.length - 1);
            else if (level === 1) sel.row = clamp(sel.row + 1, 0, geo.rows.length - 1);
            else if (level === 2) sel.run = clamp(sel.run + 1, 0, geo.rows.length - 1);
            else handled = false;
            break;
          case "ArrowLeft": case "Left":
            if (level >= 2) sel.step = Math.max(0, sel.step - (evt.shiftKey ? 10 : 1)); else pan(-1);
            break;
          case "ArrowRight": case "Right":
            if (level >= 2) { var epR = level === 3 ? scope.ep : scope.eps[sel.run]; sel.step = Math.min((epR ? epR.n : 1) - 1, sel.step + (evt.shiftKey ? 10 : 1)); } else pan(1);
            break;
          case "Home": if (level >= 2) sel.step = 0; else handled = false; break;
          case "End": if (level >= 2) { var epE = level === 3 ? scope.ep : scope.eps[sel.run]; sel.step = (epE ? epE.n : 1) - 1; } else handled = false; break;
          case "Enter": descend(null); break;
          case "Escape": case "Esc": if (level > 0) ascend(); else handled = false; break;
          case "+": case "=": { var w = window_(), c = (w[0] + w[1]) / 2, s = (w[1] - w[0]) / 4; applyView([c - s, c + s], true); break; }
          case "-": case "_": { var w2 = window_(), c2 = (w2[0] + w2[1]) / 2, s2 = Math.min(spanT() / 2, w2[1] - w2[0]); applyView([clamp(c2 - s2, 0, spanT()), clamp(c2 + s2, 0, spanT())], true); break; }
          default: handled = false;
        }
        if (handled) { evt.preventDefault(); evt.stopPropagation(); drawSelection(); drawStatus(); }
      });

      // ----------------------------------------------------------- mount
      function mountAll() { mountMini(); mountStage(); }
      var probe = H("div", {});
      root.appendChild(probe);
      L.layout.responsive(probe, function () { mountAll(); }, "evo-timescape");
      note.textContent = "Every column is a count over the recorded timelines" + (m.skipped ? "; " + m.skipped + " episodes past the timeline cap of " + DRAW_CAP + " are counted in their lane's total and not drawn" : "") + ". Levels 0 and 1 draw on a canvas from per-pixel bins and hit-test by bisection; a generation past " + RIBBON_CAP + " episodes draws per-task density bands instead of ribbons. The x measure, constriction, zoom path and window are remembered in this browser.";
    },
  });
})(typeof window !== "undefined" ? window : this);
