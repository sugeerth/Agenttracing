/* AgentDiff blocks — evidence & uncertainty.
 *
 * Six blocks that answer the questions a diff alone cannot: did the model
 * know it was going wrong, would a confidence gate have caught it, what did
 * each agent actually claim, where did the claim come from, which run
 * attributes travel with failure, and do those attributes survive controlling
 * for each other.
 *
 * Every figure here means something narrower than it looks, so every figure
 * here is rendered with its qualifier attached: synthetic demo telemetry is
 * labelled as such, an entropy floor is not called an estimate, a lift that
 * reverses under within-task stratification is marked as the artifact it is,
 * an unreliable fit says so above its own coefficients, and denominators are
 * always on screen next to the rates computed from them.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style
  // The shell cannot be edited from a module, so the few primitives these
  // blocks need beyond .kv/.tag/.grid are appended once, guarded.

  var styleInjected = false;
  function ensureStyle() {
    if (styleInjected) return;
    styleInjected = true;
    try {
      if (document.getElementById("agentdiff-signal-style")) return;
      var node = document.createElement("style");
      node.id = "agentdiff-signal-style";
      node.textContent = [
        ".sig-note{border:1px solid var(--rule);border-left-width:3px;border-radius:7px;",
        "padding:7px 9px;font-size:12px;line-height:1.45;margin:0 0 9px;",
        "background:var(--surface-2);color:var(--ink-2)}",
        ".sig-note strong{color:var(--ink)}",
        ".sig-note.bad{border-left-color:var(--bad)}",
        ".sig-note.warn{border-left-color:var(--warn)}",
        ".sig-note.good{border-left-color:var(--good)}",
        ".sig-note.info{border-left-color:var(--rule-2)}",
        ".sig-note + .sig-note{margin-top:-3px}",
        ".sig-item{border-top:1px solid var(--rule);padding:7px 0}",
        ".sig-item:first-child{border-top:none;padding-top:2px}",
        ".sig-line{font-size:12.5px;color:var(--ink)}",
        ".sig-sub{font-size:11.5px;color:var(--ink-3);line-height:1.45}",
        ".sig-chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;align-items:center}",
        ".sig-legend{display:flex;flex-wrap:wrap;gap:11px;font-size:11px;",
        "color:var(--ink-3);margin-top:7px}",
        ".sig-dot{display:inline-block;width:9px;height:9px;border-radius:50%;",
        "vertical-align:-1px;margin-right:4px}",
        ".sig-ring{display:inline-block;width:9px;height:9px;border-radius:50%;",
        "vertical-align:-1px;margin-right:4px;border:1.5px solid currentColor}",
        ".sig-h{font-size:11px;text-transform:uppercase;letter-spacing:.06em;",
        "color:var(--ink-3);margin:11px 0 5px}",
        ".sig-h:first-child{margin-top:0}",
        ".sig-more{margin-top:8px}",
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is a nicety; the blocks still read without it */ }
  }

  // ------------------------------------------------------------------ utils

  function isNum(v) { return typeof v === "number" && !isNaN(v); }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === "object" ? v : null; }

  function agentName(report, side) {
    var run = report && report[side];
    var agent = run && run.agent;
    if (agent && agent.name) return agent.name;
    return side === "a" ? "Agent A" : "Agent B";
  }

  function stepsOf(report, side) {
    var run = report && report[side];
    return run ? arr(run.steps) : [];
  }

  /* What produced these confidences, in the traces' own words.
   *
   * The demo corpus carries source: "synthetic-demo" — numbers invented to
   * exercise the analysis. Presenting those as a measurement is the single
   * most misleading thing this block could do, so the sources are collected
   * from the steps themselves rather than assumed. */
  function telemetryProvenance(report) {
    var sources = [];
    var bases = [];
    var scored = 0;
    ["a", "b"].forEach(function (side) {
      stepsOf(report, side).forEach(function (step) {
        var model = obj(step && step.model);
        if (!model) return;
        scored++;
        var source = model.source;
        if (typeof source === "string" && source && sources.indexOf(source) < 0) {
          sources.push(source);
        }
        var basis = model.entropy_basis;
        if (typeof basis === "string" && basis && bases.indexOf(basis) < 0) {
          bases.push(basis);
        }
      });
    });
    var synthetic = sources.some(function (s) {
      return s.toLowerCase().indexOf("synthetic") >= 0 || s.toLowerCase().indexOf("demo") >= 0;
    });
    return { sources: sources, bases: bases, scored: scored, synthetic: synthetic };
  }

  function note(ctx, kind, kids) {
    return ctx.h("div", { class: "sig-note " + kind }, kids);
  }

  function tag(ctx, cls, text) {
    return ctx.h("span", { class: cls ? "tag " + cls : "tag", text: text });
  }

  function pct(ctx, value, places) {
    return isNum(value) ? ctx.fmt.pct(value, places === undefined ? 1 : places) : "—";
  }

  function points(value) {
    // Confidence differences read as percentage points, not percentages.
    return (Math.abs(value) * 100).toFixed(1) + " points";
  }

  function svgText(ctx, x, y, text, fill, size, anchor, weight) {
    return ctx.svg("text", {
      x: x, y: y, fill: fill, "font-size": size || 9,
      "text-anchor": anchor || "start",
      "font-family": "var(--sans)",
      "font-weight": weight || null,
    }, [text]);
  }

  /* An SVG that fills its card but stops growing.
   *
   * These cards live in a 1-to-3 column layout, so the same chart may get
   * 300px or 1000px. Scaling a 320-unit viewBox to the full width of a
   * one-column layout would blow the labels up to headline size, so the
   * drawing is capped near its natural scale instead. */
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

  function truncate(ctx, text, max) {
    return ctx.fmt.truncate(text === null || text === undefined ? "" : String(text), max);
  }

  // ================================================================= 1. confidence

  AgentDiff.block({
    id: "confidence",
    title: "Model confidence",
    question: "Did the model know it was going wrong?",
    group: "signal",
    size: "wide",

    relevance: function (ctx) {
      var u = obj(ctx.report && ctx.report.uncertainty);
      if (!u || u.available === false) return 0;
      var hasSeries = arr(u.a && u.a.series).length || arr(u.b && u.b.series).length;
      if (!hasSeries) return 0;
      var signal = obj(u.signal);
      if (!signal) return 0.45;
      return signal.verdict === "flagged" ? 0.95 : 0.88;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var u = obj(report && report.uncertainty);
      if (!u) return ctx.empty(el, "This report carries no model telemetry, so there is no confidence to plot.");
      if (u.available === false) {
        return ctx.empty(el, "Model telemetry unavailable" +
          (typeof u.reason === "string" && u.reason ? ": " + u.reason : "") +
          ". No steps carried per-token probabilities.");
      }
      var seriesA = arr(u.a && u.a.series);
      var seriesB = arr(u.b && u.b.series);
      if (!seriesA.length && !seriesB.length) {
        return ctx.empty(el, "Telemetry is available for this batch but no step on either run was scored.");
      }

      var prov = telemetryProvenance(report);
      var signal = obj(u.signal);

      // ---- the label on the number, before the number ------------------
      if (prov.synthetic) {
        el.appendChild(note(ctx, "warn", [
          ctx.h("strong", { text: "Synthetic demo telemetry — not a measurement. " }),
          ctx.h("span", {
            text: "Every scored step on this run reports source " +
              prov.sources.join(", ") + ". These confidences were generated to " +
              "exercise the analysis; they are not provider logprobs, and no " +
              "conclusion about a real model follows from them. On a real trace " +
              "the same block reads logprobs from the serving stack.",
          }),
        ]));
      } else if (prov.sources.length) {
        el.appendChild(note(ctx, "info", [
          ctx.h("strong", { text: "Telemetry source: " }),
          ctx.h("span", { text: prov.sources.join(", ") + " — mean per-token probability over the text each step generated." }),
        ]));
      }

      // ---- verdict, in plain language ----------------------------------
      if (signal) {
        var failed = signal.failed_agent === "a" ? "a" : "b";
        var who = agentName(report, failed);
        var flagged = signal.verdict === "flagged";
        var drop = isNum(signal.drop) ? signal.drop : null;
        var lead = isNum(signal.lead_steps) ? signal.lead_steps : null;

        var sentence;
        if (flagged) {
          sentence = who + " signalled it. Confidence at the step that caused the " +
            "failure (step " + signal.root_cause_step + ") was " +
            pct(ctx, signal.confidence_at_root) + ", against its own baseline of " +
            pct(ctx, signal.baseline_confidence) +
            (drop !== null ? " — " + points(drop) + " below baseline" : "") + ". " +
            (lead === 0 || lead === null
              ? "The drop appears at the failing step itself."
              : "The drop began " + lead + " step(s) earlier.") +
            " A runtime confidence gate would have caught this run.";
        } else {
          sentence = who + " gave no warning. It was " +
            pct(ctx, signal.confidence_at_root) + " confident at the step that caused " +
            "the failure (step " + signal.root_cause_step + ") — " +
            (drop !== null && drop < 0
              ? points(drop) + " above its own baseline of " + pct(ctx, signal.baseline_confidence)
              : "no lower than its own baseline of " + pct(ctx, signal.baseline_confidence)) +
            ". No threshold on this model's own uncertainty catches this class of failure.";
        }

        el.appendChild(note(ctx, flagged ? "good" : "bad", [
          tag(ctx, flagged ? "good" : "bad", flagged ? "flagged" : "silent"),
          ctx.h("span", { text: "  " + sentence }),
        ]));
        if (typeof signal.mitigation === "string" && signal.mitigation) {
          el.appendChild(ctx.h("div", { class: "sig-sub", text: "Mitigation: " + signal.mitigation }));
        }
      } else {
        el.appendChild(note(ctx, "info", [
          ctx.h("strong", { text: "No verdict for this task. " }),
          ctx.h("span", {
            text: "No failure was attributed to a step carrying telemetry — both runs " +
              "succeeded, both failed, or the failing step is unscored. The lines below " +
              "are each run's confidence and nothing more.",
          }),
        ]));
      }

      // ---- the plot -----------------------------------------------------
      var color = ctx.color;
      var legend = ctx.h("div", { class: "sig-legend" }, [
        ctx.h("span", null, [
          ctx.h("i", { class: "sig-dot", style: { background: color.a } }),
          agentName(report, "a"),
        ]),
        ctx.h("span", null, [
          ctx.h("i", { class: "sig-dot", style: { background: color.b } }),
          agentName(report, "b"),
        ]),
        signal ? ctx.h("span", { style: { color: color.bad }, text: "▎ root cause step " + signal.root_cause_step }) : null,
      ]);

      var W = 320, H = 150;
      var pad = { l: 27, r: 6, t: 9, b: 19 };
      var values = seriesA.concat(seriesB).filter(isNum);
      var lo = Math.max(0, Math.floor((Math.min.apply(null, values) - 0.04) * 20) / 20);
      var hi = Math.min(1, Math.ceil((Math.max.apply(null, values) + 0.04) * 20) / 20);
      if (hi - lo < 0.05) { hi = Math.min(1, lo + 0.1); }
      var lastIndex = Math.max(seriesA.length, seriesB.length) - 1;
      var x = scale(0, lastIndex || 1, pad.l, W - pad.r);
      var y = scale(lo, hi, H - pad.b, pad.t);

      var plot = chart(ctx, W, H);
      [lo, (lo + hi) / 2, hi].forEach(function (value) {
        plot.appendChild(ctx.svg("line", {
          x1: pad.l, x2: W - pad.r, y1: y(value), y2: y(value),
          stroke: color.grid, "stroke-width": 1,
        }));
        plot.appendChild(svgText(ctx, pad.l - 4, y(value) + 3, ctx.fmt.pct(value, 0), color.muted, 8.5, "end"));
      });

      // step-index ticks, thinned so a ten-step run does not become a smear
      var tickEvery = lastIndex > 12 ? Math.ceil(lastIndex / 6) : (lastIndex > 6 ? 2 : 1);
      for (var i = 0; i <= lastIndex; i += tickEvery) {
        plot.appendChild(svgText(ctx, x(i), H - pad.b + 11, String(i), color.muted, 8.5, "middle"));
      }
      plot.appendChild(svgText(ctx, W - pad.r, H - 2, "step index", color.muted, 8.5, "end"));

      if (signal && isNum(signal.root_cause_step) && signal.root_cause_step <= lastIndex) {
        plot.appendChild(ctx.svg("line", {
          x1: x(signal.root_cause_step), x2: x(signal.root_cause_step),
          y1: pad.t - 2, y2: H - pad.b,
          stroke: color.bad, "stroke-width": 1.2, "stroke-dasharray": "3 2",
        }));
      }

      function drawSeries(series, stroke) {
        // Break the line at unscored steps rather than bridging them: a
        // straight segment across a null claims a measurement that was never
        // taken.
        var run = [];
        function flush() {
          if (run.length > 1) {
            plot.appendChild(ctx.svg("polyline", {
              points: run.map(function (p) { return p[0] + "," + p[1]; }).join(" "),
              fill: "none", stroke: stroke, "stroke-width": 1.6,
              "stroke-linejoin": "round", "stroke-linecap": "round",
            }));
          }
          run = [];
        }
        series.forEach(function (value, index) {
          if (!isNum(value)) { flush(); return; }
          run.push([x(index), y(value)]);
          plot.appendChild(ctx.svg("circle", {
            cx: x(index), cy: y(value), r: 2, fill: stroke,
          }));
        });
        flush();
      }
      drawSeries(seriesA, color.a);
      drawSeries(seriesB, color.b);

      if (signal && isNum(signal.confidence_at_root) && isNum(signal.root_cause_step) &&
          signal.root_cause_step <= lastIndex) {
        plot.appendChild(ctx.svg("circle", {
          cx: x(signal.root_cause_step), cy: y(signal.confidence_at_root),
          r: 4.5, fill: "none", stroke: color.bad, "stroke-width": 1.6,
        }));
      }

      el.appendChild(ctx.h("div", { class: "scroll-x" }, [plot]));
      el.appendChild(legend);

      // ---- the numbers, each with its unit -------------------------------
      var basisNote = null;
      if (prov.bases.length) {
        basisNote = prov.bases.indexOf("binary_floor") >= 0
          ? "entropy basis " + prov.bases.join(", ") +
            " — with only the chosen token's logprob, entropy is the binary entropy of that probability: a floor, not an estimate."
          : "entropy basis " + prov.bases.join(", ") +
            " — computed over the returned top-k distribution, so the truncated tail makes it approximate.";
      }

      var kv = ctx.h("dl", { class: "kv" });
      [["a", seriesA], ["b", seriesB]].forEach(function (pair) {
        var side = pair[0];
        var stats = obj(u[side]);
        if (!stats) return;
        kv.appendChild(ctx.h("dt", null, [tag(ctx, side, agentName(report, side))]));
        kv.appendChild(ctx.h("dd", {
          text: "mean " + pct(ctx, stats.mean_confidence) +
            " · low " + pct(ctx, stats.min_confidence) +
            " · worst token " + pct(ctx, stats.min_token_confidence) +
            " · entropy " + (isNum(stats.mean_entropy) ? ctx.fmt.num(stats.mean_entropy, 2) + " nats" : "—") +
            " · " + (isNum(stats.steps_scored) ? stats.steps_scored : 0) + " step(s) scored",
        }));
      });
      el.appendChild(kv);

      if (basisNote) el.appendChild(ctx.h("div", { class: "caveat", text: basisNote }));
      if (typeof u.narrative === "string" && u.narrative) {
        el.appendChild(ctx.h("div", { class: "caveat", text: u.narrative }));
      }
      el.appendChild(ctx.h("div", {
        class: "caveat",
        text: "Confidence is the mean per-token probability of the text a step generated. " +
          "It measures the model's fluency over its own output, not whether the output is true.",
      }));
    },
  });

  // ================================================================ 2. calibration

  AgentDiff.block({
    id: "calibration",
    title: "Confidence when wrong",
    question: "Was it confident when it was wrong — would a gate have caught it?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var u = obj(ctx.report && ctx.report.uncertainty);
      var local = u ? obj(u.calibration) : null;
      var batch = obj(ctx.aggregate && ctx.aggregate.calibration);
      if (!local && (!batch || batch.available === false)) return 0;
      if (local && local.confident_when_wrong) return 0.9;
      return local ? 0.7 : 0.55;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var u = obj(report && report.uncertainty);
      var local = u ? obj(u.calibration) : null;
      var signal = u ? obj(u.signal) : null;
      var batch = obj(ctx.aggregate && ctx.aggregate.calibration);
      var hasBatch = batch && batch.available !== false && obj(batch.agents);

      if (!local && !hasBatch) {
        return ctx.empty(el, "No failure on this task was attributed to a step carrying " +
          "telemetry, and the batch has no calibration summary — so there is nothing to " +
          "calibrate against.");
      }

      if (local && signal) {
        var side = signal.failed_agent === "a" ? "a" : "b";
        var who = agentName(report, side);
        var conf = isNum(local.confidence_at_wrong_step)
          ? local.confidence_at_wrong_step : signal.confidence_at_root;

        el.appendChild(note(ctx, local.confident_when_wrong ? "bad" : "good", [
          tag(ctx, local.confident_when_wrong ? "bad" : "good",
            local.confident_when_wrong ? "confident when wrong" : "uncertain when wrong"),
          ctx.h("span", {
            text: "  " + who + " was " + pct(ctx, conf) + " confident at the step that " +
              "caused the failure (step " + signal.root_cause_step + "), against a run " +
              "baseline of " + pct(ctx, signal.baseline_confidence) + ".",
          }),
        ]));

        // Would a gate have caught it? Answered from this run's own series,
        // and only as far as this run's own series can answer it.
        var series = arr(u[side] && u[side].series);
        var others = [];
        series.forEach(function (value, index) {
          if (index !== signal.root_cause_step && isNum(value)) others.push(value);
        });
        var gate = ctx.h("div");
        if (!isNum(conf) || !others.length) {
          gate.appendChild(ctx.h("div", {
            class: "sig-line",
            text: "Too few scored steps on this run to say where a threshold would sit.",
          }));
        } else {
          var minOther = Math.min.apply(null, others);
          var falseAlarms = others.filter(function (v) { return v <= conf; }).length;
          if (conf < minOther) {
            var threshold = (conf + minOther) / 2;
            gate.appendChild(note(ctx, "warn", [
              ctx.h("strong", { text: "A gate at " + ctx.fmt.pct(threshold, 1) + " would have caught this step and fired nowhere else on this run. " }),
              ctx.h("span", {
                text: "That threshold was chosen after seeing which step failed — it is " +
                  "hindsight on one run, not a validated gate. The batch view below is " +
                  "the honest version of the question.",
              }),
            ]));
          } else {
            gate.appendChild(note(ctx, "bad", [
              ctx.h("strong", { text: "No threshold would have caught this. " }),
              ctx.h("span", {
                text: "Any gate low enough to fire at the failing step (" + pct(ctx, conf) +
                  ") would first have fired on " + falseAlarms + " other scored step(s) " +
                  "that were fine — this run went as low as " + ctx.fmt.pct(minOther, 1) +
                  " without failing. Confidence does not separate the failure from the " +
                  "rest of the run; only external verification does.",
              }),
            ]));
          }
        }
        el.appendChild(gate);
      } else if (local) {
        el.appendChild(note(ctx, "info", [
          ctx.h("span", {
            text: "Calibration recorded for this task, but without an attributed failing " +
              "step there is no gate question to answer.",
          }),
        ]));
      } else {
        el.appendChild(note(ctx, "info", [
          ctx.h("span", {
            text: "This task has no attributed failure with telemetry. The batch summary " +
              "below covers every task that does.",
          }),
        ]));
      }

      if (!hasBatch) return;

      el.appendChild(ctx.h("div", { class: "sig-h", text: "Across the batch" }));
      var table = ctx.h("table", { class: "grid" }, [
        ctx.h("tr", null, [
          ctx.h("th", { text: "Agent" }),
          ctx.h("th", { class: "num", text: "Failures scored" }),
          ctx.h("th", { class: "num", text: "Flagged" }),
          ctx.h("th", { class: "num", text: "Silent" }),
          ctx.h("th", { class: "num", text: "Conf. when wrong" }),
          ctx.h("th", { text: "Verdict" }),
        ]),
      ]);
      var thin = [];
      Object.keys(batch.agents).forEach(function (name) {
        var row = obj(batch.agents[name]) || {};
        var supervisable = row.verdict === "supervisable";
        if (isNum(row.failures_with_telemetry) && row.failures_with_telemetry < 5) thin.push(name);
        table.appendChild(ctx.h("tr", null, [
          ctx.h("td", { text: name }),
          ctx.h("td", { class: "num", text: ctx.fmt.int(row.failures_with_telemetry) }),
          ctx.h("td", { class: "num", text: ctx.fmt.int(row.flagged) }),
          ctx.h("td", { class: "num", text: ctx.fmt.int(row.silent) }),
          ctx.h("td", { class: "num", text: pct(ctx, row.mean_confidence_when_wrong) }),
          ctx.h("td", null, [tag(ctx, supervisable ? "good" : "bad", row.verdict || "—")]),
        ]));
      });
      el.appendChild(ctx.h("div", { class: "scroll-x" }, [table]));

      if (thin.length) {
        el.appendChild(ctx.h("div", {
          class: "caveat",
          text: "Fewer than 5 scored failures for " + thin.join(", ") +
            " — a flagged rate on that few failures is a hint, not a rate.",
        }));
      }
      if (typeof batch.narrative === "string" && batch.narrative) {
        el.appendChild(ctx.h("div", { class: "caveat", text: batch.narrative }));
      }
      el.appendChild(ctx.h("div", {
        class: "caveat",
        text: "Only failures whose root-cause step carried telemetry are counted; " +
          "unscored failures are excluded rather than assumed silent.",
      }));
    },
  });

  // ===================================================================== 3. claims

  AgentDiff.block({
    id: "claims",
    title: "Claims",
    question: "What did each agent claim, and does it match?",
    group: "signal",
    size: "tall",

    relevance: function (ctx) {
      var s = obj(ctx.report && ctx.report.semantic);
      if (!s) return 0;
      var claims = arr(s.claims);
      if (!claims.length) return 0;
      if (arr(s.conflicts).length) return 0.92;
      var decided = claims.filter(function (c) { return c.matches_expected !== null && c.matches_expected !== undefined; });
      return decided.length ? 0.75 : 0.5;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var s = obj(report && report.semantic);
      if (!s) return ctx.empty(el, "No semantic analysis on this report.");
      var claims = arr(s.claims);
      if (!claims.length) {
        return ctx.empty(el, "No typed claims were extracted from either trajectory — " +
          "nothing here carried a money figure, date, version, URL or number to compare.");
      }

      var byId = {};
      claims.forEach(function (claim) { if (claim && claim.id) byId[claim.id] = claim; });

      // ---- conflicts first: they are the reason to read this block ------
      var conflicts = arr(s.conflicts);
      var conflicted = {};
      conflicts.forEach(function (conflict) {
        if (conflict.a_claim) conflicted[conflict.a_claim] = true;
        if (conflict.b_claim) conflicted[conflict.b_claim] = true;
      });

      if (conflicts.length) {
        el.appendChild(ctx.h("div", { class: "sig-h", text: conflicts.length + " conflicting claim(s)" }));
        conflicts.forEach(function (conflict) {
          el.appendChild(note(ctx, "bad", [
            tag(ctx, "bad", conflict.kind || "conflict"),
            ctx.h("span", { text: "  " + (conflict.summary || "the two runs carried different values") }),
          ]));
        });
      } else {
        el.appendChild(note(ctx, "good", [
          ctx.h("span", { text: "No same-kind claim was carried with different values into the two answers." }),
        ]));
      }

      // ---- claims grouped by kind ---------------------------------------
      var kinds = [];
      var groups = {};
      claims.forEach(function (claim) {
        var kind = claim.kind || "other";
        if (!groups[kind]) { groups[kind] = []; kinds.push(kind); }
        groups[kind].push(claim);
      });
      // Kinds that actually decided something — a conflict, or a claim the
      // expected answer can judge — come first; a pile of incidental URLs
      // must not push the money figure below the fold.
      function weight(kind) {
        return groups[kind].filter(function (claim) {
          return conflicted[claim.id] || claim.matches_expected === true ||
            claim.matches_expected === false;
        }).length;
      }
      kinds.sort(function (x, y) {
        return (weight(y) - weight(x)) || (groups[y].length - groups[x].length) ||
          (x < y ? -1 : x > y ? 1 : 0);
      });

      var LIMIT = 8;
      var shown = 0;
      var hiddenCount = 0;
      var body = ctx.h("div");

      function renderClaim(claim) {
        var steps = ctx.h("div", { class: "sig-chips" });
        var aSteps = arr(claim.a_steps);
        var bSteps = arr(claim.b_steps);
        steps.appendChild(tag(ctx, "a", agentName(report, "a") + ": " +
          (aSteps.length ? "step " + aSteps.join(", ") : "never")));
        steps.appendChild(tag(ctx, "b", agentName(report, "b") + ": " +
          (bSteps.length ? "step " + bSteps.join(", ") : "never")));

        if (claim.matches_expected === true) steps.appendChild(tag(ctx, "good", "matches expected"));
        else if (claim.matches_expected === false) steps.appendChild(tag(ctx, "bad", "differs from expected"));
        else steps.appendChild(tag(ctx, "", "no expected value of this kind"));
        if (conflicted[claim.id]) steps.appendChild(tag(ctx, "bad", "in conflict"));

        var origin = obj(claim.origin);
        var originText = origin
          ? "first carried by " + agentName(report, origin.agent === "b" ? "b" : "a") +
            " at step " + origin.step + (origin.source ? " · " + origin.source : "")
          : "no recorded origin step";

        var normalized = claim.normalized !== undefined && claim.normalized !== null &&
          String(claim.normalized) !== String(claim.value)
          ? " (" + claim.normalized + ")" : "";

        return ctx.h("div", { class: "sig-item" }, [
          ctx.h("div", { class: "sig-line" }, [
            ctx.h("strong", { text: truncate(ctx, claim.value, 64) }),
            normalized ? ctx.h("span", { class: "mono", text: normalized }) : null,
          ]),
          ctx.h("div", { class: "sig-sub", text: originText }),
          steps,
        ]);
      }

      kinds.forEach(function (kind) {
        var group = groups[kind];
        var head = ctx.h("div", { class: "sig-h", text: kind + " · " + group.length });
        var wrap = ctx.h("div");
        var any = false;
        group.forEach(function (claim) {
          var important = conflicted[claim.id] ||
            claim.matches_expected === true || claim.matches_expected === false;
          if (shown >= LIMIT && !important) { hiddenCount++; return; }
          shown++;
          any = true;
          wrap.appendChild(renderClaim(claim));
        });
        if (any) { body.appendChild(head); body.appendChild(wrap); }
        else hiddenCount += 0;
      });
      el.appendChild(body);

      if (hiddenCount) {
        var more = ctx.h("button", {
          class: "btn sig-more",
          text: "Show " + hiddenCount + " more claim(s)",
          onclick: function () {
            ctx.signal("expand");
            more.remove();
            var rest = ctx.h("div");
            kinds.forEach(function (kind) {
              var group = groups[kind].filter(function (claim) {
                return !(conflicted[claim.id] || claim.matches_expected === true ||
                  claim.matches_expected === false);
              });
              if (!group.length) return;
              rest.appendChild(ctx.h("div", { class: "sig-h", text: kind + " · remaining" }));
              group.forEach(function (claim) { rest.appendChild(renderClaim(claim)); });
            });
            el.insertBefore(rest, el.lastChild);
          },
        });
        el.appendChild(more);
      }

      var contradictions = arr(s.contradictions);
      if (contradictions.length) {
        el.appendChild(ctx.h("div", { class: "sig-h", text: "Internal contradictions" }));
        contradictions.forEach(function (row) {
          el.appendChild(note(ctx, "bad", [
            tag(ctx, row.agent === "a" ? "a" : "b", agentName(report, row.agent === "a" ? "a" : "b")),
            ctx.h("span", {
              text: "  " + (row.summary || "carried conflicting values within one trajectory") +
                (arr(row.values).length ? " — " + arr(row.values).join(" vs ") : "") +
                (arr(row.steps).length ? " (steps " + arr(row.steps).join(", ") + ")" : ""),
            }),
          ]));
        });
      }

      el.appendChild(ctx.h("div", {
        class: "caveat",
        text: "Claims are typed facts extracted from step text; matches expected is only " +
          "decided when the expected answer contains a comparable claim of the same kind, " +
          "so a blank there means unjudged, not wrong.",
      }));
      if (typeof s.narrative === "string" && s.narrative) {
        el.appendChild(ctx.h("div", { class: "caveat", text: s.narrative }));
      }
    },
  });

  // ================================================================= 4. provenance

  AgentDiff.block({
    id: "provenance",
    title: "Provenance",
    question: "Where did the claims come from — and is the corroboration real?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var s = obj(ctx.report && ctx.report.semantic);
      if (!s) return 0;
      var independence = arr(s.independence);
      var grounding = obj(s.grounding);
      if (!independence.length && !grounding) return 0;
      var circular = independence.filter(function (row) { return row.circular; }).length;
      if (circular) return 0.94;
      if (independence.length) return 0.6;
      return 0.4;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var s = obj(report && report.semantic);
      if (!s) return ctx.empty(el, "No semantic analysis on this report.");
      var independence = arr(s.independence);
      var grounding = obj(s.grounding);
      if (!independence.length && !grounding) {
        return ctx.empty(el, "No provenance recorded: no claim on either side was traced " +
          "to a source, so there is nothing to corroborate.");
      }

      var byId = {};
      arr(s.claims).forEach(function (claim) { if (claim && claim.id) byId[claim.id] = claim; });

      var circular = independence.filter(function (row) { return row.circular; });

      if (circular.length) {
        el.appendChild(note(ctx, "bad", [
          tag(ctx, "bad", "circular corroboration"),
          ctx.h("strong", { text: "  " + circular.length + " claim(s) corroborated by a source that cites the first. " }),
          ctx.h("span", {
            text: "Two quotes, one voice: a second source that traces back to the same " +
              "origin is not independent evidence, and the second citation adds no " +
              "confidence whatsoever.",
          }),
        ]));
      }

      if (independence.length) {
        el.appendChild(ctx.h("div", { class: "sig-h", text: "Corroboration chains" }));
        independence.forEach(function (row) {
          var claim = byId[row.claim];
          var side = row.agent === "a" ? "a" : "b";
          var chips = ctx.h("div", { class: "sig-chips" }, [
            tag(ctx, side, agentName(report, side)),
          ]);
          arr(row.sources).forEach(function (source, index) {
            chips.appendChild(tag(ctx, row.circular ? "bad" : "good",
              (index === 0 ? "origin: " : "corroborator: ") + source));
          });
          chips.appendChild(tag(ctx, row.circular ? "bad" : "good",
            row.circular ? "not independent" : "independent"));

          var item = ctx.h("div", { class: "sig-item" }, [
            ctx.h("div", { class: "sig-line" }, [
              ctx.h("strong", { text: claim ? truncate(ctx, claim.value, 60) : String(row.claim) }),
              claim && claim.kind ? ctx.h("span", { class: "sig-sub", text: "  " + claim.kind }) : null,
            ]),
            chips,
          ]);
          if (row.evidence) {
            item.appendChild(ctx.h("div", {
              class: row.circular ? "sig-note bad" : "sig-sub",
              style: row.circular ? { marginTop: "6px", marginBottom: "0" } : null,
              text: truncate(ctx, row.evidence, 240),
            }));
          }
          el.appendChild(item);
        });
      } else {
        el.appendChild(note(ctx, "info", [
          ctx.h("span", {
            text: "No claim on either side was backed by a second source, so the " +
              "independence check had nothing to test. A single-source claim is not " +
              "corroborated — it is simply uncorroborated.",
          }),
        ]));
      }

      if (grounding) {
        el.appendChild(ctx.h("div", { class: "sig-h", text: "Answer grounding" }));
        var kv = ctx.h("dl", { class: "kv" });
        ["a", "b"].forEach(function (side) {
          var row = obj(grounding[side]);
          if (!row) return;
          kv.appendChild(ctx.h("dt", null, [tag(ctx, side, agentName(report, side))]));
          kv.appendChild(ctx.h("dd", {
            text: ctx.fmt.int(row.claims_grounded) + " of " + ctx.fmt.int(row.claims_total) +
              " answer claim(s) trace to a step output" +
              (isNum(row.score) ? " · " + ctx.fmt.pct(row.score, 0) : ""),
          }));
        });
        el.appendChild(kv);

        ["a", "b"].forEach(function (side) {
          var row = obj(grounding[side]);
          var ungrounded = row ? arr(row.ungrounded) : [];
          if (!ungrounded.length) return;
          var chips = ctx.h("div", { class: "sig-chips" }, [
            tag(ctx, side, agentName(report, side) + " ungrounded:"),
          ]);
          ungrounded.forEach(function (item) {
            chips.appendChild(tag(ctx, "bad", truncate(ctx, item.value || item.claim, 40)));
          });
          el.appendChild(chips);
        });
      }

      var profile = obj(ctx.aggregate && ctx.aggregate.semantic_profile);
      if (profile && (obj(profile.a) || obj(profile.b))) {
        el.appendChild(ctx.h("div", { class: "sig-h", text: "Across the batch" }));
        var table = ctx.h("table", { class: "grid" }, [
          ctx.h("tr", null, [
            ctx.h("th", { text: "Agent" }),
            ctx.h("th", { class: "num", text: "Verifies" }),
            ctx.h("th", { class: "num", text: "Grounding" }),
            ctx.h("th", { class: "num", text: "Circular" }),
            ctx.h("th", { class: "num", text: "Contradictions" }),
          ]),
        ]);
        ["a", "b"].forEach(function (side) {
          var row = obj(profile[side]);
          if (!row) return;
          table.appendChild(ctx.h("tr", null, [
            ctx.h("td", { text: agentName(report, side) }),
            ctx.h("td", { class: "num", text: pct(ctx, row.verification_rate, 0) }),
            ctx.h("td", { class: "num", text: pct(ctx, row.grounding, 0) }),
            ctx.h("td", { class: "num", text: ctx.fmt.int(row.circular_incidents) }),
            ctx.h("td", { class: "num", text: ctx.fmt.int(row.contradictions) }),
          ]));
        });
        el.appendChild(ctx.h("div", { class: "scroll-x" }, [table]));
      }

      el.appendChild(ctx.h("div", {
        class: "caveat",
        text: "Grounding says a claim traces to some step output — that provenance exists, " +
          "not that the source is right. Circularity is detected by the corroborating " +
          "source's own text citing the first; a chain the trace never recorded cannot be checked.",
      }));
    },
  });

  // ================================================================= 5. attributes

  AgentDiff.block({
    id: "attributes",
    title: "Attributes & failure",
    question: "Which run attributes go with failure?",
    group: "signal",
    size: "tall",

    relevance: function (ctx) {
      var at = obj(ctx.aggregate && ctx.aggregate.attributes);
      if (!at) return 0;
      var rows = arr(at.attributes);
      if (!rows.length) return 0;
      var reverses = rows.filter(function (row) { return row.reverses_under_stratification; }).length;
      if (reverses) return 0.95;
      return isNum(at.notable) && at.notable > 0 ? 0.8 : 0.45;
    },

    render: function (el, ctx) {
      ensureStyle();
      var at = obj(ctx.aggregate && ctx.aggregate.attributes);
      if (!at) return ctx.empty(el, "No attribute analysis in this batch — it needs a corpus of runs, not one pair.");
      var rows = arr(at.attributes).filter(function (row) { return row && isNum(row.lift); });
      if (!rows.length) {
        return ctx.empty(el, "No attribute could be measured across this corpus.");
      }

      var color = ctx.color;
      var runs = isNum(at.runs) ? at.runs : null;
      var failures = isNum(at.failures) ? at.failures : null;
      var reversed = rows.filter(function (row) { return row.reverses_under_stratification; });

      // ---- denominator, up front ----------------------------------------
      el.appendChild(note(ctx, failures !== null && failures < 10 ? "warn" : "info", [
        ctx.h("strong", {
          text: "Denominator: " + ctx.fmt.int(runs) + " run(s), " +
            ctx.fmt.int(failures) + " failure(s). ",
        }),
        ctx.h("span", {
          text: failures !== null && failures < 10
            ? "Every lift below is a difference between two failure rates computed on " +
              failures + " failure(s) in total. At that size a lift is a pointer to look " +
              "somewhere, not a finding to act on — the intervals say the same thing."
            : (isNum(at.notable) ? at.notable + " attribute(s) clear the notability bar " +
              "(|lift| ≥ 0.25 and at least 2 runs on each side)." : ""),
        }),
      ]));

      // ---- the reversal, prominently ------------------------------------
      if (reversed.length) {
        el.appendChild(note(ctx, "bad", [
          tag(ctx, "bad", "reverses under stratification"),
          ctx.h("strong", {
            text: "  " + reversed.length + " attribute(s) flip sign once runs are compared within the same task: " +
              reversed.map(function (row) { return row.attribute; }).join(", ") + ". ",
          }),
          ctx.h("span", {
            text: "The marginal association points one way and the within-task association " +
              "points the other — Simpson's paradox, driven by task difficulty. The " +
              "unstratified figure for these rows is an artifact and must not be read as " +
              "a finding; the stratified figure is the one that controls for which task " +
              "the run was attempting.",
          }),
        ]));
      } else {
        el.appendChild(note(ctx, "good", [
          ctx.h("span", {
            text: "No attribute flips sign under within-task stratification — the check " +
              "for Simpson's paradox ran and found none. Rows with a stratified figure " +
              "show it as a hollow marker below.",
          }),
        ]));
      }

      // ---- forest plot ---------------------------------------------------
      var sorted = rows.slice().sort(function (x, y) {
        return Math.abs(y.lift) - Math.abs(x.lift);
      });

      var lows = [0], highs = [0];
      sorted.forEach(function (row) {
        lows.push(row.lift); highs.push(row.lift);
        var interval = obj(row.interval);
        if (interval) {
          if (isNum(interval.low)) lows.push(interval.low);
          if (isNum(interval.high)) highs.push(interval.high);
        }
        var strat = obj(row.stratified);
        if (strat && isNum(strat.lift)) { lows.push(strat.lift); highs.push(strat.lift); }
      });
      var lo = Math.min.apply(null, lows);
      var hi = Math.max.apply(null, highs);
      var span = hi - lo || 1;
      lo -= span * 0.06; hi += span * 0.06;

      var W = 320;
      var ROW = 31;
      var top = 6;
      var axis = 32;   // two lines: numeric ticks, then which way is worse
      var H = top + sorted.length * ROW + axis;
      var x = scale(lo, hi, 6, W - 6);

      var plot = chart(ctx, W, H);
      var zero = x(0);
      plot.appendChild(ctx.svg("line", {
        x1: zero, x2: zero, y1: top - 2, y2: H - axis + 2,
        stroke: color.axis, "stroke-width": 1.2,
      }));
      plot.appendChild(svgText(ctx, zero, H - axis + 12, "0", color.muted, 8.5, "middle"));
      [lo + (hi - lo) * 0.08, hi - (hi - lo) * 0.08].forEach(function (value) {
        if (Math.abs(x(value) - zero) < 26) return;
        plot.appendChild(svgText(ctx, x(value), H - axis + 12,
          (value > 0 ? "+" : "") + value.toFixed(2), color.muted, 8.5, "middle"));
      });
      plot.appendChild(svgText(ctx, 6, H - axis + 25, "← fails less often", color.muted, 8, "start"));
      plot.appendChild(svgText(ctx, W - 6, H - axis + 25, "fails more often →", color.muted, 8, "end"));

      sorted.forEach(function (row, index) {
        var yLabel = top + index * ROW + 10;
        var yLine = top + index * ROW + 22;
        var positive = row.lift > 0;
        var strong = row.notable === true;
        var stroke = !strong ? color.muted : (positive ? color.bad : color.good);
        if (row.reverses_under_stratification) stroke = color.bad;

        var label = row.attribute || "attribute";
        // A row that reverses is forced non-notable upstream, which would
        // otherwise mute exactly the row the reader most needs to see.
        var reverses = !!row.reverses_under_stratification;
        if (reverses) label = "⚠ " + label;
        var labelInk = reverses ? color.bad : (strong ? color.ink : color.muted);
        plot.appendChild(svgText(ctx, 6, yLabel, truncate(ctx, label, 30),
          labelInk, 9.5, "start", (strong || reverses) ? "600" : null));
        plot.appendChild(svgText(ctx, W - 6, yLabel,
          (row.lift > 0 ? "+" : "") + ctx.fmt.num(row.lift, 2),
          labelInk, 9.5, "end"));

        var interval = obj(row.interval);
        if (interval && isNum(interval.low) && isNum(interval.high)) {
          plot.appendChild(ctx.svg("line", {
            x1: x(interval.low), x2: x(interval.high), y1: yLine, y2: yLine,
            stroke: stroke, "stroke-width": 1.4, "stroke-opacity": 0.55,
          }));
          [interval.low, interval.high].forEach(function (value) {
            plot.appendChild(ctx.svg("line", {
              x1: x(value), x2: x(value), y1: yLine - 3.5, y2: yLine + 3.5,
              stroke: stroke, "stroke-width": 1.4, "stroke-opacity": 0.55,
            }));
          });
        }

        var strat = obj(row.stratified);
        if (strat && isNum(strat.lift)) {
          if (row.reverses_under_stratification) {
            plot.appendChild(ctx.svg("line", {
              x1: x(row.lift), x2: x(strat.lift), y1: yLine, y2: yLine,
              stroke: color.bad, "stroke-width": 1.2, "stroke-dasharray": "2 2",
            }));
          }
          // Drawn wider than the raw marker so a stratified lift that lands on
          // the same value reads as a ring around the dot rather than as a
          // missing marker.
          plot.appendChild(ctx.svg("circle", {
            cx: x(strat.lift), cy: yLine, r: 5.2, fill: "none",
            stroke: row.reverses_under_stratification ? color.bad : stroke,
            "stroke-width": 1.5,
          }));
        }

        plot.appendChild(ctx.svg("circle", {
          cx: x(row.lift), cy: yLine, r: 3.4, fill: stroke,
        }));
      });

      el.appendChild(ctx.h("div", { class: "scroll-x" }, [plot]));
      el.appendChild(ctx.h("div", { class: "sig-legend" }, [
        ctx.h("span", null, [ctx.h("i", { class: "sig-dot", style: { background: color.bad } }), "raw lift, notable"]),
        ctx.h("span", null, [ctx.h("i", { class: "sig-dot", style: { background: color.muted } }), "not notable"]),
        ctx.h("span", { style: { color: color.muted } }, [ctx.h("i", { class: "sig-ring" }), "within-task (stratified) lift"]),
        ctx.h("span", { text: "whiskers: bootstrap interval" }),
      ]));

      // ---- the numbers ---------------------------------------------------
      var table = ctx.h("table", { class: "grid", style: { minWidth: "420px" } }, [
        ctx.h("tr", null, [
          ctx.h("th", { text: "Attribute" }),
          ctx.h("th", { class: "num", text: "With" }),
          ctx.h("th", { class: "num", text: "Without" }),
          ctx.h("th", { class: "num", text: "Lift" }),
          ctx.h("th", { class: "num", text: "Interval" }),
          ctx.h("th", { class: "num", text: "Within-task" }),
        ]),
      ]);
      sorted.forEach(function (row) {
        var withRow = obj(row["with"]) || {};
        var withoutRow = obj(row.without) || {};
        var interval = obj(row.interval);
        var strat = obj(row.stratified);
        var flags = ctx.h("div", { class: "sig-chips" });
        if (row.notable === true) flags.appendChild(tag(ctx, row.lift > 0 ? "bad" : "good", "notable"));
        else flags.appendChild(tag(ctx, "", "not notable"));
        if (row.measurable === false) flags.appendChild(tag(ctx, "warn", "not measurable everywhere"));
        if (row.reverses_under_stratification) flags.appendChild(tag(ctx, "bad", "⚠ reverses within-task"));
        if (interval && interval.significant === false) flags.appendChild(tag(ctx, "", "interval spans 0"));
        if (!interval) flags.appendChild(tag(ctx, "warn", "no interval (too few runs)"));

        table.appendChild(ctx.h("tr", null, [
          ctx.h("td", null, [
            ctx.h("div", { text: row.attribute }),
            ctx.h("div", { class: "sig-sub", text: truncate(ctx, row.phrasing, 90) }),
            flags,
          ]),
          ctx.h("td", { class: "num", text: ctx.fmt.int(withRow.failures) + "/" + ctx.fmt.int(withRow.runs) }),
          ctx.h("td", { class: "num", text: ctx.fmt.int(withoutRow.failures) + "/" + ctx.fmt.int(withoutRow.runs) }),
          ctx.h("td", { class: "num", text: (row.lift > 0 ? "+" : "") + ctx.fmt.num(row.lift, 3) }),
          ctx.h("td", { class: "num", text: interval && isNum(interval.low)
            ? ctx.fmt.num(interval.low, 2) + " … " + ctx.fmt.num(interval.high, 2) : "—" }),
          ctx.h("td", { class: "num", text: strat && isNum(strat.lift)
            ? (strat.lift > 0 ? "+" : "") + ctx.fmt.num(strat.lift, 2) +
              " (" + ctx.fmt.int(strat.strata) + " strata)" : "no strata" }),
        ]));
      });
      el.appendChild(ctx.h("div", { class: "scroll-x" }, [table]));

      // ---- caveats, verbatim ---------------------------------------------
      if (typeof at.caveat === "string" && at.caveat) {
        el.appendChild(note(ctx, "warn", [
          ctx.h("strong", { text: "Association, not cause. " }),
          ctx.h("span", { text: at.caveat }),
        ]));
      }
      var stratMethod = null;
      sorted.some(function (row) {
        var strat = obj(row.stratified);
        if (strat && strat.method) { stratMethod = strat.method; return true; }
        return false;
      });
      if (stratMethod) {
        el.appendChild(ctx.h("div", {
          class: "caveat",
          text: "Within-task lift uses " + stratMethod + ": runs are compared only against " +
            "other runs of the same task, so task difficulty cannot drive the difference. " +
            "Strata where every run falls on one side carry no information and are skipped, " +
            "which is why some rows have no stratified figure at all.",
        }));
      }
      if (typeof at.narrative === "string" && at.narrative) {
        el.appendChild(ctx.h("div", { class: "caveat", text: at.narrative }));
      }
    },
  });

  // ====================================================================== 6. joint

  AgentDiff.block({
    id: "joint",
    title: "Joint attribute model",
    question: "Do those attributes survive controlling for each other?",
    group: "signal",
    size: "normal",

    relevance: function (ctx) {
      var joint = obj(ctx.aggregate && ctx.aggregate.attributes_joint);
      if (!joint || joint.available === false) return 0;
      var coefficients = arr(joint.coefficients);
      if (!coefficients.length) return 0;
      return joint.reliable === false ? 0.55 : 0.78;
    },

    render: function (el, ctx) {
      ensureStyle();
      var joint = obj(ctx.aggregate && ctx.aggregate.attributes_joint);
      if (!joint) {
        return ctx.empty(el, "No joint attribute model in this batch — it needs a corpus of runs.");
      }
      if (joint.available === false) {
        return ctx.empty(el, "The joint model did not run" +
          (typeof joint.reason === "string" && joint.reason ? ": " + joint.reason : "") + ".");
      }
      var coefficients = arr(joint.coefficients).filter(function (row) {
        return row && isNum(row.coefficient);
      });
      if (!coefficients.length) {
        return ctx.empty(el, "The joint model fitted no attributes — every candidate was " +
          "dropped as unmeasurable or constant across the corpus.");
      }

      var color = ctx.color;

      // ---- reliability, at the top, before any coefficient ---------------
      if (joint.reliable === false) {
        el.appendChild(note(ctx, "bad", [
          tag(ctx, "bad", "not reliable"),
          ctx.h("strong", { text: "  Read the directions below, not the magnitudes. " }),
          ctx.h("span", {
            text: "This fit has " + ctx.fmt.int(joint.runs) + " run(s) and " +
              ctx.fmt.int(joint.parameters) + " fitted parameter(s) — fewer than the 5 runs " +
              "per parameter the model requires before its numbers mean anything. The " +
              "coefficients are what the ridge penalty and a handful of runs produced " +
              "together; another few runs could move them substantially.",
          }),
        ]));
      } else {
        el.appendChild(note(ctx, "info", [
          ctx.h("span", {
            text: "Fitted on " + ctx.fmt.int(joint.runs) + " run(s) with " +
              ctx.fmt.int(joint.failures) + " failure(s) over " +
              ctx.fmt.int(joint.parameters) + " parameter(s) — at or above the 5 runs per " +
              "parameter this model asks for before it will call itself reliable.",
          }),
        ]));
      }
      if (joint.converged === false) {
        el.appendChild(note(ctx, "bad", [
          ctx.h("strong", { text: "The fit did not converge. " }),
          ctx.h("span", { text: "It stopped at the iteration cap (" + ctx.fmt.int(joint.iterations) +
            "), so these coefficients are wherever the solver happened to be, not a solution." }),
        ]));
      }

      var separating = coefficients.filter(function (row) { return row.separates; });
      if (separating.length) {
        el.appendChild(note(ctx, "warn", [
          tag(ctx, "warn", "perfect separation"),
          ctx.h("strong", {
            text: "  " + separating.map(function (row) { return row.attribute; }).join(", ") +
              " separates the outcome completely. ",
          }),
          ctx.h("span", {
            text: "Unpenalised maximum likelihood would send that coefficient to infinity; " +
              "its magnitude here is set by the ridge penalty, not by the data. Read it as " +
              "“always failed / never failed”, not as an effect size.",
          }),
        ]));
      }

      // ---- coefficient chart ----------------------------------------------
      var sorted = coefficients.slice().sort(function (x, y) {
        return Math.abs(y.coefficient) - Math.abs(x.coefficient);
      });
      var magnitudes = sorted.map(function (row) { return Math.abs(row.coefficient); });
      var max = Math.max.apply(null, magnitudes.concat([0.001]));
      var W = 320, ROW = 27, top = 4, axis = 16;
      var H = top + sorted.length * ROW + axis;
      var x = scale(-max * 1.12, max * 1.12, 6, W - 6);
      var zero = x(0);

      var plot = chart(ctx, W, H);
      plot.appendChild(ctx.svg("line", {
        x1: zero, x2: zero, y1: top, y2: H - axis + 2,
        stroke: color.axis, "stroke-width": 1.2,
      }));
      plot.appendChild(svgText(ctx, zero, H - axis + 12, "0", color.muted, 8.5, "middle"));
      plot.appendChild(svgText(ctx, 6, H - axis + 12, "← lowers odds of failure", color.muted, 8, "start"));
      plot.appendChild(svgText(ctx, W - 6, H - axis + 12, "raises odds →", color.muted, 8, "end"));

      sorted.forEach(function (row, index) {
        var yLabel = top + index * ROW + 10;
        var yBar = top + index * ROW + 15;
        var raises = row.coefficient > 0;
        var fill = raises ? color.bad : color.good;
        var label = (row.separates ? "⚠ " : "") + (row.attribute || "attribute");
        plot.appendChild(svgText(ctx, 6, yLabel, truncate(ctx, label, 30), color.ink, 9.5));
        plot.appendChild(svgText(ctx, W - 6, yLabel,
          "OR " + ctx.fmt.num(row.odds_ratio, 2), color.muted, 9.5, "end"));
        var end = x(row.coefficient);
        plot.appendChild(ctx.svg("rect", {
          x: Math.min(zero, end), y: yBar, height: 7,
          width: Math.max(1, Math.abs(end - zero)),
          fill: fill, "fill-opacity": joint.reliable === false ? 0.45 : 0.85,
          rx: 1.5,
          stroke: row.separates ? color.warn : null,
          "stroke-width": row.separates ? 1 : null,
          "stroke-dasharray": row.separates ? "2 2" : null,
        }));
      });
      el.appendChild(ctx.h("div", { class: "scroll-x" }, [plot]));
      if (joint.reliable === false) {
        el.appendChild(ctx.h("div", {
          class: "sig-legend",
          text: "Bars are drawn faded because this fit is not reliable — the ordering is " +
            "worth reading, the lengths are not.",
        }));
      }

      // ---- the table -------------------------------------------------------
      var table = ctx.h("table", { class: "grid", style: { minWidth: "360px" } }, [
        ctx.h("tr", null, [
          ctx.h("th", { text: "Attribute" }),
          ctx.h("th", { class: "num", text: "Coefficient" }),
          ctx.h("th", { class: "num", text: "Odds ratio" }),
          ctx.h("th", { text: "Direction" }),
        ]),
      ]);
      sorted.forEach(function (row) {
        table.appendChild(ctx.h("tr", null, [
          ctx.h("td", null, [
            ctx.h("div", { text: row.attribute }),
            ctx.h("div", { class: "sig-sub", text: truncate(ctx, row.phrasing, 90) }),
            row.separates ? ctx.h("div", { class: "sig-chips" }, [tag(ctx, "warn", "perfect separation — magnitude set by the penalty")]) : null,
          ]),
          ctx.h("td", { class: "num", text: (row.coefficient > 0 ? "+" : "") + ctx.fmt.num(row.coefficient, 3) }),
          ctx.h("td", { class: "num", text: ctx.fmt.num(row.odds_ratio, 2) }),
          ctx.h("td", null, [tag(ctx, row.direction === "raises" ? "bad" : "good", row.direction || "—")]),
        ]));
      });
      el.appendChild(ctx.h("div", { class: "scroll-x" }, [table]));

      // ---- how it was fitted ------------------------------------------------
      el.appendChild(ctx.h("div", { class: "sig-h", text: "How this was fitted" }));
      var kv = ctx.h("dl", { class: "kv" });
      function kvRow(label, value) {
        kv.appendChild(ctx.h("dt", { text: label }));
        kv.appendChild(ctx.h("dd", { text: value }));
      }
      kvRow("Method", joint.method || "unstated");
      kvRow("Ridge", isNum(joint.ridge)
        ? ctx.fmt.num(joint.ridge, 2) + " on the slopes; the intercept is unpenalised"
        : "—");
      kvRow("Iterations", ctx.fmt.int(joint.iterations) +
        (joint.converged === true ? " · converged" : joint.converged === false ? " · did NOT converge" : ""));
      kvRow("Intercept", isNum(joint.intercept) ? ctx.fmt.num(joint.intercept, 3) + " (log-odds at all-false)" : "—");
      kvRow("Reliable", joint.reliable === false ? "no — under 5 runs per parameter" :
        joint.reliable === true ? "yes — at least 5 runs per parameter" : "unstated");
      el.appendChild(kv);

      var dropped = arr(joint.dropped);
      if (dropped.length) {
        var chips = ctx.h("div", { class: "sig-chips" }, [
          ctx.h("span", { class: "sig-sub", text: "Dropped, not imputed:" }),
        ]);
        dropped.forEach(function (row) {
          chips.appendChild(tag(ctx, "warn", row.attribute + " — " + (row.reason || "no reason given")));
        });
        el.appendChild(chips);
      }

      if (typeof joint.caveat === "string" && joint.caveat) {
        el.appendChild(note(ctx, "warn", [
          ctx.h("strong", { text: "Still associations. " }),
          ctx.h("span", { text: joint.caveat }),
        ]));
      }
      if (typeof joint.narrative === "string" && joint.narrative) {
        el.appendChild(ctx.h("div", { class: "caveat", text: joint.narrative }));
      }
      el.appendChild(ctx.h("div", {
        class: "caveat",
        text: "Read alongside the marginal lifts, not instead of them: these coefficients " +
          "control for the other measured attributes only — not for task difficulty, which " +
          "is what the within-task stratified lift handles. Where the two disagree, the " +
          "disagreement is the finding.",
      }));
    },
  });
})(typeof window !== "undefined" ? window : this);
