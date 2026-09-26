/* AgentDiff blocks — the corpus verdict: the page's opening sentence, for a corpus.
 *
 * The pair verdict card is the best thing on this page — five lines,
 * each quoted from the engine with its source named underneath — and on
 * a page of one pair it is exactly the right opening. On a page of thirty
 * -two runs it is the wrong one: it leads with a single task ("Both solved
 * L01_service_migration") above a corpus whose real news is somewhere
 * else, and a reader who stops after the first sentence leaves with the
 * wrong story.
 *
 * So the batch view of a corpus opens with the same grammar applied to
 * the corpus: who did how well, where the trouble is, what the card can
 * and cannot see, what to change, and how far to trust any of it. Five
 * lines, every one quoted from a field the engine computed, the field
 * named underneath. The block composes the arrangement and nothing else.
 *
 * The confidence line is the one that matters most and the one a corpus
 * dashboard usually leaves out. Sixteen tasks is a small sample: a
 * difference of one task is six points, and two agents whose intervals
 * overlap have not been shown to differ however far apart their point
 * estimates look. This line says so in those words, from the Wilson
 * intervals the scorecard already computed.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-corpus-verdict-css";

  function ensureStyle() {
    // the pair verdict's grammar, so the two read as one voice
    L.style.once(STYLE_ID, [
      ".cv{display:grid;grid-template-columns:max-content 1fr;gap:8px 16px;",
      "font-size:var(--fs-m);line-height:1.5;color:var(--ink)}",
      ".cv .k{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.09em;",
      "font-weight:700;color:var(--ink-3);padding-top:4px}",
      ".cv .v{margin:0}",
      ".cv .v.verdict{font-size:var(--fs-l);font-weight:600;line-height:1.35}",
      ".cv .v.where{border-left:3px solid var(--bad);padding-left:9px}",
      ".cv .v.confidence{color:var(--ink-2);font-size:var(--fs-s)}",
      ".cv-src{display:block;font-size:var(--fs-xs);color:var(--ink-3);margin-top:1px}",
      ".cv-src code{font-family:ui-monospace,monospace}",
      ".cv-agent{white-space:nowrap}",
      ".cv-agent i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;",
      "vertical-align:1px}",
      ".cv b.bad{color:var(--bad)}.cv b.good{color:var(--good)}",
    ].join(""));
  }

  function pct(r) { return Math.round((r || 0) * 100) + "%"; }

  function corpusOf(ctx) {
    var sc = ctx.aggregate && ctx.aggregate.scorecard;
    return sc && (sc.per_run || []).length > 1 ? sc : null;
  }

  function line(H, key, label, kids, source) {
    return [
      H("div", { class: "k", text: label }),
      H("p", { class: "v " + key, role: "listitem", "data-line": key }, kids.concat(
        source ? [H("span", { class: "cv-src" }, [H("span", { text: "from " }), H("code", { text: source })])] : [])),
    ];
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var agg = ctx.aggregate || {};
    var sc = corpusOf(ctx);
    if (!sc) return ctx.empty(el, "Not a corpus: this page carries a single pair.");
    var agents = Object.keys(sc.agents || {}).sort();
    var grid = H("div", { class: "cv", role: "list" });
    var rows = [];

    // 1 — the verdict: each agent, its successes out of its runs
    var verdict = [];
    agents.forEach(function (name, i) {
      var s = (sc.agents[name].rates || {}).success || {};
      var failed = (s.runs || 0) - (s.successes || 0);
      if (i) verdict.push(H("span", { text: "; " }));
      verdict.push(H("span", { class: "cv-agent" }, [
        // the page's own agent colours, so the swatch here is the stroke
        // of that agent's strip in the chart below it
        H("i", { style: { background: i === 0 ? "var(--a)" : i === 1 ? "var(--b)" : "var(--ink-3)" } }),
        H("span", { text: name + " " }),
      ]));
      verdict.push(H("b", { class: failed ? "bad" : "good",
        text: failed ? "failed " + failed + " of " + s.runs : "failed none of " + s.runs }));
    });
    verdict.push(H("span", { text: "." }));
    rows.push(line(H, "verdict", "Verdict", verdict, "scorecard.agents[].rates.success"));

    // 2 — where: the costliest problem the pair analysis found, quoted
    var issues = (agg.issues && agg.issues.issues) || [];
    var worst = issues.filter(function (i) { return !i.suppressed; })[0];
    if (worst) {
      rows.push(line(H, "where", "Where", [H("span", { text: worst.summary })], "issues.issues[0].summary"));
    }

    // 3 — what the card can see: the known failures, and the controls
    var det = sc.detection || {};
    if (det.measurable) {
      var c = det.controls || {};
      rows.push(line(H, "seen", "Seen", [
        H("b", { class: det.caught === det.total ? "good" : "bad", text: det.caught + " of " + det.total }),
        H("span", { text: " known failures caught; " }),
        H("b", { class: c.flagged ? "bad" : "good", text: (c.flagged || "none") + " of " + c.runs }),
        H("span", { text: " runs known to be correct flagged. " + (det.graded_pass || 0) +
          " of the failures were graded a pass — the grade alone would have missed them." }),
      ], "scorecard.detection"));
    }

    // 4 — fix: the first recommendation and what it is worth
    var rec = (agg.recommendations || [])[0];
    if (rec) {
      var fix = [H("span", { text: (rec.agent ? rec.agent + ": " : "") +
        String(rec.category || "").replace(/_/g, " ") }), H("span", { text: " — " })];
      if (rec.expected_gain) fix.push(H("b", { text: rec.expected_gain }));
      fix.push(H("span", { text: ", a ceiling measured on the runs that suggested it." }));
      rows.push(line(H, "fix", "Fix", fix, "recommendations[0]"));
    }

    // 5 — confidence: sample size and whether the intervals overlap
    var sizes = agents.map(function (n) { return ((sc.agents[n].rates || {}).success || {}).runs || 0; });
    var smallest = Math.min.apply(null, sizes);
    var cis = agents.map(function (n) { return ((sc.agents[n].rates || {}).success || {}).ci95 || null; });
    var overlap = null;
    if (cis.length === 2 && cis[0] && cis[1]) {
      overlap = cis[0][0] <= cis[1][1] && cis[1][0] <= cis[0][1];
    }
    var conf = smallest ? Math.round(100 / smallest) + " points per task at " + smallest +
      " task(s) per agent" : "no graded runs";
    var said = "sample: " + conf + ". ";
    if (overlap === true) {
      said += "The two success intervals overlap (" + agents.map(function (n, i) {
        return n + " " + pct(cis[i][0]) + "–" + pct(cis[i][1]); }).join(", ") +
        "), so the difference between the agents is suggestive, not shown.";
    } else if (overlap === false) {
      said += "The two success intervals do not overlap (" + agents.map(function (n, i) {
        return n + " " + pct(cis[i][0]) + "–" + pct(cis[i][1]); }).join(", ") +
        "): on this sample the difference is real.";
    }
    rows.push(line(H, "confidence", "Confidence", [H("span", { text: said })],
                   "scorecard.agents[].rates.success.ci95"));

    rows.forEach(function (pair) { grid.appendChild(pair[0]); grid.appendChild(pair[1]); });
    el.appendChild(grid);
  }

  AgentDiff.block({
    id: "corpus-verdict",
    title: "Verdict",
    question: "How did each agent do across the corpus, where is the trouble, and how far can we trust it?",
    group: "outcome",
    size: "wide",
    lead: true,
    relevance: function (ctx) {
      // the corpus's opening sentence, on the corpus's view
      return ctx.view === "batch" && corpusOf(ctx) ? 1 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
