/* AgentDiff block — did the same thing run these generations?
 *
 * `hn-ladder` above says, per step, whether the delta may be attributed.
 * It cannot say *where* the harness moved, or which dimensions the traces
 * never recorded in the first place — and an unrecorded dimension is the
 * commonest case and the easiest one to mistake for "it held constant".
 *
 * So: a matrix. One row per dimension of the fingerprint
 * (`harnessevo.DIMENSIONS`), one column per generation, and under it a
 * band per *step*, centred on the boundary between the two columns it
 * joins, carrying the attribution verdict for that step. The two scales are
 * aligned on purpose: a cell marked "moved" sits directly above the step
 * whose delta it confounds, so the argument is one glance rather than two
 * lookups.
 *
 * Three cell states, and the third is the point:
 *
 *   held        recorded on both sides and the same
 *   moved       recorded and different — the value from and to on hover
 *   unrecorded  no episode of this generation wrote it down. Hatched, and
 *               never drawn as "held": not knowing is not the same as
 *               knowing it did not change, and the whole section exists
 *               because those two get confused.
 *
 * Nothing here computes. `dimension` on a change row and `dimensions` on a
 * fingerprint are the engine's, added so this block need not match English
 * to know which row a change belongs to.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var L = AgentDiff.lib;
  var d3 = global.d3;
  var plural = L.fmt.plural, trunc = L.fmt.trunc;

  //: the rows, in the order a reader wants them: what served the run, what
  //: it was offered, what the loop enforced, the two contract-shaped ones,
  //: and the names last because they are not the harness proper
  var ROWS = [
    { key: "decoding", label: "decoding", why: "how the model was sampled" },
    { key: "tools_offered", label: "tools offered", why: "the tool table the runner offered" },
    { key: "caps", label: "caps", why: "the settings the loop enforced" },
    { key: "token_basis", label: "token basis", why: "how the token counts were obtained" },
    { key: "schema_versions", label: "trace schema", why: "the trace contract written against" },
    { key: "identity", label: "model name", why: "the declared model and version — the names, not the harness" },
  ];
  var STATUS = {
    attributable: { glyph: "●", fill: "var(--good)", label: "attributable" },
    assumed: { glyph: "◐", fill: "var(--warn)", label: "assumed" },
    confounded: { glyph: "◌", fill: "var(--bad)", label: "confounded" },
  };

  function ensureStyle() {
    L.style.once("harness-drift", [
      ".hnd{position:relative}",
      ".hnd-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:95ch}",
      ".hnd-chart{width:100%}.hnd-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".hnd-row-label{font:500 11px var(--sans);fill:var(--ink-2)}",
      ".hnd-gen{font:600 11px var(--mono);fill:var(--ink-3)}",
      ".hnd-cell{cursor:default}",
      ".hnd-cell rect{stroke:var(--rule-2);stroke-width:1}",
      ".hnd-cell.moved rect{stroke:var(--ink);stroke-width:1.5}",
      ".hnd-cell text{font:600 10px var(--mono);pointer-events:none}",
      ".hnd-step{cursor:pointer}",
      ".hnd-step text{font:600 11px var(--sans);text-anchor:middle}",
      ".hnd-step.sel rect{stroke:var(--accent);stroke-width:2}",
      ".hnd-legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:var(--fs-xs);color:var(--ink-2);margin:8px 0 0}",
      ".hnd-legend i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-2px;border:1px solid var(--rule-2)}",
      ".hnd-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:95ch}",
      ".hnd-panel{border:1px solid var(--rule);border-radius:8px;padding:8px 10px;margin:8px 0 0;background:var(--surface);font-size:var(--fs-s)}",
      ".hnd-panel h5{margin:0 0 4px;font:600 var(--fs-s)/1.3 var(--sans);color:var(--ink)}",
      ".hnd-panel p{margin:2px 0;color:var(--ink-2);font-size:var(--fs-xs);max-width:90ch}",
      ".hnd-panel .chg{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink)}",
    ].join(""));
  }

  function model(ctx) {
    var h = (ctx.aggregate || {}).harness_evolution;
    if (!h || !h.measurable || !(h.generations || []).length) return null;
    var gens = (h.generations || []).map(function (g) {
      var fp = g.fingerprint || {};
      return { id: g.id, index: g.index, measurable: !!fp.measurable,
               dims: fp.dimensions || {}, fp: fp };
    });
    // a step's changes, keyed by the dimension the engine put on them
    var steps = (h.steps || []).map(function (s) {
      var hm = s.harness || {}, by = {};
      ((hm.changes || []).concat((hm.identity || {}).changes || [])).forEach(function (c) {
        if (!c || !c.dimension) return;
        (by[c.dimension] = by[c.dimension] || []).push(c);
      });
      return { from: s.from, to: s.to, index: s.index, by: by,
               status: (s.attribution || {}).status || "assumed",
               reason: (s.attribution || {}).reason || "",
               basis: (s.attribution || {}).basis || "",
               moved: hm.moved, explained: hm.explained || [],
               reading: s.reading || "" };
    });
    if (gens.length < 2) return null;
    return { gens: gens, steps: steps, summary: h.summary || {} };
  }

  //: the state of one dimension at one generation, and which step (if any)
  //: moved it *into* this generation
  function cellOf(m, row, gi) {
    var gen = m.gens[gi];
    if (!gen.measurable || gen.dims[row.key] === false) return { state: "unrecorded" };
    var step = gi > 0 ? m.steps[gi - 1] : null;
    var changes = step ? (step.by[row.key] || []) : [];
    return { state: changes.length ? "moved" : (gi === 0 ? "first" : "held"), changes: changes, step: step };
  }

  function fillFor(state) {
    if (state === "unrecorded") return "url(#hnd-hatch)";
    if (state === "moved") return "color-mix(in srgb, var(--warn) 45%, var(--surface))";
    if (state === "first") return "var(--surface-2)";
    return "color-mix(in srgb, var(--good) 14%, var(--surface))";
  }

  function draw(host, m, state, onPick) {
    host.innerHTML = "";
    if (!d3) return;
    var W = L.layout.measure(host, 320, 1400);
    var labelW = Math.min(120, Math.max(72, Math.round(W * 0.14)));
    var bandH = 26, rowH = Math.max(20, Math.min(30, Math.round((W - labelW) / Math.max(6, m.gens.length) * 0.55)));
    var headH = 16, H = headH + ROWS.length * rowH + bandH + 18;
    var x = d3.scaleBand().domain(m.gens.map(function (g) { return g.id; }))
      .range([labelW, W - 2]).paddingInner(0.12).paddingOuter(0.04);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img")
      .attr("aria-label", "each dimension of the harness fingerprint across "
        + plural(m.gens.length, "generation") + ", marking where what ran the agent moved, "
        + "with each step's attribution verdict beneath the gap it spans");

    // hatching for "no episode wrote this down" — a fill that reads as
    // absent rather than as a value
    var defs = svg.append("defs");
    var pat = defs.append("pattern").attr("id", "hnd-hatch").attr("width", 5).attr("height", 5)
      .attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 5).attr("height", 5).attr("fill", "var(--surface)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5)
      .attr("stroke", "var(--ink-3)").attr("stroke-width", 1).attr("stroke-opacity", 0.45);

    m.gens.forEach(function (g) {
      svg.append("text").attr("class", "hnd-gen").attr("x", x(g.id) + x.bandwidth() / 2)
        .attr("y", 11).attr("text-anchor", "middle").text(g.id);
    });

    ROWS.forEach(function (row, ri) {
      var y = headH + ri * rowH;
      svg.append("text").attr("class", "hnd-row-label").attr("x", 0).attr("y", y + rowH / 2 + 4)
        .text(trunc(row.label, Math.max(6, Math.floor(labelW / 6.2))))
        .append("title").text(row.label + " — " + row.why);
      m.gens.forEach(function (g, gi) {
        var c = cellOf(m, row, gi);
        var cell = svg.append("g").attr("class", "hnd-cell" + (c.state === "moved" ? " moved" : ""))
          .attr("data-dimension", row.key).attr("data-gen", g.id).attr("data-state", c.state)
          .attr("transform", "translate(" + x(g.id) + "," + (y + 2) + ")");
        cell.append("rect").attr("width", x.bandwidth()).attr("height", rowH - 4).attr("rx", 3)
          .attr("fill", fillFor(c.state));
        if (c.state === "moved" && x.bandwidth() >= 18) {
          cell.append("text").attr("x", x.bandwidth() / 2).attr("y", rowH / 2 + 1)
            .attr("text-anchor", "middle").attr("fill", "var(--ink)").text("→");
        }
        cell.append("title").text(
          row.label + " at " + g.id + " — "
          + (c.state === "unrecorded" ? "no episode of this generation recorded it, so whether it moved is unknown"
             : c.state === "first" ? "the first generation; there is nothing before it to differ from"
             : c.state === "moved"
               ? "moved: " + c.changes.map(function (ch) { return ch.what + " " + ch.from + " → " + ch.to; }).join("; ")
               : "recorded and unchanged from " + m.gens[gi - 1].id));
      });
    });

    // the steps, each in the gap between the two generations it joins: the
    // verdict sits directly under the cells that decided it
    var by = headH + ROWS.length * rowH + 2;
    m.steps.forEach(function (s, i) {
      var left = x(m.gens[i].id) + x.bandwidth(), right = x(m.gens[i + 1].id);
      // width from the *spacing*, never from the bandwidth: at phone width
      // a fixed minimum made neighbouring bands overlap and their labels
      // run into each other
      var cx = (left + right) / 2, w = Math.min(x.bandwidth() * 0.95, Math.max(22, x.step() - 6));
      var label = s.from + "\u2192" + s.to;
      var full = w >= (label.length + 3) * 6.2;
      var st = STATUS[s.status] || STATUS.assumed;
      var g = svg.append("g").attr("class", "hnd-step" + (state.step === s.index ? " sel" : ""))
        .attr("data-step", s.from + "→" + s.to).attr("data-status", s.status)
        .attr("transform", "translate(" + (cx - w / 2) + "," + by + ")")
        .on("click", function () { onPick(state.step === s.index ? null : s.index); });
      g.append("rect").attr("width", w).attr("height", bandH - 6).attr("rx", 4)
        .attr("fill", "color-mix(in srgb, " + st.fill + " 22%, var(--surface))")
        .attr("stroke", "var(--rule-2)");
      g.append("text").attr("x", w / 2).attr("y", bandH / 2 + 1).attr("fill", "var(--ink)")
        .text(full ? st.glyph + " " + label : st.glyph);
      g.append("title").text(s.from + "→" + s.to + " — " + st.label + ": " + s.reason);
    });
  }

  function panel(H, m, index) {
    var s = m.steps.filter(function (x) { return x.index === index; })[0];
    if (!s) return null;
    var st = STATUS[s.status] || STATUS.assumed;
    var box = H("div", { class: "hnd-panel", "data-step": s.from + "→" + s.to });
    box.appendChild(H("h5", { text: s.from + " → " + s.to + " · " + st.label }));
    box.appendChild(H("p", { text: s.reason }));
    var rows = [];
    ROWS.forEach(function (row) {
      (s.by[row.key] || []).forEach(function (c) {
        rows.push(row.label + ": " + c.what + " — " + c.from + " → " + c.to);
      });
    });
    if (rows.length) {
      rows.forEach(function (t) { box.appendChild(H("p", { class: "chg", text: t })); });
    } else {
      box.appendChild(H("p", { text: "Nothing the fingerprint reads moved across this step." }));
    }
    (s.explained || []).forEach(function (e) {
      box.appendChild(H("p", { text: "explained: " + (e.what || "") + " — " + (e.note || "") }));
    });
    if (s.reading) box.appendChild(H("p", { text: s.reading }));
    return box;
  }

  AgentDiff.block({
    id: "hn-drift",
    title: "Did the same thing run these generations?",
    question: "Where did the harness move across the lineage, and which dimensions did nothing record?",
    group: "evolution",
    size: "wide",

    relevance: function (ctx) { return model(ctx) ? 0.88 : 0; },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      if (!m) return ctx.empty(el, "This output carries no harness fingerprint across two or more generations.");
      var root = H("div", { class: "hnd" });
      el.appendChild(root);
      var fam = L.family("harness-drift", { step: null }, { scope: "page", persist: false });

      var s = m.summary;
      var unrecorded = ROWS.filter(function (row) {
        return m.gens.every(function (g) { return !g.measurable || g.dims[row.key] === false; });
      });
      root.appendChild(H("p", { class: "hnd-lede", text:
        "Each row is one dimension of what ran the agent, read from the episodes and never from the manifest; "
        + "each column a generation; each band a step, centred on the boundary between the two it joins. "
        + (unrecorded.length
           ? plural(unrecorded.length, "dimension") + " (" + unrecorded.map(function (r) { return r.label; }).join(", ")
             + ") no episode recorded at all, so whether "
             + (unrecorded.length === 1 ? "it moved is" : "they moved is") + " unknown rather than settled — "
             + "drawn hatched, never as held."
           : "Every dimension is recorded on every generation, which is rarer than it should be.") }));

      var chart = H("div", { class: "hnd-chart" });
      root.appendChild(chart);
      var detail = H("div");

      function paint() {
        L.layout.responsive(chart, function () {
          draw(chart, m, { step: fam.get().step }, function (index) { fam.set({ step: index }); });
        }, "harness-drift");
        detail.innerHTML = "";
        var index = fam.get().step;
        if (index !== null && index !== undefined) {
          var p = panel(H, m, index);
          if (p) detail.appendChild(p);
        }
      }
      // `subscribe(fn, el)` drops the subscriber when the element leaves
      // the page, so a re-render does not stack a listener per render
      fam.subscribe(paint, root);
      paint();
      root.appendChild(detail);

      var leg = H("div", { class: "hnd-legend" });
      [["recorded and unchanged", "color-mix(in srgb, var(--good) 14%, var(--surface))"],
       ["moved", "color-mix(in srgb, var(--warn) 45%, var(--surface))"],
       ["the first generation", "var(--surface-2)"]].forEach(function (pair) {
        leg.appendChild(H("span", {}, [H("i", { style: "background:" + pair[1] }), H("span", { text: pair[0] })]));
      });
      leg.appendChild(H("span", {}, [
        H("i", { style: "background:repeating-linear-gradient(45deg,var(--surface),var(--surface) 2px,var(--ink-3) 2px,var(--ink-3) 3px)" }),
        H("span", { text: "no episode recorded it" })]));
      Object.keys(STATUS).forEach(function (k) {
        if (!m.steps.some(function (s2) { return s2.status === k; })) return;
        leg.appendChild(H("span", { text: STATUS[k].glyph + " " + STATUS[k].label }));
      });
      root.appendChild(leg);

      root.appendChild(H("p", { class: "hnd-note", text:
        "A hatched cell is the finding, not a gap in the drawing: an unrecorded dimension is unknown, and reading "
        + "it as held is the mistake this whole section exists to stop. Click a step for what moved and the "
        + "sentence behind its verdict. Which row a change belongs to is the engine's own `dimension`, so this "
        + "picture cannot disagree with the ladder above it." }));
    },
  });
})(typeof window !== "undefined" ? window : this);
