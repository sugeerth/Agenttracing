/* AgentDiff block — Where the time went.
 *
 * Why each run took as long as it did: a waterfall of its steps along
 * wall-clock (thinking on the trunk colour, tool waits darker, the answer
 * solid; hatched where the reading marks the step as wasted — nothing
 * new, a repeat, a dead end, an error, or after the answer's basis was
 * complete), a share bar per run (thinking · tools · answer, with the
 * wasted share), the tools ranked by the seconds they cost, and the
 * rationale in sentences whose every number is in the ledger. From
 * `report.timing`; unmeasurable when no latency was recorded.
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
      ".tm{--tm-a:var(--a);--tm-b:var(--b);position:relative}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .tm{--tm-a:#3987e5;--tm-b:#d95926}}",
      ":root[data-theme=dark] .tm{--tm-a:#3987e5;--tm-b:#d95926}",
      ".tm-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".tm-run{margin:6px 0 0}",
      ".tm-run h5{margin:0 0 4px;font-size:var(--fs-s);font-weight:600;color:var(--ink);display:flex;gap:8px;align-items:center;flex-wrap:wrap}",
      ".tm-run h5 i{width:10px;height:10px;border-radius:50%;display:inline-block}",
      ".tm-run h5 .tot{font-family:var(--mono);font-weight:500;color:var(--ink-3);font-size:var(--fs-xs)}",
      ".tm-share{display:flex;height:4px;border-radius:2px;overflow:hidden;background:var(--surface-2);margin:2px 0 0;width:180px}",
      ".tm-share i{display:block;height:100%;border-right:2px solid var(--surface)}",
      ".tm-share i:last-child{border-right:0}",
      ".tm-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".tm-legend b{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:4px}",
      ".tm-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".tm-chart .axis text{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}.tm-chart .axis line,.tm-chart .axis path{stroke:var(--rule)}",
      ".tm-chart .seg{cursor:default}.tm-chart .seg.wasted{stroke:var(--bad);stroke-width:1}",
      ".tm-chart .lab{font-size:var(--fs-xs);fill:var(--ink-2);font-family:var(--mono)}.tm-chart .lab.why{fill:var(--bad)}",
      ".tm-rat{font-size:var(--fs-xs);color:var(--ink-3);margin:2px 0 0;max-width:100ch}",
      ".tm-tools{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}",
      ".tm-tools .chip{font-family:var(--mono);font-size:var(--fs-xs);background:var(--surface-2);border-radius:9px;padding:1px 7px;color:var(--ink-2);font-variant-numeric:tabular-nums}",
      ".tm-tools .chip.w{border:1px solid var(--warn)}",
      ".tm-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:320px}",
      ".tm-tip b{color:var(--ink)}",
      ".tm-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:90ch}",
      ".tm-details{margin-top:8px;font-size:var(--fs-xs)}.tm-details summary{cursor:pointer;color:var(--ink-2)}",
      ".tm-details table{border-collapse:collapse;margin-top:4px;font-variant-numeric:tabular-nums}.tm-details td,.tm-details th{text-align:left;padding:2px 10px 2px 0;border-top:1px solid var(--rule);color:var(--ink-2)}.tm-details th{border-top:0;color:var(--ink-3);font-weight:500}",
    ].join("");
    document.head.appendChild(node);
  }
  var CAT = { think: { label: "thinking", alpha: 0.35 }, tool: { label: "waiting on tools", alpha: 0.7 }, answer: { label: "the answer", alpha: 1 } };
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v.toFixed(1) + "s"; }
  function color(side) { return "var(--tm-" + side + ")"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "tm-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        lines.forEach(function (l) { if (!l) return; var d = document.createElement("div"); if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text; tip.appendChild(d); });
        tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 300 > r.width) x = Math.max(0, evt.clientX - r.left - 310);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  /* One strip per run: every step a segment along wall-clock, left to
   * right; thinking light, tools darker, the answer solid; wasted steps
   * hatched. The slowest few steps are named above the strip; every
   * other step is a hover away. A 300-step run is one line. */
  function waterfall(host, report, side, t, xMax, tip) {
    if (!d3) return;
    var W = Math.max(320, host.clientWidth || 640), barH = 18, m = { top: 30, right: 8, bottom: 18, left: 8 };
    var rows = t.steps || [];
    var H = m.top + barH + m.bottom;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("data-side", side).attr("role", "img")
      .attr("aria-label", name(report, side) + ": each step's seconds along wall-clock, wasted steps hatched");
    var x = d3.scaleLinear().domain([0, Math.max(1e-6, xMax)]).range([m.left, W - m.right]);
    svg.append("g").attr("class", "axis").attr("transform", "translate(0," + (m.top + barH + 1) + ")").call(d3.axisBottom(x).ticks(Math.max(3, Math.floor(W / 120))).tickSize(3).tickFormat(function (v) { return secs(v); })).select(".domain").remove();
    var c = color(side);
    var defs = svg.append("defs");
    var pid = "tm-hatch-" + side + "-" + Math.random().toString(36).slice(2, 7);
    var pat = defs.append("pattern").attr("id", pid).attr("width", 6).attr("height", 6).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 6).attr("height", 6).attr("fill", "var(--surface)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6).attr("stroke", "var(--bad)").attr("stroke-width", 2);
    var acc = 0, placed = [];
    var top3 = rows.slice().sort(function (p, q) { return q.latency_s - p.latency_s; }).slice(0, 3).map(function (r) { return r.index; });
    rows.forEach(function (r) {
      var y = m.top;
      var g = svg.append("g").attr("class", "step").attr("data-step", r.index).attr("data-category", r.category).attr("data-wasted", r.wasted || "");
      var x0 = x(acc), x1 = x(acc + r.latency_s), w = Math.max(1.5, x1 - x0 - 1);
      g.append("rect").attr("class", "seg" + (r.wasted ? " wasted" : "")).attr("x", x0).attr("y", y).attr("width", w).attr("height", barH).attr("rx", 2)
        .attr("fill", r.wasted ? "url(#" + pid + ")" : c).attr("fill-opacity", r.wasted ? 1 : CAT[r.category].alpha);
      if (r.wasted) g.append("rect").attr("x", x0).attr("y", y).attr("width", w).attr("height", barH).attr("rx", 2).attr("fill", c).attr("fill-opacity", 0.25).attr("pointer-events", "none");
      // name the slowest steps above the strip, never on top of each other
      if (top3.indexOf(r.index) >= 0 && w > 6) {
        var label = r.index + " · " + (r.name || r.type) + " " + secs(r.latency_s) + (r.wasted && r.share >= 0.1 ? " — " + r.wasted_label : "");
        var lw = label.length * 5.6, lx = Math.min(Math.max(m.left, x0 + w / 2 - lw / 2), W - m.right - lw);
        var collide = placed.some(function (p) { return lx < p.x1 + 8 && lx + lw > p.x0 - 8; });
        if (!collide) {
          placed.push({ x0: lx, x1: lx + lw });
          g.append("line").attr("x1", x0 + w / 2).attr("x2", x0 + w / 2).attr("y1", y - 3).attr("y2", y - 9).attr("stroke", "var(--ink-3)").attr("stroke-width", 1);
          g.append("text").attr("class", "lab" + (r.wasted && r.share >= 0.1 ? " why" : "")).attr("x", lx).attr("y", y - 12).text(label);
        }
      }
      g.append("rect").attr("x", x0).attr("y", y - 14).attr("width", Math.max(6, x1 - x0)).attr("height", barH + 14).attr("fill", "transparent")
        .on("pointermove", function (evt) {
          tip.show(evt, [{ b: true, text: name(report, side) + " · step " + r.index + " · " + (r.name || r.type) },
            { text: secs(r.latency_s) + " · " + Math.round(r.share * 100) + "% of the run · " + CAT[r.category].label + (isNum(r.tokens) ? " · " + r.tokens + " tokens" : "") },
            r.wasted ? { text: "wasted: " + r.wasted_label } : null, r.retry_of !== null && r.retry_of !== undefined ? { text: "a retry of step " + r.retry_of } : null,
            r.role ? { text: "role in the reading: " + String(r.role).replace(/_/g, " ") } : null]);
        }).on("pointerleave", tip.hide)
        .on("click", function () { if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, r.index); });
      acc += r.latency_s;
    });
  }

  AgentDiff.block({
    id: "time",
    title: "Where the time went",
    storyTitle: "Where the time went",
    question: "Why did each run take as long as it did — thinking, waiting on which tools, and how much of it was wasted?",
    group: "trajectory",
    size: "wide",

    relevance: function (ctx) {
      var tm = ctx.report && ctx.report.timing;
      if (!tm) return 0;
      return (tm.a && tm.a.measurable) || (tm.b && tm.b.measurable) ? 0.74 : 0.2;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, tm = report && report.timing;
      if (!tm) return ctx.empty(el, "This report carries no time attribution (it predates the section).");
      var root = H("div", { class: "tm" });
      el.appendChild(root);
      var tip = tooltip(root);
      if (tm.narrative) root.appendChild(H("p", { class: "tm-narr", text: tm.narrative }));
      var legend = H("div", { class: "tm-legend" });
      Object.keys(CAT).forEach(function (k) { legend.appendChild(H("span", null, [H("b", { style: { background: "var(--ink-3)", opacity: CAT[k].alpha } }), H("span", { text: CAT[k].label })])); });
      legend.appendChild(H("span", null, [H("b", { style: { background: "repeating-linear-gradient(45deg, var(--bad) 0 2px, transparent 2px 5px)" } }), H("span", { text: "wasted" })]));
      legend.appendChild(H("span", { text: "· hover a bar for the why, click it to open the step" }));
      root.appendChild(legend);
      var xMax = Math.max((tm.a && tm.a.total_s) || 0, (tm.b && tm.b.total_s) || 0);
      ["a", "b"].forEach(function (side) {
        var t = tm[side];
        if (!t) return;
        var run = H("section", { class: "tm-run", "data-side": side });
        run.appendChild(H("h5", null, [H("i", { style: { background: color(side) } }), H("span", { text: name(report, side) }),
          H("span", { class: "tot", text: t.measurable ? secs(t.total_s) + " · " + t.steps.length + " steps · " + ["think", "tool", "answer"].map(function (k) { var c = t.by_category[k] || { share: 0 }; return Math.round(c.share * 100) + "% " + CAT[k].label; }).join(", ") + (isNum(t.wasted_share) ? " · " + Math.round(t.wasted_share * 100) + "% wasted" : "") : "unmeasurable" })]));
        if (!t.measurable) { run.appendChild(H("p", { class: "tm-rat", text: t.rationale })); root.appendChild(run); return; }
        // the category shares, kept for the table view and tests; the header says them in words
        var share = H("div", { class: "tm-share", hidden: true });
        ["think", "tool", "answer"].forEach(function (k) {
          var c = t.by_category[k] || { share: 0 };
          share.appendChild(H("i", { "data-category": k, style: { width: (c.share * 100) + "%" } }));
        });
        run.appendChild(share);
        var host = H("div", { class: "tm-chart" });
        run.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { waterfall(host, report, side, t, xMax, tip); }) : host);
        if (!(AgentDiff.charts && AgentDiff.charts.responsive)) waterfall(host, report, side, t, xMax, tip);
        run.appendChild(H("p", { class: "tm-rat", text: t.rationale }));
        root.appendChild(run);
      });
      var det = H("details", { class: "tm-details" });
      det.appendChild(H("summary", { text: "every step's seconds, and each tool's" }));
      var toolsT = H("table");
      toolsT.appendChild(H("tr", null, ["run", "tool", "calls", "seconds", "share", "wasted calls"].map(function (h) { return H("th", { text: h }); })));
      ["a", "b"].forEach(function (side) {
        var t = tm[side];
        if (!t || !t.measurable) return;
        Object.keys(t.by_tool || {}).sort(function (p, q) { return t.by_tool[q].seconds - t.by_tool[p].seconds; }).forEach(function (k) {
          var v = t.by_tool[k];
          toolsT.appendChild(H("tr", { class: "tm-tool", "data-side": side, "data-tool": k }, [name(report, side), k, String(v.calls), v.seconds.toFixed(2), Math.round(v.share * 100) + "%", String(v.wasted_calls)].map(function (x) { return H("td", { text: x }); })));
        });
      });
      det.appendChild(H("div", { class: "scroll-x" }, [toolsT]));
      var tbl = H("table");
      tbl.appendChild(H("tr", null, ["run", "step", "name", "category", "seconds", "share", "wasted"].map(function (h) { return H("th", { text: h }); })));
      ["a", "b"].forEach(function (side) {
        ((tm[side] && tm[side].steps) || []).forEach(function (r) {
          tbl.appendChild(H("tr", null, [name(report, side), String(r.index), r.name || r.type, CAT[r.category].label, r.latency_s.toFixed(2), Math.round(r.share * 100) + "%", r.wasted_label || ""].map(function (v) { return H("td", { text: v }); })));
        });
      });
      det.appendChild(H("div", { class: "scroll-x" }, [tbl]));
      det.appendChild(H("p", { class: "tm-note", text: (tm.a && tm.a.basis) || "" }));
      root.appendChild(det);
    },
  });
})(typeof window !== "undefined" ? window : this);
