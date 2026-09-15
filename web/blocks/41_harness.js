/* AgentDiff blocks — the harness beside the agent.
 *
 * `aggregate.harness_evolution` (deepcompare/harnessevo.py) asks two
 * questions the Evolution blocks above it cannot: when a generation
 * improved, may the delta be handed to the agent at all — and was the gain
 * the agent needing less, or more being put around it?
 *
 * Two blocks, deliberately. The lineage already has seven and the page
 * eleven views; what this reading needs is not another lane but the two
 * pictures that carry it:
 *
 *   hn-ladder  one row per step: what changed, may you believe it, and the
 *              fingerprint evidence on demand
 *   hn-absorb  the plane — work per pass against pass rate, the lineage
 *              walked as a path. Up and left is the agent getting better;
 *              up and right is the scaffold carrying it. One picture, the
 *              whole argument.
 *
 * Nothing here computes a number. Every value is the section's, and a
 * quantity the section marked unmeasurable is drawn as absent rather than
 * as zero.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var L = AgentDiff.lib;
  var d3 = global.d3;
  var isNum = L.fmt.isNum, num = L.fmt.num, pct = L.fmt.pct, plural = L.fmt.plural;
  var signed = L.fmt.signed, trunc = L.fmt.trunc, join = function (a) { return (a || []).join(", "); };

  //: the three attribution statuses, in the order they degrade
  var STATUS = {
    attributable: { label: "attributable", glyph: "●", why: "a harness fingerprint on both sides, and it held" },
    assumed: { label: "assumed", glyph: "◐", why: "rests on something the traces cannot check" },
    confounded: { label: "confounded", glyph: "◌", why: "the harness moved too; the delta is neither side's" },
  };
  var KIND_ORDER = ["reasoning", "scaffold", "mixed", "none", "unreadable"];

  function ensureStyle() {
    L.style.once("harness", [
      ".hn-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:95ch}",
      ".hn-note{font-size:var(--fs-xs);color:var(--ink-2);margin:8px 0 0;max-width:95ch}",
      ".hn-nums{font-size:var(--fs-xs);color:var(--ink-2);font-family:var(--mono);font-variant-numeric:tabular-nums;margin:0 0 8px}",
      ".hn-nums b{color:var(--ink)}.hn-nums .sep{color:var(--ink-3);margin:0 6px}",
      ".hn-rows{display:flex;flex-direction:column;gap:2px;margin:6px 0 0}",
      ".hn-row{display:grid;grid-template-columns:72px 1fr auto;gap:8px;align-items:center;padding:4px 6px;"
        + "border:1px solid transparent;border-radius:7px;cursor:pointer;background:none;text-align:left;width:100%;font:inherit;color:inherit}",
      ".hn-row:hover,.hn-row[aria-expanded=true]{border-color:var(--rule-2);background:var(--surface-2)}",
      ".hn-row:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
      ".hn-step{font:600 var(--fs-xs)/1.4 var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}",
      ".hn-chips{display:flex;flex-wrap:wrap;gap:4px;align-items:center}",
      ".hn-chip{font:500 var(--fs-xs)/16px var(--sans);border-radius:5px;padding:0 6px;white-space:nowrap}",
      ".hn-chip.reasoning{background:color-mix(in srgb,var(--a) 18%,var(--surface));color:var(--ink)}",
      ".hn-chip.scaffold{background:color-mix(in srgb,var(--b) 18%,var(--surface));color:var(--ink)}",
      ".hn-chip.absorb{background:color-mix(in srgb,var(--warn,#b8860b) 20%,var(--surface));color:var(--ink)}",
      ".hn-status{font:500 var(--fs-xs)/1.4 var(--sans);color:var(--ink-2);white-space:nowrap}",
      ".hn-status .g{font-size:1.1em;margin-right:3px}",
      ".hn-status.confounded{color:var(--bad,#b3261e)}",
      ".hn-panel{border-left:2px solid var(--rule-2);margin:2px 0 6px 10px;padding:4px 0 4px 10px;font-size:var(--fs-xs);color:var(--ink-2)}",
      ".hn-panel p{margin:0 0 4px;max-width:90ch}.hn-panel p:last-child{margin:0}",
      ".hn-panel b{color:var(--ink)}",
      ".hn-legend{display:flex;flex-wrap:wrap;gap:10px;font-size:var(--fs-xs);color:var(--ink-2);margin:8px 0 0}",
      ".hn-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}",
      ".hn-stage{width:100%}",
      ".hn-tip{font-size:var(--fs-xs)}",
      ".hn-details{margin-top:8px}.hn-details summary{cursor:pointer;color:var(--ink-2);font-size:var(--fs-xs)}",
      ".hn-table{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:6px}",
      ".hn-table th,.hn-table td{padding:2px 8px;border-bottom:1px solid var(--rule);text-align:left;white-space:nowrap}",
      ".hn-table th{color:var(--ink-2);font-weight:600}.hn-table td.num,.hn-table th.num{text-align:right;font-family:var(--mono)}",
    ]);
  }

  // ----------------------------------------------------------------- model

  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {};
    if (cache && cache.agg === agg) return cache.m;
    var m;
    try { m = build(agg); }
    catch (err) { if (global.console) console.warn("AgentDiff harness: model failed", err); m = { ok: false, reason: "the harness section could not be read" }; }
    cache = { agg: agg, m: m };
    return m;
  }

  function build(agg) {
    var h = agg && agg.harness_evolution;
    if (!h || typeof h !== "object") return { ok: false, reason: null };
    if (h.measurable === false) return { ok: false, reason: h.reason || "the harness could not be read" };
    var steps = (Array.isArray(h.steps) ? h.steps : []).filter(function (s) { return s && s.from && s.to; });
    if (!steps.length) return { ok: false, reason: "the lineage carries no step to read a harness across" };
    /* The plane's points are generations, and the section stores each
     * quantity per *step* as a from/to pair — so a generation's point is
     * the `from` of the step leaving it, and the last one is the final
     * `to`. Chained this way the two readings of a shared generation are
     * the same number by construction; where a step is unmeasurable the
     * point is simply absent and the block says how many are. */
    var points = [], seen = {};
    function put(id, rate, work, n) {
      if (seen[id] || !isNum(rate) || !isNum(work)) return;
      seen[id] = true;
      points.push({ id: id, rate: rate, work: work, n: isNum(n) ? n : null, i: points.length });
    }
    steps.forEach(function (s, k) {
      var a = s.absorption || {};
      if (!a.measurable) return;
      var pr = a.pass_rate || {}, sp = a.steps_per_pass || {};
      put(s.from, pr.from, (sp.from || {}).point, (sp.from || {}).n);
      if (k === steps.length - 1 || !((steps[k + 1] || {}).absorption || {}).measurable) {
        put(s.to, pr.to, (sp.to || {}).point, (sp.to || {}).n);
      }
    });
    var index = {};
    points.forEach(function (p, i) { p.i = i; index[p.id] = p; });
    var edges = steps.map(function (s) {
      var a = s.absorption || {};
      return { step: s, from: index[s.from] || null, to: index[s.to] || null,
               flag: !!a.flag, kind: s.kind, status: (s.attribution || {}).status };
    }).filter(function (e) { return e.from && e.to; });
    return { ok: true, h: h, steps: steps, points: points, edges: edges,
             summary: h.summary || {}, gens: Array.isArray(h.generations) ? h.generations : [],
             kinds: h.kinds || {}, gap: h.gap || null,
             unmeasured: steps.filter(function (s) { return !(s.absorption || {}).measurable; }) };
  }

  function statusOf(step) { return STATUS[(step.attribution || {}).status] || STATUS.assumed; }

  // ------------------------------------------------------------ hn-ladder

  AgentDiff.block({
    id: "hn-ladder",
    title: "May the delta be attributed",
    question: "Each step changed the agent — but did the thing that ran it stay the same?",
    group: "evolution",
    size: "wide",
    relevance: function (ctx) { return model(ctx).ok ? 0.885 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      if (!m.ok) return ctx.empty(el, m.reason || "This output carries no harness reading.");
      var root = H("div", { class: "hn" });
      el.appendChild(root);
      var s = m.summary;

      var counts = s.by_kind || {};
      var shape = KIND_ORDER.filter(function (k) { return counts[k]; })
        .map(function (k) { return counts[k] + " " + k; });
      root.appendChild(H("p", { class: "hn-lede", text:
        "Four of a generation's artifacts are how the agent thinks; " +
        "tools and config are the scaffold it runs inside. " +
        (shape.length ? "Of " + plural(m.steps.length, "step") + ", " + join(shape) + "." : "") +
        " A delta is only the agent's if what ran the two generations was the same thing." }));

      var nums = H("p", { class: "hn-nums" });
      [["fingerprinted", (s.generations_fingerprinted || 0) + " of " + (s.generations || 0)],
       ["attributable", String((s.attributable || []).length)],
       ["assumed", String((s.assumed || []).length)],
       ["confounded", String((s.confounded || []).length)],
       ["scaffold-carried", String((s.absorbed || []).length)]].forEach(function (p, i) {
        if (i) nums.appendChild(H("span", { class: "sep", text: "·" }));
        nums.appendChild(H("span", { text: p[0] + " " }));
        nums.appendChild(H("b", { text: p[1] }));
      });
      root.appendChild(nums);

      var rows = H("div", { class: "hn-rows", role: "list" });
      root.appendChild(rows);
      m.steps.forEach(function (step) {
        var st = statusOf(step), changed = step.changed || {};
        var wrap = H("div", { role: "listitem" });
        var panel = H("div", { class: "hn-panel", hidden: true });
        var chips = H("span", { class: "hn-chips" });
        (changed.reasoning || []).forEach(function (k) { chips.appendChild(H("span", { class: "hn-chip reasoning", text: k })); });
        (changed.scaffold || []).forEach(function (k) { chips.appendChild(H("span", { class: "hn-chip scaffold", text: k })); });
        if (!(changed.reasoning || []).length && !(changed.scaffold || []).length) {
          chips.appendChild(H("span", { class: "hn-chip", text: "no artifact changed" }));
        }
        if ((step.absorption || {}).flag) chips.appendChild(H("span", { class: "hn-chip absorb", text: "scaffold carried it" }));
        var row = H("button", {
          type: "button", class: "hn-row", "data-step": step.from + "→" + step.to,
          "data-status": (step.attribution || {}).status, "aria-expanded": "false",
          title: st.why,
          onclick: function () {
            var open = row.getAttribute("aria-expanded") === "true";
            row.setAttribute("aria-expanded", open ? "false" : "true");
            panel.hidden = open;
          },
        }, [
          H("span", { class: "hn-step", text: step.from + "→" + step.to }),
          chips,
          H("span", { class: "hn-status " + ((step.attribution || {}).status || ""), "data-role": "status" },
            [H("span", { class: "g", text: st.glyph }), H("span", { text: st.label })]),
        ]);
        wrap.appendChild(row);

        // the evidence, on demand: the sentence, then what moved and what did not
        panel.appendChild(H("p", { text: step.reading || "" }));
        var move = step.harness || {};
        if ((move.changes || []).length) {
          panel.appendChild(H("p", {}, [H("b", { text: "the harness moved: " }),
            H("span", { text: move.changes.map(function (c) { return c.what + " " + c.from + " → " + c.to; }).join("; ") })]));
        }
        if ((move.explained || []).length) {
          panel.appendChild(H("p", {}, [H("b", { text: "the agent's own scaffold, not the environment: " }),
            H("span", { text: move.explained.map(function (c) { return c.what + " " + c.from + " → " + c.to; }).join("; ")
                              + " — " + (move.explained[0].note || "") })]));
        }
        if (((move.identity || {}).changes || []).length) {
          panel.appendChild(H("p", {}, [H("b", { text: "the model string: " }),
            H("span", { text: move.identity.changes.map(function (c) { return c.what + " " + c.from + " → " + c.to; }).join("; ")
                              + " — " + (move.identity.note || "") })]));
        }
        if (step.base_verdict) {
          panel.appendChild(H("p", { text: "The Evolution reading above calls this step " + step.base_verdict
            + ((step.base_flags || []).length ? " (" + join(step.base_flags) + ")" : "") + "." }));
        }
        wrap.appendChild(panel);
        rows.appendChild(wrap);
      });

      var leg = H("div", { class: "hn-legend" });
      leg.appendChild(H("span", {}, [H("i", { style: "background:color-mix(in srgb,var(--a) 55%,var(--surface))" }),
                                     H("span", { text: "reasoning — prompt, rules, skills, memory" })]));
      leg.appendChild(H("span", {}, [H("i", { style: "background:color-mix(in srgb,var(--b) 55%,var(--surface))" }),
                                     H("span", { text: "scaffold — tools, config" })]));
      Object.keys(STATUS).forEach(function (k) {
        leg.appendChild(H("span", { text: STATUS[k].glyph + " " + STATUS[k].label + " — " + STATUS[k].why }));
      });
      root.appendChild(leg);
      root.appendChild(H("p", { class: "hn-note", text: s.reading || "" }));
      root.appendChild(fingerprintTable(H, m));
    },
  });

  function fingerprintTable(H, m) {
    var head = ["generation", "episodes", "fingerprint", "tools offered", "caps", "token basis", "decoding"];
    var tbl = H("table", { class: "hn-table" });
    tbl.appendChild(H("thead", null, H("tr", null, head.map(function (c) {
      return H("th", { class: c === "episodes" ? "num" : null, text: c });
    }))));
    var body = H("tbody");
    m.gens.forEach(function (g) {
      var fp = g.fingerprint || {};
      var dec = (fp.decoding || []).map(function (d) {
        return d.name + (isNum(d.temperature) ? " @" + num(d.temperature, 2) : "");
      }).join(", ");
      var caps = Object.keys(fp.caps || {}).map(function (k) { return k + " " + num((fp.caps || {})[k]); }).join(", ");
      body.appendChild(H("tr", { "data-gen": g.id }, [
        g.id, String(g.episodes == null ? "—" : g.episodes),
        fp.measurable ? String(fp.digest || "").slice(0, 10) : "not recorded",
        join(fp.tools_offered || []) || "—", caps || "—",
        join(fp.token_basis || []) || "—", dec || "—",
      ].map(function (v, i) { return H("td", { class: i === 1 ? "num" : null, text: String(v) }); })));
    });
    tbl.appendChild(body);
    return H("details", { class: "hn-details", "data-role": "fingerprints" }, [
      H("summary", { text: "the fingerprint of each generation: what the traces recorded about the thing that ran it" }),
      H("div", { class: "scroll-x" }, tbl),
    ]);
  }

  // ------------------------------------------------------------ hn-absorb

  AgentDiff.block({
    id: "hn-absorb",
    title: "Where the gain came from",
    question: "Did the agent need less, or was more put around it?",
    group: "evolution",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.points.length > 1 ? 0.884 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      if (!m.ok) return ctx.empty(el, m.reason || "This output carries no harness reading.");
      if (m.points.length < 2) {
        return ctx.empty(el, "Fewer than two generations carry the passing episodes this plane needs: "
          + plural(m.unmeasured.length, "step") + " could not be measured.");
      }
      var root = H("div", { class: "hn" });
      el.appendChild(root);
      root.appendChild(H("p", { class: "hn-lede", text:
        "Each generation is a point: how much work a passing episode cost, against how often it passed. "
        + "The lineage is the path between them. Up and to the left is the agent needing less; "
        + "up and to the right is the outcome improving while each success costs more — a gain from the "
        + "scaffold, which is not a fault but does not travel with the agent." }));

      var stage = H("div", { class: "hn-stage" });
      root.appendChild(stage);
      L.layout.responsive(stage, function () { stage.innerHTML = ""; draw(stage, m); }, "harness-absorb");

      var absorbed = (m.summary.absorbed || []);
      root.appendChild(H("p", { class: "hn-note", text: absorbed.length
        ? "The marked leg" + (absorbed.length > 1 ? "s are" : " is") + " " + join(absorbed)
          + ": the pass rate rose and the work per pass rose with it."
        : "No leg of this lineage rose on both axes at once, so no gain here reads as the scaffold's." }));
      root.appendChild(H("p", { class: "hn-note", "data-role": "axes", text:
        "Both axes are the data's own range, not 0 to 100% \u2014 the page's rule is that an axis starts where "
        + "the data starts, and the table below carries the values." }));
      if (m.unmeasured.length) {
        root.appendChild(H("p", { class: "hn-note", text: plural(m.unmeasured.length, "step")
          + " is not on the plane: " + trunc((m.unmeasured[0].absorption || {}).reason || "", 160) }));
      }
      root.appendChild(pointTable(H, m));
    },
  });

  function draw(host, m) {
    var avail = L.layout.measure(host, 280, 1100);
    var narrow = avail < 520;
    /* A plane where both axes carry the argument reads badly stretched:
     * full-bleed width against a capped height leaves most of the picture
     * empty and pushes the legs into one corner. So the plot is bounded
     * and roughly square, and the space beside it is where the two
     * directions are named rather than left to be inferred. */
    var side = Math.max(200, Math.min(narrow ? avail - 8 : 580, avail - (narrow ? 8 : 260)));
    var pad = { t: 14, r: narrow ? 46 : 12, b: 38, l: 50 };
    var iw = side, ih = Math.round(side * 0.78);
    var legend = narrow ? 0 : 190;
    //: the viewBox spans the whole host, and the chart plus its legend is
    //: centred inside it as one unit — a viewBox narrower than the host
    //: letterboxes to the left and leaves a third of the block empty
    var width = avail, height = ih + pad.t + pad.b;
    var offset = Math.max(0, Math.round((avail - (iw + pad.l + pad.r + legend)) / 2));
    var works = m.points.map(function (p) { return p.work; });
    var rates = m.points.map(function (p) { return p.rate; });
    //: the data's own extent, padded — the page's rule is that an axis
    //: starts where the data starts, and a rate axis pinned to 0–100%
    //: would leave two thirds of this picture empty. The note under the
    //: chart says the axis does not start at zero.
    var wx = d3.extent(works), wy = d3.extent(rates);
    var wpad = Math.max(0.5, (wx[1] - wx[0]) * 0.12), rpad = Math.max(0.03, (wy[1] - wy[0]) * 0.12);
    var x = d3.scaleLinear().domain([wx[0] - wpad, wx[1] + wpad]).nice().range([0, iw]);
    var y = d3.scaleLinear().domain([Math.max(0, wy[0] - rpad), Math.min(1, wy[1] + rpad)]).nice().range([ih, 0]);

    var absorbedLabel = (m.summary.absorbed || []).length
      ? " The scaffold carried " + join(m.summary.absorbed || []) + "." : "";
    var label = "Work per pass against pass rate for " + plural(m.points.length, "generation") + ": "
      + m.points.map(function (p) { return p.id + " " + num(p.work, 1) + " steps at " + pct(p.rate); }).join("; ")
      + "." + absorbedLabel;
    var svg = d3.select(L.svg({ viewBox: "0 0 " + width + " " + height, width: "100%", height: height,
                               "aria-label": label }));
    var g = svg.append("g").attr("transform", "translate(" + (offset + pad.l) + "," + pad.t + ")");
    var tip = L.svg.tip(host, { class: "hn-tip" });

    g.append("g").attr("transform", "translate(0," + ih + ")")
      .call(d3.axisBottom(x).ticks(narrow ? 4 : 6).tickSizeOuter(0))
      .call(function (s) { s.selectAll("text").attr("font-size", 11).attr("fill", "var(--ink-2)"); })
      .call(function (s) { s.selectAll("line,path").attr("stroke", "var(--rule-2)"); });
    g.append("g").call(d3.axisLeft(y).ticks(4).tickFormat(function (v) { return pct(v); }).tickSizeOuter(0))
      .call(function (s) { s.selectAll("text").attr("font-size", 11).attr("fill", "var(--ink-2)"); })
      .call(function (s) { s.selectAll("line,path").attr("stroke", "var(--rule-2)"); });
    g.append("text").attr("x", iw).attr("y", ih + 31).attr("text-anchor", "end")
      .attr("font-size", 11).attr("fill", "var(--ink-2)").text("steps per passing episode \u2192");
    g.append("text").attr("transform", "rotate(-90)").attr("x", 0).attr("y", -37)
      .attr("text-anchor", "end").attr("font-size", 11).attr("fill", "var(--ink-2)").text("pass rate \u2192");

    var defs = svg.append("defs");
    [["hn-arrow", "var(--ink-3)"], ["hn-arrow-absorb", "var(--warn,#b8860b)"]].forEach(function (a) {
      defs.append("marker").attr("id", a[0]).attr("viewBox", "0 0 8 8").attr("refX", 7).attr("refY", 4)
        .attr("markerWidth", 6).attr("markerHeight", 6).attr("orient", "auto-start-reverse")
        .append("path").attr("d", "M0,0 L8,4 L0,8 Z").attr("fill", a[1]);
    });
    g.selectAll("line.leg").data(m.edges).join("line")
      .attr("class", "leg").attr("data-leg", function (e) { return e.step.from + "\u2192" + e.step.to; })
      .attr("data-absorb", function (e) { return e.flag ? "true" : "false"; })
      .attr("x1", function (e) { return x(e.from.work); }).attr("y1", function (e) { return y(e.from.rate); })
      .attr("x2", function (e) { return x(e.to.work); }).attr("y2", function (e) { return y(e.to.rate); })
      .attr("stroke", function (e) { return e.flag ? "var(--warn,#b8860b)" : "var(--ink-3)"; })
      .attr("stroke-width", function (e) { return e.flag ? 2.5 : 1.4; })
      .attr("marker-end", function (e) { return "url(#" + (e.flag ? "hn-arrow-absorb" : "hn-arrow") + ")"; })
      .attr("opacity", 0.85);

    /* Labels collide wherever the lineage doubles back on itself, which is
     * exactly where it is most interesting. Each one is placed on the side
     * with more room and nudged off any label already down. */
    var placed = [];
    m.points.forEach(function (p) {
      p.px = x(p.work); p.py = y(p.rate);
      var left = p.px > iw - 42;
      var lx = p.px + (left ? -9 : 9), ly = p.py + 4;
      for (var guard = 0; guard < 6; guard++) {
        var clash = placed.some(function (q) { return Math.abs(q.lx - lx) < 26 && Math.abs(q.ly - ly) < 12; });
        if (!clash) break;
        ly += 12;
      }
      p.lx = lx; p.ly = ly; p.anchor = left ? "end" : "start";
      placed.push(p);
    });
    var pts = g.selectAll("g.pt").data(m.points).join("g").attr("class", "pt")
      .attr("data-gen", function (p) { return p.id; });
    pts.append("circle").attr("cx", function (p) { return p.px; }).attr("cy", function (p) { return p.py; })
      .attr("r", 5).attr("fill", "var(--surface)").attr("stroke", "var(--ink)").attr("stroke-width", 1.6);
    pts.append("text").attr("x", function (p) { return p.lx; }).attr("y", function (p) { return p.ly; })
      .attr("text-anchor", function (p) { return p.anchor; })
      .attr("font-size", 11).attr("fill", "var(--ink-2)").text(function (p) { return p.id; });
    pts.append("circle").attr("cx", function (p) { return p.px; }).attr("cy", function (p) { return p.py; })
      .attr("r", 12).attr("fill", "transparent")
      .on("mousemove", function (ev, p) {
        tip.show(ev, [{ title: p.id },
                      { text: pct(p.rate) + " of its episodes passed" },
                      { mono: true, text: num(p.work, 2) + " steps per pass"
                                          + (p.n ? " over " + plural(p.n, "pass") : "") }]);
      })
      .on("mouseleave", function () { tip.hide(); });

    if (!narrow) {
      var noteX = iw + 26;
      [["\u2196 the agent", "needs less, passes more", "var(--ink-2)"],
       ["\u2197 the scaffold", "passes more, costs more", "var(--warn,#b8860b)"]].forEach(function (t, i) {
        var yy = 14 + i * 36;
        g.append("text").attr("x", noteX).attr("y", yy).attr("font-size", 11)
          .attr("fill", t[2]).attr("font-weight", 600).text(t[0]);
        g.append("text").attr("x", noteX).attr("y", yy + 13).attr("font-size", 10.5)
          .attr("fill", "var(--ink-3)").text(t[1]);
      });
    }
    host.appendChild(svg.node());
  }

  function pointTable(H, m) {
    var tbl = H("table", { class: "hn-table" });
    tbl.appendChild(H("thead", null, H("tr", null,
      ["generation", "pass rate", "steps per pass", "passes"].map(function (c, i) {
        return H("th", { class: i ? "num" : null, text: c });
      }))));
    var body = H("tbody");
    m.points.forEach(function (p) {
      body.appendChild(H("tr", { "data-row-gen": p.id }, [
        H("td", { text: p.id }),
        H("td", { class: "num", text: pct(p.rate) }),
        H("td", { class: "num", text: num(p.work, 2) }),
        H("td", { class: "num", text: p.n == null ? "—" : String(p.n) }),
      ]));
    });
    tbl.appendChild(body);
    return H("details", { class: "hn-details", "data-role": "points" }, [
      H("summary", { text: "table view: " + plural(m.points.length, "generation") + " on the plane" }),
      H("div", { class: "scroll-x" }, tbl),
    ]);
  }
})(typeof window !== "undefined" ? window : this);
