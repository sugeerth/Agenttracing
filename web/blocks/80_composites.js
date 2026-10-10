/* AgentDiff blocks — the composites.
 *
 * Three cards that say once what several blocks said separately:
 *
 *   root-cause      = attribution + divergences         (Evidence)
 *   process-checks  = the six process-integrity checks  (Evidence)
 *   variance-all    = the four variance blocks           (Batch)
 *
 * A composite lays its parts out one after another under small titles,
 * shows only the parts with something to say, says a repeated note once,
 * and opens with a one-line summary. The parts leave the default layout
 * and stay in the drawer for anyone who wants one on its own.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.composite !== "function") return;

  AgentDiff.composite({
    id: "root-cause",
    title: "Root cause",
    question: "Where did the runs first part, and what did the failure trace back to?",
    group: "outcome",
    size: "wide",
    parts: ["attribution", "divergences"],
    emptyText: "No failure to attribute: neither run failed, or the runs never diverged.",
  });

  var CHECKS = ["integrity-flags", "gap", "claims-vs-actions", "side-effects",
                "loops-repeats", "recovery-errors"];
  AgentDiff.composite({
    id: "process-checks",
    title: "Process checks",
    question: "Did the way the work was done hold up — flags, gaps, side effects, loops, errors?",
    group: "integrity",
    size: "wide",
    parts: CHECKS,
    summary: function (ctx, shown, silent) {
      if (!shown.length) return null;
      return shown.length + " of " + CHECKS.length + " checks have something to show"
           + (silent.length ? "; " + silent.length + " found nothing or could not be measured" : "");
    },
    emptyText: "No process check separates the runs, and none found anything worth showing.",
  });

  AgentDiff.composite({
    id: "variance-all",
    title: "Variance",
    question: "Where does the variation in outcomes come from — model, harness, task, or noise?",
    group: "signal",
    size: "wide",
    parts: ["variance", "variance-design", "variance-corrected", "variance-residual"],
    emptyText: "No repeated runs, so nothing to attribute variation to.",
  });
})(typeof window !== "undefined" ? window : this);
