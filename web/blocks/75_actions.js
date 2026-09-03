/* AgentDiff blocks — the actions board.
 *
 * Triage produces the most actionable artifact in the whole report: a
 * ranked, deduplicated, evidence-backed list of what to fix, each entry
 * carrying its own verification contract. This block is that list as a
 * working surface — each row expands into the full case (evidence,
 * rank_basis verbatim, a copyable fix hint, the verification checklist and
 * the copyable progress command), and the findings triage deliberately
 * refused to rank sit in a footer with their reasons, because a refusal
 * with a reason is information too.
 *
 * Everything rendered here is the engine's own text. Nothing is invented,
 * no caveat is dropped, and a figure the engine marks not-estimable is
 * shown as exactly that — never as zero.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style

  var STYLE_ID = "agentdiff-actions-css";
  var styled = false;

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".ax-lede{font-size:12px;color:var(--ink-2);margin:0 0 10px;line-height:1.55}",
        ".ax-gate{display:flex;gap:8px;align-items:flex-start;border:1px solid ",
        "color-mix(in srgb, var(--warn) 45%, transparent);border-left:3px solid var(--warn);",
        "background:color-mix(in srgb, var(--warn) 7%, var(--surface));border-radius:8px;",
        "padding:8px 10px;margin:0 0 10px;font-size:12px;line-height:1.5;color:var(--ink)}",
        ".ax-gate .k{font-weight:700;color:var(--warn);white-space:nowrap}",
        ".ax-gate .sub{color:var(--ink-2);font-size:11px;margin-top:3px}",
        ".ax-list{margin:0;padding:0;list-style:none;min-width:0}",
        ".ax-more{margin-top:10px;border:1px solid var(--rule-2);background:var(--surface);",
        "color:var(--ink-2);border-radius:7px;padding:4px 12px;font:600 12px/1.4 inherit;cursor:pointer}",
        ".ax-more:hover{border-color:var(--accent);color:var(--accent)}",
        ".ax-row{border:1px solid var(--rule);border-radius:9px;margin:0 0 8px;",
        "background:var(--surface);min-width:0;overflow:hidden}",
        ".ax-row.sneaky{border-left:3px solid var(--warn);",
        "background:color-mix(in srgb, var(--warn) 4%, var(--surface))}",
        ".ax-row.fails{border-left:3px solid var(--bad)}",
        ".ax-head{display:flex;align-items:flex-start;gap:10px;width:100%;text-align:left;",
        "background:none;border:0;padding:9px 10px;cursor:pointer;color:var(--ink);",
        "font-family:var(--sans);min-width:0}",
        ".ax-head:hover{background:color-mix(in srgb, var(--accent) 5%, transparent)}",
        ".ax-rank{flex:0 0 auto;width:24px;height:24px;border-radius:999px;display:flex;",
        "align-items:center;justify-content:center;background:var(--surface-2);",
        "border:1px solid var(--rule-2);font-size:11.5px;font-weight:700;color:var(--ink-2);",
        "font-variant-numeric:tabular-nums}",
        ".ax-main{flex:1 1 auto;min-width:0}",
        ".ax-act{font-size:13px;font-weight:600;line-height:1.45;overflow-wrap:anywhere}",
        ".ax-meta{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:5px;",
        "font-size:11px;color:var(--ink-3)}",
        ".ax-impact{font-size:11.5px;color:var(--ink-2);margin-top:4px;line-height:1.45;",
        "overflow-wrap:anywhere}",
        ".ax-impact .none{color:var(--ink-3);font-style:italic}",
        ".ax-caret{flex:0 0 auto;color:var(--ink-3);font-size:11px;padding-top:5px;",
        "transition:transform .15s ease}",
        ".ax-row.open .ax-caret{transform:rotate(90deg)}",
        ".ax-body{display:none;border-top:1px solid var(--rule);padding:10px 12px 12px;",
        "min-width:0}",
        ".ax-row.open .ax-body{display:block}",
        ".ax-sec{margin:0 0 12px;min-width:0}",
        ".ax-sec:last-child{margin-bottom:0}",
        ".ax-k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;",
        "color:var(--ink-3);font-weight:700;margin:0 0 5px}",
        ".ax-p{font-size:12.3px;line-height:1.55;color:var(--ink);margin:0 0 5px}",
        ".ax-ul{margin:0;padding-left:16px;font-size:12.3px;line-height:1.55;color:var(--ink)}",
        ".ax-ul li{margin:0 0 3px}",
        ".ax-basis{font-family:var(--mono);font-size:11px;line-height:1.6;color:var(--ink-2);",
        "margin:0;padding-left:14px}",
        ".ax-basis li{margin:0 0 2px;overflow-wrap:anywhere}",
        ".ax-fp{font-family:var(--mono);font-size:11px;background:var(--surface-2);",
        "border:1px solid var(--rule);border-radius:5px;padding:1px 5px;",
        "overflow-wrap:anywhere;word-break:break-all;display:inline-block;margin:1px 0}",
        ".ax-caveat{font-size:11px;color:var(--ink-3);line-height:1.5;margin:5px 0 0}",
        ".ax-prehead{display:flex;align-items:center;justify-content:space-between;gap:8px;",
        "margin:0 0 4px}",
        ".ax-prehead span{font-size:11px;font-weight:700;color:var(--ink-2)}",
        ".ax-pre{margin:0;background:var(--surface-2);border:1px solid var(--rule);",
        "border-radius:7px;padding:8px 10px;font-size:11px;line-height:1.55;",
        "white-space:pre-wrap;overflow-wrap:anywhere;overflow-x:auto;max-width:100%}",
        ".ax-checks{margin:0;padding:0;list-style:none}",
        ".ax-check{position:relative;padding:0 0 10px 24px;min-width:0}",
        ".ax-check::before{content:\"\";position:absolute;left:2px;top:3px;width:11px;",
        "height:11px;border:1.5px solid var(--ink-3);border-radius:3px}",
        ".ax-check:last-child{padding-bottom:0}",
        ".ax-check .what{font-size:12.3px;line-height:1.5;color:var(--ink)}",
        ".ax-check .how{font-size:11px;color:var(--ink-3);margin-top:2px;line-height:1.5}",
        ".ax-luck{border:1px solid color-mix(in srgb, var(--warn) 45%, transparent);",
        "border-left:3px solid var(--warn);",
        "background:color-mix(in srgb, var(--warn) 8%, var(--surface));border-radius:7px;",
        "padding:7px 9px;margin:6px 0 0;font-size:12.3px;line-height:1.55;color:var(--ink);",
        "font-weight:600}",
        ".ax-luck .lbl{display:block;font-size:11px;font-weight:700;letter-spacing:.08em;",
        "text-transform:uppercase;color:var(--warn);margin-bottom:2px}",
        ".ax-task{display:inline-block;border:1px solid var(--rule-2);border-radius:999px;",
        "padding:0 8px;font-size:11px;line-height:1.8;font-family:var(--mono);",
        "color:var(--ink-2);margin:1px 2px 1px 0;overflow-wrap:anywhere}",
        ".ax-task.passed{border-color:color-mix(in srgb, var(--warn) 55%, transparent);",
        "color:var(--warn)}",
        ".ax-foot{margin-top:10px;border-top:1px dashed var(--rule);padding-top:8px}",
        ".ax-foot-toggle{background:none;border:0;padding:2px 0;cursor:pointer;",
        "font-family:var(--sans);font-size:11.5px;color:var(--ink-2);display:flex;",
        "align-items:center;gap:6px;text-align:left}",
        ".ax-foot-toggle:hover{color:var(--accent)}",
        ".ax-refusal{border-top:1px solid var(--rule);padding:7px 0;font-size:12px;",
        "line-height:1.5;min-width:0}",
        ".ax-refusal:first-of-type{border-top:0}",
        ".ax-refusal .why{font-weight:700;color:var(--ink-2)}",
        ".ax-refusal .det{font-size:11px;color:var(--ink-3);margin-top:2px;line-height:1.5;",
        "overflow-wrap:anywhere}",
        "@media (max-width:520px){.ax-head{gap:7px;padding:8px}.ax-rank{width:20px;",
        "height:20px;font-size:11px}}",
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is a nicety; the list still reads */ }
  }

  // ---------------------------------------------------------------- helpers

  function isNum(value) { return typeof value === "number" && isFinite(value); }

  function term(ctx, text, key) {
    if (typeof ctx.explain === "function") {
      return ctx.explain(ctx.h("span", { text: text }), { term: key });
    }
    return ctx.h("span", { text: text, "data-explain": key });
  }

  //: an element carrying a one-off explanation, wired to the shared tooltip.
  function explained(ctx, node, def) {
    if (typeof ctx.explain === "function") return ctx.explain(node, def);
    return node;
  }

  /* Copy without a library and without assuming the async clipboard API is
   * reachable — a file:// page in a locked-down browser gets neither the
   * API nor a permission prompt; the honest fallback selects via a hidden
   * textarea and, failing that, says to press Ctrl+C rather than failing
   * silently. */
  function copyToClipboard(text, button) {
    var restore = button.textContent;
    function finish(label) {
      button.textContent = label;
      setTimeout(function () { button.textContent = restore; }, 1800);
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

  function copyable(ctx, label, text) {
    var button = ctx.h("button", { class: "btn tiny", text: "Copy", type: "button" });
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      ctx.signal("inspect");
      copyToClipboard(text, button);
    });
    return [
      ctx.h("div", { class: "ax-prehead" }, [ctx.h("span", { text: label }), button]),
      ctx.h("pre", { class: "ax-pre", text: text }),
    ];
  }

  // -------------------------------------------------------------- vocabulary

  /* Severity classes the scheme names today, with how each is worn. Anything
   * the engine adds later (new sources, new classes) falls through to a
   * plain tag spelled from the class name — rendered, never dropped. */
  var SEVERITY = {
    failure:           { label: "failure",         tag: "bad",  row: "fails" },
    passing_pathology: { label: "passed anyway",   tag: "warn", row: "sneaky" },
    failing_pathology: { label: "failing process", tag: "",     row: "" },
    cost:              { label: "cost",            tag: "",     row: "" },
    signal:            { label: "signal",          tag: "",     row: "" },
    efficiency:        { label: "efficiency",      tag: "",     row: "" },
  };

  function severityInfo(cls) {
    if (SEVERITY[cls]) return SEVERITY[cls];
    return { label: String(cls || "unclassed").replace(/_/g, " "), tag: "", row: "" };
  }

  //: process-flag keys that have a glossary entry get the tooltip; the rest
  //: are shown as-is in mono, because the key itself is the fingerprintable
  //: fact.
  var FLAG_TERMS = {
    blind_write: "blind-write",
    false_success: "false-success",
    no_information_steps: "no-information-step",
  };

  function flagNode(ctx, flag) {
    var node = ctx.h("span", { class: "ax-fp", text: flag });
    if (FLAG_TERMS[flag]) return term(ctx, flag, FLAG_TERMS[flag]);
    return node;
  }

  //: "4 of 8 tasks", "6 of 16 runs" — but a multi-word unit the engine may
  //: emit ("failure with telemetry") is quoted verbatim, not pluralised.
  function sampleText(k, n, unit) {
    unit = String(unit || "task");
    var plural = unit.indexOf(" ") < 0 && n !== 1 ? unit + "s" : unit;
    return k + " of " + n + " " + plural;
  }

  //: the backticked command inside verification.how, when the engine wrote
  //: one; the copy button copies exactly the engine's own command line.
  function progressCommand(how) {
    var match = /`([^`]+)`/.exec(String(how || ""));
    return match ? match[1] : null;
  }

  // ------------------------------------------------------------ open state

  /* Which rows are open, kept across the full re-render the core does on
   * every interaction elsewhere on the page. Keyed by rank + action text so
   * a changed list does not inherit a stale expansion. */
  var openRows = {};
  var refusalsOpen = false;

  function rowKey(action) {
    return String(action.rank) + "|" + String(action.action || "").slice(0, 80);
  }

  // ============================================================== the block

  AgentDiff.block({
    id: "actions",
    title: "Do this next",
    question: "The ranked fixes, with their evidence and how to verify each.",
    group: "outcome",
    size: "wide",

    relevance: function (ctx) {
      var triage = (ctx.aggregate || {}).triage;
      var actions = triage && Array.isArray(triage.actions) ? triage.actions : [];
      return actions.length ? 1 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var triage = (ctx.aggregate || {}).triage;
      var actions = triage && Array.isArray(triage.actions) ? triage.actions : [];
      if (!actions.length) {
        return ctx.empty(el, "Triage produced no ranked actions for this batch.");
      }

      el.appendChild(lede(ctx, triage, actions));
      var gate = gateBanner(ctx, triage.reliability_gate);
      if (gate) el.appendChild(gate);

      // in the story view: the actions that touch THIS task, three at a
      // time; the whole batch is one click away, and on the Batch view
      var inStory = ctx.lane === "story" || !!(el.closest && el.closest(".story-lane"));
      var mine = inStory && ctx.task
        ? actions.filter(function (a) { return actionTouchesTask(a, ctx.task); })
        : actions;
      if (inStory && !mine.length) mine = actions;
      var shown = !inStory ? actions : storyShowAll ? actions : mine.slice(0, 3);
      var list = ctx.h("ol", { class: "ax-list" });
      shown.forEach(function (action) {
        list.appendChild(actionRow(ctx, action));
      });
      el.appendChild(list);
      if (inStory && shown.length < actions.length) {
        el.appendChild(ctx.h("button", {
          class: "ax-more", type: "button",
          text: storyShowAll
            ? "Show the top " + Math.min(3, mine.length) + " for this task"
            : "Show all " + actions.length + " across the batch"
              + (mine.length > shown.length ? " (" + (mine.length - shown.length) + " more for this task)" : ""),
          onclick: function () { storyShowAll = !storyShowAll; el.innerHTML = ""; AgentDiff._rerender ? AgentDiff._rerender() : location.reload(); },
        }));
      }

      var footer = refusalsFooter(ctx, triage.not_actionable);
      if (footer) el.appendChild(footer);
    },
  });

  var storyShowAll = false;

  function actionTouchesTask(action, task) {
    try {
      var ev = action.evidence || {};
      var lists = [ev.tasks, ev.task_ids, action.tasks, action.evidence_tasks];
      for (var i = 0; i < lists.length; i++) {
        if (Array.isArray(lists[i]) && lists[i].indexOf(task) >= 0) return true;
      }
      return JSON.stringify(action).indexOf('"' + task + '"') >= 0;
    } catch (err) { return true; }
  }

  // ------------------------------------------------------------------ lede

  function lede(ctx, triage, actions) {
    var counts = triage.counts || {};
    var bits = [];
    var nActions = isNum(counts.actions) ? counts.actions : actions.length;
    var nTasks = isNum(triage.tasks) ? triage.tasks
               : Array.isArray(triage.tasks) ? triage.tasks.length : null;
    bits.push(String(nActions) + " action" + (nActions === 1 ? "" : "s") +
              (nTasks ? " across " + nTasks + " task" + (nTasks === 1 ? "" : "s") : ""));
    if (isNum(counts.merged) && counts.merged > 0) {
      bits.push(counts.merged + " duplicate finding" + (counts.merged === 1 ? "" : "s") +
                " merged");
    }
    var sources = counts.sources || {};
    var sourceBits = Object.keys(sources).sort().map(function (name) {
      return name + " " + sources[name];
    });
    if (sourceBits.length) bits.push("from " + sourceBits.join(" · "));

    var kids = [bits.join(", ") + ", "];
    var scheme = triage.scheme || {};
    var rankedWord = ctx.h("span", { text: "ranked by score" });
    if (scheme.formula) {
      var baseLines = [];
      var base = scheme.base || {};
      var reasons = scheme.base_reasons || {};
      Object.keys(base).forEach(function (cls) {
        baseLines.push(severityInfo(cls).label + " " + base[cls] +
                       (reasons[cls] ? " — " + reasons[cls] : ""));
      });
      rankedWord = explained(ctx, rankedWord, {
        label: "the ranking scheme",
        short: scheme.formula,
        long: baseLines.length ? "Base scores: " + baseLines.join(". ") + "." : "",
      });
    }
    kids.push(rankedWord);
    kids.push(". Expand a row for the evidence, the fix hint, and the checks " +
              "that will prove the fix worked. Rows marked ");
    kids.push(ctx.h("span", { class: "tag warn", text: "passed anyway" }));
    kids.push(" sit on runs the outcome oracle accepted — the sneaky ones " +
              "nothing outcome-only will ever surface.");
    return ctx.h("p", { class: "ax-lede" }, kids);
  }

  // ----------------------------------------------------------- gate banner

  function gateBanner(ctx, gate) {
    if (!gate || !gate.message) return null;
    var body = [
      ctx.h("div", { text: gate.message }),
    ];
    var head = [];
    if (gate.agent) head.push(String(gate.agent));
    if (gate.tier) head.push(gate.tier);
    if (isNum(gate.n_min)) head.push("n=" + gate.n_min);
    if (Array.isArray(gate.does_not_support) && gate.does_not_support.length) {
      body.push(ctx.h("div", {
        class: "sub",
        text: "This run count does not support: " + gate.does_not_support.join("; ") + ".",
      }));
    }
    return ctx.h("div", { class: "ax-gate" }, [
      ctx.h("span", { class: "k", text: "Reliability gate" + (head.length ? " · " + head.join(", ") : "") }),
      ctx.h("div", null, body),
    ]);
  }

  // ------------------------------------------------------------------ rows

  function actionRow(ctx, action) {
    var key = rowKey(action);
    var info = severityInfo(action.severity_class);
    var row = ctx.h("li", {
      class: "ax-row" + (info.row ? " " + info.row : "") + (openRows[key] ? " open" : ""),
    });

    var head = ctx.h("button", {
      class: "ax-head", type: "button",
      "aria-expanded": openRows[key] ? "true" : "false",
      title: openRows[key] ? "Collapse this action" : "Expand: evidence, fix hint, verification",
    });
    head.appendChild(ctx.h("span", { class: "ax-rank", text: String(action.rank) }));

    var main = ctx.h("div", { class: "ax-main" });
    main.appendChild(ctx.h("div", { class: "ax-act", text: action.action || "(unnamed action)" }));
    main.appendChild(metaLine(ctx, action, info));
    main.appendChild(impactLine(ctx, action.impact));
    head.appendChild(main);
    head.appendChild(ctx.h("span", { class: "ax-caret", text: "▸" }));

    var body = ctx.h("div", { class: "ax-body" }, buildCase(ctx, action));

    head.addEventListener("click", function () {
      openRows[key] = !openRows[key];
      row.classList.toggle("open", !!openRows[key]);
      head.setAttribute("aria-expanded", openRows[key] ? "true" : "false");
      head.setAttribute("title", openRows[key]
        ? "Collapse this action"
        : "Expand: evidence, fix hint, verification");
      ctx.signal("inspect");
    });

    row.appendChild(head);
    row.appendChild(body);
    return row;
  }

  function metaLine(ctx, action, info) {
    var meta = ctx.h("div", { class: "ax-meta" });
    meta.appendChild(ctx.h("span", {
      class: "tag" + (info.tag ? " " + info.tag : ""),
      text: info.label,
      title: action.severity_class === "passing_pathology"
        ? "A pathological process on a run that passed — the outcome hides it"
        : "severity class: " + String(action.severity_class || "unclassed"),
    }));
    (Array.isArray(action.agents) ? action.agents : []).forEach(function (agent) {
      meta.appendChild(ctx.h("span", { class: "tag", text: agent }));
    });

    var confidence = action.confidence || {};
    if (confidence.level) {
      var capped = confidence.capped_by ? String(confidence.capped_by) : null;
      var title = (Array.isArray(confidence.basis) ? confidence.basis.join("; ") : "") +
                  (capped ? (Array.isArray(confidence.basis) && confidence.basis.length ? " — " : "") +
                    "capped: " + capped : "");
      meta.appendChild(ctx.h("span", {
        class: "tag" + (confidence.level === "high" ? " good" : ""),
        text: confidence.level + " confidence" + (capped ? " ▾capped" : ""),
        title: title || undefined,
      }));
    }

    // The occurrence rate is the authoritative sample (its unit can differ
    // from tasks — runs, failures with telemetry); task_count is the fallback.
    var evidence = action.evidence || {};
    var rate = evidence.occurrence_rate;
    if (rate && isNum(rate.k) && isNum(rate.n)) {
      meta.appendChild(ctx.h("span", { text: sampleText(rate.k, rate.n, rate.unit) }));
    } else if (isNum(evidence.task_count) && isNum(evidence.of_tasks)) {
      meta.appendChild(ctx.h("span", {
        text: sampleText(evidence.task_count, evidence.of_tasks, "task"),
      }));
    }
    if (action.source) meta.appendChild(ctx.h("span", { text: action.source }));
    var effort = action.effort || {};
    if (effort.class) {
      meta.appendChild(ctx.h("span", {
        text: "effort: " + effort.class,
        title: (effort.detail || "") + (effort.heuristic && effort.note ? " — " + effort.note : ""),
      }));
    }
    return meta;
  }

  /* The impact one-liner. The engine's rule is honoured over the display's
   * appetite for numbers: estimable=false renders as "not estimable" with
   * the engine's reason, never as 0. */
  function impactLine(ctx, impact) {
    var line = ctx.h("div", { class: "ax-impact" });
    if (!impact) {
      line.appendChild(ctx.h("span", { class: "none", text: "impact: not stated in this report" }));
      return line;
    }
    if (impact.estimable === false) {
      var reasons = Array.isArray(impact.unestimable) ? impact.unestimable : [];
      line.appendChild(ctx.h("span", {
        class: "none",
        text: "impact not estimable — " +
              (reasons.length ? reasons[0] : (impact.summary || "the source reports no figure")),
        title: reasons.length > 1 ? reasons.join("\n") : undefined,
      }));
      return line;
    }
    line.appendChild(ctx.h("span", { text: impact.summary || "impact: see the expanded case" }));
    return line;
  }

  // ----------------------------------------------------------- the full case

  function buildCase(ctx, action) {
    var kids = [];
    var evidenceSec = evidenceSection(ctx, action);
    if (evidenceSec) kids.push(evidenceSec);
    var impactSec = impactSection(ctx, action.impact);
    if (impactSec) kids.push(impactSec);
    var whySec = whySection(ctx, action);
    if (whySec) kids.push(whySec);
    var fixSec = fixSection(ctx, action);
    if (fixSec) kids.push(fixSec);
    var verifySec = verificationSection(ctx, action.verification);
    if (verifySec) kids.push(verifySec);
    if (!kids.length) {
      kids.push(ctx.h("div", { class: "empty", text: "This action carries no expanded detail." }));
    }
    return kids;
  }

  // ------------------------------------------------------------- evidence

  function evidenceSection(ctx, action) {
    var evidence = action.evidence || {};
    var kids = [ctx.h("div", { class: "ax-k", text: "Evidence" })];
    var any = false;

    var tasks = Array.isArray(evidence.tasks) ? evidence.tasks : [];
    var passing = Array.isArray(action.on_passing_runs) ? action.on_passing_runs : [];
    if (tasks.length) {
      any = true;
      var taskLine = ctx.h("div", { class: "ax-p" });
      tasks.forEach(function (task) {
        var passed = passing.indexOf(task) >= 0;
        var chip = ctx.h("span", {
          class: "ax-task" + (passed ? " passed" : ""),
          text: task + (passed ? " · passed anyway" : ""),
        });
        taskLine.appendChild(passed
          ? explained(ctx, chip, { term: "passed-but-pathological" })
          : chip);
      });
      kids.push(taskLine);
    }

    var rate = evidence.occurrence_rate;
    if (rate && isNum(rate.k) && isNum(rate.n)) {
      any = true;
      var rateBits = [
        "Occurs on " + sampleText(rate.k, rate.n, rate.unit),
      ];
      if (isNum(rate.rate)) rateBits.push(" (" + ctx.fmt.pct(rate.rate, 0));
      var node = ctx.h("p", { class: "ax-p" }, rateBits);
      if (Array.isArray(rate.interval) && rate.interval.length === 2) {
        node.appendChild(document.createTextNode(", "));
        node.appendChild(term(ctx, rate.method || "Wilson 95%", "wilson-interval"));
        node.appendChild(document.createTextNode(
          " interval " + ctx.fmt.pct(rate.interval[0], 0) + "–" +
          ctx.fmt.pct(rate.interval[1], 0)));
      }
      if (isNum(rate.rate)) node.appendChild(document.createTextNode(")"));
      node.appendChild(document.createTextNode(
        isNum(evidence.failures_caused) && evidence.failures_caused > 0
          ? "; caused " + evidence.failures_caused + " failure" +
            (evidence.failures_caused === 1 ? "" : "s") + "."
          : "."));
      kids.push(node);
    }

    var details = Array.isArray(evidence.details) ? evidence.details : [];
    if (details.length) {
      any = true;
      kids.push(ctx.h("ul", { class: "ax-ul" }, details.map(function (line) {
        return ctx.h("li", { text: line });
      })));
    }

    var steps = Array.isArray(evidence.steps) ? evidence.steps : [];
    if (steps.length) {
      any = true;
      kids.push(ctx.h("ul", { class: "ax-basis" }, steps.map(function (step) {
        var where = [];
        if (step.task) where.push(step.task);
        where.push("A:" + (isNum(step.a_index) ? step.a_index : "—") +
                   " B:" + (isNum(step.b_index) ? step.b_index : "—"));
        return ctx.h("li", { text: where.join(" · ") + " — " + (step.what || "") });
      })));
    }

    var flags = Array.isArray(evidence.process_flags) ? evidence.process_flags : [];
    if (flags.length) {
      any = true;
      var flagLine = ctx.h("p", { class: "ax-p" }, ["Process flags raised: "]);
      flags.forEach(function (flag, index) {
        if (index) flagLine.appendChild(document.createTextNode(" "));
        flagLine.appendChild(flagNode(ctx, flag));
      });
      kids.push(flagLine);
    }

    var caveats = Array.isArray(evidence.caveats) ? evidence.caveats : [];
    caveats.forEach(function (caveat) {
      any = true;
      kids.push(ctx.h("p", { class: "ax-caveat", text: caveat }));
    });

    return any ? ctx.h("div", { class: "ax-sec" }, kids) : null;
  }

  // --------------------------------------------------------------- impact

  function impactSection(ctx, impact) {
    if (!impact) return null;
    var kids = [ctx.h("div", { class: "ax-k", text: "Impact if fixed" })];
    var rows = [];

    function push(label, valueText, basis) {
      rows.push(ctx.h("li", null, [
        ctx.h("span", { text: label + ": " + valueText }),
        basis ? ctx.h("div", { class: "ax-caveat", text: basis }) : null,
      ]));
    }

    var fa = impact.failures_avoided;
    if (fa && isNum(fa.value)) {
      push("failures avoided", "up to " + fa.value +
           (isNum(fa.of_tasks) ? " of " + fa.of_tasks + " tasks" : ""), fa.basis);
    }
    var tok = impact.tokens_saved;
    if (tok && isNum(tok.value)) push("tokens saved", ctx.fmt.int(tok.value) + " tok", tok.basis);
    var lat = impact.latency_saved_s;
    if (lat && isNum(lat.value)) push("latency saved", ctx.fmt.sec(lat.value), lat.basis);
    var usd = impact.cost_usd_saved;
    if (usd && isNum(usd.value)) push("cost saved", ctx.fmt.usd(usd.value), usd.basis);

    var reasons = Array.isArray(impact.unestimable) ? impact.unestimable : [];
    if (!rows.length) {
      // Nothing estimable: the reasons ARE the content, not an apology.
      kids.push(ctx.h("p", { class: "ax-p", text: "Not estimable from this data:" }));
      if (reasons.length) {
        kids.push(ctx.h("ul", { class: "ax-ul" }, reasons.map(function (reason) {
          return ctx.h("li", { text: reason });
        })));
      } else if (impact.summary) {
        kids.push(ctx.h("p", { class: "ax-caveat", text: impact.summary }));
      } else {
        return null;
      }
      return ctx.h("div", { class: "ax-sec" }, kids);
    }

    kids.push(ctx.h("ul", { class: "ax-ul" }, rows));
    // Partial estimates: the fields the engine could not price keep their
    // reasons alongside the ones it could.
    reasons.forEach(function (reason) {
      kids.push(ctx.h("p", { class: "ax-caveat", text: reason }));
    });
    return ctx.h("div", { class: "ax-sec" }, kids);
  }

  // ------------------------------------------------------- why it ranks here

  function whySection(ctx, action) {
    var basis = Array.isArray(action.rank_basis) ? action.rank_basis : [];
    var confidence = action.confidence || {};
    var kids = [ctx.h("div", { class: "ax-k", text: "Why it ranks here" })];
    var any = false;

    if (basis.length) {
      any = true;
      kids.push(ctx.h("ul", { class: "ax-basis" }, basis.map(function (line) {
        return ctx.h("li", { text: line });
      })));
    }
    if (isNum(action.score)) {
      any = true;
      kids.push(ctx.h("p", { class: "ax-caveat", text: "score " + ctx.fmt.num(action.score, 1) +
        (isNum(action.merged_from) && action.merged_from > 1
          ? " · merged from " + action.merged_from + " findings (" + (action.source || "several sources") + ")"
          : "") }));
    }
    if (Array.isArray(confidence.basis) && confidence.basis.length) {
      any = true;
      kids.push(ctx.h("p", { class: "ax-caveat",
        text: "Confidence " + (confidence.level || "") + ": " + confidence.basis.join("; ") }));
    }
    if (confidence.capped_by) {
      any = true;
      kids.push(ctx.h("p", { class: "ax-p" }, [
        ctx.h("span", { class: "tag warn", text: "capped" }),
        " " + confidence.capped_by,
      ]));
    }
    var effort = action.effort || {};
    if (effort.class || effort.detail) {
      any = true;
      kids.push(ctx.h("p", { class: "ax-p",
        text: "Effort: " + (effort.class || "") + (effort.detail ? " — " + effort.detail : "") }));
      if (effort.heuristic && effort.note) {
        kids.push(ctx.h("p", { class: "ax-caveat", text: effort.note }));
      }
    }
    return any ? ctx.h("div", { class: "ax-sec" }, kids) : null;
  }

  // ------------------------------------------------------------- fix hint

  function fixSection(ctx, action) {
    if (!action.fix_hint) return null;
    return ctx.h("div", { class: "ax-sec" },
      copyable(ctx, "Fix hint", String(action.fix_hint)));
  }

  // ----------------------------------------------- the verification contract

  function verificationSection(ctx, verification) {
    if (!verification) return null;
    var checks = Array.isArray(verification.checks) ? verification.checks : [];
    if (!checks.length && !verification.how) return null;

    var kids = [ctx.h("div", { class: "ax-k", text: "How you will know it worked" })];
    if (checks.length) {
      kids.push(ctx.h("ul", { class: "ax-checks" }, checks.map(function (check) {
        return checkItem(ctx, check);
      })));
    }
    if (verification.caveat) {
      kids.push(ctx.h("p", { class: "ax-caveat", text: verification.caveat }));
    }
    if (verification.how) {
      var command = progressCommand(verification.how);
      if (command) {
        kids = kids.concat(copyable(ctx, "Then compare", command));
        kids.push(ctx.h("p", { class: "ax-caveat", text: verification.how }));
      } else {
        kids = kids.concat(copyable(ctx, "Then compare", String(verification.how)));
      }
    }
    return ctx.h("div", { class: "ax-sec" }, kids);
  }

  function checkItem(ctx, check) {
    var kids = [];
    if (check.kind === "fingerprint") {
      kids.push(ctx.h("div", { class: "what",
        text: check.expect || "These fingerprints stop appearing in the next batch:" }));
      (Array.isArray(check.fingerprints) ? check.fingerprints : []).forEach(function (fp) {
        kids.push(ctx.h("div", null, [ctx.h("span", { class: "ax-fp", text: fp })]));
      });
      if (check.confirms) kids.push(ctx.h("div", { class: "how", text: check.confirms }));
    } else if (check.kind === "process_flag") {
      kids.push(ctx.h("div", { class: "what",
        text: check.expect || "These process flags stop being raised:" }));
      var flagLine = ctx.h("div", null, []);
      (Array.isArray(check.flags) ? check.flags : []).forEach(function (flag, index) {
        if (index) flagLine.appendChild(document.createTextNode(" "));
        flagLine.appendChild(flagNode(ctx, flag));
      });
      kids.push(flagLine);
      if (Array.isArray(check.tasks) && check.tasks.length) {
        kids.push(ctx.h("div", { class: "how", text: "on: " + check.tasks.join(", ") }));
      }
    } else if (check.kind === "success_rate") {
      var line = "Success rate moves from " + (check.current || "?") + " to " +
                 (check.hoped || "?");
      if (Array.isArray(check.current_interval) && check.current_interval.length === 2) {
        line += " (current rate's 95% interval " +
                ctx.fmt.pct(check.current_interval[0], 0) + "–" +
                ctx.fmt.pct(check.current_interval[1], 0) + ")";
      }
      kids.push(ctx.h("div", { class: "what", text: line + "." }));
      if (check.single_rerun_can_confirm === false) {
        // The luck figure is the point of this check, not a footnote: when
        // one re-run cannot confirm anything, say so at full volume.
        var note = check.note ||
          (isNum(check.chance_of_hoped_result_without_a_fix)
            ? "an unchanged agent reaches " + (check.hoped || "that rate") + " " +
              ctx.fmt.pct(check.chance_of_hoped_result_without_a_fix, 0) +
              " of the time by luck alone — one re-run cannot confirm this by " +
              "success rate; rely on the fingerprint check, or add runs"
            : "one re-run cannot confirm this by success rate at this suite size");
        kids.push(ctx.h("div", { class: "ax-luck" }, [
          ctx.h("span", { class: "lbl", text: "Luck warning" }),
          note,
        ]));
      } else if (check.single_rerun_can_confirm === true) {
        kids.push(ctx.h("div", { class: "how",
          text: isNum(check.chance_of_hoped_result_without_a_fix)
            ? "a single re-run can confirm this: an unchanged agent reaches " +
              (check.hoped || "that rate") + " only " +
              ctx.fmt.pct(check.chance_of_hoped_result_without_a_fix, 1) +
              " of the time by luck" + (check.note ? " — " + check.note : "")
            : (check.note || "a single re-run can confirm this by success rate") }));
      } else if (check.note) {
        kids.push(ctx.h("div", { class: "how", text: check.note }));
      }
    } else {
      // A check kind this build does not know: render its stated contract
      // rather than dropping the engine's promise on the floor.
      kids.push(ctx.h("div", { class: "what",
        text: (check.kind ? check.kind.replace(/_/g, " ") + ": " : "") +
              (check.expect || check.note || "see aggregate.json for this check") }));
      if (check.expect && check.note) {
        kids.push(ctx.h("div", { class: "how", text: check.note }));
      }
    }
    return ctx.h("li", { class: "ax-check" }, kids);
  }

  // ------------------------------------------------------- refusals footer

  function refusalsFooter(ctx, notActionable) {
    var entries = Array.isArray(notActionable) ? notActionable : [];
    if (!entries.length) return null;

    var footer = ctx.h("div", { class: "ax-foot" });
    var listHost = ctx.h("div", { style: { display: refusalsOpen ? "block" : "none" } });
    var caret = ctx.h("span", { class: "ax-caret", text: "▸",
      style: refusalsOpen ? { transform: "rotate(90deg)" } : null });
    var toggle = ctx.h("button", {
      class: "ax-foot-toggle", type: "button",
      "aria-expanded": refusalsOpen ? "true" : "false",
      onclick: function () {
        refusalsOpen = !refusalsOpen;
        listHost.style.display = refusalsOpen ? "block" : "none";
        caret.style.transform = refusalsOpen ? "rotate(90deg)" : "";
        toggle.setAttribute("aria-expanded", refusalsOpen ? "true" : "false");
        ctx.signal("inspect");
      },
    }, [
      caret,
      ctx.h("span", {
        text: entries.length + " finding" + (entries.length === 1 ? "" : "s") +
              " deliberately not ranked — each with why",
      }),
    ]);
    footer.appendChild(toggle);

    entries.forEach(function (entry) {
      listHost.appendChild(ctx.h("div", { class: "ax-refusal" }, [
        ctx.h("div", null, [
          entry.source ? ctx.h("span", { class: "tag", text: entry.source }) : null,
          entry.source ? " " : null,
          ctx.h("span", { text: entry.finding || "(unnamed finding)" }),
          " — ",
          ctx.h("span", { class: "why", text: entry.reason || "no reason recorded" }),
        ]),
        entry.detail ? ctx.h("div", { class: "det", text: entry.detail }) : null,
      ]));
    });
    footer.appendChild(listHost);
    return footer;
  }

})(typeof window !== "undefined" ? window : this);
