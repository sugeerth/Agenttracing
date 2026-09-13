/* AgentDiff blocks — the Evolution view: a self-evolving agent read as a lineage.
 *
 * A self-evolving agent is not two agents but a chain g0 → g1 → … where each
 * generation was derived from its parent by one step (a prompt edit, a rule,
 * a config change, a memory written) triggered by the parent's own episodes.
 * These blocks draw `aggregate.evolution` (deepcompare/evolve.py), and only
 * that — every point, bound, count and sentence is read from the JSON, so the
 * page and the engine cannot drift:
 *
 *   evo-lineage    the lineage as a thread: one node per generation on a
 *                  shared return axis (IQM with its bootstrap interval, the
 *                  zero line drawn), the best ringed, the recommended filled,
 *                  the last marked; every edge a step, coloured and labelled
 *                  by its verdict, as thick as |ΔIQM|. A branching lineage
 *                  lays out with d3.hierarchy; a brush under the axis picks a
 *                  range of generations for the other blocks.
 *   evo-steps      the ledger: one row per step, from → to, mechanism, diff
 *                  summary, P(improve) with its interval on one shared axis,
 *                  ΔIQM, Δpass, the tasks gained and regressed, the verdict,
 *                  the reading.
 *   evo-matrix     tasks × generations: pass rate (or mean return) per cell,
 *                  a mark where a cell fell from its left neighbour (that is
 *                  forgetting), each step's trigger tasks outlined in its
 *                  column (so overfitting shows as "the outlined cells moved
 *                  more than the rest").
 *   evo-step       one step in full: the artifact diff as a real diff beside
 *                  the effect — P(improve), per-task deltas with the trigger
 *                  tasks distinguished, the overfit, gaming and drift readings.
 *   evo-integrity  the protected paths touched, and growth against budget.
 *   evo-drift      distance from g0 and consecutive distance per step.
 *
 * Selection (the generation the reader is looking at, the matrix metric, a
 * range of generations) is one module-level store, persisted per browser
 * through the page's own Store; a change re-paints the mounted blocks in
 * place, the way the theatre does, with a transition where a shape moves.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;

  var PREF_KEY = "agentdiff:evolution";
  //: the transition when a selection moves; none under reduced motion
  var DUR = 240;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".evo{position:relative}",
      ".evo svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".evo text{font-size:var(--fs-xs)}",
      ".evo .lab{fill:var(--ink-2)}.evo .lab.dim{fill:var(--ink-3)}.evo .lab.mono{font-family:var(--mono)}.evo .lab.strong{fill:var(--ink);font-weight:600}",
      ".evo .tick{fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".evo .zero{stroke:var(--rule-2)}.evo .rule{stroke:var(--rule)}",
      ".evo .backed{paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
      ".evo-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:96ch}",
      ".evo-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 6px;max-width:96ch}",
      ".evo-lede b{font-weight:600}",
      ".evo-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".evo-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
      ".evo-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".evo-chip b{color:var(--ink);font-weight:600}",
      ".evo-bar button,.evo-nav button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer}",
      ".evo-bar button:hover,.evo-nav button:hover{color:var(--ink)}",
      ".evo-bar button[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
      ".evo-bar button:disabled,.evo-nav button:disabled{opacity:.4;cursor:default}",
      ".evo-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:96ch;line-height:1.5}",
      ".evo-note.bad{color:var(--bad)}",
      ".evo-v{font-weight:600}.evo-v .g{font-weight:700;margin-right:3px}",
      ".evo-edge{cursor:pointer;outline:none}.evo-edge:focus-visible .hit{stroke:var(--ink);stroke-opacity:.25}",
      ".evo-node{cursor:pointer;outline:none}.evo-node:focus-visible .ring{stroke:var(--ink);stroke-opacity:.6}",
      ".evo .selection{fill:var(--ink);fill-opacity:.08;stroke:var(--ink-3);stroke-width:1}",
      ".evo .overlay{cursor:crosshair}",
      // the ledger
      ".evo-rows{margin-top:4px}",
      ".evo-row{display:grid;grid-template-columns:82px 82px minmax(120px,2fr) minmax(96px,1.3fr) 62px 62px 66px 88px;gap:2px 10px;align-items:center;padding:4px 4px;border-radius:5px;cursor:pointer;font-size:var(--fs-xs);color:var(--ink-2);outline:none}",
      ".evo-row:hover,.evo-row:focus-visible{background:var(--surface-2)}",
      ".evo-row[aria-current=true]{background:var(--surface-2);box-shadow:inset 3px 0 0 var(--ink)}",
      ".evo-row[aria-current=true] .evo-id{color:var(--ink);font-weight:600}",
      ".evo-row .evo-id,.evo-row .evo-num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".evo-row .evo-num{text-align:right}.evo-row .evo-num.good{color:var(--good)}.evo-row .evo-num.bad{color:var(--bad)}",
      ".evo-row .evo-mech,.evo-row .evo-sum{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".evo-row .evo-read{grid-column:1 / -1;color:var(--ink-3);white-space:normal;line-height:1.4}",
      ".evo-head{cursor:default;color:var(--ink-3);font-family:var(--mono)}.evo-head:hover{background:none}",
      ".evo-track{position:relative;height:10px}",
      ".evo-track:before{content:'';position:absolute;left:0;right:0;top:4.5px;height:1px;background:var(--rule)}",
      ".evo-track:after{content:'';position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--rule-2)}",
      ".evo-int{position:absolute;top:2px;height:6px;border-radius:3px;background:var(--ink-3);opacity:.35}",
      ".evo-pt{position:absolute;top:1px;width:8px;height:8px;margin-left:-4px;border-radius:50%;background:var(--ink)}",
      "@media (max-width:820px){.evo-row{grid-template-columns:72px minmax(80px,1fr) 56px 84px}.evo-row .evo-mech,.evo-row .evo-sum,.evo-row .evo-pass,.evo-row .evo-tasks{display:none}}",
      // one step
      ".evo-nav{display:flex;gap:8px;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px;flex-wrap:wrap}",
      ".evo-cols{display:flex;gap:16px 24px;flex-wrap:wrap;align-items:flex-start}.evo-cols>*{flex:1 1 300px;min-width:0}",
      ".evo-h{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);margin:10px 0 3px;letter-spacing:.04em;text-transform:uppercase}",
      ".evo-h:first-child{margin-top:0}",
      ".evo-diff{font-family:var(--mono);font-size:var(--fs-xs);line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink-2);margin:0}",
      ".evo-diff .add{color:var(--good)}.evo-diff .del{color:var(--bad)}.evo-diff .hunk{color:var(--ink-3)}.evo-diff .ctx{color:var(--ink-3)}",
      ".evo-list{list-style:none;margin:0;padding:0;font-size:var(--fs-xs);font-family:var(--mono);color:var(--ink-2);line-height:1.5;overflow-wrap:anywhere}",
      ".evo-list .add{color:var(--good)}.evo-list .del{color:var(--bad)}.evo-list .chg{color:var(--ink-2)}",
      ".evo-list .quiet{color:var(--ink-3);font-family:var(--sans)}",
      ".evo-prot{display:inline-block;margin-left:6px;color:var(--bad);font-weight:600}",
      ".evo-figure{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:baseline;margin:0 0 4px}",
      ".evo-big{font-size:var(--fs-l);color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600}",
      ".evo-ci{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".evo-read{font-size:var(--fs-xs);color:var(--ink-2);margin:6px 0 0;line-height:1.5;max-width:70ch}",
      ".evo-read b{color:var(--ink);font-weight:600}.evo-read.flag b{color:var(--bad)}",
      // small multiples
      ".evo-multi{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px 18px}",
      ".evo-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
      ".evo-tip b{color:var(--ink)}.evo-tip .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
    ].join("\n");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------- helpers

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function num(v, p) {
    if (!isNum(v)) return "—";
    var s = v.toFixed(p === undefined ? 2 : p);
    if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s.replace("-", "−");
  }
  function signed(v, p) {
    if (!isNum(v)) return "—";
    var s = num(Math.abs(v), p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  }
  function pct(v) { return isNum(v) ? Math.round(v * 100) + "%" : "—"; }
  function pts(v) { return isNum(v) ? signed(v * 100, 0) + " pts" : "—"; }
  function short(id) { return String(id || "").replace(/^(rl|t)\d+_/, "").replace(/_/g, " "); }
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  function cap(s) { s = String(s || ""); return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function width(host) { var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0; return Math.max(300, Math.min(1400, w || 320)); }
  function responsive(host, draw, k) {
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, k);
    draw(); return host;
  }
  //: the page's task selection, only for a task the page actually has
  function selectTask(ctx, id) {
    var ids = typeof AgentDiff.taskIds === "function" ? AgentDiff.taskIds() : [];
    if (!id || ids.indexOf(id) < 0) return false;
    var fn = ctx && typeof ctx.selectTask === "function" ? ctx.selectTask
      : AgentDiff._internals && typeof AgentDiff._internals.selectTask === "function" ? AgentDiff._internals.selectTask : null;
    if (!fn) return false;
    fn(id);
    return true;
  }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); }
    catch (err) { return false; }
  }
  //: the concrete colour behind a token, for the scales d3 interpolates
  function resolve(el, name, fallback) {
    try { var v = getComputedStyle(el).getPropertyValue(name); return v && v.trim() ? v.trim() : fallback; }
    catch (err) { return fallback; }
  }
  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "evo-tip"; tip.hidden = true; root.appendChild(tip);
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

  /* The verdict vocabulary. Colour is polarity — good, bad, neutral, and
   * caution for the three ways a step can look good and be wrong — and the
   * glyph is the kind, in the mark vocabulary of "Where it mattered":
   * ✕ the error (gamed: it bought reward, not correctness), ▏the fault's
   * path (forgot: a task fell), ◎ the decisive step (overfit: it fitted the
   * episodes that decided it), ⇄ the divergence (traded: one task for
   * another). A verdict the engine does not name draws grey. */
  var VERDICT = {
    improved: { color: "var(--good)", glyph: "", word: "improved" },
    regressed: { color: "var(--bad)", glyph: "", word: "regressed" },
    flat: { color: "var(--ink-3)", glyph: "", word: "flat" },
    gamed: { color: "var(--bad)", glyph: "✕", word: "gamed" },
    forgot: { color: "var(--warn)", glyph: "▏", word: "forgot" },
    overfit: { color: "var(--warn)", glyph: "◎", word: "overfit" },
    traded: { color: "var(--warn)", glyph: "⇄", word: "traded" },
  };
  function verdictOf(step) { return VERDICT[step && step.verdict] || { color: "var(--ink-3)", glyph: "", word: step && step.verdict ? String(step.verdict) : "unmeasured" }; }
  function verdictLabel(v) { return (v.glyph ? v.glyph + " " : "") + v.word; }
  function verdictSpan(H, step) {
    var v = verdictOf(step);
    return H("span", { class: "evo-v", "data-verdict": step.verdict || "", style: { color: v.color } }, [
      v.glyph ? H("span", { class: "g", text: v.glyph }) : null, H("span", { text: v.word })]);
  }

  // --------------------------------------------------------------- model

  /* The lineage as the blocks want it: generations in lineage order with a
   * column index, steps keyed by the generation they made, the tasks in
   * order of first appearance, and the task × generation cells counted from
   * the episodes the engine included. Nothing is estimated here: a cell is
   * passes over runs, a return is a mean over the recorded episodes. */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {};
    if (cache && cache.agg === agg) return cache.m;
    var m;
    try { m = build(agg); } catch (err) { console.warn("AgentDiff evolution: model failed", err); m = { ok: false, reason: "the evolution section could not be read" }; }
    cache = { agg: agg, m: m };
    return m;
  }

  function build(agg) {
    var ev = agg && agg.evolution && typeof agg.evolution === "object" ? agg.evolution : null;
    if (!ev) return { ok: false, reason: null };
    if (ev.measurable === false) return { ok: false, reason: ev.reason || "the lineage could not be measured" };
    var rawGens = (Array.isArray(ev.generations) ? ev.generations : []).filter(function (g) { return g && g.id; });
    if (!rawGens.length) return { ok: false, reason: "the lineage names no generation" };
    var gens = rawGens.map(function (g, i) {
      var iqm = g.iqm && typeof g.iqm === "object" ? g.iqm : {};
      return {
        id: String(g.id), parent: g.parent === null || g.parent === undefined ? null : String(g.parent), raw: g,
        i: i, index: isNum(g.index) ? g.index : i, mechanism: g.mechanism || null,
        point: isNum(iqm.point) ? iqm.point : isNum(g.mean_return) ? g.mean_return : null,
        lo: isNum(iqm.lo) ? iqm.lo : null, hi: isNum(iqm.hi) ? iqm.hi : null,
        estimate: !isNum(iqm.point) && isNum(g.mean_return),
        pass: isNum(g.pass_rate) ? g.pass_rate : null, passes: isNum(g.passes) ? g.passes : null,
        n: isNum(g.episodes_n) ? g.episodes_n : (Array.isArray(g.episodes) ? g.episodes.length : null),
        size: g.size && typeof g.size === "object" ? g.size : {},
        episodes: Array.isArray(g.episodes) ? g.episodes.filter(function (e) { return e && e.task_id; }) : [],
        tasks: Array.isArray(g.tasks) ? g.tasks.map(String) : [],
        runs: g.runs_per_task && typeof g.runs_per_task === "object" ? g.runs_per_task : {},
        note: g.note || "",
      };
    }).sort(function (p, q) { return p.index - q.index || p.i - q.i; });
    gens.forEach(function (g, i) { g.i = i; });
    var byId = {};
    gens.forEach(function (g) { byId[g.id] = g; });
    var steps = (Array.isArray(ev.steps) ? ev.steps : []).filter(function (s) { return s && s.from && s.to && byId[s.to]; }).map(function (s, i) {
      var eff = s.effect && typeof s.effect === "object" ? s.effect : { measurable: false, reason: "no effect was measured" };
      var imp = eff.improvement && typeof eff.improvement === "object" ? eff.improvement : {};
      return {
        raw: s, from: String(s.from), to: String(s.to), fromGen: byId[s.from] || null, toGen: byId[s.to], i: i,
        key: String(s.from) + " → " + String(s.to), mechanism: s.mechanism || "", verdict: s.verdict || null,
        diff: s.diff && typeof s.diff === "object" ? s.diff : {}, effect: eff, measurable: eff.measurable !== false,
        p: isNum(imp.point) ? imp.point : null, plo: isNum(imp.lo) ? imp.lo : null, phi: isNum(imp.hi) ? imp.hi : null,
        dIqm: eff.iqm && isNum(eff.iqm.delta) ? eff.iqm.delta : null,
        dPass: eff.pass_rate && isNum(eff.pass_rate.delta) ? eff.pass_rate.delta : null,
        gained: Array.isArray(eff.gained) ? eff.gained : [], regressed: Array.isArray(eff.regressed) ? eff.regressed : [],
        forgotten: Array.isArray(eff.forgotten) ? eff.forgotten : [], noisy: imp.noisy === true,
        perTask: eff.per_task && typeof eff.per_task === "object" ? eff.per_task : {},
        trigger: Array.isArray(s.trigger_tasks) ? s.trigger_tasks.map(String) : [],
        overfit: s.overfit || null, gaming: s.gaming || null, drift: s.drift || null, reading: s.reading || "",
        summary: s.diff && s.diff.summary ? String(s.diff.summary) : "",
      };
    }).sort(function (p, q) { return p.toGen.i - q.toGen.i; });
    var stepByTo = {};
    steps.forEach(function (s) { stepByTo[s.to] = s; });
    // the tasks, in order of first appearance
    var tasks = [];
    function addTask(t) { if (t && tasks.indexOf(t) < 0) tasks.push(t); }
    gens.forEach(function (g) { g.tasks.forEach(addTask); g.episodes.forEach(function (e) { addTask(String(e.task_id)); }); });
    steps.forEach(function (s) { Object.keys(s.perTask).forEach(addTask); });
    // the cells: counts over the recorded episodes; a step's per-task pass
    // rates fill a generation that carried no episodes
    var cells = {};
    tasks.forEach(function (t) { cells[t] = {}; });
    gens.forEach(function (g) {
      g.episodes.forEach(function (e) {
        var t = String(e.task_id), c = cells[t][g.id] || (cells[t][g.id] = { n: 0, passes: 0, sum: 0, returns: [] });
        c.n++; if (e.success) c.passes++;
        if (isNum(e["return"])) { c.sum += e["return"]; c.returns.push(e["return"]); }
      });
    });
    tasks.forEach(function (t) {
      gens.forEach(function (g) {
        var c = cells[t][g.id];
        if (c) { c.pass = c.n ? c.passes / c.n : null; c.mean = c.returns.length ? c.sum / c.returns.length : null; c.source = "episodes"; return; }
        var s = stepByTo[g.id], pt = s && s.perTask[t];
        var prevS = steps.filter(function (x) { return x.from === g.id && x.perTask[t]; })[0];
        var pass = pt && isNum(pt.pass_to) ? pt.pass_to : prevS && isNum(prevS.perTask[t].pass_from) ? prevS.perTask[t].pass_from : null;
        if (pass !== null) cells[t][g.id] = { n: isNum(g.runs[t]) ? g.runs[t] : null, passes: null, pass: pass, mean: null, returns: [], source: "step" };
      });
    });
    var maxAbsDelta = 0;
    steps.forEach(function (s) { if (isNum(s.dIqm)) maxAbsDelta = Math.max(maxAbsDelta, Math.abs(s.dIqm)); });
    return {
      ok: true, ev: ev, gens: gens, byId: byId, steps: steps, stepByTo: stepByTo, tasks: tasks, cells: cells,
      maxAbsDelta: maxAbsDelta, family: ev.family || "", protected: Array.isArray(ev.protected) ? ev.protected : [],
      budget: ev.budget && typeof ev.budget === "object" ? ev.budget : {},
      best: ev.best && ev.best.id ? ev.best : null, recommended: ev.recommended && ev.recommended.id ? ev.recommended : null,
      last: gens[gens.length - 1], trajectory: ev.trajectory || {}, integrity: ev.integrity || null, drift: ev.drift || null,
      narrative: typeof ev.narrative === "string" ? ev.narrative : "", advisory: typeof ev.advisory === "string" ? ev.advisory : "",
      branching: gens.some(function (g) { return g.parent !== null && g.parent !== gens[g.i - 1].id; }),
    };
  }

  /* The lineage as a d3 hierarchy. The demo is linear, but a parent with two
   * children must draw correctly: d3.stratify builds the tree from the
   * parent chain, d3.tree orders the branches, and the columns follow
   * lineage order so every generation keeps its own place on the axis. */
  function hierarchy(m) {
    var rows = m.gens.map(function (g) { return { id: g.id, parent: g.parent && m.byId[g.parent] ? g.parent : null }; });
    var roots = rows.filter(function (r) { return r.parent === null; });
    if (roots.length !== 1) rows.forEach(function (r, i) { r.parent = i === 0 ? null : rows[i - 1].id; });
    var root;
    try { root = d3.stratify().id(function (d) { return d.id; }).parentId(function (d) { return d.parent; })(rows); }
    catch (err) { rows.forEach(function (r, i) { r.parent = i === 0 ? null : rows[i - 1].id; }); root = d3.stratify().id(function (d) { return d.id; }).parentId(function (d) { return d.parent; })(rows); }
    d3.tree().size([1, 1])(root);
    return root;
  }

  // --------------------------------------------------------------- store

  /* One store for every block: the generation in view (the step that made
   * it is the selected step), the matrix metric, and a range of generations
   * picked with the brush. Persisted per browser through the page's Store;
   * a change re-paints every mounted block in place. */
  var S = { gen: null, metric: "pass", range: null, loaded: false };
  var LISTENERS = [];
  function store() {
    try { return AgentDiff._internals && AgentDiff._internals.Store ? AgentDiff._internals.Store : null; } catch (err) { return null; }
  }
  function loadState(m) {
    if (!S.loaded) {
      S.loaded = true;
      var s = store(), v = s ? s.get(PREF_KEY) : null;
      if (v && typeof v === "object") {
        if (typeof v.gen === "string") S.gen = v.gen;
        if (v.metric === "return" || v.metric === "pass") S.metric = v.metric;
        if (Array.isArray(v.range) && isNum(v.range[0]) && isNum(v.range[1])) S.range = [v.range[0], v.range[1]];
      }
    }
    // a stored choice that this lineage cannot honour falls back to the last step
    if (!S.gen || !m.byId[S.gen]) S.gen = m.steps.length ? m.steps[m.steps.length - 1].to : m.last.id;
    if (S.range && (S.range[0] < 0 || S.range[1] >= m.gens.length || S.range[0] > S.range[1])) S.range = null;
    return S;
  }
  function saveState() {
    var s = store();
    if (s) { try { s.set(PREF_KEY, { gen: S.gen, metric: S.metric, range: S.range }); } catch (err) { /* quota; the session still holds it */ } }
  }
  function listen(host, fn) { LISTENERS.push({ host: host, fn: fn }); }
  function broadcast(what) {
    LISTENERS = LISTENERS.filter(function (l) { return l.host.isConnected; });
    LISTENERS.forEach(function (l) { try { l.fn(what); } catch (err) { console.warn("AgentDiff evolution: repaint failed", err); } });
  }
  function select(patch) {
    var changed = {};
    Object.keys(patch || {}).forEach(function (k) {
      var v = patch[k];
      if (k === "range") { var same = (v === null && S.range === null) || (v && S.range && v[0] === S.range[0] && v[1] === S.range[1]); if (same) return; }
      else if (S[k] === v) return;
      S[k] = v; changed[k] = true;
    });
    if (!Object.keys(changed).length) return;
    saveState();
    broadcast(changed);
  }
  function selectedStep(m) { return m.stepByTo[S.gen] || null; }
  function inRange(m, g) { return !S.range || (g.i >= S.range[0] && g.i <= S.range[1]); }
  //: a step is in the range when the generation it made is — so a range of one generation still shows the step that made it
  function stepInRange(m, s) { return inRange(m, s.toGen); }
  function rangeLabel(m) { return S.range ? m.gens[S.range[0]].id + "–" + m.gens[S.range[1]].id : ""; }

  // a small surface for the sibling blocks (the timescape) and the tests
  AgentDiff.evolution = {
    select: select,
    state: function () { return { gen: S.gen, metric: S.metric, range: S.range ? S.range.slice() : null }; },
    listen: listen,
  };

  function stepLines(s) {
    var v = verdictOf(s);
    var flags = Array.isArray(s.raw.flags) ? s.raw.flags.map(String) : s.raw.flags && typeof s.raw.flags === "object" ? Object.keys(s.raw.flags).filter(function (k) { return s.raw.flags[k]; }) : [];
    return [
      { b: true, text: s.key + " · " + (s.mechanism || "step") + " · " + verdictLabel(v) },
      s.summary ? { text: s.summary } : null,
      s.measurable ? { mono: true, text: "P(improve) " + pct(s.p) + " [" + pct(s.plo) + ", " + pct(s.phi) + "]" + (s.noisy ? " · spans the coin flip" : "") + " · ΔIQM " + signed(s.dIqm) + " · Δpass " + pts(s.dPass) }
        : { text: "effect not measured: " + (s.effect.reason || "no reason given") },
      s.gained.length || s.regressed.length ? { text: (s.gained.length ? "gained " + s.gained.map(short).join(", ") : "") + (s.gained.length && s.regressed.length ? " · " : "") + (s.regressed.length ? "regressed " + s.regressed.map(short).join(", ") : "") } : null,
      s.forgotten.length ? { text: "forgot " + s.forgotten.map(short).join(", ") } : null,
      flags.length ? { text: "flags: " + flags.join(", ") } : null,
      { text: "click to open this step" },
    ];
  }
  function genLines(m, g) {
    var s = m.stepByTo[g.id];
    return [
      { b: true, text: g.id + (m.best && m.best.id === g.id ? " · best" : "") + (m.recommended && m.recommended.id === g.id ? " · recommended" : "") + (g.id === m.last.id ? " · last" : "") },
      { mono: true, text: (g.estimate ? "mean return " : "IQM ") + num(g.point) + (isNum(g.lo) ? " [" + num(g.lo) + ", " + num(g.hi) + "]" : "") + " · pass " + pct(g.pass) + (isNum(g.passes) && isNum(g.n) ? " (" + g.passes + "/" + g.n + ")" : "") },
      { text: (s ? "made by " + s.key + " (" + (s.mechanism || "step") + ")" : "the root: no step made it") + " · prompt " + num(g.size.prompt_chars, 0) + " chars · " + num(g.size.rules, 0) + " rules · " + num(g.size.memory, 0) + " notes" },
      g.raw.measurable === false && g.raw.reason ? { text: "not measured: " + g.raw.reason } : null,
      { text: s ? "click to open the step that made it" : "click to open the root" },
    ];
  }

  // ============================================================ evo-lineage

  function drawLineage(host, ctx, m, tip, ref) {
    if (!d3) return;
    var W = width(host), narrow = W < 560, n = m.gens.length;
    var padL = narrow ? 40 : 48, padR = 14, padT = 30, padB = 40;
    var H = narrow ? 200 : 240;
    var ids = m.gens.map(function (g) { return g.id; });
    var x = d3.scalePoint().domain(ids).range([padL + 12, W - padR - 12]);
    var lo = 0, hi = 0;
    m.gens.forEach(function (g) { [g.point, g.lo, g.hi].forEach(function (v) { if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); });
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([H - padB, padT]);
    var sw = d3.scaleSqrt().domain([0, Math.max(1e-9, m.maxAbsDelta)]).range([1.5, narrow ? 7 : 9]);
    var root = hierarchy(m);
    var byNode = {};
    root.each(function (d) { byNode[d.data.id] = d; });
    var counts = m.trajectory || {};
    var label = m.family + ": " + n + " generations on one return axis, IQM with its bootstrap interval; "
      + m.steps.length + " steps as edges coloured by verdict and as thick as the IQM delta ("
      + Object.keys(VERDICT).filter(function (k) { return counts[k]; }).map(function (k) { return counts[k] + " " + k; }).join(", ")
      + "); best " + (m.best ? m.best.id : "—") + " ringed, recommended " + (m.recommended ? m.recommended.id : "—") + " filled, last " + m.last.id;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("aria-label", label)
      .attr("data-generations", n).attr("data-steps", m.steps.length);
    // the return axis: a few ticks, the zero line drawn where it falls
    y.ticks(narrow ? 3 : 4).forEach(function (t) {
      svg.append("line").attr("class", t === 0 ? "zero" : "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(t)).attr("y2", y(t)).attr("stroke-dasharray", t === 0 ? null : "1 3");
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(t) + 4).attr("text-anchor", "end").text(signed(t, 1));
    });
    svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 11).text("IQM return, with its interval");
    // the range, if the reader picked one
    var band = svg.append("rect").attr("class", "evo-range").attr("y", padT - 4).attr("height", H - padB - padT + 8).attr("fill", "var(--ink)").attr("fill-opacity", 0);
    function bandTo(animate) {
      var t = animate ? band.transition().duration(DUR) : band;
      if (S.range) t.attr("x", x(ids[S.range[0]]) - 10).attr("width", x(ids[S.range[1]]) - x(ids[S.range[0]]) + 20).attr("fill-opacity", 0.05);
      else t.attr("fill-opacity", 0);
    }
    band.attr("x", padL).attr("width", 0); bandTo(false);
    // the edges: one per step, a link from the parent's point to the child's
    var link = d3.linkHorizontal().x(function (d) { return d[0]; }).y(function (d) { return d[1]; });
    function yOf(g) { return y(isNum(g.point) ? g.point : 0); }
    var edges = svg.append("g").attr("class", "evo-edges").selectAll("g").data(m.steps.filter(function (s) { return s.fromGen; })).enter().append("g")
      .attr("class", "evo-edge").attr("data-step", function (s) { return s.key; }).attr("data-to", function (s) { return s.to; })
      .attr("data-verdict", function (s) { return s.verdict || ""; }).attr("tabindex", 0).attr("role", "button")
      .attr("aria-label", function (s) { return s.key + " · " + (s.mechanism || "step") + " · " + verdictOf(s).word + " · P(improve) " + pct(s.p) + " · ΔIQM " + signed(s.dIqm); });
    edges.append("path").attr("class", "hit").attr("fill", "none").attr("stroke", "transparent").attr("stroke-width", 16)
      .attr("d", function (s) { return link({ source: [x(s.from), yOf(s.fromGen)], target: [x(s.to), yOf(s.toGen)] }); });
    edges.append("path").attr("class", "seg").attr("fill", "none").attr("stroke-linecap", "round")
      .attr("stroke", function (s) { return verdictOf(s).color; })
      .attr("stroke-width", function (s) { return s.measurable ? sw(Math.abs(s.dIqm || 0)) : 1.5; })
      .attr("stroke-dasharray", function (s) { return s.measurable ? null : "3 4"; })
      .attr("d", function (s) { return link({ source: [x(s.from), yOf(s.fromGen)], target: [x(s.to), yOf(s.toGen)] }); });
    // the verdict on the edge, when the columns leave room for a word
    var room = n > 1 ? (x(ids[1]) - x(ids[0])) : W;
    edges.append("text").attr("class", "lab backed").attr("text-anchor", "middle").attr("pointer-events", "none")
      .attr("fill", function (s) { return verdictOf(s).color; })
      .attr("x", function (s) { return (x(s.from) + x(s.to)) / 2; })
      .attr("y", function (s) { return (yOf(s.fromGen) + yOf(s.toGen)) / 2 - (s.measurable ? sw(Math.abs(s.dIqm || 0)) / 2 : 1) - 5; })
      .text(function (s) { var v = verdictOf(s); return room >= 70 ? verdictLabel(v) : room >= 26 ? (v.glyph || v.word.charAt(0)) : ""; });
    edges.on("pointermove", function (evt, s) { tip.show(evt, stepLines(s)); }).on("pointerleave", tip.hide)
      .on("click", function (evt, s) { tip.hide(); select({ gen: s.to }); })
      .on("keydown", function (evt, s) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); select({ gen: s.to }); } });
    // the nodes: the interval, the point, the marks
    var nodes = svg.append("g").attr("class", "evo-nodes").selectAll("g").data(m.gens).enter().append("g")
      .attr("class", "evo-node").attr("data-gen", function (g) { return g.id; }).attr("tabindex", 0).attr("role", "button")
      .attr("data-best", function (g) { return m.best && m.best.id === g.id ? "1" : null; })
      .attr("data-recommended", function (g) { return m.recommended && m.recommended.id === g.id ? "1" : null; })
      .attr("data-last", function (g) { return g.id === m.last.id ? "1" : null; })
      .attr("aria-label", function (g) { return g.id + ": IQM " + num(g.point) + " [" + num(g.lo) + ", " + num(g.hi) + "], pass " + pct(g.pass); })
      .attr("transform", function (g) { return "translate(" + x(g.id) + ",0)"; });
    nodes.filter(function (g) { return isNum(g.lo) && isNum(g.hi); }).each(function (g) {
      var sel = d3.select(this);
      sel.append("line").attr("class", "int").attr("y1", y(g.lo)).attr("y2", y(g.hi)).attr("stroke", "var(--ink-3)").attr("stroke-width", 2).attr("stroke-opacity", 0.5).attr("stroke-linecap", "round");
      [g.lo, g.hi].forEach(function (v) { sel.append("line").attr("x1", -3.5).attr("x2", 3.5).attr("y1", y(v)).attr("y2", y(v)).attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-opacity", 0.5); });
    });
    nodes.append("circle").attr("class", "ring").attr("cy", yOf).attr("r", 10).attr("fill", "none")
      .attr("stroke", "var(--ink)").attr("stroke-width", 1.5).attr("stroke-opacity", function (g) { return m.best && m.best.id === g.id ? 0.9 : 0; });
    nodes.append("circle").attr("class", "pt").attr("cy", yOf).attr("r", 5.5)
      .attr("fill", function (g) { return m.recommended && m.recommended.id === g.id ? "var(--accent)" : "var(--surface)"; })
      .attr("stroke", function (g) { return m.recommended && m.recommended.id === g.id ? "var(--accent)" : "var(--ink-2)"; }).attr("stroke-width", 1.5)
      .attr("stroke-dasharray", function (g) { return g.estimate ? "2 2" : null; });
    nodes.append("circle").attr("cy", yOf).attr("r", 12).attr("fill", "transparent");
    // the labels under the axis: every generation when there is room, else every k-th
    var every = Math.max(1, Math.ceil((narrow ? 30 : 34) / Math.max(1, room)));
    nodes.append("text").attr("class", "lab mono").attr("text-anchor", "middle").attr("y", H - padB + 15).attr("pointer-events", "none")
      .text(function (g) { return g.i % every === 0 || g.id === m.last.id ? trunc(g.id, 6) : ""; });
    nodes.append("text").attr("class", "tick").attr("text-anchor", "middle").attr("y", H - padB + 28).attr("pointer-events", "none")
      .text(function (g) { return g.id === m.last.id ? "last" : g.i % every === 0 && room >= 40 ? pct(g.pass) : ""; });
    nodes.on("pointermove", function (evt, g) { tip.show(evt, genLines(m, g)); }).on("pointerleave", tip.hide)
      .on("click", function (evt, g) { tip.hide(); select({ gen: g.id }); })
      .on("keydown", function (evt, g) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); select({ gen: g.id }); } });
    // the brush, in the label strip under the axis: a range of generations
    // for the ledger, the matrix and the drift chart
    if (n > 1) {
      var moving = false;
      var brush = d3.brushX().extent([[padL, H - padB + 4], [W - padR, H - 2]]).on("end", function (evt) {
        if (moving || !evt.sourceEvent) return;
        if (!evt.selection) { select({ range: null }); return; }
        var i0 = null, i1 = null;
        ids.forEach(function (id, i) { var px = x(id); if (px >= evt.selection[0] - 8 && px <= evt.selection[1] + 8) { if (i0 === null) i0 = i; i1 = i; } });
        if (i0 === null) { moving = true; d3.select(this).call(brush.move, null); moving = false; select({ range: null }); return; }
        if (i0 === 0 && i1 === n - 1) { moving = true; d3.select(this).call(brush.move, null); moving = false; select({ range: null }); return; }
        moving = true; d3.select(this).call(brush.move, [x(ids[i0]) - 10, x(ids[i1]) + 10]); moving = false;
        select({ range: [i0, i1] });
      });
      var bg = svg.append("g").attr("class", "evo-brush").call(brush);
      bg.select(".overlay").append("title").text("drag across the generations to pick a range for the ledger, the matrix and the drift chart");
      if (S.range) { moving = true; bg.call(brush.move, [x(ids[S.range[0]]) - 10, x(ids[S.range[1]]) + 10]); moving = false; }
      ref.brush = { g: bg, brush: brush, clear: function () { moving = true; bg.call(brush.move, null); moving = false; } };
    }
    // what the selection looks like, applied now and again when it changes
    function apply(animate) {
      var dur = animate && !prefersReduced() ? DUR : 0;
      var step = selectedStep(m);
      var eOp = edges.select(".seg");
      (dur ? eOp.transition().duration(dur) : eOp)
        .attr("stroke-opacity", function (s) { return !step ? 0.85 : s === step ? 1 : 0.4; });
      var tOp = edges.select("text");
      (dur ? tOp.transition().duration(dur) : tOp).attr("opacity", function (s) { return !step ? 0.9 : s === step ? 1 : 0.55; });
      var pt = nodes.select(".pt");
      (dur ? pt.transition().duration(dur) : pt)
        .attr("r", function (g) { return g.id === S.gen ? 7 : 5.5; })
        .attr("stroke", function (g) { return g.id === S.gen ? "var(--ink)" : m.recommended && m.recommended.id === g.id ? "var(--accent)" : "var(--ink-2)"; })
        .attr("stroke-width", function (g) { return g.id === S.gen ? 2.5 : 1.5; });
      nodes.attr("aria-current", function (g) { return g.id === S.gen ? "true" : "false"; });
      edges.attr("aria-current", function (s) { return s === step ? "true" : "false"; });
      bandTo(dur > 0);
    }
    apply(false);
    ref.apply = apply;
  }

  function lineageBar(H, m, ctx, refs) {
    var bar = H("div", { class: "evo-bar" });
    if (m.best) bar.appendChild(H("span", { class: "evo-chip", "data-role": "best", title: m.best.why || "" }, [H("span", { text: "◯ best " }), H("b", { text: m.best.id }), H("span", { text: " · IQM " + num(m.best.iqm) })]));
    if (m.recommended) bar.appendChild(H("span", { class: "evo-chip", "data-role": "recommended", title: m.recommended.why || "" }, [H("i", { style: { background: "var(--accent)" } }), H("span", { text: "recommended " }), H("b", { text: m.recommended.id }), H("span", { text: m.recommended.is_last ? " · the last" : " · not the last" })]));
    bar.appendChild(H("span", { class: "evo-chip", "data-role": "last", text: "last " + m.last.id + (m.trajectory && isNum(m.trajectory.net_iqm_delta) ? " · net IQM " + signed(m.trajectory.net_iqm_delta) : "") }));
    Object.keys(VERDICT).forEach(function (k) {
      var c = m.trajectory ? m.trajectory[k] : 0;
      if (!isNum(c) || !c) return;
      bar.appendChild(H("span", { class: "evo-chip", "data-verdict": k, style: { color: VERDICT[k].color } }, [H("span", { text: (VERDICT[k].glyph ? VERDICT[k].glyph + " " : "") + c + " " + k })]));
    });
    // the engine's flags across the lineage — not verdicts, but counted the same way
    var fl = m.trajectory && m.trajectory.flags && typeof m.trajectory.flags === "object" ? m.trajectory.flags : null;
    if (fl) {
      var flagText = Object.keys(fl).filter(function (k) { return isNum(fl[k]) && fl[k] > 0; }).map(function (k) { return fl[k] + " " + k.replace(/_/g, " "); }).join(" · ");
      if (flagText) bar.appendChild(H("span", { class: "evo-chip", "data-role": "flags", title: "flags the engine raised on steps, beside their verdicts", text: "flags: " + flagText }));
    }
    var rangeChip = H("span", { class: "evo-chip evo-range-chip", hidden: !S.range }, [
      H("span", { text: "range " }), H("b", { text: rangeLabel(m) }), H("span", { text: " " }),
      H("button", { type: "button", text: "clear", onclick: function () { if (refs.brush) refs.brush.clear(); select({ range: null }); } })]);
    bar.appendChild(rangeChip);
    refs.rangeChip = rangeChip;
    return bar;
  }

  AgentDiff.block({
    id: "evo-lineage",
    title: "The lineage",
    question: "Generation by generation on one return axis: which step helped, which one only looked like it did, and which generation to keep.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { return model(ctx).ok ? 0.9 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evo evo-lineage" });
      el.appendChild(root);
      var tip = tooltip(root);
      // the lede is the answer to "which generation to keep"; the whole narrative is one click away
      var rec = m.recommended;
      var lede = H("p", { class: "evo-narr evo-lead", "data-recommended": rec ? rec.id : "" });
      if (rec) {
        lede.appendChild(H("span", { text: m.gens.length + " generations of " + (m.family || "the agent") + ". Keep " }));
        lede.appendChild(H("b", { text: rec.id }));
        lede.appendChild(H("span", { text: (rec.is_last ? " — the last generation" : " — not the last (" + m.last.id + ")") + (rec.why ? ": " + cap(String(rec.why)) : "") + (rec.why && String(rec.why).slice(-1) === "." ? "" : ".") }));
      } else {
        lede.appendChild(H("span", { text: m.gens.length + " generations of " + (m.family || "the agent") + "; the engine named no generation to keep." }));
      }
      root.appendChild(lede);
      var refs = {};
      root.appendChild(lineageBar(H, m, ctx, refs));
      var host = H("div", { class: "evo-chart" });
      root.appendChild(responsive(host, function () { drawLineage(host, ctx, m, tip, refs); }, "evo-lineage"));
      root.appendChild(H("p", { class: "evo-note", text:
        "A node is a generation: its IQM return over every recorded episode, the bar its stratified bootstrap interval, the zero line where it falls"
        + (m.gens.some(function (g) { return g.estimate; }) ? " (a dashed node has only a mean, no IQM)" : "") + ". "
        + "An edge is the step that made the child, coloured by its verdict and as thick as the IQM it moved; "
        + "✕ gamed bought return without passes, ▏forgot a task, ◎ overfit its trigger tasks, ⇄ traded one task for another. "
        + "The best generation is ringed, the recommended one filled, the last one says so. Hover an edge for the change and P(improve); click it to open the step; "
        + "drag under the axis to pick a range." + (m.branching ? " This lineage branches: a parent with more than one child." : "") }));
      if (m.advisory) root.appendChild(H("p", { class: "evo-note evo-advisory", text: m.advisory }));
      if (m.narrative) root.appendChild(H("details", { class: "evo-details" }, [H("summary", { class: "evo-note", style: { cursor: "pointer", color: "var(--ink-2)", marginTop: "6px" }, text: "the whole lineage in a paragraph" }), H("p", { class: "evo-note evo-narrative", text: m.narrative })]));
      listen(root, function (changed) {
        if (refs.apply && (changed.gen || changed.range)) refs.apply(true);
        if (refs.rangeChip) { refs.rangeChip.hidden = !S.range; var b = refs.rangeChip.querySelector("b"); if (b) b.textContent = rangeLabel(m); }
      });
    },
  });

  // ============================================================== evo-steps

  function pTrack(H, s) {
    var track = H("div", { class: "evo-track", role: "img", "aria-label": s.measurable ? "P(improve) " + pct(s.p) + ", interval " + pct(s.plo) + " to " + pct(s.phi) : "P(improve) not measured", title: s.measurable ? "P(" + s.to + " > " + s.from + ") = " + pct(s.p) + " [" + pct(s.plo) + ", " + pct(s.phi) + "] · the mid line is the coin flip" : (s.effect.reason || "not measured") });
    if (s.measurable && isNum(s.p)) {
      if (isNum(s.plo) && isNum(s.phi)) track.appendChild(H("div", { class: "evo-int", style: { left: (100 * s.plo) + "%", width: Math.max(0.5, 100 * (s.phi - s.plo)) + "%" } }));
      track.appendChild(H("div", { class: "evo-pt", style: { left: (100 * s.p) + "%", background: s.p > 0.5 ? "var(--good)" : s.p < 0.5 ? "var(--bad)" : "var(--ink)" } }));
    }
    return track;
  }

  AgentDiff.block({
    id: "evo-steps",
    title: "The steps",
    question: "One row per step: what changed, whether it helped (with an interval), which tasks it gained and which it lost, and the verdict.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.steps.length ? 0.89 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evo evo-steps" });
      el.appendChild(root);
      var tip = tooltip(root);
      var lede = H("p", { class: "evo-lede" });
      root.appendChild(lede);
      var list = H("div", { class: "evo-rows", role: "list", "aria-label": "the steps of the lineage, in order" });
      root.appendChild(list);
      var rows = {};
      function paint() {
        list.innerHTML = "";
        var shown = m.steps.filter(function (s) { return stepInRange(m, s); });
        var head = H("div", { class: "evo-row evo-head", role: "presentation" }, [
          H("span", { text: "step" }), H("span", { class: "evo-mech", text: "mechanism" }), H("span", { class: "evo-sum", text: "change" }),
          H("span", { text: "P(improve)" }), H("span", { class: "evo-num", text: "ΔIQM" }), H("span", { class: "evo-num evo-pass", text: "Δpass" }),
          H("span", { class: "evo-num evo-tasks", text: "+/− tasks" }), H("span", { text: "verdict" })]);
        list.appendChild(head);
        shown.forEach(function (s) {
          var row = H("div", {
            class: "evo-row", role: "listitem", tabindex: "0", "data-step": s.key, "data-to": s.to, "data-verdict": s.verdict || "",
            "aria-current": S.gen === s.to ? "true" : "false",
            "aria-label": s.key + ", " + (s.mechanism || "step") + ", " + verdictOf(s).word + (s.measurable ? ", P(improve) " + pct(s.p) : ""),
            onclick: function () { tip.hide(); select({ gen: s.to }); },
            onkeydown: function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); select({ gen: s.to }); } },
          }, [
            H("span", { class: "evo-id", text: s.key }),
            H("span", { class: "evo-mech", text: s.mechanism || "—" }),
            H("span", { class: "evo-sum", title: s.summary, text: s.summary || "—" }),
            pTrack(H, s),
            H("span", { class: "evo-num" + (isNum(s.dIqm) ? (s.dIqm > 0 ? " good" : s.dIqm < 0 ? " bad" : "") : ""), text: signed(s.dIqm) }),
            H("span", { class: "evo-num evo-pass" + (isNum(s.dPass) ? (s.dPass > 0 ? " good" : s.dPass < 0 ? " bad" : "") : ""), text: isNum(s.dPass) ? signed(s.dPass * 100, 0) : "—" }),
            H("span", { class: "evo-num evo-tasks", title: (s.gained.length ? "gained " + s.gained.map(short).join(", ") : "") + (s.regressed.length ? (s.gained.length ? " · " : "") + "regressed " + s.regressed.map(short).join(", ") : ""), text: "+" + s.gained.length + " / −" + s.regressed.length }),
            verdictSpan(H, s),
            H("span", { class: "evo-read", text: (s.summary ? s.summary + " — " : "") + (s.reading || (s.measurable ? "" : "effect not measured: " + (s.effect.reason || "no reason given"))) }),
          ]);
          row.addEventListener("pointermove", function (evt) { tip.show(evt, stepLines(s)); });
          row.addEventListener("pointerleave", tip.hide);
          row.addEventListener("focus", tip.hide);
          rows[s.key] = row;
          list.appendChild(row);
        });
        var c = m.trajectory || {};
        lede.textContent = m.steps.length + " step" + (m.steps.length === 1 ? "" : "s") + " from " + m.gens[0].id + " to " + m.last.id
          + (S.range ? ", showing " + shown.length + " in " + rangeLabel(m) : "") + ": "
          + Object.keys(VERDICT).filter(function (k) { return isNum(c[k]) && c[k]; }).map(function (k) { return c[k] + " " + k; }).join(", ")
          + (isNum(c.net_iqm_delta) ? " · net IQM " + signed(c.net_iqm_delta) : "") + (c.monotone === true ? " · every step improved" : c.monotone === false ? " · not monotone" : "") + ".";
      }
      paint();
      root.appendChild(H("p", { class: "evo-note", text: "P(improve) is the probability that a random run of the child beats a random run of the parent on the same task, its bar the bootstrap interval, the mid line a coin flip. ΔIQM is the child's IQM minus the parent's; Δpass in points of pass rate; +/− the tasks whose pass rate rose and fell — a task that fell while the average rose is forgetting. The verdict is the engine's, one per step: gamed outranks forgot, forgot outranks overfit." }));
      listen(root, function (changed) {
        if (changed.range) { paint(); return; }
        if (changed.gen) Object.keys(rows).forEach(function (k) { rows[k].setAttribute("aria-current", rows[k].getAttribute("data-to") === S.gen ? "true" : "false"); });
      });
    },
  });

  // ============================================================= evo-matrix

  function drawMatrix(host, ctx, m, tip, ref) {
    if (!d3) return;
    var W = width(host), narrow = W < 560;
    var cols = m.gens.filter(function (g) { return inRange(m, g); });
    var tasks = m.tasks;
    var labW = narrow ? 84 : Math.min(170, 16 + 6.6 * tasks.reduce(function (a, t) { return Math.max(a, short(t).length); }, 6));
    var padR = 10, headH = 34, cellH = narrow ? 16 : 20, gap = 2;
    var cellW = Math.max(12, (W - labW - padR - gap * (cols.length - 1)) / Math.max(1, cols.length));
    var H = headH + tasks.length * (cellH + gap) + 6;
    var metric = S.metric;
    var good = resolve(host, "--good", "#3f7d3f"), bad = resolve(host, "--bad", "#b03030"), mid = resolve(host, "--surface-2", "#f4f4f2");
    var maxAbs = 0;
    tasks.forEach(function (t) { cols.forEach(function (g) { var c = m.cells[t][g.id]; if (c && isNum(c.mean)) maxAbs = Math.max(maxAbs, Math.abs(c.mean)); }); });
    var color = metric === "return"
      ? d3.scaleDiverging().domain([-Math.max(1e-9, maxAbs), 0, Math.max(1e-9, maxAbs)]).interpolator(d3.piecewise(d3.interpolateLab, [bad, mid, good]))
      : d3.scaleSequential().domain([0, 1]).interpolator(d3.interpolateLab(mid, good));
    function value(c) { return !c ? null : metric === "return" ? (isNum(c.mean) ? c.mean : null) : (isNum(c.pass) ? c.pass : null); }
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("data-metric", metric)
      .attr("aria-label", tasks.length + " tasks by " + cols.length + " generations, each cell the " + (metric === "return" ? "mean return" : "pass rate") + " over that generation's runs of the task; a marked corner is a cell that fell from its left neighbour, an outlined cell a task that triggered the step into that column");
    var x = d3.scaleBand().domain(cols.map(function (g) { return g.id; })).range([labW, W - padR]).paddingInner(gap / (cellW + gap));
    var every = Math.max(1, Math.ceil(30 / x.bandwidth()));
    // the column band under the selected generation
    var bands = svg.append("g").selectAll("rect").data(cols).enter().append("rect").attr("class", "evo-col")
      .attr("data-gen", function (g) { return g.id; }).attr("x", function (g) { return x(g.id) - 1; }).attr("y", headH - 16).attr("width", x.bandwidth() + 2).attr("height", H - headH + 12)
      .attr("fill", "var(--ink)").attr("fill-opacity", 0).attr("rx", 3);
    // the headers: the generation, and the verdict of the step that made it
    var heads = svg.append("g").selectAll("g").data(cols).enter().append("g").attr("class", "evo-mhead").attr("data-gen", function (g) { return g.id; })
      .style("cursor", "pointer").on("click", function (evt, g) { select({ gen: g.id }); })
      .on("pointermove", function (evt, g) { tip.show(evt, genLines(m, g)); }).on("pointerleave", tip.hide);
    heads.append("rect").attr("x", function (g) { return x(g.id); }).attr("y", 0).attr("width", x.bandwidth()).attr("height", headH - 4).attr("fill", "transparent");
    heads.append("text").attr("class", "lab mono").attr("text-anchor", "middle").attr("x", function (g) { return x(g.id) + x.bandwidth() / 2; }).attr("y", 11)
      .text(function (g, i) { return i % every === 0 ? trunc(g.id, Math.max(2, Math.floor(x.bandwidth() / 6.5))) : ""; });
    heads.append("circle").attr("cx", function (g) { return x(g.id) + x.bandwidth() / 2; }).attr("cy", 20).attr("r", 3)
      .attr("fill", function (g) { var s = m.stepByTo[g.id]; return s ? verdictOf(s).color : "var(--surface)"; })
      .attr("stroke", function (g) { return m.stepByTo[g.id] ? "none" : "var(--ink-3)"; });
    // the cells
    tasks.forEach(function (t, ti) {
      var yy = headH + ti * (cellH + gap);
      svg.append("text").attr("class", "lab mono").attr("x", labW - 8).attr("y", yy + cellH / 2 + 4).attr("text-anchor", "end")
        .text(trunc(short(t), Math.floor((labW - 10) / 6.6))).append("title").text(t);
      var prevV = null;
      m.gens.forEach(function (g) {
        var c = m.cells[t][g.id], v = value(c);
        var fell = isNum(v) && isNum(prevV) && v < prevV - 1e-9;
        var step = m.stepByTo[g.id];
        var trig = !!(step && step.trigger.indexOf(t) >= 0);
        if (inRange(m, g)) {
          var xx = x(g.id);
          var cell = svg.append("g").attr("class", "evo-cell").attr("data-task", t).attr("data-gen", g.id).attr("data-value", isNum(v) ? v.toFixed(4) : "")
            .attr("data-fell", fell ? "1" : "0").attr("data-trigger", trig ? "1" : "0").style("cursor", "pointer");
          cell.append("rect").attr("x", xx).attr("y", yy).attr("width", x.bandwidth()).attr("height", cellH).attr("rx", 2)
            .attr("fill", isNum(v) ? color(v) : "none").attr("stroke", isNum(v) ? "none" : "var(--rule)").attr("stroke-dasharray", isNum(v) ? null : "2 2");
          if (fell) cell.append("path").attr("class", "fell").attr("d", "M" + (xx + x.bandwidth() - 1) + "," + (yy + cellH - 7) + "L" + (xx + x.bandwidth() - 1) + "," + (yy + cellH - 1) + "L" + (xx + x.bandwidth() - 7) + "," + (yy + cellH - 1) + "Z").attr("fill", "var(--bad)");
          if (trig) cell.append("rect").attr("class", "trig").attr("x", xx + 0.75).attr("y", yy + 0.75).attr("width", x.bandwidth() - 1.5).attr("height", cellH - 1.5).attr("rx", 2).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.5);
          if (x.bandwidth() >= 34 && isNum(v)) cell.append("text").attr("class", "tick").attr("x", xx + x.bandwidth() / 2).attr("y", yy + cellH / 2 + 4).attr("text-anchor", "middle").attr("pointer-events", "none")
            .attr("fill", metric === "pass" && v >= 0.6 ? "var(--bg)" : metric === "return" && maxAbs > 0 && Math.abs(v) / maxAbs >= 0.6 ? "var(--bg)" : "var(--ink-2)")
            .text(metric === "return" ? signed(v, 1) : pct(v));
          var pv = prevV, pg = g.i > 0 ? m.gens[g.i - 1] : null;
          cell.on("pointermove", function (evt) {
            tip.show(evt, [
              { b: true, text: short(t) + " · " + g.id + (step ? " · " + step.key + " " + verdictOf(step).word : " · the root") },
              c ? { mono: true, text: "pass " + pct(c.pass) + (isNum(c.passes) && isNum(c.n) ? " (" + c.passes + "/" + c.n + ")" : "") + (isNum(c.mean) ? " · mean return " + signed(c.mean) : "") + (c.source === "step" ? " · from the step's per-task rates" : "") } : { text: "not run in this generation" },
              pg && isNum(pv) ? { text: (fell ? "fell " : "moved ") + (metric === "return" ? signed(v - pv, 2) : signed((v - pv) * 100, 0) + " pts") + " from " + pg.id + (fell ? " — forgetting" : "") } : null,
              trig ? { text: "a trigger task of " + step.key + ": its episodes prompted the change" } : null,
              { text: (step ? "click to open the step" : "click to open the root") + (selectTaskable(t) ? " and this task" : "") },
            ]);
          }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); select({ gen: g.id }); selectTask(ctx, t); });
        }
        if (isNum(v)) prevV = v;
      });
    });
    function selectTaskable(t) { var ids = typeof AgentDiff.taskIds === "function" ? AgentDiff.taskIds() : []; return ids.indexOf(t) >= 0; }
    function apply(animate) {
      var dur = animate && !prefersReduced() ? DUR : 0;
      (dur ? bands.transition().duration(dur) : bands).attr("fill-opacity", function (g) { return g.id === S.gen ? 0.07 : 0; });
      heads.select("text").attr("class", function (g) { return "lab mono" + (g.id === S.gen ? " strong" : ""); });
    }
    apply(false);
    ref.apply = apply;
    ref.color = color; ref.maxAbs = maxAbs;
  }

  function legend(H, S2, m, host, refs) {
    var wrap = H("span", { class: "evo-chip evo-legend" });
    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", "0 0 120 10"); svg.setAttribute("width", "120"); svg.setAttribute("height", "10"); svg.setAttribute("role", "img");
    svg.style.display = "inline-block"; svg.style.verticalAlign = "-1px"; svg.style.width = "120px";
    var id = "evo-grad-" + Math.floor(Math.random() * 1e9);
    var defs = document.createElementNS(svgNS, "defs"), grad = document.createElementNS(svgNS, "linearGradient");
    grad.setAttribute("id", id);
    defs.appendChild(grad); svg.appendChild(defs);
    var rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", 0); rect.setAttribute("y", 0); rect.setAttribute("width", 120); rect.setAttribute("height", 10); rect.setAttribute("rx", 2); rect.setAttribute("fill", "url(#" + id + ")");
    svg.appendChild(rect);
    var lo = H("span"), hi = H("span");
    wrap.appendChild(lo); wrap.appendChild(document.createTextNode(" ")); wrap.appendChild(svg); wrap.appendChild(document.createTextNode(" ")); wrap.appendChild(hi);
    refs.legend = function () {
      if (!refs.color) return;
      var color = refs.color, ret = S.metric === "return", maxAbs = refs.maxAbs || 0;
      grad.innerHTML = "";
      for (var i = 0; i <= 10; i++) {
        var stop = document.createElementNS(svgNS, "stop");
        var v = ret ? -maxAbs + 2 * maxAbs * i / 10 : i / 10;
        stop.setAttribute("offset", (i * 10) + "%"); stop.setAttribute("stop-color", color(v));
        grad.appendChild(stop);
      }
      lo.textContent = ret ? signed(-maxAbs, 1) : "0%";
      hi.textContent = ret ? signed(maxAbs, 1) : "100%";
      svg.setAttribute("aria-label", ret ? "colour scale: mean return from " + signed(-maxAbs, 1) + " through zero to " + signed(maxAbs, 1) : "colour scale: pass rate from 0% to 100%");
    };
    return wrap;
  }

  AgentDiff.block({
    id: "evo-matrix",
    title: "Tasks × generations",
    question: "Per task, how each generation did — where a step lifted the average and sank one task, and whether the tasks that triggered a step moved more than the rest.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.tasks.length && m.gens.some(function (g) { return m.tasks.some(function (t) { return m.cells[t][g.id]; }); }) ? 0.87 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evo evo-matrix" });
      el.appendChild(root);
      var tip = tooltip(root);
      var refs = {};
      var fell = 0, trig = 0;
      m.tasks.forEach(function (t) {
        var prev = null;
        m.gens.forEach(function (g) { var c = m.cells[t][g.id]; var v = c && isNum(c.pass) ? c.pass : null; if (isNum(v) && isNum(prev) && v < prev - 1e-9) fell++; if (isNum(v)) prev = v; });
      });
      m.steps.forEach(function (s) { trig += s.trigger.length; });
      root.appendChild(H("p", { class: "evo-lede", "data-fell": fell, text:
        fell + " cell" + (fell === 1 ? "" : "s") + " fell from the generation before" + (fell ? " — each one a task a step forgot" : "") + "; "
        + trig + " outlined cell" + (trig === 1 ? "" : "s") + " triggered a step." }));
      var bar = H("div", { class: "evo-bar" });
      var btnPass = H("button", { type: "button", "data-metric": "pass", text: "pass rate", "aria-pressed": S.metric === "pass" ? "true" : "false", onclick: function () { select({ metric: "pass" }); } });
      var btnRet = H("button", { type: "button", "data-metric": "return", text: "mean return", "aria-pressed": S.metric === "return" ? "true" : "false", onclick: function () { select({ metric: "return" }); } });
      bar.appendChild(btnPass); bar.appendChild(btnRet);
      bar.appendChild(legend(H, S, m, root, refs));
      bar.appendChild(H("span", { text: "▸ corner = fell from the left · outline = trigger task of that column's step" }));
      root.appendChild(bar);
      var host = H("div", { class: "evo-chart" });
      function paint() { host.innerHTML = ""; drawMatrix(host, ctx, m, tip, refs); if (refs.legend) refs.legend(); }
      root.appendChild(responsive(host, paint, "evo-matrix"));
      root.appendChild(H("p", { class: "evo-note", text: "A cell is the task's pass rate (or mean return) over that generation's recorded runs — a count, not an estimate; a dashed cell was not run. The colour scale is sequential for pass rate and diverging around zero for return. A marked corner is a cell lower than the one to its left: forgetting, made visible. The outlined cells are the tasks whose episodes prompted the step into that column; when they improved more than the rest, the step overfit. Hover for the numbers; click to open the step (and the task, when the page has it)." }));
      listen(root, function (changed) {
        if (changed.metric) { btnPass.setAttribute("aria-pressed", S.metric === "pass" ? "true" : "false"); btnRet.setAttribute("aria-pressed", S.metric === "return" ? "true" : "false"); paint(); return; }
        if (changed.range) { paint(); return; }
        if (changed.gen && refs.apply) refs.apply(true);
      });
    },
  });

  // =============================================================== evo-step

  function diffLines(H, hunks) {
    var pre = H("pre", { class: "evo-diff" });
    (Array.isArray(hunks) ? hunks : []).forEach(function (h, hi) {
      String(h).split("\n").forEach(function (line, li) {
        if (li === 0 && hi > 0 && line === "") return;
        var cls = line.indexOf("@@") === 0 ? "hunk" : line.charAt(0) === "+" ? "add" : line.charAt(0) === "-" ? "del" : "ctx";
        pre.appendChild(H("span", { class: cls, text: line + "\n" }));
      });
    });
    return pre;
  }
  function listDiff(H, label, added, removed, changed) {
    added = Array.isArray(added) ? added : []; removed = Array.isArray(removed) ? removed : []; changed = Array.isArray(changed) ? changed : [];
    if (!added.length && !removed.length && !changed.length) return null;
    var ul = H("ul", { class: "evo-list", "data-part": label });
    function nameOf(v) { return v && typeof v === "object" ? (v.name || JSON.stringify(v)) : String(v); }
    added.forEach(function (v) { ul.appendChild(H("li", { class: "add", text: "+ " + nameOf(v) })); });
    removed.forEach(function (v) { ul.appendChild(H("li", { class: "del", text: "− " + nameOf(v) })); });
    changed.forEach(function (v) { ul.appendChild(H("li", { class: "chg", text: "~ " + nameOf(v) + " (changed)" })); });
    return ul;
  }

  function drawImprove(host, s) {
    if (!d3) return;
    var W = width(host), padL = 8, padR = 8, H = 46, y = 18;
    var x = d3.scaleLinear().domain([0, 1]).range([padL, W - padR]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "probability that a random run of " + s.to + " beats a random run of " + s.from + " on the same task: " + pct(s.p) + ", interval " + pct(s.plo) + " to " + pct(s.phi) + "; the mid line is a coin flip");
    svg.append("line").attr("class", "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y).attr("y2", y);
    svg.append("line").attr("class", "zero").attr("x1", x(0.5)).attr("x2", x(0.5)).attr("y1", y - 11).attr("y2", y + 11).attr("stroke-width", 1.5);
    svg.append("text").attr("class", "tick").attr("x", x(0.5)).attr("y", H - 4).attr("text-anchor", "middle").text("50% · a coin flip");
    svg.append("text").attr("class", "tick").attr("x", padL).attr("y", H - 4).text("0%");
    svg.append("text").attr("class", "tick").attr("x", W - padR).attr("y", H - 4).attr("text-anchor", "end").text("100%");
    var color = isNum(s.p) ? (s.p > 0.5 ? "var(--good)" : s.p < 0.5 ? "var(--bad)" : "var(--ink)") : "var(--ink-3)";
    if (isNum(s.plo) && isNum(s.phi)) svg.append("line").attr("class", "evo-pint").attr("x1", x(s.plo)).attr("x2", x(s.phi)).attr("y1", y).attr("y2", y).attr("stroke", color).attr("stroke-width", 6).attr("stroke-opacity", 0.3).attr("stroke-linecap", "round");
    if (isNum(s.p)) svg.append("circle").attr("class", "evo-ppt").attr("cx", x(s.p)).attr("cy", y).attr("r", 4.5).attr("fill", color);
  }

  function drawPerTask(host, ctx, m, s, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 420;
    var tasks = Object.keys(s.perTask);
    if (!tasks.length) return;
    tasks.sort(function (p, q) { return (s.perTask[q].delta_return || 0) - (s.perTask[p].delta_return || 0); });
    var labW = narrow ? 80 : Math.min(150, 14 + 6.6 * tasks.reduce(function (a, t) { return Math.max(a, short(t).length); }, 6));
    //: the value at the right end: the delta, and the pass rates when the width allows; the margin is measured from the longest one
    function valueText(p) { return signed(p.delta_return, 1) + (!narrow && isNum(p.pass_from) && isNum(p.pass_to) ? " · " + pct(p.pass_from) + "→" + pct(p.pass_to) : ""); }
    var rowH = 18, padT = 16, padB = 18;
    var padR = 10 + 6.4 * tasks.reduce(function (a, t) { var p = s.perTask[t]; return Math.max(a, isNum(p.delta_return) ? valueText(p).length : 8); }, 4);
    var H = padT + tasks.length * rowH + padB;
    var maxAbs = 0;
    tasks.forEach(function (t) { var d = s.perTask[t].delta_return; if (isNum(d)) maxAbs = Math.max(maxAbs, Math.abs(d)); });
    var x = d3.scaleLinear().domain([-Math.max(1e-9, maxAbs), Math.max(1e-9, maxAbs)]).nice().range([labW, W - padR]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "per task, the change in mean return from " + s.from + " to " + s.to + ", one dot per task on a shared axis with zero drawn; the " + s.trigger.length + " trigger task" + (s.trigger.length === 1 ? "" : "s") + " drawn as diamonds");
    svg.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0)).attr("y1", padT - 6).attr("y2", H - padB + 2);
    x.ticks(narrow ? 3 : 5).forEach(function (t) { svg.append("text").attr("class", "tick").attr("x", x(t)).attr("y", H - 4).attr("text-anchor", "middle").text(signed(t, 1)); });
    svg.append("text").attr("class", "lab dim").attr("x", W - padR).attr("y", 10).attr("text-anchor", "end").text("Δ mean return →");
    tasks.forEach(function (t, i) {
      var p = s.perTask[t], y = padT + i * rowH + rowH / 2;
      var trig = s.trigger.indexOf(t) >= 0;
      var polarity = isNum(p.pass_to) && isNum(p.pass_from) ? (p.pass_to > p.pass_from ? "var(--good)" : p.pass_to < p.pass_from ? "var(--bad)" : "var(--ink-2)") : "var(--ink-2)";
      var g = svg.append("g").attr("class", "evo-tdot").attr("data-task", t).attr("data-trigger", trig ? "1" : "0").attr("data-delta", isNum(p.delta_return) ? p.delta_return : "")
        .style("cursor", selectTaskable(t) ? "pointer" : "default");
      g.append("text").attr("class", "lab mono" + (trig ? " strong" : "")).attr("x", labW - 8).attr("y", y + 4).attr("text-anchor", "end").text(trunc(short(t), Math.floor((labW - 10) / 6.6)));
      if (isNum(p.delta_return)) {
        g.append("line").attr("x1", x(0)).attr("x2", x(p.delta_return)).attr("y1", y).attr("y2", y).attr("stroke", polarity).attr("stroke-opacity", 0.35).attr("stroke-width", 1.5);
        if (trig) g.append("path").attr("d", "M0,-5.5L5.5,0L0,5.5L-5.5,0Z").attr("transform", "translate(" + x(p.delta_return) + "," + y + ")").attr("fill", polarity).attr("stroke", "var(--ink)").attr("stroke-width", 1.2);
        else g.append("circle").attr("cx", x(p.delta_return)).attr("cy", y).attr("r", 4.2).attr("fill", polarity);
        g.append("text").attr("class", "tick").attr("x", W - padR + 6).attr("y", y + 4).text(valueText(p));
      } else {
        g.append("text").attr("class", "tick").attr("x", x(0) + 6).attr("y", y + 4).text("no delta");
      }
      g.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", W).attr("height", rowH).attr("fill", "transparent");
      g.on("pointermove", function (evt) {
        tip.show(evt, [{ b: true, text: short(t) + (trig ? " · trigger task" : "") },
          { mono: true, text: "Δ mean return " + signed(p.delta_return) + " · P(improve) " + pct(p.p) + " · pass " + pct(p.pass_from) + " → " + pct(p.pass_to) },
          s.regressed.indexOf(t) >= 0 ? { text: "regressed: forgetting" } : s.gained.indexOf(t) >= 0 ? { text: "gained" } : null,
          selectTaskable(t) ? { text: "click to open this task" } : null]);
      }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); selectTask(ctx, t); });
    });
    function selectTaskable(t) { var ids = typeof AgentDiff.taskIds === "function" ? AgentDiff.taskIds() : []; return ids.indexOf(t) >= 0; }
  }

  function renderStep(root, ctx, m, tip) {
    var H = ctx.h;
    root.innerHTML = "";
    var g = m.byId[S.gen], s = selectedStep(m);
    var idx = s ? m.steps.indexOf(s) : -1;
    // the step navigation
    var nav = H("div", { class: "evo-nav" }, [
      H("button", { type: "button", text: "‹ earlier", "data-nav": "prev", disabled: g.i <= 0, onclick: function () { if (g.i > 0) select({ gen: m.gens[g.i - 1].id }); } }),
      H("span", { class: "evo-chip", text: s ? "step " + (idx + 1) + " of " + m.steps.length : "the root" }),
      H("button", { type: "button", text: "later ›", "data-nav": "next", disabled: g.i >= m.gens.length - 1, onclick: function () { if (g.i < m.gens.length - 1) select({ gen: m.gens[g.i + 1].id }); } }),
    ]);
    root.appendChild(nav);
    if (!s) {
      root.appendChild(H("p", { class: "evo-lede", "data-gen": g.id }, [H("b", { text: g.id }), H("span", { text: " is the root of the lineage: no step made it. " + (g.note ? cap(g.note) + ". " : "") })]));
      root.appendChild(H("p", { class: "evo-read", text: "IQM " + num(g.point) + (isNum(g.lo) ? " [" + num(g.lo) + ", " + num(g.hi) + "]" : "") + " · pass rate " + pct(g.pass) + (isNum(g.passes) && isNum(g.n) ? " (" + g.passes + "/" + g.n + ")" : "") + " over " + (isNum(g.n) ? g.n : "?") + " episodes." }));
      root.appendChild(H("p", { class: "evo-read", text: "Artifacts: prompt " + num(g.size.prompt_chars, 0) + " chars · " + num(g.size.rules, 0) + " rules · " + num(g.size.skills, 0) + " skills · " + num(g.size.tools, 0) + " tools · " + num(g.size.memory, 0) + " memory notes · " + num(g.size.config_keys, 0) + " config keys." + (g.raw.artifacts_digest ? " Digest " + String(g.raw.artifacts_digest).slice(0, 12) + "." : "") }));
      return;
    }
    var v = verdictOf(s);
    root.appendChild(H("p", { class: "evo-lede", "data-step": s.key, "data-verdict": s.verdict || "" }, [
      H("b", { text: s.key }), H("span", { text: " · " + (s.mechanism || "step") + (s.summary ? " · " + s.summary : "") + " — " }), verdictSpan(H, s), H("span", { text: "." })]));
    if (s.reading) root.appendChild(H("p", { class: "evo-read evo-reading", text: s.reading }));
    var ev = s.raw.evidence, ec = s.raw.evidence_check;
    if (ev && (ev.summary || (Array.isArray(ev.episodes) && ev.episodes.length))) {
      root.appendChild(H("p", { class: "evo-note", "data-role": "evidence", text: "Evidence (" + (ev.source || "unknown source") + "): " + (ev.summary || "") + (Array.isArray(ev.episodes) && ev.episodes.length ? " — " + ev.episodes.length + " episode" + (ev.episodes.length === 1 ? "" : "s") + " of " + s.from + (s.trigger.length ? " on " + s.trigger.map(short).join(", ") : "") : "") + "."
        + (ec && ec.reading ? " " + cap(String(ec.reading)) + "." : ec && ec.measurable === false && ec.reason ? " Not checked: " + ec.reason + "." : "") }));
    }
    var cols = H("div", { class: "evo-cols" });
    root.appendChild(cols);
    // ---- what changed
    var left = H("div", { class: "evo-what" });
    cols.appendChild(left);
    var d = s.diff, prot = Array.isArray(d.protected_touched) ? d.protected_touched : [];
    if (prot.length) {
      var changes = Array.isArray(d.protected_changes) ? d.protected_changes : [];
      var epi = Array.isArray(s.raw.protected_episodes) ? s.raw.protected_episodes : [];
      function chg(c) { return c.path + " " + String(c.from) + " → " + String(c.to) + (c.unit ? " " + c.unit : "") + (c.direction ? " (" + c.direction + ")" : ""); }
      left.appendChild(H("p", { class: "evo-note bad", "data-role": "protected", style: { margin: "0 0 6px" },
        text: "▏Touched " + prot.length + " protected path" + (prot.length === 1 ? "" : "s") + ": " + (changes.length ? changes.map(chg).join("; ") : prot.join(", ")) + " — the agent edited what judges it."
          + (epi.length ? " In the episodes: " + epi.map(chg).join("; ") + "." : "") }));
    }
    var rest = Array.isArray(d.protected_restored) ? d.protected_restored : [];
    if (rest.length) left.appendChild(H("p", { class: "evo-note", "data-role": "restored", style: { margin: "0 0 6px", color: "var(--good)" }, text: "Restored " + rest.length + " protected path" + (rest.length === 1 ? "" : "s") + ": " + rest.map(function (r) { return typeof r === "string" ? r : chgText(r); }).join("; ") + "." }));
    function chgText(c) { return c.path + " " + String(c.from) + " → " + String(c.to); }
    var sp = d.system_prompt && typeof d.system_prompt === "object" ? d.system_prompt : null;
    var quiet = [];
    if (sp && (sp.added || sp.removed || (Array.isArray(sp.hunks) && sp.hunks.length))) {
      left.appendChild(H("div", { class: "evo-h", text: "prompt · +" + num(sp.added, 0) + " −" + num(sp.removed, 0) + " lines" }));
      if (Array.isArray(sp.hunks) && sp.hunks.length) left.appendChild(diffLines(H, sp.hunks));
      else left.appendChild(H("p", { class: "evo-note", style: { margin: 0 }, text: "the hunks were not included" }));
    } else quiet.push("prompt");
    [["rules", d.rules], ["skills", d.skills], ["tools", d.tools]].forEach(function (pair) {
      var part = pair[1] && typeof pair[1] === "object" ? pair[1] : {};
      var ul = listDiff(H, pair[0], part.added, part.removed, part.changed);
      if (!ul) { quiet.push(pair[0]); return; }
      left.appendChild(H("div", { class: "evo-h", text: pair[0] }));
      var items = ul.querySelectorAll("li");
      for (var i = 0; i < items.length; i++) {
        var nm = items[i].textContent.slice(2);
        if (prot.indexOf(pair[0] + "." + nm) >= 0) items[i].appendChild(H("span", { class: "evo-prot", text: "▏protected" }));
      }
      left.appendChild(ul);
    });
    var mem = d.memory && typeof d.memory === "object" ? d.memory : {};
    if (mem.added || mem.removed) {
      left.appendChild(H("div", { class: "evo-h", text: "memory · +" + num(mem.added, 0) + " −" + num(mem.removed, 0) + " notes" }));
      var ul2 = H("ul", { class: "evo-list", "data-part": "memory" });
      var sample = Array.isArray(mem.sample_added) ? mem.sample_added : [];
      sample.forEach(function (t) { ul2.appendChild(H("li", { class: "add", text: "+ " + String(t) })); });
      if (isNum(mem.added) && mem.added > sample.length) ul2.appendChild(H("li", { class: "quiet", text: "… and " + (mem.added - sample.length) + " more added note" + (mem.added - sample.length === 1 ? "" : "s") + " (a sample of " + sample.length + " is shown)" }));
      if (isNum(mem.removed) && mem.removed) ul2.appendChild(H("li", { class: "del", text: "− " + mem.removed + " note" + (mem.removed === 1 ? "" : "s") + " removed" }));
      left.appendChild(ul2);
    } else quiet.push("memory");
    var cfg = d.config && Array.isArray(d.config.changed) ? d.config.changed : [];
    if (cfg.length) {
      left.appendChild(H("div", { class: "evo-h", text: "config" }));
      var ul3 = H("ul", { class: "evo-list", "data-part": "config" });
      cfg.forEach(function (c) {
        var li = H("li", { class: "chg", "data-key": c.key }, [H("span", { text: String(c.key) + ": " + String(c.from) + " → " + String(c.to) })]);
        if (prot.indexOf("config." + c.key) >= 0) li.appendChild(H("span", { class: "evo-prot", text: "▏protected" }));
        ul3.appendChild(li);
      });
      left.appendChild(ul3);
    } else quiet.push("config");
    if (quiet.length) left.appendChild(H("p", { class: "evo-note", "data-role": "unchanged", text: "Unchanged: " + quiet.join(", ") + "." }));
    // ---- what it did
    var right = H("div", { class: "evo-effect" });
    cols.appendChild(right);
    var eff = s.effect;
    if (!s.measurable) {
      right.appendChild(H("div", { class: "evo-h", text: "effect" }));
      right.appendChild(H("p", { class: "evo-read", "data-role": "unmeasured", text: "The effect could not be measured: " + (eff.reason || "no reason given") + "." }));
    } else {
      right.appendChild(H("div", { class: "evo-h", text: "effect" }));
      right.appendChild(H("div", { class: "evo-figure" }, [
        H("span", { class: "evo-big", "data-p": isNum(s.p) ? s.p : "", text: "P(" + s.to + " > " + s.from + ") = " + pct(s.p) }),
        H("span", { class: "evo-ci", text: "[" + pct(s.plo) + ", " + pct(s.phi) + "]" + (s.noisy ? " · spans the coin flip" : "") })]));
      var ih = H("div", { class: "evo-chart" });
      right.appendChild(responsive(ih, function () { drawImprove(ih, s); }, "evo-step-p"));
      var iq = eff.iqm || {}, pr = eff.pass_rate || {};
      var iqText = num(iq.from) + (isNum(iq.from_lo) ? " [" + num(iq.from_lo) + ", " + num(iq.from_hi) + "]" : "") + " → " + num(iq.to) + (isNum(iq.to_lo) ? " [" + num(iq.to_lo) + ", " + num(iq.to_hi) + "]" : "");
      var prText = pct(pr.from) + (isNum(pr.passes_from) && isNum(pr.episodes_from) ? " (" + pr.passes_from + "/" + pr.episodes_from + ")" : "") + " → " + pct(pr.to) + (isNum(pr.passes_to) && isNum(pr.episodes_to) ? " (" + pr.passes_to + "/" + pr.episodes_to + ")" : "");
      right.appendChild(H("div", { class: "evo-bar", style: { margin: "6px 0 2px" } }, [
        H("span", { class: "evo-chip", title: iq.basis || "" }, [H("span", { text: "IQM " }), H("b", { text: iqText }), H("span", { text: " (" + signed(iq.delta) + ")" })]),
        H("span", { class: "evo-chip" }, [H("span", { text: "pass " }), H("b", { text: prText }), H("span", { text: " (" + pts(pr.delta) + ")" })]),
        H("span", { class: "evo-chip", text: "+" + s.gained.length + " gained · −" + s.regressed.length + " regressed" })]));
      var th = H("div", { class: "evo-chart" });
      right.appendChild(responsive(th, function () { drawPerTask(th, ctx, m, s, tip); }, "evo-step-tasks"));
    }
    // the three readings, each one sentence with its numbers
    right.appendChild(H("div", { class: "evo-h", text: "readings" }));
    function reading(kind, part, flagged, text) {
      right.appendChild(H("p", { class: "evo-read" + (flagged ? " flag" : ""), "data-kind": kind }, [H("b", { text: cap(kind) + " · " }), H("span", { text: text })]));
    }
    var of = s.overfit;
    reading("overfit", of, s.verdict === "overfit", !of ? "not measured." : of.measurable === false ? "not measured: " + (of.reason || "no reason given") + "."
      : (of.reading ? cap(of.reading) : "trigger tasks " + signed(of.trigger_delta) + ", held-out " + signed(of.held_out_delta) + ", gap " + signed(of.gap)) + (of.reading && isNum(of.gap) ? " (trigger " + signed(of.trigger_delta) + " vs held-out " + signed(of.held_out_delta) + ", gap " + signed(of.gap) + ")" : "") + ".");
    var ga = s.gaming;
    reading("gaming", ga, !!(ga && ga.flag), !ga ? "not measured." : (ga.reading ? cap(ga.reading) : "return " + signed(ga.return_delta) + ", passes " + pts(ga.pass_delta)) + (ga.reading && isNum(ga.return_delta) ? " (return " + signed(ga.return_delta) + ", passes " + pts(ga.pass_delta) + ")" : "") + ".");
    var dr = s.drift;
    reading("drift", dr, false, !dr ? "not measured." : dr.measurable === false ? "not measured: " + (dr.reason || "no reason given") + "."
      : "behaviour moved " + num(dr.between) + " between " + s.from + " and " + s.to + " (spread " + num(dr.spread_from) + " → " + num(dr.spread_to) + ")"
        + (dr.top_branch && dr.top_branch.label ? "; they first part at " + dr.top_branch.label : "") + ".");
    var adv = eff.advisory && typeof eff.advisory === "object" ? eff.advisory : null;
    var advText = adv && adv.message ? String(adv.message) : m.advisory || "";
    if (advText) right.appendChild(H("p", { class: "evo-note evo-advisory", text: advText }));
  }

  AgentDiff.block({
    id: "evo-step",
    title: "One step, in full",
    question: "For the selected step: exactly what the agent changed in itself, beside exactly what that did to its behaviour — and the three ways the change could be a lie.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { return model(ctx).ok ? 0.86 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evo evo-step" });
      el.appendChild(root);
      var tip = tooltip(root);
      var body = H("div", { class: "evo-step-body" });
      root.appendChild(body);
      renderStep(body, ctx, m, tip);
      root.appendChild(H("p", { class: "evo-note", text: "Left: the diff of the agent's artifacts from parent to child — added lines in green, removed in red, everything unchanged said once. Right: what the change did over the recorded runs, and the three readings that can turn a good-looking step into a bad one: overfit (the trigger tasks moved more than the rest), gaming (return up while passes did not follow), drift (how far the behaviour moved). Click a task to open it when the page has it." }));
      listen(root, function (changed) { if (changed.gen) renderStep(body, ctx, m, tip); });
    },
  });

  // ========================================================== evo-integrity

  function drawGrowth(host, ctx, m, key, label, values, budget, over, tip) {
    if (!d3) return;
    var W = width(host), H = 118, padL = 40, padR = 10, padT = 18, padB = 22;
    var ids = m.gens.map(function (g) { return g.id; });
    var x = d3.scalePoint().domain(ids).range([padL + 4, W - padR - 4]);
    var top = 0;
    values.forEach(function (v) { if (isNum(v)) top = Math.max(top, v); });
    if (isNum(budget)) top = Math.max(top, budget);
    var y = d3.scaleLinear().domain([0, top || 1]).nice().range([H - padB, padT]);
    var overSet = {};
    over.forEach(function (o) { if (o.what === key) overSet[o.gen] = o; });
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("data-key", key)
      .attr("aria-label", label + " per generation, " + values.map(function (v, i) { return ids[i] + " " + num(v, 0); }).join(", ") + (isNum(budget) ? "; budget " + num(budget, 0) + ", " + Object.keys(overSet).length + " over it" : "; no budget set"));
    svg.append("text").attr("class", "lab").attr("x", padL).attr("y", 11).text(label + " · " + num(values[values.length - 1], 0) + " at " + ids[ids.length - 1]);
    y.ticks(2).forEach(function (t) {
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(t) + 4).attr("text-anchor", "end").text(num(t, 0));
    });
    if (isNum(budget)) {
      svg.append("line").attr("class", "evo-budget").attr("x1", padL).attr("x2", W - padR).attr("y1", y(budget)).attr("y2", y(budget)).attr("stroke", "var(--rule-2)").attr("stroke-dasharray", "3 3");
      // the label sits at the left end, where growth has not yet reached the rule
      svg.append("text").attr("class", "tick backed").attr("x", padL + 2).attr("y", y(budget) - 3).text("budget " + num(budget, 0));
    }
    var pts = values.map(function (v, i) { return { id: ids[i], v: v, i: i }; }).filter(function (p) { return isNum(p.v); });
    var line = d3.line().x(function (p) { return x(p.id); }).y(function (p) { return y(p.v); });
    svg.append("path").attr("class", "evo-growth").attr("fill", "none").attr("stroke", "var(--ink-2)").attr("stroke-width", 1.6).attr("d", line(pts));
    var every = Math.max(1, Math.ceil(28 / Math.max(1, x.step())));
    pts.forEach(function (p) {
      var bad = !!overSet[p.id];
      svg.append("circle").attr("class", "evo-gpt").attr("data-gen", p.id).attr("data-over", bad ? "1" : "0").attr("cx", x(p.id)).attr("cy", y(p.v)).attr("r", bad ? 4.5 : 3)
        .attr("fill", bad ? "var(--bad)" : "var(--surface)").attr("stroke", bad ? "var(--bad)" : "var(--ink-2)").attr("stroke-width", 1.5).style("cursor", "pointer")
        .on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: p.id + " · " + label + " " + num(p.v, 0) + (bad ? " · over budget " + num(budget, 0) : isNum(budget) ? " · budget " + num(budget, 0) : "") }, { text: "click to open this generation" }]); })
        .on("pointerleave", tip.hide).on("click", function () { tip.hide(); select({ gen: p.id }); });
      if (p.i % every === 0 || p.i === pts.length - 1) svg.append("text").attr("class", "tick").attr("x", x(p.id)).attr("y", H - 6).attr("text-anchor", "middle").text(trunc(p.id, 5));
    });
  }

  AgentDiff.block({
    id: "evo-integrity",
    title: "Is the evolution sound?",
    question: "Did any step touch a protected component — the agent's own verifier, grader or reward config — and is the prompt or memory growing past its budget?",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && (m.integrity || m.gens.some(function (g) { return isNum(g.size.prompt_chars); })) ? 0.85 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evo evo-integrity" });
      el.appendChild(root);
      var tip = tooltip(root);
      var ig = m.integrity || {};
      var touched = Array.isArray(ig.touched) ? ig.touched : [];
      var lede = H("p", { class: "evo-lede", "data-touched": touched.length });
      //: the step a touch names: "g2 → g3", {from_gen, to_gen}, or the child's index
      function stepOfTouch(t) {
        if (t.to_gen && m.stepByTo[t.to_gen]) return m.stepByTo[t.to_gen];
        var key = String(t.step === undefined || t.step === null ? "" : t.step);
        return m.steps.filter(function (s) { return s.key === key || s.to === key || String(s.raw.index) === key || String(s.toGen.i) === key; })[0] || null;
      }
      function touchText(t) { return t.path + " " + String(t.from) + " → " + String(t.to) + (t.unit ? " " + t.unit : "") + (t.source === "episodes" ? " (seen in the episodes)" : ""); }
      //: one clause per step: "g2 → g3 weakened config.checks 5 → 0, tools.run_check present → absent"
      function clauses(list, verb) {
        var groups = [], byKey = {};
        list.forEach(function (t) {
          var s = stepOfTouch(t), k = s ? s.key : (t.from_gen && t.to_gen ? t.from_gen + " → " + t.to_gen : "step " + String(t.step));
          if (!byKey[k]) { byKey[k] = { key: k, step: s, items: [] }; groups.push(byKey[k]); }
          byKey[k].items.push(t);
        });
        return groups.map(function (g) {
          return H("span", { class: "evo-touch", "data-step": g.key, "data-paths": g.items.map(function (t) { return t.path; }).join(","), style: { cursor: g.step ? "pointer" : "default" },
            text: g.key + " " + verb + " " + g.items.map(touchText).join(", "),
            onclick: function () { if (g.step) select({ gen: g.step.to }); } });
        });
      }
      var distinct = {};
      touched.forEach(function (t) { distinct[t.path] = true; });
      var nPaths = Object.keys(distinct).length;
      if (touched.length) {
        lede.appendChild(H("b", { text: nPaths + " protected path" + (nPaths === 1 ? "" : "s") + " touched. " }));
        clauses(touched, "weakened").forEach(function (c, i, all) { lede.appendChild(c); lede.appendChild(H("span", { text: i < all.length - 1 ? "; " : ". " })); });
        var restored = Array.isArray(ig.restored) ? ig.restored : [];
        if (restored.length) {
          lede.appendChild(H("span", { class: "evo-restored", style: { color: "var(--ink-2)" } }, [H("span", { text: "Restored later: " })].concat(clauses(restored, "restored").map(function (c, i, all) { return H("span", null, [c, H("span", { text: i < all.length - 1 ? "; " : "." })]); }))));
        }
      } else {
        lede.appendChild(H("span", { text: "No protected path was touched across " + m.steps.length + " step" + (m.steps.length === 1 ? "" : "s") + (m.protected.length ? " (protected: " + m.protected.join(", ") + ")" : " — none was declared, so nothing could be caught") + "." }));
      }
      root.appendChild(lede);
      var growth = ig.growth && typeof ig.growth === "object" ? ig.growth : {};
      var over = Array.isArray(growth.over_budget) ? growth.over_budget : [];
      var multi = H("div", { class: "evo-multi" });
      root.appendChild(multi);
      [["prompt_chars", "prompt chars"], ["rules", "rules"], ["memory", "memory notes"]].forEach(function (pair) {
        var key = pair[0];
        var values = Array.isArray(growth[key]) && growth[key].length === m.gens.length ? growth[key] : m.gens.map(function (g) { return isNum(g.size[key]) ? g.size[key] : null; });
        if (!values.some(isNum)) return;
        var host = H("div", { class: "evo-chart", "data-growth": key });
        multi.appendChild(responsive(host, function () { drawGrowth(host, ctx, m, key, pair[1], values, isNum(m.budget[key]) ? m.budget[key] : null, over, tip); }, "evo-growth-" + key));
      });
      var reading = ig.reading ? cap(String(ig.reading)) : (over.length ? over.length + " generation-budget overrun" + (over.length === 1 ? "" : "s") + ": " + over.map(function (o) { return o.gen + " " + o.what + " " + num(o.value, 0) + " > " + num(o.budget, 0); }).join(", ") : "Nothing is over budget" + (Object.keys(m.budget).length ? "" : " — no budget was declared"));
      root.appendChild(H("p", { class: "evo-read evo-reading", "data-over": over.length, text: reading + (reading.slice(-1) === "." ? "" : ".") }));
      root.appendChild(H("p", { class: "evo-note", text: "Protected paths are the artifacts the lineage declared as the things that judge the agent — its checks, its verifier, its reward config; a step that changes one is an integrity finding whatever its verdict. The three lines are sizes per generation, the dashed rule the declared budget, a filled red point a generation over it." }));
    },
  });

  // ============================================================== evo-drift

  function drawDrift(host, ctx, m, tip, ref) {
    if (!d3) return;
    var dr = m.drift || {};
    var W = width(host), narrow = W < 560, H = narrow ? 170 : 200, padL = 40, padR = 12, padT = 16, padB = 26;
    var ids = m.gens.map(function (g) { return g.id; });
    var x = d3.scalePoint().domain(ids).range([padL + 6, W - padR - 6]);
    // the origin is at distance 0 from itself by definition; the engine writes null with the reason "the origin"
    var origin = (Array.isArray(dr.from_origin) ? dr.from_origin : []).map(function (p) {
      return p && p.id === m.gens[0].id && !isNum(p.distance) ? { id: p.id, distance: 0, origin: true } : p;
    }).filter(function (p) { return p && m.byId[p.id] && isNum(p.distance); });
    var consec = (Array.isArray(dr.consecutive) ? dr.consecutive : []).filter(function (p) { return p && m.byId[p.to] && isNum(p.distance); });
    if (!consec.length) consec = m.steps.filter(function (s) { return s.drift && isNum(s.drift.between); }).map(function (s) { return { from: s.from, to: s.to, distance: s.drift.between }; });
    var top = 0;
    origin.concat(consec).forEach(function (p) { top = Math.max(top, p.distance); });
    var y = d3.scaleLinear().domain([0, top || 1]).nice().range([H - padB, padT]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "behaviour distance per generation: from " + ids[0] + " (" + origin.map(function (p) { return p.id + " " + num(p.distance); }).join(", ") + ") and between consecutive generations (" + consec.map(function (p) { return p.to + " " + num(p.distance); }).join(", ") + ")");
    y.ticks(3).forEach(function (t) {
      svg.append("line").attr("class", t === 0 ? "zero" : "rule").attr("x1", padL).attr("x2", W - padR).attr("y1", y(t)).attr("y2", y(t)).attr("stroke-dasharray", t === 0 ? null : "1 3");
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(t) + 4).attr("text-anchor", "end").text(num(t));
    });
    svg.append("text").attr("class", "lab dim").attr("x", padL).attr("y", 10).text("normalised edit distance between the behaviours");
    var band = svg.append("rect").attr("y", padT - 4).attr("height", H - padB - padT + 8).attr("fill", "var(--ink)").attr("fill-opacity", 0).attr("x", padL).attr("width", 0);
    function bandTo(animate) {
      var t = animate ? band.transition().duration(DUR) : band;
      if (S.range) t.attr("x", x(ids[S.range[0]]) - 8).attr("width", x(ids[S.range[1]]) - x(ids[S.range[0]]) + 16).attr("fill-opacity", 0.05); else t.attr("fill-opacity", 0);
    }
    bandTo(false);
    var lineO = d3.line().x(function (p) { return x(p.id); }).y(function (p) { return y(p.distance); });
    var lineC = d3.line().x(function (p) { return x(p.to); }).y(function (p) { return y(p.distance); });
    if (origin.length > 1) svg.append("path").attr("class", "evo-line origin").attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.8).attr("d", lineO(origin));
    if (consec.length > 1) svg.append("path").attr("class", "evo-line consecutive").attr("fill", "none").attr("stroke", "var(--accent)").attr("stroke-width", 1.6).attr("stroke-dasharray", "4 3").attr("d", lineC(consec));
    origin.forEach(function (p) { svg.append("circle").attr("class", "evo-opt").attr("data-gen", p.id).attr("cx", x(p.id)).attr("cy", y(p.distance)).attr("r", 3.2).attr("fill", "var(--ink)"); });
    var cpts = svg.append("g").selectAll("circle").data(consec).enter().append("circle").attr("class", "evo-cpt").attr("data-to", function (p) { return p.to; })
      .attr("cx", function (p) { return x(p.to); }).attr("cy", function (p) { return y(p.distance); }).attr("r", 3.2).attr("fill", "var(--surface)").attr("stroke", "var(--accent)").attr("stroke-width", 1.6);
    var every = Math.max(1, Math.ceil(30 / Math.max(1, x.step())));
    ids.forEach(function (id, i) { if (i % every === 0 || i === ids.length - 1) svg.append("text").attr("class", "tick").attr("x", x(id)).attr("y", H - 8).attr("text-anchor", "middle").text(trunc(id, 5)); });
    // the hover: nearest generation, both values
    var tracker = svg.append("line").attr("y1", padT).attr("y2", H - padB).attr("stroke", "var(--ink-3)").attr("opacity", 0);
    var surface = svg.append("rect").attr("x", padL).attr("y", padT).attr("width", Math.max(1, W - padL - padR)).attr("height", Math.max(1, H - padT - padB)).attr("fill", "transparent").style("cursor", "pointer");
    function nearest(evt) {
      var box = svg.node().getBoundingClientRect();
      var px = (evt.clientX - box.left) * (W / Math.max(1, box.width));
      var best = 0, bd = Infinity;
      ids.forEach(function (id, i) { var d = Math.abs(x(id) - px); if (d < bd) { bd = d; best = i; } });
      return ids[best];
    }
    surface.on("pointermove", function (evt) {
      var id = nearest(evt), o = origin.filter(function (p) { return p.id === id; })[0], c = consec.filter(function (p) { return p.to === id; })[0];
      tracker.attr("x1", x(id)).attr("x2", x(id)).attr("opacity", 0.5);
      tip.show(evt, [{ b: true, text: id }, o ? { mono: true, text: "from " + ids[0] + ": " + num(o.distance) } : null, c ? { mono: true, text: c.from + " → " + c.to + ": " + num(c.distance) } : null, { text: "click to open this generation" }]);
    }).on("pointerleave", function () { tracker.attr("opacity", 0); tip.hide(); }).on("click", function (evt) { tip.hide(); select({ gen: nearest(evt) }); });
    function apply(animate) {
      var dur = animate && !prefersReduced() ? DUR : 0;
      (dur ? cpts.transition().duration(dur) : cpts).attr("r", function (p) { return p.to === S.gen ? 5.5 : 3.2; }).attr("fill", function (p) { return p.to === S.gen ? "var(--accent)" : "var(--surface)"; });
      bandTo(dur > 0);
    }
    apply(false);
    ref.apply = apply;
  }

  AgentDiff.block({
    id: "evo-drift",
    title: "How far it has moved",
    question: "How far each generation's behaviour sits from the origin, how far each step moved it, and where the selected step's behaviours first part.",
    group: "evolution",
    size: "full",
    relevance: function (ctx) { var m = model(ctx); return m.ok && ((m.drift && (Array.isArray(m.drift.from_origin) || Array.isArray(m.drift.consecutive))) || m.steps.some(function (s) { return s.drift && isNum(s.drift.between); })) ? 0.84 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      loadState(m);
      var root = H("div", { class: "evo evo-drift" });
      el.appendChild(root);
      var tip = tooltip(root);
      var refs = {};
      root.appendChild(H("div", { class: "evo-bar" }, [
        H("span", { class: "evo-chip" }, [H("i", { style: { background: "var(--ink)" } }), H("span", { text: "distance from " + m.gens[0].id })]),
        H("span", { class: "evo-chip" }, [H("i", { style: { background: "var(--accent)" } }), H("span", { text: "distance from the generation before" })]),
      ]));
      var host = H("div", { class: "evo-chart" });
      root.appendChild(responsive(host, function () { drawDrift(host, ctx, m, tip, refs); }, "evo-drift"));
      var branch = H("p", { class: "evo-read evo-branch" });
      root.appendChild(branch);
      function sentence() {
        var s = selectedStep(m);
        branch.setAttribute("data-step", s ? s.key : "");
        if (!s) { branch.textContent = m.gens[0].id + " is the origin: every distance is measured from it."; return; }
        var dr = s.drift;
        if (!dr || dr.measurable === false) { branch.textContent = s.key + ": the behaviour distance was not measured" + (dr && dr.reason ? " — " + dr.reason : "") + "."; return; }
        var tb = dr.top_branch;
        branch.innerHTML = "";
        branch.appendChild(H("b", { text: s.key + " · " }));
        branch.appendChild(H("span", { text: "the two generations' behaviours sit " + num(dr.between) + " apart (spread within " + s.from + " " + num(dr.spread_from) + ", within " + s.to + " " + num(dr.spread_to) + ")"
          + (tb && tb.label ? "; they first part at " + tb.label + (isNum(tb.episodes) ? " — " + tb.episodes + " episodes reach that step" : "") : "; no branch point was found") + "." }));
      }
      sentence();
      if (m.drift && m.drift.reading) root.appendChild(H("p", { class: "evo-read evo-reading", text: cap(String(m.drift.reading)) + (String(m.drift.reading).slice(-1) === "." ? "" : ".") }));
      root.appendChild(H("p", { class: "evo-note", text: "Distance is the normalised edit distance between two generations' step-token streams, averaged over their episodes — 0 the same behaviour, 1 nothing in common. The solid line is each generation against the origin; the dashed line is each step against its parent, so a step that changed the behaviour most stands out even when the lineage as a whole drifts slowly. A branch point is the first step at which the two generations' episodes take different paths." }));
      listen(root, function (changed) { if (refs.apply && (changed.gen || changed.range)) refs.apply(true); if (changed.gen) sentence(); });
    },
  });
})(typeof window !== "undefined" ? window : this);
