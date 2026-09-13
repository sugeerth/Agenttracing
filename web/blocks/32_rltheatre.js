/* AgentDiff blocks — the episode theatre.
 *
 * The rest of the Training view draws distributions: returns per episode,
 * rewards as a map, advantages as a histogram. None of them show an
 * episode *happening* — reward arriving step by step, the return climbing
 * or sinking, two policies on the same task coming apart at one moment.
 * These two blocks are that, overview first:
 *
 *   rl-ridgeline  every episode at once — one row per policy per task,
 *                 the episodes' cumulative-return curves overlaid with
 *                 the median solid. Click a curve to open it below.
 *   rl-theatre    two episodes, played — a scrubber over both runs'
 *                 cumulative return, their step tracks as ribbons, and
 *                 the step under the scrubber in full.
 *
 * Nothing here computes anything the engine did not: every curve is the
 * `cum` array recorded on an episode (or the running sum of its rewards
 * when the engine did not store one), every reward is a recorded reward,
 * and the per-step tool, input and labels come from a pair report when
 * one covers that run and are left blank when none does.
 *
 * Reads `aggregate.rl` (agents -> episodes, tasks -> per-policy returns
 * and delta) and, for per-step detail, `report.rl.a/b.rewards` alongside
 * `report.a/b.steps`. It writes nothing back to the engine.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;

  //: eight steps a second — fast enough to feel like motion, slow enough to read
  var STEP_MS = 125;
  //: the returns "part" when they differ by a whole point, the unit the reward is written in
  var PART_AT = 1.0;
  //: past this many episodes in one ridgeline row the curves stop being legible and become a band
  var BAND_AT = 16;
  var PREF_KEY = "agentdiff:rl-theatre";

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".rlt{position:relative}",
      ".rlt svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rlt .lab{font-size:var(--fs-xs);fill:var(--ink-2)}.rlt .lab.dim{fill:var(--ink-3)}.rlt .lab.mono{font-family:var(--mono)}",
      ".rlt .tick{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".rlt .grid{stroke:var(--rule)}.rlt .zero{stroke:var(--rule-2)}",
      ".rlt-bar{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".rlt-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px;flex:0 0 auto}",
      ".rlt-bar label{display:inline-flex;align-items:center;gap:6px;min-width:0}",
      ".rlt-bar select{font:inherit;font-size:var(--fs-xs);font-family:var(--mono);color:var(--ink);background:var(--surface);border:1px solid var(--rule-2);border-radius:6px;padding:2px 6px;max-width:100%}",
      ".rlt-btn{font:inherit;font-size:var(--fs-xs);border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);border-radius:999px;padding:2px 10px;cursor:pointer;min-height:22px}",
      ".rlt-btn:hover{color:var(--ink);background:var(--surface-2)}",
      ".rlt-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}",
      ".rlt-btn[disabled]{opacity:.45;cursor:default}",
      ".rlt-run{display:inline-flex;align-items:center;gap:5px;min-width:0}",
      ".rlt-run .rlt-btn{padding:0 7px;font-family:var(--mono)}",
      ".rlt-run b{font-weight:600;color:var(--ink);font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".rlt-now{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink-2);white-space:nowrap}",
      ".rlt-stage{position:relative;touch-action:none}",
      ".rlt-scrubber{position:absolute;inset:0;cursor:ew-resize;background:transparent;touch-action:none}",
      ".rlt-scrubber:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}",
      ".rlt-cards{display:grid;gap:8px 10px;margin-top:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}",
      ".rlt-card{min-width:0;border-left:2px solid var(--rule-2);padding:2px 0 2px 8px;font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5}",
      ".rlt-card[role=button]{cursor:pointer;border-radius:0 5px 5px 0}",
      ".rlt-card[role=button]:hover,.rlt-card[role=button]:focus-visible{background:var(--surface-2);outline:none}",
      ".rlt-card b{color:var(--ink);font-weight:600}",
      ".rlt-card .k{color:var(--ink-2);font-family:var(--mono);font-variant-numeric:tabular-nums;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".rlt-card .in{font-family:var(--mono);display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".rlt-card .ls{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".rlt-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:90ch;line-height:1.45}",
      ".rlt-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:300px}",
      ".rlt-tip b{color:var(--ink)}",
      ".rlt .hit{cursor:pointer}",
      ".rlt-cell{shape-rendering:crispEdges}",
      "@media (max-width:520px){.rlt-cards{grid-template-columns:1fr}}",
    ].join("\n");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------ helpers

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function signed(v, p) {
    if (!isNum(v)) return "—";
    var s = Math.abs(v).toFixed(p === undefined ? 2 : p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  }
  function plain(v, p) { return isNum(v) ? v.toFixed(p === undefined ? 2 : p) : "—"; }
  function short(id) { return String(id || "").replace(/^t\d+_/, "").replace(/_/g, " "); }
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  function sum(arr) { var t = 0; for (var i = 0; i < arr.length; i++) if (isNum(arr[i])) t += arr[i]; return t; }
  function mean(arr) { var xs = arr.filter(isNum); return xs.length ? sum(xs) / xs.length : null; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function width(host) { var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0; return Math.max(280, Math.min(1400, w || 320)); }
  function colorOf(side, i) { return side === "a" ? "var(--a)" : side === "b" ? "var(--b)" : i === 2 ? "var(--warn)" : "var(--ink-3)"; }
  function epKey(ep) { return ep.task_id + "|" + ep.policy + "|" + (ep.run_id || ""); }
  function prefersReduced() {
    try { return !!(global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches); }
    catch (err) { return false; }
  }
  function responsive(host, draw, k) {
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, k);
    draw(); return host;
  }
  //: the shared step inspector, when the page has one — otherwise nothing happens
  function selectStep(report, side, step) {
    var fn = AgentDiff.charts && AgentDiff.charts.selectStep;
    if (typeof fn === "function" && report && side) { try { fn(report, side, step); } catch (err) { /* the inspector is not ours to fix */ } }
  }
  function canSelectStep() { return !!(AgentDiff.charts && typeof AgentDiff.charts.selectStep === "function"); }

  /* The reader's task and run choice, per browser. The page's own store
   * degrades to memory when a file:// origin refuses localStorage, so a
   * choice never fails loudly; it just does not survive the reload. */
  function store() {
    try { return AgentDiff._internals && AgentDiff._internals.Store ? AgentDiff._internals.Store : null; }
    catch (err) { return null; }
  }
  function loadPref() {
    var s = store();
    var v = s ? s.get(PREF_KEY) : null;
    if (!v || typeof v !== "object") v = {};
    return { task: typeof v.task === "string" ? v.task : null, runs: v.runs && typeof v.runs === "object" ? v.runs : {} };
  }
  function savePref(pref) {
    var s = store();
    if (s) { try { s.set(PREF_KEY, { task: pref.task, runs: pref.runs }); } catch (err) { /* quota; the session still holds it */ } }
  }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "rlt-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        lines.forEach(function (l) {
          if (!l) return;
          var d = document.createElement("div");
          if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text;
          tip.appendChild(d);
        });
        tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 300 > r.width) x = Math.max(0, evt.clientX - r.left - 310);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  // -------------------------------------------------------------- model

  /* The episodes, keyed the way the theatre needs them: per policy, per
   * task, with the per-step detail attached when a pair report covers
   * that exact run. Built here rather than borrowed from the Training
   * blocks because a block must render on its own — but it reads the
   * same JSON, assigns sides the same way and uses the same colours, so
   * a policy is the same colour everywhere on the page. */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {}, reports = ctx.reports || [], report = ctx.report || null;
    if (cache && cache.agg === agg && cache.reports === reports && cache.report === report) return cache.m;
    var m;
    try { m = build(agg, reports, report); }
    catch (err) { console.warn("AgentDiff theatre: model failed", err); m = { policies: [], episodes: [], tasks: [], gamma: 0.99, source: null }; }
    cache = { agg: agg, reports: reports, report: report, m: m };
    return m;
  }

  function cumsum(rewards) { var out = [], acc = 0; rewards.forEach(function (r) { acc += r; out.push(acc); }); return out; }

  //: a per-step reward list from either numbers or {step, reward} entries, zero-filled to n
  function perStep(list, n) {
    var out = [];
    if (Array.isArray(list)) {
      list.forEach(function (v, i) {
        if (isNum(v)) { out[i] = v; return; }
        if (v && typeof v === "object") { var s = isNum(v.step) ? v.step : i; if (isNum(v.reward)) out[s] = v.reward; }
      });
    }
    var len = Math.max(isNum(n) ? n : 0, out.length);
    for (var i = 0; i < len; i++) if (!isNum(out[i])) out[i] = 0;
    out.length = len;
    return out;
  }

  /* Per-step detail for one run: the tool it called, the input it passed
   * and the labels the reward analysis attached. Only a pair report
   * carries this, and only for the two runs it compares, so most
   * episodes get null and the panel says the detail was not recorded. */
  function detailIndex(reports) {
    var index = {};
    reports.forEach(function (r) {
      var rl = r && r.rl; if (!rl || !r.task) return;
      ["a", "b"].forEach(function (side) {
        var run = rl[side];
        if (!run || !Array.isArray(run.rewards)) return;
        var policy = run.agent || (r[side] && r[side].agent && r[side].agent.name) || side.toUpperCase();
        var rid = r[side] && r[side].run_id !== null && r[side].run_id !== undefined ? String(r[side].run_id) : "";
        var traceSteps = r[side] && Array.isArray(r[side].steps) ? r[side].steps : [];
        var steps = [];
        run.rewards.forEach(function (e, i) {
          if (!e || typeof e !== "object") return;
          var k = isNum(e.step) ? e.step : i;
          var t = traceSteps[k] || {};
          steps[k] = {
            name: e.name || t.name || null,
            kind: e.kind || t.type || null,
            input: t.input === undefined || t.input === null ? null : String(t.input),
            labels: Array.isArray(e.labels) ? e.labels.filter(Boolean).map(String) : [],
          };
        });
        index[r.task.id + "|" + policy + "|" + rid] = { steps: steps, report: r, side: side };
      });
    });
    return index;
  }

  function episodeOf(raw, pol, source) {
    var n = isNum(raw.steps) ? raw.steps : 0;
    var rewards = perStep(raw.rewards, n);
    n = rewards.length;
    var ep = {
      policy: pol.name, side: pol.side, color: pol.color,
      task_id: String(raw.task_id || ""),
      run_id: raw.run_id === null || raw.run_id === undefined ? "" : String(raw.run_id),
      steps: n, rewards: rewards, success: !!raw.success,
      seconds: isNum(raw.seconds) ? raw.seconds : null,
      source: source,
    };
    ep.cum = Array.isArray(raw.cum) && raw.cum.length === n && raw.cum.every(isNum) ? raw.cum.slice() : cumsum(rewards);
    ep.ret = isNum(raw["return"]) ? raw["return"] : sum(rewards);
    return ep;
  }

  function build(agg, reports, report) {
    var rl = agg && agg.rl && typeof agg.rl === "object" ? agg.rl : null;
    var an = report && report.a && report.a.agent && report.a.agent.name;
    var bn = report && report.b && report.b.agent && report.b.agent.name;
    var m = { policies: [], episodes: [], tasks: [], gamma: isNum(rl && rl.gamma) ? rl.gamma : 0.99, source: null, reward_source: rl ? rl.source || null : null };
    var detail = detailIndex(reports);
    var names = [], raws = {};

    if (rl && rl.agents && typeof rl.agents === "object" && Object.keys(rl.agents).length) {
      m.source = "aggregate";
      names = Object.keys(rl.agents);
      names.forEach(function (n) {
        var ag = rl.agents[n] || {};
        raws[n] = (Array.isArray(ag.episodes) ? ag.episodes : []).filter(function (e) { return e && typeof e === "object"; });
      });
    } else {
      // no batch aggregate: one episode per policy per task, from the reports
      m.source = "reports";
      reports.forEach(function (r) {
        var prl = r && r.rl; if (!prl || !r.task) return;
        ["a", "b"].forEach(function (side) {
          var run = prl[side]; if (!run || !Array.isArray(run.rewards)) return;
          var policy = run.agent || (r[side] && r[side].agent && r[side].agent.name) || side.toUpperCase();
          if (names.indexOf(policy) < 0) { names.push(policy); raws[policy] = []; }
          if (!m.reward_source) m.reward_source = prl.source || null;
          if (isNum(prl.gamma)) m.gamma = prl.gamma;
          raws[policy].push({
            task_id: r.task.id,
            run_id: r[side] && r[side].run_id !== null && r[side].run_id !== undefined ? String(r[side].run_id) : "",
            steps: isNum(run.steps) ? run.steps : run.rewards.length,
            rewards: run.rewards, "return": run["return"], success: !!run.success, seconds: run.seconds,
          });
        });
      });
    }
    if (!names.length) return m;

    // sides: the pair the page is on keeps its colours, everything else follows
    var sideOf = {};
    names.forEach(function (n) { sideOf[n] = n === an ? "a" : n === bn ? "b" : null; });
    var free = ["a", "b"].filter(function (s) { return names.every(function (n) { return sideOf[n] !== s; }); });
    names.forEach(function (n) { if (!sideOf[n] && free.length) sideOf[n] = free.shift(); });
    var rankOf = { a: 0, b: 1 };
    names.sort(function (x, y) { return (sideOf[x] in rankOf ? rankOf[sideOf[x]] : 2) - (sideOf[y] in rankOf ? rankOf[sideOf[y]] : 2); });

    names.forEach(function (n, i) {
      var pol = { name: n, side: sideOf[n], color: colorOf(sideOf[n], i), episodes: [] };
      raws[n].forEach(function (raw) {
        var ep = episodeOf(raw, pol, m.source);
        if (!ep.steps) return;
        ep.detail = detail[epKey(ep)] || null;
        pol.episodes.push(ep);
        m.episodes.push(ep);
      });
      pol.mean_return = mean(pol.episodes.map(function (e) { return e.ret; }));
      if (pol.episodes.length) m.policies.push(pol);
    });

    // tasks in the page's order first, then whatever the aggregate names beyond it
    var order = [];
    reports.forEach(function (r) { if (r && r.task && order.indexOf(r.task.id) < 0) order.push(r.task.id); });
    m.episodes.forEach(function (e) { if (order.indexOf(e.task_id) < 0) order.push(e.task_id); });
    var rt = rl && rl.tasks && typeof rl.tasks === "object" ? rl.tasks : {};
    order.forEach(function (t) {
      var eps = m.episodes.filter(function (e) { return e.task_id === t; });
      if (!eps.length) return;
      var raw = rt[t] && typeof rt[t] === "object" ? rt[t] : null;
      var per = {};
      m.policies.forEach(function (p) {
        var src = raw && raw[p.name] && typeof raw[p.name] === "object" ? raw[p.name] : null;
        var own = eps.filter(function (e) { return e.policy === p.name; });
        per[p.name] = { mean_return: src && isNum(src.mean_return) ? src.mean_return : mean(own.map(function (e) { return e.ret; })), episodes: own };
      });
      var A = m.policies[0], B = m.policies[1];
      var delta = raw && isNum(raw.delta) ? raw.delta
        : A && B && isNum(per[B.name].mean_return) && isNum(per[A.name].mean_return) ? per[B.name].mean_return - per[A.name].mean_return : null;
      m.tasks.push({ id: t, per: per, delta: delta, episodes: eps });
    });
    return m;
  }

  /* Which task to open on.
   *
   * The obvious answer is the largest |Δ|, and that is the fallback. But
   * the largest gap is usually the least interesting one to watch: it is
   * the task the winning policy wins the way it wins everywhere. The task
   * worth playing is the *contested* one — where the policy that is ahead
   * across the batch is behind here, so the two runs really do pull apart
   * for a reason. Among contested tasks the widest gap wins; with none,
   * the widest gap overall. The bar says which rule fired. */
  function defaultTask(m) {
    var A = m.policies[0], B = m.policies[1];
    var overall = A && B && isNum(A.mean_return) && isNum(B.mean_return) ? B.mean_return - A.mean_return : 0;
    var s = overall > 0 ? 1 : overall < 0 ? -1 : 0;
    var contested = null, cAbs = -1, widest = null, wAbs = -1;
    m.tasks.forEach(function (t) {
      if (!isNum(t.delta)) return;
      var a = Math.abs(t.delta);
      if (a > wAbs) { wAbs = a; widest = t; }
      if (s && t.delta !== 0 && (t.delta > 0 ? 1 : -1) === -s && a > cAbs) { cAbs = a; contested = t; }
    });
    if (contested) { contested.__why = "contested"; return contested; }
    if (widest) { widest.__why = "widest"; return widest; }
    return m.tasks[0] || null;
  }
  //: the policy that is ahead over every episode, named so the contested rule can be said out loud
  function leader(m) {
    var best = null;
    m.policies.forEach(function (p) { if (isNum(p.mean_return) && (!best || p.mean_return > best.mean_return)) best = p; });
    return best;
  }
  function taskById(m, id) { for (var i = 0; i < m.tasks.length; i++) if (m.tasks[i].id === id) return m.tasks[i]; return null; }

  //: sorted by return, so "step to the next run" walks the policy's spread in order
  function runsOn(task, policy) {
    var eps = (task && task.per[policy] ? task.per[policy].episodes : []).slice();
    eps.sort(function (x, y) { return x.ret - y.ret || String(x.run_id).localeCompare(String(y.run_id)); });
    return eps;
  }
  /* The median episode by return: the honest representative of a policy on
   * a task. With an even count there is no middle episode, so this takes
   * the lower of the two rather than averaging two runs into one that
   * nobody ran. */
  function medianIndex(n) { return n ? (n - 1) >> 1 : 0; }

  /* The first step at which the two returns differ by a whole point,
   * counted only over steps both runs actually reached. */
  function partedAt(eps) {
    if (eps.length < 2) return null;
    var n = Math.min.apply(null, eps.map(function (e) { return e.steps; }));
    for (var i = 0; i < n; i++) {
      var lo = Infinity, hi = -Infinity;
      for (var j = 0; j < eps.length; j++) { var v = eps[j].cum[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      if (hi - lo >= PART_AT) return { step: i, spread: hi - lo };
    }
    return null;
  }

  // ------------------------------------------- the live theatre instances

  /* The ridgeline opens an episode in the theatre by talking to the
   * mounted block, not by re-rendering the page: a redraw would lose the
   * reader's scroll position and their scrub. Detached mounts are pruned
   * as they are found. */
  var MOUNTED = [];
  function mount(entry) {
    MOUNTED = MOUNTED.filter(function (e) { return e.root.isConnected; });
    MOUNTED.push(entry);
  }
  function openInTheatre(taskId, policy, runId) {
    MOUNTED = MOUNTED.filter(function (e) { return e.root.isConnected; });
    var hit = false;
    MOUNTED.forEach(function (e) { if (e.show(taskId, policy, runId)) hit = true; });
    if (!hit) {
      // the theatre is not on the page right now; leave the choice for next time
      var pref = loadPref();
      pref.task = taskId; pref.runs[policy] = runId;
      savePref(pref);
    }
    return hit;
  }

  // ----------------------------------------------------------- ridgeline

  function drawRidge(host, m, tip) {
    if (!d3) return;
    var W = width(host), narrow = W < 520;
    var gutter = narrow ? 30 : 40, padR = narrow ? 8 : 14;
    var LABH = 14, TOP = 4;

    var rows = [];
    m.tasks.forEach(function (t) {
      m.policies.forEach(function (p) {
        var eps = runsOn(t, p.name);
        if (eps.length) rows.push({ task: t, policy: p, eps: eps });
      });
    });
    if (!rows.length) return;
    var ROW = narrow ? 34 : 40;
    //: the policies of one task sit close together, tasks are held apart — the comparison is within a task
    var GAP_IN = 5, GAP_OUT = 15;

    var lo = Infinity, hi = -Infinity, maxSteps = 1;
    m.episodes.forEach(function (e) {
      maxSteps = Math.max(maxSteps, e.steps);
      e.cum.forEach(function (v) { if (v < lo) lo = v; if (v > hi) hi = v; });
    });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    lo = Math.min(lo, 0); hi = Math.max(hi, 0);
    if (lo === hi) { lo -= 1; hi += 1; }

    var tops = [], at = TOP;
    rows.forEach(function (row, ri) {
      tops.push(at);
      at += LABH + ROW + (ri + 1 < rows.length && rows[ri + 1].task === row.task ? GAP_IN : GAP_OUT);
    });
    var Hh = at;
    var x = d3.scaleLinear().domain([0, Math.max(1, maxSteps - 1)]).range([gutter, W - padR]);

    var svg = d3.select(host).append("svg")
      .attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "every episode's cumulative return, one row per policy per task over " + m.tasks.length +
        " tasks; each row's episodes overlaid with the median solid, all rows on one return axis from " +
        plain(lo, 1) + " to " + plain(hi, 1));

    rows.forEach(function (row, ri) {
      var top = tops[ri];
      var y = d3.scaleLinear().domain([lo, hi]).range([top + LABH + ROW, top + LABH]);
      var col = row.policy.color;
      var solved = row.eps.filter(function (e) { return e.success; }).length;
      var tm = row.task.per[row.policy.name] ? row.task.per[row.policy.name].mean_return : null;

      // the label is cut to the drawing's own width, so a phone clips nothing
      var budget = Math.max(12, Math.floor((W - padR - gutter) / 6.4));
      var who = trunc(row.policy.name, Math.max(8, budget - 14));
      var rest = narrow
        ? " · " + trunc(short(row.task.id), 13) + " · " + signed(tm) + " · " + solved + "/" + row.eps.length
        : " · " + short(row.task.id) + " · n=" + row.eps.length + " · mean " + signed(tm) + " · " + solved + "/" + row.eps.length + " solved";
      var head = svg.append("text").attr("class", "lab").attr("x", gutter).attr("y", top + 10);
      head.append("tspan").attr("fill", col).attr("font-weight", "600").text(who);
      head.append("tspan").attr("class", "dim").attr("fill", "var(--ink-3)").text(trunc(rest, Math.max(6, budget - who.length)));
      head.append("title").text(row.policy.name + " · " + short(row.task.id) + " · " + row.eps.length +
        " episodes · mean return " + signed(tm) + " · " + solved + " of " + row.eps.length + " solved");

      // the shared zero line, drawn in every row so the eye can land on it
      svg.append("line").attr("class", "zero").attr("x1", gutter).attr("x2", W - padR).attr("y1", y(0)).attr("y2", y(0));
      svg.append("text").attr("class", "tick").attr("x", gutter - 4).attr("y", y(0) + 4).attr("text-anchor", "end").text("0");

      var med = row.eps[medianIndex(row.eps.length)];

      function path(ep) {
        return ep.cum.map(function (v, i) { return (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); }).join(" ");
      }

      if (row.eps.length > BAND_AT) {
        // too many curves to read: the envelope at each step, over the
        // episodes that actually reach that step, with the median over it
        var area = [], back = [];
        for (var i = 0; i < maxSteps; i++) {
          var alive = row.eps.filter(function (e) { return i < e.steps; });
          if (!alive.length) break;
          var vs = alive.map(function (e) { return e.cum[i]; });
          area.push(x(i).toFixed(1) + "," + y(Math.max.apply(null, vs)).toFixed(1));
          back.unshift(x(i).toFixed(1) + "," + y(Math.min.apply(null, vs)).toFixed(1));
        }
        if (area.length) {
          svg.append("path").attr("class", "rlr-band").attr("data-policy", row.policy.name).attr("data-task", row.task.id)
            .attr("d", "M" + area.concat(back).join("L") + "Z").attr("fill", col).attr("fill-opacity", 0.14).attr("stroke", "none");
        }
      } else {
        row.eps.forEach(function (ep) {
          if (ep === med) return;
          svg.append("path").attr("class", "rlr-line").attr("data-policy", ep.policy).attr("data-task", ep.task_id).attr("data-run", ep.run_id)
            .attr("fill", "none").attr("stroke", col).attr("stroke-opacity", 0.4).attr("stroke-width", 1)
            .attr("stroke-dasharray", ep.success ? null : "3 2").attr("d", path(ep));
        });
      }

      if (med) {
        svg.append("path").attr("class", "rlr-median").attr("data-policy", med.policy).attr("data-task", med.task_id).attr("data-run", med.run_id)
          .attr("fill", "none").attr("stroke", col).attr("stroke-width", 1.8)
          .attr("stroke-dasharray", med.success ? null : "3 2").attr("d", path(med));
      }

      // a transparent hit path per episode: a 1px line is not a target
      (row.eps.length > BAND_AT ? [med] : row.eps).forEach(function (ep) {
        if (!ep) return;
        svg.append("path").attr("class", "rlr-hit hit").attr("data-policy", ep.policy).attr("data-task", ep.task_id).attr("data-run", ep.run_id)
          .attr("fill", "none").attr("stroke", "transparent").attr("stroke-width", 9).attr("d", path(ep))
          .on("pointermove", function (evt) {
            tip.show(evt, [
              { b: true, text: ep.policy + " · " + short(ep.task_id) + (ep.run_id ? " · " + ep.run_id : "") },
              { text: "return " + signed(ep.ret) + " · " + ep.steps + " steps · " + (ep.success ? "solved" : "failed") + (ep === med ? " · median" : "") },
              { text: "click to play this episode below" },
            ]);
          })
          .on("pointerleave", tip.hide)
          .on("click", function () { tip.hide(); openInTheatre(ep.task_id, ep.policy, ep.run_id); });
      });
    });
  }

  AgentDiff.block({
    id: "rl-ridgeline",
    title: "Every episode",
    question: "One row per policy per task: every episode's cumulative return overlaid, the median solid — the overview the theatre is the detail of.",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var m = model(ctx);
      return m.episodes.length && m.tasks.length ? 0.785 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rlt rlt-ridge" });
      el.appendChild(root);
      var tip = tooltip(root);

      var bar = H("div", { class: "rlt-bar" });
      m.policies.forEach(function (p) {
        bar.appendChild(H("span", { class: "rlt-now" }, [
          H("i", { style: { background: p.color } }),
          H("b", { text: p.name }),
          H("span", { text: " · mean " + signed(p.mean_return) + " · n=" + p.episodes.length }),
        ]));
      });
      bar.appendChild(H("span", { text: "solid = solved · dashed = failed" }));
      root.appendChild(bar);

      var host = H("div", { class: "rlt-stage" });
      root.appendChild(responsive(host, function () { drawRidge(host, m, tip); }, "rl-ridgeline"));

      // said from the data rather than from the draw, so the note is right before the first paint
      var lo = Infinity, hi = -Infinity;
      m.episodes.forEach(function (e) { e.cum.forEach(function (v) { if (v < lo) lo = v; if (v > hi) hi = v; }); });
      lo = Math.min(isFinite(lo) ? lo : 0, 0); hi = Math.max(isFinite(hi) ? hi : 1, 0);
      var banded = m.tasks.some(function (t) {
        return m.policies.some(function (p) { return runsOn(t, p.name).length > BAND_AT; });
      });
      root.appendChild(H("p", { class: "rlt-note", text:
        "x = step index, y = cumulative return, every row on the same return axis from " + plain(lo, 1) + " to " + plain(hi, 1) +
        " and on the same step axis, so a short task's curves stop early. " +
        (banded
          ? "Rows with more than " + BAND_AT + " episodes are drawn as a band from the lowest to the highest return at each step, with the median episode over it; the band at step k covers only the episodes that reach step k, and the median is the curve you can click."
          : "Each row draws every episode; the median episode by return is the solid heavy line.") +
        " Click any curve to play that episode in the theatre below." +
        (m.reward_source ? " Rewards " + m.reward_source + "." : "") }));
    },
  });

  // ------------------------------------------------------------- theatre

  function tsLabel(ep, i) {
    if (!ep || i >= ep.steps) return "ended";
    var d = ep.detail && ep.detail.steps[i];
    var what = d && d.name ? d.name : d && d.kind ? d.kind : "step " + i;
    return what + " " + signed(ep.rewards[i]);
  }

  function drawStage(host, ctx, m, state, paintRef) {
    if (!d3) return;
    var eps = state.eps;
    if (!eps.length) return;
    var W = width(host), narrow = W < 520;
    //: the top band is the parting label's lane, so it never lands on a curve
    var padL = narrow ? 34 : 48, padR = narrow ? 10 : 16, padT = 24, padB = 6;
    var chartH = Math.max(118, Math.min(210, Math.round(W * 0.26)));
    var LABH = 13, RIB = 11, GAP = 9;
    var y0 = padT, y1 = padT + chartH;
    var trackTop = y1 + 20;
    var Hh = trackTop + eps.length * (LABH + RIB + GAP) + padB;
    var N = state.n;

    var lo = Infinity, hi = -Infinity, maxAbs = 0;
    eps.forEach(function (e) {
      e.cum.forEach(function (v) { if (v < lo) lo = v; if (v > hi) hi = v; });
      e.rewards.forEach(function (r) { maxAbs = Math.max(maxAbs, Math.abs(r)); });
    });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    lo = Math.min(lo, 0); hi = Math.max(hi, 0);
    if (lo === hi) { lo -= 1; hi += 1; }

    var x = d3.scaleLinear().domain([0, Math.max(1, N - 1)]).range([padL, W - padR]);
    var y = d3.scaleLinear().domain([lo, hi]).nice(4).range([y1, y0]);
    var cw = (W - padR - padL) / Math.max(1, N);
    var BAR = Math.min(18, chartH * 0.18);

    var svg = d3.select(host).append("svg")
      .attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "cumulative return of " + eps.map(function (e) { return e.policy + " run " + (e.run_id || "1"); }).join(" and ") +
        " on " + short(state.task.id) + ", drawn up to the scrub position, with each run's steps as a ribbon below");

    // the return axis: the zero line carries the meaning, the rest is faint
    y.ticks(4).forEach(function (v) {
      svg.append("line").attr("class", v === 0 ? "zero" : "grid").attr("x1", padL).attr("x2", W - padR).attr("y1", y(v)).attr("y2", y(v))
        .attr("stroke-opacity", v === 0 ? 1 : 0.5);
      svg.append("text").attr("class", "tick").attr("x", padL - 5).attr("y", y(v) + 4).attr("text-anchor", "end").text(signed(v, 0));
    });

    function pathTo(ep, upto) {
      var n = Math.min(upto + 1, ep.steps);
      if (n <= 0) return "";
      var d = "";
      for (var i = 0; i < n; i++) d += (i ? "L" : "M") + x(i).toFixed(1) + "," + y(ep.cum[i]).toFixed(1);
      return d;
    }

    var lines = eps.map(function (ep) {
      svg.append("path").attr("class", "rlt-ghost").attr("data-policy", ep.policy).attr("fill", "none")
        .attr("stroke", ep.color).attr("stroke-opacity", 0.2).attr("stroke-width", 1.2)
        .attr("stroke-dasharray", ep.success ? null : "3 2")
        .attr("d", pathTo(ep, ep.steps - 1));
      return svg.append("path").attr("class", "rlt-played").attr("data-policy", ep.policy).attr("data-run", ep.run_id)
        .attr("fill", "none").attr("stroke", ep.color).attr("stroke-width", 1.9)
        .attr("stroke-dasharray", ep.success ? null : "3 2").attr("d", "");
    });

    // where the returns first parted by a whole point: ringed, and left ringed
    var part = state.part;
    if (part) {
      var vs = eps.map(function (e) { return e.cum[part.step]; });
      var pLo = Math.min.apply(null, vs), pHi = Math.max.apply(null, vs);
      var g = svg.append("g").attr("class", "rlt-part").attr("data-step", part.step);
      g.append("line").attr("x1", x(part.step)).attr("x2", x(part.step)).attr("y1", y(pHi)).attr("y2", y(pLo))
        .attr("stroke", "var(--warn)").attr("stroke-width", 1).attr("stroke-opacity", 0.8);
      vs.forEach(function (v) {
        g.append("circle").attr("cx", x(part.step)).attr("cy", y(v)).attr("r", 5.5)
          .attr("fill", "none").attr("stroke", "var(--warn)").attr("stroke-width", 1.3);
      });
      // the label rides at the top of the plot, out of the way of the curves and the scrub marks
      var right = x(part.step) > W * 0.5;
      g.append("line").attr("x1", x(part.step)).attr("x2", x(part.step)).attr("y1", y0 - 6).attr("y2", y(pHi) - 7)
        .attr("stroke", "var(--warn)").attr("stroke-width", 1).attr("stroke-opacity", 0.35).attr("stroke-dasharray", "2 3");
      g.append("text").attr("class", "lab").style("fill", "var(--warn)")
        .attr("x", clamp(x(part.step) + (right ? -7 : 7), padL, W - padR))
        .attr("y", y0 - 10)
        .attr("text-anchor", right ? "end" : "start")
        .text(narrow ? "part · step " + part.step : "returns part here · step " + part.step + " · " + plain(part.spread, 1) + " apart");
      g.append("title").text("the first step at which the returns differ by " + PART_AT.toFixed(1) + " or more: step " + part.step + ", " + plain(part.spread, 2) + " apart");
    }

    // the step ribbons: one cell per step, reward sign and intensity
    var tracks = eps.map(function (ep, ei) {
      var top = trackTop + ei * (LABH + RIB + GAP);
      var g = svg.append("g").attr("class", "rlt-track").attr("data-policy", ep.policy).attr("data-run", ep.run_id).attr("data-steps", ep.steps);
      var budget = Math.max(12, Math.floor((W - padR - padL) / 6.4));
      var who = trunc(ep.policy, Math.max(8, budget - 16));
      var rest = " · " + (ep.run_id || "run 1") + " · " + ep.steps + " steps · return " + signed(ep.ret) + " · " + (ep.success ? "solved" : "failed");
      var t = g.append("text").attr("class", "lab").attr("x", padL).attr("y", top + 9);
      t.append("tspan").style("fill", ep.color).attr("font-weight", "600").text(who);
      t.append("tspan").style("fill", "var(--ink-3)").text(trunc(rest, Math.max(6, budget - who.length)));
      t.append("title").text(ep.policy + " · " + (ep.run_id || "run 1") + " · " + ep.steps + " steps · return " + signed(ep.ret) + " · " + (ep.success ? "solved" : "failed"));
      var ribY = top + LABH;
      // the run's own extent: it stops where the run stopped
      g.append("rect").attr("x", padL).attr("y", ribY).attr("width", Math.max(1, cw * ep.steps)).attr("height", RIB)
        .attr("fill", "var(--surface-2)");
      for (var i = 0; i < ep.steps; i++) {
        var r = ep.rewards[i];
        if (r === 0) continue;
        var op = maxAbs > 0 ? 0.28 + 0.72 * Math.abs(r) / maxAbs : 0.8;
        g.append("rect").attr("class", "rlt-cell").attr("data-step", i).attr("data-reward", r)
          .attr("x", padL + i * cw).attr("y", ribY).attr("width", Math.max(0.8, cw - (cw > 3 ? 0.7 : 0))).attr("height", RIB)
          .attr("fill", r > 0 ? "var(--good)" : "var(--bad)").attr("fill-opacity", op);
      }
      // the end of the run, said rather than padded
      g.append("line").attr("class", "rlt-end").attr("x1", padL + cw * ep.steps).attr("x2", padL + cw * ep.steps)
        .attr("y1", ribY - 2).attr("y2", ribY + RIB + 2).attr("stroke", ep.color).attr("stroke-width", 1.2);
      var mark = g.append("rect").attr("class", "rlt-cellnow").attr("y", ribY - 2).attr("height", RIB + 4)
        .attr("width", Math.max(2, cw)).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1).attr("x", padL).attr("opacity", 0);
      return { ep: ep, top: top, ribY: ribY, mark: mark };
    });

    var scrub = svg.append("line").attr("class", "rlt-scrubline").attr("y1", y0).attr("y2", Hh - padB)
      .attr("stroke", "var(--ink)").attr("stroke-opacity", 0.45).attr("stroke-width", 1);
    var now = svg.append("g").attr("class", "rlt-nowlayer");

    state.geom = { W: W, padL: padL, padR: padR, x: x, cw: cw };

    paintRef.paint = function (pos) {
      var px = x(pos);
      scrub.attr("x1", px).attr("x2", px);
      eps.forEach(function (ep, i) { lines[i].attr("d", pathTo(ep, pos)); });
      tracks.forEach(function (tr) {
        if (pos < tr.ep.steps) tr.mark.attr("x", padL + pos * cw).attr("opacity", 1);
        else tr.mark.attr("opacity", 0);
      });
      now.selectAll("*").remove();
      // the two marks, labelled away from each other so neither is covered
      var live = [];
      eps.forEach(function (ep) { if (pos < ep.steps) live.push(ep); });
      live.sort(function (p, q) { return y(p.cum[pos]) - y(q.cum[pos]); });
      live.forEach(function (ep, k) {
        var cy = y(ep.cum[pos]), r = ep.rewards[pos];
        if (r !== 0 && maxAbs > 0) {
          /* Square-rooted, and the note says so. These rewards span two
           * orders of magnitude — a −0.1 step against a −10 wrong answer —
           * and a linear bar renders every ordinary step as nothing at all.
           * The root keeps the order intact and keeps the small ones seen. */
          var hgt = Math.max(1.5, BAR * Math.sqrt(Math.abs(r) / maxAbs));
          now.append("rect").attr("class", "rlt-rbar").attr("x", px - 1.6).attr("width", 3.2)
            .attr("y", r > 0 ? cy - hgt : cy).attr("height", hgt)
            .attr("fill", r > 0 ? "var(--good)" : "var(--bad)").attr("fill-opacity", 0.85);
        }
        now.append("circle").attr("class", "rlt-mark").attr("data-policy", ep.policy).attr("cx", px).attr("cy", cy).attr("r", 3.6)
          .attr("fill", ep.color).attr("stroke", "var(--surface)").attr("stroke-width", 1.2);
        var right = px > W * 0.58;
        now.append("text").attr("class", "lab mono").style("fill", ep.color)
          .attr("x", px + (right ? -7 : 7)).attr("y", cy + (k === 0 ? -8 : 15))
          .attr("text-anchor", right ? "end" : "start")
          .text(trunc(tsLabel(ep, pos), narrow ? 16 : 28));
      });
    };
    paintRef.paint(state.pos);
  }

  function cardFor(H, ep, pos) {
    var ended = pos >= ep.steps;
    var d = !ended && ep.detail ? ep.detail.steps[pos] : null;
    var actionable = !ended && ep.detail && canSelectStep();
    var attrs = { class: "rlt-card", "data-policy": ep.policy, "data-run": ep.run_id, style: { borderLeftColor: ep.color } };
    if (actionable) {
      attrs.role = "button";
      attrs.tabindex = "0";
      attrs["aria-label"] = "open step " + pos + " of " + ep.policy + " " + (ep.run_id || "run 1") + " in the step inspector";
    }
    var kids = [
      H("div", {}, [H("i", { class: "dot", style: { display: "inline-block", width: "8px", height: "8px", borderRadius: "50%", background: ep.color, marginRight: "5px" } }),
        H("b", { text: ep.policy }), H("span", { text: " · " + (ep.run_id || "run 1") })]),
    ];
    if (ended) {
      kids.push(H("span", { class: "k", text: "ended at step " + (ep.steps - 1) }));
      kids.push(H("span", { class: "ls", text: "return " + signed(ep.ret) + " · " + (ep.success ? "solved" : "failed") }));
    } else {
      kids.push(H("span", { class: "k", text: "step " + pos + (d && d.name ? " · " + d.name : d && d.kind ? " · " + d.kind : "") }));
      if (d && d.input) kids.push(H("span", { class: "in", text: trunc(d.input.replace(/\s+/g, " "), 64) }));
      kids.push(H("span", { class: "k", text: "reward " + signed(ep.rewards[pos]) + " · return " + signed(ep.cum[pos]) }));
      if (d && d.labels.length) kids.push(H("span", { class: "ls", text: d.labels.join(", ").replace(/_/g, " ") }));
      if (!ep.detail) kids.push(H("span", { class: "ls", text: "no per-step detail recorded for this run" }));
    }
    var card = H("div", attrs, kids);
    if (actionable) {
      var open = function () { selectStep(ep.detail.report, ep.detail.side, pos); };
      card.addEventListener("click", open);
      card.addEventListener("keydown", function (evt) {
        if (evt.key === "Enter" || evt.key === " " || evt.key === "Spacebar") { evt.preventDefault(); open(); }
      });
    }
    return card;
  }

  AgentDiff.block({
    id: "rl-theatre",
    title: "Episode theatre",
    question: "One episode per policy on a task, played step by step: where the reward lands, where the returns come apart.",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var m = model(ctx);
      return m.episodes.length && m.tasks.length ? 0.782 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rlt rlt-theatre" });
      el.appendChild(root);

      var pref = loadPref();
      var reduced = prefersReduced();
      var auto = defaultTask(m), lead = leader(m);
      var state = {
        task: taskById(m, pref.task) || auto,
        chosen: {},          // policy name -> run_id
        eps: [], n: 1, pos: 0, part: null,
        playing: false, timer: null, geom: null,
      };

      var paintRef = { paint: function () {} };

      function pickEpisodes() {
        state.eps = [];
        m.policies.forEach(function (p) {
          var runs = runsOn(state.task, p.name);
          if (!runs.length) return;
          var want = state.chosen[p.name] !== undefined ? state.chosen[p.name] : pref.runs[p.name];
          var at = -1;
          if (want !== undefined && want !== null) {
            for (var i = 0; i < runs.length; i++) if (runs[i].run_id === String(want)) { at = i; break; }
          }
          if (at < 0) at = medianIndex(runs.length);
          state.chosen[p.name] = runs[at].run_id;
          runs[at].__at = at; runs[at].__of = runs.length;
          state.eps.push(runs[at]);
        });
        state.n = Math.max(1, state.eps.reduce(function (a, e) { return Math.max(a, e.steps); }, 1));
        state.pos = clamp(state.pos, 0, state.n - 1);
        state.part = partedAt(state.eps);
        state.maxAbs = state.eps.reduce(function (a, e) {
          return e.rewards.reduce(function (b, r) { return Math.max(b, Math.abs(r)); }, a);
        }, 0);
      }

      // ------------------------------------------------------- controls

      var bar = H("div", { class: "rlt-bar" });
      root.appendChild(bar);

      var sel = H("select", { "aria-label": "Task to play" });
      m.tasks.forEach(function (t) {
        sel.appendChild(H("option", { value: t.id, text: trunc(short(t.id), 34) + (isNum(t.delta) ? " · Δ " + signed(t.delta, 1) : "") }));
      });
      sel.addEventListener("change", function () {
        var t = taskById(m, sel.value);
        if (!t) return;
        stop();
        state.task = t; state.chosen = {}; state.pos = 0;
        pref.task = t.id; pref.runs = {};
        rebuild();
      });
      bar.appendChild(H("label", {}, [H("span", { text: "task" }), sel]));

      var playBtn = H("button", { class: "rlt-btn", type: "button", "aria-label": "Play the episode", "aria-pressed": "false", text: "▶ play" });
      playBtn.addEventListener("click", function () { if (state.playing) stop(); else play(); });
      bar.appendChild(playBtn);

      var readout = H("span", { class: "rlt-now" });
      bar.appendChild(readout);

      var why = H("span", {});
      bar.appendChild(why);

      var runsBar = H("div", { class: "rlt-bar" });
      root.appendChild(runsBar);

      // ---------------------------------------------------------- stage

      var stage = H("div", { class: "rlt-stage" });
      var svgHost = H("div", {});
      var scrubber = H("div", {
        class: "rlt-scrubber", role: "slider", tabindex: "0",
        "aria-label": "Episode position: the step both runs are shown at",
        "aria-orientation": "horizontal", "aria-valuemin": "0", "aria-valuemax": "0", "aria-valuenow": "0", "aria-valuetext": "step 0",
      });
      stage.appendChild(svgHost);
      stage.appendChild(scrubber);
      root.appendChild(stage);

      var cards = H("div", { class: "rlt-cards" });
      root.appendChild(cards);

      var note = H("p", { class: "rlt-note" });
      root.appendChild(note);

      // ------------------------------------------------------- painting

      function valueText() {
        var parts = ["step " + state.pos + " of " + (state.n - 1)];
        state.eps.forEach(function (ep) {
          if (state.pos >= ep.steps) { parts.push(ep.policy + " ended at step " + (ep.steps - 1) + ", return " + signed(ep.ret)); return; }
          var d = ep.detail && ep.detail.steps[state.pos];
          parts.push(ep.policy + " " + (d && d.name ? d.name : "step " + state.pos) +
            ", reward " + signed(ep.rewards[state.pos]) + ", return " + signed(ep.cum[state.pos]));
        });
        return parts.join(" · ");
      }

      function paint() {
        try { paintRef.paint(state.pos); } catch (err) { /* the stage may be mid-redraw */ }
        scrubber.setAttribute("aria-valuemin", "0");
        scrubber.setAttribute("aria-valuemax", String(state.n - 1));
        scrubber.setAttribute("aria-valuenow", String(state.pos));
        scrubber.setAttribute("aria-valuetext", valueText());
        readout.textContent = "step " + state.pos + " / " + (state.n - 1);
        cards.innerHTML = "";
        state.eps.forEach(function (ep) { cards.appendChild(cardFor(H, ep, state.pos)); });
      }

      function setPos(v) {
        v = clamp(Math.round(v), 0, state.n - 1);
        if (v === state.pos) return;
        state.pos = v;
        paint();
      }

      // -------------------------------------------------------- playing

      function stop() {
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
        state.playing = false;
        playBtn.textContent = "▶ play";
        playBtn.setAttribute("aria-pressed", "false");
        playBtn.setAttribute("aria-label", "Play the episode");
      }
      function play() {
        if (state.playing) return;
        if (state.pos >= state.n - 1) state.pos = 0;
        state.playing = true;
        playBtn.textContent = "❚❚ pause";
        playBtn.setAttribute("aria-pressed", "true");
        playBtn.setAttribute("aria-label", "Pause the episode");
        paint();
        state.timer = setInterval(function () {
          if (!root.isConnected) { stop(); return; }
          if (state.pos >= state.n - 1) { stop(); return; }
          state.pos += 1;
          paint();
        }, STEP_MS);
      }

      // -------------------------------------------------------- pointer

      function posFromEvent(evt) {
        var g = state.geom;
        if (!g) return state.pos;
        var r = stage.getBoundingClientRect();
        if (!r.width) return state.pos;
        var scale = g.W / r.width;
        var px = (evt.clientX - r.left) * scale;
        var lo = g.x.range()[0], hi = g.x.range()[1];
        if (hi <= lo) return 0;
        return Math.round((clamp(px, lo, hi) - lo) / (hi - lo) * (state.n - 1));
      }
      var dragging = false;
      scrubber.addEventListener("pointerdown", function (evt) {
        dragging = true; stop();
        try { scrubber.setPointerCapture(evt.pointerId); } catch (err) { /* older pointer stacks */ }
        setPos(posFromEvent(evt));
        scrubber.focus();
        evt.preventDefault();
      });
      scrubber.addEventListener("pointermove", function (evt) { if (dragging) setPos(posFromEvent(evt)); });
      scrubber.addEventListener("pointerup", function (evt) {
        dragging = false;
        try { scrubber.releasePointerCapture(evt.pointerId); } catch (err) { /* already released */ }
      });
      scrubber.addEventListener("pointercancel", function () { dragging = false; });

      // ------------------------------------------------------- keyboard

      scrubber.addEventListener("keydown", function (evt) {
        var jump = evt.shiftKey ? 10 : 1, handled = true;
        switch (evt.key) {
          case "ArrowLeft": case "Left": stop(); setPos(state.pos - jump); break;
          case "ArrowRight": case "Right": stop(); setPos(state.pos + jump); break;
          case "ArrowDown": case "Down": stop(); setPos(state.pos - jump); break;
          case "ArrowUp": case "Up": stop(); setPos(state.pos + jump); break;
          case "PageDown": stop(); setPos(state.pos - 10); break;
          case "PageUp": stop(); setPos(state.pos + 10); break;
          case "Home": stop(); setPos(0); break;
          case "End": stop(); setPos(state.n - 1); break;
          case " ": case "Spacebar": case "Space":
            if (state.playing) stop(); else play();
            break;
          default: handled = false;
        }
        if (handled) { evt.preventDefault(); evt.stopPropagation(); }
      });

      // -------------------------------------------------------- rebuild

      function runStepper(p) {
        var runs = runsOn(state.task, p.name);
        var ep = null, at = 0;
        for (var i = 0; i < runs.length; i++) if (runs[i].run_id === state.chosen[p.name]) { ep = runs[i]; at = i; }
        if (!ep) return null;
        function go(delta) {
          stop();
          var next = runs[(at + delta + runs.length) % runs.length];
          state.chosen[p.name] = next.run_id;
          pref.runs[p.name] = next.run_id;
          pref.task = state.task.id;
          savePref(pref);
          rebuild();
        }
        var prev = H("button", { class: "rlt-btn", type: "button", "aria-label": "Previous episode of " + p.name + " on this task", text: "◂" });
        var next = H("button", { class: "rlt-btn", type: "button", "aria-label": "Next episode of " + p.name + " on this task", text: "▸" });
        prev.addEventListener("click", function () { go(-1); });
        next.addEventListener("click", function () { go(1); });
        if (runs.length < 2) { prev.disabled = true; next.disabled = true; }
        return H("span", { class: "rlt-run" }, [
          H("i", { style: { background: p.color } }),
          prev,
          H("b", { text: (ep.run_id || "run 1") }),
          next,
          H("span", { text: "return " + signed(ep.ret) + " · " + (at + 1) + " of " + runs.length + " by return" +
            (at === medianIndex(runs.length) ? " · median" : "") + (ep.detail ? " · step detail" : "") }),
        ]);
      }

      function rebuild() {
        pickEpisodes();
        sel.value = state.task.id;
        savePref(pref);
        runsBar.innerHTML = "";
        m.policies.forEach(function (p) {
          var s = runStepper(p);
          if (s) runsBar.appendChild(s);
        });
        svgHost.innerHTML = "";
        responsive(svgHost, function () { drawStage(svgHost, ctx, m, state, paintRef); paintRef.paint(state.pos); }, "rl-theatre");
        paint();
        why.textContent = state.task !== auto || !auto.__why ? ""
          : auto.__why === "contested" && lead
            ? "opened here because this is the contested task: " + lead.name + " is ahead over the batch but behind on this one (Δ " + signed(state.task.delta, 1) + ")"
            : "opened here because this is the widest gap between the policies (Δ " + signed(state.task.delta, 1) + ")";
        note.textContent = "The two runs are lined up by step index only: step " + state.pos +
          " of one run is not the same action as step " + state.pos + " of the other, and the tracks end where each run ended — " +
          state.eps.map(function (e) { return e.policy + " " + e.steps; }).join(" steps, ") + " steps. " +
          "Drag or click the chart to scrub, ← → to step (shift for ten), Home and End for the ends, space to play at 8 steps a second. " +
          "The bar at each mark is that step's reward, up or down from the line, its height the square root of the reward against the largest here (" +
          plain(state.maxAbs) + ") so the ordinary −0.10 steps stay visible next to a terminal ±5." +
          (reduced ? " Reduced motion is on, so it never plays on its own." : " It never plays on its own.") +
          (state.part ? " The ring marks step " + state.part.step + ", where the returns first differ by a whole point." : " The returns never differ by a whole point on these two runs.");
      }

      // ridgeline hand-off: show this exact episode
      mount({
        root: root,
        show: function (taskId, policy, runId) {
          var t = taskById(m, taskId);
          if (!t) return false;
          stop();
          if (t !== state.task) { state.task = t; state.chosen = {}; state.pos = 0; pref.runs = {}; }
          state.chosen[policy] = runId;
          pref.task = taskId; pref.runs[policy] = runId;
          rebuild();
          try { root.scrollIntoView({ block: "nearest", behavior: reduced ? "auto" : "smooth" }); } catch (err) { /* older browsers */ }
          return true;
        },
      });

      rebuild();
    },
  });
})(typeof window !== "undefined" ? window : this);
