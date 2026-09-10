/* AgentDiff block — Where it mattered.
 *
 * A focus-and-context timeline for multi-agent runs. Each run is a band,
 * one row per lane (the root agent, then its depth-1 sub-agents); each
 * contiguous stretch of steps the engine clustered is a box in its lane's
 * row. The x-axis is not time: in "focus" mode a box's width grows with
 * the cluster's impact, so the stretches that carried the outcome dilate
 * and the quiet ones constrict — runs of quiet clusters fold into one thin
 * hatched strip. "even" is plain wall-clock, so the reader can see what
 * the dilation did. A box opens on click to show its marks (the decisive
 * step, faults, errors, retries, milestones, divergences, the answer) as
 * ticks that open the step in the shared inspector.
 *
 * Reads `report.impact` ({a, b, narrative}); every width is a function of
 * the cluster's `impact` (0..1) or its `seconds`, every mark a step index.
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
      ".im{--im-a:var(--a);--im-b:var(--b);position:relative}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .im{--im-a:#3987e5;--im-b:#d95926}}",
      ":root[data-theme=dark] .im{--im-a:#3987e5;--im-b:#d95926}",
      ".im-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".im-bar{display:flex;gap:6px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".im-bar .seg{display:inline-flex;gap:2px}",
      ".im-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 8px;cursor:pointer}",
      ".im-bar button[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
      ".im-legend{font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px;max-width:100ch}",
      ".im-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".im-chart text{font-size:var(--fs-xs)}",
      ".im-chart .im-head{font-weight:700;letter-spacing:.04em}",
      ".im-chart .im-lab{fill:var(--ink-2)}",
      ".im-chart .im-clab{pointer-events:none;font-family:var(--mono)}",
      ".im-chart .im-why{fill:var(--ink-3)}",
      ".im-chart .im-hwhy{fill:var(--ink-2);pointer-events:none}",
      ".im-chart .im-cluster{cursor:pointer}.im-chart .im-cluster:hover{stroke:var(--ink);stroke-width:1.5}",
      ".im-chart .im-cluster.open{stroke-width:1.5}",
      ".im-chart .im-fold{cursor:pointer}.im-chart .im-fold text{fill:var(--ink-3);pointer-events:none;font-family:var(--mono)}",
      ".im-chart .im-fold:hover rect.im-fold-rect{stroke:var(--ink);stroke-width:1}",
      ".im-chart .im-mark{cursor:pointer}.im-chart .im-mark text.g{font-weight:700}.im-chart .im-mark text.im-mlab{fill:var(--ink-2);font-family:var(--mono)}",
      ".im-chart .im-mark:hover text.g{fill:var(--ink)}",
      ".im-band + .im-band{margin-top:10px}",
      ".im-none{font-size:var(--fs-xs);color:var(--ink-3);margin:4px 0 8px}",
      ".im-details{margin-top:8px;font-size:var(--fs-xs)}.im-details summary{cursor:pointer;color:var(--ink-2)}",
      ".im-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:6px}",
      ".im-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".im-table td{padding:3px 10px 3px 0;color:var(--ink-2);vertical-align:top}.im-table td.n{white-space:nowrap}",
      ".im-table td.hot{color:var(--ink);font-weight:600}.im-table td.quiet{color:var(--ink-3)}",
      ".im-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
      ".im-tip b{color:var(--ink)}",
    ].join("\n");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------ helpers
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v >= 1 ? v.toFixed(1) + "s" : v.toFixed(2) + "s"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function color(side) { return "var(--im-" + side + ")"; }
  //: ~6.2px per character at the page's small size; good enough to truncate by
  var CH = 6.2;
  function fit(text, px) {
    text = String(text === null || text === undefined ? "" : text).replace(/\s+/g, " ").trim();
    var n = Math.floor(px / CH);
    if (n < 2) return "";
    return text.length > n ? text.slice(0, Math.max(1, n - 1)) + "…" : text;
  }
  function clamp01(v) { return isNum(v) ? Math.max(0, Math.min(1, v)) : 0; }
  //: the mark vocabulary — the same glyphs the map and the run lens use
  var GLYPH = { decisive: "◎", fault: "▏", error: "✕", retry: "↻", milestone: "◆", divergence: "⇄", answer: "■" };
  var MARK_WORD = { decisive: "decisive step", fault: "on the fault's path", error: "error", retry: "retry", milestone: "milestone", divergence: "divergence", answer: "the answer" };

  //: per task: which clusters are open, which folds are unfolded, the mode
  var STATE = {};
  function stateFor(task) {
    var k = String(task || "");
    return STATE[k] || (STATE[k] = { mode: "focus", open: {}, unfolded: {} });
  }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "im-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        lines.forEach(function (l) { if (!l) return; var d = document.createElement("div"); if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text; tip.appendChild(d); });
        tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 340 > r.width) x = Math.max(0, evt.clientX - r.left - 350);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  // ------------------------------------------------------------ the units
  //
  // A band is a sequence of units: a cluster, or a fold — two or more
  // consecutive quiet clusters folded into one strip (focus mode only,
  // and only while the reader has not opened it).
  function clustersOf(run) {
    return (run && Array.isArray(run.clusters) ? run.clusters : []).filter(function (c) { return c && isNum(c.from); })
      .slice().sort(function (p, q) { return p.from - q.from; });
  }
  //: state keys are side-qualified: both runs number their clusters c0, c1, …
  function skey(side, id) { return side + ":" + id; }
  function unitsOf(run, st, side) {
    var cs = clustersOf(run), out = [];
    var i = 0;
    while (i < cs.length) {
      var c = cs[i];
      if (st.mode === "focus" && c.kind === "quiet") {
        var j = i;
        while (j < cs.length && cs[j].kind === "quiet") j++;
        var group = cs.slice(i, j);
        var fid = group.map(function (g) { return g.id; }).join(",");
        if (group.length >= 2) {
          if (st.unfolded[skey(side, fid)]) {
            // opened: every cluster of the run shows, so none of it refolds
            group.forEach(function (g) { out.push({ type: "cluster", id: g.id, c: g }); });
          } else {
            out.push({ type: "fold", id: fid, clusters: group,
              steps: group.reduce(function (s, g) { return s + (isNum(g.steps) ? g.steps : (g.to - g.from + 1)); }, 0),
              seconds: group.reduce(function (s, g) { return s + (g.seconds || 0); }, 0),
              from: group[0].from, to: group[group.length - 1].to });
          }
          i = j;
          continue;
        }
      }
      out.push({ type: "cluster", id: c.id, c: c });
      i++;
    }
    return out;
  }
  //: the root agent's row first, then the sub-agents in the order the run met them
  function lanesOf(run) {
    var listed = (run && Array.isArray(run.lanes) ? run.lanes : []).filter(function (l) { return l && l.agent; });
    var lanes = listed.filter(function (l) { return !l.depth; }).concat(listed.filter(function (l) { return l.depth; })).map(function (l) { return l.agent; });
    lanes = lanes.filter(function (l, i) { return lanes.indexOf(l) === i; });
    clustersOf(run).forEach(function (c) { if (c.lane && lanes.indexOf(c.lane) < 0) lanes.push(c.lane); });
    if (!lanes.length) lanes.push("run");
    return lanes;
  }
  function weight(c) { return clamp01(c.impact); }
  //: a 1.00 cluster is at least this many times the width of a floor-width one
  var RATIO = 4;
  function stepsIn(c) { return isNum(c.steps) ? c.steps : Math.max(1, (c.to || 0) - (c.from || 0) + 1); }
  //: a hot cluster's why, short ("6 errors · 26s wasted") and compact ("6✕ 6↻ 26s", the legend's glyphs)
  function shortWhy(c) {
    var r = c.reasons || {}, parts = [];
    if (r.decisive) parts.push("decisive");
    if (r.errors) parts.push(r.errors + (r.errors === 1 ? " error" : " errors"));
    if (r.retries) parts.push(r.retries + (r.retries === 1 ? " retry" : " retries"));
    if (isNum(r.wasted_s) && r.wasted_s >= 1) parts.push(secs(r.wasted_s) + " wasted");
    return parts.length ? parts.join(" · ") : String(c.why || "").split(";")[0].trim();
  }
  function compactWhy(c) {
    var r = c.reasons || {}, parts = [];
    if (r.decisive) parts.push("◎");
    if (r.errors) parts.push(r.errors + "✕");
    if (r.retries) parts.push(r.retries + "↻");
    if (isNum(r.wasted_s) && r.wasted_s >= 1) parts.push(secs(r.wasted_s));
    return parts.join(" ");
  }

  /* One width scale for both bands: equal impacts get equal widths across
   * A and B, and neither band runs past the available width. In focus
   * mode a cluster is max(floor, k·impact), a fold a fixed thin strip, an
   * open cluster a box wide enough for its marks; k is the largest value
   * that fits the wider band — and when that would leave a 1.00 cluster
   * under RATIO× a floor one, the floor shrinks so k can take the rest.
   * In even mode every cluster is its seconds. */
  function scale(im, sides, states, avail, narrow, mode) {
    var wMin = narrow ? 8 : 14, gap = 2;
    //: a fold is a thin strip — the constriction is the point; its label runs vertically
    var wFold = narrow ? 12 : 22;
    var per = {};
    var k = Infinity;
    if (mode === "even") {
      var maxS = 1e-9;
      sides.forEach(function (s) { maxS = Math.max(maxS, im[s].total_s || clustersOf(im[s]).reduce(function (t, c) { return t + (c.seconds || 0); }, 0)); });
      sides.forEach(function (s) {
        var units = unitsOf(im[s], states, s);
        var n = units.length;
        var free = avail - gap * Math.max(0, n - 1);
        var kk = free / maxS;
        var ws = units.map(function (u) { return Math.max(2, (u.c.seconds || 0) * kk); });
        var sum = ws.reduce(function (t, w) { return t + w; }, 0);
        if (sum > free) ws = ws.map(function (w) { return w * free / sum; });
        per[s] = { units: units, widths: ws, gap: gap, wFold: wFold };
      });
      return per;
    }
    var budgets = {}, fs = {};
    sides.forEach(function (s) {
      var units = unitsOf(im[s], states, s);
      var fixed = gap * Math.max(0, units.length - 1), f = [];
      units.forEach(function (u) {
        if (u.type === "fold") fixed += wFold;
        else if (states.open[skey(s, u.id)]) fixed += openWidth(u.c, avail, narrow);
        else f.push(weight(u.c));
      });
      budgets[s] = avail - fixed; fs[s] = f;
      per[s] = { units: units, gap: gap, wFold: wFold };
    });
    function total(f, floor, kk) { return f.reduce(function (t, v) { return t + Math.max(floor, kk * v); }, 0); }
    // the floor as given, k the largest that fits the wider band
    sides.forEach(function (s) {
      if (!fs[s].length) return;
      var lo = 0, hi = avail;
      for (var it = 0; it < 40; it++) { var mid = (lo + hi) / 2; if (total(fs[s], wMin, mid) <= budgets[s]) lo = mid; else hi = mid; }
      k = Math.min(k, lo);
    });
    if (!isFinite(k)) k = 0;
    var floor = wMin;
    // too crowded for the dilation to show: shrink the floor until a 1.00 cluster is RATIO× a floor one
    if (k < RATIO * wMin) {
      var w = wMin;
      sides.forEach(function (s) {
        if (!fs[s].length) return;
        w = Math.min(w, budgets[s] / fs[s].reduce(function (t, v) { return t + Math.max(1, RATIO * v); }, 0));
      });
      floor = Math.max(narrow ? 3 : 5, Math.min(wMin, w));
      k = RATIO * floor;
    }
    k = Math.max(0, Math.min(k, avail * 0.5));
    sides.forEach(function (s) {
      var p = per[s];
      var n = p.units.length;
      var free = avail - gap * Math.max(0, n - 1);
      p.floor = floor; p.k = k;
      p.widths = p.units.map(function (u) {
        if (u.type === "fold") return wFold;
        var base = Math.max(floor, k * weight(u.c));
        return states.open[skey(s, u.id)] ? Math.max(base, openWidth(u.c, avail, narrow)) : base;
      });
      var sum = p.widths.reduce(function (t, w) { return t + w; }, 0);
      if (sum > free && sum > 0) p.widths = p.widths.map(function (w) { return w * free / sum; });
    });
    return per;
  }
  function openWidth(c, avail, narrow) {
    var marks = Array.isArray(c.marks) ? c.marks.length : 0;
    return Math.min(avail * 0.35, Math.max(narrow ? 60 : 110, (narrow ? 40 : 70) + marks * (narrow ? 16 : 48)));
  }

  // ------------------------------------------------------------ drawing
  var seq = 0;
  function draw(host, report, im, st, tip, ctx) {
    if (!d3) return;
    var task = report.task && report.task.id;
    var W = Math.max(300, host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 640);
    var narrow = W < 560;
    var sides = ["a", "b"].filter(function (s) { return im[s] && im[s].measurable !== false && clustersOf(im[s]).length; });
    var longest = 0;
    sides.forEach(function (s) { lanesOf(im[s]).forEach(function (l) { longest = Math.max(longest, String(l).length + 2); }); });
    var labW = narrow ? 68 : Math.min(200, Math.max(100, Math.ceil(8 + longest * CH))), padR = 6;
    var avail = W - labW - padR;
    var per = scale(im, sides, st, avail, narrow, st.mode);
    var rowH = 16, rowGap = 3, whyH = 13, head = 18, foot = 4;
    ["a", "b"].forEach(function (side) {
      var run = im[side];
      if (sides.indexOf(side) < 0) {
        host.appendChild(ctx.h("p", { class: "im-none", "data-side": side, text: name(report, side) + ": " + (run && run.narrative ? run.narrative : "no clusters were measured for this run.") }));
        return;
      }
      var p = per[side];
      var lanes = lanesOf(run);
      // rows: y per lane; a lane with open clusters gets a why line per open cluster under it
      var rows = {}, y = head, openIn = {};
      p.units.forEach(function (u) { if (u.type === "cluster" && st.open[skey(side, u.id)]) { openIn[u.c.lane] = (openIn[u.c.lane] || 0) + 1; } });
      lanes.forEach(function (lane) {
        rows[lane] = { y: y, why: 0 };
        y += rowH + (openIn[lane] || 0) * whyH + rowGap;
      });
      var rowsTop = head, rowsBottom = y - rowGap;
      var H = y - rowGap + foot;
      var hot = clustersOf(run).filter(function (c) { return c.kind === "hot"; }).length;
      var svg = d3.select(host).append("svg").attr("class", "im-band").attr("data-side", side).attr("data-mode", st.mode)
        .attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
        .attr("aria-label", name(report, side) + ": " + clustersOf(run).length + " stretches over " + lanes.length + " lane" + (lanes.length === 1 ? "" : "s") + ", " + hot + " hot, width " + (st.mode === "focus" ? "by impact" : "by seconds"));
      var hid = "im-hatch-" + side + "-" + (seq++);
      var pat = svg.append("defs").append("pattern").attr("id", hid).attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
      pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "var(--ink-3)").attr("stroke-width", 1).attr("stroke-opacity", 0.45);
      // the run's line: name, steps, seconds
      svg.append("text").attr("class", "im-head").attr("x", 0).attr("y", 11).attr("fill", color(side))
        .text(fit(name(report, side) + " · " + (isNum(run.total_steps) ? run.total_steps + " steps · " : "") + secs(run.total_s), W))
        .append("title").text(run.narrative || "");
      // the rows
      lanes.forEach(function (lane, li) {
        var g = svg.append("g").attr("class", "im-row").attr("data-lane", lane).attr("data-side", side);
        g.append("line").attr("x1", labW).attr("x2", W - padR).attr("y1", rows[lane].y + rowH / 2).attr("y2", rows[lane].y + rowH / 2).attr("stroke", "var(--rule)").attr("stroke-dasharray", li === 0 ? null : "2 3");
        g.append("text").attr("class", "im-lab").attr("x", labW - 6).attr("y", rows[lane].y + rowH / 2 + 4).attr("text-anchor", "end")
          .text(fit((li === 0 || narrow ? "" : "↳ ") + lane, labW - 8)).append("title").text(lane);
      });
      // the units, left to right
      var x = labW, whys = [], xs0 = [];
      p.units.forEach(function (u, ui) { xs0.push(x); x += p.widths[ui] + p.gap; });
      //: the x where the next thing in this row (or the next fold) starts
      function nextBound(ui, lane) {
        for (var j = ui + 1; j < p.units.length; j++) {
          var v = p.units[j];
          if (v.type === "fold" || (v.c.lane || lanes[0]) === lane) return xs0[j];
        }
        return W - padR;
      }
      x = labW;
      p.units.forEach(function (u, ui) {
        var w = p.widths[ui];
        if (u.type === "fold") {
          var fg = svg.append("g").attr("class", "im-fold").attr("data-ids", u.id).attr("data-side", side);
          fg.append("rect").attr("class", "im-fold-rect").attr("x", x).attr("y", rowsTop).attr("width", w).attr("height", rowsBottom - rowsTop).attr("rx", 3)
            .attr("fill", "var(--ink-3)").attr("fill-opacity", 0.08);
          fg.append("rect").attr("x", x).attr("y", rowsTop).attr("width", w).attr("height", rowsBottom - rowsTop).attr("rx", 3).attr("fill", "url(#" + hid + ")").attr("pointer-events", "none");
          var full = "⋯ " + u.steps + " steps · " + secs(u.seconds), mid = "⋯ " + u.steps + " steps", rowsH = rowsBottom - rowsTop;
          var lab = rowsH >= full.length * CH + 8 ? full : rowsH >= mid.length * CH + 8 ? mid : "⋯";
          if (lab === "⋯") fg.append("text").attr("x", x + w / 2).attr("y", (rowsTop + rowsBottom) / 2 + 4).attr("text-anchor", "middle").text(lab);
          else fg.append("text").attr("transform", "translate(" + (x + w / 2 + 4) + "," + ((rowsTop + rowsBottom) / 2) + ") rotate(-90)").attr("text-anchor", "middle").text(lab);
          fg.append("title").text(u.clusters.length + " quiet stretches folded: steps " + u.from + "–" + u.to + ", " + u.steps + " steps, " + secs(u.seconds) + " — click to open");
          fg.on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: name(report, side) + " · " + u.clusters.length + " quiet stretches" }, { text: "steps " + u.from + "–" + u.to + " · " + u.steps + " steps · " + secs(u.seconds) }, { text: "click to open them" }]);
          }).on("pointerleave", tip.hide).on("click", function () { st.unfolded[skey(side, u.id)] = true; repaint(); });
          x += w + p.gap;
          return;
        }
        var c = u.c, lane = c.lane && rows[c.lane] ? c.lane : lanes[0], ry = rows[lane].y, open = !!st.open[skey(side, u.id)];
        var quiet = c.kind === "quiet", hotK = c.kind === "hot";
        var op = quiet ? 0.18 : 0.15 + 0.75 * clamp01(c.impact);
        var bh = open || hotK ? rowH - 2 : quiet ? 4 : Math.max(4, Math.min(rowH - 2, Math.round(4 + 10 * clamp01(c.impact))));
        var by = ry + Math.round((rowH - bh) / 2);
        var rect = svg.append("rect").attr("class", "im-cluster" + (open ? " open" : "")).attr("data-id", c.id).attr("data-kind", c.kind || "work").attr("data-side", side).attr("data-lane", lane)
          .attr("x", x).attr("y", by).attr("width", w).attr("height", bh).attr("rx", Math.min(3, bh / 2))
          .attr("fill", quiet ? "var(--ink-3)" : color(side)).attr("fill-opacity", op)
          .attr("stroke", hotK ? color(side) : open ? "var(--ink-2)" : "none").attr("stroke-width", hotK ? 1.5 : open ? 1 : 0);
        rect.append("title").text(name(report, side) + " · " + lane + " · steps " + c.from + "–" + c.to + " · " + secs(c.seconds) + " · impact " + clamp01(c.impact).toFixed(2) + (c.why ? " — " + c.why : ""));
        if (quiet) svg.append("rect").attr("x", x).attr("y", by).attr("width", w).attr("height", bh).attr("rx", Math.min(3, bh / 2)).attr("fill", "url(#" + hid + ")").attr("pointer-events", "none");
        rect.on("pointermove", function (evt) {
          tip.show(evt, [{ b: true, text: name(report, side) + " · " + lane + " · " + (c.label || c.kind) },
            { text: "steps " + c.from + "–" + c.to + " (" + stepsIn(c) + ") · " + secs(c.seconds) + " · impact " + clamp01(c.impact).toFixed(2) + " · " + (c.kind || "work") },
            c.why ? { text: c.why } : null,
            { text: open ? "click to collapse" : "click to open its steps" }]);
        }).on("pointerleave", tip.hide).on("click", function () { var k = skey(side, u.id); if (st.open[k]) delete st.open[k]; else st.open[k] = true; repaint(); });
        var marks = open && Array.isArray(c.marks) ? c.marks.filter(function (m) { return m && isNum(m.step); }).slice().sort(function (p, q) { return p.step - q.step; }) : [];
        if (!open && bh >= 12) {
          var lab = c.label || c.kind || "", why = hotK ? shortWhy(c) : "", tight = hotK ? compactWhy(c) : "";
          // what the box can hold, most to least said: label · why, the why, label · compact, compact, the label cut
          var tiers = why ? [lab + " · " + why, why, tight ? lab + " · " + tight : null, tight] : [];
          var inside = null;
          for (var ti = 0; ti < tiers.length && inside === null; ti++) { if (tiers[ti] && tiers[ti].length * CH <= w - 8) inside = tiers[ti]; }
          if (inside === null && w >= 30) inside = fit(lab, w - 8);
          if (inside) svg.append("text").attr("class", "im-clab").attr("data-id", c.id).attr("x", x + 4).attr("y", ry + rowH / 2 + 4).attr("fill", op >= 0.55 ? "var(--bg)" : "var(--ink)").text(inside);
          if (why && (inside === null || inside.indexOf(why) < 0) && !narrow) {
            var room = nextBound(ui, lane) - (x + w) - 8;
            if (room >= Math.min(why.length, 10) * CH) {
              svg.append("text").attr("class", "im-hwhy").attr("data-id", c.id).attr("x", x + w + 4).attr("y", ry + rowH / 2 + 4).text(fit(why, room));
            }
          }
        }
        if (open) {
          // ticks for the marks, at their step's place in the box; short labels when they fit
          var span = Math.max(1, (c.to || 0) - (c.from || 0));
          var inner = Math.max(1, w - 12);
          var xs = marks.map(function (m) { return x + 6 + inner * Math.max(0, Math.min(1, (m.step - c.from) / span)); });
          // two marks on one step sit side by side rather than on top of each other
          for (var mi2 = 1; mi2 < xs.length; mi2++) { if (xs[mi2] < xs[mi2 - 1] + 9) xs[mi2] = xs[mi2 - 1] + 9; }
          marks.forEach(function (m, mi) {
            var mx = xs[mi], my = ry + rowH / 2;
            var mg = svg.append("g").attr("class", "im-mark").attr("data-step", m.step).attr("data-kind", m.kind || "mark").attr("data-side", side);
            var red = m.kind === "decisive" || m.kind === "fault" || m.kind === "error";
            if (m.kind === "decisive") {
              mg.append("circle").attr("cx", mx).attr("cy", my).attr("r", 5.5).attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-width", 2);
            } else if (m.kind === "fault") {
              mg.append("line").attr("x1", mx).attr("x2", mx).attr("y1", ry + 1).attr("y2", ry + rowH - 1).attr("stroke", "var(--bad)").attr("stroke-width", 2.5);
            } else {
              mg.append("text").attr("class", "g").attr("x", mx).attr("y", my + 4).attr("text-anchor", "middle").attr("fill", red ? "var(--bad)" : "var(--ink)").text(GLYPH[m.kind] || "•");
            }
            mg.append("rect").attr("x", mx - 6).attr("y", ry).attr("width", 12).attr("height", rowH).attr("fill", "transparent");
            var room = (mi + 1 < marks.length ? xs[mi + 1] : x + w) - mx - 10;
            if (!narrow && room >= 3 * CH && m.label) {
              mg.append("text").attr("class", "im-mlab").attr("x", mx + 8).attr("y", my + 4).text(fit(m.label, room));
            }
            mg.append("title").text("step " + m.step + " · " + (MARK_WORD[m.kind] || m.kind) + (m.label ? " · " + m.label : "") + " — click to open the step");
            mg.on("pointermove", function (evt) {
              evt.stopPropagation();
              tip.show(evt, [{ b: true, text: name(report, side) + " · step " + m.step + " · " + (MARK_WORD[m.kind] || m.kind) }, m.label ? { text: m.label } : null, { text: "click to open the step" }]);
            }).on("pointerleave", tip.hide).on("click", function (evt) {
              evt.stopPropagation();
              if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, m.step);
            });
          });
          // the why line, under the row — drawn last, over a backing, so no fold strikes through it
          var wy = ry + rowH + 10 + rows[lane].why * whyH;
          rows[lane].why++;
          whys.push({ id: c.id, y: wy, text: fit((c.label ? c.label + " · " : "") + "steps " + c.from + "–" + c.to + " · " + secs(c.seconds) + (c.why ? " — " + c.why : ""), avail) });
        }
        x += w + p.gap;
      });
      whys.forEach(function (wl) {
        svg.append("rect").attr("x", labW - 2).attr("y", wl.y - 10).attr("width", Math.min(avail + 4, wl.text.length * CH + 6)).attr("height", 13).attr("rx", 2).attr("fill", "var(--bg)").attr("fill-opacity", 0.92);
        svg.append("text").attr("class", "im-why").attr("data-id", wl.id).attr("x", labW).attr("y", wl.y).text(wl.text);
      });
    });
    function repaint() { host.innerHTML = ""; draw(host, report, im, st, tip, ctx); }
  }

  AgentDiff.block({
    id: "impact",
    title: "Where it mattered",
    question: "Which stretches of each run carried the impact — and which were quiet — with the timeline dilated where it mattered and constricted where it did not?",
    group: "trajectory",
    size: "wide",
    relevance: function (ctx) { var im = ctx.report && ctx.report.impact; return im && im.a && im.a.measurable ? 0.85 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, im = report && report.impact;
      if (!im || !im.a || !im.a.measurable) return ctx.empty(el, "No impact clusters were measured for this run, so the timeline cannot be weighted.");
      var root = H("div", { class: "im" });
      el.appendChild(root);
      var tip = tooltip(root);
      var task = report.task && report.task.id;
      var st = stateFor(task);
      if (im.narrative) root.appendChild(H("p", { class: "im-narr", text: im.narrative }));
      var host = H("div", { class: "im-chart" });
      function redraw() { host.innerHTML = ""; draw(host, report, im, st, tip, ctx); sync(); }
      var bar = H("div", { class: "im-bar" });
      bar.appendChild(H("span", null, [H("i", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "2px", verticalAlign: "-1px", marginRight: "4px", background: color("a") } }), H("span", { text: name(report, "a") })]));
      bar.appendChild(H("span", null, [H("i", { style: { display: "inline-block", width: "10px", height: "10px", borderRadius: "2px", verticalAlign: "-1px", marginRight: "4px", background: color("b") } }), H("span", { text: name(report, "b") })]));
      var seg = H("span", { class: "seg", role: "group", "aria-label": "width" });
      var modeBtns = {};
      [["focus", "width by impact"], ["even", "width by seconds (wall-clock)"]].forEach(function (pair) {
        modeBtns[pair[0]] = H("button", { text: pair[0], "data-mode": pair[0], title: pair[1], "aria-pressed": st.mode === pair[0] ? "true" : "false",
          onclick: function () { if (st.mode === pair[0]) return; st.mode = pair[0]; redraw(); } });
        seg.appendChild(modeBtns[pair[0]]);
      });
      bar.appendChild(seg);
      bar.appendChild(H("button", { text: "expand hot", "data-act": "expand-hot", title: "open every hot stretch", onclick: function () {
        ["a", "b"].forEach(function (s) {
          var run = im[s]; if (!run) return;
          var hotIds = Array.isArray(run.hot) ? run.hot.slice() : [];
          clustersOf(run).forEach(function (c) { if (c.kind === "hot" && hotIds.indexOf(c.id) < 0) hotIds.push(c.id); });
          hotIds.forEach(function (id) { st.open[skey(s, id)] = true; });
        });
        redraw();
      } }));
      bar.appendChild(H("button", { text: "collapse all", "data-act": "collapse-all", title: "close every open stretch and refold the quiet ones", onclick: function () { st.open = {}; st.unfolded = {}; redraw(); } }));
      root.appendChild(bar);
      root.appendChild(H("p", { class: "im-legend", text: "width ∝ impact (focus) · grey folds = quiet stretches, click to open · click a cluster for its steps · a tick opens the step · ◎ decisive · ▏fault · ✕ error · ↻ retry · ◆ milestone · ⇄ divergence · ■ answer" }));
      function sync() { Object.keys(modeBtns).forEach(function (k) { modeBtns[k].setAttribute("aria-pressed", st.mode === k ? "true" : "false"); }); }
      root.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { draw(host, report, im, st, tip, ctx); }, "impact:" + task) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) draw(host, report, im, st, tip, ctx);
      // the table: every cluster, both runs, no hovering
      var details = H("details", { class: "im-details" }, [H("summary", { text: "every stretch, both runs" })]);
      var t = H("table", { class: "im-table" });
      t.appendChild(H("tr", null, ["run", "lane", "steps", "s", "impact", "kind", "why"].map(function (h) { return H("th", { text: h }); })));
      ["a", "b"].forEach(function (side) {
        clustersOf(im[side]).forEach(function (c) {
          t.appendChild(H("tr", { "data-side": side, "data-id": c.id }, [
            H("td", { class: "n", text: name(report, side) }),
            H("td", { class: "n", text: c.lane || "" }),
            H("td", { class: "n", text: c.from + "–" + c.to }),
            H("td", { class: "n", text: secs(c.seconds) }),
            H("td", { class: "n", text: clamp01(c.impact).toFixed(2) }),
            H("td", { class: "n " + (c.kind || ""), text: c.kind || "work" }),
            H("td", { text: (c.label ? c.label + " — " : "") + (c.why || "") }),
          ]));
        });
      });
      details.appendChild(H("div", { class: "scroll-x" }, [t]));
      root.appendChild(details);
    },
  });
})(typeof window !== "undefined" ? window : this);
