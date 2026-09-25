/* AgentDiff blocks — the dashboard: what is wrong, and how we know.
 *
 * A dashboard for this page is not a wall of tiles. Every other block
 * here speaks in sentences because the findings *are* sentences — "seen
 * on 13 tasks, causing 3 failures, costing 13,896 extra tokens" says
 * something a number in a box cannot — and a summary view that drops
 * into uppercase labels and big figures loses exactly the part a reader
 * needed.
 *
 * So this is the ordering of a dashboard with the voice of the page: the
 * problems first, ranked by what they cost, each **quoted from the engine
 * that found it**, with where it was seen and the field it came from
 * underneath. The counts run along one line as prose rather than standing
 * in a grid.
 *
 * Two rules it keeps, because they are the difference between a dashboard
 * and a decoration:
 *
 *   1. **It composes no finding of its own.** `issues[].summary` and the
 *      detection narrative are quoted verbatim; the structural problems
 *      it adds are phrased from the value that fired and name the field
 *      (`milestones.stalled_at`, `recovery.errors − recovered`). If the
 *      engine cannot say why, the row says so and names the reason.
 *   2. **No number without its denominator.** "12 of 12", "none of 20" —
 *      a bare count is the figure that makes a dashboard feel
 *      authoritative and be wrong.
 *
 * Reading order is severity, then cost: a policy breach on one run
 * outranks a hygiene note on forty, because the next action differs.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-dashboard-css";

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".db{display:flex;flex-direction:column;gap:16px}",
      ".db-lede{font-size:var(--fs-m);line-height:1.6;color:var(--ink);margin:0}",
      ".db-stats{font-size:var(--fs-s);line-height:1.6;color:var(--ink-2);margin:0}",
      ".db-stats b{color:var(--ink);font-variant-numeric:tabular-nums}",
      ".db-stats .sep{color:var(--ink-3);margin:0 7px}",
      ".db-stats .bad{color:var(--bad)}.db-stats .good{color:var(--good)}",
      ".db-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);",
      "font-weight:700;margin:0 0 8px}",
      ".db-list{list-style:none;margin:0;padding:0}",
      ".db-item{padding:9px 0 9px 12px;border-bottom:1px solid var(--rule);position:relative}",
      ".db-item:before{content:'';position:absolute;left:0;top:11px;bottom:11px;width:2px;",
      "border-radius:2px;background:var(--rule)}",
      ".db-item[data-sev='critical']:before{background:var(--bad)}",
      ".db-item[data-sev='major']:before{background:var(--warn)}",
      ".db-say{font-size:var(--fs-s);line-height:1.55;color:var(--ink);margin:0}",
      ".db-say .sev{font-weight:700}",
      ".db-say .sev.critical{color:var(--bad)}.db-say .sev.major{color:var(--warn)}",
      ".db-say .sev.minor{color:var(--ink-3)}",
      ".db-prov{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:3px 0 0}",
      ".db-prov code{font-family:ui-monospace,monospace;color:var(--ink-3)}",
      ".db-step{font:600 var(--fs-xs)/18px ui-monospace,monospace;color:var(--accent);cursor:pointer;",
      "border:1px solid var(--rule);border-radius:999px;padding:0 7px;background:var(--surface);",
      "margin-left:4px}",
      ".db-step:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
      ".db-more{font-size:var(--fs-xs);color:var(--ink-3);padding:8px 0 0}",
      ".db-note{font-size:var(--fs-s);color:var(--ink-2);line-height:1.6;margin:0}",
      ".db-note b{color:var(--ink)}",
      ".db-judges{display:flex;flex-direction:column;gap:7px}",
      ".db-judge{font-size:var(--fs-s);line-height:1.55;color:var(--ink-2)}",
      ".db-judge .t{font-weight:700;color:var(--ink)}",
      ".db-judge .k{color:var(--ink-3);font-size:var(--fs-xs)}",
    ].join(""));
  }

  /* ---------------------------------------------------------------- data */

  var ORDER = { critical: 0, major: 1, minor: 2 };

  /* The problems the *pair* analysis found, in its own words. `issues`
     has already collapsed the divergences, ranked them and written the
     sentence; repeating that work here would only be a second opinion
     with less evidence behind it. */
  function fromIssues(agg) {
    var issues = (agg.issues && agg.issues.issues) || [];
    return issues.filter(function (i) { return !i.suppressed; }).map(function (i) {
      var ex = i.example || {};
      return {
        sev: i.severity || "minor",
        say: i.summary || i.title || "",
        where: (i.tasks || []).length ? (i.tasks.length + " task(s)") : "",
        step: typeof ex.step === "number" ? ex.step : (typeof ex.index === "number" ? ex.index : null),
        field: "issues[].summary",
        quoted: true,
      };
    });
  }

  /* What the per-run card found that a pair diff cannot see: a milestone
     never reached, a rule broken, an error nobody repaired. Phrased from
     the value that fired, in the register of the sentences above. */
  function fromRuns(card) {
    var out = [];
    (card.per_run || []).forEach(function (r) {
      var who = r.agent + " on " + r.task;
      function add(sev, say, field, step) {
        out.push({ sev: sev, say: say, where: who, step: step == null ? null : step, field: field });
      }
      (r.safety && r.safety.risk_flags || []).forEach(function (f) {
        var step = f.step && typeof f.step === "object" ? f.step.index : f.step;
        add("critical", who + ": " + String(f.kind).replace(/_/g, " ") + " — " + (f.detail || "no detail recorded"),
            "safety.risk_flags[].detail", step);
      });
      if (r.safety && r.safety.policy_compliant === false) {
        add("critical", who + " broke a rule the operator declared.", "safety.policy_compliant", null);
      }
      var rec = r.recovery || {};
      var open = (rec.errors || 0) - (rec.recovered || 0);
      if (open > 0) {
        add("critical", who + ": " + open + " error(s) nothing repaired — " + rec.errors +
            " tool error(s), " + rec.recovered + " returned within the window.",
            "recovery.errors − recovery.recovered", null);
      }
      var ms = r.milestones || {};
      if (ms.measurable && ms.complete === false) {
        add("major", who + " stalled at " + (ms.stalled_at || "an unreached milestone") + " — " +
            ms.reached + " of " + ms.total + " milestone(s) reached" +
            ((ms.missed_ids || []).length ? ", missing " + ms.missed_ids.slice(0, 3).join(", ") : "") + ".",
            "milestones.stalled_at", ms.last_reached_step);
      }
      if (ms.measurable && ms.in_order === false) {
        add("major", who + " reached its milestones out of order — a later one before an earlier one.",
            "milestones.in_order", null);
      }
      var g = r.grounding || {};
      if (g.grounded === false) {
        add("major", who + ": the answer is " + (g.status || "not supported by the run") + " — " +
            g.supported + " of " + g.values + " value(s) trace to something the run observed.",
            "grounding.status", null);
      }
      var tj = r.trajectory || {};
      if (tj.redundant_stretch) {
        add("minor", who + " re-did work it already had: " + tj.redundant_stretch +
            " consecutive step(s) repeating an earlier call and its result.",
            "trajectory.redundant_stretch", (tj.redundant_at || [])[0]);
      }
      if (tj.loop_free === false) {
        add("minor", who + " went in circles — " + (tj.cycles || 0) + " recurring call/result pair(s)" +
            (tj.cycle_share != null ? ", " + Math.round(tj.cycle_share * 100) + "% of its tool steps" : "") + ".",
            "trajectory.loop_free", null);
      }
      if (tj.stopped_when_done === false) {
        add("minor", who + " kept looking after the answer was in hand — " + (tj.steps_after_done || 0) +
            " step(s) past the last evidence it rests on.", "trajectory.stopped_when_done", null);
      }
      if (tj.at_step_limit) {
        add("major", who + " ran out of budget before it finished.", "trajectory.at_step_limit", null);
      }
    });
    return out;
  }

  /* One sentence said about forty runs is one problem with a count, not
     forty lines: a dashboard that makes a reader scroll to reach the
     second kind of problem has hidden it. */
  function fold(rows) {
    var by = {};
    rows.forEach(function (r) {
      var key = r.sev + "|" + r.field + "|" + r.say.replace(/^[^:]*[:]?\s*/, "").slice(0, 60);
      if (!by[key]) by[key] = { sample: r, n: 0 };
      by[key].n += 1;
    });
    return Object.keys(by).map(function (k) { return by[k]; })
      .sort(function (a, b) {
        return (ORDER[a.sample.sev] - ORDER[b.sample.sev]) || (b.n - a.n);
      });
  }

  /* ---------------------------------------------------------------- view */

  function stat(H, line, label, value, tone) {
    if (line.childNodes.length) line.appendChild(H("span", { class: "sep", text: "·" }));
    line.appendChild(H("b", { class: tone || "", text: String(value) }));
    line.appendChild(H("span", { text: " " + label }));
  }

  function judgeLine(H, title, block, extra) {
    var row = H("div", { class: "db-judge" });
    row.appendChild(H("span", { class: "t", text: title + ": " }));
    if (!block || !block.measurable) {
      row.appendChild(H("span", { class: "dim",
        text: (block && block.reason) || "not run on this corpus." }));
      return row;
    }
    row.appendChild(H("span", { text: block.narrative }));
    if (block.only_the_judge && block.only_the_judge.length) {
      row.appendChild(H("span", { text: " Only it caught " +
        block.only_the_judge.map(function (x) { return x.mode; }).join(", ") + "." }));
    }
    if (extra) row.appendChild(H("span", { text: " " + extra }));
    var k = block.kappa;
    if (k) {
      row.appendChild(H("div", { class: "k", text: k.measurable
        ? "Cohen's κ " + k.kappa + " (" + k.reading + ") against what is known of these runs, over " +
          k.n + " of them — raw agreement " + Math.round(k.observed * 100) + "%, which is why κ is the one reported."
        : "κ not measurable here: " + k.reason }));
    }
    return row;
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var agg = ctx.aggregate || {};
    var card = agg.scorecard;
    if (!card || !card.per_run || !card.per_run.length) {
      return ctx.empty(el, "No dashboard: this page was built from a single pair without scored runs.");
    }
    var root = H("div", { class: "db" });
    el.appendChild(root);

    var runs = card.per_run;
    var det = card.detection || {};
    var rows = fromIssues(agg).concat(fromRuns(card));
    var folded = fold(rows);
    var counts = { critical: 0, major: 0, minor: 0 };
    folded.forEach(function (f) { counts[f.sample.sev] = (counts[f.sample.sev] || 0) + 1; });

    // the lede, in the page's voice: what is here, worst first
    var lede = H("p", { class: "db-lede" });
    if (!folded.length) {
      lede.appendChild(H("span", { text:
        "Nothing on this page is wrong: every run is complete, grounded, inside policy, free of " +
        "unrepaired errors and free of loops." }));
    } else {
      var worst = folded[0].sample;
      lede.appendChild(H("span", { text: folded.length + " distinct problem(s) across " + runs.length +
        " run(s)" + (counts.critical ? ", " + counts.critical + " of them critical" : "") + ". The worst: " }));
      lede.appendChild(H("b", { text: worst.say }));
    }
    root.appendChild(lede);

    // the counts as a line of prose, each with what it is out of
    var stats = H("p", { class: "db-stats" });
    stat(H, stats, "runs scored", runs.length);
    var failed = runs.filter(function (r) { return r.success === false; }).length;
    stat(H, stats, "graded a failure", failed + " of " + runs.length);
    if (det.measurable) {
      stat(H, stats, "known failures caught", det.caught + " of " + det.total,
           det.caught === det.total ? "good" : "bad");
      var c = det.controls || {};
      stat(H, stats, "runs known to be right were flagged", (c.flagged || "none") + " of " + c.runs,
           c.flagged ? "bad" : "good");
    }
    root.appendChild(stats);

    // the problems, quoted
    var sec = H("section");
    sec.appendChild(H("div", { class: "db-h", text: "What is wrong, and how we know" }));
    var list = H("ul", { class: "db-list" });
    var SHOWN = 14;
    folded.slice(0, SHOWN).forEach(function (row) {
      var p = row.sample;
      var li = H("li", { class: "db-item", "data-sev": p.sev, "data-field": p.field });
      var say = H("p", { class: "db-say" });
      say.appendChild(H("span", { class: "sev " + p.sev, text: p.sev + " · " }));
      say.appendChild(H("span", { text: p.say }));
      if (p.step != null) {
        var chip = H("button", { class: "db-step", type: "button", text: "step " + p.step });
        chip.addEventListener("click", function () {
          try { AgentDiff.cursor && AgentDiff.cursor.set && AgentDiff.cursor.set(p.step); } catch (e) { /* no cursor */ }
        });
        say.appendChild(chip);
      }
      li.appendChild(say);
      var prov = H("p", { class: "db-prov" });
      prov.appendChild(H("span", { text: (row.n > 1 ? "on " + row.n + " run(s) · " : "") +
        (p.where ? p.where + " · " : "") + (p.quoted ? "quoted from " : "read from ") }));
      prov.appendChild(H("code", { text: p.field }));
      li.appendChild(prov);
      list.appendChild(li);
    });
    sec.appendChild(list);
    if (folded.length > SHOWN) {
      sec.appendChild(H("p", { class: "db-more", text: folded.length - SHOWN +
        " more, each in the block that found it — issues, scorecard, trust." }));
    }
    root.appendChild(sec);

    // what nothing caught: the line a dashboard is most tempted to omit
    if (det.measurable) {
      var missed = Array.from(new Set(det.missed || []));
      var note = H("p", { class: "db-note", "data-role": "missed" });
      note.appendChild(H("b", { text: missed.length
        ? "Nothing on this page catches " + missed.join(", ") + ". "
        : "Every failure this corpus is known to contain is caught by something above. " }));
      note.appendChild(H("span", { text: det.basis + "." }));
      root.appendChild(note);
    }

    // the judges, beside the numbers and never inside them
    var judges = H("section");
    judges.appendChild(H("div", { class: "db-h", text: "What a model makes of it, reported beside the above" }));
    var box = H("div", { class: "db-judges" });
    box.appendChild(judgeLine(H, "A judge reading the answer", det.judge,
      det.judge && det.judge.measurable && det.judge.on_an_excerpt
        ? det.judge.on_an_excerpt + " of those verdicts are about an excerpt of the run, not the whole of it."
        : ""));
    box.appendChild(judgeLine(H, "A judge reading the run", det.agent_judge,
      det.agent_judge && det.agent_judge.measurable
        ? "It reads the trace with tools and judges one requirement at a time."
        : ""));
    judges.appendChild(box);
    judges.appendChild(H("p", { class: "db-note", text:
      "Neither is counted into the numbers above: everything else on this page is reproducible from the " +
      "traces alone, and one sampled verdict would end that without saying so." }));
    root.appendChild(judges);
  }

  AgentDiff.block({
    id: "dashboard",
    storyTitle: "What is wrong",
    title: "Dashboard",
    question: "Is there a problem in these runs, where is it, and how do we know?",
    group: "other",
    size: "wide",
    relevance: function (ctx) {
      var card = ctx.aggregate && ctx.aggregate.scorecard;
      return card && card.per_run && card.per_run.length ? 0.99 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
