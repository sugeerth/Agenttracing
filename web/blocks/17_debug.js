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
  function modelName(step) {
    var m = step && step.model;
    if (!m) return null;
    if (typeof m === "string") return m;
    return m.name || m.model || null;
  }
  //: the environment-facing step types (SCHEMA STEP_TYPES): a call the
  //: run made and a response it got back; plan and reason are model turns
  var TOOLISH = ["tool_call", "search", "retrieve", "read"];
  function kindOf(step) {
    var t = String(step && step.type || "");
    if (t === "answer") return "answer";
    if (TOOLISH.indexOf(t) >= 0) return "tool";
    return "reason";
  }
  function isErr(step, side, report) {
    if (!step) return false;
    if (step.error) return true;
    var rec = report && report.process && report.process[side] && report.process[side].recovery;
    return !!(rec && Array.isArray(rec.error_steps) && rec.error_steps.indexOf(step.index) >= 0);
  }
  function normInput(s) { return String(s || "").replace(/\s+/g, " ").trim().toLowerCase(); }

  /* the per-run analysis the strip and the layers read */
  function analyseRun(report, side) {
    var steps = stepsOf(report, side);
    var agent = agentOf(report, side);
    var reading = (report.reading || {})[side] || {};
    var phases = reading.phases || [];
    var phaseOf = {};
    phases.forEach(function (p) { (p.steps || []).forEach(function (i) { phaseOf[i] = p.intent; }); });
    var proc = (report.process || {})[side] || {};
    var noInfo = ((proc.repeats || {}).no_information_detail || []).map(function (d) { return typeof d === "number" ? d : d && (d.index !== undefined ? d.index : d.step); });
    var baseModel = agent.model || null;
    var lastModel = baseModel, lastTool = {};
    var info = {}, retries = 0, switches = 0, errors = 0, toolCalls = 0, turns = 0, transitions = 0, prevPhase = null;
    var basisSteps = ((reading.answer_basis || {}).basis_steps) || [];
    var restsOn = reading.rests_on || [];
    steps.forEach(function (s, k) {
      var kind = kindOf(s);
      var e = { kind: kind, error: isErr(s, side, report), retry: null, modelSwitch: null, noInfo: noInfo.indexOf(s.index) >= 0,
                phase: phaseOf[s.index] || null, transition: null, values: [] };
      if (kind === "tool") toolCalls++; else turns++;
      if (e.error) errors++;
      var m = modelName(s);
      if (m && lastModel && m !== lastModel) { e.modelSwitch = { from: lastModel, to: m }; switches++; }
      if (m) lastModel = m;
      if (kind === "tool") {
        var prev = lastTool[s.name];
        if (prev && prev.error) {
          e.retry = { of: prev.index, same: normInput(prev.input) === normInput(s.input) };
          retries++;
        }
        lastTool[s.name] = { index: s.index, input: s.input, error: e.error };
      }
      if (e.phase && prevPhase && e.phase !== prevPhase) { e.transition = { from: prevPhase, to: e.phase }; transitions++; }
      if (e.phase) prevPhase = e.phase;
      restsOn.forEach(function (r) {
        var at = r && (r.first_step !== undefined ? r.first_step : r.step !== undefined ? r.step : r.source_step);
        if (at === s.index && r.value !== undefined) {
          e.values.push({ value: String(r.value) + (r.kind ? " (" + r.kind + ")" : "") + (r.matches_expected === true ? " · matches expected" : r.matches_expected === false ? " · not the expected value" : ""),
                          status: r.status || r.support || "" });
        }
      });
      if (basisSteps.indexOf(s.index) >= 0 && !e.values.length) e.values.push({ value: "(a value the answer rests on)", status: "basis" });
      info[s.index] = e;
    });
    var tot = (report[side] && report[side].totals) || {};
    return { steps: steps, info: info, phases: phases, agent: agent, retries: retries, switches: switches, errors: errors,
             toolCalls: toolCalls, turns: turns, transitions: transitions, noInfo: noInfo.length,
             tokens: (tot.input_tokens || 0) + (tot.output_tokens || 0), latency: tot.latency_s, cost: tot.cost_usd,
             outcome: (report[side] && report[side].outcome) || {} };
  }

  function decisive(report) {
    var diag = report.diagnosis || {}, dec = diag.decisive_step || {};
    return { side: diag.subject === "a" || diag.subject === "b" ? diag.subject : null, step: isNum(dec.step) ? dec.step : null,
             verification: dec.verification || null, replay: dec.replay || null, recipe: dec.replay_recipe || null };
  }

  // -------------------------------------------------------------- render
  AgentDiff.block({
    id: "debug-session",
    title: "Debug session",
    question: "Step by step, what did each run do — model turns, tool choices and responses, retries, model switches, state changes — and what did the selected step produce?",
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

      // the aligned A/B strip
      var selected = { side: dec.side || sides[0], step: dec.step !== null ? dec.step : stepsOf(report, dec.side || sides[0])[0].index };
      var strip = H("div", { class: "dbg-strip" });
      var table = H("table");
      var cellIndex = {};
      sides.forEach(function (s) {
        var r = runs[s];
        var tr = H("tr", { "data-side": s });
        tr.appendChild(H("th", { text: r.agent.name || s }));
        rows.forEach(function (row, ri) {
          var idx = row[s + "_index"];
          var td = H("td");
          if (idx === null || idx === undefined) { td.appendChild(H("div", { class: "dbg-cell gap" })); tr.appendChild(td); return; }
          var step = stepAt(report, s, idx), e = r.info[idx] || {};
          var cell = H("div", { class: "dbg-cell " + e.kind + (e.error ? " error" : ""), "data-side": s, "data-step": idx, "data-row": ri,
                               style: { "--side": "var(--dbg-" + s + ")" },
                               title: "step " + idx + " · " + (step.type || "") + (step.name ? " " + step.name : "") + (e.phase ? " · " + e.phase : "") });
          cell.appendChild(H("span", { class: "i", text: String(idx) }));
          var marks = [];
          if (e.error) marks.push("✕");
          if (e.retry) marks.push("↻");
          if (e.modelSwitch) marks.push("⇄");
          if (e.noInfo) marks.push("∅");
          if (dec.side === s && dec.step === idx) marks.push(dec.verification === "replay-verified" ? "◉" : "◎");
          if (marks.length) cell.appendChild(H("span", { class: "m" + (e.error ? " warn" : ""), text: marks.join("") }));
          cell.addEventListener("click", function () {
            selected = { side: s, step: idx };
            paintSelection(); renderLayers();
            if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, s, idx);
          });
          td.appendChild(cell);
          td.appendChild(H("div", { class: "dbg-phase " + (e.phase || ""), title: e.phase ? "phase: " + e.phase : "" }));
          cellIndex[s + ":" + idx] = cell;
          tr.appendChild(td);
        });
        table.appendChild(tr);
      });
      strip.appendChild(table);
      root.appendChild(strip);
      root.appendChild(H("div", { class: "dbg-legend" }, [
        H("span", null, [H("b", { text: "cells" }), H("span", { text: " light = model turn, mid = tool call, solid = answer; red outline = error" })]),
        H("span", null, [H("b", { text: "marks" }), H("span", { text: " ✕ error · ↻ retry · ⇄ model switch · ∅ no information · ◎ decisive (hypothesized) · ◉ decisive (replay-verified)" })]),
        H("span", null, [H("b", { text: "bands" }), H("span", { text: " the phase (state) each step belongs to; a colour change is a transition" })]),
      ]));

      var layers = H("div", { class: "dbg-layers" });
      root.appendChild(layers);
      root.appendChild(H("p", { class: "dbg-note", text: "Every layer quotes the trace as recorded: the model turn's own tokens and latency, the tool call's input and its response, the phase the reading assigned, the values the answer rests on that this step produced, and the replay verdict at the decisive step when a replay ran. A retry is a call to the same tool after that tool returned an error; a model switch is a step whose recorded model differs from the one before it." }));

      function paintSelection() {
        Object.keys(cellIndex).forEach(function (k) { cellIndex[k].classList.remove("selected"); });
        var c = cellIndex[selected.side + ":" + selected.step];
        if (c) c.classList.add("selected");
      }
      function alignedRow(side, step) {
        for (var i = 0; i < rows.length; i++) if (rows[i][side + "_index"] === step) return { row: rows[i], ri: i };
        return null;
      }
      function layer(host, key, sub, body, quiet) {
        var l = H("div", { class: "dbg-layer" + (quiet ? " quiet" : ""), "data-layer": key });
        l.appendChild(H("span", { class: "k" }, [H("b", { text: key }), H("span", { text: sub || "" })]));
        var b = H("div", { class: "b" });
        (Array.isArray(body) ? body : [body]).forEach(function (x) { if (x) b.appendChild(typeof x === "string" ? H("span", { text: x }) : x); });
        l.appendChild(b);
        host.appendChild(l);
      }
      function renderRun(side, step, counterpart, counterSide) {
        var r = runs[side], e = r.info[step.index] || {}, m = modelName(step);
        var card = H("div", { class: "dbg-run", "data-side": side, "data-step": step.index });
        card.appendChild(H("h5", null, [H("i", { style: { background: "var(--dbg-" + side + ")" } }), H("span", { text: (r.agent.name || side) + " · step " + step.index + " · " + (step.type || "") + (step.name ? " " + step.name : "") }),
          H("span", { class: "st", text: e.phase ? "state: " + e.phase : "" })]));
        // 1 · model call
        var conf = step.model && isNum(step.model.confidence) ? step.model.confidence : null;
        layer(card, "model call", e.kind === "tool" ? "the turn that chose this call" : "the turn", [
          H("span", null, [H("b", { text: m || r.agent.model || "model not recorded" }), H("span", { text: (isNum(step.tokens) ? " · " + step.tokens + " tokens" + (step.tokens_basis ? " (" + step.tokens_basis + ")" : "") : "") + (isNum(step.latency_s) ? " · " + step.latency_s.toFixed(2) + "s" : "") + (conf !== null ? " · confidence " + conf.toFixed(2) : "") })]),
          e.modelSwitch ? H("div", null, [H("span", { class: "tag warn", text: "model switch" }), H("span", { text: e.modelSwitch.from + " → " + e.modelSwitch.to })]) : null,
          e.kind === "reason" ? H("pre", { text: trunc(step.input || step.output, 600) }) : null,
        ]);
        // 2 · tool selection
        if (e.kind === "tool") {
          var same = counterpart && kindOf(counterpart) === "tool" ? (counterpart.name === step.name) : null;
          var tdiff = counterpart && counterSide ? ((alignedRow(side, step.index) || {}).row || {}).tool_diff : null;
          layer(card, "tool selection", "which tool, with what", [
            H("span", null, [H("b", { text: step.name || "?" }), H("span", { text: same === null ? (counterpart ? " · the other side did not call a tool here" : " · no aligned step on the other side") : same ? " · same tool as the other side" : " · the other side used " + counterpart.name })]),
            e.retry ? H("div", null, [H("span", { class: "tag warn", text: e.retry.same ? "retry, identical arguments" : "retry, changed arguments" }), H("span", { text: "of step " + e.retry.of })]) : null,
            tdiff && tdiff.changed && tdiff.changed.length ? H("div", null, [H("span", { class: "tag", text: "argument diff" }), H("span", { text: tdiff.changed.map(function (c) { return c.key + ": " + trunc(c.a, 40) + " ↔ " + trunc(c.b, 40); }).join("; ") })]) : null,
            H("pre", { text: trunc(step.input, 500) }),
          ]);
          // 3 · tool response
          layer(card, "tool response", "what came back", [
            e.error ? H("span", { class: "tag bad", text: "error" }) : null,
            e.noInfo ? H("span", { class: "tag warn", text: "no new information" }) : null,
            H("pre", { text: trunc(step.output || step.error || "(empty)", 600) }),
          ]);
        } else {
          layer(card, "tool selection", "", "no tool at this step", true);
          layer(card, "tool response", "", "—", true);
        }
        // 4 · state transition
        layer(card, "state", "phase from the reading", e.transition ? [H("span", { class: "tag", text: e.transition.from + " → " + e.transition.to }), H("span", { text: "a transition at this step" })]
          : e.phase ? "stays in " + e.phase : "no phase assigned", !e.transition);
        // 5 · output
        var out = [];
        if (e.kind === "answer") {
          out.push(H("div", null, [H("span", { class: "tag " + (r.outcome.success ? "good" : "bad"), text: r.outcome.success ? "final answer · solved" : "final answer · failed" }), H("pre", { text: trunc(step.output || step.input, 600) })]));
        }
        e.values.forEach(function (v) { out.push(H("div", null, [H("span", { class: "tag " + (v.status === "wrong" || v.status === "unsupported" ? "bad" : v.status === "supported" || v.status === "basis" ? "good" : ""), text: String(v.status || "value") }), H("span", { text: String(v.value) })])); });
        layer(card, "output", "what this step gave the answer", out.length ? out : "nothing the answer rests on", !out.length);
        // 6 · replay
        if (dec.side === side && dec.step === step.index) {
          var rp = dec.replay;
          layer(card, "replay", "the decisive step", rp ? [H("span", { class: "tag " + (String(dec.verification).indexOf("verified") >= 0 ? "good" : String(dec.verification).indexOf("refuted") >= 0 ? "bad" : "warn"), text: String(dec.verification) }),
            H("span", { text: (rp.flipped !== undefined ? rp.flipped + " of " + rp.replays + " replay(s) flipped the outcome" : "") + (rp.provider ? " · " + (rp.provider.name || "") : "") })]
            : [H("span", { class: "tag warn", text: dec.verification || "hypothesized" }), H("span", { text: dec.recipe ? "not replayed yet — `deepcompare replay` from step " + (dec.recipe.step !== undefined ? dec.recipe.step : step.index) + " would test it" : "not replayed yet" })]);
        }
        // stats: own and cumulative
        var cum = { tokens: 0, latency: 0, calls: 0, errors: 0 };
        r.steps.forEach(function (s) { if (s.index <= step.index) { cum.tokens += isNum(s.tokens) ? s.tokens : 0; cum.latency += isNum(s.latency_s) ? s.latency_s : 0; if (kindOf(s) === "tool") cum.calls++; if ((r.info[s.index] || {}).error) cum.errors++; } });
        card.appendChild(H("div", { class: "dbg-stats" }, [
          H("span", null, [H("span", { text: "this step " }), H("b", { text: (isNum(step.tokens) ? step.tokens : 0) + " tok" }), H("span", { text: " · " }), H("b", { text: (isNum(step.latency_s) ? step.latency_s.toFixed(2) : "0.00") + "s" })]),
          H("span", null, [H("span", { text: "so far " }), H("b", { text: cum.tokens + " tok" }), H("span", { text: " · " }), H("b", { text: cum.latency.toFixed(2) + "s" }), H("span", { text: " · " }), H("b", { text: cum.calls + " call(s)" }), H("span", { text: " · " }), H("b", { text: cum.errors + " error(s)" })]),
          H("span", null, [H("span", { text: "run total " }), H("b", { text: r.tokens + " tok" }), H("span", { text: " · " }), H("b", { text: (isNum(r.latency) ? r.latency.toFixed(2) : "?") + "s" })]),
        ]));
        return card;
      }
      function renderLayers() {
        layers.innerHTML = "";
        var step = stepAt(report, selected.side, selected.step);
        if (!step) return;
        var ar = alignedRow(selected.side, selected.step);
        var other = selected.side === "a" ? "b" : "a";
        var otherIdx = ar && ar.row ? ar.row[other + "_index"] : null;
        var otherStep = (otherIdx !== null && otherIdx !== undefined) ? stepAt(report, other, otherIdx) : null;
        layers.appendChild(renderRun(selected.side, step, otherStep, other));
        if (otherStep && sides.indexOf(other) >= 0) layers.appendChild(renderRun(other, otherStep, step, selected.side));
        else if (sides.indexOf(other) >= 0) layers.appendChild(H("div", { class: "dbg-run", "data-side": other, "data-step": "" }, [H("h5", { text: (runs[other].agent.name || other) + " · no aligned step" }), H("p", { class: "dbg-note", text: "The other run has no step aligned with this one — a one-sided row of the alignment." })]));
      }
      paintSelection(); renderLayers();

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
          paintSelection(); renderLayers();
        });
      } catch (err) { /* no document */ }
    },
  });
})(typeof window !== "undefined" ? window : this);
