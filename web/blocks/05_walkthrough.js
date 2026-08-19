/* AgentDiff blocks — the guided walkthrough.
 *
 * One block that tells the comparison as a story: the verdict, where the
 * runs parted, what the detour cost, whether the process was clean, what
 * would have happened otherwise, and what to do about it. Every step of the
 * story is assembled from the report's own fields — the narratives the
 * engine already writes are reused verbatim, with connective tissue around
 * them — and every claim carries an evidence chip that jumps to the step or
 * block that backs it.
 *
 * Evidence chips prefer the trajectory module's own selection machinery:
 * the Tracks block exposes one hit target per (row, side), so dispatching a
 * real click on the right target drives the same pub/sub a hand click would
 * — Tracks highlights the column and Step detail opens the row. When that
 * path is unavailable (tracks collapsed, hidden, or absent), the chip
 * announces the selection as a DOM CustomEvent `agentdiff:select-step`
 * with {row, side} for any listener, and degrades to scrolling to the most
 * relevant block. Nothing here reaches into another module's code.
 *
 * A section whose data is missing is skipped, never faked: the story only
 * says what the report can back.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style

  var STYLE_ID = "agentdiff-walkthrough-css";
  var styled = false;

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".wt-lede{font-size:12px;color:var(--ink-2);margin:0 0 12px;line-height:1.55}",
        ".wt-story{margin:0;padding:0;list-style:none;counter-reset:wt}",
        ".wt-story>li{counter-increment:wt;position:relative;padding:0 0 16px 34px;margin:0;min-width:0}",
        ".wt-story>li::before{content:counter(wt);position:absolute;left:0;top:0;",
        "width:22px;height:22px;border-radius:999px;display:flex;align-items:center;",
        "justify-content:center;background:var(--surface-2);border:1px solid var(--rule-2);",
        "color:var(--ink-2);font-size:11px;font-weight:700;font-variant-numeric:tabular-nums}",
        ".wt-story>li:not(:last-child)::after{content:\"\";position:absolute;left:10.5px;",
        "top:27px;bottom:3px;width:1px;background:var(--rule)}",
        ".wt-k{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;",
        "color:var(--ink-3);font-weight:700;margin:2px 0 4px}",
        ".wt-p{font-size:12.8px;line-height:1.62;color:var(--ink);margin:0}",
        ".wt-p+.wt-p{margin-top:7px}",
        ".wt-p .said{color:var(--ink-2)}",
        ".wt-quote{font-family:var(--mono);font-size:11px;background:var(--surface-2);",
        "border:1px solid var(--rule);border-radius:5px;padding:0 4px;word-break:break-word}",
        ".wt-ev{display:inline-flex;align-items:center;gap:4px;vertical-align:baseline;",
        "margin-left:6px;border:1px solid var(--rule-2);background:var(--surface);",
        "color:var(--accent);border-radius:999px;padding:0 8px 0 6px;font-size:10.5px;",
        "line-height:1.7;cursor:pointer;white-space:nowrap;font-family:var(--sans)}",
        ".wt-ev::before{content:\"⌖\";font-size:11px;line-height:1}",
        ".wt-ev:hover{border-color:var(--accent);",
        "background:color-mix(in srgb, var(--accent) 8%, var(--surface))}",
        ".wt-note{font-size:11px;color:var(--ink-3);margin-top:4px;line-height:1.5}",
        ".wt-flash{outline:2px solid var(--accent);outline-offset:3px;border-radius:11px}",
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is a nicety; the story still reads */ }
  }

  // ------------------------------------------------------------ data access

  function nameOf(report, side) {
    var box = report && report[side];
    return (box && box.agent && box.agent.name) || side.toUpperCase();
  }

  function rowsOf(report) {
    return report && Array.isArray(report.alignment) ? report.alignment : [];
  }

  function stepOf(report, side, index) {
    if (index === null || index === undefined) return null;
    var box = report && report[side];
    var steps = box && Array.isArray(box.steps) ? box.steps : [];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] && steps[i].index === index) return steps[i];
    }
    return steps[index] || null;
  }

  function rowForStep(report, side, index) {
    if (index === null || index === undefined) return -1;
    var rows = rowsOf(report);
    for (var i = 0; i < rows.length; i++) {
      if (rows[i][side + "_index"] === index) return i;
    }
    return -1;
  }

  //: a divergence's indices may fall in different rows when the region is
  //: one-sided; the earliest row matching either side is the anchor.
  function rowForDivergence(report, divergence) {
    var rows = rowsOf(report);
    for (var i = 0; i < rows.length; i++) {
      if (divergence.a_index !== null && divergence.a_index !== undefined &&
          rows[i].a_index === divergence.a_index) return i;
      if (divergence.b_index !== null && divergence.b_index !== undefined &&
          rows[i].b_index === divergence.b_index) return i;
    }
    return -1;
  }

  function isNum(value) { return typeof value === "number" && isFinite(value); }

  function other(side) { return side === "a" ? "b" : "a"; }

  // --------------------------------------------------- evidence navigation

  function flash(node) {
    if (!node) return;
    var bar = document.querySelector(".topbar");
    var offset = (bar ? bar.offsetHeight : 48) + 12;
    var top = node.getBoundingClientRect().top + (global.pageYOffset || 0) - offset;
    try { global.scrollTo({ top: Math.max(0, top), behavior: "smooth" }); }
    catch (err) { global.scrollTo(0, Math.max(0, top)); }
    node.classList.add("wt-flash");
    setTimeout(function () { node.classList.remove("wt-flash"); }, 1300);
  }

  /* Select an alignment row in the trajectory blocks.
   *
   * Preferred path: Tracks draws one hit rect per (row, side) in row order
   * — a real click on the right one drives the trajectory module's own
   * selection pub/sub, so Tracks and Step detail react exactly as they
   * would to a hand click. Fallbacks: a CustomEvent any module may listen
   * for, then a plain scroll to whatever step-ward block exists. */
  function gotoRow(row, side, rowCount) {
    if (row === null || row === undefined || row < 0) return;
    var tracks = document.querySelector('.block[data-block="tracks"]');
    if (tracks) {
      var hits = tracks.querySelectorAll("svg.tj .tj-hit");
      // Two targets per row (A above, B below), appended in row order —
      // verified against the alignment's own row count, so any other
      // layout the tracks block might be drawing (a different view mode,
      // a future change) degrades to the announce-and-scroll path instead
      // of clicking the wrong target.
      var index = row * 2 + (side === "b" ? 1 : 0);
      if (hits.length && hits.length === rowCount * 2 && index < hits.length) {
        try {
          hits[index].dispatchEvent(new MouseEvent("click", {
            bubbles: true, cancelable: true, view: global,
          }));
          flash(tracks);
          return;
        } catch (err) { /* fall through to the announce + scroll path */ }
      }
    }
    try {
      document.dispatchEvent(new CustomEvent("agentdiff:select-step", {
        detail: { row: row, side: side || null },
      }));
    } catch (err) { /* CustomEvent unavailable: scrolling still works */ }
    flash(tracks || document.querySelector('.block[data-block="step-detail"]'));
  }

  //: scroll to the first of the named blocks that is actually on the page.
  function gotoBlock(ids) {
    for (var i = 0; i < ids.length; i++) {
      var node = document.querySelector('.block[data-block="' + ids[i] + '"]');
      if (node) { flash(node); return true; }
    }
    return false;
  }

  // ----------------------------------------------------------- composition

  function chip(ctx, label, title, onclick) {
    return ctx.h("button", {
      class: "wt-ev", type: "button", text: label, title: title,
      onclick: function () {
        if (ctx.signal) ctx.signal("inspect");
        onclick();
      },
    });
  }

  function rowChip(ctx, report, row, side, title) {
    var label = "row " + row + (side ? " · " + side.toUpperCase() : "");
    var rowCount = rowsOf(report).length;
    return chip(ctx, label, title || "Highlight this step in Tracks and open it in Step detail",
                function () { gotoRow(row, side, rowCount); });
  }

  function blockChip(ctx, label, ids, title) {
    return chip(ctx, label, title || "Scroll to the block that carries this evidence",
                function () {
                  if (!gotoBlock(ids)) {
                    // The block is hidden from this layout; the drawer can
                    // restore it, which is where the flash points.
                    var drawer = document.getElementById("btn-drawer");
                    if (drawer) flash(drawer.closest(".topbar") || drawer);
                  }
                });
  }

  //: a term of art, wired to the shared glossary tooltip.
  function term(ctx, text, key) {
    if (typeof ctx.explain === "function") {
      return ctx.explain(ctx.h("span", { text: text }), { term: key });
    }
    return ctx.h("span", { text: text, "data-explain": key });
  }

  //: a verbatim quote from a step's recorded text, kept short.
  function snip(ctx, text, max) {
    var clean = String(text === null || text === undefined ? "" : text).replace(/\s+/g, " ").trim();
    return ctx.h("q", { class: "wt-quote", text: ctx.fmt.truncate(clean, max || 140) });
  }

  function paragraph(ctx, kids) { return ctx.h("p", { class: "wt-p" }, kids); }

  function section(ctx, kicker, kids) {
    return ctx.h("li", null, [ctx.h("div", { class: "wt-k", text: kicker })].concat(kids));
  }

  // ============================================================ the block

  AgentDiff.block({
    id: "walkthrough",
    title: "What happened here",
    question: "The story of this comparison, step by step, with the evidence.",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var report = ctx.report;
      // The narrative leads whenever there is a paired report to narrate.
      return report && report.a && report.b ? 1 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      if (!report || !report.a || !report.b) {
        return ctx.empty(el, "No paired run loaded — there is no story to tell yet.");
      }

      el.appendChild(ctx.h("p", {
        class: "wt-lede",
        text: "Assembled from this report's own findings — nothing below is " +
              "invented, and a section the report cannot back is skipped. " +
              "The ⌖ chips jump to the step or block that carries each claim; " +
              "dotted terms explain themselves on hover, focus, or tap.",
      }));

      var story = ctx.h("ol", { class: "wt-story" });
      var sections = [
        verdictSection(ctx, report),
        diagnosisSection(ctx, report),
        partingSection(ctx, report),
        costSection(ctx, report),
        processSection(ctx, report),
        counterfactualSection(ctx, report),
        triageSection(ctx, report),
      ];
      var told = 0;
      sections.forEach(function (node) {
        if (node) { story.appendChild(node); told++; }
      });
      if (!told) {
        return ctx.empty(el, "This report carries none of the sections the story is built from.");
      }
      el.appendChild(story);
      el.appendChild(ctx.h("p", {
        class: "wt-note",
        text: "Sections not shown are analyses this report does not carry.",
      }));
    },
  });

  // ------------------------------------------------------- 1. the verdict

  function verdictSection(ctx, report) {
    var oa = report.a.outcome || {};
    var ob = report.b.outcome || {};
    if (oa.success === undefined && ob.success === undefined) return null;

    var analysis = report.success_analysis || {};
    var winner = analysis.winner === "a" || analysis.winner === "b" ? analysis.winner : null;
    var nameA = nameOf(report, "a"), nameB = nameOf(report, "b");
    var kids = [];

    var lead;
    if (winner && analysis.basis === "outcome") {
      lead = nameOf(report, winner) + " solved the task and " +
             nameOf(report, other(winner)) + " did not, so the verdict goes to " +
             nameOf(report, winner) + " on outcome.";
    } else if (winner) {
      lead = "Both agents got there; " + nameOf(report, winner) +
             " takes the verdict on efficiency — same destination, less spent on the way.";
    } else if (oa.success === false && ob.success === false) {
      lead = "Neither " + nameA + " nor " + nameB +
             " solved this task, so there is no winner to declare — what follows is the story of how each one failed.";
    } else if (oa.success && ob.success) {
      lead = nameA + " and " + nameB +
             " both solved the task and neither clearly outspent the other; on outcome the runs are equivalent.";
    } else {
      lead = "One side's outcome is undeclared, so the verdict rests on what the trace itself shows.";
    }

    var first = [lead];
    var decision = Array.isArray(analysis.winning_decisions) ? analysis.winning_decisions[0] : null;
    if (decision && winner) {
      var row = rowForStep(report, winner, decision.step_index);
      if (row >= 0) {
        first.push(" The decision that settled it sits at step " + decision.step_index + ".");
        var rowCount = rowsOf(report).length;
        first.push(chip(ctx, "step " + winner.toUpperCase() + "·" + decision.step_index,
                        "The winning decision, in Tracks and Step detail",
                        function () { gotoRow(row, winner, rowCount); }));
      }
    }
    kids.push(paragraph(ctx, first));

    // The engine's own words — its narrative is the best prose in the file.
    if (analysis.narrative) {
      kids.push(paragraph(ctx, [
        ctx.h("span", { class: "said", text: "In the report's own words: " + analysis.narrative }),
      ]));
    }

    // The basis, when an expected answer exists to grade against.
    var evaluation = report.answer_eval || {};
    var va = evaluation.a_vs_expected && evaluation.a_vs_expected.verdict;
    var vb = evaluation.b_vs_expected && evaluation.b_vs_expected.verdict;
    if (evaluation.expected && va && vb && va !== "unknown") {
      kids.push(paragraph(ctx, [
        "Graded against the expected answer, " + nameA + "'s answer is a " + va +
        " and " + nameB + "'s a " + vb + ".",
        blockChip(ctx, "answer diff", ["answer-diff"], "The two answers, token by token"),
      ]));
    }
    return section(ctx, "The verdict", kids);
  }

  // ---------------------------------- 1b. what actually caused it

  function diagnosisSection(ctx, report) {
    var diagnosis = report.diagnosis || {};
    var hypotheses = Array.isArray(diagnosis.hypotheses) ? diagnosis.hypotheses : [];
    if (!hypotheses.length) return null;

    var kids = [];
    var leading = null;
    for (var i = 0; i < hypotheses.length; i++) {
      if (hypotheses[i].id === diagnosis.leading) { leading = hypotheses[i]; }
    }

    // The adjudicated verdict, verbatim — it already hedges exactly as much
    // as the margin warrants, so no prose is added around the claim itself.
    var first = [];
    if (leading) {
      first.push("Every signal in this report was made to argue its own cause, and one account won: ");
      first.push(ctx.h("span", { class: "said", text: diagnosis.verdict }));
    } else {
      first.push("Every signal in this report was made to argue its own cause, and none won: ");
      first.push(ctx.h("span", { class: "said", text: diagnosis.verdict }));
    }
    first.push(blockChip(ctx, "diagnosis", ["diagnosis"],
                         "The full hypothesis ranking, with its evidence"));
    kids.push(paragraph(ctx, first));

    // When the winner points away from the agent, the reader must know
    // before they reach the divergence story below.
    if (leading && leading.kind === "grader_or_label") {
      kids.push(paragraph(ctx, [
        "Read the parting-of-ways below with that in mind: the divergence " +
        "story is still told, but the ranked evidence says the cheaper " +
        "explanation is the grader, and the discriminating check is " +
        "human, not code — " + (leading.discriminator || "re-grade by hand") + ".",
      ]));
    } else if (!leading) {
      kids.push(paragraph(ctx, [
        "Until one of the discriminating checks is run, fixing anything " +
        "is a coin flip between the tied causes.",
      ]));
    }

    // The report arguing with itself is a finding, not a blemish.
    if (Array.isArray(diagnosis.contradictions) && diagnosis.contradictions.length) {
      var tension = ["The report argues with itself, and the diagnosis keeps the argument visible: "];
      tension.push(ctx.h("span", { class: "said", text: diagnosis.contradictions.join(" — and ") }));
      kids.push(paragraph(ctx, tension));
    }

    if (diagnosis.confidence && diagnosis.confidence.basis) {
      kids.push(paragraph(ctx, [
        "How sure: " + (diagnosis.confidence.level || "unstated") + " — " +
        diagnosis.confidence.basis + ".",
      ]));
    }
    return section(ctx, "What actually caused it", kids);
  }

  // ------------------------------------------- 2. where the runs parted

  function partingSection(ctx, report) {
    var divergence = Array.isArray(report.divergences) ? report.divergences[0] : null;
    var attribution = report.attribution || {};
    if (!divergence && !attribution.failed_agent) return null;

    var kids = [];
    var rows = rowsOf(report);
    var anchorRow = divergence ? rowForDivergence(report, divergence) : -1;
    if (anchorRow < 0 && attribution.failed_agent) {
      anchorRow = rowForStep(report, attribution.failed_agent, attribution.root_cause_step);
    }

    if (divergence) {
      var drifted = 0;
      for (var i = 0; i < rows.length && (anchorRow < 0 || i < anchorRow); i++) {
        if (rows[i].op === "drift") drifted++;
      }
      var opening = [
        "The two runs moved in step until ",
        anchorRow >= 0
          ? term(ctx, "alignment row " + anchorRow, "alignment-row")
          : term(ctx, "the first divergence", "divergence"),
        ", where the first ",
        term(ctx, "divergence", "divergence"),
        " opens: " + (divergence.summary || "the paired steps stop corresponding."),
      ];
      if (anchorRow >= 0) opening.push(rowChip(ctx, report, anchorRow, null, "The fork, in Tracks and Step detail"));
      if (drifted > 0) {
        opening.push(" Before the split, " + drifted + " row" + (drifted === 1 ? " had" : "s had") + " already ");
        opening.push(term(ctx, "drifted", "drift"));
        opening.push(" — the same kind of move, different content.");
      }
      kids.push(paragraph(ctx, opening));
    }

    if (attribution.failed_agent && attribution.explanation) {
      kids.push(paragraph(ctx, [
        "The engine's ",
        term(ctx, "root-cause", "root-cause"),
        " read, verbatim: ",
        ctx.h("span", { class: "said", text: attribution.explanation }),
        Array.isArray(attribution.chain) && attribution.chain.length > 1
          ? " The chain shows the mistake "
          : null,
        Array.isArray(attribution.chain) && attribution.chain.length > 1
          ? term(ctx, "propagating", "propagation")
          : null,
        Array.isArray(attribution.chain) && attribution.chain.length > 1
          ? " through " + (attribution.chain.length - 1) + " later step" +
            (attribution.chain.length === 2 ? "" : "s") + "."
          : null,
      ]));
    }

    // The actual texts, quoted — the evidence is the step, not the summary.
    var failed = attribution.failed_agent === "a" || attribution.failed_agent === "b"
      ? attribution.failed_agent : null;
    if (failed && isNum(attribution.root_cause_step)) {
      var rootStep = stepOf(report, failed, attribution.root_cause_step);
      var rootRow = rowForStep(report, failed, attribution.root_cause_step);
      if (rootStep && (rootStep.input || rootStep.output)) {
        var quoteBits = ["At the pivotal step, " + nameOf(report, failed) + "'s input read "];
        quoteBits.push(snip(ctx, rootStep.input || "(empty)"));
        if (rootStep.output) {
          quoteBits.push(" and it came back with ");
          quoteBits.push(snip(ctx, rootStep.output));
        }
        quoteBits.push(".");
        var counterpartIndex = rootRow >= 0 ? rows[rootRow][other(failed) + "_index"] : null;
        var counterpart = stepOf(report, other(failed), counterpartIndex);
        if (counterpart && counterpart.output) {
          quoteBits.push(" " + nameOf(report, other(failed)) + "'s counterpart in the same row produced ");
          quoteBits.push(snip(ctx, counterpart.output));
          quoteBits.push(".");
        }
        if (rootRow >= 0) quoteBits.push(rowChip(ctx, report, rootRow, failed, "The root-cause step itself"));
        kids.push(paragraph(ctx, quoteBits));
      }
    }
    if (!kids.length) return null;
    return section(ctx, "Where the runs parted", kids);
  }

  // ------------------------------------------------------ 3. what it cost

  function costSection(ctx, report) {
    var delta = report.metrics_delta;
    if (!delta || !delta.tokens || !isNum(delta.tokens.a) || !isNum(delta.tokens.b)) return null;

    var F = ctx.fmt;
    var heavy = delta.tokens.b > delta.tokens.a ? "b"
              : delta.tokens.a > delta.tokens.b ? "a" : null;
    var kids = [];
    if (!heavy) {
      kids.push(paragraph(ctx, [
        "On resources the runs are even: both spent " + F.tokens(delta.tokens.a) +
        (delta.latency_s ? ", around " + F.sec(delta.latency_s.a) + " apiece" : "") + ".",
        blockChip(ctx, "the numbers", ["deltas"], "Every metric, side by side"),
      ]));
    } else {
      var light = other(heavy);
      var pieces = [
        nameOf(report, heavy) + " paid for its path: " +
        F.tokens(delta.tokens[heavy]) + " against " + F.tokens(delta.tokens[light]) +
        " (" + F.delta(delta.tokens[heavy] - delta.tokens[light], "tokens") + ")",
      ];
      if (delta.latency_s && isNum(delta.latency_s[heavy]) && isNum(delta.latency_s[light])) {
        pieces.push(", " + F.sec(delta.latency_s[heavy]) + " of latency against " +
                    F.sec(delta.latency_s[light]));
      }
      if (delta.cost_usd && isNum(delta.cost_usd[heavy]) && isNum(delta.cost_usd[light])) {
        pieces.push(", and " + F.usd(delta.cost_usd[heavy]) + " against " +
                    F.usd(delta.cost_usd[light]));
      }
      if (delta.steps && isNum(delta.steps[heavy]) && isNum(delta.steps[light])) {
        var extra = delta.steps[heavy] - delta.steps[light];
        pieces.push(" — " + (extra > 0 ? extra + " extra step" + (extra === 1 ? "" : "s")
                                       : "in " + delta.steps[heavy] + " steps") + ".");
      } else {
        pieces.push(".");
      }
      pieces.push(blockChip(ctx, "the numbers", ["deltas"], "Every metric, side by side"));
      kids.push(paragraph(ctx, pieces));
    }

    // Say when the counts are estimates, with the glossary carrying why it matters.
    var bases = [];
    ["a", "b"].forEach(function (side) {
      var accounting = report[side] && report[side].token_accounting;
      if (accounting && accounting.basis && accounting.basis !== "measured") {
        bases.push(nameOf(report, side));
      }
    });
    if (bases.length) {
      kids.push(paragraph(ctx, [
        "Token counts for " + bases.join(" and ") + " are partly ",
        term(ctx, "estimated rather than measured", "tokens-basis"),
        ", so read the token comparison as approximate.",
      ]));
    }
    return section(ctx, "What it cost", kids);
  }

  // ------------------------------------------- 4. was the process clean?

  function processSection(ctx, report) {
    var process = report.process;
    var gapA = process && process.a && process.a.gap;
    var gapB = process && process.b && process.b.gap;
    if (!gapA && !gapB) return null;

    var kids = [];
    var opener = [];
    if (gapA && gapA.verdict) opener.push(nameOf(report, "a") + " " + gapA.verdict);
    if (gapB && gapB.verdict) opener.push(nameOf(report, "b") + " " + gapB.verdict);
    var openerKids = ["Held against its own trace, " + opener.join("; ") + "."];
    if (process.narrative) {
      openerKids.push(ctx.h("span", { class: "said", text: " " + process.narrative }));
    }
    openerKids.push(blockChip(ctx, "process evidence", ["gap", "integrity-flags"],
                              "The gap verdicts and every raised flag"));
    kids.push(paragraph(ctx, openerKids));

    ["a", "b"].forEach(function (side) {
      var gap = side === "a" ? gapA : gapB;
      if (!gap || !gap.verdict) return;
      if (gap.verdict === "passed but pathological") {
        var flagBits = flagPhrases(ctx, gap.raised || []);
        var bits = [
          nameOf(report, side) + "'s pass is the outcome-process gap in person — ",
          term(ctx, "passed but pathological", "passed-but-pathological"),
          ": on the way to its accepted answer it ",
        ];
        bits = bits.concat(flagBits);
        bits.push(". An outcome-only scoreboard gives this run the same mark as a clean one.");
        kids.push(paragraph(ctx, bits));
      } else if (gap.verdict === "failed but clean") {
        kids.push(paragraph(ctx, [
          nameOf(report, side) + " is the mirror case — ",
          term(ctx, "failed but clean", "failed-but-clean"),
          ": it failed the check with nothing visibly wrong in its trace, which is evidence " +
          "about the grader as much as the agent. Read the grading before blaming the run.",
        ]));
      }
    });
    return section(ctx, "Was the process clean?", kids);
  }

  //: raised flag keys → readable phrases, with glossary terms where one exists.
  function flagPhrases(ctx, raised) {
    var wording = {
      looped: "looped",
      loop_block: "cycled through the same block of calls",
      repeated_calls: "repeated the same call",
      swallowed_error: "hit an error and pressed on as if it had not",
      budget_pressure: "finished on the edge of its step budget",
      undeclared_tools: "called a tool it was never offered",
      invented_arguments: "used argument values with no source in the trace",
      schema_violation: "called a tool with arguments that do not typecheck",
    };
    var nodes = raised.map(function (flag) {
      if (flag === "blind_write") return term(ctx, "wrote blind", "blind-write");
      if (flag === "false_success") return term(ctx, "claimed a false success", "false-success");
      if (flag === "no_information_steps") return term(ctx, "took no-information steps", "no-information-step");
      return wording[flag] || flag.replace(/_/g, " ");
    });
    if (!nodes.length) return ["raised no individual flags"];
    var out = [];
    nodes.forEach(function (node, index) {
      if (index > 0) out.push(index === nodes.length - 1 ? " and " : ", ");
      out.push(node);
    });
    return out;
  }

  // ------------------------------------- 5. what would have happened

  function counterfactualSection(ctx, report) {
    var cf = report.counterfactual;
    if (!cf || (!cf.narrative && !cf.estimate)) return null;

    var kids = [];
    var lead = [];
    if (cf.narrative) {
      lead.push(cf.narrative);
    } else if (cf.estimate) {
      lead.push((cf.premise ? cf.premise.charAt(0).toUpperCase() + cf.premise.slice(1) : "In the counterfactual") +
                ", the estimated run ends in " + (cf.estimate.outcome || "an unknown outcome") + ".");
    }
    if (cf.confidence) lead.push(" Confidence: " + cf.confidence + ".");
    lead.push(blockChip(ctx, "the what-if", ["counterfactual"], "The full counterfactual estimate"));
    kids.push(paragraph(ctx, lead));

    kids.push(paragraph(ctx, [
      "That estimate is a ",
      term(ctx, "splice of observed steps", "splice-counterfactual"),
      " — the winner's recorded suffix grafted onto the loser's recorded prefix, costed at " +
      "what those steps actually spent — not a simulation of what the agent would really have done.",
    ]));

    // Only when there is a positive gap to divide — a negative "saving"
    // (the winner actually spent more) would read as nonsense here, and the
    // Shapley block itself carries that nuance.
    var shapley = report.shapley;
    if (shapley && shapley.available && Array.isArray(shapley.allocations) &&
        shapley.allocations.length && isNum(shapley.total_saving) && shapley.total_saving > 0) {
      var top = shapley.allocations[0];
      shapley.allocations.forEach(function (allocation) {
        if (isNum(allocation.shapley) && Math.abs(allocation.shapley) > Math.abs(top.shapley || 0)) {
          top = allocation;
        }
      });
      var unit = shapley.metric === "tokens" ? "token" : (shapley.metric || "token");
      kids.push(paragraph(ctx, [
        "Dividing the " + ctx.fmt.int(shapley.total_saving) + "-" + unit +
        " gap fairly across " + shapley.regions + " divergence region" +
        (shapley.regions === 1 ? "" : "s") + ", the " + (top.kind || "top") +
        " decision carries a ",
        term(ctx, "Shapley share", "shapley-share"),
        " of " + ctx.fmt.pct(top.share, 0) + ".",
        blockChip(ctx, "the split", ["shapley"], "The full Shapley allocation"),
      ]));
    }
    return section(ctx, "What would have happened otherwise", kids);
  }

  // -------------------------------------------------- 6. what to do next

  function triageSection(ctx, report) {
    var triage = (ctx.aggregate || {}).triage;
    var actions = triage && Array.isArray(triage.actions) ? triage.actions : [];
    if (!actions.length) return null;

    var kids = [];
    var mastKnown = !!(ctx.aggregate && ctx.aggregate.taxonomy);
    actions.slice(0, 2).forEach(function (action, index) {
      var bits = [(index === 0 ? "Across the whole batch, triage puts this first: " : "Second: ") +
                  action.action + "."];
      var evidence = action.evidence || {};
      var rate = evidence.occurrence_rate;
      if (rate && isNum(rate.rate) && Array.isArray(rate.interval)) {
        bits.push(" Seen on " + (evidence.task_count || rate.k) + " of " +
                  (evidence.of_tasks || rate.n) + " tasks (");
        bits.push(term(ctx, "Wilson 95%", "wilson-interval"));
        bits.push(" interval " + ctx.fmt.pct(rate.interval[0], 0) + "–" +
                  ctx.fmt.pct(rate.interval[1], 0) + ").");
      } else if (evidence.task_count && evidence.of_tasks) {
        bits.push(" Seen on " + evidence.task_count + " of " + evidence.of_tasks + " tasks.");
      }
      if (index === 0) {
        bits.push(blockChip(ctx, "triage detail", ["recommendations", "issues"],
                            "The ranked actions with their evidence"));
      }
      // The verification contract makes the action a testable hypothesis:
      // say how the fixer will know it worked, and be honest when the
      // success rate cannot confirm it at this suite size.
      var verification = action.verification || {};
      var checks = Array.isArray(verification.checks) ? verification.checks : [];
      var fingerprintCheck = null, rateCheck = null;
      checks.forEach(function (check) {
        if (check.kind === "fingerprint" && !fingerprintCheck) fingerprintCheck = check;
        if (check.kind === "success_rate") rateCheck = check;
      });
      if (fingerprintCheck || rateCheck) {
        var how = ["How you will know the fix worked: "];
        if (fingerprintCheck) {
          how.push("the fingerprint" +
                   (fingerprintCheck.fingerprints.length > 1 ? "s" : "") +
                   " behind this finding should stop appearing in the next batch" +
                   " — a binary, deterministic check");
        }
        if (rateCheck) {
          how.push((fingerprintCheck ? ". " : "") +
                   (rateCheck.single_rerun_can_confirm
                    ? "The success rate can also confirm it: an unchanged agent " +
                      "reaches " + rateCheck.hoped + " only " +
                      ctx.fmt.pct(rateCheck.chance_of_hoped_result_without_a_fix, 1) +
                      " of the time by luck"
                    : "The success rate alone cannot confirm it here: an " +
                      "unchanged agent reaches " + rateCheck.hoped + " " +
                      ctx.fmt.pct(rateCheck.chance_of_hoped_result_without_a_fix, 0) +
                      " of the time by pure luck, so rely on the fingerprint " +
                      "check or add runs"));
        }
        how.push(". Re-run the same tasks, then compare with the progress command.");
      }
      kids.push(paragraph(ctx, bits));
      if (fingerprintCheck || rateCheck) {
        kids.push(paragraph(ctx, [ctx.h("span", { class: "wt-note" }, null)].map(function (span) {
          span.textContent = how.join("");
          return span;
        })));
      }
    });
    if (mastKnown) {
      kids.push(paragraph(ctx, [
        "The failure categories behind these actions are also mapped onto the ",
        term(ctx, "MAST and TRAIL", "mast-trail"),
        " taxonomies, so the findings can be discussed in a shared vocabulary.",
        blockChip(ctx, "taxonomy", ["taxonomy", "taxonomy-coverage"], "The MAST and TRAIL labels"),
      ]));
    }
    kids.push(paragraph(ctx, [
      ctx.h("span", { class: "wt-note", text: "These actions are batch-level: they rank what recurs across every task, not this task alone." }),
    ]));
    return section(ctx, "What to do", kids);
  }

})(typeof window !== "undefined" ? window : this);
