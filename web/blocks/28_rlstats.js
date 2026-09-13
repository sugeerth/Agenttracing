/* AgentDiff blocks — two policies from few episodes, read the way the RL
 * literature asks for rather than as a mean with a normal interval.
 *
 * `aggregate.rl.stats` (deepcompare/rlstats.py) carries the small-sample
 * toolkit over the same episodes the rest of the Training view draws:
 * IQM, median, mean and the optimality gap, each with a stratified
 * bootstrap interval; the performance profiles with their bands; and the
 * probability that a random run of one policy beats a random run of the
 * other on the same task. These blocks draw exactly those numbers:
 *
 *   rl-stats-aggregate   four metric rows on one shared score axis, both
 *                        policies' point and interval per row — overlap is
 *                        the thing the reader is meant to see first
 *   rl-stats-profile     fraction of runs clearing τ, for every τ, with the
 *                        bootstrap band; says whether the curves cross
 *   rl-stats-improvement P(B > A) with its interval, and the per-task
 *                        breakdown the average hides (hover for the task's
 *                        own runs, click to open it)
 *
 * Nothing here is recomputed in the browser: every point, bound and
 * sentence comes from the JSON, so the page and the engine cannot drift.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".rls{position:relative}",
      ".rls svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rls .lab{font-size:var(--fs-xs);fill:var(--ink-2)}",
      ".rls .lab.dim{fill:var(--ink-3)}",
      ".rls .tick{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".rls .val{font-size:var(--fs-xs);font-variant-numeric:tabular-nums;font-family:var(--mono)}",
      ".rls .zero{stroke:var(--rule-2)}.rls .rule{stroke:var(--rule)}",
      ".rls .hit{cursor:default}",
      ".rls-head{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".rls-bar{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".rls-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
      ".rls-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".rls-chip b{color:var(--ink);font-weight:600}",
      ".rls-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:90ch;line-height:1.5}",
      ".rls-figure{display:flex;gap:6px 16px;flex-wrap:wrap;align-items:baseline;margin:0 0 6px}",
      ".rls-big{font-size:var(--fs-l);color:var(--ink);font-variant-numeric:tabular-nums;font-weight:600}",
      ".rls-ci{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".rls-rows{margin-top:10px}",
      ".rls-row{display:grid;grid-template-columns:minmax(74px,1fr) minmax(110px,2.4fr) 46px;gap:10px;align-items:center;padding:3px 2px;border-radius:5px;cursor:pointer;font-size:var(--fs-xs);color:var(--ink-2)}",
      ".rls-row:hover,.rls-row:focus-visible{background:var(--surface-2);outline:none}",
      ".rls-row[aria-current=true] .rls-lab{color:var(--ink);font-weight:600}",
      ".rls-lab{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:var(--mono)}",
      ".rls-track{position:relative;height:10px;background:var(--surface-2);border-radius:2px}",
      ".rls-track:before{content:'';position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--rule-2)}",
      ".rls-fill{position:absolute;top:0;height:10px;border-radius:2px}",
      ".rls-val{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink)}",
      ".rls-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
      ".rls-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".rls-table td{padding:3px 10px 3px 0;white-space:nowrap;color:var(--ink-2)}",
      ".rls-table td.num{text-align:right;font-family:var(--mono)}",
      ".rls-details{margin-top:8px;font-size:var(--fs-xs)}",
      ".rls-details summary{cursor:pointer;color:var(--ink-2)}",
      ".rls-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:300px}",
      ".rls-tip b{color:var(--ink)}",
      ".rls-tip .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}",
      "@media (max-width:640px){.rls-row{grid-template-columns:minmax(58px,1fr) minmax(80px,2fr) 44px}}",
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
  function pct(v) { return isNum(v) ? Math.round(v * 100) + "%" : "—"; }
  function short(id) { return String(id || "").replace(/^(rl|t)\d+_/, "").replace(/_/g, " "); }
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function hostWidth(host) {
    var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
    return Math.max(280, Math.min(1400, w || 320));
  }
  function responsive(host, draw, key) {
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, key);
    draw();
    return host;
  }
  function selectTask(ctx, id) {
    var fn = ctx && typeof ctx.selectTask === "function" ? ctx.selectTask
      : AgentDiff._internals && typeof AgentDiff._internals.selectTask === "function" ? AgentDiff._internals.selectTask : null;
    if (fn && id) fn(id);
  }
  //: a linear map from the data domain onto pixels; a flat domain maps to r0
  function scale(d0, d1, r0, r1) {
    var span = d1 - d0;
    if (!(Math.abs(span) > 1e-12)) return function () { return r0; };
    return function (v) { return r0 + (v - d0) * (r1 - r0) / span; };
  }
  //: round tick values at roughly `count` intervals, so an axis never invents precision
  function niceTicks(lo, hi, count) {
    if (!(hi > lo)) return [lo];
    var raw = (hi - lo) / Math.max(1, count);
    var step = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var err = raw / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var out = [], t = Math.ceil(lo / step) * step;
    for (var guard = 0; t <= hi + step * 1e-9 && guard < 40; guard++, t += step) out.push(Math.round(t / step) * step);
    return out.length ? out : [lo, hi];
  }

  function tooltip(root) {
    var tip = document.createElement("div");
    tip.className = "rls-tip";
    tip.hidden = true;
    root.appendChild(tip);
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
        if (x + 300 > r.width) x = Math.max(0, evt.clientX - r.left - 310);
        tip.style.left = x + "px";
        tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  // --------------------------------------------------------------- model

  /* The JSON as the blocks want it: the policies in the aggregate's order,
   * each given the page's own A/B colour when it is one of the selected
   * pair's sides, so a policy is the same colour here as in every other
   * Training block. Nothing is recomputed — `s` is `aggregate.rl.stats`. */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {}, report = ctx.report || null;
    if (cache && cache.agg === agg && cache.report === report) return cache.m;
    var m = null;
    try { m = build(agg, report); } catch (err) { console.warn("AgentDiff rlstats: model failed", err); m = null; }
    cache = { agg: agg, report: report, m: m };
    return m;
  }

  function build(agg, report) {
    var rl = agg && agg.rl && typeof agg.rl === "object" ? agg.rl : null;
    var s = rl && rl.stats && typeof rl.stats === "object" ? rl.stats : null;
    if (!s || !s.measurable || !Array.isArray(s.policies) || !s.policies.length) return null;
    var an = report && report.a && report.a.agent && report.a.agent.name;
    var bn = report && report.b && report.b.agent && report.b.agent.name;
    var sideOf = {};
    s.policies.forEach(function (n) { sideOf[n] = n === an ? "a" : n === bn ? "b" : null; });
    var free = ["a", "b"].filter(function (side) {
      return s.policies.every(function (n) { return sideOf[n] !== side; });
    });
    s.policies.forEach(function (n) { if (!sideOf[n] && free.length) sideOf[n] = free.shift(); });
    var policies = s.policies.map(function (n, i) {
      return {
        name: n, side: sideOf[n],
        color: sideOf[n] === "a" ? "var(--a)" : sideOf[n] === "b" ? "var(--b)" : i === 2 ? "var(--warn)" : "var(--ink-3)",
        n: (s.n && isNum(s.n[n])) ? s.n[n] : null,
        agg: (s.aggregates && s.aggregates[n]) || {},
      };
    });
    return {
      s: s, policies: policies, tasks: Array.isArray(s.tasks) ? s.tasks : [],
      byTask: s.by_task && typeof s.by_task === "object" ? s.by_task : {},
      profile: s.profile && typeof s.profile === "object" ? s.profile : null,
      improvement: s.improvement && typeof s.improvement === "object" ? s.improvement : null,
      advisory: s.advisory && typeof s.advisory === "object" ? s.advisory : null,
      metric: s.metric_label || s.metric || "score",
      higher: s.higher_is_better !== false,
      target: s.target && typeof s.target === "object" ? s.target : null,
      samples: s.bootstrap && isNum(s.bootstrap.samples) ? s.bootstrap.samples : null,
    };
  }

  function legend(H, m, text) {
    var bar = H("div", { class: "rls-bar" });
    m.policies.forEach(function (p) {
      bar.appendChild(H("span", { class: "rls-chip", "data-policy": p.name }, [
        H("i", { style: { background: p.color } }),
        H("b", { text: p.name }),
        H("span", { text: " · " + text(p) }),
      ]));
    });
    return bar;
  }

  function runsOn(m, task, policy) {
    var cell = m.byTask[task];
    var runs = cell && Array.isArray(cell[policy]) ? cell[policy] : [];
    return runs.slice().sort(function (x, y) { return x - y; });
  }

  function taskLines(m, task) {
    var lines = [{ b: true, text: short(task) }];
    m.policies.forEach(function (p) {
      var runs = runsOn(m, task, p.name);
      lines.push({ mono: true, text: p.name + ": " + (runs.length ? runs.map(function (v) { return num(v); }).join("  ") : "no runs") });
    });
    lines.push({ text: "click to open this task" });
    return lines;
  }

  //: the advisory sentence, never omitted — a wide interval must not read as a finding
  function advisoryNote(H, m) {
    var message = m.advisory && m.advisory.message ? String(m.advisory.message) : "";
    if (!message) return null;
    return H("p", { class: "rls-note rls-advisory", text: message });
  }

  // ------------------------------------------------ the aggregate interval plot

  var ROWS = [
    { key: "iqm", label: "IQM", hint: "the mean of the middle 50% of runs — the least moved by one exceptional episode" },
    { key: "median", label: "median", hint: "the middle run; robust, but it throws away half the sample" },
    { key: "mean", label: "mean", hint: "every run weighted equally, so one outlier carries it" },
    { key: "optimality_gap", label: "optimality gap", sub: "lower is better", hint: "mean shortfall against the target — lower is better" },
  ];

  function drawAggregate(host, ctx, m, tip) {
    host.innerHTML = "";
    var H = ctx.h, S = ctx.svg;
    var W = hostWidth(host);
    var padL = W < 420 ? 78 : 104, padR = 10, padT = 8, padB = 24;
    var rowH = 46, lineGap = 18;
    var lo = Infinity, hi = -Infinity;
    ROWS.forEach(function (row) {
      m.policies.forEach(function (p) {
        var cell = p.agg[row.key];
        if (!cell) return;
        [cell.point, cell.lo, cell.hi].forEach(function (v) {
          if (!isNum(v)) return;
          lo = Math.min(lo, v); hi = Math.max(hi, v);
        });
      });
    });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (Math.abs(hi - lo) < 1e-9) { lo -= 1; hi += 1; }
    // the axis starts where the data starts: one row's padding, never at zero
    var padding = (hi - lo) * 0.06;
    lo -= padding; hi += padding;
    var height = padT + ROWS.length * rowH + padB;
    var x = scale(lo, hi, padL, W - padR);
    var node = S("svg", {
      viewBox: "0 0 " + W + " " + height, width: W, height: height, role: "img",
      "aria-label": "Aggregate " + m.metric + " per policy: " + ROWS.map(function (r) { return r.label; }).join(", ")
        + ", each with a stratified bootstrap interval",
    });

    if (lo < 0 && hi > 0) {
      node.appendChild(S("line", { class: "zero", x1: x(0), x2: x(0), y1: padT, y2: padT + ROWS.length * rowH, "stroke-width": 1 }));
    }
    niceTicks(lo, hi, W < 480 ? 3 : 5).forEach(function (t) {
      if (t < lo || t > hi) return;
      node.appendChild(S("line", { class: "rule", x1: x(t), x2: x(t), y1: padT + ROWS.length * rowH, y2: padT + ROWS.length * rowH + 4, "stroke-width": 1 }));
      node.appendChild(S("text", { class: "tick", x: x(t), y: padT + ROWS.length * rowH + 16, "text-anchor": "middle", text: num(t) }));
    });

    ROWS.forEach(function (row, ri) {
      var top = padT + ri * rowH;
      var mid = top + rowH / 2;
      var g = S("g", { class: "rls-metric", "data-metric": row.key });
      node.appendChild(g);
      g.appendChild(S("text", {
        class: "lab", x: padL - 10, y: row.sub ? mid : mid + 4, "text-anchor": "end",
        text: trunc(row.label, W < 420 ? 11 : 16),
      }));
      if (row.sub) {
        g.appendChild(S("text", { class: "tick", x: padL - 10, y: mid + 13, "text-anchor": "end", text: row.sub }));
      }
      m.policies.forEach(function (p, pi) {
        var cell = p.agg[row.key];
        if (!cell || !isNum(cell.point)) return;
        var y = top + (rowH - (m.policies.length - 1) * lineGap) / 2 + pi * lineGap;
        var a = isNum(cell.lo) ? x(cell.lo) : x(cell.point);
        var b = isNum(cell.hi) ? x(cell.hi) : x(cell.point);
        var line = S("g", { class: "rls-int hit", "data-metric": row.key, "data-policy": p.name, "data-point": cell.point });
        g.appendChild(line);
        line.appendChild(S("line", { x1: a, x2: b, y1: y, y2: y, stroke: p.color, "stroke-width": 2, "stroke-opacity": 0.42, "stroke-linecap": "round" }));
        [a, b].forEach(function (px) {
          line.appendChild(S("line", { x1: px, x2: px, y1: y - 4, y2: y + 4, stroke: p.color, "stroke-width": 1.5, "stroke-opacity": 0.42 }));
        });
        line.appendChild(S("circle", { cx: x(cell.point), cy: y, r: 3.6, fill: p.color }));
        line.appendChild(S("text", {
          class: "val", x: clamp(x(cell.point), padL + 14, W - padR - 14), y: y - 6,
          "text-anchor": "middle", fill: p.color, text: num(cell.point),
        }));
        line.appendChild(S("rect", { x: Math.min(a, b) - 6, y: y - 10, width: Math.abs(b - a) + 12, height: 20, fill: "transparent" }));
        var lines = [
          { b: true, text: p.name + " · " + row.label },
          { mono: true, text: num(cell.point) + "  [" + num(cell.lo) + ", " + num(cell.hi) + "]" },
          { text: cell.degenerate && cell.reason ? cell.reason : row.hint },
        ];
        line.addEventListener("pointermove", function (evt) { tip.show(evt, lines); });
        line.addEventListener("pointerleave", tip.hide);
      });
    });
    host.appendChild(node);
  }

  //: do the two policies' intervals on `key` overlap? null when there is no pair
  function overlapOn(m, key) {
    if (m.policies.length !== 2) return null;
    var a = m.policies[0].agg[key], b = m.policies[1].agg[key];
    if (!a || !b || !isNum(a.lo) || !isNum(a.hi) || !isNum(b.lo) || !isNum(b.hi)) return null;
    return a.lo <= b.hi && b.lo <= a.hi;
  }

  AgentDiff.block({
    id: "rl-stats-aggregate",
    title: "Aggregate score, with intervals",
    question: "IQM, median, mean and optimality gap for each policy — and whether the bootstrap intervals over these runs actually separate them.",
    group: "training",
    size: "wide",
    relevance: function (ctx) { return model(ctx) ? 0.795 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rls rls-aggregate" });
      el.appendChild(root);
      var tip = tooltip(root);

      var overlap = overlapOn(m, "iqm");
      var head;
      if (overlap === null) {
        head = "IQM " + m.metric + " per policy, each with its stratified bootstrap interval.";
      } else {
        var a = m.policies[0], b = m.policies[1];
        var ahead = a.agg.iqm.point > b.agg.iqm.point ? a : b;
        head = overlap
          ? "The IQM intervals overlap: these runs do not separate " + a.name + " from " + b.name + ". "
            + "That is not the same as the policies being equal — it is the sample saying so."
          : "The IQM intervals do not overlap: over these runs " + ahead.name + " is ahead at every resample.";
      }
      root.appendChild(H("p", { class: "rls-head rls-verdict", "data-overlap": overlap === null ? "" : String(overlap), text: head }));
      root.appendChild(legend(H, m, function (p) {
        var iqm = p.agg.iqm || {};
        return "IQM " + num(iqm.point) + " [" + num(iqm.lo) + ", " + num(iqm.hi) + "] · n=" + (isNum(p.n) ? p.n : "?");
      }));

      var chart = H("div", { class: "rls-chart" });
      root.appendChild(responsive(chart, function () { drawAggregate(chart, ctx, m, tip); }, "rl-stats-aggregate"));

      var details = H("details", { class: "rls-details" }, [H("summary", { text: "every number, both policies" })]);
      var table = H("table", { class: "rls-table" });
      var head2 = [H("th", { text: "metric" })];
      m.policies.forEach(function (p) { head2.push(H("th", { text: p.name })); });
      table.appendChild(H("tr", null, head2));
      ROWS.forEach(function (row) {
        var tr = H("tr", { "data-metric": row.key }, [H("td", { text: row.label })]);
        m.policies.forEach(function (p) {
          var cell = p.agg[row.key] || {};
          tr.appendChild(H("td", { class: "num", text: num(cell.point) + " [" + num(cell.lo) + ", " + num(cell.hi) + "]" }));
        });
        table.appendChild(tr);
      });
      details.appendChild(table);
      root.appendChild(details);

      var target = m.target || {};
      root.appendChild(H("p", { class: "rls-note", text:
        "Score: " + m.metric + (m.higher ? ", higher is better. " : ", lower is better. ")
        + "The interval is a percentile bootstrap over " + (isNum(m.samples) ? m.samples : "the") + " resamples, "
        + "stratified by task: runs are redrawn with replacement within each task, and every task keeps its own run count. "
        + (target.note ? target.note.charAt(0).toUpperCase() + target.note.slice(1) + "." : "") }));
      var advisory = advisoryNote(H, m);
      if (advisory) root.appendChild(advisory);
    },
  });

  // ------------------------------------------------------- performance profile

  function drawProfile(host, ctx, m, tip) {
    host.innerHTML = "";
    var H = ctx.h, S = ctx.svg;
    var profile = m.profile;
    var taus = profile.taus || [];
    var W = hostWidth(host);
    var padL = 44, padR = 12, padT = 10, padB = 26;
    var height = Math.max(160, Math.min(240, Math.round(W * 0.34))) + padT + padB;
    var plotBottom = height - padB, plotTop = padT;
    var lo = taus.length ? taus[0] : 0, hi = taus.length ? taus[taus.length - 1] : 1;
    var x = scale(lo, hi, padL, W - padR);
    var y = scale(0, 1, plotBottom, plotTop);
    var node = S("svg", {
      viewBox: "0 0 " + W + " " + height, width: W, height: height, role: "img",
      "aria-label": "Performance profile: " + (profile.rule || "fraction of runs reaching τ") + ", per policy, with a bootstrap band",
    });

    [0, 0.5, 1].forEach(function (f) {
      node.appendChild(S("line", { class: "rule", x1: padL, x2: W - padR, y1: y(f), y2: y(f), "stroke-width": 1, "stroke-dasharray": f === 0.5 ? "2 4" : null }));
      node.appendChild(S("text", { class: "tick", x: padL - 6, y: y(f) + 4, "text-anchor": "end", text: Math.round(f * 100) + "%" }));
    });
    if (lo < 0 && hi > 0) node.appendChild(S("line", { class: "zero", x1: x(0), x2: x(0), y1: plotTop, y2: plotBottom, "stroke-width": 1 }));
    niceTicks(lo, hi, W < 480 ? 3 : 6).forEach(function (t) {
      if (t < lo || t > hi) return;
      node.appendChild(S("text", { class: "tick", x: x(t), y: plotBottom + 16, "text-anchor": "middle", text: num(t) }));
    });
    (profile.crossings || []).forEach(function (t) {
      if (t < lo || t > hi) return;
      node.appendChild(S("line", { class: "rls-cross", x1: x(t), x2: x(t), y1: plotTop, y2: plotBottom, stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "1 3" }));
    });

    m.policies.forEach(function (p) {
      var rows = (profile.curves || {})[p.name];
      if (!Array.isArray(rows) || !rows.length) return;
      var up = rows.map(function (r) { return x(r[0]) + "," + y(r[3]); });
      var down = rows.slice().reverse().map(function (r) { return x(r[0]) + "," + y(r[2]); });
      node.appendChild(S("polygon", { class: "rls-band", "data-policy": p.name, points: up.concat(down).join(" "), fill: p.color, "fill-opacity": 0.15, stroke: "none" }));
      node.appendChild(S("polyline", {
        class: "rls-curve", "data-policy": p.name, fill: "none", stroke: p.color, "stroke-width": 1.8,
        points: rows.map(function (r) { return x(r[0]) + "," + y(r[1]); }).join(" "),
      }));
    });

    var tracker = S("line", { x1: 0, x2: 0, y1: plotTop, y2: plotBottom, stroke: "var(--ink-3)", "stroke-width": 1, opacity: 0 });
    node.appendChild(tracker);
    var surface = S("rect", { class: "hit", x: padL, y: plotTop, width: Math.max(1, W - padR - padL), height: Math.max(1, plotBottom - plotTop), fill: "transparent" });
    node.appendChild(surface);
    surface.addEventListener("pointermove", function (evt) {
      if (!taus.length) return;
      var box = node.getBoundingClientRect();
      var px = (evt.clientX - box.left) * (W / Math.max(1, box.width));
      var best = 0, bestD = Infinity;
      taus.forEach(function (t, i) { var d = Math.abs(x(t) - px); if (d < bestD) { bestD = d; best = i; } });
      tracker.setAttribute("x1", x(taus[best]));
      tracker.setAttribute("x2", x(taus[best]));
      tracker.setAttribute("opacity", 0.5);
      var lines = [{ b: true, text: "τ = " + num(taus[best]) }];
      m.policies.forEach(function (p) {
        var row = ((profile.curves || {})[p.name] || [])[best];
        if (!row) return;
        lines.push({ mono: true, text: p.name + ": " + pct(row[1]) + " of runs  [" + pct(row[2]) + ", " + pct(row[3]) + "]" });
      });
      tip.show(evt, lines);
    });
    surface.addEventListener("pointerleave", function () { tracker.setAttribute("opacity", 0); tip.hide(); });
    host.appendChild(node);
  }

  AgentDiff.block({
    id: "rl-stats-profile",
    title: "Performance profile",
    question: "For every threshold τ, what fraction of each policy's runs clears it — a whole distribution rather than one number, and whether the two curves cross.",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var m = model(ctx);
      if (!m || !m.profile || !Array.isArray(m.profile.taus) || m.profile.taus.length < 2) return 0;
      return 0.794;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx), profile = m.profile;
      var root = H("div", { class: "rls rls-profile" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(H("p", { class: "rls-head rls-reading",
        "data-crossings": (profile.crossings || []).length,
        text: (profile.reading ? profile.reading.charAt(0).toUpperCase() + profile.reading.slice(1) : "") + "." }));
      root.appendChild(legend(H, m, function (p) { return "n=" + (isNum(p.n) ? p.n : "?") + " runs"; }));
      var chart = H("div", { class: "rls-chart" });
      root.appendChild(responsive(chart, function () { drawProfile(chart, ctx, m, tip); }, "rl-stats-profile"));
      root.appendChild(H("p", { class: "rls-note", text:
        "Vertical: the " + (profile.rule || "fraction of runs reaching τ") + ". Horizontal: τ in " + m.metric + ", running from "
        + "the lowest to the highest score any run reached — the axis starts at the data, not at zero, and the zero line is drawn "
        + "where it falls. "
        + "The band is the stratified bootstrap of the same fraction. A curve everywhere above the other is ahead at every "
        + "threshold; a crossing (dotted) means the ordering depends on where the bar is set." }));
    },
  });

  // --------------------------------------------------- probability of improvement

  function drawImprovement(host, ctx, m, tip) {
    host.innerHTML = "";
    var S = ctx.svg;
    var imp = m.improvement;
    var W = hostWidth(host);
    var padL = 10, padR = 10, height = 54;
    var x = scale(0, 1, padL, W - padR);
    var color = m.policies.length === 2 ? m.policies[1].color : "var(--ink-2)";
    var node = S("svg", {
      viewBox: "0 0 " + W + " " + height, width: W, height: height, role: "img",
      "aria-label": "Probability that a random run of " + imp.b + " beats a random run of " + imp.a
        + " on the same task: " + pct(imp.point) + ", bootstrap interval " + pct(imp.lo) + " to " + pct(imp.hi),
    });
    var y = 20;
    node.appendChild(S("line", { class: "rule", x1: padL, x2: W - padR, y1: y, y2: y, "stroke-width": 1 }));
    node.appendChild(S("line", { class: "zero", x1: x(0.5), x2: x(0.5), y1: y - 13, y2: y + 13, "stroke-width": 1.5 }));
    node.appendChild(S("text", { class: "tick", x: x(0.5), y: height - 5, "text-anchor": "middle", text: "50% · a coin flip" }));
    node.appendChild(S("text", { class: "tick", x: padL, y: height - 5, "text-anchor": "start", text: "0%" }));
    node.appendChild(S("text", { class: "tick", x: W - padR, y: height - 5, "text-anchor": "end", text: "100%" }));
    if (isNum(imp.lo) && isNum(imp.hi)) {
      node.appendChild(S("line", { class: "rls-int", x1: x(imp.lo), x2: x(imp.hi), y1: y, y2: y, stroke: color, "stroke-width": 6, "stroke-opacity": 0.3, "stroke-linecap": "round" }));
    }
    if (isNum(imp.point)) node.appendChild(S("circle", { class: "rls-point", cx: x(imp.point), cy: y, r: 4.5, fill: color }));
    host.appendChild(node);
  }

  AgentDiff.block({
    id: "rl-stats-improvement",
    title: "Probability of improvement",
    question: "How often a random run of one policy beats a random run of the other on the same task — which is not the same question as whose mean is higher.",
    group: "training",
    size: "normal",
    relevance: function (ctx) {
      var m = model(ctx);
      return m && m.improvement && m.improvement.measurable && isNum(m.improvement.point) ? 0.793 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx), imp = m.improvement;
      var root = H("div", { class: "rls rls-improve" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(H("div", { class: "rls-figure" }, [
        H("span", { class: "rls-big", "data-p": imp.point, text: "P(" + imp.b + " > " + imp.a + ") = " + pct(imp.point) }),
        H("span", { class: "rls-ci", text: "[" + pct(imp.lo) + ", " + pct(imp.hi) + "]" }),
      ]));
      var chart = H("div", { class: "rls-chart" });
      root.appendChild(responsive(chart, function () { drawImprovement(chart, ctx, m, tip); }, "rl-stats-improvement"));
      root.appendChild(H("p", { class: "rls-head rls-reading", text:
        imp.reading ? imp.reading.charAt(0).toUpperCase() + imp.reading.slice(1) : "" }));

      var per = imp.per_task && typeof imp.per_task === "object" ? imp.per_task : {};
      var ids = Object.keys(per).sort(function (p, q) { return per[p] - per[q]; });
      if (ids.length) {
        var rows = H("div", { class: "rls-rows", role: "list" });
        ids.forEach(function (id) {
          var p = per[id];
          var worse = p < 0.5;
          var fill = H("div", { class: "rls-fill", style: worse
            ? { right: "50%", width: (100 * (0.5 - p)) + "%", background: m.policies[0] ? m.policies[0].color : "var(--bad)" }
            : { left: "50%", width: (100 * (p - 0.5)) + "%", background: m.policies[1] ? m.policies[1].color : "var(--good)" } });
          var row = H("div", {
            class: "rls-row", role: "listitem", tabindex: "0", "data-task": id, "data-p": p,
            "aria-current": ctx.task === id ? "true" : "false",
            "aria-label": short(id) + ": " + pct(p),
            onclick: function () { selectTask(ctx, id); },
            onkeydown: function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); selectTask(ctx, id); } },
          }, [
            H("span", { class: "rls-lab", text: short(id) }),
            H("div", { class: "rls-track" }, [fill]),
            H("span", { class: "rls-val", text: pct(p) }),
          ]);
          var lines = taskLines(m, id).concat([{ text: "P(" + imp.b + " > " + imp.a + ") on this task: " + pct(p) }]);
          row.addEventListener("pointermove", function (evt) { tip.show(evt, lines); });
          row.addEventListener("pointerleave", tip.hide);
          row.addEventListener("focus", function () { tip.hide(); });
          rows.appendChild(row);
        });
        root.appendChild(rows);
      }
      if (Array.isArray(imp.tasks_skipped) && imp.tasks_skipped.length) {
        root.appendChild(H("p", { class: "rls-note", text:
          "Excluded, because only one policy ran them: " + imp.tasks_skipped.map(short).join(", ") + "." }));
      }
      root.appendChild(H("p", { class: "rls-note", text:
        "Per task, every run of " + imp.b + " is compared against every run of " + imp.a + " (a tie counts a half), "
        + "and the tasks are averaged — so one task cannot dominate by having more runs, and one enormous episode "
        + "cannot win more than one comparison. The interval is the same stratified bootstrap." }));
      var advisory = advisoryNote(H, m);
      if (advisory) root.appendChild(advisory);
    },
  });
})(typeof window !== "undefined" ? window : this);
