/* AgentDiff block — Tool behaviour, and the tool dossier on demand.
 *
 * The statistics settle who won; this shows how each agent used each
 * tool: calls, distinct inputs, repeats, the longest run of identical
 * calls, errors, wasted calls, latency, and which sub-agents touched it.
 * Under it, the suggestions for the next prompt derived from the
 * contrast — each a sentence with its evidence and a copy button.
 *
 * Any tool anywhere on the page opens its dossier: the same numbers for
 * one tool, both runs, the agents that touched it, a strip of its calls
 * (click one to open the step), sample inputs, and the suggestions that
 * concern it. Open it with AgentDiff.tools.open(name) or by dispatching
 * `agentdiff:select-tool` with {name} on the document; the charts do
 * that on a double-click of a tool step, the tables on a click of the
 * tool's name.
 *
 * Reads `report.tools_profile` (see deepcompare/toolprofile.py).
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
      ".tb{--tb-a:var(--a);--tb-b:var(--b)}",
      ".tb-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".tb-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
      ".tb-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".tb-table td{padding:3px 10px 3px 0;white-space:nowrap;color:var(--ink-2);vertical-align:top}",
      ".tb-table td.tool button{font:inherit;font-family:var(--mono);color:var(--ink);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline dotted var(--rule-2);text-underline-offset:3px}",
      ".tb-table td.tool button:hover{color:var(--accent)}",
      ".tb-table .pair{display:inline-grid;grid-template-columns:auto 1fr;gap:1px 6px;align-items:center;min-width:110px}",
      ".tb-table .pair i{display:block;height:5px;border-radius:0 3px 3px 0;min-width:1px}.tb-table .pair i.a{background:var(--tb-a)}.tb-table .pair i.b{background:var(--tb-b)}",
      ".tb-table .bad{color:var(--bad)}.tb-table .warn{color:var(--warn)}.tb-table .dim{color:var(--ink-3)}",
      ".tb-agents span{display:inline-block;margin:0 4px 0 0;color:var(--ink-3)}",
      ".tb-sugg{margin:12px 0 0;padding:0;list-style:none}",
      ".tb-sugg li{margin:0 0 8px;font-size:var(--fs-s);color:var(--ink);max-width:100ch;display:grid;grid-template-columns:1fr auto;gap:2px 10px;align-items:start}",
      ".tb-sugg li .ev{font-size:var(--fs-xs);color:var(--ink-3);grid-column:1/-1}",
      ".tb-sugg li .k{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);margin-right:6px}",
      ".tb-sugg button, .tb-copyall{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 8px;cursor:pointer}",
      ".tb-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0 4px;font-size:var(--fs-xs);color:var(--ink-3);text-transform:uppercase;letter-spacing:.08em}",
      ".tb-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:6px;max-width:100ch}",
      /* the dossier */
      ".tool-dossier{position:fixed;right:14px;top:60px;width:380px;max-width:calc(100vw - 28px);max-height:calc(100vh - 80px);overflow:auto;background:var(--surface);box-shadow:var(--shadow);border-radius:10px;padding:12px 14px 14px;z-index:40;font-size:var(--fs-s);color:var(--ink)}",
      ".tool-dossier[hidden]{display:none}",
      ".tool-dossier h4{margin:0 0 6px;font-family:var(--mono);font-size:var(--fs-m);font-weight:600;display:flex;gap:8px;align-items:center}",
      ".tool-dossier h4 .x{margin-left:auto;font:inherit;border:0;background:none;color:var(--ink-3);cursor:pointer}",
      ".tool-dossier .chips span{display:inline-block;font-size:var(--fs-xs);color:var(--ink-3);background:var(--surface-2);border-radius:999px;padding:0 7px;margin:0 4px 6px 0}",
      ".tool-dossier table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin:4px 0 8px}",
      ".tool-dossier th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:1px 8px 3px 0;border-bottom:1px solid var(--rule)}",
      ".tool-dossier td{padding:2px 8px 2px 0;color:var(--ink-2)}.tool-dossier td.k{color:var(--ink-3)}.tool-dossier td.bad{color:var(--bad)}",
      ".tool-dossier .who{font-size:var(--fs-xs);color:var(--ink-2);margin:0 0 6px}.tool-dossier .who b{font-weight:600}",
      ".tool-dossier .strip svg{display:block;width:100%;height:auto}",
      ".tool-dossier .strip .tick{font-size:var(--fs-xs);fill:var(--ink-3)}",
      ".tool-dossier details{font-size:var(--fs-xs);color:var(--ink-2);margin-top:6px}.tool-dossier details summary{cursor:pointer;color:var(--ink-3)}",
      ".tool-dossier pre{white-space:pre-wrap;word-break:break-word;font-size:var(--fs-xs);background:var(--surface-2);border-radius:6px;padding:4px 6px;margin:3px 0}",
      ".tool-dossier .sug{font-size:var(--fs-xs);color:var(--ink);margin:6px 0 0}",
    ].join("\n");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v >= 1 ? v.toFixed(1) + "s" : v.toFixed(2) + "s"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function color(side) { return "var(--tb-" + side + ")"; }
  var Current = { report: null };

  function copy(text, btn) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text);
    } catch (err) { /* no clipboard */ }
    if (btn) { var was = btn.textContent; btn.textContent = "copied"; setTimeout(function () { btn.textContent = was; }, 900); }
  }

  // ------------------------------------------------------------ dossier
  function dossierRow(H, label, a, b, cls) {
    return H("tr", null, [H("td", { class: "k", text: label }), H("td", { class: cls && cls.a ? cls.a : "", text: a }), H("td", { class: cls && cls.b ? cls.b : "", text: b })]);
  }
  function strip(H, host, report, toolName, row) {
    if (!d3) return;
    var W = 340, Hh = 46, m = { l: 6, r: 6, t: 14 };
    var maxStep = 1;
    ["a", "b"].forEach(function (s) { var st = report[s] && report[s].steps; if (st && st.length) maxStep = Math.max(maxStep, st.length - 1); });
    var x = d3.scaleLinear().domain([0, maxStep]).range([m.l, W - m.r]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img").attr("aria-label", "every call of " + toolName + " by step, both runs");
    svg.append("text").attr("class", "tick").attr("x", m.l).attr("y", 9).text("step 0");
    svg.append("text").attr("class", "tick").attr("x", W - m.r).attr("y", 9).attr("text-anchor", "end").text("step " + maxStep);
    ["a", "b"].forEach(function (s, si) {
      var t = row[s];
      var y = m.t + 8 + si * 16;
      svg.append("line").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y).attr("y2", y).attr("stroke", "var(--rule)");
      (t && t.steps || []).forEach(function (c) {
        svg.append("line").attr("class", "call").attr("data-side", s).attr("data-step", c.step)
          .attr("x1", x(c.step)).attr("x2", x(c.step)).attr("y1", y - 6).attr("y2", y + 6)
          .attr("stroke", c.error ? "var(--bad)" : color(s)).attr("stroke-width", c.error ? 2.2 : 1.6).attr("stroke-opacity", c.wasted ? 0.45 : 1).style("cursor", "pointer")
          .append("title").text(name(report, s) + " · step " + c.step + " · " + secs(c.seconds) + (c.error ? " · error" : "") + (c.wasted ? " · wasted" : "") + " · " + c.agent);
        svg.selectAll("line.call[data-step='" + c.step + "'][data-side='" + s + "']").on("click", function () {
          if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, s, c.step);
        });
      });
    });
  }
  function openDossier(toolName, report) {
    ensureStyle();
    report = report || Current.report;
    var H = AgentDiff._internals && AgentDiff._internals.h ? AgentDiff._internals.h : null;
    if (!report || !report.tools_profile) return;
    var h = H || function (tag, attrs, kids) {
      var el = document.createElement(tag);
      Object.keys(attrs || {}).forEach(function (k) { if (k === "text") el.textContent = attrs[k]; else if (k === "onclick") el.onclick = attrs[k]; else if (k === "class") el.className = attrs[k]; else el.setAttribute(k, attrs[k]); });
      (kids || []).forEach(function (c) { if (c) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
      return el;
    };
    var pair = report.tools_profile;
    var row = null;
    (pair.tools || []).forEach(function (r) { if (r.name === toolName) row = r; });
    var panel = document.querySelector(".tool-dossier");
    if (!panel) {
      panel = h("aside", { class: "tool-dossier", role: "dialog", "aria-label": "tool dossier" });
      document.body.appendChild(panel);
      document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closeDossier(); });
    }
    panel.innerHTML = "";
    panel.hidden = false;
    panel.setAttribute("data-tool", toolName);
    var head = h("h4", null, [h("span", { text: toolName }), h("button", { class: "x", text: "✕", title: "close", "aria-label": "close", onclick: closeDossier })]);
    panel.appendChild(head);
    if (!row) { panel.appendChild(h("p", { class: "who", text: "Neither run called " + toolName + "." })); return; }
    var chips = h("div", { class: "chips" });
    var any = row.a || row.b;
    if (any && any.external) chips.appendChild(h("span", { text: "reaches outside" }));
    var effects = {};
    ["a", "b"].forEach(function (s) { var t = row[s]; if (t) Object.keys(t.effects || {}).forEach(function (e) { effects[e] = (effects[e] || 0) + t.effects[e]; }); });
    Object.keys(effects).forEach(function (e) { chips.appendChild(h("span", { text: e + " ×" + effects[e] })); });
    if ((row.a && row.a.decisive) || (row.b && row.b.decisive)) chips.appendChild(h("span", { text: "decisive step", style: "color:var(--bad)" }));
    panel.appendChild(chips);
    var who = h("p", { class: "who" });
    ["a", "b"].forEach(function (s) {
      var t = row[s];
      if (!t) { who.appendChild(h("span", { text: name(report, s) + ": never called · " })); return; }
      var agents = Object.keys(t.agents).map(function (a) { return a + " ×" + t.agents[a]; }).join(", ");
      who.appendChild(h("span", null, [h("b", { text: name(report, s) + ": " }), h("span", { text: t.calls + " call(s) by " + Object.keys(t.agents).length + " agent(s) — " + agents + " · " })]));
    });
    panel.appendChild(who);
    var t = h("table", null, [h("tr", null, [h("th", { text: "" }), h("th", { text: name(report, "a") }), h("th", { text: name(report, "b") })])]);
    var g = function (s, k, f) { var x = row[s]; return x ? (f ? f(x[k]) : String(x[k])) : "—"; };
    t.appendChild(dossierRow(h, "calls", g("a", "calls"), g("b", "calls")));
    t.appendChild(dossierRow(h, "distinct inputs", g("a", "distinct_inputs"), g("b", "distinct_inputs")));
    t.appendChild(dossierRow(h, "repeats", g("a", "repeats"), g("b", "repeats"), { a: row.a && row.a.repeats ? "bad" : "", b: row.b && row.b.repeats ? "bad" : "" }));
    t.appendChild(dossierRow(h, "longest identical run", g("a", "max_identical_run"), g("b", "max_identical_run")));
    t.appendChild(dossierRow(h, "errors", g("a", "errors"), g("b", "errors"), { a: row.a && row.a.errors ? "bad" : "", b: row.b && row.b.errors ? "bad" : "" }));
    t.appendChild(dossierRow(h, "wasted", g("a", "wasted_calls"), g("b", "wasted_calls")));
    t.appendChild(dossierRow(h, "seconds · mean", g("a", "seconds", function (v) { return secs(v) + " · " + secs(row.a.mean_s); }), g("b", "seconds", function (v) { return secs(v) + " · " + secs(row.b.mean_s); })));
    t.appendChild(dossierRow(h, "first · last step", g("a", "first_step", function (v) { return v + " · " + row.a.last_step; }), g("b", "first_step", function (v) { return v + " · " + row.b.last_step; })));
    t.appendChild(dossierRow(h, "fed the answer", g("a", "fed_answer"), g("b", "fed_answer")));
    t.appendChild(dossierRow(h, "on the fault's path", g("a", "fault_calls"), g("b", "fault_calls"), { a: row.a && row.a.fault_calls ? "bad" : "", b: row.b && row.b.fault_calls ? "bad" : "" }));
    panel.appendChild(t);
    var stripHost = h("div", { class: "strip" });
    panel.appendChild(stripHost);
    strip(h, stripHost, report, toolName, row);
    ["a", "b"].forEach(function (s) {
      var x = row[s];
      if (!x) return;
      var d = h("details", null, [h("summary", { text: name(report, s) + " · sample inputs and outputs" })]);
      (x.samples.inputs || []).forEach(function (i) { d.appendChild(h("pre", { text: "→ " + i })); });
      (x.samples.outputs || []).forEach(function (o) { d.appendChild(h("pre", { text: "← " + o })); });
      panel.appendChild(d);
    });
    (pair.suggestions || []).filter(function (s) { return s.tool === toolName; }).forEach(function (s) {
      panel.appendChild(h("p", { class: "sug", text: "for the next prompt: " + s.text }));
    });
  }
  function closeDossier() { var p = document.querySelector(".tool-dossier"); if (p) p.hidden = true; }
  AgentDiff.tools = { open: function (toolName, report) { openDossier(toolName, report); }, close: closeDossier };
  try {
    document.addEventListener("agentdiff:select-tool", function (ev) {
      var d = ev && ev.detail;
      if (d && d.name) openDossier(String(d.name), d.report || null);
    });
  } catch (err) { /* no document */ }

  // ------------------------------------------------------------ the block
  AgentDiff.block({
    id: "tool-behaviour",
    title: "Tool behaviour",
    question: "How did each agent use each tool — calls, repeats, errors, wasted, who touched it — and what should the next prompt say about it?",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) { var tp = ctx.report && ctx.report.tools_profile; return tp && (tp.a.measurable || tp.b.measurable) ? 0.8 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, tp = report && report.tools_profile;
      Current.report = report;
      if (!tp || !(tp.a.measurable || tp.b.measurable)) return ctx.empty(el, "Neither run called a tool.");
      var root = H("div", { class: "tb" });
      el.appendChild(root);
      root.appendChild(H("p", { class: "tb-narr", text: tp.narrative || "" }));
      var table = H("table", { class: "tb-table" });
      table.appendChild(H("tr", null, ["tool", "calls", "repeats · longest run", "errors", "wasted", "mean", "touched by"].map(function (t) { return H("th", { text: t }); })));
      var maxCalls = Math.max.apply(null, tp.tools.map(function (r) { return Math.max(r.a ? r.a.calls : 0, r.b ? r.b.calls : 0); }).concat([1]));
      tp.tools.forEach(function (r) {
        var tr = H("tr", { "data-tool": r.name });
        tr.appendChild(H("td", { class: "tool" }, [H("button", { text: r.name, title: "open the dossier for " + r.name, "data-open": r.name, onclick: function () { openDossier(r.name, report); } })]));
        var pair = H("span", { class: "pair" });
        ["a", "b"].forEach(function (s) {
          var t = r[s];
          pair.appendChild(H("span", { class: t ? "" : "dim", text: t ? String(t.calls) : "—" }));
          pair.appendChild(H("i", { class: s, style: { width: Math.round(100 * (t ? t.calls : 0) / maxCalls) + "%" } }));
        });
        tr.appendChild(H("td", null, [pair]));
        var cell = function (f, cls) {
          var td = H("td");
          ["a", "b"].forEach(function (s, i) {
            var t = r[s];
            var sp = H("span", { class: t ? (cls ? cls(t) : "") : "dim", text: t ? f(t) : "—" });
            td.appendChild(sp);
            if (!i) td.appendChild(H("br"));
          });
          return td;
        };
        tr.appendChild(cell(function (t) { return t.repeats + " · " + t.max_identical_run + "×"; }, function (t) { return t.max_identical_run >= 3 ? "bad" : t.repeats ? "warn" : ""; }));
        tr.appendChild(cell(function (t) { return String(t.errors); }, function (t) { return t.errors ? "bad" : ""; }));
        tr.appendChild(cell(function (t) { return t.wasted_calls + " (" + secs(t.wasted_s) + ")"; }, function (t) { return t.wasted_share >= 0.6 ? "warn" : ""; }));
        tr.appendChild(cell(function (t) { return secs(t.mean_s); }));
        var agents = H("td", { class: "tb-agents" });
        r.agents.forEach(function (a) { agents.appendChild(H("span", { text: a })); });
        tr.appendChild(agents);
        table.appendChild(tr);
      });
      root.appendChild(H("div", { class: "scroll-x" }, [table]));
      root.appendChild(H("p", { class: "tb-note", text: "each cell: " + name(report, "a") + " over " + name(report, "b") + " · click a tool's name for its dossier · double-click a tool step in any chart for the same" }));
      // the suggestions for the next prompt
      var sug = tp.suggestions || [];
      var head = H("div", { class: "tb-head" }, [H("span", { text: "for the next prompt" + (sug.length ? " · " + sug.length : "") })]);
      if (sug.length) head.appendChild(H("button", { class: "tb-copyall", text: "copy all", onclick: function (ev) { copy(sug.map(function (s) { return "- " + s.text; }).join("\n"), ev.currentTarget); } }));
      root.appendChild(head);
      if (!sug.length) {
        root.appendChild(H("p", { class: "tb-note", text: "No suggestion from tool behaviour: the runs used their tools alike, or neither failed." }));
      } else {
        var ul = H("ul", { class: "tb-sugg" });
        sug.forEach(function (s) {
          var li = H("li", { "data-kind": s.kind, "data-tool": s.tool });
          li.appendChild(H("span", null, [H("span", { class: "k", text: s.kind.replace(/_/g, " ") }), H("span", { text: s.text })]));
          li.appendChild(H("button", { text: "copy", onclick: function (ev) { copy(s.text, ev.currentTarget); } }));
          li.appendChild(H("span", { class: "ev", text: "for " + s.for + " · " + s.status + " · evidence: " + Object.keys(s.evidence).map(function (k) { return k.replace(/_/g, " ") + " " + JSON.stringify(s.evidence[k]); }).join(", ") }));
          ul.appendChild(li);
        });
        root.appendChild(ul);
      }
    },
  });
})(typeof window !== "undefined" ? window : this);
