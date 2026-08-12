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
      ".tj-side em{font-style:normal;color:var(--ink-3);font-size:11px}",
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

  function sideHeader(report, mark) {
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
    if (mark) { box.appendChild(H("span", { class: "sp", style: { flex: "1" } })); box.appendChild(mark); }
    return box;
  }

  // ================================================================ tracks

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

      el.appendChild(sideHeader(report));
      var readout = H("div", { class: "tj-read" });
      var host = sized(el, ctx, function (box, avail) { drawTracks(box, avail, ctx, readout); });
      el.appendChild(readout);
      subscribe(el, function () {
        host.innerHTML = "";
        drawTracks(host, measure(el), ctx, readout);
      });

      var typeItems = [{ heading: "step" }];
      TYPES.forEach(function (type) {
        typeItems.push({ node: tinyGlyph(type, "a", null), text: TYPE_LABEL[type] });
      });
      el.appendChild(legend(typeItems));
      el.appendChild(legend([
        { heading: "quality" },
        { node: tinyGlyph("search", "a", "good"), text: "good" },
        { node: tinyGlyph("search", "a", "weak"), text: "weak" },
        { node: tinyGlyph("search", "a", "bad"), text: "bad" },
        { node: tinyGlyph("search", "a", null), text: "unannotated" },
        { heading: "link" },
        { node: swatchLine(C.axis, null), text: "match" },
        { node: swatchLine(C.warn, "3 2"), text: "drift" },
        { node: swatchLine(C.bad, "2 2"), text: "divergence" },
      ]));
      el.appendChild(H("p", {
        class: "caveat",
        text: "One column per alignment row: matched steps sit in the same column, so a step with " +
              "no counterpart leaves a gap on the other track. Bar length is tokens spent on that step.",
      }));
    },
  });

  function drawTracks(host, avail, ctx, readout) {
    bind(ctx);
    var report = ctx.report;
    var rows = rowsOf(report);
    var n = rows.length;
    var divs = divergenceMap(report);
    var root = rootInfo(report);
    var sel = resolved(report);

    var padL = 10, padR = 10;
    var colW = Math.max(38, Math.min(78, Math.floor((avail - padL - padR) / Math.max(n, 1))));
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

    var root_ = S("svg", {
      class: "tj", width: W, height: H_, viewBox: "0 0 " + W + " " + H_,
      role: "img",
      "aria-label": "Step tracks for " + agentName(report, "a") + " and " + agentName(report, "b"),
    });

    // --- selected column wash (drawn first so marks sit on top)
    if (sel.row >= 0) {
      root_.appendChild(S("rect", {
        x: colX(sel.row) - colW / 2, y: 2, width: colW, height: H_ - 16,
        fill: C.ink, "fill-opacity": 0.055, rx: 4,
      }));
    }

    // --- divergence rules
    Object.keys(divs).forEach(function (key) {
      var i = Number(key);
      var mark = divs[key];
      root_.appendChild(tick(colX(i), 12, colX(i), H_ - 18, C.bad, mark.primary ? 1.2 : 1,
                             mark.primary ? "3 3" : "1 4"));
      if (mark.primary) {
        root_.appendChild(S("polygon", {
          points: pts([[colX(i) - 4.5, 4], [colX(i) + 4.5, 4], [colX(i), 11]]),
          fill: C.bad,
        }));
        if (colW >= 42) {
          root_.appendChild(label(colX(i) + 8, 10, "d" + (mark.div.rank || 1), C.bad, 8.5, "start"));
        }
      }
    });

    // --- track baselines
    root_.appendChild(tick(padL, yA, W - padR, yA, C.grid, 1));
    root_.appendChild(tick(padL, yB, W - padR, yB, C.grid, 1));

    // --- connectors
    rows.forEach(function (row, i) {
      var x = colX(i);
      var hasA = row.a_index !== null && row.a_index !== undefined;
      var hasB = row.b_index !== null && row.b_index !== undefined;
      if (hasA && hasB) {
        var drift = row.op !== "match";
        root_.appendChild(tick(x, yA + R + 2, x, yB - R - 2,
                               drift ? C.warn : C.grid, drift ? 1.2 : 1, drift ? "3 2" : null));
      } else if (hasA || hasB) {
        var from = hasA ? yA + R + 2 : yB - R - 2;
        var to = hasA ? yA + 16 : yB - 16;
        root_.appendChild(tick(x, from, x, to, sideColor(hasA ? "a" : "b"), 1, "2 2"));
        var absentY = hasA ? yB : yA;
        root_.appendChild(tick(x - 4, absentY, x + 4, absentY, C.muted, 1.6));
      }
    });

    // --- steps
    rows.forEach(function (row, i) {
      var x = colX(i);
      ["a", "b"].forEach(function (side) {
        var index = row[side + "_index"];
        if (index === null || index === undefined) return;
        var step = stepAt(report, side, index);
        if (!step) return;
        var y = side === "a" ? yA : yB;
        var dir = side === "a" ? -1 : 1;

        // token bar, growing away from the middle
        if (maxTokens > 0 && typeof step.tokens === "number" && step.tokens > 0) {
          var hgt = Math.max(1.5, (step.tokens / maxTokens) * barMax);
          var bw = Math.max(3, Math.min(7, colW - 22));
          root_.appendChild(S("rect", {
            x: x - bw / 2, y: dir < 0 ? y - R - 3 - hgt : y + R + 3,
            width: bw, height: hgt, fill: sideColor(side), "fill-opacity": 0.42, rx: 1,
          }));
        }

        if (root && root.side === side && root.index === index) {
          root_.appendChild(S("circle", {
            cx: x, cy: y, r: R + 4.5, fill: "none", stroke: C.bad, "stroke-width": 1.2,
          }));
        }
        if (sel.row === i && (sel.side === side || sel.side === null)) {
          root_.appendChild(S("circle", {
            cx: x, cy: y, r: R + 2.5, fill: "none", stroke: C.ink,
            "stroke-width": 1, "stroke-opacity": 0.55,
          }));
        }

        root_.appendChild(styleGlyph(glyphNode(step.type, x, y, R),
                                     qualityStyle(step.quality, side)));
        if (colW >= 36) {
          root_.appendChild(label(x + R + 3.5, y + 3, String(index), C.muted, 8, "start"));
        }
      });
    });

    // --- row axis
    root_.appendChild(tick(padL, axisY - 9, W - padR, axisY - 9, C.grid, 1));
    rows.forEach(function (row, i) {
      root_.appendChild(label(colX(i), axisY, String(i), C.muted, 8.5));
    });
    root_.appendChild(label(padL, axisY - 13, "row", C.muted, 8, "start"));

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
        root_.appendChild(hit);
      });
    });

    var wrap = H("div", { class: W > avail ? "scroll-x" : "" });
    wrap.appendChild(root_);
    host.appendChild(wrap);
    readout.innerHTML = "";
    readout.appendChild(H("i", { text: hint(report) }));
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

})(typeof window !== "undefined" ? window : this);
