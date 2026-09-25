/* AgentDiff blocks — what to change, and what it would be worth.
 *
 * The dashboard next door says what is wrong. This one is the other half
 * a reader came for: what to do about it, ranked by what the change would
 * be worth, in the engine's own words.
 *
 * `aggregate.recommendations` has already done the work — it has the
 * finding, the tasks it rests on, and an expected gain stated as a range
 * rather than a promise ("up to +31pt success (5/16 tasks), −1,187 wasted
 * tokens"). Paraphrasing that would only lose the denominator, so every
 * line here is quoted and the field it came from is named underneath.
 *
 * The suggested prompt is folded away rather than shown. A rewritten
 * instruction is a thing a reader should choose to open and then read
 * carefully; putting it in the flow invites copying it without reading,
 * which is how an evaluation's advice becomes an agent's next problem.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-actions-dash-css";

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".ad{display:flex;flex-direction:column;gap:14px}",
      ".ad-lede{font-size:var(--fs-m);line-height:1.6;color:var(--ink);margin:0}",
      ".ad-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);",
      "font-weight:700;margin:0 0 8px}",
      ".ad-list{list-style:none;margin:0;padding:0}",
      ".ad-item{padding:10px 0 10px 12px;border-bottom:1px solid var(--rule);position:relative}",
      ".ad-item:before{content:'';position:absolute;left:0;top:12px;bottom:12px;width:2px;",
      "border-radius:2px;background:var(--rule)}",
      ".ad-item[data-sev='critical']:before{background:var(--bad)}",
      ".ad-item[data-sev='major']:before{background:var(--warn)}",
      ".ad-say{font-size:var(--fs-s);line-height:1.55;color:var(--ink);margin:0}",
      ".ad-say .sev{font-weight:700}",
      ".ad-say .sev.critical{color:var(--bad)}.ad-say .sev.major{color:var(--warn)}",
      ".ad-say .sev.minor{color:var(--ink-3)}",
      ".ad-gain{display:block;margin-top:4px;font-size:var(--fs-s);color:var(--ink)}",
      ".ad-gain b{color:var(--good)}",
      ".ad-prov{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:4px 0 0}",
      ".ad-prov code{font-family:ui-monospace,monospace}",
      ".ad-fold{margin-top:5px;font-size:var(--fs-xs)}",
      ".ad-fold summary{cursor:pointer;color:var(--ink-2)}",
      ".ad-fold pre{white-space:pre-wrap;margin:5px 0 0;padding:7px 9px;border:1px solid var(--rule);",
      "border-radius:8px;background:var(--surface-2);font-size:var(--fs-xs);line-height:1.5;color:var(--ink-2)}",
      ".ad-note{font-size:var(--fs-s);color:var(--ink-2);line-height:1.6;margin:0}",
    ].join(""));
  }

  var ORDER = { critical: 0, major: 1, minor: 2 };

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var agg = ctx.aggregate || {};
    var recs = (agg.recommendations || []).slice();
    if (!recs.length) {
      return ctx.empty(el, "Nothing to change: no recommendation survived the evidence this page has.");
    }
    recs.sort(function (a, b) {
      return (ORDER[a.severity] === undefined ? 3 : ORDER[a.severity]) -
             (ORDER[b.severity] === undefined ? 3 : ORDER[b.severity]);
    });

    var root = H("div", { class: "ad" });
    el.appendChild(root);

    var top = recs[0];
    var lede = H("p", { class: "ad-lede" });
    lede.appendChild(H("span", { text: recs.length + " change(s) worth making, worst first. Start with: " }));
    lede.appendChild(H("b", { text: top.finding }));
    root.appendChild(lede);

    var sec = H("section");
    sec.appendChild(H("div", { class: "ad-h", text: "What to change, and what it would be worth" }));
    var list = H("ul", { class: "ad-list" });
    recs.forEach(function (r) {
      var li = H("li", { class: "ad-item", "data-sev": r.severity || "minor",
                         "data-category": r.category || "" });
      var say = H("p", { class: "ad-say" });
      say.appendChild(H("span", { class: "sev " + (r.severity || "minor"),
                                  text: (r.severity || "minor") + " · " }));
      say.appendChild(H("span", { text: r.finding }));
      if (r.expected_gain) {
        var gain = H("span", { class: "ad-gain" });
        gain.appendChild(H("span", { text: "Worth: " }));
        gain.appendChild(H("b", { text: r.expected_gain }));
        say.appendChild(gain);
      }
      li.appendChild(say);

      var prov = H("p", { class: "ad-prov" });
      var tasks = r.evidence_tasks || [];
      prov.appendChild(H("span", { text: (r.agent ? r.agent + " · " : "") +
        (r.category ? String(r.category).replace(/_/g, " ") + " · " : "") +
        (tasks.length ? "evidence from " + tasks.length + " task(s): " +
          tasks.slice(0, 3).join(", ") + (tasks.length > 3 ? ", …" : "") + " · " : "") +
        "quoted from " }));
      prov.appendChild(H("code", { text: "recommendations[].finding" }));
      li.appendChild(prov);

      if (r.suggested_prompt) {
        var fold = H("details", { class: "ad-fold" });
        fold.appendChild(H("summary", { text: "The instruction this suggests — read it before using it" }));
        fold.appendChild(H("pre", { text: r.suggested_prompt }));
        li.appendChild(fold);
      }
      list.appendChild(li);
    });
    sec.appendChild(list);
    root.appendChild(sec);

    var eff = agg.efficiency && agg.efficiency.narrative;
    if (eff) {
      var note = H("p", { class: "ad-note", "data-role": "efficiency" });
      note.appendChild(H("b", { text: "And what is simply wasted: " }));
      note.appendChild(H("span", { text: eff }));
      root.appendChild(note);
    }

    root.appendChild(H("p", { class: "ad-note", "data-role": "caveat", text:
      "Every gain above is a ceiling, not a forecast: it is what this batch would have saved had the change " +
      "been in place, over the tasks named beside it. A change tested on the runs that suggested it is not " +
      "yet a change that works." }));
  }

  AgentDiff.block({
    id: "what-to-do",
    storyTitle: "What to change",
    title: "What to change",
    question: "What should change about these agents, and what would it be worth?",
    group: "other",
    size: "wide",
    relevance: function (ctx) {
      var recs = ctx.aggregate && ctx.aggregate.recommendations;
      return recs && recs.length ? 0.97 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
