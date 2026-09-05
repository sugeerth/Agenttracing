/* AgentDiff block — Agent loop.
 *
 * What the loop did on its own, as statistics and charts: a KPI row
 * (each agent's pooled success with its Wilson interval, the paired
 * difference with its interval and sign test, runs spent against the
 * budget, decisions, the stop), success by iteration with interval
 * bands and the experiments marked, each prompt experiment task by
 * task as a dumbbell (without → with the change) over two pooled
 * intervals, a grid of where the runs went (interval width per family
 * per iteration), a table view of every number, and the ledger of
 * decisions with the sentence behind each. From `aggregate.loop`.
 * Every number is a count or an interval over the runs listed.
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
      ".lp{--lp-a:var(--a);--lp-b:var(--b);--lp-good:var(--good);--lp-warn:var(--warn);position:relative}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .lp{--lp-a:#3987e5;--lp-b:#d95926}}",
      ":root[data-theme=dark] .lp{--lp-a:#3987e5;--lp-b:#d95926}",
      ".lp-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:0 0 12px}",
      ".lp-tile{border:1px solid var(--rule);border-radius:8px;padding:8px 10px;background:var(--surface);min-width:0}",
      ".lp-tile .k{font-size:var(--fs-xs);color:var(--ink-3);display:flex;align-items:center;gap:6px}",
      ".lp-tile .k i{width:10px;height:3px;border-radius:2px;display:inline-block}",
      ".lp-tile .v{font-family:var(--sans);font-weight:600;font-size:26px;line-height:1.15;color:var(--ink);margin:3px 0 2px}",
      ".lp-tile .v small{font-size:var(--fs-s);font-weight:500;color:var(--ink-2);margin-left:4px}",
      ".lp-tile .s{font-size:var(--fs-xs);color:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".lp-meter{height:6px;border-radius:3px;background:var(--surface-2);margin-top:6px;overflow:hidden}",
      ".lp-meter b{display:block;height:100%;background:var(--accent);border-radius:3px}",
      ".lp-tag{font-family:var(--mono);font-weight:500;font-size:var(--fs-xs);padding:1px 7px;border-radius:9px;background:var(--surface-2);color:var(--ink-2);white-space:nowrap}",
      ".lp-tag.kept{background:var(--lp-good);color:#fff}.lp-tag.reverted{background:var(--lp-warn);color:#fff}.lp-tag.dropped{border:1px solid var(--rule)}",
      ".lp-sec{margin:14px 0 0}",
      ".lp-sec h4{margin:0 0 2px;font-size:var(--fs-m);font-weight:600;color:var(--ink)}",
      ".lp-sec .sub{margin:0 0 6px;font-size:var(--fs-xs);color:var(--ink-3);max-width:80ch}",
      ".lp-legend{display:flex;gap:14px;font-size:var(--fs-xs);color:var(--ink-2);margin:0 0 4px;flex-wrap:wrap}",
      ".lp-legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:5px;border-radius:1px}",
      ".lp-legend i.hollow{height:8px;width:8px;border-radius:50%;background:var(--surface)!important;border:2px solid var(--ink-2)}",
      ".lp-chart{width:100%}.lp-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".lp-chart .grid line{stroke:var(--rule);stroke-width:1}.lp-chart .axis text{font-size:10.5px;fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".lp-chart .axis path,.lp-chart .axis line{stroke:var(--rule)}",
      ".lp-chart .band{opacity:.12}.lp-chart .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}",
      ".lp-chart .pt{stroke:var(--surface);stroke-width:2}.lp-chart .pt.variant{fill:var(--surface);stroke-width:2}",
      ".lp-chart .whisk{stroke-width:1.5}",
      ".lp-chart .exp{fill:var(--surface-2);opacity:.7}",
      ".lp-chart .lab{font-size:10.5px;fill:var(--ink-2)}.lp-chart .lab.strong{fill:var(--ink);font-weight:600}",
      ".lp-chart .hit{fill:transparent;cursor:crosshair}.lp-chart .xhair{stroke:var(--ink-3);stroke-width:1;pointer-events:none}",
      ".lp-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);min-width:150px}",
      ".lp-tip b{color:var(--ink);font-variant-numeric:tabular-nums}.lp-tip .row{display:flex;gap:8px;align-items:center;margin-top:2px}.lp-tip .row i{display:inline-block;width:12px;height:2px}",
      ".lp-exp{border:1px solid var(--rule);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--surface)}",
      ".lp-exp .head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:4px}",
      ".lp-exp .head .t{font-weight:600;color:var(--ink);font-size:var(--fs-s)}",
      ".lp-exp .stat{font-size:var(--fs-xs);color:var(--ink-2);font-variant-numeric:tabular-nums}",
      ".lp-exp .stat b{color:var(--ink)}",
      ".lp-exp .txt{font-size:var(--fs-xs);color:var(--ink-3);margin-top:6px;font-style:italic;max-width:90ch}",
      ".lp-exp .why{font-size:var(--fs-xs);color:var(--ink-3);margin-top:4px;max-width:90ch}",
      ".lp-grid{border-collapse:separate;border-spacing:2px;font-size:var(--fs-xs);font-variant-numeric:tabular-nums}",
      ".lp-grid th{font-weight:500;color:var(--ink-3);text-align:left;padding:2px 6px;font-family:var(--mono);font-size:var(--fs-xs)}",
      ".lp-grid th.fam{max-width:16ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".lp-grid td{width:74px;height:26px;border-radius:4px;text-align:center;padding:0 4px;white-space:nowrap;cursor:default}",
      ".lp-grid td.none{background:transparent;color:var(--ink-3)}",
      ".lp-table{margin-top:10px;font-size:var(--fs-xs)}.lp-table summary{cursor:pointer;color:var(--ink-2)}",
      ".lp-table table{border-collapse:collapse;margin-top:6px;font-variant-numeric:tabular-nums}",
      ".lp-table th,.lp-table td{text-align:left;padding:3px 10px 3px 0;border-top:1px solid var(--rule);color:var(--ink-2)}",
      ".lp-table th{font-weight:500;color:var(--ink-3);border-top:0}",
      ".lp-steps{list-style:none;margin:8px 0 0;padding:0;font-size:var(--fs-s)}",
      ".lp-steps li{padding:7px 0;border-top:1px solid var(--rule);display:grid;grid-template-columns:6.5em 1fr;gap:10px;align-items:start}",
      ".lp-steps .n{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3)}.lp-steps .n b{display:block;color:var(--ink)}",
      ".lp-steps .stats{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:3px}",
      ".lp-steps .chip{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2);background:var(--surface-2);border-radius:9px;padding:1px 7px;font-variant-numeric:tabular-nums}",
      ".lp-steps .chip i{display:inline-block;width:8px;height:8px;border-radius:50%;vertical-align:-1px;margin-right:4px}",
      ".lp-steps .why{color:var(--ink-3);font-size:var(--fs-xs)}",
      ".lp-steps .txt{font-size:var(--fs-xs);color:var(--ink-3);margin-top:2px;font-style:italic}",
      ".lp-stop{margin-top:10px;font-size:var(--fs-s)}.lp-stop b{font-family:var(--mono);font-weight:500}",
      ".lp-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:78ch}",
    ].join("");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function pct(v) { return isNum(v) ? Math.round(v * 100) + "%" : "—"; }
  function ci(c) { return c && isNum(c[0]) && isNum(c[1]) ? c[0].toFixed(2) + "–" + c[1].toFixed(2) : "—"; }
  function signed(v) { return isNum(v) ? (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2) : "—"; }
  function pfmt(p) { return isNum(p) ? (p < 0.001 ? "<0.001" : p.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")) : "—"; }
  function cssVar(el, name, fallback) {
    try { var v = getComputedStyle(el).getPropertyValue(name).trim(); return v || fallback; } catch (e) { return fallback; }
  }
  function agentColor(agents, a) { return a === agents[0] ? "var(--lp-a)" : "var(--lp-b)"; }

  /* one tooltip per block, positioned inside it */
  function tooltip(root) {
    var tip = root.querySelector(".lp-tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "lp-tip"; tip.hidden = true; root.appendChild(tip); }
    return {
      show: function (evt, build) {
        tip.innerHTML = ""; build(tip); tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 190 > r.width) x = Math.max(0, evt.clientX - r.left - 200);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }
  function tipRow(tip, color, label, value) {
    var row = document.createElement("div"); row.className = "row";
    if (color) { var i = document.createElement("i"); i.style.background = color; row.appendChild(i); }
    var b = document.createElement("b"); b.textContent = value; row.appendChild(b);
    var s = document.createElement("span"); s.textContent = label; row.appendChild(s);
    tip.appendChild(row);
  }

  // ------------------------------------------------------------ KPI row
  function kpis(H, loop) {
    var st = loop.state, sm = loop.summary || {}, cfg = loop.config || {};
    var agents = st.agents || [];
    var row = H("div", { class: "lp-kpi" });
    agents.forEach(function (a) {
      var r = (sm.agents || {})[a] || {};
      var tile = H("div", { class: "lp-tile", "data-agent": a });
      tile.appendChild(H("div", { class: "k" }, [H("i", { style: { background: agentColor(agents, a) } }), H("span", { text: a + " · pooled success" })]));
      tile.appendChild(H("div", { class: "v" }, [H("span", { class: "num", text: r.runs ? pct(r.success) : "—" })]));
      var last = lastCompare(st);
      var res = last && last.results && last.results[a];
      tile.appendChild(H("div", { class: "s", text: r.runs ? (res ? res.successes + "/" + res.runs : r.runs + " runs") + " · 95% Wilson " + ci(r.ci95) + " · prompt v" + r.prompt_version : "no pooled runs" }));
      row.appendChild(tile);
    });
    var pr = sm.paired || {};
    var t = H("div", { class: "lp-tile", "data-kpi": "paired" });
    t.appendChild(H("div", { class: "k", text: "paired difference · " + (pr.labels ? pr.labels[0] + " − " + pr.labels[1] : "—") }));
    t.appendChild(H("div", { class: "v", text: signed(pr.diff) }));
    t.appendChild(H("div", { class: "s", text: pr.diff === undefined || pr.diff === null ? "no comparison yet" :
      "95% " + (pr.ci95 ? signed(pr.ci95[0]) + " to " + signed(pr.ci95[1]) : "—") + " · sign test p " + pfmt(pr.sign_test_p) + " · " + (pr.n_pairs || 0) + " paired task(s)" }));
    row.appendChild(t);
    t = H("div", { class: "lp-tile", "data-kpi": "runs" });
    t.appendChild(H("div", { class: "k", text: "runs spent" }));
    t.appendChild(H("div", { class: "v" }, [H("span", { class: "num", text: String(sm.spent_runs || 0) }), H("small", { text: cfg.max_runs ? "of " + cfg.max_runs : "no run budget" })]));
    t.appendChild(H("div", { class: "s", text: (sm.iterations || 0) + " of " + (cfg.max_iterations || "—") + " iterations · " + (cfg.tasks || (st.tasks || []).length) + " tasks · " + cfg.runs + " runs per batch" }));
    if (cfg.max_runs) t.appendChild(H("div", { class: "lp-meter" }, [H("b", { style: { width: Math.min(100, Math.round(100 * (sm.spent_runs || 0) / cfg.max_runs)) + "%" } })]));
    row.appendChild(t);
    t = H("div", { class: "lp-tile", "data-kpi": "decisions" });
    t.appendChild(H("div", { class: "k", text: "prompt hypotheses" }));
    var total = (sm.kept_changes || 0) + (sm.reverted_changes || 0) + (sm.dropped_changes || 0);
    t.appendChild(H("div", { class: "v" }, [H("span", { class: "num", text: String(total) }), H("small", { text: "tested or dropped" })]));
    t.appendChild(H("div", { class: "s" }, [
      H("span", { class: "lp-tag kept", text: (sm.kept_changes || 0) + " kept" }), H("span", { text: " " }),
      H("span", { class: "lp-tag reverted", text: (sm.reverted_changes || 0) + " reverted" }), H("span", { text: " " }),
      H("span", { class: "lp-tag dropped", text: (sm.dropped_changes || 0) + " dropped" })]));
    row.appendChild(t);
    var rt = sm.routing || {};
    t = H("div", { class: "lp-tile", "data-kpi": "routing" });
    t.appendChild(H("div", { class: "k", text: "routing picks clear" }));
    t.appendChild(H("div", { class: "v" }, [H("span", { class: "num", text: rt.families ? rt.clear + "/" + rt.families : "—" }), H("small", { text: "families" })]));
    t.appendChild(H("div", { class: "s", text: (st.stop ? "stopped: " + st.stop.kind : "running") + (rt.overall_pick ? " · overall pick " + rt.overall_pick : "") }));
    row.appendChild(t);
    return row;
  }
  function lastCompare(st) {
    var its = st.iterations || [];
    for (var i = its.length - 1; i >= 0; i--) if (its[i].action === "compare") return its[i];
    return null;
  }

  // ------------------------------------------- chart 1: success by iteration
  function drawSuccess(host, loop, tip) {
    if (!d3) return;
    var st = loop.state, iters = st.iterations || [], agents = st.agents || [];
    var W = Math.max(360, host.clientWidth || 680), H = 210;
    var m = { top: 26, right: 96, bottom: 26, left: 40 };
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H);
    var x = d3.scalePoint().domain(iters.map(function (it) { return it.n; })).range([m.left, W - m.right]).padding(0.5);
    var y = d3.scaleLinear().domain([0, 1]).range([H - m.bottom, m.top]);
    var step = iters.length > 1 ? x(iters[1].n) - x(iters[0].n) : (W - m.left - m.right);
    // experiment columns
    iters.forEach(function (it) {
      if (it.action !== "test-prompt") return;
      svg.append("rect").attr("class", "exp").attr("x", x(it.n) - step / 2 + 2).attr("y", m.top - 2).attr("width", step - 4).attr("height", H - m.bottom - m.top + 2).attr("rx", 4);
    });
    svg.append("g").attr("class", "grid").selectAll("line").data([0, 0.25, 0.5, 0.75, 1]).enter().append("line")
      .attr("x1", m.left).attr("x2", W - m.right).attr("y1", y).attr("y2", y);
    svg.append("g").attr("class", "axis").attr("transform", "translate(0," + (H - m.bottom) + ")")
      .call(d3.axisBottom(x).tickSize(0).tickFormat(function (n) { var it = iters[n - 1]; return (it && it.action === "test-prompt" ? "⚗ " : "") + "it " + n; })).select(".domain").remove();
    svg.append("g").attr("class", "axis").attr("transform", "translate(" + m.left + ",0)")
      .call(d3.axisLeft(y).tickValues([0, 0.5, 1]).tickSize(0).tickFormat(d3.format(".0%"))).select(".domain").remove();
    var series = {};
    agents.forEach(function (agent) {
      var color = agentColor(agents, agent);
      var pts = [], variants = [];
      iters.forEach(function (it) {
        var r = it.results && it.results[agent];
        if (r && isNum(r.success)) pts.push({ n: it.n, rate: r.success, ci: r.ci95, s: r.successes, runs: r.runs, it: it });
        if (it.action === "test-prompt" && it.decision && it.decision.agent === agent) {
          var v = it.results && it.results[it.decision.variant];
          if (v && isNum(v.success)) variants.push({ n: it.n, rate: v.success, ci: v.ci95, s: v.successes, runs: v.runs, status: it.decision.status });
        }
      });
      series[agent] = { pts: pts, variants: variants };
      var band = pts.filter(function (p) { return p.ci; });
      svg.append("path").attr("class", "band").attr("fill", color)
        .attr("d", d3.area().x(function (p) { return x(p.n); }).y0(function (p) { return y(p.ci[0]); }).y1(function (p) { return y(p.ci[1]); }).curve(d3.curveMonotoneX)(band));
      svg.append("path").attr("class", "line").attr("stroke", color)
        .attr("d", d3.line().x(function (p) { return x(p.n); }).y(function (p) { return y(p.rate); }).curve(d3.curveMonotoneX)(pts));
      var g = svg.append("g").attr("data-agent", agent);
      pts.forEach(function (p) {
        g.append("circle").attr("class", "pt").attr("cx", x(p.n)).attr("cy", y(p.rate)).attr("r", 4.5).attr("fill", color);
      });
      variants.forEach(function (v) {
        var cx = x(v.n) + 9;
        if (v.ci) g.append("line").attr("class", "whisk").attr("stroke", color).attr("x1", cx).attr("x2", cx).attr("y1", y(v.ci[0])).attr("y2", y(v.ci[1]));
        g.append("circle").attr("class", "pt variant").attr("cx", cx).attr("cy", y(v.rate)).attr("r", 4.5).attr("stroke", color);
        var kept = v.status && v.status.indexOf("kept") === 0;
        svg.append("text").attr("class", "lab strong").attr("x", x(v.n)).attr("y", m.top - 9).attr("text-anchor", "middle").text(kept ? "kept" : v.status);
      });
      if (pts.length) {
        var last = pts[pts.length - 1];
        svg.append("text").attr("class", "lab strong").attr("x", W - m.right + 10).attr("y", y(last.rate) + 4).text(agent + " " + pct(last.rate));
      }
    });
    // converging end-labels: separate them
    var labs = svg.selectAll("text.lab.strong").filter(function () { return +this.getAttribute("x") > W - m.right; }).nodes()
      .sort(function (a, b) { return +a.getAttribute("y") - +b.getAttribute("y"); });
    for (var i = 1; i < labs.length; i++) {
      var prev = +labs[i - 1].getAttribute("y"), cur = +labs[i].getAttribute("y");
      if (cur - prev < 12) labs[i].setAttribute("y", prev + 12);
    }
    // crosshair + tooltip, one readout for every series at that iteration
    var xhair = svg.append("line").attr("class", "xhair").attr("y1", m.top).attr("y2", H - m.bottom).style("display", "none");
    iters.forEach(function (it) {
      svg.append("rect").attr("class", "hit").attr("x", x(it.n) - step / 2).attr("y", 0).attr("width", step).attr("height", H)
        .on("pointermove", function (evt) {
          xhair.style("display", null).attr("x1", x(it.n)).attr("x2", x(it.n));
          tip.show(evt, function (el) {
            var h = document.createElement("div"); h.textContent = "iteration " + it.n + " · " + it.action + (it.runs ? " · " + it.runs + " run(s) × " + (it.tasks || []).length + " task(s)" : ""); el.appendChild(h);
            agents.forEach(function (agent) {
              var p = series[agent].pts.filter(function (q) { return q.n === it.n; })[0];
              if (p) tipRow(el, agentColor(agents, agent), agent + " · " + p.s + "/" + p.runs + " · 95% " + ci(p.ci), pct(p.rate));
              series[agent].variants.filter(function (q) { return q.n === it.n; }).forEach(function (v) {
                tipRow(el, agentColor(agents, agent), agent + " with the change · " + v.s + "/" + v.runs + " · 95% " + ci(v.ci), pct(v.rate));
              });
            });
            if (it.paired && isNum(it.paired.diff)) tipRow(el, null, "paired Δ · 95% " + (it.paired.ci95 ? signed(it.paired.ci95[0]) + " to " + signed(it.paired.ci95[1]) : "—") + " · p " + pfmt(it.paired.sign_test_p), signed(it.paired.diff));
          });
        })
        .on("pointerleave", function () { xhair.style("display", "none"); tip.hide(); });
    });
  }

  // ---------------------------------------- chart 2: an experiment, per task
  function drawExperiment(host, it, agents, tip) {
    if (!d3) return;
    var d = it.decision, ev = d.evidence || {}, rows = ev.per_task || [];
    var color = agentColor(agents, d.agent);
    var W = Math.max(360, host.clientWidth || 680);
    var rowH = 22, m = { top: 18, right: 70, bottom: 8, left: 120 };
    var H = m.top + rows.length * rowH + 14 + 2 * 18 + m.bottom;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H);
    var x = d3.scaleLinear().domain([0, 1]).range([m.left, W - m.right]);
    svg.append("g").attr("class", "grid").selectAll("line").data([0, 0.5, 1]).enter().append("line").attr("x1", x).attr("x2", x).attr("y1", m.top - 6).attr("y2", H - m.bottom);
    svg.append("g").attr("class", "axis").attr("transform", "translate(0," + (m.top - 8) + ")")
      .call(d3.axisTop(x).tickValues([0, 0.5, 1]).tickSize(0).tickFormat(d3.format(".0%"))).select(".domain").remove();
    rows.forEach(function (r, i) {
      var cy = m.top + i * rowH + rowH / 2;
      var g = svg.append("g").attr("class", "task").attr("data-task", r.task).attr("data-outcome", r.outcome);
      g.append("text").attr("class", "lab").attr("x", m.left - 10).attr("y", cy + 4).attr("text-anchor", "end").text(r.task.length > 16 ? r.task.slice(0, 15) + "…" : r.task);
      if (isNum(r.baseline_rate) && isNum(r.variant_rate)) {
        g.append("line").attr("class", "line").attr("stroke", color).attr("x1", x(r.baseline_rate)).attr("x2", x(r.variant_rate)).attr("y1", cy).attr("y2", cy);
        g.append("circle").attr("class", "pt base").attr("cx", x(r.baseline_rate)).attr("cy", cy).attr("r", 4.5).attr("fill", color).attr("opacity", 0.45);
        g.append("circle").attr("class", "pt with").attr("cx", x(r.variant_rate)).attr("cy", cy).attr("r", 4.5).attr("fill", color);
      }
      g.append("text").attr("class", "lab").attr("x", W - m.right + 10).attr("y", cy + 4).text(r.outcome + (r.outcome === "win" ? " ▲" : r.outcome === "loss" ? " ▼" : ""));
      g.append("rect").attr("class", "hit").attr("x", 0).attr("y", cy - rowH / 2).attr("width", W).attr("height", rowH)
        .on("pointermove", function (evt) {
          tip.show(evt, function (el) {
            var h = document.createElement("div"); h.textContent = r.task; el.appendChild(h);
            tipRow(el, color, "without the change · " + r.baseline[0] + "/" + r.baseline[1], pct(r.baseline_rate));
            tipRow(el, color, "with the change · " + r.variant[0] + "/" + r.variant[1], pct(r.variant_rate));
          });
        }).on("pointerleave", tip.hide);
    });
    // the two pooled intervals
    var y0 = m.top + rows.length * rowH + 14;
    [["without", ev.baseline, 0.45], ["with the change", ev.variant, 1]].forEach(function (spec, i) {
      var f = spec[1] || {}, cy = y0 + i * 18 + 6;
      var g = svg.append("g").attr("class", "pooled").attr("data-side", i ? "variant" : "baseline");
      g.append("text").attr("class", "lab").attr("x", m.left - 10).attr("y", cy + 4).attr("text-anchor", "end").text(spec[0]);
      if (f.ci95) g.append("rect").attr("x", x(f.ci95[0])).attr("y", cy - 4).attr("width", Math.max(2, x(f.ci95[1]) - x(f.ci95[0]))).attr("height", 8).attr("rx", 4).attr("fill", color).attr("opacity", spec[2] * 0.5);
      if (isNum(f.rate)) g.append("circle").attr("class", "pt").attr("cx", x(f.rate)).attr("cy", cy).attr("r", 4.5).attr("fill", color).attr("opacity", spec[2]);
      g.append("text").attr("class", "lab").attr("x", W - m.right + 10).attr("y", cy + 4).text(f.successes + "/" + f.runs + " · " + ci(f.ci95));
    });
  }

  function experimentCard(H, it, agents, tip) {
    var d = it.decision, ev = d.evidence || {}, pr = ev.paired || {};
    var card = H("div", { class: "lp-exp", "data-iteration": it.n });
    var kept = d.status.indexOf("kept") === 0;
    card.appendChild(H("div", { class: "head" }, [
      H("span", { class: "t", text: "iteration " + it.n + " · " + d.agent + " · " + String(d.kind || "").replace(/_/g, " ") }),
      H("span", { class: "lp-tag " + (kept ? "kept" : "reverted"), text: d.status }),
      H("span", { class: "stat" }, [H("span", { text: "Δ with − without " }), H("b", { text: signed(pr.diff) }),
        H("span", { text: pr.ci95 ? " (95% " + signed(pr.ci95[0]) + " to " + signed(pr.ci95[1]) + ")" : "" }),
        H("span", { text: " · wins " }), H("b", { text: String(ev.wins) }), H("span", { text: " losses " }), H("b", { text: String(ev.losses) }),
        H("span", { text: " ties " }), H("b", { text: String(ev.ties) }),
        H("span", { text: " · sign test p " }), H("b", { text: isNum(ev.sign_test_p) ? pfmt(ev.sign_test_p) : "no discordant task" }),
        H("span", { text: " · " + (ev.variant ? ev.variant.runs : "?") + " runs a side" })]),
    ]));
    var host = H("div", { class: "lp-chart" });
    card.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { drawExperiment(host, it, agents, tip); }) : host);
    if (!(AgentDiff.charts && AgentDiff.charts.responsive)) drawExperiment(host, it, agents, tip);
    card.appendChild(H("div", { class: "txt", text: "“" + d.text + "”" }));
    card.appendChild(H("div", { class: "why", text: d.why + (d.relabel ? " " + d.relabel + "." : "") }));
    return card;
  }

  // ------------------------------------- chart 3: where the runs went (grid)
  function uncertaintyGrid(H, root, loop, tip) {
    var st = loop.state, iters = (st.iterations || []).filter(function (it) { return it.action === "compare"; });
    var fams = {};
    iters.forEach(function (it) { Object.keys((it.routing || {}).families || {}).forEach(function (f) { fams[f] = true; }); });
    var names = Object.keys(fams);
    if (!names.length || !iters.length) return null;
    var lastIt = iters[iters.length - 1];
    names.sort(function (a, b) {
      var ra = (lastIt.routing.families[a] || {}), rb = (lastIt.routing.families[b] || {});
      return (rb.width || 0) - (ra.width || 0) || a.localeCompare(b);
    });
    var MAX = 24, shown = names.slice(0, MAX);
    var lo = cssVar(root, "--surface-2", "#f4f4f2"), hi = cssVar(root, "--accent", "#2f6f9f"), ink = cssVar(root, "--ink", "#1a1a18");
    var ramp = d3 ? d3.interpolateLab(lo, hi) : function (t) { return t > 0.5 ? hi : lo; };
    var table = H("table", { class: "lp-grid" });
    var head = H("tr", null, [H("th", { text: "family" })]);
    iters.forEach(function (it) { head.appendChild(H("th", { text: "it " + it.n + " · +" + (it.runs * (it.tasks || []).length * (it.agents || []).length) + " runs" })); });
    table.appendChild(head);
    var GLYPH = { clear: "✓ clear", tie: "= tie", overlapping: "~ overlap", insufficient: "· few runs" };
    shown.forEach(function (f) {
      var tr = H("tr", { "data-family": f });
      tr.appendChild(H("th", { class: "fam", title: f, text: f }));
      iters.forEach(function (it) {
        var row = (it.routing.families || {})[f];
        if (!row) { tr.appendChild(H("td", { class: "none", text: "—" })); return; }
        var w = isNum(row.width) ? row.width : 1;
        var state = row.tie ? "tie" : row.confidence;
        var fill = ramp(Math.min(1, w));
        var td = H("td", { "data-state": state, "data-width": String(w), style: { background: fill, color: w > 0.55 ? "#fff" : ink }, text: GLYPH[state] || state });
        td.addEventListener("pointermove", function (evt) {
          tip.show(evt, function (el) {
            var h = document.createElement("div"); h.textContent = f + " · iteration " + it.n + " · " + (row.tie ? "tie — equal rates, more runs cannot separate them" : row.why); el.appendChild(h);
            (row.candidates || []).forEach(function (c) {
              var ft = c.features || {};
              tipRow(el, agentColor(st.agents, c.agent), c.agent + " · " + (ft.successes !== undefined ? ft.successes + "/" : "n ") + ft.n + " · 95% " + ci(ft.ci95), pct(ft.rate));
            });
            tipRow(el, null, "widest interval", isNum(row.width) ? row.width.toFixed(2) : "—");
          });
        });
        td.addEventListener("pointerleave", tip.hide);
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    var wrap = H("div", { class: "scroll-x" }, [table]);
    if (names.length > MAX) wrap.appendChild(H("p", { class: "lp-note", text: "… " + (names.length - MAX) + " more famil" + (names.length - MAX === 1 ? "y" : "ies") + " with narrower intervals; every one is in the table view." }));
    return wrap;
  }

  // ------------------------------------------------------------ table view
  function tableView(H, loop) {
    var st = loop.state, iters = st.iterations || [];
    var det = H("details", { class: "lp-table" });
    det.appendChild(H("summary", { text: "Table view — every number behind the charts" }));
    var t = H("table");
    t.appendChild(H("tr", null, ["iteration", "action", "agent", "successes", "runs", "rate", "95% Wilson", "output equality"].map(function (h) { return H("th", { text: h }); })));
    iters.forEach(function (it) {
      Object.keys(it.results || {}).forEach(function (a) {
        var r = it.results[a];
        t.appendChild(H("tr", null, [String(it.n), it.action, a, String(r.successes), String(r.runs), pct(r.success), ci(r.ci95), isNum(r.equality_rate) ? pct(r.equality_rate) : "—"].map(function (v) { return H("td", { text: v }); })));
      });
    });
    det.appendChild(H("div", { class: "scroll-x" }, [t]));
    var t2 = H("table");
    t2.appendChild(H("tr", null, ["iteration", "family", "confidence", "pick", "widest interval", "candidates"].map(function (h) { return H("th", { text: h }); })));
    iters.forEach(function (it) {
      if (it.action !== "compare") return;
      Object.keys((it.routing || {}).families || {}).forEach(function (f) {
        var row = it.routing.families[f];
        t2.appendChild(H("tr", null, [String(it.n), f, row.tie ? "tie" : row.confidence, row.pick || "—", isNum(row.width) ? row.width.toFixed(2) : "—",
          (row.candidates || []).map(function (c) { return c.agent + " " + pct(c.features.rate) + " [" + ci(c.features.ci95) + "] n=" + c.features.n; }).join("; ")].map(function (v) { return H("td", { text: v }); })));
      });
    });
    det.appendChild(H("div", { class: "scroll-x" }, [t2]));
    return det;
  }

  // ------------------------------------------------------------ the ledger
  function ledger(H, loop) {
    var st = loop.state, iters = st.iterations || [], agents = st.agents || [];
    var list = H("ol", { class: "lp-steps" });
    iters.forEach(function (it) {
      var li = H("li", { "data-iteration": it.n, "data-action": it.action });
      li.appendChild(H("span", { class: "n", text: it.action }, [H("b", { text: "iteration " + it.n })]));
      var body = H("div");
      var stats = H("div", { class: "stats" });
      Object.keys(it.results || {}).forEach(function (a) {
        var r = it.results[a];
        if (!r.runs) return;
        var isVariant = agents.indexOf(a) < 0;
        stats.appendChild(H("span", { class: "chip" }, [H("i", { style: { background: agentColor(agents, isVariant ? (it.decision && it.decision.agent) : a), opacity: isVariant ? 0.45 : 1 } }),
          H("span", { text: a + " " + r.successes + "/" + r.runs + " [" + ci(r.ci95) + "]" })]));
      });
      if (it.action === "compare") {
        var fams = (it.routing && it.routing.families) || {}, keys = Object.keys(fams);
        var clear = keys.filter(function (f) { return fams[f].confidence === "clear"; }).length;
        var ties = keys.filter(function (f) { return fams[f].tie; }).length;
        stats.appendChild(H("span", { class: "chip", text: clear + "/" + keys.length + " clear" + (ties ? " · " + ties + " tie" + (ties === 1 ? "" : "s") : "") }));
        if (it.paired && isNum(it.paired.diff)) stats.appendChild(H("span", { class: "chip", text: "Δ " + signed(it.paired.diff) + (it.paired.ci95 ? " [" + signed(it.paired.ci95[0]) + ", " + signed(it.paired.ci95[1]) + "]" : "") + " p " + pfmt(it.paired.sign_test_p) }));
        if (it.suggestions_added) stats.appendChild(H("span", { class: "chip", text: it.suggestions_added + " hypothesis" + (it.suggestions_added === 1 ? "" : "es") + " queued" }));
      } else if (it.decision) {
        var d = it.decision, ev = d.evidence || {};
        stats.appendChild(H("span", { class: "lp-tag " + (d.status.indexOf("kept") === 0 ? "kept" : "reverted"), text: d.status }));
        stats.appendChild(H("span", { class: "chip", text: "wins " + ev.wins + " · losses " + ev.losses + " · ties " + ev.ties + " · p " + (isNum(ev.sign_test_p) ? pfmt(ev.sign_test_p) : "—") }));
      }
      body.appendChild(stats);
      body.appendChild(H("div", { class: "why", text: it.why }));
      if (it.decision) body.appendChild(H("div", { class: "txt", text: "“" + it.decision.text + "”" }));
      li.appendChild(body);
      list.appendChild(li);
    });
    return list;
  }

  AgentDiff.block({
    id: "loop",
    title: "Agent loop",
    question: "What did the loop do on its own, what do the numbers say, and why did it stop?",
    group: "other",
    size: "wide",

    relevance: function (ctx) {
      var loop = ctx.aggregate && ctx.aggregate.loop;
      return loop && loop.state && (loop.state.iterations || []).length ? 0.86 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h;
      var loop = ctx.aggregate && ctx.aggregate.loop;
      if (!loop || !loop.state) return ctx.empty(el, "This page was not produced by the loop.");
      var st = loop.state, iters = st.iterations || [], agents = st.agents || [];
      var root = H("div", { class: "lp" });
      el.appendChild(root);
      var tip = tooltip(root);
      root.appendChild(kpis(H, loop));

      var sec = H("section", { class: "lp-sec", "data-sec": "success" });
      sec.appendChild(H("h4", { text: "Success by iteration" }));
      sec.appendChild(H("p", { class: "sub", text: "Pooled success per agent after each iteration (line), its 95% Wilson interval (band); in an experiment (⚗) the hollow point is the agent with the change, whisker its interval. Hover a column for every number." }));
      var legend = H("div", { class: "lp-legend" });
      agents.forEach(function (a) { legend.appendChild(H("span", null, [H("i", { style: { background: agentColor(agents, a) } }), H("span", { text: a })])); });
      legend.appendChild(H("span", null, [H("i", { class: "hollow" }), H("span", { text: "with the change under test" })]));
      sec.appendChild(legend);
      var host = H("div", { class: "lp-chart", "data-chart": "success" });
      sec.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { drawSuccess(host, loop, tip); }) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) drawSuccess(host, loop, tip);
      root.appendChild(sec);

      var experiments = iters.filter(function (it) { return it.action === "test-prompt" && it.decision; });
      if (experiments.length) {
        sec = H("section", { class: "lp-sec", "data-sec": "experiments" });
        sec.appendChild(H("h4", { text: "Each experiment, task by task" }));
        sec.appendChild(H("p", { class: "sub", text: "The agent without the change (light) → with it (dark), success rate per task; below, the two pooled rates with their 95% Wilson intervals. Kept only when wins exceed losses with no always-pass → always-fail regression; the sign test is exact over the discordant tasks." }));
        experiments.forEach(function (it) { sec.appendChild(experimentCard(H, it, agents, tip)); });
        root.appendChild(sec);
      }

      var grid = uncertaintyGrid(H, root, loop, tip);
      if (grid) {
        sec = H("section", { class: "lp-sec", "data-sec": "uncertainty" });
        sec.appendChild(H("h4", { text: "Where the runs went" }));
        sec.appendChild(H("p", { class: "sub", text: "Per task family and comparison: the widest 95% interval among the candidates (darker = wider = less certain) and the routing state. The loop spends runs on the widest first; a tie is equal rates no run can separate." }));
        sec.appendChild(grid);
        root.appendChild(sec);
      }

      root.appendChild(tableView(H, loop));

      sec = H("section", { class: "lp-sec", "data-sec": "ledger" });
      sec.appendChild(H("h4", { text: "The ledger" }));
      sec.appendChild(ledger(H, loop));
      var dropped = [];
      Object.keys(st.prompts || {}).forEach(function (a) {
        (st.prompts[a].history || []).forEach(function (h) { if (h.status === "dropped") dropped.push(a + ": " + h.kind.replace(/_/g, " ") + " — " + h.why); });
      });
      if (dropped.length) sec.appendChild(H("p", { class: "lp-note", text: "Dropped without a run: " + dropped.join("; ") }));
      var stop = st.stop || {};
      sec.appendChild(H("p", { class: "lp-stop" }, [H("b", { text: "stopped · " }), H("span", { text: stop.reason || "not stopped" })]));
      if (loop.note) sec.appendChild(H("p", { class: "lp-note", text: loop.note }));
      root.appendChild(sec);
    },
  });
})(typeof window !== "undefined" ? window : this);
