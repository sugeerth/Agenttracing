/* AgentDiff block — Output equality.
 *
 * Do repeated runs say the same thing, and do the two agents? Per task,
 * each agent's runs as a strip of dots grouped by answer (one colour per
 * distinct answer, the majority first, ✓ when it matches the expected
 * answer), the equality rate as a bar, and whether the agents' majority
 * answers agree. The divergence of outputs at a glance, from
 * `aggregate.equality`; every number a count over the runs shown.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".eq-lede{font-size:var(--fs-s);color:var(--ink-3);margin:0 0 8px}",
      ".eq-table{border-collapse:collapse;width:100%;font-size:var(--fs-s)}",
      ".eq-table th{text-align:left;font-family:var(--mono);font-weight:500;font-size:var(--fs-xs);color:var(--ink-3);padding:4px 10px 6px 0;border-bottom:1px solid var(--rule)}",
      ".eq-table td{padding:5px 10px 5px 0;border-top:1px solid var(--rule);vertical-align:middle}",
      ".eq-table td.task{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2)}",
      ".eq-dots{display:inline-flex;gap:3px;align-items:center;vertical-align:middle}",
      ".eq-dots i{width:10px;height:10px;border-radius:50%;display:inline-block;border:1.5px solid transparent}",
      ".eq-dots i.wrong{border-color:var(--bad)}",
      ".eq-rate{display:inline-block;width:70px;height:6px;background:var(--rule);border-radius:3px;vertical-align:middle;margin-left:8px;position:relative}",
      ".eq-rate b{position:absolute;left:0;top:0;height:6px;border-radius:3px;background:var(--accent)}",
      ".eq-num{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);margin-left:6px}",
      ".eq-ans{font-size:var(--fs-xs);color:var(--ink-3);max-width:34ch;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:inline-block;vertical-align:middle;margin-left:6px}",
      ".eq-cross{font-family:var(--mono);font-size:var(--fs-xs)}",
      ".eq-cross.same{color:var(--good)}.eq-cross.diff{color:var(--warn)}",
      ".eq-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:72ch}",
    ].join("");
    document.head.appendChild(node);
  }
  var PALETTE = ["#2f6f9f", "#b5651d", "#3f7d3f", "#7c3aed", "#b03030", "#0f7f7a", "#a97a12", "#6b6a63"];

  AgentDiff.block({
    id: "equality",
    title: "Output equality",
    question: "Do repeated runs give the same answer, and do the two agents?",
    group: "other",
    size: "wide",

    relevance: function (ctx) {
      var eq = ctx.aggregate && ctx.aggregate.equality;
      if (!eq || !eq.tasks || !Object.keys(eq.tasks).length) return 0;
      var any = Object.keys(eq.tasks).some(function (t) { return Object.keys(eq.tasks[t].agents).some(function (a) { return eq.tasks[t].agents[a].runs > 1; }); });
      return any ? 0.78 : 0.3;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h;
      var eq = ctx.aggregate && ctx.aggregate.equality;
      if (!eq) return ctx.empty(el, "This batch carries no equality analysis (it needs repeated runs).");
      var agents = Object.keys(eq.per_agent || {});
      el.appendChild(H("p", { class: "eq-lede", text:
        agents.map(function (a) { var p = eq.per_agent[a]; return a + ": " + Math.round((p.equality_rate || 0) * 100) + "% of runs agree with their task's majority, unanimous on " + p.unanimous_tasks + " of " + p.tasks + " tasks"; }).join(" · ") +
        (eq.cross_agent ? " · the two agents' majorities agree on " + eq.cross_agent.majorities_equal + " of " + eq.cross_agent.tasks_compared + " tasks" : "") }));
      var table = H("table", { class: "eq-table" });
      table.appendChild(H("tr", null, ["task"].concat(agents).concat(["agents agree?"]).map(function (t) { return H("th", { text: t }); })));
      Object.keys(eq.tasks).forEach(function (tid) {
        var row = eq.tasks[tid];
        var tr = H("tr", { "data-task": tid });
        tr.appendChild(H("td", { class: "task", text: tid }));
        agents.forEach(function (a) {
          var e = row.agents[a];
          var td = H("td");
          if (!e) { td.textContent = "—"; tr.appendChild(td); return; }
          var dots = H("span", { class: "eq-dots", title: e.distinct_answers + " distinct answer(s) over " + e.runs + " run(s)" });
          (e.answers || []).forEach(function (grp, gi) {
            for (var k = 0; k < grp.runs; k++) {
              dots.appendChild(H("i", { style: { background: PALETTE[gi % PALETTE.length] }, class: grp.success ? "" : "wrong",
                                        title: grp.answer + (grp.success ? " · solved" : " · failed") }));
            }
          });
          td.appendChild(dots);
          td.appendChild(H("span", { class: "eq-rate", title: "equality rate " + Math.round(e.equality_rate * 100) + "%" }, [H("b", { style: { width: Math.round(e.equality_rate * 100) + "%" } })]));
          td.appendChild(H("span", { class: "eq-num", text: e.distinct_answers + " distinct" + (e.majority_matches_expected === true ? " · majority ✓" : e.majority_matches_expected === false ? " · majority ✗" : "") }));
          td.appendChild(H("span", { class: "eq-ans", title: String(e.majority_answer || ""), text: String(e.majority_answer || "") }));
          tr.appendChild(td);
        });
        var cross = row.cross_agent;
        tr.appendChild(H("td", null, [H("span", { class: "eq-cross " + (cross ? (cross.majorities_equal ? "same" : "diff") : ""), text: cross ? (cross.majorities_equal ? "same answer" : "different answers") : "—" })]));
        table.appendChild(tr);
      });
      el.appendChild(H("div", { class: "scroll-x" }, [table]));
      el.appendChild(H("p", { class: "eq-note", text: "equal = " + String(eq.normalisation || "") + ". " + String(eq.note || "") }));
    },
  });

})(typeof window !== "undefined" ? window : this);
