/* AgentDiff block — Agent loop.
 *
 * What the loop did on its own, and why: the success of each agent
 * across iterations with its interval, the prompt experiments marked
 * where they happened (kept, reverted, dropped), every decision with
 * the sentence that justifies it, and the reason the loop stopped.
 * From `aggregate.loop` (the ledger the loop writes); the controller is
 * a set of rules over these numbers, so each decision can be checked
 * against the line above it.
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
      ".lp-lede{font-size:var(--fs-s);color:var(--ink-2);margin:0 0 8px;max-width:78ch}",
      ".lp-chart{width:100%;min-height:170px}",
      ".lp-chart svg{display:block;width:100%;height:auto;font-family:var(--mono)}",
      ".lp-chart .axis text{font-size:10px;fill:var(--ink-3)}.lp-chart .axis line,.lp-chart .axis path{stroke:var(--rule)}",
      ".lp-chart .lp-line{fill:none;stroke-width:1.6}",
      ".lp-chart .lp-ci{stroke-width:1.2;opacity:.7}",
      ".lp-chart .lp-tag{font-size:10px;fill:var(--ink-2)}",
      ".lp-chart .lp-tag.kept{fill:var(--good)}.lp-chart .lp-tag.reverted{fill:var(--warn)}",
      ".lp-chart .lp-legend{font-size:10px;fill:var(--ink-2)}",
      ".lp-steps{list-style:none;margin:10px 0 0;padding:0;font-size:var(--fs-s)}",
      ".lp-steps li{padding:6px 0;border-top:1px solid var(--rule);display:grid;grid-template-columns:5.5em 1fr;gap:8px}",
      ".lp-steps .n{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3)}",
      ".lp-steps .n b{display:block;color:var(--ink)}",
      ".lp-steps .why{color:var(--ink-2)}",
      ".lp-steps .dec{margin-top:3px}",
      ".lp-steps .dec b{font-family:var(--mono);font-weight:500;font-size:var(--fs-xs);padding:1px 6px;border-radius:9px;background:var(--rule);margin-right:6px}",
      ".lp-steps .dec b.kept{background:var(--good);color:#fff}.lp-steps .dec b.reverted{background:var(--warn);color:#fff}",
      ".lp-steps .txt{font-size:var(--fs-xs);color:var(--ink-3);margin-top:2px;font-style:italic}",
      ".lp-stop{margin-top:10px;font-size:var(--fs-s)}.lp-stop b{font-family:var(--mono);font-weight:500}",
      ".lp-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:72ch}",
    ].join("");
    document.head.appendChild(node);
  }
  var COLORS = ["#2f6f9f", "#b5651d", "#3f7d3f", "#7c3aed"];

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  function drawChart(host, loop) {
    if (!d3) return;
    var st = loop.state || {};
    var iters = st.iterations || [];
    var agents = st.agents || [];
    var W = Math.max(320, host.clientWidth || 640), H = 170;
    var m = { top: 22, right: 12, bottom: 24, left: 34 };
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("preserveAspectRatio", "none");
    var x = d3.scalePoint().domain(iters.map(function (it) { return it.n; })).range([m.left, W - m.right]).padding(0.5);
    var y = d3.scaleLinear().domain([0, 1]).range([H - m.bottom, m.top]);
    svg.append("g").attr("class", "axis").attr("transform", "translate(0," + (H - m.bottom) + ")")
      .call(d3.axisBottom(x).tickFormat(function (n) { var it = iters[n - 1]; return "it " + n + (it && it.action === "test-prompt" ? " ⚗" : ""); }));
    svg.append("g").attr("class", "axis").attr("transform", "translate(" + m.left + ",0)")
      .call(d3.axisLeft(y).ticks(4).tickFormat(d3.format(".0%")));
    agents.forEach(function (agent, ai) {
      var color = COLORS[ai % COLORS.length];
      var pts = [];
      iters.forEach(function (it) {
        var r = it.results && it.results[agent];
        if (it.action === "test-prompt" && it.decision && it.decision.agent === agent) {
          // the experiment: the baseline point and the variant point
          var v = it.results && it.results[it.decision.variant];
          if (v && isNum(v.success)) pts.push({ n: it.n, rate: v.success, ci: v.ci95, variant: true, status: it.decision.status });
        }
        if (r && isNum(r.success)) pts.push({ n: it.n, rate: r.success, ci: r.ci95, variant: false });
      });
      var base = pts.filter(function (p) { return !p.variant; });
      svg.append("path").attr("class", "lp-line").attr("stroke", color)
        .attr("d", d3.line().x(function (p) { return x(p.n); }).y(function (p) { return y(p.rate); })(base));
      var g = svg.append("g").attr("class", "lp-agent").attr("data-agent", agent);
      pts.forEach(function (p) {
        var cx = x(p.n) + (p.variant ? 7 : 0);
        if (p.ci && isNum(p.ci[0]) && isNum(p.ci[1])) {
          g.append("line").attr("class", "lp-ci").attr("stroke", color).attr("x1", cx).attr("x2", cx).attr("y1", y(p.ci[0])).attr("y2", y(p.ci[1]));
        }
        g.append("circle").attr("class", "lp-pt" + (p.variant ? " variant" : "")).attr("cx", cx).attr("cy", y(p.rate)).attr("r", p.variant ? 4 : 3.5)
          .attr("fill", p.variant ? "#fff" : color).attr("stroke", color).attr("stroke-width", 1.4)
          .append("title").text(agent + (p.variant ? " with the change" : "") + ": " + Math.round(p.rate * 100) + "%" + (p.ci ? " [" + p.ci[0].toFixed(2) + "–" + p.ci[1].toFixed(2) + "]" : ""));
        if (p.variant) {
          g.append("text").attr("class", "lp-tag " + (p.status && p.status.indexOf("kept") === 0 ? "kept" : "reverted")).attr("x", x(p.n)).attr("y", m.top - 8).attr("text-anchor", "middle")
            .text(p.status && p.status.indexOf("kept") === 0 ? "kept" : "reverted");
        }
      });
      svg.append("text").attr("class", "lp-legend").attr("x", m.left + 4 + ai * 110).attr("y", 12).attr("fill", color).text("● " + agent);
    });
  }

  AgentDiff.block({
    id: "loop",
    title: "Agent loop",
    question: "What did the loop do on its own, what did it decide, and why did it stop?",
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
      var st = loop.state, sm = loop.summary || {}, cfg = loop.config || {};
      var iters = st.iterations || [];
      el.appendChild(H("p", { class: "lp-lede", text:
        iters.length + " iteration(s), " + (sm.spent_runs || 0) + " run(s)" + (cfg.max_runs ? " of " + cfg.max_runs : "") +
        " over " + (cfg.tasks || (st.tasks || []).length) + " task(s) · " + (sm.kept_changes || 0) + " prompt change(s) kept, " +
        (sm.reverted_changes || 0) + " reverted, " + (sm.dropped_changes || 0) + " dropped · " +
        Object.keys(sm.agents || {}).map(function (a) { var r = sm.agents[a]; return r.runs ? a + " " + Math.round(r.success * 100) + "% [" + r.ci95[0].toFixed(2) + "–" + r.ci95[1].toFixed(2) + "] over " + r.runs + " run(s), prompt v" + r.prompt_version : a + ": no pooled runs"; }).join(" · ") }));
      var host = H("div", { class: "lp-chart" });
      el.appendChild(AgentDiff.charts && AgentDiff.charts.responsive ? AgentDiff.charts.responsive(host, function () { drawChart(host, loop); }) : host);
      if (!(AgentDiff.charts && AgentDiff.charts.responsive)) drawChart(host, loop);
      var list = H("ol", { class: "lp-steps" });
      iters.forEach(function (it) {
        var li = H("li", { "data-iteration": it.n, "data-action": it.action });
        li.appendChild(H("span", { class: "n", text: it.action }, [H("b", { text: "iteration " + it.n })]));
        var body = H("div");
        body.appendChild(H("div", { class: "why", text: it.why }));
        if (it.action === "compare") {
          var parts = Object.keys(it.results || {}).map(function (a) { var r = it.results[a]; return r.runs ? a + " " + r.successes + "/" + r.runs : null; }).filter(Boolean);
          var fams = (it.routing && it.routing.families) || {};
          var clear = Object.keys(fams).filter(function (f) { return fams[f].confidence === "clear"; }).length;
          parts.push(clear + " of " + Object.keys(fams).length + " famil" + (Object.keys(fams).length === 1 ? "y" : "ies") + " clear");
          if (it.suggestions_added) parts.push(it.suggestions_added + " hypothesis(es) queued from the reading");
          body.appendChild(H("div", { class: "dec", text: parts.join(" · ") }));
        } else if (it.decision) {
          var d = it.decision;
          var tag = d.status.indexOf("kept") === 0 ? "kept" : d.status;
          body.appendChild(H("div", { class: "dec" }, [H("b", { class: tag, text: d.status }), H("span", { text: d.why })]));
          body.appendChild(H("div", { class: "txt", text: "“" + d.text + "”" }));
        }
        li.appendChild(body);
        list.appendChild(li);
      });
      var dropped = [];
      Object.keys(st.prompts || {}).forEach(function (a) {
        (st.prompts[a].history || []).forEach(function (h) { if (h.status === "dropped") dropped.push(a + ": " + h.kind.replace(/_/g, " ") + " — " + h.why); });
      });
      el.appendChild(list);
      if (dropped.length) el.appendChild(H("p", { class: "lp-note", text: "Dropped without a run: " + dropped.join("; ") }));
      var stop = st.stop || {};
      el.appendChild(H("p", { class: "lp-stop" }, [H("b", { text: "stopped · " }), H("span", { text: stop.reason || "not stopped" })]));
      if (loop.note) el.appendChild(H("p", { class: "lp-note", text: loop.note }));
    },
  });
})(typeof window !== "undefined" ? window : this);
