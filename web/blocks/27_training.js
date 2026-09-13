/* AgentDiff blocks — the Training view: the RL training ground.
 *
 * A runs-layout batch is a training set: several episodes per policy per
 * task, each with a per-step reward, sometimes a value estimate and an
 * advantage, and a preference between the two policies on every task.
 * These blocks draw that set — every number a count or a sum from the JSON:
 *
 *   rl-here         the pair's own reward & credit panel, docked at the top
 *   rl-curves       per task, return per episode for each policy, the
 *                   policy's interval as a band, successes filled
 *   rl-reward-map   episodes × steps: every reward as a diverging cell,
 *                   quiet stretches folded (×N) as in Where it mattered
 *   rl-advantage    value estimate against discounted return-to-go, and
 *                   the advantages as a histogram per policy
 *   rl-events       what the reward paid for and punished, by label
 *   rl-preferences  the preference pairs as a table and as JSONL
 *   rl-policy-delta Δ mean return per task, sorted, with the sign count
 *
 * Reads `aggregate.rl` ({gamma, source, agents: {name: {episodes, …}},
 * tasks, preferences}) and, for a single pair, `report.rl` (a, b as
 * RLRun with per-step rewards); the pair views follow a click on a task.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;

  //: a quiet stretch of this many zero-reward steps folds into ×N
  var FOLD_MIN = 5;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".rl{position:relative}",
      ".rl svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rl .lab{font-size:var(--fs-xs);fill:var(--ink-2)}.rl .lab.dim{fill:var(--ink-3)}.rl .lab.mono{font-family:var(--mono)}",
      ".rl .tick{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".rl .grid{stroke:var(--rule)}.rl .zero{stroke:var(--rule-2)}",
      ".rl-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".rl-bar{display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".rl-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
      ".rl-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".rl-chip b{color:var(--ink);font-weight:600}",
      ".rl-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer}",
      ".rl-bar button:hover{color:var(--ink)}",
      ".note.rl-note,.rl-note{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0;max-width:90ch;line-height:1.45}",
      ".rl-grid{display:grid;gap:14px 16px}",
      ".rl-multiple svg{cursor:default}.rl-multiple[aria-current=true] .title{fill:var(--ink);font-weight:600}",
      ".rl .pt{cursor:pointer}.rl .pt:hover{stroke-width:2.5}",
      ".rl .rlm-cell{shape-rendering:crispEdges}.rl .rlm-cell.hit{cursor:pointer}",
      ".rl .rlm-fold{cursor:pointer}.rl .rlm-fold text{fill:var(--ink-3);font-family:var(--mono);font-size:var(--fs-xs);pointer-events:none}.rl .rlm-fold:hover line{stroke:var(--ink)}",
      ".rl .rlm-lab.pair{fill:var(--ink);font-weight:600}",
      ".rl-row{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start}.rl-row>*{flex:1 1 280px;min-width:0}",
      ".rl-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
      ".rl-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".rl-table td{padding:3px 10px 3px 0;white-space:nowrap;color:var(--ink-2);vertical-align:top}.rl-table td.num{text-align:right;font-family:var(--mono)}",
      ".rl-table td.basis{white-space:normal;min-width:16ch;max-width:44ch;color:var(--ink-3)}",
      ".rl-table td i{display:inline-block;width:8px;height:8px;border-radius:50%;vertical-align:0;margin-right:5px}",
      ".rl-table tr[data-task]{cursor:pointer}.rl-table tr[data-task]:hover td{color:var(--ink)}",
      ".rl-details{margin-top:6px;font-size:var(--fs-xs)}.rl-details summary{cursor:pointer;color:var(--ink-2)}",
      ".rl-details pre{font-size:var(--fs-xs);font-family:var(--mono);color:var(--ink-2);white-space:pre;overflow-x:auto;margin:6px 0 0;max-width:100%}",
      ".rld-sum{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px}",
      ".rld-row{display:grid;grid-template-columns:minmax(80px,1fr) minmax(120px,2fr) 52px;gap:8px;align-items:center;padding:3px 2px;border-radius:5px;cursor:pointer;font-size:var(--fs-xs);color:var(--ink-2)}",
      ".rld-row:hover,.rld-row:focus-visible{background:var(--surface-2);outline:none}.rld-row[aria-current=true] .rld-lab{color:var(--ink);font-weight:600}",
      ".rld-lab{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:var(--mono)}",
      ".rld-bar{position:relative;height:10px}.rld-bar:before{content:'';position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--rule-2)}",
      ".rld-fill{position:absolute;top:1px;height:8px;border-radius:2px}",
      ".rld-val{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink)}",
      ".rl-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:320px}",
      ".rl-tip b{color:var(--ink)}",
      "@media (max-width:640px){.rld-row{grid-template-columns:minmax(60px,1fr) minmax(80px,2fr) 48px}}",
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
  function ret(ep) { return ep.ret; }
  function key(ep) { return ep.task_id + "|" + ep.policy + "|" + (ep.run_id || ""); }
  function colorOf(side, i) { return side === "a" ? "var(--a)" : side === "b" ? "var(--b)" : i === 2 ? "var(--warn)" : "var(--ink-3)"; }
  //: a fold's length scales with the steps it constricts — the same rule as Where it mattered
  function foldW(steps) { return 6 + 6 * Math.log(1 + Math.max(0, steps)) / Math.LN2; }
  function width(host) { var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0; return Math.max(300, Math.min(1400, w || 320)); }
  function responsive(host, draw, k) {
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, k);
    draw(); return host;
  }
  function selectStep(report, side, step) {
    if (AgentDiff.charts && AgentDiff.charts.selectStep && report) AgentDiff.charts.selectStep(report, side, step);
  }
  function selectTask(ctx, id) {
    var fn = ctx && typeof ctx.selectTask === "function" ? ctx.selectTask
      : AgentDiff._internals && typeof AgentDiff._internals.selectTask === "function" ? AgentDiff._internals.selectTask : null;
    if (fn && id) fn(id);
  }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "rl-tip"; tip.hidden = true; root.appendChild(tip);
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
        if (x + 320 > r.width) x = Math.max(0, evt.clientX - r.left - 330);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  //: a per-step number list from either numbers or {step, reward} entries
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
  function perStepNullable(list, n) {
    var out = [];
    if (Array.isArray(list)) list.forEach(function (v, i) { out[i] = isNum(v) ? v : (v && typeof v === "object" && isNum(v.value)) ? v.value : null; });
    for (var i = 0; i < n; i++) if (out[i] === undefined) out[i] = null;
    out.length = n;
    return out;
  }
  function cumsum(rewards) { var out = [], acc = 0; rewards.forEach(function (r) { acc += r; out.push(acc); }); return out; }
  //: G_k = Σ_{j≥k} γ^{j−k} r_j
  function toGo(rewards, gamma) {
    var out = new Array(rewards.length), acc = 0;
    for (var i = rewards.length - 1; i >= 0; i--) { acc = rewards[i] + gamma * acc; out[i] = acc; }
    return out;
  }

  // -------------------------------------------------------------- model

  /* One model for every block: the policies (with a side, so the page's
   * colours carry over), every episode with its per-step arrays, the
   * tasks with their per-policy means and delta, the preference pairs.
   * From `aggregate.rl` when the batch has runs; from the reports' own
   * `rl` when it has only a pair per task. */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {}, reports = ctx.reports || [], report = ctx.report || null;
    if (cache && cache.agg === agg && cache.reports === reports && cache.report === report) return cache.m;
    var m;
    try { m = build(agg, reports, report); } catch (err) { console.warn("AgentDiff training: model failed", err); m = build({}, [], null); }
    cache = { agg: agg, reports: reports, report: report, m: m };
    return m;
  }

  function episodeFromAggregate(raw, pol, gamma) {
    var n = isNum(raw.steps) ? raw.steps : 0;
    var rewards = perStep(raw.rewards, n);
    n = rewards.length;
    var ep = {
      policy: pol.name, side: pol.side, task_id: String(raw.task_id || ""), run_id: raw.run_id === null || raw.run_id === undefined ? "" : String(raw.run_id),
      steps: n, rewards: rewards, success: !!raw.success, seconds: isNum(raw.seconds) ? raw.seconds : null,
      events: raw.events && typeof raw.events === "object" ? raw.events : {}, tools: raw.tools && typeof raw.tools === "object" ? raw.tools : {},
      distinct_inputs: isNum(raw.distinct_inputs) ? raw.distinct_inputs : null,
    };
    ep.cum = Array.isArray(raw.cum) && raw.cum.length === n && raw.cum.every(isNum) ? raw.cum : cumsum(rewards);
    ep.values = perStepNullable(raw.values, n);
    ep.advantages = perStepNullable(raw.advantages, n);
    ep.ret = isNum(raw["return"]) ? raw["return"] : sum(rewards);
    ep.dret = isNum(raw.discounted_return) ? raw.discounted_return : (toGo(rewards, gamma)[0] || 0);
    return ep;
  }

  function episodeFromReport(p, pol, gamma) {
    var run = p.run, r = p.report, side = p.side;
    var steps = (r[side] && Array.isArray(r[side].steps)) ? r[side].steps : [];
    var n = isNum(run.steps) ? run.steps : steps.length;
    var rewards = perStep(run.rewards, n);
    n = rewards.length;
    var values = [], advantages = [], tools = {}, inputs = {};
    for (var i = 0; i < n; i++) {
      var s = steps[i] || {};
      values.push(isNum(s.value) ? s.value : null);
      advantages.push(isNum(s.advantage) ? s.advantage : null);
      if (s.name && s.type !== "plan" && s.type !== "reason" && s.type !== "answer") tools[s.name] = (tools[s.name] || 0) + 1;
      if (s.input !== undefined && s.input !== null) inputs[String(s.input)] = true;
    }
    var events = {};
    (run.rewards || []).forEach(function (e) { if (e && Array.isArray(e.labels)) e.labels.forEach(function (l) { events[l] = (events[l] || 0) + 1; }); });
    var ep = {
      policy: pol.name, side: pol.side, task_id: p.task, run_id: p.run_id, steps: n, rewards: rewards,
      success: !!(r[side] && r[side].outcome && r[side].outcome.success), seconds: isNum(run.seconds) ? run.seconds : null,
      events: events, tools: tools, distinct_inputs: Object.keys(inputs).length,
      cum: cumsum(rewards), values: values, advantages: advantages,
    };
    ep.ret = isNum(run["return"]) ? run["return"] : sum(rewards);
    ep.dret = isNum(run.discounted_return) ? run.discounted_return : (toGo(rewards, gamma)[0] || 0);
    return ep;
  }

  function build(agg, reports, report) {
    var rl = agg && agg.rl && typeof agg.rl === "object" ? agg.rl : null;
    var an = report && report.a && report.a.agent && report.a.agent.name;
    var bn = report && report.b && report.b.agent && report.b.agent.name;
    var m = { source: null, gamma: 0.99, reward_source: null, policies: [], episodes: [], tasks: [], preferences: [], shaping: null, narrative: "", pairRuns: [] };
    var labelsIndex = {};
    reports.forEach(function (r) {
      var prl = r && r.rl; if (!prl || !r.task) return;
      ["a", "b"].forEach(function (s) {
        var run = prl[s]; if (!run || !Array.isArray(run.rewards)) return;
        var policy = run.agent || (r[s] && r[s].agent && r[s].agent.name) || s.toUpperCase();
        var rid = r[s] && r[s].run_id !== null && r[s].run_id !== undefined ? String(r[s].run_id) : "";
        var map = {};
        run.rewards.forEach(function (e) { if (e && isNum(e.step) && Array.isArray(e.labels) && e.labels.length) map[e.step] = e.labels; });
        labelsIndex[r.task.id + "|" + policy + "|" + rid] = map;
        m.pairRuns.push({ task: r.task.id, policy: policy, side: s, run_id: rid, run: run, report: r });
      });
    });
    var names = [];
    if (rl && rl.agents && typeof rl.agents === "object" && Object.keys(rl.agents).length) {
      m.source = "aggregate";
      names = Object.keys(rl.agents);
      if (isNum(rl.gamma)) m.gamma = rl.gamma;
      m.reward_source = rl.source || null;
      m.narrative = typeof rl.narrative === "string" ? rl.narrative : "";
      var sh = rl.shaping || rl.signs || rl.weights;
      if (sh && typeof sh === "object" && Object.keys(sh).some(function (k) { return isNum(sh[k]); })) m.shaping = sh;
    } else if (m.pairRuns.length) {
      m.source = "reports";
      m.pairRuns.forEach(function (p) { if (names.indexOf(p.policy) < 0) names.push(p.policy); });
      var first = m.pairRuns[0].report.rl;
      if (isNum(first.gamma)) m.gamma = first.gamma;
      m.reward_source = first.source || null;
      m.narrative = typeof first.narrative === "string" ? first.narrative : "";
    } else {
      return m;
    }
    // sides: the selected pair's names first, then by order
    var sideOf = {};
    names.forEach(function (n) { sideOf[n] = n === an ? "a" : n === bn ? "b" : null; });
    var free = ["a", "b"].filter(function (s) { return names.every(function (n) { return sideOf[n] !== s; }); });
    names.forEach(function (n) { if (!sideOf[n] && free.length) sideOf[n] = free.shift(); });
    var rankOf = { a: 0, b: 1 };
    names.sort(function (x, y) { return (sideOf[x] in rankOf ? rankOf[sideOf[x]] : 2) - (sideOf[y] in rankOf ? rankOf[sideOf[y]] : 2); });
    names.forEach(function (n, i) {
      var pol = { name: n, side: sideOf[n], color: colorOf(sideOf[n], i), episodes: [], mean_return: null, return_ci: null, episodes_n: 0 };
      if (m.source === "aggregate") {
        var ag = rl.agents[n] || {};
        (Array.isArray(ag.episodes) ? ag.episodes : []).forEach(function (raw) { if (raw && typeof raw === "object") pol.episodes.push(episodeFromAggregate(raw, pol, m.gamma)); });
        pol.mean_return = isNum(ag.mean_return) ? ag.mean_return : mean(pol.episodes.map(ret));
        pol.return_ci = Array.isArray(ag.return_ci) && isNum(ag.return_ci[0]) && isNum(ag.return_ci[1]) ? [ag.return_ci[0], ag.return_ci[1]] : null;
        pol.episodes_n = isNum(ag.episodes_n) ? ag.episodes_n : pol.episodes.length;
      } else {
        m.pairRuns.filter(function (p) { return p.policy === n; }).forEach(function (p) { pol.episodes.push(episodeFromReport(p, pol, m.gamma)); });
        pol.mean_return = mean(pol.episodes.map(ret));
        pol.episodes_n = pol.episodes.length;
      }
      var pairName = pol.side === "a" ? an : pol.side === "b" ? bn : null;
      pol.episodes.forEach(function (ep) {
        ep.labels = labelsIndex[key(ep)] || null;
        var pairRun = report && pol.side && report[pol.side] && report[pol.side].run_id !== null && report[pol.side].run_id !== undefined ? String(report[pol.side].run_id) : null;
        ep.inPair = report && report.task && ep.task_id === report.task.id && ep.policy === pairName && (!ep.run_id || pairRun === null || ep.run_id === pairRun) ? pol.side : null;
        m.episodes.push(ep);
      });
      m.policies.push(pol);
    });
    // tasks: the page's order, then any the aggregate names beyond it
    var order = [];
    reports.forEach(function (r) { if (r && r.task && order.indexOf(r.task.id) < 0) order.push(r.task.id); });
    m.episodes.forEach(function (ep) { if (order.indexOf(ep.task_id) < 0) order.push(ep.task_id); });
    var rt = rl && rl.tasks && typeof rl.tasks === "object" ? rl.tasks : {};
    Object.keys(rt).forEach(function (t) { if (order.indexOf(t) < 0) order.push(t); });
    var A = m.policies[0], B = m.policies[1];
    order.forEach(function (t) {
      var eps = m.episodes.filter(function (e) { return e.task_id === t; });
      var raw = rt[t] && typeof rt[t] === "object" ? rt[t] : null;
      if (!eps.length && !raw) return;
      var perRaw = raw && raw.agents && typeof raw.agents === "object" ? raw.agents
        : raw && raw.agent && typeof raw.agent === "object" && !("mean_return" in raw.agent) ? raw.agent : raw;
      var per = {};
      m.policies.forEach(function (p) {
        var src = perRaw && perRaw[p.name] && typeof perRaw[p.name] === "object" ? perRaw[p.name] : null;
        var returns = src && Array.isArray(src.returns) ? src.returns.filter(isNum) : eps.filter(function (e) { return e.policy === p.name; }).map(ret);
        per[p.name] = { mean_return: src && isNum(src.mean_return) ? src.mean_return : mean(returns), returns: returns };
      });
      var delta = raw && isNum(raw.delta) ? raw.delta
        : A && B && isNum(per[B.name].mean_return) && isNum(per[A.name].mean_return) ? per[B.name].mean_return - per[A.name].mean_return : null;
      var sign = raw && isNum(raw.sign) ? (raw.sign > 0 ? 1 : raw.sign < 0 ? -1 : 0) : isNum(delta) ? (delta > 0 ? 1 : delta < 0 ? -1 : 0) : null;
      m.tasks.push({ id: t, per: per, delta: delta, sign: sign, episodes: eps });
    });
    // preferences
    if (rl && Array.isArray(rl.preferences)) {
      rl.preferences.forEach(function (p) { if (p && typeof p === "object") m.preferences.push(p); });
    } else {
      reports.forEach(function (r) {
        var pf = r && r.rl && r.rl.preference;
        if (pf && typeof pf === "object" && (pf.chosen || pf.rejected)) m.preferences.push(Object.assign({ task_id: r.task.id }, pf));
      });
    }
    return m;
  }

  function policyOf(m, name) { for (var i = 0; i < m.policies.length; i++) if (m.policies[i].name === name) return m.policies[i]; return null; }
  function colorFor(m, name) { var p = policyOf(m, name); return p ? p.color : "var(--ink-3)"; }

  /* What the reward paid for: per policy, per label, the events counted
   * and the reward summed. With shaping weights: every episode's events
   * × the weight. Otherwise each recorded reward in the pair reports,
   * split evenly over its labels (its step kind when it has none). */
  function composition(m) {
    var per = {}, labels = [], totals = {};
    m.policies.forEach(function (p) { per[p.name] = {}; });
    function add(policy, label, events, reward) {
      if (!per[policy]) return;
      var cell = per[policy][label] || (per[policy][label] = { events: 0, reward: 0 });
      cell.events += events; cell.reward += reward;
      totals[label] = (totals[label] || 0) + reward;
      if (labels.indexOf(label) < 0) labels.push(label);
    }
    var basis;
    if (m.shaping) {
      basis = "every episode's events × the shaping weight of each label";
      m.episodes.forEach(function (ep) {
        Object.keys(ep.events).forEach(function (l) { var c = ep.events[l]; if (isNum(c) && isNum(m.shaping[l])) add(ep.policy, l, c, c * m.shaping[l]); });
      });
    } else {
      basis = "each recorded reward in the pair reports, split evenly over its labels (its step kind when it carries none)";
      m.pairRuns.forEach(function (p) {
        (p.run.rewards || []).forEach(function (e) {
          if (!e || !isNum(e.reward) || e.reward === 0) return;
          var ls = Array.isArray(e.labels) && e.labels.length ? e.labels.map(String) : [e.kind ? String(e.kind) : "unlabelled"];
          ls.forEach(function (l) { add(p.policy, l, 1, e.reward / ls.length); });
        });
      });
    }
    labels.sort(function (x, y) { return Math.abs(totals[y]) - Math.abs(totals[x]); });
    return { per: per, labels: labels, totals: totals, basis: basis };
  }

  // -------------------------------------------------------------- blocks

  function policyChips(H, m, text) {
    var bar = H("div", { class: "rl-bar" });
    m.policies.forEach(function (p) {
      var ci = p.return_ci;
      bar.appendChild(H("span", { class: "rl-chip rlc-chip", "data-policy": p.name }, [
        H("i", { style: { background: p.color } }),
        H("b", { text: p.name }),
        H("span", { text: " · " + (text ? text(p) : "mean " + signed(p.mean_return) + (ci ? " [" + plain(ci[0]) + ", " + plain(ci[1]) + "]" : "") + " · n=" + p.episodes_n) }),
      ]));
    });
    return bar;
  }

  /* rl-here: the pair's own reward & credit panel, rendered through its
   * own renderer, so the training ground opens on the pair the page is
   * on — the way the map docks the step inspector. */
  AgentDiff.block({
    id: "rl-here",
    title: "Reward & credit · this pair",
    question: "The selected pair's rewards and credit, at the top of the training ground.",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var entry = typeof AgentDiff.blockEntry === "function" ? AgentDiff.blockEntry("rl") : null;
      if (!entry || typeof entry.render !== "function") return 0;
      try { var v = entry.relevance(ctx); return isNum(v) && v > 0 ? 0.8 : 0; } catch (err) { return 0; }
    },
    render: function (el, ctx) {
      var entry = AgentDiff.blockEntry("rl");
      if (!entry) return ctx.empty(el, "No reward panel is registered.");
      entry.render(el, ctx);
    },
  });

  /* rl-curves: per task, return per episode for each policy. */
  function drawCurves(host, ctx, m, tip) {
    if (!d3) return;
    var H = ctx.h;
    var W = width(host);
    var gap = 16, cols = Math.max(1, Math.min(m.tasks.length, Math.floor((W + gap) / (160 + gap))));
    var cw = Math.floor((W - gap * (cols - 1)) / cols), ch = 118;
    var pad = { l: 44, r: 10, t: 26, b: 16 };
    var maxN = 1, lo = Infinity, hi = -Infinity;
    m.episodes.forEach(function (e) { lo = Math.min(lo, e.ret); hi = Math.max(hi, e.ret); });
    m.policies.forEach(function (p) { if (p.return_ci) { lo = Math.min(lo, p.return_ci[0]); hi = Math.max(hi, p.return_ci[1]); } });
    m.tasks.forEach(function (t) { m.policies.forEach(function (p) { maxN = Math.max(maxN, t.episodes.filter(function (e) { return e.policy === p.name; }).length); }); });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (lo === hi) { lo -= 1; hi += 1; }
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([ch - pad.b, pad.t]);
    var x = d3.scaleLinear().domain([1, Math.max(2, maxN)]).range([pad.l, cw - pad.r]);
    var grid = H("div", { class: "rl-grid" });
    grid.style.gridTemplateColumns = "repeat(" + cols + ", minmax(0, 1fr))";
    host.appendChild(grid);
    m.tasks.forEach(function (t, ti) {
      var cell = H("div", { class: "rl-multiple", "data-task": t.id, "aria-current": ctx.task === t.id ? "true" : "false" });
      grid.appendChild(cell);
      var svg = d3.select(cell).append("svg").attr("viewBox", "0 0 " + cw + " " + ch).attr("width", cw).attr("height", ch).attr("role", "img")
        .attr("aria-label", "returns per episode on " + t.id + ", each policy");
      svg.append("text").attr("class", "lab mono title").attr("x", pad.l).attr("y", 11).text(trunc(short(t.id), Math.floor((cw - pad.l) / 7)));
      var ticks = y.ticks(3);
      ticks.forEach(function (v) {
        svg.append("line").attr("class", v === 0 ? "zero" : "grid").attr("x1", pad.l).attr("x2", cw - pad.r).attr("y1", y(v)).attr("y2", y(v));
        if (ti % cols === 0) svg.append("text").attr("class", "tick").attr("x", pad.l - 5).attr("y", y(v) + 4).attr("text-anchor", "end").text(signed(v, 1));
      });
      m.policies.forEach(function (p, pi) {
        var eps = t.episodes.filter(function (e) { return e.policy === p.name; });
        var tm = t.per[p.name] && t.per[p.name].mean_return;
        if (p.return_ci) {
          svg.append("rect").attr("class", "rlc-band").attr("data-policy", p.name).attr("x", pad.l).attr("width", cw - pad.l - pad.r)
            .attr("y", y(p.return_ci[1])).attr("height", Math.max(1, y(p.return_ci[0]) - y(p.return_ci[1]))).attr("fill", p.color).attr("fill-opacity", 0.09)
            .append("title").text(p.name + " · mean return " + signed(p.mean_return) + " [" + plain(p.return_ci[0]) + ", " + plain(p.return_ci[1]) + "] over " + p.episodes_n + " episodes");
        }
        if (isNum(tm)) {
          svg.append("line").attr("class", "rlc-mean").attr("data-policy", p.name).attr("x1", pad.l).attr("x2", cw - pad.r).attr("y1", y(tm)).attr("y2", y(tm))
            .attr("stroke", p.color).attr("stroke-opacity", 0.55).attr("stroke-dasharray", "2 3");
        }
        if (eps.length > 1) {
          svg.append("path").attr("class", "rlc-line").attr("data-policy", p.name).attr("fill", "none").attr("stroke", p.color).attr("stroke-opacity", 0.5).attr("stroke-width", 1.2)
            .attr("d", eps.map(function (e, i) { return (i ? "L" : "M") + x(i + 1).toFixed(1) + "," + y(e.ret).toFixed(1); }).join(" "));
        }
        eps.forEach(function (e, i) {
          svg.append("circle").attr("class", "pt rlc-pt" + (e.success ? " ok" : " fail")).attr("data-policy", p.name).attr("data-task", t.id).attr("data-run", e.run_id).attr("data-return", e.ret)
            .attr("cx", x(i + 1)).attr("cy", y(e.ret)).attr("r", 3.6).attr("fill", e.success ? p.color : "var(--surface)").attr("stroke", p.color).attr("stroke-width", 1.5)
            .on("pointermove", function (evt) {
              tip.show(evt, [{ b: true, text: p.name + " · " + short(t.id) + (e.run_id ? " · " + e.run_id : "") },
                { text: "return " + signed(e.ret) + " · " + e.steps + " steps · " + (e.success ? "solved" : "failed") + (isNum(e.seconds) ? " · " + e.seconds.toFixed(1) + "s" : "") },
                { text: "click to open this task" }]);
            }).on("pointerleave", tip.hide)
            .on("click", function () { tip.hide(); selectTask(ctx, t.id); });
        });
      });
    });
  }

  AgentDiff.block({
    id: "rl-curves",
    title: "Learning curves",
    question: "Per task: the return of every episode of each policy, the policy's interval as a band — solved episodes filled, failures hollow.",
    group: "training",
    size: "wide",
    relevance: function (ctx) { return model(ctx).episodes.length ? 0.79 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rl rl-curves" });
      el.appendChild(root);
      var tip = tooltip(root);
      if (m.narrative) root.appendChild(H("p", { class: "rl-narr", text: m.narrative }));
      root.appendChild(policyChips(H, m));
      var host = H("div", { class: "rl-chart" });
      root.appendChild(responsive(host, function () { drawCurves(host, ctx, m, tip); }, "rl-curves"));
      root.appendChild(H("p", { class: "rl-note", text: "x = episode index within the task · band = the policy's mean return interval over every episode · dashed = the task mean · filled = solved · " + (m.source === "aggregate" ? m.episodes.length + " episodes over " + m.tasks.length + " tasks" : "one episode per policy per task") + (m.reward_source ? " · rewards " + m.reward_source : "") + "." }));
    },
  });

  /* rl-reward-map: episodes × steps, every reward a cell. */
  var UNFOLDED = {};
  function units(ep) {
    var out = [], i = 0, n = ep.rewards.length;
    while (i < n) {
      if (ep.rewards[i] !== 0) { out.push({ type: "cell", step: i, reward: ep.rewards[i] }); i++; continue; }
      var j = i;
      while (j < n && ep.rewards[j] === 0) j++;
      var len = j - i;
      if (len >= FOLD_MIN && !UNFOLDED[key(ep) + "|" + i]) out.push({ type: "fold", from: i, to: j - 1, steps: len });
      else for (var k = i; k < j; k++) out.push({ type: "blank", step: k });
      i = j;
    }
    return out;
  }

  //: "task · run", the task name giving way first
  function rowLabel(e, maxChars) {
    var run = e.run_id ? " · " + e.run_id : "";
    return trunc(short(e.task_id), Math.max(4, maxChars - run.length)) + run;
  }
  function drawMap(host, ctx, m, tip, repaint) {
    if (!d3) return;
    var W = width(host);
    var rowH = 14, headH = 22, axisH = 20, padR = 16;
    var maxSteps = 1, maxAbs = 0;
    m.episodes.forEach(function (e) { maxSteps = Math.max(maxSteps, e.steps); e.rewards.forEach(function (r) { maxAbs = Math.max(maxAbs, Math.abs(r)); }); });
    var labels = m.episodes.map(function (e) { return short(e.task_id) + (e.run_id ? " · " + e.run_id : ""); });
    var labW = Math.min(224, Math.max(60, 12 + 7.3 * labels.reduce(function (a, s) { return Math.max(a, s.length); }, 6)));
    var sw = Math.max(1.5, (W - labW - padR) / maxSteps);
    var rows = [], yAt = axisH;
    m.policies.forEach(function (p) {
      rows.push({ type: "head", policy: p, y: yAt }); yAt += headH;
      p.episodes.forEach(function (e, i) { rows.push({ type: "ep", ep: e, y: yAt, label: short(e.task_id) + (e.run_id ? " · " + e.run_id : "") }); yAt += rowH; });
      yAt += 8;
    });
    var Hh = yAt;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "reward map: one row per episode, one cell per step with a reward, grouped by policy; quiet stretches folded");
    // the step axis: ticks that keep 40px apart
    var every = [1, 2, 5, 10, 20, 50, 100, 200, 500].filter(function (k) { return k * sw >= 40; })[0] || 1000;
    svg.append("text").attr("class", "tick").attr("x", labW).attr("y", 12).text("step");
    for (var s = 0; s < maxSteps; s += every) {
      if (s === 0) continue;
      svg.append("line").attr("class", "grid").attr("x1", labW + s * sw).attr("x2", labW + s * sw).attr("y1", axisH - 6).attr("y2", Hh - 8);
      svg.append("text").attr("class", "tick").attr("x", labW + s * sw + 2).attr("y", 12).text(String(s));
    }
    rows.forEach(function (row) {
      if (row.type === "head") {
        var p = row.policy;
        var g = svg.append("g").attr("class", "rlm-policy").attr("data-policy", p.name);
        g.append("circle").attr("cx", 5).attr("cy", row.y + 12).attr("r", 4).attr("fill", p.color);
        g.append("text").attr("class", "lab").attr("x", 14).attr("y", row.y + 16).text(p.name + " · " + p.episodes.length + " episodes · Σ return " + signed(sum(p.episodes.map(ret))));
        return;
      }
      var e = row.ep, p2 = policyOf(m, e.policy), color = p2 ? p2.color : "var(--ink-3)";
      var g2 = svg.append("g").attr("class", "rlm-row").attr("data-policy", e.policy).attr("data-task", e.task_id).attr("data-run", e.run_id).attr("data-pair", e.inPair || null);
      g2.append("text").attr("class", "lab mono rlm-lab" + (e.inPair ? " pair" : " dim")).attr("x", labW - 6).attr("y", row.y + rowH - 3).attr("text-anchor", "end")
        .text(rowLabel(e, Math.floor((labW - 8) / 7.3)));
      var x = labW, cells = 0;
      units(e).forEach(function (u) {
        if (u.type === "blank") { x += sw; return; }
        if (u.type === "fold") {
          var w = Math.min(foldW(u.steps), u.steps * sw);
          var fg = g2.append("g").attr("class", "rlm-fold").attr("data-from", u.from).attr("data-steps", u.steps);
          fg.append("line").attr("x1", x + 1).attr("x2", x + w - 1).attr("y1", row.y + rowH / 2).attr("y2", row.y + rowH / 2)
            .attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-dasharray", "1.5 2.5").attr("stroke-linecap", "round");
          if (w >= 24) fg.append("text").attr("x", x + w / 2).attr("y", row.y + rowH - 3).attr("text-anchor", "middle").text("×" + u.steps);
          fg.append("rect").attr("x", x).attr("y", row.y).attr("width", w).attr("height", rowH).attr("fill", "transparent");
          fg.append("title").text(u.steps + " steps without reward (" + u.from + "–" + u.to + ") folded; click to open");
          fg.on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: e.policy + " · " + short(e.task_id) + (e.run_id ? " · " + e.run_id : "") }, { text: "steps " + u.from + "–" + u.to + " · " + u.steps + " steps · reward 0" }, { text: "click to open" }]);
          }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); UNFOLDED[key(e) + "|" + u.from] = true; repaint(); });
          x += w;
          return;
        }
        cells++;
        var op = maxAbs > 0 ? 0.3 + 0.7 * Math.abs(u.reward) / maxAbs : 0.8;
        var rect = g2.append("rect").attr("class", "rlm-cell" + (e.inPair ? " hit" : "")).attr("data-step", u.step).attr("data-reward", u.reward)
          .attr("x", x).attr("y", row.y + 1).attr("width", Math.max(1, sw - (sw > 4 ? 1 : 0))).attr("height", rowH - 2)
          .attr("fill", u.reward > 0 ? "var(--good)" : "var(--bad)").attr("fill-opacity", op);
        rect.on("pointermove", function (evt) {
          var ls = e.labels && e.labels[u.step] ? e.labels[u.step].join(", ") : null;
          tip.show(evt, [{ b: true, text: e.policy + " · " + short(e.task_id) + (e.run_id ? " · " + e.run_id : "") },
            { text: "step " + u.step + " · reward " + signed(u.reward) + " · cum " + signed(e.cum[u.step]) + (isNum(e.values[u.step]) ? " · value " + plain(e.values[u.step]) : "") },
            ls ? { text: ls } : null,
            e.inPair ? { text: "click to open the step" } : null]);
        }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); if (e.inPair) selectStep(ctx.report, e.inPair, u.step); });
        x += sw;
      });
      // the episode's end: solved filled, failed hollow
      g2.append("circle").attr("class", "rlm-end" + (e.success ? " ok" : " fail")).attr("cx", x + 5).attr("cy", row.y + rowH / 2).attr("r", 2.6)
        .attr("fill", e.success ? color : "var(--surface)").attr("stroke", color).attr("stroke-width", 1.2)
        .append("title").text((e.success ? "solved" : "failed") + " · return " + signed(e.ret));
      g2.attr("data-end", x.toFixed(1)).attr("data-cells", cells);
    });
  }

  AgentDiff.block({
    id: "rl-reward-map",
    title: "Reward map",
    question: "Every episode as a strip of steps: where the reward landed (green), where it was lost (red), and the quiet stretches folded.",
    group: "training",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); return m.episodes.some(function (e) { return e.steps > 0; }) ? 0.78 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rl rl-map" });
      el.appendChild(root);
      var tip = tooltip(root);
      var pos = 0, neg = 0, zero = 0;
      m.episodes.forEach(function (e) { e.rewards.forEach(function (r) { if (r > 0) pos++; else if (r < 0) neg++; else zero++; }); });
      var bar = H("div", { class: "rl-bar" }, [
        H("span", { class: "rl-chip", "data-key": "pos" }, [H("i", { style: { background: "var(--good)", borderRadius: "2px" } }), H("span", { text: pos + " rewarded steps" })]),
        H("span", { class: "rl-chip", "data-key": "neg" }, [H("i", { style: { background: "var(--bad)", borderRadius: "2px" } }), H("span", { text: neg + " penalised" })]),
        H("span", { class: "rl-chip", "data-key": "zero", text: zero + " silent" }),
        H("span", { text: "a fold ×N holds N silent steps · bold rows are the pair on the page; a click there opens the step" }),
      ]);
      var folded = Object.keys(UNFOLDED).length;
      if (folded) bar.appendChild(H("button", { text: "fold the quiet back", onclick: function () { UNFOLDED = {}; if (AgentDiff._rerender) AgentDiff._rerender(); } }));
      root.appendChild(bar);
      var host = H("div", { class: "rl-chart" });
      function repaint() { host.innerHTML = ""; drawMap(host, ctx, m, tip, repaint); }
      root.appendChild(responsive(host, function () { drawMap(host, ctx, m, tip, repaint); }, "rl-map"));
    },
  });

  /* rl-advantage: value estimate vs discounted return-to-go; advantages. */
  function valuePoints(m) {
    var pts = [];
    m.episodes.forEach(function (e) {
      var g = toGo(e.rewards, m.gamma);
      e.values.forEach(function (v, k) { if (isNum(v)) pts.push({ ep: e, step: k, value: v, togo: g[k] }); });
    });
    return pts;
  }
  function advantages(m) {
    var out = [];
    m.episodes.forEach(function (e) { e.advantages.forEach(function (a, k) { if (isNum(a)) out.push({ ep: e, step: k, adv: a }); }); });
    return out;
  }

  function drawScatter(host, ctx, m, pts, tip) {
    if (!d3) return;
    var W = Math.max(240, host.clientWidth || 300), Hh = 220, pad = { l: 40, r: 12, t: 14, b: 26 };
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) { lo = Math.min(lo, p.value, p.togo); hi = Math.max(hi, p.value, p.togo); });
    if (lo === hi) { lo -= 1; hi += 1; }
    var x = d3.scaleLinear().domain([lo, hi]).nice().range([pad.l, W - pad.r]);
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([Hh - pad.b, pad.t]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "value estimate against discounted return-to-go, one point per step, with the diagonal");
    x.ticks(4).forEach(function (v) {
      svg.append("line").attr("class", v === 0 ? "zero" : "grid").attr("x1", x(v)).attr("x2", x(v)).attr("y1", pad.t).attr("y2", Hh - pad.b);
      svg.append("text").attr("class", "tick").attr("x", x(v)).attr("y", Hh - pad.b + 13).attr("text-anchor", "middle").text(signed(v, 1));
    });
    y.ticks(4).forEach(function (v) {
      svg.append("line").attr("class", v === 0 ? "zero" : "grid").attr("x1", pad.l).attr("x2", W - pad.r).attr("y1", y(v)).attr("y2", y(v));
      svg.append("text").attr("class", "tick").attr("x", pad.l - 5).attr("y", y(v) + 4).attr("text-anchor", "end").text(signed(v, 1));
    });
    var d0 = Math.max(x.domain()[0], y.domain()[0]), d1 = Math.min(x.domain()[1], y.domain()[1]);
    svg.append("line").attr("class", "rla-diag").attr("x1", x(d0)).attr("y1", y(d0)).attr("x2", x(d1)).attr("y2", y(d1)).attr("stroke", "var(--ink-3)").attr("stroke-dasharray", "3 3");
    svg.append("text").attr("class", "lab dim").attr("x", W - pad.r).attr("y", Hh - 2).attr("text-anchor", "end").text("value estimate →");
    svg.append("text").attr("class", "lab dim").attr("x", pad.l + 4).attr("y", pad.t - 3).text("↑ discounted return-to-go (γ=" + m.gamma + ")");
    pts.forEach(function (p) {
      svg.append("circle").attr("class", "pt rla-pt").attr("data-policy", p.ep.policy).attr("data-task", p.ep.task_id).attr("data-run", p.ep.run_id).attr("data-step", p.step)
        .attr("cx", x(p.value)).attr("cy", y(p.togo)).attr("r", 3).attr("fill", colorFor(m, p.ep.policy)).attr("fill-opacity", 0.55).attr("stroke", colorFor(m, p.ep.policy)).attr("stroke-width", 1)
        .on("pointermove", function (evt) {
          tip.show(evt, [{ b: true, text: p.ep.policy + " · " + short(p.ep.task_id) + (p.ep.run_id ? " · " + p.ep.run_id : "") + " · step " + p.step },
            { text: "value " + signed(p.value) + " · return-to-go " + signed(p.togo) + " · error " + signed(p.value - p.togo) },
            p.ep.inPair ? { text: "click to open the step" } : null]);
        }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); if (p.ep.inPair) selectStep(ctx.report, p.ep.inPair, p.step); });
    });
  }

  function drawHist(host, ctx, m, advs, tip) {
    if (!d3) return;
    var W = Math.max(240, host.clientWidth || 300), Hh = 220, pad = { l: 30, r: 12, t: 14, b: 26 };
    var ext = d3.extent(advs, function (a) { return a.adv; });
    if (ext[0] === ext[1]) ext = [ext[0] - 1, ext[1] + 1];
    var x = d3.scaleLinear().domain(ext).nice().range([pad.l, W - pad.r]);
    var bins = d3.bin().domain(x.domain()).thresholds(12).value(function (a) { return a.adv; })(advs);
    var counts = {};
    var maxC = 1;
    bins.forEach(function (b, bi) {
      m.policies.forEach(function (p) { var c = b.filter(function (a) { return a.ep.policy === p.name; }).length; counts[bi + "|" + p.name] = c; maxC = Math.max(maxC, c); });
    });
    var y = d3.scaleLinear().domain([0, maxC]).nice().range([Hh - pad.b, pad.t]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img").attr("aria-label", "advantages as a histogram, one bar per bin per policy");
    y.ticks(3).forEach(function (v) {
      svg.append("line").attr("class", "grid").attr("x1", pad.l).attr("x2", W - pad.r).attr("y1", y(v)).attr("y2", y(v));
      svg.append("text").attr("class", "tick").attr("x", pad.l - 5).attr("y", y(v) + 4).attr("text-anchor", "end").text(String(v));
    });
    x.ticks(5).forEach(function (v) {
      svg.append("text").attr("class", "tick").attr("x", x(v)).attr("y", Hh - pad.b + 13).attr("text-anchor", "middle").text(signed(v, 1));
    });
    if (x.domain()[0] < 0 && x.domain()[1] > 0) svg.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0)).attr("y1", pad.t).attr("y2", Hh - pad.b);
    svg.append("text").attr("class", "lab dim").attr("x", W - pad.r).attr("y", Hh - 2).attr("text-anchor", "end").text("advantage →");
    svg.append("text").attr("class", "lab dim").attr("x", pad.l + 4).attr("y", pad.t - 3).text("↑ steps");
    var np = Math.max(1, m.policies.length);
    bins.forEach(function (b, bi) {
      var bw = Math.max(1, x(b.x1) - x(b.x0) - 2), each = bw / np;
      m.policies.forEach(function (p, pi) {
        var c = counts[bi + "|" + p.name];
        if (!c) return;
        svg.append("rect").attr("class", "rla-bar").attr("data-policy", p.name).attr("data-count", c).attr("data-from", b.x0).attr("data-to", b.x1)
          .attr("x", x(b.x0) + 1 + pi * each).attr("width", Math.max(1, each - 0.5)).attr("y", y(c)).attr("height", Hh - pad.b - y(c)).attr("fill", p.color).attr("fill-opacity", 0.75)
          .on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: p.name }, { text: c + " steps with advantage in [" + signed(b.x0) + ", " + signed(b.x1) + ")" }]); })
          .on("pointerleave", tip.hide);
      });
    });
  }

  AgentDiff.block({
    id: "rl-advantage",
    title: "Advantage & value",
    question: "Is the value estimate calibrated against what the episode actually returned from each step, and how are the advantages distributed?",
    group: "training",
    size: "wide",
    relevance: function (ctx) { return model(ctx).episodes.length ? 0.77 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rl rl-adv" });
      el.appendChild(root);
      var tip = tooltip(root);
      var pts = valuePoints(m), advs = advantages(m);
      var withValues = {};
      pts.forEach(function (p) { withValues[key(p.ep)] = true; });
      var nEp = Object.keys(withValues).length;
      if (!pts.length && !advs.length) {
        root.appendChild(H("p", { class: "note rl-note rla-note", text: "Rewards are recorded for " + m.episodes.length + " episode" + (m.episodes.length === 1 ? "" : "s") + ", but no value estimates were, so calibration and advantages cannot be drawn." }));
        return;
      }
      var bar = H("div", { class: "rl-bar" }, [
        H("span", { class: "rl-chip", "data-key": "values", text: pts.length + " steps with a value estimate · " + nEp + " of " + m.episodes.length + " episodes" }),
        H("span", { class: "rl-chip", "data-key": "advs", text: advs.length + " advantages" }),
      ]);
      m.policies.forEach(function (p) {
        var mine = advs.filter(function (a) { return a.ep.policy === p.name; });
        if (!mine.length) return;
        bar.appendChild(H("span", { class: "rl-chip", "data-policy": p.name }, [H("i", { style: { background: p.color } }), H("b", { text: p.name }), H("span", { text: " · mean advantage " + signed(mean(mine.map(function (a) { return a.adv; })), 3) })]));
      });
      root.appendChild(bar);
      var row = H("div", { class: "rl-row" });
      root.appendChild(row);
      if (pts.length) {
        var sh = H("div", { class: "rl-chart rla-scatter" });
        row.appendChild(responsive(sh, function () { drawScatter(sh, ctx, m, pts, tip); }, "rl-adv-scatter"));
      }
      if (advs.length) {
        var hh = H("div", { class: "rl-chart rla-hist" });
        row.appendChild(responsive(hh, function () { drawHist(hh, ctx, m, advs, tip); }, "rl-adv-hist"));
      }
      root.appendChild(H("p", { class: "rl-note", text: "Left: each step's value estimate against the discounted reward the episode went on to collect from it (γ=" + m.gamma + "); on the diagonal the critic was right, above it the critic was pessimistic, below it optimistic. Right: the advantages recorded per step, one bar per bin per policy." }));
    },
  });

  /* rl-events: what the reward paid for and punished, by label. */
  function drawEvents(host, ctx, m, comp, tip) {
    if (!d3) return;
    var W = width(host), rowH = 30, labW = Math.min(170, 18 + 7.5 * m.policies.reduce(function (a, p) { return Math.max(a, p.name.length); }, 4)), padR = 60;
    var negMax = 0, posMax = 0;
    m.policies.forEach(function (p) {
      var pos = 0, neg = 0;
      comp.labels.forEach(function (l) { var c = comp.per[p.name][l]; if (!c) return; if (c.reward > 0) pos += c.reward; else neg += -c.reward; });
      negMax = Math.max(negMax, neg); posMax = Math.max(posMax, pos);
    });
    var span = Math.max(1e-9, negMax + posMax);
    var k = (W - labW - padR - 58) / span;
    var x0 = labW + 52 + negMax * k;
    var Hh = 18 + m.policies.length * rowH;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img").attr("aria-label", "reward by event label per policy: lost to the left of zero, earned to the right");
    svg.append("line").attr("class", "zero").attr("x1", x0).attr("x2", x0).attr("y1", 6).attr("y2", Hh - 4);
    svg.append("text").attr("class", "tick").attr("x", x0).attr("y", 12).attr("text-anchor", "middle").text("0");
    m.policies.forEach(function (p, pi) {
      var y = 18 + pi * rowH;
      var g = svg.append("g").attr("class", "rle-bar").attr("data-policy", p.name);
      g.append("circle").attr("cx", 5).attr("cy", y + 10).attr("r", 4).attr("fill", p.color);
      g.append("text").attr("class", "lab").attr("x", 14).attr("y", y + 14).text(trunc(p.name, Math.floor((labW - 14) / 7.5)));
      var xr = x0, xl = x0, pos = 0, neg = 0, segs = 0;
      comp.labels.forEach(function (l, li) {
        var c = comp.per[p.name][l];
        if (!c || !c.reward) return;
        segs++;
        var w = Math.abs(c.reward) * k, positive = c.reward > 0, x;
        if (positive) { x = xr; xr += w; pos += c.reward; } else { xl -= w; x = xl; neg += c.reward; }
        var op = 0.85 - 0.5 * (li / Math.max(1, comp.labels.length - 1));
        var seg = g.append("rect").attr("class", "rle-seg").attr("data-policy", p.name).attr("data-label", l).attr("data-events", c.events).attr("data-reward", c.reward)
          .attr("x", x).attr("y", y + 2).attr("width", Math.max(1, w - 1)).attr("height", 16).attr("fill", positive ? "var(--good)" : "var(--bad)").attr("fill-opacity", op).style("cursor", "default");
        seg.append("title").text(l + " · " + Math.round(c.events * 100) / 100 + " events · " + signed(c.reward));
        seg.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: p.name + " · " + l }, { text: Math.round(c.events * 100) / 100 + " events · reward " + signed(c.reward) + " · " + (positive ? "paid for" : "punished") }]); }).on("pointerleave", tip.hide);
        if (w >= 7 * l.length + 8) g.append("text").attr("class", "lab").attr("x", x + w / 2).attr("y", y + 14).attr("text-anchor", "middle").attr("fill", "#fff").style("pointer-events", "none").text(l);
      });
      g.attr("data-segments", segs);
      if (neg) g.append("text").attr("class", "tick rle-tot neg").attr("x", xl - 4).attr("y", y + 14).attr("text-anchor", "end").text(signed(neg));
      if (pos) g.append("text").attr("class", "tick rle-tot pos").attr("x", xr + 4).attr("y", y + 14).text(signed(pos));
      if (!segs) g.append("text").attr("class", "tick").attr("x", x0 + 6).attr("y", y + 14).text("no reward attributed");
    });
  }

  AgentDiff.block({
    id: "rl-events",
    title: "Reward composition",
    question: "Which behaviours the reward paid for, and which it punished — per policy, by event label.",
    group: "training",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); if (!m.episodes.length) return 0; return composition(m).labels.length ? 0.76 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx), comp = composition(m);
      var root = H("div", { class: "rl rl-events" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(policyChips(H, m, function (p) {
        var pos = 0, neg = 0;
        comp.labels.forEach(function (l) { var c = comp.per[p.name][l]; if (!c) return; if (c.reward > 0) pos += c.reward; else neg += c.reward; });
        return "earned " + signed(pos) + " · lost " + signed(neg) + " · net " + signed(pos + neg);
      }));
      var host = H("div", { class: "rl-chart" });
      root.appendChild(responsive(host, function () { drawEvents(host, ctx, m, comp, tip); }, "rl-events"));
      var details = H("details", { class: "rl-details" }, [H("summary", { text: "every label, both policies" })]);
      var t = H("table", { class: "rl-table" });
      var head = [H("th", { text: "label" })];
      m.policies.forEach(function (p) { head.push(H("th", { text: p.name + " events" })); head.push(H("th", { text: p.name + " reward" })); });
      t.appendChild(H("tr", null, head));
      comp.labels.forEach(function (l) {
        var tr = H("tr", { "data-label": l }, [H("td", { text: l })]);
        m.policies.forEach(function (p) {
          var c = comp.per[p.name][l];
          tr.appendChild(H("td", { class: "num", text: c ? String(Math.round(c.events * 100) / 100) : "—" }));
          tr.appendChild(H("td", { class: "num", text: c ? signed(c.reward) : "—" }));
        });
        t.appendChild(tr);
      });
      details.appendChild(t);
      details.appendChild(H("p", { class: "rl-note", text: "Basis: " + comp.basis + ". Reward source: " + (m.reward_source || "unknown") + "." }));
      root.appendChild(details);
    },
  });

  /* rl-preferences: the preference pairs as a table and as JSONL. */
  function jsonl(pairs) { return pairs.map(function (p) { return JSON.stringify(p); }).join("\n"); }
  function copyText(text, done) {
    var ok = false;
    try {
      if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
        global.navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallback()); });
        return;
      }
    } catch (err) { /* fall through */ }
    done(fallback());
    function fallback() {
      try {
        var ta = document.createElement("textarea");
        ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.left = "-9999px";
        document.body.appendChild(ta); ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (err) { ok = false; }
      return ok;
    }
  }

  AgentDiff.block({
    id: "rl-preferences",
    title: "Preference dataset",
    question: "On every task, which policy's episode is preferred, on what basis, by how much — the pairs a preference model would train on.",
    group: "training",
    size: "normal",
    relevance: function (ctx) { return model(ctx).preferences.length ? 0.75 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx), pairs = m.preferences;
      var root = H("div", { class: "rl rl-prefs" });
      el.appendChild(root);
      var copyBtn = H("button", { class: "rlp-copy", text: "copy JSONL", title: "copy the pairs as JSON lines", onclick: function () {
        copyText(jsonl(pairs), function (ok) {
          copyBtn.textContent = ok ? "copied " + pairs.length + " lines" : "copy failed — open the JSONL below";
          setTimeout(function () { copyBtn.textContent = "copy JSONL"; }, 1800);
        });
      } });
      function who(v) { return v && typeof v === "object" ? (v.agent || v.name || (v.side ? "side " + v.side : "")) : v === null || v === undefined ? "" : String(v); }
      pairs = pairs.map(function (p) {
        var out = {}; Object.keys(p).forEach(function (k) { if (k !== "chosen" && k !== "rejected") out[k] = p[k]; });
        out.chosen = who(p.chosen); out.rejected = who(p.rejected);
        if (p.chosen && typeof p.chosen === "object" && p.chosen.basis && out.basis === undefined) out.basis = p.chosen.basis;
        if (!isNum(out.margin) && p.estimate && isNum(p.estimate.margin)) out.margin = p.estimate.margin;
        return out;
      });
      var counts = {};
      pairs.forEach(function (p) { if (p.chosen) counts[p.chosen] = (counts[p.chosen] || 0) + 1; });
      root.appendChild(H("div", { class: "rl-bar" }, [
        H("span", { class: "rl-chip rlp-count", text: pairs.length + " pair" + (pairs.length === 1 ? "" : "s") }),
        H("span", { class: "rl-chip", text: Object.keys(counts).map(function (n) { return n + " chosen " + counts[n] + "×"; }).join(" · ") }),
        copyBtn,
      ]));
      var t = H("table", { class: "rl-table rlp-table" });
      t.appendChild(H("tr", null, ["task", "chosen", "rejected", "basis", "margin"].map(function (h) { return H("th", { text: h }); })));
      pairs.forEach(function (p) {
        var tid = p.task_id || p.task || "";
        var tr = H("tr", { class: "rlp-row", "data-task": tid || null, onclick: function () { selectTask(ctx, tid); } }, [
          H("td", { class: "mono", text: short(tid) || "—" }),
          H("td", null, [H("i", { style: { background: colorFor(m, p.chosen) } }), H("span", { text: p.chosen || "—" })]),
          H("td", null, [H("i", { style: { background: colorFor(m, p.rejected) } }), H("span", { text: p.rejected || "—" })]),
          H("td", { class: "basis", text: p.basis === undefined || p.basis === null ? "—" : String(p.basis) }),
          H("td", { class: "num", text: isNum(p.margin) ? signed(p.margin) : "—" }),
        ]);
        t.appendChild(tr);
      });
      root.appendChild(t);
      var details = H("details", { class: "rl-details" }, [H("summary", { text: "the JSONL" }), H("pre", { class: "rlp-jsonl", text: jsonl(pairs) })]);
      root.appendChild(details);
    },
  });

  /* rl-policy-delta: Δ mean return per task, sorted, with the sign count. */
  AgentDiff.block({
    id: "rl-policy-delta",
    title: "Policy delta",
    question: "Per task, how much more the second policy returned than the first — and on how many tasks it came out ahead.",
    group: "training",
    size: "normal",
    relevance: function (ctx) { var m = model(ctx); return m.tasks.some(function (t) { return isNum(t.delta); }) ? 0.74 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var A = m.policies[0], B = m.policies[1];
      var root = H("div", { class: "rl rl-delta" });
      el.appendChild(root);
      var tasks = m.tasks.filter(function (t) { return isNum(t.delta); }).slice().sort(function (x, y) { return Math.abs(y.delta) - Math.abs(x.delta); });
      var up = tasks.filter(function (t) { return t.sign > 0; }).length, down = tasks.filter(function (t) { return t.sign < 0; }).length, tie = tasks.length - up - down;
      var lead = up >= down ? B : A, leadN = up >= down ? up : down, other = up >= down ? A : B, otherN = up >= down ? down : up;
      root.appendChild(H("p", { class: "rld-sum", "data-up": up, "data-down": down, "data-tie": tie,
        text: (lead ? lead.name : "B") + " better on " + leadN + " of " + tasks.length + " task" + (tasks.length === 1 ? "" : "s") + (otherN ? " · " + (other ? other.name : "A") + " on " + otherN : "") + (tie ? " · " + tie + " tied" : "") + "." }));
      var maxAbs = tasks.reduce(function (a, t) { return Math.max(a, Math.abs(t.delta)); }, 0) || 1;
      var list = H("div", { class: "rld-list", role: "list" });
      tasks.forEach(function (t) {
        var pct = 50 * Math.abs(t.delta) / maxAbs;
        var fill = H("div", { class: "rld-fill", style: t.delta >= 0 ? { left: "50%", width: pct + "%", background: B ? B.color : "var(--good)" } : { right: "50%", width: pct + "%", background: A ? A.color : "var(--bad)" } });
        var row = H("div", { class: "rld-row", role: "listitem", tabindex: "0", "data-task": t.id, "data-delta": t.delta, "data-sign": t.sign, "aria-current": ctx.task === t.id ? "true" : "false",
          title: (A ? A.name + " " + signed(t.per[A.name].mean_return) : "") + (B ? " · " + B.name + " " + signed(t.per[B.name].mean_return) : "") + " · click to open this task",
          onclick: function () { selectTask(ctx, t.id); },
          onkeydown: function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); selectTask(ctx, t.id); } },
        }, [
          H("span", { class: "rld-lab", text: short(t.id) }),
          H("div", { class: "rld-bar" }, [fill]),
          H("span", { class: "rld-val", text: signed(t.delta) }),
        ]);
        list.appendChild(row);
      });
      root.appendChild(list);
      root.appendChild(H("p", { class: "rl-note", text: "Δ = mean return of " + (B ? B.name : "B") + " − " + (A ? A.name : "A") + " on the task, over its episodes; sorted by |Δ|." }));
    },
  });
})(typeof window !== "undefined" ? window : this);
