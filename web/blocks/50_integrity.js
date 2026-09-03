/* AgentDiff blocks — process integrity (v22).
 *
 * Outcome-only evaluation is blind by construction: a run can satisfy its
 * oracle while looping, swallowing an error or writing something nobody
 * asked for, and a run can fail with a spotless process because the *oracle*
 * is wrong. `deepcompare.process` computes the deterministic subset of that
 * from the logged trace — no judge, no re-execution. These six cards are its
 * interface.
 *
 * Two rules from the analysis carry straight through into the pixels:
 *
 *   - **A qualifier is never dropped to make a card look tidy.**
 *     `recovery.basis`, `side_effects.basis`, `false_success.measurable`,
 *     `schema.measurable` and `grounding.schema_checked` change what the
 *     number means, so they are rendered beside the number, not under it.
 *   - **Unmeasurable is not clean.** Without a declared tool list there is
 *     nothing to check a call against, and an unchecked call is not a valid
 *     one. Those states get their own hatched, dashed treatment — never a
 *     green tick, which would be a lie told in a colour.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // Bound at the top of each render; the core hands out the same helpers
  // every time, so this is a convenience rather than shared state.
  var H = null, S = null, C = null, F = null;
  function bind(ctx) { H = ctx.h; S = ctx.svg; C = ctx.color; F = ctx.fmt; }

  // ------------------------------------------------------------------ style

  var STYLE_ID = "agentdiff-integrity-css";
  var CSS = [
    ".ig-lead{font-size:13px;line-height:1.5;margin:0 0 8px;color:var(--ink)}",
    ".ig-lead b{font-weight:650}",
    ".ig-sub{font-size:11.5px;color:var(--ink-3);margin:0 0 8px;line-height:1.45}",
    ".ig-cols{display:flex;flex-wrap:wrap;gap:8px}",
    ".ig-col{flex:1 1 250px;min-width:0}",
    ".ig-panel{border:1px solid var(--rule);border-radius:9px;padding:8px 9px;min-width:0}",
    ".ig-panel+.ig-panel{margin-top:8px}",
    ".ig-panel.sa{border-left:3px solid var(--a)}",
    ".ig-panel.sb{border-left:3px solid var(--b)}",
    ".ig-head{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;margin-bottom:5px}",
    ".ig-name{font-weight:620;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;",
    "white-space:nowrap;max-width:100%;min-width:0}",
    ".ig-note{font-size:11.5px;line-height:1.45;color:var(--ink-2);margin:5px 0 0}",
    ".ig-mini{font-size:11px;line-height:1.45;color:var(--ink-3);margin:5px 0 0}",
    ".ig-tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}",
    ".ig-chip{font-family:var(--mono);font-size:11px;color:var(--ink-2);background:var(--surface-2);",
    "border:1px solid var(--rule);border-radius:5px;padding:1px 5px;white-space:nowrap}",
    ".ig-chip.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 50%,transparent)}",
    ".ig-chip.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 50%,transparent)}",
    ".ig-chip.good{color:var(--good);border-color:color-mix(in srgb,var(--good) 50%,transparent)}",

    /* verdict badges — the two disagreements are the loud ones on purpose */
    ".ig-verdict{display:inline-flex;align-items:center;gap:5px;border-radius:7px;",
    "padding:3px 8px;font-size:11.5px;font-weight:650;letter-spacing:-.01em;",
    "border:1px solid var(--rule-2);background:var(--surface-2);color:var(--ink-2)}",
    ".ig-verdict .gl{font-size:12px;line-height:1}",
    ".ig-verdict.ok{color:var(--good);border-color:color-mix(in srgb,var(--good) 45%,transparent);",
    "background:color-mix(in srgb,var(--good) 12%,transparent)}",
    ".ig-verdict.cause{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,transparent);",
    "background:color-mix(in srgb,var(--bad) 12%,transparent)}",
    ".ig-verdict.patho{color:var(--warn);border:1.5px solid var(--warn);",
    "background:color-mix(in srgb,var(--warn) 20%,transparent);",
    "box-shadow:0 0 0 3px color-mix(in srgb,var(--warn) 16%,transparent)}",
    ".ig-verdict.oracle{color:var(--accent);border:1.5px solid var(--accent);",
    "background:color-mix(in srgb,var(--accent) 16%,transparent);",
    "box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 14%,transparent)}",
    ".ig-panel.patho{border-left:3px solid var(--warn);",
    "background:color-mix(in srgb,var(--warn) 7%,transparent)}",
    ".ig-panel.oracle{border-left:3px solid var(--accent);",
    "background:color-mix(in srgb,var(--accent) 6%,transparent)}",
    ".ig-why{font-size:11px;line-height:1.45;margin:6px 0 0;padding-left:7px;color:var(--ink-2)}",
    ".ig-why.patho{border-left:2px solid var(--warn)}",
    ".ig-why.oracle{border-left:2px solid var(--accent)}",

    /* the 2x2: outcome across, process down */
    ".ig-quad{display:grid;grid-template-columns:auto 1fr 1fr;gap:4px;margin:2px 0 4px}",
    ".ig-quad .hd{font-size:11px;text-transform:uppercase;letter-spacing:.08em;",
    "color:var(--ink-3);align-self:end;padding-bottom:2px}",
    ".ig-quad .rw{font-size:11px;text-transform:uppercase;letter-spacing:.08em;",
    "color:var(--ink-3);align-self:center;writing-mode:horizontal-tb;max-width:66px;line-height:1.25}",
    ".ig-cell{border:1px solid var(--rule);border-radius:8px;padding:6px 7px;min-height:56px;",
    "display:flex;flex-direction:column;gap:5px;justify-content:space-between;min-width:0}",
    ".ig-cell .lab{font-size:11px;line-height:1.3;color:var(--ink-3)}",
    ".ig-cell.dis{border-style:dashed;border-color:var(--rule-2)}",
    ".ig-cell.on .lab{color:var(--ink);font-weight:600}",
    ".ig-cell.on{background:var(--surface-2)}",
    ".ig-cell.dis.on.patho{border:1.5px solid var(--warn);border-style:solid;",
    "background:color-mix(in srgb,var(--warn) 16%,transparent)}",
    ".ig-cell.dis.on.patho .lab{color:var(--warn)}",
    ".ig-cell.dis.on.oracle{border:1.5px solid var(--accent);border-style:solid;",
    "background:color-mix(in srgb,var(--accent) 14%,transparent)}",
    ".ig-cell.dis.on.oracle .lab{color:var(--accent)}",
    ".ig-who{display:flex;flex-wrap:wrap;gap:3px;min-height:16px}",

    /* flag matrix */
    "table.ig-mx{border-collapse:collapse;width:100%;font-size:11.5px}",
    ".ig-mx th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3);",
    "font-weight:500;text-align:left;padding:0 6px 4px 0;white-space:nowrap}",
    ".ig-mx th.c,.ig-mx td.c{text-align:center;width:40px;padding-left:2px;padding-right:2px}",
    ".ig-mx td{padding:3px 6px 3px 0;border-top:1px solid var(--rule);vertical-align:middle;line-height:1.3}",
    ".ig-mx td.k{padding-left:7px}",
    ".ig-mx tr.differs td{background:color-mix(in srgb,var(--accent) 8%,transparent)}",
    ".ig-mx tr.differs td.k{box-shadow:inset 2px 0 0 var(--accent)}",
    ".ig-mx tr.shared td.k{box-shadow:inset 2px 0 0 var(--bad)}",
    ".ig-mx tr.differs td.k,.ig-mx tr.shared td.k{font-weight:600}",
    ".ig-mx .dag{font-size:11px;color:var(--ink-3);vertical-align:super;margin-left:1px}",
    ".ig-legend{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;margin-top:7px;",
    "font-size:11px;color:var(--ink-3)}",
    ".ig-legend .k{display:inline-flex;align-items:center;gap:4px}",

    /* bars + ledgers */
    ".ig-bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--surface-2);",
    "border:1px solid var(--rule);margin:6px 0 4px}",
    ".ig-bar>i{display:block;height:100%}",
    ".ig-swatch{display:inline-block;width:9px;height:9px;border-radius:2px;flex:none}",
    ".ig-rows{margin-top:6px}",
    ".ig-row{display:flex;gap:6px;align-items:baseline;font-size:11.5px;padding:3px 0;",
    "border-top:1px solid var(--rule);min-width:0}",
    ".ig-row .sp{flex:1 1 120px;min-width:0;color:var(--ink-2);overflow-wrap:anywhere}",
    ".ig-list{max-height:190px;overflow:auto}",

    /* unmeasurable: hatched, dashed, never green */
    ".ig-unmeas{border:1px dashed var(--rule-2);border-radius:9px;padding:8px 9px;margin-top:6px;",
    "background:repeating-linear-gradient(135deg,transparent,transparent 5px,",
    "color-mix(in srgb,var(--ink-3) 9%,transparent) 5px,",
    "color-mix(in srgb,var(--ink-3) 9%,transparent) 10px)}",
    ".ig-unmeas .hd{display:flex;flex-wrap:wrap;align-items:center;gap:5px 7px;",
    "font-size:11.5px;font-weight:640;color:var(--ink-2)}",
    ".ig-unmeas .hd>.ig-badge{flex:none}",
    ".ig-unmeas .hd>span:last-child{flex:1 1 130px;min-width:0}",
    ".ig-unmeas p{margin:4px 0 0;font-size:11px;line-height:1.45;color:var(--ink-2)}",
    ".ig-unmeas b{color:var(--ink)}",
    ".ig-badge{display:inline-flex;align-items:center;gap:4px;border:1px dashed var(--rule-2);",
    "border-radius:999px;padding:0 7px;font-size:11px;color:var(--ink-3);background:var(--surface)}",
    ".ig-basis{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--ink-3);",
    "border:1px solid var(--rule);border-radius:999px;padding:0 7px;background:var(--surface-2);white-space:nowrap}",
    ".ig-basis.inferred{border-style:dashed;color:var(--warn);",
    "border-color:color-mix(in srgb,var(--warn) 45%,transparent)}",
    ".ig-svg{display:block;max-width:100%}",
    ".ig-hr{height:1px;background:var(--rule);margin:9px 0}",
  ].join("");

  function ensureStyle() {
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.appendChild(document.createTextNode(CSS));
      (document.head || document.documentElement).appendChild(node);
    } catch (err) { /* styling is a nicety; the markup still reads without it */ }
  }

  // ------------------------------------------------------------- data access

  function processOf(ctx) {
    var report = ctx && ctx.report;
    var p = report && report.process;
    if (!p || typeof p !== "object" || !p.a || !p.b) return null;
    return p;
  }

  function sides(p) { return [{ k: "a", d: p.a }, { k: "b", d: p.b }]; }

  function nameOf(d, which) {
    var name = d && d.agent;
    return name || (which === "a" ? "Agent A" : "Agent B");
  }

  function num(value) {
    return typeof value === "number" && !isNaN(value) ? value : null;
  }

  function count(list) { return Array.isArray(list) ? list.length : 0; }

  function plural(n, word) {
    var value = num(n) || 0;
    return F.int(value) + " " + word + (value === 1 ? "" : "s");
  }

  //: the eleven checks, in the order `process._PATHOLOGIES` declares them, so
  //: the matrix reads in the same order the narrative is built from.
  var FLAGS = [
    { key: "false_success", label: "Claimed a completion it never made" },
    { key: "looped", label: "Repeated the same call-and-result" },
    { key: "loop_block", label: "Cycled through a block of calls" },
    { key: "repeated_calls", label: "Made the same call more than once" },
    { key: "no_information_steps", label: "Took steps that returned nothing new" },
    { key: "swallowed_error", label: "Hit an error and never recovered" },
    { key: "blind_write", label: "Wrote before reading anything" },
    { key: "budget_pressure", label: "Finished on the edge of its budget" },
    { key: "undeclared_tools", label: "Called a tool it was not offered" },
    { key: "invented_arguments", label: "Used arguments with no source" },
    { key: "schema_violation", label: "Called a tool with arguments that do not typecheck" },
  ];

  var FLAG_LABEL = {};
  FLAGS.forEach(function (f) { FLAG_LABEL[f.key] = f.label; });

  function inferred(basis) {
    return typeof basis === "string" && basis.indexOf("declared") !== 0;
  }

  /* Three states, not two.
   *
   * `clear` means a check ran and found nothing. `unchecked` means the trace
   * never carried what the check needs — no tool list, no parameter schemas,
   * no declared budget, no argument long enough to trace. Collapsing the
   * second into the first is exactly the lie this module exists to avoid, so
   * they are different states with different marks. */
  function checkState(d) {
    var flags = (d && d.gap && d.gap.flags) || {};
    var se = (d && d.side_effects) || {};
    var rec = (d && d.recovery) || {};
    var gr = (d && d.grounding) || {};
    var sc = (d && d.schema) || {};
    var fs = (d && d.false_success) || {};
    var tm = (d && d.termination) || {};

    return function (key) {
      var raised = !!flags[key];
      var measurable = true, why = null, basis = null;
      switch (key) {
        case "false_success":
          measurable = fs.measurable !== false;
          why = "no tool list declared — whether this run could write anything at all is unknown";
          break;
        case "undeclared_tools":
          measurable = !!gr.schema_checked;
          why = "no tool list declared — a call cannot be checked against what was offered";
          break;
        case "schema_violation":
          measurable = sc.measurable !== false;
          why = sc.note || "no tool parameter schemas declared; call validity unchecked";
          break;
        case "invented_arguments":
          measurable = (num(gr.arguments_checked) || 0) > 0;
          why = "no argument value long enough to trace to a source (short values match by accident)";
          break;
        case "budget_pressure":
          measurable = tm.max_steps !== null && tm.max_steps !== undefined;
          why = "no step budget declared by the harness — there is no edge to be near";
          break;
        case "swallowed_error":
          basis = rec.basis || null;
          break;
        case "blind_write":
          basis = se.basis || null;
          break;
        default:
          break;
      }
      if (!measurable) {
        return { state: "unchecked", raised: false, why: why, basis: null };
      }
      return { state: raised ? "raised" : "clear", raised: raised, why: null, basis: basis };
    };
  }

  function unmeasurableKeys(d) {
    var state = checkState(d);
    return FLAGS.filter(function (f) { return state(f.key).state === "unchecked"; })
                .map(function (f) { return f.key; });
  }

  // -------------------------------------------------------------- primitives

  function tag(text, kind) {
    return H("span", { class: kind ? "tag " + kind : "tag", text: text });
  }

  function chip(text, kind) {
    return H("span", { class: kind ? "ig-chip " + kind : "ig-chip", text: text });
  }

  function sideTag(p, which) {
    return tag(F.truncate(nameOf(p[which], which), 22), which === "a" ? "a" : "b");
  }

  /* A basis pill. Inferred bases are dashed and warn-coloured because they
   * are a heuristic reading of observation text or tool names, and the
   * difference is the whole point of printing it. */
  function basisPill(label, basis) {
    if (!basis) return null;
    var isInferred = inferred(basis);
    return H("span", {
      class: "ig-basis" + (isInferred ? " inferred" : ""),
      title: isInferred
        ? "Heuristic: this count was read out of the log's text, not declared by it."
        : "The log declared this directly.",
      text: label + ": " + basis,
    });
  }

  function unmeasurable(title, why, consequence) {
    return H("div", { class: "ig-unmeas" }, [
      H("div", { class: "hd" }, [
        H("span", { class: "ig-badge", text: "? unmeasurable" }),
        H("span", { text: title }),
      ]),
      why ? H("p", { text: why }) : null,
      consequence ? H("p", null, [H("b", { text: consequence })]) : null,
    ]);
  }

  function bar(segments) {
    var total = segments.reduce(function (sum, s) { return sum + Math.max(0, s.value || 0); }, 0);
    var wrap = H("div", { class: "ig-bar" });
    if (!total) {
      wrap.appendChild(H("i", { style: { width: "100%", background: "var(--rule)" } }));
      return wrap;
    }
    segments.forEach(function (s) {
      var value = Math.max(0, s.value || 0);
      if (!value) return;
      wrap.appendChild(H("i", {
        title: s.label + ": " + value,
        style: { width: (value / total * 100).toFixed(2) + "%", background: s.fill },
      }));
    });
    return wrap;
  }

  function legend(entries) {
    var wrap = H("div", { class: "ig-legend" });
    entries.forEach(function (entry) {
      if (!entry) return;
      wrap.appendChild(H("span", { class: "k" }, [
        entry.node || H("span", { class: "ig-swatch", style: { background: entry.fill } }),
        H("span", { text: entry.label }),
      ]));
    });
    return wrap;
  }

  function rows(list, limit) {
    var wrap = H("div", { class: "ig-rows ig-list" });
    list.slice(0, limit || 24).forEach(function (node) { wrap.appendChild(node); });
    if (list.length > (limit || 24)) {
      wrap.appendChild(H("div", { class: "ig-row" }, [
        H("span", { class: "sp", text: "+" + (list.length - (limit || 24)) + " more" }),
      ]));
    }
    return wrap;
  }

  function stepRow(index, name, detail, kind) {
    return H("div", { class: "ig-row" }, [
      chip("step " + index, kind),
      H("span", { class: "sp", text: (name || "step") + (detail ? " — " + detail : "") }),
    ]);
  }

  // ---------------------------------------------------------- matrix glyphs

  function markRaised() {
    return S("svg", { width: 14, height: 14, viewBox: "0 0 14 14", "aria-label": "raised" }, [
      S("rect", { x: 2.5, y: 2.5, width: 9, height: 9, rx: 2, fill: C.bad, "fill-opacity": 0.85,
                  stroke: C.bad, "stroke-width": 1.2 }),
    ]);
  }

  function markClear() {
    return S("svg", { width: 14, height: 14, viewBox: "0 0 14 14", "aria-label": "clear" }, [
      S("circle", { cx: 7, cy: 7, r: 2.6, fill: "none", stroke: C.muted, "stroke-width": 1.2,
                    "stroke-opacity": 0.75 }),
    ]);
  }

  function markUnchecked() {
    return S("svg", { width: 14, height: 14, viewBox: "0 0 14 14", "aria-label": "unchecked" }, [
      S("circle", { cx: 7, cy: 7, r: 5.2, fill: "none", stroke: C.muted, "stroke-width": 1.1,
                    "stroke-dasharray": "2 1.8" }),
      S("text", { x: 7, y: 10.2, "text-anchor": "middle", "font-size": 8.5, fill: C.muted,
                  "font-family": "var(--sans)", text: "?" }),
    ]);
  }

  function markFor(state) {
    if (state === "raised") return markRaised();
    if (state === "unchecked") return markUnchecked();
    return markClear();
  }

  // ------------------------------------------------------------- responsive
  //
  // The core renders a block while its card is still detached, so clientWidth
  // is 0 at paint time. Draw once at a nominal width, re-measure on the next
  // frame, repaint only if the real width differs — layout-driven, so the
  // result is identical on every load.

  var painters = [];
  var resizeTimer = null;

  function measure(el) {
    var w = 0;
    try { w = el.clientWidth || 0; } catch (err) { w = 0; }
    if (!w) w = 320;
    return Math.max(180, w - 2);
  }

  function sized(el, draw) {
    var host = H("div");
    el.appendChild(host);
    var last = -1;
    function go() {
      var w = measure(el);
      if (w === last) return;
      last = w;
      host.innerHTML = "";
      try { draw(host, w); }
      catch (err) { console.warn("AgentDiff integrity: draw failed", err); }
    }
    go();
    if (typeof global.requestAnimationFrame === "function") {
      global.requestAnimationFrame(function () { if (el.isConnected) go(); });
    }
    painters.push({ el: el, go: go });
    if (painters.length > 64) {
      painters = painters.filter(function (p) { return p.el.isConnected; });
    }
    return host;
  }

  try {
    global.addEventListener("resize", function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        resizeTimer = null;
        painters = painters.filter(function (p) { return p.el.isConnected; });
        painters.forEach(function (p) {
          try { p.go(); } catch (err) { /* one stale painter must not stop the rest */ }
        });
      }, 140);
    });
  } catch (err) { /* no window: nothing to resize */ }

  // ============================================================== 1. the gap

  var VERDICT_STYLE = {
    "passed cleanly": { cls: "ok", glyph: "✓", agree: true },
    "failed with cause": { cls: "cause", glyph: "✕", agree: true },
    "passed but pathological": { cls: "patho", glyph: "⚠", agree: false },
    "failed but clean": { cls: "oracle", glyph: "?", agree: false },
  };

  var VERDICT_WHY = {
    "passed but pathological":
      "A leaderboard scores this identically to a clean pass. The oracle was " +
      "satisfied; the run was not sound.",
    "failed but clean":
      "Nothing visible went wrong, yet it was marked wrong — that is evidence " +
      "about the grader, not only about the agent. Worth checking the oracle over.",
  };

  function verdictBadge(verdict) {
    var style = VERDICT_STYLE[verdict] || { cls: "", glyph: "•" };
    return H("span", { class: "ig-verdict " + style.cls }, [
      H("span", { class: "gl", text: style.glyph }),
      H("span", { text: verdict || "no verdict" }),
    ]);
  }

  function quadrant(p) {
    var occupants = { "passed cleanly": [], "passed but pathological": [],
                      "failed with cause": [], "failed but clean": [] };
    sides(p).forEach(function (s) {
      var verdict = s.d.gap && s.d.gap.verdict;
      if (occupants[verdict]) occupants[verdict].push(s.k);
    });

    function cell(verdict) {
      var style = VERDICT_STYLE[verdict];
      var here = occupants[verdict];
      var who = H("div", { class: "ig-who" });
      here.forEach(function (which) { who.appendChild(sideTag(p, which)); });
      return H("div", {
        class: "ig-cell" + (style.agree ? "" : " dis " + style.cls) + (here.length ? " on" : ""),
        title: style.agree ? "outcome and process agree" : "outcome and process disagree",
      }, [
        H("div", { class: "lab" }, [
          H("span", { class: "gl", text: style.glyph + " " }),
          H("span", { text: verdict }),
        ]),
        who,
      ]);
    }

    return H("div", { class: "ig-quad" }, [
      H("div", null, ""),
      H("div", { class: "hd", text: "oracle: passed" }),
      H("div", { class: "hd", text: "oracle: failed" }),
      H("div", { class: "rw", text: "process clean" }),
      cell("passed cleanly"),
      cell("failed but clean"),
      H("div", { class: "rw", text: "process flagged" }),
      cell("passed but pathological"),
      cell("failed with cause"),
    ]);
  }

  AgentDiff.block({
    id: "gap",
    title: "Outcome–process gap",
    question: "Do the verdict and the process agree?",
    group: "integrity",
    size: "wide",

    relevance: function (ctx) {
      var p = processOf(ctx);
      if (!p) return 0;
      var disagreement = sides(p).some(function (s) {
        var style = VERDICT_STYLE[s.d.gap && s.d.gap.verdict];
        return style && !style.agree;
      });
      return disagreement ? 1 : 0.82;
    },

    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var p = processOf(ctx);
      if (!p) {
        ctx.empty(el, "This report predates process integrity (v22) — no process " +
                      "analysis was recorded for it.");
        return;
      }

      var loud = sides(p).filter(function (s) {
        var style = VERDICT_STYLE[s.d.gap && s.d.gap.verdict];
        return style && !style.agree;
      });

      el.appendChild(H("p", { class: "ig-lead" }, [
        loud.length
          ? H("span", null, [
              H("b", { text: "The outcome and the process disagree" }),
              H("span", { text: " on " + (loud.length === 2 ? "both runs" : nameOf(loud[0].d, loud[0].k)) +
                                ". The verdict alone would not tell you." }),
            ])
          : H("span", { text: "Outcome and process agree on both runs — the verdict is " +
                              "corroborated by what the trace shows, as far as it can be checked." }),
      ]));

      el.appendChild(quadrant(p));

      sides(p).forEach(function (s) {
        var gap = s.d.gap || {};
        var style = VERDICT_STYLE[gap.verdict] || { cls: "", agree: true };
        var panel = H("div", {
          class: "ig-panel s" + s.k + (style.agree ? "" : " " + style.cls),
        });
        panel.appendChild(H("div", { class: "ig-head" }, [
          H("span", { class: "ig-name", text: nameOf(s.d, s.k) }),
          tag(s.k.toUpperCase(), s.k),
          verdictBadge(gap.verdict),
        ]));
        panel.appendChild(H("p", { class: "ig-note", text: gap.narrative || "" }));

        var raised = Array.isArray(gap.raised) ? gap.raised : [];
        if (raised.length) {
          var tags = H("div", { class: "ig-tags" });
          raised.forEach(function (key) {
            tags.appendChild(tag(FLAG_LABEL[key] || key, "bad"));
          });
          panel.appendChild(tags);
        }

        // A clean run is only as clean as the checks that could run on it.
        var blind = unmeasurableKeys(s.d);
        if (!raised.length) {
          panel.appendChild(H("div", { class: "ig-tags" }, [tag("no flags raised", "good")]));
        }
        if (blind.length) {
          panel.appendChild(H("p", { class: "ig-mini", text:
            blind.length + " of the " + FLAGS.length + " checks could not be measured on this " +
            "trace" + (raised.length ? "" : ", so “clean” here means “nothing found by the checks " +
            "that could run”") + ": " + blind.map(function (k) {
              return (FLAG_LABEL[k] || k).toLowerCase();
            }).join("; ") + "." }));
        }

        if (VERDICT_WHY[gap.verdict]) {
          panel.appendChild(H("p", { class: "ig-why " + style.cls, text: VERDICT_WHY[gap.verdict] }));
        }
        el.appendChild(panel);
      });

      if (p.narrative) {
        el.appendChild(H("p", { class: "caveat", text: "Pair: " + p.narrative }));
      }
      el.appendChild(H("p", { class: "caveat", text:
        "Computed from the logged trace alone — no judge, no re-execution. It " +
        "cannot see whether the content of an answer was right, only whether the " +
        "process that produced it contradicts itself." }));
    },
  });

  // ======================================================== 2. flag matrix

  AgentDiff.block({
    id: "integrity-flags",
    title: "Process checks",
    question: "Which process checks fired, on which side?",
    group: "integrity",
    size: "normal",

    relevance: function (ctx) {
      var p = processOf(ctx);
      if (!p) return 0;
      var differing = count(p.differing_flags);
      var shared = count(p.shared_flags);
      if (differing) return 0.95;
      if (shared) return 0.8;
      return 0.45;
    },

    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var p = processOf(ctx);
      if (!p) {
        ctx.empty(el, "This report predates process integrity (v22) — there are no " +
                      "process checks to compare.");
        return;
      }

      var differing = Array.isArray(p.differing_flags) ? p.differing_flags : [];
      var shared = Array.isArray(p.shared_flags) ? p.shared_flags : [];
      var stateA = checkState(p.a), stateB = checkState(p.b);

      var unchecked = FLAGS.filter(function (f) {
        return stateA(f.key).state === "unchecked" && stateB(f.key).state === "unchecked";
      }).length;

      el.appendChild(H("p", { class: "ig-sub", text:
        (differing.length
          ? differing.length + " check" + (differing.length === 1 ? "" : "s") + " separate the two runs"
          : "No check separates the two runs") +
        (shared.length ? "; " + shared.length + " fired on both" : "") +
        (unchecked ? "; " + unchecked + " could not be measured on either" : "") + "." }));

      var table = H("table", { class: "ig-mx" });
      table.appendChild(H("tr", null, [
        H("th", { text: "check" }),
        H("th", { class: "c" }, [tag("A", "a")]),
        H("th", { class: "c" }, [tag("B", "b")]),
      ]));

      FLAGS.forEach(function (flag) {
        var sa = stateA(flag.key), sb = stateB(flag.key);
        var differs = differing.indexOf(flag.key) >= 0;
        var both = shared.indexOf(flag.key) >= 0;
        var label = H("td", { class: "k" }, [H("span", { text: flag.label })]);
        if (differs) label.appendChild(H("span", { class: "ig-mini",
          style: { display: "inline", marginLeft: "5px" }, text: "differs" }));
        if (both) label.appendChild(H("span", { class: "ig-mini",
          style: { display: "inline", marginLeft: "5px" }, text: "both" }));

        function cell(state, which) {
          var td = H("td", { class: "c" });
          var mark = markFor(state.state);
          if (state.state === "unchecked" && state.why) {
            mark.setAttribute("title", "unmeasurable — " + state.why);
          } else if (state.state === "raised") {
            mark.setAttribute("title", nameOf(p[which], which) + ": raised");
          } else {
            mark.setAttribute("title", nameOf(p[which], which) + ": checked, nothing found");
          }
          td.appendChild(mark);
          if (state.basis && inferred(state.basis)) {
            td.appendChild(H("span", { class: "dag", title: "basis: " + state.basis, text: "†" }));
          }
          return td;
        }

        table.appendChild(H("tr", {
          class: differs ? "differs" : (both ? "shared" : ""),
        }, [label, cell(sa, "a"), cell(sb, "b")]));
      });

      var wrap = H("div", { class: "scroll-x" }, [table]);
      el.appendChild(wrap);

      el.appendChild(legend([
        { node: markRaised(), label: "raised" },
        { node: markClear(), label: "checked, nothing found" },
        { node: markUnchecked(), label: "unmeasurable — not the same as clean" },
        { node: H("span", { class: "dag", text: "†" }), label: "inferred basis, not declared" },
      ]));

      el.appendChild(H("p", { class: "caveat", text:
        "There is deliberately no single process score: weighting a loop against " +
        "a blind write needs a judgement about the domain that this tool does not " +
        "have. The flags are reported so you can apply your own." }));
    },
  });

  // ===================================================== 3. loops & repeats

  function toolSteps(report, which) {
    var box = report && report[which];
    var steps = box && Array.isArray(box.steps) ? box.steps : [];
    return steps.filter(function (s) {
      return s && (s.type === "tool_call" || s.type === "search");
    });
  }

  /* The repeat picture: one cell per tool step in trace order, the longest
   * back-to-back repeated block drawn as recurring bands, and an arc from
   * each repeat back to the step it repeats. Positions are tool-step
   * positions because that is what `loops()` counts its period in; the
   * labels underneath are the real step indices, so the two readings line up. */
  function repeatStrip(host, width, ctx, report, which, d) {
    var ts = toolSteps(report, which);
    var n = ts.length;
    if (!n) {
      host.appendChild(H("p", { class: "ig-mini", text: "No tool calls in this run — nothing to repeat." }));
      return;
    }
    var pos = {};
    ts.forEach(function (s, i) { pos[s.index] = i; });

    var repeatsData = (d && d.repeats) || {};
    var arcs = [];
    (repeatsData.cycle_steps || []).forEach(function (c) {
      arcs.push({ from: c.first_seen, to: c.index, kind: "cycle" });
    });
    (repeatsData.repeated_steps || []).forEach(function (r) {
      arcs.push({ from: r.first_seen, to: r.index, kind: "repeat" });
    });
    (repeatsData.no_information_detail || []).forEach(function (r) {
      arcs.push({ from: r.same_as, to: r.index, kind: "noinfo" });
    });
    arcs = arcs.filter(function (arc) {
      return pos[arc.from] !== undefined && pos[arc.to] !== undefined;
    });

    var block = ((d && d.loops) || {}).longest_repeated_block || {};
    var hasBlock = num(block.period) > 0 && num(block.repeats) >= 2 &&
                   pos[block.starts_at] !== undefined;

    var cell = Math.max(9, Math.min(42, (width - 4) / n));
    var stripW = Math.max(60, cell * n + 2);
    var arcH = arcs.length ? 24 : 4;
    var stripY = arcH;
    var stripH = 14;
    var bandY = stripY + stripH + 3;
    var labelY = bandY + 13;
    var height = labelY + (hasBlock ? 20 : 4);

    var canvas = S("svg", {
      class: "ig-svg", width: Math.min(width, stripW + 2), height: height,
      viewBox: "0 0 " + (stripW + 2) + " " + height,
      preserveAspectRatio: "xMinYMid meet", role: "img",
      "aria-label": "Tool steps in order, with repeated calls arced back to the step they repeat.",
    });

    function cx(i) { return 1 + i * cell + cell / 2; }

    /* Each occurrence of the repeated block gets its own underline segment, so
     * "period 1 × 3" is legible as three beats rather than one long bar — and
     * an underline rather than a wash behind the cells, which would fight the
     * error colouring the cells already carry. */
    if (hasBlock) {
      var start = pos[block.starts_at];
      for (var k = 0; k < block.repeats; k++) {
        var x0 = 1 + (start + k * block.period) * cell;
        canvas.appendChild(S("rect", {
          x: x0 + 1, y: bandY, width: Math.max(3, block.period * cell - 3), height: 3.5, rx: 1.75,
          fill: C.warn, "fill-opacity": k % 2 ? 0.55 : 0.9,
        }, [S("title", { text: "occurrence " + (k + 1) + " of " + block.repeats })]));
      }
      var bx0 = 1 + start * cell;
      var bx1 = 1 + (start + block.period * block.repeats) * cell;
      canvas.appendChild(S("path", {
        d: "M" + bx0.toFixed(1) + "," + (labelY + 6) + " v4 H" + bx1.toFixed(1) + " v-4",
        fill: "none", stroke: C.warn, "stroke-width": 1, "stroke-opacity": 0.8,
      }));
      canvas.appendChild(S("text", {
        x: ((bx0 + bx1) / 2).toFixed(1), y: labelY + 19, "text-anchor": "middle",
        "font-size": 11.5, fill: C.warn,
        text: "period " + block.period + " × " + block.repeats,
      }));
    }

    // One cell per tool step; errored steps read as errored.
    ts.forEach(function (step, i) {
      var errored = step.error === true;
      canvas.appendChild(S("rect", {
        x: 1 + i * cell + 1.5, y: stripY, width: Math.max(4, cell - 3), height: stripH, rx: 2,
        fill: errored ? C.bad : C.axis, "fill-opacity": errored ? 0.45 : 0.28,
        stroke: errored ? C.bad : C.axis, "stroke-width": 1, "stroke-opacity": 0.8,
      }, [S("title", { text: "step " + step.index + " · " + (step.name || step.type) +
                             (errored ? " · error" : "") })]));
      if (cell >= 13 || i % 2 === 0) {
        canvas.appendChild(S("text", {
          x: cx(i).toFixed(1), y: labelY, "text-anchor": "middle",
          "font-size": 11, fill: C.muted, text: String(step.index),
        }));
      }
    });

    var ARC = { cycle: C.bad, repeat: C.warn, noinfo: C.muted };
    arcs.forEach(function (arc, i) {
      var x1 = cx(pos[arc.from]), x2 = cx(pos[arc.to]);
      var lift = Math.min(arcH - 3, 8 + Math.abs(x2 - x1) * 0.25 + (i % 2) * 3);
      canvas.appendChild(S("path", {
        d: "M" + x1.toFixed(1) + "," + (stripY - 1) + " C" + x1.toFixed(1) + "," +
           (stripY - 1 - lift) + " " + x2.toFixed(1) + "," + (stripY - 1 - lift) + " " +
           x2.toFixed(1) + "," + (stripY - 1),
        fill: "none", stroke: ARC[arc.kind] || C.muted, "stroke-width": 1.2,
        "stroke-opacity": arc.kind === "noinfo" ? 0.7 : 0.9,
        "stroke-dasharray": arc.kind === "noinfo" ? "2 2" : null,
      }));
      canvas.appendChild(S("circle", {
        cx: x2.toFixed(1), cy: stripY - 1.5, r: 1.9,
        fill: ARC[arc.kind] || C.muted, "fill-opacity": 0.9,
      }));
    });

    host.appendChild(H("p", { class: "ig-mini", text:
      "Tool calls in trace order; the number under each is its step index." }));
    host.appendChild(canvas);
    if (arcs.length || hasBlock) {
      host.appendChild(legend([
        arcs.some(function (a) { return a.kind === "cycle"; })
          ? { fill: C.bad, label: "same call, same result" } : null,
        arcs.some(function (a) { return a.kind === "repeat"; })
          ? { fill: C.warn, label: "same call again" } : null,
        arcs.some(function (a) { return a.kind === "noinfo"; })
          ? { fill: C.muted, label: "nothing new returned" } : null,
        hasBlock ? { fill: C.warn, label: "repeated block" } : null,
      ]));
    }
  }

  AgentDiff.block({
    id: "loops-repeats",
    title: "Loops & repeats",
    question: "Did it get stuck?",
    group: "integrity",
    size: "normal",

    relevance: function (ctx) {
      var p = processOf(ctx);
      if (!p) return 0;
      var worst = 0;
      sides(p).forEach(function (s) {
        var r = s.d.repeats || {}, l = s.d.loops || {};
        if (l.looping) worst = Math.max(worst, 0.9);
        if ((r.cycles || 0) > 0) worst = Math.max(worst, 0.88);
        if ((r.repeated_calls || 0) > 0 || (r.no_information_steps || 0) > 0) {
          worst = Math.max(worst, 0.75);
        }
      });
      return worst || 0.3;
    },

    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var p = processOf(ctx);
      if (!p) {
        ctx.empty(el, "This report predates process integrity (v22) — repeats and " +
                      "loops were not computed for it.");
        return;
      }
      var report = ctx.report;

      var anything = sides(p).some(function (s) {
        var r = s.d.repeats || {}, l = s.d.loops || {};
        return (r.cycles || 0) || (r.repeated_calls || 0) || (r.no_information_steps || 0) ||
               l.looping || (l.max_call_multiplicity || 0) > 1;
      });
      el.appendChild(H("p", { class: "ig-sub", text: anything
        ? "Three different shapes of stuck: the same call again, the same call " +
          "with the same result, and a step that returned nothing new."
        : "Neither run repeats a call, cycles, or takes a step that returns " +
          "nothing new. Every tool call in both traces is distinct." }));

      sides(p).forEach(function (s) {
        var r = s.d.repeats || {};
        var l = s.d.loops || {};
        var block = l.longest_repeated_block || {};
        var stuck = (r.cycles || 0) + (r.repeated_calls || 0) + (r.no_information_steps || 0);
        var panel = H("div", { class: "ig-panel s" + s.k });
        panel.appendChild(H("div", { class: "ig-head" }, [
          H("span", { class: "ig-name", text: nameOf(s.d, s.k) }),
          tag(s.k.toUpperCase(), s.k),
          l.looping ? tag("looping", "bad")
            : (stuck ? tag(plural(stuck, "repeat") + ", no loop block", "warn")
                     : tag("nothing repeated", "good")),
        ]));

        var stats = H("dl", { class: "kv" }, [
          H("dt", { text: "Repeated calls" }),
          H("dd", { text: F.int(r.repeated_calls || 0) }),
          H("dt", { text: "Cycles (call + result)" }),
          H("dd", { text: F.int(r.cycles || 0) }),
          H("dt", { text: "No-information steps" }),
          H("dd", { text: F.int(r.no_information_steps || 0) }),
          H("dt", { text: "Longest repeated block" }),
          H("dd", { text: num(block.period) && num(block.repeats) >= 2
            ? "period " + block.period + ", ×" + block.repeats + " from step " + block.starts_at
            : "none" }),
          H("dt", { text: "Max call multiplicity" }),
          H("dd", { text: F.int(l.max_call_multiplicity || 0) + " ×" }),
        ]);
        panel.appendChild(stats);

        sized(panel, function (host, width) {
          repeatStrip(host, width, ctx, report, s.k, s.d);
        });

        var detail = [];
        (r.cycle_steps || []).forEach(function (c) {
          detail.push(stepRow(c.index, c.name, "same call and same result as step " +
                              c.first_seen + " (period " + c.period + ")", "bad"));
        });
        (r.repeated_steps || []).forEach(function (x) {
          detail.push(stepRow(x.index, x.name, "same call as step " + x.first_seen, "warn"));
        });
        (r.no_information_detail || []).forEach(function (x) {
          detail.push(stepRow(x.index, x.name, "observation identical to step " + x.same_as));
        });
        if (detail.length) panel.appendChild(rows(detail, 14));

        el.appendChild(panel);
      });

      el.appendChild(H("p", { class: "caveat", text:
        "A retry after an error is deliberately not counted as a repeat: retrying " +
        "a failed call is correct behaviour, and conflating it with looping would " +
        "penalise recovery. A repeat is the same call made twice with no error " +
        "between them; a cycle is the same call and the same observation, which " +
        "is the shape of a loop rather than a retry." }));
    },
  });

  // ==================================================== 4. recovery & errors

  var OUTCOMES = [
    { key: "recovered", label: "recovered", tone: "good" },
    { key: "retried, still failing", label: "retried, still failing", tone: "warn" },
    { key: "repeated the failing call", label: "repeated the failing call", tone: "bad" },
    { key: "abandoned", label: "abandoned", tone: "bad" },
  ];

  function outcomeFill(tone) {
    if (tone === "good") return "color-mix(in srgb, var(--good) 72%, transparent)";
    if (tone === "warn") return "color-mix(in srgb, var(--warn) 72%, transparent)";
    return "color-mix(in srgb, var(--bad) 72%, transparent)";
  }

  AgentDiff.block({
    id: "recovery-errors",
    title: "Errors & recovery",
    question: "What happened after something went wrong?",
    group: "integrity",
    size: "normal",

    relevance: function (ctx) {
      var p = processOf(ctx);
      if (!p) return 0;
      var worst = 0;
      sides(p).forEach(function (s) {
        var rec = s.d.recovery || {};
        var errors = rec.errors || 0;
        if (!errors) return;
        var unrecovered = errors - (rec.recovered || 0);
        worst = Math.max(worst, unrecovered > 0 ? 0.9 : 0.7);
      });
      return worst || 0.3;
    },

    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var p = processOf(ctx);
      if (!p) {
        ctx.empty(el, "This report predates process integrity (v22) — error recovery " +
                      "was not computed for it.");
        return;
      }

      var total = sides(p).reduce(function (sum, s) {
        return sum + ((s.d.recovery || {}).errors || 0);
      }, 0);
      el.appendChild(H("p", { class: "ig-sub", text: total
        ? "An error is only a failure if it is not recovered from. Each error is " +
          "followed by what the next tool step actually did."
        : "No error observation in either run — so there is nothing here to " +
          "recover from. Read that together with the basis below." }));

      sides(p).forEach(function (s) {
        var rec = s.d.recovery || {};
        var errors = rec.errors || 0;
        var steps = Array.isArray(rec.error_steps) ? rec.error_steps : [];
        var panel = H("div", { class: "ig-panel s" + s.k });

        var unrecovered = errors - (rec.recovered || 0);
        panel.appendChild(H("div", { class: "ig-head" }, [
          H("span", { class: "ig-name", text: nameOf(s.d, s.k) }),
          tag(s.k.toUpperCase(), s.k),
          errors
            ? tag(plural(errors, "error"), unrecovered > 0 ? "bad" : "warn")
            // Green only when the log declares its errors; on the inferred
            // basis "none" means "no marker matched", which is weaker.
            : tag("no error observations", inferred(rec.basis) ? "" : "good"),
          rec.abandoned_after_error ? tag("abandoned after error", "bad") : null,
        ]));

        // The basis is the first thing said about the number, not a footnote:
        // "inferred from observation text" means an observation that merely
        // discusses an error reads the same as one that is an error.
        panel.appendChild(H("div", { class: "ig-tags" }, [basisPill("basis", rec.basis)]));

        if (errors) {
          var tally = {};
          steps.forEach(function (step) {
            tally[step.outcome] = (tally[step.outcome] || 0) + 1;
          });
          panel.appendChild(bar(OUTCOMES.map(function (o) {
            return { value: tally[o.key] || 0, label: o.label, fill: outcomeFill(o.tone) };
          })));
          panel.appendChild(legend(OUTCOMES.filter(function (o) { return tally[o.key]; })
            .map(function (o) {
              return { fill: outcomeFill(o.tone), label: tally[o.key] + " " + o.label };
            })));

          panel.appendChild(H("dl", { class: "kv", style: { marginTop: "7px" } }, [
            H("dt", { text: "Recovery rate" }),
            H("dd", { text: rec.recovery_rate === null || rec.recovery_rate === undefined
              ? "—"
              : F.pct(rec.recovery_rate) + "  (" + F.int(rec.recovered || 0) + " of " +
                F.int(errors) + ")" }),
            H("dt", { text: "Adaptation attempts" }),
            H("dd", { text: F.int(rec.recovery_attempts || 0) + " of " + F.int(errors) +
                            " changed the call" }),
            H("dt", { text: "Abandoned after error" }),
            H("dd", { text: F.int(rec.abandoned_after_error || 0) }),
          ]));

          panel.appendChild(rows(steps.map(function (step) {
            var tone = step.outcome === "recovered" ? "good"
              : (step.outcome === "retried, still failing" ? "warn" : "bad");
            var row = stepRow(step.index, step.name, step.outcome, tone);
            if (inferred(step.basis)) {
              row.appendChild(H("span", { class: "ig-mini", style: { display: "inline" },
                                          title: "read from the observation text",
                                          text: "inferred" }));
            }
            return row;
          }), 14));
        } else {
          panel.appendChild(H("p", { class: "ig-mini", text: inferred(rec.basis)
            ? "No observation in this trace matched an error marker. The trace does " +
              "not declare step errors, so this is the absence of a heuristic hit, " +
              "not a declared clean run."
            : "The trace declares an error field on its steps and none of them is set." }));
        }
        el.appendChild(panel);
      });

      el.appendChild(H("p", { class: "caveat", text:
        "Outcomes are read off the following tool step: the call either changed " +
        "(an adaptation attempt) or was repeated verbatim, and either succeeded or " +
        "did not. An error with no following tool step is “abandoned” — giving up " +
        "after an error is a different behaviour from failing to fix it, so the two " +
        "are never pooled." }));
    },
  });

  // ========================================================= 5. side effects

  AgentDiff.block({
    id: "side-effects",
    title: "Side effects",
    question: "What did it change, and had it looked first?",
    group: "integrity",
    size: "normal",

    relevance: function (ctx) {
      var p = processOf(ctx);
      if (!p) return 0;
      var worst = 0;
      sides(p).forEach(function (s) {
        var se = s.d.side_effects || {};
        if ((se.writes_before_any_read || 0) > 0) worst = Math.max(worst, 0.93);
        else if ((se.writes || 0) > 0) worst = Math.max(worst, 0.7);
        if ((se.unclassified || 0) > 0) worst = Math.max(worst, 0.6);
      });
      return worst || 0.35;
    },

    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var p = processOf(ctx);
      if (!p) {
        ctx.empty(el, "This report predates process integrity (v22) — no write " +
                      "ledger was recorded for it.");
        return;
      }

      var blind = sides(p).reduce(function (sum, s) {
        return sum + ((s.d.side_effects || {}).writes_before_any_read || 0);
      }, 0);
      var writes = sides(p).reduce(function (sum, s) {
        return sum + ((s.d.side_effects || {}).writes || 0);
      }, 0);

      el.appendChild(H("p", { class: "ig-lead" }, [
        blind
          ? H("span", null, [
              H("b", { text: blind + " blind write" + (blind === 1 ? "" : "s") }),
              H("span", { text: " — a change made before any read had succeeded. A write " +
                                "is the step you cannot re-run to check." }),
            ])
          : H("span", { text: writes
              ? "Every write in both runs came after a read that worked."
              : "Neither run changed anything outside itself." }),
      ]));

      sides(p).forEach(function (s) {
        var se = s.d.side_effects || {};
        var writeSteps = Array.isArray(se.write_steps) ? se.write_steps : [];
        var blindSteps = Array.isArray(se.blind_write_steps) ? se.blind_write_steps : [];
        var blindIndex = {};
        blindSteps.forEach(function (x) { blindIndex[x.index] = true; });

        var panel = H("div", { class: "ig-panel s" + s.k });
        panel.appendChild(H("div", { class: "ig-head" }, [
          H("span", { class: "ig-name", text: nameOf(s.d, s.k) }),
          tag(s.k.toUpperCase(), s.k),
          // "No blind writes" is only a green statement when the effects were
          // declared; on an inferred basis it is the absence of a guess.
          blindSteps.length
            ? tag(plural(blindSteps.length, "blind write"), "bad")
            : ((se.writes || 0) || (se.reads || 0)
                ? tag("no blind writes", inferred(se.basis) ? "" : "good")
                : tag("no tool effects recorded", "")),
        ]));

        panel.appendChild(H("div", { class: "ig-tags" }, [
          basisPill("effects", se.basis),
          (se.unclassified || 0) > 0
            ? H("span", { class: "ig-basis inferred",
                          title: "Neither the step nor the tool table declared an effect and the " +
                                 "name matched no known stem; counted as reads.",
                          text: (se.unclassified || 0) + " unclassified" })
            : null,
        ]));

        panel.appendChild(bar([
          { value: (se.reads || 0), label: "reads",
            fill: "color-mix(in srgb, var(--ink-3) 45%, transparent)" },
          { value: (se.writes || 0) - blindSteps.length, label: "writes after a read",
            fill: "color-mix(in srgb, var(--warn) 70%, transparent)" },
          { value: blindSteps.length, label: "blind writes",
            fill: "color-mix(in srgb, var(--bad) 85%, transparent)" },
        ]));
        panel.appendChild(legend([
          { fill: "color-mix(in srgb, var(--ink-3) 45%, transparent)",
            label: F.int(se.reads || 0) + " read" + ((se.reads || 0) === 1 ? "" : "s") },
          (se.writes || 0) - blindSteps.length > 0
            ? { fill: "color-mix(in srgb, var(--warn) 70%, transparent)",
                label: ((se.writes || 0) - blindSteps.length) + " write after a read" }
            : null,
          blindSteps.length
            ? { fill: "color-mix(in srgb, var(--bad) 85%, transparent)",
                label: blindSteps.length + " blind write" + (blindSteps.length === 1 ? "" : "s") }
            : null,
        ]));

        if (writeSteps.length) {
          panel.appendChild(rows(writeSteps.map(function (step) {
            var isBlind = blindIndex[step.index];
            var row = stepRow(step.index, step.name,
              isBlind ? "wrote before any read had succeeded" : "write",
              isBlind ? "bad" : "warn");
            row.appendChild(H("span", { class: "ig-basis" + (inferred(step.basis) ? " inferred" : ""),
                                        text: step.basis }));
            return row;
          }), 12));
        } else {
          panel.appendChild(H("p", { class: "ig-mini", text: "No write in this trace." }));
        }

        if ((se.unclassified || 0) > 0) {
          panel.appendChild(H("p", { class: "ig-mini", text:
            (se.unclassified || 0) + " tool step" + ((se.unclassified || 0) === 1 ? "" : "s") +
            " could not be classified from the log or the name and were counted as " +
            "reads — so the write count is a floor, not a ceiling." }));
        }
        el.appendChild(panel);
      });

      var anyInferred = sides(p).some(function (s) {
        return inferred((s.d.side_effects || {}).basis);
      });
      el.appendChild(H("p", { class: "caveat", text: anyInferred
        ? "At least one side's effects were inferred from tool names, not declared " +
          "by the log: a tool named run_report is guessed to be a write, and one " +
          "named check_out is guessed to be a read. Declaring an effect on each " +
          "step, or a tools list on the run, replaces the guess with a fact."
        : "Effects are declared by the log, so this ledger is a record rather than " +
          "a reading. Only a read that worked counts as having looked — three " +
          "failed lookups followed by a write is the blind-write case exactly." }));
    },
  });

  // ==================================================== 6. claims vs actions

  function sideHeading(p, which, extra) {
    var head = H("div", { class: "ig-head" }, [
      H("span", { class: "ig-name", text: nameOf(p[which], which) }),
      tag(which.toUpperCase(), which),
    ]);
    (extra || []).forEach(function (node) { if (node) head.appendChild(node); });
    return head;
  }

  function falseSuccessPanel(p, which) {
    var d = p[which] || {};
    var fs = d.false_success || {};
    var panel = H("div", { class: "ig-panel s" + which });
    var measurable = fs.measurable !== false;
    panel.appendChild(sideHeading(p, which, [
      measurable
        ? tag(fs.flagged ? "false success" : "checked, clear", fs.flagged ? "bad" : "good")
        : H("span", { class: "ig-badge", text: "? unmeasurable" }),
    ]));

    if (!measurable) {
      panel.appendChild(unmeasurable(
        "A false success cannot be told from an honest answer here",
        "The run declares no tool list, so whether it was ever offered a way to " +
        "write anything is unknown — and “said done, wrote nothing” only means " +
        "something when writing was possible. The trace records " +
        plural(fs.writes || 0, "write") +
        (fs.claim_phrases && fs.claim_phrases.length
          ? ", and the answer does contain completion language." : "."),
        "Not flagged is not the same as not lying. Declaring a tools list on the " +
        "run is what makes this measurable."));
    } else {
      panel.appendChild(H("p", { class: "ig-note", text: fs.verdict || "" }));
      panel.appendChild(H("dl", { class: "kv", style: { marginTop: "6px" } }, [
        H("dt", { text: "Write tools offered" }),
        H("dd", { text: fs.write_tools_offered ? "yes" : "no" }),
        H("dt", { text: "Writes made" }),
        H("dd", { text: F.int(fs.writes || 0) }),
      ]));
    }

    var phrases = Array.isArray(fs.claim_phrases) ? fs.claim_phrases : [];
    if (phrases.length) {
      var tags = H("div", { class: "ig-tags" });
      phrases.slice(0, 8).forEach(function (phrase) {
        tags.appendChild(chip("“" + phrase + "”", fs.flagged ? "bad" : null));
      });
      panel.appendChild(tags);
      panel.appendChild(H("p", { class: "ig-mini", text:
        "Completion language found in the answer. Confident closing language is " +
        "exactly what judges latch onto, which is why it is checked against the " +
        "write ledger rather than believed." }));
    }
    return panel;
  }

  function schemaPanel(p, which) {
    var d = p[which] || {};
    var sc = d.schema || {};
    var panel = H("div", { class: "ig-panel s" + which });
    var measurable = sc.measurable !== false;
    panel.appendChild(sideHeading(p, which, [
      measurable
        ? tag((sc.violations || 0) + " violation" + ((sc.violations || 0) === 1 ? "" : "s"),
              (sc.violations || 0) ? "bad" : "good")
        : H("span", { class: "ig-badge", text: "? unmeasurable" }),
    ]));

    if (!measurable) {
      panel.appendChild(unmeasurable(
        "Call validity was not checked",
        sc.note || "no tool parameter schemas declared; call validity unchecked",
        "An unchecked call is not a valid one — this is reported unmeasurable " +
        "rather than scored 100%."));
      return panel;
    }

    panel.appendChild(H("dl", { class: "kv" }, [
      H("dt", { text: "Calls checked" }), H("dd", { text: F.int(sc.checked || 0) }),
      H("dt", { text: "Violations" }), H("dd", { text: F.int(sc.violations || 0) }),
      H("dt", { text: "Validity" }),
      H("dd", { text: sc.validity === null || sc.validity === undefined
        ? "— (nothing to check)" : F.pct(sc.validity) }),
    ]));
    var detail = Array.isArray(sc.detail) ? sc.detail : [];
    if (detail.length) {
      panel.appendChild(rows(detail.map(function (v) {
        return stepRow(v.index, v.name,
          v.kind === "missing_required" ? "missing required argument “" + v.argument + "”"
          : v.kind === "unknown_argument" ? "unknown argument “" + v.argument + "”"
          : "argument “" + v.argument + "” is not " + (v.expected || "the declared type"),
          "bad");
      }), 12));
    }
    return panel;
  }

  function groundingPanel(p, which) {
    var d = p[which] || {};
    var gr = d.grounding || {};
    var panel = H("div", { class: "ig-panel s" + which });
    var checked = num(gr.arguments_checked) || 0;
    panel.appendChild(sideHeading(p, which, [
      gr.schema_checked
        ? tag((gr.undeclared_tool_calls || 0) + " undeclared call" +
              ((gr.undeclared_tool_calls || 0) === 1 ? "" : "s"),
              (gr.undeclared_tool_calls || 0) ? "bad" : "good")
        : H("span", { class: "ig-badge", text: "? tool list unmeasurable" }),
    ]));

    if (!gr.schema_checked) {
      panel.appendChild(unmeasurable(
        "No tool list, so no call could be checked against what was offered",
        "The run made " + plural(gr.calls || 0, "tool call") + ". Whether any of those " +
        "named a tool the run never had is unknown — schema grounding is reported " +
        "unmeasurable rather than scored 100%.",
        "A call nobody checked is not a call that was allowed."));
    } else {
      panel.appendChild(H("dl", { class: "kv" }, [
        H("dt", { text: "Tool calls" }), H("dd", { text: F.int(gr.calls || 0) }),
        H("dt", { text: "Schema grounding" }),
        H("dd", { text: gr.schema_grounding === null || gr.schema_grounding === undefined
          ? "—" : F.pct(gr.schema_grounding) }),
      ]));
      var undeclared = Array.isArray(gr.undeclared_tool_steps) ? gr.undeclared_tool_steps : [];
      if (undeclared.length) {
        panel.appendChild(rows(undeclared.map(function (x) {
          return stepRow(x.index, x.name, "called a tool that was never offered", "bad");
        }), 8));
      }
    }

    panel.appendChild(H("div", { class: "ig-hr" }));
    if (!checked) {
      panel.appendChild(unmeasurable(
        "Argument provenance was not checked",
        "No argument value in this run is long enough to trace (values under six " +
        "characters match an earlier observation by accident, in either direction), " +
        "so nothing was tested.",
        "Zero unsourced arguments here means zero arguments checked."));
    } else {
      var invented = Array.isArray(gr.invented_arguments) ? gr.invented_arguments : [];
      panel.appendChild(H("dl", { class: "kv" }, [
        H("dt", { text: "Arguments checked" }), H("dd", { text: F.int(checked) }),
        H("dt", { text: "Without a source" }),
        H("dd", { text: F.int(gr.arguments_without_source || 0) }),
        H("dt", { text: "Provenance" }),
        H("dd", { text: gr.argument_provenance === null || gr.argument_provenance === undefined
          ? "—" : F.pct(gr.argument_provenance) }),
      ]));
      if (invented.length) {
        panel.appendChild(rows(invented.map(function (x) {
          return stepRow(x.index, x.name,
            x.argument + " = " + F.truncate(String(x.value), 40) + " — appears in no earlier " +
            "observation and not in the prompt", "bad");
        }), 10));
      }
    }
    return panel;
  }

  AgentDiff.block({
    id: "claims-vs-actions",
    title: "Claims vs actions",
    question: "Did it claim something it never did?",
    group: "integrity",
    size: "wide",

    relevance: function (ctx) {
      var p = processOf(ctx);
      if (!p) return 0;
      var score = 0;
      sides(p).forEach(function (s) {
        var fs = s.d.false_success || {}, sc = s.d.schema || {}, gr = s.d.grounding || {};
        if (fs.flagged) score = Math.max(score, 1);
        if ((sc.violations || 0) > 0) score = Math.max(score, 0.86);
        if ((gr.undeclared_tool_calls || 0) > 0) score = Math.max(score, 0.86);
        if ((gr.arguments_without_source || 0) > 0) score = Math.max(score, 0.8);
        // An unmeasurable check is worth saying out loud, not worth hiding.
        if (fs.measurable === false || sc.measurable === false || !gr.schema_checked) {
          score = Math.max(score, 0.62);
        }
      });
      return score || 0.42;
    },

    render: function (el, ctx) {
      bind(ctx);
      ensureStyle();
      var p = processOf(ctx);
      if (!p) {
        ctx.empty(el, "This report predates process integrity (v22) — claims were " +
                      "not checked against actions for it.");
        return;
      }

      var unmeasured = 0;
      sides(p).forEach(function (s) {
        var fs = s.d.false_success || {}, sc = s.d.schema || {}, gr = s.d.grounding || {};
        if (fs.measurable === false) unmeasured++;
        if (sc.measurable === false) unmeasured++;
        if (!gr.schema_checked) unmeasured++;
      });

      el.appendChild(H("p", { class: "ig-lead" }, [
        unmeasured
          ? H("span", null, [
              H("b", { text: unmeasured === 6
                ? "None of the three checks could run, on either side"
                : unmeasured + " of the six side-by-side checks could not be run" }),
              H("span", { text: " — the trace does not declare what the agent was " +
                                "offered, and an unchecked call is not a valid one. " +
                                "The panels below say which, rather than reporting a clean pass." }),
            ])
          : H("span", { text: "All three checks ran on both sides: what the answer " +
                              "claims, whether the calls typecheck, and whether the " +
                              "arguments came from anywhere." }),
      ]));

      [
        { title: "Claimed completion without writing",
          why: "The failure mode judges are worst at: across five judges and five " +
               "prompting strategies, none exceeded AUROC 0.65 at spotting it, because " +
               "they read confident closing language as evidence. Here it is a " +
               "contradiction between two things already in the log.",
          make: falseSuccessPanel },
        { title: "Arguments that do not typecheck",
          why: "A shallow walk against the declared parameter schemas: required keys, " +
               "unknown keys and primitive types. Deeper validation would be confident " +
               "about text it only half understands.",
          make: schemaPanel },
        { title: "Grounding: real tools, sourced arguments",
          why: "Two containment tests over the trace — did the call name a tool the run " +
               "was offered, and does each argument value appear in an earlier " +
               "observation or in the prompt?",
          make: groundingPanel },
      ].forEach(function (section, i) {
        if (i) el.appendChild(H("div", { class: "ig-hr" }));
        el.appendChild(H("div", { class: "ig-head" }, [
          H("span", { class: "ig-name", text: section.title }),
        ]));
        el.appendChild(H("p", { class: "ig-mini", text: section.why }));
        var cols = H("div", { class: "ig-cols", style: { marginTop: "6px" } });
        ["a", "b"].forEach(function (which) {
          cols.appendChild(H("div", { class: "ig-col" }, [section.make(p, which)]));
        });
        el.appendChild(cols);
      });

      el.appendChild(H("p", { class: "caveat", text:
        "Every unmeasurable state above is a missing declaration in the trace, not a " +
        "result. Rendering them green would be the same mistake outcome-only grading " +
        "makes: treating “nobody looked” as “nothing wrong”." }));
    },
  });
})(typeof window !== "undefined" ? window : this);
