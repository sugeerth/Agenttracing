/* AgentDiff block — Where it mattered.
 *
 * A focus-and-context timeline for multi-agent runs, drawn the way the
 * body chart draws a run: each run is one trunk, left to right (A above
 * B, A's branches growing up and B's down, so the two trunks face each
 * other). The orchestrator's own stretches lie on the trunk; a sub-agent's
 * stretch hangs off it as a branch — a short stem to a horizontal segment
 * named once with the sub-agent, nested sub-agents as sub-branches (depth
 * ≤ 2 drawn, deeper folded into the parent). The x-axis is not time: in
 * "focus" mode a stretch's length grows with its impact and a hot one is
 * also thicker, so what carried the outcome dilates; consecutive quiet
 * stretches constrict into one short dotted segment whose length scales
 * with the steps it folds (×N). "even" is plain wall-clock, so the reader
 * can see what the dilation did. A stretch opens on click to show its
 * marks (the decisive step, faults, errors, retries, milestones,
 * divergences, the answer) as leaves that open the step in the shared
 * inspector; a fold opens on click into its stretches, and any of them
 * folds it back.
 *
 * Reads `report.impact` ({a, b, narrative}); a run is {clusters, lanes,
 * total_s, total_steps, hot, narrative}: a cluster {id, from, to, steps,
 * seconds, lane, impact, kind, reasons, why, label, marks}, a lane {agent,
 * depth, parent}. Every length is a function of the cluster's `impact`
 * (0..1) or its `seconds`, every mark a step index.
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
      ".im-bar{display:flex;gap:4px 10px;flex-wrap:nowrap;white-space:nowrap;overflow:hidden;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".im-bar .seg{display:inline-flex;gap:2px;flex:0 0 auto}",
      ".im-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 8px;cursor:pointer;flex:0 0 auto}",
      ".im-bar button[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
      ".im-legend{font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".im-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".im-chart text{font-size:var(--fs-xs)}",
      ".im-chart .im-head{font-weight:700;letter-spacing:.04em}",
      ".im-chart .im-lab{fill:var(--ink-3);pointer-events:none}",
      ".im-chart .im-hwhy{fill:var(--ink-2);pointer-events:none;font-family:var(--mono)}",
      ".im-chart .im-why{fill:var(--ink-3)}",
      ".im-chart .im-cluster{cursor:pointer}.im-chart .im-cluster:hover{stroke-opacity:1}",
      ".im-chart .im-cluster.open{stroke-opacity:1}",
      ".im-chart .im-fold{cursor:pointer}.im-chart .im-fold text{fill:var(--ink-3);pointer-events:none;font-family:var(--mono)}",
      ".im-chart .im-fold:hover line{stroke:var(--ink)}",
      ".im-chart .im-mark{cursor:pointer}.im-chart .im-mark text.g{font-weight:700;paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
      ".im-chart .im-mark:hover text.g{fill:var(--ink)}",
      ".im-band + .im-band{margin-top:6px}",
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
  // consecutive quiet clusters constricted into one short segment (focus
  // mode only, and only while the reader has not opened it).
  function clustersOf(run) {
    return (run && Array.isArray(run.clusters) ? run.clusters : []).filter(function (c) { return c && isNum(c.from); })
      .slice().sort(function (p, q) { return p.from - q.from; });
  }
  //: state keys are side-qualified: both runs number their clusters c0, c1, …
  function skey(side, id) { return side + ":" + id; }
  function stepsIn(c) { return isNum(c.steps) ? c.steps : Math.max(1, (c.to || 0) - (c.from || 0) + 1); }
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
            // opened: every cluster of the fold shows, and any of them folds it back
            group.forEach(function (g) { out.push({ type: "cluster", id: g.id, c: g, fold: fid }); });
          } else {
            var lanes = [];
            group.forEach(function (g) { if (g.lane && lanes.indexOf(g.lane) < 0) lanes.push(g.lane); });
            out.push({ type: "fold", id: fid, clusters: group, lanes: lanes,
              steps: group.reduce(function (s, g) { return s + stepsIn(g); }, 0),
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
  //: a fold's length scales with the steps it constricts: 4–5 steps ≈ 20px, 11 ≈ 27px, 100 ≈ 46px
  function foldW(steps) { return 6 + 6 * Math.log(1 + Math.max(0, steps)) / Math.LN2; }
  //: the lanes: who is the root, each lane's depth and parent
  function laneInfo(run) {
    var info = {}, root = null;
    (run && Array.isArray(run.lanes) ? run.lanes : []).forEach(function (l) {
      if (!l || !l.agent) return;
      info[l.agent] = { depth: isNum(l.depth) ? l.depth : 1, parent: l.parent || null };
      if (!l.depth && root === null) root = l.agent;
    });
    if (root === null) {
      // no lanes listed: the lane with the first cluster is the run
      var cs = clustersOf(run);
      root = cs.length && cs[0].lane ? cs[0].lane : "run";
      info[root] = info[root] || { depth: 0, parent: null };
    }
    return { root: root, of: info };
  }
  function weight(c) { return clamp01(c.impact); }
  //: a 1.00 cluster is at least this many times the length of a floor-length one
  var RATIO = 4;
  //: a hot cluster's why, compact ("6✕ 6↻ 26s", the legend's glyphs)
  function compactWhy(c) {
    var r = c.reasons || {}, parts = [];
    if (r.decisive) parts.push("◎");
    if (r.errors) parts.push(r.errors + "✕");
    if (r.retries) parts.push(r.retries + "↻");
    if (isNum(r.wasted_s) && r.wasted_s >= 1) parts.push(secs(r.wasted_s));
    return parts.join(" ");
  }
  function onFault(c) {
    var r = c.reasons || {};
    if (isNum(r.fault_steps) && r.fault_steps > 0) return true;
    return Array.isArray(c.marks) && c.marks.some(function (m) { return m && m.kind === "fault"; });
  }

  /* One length scale for both bands: equal impacts get equal lengths
   * across A and B, and neither band runs past the available width. In
   * focus mode a cluster is max(floor, k·impact), a fold its step-scaled
   * length, an open cluster long enough for its marks; k is the largest
   * value that fits the longer band — and when that would leave a 1.00
   * cluster under RATIO× a floor one, the floor shrinks so k can take
   * the rest. In even mode every cluster is its seconds. */
  function scale(im, sides, states, avail, narrow, mode) {
    var wMin = narrow ? 6 : 10, gap = 2;
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
        per[s] = { units: units, widths: ws, gap: gap };
      });
      return per;
    }
    var budgets = {}, fs = {};
    sides.forEach(function (s) {
      var units = unitsOf(im[s], states, s);
      var fixed = gap * Math.max(0, units.length - 1), f = [];
      units.forEach(function (u) {
        if (u.type === "fold") fixed += foldW(u.steps);
        else if (states.open[skey(s, u.id)]) fixed += openWidth(u.c, avail, narrow);
        else f.push(weight(u.c));
      });
      budgets[s] = avail - fixed; fs[s] = f;
      per[s] = { units: units, gap: gap };
    });
    function total(f, floor, kk) { return f.reduce(function (t, v) { return t + Math.max(floor, kk * v); }, 0); }
    // the floor as given, k the largest that fits the longer band
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
      floor = Math.max(narrow ? 3 : 4, Math.min(wMin, w));
      k = RATIO * floor;
    }
    k = Math.max(0, Math.min(k, avail * 0.5));
    sides.forEach(function (s) {
      var p = per[s];
      var n = p.units.length;
      var free = avail - gap * Math.max(0, n - 1);
      p.floor = floor; p.k = k;
      p.widths = p.units.map(function (u) {
        if (u.type === "fold") return foldW(u.steps);
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
    return Math.min(avail * 0.35, Math.max(narrow ? 50 : 90, 40 + marks * (narrow ? 10 : 12)));
  }

  // ------------------------------------------------------------ drawing
  function draw(host, report, im, st, tip, ctx) {
    if (!d3) return;
    var W = Math.max(300, host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 640);
    var narrow = W < 560;
    var sides = ["a", "b"].filter(function (s) { return im[s] && im[s].measurable !== false && clustersOf(im[s]).length; });
    var padL = 4, padR = 6;
    var avail = W - padL - padR;
    var per = scale(im, sides, st, avail, narrow, st.mode);
    var levelH = narrow ? 16 : 20, head = 16, labH = 12, whyH = 13, foot = 4;
    ["a", "b"].forEach(function (side) {
      var run = im[side];
      if (sides.indexOf(side) < 0) {
        host.appendChild(ctx.h("p", { class: "im-none", "data-side": side, text: name(report, side) + ": " + (run && run.narrative ? run.narrative : "no clusters were measured for this run.") }));
        return;
      }
      var p = per[side];
      var lanes = laneInfo(run);
      function depthOf(lane) { if (lane === lanes.root) return 0; var l = lanes.of[lane]; return l ? Math.max(0, l.depth) : 1; }
      //: the lane a cluster is drawn in: its own down to depth 2, deeper ones fold into their depth-2 ancestor
      function drawLane(lane) {
        lane = lane || lanes.root;
        var guard = 0;
        while (depthOf(lane) > 2 && guard++ < 20) { var l = lanes.of[lane]; if (!l || !l.parent || !lanes.of[l.parent]) break; lane = l.parent; }
        return depthOf(lane) > 2 ? lanes.root : lane;
      }
      var L = 0;
      clustersOf(run).forEach(function (c) { L = Math.max(L, depthOf(drawLane(c.lane))); });
      // A's branches grow up from its trunk, B's down: the two trunks face each other
      var up = side === "a";
      var trunkY = up ? head + labH + L * levelH : head + 12;
      function yOf(d) { return up ? trunkY - d * levelH : trunkY + d * levelH; }
      var nOpen = p.units.filter(function (u) { return u.type === "cluster" && st.open[skey(side, u.id)]; }).length;
      var whyTop = up ? trunkY + 26 : yOf(L) + 24;
      var H = (nOpen ? whyTop + (nOpen - 1) * whyH : up ? trunkY + 14 : yOf(L) + labH) + foot;
      var hot = clustersOf(run).filter(function (c) { return c.kind === "hot"; }).length;
      var subs = Object.keys(lanes.of).filter(function (l) { return depthOf(l) > 0; }).length;
      var svg = d3.select(host).append("svg").attr("class", "im-band").attr("data-side", side).attr("data-mode", st.mode)
        .attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
        .attr("aria-label", name(report, side) + ": " + clustersOf(run).length + " stretches over " + subs + " sub-agent" + (subs === 1 ? "" : "s") + ", " + hot + " hot, length " + (st.mode === "focus" ? "by impact" : "by seconds"));
      // the run's line: name, steps, seconds
      svg.append("text").attr("class", "im-head").attr("x", padL).attr("y", 11).attr("fill", color(side))
        .text(fit(name(report, side) + " · " + (isNum(run.total_steps) ? run.total_steps + " steps · " : "") + secs(run.total_s), W - padL))
        .append("title").text(run.narrative || "");
      // where every unit starts
      var xs0 = [], x = padL;
      p.units.forEach(function (u, ui) { xs0.push(x); x += p.widths[ui] + p.gap; });
      var xEnd = Math.max(padL + 1, x - p.gap);
      // the trunk
      svg.append("line").attr("class", "im-trunk").attr("x1", padL).attr("x2", xEnd).attr("y1", trunkY).attr("y2", trunkY)
        .attr("stroke", color(side)).attr("stroke-width", 1).attr("stroke-opacity", 0.35);
      // pass 1: which row each unit sits in, and the branches — consecutive
      // units of one sub-agent hang off the trunk (or off the parent's branch) as one branch
      var branches = [], active = { 1: null, 2: null }, cur = { 1: null, 2: null };
      p.units.forEach(function (u, ui) {
        if (u.type === "fold") { active[1] = active[2] = null; u.y = trunkY; u.depth = 0; return; }
        var dl = drawLane(u.c.lane), d = depthOf(dl);
        u.depth = d; u.dlane = dl;
        if (d === 0) { active[1] = active[2] = null; u.y = trunkY; return; }
        var anchorY = trunkY, anchorLane = null;
        if (d === 2) {
          var parent = lanes.of[dl] && lanes.of[dl].parent;
          if (parent && active[1] === parent) { anchorY = yOf(1); anchorLane = parent; }
        } else active[2] = null;
        if (active[d] !== dl) {
          cur[d] = { lane: dl, depth: d, x0: xs0[ui], x1: xs0[ui] + p.widths[ui], y: yOf(d), anchorY: anchorY, anchor: anchorLane, units: [] };
          branches.push(cur[d]);
          active[d] = dl;
          if (d === 1) active[2] = null;
        }
        cur[d].units.push(ui);
        cur[d].x1 = xs0[ui] + p.widths[ui];
        if (d === 2 && anchorLane && cur[1]) cur[1].x1 = Math.max(cur[1].x1, cur[d].x1);
        u.y = yOf(d);
      });
      // the branches: a stem, a thin baseline, the sub-agent's name once on the far
      // side — the longest branches label first, and a label that would overlap one
      // already placed in its row is left to the tooltip
      var placed = {};
      branches.slice().sort(function (p1, p2) { return (p2.x1 - p2.x0) - (p1.x1 - p1.x0); }).forEach(function (b) {
        var lw = String(b.lane).length * CH, row = placed[b.depth] || (placed[b.depth] = []);
        if (b.x0 + lw > W - padR) return;
        if (row.some(function (r) { return b.x0 < r[1] + 6 && b.x0 + lw + 6 > r[0]; })) return;
        row.push([b.x0, b.x0 + lw]); b.labelled = true;
      });
      branches.forEach(function (b) {
        var g = svg.append("g").attr("class", "im-branch").attr("data-lane", b.lane).attr("data-side", side).attr("data-depth", b.depth);
        g.append("line").attr("x1", b.x0).attr("x2", b.x0).attr("y1", b.anchorY).attr("y2", b.y).attr("stroke", color(side)).attr("stroke-width", 1).attr("stroke-opacity", 0.45);
        g.append("line").attr("x1", b.x0).attr("x2", b.x1).attr("y1", b.y).attr("y2", b.y).attr("stroke", color(side)).attr("stroke-width", 1).attr("stroke-opacity", 0.3);
        if (b.labelled) g.append("text").attr("class", "im-lab").attr("x", b.x0).attr("y", b.y + (up ? -4 : 11)).text(String(b.lane)).append("title").text(b.lane);
        g.append("title").text(b.lane + " · " + b.units.length + " stretch" + (b.units.length === 1 ? "" : "es"));
      });
      //: the x where the next thing in this row starts — or the next branch's stem, whichever is first
      function nextInRow(ui, y) {
        for (var j = ui + 1; j < p.units.length; j++) { if (p.units[j].y === y || p.units[j].depth > 0) return xs0[j]; }
        return xEnd;
      }
      // pass 2: the units, left to right
      var whys = [], whyEnd = {};
      p.units.forEach(function (u, ui) {
        var w = p.widths[ui], x0 = xs0[ui];
        if (u.type === "fold") {
          var fg = svg.append("g").attr("class", "im-fold").attr("data-ids", u.id).attr("data-side", side).attr("data-steps", u.steps);
          fg.append("line").attr("x1", x0).attr("x2", x0 + w).attr("y1", trunkY).attr("y2", trunkY)
            .attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-dasharray", "1.5 2.5").attr("stroke-linecap", "round");
          if (w >= 18) fg.append("text").attr("x", x0 + w / 2).attr("y", up ? trunkY + 12 : trunkY - 5).attr("text-anchor", "middle").text("×" + u.steps);
          fg.append("rect").attr("x", x0).attr("y", trunkY - 8).attr("width", w).attr("height", 16).attr("fill", "transparent");
          fg.append("title").text(u.steps + " steps · " + secs(u.seconds) + " · " + u.lanes.join(", ") + " — " + u.clusters.length + " quiet stretches folded; click to open");
          fg.on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: name(report, side) + " · ×" + u.steps + " steps folded" }, { text: u.steps + " steps · " + secs(u.seconds) + " · " + u.lanes.join(", ") }, { text: "steps " + u.from + "–" + u.to + " · " + u.clusters.length + " quiet stretches · click to open" }]);
          }).on("pointerleave", tip.hide).on("click", function () { st.unfolded[skey(side, u.id)] = true; repaint(); });
          return;
        }
        var c = u.c, y = u.y, open = !!st.open[skey(side, u.id)];
        var quiet = c.kind === "quiet", hotK = c.kind === "hot", fault = onFault(c);
        var imp = clamp01(c.impact);
        var sw = quiet ? 1.5 : 1.5 + 3.5 * imp;
        var op = quiet ? 0.5 : 0.45 + 0.55 * imp;
        var seg = svg.append("path").attr("class", "im-cluster" + (open ? " open" : "")).attr("data-id", c.id).attr("data-kind", c.kind || "work").attr("data-side", side).attr("data-lane", c.lane || lanes.root)
          .attr("d", "M" + x0.toFixed(2) + "," + y + "H" + (x0 + w).toFixed(2))
          .attr("stroke", fault ? "var(--bad)" : quiet ? "var(--ink-3)" : color(side)).attr("stroke-width", sw).attr("stroke-opacity", op).attr("fill", "none");
        if (u.fold) seg.attr("data-fold", u.fold);
        seg.append("title").text(name(report, side) + " · " + (c.lane || lanes.root) + " · steps " + c.from + "–" + c.to + " · " + secs(c.seconds) + " · impact " + imp.toFixed(2) + (c.why ? " — " + c.why : ""));
        // a thin segment is hard to hit: a transparent band over it
        svg.append("rect").attr("x", x0).attr("y", y - 6).attr("width", w).attr("height", 12).attr("fill", "transparent").style("cursor", "pointer")
          .on("pointermove", onMove).on("pointerleave", tip.hide).on("click", onClick);
        function onMove(evt) {
          tip.show(evt, [{ b: true, text: name(report, side) + " · " + (c.lane || lanes.root) + " · " + (c.label || c.kind) },
            { text: "steps " + c.from + "–" + c.to + " (" + stepsIn(c) + ") · " + secs(c.seconds) + " · impact " + imp.toFixed(2) + " · " + (c.kind || "work") },
            c.why ? { text: c.why } : null,
            { text: u.fold ? "click to fold back" : open ? "click to collapse" : "click to open its steps" }]);
        }
        function onClick() {
          if (u.fold) { delete st.unfolded[skey(side, u.fold)]; repaint(); return; }
          var k = skey(side, u.id); if (st.open[k]) delete st.open[k]; else st.open[k] = true; repaint();
        }
        seg.on("pointermove", onMove).on("pointerleave", tip.hide).on("click", onClick);
        // a hot stretch says why, compactly, on the near side (toward the trunk; the trunk's own beyond it)
        var nearY = up ? y + 12 : y - 5;
        if (!open && hotK) {
          var why = compactWhy(c);
          var room = nextInRow(ui, y) - x0 - 2;
          if (why && why.length * CH <= room && x0 >= (whyEnd[nearY] || -Infinity)) {
            svg.append("text").attr("class", "im-hwhy").attr("data-id", c.id).attr("x", x0).attr("y", nearY).text(why);
            whyEnd[nearY] = x0 + why.length * CH + 6;
          }
        }
        if (open) {
          // the marks as leaves at their step's place along the segment
          var marks = Array.isArray(c.marks) ? c.marks.filter(function (m) { return m && isNum(m.step); }).slice().sort(function (p, q) { return p.step - q.step; }) : [];
          var span = Math.max(1, (c.to || 0) - (c.from || 0));
          var inner = Math.max(1, w - 12);
          var xs = marks.map(function (m) { return x0 + 6 + inner * Math.max(0, Math.min(1, (m.step - c.from) / span)); });
          // two marks on one step sit side by side rather than on top of each other
          for (var mi2 = 1; mi2 < xs.length; mi2++) { if (xs[mi2] < xs[mi2 - 1] + 9) xs[mi2] = xs[mi2 - 1] + 9; }
          marks.forEach(function (m, mi) {
            var mx = xs[mi];
            var mg = svg.append("g").attr("class", "im-mark").attr("data-step", m.step).attr("data-kind", m.kind || "mark").attr("data-side", side);
            var red = m.kind === "decisive" || m.kind === "fault" || m.kind === "error";
            if (m.kind === "decisive") {
              mg.append("circle").attr("cx", mx).attr("cy", y).attr("r", 5).attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-width", 2);
            } else if (m.kind === "fault") {
              mg.append("line").attr("x1", mx).attr("x2", mx).attr("y1", y - 6).attr("y2", y + 6).attr("stroke", "var(--bad)").attr("stroke-width", 2.5);
            } else {
              mg.append("text").attr("class", "g").attr("x", mx).attr("y", y + 4).attr("text-anchor", "middle").attr("fill", red ? "var(--bad)" : "var(--ink)").text(GLYPH[m.kind] || "•");
            }
            mg.append("rect").attr("x", mx - 5).attr("y", y - 7).attr("width", 10).attr("height", 14).attr("fill", "transparent");
            mg.append("title").text("step " + m.step + " · " + (MARK_WORD[m.kind] || m.kind) + (m.label ? " · " + m.label : "") + " — click to open the step");
            mg.on("pointermove", function (evt) {
              evt.stopPropagation();
              tip.show(evt, [{ b: true, text: name(report, side) + " · step " + m.step + " · " + (MARK_WORD[m.kind] || m.kind) }, m.label ? { text: m.label } : null, { text: "click to open the step" }]);
            }).on("pointerleave", tip.hide).on("click", function (evt) {
              evt.stopPropagation();
              if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, m.step);
            });
          });
          // its line, under the band: label · steps · seconds — why
          whys.push({ id: c.id, text: fit((c.lane && c.lane !== lanes.root ? c.lane + " · " : "") + (c.label ? c.label + " · " : "") + "steps " + c.from + "–" + c.to + " · " + secs(c.seconds) + (c.why ? " — " + c.why : ""), avail) });
        }
      });
      whys.forEach(function (wl, i) {
        svg.append("text").attr("class", "im-why").attr("data-id", wl.id).attr("x", padL).attr("y", whyTop + i * whyH).text(wl.text);
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
      var seg = H("span", { class: "seg", role: "group", "aria-label": "length" });
      var modeBtns = {};
      [["focus", "length by impact"], ["even", "length by seconds (wall-clock)"]].forEach(function (pair) {
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
      root.appendChild(H("p", { class: "im-legend", text: "trunk = run · branch = sub-agent · length ∝ impact · thick = hot · red = fault's path · ⋯×N = quiet steps folded, click to open · click a stretch for its steps · ◎ decisive ▏fault ✕ error ↻ retry ◆ milestone ⇄ divergence ■ answer" }));
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
