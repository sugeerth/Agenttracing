/* AgentDiff blocks — variance attribution.
 *
 * Four blocks over `deepcompare.variance`, the module that asks where the
 * variation in an outcome actually comes from: the model underneath, the
 * harness around it, the task being hard, or the run simply coming out
 * differently this time. Those four answers call for four different
 * responses, so getting the attribution wrong is expensive.
 *
 * The engine is unusually careful about the two ways this goes wrong, and
 * these blocks exist to make sure the care survives contact with a chart:
 *
 *   · a factor's share is a *range* whenever the design cannot identify it,
 *     and the width of that range is variance the factors hold in common.
 *     Drawing the midpoint — or the maximum, or whichever end flatters the
 *     story — would turn an arbitrary attribution order into a finding, so
 *     an unidentified factor is never drawn as a point. The solid part of
 *     its bar is what it owns outright; the hatched tail is contested;
 *     the pooled contested share is a segment of its own in the headline
 *     bar, labelled as unattributable rather than silently handed out;
 *   · raw explained variance is not comparable across factors with
 *     different level counts. A 33-level harness explains 12% of a
 *     264-run corpus *by chance alone* before any real effect exists, so
 *     the corrected block draws that chance floor as a zone on the same
 *     axis as the raw range and the bias-corrected omega squared. Where
 *     omega squared is at or below zero the block prints "at chance" and
 *     draws nothing — a one-pixel positive bar would be a lie about sign;
 *   · the residual is only noise when there are repeats. With one run per
 *     cell it is interaction *and* stochasticity inseparably, so the
 *     residual block renders the two cases with different marks: a solid
 *     bar for measured run-to-run variation, an undivided hatched bar with
 *     a moving boundary for the case where the split is unknowable;
 *   · the design block runs first in reading order for a confounded corpus,
 *     because when model and harness are the same partition of the data
 *     every model-versus-harness number below it is an artefact.
 *
 * `variance.json` is written by `deepcompare variance`, which is a separate
 * command from the `batch`/`runs` that render this page. So the data is
 * usually absent, and absent means relevance 0 and hidden — not a chart of
 * something else reconstructed from the reports. This module never derives
 * a decomposition of its own.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style

  var STYLE_ID = "agentdiff-variance-css";
  var styled = false;

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".vz-note{border:1px solid var(--rule);border-left-width:3px;border-radius:7px;",
        "padding:7px 9px;font-size:12px;line-height:1.45;margin:0 0 10px;",
        "background:var(--surface-2);color:var(--ink-2)}",
        ".vz-note strong{color:var(--ink)}",
        ".vz-note.bad{border-left-color:var(--bad)}",
        ".vz-note.warn{border-left-color:var(--warn)}",
        ".vz-note.good{border-left-color:var(--good)}",
        ".vz-note.info{border-left-color:var(--rule-2)}",
        ".vz-lead{border:1px solid var(--rule-2);border-left-width:4px;border-radius:8px;",
        "padding:10px 11px;margin:0 0 11px;background:var(--surface-2)}",
        ".vz-lead.bad{border-left-color:var(--bad)}",
        ".vz-lead.warn{border-left-color:var(--warn)}",
        ".vz-lead.good{border-left-color:var(--good)}",
        ".vz-lead-top{display:flex;flex-wrap:wrap;gap:7px;align-items:baseline;margin-bottom:4px}",
        ".vz-lead-n{font-size:23px;font-weight:660;letter-spacing:-.02em;color:var(--ink);",
        "font-variant-numeric:tabular-nums;line-height:1.05}",
        ".vz-lead-t{font-size:12.5px;font-weight:600;color:var(--ink)}",
        ".vz-lead-b{font-size:12px;color:var(--ink-2);line-height:1.5}",
        ".vz-h{font-size:11px;text-transform:uppercase;letter-spacing:.06em;",
        "color:var(--ink-3);margin:13px 0 6px}",
        ".vz-h:first-child{margin-top:0}",
        ".vz-sub{font-size:11.5px;color:var(--ink-3);line-height:1.45}",
        ".vz-line{font-size:12.5px;color:var(--ink);line-height:1.5}",
        ".vz-metric{border-top:1px solid var(--rule);padding-top:11px;margin-top:13px}",
        ".vz-metric:first-child{border-top:none;padding-top:0;margin-top:0}",
        ".vz-metric-head{display:flex;flex-wrap:wrap;gap:7px;align-items:baseline;margin-bottom:6px}",
        ".vz-metric-head b{font-size:13px;color:var(--ink);letter-spacing:-.01em}",
        ".vz-chips{display:flex;flex-wrap:wrap;gap:5px 9px;align-items:center;",
        "margin:6px 0 0;font-size:11px;color:var(--ink-3)}",
        ".vz-chip{display:inline-flex;align-items:center;gap:4px}",
        ".vz-sw{width:11px;height:9px;border-radius:2px;display:inline-block;flex:0 0 auto;",
        "border:1px solid var(--rule-2)}",
        ".vz-tabs{display:flex;flex-wrap:wrap;gap:5px;margin:0 0 10px}",
        ".vz-tab{border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);",
        "border-radius:6px;padding:3px 9px;font-size:11.5px;cursor:pointer;font-family:inherit}",
        ".vz-tab:hover{background:var(--surface-2);color:var(--ink)}",
        ".vz-tab[aria-pressed=\"true\"]{background:var(--surface-2);color:var(--ink);",
        "border-color:var(--ink-3);font-weight:600}",
        ".vz-kv{margin:0}",
        ".vz-fix{border:1px dashed var(--rule-2);border-radius:7px;padding:8px 10px;",
        "margin:10px 0 0;font-size:12px;line-height:1.5;color:var(--ink-2);background:var(--surface-2)}",
        ".vz-fix b{color:var(--ink);display:block;font-size:11px;text-transform:uppercase;",
        "letter-spacing:.06em;margin-bottom:3px;color:var(--ink-3)}",
        ".vz-names{font-size:11px;line-height:1.5;color:var(--ink-2);word-break:break-word}",
        ".vz-svg-wrap{max-width:100%}",
      ].join("");
      (document.head || document.documentElement).appendChild(node);
    } catch (err) { /* a card without its stylesheet still reads */ }
  }

  // ------------------------------------------------------------------ utils

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function obj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : null; }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function str(v) { return typeof v === "string" && v ? v : ""; }
  function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }

  var FACTORS = ["task", "model", "harness", "version"];
  var METRIC_ORDER = ["success", "tokens", "latency_s", "cost_usd", "steps"];
  var METRIC_LABEL = {
    success: "success", tokens: "tokens", latency_s: "latency",
    cost_usd: "cost", steps: "steps",
  };

  /* The decomposition lives in the aggregate when the page was rendered
   * beside one, and on the raw payload when a test page was assembled by
   * hand. It is written by `deepcompare variance`, which batch and runs do
   * not invoke, so absent is the normal case and is handled as absence. */
  function payloadOf(ctx) {
    var fromAggregate = obj(ctx && ctx.aggregate && ctx.aggregate.variance);
    if (fromAggregate) return fromAggregate;
    var raw = obj(global.DEEPCOMPARE_DATA);
    return raw ? obj(raw.variance) : null;
  }

  function designOf(v) { return obj(v && v.design); }

  /* Metric blocks in the order a reader cares about them, unknown metrics
   * kept rather than dropped — a corpus decomposed on `steps` should not
   * render an empty card because this list did not anticipate it. */
  function metricsOf(v) {
    var metrics = obj(v && v.metrics);
    if (!metrics) return [];
    var keys = Object.keys(metrics).filter(function (k) { return !!obj(metrics[k]); });
    keys.sort(function (x, y) {
      var ix = METRIC_ORDER.indexOf(x), iy = METRIC_ORDER.indexOf(y);
      if (ix < 0) ix = METRIC_ORDER.length + 1;
      if (iy < 0) iy = METRIC_ORDER.length + 1;
      if (ix !== iy) return ix - iy;
      return x < y ? -1 : (x > y ? 1 : 0);
    });
    return keys.map(function (key) {
      return { key: key, label: METRIC_LABEL[key] || key, m: obj(metrics[key]) };
    });
  }

  /* A metric block is drawable when it carries components and a residual.
   * The engine returns neither when there was nothing to split — one run,
   * no varying factor, or a constant outcome — and states why in `reason`,
   * which is what gets rendered instead. */
  function usable(m) {
    if (!m) return false;
    var comps = obj(m.components);
    return !!comps && Object.keys(comps).length > 0 && isNum(m.residual);
  }

  function drawable(v) {
    return metricsOf(v).filter(function (entry) { return usable(entry.m); });
  }

  function correctedKey(c) {
    // Ordering is by corrected effect, because ordering by the raw share
    // would put the factor with the most levels first for having levels.
    return isNum(c.omega_squared_min) ? c.omega_squared_min : -1e6;
  }

  /* Components as a sorted list: strongest corrected effect first, factors
   * whose correction is undefined last, ties broken by raw reach so the
   * order is total and stable. */
  function componentsOf(m) {
    var comps = obj(m && m.components) || {};
    var rows = Object.keys(comps).filter(function (k) { return !!obj(comps[k]); })
      .map(function (name) { return { name: name, c: obj(comps[name]) }; });
    rows.sort(function (x, y) {
      var dk = correctedKey(y.c) - correctedKey(x.c);
      if (dk) return dk;
      var dm = (num(y.c.max_share) || 0) - (num(x.c.max_share) || 0);
      if (dm) return dm;
      return x.name < y.name ? -1 : (x.name > y.name ? 1 : 0);
    });
    return rows;
  }

  function num(v) { return isNum(v) ? v : null; }

  function isIdentified(c) {
    if (typeof c.identified === "boolean") return c.identified;
    var lo = num(c.min_share), hi = num(c.max_share);
    return isNum(lo) && isNum(hi) && (hi - lo) < 0.005;
  }

  /* "17%–20%" for a contested factor, "6%" for one the design identifies.
   * Never a midpoint: the midpoint of a range no method can split is a
   * number nothing in the data supports. */
  function rangeText(ctx, c) {
    var lo = num(c.min_share), hi = num(c.max_share);
    if (!isNum(lo) && !isNum(hi)) return "—";
    if (!isNum(hi) || isIdentified(c)) return ctx.fmt.pct(lo, 0);
    if (!isNum(lo)) return "≤ " + ctx.fmt.pct(hi, 0);
    return ctx.fmt.pct(lo, 0) + "–" + ctx.fmt.pct(hi, 0);
  }

  /* Omega squared at or below zero is the finding, not a rounding artefact:
   * the factor explained no more than its level count predicts. It is never
   * drawn as a short bar and never printed as "0%", which would read as a
   * measured near-zero effect rather than as indistinguishable from chance. */
  function correctedText(ctx, c) {
    var omega = num(c.omega_squared_min);
    if (omega === null) return "not correctable";
    if (omega <= 0) return "at chance";
    return ctx.fmt.pct(omega, 0);
  }

  function colorFor(ctx, factor) {
    if (factor === "task") return ctx.color.a;
    if (factor === "model") return ctx.color.b;
    if (factor === "harness") return ctx.color.warn;
    if (factor === "version") return ctx.color.good;
    return ctx.color.muted;
  }

  function tag(ctx, cls, text) {
    return ctx.h("span", { class: cls ? "tag " + cls : "tag", text: text });
  }

  function note(ctx, kind, kids) {
    return ctx.h("div", { class: "vz-note " + kind }, kids);
  }

  function head(ctx, text) {
    return ctx.h("div", { class: "vz-h", text: text });
  }

  /* The engine's caveat strings are the label on the number: the binary
   * one says the success components are on the probability scale, the
   * confounded one says the model/harness split is an artefact. They are
   * reproduced word for word — paraphrasing a hedge is how a hedge dies. */
  function caveatNode(ctx, text, prefix) {
    if (!str(text)) return null;
    return ctx.h("div", { class: "caveat", text: (prefix ? prefix + " " : "") + text });
  }

  function swatch(ctx, style) {
    return ctx.h("span", { class: "vz-sw", style: style });
  }

  function chipRow(ctx, chips) {
    return ctx.h("div", { class: "vz-chips" }, chips.filter(Boolean));
  }

  function chip(ctx, mark, label) {
    return ctx.h("span", { class: "vz-chip" }, [mark, ctx.h("span", { text: label })]);
  }

  // --------------------------------------------------------------- svg bits

  var UID = 0;

  /* Sized like the other modules: fills the card, stops growing, so the
   * same drawing survives a five-column and a one-column reading. */
  function chart(ctx, width, height) {
    return ctx.svg("svg", {
      viewBox: "0 0 " + width + " " + height,
      preserveAspectRatio: "xMinYMin meet",
      style: "width:100%;max-width:" + Math.round(width * 1.45) +
             "px;height:auto;display:block;overflow:visible",
      role: "img",
    });
  }

  function txt(ctx, x, y, text, fill, size, anchor, weight) {
    return ctx.svg("text", {
      x: x, y: y, fill: fill, "font-size": size || 8.5,
      "text-anchor": anchor || "start",
      "font-family": "var(--sans)",
      "font-weight": weight || null,
    }, [String(text)]);
  }

  /* Diagonal hatch: the mark this module uses for "not attributable".
   * Every contested quantity — a factor's contested tail, the pooled
   * shared segment, an inseparable residual — carries it, so the reader
   * learns one texture rather than four legends. */
  function hatchDefs(ctx, stroke) {
    var id = "vz-hatch-" + (++UID);
    return {
      id: id,
      node: ctx.svg("defs", null, [
        ctx.svg("pattern", {
          id: id, width: 5, height: 5, patternUnits: "userSpaceOnUse",
          patternTransform: "rotate(45)",
        }, [
          ctx.svg("rect", { width: 5, height: 5, fill: "transparent" }),
          ctx.svg("line", {
            x1: 0, y1: 0, x2: 0, y2: 5, stroke: stroke,
            "stroke-width": 1.6, opacity: 0.5,
          }),
        ]),
      ]),
    };
  }

  function scale(lo, hi, from, to) {
    var span = hi - lo;
    if (!span) span = 1;
    return function (value) { return from + ((value - lo) / span) * (to - from); };
  }

  function axis(ctx, plot, x, y0, y1, ticks, labelY) {
    ticks.forEach(function (t) {
      plot.appendChild(ctx.svg("line", {
        x1: x(t), x2: x(t), y1: y0, y2: y1,
        stroke: ctx.color.grid, "stroke-width": 1,
      }));
      if (labelY !== null && labelY !== undefined) {
        plot.appendChild(txt(ctx, x(t), labelY, ctx.fmt.pct(t, 0),
          ctx.color.muted, 7.5, "middle"));
      }
    });
  }

  // ======================================================== 1. variance

  /* The headline bar accounts for exactly 100% of the variation, which
   * forces an honest choice about the contested share. Handing it to a
   * factor would over-credit that factor; splitting it evenly would invent
   * a number. So each factor gets the share it owns under every ordering
   * (its minimum), the leftover explained-but-contested variance becomes a
   * hatched segment of its own, and the residual closes the bar. */
  function stackFor(m) {
    var rows = componentsOf(m);
    var residual = clamp01(num(m.residual) || 0);
    var owned = 0;
    var segments = rows.map(function (row) {
      var lo = clamp01(num(row.c.min_share) || 0);
      owned += lo;
      return { name: row.name, c: row.c, width: lo };
    });
    // Sequential sums of squares are exhaustive for any single ordering, so
    // shares + residual = 1 exactly; the pool is what no ordering pins down.
    var pool = 1 - residual - owned;
    if (pool < 0) pool = 0;
    var total = owned + pool + residual;
    if (total > 1.0001) {
      // Defensive only: rounding in the JSON should never make this fire.
      segments.forEach(function (s) { s.width /= total; });
      pool /= total; residual /= total;
    }
    return { segments: segments, pool: pool, residual: residual, rows: rows };
  }

  function drawStack(ctx, m) {
    var stack = stackFor(m);
    var rows = stack.rows;
    var W = 372;
    var LG = 52, RG = 52;
    var barH = 22, rowH = 17;
    var top = 3;
    var H = top + barH + 12 + rows.length * rowH + 15;
    var plot = chart(ctx, W, H);
    var x = scale(0, 1, LG, W - RG);
    var hatch = hatchDefs(ctx, ctx.color.muted);
    plot.appendChild(hatch.node);

    // --- the 100% bar
    var cursor = 0;
    plot.appendChild(txt(ctx, LG - 6, top + barH / 2 + 3, "share", ctx.color.muted, 8.5, "end"));

    stack.segments.forEach(function (seg) {
      var w = (x(cursor + seg.width) - x(cursor));
      if (w > 0.4) {
        plot.appendChild(ctx.svg("rect", {
          x: x(cursor), y: top, width: w, height: barH,
          fill: colorFor(ctx, seg.name), opacity: 0.88,
        }));
        if (w >= 34) {
          plot.appendChild(txt(ctx, x(cursor) + w / 2, top + barH / 2 + 3.2,
            seg.name, ctx.color.surface, 8.5, "middle", 600));
        }
      }
      cursor += seg.width;
    });

    if (stack.pool > 0.0005) {
      var pw = x(cursor + stack.pool) - x(cursor);
      plot.appendChild(ctx.svg("rect", {
        x: x(cursor), y: top, width: pw, height: barH,
        fill: "url(#" + hatch.id + ")", stroke: ctx.color.axis, "stroke-width": 1,
      }));
      cursor += stack.pool;
    }

    if (stack.residual > 0.0005) {
      var rw = x(cursor + stack.residual) - x(cursor);
      plot.appendChild(ctx.svg("rect", {
        x: x(cursor), y: top, width: rw, height: barH,
        fill: ctx.color.muted, opacity: 0.17,
        stroke: ctx.color.axis, "stroke-width": 1,
      }));
      if (rw >= 46) {
        plot.appendChild(txt(ctx, x(cursor) + rw / 2, top + barH / 2 + 3.2,
          "residual " + ctx.fmt.pct(stack.residual, 0), ctx.color.ink, 8.5, "middle", 600));
      }
    }
    plot.appendChild(ctx.svg("rect", {
      x: LG, y: top, width: (W - RG) - LG, height: barH,
      fill: "none", stroke: ctx.color.axis, "stroke-width": 1,
    }));

    // --- one range row per factor, on the same scale as the bar above
    var y = top + barH + 12;
    axis(ctx, plot, x, y - 5, y + rows.length * rowH - 4, [0, 0.25, 0.5, 0.75, 1],
      y + rows.length * rowH + 6);

    rows.forEach(function (row, i) {
      var c = row.c;
      var lo = clamp01(num(c.min_share) || 0);
      var hi = clamp01(isNum(c.max_share) ? c.max_share : lo);
      var cy = y + i * rowH + rowH / 2 - 2;
      var fill = colorFor(ctx, row.name);

      plot.appendChild(txt(ctx, LG - 6, cy + 3, row.name, fill, 8.5, "end", 600));

      // Owned outright under every attribution order.
      if (x(lo) - x(0) > 0.4) {
        plot.appendChild(ctx.svg("rect", {
          x: x(0), y: cy - 3.5, width: x(lo) - x(0), height: 7,
          fill: fill, opacity: 0.9, rx: 1,
        }));
      }
      if (hi > lo + 0.0005) {
        // The contested tail. Hatched, capped at both ends, never filled in
        // as if the factor had earned it.
        plot.appendChild(ctx.svg("rect", {
          x: x(lo), y: cy - 3.5, width: x(hi) - x(lo), height: 7,
          fill: "url(#" + hatch.id + ")", stroke: fill, "stroke-width": 1,
          "stroke-dasharray": "2 2", opacity: 0.95, rx: 1,
        }));
        [lo, hi].forEach(function (v) {
          plot.appendChild(ctx.svg("line", {
            x1: x(v), x2: x(v), y1: cy - 6, y2: cy + 6,
            stroke: fill, "stroke-width": 1.4,
          }));
        });
      } else {
        // Identified: a point, and marked as one, so the eye can tell a
        // measured value from a range that happens to be narrow.
        plot.appendChild(ctx.svg("line", {
          x1: x(lo), x2: x(lo), y1: cy - 6, y2: cy + 6,
          stroke: fill, "stroke-width": 1.6,
        }));
      }
      plot.appendChild(txt(ctx, W - RG + 5, cy + 3, rangeText(ctx, c),
        ctx.color.ink, 8.5, "start"));
    });

    return { node: plot, stack: stack };
  }

  AgentDiff.block({
    id: "variance",
    title: "Where the variation comes from",
    question: "What explains the variation in outcomes?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var v = payloadOf(ctx);
      if (!v) return 0;
      return drawable(v).length ? 1 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var v = payloadOf(ctx);
      if (!v) {
        return ctx.empty(el, "No variance decomposition in this report — it is written " +
          "by `deepcompare variance`, which batch and runs do not run.");
      }
      var plan = designOf(v) || {};
      var entries = metricsOf(v);
      if (!entries.length) return ctx.empty(el, "The variance report carries no metrics.");

      if (str(v.narrative)) {
        el.appendChild(note(ctx, plan.shape === "confounded" ? "bad" : "info",
          [ctx.h("span", { text: v.narrative })]));
      }

      var any = false;
      entries.forEach(function (entry) {
        var m = entry.m;
        var section = ctx.h("div", { class: "vz-metric" });
        var headRow = ctx.h("div", { class: "vz-metric-head" }, [
          ctx.h("b", { text: entry.label }),
          isNum(m.runs) ? ctx.h("span", { class: "vz-sub", text: ctx.fmt.int(m.runs) + " runs" }) : null,
          isNum(m.orders_tried) && m.orders_tried > 0
            ? ctx.h("span", { class: "vz-sub", text: m.orders_tried + " attribution orders swept" })
            : null,
        ]);
        section.appendChild(headRow);

        if (!usable(m)) {
          // A metric with nothing to split says why, in the engine's words.
          section.appendChild(ctx.h("div", {
            class: "empty",
            text: str(m.reason) || "no decomposition for this metric",
          }));
          el.appendChild(section);
          return;
        }
        any = true;

        var drawn = drawStack(ctx, m);
        section.appendChild(ctx.h("div", { class: "vz-svg-wrap" }, [drawn.node]));

        var chips = drawn.stack.segments.map(function (seg) {
          return chip(ctx, swatch(ctx, { background: colorFor(ctx, seg.name), opacity: "0.88" }),
            seg.name + " " + rangeText(ctx, seg.c));
        });
        if (drawn.stack.pool > 0.0005) {
          chips.push(chip(ctx,
            swatch(ctx, { background: "var(--surface)", borderStyle: "dashed" }),
            "shared " + ctx.fmt.pct(drawn.stack.pool, 0) + " — no ordering can assign it"));
        }
        chips.push(chip(ctx, swatch(ctx, { background: ctx.color.muted, opacity: "0.25" }),
          "residual " + ctx.fmt.pct(drawn.stack.residual, 0)));
        section.appendChild(chipRow(ctx, chips));

        if (str(m.narrative)) {
          section.appendChild(ctx.h("div", {
            class: "vz-sub", style: { marginTop: "7px" }, text: m.narrative,
          }));
        }
        var cav = caveatNode(ctx, m.caveat, "Caveat:");
        if (cav) section.appendChild(cav);
        el.appendChild(section);
      });

      if (!any) {
        el.appendChild(ctx.h("div", {
          class: "caveat",
          text: "No metric in this corpus had variance to attribute.",
        }));
      } else if (str(v.metrics && v.metrics.success && v.metrics.success.method)) {
        el.appendChild(ctx.h("div", { class: "caveat", text: "Method: " + v.metrics.success.method }));
      }
    },
  });

  // =============================================== 2. variance-corrected

  /* Raw share against the chance floor against omega squared, on one axis.
   *
   * This is the honesty point of the whole module: a factor with many
   * levels explains variance for having levels. Drawing the chance floor as
   * a zone rather than a footnote means the reader sees a 17% raw share
   * standing barely clear of a 12% floor without having to do arithmetic,
   * and sees the corrected bar land where it actually lands. */
  function drawCorrected(ctx, m) {
    var rows = componentsOf(m);
    var maxValue = 0.05;
    rows.forEach(function (row) {
      [row.c.max_share, row.c.expected_by_chance, row.c.omega_squared_max,
       row.c.omega_squared_min].forEach(function (value) {
        if (isNum(value) && value > maxValue) maxValue = value;
      });
    });
    var top = Math.min(1, Math.ceil((maxValue * 1.12) * 20) / 20);

    var W = 380, LG = 60, RG = 76;
    var rowH = 34, headH = 11;
    var H = headH + rows.length * rowH + 16;
    var plot = chart(ctx, W, H);
    var x = scale(0, top, LG, W - RG);
    var hatch = hatchDefs(ctx, ctx.color.muted);
    plot.appendChild(hatch.node);

    var ticks = [0, top / 2, top];
    axis(ctx, plot, x, headH, headH + rows.length * rowH - 6,
      ticks, headH + rows.length * rowH + 6);

    rows.forEach(function (row, i) {
      var c = row.c;
      var y0 = headH + i * rowH;
      var fill = colorFor(ctx, row.name);
      var lo = Math.max(0, num(c.min_share) || 0);
      var hi = Math.max(lo, isNum(c.max_share) ? c.max_share : lo);
      var chance = Math.max(0, num(c.expected_by_chance) || 0);
      var omega = num(c.omega_squared_min);

      plot.appendChild(txt(ctx, LG - 6, y0 + 12, row.name, fill, 8.5, "end", 600));
      plot.appendChild(txt(ctx, LG - 6, y0 + 22,
        (isNum(c.levels) ? c.levels : "?") + " levels", ctx.color.muted, 7.5, "end"));

      /* The zone every factor gets for free, before any real effect.
       *
       * It is drawn behind the *raw* bar only. Extending it across the
       * corrected bar would read as "this ω² is below chance", which is
       * backwards: omega squared has already had the floor subtracted, so
       * a corrected bar shorter than the floor is the correction working. */
      if (chance > 0) {
        plot.appendChild(ctx.svg("rect", {
          x: x(0), y: y0 + 2, width: Math.max(0, x(chance) - x(0)), height: 13,
          fill: ctx.color.muted, opacity: 0.16,
        }));
      }

      // Raw: a range where the design leaves one, a capped point where not.
      var yRaw = y0 + 6;
      if (x(lo) - x(0) > 0.4) {
        plot.appendChild(ctx.svg("rect", {
          x: x(0), y: yRaw, width: x(lo) - x(0), height: 7,
          fill: fill, opacity: 0.85, rx: 1,
        }));
      }
      if (hi > lo + 0.0005) {
        plot.appendChild(ctx.svg("rect", {
          x: x(lo), y: yRaw, width: x(hi) - x(lo), height: 7,
          fill: "url(#" + hatch.id + ")", stroke: fill, "stroke-width": 1,
          "stroke-dasharray": "2 2", rx: 1,
        }));
        [lo, hi].forEach(function (value) {
          plot.appendChild(ctx.svg("line", {
            x1: x(value), x2: x(value), y1: yRaw - 2, y2: yRaw + 9,
            stroke: fill, "stroke-width": 1.3,
          }));
        });
      } else {
        plot.appendChild(ctx.svg("line", {
          x1: x(lo), x2: x(lo), y1: yRaw - 2, y2: yRaw + 9,
          stroke: fill, "stroke-width": 1.5,
        }));
      }
      plot.appendChild(txt(ctx, W - RG + 5, yRaw + 7, "raw " + rangeText(ctx, c),
        ctx.color.ink, 8, "start"));

      /* The floor is ruled *over* the raw bar, not behind it: the whole
       * comparison is how far the bar clears the line, and a line hidden
       * under the bar it qualifies is a line nobody reads. */
      if (chance > 0) {
        plot.appendChild(ctx.svg("line", {
          x1: x(chance), x2: x(chance), y1: y0 + 2, y2: y0 + 16,
          stroke: ctx.color.ink, "stroke-width": 1.1, "stroke-dasharray": "3 2",
          opacity: 0.75,
        }));
        if (i === 0) {
          plot.appendChild(txt(ctx, x(chance) + 2, y0 - 1,
            "chance floor", ctx.color.muted, 7.5, "start"));
        }
      }

      // Corrected: drawn only where it is positive. At or below chance the
      // row says so in words — a hairline bar would imply a small effect
      // where the finding is "no more than its level count predicts".
      var yCor = y0 + 17;
      if (omega !== null && omega > 0) {
        plot.appendChild(ctx.svg("rect", {
          x: x(0), y: yCor, width: Math.max(1, x(omega) - x(0)), height: 7,
          fill: fill, opacity: 1, rx: 1,
        }));
        plot.appendChild(txt(ctx, W - RG + 5, yCor + 7, "ω² " + ctx.fmt.pct(omega, 0),
          ctx.color.ink, 8, "start", 600));
      } else {
        plot.appendChild(ctx.svg("line", {
          x1: x(0), x2: x(0), y1: yCor, y2: yCor + 7,
          stroke: ctx.color.warn, "stroke-width": 2,
        }));
        plot.appendChild(txt(ctx, x(0) + 4, yCor + 7,
          omega === null
            ? "ω² not correctable — no residual degrees of freedom"
            : "ω² at chance",
          ctx.color.warn, 8, "start", 600));
      }
    });

    return plot;
  }

  AgentDiff.block({
    id: "variance-corrected",
    title: "Raw share vs. chance",
    question: "Is that share real, or just a lot of levels?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var v = payloadOf(ctx);
      if (!v) return 0;
      var list = drawable(v);
      if (!list.length) return 0;
      var loud = false;
      list.forEach(function (entry) {
        componentsOf(entry.m).forEach(function (row) {
          var omega = num(row.c.omega_squared_min);
          var chance = num(row.c.expected_by_chance) || 0;
          if (omega !== null && omega <= 0 && (num(row.c.max_share) || 0) > 0.02) loud = true;
          if (chance > 0.05) loud = true;
        });
      });
      return loud ? 0.98 : 0.7;
    },

    render: function (el, ctx) {
      ensureStyle();
      var v = payloadOf(ctx);
      if (!v) {
        return ctx.empty(el, "No variance decomposition in this report — the bias " +
          "correction comes with `deepcompare variance`.");
      }
      var list = drawable(v);
      if (!list.length) {
        return ctx.empty(el, "No metric in this corpus had variance to attribute, so " +
          "there is nothing to correct.");
      }

      /* The lead sentence is arithmetic the reader should not have to do:
       * the factor with the most levels, and what that alone buys it. */
      var worst = null;
      componentsOf(list[0].m).forEach(function (row) {
        var chance = num(row.c.expected_by_chance);
        if (chance === null) return;
        if (!worst || chance > worst.chance) worst = { name: row.name, chance: chance, c: row.c };
      });
      if (worst) {
        el.appendChild(note(ctx, "warn", [
          ctx.h("strong", { text: worst.name + " has " +
            (isNum(worst.c.levels) ? worst.c.levels : "several") + " levels. " }),
          ctx.h("span", { text: "Across " + ctx.fmt.int(list[0].m.runs) + " runs that alone " +
            "explains " + ctx.fmt.pct(worst.chance, 0) + " of the variance, before any real " +
            "effect exists. The shaded zone and dashed rule on each row are that floor: a raw " +
            "bar that barely clears it has barely done anything. The solid ω² bar is what " +
            "survives subtracting it." }),
        ]));
      }

      el.appendChild(chipRow(ctx, [
        chip(ctx, swatch(ctx, { background: ctx.color.a, opacity: "0.45", borderStyle: "dashed" }),
          "raw share — hatched and capped where contested"),
        chip(ctx, swatch(ctx, { background: ctx.color.a }), "ω² corrected (solid, only where positive)"),
        chip(ctx, swatch(ctx, { background: ctx.color.muted, opacity: "0.35" }), "expected by chance"),
      ]));

      /* Three metrics at four factors is twelve rows; tabs keep one
       * comparison on screen at a time, which is the comparison this card
       * exists to make. */
      var state = { key: list[0].key };
      var tabs = ctx.h("div", { class: "vz-tabs" });
      var body = ctx.h("div", null, null);

      function paint() {
        var entry = null;
        for (var i = 0; i < list.length; i++) if (list[i].key === state.key) entry = list[i];
        if (!entry) entry = list[0];
        body.innerHTML = "";
        Array.prototype.forEach.call(tabs.childNodes, function (node) {
          if (node.setAttribute) {
            node.setAttribute("aria-pressed", node.getAttribute("data-key") === entry.key ? "true" : "false");
          }
        });
        body.appendChild(ctx.h("div", { class: "vz-svg-wrap" }, [drawCorrected(ctx, entry.m)]));

        var atChance = componentsOf(entry.m).filter(function (row) {
          var omega = num(row.c.omega_squared_min);
          return omega !== null && omega <= 0;
        }).map(function (row) { return row.name; });
        if (atChance.length) {
          body.appendChild(ctx.h("div", { class: "vz-sub", style: { marginTop: "7px" },
            text: atChance.join(", ") + (atChance.length === 1 ? " explains" : " explain") +
              " no more of " + entry.label + " than its level count predicts by chance — the raw " +
              "share above it is an artefact of how many levels it has." }));
        }
        var cav = caveatNode(ctx, entry.m.caveat, "Caveat:");
        if (cav) body.appendChild(cav);
      }

      list.forEach(function (entry) {
        tabs.appendChild(ctx.h("button", {
          class: "vz-tab", type: "button", "data-key": entry.key,
          "aria-pressed": entry.key === state.key ? "true" : "false",
          text: entry.label,
          onclick: function () {
            state.key = entry.key;
            ctx.signal("inspect");
            paint();
          },
        }));
      });

      if (list.length > 1) el.appendChild(tabs);
      el.appendChild(body);
      paint();
    },
  });

  // ================================================== 3. variance-design

  var SHAPE_STYLE = {
    crossed: { kind: "good", word: "crossed", lead: "Model and harness vary independently — the split below is identifiable." },
    nested: { kind: "warn", word: "nested", lead: "Partly separable: harnesses that share a model carry harness variance." },
    confounded: { kind: "bad", word: "confounded", lead: "Model and harness are the same partition of the data." },
  };

  /* The partition picture: one row per model, one cell per harness on it.
   *
   * A confounded corpus draws as a column of single cells — every model
   * carries exactly one harness, so the two factors cut the runs the same
   * way and no method can tell them apart. A nested corpus draws as ragged
   * rows, and the raggedness is exactly the variance that is separable. */
  function drawPartition(ctx, plan) {
    var names = obj(plan.level_names) || {};
    var models = arr(names.model).slice(0);
    var shared = obj(plan.harnesses_sharing_a_model) || {};
    var levels = obj(plan.levels) || {};
    if (!models.length) return null;

    var W = 372, LG = 108, cellW = 15, cellH = 11, rowH = 16;
    var shownModels = models.slice(0, 10);
    var H = 14 + shownModels.length * rowH + 6;
    var plot = chart(ctx, W, H);

    plot.appendChild(txt(ctx, LG - 6, 9, "model", ctx.color.muted, 7.5, "end", 600));
    plot.appendChild(txt(ctx, LG, 9, "harnesses on it", ctx.color.muted, 7.5, "start", 600));

    shownModels.forEach(function (model, i) {
      var y = 14 + i * rowH;
      var list = arr(shared[model]);
      // A model absent from `harnesses_sharing_a_model` carries exactly one
      // harness. Which one is not in the payload, so it is drawn unnamed
      // rather than guessed at.
      var count = list.length || 1;
      plot.appendChild(txt(ctx, LG - 6, y + 8, ctx.fmt.truncate(model, 18),
        ctx.color.ink, 8, "end"));
      var maxCells = Math.floor((W - LG - 34) / cellW);
      var drawnCells = Math.min(count, maxCells);
      for (var k = 0; k < drawnCells; k++) {
        plot.appendChild(ctx.svg("rect", {
          x: LG + k * cellW, y: y + 1, width: cellW - 3, height: cellH,
          rx: 2, fill: colorFor(ctx, "harness"),
          opacity: count > 1 ? 0.85 : 0.4,
          stroke: count > 1 ? "none" : ctx.color.bad,
          "stroke-width": count > 1 ? 0 : 1,
          "stroke-dasharray": count > 1 ? null : "2 2",
        }));
      }
      var label = count > 1 ? count + " harnesses" : "1 harness";
      if (count > drawnCells) label = count + " harnesses (" + drawnCells + " shown)";
      plot.appendChild(txt(ctx, LG + drawnCells * cellW + 3, y + 9, label,
        count > 1 ? ctx.color.muted : ctx.color.bad, 7.5, "start"));
    });

    if (models.length < (isNum(levels.model) ? levels.model : 0) ||
        shownModels.length < models.length) {
      var more = (isNum(levels.model) ? levels.model : models.length) - shownModels.length;
      if (more > 0) {
        plot.appendChild(txt(ctx, LG, 14 + shownModels.length * rowH + 2,
          "+" + more + " more model(s)", ctx.color.muted, 7.5, "start"));
      }
    }
    return plot;
  }

  AgentDiff.block({
    id: "variance-design",
    title: "What this design can identify",
    question: "Can this data even answer the question?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var v = payloadOf(ctx);
      var plan = designOf(v);
      if (!plan) return 0;
      if (plan.shape === "confounded") return 1;
      if (plan.shape === "nested") return 0.85;
      return 0.6;
    },

    render: function (el, ctx) {
      ensureStyle();
      var v = payloadOf(ctx);
      var plan = designOf(v);
      if (!plan) {
        return ctx.empty(el, "No variance decomposition in this report — the design " +
          "audit comes with `deepcompare variance`.");
      }
      var shape = str(plan.shape);
      var style = SHAPE_STYLE[shape] || { kind: "info", word: shape || "unknown", lead: "" };

      /* Loudest first for the case that invalidates everything under it. */
      var lead = ctx.h("div", { class: "vz-lead " + style.kind });
      lead.appendChild(ctx.h("div", { class: "vz-lead-top" }, [
        ctx.h("span", { class: "vz-lead-t", text: "Design is " + style.word }),
        isNum(plan.runs) ? tag(ctx, "", ctx.fmt.int(plan.runs) + (plan.runs === 1 ? " run" : " runs")) : null,
        shape === "confounded" ? tag(ctx, "bad", "model ≡ harness") : null,
      ]));
      if (style.lead) lead.appendChild(ctx.h("div", { class: "vz-lead-b", text: style.lead }));
      // The engine's note, word for word — it carries the fix.
      if (str(plan.note)) {
        lead.appendChild(ctx.h("div", { class: "vz-lead-b", style: { marginTop: "5px" },
          text: plan.note }));
      }
      el.appendChild(lead);

      if (shape === "confounded") {
        el.appendChild(note(ctx, "bad", [
          ctx.h("strong", { text: "Any model-versus-harness split below is an artefact of " +
            "attribution order, not a finding. " }),
          ctx.h("span", { text: "The two factors cut these runs identically, so whichever is " +
            "fitted first takes the variance and the other takes none." }),
        ]));

        /* Evidence, not assertion: if the two factors really are the same
         * partition, every metric gives them byte-identical ranges. */
        var rowsOut = [];
        drawable(v).forEach(function (entry) {
          var comps = obj(entry.m.components) || {};
          var mo = obj(comps.model), ha = obj(comps.harness);
          if (!mo || !ha) return;
          var same = num(mo.min_share) === num(ha.min_share) &&
                     num(mo.max_share) === num(ha.max_share);
          rowsOut.push(ctx.h("tr", null, [
            ctx.h("td", { text: entry.label }),
            ctx.h("td", { class: "num", text: rangeText(ctx, mo) }),
            ctx.h("td", { class: "num", text: rangeText(ctx, ha) }),
            ctx.h("td", { class: "num" }, [tag(ctx, same ? "bad" : "", same ? "identical" : "differs")]),
          ]));
        });
        if (rowsOut.length) {
          el.appendChild(head(ctx, "the same numbers, twice"));
          el.appendChild(ctx.h("div", { class: "scroll-x" }, [
            ctx.h("table", { class: "grid" }, [
              ctx.h("thead", null, [ctx.h("tr", null, [
                ctx.h("th", { text: "metric" }),
                ctx.h("th", { class: "num", text: "model" }),
                ctx.h("th", { class: "num", text: "harness" }),
                ctx.h("th", { class: "num", text: "" }),
              ])]),
              ctx.h("tbody", null, rowsOut),
            ]),
          ]));
        }
      }

      // --- levels per factor
      el.appendChild(head(ctx, "levels per factor"));
      var levels = obj(plan.levels) || {};
      var names = obj(plan.level_names) || {};
      var constant = arr(plan.constant);
      var factorRows = FACTORS.filter(function (f) { return isNum(levels[f]); }).map(function (f) {
        var shown = arr(names[f]);
        var isConstant = constant.indexOf(f) >= 0;
        var sample = shown.slice(0, 3).join(", ");
        if (shown.length && levels[f] > shown.length) sample += ", +" + (levels[f] - shown.length) + " more";
        else if (shown.length > 3) sample += ", +" + (shown.length - 3) + " more";
        return ctx.h("tr", null, [
          ctx.h("td", null, [
            ctx.h("span", { style: { color: colorFor(ctx, f), fontWeight: "600" }, text: f }),
          ]),
          ctx.h("td", { class: "num", text: ctx.fmt.int(levels[f]) }),
          ctx.h("td", null, [
            isConstant
              ? tag(ctx, "warn", "constant — not attributable")
              : ctx.h("span", { class: "vz-names", text: sample || "—" }),
          ]),
        ]);
      });
      el.appendChild(ctx.h("div", { class: "scroll-x" }, [
        ctx.h("table", { class: "grid" }, [
          ctx.h("thead", null, [ctx.h("tr", null, [
            ctx.h("th", { text: "factor" }),
            ctx.h("th", { class: "num", text: "levels" }),
            ctx.h("th", { text: "values" }),
          ])]),
          ctx.h("tbody", null, factorRows),
        ]),
      ]));

      // --- repeats: the fact that decides whether a residual is noise
      var repeats = obj(plan.repeats_per_cell) || {};
      var repeatText = isNum(repeats.min) && isNum(repeats.max)
        ? (repeats.min === repeats.max ? repeats.min + " per cell" : repeats.min + "–" + repeats.max + " per cell")
        : "unrecorded";
      el.appendChild(head(ctx, "repeats"));
      el.appendChild(ctx.h("dl", { class: "kv vz-kv" }, [
        ctx.h("dt", { text: "runs per cell" }),
        ctx.h("dd", null, [
          ctx.h("span", { text: repeatText }),
          isNum(repeats.cells) ? ctx.h("span", { class: "vz-sub", text: " · " + ctx.fmt.int(repeats.cells) + " cells" }) : null,
        ]),
        ctx.h("dt", { text: "residual is noise" }),
        ctx.h("dd", null, [
          plan.residual_is_noise === true
            ? tag(ctx, "good", "yes — repeats measure it")
            : tag(ctx, "warn", "no — interaction and noise together"),
        ]),
        ctx.h("dt", { text: "identifiable" }),
        ctx.h("dd", { text: arr(plan.identifiable).join(", ") || "none" }),
      ]));

      // --- the partition picture
      var picture = drawPartition(ctx, plan);
      if (picture) {
        el.appendChild(head(ctx, shape === "confounded"
          ? "one harness per model — the partitions coincide"
          : "harnesses per model"));
        el.appendChild(ctx.h("div", { class: "vz-svg-wrap" }, [picture]));
      }

      /* What would fix it, in the engine's words rather than mine. */
      var fixAt = str(plan.note).indexOf("Run one harness");
      if (fixAt >= 0) {
        el.appendChild(ctx.h("div", { class: "vz-fix" }, [
          ctx.h("b", { text: "what would fix it" }),
          ctx.h("span", { text: plan.note.slice(fixAt) }),
        ]));
      }
    },
  });

  // ================================================ 4. variance-residual

  /* The residual bar, drawn two ways because it means two things.
   *
   * With repeats it is measured run-to-run variation and gets a solid mark.
   * With one run per cell it is interaction *and* noise with no way to say
   * how much of each, so it gets a hatched bar whose internal boundary is a
   * double-headed arrow: the split can sit anywhere along it. Calling that
   * flakiness would invite someone to dismiss a real interaction as luck. */
  function drawResidual(ctx, m, plan) {
    var residual = clamp01(num(m.residual) || 0);
    var noise = plan && plan.residual_is_noise === true;
    var W = 372, H = 46, LG = 2, RG = 2;
    var plot = chart(ctx, W, H);
    var x = scale(0, 1, LG, W - RG);
    var barY = 4, barH = 20;
    var hatch = hatchDefs(ctx, noise ? ctx.color.warn : ctx.color.muted);
    plot.appendChild(hatch.node);

    var explained = 1 - residual;
    if (explained > 0.0005) {
      plot.appendChild(ctx.svg("rect", {
        x: x(0), y: barY, width: x(explained) - x(0), height: barH,
        fill: ctx.color.a, opacity: 0.3,
      }));
      if (x(explained) - x(0) > 66) {
        plot.appendChild(txt(ctx, x(0) + 5, barY + barH / 2 + 3.2,
          "explained " + ctx.fmt.pct(explained, 0), ctx.color.ink, 8.5, "start", 600));
      }
    }

    var rx = x(explained), rw = x(1) - x(explained);
    plot.appendChild(ctx.svg("rect", {
      x: rx, y: barY, width: rw, height: barH,
      fill: noise ? ctx.color.warn : "url(#" + hatch.id + ")",
      opacity: noise ? 0.55 : 1,
      stroke: ctx.color.axis, "stroke-width": 1,
    }));
    if (rw > 78) {
      plot.appendChild(txt(ctx, rx + rw / 2, barY + barH / 2 + 3.2,
        "residual " + ctx.fmt.pct(residual, 0),
        ctx.color.ink, 9, "middle", 660));
    }
    plot.appendChild(ctx.svg("rect", {
      x: x(0), y: barY, width: x(1) - x(0), height: barH,
      fill: "none", stroke: ctx.color.axis, "stroke-width": 1,
    }));

    if (noise) {
      plot.appendChild(txt(ctx, rx + rw / 2, barY + barH + 13,
        "measured: the same cell, run again", ctx.color.muted, 8, "middle"));
    } else if (rw > 40) {
      // The unknowable boundary, drawn as a boundary that will not sit still.
      var mid = rx + rw / 2, span = Math.min(rw / 2 - 6, 46);
      plot.appendChild(ctx.svg("line", {
        x1: mid - span, x2: mid + span, y1: barY + barH + 8, y2: barY + barH + 8,
        stroke: ctx.color.muted, "stroke-width": 1,
      }));
      [[mid - span, 1], [mid + span, -1]].forEach(function (end) {
        plot.appendChild(ctx.svg("path", {
          d: "M " + end[0] + " " + (barY + barH + 8) +
             " l " + (4 * end[1]) + " -3 l 0 6 z",
          fill: ctx.color.muted,
        }));
      });
      plot.appendChild(ctx.svg("line", {
        x1: mid, x2: mid, y1: barY + 2, y2: barY + barH + 8,
        stroke: ctx.color.muted, "stroke-width": 1.2, "stroke-dasharray": "3 2",
      }));
      plot.appendChild(txt(ctx, mid - span - 3, barY + barH + 11, "interaction",
        ctx.color.muted, 8, "end"));
      plot.appendChild(txt(ctx, mid + span + 3, barY + barH + 11, "noise",
        ctx.color.muted, 8, "start"));
      plot.appendChild(txt(ctx, mid, barY + barH + 21, "boundary unknown",
        ctx.color.muted, 7.5, "middle"));
    }
    return plot;
  }

  AgentDiff.block({
    id: "variance-residual",
    title: "What is left over",
    question: "What is left over, and is it noise?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var v = payloadOf(ctx);
      if (!v) return 0;
      var list = drawable(v);
      if (!list.length) return 0;
      var plan = designOf(v) || {};
      var worst = 0;
      list.forEach(function (entry) {
        var r = num(entry.m.residual);
        if (r !== null && r > worst) worst = r;
      });
      if (worst > 0.5) return plan.residual_is_noise ? 0.9 : 1;
      return Math.min(0.95, 0.45 + worst);
    },

    render: function (el, ctx) {
      ensureStyle();
      var v = payloadOf(ctx);
      if (!v) {
        return ctx.empty(el, "No variance decomposition in this report — the residual " +
          "comes with `deepcompare variance`.");
      }
      var list = drawable(v);
      if (!list.length) {
        return ctx.empty(el, "No metric in this corpus had variance to attribute, so there " +
          "is no residual to interpret.");
      }
      var plan = designOf(v) || {};
      var noise = plan.residual_is_noise === true;

      /* Headline: the single loudest number in the analysis. Most of what
       * happens is not explained by anything the corpus recorded. */
      var lead = list[0];
      var residual = num(lead.m.residual) || 0;
      var factors = arr(lead.m.factors);
      var leadCard = ctx.h("div", { class: "vz-lead " + (residual > 0.5 ? "bad" : "warn") });
      leadCard.appendChild(ctx.h("div", { class: "vz-lead-top" }, [
        ctx.h("span", { class: "vz-lead-n", text: ctx.fmt.pct(residual, 0) }),
        ctx.h("span", { class: "vz-lead-t", text: "of the variation in " + lead.label +
          " is unexplained" }),
      ]));
      leadCard.appendChild(ctx.h("div", { class: "vz-lead-b",
        text: factors.length
          ? "Nothing in " + factors.join(", ") + " accounts for it."
          : "No recorded factor accounts for it." }));
      leadCard.appendChild(ctx.h("div", { class: "vz-lead-b", style: { marginTop: "4px" } }, [
        noise
          ? tag(ctx, "good", "repeats present — this is run-to-run variation")
          : tag(ctx, "bad", "one run per cell — this is NOT flakiness"),
      ]));
      el.appendChild(leadCard);

      el.appendChild(note(ctx, noise ? "info" : "warn", [
        ctx.h("strong", { text: noise
          ? "The same cell was run more than once, so the residual is measured noise. "
          : "There is one run per cell, so interaction and noise cannot be separated. " }),
        ctx.h("span", { text: noise
          ? "Repeats hold every factor fixed and let the outcome move, which is exactly what " +
            "run-to-run variation means."
          : "A factor pairing that behaves unusually and a run that simply came out differently " +
            "land in the same number here. Reading it as flakiness would dismiss a real " +
            "interaction as luck." }),
      ]));

      var seen = {};
      list.forEach(function (entry) {
        var m = entry.m;
        var section = ctx.h("div", { class: "vz-metric" });
        section.appendChild(ctx.h("div", { class: "vz-metric-head" }, [
          ctx.h("b", { text: entry.label }),
          ctx.h("span", { class: "vz-sub", text: ctx.fmt.pct(num(m.residual), 0) + " residual" }),
          isNum(m.df_residual)
            ? ctx.h("span", { class: "vz-sub mono", text: "df " + ctx.fmt.int(m.df_residual) })
            : null,
        ]));
        section.appendChild(ctx.h("div", { class: "vz-svg-wrap" }, [drawResidual(ctx, m, plan)]));
        // The engine names what the residual can contain; that name is the
        // whole interpretation, so it is printed verbatim beside the bar.
        if (str(m.residual_meaning)) {
          section.appendChild(ctx.h("div", { class: "vz-sub", style: { marginTop: "5px" },
            text: "Residual here means: " + m.residual_meaning }));
        }
        if (str(m.caveat)) seen[m.caveat] = arr(seen[m.caveat]).concat([entry.label]);
        el.appendChild(section);
      });

      if (isNum(plan.runs)) {
        var repeats = obj(plan.repeats_per_cell) || {};
        el.appendChild(ctx.h("div", { class: "vz-sub", style: { marginTop: "10px" },
          text: ctx.fmt.int(plan.runs) + " runs over " +
            (isNum(repeats.cells) ? ctx.fmt.int(repeats.cells) + " cells" : "an unrecorded number of cells") +
            (isNum(repeats.min) && isNum(repeats.max)
              ? ", " + (repeats.min === repeats.max
                  ? repeats.min + (repeats.min === 1 ? " run" : " runs")
                  : repeats.min + "–" + repeats.max + " runs") + " per cell."
              : ".") }));
      }

      Object.keys(seen).forEach(function (text) {
        var cav = caveatNode(ctx, text, "Caveat (" + seen[text].join(", ") + "):");
        if (cav) el.appendChild(cav);
      });
    },
  });

})(typeof window !== "undefined" ? window : this);
