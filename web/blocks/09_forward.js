/* AgentDiff block — Take forward.
 *
 * The reading's located next actions for the run being read, pinned to
 * the steps they apply to, numbered to match a list that quotes each
 * `instead` verbatim, with the replay that would test it and — when the
 * report carries a counterfactual estimate — what the fix buys, labelled
 * as an estimate. Reads `reading[side].take_forward` and nothing else the
 * reading block does not already show; in the story this is section 3.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  function readingOf(report, side) {
    var r = report && report.reading && report.reading[side];
    return r && typeof r === "object" ? r : null;
  }
  function failingSide(report) {
    var a = report && report.a && report.a.outcome, b = report && report.b && report.b.outcome;
    if (a && b && a.success !== b.success) return a.success ? "b" : "a";
    var diag = report && report.diagnosis;
    return diag && (diag.subject === "a" || diag.subject === "b") ? diag.subject : "b";
  }
  function sideOf(report) {
    var lens = AgentDiff.readLens;
    return (lens && (lens.side === "a" || lens.side === "b")) ? lens.side : failingSide(report);
  }
  function agentName(report, side) {
    var box = report && report[side];
    return (box && box.agent && box.agent.name) || side.toUpperCase();
  }

  AgentDiff.block({
    id: "take-forward",
    title: "Take forward",
    storyTitle: "Take forward",
    question: "What to change, at which step, and what it buys?",
    group: "outcome",
    size: "wide",

    relevance: function (ctx) {
      var reading = readingOf(ctx.report, sideOf(ctx.report));
      var items = reading && Array.isArray(reading.take_forward) ? reading.take_forward : [];
      if (!items.length) return 0;
      return items.some(function (t) { return t && typeof t.at_step === "number"; }) ? 0.92 : 0.7;
    },

    render: function (el, ctx) {
      var report = ctx.report;
      var side = sideOf(report);
      var reading = readingOf(report, side);
      var items = reading && Array.isArray(reading.take_forward) ? reading.take_forward : [];
      if (!items.length) return ctx.empty(el, "The reading located no next action for this run.");
      el.appendChild(ctx.h("p", {
        class: "fw-lede",
        text: items.length + " next action" + (items.length === 1 ? "" : "s") + " for " + agentName(report, side) +
              ", each at the step it applies to. The wording is the reading's own; a replay recipe says how to test it.",
      }));
      var host = ctx.h("div", { class: "fw-chart" });
      el.appendChild(host);
      var drawn = null;
      if (AgentDiff.charts && AgentDiff.charts.available()) {
        try { drawn = AgentDiff.charts.forward(host, ctx, side); }
        catch (err) { console.warn("AgentDiff take-forward: chart failed", err); }
      }
      if (!drawn) {
        // no chart engine: the list alone, still verbatim
        host.appendChild(ctx.h("ol", { class: "d3c-list" }, items.map(function (t) {
          return ctx.h("li", null, [
            ctx.h("span", { class: "n", text: "•" }),
            ctx.h("div", { text: (typeof t.at_step === "number" ? "at step " + t.at_step + " — " : "") + String(t.instead || t.action || "") }),
          ]);
        })));
      }
    },
  });

})(typeof window !== "undefined" ? window : this);
