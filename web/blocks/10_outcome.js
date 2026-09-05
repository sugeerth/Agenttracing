/* AgentDiff blocks — the outcome group.
 *
 * Six cards that answer, in order: who won, where the two runs parted, what
 * caused the failure, what a different choice would have cost, how the credit
 * splits, and how the two answers actually read.
 *
 * The honesty rules from SCHEMA.md are load-bearing here, not decoration:
 *   - `attribution.failed_agent === null` means nobody failed. The object is
 *     always present, so presence is not the test.
 *   - `counterfactual` is a splice of steps that were really recorded on one
 *     side or the other — never a simulation. The card says so and shows
 *     `confidence` next to the estimate, not buried under it.
 *   - `shapley.outcome_attributable === false` means the binary outcome is
 *     NOT pinned to one divergence. The card states that in words.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ styles

  var STYLE_ID = "agentdiff-outcome-css";
  var CSS = [
    ".oc-lead{font-size:var(--fs-m);line-height:1.5;margin:0 0 9px;color:var(--ink)}",
    ".oc-lead b{font-weight:650}",
    ".oc-sub{font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px;line-height:1.45}",
    ".oc-sides{display:grid;grid-template-columns:1fr 1fr;gap:8px}",
    ".oc-side{border:1px solid var(--rule);border-radius:9px;padding:8px 9px;min-width:0}",
    ".oc-side.sa{border-left:3px solid var(--a)}",
    ".oc-side.sb{border-left:3px solid var(--b)}",
    ".oc-name{font-weight:620;font-size:var(--fs-s);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".oc-model{font-size:var(--fs-xs);color:var(--ink-3);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".oc-row{display:flex;gap:5px;align-items:center;flex-wrap:wrap;margin-top:6px}",
    ".oc-item{border:1px solid var(--rule);border-radius:9px;padding:8px 9px;margin-bottom:7px;",
    "background:transparent;cursor:pointer;text-align:left;width:100%;font:inherit;color:inherit;display:block}",
    ".oc-item:hover{border-color:var(--accent)}",
    ".oc-item:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
    ".oc-item.causal{border-left:3px solid var(--bad)}",
    ".oc-sum{font-size:var(--fs-s);line-height:1.45;color:var(--ink-2);margin:5px 0 0}",
    ".oc-chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}",
    ".oc-chip{font-size:var(--fs-xs);font-family:var(--mono);color:var(--ink-3);",
    "background:var(--surface-2);border-radius:5px;padding:1px 5px;white-space:nowrap}",
    ".oc-bar{height:6px;border-radius:3px;background:var(--rule);overflow:hidden;margin-top:5px}",
    ".oc-bar>i{display:block;height:100%;border-radius:3px}",
    ".oc-alloc{padding:7px 0;border-bottom:1px solid var(--rule)}",
    ".oc-alloc:last-child{border-bottom:0}",
    ".oc-allochead{display:flex;gap:6px;align-items:baseline;justify-content:space-between}",
    ".oc-num{font-variant-numeric:tabular-nums;font-family:var(--mono);font-size:var(--fs-xs);white-space:nowrap}",
    ".oc-diff{font-size:var(--fs-s);line-height:1.75;max-height:300px;overflow:auto;",
    "border:1px solid var(--rule);border-radius:8px;padding:8px 9px;word-break:break-word}",
    ".oc-diff del,.oc-diff ins{text-decoration:none;border-radius:3px;padding:0 2px;",
    "box-decoration-break:clone;-webkit-box-decoration-break:clone}",
    ".oc-diff del{background:color-mix(in srgb,var(--a) 20%,transparent);",
    "color:var(--ink);border-bottom:1px solid color-mix(in srgb,var(--a) 55%,transparent)}",
    ".oc-diff ins{background:color-mix(in srgb,var(--b) 20%,transparent);",
    "color:var(--ink);border-bottom:1px solid color-mix(in srgb,var(--b) 55%,transparent)}",
    ".oc-legend{display:flex;gap:10px;flex-wrap:wrap;font-size:var(--fs-xs);color:var(--ink-3);margin:7px 0 0}",
    ".oc-swatch{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}",
    ".oc-panel{border:1px solid var(--rule);border-radius:9px;padding:8px 9px;margin-top:8px}",
    ".oc-panel.bad{border-left:3px solid var(--bad)}",
    ".oc-panel.warn{border-left:3px solid var(--warn)}",
    ".oc-quote{font-size:var(--fs-s);line-height:1.5;color:var(--ink);margin:0}",
    ".oc-svgwrap{margin:8px 0 2px}",
    ".oc-svgwrap svg{width:100%;max-width:360px;height:auto;display:block}",
    ".oc-hr{height:1px;background:var(--rule);margin:9px 0}",
    ".oc-mini{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.45;margin:5px 0 0}",
  ].join("");

  function ensureStyles() {
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = CSS;
      (document.head || document.documentElement).appendChild(node);
    } catch (err) { /* styling is a nicety; the markup still reads */ }
  }

  // ------------------------------------------------------------------ shared

  function side(report, which) {
    return (report && report[which]) || null;
  }

  function agentName(report, which) {
    var s = side(report, which);
    var name = s && s.agent && s.agent.name;
    return name || (which === "a" ? "Agent A" : "Agent B");
  }

  function otherSide(which) { return which === "a" ? "b" : "a"; }

  function sideClass(which) { return which === "a" ? "a" : "b"; }

  function num(value) {
    return typeof value === "number" && !isNaN(value) ? value : null;
  }

  /* Downstream keys carry the suffix of the side that spent more, which is
   * not always the side that failed — resolve by looking for the suffix that
   * is actually present rather than assuming `_b`. */
  function downstreamOf(divergence) {
    var ds = (divergence && divergence.downstream) || {};
    var has = function (which) {
      return ds["extra_steps_" + which] !== undefined ||
             ds["extra_tokens_" + which] !== undefined ||
             ds["extra_latency_s_" + which] !== undefined;
    };
    var heavier = has("a") ? "a" : (has("b") ? "b" : null);
    return {
      heavier: heavier,
      steps: heavier ? num(ds["extra_steps_" + heavier]) : null,
      tokens: heavier ? num(ds["extra_tokens_" + heavier]) : null,
      latency: heavier ? num(ds["extra_latency_s_" + heavier]) : null,
      caused: !!ds.caused_failure,
      failed: ds.failed_agent === "a" || ds.failed_agent === "b" ? ds.failed_agent : null,
    };
  }

  /* Divergences carry step indices; the alignment row holding them is what a
   * reader can actually navigate to. One-sided regions only resolve on one
   * of the two columns, so match either. */
  function alignmentRowFor(report, divergence) {
    var rows = (report && report.alignment) || [];
    if (!divergence) return null;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i] || {};
      if (divergence.a_index !== null && divergence.a_index !== undefined &&
          row.a_index === divergence.a_index) return i;
      if (divergence.b_index !== null && divergence.b_index !== undefined &&
          row.b_index === divergence.b_index) return i;
    }
    return null;
  }

  function stepAt(report, which, index) {
    var s = side(report, which);
    var steps = (s && s.steps) || [];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] && steps[i].index === index) return steps[i];
    }
    return steps[index] || null;
  }

  function stepLabel(step) {
    if (!step) return "step";
    var type = step.type || "step";
    var name = step.name && step.name !== type ? step.name : "";
    return name ? type + " · " + name : type;
  }

  function ranges(list) {
    if (!Array.isArray(list) || !list.length) return "—";
    var sorted = list.filter(function (n) { return typeof n === "number"; })
                     .slice().sort(function (x, y) { return x - y; });
    if (!sorted.length) return "—";
    var out = [];
    var start = sorted[0];
    var prev = sorted[0];
    for (var i = 1; i <= sorted.length; i++) {
      var value = sorted[i];
      if (value === prev + 1) { prev = value; continue; }
      out.push(start === prev ? String(start) : start + "–" + prev);
      start = value; prev = value;
    }
    return out.join(", ");
  }

  function signed(value, unit, places) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    var sign = value > 0 ? "+" : (value < 0 ? "−" : "±");
    var magnitude = Math.abs(value);
    var body = places === undefined
      ? Math.round(magnitude).toLocaleString()
      : magnitude.toFixed(places);
    return sign + body + (unit ? " " + unit : "");
  }

  function signedUsd(value) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    var sign = value > 0 ? "+" : (value < 0 ? "−" : "±");
    var magnitude = Math.abs(value);
    return sign + "$" + magnitude.toFixed(magnitude < 0.01 ? 4 : 2);
  }

  /* For steps / tokens / latency / cost, less is better. */
  function deltaColor(ctx, value) {
    if (value === null || value === undefined || isNaN(value) || value === 0) return ctx.color.muted;
    return value < 0 ? ctx.color.good : ctx.color.bad;
  }

  function verdictClass(verdict) {
    if (verdict === "match") return "tag good";
    if (verdict === "partial") return "tag warn";
    if (verdict === "mismatch") return "tag bad";
    return "tag";
  }

  function chips(ctx, entries) {
    var wrap = ctx.h("div", { class: "oc-chips" });
    entries.forEach(function (entry) {
      if (!entry) return;
      wrap.appendChild(ctx.h("span", { class: "oc-chip", text: entry }));
    });
    return wrap;
  }

  function bar(ctx, fraction, fill) {
    var pct = Math.max(0, Math.min(1, num(fraction) || 0)) * 100;
    return ctx.h("div", { class: "oc-bar" }, [
      ctx.h("i", { style: { width: pct.toFixed(1) + "%", background: fill } }),
    ]);
  }

  // ----------------------------------------------------------- 1. verdict

  AgentDiff.block({
    id: "verdict",
    title: "Verdict",
    question: "Who won, and was it decisive?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var report = ctx.report;
      if (!report || !report.a || !report.b) return 0;
      // the verdict card leads the page with the same answer and more:
      // this block stands down (still one click away in the drawer)
      if (report.verdict_card && report.verdict_card.lines && report.verdict_card.lines.length) return 0;
      var oa = report.a.outcome || {};
      var ob = report.b.outcome || {};
      if (oa.success === undefined && ob.success === undefined) return 0.4;
      // A split outcome is the sharpest thing this report can say.
      if (oa.success !== ob.success) return 1;
      var winner = (report.success_analysis || {}).winner;
      return winner ? 0.8 : 0.65;
    },

    render: function (el, ctx) {
      ensureStyles();
      var report = ctx.report;
      if (!report || !report.a || !report.b) {
        ctx.empty(el, "No paired run loaded.");
        return;
      }
      var oa = report.a.outcome || {};
      var ob = report.b.outcome || {};
      var analysis = report.success_analysis || {};
      var evaluation = report.answer_eval || {};
      var nameA = agentName(report, "a");
      var nameB = agentName(report, "b");

      // The plain-language call comes first; the table is corroboration.
      var lead;
      var winner = analysis.winner === "a" || analysis.winner === "b" ? analysis.winner : null;
      if (winner) {
        var winnerName = agentName(report, winner);
        var loserName = agentName(report, otherSide(winner));
        lead = analysis.basis === "outcome"
          ? winnerName + " won outright — " + loserName + " failed this task."
          : winnerName + " won on efficiency; both agents got there.";
      } else if (oa.success === true && ob.success === true) {
        lead = "Both agents succeeded — no winner called.";
      } else if (oa.success === false && ob.success === false) {
        lead = "Both agents failed this task.";
      } else if (oa.success !== ob.success) {
        lead = (oa.success ? nameA : nameB) + " succeeded; " +
               (oa.success ? nameB : nameA) + " did not.";
      } else {
        lead = "No winner recorded for this run.";
      }

      el.appendChild(ctx.h("p", { class: "oc-lead" }, [ctx.h("b", { text: lead })]));

      if (analysis.basis) {
        el.appendChild(ctx.h("p", {
          class: "oc-sub",
          text: analysis.basis === "outcome"
            ? "Basis: outcome — exactly one agent failed."
            : "Basis: efficiency — both succeeded, so the call is about what it cost.",
        }));
      }

      var sides = ctx.h("div", { class: "oc-sides" });
      [["a", nameA, oa], ["b", nameB, ob]].forEach(function (row) {
        var which = row[0];
        var outcome = row[2];
        var against = evaluation[which + "_vs_expected"] || {};
        var card = ctx.h("div", { class: "oc-side s" + which });
        card.appendChild(ctx.h("div", { class: "oc-name", text: row[1] }));
        var model = side(report, which).agent || {};
        card.appendChild(ctx.h("div", {
          class: "oc-model",
          text: (model.model || "model n/a") + (model.version ? " · " + model.version : ""),
        }));
        var tags = ctx.h("div", { class: "oc-row" });
        if (outcome.success === true) tags.appendChild(ctx.h("span", { class: "tag good", text: "✓ success" }));
        else if (outcome.success === false) tags.appendChild(ctx.h("span", { class: "tag bad", text: "✗ failed" }));
        else tags.appendChild(ctx.h("span", { class: "tag", text: "outcome unknown" }));
        if (outcome.graded_by === "model") tags.appendChild(ctx.h("span", { class: "tag warn", text: "graded by a model" }));
        if (outcome.judge && typeof outcome.judge === "object" && outcome.judge.success !== null && outcome.judge.success !== undefined) {
          var j = outcome.judge;
          tags.appendChild(ctx.h("span", {
            class: "tag " + (j.success ? "good" : "bad"),
            title: (j.rationale || "") + (j.self_judged ? " (self-judged: the same model as the agent)" : ""),
            text: "judge " + (j.model || "model") + ": " + (j.success ? "✓" : "✗") + (num(j.score) !== null ? " " + ctx.fmt.num(j.score, 2) : "") +
                  (j.agrees_with_prior === false ? " · disagrees with the grade" : "") + (j.self_judged ? " · self" : ""),
          }));
        }
        if (num(outcome.score) !== null) {
          tags.appendChild(ctx.h("span", { class: "tag " + sideClass(which), text: "score " + ctx.fmt.num(outcome.score, 2) }));
        }
        card.appendChild(tags);

        var answerRow = ctx.h("div", { class: "oc-row" });
        if (against.verdict) {
          answerRow.appendChild(ctx.h("span", {
            class: verdictClass(against.verdict),
            text: "answer " + against.verdict,
          }));
        }
        if (num(against.coverage) !== null) {
          answerRow.appendChild(ctx.h("span", {
            class: "oc-num",
            style: { color: ctx.color.muted },
            text: ctx.fmt.pct(against.coverage) + " of expected",
          }));
        }
        if (answerRow.childNodes.length) card.appendChild(answerRow);
        sides.appendChild(card);
      });
      el.appendChild(sides);

      if (!evaluation.expected) {
        el.appendChild(ctx.h("p", {
          class: "caveat",
          text: "No expected answer recorded for this task, so the answer verdict is unavailable — success is the trace's own label.",
        }));
      }
      if (analysis.narrative) {
        el.appendChild(ctx.h("p", { class: "caveat", text: analysis.narrative }));
      } else if (!winner && report.attribution && report.attribution.explanation) {
        // Nobody failed, so the attribution object carries the comparison
        // instead of a cause — it is the only prose this run has.
        el.appendChild(ctx.h("p", { class: "caveat", text: report.attribution.explanation }));
      }
    },
  });

  // ------------------------------------------------------- 2. divergences

  AgentDiff.block({
    id: "divergences",
    title: "Divergences",
    question: "Where did they first part?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var list = (ctx.report && ctx.report.divergences) || [];
      if (!list.length) return 0;
      var causal = list.some(function (d) { return (d.downstream || {}).caused_failure; });
      return causal ? 1 : Math.min(0.85, 0.55 + 0.1 * list.length);
    },

    render: function (el, ctx) {
      ensureStyles();
      var report = ctx.report;
      var list = (report && report.divergences) || [];
      if (!list.length) {
        ctx.empty(el, "The two trajectories never parted — no divergence regions in this run.");
        return;
      }

      el.appendChild(ctx.h("p", {
        class: "oc-sub",
        text: list.length + " divergence region" + (list.length === 1 ? "" : "s") +
              ", ranked by downstream cost. Select one to mark it as inspected.",
      }));

      list.forEach(function (divergence) {
        var ds = downstreamOf(divergence);
        var row = alignmentRowFor(report, divergence);
        var item = ctx.h("button", {
          type: "button",
          class: "oc-item" + (ds.caused ? " causal" : ""),
          onclick: function () { ctx.signal("inspect"); },
        });

        var head = ctx.h("div", { class: "oc-row", style: { marginTop: "0" } }, [
          ctx.h("span", { class: "tag", text: "#" + (divergence.rank === undefined ? "?" : divergence.rank) }),
          ctx.h("span", { class: "tag", text: divergence.kind || "unclassified" }),
        ]);
        if (ds.caused) {
          head.appendChild(ctx.h("span", {
            class: "tag bad",
            text: ds.failed ? "caused " + agentName(report, ds.failed) + "'s failure" : "caused the failure",
          }));
        }
        item.appendChild(head);

        item.appendChild(ctx.h("p", { class: "oc-sum", text: divergence.summary || "No summary recorded." }));

        var position = [];
        position.push(row === null ? "alignment row —" : "alignment row " + row);
        position.push("A step " + (divergence.a_index === null || divergence.a_index === undefined ? "—" : divergence.a_index));
        position.push("B step " + (divergence.b_index === null || divergence.b_index === undefined ? "—" : divergence.b_index));
        item.appendChild(chips(ctx, position));

        if (ds.heavier) {
          var who = agentName(report, ds.heavier);
          item.appendChild(ctx.h("div", { class: "oc-chips" }, [
            ctx.h("span", { class: "oc-chip", style: { color: ctx.color[sideClass(ds.heavier)] }, text: who + " spent more downstream:" }),
            ds.steps === null ? null : ctx.h("span", { class: "oc-chip", text: signed(ds.steps, "steps") }),
            ds.tokens === null ? null : ctx.h("span", { class: "oc-chip", text: signed(ds.tokens, "tok") }),
            ds.latency === null ? null : ctx.h("span", { class: "oc-chip", text: signed(ds.latency, "s", 2) }),
          ].filter(Boolean)));
        } else {
          item.appendChild(ctx.h("p", { class: "oc-mini", text: "No downstream cost recorded for this region." }));
        }

        el.appendChild(item);
      });
    },
  });

  // -------------------------------------------------------- 3. attribution

  function chainNodes(report, failed, attribution) {
    var chain = Array.isArray(attribution.chain) ? attribution.chain.slice() : [];
    var root = num(attribution.root_cause_step);
    if (!chain.length && root !== null) chain = [root];
    var steps = (side(report, failed) || {}).steps || [];
    var lastStepIndex = steps.length ? (num(steps[steps.length - 1].index) !== null
      ? steps[steps.length - 1].index : steps.length - 1) : null;

    var nodes = chain.map(function (index, position) {
      var step = stepAt(report, failed, index);
      return {
        index: index,
        label: stepLabel(step),
        quality: step && step.quality ? step.quality : null,
        role: position === 0 ? "root" : "link",
      };
    });
    // If the chain already ends on the run's final step, that node IS the
    // outcome; otherwise say so explicitly rather than implying the chain
    // stopped mid-run.
    if (nodes.length && lastStepIndex !== null && nodes[nodes.length - 1].index === lastStepIndex) {
      nodes[nodes.length - 1].role = nodes.length === 1 ? "root" : "outcome";
    }
    if (!nodes.length || nodes[nodes.length - 1].role !== "outcome") {
      nodes.push({ index: null, label: "task failed", quality: null, role: "outcome" });
    }
    return nodes;
  }

  function drawChain(ctx, nodes) {
    var W = 300;
    var NH = 32;
    var GAP = 16;
    var TOP = 4;
    var height = TOP + nodes.length * NH + Math.max(0, nodes.length - 1) * GAP + TOP;
    var root = ctx.svg("svg", {
      viewBox: "0 0 " + W + " " + height,
      role: "img",
      "aria-label": "Causal chain from the root cause step to the failed outcome",
    });

    nodes.forEach(function (node, i) {
      var y = TOP + i * (NH + GAP);
      var accent = node.role === "link" ? ctx.color.axis : ctx.color.bad;
      var strong = node.role !== "link";

      root.appendChild(ctx.svg("rect", {
        x: 6, y: y, width: W - 12, height: NH, rx: 8,
        fill: accent, "fill-opacity": strong ? 0.12 : 0.04,
        stroke: accent, "stroke-width": strong ? 1.4 : 1,
      }));
      root.appendChild(ctx.svg("text", {
        x: 16, y: y + NH / 2 + 4, "font-size": 11.5,
        "font-family": "ui-monospace, Menlo, Consolas, monospace",
        fill: strong ? ctx.color.bad : ctx.color.muted,
        text: node.index === null ? "✗" : "#" + node.index,
      }));
      root.appendChild(ctx.svg("text", {
        x: node.index === null ? 34 : 48, y: y + NH / 2 + 4, "font-size": 11.5,
        fill: ctx.color.ink,
        text: ctx.fmt.truncate(node.label, node.role === "link" ? 30 : 26),
      }));
      if (node.role !== "link") {
        root.appendChild(ctx.svg("text", {
          x: W - 14, y: y + NH / 2 + 4, "font-size": 11, "text-anchor": "end",
          fill: ctx.color.bad,
          text: node.role === "root" ? "root cause" : "outcome",
        }));
      }
      if (node.quality === "bad" || node.quality === "weak") {
        root.appendChild(ctx.svg("circle", {
          cx: W - 20, cy: y + NH - 8, r: 2.5,
          fill: node.quality === "bad" ? ctx.color.bad : ctx.color.warn,
        }));
      }

      if (i < nodes.length - 1) {
        var top = y + NH;
        var bottom = top + GAP;
        root.appendChild(ctx.svg("line", {
          x1: W / 2, y1: top + 1, x2: W / 2, y2: bottom - 5,
          stroke: ctx.color.axis, "stroke-width": 1.2,
        }));
        root.appendChild(ctx.svg("polygon", {
          points: (W / 2 - 4) + "," + (bottom - 6) + " " + (W / 2 + 4) + "," + (bottom - 6) +
                  " " + (W / 2) + "," + bottom,
          fill: ctx.color.axis,
        }));
      }
    });
    return ctx.h("div", { class: "oc-svgwrap" }, [root]);
  }

  AgentDiff.block({
    id: "attribution",
    title: "Attribution",
    question: "What caused the failure?",
    group: "outcome",
    size: "wide",

    relevance: function (ctx) {
      var attribution = (ctx.report && ctx.report.attribution) || null;
      if (!attribution) return 0;
      // The object is always emitted; a null failed_agent means nobody failed.
      if (attribution.failed_agent !== "a" && attribution.failed_agent !== "b") return 0;
      // more to say with a traced chain than with a bare root step
      var chain = Array.isArray(attribution.chain) ? attribution.chain.length : 0;
      return chain > 1 ? 0.9 : 0.6;
    },

    render: function (el, ctx) {
      ensureStyles();
      var report = ctx.report;
      var attribution = (report && report.attribution) || null;
      var failed = attribution && (attribution.failed_agent === "a" || attribution.failed_agent === "b")
        ? attribution.failed_agent : null;

      if (!failed) {
        ctx.empty(el, attribution && attribution.explanation
          ? attribution.explanation
          : "Nothing failed on this task, so there is no failure to attribute.");
        return;
      }

      var failedName = agentName(report, failed);
      var root = num(attribution.root_cause_step);

      el.appendChild(ctx.h("p", { class: "oc-lead" }, [
        ctx.h("b", { text: failedName + " failed." }),
        document.createTextNode(root === null
          ? " No root-cause step was isolated."
          : " Root cause: " + (attribution.category || "unclassified") + " at step " + root + "."),
      ]));

      var tags = ctx.h("div", { class: "oc-row", style: { marginTop: "0" } }, [
        ctx.h("span", { class: "tag " + sideClass(failed), text: failed.toUpperCase() + " · " + failedName }),
        attribution.category ? ctx.h("span", { class: "tag bad", text: attribution.category }) : null,
        ctx.h("span", {
          class: "tag",
          text: (Array.isArray(attribution.chain) ? attribution.chain.length : 0) + " step chain",
        }),
      ].filter(Boolean));
      el.appendChild(tags);

      el.appendChild(drawChain(ctx, chainNodes(report, failed, attribution)));

      if (attribution.explanation) {
        el.appendChild(ctx.h("div", { class: "oc-panel bad" }, [
          ctx.h("p", { class: "oc-quote", text: attribution.explanation }),
        ]));
      }
      el.appendChild(ctx.h("p", {
        class: "caveat",
        text: "The chain is the propagation path through " + failedName +
              "'s own steps: the root divergence, every step that carried its content forward, and the answer that came out. " +
              "A dot marks a step the trace itself annotated — red for bad, amber for weak.",
      }));
    },
  });

  // ------------------------------------------------------ 4. counterfactual

  AgentDiff.block({
    id: "counterfactual",
    title: "Counterfactual",
    question: "What if it had chosen differently?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var cf = (ctx.report && ctx.report.counterfactual) || null;
      if (!cf || !cf.estimate) return 0;
      if (cf.confidence === "high") return 0.95;
      if (cf.confidence === "medium") return 0.8;
      return 0.65;
    },

    render: function (el, ctx) {
      ensureStyles();
      var report = ctx.report;
      var cf = (report && report.counterfactual) || null;
      if (!cf || !cf.estimate) {
        ctx.empty(el, "No counterfactual: the engine only splices one when a failure was attributed.");
        return;
      }

      var estimate = cf.estimate || {};
      var splice = cf.splice || {};
      var confidence = cf.confidence || "unknown";
      var confidenceClass = confidence === "high" ? "tag good"
        : (confidence === "medium" ? "tag warn" : "tag bad");

      el.appendChild(ctx.h("div", { class: "oc-row", style: { marginTop: "0" } }, [
        ctx.h("span", { class: "tag", text: "splice of observed steps" }),
        ctx.h("span", { class: confidenceClass, text: "confidence: " + confidence }),
        estimate.outcome ? ctx.h("span", {
          class: estimate.outcome === "success" ? "tag good" : "tag bad",
          text: "estimated outcome: " + estimate.outcome,
        }) : null,
      ].filter(Boolean)));

      if (cf.premise) {
        el.appendChild(ctx.h("div", { class: "oc-panel" }, [
          ctx.h("p", { class: "oc-quote", text: "Premise: " + cf.premise }),
        ]));
      }

      var adopted = splice.adopted_from === "a" || splice.adopted_from === "b" ? splice.adopted_from : null;
      el.appendChild(ctx.h("dl", { class: "kv", style: { marginTop: "9px" } }, [
        ctx.h("dt", { text: "Kept prefix" }),
        ctx.h("dd", { class: "mono", text: "steps " + ranges(splice.prefix_steps) }),
        ctx.h("dt", { text: "Adopted" }),
        ctx.h("dd", { class: "mono", text: "steps " + ranges(splice.adopted_steps) +
          (adopted ? " from " + agentName(report, adopted) : "") }),
      ]));

      var deltas = [
        { label: "Steps", value: num(estimate.steps), delta: num(estimate.steps_delta), text: ctx.fmt.int(estimate.steps), d: signed(estimate.steps_delta) },
        { label: "Tokens", value: num(estimate.tokens), delta: num(estimate.tokens_delta), text: ctx.fmt.tokens(estimate.tokens), d: signed(estimate.tokens_delta, "tok") },
        { label: "Latency", value: num(estimate.latency_s), delta: num(estimate.latency_delta_s), text: ctx.fmt.sec(estimate.latency_s), d: signed(estimate.latency_delta_s, "s", 2) },
        { label: "Cost", value: num(estimate.cost_usd), delta: num(estimate.cost_delta_usd), text: ctx.fmt.usd(estimate.cost_usd), d: signedUsd(estimate.cost_delta_usd) },
      ];
      var table = ctx.h("table", { class: "grid", style: { marginTop: "9px" } }, [
        ctx.h("tr", null, [
          ctx.h("th", { text: "Estimate" }),
          ctx.h("th", { class: "num", text: "Spliced" }),
          ctx.h("th", { class: "num", text: "vs actual" }),
        ]),
      ]);
      deltas.forEach(function (row) {
        if (row.value === null && row.delta === null) return;
        table.appendChild(ctx.h("tr", null, [
          ctx.h("td", { text: row.label }),
          ctx.h("td", { class: "num", text: row.value === null ? "—" : row.text }),
          ctx.h("td", { class: "num", style: { color: deltaColor(ctx, row.delta) }, text: row.delta === null ? "—" : row.d }),
        ]));
      });
      el.appendChild(ctx.h("div", { class: "scroll-x" }, [table]));

      if (cf.narrative) {
        el.appendChild(ctx.h("p", { class: "oc-mini", text: cf.narrative }));
      }

      el.appendChild(ctx.h("p", {
        class: "caveat",
        text: "Not a simulation and not a prediction. The engine never re-runs an agent: this trajectory is assembled from steps that were really recorded — " +
              (adopted ? agentName(report, adopted) + "'s" : "the reference") +
              " suffix pasted onto the failing run's own prefix — and costed at the observed per-step rates. Confidence is \"" +
              confidence + "\", which describes how clean that splice was, not how likely the outcome is.",
      }));
    },
  });

  // ------------------------------------------------------------- 5. shapley

  AgentDiff.block({
    id: "shapley",
    title: "Credit split",
    question: "How is the credit split?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var shapley = (ctx.report && ctx.report.shapley) || null;
      if (!shapley || shapley.available === false) return 0;
      var allocations = shapley.allocations || [];
      if (!allocations.length) return 0;
      // Splitting credit only earns its space once there is more than one
      // region to split it between; with one, the verdict already said it
      return allocations.length > 1 ? 0.9 : 0;
    },

    render: function (el, ctx) {
      ensureStyles();
      var report = ctx.report;
      var shapley = (report && report.shapley) || null;
      if (!shapley) {
        ctx.empty(el, "No credit split: this run has no divergence regions to allocate between.");
        return;
      }
      if (shapley.available === false) {
        ctx.empty(el, shapley.reason || shapley._note ||
          "Credit allocation is marked unavailable for this run.");
        return;
      }
      var allocations = shapley.allocations || [];
      if (!allocations.length) {
        ctx.empty(el, "No allocations recorded for this run.");
        return;
      }

      var metric = shapley.metric || "tokens";
      var total = num(shapley.total_saving);

      el.appendChild(ctx.h("div", { class: "oc-row", style: { marginTop: "0" } }, [
        ctx.h("span", { class: "tag", text: "metric: " + metric }),
        ctx.h("span", {
          class: shapley.method === "exact" ? "tag good" : "tag warn",
          text: shapley.method === "exact" ? "exact enumeration" : (shapley.method || "method n/a"),
        }),
        ctx.h("span", { class: "tag", text: allocations.length + " region" + (allocations.length === 1 ? "" : "s") }),
      ]));

      if (total !== null) {
        el.appendChild(ctx.h("p", {
          class: "oc-lead", style: { marginTop: "9px", marginBottom: "4px" },
        }, [
          ctx.h("b", { text: total >= 0
            ? "Taking the reference path at every divergence saves " + Math.round(total).toLocaleString() + " " + metric + "."
            : "Taking the reference path at every divergence costs " + Math.round(-total).toLocaleString() + " more " + metric + " — the gap runs the other way." }),
        ]));
      }
      if (shapley.loser && shapley.winner) {
        el.appendChild(ctx.h("p", {
          class: "oc-sub",
          text: shapley.loser + "'s spend measured against " + shapley.winner + "'s path.",
        }));
      }

      allocations.forEach(function (allocation) {
        var share = num(allocation.share);
        var value = num(allocation.shapley);
        var causal = !!allocation.caused_failure;
        var fill = causal ? ctx.color.bad : ctx.color.axis;
        var row = ctx.h("div", { class: "oc-alloc" });
        row.appendChild(ctx.h("div", { class: "oc-allochead" }, [
          ctx.h("span", { class: "oc-row", style: { marginTop: "0" } }, [
            ctx.h("span", { class: "tag", text: "region " + (allocation.region === undefined ? "?" : allocation.region) }),
            ctx.h("span", { class: "tag", text: allocation.kind || "unclassified" }),
            causal ? ctx.h("span", { class: "tag bad", text: "causal" }) : null,
          ].filter(Boolean)),
          ctx.h("span", {
            class: "oc-num",
            text: (share === null ? "—" : ctx.fmt.pct(share)) +
                  (value === null ? "" : "  ·  " + Math.round(value).toLocaleString()),
          }),
        ]));
        row.appendChild(bar(ctx, share === null ? 0 : Math.abs(share), fill));
        if (allocation.summary) {
          row.appendChild(ctx.h("p", { class: "oc-sum", text: ctx.fmt.truncate(allocation.summary, 190) }));
        }
        if (Array.isArray(allocation.alignment_rows) && allocation.alignment_rows.length) {
          row.appendChild(chips(ctx, [
            "alignment row" + (allocation.alignment_rows.length === 1 ? " " : "s ") +
            ranges(allocation.alignment_rows),
          ]));
        }
        el.appendChild(row);
      });

      var attributable = shapley.outcome_attributable === true;
      el.appendChild(ctx.h("div", { class: "oc-panel " + (attributable ? "" : "warn") }, [
        ctx.h("div", { class: "oc-row", style: { marginTop: "0" } }, [
          ctx.h("span", {
            class: attributable ? "tag good" : "tag warn",
            text: attributable ? "outcome attributable" : "outcome NOT attributable",
          }),
        ]),
        ctx.h("p", {
          class: "oc-mini",
          text: attributable
            ? "Exactly one divergence was causal, so the pass/fail outcome does belong to it. The shares above still describe " + metric + ", not the outcome."
            : "The shares above split " + metric + " only — the pass/fail outcome is NOT attributable to any single divergence here.",
        }),
        // The engine's own wording, kept verbatim: it is the field that says
        // why credit was declined rather than guessed.
        shapley.outcome_note
          ? ctx.h("p", { class: "oc-mini", style: { fontStyle: "italic" }, text: shapley.outcome_note })
          : null,
      ].filter(Boolean)));

      var footnotes = [];
      if (num(shapley.efficiency_check) !== null) {
        footnotes.push("Efficiency check (Σ allocations − total): " + ctx.fmt.num(shapley.efficiency_check, 2) + ".");
      }
      footnotes.push("Splice-Shapley: exact with respect to the splice surrogate — adopting the reference path yields the steps that side actually took — not with respect to the agent, which would require re-running it.");
      if (shapley._note) footnotes.push(shapley._note);
      el.appendChild(ctx.h("p", { class: "caveat", text: footnotes.join(" ") }));
    },
  });

  // --------------------------------------------------------- 6. answer diff

  var OP_EQUAL = { eq: 1, equal: 1, "=": 1 };
  var OP_DELETE = { del: 1, delete: 1, "-": 1 };
  var OP_INSERT = { ins: 1, insert: 1, "+": 1 };

  function opKind(raw) {
    var op = String(raw);
    if (OP_DELETE[op]) return "del";
    if (OP_INSERT[op]) return "ins";
    if (OP_EQUAL[op]) return "eq";
    return "eq";
  }

  /* The engine emits a token-level diff, so a rewritten clause arrives as
   * del/ins/del/ins alternating around shared spaces — legible to a diff
   * algorithm, unreadable to a person. Coalescing each run back into "what A
   * said" then "what B said" changes no text: the shared separator that is
   * consumed belongs to both sides, and it is re-emitted inside both. */
  function coalesceDiff(pairs) {
    var ops = [];
    (pairs || []).forEach(function (pair) {
      if (!Array.isArray(pair) || pair.length < 2) return;
      var text = pair[1] === null || pair[1] === undefined ? "" : String(pair[1]);
      if (!text) return;
      ops.push({ op: opKind(pair[0]), text: text });
    });

    var out = [];
    var i = 0;
    while (i < ops.length) {
      if (ops[i].op === "eq") { out.push(ops[i]); i++; continue; }
      var dels = [];
      var inss = [];
      var separator = null;
      while (i < ops.length) {
        var op = ops[i];
        if (op.op === "eq") {
          // Only swallow a shared separator once BOTH sides have text to
          // separate — then it can be re-emitted into both and no character
          // of either answer is lost. Anything else ends the run.
          var joins = /^\s+$/.test(op.text) && dels.length && inss.length &&
                      i + 1 < ops.length && ops[i + 1].op !== "eq";
          if (!joins) break;
          separator = op.text;
          i++;
          continue;
        }
        if (separator !== null) {
          dels.push(separator);
          inss.push(separator);
          separator = null;
        }
        (op.op === "del" ? dels : inss).push(op.text);
        i++;
      }
      if (dels.length) out.push({ op: "del", text: dels.join("") });
      if (inss.length) out.push({ op: "ins", text: inss.join("") });
    }
    return out;
  }

  AgentDiff.block({
    id: "answer-diff",
    title: "Answer diff",
    question: "How do the two answers differ?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var evaluation = (ctx.report && ctx.report.answer_eval) || null;
      if (!evaluation) return 0;
      var diff = evaluation.diff_ab || [];
      if (!diff.length) return 0;
      var differs = diff.some(function (pair) {
        return pair && !OP_EQUAL[String(pair[0])];
      });
      return differs ? 0.85 : 0.3;
    },

    render: function (el, ctx) {
      ensureStyles();
      var report = ctx.report;
      var evaluation = (report && report.answer_eval) || null;
      if (!evaluation) {
        ctx.empty(el, "This run carries no answer evaluation.");
        return;
      }
      var diff = evaluation.diff_ab || [];
      if (!diff.length) {
        ctx.empty(el, "No answer diff recorded — at least one side emitted no final answer.");
        return;
      }

      var nameA = agentName(report, "a");
      var nameB = agentName(report, "b");

      var body = ctx.h("div", { class: "oc-diff" });
      var identical = true;
      coalesceDiff(diff).forEach(function (segment) {
        if (segment.op === "del") { identical = false; body.appendChild(ctx.h("del", { text: segment.text })); }
        else if (segment.op === "ins") { identical = false; body.appendChild(ctx.h("ins", { text: segment.text })); }
        else { body.appendChild(document.createTextNode(segment.text)); }
      });
      el.appendChild(body);

      el.appendChild(ctx.h("div", { class: "oc-legend" }, [
        ctx.h("span", null, [
          ctx.h("i", { class: "oc-swatch", style: { background: ctx.color.a } }),
          document.createTextNode("only in A · " + nameA),
        ]),
        ctx.h("span", null, [
          ctx.h("i", { class: "oc-swatch", style: { background: ctx.color.b } }),
          document.createTextNode("only in B · " + nameB),
        ]),
        ctx.h("span", { text: "unmarked text is shared wording" }),
      ]));
      el.appendChild(ctx.h("p", {
        class: "oc-mini",
        text: "Token-level diff of A's final answer against B's; within a change A's wording is shown first, then B's.",
      }));

      if (identical) {
        el.appendChild(ctx.h("p", { class: "oc-mini", text: "The two final answers are token-identical." }));
      }

      el.appendChild(ctx.h("div", { class: "oc-hr" }));

      if (evaluation.expected) {
        el.appendChild(ctx.h("dl", { class: "kv" }, [
          ctx.h("dt", { text: "Expected" }),
          ctx.h("dd", { class: "mono", text: ctx.fmt.truncate(evaluation.expected, 240) }),
        ]));
      } else {
        el.appendChild(ctx.h("p", {
          class: "oc-mini",
          text: "No expected answer recorded for this task, so neither side can be scored for coverage.",
        }));
      }

      [["a", nameA], ["b", nameB]].forEach(function (row) {
        var which = row[0];
        var against = evaluation[which + "_vs_expected"] || {};
        if (against.coverage === undefined && !against.verdict) return;
        var block = ctx.h("div", { style: { marginTop: "8px" } });
        block.appendChild(ctx.h("div", { class: "oc-allochead" }, [
          ctx.h("span", { class: "oc-row", style: { marginTop: "0" } }, [
            ctx.h("span", { class: "tag " + sideClass(which), text: which.toUpperCase() + " · " + row[1] }),
            ctx.h("span", { class: verdictClass(against.verdict), text: against.verdict || "unknown" }),
          ]),
          ctx.h("span", {
            class: "oc-num",
            text: num(against.coverage) === null ? "—" : ctx.fmt.pct(against.coverage) + " coverage",
          }),
        ]));
        block.appendChild(bar(ctx, against.coverage, ctx.color[sideClass(which)]));
        el.appendChild(block);
      });

      el.appendChild(ctx.h("p", {
        class: "caveat",
        text: "Coverage is the fraction of expected-answer tokens present in the answer — a lexical check, not a judgement of meaning. A wrong number caps the verdict at \"partial\" even when the wording matches.",
      }));
    },
  });
})(typeof window !== "undefined" ? window : this);
