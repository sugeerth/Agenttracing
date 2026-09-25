/* AgentDiff block — Reconcile.
 *
 * The reconciling strategy for the failing run: the report's counterfactual
 * splice (keep the failing run's prefix, take the passing run's decision at
 * the decisive step, follow the passing run from there) drawn as three
 * lanes with the cut marked, then the strategy as numbered steps with the
 * replay that would verify it and the estimate stated as an estimate.
 * Story section 4. Draws only what `counterfactual` and the decisive
 * step's replay recipe say.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  function hasPlan(report) {
    if (!report) return false;
    var cf = report.counterfactual;
    var dec = report.diagnosis && report.diagnosis.decisive_step;
    return !!((cf && cf.splice) || (dec && dec.replay_recipe));
  }

  AgentDiff.block({
    id: "reconcile",
    title: "Reconcile",
    storyTitle: "Reconcile",
    question: "How does the failing run get back onto the passing one, and what would that buy?",
    group: "outcome",
    size: "wide",

    relevance: function (ctx) {
      if (!hasPlan(ctx.report)) return 0;
      if (!AgentDiff.charts || !AgentDiff.charts.available()) return 0;
      var cf = ctx.report.counterfactual;
      return cf && cf.splice ? 0.9 : 0.7;
    },

    render: function (el, ctx) {
      var report = ctx.report;
      if (!hasPlan(report)) return ctx.empty(el, "This report carries no counterfactual splice and no replay recipe, so there is nothing to reconcile.");
      var cf = report.counterfactual || {};
      el.appendChild(ctx.h("p", {
        class: "rc-lede",
        text: cf.splice
          ? "The reconciled trajectory keeps the failing run up to the cut, takes the passing run's decision there, and follows the passing run after it. The estimate is the report's splice, not a replay."
          : "No splice estimate in this report; the strategy below is the decisive step's replay recipe.",
      }));
      var host = ctx.h("div", { class: "rc-chart" });
      el.appendChild(host);
      var drawn = null;
      try { drawn = AgentDiff.charts.reconcile(host, ctx); }
      catch (err) { console.warn("AgentDiff reconcile: chart failed", err); }
      if (!drawn && !host.childNodes.length) host.remove();
    },
  });

})(typeof window !== "undefined" ? window : this);
