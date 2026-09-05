/* AgentDiff block — Debug session.
 *
 * One run, or the A/B pair, debugged step by step: an aligned A/B strip
 * of every step (model turn, tool call, tool response, answer) with the
 * phases beneath as state bands and markers for errors, retries, model
 * switches, no-information steps, the decisive step and its replay
 * verdict; per-run aggregates on top (model turns, tool calls, errors,
 * retries, switches, phases, tokens, latency, cost); and, for the
 * selected step, the layers one by one — the model call, the tool
 * selection (against the other side's aligned tool), the tool response,
 * the state transition, the output values it produced, and the replay
 * at the decisive step — with the step's own and cumulative statistics.
 * Clicking a step moves the shared cursor (`agentdiff:select-step`), so
 * the timeline, the map and the inspector follow; their clicks move this.
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
      ".dbg{--dbg-a:var(--a);--dbg-b:var(--b);position:relative;font-size:var(--fs-s)}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .dbg{--dbg-a:#3987e5;--dbg-b:#d95926}}",
      ":root[data-theme=dark] .dbg{--dbg-a:#3987e5;--dbg-b:#d95926}",
      ".dbg-kpi{border-collapse:collapse;font-size:var(--fs-xs);margin-bottom:10px;white-space:nowrap}",
      ".dbg-kpi th.h{color:var(--ink-3);font-family:var(--mono);font-size:10.5px;font-weight:500;text-align:right;padding:0 10px 4px 0}",
      ".dbg-kpi td{padding:2px 10px 2px 0;border-top:1px solid var(--rule)}",
      ".dbg-kpi td.side{color:var(--ink);font-weight:600}.dbg-kpi td.side i{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:-1px}",
      ".dbg-kpi td.v{font-variant-numeric:tabular-nums;color:var(--ink);font-weight:600;font-size:var(--fs-m);text-align:right}",
      ".dbg-strip{overflow-x:auto;padding-bottom:4px}",
      ".dbg-strip table{border-collapse:separate;border-spacing:2px 0;font-size:10px}",
      ".dbg-strip th{text-align:left;font-family:var(--mono);color:var(--ink-3);font-weight:500;padding:0 8px 0 0;white-space:nowrap}",
      ".dbg-strip td{padding:0;vertical-align:top}",
      ".dbg-cell{width:34px;height:26px;border-radius:4px;background:var(--surface-2);position:relative;cursor:pointer;border:2px solid transparent;box-sizing:border-box}",
      ".dbg-cell.gap{background:transparent;border:1px dashed var(--rule)}",
      ".dbg-cell.reason{background:color-mix(in srgb,var(--side) 22%,var(--surface))}",
      ".dbg-cell.tool{background:color-mix(in srgb,var(--side) 55%,var(--surface))}",
      ".dbg-cell.answer{background:var(--side)}",
      ".dbg-cell.error{outline:2px solid var(--bad);outline-offset:-2px}",
      ".dbg-cell.selected{border-color:var(--ink)}",
      ".dbg-cell .m{position:absolute;right:1px;top:-1px;font-size:9px;color:var(--ink);line-height:1;font-family:var(--mono)}",
      ".dbg-cell .m.warn{color:var(--bad)}",
      ".dbg-cell .i{position:absolute;left:3px;bottom:1px;font-size:8.5px;color:var(--ink-3);font-family:var(--mono)}",
      ".dbg-cell.answer .i,.dbg-cell.tool .i{color:var(--surface)}",
      ".dbg-phase{height:6px;margin-top:2px;border-radius:3px;background:var(--rule)}",
      ".dbg-phase.frame{background:#9aa5b1}.dbg-phase.gather{background:#7fa7c9}.dbg-phase.transform{background:#c9a26b}.dbg-phase.verify{background:#8fbf8f}.dbg-phase.commit{background:#8f8fbf}.dbg-phase.recover{background:#d98f8f}",
      ".dbg-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:10.5px;color:var(--ink-3);margin:6px 0 10px}",
      ".dbg-legend b{font-family:var(--mono);font-weight:500;color:var(--ink-2)}",
      ".dbg-layers{display:grid;grid-template-columns:1fr 1fr;gap:10px}",
      "@media (max-width:760px){.dbg-layers{grid-template-columns:1fr}}",
      ".dbg-run{border:1px solid var(--rule);border-radius:8px;padding:8px 10px;background:var(--surface);min-width:0}",
      ".dbg-run h5{margin:0 0 6px;font-size:var(--fs-s);font-weight:600;color:var(--ink);display:flex;gap:8px;align-items:center}",
      ".dbg-run h5 i{width:10px;height:10px;border-radius:50%;display:inline-block}",
      ".dbg-run h5 .st{font-family:var(--mono);font-weight:500;font-size:10.5px;color:var(--ink-3);margin-left:auto}",
      ".dbg-layer{border-top:1px solid var(--rule);padding:6px 0;display:grid;grid-template-columns:7.5em 1fr;gap:8px;font-size:var(--fs-xs)}",
      ".dbg-layer .k{font-family:var(--mono);color:var(--ink-3);font-size:10.5px;padding-top:1px}",
      ".dbg-layer .k b{display:block;color:var(--ink-2);font-weight:500}",
      ".dbg-layer .b{color:var(--ink-2);min-width:0}",
      ".dbg-layer .b b{color:var(--ink);font-weight:600}",
      ".dbg-layer pre{margin:3px 0 0;white-space:pre-wrap;word-break:break-word;font-family:var(--mono);font-size:10.5px;color:var(--ink-2);background:var(--surface-2);border-radius:5px;padding:5px 7px;max-height:120px;overflow:auto}",
      ".dbg-layer .tag{display:inline-block;font-family:var(--mono);font-size:10px;padding:0 6px;border-radius:8px;background:var(--surface-2);color:var(--ink-2);margin:0 4px 2px 0}",
      ".dbg-layer .tag.bad{background:var(--bad);color:#fff}.dbg-layer .tag.good{background:var(--good);color:#fff}.dbg-layer .tag.warn{border:1px solid var(--warn)}",
      ".dbg-layer.quiet .b{color:var(--ink-3);font-style:italic}",
      ".dbg-stats{display:flex;gap:10px;flex-wrap:wrap;font-family:var(--mono);font-size:10.5px;color:var(--ink-3);margin-top:6px;font-variant-numeric:tabular-nums}",
      ".dbg-stats b{color:var(--ink);font-weight:500}",
      ".dbg-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:90ch}",
    ].join("");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function trunc(s, n) { s = String(s === null || s === undefined ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function stepsOf(report, side) { var box = report && report[side]; return box && Array.isArray(box.steps) ? box.steps : []; }
  function stepAt(report, side, index) {
    var steps = stepsOf(report, side);
    for (var i = 0; i < steps.length; i++) if (steps[i] && steps[i].index === index) return steps[i];
    return steps[index] || null;
  }
  function agentOf(report, side) { return (report && report[side] && report[side].agent) || {}; }
  var TOOLISH = ["tool_call", "search", "retrieve", "read"];
  function kindOf(step) {
    var t = String(step && step.type || "");
    if (t === "answer") return "answer";
    if (TOOLISH.indexOf(t) >= 0) return "tool";
    return "reason";
  }

  /* the per-run analysis, shared with the body chart (charts.debugInfo) */
  function analyseRun(report, side) {
    var d = AgentDiff.charts && AgentDiff.charts.debugInfo ? AgentDiff.charts.debugInfo(report, side) : { info: {}, agg: {}, phases: [], agent: agentOf(report, side), steps: stepsOf(report, side) };
    var tot = (report[side] && report[side].totals) || {};
    return { steps: d.steps, info: d.info, phases: d.phases, agent: d.agent, retries: d.agg.retries || 0, switches: d.agg.switches || 0, errors: d.agg.errors || 0,
             toolCalls: d.agg.toolCalls || 0, turns: d.agg.turns || 0, transitions: d.agg.transitions || 0, noInfo: d.agg.noInfo || 0,
             tokens: (tot.input_tokens || 0) + (tot.output_tokens || 0), latency: tot.latency_s, cost: tot.cost_usd,
             outcome: (report[side] && report[side].outcome) || {} };
  }

  function decisive(report) {
    var diag = report.diagnosis || {}, dec = diag.decisive_step || {};
    return { side: diag.subject === "a" || diag.subject === "b" ? diag.subject : null, step: isNum(dec.step) ? dec.step : null,
             verification: dec.verification || null, replay: dec.replay || null, recipe: dec.replay_recipe || null };
  }

  function alignedRow(report, side, step) {
    var rows = Array.isArray(report.alignment) ? report.alignment : [];
    for (var i = 0; i < rows.length; i++) if (rows[i][side + "_index"] === step) return { row: rows[i], ri: i };
    return null;
  }

  /* The six layers of one step, beside the aligned step of the other run.
   * Exported as AgentDiff.debugSession.layers so the body block's docked
   * inspector can show the same. */
  function renderLayers(H, report, side, step, runs) {
    runs = runs || { a: analyseRun(report, "a"), b: analyseRun(report, "b") };
    var dec = decisive(report);
    function layer(host, key, sub, body, quiet) {
      var l = H("div", { class: "dbg-layer" + (quiet ? " quiet" : ""), "data-layer": key });
      l.appendChild(H("span", { class: "k" }, [H("b", { text: key }), H("span", { text: sub || "" })]));
      var b = H("div", { class: "b" });
      (Array.isArray(body) ? body : [body]).forEach(function (x) { if (x) b.appendChild(typeof x === "string" ? H("span", { text: x }) : x); });
      l.appendChild(b);
      host.appendChild(l);
    }
    function renderRun(sd, st, counterpart) {
      var r = runs[sd], e = r.info[st.index] || {}, m = e.model;
      var card = H("div", { class: "dbg-run", "data-side": sd, "data-step": st.index });
      card.appendChild(H("h5", null, [H("i", { style: { background: "var(--dbg-" + sd + ")" } }), H("span", { text: (r.agent.name || sd) + " · step " + st.index + " · " + (st.type || "") + (st.name ? " " + st.name : "") }),
        H("span", { class: "st", text: e.phase ? "state: " + e.phase : "" })]));
      var conf = st.model && isNum(st.model.confidence) ? st.model.confidence : null;
      layer(card, "model call", e.kind === "tool" ? "the turn that chose this call" : "the turn", [
        H("span", null, [H("b", { text: m || r.agent.model || "model not recorded" }), H("span", { text: (isNum(st.tokens) ? " · " + st.tokens + " tokens" + (st.tokens_basis ? " (" + st.tokens_basis + ")" : "") : "") + (isNum(st.latency_s) ? " · " + st.latency_s.toFixed(2) + "s" : "") + (conf !== null ? " · confidence " + conf.toFixed(2) : "") })]),
        e.modelSwitch ? H("div", null, [H("span", { class: "tag warn", text: "model switch" }), H("span", { text: e.modelSwitch.from + " → " + e.modelSwitch.to })]) : null,
        e.kind === "reason" ? H("pre", { text: trunc(st.input || st.output, 600) }) : null,
      ]);
      if (e.kind === "tool") {
        var same = counterpart && kindOf(counterpart) === "tool" ? (counterpart.name === st.name) : null;
        var tdiff = ((alignedRow(report, sd, st.index) || {}).row || {}).tool_diff;
        layer(card, "tool selection", "which tool, with what", [
          H("span", null, [H("b", { text: st.name || "?" }), H("span", { text: same === null ? (counterpart ? " · the other side did not call a tool here" : " · no aligned step on the other side") : same ? " · same tool as the other side" : " · the other side used " + counterpart.name })]),
          e.retry ? H("div", null, [H("span", { class: "tag warn", text: e.retry.same ? "retry, identical arguments" : "retry, changed arguments" }), H("span", { text: "of step " + e.retry.of })]) : null,
          tdiff && tdiff.changed && tdiff.changed.length ? H("div", null, [H("span", { class: "tag", text: "argument diff" }), H("span", { text: tdiff.changed.map(function (c) { return c.key + ": " + trunc(c.a, 40) + " ↔ " + trunc(c.b, 40); }).join("; ") })]) : null,
          H("pre", { text: trunc(st.input, 500) }),
        ]);
        layer(card, "tool response", "what came back", [
          e.error ? H("span", { class: "tag bad", text: "error" }) : null,
          e.noInfo ? H("span", { class: "tag warn", text: "no new information" }) : null,
          H("pre", { text: trunc(st.output || st.error || "(empty)", 600) }),
        ]);
      } else {
        layer(card, "tool selection", "", "no tool at this step", true);
        layer(card, "tool response", "", "—", true);
      }
      layer(card, "state", "phase from the reading", e.transition ? [H("span", { class: "tag", text: e.transition.from + " → " + e.transition.to }), H("span", { text: "a transition at this step" })]
        : e.phase ? "stays in " + e.phase : "no phase assigned", !e.transition);
      var out = [];
      if (e.kind === "answer") out.push(H("div", null, [H("span", { class: "tag " + (r.outcome.success ? "good" : "bad"), text: r.outcome.success ? "final answer · solved" : "final answer · failed" }), H("pre", { text: trunc(st.output || st.input, 600) })]));
      (e.values || []).forEach(function (v) { out.push(H("div", null, [H("span", { class: "tag " + (v.status === "wrong" || v.status === "unsupported" ? "bad" : v.status === "supported" || v.status === "basis" ? "good" : ""), text: String(v.status || "value") }), H("span", { text: String(v.value) })])); });
      layer(card, "output", "what this step gave the answer", out.length ? out : "nothing the answer rests on", !out.length);
      if (dec.side === sd && dec.step === st.index) {
        var rp = dec.replay;
        layer(card, "replay", "the decisive step", rp ? [H("span", { class: "tag " + (String(dec.verification).indexOf("verified") >= 0 ? "good" : String(dec.verification).indexOf("refuted") >= 0 ? "bad" : "warn"), text: String(dec.verification) }),
          H("span", { text: (rp.flipped !== undefined ? rp.flipped + " of " + rp.replays + " replay(s) flipped the outcome" : "") })]
          : [H("span", { class: "tag warn", text: dec.verification || "hypothesized" }), H("span", { text: dec.recipe ? "not replayed yet — `deepcompare replay` from step " + (dec.recipe.step !== undefined ? dec.recipe.step : st.index) + " would test it" : "not replayed yet" })]);
      }
      var cum = { tokens: 0, latency: 0, calls: 0, errors: 0 };
      r.steps.forEach(function (s2) { if (s2.index <= st.index) { cum.tokens += isNum(s2.tokens) ? s2.tokens : 0; cum.latency += isNum(s2.latency_s) ? s2.latency_s : 0; if (kindOf(s2) === "tool") cum.calls++; if ((r.info[s2.index] || {}).error) cum.errors++; } });
      card.appendChild(H("div", { class: "dbg-stats" }, [
        H("span", null, [H("span", { text: "this step " }), H("b", { text: (isNum(st.tokens) ? st.tokens : 0) + " tok" }), H("span", { text: " · " }), H("b", { text: (isNum(st.latency_s) ? st.latency_s.toFixed(2) : "0.00") + "s" })]),
        H("span", null, [H("span", { text: "so far " }), H("b", { text: cum.tokens + " tok" }), H("span", { text: " · " }), H("b", { text: cum.latency.toFixed(2) + "s" }), H("span", { text: " · " }), H("b", { text: cum.calls + " call(s)" }), H("span", { text: " · " }), H("b", { text: cum.errors + " error(s)" })]),
        H("span", null, [H("span", { text: "run total " }), H("b", { text: r.tokens + " tok" }), H("span", { text: " · " }), H("b", { text: (isNum(r.latency) ? r.latency.toFixed(2) : "?") + "s" })]),
      ]));
      return card;
    }
    var wrap = H("div", { class: "dbg-layers" });
    var st = stepAt(report, side, step);
    if (!st) return wrap;
    var ar = alignedRow(report, side, step);
    var other = side === "a" ? "b" : "a";
    var otherIdx = ar && ar.row ? ar.row[other + "_index"] : null;
    var otherStep = (otherIdx !== null && otherIdx !== undefined) ? stepAt(report, other, otherIdx) : null;
    wrap.appendChild(renderRun(side, st, otherStep));
    if (otherStep) wrap.appendChild(renderRun(other, otherStep, st));
    else if (stepsOf(report, other).length) wrap.appendChild(H("div", { class: "dbg-run", "data-side": other, "data-step": "" }, [H("h5", { text: (agentOf(report, other).name || other) + " · no aligned step" }), H("p", { class: "dbg-note", text: "The other run has no step aligned with this one — a one-sided row of the alignment." })]));
    return wrap;
  }
  AgentDiff.debugSession = { analyse: analyseRun, layers: renderLayers, ensureStyle: ensureStyle };

  // -------------------------------------------------------------- render
  AgentDiff.block({
    id: "debug-session",
    title: "Debug session",
    question: "Step by step on the timeline: model turns, tool choices and responses, retries, model switches, state changes — and, layer by layer, what the selected step did.",
    group: "trajectory",
    size: "wide",

    relevance: function (ctx) {
      var r = ctx.report;
      if (!r || !(stepsOf(r, "a").length || stepsOf(r, "b").length)) return 0;
      return 0.72;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report;
      var sides = ["a", "b"].filter(function (s) { return stepsOf(report, s).length; });
      if (!sides.length) return ctx.empty(el, "No steps to debug in this report.");
      var runs = {};
      sides.forEach(function (s) { runs[s] = analyseRun(report, s); });
      var dec = decisive(report);
      var rows = Array.isArray(report.alignment) && report.alignment.length ? report.alignment
        : stepsOf(report, sides[0]).map(function (s, i) { var o = {}; o[sides[0] + "_index"] = s.index; return o; });
      var root = H("div", { class: "dbg" });
      el.appendChild(root);

      // aggregates per run
      var kpi = H("table", { class: "dbg-kpi" });
      var cols = [["model turns", function (r) { return r.turns; }], ["tool calls", function (r) { return r.toolCalls; }], ["tool errors", function (r) { return r.errors; }],
                  ["retries", function (r) { return r.retries; }], ["model switches", function (r) { return r.switches; }], ["no-info steps", function (r) { return r.noInfo; }],
                  ["phases · transitions", function (r) { return r.phases.length + " · " + r.transitions; }],
                  ["tokens", function (r) { return r.tokens; }], ["latency", function (r) { return isNum(r.latency) ? r.latency.toFixed(1) + "s" : "—"; }],
                  ["cost", function (r) { return isNum(r.cost) ? "$" + r.cost.toFixed(4) : "—"; }]];
      var head = H("tr", null, [H("th")]);
      cols.forEach(function (c) { head.appendChild(H("th", { class: "h", text: c[0] })); });
      kpi.appendChild(head);
      sides.forEach(function (s) {
        var r = runs[s];
        var tr = H("tr", { "data-side": s });
        tr.appendChild(H("td", { class: "side" }, [H("i", { style: { background: "var(--dbg-" + s + ")" } }), H("span", { text: (r.agent.name || s) + (r.outcome.success ? " ✓" : " ✗") })]));
        cols.forEach(function (c) { tr.appendChild(H("td", { class: "v", "data-side": s, "data-kpi": c[0], text: String(c[1](r)) })); });
        kpi.appendChild(tr);
      });
      root.appendChild(H("div", { class: "scroll-x" }, [kpi]));

      // the body chart in debug mode: the two runs over time, tools as branches,
      // the phases as state bands, retries / switches / no-info marked, the replay at the decisive step
      var selected = { side: dec.side || sides[0], step: dec.step !== null ? dec.step : stepsOf(report, dec.side || sides[0])[0].index };
      var chartHost = H("div", { class: "dbg-chart" });
      var drawn = null;
      try { drawn = AgentDiff.charts && AgentDiff.charts.body ? AgentDiff.charts.body(chartHost, ctx, { debug: true }) : null; }
      catch (err) { drawn = null; console.warn("AgentDiff debug-session: chart failed", err); }
      if (drawn) root.appendChild(drawn); else root.appendChild(H("p", { class: "dbg-note", text: "The body chart needs D3; the layers below still read the selected step." }));
      var layersHost = H("div");
      root.appendChild(layersHost);
      root.appendChild(H("p", { class: "dbg-note", text: "Every layer quotes the trace as recorded: the model turn's own tokens and latency, the tool call's input and its response, the phase the reading assigned, the values the answer rests on that this step produced, and the replay verdict at the decisive step when a replay ran. A retry is a call to the same tool after that tool returned an error; a model switch is a step whose recorded model differs from the one before it. Click a node on the chart to open its layers; the timeline, map and inspector follow the same cursor." }));
      var rows = Array.isArray(report.alignment) ? report.alignment : [];
      function renderLayersNow() {
        layersHost.innerHTML = "";
        layersHost.appendChild(renderLayers(H, report, selected.side, selected.step, runs));
      }
      renderLayersNow();

      // follow the shared cursor
      try {
        document.addEventListener("agentdiff:select-step", function (event) {
          var d = event && event.detail;
          if (!d || typeof d.row !== "number" || !root.isConnected) return;
          var row = rows[d.row];
          if (!row) return;
          var side = d.side === "a" || d.side === "b" ? d.side : null;
          if (!side || row[side + "_index"] === null || row[side + "_index"] === undefined) side = sides.filter(function (s) { return row[s + "_index"] !== null && row[s + "_index"] !== undefined; })[0];
          if (!side) return;
          selected = { side: side, step: row[side + "_index"] };
          renderLayersNow();
        });
      } catch (err) { /* no document */ }
    },
  });
})(typeof window !== "undefined" ? window : this);
