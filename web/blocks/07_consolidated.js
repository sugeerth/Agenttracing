/* AgentDiff blocks — cross-run diagnosis consolidation.
 *
 * One block over `aggregate.diagnosis_consolidated`, the engine's answer to
 * the question a single pair report cannot ask: does the diagnosis survive
 * repetition? A pair diagnosis is n=1 by construction; when the corpus holds
 * repeated runs, `deepcompare runs` diagnoses every failing run and asks
 * whether the same hypothesis leads each time — and it executes the
 * discriminating checks that are answerable offline (grader consistency,
 * environment reproduction, harness flake rate).
 *
 * The honesty rules match the engine's own: failure reproduction is always
 * shown with its denominator ("fails 2 of 3" is a flake, not a systematic
 * fault); the consolidated statement is quoted verbatim, never paraphrased;
 * a refuted hypothesis is struck through but stays on the page; and an
 * inconclusive check is a result — it stays visible rather than being
 * filtered down to only the checks that found something. Statuses follow
 * the vocabulary the engine enforces: scores can make a hypothesis leading,
 * but only an executed check can make it confirmed or refuted.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  // ------------------------------------------------------------------ style

  var STYLE_ID = "agentdiff-consolidated-css";
  var styled = false;

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".cx-lede{font-size:var(--fs-s);color:var(--ink-2);margin:0 0 10px;line-height:1.55}",
        ".cx-narrative{border:1px solid var(--rule);border-left:3px solid var(--accent);",
        "border-radius:7px;background:var(--surface-2);padding:8px 10px;margin:0 0 10px;",
        "font-size:var(--fs-m);line-height:1.55;color:var(--ink)}",
        ".cx-narrative .k{display:block;font-size:var(--fs-xs);text-transform:uppercase;",
        "letter-spacing:.08em;color:var(--ink-3);font-weight:700;margin-bottom:3px}",
        ".cx-list{list-style:none;margin:0;padding:0}",
        ".cx-row{border-top:1px solid var(--rule)}",
        ".cx-row:first-child{border-top:none}",
        ".cx-head{display:flex;width:100%;gap:8px;align-items:baseline;background:none;",
        "border:0;padding:8px 0;cursor:pointer;text-align:left;font:inherit;color:inherit}",
        ".cx-head:hover .cx-statement{color:var(--accent)}",
        ".cx-main{flex:1;min-width:0}",
        ".cx-who{display:flex;flex-wrap:wrap;gap:6px;align-items:baseline;margin-bottom:2px}",
        ".cx-task{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3)}",
        ".cx-agent{font-size:var(--fs-xs);font-weight:600;color:var(--ink)}",
        ".cx-kn{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2);",
        "font-variant-numeric:tabular-nums}",
        ".cx-statement{display:block;font-size:var(--fs-s);line-height:1.5;color:var(--ink)}",
        // The status register: confirmed is the strongest accent (an executed
        // check settled it), reproducible strong, the shaky statuses warn-muted,
        // refuted struck through — visible, never deleted.
        ".tag.cx-confirmed{color:var(--accent);font-weight:700;",
        "background:color-mix(in srgb, var(--accent) 12%, transparent);",
        "border-color:var(--accent)}",
        ".tag.cx-reproducible{color:var(--accent);font-weight:700;",
        "border-color:color-mix(in srgb, var(--accent) 55%, transparent)}",
        ".tag.cx-shaky{color:var(--warn);",
        "border-color:color-mix(in srgb, var(--warn) 45%, transparent)}",
        ".tag.cx-refuted{color:var(--accent);text-decoration:line-through;",
        "border-color:color-mix(in srgb, var(--accent) 55%, transparent)}",
        ".cx-row.struck .cx-statement{text-decoration:line-through;color:var(--ink-3)}",
        ".cx-caret{flex:none;color:var(--ink-3);font-size:var(--fs-xs);",
        "transition:transform .12s ease}",
        ".cx-row.open .cx-caret{transform:rotate(90deg)}",
        ".cx-body{display:none;padding:0 0 10px 8px}",
        ".cx-row.open .cx-body{display:block}",
        ".cx-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.06em;",
        "color:var(--ink-3);font-weight:700;margin:6px 0 3px}",
        ".cx-checks{list-style:none;margin:0;padding:0}",
        ".cx-checks li{font-size:var(--fs-xs);line-height:1.55;color:var(--ink-2);",
        "padding:3px 0;word-break:break-word}",
        ".cx-check-name{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink)}",
        ".cx-detail{display:block;margin-top:1px}",
        ".cx-runs{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3)}",
        ".cx-basisline{font-size:var(--fs-xs);color:var(--ink-3);font-style:italic;margin:4px 0 0}",
        ".cx-perrun{font-size:var(--fs-xs);line-height:1.55;color:var(--ink-2);margin:0;",
        "padding:0;list-style:none}",
        ".cx-perrun .r{font-family:var(--mono);font-size:var(--fs-xs)}",
        ".cx-foot{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;",
        "border-top:1px solid var(--rule);margin-top:10px;padding-top:7px}",
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is a nicety; the list still reads */ }
  }

  // ------------------------------------------------------------------ utils

  function obj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : null; }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function str(v) { return typeof v === "string" && v ? v : ""; }

  /* The consolidation lives in the aggregate when the page was rendered by
   * `deepcompare runs`, and on the raw payload when a test page was
   * assembled by hand. `batch` and `compare` never write it, so absence is
   * the normal case and means the block hides. */
  function payloadOf(ctx) {
    var fromAggregate = obj(ctx && ctx.aggregate && ctx.aggregate.diagnosis_consolidated);
    if (fromAggregate) return fromAggregate;
    var raw = obj(global.DEEPCOMPARE_DATA);
    return raw ? obj(raw.diagnosis_consolidated) : null;
  }

  function entriesOf(payload) {
    return arr(payload && payload.per_task_agent).filter(obj);
  }

  function failingEntries(payload) {
    return entriesOf(payload).filter(function (e) { return (e.failures || 0) > 0; });
  }

  /* Which visual register each consolidated status gets. The wording stays
   * the data's own (underscores aside); only the styling is ours. */
  var STATUS = {
    confirmed:     { tag: "cx-confirmed",    row: "" },
    reproducible:  { tag: "cx-reproducible", row: "" },
    refuted:       { tag: "cx-refuted",      row: "struck" },
    unstable:      { tag: "cx-shaky",        row: "" },
    single_run:    { tag: "cx-shaky",        row: "" },
    all_contested: { tag: "cx-shaky",        row: "" },
  };

  /* Failure reproduction verdicts, from the engine's own vocabulary:
   * failing every run is a solid (bad) finding; failing some runs is a
   * flake and gets the caution color; a single run cannot say either. */
  var REPRO_TAG = {
    "reproducible": "bad",
    "flaky": "warn",
    "single run": "",
    "no failures": "good",
  };

  var OUTCOME_TAG = {
    confirms: "cx-confirmed",
    refutes: "bad",
    inconclusive: "",           // visible, just not loud — it is still a result
  };

  //: open/closed per (task, agent) row, surviving re-renders in the session.
  var openRows = {};

  function tag(ctx, cls, text) {
    return ctx.h("span", { class: cls ? "tag " + cls : "tag", text: text });
  }

  // ------------------------------------------------------------------- rows

  function checkLine(ctx, check) {
    var kids = [
      ctx.h("span", { class: "cx-check-name", text: str(check.check) || "check" }),
      " ",
      tag(ctx, OUTCOME_TAG[str(check.outcome)] || "", str(check.outcome) || "?"),
    ];
    if (str(check.hypothesis_kind)) {
      kids.push(" ");
      kids.push(ctx.h("span", { class: "cx-runs", text: "→ " + check.hypothesis_kind }));
    }
    // The detail is the executed check's own sentence — verbatim, because a
    // paraphrased measurement is no longer a measurement.
    kids.push(ctx.h("span", { class: "cx-detail", text: str(check.detail) }));
    var runs = arr(check.runs);
    if (runs.length) {
      kids.push(ctx.h("span", {
        class: "cx-runs",
        text: (str(check.basis) ? check.basis + " · " : "") + runs.join(", "),
      }));
    }
    return ctx.h("li", null, kids);
  }

  function entryRow(ctx, entry) {
    var consolidated = obj(entry.consolidated) || {};
    var status = str(consolidated.status);
    var look = STATUS[status] || { tag: "cx-shaky", row: "" };
    var repro = obj(entry.failure_reproduction) || {};
    var rowKey = str(entry.task) + ":" + str(entry.agent);
    var open = !!openRows[rowKey];

    var row = ctx.h("li", {
      class: "cx-row" + (look.row ? " " + look.row : "") + (open ? " open" : ""),
    });

    var main = ctx.h("div", { class: "cx-main" }, [
      ctx.h("div", { class: "cx-who" }, [
        ctx.h("span", { class: "cx-task", text: str(entry.task) }),
        ctx.h("span", { class: "cx-agent", text: str(entry.agent) }),
        ctx.h("span", {
          class: "cx-kn",
          text: "fails " + entry.failures + " of " + entry.runs + " runs",
          title: "failure reproduction: k of n, denominator always stated",
        }),
        str(repro.verdict)
          ? tag(ctx, REPRO_TAG[repro.verdict] !== undefined
              ? REPRO_TAG[repro.verdict] : "", repro.verdict)
          : null,
      ]),
      // The consolidated statement, in the engine's exact words.
      ctx.h("span", { class: "cx-statement", text: str(consolidated.statement) }),
    ]);

    var checks = arr(entry.checks_run).filter(obj);
    var head = ctx.h("button", {
      class: "cx-head", type: "button",
      "aria-expanded": open ? "true" : "false",
      title: open ? "Collapse this entry"
                  : "Expand: the executed checks and the per-run diagnoses",
    }, [
      status ? ctx.h("span", null, [
        (function () {
          var badge = tag(ctx, look.tag, status.replace(/_/g, " "));
          badge.classList.add("cx-badge");
          return badge;
        })(),
      ]) : null,
      main,
      ctx.h("span", { class: "cx-caret", text: "▸" }),
    ]);

    var body = ctx.h("div", { class: "cx-body" });

    var perRun = arr(entry.per_run).filter(obj);
    if (perRun.length) {
      body.appendChild(ctx.h("div", { class: "cx-h", text: "Per-run diagnoses" }));
      body.appendChild(ctx.h("ul", { class: "cx-perrun" }, perRun.map(function (r) {
        return ctx.h("li", null, [
          ctx.h("span", { class: "r", text: str(r.run) }),
          " — ",
          r.leading === null || r.leading === undefined
            ? ctx.h("span", { text: str(r.note) || "contested" })
            : ctx.h("span", { text: String(r.leading) +
                (typeof r.margin === "number" && isFinite(r.margin)
                  ? " (margin " + ctx.fmt.num(r.margin, 2) + ")" : "") }),
        ]);
      })));
    }

    body.appendChild(ctx.h("div", {
      class: "cx-h",
      text: "Executed checks (" + checks.length + ")",
    }));
    if (checks.length) {
      // Every check that ran is listed, whatever it found: an inconclusive
      // check is the corpus saying it cannot settle the question, and hiding
      // that would overstate what the confirming checks mean.
      body.appendChild(ctx.h("ul", { class: "cx-checks" }, checks.map(function (check) {
        return checkLine(ctx, check);
      })));
    } else {
      body.appendChild(ctx.h("div", {
        class: "cx-basisline",
        text: "No discriminating check was answerable from the runs on disk.",
      }));
    }

    if (str(consolidated.basis)) {
      body.appendChild(ctx.h("p", {
        class: "cx-basisline",
        text: "Basis: " + consolidated.basis,
      }));
    }

    head.addEventListener("click", function () {
      openRows[rowKey] = !openRows[rowKey];
      row.classList.toggle("open", !!openRows[rowKey]);
      head.setAttribute("aria-expanded", openRows[rowKey] ? "true" : "false");
      head.setAttribute("title", openRows[rowKey]
        ? "Collapse this entry"
        : "Expand: the executed checks and the per-run diagnoses");
      ctx.signal("inspect");
    });

    row.appendChild(head);
    row.appendChild(body);
    return row;
  }

  // ============================================================ the block

  AgentDiff.block({
    id: "diagnosis-consolidated",
    title: "Across runs",
    question: "Does the diagnosis survive repetition — and what did executed checks settle?",
    group: "outcome",
    size: "normal",

    relevance: function (ctx) {
      var payload = payloadOf(ctx);
      if (!payload || !entriesOf(payload).length) return 0;
      return failingEntries(payload).length ? 0.9 : 0.3;
    },

    render: function (el, ctx) {
      ensureStyle();
      var payload = payloadOf(ctx);
      if (!payload || !entriesOf(payload).length) {
        return ctx.empty(el, "No cross-run consolidation in this report — it is " +
          "written by `deepcompare runs` over repeated runs of the same tasks.");
      }

      el.appendChild(ctx.h("p", {
        class: "cx-lede",
        text: "Each failing (task, agent) below was diagnosed in every failing " +
              "run, not one representative pair. Scores can make a hypothesis " +
              "leading; only an executed check can make it confirmed or " +
              "refuted. Click a row for the checks that ran — inconclusive " +
              "ones included.",
      }));

      // 1. The summary narrative, in the engine's exact words.
      if (str(payload.narrative)) {
        el.appendChild(ctx.h("div", { class: "cx-narrative" }, [
          ctx.h("span", { class: "k", text: "Across runs" }),
          document.createTextNode(str(payload.narrative)),
        ]));
      }

      // 2. One row per (task, agent) with failures, as the engine ordered them.
      var failing = failingEntries(payload);
      if (failing.length) {
        var list = ctx.h("ol", { class: "cx-list" });
        failing.forEach(function (entry) {
          list.appendChild(entryRow(ctx, entry));
        });
        el.appendChild(list);
      } else {
        el.appendChild(ctx.h("div", {
          class: "empty",
          text: "No failures to diagnose across these runs.",
        }));
      }

      // 3. The clean entries, counted rather than hidden — the denominator
      // for everything above.
      var clean = entriesOf(payload).length - failing.length;
      var summary = obj(payload.summary) || {};
      var footParts = [];
      if (clean > 0) {
        footParts.push(clean + " (task, agent) entr" + (clean === 1 ? "y" : "ies") +
          " had no failures to diagnose");
      }
      if (typeof summary.tasks === "number") {
        footParts.push(summary.tasks + " task(s) in the corpus");
      }
      if (typeof summary.confirmed_by_checks === "number") {
        footParts.push(summary.confirmed_by_checks + " confirmed by executed checks");
      }
      if (footParts.length) {
        el.appendChild(ctx.h("div", { class: "cx-foot", text: footParts.join(" · ") }));
      }
    },
  });

})(typeof window !== "undefined" ? window : this);
