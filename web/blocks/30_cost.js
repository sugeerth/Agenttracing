/* AgentDiff blocks — cost & change.
 *
 * Six blocks answering the two questions that survive the demo: what did the
 * difference between these agents cost, and what should I change because of
 * it? One is per-task (metrics_delta); the other five read the batch
 * aggregate, so they say the same thing whichever task is selected — a
 * recommendation drawn from eight tasks does not become a different
 * recommendation because the picker moved.
 *
 * Direction of "better" is stated per metric rather than assumed. Fewer
 * tokens is better; fewer searches is not obviously anything, and a block
 * that colours it green anyway is inventing a judgement the data did not
 * make.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------- css
  //
  // The shell is built, not editable from here, so anything it does not
  // already provide is appended once — guarded, because every module load and
  // every re-render would otherwise stack another copy.

  var STYLE_ID = "agentdiff-cost-css";
  var CSS = [
    ".cost-legend{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}",
    ".cost-note{font-size:11.5px;color:var(--ink-2);margin:0 0 8px;line-height:1.45}",
    ".cost-bars{display:grid;grid-template-columns:minmax(78px,auto) minmax(50px,1fr) auto;",
    "gap:6px 10px;align-items:center;font-size:12px}",
    ".cost-track{height:8px;border-radius:4px;background:var(--surface-2);",
    "border:1px solid var(--rule);overflow:hidden}",
    ".cost-track>i{display:block;height:100%}",
    ".cost-val{font-variant-numeric:tabular-nums;color:var(--ink-2);white-space:nowrap}",
    ".cost-list{display:flex;flex-direction:column;gap:8px}",
    ".cost-card{border:1px solid var(--rule);border-radius:9px;padding:9px 10px;",
    "background:var(--surface);min-width:0}",
    ".cost-card h4{font-size:12.5px;margin:0;line-height:1.35}",
    ".cost-tags{display:flex;flex-wrap:wrap;gap:4px;margin:5px 0 0}",
    ".cost-text{font-size:12px;color:var(--ink-2);margin:5px 0 0;line-height:1.45}",
    ".cost-toggle{border:none;background:transparent;color:var(--ink-3);cursor:pointer;",
    "font-size:11.5px;padding:4px 0 0;text-align:left;font-family:inherit}",
    ".cost-toggle:hover{color:var(--accent)}",
    ".cost-detail{margin-top:6px;border-top:1px solid var(--rule);padding-top:6px}",
    ".cost-prehead{display:flex;align-items:center;justify-content:space-between;",
    "gap:8px;margin-top:8px}",
    ".cost-prehead span{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;",
    "color:var(--ink-3)}",
    "pre.cost-pre{margin:5px 0 0;padding:8px 9px;background:var(--surface-2);",
    "border:1px solid var(--rule);border-radius:7px;white-space:pre-wrap;",
    "word-break:break-word;line-height:1.5;color:var(--ink-2)}",
    ".cost-mini{font-size:11px;color:var(--ink-3);line-height:1.45}",
    ".cost-strong{font-weight:600}",
  ].join("");

  function ensureStyle() {
    try {
      if (document.getElementById(STYLE_ID)) return;
      var style = document.createElement("style");
      style.id = STYLE_ID;
      style.textContent = CSS;
      (document.head || document.documentElement).appendChild(style);
    } catch (err) { /* styling is a nicety; never take the block with it */ }
  }
  ensureStyle();

  // -------------------------------------------------------------- utilities

  function isNum(value) {
    return typeof value === "number" && isFinite(value);
  }

  function arr(value) { return Array.isArray(value) ? value : []; }

  function agentNames(ctx) {
    var names = (ctx.aggregate && ctx.aggregate.agents) || {};
    var report = ctx.report;
    return {
      a: names.a || (report && report.a && report.a.agent && report.a.agent.name) || "Agent A",
      b: names.b || (report && report.b && report.b.agent && report.b.agent.name) || "Agent B",
    };
  }

  /* The metrics both the per-task delta and the batch means carry, with the
   * one fact neither JSON records: which direction counts as an improvement.
   * `better: "none"` means the data genuinely does not say — more searches
   * can be diligence or thrash — so those rows are shown uncoloured. */
  /* Counts are whole on a single task and fractional as a batch mean. Printing
   * a mean of 0.75 searches as "1" made the table contradict itself — 1 vs 1
   * with a delta of +1 — so a non-integer small count keeps its decimals. */
  function countValue(fmt, value) {
    if (!isNum(value)) return "—";
    if (value === Math.round(value)) return fmt.int(value);
    return Math.abs(value) < 100 ? fmt.num(value, 2) : fmt.int(value);
  }

  var METRICS = [
    { key: "steps", label: "Steps", better: "lower", format: countValue },
    { key: "tokens", label: "Tokens", better: "lower", format: countValue },
    { key: "cost_usd", label: "Cost", better: "lower",
      format: function (fmt, v) { return fmt.usd(v); } },
    { key: "latency_s", label: "Latency", better: "lower",
      format: function (fmt, v) { return fmt.sec(v); } },
    { key: "tool_calls", label: "Tool calls", better: "none", format: countValue },
    { key: "searches", label: "Searches", better: "none", format: countValue },
  ];

  function signed(metric, fmt, value) {
    if (!isNum(value)) return "—";
    if (value === 0) return "0";
    return (value > 0 ? "+" : "−") + metric.format(fmt, Math.abs(value));
  }

  function signedPct(fmt, a, b) {
    if (!isNum(a) || !isNum(b) || a === 0) return "—";
    var change = (b - a) / Math.abs(a);
    if (change === 0) return "0%";
    return (change > 0 ? "+" : "−") + fmt.pct(Math.abs(change), Math.abs(change) < 0.1 ? 1 : 0);
  }

  /* One A/B comparison table, shared by the per-task deltas and the batch
   * means so both read identically. `pick(key)` hands back {a, b} or null for
   * a metric the payload does not carry. */
  function comparisonTable(ctx, pick, options) {
    var fmt = ctx.fmt, h = ctx.h, color = ctx.color;
    var judge = options.judge !== false;
    var table = ctx.h("table", { class: "grid" }, [
      h("tr", null, [
        h("th", { text: "Metric" }),
        h("th", { class: "num", style: { color: color.a }, text: options.aLabel }),
        h("th", { class: "num", style: { color: color.b }, text: options.bLabel }),
        h("th", { class: "num", text: "Δ B−A" }),
        h("th", { class: "num", text: "Change" }),
      ]),
    ]);

    var rows = 0;
    METRICS.forEach(function (metric) {
      var pair = pick(metric.key);
      if (!pair || (!isNum(pair.a) && !isNum(pair.b))) return;
      rows++;
      var delta = isNum(pair.a) && isNum(pair.b) ? pair.b - pair.a : null;
      var tint = color.muted;
      if (judge && metric.better === "lower" && isNum(delta) && delta !== 0) {
        // Lower is better, so B spending more is B doing worse.
        tint = delta > 0 ? color.bad : color.good;
      }
      var label = h("td", null, [
        metric.label,
        metric.better === "none" ? h("span", { class: "cost-mini", text: " ·" }) : null,
      ]);
      table.appendChild(h("tr", null, [
        label,
        h("td", { class: "num", text: metric.format(fmt, pair.a) }),
        h("td", { class: "num", text: metric.format(fmt, pair.b) }),
        h("td", { class: "num", style: { color: tint }, text: signed(metric, fmt, delta) }),
        h("td", { class: "num", style: { color: tint }, text: signedPct(fmt, pair.a, pair.b) }),
      ]));
    });
    return rows ? table : null;
  }

  var DIRECTION_NOTE = "Δ is B minus A. Fewer steps, tokens, dollars and seconds is " +
    "better, so red means B spent more. Rows marked · (tool calls, searches) are " +
    "left uncoloured: the data does not say whether more of them is diligence or thrash.";

  // ------------------------------------------------------------------ copy

  /* Copy without a library and without assuming the clipboard API is
   * reachable — a file:// page in a locked-down browser gets neither the
   * async API nor a permission prompt, and the honest fallback is to select
   * the text and say so rather than to fail silently. */
  function copyToClipboard(text, button, onDone) {
    var restore = button.textContent;
    function finish(label) {
      button.textContent = label;
      setTimeout(function () { button.textContent = restore; }, 1800);
      if (onDone) onDone();
    }
    function legacy() {
      var ok = false;
      try {
        var pad = document.createElement("textarea");
        pad.value = text;
        pad.setAttribute("readonly", "readonly");
        pad.style.position = "fixed";
        pad.style.left = "-9999px";
        pad.style.top = "0";
        document.body.appendChild(pad);
        pad.select();
        ok = document.execCommand("copy");
        document.body.removeChild(pad);
      } catch (err) { ok = false; }
      finish(ok ? "Copied" : "Press ⌘/Ctrl+C");
    }
    try {
      if (global.navigator && global.navigator.clipboard &&
          typeof global.navigator.clipboard.writeText === "function") {
        global.navigator.clipboard.writeText(text).then(
          function () { finish("Copied"); },
          function () { legacy(); }
        );
        return;
      }
    } catch (err) { /* fall through to the synchronous path */ }
    legacy();
  }

  function copyablePrompt(ctx, label, text) {
    var h = ctx.h;
    var button = h("button", { class: "btn", text: "Copy" });
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      ctx.signal("inspect");
      copyToClipboard(text, button);
    });
    return [
      h("div", { class: "cost-prehead" }, [h("span", { text: label }), button]),
      h("pre", { class: "mono cost-pre", text: text }),
    ];
  }

  // -------------------------------------------------------- severity tags

  var SEVERITY_CLASS = {
    critical: "bad", major: "warn", moderate: "warn", minor: "", low: "",
  };

  function severityTag(ctx, severity) {
    var key = String(severity || "").toLowerCase();
    var extra = SEVERITY_CLASS[key];
    return ctx.h("span", {
      class: "tag" + (extra ? " " + extra : ""),
      text: key || "unrated",
    });
  }

  var SEVERITY_RANK = { critical: 3, major: 2, moderate: 2, minor: 1 };

  function rankOf(severity) {
    return SEVERITY_RANK[String(severity || "").toLowerCase()] || 0;
  }

  // ============================================================== 1. deltas

  AgentDiff.block({
    id: "deltas",
    title: "Cost of the difference",
    question: "What did the difference cost?",
    group: "cost",
    size: "normal",

    relevance: function (ctx) {
      var delta = ctx.report && ctx.report.metrics_delta;
      if (!delta) return 0;
      // How much this block has to say = how far apart the two runs actually
      // are on the metrics that carry a direction.
      var worst = 0, seen = 0;
      METRICS.forEach(function (metric) {
        var pair = delta[metric.key];
        if (!pair || !isNum(pair.a) || !isNum(pair.b)) return;
        seen++;
        if (metric.better !== "lower" || pair.a === 0) return;
        worst = Math.max(worst, Math.abs(pair.b - pair.a) / Math.abs(pair.a));
      });
      if (!seen) return 0;
      return Math.min(1, 0.55 + worst);
    },

    render: function (el, ctx) {
      ensureStyle();
      var h = ctx.h;
      var report = ctx.report;
      var delta = report && report.metrics_delta;
      if (!delta) {
        ctx.empty(el, "This report carries no metrics_delta — nothing to compare.");
        return;
      }
      var names = agentNames(ctx);
      var outA = report.a && report.a.outcome;
      var outB = report.b && report.b.outcome;

      el.appendChild(h("div", { class: "cost-legend" }, [
        h("span", { class: "tag a", text: "A · " + names.a }),
        h("span", { class: "tag b", text: "B · " + names.b }),
        report.task && report.task.id
          ? h("span", { class: "cost-mini", text: report.task.id }) : null,
      ]));

      // Outcome first: success is the one comparison where the direction of
      // "better" is not a matter of taste, and it reframes every row below.
      var sameOutcome = null;
      if (outA && typeof outA.success === "boolean" &&
          outB && typeof outB.success === "boolean") {
        sameOutcome = outA.success === outB.success;
        el.appendChild(h("dl", { class: "kv" }, [
          h("dt", { text: "Outcome" }),
          h("dd", null, [
            h("span", {
              class: "tag " + (outA.success ? "good" : "bad"),
              text: "A " + (outA.success ? "succeeded" : "failed"),
            }),
            " ",
            h("span", {
              class: "tag " + (outB.success ? "good" : "bad"),
              text: "B " + (outB.success ? "succeeded" : "failed"),
            }),
          ]),
        ]));
      }

      var table = comparisonTable(ctx, function (key) { return delta[key]; }, {
        aLabel: "A", bLabel: "B", judge: true,
      });
      if (!table) {
        ctx.empty(el, "metrics_delta carries no numbers for this pair.");
        return;
      }
      el.appendChild(h("div", { class: "scroll-x" }, [table]));

      el.appendChild(h("p", { class: "caveat", text: DIRECTION_NOTE }));
      if (sameOutcome === false) {
        el.appendChild(h("p", {
          class: "caveat",
          text: "The two runs did not reach the same outcome, so the cheaper " +
                "run is not automatically the better one — read the spend " +
                "against " + (outA.success ? "A's" : "B's") + " success, not on its own.",
        }));
      }
    },
  });

  // ======================================================= 2. batch-summary

  AgentDiff.block({
    id: "batch-summary",
    title: "Across the batch",
    question: "How do they compare across every task?",
    group: "cost",
    size: "normal",

    relevance: function (ctx) {
      var agg = ctx.aggregate || {};
      var means = agg.means;
      if (!means || !means.a || !means.b) return 0;
      var tasks = isNum(agg.tasks) ? agg.tasks : 0;
      var rate = agg.success_rate || {};
      var gap = isNum(rate.a) && isNum(rate.b) ? Math.abs(rate.a - rate.b) : 0;
      // More tasks and a wider outcome gap both mean more to say here.
      return Math.min(1, 0.5 + Math.min(0.25, tasks / 32) + gap * 0.5);
    },

    render: function (el, ctx) {
      ensureStyle();
      var h = ctx.h, fmt = ctx.fmt, color = ctx.color;
      var agg = ctx.aggregate || {};
      var means = agg.means || {};
      if (!means.a || !means.b) {
        ctx.empty(el, "The batch aggregate carries no per-side means.");
        return;
      }
      var names = agentNames(ctx);
      var tasks = isNum(agg.tasks) ? agg.tasks : (ctx.reports || []).length;
      var rate = agg.success_rate || {};

      el.appendChild(h("div", { class: "cost-legend" }, [
        h("span", { class: "tag a", text: "A · " + names.a }),
        h("span", { class: "tag b", text: "B · " + names.b }),
        h("span", { class: "cost-mini", text: tasks + " task" + (tasks === 1 ? "" : "s") }),
      ]));

      // A rate on its own hides the sample: 87.5% of 8 is seven tasks, and
      // seven-of-eight is the number a reader can actually reason about.
      if (isNum(rate.a) || isNum(rate.b)) {
        var successRow = function (side, label, tint) {
          var value = rate[side];
          if (!isNum(value)) return null;
          var wins = tasks ? Math.round(value * tasks) : null;
          return [
            h("dt", { style: { color: tint }, text: label }),
            h("dd", {
              text: fmt.pct(value, value * 100 % 1 === 0 ? 0 : 1) +
                    (wins === null ? "" : "   (" + wins + " of " + tasks + ")"),
            }),
          ];
        };
        var kids = [];
        [["a", names.a, color.a], ["b", names.b, color.b]].forEach(function (row) {
          var pair = successRow(row[0], row[1], row[2]);
          if (pair) kids = kids.concat(pair);
        });
        if (isNum(rate.a) && isNum(rate.b) && rate.a !== rate.b) {
          var lead = rate.a > rate.b ? names.a : names.b;
          kids.push(h("dt", { text: "Gap" }));
          kids.push(h("dd", {
            style: { color: color.muted },
            text: fmt.num(Math.abs(rate.a - rate.b) * 100, 1) + " pt to " + lead,
          }));
        }
        if (kids.length) el.appendChild(h("dl", { class: "kv" }, kids));
      }

      var table = comparisonTable(ctx, function (key) {
        return { a: means.a[key], b: means.b[key] };
      }, { aLabel: "A mean", bLabel: "B mean", judge: true });
      if (table) {
        el.appendChild(h("div", { class: "scroll-x", style: { marginTop: "8px" } }, [table]));
      }

      var regressions = arr(agg.regressions);
      if (regressions.length) {
        el.appendChild(h("div", { class: "cost-prehead" }, [h("span", { text: "Regressions" })]));
        var list = h("div", { class: "cost-list" });
        regressions.forEach(function (line) {
          list.appendChild(h("div", { class: "cost-text", text: "• " + String(line) }));
        });
        el.appendChild(list);
      }

      el.appendChild(h("p", {
        class: "caveat",
        text: "Means over all " + tasks + " task(s), successes and failures alike. " +
              DIRECTION_NOTE +
              (regressions.length ? "" : " No regressions were flagged for this batch."),
      }));
    },
  });

  // ====================================================== 3. failure-origins

  AgentDiff.block({
    id: "failure-origins",
    title: "Where failures start",
    question: "Where do failures come from?",
    group: "cost",
    size: "normal",

    relevance: function (ctx) {
      var origins = (ctx.aggregate || {}).failure_origins;
      if (!origins) return 0;
      var keys = Object.keys(origins).filter(function (key) { return isNum(origins[key]); });
      if (!keys.length) return 0;
      // A single origin is a clean finding; a wide spread is a busier one.
      return Math.min(1, 0.6 + keys.length * 0.08);
    },

    render: function (el, ctx) {
      ensureStyle();
      var h = ctx.h, fmt = ctx.fmt, color = ctx.color;
      var agg = ctx.aggregate || {};
      var origins = agg.failure_origins || {};
      var rows = Object.keys(origins)
        .filter(function (key) { return isNum(origins[key]); })
        .map(function (key) { return { kind: key, share: origins[key] }; })
        .sort(function (x, y) {
          if (y.share !== x.share) return y.share - x.share;
          return x.kind < y.kind ? -1 : 1;   // ties broken by name, never by order of keys
        });

      if (!rows.length) {
        ctx.empty(el, "No failures were attributed in this batch, so there is no origin mix.");
        return;
      }

      // The denominator: attributed failures, not tasks. Counted from the
      // reports so the block can say "2 of 4" instead of a bare percentage —
      // but only when the fractions actually reconstruct whole runs.
      var failures = 0;
      (ctx.reports || []).forEach(function (report) {
        if (report && report.attribution && report.attribution.failed_agent) failures++;
      });
      var counted = failures > 0;
      if (counted) {
        var total = 0;
        rows.forEach(function (row) {
          var n = row.share * failures;
          if (Math.abs(n - Math.round(n)) > 0.02) counted = false;
          total += Math.round(n);
        });
        if (total !== failures) counted = false;
      }

      var tasks = isNum(agg.tasks) ? agg.tasks : (ctx.reports || []).length;
      el.appendChild(h("p", {
        class: "cost-note",
        text: counted
          ? "Share of the " + failures + " attributed failure(s) across " + tasks + " task(s)."
          : "Share of attributed failures across " + tasks + " task(s); fractions sum to 1.",
      }));

      var max = rows[0].share || 1;
      var chart = h("div", { class: "cost-bars" });
      rows.forEach(function (row) {
        var width = Math.max(2, Math.round((row.share / max) * 100));
        chart.appendChild(h("div", { text: row.kind.replace(/_/g, " ") }));
        chart.appendChild(h("div", { class: "cost-track" }, [
          h("i", { style: { width: width + "%", background: color.bad } }),
        ]));
        chart.appendChild(h("div", {
          class: "cost-val",
          text: fmt.pct(row.share, row.share * 100 % 1 === 0 ? 0 : 1) +
                (counted ? "  ·  " + Math.round(row.share * failures) + " of " + failures : ""),
        }));
      });
      el.appendChild(chart);

      el.appendChild(h("p", {
        class: "caveat",
        text: "Bars are scaled to the largest share, not to 100%. Origin is the " +
              "category of the divergence the engine held responsible — the place " +
              "the run left the path, not the step where it visibly went wrong.",
      }));
    },
  });

  // ============================================================== 4. issues

  AgentDiff.block({
    id: "issues",
    title: "Recurring issues",
    question: "What recurs across tasks?",
    group: "cost",
    size: "normal",

    relevance: function (ctx) {
      var issues = (ctx.aggregate || {}).issues;
      var list = issues && arr(issues.issues);
      if (!list || !list.length) return 0;
      var counts = (issues && issues.counts) || {};
      var critical = isNum(counts.critical) ? counts.critical : 0;
      return Math.min(1, 0.6 + critical * 0.1 + Math.min(0.2, list.length * 0.03));
    },

    render: function (el, ctx) {
      ensureStyle();
      var h = ctx.h, fmt = ctx.fmt;
      var issues = (ctx.aggregate || {}).issues || {};
      var list = arr(issues.issues);
      if (!list.length) {
        ctx.empty(el, "No divergences collapsed into a recurring issue for this batch.");
        return;
      }

      var counts = issues.counts || {};
      var tags = h("div", { class: "cost-legend" });
      ["critical", "major", "minor"].forEach(function (name) {
        if (!isNum(counts[name]) || counts[name] <= 0) return;
        tags.appendChild(h("span", {
          class: "tag" + (SEVERITY_CLASS[name] ? " " + SEVERITY_CLASS[name] : ""),
          text: counts[name] + " " + name,
        }));
      });
      if (isNum(issues.total_divergences)) {
        tags.appendChild(h("span", {
          class: "cost-mini",
          text: issues.total_divergences + " divergence(s) → " +
                (isNum(issues.active) ? issues.active : list.length) + " active issue(s)",
        }));
      }
      if (tags.childNodes.length) el.appendChild(tags);

      if (issues.narrative) {
        el.appendChild(h("p", { class: "cost-note", text: issues.narrative }));
      }

      // Impact order: anything that caused a failure outranks anything that
      // only cost tokens, and within that, the expensive ones come first.
      var sorted = list.slice().sort(function (x, y) {
        var bySeverity = rankOf(y.severity) - rankOf(x.severity);
        if (bySeverity) return bySeverity;
        var byFailures = (y.failures_caused || 0) - (x.failures_caused || 0);
        if (byFailures) return byFailures;
        var byTokens = (y.extra_tokens || 0) - (x.extra_tokens || 0);
        if (byTokens) return byTokens;
        return String(x.id) < String(y.id) ? -1 : 1;
      });

      var container = h("div", { class: "cost-list" });
      sorted.forEach(function (issue) {
        container.appendChild(issueCard(ctx, issue));
      });
      el.appendChild(container);

      var caveats = [];
      if (isNum(issues.suppressed) && issues.suppressed > 0) {
        caveats.push(issues.suppressed + " issue(s) are suppressed by .agentdiffignore — " +
          "still listed and marked, excluded only from the headline counts.");
      }
      caveats.push("Severity is mechanical: critical = caused a failure, " +
        "major = wasted ≥500 tokens, otherwise minor.");
      el.appendChild(h("p", { class: "caveat", text: caveats.join(" ") }));

      function issueCard(ctx, issue) {
        var card = h("div", { class: "cost-card" });
        card.appendChild(h("h4", { text: issue.title || issue.id || "untitled issue" }));

        var row = h("div", { class: "cost-tags" }, [
          severityTag(ctx, issue.severity),
          issue.kind ? h("span", { class: "tag", text: String(issue.kind).replace(/_/g, " ") }) : null,
          issue.recurring ? h("span", { class: "tag warn", text: "recurring" }) : null,
          issue.suppressed ? h("span", { class: "tag", text: "suppressed" }) : null,
        ]);
        arr(issue.agents).forEach(function (agent) {
          row.appendChild(h("span", { class: "tag b", text: agent }));
        });
        card.appendChild(row);

        var facts = [];
        if (isNum(issue.occurrence_count)) {
          facts.push(h("dt", { text: "Occurrences" }));
          facts.push(h("dd", {
            text: issue.occurrence_count + " on " + arr(issue.tasks).length + " task(s)",
          }));
        }
        if (isNum(issue.failures_caused)) {
          facts.push(h("dt", { text: "Failures" }));
          facts.push(h("dd", { text: issue.failures_caused + " caused" }));
        }
        if (isNum(issue.extra_tokens) || isNum(issue.extra_steps)) {
          facts.push(h("dt", { text: "Waste" }));
          facts.push(h("dd", {
            text: [
              isNum(issue.extra_steps) ? "+" + fmt.int(issue.extra_steps) + " steps" : null,
              isNum(issue.extra_tokens) ? "+" + fmt.int(issue.extra_tokens) + " tok" : null,
              isNum(issue.extra_latency_s) ? "+" + fmt.sec(issue.extra_latency_s) : null,
            ].filter(Boolean).join("  ·  "),
          }));
        }
        if (arr(issue.tasks).length) {
          facts.push(h("dt", { text: "Tasks" }));
          facts.push(h("dd", { class: "mono", text: arr(issue.tasks).join(", ") }));
        }
        if (facts.length) card.appendChild(h("dl", { class: "kv" }, facts));

        if (issue.summary) card.appendChild(h("p", { class: "cost-text", text: issue.summary }));

        var occurrences = arr(issue.occurrences);
        if (occurrences.length) {
          var detail = h("div", { class: "cost-detail", style: { display: "none" } });
          occurrences.forEach(function (item) {
            detail.appendChild(h("div", { class: "cost-mini" }, [
              h("span", { class: "mono cost-strong", text: item.task || "?" }),
              item.caused_failure ? h("span", { class: "tag bad", text: "caused failure" }) : null,
              " ",
              String(item.summary || ""),
            ]));
          });
          var toggle = h("button", {
            class: "cost-toggle",
            text: "▸ " + occurrences.length + " occurrence(s)",
          });
          toggle.addEventListener("click", function (event) {
            event.stopPropagation();
            var open = detail.style.display === "none";
            detail.style.display = open ? "block" : "none";
            toggle.textContent = (open ? "▾ " : "▸ ") + occurrences.length + " occurrence(s)";
            if (open) ctx.signal("inspect");
          });
          card.appendChild(toggle);
          card.appendChild(detail);
        }
        return card;
      }
    },
  });

  // ===================================================== 5. recommendations

  AgentDiff.block({
    id: "recommendations",
    title: "What to change",
    question: "What should I change?",
    group: "cost",
    size: "normal",

    relevance: function (ctx) {
      var list = arr((ctx.aggregate || {}).recommendations);
      if (!list.length) return 0;
      var critical = list.filter(function (item) { return rankOf(item.severity) >= 3; }).length;
      return Math.min(1, 0.65 + critical * 0.1 + Math.min(0.15, list.length * 0.03));
    },

    render: function (el, ctx) {
      ensureStyle();
      var h = ctx.h;
      var list = arr((ctx.aggregate || {}).recommendations);
      if (!list.length) {
        ctx.empty(el, "No recommendations were derived from this batch.");
        return;
      }

      var sorted = list.slice().sort(function (x, y) {
        var bySeverity = rankOf(y.severity) - rankOf(x.severity);
        if (bySeverity) return bySeverity;
        var byEvidence = arr(y.evidence_tasks).length - arr(x.evidence_tasks).length;
        if (byEvidence) return byEvidence;
        return String(x.category || "") < String(y.category || "") ? -1 : 1;
      });

      var container = h("div", { class: "cost-list" });
      sorted.forEach(function (rec) {
        var card = h("div", { class: "cost-card" });

        card.appendChild(h("div", { class: "cost-tags" }, [
          severityTag(ctx, rec.severity),
          rec.category
            ? h("span", { class: "tag", text: String(rec.category).replace(/_/g, " ") }) : null,
          rec.agent ? h("span", { class: "tag b", text: rec.agent }) : null,
        ]));

        if (rec.finding) {
          card.appendChild(h("p", { class: "cost-text", text: rec.finding }));
        }

        var facts = [];
        if (rec.expected_gain) {
          facts.push(h("dt", { text: "Expected gain" }));
          facts.push(h("dd", { style: { color: ctx.color.good }, text: rec.expected_gain }));
        }
        if (arr(rec.evidence_tasks).length) {
          facts.push(h("dt", { text: "Evidence" }));
          facts.push(h("dd", { class: "mono", text: arr(rec.evidence_tasks).join(", ") }));
        }
        if (facts.length) card.appendChild(h("dl", { class: "kv" }, facts));

        if (rec.suggested_prompt) {
          copyablePrompt(ctx, "Suggested prompt", rec.suggested_prompt)
            .forEach(function (node) { card.appendChild(node); });
        }
        container.appendChild(card);
      });
      el.appendChild(container);

      el.appendChild(h("p", {
        class: "caveat",
        text: "Expected gain is a ceiling read off the tasks named in the evidence — " +
              "what these runs would have saved had the issue not occurred, not a " +
              "prediction for the next batch.",
      }));
    },
  });

  // ============================================================ 6. playbook

  AgentDiff.block({
    id: "playbook",
    title: "What good looks like",
    question: "What habits are winning?",
    group: "cost",
    size: "normal",

    relevance: function (ctx) {
      var list = arr((ctx.aggregate || {}).playbook);
      if (!list.length) return 0;
      return Math.min(1, 0.6 + Math.min(0.3, list.length * 0.08));
    },

    render: function (el, ctx) {
      ensureStyle();
      var h = ctx.h;
      var list = arr((ctx.aggregate || {}).playbook);
      if (!list.length) {
        ctx.empty(el, "No winning habit generalized across enough tasks to be worth naming.");
        return;
      }

      el.appendChild(h("p", {
        class: "cost-note",
        text: "The mirror of the issue list: decisions that went right often enough, " +
              "and mattered enough, to be worth repeating.",
      }));

      var container = h("div", { class: "cost-list" });
      list.forEach(function (entry) {
        var card = h("div", { class: "cost-card" });
        card.appendChild(h("h4", { text: entry.habit || "unnamed habit" }));

        var tags = h("div", { class: "cost-tags" }, [
          entry.kind ? h("span", { class: "tag good", text: String(entry.kind).replace(/_/g, " ") }) : null,
        ]);
        arr(entry.agents).forEach(function (agent) {
          tags.appendChild(h("span", { class: "tag a", text: agent }));
        });
        card.appendChild(tags);

        var facts = [];
        if (entry.impact) {
          facts.push(h("dt", { text: "Impact" }));
          facts.push(h("dd", { style: { color: ctx.color.good }, text: entry.impact }));
        }
        if (entry.evidence) {
          facts.push(h("dt", { text: "Evidence" }));
          facts.push(h("dd", { text: entry.evidence }));
        }
        if (facts.length) card.appendChild(h("dl", { class: "kv" }, facts));
        container.appendChild(card);
      });
      el.appendChild(container);

      el.appendChild(h("p", {
        class: "caveat",
        text: "Attribution, not endorsement: a habit is listed because it decided " +
              "divergences in this batch's favour, which is evidence it worked here " +
              "rather than proof it generalizes.",
      }));
    },
  });
})(typeof window !== "undefined" ? window : this);
