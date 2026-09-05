/* AgentDiff block — Running now.
 *
 * The runs still being written for the selected task, as they arrive:
 * one line per agent with a mark per step (the newest pulsing), tokens
 * and latency so far, and the last three steps in full. No analysis is
 * run on a run in progress — a diagnosis on half a trace would flip
 * around as it grows — so this block shows, and the story appears when
 * the pair finishes. Reads `State.data.live.runs`.
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
      ".lv-run{padding:8px 0;border-top:1px solid var(--rule)}",
      ".lv-run:first-child{border-top:0}",
      ".lv-head{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline;font-size:var(--fs-m)}",
      ".lv-head b{font-weight:650}",
      ".lv-head .mono{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3)}",
      ".lv-line{display:block;width:100%;height:44px;overflow:visible}",
      ".lv-line .lv-mark{transition:opacity .25s}",
      ".lv-line .lv-new circle,.lv-line .lv-new rect{transform-box:fill-box;transform-origin:center;animation:lv-pop .5s ease-out}",
      "@keyframes lv-pop{from{transform:scale(2.2);opacity:.2}to{transform:scale(1);opacity:1}}",
      ".lv-line .lv-now circle,.lv-line .lv-now path{stroke:var(--accent);stroke-width:2.5px;animation:lv-pulse .8s ease-in-out infinite alternate}",
      "@keyframes lv-pulse{from{stroke-opacity:.4}to{stroke-opacity:1}}",
      "@media (prefers-reduced-motion:reduce){.lv-line .lv-new,.lv-line .lv-now circle,.lv-line .lv-now path{animation:none}}",
      ".lv-last{list-style:none;margin:4px 0 0;padding:0;font-size:var(--fs-s)}",
      ".lv-last li{display:grid;grid-template-columns:5ch 1fr;gap:8px;padding:2px 0;color:var(--ink-2)}",
      ".lv-last .n{font-family:var(--mono);color:var(--ink-3)}",
      ".lv-last .t{font-family:var(--mono);font-size:var(--fs-xs);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".lv-done{font-size:var(--fs-s);color:var(--ink-3)}",
      ".lv-fold > summary{cursor:pointer;font-size:var(--fs-xs);color:var(--ink-3)}",
    ].join("");
    document.head.appendChild(node);
  }

  var Seen = {};   // task:agent -> steps drawn last time, so new ones can pop

  function liveOf(ctx) {
    var state = typeof AgentDiff.state === "function" ? AgentDiff.state() : null;
    var data = state && state.data;
    var live = data && data.live;
    return live && typeof live === "object" ? live : null;
  }
  function runsFor(ctx) {
    var live = liveOf(ctx);
    if (!live || !Array.isArray(live.runs)) return [];
    return live.runs.filter(function (r) { return r && r.task === ctx.task; });
  }
  function finishedFor(ctx) {
    var live = liveOf(ctx);
    if (!live || !Array.isArray(live.finished)) return [];
    return live.finished.filter(function (r) { return r && r.task === ctx.task; });
  }
  function isNum(v) { return typeof v === "number" && isFinite(v); }

  AgentDiff.block({
    id: "live-run",
    title: "Running now",
    storyTitle: "Running now",
    question: "What is each agent doing at this moment?",
    group: "outcome",
    size: "wide",
    lead: false,

    relevance: function (ctx) {
      return runsFor(ctx).length || (finishedFor(ctx).length && !ctx.report) ? 1 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, S = ctx.svg;
      var runs = runsFor(ctx);
      var finished = finishedFor(ctx);
      if (!runs.length && !finished.length) return ctx.empty(el, "Nothing is running for this task.");
      var color = ctx.color;
      runs.forEach(function (run, ri) {
        var key = run.task + ":" + run.agent;
        var steps = Array.isArray(run.steps) ? run.steps : [];
        var before = Seen[key] || 0;
        Seen[key] = steps.length;
        var tokens = steps.reduce(function (a, s) { return a + (isNum(s.tokens) ? s.tokens : 0); }, 0);
        var latency = steps.reduce(function (a, s) { return a + (isNum(s.latency_s) ? s.latency_s : 0); }, 0);
        var box = H("div", { class: "lv-run", "data-agent": run.agent, "data-steps": String(steps.length) });
        box.appendChild(H("div", { class: "lv-head" }, [
          H("b", { text: run.agent }),
          H("span", { class: "mono", text: steps.length + " step" + (steps.length === 1 ? "" : "s") + " so far · " +
            ctx.fmt.int(tokens) + " tokens · " + ctx.fmt.sec(latency) + (run.model ? " · " + run.model : "") }),
          H("span", { class: "mono", text: "running" }),
        ]));
        // the line: a mark per step, the newest pulsing, new arrivals popping;
        // drawn once the host has a width (and again on resize)
        var lineHost = H("div", { class: "lv-line-host" });
        box.appendChild(lineHost);
        var tint = ri === 0 ? color.a : color.b;
        var drawLine = function () {
          var W = Math.max(320, lineHost.clientWidth || ctx.width(el));
          var svg = S("svg", { class: "lv-line", viewBox: "0 0 " + W + " 44", width: W, height: 44, role: "img",
                               "aria-label": run.agent + ": " + steps.length + " steps so far" });
          var n = Math.max(1, steps.length);
          var cap = Math.max(8, Math.floor((W - 28) / 22));
          var start = Math.max(0, n - cap);
          var slot = (W - 28) / Math.min(n, cap);
          svg.appendChild(S("line", { x1: 14, x2: W - 14, y1: 20, y2: 20, stroke: color.grid, "stroke-width": 1.4 }));
          if (start > 0) svg.appendChild(S("text", { x: 14, y: 8, "font-size": 10, fill: color.muted, text: "← " + start + " earlier" }));
          steps.forEach(function (s, i) {
            if (i < start) return;
            var x = 14 + (i - start + 0.5) * slot;
            var g = S("g", { class: "lv-mark" + (i >= before ? " lv-new" : "") + (i === steps.length - 1 ? " lv-now" : ""),
                             transform: "translate(" + x + ",20)", "data-step": String(i) });
            var shape = s.type === "plan" ? "rect" : "circle";
            if (shape === "rect") g.appendChild(S("rect", { x: -5, y: -5, width: 10, height: 10, fill: color.surface, stroke: tint, "stroke-width": 1.6 }));
            else g.appendChild(S("circle", { r: 5, fill: s.type === "tool_call" ? tint : color.surface, stroke: tint, "stroke-width": 1.6 }));
            if (slot >= 22) g.appendChild(S("text", { y: 18, "text-anchor": "middle", "font-size": 9.5, fill: color.muted, "font-family": "var(--mono)", text: String(i) }));
            g.appendChild(S("title", { text: "step " + i + " · " + (s.name || s.type || "") + "\n" + String(s.output || s.input || "").slice(0, 200) }));
            svg.appendChild(g);
          });
          lineHost.appendChild(svg);
        };
        if (AgentDiff.charts && AgentDiff.charts.responsive) AgentDiff.charts.responsive(lineHost, drawLine);
        else drawLine();
        // the last three steps in full; every step one click away
        var last = steps.slice(-3);
        var list = H("ul", { class: "lv-last" });
        last.forEach(function (s) {
          list.appendChild(H("li", null, [
            H("span", { class: "n", text: String(s.index !== undefined ? s.index : steps.indexOf(s)) }),
            H("span", { class: "t", text: (s.name || s.type || "step") + " · " + String(s.output || s.input || "").replace(/\s+/g, " ").slice(0, 160) }),
          ]));
        });
        box.appendChild(list);
        if (steps.length > 3) {
          var fold = H("details", { class: "lv-fold" }, [H("summary", { text: "all " + steps.length + " steps" })]);
          var all = H("ul", { class: "lv-last" });
          steps.forEach(function (s, i) {
            all.appendChild(H("li", null, [H("span", { class: "n", text: String(i) }),
              H("span", { class: "t", text: (s.name || s.type || "step") + " · " + String(s.output || s.input || "").replace(/\s+/g, " ").slice(0, 160) })]));
          });
          fold.appendChild(all);
          box.appendChild(fold);
        }
        el.appendChild(box);
      });
      finished.forEach(function (f) {
        if (runs.some(function (r) { return r.agent === f.agent; })) return;
        el.appendChild(H("div", { class: "lv-run" }, [
          H("div", { class: "lv-head" }, [H("b", { text: f.agent }), H("span", { class: "lv-done", text: (f.success ? "solved" : "failed") + " in " + f.steps + " steps · waiting for the other run to finish before comparing" })]),
        ]));
      });
    },
  });

})(typeof window !== "undefined" ? window : this);
