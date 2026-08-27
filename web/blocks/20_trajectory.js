/* AgentDiff blocks — trajectory.
 *
 * The trace visualization: five blocks that read the alignment as a picture
 * rather than a table. The governing idea is that a diff between two agent
 * runs is *spatial* — two tracks, one above the other, matched steps in the
 * same column — so divergence is something you see before you read.
 *
 * Everything here is hand-rolled SVG sized from the container, theme-aware
 * through ctx.color, and honest about absence: a one-sided alignment row
 * draws a gap, never a zero.
 */
(function (global) {
  "use strict";

  if (!global.AgentDiff || typeof global.AgentDiff.block !== "function") return;

  // Bound at the top of every draw; the core hands out the same helpers each
  // time, so this is a convenience, not shared state.
  var H = null, S = null, C = null, F = null;

  function bind(ctx) { H = ctx.h; S = ctx.svg; C = ctx.color; F = ctx.fmt; }

  // ------------------------------------------------------------------ style

  var STYLED = false;
  function ensureStyle() {
    if (STYLED) return;
    STYLED = true;
    var css = [
      ".tj-sides{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;font-size:11.5px;margin-bottom:6px}",
      ".tj-side{display:inline-flex;align-items:center;gap:5px;min-width:0}",
      ".tj-chip{width:9px;height:9px;border-radius:2px;flex:none}",
      ".tj-side b{font-weight:600;letter-spacing:-.01em}",
      ".tj-legend{display:flex;flex-wrap:wrap;gap:3px 11px;align-items:center;",
      "margin-top:7px;font-size:10.5px;color:var(--ink-3);line-height:1}",
      ".tj-legend .k{display:inline-flex;align-items:center;gap:4px}",
      ".tj-legend .lab{text-transform:uppercase;letter-spacing:.07em;opacity:.75}",
      ".tj-sw{width:12px;height:8px;border-radius:2px;flex:none;display:inline-block}",
      ".tj-read{margin-top:7px;min-height:15px;font-family:var(--mono);font-size:11px;",
      "color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".tj-read i{font-style:normal;color:var(--ink-3)}",
      ".tj-cols{display:flex;flex-wrap:wrap;gap:10px}",
      ".tj-pane{flex:1 1 210px;min-width:0;border:1px solid var(--rule);border-radius:8px;padding:8px 9px}",
      ".tj-pane.absent{border-style:dashed;opacity:.72}",
      ".tj-pane h4{display:flex;align-items:center;gap:6px;font-size:11px;margin:0 0 5px;",
      "text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3);font-weight:600}",
      ".tj-lbl{font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);margin:6px 0 2px}",
      ".tj-text{font-family:var(--mono);font-size:11px;line-height:1.45;white-space:pre-wrap;",
      "word-break:break-word;background:var(--surface-2);border:1px solid var(--rule);",
      "border-radius:6px;padding:6px 7px;max-height:132px;overflow:auto}",
      ".tj-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:8px}",
      ".tj-head .sp{flex:1}",
      ".tj-nav{display:flex;gap:4px}",
      ".tj-nav button{border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);",
      "border-radius:6px;padding:1px 7px;font-size:11px;line-height:1.5;cursor:pointer}",
      ".tj-nav button:hover:not([disabled]){border-color:var(--accent);color:var(--accent)}",
      ".tj-nav button[disabled]{opacity:.4;cursor:default}",
      ".tj-note{margin-top:6px;font-size:11px;color:var(--ink-2);border-left:2px solid var(--warn);padding-left:7px}",
      ".tj-seq{font-family:var(--mono);font-size:11px;color:var(--ink-2);display:flex;",
      "align-items:baseline;gap:6px;margin-top:2px}",
      ".tj-seq b{font-weight:600;font-size:10.5px;letter-spacing:.04em}",
      ".tj-seq span{letter-spacing:.14em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      "svg.tj{display:block;max-width:none}",
      ".tj-hit{cursor:pointer}",
      ".tj-ctl{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0 0 8px}",
      ".tj-ctl .grp{display:inline-flex;border:1px solid var(--rule-2);border-radius:6px;overflow:hidden}",
      ".tj-ctl .grp button{border:0;background:var(--surface);color:var(--ink-2);font-size:11px;",
      "line-height:1.7;padding:2px 9px;cursor:pointer;font-family:inherit}",
      ".tj-ctl .grp button+button{border-left:1px solid var(--rule-2)}",
      ".tj-ctl .grp button:hover:not([disabled]):not([aria-pressed=\"true\"]){color:var(--accent)}",
      ".tj-ctl .grp button[aria-pressed=\"true\"]{background:var(--accent);color:#fff}",
      ".tj-ctl .grp button[disabled]{opacity:.45;cursor:default}",
      ".tj-ctl select{font-family:inherit;font-size:11px;background:var(--surface);color:var(--ink-2);",
      "border:1px solid var(--rule-2);border-radius:6px;padding:2px 4px}",
      ".tj-ctl select[disabled]{opacity:.45}",
      ".tj-status{font-family:var(--mono);font-size:10.5px;color:var(--ink-3)}",
      ".tj-trade{margin-top:8px;font-size:11.5px;color:var(--ink-2);border-left:2px solid var(--accent);",
      "padding-left:8px;line-height:1.45}",
      ".tj-trade b{display:block;font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;",
      "color:var(--ink-3);font-weight:600;margin-bottom:1px}",
      "@keyframes tjPulse{0%,100%{opacity:1}50%{opacity:.45}}",
      ".tj-live{animation:tjPulse .9s ease-in-out infinite}",
      "@keyframes tjFlash{0%,100%{opacity:1}50%{opacity:.15}}",
      ".tj-flash{animation:tjFlash .45s ease-in-out infinite}",
      "@media (prefers-reduced-motion: reduce){.tj-live,.tj-flash{animation:none}}",
    ].join("");
    try {
      var tag = document.createElement("style");
      tag.setAttribute("data-agentdiff", "trajectory");
      tag.appendChild(document.createTextNode(css));
      document.head.appendChild(tag);
    } catch (err) { /* styling is a nicety; the blocks still read without it */ }
  }

  // ------------------------------------------------------------- responsive
  //
  // The core renders a block while its card is still detached, so clientWidth
  // is 0 at paint time. Draw once at a nominal width, then re-measure on the
  // next frame and repaint if the real width differs — layout-driven, not
  // time-driven, so the result is the same on every load.

  var painters = [];
  var resizeTimer = null;

  function measure(el) {
    var w = 0;
    try { w = el.clientWidth || 0; } catch (err) { w = 0; }
    if (!w) w = 340;
    return Math.max(220, w - 24);
  }

  function sized(el, ctx, draw) {
    var host = ctx.h("div");
    el.appendChild(host);
    var last = -1;
    function go() {
      var w = measure(el);
      if (w === last) return;
      last = w;
      host.innerHTML = "";
      draw(host, w);
    }
    go();
    if (typeof global.requestAnimationFrame === "function") {
      global.requestAnimationFrame(function () { if (el.isConnected) go(); });
    }
    painters.push({ el: el, go: go });
    if (painters.length > 64) {
      painters = painters.filter(function (p) { return p.el.isConnected; });
    }
    return host;
  }

  try {
    global.addEventListener("resize", function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        resizeTimer = null;
        painters = painters.filter(function (p) { return p.el.isConnected; });
        painters.forEach(function (p) {
          try { p.go(); } catch (err) { /* one stale painter must not stop the rest */ }
        });
      }, 140);
    });
  } catch (err) { /* no window: nothing to resize */ }

  // -------------------------------------------------------------- selection
  //
  // One shared cursor across the trajectory blocks: clicking a step in
  // `tracks` is what `step-detail` is looking at. Subscribers are pruned by
  // DOM connectivity, so a re-render of the page cannot leave ghosts behind.

  var Sel = { task: null, row: null, side: null, subs: [] };

  function subscribe(el, fn) { Sel.subs.push({ el: el, fn: fn }); }

  function notify() {
    Sel.subs = Sel.subs.filter(function (s) { return s.el.isConnected; });
    Sel.subs.forEach(function (s) {
      try { s.fn(); } catch (err) { console.warn("AgentDiff trajectory: subscriber failed", err); }
    });
  }

  function syncTask(ctx) {
    var task = ctx.task || (ctx.report && ctx.report.task ? ctx.report.task.id : null);
    if (Sel.task !== task) { Sel.task = task; Sel.row = null; Sel.side = null; }
  }

  function select(row, side, ctx) {
    Sel.row = row;
    Sel.side = side || null;
    if (ctx && ctx.signal) ctx.signal("inspect");
    notify();
  }

  // ------------------------------------------------------------ data access

  function rowsOf(report) {
    return report && Array.isArray(report.alignment) ? report.alignment : [];
  }

  function stepsOf(report, side) {
    var box = report && report[side];
    return box && Array.isArray(box.steps) ? box.steps : [];
  }

  function stepAt(report, side, index) {
    if (index === null || index === undefined) return null;
    var steps = stepsOf(report, side);
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] && steps[i].index === index) return steps[i];
    }
    return steps[index] || null;
  }

  function agentName(report, side) {
    var box = report && report[side];
    var name = box && box.agent && box.agent.name;
    return name || side.toUpperCase();
  }

  function sideColor(side) { return side === "a" ? C.a : C.b; }

  /* Divergences carry a_index and b_index that may land in different
   * alignment rows when the region is one-sided, so a column is divergent if
   * it matches either. The rank marker goes on the earliest such column. */
  function divergenceMap(report) {
    var rows = rowsOf(report);
    var map = {};
    (report && Array.isArray(report.divergences) ? report.divergences : []).forEach(function (d) {
      var hits = [];
      rows.forEach(function (row, i) {
        var a = d.a_index !== null && d.a_index !== undefined && row.a_index === d.a_index;
        var b = d.b_index !== null && d.b_index !== undefined && row.b_index === d.b_index;
        if (a || b) hits.push(i);
      });
      hits.forEach(function (i, k) {
        var mark = { div: d, primary: k === 0 };
        if (!map[i] || (map[i].div.rank || 99) > (d.rank || 99)) map[i] = mark;
        else if (!map[i].primary && mark.primary) map[i] = mark;
      });
    });
    return map;
  }

  function rootInfo(report) {
    var at = report && report.attribution;
    if (!at || !at.failed_agent || at.root_cause_step === null || at.root_cause_step === undefined) {
      return null;
    }
    var side = at.failed_agent === "a" ? "a" : "b";
    var rows = rowsOf(report);
    var row = -1;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i][side + "_index"] === at.root_cause_step) { row = i; break; }
    }
    return { side: side, index: at.root_cause_step, row: row, category: at.category || null,
             explanation: at.explanation || "" };
  }

  function defaultSelection(report) {
    var rows = rowsOf(report);
    if (!rows.length) return { row: -1, side: null, why: "no alignment" };
    var root = rootInfo(report);
    if (root && root.row >= 0) {
      return { row: root.row, side: root.side, why: "the attributed root cause" };
    }
    var map = divergenceMap(report);
    var keys = Object.keys(map).map(Number).sort(function (x, y) { return x - y; });
    if (keys.length) return { row: keys[0], side: null, why: "the first divergence" };
    return { row: 0, side: null, why: "the first aligned step" };
  }

  function resolved(report) {
    if (Sel.row === null || Sel.row === undefined || !rowsOf(report)[Sel.row]) {
      var def = defaultSelection(report);
      return { row: def.row, side: def.side, why: def.why, pinned: false };
    }
    return { row: Sel.row, side: Sel.side, why: null, pinned: true };
  }

  function semanticOf(report) {
    var sem = report && report.semantic;
    return sem && typeof sem === "object" ? sem : null;
  }

  function semanticByRow(report) {
    var sem = semanticOf(report);
    var out = {};
    if (sem && Array.isArray(sem.rows)) {
      sem.rows.forEach(function (r) { if (r && typeof r.row === "number") out[r.row] = r; });
    }
    return out;
  }

  // ------------------------------------------------------------ svg helpers

  var TYPES = ["plan", "search", "retrieve", "read", "tool_call", "reason", "answer"];
  var TYPE_LABEL = {
    plan: "plan", search: "search", retrieve: "retrieve", read: "read",
    tool_call: "tool", reason: "reason", answer: "answer",
  };

  function pts(list) {
    return list.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ");
  }

  /* One shape per step type. Shapes, not colours, carry type — colour is
   * already spoken for by side and by quality. */
  function glyphNode(type, cx, cy, r) {
    switch (type) {
      case "plan":
        return S("rect", { x: cx - r, y: cy - r, width: 2 * r, height: 2 * r, rx: 1.5 });
      case "search":
        return S("circle", { cx: cx, cy: cy, r: r });
      case "retrieve":
        return S("polygon", { points: pts([[cx - r, cy - r * 0.78], [cx + r, cy - r * 0.78], [cx, cy + r]]) });
      case "read": {
        var w = r * 0.82, cut = r * 0.72;
        return S("polygon", { points: pts([
          [cx - w, cy - r], [cx + w - cut, cy - r], [cx + w, cy - r + cut],
          [cx + w, cy + r], [cx - w, cy + r]]) });
      }
      case "tool_call": {
        var hex = [];
        for (var i = 0; i < 6; i++) {
          var ang = (Math.PI / 180) * (60 * i - 90);
          hex.push([cx + r * Math.cos(ang), cy + r * Math.sin(ang)]);
        }
        return S("polygon", { points: pts(hex) });
      }
      case "reason":
        return S("polygon", { points: pts([[cx, cy - r], [cx + r, cy + r * 0.78], [cx - r, cy + r * 0.78]]) });
      case "answer":
        return S("polygon", { points: pts([[cx, cy - r], [cx + r, cy], [cx, cy + r], [cx - r, cy]]) });
      default:
        return S("circle", { cx: cx, cy: cy, r: r * 0.55 });
    }
  }

  /* Quality is a fill treatment so it reads at a glance without a second
   * mark: solid = bad, tinted = weak, side-tinted outline = good, dashed
   * hairline = unannotated (not "fine" — simply not judged). */
  function qualityStyle(quality, side) {
    if (quality === "bad")  return { stroke: C.bad,  fill: C.bad,  op: 0.85, w: 1.4, dash: null };
    if (quality === "weak") return { stroke: C.warn, fill: C.warn, op: 0.3,  w: 1.3, dash: null };
    if (quality === "good") return { stroke: sideColor(side), fill: sideColor(side), op: 0.16, w: 1.4, dash: null };
    return { stroke: C.muted, fill: C.muted, op: 0, w: 1, dash: "2 2" };
  }

  function styleGlyph(node, st) {
    node.setAttribute("stroke", st.stroke);
    node.setAttribute("stroke-width", st.w);
    node.setAttribute("fill", st.fill);
    node.setAttribute("fill-opacity", st.op);
    node.setAttribute("stroke-linejoin", "round");
    if (st.dash) node.setAttribute("stroke-dasharray", st.dash);
    return node;
  }

  function tinyGlyph(type, side, quality) {
    var node = glyphNode(type, 7, 7, 4.4);
    styleGlyph(node, qualityStyle(quality === undefined ? "good" : quality, side || "a"));
    return S("svg", { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true" }, [node]);
  }

  function absentSwatch() {
    return S("svg", { width: 14, height: 12, viewBox: "0 0 14 12", "aria-hidden": "true" }, [
      S("line", { x1: 1, y1: 6, x2: 13, y2: 6, stroke: C.grid, "stroke-width": 1 }),
      S("circle", { cx: 7, cy: 6, r: 2.6, fill: C.surface, stroke: C.muted,
                    "stroke-width": 1, "stroke-dasharray": "1.6 1.4" }),
    ]);
  }

  function tick(x1, y1, x2, y2, stroke, width, dash) {
    var line = S("line", { x1: x1, y1: y1, x2: x2, y2: y2, stroke: stroke, "stroke-width": width || 1 });
    if (dash) line.setAttribute("stroke-dasharray", dash);
    return line;
  }

  function label(x, y, text, fill, size, anchor) {
    return S("text", {
      x: x, y: y, fill: fill, "font-size": size || 9,
      "text-anchor": anchor || "middle",
      "font-family": "var(--mono)", "letter-spacing": "0.02em", text: text,
    });
  }

  function legend(items) {
    var box = H("div", { class: "tj-legend" });
    items.forEach(function (item) {
      if (item.heading) {
        box.appendChild(H("span", { class: "lab", text: item.heading }));
        return;
      }
      box.appendChild(H("span", { class: "k" }, [
        item.node || H("i", {
          class: "tj-sw",
          style: {
            background: item.fill || "transparent",
            opacity: item.opacity === undefined ? "1" : String(item.opacity),
            border: item.border || "none",
            borderRadius: item.round ? "999px" : "2px",
          },
        }),
        H("span", { text: item.text }),
      ]));
    });
    return box;
  }

  function sideHeader(report) {
    var box = H("div", { class: "tj-sides" });
    ["a", "b"].forEach(function (side) {
      var out = report[side] && report[side].outcome;
      var ok = out ? out.success : null;
      box.appendChild(H("span", { class: "tj-side" }, [
        H("i", { class: "tj-chip", style: { background: sideColor(side) } }),
        H("b", { text: side.toUpperCase() + " " + agentName(report, side) }),
        ok === null || ok === undefined ? null :
          H("span", { class: "tag " + (ok ? "good" : "bad"), text: ok ? "solved" : "failed" }),
      ]));
    });
    return box;
  }

  // ================================================================ tracks
  //
  // The hero has two ways to lay the same two runs out, and a replay that
  // animates either. `columns` is the alignment view: matched steps share a
  // column, so divergence is spatial. `time` superimposes both runs on one
  // shared wall-clock axis in seconds, so the viewer sees one agent finish
  // while the other is still working — the thing alignment columns cannot
  // show. Replay is ephemeral view state: deliberately never written to the
  // layout store, cancelled on every re-render, and every animation frame is
  // guarded by a generation counter so a task switch, mode switch or resize
  // leaves no orphan frames behind. The final frame of a replay is exactly
  // the static render — replay restates the picture, it never redraws it.

  var View = { mode: "columns" };

  var Replay = {
    gen: 0,          // bumped on every draw; frames from an older draw stop
    raf: 0,
    loopGen: -1,
    playing: false,
    done: false,
    reduced: false,  // prefers-reduced-motion, sampled when play is pressed
    t: 0,            // simulated wall-clock seconds
    speed: 1,
    lastNow: 0,
  };

  var Plan = null;      // timing derived from the current report
  var Anim = null;      // the drawn nodes replay animates
  var Controls = null;  // the control strip's updater for the current render

  var REPLAY_WALL_S = 12;   // at ×1 a full replay takes at most this long

  function prefersReduced() {
    try {
      return !!(global.matchMedia &&
                global.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (err) { return false; }
  }

  /* Cumulative start times per side: step k starts when steps 0..k-1 have
   * finished. A missing or zero latency contributes nothing to the clock but
   * is counted, so the view can say "timing was not recorded" instead of
   * silently drawing an instant step. */
  function sideTiming(report, side) {
    var steps = stepsOf(report, side);
    var t = 0, events = [], zeros = 0;
    steps.forEach(function (step, k) {
      var lat = step && typeof step.latency_s === "number" &&
                isFinite(step.latency_s) && step.latency_s > 0 ? step.latency_s : 0;
      if (step && !lat) zeros++;
      events.push({
        index: step && typeof step.index === "number" ? step.index : k,
        start: t, dur: lat, step: step,
      });
      t += lat;
    });
    return { events: events, total: t, zeros: zeros };
  }

  function evFor(lane, index) {
    if (index === null || index === undefined) return null;
    for (var i = 0; i < lane.events.length; i++) {
      if (lane.events[i].index === index) return lane.events[i];
    }
    return lane.events[index] || null;
  }

  function buildPlan(report) {
    var a = sideTiming(report, "a"), b = sideTiming(report, "b");
    var total = Math.max(a.total, b.total);
    // The first divergence as a wall-clock moment. The aligned pair's start
    // times may differ per side, so both are kept and drawn connected.
    var div = null;
    var list = (report && Array.isArray(report.divergences) ? report.divergences : [])
      .slice().sort(function (x, y) { return (x.rank || 99) - (y.rank || 99); });
    if (list.length) {
      var d = list[0];
      var ea = evFor(a, d.a_index), eb = evFor(b, d.b_index);
      var ta = ea ? ea.start : null, tb = eb ? eb.start : null;
      if (ta !== null || tb !== null) {
        div = { d: d, a: ta, b: tb,
                at: Math.min(ta === null ? Infinity : ta, tb === null ? Infinity : tb) };
      }
    }
    // Step boundaries, for the reduced-motion replay that jumps rather than
    // sweeps.
    var bounds = [0, total];
    [a, b].forEach(function (lane) {
      lane.events.forEach(function (ev) { bounds.push(ev.start); bounds.push(ev.start + ev.dur); });
    });
    if (div && isFinite(div.at)) bounds.push(div.at);
    bounds = bounds.filter(function (v, i, arr) { return arr.indexOf(v) === i; })
                   .sort(function (x, y) { return x - y; });
    return {
      a: a, b: b, total: total, zeros: a.zeros + b.zeros,
      compress: total > REPLAY_WALL_S ? total / REPLAY_WALL_S : 1,
      div: div, bounds: bounds,
    };
  }

  function rowIndexFor(report, side, index) {
    var rows = rowsOf(report);
    for (var i = 0; i < rows.length; i++) {
      if (rows[i][side + "_index"] === index) return i;
    }
    return -1;
  }

  // ------------------------------------------------------------- the replay

  function cancelReplay() {
    if (Replay.raf) {
      try { global.cancelAnimationFrame(Replay.raf); } catch (err) {}
      Replay.raf = 0;
    }
    Replay.playing = false;
    Replay.done = false;
    Replay.t = 0;
  }

  function displayT() {
    if (!Replay.reduced || !Plan) return Replay.t;
    // Reduced motion: land on the latest step boundary instead of sweeping.
    var t = 0;
    for (var i = 0; i < Plan.bounds.length; i++) {
      if (Plan.bounds[i] <= Replay.t + 1e-9) t = Plan.bounds[i]; else break;
    }
    return t;
  }

  /* Everything replay does is attribute/class churn on nodes the static
   * render created — it never adds or removes a node, which is what makes
   * "final state equals static render" checkable rather than hoped-for. */
  function applyReplay(t) {
    if (!Anim || !Plan) return;
    var EPS = 1e-6;
    var running = Replay.playing || (t > 0 && t < Plan.total - EPS);
    Anim.items.forEach(function (it) {
      var started = t + EPS >= it.start;
      var live = started && t < it.end - EPS;
      if (started) it.node.style.removeProperty("opacity");
      else it.node.style.opacity = "0.15";
      if (live && !Replay.reduced) it.node.classList.add("tj-live");
      else it.node.classList.remove("tj-live");
    });
    ["a", "b"].forEach(function (side) {
      var g = Anim.lanes[side];
      if (!g) return;
      var laneTotal = side === "a" ? Anim.totalA : Anim.totalB;
      // The finished lane dims while the other is still burning tokens.
      var dim = t > laneTotal + EPS && t < Plan.total - EPS;
      if (dim) g.style.opacity = "0.45";
      else g.style.removeProperty("opacity");
    });
    if (Anim.cursor && Anim.x) {
      if (running) {
        var cx = Anim.x(Math.min(t, Plan.total));
        Anim.cursor.setAttribute("x1", cx);
        Anim.cursor.setAttribute("x2", cx);
        Anim.cursor.setAttribute("visibility", "visible");
      } else {
        Anim.cursor.setAttribute("visibility", "hidden");
      }
    }
    if (Plan.div && isFinite(Plan.div.at) && Anim.divNodes.length) {
      var win = 0.9 * Plan.compress * Replay.speed;   // ≈0.9s of real time
      var flash = running && t + EPS >= Plan.div.at && t < Plan.div.at + win;
      Anim.divNodes.forEach(function (node) {
        if (flash && !Replay.reduced) node.classList.add("tj-flash");
        else node.classList.remove("tj-flash");
      });
    }
  }

  function resetReplayVisuals() {
    if (!Anim) return;
    Anim.items.forEach(function (it) {
      it.node.style.removeProperty("opacity");
      it.node.classList.remove("tj-live");
    });
    ["a", "b"].forEach(function (side) {
      if (Anim.lanes[side]) Anim.lanes[side].style.removeProperty("opacity");
    });
    if (Anim.cursor) Anim.cursor.setAttribute("visibility", "hidden");
    Anim.divNodes.forEach(function (node) { node.classList.remove("tj-flash"); });
  }

  function replayFrame(now) {
    Replay.raf = 0;
    // Generation guard: a frame queued before a re-render must do nothing.
    if (Replay.loopGen !== Replay.gen || !Anim || !Anim.svg.isConnected || !Plan) {
      Replay.playing = false;
      return;
    }
    if (!Replay.playing) return;
    var dt = Math.min(0.1, Math.max(0, (now - Replay.lastNow) / 1000));
    Replay.lastNow = now;
    Replay.t += dt * Plan.compress * Replay.speed;
    if (Replay.t >= Plan.total - 1e-9) {
      // Deterministic landing: the final frame is the static render.
      Replay.t = Plan.total;
      Replay.playing = false;
      Replay.done = true;
      resetReplayVisuals();
      if (Controls) { Controls.sync(); Controls.status(); }
      return;
    }
    applyReplay(displayT());
    if (Controls) Controls.status();
    Replay.raf = global.requestAnimationFrame(replayFrame);
  }

  function toggleReplay(ctx) {
    if (!Plan || Plan.total <= 0 || !Anim) return;
    if (Replay.playing) {
      Replay.playing = false;
      if (Replay.raf) {
        try { global.cancelAnimationFrame(Replay.raf); } catch (err) {}
        Replay.raf = 0;
      }
    } else {
      if (Replay.done || Replay.t >= Plan.total - 1e-9) { Replay.t = 0; Replay.done = false; }
      Replay.reduced = prefersReduced();
      Replay.playing = true;
      Replay.loopGen = Replay.gen;
      Replay.lastNow = global.performance && global.performance.now
        ? global.performance.now() : Date.now();
      applyReplay(displayT());
      Replay.raf = global.requestAnimationFrame(replayFrame);
      if (ctx && ctx.signal) ctx.signal("inspect");
    }
    if (Controls) { Controls.sync(); Controls.status(); }
  }

  var NO_TIMING = "no timing recorded in this trace";

  function controlsBar(ctx, repaint) {
    var timed = Plan && Plan.total > 0;

    function modeBtn(mode, text, title) {
      var btn = H("button", {
        text: text,
        "aria-pressed": View.mode === mode ? "true" : "false",
        title: mode === "time" && !timed ? NO_TIMING : title,
        onclick: function () {
          if (View.mode === mode || (mode === "time" && !timed)) return;
          View.mode = mode;
          if (ctx.signal) ctx.signal("inspect");
          repaint();
        },
      });
      if (mode === "time" && !timed) btn.setAttribute("disabled", "disabled");
      return btn;
    }

    var colBtn = modeBtn("columns", "columns",
      "Alignment view — matched steps share a column");
    var timeBtn = modeBtn("time", "time",
      "One shared wall-clock axis — see who finishes first");

    var playBtn = H("button", {
      class: "play", text: "⏵",
      "aria-label": "Replay both runs in proportional time",
      title: timed ? "Replay both runs in proportional time" : NO_TIMING,
      onclick: function () { toggleReplay(ctx); },
    });
    if (!timed) playBtn.setAttribute("disabled", "disabled");

    var speedSel = H("select", { title: "Replay speed", "aria-label": "Replay speed" });
    [1, 4, 16].forEach(function (v) {
      speedSel.appendChild(H("option", { value: String(v), text: "×" + v }));
    });
    speedSel.value = String(Replay.speed);
    speedSel.addEventListener("change", function () {
      Replay.speed = Number(speedSel.value) || 1;
      if (Controls) Controls.status();
    });
    if (!timed) speedSel.setAttribute("disabled", "disabled");

    var status = H("span", { class: "tj-status" });

    var bar = H("div", { class: "tj-ctl" }, [
      H("span", { class: "grp" }, [colBtn, timeBtn]),
      H("span", { class: "grp" }, [playBtn]),
      speedSel,
      status,
    ]);

    Controls = {
      sync: function () {
        colBtn.setAttribute("aria-pressed", View.mode === "columns" ? "true" : "false");
        timeBtn.setAttribute("aria-pressed", View.mode === "time" ? "true" : "false");
        playBtn.textContent = Replay.playing ? "❚❚" : Replay.done ? "↺" : "⏵";
        playBtn.setAttribute("title", !timed ? NO_TIMING :
          Replay.playing ? "Pause the replay" :
          Replay.done ? "Replay again from the start" :
          Replay.t > 0 ? "Resume the replay" : "Replay both runs in proportional time");
      },
      status: function () {
        if (!timed) { status.textContent = NO_TIMING; return; }
        if (Replay.playing || (Replay.t > 0 && !Replay.done)) {
          status.textContent = "t " + F.sec(displayT()) + " / " + F.sec(Plan.total) +
            (Replay.playing ? "" : " · paused");
        } else if (Replay.done) {
          status.textContent = "replayed " + F.sec(Plan.total) +
            " of wall-clock — the final state is the static picture";
        } else {
          status.textContent = Plan.compress > 1.01
            ? F.sec(Plan.total) + " wall-clock in ~" +
              Math.round(Plan.total / Plan.compress) + "s at ×1 (" +
              F.num(Plan.compress, 1) + "× compressed)"
            : "replay runs at true speed at ×1 (" + F.sec(Plan.total) + " total)";
        }
      },
    };
    Controls.sync();
    Controls.status();
    return bar;
  }

  // ------------------------------------------------------- the tracks block

  AgentDiff.block({
    id: "tracks",
    title: "Tracks",
    question: "How did the two runs unfold?",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) {
      var report = ctx.report;
      if (!report) return 0;
      if (!rowsOf(report).length) return 0;
      if (!stepsOf(report, "a").length && !stepsOf(report, "b").length) return 0;
      var divergent = (report.divergences || []).length;
      return divergent ? 1 : 0.86;
    },
    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      var rows = rowsOf(report);
      if (!rows.length) return ctx.empty(el, "This report carries no alignment, so there is nothing to lay side by side.");
      if (!stepsOf(report, "a").length && !stepsOf(report, "b").length) {
        return ctx.empty(el, "Neither trajectory recorded any steps.");
      }
      syncTask(ctx);

      Plan = buildPlan(report);
      if (Plan.total <= 0 && View.mode === "time") View.mode = "columns";

      el.appendChild(sideHeader(report));
      var readout = H("div", { class: "tj-read" });
      var extras = H("div");   // legends + captions, filled per mode
      var host = null;
      el.appendChild(controlsBar(ctx, function () {
        if (!host) return;
        host.innerHTML = "";
        drawTracks(host, measure(el), ctx, readout, extras);
      }));
      host = sized(el, ctx, function (box, avail) {
        drawTracks(box, avail, ctx, readout, extras);
      });
      el.appendChild(readout);
      el.appendChild(extras);
      subscribe(el, function () {
        host.innerHTML = "";
        drawTracks(host, measure(el), ctx, readout, extras);
      });
    },
  });

  function typeLegend() {
    var items = [{ heading: "step" }];
    TYPES.forEach(function (type) {
      items.push({ node: tinyGlyph(type, "a", null), text: TYPE_LABEL[type] });
    });
    return legend(items);
  }

  function qualityLegendItems() {
    return [
      { heading: "quality" },
      { node: tinyGlyph("search", "a", "good"), text: "good" },
      { node: tinyGlyph("search", "a", "weak"), text: "weak" },
      { node: tinyGlyph("search", "a", "bad"), text: "bad" },
      { node: tinyGlyph("search", "a", null), text: "unannotated" },
    ];
  }

  /* Every draw is a fresh generation: whatever replay was doing on the old
   * nodes is over, and its queued frames are orphaned by the gen bump. */
  function drawTracks(host, avail, ctx, readout, extras) {
    bind(ctx);
    var report = ctx.report;
    Replay.gen++;
    cancelReplay();
    Anim = null;
    Plan = buildPlan(report);
    if (View.mode === "time" && Plan.total <= 0) View.mode = "columns";
    if (View.mode === "time") drawTime(host, avail, ctx, readout, extras);
    else drawColumns(host, avail, ctx, readout, extras);
    if (Controls) { Controls.sync(); Controls.status(); }
    readout.innerHTML = "";
    readout.appendChild(H("i", { text: hint(report) }));
  }

  function drawColumns(host, avail, ctx, readout, extras) {
    bind(ctx);
    var report = ctx.report;
    var rows = rowsOf(report);
    var n = rows.length;
    var divs = divergenceMap(report);
    var root = rootInfo(report);
    var sel = resolved(report);

    var padL = 10, padR = 10;
    // 78px was a sensible ceiling when this lived in a column. In the hero
    // lane a short run then left half the width empty, which defeats the
    // point of giving it the width. Let a column grow to fill what is
    // actually available, capped where a step glyph stops gaining from more
    // room and the track starts reading as scattered dots.
    var roomy = Math.floor((avail - padL - padR) / Math.max(n, 1));
    var colW = Math.max(38, Math.min(avail > 900 ? 130 : 78, roomy));
    var W = padL + padR + colW * n;
    var R = 7, barMax = 22;
    var yA = 46, yB = 104, H_ = 152;
    var axisY = 145;

    function colX(i) { return padL + colW * i + colW / 2; }

    var maxTokens = 0;
    ["a", "b"].forEach(function (side) {
      stepsOf(report, side).forEach(function (step) {
        var t = step && step.tokens;
        if (typeof t === "number" && isFinite(t) && t > maxTokens) maxTokens = t;
      });
    });

    var svgEl = S("svg", {
      class: "tj", width: W, height: H_, viewBox: "0 0 " + W + " " + H_,
      role: "img",
      "aria-label": "Step tracks for " + agentName(report, "a") + " and " + agentName(report, "b"),
    });

    // --- selected column wash (drawn first so marks sit on top)
    if (sel.row >= 0) {
      svgEl.appendChild(S("rect", {
        x: colX(sel.row) - colW / 2, y: 2, width: colW, height: H_ - 16,
        fill: C.ink, "fill-opacity": 0.055, rx: 4,
      }));
    }

    // --- divergence rules
    var divFlash = [];
    Object.keys(divs).forEach(function (key) {
      var i = Number(key);
      var mark = divs[key];
      var dg = S("g");
      dg.appendChild(tick(colX(i), 12, colX(i), H_ - 18, C.bad, mark.primary ? 1.2 : 1,
                          mark.primary ? "3 3" : "1 4"));
      if (mark.primary) {
        dg.appendChild(S("polygon", {
          points: pts([[colX(i) - 4.5, 4], [colX(i) + 4.5, 4], [colX(i), 11]]),
          fill: C.bad,
        }));
        if (colW >= 42) {
          dg.appendChild(label(colX(i) + 8, 10, "d" + (mark.div.rank || 1), C.bad, 8.5, "start"));
        }
        if ((mark.div.rank || 99) === (Plan.div && Plan.div.d ? (Plan.div.d.rank || 99) : -1)) {
          divFlash.push(dg);
        }
      }
      svgEl.appendChild(dg);
    });

    // --- track baselines
    svgEl.appendChild(tick(padL, yA, W - padR, yA, C.grid, 1));
    svgEl.appendChild(tick(padL, yB, W - padR, yB, C.grid, 1));

    // --- connectors
    rows.forEach(function (row, i) {
      var x = colX(i);
      var hasA = row.a_index !== null && row.a_index !== undefined;
      var hasB = row.b_index !== null && row.b_index !== undefined;
      if (hasA && hasB) {
        var drift = row.op !== "match";
        svgEl.appendChild(tick(x, yA + R + 2, x, yB - R - 2,
                               drift ? C.warn : C.grid, drift ? 1.2 : 1, drift ? "3 2" : null));
      } else if (hasA || hasB) {
        var from = hasA ? yA + R + 2 : yB - R - 2;
        var to = hasA ? yA + 16 : yB - 16;
        svgEl.appendChild(tick(x, from, x, to, sideColor(hasA ? "a" : "b"), 1, "2 2"));
        // A hollow dot on the baseline, smaller than any step glyph: this
        // agent has nothing at all here, which is not the same as a cheap step.
        var absentY = hasA ? yB : yA;
        svgEl.appendChild(S("circle", {
          cx: x, cy: absentY, r: 2.6, fill: C.surface, stroke: C.muted,
          "stroke-width": 1, "stroke-dasharray": "1.6 1.4",
        }));
      }
    });

    // --- steps, grouped per lane so replay can dim a finished lane whole
    var laneG = { a: S("g"), b: S("g") };
    var animItems = [];
    rows.forEach(function (row, i) {
      var x = colX(i);
      ["a", "b"].forEach(function (side) {
        var index = row[side + "_index"];
        if (index === null || index === undefined) return;
        var step = stepAt(report, side, index);
        if (!step) return;
        var y = side === "a" ? yA : yB;
        var dir = side === "a" ? -1 : 1;
        var g = S("g");

        // token bar, growing away from the middle
        if (maxTokens > 0 && typeof step.tokens === "number" && step.tokens > 0) {
          var hgt = Math.max(1.5, (step.tokens / maxTokens) * barMax);
          var bw = Math.max(3, Math.min(7, colW - 22));
          g.appendChild(S("rect", {
            x: x - bw / 2, y: dir < 0 ? y - R - 3 - hgt : y + R + 3,
            width: bw, height: hgt, fill: sideColor(side), "fill-opacity": 0.42, rx: 1,
          }));
        }

        if (root && root.side === side && root.index === index) {
          g.appendChild(S("circle", {
            cx: x, cy: y, r: R + 4.5, fill: "none", stroke: C.bad, "stroke-width": 1.2,
          }));
        }
        if (sel.row === i && (sel.side === side || sel.side === null)) {
          g.appendChild(S("circle", {
            cx: x, cy: y, r: R + 2.5, fill: "none", stroke: C.ink,
            "stroke-width": 1, "stroke-opacity": 0.55,
          }));
        }

        g.appendChild(styleGlyph(glyphNode(step.type, x, y, R),
                                 qualityStyle(step.quality, side)));
        if (colW >= 36) {
          g.appendChild(label(x + R + 3.5, y + 3, String(index), C.muted, 8, "start"));
        }
        laneG[side].appendChild(g);
        var ev = evFor(Plan[side], index);
        animItems.push({
          node: g, side: side,
          start: ev ? ev.start : 0,
          end: ev ? ev.start + ev.dur : 0,
        });
      });
    });
    svgEl.appendChild(laneG.a);
    svgEl.appendChild(laneG.b);

    // --- row axis
    svgEl.appendChild(tick(padL, axisY - 9, W - padR, axisY - 9, C.grid, 1));
    rows.forEach(function (row, i) {
      svgEl.appendChild(label(colX(i), axisY, String(i), C.muted, 8.5));
    });
    svgEl.appendChild(label(padL, axisY - 13, "row", C.muted, 8, "start"));

    // --- hit targets, one per side per column
    var midY = (yA + yB) / 2;
    rows.forEach(function (row, i) {
      ["a", "b"].forEach(function (side) {
        var index = row[side + "_index"];
        var hit = S("rect", {
          class: "tj-hit",
          x: colX(i) - colW / 2, y: side === "a" ? 0 : midY,
          width: colW, height: side === "a" ? midY : H_ - midY - 12,
          fill: C.ink, "fill-opacity": 0, "pointer-events": "all",
        });
        hit.addEventListener("mouseenter", function () {
          hit.setAttribute("fill-opacity", 0.05);
          readout.innerHTML = "";
          readout.appendChild(readoutFor(report, row, i, side, index));
          if (ctx.signal) ctx.signal("hover");
        });
        hit.addEventListener("mouseleave", function () {
          hit.setAttribute("fill-opacity", 0);
          readout.innerHTML = "";
          readout.appendChild(H("i", { text: hint(report) }));
        });
        hit.addEventListener("click", function () { select(i, side, ctx); });
        svgEl.appendChild(hit);
      });
    });

    Anim = {
      svg: svgEl, items: animItems, lanes: laneG, cursor: null, x: null,
      divNodes: divFlash, totalA: Plan.a.total, totalB: Plan.b.total,
    };

    var wrap = H("div", { class: W > avail ? "scroll-x" : "" });
    wrap.appendChild(svgEl);
    host.appendChild(wrap);
    fillColumnsExtras(extras);
  }

  function fillColumnsExtras(extras) {
    extras.innerHTML = "";
    extras.appendChild(typeLegend());
    extras.appendChild(legend(qualityLegendItems().concat([
      { heading: "link" },
      { node: swatchLine(C.axis, null), text: "match" },
      { node: swatchLine(C.warn, "3 2"), text: "drift" },
      { node: swatchLine(C.bad, "2 2"), text: "divergence" },
      { node: absentSwatch(), text: "no counterpart" },
    ])));
    extras.appendChild(H("p", {
      class: "caveat",
      text: "One column per alignment row: matched steps sit in the same column, so a step with " +
            "no counterpart leaves a gap on the other track. Bar length is tokens spent on that step.",
    }));
  }

  // ------------------------------------------------------ the time view
  //
  // Both runs drawn against one shared wall-clock x-axis: each step is a bar
  // from its cumulative start to start + latency, A's lane above, B's below,
  // same glyph and quality treatment as the columns view. What this buys
  // over alignment columns is the superimposition itself — one agent
  // literally finishes earlier, and the gap between the two finish ticks is
  // the price (or the saving) in seconds.

  function niceTimeStep(total) {
    var candidates = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    for (var i = 0; i < candidates.length; i++) {
      if (total / candidates[i] <= 8) return candidates[i];
    }
    return Math.ceil(total / 8);
  }

  function secLabel(v, stepv) {
    return (stepv < 1 ? F.num(v, 2).replace(/\.?0+$/, "") : String(Math.round(v * 10) / 10)) + "s";
  }

  function drawTime(host, avail, ctx, readout, extras) {
    bind(ctx);
    var report = ctx.report;
    var sel = resolved(report);
    var root = rootInfo(report);
    var total = Plan.total;

    var padL = 30, padR = 14;
    var W = Math.max(avail, 320);
    var plotW = W - padL - padR;
    function x(t) { return padL + (Math.max(0, Math.min(total, t)) / total) * plotW; }

    var barH = 16;
    var yA = 56, yB = 118;
    var midY = (yA + yB) / 2;
    var axisY = 160, H_ = 168;

    var svgEl = S("svg", {
      class: "tj", width: W, height: H_, viewBox: "0 0 " + W + " " + H_,
      role: "img",
      "aria-label": "Wall-clock timeline for " + agentName(report, "a") + " and " +
                    agentName(report, "b") + " over " + F.sec(total),
    });

    // --- time axis
    var stepv = niceTimeStep(total);
    svgEl.appendChild(tick(padL, axisY - 10, W - padR, axisY - 10, C.grid, 1));
    for (var k = 0; k * stepv <= total + 1e-9; k++) {
      var tv = k * stepv;
      var tx = x(tv);
      svgEl.appendChild(tick(tx, 16, tx, axisY - 10, C.grid, 1, "1 3"));
      svgEl.appendChild(label(tx, axisY, secLabel(tv, stepv), C.muted, 8.5));
    }

    // --- lane baselines + letters
    [["a", yA], ["b", yB]].forEach(function (pair) {
      svgEl.appendChild(tick(padL, pair[1], W - padR, pair[1], C.grid, 1));
      svgEl.appendChild(label(8, pair[1] + 3, pair[0].toUpperCase(), sideColor(pair[0]), 9.5, "start"));
    });

    // --- step bars, grouped per lane for replay
    var laneG = { a: S("g"), b: S("g") };
    var animItems = [];
    ["a", "b"].forEach(function (side) {
      var lane = Plan[side];
      var y = side === "a" ? yA : yB;
      lane.events.forEach(function (ev) {
        var step = ev.step || {};
        var zero = ev.dur <= 0;
        var bx = x(ev.start);
        // A step with no recorded latency still gets a minimum visible
        // width — dashed, so it does not read as a measured instant.
        var bw = zero ? 3 : Math.max(2, x(ev.start + ev.dur) - bx);
        var st = qualityStyle(step.quality, side);
        var g = S("g");
        var bar = S("rect", {
          x: bx + 0.3, y: y - barH / 2, width: Math.max(1.4, bw - 0.6),
          height: barH, rx: 2,
        });
        styleGlyph(bar, st);
        if (zero) bar.setAttribute("stroke-dasharray", "2 2");
        g.appendChild(bar);
        if (bw >= 15) {
          g.appendChild(styleGlyph(glyphNode(step.type, bx + bw / 2, y, 4.6), st));
        }
        var rowIdx = rowIndexFor(report, side, ev.index);
        if (root && root.side === side && root.index === ev.index) {
          g.appendChild(S("rect", {
            x: bx - 2, y: y - barH / 2 - 2.5, width: bw + 4, height: barH + 5,
            rx: 3.5, fill: "none", stroke: C.bad, "stroke-width": 1.2,
          }));
        }
        if (rowIdx === sel.row && rowIdx >= 0 && (sel.side === side || sel.side === null)) {
          g.appendChild(S("rect", {
            x: bx - 4, y: y - barH / 2 - 4.5, width: bw + 8, height: barH + 9,
            rx: 4.5, fill: "none", stroke: C.ink, "stroke-width": 1, "stroke-opacity": 0.55,
          }));
        }
        laneG[side].appendChild(g);
        animItems.push({ node: g, side: side, start: ev.start, end: ev.start + ev.dur });
      });
    });
    svgEl.appendChild(laneG.a);
    svgEl.appendChild(laneG.b);

    // --- finish ticks: the point of the whole view
    ["a", "b"].forEach(function (side) {
      var tt = Plan[side].total;
      if (tt <= 0) return;
      var fx = x(tt);
      var y = side === "a" ? yA : yB;
      svgEl.appendChild(tick(fx, y - barH / 2 - 6, fx, y + barH / 2 + 6, sideColor(side), 1.5));
      var anchor = fx > W - 76 ? "end" : "start";
      var lx = anchor === "end" ? fx - 4 : fx + 4;
      var ly = side === "a" ? y - barH / 2 - 10 : y + barH / 2 + 15;
      svgEl.appendChild(label(lx, ly, "finish " + F.sec(tt), sideColor(side), 8.5, anchor));
    });

    // --- the first divergence as a wall-clock moment, both sides connected
    var divFlash = [];
    if (Plan.div && isFinite(Plan.div.at)) {
      var dg = S("g");
      var dxA = Plan.div.a === null ? null : x(Plan.div.a);
      var dxB = Plan.div.b === null ? null : x(Plan.div.b);
      if (dxA !== null) dg.appendChild(tick(dxA, yA - barH / 2 - 7, dxA, yA + barH / 2 + 7, C.bad, 1.2, "3 3"));
      if (dxB !== null) dg.appendChild(tick(dxB, yB - barH / 2 - 7, dxB, yB + barH / 2 + 7, C.bad, 1.2, "3 3"));
      if (dxA !== null && dxB !== null) {
        dg.appendChild(S("line", {
          x1: dxA, y1: yA + barH / 2 + 7, x2: dxB, y2: yB - barH / 2 - 7,
          stroke: C.bad, "stroke-width": 1, "stroke-dasharray": "3 3", "stroke-opacity": 0.8,
        }));
      }
      var fx0 = dxA !== null ? dxA : dxB;
      dg.appendChild(S("polygon", {
        points: pts([[fx0 - 4.5, 6], [fx0 + 4.5, 6], [fx0, 13]]), fill: C.bad,
      }));
      var ta = fx0 > W - 110 ? "end" : "start";
      dg.appendChild(label(ta === "end" ? fx0 - 7 : fx0 + 7, 12,
        "d" + (Plan.div.d.rank || 1) + " at " + F.sec(Plan.div.at), C.bad, 8.5, ta));
      svgEl.appendChild(dg);
      divFlash.push(dg);
    }

    // --- replay cursor: exists from the start, hidden until replay runs, so
    // the replay adds and removes nothing from the DOM
    var cursor = S("line", {
      x1: padL, y1: 14, x2: padL, y2: axisY - 10, stroke: C.ink,
      "stroke-width": 1, "stroke-opacity": 0.65, visibility: "hidden",
    });
    svgEl.appendChild(cursor);

    // --- hit targets, one per step bar
    ["a", "b"].forEach(function (side) {
      var lane = Plan[side];
      var y0 = side === "a" ? 14 : midY;
      var hh = side === "a" ? midY - 14 : axisY - 10 - midY;
      lane.events.forEach(function (ev) {
        var bx = x(ev.start);
        var bw = ev.dur <= 0 ? 6 : Math.max(6, x(ev.start + ev.dur) - bx);
        var rowIdx = rowIndexFor(report, side, ev.index);
        var hit = S("rect", {
          class: "tj-hit", x: bx - 1, y: y0, width: bw + 2, height: hh,
          fill: C.ink, "fill-opacity": 0, "pointer-events": "all",
        });
        hit.addEventListener("mouseenter", function () {
          hit.setAttribute("fill-opacity", 0.05);
          readout.innerHTML = "";
          readout.appendChild(readoutTimeFor(report, side, ev));
          if (ctx.signal) ctx.signal("hover");
        });
        hit.addEventListener("mouseleave", function () {
          hit.setAttribute("fill-opacity", 0);
          readout.innerHTML = "";
          readout.appendChild(H("i", { text: hint(report) }));
        });
        hit.addEventListener("click", function () {
          if (rowIdx >= 0) select(rowIdx, side, ctx);
        });
        svgEl.appendChild(hit);
      });
    });

    Anim = {
      svg: svgEl, items: animItems, lanes: laneG, cursor: cursor, x: x,
      divNodes: divFlash, totalA: Plan.a.total, totalB: Plan.b.total,
    };

    var wrap = H("div", { class: W > avail ? "scroll-x" : "" });
    wrap.appendChild(svgEl);
    host.appendChild(wrap);
    fillTimeExtras(extras, report);
  }

  function readoutTimeFor(report, side, ev) {
    var step = ev.step || {};
    var bits = [
      side.toUpperCase() + "·" + ev.index,
      step.type || "?",
      step.name ? "“" + step.name + "”" : null,
      "starts " + F.sec(ev.start),
      ev.dur > 0 ? F.sec(ev.dur) : "timing not recorded",
      F.tokens(step.tokens),
      step.quality || "unannotated",
    ].filter(Boolean);
    return H("span", { text: bits.join("  ·  ") });
  }

  function fillTimeExtras(extras, report) {
    extras.innerHTML = "";
    extras.appendChild(typeLegend());
    extras.appendChild(legend(qualityLegendItems().concat([
      { heading: "marks" },
      { node: swatchLine(C.bad, "3 3"), text: "first divergence" },
      { node: swatchLine(C.a, null), text: "finish" },
    ])));
    var trade = report && report.tradeoff;
    if (trade && trade.statement) {
      extras.appendChild(H("div", { class: "tj-trade" }, [
        H("b", { text: "speed vs quality" }),
        H("span", { text: trade.statement }),
      ]));
    }
    if (Plan && Plan.zeros > 0) {
      extras.appendChild(H("p", {
        class: "caveat",
        text: Plan.zeros + " step(s) carry no recorded latency; each is drawn dashed at a " +
              "minimum visible width at its cumulative position — timing was not recorded, " +
              "which is not the same as instant.",
      }));
    }
    extras.appendChild(H("p", {
      class: "caveat",
      text: "Both runs against one shared wall-clock axis: each bar runs from a step's start " +
            "to start + latency, cumulatively, so the shorter lane simply stops where that " +
            "agent finished. Bar length is time here, not tokens.",
    }));
  }

  function hint(report) {
    var sel = resolved(report);
    if (sel.pinned) return "row " + sel.row + " selected — shown in Step detail";
    return "click a step to open it in Step detail" + (sel.why ? " (showing " + sel.why + ")" : "");
  }

  function readoutFor(report, row, i, side, index) {
    var frag = H("span");
    if (index === null || index === undefined) {
      frag.appendChild(H("i", {
        text: side.toUpperCase() + " · row " + i + " · no step here — " +
              agentName(report, side) + " never did this (" + row.op + ")",
      }));
      return frag;
    }
    var step = stepAt(report, side, index) || {};
    var bits = [
      side.toUpperCase() + "·" + index,
      step.type || "?",
      step.name ? "“" + step.name + "”" : null,
      F.tokens(step.tokens),
      F.sec(step.latency_s),
      step.quality || "unannotated",
    ].filter(Boolean);
    frag.appendChild(H("span", { text: bits.join("  ·  ") }));
    return frag;
  }

  // ======================================================= alignment-ribbon

  AgentDiff.block({
    id: "alignment-ribbon",
    title: "Alignment ribbon",
    question: "Where did they match, drift, and go it alone?",
    group: "trajectory",
    size: "normal",
    relevance: function (ctx) {
      var rows = rowsOf(ctx.report);
      if (!rows.length) return 0;
      var off = rows.filter(function (r) { return r.op !== "match"; }).length;
      return Math.min(1, 0.5 + 0.6 * (off / rows.length));
    },
    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      var rows = rowsOf(report);
      if (!rows.length) return ctx.empty(el, "This report carries no alignment rows.");
      syncTask(ctx);

      var counts = { match: 0, drift: 0, a_only: 0, b_only: 0 };
      rows.forEach(function (r) { if (counts[r.op] !== undefined) counts[r.op]++; else counts[r.op] = 1; });

      var host = sized(el, ctx, function (box, avail) { drawRibbon(box, avail, ctx); });
      subscribe(el, function () { host.innerHTML = ""; drawRibbon(host, measure(el), ctx); });

      el.appendChild(legend([
        { heading: "op" },
        { node: opSwatch("match"), text: "match " + counts.match },
        { node: opSwatch("drift"), text: "drift " + counts.drift },
        { node: opSwatch("a_only"), text: "A only " + (counts.a_only || 0) },
        { node: opSwatch("b_only"), text: "B only " + (counts.b_only || 0) },
      ]));
      var sem = semanticOf(report);
      el.appendChild(H("p", {
        class: "caveat",
        text: "Similarity is the aligner's lexical score for the paired steps. One-sided rows have " +
              "nothing to compare, so the line breaks rather than dropping to zero." +
              (sem && sem.first_semantic_break !== null && sem.first_semantic_break !== undefined
                ? " The dashed red rule is row " + sem.first_semantic_break +
                  ", where meaning first parted from wording."
                : ""),
      }));
    },
  });

  /* Op is encoded by two channels at once, because the side colours and the
   * drift colour are close cousins in this palette: full-height means both
   * agents were present (outlined when they drifted), half-height means one
   * side acted alone, and it sits on that side's half of the band. */
  function opMarks(op, x, y, w, h) {
    if (op === "drift") {
      return [S("rect", { x: x, y: y, width: w, height: h, rx: 1.5,
                          fill: C.warn, "fill-opacity": 0.26,
                          stroke: C.warn, "stroke-width": 1 })];
    }
    if (op === "a_only" || op === "b_only") {
      var side = op === "a_only" ? "a" : "b";
      return [S("rect", {
        x: x, y: op === "a_only" ? y : y + h / 2, width: w, height: h / 2, rx: 1.5,
        fill: sideColor(side), "fill-opacity": 0.75,
      })];
    }
    return [S("rect", { x: x, y: y, width: w, height: h, rx: 1.5,
                        fill: C.ink, "fill-opacity": 0.13 })];
  }

  function opSwatch(op) {
    return S("svg", { width: 14, height: 12, viewBox: "0 0 14 12", "aria-hidden": "true" },
             opMarks(op, 1, 1, 12, 10));
  }

  function drawRibbon(host, avail, ctx) {
    bind(ctx);
    var report = ctx.report;
    var rows = rowsOf(report);
    var n = rows.length;
    var sem = semanticOf(report);
    var brk = sem && typeof sem.first_semantic_break === "number" ? sem.first_semantic_break : null;
    var sel = resolved(report);

    var gut = 22, padR = 8;
    var colW = Math.max(20, Math.min(64, Math.floor((avail - gut - padR) / Math.max(n, 1))));
    var W = gut + padR + colW * n;
    var top = 14, plotH = 54;
    var bandY = top + plotH + 8, bandH = 18;
    var axisY = bandY + bandH + 13;
    var H_ = axisY + 5;

    function colX(i) { return gut + colW * i + colW / 2; }
    function y(v) { return top + plotH - Math.max(0, Math.min(1, v)) * plotH; }

    var svgEl = S("svg", {
      class: "tj", width: W, height: H_, viewBox: "0 0 " + W + " " + H_,
      role: "img", "aria-label": "Alignment similarity and operation per row",
    });

    // gridlines
    [0, 0.5, 1].forEach(function (v) {
      svgEl.appendChild(tick(gut, y(v), W - padR, y(v), C.grid, 1, v === 0.5 ? "2 3" : null));
      svgEl.appendChild(label(gut - 3, y(v) + 3, v === 0.5 ? ".5" : String(v), C.muted, 8, "end"));
    });

    if (sel.row >= 0) {
      svgEl.appendChild(S("rect", {
        x: colX(sel.row) - colW / 2, y: top - 6, width: colW, height: (axisY - 8) - (top - 6),
        fill: C.ink, "fill-opacity": 0.055, rx: 3,
      }));
    }

    // step chart, broken across one-sided rows
    var run = [];
    function flush() {
      if (run.length) {
        var d = "";
        run.forEach(function (p, k) {
          d += (k ? "L" : "M") + (p.x0).toFixed(1) + " " + p.y.toFixed(1) +
               "L" + (p.x1).toFixed(1) + " " + p.y.toFixed(1);
          if (k < run.length - 1) d += "L" + (p.x1).toFixed(1) + " " + run[k + 1].y.toFixed(1);
        });
        svgEl.appendChild(S("path", { d: d, fill: "none", stroke: C.ink, "stroke-width": 1.3,
                                      "stroke-opacity": 0.8, "stroke-linejoin": "round" }));
      }
      run = [];
    }
    rows.forEach(function (row, i) {
      var both = row.a_index !== null && row.a_index !== undefined &&
                 row.b_index !== null && row.b_index !== undefined;
      if (!both) { flush(); return; }
      var v = typeof row.similarity === "number" ? row.similarity : 0;
      run.push({ x0: colX(i) - colW * 0.4, x1: colX(i) + colW * 0.4, y: y(v) });
    });
    flush();

    rows.forEach(function (row, i) {
      var both = row.a_index !== null && row.a_index !== undefined &&
                 row.b_index !== null && row.b_index !== undefined;
      if (!both) return;
      var v = typeof row.similarity === "number" ? row.similarity : 0;
      svgEl.appendChild(S("circle", {
        cx: colX(i), cy: y(v), r: 2.4,
        fill: row.op === "match" ? C.ink : C.warn,
        stroke: C.surface, "stroke-width": 0.8,
      }));
      if (colW >= 40) {
        svgEl.appendChild(label(colX(i), y(v) - 6, F.num(v, 2).replace(/^0/, "."), C.muted, 8));
      }
    });

    // op band — A-only sits high, B-only low, echoing the tracks
    rows.forEach(function (row, i) {
      opMarks(row.op, colX(i) - colW * 0.42, bandY, colW * 0.84, bandH).forEach(function (node) {
        svgEl.appendChild(node);
      });
      svgEl.appendChild(label(colX(i), axisY, String(i), C.muted, 8.5));
    });
    svgEl.appendChild(label(gut - 3, bandY + bandH - 3, "op", C.muted, 8, "end"));

    if (brk !== null && brk >= 0 && brk < n) {
      svgEl.appendChild(tick(colX(brk), top - 8, colX(brk), axisY - 9, C.bad, 1.2, "3 3"));
      svgEl.appendChild(S("polygon", {
        points: pts([[colX(brk) - 4, top - 12], [colX(brk) + 4, top - 12], [colX(brk), top - 6]]),
        fill: C.bad,
      }));
    }

    rows.forEach(function (row, i) {
      var hit = S("rect", {
        class: "tj-hit", x: colX(i) - colW / 2, y: 0, width: colW, height: H_,
        fill: C.ink, "fill-opacity": 0, "pointer-events": "all",
      });
      hit.addEventListener("mouseenter", function () {
        hit.setAttribute("fill-opacity", 0.05);
        if (ctx.signal) ctx.signal("hover");
      });
      hit.addEventListener("mouseleave", function () { hit.setAttribute("fill-opacity", 0); });
      hit.addEventListener("click", function () {
        select(i, row.op === "a_only" ? "a" : row.op === "b_only" ? "b" : null, ctx);
      });
      var title = S("title", {
        text: "row " + i + " · " + row.op + " · similarity " + F.num(row.similarity, 2),
      });
      hit.appendChild(title);
      svgEl.appendChild(hit);
    });

    var wrap = H("div", { class: W > avail ? "scroll-x" : "" });
    wrap.appendChild(svgEl);
    host.appendChild(wrap);
  }

  // =========================================================== step-detail

  AgentDiff.block({
    id: "step-detail",
    title: "Step detail",
    question: "What exactly happened at this step?",
    group: "trajectory",
    size: "normal",
    relevance: function (ctx) {
      var report = ctx.report;
      if (!report || !rowsOf(report).length) return 0;
      // With a failure attributed, the text of the step that caused it is the
      // next thing worth reading after the tracks themselves.
      return rootInfo(report) ? 0.94 : 0.62;
    },
    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      if (!rowsOf(report).length) return ctx.empty(el, "No alignment rows to open.");
      syncTask(ctx);

      var host = ctx.h("div");
      el.appendChild(host);
      function paint() {
        host.innerHTML = "";
        drawDetail(host, ctx);
      }
      paint();
      subscribe(el, paint);
    },
  });

  function drawDetail(host, ctx) {
    bind(ctx);
    var report = ctx.report;
    var rows = rowsOf(report);
    var sel = resolved(report);
    var row = rows[sel.row];
    if (!row) return ctx.empty(host, "That alignment row is gone.");
    var semRow = semanticByRow(report)[sel.row];
    var root = rootInfo(report);
    var divs = divergenceMap(report);

    var head = H("div", { class: "tj-head" }, [
      H("span", { class: "tag mono", text: "row " + sel.row }),
      H("span", { class: "tag " + (row.op === "match" ? "good" : row.op === "drift" ? "warn" : row.op === "a_only" ? "a" : "b"),
                  text: row.op }),
      row.a_index !== null && row.a_index !== undefined && row.b_index !== null && row.b_index !== undefined
        ? H("span", { class: "mono", style: { color: "var(--ink-3)" },
                      text: "lexical " + F.num(row.similarity, 2) +
                            (semRow ? " · semantic " + F.num(semRow.semantic, 2) : "") })
        : null,
      divs[sel.row] ? H("span", { class: "tag bad", text: "divergence " + (divs[sel.row].div.rank || 1) }) : null,
      H("span", { class: "sp" }),
      H("div", { class: "tj-nav" }, [
        navButton("‹", sel.row > 0, function () { select(sel.row - 1, null, ctx); }),
        navButton("›", sel.row < rows.length - 1, function () { select(sel.row + 1, null, ctx); }),
      ]),
    ]);
    host.appendChild(head);

    var cols = H("div", { class: "tj-cols" });
    ["a", "b"].forEach(function (side) {
      cols.appendChild(detailPane(report, side, row, root, ctx));
    });
    host.appendChild(cols);

    if (divs[sel.row] && divs[sel.row].div.summary) {
      host.appendChild(H("div", { class: "tj-note", text: divs[sel.row].div.summary }));
    }
    if (root && root.row === sel.row && root.explanation) {
      host.appendChild(H("p", {
        class: "caveat",
        text: "Attributed root cause (" + (root.category || "uncategorised") + "): " + root.explanation,
      }));
    }
    host.appendChild(H("p", {
      class: "caveat",
      text: sel.pinned
        ? "Showing the step you selected in Tracks or the ribbon."
        : "Nothing selected yet, so this is " + (sel.why || "the first row") +
          ". Click any step in Tracks to change it.",
    }));
  }

  function navButton(text, enabled, onclick) {
    var attrs = { text: text, onclick: function () { if (enabled) onclick(); } };
    var btn = H("button", attrs);
    if (!enabled) btn.setAttribute("disabled", "disabled");
    return btn;
  }

  function detailPane(report, side, row, root, ctx) {
    var index = row[side + "_index"];
    var step = stepAt(report, side, index);
    var pane = H("div", { class: "tj-pane" + (step ? "" : " absent") });
    pane.appendChild(H("h4", null, [
      H("i", { class: "tj-chip", style: { background: sideColor(side) } }),
      H("span", { text: side.toUpperCase() + " · " + agentName(report, side) }),
      step ? H("span", { class: "mono", style: { marginLeft: "auto", color: "var(--ink-3)" },
                         text: "step " + index }) : null,
    ]));

    if (!step) {
      pane.appendChild(H("div", {
        class: "empty",
        text: agentName(report, side) + " has no step in this row — the other agent did this alone.",
      }));
      return pane;
    }

    var isRoot = root && root.side === side && root.index === index;
    pane.appendChild(H("div", { class: "tj-head", style: { marginBottom: "4px" } }, [
      H("span", { class: "tag", text: step.type || "step" }),
      step.name ? H("span", { class: "mono", text: step.name }) : null,
      H("span", {
        class: "tag " + (step.quality === "good" ? "good" : step.quality === "bad" ? "bad" :
                         step.quality === "weak" ? "warn" : ""),
        text: step.quality || "unannotated",
      }),
      isRoot ? H("span", { class: "tag bad", text: "root cause" }) : null,
    ]));

    pane.appendChild(H("dl", { class: "kv" }, [
      H("dt", { text: "tokens" }), H("dd", { text: F.int(step.tokens) }),
      H("dt", { text: "latency" }), H("dd", { text: F.sec(step.latency_s) }),
    ]));

    pane.appendChild(H("div", { class: "tj-lbl", text: "input" }));
    pane.appendChild(H("div", { class: "tj-text", text: textOr(step.input) }));
    pane.appendChild(H("div", { class: "tj-lbl", text: "output" }));
    pane.appendChild(H("div", { class: "tj-text", text: textOr(step.output) }));
    if (step.note) pane.appendChild(H("div", { class: "tj-note", text: step.note }));

    if (ctx && ctx.signal) {
      pane.addEventListener("mouseenter", function () { ctx.signal("hover"); }, { once: true });
    }
    return pane;
  }

  function textOr(value) {
    if (value === null || value === undefined || String(value) === "") return "— nothing recorded —";
    return String(value);
  }

  // ========================================================== semantic-rows

  AgentDiff.block({
    id: "semantic-rows",
    title: "Meaning vs wording",
    question: "Did meaning diverge from wording?",
    group: "trajectory",
    size: "normal",
    relevance: function (ctx) {
      var sem = semanticOf(ctx.report);
      if (!sem || !Array.isArray(sem.rows) || sem.rows.length < 2) return 0;
      var gap = 0;
      sem.rows.forEach(function (r) {
        var d = (Number(r.lexical) || 0) - (Number(r.semantic) || 0);
        if (d > gap) gap = d;
      });
      var broke = sem.first_semantic_break !== null && sem.first_semantic_break !== undefined;
      return Math.min(1, (broke ? 0.66 : 0.36) + gap * 0.4);
    },
    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      var sem = semanticOf(report);
      if (!sem) return ctx.empty(el, "This report carries no semantic analysis.");
      if (!Array.isArray(sem.rows) || !sem.rows.length) {
        return ctx.empty(el, "No alignment row pairs both sides, so meaning has nothing to compare.");
      }
      syncTask(ctx);

      var host = sized(el, ctx, function (box, avail) { drawSemantic(box, avail, ctx); });
      subscribe(el, function () { host.innerHTML = ""; drawSemantic(host, measure(el), ctx); });

      el.appendChild(legend([
        { node: swatchLine(C.muted, "3 2"), text: "lexical (wording)" },
        { node: swatchLine(C.ink, null), text: "semantic (meaning)" },
        { fill: C.bad, opacity: 0.35, text: "meaning below wording" },
        { fill: C.good, opacity: 0.35, text: "meaning above wording" },
      ]));
      if (sem.narrative) el.appendChild(H("p", { class: "caveat", text: sem.narrative }));
      el.appendChild(H("p", {
        class: "caveat",
        text: "Semantic similarity is TF-IDF cosine over the paired step texts; a break is the first " +
              "row below 0.5. Rows are alignment rows with both sides present — " +
              (Array.isArray(sem.methods) ? sem.methods.join(" + ") : "tfidf_cosine") + ".",
      }));
    },
  });

  function swatchLine(stroke, dash) {
    var line = tick(1, 5, 15, 5, stroke, 1.6, dash);
    return S("svg", { width: 16, height: 10, viewBox: "0 0 16 10", "aria-hidden": "true" }, [line]);
  }

  function drawSemantic(host, avail, ctx) {
    bind(ctx);
    var report = ctx.report;
    var sem = semanticOf(report);
    var rows = sem.rows.filter(function (r) { return r && typeof r.row === "number"; });
    var n = rows.length;
    var brk = typeof sem.first_semantic_break === "number" ? sem.first_semantic_break : null;

    var gut = 22, padR = 10;
    var colW = Math.max(30, Math.min(84, Math.floor((avail - gut - padR) / Math.max(n, 1))));
    var W = gut + padR + colW * n;
    var top = 16, plotH = 84;
    var axisY = top + plotH + 14;
    var H_ = axisY + 6;

    function colX(i) { return gut + colW * i + colW / 2; }
    function y(v) { return top + plotH - Math.max(0, Math.min(1, Number(v) || 0)) * plotH; }

    var svgEl = S("svg", {
      class: "tj", width: W, height: H_, viewBox: "0 0 " + W + " " + H_,
      role: "img", "aria-label": "Lexical versus semantic similarity per alignment row",
    });

    [0, 0.5, 1].forEach(function (v) {
      svgEl.appendChild(tick(gut, y(v), W - padR, y(v), C.grid, 1, v === 0.5 ? "2 3" : null));
      svgEl.appendChild(label(gut - 3, y(v) + 3, v === 0.5 ? ".5" : String(v), C.muted, 8, "end"));
    });
    // Sits on the left, where the series start high and the space is free.
    svgEl.appendChild(label(gut + 4, y(0.5) - 4, "break threshold", C.muted, 8, "start"));

    // gap bands: the point of the chart
    rows.forEach(function (r, i) {
      var lex = Number(r.lexical) || 0, semv = Number(r.semantic) || 0;
      if (Math.abs(lex - semv) < 0.005) return;
      var down = semv < lex;
      var w = Math.min(9, colW * 0.3);
      svgEl.appendChild(S("rect", {
        x: colX(i) - w / 2, y: Math.min(y(lex), y(semv)), width: w,
        height: Math.abs(y(lex) - y(semv)),
        fill: down ? C.bad : C.good, "fill-opacity": 0.3, rx: 1,
      }));
    });

    function series(key, stroke, dash, marker) {
      var d = "";
      rows.forEach(function (r, i) {
        d += (i ? "L" : "M") + colX(i).toFixed(1) + " " + y(r[key]).toFixed(1);
      });
      var path = S("path", { d: d, fill: "none", stroke: stroke, "stroke-width": 1.3,
                             "stroke-opacity": 0.85, "stroke-linejoin": "round" });
      if (dash) path.setAttribute("stroke-dasharray", dash);
      svgEl.appendChild(path);
      rows.forEach(function (r, i) {
        var v = Number(r[key]) || 0;
        if (marker === "square") {
          svgEl.appendChild(S("rect", { x: colX(i) - 2.6, y: y(v) - 2.6, width: 5.2, height: 5.2,
                                        fill: C.surface, stroke: stroke, "stroke-width": 1.2 }));
        } else {
          svgEl.appendChild(S("circle", { cx: colX(i), cy: y(v), r: 2.8, fill: stroke,
                                          stroke: C.surface, "stroke-width": 0.8 }));
        }
      });
    }
    series("lexical", C.muted, "3 2", "square");
    series("semantic", C.ink, null, "circle");

    rows.forEach(function (r, i) {
      svgEl.appendChild(label(colX(i), axisY, String(r.row), C.muted, 8.5));
      var hit = S("rect", { class: "tj-hit", x: colX(i) - colW / 2, y: 0, width: colW, height: H_,
                            fill: C.ink, "fill-opacity": 0, "pointer-events": "all" });
      hit.appendChild(S("title", {
        text: "row " + r.row + " · A step " + r.a_index + " ↔ B step " + r.b_index +
              " · lexical " + F.num(r.lexical, 2) + " · semantic " + F.num(r.semantic, 2),
      }));
      hit.addEventListener("mouseenter", function () {
        hit.setAttribute("fill-opacity", 0.05);
        if (ctx.signal) ctx.signal("hover");
      });
      hit.addEventListener("mouseleave", function () { hit.setAttribute("fill-opacity", 0); });
      hit.addEventListener("click", function () { select(r.row, null, ctx); });
      svgEl.appendChild(hit);
    });
    svgEl.appendChild(label(gut - 3, axisY, "row", C.muted, 8, "end"));

    if (brk !== null) {
      var at = -1;
      rows.forEach(function (r, i) { if (r.row === brk) at = i; });
      if (at >= 0) {
        svgEl.appendChild(tick(colX(at), top - 10, colX(at), axisY - 9, C.bad, 1.2, "3 3"));
        svgEl.appendChild(S("polygon", {
          points: pts([[colX(at) - 4, top - 14], [colX(at) + 4, top - 14], [colX(at), top - 8]]),
          fill: C.bad,
        }));
        if (colW >= 44) {
          svgEl.appendChild(label(Math.min(colX(at) + 7, W - padR), top - 9, "break", C.bad, 8, "start"));
        }
      }
    }

    var wrap = H("div", { class: W > avail ? "scroll-x" : "" });
    wrap.appendChild(svgEl);
    host.appendChild(wrap);
  }

  // ================================================================ intents

  var INTENTS = ["frame", "acquire", "verify", "transform", "decide", "commit"];
  var INTENT_LETTER = { frame: "F", acquire: "A", verify: "V", transform: "T", decide: "D", commit: "C" };

  AgentDiff.block({
    id: "intents",
    title: "Intent bands",
    question: "What was each agent trying to do?",
    group: "trajectory",
    size: "normal",
    relevance: function (ctx) {
      var sem = semanticOf(ctx.report);
      var it = sem && sem.intents;
      if (!it || (!Array.isArray(it.a) && !Array.isArray(it.b))) return 0;
      if (!(it.a || []).length && !(it.b || []).length) return 0;
      var missing = it.missing || {};
      var count = ((missing.a || []).length + (missing.b || []).length);
      return Math.min(1, 0.46 + 0.25 * count);
    },
    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      var sem = semanticOf(report);
      var it = sem && sem.intents;
      if (!it) return ctx.empty(el, "This report carries no intent classification.");
      var a = Array.isArray(it.a) ? it.a : [], b = Array.isArray(it.b) ? it.b : [];
      if (!a.length && !b.length) return ctx.empty(el, "No steps were classified into the process grammar.");
      syncTask(ctx);

      el.appendChild(sideHeader(report));
      var host = sized(el, ctx, function (box, avail) { drawIntents(box, avail, ctx); });
      subscribe(el, function () { host.innerHTML = ""; drawIntents(host, measure(el), ctx); });

      // The two sequences, read as text — the same data, one line per side.
      [["a", a], ["b", b]].forEach(function (pair) {
        var letters = pair[1].map(function (row) {
          return INTENT_LETTER[row.intent] || "?";
        }).join(" ");
        el.appendChild(H("div", { class: "tj-seq" }, [
          H("b", { style: { color: sideColor(pair[0]) }, text: pair[0].toUpperCase() }),
          H("span", { text: letters || "—" }),
        ]));
      });

      var missing = it.missing || {};
      var gaps = H("div", { class: "tj-legend" });
      var any = false;
      ["a", "b"].forEach(function (side) {
        (missing[side] || []).forEach(function (intent) {
          any = true;
          gaps.appendChild(H("span", { class: "tag " + side,
            text: agentName(report, side) + " never " + phrase(intent) }));
        });
      });
      if (any) {
        el.appendChild(gaps);
        el.appendChild(H("p", {
          class: "caveat",
          text: "An intent the other agent used and this one never did — a process-grammar gap, " +
                "not a wording difference.",
        }));
      } else {
        el.appendChild(H("p", {
          class: "caveat",
          text: "Both agents used the same set of intents; they differ, if at all, in order and count.",
        }));
      }
    },
  });

  function phrase(intent) {
    return { frame: "framed the task", acquire: "acquired evidence", verify: "verified",
             transform: "transformed anything", decide: "decided between options",
             commit: "committed an answer" }[intent] || ("used " + intent);
  }

  function drawIntents(host, avail, ctx) {
    bind(ctx);
    var report = ctx.report;
    var sem = semanticOf(report);
    var it = sem.intents;
    var series = { a: Array.isArray(it.a) ? it.a : [], b: Array.isArray(it.b) ? it.b : [] };
    var maxStep = 0;
    ["a", "b"].forEach(function (side) {
      series[side].forEach(function (row) {
        if (typeof row.step === "number" && row.step + 1 > maxStep) maxStep = row.step + 1;
      });
    });
    if (!maxStep) maxStep = 1;

    var gut = 62, padR = 10;
    var colW = Math.max(18, Math.min(46, Math.floor((avail - gut - padR) / maxStep)));
    var W = gut + padR + colW * maxStep;
    var top = 10, laneH = 20;
    var H_ = top + laneH * INTENTS.length + 20;
    var axisY = top + laneH * INTENTS.length + 13;

    function colX(i) { return gut + colW * i + colW / 2; }
    function laneMid(intent) {
      var i = INTENTS.indexOf(intent);
      if (i < 0) i = INTENTS.length - 1;
      return top + laneH * i + laneH / 2;
    }
    // Each lane is split into an A half and a B half: where the two agents do
    // the same thing at the same step their marks sit one above the other
    // instead of one hiding the other.
    function laneY(intent, side) { return laneMid(intent) + (side === "a" ? -4 : 4); }

    var svgEl = S("svg", {
      class: "tj", width: W, height: H_, viewBox: "0 0 " + W + " " + H_,
      role: "img", "aria-label": "Intent sequence per agent over the process grammar",
    });

    var used = { a: {}, b: {} };
    ["a", "b"].forEach(function (side) {
      series[side].forEach(function (row) { used[side][row.intent] = true; });
    });

    INTENTS.forEach(function (intent) {
      var yy = laneMid(intent);
      svgEl.appendChild(tick(gut, yy, W - padR, yy, C.grid, 1));
      var unused = !used.a[intent] && !used.b[intent];
      svgEl.appendChild(S("text", {
        x: gut - 6, y: yy + 3, "text-anchor": "end", "font-size": 9.5,
        fill: unused ? C.grid : C.muted, text: intent,
      }));
    });

    ["a", "b"].forEach(function (side) {
      var list = series[side].slice().sort(function (x, y) { return x.step - y.step; });
      if (!list.length) return;
      var d = "";
      list.forEach(function (row, i) {
        d += (i ? "L" : "M") + colX(row.step).toFixed(1) + " " + laneY(row.intent, side).toFixed(1);
      });
      svgEl.appendChild(S("path", {
        d: d, fill: "none", stroke: sideColor(side), "stroke-width": 1.3,
        "stroke-opacity": 0.5, "stroke-linejoin": "round",
      }));
      list.forEach(function (row) {
        var x = colX(row.step), yy = laneY(row.intent, side);
        var node = side === "a"
          ? S("rect", { x: x - 3.4, y: yy - 3.4, width: 6.8, height: 6.8, rx: 1 })
          : S("circle", { cx: x, cy: yy, r: 3.8 });
        node.setAttribute("fill", sideColor(side));
        node.setAttribute("fill-opacity", 0.85);
        node.setAttribute("stroke", C.surface);
        node.setAttribute("stroke-width", 0.8);
        node.appendChild(S("title", {
          text: side.toUpperCase() + " step " + row.step + " · " + row.intent,
        }));
        svgEl.appendChild(node);
      });
    });

    for (var i = 0; i < maxStep; i++) {
      svgEl.appendChild(label(colX(i), axisY, String(i), C.muted, 8.5));
    }
    svgEl.appendChild(label(gut - 6, axisY, "step", C.muted, 8, "end"));

    var wrap = H("div", { class: W > avail ? "scroll-x" : "" });
    wrap.appendChild(svgEl);
    host.appendChild(wrap);
  }

  // ------------------------------------------------------------------- map
  //
  // The trajectory map answers a different question than Tracks. Tracks
  // compresses both runs into one horizontal picture; the map lays each run
  // out VERTICALLY as its own readable list — every individual step a row,
  // in run order — and draws the conversation between them in the gutter:
  // alignment edges (matched, drifted, or absent), and claim edges where
  // the same fact surfaces in both runs. Click any step on either side and
  // the shared cursor moves, so Step detail and Tracks follow.

  var MAP_STYLE_DONE = false;
  function ensureMapStyle() {
    if (MAP_STYLE_DONE) return;
    MAP_STYLE_DONE = true;
    var css = [
      ".tjm-wrap{overflow-y:auto;max-height:520px;border:1px solid var(--rule);",
      "border-radius:8px;background:var(--surface)}",
      ".tjm-foot{display:flex;flex-wrap:wrap;gap:3px 12px;margin-top:7px;",
      "font-size:10.5px;color:var(--ink-3);align-items:center;line-height:1.2}",
      ".tjm-foot .k{display:inline-flex;align-items:center;gap:4px}",
      ".tjm-note{margin-top:6px;font-size:11px;color:var(--ink-2);",
      "border-left:2px solid var(--warn);padding-left:7px}",
    ].join("");
    var tag = document.createElement("style");
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  function mapRowFor(report, side, index) {
    var rows = rowsOf(report);
    for (var i = 0; i < rows.length; i++) {
      if (rows[i][side + "_index"] === index) return i;
    }
    return -1;
  }

  function decisiveInfo(report) {
    var diag = report && report.diagnosis;
    if (!diag || diag.mode !== "single_failure") return null;
    var side = diag.subject === "a" ? "a" : diag.subject === "b" ? "b" : null;
    if (!side) return null;
    var dec = diag.decisive_step || {};
    var account = {};
    (Array.isArray(diag.causal_account) ? diag.causal_account : []).forEach(function (entry) {
      if (entry && entry.step !== null && entry.step !== undefined) account[entry.step] = entry;
    });
    return {
      side: side,
      step: dec.step !== undefined ? dec.step : null,
      reason: dec.reason || null,
      contested: diag.leading === null || diag.leading === undefined,
      account: account,
    };
  }

  function claimEdges(report) {
    var sem = semanticOf(report);
    var out = [];
    if (!sem || !Array.isArray(sem.claims)) return out;
    sem.claims.forEach(function (claim) {
      if (!claim) return;
      var aSteps = Array.isArray(claim.a_steps) ? claim.a_steps : [];
      var bSteps = Array.isArray(claim.b_steps) ? claim.b_steps : [];
      if (!aSteps.length || !bSteps.length) return;
      out.push({
        a: aSteps[0], b: bSteps[0],
        value: claim.value !== undefined ? String(claim.value) : "",
        wrong: claim.matches_expected === false,
      });
    });
    return out;
  }

  AgentDiff.block({
    id: "trajectory-map",
    title: "Trajectory map",
    question: "What did each run do, step by step — and where do the runs speak to each other?",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) {
      var report = ctx.report;
      if (!report) return 0;
      if (!stepsOf(report, "a").length && !stepsOf(report, "b").length) return 0;
      return rowsOf(report).length ? 0.9 : 0.4;
    },
    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      ensureMapStyle();
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      if (!stepsOf(report, "a").length && !stepsOf(report, "b").length) {
        return ctx.empty(el, "Neither trajectory recorded any steps.");
      }
      syncTask(ctx);
      el.appendChild(sideHeader(report));
      var host = sized(el, ctx, function (box, avail) {
        drawMap(box, avail, ctx);
      });
      subscribe(el, function () {
        host.innerHTML = "";
        drawMap(host, measure(el), ctx);
      });
    },
  });

  function drawMap(host, avail, ctx) {
    bind(ctx);
    var report = ctx.report;
    var stepsA = stepsOf(report, "a");
    var stepsB = stepsOf(report, "b");
    var rows = rowsOf(report);
    var sel = resolved(report);
    var root = rootInfo(report);
    var dec = decisiveInfo(report);
    var claims = claimEdges(report);
    var divs = divergenceMap(report);

    var rowH = 26, padTop = 14, padBot = 12;
    var n = Math.max(stepsA.length, stepsB.length, 1);
    var height = padTop + n * rowH + padBot;
    var W = Math.max(480, avail);
    var laneA = Math.round(W * 0.30), laneB = Math.round(W * 0.70);
    var labelW = laneA - 26;

    function yAt(i) { return padTop + i * rowH + rowH / 2; }
    function nodeXY(side, index) {
      var steps = side === "a" ? stepsA : stepsB;
      for (var i = 0; i < steps.length; i++) {
        if (steps[i] && steps[i].index === index) {
          return { x: side === "a" ? laneA : laneB, y: yAt(i) };
        }
      }
      return null;
    }

    var svgEl = S("svg", {
      class: "tj", width: W, height: height,
      viewBox: "0 0 " + W + " " + height, role: "img",
      "aria-label": "Trajectory map: each run's steps in order, with alignment and claim edges between them",
    });

    // lane spines first, so everything else draws over them
    [["a", laneA, stepsA], ["b", laneB, stepsB]].forEach(function (lane) {
      if (!lane[2].length) return;
      svgEl.appendChild(S("line", {
        x1: lane[1], y1: yAt(0), x2: lane[1], y2: yAt(lane[2].length - 1),
        stroke: C.grid, "stroke-width": 1.4,
      }));
    });

    // claim edges: the same fact surfacing in both runs. Drawn beneath the
    // alignment edges — they are context, not structure.
    claims.forEach(function (edge) {
      var pa = nodeXY("a", edge.a), pb = nodeXY("b", edge.b);
      if (!pa || !pb) return;
      var midX = (laneA + laneB) / 2;
      var stroke = edge.wrong ? C.bad : C.good;
      var path = S("path", {
        d: "M" + (pa.x + 8) + " " + pa.y +
           " C" + midX + " " + pa.y + " " + midX + " " + pb.y +
           " " + (pb.x - 8) + " " + pb.y,
        fill: "none", stroke: stroke, "stroke-width": 1,
        "stroke-dasharray": "1.5,3", opacity: 0.75,
      });
      path.appendChild(S("title", {
        text: (edge.wrong ? "wrong-valued claim" : "shared claim") +
              (edge.value ? ": " + edge.value : "") +
              " — A step " + edge.a + " ↔ B step " + edge.b,
      }));
      svgEl.appendChild(path);
    });

    // alignment edges: one per row that has both sides. A one-sided row is
    // an honest gap — the absence of an edge IS the picture.
    rows.forEach(function (row, i) {
      if (row.a_index === null || row.a_index === undefined) return;
      if (row.b_index === null || row.b_index === undefined) return;
      var pa = nodeXY("a", row.a_index), pb = nodeXY("b", row.b_index);
      if (!pa || !pb) return;
      var divergent = !!divs[i];
      var stroke = divergent ? C.bad : row.op === "match" ? C.axis : C.warn;
      var line = S("line", {
        x1: pa.x + 8, y1: pa.y, x2: pb.x - 8, y2: pb.y,
        stroke: stroke, "stroke-width": divergent ? 1.8 : 1.2,
        "stroke-dasharray": row.op === "match" ? null : "4,3",
        opacity: divergent ? 0.95 : 0.7,
      });
      line.appendChild(S("title", {
        text: "row " + i + " · " + row.op + " · similarity " + F.num(row.similarity, 2) +
              (divergent ? " · divergence" : ""),
      }));
      svgEl.appendChild(line);
    });

    // step nodes: every individual step, in run order
    [["a", laneA, stepsA], ["b", laneB, stepsB]].forEach(function (lane) {
      var side = lane[0], x = lane[1], steps = lane[2];
      steps.forEach(function (step, i) {
        if (!step) return;
        var y = yAt(i);
        var rowIdx = mapRowFor(report, side, step.index);
        var onAccount = dec && dec.side === side && dec.account[step.index];
        var isDecisive = dec && dec.side === side && dec.step === step.index;
        var isRoot = root && root.side === side && root.index === step.index;
        var picked = sel.row === rowIdx && rowIdx >= 0;

        var g = S("g", { class: "tj-hit" });
        if (onAccount) {
          g.appendChild(S("circle", { cx: x, cy: y, r: 10.5, fill: C.warn, opacity: 0.16 }));
        }
        if (isRoot || isDecisive) {
          g.appendChild(S("circle", {
            cx: x, cy: y, r: 9.5, fill: "none",
            stroke: C.bad, "stroke-width": isDecisive ? 2 : 1.2,
            "stroke-dasharray": isDecisive ? null : "2.5,2",
          }));
        }
        if (picked) {
          g.appendChild(S("circle", {
            cx: x, cy: y, r: 12, fill: "none",
            stroke: cssAccent(), "stroke-width": 1.4, opacity: 0.9,
          }));
        }
        var glyph = glyphNode(step.type, x, y, 5.5);
        glyph.setAttribute("fill", side === "a" ? C.a : C.b);
        if (step.error) {
          glyph.setAttribute("stroke", C.bad);
          glyph.setAttribute("stroke-width", 2);
        }
        g.appendChild(glyph);

        var textX = side === "a" ? x - 14 : x + 14;
        var anchor = side === "a" ? "end" : "start";
        var name = String(step.name || step.type || "step");
        var maxChars = Math.max(8, Math.floor(labelW / 6.4));
        if (name.length > maxChars) name = name.slice(0, maxChars - 1) + "…";
        g.appendChild(S("text", {
          x: textX, y: y + 3, "text-anchor": anchor,
          "font-family": "var(--mono)", "font-size": 10.5,
          fill: picked ? C.ink : C.muted,
          text: step.index + " · " + name + (step.error ? " ⚠" : ""),
        }));
        g.appendChild(S("title", {
          text: side.toUpperCase() + " step " + step.index + " · " + (step.type || "") +
                " " + (step.name || "") +
                (step.error ? " · ERROR" : "") +
                (isDecisive ? " · decisive step" : isRoot ? " · attributed root" : "") +
                (onAccount ? " · on the causal account" : ""),
        }));
        g.addEventListener("click", function () {
          if (rowIdx >= 0) select(rowIdx, side, ctx);
        });
        svgEl.appendChild(g);
      });
    });

    var wrap = H("div", { class: "tjm-wrap" });
    wrap.appendChild(svgEl);
    host.appendChild(wrap);

    var foot = H("div", { class: "tjm-foot" }, [
      legendKey("solid line", "matched step"),
      legendKey("dashed line", "drifted / divergent"),
      legendKey("dotted curve", "same claim in both runs (red = wrong-valued)"),
      legendKey("no line", "step only one run took"),
      dec ? legendKey("solid red ring", "decisive step") : null,
      root ? legendKey("dashed red ring", "attributed root") : null,
      dec ? legendKey("amber halo", "on the causal account") : null,
    ]);
    host.appendChild(foot);

    if (dec && dec.contested) {
      host.appendChild(H("div", { class: "tjm-note",
        text: "Diagnosis contested — no decisive step is committed" +
              (dec.reason ? ": " + dec.reason : ".") }));
    }
  }

  function cssAccent() {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue("--accent");
      if (v) return v.trim();
    } catch (err) { /* fall through */ }
    return C.ink;
  }

  function legendKey(mark, meaning) {
    return H("span", { class: "k" }, [
      H("b", { text: mark, style: { fontWeight: "600" } }),
      H("span", { text: meaning }),
    ]);
  }

})(typeof window !== "undefined" ? window : this);
