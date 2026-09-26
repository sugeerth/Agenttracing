/* AgentDiff blocks — what the traces taught, and whether it held.
 *
 * Every other corpus block describes the corpus. `aggregate.lessons` is
 * the engine learning from it: seventeen properties a run can have, each
 * scored by how it travels with the run being wrong, on each half of the
 * tasks separately — and a lesson is one that held on both. Given a
 * ledger from earlier batches, every earlier lesson is re-tested here.
 *
 * The block's job is to make that test visible, not to restate it. Each
 * lesson is one sentence quoted from the engine (`lessons[].sentence`)
 * beside a two-bar glyph: the difference on the first half of the tasks
 * and on the second, from the same centre line. A lesson that held has
 * two bars on the same side; one that did not has one bar, or two that
 * disagree — the replication is something a reader *sees*, not a badge
 * they are asked to believe. When a ledger is in play, a row of dots
 * follows: where it was learned, then every later corpus it was tested
 * on, filled when it held again.
 *
 * Lessons that read an annotation someone wrote on the trace ("a step
 * annotated bad") are listed apart and never lead: an annotator who knew
 * which runs were wrong separates them perfectly, and that is their
 * knowledge, not a lesson about agents.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-lessons-css";

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".ls{display:flex;flex-direction:column;gap:14px}",
      ".ls-lede{font-size:var(--fs-m);line-height:1.6;color:var(--ink);margin:0}",
      ".ls-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);",
      "font-weight:700;margin:0 0 6px}",
      ".ls-list{list-style:none;margin:0;padding:0}",
      ".ls-row{display:grid;grid-template-columns:88px minmax(0,1fr);gap:4px 14px;align-items:start;",
      "padding:10px 0;border-bottom:1px solid var(--rule)}",
      ".ls-row:last-child{border-bottom:0}",
      ".ls-glyph{display:grid;grid-template-columns:10px 76px;gap:3px 2px;align-items:center;padding-top:5px}",
      ".ls-track{position:relative;height:7px;width:76px}",
      ".ls-track:before{content:'';position:absolute;left:37px;top:-2px;bottom:-2px;width:1px;background:var(--rule-2)}",
      ".ls-bar{position:absolute;top:0;height:7px;border-radius:2px;background:var(--ink-3)}",
      ".ls-bar.more{background:var(--bad)}.ls-bar.less{background:var(--good)}",
      ".ls-bar.none{background:transparent;border:1px dashed var(--rule-2);height:5px;left:30px;width:14px}",
      ".ls-gl{font-size:11px;font-weight:600;color:var(--ink-3);line-height:1;font-variant-numeric:tabular-nums}",
      ".ls-say{font-size:var(--fs-s);line-height:1.55;color:var(--ink);margin:0}",
      ".ls-st{display:inline-block;margin-right:7px;padding:0 7px;border-radius:999px;font-size:var(--fs-xs);",
      "font-weight:700;line-height:18px;background:var(--surface-2);color:var(--ink-2);vertical-align:1px}",
      ".ls-st.held{color:var(--good)}.ls-st.reversed{color:var(--bad)}",
      ".ls-st.did_not_hold,.ls-st.untested{color:var(--warn)}",
      ".ls-meta{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:3px 0 0}",
      ".ls-meta code{font-family:ui-monospace,monospace}",
      ".ls-dots{display:inline-flex;gap:3px;vertical-align:-1px;margin-right:6px}",
      ".ls-dot{width:8px;height:8px;border-radius:50%;border:1.5px solid var(--ink-3);box-sizing:border-box}",
      ".ls-dot.learned{background:var(--ink-3)}",
      ".ls-dot.held_again{background:var(--good);border-color:var(--good)}",
      ".ls-dot.reversed{border-color:var(--bad);background:var(--bad)}",
      ".ls-dot.weakened{border-color:var(--warn)}",
      ".ls-row.annotation .ls-say{color:var(--ink-2)}",
      ".ls-again-row{font-size:var(--fs-s);line-height:1.5;color:var(--ink-2);padding:4px 0}",
      ".ls-note{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.6;margin:0}",
      ".ls-note b{color:var(--ink-2)}",
      ".ls-how{font-size:var(--fs-xs);color:var(--ink-2);line-height:1.6;margin:0;",
      "border-left:2px solid var(--rule-2);padding-left:10px}",
    ].join(""));
  }

  var STATUS = {
    held: "held on both halves",
    did_not_hold: "did not hold",
    reversed: "reversed",
    untested: "too few to test",
  };
  var AGAIN = {
    learned: "learned here",
    held_again: "held again",
    weakened: "weakened",
    reversed: "reversed",
    untestable: "untestable",
  };

  function lessonsOf(ctx) {
    var ls = ctx.aggregate && ctx.aggregate.lessons;
    return ls && (ls.measurable || (ls.ledger && (ls.ledger.rechecked || []).length)) ? ls : null;
  }

  /* one half's difference as a bar from the centre: right is "more often
     wrong", left is "less often"; a half too small to read is a dashed
     stub, so a missing test never looks like a small effect */
  function track(H, half) {
    var t = H("div", { class: "ls-track" });
    if (!half || !half.measurable) {
      t.appendChild(H("span", { class: "ls-bar none" }));
      return t;
    }
    var e = Math.max(-1, Math.min(1, half.effect || 0));
    var w = Math.max(2, Math.round(Math.abs(e) * 37));
    t.appendChild(H("span", {
      class: "ls-bar " + (e > 0 ? "more" : e < 0 ? "less" : ""),
      style: { left: (e >= 0 ? 38 : 37 - w) + "px", width: w + "px" },
    }));
    return t;
  }

  function row(H, lesson, corpus) {
    var li = H("li", { class: "ls-row " + lesson.source, "data-lesson": lesson.name,
                       "data-status": lesson.status });
    var halves = lesson.halves || [];
    var glyph = H("div", { class: "ls-glyph", role: "img",
      "aria-label": "first half " + (halves[0] && halves[0].measurable ? Math.round(halves[0].effect * 100) + " points" : "too few") +
                    ", second half " + (halves[1] && halves[1].measurable ? Math.round(halves[1].effect * 100) + " points" : "too few"),
      title: "Each bar is the difference on one half of the tasks: right of the line, runs with it were wrong more often; left, less often." });
    [0, 1].forEach(function (i) {
      glyph.appendChild(H("span", { class: "ls-gl", text: String(i + 1) }));
      glyph.appendChild(track(H, halves[i]));
    });
    li.appendChild(glyph);

    var body = H("div");
    var say = H("p", { class: "ls-say" });
    say.appendChild(H("span", { class: "ls-st " + lesson.status, text: STATUS[lesson.status] || lesson.status }));
    say.appendChild(H("span", { text: lesson.sentence || lesson.phrasing }));
    body.appendChild(say);

    var meta = H("p", { class: "ls-meta" });
    var led = lesson.ledger;
    if (led && (led.history || []).length) {
      var dots = H("span", { class: "ls-dots", "data-role": "history" });
      led.history.forEach(function (h) {
        dots.appendChild(H("span", { class: "ls-dot " + h.status,
          title: (AGAIN[h.status] || h.status) + " · corpus " + h.corpus + " · " + h.runs + " runs" }));
      });
      meta.appendChild(dots);
      var here = corpus && led.learned_from === corpus;
      meta.appendChild(H("span", { text: here
        ? "first learned from this corpus · "
        : "learned on an earlier corpus; held again on " + led.held_again + " of " + led.tested_on +
          " tested since · " }));
    }
    meta.appendChild(H("span", { text: lesson.source }));
    body.appendChild(meta);
    li.appendChild(body);
    return li;
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var ls = lessonsOf(ctx);
    if (!ls) {
      var raw = ctx.aggregate && ctx.aggregate.lessons;
      return ctx.empty(el, raw && raw.reason
        ? "Nothing learned: " + raw.reason + "."
        : "Nothing learned: this page carries no corpus. `agentdiff batch` learns from one.");
    }
    var root = H("div", { class: "ls" });
    el.appendChild(root);
    root.appendChild(H("p", { class: "ls-lede", text: ls.narrative || "" }));

    var all = ls.lessons || [];
    var observed = all.filter(function (l) { return l.source !== "annotation"; });
    var noted = all.filter(function (l) { return l.source === "annotation"; });

    if (observed.length) {
      var sec = H("section", { "data-role": "observed" });
      sec.appendChild(H("div", { class: "ls-h", text: "What the runs did" }));
      var list = H("ul", { class: "ls-list" });
      observed.forEach(function (l) { list.appendChild(row(H, l, ls.corpus)); });
      sec.appendChild(list);
      root.appendChild(sec);
    }
    if (noted.length) {
      var sec2 = H("section", { "data-role": "annotations" });
      sec2.appendChild(H("div", { class: "ls-h", text: "What someone wrote on the trace — not a behaviour" }));
      var list2 = H("ul", { class: "ls-list" });
      noted.forEach(function (l) { list2.appendChild(row(H, l, ls.corpus)); });
      sec2.appendChild(list2);
      root.appendChild(sec2);
    }

    // earlier lessons this corpus was asked to confirm: one line each, the
    // verdict first, so "held again" and "too few to test" read apart
    var led = ls.ledger || {};
    var again = led.rechecked || [];
    if (again.length) {
      var sec3 = H("section", { "data-role": "rechecked" });
      sec3.appendChild(H("div", { class: "ls-h", text: "Carried in from earlier corpora, re-tested here" }));
      var list3 = H("ul", { class: "ls-list ls-again" });
      again.forEach(function (r) {
        var li = H("li", { class: "ls-again-row", "data-lesson": r.name, "data-status": r.status });
        li.appendChild(H("span", { class: "ls-st " + (r.status === "held_again" ? "held" : r.status === "reversed" ? "reversed" : "untested"),
          text: r.status === "untestable" ? "too few to test" : (AGAIN[r.status] || r.status) }));
        li.appendChild(H("span", { text: (r.phrasing || r.name) + (r.with ? " — " + r.with.wrong + " of " + r.with.runs +
          " wrong with it, " + r.without.wrong + " of " + r.without.runs + " without" : r.why ? " — " + r.why : "") }));
        list3.appendChild(li);
      });
      sec3.appendChild(list3);
      root.appendChild(sec3);
    }

    var how = H("p", { class: "ls-how", "data-role": "method" });
    var halves = ls.halves || [[], []];
    how.appendChild(H("span", { text: "How this learns: " + (ls.tried || 0) + " properties tried; the " +
      ((halves[0] || []).length + (halves[1] || []).length) + " tasks split in two by a hash of their id (" +
      (halves[0] || []).length + " and " + (halves[1] || []).length + "), fixed before anything was scored; " +
      "a lesson must hold on both. Wrong means the golden set's label for " +
      ((ls.target || {}).golden || 0) + " run(s) and the run's own outcome for " + ((ls.target || {}).outcome || 0) + ". " }));
    how.appendChild(H("span", { text: led.had_prior
      ? (led.seen_before
          ? "This corpus is already in the ledger, so reading it again counts for nothing."
          : "The ledger had " + led.prior_corpora + " earlier corpus(es); this one has been added.")
      : "No ledger: `agentdiff batch --lessons FILE` carries these to the next corpus, which confirms or retires them." }));
    root.appendChild(how);
    if (ls.caveat) {
      root.appendChild(H("p", { class: "ls-note", "data-role": "caveat" }, [
        H("span", { text: ls.caveat + " Every sentence above is quoted from " }),
        H("code", { text: "lessons" }),
        H("span", { text: "; the page composes, it does not compute." }),
      ]));
    }
  }

  AgentDiff.block({
    id: "lessons",
    storyTitle: "What the traces taught",
    title: "What the traces taught",
    question: "What does this corpus teach about which runs go wrong — and did it hold on the tasks it was not learned from?",
    group: "signal",
    size: "wide",
    relevance: function (ctx) { return lessonsOf(ctx) ? 0.995 : 0; },
    render: render,
  });

  AgentDiff.lessons = { lessonsOf: lessonsOf };
})(typeof window !== "undefined" ? window : this);
