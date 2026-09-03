/* AgentDiff blocks — the adjudicated diagnosis.
 *
 * One block that shows the report's competing explanations for what went
 * wrong, ranked as the engine ranked them. The verdict is quoted verbatim —
 * never paraphrased — and when no hypothesis clears the runner-up the block
 * says "contested" plainly instead of manufacturing a winner. Every
 * hypothesis stays visible whatever its fate: merged ones are muted and
 * labelled as part of the leading account, ruled-out ones are struck
 * through, untestable ones show no score rather than an invented one.
 *
 * Details on demand, following the actions block's pattern: each hypothesis
 * row is a button that opens its evidence — the exact spans and metrics the
 * engine scored it on, each with its basis label — and the discriminator:
 * the concrete check that would settle the question.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style

  var STYLE_ID = "agentdiff-diagnosis-css";
  var styled = false;

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".dx-lede{font-size:12px;color:var(--ink-2);margin:0 0 10px;line-height:1.55}",
        ".dx-fold{margin:8px 0 0}.dx-fold>summary{cursor:pointer;font-size:12px;color:var(--ink-3);",
        "list-style:none;padding:4px 0}.dx-fold>summary::before{content:'▸ ';}.dx-fold[open]>summary::before{content:'▾ ';}",
        ".dx-verdict{border:1px solid var(--rule);border-left:3px solid var(--accent);",
        "border-radius:7px;background:var(--surface-2);padding:8px 10px;margin:0 0 10px;",
        "font-size:13px;line-height:1.55;color:var(--ink)}",
        ".dx-verdict.contested{border-left-color:var(--warn)}",
        ".dx-verdict .k{display:block;font-size:11px;text-transform:uppercase;",
        "letter-spacing:.08em;color:var(--ink-3);font-weight:700;margin-bottom:3px}",
        ".dx-decisive{border:1px solid var(--rule);border-left:3px solid var(--accent);",
        "border-radius:7px;background:var(--surface-2);padding:7px 10px;margin:0 0 10px;",
        "font-size:12.5px;line-height:1.5;color:var(--ink)}",
        ".dx-decisive .k{display:block;font-size:11px;text-transform:uppercase;",
        "letter-spacing:.08em;color:var(--ink-3);font-weight:700;margin-bottom:3px}",
        ".dx-decisive b{color:var(--accent)}",
        ".dx-decisive .dx-crit{display:block;font-size:11px;color:var(--ink-3);",
        "margin-top:2px}",
        ".dx-decisive.abstain{border-left-style:dashed;border-left-color:var(--rule-2);",
        "color:var(--ink-2)}",
        ".dx-list{list-style:none;margin:0;padding:0}",
        ".dx-row{border-top:1px solid var(--rule)}",
        ".dx-row:first-child{border-top:none}",
        ".dx-head{display:flex;width:100%;gap:8px;align-items:baseline;background:none;",
        "border:0;padding:8px 0;cursor:pointer;text-align:left;font:inherit;color:inherit}",
        ".dx-head:hover .dx-statement{color:var(--accent)}",
        ".dx-badge{flex:none}",
        ".tag.dx-lead{color:var(--accent);font-weight:700;",
        "border-color:color-mix(in srgb, var(--accent) 55%, transparent)}",
        ".tag.dx-mute{color:var(--ink-3);border-color:var(--rule)}",
        ".dx-main{flex:1;min-width:0}",
        ".dx-kind{font-family:var(--mono);font-size:11px;color:var(--ink-3)}",
        ".dx-score{font-family:var(--mono);font-size:11px;color:var(--ink-2);",
        "font-variant-numeric:tabular-nums;flex:none}",
        ".dx-statement{display:block;font-size:12.5px;line-height:1.5;color:var(--ink)}",
        ".dx-part{font-size:11px;color:var(--ink-3);font-style:italic}",
        ".dx-row.dim .dx-statement{color:var(--ink-3)}",
        ".dx-row.struck .dx-statement{text-decoration:line-through;color:var(--ink-3)}",
        ".dx-caret{flex:none;color:var(--ink-3);font-size:11px;",
        "transition:transform .12s ease}",
        ".dx-row.open .dx-caret{transform:rotate(90deg)}",
        ".dx-body{display:none;padding:0 0 10px 8px}",
        ".dx-row.open .dx-body{display:block}",
        ".dx-h{font-size:11px;text-transform:uppercase;letter-spacing:.06em;",
        "color:var(--ink-3);font-weight:700;margin:6px 0 3px}",
        ".dx-ev{list-style:none;margin:0;padding:0}",
        ".dx-ev li{font-size:11.5px;line-height:1.55;color:var(--ink-2);",
        "padding:2px 0;word-break:break-word}",
        ".dx-quote{font-family:var(--mono);font-size:11px;background:var(--surface-2);",
        "border:1px solid var(--rule);border-radius:5px;padding:0 4px}",
        ".dx-path{font-family:var(--mono);font-size:11px}",
        ".dx-basis{display:inline-block;margin-left:6px;font-size:11px;",
        "text-transform:uppercase;letter-spacing:.05em;border:1px solid var(--rule-2);",
        "border-radius:999px;padding:0 6px;color:var(--ink-3);vertical-align:1px}",
        ".dx-basis.soft{color:var(--warn);",
        "border-color:color-mix(in srgb, var(--warn) 45%, transparent)}",
        ".dx-settle{font-size:11.5px;line-height:1.55;color:var(--ink-2);margin:6px 0 0}",
        ".dx-settle b{color:var(--ink)}",
        ".dx-causal{border-top:1px solid var(--rule);margin-top:4px}",
        ".dx-causal-head{display:flex;width:100%;gap:8px;align-items:baseline;",
        "background:none;border:0;padding:8px 0;cursor:pointer;text-align:left;",
        "font:inherit;color:inherit}",
        ".dx-causal-title{flex:1;font-size:11px;text-transform:uppercase;",
        "letter-spacing:.06em;color:var(--ink-3);font-weight:700}",
        ".dx-causal-head:hover .dx-causal-title{color:var(--accent)}",
        ".dx-causal.open .dx-caret{transform:rotate(90deg)}",
        ".dx-causal-body{display:none;padding:0 0 10px 8px}",
        ".dx-causal.open .dx-causal-body{display:block}",
        ".dx-clist{list-style:none;margin:0;padding:0}",
        ".dx-clist li{padding:4px 0;border-top:1px solid var(--rule)}",
        ".dx-clist li:first-child{border-top:none}",
        ".dx-cstep{display:block;font-size:12px;line-height:1.5;color:var(--ink)}",
        ".dx-cstep .n{font-family:var(--mono);font-size:11px;color:var(--ink-2)}",
        ".dx-mech{display:block;font-size:11px;line-height:1.5;color:var(--ink-3);",
        "margin-top:1px;padding-left:8px;border-left:2px solid var(--rule-2)}",
        ".dx-mech.soft{font-style:italic;border-left-style:dashed}",
        ".dx-ceid{display:block;font-family:var(--mono);font-size:11px;",
        "color:var(--ink-3);margin-top:1px;padding-left:10px}",
        ".dx-tension{border:1px solid var(--rule);border-left:3px solid var(--warn);",
        "border-radius:7px;background:var(--surface-2);padding:7px 10px;margin:10px 0 0}",
        ".dx-tension .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;",
        "color:var(--warn);font-weight:700;margin-bottom:3px}",
        ".dx-tension ul{margin:0;padding-left:16px}",
        ".dx-tension li{font-size:11.5px;line-height:1.55;color:var(--ink-2)}",
        ".dx-conf{font-size:11.5px;color:var(--ink-3);line-height:1.5;",
        "border-top:1px solid var(--rule);margin-top:10px;padding-top:7px}",
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is a nicety; the list still reads */ }
  }

  // ------------------------------------------------------------------ utils

  function diagnosisOf(report) {
    var diag = report && report.diagnosis;
    if (!diag || typeof diag !== "object") return null;
    if (!Array.isArray(diag.hypotheses) || !diag.hypotheses.length) return null;
    return diag;
  }

  function agentName(report, side) {
    var run = report && report[side];
    var agent = run && run.agent;
    if (agent && agent.name) return agent.name;
    return side ? String(side).toUpperCase() : "?";
  }

  function hasScore(hypothesis) {
    return typeof hypothesis.score === "number" && isFinite(hypothesis.score);
  }

  //: a term of art, wired to the shared glossary tooltip.
  function term(ctx, text, glossaryKey) {
    if (typeof ctx.explain === "function") {
      return ctx.explain(ctx.h("span", { text: text }), { term: glossaryKey });
    }
    return ctx.h("span", { text: text, "data-explain": glossaryKey });
  }

  /* Which visual register each status gets. The wording stays the data's
   * own (underscores aside); only the styling is ours. */
  var STATUS = {
    leading:    { tag: "dx-lead", row: "" },
    plausible:  { tag: "",        row: "" },
    weak:       { tag: "dx-mute", row: "dim" },
    merged:     { tag: "dx-mute", row: "dim" },
    ruled_out:  { tag: "dx-mute", row: "struck" },
    untestable: { tag: "dx-mute", row: "dim" },
  };

  //: open/closed per hypothesis, surviving re-renders within the session.
  var openRows = {};

  // --------------------------------------------------------------- evidence

  function evidenceIndex(diag) {
    var byId = {};
    (Array.isArray(diag.evidence) ? diag.evidence : []).forEach(function (item) {
      if (item && item.id) byId[item.id] = item;
    });
    return byId;
  }

  function basisTag(ctx, basis) {
    if (!basis) return null;
    var soft = basis === "estimated" || basis === "inferred";
    return ctx.h("span", {
      class: "dx-basis" + (soft ? " soft" : ""),
      text: String(basis),
      title: soft
        ? "This item is " + basis + ", not read directly from the trace"
        : "This item is " + basis + " in the trace",
    });
  }

  /* One evidence item, verbatim.
   *   span   → step N (agent) — 'quote'
   *   metric → path = value
   * An id the ledger does not carry is shown as the bare id — a gap in the
   * data is shown as a gap, never papered over. */
  function evidenceLine(ctx, report, item, eid) {
    if (!item) {
      return ctx.h("li", null, [ctx.h("span", { class: "dx-path", text: String(eid) }),
                                " (not in this report's evidence ledger)"]);
    }
    var kids = [];
    if (item.type === "span") {
      kids.push("step " + item.step + " (" + agentName(report, item.agent) + ") — ");
      kids.push(ctx.h("q", { class: "dx-quote", text: String(item.quote === undefined || item.quote === null ? "" : item.quote) }));
    } else if (item.type === "metric") {
      kids.push(ctx.h("span", { class: "dx-path", text: String(item.path) + " = " + String(item.value) }));
    } else {
      kids.push(ctx.h("span", { class: "dx-path", text: String(item.id || eid) }));
    }
    if (item.signal) kids.push(" · " + String(item.signal));
    kids.push(basisTag(ctx, item.basis));
    return ctx.h("li", null, kids);
  }

  function evidenceList(ctx, report, byId, ids) {
    var list = ctx.h("ul", { class: "dx-ev" });
    ids.forEach(function (eid) {
      list.appendChild(evidenceLine(ctx, report, byId[eid], eid));
    });
    return list;
  }

  // ------------------------------------------------------------------- rows

  function hypothesisRow(ctx, report, diag, byId, hypothesis, rowKey) {
    var status = String(hypothesis.status || "");
    var look = STATUS[status] || { tag: "", row: "" };
    var open = !!openRows[rowKey];

    var row = ctx.h("li", {
      class: "dx-row" + (look.row ? " " + look.row : "") + (open ? " open" : ""),
      "data-status": status,
    });

    var badge = term(ctx, status.replace(/_/g, " "), "hypothesis-status");
    badge.className = "tag dx-badge" + (look.tag ? " " + look.tag : "");

    var main = ctx.h("div", { class: "dx-main" }, [
      ctx.h("span", {
        class: "dx-kind",
        text: String(hypothesis.kind || "") +
              (hypothesis.flag ? " (" + hypothesis.flag + ")" : ""),
      }),
      ctx.h("span", { class: "dx-statement" }, [
        String(hypothesis.statement || ""),
        status === "merged"
          ? ctx.h("span", { class: "dx-part", text: " — part of the leading account" })
          : null,
      ]),
    ]);

    var head = ctx.h("button", {
      class: "dx-head", type: "button",
      "aria-expanded": open ? "true" : "false",
      title: open ? "Collapse this hypothesis"
                  : "Expand: the evidence behind it, and how to settle it",
    }, [
      badge,
      main,
      ctx.h("span", {
        class: "dx-score",
        text: hasScore(hypothesis) ? ctx.fmt.num(hypothesis.score, 2) : "—",
        title: hasScore(hypothesis) ? "adjudication score" : "no score — not testable from this trace",
      }),
      ctx.h("span", { class: "dx-caret", text: "▸" }),
    ]);

    var body = ctx.h("div", { class: "dx-body" });
    var supports = Array.isArray(hypothesis.supports) ? hypothesis.supports : [];
    var contradicts = Array.isArray(hypothesis.contradicts) ? hypothesis.contradicts : [];
    if (supports.length) {
      body.appendChild(ctx.h("div", { class: "dx-h", text: "Supported by" }));
      body.appendChild(evidenceList(ctx, report, byId, supports));
    }
    if (contradicts.length) {
      body.appendChild(ctx.h("div", { class: "dx-h", text: "Contradicted by" }));
      body.appendChild(evidenceList(ctx, report, byId, contradicts));
    }
    if (!supports.length && !contradicts.length) {
      body.appendChild(ctx.h("div", {
        class: "dx-settle",
        text: "No evidence items scored for or against this hypothesis.",
      }));
    }
    if (hypothesis.discriminator) {
      var settle = ctx.h("p", { class: "dx-settle" });
      var label = term(ctx, "How to settle it", "discriminator");
      settle.appendChild(ctx.h("b", null, [label, ": "]));
      settle.appendChild(document.createTextNode(String(hypothesis.discriminator)));
      body.appendChild(settle);
    }

    head.addEventListener("click", function (event) {
      // A click on the status term is a glossary lookup, not a toggle.
      if (event.target && event.target.closest &&
          event.target.closest("[data-explain]")) return;
      openRows[rowKey] = !openRows[rowKey];
      row.classList.toggle("open", !!openRows[rowKey]);
      head.setAttribute("aria-expanded", openRows[rowKey] ? "true" : "false");
      head.setAttribute("title", openRows[rowKey]
        ? "Collapse this hypothesis"
        : "Expand: the evidence behind it, and how to settle it");
      ctx.signal("inspect");
    });

    row.appendChild(head);
    row.appendChild(body);
    return row;
  }

  // --------------------------------------------------- the decisive step

  /* The decisive-step call, right under the verdict. A null step is an
   * honest abstention with its reason, not a missing field — it gets a
   * distinct muted-but-visible register instead of being dropped. */
  function decisiveLine(ctx, decisive) {
    var hasStep = decisive.step !== null && decisive.step !== undefined;
    var criterion = decisive.criterion ? String(decisive.criterion) : "";
    var line = ctx.h("div", {
      class: "dx-decisive" + (hasStep ? "" : " abstain"),
      title: criterion || null,
    });
    line.appendChild(ctx.h("span", { class: "k", text: "Decisive step" }));
    if (hasStep) {
      line.appendChild(ctx.h("b", { text: "Step " + decisive.step }));
      if (decisive.basis) {
        line.appendChild(document.createTextNode(" — " + String(decisive.basis)));
      }
      if (criterion) {
        line.appendChild(ctx.h("span", { class: "dx-crit", text: criterion }));
      }
    } else {
      line.appendChild(document.createTextNode(
        "No decisive step: " + String(decisive.reason || "")));
    }
    return line;
  }

  // --------------------------------------------------- the causal account

  /* Is this link's mechanism merely positional — adjacency in the trace —
   * rather than a measured propagation? The wording stays the data's own;
   * only the visual register (italic, dashed accent) is ours. */
  function isPositionalMechanism(mechanism) {
    return /\b(adjacency|positional)\b/i.test(mechanism) &&
           mechanism.indexOf("measured") < 0;
  }

  //: one causal link, verbatim: "step N — happened", mechanism underneath.
  function causalRow(ctx, link) {
    var kids = [
      ctx.h("span", { class: "dx-cstep" }, [
        ctx.h("span", { class: "n", text: "step " + link.step }),
        " — " + String(link.happened || ""),
      ]),
    ];
    if (link.mechanism !== null && link.mechanism !== undefined) {
      var mechanism = String(link.mechanism);
      var soft = isPositionalMechanism(mechanism);
      kids.push(ctx.h("span", {
        class: "dx-mech" + (soft ? " soft" : ""),
        text: mechanism,
        title: soft
          ? "adjacency/positional link — not a measured propagation"
          : null,
      }));
    }
    var eids = Array.isArray(link.evidence) ? link.evidence : [];
    if (eids.length) {
      kids.push(ctx.h("span", { class: "dx-ceid", text: eids.join(" ") }));
    }
    return ctx.h("li", null, kids);
  }

  /* The step-by-step account of how the fault travelled, collapsible in the
   * block's details-on-demand pattern. Every string is the report's own. */
  function causalSection(ctx, account, sectionKey) {
    var open = !!openRows[sectionKey];
    var section = ctx.h("div", { class: "dx-causal" + (open ? " open" : "") });

    var head = ctx.h("button", {
      class: "dx-causal-head", type: "button",
      "aria-expanded": open ? "true" : "false",
      title: open ? "Collapse the causal account"
                  : "Expand: how the fault travelled, step by step",
    }, [
      ctx.h("span", {
        class: "dx-causal-title",
        text: "Causal account — " + account.length + " step" +
              (account.length === 1 ? "" : "s"),
      }),
      ctx.h("span", { class: "dx-caret", text: "▸" }),
    ]);
    head.addEventListener("click", function () {
      openRows[sectionKey] = !openRows[sectionKey];
      section.classList.toggle("open", !!openRows[sectionKey]);
      head.setAttribute("aria-expanded", openRows[sectionKey] ? "true" : "false");
      head.setAttribute("title", openRows[sectionKey]
        ? "Collapse the causal account"
        : "Expand: how the fault travelled, step by step");
      ctx.signal("inspect");
    });

    var body = ctx.h("div", { class: "dx-causal-body" }, [
      ctx.h("ol", { class: "dx-clist" }, account.map(function (link) {
        return causalRow(ctx, link);
      })),
    ]);

    section.appendChild(head);
    section.appendChild(body);
    return section;
  }

  // ============================================================ the block

  AgentDiff.block({
    id: "diagnosis",
    title: "Diagnosis",
    question: "Which explanation best fits the evidence — and what would settle it?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      return diagnosisOf(ctx.report) ? 0.95 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var diag = diagnosisOf(report);
      if (!diag) {
        return ctx.empty(el, "This report carries no adjudicated diagnosis.");
      }

      el.appendChild(ctx.h("p", {
        class: "dx-lede",
        text: "Competing explanations, ranked as the engine scored them " +
              "against this report's own evidence. Nothing below is " +
              "invented: the verdict is quoted verbatim, and every " +
              "hypothesis stays on the list whatever its fate. Click a row " +
              "for its evidence and the check that would settle it.",
      }));

      // 1. The verdict, verbatim — with a clearly marked contested state
      // when the scores refused to pick a single cause.
      var contested = (diag.leading === null || diag.leading === undefined) &&
                      diag.hypotheses.some(hasScore);
      var verdict = ctx.h("div", {
        class: "dx-verdict" + (contested ? " contested" : ""),
      });
      if (contested) {
        var kicker = ctx.h("span", { class: "k" });
        kicker.appendChild(term(ctx, "Contested — no single cause", "contested-diagnosis"));
        verdict.appendChild(kicker);
      } else {
        verdict.appendChild(ctx.h("span", { class: "k", text: "Verdict" }));
      }
      verdict.appendChild(document.createTextNode(String(diag.verdict || "")));
      el.appendChild(verdict);

      // 1b. The decisive step — the earliest correction expected to flip
      // the outcome, or the stated reason there is none.
      var decisive = diag.decisive_step;
      if (decisive && typeof decisive === "object") {
        el.appendChild(decisiveLine(ctx, decisive));
      }

      // 2. The hypotheses, in the order the report ranked them.
      var byId = evidenceIndex(diag);
      var taskKey = (report.task && report.task.id ? report.task.id : "?");
      var list = ctx.h("ol", { class: "dx-list" });
      diag.hypotheses.forEach(function (hypothesis, index) {
        var rowKey = taskKey + ":" + (hypothesis.id || index);
        list.appendChild(hypothesisRow(ctx, report, diag, byId, hypothesis, rowKey));
      });
      el.appendChild(list);
      if (ctx.lane === "story" || (el.closest && el.closest(".story-lane"))) {
        // the story shows the leading account; every other hypothesis is
        // one click away, still verbatim, still on the page
        var others = Array.prototype.slice.call(list.querySelectorAll('.dx-row:not([data-status="leading"])'));
        if (others.length) {
          var fold = ctx.h("details", { class: "dx-fold" }, [
            ctx.h("summary", { text: others.length + " other hypothes" + (others.length === 1 ? "is" : "es") + " (merged, plausible, ruled out)" }),
          ]);
          var inner = ctx.h("ol", { class: "dx-list" });
          others.forEach(function (row) { inner.appendChild(row); });
          fold.appendChild(inner);
          list.parentNode.insertBefore(fold, list.nextSibling);
        }
      }

      // 2b. The causal account — how the fault travelled, step by step.
      var account = Array.isArray(diag.causal_account) ? diag.causal_account : [];
      if (account.length) {
        el.appendChild(causalSection(ctx, account, taskKey + ":causal-account"));
      }

      // 3. Cross-signal conflicts, stated plainly whoever wins.
      var tensions = Array.isArray(diag.contradictions) ? diag.contradictions : [];
      if (tensions.length) {
        el.appendChild(ctx.h("div", { class: "dx-tension" }, [
          ctx.h("div", { class: "k", text: "Evidence in tension" }),
          ctx.h("ul", null, tensions.map(function (line) {
            return ctx.h("li", { text: String(line) });
          })),
        ]));
      }

      // 4. Confidence — level and basis, in the report's own words.
      var confidence = diag.confidence || {};
      if (confidence.level || confidence.basis) {
        el.appendChild(ctx.h("div", {
          class: "dx-conf",
          text: "Confidence: " + String(confidence.level || "unstated") +
                (confidence.basis ? " — " + String(confidence.basis) : ""),
        }));
      }
    },
  });

})(typeof window !== "undefined" ? window : this);
