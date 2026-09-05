/* AgentDiff block — The two runs over time (the story's hero).
 *
 * A super panel: the numbers that matter first (outcome, decisive step,
 * steps, tool calls, tokens, latency, cost, similarity, confidence — A
 * beside B with a paired bar), then the body chart: each run a trunk
 * along wall-clock time, its thinking on the trunk, its tool calls as
 * branches with leaves, the alignment between the two in the gutter, the
 * fault's path in red, the decisive step ringed; zoomable. Click a node
 * and the inspector below opens it. Every number is the report's.
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
      ".bd-panel{display:grid;grid-template-columns:1fr;gap:10px 18px;margin-bottom:8px}",
      "@media (min-width:900px){.bd-panel{grid-template-columns:minmax(0,1fr) 240px}}",
      ".bd-lede{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:baseline;font-size:var(--fs-m);color:var(--ink-2)}",
      ".bd-lede b{font-family:var(--display,var(--sans));font-size:var(--fs-l);color:var(--ink);font-weight:650}",
      ".bd-lede .tag{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3)}",
      ".bd-stats{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:var(--fs-xs);align-content:start}",
      ".bd-stats .k{color:var(--ink-3);text-transform:uppercase;letter-spacing:.06em;font-size:var(--fs-xs);padding-top:3px;white-space:nowrap}",
      ".bd-stats .v{display:grid;grid-template-columns:1fr auto;gap:2px 8px;align-items:center;font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink-2)}",
      ".bd-stats .bars{height:10px;display:grid;grid-template-rows:4px 4px;gap:2px}",
      ".bd-stats .bars i{display:block;height:4px;border-radius:2px}",
      ".bd-stats .bars i.a{background:var(--a)}.bd-stats .bars i.b{background:var(--b)}",
      ".bd-stats .nums{white-space:nowrap}.bd-stats .nums .a{color:var(--a)}.bd-stats .nums .b{color:var(--b)}",
      ".bd-stats .one{grid-column:1 / -1;font-family:var(--sans);color:var(--ink-2)}",
      ".bd-stats .one b{font-weight:600;color:var(--ink)}",
      ".bd-outcome{font-family:var(--mono);font-size:var(--fs-s)}",
      ".bd-outcome .ok{color:var(--good);font-weight:700}.bd-outcome .no{color:var(--bad);font-weight:700}",
      "body[data-view=\"story\"] .hero-lane .bd-panel{margin-top:2px}",
      ".bd-inspector{margin-top:10px}",
      "body[data-view=\"story\"] .hero-lane .bd-inspector{border:0;border-top:1px solid var(--rule);border-radius:0;background:transparent;padding-top:6px}",
    ].join("");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function stepsOf(report, side) { var b = report && report[side]; return b && Array.isArray(b.steps) ? b.steps : []; }
  function name(report, side) { var b = report && report[side]; return (b && b.agent && b.agent.name) || side.toUpperCase(); }

  function stat(ctx, label, a, b, fmt) {
    var H = ctx.h;
    if (!isNum(a) && !isNum(b)) return null;
    var max = Math.max(isNum(a) ? a : 0, isNum(b) ? b : 0, 1e-9);
    var frag = document.createDocumentFragment();
    frag.appendChild(H("span", { class: "k", text: label }));
    frag.appendChild(H("span", { class: "v" }, [
      H("span", { class: "bars" }, [
        H("i", { class: "a", style: { width: Math.round(100 * (isNum(a) ? a : 0) / max) + "%" } }),
        H("i", { class: "b", style: { width: Math.round(100 * (isNum(b) ? b : 0) / max) + "%" } }),
      ]),
      H("span", { class: "nums" }, [H("span", { class: "a", text: isNum(a) ? fmt(a) : "—" }), H("span", { text: " / " }), H("span", { class: "b", text: isNum(b) ? fmt(b) : "—" })]),
    ]));
    return frag;
  }

  AgentDiff.block({
    id: "trace-body",
    title: "The two runs over time",
    question: "Where did each run spend its time, what did it call, and where did they part?",
    group: "trajectory",
    size: "wide",

    relevance: function (ctx) {
      var r = ctx.report;
      if (!r || (!stepsOf(r, "a").length && !stepsOf(r, "b").length)) return 0;
      if (!AgentDiff.charts || !AgentDiff.charts.available()) return 0;
      return 0.97;
    },

    render: function (el, ctx) {
      ensureStyle();
      var r = ctx.report, H = ctx.h, F = ctx.fmt;
      if (!r) return ctx.empty(el, "No report loaded.");
      var md = r.metrics_delta || {};
      var diag = r.diagnosis || {};
      var dec = diag.decisive_step || {};
      var sem = r.semantic || {};
      var oa = (r.a && r.a.outcome) || {}, ob = (r.b && r.b.outcome) || {};
      var simRows = Array.isArray(sem.rows) ? sem.rows.filter(function (x) { return isNum(x.semantic); }) : [];
      var meanSim = simRows.length ? simRows.reduce(function (s, x) { return s + x.semantic; }, 0) / simRows.length : null;
      var divs = Array.isArray(r.divergences) ? r.divergences : [];
      var firstDiv = divs.length ? divs[0] : null;
      var conf = diag.confidence || {};

      var panel = H("div", { class: "bd-panel" });
      var lede = H("div", { class: "bd-lede" });
      lede.appendChild(H("b", { text: name(r, "a") + " vs " + name(r, "b") }));
      lede.appendChild(H("span", { class: "bd-outcome" }, [
        H("span", { class: oa.success ? "ok" : "no", text: name(r, "a") + (oa.success ? " ✓" : " ✗") }),
        H("span", { text: "  " }),
        H("span", { class: ob.success ? "ok" : "no", text: name(r, "b") + (ob.success ? " ✓" : " ✗") }),
      ]));
      if (isNum(dec.step) && (diag.subject === "a" || diag.subject === "b")) {
        lede.appendChild(H("span", { class: "tag", text: "decisive: " + name(r, diag.subject) + " step " + dec.step + " (" + (dec.verification || "hypothesized") + ")" }));
      }
      if (firstDiv) {
        // the divergence names the failed run's step; find the row that holds it
        var fs = firstDiv.downstream && (firstDiv.downstream.failed_agent === "a" || firstDiv.downstream.failed_agent === "b") ? firstDiv.downstream.failed_agent : (diag.subject || "b");
        var idx = firstDiv[fs + "_index"];
        var row = (r.alignment || []).findIndex(function (x) { return x[fs + "_index"] === idx; });
        lede.appendChild(H("span", { class: "tag", text: "first divergence: " + (row >= 0 ? "row " + row : name(r, fs) + " step " + idx) + " · " + String(firstDiv.kind || "").replace(/_/g, " ") }));
      }
      var chartHost = H("div", { class: "bd-chart" });
      var left = H("div", null, [lede, chartHost]);
      var stats = H("div", { class: "bd-stats", role: "list", "aria-label": "A versus B" });
      [
        stat(ctx, "steps", md.steps && md.steps.a, md.steps && md.steps.b, function (v) { return String(v); }),
        stat(ctx, "tool calls", md.tool_calls && md.tool_calls.a, md.tool_calls && md.tool_calls.b, function (v) { return String(v); }),
        stat(ctx, "tokens", md.tokens && md.tokens.a, md.tokens && md.tokens.b, function (v) { return F.int(v); }),
        stat(ctx, "latency", md.latency_s && md.latency_s.a, md.latency_s && md.latency_s.b, function (v) { return F.sec(v); }),
        stat(ctx, "cost", md.cost_usd && md.cost_usd.a, md.cost_usd && md.cost_usd.b, function (v) { return "$" + v.toFixed(4); }),
      ].forEach(function (f) { if (f) stats.appendChild(f); });
      if (isNum(meanSim)) stats.appendChild(H("span", { class: "one" }, [H("b", { text: "semantic similarity " + meanSim.toFixed(2) }), H("span", { text: " mean over " + simRows.length + " aligned rows" + (isNum(sem.first_semantic_break) ? " · first break at row " + sem.first_semantic_break : "") })]));
      if (conf.level) stats.appendChild(H("span", { class: "one" }, [H("b", { text: "confidence " + conf.level }), H("span", { text: isNum(conf.n) ? " · n=" + conf.n : "" })]));
      if (r.tradeoff && r.tradeoff.statement) stats.appendChild(H("span", { class: "one", text: String(r.tradeoff.statement) }));
      panel.appendChild(left);
      panel.appendChild(stats);
      el.appendChild(panel);
      var drawn = null;
      try { drawn = AgentDiff.charts.body(chartHost, ctx); }
      catch (err) { console.warn("AgentDiff trace-body: chart failed", err); }
      if (!drawn) chartHost.remove();

      // the inspector, docked under the chart: the step under the cursor,
      // both sides, updated on every click; folded on a narrow screen
      var detail = typeof AgentDiff.blockEntry === "function" ? AgentDiff.blockEntry("step-detail") : null;
      if (detail && typeof detail.render === "function") {
        var dock = H("div", { class: "tj-inspector block bd-inspector", "data-block": "step-detail" });
        dock.appendChild(H("div", { class: "tj-inspector-title", text: "Step under the cursor" }));
        var body = H("div", { class: "block-body" });
        dock.appendChild(body);
        try { detail.render(body, ctx); } catch (err) { console.warn("AgentDiff trace-body: inspector failed", err); }
        var narrow = false;
        try { narrow = (global.innerWidth || 1024) <= 700; } catch (err) { narrow = false; }
        if (narrow) {
          var fold = H("details", { class: "tj-inspector-fold" }, [H("summary", { text: "Step under the cursor" })]);
          fold.appendChild(dock);
          el.appendChild(fold);
        } else {
          el.appendChild(dock);
        }
      }
    },
  });

})(typeof window !== "undefined" ? window : this);
