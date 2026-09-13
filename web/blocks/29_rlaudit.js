/* AgentDiff blocks — auditing the signal itself.
 *
 * Every other training block reads a policy by what it earned. These two
 * ask the question one layer down, the one nobody asks until a policy is
 * already gamed:
 *
 *   rl-audit-reward  is the reward measuring the right thing? the return
 *                    against the outcome, episode by episode, with the
 *                    disagreeing ones ringed and named; the concentration
 *                    reading; the steps paid while labelled bad
 *   rl-audit-critic  does the critic know what is coming? the value
 *                    estimate against the realised discounted return-to-go
 *                    with the y = x line as the only reference, the decile
 *                    calibration curve over it, the residuals as a
 *                    marginal, and the explained variance said in words
 *
 * Both read `aggregate.rl.audit` (deepcompare/rlaudit.py) and fall back to
 * `report.rl.audit` for a single pair. Every number drawn here is one the
 * engine computed and wrote down — nothing is recomputed in the page, so
 * the chart and the finding list cannot drift apart.
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
      ".rlq{position:relative}",
      ".rlq svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rlq .lab{font-size:var(--fs-xs);fill:var(--ink-2)}.rlq .lab.dim{fill:var(--ink-3)}",
      ".rlq .tick{font-size:var(--fs-xs);fill:var(--ink-3);font-variant-numeric:tabular-nums}",
      ".rlq .zero{stroke:var(--rule-2)}.rlq .diag{stroke:var(--ink-3);stroke-dasharray:3 3;fill:none}",
      ".rlq-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 6px;max-width:90ch}",
      ".rlq-lede b{font-weight:650}",
      ".rlq-bar{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".rlq-bar i{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
      ".rlq-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}",
      "@media (max-width:560px){.rlq-chip{white-space:normal}}",
      ".rlq-chip b{color:var(--ink);font-weight:600}",
      ".rlq-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-3);border-radius:999px;padding:1px 9px;cursor:pointer}",
      ".rlq-bar button[aria-pressed=true]{background:var(--ink-3);color:var(--surface)}",
      ".rlq-bar button:hover{color:var(--ink)}.rlq-bar button[aria-pressed=true]:hover{color:var(--surface)}",
      ".rlq-note{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0;max-width:90ch;line-height:1.45}",
      ".rlq-row{display:flex;gap:14px 18px;flex-wrap:wrap;align-items:flex-start}",
      ".rlq-row>.rlq-main{flex:3 1 320px;min-width:0}.rlq-row>.rlq-side{flex:1 1 220px;min-width:0}",
      ".rlq-h{font-size:var(--fs-xs);color:var(--ink-3);text-transform:uppercase;letter-spacing:.07em;margin:0 0 5px}",
      ".rlq-strip{display:flex;height:9px;border-radius:2px;overflow:hidden;margin:0 0 6px}",
      ".rlq-strip span{display:block;height:100%}",
      ".rlq-kv{display:grid;grid-template-columns:1fr auto;gap:2px 10px;font-size:var(--fs-xs);color:var(--ink-3);font-variant-numeric:tabular-nums;margin:0 0 8px}",
      ".rlq-kv b{color:var(--ink);font-weight:600;font-family:var(--mono)}",
      ".rlq-list{list-style:none;margin:0;padding:0}",
      ".rlq-list li{display:grid;grid-template-columns:1fr auto;gap:0 10px;padding:3px 4px;border-radius:5px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".rlq-list li.hit{cursor:pointer}.rlq-list li.hit:hover,.rlq-list li.hit:focus-visible{background:var(--surface-2);outline:none;color:var(--ink-2)}",
      ".rlq-list .who{font-family:var(--mono);color:var(--ink-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".rlq-list .amt{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink);white-space:nowrap}",
      ".rlq-list .why{grid-column:1/-1;color:var(--ink-3)}",
      ".rlq .pt{cursor:default}.rlq .pt.hit{cursor:pointer}.rlq .pt.hit:hover{stroke-width:2.5}",
      ".rlq .ring{fill:none;stroke-width:1.4;pointer-events:none}",
      ".rlq-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:300px}",
      ".rlq-tip b{color:var(--ink)}",
      ".rlq-scroll{overflow-x:auto;max-width:100%}",
      /* the audit reads across the whole desk: the scatter needs the width,
         and the panel beside it is the evidence for the same sentence */
      'body[data-view="training"] .stack>[data-block="rl-audit-reward"],',
      'body[data-view="training"] .stack>[data-block="rl-audit-critic"]{grid-column:1/-1}',
    ].join("\n");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------ helpers

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function plain(v, p) { return isNum(v) ? v.toFixed(p === undefined ? 2 : p).replace("-", "−") : "—"; }
  function signed(v, p) {
    if (!isNum(v)) return "—";
    var s = Math.abs(v).toFixed(p === undefined ? 2 : p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  }
  function pct(v, p) { return isNum(v) ? (100 * v).toFixed(p === undefined ? 0 : p) + "%" : "—"; }
  function short(id) { return String(id || "").replace(/^(rl|t)\d+_/, "").replace(/_/g, " "); }
  function trunc(s, n) { s = String(s === null || s === undefined ? "" : s); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  function width(host) {
    var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0;
    return Math.max(280, Math.min(1120, w || 320));
  }
  function responsive(host, draw, k) {
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, k);
    draw(); return host;
  }
  function selectStep(report, side, step) {
    if (AgentDiff.charts && AgentDiff.charts.selectStep && report && side && isNum(step)) {
      AgentDiff.charts.selectStep(report, side, step);
    }
  }
  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "rlq-tip"; tip.hidden = true; root.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        lines.forEach(function (l) {
          if (!l) return;
          var d = document.createElement("div");
          if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text;
          tip.appendChild(d);
        });
        tip.hidden = false;
        var r = root.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + 300 > r.width) x = Math.max(0, evt.clientX - r.left - 310);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  // -------------------------------------------------------------- model

  /* The audit as the engine wrote it, plus the page's own colours for the
   * policies and the side each episode sits on in the open pair (so a row
   * can click through to the step it names). */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {}, report = ctx.report || null;
    if (cache && cache.agg === agg && cache.report === report) return cache.m;
    var m;
    try { m = build(agg, report); } catch (err) { console.warn("AgentDiff rl-audit: model failed", err); m = build({}, null); }
    cache = { agg: agg, report: report, m: m };
    return m;
  }

  function build(agg, report) {
    var rl = agg && agg.rl && typeof agg.rl === "object" ? agg.rl : null;
    var audit = rl && rl.audit && typeof rl.audit === "object" ? rl.audit : null;
    var scope = audit ? "batch" : null;
    if (!audit && report && report.rl && report.rl.audit && typeof report.rl.audit === "object") {
      audit = report.rl.audit; scope = "pair";
    }
    var m = { audit: null, scope: scope, gamma: 0.99, policies: [], colors: {}, report: report };
    if (!audit || !audit.measurable) return m;
    m.audit = audit;
    m.gamma = isNum(audit.gamma) ? audit.gamma : 0.99;
    var an = report && report.a && report.a.agent && report.a.agent.name;
    var bn = report && report.b && report.b.agent && report.b.agent.name;
    var names = Array.isArray(audit.policies) ? audit.policies.slice() : [];
    var sideOf = {};
    names.forEach(function (n) { sideOf[n] = n === an ? "a" : n === bn ? "b" : null; });
    var free = ["a", "b"].filter(function (s) { return names.every(function (n) { return sideOf[n] !== s; }); });
    names.forEach(function (n) { if (!sideOf[n] && free.length) sideOf[n] = free.shift(); });
    names.sort(function (x, y) {
      var rank = { a: 0, b: 1 };
      return (sideOf[x] in rank ? rank[sideOf[x]] : 2) - (sideOf[y] in rank ? rank[sideOf[y]] : 2);
    });
    names.forEach(function (n) {
      var color = sideOf[n] === "a" ? "var(--a)" : sideOf[n] === "b" ? "var(--b)" : "var(--ink-3)";
      m.colors[n] = color;
      m.policies.push({ name: n, side: sideOf[n], color: color });
    });
    return m;
  }

  function colorOf(m, name) { return m.colors[name] || "var(--ink-3)"; }

  /* Which side of the open pair a finding sits on, when it sits on one at
   * all: the page can only open a step it is actually showing. */
  function sideFor(m, row) {
    if (!m.report || !row) return null;
    if (m.scope === "pair") return row.side || null;
    var task = m.report.task && m.report.task.id;
    if (!task || String(row.task_id) !== String(task)) return null;
    for (var i = 0; i < 2; i++) {
      var side = i ? "b" : "a", block = m.report[side];
      if (!block || !block.agent || block.agent.name !== row.agent) continue;
      var rid = block.run_id === null || block.run_id === undefined ? null : String(block.run_id);
      if (row.run_id && rid && String(row.run_id) !== rid) continue;
      return side;
    }
    return null;
  }

  function policyChips(H, m, text) {
    var bar = H("div", { class: "rlq-bar" });
    m.policies.forEach(function (p) {
      var t = text(p);
      if (t === null) return;
      bar.appendChild(H("span", { class: "rlq-chip", "data-policy": p.name }, [
        H("i", { style: { background: p.color } }), H("b", { text: p.name }), H("span", { text: " · " + t }),
      ]));
    });
    return bar;
  }

  // ------------------------------------------- 1. is the reward right?

  var MEASURE = {};   //: the chosen x measure per block instance, by id

  function measures(audit) {
    var d = audit.reward.disagreement || {};
    var scopes = d.scopes || {};
    var out = [{ key: "return", label: "return", field: "return",
                 inversions: (scopes.by_task || scopes.pooled || {}).inversions || 0,
                 pairs: (scopes.by_task || scopes.pooled || {}).pairs_n || 0 }];
    if (scopes.shaping && scopes.shaping.pairs_n) {
      out.push({ key: "shaping", label: "shaping only", field: "shaping_return",
                 inversions: scopes.shaping.inversions || 0, pairs: scopes.shaping.pairs_n || 0 });
    }
    return out;
  }

  /* The sentence that answers the question, before any chart. */
  function verdict(audit) {
    var d = audit.reward.disagreement || {};
    if (!d.measurable) return { head: "Not from these episodes.", rest: d.reason || "" };
    var scopes = d.scopes || {}, task = scopes.by_task || scopes.pooled || {}, sh = scopes.shaping || {};
    if (task.inversions) {
      return { head: "No — the return and the outcome disagree.",
               rest: task.inversions + " of " + task.pairs_n + " ordered pairs within a task put a failed episode "
                     + "above a passing one, which is where a policy collects return without passing." };
    }
    if (sh.inversions) {
      return { head: "Only through its last step.",
               rest: "The return never puts a failed episode above a passing one on the same task (" + task.pairs_n
                     + " ordered pairs). Take the last step out and the dense shaping that remains gets "
                     + sh.inversions + " of " + sh.pairs_n + " the wrong way round — so the agreement is the "
                     + "outcome term restating itself, not the shaping tracking the outcome." };
    }
    return { head: "As far as these episodes can show, yes.",
             rest: "No failed episode out-earns a passing one on the same task, over " + task.pairs_n
                   + " ordered pairs, and the shaping alone orders them the same way." };
  }

  function drawOutcomes(host, ctx, m, measure, tip) {
    if (!d3) return;
    var audit = m.audit, rows = audit.reward.episodes || [];
    var d = audit.reward.disagreement || {};
    var showRings = d.findings_basis === measure.key
      || (d.findings_basis === "by_task" && measure.key === "return")
      || (d.findings_basis === "pooled" && measure.key === "return");
    var W = width(host), R = 3.2;
    var pad = { l: 76, r: 14, t: 22, b: 34 };
    var vals = rows.map(function (r) { return r[measure.field]; }).filter(isNum);
    if (!vals.length) return;
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (lo === hi) { lo -= 1; hi += 1; }
    var x = d3.scaleLinear().domain([lo, hi]).nice().range([pad.l, W - pad.r]);
    var lanes = [{ key: true, label: "passed" }, { key: false, label: "failed" }];
    // deterministic stacking: points near each other on x step away from the
    // lane in a fixed order, so the same data always draws the same picture
    var placed = {};
    lanes.forEach(function (lane) {
      var mine = rows.filter(function (r) { return r.success === lane.key && isNum(r[measure.field]); })
        .sort(function (p, q) {
          return (p[measure.field] - q[measure.field]) || (p.agent < q.agent ? -1 : p.agent > q.agent ? 1 : 0)
            || (String(p.task_id) < String(q.task_id) ? -1 : 1) || (String(p.run_id) < String(q.run_id) ? -1 : 1);
        });
      var lastX = -1e9, depth = 0, maxDepth = 0;
      mine.forEach(function (r) {
        var px = x(r[measure.field]);
        if (px - lastX < 2 * R - 0.5) { depth += 1; } else { depth = 0; lastX = px; }
        maxDepth = Math.max(maxDepth, depth);
        r._px = px; r._depth = depth;
      });
      placed[String(lane.key)] = { rows: mine, depth: maxDepth };
    });
    // passes stack upward and failures downward from their own baselines, so
    // a deep pile never climbs out of the frame
    var STEP = R * 1.55, GAP = 30;
    var up = placed["true"].depth * STEP, down = placed["false"].depth * STEP;
    var yOf = { "true": pad.t + up + R, "false": pad.t + up + R + GAP };
    var Hh = yOf["false"] + down + R + pad.b;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "each episode's " + measure.label + " on the horizontal axis, passing episodes on the top "
        + "row and failing episodes below, coloured by policy"
        + (showRings && d.findings_n ? "; the " + d.findings_n + " episodes the outcome disagrees with are ringed" : ""));
    x.ticks(Math.max(3, Math.min(7, Math.round(W / 130)))).forEach(function (v) {
      svg.append("text").attr("class", "tick").attr("x", x(v)).attr("y", Hh - pad.b + 15)
        .attr("text-anchor", "middle").text(signed(v, 0));
    });
    if (x.domain()[0] < 0 && x.domain()[1] > 0) {
      svg.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0))
        .attr("y1", pad.t - 2).attr("y2", Hh - pad.b);
    }
    svg.append("text").attr("class", "lab dim").attr("x", W - pad.r).attr("y", pad.t - 4)
      .attr("text-anchor", "end").text(measure.label + " →");
    lanes.forEach(function (lane, li) {
      var y0 = yOf[String(lane.key)];
      var bundle = placed[String(lane.key)];
      svg.append("text").attr("class", "lab").attr("x", pad.l - 10).attr("y", y0 + 4)
        .attr("text-anchor", "end").text(lane.label + " " + bundle.rows.length);
      bundle.rows.forEach(function (r) {
        var dir = li ? 1 : -1;                       // passes stack up, failures down
        var cy = y0 + dir * r._depth * STEP;
        var side = sideFor(m, r);
        var g = svg.append("g").attr("class", "rlq-ep");
        if (showRings && r.flagged) {
          g.append("circle").attr("class", "ring").attr("cx", r._px).attr("cy", cy).attr("r", R + 3)
            .attr("stroke", "var(--warn, var(--bad))");
        }
        g.append("circle").attr("class", "pt" + (side ? " hit" : "")).attr("data-policy", r.agent)
          .attr("data-task", r.task_id).attr("data-run", r.run_id).attr("data-flagged", r.flagged ? "true" : "false")
          .attr("cx", r._px).attr("cy", cy).attr("r", R)
          .attr("fill", colorOf(m, r.agent)).attr("fill-opacity", lane.key ? 0.85 : 0.35)
          .attr("stroke", colorOf(m, r.agent)).attr("stroke-width", 1)
          .on("pointermove", function (evt) {
            tip.show(evt, [
              { b: true, text: r.agent + " · " + short(r.task_id) + (r.run_id ? " · " + r.run_id : "") },
              { text: measure.label + " " + signed(r[measure.field]) + " · " + (r.success ? "passed" : "failed")
                  + " · " + r.steps + " steps" },
              { text: "return " + signed(r["return"]) + ", of which the last step paid " + signed(r.last_reward) },
              showRings && r.flagged ? { text: "the outcome disagrees with this one" } : null,
              side ? { text: "click to open this run" } : null]);
          })
          .on("pointerleave", tip.hide)
          .on("click", function () { tip.hide(); if (side) selectStep(m.report, side, 0); });
      });
    });
    // the named ones: the largest disagreements, labelled where they sit
    if (showRings) {
      var labelled = { 0: [], 1: [] };
      (d.findings || []).forEach(function (f) {
        var r = rows.filter(function (row) {
          return row.agent === f.agent && String(row.task_id) === String(f.task_id) && String(row.run_id) === String(f.run_id);
        })[0];
        if (!r || !isNum(r._px)) return;
        var li = r.success ? 0 : 1, cy = yOf[String(r.success)] + (li ? 1 : -1) * r._depth * STEP;
        // one name per neighbourhood: a label that lands on another says less
        // than no label at all
        if (labelled[li].length >= 2) return;
        if (labelled[li].some(function (px) { return Math.abs(px - r._px) < 130; })) return;
        labelled[li].push(r._px);
        var anchor = r._px > W * 0.7 ? "end" : "start";
        svg.append("text").attr("class", "lab").attr("x", r._px + (anchor === "end" ? -(R + 6) : R + 6))
          .attr("y", cy + (li ? 13 : -8)).attr("text-anchor", anchor)
          .text(trunc(short(f.task_id) + " " + (f.run_id || ""), 26));
      });
    }
  }

  function concentrationPanel(H, m) {
    var c = m.audit.reward.concentration || {};
    var wrap = H("div", { class: "rlq-conc" });
    wrap.appendChild(H("p", { class: "rlq-h", text: "Where the reward sits" }));
    if (!c.measurable) {
      wrap.appendChild(H("p", { class: "rlq-note", text: c.reason || "No episode was paid any reward." }));
      return wrap;
    }
    var last = c.mean_last_share || 0;
    wrap.appendChild(H("div", { class: "rlq-strip", "data-last": last, title: "the last step against everything else" }, [
      H("span", { style: { width: (100 * last).toFixed(2) + "%", background: "var(--ink)" } }),
      H("span", { style: { width: (100 * (1 - last)).toFixed(2) + "%", background: "var(--rule-2)" } }),
    ]));
    var kv = H("div", { class: "rlq-kv" });
    [["last step", pct(c.mean_last_share, 1)],
     ["largest step", pct(c.mean_largest_share, 1)],
     ["× an even spread", plain(c.mean_peakedness, 1)],
     ["steps paid anything", pct(c.mean_paid_share, 0)]].forEach(function (row) {
      kv.appendChild(H("span", { text: row[0] }));
      kv.appendChild(H("b", { text: row[1] }));
    });
    wrap.appendChild(kv);
    wrap.appendChild(H("p", { class: "rlq-note", "data-kind": c.kind, text: c.note }));
    return wrap;
  }

  function unearnedPanel(H, m) {
    var u = m.audit.reward.unearned || {};
    var wrap = H("div", { class: "rlq-unearned" });
    wrap.appendChild(H("p", { class: "rlq-h", text: "Paid while labelled bad" }));
    if (!u.measurable) {
      wrap.appendChild(H("p", { class: "rlq-note", text: u.reason || "No step carries a label." }));
      return wrap;
    }
    if (!(u.rows || []).length) {
      wrap.appendChild(H("p", { class: "rlq-note", text: u.note }));
      return wrap;
    }
    var labels = u.by_label || {};
    var keys = Object.keys(labels).sort();
    if (keys.length) {
      var kv = H("div", { class: "rlq-kv" });
      keys.forEach(function (label) {
        m.policies.forEach(function (p) {
          var cell = labels[label][p.name];
          if (!cell) return;
          kv.appendChild(H("span", { "data-label": label, "data-policy": p.name }, [
            H("i", { style: { display: "inline-block", width: "8px", height: "8px", borderRadius: "50%",
                              background: p.color, marginRight: "5px" } }),
            H("span", { text: label.replace(/_/g, " ") + " \u00b7 " + cell.steps + " steps" }),
          ]));
          kv.appendChild(H("b", { text: signed(cell.reward, 1) }));
        });
      });
      wrap.appendChild(kv);
    }
    var list = H("ul", { class: "rlq-list" });
    u.rows.slice(0, 6).forEach(function (r) {
      var side = sideFor(m, r);
      var li = H("li", {
        class: side ? "hit" : "", "data-kind": r.kind, "data-step": r.step, "data-policy": r.agent,
        tabindex: side ? "0" : null, role: side ? "button" : null,
        onclick: side ? function () { selectStep(m.report, side, r.step); } : null,
        onkeydown: side ? function (evt) { if (evt.key === "Enter" || evt.key === " ") { evt.preventDefault(); selectStep(m.report, side, r.step); } } : null,
      }, [
        H("span", { class: "who", text: r.agent + " · " + short(r.task_id) + (r.run_id ? " " + r.run_id : "") + " · step " + r.step }),
        H("span", { class: "amt", style: { color: r.reward > 0 ? "var(--good)" : "var(--bad)" }, text: signed(r.reward) }),
        H("span", { class: "why", text: (r.labels || []).join(", ") + (r.name ? " · " + r.name : "") }),
      ]);
      list.appendChild(li);
    });
    wrap.appendChild(list);
    var shown = Math.min(6, u.rows.length), total = u.positive_while_bad + u.negative_while_good;
    if (shown < total) {
      wrap.appendChild(H("p", { class: "rlq-note", text: "the " + shown + " largest of " + total
        + ", at most two from any one policy and task; each names the step it happened on." }));
    }
    return wrap;
  }

  AgentDiff.block({
    id: "rl-audit-reward",
    title: "Reward integrity",
    question: "Is the reward measuring the right thing?",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var m = model(ctx);
      return m.audit && (m.audit.reward || {}).measurable && (m.audit.reward.episodes || []).length ? 0.735 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx), audit = m.audit;
      var root = H("div", { class: "rlq rlq-reward" });
      el.appendChild(root);
      var tip = tooltip(root);
      var v = verdict(audit);
      root.appendChild(H("p", { class: "rlq-lede" }, [H("b", { text: v.head }), H("span", { text: " " + v.rest })]));

      var opts = measures(audit);
      var chosen = MEASURE["rl-audit-reward"];
      if (!chosen || !opts.some(function (o) { return o.key === chosen; })) chosen = opts[0].key;
      var bar = H("div", { class: "rlq-bar" });
      if (opts.length > 1) {
        bar.appendChild(H("span", { class: "rlq-chip", text: "x axis" }));
        opts.forEach(function (o) {
          bar.appendChild(H("button", {
            type: "button", "data-measure": o.key, "aria-pressed": o.key === chosen ? "true" : "false",
            text: o.label + (o.inversions ? " · " + o.inversions + " disagree" : " · none disagree"),
            onclick: function () { MEASURE["rl-audit-reward"] = o.key; if (AgentDiff._rerender) AgentDiff._rerender(); },
          }));
        });
      }
      var rank = audit.reward.rank_agreement || {};
      if (rank.measurable) {
        bar.appendChild(H("span", { class: "rlq-chip", "data-key": "spearman" }, [
          H("b", { text: "ρ " + plain(rank.spearman) }),
          H("span", { text: " of a possible " + plain(rank.ceiling) + " · n=" + rank.n }),
        ]));
      }
      var cost = (audit.reward.cost || {}).per_agent || {};
      m.policies.forEach(function (p) {
        var c = cost[p.name];
        if (!c) return;
        bar.appendChild(H("span", { class: "rlq-chip", "data-policy": p.name }, [
          H("i", { style: { background: p.color } }), H("b", { text: p.name }),
          H("span", { text: " · " + signed(c.return_per_step, 3) + "/step"
            + (isNum(c.return_per_second) ? ", " + signed(c.return_per_second, 3) + "/s" : "") }),
        ]));
      });
      root.appendChild(bar);

      var row = H("div", { class: "rlq-row" });
      root.appendChild(row);
      var main = H("div", { class: "rlq-main" });
      var host = H("div", { class: "rlq-chart" });
      main.appendChild(responsive(host, function () {
        drawOutcomes(host, ctx, m, opts.filter(function (o) { return o.key === chosen; })[0], tip);
      }, "rl-audit-outcomes"));
      row.appendChild(main);
      var side = H("div", { class: "rlq-side" });
      side.appendChild(concentrationPanel(H, m));
      side.appendChild(unearnedPanel(H, m));
      row.appendChild(side);

      var notes = [audit.reward.disagreement.note, rank.measurable ? rank.note : null];
      var tools = audit.reward.tools || {};
      if ((tools.findings || []).length) {
        var byTool = {};
        tools.findings.forEach(function (f) {
          (byTool[f.tool] || (byTool[f.tool] = [])).push(f.agent + " " + pct(f.share));
        });
        notes.push("The positive reward is tool-concentrated: "
          + Object.keys(byTool).sort().map(function (t) {
              return byTool[t].join(" and ") + " of it through " + t;
            }).join("; ")
          + ". A policy earning through one tool is a fragile policy.");
      }
      notes.push(audit.caveat);
      notes.filter(Boolean).forEach(function (t) {
        main.appendChild(H("p", { class: "rlq-note", text: t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") }));
      });
    },
  });

  // ------------------------------------- 2. does the critic know what is coming?

  function drawCalibration(host, ctx, m, tip) {
    if (!d3) return;
    var c = m.audit.critic, pts = c.points || [];
    if (!pts.length) return;
    var W = width(host), Hh = Math.max(200, Math.min(340, Math.round(W * 0.52)));
    var pad = { l: 44, r: 12, t: 16, b: 30 };
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) { lo = Math.min(lo, p.predicted, p.actual); hi = Math.max(hi, p.predicted, p.actual); });
    if (lo === hi) { lo -= 1; hi += 1; }
    var x = d3.scaleLinear().domain([lo, hi]).nice().range([pad.l, W - pad.r]);
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([Hh - pad.b, pad.t]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "the value estimate against the realised discounted return-to-go, one point per step, "
        + "coloured by policy, with the y equals x line and the decile calibration curve over it");
    x.ticks(5).forEach(function (v) {
      svg.append("text").attr("class", "tick").attr("x", x(v)).attr("y", Hh - pad.b + 15).attr("text-anchor", "middle").text(signed(v, 0));
    });
    y.ticks(5).forEach(function (v) {
      if (y(v) > Hh - pad.b - 10) return;          // the corner belongs to the x axis
      svg.append("text").attr("class", "tick").attr("x", pad.l - 6).attr("y", y(v) + 4).attr("text-anchor", "end").text(signed(v, 0));
    });
    // the only reference a reader needs
    var d0 = Math.max(x.domain()[0], y.domain()[0]), d1 = Math.min(x.domain()[1], y.domain()[1]);
    svg.append("line").attr("class", "diag").attr("x1", x(d0)).attr("y1", y(d0)).attr("x2", x(d1)).attr("y2", y(d1));
    svg.append("text").attr("class", "lab dim").attr("x", x(d1)).attr("y", y(d1) - 5).attr("text-anchor", "end").text("y = x");
    svg.append("text").attr("class", "lab dim").attr("x", W - pad.r).attr("y", Hh - 4).attr("text-anchor", "end").text("value estimate →");
    svg.append("text").attr("class", "lab dim").attr("x", pad.l - 30).attr("y", pad.t - 4).text("↑ realised return-to-go (γ=" + m.gamma + ")");
    pts.forEach(function (p) {
      var side = sideFor(m, p);
      svg.append("circle").attr("class", "pt" + (side ? " hit" : "")).attr("data-policy", p.agent)
        .attr("data-task", p.task_id).attr("data-run", p.run_id).attr("data-step", p.step)
        .attr("cx", x(p.predicted)).attr("cy", y(p.actual)).attr("r", 2.6)
        .attr("fill", colorOf(m, p.agent)).attr("fill-opacity", 0.5).attr("stroke", "none")
        .on("pointermove", function (evt) {
          tip.show(evt, [
            { b: true, text: p.agent + " · " + short(p.task_id) + (p.run_id ? " · " + p.run_id : "") + " · step " + p.step },
            { text: "predicted " + signed(p.predicted) + " · arrived " + signed(p.actual) },
            { text: "residual " + signed(p.residual) + " (" + (p.residual > 0 ? "optimistic" : p.residual < 0 ? "pessimistic" : "exact") + ")" },
            side ? { text: "click to open the step" } : null]);
        })
        .on("pointerleave", tip.hide)
        .on("click", function () { tip.hide(); if (side) selectStep(m.report, side, p.step); });
    });
    // the calibration curve: predicted against actual, averaged by decile
    var bins = (c.deciles || []).filter(function (b) { return isNum(b.predicted) && isNum(b.actual); });
    if (bins.length > 1) {
      svg.append("path").attr("class", "rlq-cal").attr("fill", "none").attr("stroke", "var(--ink)")
        .attr("stroke-width", 1.6).attr("stroke-linejoin", "round")
        .attr("d", d3.line().x(function (b) { return x(b.predicted); }).y(function (b) { return y(b.actual); })(bins));
      bins.forEach(function (b) {
        svg.append("rect").attr("class", "rlq-cal-pt").attr("data-bin", b.bin).attr("data-n", b.n)
          .attr("x", x(b.predicted) - 2.6).attr("y", y(b.actual) - 2.6).attr("width", 5.2).attr("height", 5.2)
          .attr("fill", "var(--ink)")
          .on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: "decile " + (b.bin + 1) + " of " + bins.length + " · " + b.n + " steps" },
              { text: "predicted " + signed(b.predicted) + " in [" + signed(b.predicted_lo) + ", " + signed(b.predicted_hi) + "]" },
              { text: "arrived " + signed(b.actual) + " · residual " + signed(b.residual) }]);
          }).on("pointerleave", tip.hide);
      });
    }
  }

  function drawResiduals(host, ctx, m, tip) {
    if (!d3) return;
    var c = m.audit.critic, bins = c.residual_bins || [];
    if (!bins.length) return;
    var W = width(host), Hh = 96, pad = { l: 44, r: 12, t: 18, b: 22 };
    var lo = bins[0].from, hi = bins[bins.length - 1].to;
    if (lo === hi) { lo -= 1; hi += 1; }
    var x = d3.scaleLinear().domain([lo, hi]).range([pad.l, W - pad.r]);
    var maxC = 1;
    bins.forEach(function (b) { maxC = Math.max(maxC, b.count); });
    var y = d3.scaleLinear().domain([0, maxC]).range([Hh - pad.b, pad.t]);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "the residuals, value estimate minus what arrived, as a histogram stacked by policy");
    svg.append("text").attr("class", "lab dim").attr("x", pad.l - 30).attr("y", pad.t - 3)
      .text("↑ steps, tallest bin " + maxC);
    svg.append("text").attr("class", "lab dim").attr("x", W - pad.r).attr("y", pad.t - 3).attr("text-anchor", "end")
      .text("residual: value − what arrived →");
    [lo, 0, hi].forEach(function (v) {
      if (v < lo || v > hi) return;
      svg.append("text").attr("class", "tick").attr("x", x(v)).attr("y", Hh - pad.b + 14)
        .attr("text-anchor", v === lo ? "start" : v === hi ? "end" : "middle").text(signed(v, 1));
    });
    if (lo < 0 && hi > 0) {
      svg.append("line").attr("class", "zero").attr("x1", x(0)).attr("x2", x(0)).attr("y1", pad.t - 4).attr("y2", Hh - pad.b);
    }
    bins.forEach(function (b, bi) {
      var bw = Math.max(1, x(b.to) - x(b.from) - 1), base = Hh - pad.b, acc = 0;
      m.policies.forEach(function (p) {
        var n = (b.by_agent || {})[p.name] || 0;
        if (!n) return;
        var h = base - y(n);
        svg.append("rect").attr("class", "rlq-res").attr("data-bin", bi).attr("data-policy", p.name).attr("data-count", n)
          .attr("x", x(b.from) + 0.5).attr("width", bw).attr("y", base - acc - h).attr("height", Math.max(0.5, h))
          .attr("fill", p.color).attr("fill-opacity", 0.75)
          .on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: p.name },
              { text: n + " steps with a residual in [" + signed(b.from) + ", " + signed(b.to) + ")" }]);
          }).on("pointerleave", tip.hide);
        acc += h;
      });
    });
  }

  AgentDiff.block({
    id: "rl-audit-critic",
    title: "Critic calibration",
    question: "Does the critic know what is coming?",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var m = model(ctx);
      return m.audit && (m.audit.critic || {}).measurable && (m.audit.critic.points || []).length ? 0.73 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx), c = m.audit.critic;
      var root = H("div", { class: "rlq rlq-critic" });
      el.appendChild(root);
      var tip = tooltip(root);
      var ev = (c.overall || {}).explained_variance;
      var head = !isNum(ev) ? "There is no variance to explain."
        : ev < 0 ? "No — it is worse than predicting the mean."
        : ev < 0.5 ? "Only partly." : "Mostly, yes.";
      root.appendChild(H("p", { class: "rlq-lede" }, [
        H("b", { class: "rlq-verdict", "data-ev": isNum(ev) ? ev : "", text: head }),
        H("span", { text: " " + c.narrative }),
      ]));
      root.appendChild(policyChips(H, m, function (p) {
        var b = (c.per_agent || {})[p.name];
        if (!b) return null;
        return "EV " + (isNum(b.explained_variance) ? plain(b.explained_variance) : "—")
          + (b.worse_than_the_mean ? " (worse than the mean)" : "")
          + " · bias " + signed(b.mean_error) + " · RMSE " + plain(b.rmse) + " · n=" + b.n;
      }));

      var main = H("div", { class: "rlq-main" });
      root.appendChild(main);
      var scatter = H("div", { class: "rlq-chart rlq-cal-host" });
      main.appendChild(responsive(scatter, function () { drawCalibration(scatter, ctx, m, tip); }, "rl-audit-calibration"));
      var margin = H("div", { class: "rlq-chart rlq-res-host" });
      main.appendChild(responsive(margin, function () { drawResiduals(margin, ctx, m, tip); }, "rl-audit-residuals"));

      var worse = Object.keys(c.per_agent || {}).filter(function (n) { return c.per_agent[n].worse_than_the_mean; }).sort();
      var notes = [c.note];
      if (worse.length) {
        notes.push("Read the explained variance plainly: for " + worse.join(" and ")
          + ", a constant equal to the average return-to-go would have scored better than this value head. "
          + "That is what a negative explained variance means, and it is worth saying rather than rounding past.");
      }
      notes.push("The heavy line is the calibration curve: each mark is a decile of predicted value, plotted at the "
        + "decile's mean prediction against the mean of what actually arrived. Where it sits above y = x the critic "
        + "was pessimistic in that band, below it optimistic.");
      var adv = c.advantages || {};
      if (adv.measurable) {
        notes.push(adv.note.charAt(0).toUpperCase() + adv.note.slice(1) + ".");
      } else if (adv.reason) {
        notes.push("Advantages: " + adv.reason + ".");
      }
      notes.filter(Boolean).forEach(function (t) {
        main.appendChild(H("p", { class: "rlq-note", text: t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? "" : ".") }));
      });
    },
  });
})(this);
