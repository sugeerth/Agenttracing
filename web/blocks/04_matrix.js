/* AgentDiff blocks — which dimension caught which failure.
 *
 * `detection` already reports, per known failure, the list of dimensions
 * that said something was wrong about it. As a list per row that is
 * readable and says nothing: the fact worth seeing is not *what caught
 * this one* but the shape of the whole grid — whether any single column
 * is full, and whether any row has only one filled cell.
 *
 * Those two questions have opposite answers and both matter:
 *
 *   · **No column is full.** If one dimension caught everything, it would
 *     not be a strong dimension — it would be a detector that had learned
 *     the corpus, and the rest of the card would be decoration. A spread
 *     grid is the evidence that the catch is real.
 *   · **A row with one filled cell is fragile.** That failure is caught
 *     by exactly one thing, and the day that dimension changes it stops
 *     being caught. A table of lists hides those rows; a grid puts them
 *     in the same glance as everything else.
 *
 * Drawn as a grid rather than a heatmap because the value is binary —
 * caught or not — and a colour ramp over a boolean is a lie about
 * precision. Column totals sit under the columns, where a reader
 * comparing them is already looking.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-matrix-css";

  /* the dimensions, in the order `scorecard.DETECTION_SIGNALS` declares
     them, so the page and the engine read left to right the same way */
  var LABELS = {
    grade: "the grade", milestones: "milestones", order: "order",
    grounded: "grounding", policy: "policy", loop: "loops",
    unrecovered: "unrepaired errors", redundant: "re-done work",
    flags: "risk flags", kept_looking: "kept looking",
  };

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".mx{display:flex;flex-direction:column;gap:12px}",
      ".mx-lede{font-size:var(--fs-s);line-height:1.6;color:var(--ink-2);margin:0}",
      ".mx-lede b{color:var(--ink)}",
      ".mx-wrap{overflow-x:auto}",
      ".mx-table{border-collapse:collapse;font-size:var(--fs-xs)}",
      ".mx-table th.mode{text-align:right;font-weight:600;color:var(--ink-2);padding:0 8px 0 0;",
      "white-space:nowrap;font-family:ui-monospace,monospace}",
      ".mx-table th.dim{font-weight:600;color:var(--ink-3);padding:0 0 6px;height:88px;vertical-align:bottom}",
      ".mx-table th.dim span{display:inline-block;transform:rotate(-60deg);transform-origin:left bottom;",
      "white-space:nowrap;width:14px}",
      ".mx-table td{width:22px;height:20px;padding:0;text-align:center}",
      ".mx-cell{width:14px;height:14px;border-radius:3px;display:inline-block;",
      "border:1px solid var(--rule);background:transparent}",
      ".mx-cell.on{background:var(--accent);border-color:var(--accent)}",
      ".mx-table tr[data-fragile='true'] th.mode{color:var(--warn)}",
      ".mx-table tr[data-fragile='true'] .mx-cell.on{background:var(--warn);border-color:var(--warn)}",
      ".mx-total{font-family:ui-monospace,monospace;color:var(--ink-3);font-size:var(--fs-xs)}",
      ".mx-total.full{color:var(--bad);font-weight:700}",
      ".mx-note{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:0}",
      ".mx-note b{color:var(--ink-2)}",
    ].join(""));
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var card = ctx.aggregate && ctx.aggregate.scorecard;
    var det = card && card.detection;
    if (!det || !det.measurable || !(det.modes || []).length) {
      return ctx.empty(el, det && det.reason
        ? "No matrix: " + det.reason
        : "No matrix: this needs a golden set that names which failure each run is known to carry.");
    }
    var root = H("div", { class: "mx" });
    el.appendChild(root);

    // one row per known failure, collapsed by mode when a mode repeats
    var byMode = {};
    det.modes.forEach(function (row) {
      var got = byMode[row.mode] || (byMode[row.mode] = { mode: row.mode, runs: 0, signals: {} });
      got.runs += 1;
      (row.signals || []).forEach(function (s) { got.signals[s] = (got.signals[s] || 0) + 1; });
    });
    var modes = Object.keys(byMode).sort().map(function (k) { return byMode[k]; });
    var dims = Object.keys(det.by_signal || {});
    if (!dims.length) {
      dims = Object.keys(modes.reduce(function (acc, m) {
        Object.keys(m.signals).forEach(function (s) { acc[s] = 1; }); return acc; }, {}));
    }

    var fragile = modes.filter(function (m) { return Object.keys(m.signals).length === 1; });
    var fullest = dims.reduce(function (best, d) {
      var n = modes.filter(function (m) { return m.signals[d]; }).length;
      return n > best.n ? { d: d, n: n } : best;
    }, { d: null, n: 0 });

    var lede = H("p", { class: "mx-lede" });
    lede.appendChild(H("span", { text: modes.length + " known failure mode(s) down, " + dims.length +
      " dimension(s) across. " }));
    lede.appendChild(H("b", { text: "No dimension catches more than " + fullest.n + " of " + modes.length +
      (fullest.d ? " (" + (LABELS[fullest.d] || fullest.d) + ")" : "") + "." }));
    lede.appendChild(H("span", { text: fragile.length
      ? " " + fragile.length + " mode(s) are caught by exactly one dimension, and stop being caught the day it changes: "
        + fragile.map(function (m) { return m.mode; }).join(", ") + "."
      : " Every mode is caught by more than one dimension." }));
    root.appendChild(lede);

    var t = H("table", { class: "mx-table" });
    var head = H("tr", null, [H("th", { class: "mode" })]);
    dims.forEach(function (d) {
      head.appendChild(H("th", { class: "dim" }, [H("span", { text: LABELS[d] || d })]));
    });
    t.appendChild(head);

    modes.forEach(function (m) {
      var caught = Object.keys(m.signals).length;
      var tr = H("tr", { "data-mode": m.mode, "data-fragile": caught === 1 ? "true" : "false",
                         "data-caught-by": String(caught) });
      tr.appendChild(H("th", { class: "mode", text: m.mode }));
      dims.forEach(function (d) {
        var n = m.signals[d] || 0;
        var td = H("td");
        td.appendChild(H("span", { class: "mx-cell" + (n ? " on" : ""),
                                   title: (LABELS[d] || d) + " · " + m.mode + ": " +
                                          (n ? n + " of " + m.runs + " run(s)" : "did not catch it") }));
        td.setAttribute("data-on", n ? "1" : "0");
        tr.appendChild(td);
      });
      t.appendChild(tr);
    });

    var totals = H("tr", { "data-role": "totals" });
    totals.appendChild(H("th", { class: "mode mx-total", text: "of " + modes.length }));
    dims.forEach(function (d) {
      var n = modes.filter(function (m) { return m.signals[d]; }).length;
      totals.appendChild(H("td", null, [H("span", {
        class: "mx-total" + (n === modes.length ? " full" : ""), text: String(n) })]));
    });
    t.appendChild(totals);
    root.appendChild(H("div", { class: "mx-wrap" }, [t]));

    var controls = det.controls || {};
    var note = H("p", { class: "mx-note" });
    note.appendChild(H("b", { text: "Read the grid with the control line or neither means anything: " }));
    note.appendChild(H("span", { text: (controls.flagged ? controls.flagged : "none") + " of " +
      controls.runs + " run(s) known to be correct were flagged. A dimension that fired on everything " +
      "would fill its column and catch nothing." }));
    root.appendChild(note);
  }

  AgentDiff.block({
    id: "detection-matrix",
    storyTitle: "What caught what",
    title: "What caught what",
    question: "Which dimension catches which failure — and is any of them carrying the card alone?",
    group: "other",
    size: "wide",
    relevance: function (ctx) {
      var det = ctx.aggregate && ctx.aggregate.scorecard && ctx.aggregate.scorecard.detection;
      return det && det.measurable && (det.modes || []).length ? 0.96 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
