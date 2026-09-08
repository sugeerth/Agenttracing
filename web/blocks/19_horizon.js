/* AgentDiff block — Long horizon: parts and sub-agents.
 *
 * A long run folded into a tree a reader can open on demand — the run,
 * the sub-agents it delegated to (nested), the parts within each, the
 * steps — drawn as a time-weighted icicle for both runs around a shared
 * axis. The gutter names each row; a cell says what it is and how much;
 * hatching is wasted time, red the fault's path, a ring the decisive
 * step. Click a part to zoom into it, a step to open it. One line per
 * run says what mattered; the sub-agents' numbers fold away beneath.
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
      ".hz-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".hz-axis{display:inline-flex;gap:2px}.hz-grp{display:inline-flex;gap:6px;align-items:center;padding-right:10px;border-right:1px solid var(--rule)}",
      ".hz-axis-btn{font:inherit;font-size:var(--fs-xs);padding:0 7px;border:1px solid var(--rule-2);border-radius:999px;background:var(--surface);color:var(--ink-2);cursor:pointer;line-height:1.5}",
      ".hz-axis-btn[aria-pressed=\"true\"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}",
      ".hz-key i{display:inline-block;width:10px;height:8px;border-radius:2px;vertical-align:-1px;margin:0 3px 0 8px}",
      ".hz-key i.w{background:repeating-linear-gradient(45deg,var(--bad) 0 1.5px,transparent 1.5px 4.5px)}.hz-key i.f{background:var(--bad)}.hz-key i.d{border:2px solid var(--bad);border-radius:50%;width:6px;height:6px}",
      ".hz-narr{font-size:var(--fs-m);color:var(--ink);margin:8px 0 0;max-width:100ch}",
      ".hz-sum{font-size:var(--fs-s);color:var(--ink-2);margin:6px 0 0;display:grid;grid-template-columns:auto 1fr;gap:2px 8px;align-items:baseline}",
      ".hz-sum .who{font-weight:600;white-space:nowrap}.hz-sum .who i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:-1px}",
      ".hz-sum .what b{color:var(--ink);font-weight:600}",
      ".hz-more{margin-top:6px;font-size:var(--fs-xs)}.hz-more summary{cursor:pointer;color:var(--ink-3)}",
      ".hz-agents{border-collapse:collapse;font-size:var(--fs-xs);margin-top:6px;font-variant-numeric:tabular-nums}",
      ".hz-agents th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 12px 4px 0}",
      ".hz-agents td{padding:3px 12px 3px 0;border-top:1px solid var(--rule);color:var(--ink-2)}",
      ".hz-agents td.n{text-align:right}.hz-agents td i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:-1px}",
      ".hz-full{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0;max-width:100ch}",
    ].join("");
    document.head.appendChild(node);
  }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  var HzView = {};   // per task: icicle | tree | diff
  var HzLabels = {};   // per task: the diff's labels, words | compact
  function secs(v) { return typeof v !== "number" ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v.toFixed(1) + "s"; }
  function walk(n, out) { out.push(n); (n.children || []).forEach(function (c) { walk(c, out); }); return out; }

  /* the one line per run: the longest part and the costliest sub-agent */
  function gist(H, h) {
    var nodes = walk(h.tree, []);
    var eps = nodes.filter(function (n) { return n.kind === "episode"; });
    var longest = eps.length ? eps.reduce(function (a, b) { return b.seconds > a.seconds ? b : a; }) : null;
    var frag = H("span", { class: "what" });
    var bits = [];
    if (longest && h.total_s) bits.push(H("span", null, [H("span", { text: "longest part " }), H("b", { text: longest.label }), H("span", { text: " " + secs(longest.seconds) + " (" + Math.round(100 * longest.seconds / h.total_s) + "%" + (longest.wasted_s ? ", " + Math.round(100 * longest.wasted_s / Math.max(1e-9, longest.seconds)) + "% wasted" : "") + ")" })]));
    if (h.agents && h.agents.length) {
      var top = h.agents[0];
      bits.push(H("span", null, [H("span", { text: " · costliest sub-agent " }), H("b", { text: top.agent }), H("span", { text: " " + secs(top.seconds) + (top.wasted_s ? ", " + secs(top.wasted_s) + " wasted" : "") + (top.errors ? ", " + top.errors + " error" + (top.errors === 1 ? "" : "s") : "") })]));
    }
    var dec = nodes.filter(function (n) { return n.kind === "episode" && n.decisive; })[0];
    if (dec) bits.push(H("span", null, [H("span", { text: " · decisive step in " }), H("b", { text: dec.label }), H("span", { text: dec.agent && dec.agent !== h.tree.label ? " (" + dec.agent + ")" : "" })]));
    if (!bits.length) bits.push(H("span", { text: h.summary }));
    bits.forEach(function (b) { frag.appendChild(b); });
    return frag;
  }

  AgentDiff.block({
    id: "horizon",
    title: "Long horizon: parts and sub-agents",
    storyTitle: "Parts and sub-agents",
    question: "How does a long run break into parts and delegations, which took the time, and where did the fault enter?",
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
      el.appendChild(H("div", { class: "hz-bar" }, [H("span", { class: "hz-grp" }, [H("span", { text: "view" })]), H("span", { class: "hz-grp" }, [H("span", { text: "width ∝" }), ctl]),
        H("span", { class: "hz-key" }, [H("i", { class: "w" }), H("span", { text: "wasted" }), H("i", { class: "f" }), H("span", { text: "fault" }), H("i", { class: "d" }), H("span", { text: "decisive" })]),
        H("span", { text: "· click a part to zoom, a step to open" })]));
      // two drawings of one tree: the icicle (time along x) and the nodes-and-links (delegation as depth)
      var viewCtl = H("span", { class: "hz-axis", role: "group", "aria-label": "how to draw the tree" });
      [["icicle", "the run over time, rows outward from the axis"], ["tree", "the run and its sub-agents as nodes and links"], ["diff", "the two runs' delegation graphs aligned: who delegated to whom, how often, and what only one run did"]].forEach(function (pair) {
        var b = H("button", { type: "button", class: "hz-axis-btn view-" + pair[0], text: pair[0], title: pair[1], "aria-pressed": (HzView[taskKey] || "icicle") === pair[0] ? "true" : "false" });
        b.addEventListener("click", function () { HzView[taskKey] = pair[0]; AgentDiff._rerender ? AgentDiff._rerender() : null; });
        viewCtl.appendChild(b);
      });
      el.firstChild.firstChild.appendChild(viewCtl);
      if ((HzView[taskKey] || "icicle") === "diff") {
        // the diff's labels two ways: in words where the runs differ, or compact numbers everywhere
        var labelCtl = H("span", { class: "hz-axis", role: "group", "aria-label": "how the diff is labelled" });
        [["words", "names and one line per run; links labelled only where the runs differ"], ["compact", "one line per agent and a count on every link, as " + name(report, "a") + " · " + name(report, "b")]].forEach(function (pair) {
          var b = H("button", { type: "button", class: "hz-axis-btn labels-" + pair[0], text: pair[0], title: pair[1], "aria-pressed": (HzLabels[taskKey] || "words") === pair[0] ? "true" : "false" });
          b.addEventListener("click", function () { HzLabels[taskKey] = pair[0]; AgentDiff._rerender ? AgentDiff._rerender() : null; });
          labelCtl.appendChild(b);
        });
        el.firstChild.appendChild(H("span", { class: "hz-grp" }, [H("span", { text: "labels" }), labelCtl]));
      }
      var host = H("div", { class: "hz-chart" });
      var drawn = null;
      try {
        var view = HzView[taskKey] || "icicle";
        drawn = view === "tree"
          ? AgentDiff.charts.agentTree(host, hz, { key: "agent-tree:" + taskKey, onSelect: function (side, key) { HzView[taskKey] = "icicle"; AgentDiff.charts.horizonZoom.set(taskKey, side, key); AgentDiff._rerender ? AgentDiff._rerender() : null; } })
          : view === "diff" && hz.diff
          ? AgentDiff.charts.graphDiff(host, hz, { key: "graph-diff:" + taskKey, names: { a: name(report, "a"), b: name(report, "b") }, compact: HzLabels[taskKey] === "compact" })
          : AgentDiff.charts.horizon(host, ctx);
      } catch (err) { console.warn("AgentDiff horizon: chart failed", err); }
      if (drawn) el.appendChild(drawn);
      if (hz.narrative) el.appendChild(H("p", { class: "hz-narr", text: hz.narrative }));
      var sum = H("div", { class: "hz-sum" });
      ["a", "b"].forEach(function (side) {
        var h = hz[side];
        if (!h) return;
        sum.appendChild(H("span", { class: "who", "data-side": side }, [H("i", { style: { background: "var(--" + side + ")" } }), H("span", { text: name(report, side) })]));
        sum.appendChild(gist(H, h));
      });
      el.appendChild(sum);
      // the numbers, folded away: every sub-agent, and the full sentence per run
      var more = H("details", { class: "hz-more" });
      more.appendChild(H("summary", { text: "numbers per sub-agent, and the full account" }));
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
        more.appendChild(H("div", { class: "scroll-x" }, [t]));
      } else {
        more.appendChild(H("p", { class: "hz-full", text: "No delegation recorded: the root agent acted throughout. `with recorder.span(\"name\"):` (or a step's `span` field) makes sub-agents appear as nested rows." }));
      }
      ["a", "b"].forEach(function (side) { if (hz[side]) more.appendChild(H("p", { class: "hz-full", "data-side": side, text: hz[side].summary })); });
      more.appendChild(H("p", { class: "hz-full", text: (hz.a || hz.b).basis }));
      el.appendChild(more);
    },
  });
})(typeof window !== "undefined" ? window : this);
