/* AgentDiff block — Milestones.
 *
 * A long task's progress before the answer: the milestones the golden
 * task names (each recognised by evidence in a step's text), and when
 * each run reached them — a ladder over time, one stepped line per run,
 * the milestone a run never reached marked at the right edge. The gap
 * between the two lines at any rung is how much later one run got
 * there; where one line stops is where that run stalled.
 *
 * Reads `report.milestones` ({a, b, diff, narrative}); every mark is a
 * first step index and a cumulative second from the trace.
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
      ".ms{--ms-a:var(--a);--ms-b:var(--b);position:relative}",
      ".ms-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".ms-bar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".ms-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:4px}",
      ".ms-bar .seg button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 8px;cursor:pointer}",
      ".ms-bar .seg button[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
      ".ms-chart svg{display:block;width:100%;height:auto}",
      ".ms-chart .tick{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".ms-chart .lab{font-size:var(--fs-xs);fill:var(--ink-2)}.ms-chart .lab.miss{fill:var(--bad)}",
      ".ms-chart .rung{stroke:var(--rule)}",
      ".ms-chart .never{font-size:var(--fs-xs);fill:var(--bad);font-family:var(--mono)}",
      ".ms-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:8px}",
      ".ms-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".ms-table td{padding:3px 10px 3px 0;white-space:nowrap;color:var(--ink-2)}.ms-table td.never{color:var(--bad)}.ms-table td.late{color:var(--warn)}",
      ".ms-details{margin-top:6px;font-size:var(--fs-xs)}.ms-details summary{cursor:pointer;color:var(--ink-2)}",
      ".ms-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:320px}",
    ].join("\n");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v.toFixed(1) + "s"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function color(side) { return "var(--ms-" + side + ")"; }
  var Axis = {};   // per task: "time" | "steps"

  function draw(host, report, ms, axis, tip) {
    if (!d3) return;
    var W = Math.max(360, host.clientWidth || 640);
    var rows = ms.a.milestones.map(function (m, i) { return { i: i, id: m.id, label: m.label || m.id, a: m, b: ms.b.milestones[i] || { reached: false } }; });
    var maxX = 1e-9;
    ["a", "b"].forEach(function (s) {
      var run = ms[s];
      maxX = Math.max(maxX, axis === "time" ? (run.total_seconds || 0) : Math.max(0, (run.total_steps || 1) - 1));
    });
    var labW = Math.min(220, 8 + 6.4 * rows.reduce(function (m, r) { return Math.max(m, r.label.length); }, 8));
    var m = { l: labW, r: 70, t: 22, b: 10 }, rowH = 22;
    var H = m.t + rows.length * rowH + m.b;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "when each run reached each milestone, along " + (axis === "time" ? "wall-clock time" : "steps"));
    var x = d3.scaleLinear().domain([0, maxX]).nice().range([m.l, W - m.r]);
    x.ticks(6).forEach(function (t) {
      svg.append("line").attr("x1", x(t)).attr("x2", x(t)).attr("y1", m.t - 4).attr("y2", H - m.b).attr("stroke", "var(--rule)");
      svg.append("text").attr("class", "tick").attr("x", x(t)).attr("y", m.t - 8).attr("text-anchor", "middle").text(axis === "time" ? secs(t) : String(t));
    });
    var y = function (i) { return m.t + i * rowH + rowH / 2; };
    rows.forEach(function (r) {
      svg.append("line").attr("class", "rung").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(r.i)).attr("y2", y(r.i));
      var never = !r.a.reached && !r.b.reached;
      svg.append("text").attr("class", "lab" + (never ? " miss" : "")).attr("x", m.l - 8).attr("y", y(r.i) + 4).attr("text-anchor", "end")
        .text(r.label.length > 30 ? r.label.slice(0, 29) + "…" : r.label);
    });
    ["a", "b"].forEach(function (side, si) {
      var pts = rows.filter(function (r) { return r[side].reached; }).map(function (r) {
        var v = axis === "time" ? r[side].seconds : r[side].step;
        return { x: x(isNum(v) ? v : 0), y: y(r.i), r: r, v: v };
      });
      var dy = si === 0 ? -4 : 4;
      if (pts.length > 1) {
        svg.append("path").attr("class", "ladder").attr("data-side", side).attr("fill", "none").attr("stroke", color(side)).attr("stroke-width", 1.6).attr("stroke-opacity", 0.8)
          .attr("d", pts.map(function (p, k) { return (k ? "L" : "M") + p.x + "," + (p.y + dy); }).join(" "));
      }
      pts.forEach(function (p) {
        var late = p.r[side].on_time === false;
        svg.append("circle").attr("class", "mark" + (late ? " late" : "")).attr("data-side", side).attr("data-id", p.r.id)
          .attr("cx", p.x).attr("cy", p.y + dy).attr("r", 4.5).attr("fill", late ? "var(--surface)" : color(side)).attr("stroke", late ? "var(--warn)" : color(side)).attr("stroke-width", 1.6).style("cursor", "pointer")
          .on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: name(report, side) + " · " + p.r.label }, { text: "step " + p.r[side].step + " · " + secs(p.r[side].seconds) + (p.r[side].agent ? " · in " + p.r[side].agent : "") + (late ? " · later than the deadline" : "") }]);
          }).on("pointerleave", tip.hide)
          .on("click", function () { if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, p.r[side].step); });
      });
      rows.forEach(function (r) {
        if (r[side].reached) return;
        svg.append("text").attr("class", "never").attr("data-side", side).attr("data-id", r.id).attr("x", W - m.r + 8).attr("y", y(r.i) + dy + 3)
          .text((side === "a" ? "A" : "B") + " never");
      });
    });
  }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "ms-tip"; tip.hidden = true; root.appendChild(tip);
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

  AgentDiff.block({
    id: "milestones",
    title: "Milestones",
    question: "How far did each run get through the task, and when did it reach each milestone — before the answer?",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) { var ms = ctx.report && ctx.report.milestones; return ms && ms.a && ms.a.measurable ? 0.8 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, ms = report && report.milestones;
      if (!ms || !ms.a || !ms.a.measurable) return ctx.empty(el, "The golden task names no milestones, so progress before the answer is not measured (run batch with --golden).");
      var root = H("div", { class: "ms" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(H("p", { class: "ms-narr", text: ms.narrative || "" }));
      var task = report.task && report.task.id;
      var axis = Axis[task] || "time";
      var bar = H("div", { class: "ms-bar" });
      bar.appendChild(H("span", null, [H("i", { style: { background: color("a") } }), H("span", { text: name(report, "a") + " · " + ms.a.reached + "/" + ms.a.total })]));
      bar.appendChild(H("span", null, [H("i", { style: { background: color("b") } }), H("span", { text: name(report, "b") + " · " + ms.b.reached + "/" + ms.b.total })]));
      var seg = H("span", { class: "seg", role: "group", "aria-label": "axis" });
      ["time", "steps"].forEach(function (k) {
        seg.appendChild(H("button", { text: k, "data-axis": k, "aria-pressed": axis === k ? "true" : "false", onclick: function () { Axis[task] = k; if (AgentDiff._rerender) AgentDiff._rerender(); } }));
      });
      bar.appendChild(seg);
      bar.appendChild(H("span", { text: "a dot = the first step with the milestone's evidence · hollow = later than its deadline · click a dot to open the step" }));
      root.appendChild(bar);
      var host = H("div", { class: "ms-chart" });
      root.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { draw(host, report, ms, axis, tip); }, "milestones:" + task) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) draw(host, report, ms, axis, tip);
      // the table: every number, no hovering
      var details = H("details", { class: "ms-details" }, [H("summary", { text: "every milestone, both runs" })]);
      var t = H("table", { class: "ms-table" });
      t.appendChild(H("tr", null, ["milestone", name(report, "a") + " step", "s", name(report, "b") + " step", "s", "first", "gap"].map(function (h) { return H("th", { text: h }); })));
      (ms.diff && ms.diff.rows ? ms.diff.rows : []).forEach(function (r) {
        var tr = H("tr", { "data-id": r.id });
        tr.appendChild(H("td", { text: r.label }));
        ["a", "b"].forEach(function (s) {
          var v = r[s];
          tr.appendChild(H("td", { class: v.reached ? (v.on_time === false ? "late" : "") : "never", text: v.reached ? String(v.step) : "never" }));
          tr.appendChild(H("td", { class: v.reached ? "" : "never", text: v.reached ? secs(v.seconds) : "—" }));
        });
        tr.appendChild(H("td", { text: r.first === "a" ? name(report, "a") : r.first === "b" ? name(report, "b") : r.first === "tie" ? "tie" : "—" }));
        tr.appendChild(H("td", { text: isNum(r.gap_seconds) ? (r.gap_seconds > 0 ? "+" : "") + secs(Math.abs(r.gap_seconds)).replace(/^/, r.gap_seconds < 0 ? "−" : "") : (isNum(r.gap_steps) ? r.gap_steps + " steps" : "—") }));
        t.appendChild(tr);
      });
      details.appendChild(t);
      details.appendChild(H("p", { class: "ms-note", text: "gap = " + name(report, "a") + " minus " + name(report, "b") + " at the milestone; a positive gap means " + name(report, "a") + " got there later. Source: " + (ms.source || "golden tasks") + "." }));
      root.appendChild(details);
    },
  });
})(typeof window !== "undefined" ? window : this);
