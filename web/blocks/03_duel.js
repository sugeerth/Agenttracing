/* AgentDiff blocks — two vendor agents on the same task: the fair reading.
 *
 * `aggregate.duel` is what `agentdiff duel` wrote after running Codex CLI
 * and Claude Code on the same task, each in its own copy of the same
 * workspace, launched together and graded by the operator's check. The
 * block puts it in the order a fair comparison has to be read:
 *
 *   1. **What was not equal**, before any number. A comparison in which
 *      one agent was sandboxed and the other was not, or one could be
 *      stopped at its budget and the other could not, is still worth
 *      reading — but only with that said first. Unequal conditions lead
 *      the ledger; equal ones follow, quieter.
 *   2. **Head to head**: the check's verdict with its interval, tokens
 *      split into cached input, fresh input and output, cost where the
 *      vendor reported one and "not reported" where it did not, wall time,
 *      and passing runs per million tokens. Each row carries two bars on
 *      one scale, so the difference is read before the digits are.
 *   3. **How each one worked** — one bar per agent, its actions divided
 *      into explore, edit, verify, run: the shape of its habits, drawn at
 *      the same length for both so the shares compare. Under it, the two
 *      habits that decide whether a change can be trusted: how much it
 *      looked before its first edit, and whether it ran a check after its
 *      last one. And the one line a reader must not miss: a run that *said*
 *      it was done and failed the check.
 *   4. **What each one made**: the head of each run's patch, side by side.
 *
 * Every figure is quoted from `aggregate.duel`; the block draws.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-duel-css";
  var KINDS = ["explore", "edit", "verify", "run", "research", "plan", "delegate", "other"];
  var KIND_COLOR = { explore: "var(--ink-3)", edit: "var(--warn)", verify: "var(--good)", run: "var(--accent)",
                     research: "var(--b)", plan: "var(--a)", delegate: "var(--bad)", other: "var(--rule-2)" };

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".dl{display:flex;flex-direction:column;gap:16px}",
      ".dl-lede{font-size:var(--fs-m);line-height:1.6;color:var(--ink);margin:0}",
      ".dl-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);font-weight:700;margin:0 0 6px}",
      ".dl-parity{display:flex;flex-wrap:wrap;gap:6px}",
      ".dl-chip{font-size:var(--fs-xs);line-height:20px;padding:0 9px;border-radius:999px;background:var(--surface-2);color:var(--ink-3)}",
      ".dl-chip.no{color:var(--warn);font-weight:700;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--warn) 45%,transparent)}",
      ".dl-chip.yes:before{content:'= ';}.dl-chip.no:before{content:'≠ ';}",
      ".dl-grid{display:grid;grid-template-columns:minmax(140px,max-content) repeat(2,minmax(0,1fr));gap:6px 16px;align-items:center;font-size:var(--fs-s)}",
      ".dl-grid .k{color:var(--ink-2)}",
      ".dl-grid .hd{font-weight:700;display:flex;align-items:center;gap:6px}",
      ".dl-grid .hd i{width:9px;height:9px;border-radius:2px;display:inline-block}",
      ".dl-cell{display:flex;flex-direction:column;gap:3px;min-width:0}",
      ".dl-num{font-variant-numeric:tabular-nums;color:var(--ink)}",
      ".dl-num small{color:var(--ink-3);font-size:var(--fs-xs)}",
      ".dl-bar{height:6px;border-radius:3px;background:var(--rule);position:relative;overflow:hidden}",
      ".dl-bar span{position:absolute;top:0;bottom:0;left:0}",
      ".dl-stack{display:flex;height:14px;border-radius:3px;overflow:hidden;background:var(--rule)}",
      ".dl-stack span{height:100%}",
      ".dl-key{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".dl-key i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}",
      ".dl-flag{font-size:var(--fs-s);color:var(--bad);font-weight:600;margin:0}",
      ".dl-diffs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}",
      "@media (max-width:760px){.dl-diffs{grid-template-columns:minmax(0,1fr)}.dl-grid{grid-template-columns:minmax(96px,max-content) repeat(2,minmax(0,1fr));gap:6px 8px}}",
      ".dl-patch{margin:0;font-family:ui-monospace,monospace;font-size:var(--fs-xs);line-height:1.45;white-space:pre;",
      "overflow:auto;max-height:320px;background:var(--surface-2);border-radius:6px;padding:8px}",
      ".dl-patch .add{color:var(--good)}.dl-patch .del{color:var(--bad)}.dl-patch .hunk{color:var(--ink-3)}",
      ".dl-note{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:0}",
      ".dl-note code{font-family:ui-monospace,monospace}",
      ".dl.compact{gap:10px}.dl.compact .dl-grid{gap:3px 12px;font-size:var(--fs-xs)}",
    ].join(""));
  }

  function duelOf(ctx) {
    var d = ctx.aggregate && ctx.aggregate.duel;
    return d && d.measurable ? d : null;
  }

  function color(i) { return i === 0 ? "var(--a)" : "var(--b)"; }
  function fmtInt(n) { return n == null ? "—" : Math.round(n).toLocaleString("en-US"); }
  function pct(x) { return Math.round((x || 0) * 100) + "%"; }

  function barCell(H, text, value, max, i, sub) {
    var cell = H("div", { class: "dl-cell" });
    var num = H("div", { class: "dl-num" }, [H("span", { text: text })]);
    if (sub) num.appendChild(H("small", { text: " " + sub }));
    cell.appendChild(num);
    if (value != null && max > 0) {
      var bar = H("div", { class: "dl-bar" });
      bar.appendChild(H("span", { style: { width: Math.max(1, value / max * 100) + "%", background: color(i) } }));
      cell.appendChild(bar);
    }
    return cell;
  }

  function stack(H, shares) {
    var s = H("div", { class: "dl-stack", role: "img",
      "aria-label": KINDS.filter(function (k) { return shares[k]; }).map(function (k) { return k + " " + pct(shares[k]); }).join(", ") });
    KINDS.forEach(function (k) {
      if (shares[k]) s.appendChild(H("span", { title: k + " " + pct(shares[k]),
        style: { width: shares[k] * 100 + "%", background: KIND_COLOR[k] } }));
    });
    return s;
  }

  function patchView(H, text) {
    var pre = H("pre", { class: "dl-patch" });
    String(text || "").split("\n").slice(0, 160).forEach(function (line) {
      var cls = /^\+(?!\+\+)/.test(line) ? "add" : /^-(?!--)/.test(line) ? "del" : /^@@/.test(line) ? "hunk" : "";
      pre.appendChild(H("span", { class: cls, text: line + "\n" }));
    });
    return pre;
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var d = duelOf(ctx);
    if (!d) return ctx.empty(el, "No duel on this page: `agentdiff duel` runs Codex CLI and Claude Code on the same task.");
    var agents = d.agents;
    var pa = d.per_agent;
    var compact = ctx.lane === "focus";
    var root = H("div", { class: "dl" + (compact ? " compact" : "") });
    el.appendChild(root);
    if (!compact) root.appendChild(H("p", { class: "dl-lede", text: d.narrative }));

    // 1 — parity: the unequal conditions first
    var par = H("section", { "data-role": "parity" });
    par.appendChild(H("div", { class: "dl-h", text: "What was equal, and what was not" }));
    var chips = H("div", { class: "dl-parity" });
    var rows = (d.parity || []).slice().sort(function (x, y) {
      return (x.equal === false ? 0 : x.equal === true ? 2 : 1) - (y.equal === false ? 0 : y.equal === true ? 2 : 1);
    });
    rows.forEach(function (r) {
      var vals = Object.keys(r.values || {}).map(function (k) { return k + ": " + r.values[k]; }).join(" · ");
      chips.appendChild(H("span", { class: "dl-chip " + (r.equal === false ? "no" : r.equal === true ? "yes" : "na"),
        "data-equal": String(r.equal), title: r.why + (vals ? " — " + vals : ""), text: r.what }));
    });
    par.appendChild(chips);
    root.appendChild(par);

    // 2 — head to head
    var h2h = H("section", { "data-role": "head-to-head" });
    h2h.appendChild(H("div", { class: "dl-h", text: "Head to head" }));
    var g = H("div", { class: "dl-grid" });
    g.appendChild(H("span"));
    agents.forEach(function (a, i) {
      g.appendChild(H("span", { class: "hd" }, [H("i", { style: { background: color(i) } }), H("span", { text: a })]));
    });
    function row(label, fn, maxFn) {
      g.appendChild(H("span", { class: "k", text: label }));
      var max = maxFn ? Math.max.apply(null, agents.map(function (a) { return maxFn(pa[a]) || 0; })) : 0;
      agents.forEach(function (a, i) {
        var got = fn(pa[a]);
        g.appendChild(barCell(H, got[0], maxFn ? maxFn(pa[a]) : null, max, i, got[1]));
      });
    }
    row("passed the check", function (p) {
      return p.graded ? [p.passed + " of " + p.graded, pct(p.ci95[0]) + "–" + pct(p.ci95[1])] : ["no check", ""];
    }, function (p) { return p.graded ? p.passed / p.graded : 0; });
    row("tokens per run", function (p) {
      var m = p.median;
      return [fmtInt(m.tokens), fmtInt(m.cached_input) + " cached · " + fmtInt(m.output) + " out"];
    }, function (p) { return p.median.tokens; });
    row("cost", function (p) {
      return p.cost_usd == null ? ["not reported", ""] : ["$" + p.cost_usd.toFixed(4), (p.cost_basis || []).join(", ")];
    }, function (p) { return p.cost_usd; });
    row("wall time per run", function (p) { return [(p.median.wall_s || 0).toFixed(1) + "s", ""]; },
        function (p) { return p.median.wall_s; });
    row("passing runs per 1M tokens", function (p) {
      return [p.passes_per_million_tokens == null ? "—" : String(p.passes_per_million_tokens), ""];
    }, function (p) { return p.passes_per_million_tokens; });
    row("lines changed per run", function (p) {
      return [String(p.median.lines_changed || 0), (p.median.files_changed || 0) + " file(s)"];
    }, function (p) { return p.median.lines_changed; });
    h2h.appendChild(g);
    var matched = d.budget_matched_pairs, pairs = (d.pairs || []).length;
    h2h.appendChild(H("p", { class: "dl-note", "data-role": "band",
      text: matched + " of " + pairs + " paired run(s) spent within ±" + Math.round(d.band * 100) +
            "% of each other's tokens" + (matched < pairs ? " — where they did not, read the per-token row, not the per-run one." : ".") }));
    root.appendChild(h2h);

    // 3 — how each one worked
    var how = H("section", { "data-role": "habits" });
    how.appendChild(H("div", { class: "dl-h", text: "How each one worked" }));
    var hg = H("div", { class: "dl-grid" });
    agents.forEach(function (a, i) {
      var p = pa[a];
      hg.appendChild(H("span", { class: "hd" }, [H("i", { style: { background: color(i) } }), H("span", { text: a })]));
      hg.appendChild(H("div", { class: "dl-cell", style: { gridColumn: "2 / -1" } }, [stack(H, p.shares || {})]));
    });
    how.appendChild(hg);
    var key = H("div", { class: "dl-key" });
    KINDS.forEach(function (k) {
      if (agents.some(function (a) { return (pa[a].shares || {})[k]; })) {
        key.appendChild(H("span", null, [H("i", { style: { background: KIND_COLOR[k] } }), H("span", { text: k })]));
      }
    });
    how.appendChild(key);
    var hab = H("div", { class: "dl-grid", style: { marginTop: "8px" } });
    hab.appendChild(H("span"));
    agents.forEach(function (a, i) {
      hab.appendChild(H("span", { class: "hd" }, [H("i", { style: { background: color(i) } }), H("span", { text: a })]));
    });
    [["looked before its first edit", function (p) {
        return p.explored_before_first_edit == null ? "never edited" : p.explored_before_first_edit + " explore action(s)"; }],
     ["ran a check after its last edit", function (p) { return p.verified_after_last_edit + " of " + p.edited_runs + " run(s)"; }],
     ["errors it never came back to", function (p) { return String(p.unrecovered_errors); }],
     ["touched the tests", function (p) { return p.touched_tests + " run(s)"; }],
    ].forEach(function (r) {
      hab.appendChild(H("span", { class: "k", text: r[0] }));
      agents.forEach(function (a) { hab.appendChild(H("span", { class: "dl-num", text: r[1](pa[a]) })); });
    });
    how.appendChild(hab);
    agents.forEach(function (a) {
      if (pa[a].claimed_but_failed) {
        how.appendChild(H("p", { class: "dl-flag", "data-role": "claimed", "data-agent": a,
          text: a + " said it was done and failed the check in " + pa[a].claimed_but_failed + " run(s)." }));
      }
    });
    root.appendChild(how);

    // 4 — what each one made
    var runs = d.runs_detail || [];
    var firstTask = (d.tasks || [])[0];
    var shown = agents.map(function (a) {
      return runs.filter(function (r) { return r.agent === a && r.task === firstTask; })[0];
    });
    if (!compact && shown.some(function (r) { return r && r.patch_head; })) {
      var made = H("section", { "data-role": "made" });
      made.appendChild(H("div", { class: "dl-h", text: "What each one made — " + firstTask + ", first run" }));
      var cols = H("div", { class: "dl-diffs" });
      shown.forEach(function (r, i) {
        var col = H("div", { "data-agent": agents[i] });
        col.appendChild(H("div", { class: "hd", style: { fontSize: "var(--fs-s)", marginBottom: "4px" } }, [
          H("i", { style: { background: color(i), width: "9px", height: "9px", borderRadius: "2px", display: "inline-block", marginRight: "6px" } }),
          H("span", { text: agents[i] + (r ? " · +" + r.lines_added + " −" + r.lines_removed + " in " + r.files_changed + " file(s)" +
                      (r.passed === true ? " · passed" : r.passed === false ? " · failed" : "") : "") })]));
        col.appendChild(r && r.patch_head ? patchView(H, r.patch_head) : H("p", { class: "dl-note", text: "changed nothing" }));
        if (r && r.patch_truncated) {
          col.appendChild(H("p", { class: "dl-note" }, [H("span", { text: "the rest is in " }), H("code", { text: r.patch_path })]));
        }
        cols.appendChild(col);
      });
      made.appendChild(cols);
      root.appendChild(made);
    }
    if (compact) return;
    root.appendChild(H("p", { class: "dl-note" }, [H("span", { text: d.caveat + " Quoted from " }),
      H("code", { text: "duel" }), H("span", { text: "." })]));
  }

  AgentDiff.block({
    id: "duel",
    title: "Same task, two vendors",
    question: "Given the same task, workspace and check, how did the two agents do, what did each make, and how did each work?",
    group: "outcome",
    size: "wide",
    lead: true,
    relevance: function (ctx) { return duelOf(ctx) ? 1 : 0; },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
