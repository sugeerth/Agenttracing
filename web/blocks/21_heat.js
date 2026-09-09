/* AgentDiff blocks — three comparison panels over the same steps.
 *
 *   heatmap        calls × time: each tool (and thinking, and the answer) a
 *                  row, time in bins along x, a cell's colour the seconds
 *                  spent there; both runs in every cell (upper half A,
 *                  lower half B) so the two shapes compare at a glance;
 *                  hatched where the seconds were wasted.
 *   tool-matrix    per tool, side by side: calls, seconds, mean latency,
 *                  wasted seconds, errors — A · B with a bar in each cell.
 *   latency-strip  per tool, every call as a dot at its latency, A above
 *                  the line and B below, the mean as a tick — the spread,
 *                  not only the mean.
 *
 *   treemap        where the seconds went as area: both runs on one scale,
 *                  sub-agents and parts as boxes, steps as tiles; zoom.
 *
 * All four read `report.timing` (seconds, category, wasted per step) and
 * the steps themselves; every number is a count or a sum over the steps.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".hm{--hm-a:var(--a);--hm-b:var(--b);position:relative}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .hm{--hm-a:#3987e5;--hm-b:#d95926}}",
      ":root[data-theme=dark] .hm{--hm-a:#3987e5;--hm-b:#d95926}",
      ".hm-bar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".hm-bar i{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:4px}",
      ".hm-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".hm-chart .lab{font-size:var(--fs-xs);fill:var(--ink-2)}.hm-chart .tick{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".hm-chart .cell{cursor:pointer}.hm-chart .cell:hover rect.hit{stroke:var(--ink);stroke-width:1.5}",
      ".hm-chart .tot{font-size:var(--fs-xs);fill:var(--ink-3);font-family:var(--mono);font-variant-numeric:tabular-nums}",
      ".hm-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
      ".hm-tip b{color:var(--ink)}",
      ".tm-matrix{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
      ".tm-matrix th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".tm-matrix td{padding:4px 10px 4px 0;border-top:1px solid var(--rule);vertical-align:middle;white-space:nowrap}",
      ".tm-matrix td.tool{font-family:var(--mono);color:var(--ink)}",
      ".tm-matrix .pair{display:inline-grid;grid-template-columns:auto 1fr;gap:1px 6px;align-items:center;min-width:120px}",
      ".tm-matrix .pair span{color:var(--ink-2)}.tm-matrix .pair span.w{color:var(--warn)}",
      ".tm-matrix .pair i{display:block;height:5px;border-radius:0 3px 3px 0;min-width:1px}",
      ".tm-matrix .pair i.a{background:var(--hm-a)}.tm-matrix .pair i.b{background:var(--hm-b)}",
      ".hm-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:6px;max-width:90ch}",
    ].join("");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v >= 1 ? v.toFixed(1) + "s" : v.toFixed(2) + "s"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function color(side) { return "var(--hm-" + side + ")"; }
  function rowKey(r) { return r.category === "tool" ? (r.name || "?") : r.category === "answer" ? "the answer" : "thinking"; }

  /* the rows both runs share: tools by total seconds, then thinking, then the answer */
  function rowsOf(tm) {
    var totals = {};
    ["a", "b"].forEach(function (side) {
      ((tm[side] && tm[side].steps) || []).forEach(function (r) { var k = rowKey(r); totals[k] = (totals[k] || 0) + (r.latency_s || 0); });
    });
    var tools = Object.keys(totals).filter(function (k) { return k !== "thinking" && k !== "the answer"; }).sort(function (p, q) { return totals[q] - totals[p]; });
    if (totals.thinking !== undefined) tools.push("thinking");
    if (totals["the answer"] !== undefined) tools.push("the answer");
    return tools;
  }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "hm-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        lines.forEach(function (l) { if (!l) return; var d = document.createElement("div"); if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text; tip.appendChild(d); });
        tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 320 > r.width) x = Math.max(0, evt.clientX - r.left - 330);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  // ------------------------------------------------------------ heat map
  function drawHeat(host, report, tm, tip) {
    if (!d3) return;
    var W = Math.max(360, host.clientWidth || 640);
    var rows = rowsOf(tm);
    var total = Math.max((tm.a && tm.a.total_s) || 0, (tm.b && tm.b.total_s) || 0, 1e-6);
    var bins = Math.max(8, Math.min(24, Math.floor((W - 200) / 34)));
    // the right margin fits the longest "A · B" row total
    var totLen = 0;
    rows.forEach(function (k) {
      var ta = 0, tb = 0;
      ((tm.a && tm.a.steps) || []).forEach(function (r) { if (rowKey(r) === k) ta += r.latency_s || 0; });
      ((tm.b && tm.b.steps) || []).forEach(function (r) { if (rowKey(r) === k) tb += r.latency_s || 0; });
      totLen = Math.max(totLen, (secs(ta) + " · " + secs(tb)).length);
    });
    var m = { l: 120, r: 14 + Math.ceil(totLen * 6.6), t: 22, b: 8 }, rowH = 26, half = 12;
    var H = m.t + rows.length * rowH + m.b;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("data-bins", bins)
      .attr("aria-label", "calls by tool over time for both runs, cell colour the seconds spent");
    var x = d3.scaleLinear().domain([0, total]).range([m.l, W - m.r]);
    var bw = (W - m.l - m.r) / bins;
    // seconds per (row, bin, side), a step spread over the bins it spans; the steps in each cell for the tooltip
    var cells = {};
    var maxCell = 1e-9;
    ["a", "b"].forEach(function (side) {
      var acc = 0;
      ((tm[side] && tm[side].steps) || []).forEach(function (r) {
        var t0 = acc, t1 = acc + (r.latency_s || 0); acc = t1;
        var k = rowKey(r);
        var b0 = Math.min(bins - 1, Math.floor(t0 / total * bins)), b1 = Math.min(bins - 1, Math.floor(Math.max(t0, t1 - 1e-9) / total * bins));
        for (var b = b0; b <= b1; b++) {
          var lo = Math.max(t0, b * total / bins), hi = Math.min(t1, (b + 1) * total / bins);
          var part = Math.max(0, hi - lo);
          var key = k + "|" + b + "|" + side;
          var c = cells[key] || (cells[key] = { row: k, bin: b, side: side, seconds: 0, wasted: 0, steps: [] });
          c.seconds += part; if (r.wasted) c.wasted += part;
          if (c.steps.indexOf(r) < 0) c.steps.push(r);
          maxCell = Math.max(maxCell, c.seconds);
        }
      });
    });
    // axis: time
    var ticks = x.ticks(Math.max(3, Math.floor(bins / 4)));
    ticks.forEach(function (t) {
      svg.append("text").attr("class", "tick").attr("x", x(t)).attr("y", m.t - 8).attr("text-anchor", "middle").text(secs(t));
    });
    rows.forEach(function (k, i) {
      var y = m.t + i * rowH;
      svg.append("text").attr("class", "lab").attr("x", m.l - 8).attr("y", y + rowH / 2 + 4).attr("text-anchor", "end").text(k.length > 16 ? k.slice(0, 15) + "…" : k);
      for (var b = 0; b < bins; b++) {
        ["a", "b"].forEach(function (side, si) {
          var c = cells[k + "|" + b + "|" + side];
          var cx = m.l + b * bw + 1, cy = y + 1 + si * half, cw = Math.max(1, bw - 2), ch = half - 1;
          var g = svg.append("g").attr("class", "cell").attr("data-row", k).attr("data-bin", b).attr("data-side", side).attr("data-seconds", c ? c.seconds.toFixed(3) : "0");
          g.append("rect").attr("class", "hit").attr("x", cx).attr("y", cy).attr("width", cw).attr("height", ch).attr("rx", 2)
            .attr("fill", c ? color(side) : "var(--surface-2)").attr("fill-opacity", c ? 0.15 + 0.85 * Math.sqrt(c.seconds / maxCell) : 1).attr("stroke", "none");
          if (c && c.wasted > 0) {
            g.append("rect").attr("x", cx + cw * (1 - c.wasted / c.seconds)).attr("y", cy).attr("width", cw * (c.wasted / c.seconds)).attr("height", ch).attr("rx", 2)
              .attr("fill", "url(#hm-hatch)").attr("pointer-events", "none");
          }
          if (c) {
            g.on("pointermove", function (evt) {
              tip.show(evt, [{ b: true, text: name(report, side) + " · " + k + " · " + secs(b * total / bins) + "–" + secs((b + 1) * total / bins) },
                { text: secs(c.seconds) + " here" + (c.wasted ? ", " + secs(c.wasted) + " wasted" : "") + " · step" + (c.steps.length === 1 ? " " : "s ") + c.steps.map(function (s) { return s.index; }).join(", ") },
                { text: c.steps.map(function (s) { return s.index + " · " + (s.name || s.type) + " " + secs(s.latency_s) + (s.wasted_label ? " — " + s.wasted_label : ""); }).slice(0, 4).join(" · ") },
                { text: "click to open the first step" }]);
            }).on("pointerleave", tip.hide)
              .on("click", function () { if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, c.steps[0].index); });
          }
        });
      }
      // row totals, A · B
      var ta = 0, tb = 0;
      Object.keys(cells).forEach(function (key) { var c = cells[key]; if (c.row === k) { if (c.side === "a") ta += c.seconds; else tb += c.seconds; } });
      svg.append("text").attr("class", "tot").attr("data-row", k).attr("x", W - m.r + 8).attr("y", y + rowH / 2 + 4).text(secs(ta) + " · " + secs(tb));
    });
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "hm-hatch").attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "var(--bad)").attr("stroke-width", 1.4).attr("stroke-opacity", 0.8);
  }

  AgentDiff.block({
    id: "heatmap",
    title: "Calls × time",
    question: "When did each run spend its time on which tool, and where were the seconds wasted?",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) { var tm = ctx.report && ctx.report.timing; return tm && ((tm.a && tm.a.measurable) || (tm.b && tm.b.measurable)) ? 0.7 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, tm = report && report.timing;
      if (!tm || !((tm.a && tm.a.measurable) || (tm.b && tm.b.measurable))) return ctx.empty(el, "No step latencies were recorded, so time cannot be placed.");
      var root = H("div", { class: "hm" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(H("div", { class: "hm-bar" }, [
        H("span", null, [H("i", { style: { background: color("a") } }), H("span", { text: name(report, "a") + " upper half" })]),
        H("span", null, [H("i", { style: { background: color("b") } }), H("span", { text: name(report, "b") + " lower half" })]),
        H("span", { text: "darker = more seconds in that bin · hatched = wasted · right: seconds per row, " + name(report, "a") + " · " + name(report, "b") }),
      ]));
      var host = H("div", { class: "hm-chart" });
      root.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { drawHeat(host, report, tm, tip); }) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) drawHeat(host, report, tm, tip);
    },
  });

  // ------------------------------------------------------------ tool matrix
  function statsOf(tm, report, side) {
    var out = {};
    ((tm[side] && tm[side].steps) || []).forEach(function (r) {
      var k = rowKey(r);
      var st = out[k] || (out[k] = { calls: 0, seconds: 0, wasted: 0, errors: 0, lat: [] });
      st.calls++; st.seconds += r.latency_s || 0; if (r.wasted) st.wasted += r.latency_s || 0; if (r.wasted === "error") st.errors++; st.lat.push(r.latency_s || 0);
    });
    Object.keys(out).forEach(function (k) { out[k].mean = out[k].calls ? out[k].seconds / out[k].calls : 0; });
    return out;
  }

  AgentDiff.block({
    id: "tool-matrix",
    title: "Tool matrix",
    question: "Per tool, side by side: how many calls, how many seconds, how slow each call, how much wasted, how many errors?",
    group: "trajectory",
    size: "normal",
    relevance: function (ctx) { var tm = ctx.report && ctx.report.timing; return tm && tm.a && tm.b ? 0.66 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, tm = report && report.timing;
      if (!tm || !tm.a || !tm.b) return ctx.empty(el, "This report carries no per-step timing.");
      var root = H("div", { class: "hm" });
      el.appendChild(root);
      var rows = rowsOf(tm), A = statsOf(tm, report, "a"), B = statsOf(tm, report, "b");
      var cols = [["calls", function (s) { return s.calls; }, function (v) { return String(v); }], ["seconds", function (s) { return s.seconds; }, secs],
                  ["per call", function (s) { return s.mean; }, secs], ["wasted", function (s) { return s.wasted; }, secs], ["errors", function (s) { return s.errors; }, function (v) { return String(v); }]];
      var t = H("table", { class: "tm-matrix" });
      t.appendChild(H("tr", null, [H("th", { text: "tool" })].concat(cols.map(function (c) { return H("th", { text: c[0] }); }))));
      rows.forEach(function (k) {
        var a = A[k] || { calls: 0, seconds: 0, wasted: 0, errors: 0, mean: 0 }, b = B[k] || { calls: 0, seconds: 0, wasted: 0, errors: 0, mean: 0 };
        var tr = H("tr", { "data-tool": k });
        tr.appendChild(H("td", { class: "tool", text: k }));
        cols.forEach(function (c) {
          var va = c[1](a), vb = c[1](b), mx = Math.max(va, vb, 1e-9);
          var cell = H("td", { "data-col": c[0] });
          cell.appendChild(H("span", { class: "pair" }, [
            H("span", { class: "a" + (c[0] === "wasted" && va ? " w" : ""), text: c[2](va) }), H("i", { class: "a", style: { width: Math.round(100 * va / mx) + "%" } }),
            H("span", { class: "b" + (c[0] === "wasted" && vb ? " w" : ""), text: c[2](vb) }), H("i", { class: "b", style: { width: Math.round(100 * vb / mx) + "%" } })]));
          tr.appendChild(cell);
        });
        t.appendChild(tr);
      });
      root.appendChild(H("div", { class: "scroll-x" }, [t]));
      root.appendChild(H("p", { class: "hm-note", text: "each cell: " + name(report, "a") + " above " + name(report, "b") + ", bars scaled to the larger of the two; wasted = seconds in calls the reading marks as wasted; errors = calls that returned an error" }));
    },
  });

  // ------------------------------------------------------------ latency strip
  function drawStrip(host, report, tm, tip) {
    if (!d3) return;
    var W = Math.max(360, host.clientWidth || 640);
    var rows = rowsOf(tm);
    var maxLat = 1e-9;
    ["a", "b"].forEach(function (side) { ((tm[side] && tm[side].steps) || []).forEach(function (r) { maxLat = Math.max(maxLat, r.latency_s || 0); }); });
    var m = { l: 120, r: 16, t: 22, b: 8 }, rowH = 30;
    var H = m.t + rows.length * rowH + m.b;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img").attr("aria-label", "every call's latency per tool, both runs");
    var x = d3.scaleLinear().domain([0, maxLat]).nice().range([m.l, W - m.r]);
    x.ticks(5).forEach(function (t) {
      svg.append("line").attr("x1", x(t)).attr("x2", x(t)).attr("y1", m.t - 4).attr("y2", H - m.b).attr("stroke", "var(--rule)");
      svg.append("text").attr("class", "tick").attr("x", x(t)).attr("y", m.t - 8).attr("text-anchor", "middle").text(secs(t));
    });
    rows.forEach(function (k, i) {
      var y = m.t + i * rowH + rowH / 2;
      svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y).attr("stroke", "var(--rule)");
      svg.append("text").attr("class", "lab").attr("x", m.l - 8).attr("y", y + 4).attr("text-anchor", "end").text(k.length > 16 ? k.slice(0, 15) + "…" : k);
      ["a", "b"].forEach(function (side, si) {
        var calls = ((tm[side] && tm[side].steps) || []).filter(function (r) { return rowKey(r) === k; });
        if (!calls.length) return;
        var dy = si === 0 ? -6 : 6;
        var mean = calls.reduce(function (s, r) { return s + (r.latency_s || 0); }, 0) / calls.length;
        svg.append("line").attr("class", "mean").attr("data-side", side).attr("data-row", k).attr("x1", x(mean)).attr("x2", x(mean)).attr("y1", y + dy - 6).attr("y2", y + dy + 6).attr("stroke", color(side)).attr("stroke-width", 2);
        calls.forEach(function (r) {
          svg.append("circle").attr("class", "call" + (r.wasted ? " wasted" : "")).attr("data-side", side).attr("data-row", k).attr("data-step", r.index)
            .attr("cx", x(r.latency_s || 0)).attr("cy", y + dy).attr("r", 4).attr("fill", r.wasted ? "var(--surface)" : color(side)).attr("stroke", color(side)).attr("stroke-width", 1.6).style("cursor", "pointer")
            .on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: name(report, side) + " · step " + r.index + " · " + (r.name || r.type) }, { text: secs(r.latency_s) + (r.wasted_label ? " — " + r.wasted_label : "") + " · mean for this tool " + secs(mean) }]); })
            .on("pointerleave", tip.hide)
            .on("click", function () { if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, r.index); });
        });
      });
    });
  }

  AgentDiff.block({
    id: "latency-strip",
    title: "Latency by tool",
    question: "How slow was each call, tool by tool, and how spread out — not only the mean?",
    group: "trajectory",
    size: "normal",
    relevance: function (ctx) { var tm = ctx.report && ctx.report.timing; return tm && ((tm.a && tm.a.measurable) || (tm.b && tm.b.measurable)) ? 0.62 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, tm = report && report.timing;
      if (!tm || !((tm.a && tm.a.measurable) || (tm.b && tm.b.measurable))) return ctx.empty(el, "No step latencies were recorded.");
      var root = H("div", { class: "hm" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(H("div", { class: "hm-bar" }, [
        H("span", null, [H("i", { style: { background: color("a") } }), H("span", { text: name(report, "a") + " above the line" })]),
        H("span", null, [H("i", { style: { background: color("b") } }), H("span", { text: name(report, "b") + " below" })]),
        H("span", { text: "dot = one call at its latency, hollow = wasted; tick = the mean; click a dot to open the step" }),
      ]));
      var host = H("div", { class: "hm-chart" });
      root.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { drawStrip(host, report, tm, tip); }) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) drawStrip(host, report, tm, tip);
    },
  });

  // ------------------------------------------------------------ treemap
  //
  // Where the seconds went, as area: both runs on one scale, so the run
  // that took longer is the larger map; inside, sub-agents and parts as
  // nested boxes, every step a tile whose area is its seconds. Overview
  // first — a big tile is what the eye finds — then click a box to zoom.
  var TmZoom = {};   // task+side → the node key zoomed into
  function treeOf(report, side, tm) {
    var hz = report && report.horizon && report.horizon[side];
    if (hz && hz.tree) return hz.tree;
    var steps = (tm && tm[side] && tm[side].steps) || [];
    if (!steps.length) return null;
    var groups = {}, order = [];
    steps.forEach(function (r) {
      var k = rowKey(r);
      if (!groups[k]) { groups[k] = { kind: "group", key: "g:" + k, label: k, children: [], seconds: 0, wasted_s: 0 }; order.push(k); }
      var g = groups[k];
      g.children.push({ kind: "step", key: "step:" + r.index, label: r.name || r.type, type: r.type, from: r.index, seconds: r.latency_s || 0, wasted: r.wasted, wasted_label: r.wasted_label, wasted_s: r.wasted ? (r.latency_s || 0) : 0, fault: false, decisive: false });
      g.seconds += r.latency_s || 0; if (r.wasted) g.wasted_s += r.latency_s || 0;
    });
    var kids = order.map(function (k) { return groups[k]; });
    return { kind: "run", key: "run", label: name(report, side), children: kids, seconds: kids.reduce(function (s, g) { return s + g.seconds; }, 0), wasted_s: kids.reduce(function (s, g) { return s + g.wasted_s; }, 0) };
  }
  function findNode(node, key) {
    if (!node) return null;
    if (node.key === key) return node;
    var kids = node.children || [];
    for (var i = 0; i < kids.length; i++) { var f = findNode(kids[i], key); if (f) return f; }
    return null;
  }
  function tileOpacity(d) {
    var t = d.type || "";
    return t === "answer" ? 1 : (t === "plan" || t === "reason" || t === "think") ? 0.38 : 0.8;
  }
  function drawTreemap(host, report, tm, tip, crumbs) {
    if (!d3) return;
    var W = Math.max(360, host.clientWidth || 640), Hh = 190, gap = 18, head = 16;
    var task = report && report.task && report.task.id;
    var sides = ["a", "b"].filter(function (s) { return treeOf(report, s, tm); });
    if (!sides.length) return;
    var roots = {}, secs_ = {};
    sides.forEach(function (s) { roots[s] = treeOf(report, s, tm); secs_[s] = roots[s].seconds || 0; });
    var totalS = sides.reduce(function (t, s) { return t + secs_[s]; }, 0) || 1;
    var avail = W - gap * (sides.length - 1);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + (Hh + head)).attr("role", "img")
      .attr("aria-label", "where each run's seconds went, as area, both runs on one scale");
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "tm-hatch").attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "var(--bad)").attr("stroke-width", 1).attr("stroke-opacity", 0.5);
    var x0 = 0;
    crumbs.innerHTML = "";
    sides.forEach(function (side) {
      var zoomKey = TmZoom[task + ":" + side];
      var zoomed = zoomKey ? findNode(roots[side], zoomKey) : null;
      var w = sides.length === 1 ? avail : Math.max(70, Math.round(avail * secs_[side] / totalS));
      if (zoomed) w = Math.max(w, Math.round(avail * 0.5));
      var data = zoomed || roots[side];
      var root = d3.hierarchy(data, function (d) { return d.children; }).sum(function (d) { return d.kind === "step" ? (d.seconds || 0) : 0; }).sort(function (p, q) { return q.value - p.value; });
      d3.treemap().size([w, Hh]).paddingOuter(1.5).paddingInner(1.5)
        .paddingTop(function (d) { return d.depth > 0 && d.children && labelled(d) ? 12 : 1.5; })(root);
      // a box is labelled when it is a sub-agent, or a part large enough to read
      function labelled(d) { return d.data.kind === "span" || d.depth === 1 || (d.x1 - d.x0 > 130 && d.y1 - d.y0 > 44); }
      var g = svg.append("g").attr("class", "tmap-side").attr("data-side", side).attr("transform", "translate(" + x0 + ",0)");
      // the run's line: name, seconds, wasted share — the overview in words
      var wasted = data.wasted_s || 0;
      g.append("text").attr("class", "tmap-head").attr("x", 0).attr("y", 10).attr("fill", color(side)).text(
        (zoomed ? (data.label || data.agent || data.key) : name(report, side)) + " · " + secs(data.seconds || 0)
        + (wasted && data.seconds ? " · " + Math.round(100 * wasted / data.seconds) + "% wasted" : ""));
      var nodes = g.append("g").attr("transform", "translate(0," + head + ")");
      root.descendants().forEach(function (d) {
        if (d.depth === 0) return;
        var dw = d.x1 - d.x0, dh = d.y1 - d.y0;
        if (dw <= 0 || dh <= 0) return;
        var leaf = !d.children;
        var n = nodes.append("g").attr("class", "tmap-node" + (leaf ? " leaf" : " box")).attr("data-side", side).attr("data-kind", d.data.kind).attr("data-key", d.data.key)
          .attr("transform", "translate(" + d.x0 + "," + d.y0 + ")").style("cursor", "pointer");
        n.append("rect").attr("width", dw).attr("height", dh).attr("rx", leaf ? 1.5 : 2)
          .attr("fill", leaf ? color(side) : "var(--surface-2)").attr("fill-opacity", leaf ? tileOpacity(d.data) : 0.55)
          .attr("stroke", d.data.decisive && leaf ? "var(--bad)" : d.data.fault ? "var(--bad)" : "none").attr("stroke-width", d.data.decisive && leaf ? 2.2 : 1.2);
        if (leaf && d.data.wasted_s > 0) n.append("rect").attr("class", "waste").attr("width", dw).attr("height", dh).attr("rx", 1.5).attr("fill", "url(#tm-hatch)").attr("pointer-events", "none");
        var label = leaf ? (d.data.label || d.data.type || "") : (d.data.agent && d.data.kind === "span" ? d.data.agent : (d.data.label || d.data.key));
        var fits = Math.floor((dw - 6) / 5.8);
        if (leaf ? (dw >= 40 && dh >= 16) : (labelled(d) && dw > 40 && dh > 14)) {
          n.append("text").attr("class", leaf ? "tmap-lab leaf" : "tmap-lab").attr("x", 3).attr("y", leaf ? Math.min(dh - 3, 10) : 9)
            .attr("fill", leaf ? (tileOpacity(d.data) < 0.5 ? "var(--ink)" : "var(--bg)") : "var(--ink-2)").text(label.length > fits ? label.slice(0, Math.max(1, fits - 1)) + "…" : label);
        }
        n.on("pointermove", function (evt) {
          tip.show(evt, [{ b: true, text: name(report, side) + " · " + (leaf ? "step " + d.data.from + " · " + label : (d.data.kind + " · " + label)) },
            { text: secs(d.data.seconds || 0) + (d.data.wasted_s ? ", " + secs(d.data.wasted_s) + " wasted" : "") + (d.data.wasted_label ? " — " + d.data.wasted_label : "") + (d.data.fault ? " · on the fault's path" : "") + (d.data.decisive ? " · decisive" : "") },
            leaf ? null : { text: "click to zoom in" }]);
        }).on("pointerleave", tip.hide)
          .on("click", function () {
            if (leaf) { if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, d.data.from); return; }
            TmZoom[task + ":" + side] = d.data.key; redraw();
          });
      });
      if (zoomed) {
        var back = document.createElement("button"); back.className = "tmap-back"; back.setAttribute("data-side", side);
        back.textContent = "‹ " + name(report, side) + " · whole run"; back.onclick = function () { delete TmZoom[task + ":" + side]; redraw(); };
        crumbs.appendChild(back);
      }
      x0 += w + gap;
    });
    function redraw() { host.innerHTML = ""; drawTreemap(host, report, tm, tip, crumbs); }
  }

  AgentDiff.block({
    id: "treemap",
    title: "Where the seconds went",
    question: "Which run took longer, and inside it, which sub-agent, part and step — area is seconds, both runs on one scale.",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) { var tm = ctx.report && ctx.report.timing; return tm && ((tm.a && tm.a.measurable) || (tm.b && tm.b.measurable)) ? 0.75 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, tm = report && report.timing;
      if (!tm || !((tm.a && tm.a.measurable) || (tm.b && tm.b.measurable))) return ctx.empty(el, "No step latencies were recorded, so time cannot be placed.");
      var root = H("div", { class: "hm" });
      el.appendChild(root);
      var tip = tooltip(root);
      var crumbs = H("div", { class: "tmap-crumbs" });
      root.appendChild(crumbs);
      var host = H("div", { class: "hm-chart" });
      root.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { drawTreemap(host, report, tm, tip, crumbs); }) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) drawTreemap(host, report, tm, tip, crumbs);
      root.appendChild(H("div", { class: "hm-bar" }, [
        H("span", { text: "area = seconds, on one scale for both runs · a box is a sub-agent or a part, a tile a step · light tiles think, solid ones call a tool · hatched = wasted · red edge = the fault's path · click a box to zoom, a tile to open the step" }),
      ]));
    },
  });

})(typeof window !== "undefined" ? window : this);
