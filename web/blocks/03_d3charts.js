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
      ".d3c:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}",
      ".d3c-toolbar{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;margin:2px 0 4px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".d3c-seg{display:inline-flex;gap:4px;align-items:center}",
      ".d3c-tb{font:inherit;font-size:var(--fs-xs);padding:2px 8px;border:1px solid var(--rule-2);border-radius:999px;background:var(--surface);color:var(--ink-2);cursor:pointer}",
      ".d3c-tb[aria-pressed=\"true\"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}",
      ".d3c-tb.play{font-weight:600}",
      ".d3c-scrub{width:140px;vertical-align:middle;accent-color:var(--accent)}",
      ".d3c-where{min-width:9ch;color:var(--ink-2)}",
      ".d3c-speed{font:inherit;font-size:var(--fs-xs);border:1px solid var(--rule-2);border-radius:4px;background:var(--surface);color:var(--ink-2)}",
      ".d3c-follow{display:inline-flex;gap:4px;align-items:center;cursor:pointer}",
      ".d3c .d3c-now .d3c-mark{stroke:var(--accent);stroke-width:3px;animation:d3c-pulse .7s ease-in-out infinite alternate}",
      ".d3c .d3c-pcell.d3c-now rect{stroke:var(--accent);stroke-width:2px}",
      ".d3c-replaying .d3c-future{transition:opacity .12s}",
      "@keyframes d3c-pulse{from{stroke-opacity:.5}to{stroke-opacity:1}}",
      "@media (prefers-reduced-motion:reduce){.d3c .d3c-now .d3c-mark{animation:none}}",
      "@media (max-width:480px){.d3c-scrub{width:90px}.d3c-follow{display:none}}",
      ".d3c .d3c-brush .selection{cursor:grab}",
      ".d3c .d3c-ov-arrow{user-select:none}",
      ".d3c-list .step.outside{color:var(--accent)}",
      ".tt-fold > summary{cursor:pointer;font-size:var(--fs-s);color:var(--ink-2);padding:6px 0;list-style:none}",
      ".tt-fold > summary::before{content:'▸ '}.tt-fold[open] > summary::before{content:'▾ '}",
      ".d3c-scroll{overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;max-width:100%}",
      ".d3c-scroll > svg{display:block;max-width:none}",
      ".d3c .d3c-bnode{cursor:pointer;outline:none}",
      ".d3c .d3c-bnode:focus-visible .d3c-bmark path,.d3c .d3c-bnode:focus-visible .d3c-bmark circle,.d3c .d3c-bnode:focus-visible .d3c-bleaf circle{stroke:var(--accent);stroke-width:3px}",
      ".d3c-body{cursor:grab}.d3c-body:active{cursor:grabbing}",
      ".d3c .d3c-bubble{cursor:zoom-in;outline:none}",
      ".d3c .d3c-bubble:focus-visible rect{stroke:var(--accent);stroke-width:3px}",
      ".d3c .d3c-tnode{cursor:pointer;outline:none}",
      ".d3c .d3c-tnode.kind-task{cursor:default}",
      ".d3c .d3c-tnode:focus-visible > circle:first-child{fill:var(--accent);fill-opacity:.15;stroke:var(--accent);stroke-width:1.5px}",
      ".d3c .d3c-tnode.branch.collapsed .d3c-tlabel{fill:var(--ink-3)}",
      ".d3c .d3c-tlabel{font-family:var(--sans)}",
      ".d3c .d3c-tlabel.mono{font-family:var(--mono)}",
      "body[data-view=\"story\"] .d3c-list li.on{background:transparent;box-shadow:inset 3px 0 0 var(--accent)}",
      "@media (max-width:480px){.d3c-list li{grid-template-columns:20px 1fr}",
      ".d3c-list .what{display:none}.d3c-list li[data-n]:hover .what,.d3c-list li[data-n]:focus-within .what{display:block}",
      ".d3c-buys{grid-template-columns:auto 1fr;}.d3c-buys .v{grid-column:2;text-align:left}}",
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
  function responsive(host, draw, key) {
    var last = -1;
    function go(force) {
      if (!host.isConnected && last >= 0) return;
      var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
      if (!force && w && Math.abs(w - last) < 2) return;
      last = w || last;
      host.innerHTML = "";
      draw();
    }
    // a detached host has no width: draw once it is in the document
    if (host.isConnected && host.clientWidth) go();
    if (typeof global.requestAnimationFrame === "function") {
      global.requestAnimationFrame(function () { if (host.isConnected) go(); });
    } else if (last < 0) {
      go();
    }
    painters.push({ host: host, go: go, key: key || null });
    if (painters.length > 64) painters = painters.filter(function (p) { return p.host.isConnected; });
    return host;
  }
  /* Redraw every chart that shares a focus key — the story, the forward
   * pins and the reconcile lanes of one run move their window together. */
  function repaint(key) {
    painters = painters.filter(function (p) { return p.host.isConnected; });
    painters.forEach(function (p) { if (p.key === key) { try { p.go(true); } catch (err) { /* keep the rest */ } } });
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

  // ------------------------------------------------ the focus window

  /* Long trajectories: every step-line chart draws a window of steps at a
   * readable size (≥ minSlot px each) and an overview of all of them
   * beneath a brush. The window is state per key — one per run per task —
   * so the story, the forward pins and the reconcile lanes move together.
   * The first window is centred on the anchor (the decisive step where
   * there is one); `all` shows every step compressed, labels off. */
  var Focus = {};
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function focusWindow(key, n, W, left, right, anchor, minSlot) {
    var cap = Math.max(8, Math.floor((W - left - right) / (minSlot || 44)));
    var state = Focus[key] || (Focus[key] = { start: null, all: false });
    if (n <= cap || state.all) {
      return { key: key, state: state, n: n, cap: cap, start: 0, end: n, size: n, windowed: false, compressed: n > cap,
               x: d3.scalePoint().domain(d3.range(n)).range([left, W - right]).padding(0.5),
               has: function (i) { return i >= 0 && i < n; } };
    }
    if (state.start === null) state.start = clamp((isNum(anchor) ? anchor - Math.floor(cap / 2) : n - cap), 0, n - cap);
    var start = clamp(state.start, 0, n - cap), end = start + cap;
    state.start = start;
    return { key: key, state: state, n: n, cap: cap, start: start, end: end, size: cap, windowed: true, compressed: false,
             x: d3.scalePoint().domain(d3.range(start, end)).range([left, W - right]).padding(0.5),
             has: function (i) { return i >= start && i < end; } };
  }
  charts.focus = {
    get: function (key) { var st = Focus[key]; return st ? { start: st.start, all: st.all } : null; },
    set: function (key, start) { var st = Focus[key] || (Focus[key] = { start: null, all: false }); st.start = Math.max(0, Math.round(start)); st.all = false; repaint(key); },
    all: function (key, on) { var st = Focus[key] || (Focus[key] = { start: null, all: false }); st.all = on === undefined ? !st.all : !!on; repaint(key); },
    keys: function () { return Object.keys(Focus); },
  };
  function moveWindow(win, delta) {
    if (!win.windowed) return;
    charts.focus.set(win.key, clamp(win.start + delta, 0, win.n - win.cap));
  }
  function bindWindowKeys(svg, win) {
    if (!win.windowed && !win.compressed) return;
    svg.attr("tabindex", 0).on("keydown.focus", function (event) {
      var k = event.key;
      if (k === "ArrowRight") { event.preventDefault(); moveWindow(win, Math.max(1, Math.floor(win.cap / 2))); }
      else if (k === "ArrowLeft") { event.preventDefault(); moveWindow(win, -Math.max(1, Math.floor(win.cap / 2))); }
      else if (k === "Home") { event.preventDefault(); charts.focus.set(win.key, 0); }
      else if (k === "End") { event.preventDefault(); charts.focus.set(win.key, win.n - win.cap); }
      else if (k === "a" || k === "A") { event.preventDefault(); charts.focus.all(win.key); }
    });
  }

  /* The overview: every step as a thin cell (tinted by its state when the
   * caller has one), the decisive step ticked, the window as a brush that
   * drags; arrows at either end page it; the caption says where you are. */
  function overviewStrip(svg, y, W, left, right, win, states, marks, label) {
    var P = palette();
    var n = win.n;
    var g = svg.append("g").attr("class", "d3c-overview").attr("transform", "translate(0," + y + ")");
    var trackX0 = left + 16, trackX1 = W - right - 16;
    var cw = (trackX1 - trackX0) / n;
    g.append("rect").attr("x", trackX0).attr("y", 0).attr("width", trackX1 - trackX0).attr("height", 10).attr("rx", 2).attr("fill", P.rule).attr("opacity", 0.7);
    var STATE_FILL = { fault: P.bad, committed: P.bad, diverged: P.warn, drift: P.warn, same: P.good, alone: P.rule2 };
    if (states) {
      var cells = g.append("g").attr("class", "d3c-ov-cells");
      // at most ~600 cells: past that, cells are merged into bins
      var bins = Math.min(n, Math.max(1, Math.floor(trackX1 - trackX0)));
      var per = n / bins;
      for (var b = 0; b < bins; b++) {
        var i0 = Math.floor(b * per), i1 = Math.max(i0 + 1, Math.floor((b + 1) * per));
        var worst = null, rank = { fault: 4, committed: 4, diverged: 3, drift: 2, alone: 1, same: 0 };
        for (var i = i0; i < i1; i++) {
          var st = states[i] && states[i].state;
          if (st && (worst === null || rank[st] > rank[worst])) worst = st;
        }
        if (!worst) continue;
        cells.append("rect").attr("x", trackX0 + i0 * cw).attr("y", 1).attr("width", Math.max(1, (i1 - i0) * cw)).attr("height", 8)
          .attr("fill", STATE_FILL[worst] || P.rule2).attr("opacity", worst === "same" ? 0.5 : 0.85);
      }
    }
    (marks || []).forEach(function (m) {
      if (!isNum(m.step)) return;
      g.append("line").attr("class", "d3c-ov-mark").attr("x1", trackX0 + (m.step + 0.5) * cw).attr("x2", trackX0 + (m.step + 0.5) * cw)
        .attr("y1", -3).attr("y2", 13).attr("stroke", m.color || P.bad).attr("stroke-width", 2);
    });
    var x0 = trackX0 + win.start * cw, x1 = trackX0 + win.end * cw;
    var brush = d3.brushX().extent([[trackX0, -2], [trackX1, 12]]).handleSize(6)
      .on("end", function (event) {
        if (!event.sourceEvent) return;
        var centre;
        if (event.selection) {
          centre = (event.selection[0] + event.selection[1]) / 2;
        } else {
          // a plain click on the track: centre the window there
          try { centre = d3.pointer(event.sourceEvent, this)[0]; } catch (err) { centre = null; }
          if (!isNum(centre)) { d3.select(this).call(brush.move, [x0, x1]); return; }
        }
        var start = clamp(Math.round((centre - trackX0) / cw - win.cap / 2), 0, n - win.cap);
        if (start !== win.start) charts.focus.set(win.key, start);
        else d3.select(this).call(brush.move, [x0, x1]);
      });
    var bg = g.append("g").attr("class", "d3c-brush").call(brush);
    bg.call(brush.move, [x0, x1]);
    bg.selectAll(".selection").attr("fill", P.accent).attr("fill-opacity", 0.18).attr("stroke", P.accent).attr("stroke-width", 1.2);
    bg.selectAll(".overlay").attr("cursor", "pointer");
    // paging arrows
    [["◀", left, -Math.max(1, Math.floor(win.cap / 2)), win.start > 0], ["▶", W - right - 12, Math.max(1, Math.floor(win.cap / 2)), win.end < n]].forEach(function (a) {
      var t = g.append("text").attr("class", "d3c-ov-arrow").attr("x", a[1]).attr("y", 9).attr("font-size", 10)
        .attr("fill", a[3] ? P.ink2 : P.rule2).attr("cursor", a[3] ? "pointer" : "default").attr("role", "button")
        .attr("aria-label", a[2] < 0 ? "earlier steps" : "later steps").text(a[0]);
      if (a[3]) t.on("click", function () { moveWindow(win, a[2]); });
    });
    g.append("text").attr("class", "d3c-cap d3c-ov-caption").attr("x", trackX0).attr("y", 24).attr("fill", P.ink2)
      .text("steps " + win.start + "–" + (win.end - 1) + " of " + n + (label ? " · " + label : "") + " · drag the window, ← → to page, a for all");
    return g;
  }

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

  function focusKey(report, side) {
    return (report && report.task && report.task.id ? report.task.id : "task") + ":" + side;
  }

  /* View modes per run: the x axis (step order or wall-clock time),
   * compression (runs of identical tool calls fold into one unit that
   * opens on click), and replay (the run re-told step by step). */
  var Modes = {};
  function modeOf(key) { return Modes[key] || (Modes[key] = { axis: "steps", compress: false, expanded: {}, play: null }); }
  charts.mode = {
    get: function (key) { var m = modeOf(key); return { axis: m.axis, compress: m.compress }; },
    set: function (key, patch) { var m = modeOf(key); Object.keys(patch || {}).forEach(function (k) { m[k] = patch[k]; }); repaint(key); },
    expand: function (key, steps, on) {
      var m = modeOf(key);
      (Array.isArray(steps) ? steps : [steps]).forEach(function (i) { if (on === false) delete m.expanded[i]; else m.expanded[i] = true; });
      repaint(key);
    },
  };

  /* The compressed view: consecutive steps with the same type and name —
   * and nothing that matters on its own (the decisive step, an error, a
   * step that produced an answer value, the answer) — become one unit
   * "lookup ×300". Everything downstream draws units; `real` maps a unit
   * back to its first step for selection and the inspector. */
  function compressView(steps, what, rests, dec, side, errors, path, expanded) {
    var byStep = {};
    what.forEach(function (w) { if (w && isNum(w.step)) byStep[w.step] = w; });
    var keep = {};
    rests.forEach(function (r) { keep[r.first_step] = true; });
    if (dec && dec.side === side) keep[dec.step] = true;
    errors.forEach(function (e) { if (e && isNum(e.step)) keep[e.step] = true; });
    var units = [], unitOf = [];
    var i = 0;
    while (i < steps.length) {
      var st = steps[i];
      var sig = (st.type || "") + "\u0000" + (st.name || "");
      var j = i + 1;
      var solo = keep[i] || st.type === "answer" || st.error === true || expanded[i];
      if (!solo) {
        while (j < steps.length) {
          var nx = steps[j];
          if ((nx.type || "") + "\u0000" + (nx.name || "") !== sig) break;
          if (keep[j] || nx.type === "answer" || nx.error === true || expanded[j]) break;
          j++;
        }
      }
      if (j - i >= 2) {
        var members = d3.range(i, j);
        var roles = {};
        members.forEach(function (k) { var r = (byStep[k] || {}).role || "dead_end"; roles[r] = (roles[r] || 0) + 1; });
        var role = Object.keys(roles).sort(function (a, b) { return roles[b] - roles[a]; })[0];
        var tokens = d3.sum(members, function (k) { return isNum(steps[k].tokens) ? steps[k].tokens : 0; });
        var latency = d3.sum(members, function (k) { return isNum(steps[k].latency_s) ? steps[k].latency_s : 0; });
        units.push({ kind: "group", from: i, to: j - 1, count: j - i, members: members,
                     step: { index: i, type: st.type, name: (st.name || st.type || "step") + " ×" + (j - i),
                             tokens: tokens, latency_s: latency, input: st.input, output: steps[j - 1].output },
                     w: { role: role, intent: (byStep[i] || {}).intent } });
        members.forEach(function () { unitOf.push(units.length - 1); });
        i = j;
      } else {
        units.push({ kind: "step", from: i, to: i, count: 1, members: [i], step: st, w: byStep[i] || {} });
        unitOf.push(units.length - 1);
        i = i + 1;
      }
    }
    return { units: units, unitOf: unitOf };
  }

  // wall-clock start of every step (cumulative latency), for the time axis
  function startTimes(steps) {
    var t = 0, out = [];
    steps.forEach(function (st) { out.push(t); t += isNum(st.latency_s) ? Math.max(0, st.latency_s) : 0; });
    return { starts: out, total: t };
  }

  charts.story = function (host, ctx, side) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    var reading = readingOf(report, side);
    var steps = stepsOf(report, side);
    if (!reading || !steps.length) return null;
    return responsive(host, function () { drawStory(host, ctx, side, report, reading, steps); }, focusKey(report, side));
  };

  function drawStory(host, ctx, side, report, reading, steps) {
    var P = palette();
    var color = sideColor(side);
    var duration = charts.motion();
    var key = focusKey(report, side);
    var mode = modeOf(key);
    var realSteps = steps;
    var what0 = Array.isArray(reading.what_happened) ? reading.what_happened : [];
    var failed = !(report && report[side] && report[side].outcome && report[side].outcome.success === true);
    var dec0 = decisiveOf(report);
    var errors0 = Array.isArray(reading.errors) ? reading.errors : [];
    var realAnswer = realSteps.length - 1;
    what0.forEach(function (w) { if (w && w.role === "answer") realAnswer = w.step; });
    var rests0 = (Array.isArray(reading.rests_on) ? reading.rests_on : []).filter(function (r) {
      return r && isNum(r.first_step) && r.first_step < realAnswer;
    });
    var path0 = failureStates(report, side, realSteps.length);

    // ---- the view model: real steps, or compressed units
    var view = compressView(realSteps, what0, rests0, dec0, side, errors0, path0, mode.compress ? mode.expanded : null || {});
    if (!mode.compress) view = { units: realSteps.map(function (st, i) { return { kind: "step", from: i, to: i, count: 1, members: [i], step: st, w: (function () { var f = null; what0.forEach(function (w) { if (w && w.step === i) f = w; }); return f || {}; })() }; }),
                                 unitOf: realSteps.map(function (st, i) { return i; }) };
    var units = view.units, unitOf = view.unitOf;
    var U = function (i) { return unitOf[i]; };                         // real step → unit index
    var real = function (u) { return units[u] ? units[u].from : u; };  // unit → its first real step
    var steps = units.map(function (u) { return u.step; });
    var byStep = {};
    units.forEach(function (u, i) { byStep[i] = u.w || {}; });
    var what = units.map(function (u, i) { return Object.assign({ step: i }, u.w || {}); });
    var n = units.length;
    var answerIdx = U(realAnswer);
    var rests = rests0.map(function (r) { return Object.assign({}, r, { first_step: U(r.first_step), real_step: r.first_step }); });
    var basis0 = reading.answer_basis || {};
    var basis = Object.assign({}, basis0, isNum(basis0.basis_complete_at) ? { basis_complete_at: U(basis0.basis_complete_at) } : {});
    var dec = dec0 && dec0.side === side ? Object.assign({}, dec0, { step: U(dec0.step), real_step: dec0.step }) : dec0;
    var errors = errors0.map(function (e) { return e && isNum(e.step) ? Object.assign({}, e, { step: U(e.step) }) : e; });
    var critical = reading.critical_error && isNum(reading.critical_error.step) ? U(reading.critical_error.step) : null;
    // the failure states per unit: the worst of its members
    var path = null;
    if (path0) {
      var rank = { fault: 5, committed: 6, diverged: 4, drift: 3, alone: 2, same: 1 };
      path = units.map(function (u, i) {
        var worst = null;
        u.members.forEach(function (k) { var d = path0[k]; if (d && (!worst || rank[d.state] > rank[worst.state])) worst = d; });
        var d = Object.assign({}, worst || { state: "alone", label: "", detail: "", source: "" }, { step: i });
        if (u.count > 1) d.label = u.count + " steps · " + (worst ? worst.label : "");
        return d;
      });
    }

    var W = width(host, ctx);
    var m = { l: 14, r: 14 };
    var anchor = dec && dec.side === side ? dec.step : (critical !== null ? critical : answerIdx);
    var win = focusWindow(key, n, W, m.l, m.r, anchor, 44);
    var x = win.x;
    var slot = x.step();
    // the time axis: units placed at their wall-clock start inside the
    // window, never closer than 9px so zero-latency steps stay distinct
    var times = startTimes(steps);
    if (mode.axis === "time") {
      var t0 = times.starts[win.start], t1 = times.starts[win.end - 1] + (isNum(steps[win.end - 1].latency_s) ? steps[win.end - 1].latency_s : 0);
      var tScale = d3.scaleLinear().domain([t0, Math.max(t1, t0 + 1e-6)]).range([m.l + 8, W - m.r - 8]);
      var pos = {}, prev = -Infinity;
      d3.range(win.start, win.end).forEach(function (i) { var px = Math.max(tScale(times.starts[i]), prev + 9); pos[i] = px; prev = px; });
      x = function (i) { return pos[i]; };
      x.step = function () { return slot; };
      slot = Math.min(slot, 44);
    }
    var wide = slot >= 54;
    var tiny = slot < 14;
    var top = win.windowed ? 34 : 0;
    var yPhase = 18 + top, yStep = 62 + top, yArcTop = 84 + top;
    var visible = d3.range(win.start, win.end);
    var spent = isNum(basis.basis_complete_at) && isNum(basis.steps_after_basis_complete) &&
                basis.steps_after_basis_complete > 0 && basis.basis_complete_at < answerIdx &&
                (win.has(basis.basis_complete_at) || win.has(answerIdx) || (basis.basis_complete_at < win.start && answerIdx >= win.end));
    // an x for any step: inside the window it is its own; outside it is the edge
    var xe = function (i) { return win.has(i) ? x(i) : (i < win.start ? m.l : W - m.r); };
    var maxStack = 0;
    var seen = {};
    rests.forEach(function (r) { seen[r.first_step] = (seen[r.first_step] || 0) + 1; if (seen[r.first_step] > maxStack) maxStack = seen[r.first_step]; });
    var captioned = spent && wide && (xe(answerIdx) - xe(basis.basis_complete_at) - slot) >= 160;
    var arcSpan = rests.length ? 46 + Math.min(4, maxStack) * 13 + (captioned ? 12 : 0) : 0;
    var stripTop = yArcTop + arcSpan + (wide ? 28 : 18) + (spent && !rests.length ? 14 : 0);
    var stripH = path ? 44 : 0;
    var H = stripTop + stripH;

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap");
    var svg = wrap.append("svg")
      .attr("class", "d3c d3c-story")
      .attr("width", W).attr("height", H).attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img")
      .attr("aria-label", agentName(report, side) + ": " + n + " steps in order, phases, where each answer value entered, and the decisive step" +
            (win.windowed ? "; showing steps " + win.start + " to " + (win.end - 1) : ""));
    svg.append("title").text("What happened in " + agentName(report, side) + ", step by step");
    bindWindowKeys(svg, win);
    if (win.windowed) {
      overviewStrip(svg, 6, W, m.l, m.r, win, path,
        dec && dec.side === side ? [{ step: dec.step, color: P.bad }] : [],
        (dec && dec.side === side ? "decisive at " + dec.real_step : "") + (mode.compress && n < realSteps.length ? " · " + realSteps.length + " steps as " + n + " units" : ""));
    }

    // ---- phases: a band per phase, tinted by intent, labelled when there is room
    var phases = Array.isArray(reading.phases) ? reading.phases : [];
    var phaseG = svg.append("g").attr("class", "d3c-phases");
    phases.forEach(function (ph) {
      var st = Array.isArray(ph.steps) ? ph.steps.filter(isNum).map(U).filter(function (i, k, all) { return win.has(i) && all.indexOf(i) === k; }) : [];
      if (!st.length) return;
      var pad0 = mode.axis === "time" ? 5 : slot / 2 - 2;
      var x0 = x(d3.min(st)) - pad0, x1 = x(d3.max(st)) + pad0;
      var tint = P[INTENT_TINT[ph.intent] || "rule2"] || P.rule2;
      var g = phaseG.append("g").attr("class", "d3c-phase").attr("data-intent", ph.intent || "");
      g.append("rect").attr("x", x0).attr("y", yPhase - 8).attr("width", Math.max(4, x1 - x0)).attr("height", 16)
        .attr("rx", 3).attr("fill", tint).attr("opacity", 0.16);
      g.append("rect").attr("x", x0).attr("y", yPhase + 6).attr("width", Math.max(4, x1 - x0)).attr("height", 2)
        .attr("fill", tint).attr("opacity", 0.7);
      if (x1 - x0 >= (mode.axis === "time" ? 64 : 44)) {
        g.append("text").attr("class", "d3c-phase-label").attr("x", (x0 + x1) / 2).attr("y", yPhase + 3)
          .attr("text-anchor", "middle").attr("fill", tint).text(truncate(ph.intent || "phase", Math.floor((x1 - x0) / 7)));
      }
      g.append("title").text((ph.intent || "phase") + " — " + (ph.summary || ""));
    });

    // ---- the spent-after-basis region, behind the marks
    if (spent) {
      var sx0 = win.has(basis.basis_complete_at) ? x(basis.basis_complete_at) + slot / 2 : m.l;
      var sx1 = win.has(answerIdx) ? x(answerIdx) - slot / 2 : W - m.r;
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
    svg.append("line").attr("x1", x(win.start)).attr("x2", x(win.end - 1)).attr("y1", yStep).attr("y2", yStep)
      .attr("stroke", P.rule2).attr("stroke-width", 1.4);
    if (win.windowed && win.start > 0) svg.append("text").attr("class", "d3c-cap").attr("x", m.l).attr("y", yStep - 16).attr("text-anchor", "start").text("← " + win.start + " earlier");
    if (win.windowed && win.end < n) svg.append("text").attr("class", "d3c-cap").attr("x", W - m.r).attr("y", yStep - 16).attr("text-anchor", "end").text((n - win.end) + " later →");

    // ---- answer-basis arcs: from the step that first produced a value to the answer
    var arcG = svg.append("g").attr("class", "d3c-arcs");
    var byOrigin = {};
    rests.forEach(function (r) { (byOrigin[r.first_step] = byOrigin[r.first_step] || []).push(r); });
    var labelJobs = [];
    var outside = 0;
    rests.forEach(function (r, i) {
      var group = byOrigin[r.first_step];
      var k = group.indexOf(r);
      if (!win.has(r.first_step) && !win.has(answerIdx)) { outside++; return; }
      var x0 = xe(r.first_step), x1 = xe(answerIdx);
      if (x1 <= x0) { outside++; return; }
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
      // a value that does not match the expected answer is only *wrong*
      // on a run that failed; on a passing run it is an intermediate
      var wrong = failed && r.matches_expected === false;
      if (k < 4 && (x1 - x0) >= 40 && !tiny) {
        // the label sits on the curve's apex (cubic with both controls at yArcTop+depth)
        labelJobs.push({ r: r, wrong: wrong, stroke: stroke, x: (x0 + x1) / 2,
                         y: (yStep + 8) + 0.75 * ((yArcTop + depth) - (yStep + 8)) + 3,
                         label: (win.has(r.first_step) ? "" : "from step " + r.real_step + " · ") +
                                truncate(r.value, Math.max(6, Math.floor((x1 - x0) / 6.5))) });
      }
      path.append("title").text(String(r.value) + " — " + humanKind(r.status) + ", first at step " + r.real_step +
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
    if (rests.length > 4 || outside) {
      arcG.append("text").attr("class", "d3c-cap").attr("x", xe(answerIdx)).attr("y", yArcTop + arcSpan + 4)
        .attr("text-anchor", "end").text(rests.length + " values in the answer, " + Object.keys(byOrigin).length + " origin step(s)" +
          (outside ? " · " + outside + " from steps outside this window" : ""));
    }

    // ---- basis-complete tick
    if (isNum(basis.basis_complete_at) && basis.basis_complete_at < answerIdx && win.has(basis.basis_complete_at)) {
      var bx = x(basis.basis_complete_at);
      var bt = svg.append("g").attr("class", "d3c-basis");
      bt.append("line").attr("x1", bx).attr("x2", bx).attr("y1", yStep - 14).attr("y2", yStep + 14)
        .attr("stroke", P.good).attr("stroke-width", 1.2).attr("stroke-dasharray", "2 2");
      bt.append("title").text("basis complete at step " + basis.basis_complete_at + " — every value the answer rests on existed by here");
    }

    // ---- steps: one focusable mark each
    var marks = svg.append("g").attr("class", "d3c-steps").selectAll("g.d3c-step")
      .data(visible.map(function (i) { return { i: i, step: steps[i], w: byStep[i] || {}, unit: units[i] }; }))
      .join("g")
      .attr("class", function (d) { return "d3c-step" + (d.unit.count > 1 ? " group" : ""); })
      .attr("data-step", function (d) { return d.unit.from; })
      .attr("data-unit", function (d) { return d.i; })
      .attr("data-count", function (d) { return d.unit.count; })
      .attr("data-role", function (d) { return d.w.role || ""; })
      .attr("tabindex", 0).attr("role", "button")
      .attr("aria-label", function (d) {
        return (d.unit.count > 1 ? "steps " + d.unit.from + " to " + d.unit.to + " · " : "step " + d.unit.from + " · ") +
               (d.step.name || d.step.type || "step") + " · " + ((ROLE[d.w.role] || {}).label || d.w.role || "") +
               (d.unit.count > 1 ? " · click to open" : "");
      })
      .attr("transform", function (d) { return "translate(" + x(d.i) + "," + yStep + ")"; });
    marks.each(function (d) {
      var g = d3.select(this);
      // the hit area is the step's own row slot, never taller than the row:
      // a wide slot must not reach up over the toolbar or down over the arcs
      var hw = Math.max(10, Math.min(slot, 120) / 2);
      g.append("rect").attr("class", "d3c-hit").attr("x", -hw).attr("y", -16).attr("width", hw * 2).attr("height", wide ? 52 : 40).attr("fill", "transparent");
      var role = d.w.role || (d.step.type === "answer" ? "answer" : "dead_end");
      if (d.step.error === true) role = "error";
      if (d.unit.count > 1) {
        // a folded run of identical calls: a stacked mark with its count
        g.append("circle").attr("r", tiny ? 4 : 7.5).attr("cx", 2).attr("cy", -2).attr("fill", P.surface).attr("stroke", color).attr("stroke-width", 1.2).attr("opacity", 0.7);
        g.append("circle").attr("r", tiny ? 4 : 7.5).attr("cx", 1).attr("cy", -1).attr("fill", P.surface).attr("stroke", color).attr("stroke-width", 1.2).attr("opacity", 0.85);
      }
      drawMark(g, role, color, tiny ? 3 : 6);
      if (d.unit.count > 1 && !tiny) {
        g.append("text").attr("class", "d3c-cap").attr("x", 0).attr("y", -12).attr("text-anchor", "middle")
          .attr("fill", color).attr("font-weight", 700).text("×" + d.unit.count);
      }
      if (d.w.invented_argument) {
        g.append("text").attr("class", "d3c-cap").attr("x", 0).attr("y", -13).attr("text-anchor", "middle")
          .attr("fill", P.warn).attr("font-weight", 700).text("invented");
      }
      if (!tiny || d.i % Math.ceil(14 / Math.max(1, slot)) === 0) {
        g.append("text").attr("class", "d3c-idx").attr("x", 0).attr("y", wide ? 20 : 19).attr("text-anchor", "middle")
          .text(d.unit.count > 1 ? d.unit.from + "–" + d.unit.to : String(d.unit.from));
      }
      if (wide) {
        var name = truncate(d.step.name || d.step.type || "", Math.floor(slot / 6.2));
        g.append("text").attr("class", "d3c-name").attr("x", 0).attr("y", 31).attr("text-anchor", "middle").text(name);
      }
    });
    if (duration && !win.compressed) {
      marks.attr("opacity", 0).transition().duration(duration * 0.6).delay(function (d) { return 30 * (d.i - win.start); }).attr("opacity", 1);
    }

    // ---- errors and the critical one
    errors.forEach(function (e) {
      if (!e || !isNum(e.step) || e.step >= n || !win.has(e.step)) return;
      var eg = svg.append("g").attr("class", "d3c-error").attr("transform", "translate(" + x(e.step) + "," + (yStep - 16) + ")");
      eg.append("text").attr("text-anchor", "middle").attr("fill", P.bad).attr("font-weight", 800).attr("font-size", 11)
        .text(e.step === critical ? "!!" : "!");
      eg.append("title").text("error at step " + e.step + (e.status ? " — " + humanKind(e.status) : "") + (e.step === critical ? " (critical)" : ""));
    });

    // ---- decisive ring
    if (dec && dec.side === side && dec.step < n && win.has(dec.step)) {
      var ring = svg.append("g").attr("class", "d3c-decisive").attr("transform", "translate(" + x(dec.step) + "," + yStep + ")");
      ring.append("circle").attr("class", "d3c-ring " + (dec.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", tiny ? 7 : 12);
      ring.append("text").attr("class", "d3c-cap").attr("x", 0).attr("y", -17).attr("text-anchor", "middle")
        .attr("fill", P.bad).attr("font-weight", 700).text("decisive");
      ring.append("title").text("decisive step " + (isNum(dec.real_step) ? dec.real_step : dec.step) + " — " + dec.criterion + " (" + dec.verification + ")");
    }

    // ---- interactions
    function actStep(d) {
      if (d.unit.count > 1) { charts.mode.expand(key, d.unit.members, true); return; }
      selectStep(report, side, d.unit.from);
    }
    marks.on("mousemove", function (event, d) {
      var head = d.unit.count > 1 ? "steps " + d.unit.from + "–" + d.unit.to + " · " + (d.step.name || d.step.type || "step")
                                  : "step " + d.unit.from + " · " + (d.step.name || d.step.type || "step");
      var lines = [{ b: true, text: head },
        { text: (d.step.type || "") + " · " + ((ROLE[d.w.role] || {}).label || humanKind(d.w.role)) +
                (d.w.intent ? " · " + d.w.intent : "") +
                (isNum(d.step.tokens) ? " · " + d.step.tokens + " tokens" : "") +
                (isNum(d.step.latency_s) ? " · " + d.step.latency_s.toFixed(2) + "s" : "") }];
      var out = truncate(d.step.output || d.step.input || "", 140);
      if (out) lines.push({ mono: true, text: out });
      if (d.unit.count > 1) lines.push({ text: "click to open these " + d.unit.count + " steps" });
      showTip(event, lines);
    }).on("mouseleave", hideTip)
      .on("click", function (event, d) { hideTip(); actStep(d); })
      .on("keydown", function (event, d) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); actStep(d); }
      });
    arcG.selectAll("path.d3c-arc").on("mousemove", function (event) {
      var r = rests[arcG.selectAll("path.d3c-arc").nodes().indexOf(this)];
      if (!r) return;
      showTip(event, [{ b: true, text: String(r.value) },
        { text: humanKind(r.status) + " · first at step " + r.real_step + (r.source ? " via " + r.source : "") },
        r.matches_expected === false ? { text: "does not match the expected answer" } : null]);
    }).on("mouseleave", hideTip);

    // ---- how it became a failure: one cell per step, its state read from
    // the alignment (same / drifted / only this run), the divergences and
    // the causal account (the fault enters, is carried, is committed)
    if (path) {
      var sg = svg.append("g").attr("class", "d3c-path").attr("transform", "translate(0," + (stripTop + 6) + ")");
      sg.append("text").attr("class", "d3c-cap").attr("x", x(win.start) - slot / 2 + 2).attr("y", -1)
        .attr("fill", failed ? P.bad : P.good).attr("font-weight", 600)
        .text(failed ? "how it became a failure" : "how it stayed on track");
      var cellW = Math.max(2, slot - (tiny ? 1 : 6));
      var cellWidth = function (d) {
        if (mode.axis !== "time") return cellW;
        var next = d.step + 1 < win.end ? x(d.step + 1) : x(d.step) + slot;
        return Math.max(2, Math.min(cellW, next - x(d.step) - 2));
      };
      var cells = sg.selectAll("g.d3c-pcell").data(path.filter(function (d) { return win.has(d.step); })).join("g")
        .attr("class", function (d) { return "d3c-pcell " + d.state; })
        .attr("data-state", function (d) { return d.state; })
        .attr("data-step", function (d) { return d.step; })
        .attr("transform", function (d) { return "translate(" + (x(d.step) - cellWidth(d) / 2) + ",4)"; });
      cells.append("rect").attr("width", cellWidth).attr("height", 12).attr("rx", 2)
        .attr("fill", function (d) { return d.state === "fault" || d.state === "committed" ? P.bad
          : d.state === "diverged" ? P.warn : d.state === "drift" ? P.warn : d.state === "same" ? P.good : P.rule2; })
        .attr("opacity", function (d) { return d.state === "fault" || d.state === "committed" ? 0.9 : d.state === "diverged" ? 0.85 : d.state === "drift" ? 0.45 : d.state === "same" ? 0.45 : 0.6; })
        .attr("stroke", function (d) { return d.error ? P.bad : "none"; }).attr("stroke-width", 1.5)
        .attr("stroke-dasharray", function (d) { return d.state === "alone" ? "3 2" : null; });
      cells.filter(function (d) { return d.state === "fault" && d.enters && !tiny; })
        .append("text").attr("class", "d3c-cap").attr("x", function (d) { return cellWidth(d) / 2; }).attr("y", 25).attr("text-anchor", "middle")
        .attr("fill", P.bad).attr("font-weight", 700).text(wide ? "fault enters" : "enters");
      cells.filter(function (d) { return d.state === "committed" && !tiny; })
        .append("text").attr("class", "d3c-cap").attr("x", function (d) { return cellWidth(d) / 2; }).attr("y", 25).attr("text-anchor", "middle")
        .attr("fill", P.bad).attr("font-weight", 700).text(wide ? "wrong answer" : "wrong");
      cells.filter(function (d) { return d.state === "fault" && !d.enters && wide && slot >= 96 && cellWidth(d) >= 60; })
        .append("text").attr("class", "d3c-cap").attr("x", function (d) { return cellWidth(d) / 2; }).attr("y", 25).attr("text-anchor", "middle")
        .attr("fill", P.bad).text("carried");
      var cellName = function (d) { var u = units[d.step]; return u && u.count > 1 ? "steps " + u.from + "–" + u.to : "step " + real(d.step); };
      cells.append("title").text(function (d) { return cellName(d) + " · " + d.label + (d.detail ? "\n" + d.detail : ""); });
      cells.on("mousemove", function (event, d) {
        showTip(event, [{ b: true, text: cellName(d) + " · " + d.label }, d.detail ? { text: d.detail } : null, { mono: true, text: "source: " + d.source }]);
      }).on("mouseleave", hideTip).on("click", function (event, d) { hideTip(); selectStep(report, side, real(d.step)); });
    }

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
      if (failed && rests.some(function (r) { return r.matches_expected === false; })) {
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
    if (path) {
      var states = {};
      path.forEach(function (d) { states[d.state] = true; });
      var STATE_KEY = { same: [P.good, "same as the other run"], drift: [P.warn, "drifted from the other run"],
        diverged: [P.warn, "diverged (a divergence the report ranks)"], alone: [P.rule2, "only this run took this step"],
        fault: [P.bad, "the fault (causal account)"], committed: [P.bad, "the wrong answer committed"] };
      Object.keys(STATE_KEY).forEach(function (st) {
        if (states[st]) legend.appendChild(legendItem(P, { hatch: true }, STATE_KEY[st][0], STATE_KEY[st][1]));
      });
    }
    host.appendChild(legend);

    // ---- the toolbar: axis, compression, replay
    storyToolbar(host, wrap.node(), svg, key, mode, {
      n: n, realN: realSteps.length, answerIdx: answerIdx, decStep: dec && dec.side === side ? dec.step : null,
      compressible: (function () { var c = compressView(realSteps, what0, rests0, dec0, side, errors0, path0, {}); return c.units.length < realSteps.length; })(),
      win: win, report: report, side: side, real: real, units: units, duration: duration,
    });
    return svg.node();
  }

  /* Replay: the run re-told one unit at a time. Everything the static
   * render drew carries an order (a step, the answer for the arcs, the
   * decisive step for its ring); replay only changes opacity and a class,
   * so the final frame is exactly the static picture. State lives in the
   * mode so a repaint — a window move, a resize — resumes where it was. */
  function storyToolbar(host, wrapEl, svg, key, mode, info) {
    var P = palette();
    var bar = document.createElement("div");
    bar.className = "d3c-toolbar";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "view and replay controls");
    function button(text, title, on, cls) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "d3c-tb" + (cls ? " " + cls : ""); b.textContent = text; b.title = title;
      b.addEventListener("click", on);
      return b;
    }
    // axis
    var axis = document.createElement("span"); axis.className = "d3c-seg"; axis.setAttribute("role", "group"); axis.setAttribute("aria-label", "x axis");
    [["steps", "steps in order"], ["time", "wall-clock time (cumulative latency)"]].forEach(function (a) {
      var b = button(a[0], a[1], function () { charts.mode.set(key, { axis: a[0] }); }, "axis-" + a[0]);
      b.setAttribute("aria-pressed", mode.axis === a[0] ? "true" : "false");
      axis.appendChild(b);
    });
    bar.appendChild(axis);
    // compression
    if (info.compressible || mode.compress) {
      var c = button(mode.compress ? "compressed · " + info.n + " of " + info.realN : "compress repeats",
        "fold runs of identical calls into one unit each (click a unit to open it)",
        function () { charts.mode.set(key, { compress: !mode.compress, expanded: {} }); }, "compress");
      c.setAttribute("aria-pressed", mode.compress ? "true" : "false");
      bar.appendChild(c);
    }
    // replay
    var play = mode.play || (mode.play = { t: null, playing: false, speed: 1, follow: true, timer: 0 });
    var reduced = charts.motion() === 0;
    var pace = Math.max(45, Math.min(650, 7000 / Math.max(1, info.n)));
    var rep = document.createElement("span"); rep.className = "d3c-seg d3c-replay"; rep.setAttribute("role", "group"); rep.setAttribute("aria-label", "replay");
    var playBtn = button(play.playing ? "⏸ pause" : "▶ play how it went", "re-tell the run step by step (space)", function () { toggle(); }, "play");
    var scrub = document.createElement("input");
    scrub.type = "range"; scrub.min = "0"; scrub.max = String(Math.max(0, info.n - 1)); scrub.step = "1";
    scrub.value = String(play.t === null ? info.n - 1 : play.t); scrub.className = "d3c-scrub";
    scrub.setAttribute("aria-label", "replay position");
    var where = document.createElement("span"); where.className = "d3c-where mono";
    var speed = document.createElement("select"); speed.className = "d3c-speed"; speed.setAttribute("aria-label", "replay speed");
    [1, 2, 4, 8].forEach(function (v) { var o = document.createElement("option"); o.value = String(v); o.textContent = "×" + v; if (play.speed === v) o.selected = true; speed.appendChild(o); });
    var followLab = document.createElement("label"); followLab.className = "d3c-follow";
    var follow = document.createElement("input"); follow.type = "checkbox"; follow.checked = !!play.follow;
    followLab.appendChild(follow); followLab.appendChild(document.createTextNode(" inspector follows"));
    var reset = button("⟲", "back to the full picture", function () { stop(); play.t = null; apply(); }, "reset");
    rep.appendChild(playBtn); rep.appendChild(scrub); rep.appendChild(where); rep.appendChild(speed); rep.appendChild(followLab); rep.appendChild(reset);
    bar.appendChild(rep);
    host.insertBefore(bar, wrapEl);

    var svgEl = svg.node();
    function orderOf(el) {
      var v = el.getAttribute("data-unit");
      if (v === null) v = el.getAttribute("data-order");
      if (v === null && el.classList.contains("d3c-pcell")) v = el.getAttribute("data-step");
      return v === null ? null : +v;
    }
    // tag the drawn groups with their order once
    svg.selectAll(".d3c-arcs, .d3c-basis, .d3c-spent").attr("data-order", info.answerIdx);
    svg.selectAll(".d3c-decisive").attr("data-order", info.decStep === null ? 0 : info.decStep);
    svg.selectAll(".d3c-error").each(function () {
      var m = /translate\(([-\d.]+),/.exec(this.getAttribute("transform") || "");
      if (!m) return;
      var units = info.units, best = 0, bx = +m[1];
      for (var i = info.win.start; i < info.win.end; i++) { if (Math.abs(info.win.x(i) - bx) < 0.5) { best = i; break; } }
      this.setAttribute("data-order", best);
    });
    function apply() {
      var t = play.t;
      var nodes = svgEl.querySelectorAll("[data-unit], [data-order], .d3c-pcell");
      for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i], o = orderOf(el);
        if (o === null) continue;
        var shown = t === null || o <= t;
        el.style.opacity = shown ? "" : "0.12";
        el.classList.toggle("d3c-now", t !== null && o === t && play.playing);
        el.classList.toggle("d3c-future", !shown);
      }
      svgEl.classList.toggle("d3c-replaying", t !== null);
      scrub.value = String(t === null ? info.n - 1 : t);
      var unit = t === null ? null : info.units[t];
      where.textContent = t === null ? "" : (unit && unit.count > 1 ? "steps " + unit.from + "–" + unit.to : "step " + (unit ? unit.from : t)) + " of " + info.realN;
      playBtn.textContent = play.playing ? "⏸ pause" : "▶ play how it went";
      playBtn.setAttribute("aria-pressed", play.playing ? "true" : "false");
      if (t !== null && !info.win.has(t) && info.win.windowed) {
        // the playhead left the window: follow it (the repaint resumes replay)
        charts.focus.set(key, t - 2);
      }
    }
    function stop() {
      if (play.timer) { clearTimeout(play.timer); play.timer = 0; }
      play.playing = false;
    }
    function tick() {
      play.timer = 0;
      if (!play.playing || !svgEl.isConnected) return;
      if (play.t === null || play.t >= info.n - 1) { play.playing = false; play.t = info.n - 1; apply(); return; }
      play.t += 1;
      apply();
      if (play.follow && pace / play.speed >= 120) {
        try { selectStep(info.report, info.side, info.real(play.t)); } catch (err) { /* optional */ }
      }
      if (play.t >= info.n - 1) { play.playing = false; apply(); return; }
      play.timer = setTimeout(tick, pace / play.speed);
    }
    function start() {
      if (reduced) { play.t = info.n - 1; play.playing = false; apply(); return; }
      if (play.t === null || play.t >= info.n - 1) play.t = -1;
      play.playing = true;
      apply();
      play.timer = setTimeout(tick, 60);
    }
    function toggle() { if (play.playing) { stop(); apply(); } else start(); }
    scrub.addEventListener("input", function () { stop(); play.t = +scrub.value; apply(); });
    speed.addEventListener("change", function () { play.speed = +speed.value; });
    follow.addEventListener("change", function () { play.follow = follow.checked; });
    svg.on("keydown.replay", function (event) {
      if (event.key === " " && event.target === svgEl) { event.preventDefault(); toggle(); }
    });
    // resume after a repaint (window move, resize) without losing the playhead
    if (play.timer) { clearTimeout(play.timer); play.timer = 0; }
    apply();
    if (play.playing) play.timer = setTimeout(tick, pace / play.speed);
    charts._play = charts._play || {};
    charts._play[key] = { start: start, stop: stop, toggle: toggle, state: play };
  }

  /* One state per step of `side`, read from the report: the causal account
   * (fault carried), the ranked divergences, the alignment op against the
   * other run, and the outcome. Null when the report aligns nothing. */
  function failureStates(report, side, n) {
    var rows = report && Array.isArray(report.alignment) ? report.alignment : [];
    if (!rows.length || !n) return null;
    var other = side === "a" ? "b" : "a";
    var diag = report.diagnosis || {};
    var account = {};
    if (diag.subject === side) {
      (Array.isArray(diag.causal_account) ? diag.causal_account : []).forEach(function (l) {
        if (l && isNum(l.step)) account[l.step] = l;
      });
    }
    var attr = report.attribution || {};
    var chain = {};
    if (attr.failed_agent === side && Array.isArray(attr.chain)) attr.chain.forEach(function (i) { if (isNum(i)) chain[i] = true; });
    var diverged = {};
    (Array.isArray(report.divergences) ? report.divergences : []).forEach(function (d) {
      if (d && isNum(d[side + "_index"])) diverged[d[side + "_index"]] = d;
    });
    var dec = decisiveOf(report);
    var failed = !(report[side] && report[side].outcome && report[side].outcome.success === true);
    var errors = {};
    var reading = readingOf(report, side);
    ((reading && reading.errors) || []).forEach(function (e) { if (e && isNum(e.step)) errors[e.step] = true; });
    var byStep = {};
    rows.forEach(function (row) { if (row && isNum(row[side + "_index"])) byStep[row[side + "_index"]] = row; });
    var out = [];
    for (var i = 0; i < n; i++) {
      var row = byStep[i];
      var op = row ? String(row.op || "") : "";
      var alone = !row || row[other + "_index"] === null || row[other + "_index"] === undefined;
      var d = { step: i, error: !!errors[i], enters: false };
      var isAnswer = report[side].steps[i] && report[side].steps[i].type === "answer";
      if (account[i] || chain[i]) {
        d.state = failed && isAnswer ? "committed" : "fault";
        d.enters = dec && dec.side === side && dec.step === i;
        d.label = account[i] ? account[i].happened : "on the attribution chain";
        d.detail = account[i] && account[i].mechanism ? account[i].mechanism : (attr.category ? "attributed: " + String(attr.category).replace(/_/g, " ") : "");
        d.source = account[i] ? "diagnosis.causal_account" : "attribution.chain";
      } else if (diverged[i]) {
        d.state = "diverged";
        d.label = "diverged: " + String(diverged[i].kind || "").replace(/_/g, " ");
        d.detail = diverged[i].summary || "";
        d.source = "divergences";
      } else if (alone) {
        d.state = "alone";
        d.label = "only this run took this step";
        d.detail = "";
        d.source = "alignment (" + (op || "unpaired") + ")";
      } else if (op === "match") {
        d.state = "same";
        d.label = "same as " + agentName(report, other) + " step " + row[other + "_index"];
        d.detail = isNum(row.similarity) ? "similarity " + row.similarity.toFixed(2) : "";
        d.source = "alignment (match)";
      } else {
        d.state = "drift";
        d.label = "drifted from " + agentName(report, other) + " step " + row[other + "_index"];
        d.detail = isNum(row.similarity) ? "similarity " + row.similarity.toFixed(2) : "";
        d.source = "alignment (" + (op || "drift") + ")";
      }
      out.push(d);
    }
    return out;
  }

  // ============================================================ 2. why

  charts.why = function (host, ctx) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    var diag = report && report.diagnosis;
    var hyps = diag && Array.isArray(diag.hypotheses) ? diag.hypotheses : [];
    if (!hyps.length) return null;
    return responsive(host, function () { drawWhy(host, ctx, report, diag, hyps); });
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
      // a long run keeps the line and the window; per-step dots would be a smear
      var dotEvery = sx.step() >= 6 ? 1 : Math.ceil(6 / Math.max(0.1, sx.step()));
      steps.forEach(function (s, i) {
        if (i % dotEvery) return;
        svg.append("circle").attr("cx", sx(i)).attr("cy", sy).attr("r", 2.5).attr("fill", P.rule2);
        if (sx.step() >= 18) svg.append("text").attr("class", "d3c-idx").attr("x", sx(i)).attr("y", sy + 14).attr("text-anchor", "middle").text(String(i));
      });
      if (sx.step() < 18) {
        [0, steps.length - 1].forEach(function (i) {
          svg.append("text").attr("class", "d3c-idx").attr("x", sx(i)).attr("y", sy + 14).attr("text-anchor", i ? "end" : "start").text(String(i));
        });
      }
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
    return responsive(host, function () { drawForward(host, ctx, side, report, steps, items); }, focusKey(report, side));
  };

  function drawForward(host, ctx, side, report, steps, items) {
    var P = palette();
    var color = sideColor(side);
    var duration = charts.motion();
    var dec = decisiveOf(report);
    var n = steps.length;

    var W = width(host, ctx);
    var firstAt = items.map(function (t) { return t.at_step; }).filter(isNum)[0];
    var win = focusWindow(focusKey(report, side), n, W, 14, 14, dec && dec.side === side ? dec.step : firstAt, 44);
    var x = win.x;
    var slot = x.step();
    var tiny = slot < 14;
    // pins at the same step stack upward
    var lanes = {};
    var pinned = items.map(function (t, i) {
      var at = isNum(t.at_step) && t.at_step < n ? t.at_step : null;
      var lane = at === null ? 0 : (lanes[at] = (lanes[at] || 0) + 1) - 1;
      return { i: i, n: i + 1, t: t, at: at, lane: lane, shown: at !== null && win.has(at) };
    });
    var maxLane = d3.max(pinned, function (p) { return p.lane; }) || 0;
    var pinTop = 14 + (win.windowed ? 34 : 0), pinGap = 24;
    var ySpine = pinTop + (maxLane + 1) * pinGap + 8;
    var H = ySpine + 24;

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap");
    var svg = wrap.append("svg").attr("class", "d3c d3c-forward")
      .attr("width", W).attr("height", H).attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img").attr("aria-label", items.length + " next action(s) for " + agentName(report, side) + ", pinned to the steps they apply to");
    svg.append("title").text("Take forward: where to intervene in " + agentName(report, side));

    bindWindowKeys(svg, win);
    if (win.windowed) {
      overviewStrip(svg, 6, W, 14, 14, win, null,
        pinned.filter(function (p) { return p.at !== null; }).map(function (p) { return { step: p.at, color: dec && dec.side === side && dec.step === p.at ? P.bad : color }; }),
        items.length + " action(s)");
    }
    svg.append("line").attr("x1", x(win.start)).attr("x2", x(win.end - 1)).attr("y1", ySpine).attr("y2", ySpine).attr("stroke", P.rule2).attr("stroke-width", 1.4);
    d3.range(win.start, win.end).forEach(function (i) {
      var s = steps[i];
      var g = svg.append("g").attr("class", "d3c-ghost").attr("transform", "translate(" + x(i) + "," + ySpine + ")");
      var isDec = dec && dec.side === side && dec.step === i;
      g.append("circle").attr("r", isDec ? 4 : tiny ? 1.5 : 3).attr("fill", isDec ? P.bad : P.surface).attr("stroke", isDec ? P.bad : P.rule2).attr("stroke-width", 1.4);
      if (slot >= 18) g.append("text").attr("class", "d3c-idx").attr("y", 16).attr("text-anchor", "middle").text(String(i));
      g.append("title").text("step " + i + " · " + (s.name || s.type || "") + (isDec ? " · decisive" : ""));
    });

    var pins = svg.append("g").selectAll("g.d3c-pin").data(pinned.filter(function (p) { return p.shown; })).join("g")
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
        var stepLink = document.createElement("span"); stepLink.className = "step" + (p.shown ? "" : " outside");
        stepLink.textContent = "at step " + p.at + (p.shown ? " " : " (outside the window — jump) ");
        stepLink.setAttribute("role", "button"); stepLink.tabIndex = 0;
        var jump = function () {
          if (!p.shown && win.windowed) charts.focus.set(win.key, p.at - Math.floor(win.cap / 2));
          selectStep(report, side, p.at);
        };
        stepLink.addEventListener("click", jump);
        stepLink.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump(); } });
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
        var replays = String(t.replay_recipe.replays || "");
        rr.textContent = "replay " + (replays.split(" — ")[0] || "").trim();
        rr.title = (t.replay_recipe.expects ? "expects " + t.replay_recipe.expects + ". " : "") +
          (replays ? replays + ". " : "") + String(t.replay_recipe.correction || "");
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

  // ================================================== 4. the trace as a tree

  /* The pair as one tree: the task at the root, each run a branch, each
   * run's phases, each phase's steps, and under a step the answer values
   * it first produced. Links along the fault's path (the attribution
   * chain on the failed run) are drawn in red; the decisive step keeps
   * its ring. Phases collapse and expand (click or Enter); a step moves
   * the shared cursor; a value selects the step that produced it.
   * Nothing is derived: the hierarchy is the reading's phases, the roles
   * are the reading's roles, the values are `rests_on` as written. */
  var TreeState = {};   // per task: which node keys are collapsed

  function treeData(report, pages, dec) {
    var task = report && report.task ? report.task : {};
    pages = pages || {};
    var runs = ["a", "b"].map(function (side) {
      var reading = readingOf(report, side);
      var steps = stepsOf(report, side);
      var outcome = report && report[side] && report[side].outcome ? report[side].outcome : {};
      var what = {};
      ((reading && reading.what_happened) || []).forEach(function (w) { if (w && isNum(w.step)) what[w.step] = w; });
      var rests = (reading && Array.isArray(reading.rests_on) ? reading.rests_on : []).filter(function (r) { return r && isNum(r.first_step); });
      var phases = reading && Array.isArray(reading.phases) && reading.phases.length
        ? reading.phases
        : [{ intent: "steps", steps: steps.map(function (s, i) { return i; }), summary: steps.length + " steps" }];
      var stepNode = function (i) {
        var st = steps[i];
        if (!st) return null;
        var w = what[i] || {};
        var values = rests.filter(function (r) { return r.first_step === i; }).map(function (r) {
          return { kind: "value", key: side + ":v:" + i + ":" + String(r.value), side: side, step: i,
                   value: String(r.value), status: r.status || "", wrong: outcome.success !== true && r.matches_expected === false,
                   right: r.matches_expected === true, source: r.source || "" };
        });
        return { kind: "step", key: side + ":s:" + i, side: side, index: i, name: st.name || st.type || "step",
                 type: st.type || "", role: w.role || (st.type === "answer" ? "answer" : "dead_end"),
                 error: st.error === true, quality: st.quality || null, invented: !!w.invented_argument,
                 excerpt: truncate(st.output || st.input || "", 140), children: values.length ? values : null };
      };
      return {
        kind: "run", key: side + ":run", side: side, name: agentName(report, side),
        success: outcome.success === true, steps: steps.length,
        children: phases.map(function (ph, pi) {
          var idx = (ph.steps || []).filter(function (i) { return isNum(i) && i < steps.length; });
          var key = side + ":p:" + pi;
          var kids;
          if (idx.length > TREE_PAGE) {
            // a long phase shows one page at a time: the page around the
            // decisive step when it holds one, else the first; "more"
            // nodes at either end page it
            var page = pages[key];
            if (!page) {
              var at = dec && dec.side === side ? idx.indexOf(dec.step) : -1;
              var from = at >= 0 ? Math.max(0, at - Math.floor(TREE_PAGE / 2)) : 0;
              page = pages[key] = { from: from, to: Math.min(idx.length, from + TREE_PAGE) };
            }
            kids = idx.slice(page.from, page.to).map(stepNode).filter(Boolean);
            if (page.from > 0) kids.unshift({ kind: "more", key: key + ":before", side: side, phase: key, dir: -1, count: page.from });
            if (page.to < idx.length) kids.push({ kind: "more", key: key + ":after", side: side, phase: key, dir: 1, count: idx.length - page.to });
          } else {
            kids = idx.map(stepNode).filter(Boolean);
          }
          return { kind: "phase", key: key, side: side, intent: ph.intent || "phase",
                   summary: ph.summary || "", count: idx.length, children: kids };
        }),
      };
    });
    return { kind: "task", key: "task", name: task.id || "task", prompt: task.prompt || "", children: runs };
  }

  function faultPath(report) {
    // the steps the report says the fault travelled through, by side
    var out = { a: {}, b: {} };
    var attr = report && report.attribution;
    if (attr && (attr.failed_agent === "a" || attr.failed_agent === "b") && Array.isArray(attr.chain)) {
      attr.chain.forEach(function (i) { if (isNum(i)) out[attr.failed_agent][i] = true; });
    }
    var diag = report && report.diagnosis;
    if (diag && (diag.subject === "a" || diag.subject === "b") && Array.isArray(diag.causal_account)) {
      diag.causal_account.forEach(function (link) { if (link && isNum(link.step)) out[diag.subject][link.step] = true; });
    }
    return out;
  }

  charts.tree = function (host, ctx) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    if (!report || (!stepsOf(report, "a").length && !stepsOf(report, "b").length)) return null;
    return responsive(host, function () { drawTree(host, ctx, report); }, "tree:" + (report.task && report.task.id));
  };
  var TREE_PAGE = 20;   // steps a phase shows at a time; "more" nodes page it

  function drawTree(host, ctx, report) {
    var P = palette();
    var duration = charts.motion();
    var dec = decisiveOf(report);
    var fault = faultPath(report);
    var taskKey = report.task && report.task.id ? report.task.id : "task";
    var tstate = TreeState[taskKey] = TreeState[taskKey] || { collapsed: {}, pages: {} };
    var collapsed = tstate.collapsed, pages = tstate.pages;
    var data = treeData(report, pages, dec);

    var avail = width(host, ctx);
    // five columns need room; below ~640px the tree keeps its width and
    // scrolls inside its own box rather than shrinking labels to nothing
    var W = Math.max(avail, 640);
    var rowH = 22;
    var colW = Math.max(112, Math.min(230, Math.floor((W - 30) / 4.4)));
    var left = 12, pad = 16;
    var wide = colW >= 150;

    var root = d3.hierarchy(data);
    root.descendants().forEach(function (d) {
      d.id = d.data.key;
      d._children = d.children;
      var big = d.data.kind === "phase" && d.data.count > 8;
      var holdsDecisive = dec && d.data.kind === "phase" && d.data.side === dec.side &&
        (d._children || []).some(function (c) { return c.data.index === dec.step; });
      if (collapsed[d.id] === undefined) collapsed[d.id] = big && !holdsDecisive;
      if (collapsed[d.id] && d._children) d.children = null;
    });
    root.x0 = 0; root.y0 = 0;

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap" + (W > avail ? " d3c-scroll" : ""));
    var svg = wrap.append("svg").attr("class", "d3c d3c-tree").attr("width", W)
      .attr("role", "img").attr("aria-label", "The task as a tree: each run a branch, its phases, their steps, and the answer values each step produced");
    svg.append("title").text("The trace as a tree");
    var g = svg.append("g");
    var linkG = g.append("g").attr("class", "d3c-tree-links");
    var nodeG = g.append("g").attr("class", "d3c-tree-nodes");
    var layout = d3.tree().nodeSize([rowH, colW]);
    var linkPath = d3.linkHorizontal().x(function (d) { return d.y; }).y(function (d) { return d.x; });

    function labelFor(d) {
      var k = d.data;
      if (k.kind === "task") return truncate(k.name, Math.floor((colW - 24) / 6.4));
      if (k.kind === "run") return truncate(k.name, Math.floor((colW - 40) / 6.4));
      if (k.kind === "phase") return truncate(k.intent, Math.floor((colW - 60) / 6.4));
      if (k.kind === "step") return truncate(k.index + " · " + k.name, Math.floor((colW - 30) / 6.4));
      if (k.kind === "more") return (k.dir < 0 ? "▲ " : "▼ ") + k.count + " more";
      return truncate(k.value, Math.max(6, Math.floor((W - d.y - left - 44) / 6.6)));
    }
    function pageMore(k) {
      var page = pages[k.phase];
      if (!page) return;
      if (k.dir < 0) page.from = Math.max(0, page.from - TREE_PAGE);
      else page.to = page.to + TREE_PAGE;
      repaint("tree:" + taskKey);
    }
    function isFault(d) { return d.data.kind === "step" && fault[d.data.side] && fault[d.data.side][d.data.index]; }
    function linkStroke(l) {
      var t = l.target.data;
      if (t.kind === "step" && isFault(l.target) && isFault(l.source) === undefined) { /* phase → step */ }
      if (t.kind === "step" && fault[t.side] && fault[t.side][t.index]) return P.bad;
      if (t.kind === "value") return P[STATUS_COLOR[t.status] || "muted"];
      if (t.kind === "run") return sideColor(t.side);
      return P.rule2;
    }

    function toggle(d) {
      if (!d._children) return;
      collapsed[d.id] = !collapsed[d.id];
      d.children = collapsed[d.id] ? null : d._children;
      update(d);
    }

    function update(source) {
      layout(root);
      var nodes = root.descendants(), links = root.links();
      var minX = d3.min(nodes, function (d) { return d.x; }), maxX = d3.max(nodes, function (d) { return d.x; });
      var H = Math.max(60, maxX - minX + pad * 2 + 8);
      svg.transition().duration(duration).attr("height", H).attr("viewBox", "0 0 " + W + " " + H);
      g.transition().duration(duration).attr("transform", "translate(" + left + "," + (pad - minX) + ")");

      var node = nodeG.selectAll("g.d3c-tnode").data(nodes, function (d) { return d.id; });
      var enter = node.enter().append("g")
        .attr("class", function (d) { return "d3c-tnode kind-" + d.data.kind + (d._children ? " branch" : ""); })
        .attr("data-kind", function (d) { return d.data.kind; })
        .attr("data-side", function (d) { return d.data.side || ""; })
        .attr("data-step", function (d) { return d.data.kind === "step" ? d.data.index : null; })
        .attr("data-key", function (d) { return d.id; })
        .attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", function (d) {
          var k = d.data;
          if (k.kind === "task") return "task " + k.name;
          if (k.kind === "more") return "show " + k.count + " more step" + (k.count === 1 ? "" : "s") + (k.dir < 0 ? " before" : " after");
          if (k.kind === "run") return k.name + (k.success ? " solved" : " failed") + ", " + k.steps + " steps";
          if (k.kind === "phase") return "phase " + k.intent + ", " + k.count + " steps";
          if (k.kind === "step") return "step " + k.index + " " + k.name + " · " + ((ROLE[k.role] || {}).label || k.role);
          return "value " + k.value + " · " + humanKind(k.status);
        })
        .attr("transform", function () { return "translate(" + source.y0 + "," + source.x0 + ")"; })
        .attr("opacity", 0);
      enter.append("circle").attr("r", 9).attr("fill", "transparent");   // hit area
      enter.each(function (d) {
        var gg = d3.select(this), k = d.data;
        if (k.kind === "task") {
          gg.append("circle").attr("r", 5).attr("fill", P.ink);
        } else if (k.kind === "run") {
          gg.append("circle").attr("r", 6).attr("fill", sideColor(k.side));
          gg.append("text").attr("class", "d3c-cap").attr("x", 11).attr("y", 4).attr("fill", k.success ? P.good : P.bad)
            .attr("font-weight", 700).text(k.success ? "✓" : "✗");
        } else if (k.kind === "phase") {
          var tint = P[INTENT_TINT[k.intent] || "rule2"] || P.rule2;
          gg.append("rect").attr("x", -6).attr("y", -6).attr("width", 12).attr("height", 12).attr("rx", 3)
            .attr("fill", tint).attr("opacity", 0.85);
        } else if (k.kind === "more") {
          gg.append("circle").attr("r", 5).attr("fill", P.surface).attr("stroke", P.ink2).attr("stroke-width", 1.4).attr("stroke-dasharray", "2 2");
        } else if (k.kind === "step") {
          drawMark(gg, k.error ? "error" : k.role, sideColor(k.side), 5);
          if (dec && dec.side === k.side && dec.step === k.index) {
            gg.append("circle").attr("class", "d3c-ring " + (dec.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", 10);
          }
        } else {
          gg.append("rect").attr("x", -4).attr("y", -4).attr("width", 8).attr("height", 8).attr("rx", 1.5)
            .attr("fill", P[STATUS_COLOR[k.status] || "muted"]);
        }
        var tx = k.kind === "run" ? 22 : k.kind === "step" && dec && dec.side === k.side && dec.step === k.index ? 15 : 12;
        var label = gg.append("text").attr("class", "d3c-tlabel" + (k.kind === "value" ? " mono" : ""))
          .attr("x", tx).attr("y", 4).attr("font-size", k.kind === "value" ? 10.5 : 11.5)
          .attr("fill", k.kind === "value" ? (k.wrong ? P.bad : P[STATUS_COLOR[k.status] || "muted"]) :
                        k.kind === "phase" || k.kind === "more" ? P.ink2 : P.ink)
          .attr("font-weight", k.kind === "run" || k.kind === "task" ? 650 : k.kind === "phase" ? 600 : 400)
          .text(labelFor(d) + (k.kind === "value" && k.wrong ? " ✗" : k.kind === "value" && k.right ? " ✓" : ""));
        // a branch's label sits over its outgoing links: back it so the
        // links never run through the words
        if (k.kind !== "value") backed(gg, label, P.surface, 2);
        if (k.kind === "phase") {
          gg.append("text").attr("class", "d3c-cap d3c-tcount").attr("y", 4)
            .attr("x", tx + (label.node().getComputedTextLength ? label.node().getComputedTextLength() : 40) + 6)
            .text("");
        }
        if (k.kind === "step" && k.quality && k.quality !== "good" && wide) {
          gg.append("text").attr("class", "d3c-cap").attr("y", 4)
            .attr("x", tx + (label.node().getComputedTextLength ? label.node().getComputedTextLength() : 40) + 6)
            .attr("fill", k.quality === "bad" ? P.bad : P.warn).text(k.quality);
        }
        gg.selectAll("text.d3c-tcount").each(function () { backed(gg, d3.select(this), P.surface, 2); });
        var title = k.kind === "phase" ? k.intent + " — " + k.summary
          : k.kind === "step" ? "step " + k.index + " · " + k.name + " · " + ((ROLE[k.role] || {}).label || k.role) + "\n" + k.excerpt
          : k.kind === "value" ? k.value + " — " + humanKind(k.status) + (k.source ? " via " + k.source : "") + (k.wrong ? " · does not match the expected answer" : "")
          : k.kind === "run" ? k.name + " — " + (k.success ? "solved" : "failed") + " in " + k.steps + " steps"
          : k.name + (k.prompt ? " — " + truncate(k.prompt, 160) : "");
        gg.append("title").text(title);
      });

      var merged = enter.merge(node);
      merged.classed("collapsed", function (d) { return !!(d._children && !d.children); });
      merged.select("text.d3c-tcount").text(function (d) {
        return (d._children && !d.children ? "▸ " : "") + d.data.count + " step" + (d.data.count === 1 ? "" : "s");
      });
      merged.transition().duration(duration)
        .attr("transform", function (d) { return "translate(" + d.y + "," + d.x + ")"; }).attr("opacity", 1);
      node.exit().transition().duration(duration)
        .attr("transform", function () { return "translate(" + source.y + "," + source.x + ")"; }).attr("opacity", 0).remove();

      var link = linkG.selectAll("path.d3c-tlink").data(links, function (l) { return l.target.id; });
      var linkEnter = link.enter().insert("path", "g").attr("class", "d3c-tlink")
        .attr("fill", "none")
        .attr("d", function () { var o = { x: source.x0, y: source.y0 }; return linkPath({ source: o, target: o }); });
      linkEnter.merge(link)
        .attr("class", function (l) { return "d3c-tlink" + (l.target.data.kind === "step" && fault[l.target.data.side] && fault[l.target.data.side][l.target.data.index] ? " fault" : ""); })
        .attr("stroke", linkStroke)
        .attr("stroke-width", function (l) { return l.target.data.kind === "step" && fault[l.target.data.side] && fault[l.target.data.side][l.target.data.index] ? 2 : 1.2; })
        .attr("stroke-dasharray", function (l) { return l.target.data.kind === "step" && (l.target.data.role === "dead_end" || l.target.data.role === "no_information" || l.target.data.role === "repeat") ? "3 3" : null; })
        .attr("opacity", 0.85)
        .transition().duration(duration).attr("d", linkPath);
      link.exit().transition().duration(duration)
        .attr("d", function () { var o = { x: source.x, y: source.y }; return linkPath({ source: o, target: o }); }).remove();

      nodes.forEach(function (d) { d.x0 = d.x; d.y0 = d.y; });

      // interactions, rebound on every update so new nodes get them
      function act(d) {
        if (d.data.kind === "step") selectStep(report, d.data.side, d.data.index);
        else if (d.data.kind === "value") selectStep(report, d.data.side, d.data.step);
        else if (d.data.kind === "more") pageMore(d.data);
        else toggle(d);
      }
      merged.on("click", function (event, d) { hideTip(); act(d); })
        .on("keydown", function (event, d) {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          act(d);
        }).on("mousemove", function (event, d) {
        var k = d.data;
        if (k.kind === "step") showTip(event, [{ b: true, text: "step " + k.index + " · " + k.name }, { text: k.type + " · " + ((ROLE[k.role] || {}).label || k.role) }, k.excerpt ? { mono: true, text: k.excerpt } : null]);
        else if (k.kind === "phase") showTip(event, [{ b: true, text: k.intent }, { text: k.summary }, { text: d.children ? "click to collapse" : "click to expand" }]);
        else if (k.kind === "value") showTip(event, [{ b: true, text: k.value }, { text: humanKind(k.status) + (k.source ? " via " + k.source : "") + " · first at step " + k.step }, k.wrong ? { text: "does not match the expected answer" } : null]);
        else if (k.kind === "run") showTip(event, [{ b: true, text: k.name }, { text: (k.success ? "solved" : "failed") + " in " + k.steps + " steps" }]);
        else if (k.kind === "more") showTip(event, [{ b: true, text: k.count + " more step" + (k.count === 1 ? "" : "s") }, { text: "click to show the next " + Math.min(TREE_PAGE, k.count) }]);
        else hideTip();
      }).on("mouseleave", hideTip);
    }
    update(root);

    var legend = document.createElement("div");
    legend.className = "d3c-legend";
    legend.appendChild(legendItem(P, { role: "feeds_answer" }, P.a, agentName(report, "a")));
    legend.appendChild(legendItem(P, { role: "feeds_answer" }, P.b, agentName(report, "b")));
    legend.appendChild(legendItem(P, { hatch: true }, P.rule2, "phase (click to fold)"));
    legend.appendChild(legendItem(P, { line: true }, P.bad, "the fault's path"));
    legend.appendChild(legendItem(P, { line: true, dashed: true }, P.rule2, "dead end / repeat"));
    legend.appendChild(legendItem(P, { hatch: true }, P.good, "value in the answer, by basis status"));
    if (dec) legend.appendChild(legendItem(P, { ring: true, dashed: dec.verification !== "replay-verified" }, P.bad, "decisive step"));
    host.appendChild(legend);
    return svg.node();
  }

  // ========================================================= 5. reconcile

  /* The reconciling strategy: the report's counterfactual splice drawn as
   * three lanes — the passing run, the failing run, and the reconciled
   * trajectory that keeps the failing run's prefix, takes the passing
   * run's decision at the decisive step, and follows the passing run from
   * there — with the cut marked and the estimate stated as an estimate.
   * Below it, the strategy as steps a person can run: keep, correct,
   * follow, replay, and what to expect. Everything quoted from
   * `counterfactual`, `diagnosis.decisive_step.replay_recipe` and the
   * outcomes; nothing is estimated here. */
  charts.reconcile = function (host, ctx) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    var plan = reconcilePlan(report);
    if (!plan) return null;
    return responsive(host, function () { drawReconcile(host, ctx, report, plan); },
                      (report.task && report.task.id ? report.task.id : "task") + ":reconcile");
  };

  function reconcilePlan(report) {
    if (!report) return null;
    var cf = report.counterfactual && typeof report.counterfactual === "object" ? report.counterfactual : null;
    var dec = decisiveOf(report);
    var diag = report.diagnosis || {};
    var recipe = diag.decisive_step && diag.decisive_step.replay_recipe && typeof diag.decisive_step.replay_recipe === "object"
      ? diag.decisive_step.replay_recipe : null;
    var splice = cf && cf.splice && typeof cf.splice === "object" ? cf.splice : null;
    if (!splice && !recipe) return null;
    var failing = splice && (splice.adopted_from === "a" || splice.adopted_from === "b")
      ? (splice.adopted_from === "a" ? "b" : "a")
      : (dec && dec.side) || (recipe && (recipe.side === "a" || recipe.side === "b") ? recipe.side : null);
    if (!failing) return null;
    var passing = failing === "a" ? "b" : "a";
    var prefix = splice && Array.isArray(splice.prefix_steps) ? splice.prefix_steps.filter(isNum) : [];
    var adopted = splice && Array.isArray(splice.adopted_steps) ? splice.adopted_steps.filter(isNum) : [];
    var cut = dec && dec.side === failing ? dec.step : (recipe && isNum(recipe.step) ? recipe.step : null);
    return { cf: cf, splice: splice, recipe: recipe, failing: failing, passing: passing, prefix: prefix, adopted: adopted,
             cut: cut, estimate: cf && cf.estimate && typeof cf.estimate === "object" ? cf.estimate : null,
             confidence: cf ? cf.confidence : null, premise: cf ? cf.premise : null, narrative: cf ? cf.narrative : null,
             verification: dec ? dec.verification : null };
  }

  function drawReconcile(host, ctx, report, plan) {
    var P = palette();
    var duration = charts.motion();
    var stepsP = stepsOf(report, plan.passing), stepsF = stepsOf(report, plan.failing);
    var readP = readingOf(report, plan.passing), readF = readingOf(report, plan.failing);
    var roleOf = function (reading, i) {
      var w = ((reading && reading.what_happened) || []).filter(function (x) { return x && x.step === i; })[0];
      return w ? w.role : null;
    };
    // the reconciled lane: the failing run's prefix, then the passing run's adopted steps
    var merged = plan.prefix.map(function (i) { return { from: plan.failing, step: i }; })
      .concat(plan.adopted.map(function (i) { return { from: plan.passing, step: i }; }));
    var hasSplice = merged.length > 0;
    var n = Math.max(stepsP.length, stepsF.length, merged.length, 1);

    var W = width(host, ctx);
    var laneLabelW = Math.min(120, Math.round(W * 0.2));
    var win = focusWindow((report.task && report.task.id ? report.task.id : "task") + ":reconcile", n, W, laneLabelW + 14, 70,
                          isNum(plan.cut) ? plan.prefix.length : null, 36);
    var x = win.x;
    var slot = x.step();
    var top = win.windowed ? 34 : 0;
    var lanes = [
      { key: "passing", side: plan.passing, name: agentName(report, plan.passing), steps: stepsP, reading: readP,
        outcome: report[plan.passing].outcome, y: 24 + top },
      { key: "reconciled", side: null, name: "reconciled", steps: merged, y: hasSplice ? 74 + top : null },
      { key: "failing", side: plan.failing, name: agentName(report, plan.failing), steps: stepsF, reading: readF,
        outcome: report[plan.failing].outcome, y: hasSplice ? 124 + top : 74 + top },
    ].filter(function (l) { return l.y !== null; });
    var H = (hasSplice ? 150 : 100) + 6 + top;

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap");
    var svg = wrap.append("svg").attr("class", "d3c d3c-reconcile").attr("width", W).attr("height", H)
      .attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "Reconciling strategy: the failing run's prefix, the passing run's decision at the cut, the passing run's steps after it");
    svg.append("title").text("Reconcile: the splice that the report estimates would flip the outcome");

    bindWindowKeys(svg, win);
    if (win.windowed) {
      overviewStrip(svg, 6, W, laneLabelW + 14, 70, win, null,
        isNum(plan.cut) ? [{ step: plan.prefix.length, color: P.bad }] : [], "positions along the lanes");
    }
    // cut line first, beneath everything
    if (hasSplice && isNum(plan.cut) && win.has(plan.prefix.length)) {
      var cutPos = plan.prefix.length;   // the reconciled lane's position where the adopted steps begin
      var cx = x(Math.min(cutPos, n - 1)) - slot / 2;
      svg.append("line").attr("x1", cx).attr("x2", cx).attr("y1", 8).attr("y2", H - 10)
        .attr("stroke", P.bad).attr("stroke-width", 1.2).attr("stroke-dasharray", "4 3").attr("class", "d3c-cut");
      svg.append("text").attr("class", "d3c-cap").attr("x", cx + 4).attr("y", H - 2).attr("fill", P.bad).attr("font-weight", 700)
        .text("cut · " + agentName(report, plan.failing) + " step " + plan.cut + (plan.verification ? " (" + plan.verification + ")" : ""));
    }

    lanes.forEach(function (lane) {
      var g = svg.append("g").attr("class", "d3c-lane d3c-lane-" + lane.key).attr("data-lane", lane.key);
      var tint = lane.side ? sideColor(lane.side) : P.ink2;
      g.append("text").attr("x", laneLabelW).attr("y", lane.y + 4).attr("text-anchor", "end").attr("font-size", 11.5)
        .attr("font-weight", 650).attr("fill", tint).text(truncate(lane.name, Math.floor(laneLabelW / 6.6)));
      var laneEnd = Math.min(lane.steps.length, win.end) - 1;
      if (laneEnd >= win.start) {
        g.append("line").attr("x1", x(win.start)).attr("x2", x(laneEnd)).attr("y1", lane.y).attr("y2", lane.y)
          .attr("stroke", P.rule2).attr("stroke-width", 1.2);
      }
      var marks = g.selectAll("g.d3c-rmark").data(lane.steps.map(function (s, i) {
        var from = lane.key === "reconciled" ? s.from : lane.side;
        var index = lane.key === "reconciled" ? s.step : i;
        var src = stepsOf(report, from)[index] || {};
        var reading = readingOf(report, from);
        return { pos: i, from: from, index: index, step: src, role: roleOf(reading, index) || (src.type === "answer" ? "answer" : "dead_end") };
      }).filter(function (d) { return win.has(d.pos); })).join("g").attr("class", "d3c-rmark").attr("data-from", function (d) { return d.from; })
        .attr("data-index", function (d) { return d.index; }).attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", function (d) { return agentName(report, d.from) + " step " + d.index + " · " + (d.step.name || d.step.type || ""); })
        .attr("transform", function (d) { return "translate(" + x(d.pos) + "," + lane.y + ")"; });
      marks.each(function (d) {
        var gg = d3.select(this);
        var hw = Math.max(9, Math.min(slot, 60) / 2);
        gg.append("rect").attr("class", "d3c-hit").attr("x", -hw).attr("y", -12).attr("width", hw * 2).attr("height", 30).attr("fill", "transparent");
        drawMark(gg, d.role, sideColor(d.from), 5);
        if (lane.key !== "reconciled" && isNum(plan.cut) && d.from === plan.failing && d.index === plan.cut) {
          gg.append("circle").attr("class", "d3c-ring " + (plan.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", 9.5);
        }
        if (slot >= 22) gg.append("text").attr("class", "d3c-idx").attr("y", 17).attr("text-anchor", "middle").text(String(d.index));
        gg.append("title").text(agentName(report, d.from) + " step " + d.index + " · " + (d.step.name || d.step.type || "") +
          "\n" + truncate(d.step.output || d.step.input || "", 160));
      });
      marks.on("click", function (event, d) { hideTip(); selectStep(report, d.from, d.index); })
        .on("keydown", function (event, d) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectStep(report, d.from, d.index); } })
        .on("mousemove", function (event, d) {
          showTip(event, [{ b: true, text: agentName(report, d.from) + " · step " + d.index + " · " + (d.step.name || d.step.type || "") },
            { text: ((ROLE[d.role] || {}).label || d.role) }, { mono: true, text: truncate(d.step.output || d.step.input || "", 140) }]);
        }).on("mouseleave", hideTip);
      // the outcome at the lane's end
      if (!win.has(lane.steps.length - 1)) return;   // the lane's end is outside the window
      var endX = x(Math.max(0, lane.steps.length - 1)) + slot / 2 + 6;
      var label, fill;
      if (lane.key === "reconciled") {
        label = plan.estimate && plan.estimate.outcome ? "est. " + String(plan.estimate.outcome) : "estimate";
        fill = plan.estimate && plan.estimate.outcome === "success" ? P.good : P.muted;
      } else {
        var ok = lane.outcome && lane.outcome.success === true;
        label = ok ? "✓ solved" : "✗ failed";
        fill = ok ? P.good : P.bad;
      }
      g.append("text").attr("class", "d3c-cap d3c-rend").attr("x", Math.min(endX, W - 4)).attr("y", lane.y + 4).attr("fill", fill)
        .attr("font-weight", 700).attr("text-anchor", endX > W - 60 ? "end" : "start").text(label);
    });

    // links: every reconciled step back to the run it came from
    if (hasSplice) {
      var yBy = {}; lanes.forEach(function (l) { yBy[l.key] = l.y; });
      var yRec = yBy.reconciled;
      var linkG = svg.insert("g", ":first-child").attr("class", "d3c-rlinks");
      merged.forEach(function (m, pos) {
        if (!win.has(pos) || !win.has(m.step)) return;
        var srcLane = m.from === plan.passing ? "passing" : "failing";
        var sy = yBy[srcLane], sx = x(Math.min(m.step, n - 1)), tx = x(pos);
        var d = "M" + sx + "," + (sy + (sy < yRec ? 7 : -7)) + " C" + sx + "," + ((sy + yRec) / 2) + " " + tx + "," + ((sy + yRec) / 2) + " " + tx + "," + (yRec + (sy < yRec ? -7 : 7));
        var l = linkG.append("path").attr("class", "d3c-rlink").attr("d", d).attr("fill", "none")
          .attr("stroke", sideColor(m.from)).attr("stroke-width", 1.3).attr("opacity", 0.55).attr("data-from", m.from).attr("data-step", m.step);
        if (duration && l.node().getTotalLength) {
          var len = l.node().getTotalLength();
          l.attr("stroke-dasharray", len + " " + len).attr("stroke-dashoffset", len)
            .transition().duration(duration).delay(50 * pos).ease(d3.easeCubicOut).attr("stroke-dashoffset", 0);
        }
      });
    }

    // ---- the strategy, as steps a person can run
    var list = document.createElement("ol");
    list.className = "d3c-list d3c-strategy";
    var k = 0;
    function item(text, sub, cls) {
      k++;
      var li = document.createElement("li");
      li.setAttribute("data-n", String(k));
      if (cls) li.className = cls;
      var num = document.createElement("span"); num.className = "n"; num.textContent = String(k);
      var body = document.createElement("div");
      var main = document.createElement("div"); var strong = document.createElement("strong"); strong.textContent = text; main.appendChild(strong);
      body.appendChild(main);
      if (sub) { var w = document.createElement("div"); w.className = "what"; w.textContent = sub; body.appendChild(w); }
      li.appendChild(num); li.appendChild(body); list.appendChild(li);
      return li;
    }
    var fName = agentName(report, plan.failing), pName = agentName(report, plan.passing);
    if (plan.prefix.length) {
      item("Keep " + fName + "'s step" + (plan.prefix.length === 1 ? " " : "s ") + plan.prefix[0] + (plan.prefix.length > 1 ? "–" + plan.prefix[plan.prefix.length - 1] : ""),
           "the prefix before the cut is unchanged");
    }
    if (isNum(plan.cut)) {
      item("At step " + plan.cut + ", " + (plan.recipe && plan.recipe.correction ? plan.recipe.correction : "take the decision " + pName + " took"),
           plan.premise ? plan.premise : null, "decisive");
    }
    if (plan.adopted.length) {
      item("Then follow " + pName + " from its step " + plan.adopted[0] + (plan.adopted.length > 1 ? " to " + plan.adopted[plan.adopted.length - 1] : ""),
           plan.adopted.length + " adopted step" + (plan.adopted.length === 1 ? "" : "s"));
    }
    if (plan.recipe) {
      item("Replay " + String(plan.recipe.replays || "").split(" — ")[0].trim() + (plan.recipe.expects ? " · expects " + plan.recipe.expects : ""),
           String(plan.recipe.replays || "").indexOf(" — ") > 0 ? plan.recipe.replays.split(" — ").slice(1).join(" — ") : null);
    }
    if (plan.estimate) {
      var e = plan.estimate;
      var parts = [];
      if (e.outcome) parts.push("outcome " + e.outcome);
      if (isNum(e.steps)) parts.push(e.steps + " steps" + (isNum(e.steps_delta) ? " (" + (e.steps_delta >= 0 ? "+" : "") + e.steps_delta + ")" : ""));
      if (isNum(e.tokens)) parts.push((ctx.fmt && ctx.fmt.int ? ctx.fmt.int(e.tokens) : e.tokens) + " tokens" + (isNum(e.tokens_delta) ? " (" + (e.tokens_delta >= 0 ? "+" : "") + e.tokens_delta + ")" : ""));
      if (isNum(e.latency_s)) parts.push((ctx.fmt && ctx.fmt.sec ? ctx.fmt.sec(e.latency_s) : e.latency_s + "s") + (isNum(e.latency_delta_s) ? " (" + (e.latency_delta_s >= 0 ? "+" : "") + e.latency_delta_s.toFixed(2) + "s)" : ""));
      item("Expect: " + parts.join(" · "),
           "a splice estimate, not a replay — confidence " + String(plan.confidence || "unstated") + "; the replay above is what would verify it");
    }
    host.appendChild(list);
    if (plan.narrative) {
      var note = document.createElement("p"); note.className = "d3c-note d3c-narrative"; note.textContent = String(plan.narrative);
      host.appendChild(note);
    }
    var legend = document.createElement("div");
    legend.className = "d3c-legend";
    legend.appendChild(legendItem(P, { role: "feeds_answer" }, sideColor(plan.passing), pName + " (passing)"));
    legend.appendChild(legendItem(P, { role: "feeds_answer" }, sideColor(plan.failing), fName + " (failing)"));
    if (hasSplice) legend.appendChild(legendItem(P, { line: true }, P.rule2, "where each reconciled step comes from"));
    legend.appendChild(legendItem(P, { line: true, dashed: true }, P.bad, "the cut, at the decisive step"));
    host.appendChild(legend);
    return svg.node();
  }

  // ======================================================= 6. the body

  /* The two runs over time, as bodies with branches. Each run is a trunk
   * along one wall-clock axis (cumulative latency): the thinking — plan,
   * reason, decide — sits on the trunk; every tool call branches off it
   * to a leaf that names the tool, filled when its result fed the answer,
   * dashed when it was a dead end; the answer ends the trunk with ✓ or ✗.
   * A's branches grow up, B's grow down, so the gutter between the trunks
   * holds the alignment: matched steps joined by a faint curve, drift in
   * amber, a ranked divergence in red. The fault's path (attribution
   * chain) reddens the failed trunk; the decisive step is ringed. Wheel
   * or drag zooms and pans time (d3.zoom); double-click resets; a click
   * on any node opens it in the inspector. Nothing here is derived: the
   * times are the steps' latencies, the roles the reading's, the links
   * the alignment's. */
  charts.body = function (host, ctx) {
    ensureStyle();
    if (!charts.available()) return null;
    var report = ctx.report;
    if (!report || (!stepsOf(report, "a").length && !stepsOf(report, "b").length)) return null;
    return responsive(host, function () { drawBody(host, ctx, report); }, "body:" + (report.task && report.task.id));
  };

  var BodyZoom = {};   // per task: the current zoom transform, kept across repaints

  /* Time-adaptive clustering. At the current zoom, steps of one run that
   * land within `minGap` pixels of each other fold into one bubble: a
   * capsule on the trunk spanning their time, labelled with what is
   * inside (×N, the dominant tool, how many thoughts), flagged red when
   * the fault runs through it, with a "!" for errors inside. The decisive
   * step, the answer and an error never fold, so what matters stays a
   * node at every zoom. Zoom in and a bubble splits into its steps; click
   * one and the chart zooms to fit it — details on demand. */
  function clusterRun(run, x, minGap, binPx) {
    // a bubble also never spans more than binPx of screen: uniform runs
    // then fold into a row of capsules that split level by level as the
    // zoom grows, instead of one capsule bursting into hundreds of steps
    binPx = binPx || 160;
    var items = [], cur = null;
    function anchor(n) { return n.decisive || n.kind === "answer" || n.error; }
    function flush() {
      if (!cur) return;
      if (cur.nodes.length === 1) items.push({ kind: "node", node: cur.nodes[0], side: run.side });
      else {
        var tools = {}, thoughts = 0, fault = false, errors = 0, dead = 0, fed = 0, lat = 0;
        cur.nodes.forEach(function (n) {
          if (n.kind === "branch") tools[n.step.name || n.step.type] = (tools[n.step.name || n.step.type] || 0) + 1; else thoughts++;
          if (n.fault) fault = true; if (n.error) errors++;
          if (n.role === "dead_end" || n.role === "no_information" || n.role === "repeat") dead++;
          if (n.role === "feeds_answer") fed++;
          lat += n.lat;
        });
        var top = Object.keys(tools).sort(function (a, b) { return tools[b] - tools[a]; })[0] || null;
        items.push({ kind: "bubble", side: run.side, nodes: cur.nodes, t0: cur.nodes[0].t0, t1: cur.nodes[cur.nodes.length - 1].t1,
                     count: cur.nodes.length, tools: tools, top: top, topCount: top ? tools[top] : 0, thoughts: thoughts,
                     fault: fault, errors: errors, dead: dead, fed: fed, lat: lat,
                     from: cur.nodes[0].i, to: cur.nodes[cur.nodes.length - 1].i, key: run.side + ":c:" + cur.nodes[0].i });
      }
      cur = null;
    }
    run.nodes.forEach(function (n) {
      if (anchor(n)) { flush(); items.push({ kind: "node", node: n, side: run.side }); return; }
      if (cur && x(n.t0) - x(cur.last.t0) < minGap && x(n.t0) - x(cur.nodes[0].t0) < binPx) { cur.nodes.push(n); cur.last = n; return; }
      flush();
      cur = { nodes: [n], last: n };
    });
    flush();
    return items;
  }

  function drawBody(host, ctx, report) {
    var P = palette();
    var duration = charts.motion();
    var taskKey = report.task && report.task.id ? report.task.id : "task";
    var dec = decisiveOf(report);
    var fault = faultPath(report);
    var rows = Array.isArray(report.alignment) ? report.alignment : [];
    // a ranked divergence names a step on each side; the alignment link
    // that touches either step is the divergence's link
    var diverged = { a: {}, b: {} };
    (Array.isArray(report.divergences) ? report.divergences : []).forEach(function (d) {
      if (!d) return;
      if (isNum(d.a_index)) diverged.a[d.a_index] = d;
      if (isNum(d.b_index)) diverged.b[d.b_index] = d;
    });
    var runs = ["a", "b"].map(function (side) {
      var steps = stepsOf(report, side);
      var reading = readingOf(report, side);
      var roles = {};
      ((reading && reading.what_happened) || []).forEach(function (w) { if (w && isNum(w.step)) roles[w.step] = w.role; });
      var t = 0, nodes = [];
      steps.forEach(function (st, i) {
        var lat = isNum(st.latency_s) ? Math.max(0, st.latency_s) : 0;
        var kind = st.type === "answer" ? "answer"
          : (st.type === "tool_call" || st.type === "search" || st.type === "retrieve" || st.type === "read") ? "branch" : "trunk";
        nodes.push({ side: side, i: i, step: st, t0: t, t1: t + lat, lat: lat, kind: kind, role: roles[i] || null,
                     fault: !!(fault[side] && fault[side][i]), decisive: !!(dec && dec.side === side && dec.step === i),
                     error: st.error === true });
        t += lat;
      });
      var outcome = report[side] && report[side].outcome ? report[side].outcome : {};
      return { side: side, name: agentName(report, side), nodes: nodes, total: t, success: outcome.success === true, steps: steps };
    });
    var tMax = Math.max(1e-6, d3.max(runs, function (r) { return r.total; }));

    var W = width(host, ctx);
    var H = W >= 700 ? 340 : 300;
    var m = { l: 16, r: 16 };
    var yA = Math.round(H * 0.36), yB = Math.round(H * 0.64), yAxis = Math.round(H / 2);
    var reach = Math.min(74, Math.round(H * 0.2));
    var x0 = d3.scaleLinear().domain([0, tMax]).range([m.l + 30, W - m.r - 46]);
    var zoomState = BodyZoom[taskKey] || d3.zoomIdentity;
    var x = zoomState.rescaleX(x0);

    var wrap = d3.select(host).append("div").attr("class", "d3c-wrap d3c-body-wrap");
    var svg = wrap.append("svg").attr("class", "d3c d3c-body").attr("width", W).attr("height", H)
      .attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "Both runs over time: the thinking on each trunk, tool calls as branches, the alignment between them");
    svg.append("title").text("The two runs over time, as bodies with branches");
    var defs = svg.append("defs");
    defs.append("clipPath").attr("id", "d3c-body-clip-" + taskKey.replace(/[^a-zA-Z0-9_-]/g, "_"))
      .append("rect").attr("x", 0).attr("y", 0).attr("width", W).attr("height", H);
    var clip = "url(#d3c-body-clip-" + taskKey.replace(/[^a-zA-Z0-9_-]/g, "_") + ")";
    var scene = svg.append("g").attr("class", "d3c-scene").attr("clip-path", clip);
    var gLinks = scene.append("g").attr("class", "d3c-body-links");
    var gTrunks = scene.append("g").attr("class", "d3c-body-trunks");
    var gAxis = scene.append("g").attr("class", "d3c-body-axis");
    var gNodes = scene.append("g").attr("class", "d3c-body-nodes");
    var gEnds = scene.append("g").attr("class", "d3c-body-ends");

    // run names in the corners, clear of every leaf label; never scroll away
    [["a", 12], ["b", H - 6]].forEach(function (pair) {
      var run = runs[pair[0] === "a" ? 0 : 1];
      svg.append("text").attr("class", "d3c-cap").attr("x", m.l).attr("y", pair[1])
        .attr("fill", sideColor(pair[0])).attr("font-weight", 700).attr("font-size", 11.5).text(run.name);
    });

    var link = d3.linkVertical().x(function (d) { return d.x; }).y(function (d) { return d.y; });
    var zoom = null;   // set below; bubbles zoom through it
    var timeFmt = function (t) { return t >= 100 ? Math.round(t) + "s" : t >= 10 ? t.toFixed(0) + "s" : t.toFixed(1) + "s"; };

    function leafY(node, k) {
      var dir = node.side === "a" ? -1 : 1;
      var base = node.side === "a" ? yA : yB;
      return base + dir * (reach * (k % 2 ? 1 : 0.62));
    }

    var MIN_GAP = 40;
    function render(first) {
      var span = x.domain()[1] - x.domain()[0];
      var pxPerS = (x.range()[1] - x.range()[0]) / Math.max(1e-6, span);

      // ---- cluster each run at this zoom; where every step sits now
      var clustered = runs.map(function (run) { return clusterRun(run, x, MIN_GAP, 160); });
      var where = { a: {}, b: {} };   // side → step → {x, key, bubble?}
      clustered.forEach(function (items, ri) {
        var side = ri === 0 ? "a" : "b";
        items.forEach(function (it) {
          if (it.kind === "node") where[side][it.node.i] = { x: x(it.node.t0), key: side + ":" + it.node.i, bubble: null };
          else it.nodes.forEach(function (n) { where[side][n.i] = { x: x(n.t0), key: it.key, bubble: it }; });
        });
      });
      var visibleItems = clustered[0].length + clustered[1].length;
      svg.attr("data-items", visibleItems).attr("data-bubbles", clustered[0].concat(clustered[1]).filter(function (it) { return it.kind === "bubble"; }).length);

      // ---- the alignment, beneath everything; one link per pair of places
      var seen = {};
      var pairs = [];
      rows.forEach(function (r) {
        if (!r || !isNum(r.a_index) || !isNum(r.b_index)) return;
        var wa = where.a[r.a_index], wb = where.b[r.b_index];
        if (!wa || !wb) return;
        var div = !!(diverged.a[r.a_index] || diverged.b[r.b_index]);
        var key = wa.key + "|" + wb.key;
        var rank = div ? 3 : r.op === "match" ? 1 : 2;
        if (seen[key]) { seen[key].n++; if (rank > seen[key].rank) { seen[key].rank = rank; seen[key].r = r; seen[key].div = div; } return; }
        seen[key] = { key: key, wa: wa, wb: wb, r: r, div: div, rank: rank, n: 1 };
        pairs.push(seen[key]);
      });
      var al = gLinks.selectAll("path.d3c-body-align").data(pairs, function (d) { return d.key; });
      al.enter().append("path").attr("fill", "none").attr("opacity", 0.8)
        .merge(al)
        .attr("class", function (d) { return "d3c-body-align " + (d.div ? "diverge" : d.r.op === "match" ? "match" : "drift"); })
        .attr("stroke", function (d) { return d.div ? P.bad : d.r.op === "match" ? P.rule2 : P.warn; })
        .attr("stroke-width", function (d) { return d.div ? 1.6 : Math.min(3, 1 + Math.log(d.n)); })
        .attr("stroke-dasharray", function (d) { return d.r.op === "match" ? null : "3 3"; })
        .attr("d", function (d) { return link({ source: { x: d.wa.x, y: yA + 6 }, target: { x: d.wb.x, y: yB - 6 } }); })
        .each(function (d) {
          var t = d3.select(this).select("title");
          if (t.empty()) t = d3.select(this).append("title");
          t.text((d.n > 1 ? d.n + " aligned rows · " : "row " + rows.indexOf(d.r) + " · ") + d.r.op + (isNum(d.r.similarity) ? " · similarity " + d.r.similarity.toFixed(2) : "") + (d.div ? " · divergence" : ""));
        });
      al.exit().remove();

      // ---- bubbles: folded stretches of a run, on the trunk
      var bubbles = clustered[0].concat(clustered[1]).filter(function (it) { return it.kind === "bubble"; });
      var bub = gNodes.selectAll("g.d3c-bubble").data(bubbles, function (d) { return d.key; });
      var bEnter = bub.enter().append("g").attr("class", "d3c-bubble").attr("tabindex", 0).attr("role", "button");
      bEnter.append("rect").attr("class", "d3c-bubble-box").attr("rx", 9).attr("ry", 9);
      bEnter.append("rect").attr("class", "d3c-bubble-tint").attr("rx", 9).attr("ry", 9).attr("pointer-events", "none");
      bEnter.append("text").attr("class", "d3c-bubble-label").attr("text-anchor", "middle").attr("font-family", "var(--mono)").attr("font-size", 10.5);
      bEnter.append("text").attr("class", "d3c-bubble-sub").attr("text-anchor", "middle").attr("font-family", "var(--mono)").attr("font-size", 9.5);
      bEnter.append("title");
      var bMerged = bEnter.merge(bub);
      bMerged.each(function (d) {
        var g = d3.select(this);
        var y = d.side === "a" ? yA : yB;
        var bx0 = x(d.t0) - 10, bx1 = x(d.t1) + 10, bw = Math.max(26, bx1 - bx0);
        var color = d.fault ? P.bad : sideColor(d.side);
        g.attr("data-side", d.side).attr("data-from", d.from).attr("data-to", d.to).attr("data-count", d.count)
          .attr("class", "d3c-bubble" + (d.fault ? " fault" : "") + (d.errors ? " errors" : ""))
          .attr("aria-label", d.side.toUpperCase() + " steps " + d.from + " to " + d.to + ": " + d.count + " steps folded" + (d.top ? ", mostly " + d.top : "") + (d.fault ? ", the fault runs through them" : "") + " — press Enter to zoom in");
        g.select("rect.d3c-bubble-box").attr("x", bx0).attr("y", y - 9).attr("width", bw).attr("height", 18)
          .attr("fill", P.surface)
          .attr("stroke", color).attr("stroke-width", d.fault ? 2 : 1.6)
          .attr("stroke-dasharray", d.dead === d.count ? "3 3" : null);
        g.select("rect.d3c-bubble-tint").attr("x", bx0).attr("y", y - 9).attr("width", bw).attr("height", 18)
          .attr("fill", d.fault ? P.bad : sideColor(d.side)).attr("fill-opacity", d.fault ? 0.14 : 0.06);
        var label = "×" + d.count + (d.top && bw >= 70 ? " " + truncate(d.top, Math.floor((bw - 34) / 6.5)) : "");
        g.select("text.d3c-bubble-label").attr("x", bx0 + bw / 2).attr("y", y + 4).attr("fill", d.fault ? P.bad : P.ink).attr("font-weight", 600).text(label);
        var sub = (d.topCount && d.top ? d.top + " ×" + d.topCount : "") + (d.thoughts ? (d.top ? " · " : "") + d.thoughts + " thought" + (d.thoughts === 1 ? "" : "s") : "") +
                  (d.errors ? " · " + d.errors + " error" + (d.errors === 1 ? "" : "s") : "") + (d.fed ? " · " + d.fed + " fed the answer" : "");
        g.select("text.d3c-bubble-sub").attr("x", bx0 + bw / 2).attr("y", y + (d.side === "a" ? -14 : 22)).attr("fill", d.errors ? P.bad : P.muted)
          .text(bw >= 90 ? truncate(sub, Math.floor(bw / 5.6)) : (d.errors ? "!" : ""));
        g.select("title").text(d.side.toUpperCase() + " steps " + d.from + "–" + d.to + " · " + d.count + " steps over " + timeFmt(d.lat) + "\n" +
          Object.keys(d.tools).map(function (k) { return d.tools[k] + "× " + k; }).join(", ") + (d.thoughts ? (Object.keys(d.tools).length ? ", " : "") + d.thoughts + " thought(s)" : "") +
          (d.fault ? "\nthe fault runs through these steps" : "") + (d.errors ? "\n" + d.errors + " error(s) inside" : "") + "\nclick to zoom in");
      });
      bub.exit().remove();
      function zoomTo(d) {
        // zoom so the bubble's steps get room to stand apart (MIN_GAP each),
        // never past the zoom's limit; anchored on the bubble's first step
        var width = x0.range()[1] - x0.range()[0];
        var span = Math.max(1e-6, x0(d.t1) - x0(d.t0));
        var fit = width / Math.max(span, width * 0.05);
        var spread = (d.count * MIN_GAP * 1.3) / span;
        var k = Math.max(fit, spread);
        var cur = d3.zoomTransform(svg.node()).k;
        if (k <= cur * 1.05) k = cur * 2;            // already fitted: keep zooming in
        k = Math.min(40, k, cur * 4);                // one click, one level: bubbles split into bubbles, then steps
        var t0 = Math.max(0, d.t0 - 0.02 * (d.t1 - d.t0));
        var tx = Math.min(0, x0.range()[0] - k * x0(t0));
        svg.transition().duration(duration).call(zoom.transform, d3.zoomIdentity.translate(tx, 0).scale(k));
      }
      bMerged.on("click", function (event, d) { hideTip(); zoomTo(d); })
        .on("keydown", function (event, d) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); zoomTo(d); } })
        .on("mousemove", function (event, d) {
          showTip(event, [{ b: true, text: d.side.toUpperCase() + " · steps " + d.from + "–" + d.to + " · " + d.count + " folded" },
            { text: Object.keys(d.tools).map(function (k) { return d.tools[k] + "× " + k; }).join(", ") + (d.thoughts ? (Object.keys(d.tools).length ? ", " : "") + d.thoughts + " thought(s)" : "") + " · " + timeFmt(d.lat) },
            d.fault ? { text: "the fault runs through these steps" } : null, d.errors ? { text: d.errors + " error(s) inside" } : null,
            { text: "click to zoom in" }]);
        }).on("mouseleave", hideTip);

      // ---- trunks: one thick line per run, red along the fault's path
      runs.forEach(function (run) {
        var y = run.side === "a" ? yA : yB;
        var segs = [];
        run.nodes.forEach(function (n, i) {
          var next = run.nodes[i + 1];
          var end = next ? next.t0 : n.t1;
          segs.push({ key: run.side + ":" + i, t0: n.t0, t1: Math.max(end, n.t0), fault: n.fault && (!next || next.fault || next.kind === "answer") });
        });
        var seg = gTrunks.selectAll("line.d3c-trunk-" + run.side).data(segs, function (d) { return d.key; });
        seg.enter().append("line").attr("class", "d3c-trunk d3c-trunk-" + run.side)
          .attr("stroke-linecap", "round").merge(seg)
          .attr("x1", function (d) { return x(d.t0); }).attr("x2", function (d) { return Math.max(x(d.t0), x(d.t1)); })
          .attr("y1", y).attr("y2", y)
          .attr("stroke", function (d) { return d.fault ? P.bad : sideColor(run.side); })
          .attr("stroke-width", function (d) { return d.fault ? 5 : 3.5; })
          .attr("opacity", function (d) { return d.fault ? 0.85 : 0.55; });
        seg.exit().remove();
      });

      // ---- the time ruler in the gutter
      gAxis.selectAll("*").remove();
      var ticks = x.ticks(Math.max(3, Math.floor((W - 40) / 90)));
      gAxis.append("line").attr("x1", x.range()[0]).attr("x2", x.range()[1]).attr("y1", yAxis).attr("y2", yAxis).attr("stroke", P.rule).attr("stroke-width", 1);
      ticks.forEach(function (t) {
        gAxis.append("line").attr("x1", x(t)).attr("x2", x(t)).attr("y1", yAxis - 3).attr("y2", yAxis + 3).attr("stroke", P.rule2);
        gAxis.append("text").attr("class", "d3c-idx").attr("x", x(t)).attr("y", yAxis + 13).attr("text-anchor", "middle").text(timeFmt(t));
      });
      gAxis.append("text").attr("class", "d3c-cap").attr("x", x.range()[1]).attr("y", yAxis - 6).attr("text-anchor", "end").text("elapsed, from each run's own latencies");

      // ---- nodes: thinking on the trunk, tools as branches, the answer at the end
      var all = [];
      clustered.forEach(function (items) { items.forEach(function (it) { if (it.kind === "node") all.push(it.node); }); });
      var node = gNodes.selectAll("g.d3c-bnode").data(all, function (d) { return d.side + ":" + d.i; });
      var enter = node.enter().append("g")
        .attr("class", function (d) { return "d3c-bnode kind-" + d.kind + (d.fault ? " fault" : "") + (d.decisive ? " decisive" : ""); })
        .attr("data-side", function (d) { return d.side; }).attr("data-step", function (d) { return d.i; })
        .attr("data-kind", function (d) { return d.kind; })
        .attr("tabindex", 0).attr("role", "button")
        .attr("aria-label", function (d) { return d.side.toUpperCase() + " step " + d.i + " · " + (d.step.name || d.step.type) + " at " + timeFmt(d.t0) + (d.decisive ? " · decisive" : ""); });
      enter.append("path").attr("class", "d3c-branch").attr("fill", "none");
      enter.append("g").attr("class", "d3c-bleaf");
      enter.append("g").attr("class", "d3c-bmark");
      enter.append("text").attr("class", "d3c-blabel");
      enter.append("title");
      var merged = enter.merge(node);
      merged.each(function (d, k) {
        var g = d3.select(this);
        var y = d.side === "a" ? yA : yB;
        var px = x(d.t0);
        var color = d.fault ? P.bad : sideColor(d.side);
        var dense = !d.decisive && pxPerS * Math.max(d.lat, 0.4) < 26;   // too tight for a label at this zoom; the decisive step always keeps its
        var mark = g.select("g.d3c-bmark").attr("transform", "translate(" + px + "," + y + ")");
        mark.selectAll("*").remove();
        var leaf = g.select("g.d3c-bleaf");
        leaf.selectAll("*").remove();
        var branch = g.select("path.d3c-branch");
        var label = g.select("text.d3c-blabel");
        if (d.kind === "branch") {
          // a branch: from the trunk out to a leaf placed within the call's own duration
          var ly = leafY(d, d.i);
          var lx = px + Math.max(10, Math.min(60, pxPerS * d.lat * 0.5));
          branch.attr("d", "M" + px + "," + y + " C" + px + "," + ((y + ly) / 2) + " " + lx + "," + ((y + ly) / 2) + " " + lx + "," + ly)
            .attr("stroke", color).attr("stroke-width", d.fault ? 2 : 1.4)
            .attr("stroke-dasharray", d.role === "dead_end" || d.role === "no_information" || d.role === "repeat" ? "3 3" : null)
            .attr("opacity", 0.9).style("display", null);
          mark.append("circle").attr("r", 3).attr("fill", color);
          leaf.attr("transform", "translate(" + lx + "," + ly + ")");
          var fed = d.role === "feeds_answer";
          leaf.append("circle").attr("r", d.decisive ? 7 : 6).attr("fill", fed ? color : P.surface).attr("stroke", color)
            .attr("stroke-width", 1.8).attr("stroke-dasharray", d.role === "dead_end" || d.role === "no_information" || d.role === "repeat" ? "2.5 2" : null);
          if (d.error) leaf.append("text").attr("class", "d3c-cap").attr("y", 4).attr("text-anchor", "middle").attr("fill", P.bad).attr("font-weight", 800).text("!");
          if (d.decisive) leaf.append("circle").attr("class", "d3c-ring " + (dec.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", 12);
          label.attr("x", lx).attr("y", ly + (d.side === "a" ? -12 : 18)).attr("text-anchor", "middle").attr("fill", d.fault ? P.bad : P.ink2)
            .attr("font-size", 10.5).attr("font-family", "var(--mono)")
            .text(dense ? "" : truncate(d.step.name || d.step.type, 16) + (d.decisive ? " · decisive" : ""));
        } else if (d.kind === "answer") {
          branch.style("display", "none");
          mark.append("circle").attr("r", 10).attr("fill", "none").attr("stroke", d.step && runs[d.side === "a" ? 0 : 1].success ? P.good : P.bad).attr("stroke-width", 2);
          mark.append("circle").attr("r", 5).attr("fill", color);
          if (d.decisive) mark.append("circle").attr("class", "d3c-ring hypothesized").attr("r", 14);
          label.attr("x", px).attr("y", y + (d.side === "a" ? -16 : 22)).attr("text-anchor", "middle").attr("fill", runs[d.side === "a" ? 0 : 1].success ? P.good : P.bad)
            .attr("font-size", 11).attr("font-weight", 700).attr("font-family", "var(--sans)")
            .text((runs[d.side === "a" ? 0 : 1].success ? "✓ solved" : "✗ failed") + " · " + timeFmt(d.t0));
        } else {
          // thinking on the trunk
          branch.style("display", "none");
          var role = d.role || d.step.type;
          var shape = role === "verify" ? "diamond" : "square";
          var pathD = markPath(shape, d.decisive ? 6 : 4.5);
          mark.append("path").attr("d", pathD).attr("fill", P.surface).attr("stroke", color).attr("stroke-width", 1.8);
          if (d.decisive) mark.append("circle").attr("class", "d3c-ring " + (dec.verification === "replay-verified" ? "verified" : "hypothesized")).attr("r", 11);
          label.attr("x", px).attr("y", y + (d.side === "a" ? 18 : -12)).attr("text-anchor", "middle").attr("fill", P.muted)
            .attr("font-size", 10).attr("font-family", "var(--mono)")
            .text(dense ? "" : truncate(role === "feeds_answer" ? (d.step.name || d.step.type) : role.replace(/_/g, " "), 12) + (d.decisive ? " · decisive" : ""));
        }
        g.select("title").text(d.side.toUpperCase() + " step " + d.i + " · " + (d.step.name || d.step.type) + " · " + timeFmt(d.t0) + (d.lat ? " +" + d.lat.toFixed(2) + "s" : "") +
          (d.role ? " · " + ((ROLE[d.role] || {}).label || d.role.replace(/_/g, " ")) : "") + "\n" + truncate(d.step.output || d.step.input || "", 220));
      });
      node.exit().remove();
      merged.on("mousemove", function (event, d) {
        showTip(event, [{ b: true, text: d.side.toUpperCase() + " · step " + d.i + " · " + (d.step.name || d.step.type) },
          { text: timeFmt(d.t0) + (d.lat ? " + " + d.lat.toFixed(2) + "s" : "") + (isNum(d.step.tokens) ? " · " + d.step.tokens + " tokens" : "") + (d.role ? " · " + ((ROLE[d.role] || {}).label || d.role.replace(/_/g, " ")) : "") },
          { mono: true, text: truncate(d.step.input || "", 110) }, { mono: true, text: "→ " + truncate(d.step.output || "", 140) }]);
      }).on("mouseleave", hideTip)
        .on("click", function (event, d) { hideTip(); selectStep(report, d.side, d.i); })
        .on("keydown", function (event, d) { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectStep(report, d.side, d.i); } });
      if (first && duration && !zoomState.k) { /* no entrance animation beyond the arrival of nodes */ }
    }
    render(true);

    // ---- zoom & pan on time; double-click resets
    zoom = d3.zoom().scaleExtent([1, 40])
      .translateExtent([[x0.range()[0] - 8, 0], [x0.range()[1] + 8, H]])
      .extent([[x0.range()[0], 0], [x0.range()[1], H]])
      .on("zoom", function (event) {
        BodyZoom[taskKey] = event.transform;
        x = event.transform.rescaleX(x0);
        render(false);
        hideTip();
      });
    svg.call(zoom).on("dblclick.zoom", null).on("dblclick", function () { svg.transition().duration(duration).call(zoom.transform, d3.zoomIdentity); });
    if (zoomState !== d3.zoomIdentity) svg.call(zoom.transform, zoomState);
    svg.attr("tabindex", 0).on("keydown.body", function (event) {
      if (event.key === "0") { svg.call(zoom.transform, d3.zoomIdentity); }
      else if (event.key === "+" || event.key === "=") { svg.call(zoom.scaleBy, 1.5); }
      else if (event.key === "-") { svg.call(zoom.scaleBy, 1 / 1.5); }
    });

    var legend = document.createElement("div");
    legend.className = "d3c-legend";
    legend.appendChild(legendItem(P, { line: true }, P.ink2, "trunk = the run over time; squares think, diamonds verify"));
    legend.appendChild(legendItem(P, { role: "feeds_answer" }, P.ink2, "leaf = a tool call; filled when its result fed the answer, dashed when a dead end"));
    legend.appendChild(legendItem(P, { line: true }, P.bad, "the fault's path"));
    legend.appendChild(legendItem(P, { line: true, dashed: true }, P.warn, "aligned but drifted; red = a ranked divergence"));
    if (dec) legend.appendChild(legendItem(P, { ring: true, dashed: dec.verification !== "replay-verified" }, P.bad, "decisive step"));
    legend.appendChild(legendItem(P, { hatch: true }, P.rule2, "capsule = steps folded at this zoom (×N, what is inside); click it to zoom in"));
    var hint = document.createElement("span"); hint.textContent = "wheel or drag to zoom time · double-click to reset · click a node to open it";
    legend.appendChild(hint);
    host.appendChild(legend);
    return svg.node();
  }

})(typeof window !== "undefined" ? window : this);
