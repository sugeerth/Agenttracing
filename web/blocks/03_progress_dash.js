/* AgentDiff blocks — is it getting better, and do we believe it.
 *
 * A self-improving agent is a lineage: g0 → g1 → … , each step a change
 * someone made and a claim that it helped. The claim is the easy part.
 * This block is the other half, in the dashboard's order — the verdict
 * first, then every step that produced it, then the reasons to doubt it.
 *
 * `aggregate.evolution` has already written each step's sentence, with
 * its interval and its passes ("traded: P(g1 > g0) 50% [36%, 64%],
 * task-balanced IQM −1.22 → −0.06"), so the lines here are quoted. What
 * this block adds is the *shape*: how many steps improved against how
 * many were gamed, forgot or were accepted on noise, and the flags
 * totalled across the lineage.
 *
 * The rule the rest of the page keeps applies hardest here, because a
 * progress view is the one a reader most wants to believe: **the doubts
 * are not a footnote.** `accepted_on_noise`, `overfit`, `gamed`,
 * `protected` and `over_budget` sit in the same list as the gains, at the
 * same size, because a lineage that improved four times and was gamed
 * twice has not improved four times.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-progress-dash-css";

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".pg{display:flex;flex-direction:column;gap:14px}",
      ".pg-lede{font-size:var(--fs-m);line-height:1.6;color:var(--ink);margin:0}",
      ".pg-stats{font-size:var(--fs-s);line-height:1.6;color:var(--ink-2);margin:0}",
      ".pg-stats b{color:var(--ink);font-variant-numeric:tabular-nums}",
      ".pg-stats .sep{color:var(--ink-3);margin:0 7px}",
      ".pg-stats .good{color:var(--good)}.pg-stats .bad{color:var(--bad)}.pg-stats .warn{color:var(--warn)}",
      ".pg-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);",
      "font-weight:700;margin:0 0 8px}",
      ".pg-list{list-style:none;margin:0;padding:0}",
      ".pg-step{padding:10px 0 10px 12px;border-bottom:1px solid var(--rule);position:relative}",
      ".pg-step:before{content:'';position:absolute;left:0;top:12px;bottom:12px;width:2px;",
      "border-radius:2px;background:var(--rule)}",
      ".pg-step[data-verdict='improved']:before{background:var(--good)}",
      ".pg-step[data-verdict='regressed']:before,.pg-step[data-verdict='gamed']:before,",
      ".pg-step[data-verdict='forgot']:before{background:var(--bad)}",
      ".pg-step[data-verdict='traded']:before{background:var(--warn)}",
      ".pg-say{font-size:var(--fs-s);line-height:1.55;color:var(--ink);margin:0}",
      ".pg-v{font-weight:700;text-transform:lowercase}",
      ".pg-v.improved{color:var(--good)}",
      ".pg-v.regressed,.pg-v.gamed,.pg-v.forgot{color:var(--bad)}",
      ".pg-v.traded{color:var(--warn)}.pg-v.flat{color:var(--ink-3)}",
      ".pg-flag{display:inline-block;margin:4px 4px 0 0;padding:0 7px;border-radius:999px;",
      "border:1px solid var(--rule);background:var(--surface-2);font:600 var(--fs-xs)/18px inherit;",
      "color:var(--warn)}",
      ".pg-prov{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:4px 0 0}",
      ".pg-prov code{font-family:ui-monospace,monospace}",
      ".pg-note{font-size:var(--fs-s);color:var(--ink-2);line-height:1.6;margin:0}",
      ".pg-note b{color:var(--ink)}",
      ".pg-doubt{border-left:2px solid var(--warn);padding-left:10px}",
    ].join(""));
  }

  /* what a flag means, in the words the reader needs rather than the key */
  var FLAGS = {
    overfit: "gained on the tasks that triggered it and not on the rest",
    noisy: "accepted on a difference the interval cannot separate from none",
    protected: "touched a path the lineage declared off limits",
    over_budget: "grew past the size the lineage set itself",
    collapsed: "a task stopped being solved at all",
    axes_disagree: "return and outcome moved in different directions",
  };

  function stat(H, line, label, value, tone) {
    if (line.childNodes.length) line.appendChild(H("span", { class: "sep", text: "·" }));
    line.appendChild(H("b", { class: tone || "", text: String(value) }));
    line.appendChild(H("span", { text: " " + label }));
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var ev = (ctx.aggregate || {}).evolution;
    if (!ev || !ev.measurable || !(ev.steps || []).length) {
      return ctx.empty(el, ev && ev.reason
        ? "No lineage to read: " + ev.reason
        : "No lineage on this page: `agentdiff evolve <lineage>` reads an agent that changes over generations.");
    }
    var root = H("div", { class: "pg" });
    el.appendChild(root);

    var steps = ev.steps || [];
    var tj = ev.trajectory || {};
    var flags = tj.flags || {};
    var gains = tj.improved || 0;
    var doubts = (tj.gamed || 0) + (tj.forgot || 0) + (tj.regressed || 0);

    var lede = H("p", { class: "pg-lede" });
    lede.appendChild(H("span", { text: (ev.family ? ev.family + ": " : "") +
      (ev.generations || []).length + " generation(s) over " + steps.length + " step(s). " }));
    if (gains && !doubts) {
      lede.appendChild(H("b", { text: gains + " improved, and nothing was gamed, forgotten or lost." }));
    } else if (doubts) {
      lede.appendChild(H("b", { text: gains + " improved — and " + doubts +
        " step(s) gamed, forgot or regressed, which is the half a progress view is built to hide." }));
    } else {
      lede.appendChild(H("b", { text: "Nothing here improved on the axis the lineage is scored on." }));
    }
    root.appendChild(lede);

    var stats = H("p", { class: "pg-stats" });
    ["improved", "traded", "flat", "regressed", "gamed", "forgot"].forEach(function (k) {
      if (tj[k]) {
        stat(H, stats, k, tj[k],
             k === "improved" ? "good" : (k === "traded" || k === "flat") ? "warn" : "bad");
      }
    });
    if (tj.accepted_on_noise) {
      stat(H, stats, "accepted on noise", tj.accepted_on_noise + " of " + steps.length, "warn");
    }
    root.appendChild(stats);

    var sec = H("section");
    sec.appendChild(H("div", { class: "pg-h", text: "Every step, in the words of the reading that scored it" }));
    var list = H("ul", { class: "pg-list" });
    steps.forEach(function (st) {
      var verdict = st.verdict || "unmeasurable";
      var li = H("li", { class: "pg-step", "data-verdict": verdict,
                         "data-step": (st.from || "") + "→" + (st.to || "") });
      var say = H("p", { class: "pg-say" });
      say.appendChild(H("span", { class: "pg-v " + verdict, text: verdict + " · " }));
      say.appendChild(H("span", { text: st.reading || ((st.from || "?") + " → " + (st.to || "?")) }));
      li.appendChild(say);
      (st.flags || []).forEach(function (f) {
        li.appendChild(H("span", { class: "pg-flag", text: f + " — " + (FLAGS[f] || "see the step") }));
      });
      var prov = H("p", { class: "pg-prov" });
      prov.appendChild(H("span", { text: (st.mechanism ? String(st.mechanism).replace(/_/g, " ") + " · " : "") +
        "quoted from " }));
      prov.appendChild(H("code", { text: "evolution.steps[].reading" }));
      li.appendChild(prov);
      list.appendChild(li);
    });
    sec.appendChild(list);
    root.appendChild(sec);

    // the doubts, at the same size as the gains
    var keys = Object.keys(flags).filter(function (k) { return flags[k]; });
    if (keys.length) {
      var doubt = H("p", { class: "pg-note pg-doubt", "data-role": "doubts" });
      doubt.appendChild(H("b", { text: "Reasons to hold this loosely: " }));
      doubt.appendChild(H("span", { text: keys.map(function (k) {
        return flags[k] + " step(s) " + (FLAGS[k] || k);
      }).join("; ") + "." }));
      root.appendChild(doubt);
    }

    var rec = ev.recommended;
    if (rec && rec.id) {
      var pick = H("p", { class: "pg-note", "data-role": "recommended" });
      pick.appendChild(H("b", { text: "Ship " + rec.id + (rec.is_last ? " (the latest)" : "") + ". " }));
      pick.appendChild(H("span", { text: rec.why || "" }));
      root.appendChild(pick);
    }
    if (ev.advisory) {
      root.appendChild(H("p", { class: "pg-note", "data-role": "advisory", text: ev.advisory }));
    }
  }

  AgentDiff.block({
    id: "getting-better",
    storyTitle: "Is it getting better",
    title: "Is it getting better",
    question: "Did this agent improve across its generations, and how much of that should we believe?",
    group: "evolution",
    size: "wide",
    relevance: function (ctx) {
      var ev = ctx.aggregate && ctx.aggregate.evolution;
      return ev && ev.measurable && (ev.steps || []).length ? 0.98 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
