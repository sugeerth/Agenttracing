/* AgentDiff block — what this harness can act on.
 *
 * The scaffold actuator's central claim is that a hypothesis the runner
 * cannot express is not a hypothesis: it goes in `unactionable` with the
 * reason, where it can be counted. That claim is about the *whole*
 * recommendation vocabulary — every category `triage.EFFORT` can produce —
 * and not about whichever findings a particular batch happened to turn up.
 * Until this block it could only be read as a table in the docs.
 *
 * So: one cell per category, grouped by where the fix lives, area equal
 * per category so the picture is the share of the vocabulary each class
 * takes. Colour is the verdict `deepcompare.scaffold.reach()` gave it —
 * expressible in a knob, prompt-shaped, an investigation, or the scaffold
 * with nothing here that reaches it. The last of those is the finding, and
 * it is drawn as the only outlined kind so the eye lands on it.
 *
 * A category this loop actually met carries a dot; the counts are the
 * engine's own tally (`scaffold.seen_in`), because this page does not do
 * arithmetic. Every sentence is `reach().reading`.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var L = AgentDiff.lib;
  var d3 = global.d3;
  var plural = L.fmt.plural;

  //: the four verdicts, in the order they degrade for a reader who wants
  //: to know what can be tried
  var VERDICT = {
    "knob": { label: "a knob reaches it", fill: "var(--good)", ink: "#fff" },
    "prompt": { label: "prompt-shaped — the prompt loop tests these", fill: "var(--a)", ink: "#fff" },
    "investigation": { label: "an investigation, not a change", fill: "var(--surface-2)", ink: "var(--ink-2)" },
    "no knob": { label: "the scaffold, and nothing here reaches it", fill: "none", ink: "var(--ink)" },
  };
  var ORDER = ["knob", "prompt", "investigation", "no knob"];

  function ensureStyle() {
    L.style.once("reach", [
      ".rch{position:relative}",
      ".rch-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:95ch}",
      ".rch-chart{width:100%}.rch-chart svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rch-cell rect{stroke:var(--rule-2);stroke-width:1}",
      ".rch-cell.out rect{stroke:var(--warn);stroke-width:1.5;stroke-dasharray:3 2}",
      ".rch-cell text{font-size:10px;pointer-events:none}",
      ".rch-cell .cat{font-weight:600}",
      ".rch-grp{font:600 10.5px var(--mono);fill:var(--ink-3)}",
      ".rch-legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:var(--fs-xs);color:var(--ink-2);margin:8px 0 0}",
      ".rch-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px;border:1px solid var(--rule-2)}",
      ".rch-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:95ch}",
      ".rch-tbl{margin-top:10px;font-size:var(--fs-xs)}.rch-tbl summary{cursor:pointer;color:var(--ink-3)}",
      ".rch-tbl table{border-collapse:collapse;margin-top:6px;font-variant-numeric:tabular-nums}",
      ".rch-tbl th,.rch-tbl td{text-align:left;padding:3px 12px 3px 0;border-top:1px solid var(--rule);color:var(--ink-2);vertical-align:top}",
      ".rch-tbl th{font-weight:500;color:var(--ink-3);border-top:0}",
      ".rch-tbl td.k{font-family:var(--mono);color:var(--ink)}",
    ].join(""));
  }

  function model(ctx) {
    var reach = ((ctx.aggregate || {}).loop || {}).reach;
    if (!reach || !(reach.rows || []).length) return null;
    var seen = reach.seen || {};
    var groups = [];
    (reach.rows || []).forEach(function (row) {
      var g = groups.filter(function (x) { return x.effort === row.effort; })[0];
      if (!g) { g = { effort: row.effort, rows: [] }; groups.push(g); }
      g.rows.push({
        category: row.category, effort: row.effort, verdict: row.verdict,
        knob: row.knob || null, detail: row.detail || "",
        met: seen[row.category] || null,
      });
    });
    return { groups: groups, rows: reach.rows, counts: reach.counts || {},
             total: reach.total || (reach.rows || []).length,
             reading: reach.reading || "", seen: seen };
  }

  function draw(host, m) {
    host.innerHTML = "";
    if (!d3) return;
    // `layout.responsive` calls back with no arguments; the host measures
    // itself, and `measure` clamps a detached host to 320 rather than
    // handing back a zero that becomes NaN in the viewBox
    var W = L.layout.measure(host, 320, 1400), pad = 2, headH = 14;
    // area per category is equal, so the picture is each class's share of
    // the vocabulary — the quantity the claim is about
    var data = { children: m.groups.map(function (g) {
      return { effort: g.effort, children: g.rows.map(function (r) { return { row: r, size: 1 }; }) };
    }) };
    var H = Math.max(190, Math.min(360, Math.round(W * 0.33)));
    var root = d3.hierarchy(data, function (d) { return d.children; })
      .sum(function (d) { return d.size || 0; })
      .sort(function (a, b) { return b.value - a.value; });
    d3.treemap().size([W, H]).paddingOuter(pad).paddingInner(pad).paddingTop(headH)(root);
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H)
      .attr("role", "img")
      .attr("aria-label", "every recommendation category the engine can produce, grouped by where the fix "
        + "lives and marked by whether this harness can express it: " + m.reading);
    root.descendants().forEach(function (d) {
      if (d.depth === 0) return;
      var w = d.x1 - d.x0, h = d.y1 - d.y0;
      if (w <= 0 || h <= 0) return;
      if (d.depth === 1) {
        // truncated to its own cell: two narrow groups side by side drew
        // their headers straight over each other
        var full = d.data.effort + " · " + plural(d.children ? d.children.length : 0, "category", "categories");
        var fits = Math.max(3, Math.floor((w - 4) / 5.6));
        svg.append("text").attr("class", "rch-grp").attr("x", d.x0 + 2).attr("y", d.y0 + 10)
          .text(L.fmt.trunc(full, fits))
          .append("title").text(full);
        return;
      }
      var r = d.data.row, v = VERDICT[r.verdict] || VERDICT["no knob"];
      var g = svg.append("g").attr("class", "rch-cell" + (r.verdict === "no knob" ? " out" : ""))
        .attr("data-category", r.category).attr("data-verdict", r.verdict)
        .attr("transform", "translate(" + d.x0 + "," + d.y0 + ")");
      g.append("rect").attr("width", w).attr("height", h).attr("rx", 3)
        .attr("fill", v.fill === "none" ? "var(--surface)" : v.fill)
        .attr("fill-opacity", v.fill === "none" ? 1 : 0.85);
      g.append("title").text(r.category + " — " + r.effort + ": " + v.label
        + (r.knob ? " (" + r.knob + ")" : "")
        + (r.met ? " · this loop met it " + plural((r.met.proposed || 0) + (r.met.unactionable || 0), "time") : ""));
      if (h >= 22 && w >= 52) {
        g.append("text").attr("class", "cat").attr("x", 5).attr("y", 14)
          .attr("fill", v.ink).text(L.fmt.trunc(r.category, Math.max(4, Math.floor((w - 12) / 5.6))));
      }
      // a category this loop actually met
      if (r.met && w >= 16 && h >= 16) {
        g.append("circle").attr("cx", w - 7).attr("cy", 7).attr("r", 3)
          .attr("fill", v.fill === "none" ? "var(--ink)" : "#fff").attr("fill-opacity", 0.9);
      }
    });
  }

  function table(H, m) {
    var fold = H("details", { class: "rch-tbl" });
    fold.appendChild(H("summary", { text: "table view: every category, its class, and what reaches it" }));
    var t = H("table");
    t.appendChild(H("tr", {}, ["where the fix lives", "category", "verdict", "knob", "this loop met it"]
      .map(function (c) { return H("th", { text: c }); })));
    m.rows.forEach(function (r) {
      var met = m.seen[r.category];
      t.appendChild(H("tr", { "data-category": r.category }, [
        H("td", { text: r.effort }), H("td", { class: "k", text: r.category }),
        H("td", { text: (VERDICT[r.verdict] || {}).label || r.verdict }),
        H("td", { class: "k", text: r.knob || "—" }),
        H("td", { text: met ? (met.proposed || 0) + " proposed · " + (met.unactionable || 0) + " refused" : "—" }),
      ]));
    });
    fold.appendChild(t);
    return fold;
  }

  AgentDiff.block({
    id: "hn-reach",
    title: "What this harness can act on",
    question: "Of everything the engine can recommend, how much can this harness actually try?",
    group: "other",
    size: "wide",

    relevance: function (ctx) {
      var reach = ((ctx.aggregate || {}).loop || {}).reach;
      return reach && (reach.rows || []).length ? 0.84 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      if (!m) return ctx.empty(el, "No loop ran here, so there is no harness to describe.");
      var root = H("div", { class: "rch" });
      el.appendChild(root);
      root.appendChild(H("p", { class: "rch-lede", text: m.reading }));
      var chart = H("div", { class: "rch-chart" });
      root.appendChild(chart);
      L.layout.responsive(chart, function () { draw(chart, m); }, "harness-reach");

      var leg = H("div", { class: "rch-legend" });
      ORDER.forEach(function (k) {
        if (!m.counts[k]) return;
        var v = VERDICT[k];
        leg.appendChild(H("span", {}, [
          H("i", { style: v.fill === "none"
            ? "background:var(--surface);border:1.5px dashed var(--warn)"
            : "background:" + v.fill }),
          H("span", { text: v.label + " · " + m.counts[k] }),
        ]));
      });
      if (Object.keys(m.seen).length) {
        leg.appendChild(H("span", { text: "• = a category this loop actually met" }));
      }
      root.appendChild(leg);

      root.appendChild(H("p", { class: "rch-note", text:
        "Every category is one cell, so a class's area is its share of the vocabulary rather than its "
        + "importance. The dashed cells are the finding: the engine can recommend them and this harness has "
        + "nothing to try. Which knob reaches which category is derived from the actuator's own rules, so "
        + "this picture cannot drift from what the loop will really propose." }));
      root.appendChild(table(H, m));
    },
  });
})(typeof window !== "undefined" ? window : this);
