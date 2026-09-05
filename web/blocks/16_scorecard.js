/* AgentDiff block — Evaluation scorecard.
 *
 * One agent, many dimensions, every one a count or an interval over the
 * runs listed: task success, correct tool called, answer grounded,
 * policy compliant, no risk flag, stopped when done, no loop, no tool
 * error, errors recovered — each as a dot with its 95% Wilson interval
 * on one axis for both agents; spend per run (latency, cost, tokens,
 * steps, tool calls) as bars; risk against reward; the trajectory
 * counts (loops, repeats, steps after done, recovery, terminations);
 * and the judging model's verdicts beside the grade with the 2×2 they
 * agree and disagree on. From `aggregate.scorecard`; offline (a golden
 * set) or online (traces as recorded), and it says which.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".sc{--sc-a:var(--a);--sc-b:var(--b);position:relative}",
      "@media (prefers-color-scheme: dark){:root:not([data-theme=light]) .sc{--sc-a:#3987e5;--sc-b:#d95926}}",
      ":root[data-theme=dark] .sc{--sc-a:#3987e5;--sc-b:#d95926}",
      ".sc-lede{font-size:var(--fs-s);color:var(--ink-2);margin:0 0 8px;max-width:90ch}",
      ".sc-lede .mode{font-family:var(--mono);font-size:var(--fs-xs);padding:1px 7px;border-radius:9px;background:var(--surface-2);color:var(--ink-2);margin-right:6px}",
      ".sc-legend{display:flex;gap:14px;font-size:var(--fs-xs);color:var(--ink-2);margin:0 0 6px;flex-wrap:wrap}",
      ".sc-legend i{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
      ".sc-sec{margin:12px 0 0}.sc-sec h4{margin:0 0 2px;font-size:var(--fs-m);font-weight:600;color:var(--ink)}",
      ".sc-sec .sub{margin:0 0 6px;font-size:var(--fs-xs);color:var(--ink-3);max-width:90ch}",
      ".sc-grid{display:grid;grid-template-columns:minmax(120px,11em) 1fr minmax(120px,auto);gap:4px 12px;align-items:center;font-size:var(--fs-xs)}",
      ".sc-grid .lab{color:var(--ink-2)}.sc-grid .lab.na{color:var(--ink-3)}",
      ".sc-grid .val{font-family:var(--mono);color:var(--ink-3);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".sc-grid .val b{color:var(--ink);font-weight:500}",
      ".sc-grid .axis{grid-column:2;display:flex;justify-content:space-between;font-family:var(--mono);color:var(--ink-3);font-size:10px;padding:0 0 2px}",
      ".sc-strip{position:relative;height:22px;background:var(--surface-2);border-radius:4px}",
      ".sc-strip .tick{position:absolute;top:0;bottom:0;width:1px;background:var(--rule)}",
      ".sc-strip .ci{position:absolute;height:6px;border-radius:3px;opacity:.35}",
      ".sc-strip .pt{position:absolute;width:9px;height:9px;border-radius:50%;border:2px solid var(--surface);box-sizing:content-box;transform:translate(-50%,-50%)}",
      ".sc-strip .hit{position:absolute;inset:0;cursor:default}",
      ".sc-strip.na{background:transparent;border:1px dashed var(--rule);color:var(--ink-3);font-size:10px;line-height:22px;padding-left:8px}",
      ".sc-bar{position:relative;height:22px}",
      ".sc-bar .row{position:absolute;left:0;height:8px;border-radius:0 4px 4px 0}",
      ".sc-bar .row.a{top:2px}.sc-bar .row.b{top:12px}",
      ".sc-rr{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}",
      ".sc-rr svg{display:block;font-family:var(--sans)}",
      ".sc-rr .axis text{font-size:10px;fill:var(--ink-3)}.sc-rr .axis line,.sc-rr .axis path{stroke:var(--rule)}",
      ".sc-rr .lab{font-size:10.5px;fill:var(--ink)}",
      ".sc-chips{display:flex;flex-direction:column;gap:6px;font-size:var(--fs-xs);color:var(--ink-2);min-width:200px}",
      ".sc-chips .chip{display:inline-block;font-family:var(--mono);background:var(--surface-2);border-radius:9px;padding:1px 7px;margin:0 4px 3px 0;color:var(--ink-2)}",
      ".sc-chips .chip.flag{border:1px solid var(--warn)}",
      ".sc-table{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;width:100%}",
      ".sc-table th,.sc-table td{text-align:left;padding:4px 10px 4px 0;border-top:1px solid var(--rule);color:var(--ink-2)}",
      ".sc-table th{font-weight:500;color:var(--ink-3);border-top:0;font-family:var(--mono)}",
      ".sc-table td.num{text-align:right;padding-right:18px}",
      ".sc-judge{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}",
      ".sc-judge .card{border:1px solid var(--rule);border-radius:8px;padding:8px 10px;font-size:var(--fs-xs);color:var(--ink-2)}",
      ".sc-judge .card b{color:var(--ink)}",
      ".sc-judge table{border-collapse:collapse;margin-top:6px;font-variant-numeric:tabular-nums}",
      ".sc-judge td,.sc-judge th{padding:2px 10px 2px 0;font-weight:500;font-family:var(--mono);font-size:10.5px;color:var(--ink-3)}",
      ".sc-judge td.n{color:var(--ink);font-size:var(--fs-s)}",
      ".sc-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);min-width:160px}",
      ".sc-tip b{color:var(--ink);font-variant-numeric:tabular-nums}.sc-tip .row{display:flex;gap:8px;align-items:center;margin-top:2px}.sc-tip .row i{display:inline-block;width:9px;height:9px;border-radius:50%}",
      ".sc-details{margin-top:10px;font-size:var(--fs-xs)}.sc-details summary{cursor:pointer;color:var(--ink-2)}",
      ".sc-note{font-size:var(--fs-xs);color:var(--ink-3);margin-top:8px;max-width:90ch}",
    ].join("");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function pct(v) { return isNum(v) ? Math.round(v * 100) + "%" : "—"; }
  function ci(c) { return c && isNum(c[0]) && isNum(c[1]) ? c[0].toFixed(2) + "–" + c[1].toFixed(2) : "—"; }
  function fmt(v, key) {
    if (!isNum(v)) return "—";
    if (key === "cost_usd") return "$" + v.toFixed(4);
    if (key === "latency_s") return v.toFixed(1) + "s";
    return Math.round(v * 10) / 10 + "";
  }
  function color(agents, a) { return a === agents[0] ? "var(--sc-a)" : "var(--sc-b)"; }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "sc-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, build) {
        tip.innerHTML = ""; build(tip); tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 200 > r.width) x = Math.max(0, evt.clientX - r.left - 210);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }
  function tipRow(tip, col, label, value) {
    var row = document.createElement("div"); row.className = "row";
    if (col) { var i = document.createElement("i"); i.style.background = col; row.appendChild(i); }
    var b = document.createElement("b"); b.textContent = value; row.appendChild(b);
    var s = document.createElement("span"); s.textContent = label; row.appendChild(s);
    tip.appendChild(row);
  }

  // ------------------------------------------------------- rate strips
  function rateRows(H, card, agents, tip) {
    var grid = H("div", { class: "sc-grid", "data-sec": "rates" });
    grid.appendChild(H("span"));
    grid.appendChild(H("div", { class: "axis" }, ["0%", "50%", "100%"].map(function (t) { return H("span", { text: t }); })));
    grid.appendChild(H("span"));
    (card.dimensions.rates || []).forEach(function (dim) {
      var key = dim[0], label = dim[1];
      var rows = agents.map(function (a) { return { agent: a, r: card.agents[a].rates[key] }; });
      var measurable = rows.some(function (x) { return x.r && x.r.runs; });
      grid.appendChild(H("span", { class: "lab" + (measurable ? "" : " na"), text: label }));
      if (!measurable) {
        grid.appendChild(H("div", { class: "sc-strip na", "data-dim": key, text: key === "tool_correct" ? "not measurable — needs a golden set with expected_tools" : key === "policy_compliant" ? "not measurable — needs a policy or forbidden_tools" : key === "recovered_errors" ? "no tool error to recover from" : "not measurable for these runs" }));
        grid.appendChild(H("span", { class: "val", text: "—" }));
        return;
      }
      var strip = H("div", { class: "sc-strip", "data-dim": key });
      [0.25, 0.5, 0.75].forEach(function (t) { strip.appendChild(H("i", { class: "tick", style: { left: (t * 100) + "%" } })); });
      rows.forEach(function (x, i) {
        var r = x.r;
        if (!r || !r.runs) return;
        var top = agents.length > 1 ? (i === 0 ? 7 : 15) : 11;
        var col = color(agents, x.agent);
        if (r.ci95) strip.appendChild(H("i", { class: "ci", "data-agent": x.agent, style: { left: (r.ci95[0] * 100) + "%", width: Math.max(0.5, (r.ci95[1] - r.ci95[0]) * 100) + "%", top: (top - 3) + "px", background: col } }));
        strip.appendChild(H("i", { class: "pt", "data-agent": x.agent, style: { left: (r.rate * 100) + "%", top: top + "px", background: col } }));
      });
      var hit = H("div", { class: "hit" });
      hit.addEventListener("pointermove", function (evt) {
        tip.show(evt, function (el) {
          var h = document.createElement("div"); h.textContent = label + " · 95% Wilson"; el.appendChild(h);
          rows.forEach(function (x) { if (x.r && x.r.runs) tipRow(el, color(agents, x.agent), x.agent + " · " + x.r.successes + "/" + x.r.runs + " · " + ci(x.r.ci95), pct(x.r.rate)); });
        });
      });
      hit.addEventListener("pointerleave", tip.hide);
      strip.appendChild(hit);
      grid.appendChild(strip);
      var val = H("span", { class: "val" });
      rows.forEach(function (x, i) {
        if (i) val.appendChild(H("span", { text: " · " }));
        val.appendChild(H("b", { text: x.r && x.r.runs ? x.r.successes + "/" + x.r.runs : "—" }));
      });
      grid.appendChild(val);
    });
    return grid;
  }

  // ------------------------------------------------------- spend bars
  function spendRows(H, card, agents, tip) {
    var grid = H("div", { class: "sc-grid", "data-sec": "spend" });
    (card.dimensions.spend || []).forEach(function (dim) {
      var key = dim[0], label = dim[1];
      var rows = agents.map(function (a) { return { agent: a, s: card.agents[a].spend[key] }; });
      var max = Math.max.apply(null, rows.map(function (x) { return x.s ? x.s.max : 0; }).concat([0]));
      grid.appendChild(H("span", { class: "lab", text: label + " per run" }));
      var bar = H("div", { class: "sc-bar", "data-dim": key });
      rows.forEach(function (x, i) {
        if (!x.s) return;
        bar.appendChild(H("i", { class: "row " + (i === 0 ? "a" : "b"), "data-agent": x.agent,
          style: { width: (max ? Math.max(0.5, x.s.mean / max * 100) : 0) + "%", background: color(agents, x.agent) } }));
      });
      bar.addEventListener("pointermove", function (evt) {
        tip.show(evt, function (el) {
          var h = document.createElement("div"); h.textContent = label + " per run · mean (median, min–max)"; el.appendChild(h);
          rows.forEach(function (x) { if (x.s) tipRow(el, color(agents, x.agent), x.agent + " · median " + fmt(x.s.median, key) + " · " + fmt(x.s.min, key) + "–" + fmt(x.s.max, key) + " · n " + x.s.n, fmt(x.s.mean, key)); });
        });
      });
      bar.addEventListener("pointerleave", tip.hide);
      grid.appendChild(bar);
      var val = H("span", { class: "val" });
      rows.forEach(function (x, i) {
        if (i) val.appendChild(H("span", { text: " · " }));
        val.appendChild(H("b", { text: x.s ? fmt(x.s.mean, key) : "—" }));
      });
      grid.appendChild(val);
    });
    return grid;
  }

  // ------------------------------------------------------- risk vs reward
  function riskReward(H, card, agents, tip) {
    var wrap = H("div", { class: "sc-rr", "data-sec": "risk-reward" });
    var host = H("div");
    wrap.appendChild(host);
    if (d3) {
      var W = 260, Hh = 190, m = { top: 12, right: 16, bottom: 30, left: 36 };
      var svg = d3.select(host).append("svg").attr("width", W).attr("height", Hh);
      var x = d3.scaleLinear().domain([0, 1]).range([m.left, W - m.right]);
      var y = d3.scaleLinear().domain([0, 1]).range([Hh - m.bottom, m.top]);
      svg.append("g").attr("class", "axis").attr("transform", "translate(0," + (Hh - m.bottom) + ")").call(d3.axisBottom(x).ticks(4).tickSize(3).tickFormat(d3.format(".0%")));
      svg.append("g").attr("class", "axis").attr("transform", "translate(" + m.left + ",0)").call(d3.axisLeft(y).ticks(4).tickSize(3).tickFormat(d3.format(".0%")));
      svg.append("text").attr("class", "axis").attr("x", (m.left + W - m.right) / 2).attr("y", Hh - 4).attr("text-anchor", "middle").style("font-size", "10px").style("fill", "var(--ink-3)").text("risk: share of runs with a flag →");
      svg.append("text").attr("x", 10).attr("y", (m.top + Hh - m.bottom) / 2).attr("transform", "rotate(-90 10," + ((m.top + Hh - m.bottom) / 2) + ")").attr("text-anchor", "middle").style("font-size", "10px").style("fill", "var(--ink-3)").text("reward: success ↑");
      agents.forEach(function (a) {
        var rr = card.agents[a].risk_reward || {};
        if (!isNum(rr.reward) || !isNum(rr.risk)) return;
        svg.append("circle").attr("data-agent", a).attr("cx", x(rr.risk)).attr("cy", y(rr.reward)).attr("r", 6).attr("fill", color(agents, a)).attr("stroke", "var(--surface)").attr("stroke-width", 2);
        svg.append("text").attr("class", "lab").attr("x", x(rr.risk) + 9).attr("y", y(rr.reward) + 4).text(a);
      });
    }
    var chips = H("div", { class: "sc-chips" });
    agents.forEach(function (a) {
      var rr = card.agents[a].risk_reward || {}, sf = card.agents[a].safety || {};
      var line = H("div");
      line.appendChild(H("span", { class: "chip", style: { borderLeft: "3px solid " + color(agents, a) }, text: a }));
      line.appendChild(H("span", { text: " reward " + pct(rr.reward) + " · risk " + pct(rr.risk) + " · ratio " + (isNum(rr.ratio) ? rr.ratio : "— (no flag)") + (isNum(rr.flags_per_success) ? " · " + rr.flags_per_success + " flag(s) per success" : "") }));
      var kinds = H("div");
      Object.keys(sf.flag_kinds || {}).forEach(function (k) { kinds.appendChild(H("span", { class: "chip flag", text: k.replace(/_/g, " ") + " ×" + sf.flag_kinds[k] })); });
      if (!Object.keys(sf.flag_kinds || {}).length) kinds.appendChild(H("span", { class: "chip", text: "no risk flag" }));
      line.appendChild(kinds);
      chips.appendChild(line);
    });
    wrap.appendChild(chips);
    return wrap;
  }

  // ------------------------------------------------------- trajectory table
  function trajectoryTable(H, card, agents) {
    var rows = [
      ["runs · tasks", function (a) { return a.runs + " · " + a.tasks; }],
      ["tool calls · distinct tools", function (a) { return a.tools.calls + " · " + a.tools.distinct.length; }],
      ["wrong-tool calls (golden)", function (a) { return String(a.tools.wrong_tool_calls); }],
      ["undeclared calls · invented arguments", function (a) { return a.tools.undeclared_calls + " · " + a.tools.invented_arguments; }],
      ["answer values · grounded · unsourced", function (a) { return a.grounding.values + " · " + a.grounding.supported + " · " + a.grounding.unsourced_values; }],
      ["repeated calls · cycles · looping runs", function (a) { return a.trajectory.repeated_calls + " · " + a.trajectory.cycles + " · " + a.trajectory.looping_runs; }],
      ["steps after the answer was in hand", function (a) { return String(a.trajectory.steps_after_done); }],
      ["no-information steps", function (a) { return String(a.trajectory.no_information_steps); }],
      ["tool errors · recovered", function (a) { return a.tools.errors + " · " + a.rates.recovered_errors.successes; }],
      ["runs at the step limit", function (a) { return String(a.trajectory.step_limit_runs); }],
      ["writes · blind writes", function (a) { return a.safety.writes + " · " + a.safety.blind_writes; }],
      ["terminations", function (a) { return Object.keys(a.trajectory.terminations).map(function (k) { return k + " ×" + a.trajectory.terminations[k]; }).join(", "); }],
      ["graded by", function (a) { return Object.keys(a.graded_by).map(function (k) { return k + " ×" + a.graded_by[k]; }).join(", "); }],
    ];
    var t = H("table", { class: "sc-table", "data-sec": "trajectory" });
    t.appendChild(H("tr", null, [H("th", { text: "count" })].concat(agents.map(function (a) { return H("th", { text: a }); }))));
    rows.forEach(function (row) {
      t.appendChild(H("tr", null, [H("td", { text: row[0] })].concat(agents.map(function (a) { return H("td", { class: "num", text: row[1](card.agents[a]) }); }))));
    });
    return t;
  }

  // ------------------------------------------------------- judge
  function judgeCards(H, card, agents) {
    var any = agents.some(function (a) { return card.agents[a].judge; });
    if (!any) return H("p", { class: "sc-note", "data-sec": "judge", text: "No judging model has graded these runs. `deepcompare eval … --judge NAME=kind:model` (or `loop --judge`) adds a second model's verdict beside the grade, never in place of it." });
    var wrap = H("div", { class: "sc-judge", "data-sec": "judge" });
    agents.forEach(function (a) {
      var j = card.agents[a].judge;
      var c = H("div", { class: "card", "data-agent": a });
      if (!j) { c.appendChild(H("div", { text: a + ": not judged" })); wrap.appendChild(c); return; }
      c.appendChild(H("div", null, [H("b", { text: a }), H("span", { text: " · judged by " + (j.model || "a model") + " on " + j.judged + " run(s)" })]));
      c.appendChild(H("div", null, [H("span", { text: "judge says solved " }), H("b", { text: j.success.successes + "/" + j.success.runs + " = " + pct(j.success.rate) }), H("span", { text: " [" + ci(j.success.ci95) + "]" + (isNum(j.score_mean) ? " · mean score " + j.score_mean : "") })]));
      c.appendChild(H("div", null, [H("span", { text: "agrees with the grade on " }), H("b", { text: j.agreement.runs ? j.agreement.successes + "/" + j.agreement.runs + " = " + pct(j.agreement.rate) : "— (no exact-match grade to compare)" }), H("span", { text: j.applied ? " · applied as the grade on " + j.applied : "" })]));
      var cf = j.confusion || {};
      var t = H("table");
      t.appendChild(H("tr", null, [H("th", { text: "" }), H("th", { text: "judge ✓" }), H("th", { text: "judge ✗" })]));
      t.appendChild(H("tr", null, [H("th", { text: "grade ✓" }), H("td", { class: "n", text: String(cf.both_pass || 0) }), H("td", { class: "n", text: String(cf.grade_pass_judge_fail || 0) })]));
      t.appendChild(H("tr", null, [H("th", { text: "grade ✗" }), H("td", { class: "n", text: String(cf.grade_fail_judge_pass || 0) }), H("td", { class: "n", text: String(cf.both_fail || 0) })]));
      c.appendChild(t);
      wrap.appendChild(c);
    });
    return wrap;
  }

  function tableView(H, card, agents) {
    var det = H("details", { class: "sc-details" });
    det.appendChild(H("summary", { text: "Table view — every rate with its counts and interval" }));
    var t = H("table", { class: "sc-table" });
    t.appendChild(H("tr", null, [H("th", { text: "dimension" })].concat(agents.map(function (a) { return H("th", { text: a }); }))));
    card.dimensions.rates.forEach(function (dim) {
      t.appendChild(H("tr", null, [H("td", { text: dim[1] })].concat(agents.map(function (a) {
        var r = card.agents[a].rates[dim[0]];
        return H("td", { class: "num", text: r && r.runs ? r.successes + "/" + r.runs + " = " + pct(r.rate) + " [" + ci(r.ci95) + "]" : "not measurable" });
      }))));
    });
    card.dimensions.spend.forEach(function (dim) {
      t.appendChild(H("tr", null, [H("td", { text: dim[1] + " per run" })].concat(agents.map(function (a) {
        var s = card.agents[a].spend[dim[0]];
        return H("td", { class: "num", text: s ? "mean " + fmt(s.mean, dim[0]) + " · median " + fmt(s.median, dim[0]) + " · " + fmt(s.min, dim[0]) + "–" + fmt(s.max, dim[0]) : "—" });
      }))));
    });
    det.appendChild(H("div", { class: "scroll-x" }, [t]));
    return det;
  }

  AgentDiff.block({
    id: "scorecard",
    title: "Evaluation scorecard",
    question: "How do the agents score on success, tools, grounding, spend, safety, trajectory quality — and what does the judge say?",
    group: "other",
    size: "wide",

    relevance: function (ctx) {
      var sc = ctx.aggregate && ctx.aggregate.scorecard;
      return sc && sc.agents && Object.keys(sc.agents).length ? 0.84 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h;
      var card = ctx.aggregate && ctx.aggregate.scorecard;
      if (!card || !card.agents || !Object.keys(card.agents).length) return ctx.empty(el, "No scorecard: this page was built from a single pair without traces to score.");
      var agents = Object.keys(card.agents);
      var root = H("div", { class: "sc" });
      el.appendChild(root);
      var tip = tooltip(root);
      var lede = H("p", { class: "sc-lede" });
      lede.appendChild(H("span", { class: "mode", text: card.mode }));
      lede.appendChild(H("span", { text: (card.golden ? "golden set covers " + card.golden.covered + " of " + card.golden.tasks + " tasks" + (card.golden.uncovered_runs_tasks && card.golden.uncovered_runs_tasks.length ? " (" + card.golden.uncovered_runs_tasks.length + " task(s) in the runs have no golden entry)" : "") : "no golden set: tool correctness and policy read as not measurable") +
        (card.policy ? " · policy: " + Object.keys(card.policy).map(function (k) { var v = card.policy[k]; return k.replace(/_/g, " ") + (Array.isArray(v) ? " " + v.length : v === true ? "" : " " + v); }).join(", ") : " · no policy") + " · " +
        agents.map(function (a) { return a + " " + card.agents[a].runs + " run(s)"; }).join(", ") }));
      root.appendChild(lede);
      var legend = H("div", { class: "sc-legend" });
      agents.forEach(function (a) { legend.appendChild(H("span", null, [H("i", { style: { background: color(agents, a) } }), H("span", { text: a })])); });
      root.appendChild(legend);

      var sec = H("section", { class: "sc-sec" });
      sec.appendChild(H("h4", { text: "Rates, each with its 95% Wilson interval" }));
      sec.appendChild(H("p", { class: "sub", text: "Dot = successes / runs; bar = the interval. A dimension that needs a golden set or a policy says so instead of guessing." }));
      sec.appendChild(rateRows(H, card, agents, tip));
      root.appendChild(sec);

      sec = H("section", { class: "sc-sec" });
      sec.appendChild(H("h4", { text: "Spend per run" }));
      sec.appendChild(H("p", { class: "sub", text: "Mean per run as the bar (scaled to the larger of the two maxima); hover for median and range. Tokens and cost are as recorded — the trace says whether they were measured or estimated." }));
      sec.appendChild(spendRows(H, card, agents, tip));
      root.appendChild(sec);

      sec = H("section", { class: "sc-sec" });
      sec.appendChild(H("h4", { text: "Risk against reward" }));
      sec.appendChild(H("p", { class: "sub", text: "Reward is the success rate; risk is the share of runs with at least one risk flag (forbidden tool or pattern, blind or unverified write, undeclared tool, invented argument, loop, step limit). The ratio is reward / risk — a run that never flags has no ratio, not an infinite one." }));
      sec.appendChild(riskReward(H, card, agents, tip));
      root.appendChild(sec);

      sec = H("section", { class: "sc-sec" });
      sec.appendChild(H("h4", { text: "Trajectory quality, as counts" }));
      sec.appendChild(H("p", { class: "sub", text: "Correct tool, loops and repeats, steps spent after the answer was in hand, recovery after tool errors, writes without a read or a check, how runs ended." }));
      sec.appendChild(H("div", { class: "scroll-x" }, [trajectoryTable(H, card, agents)]));
      root.appendChild(sec);

      sec = H("section", { class: "sc-sec" });
      sec.appendChild(H("h4", { text: "LLM as a judge, beside the grade" }));
      sec.appendChild(judgeCards(H, card, agents));
      root.appendChild(sec);

      root.appendChild(tableView(H, card, agents));
      if (card.note) root.appendChild(H("p", { class: "sc-note", text: card.note }));
    },
  });
})(typeof window !== "undefined" ? window : this);
