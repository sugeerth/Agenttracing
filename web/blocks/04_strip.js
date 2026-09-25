/* AgentDiff blocks — the corpus as strips: where in each run the trouble is.
 *
 * Every number on this page about a long run is a count, and a count
 * throws away the one thing a long run has that a short one does not:
 * *position*. "Twelve unrepaired errors" is the same figure whether the
 * run fell over at step 12 or step 212, and those are not the same run —
 * one never got going, the other got most of the way and then broke.
 *
 * So: one strip per run, steps left to right, with a mark wherever a
 * dimension of the card fired. Nothing here is derived — every mark is a
 * step index the engine already recorded (`safety.risk_flags[].step`,
 * `recovery.unrecovered_at`, `trajectory.redundant_at`,
 * `milestones.last_reached_step`) — and the chart's whole job is to put
 * them on the same axis, which no table can do.
 *
 * What it makes visible, on the long-horizon suite, in one look:
 *
 *   · the correct runs are clean strips, and they are clean *along their
 *     whole length* rather than on average;
 *   · the failures cluster late, which is why an excerpt taken from the
 *     front of a run finds nothing;
 *   · where a run stopped making progress sits well before where it
 *     stopped, and the gap between those two marks is the part of the run
 *     that was already lost.
 *
 * Restraint, because this page does not decorate: no animation on load,
 * no gradient, no axis that is not read. Colour carries one meaning
 * (which dimension), position carries the other (where), and the legend
 * is the same words the dashboard uses.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-strip-css";

  /* the dimensions a strip can show, in the order they are read:
     the worst first, so an eye running down a column meets them first */
  var KINDS = [
    { key: "flag", label: "risk flag", css: "bad" },
    { key: "unrecovered", label: "error nothing repaired", css: "bad" },
    { key: "redundant", label: "work re-done", css: "warn" },
    { key: "stalled", label: "last milestone reached", css: "mid" },
  ];

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".st{display:flex;flex-direction:column;gap:12px}",
      ".st-lede{font-size:var(--fs-s);line-height:1.6;color:var(--ink-2);margin:0}",
      ".st-lede b{color:var(--ink)}",
      ".st-key{display:flex;flex-wrap:wrap;gap:12px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".st-key span{display:inline-flex;align-items:center;gap:5px}",
      ".st-key i{width:9px;height:9px;border-radius:2px;display:inline-block}",
      ".st-key i.bad{background:var(--bad)}.st-key i.warn{background:var(--warn)}",
      ".st-key i.mid{background:var(--accent)}.st-key i.run{background:var(--rule)}",
      ".st-wrap{overflow-x:auto}",
      ".st-row-label{font:600 var(--fs-xs)/1 inherit;fill:var(--ink-2)}",
      ".st-row-label.clean{fill:var(--ink-3)}",
      ".st-axis{font:var(--fs-xs)/1 inherit;fill:var(--ink-3)}",
      ".st-note{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:0}",
      ".st-mark{cursor:pointer}",
      ".st-mark:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ].join(""));
  }

  /* ---------------------------------------------------------------- data */

  function marksOf(run) {
    var out = [];
    (run.safety && run.safety.risk_flags || []).forEach(function (f) {
      var step = f.step && typeof f.step === "object" ? f.step.index : f.step;
      if (typeof step === "number") {
        out.push({ at: step, kind: "flag",
                   say: String(f.kind || "risk flag").replace(/_/g, " ") + " — " + (f.detail || "") });
      }
    });
    ((run.recovery || {}).unrecovered_at || []).forEach(function (step) {
      out.push({ at: step, kind: "unrecovered", say: "an error nothing repaired" });
    });
    ((run.trajectory || {}).redundant_at || []).forEach(function (step) {
      out.push({ at: step, kind: "redundant",
                 say: (run.trajectory.redundant_stretch || 0) + " steps that produced nothing new" });
    });
    var ms = run.milestones || {};
    if (ms.measurable && ms.complete === false && typeof ms.last_reached_step === "number") {
      out.push({ at: ms.last_reached_step, kind: "stalled",
                 say: "last milestone reached — " + ms.reached + " of " + ms.total +
                      ", then stalled at " + (ms.stalled_at || "the next one") });
    }
    return out;
  }

  function rowsOf(card) {
    return (card.per_run || []).map(function (r) {
      var marks = marksOf(r);
      return {
        task: r.task, agent: r.agent, run: r.task + " · " + r.agent,
        steps: (r.spend && r.spend.steps) || 0,
        success: r.success, marks: marks, clean: !marks.length,
        capped: !!((r.recovery || {}).unrecovered_capped),
      };
    }).sort(function (a, b) {
      return (a.agent < b.agent ? -1 : a.agent > b.agent ? 1 : 0) ||
             (a.task < b.task ? -1 : a.task > b.task ? 1 : 0);
    });
  }

  /* ---------------------------------------------------------------- view */

  function draw(host, rows, ctx) {
    var d3 = global.d3;
    var charts = AgentDiff.charts;
    var H = ctx.h;
    var ROW = 15, PAD_L = 186, PAD_R = 14, PAD_T = 22, PAD_B = 22;
    var measured = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
    var width = Math.max(560, Math.min(1120, measured || 720));
    var height = PAD_T + rows.length * ROW + PAD_B;
    var longest = Math.max(1, d3.max(rows, function (r) { return r.steps; }) || 1);

    var svg = d3.select(host).append("svg")
      .attr("width", width).attr("height", height)
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("role", "img")
      .attr("aria-label", "One strip per run: steps left to right, marked where each dimension fired.");

    var x = d3.scaleLinear().domain([0, longest]).range([PAD_L, width - PAD_R]);

    // the axis, read once at the top: steps, not time
    [0, Math.round(longest / 2), longest].forEach(function (v) {
      svg.append("text").attr("class", "st-axis").attr("x", x(v))
        .attr("y", 12).attr("text-anchor", v === 0 ? "start" : v === longest ? "end" : "middle")
        .text(v === 0 ? "step 0" : String(v));
    });

    var color = { flag: "var(--bad)", unrecovered: "var(--bad)",
                  redundant: "var(--warn)", stalled: "var(--accent)" };

    rows.forEach(function (row, i) {
      var y = PAD_T + i * ROW + ROW / 2;
      svg.append("text").attr("class", "st-row-label" + (row.clean ? " clean" : ""))
        .attr("x", PAD_L - 8).attr("y", y + 3).attr("text-anchor", "end")
        .text(row.run.length > 34 ? row.run.slice(0, 33) + "…" : row.run);

      // the run itself: its length, to the same scale as every other run
      svg.append("rect").attr("x", x(0)).attr("y", y - 2)
        .attr("width", Math.max(1, x(row.steps) - x(0))).attr("height", 4)
        .attr("rx", 2).attr("fill", "var(--rule)");

      row.marks.forEach(function (m) {
        var mark = svg.append("rect").attr("class", "st-mark")
          .attr("x", x(Math.min(m.at, row.steps)) - 1.5).attr("y", y - 6)
          .attr("width", 3).attr("height", 12).attr("rx", 1.5)
          .attr("fill", color[m.kind] || "var(--ink-3)")
          .attr("tabindex", 0)
          .attr("aria-label", row.run + ", step " + m.at + ": " + m.say);
        if (charts && charts._tip) {
          mark.on("mouseenter", function (event) {
            charts._tip.show(event, [row.run, "step " + m.at + " of " + row.steps, m.say]);
          }).on("mousemove", function (event) { charts._tip.show(event, [row.run,
            "step " + m.at + " of " + row.steps, m.say]); })
            .on("mouseleave", function () { charts._tip.hide(); });
        }
        mark.on("click", function () {
          try { charts && charts.selectStep && charts.selectStep(ctx.report, "a", m.at); } catch (e) { /* batch page */ }
        });
      });
    });
    return svg;
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var card = ctx.aggregate && ctx.aggregate.scorecard;
    if (!card || !(card.per_run || []).length) {
      return ctx.empty(el, "No strips: this page was built without scored runs.");
    }
    if (!AgentDiff.charts || !AgentDiff.charts.available()) {
      return ctx.empty(el, "No strips: the chart library did not load, so this block draws nothing rather "
                           + "than drawing something it cannot check.");
    }
    var rows = rowsOf(card);
    var root = H("div", { class: "st" });
    el.appendChild(root);

    var marked = rows.filter(function (r) { return !r.clean; });
    var late = marked.filter(function (r) {
      return r.marks.some(function (m) { return r.steps && m.at / r.steps > 0.5; });
    });
    var lede = H("p", { class: "st-lede" });
    if (!marked.length) {
      lede.appendChild(H("span", { text: "Every run is clean along its whole length, not on average." }));
    } else {
      lede.appendChild(H("b", { text: marked.length + " of " + rows.length + " run(s) carry a mark" }));
      lede.appendChild(H("span", { text: ", and " + late.length + " of those carry one past their own " +
        "halfway point. A count cannot say that: twelve unrepaired errors reads the same whether a run " +
        "fell over at step 12 or step 212." }));
    }
    root.appendChild(lede);

    var key = H("div", { class: "st-key" });
    KINDS.forEach(function (k) {
      key.appendChild(H("span", null, [H("i", { class: k.css }), H("span", { text: k.label })]));
    });
    key.appendChild(H("span", null, [H("i", { class: "run" }), H("span", { text: "the run, to scale" })]));
    root.appendChild(key);

    var wrap = H("div", { class: "st-wrap" });
    root.appendChild(wrap);
    // `responsive` calls its painter with no arguments and redraws on
    // resize, so the host is captured rather than passed
    AgentDiff.charts.responsive(wrap, function () { draw(wrap, rows, ctx); }, "strip");

    var capped = rows.filter(function (r) { return r.capped; }).length;
    root.appendChild(H("p", { class: "st-note", text:
      "Every mark is a step index the engine recorded — a risk flag's own step, an error the run never came "
      + "back to, the start of a stretch that produced nothing new, the last milestone reached. Runs share "
      + "one scale, so a short run's strip is short."
      + (capped ? " " + capped + " run(s) have more unrepaired errors than the row carries; the earliest are shown."
                : "") }));
  }

  AgentDiff.block({
    id: "evidence-strip",
    storyTitle: "Where the trouble is",
    title: "Where the trouble is",
    question: "Where along each run did something go wrong, and which dimension saw it?",
    group: "other",
    size: "wide",
    relevance: function (ctx) {
      var card = ctx.aggregate && ctx.aggregate.scorecard;
      return card && (card.per_run || []).length > 1 ? 0.98 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
