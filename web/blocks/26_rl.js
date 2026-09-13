/* AgentDiff block — Reward & credit.
 *
 * The RL view of a pair: where each run earned and lost reward, who gets
 * the credit, and which trajectory a preference judge would pick. Three
 * drawings, one vocabulary:
 *
 *  - the return curves: both runs' cumulative return over steps on one
 *    axis, the zero line drawn, the first step where the two returns
 *    part by a whole point ringed, the answer step a square;
 *  - the reward thread: the same technology as "Where it mattered" — one
 *    horizontal thread per run, its stretches in step order on a length
 *    scale shared by A and B where length is the stretch's reward-driven
 *    impact, consecutive quiet stretches folded into one dotted "×N"
 *    segment as long as log2 of the steps it holds. On the thread every
 *    step whose |reward| reaches the run's 90th percentile is a tick, up
 *    and green for a gain, down and red for a loss, as tall as the reward;
 *    the decisive step is ringed, the answer squared; a faint bar under
 *    the thread is the credit (Shapley) a stretch's steps carry. A fold
 *    dilates on click, a stretch opens to its rewarded steps as leaves,
 *    a leaf opens the step in the shared inspector;
 *  - the preference: the chosen trajectory over the rejected one, and why.
 *
 * Reads `report.rl` ({measurable, source, gamma, a, b, preference,
 * narrative}); a run is {steps, return, discounted_return, positive,
 * negative, zero, rewards, largest, credit, clusters, narrative}: a
 * reward {step, reward, cum, to_go, discounted_to_go, credit, labels,
 * agent, kind, name}, a cluster {id, from, to, steps, seconds, lane,
 * impact, kind, why, label, marks}. Every length is a function of a
 * cluster's `impact` (0..1) or a reward's magnitude, every mark a step.
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
      ".rl{--rl-a:var(--a);--rl-b:var(--b);position:relative}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .rl{--rl-a:#3987e5;--rl-b:#d95926}}",
      ":root[data-theme=dark] .rl{--rl-a:#3987e5;--rl-b:#d95926}",
      ".rl-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".rl-bar{display:flex;gap:4px 10px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".rl-bar .rl-chip{display:inline-block;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 8px;white-space:nowrap;font-variant-numeric:tabular-nums}",
      ".rl-bar .rl-chip b{font-weight:600}",
      ".rl-bar .rl-chip .up{color:var(--good)}.rl-bar .rl-chip .dn{color:var(--bad)}",
      ".rl-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 8px;cursor:pointer;flex:0 0 auto}",
      ".rl-legend{font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".rl-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rl-chart text{font-size:var(--fs-xs)}",
      ".rl-chart .rl-ax{fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".rl-chart .rl-tlabel{fill:var(--ink)}",
      ".rl-chart .rl-fold{cursor:pointer}.rl-chart .rl-fold text{fill:var(--ink-3);pointer-events:none;font-family:var(--mono)}",
      ".rl-chart .rl-fold:hover .rl-fold-line{stroke:var(--ink)}",
      ".rl-chart .rl-cluster{cursor:pointer}.rl-chart .rl-cluster:hover path{stroke-opacity:1}.rl-chart .rl-cluster.open path{stroke-opacity:1}",
      ".rl-chart .rl-tick{cursor:pointer}.rl-chart .rl-tick:hover line{stroke-width:2.6}",
      ".rl-chart .rl-leaf{cursor:pointer}.rl-chart .rl-leaf text{pointer-events:none}.rl-chart .rl-leaf:hover .rl-tlabel{fill:var(--ink)}",
      ".rl-chart .rl-leaf .rl-tlabel{fill:var(--ink-2)}.rl-chart .rl-leaf .g{font-weight:700}",
      ".rl-chart .rl-why{fill:var(--ink-3);font-family:var(--mono)}",
      ".rl-chart .rl-parted-lab{fill:var(--ink-2);font-family:var(--mono)}",
      ".rl-curve{margin:0 0 8px}",
      ".rl-band + .rl-band{margin-top:6px}",
      ".rl-none{font-size:var(--fs-xs);color:var(--ink-3);margin:4px 0 8px}",
      ".rl-pref{font-size:var(--fs-m);color:var(--ink);margin:8px 0 0;max-width:100ch}.rl-pref b{font-weight:600}.rl-pref .basis{color:var(--ink-2)}",
      ".rl-details{margin-top:8px;font-size:var(--fs-xs)}.rl-details summary{cursor:pointer;color:var(--ink-2)}",
      ".rl-details h5{margin:8px 0 0;font-size:var(--fs-xs);font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}",
      ".rl-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:6px}",
      ".rl-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".rl-table td{padding:3px 10px 3px 0;color:var(--ink-2);vertical-align:top}.rl-table td.n{white-space:nowrap}",
      ".rl-table td.pos{color:var(--good)}.rl-table td.neg{color:var(--bad)}.rl-table td.hot{color:var(--ink);font-weight:600}.rl-table td.quiet{color:var(--ink-3)}",
      ".rl-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:340px}",
      ".rl-tip b{color:var(--ink)}",
    ].join("\n");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------ helpers
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v >= 1 ? v.toFixed(1) + "s" : v.toFixed(2) + "s"; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }
  function color(side) { return "var(--rl-" + side + ")"; }
  //: a reward with its sign: +12.4, −1.0, +0.05, 0
  function signed(v) {
    if (!isNum(v)) return "—";
    if (v === 0) return "0";
    var a = Math.abs(v);
    var s = a >= 100 || (a >= 10 && Math.round(a) === a) ? String(Math.round(a)) : a >= 1 || a.toFixed(1) !== "0.0" ? a.toFixed(1) : a.toFixed(2);
    return (v < 0 ? "−" : "+") + s;
  }
  //: ~6.2px per character at the page's small size; good enough to truncate by
  var CH = 6.2;
  function fit(text, px) {
    text = String(text === null || text === undefined ? "" : text).replace(/\s+/g, " ").trim();
    var n = Math.floor(px / CH);
    if (n < 2) return "";
    return text.length > n ? text.slice(0, Math.max(1, n - 1)) + "…" : text;
  }
  function clamp01(v) { return isNum(v) ? Math.max(0, Math.min(1, v)) : 0; }
  //: nearest-rank quantile of a list of numbers
  function quantile(values, q) {
    var v = values.filter(isNum).slice().sort(function (p, r) { return p - r; });
    if (!v.length) return 0;
    return v[Math.max(0, Math.min(v.length - 1, Math.ceil(q * v.length) - 1))];
  }

  //: per task: which stretches are open, which folds are unfolded
  var STATE = {};
  function stateFor(task) {
    var k = String(task || "");
    return STATE[k] || (STATE[k] = { open: {}, unfolded: {} });
  }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "rl-tip"; tip.hidden = true; root.appendChild(tip);
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

  // ------------------------------------------------------------ the data
  function measurable(run) { return !!(run && run.measurable !== false && clustersOf(run).length); }
  function clustersOf(run) {
    return (run && Array.isArray(run.clusters) ? run.clusters : []).filter(function (c) { return c && isNum(c.from); })
      .slice().sort(function (p, q) { return p.from - q.from; });
  }
  function rewardsOf(run) {
    return (run && Array.isArray(run.rewards) ? run.rewards : []).filter(function (r) { return r && isNum(r.step) && isNum(r.reward); })
      .slice().sort(function (p, q) { return p.step - q.step; });
  }
  //: the rewarded steps inside a stretch
  function rewardsIn(run, c) { return rewardsOf(run).filter(function (r) { return r.step >= c.from && r.step <= c.to; }); }
  //: a stretch's leaves: its non-zero rewards — the LEAF_CAP largest when there are more, in step order
  var LEAF_CAP = 20;
  function leavesOf(run, c) {
    var all = rewardsIn(run, c).filter(function (r) { return r.reward !== 0; });
    if (all.length <= LEAF_CAP) return { leaves: all, more: 0 };
    var top = all.slice().sort(function (p, q) { return Math.abs(q.reward) - Math.abs(p.reward) || p.step - q.step; }).slice(0, LEAF_CAP)
      .sort(function (p, q) { return p.step - q.step; });
    return { leaves: top, more: all.length - LEAF_CAP };
  }
  //: the run's 90th percentile of |reward| — the ticks are the steps at or above it
  function threshold(run) { return quantile(rewardsOf(run).map(function (r) { return Math.abs(r.reward); }), 0.9); }
  function ticksOf(run) {
    var t = threshold(run);
    return rewardsOf(run).filter(function (r) { return r.reward !== 0 && Math.abs(r.reward) >= t; });
  }
  function maxAbs(run) { return rewardsOf(run).reduce(function (m, r) { return Math.max(m, Math.abs(r.reward)); }, 0); }
  //: a stretch's credit: the sum over its steps' credit, null when none of them carries any
  function creditOf(run, c) {
    var any = false, sum = 0;
    rewardsIn(run, c).forEach(function (r) { if (isNum(r.credit)) { any = true; sum += r.credit; } });
    return any ? sum : null;
  }
  function rewardSum(run, c) { return rewardsIn(run, c).reduce(function (t, r) { return t + r.reward; }, 0); }
  //: the marks a run's stretches carry, by kind
  function markSteps(run, kind) {
    var out = [];
    clustersOf(run).forEach(function (c) { (Array.isArray(c.marks) ? c.marks : []).forEach(function (m) { if (m && m.kind === kind && isNum(m.step)) out.push(m); }); });
    return out;
  }
  //: the answer step: the answer mark, else the reward tagged as the answer, else the last rewarded step
  function answerStep(run) {
    var m = markSteps(run, "answer");
    if (m.length) return m[m.length - 1].step;
    var rs = rewardsOf(run);
    for (var i = rs.length - 1; i >= 0; i--) { var r = rs[i]; if (r.kind === "answer" || (Array.isArray(r.labels) && r.labels.indexOf("answer") >= 0)) return r.step; }
    return rs.length ? rs[rs.length - 1].step : null;
  }
  //: the cumulative return at a step: the cum of the last reward at or before it (0 before the first);
  //: the running series is computed once per list and searched, so a loop over the steps stays linear
  function seriesOf(rs) { var v = 0; return rs.map(function (r) { v = isNum(r.cum) ? r.cum : v + r.reward; return v; }); }
  function cumAt(rs, step) {
    var ser = rs._cum || (rs._cum = seriesOf(rs));
    var lo = 0, hi = rs.length - 1, at = -1;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (rs[mid].step <= step) { at = mid; lo = mid + 1; } else hi = mid - 1; }
    return at < 0 ? 0 : ser[at];
  }
  //: the first step where the two returns part by a whole point, or null
  function partedAt(ra, rb) {
    var steps = {};
    ra.concat(rb).forEach(function (r) { steps[r.step] = true; });
    var ordered = Object.keys(steps).map(Number).sort(function (p, q) { return p - q; });
    for (var i = 0; i < ordered.length; i++) { if (Math.abs(cumAt(ra, ordered[i]) - cumAt(rb, ordered[i])) >= 1) return ordered[i]; }
    return null;
  }
  function labelsOf(r) { return Array.isArray(r.labels) ? r.labels.filter(Boolean).join(", ") : ""; }
  function leafText(r) { return "step " + r.step + (r.name ? " · " + r.name : "") + " · " + signed(r.reward) + (labelsOf(r) ? " · " + labelsOf(r) : ""); }
  function leafTip(report, side, r) {
    return [{ b: true, text: name(report, side) + " · step " + r.step + (r.name ? " · " + r.name : "") + (r.agent ? " · " + r.agent : "") },
      { text: "reward " + signed(r.reward) + " · return so far " + signed(r.cum) + (isNum(r.to_go) ? " · to go " + signed(r.to_go) : "") + (isNum(r.credit) ? " · credit " + signed(r.credit) : "") },
      labelsOf(r) ? { text: labelsOf(r) } : null, { text: "click to open the step" }];
  }
  function select(report, side, step) { if (AgentDiff.charts && AgentDiff.charts.selectStep) AgentDiff.charts.selectStep(report, side, step); }

  // ------------------------------------------------------------ the units
  //
  // A band is a sequence of units: a stretch, or a fold — two or more
  // consecutive quiet stretches constricted into one dotted segment while
  // the reader has not opened it.
  function skey(side, id) { return side + ":" + id; }
  function stepsIn(c) { return isNum(c.steps) ? c.steps : Math.max(1, (c.to || 0) - (c.from || 0) + 1); }
  function unitsOf(run, st, side) {
    var cs = clustersOf(run), out = [], i = 0;
    while (i < cs.length) {
      var c = cs[i];
      if (c.kind === "quiet") {
        var j = i;
        while (j < cs.length && cs[j].kind === "quiet") j++;
        var group = cs.slice(i, j);
        var fid = group.map(function (g) { return g.id; }).join(",");
        if (group.length >= 2) {
          if (st.unfolded[skey(side, fid)]) group.forEach(function (g) { out.push({ type: "cluster", id: g.id, c: g, fold: fid }); });
          else {
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
  function weight(c) { return clamp01(c.impact); }
  //: a 1.00 stretch is at least this many times the length of a floor-length one
  var RATIO = 4;

  /* One length scale for both threads: equal impacts get equal lengths
   * across A and B, and neither thread runs past the available width. A
   * stretch is max(floor, k·impact), a fold its step-scaled length; k is
   * the largest value that fits the longer thread — and when that would
   * leave a 1.00 stretch under RATIO× a floor one, the floor shrinks so k
   * can take the rest. Opening a stretch never moves the thread. */
  function scale(rl, sides, st, avail, narrow) {
    var wMin = narrow ? 6 : 10, gap = 2;
    var per = {}, budgets = {}, fs = {}, k = Infinity;
    sides.forEach(function (s) {
      var units = unitsOf(rl[s], st, s);
      var fixed = gap * Math.max(0, units.length - 1), f = [];
      units.forEach(function (u) { if (u.type === "fold") fixed += foldW(u.steps); else f.push(weight(u.c)); });
      budgets[s] = avail - fixed; fs[s] = f;
      per[s] = { units: units, gap: gap };
    });
    function total(f, floor, kk) { return f.reduce(function (t, v) { return t + Math.max(floor, kk * v); }, 0); }
    sides.forEach(function (s) {
      if (!fs[s].length) return;
      var lo = 0, hi = avail;
      for (var it = 0; it < 40; it++) { var mid = (lo + hi) / 2; if (total(fs[s], wMin, mid) <= budgets[s]) lo = mid; else hi = mid; }
      k = Math.min(k, lo);
    });
    if (!isFinite(k)) k = 0;
    var floor = wMin;
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
      var p = per[s], n = p.units.length;
      var free = avail - gap * Math.max(0, n - 1);
      p.floor = floor; p.k = k;
      p.widths = p.units.map(function (u) { return u.type === "fold" ? foldW(u.steps) : Math.max(floor, k * weight(u.c)); });
      var sum = p.widths.reduce(function (t, w) { return t + w; }, 0);
      if (sum > free && sum > 0) p.widths = p.widths.map(function (w) { return w * free / sum; });
    });
    return per;
  }

  // ------------------------------------------------------------ the return curves
  function drawCurve(host, report, rl, tip, W, sides) {
    if (!d3 || !sides.length) return;
    var narrow = W < 560;
    var m = { l: 40, r: narrow ? 8 : 64, t: 12, b: 16 }, H = 118;
    var rs = {}, maxStep = 1, lo = 0, hi = 0;
    sides.forEach(function (s) {
      rs[s] = rewardsOf(rl[s]);
      maxStep = Math.max(maxStep, isNum(rl[s].steps) ? rl[s].steps - 1 : 0, rs[s].length ? rs[s][rs[s].length - 1].step : 0);
      rs[s].forEach(function (r) { var c = cumAt(rs[s], r.step); lo = Math.min(lo, c); hi = Math.max(hi, c); });
    });
    if (hi === lo) hi = lo + 1;
    var x = d3.scaleLinear().domain([0, maxStep]).range([m.l, W - m.r]);
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([H - m.b, m.t]);
    var parted = sides.length === 2 ? partedAt(rs.a, rs.b) : null;
    var svg = d3.select(host).append("svg").attr("class", "rl-curve").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", "cumulative return over steps, " + sides.map(function (s) { return name(report, s) + " " + signed(rl[s]["return"]); }).join(" and ") +
        (parted !== null ? "; the two returns part at step " + parted : "; the two returns never part by a whole point"));
    // the frame: the zero line, the ends of the y range, the ends of the x range
    svg.append("line").attr("class", "rl-zero").attr("x1", m.l).attr("x2", W - m.r).attr("y1", y(0)).attr("y2", y(0)).attr("stroke", "var(--ink-3)").attr("stroke-width", 1).attr("stroke-opacity", 0.6);
    svg.append("text").attr("class", "rl-ax").attr("x", m.l - 6).attr("y", y(0) + 4).attr("text-anchor", "end").text("0");
    var yd = y.domain();
    if (yd[1] > 0) svg.append("text").attr("class", "rl-ax").attr("x", m.l - 6).attr("y", y(yd[1]) + 4).attr("text-anchor", "end").text(signed(yd[1]));
    if (yd[0] < 0) svg.append("text").attr("class", "rl-ax").attr("x", m.l - 6).attr("y", y(yd[0]) + 4).attr("text-anchor", "end").text(signed(yd[0]));
    svg.append("text").attr("class", "rl-ax").attr("x", m.l).attr("y", H - 3).text("step 0");
    svg.append("text").attr("class", "rl-ax").attr("x", W - m.r).attr("y", H - 3).attr("text-anchor", "end").text("step " + maxStep);
    var line = d3.line().x(function (d) { return x(d[0]); }).y(function (d) { return y(d[1]); }).curve(d3.curveStepAfter);
    var ends = [];
    sides.forEach(function (s) {
      var pts = [[0, 0]];
      rs[s].forEach(function (r) { pts.push([r.step, cumAt(rs[s], r.step)]); });
      var last = pts[pts.length - 1];
      if (last[0] < maxStep) pts.push([maxStep, last[1]]);
      svg.append("path").attr("class", "rl-cum").attr("data-side", s).attr("d", line(pts)).attr("fill", "none")
        .attr("stroke", color(s)).attr("stroke-width", 1.6).attr("stroke-opacity", 0.9)
        .append("title").text(name(report, s) + " · return " + signed(rl[s]["return"]) + " · discounted " + signed(rl[s].discounted_return));
      // the answer step, a square on the curve
      var ans = answerStep(rl[s]);
      if (isNum(ans)) {
        svg.append("rect").attr("class", "rl-answer").attr("data-side", s).attr("x", x(ans) - 3).attr("y", y(cumAt(rs[s], ans)) - 3).attr("width", 6).attr("height", 6).attr("fill", color(s))
          .append("title").text(name(report, s) + " · the answer at step " + ans + " · return " + signed(cumAt(rs[s], ans)));
      }
      ends.push({ s: s, y: y(last[1]), v: last[1] });
      // the rewarded steps: an invisible hit target each, so the curve explains itself
      rs[s].forEach(function (r) {
        svg.append("circle").attr("class", "rl-pt").attr("data-side", s).attr("data-step", r.step).attr("cx", x(r.step)).attr("cy", y(cumAt(rs[s], r.step))).attr("r", 5).attr("fill", "transparent").style("cursor", "pointer")
          .on("pointermove", function (evt) { tip.show(evt, leafTip(report, s, r)); }).on("pointerleave", tip.hide)
          .on("click", function () { select(report, s, r.step); });
      });
    });
    // the final returns at the ends of the lines, kept apart when the lines end together
    if (!narrow) {
      ends.sort(function (p, q) { return p.y - q.y; });
      for (var i = 1; i < ends.length; i++) { if (ends[i].y < ends[i - 1].y + 12) ends[i].y = ends[i - 1].y + 12; }
      ends.forEach(function (e) {
        svg.append("text").attr("class", "rl-end").attr("data-side", e.s).attr("x", W - m.r + 6).attr("y", e.y + 4).attr("fill", color(e.s)).attr("font-weight", 600).text(signed(e.v));
      });
    }
    // where the two returns part
    if (parted !== null) {
      var ya = y(cumAt(rs.a, parted)), yb = y(cumAt(rs.b, parted)), px = x(parted);
      svg.append("line").attr("class", "rl-parted-gap").attr("x1", px).attr("x2", px).attr("y1", Math.min(ya, yb)).attr("y2", Math.max(ya, yb)).attr("stroke", "var(--ink-3)").attr("stroke-width", 1).attr("stroke-dasharray", "2 2");
      svg.append("circle").attr("class", "rl-parted").attr("data-step", parted).attr("cx", px).attr("cy", (ya + yb) / 2).attr("r", 5).attr("fill", "none").attr("stroke", "var(--ink)").attr("stroke-width", 1.4)
        .append("title").text("the returns part at step " + parted + ": " + name(report, "a") + " " + signed(cumAt(rs.a, parted)) + ", " + name(report, "b") + " " + signed(cumAt(rs.b, parted)));
      // the words sit beside the ring, on the side the lines leave empty: above them when they fall from here, below when they rise
      var lab = "parted at step " + parted, lw = lab.length * CH;
      var right = px + 10 + lw <= W - m.r;
      var endA = cumAt(rs.a, maxStep), endB = cumAt(rs.b, maxStep), falling = endA + endB < cumAt(rs.a, parted) + cumAt(rs.b, parted);
      var ly = falling ? Math.min(ya, yb) - 5 : Math.max(ya, yb) + 12;
      ly = Math.max(m.t + 4, Math.min(H - m.b - 2, ly));
      svg.append("text").attr("class", "rl-parted-lab").attr("x", right ? px + 10 : px - 10).attr("y", ly).attr("text-anchor", right ? "start" : "end").text(lab);
    }
  }

  // ------------------------------------------------------------ the reward threads
  var ROW = 16;
  function drawBands(host, report, rl, st, tip, ctx, W, sides, repaint) {
    if (!d3) return;
    var narrow = W < 560, padR = 6, yT = 24;
    function runLabel(s) { return narrow ? name(report, s) + " · " + signed(rl[s]["return"]) : name(report, s) + " · " + (isNum(rl[s].steps) ? rl[s].steps + " steps · " : "") + "return " + signed(rl[s]["return"]); }
    var x0 = 0;
    sides.forEach(function (s) { x0 = Math.max(x0, 16 + fit(runLabel(s), narrow ? 110 : 260).length * CH * 1.12 + 12 + 12); });
    var availT = Math.max(60, W - padR - x0);
    var per = scale(rl, sides, st, availT, narrow);
    // the credit scale is shared: the tallest bar is the stretch carrying the most |credit| in either run
    var creditMax = 0;
    sides.forEach(function (s) {
      per[s].units.forEach(function (u) {
        var v = null;
        (u.type === "fold" ? u.clusters : [u.c]).forEach(function (c) { var cv = creditOf(rl[s], c); if (isNum(cv)) v = (v || 0) + cv; });
        if (isNum(v)) creditMax = Math.max(creditMax, Math.abs(v));
      });
    });
    var cap = W - padR - (narrow ? 170 : 300);
    ["a", "b"].forEach(function (side) {
      var run = rl[side];
      if (sides.indexOf(side) < 0) {
        host.appendChild(ctx.h("p", { class: "rl-none", "data-side": side, text: name(report, side) + ": " + (run && run.narrative ? run.narrative : "no rewards were measured for this run.") }));
        return;
      }
      var p = per[side], cs = clustersOf(run), rs = rewardsOf(run);
      // where every unit sits on the thread, and which unit holds each stretch
      var xs0 = [], x = x0, unitOf = {};
      p.units.forEach(function (u, ui) {
        xs0.push(x); x += p.widths[ui] + p.gap;
        if (u.type === "fold") u.clusters.forEach(function (c) { unitOf[c.id] = ui; }); else unitOf[u.id] = ui;
      });
      var xEnd = W - padR;
      //: the x of a step on the thread: inside the unit that holds it, proportionally; between units, at their seam
      function stepX(step) {
        for (var ui = 0; ui < p.units.length; ui++) {
          var u = p.units[ui], from = u.type === "fold" ? u.from : u.c.from, to = u.type === "fold" ? u.to : u.c.to;
          if (step >= from && step <= to) return xs0[ui] + p.widths[ui] * Math.max(0, Math.min(1, (step - from) / Math.max(1, to - from)));
          if (step < from) return ui ? xs0[ui] - p.gap / 2 : x0;
        }
        var maxStep = isNum(run.steps) ? run.steps - 1 : (rs.length ? rs[rs.length - 1].step : 1);
        return x0 + availT * Math.max(0, Math.min(1, step / Math.max(1, maxStep)));
      }
      // the open stretches' leaves, a row each under the thread, after a row saying what the stretch was
      var openUnits = p.units.filter(function (u) { return u.type === "cluster" && st.open[skey(side, u.id)]; });
      var rows = 0;
      openUnits.forEach(function (u) { var l = leavesOf(run, u.c); u.leaves = l.leaves; u.more = l.more; u.row0 = rows; rows += 1 + u.leaves.length + (u.more ? 1 : 0); });
      var rowsTop = yT + 38;
      var H = rows ? rowsTop + rows * ROW + 4 : yT + 34;
      var hot = cs.filter(function (c) { return c.kind === "hot"; }).length, ticks = ticksOf(run), big = maxAbs(run);
      var svg = d3.select(host).append("svg").attr("class", "rl-band").attr("data-side", side)
        .attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
        .attr("aria-label", name(report, side) + " as a reward thread: " + cs.length + " stretches, " + hot + " hot; " + rs.length + " rewarded steps, return " + signed(run["return"]) + ", " + ticks.length + " at or above the 90th percentile; length is reward impact");
      function measure(sel) { var n = sel.node(); var w = n && n.getComputedTextLength ? n.getComputedTextLength() : 0; return w > 0 ? w : String(sel.text() || "").length * CH; }
      function rowY(r) { return rowsTop + r * ROW; }
      // ---- the run node and its thread
      var rg = svg.append("g").attr("class", "rl-node").attr("data-kind", "run").attr("data-side", side).attr("transform", "translate(6," + yT + ")").style("cursor", "default");
      rg.append("circle").attr("r", 5.5).attr("fill", color(side));
      var oc = report[side] && report[side].outcome, ok = oc && typeof oc.success === "boolean" ? oc.success : null;
      var rlab = rg.append("text").attr("class", "rl-tlabel").attr("x", 10).attr("y", 4).attr("font-weight", 650).attr("fill", color(side)).text(fit(runLabel(side), narrow ? 110 : 260));
      if (ok !== null) rlab.append("tspan").attr("fill", ok ? "var(--good)" : "var(--bad)").text(ok ? " ✓" : " ✗");
      rg.append("title").text(run.narrative || "");
      rg.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: name(report, side) + " · return " + signed(run["return"]) + " · discounted " + signed(run.discounted_return) }, { text: (run.positive || 0) + " gains · " + (run.negative || 0) + " losses · " + (run.zero || 0) + " zero" }, run.narrative ? { text: run.narrative } : null]); }).on("pointerleave", tip.hide);
      svg.append("path").attr("class", "rl-thread").attr("data-side", side).attr("d", "M" + x0 + "," + yT + "H" + xEnd)
        .attr("fill", "none").attr("stroke", color(side)).attr("stroke-width", 1.5).attr("stroke-opacity", 0.6);
      // ---- on the thread: the folds and the stretches
      p.units.forEach(function (u, ui) {
        var ux = xs0[ui], w = p.widths[ui];
        if (u.type === "fold") {
          var fg = svg.append("g").attr("class", "rl-fold").attr("data-ids", u.id).attr("data-side", side).attr("data-steps", u.steps).attr("transform", "translate(" + ux + "," + yT + ")");
          fg.append("line").attr("x1", 0).attr("x2", w).attr("y1", 0).attr("y2", 0).attr("stroke", "var(--bg)").attr("stroke-width", 3);
          fg.append("line").attr("class", "rl-fold-line").attr("x1", 0).attr("x2", w).attr("y1", 0).attr("y2", 0)
            .attr("stroke", "var(--ink-3)").attr("stroke-width", 1.5).attr("stroke-dasharray", "1.5 2.5").attr("stroke-linecap", "round");
          var sumR = u.clusters.reduce(function (t, c) { return t + rewardSum(run, c); }, 0);
          var flab = "×" + u.steps + " · " + signed(sumR);
          if (w >= 18) fg.append("text").attr("x", w / 2).attr("y", -4).attr("text-anchor", "middle").text(flab.length * CH <= w + 8 ? flab : "×" + u.steps);
          fg.append("rect").attr("x", 0).attr("y", -8).attr("width", w).attr("height", 16).attr("fill", "transparent");
          var fcredit = null;
          u.clusters.forEach(function (c) { var v = creditOf(run, c); if (isNum(v)) fcredit = (fcredit || 0) + v; });
          if (isNum(fcredit) && creditMax > 0) {
            svg.append("rect").attr("class", "rl-credit").attr("data-id", u.id).attr("data-fold", "1").attr("data-side", side).attr("data-credit", fcredit.toFixed(4))
              .attr("x", ux).attr("y", yT + 17).attr("width", Math.max(w, 1)).attr("height", Math.max(1.5, 10 * Math.abs(fcredit) / creditMax)).attr("fill", fcredit < 0 ? "var(--bad)" : "var(--good)").attr("fill-opacity", 0.35)
              .append("title").text(name(report, side) + " · " + u.clusters.length + " quiet stretches folded · credit " + signed(fcredit));
          }
          fg.append("title").text(u.steps + " steps · " + secs(u.seconds) + " · reward " + signed(sumR) + " — " + u.clusters.length + " quiet stretches folded; click to open");
          fg.on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: name(report, side) + " · ×" + u.steps + " steps folded" }, { text: u.steps + " steps · " + secs(u.seconds) + " · reward " + signed(sumR) + (u.lanes.length ? " · " + u.lanes.join(", ") : "") }, { text: "steps " + u.from + "–" + u.to + " · " + u.clusters.length + " quiet stretches · click to open" }]);
          }).on("pointerleave", tip.hide).on("click", function () { st.unfolded[skey(side, u.id)] = true; repaint(); });
          return;
        }
        var c = u.c, imp = clamp01(c.impact), quiet = c.kind === "quiet", hotK = c.kind === "hot", open = !!st.open[skey(side, u.id)];
        var sum = rewardSum(run, c), credit = creditOf(run, c), n = rewardsIn(run, c).filter(function (r) { return r.reward !== 0; }).length;
        var cg = svg.append("g").attr("class", "rl-cluster" + (open ? " open" : "")).attr("data-id", c.id).attr("data-kind", c.kind || "work").attr("data-side", side).attr("transform", "translate(" + ux + "," + yT + ")");
        var bar = cg.append("path").attr("data-id", c.id).attr("d", "M0,0H" + w.toFixed(2)).attr("fill", "none")
          .attr("stroke", quiet ? "var(--ink-3)" : color(side)).attr("stroke-width", quiet ? 2 : 1.5 + 3.5 * imp).attr("stroke-opacity", quiet ? 0.7 : 0.45 + 0.55 * imp);
        if (u.fold) bar.attr("data-fold", u.fold);
        cg.append("rect").attr("x", 0).attr("y", -8).attr("width", Math.max(w, 6)).attr("height", 16).attr("fill", "transparent");
        cg.append("title").text(name(report, side) + " · " + (c.label || c.kind) + " · steps " + c.from + "–" + c.to + " · " + secs(c.seconds) + " · reward " + signed(sum) + (isNum(credit) ? " · credit " + signed(credit) : "") + " · impact " + imp.toFixed(2) + (c.why ? " — " + c.why : ""));
        cg.on("pointermove", function (evt) {
          tip.show(evt, [{ b: true, text: name(report, side) + (c.lane ? " · " + c.lane : "") + " · " + (c.label || c.kind) },
            { text: "steps " + c.from + "–" + c.to + " (" + stepsIn(c) + ") · " + secs(c.seconds) + " · " + (c.kind || "work") + " · impact " + imp.toFixed(2) },
            { text: "reward " + signed(sum) + " over " + n + " rewarded step" + (n === 1 ? "" : "s") + (isNum(credit) ? " · credit " + signed(credit) : "") },
            c.why ? { text: c.why } : null, { text: open ? "click to close" : n ? "click to open its rewards" : "no rewarded steps" }]);
        }).on("pointerleave", tip.hide).on("click", function () { var k = skey(side, u.id); if (st.open[k]) delete st.open[k]; else st.open[k] = true; repaint(); });
        // the credit its steps carry: a faint bar under the thread, tall by |credit|, coloured by sign
        if (isNum(credit) && creditMax > 0) {
          var ch = Math.max(1.5, 10 * Math.abs(credit) / creditMax);
          svg.append("rect").attr("class", "rl-credit").attr("data-id", c.id).attr("data-side", side).attr("data-credit", credit.toFixed(4))
            .attr("x", ux).attr("y", yT + 17).attr("width", Math.max(w, 1)).attr("height", ch).attr("fill", credit < 0 ? "var(--bad)" : "var(--good)").attr("fill-opacity", 0.35)
            .append("title").text(name(report, side) + " · " + (c.label || c.kind) + " · credit " + signed(credit) + " (" + ((run.credit && run.credit.source) || "credit") + ")");
        }
      });
      // ---- the ticks: every step whose |reward| reaches the run's 90th percentile, tall by |reward| — where the
      // thread is dilated; a closed fold keeps its steps to its label and its tooltip
      function folded(step) { return p.units.some(function (u) { return u.type === "fold" && step >= u.from && step <= u.to; }); }
      ticks.forEach(function (r) {
        if (folded(r.step)) return;
        var tx = stepX(r.step), pos = r.reward > 0, h = big > 0 ? Math.max(4, 14 * Math.abs(r.reward) / big) : 4;
        var tg = svg.append("g").attr("class", "rl-tick").attr("data-step", r.step).attr("data-sign", pos ? "pos" : "neg").attr("data-side", side);
        tg.append("line").attr("x1", tx).attr("x2", tx).attr("y1", yT).attr("y2", pos ? yT - h : yT + h).attr("stroke", pos ? "var(--good)" : "var(--bad)").attr("stroke-width", 1.8).attr("stroke-linecap", "round");
        tg.append("rect").attr("x", tx - 3).attr("y", yT - 15).attr("width", 6).attr("height", 30).attr("fill", "transparent");
        tg.append("title").text(leafText(r) + " — click to open the step");
        tg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, leafTip(report, side, r)); }).on("pointerleave", tip.hide)
          .on("click", function (evt) { evt.stopPropagation(); select(report, side, r.step); });
      });
      // the decisive step's ring and the answer's square, on the thread
      markSteps(run, "decisive").forEach(function (m) {
        svg.append("circle").attr("class", "rl-decisive").attr("data-step", m.step).attr("cx", stepX(m.step)).attr("cy", yT).attr("r", 5).attr("fill", "none").attr("stroke", "var(--bad)").attr("stroke-width", 2)
          .append("title").text("decisive step " + m.step + (m.label ? " · " + m.label : ""));
      });
      var ans = answerStep(run);
      if (isNum(ans)) {
        svg.append("rect").attr("class", "rl-answer").attr("data-step", ans).attr("x", stepX(ans) - 3.5).attr("y", yT - 3.5).attr("width", 7).attr("height", 7).attr("fill", color(side))
          .append("title").text("the answer at step " + ans + " · return " + signed(cumAt(rs, ans)));
      }
      // ---- the open stretches: a row saying what the stretch was, then its rewarded steps as leaves
      openUnits.forEach(function (u) {
        var ui = unitOf[u.id], ux = xs0[ui], c = u.c;
        var lx = Math.max(x0, Math.min(ux, cap));
        var y0 = rowY(u.row0), y1 = rowY(u.row0 + u.leaves.length);
        var mid = (yT + 6 + y0 - 8) / 2;
        svg.append("path").attr("class", "rl-link").attr("data-id", c.id).attr("fill", "none")
          .attr("d", "M" + ux + "," + (yT + 5) + "C" + ux + "," + mid + " " + lx + "," + mid + " " + lx + "," + (y0 - 8))
          .attr("stroke", color(side)).attr("stroke-width", 1.1).attr("stroke-opacity", 0.4);
        if (u.leaves.length) svg.append("line").attr("class", "rl-stem").attr("x1", lx).attr("x2", lx).attr("y1", y0 + 6).attr("y2", y1).attr("stroke", color(side)).attr("stroke-width", 1.1).attr("stroke-opacity", 0.4);
        var sum = rewardSum(run, c), credit = creditOf(run, c);
        var why = svg.append("text").attr("class", "rl-why").attr("data-id", c.id).attr("x", lx - 4).attr("y", y0 + 4)
          .text(fit((c.label ? c.label + " · " : "") + "steps " + c.from + "–" + c.to + " · " + secs(c.seconds) + " · reward " + signed(sum) + (isNum(credit) ? " · credit " + signed(credit) : "") + (c.why ? " — " + c.why : ""), W - padR - lx + 4));
        u.leaves.forEach(function (r, li) {
          var ly = rowY(u.row0 + 1 + li), pos = r.reward > 0, neg = r.reward < 0;
          var lg = svg.append("g").attr("class", "rl-leaf").attr("data-step", r.step).attr("data-side", side).attr("data-id", c.id).attr("transform", "translate(" + lx + "," + ly + ")");
          lg.append("text").attr("class", "g").attr("x", 0).attr("y", 4).attr("text-anchor", "middle").attr("fill", pos ? "var(--good)" : neg ? "var(--bad)" : "var(--ink-3)").text(pos ? "▲" : neg ? "▼" : "·");
          var lab = lg.append("text").attr("class", "rl-tlabel").attr("x", 12).attr("y", 4).text(fit(leafText(r), W - padR - lx - 12));
          var lw = measure(lab);
          lg.insert("rect", "text").attr("x", -8).attr("y", -ROW / 2).attr("width", lw + 24).attr("height", ROW).attr("fill", "var(--bg)").attr("fill-opacity", 0.9);
          lg.append("title").text(leafText(r) + " — click to open the step");
          lg.on("pointermove", function (evt) { evt.stopPropagation(); tip.show(evt, leafTip(report, side, r)); }).on("pointerleave", tip.hide)
            .on("click", function (evt) { evt.stopPropagation(); select(report, side, r.step); });
        });
        if (u.more) {
          svg.append("text").attr("class", "rl-more").attr("data-id", c.id).attr("data-more", u.more).attr("x", lx + 12).attr("y", rowY(u.row0 + 1 + u.leaves.length) + 4).attr("fill", "var(--ink-3)")
            .text("… and " + u.more + " smaller reward" + (u.more === 1 ? "" : "s") + " — the " + LEAF_CAP + " largest are shown; every step is in the table");
        }
        why.raise();
      });
    });
  }

  function draw(host, report, rl, st, tip, ctx) {
    if (!d3) return;
    var W = Math.max(300, host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 640);
    var sides = ["a", "b"].filter(function (s) { return measurable(rl[s]); });
    function repaint() { host.innerHTML = ""; draw(host, report, rl, st, tip, ctx); }
    drawCurve(host, report, rl, tip, W, sides);
    drawBands(host, report, rl, st, tip, ctx, W, sides, repaint);
  }

  // ------------------------------------------------------------ the block
  function prefLine(H, report, rl) {
    var pref = rl.preference;
    if (!pref || !pref.chosen) return null;
    function sideOf(who) {
      if (!who) return null;
      if (who.side === "a" || who.side === "b") return who.side;
      return who.agent === name(report, "a") ? "a" : who.agent === name(report, "b") ? "b" : null;
    }
    var cs = sideOf(pref.chosen), rsd = sideOf(pref.rejected);
    var kids = [document.createTextNode("preferred: "),
      H("b", { "data-side": cs || "", style: { color: cs ? color(cs) : "inherit" }, text: pref.chosen.agent || (cs ? name(report, cs) : "?") }),
      document.createTextNode(" (chosen)")];
    if (pref.rejected) {
      kids.push(document.createTextNode(" over "));
      kids.push(H("b", { "data-side": rsd || "", style: { color: rsd ? color(rsd) : "inherit" }, text: pref.rejected.agent || (rsd ? name(report, rsd) : "?") }));
      kids.push(document.createTextNode(" (rejected)"));
    }
    var basis = pref.chosen.basis || pref.basis || "";
    var tail = (basis ? " — basis: " + basis : "") + (isNum(pref.margin) ? " · margin " + signed(pref.margin) : "");
    if (tail) kids.push(H("span", { class: "basis", text: tail }));
    return H("p", { class: "rl-pref", "data-chosen": cs || "" }, kids);
  }

  AgentDiff.block({
    id: "rl",
    title: "Reward & credit",
    question: "Where did each run earn and lose reward, who gets the credit, and which trajectory is preferred?",
    group: "outcome",
    size: "wide",
    relevance: function (ctx) { var rl = ctx.report && ctx.report.rl; return rl && rl.measurable ? 0.85 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, rl = report && report.rl;
      if (!rl || !rl.measurable) return ctx.empty(el, "No rewards were measured for this pair, so reward and credit cannot be drawn.");
      var root = H("div", { class: "rl" });
      el.appendChild(root);
      var tip = tooltip(root);
      var task = report.task && report.task.id;
      var st = stateFor(task);
      if (rl.narrative) root.appendChild(H("p", { class: "rl-narr", text: rl.narrative }));
      var host = H("div", { class: "rl-chart" });
      function redraw() { host.innerHTML = ""; draw(host, report, rl, st, tip, ctx); }
      // the chips: the source of the rewards, one line of numbers per run, the two actions
      var bar = H("div", { class: "rl-bar" });
      var shaped = rl.source === "shaped";
      bar.appendChild(H("span", { class: "rl-chip rl-src", "data-source": rl.source || "recorded",
        title: (shaped ? "no reward was recorded in the trace: each reward is shaped from the reading — the errors, retries, milestones and the answer — and labelled" : "the rewards are the ones recorded in the trace") + (isNum(rl.gamma) ? " · discounted at γ = " + rl.gamma : ""),
        text: shaped ? "rewards: shaped from the reading (labelled)" : "rewards: recorded" }));
      ["a", "b"].forEach(function (s) {
        var run = rl[s];
        if (!run) return;
        var chip = H("span", { class: "rl-chip rl-run", "data-side": s, title: run.narrative || "" }, [
          H("b", { style: { color: color(s) }, text: name(report, s) }),
          document.createTextNode(" · return " + signed(run["return"]) + " · discounted " + signed(run.discounted_return) + " · "),
          H("span", { class: "up", text: (run.positive || 0) + "↑" }), document.createTextNode(" "),
          H("span", { class: "dn", text: (run.negative || 0) + "↓" }),
        ]);
        bar.appendChild(chip);
      });
      bar.appendChild(H("button", { text: "expand hot", "data-act": "expand-hot", title: "open every hot stretch to its rewards", onclick: function () {
        ["a", "b"].forEach(function (s) { clustersOf(rl[s]).forEach(function (c) { if (c.kind === "hot") st.open[skey(s, c.id)] = true; }); });
        redraw();
      } }));
      bar.appendChild(H("button", { text: "collapse all", "data-act": "collapse-all", title: "close every open stretch and refold the quiet ones", onclick: function () { st.open = {}; st.unfolded = {}; redraw(); } }));
      root.appendChild(bar);
      root.appendChild(H("p", { class: "rl-legend", text: "thread length ∝ reward impact · ▲▼ a step at or above the run's 90th percentile of |reward|, tall by |reward| · faint bar = the credit a stretch's steps carry" + (rl.a && rl.a.credit && rl.a.credit.source ? " (" + rl.a.credit.source + ")" : "") + " · ×N = quiet steps folded, click to open · click a stretch for its rewards, a leaf for the step · ◎ decisive ■ answer" }));
      root.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { draw(host, report, rl, st, tip, ctx); }, "rl:" + task) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) draw(host, report, rl, st, tip, ctx);
      var pref = prefLine(H, report, rl);
      if (pref) root.appendChild(pref);
      // the tables: the largest rewards, then every stretch — both runs, no hovering
      var details = H("details", { class: "rl-details" }, [H("summary", { text: "the largest rewards and every stretch, both runs" })]);
      details.appendChild(H("h5", { text: "largest rewards" }));
      var t = H("table", { class: "rl-table" });
      t.appendChild(H("tr", null, ["run", "step", "name", "reward", "why", "credit"].map(function (h) { return H("th", { text: h }); })));
      ["a", "b"].forEach(function (side) {
        var run = rl[side]; if (!run) return;
        var byStep = {};
        rewardsOf(run).forEach(function (r) { byStep[r.step] = r; });
        (Array.isArray(run.largest) ? run.largest : []).forEach(function (l) {
          if (!l || !isNum(l.step)) return;
          var r = byStep[l.step] || {}, v = isNum(l.reward) ? l.reward : r.reward;
          t.appendChild(H("tr", { "data-side": side, "data-step": l.step }, [
            H("td", { class: "n", text: name(report, side) }),
            H("td", { class: "n", text: String(l.step) }),
            H("td", { class: "n", text: r.name || l.name || "" }),
            H("td", { class: "n " + (v > 0 ? "pos" : v < 0 ? "neg" : ""), text: signed(v) }),
            H("td", { text: l.why || labelsOf(r) || "" }),
            H("td", { class: "n", text: isNum(r.credit) ? signed(r.credit) : "—" }),
          ]));
        });
      });
      details.appendChild(H("div", { class: "scroll-x" }, [t]));
      details.appendChild(H("h5", { text: "every stretch" }));
      var t2 = H("table", { class: "rl-table rl-clusters" });
      t2.appendChild(H("tr", null, ["run", "steps", "s", "kind", "impact", "reward", "credit", "why"].map(function (h) { return H("th", { text: h }); })));
      ["a", "b"].forEach(function (side) {
        var run = rl[side]; if (!run) return;
        clustersOf(run).forEach(function (c) {
          var sum = rewardSum(run, c), credit = creditOf(run, c);
          t2.appendChild(H("tr", { "data-side": side, "data-id": c.id }, [
            H("td", { class: "n", text: name(report, side) }),
            H("td", { class: "n", text: c.from + "–" + c.to }),
            H("td", { class: "n", text: secs(c.seconds) }),
            H("td", { class: "n " + (c.kind || ""), text: c.kind || "work" }),
            H("td", { class: "n", text: clamp01(c.impact).toFixed(2) }),
            H("td", { class: "n " + (sum > 0 ? "pos" : sum < 0 ? "neg" : ""), text: signed(sum) }),
            H("td", { class: "n", text: isNum(credit) ? signed(credit) : "—" }),
            H("td", { text: (c.label ? c.label + " — " : "") + (c.why || "") }),
          ]));
        });
      });
      details.appendChild(H("div", { class: "scroll-x" }, [t2]));
      if (rl.a && rl.a.credit && rl.a.credit.source) details.appendChild(H("p", { class: "rl-none", text: "credit: " + rl.a.credit.source + (rl.a.credit.metric ? " on " + rl.a.credit.metric : "") + " — a step's share of the run's outcome; a stretch's credit is the sum over its steps." }));
      root.appendChild(details);
    },
  });
})(typeof window !== "undefined" ? window : this);
