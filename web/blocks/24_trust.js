/* AgentDiff block — Trust & behaviour.
 *
 * How each agent behaved — tool calls, stops, permissions, determinism —
 * and how far the data underneath can be trusted. One ledger, two
 * columns (A | B), every cell a count from `report.trust` and every
 * grade a rubric whose deductions are sentences under a fold. No chart:
 * the numbers are the picture; a tiny inline bar scales the tool calls
 * and the measured shares so the eye can compare without reading.
 *
 * Reads `report.trust` ({version, a, b, narrative}); each side carries
 * behaviour, permissions, determinism, data and grade {score, label,
 * reasons[]} — see deepcompare/trust.py for the rubric.
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
      ".tr{--tr-a:var(--a);--tr-b:var(--b)}",
      ".tr-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 10px;max-width:90ch}",
      ".tr-ledger{border-collapse:collapse;width:100%;table-layout:fixed;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
      ".tr-ledger th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 6px 0;vertical-align:bottom;overflow-wrap:anywhere}",
      ".tr-ledger th.side{color:var(--ink);font-weight:600}",
      ".tr-ledger th.side i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:-1px}",
      ".tr-ledger th.side i.a{background:var(--tr-a)}.tr-ledger th.side i.b{background:var(--tr-b)}",
      ".tr-ledger td{padding:4px 10px 4px 0;color:var(--ink-2);vertical-align:top;overflow-wrap:anywhere}",
      ".tr-ledger td.lab{color:var(--ink-3);font-family:var(--mono);white-space:normal}",
      ".tr-ledger td.bad{color:var(--bad);font-weight:600}.tr-ledger td.warn{color:var(--warn)}.tr-ledger td.dim{color:var(--ink-3)}",
      ".tr-ledger .bar{display:block;height:4px;border-radius:0 2px 2px 0;margin-top:3px;min-width:1px;max-width:100%}",
      ".tr-ledger .bar.a{background:var(--tr-a)}.tr-ledger .bar.b{background:var(--tr-b)}.tr-ledger .bar.dim{background:var(--rule-2,var(--rule))}",
      ".tr-chip{display:inline-block;font-family:var(--mono);border-radius:9px;padding:1px 8px;color:var(--bg);font-weight:600}",
      ".tr-chip.good{background:var(--good)}.tr-chip.warn{background:var(--warn)}.tr-chip.bad{background:var(--bad)}",
      ".tr-flag{display:inline-block;font-family:var(--mono);border-radius:9px;padding:0 6px;background:var(--surface-2);color:var(--ink-2);margin-right:4px}",
      ".tr-flag.syn{color:var(--warn)}",
      ".tr-share{display:inline-block;min-width:88px;margin:2px 10px 0 0;color:var(--ink-3)}",
      ".tr-details{margin-top:8px;font-size:var(--fs-xs)}.tr-details summary{cursor:pointer;color:var(--ink-2)}",
      ".tr-details ul{margin:4px 0 6px;padding-left:18px;color:var(--ink-2)}.tr-details li{margin:1px 0}",
      ".tr-details p{margin:4px 0 0;color:var(--ink-3);max-width:90ch}",
      ".tr-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:100ch}",
    ].join("\n");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function pct(v) { return isNum(v) ? Math.round(v * 100) + "%" : "—"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function chipClass(label) { return label === "high" ? "good" : label === "medium" ? "warn" : "bad"; }
  function forbiddenCount(p) {
    var steps = {};
    (p.forbidden_calls || []).concat(p.forbidden_patterns || []).forEach(function (f) { steps[f.step] = true; });
    return Object.keys(steps).length;
  }

  /* each row: key, label, and a cell builder (H, run, side, both) → td attrs + kids */
  function rows(H, t) {
    var maxCalls = Math.max(1, (t.a.behaviour.tool_calls || 0), (t.b.behaviour.tool_calls || 0));
    function bar(side, v, max, dim) {
      return H("i", { class: "bar " + side + (dim ? " dim" : ""), style: { width: Math.max(1, Math.round(100 * (v || 0) / Math.max(1e-9, max))) + "%" } });
    }
    function share(side, label, v) {
      return H("span", { class: "tr-share", "data-share": label }, [label + " " + pct(v), bar(side, isNum(v) ? v : 0, 1, !isNum(v) || v < 0.5)]);
    }
    return [
      { key: "grade", label: "trust grade", cell: function (r, side) {
        var g = r.grade; return { kids: [H("span", { class: "tr-chip " + chipClass(g.label), text: g.label + " " + g.score.toFixed(2) })] }; } },
      { key: "tool_calls", label: "tool calls", cell: function (r, side) {
        var b = r.behaviour; return { kids: [String(b.tool_calls), bar(side, b.tool_calls, maxCalls)] }; } },
      { key: "distinct_tools", label: "distinct tools", cell: function (r) {
        var b = r.behaviour, names = Object.keys(b.tools || {}).sort(function (p, q) { return b.tools[q] - b.tools[p] || (p < q ? -1 : 1); });
        return { kids: [String(b.distinct_tools) + (names.length ? " · " + names.slice(0, 4).map(function (k) { return k + " ×" + b.tools[k]; }).join(", ") + (names.length > 4 ? ", …" : "") : "")] }; } },
      { key: "thinking_steps", label: "thinking steps", cell: function (r) { return { kids: [String(r.behaviour.thinking_steps) + " of " + r.behaviour.steps] }; } },
      { key: "stop", label: "stop", cell: function (r) {
        var b = r.behaviour, harness = b.stopped_by === "harness";
        var text = b.answered ? "answered" : "no answer";
        if (b.termination) text += " · " + b.termination.replace(/_/g, " ");
        if (harness) text += " · by harness";
        else if (b.stopped_by === "unknown") text += " · undeclared";
        return { cls: harness ? "warn" : "", kids: [text] }; } },
      { key: "faults", label: "loops · retries · errors", cell: function (r, side) {
        var b = r.behaviour;
        return { cls: b.errors > 3 ? "warn" : "", kids: [
          H("span", { "data-key": "loops", "data-side": side, text: String(b.loops) }), " · ",
          H("span", { "data-key": "retries", "data-side": side, text: String(b.retries) }), " · ",
          H("span", { "data-key": "errors", "data-side": side, text: String(b.errors) })] }; } },
      { key: "effects", label: "effects read · write · undeclared", cell: function (r, side) {
        var e = r.permissions.effects;
        return { kids: [
          H("span", { "data-key": "reads", "data-side": side, text: String(e.read) }), " · ",
          H("span", { "data-key": "writes", "data-side": side, text: String(e.write) }), " · ",
          H("span", { "data-key": "undeclared", "data-side": side, text: String(e.undeclared) }),
          (r.permissions.verify_after_write === false ? " · last write unverified" : r.permissions.verify_after_write === true ? " · verified after write" : "")] }; } },
      { key: "forbidden", label: "forbidden calls", cell: function (r) {
        var p = r.permissions, n = forbiddenCount(p);
        var names = {}; (p.forbidden_calls || []).concat(p.forbidden_patterns || []).forEach(function (f) { names[f.name] = true; });
        return { cls: n > 0 ? "bad" : "", kids: [String(n) + (n ? " · " + Object.keys(names).sort().join(", ") : "")] }; } },
      { key: "writes_without_read", label: "writes without read", cell: function (r) {
        var n = r.permissions.writes_without_read; return { cls: n > 0 ? "bad" : "", kids: [String(n)] }; } },
      { key: "external", label: "external calls", cell: function (r) {
        var p = r.permissions, extra = [];
        if (p.mcp_servers && p.mcp_servers.length) extra.push("mcp: " + p.mcp_servers.join(", "));
        if (isNum(p.handoffs) && p.handoffs) extra.push(p.handoffs + " handoff(s)");
        return { kids: [String(p.external) + (extra.length ? " · " + extra.join(" · ") : "")] }; } },
      { key: "sub_agents", label: "sub-agents · depth", cell: function (r) {
        var b = r.behaviour; return { kids: [b.sub_agents + " · " + b.max_depth + (b.delegations ? " (" + b.delegations + " delegation" + (b.delegations === 1 ? "" : "s") + ")" : "")] }; } },
      { key: "replay", label: "replay verification", cell: function (r) {
        var d = r.determinism, text = d.replay_verification || "not the diagnosed side";
        if (d.replay_reproduced === true) text += " · rerun reproduced";
        else if (d.replay_reproduced === false) text += " · rerun diverged" + (isNum(d.replay_first_divergence) ? " at step " + d.replay_first_divergence : "");
        return { cls: d.replay_verification ? (d.replay_verification === "replay-verified" ? "" : "warn") : "dim", kids: [text] }; } },
      { key: "consistency", label: "run consistency", cell: function (r) {
        var c = r.determinism.run_consistency, t = r.determinism.temperature;
        var text = c ? ((c.verdict ? c.verdict : "") + (isNum(c.successes) && isNum(c.runs) ? " " + c.successes + "/" + c.runs : "") +
          (isNum(c.equality_rate) ? " · equality " + pct(c.equality_rate) : "") + (isNum(c.distinct_answers) ? " · " + c.distinct_answers + " distinct answer(s)" : "")).trim() : "single run";
        if (t !== null && t !== undefined) text += " · T " + (isNum(t) ? t : t.min + "–" + t.max);
        return { cls: c ? "" : "dim", kids: [text] }; } },
      { key: "data", label: "data", cell: function (r, side) {
        var d = r.data, kids = [];
        kids.push(H("span", { class: "tr-flag", text: d.adapter || "adapter unknown" }));
        kids.push(H("span", { class: "tr-flag", text: "graded by " + (d.graded_by || "—") }));
        if (d.synthetic) kids.push(H("span", { class: "tr-flag syn", "data-synthetic": "1", text: "SYNTHETIC" }));
        if (d.spans_recorded) kids.push(H("span", { class: "tr-flag", text: "spans" }));
        kids.push(H("br"));
        kids.push(share(side, "latency", d.latency_measured_share));
        kids.push(share(side, "tokens", d.tokens_measured_share));
        kids.push(share(side, "effects", d.effects_declared_share));
        return { cls: d.synthetic ? "warn" : "", kids: kids }; } },
    ];
  }

  AgentDiff.block({
    id: "trust",
    title: "Trust & behaviour",
    question: "How did each agent behave — tool calls, stops, permissions, determinism — and how far can the data be trusted?",
    group: "outcome",
    size: "wide",
    relevance: function (ctx) { var t = ctx.report && ctx.report.trust; return t && t.a ? 0.8 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, t = report && report.trust;
      if (!t || !t.a || !t.b) return ctx.empty(el, t && t.narrative ? t.narrative : "This report carries no trust section.");
      var root = H("div", { class: "tr" });
      el.appendChild(root);
      root.appendChild(H("p", { class: "tr-narr", text: t.narrative || "" }));
      var table = H("table", { class: "tr-ledger", "aria-label": "behaviour and trust, both runs" });
      var head = H("tr", null, [H("th", { text: "" })]);
      ["a", "b"].forEach(function (side) {
        head.appendChild(H("th", { class: "side", "data-side": side }, [H("i", { class: side }), name(report, side)]));
      });
      table.appendChild(head);
      rows(H, t).forEach(function (row) {
        var tr = H("tr", { "data-row": row.key }, [H("td", { class: "lab", text: row.label })]);
        ["a", "b"].forEach(function (side) {
          var spec = row.cell(t[side], side) || {};
          tr.appendChild(H("td", { "data-key": row.key, "data-side": side, class: spec.cls || null }, spec.kids || []));
        });
        table.appendChild(tr);
      });
      root.appendChild(table);
      ["a", "b"].forEach(function (side) {
        var r = t[side], g = r.grade;
        var details = H("details", { class: "tr-details", "data-side": side }, [
          H("summary", { text: "why " + name(report, side) + " grades " + g.label + " (" + g.score.toFixed(2) + ")" + (g.reasons.length ? " — " + g.reasons.length + " deduction" + (g.reasons.length === 1 ? "" : "s") : " — no deduction") })]);
        if (g.reasons.length) details.appendChild(H("ul", null, g.reasons.map(function (s) { return H("li", { text: s }); })));
        details.appendChild(H("p", { text: r.narrative || "" }));
        root.appendChild(details);
      });
      if (t.note) root.appendChild(H("p", { class: "tr-note", text: t.note }));
    },
  });
})(typeof window !== "undefined" ? window : this);
