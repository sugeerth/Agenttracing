/* AgentDiff blocks — Focus: the corpus on one screen.
 *
 * The page has grown a block for every question, and every one of them
 * earns its place; together they are a long scroll, and the picture a
 * reader needs first is spread down it. Focus is the opposite
 * arrangement: five panels in one frame the height of the window, no page
 * scroll on a desk, each panel scrolling inside itself.
 *
 *   ┌ verdict (or the duel) ─┬ where the trouble is ──────────┐
 *   ├ what the traces taught ┼ the evals they wrote ┼ what to do ┤
 *
 * Nothing here draws its own chart: each panel is the block that already
 * answers that question, rendered through its own `render(el, ctx)` —
 * so the frame cannot drift from the full-size view, and a block improved
 * anywhere is improved here. Each panel's title jumps to that block in
 * the view where it lives at full size.
 *
 * Screen real estate is the design: the strip, which needs width, gets
 * the widest cell; the verdict, which is prose, the narrowest that keeps
 * a line readable; the bottom row splits evenly because its three panels
 * are lists. On a phone the frame becomes a column and the page scrolls,
 * because five scrolling panels on a phone are five traps.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-focus-css";

  //: [cell, block id, fallback block id, the view the block lives in at full size]
  var PANELS = [
    ["verdict", "duel", "corpus-verdict", "batch"],
    ["trouble", "evidence-strip", null, "batch"],
    ["taught", "lessons", null, "batch"],
    ["forged", "forge", null, "batch"],
    ["todo", "what-to-do", "recommendations", "batch"],
  ];

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".ff{display:grid;gap:12px;grid-template-columns:repeat(12,minmax(0,1fr));",
      "grid-template-rows:minmax(0,1.15fr) minmax(0,1fr);min-height:560px}",
      ".ff-p{display:flex;flex-direction:column;min-height:0;min-width:0;background:var(--surface);",
      "border:1px solid var(--rule);border-radius:10px;overflow:hidden}",
      ".ff-p[data-cell=verdict]{grid-column:1 / span 5}",
      ".ff-p[data-cell=trouble]{grid-column:6 / span 7}",
      ".ff-p[data-cell=taught]{grid-column:1 / span 4}",
      ".ff-p[data-cell=forged]{grid-column:5 / span 4}",
      ".ff-p[data-cell=todo]{grid-column:9 / span 4}",
      ".ff-head{display:flex;align-items:baseline;gap:8px;padding:8px 12px 6px;border-bottom:1px solid var(--rule)}",
      ".ff-head button{font:inherit;font-size:var(--fs-xs);font-weight:700;letter-spacing:.08em;text-transform:uppercase;",
      "color:var(--ink-2);background:none;border:0;padding:0;cursor:pointer;text-align:left}",
      ".ff-head button:hover,.ff-head button:focus-visible{color:var(--accent)}",
      ".ff-head .q{font-size:var(--fs-xs);color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1}",
      ".ff-body{flex:1;min-height:0;overflow:auto;padding:10px 12px}",
      "@media (max-width:1099px){.ff{grid-template-columns:minmax(0,1fr);grid-template-rows:none;height:auto !important}",
      ".ff-p[data-cell]{grid-column:1 / -1}.ff-body{max-height:none;overflow:visible}}",
    ].join(""));
  }

  function registry() {
    var I = AgentDiff._internals || {};
    return I.BY_ID || {};
  }

  function pick(ctx, ids) {
    var by = registry();
    for (var i = 0; i < ids.length; i++) {
      var e = ids[i] && by[ids[i]];
      if (e) {
        var rel = 0;
        try { rel = e.relevance ? e.relevance(Object.assign(Object.create(ctx), { view: "batch" })) : 1; } catch (err) { rel = 0; }
        if (rel > 0) return e;
      }
    }
    return null;
  }

  function hasCorpus(ctx) {
    var agg = ctx.aggregate || {};
    var sc = agg.scorecard;
    return !!((agg.duel && agg.duel.measurable) || (sc && (sc.per_run || []).length > 1));
  }

  function fit(frame) {
    if (global.innerWidth < 1100) { frame.style.height = ""; return; }
    var top = frame.getBoundingClientRect().top + (global.pageYOffset || 0);
    frame.style.height = Math.max(560, Math.floor(global.innerHeight - top - 14)) + "px";
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    if (!hasCorpus(ctx)) return ctx.empty(el, "Focus reads a corpus or a duel; this page carries one pair.");
    var frame = H("div", { class: "ff", "data-role": "focus-frame" });
    el.appendChild(frame);
    PANELS.forEach(function (p) {
      var entry = pick(ctx, [p[1], p[2]]);
      if (!entry) return;
      var body = H("div", { class: "ff-body" });
      var panel = H("section", { class: "ff-p", "data-cell": p[0], "data-block": entry.id }, [
        H("div", { class: "ff-head" }, [
          H("button", { text: entry.title, title: "Open " + entry.title + " at full size",
            onclick: function () {
              try { global.location.hash = "view=" + p[3]; } catch (err) { /* no location */ }
              setTimeout(function () {
                var node = document.querySelector('#stacks .block[data-block="' + entry.id + '"], #lead-lane .block[data-block="' + entry.id + '"], #hero-lane .block[data-block="' + entry.id + '"]');
                if (node && node.scrollIntoView) node.scrollIntoView({ block: "start" });
              }, 80);
            } }),
          H("span", { class: "q", text: entry.question || "" }),
        ]),
        body,
      ]);
      frame.appendChild(panel);
      var sub = Object.create(ctx);
      sub.lane = "focus";
      sub.view = "batch";
      try { entry.render(body, sub); } catch (err) {
        body.appendChild(H("div", { class: "empty", text: "This panel could not render: " + err.message }));
      }
    });
    requestAnimationFrame(function () { fit(frame); });
    if (!global.__agentdiffFocusResize) {
      global.__agentdiffFocusResize = true;
      global.addEventListener("resize", function () {
        var f = document.querySelector('[data-role="focus-frame"]');
        if (f) fit(f);
      });
    }
  }

  AgentDiff.block({
    id: "focus-frame",
    title: "Focus",
    question: "The corpus on one screen: the verdict, where the trouble is, what the traces taught, the evals they wrote, what to do.",
    group: "focus",
    size: "wide",
    unclamped: true,
    relevance: function (ctx) { return hasCorpus(ctx) ? 1 : 0; },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
