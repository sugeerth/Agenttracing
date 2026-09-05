/* AgentDiff block — The trace as a tree.
 *
 * The pair as one hierarchy: the task at the root, each run a branch,
 * the reading's phases under each run, their steps, and under a step the
 * answer values it first produced. The fault's path is red, the decisive
 * step ringed, dead ends dashed; phases fold and unfold; a step opens in
 * the inspector. Story section 2. Reads the same report fields the
 * reading and the map read; draws nothing derived.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  function stepsOf(report, side) {
    var box = report && report[side];
    return box && Array.isArray(box.steps) ? box.steps : [];
  }

  AgentDiff.block({
    id: "trace-tree",
    title: "The trace as a tree",
    storyTitle: "The trace as a tree",
    question: "How does each run branch from the task, and where does the fault run?",
    group: "trajectory",
    size: "wide",

    relevance: function (ctx) {
      var report = ctx.report;
      if (!report || (!stepsOf(report, "a").length && !stepsOf(report, "b").length)) return 0;
      if (!AgentDiff.charts || !AgentDiff.charts.available()) return 0;
      return report.reading ? 0.86 : 0.6;
    },

    render: function (el, ctx) {
      var report = ctx.report;
      if (!report) return ctx.empty(el, "No report loaded.");
      var n = stepsOf(report, "a").length + stepsOf(report, "b").length;
      if (!n) return ctx.empty(el, "Neither run recorded a step, so there is nothing to branch.");
      el.appendChild(ctx.h("p", {
        class: "tt-lede",
        text: "One tree for the pair: the task at the root, each run a branch, the reading's phases, their steps, " +
              "and the answer values a step first produced. Red links follow the fault; dashed links are dead ends. " +
              "Click a phase to fold it, a step to open it.",
      }));
      var host = ctx.h("div", { class: "tt-chart" });
      var narrow = false;
      try { narrow = (global.innerWidth || 1024) <= 700; } catch (err) { narrow = false; }
      if (narrow) {
        // on a phone the tree scrolls sideways, so it opens on request and
        // draws only then — the story's height budget stays honest
        var fold = ctx.h("details", { class: "tt-fold" }, [
          ctx.h("summary", { text: "Open the tree (" + n + " steps; scrolls sideways)" }),
        ]);
        fold.appendChild(host);
        var drawnLazy = false;
        fold.addEventListener("toggle", function () {
          if (!fold.open || drawnLazy) return;
          drawnLazy = true;
          try { AgentDiff.charts.tree(host, ctx); }
          catch (err) { console.warn("AgentDiff trace-tree: chart failed", err); }
        });
        el.appendChild(fold);
        return;
      }
      el.appendChild(host);
      var drawn = null;
      try { drawn = AgentDiff.charts.tree(host, ctx); }
      catch (err) { console.warn("AgentDiff trace-tree: chart failed", err); }
      if (!drawn) host.remove();
    },
  });

})(typeof window !== "undefined" ? window : this);
