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
      ".st-key i.mid{background:var(--ink-2)}.st-key i.run{background:var(--rule)}",
      ".st-wrap{overflow-x:auto}",
      // SVG text takes explicit properties: `inherit` is not a family inside
      // the `font` shorthand, so a shorthand here is dropped whole and the
      // text falls back to 16px
      ".st-row-label{font-size:var(--fs-xs);font-weight:500;fill:var(--ink-2)}",
      ".st-agent{font-size:var(--fs-s);font-weight:700;fill:var(--ink)}",
      ".st-agent-sub{font-size:var(--fs-xs);fill:var(--ink-3)}",
      ".st-row-label.clean{fill:var(--ink-3)}",
      ".st-axis{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".st-note{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:0}",
      ".st-mark{cursor:pointer}",
      ".st-mark:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
      ".st-mark.seeded{stroke:var(--ink);stroke-width:2}",
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

  function human(task) {
    // "L03_data_backfill" → "L03 data backfill": the id, readable
    return String(task || "").replace(/_/g, " ");
  }

  function draw(host, rows, ctx) {
    var d3 = global.d3;
    var charts = AgentDiff.charts;
    var ROW = 16, HEAD = 24, PAD_R = 16, PAD_T = 24, PAD_B = 10;
    var measured = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
    var width = Math.max(560, Math.min(1180, measured || 760));

    // rows grouped under their agent: the agent is said once, as a heading,
    // and each row is labelled by its task — half the width of "task ·
    // agent" on every line, and the grouping is the comparison a reader
    // is making anyway
    var groups = [];
    rows.forEach(function (r) {
      var g = groups[groups.length - 1];
      if (!g || g.agent !== r.agent) groups.push(g = { agent: r.agent, rows: [] });
      g.rows.push(r);
    });
    // the label column is as wide as the widest label actually renders — an
    // estimate per character was how the first version clipped them
    var probe = d3.select(host).append("svg").attr("width", 0).attr("height", 0)
      .style("position", "absolute").style("visibility", "hidden");
    var widest = 0;
    rows.forEach(function (r) {
      var t = probe.append("text").attr("class", "st-row-label").text(human(r.task));
      try { widest = Math.max(widest, t.node().getComputedTextLength()); } catch (e) { widest = 180; }
    });
    probe.remove();
    var PAD_L = Math.max(96, Math.min(280, Math.ceil(widest) + 22));
    var height = PAD_T + groups.length * HEAD + rows.length * ROW + PAD_B;
    var longest = Math.max(1, d3.max(rows, function (r) { return r.steps; }) || 1);

    var svg = d3.select(host).append("svg")
      .attr("width", width).attr("height", height)
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("role", "img")
      .attr("aria-label", "One strip per run, grouped by agent: steps left to right on one scale, " +
            "marked where each dimension fired.");

    var x = d3.scaleLinear().domain([0, longest]).range([PAD_L, width - PAD_R]);

    // the scale, once, at the top: steps, not time, shared by every run
    var ticks = x.ticks(6).filter(function (t) { return t > 0 && t < longest; });
    ticks.forEach(function (t) {
      svg.append("line").attr("x1", x(t)).attr("x2", x(t))
        .attr("y1", PAD_T - 6).attr("y2", height - PAD_B)
        .attr("stroke", "var(--rule)").attr("stroke-width", 1).attr("stroke-dasharray", "2,4");
      svg.append("text").attr("class", "st-axis").attr("x", x(t)).attr("y", PAD_T - 10)
        .attr("text-anchor", "middle").text(String(t));
    });
    svg.append("text").attr("class", "st-axis").attr("x", x(0)).attr("y", PAD_T - 10)
      .attr("text-anchor", "start").text("step 0");

    var color = { flag: "var(--bad)", unrecovered: "var(--bad)",
                  redundant: "var(--warn)", stalled: "var(--ink-2)" };
    var y = PAD_T;
    groups.forEach(function (group, gi) {
      var markedInGroup = group.rows.filter(function (r) { return !r.clean; }).length;
      var headY = y + HEAD - 8;
      svg.append("rect").attr("x", 0).attr("y", headY - 9).attr("width", 9).attr("height", 9)
        .attr("rx", 2).attr("fill", gi === 0 ? "var(--a)" : gi === 1 ? "var(--b)" : "var(--ink-3)");
      svg.append("text").attr("class", "st-agent").attr("x", 15).attr("y", headY)
        .text(group.agent);
      svg.append("text").attr("class", "st-agent-sub").attr("x", width - PAD_R).attr("y", headY)
        .attr("text-anchor", "end")
        .text(markedInGroup ? markedInGroup + " of " + group.rows.length + " run(s) marked"
                            : "all " + group.rows.length + " runs clean");
      y += HEAD;

      group.rows.forEach(function (row) {
        var cy = y + ROW / 2;
        svg.append("text").attr("class", "st-row-label" + (row.clean ? " clean" : ""))
          .attr("x", PAD_L - 10).attr("y", cy + 3.5).attr("text-anchor", "end")
          .text(human(row.task));

        // the run: its length, on the scale every other run shares
        svg.append("rect").attr("class", "st-run").attr("data-steps", row.steps)
          .attr("x", x(0)).attr("y", cy - 2)
          .attr("width", Math.max(1, x(row.steps) - x(0))).attr("height", 4)
          .attr("rx", 2).attr("fill", row.clean ? "var(--rule)" : "var(--rule-2, var(--rule))");
        // its own halfway point — the lede counts marks past it, so the
        // chart shows where it is on every run rather than asserting it
        if (!row.clean && row.steps) {
          svg.append("line").attr("class", "st-half")
            .attr("x1", x(row.steps / 2)).attr("x2", x(row.steps / 2))
            .attr("y1", cy - 5).attr("y2", cy + 5)
            .attr("stroke", "var(--ink-3)").attr("stroke-width", 1);
        }

        row.marks.forEach(function (m) {
          var label = human(row.task) + " · " + row.agent + ", step " + m.at + ": " + m.say;
          var mark = svg.append("rect").attr("class", "st-mark")
            .attr("x", x(Math.min(m.at, row.steps)) - 1.75).attr("y", cy - 6.5)
            .attr("width", 3.5).attr("height", 13).attr("rx", 1.75)
            .attr("fill", color[m.kind] || "var(--ink-3)")
            .attr("tabindex", 0)
            .attr("aria-label", label);
          var lines = [human(row.task) + " · " + row.agent, "step " + m.at + " of " + row.steps + " (" +
                       Math.round(100 * m.at / Math.max(1, row.steps)) + "% through)", m.say];
          if (charts && charts._tip) {
            mark.on("mouseenter", function (event) { charts._tip.show(event, lines); })
              .on("mousemove", function (event) { charts._tip.show(event, lines); })
              .on("mouseleave", function () { charts._tip.hide(); })
              .on("focus", function (event) { charts._tip.show(event, lines); })
              .on("blur", function () { charts._tip.hide(); });
          }
          // a click marks the step as an eval seed (`05_forge.js`): the
          // reader's eye joins the forge's loop, and nothing leaves the page
          var seed = { task: row.task, agent: row.agent, step: m.at, kind: m.kind, say: m.say };
          var seeds = global.AgentDiff && global.AgentDiff.seeds;
          if (seeds && seeds.has(seed)) mark.classed("seeded", true);
          var toggle = function () {
            try { charts && charts.selectStep && charts.selectStep(ctx.report, "a", m.at); } catch (e) { /* batch page */ }
            var S = global.AgentDiff && global.AgentDiff.seeds;
            if (!S) return;
            var on = S.toggle(seed);
            mark.classed("seeded", on);
            if (global.AgentDiff.toast) {
              global.AgentDiff.toast(on ? "Step " + m.at + " of " + human(row.task) + " · " + row.agent +
                                          " marked as an eval seed (" + S.list().length + " marked)"
                                        : "Unmarked step " + m.at + " of " + human(row.task));
            }
            try { global.dispatchEvent(new CustomEvent("agentdiff-seeds")); } catch (e) { /* old browser */ }
          };
          mark.on("click", toggle)
            .on("keydown", function (event) {
              if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
            });
        });
        y += ROW;
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
