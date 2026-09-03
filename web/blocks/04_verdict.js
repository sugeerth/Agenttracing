/* AgentDiff blocks — the verdict card.
 *
 * Five lines, first on the page: who won and who failed; why, in the
 * trace's own words when the decisive step carries a note; what the
 * outcome cost; what to do next; how sure the engine is. Every line is
 * computed once by the engine (`report.verdict_card`) and quoted here
 * verbatim — the block never composes a sentence of its own, so the page
 * can never say something the report does not. Lines that carry a step
 * are chips: clicking one moves the shared cursor the trajectory map,
 * step detail and run lens already follow.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  var STYLE_ID = "agentdiff-verdict-css";
  var styled = false;

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".vc{display:grid;grid-template-columns:max-content 1fr;gap:6px 14px;",
        "font-size:13.5px;line-height:1.5;color:var(--ink)}",
        ".vc .k{font-size:11px;text-transform:uppercase;letter-spacing:.09em;",
        "font-weight:700;color:var(--ink-3);padding-top:3px}",
        ".vc .v{margin:0}",
        ".vc .v.verdict{font-size:15px;font-weight:600}",
        ".vc .v.cause{border-left:3px solid var(--accent);padding-left:8px}",
        ".vc .v.confidence{color:var(--ink-2);font-size:12.5px}",
        ".vc-chip{display:inline-block;margin-right:6px;padding:0 7px;border-radius:999px;",
        "border:1px solid var(--rule);background:var(--surface-2);font:600 11px/18px ",
        "ui-monospace,monospace;color:var(--accent);cursor:pointer}",
        ".vc-chip:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
        ".vc-src{display:block;font-size:11px;color:var(--ink-3);margin-top:1px}"
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is optional */ }
  }

  var LABELS = { verdict: "Verdict", cause: "Cause", cost: "Cost",
                 fix: "Fix", confidence: "Confidence" };

  function rowFor(report, side, step) {
    var rows = report && Array.isArray(report.alignment) ? report.alignment : [];
    var key = side + "_index";
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i][key] === step) return i;
    }
    return -1;
  }

  function stepChip(ctx, report, line) {
    var side = line.side, step = line.step;
    var label = (side === "a" || side === "b" ? side.toUpperCase() + " " : "") + "step " + step;
    return ctx.h("button", {
      class: "vc-chip", type: "button", text: label,
      title: "show this step in the map, step detail and run lens",
      "data-step": String(step), "data-side": side || "",
      onclick: function () {
        var row = rowFor(report, side, step);
        try { ctx.signal("inspect"); } catch (e) { /* optional */ }
        if (row < 0) return;
        try {
          document.dispatchEvent(new CustomEvent("agentdiff:select-step",
            { detail: { row: row, side: side } }));
        } catch (e) { /* no CustomEvent: the chip is inert */ }
      }
    });
  }

  AgentDiff.block({
    id: "verdict-card",
    title: "Verdict",
    question: "Who won, why, at what cost, what next, how sure?",
    group: "outcome",
    size: "wide",
    lead: true,
    relevance: function (ctx) {
      var card = ctx.report && ctx.report.verdict_card;
      return card && Array.isArray(card.lines) && card.lines.length ? 1 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var card = report && report.verdict_card;
      if (!card || !Array.isArray(card.lines) || !card.lines.length) {
        ctx.empty(el, "This report predates the verdict card; re-run compare to get one.");
        return;
      }
      var grid = ctx.h("div", { class: "vc", role: "list" });
      card.lines.forEach(function (line) {
        grid.appendChild(ctx.h("div", { class: "k", text: LABELS[line.key] || line.key }));
        var kids = [];
        if (typeof line.step === "number") kids.push(stepChip(ctx, report, line));
        kids.push(ctx.h("span", { text: line.text }));
        if (line.source) kids.push(ctx.h("span", { class: "vc-src", text: "from " + line.source }));
        grid.appendChild(ctx.h("p", { class: "v " + line.key, role: "listitem" }, kids));
      });
      el.appendChild(grid);
    }
  });
})(typeof window !== "undefined" ? window : this);
