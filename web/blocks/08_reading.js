/* AgentDiff blocks — the reading of one run.
 *
 * The eval reasoning layer, on the page. Each run is understood on its
 * own before it is compared: what happened (phases), what the answer
 * rests on (every typed value with its basis status), why it ended, what
 * it means (findings, each tagged with its evidence class), and what to
 * take forward (located next actions). Everything here is quoted from
 * `report.reading[side]` verbatim — the block composes no sentence of its
 * own — and every step reference is a chip that moves the shared cursor
 * the map, step detail and run lens already follow.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;

  var STYLE_ID = "agentdiff-reading-css";
  var styled = false;
  var Lens = { side: null };  // the side being read; null = decide from data

  function ensureStyle() {
    if (styled) return;
    styled = true;
    try {
      if (document.getElementById(STYLE_ID)) return;
      var node = document.createElement("style");
      node.id = STYLE_ID;
      node.textContent = [
        ".rd-head{display:flex;align-items:center;gap:8px;margin:0 0 8px;flex-wrap:wrap}",
        ".rd-head .grp{display:inline-flex;border:1px solid var(--rule);border-radius:7px;overflow:hidden}",
        ".rd-head .grp button{border:0;background:var(--surface-2);color:var(--ink-2);",
        "font:600 var(--fs-s)/24px inherit;padding:0 10px;cursor:pointer}",
        ".rd-head .grp button[aria-pressed=true]{background:var(--accent);color:#fff}",
        ".rd-status{font-size:var(--fs-s);color:var(--ink-3)}",
        ".rd-sum{font-size:var(--fs-m);line-height:1.55;margin:0 0 10px;color:var(--ink)}",
        ".rd-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);",
        "font-weight:700;margin:12px 0 5px}",
        ".rd-phases{margin:0;padding-left:18px;font-size:var(--fs-s);line-height:1.5}",
        ".rd-table{width:100%;border-collapse:collapse;font-size:var(--fs-s)}",
        ".rd-table th{text-align:left;font-weight:600;color:var(--ink-3);font-size:var(--fs-xs);",
        "padding:3px 6px;border-bottom:1px solid var(--rule)}",
        ".rd-table td{padding:4px 6px;border-bottom:1px solid var(--rule);vertical-align:top}",
        ".rd-chip{display:inline-block;padding:0 7px;border-radius:999px;border:1px solid var(--rule);",
        "font:600 var(--fs-xs)/18px inherit;background:var(--surface-2);color:var(--ink-2)}",
        ".rd-chip.supported{color:var(--good)}.rd-chip.contradicted,.rd-chip.unsupported{color:var(--bad)}",
        ".rd-chip.self_asserted,.rd-chip.stale{color:var(--warn)}",
        ".rd-step{display:inline-block;margin:0 4px 2px 0;padding:0 7px;border-radius:999px;",
        "border:1px solid var(--rule);background:var(--surface-2);font:600 var(--fs-xs)/18px ",
        "ui-monospace,monospace;color:var(--accent);cursor:pointer}",
        ".rd-step:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
        ".rd-list{margin:0;padding-left:0;list-style:none;font-size:var(--fs-s);line-height:1.5}",
        ".rd-list li{padding:4px 0;border-bottom:1px dashed var(--rule)}",
        ".rd-cls{font-size:var(--fs-xs);color:var(--ink-3);margin-right:6px;text-transform:uppercase;",
        "letter-spacing:.05em}",
        ".rd-why{font-size:var(--fs-s);line-height:1.5;margin:0}",
        ".rd-conf{font-size:var(--fs-s);color:var(--ink-3);margin:10px 0 0}",
        ".rd-steps{margin-top:8px}.rd-steps>summary{cursor:pointer;font-size:var(--fs-s);color:var(--ink-3);",
        "list-style:none}.rd-steps>summary::before{content:'▸ '}.rd-steps[open]>summary::before{content:'▾ '}",
        ".rd-steps-body{margin-top:8px}",
        ".rd-more{margin-top:12px}.rd-more>summary{cursor:pointer;font-size:var(--fs-s);color:var(--ink-3);",
        "list-style:none}.rd-more>summary::before{content:'▸ '}.rd-more[open]>summary::before{content:'▾ '}",
        ".rd-validity{border-left:3px solid var(--warn);padding:6px 10px;margin:0 0 10px;",
        "font-size:var(--fs-s);background:var(--surface-2);border-radius:0 7px 7px 0}"
      ].join("");
      document.head.appendChild(node);
    } catch (err) { /* styling is optional */ }
  }

  // ------------------------------------------------------------ data access

  function readingOf(report, side) {
    var box = report && report.reading;
    return box && box[side] ? box[side] : null;
  }

  function failingSide(report) {
    var a = report && report.a && report.a.outcome;
    var b = report && report.b && report.b.outcome;
    if (a && b && a.success === true && b.success === false) return "b";
    if (a && b && b.success === true && a.success === false) return "a";
    return "b";
  }

  function agentName(report, side) {
    var box = report && report[side];
    return (box && box.agent && box.agent.name) || side.toUpperCase();
  }

  function rowFor(report, side, step) {
    var rows = report && Array.isArray(report.alignment) ? report.alignment : [];
    var key = side + "_index";
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i][key] === step) return i;
    }
    return -1;
  }

  function stepChip(ctx, report, side, step) {
    return ctx.h("button", {
      class: "rd-step", type: "button", text: "⌖ " + step,
      title: "show step " + step + " in the map, step detail and run lens",
      "data-step": String(step), "data-side": side,
      onclick: function () {
        var row = rowFor(report, side, step);
        try { ctx.signal("inspect"); } catch (e) { /* optional */ }
        if (row < 0) return;
        try {
          document.dispatchEvent(new CustomEvent("agentdiff:select-step",
            { detail: { row: row, side: side } }));
        } catch (e) { /* no CustomEvent: the chip is inert */ }
      }
    });
  }

  function matchWord(r) {
    if (r.matches_expected === true) return "matches expected";
    if (r.status === "contradicted") return "contradicts expected";
    if (r.matches_expected === false) return "not in expected";
    return "no expected value";
  }

  // ---------------------------------------------------------------- render

  function render(el, ctx) {
    ensureStyle();
    var report = ctx.report;
    if (!report || !report.reading) {
      return ctx.empty(el, "This report carries no reading; re-run compare to get one.");
    }
    var side = Lens.side || failingSide(report);
    var reading = readingOf(report, side);
    if (!reading) return ctx.empty(el, "No reading for this side.");
    var H = ctx.h;

    el.appendChild(H("div", { class: "rd-head" }, [
      H("div", { class: "grp", role: "group", "aria-label": "which run to read" }, ["a", "b"].map(function (s) {
        return H("button", {
          text: agentName(report, s), type: "button",
          "aria-pressed": side === s ? "true" : "false",
          onclick: function () { Lens.side = s; try { ctx.signal("inspect"); } catch (e) { /* optional */ } render(clear(el), ctx); }
        });
      })),
      H("span", { class: "rd-status", text: (reading.outcome && reading.outcome.success === false ? "failed" : "succeeded")
        + " · " + (reading.rests_on || []).length + " typed value(s) in the answer" })
    ]));

    el.appendChild(H("p", { class: "rd-sum", text: reading.summary || "" }));

    var validity = reading.validity;
    if (validity && validity.status && validity.status !== "clean") {
      el.appendChild(H("div", { class: "rd-validity", text:
        "Fix the measurement first: " + (validity.reason || validity.status) }));
    }

    // in the story only the essentials stay open: the summary, what the
    // answer rests on, what to take forward; the two walks fold away
    var inStory = ctx.lane === "story" || !!(el.closest && el.closest(".story-lane"));
    var more = inStory ? H("details", { class: "rd-more" }, [
      H("summary", { text: "What happened, and what it means" })]) : null;
    var target = more || el;
    if (reading.phases && reading.phases.length) {
      target.appendChild(H("div", { class: "rd-h", text: "What happened" }));
      target.appendChild(H("ol", { class: "rd-phases" }, reading.phases.map(function (ph) {
        var steps = ph.steps || [];
        var kids = [H("b", { text: steps.length ? "steps " + steps[0] + "–" + steps[steps.length - 1] : "no steps" }),
                    H("span", { text: ": " + (ph.summary || "") + " " })];
        if (steps.length) kids.push(stepChip(ctx, report, side, steps[0]));
        return H("li", null, kids);
      })));
    }

    var basis = reading.answer_basis || {};
    if (reading.rests_on && reading.rests_on.length) {
      el.appendChild(H("div", { class: "rd-h", text: "The answer rests on (" + (basis.status || "").replace(/_/g, " ") + ")" }));
      var table = H("table", { class: "rd-table" }, [
        H("tr", null, ["value", "status", "first at", "source", "vs expected"].map(function (t) { return H("th", { text: t }); }))
      ]);
      reading.rests_on.forEach(function (r) {
        table.appendChild(H("tr", null, [
          H("td", null, [H("code", { text: String(r.value) })]),
          H("td", null, [H("span", { class: "rd-chip " + (r.status || ""), text: String(r.status || "").replace(/_/g, " ") })]),
          H("td", null, r.first_step === null || r.first_step === undefined
            ? [H("span", { text: "no earlier step" })] : [stepChip(ctx, report, side, r.first_step)]),
          H("td", { text: r.source || "" }),
          H("td", { text: matchWord(r) })
        ]));
      });
      el.appendChild(table);
    }

    var why = reading.why_it_ended;
    if (why) {
      el.appendChild(H("div", { class: "rd-h", text: "Why it ended" }));
      el.appendChild(H("p", { class: "rd-why", text:
        (why.success ? "succeeded" : "failed") + ", "
        + (why.declared ? "termination " + why.termination : "termination not declared")
        + " — " + (why.verdict_basis || "") }));
    }

    if (reading.what_it_means && reading.what_it_means.length) {
      target.appendChild(H("div", { class: "rd-h", text: "What it means" }));
      var order = ["observable", "annotation", "stated"];
      var findings = reading.what_it_means.slice().sort(function (x, y) {
        return order.indexOf(x.evidence_class) - order.indexOf(y.evidence_class);
      });
      target.appendChild(H("ul", { class: "rd-list" }, findings.map(function (f) {
        var kids = [H("span", { class: "rd-cls", text: f.evidence_class || "" }),
                    H("span", { text: f.statement + " " })];
        (f.steps || []).slice(0, 4).forEach(function (st) { kids.push(stepChip(ctx, report, side, st)); });
        return H("li", null, kids);
      })));
    }

    if (reading.take_forward && reading.take_forward.length) {
      el.appendChild(H("div", { class: "rd-h", text: "Take forward" }));
      el.appendChild(H("ul", { class: "rd-list rd-todo" }, reading.take_forward.map(function (t) {
        var kids = [];
        if (t.at_step !== null && t.at_step !== undefined) kids.push(stepChip(ctx, report, side, t.at_step));
        kids.push(H("span", { text: (t.conditional_on_validity ? "(conditional on the measurement) " : "") + t.instead }));
        return H("li", null, kids);
      })));
    }

    var conf = reading.confidence;
    if (conf && conf.level) {
      el.appendChild(H("p", { class: "rd-conf", text: "Confidence: " + conf.level + " — " + (conf.basis || "") }));
    }
    if (more && more.childNodes.length > 1) el.appendChild(more);
    if (inStory && AgentDiff.renderLens) {
      // every step of this run, in full, one click away — the lens, drawn
      // into the reading so the page has one place to read a run
      var steps = H("details", { class: "rd-steps" }, [
        H("summary", { text: "Every step of " + agentName(report, side) + ", in full" })]);
      var body = H("div", { class: "rd-steps-body" });
      steps.appendChild(body);
      var drawn = false;
      steps.addEventListener("toggle", function () {
        if (steps.open && !drawn) { drawn = true; AgentDiff.renderLens(body, ctx, side); }
      });
      el.appendChild(steps);
    }
  }

  function clear(el) { el.innerHTML = ""; return el; }

  AgentDiff.block({
    id: "reading",
    title: "Reading",
    question: "What did this run do, what does its answer rest on, and what should it take forward?",
    group: "outcome",
    size: "tall",
    relevance: function (ctx) {
      var r = ctx.report && ctx.report.reading;
      return r && (r.a || r.b) ? 1 : 0;
    },
    render: render
  });
})(typeof window !== "undefined" ? window : this);
