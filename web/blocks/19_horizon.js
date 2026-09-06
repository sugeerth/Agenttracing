/* AgentDiff block — Long horizon: subdivisions and sub-agents.
 *
 * A long run folded into a tree a reader can open on demand: the run,
 * the delegation spans inside it (which sub-agent acted, nested), the
 * subdivisions within each span, and the steps — drawn as a
 * time-weighted icicle for both runs around a shared axis (the body
 * chart's axis: seconds, tokens or steps), wasted time hatched, the
 * fault's path red, the decisive step ringed. Click a node to zoom into
 * it. Beneath: the summary sentence per run and the sub-agents' ledger
 * — delegations, steps, seconds, wasted, errors. From `report.horizon`.
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
      ".hz-sum{font-size:var(--fs-s);color:var(--ink-2);margin:6px 0 0;max-width:100ch}",
      ".hz-sum b{color:var(--ink)}",
      ".hz-agents{border-collapse:collapse;font-size:var(--fs-xs);margin-top:8px;font-variant-numeric:tabular-nums}",
      ".hz-agents th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 12px 4px 0}",
      ".hz-agents td{padding:3px 12px 3px 0;border-top:1px solid var(--rule);color:var(--ink-2)}",
      ".hz-agents td.n{text-align:right}.hz-agents td i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:-1px}",
      ".hz-axis{display:inline-flex;gap:3px;margin:0 0 6px}",
      ".hz-axis-btn{font:inherit;font-size:var(--fs-xs);padding:1px 8px;border:1px solid var(--rule-2);border-radius:999px;background:var(--surface);color:var(--ink-2);cursor:pointer}",
      ".hz-axis-btn[aria-pressed=\"true\"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}",
      ".hz-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:100ch}",
    ].join("");
    document.head.appendChild(node);
  }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function secs(v) { return typeof v !== "number" ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v.toFixed(1) + "s"; }

  AgentDiff.block({
    id: "horizon",
    title: "Long horizon: subdivisions and sub-agents",
    storyTitle: "Subdivisions and sub-agents",
    question: "How does a long run break into parts and delegations, which of them took the time, and where did the fault enter?",
    group: "trajectory",
    size: "wide",

    relevance: function (ctx) {
      var hz = ctx.report && ctx.report.horizon;
      if (!hz || !(hz.a || hz.b)) return 0;
      var steps = Math.max((hz.a || {}).steps || 0, (hz.b || {}).steps || 0);
      var agents = Math.max(((hz.a || {}).agents || []).length, ((hz.b || {}).agents || []).length);
      return agents ? 0.8 : steps > 12 ? 0.72 : 0.5;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, hz = report && report.horizon;
      if (!hz) return ctx.empty(el, "This report carries no horizon tree (it predates the section).");
      var taskKey = report.task && report.task.id ? report.task.id : "task";
      var axisNow = AgentDiff.charts && AgentDiff.charts.bodyAxis ? AgentDiff.charts.bodyAxis.get(taskKey) : "time";
      var ctl = H("span", { class: "hz-axis", role: "group", "aria-label": "what the widths measure" });
      [["time", "widths ∝ seconds"], ["tokens", "widths ∝ tokens"], ["steps", "widths ∝ steps"]].forEach(function (pair) {
        var b = H("button", { type: "button", class: "hz-axis-btn axis-" + pair[0], text: pair[0], title: pair[1], "aria-pressed": axisNow === pair[0] ? "true" : "false" });
        b.addEventListener("click", function () { AgentDiff.charts.bodyAxis.set(taskKey, pair[0]); AgentDiff._rerender ? AgentDiff._rerender() : null; });
        ctl.appendChild(b);
      });
      el.appendChild(H("div", null, [H("span", { class: "hz-note", text: "width ∝ " }), ctl]));
      var host = H("div", { class: "hz-chart" });
      var drawn = null;
      try { drawn = AgentDiff.charts && AgentDiff.charts.horizon ? AgentDiff.charts.horizon(host, ctx) : null; } catch (err) { console.warn("AgentDiff horizon: chart failed", err); }
      if (drawn) el.appendChild(drawn);
      ["a", "b"].forEach(function (side) {
        var h = hz[side];
        if (!h) return;
        el.appendChild(H("p", { class: "hz-sum", "data-side": side }, [H("b", { text: name(report, side) + " · " }), H("span", { text: h.summary })]));
      });
      var agents = [];
      ["a", "b"].forEach(function (side) { ((hz[side] || {}).agents || []).forEach(function (a) { agents.push(Object.assign({ side: side }, a)); }); });
      if (agents.length) {
        var t = H("table", { class: "hz-agents" });
        t.appendChild(H("tr", null, ["run", "sub-agent", "delegations", "steps", "seconds", "wasted", "errors"].map(function (x) { return H("th", { text: x }); })));
        agents.forEach(function (a) {
          t.appendChild(H("tr", { "data-side": a.side, "data-agent": a.agent }, [
            H("td", null, [H("i", { style: { background: "var(--" + a.side + ")" } }), H("span", { text: name(report, a.side) })]),
            H("td", { text: a.agent }), H("td", { class: "n", text: String(a.delegations) }), H("td", { class: "n", text: String(a.steps) }),
            H("td", { class: "n", text: secs(a.seconds) }), H("td", { class: "n", text: a.wasted_s ? secs(a.wasted_s) : "—" }), H("td", { class: "n", text: String(a.errors) })]));
        });
        el.appendChild(H("div", { class: "scroll-x" }, [t]));
      } else {
        el.appendChild(H("p", { class: "hz-note", text: "No delegation spans recorded: the root agent acted throughout. Record sub-agents with `with recorder.span(\"name\"):` (or a step's `span` field) and they appear as nested rows." }));
      }
      el.appendChild(H("p", { class: "hz-note", text: (hz.a || hz.b).basis }));
    },
  });
})(typeof window !== "undefined" ? window : this);
