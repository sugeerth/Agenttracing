/* AgentDiff blocks — reliability over repeats, and the failure taxonomies.
 *
 * Six blocks over two engine modules that had no interface: deepcompare
 * .reliability (pass^k, consistency, ICC, the runs advisory) and
 * deepcompare.taxonomy (MAST and TRAIL).
 *
 * Both modules exist because a number can be true and still mislead, so both
 * ship their qualifiers as data rather than as prose someone may not read.
 * These blocks are built the same way round: the qualifier is not a footnote
 * under the figure, it is the frame the figure is drawn inside. Concretely —
 *
 *   · the pass^k curve is never drawn without pass@k beside it, because the
 *     divergence between "can it ever" and "can it every time" is the finding
 *     and either line alone is the wrong half of it;
 *   · the runs advisory is rendered above every reliability figure it
 *     qualifies, and on a three-run demo the loudest thing on the card is
 *     that nothing here can rank two agents;
 *   · a value of null with a stated reason renders as the reason, never as 0
 *     and never as a blank cell;
 *   · a taxonomy category that is 0 by construction says *by construction*,
 *     because "AgentDiff found no inter-agent misalignment" and "AgentDiff
 *     cannot see inter-agent misalignment" are opposite claims;
 *   · coverage is reported mass-weighted as well as by mode count, since the
 *     modes this tool cannot reach are not the rare ones.
 *
 * Relevance is 0 when the aggregate key is absent: reliability exists only in
 * `deepcompare runs` output and taxonomy in batch output, so on any one
 * report half of these blocks hide themselves. That is the contract.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style

  var styleInjected = false;
  function ensureStyle() {
    if (styleInjected) return;
    styleInjected = true;
    try {
      if (document.getElementById("agentdiff-science-style")) return;
      var node = document.createElement("style");
      node.id = "agentdiff-science-style";
      node.textContent = [
        ".sc-note{border:1px solid var(--rule);border-left-width:3px;border-radius:7px;",
        "padding:7px 9px;font-size:var(--fs-s);line-height:1.45;margin:0 0 9px;",
        "background:var(--surface-2);color:var(--ink-2)}",
        ".sc-note strong{color:var(--ink)}",
        ".sc-note.bad{border-left-color:var(--bad)}",
        ".sc-note.warn{border-left-color:var(--warn)}",
        ".sc-note.good{border-left-color:var(--good)}",
        ".sc-note.info{border-left-color:var(--rule-2)}",
        ".sc-verdict{border:1px solid var(--rule-2);border-left-width:4px;",
        "border-radius:8px;padding:10px 11px;margin:0 0 11px;background:var(--surface-2)}",
        ".sc-verdict.bad{border-left-color:var(--bad)}",
        ".sc-verdict.warn{border-left-color:var(--warn)}",
        ".sc-verdict.good{border-left-color:var(--good)}",
        ".sc-verdict-head{display:flex;flex-wrap:wrap;gap:7px;align-items:baseline;",
        "margin-bottom:5px}",
        ".sc-verdict-lead{font-size:var(--fs-m);font-weight:650;color:var(--ink);",
        "line-height:1.35}",
        ".sc-verdict-body{font-size:var(--fs-s);color:var(--ink-2);line-height:1.5}",
        ".sc-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.06em;",
        "color:var(--ink-3);margin:12px 0 6px}",
        ".sc-h:first-child{margin-top:0}",
        ".sc-sub{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.45}",
        ".sc-line{font-size:var(--fs-s);color:var(--ink);line-height:1.5}",
        ".sc-chips{display:flex;flex-wrap:wrap;gap:4px;margin:5px 0 0;align-items:center}",
        ".sc-panel{border-top:1px solid var(--rule);padding-top:10px;margin-top:12px}",
        ".sc-panel:first-child{border-top:none;padding-top:0;margin-top:0}",
        ".sc-agent{display:flex;flex-wrap:wrap;gap:7px;align-items:baseline;margin-bottom:6px}",
        ".sc-agent b{font-size:var(--fs-m);color:var(--ink)}",
        ".sc-claims{list-style:none;margin:6px 0 0;padding:0;font-size:var(--fs-s);line-height:1.5}",
        ".sc-claims li{display:flex;gap:7px;padding:2px 0;color:var(--ink-2)}",
        ".sc-claims li i{font-style:normal;flex:0 0 auto;width:12px;text-align:center}",
        ".sc-claims li.yes i{color:var(--good)}",
        ".sc-claims li.no i{color:var(--bad)}",
        ".sc-claims li.no{color:var(--ink)}",
        ".sc-bars{display:grid;gap:5px;margin-top:4px;max-width:640px}",
        ".sc-bar{display:grid;grid-template-columns:minmax(88px,0.9fr) minmax(60px,1.6fr) auto;",
        "gap:9px;align-items:center;font-size:var(--fs-s)}",
        ".sc-meter{max-width:320px}",
        ".sc-bar-l{color:var(--ink-2);overflow-wrap:anywhere}",
        ".sc-bar-t{height:9px;border-radius:3px;background:var(--rule);overflow:hidden}",
        ".sc-bar-t>i{display:block;height:100%;border-radius:3px}",
        ".sc-bar-v{font-variant-numeric:tabular-nums;color:var(--ink);font-size:var(--fs-xs);",
        "white-space:nowrap}",
        ".sc-bar.zero .sc-bar-t{background:repeating-linear-gradient(135deg,",
        "var(--rule) 0 4px,transparent 4px 8px);border:1px solid var(--rule)}",
        ".sc-hero{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin:2px 0 4px}",
        ".sc-hero-n{font-size:var(--fs-xxl);font-weight:640;line-height:1;color:var(--ink);",
        "font-variant-numeric:tabular-nums}",
        ".sc-hero-c{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.4;max-width:34ch}",
        ".sc-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:var(--fs-xs);",
        "color:var(--ink-3);margin:6px 0 2px;align-items:center}",
        ".sc-dot{display:inline-block;width:16px;height:0;vertical-align:2px;",
        "margin-right:5px;border-top-width:2.4px;border-top-style:solid}",
        ".sc-swatch{display:inline-block;width:12px;height:9px;vertical-align:-1px;",
        "margin-right:5px;border-radius:2px}",
        ".sc-flag{color:var(--bad);font-weight:600}",
        ".sc-mono-b{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2)}",
        "table.grid td.sc-num{font-variant-numeric:tabular-nums;white-space:nowrap}",
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is a nicety; the blocks still read without it */ }
  }

  // ------------------------------------------------------------------ utils

  function isNum(v) { return typeof v === "number" && !isNaN(v); }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : null; }
  function str(v) { return typeof v === "string" && v ? v : ""; }

  function reliabilityOf(ctx) {
    var block = obj(ctx && ctx.aggregate && ctx.aggregate.reliability);
    if (!block) return null;
    return obj(block.per_agent) ? block : null;
  }

  function taxonomyOf(ctx) {
    return obj(ctx && ctx.aggregate && ctx.aggregate.taxonomy);
  }

  /* Sides in a stable order, each with its side key so the agent's own colour
   * token follows it into every chart and chip. */
  function sides(rel) {
    var per = obj(rel && rel.per_agent) || {};
    return Object.keys(per).sort().map(function (side) {
      var row = obj(per[side]) || {};
      return {
        side: side,
        row: row,
        name: str(row.agent) || str((obj(rel.agents) || {})[side]) || side,
      };
    });
  }

  function sideColor(ctx, side) {
    return side === "b" ? ctx.color.b : ctx.color.a;
  }

  function note(ctx, kind, kids) {
    return ctx.h("div", { class: "sc-note " + kind }, kids);
  }

  function tag(ctx, cls, text) {
    return ctx.h("span", { class: cls ? "tag " + cls : "tag", text: text });
  }

  function head(ctx, text) {
    return ctx.h("div", { class: "sc-h", text: text });
  }

  function caveat(ctx, text) {
    return ctx.h("div", { class: "caveat", text: text });
  }

  function pct(ctx, value, places) {
    return isNum(value) ? ctx.fmt.pct(value, places === undefined ? 1 : places) : "—";
  }

  /* A value that could not be computed renders as its reason.
   *
   * The engine returns None with a `reason` precisely so that nobody has to
   * guess whether a blank cell means zero, means missing, or means nobody
   * looked. Reproducing that distinction is the whole job of this helper. */
  function valueOrReason(ctx, value, reason, render) {
    if (isNum(value)) {
      return ctx.h("span", { text: (render || function (v) { return pct(ctx, v); })(value) });
    }
    return ctx.h("span", {
      class: "sc-sub",
      text: "not computed — " + (str(reason) || "no reason given"),
    });
  }

  function bar(ctx, label, fraction, valueText, fill, isZero) {
    var width = Math.max(0, Math.min(1, isNum(fraction) ? fraction : 0)) * 100;
    return ctx.h("div", { class: "sc-bar" + (isZero ? " zero" : "") }, [
      ctx.h("div", { class: "sc-bar-l", text: label }),
      ctx.h("div", { class: "sc-bar-t" }, [
        ctx.h("i", { style: { width: width.toFixed(1) + "%", background: fill } }),
      ]),
      ctx.h("div", { class: "sc-bar-v", text: valueText }),
    ]);
  }

  function svgText(ctx, x, y, text, fill, size, anchor, weight) {
    return ctx.svg("text", {
      x: x, y: y, fill: fill, "font-size": size || 9,
      "text-anchor": anchor || "start",
      "font-family": "var(--sans)",
      "font-weight": weight || null,
    }, [text]);
  }

  /* An SVG that fills its card but stops growing — same reasoning as the
   * other modules: these cards live in a 1-to-5 column layout, so an
   * uncapped viewBox would render the axis labels at headline size in a
   * one-column reading. */
  function chart(ctx, width, height) {
    return ctx.svg("svg", {
      viewBox: "0 0 " + width + " " + height,
      preserveAspectRatio: "xMinYMin meet",
      style: "width:100%;max-width:" + Math.round(width * 1.5) +
             "px;height:auto;display:block;overflow:visible",
      role: "img",
    });
  }

  function scale(lo, hi, from, to) {
    var span = hi - lo;
    if (!span) span = 1;
    return function (value) { return from + ((value - lo) / span) * (to - from); };
  }

  function curveAt(curve, k) {
    var rows = arr(curve);
    for (var i = 0; i < rows.length; i++) {
      var row = obj(rows[i]);
      if (row && row.k === k) return isNum(row.value) ? row.value : null;
    }
    return null;
  }

  function points(delta) {
    return (Math.abs(delta) * 100).toFixed(1) + " points";
  }

  // ============================================================ 1. pass^k

  /* The two curves, on one pair of axes.
   *
   * They share a unit (a probability over the same k) so they share an axis;
   * what separates them is the question they answer, which is carried by line
   * style, by the endpoint labels, and by the shaded band between them. The
   * band is the point of the chart: it is the distance between "this agent
   * can do the task" and "this agent can be relied on to do the task".
   */
  function passCurveChart(ctx, entry) {
    var row = entry.row;
    var hat = obj(row.pass_hat_k) || {};
    var cov = obj(row.pass_at_k) || {};
    var maxK = isNum(row.max_k) ? row.max_k : 0;
    var color = ctx.color;
    var accent = sideColor(ctx, entry.side);

    var W = 372, H = 140;
    // Right padding holds the direct end labels ("pass@3 88%"), which are
    // drawn outside the plot area — too little and they clip at the card edge.
    var pad = { l: 30, r: 74, t: 11, b: 24 };
    var x = scale(1, Math.max(2, maxK), pad.l, W - pad.r);
    if (maxK <= 1) x = function () { return pad.l + (W - pad.r - pad.l) / 2; };
    var y = scale(0, 1, H - pad.b, pad.t);
    var plot = chart(ctx, W, H);

    [0, 0.25, 0.5, 0.75, 1].forEach(function (value) {
      plot.appendChild(ctx.svg("line", {
        x1: pad.l, x2: W - pad.r, y1: y(value), y2: y(value),
        stroke: color.grid, "stroke-width": 1,
      }));
      if (value === 0 || value === 0.5 || value === 1) {
        plot.appendChild(svgText(ctx, pad.l - 5, y(value) + 3,
          ctx.fmt.pct(value, 0), color.muted, 8.5, "end"));
      }
    });

    var both = [];
    for (var k = 1; k <= maxK; k++) {
      var h = curveAt(hat.curve, k);
      var c = curveAt(cov.curve, k);
      plot.appendChild(svgText(ctx, x(k), H - pad.b + 12, String(k), color.muted, 8.5, "middle"));
      if (h !== null && c !== null) both.push({ k: k, hat: h, cov: c });
    }
    plot.appendChild(svgText(ctx, (pad.l + W - pad.r) / 2, H - 3,
      "k — runs asked for", color.muted, 8.5, "middle"));

    // The gap, as an area. Drawn first so the lines sit on top of it.
    if (both.length >= 2) {
      var top = both.map(function (p) { return x(p.k) + "," + y(p.cov); });
      var bottom = both.slice().reverse().map(function (p) { return x(p.k) + "," + y(p.hat); });
      plot.appendChild(ctx.svg("polygon", {
        points: top.concat(bottom).join(" "),
        fill: color.warn, "fill-opacity": 0.22, stroke: "none",
      }));
    }

    function draw(key, stroke, dash) {
      var run = [];
      for (var i = 0; i < both.length; i++) {
        run.push(x(both[i].k) + "," + y(both[i][key]));
      }
      if (run.length > 1) {
        plot.appendChild(ctx.svg("polyline", {
          points: run.join(" "), fill: "none", stroke: stroke,
          "stroke-width": key === "hat" ? 2.2 : 1.8,
          "stroke-dasharray": dash, "stroke-linejoin": "round",
          "stroke-linecap": "round",
        }));
      }
      both.forEach(function (p) {
        plot.appendChild(ctx.svg("circle", {
          cx: x(p.k), cy: y(p[key]), r: key === "hat" ? 3.1 : 2.8,
          fill: key === "hat" ? stroke : color.surface,
          stroke: stroke, "stroke-width": key === "hat" ? 1.6 : 1.6,
        }));
      });
    }
    draw("cov", color.ink, "4 3");
    draw("hat", accent, null);

    // Direct labels at the right-hand end: with two series and an endpoint
    // apiece, a legend alone would make the reader count dashes.
    if (both.length) {
      var last = both[both.length - 1];
      var lx = x(last.k) + 6;
      var hy = y(last.hat);
      var cy = y(last.cov);
      if (Math.abs(hy - cy) < 9) { hy += 4; cy -= 4; }
      plot.appendChild(svgText(ctx, lx, cy + 3,
        "pass@" + last.k + " " + ctx.fmt.pct(last.cov, 0), color.ink, 8.5, "start", 600));
      plot.appendChild(svgText(ctx, lx, hy + 3,
        "pass^" + last.k + " " + ctx.fmt.pct(last.hat, 0), accent, 8.5, "start", 600));

      var gap = last.cov - last.hat;
      // Only annotate the band in place when there is room to read the label
      // inside it; below that the label sits on the lines and the sentence
      // under the chart carries the number instead.
      if (gap >= 0.02 && Math.abs(y(last.hat) - y(last.cov)) >= 17) {
        var mid = (y(last.cov) + y(last.hat)) / 2;
        plot.appendChild(ctx.svg("line", {
          x1: x(last.k) - 9, x2: x(last.k) - 9, y1: y(last.cov), y2: y(last.hat),
          stroke: color.warn, "stroke-width": 1.4,
        }));
        plot.appendChild(svgText(ctx, x(last.k) - 12, mid + 3,
          "gap " + (gap * 100).toFixed(0), color.warn, 8.5, "end", 600));
      }
    }
    return plot;
  }

  AgentDiff.block({
    id: "passk",
    title: "pass^k — reliability curve",
    question: "Does it work every time, or just once?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var rel = reliabilityOf(ctx);
      if (!rel) return 0;
      var list = sides(rel);
      if (!list.length) return 0;
      var maxK = 0, gap = 0;
      list.forEach(function (entry) {
        var k = isNum(entry.row.max_k) ? entry.row.max_k : 0;
        if (k > maxK) maxK = k;
        var h = obj(entry.row.pass_hat_k), c = obj(entry.row.pass_at_k);
        if (h && c && isNum(h.value_at_max_k) && isNum(c.value_at_max_k)) {
          gap = Math.max(gap, c.value_at_max_k - h.value_at_max_k);
        }
      });
      if (maxK <= 0) return 0.3;
      if (maxK === 1) return 0.5;
      return gap >= 0.02 ? 0.97 : 0.82;
    },

    render: function (el, ctx) {
      ensureStyle();
      var rel = reliabilityOf(ctx);
      if (!rel) {
        return ctx.empty(el, "No reliability analysis in this report — pass^k needs " +
          "repeated runs of the same task, which only `deepcompare runs` produces.");
      }
      var list = sides(rel);
      if (!list.length) return ctx.empty(el, "The reliability block carries no agents.");

      el.appendChild(note(ctx, "info", [
        ctx.h("strong", { text: "Two questions, one axis. " }),
        ctx.h("span", {
          text: "pass@k rises with k and asks can it ever — the chance at least one " +
            "of k runs passes. pass^k falls with k and asks can it every time — the " +
            "chance all k pass. The shaded gap between them is the part of the " +
            "success rate that does not survive being asked twice.",
        }),
      ]));

      list.forEach(function (entry) {
        var row = entry.row;
        var maxK = isNum(row.max_k) ? row.max_k : 0;
        var hat = obj(row.pass_hat_k) || {};
        var cov = obj(row.pass_at_k) || {};
        var accent = sideColor(ctx, entry.side);
        var panel = ctx.h("div", { class: "sc-panel" });

        panel.appendChild(ctx.h("div", { class: "sc-agent" }, [
          ctx.h("b", { text: entry.name }),
          tag(ctx, entry.side, entry.side.toUpperCase()),
          ctx.h("span", {
            class: "sc-sub",
            text: (isNum(row.successes) ? row.successes : "—") + " of " +
              (isNum(row.runs_used) ? row.runs_used : "—") + " eligible run(s) passed" +
              (isNum(row.mean_success_rate)
                ? " (" + ctx.fmt.pct(row.mean_success_rate, 1) + ")" : "") +
              " across " + (isNum(row.tasks_scored) ? row.tasks_scored : "—") + " task(s)",
          }),
        ]));

        if (!maxK || str(hat.reason)) {
          panel.appendChild(note(ctx, "warn", [
            ctx.h("strong", { text: "No curve for this agent. " }),
            ctx.h("span", { text: str(hat.reason) || "max_k is 0, so no k can be reported." }),
          ]));
          el.appendChild(panel);
          return;
        }

        panel.appendChild(ctx.h("div", { class: "scroll-x" }, [passCurveChart(ctx, entry)]));

        panel.appendChild(ctx.h("div", { class: "sc-legend" }, [
          ctx.h("span", null, [
            ctx.h("i", { class: "sc-dot", style: { borderTopColor: accent } }),
            "pass^k — all k runs pass (reliability)",
          ]),
          ctx.h("span", null, [
            ctx.h("i", {
              class: "sc-dot",
              style: { borderTopColor: ctx.color.ink, borderTopStyle: "dashed" },
            }),
            "pass@k — at least one of k passes (coverage)",
          ]),
          ctx.h("span", null, [
            ctx.h("i", {
              class: "sc-swatch",
              style: { background: ctx.color.warn, opacity: "0.45" },
            }),
            "the gap",
          ]),
        ]));

        var h = isNum(hat.value_at_max_k) ? hat.value_at_max_k : null;
        var c = isNum(cov.value_at_max_k) ? cov.value_at_max_k : null;
        if (h !== null && c !== null && maxK >= 2) {
          var gap = c - h;
          panel.appendChild(ctx.h("div", { class: "sc-line", style: { marginTop: "7px" } }, [
            gap >= 0.005
              ? ctx.h("span", {
                  text: "Asked to do it " + maxK + " times running it holds up " +
                    ctx.fmt.pct(h, 0) + " of the time; its chance of getting there at " +
                    "least once in " + maxK + " tries is " + ctx.fmt.pct(c, 0) + ". That " +
                    points(gap) + " is the distance between “can it ever” and “can it " +
                    "every time”.",
                })
              : ctx.h("span", {
                  text: "The two curves agree at k=" + maxK + " (" + ctx.fmt.pct(h, 0) +
                    "): on this suite, whatever this agent can do at all it does every " +
                    "time. The gap is a finding when it opens, and its absence is a " +
                    "finding too.",
                }),
          ]));
        } else if (maxK === 1) {
          panel.appendChild(ctx.h("div", { class: "sc-line", style: { marginTop: "7px" } }, [
            ctx.h("span", {
              text: "With one eligible run per task the curve stops at k=1, where both " +
                "measures collapse to the success rate. Nothing here speaks to " +
                "repeatability.",
            }),
          ]));
        }

        var dl = ctx.h("dl", { class: "kv", style: { marginTop: "8px" } }, [
          ctx.h("dt", { text: "max_k" }),
          ctx.h("dd", { text: String(maxK) }),
          ctx.h("dt", { text: "basis" }),
          ctx.h("dd", { class: "sc-sub", text: str(row.max_k_basis) || "—" }),
          ctx.h("dt", { text: "pass^k" }),
          ctx.h("dd", { class: "sc-sub", text: str(hat.basis) + (str(hat.source) ? " · " + hat.source : "") }),
          ctx.h("dt", { text: "pass@k" }),
          ctx.h("dd", { class: "sc-sub", text: str(cov.basis) + (str(cov.source) ? " · " + cov.source : "") }),
        ]);
        panel.appendChild(dl);
        el.appendChild(panel);
      });

      var advisory = null;
      list.forEach(function (entry) {
        var a = obj(entry.row.runs_advisory);
        if (a && (advisory === null || (isNum(a.n_min) && isNum(advisory.n_min) && a.n_min < advisory.n_min))) {
          advisory = a;
        }
      });
      if (advisory && advisory.tier === "insufficient") {
        el.appendChild(caveat(ctx,
          "These curves are capped at k=" + (isNum(advisory.n_min) ? advisory.n_min : "?") +
          " and describe these runs only — at this run count they do not support " +
          "ranking one agent against the other. See “What the runs support”."));
      }
    },
  });

  // =================================================== 2. reliability-guards

  var TIER_STYLE = {
    "insufficient": { kind: "bad", word: "insufficient" },
    "below-floor": { kind: "warn", word: "below floor" },
    "structured-ok": { kind: "good", word: "structured-ok" },
    "open-ended-ok": { kind: "good", word: "open-ended-ok" },
    "none": { kind: "bad", word: "no eligible runs" },
  };

  AgentDiff.block({
    id: "reliability-guards",
    title: "What the runs support",
    question: "What was excluded, and does this even support a claim?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var rel = reliabilityOf(ctx);
      if (!rel) return 0;
      var list = sides(rel);
      if (!list.length) return 0;
      var loud = false;
      list.forEach(function (entry) {
        var a = obj(entry.row.runs_advisory) || {};
        var ex = obj(entry.row.excluded_runs) || {};
        var un = obj(entry.row.unequal_trials) || {};
        if (a.tier === "insufficient" || a.tier === "none" || a.tier === "below-floor") loud = true;
        if (isNum(ex.count) && ex.count > 0) loud = true;
        if (un.flagged) loud = true;
      });
      return loud ? 1 : 0.75;
    },

    render: function (el, ctx) {
      ensureStyle();
      var rel = reliabilityOf(ctx);
      if (!rel) {
        return ctx.empty(el, "No reliability analysis in this report — the run-count " +
          "advisory and the harness-failure exclusions come with it.");
      }
      var list = sides(rel);
      if (!list.length) return ctx.empty(el, "The reliability block carries no agents.");

      /* The advisory goes first, above every figure it qualifies. Anything
       * else is a footnote under a number that has already been read. */
      list.forEach(function (entry) {
        var row = entry.row;
        var advisory = obj(row.runs_advisory) || {};
        var style = TIER_STYLE[advisory.tier] || { kind: "warn", word: str(advisory.tier) || "unknown" };

        var card = ctx.h("div", { class: "sc-verdict " + style.kind });
        card.appendChild(ctx.h("div", { class: "sc-verdict-head" }, [
          ctx.h("span", { class: "sc-verdict-lead", text: entry.name }),
          tag(ctx, style.kind, style.word),
          isNum(advisory.n_min)
            ? ctx.h("span", {
                class: "sc-sub",
                text: advisory.n_min + " run(s) at the thinnest of " +
                  (isNum(advisory.tasks) ? advisory.tasks : "—") + " task(s)",
              })
            : null,
        ]));
        card.appendChild(ctx.h("div", {
          class: "sc-verdict-body",
          text: str(advisory.message) || str(advisory.reason) ||
            "No advisory was produced for this agent.",
        }));

        var supports = arr(advisory.supports);
        var refuses = arr(advisory.does_not_support);
        if (supports.length || refuses.length) {
          var claims = ctx.h("ul", { class: "sc-claims" });
          supports.forEach(function (text) {
            claims.appendChild(ctx.h("li", { class: "yes" }, [
              ctx.h("i", { text: "✓" }), ctx.h("span", { text: "Supports " + text }),
            ]));
          });
          refuses.forEach(function (text) {
            claims.appendChild(ctx.h("li", { class: "no" }, [
              ctx.h("i", { text: "✗" }), ctx.h("span", { text: "Does not support " + text }),
            ]));
          });
          card.appendChild(claims);
        }

        var thresholds = obj(advisory.thresholds);
        if (thresholds) {
          card.appendChild(caveat(ctx,
            "Thresholds: no comparison below " + thresholds.no_comparison_below +
            " runs/task · " + thresholds.structured_floor + "–" +
            thresholds.structured_comfortable + " is the floor for structured tool-use · " +
            thresholds.open_ended_floor + "+ for open-ended reasoning."));
        }
        el.appendChild(card);
      });

      // ---- what was removed before anything was computed -----------------
      el.appendChild(head(ctx, "Excluded before any statistic"));
      el.appendChild(ctx.h("div", {
        class: "sc-sub",
        text: "Counting a rate-limited run as an agent failure makes the agent look " +
          "worse and the harness look fine — which is backwards for whoever has to " +
          "fix it. Harness failures are removed first, and counted here so the " +
          "removal is visible rather than silent.",
      }));

      list.forEach(function (entry) {
        var ex = obj(entry.row.excluded_runs) || {};
        var count = isNum(ex.count) ? ex.count : 0;
        var of = isNum(ex.of_runs) ? ex.of_runs : null;
        var wrap = ctx.h("div", { class: "sc-panel" });

        wrap.appendChild(ctx.h("div", { class: "sc-agent" }, [
          ctx.h("b", { text: entry.name }),
          tag(ctx, count ? "warn" : "", count + " excluded" + (of !== null ? " of " + of : "")),
          count === 0
            ? ctx.h("span", { class: "sc-sub", text: "checked, none found — not unchecked" })
            : null,
        ]));

        var byTermination = obj(ex.by_termination) || {};
        var reasons = Object.keys(byTermination).sort();
        if (reasons.length) {
          var chips = ctx.h("div", { class: "sc-chips" });
          reasons.forEach(function (reason) {
            chips.appendChild(tag(ctx, "bad", reason + " ×" + byTermination[reason]));
          });
          wrap.appendChild(chips);
        }

        var rows = arr(ex.runs);
        if (rows.length) {
          var table = ctx.h("table", { class: "grid" }, [
            ctx.h("tr", null, [
              ctx.h("th", { text: "Task" }),
              ctx.h("th", { text: "Run" }),
              ctx.h("th", { text: "Termination" }),
            ]),
          ]);
          rows.forEach(function (item) {
            var r = obj(item) || {};
            table.appendChild(ctx.h("tr", null, [
              ctx.h("td", { class: "mono", text: str(r.task) || "—" }),
              ctx.h("td", { class: "mono", text: str(r.run_id) || "—" }),
              ctx.h("td", null, [tag(ctx, "bad", str(r.termination) || "unknown")]),
            ]));
          });
          wrap.appendChild(ctx.h("div", {
            class: "scroll-x", style: { marginTop: "6px" },
          }, [table]));
        }

        var empties = arr(ex.tasks_left_empty);
        if (empties.length) {
          wrap.appendChild(note(ctx, "bad", [
            ctx.h("strong", { text: empties.length + " task(s) left with no eligible run: " }),
            ctx.h("span", { class: "mono", text: empties.join(", ") }),
            ctx.h("span", { text: " — these tasks contribute nothing to any figure above." }),
          ]));
        }
        if (str(ex.basis)) wrap.appendChild(caveat(ctx, ex.basis));

        // ---- unequal trials ---------------------------------------------
        var un = obj(entry.row.unequal_trials) || {};
        wrap.appendChild(ctx.h("div", { class: "sc-chips", style: { marginTop: "7px" } }, [
          tag(ctx, un.flagged ? "warn" : "good",
            un.flagged ? "unequal trials" : "equal trials"),
          ctx.h("span", {
            class: "sc-sub",
            text: (isNum(un.min) && isNum(un.max)
              ? un.min + "–" + un.max + " eligible runs per task. " : "") + (str(un.note) || ""),
          }),
        ]));
        el.appendChild(wrap);
      });
    },
  });

  // ============================================================ 3. consistency

  function consistencyRow(ctx, label, block, extra) {
    var b = obj(block) || {};
    var wrap = ctx.h("div", { style: { marginTop: "8px" } });
    wrap.appendChild(ctx.h("div", { class: "sc-line" }, [
      ctx.h("strong", { text: label + ": " }),
      valueOrReason(ctx, b.value, b.reason, function (v) { return ctx.fmt.num(v, 3); }),
      isNum(b.value) && isNum(b.tasks_scored)
        ? ctx.h("span", {
            class: "sc-sub",
            text: "  over " + b.tasks_scored + " of " +
              (isNum(b.of_tasks) ? b.of_tasks : "—") + " task(s)",
          })
        : null,
    ]));
    if (isNum(b.value)) {
      wrap.appendChild(ctx.h("div", { class: "sc-bar-t sc-meter", style: { marginTop: "4px" } }, [
        ctx.h("i", {
          style: {
            width: (Math.max(0, Math.min(1, b.value)) * 100).toFixed(1) + "%",
            background: ctx.color.a,
          },
        }),
      ]));
    }
    if (extra) wrap.appendChild(extra);
    if (str(b.basis)) wrap.appendChild(caveat(ctx, "Basis: " + b.basis));
    if (str(b.source)) wrap.appendChild(caveat(ctx, "Source: " + b.source));
    return wrap;
  }

  var RESOURCES = ["tokens", "cost_usd", "latency_s", "steps"];

  AgentDiff.block({
    id: "consistency",
    title: "Consistency & ICC",
    question: "Is the variance the task or the agent?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var rel = reliabilityOf(ctx);
      if (!rel) return 0;
      var list = sides(rel);
      if (!list.length) return 0;
      var scored = 0;
      list.forEach(function (entry) {
        ["outcome_consistency", "trajectory_consistency", "resource_consistency"].forEach(function (key) {
          var b = obj(entry.row[key]);
          if (b && isNum(b.value)) scored++;
        });
        var icc = obj(entry.row.icc);
        if (icc && isNum(icc.icc1)) scored++;
      });
      if (!scored) return 0.35;
      return 0.88;
    },

    render: function (el, ctx) {
      ensureStyle();
      var rel = reliabilityOf(ctx);
      if (!rel) {
        return ctx.empty(el, "No reliability analysis in this report — consistency " +
          "and ICC are measured across repeats of the same task.");
      }
      var list = sides(rel);
      if (!list.length) return ctx.empty(el, "The reliability block carries no agents.");

      el.appendChild(note(ctx, "info", [
        ctx.h("strong", { text: "Agreement, not quality. " }),
        ctx.h("span", {
          text: "A task the agent reliably fails scores 1.0 for outcome consistency. " +
            "These numbers measure determinism, and are read next to the success rate, " +
            "never instead of it.",
        }),
      ]));

      list.forEach(function (entry) {
        var row = entry.row;
        var panel = ctx.h("div", { class: "sc-panel" });
        panel.appendChild(ctx.h("div", { class: "sc-agent" }, [
          ctx.h("b", { text: entry.name }),
          tag(ctx, entry.side, entry.side.toUpperCase()),
        ]));

        panel.appendChild(consistencyRow(ctx, "Outcome", row.outcome_consistency));
        panel.appendChild(consistencyRow(ctx, "Trajectory", row.trajectory_consistency));

        // Resources: name what was dropped, not just what was scored.
        var res = obj(row.resource_consistency) || {};
        var byResource = obj(res.by_resource) || {};
        var logged = Object.keys(byResource).sort();
        var unlogged = RESOURCES.filter(function (name) { return logged.indexOf(name) < 0; });
        var detail = ctx.h("div", { style: { marginTop: "5px" } });
        if (logged.length) {
          var bars = ctx.h("div", { class: "sc-bars" });
          logged.forEach(function (name) {
            bars.appendChild(bar(ctx, name,
              byResource[name], ctx.fmt.num(byResource[name], 3), ctx.color.a));
          });
          detail.appendChild(bars);
        }
        detail.appendChild(caveat(ctx, unlogged.length
          ? "Dropped as unlogged: " + unlogged.join(", ") + ". An all-zero column is " +
            "the harness not recording the resource; scoring it exp(-0) = 1.0 would " +
            "credit the agent with perfect stability for having no data."
          : "All four resources (tokens, cost, latency, steps) are logged and scored."));
        panel.appendChild(consistencyRow(ctx, "Resource", res, detail));

        // ---- ICC ---------------------------------------------------------
        var icc = obj(row.icc) || {};
        panel.appendChild(head(ctx, "ICC(1) — whose variance is it?"));
        if (!isNum(icc.icc1)) {
          panel.appendChild(note(ctx, "warn", [
            ctx.h("strong", { text: "ICC could not be estimated. " }),
            ctx.h("span", { text: str(icc.reason) || "no reason given" }),
          ]));
        } else {
          var within = isNum(icc.within_task_variance_share) ? icc.within_task_variance_share : null;
          var between = isNum(icc.between_task_variance_share) ? icc.between_task_variance_share : null;
          panel.appendChild(ctx.h("div", { class: "sc-hero" }, [
            ctx.h("div", null, [
              ctx.h("div", { class: "sc-hero-n", text: ctx.fmt.num(icc.icc1_clamped, 2) }),
              ctx.h("div", { class: "sc-hero-c", text: "ICC(1) over " +
                (isNum(icc.observations) ? icc.observations : "—") + " run outcomes on " +
                (isNum(icc.tasks) ? icc.tasks : "—") + " task(s)" }),
            ]),
            within !== null
              ? ctx.h("div", { class: "sc-hero-c", style: { maxWidth: "44ch" } }, [
                  ctx.h("span", {
                    text: ctx.fmt.pct(within, 0) + " of the variance is this agent " +
                      "disagreeing with itself on the same task; " +
                      ctx.fmt.pct(between, 0) + " is tasks differing in difficulty. ",
                  }),
                  ctx.h("span", {
                    class: within >= 0.5 ? "sc-flag" : "",
                    text: within >= 0.5
                      ? "Most of what you are looking at is the agent's own inconsistency, not task difficulty."
                      : "The tasks, not the agent's inconsistency, account for most of the spread.",
                  }),
                ])
              : null,
          ]));
          if (between !== null) {
            panel.appendChild(ctx.h("div", { class: "sc-bars" }, [
              bar(ctx, "tasks differ", between, ctx.fmt.pct(between, 0), ctx.color.a),
              bar(ctx, "agent differs from itself", within, ctx.fmt.pct(within, 0), ctx.color.warn),
            ]));
          }
          if (icc.negative_raw) {
            panel.appendChild(note(ctx, "bad", [
              ctx.h("strong", { text: "Raw estimate is negative (" + ctx.fmt.num(icc.icc1, 3) + "). " }),
              ctx.h("span", {
                text: "Within-task variance exceeds between-task variance — the model " +
                  "cannot represent that as a correlation, so the usable value is " +
                  "clamped to " + ctx.fmt.num(icc.icc1_clamped, 2) + ". The raw sign is " +
                  "shown rather than floored because “more variable within a task than " +
                  "across tasks” is the most alarming thing this metric can say.",
              }),
            ]));
          }
          if (str(icc.caveat)) panel.appendChild(caveat(ctx, "Caveat: " + icc.caveat));
          if (isNum(icc.k0)) {
            panel.appendChild(caveat(ctx,
              "One-way random-effects ANOVA, unbalanced-design correction k0 = " +
              ctx.fmt.num(icc.k0, 2) + "."));
          }
        }
        el.appendChild(panel);
      });
    },
  });

  // ==================================================== 4. per-task-reliability

  function taskCurve(rows, k) {
    for (var i = 0; i < arr(rows).length; i++) {
      var r = obj(rows[i]);
      if (r && r.k === k) return isNum(r.value) ? r.value : null;
    }
    return null;
  }

  /* The smallest k at which a task that *does* sometimes pass never passes k
   * times running. This is the number the success rate hides. */
  function collapseK(task) {
    var rate = isNum(task.success_rate) ? task.success_rate : 0;
    if (rate <= 0) return null;
    var curve = arr(task.pass_hat_k);
    for (var i = 0; i < curve.length; i++) {
      var row = obj(curve[i]);
      if (row && isNum(row.value) && row.value === 0) return row.k;
    }
    return null;
  }

  function taskRank(task, maxK) {
    var last = taskCurve(task.pass_hat_k, maxK);
    return [
      isNum(last) ? last : -1,
      isNum(task.success_rate) ? task.success_rate : -1,
    ];
  }

  AgentDiff.block({
    id: "per-task-reliability",
    title: "Flaky tasks",
    question: "Which tasks are flaky?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var rel = reliabilityOf(ctx);
      if (!rel) return 0;
      var list = sides(rel);
      var rows = 0, collapses = 0;
      list.forEach(function (entry) {
        arr(entry.row.per_task).forEach(function (task) {
          var t = obj(task);
          if (!t) return;
          rows++;
          if (collapseK(t) !== null) collapses++;
        });
      });
      if (!rows) return 0;
      return collapses ? 0.96 : 0.7;
    },

    render: function (el, ctx) {
      ensureStyle();
      var rel = reliabilityOf(ctx);
      if (!rel) {
        return ctx.empty(el, "No reliability analysis in this report — per-task " +
          "flakiness needs repeated runs of each task.");
      }
      var list = sides(rel);
      if (!list.length) return ctx.empty(el, "The reliability block carries no agents.");

      var anyRows = false;
      list.forEach(function (entry) { if (arr(entry.row.per_task).length) anyRows = true; });
      if (!anyRows) return ctx.empty(el, "No task carries a per-task reliability row.");

      // The whole argument for the metric, stated with the actual case that
      // makes it, if this report contains one.
      var example = null;
      list.forEach(function (entry) {
        var maxK = isNum(entry.row.max_k) ? entry.row.max_k : 0;
        arr(entry.row.per_task).forEach(function (task) {
          var t = obj(task);
          if (!t) return;
          var k = collapseK(t);
          if (k === null || example) return;
          example = { entry: entry, task: t, k: k, maxK: maxK };
        });
      });

      if (example) {
        var t = example.task;
        var atK = taskCurve(t.pass_at_k, example.k);
        el.appendChild(note(ctx, "bad", [
          ctx.h("strong", { text: "A success rate can be non-zero while pass^k is exactly 0. " }),
          ctx.h("span", {
            text: example.entry.name + " on " + str(t.task) + " passed " +
              t.successes + " of " + t.runs + " runs — a success rate of " +
              pct(ctx, t.success_rate, 1) + ". Its pass^" + example.k + " is 0" +
              (isNum(atK) ? ", against a pass@" + example.k + " of " + ctx.fmt.pct(atK, 0) : "") +
              ". It has never once demonstrated it can do this task " + example.k +
              " times running, and the mean says nothing about that.",
          }),
        ]));
      } else {
        el.appendChild(note(ctx, "info", [
          ctx.h("strong", { text: "No task collapses. " }),
          ctx.h("span", {
            text: "Every task that passes at all passes every eligible run, so pass^k " +
              "never falls to 0 within the reported k. Sorted least reliable first.",
          }),
        ]));
      }

      list.forEach(function (entry) {
        var row = entry.row;
        var maxK = isNum(row.max_k) ? row.max_k : 0;
        var tasks = arr(row.per_task).map(obj).filter(Boolean).slice();
        if (!tasks.length) return;
        tasks.sort(function (x, y) {
          var rx = taskRank(x, maxK), ry = taskRank(y, maxK);
          if (rx[0] !== ry[0]) return rx[0] - ry[0];
          if (rx[1] !== ry[1]) return rx[1] - ry[1];
          return String(x.task).localeCompare(String(y.task));
        });

        var panel = ctx.h("div", { class: "sc-panel" });
        panel.appendChild(ctx.h("div", { class: "sc-agent" }, [
          ctx.h("b", { text: entry.name }),
          tag(ctx, entry.side, entry.side.toUpperCase()),
          ctx.h("span", { class: "sc-sub", text: "least reliable first" }),
        ]));

        var headRow = ctx.h("tr", null, [
          ctx.h("th", { text: "Task" }),
          ctx.h("th", { class: "num", text: "Runs" }),
          ctx.h("th", { class: "num", text: "Passed" }),
          ctx.h("th", { class: "num", text: "Rate" }),
        ]);
        for (var k = 1; k <= maxK; k++) {
          headRow.appendChild(ctx.h("th", { class: "num", text: "pass^" + k }));
        }
        headRow.appendChild(ctx.h("th", { text: "" }));
        var table = ctx.h("table", { class: "grid" }, [headRow]);

        tasks.forEach(function (task) {
          var collapse = collapseK(task);
          var cells = [
            ctx.h("td", { class: "mono", text: str(task.task) || "—" }),
            ctx.h("td", { class: "num sc-num", text: isNum(task.runs) ? String(task.runs) : "—" }),
            ctx.h("td", { class: "num sc-num", text: isNum(task.successes) ? String(task.successes) : "—" }),
            ctx.h("td", { class: "num sc-num", text: pct(ctx, task.success_rate, 1) }),
          ];
          for (var kk = 1; kk <= maxK; kk++) {
            var value = taskCurve(task.pass_hat_k, kk);
            cells.push(ctx.h("td", {
              class: "num sc-num" + (value === 0 && isNum(task.success_rate) && task.success_rate > 0 ? " " : ""),
            }, [
              value === null
                ? ctx.h("span", { class: "sc-sub", text: "—" })
                : ctx.h("span", {
                    class: value === 0 && isNum(task.success_rate) && task.success_rate > 0 ? "sc-flag" : "",
                    text: ctx.fmt.num(value, 2),
                  }),
            ]));
          }
          var flags = ctx.h("td");
          if (collapse !== null) {
            flags.appendChild(tag(ctx, "bad", "never " + collapse + "× running"));
          } else if (isNum(task.success_rate) && task.success_rate === 0) {
            flags.appendChild(tag(ctx, "bad", "never passes"));
          } else if (isNum(task.success_rate) && task.success_rate === 1) {
            flags.appendChild(tag(ctx, "good", "every run"));
          }
          if (isNum(task.runs_excluded) && task.runs_excluded > 0) {
            flags.appendChild(tag(ctx, "warn", task.runs_excluded + " excluded"));
          }
          var tc = obj(task.trajectory_consistency) || {};
          if (!isNum(tc.value) && str(tc.reason)) {
            flags.appendChild(ctx.h("div", {
              class: "sc-sub",
              text: "path consistency: " + tc.reason,
            }));
          }
          cells.push(flags);
          table.appendChild(ctx.h("tr", null, cells));
        });

        panel.appendChild(ctx.h("div", { class: "scroll-x" }, [table]));
        panel.appendChild(caveat(ctx,
          "pass^k is C(c,k)/C(n,k) for that task alone — the chance all k sampled runs " +
          "pass. Rate is c/n. A rate above 0 with pass^k at 0 means the task works and " +
          "does not keep working. Curves stop at k=" + maxK + ": " +
          (str(row.max_k_basis) || "the thinnest task's eligible trial count.")));
        el.appendChild(panel);
      });
    },
  });

  // ============================================================== 5. taxonomy

  var MAST_INTER = "Inter-Agent Misalignment";

  function distribution(ctx, counts, order, total, fill, zeroNote) {
    var names = arr(order).length ? arr(order) : Object.keys(obj(counts) || {});
    var wrap = ctx.h("div", { class: "sc-bars" });
    names.forEach(function (name) {
      var value = (obj(counts) || {})[name];
      var n = isNum(value) ? value : 0;
      wrap.appendChild(bar(ctx, name, total ? n / total : 0,
        n + (total ? " · " + ctx.fmt.pct(n / total, 0) : ""),
        fill, n === 0));
    });
    if (zeroNote) wrap.appendChild(zeroNote);
    return wrap;
  }

  AgentDiff.block({
    id: "taxonomy",
    title: "MAST & TRAIL labels",
    question: "What are these failures called in the literature?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var tax = taxonomyOf(ctx);
      if (!tax) return 0;
      var mast = obj(tax.mast) || {};
      var trail = obj(tax.trail) || {};
      var totals = obj(tax.signal_totals) || {};
      var labelled = 0;
      [obj(mast.by_category), obj(trail.by_branch)].forEach(function (counts) {
        Object.keys(counts || {}).forEach(function (key) {
          if (isNum(counts[key])) labelled += counts[key];
        });
      });
      if (!Object.keys(totals).length) return 0.35;
      return labelled ? 0.9 : 0.5;
    },

    render: function (el, ctx) {
      ensureStyle();
      var tax = taxonomyOf(ctx);
      if (!tax) {
        return ctx.empty(el, "No taxonomy mapping in this report — it is produced by " +
          "`deepcompare batch` over a comparison suite.");
      }
      var totals = obj(tax.signal_totals) || {};
      var mast = obj(tax.mast) || {};
      var trail = obj(tax.trail) || {};

      el.appendChild(note(ctx, "warn", [
        ctx.h("strong", { text: "Rule-based mappings, not taxonomy labels. " }),
        ctx.h("span", {
          text: "MAST's distribution came from six expert annotators (Cohen's κ = 0.88) " +
            "and a judge validated at 94% accuracy; TRAIL's from expert annotation. " +
            "Nothing below reads a trace for meaning — these are deterministic signals " +
            "translated into the papers' vocabulary. Comparable in vocabulary, not in " +
            "method, and never to be quoted as a MAST or TRAIL measurement.",
        }),
      ]));

      // ---- what fired at all --------------------------------------------
      el.appendChild(head(ctx, "AgentDiff signals over " +
        (isNum(tax.tasks) ? tax.tasks : "—") + " task(s)"));
      var names = Object.keys(totals).sort(function (x, y) {
        return (totals[y] || 0) - (totals[x] || 0) || x.localeCompare(y);
      });
      if (!names.length) {
        el.appendChild(ctx.h("div", {
          class: "sc-sub",
          text: "No divergence kind or process flag fired on this batch, so no label " +
            "is licensed below. That is an absence of signals, not a clean bill.",
        }));
      } else {
        var chips = ctx.h("div", { class: "sc-chips" });
        names.forEach(function (name) {
          chips.appendChild(tag(ctx, "", name + " ×" + totals[name]));
        });
        el.appendChild(chips);
      }

      // ---- MAST ----------------------------------------------------------
      var byCategory = obj(mast.by_category) || {};
      var categoryOrder = Object.keys(byCategory);
      var mastTotal = 0;
      categoryOrder.forEach(function (name) {
        if (isNum(byCategory[name])) mastTotal += byCategory[name];
      });

      el.appendChild(head(ctx, "MAST — three categories (arXiv 2503.13657)"));
      el.appendChild(distribution(ctx, byCategory, categoryOrder, mastTotal, ctx.color.a));

      if (isNum(byCategory[MAST_INTER]) && byCategory[MAST_INTER] === 0) {
        el.appendChild(note(ctx, "info", [
          ctx.h("strong", { text: "Inter-Agent Misalignment is 0 by construction. " }),
          ctx.h("span", {
            text: "That zero is structural, not empirical: AgentDiff compares " +
              "single-agent trajectories, and all six modes in this category need two " +
              "or more agents exchanging messages. About 36.9% of MAST's measured " +
              "failure mass lives here and is invisible to this tool — read the zero " +
              "as “cannot see”, never as “did not happen”.",
          }),
        ]));
      }

      var byCode = obj(mast.by_code) || {};
      var codes = Object.keys(byCode).filter(function (code) { return byCode[code] > 0; });
      if (codes.length) {
        var mastTable = ctx.h("table", { class: "grid" }, [
          ctx.h("tr", null, [
            ctx.h("th", { text: "Mode" }),
            ctx.h("th", { class: "num", text: "Occurrences" }),
          ]),
        ]);
        codes.sort(function (x, y) { return byCode[y] - byCode[x] || x.localeCompare(y); });
        codes.forEach(function (code) {
          mastTable.appendChild(ctx.h("tr", null, [
            ctx.h("td", { class: "mono", text: code }),
            ctx.h("td", { class: "num sc-num", text: String(byCode[code]) }),
          ]));
        });
        el.appendChild(ctx.h("div", { class: "scroll-x", style: { marginTop: "6px" } }, [mastTable]));
        el.appendChild(caveat(ctx,
          "Modes with 0 occurrences are omitted from this table but present in the " +
          "data as explicit zeroes — “looked, found none”, except in category 2 where " +
          "the zero means “could not look”."));
      }

      // ---- TRAIL ---------------------------------------------------------
      var byBranch = obj(trail.by_branch) || {};
      var branchOrder = Object.keys(byBranch);
      var trailTotal = 0;
      branchOrder.forEach(function (name) {
        if (isNum(byBranch[name])) trailTotal += byBranch[name];
      });

      el.appendChild(head(ctx, "TRAIL — three branches (arXiv 2505.08638)"));
      el.appendChild(distribution(ctx, byBranch, branchOrder, trailTotal, ctx.color.b));

      var trailCodes = obj(trail.by_code) || {};
      var leaves = Object.keys(trailCodes).filter(function (code) { return trailCodes[code] > 0; });
      if (leaves.length) {
        leaves.sort(function (x, y) { return trailCodes[y] - trailCodes[x] || x.localeCompare(y); });
        var trailTable = ctx.h("table", { class: "grid" }, [
          ctx.h("tr", null, [
            ctx.h("th", { text: "Leaf" }),
            ctx.h("th", { class: "num", text: "Occurrences" }),
          ]),
        ]);
        leaves.forEach(function (code) {
          trailTable.appendChild(ctx.h("tr", null, [
            ctx.h("td", { class: "mono", text: code }),
            ctx.h("td", { class: "num sc-num", text: String(trailCodes[code]) }),
          ]));
        });
        el.appendChild(ctx.h("div", { class: "scroll-x", style: { marginTop: "6px" } }, [trailTable]));
      }

      el.appendChild(caveat(ctx,
        "Both taxonomies are reported because neither is a superset of the other: " +
        "MAST has no system-execution category, TRAIL has no verification category, " +
        "and each is blind to roughly a fifth of the failure mass the other captures. " +
        "Quoting one alone quotes an incomplete picture."));
    },
  });

  // ==================================================== 6. taxonomy-coverage

  var BLOCKER_ORDER = ["multi_agent", "judge", "label_vocabulary", "outside_trace"];

  AgentDiff.block({
    id: "taxonomy-coverage",
    title: "Coverage & blind spots",
    question: "What can this tool even see?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var tax = taxonomyOf(ctx);
      if (!tax) return 0;
      var coverage = obj(tax.coverage);
      if (!coverage) return 0;
      return obj(coverage.mast) || obj(coverage.trail) ? 0.86 : 0.3;
    },

    render: function (el, ctx) {
      ensureStyle();
      var tax = taxonomyOf(ctx);
      if (!tax) {
        return ctx.empty(el, "No taxonomy mapping in this report, so there is no " +
          "coverage claim to audit.");
      }
      var coverage = obj(tax.coverage);
      if (!coverage) {
        return ctx.empty(el, "This taxonomy block carries no coverage section.");
      }
      var mast = obj(coverage.mast) || {};
      var trail = obj(coverage.trail) || {};

      // ---- the honest headline -------------------------------------------
      var massReach = isNum(mast.category_mass_weighted_reach) ? mast.category_mass_weighted_reach : null;
      var countReach = isNum(mast.fraction) ? mast.fraction : null;

      el.appendChild(ctx.h("div", { class: "sc-hero" }, [
        ctx.h("div", null, [
          ctx.h("div", {
            class: "sc-hero-n",
            text: massReach === null ? "—" : ctx.fmt.pct(massReach, 1),
          }),
          ctx.h("div", {
            class: "sc-hero-c",
            text: "of MAST's observed failure mass is reachable — the number that counts",
          }),
        ]),
        ctx.h("div", null, [
          ctx.h("div", {
            class: "sc-hero-n",
            style: { color: ctx.color.muted },
            text: countReach === null ? "—" : ctx.fmt.pct(countReach, 1),
          }),
          ctx.h("div", {
            class: "sc-hero-c",
            text: "of MAST's 14 modes by count — the flattering number",
          }),
        ]),
      ]));
      el.appendChild(note(ctx, "info", [
        ctx.h("strong", { text: "Reach is reported twice on purpose. " }),
        ctx.h("span", {
          text: str(mast.note) || ("Mode count flatters the tool. Weighted by MAST's " +
            "published category mass rather than by mode count, the reachable part " +
            "covers a different share of real failures."),
        }),
        ctx.h("span", {
          text: " Reach is counted per code: a mode is reachable when some AgentDiff " +
            "signal maps onto it with direct or partial confidence. A code named only " +
            "to record a refusal does not count as reached.",
        }),
      ]));

      // The engine's own summary, minus the caveat text it ends with — the
      // four caveats are rendered verbatim at the foot of this block, and
      // printing them twice trains the reader to skip them.
      var caveats = arr(coverage.caveats).length ? arr(coverage.caveats) : arr(tax.caveats);
      var summary = str(coverage.narrative);
      caveats.forEach(function (text) {
        if (str(text)) summary = summary.split(String(text)).join(" ");
      });
      summary = summary.replace(/\s+/g, " ").trim();
      if (summary) el.appendChild(ctx.h("div", { class: "sc-line" }, [summary]));

      // ---- reach per taxonomy --------------------------------------------
      el.appendChild(head(ctx, "MAST — " + (isNum(mast.reachable) ? mast.reachable : "—") +
        " of " + (isNum(mast.total) ? mast.total : "—") + " modes reachable"));
      var byCategory = obj(mast.by_category) || {};
      var catBars = ctx.h("div", { class: "sc-bars" });
      Object.keys(byCategory).forEach(function (name) {
        var bucket = obj(byCategory[name]) || {};
        var fraction = isNum(bucket.fraction) ? bucket.fraction : 0;
        catBars.appendChild(bar(ctx, name, fraction,
          (isNum(bucket.reachable) ? bucket.reachable : "—") + "/" +
          (isNum(bucket.total) ? bucket.total : "—") +
          (isNum(bucket.published_mass)
            ? " · " + ctx.fmt.pct(bucket.published_mass, 1) + " of mass" : ""),
          fraction === 0 ? ctx.color.bad : ctx.color.a, fraction === 0));
      });
      el.appendChild(catBars);
      el.appendChild(caveat(ctx,
        "The right-hand figure is MAST's published share of observed failures for that " +
        "category, not a share of this batch. A category at 0/n with a large published " +
        "mass is the expensive kind of blind spot."));

      el.appendChild(head(ctx, "TRAIL — " + (isNum(trail.reachable) ? trail.reachable : "—") +
        " of " + (isNum(trail.total) ? trail.total : "—") + " leaves reachable"));
      var byBranch = obj(trail.by_branch) || {};
      var branchBars = ctx.h("div", { class: "sc-bars" });
      Object.keys(byBranch).forEach(function (name) {
        var bucket = obj(byBranch[name]) || {};
        var fraction = isNum(bucket.fraction) ? bucket.fraction : 0;
        branchBars.appendChild(bar(ctx, name, fraction,
          (isNum(bucket.reachable) ? bucket.reachable : "—") + "/" +
          (isNum(bucket.total) ? bucket.total : "—"),
          fraction === 0 ? ctx.color.bad : ctx.color.b, fraction === 0));
      });
      el.appendChild(branchBars);

      // ---- what blocks each unreachable mode ------------------------------
      var blockers = obj(coverage.blockers) || {};
      var counts = obj(coverage.blocker_counts) || {};
      var unreachable = arr(mast.unreachable).map(function (row) {
        var r = obj(row) || {};
        return {
          taxonomy: "MAST", code: str(r.code), name: str(r.name),
          where: str(r.category), blocker: str(r.blocker), reason: str(r.reason),
        };
      }).concat(arr(trail.unreachable).map(function (row) {
        var r = obj(row) || {};
        return {
          taxonomy: "TRAIL", code: str(r.code), name: str(r.name),
          where: str(r.branch), blocker: str(r.blocker), reason: str(r.reason),
        };
      }));

      if (unreachable.length) {
        el.appendChild(head(ctx, unreachable.length + " unreachable mode(s), by what blocks them"));
        var chips = ctx.h("div", { class: "sc-chips" });
        BLOCKER_ORDER.concat(Object.keys(counts).filter(function (name) {
          return BLOCKER_ORDER.indexOf(name) < 0;
        })).forEach(function (name) {
          if (!isNum(counts[name])) return;
          chips.appendChild(tag(ctx,
            name === "multi_agent" || name === "judge" ? "bad" : "warn",
            name + " ×" + counts[name]));
        });
        el.appendChild(chips);
        el.appendChild(ctx.h("div", { class: "sc-sub", style: { marginTop: "5px" } }, [
          ctx.h("span", {
            text: "Two of these are fixable by richer traces (label_vocabulary, " +
              "outside_trace); two are not fixable without a judge or a second agent " +
              "(judge, multi_agent). Readers need to tell those apart, so the blocker " +
              "is named per mode rather than lumped into “unsupported”.",
          }),
        ]));

        // Defined once here rather than repeated down the table: five modes
        // share the multi_agent blocker, and five identical paragraphs read
        // as noise instead of as one structural limit.
        var glossary = ctx.h("dl", { class: "kv", style: { marginTop: "6px" } });
        BLOCKER_ORDER.concat(Object.keys(blockers).filter(function (name) {
          return BLOCKER_ORDER.indexOf(name) < 0;
        })).forEach(function (name) {
          if (!str(blockers[name])) return;
          glossary.appendChild(ctx.h("dt", null, [
            tag(ctx, name === "multi_agent" || name === "judge" ? "bad" : "warn", name),
          ]));
          glossary.appendChild(ctx.h("dd", { class: "sc-sub", text: blockers[name] }));
        });
        el.appendChild(glossary);

        var table = ctx.h("table", { class: "grid" }, [
          ctx.h("tr", null, [
            ctx.h("th", { text: "Taxonomy" }),
            ctx.h("th", { text: "Mode" }),
            ctx.h("th", { text: "Blocker" }),
            ctx.h("th", { text: "Why not" }),
          ]),
        ]);
        unreachable.sort(function (x, y) {
          var bx = BLOCKER_ORDER.indexOf(x.blocker), by = BLOCKER_ORDER.indexOf(y.blocker);
          if (bx !== by) return (bx < 0 ? 99 : bx) - (by < 0 ? 99 : by);
          if (x.taxonomy !== y.taxonomy) return x.taxonomy.localeCompare(y.taxonomy);
          return x.code.localeCompare(y.code);
        });
        unreachable.forEach(function (row) {
          table.appendChild(ctx.h("tr", null, [
            ctx.h("td", null, [tag(ctx, row.taxonomy === "MAST" ? "a" : "b", row.taxonomy)]),
            ctx.h("td", null, [
              ctx.h("div", { text: row.name || row.code }),
              ctx.h("div", { class: "sc-mono-b", text: row.code }),
              row.where ? ctx.h("div", { class: "sc-sub", text: row.where }) : null,
            ]),
            ctx.h("td", null, [
              tag(ctx, row.blocker === "multi_agent" || row.blocker === "judge" ? "bad" : "warn",
                row.blocker || "—"),
            ]),
            ctx.h("td", { class: "sc-sub", text: row.reason || "—" }),
          ]));
        });
        el.appendChild(ctx.h("div", { class: "scroll-x", style: { marginTop: "7px" } }, [table]));
      }

      // ---- the standing caveats, verbatim ---------------------------------
      if (caveats.length) {
        el.appendChild(head(ctx, "Standing caveats, verbatim"));
        caveats.forEach(function (text, index) {
          if (!str(text)) return;
          el.appendChild(note(ctx, index === 1 ? "warn" : "info", [
            ctx.h("span", { text: String(text) }),
          ]));
        });
      }
    },
  });
})(typeof window !== "undefined" ? window : this);
