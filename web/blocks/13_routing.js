/* AgentDiff block — Routing.
 *
 * What a router would read off this batch: per task family, each agent's
 * success rate with its interval, cost, latency, steps and tool calls,
 * and the pick under the stated objective — 'clear' only when the top
 * two intervals do not overlap, 'overlapping' (either) when they do,
 * 'insufficient' under three runs. Reads `aggregate.routing`; every
 * number is a mean or a count over the runs listed.
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
      ".rt-lede{font-size:var(--fs-s);color:var(--ink-3);margin:0 0 8px}",
      ".rt-table{border-collapse:collapse;width:100%;font-size:var(--fs-s)}",
      ".rt-table th{text-align:left;font-family:var(--mono);font-weight:500;font-size:var(--fs-xs);color:var(--ink-3);padding:4px 8px 6px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".rt-table td{padding:5px 8px 5px 0;border-top:1px solid var(--rule);vertical-align:top}",
      ".rt-table td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--ink-2)}",
      ".rt-table td.fam{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2)}",
      ".rt-pick{font-weight:600}",
      ".rt-conf{font-family:var(--mono);font-size:var(--fs-xs);padding:1px 7px;border-radius:999px;border:1px solid var(--rule-2)}",
      ".rt-conf.clear{color:var(--good);border-color:var(--good)}",
      ".rt-conf.overlapping{color:var(--warn);border-color:var(--warn)}",
      ".rt-conf.insufficient{color:var(--ink-3)}",
      ".rt-bar{display:inline-block;height:6px;border-radius:3px;background:var(--rule-2);vertical-align:middle;position:relative;width:80px;margin-right:6px}",
      ".rt-bar i{position:absolute;top:0;height:6px;border-radius:3px;background:var(--accent);opacity:.75}",
      ".rt-bar b{position:absolute;top:-2px;width:2px;height:10px;background:var(--ink)}",
      ".rt-why{font-size:var(--fs-xs);color:var(--ink-3)}",
      ".rt-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:72ch}",
    ].join("");
    document.head.appendChild(node);
  }
  function isNum(v) { return typeof v === "number" && isFinite(v); }

  AgentDiff.block({
    id: "routing",
    title: "Routing",
    question: "Which agent should a router pick for which kind of task, and how sure is that?",
    group: "other",
    size: "wide",

    relevance: function (ctx) {
      var rt = ctx.aggregate && ctx.aggregate.routing;
      if (!rt || !rt.families || !Object.keys(rt.families).length) return 0;
      var agents = rt.overall && rt.overall.candidates ? rt.overall.candidates.length : 0;
      return agents >= 2 ? 0.8 : 0.4;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, F = ctx.fmt;
      var rt = ctx.aggregate && ctx.aggregate.routing;
      if (!rt) return ctx.empty(el, "This batch carries no routing table.");
      var fams = Object.keys(rt.families || {});
      var ov = rt.overall || {};
      el.appendChild(H("p", { class: "rt-lede", text:
        "Objective: " + rt.objective + ". The pick ranks by the lower bound of each agent's 95% interval on success, then cost. " +
        (ov.pick ? "Over every task: " + ov.pick + "." : "") }));
      var table = H("table", { class: "rt-table" });
      table.appendChild(H("tr", null, ["family", "agent", "success [95% interval]", "n", "cost", "latency", "steps", "tools", "pick"].map(function (t) { return H("th", { text: t }); })));
      fams.forEach(function (fam) {
        var row = rt.families[fam];
        (row.candidates || []).forEach(function (c, i) {
          var f = c.features || {};
          var lo = f.ci95 ? f.ci95[0] : 0, hi = f.ci95 ? f.ci95[1] : 0;
          var bar = H("span", { class: "rt-bar", title: "interval " + lo.toFixed(2) + "–" + hi.toFixed(2) }, [
            H("i", { style: { left: Math.round(lo * 100) * 0.8 + "px", width: Math.max(2, Math.round((hi - lo) * 100) * 0.8) + "px" } }),
            isNum(f.rate) ? H("b", { style: { left: Math.round(f.rate * 100) * 0.8 + "px" } }) : null,
          ]);
          var tr = H("tr", { "data-family": fam, "data-agent": c.agent, "data-rank": String(i) }, [
            H("td", { class: "fam", text: i === 0 ? fam : "" }),
            H("td", { class: i === 0 && row.confidence !== "insufficient" ? "rt-pick" : "", text: c.agent }),
            H("td", { class: "num" }, [bar, H("span", { text: isNum(f.rate) ? (f.rate * 100).toFixed(0) + "% [" + lo.toFixed(2) + "–" + hi.toFixed(2) + "]" : "—" })]),
            H("td", { class: "num", text: String(f.n) }),
            H("td", { class: "num", text: isNum(f.cost_usd) ? "$" + f.cost_usd.toFixed(4) : "—" }),
            H("td", { class: "num", text: isNum(f.latency_s) ? F.sec(f.latency_s) : "—" }),
            H("td", { class: "num", text: isNum(f.steps) ? f.steps.toFixed(1) : "—" }),
            H("td", { class: "num", text: isNum(f.tool_calls) ? f.tool_calls.toFixed(1) : "—" }),
            i === 0 ? H("td", null, [H("span", { class: "rt-conf " + row.confidence, text: row.confidence === "overlapping" ? "either" : row.confidence }),
                                     H("div", { class: "rt-why", text: row.why || "" })]) : H("td"),
          ]);
          table.appendChild(tr);
        });
      });
      el.appendChild(H("div", { class: "scroll-x" }, [table]));
      if (rt.note) el.appendChild(H("p", { class: "rt-note", text: rt.note }));
    },
  });

})(typeof window !== "undefined" ? window : this);
