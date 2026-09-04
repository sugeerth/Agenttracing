/* AgentDiff — the story charts (D3).
 *
 * Three charts that carry the story view, drawn with the vendored D3 on one
 * shared idea: a run is a line of steps, and everything worth understanding
 * about it — what each step was for, where the answer's values came from,
 * where it went wrong, where to intervene — sits on that line.
 *
 *   charts.story(host, ctx, side)    what happened: phases, step roles,
 *                                    where each answer value entered, the
 *                                    decisive step, the spend after the
 *                                    basis was complete
 *   charts.why(host, ctx)            why: hypotheses as scored bars with
 *                                    their evidence, the margin, and the
 *                                    decisive window on the step line
 *   charts.forward(host, ctx, side)  take forward: numbered pins on the
 *                                    step line, one per located next action,
 *                                    and what the counterfactual buys
 *
 * Every number drawn here is read from the report as written: the engine's
 * scores, statuses, steps and estimates, never a chart-side derivation. The
 * charts share the trajectory family's cursor: clicking a step dispatches
 * `agentdiff:select-step`, so the map's inspector opens the same step.
 * Motion honours prefers-reduced-motion (duration 0).
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  var d3 = global.d3;
  if (!AgentDiff) return;

  var charts = {};
  AgentDiff.charts = charts;

  charts.available = function () { return !!(d3 && typeof d3.select === "function"); };
  charts.motion = function () {
    try {
      return global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 380;
    } catch (err) { return 0; }
  };

  // ------------------------------------------------------------- style

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".d3c-wrap{position:relative;margin:4px 0 10px}",
      ".d3c{display:block;max-width:100%;overflow:visible;font-family:var(--sans)}",
      ".d3c text{font-family:var(--sans);fill:var(--ink-2)}",
      ".d3c .mono{font-family:var(--mono)}",
      ".d3c .d3c-step{cursor:pointer;outline:none}",
      ".d3c .d3c-step:focus-visible .d3c-mark{stroke:var(--accent);stroke-width:2.4px}",
      ".d3c .d3c-step:hover .d3c-mark{filter:brightness(.92)}",
      ".d3c .d3c-name{fill:var(--ink-3);font-size:10.5px}",
      ".d3c .d3c-idx{fill:var(--ink-3);font-size:10px;font-family:var(--mono)}",
      ".d3c .d3c-phase-label{font-size:10px;letter-spacing:.04em;text-transform:uppercase}",
      ".d3c .d3c-cap{font-size:10.5px;fill:var(--ink-3)}",
      ".d3c .d3c-arc{fill:none;stroke-width:1.6px;stroke-linecap:round}",
      ".d3c .d3c-arc.contradicted,.d3c .d3c-arc.stale{stroke-dasharray:4 3}",
      ".d3c .d3c-arc-label{font-size:10px;font-family:var(--mono)}",
      ".d3c .d3c-ring{fill:none;stroke:var(--bad);stroke-width:2px}",
      ".d3c .d3c-ring.hypothesized{stroke-dasharray:6 3}",
      ".d3c .d3c-bar{cursor:default}",
      ".d3c .d3c-hyp{font-size:var(--fs-s);fill:var(--ink)}",
      ".d3c .d3c-hyp.ruled_out{fill:var(--ink-3);text-decoration:line-through}",
      ".d3c .d3c-ev{cursor:help}",
      ".d3c .d3c-pin{cursor:pointer;outline:none}",
      ".d3c .d3c-pin:focus-visible circle{stroke:var(--accent);stroke-width:2.4px}",
      ".d3c .d3c-pin text{fill:#fff;font-size:10.5px;font-weight:700;font-family:var(--mono);pointer-events:none}",
      ".d3c-legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:var(--fs-xs);color:var(--ink-3);margin-top:2px;align-items:center}",
      ".d3c-legend svg{vertical-align:-2px;margin-right:4px}",
      ".d3c-tip{position:fixed;z-index:60;max-width:320px;padding:7px 9px;border-radius:6px;",
      "background:var(--ink);color:var(--bg);font-size:var(--fs-xs);line-height:1.4;pointer-events:none;",
      "box-shadow:var(--shadow);opacity:0;transition:opacity .12s}",
      ".d3c-tip.on{opacity:1}",
      ".d3c-tip b{display:block;font-weight:600;margin-bottom:2px}",
      ".d3c-tip .mono{font-family:var(--mono);opacity:.85}",
      ".d3c-list{list-style:none;margin:6px 0 0;padding:0}",
      ".d3c-list li{display:grid;grid-template-columns:22px 1fr;gap:8px;padding:6px 0;border-top:1px solid var(--rule);font-size:var(--fs-m);align-items:start}",
      ".d3c-list li:first-child{border-top:0}",
      ".d3c-list li.on{background:var(--surface-2)}",
      ".d3c-list .n{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;",
      "background:var(--accent);color:#fff;font-family:var(--mono);font-size:var(--fs-xs);font-weight:700;margin-top:1px}",
      ".d3c-list li.decisive .n{background:var(--bad)}",
      ".d3c-list .what{color:var(--ink-3);font-size:var(--fs-s);margin-top:2px}",
      ".d3c-list .meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".d3c-list .meta .tag{font-family:var(--mono)}",
      ".d3c-list .step{font-family:var(--mono);color:var(--ink-3);font-size:var(--fs-xs);cursor:pointer;text-decoration:underline dotted}",
      ".d3c-buys{display:grid;grid-template-columns:auto 1fr auto;gap:4px 10px;align-items:center;font-size:var(--fs-s);margin-top:10px}",
      ".d3c-buys .k{color:var(--ink-3);font-size:var(--fs-xs)}",
      ".d3c-buys .v{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2);text-align:right}",
      ".d3c-buys .bars{height:14px;position:relative}",
      ".d3c-buys .bars i{position:absolute;left:0;height:5px;border-radius:2px}",
      ".d3c-buys .bars i.actual{top:1px;background:var(--ink-3)}",
      ".d3c-buys .bars i.estimate{top:8px;background:var(--accent);opacity:.7}",
      ".d3c-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:6px}",
      "body[data-view=\"story\"] .d3c-list li.on{background:transparent;box-shadow:inset 3px 0 0 var(--accent)}",
      "@media (max-width:480px){.d3c-list li{grid-template-columns:20px 1fr}}",
    ].join("");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------ helpers

  function cssVar(name, fallback) {
    try {
      var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return value || fallback;
    } catch (err) { return fallback; }
  }
  function palette() {
    return {
      a: cssVar("--a", "#2f6f9f"), b: cssVar("--b", "#b5651d"),
      good: cssVar("--good", "#3f7d3f"), bad: cssVar("--bad", "#b03030"),
      warn: cssVar("--warn", "#b5891d"), ink: cssVar("--ink", "#1a1a18"),
      ink2: cssVar("--ink-2", "#4a4a46"), muted: cssVar("--ink-3", "#6b6a63"),
      rule: cssVar("--rule", "#e2e2dd"), rule2: cssVar("--rule-2", "#cfcfc8"),
      accent: cssVar("--accent", "#2f6f9f"), surface: cssVar("--surface", "#fff"),
      surface2: cssVar("--surface-2", "#f4f4f2"),
    };
  }
  function sideColor(side) { var P = palette(); return side === "a" ? P.a : P.b; }
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function readingOf(report, side) {
    var r = report && report.reading && report.reading[side];
    return r && typeof r === "object" ? r : null;
  }
  function stepsOf(report, side) {
    var box = report && report[side];
    return box && Array.isArray(box.steps) ? box.steps : [];
  }
  function agentName(report, side) {
    var box = report && report[side];
    return (box && box.agent && box.agent.name) || side.toUpperCase();
  }
  function rowFor(report, side, step) {
    var rows = report && Array.isArray(report.alignment) ? report.alignment : [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i][side + "_index"] === step) return i;
    }
    return -1;
  }
  function decisiveOf(report) {
    var diag = report && report.diagnosis;
    var dec = diag && diag.decisive_step;
    if (!dec || typeof dec !== "object" || !isNum(dec.step)) return null;
    var side = diag.subject === "a" || diag.subject === "b" ? diag.subject : null;
    return { side: side, step: dec.step, window: dec.window || null,
             verification: dec.verification || "hypothesized", criterion: dec.criterion || "" };
  }
  function selectStep(report, side, step) {
    var row = rowFor(report, side, step);
    if (row < 0) return;
    try {
      document.dispatchEvent(new CustomEvent("agentdiff:select-step", { detail: { row: row, side: side } }));
    } catch (err) { /* nothing to notify */ }
  }
  function truncate(text, n) {
    text = String(text === null || text === undefined ? "" : text).replace(/\s+/g, " ").trim();
    return text.length > n ? text.slice(0, Math.max(1, n - 1)) + "…" : text;
  }
  function humanKind(kind) { return String(kind || "").replace(/_/g, " "); }
  function width(host, ctx) {
    var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
    if (!w && ctx && ctx.width) w = ctx.width(host);
    return Math.max(300, Math.min(1120, w || 320));
  }

  /* Blocks render before their card is in the document, so the first
   * measurement is a guess. `responsive` draws now, redraws once the host
   * is attached (next frame) and again whenever the window resizes, and
   * only when the measured width actually changed — a chart never redraws
   * itself into a loop. */
  var painters = [];
  var resizeTimer = null;
  function responsive(host, draw) {
    var last = -1;
    function go() {
      if (!host.isConnected && last >= 0) return;
      var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
      if (w && Math.abs(w - last) < 2) return;
      last = w || last;
      host.innerHTML = "";
      draw();
    }
    go();
    if (typeof global.requestAnimationFrame === "function") {
      global.requestAnimationFrame(function () { if (host.isConnected) go(); });
    }
    painters.push({ host: host, go: go });
    if (painters.length > 64) painters = painters.filter(function (p) { return p.host.isConnected; });
  }
  try {
    global.addEventListener("resize", function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        resizeTimer = null;
        painters = painters.filter(function (p) { return p.host.isConnected; });
        painters.forEach(function (p) { try { p.go(); } catch (err) { /* keep the rest */ } });
      }, 140);
    });
  } catch (err) { /* no window */ }
  charts.responsive = responsive;
  /* A solid backing behind an SVG label, sized from its box, so a line
   * running underneath never shows through the spaces between words. */
  function backed(parent, textSel, fill, pad) {
    var node = textSel.node();
    if (!node || !node.getBBox) return;
    var box;
    try { box = node.getBBox(); } catch (err) { return; }
    if (!box || !box.width) return;
    pad = pad || 2;
    var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", box.x - pad); rect.setAttribute("y", box.y - 1);
    rect.setAttribute("width", box.width + 2 * pad); rect.setAttribute("height", box.height + 2);
    rect.setAttribute("rx", 2); rect.setAttribute("fill", fill); rect.setAttribute("opacity", 0.92);
    rect.setAttribute("class", "d3c-backing");
    node.parentNode.insertBefore(rect, node);
  }

  function hatch(svg, id, color) {
    var defs = svg.select("defs");
    if (defs.empty()) defs = svg.append("defs");
    var pat = defs.append("pattern")
      .attr("id", id).attr("width", 6).attr("height", 6)
      .attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 6)
      .attr("stroke", color).attr("stroke-width", 1.5).attr("opacity", 0.55);
    return "url(#" + id + ")";
  }

  // ------------------------------------------------------------ tooltip

  var tipEl = null;
  function tip() {
    if (tipEl && tipEl.isConnected) return tipEl;
    tipEl = document.createElement("div");
    tipEl.className = "d3c-tip";
    tipEl.setAttribute("role", "tooltip");
    tipEl.setAttribute("aria-hidden", "true");
    document.body.appendChild(tipEl);
    return tipEl;
  }
  function showTip(event, lines) {
    var el = tip();
    el.innerHTML = "";
    lines.forEach(function (line) {
      if (!line) return;
      var node = document.createElement(line.b ? "b" : "div");
      if (line.mono) node.className = "mono";
      node.textContent = line.text !== undefined ? line.text : String(line);
      el.appendChild(node);
    });
    el.classList.add("on");
    el.setAttribute("aria-hidden", "false");
    moveTip(event);
  }
  function moveTip(event) {
    if (!tipEl) return;
    var x = event.clientX + 14, y = event.clientY + 14;
    var vw = global.innerWidth || 1200, vh = global.innerHeight || 800;
    var w = tipEl.offsetWidth || 240, h = tipEl.offsetHeight || 60;
    if (x + w > vw - 8) x = Math.max(8, event.clientX - w - 14);
    if (y + h > vh - 8) y = Math.max(8, event.clientY - h - 14);
    tipEl.style.left = x + "px";
    tipEl.style.top = y + "px";
  }
  function hideTip() {
    if (!tipEl) return;
    tipEl.classList.remove("on");
    tipEl.setAttribute("aria-hidden", "true");
  }
  charts._tip = { show: showTip, hide: hideTip };

  // ---------------------------------------------------- the step line

  /* One scale for a run's steps, shared by the story and the forward
   * charts so a step sits at the same x in both. */
  function stepScale(n, W, left, right) {
    return d3.scalePoint().domain(d3.range(n)).range([left, W - right]).padding(0.5);
  }

  var ROLE = {
    feeds_answer:   { label: "fed the answer",   shape: "circle",  fill: true },
    answer:         { label: "the answer",       shape: "answer",  fill: true },
    frame:          { label: "framed the task",  shape: "square",  fill: false },
    decide:         { label: "decided",          shape: "square",  fill: false },
    verify:         { label: "checked its work", shape: "diamond", fill: false },
    dead_end:       { label: "dead end",         shape: "circle",  fill: false, dashed: true },
    no_information: { label: "returned nothing", shape: "circle",  fill: false, dashed: true },
    repeat:         { label: "repeated a step",  shape: "circle",  fill: false, dashed: true },
    error:          { label: "error",            shape: "circle",  fill: false, bad: true },
  };
  var INTENT_TINT = { frame: "muted", acquire: "a", transform: "warn", verify: "good", commit: "ink", decide: "muted" };
  var STATUS_COLOR = { supported: "good", stale: "muted", self_asserted: "warn", unsupported: "bad", contradicted: "bad" };

  function markPath(shape, r) {
    if (shape === "square") return "M" + (-r) + "," + (-r) + "h" + (2 * r) + "v" + (2 * r) + "h" + (-2 * r) + "z";
    if (shape === "diamond") return "M0," + (-r - 1) + "L" + (r + 1) + ",0L0," + (r + 1) + "L" + (-r - 1) + ",0z";
    return null;
  }

  function drawMark(g, role, color, r) {
    var spec = ROLE[role] || { shape: "circle", fill: false };
    var P = palette();
    var stroke = spec.bad ? P.bad : color;
    var path = markPath(spec.shape, r);
    var node;
    if (spec.shape === "answer") {
      g.append("circle").attr("r", r + 4).attr("fill", "none").attr("stroke", color).attr("stroke-width", 1.2).attr("opacity", 0.6);
      node = g.append("circle").attr("r", r).attr("fill", color);
    } else if (path) {
      node = g.append("path").attr("d", path).attr("fill", spec.fill ? color : P.surface).attr("stroke", stroke).attr("stroke-width", 1.6);
    } else {
      node = g.append("circle").attr("r", r).attr("fill", spec.fill ? color : P.surface).attr("stroke", stroke).attr("stroke-width", 1.6);
    }
    node.attr("class", "d3c-mark");
    if (spec.dashed) node.attr("stroke-dasharray", "2.5 2");
    return node;
  }

  function legendItem(P, spec, color, text) {
    var wrap = document.createElement("span");
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", 14); svg.setAttribute("height", 14); svg.setAttribute("viewBox", "-7 -7 14 14");
    svg.setAttribute("aria-hidden", "true");
    var sel = d3.select(svg);
    if (spec.line) {
      sel.append("line").attr("x1", -6).attr("x2", 6).attr("y1", 0).attr("y2", 0)
        .attr("stroke", color).attr("stroke-width", 2).attr("stroke-dasharray", spec.dashed ? "3 2" : null);
    } else if (spec.ring) {
      sel.append("circle").attr("r", 5).attr("fill", "none").attr("stroke", color).attr("stroke-width", 2).attr("stroke-dasharray", spec.dashed ? "4 2" : null);
    } else if (spec.hatch) {
      sel.append("rect").attr("x", -6).attr("y", -5).attr("width", 12).attr("height", 10).attr("fill", color).attr("opacity", 0.35);
    } else {
      drawMark(sel.append("g"), spec.role, color, 4.5);
    }
    wrap.appendChild(svg);
    wrap.appendChild(document.createTextNode(text));
    return wrap;
  }

  // ================================================ 1. what happened

  charts.story = function (host, ctx, side) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    var reading = readingOf(report, side);
    var steps = stepsOf(report, side);
    if (!reading || !steps.length) return null;
    var drawn = null;
    responsive(host, function () { drawn = drawStory(host, ctx, side, report, reading, steps); });
    return drawn;
  };

  function drawStory(host, ctx, side, report, reading, steps) {
    var P = palette();
    var color = sideColor(side);
    var duration = charts.motion();
    var what = Array.isArray(reading.what_happened) ? reading.what_happened : [];
    var byStep = {};
    what.forEach(function (w) { if (w && isNum(w.step)) byStep[w.step] = w; });
    var n = steps.length;
    var answerIdx = -1;
    what.forEach(function (w) { if (w.role === "answer") answerIdx = w.step; });
    if (answerIdx < 0) answerIdx = n - 1;
    var rests = (Array.isArray(reading.rests_on) ? reading.rests_on : []).filter(function (r) {
      return r && isNum(r.first_step) && r.first_step < answerIdx;
    });
    var basis = reading.answer_basis || {};
    var dec = decisiveOf(report);
    var errors = Array.isArray(reading.errors) ? reading.errors : [];
    var critical = reading.critical_error && isNum(reading.critical_error.step) ? reading.critical_error.step : null;

    var W = width(host, ctx);
    var m = { l: 14, r: 14 };
    var x = stepScale(n, W, m.l, m.r);
    var slot = x.step();
    var wide = slot >= 54;
    var yPhase = 18, yStep = 62, yArcTop = 84;
    var spent = isNum(basis.basis_complete_at) && isNum(basis.steps_after_basis_complete) &&
                basis.steps_after_basis_complete > 0 && basis.basis_complete_at < answerIdx;
    var maxStack = 0;
    var seen = {};
    rests.forEach(function (r) { seen[r.first_step] = (seen[r.first_step] || 0) + 1; if (seen[r.first_step] > maxStack) maxStack = seen[r.first_step]; });
    var captioned = spent && wide && (x(answerIdx) - x(basis.basis_complete_at) - slot) >= 160;
    var arcSpan = rests.length ? 46 + Math.min(4, maxStack) * 13 + (captioned ? 12 : 0) : 0;
    var H = yArcTop + arcSpan + (wide ? 28 : 18) + (spent && !rests.length ? 14 : 0);

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap");
    var svg = wrap.append("svg")
      .attr("class", "d3c d3c-story")
      .attr("width", W).attr("height", H).attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img")
      .attr("aria-label", agentName(report, side) + ": " + n + " steps in order, phases, where each answer value entered, and the decisive step");
    svg.append("title").text("What happened in " + agentName(report, side) + ", step by step");

    // ---- phases: a band per phase, tinted by intent, labelled when there is room
    var phases = Array.isArray(reading.phases) ? reading.phases : [];
    var phaseG = svg.append("g").attr("class", "d3c-phases");
    phases.forEach(function (ph) {
      var st = Array.isArray(ph.steps) ? ph.steps.filter(isNum) : [];
      if (!st.length) return;
      var x0 = x(d3.min(st)) - slot / 2 + 2, x1 = x(d3.max(st)) + slot / 2 - 2;
      var tint = P[INTENT_TINT[ph.intent] || "rule2"] || P.rule2;
      var g = phaseG.append("g").attr("class", "d3c-phase").attr("data-intent", ph.intent || "");
      g.append("rect").attr("x", x0).attr("y", yPhase - 8).attr("width", Math.max(4, x1 - x0)).attr("height", 16)
        .attr("rx", 3).attr("fill", tint).attr("opacity", 0.16);
      g.append("rect").attr("x", x0).attr("y", yPhase + 6).attr("width", Math.max(4, x1 - x0)).attr("height", 2)
        .attr("fill", tint).attr("opacity", 0.7);
      if (x1 - x0 >= 44) {
        g.append("text").attr("class", "d3c-phase-label").attr("x", (x0 + x1) / 2).attr("y", yPhase + 3)
          .attr("text-anchor", "middle").attr("fill", tint).text(truncate(ph.intent || "phase", Math.floor((x1 - x0) / 7)));
      }
      g.append("title").text((ph.intent || "phase") + " — " + (ph.summary || ""));
    });

    // ---- the spent-after-basis region, behind the marks
    if (spent) {
      var sx0 = x(basis.basis_complete_at) + slot / 2, sx1 = x(answerIdx) - slot / 2;
      if (sx1 > sx0) {
        var g2 = svg.append("g").attr("class", "d3c-spent");
        g2.append("rect").attr("x", sx0).attr("y", yStep - 11).attr("width", sx1 - sx0).attr("height", 22)
          .attr("fill", hatch(svg, "d3c-hatch-" + side, P.warn)).attr("rx", 3);
        if (wide && sx1 - sx0 >= 160) {
          // the caption sits under the step names, clear of the marks; a
          // narrow chart leaves it to the legend
          g2.append("text").attr("class", "d3c-cap").attr("x", (sx0 + sx1) / 2).attr("y", yStep + (wide ? 44 : 31))
            .attr("text-anchor", "middle").attr("fill", P.warn).attr("font-weight", 600)
            .text(basis.steps_after_basis_complete + " step" + (basis.steps_after_basis_complete === 1 ? "" : "s") +
                  " spent after the basis was complete");
        }
        g2.append("title").text("the answer's basis was complete at step " + basis.basis_complete_at +
          "; " + basis.steps_after_basis_complete + " more step(s) were spent before committing");
      }
    }

    // ---- spine
    svg.append("line").attr("x1", x(0)).attr("x2", x(n - 1)).attr("y1", yStep).attr("y2", yStep)
      .attr("stroke", P.rule2).attr("stroke-width", 1.4);

    // ---- answer-basis arcs: from the step that first produced a value to the answer
    var arcG = svg.append("g").attr("class", "d3c-arcs");
    var byOrigin = {};
    rests.forEach(function (r) { (byOrigin[r.first_step] = byOrigin[r.first_step] || []).push(r); });
    var labelJobs = [];
    rests.forEach(function (r, i) {
      var group = byOrigin[r.first_step];
      var k = group.indexOf(r);
      var x0 = x(r.first_step), x1 = x(answerIdx);
      var depth = 20 + k * 13 + Math.min(30, (x1 - x0) / 12) + (captioned ? 12 : 0);
      var key = STATUS_COLOR[r.status] || "muted";
      var stroke = P[key];
      var d = "M" + x0 + "," + (yStep + 8) + " C" + x0 + "," + (yArcTop + depth) + " " + x1 + "," + (yArcTop + depth) + " " + x1 + "," + (yStep + 8);
      var path = arcG.append("path").attr("class", "d3c-arc " + (r.status || "")).attr("d", d).attr("stroke", stroke)
        .attr("data-status", r.status || "").attr("data-from", r.first_step).attr("data-value", String(r.value));
      if (duration && path.node().getTotalLength) {
        var len = path.node().getTotalLength();
        path.attr("stroke-dasharray", (r.status === "contradicted" || r.status === "stale") ? "4 3" : len + " " + len)
          .attr("stroke-dashoffset", (r.status === "contradicted" || r.status === "stale") ? 0 : len);
        if (r.status !== "contradicted" && r.status !== "stale") {
          path.transition().duration(duration).delay(60 * i).ease(d3.easeCubicOut).attr("stroke-dashoffset", 0);
        }
      }
      var wrong = r.matches_expected === false;
      if (k < 4 && (x1 - x0) >= 40) {
        // the label sits on the curve's apex (cubic with both controls at yArcTop+depth)
        labelJobs.push({ r: r, wrong: wrong, stroke: stroke, x: (x0 + x1) / 2,
                         y: (yStep + 8) + 0.75 * ((yArcTop + depth) - (yStep + 8)) + 3,
                         label: truncate(r.value, Math.max(6, Math.floor((x1 - x0) / 6.5))) });
      }
      path.append("title").text(String(r.value) + " — " + humanKind(r.status) + ", first at step " + r.first_step +
        (r.source ? " via " + r.source : "") + (wrong ? " · does not match the expected answer" : ""));
    });
    labelJobs.forEach(function (job) {
      var r = job.r;
      var t = arcG.append("text").attr("class", "d3c-arc-label").attr("x", job.x).attr("y", job.y)
        .attr("text-anchor", "middle").attr("fill", job.wrong ? P.bad : job.stroke)
        .text(job.label + (job.wrong ? " ✗" : ""));
      backed(arcG, t, P.surface, 3);
      t.append("title").text(String(r.value) + " — " + humanKind(r.status) + (r.source ? " (" + r.source + ")" : "") +
        (job.wrong ? " · does not match the expected answer" : r.matches_expected === true ? " · matches the expected answer" : ""));
    });
    if (rests.length > 4) {
      arcG.append("text").attr("class", "d3c-cap").attr("x", x(answerIdx)).attr("y", yArcTop + arcSpan + 4)
        .attr("text-anchor", "end").text(rests.length + " values in the answer, " + Object.keys(byOrigin).length + " origin step(s)");
    }

    // ---- basis-complete tick
    if (isNum(basis.basis_complete_at) && basis.basis_complete_at < answerIdx) {
      var bx = x(basis.basis_complete_at);
      var bt = svg.append("g").attr("class", "d3c-basis");
      bt.append("line").attr("x1", bx).attr("x2", bx).attr("y1", yStep - 14).attr("y2", yStep + 14)
        .attr("stroke", P.good).attr("stroke-width", 1.2).attr("stroke-dasharray", "2 2");
      bt.append("title").text("basis complete at step " + basis.basis_complete_at + " — every value the answer rests on existed by here");
    }

    // ---- steps: one focusable mark each
    var marks = svg.append("g").attr("class", "d3c-steps").selectAll("g.d3c-step")
      .data(steps.map(function (s, i) { return { i: i, step: s, w: byStep[i] || {} }; }))
      .join("g")
      .attr("class", "d3c-step")
      .attr("data-step", function (d) { return d.i; })
      .attr("data-role", function (d) { return d.w.role || ""; })
      .attr("tabindex", 0).attr("role", "button")
      .attr("aria-label", function (d) {
        return "step " + d.i + " · " + (d.step.name || d.step.type || "step") + " · " + ((ROLE[d.w.role] || {}).label || d.w.role || "");
      })
      .attr("transform", function (d) { return "translate(" + x(d.i) + "," + yStep + ")"; });
    marks.each(function (d) {
      var g = d3.select(this);
      g.append("circle").attr("r", Math.max(10, slot / 2)).attr("fill", "transparent");
      var role = d.w.role || (d.step.type === "answer" ? "answer" : "dead_end");
      if (d.step.error === true) role = "error";
      drawMark(g, role, color, 6);
      if (d.w.invented_argument) {
        g.append("text").attr("class", "d3c-cap").attr("x", 0).attr("y", -13).attr("text-anchor", "middle")
          .attr("fill", P.warn).attr("font-weight", 700).text("invented");
      }
      g.append("text").attr("class", "d3c-idx").attr("x", 0).attr("y", wide ? 20 : 19).attr("text-anchor", "middle").text(String(d.i));
      if (wide) {
        var name = truncate(d.step.name || d.step.type || "", Math.floor(slot / 6.2));
        g.append("text").attr("class", "d3c-name").attr("x", 0).attr("y", 31).attr("text-anchor", "middle").text(name);
      }
    });
    if (duration) {
      marks.attr("opacity", 0).transition().duration(duration * 0.6).delay(function (d) { return 30 * d.i; }).attr("opacity", 1);
    }

    // ---- errors and the critical one
    errors.forEach(function (e) {
      if (!e || !isNum(e.step) || e.step >= n) return;
      var eg = svg.append("g").attr("class", "d3c-error").attr("transform", "translate(" + x(e.step) + "," + (yStep - 16) + ")");
      eg.append("text").attr("text-anchor", "middle").attr("fill", P.bad).attr("font-weight", 800).attr("font-size", 11)
        .text(e.step === critical ? "!!" : "!");
      eg.append("title").text("error at step " + e.step + (e.status ? " — " + humanKind(e.status) : "") + (e.step === critical ? " (critical)" : ""));
    });

    // ---- decisive ring
    if (dec && dec.side === side && dec.step < n) {
      var ring = svg.append("g").attr("class", "d3c-decisive").attr("transform", "translate(" + x(dec.step) + "," + yStep + ")");
      ring.append("circle").attr("class", "d3c-ring " + (dec.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", 12);
      ring.append("text").attr("class", "d3c-cap").attr("x", 0).attr("y", -17).attr("text-anchor", "middle")
        .attr("fill", P.bad).attr("font-weight", 700).text("decisive");
      ring.append("title").text("decisive step " + dec.step + " — " + dec.criterion + " (" + dec.verification + ")");
    }

    // ---- interactions
    marks.on("mousemove", function (event, d) {
      var lines = [{ b: true, text: "step " + d.i + " · " + (d.step.name || d.step.type || "step") },
        { text: (d.step.type || "") + " · " + ((ROLE[d.w.role] || {}).label || humanKind(d.w.role)) +
                (d.w.intent ? " · " + d.w.intent : "") }];
      var out = truncate(d.step.output || d.step.input || "", 140);
      if (out) lines.push({ mono: true, text: out });
      showTip(event, lines);
    }).on("mouseleave", hideTip)
      .on("click", function (event, d) { hideTip(); selectStep(report, side, d.i); })
      .on("keydown", function (event, d) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectStep(report, side, d.i); }
      });
    arcG.selectAll("path.d3c-arc").on("mousemove", function (event) {
      var r = rests[arcG.selectAll("path.d3c-arc").nodes().indexOf(this)];
      if (!r) return;
      showTip(event, [{ b: true, text: String(r.value) },
        { text: humanKind(r.status) + " · first at step " + r.first_step + (r.source ? " via " + r.source : "") },
        r.matches_expected === false ? { text: "does not match the expected answer" } : null]);
    }).on("mouseleave", hideTip);

    // ---- legend
    var legend = document.createElement("div");
    legend.className = "d3c-legend";
    var used = {};
    what.forEach(function (w) { if (w && w.role) used[w.role] = true; });
    ["feeds_answer", "answer", "frame", "decide", "verify", "dead_end", "no_information", "repeat", "error"].forEach(function (role) {
      if (used[role]) legend.appendChild(legendItem(P, { role: role }, color, ROLE[role].label));
    });
    if (rests.length) {
      var statuses = {};
      rests.forEach(function (r) { statuses[r.status] = true; });
      Object.keys(statuses).forEach(function (st) {
        legend.appendChild(legendItem(P, { line: true, dashed: st === "contradicted" || st === "stale" }, P[STATUS_COLOR[st] || "muted"],
          "value " + humanKind(st) + " → answer"));
      });
      if (rests.some(function (r) { return r.matches_expected === false; })) {
        var wrongKey = document.createElement("span");
        wrongKey.style.color = P.bad;
        wrongKey.textContent = "✗ value does not match the expected answer";
        legend.appendChild(wrongKey);
      }
    }
    if (dec && dec.side === side) legend.appendChild(legendItem(P, { ring: true, dashed: dec.verification !== "replay-verified" }, P.bad,
      "decisive step (" + dec.verification + ")"));
    if (isNum(basis.steps_after_basis_complete) && basis.steps_after_basis_complete > 0) {
      legend.appendChild(legendItem(P, { hatch: true }, P.warn, "spent after the basis was complete"));
    }
    host.appendChild(legend);
    return svg.node();
  }

  // ============================================================ 2. why

  charts.why = function (host, ctx) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    var diag = report && report.diagnosis;
    var hyps = diag && Array.isArray(diag.hypotheses) ? diag.hypotheses : [];
    if (!hyps.length) return null;
    var drawn = null;
    responsive(host, function () { drawn = drawWhy(host, ctx, report, diag, hyps); });
    return drawn;
  };

  function drawWhy(host, ctx, report, diag, hyps) {
    var P = palette();
    var duration = charts.motion();
    var evidence = {};
    (Array.isArray(diag.evidence) ? diag.evidence : []).forEach(function (e) { if (e && e.id) evidence[e.id] = e; });
    var side = diag.subject === "a" || diag.subject === "b" ? diag.subject : "b";
    var steps = stepsOf(report, side);
    var dec = decisiveOf(report);

    var W = width(host, ctx);
    var labelW = Math.min(190, Math.round(W * 0.32));
    var evW = Math.min(150, Math.round(W * 0.24));
    var rowH = 30, top = 8;
    var barX0 = labelW + 8, barX1 = W - evW - 12;
    var stripH = dec && steps.length ? 46 : 0;
    var H = top + hyps.length * rowH + 22 + stripH;
    var x = d3.scaleLinear().domain([0, 1]).range([barX0, barX1]);

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap");
    var svg = wrap.append("svg").attr("class", "d3c d3c-why")
      .attr("width", W).attr("height", H).attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img").attr("aria-label", "Hypotheses ranked by the engine's score, with their evidence");
    svg.append("title").text("Why: the hypotheses as scored, with their evidence");

    // grid
    [0, 0.25, 0.5, 0.75, 1].forEach(function (v) {
      svg.append("line").attr("x1", x(v)).attr("x2", x(v)).attr("y1", top).attr("y2", top + hyps.length * rowH)
        .attr("stroke", P.rule).attr("stroke-width", 1);
    });
    svg.append("text").attr("class", "d3c-cap").attr("x", barX1).attr("y", top + hyps.length * rowH + 14).attr("text-anchor", "end")
      .text("score, as the engine assigned it (0–1)");
    svg.append("text").attr("class", "d3c-cap").attr("x", barX1 + 12).attr("y", top + hyps.length * rowH + 14).attr("text-anchor", "start")
      .text("evidence");

    var statusFill = { leading: P.accent, merged: P.muted, plausible: P.warn, ruled_out: P.rule2 };
    var rows = svg.append("g").selectAll("g.d3c-hyprow").data(hyps).join("g")
      .attr("class", "d3c-hyprow").attr("data-status", function (h) { return h.status || ""; })
      .attr("data-kind", function (h) { return h.kind || ""; })
      .attr("transform", function (h, i) { return "translate(0," + (top + i * rowH) + ")"; });
    rows.append("text").attr("class", function (h) { return "d3c-hyp " + (h.status || ""); })
      .attr("x", labelW).attr("y", rowH / 2 + 4).attr("text-anchor", "end")
      .text(function (h) { return truncate(humanKind(h.kind), Math.floor(labelW / 6.6)); })
      .append("title").text(function (h) { return humanKind(h.kind) + " — " + humanKind(h.status); });
    var bars = rows.append("rect").attr("class", "d3c-bar")
      .attr("x", barX0).attr("y", 7).attr("height", rowH - 14).attr("rx", 3)
      .attr("fill", function (h) { return statusFill[h.status] || P.muted; })
      .attr("opacity", function (h) { return h.status === "leading" ? 0.95 : 0.6; })
      .attr("width", 0);
    bars.append("title").text(function (h) {
      return humanKind(h.kind) + " · score " + (isNum(h.score) ? h.score.toFixed(2) : "—") + " · " + humanKind(h.status) +
        " · " + (h.supports || []).length + " for, " + (h.contradicts || []).length + " against";
    });
    var finalW = function (h) { return isNum(h.score) ? Math.max(0, x(h.score) - barX0) : 0; };
    if (duration) bars.transition().duration(duration).ease(d3.easeCubicOut).attr("width", finalW);
    else bars.attr("width", finalW);
    rows.append("text").attr("class", "d3c-cap mono").attr("y", rowH / 2 + 4)
      .attr("x", function (h) { return (isNum(h.score) ? x(h.score) : barX0) + 5; })
      .attr("fill", P.ink2).text(function (h) { return isNum(h.score) ? h.score.toFixed(2) : "no score"; });

    // evidence marks: supports (filled by class) then contradicts (red x)
    var CLASS_STYLE = { observable: { fill: true }, annotation: { half: true }, stated: { fill: false } };
    rows.each(function (h) {
      var g = d3.select(this).append("g").attr("class", "d3c-evidence").attr("transform", "translate(" + (barX1 + 12) + "," + (rowH / 2) + ")");
      var k = 0;
      var maxDots = Math.floor((evW - 8) / 11);
      var sup = Array.isArray(h.supports) ? h.supports : [];
      var con = Array.isArray(h.contradicts) ? h.contradicts : [];
      sup.slice(0, maxDots).forEach(function (id) {
        var e = evidence[id] || {};
        var st = CLASS_STYLE[e.evidence_class] || CLASS_STYLE.stated;
        var dot = g.append("circle").attr("class", "d3c-ev").attr("cx", k * 11 + 4).attr("cy", 0).attr("r", 3.6)
          .attr("fill", st.fill ? P.good : st.half ? P.surface : P.surface).attr("stroke", P.good).attr("stroke-width", 1.4)
          .attr("data-evidence", id).attr("data-class", e.evidence_class || "");
        if (st.half) dot.attr("stroke-dasharray", "3 2");
        dot.on("mousemove", function (event) {
          showTip(event, [{ b: true, text: id + " · supports · " + (e.evidence_class || "") },
            { text: e.signal || "" }, e.basis ? { mono: true, text: "basis: " + e.basis } : null]);
        }).on("mouseleave", hideTip);
        k++;
      });
      con.slice(0, Math.max(0, maxDots - k)).forEach(function (id) {
        var e = evidence[id] || {};
        var cx = k * 11 + 4;
        var xg = g.append("g").attr("class", "d3c-ev d3c-ev-con").attr("data-evidence", id).attr("data-class", e.evidence_class || "");
        xg.append("line").attr("x1", cx - 3.5).attr("x2", cx + 3.5).attr("y1", -3.5).attr("y2", 3.5).attr("stroke", P.bad).attr("stroke-width", 1.8);
        xg.append("line").attr("x1", cx - 3.5).attr("x2", cx + 3.5).attr("y1", 3.5).attr("y2", -3.5).attr("stroke", P.bad).attr("stroke-width", 1.8);
        xg.on("mousemove", function (event) {
          showTip(event, [{ b: true, text: id + " · contradicts · " + (e.evidence_class || "") },
            { text: e.signal || "" }, e.basis ? { mono: true, text: "basis: " + e.basis } : null]);
        }).on("mouseleave", hideTip);
        k++;
      });
      if (sup.length + con.length > maxDots) {
        g.append("text").attr("class", "d3c-cap").attr("x", k * 11 + 4).attr("y", 4).text("+" + (sup.length + con.length - maxDots));
      }
    });

    // margin bracket under the leading bar: what the number means, drawn
    var lead = hyps.filter(function (h) { return h.status === "leading"; })[0];
    if (lead && isNum(lead.score) && isNum(diag.margin) && diag.margin > 0) {
      var li = hyps.indexOf(lead);
      var y = top + li * rowH + rowH - 4;
      var mx1 = x(lead.score), mx0 = x(Math.max(0, lead.score - diag.margin));
      var mg = svg.append("g").attr("class", "d3c-margin");
      mg.append("line").attr("x1", mx0).attr("x2", mx1).attr("y1", y).attr("y2", y).attr("stroke", P.ink2).attr("stroke-width", 1);
      mg.append("line").attr("x1", mx0).attr("x2", mx0).attr("y1", y - 3).attr("y2", y + 3).attr("stroke", P.ink2).attr("stroke-width", 1);
      mg.append("line").attr("x1", mx1).attr("x2", mx1).attr("y1", y - 3).attr("y2", y + 3).attr("stroke", P.ink2).attr("stroke-width", 1);
      mg.append("title").text("margin " + diag.margin.toFixed(2) + " over the next unmerged hypothesis");
    }

    // decisive window on the step line
    if (stripH) {
      var sy = top + hyps.length * rowH + 22 + 18;
      var sx = stepScale(steps.length, W, 14, 14);
      svg.append("line").attr("x1", sx(0)).attr("x2", sx(steps.length - 1)).attr("y1", sy).attr("y2", sy).attr("stroke", P.rule2).attr("stroke-width", 1.2);
      steps.forEach(function (s, i) {
        svg.append("circle").attr("cx", sx(i)).attr("cy", sy).attr("r", 2.5).attr("fill", P.rule2);
        if (sx.step() >= 18) svg.append("text").attr("class", "d3c-idx").attr("x", sx(i)).attr("y", sy + 14).attr("text-anchor", "middle").text(String(i));
      });
      var win = dec.window || {};
      if (isNum(win.earliest) && isNum(win.point_of_no_return) && win.point_of_no_return >= win.earliest) {
        var wg = svg.append("g").attr("class", "d3c-window");
        wg.append("rect").attr("x", sx(win.earliest) - sx.step() / 2 + 2).attr("y", sy - 9)
          .attr("width", Math.max(4, sx(win.point_of_no_return) - sx(win.earliest) + sx.step() - 4)).attr("height", 18).attr("rx", 3)
          .attr("fill", P.bad).attr("opacity", 0.12);
        wg.append("title").text("window: earliest " + win.earliest + " → point of no return " + win.point_of_no_return);
      }
      var dg = svg.append("g").attr("class", "d3c-decisive").attr("transform", "translate(" + sx(dec.step) + "," + sy + ")");
      dg.append("circle").attr("class", "d3c-ring " + (dec.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", 6.5);
      dg.append("circle").attr("r", 3).attr("fill", P.bad);
      dg.append("title").text("decisive step " + dec.step + " (" + dec.verification + ")");
      svg.append("text").attr("class", "d3c-cap").attr("x", 14).attr("y", sy - 14).attr("text-anchor", "start")
        .text(agentName(report, side) + " · decisive step " + dec.step + " · " + dec.verification +
              (isNum((dec.window || {}).steps) ? " · window of " + dec.window.steps + " step(s)" : ""));
    }

    var legend = document.createElement("div");
    legend.className = "d3c-legend";
    [["leading", "leading"], ["merged", "merged into the leader"], ["plausible", "plausible"], ["ruled_out", "ruled out"]].forEach(function (pair) {
      if (!hyps.some(function (h) { return h.status === pair[0]; })) return;
      legend.appendChild(legendItem(P, { hatch: true }, statusFill[pair[0]], pair[1]));
    });
    legend.appendChild(legendItem(P, { role: "feeds_answer" }, P.good, "supports (filled: observable · dashed: annotation · hollow: stated)"));
    var xKey = document.createElement("span"); xKey.style.color = P.bad; xKey.textContent = "× contradicts";
    legend.appendChild(xKey);
    host.appendChild(legend);
    return svg.node();
  }

  // ==================================================== 3. take forward

  charts.forward = function (host, ctx, side) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    var reading = readingOf(report, side);
    var steps = stepsOf(report, side);
    var items = reading && Array.isArray(reading.take_forward) ? reading.take_forward : [];
    if (!steps.length || !items.length) return null;
    var drawn = null;
    responsive(host, function () { drawn = drawForward(host, ctx, side, report, steps, items); });
    return drawn;
  };

  function drawForward(host, ctx, side, report, steps, items) {
    var P = palette();
    var color = sideColor(side);
    var duration = charts.motion();
    var dec = decisiveOf(report);
    var n = steps.length;

    var W = width(host, ctx);
    var x = stepScale(n, W, 14, 14);
    var slot = x.step();
    // pins at the same step stack upward
    var lanes = {};
    var pinned = items.map(function (t, i) {
      var at = isNum(t.at_step) && t.at_step < n ? t.at_step : null;
      var lane = at === null ? 0 : (lanes[at] = (lanes[at] || 0) + 1) - 1;
      return { i: i, n: i + 1, t: t, at: at, lane: lane };
    });
    var maxLane = d3.max(pinned, function (p) { return p.lane; }) || 0;
    var pinTop = 14, pinGap = 24;
    var ySpine = pinTop + (maxLane + 1) * pinGap + 8;
    var H = ySpine + 24;

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap");
    var svg = wrap.append("svg").attr("class", "d3c d3c-forward")
      .attr("width", W).attr("height", H).attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img").attr("aria-label", items.length + " next action(s) for " + agentName(report, side) + ", pinned to the steps they apply to");
    svg.append("title").text("Take forward: where to intervene in " + agentName(report, side));

    svg.append("line").attr("x1", x(0)).attr("x2", x(n - 1)).attr("y1", ySpine).attr("y2", ySpine).attr("stroke", P.rule2).attr("stroke-width", 1.4);
    steps.forEach(function (s, i) {
      var g = svg.append("g").attr("class", "d3c-ghost").attr("transform", "translate(" + x(i) + "," + ySpine + ")");
      var isDec = dec && dec.side === side && dec.step === i;
      g.append("circle").attr("r", isDec ? 4 : 3).attr("fill", isDec ? P.bad : P.surface).attr("stroke", isDec ? P.bad : P.rule2).attr("stroke-width", 1.4);
      if (slot >= 18) g.append("text").attr("class", "d3c-idx").attr("y", 16).attr("text-anchor", "middle").text(String(i));
      g.append("title").text("step " + i + " · " + (s.name || s.type || "") + (isDec ? " · decisive" : ""));
    });

    var pins = svg.append("g").selectAll("g.d3c-pin").data(pinned.filter(function (p) { return p.at !== null; })).join("g")
      .attr("class", "d3c-pin").attr("tabindex", 0).attr("role", "button")
      .attr("data-n", function (p) { return p.n; }).attr("data-step", function (p) { return p.at; })
      .attr("aria-label", function (p) { return "action " + p.n + " at step " + p.at + ": " + (p.t.instead || p.t.action || ""); })
      .attr("transform", function (p) { return "translate(" + x(p.at) + "," + (ySpine - 10 - p.lane * pinGap) + ")"; });
    pins.append("line").attr("x1", 0).attr("x2", 0).attr("y1", 0).attr("y2", function (p) { return 10 + p.lane * pinGap - 4; })
      .attr("stroke", function (p) { return dec && dec.side === side && dec.step === p.at ? P.bad : color; }).attr("stroke-width", 1.4);
    pins.append("circle").attr("r", 9).attr("cy", -4)
      .attr("fill", function (p) { return dec && dec.side === side && dec.step === p.at ? P.bad : color; });
    pins.append("text").attr("y", 0).attr("text-anchor", "middle").text(function (p) { return String(p.n); });
    pins.append("title").text(function (p) { return "at step " + p.at + " — " + (p.t.instead || p.t.action || ""); });
    if (duration) {
      pins.attr("opacity", 0).attr("transform", function (p) { return "translate(" + x(p.at) + "," + (ySpine - 24 - p.lane * pinGap) + ")"; })
        .transition().duration(duration).delay(function (p) { return 70 * p.i; }).ease(d3.easeCubicOut)
        .attr("opacity", 1).attr("transform", function (p) { return "translate(" + x(p.at) + "," + (ySpine - 10 - p.lane * pinGap) + ")"; });
    }

    // the list, numbered to match the pins
    var list = document.createElement("ol");
    list.className = "d3c-list";
    var lis = {};
    pinned.forEach(function (p) {
      var t = p.t;
      var li = document.createElement("li");
      li.setAttribute("data-n", String(p.n));
      if (dec && dec.side === side && p.at === dec.step) li.className = "decisive";
      var num = document.createElement("span"); num.className = "n"; num.textContent = String(p.n);
      var body = document.createElement("div");
      var main = document.createElement("div");
      if (p.at !== null) {
        var stepLink = document.createElement("span"); stepLink.className = "step";
        stepLink.textContent = "at step " + p.at + " ";
        stepLink.setAttribute("role", "button"); stepLink.tabIndex = 0;
        stepLink.addEventListener("click", function () { selectStep(report, side, p.at); });
        stepLink.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectStep(report, side, p.at); } });
        main.appendChild(stepLink);
      } else {
        var noStep = document.createElement("span"); noStep.className = "step"; noStep.textContent = "no single step ";
        main.appendChild(noStep);
      }
      var instead = document.createElement("strong");
      instead.textContent = (t.conditional_on_validity ? "(conditional on the measurement) " : "") + String(t.instead || t.action || "");
      main.appendChild(instead);
      body.appendChild(main);
      if (t.what) { var what = document.createElement("div"); what.className = "what"; what.textContent = String(t.what); body.appendChild(what); }
      var meta = document.createElement("div"); meta.className = "meta";
      if (t.because) { var b = document.createElement("span"); b.className = "tag"; b.textContent = humanKind(t.because); meta.appendChild(b); }
      if (t.replay_recipe && typeof t.replay_recipe === "object") {
        var rr = document.createElement("span"); rr.className = "tag";
        rr.textContent = "replay" + (t.replay_recipe.replays ? " " + t.replay_recipe.replays : "") +
          (t.replay_recipe.expects ? " · expects " + t.replay_recipe.expects : "");
        rr.title = String(t.replay_recipe.correction || "");
        meta.appendChild(rr);
      }
      (Array.isArray(t.refs) ? t.refs : []).slice(0, 4).forEach(function (ref) {
        var r = document.createElement("span"); r.className = "tag"; r.textContent = String(ref); meta.appendChild(r);
      });
      if (meta.childNodes.length) body.appendChild(meta);
      li.appendChild(num); li.appendChild(body);
      list.appendChild(li);
      lis[p.n] = li;
    });
    host.appendChild(list);

    function light(nn, on) {
      Object.keys(lis).forEach(function (k) { lis[k].classList.toggle("on", on && String(k) === String(nn)); });
    }
    pins.on("mousemove", function (event, p) {
      light(p.n, true);
      showTip(event, [{ b: true, text: "action " + p.n + " · at step " + p.at }, { text: p.t.instead || p.t.action || "" }]);
    }).on("mouseleave", function () { light(null, false); hideTip(); })
      .on("click", function (event, p) { hideTip(); selectStep(report, side, p.at); })
      .on("keydown", function (event, p) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectStep(report, side, p.at); } });

    // what the fix buys — the report's own counterfactual estimate, labelled as one
    var cf = report && report.counterfactual;
    var est = cf && cf.estimate && typeof cf.estimate === "object" ? cf.estimate : null;
    var fail = report && report[side] && report[side].outcome ? report[side].outcome : null;
    var spend = report && report.tradeoff && report.tradeoff.spend ? report.tradeoff.spend[side] : null;
    if (est && spend && dec && dec.side === side) {
      var buys = document.createElement("div"); buys.className = "d3c-buys";
      function row(label, actual, estimate, fmt) {
        if (!isNum(actual) || !isNum(estimate)) return;
        var k = document.createElement("span"); k.className = "k"; k.textContent = label;
        var bars = document.createElement("div"); bars.className = "bars";
        var max = Math.max(actual, estimate, 1e-9);
        var a = document.createElement("i"); a.className = "actual"; a.style.width = Math.round(100 * actual / max) + "%"; a.title = "actual " + fmt(actual);
        var e = document.createElement("i"); e.className = "estimate"; e.style.width = Math.round(100 * estimate / max) + "%"; e.title = "estimate " + fmt(estimate);
        bars.appendChild(a); bars.appendChild(e);
        var v = document.createElement("span"); v.className = "v";
        v.textContent = fmt(actual) + " → " + fmt(estimate);
        buys.appendChild(k); buys.appendChild(bars); buys.appendChild(v);
      }
      row("tokens", spend.tokens, est.tokens, function (v) { return ctx.fmt && ctx.fmt.int ? ctx.fmt.int(v) : String(Math.round(v)); });
      row("latency", spend.latency_s, est.latency_s, function (v) { return (ctx.fmt && ctx.fmt.sec ? ctx.fmt.sec(v) : v.toFixed(2) + "s"); });
      row("steps", spend.steps, est.steps, function (v) { return String(Math.round(v)); });
      if (buys.childNodes.length) {
        var head = document.createElement("div"); head.className = "d3c-note";
        head.textContent = "What the fix buys — " + (cf.premise || "the counterfactual") + ": outcome " + String(est.outcome || "unknown") +
          ". A splice estimate, not a replay (confidence " + String(cf.confidence || "unstated") + "); grey is this run, blue the estimate.";
        host.appendChild(head);
        host.appendChild(buys);
      }
    }
    return svg.node();
  }

})(typeof window !== "undefined" ? window : this);
