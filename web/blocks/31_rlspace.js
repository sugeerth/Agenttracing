/* AgentDiff blocks — the behaviour space: what a policy does, and where two part.
 *
 * The Training view's other blocks read the reward. These read the
 * *behaviour*: every step reduced to one token (the tool's name, or the
 * step's family), so an episode is a stream of tokens and a policy is a
 * set of streams. Two blocks over `aggregate.rl.space`:
 *
 *   rl-atlas       the episodes laid out by how differently they behaved —
 *                  classical MDS of the pairwise normalised edit distance,
 *                  a mark per episode, shape by outcome, colour by policy,
 *                  size by |return| — with the n-gram habits underneath: a
 *                  ranked row highlights, in the atlas above, the episodes
 *                  that actually contain it. The axes are meaningless and
 *                  are therefore not drawn; a scale bar carries the one
 *                  length that means something.
 *   rl-divergence  the policy trie as a horizontal thread with branches,
 *                  in the vocabulary of "Where it mattered": thickness is
 *                  the episodes through a branch, colour the mix of the
 *                  two policies, quiet runs of single-child nodes folded
 *                  into ×N segments that dilate on click, the ranked
 *                  branch points ringed and labelled with the return on
 *                  each side.
 *
 * Every number is read from the JSON, never recomputed here.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var d3 = global.d3;

  //: rows the divergence tree draws before it folds the rest away
  var MAX_ROWS = 12;
  var ROW = 17;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".rsp{position:relative}",
      ".rsp svg{display:block;width:100%;height:auto;font-family:var(--sans)}",
      ".rsp text{font-size:var(--fs-xs)}",
      ".rsp .lab{fill:var(--ink-2)}.rsp .lab.dim{fill:var(--ink-3)}.rsp .lab.mono{font-family:var(--mono)}",
      ".rsp-narr{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:90ch}",
      ".rsp-bar{display:flex;gap:8px 14px;flex-wrap:wrap;align-items:center;font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 8px}",
      ".rsp-bar i{display:inline-block;width:10px;height:10px;border-radius:50%;vertical-align:-1px;margin-right:5px}",
      ".rsp-chip{font-family:var(--mono);color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}",
      ".rsp-chip b{color:var(--ink);font-weight:600}",
      ".rsp-bar button{font:inherit;font-size:var(--fs-xs);border:0;background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:1px 9px;cursor:pointer}",
      ".rsp-bar button[aria-pressed=true]{background:var(--ink);color:var(--bg)}",
      ".rsp-note{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0;max-width:90ch;line-height:1.45}",
      ".rsp-mark{cursor:pointer}.rsp-mark.mute{opacity:.18}",
      ".rsp-mark:hover .hit,.rsp-mark[aria-current=true] .hit{stroke:var(--ink);stroke-width:1.4;stroke-opacity:.9}",
      ".rsp-scale line{stroke:var(--ink-3)}.rsp-scale text{fill:var(--ink-3);font-family:var(--mono)}",
      ".rsp-habits{margin-top:10px}",
      ".rsp-row{display:grid;grid-template-columns:minmax(90px,1fr) 66px 74px;gap:8px;align-items:center;padding:3px 4px;border-radius:5px;cursor:pointer;font-size:var(--fs-xs);color:var(--ink-2)}",
      ".rsp-row:hover,.rsp-row:focus-visible{background:var(--surface-2);outline:none}",
      ".rsp-row[aria-current=true]{background:var(--surface-2)}.rsp-row[aria-current=true] .rsp-g{color:var(--ink);font-weight:600}",
      ".rsp-g{font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
      ".rsp-n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink-3)}",
      ".rsp-n.k{color:var(--ink)}",
      ".rsp-head{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 2px;font-family:var(--mono)}",
      ".rsp-details{margin-top:8px;font-size:var(--fs-xs)}.rsp-details summary{cursor:pointer;color:var(--ink-2)}",
      ".rsp-table{border-collapse:collapse;width:100%;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:4px}",
      ".rsp-table th{font-family:var(--mono);font-weight:500;color:var(--ink-3);text-align:left;padding:2px 10px 5px 0;border-bottom:1px solid var(--rule);white-space:nowrap}",
      ".rsp-table td{padding:3px 10px 3px 0;color:var(--ink-2);vertical-align:top}",
      ".rsp-table td.n{text-align:right;font-family:var(--mono);white-space:nowrap}",
      ".rsp-table tr[data-node]{cursor:pointer}.rsp-table tr[data-node]:hover td{color:var(--ink)}",
      ".rsp-table tr[aria-current=true] td{color:var(--ink)}",
      ".rsp-unit{cursor:pointer}.rsp-unit:hover .seg{stroke-opacity:1}",
      ".rsp-fold text{fill:var(--ink-3);font-family:var(--mono);pointer-events:none}",
      ".rsp-fold:hover line{stroke:var(--ink)}",
      ".rsp-bp{cursor:pointer}.rsp-bp text{font-family:var(--mono);fill:var(--ink);paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}",
      ".rsp-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:330px}",
      ".rsp-tip b{color:var(--ink)}",
      "@media (max-width:640px){.rsp-row{grid-template-columns:minmax(70px,1fr) 58px 62px}}",
    ].join("\n");
    document.head.appendChild(node);
  }

  // ------------------------------------------------------------ helpers

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function signed(v, p) {
    if (!isNum(v)) return "—";
    var s = Math.abs(v).toFixed(p === undefined ? 1 : p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  }
  function plain(v, p) { return isNum(v) ? v.toFixed(p === undefined ? 2 : p) : "—"; }
  function short(id) { return String(id || "").replace(/^(rl|t)\d+_/, "").replace(/_/g, " "); }
  function trunc(s, n) { s = String(s === null || s === undefined ? "" : s); return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s; }
  var CH = 6.2;
  function width(host) { var w = host.clientWidth || (host.parentNode && host.parentNode.clientWidth) || 0; return Math.max(300, Math.min(1400, w || 320)); }
  function responsive(host, draw, k) {
    if (AgentDiff.charts && AgentDiff.charts.responsive) return AgentDiff.charts.responsive(host, draw, k);
    draw(); return host;
  }
  function selectTask(ctx, id) {
    var fn = ctx && typeof ctx.selectTask === "function" ? ctx.selectTask
      : AgentDiff._internals && typeof AgentDiff._internals.selectTask === "function" ? AgentDiff._internals.selectTask : null;
    if (fn && id) fn(id);
  }
  //: a fold's length grows with what it holds — the rule "Where it mattered" uses
  function foldW(n) { return 6 + 6 * Math.log(1 + Math.max(0, n)) / Math.LN2; }

  function tooltip(root) {
    var tip = document.createElement("div"); tip.className = "rsp-tip"; tip.hidden = true; root.appendChild(tip);
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
        if (x + 330 > r.width) x = Math.max(0, evt.clientX - r.left - 340);
        tip.style.left = x + "px"; tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
    };
  }

  // -------------------------------------------------------------- model

  /* The space as the page needs it: the policies with a side (so the page's
   * A/B colours carry over), the layout's marks, the trie, the branch points
   * and the habits — all straight off `aggregate.rl.space`. */
  var cache = null;
  function model(ctx) {
    var agg = ctx.aggregate || {}, report = ctx.report || null;
    if (cache && cache.agg === agg && cache.report === report) return cache.m;
    var m;
    try { m = build(agg, report); } catch (err) { console.warn("AgentDiff behaviour space: model failed", err); m = { ok: false }; }
    cache = { agg: agg, report: report, m: m };
    return m;
  }

  function build(agg, report) {
    var rl = agg && agg.rl && typeof agg.rl === "object" ? agg.rl : null;
    var space = rl && rl.space && typeof rl.space === "object" ? rl.space : null;
    if (!space || !space.measurable) return { ok: false, reason: space ? space.reason : null };
    var names = Array.isArray(space.policies) ? space.policies.slice() : [];
    var an = report && report.a && report.a.agent && report.a.agent.name;
    var bn = report && report.b && report.b.agent && report.b.agent.name;
    var sideOf = {};
    names.forEach(function (n) { sideOf[n] = n === an ? "a" : n === bn ? "b" : null; });
    var free = ["a", "b"].filter(function (s) { return names.every(function (n) { return sideOf[n] !== s; }); });
    names.forEach(function (n) { if (!sideOf[n] && free.length) sideOf[n] = free.shift(); });
    var policies = names.map(function (n) {
      return { name: n, side: sideOf[n], color: sideOf[n] === "a" ? "var(--a)" : sideOf[n] === "b" ? "var(--b)" : "var(--ink-3)" };
    });
    var points = (space.layout && Array.isArray(space.layout.points) ? space.layout.points : [])
      .filter(function (p) { return p && isNum(p.x) && isNum(p.y); });
    return {
      ok: points.length > 0, space: space, policies: policies,
      colorOf: function (name) { for (var i = 0; i < policies.length; i++) if (policies[i].name === name) return policies[i].color; return "var(--ink-3)"; },
      points: points, trie: space.trie || null, branches: space.branches || null,
      ngrams: space.ngrams || null, distance: space.distance || null,
      vocabulary: space.vocabulary || null, narrative: space.narrative || "",
    };
  }

  //: the concrete colours behind --a/--b, so a branch can be drawn as the
  //: *mix* of the two policies rather than as one of them
  function resolve(el, name, fallback) {
    try {
      var v = getComputedStyle(el).getPropertyValue(name);
      return v && v.trim() ? v.trim() : fallback;
    } catch (err) { return fallback; }
  }
  function mixer(el, m) {
    var ca = resolve(el, "--a", "#2f6f9f"), cb = resolve(el, "--b", "#b5651d");
    var pa = m.policies[0] ? m.policies[0].name : null, pb = m.policies[1] ? m.policies[1].name : null;
    if (m.policies[0] && m.policies[0].side === "b") { var t = ca; ca = cb; cb = t; }
    var interp = null;
    try { interp = d3 && d3.interpolateLab ? d3.interpolateLab(ca, cb) : null; } catch (err) { interp = null; }
    return function (by) {
      var na = (by && by[pa]) || 0, nb = (by && by[pb]) || 0;
      if (!na && !nb) return "var(--ink-3)";
      if (!interp) return na >= nb ? ca : cb;
      return interp(nb / (na + nb));
    };
  }

  function policyChips(H, m) {
    var bar = H("div", { class: "rsp-bar" });
    m.policies.forEach(function (p) {
      var within = m.distance && m.distance.within && m.distance.within[p.name];
      var spread = within && isNum(within.spread) ? "spread " + plain(within.spread) : (within && within.reason) || "";
      bar.appendChild(H("span", { class: "rsp-chip" }, [
        H("i", { style: { background: p.color } }),
        H("b", { text: p.name }), document.createTextNode(spread ? " · " + spread : ""),
      ]));
    });
    if (m.distance && isNum(m.distance.between)) {
      bar.appendChild(H("span", { class: "rsp-chip", text: "between " + plain(m.distance.between) }));
    }
    return bar;
  }

  // ============================================================ the atlas

  function drawAtlas(host, ctx, m, tip, state) {
    if (!d3) return;
    var W = width(host), narrow = W < 520;
    var H = Math.max(220, Math.min(narrow ? 300 : 420, Math.round(W * 0.52)));
    var pad = 16, padB = 26;
    var pts = m.points;
    var xs = pts.map(function (p) { return p.x; }), ys = pts.map(function (p) { return p.y; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var spanX = Math.max(x1 - x0, 1e-9), spanY = Math.max(y1 - y0, 1e-9);
    // one scale for both axes: the distance between two marks is the only
    // thing this picture says, so it must not be stretched in one direction
    var k = Math.min((W - 2 * pad) / spanX, (H - pad - padB) / spanY);
    if (!isFinite(k) || k <= 0) k = 1;
    var cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    function px(p) { return W / 2 + (p.x - cx) * k; }
    function py(p) { return (H - padB + pad) / 2 - (p.y - cy) * k; }
    var maxAbs = 0;
    pts.forEach(function (p) { if (isNum(p["return"])) maxAbs = Math.max(maxAbs, Math.abs(p["return"])); });
    function radius(p) {
      var r = isNum(p["return"]) && maxAbs > 0 ? Math.abs(p["return"]) / maxAbs : 0;
      return (narrow ? 2.6 : 3) + (narrow ? 3.4 : 4.4) * Math.sqrt(r);
    }
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + H).attr("role", "img")
      .attr("aria-label", pts.length + " episodes laid out by how differently they behaved: "
        + m.policies.map(function (p) { return p.name; }).join(" and ")
        + ", a filled mark solved, a hollow diamond failed, its size the size of the return; the axes carry no meaning");
    // the scale bar: the one length in the picture that means something
    var want = spanX / 4, step = [0.05, 0.1, 0.2, 0.25, 0.5, 1].filter(function (s) { return s >= want; })[0] || 1;
    var bar = svg.append("g").attr("class", "rsp-scale").attr("transform", "translate(" + pad + "," + (H - 10) + ")");
    bar.append("line").attr("x1", 0).attr("x2", step * k).attr("y1", 0).attr("y2", 0).attr("stroke-width", 1);
    bar.append("line").attr("x1", 0).attr("x2", 0).attr("y1", -3).attr("y2", 3).attr("stroke-width", 1);
    bar.append("line").attr("x1", step * k).attr("x2", step * k).attr("y1", -3).attr("y2", 3).attr("stroke-width", 1);
    bar.append("text").attr("x", step * k + 6).attr("y", 3.5).text(plain(step) + " distance");
    // the marks
    var sel = state.gram ? state.gram.keys : null;
    pts.forEach(function (p) {
      var muted = sel && !sel[p.key];
      var g = svg.append("g").attr("class", "rsp-mark" + (muted ? " mute" : "")).attr("data-key", p.key)
        .attr("data-policy", p.policy).attr("data-task", p.task_id).attr("data-success", p.success ? "1" : "0")
        .attr("transform", "translate(" + px(p).toFixed(2) + "," + py(p).toFixed(2) + ")");
      var color = m.colorOf(p.policy), r = radius(p);
      if (p.success) {
        g.append("circle").attr("class", "hit").attr("r", r).attr("fill", color).attr("fill-opacity", 0.72).attr("stroke", color).attr("stroke-opacity", 0);
      } else {
        var d = r + 1.4;
        g.append("path").attr("class", "hit").attr("d", "M0," + (-d) + "L" + d + ",0L0," + d + "L" + (-d) + ",0Z")
          .attr("fill", "var(--surface)").attr("fill-opacity", 0.55).attr("stroke", color).attr("stroke-width", 1.4);
      }
      // an episode whose nearest neighbour belongs to the other policy: the
      // interesting case, ringed so it can be found
      if (p.nearest && p.nearest.other_policy) {
        g.append("circle").attr("class", "rsp-cross").attr("r", r + 3.6).attr("fill", "none")
          .attr("stroke", "var(--ink-3)").attr("stroke-width", 1).attr("stroke-dasharray", "1.5 2");
      }
      g.append("circle").attr("r", Math.max(9, r + 4)).attr("fill", "transparent");
      var lines = [
        { b: true, text: p.policy + " · " + short(p.task_id) + (p.run_id ? " · " + p.run_id : "") },
        { text: "return " + signed(p["return"]) + " · " + p.steps + " steps · " + (p.success ? "solved" : "failed") },
        p.nearest ? { text: "nearest: " + p.nearest.key.split("|").slice(0, 1).concat(short(p.nearest.key.split("|")[1] || "")).join(" · ") + " at " + plain(p.nearest.distance) + (p.nearest.other_policy ? " — the other policy" : "") } : null,
        { text: "click to open the task" },
      ];
      g.append("title").text(p.policy + " · " + p.task_id + " · " + (p.run_id || "") + " — return " + signed(p["return"]) + ", " + (p.success ? "solved" : "failed"));
      g.on("pointermove", function (evt) { tip.show(evt, lines); }).on("pointerleave", tip.hide)
        .on("click", function () { tip.hide(); selectTask(ctx, p.task_id); });
    });
  }

  //: the episodes whose token stream contains this gram
  function containing(points, gram) {
    var keys = {}, n = 0;
    points.forEach(function (p) {
      var toks = Array.isArray(p.tokens) ? p.tokens : [];
      for (var i = 0; i + gram.length <= toks.length; i++) {
        var hit = true;
        for (var j = 0; j < gram.length; j++) if (toks[i + j] !== gram[j]) { hit = false; break; }
        if (hit) { keys[p.key] = true; n++; return; }
      }
    });
    return { keys: keys, episodes: Object.keys(keys).length, hits: n };
  }

  function habits(H, m, state, repaint) {
    var win = m.ngrams && m.ngrams.winning ? m.ngrams.winning : null;
    var wrap = H("div", { class: "rsp-habits" });
    if (!win || !win.measurable) {
      wrap.appendChild(H("p", { class: "rsp-note", text: "Habits: " + ((win && win.reason) || "no n-gram ratio to show.") }));
      return wrap;
    }
    var side = state.side || "top";
    var bar = H("div", { class: "rsp-bar" });
    [["top", "over-represented in the " + win.winners + " solved"], ["bottom", "in the " + win.losers + " failed"]].forEach(function (opt) {
      bar.appendChild(H("button", {
        type: "button", "aria-pressed": side === opt[0] ? "true" : "false", text: opt[1],
        onclick: function () { state.side = opt[0]; state.gram = null; repaint(); },
      }));
    });
    wrap.appendChild(bar);
    var rows = (side === "top" ? win.top : win.bottom) || [];
    rows.forEach(function (r) {
      var on = state.gram && state.gram.text === r.text;
      var row = H("div", {
        class: "rsp-row", role: "button", tabindex: "0", "data-gram": r.text, "data-n": r.n,
        "aria-current": on ? "true" : "false",
        "data-ratio": r.ratio === null || r.ratio === undefined ? "" : r.ratio,
        onclick: function () {
          if (on) { state.gram = null; } else {
            var hit = containing(m.points, r.gram);
            state.gram = { text: r.text, gram: r.gram, keys: hit.keys, episodes: hit.episodes };
          }
          repaint();
        },
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.click(); } },
      }, [
        H("span", { class: "rsp-g", title: r.text, text: r.text }),
        H("span", { class: "rsp-n k", text: r.ratio === null || r.ratio === undefined ? "only here" : plain(r.ratio) + "×" }),
        H("span", { class: "rsp-n", text: r.win_count + " / " + r.lose_count }),
      ]);
      wrap.appendChild(row);
    });
    wrap.appendChild(H("p", {
      class: "rsp-note",
      text: "ratio = the gram's share of every " + (win.top[0] ? "" : "") + "gram of its length among the solved episodes over its share among the failed; "
        + "the pair after it is those two counts (solved / failed), so a big ratio on small counts stays visible. "
        + (isNum(win.win_steps_mean) && isNum(win.lose_steps_mean)
          ? "A solved episode runs " + plain(win.win_steps_mean, 1) + " steps against " + plain(win.lose_steps_mean, 1) + ", which lifts every rate a short episode has. " : "")
        + "Click a row to light up the episodes that contain it.",
    }));
    return wrap;
  }

  AgentDiff.block({
    id: "rl-atlas",
    title: "Behaviour atlas",
    question: "Laid out by how differently they behaved: which episodes did the same thing, which policy sits where — and which habits go with winning.",
    group: "training",
    size: "wide",
    relevance: function (ctx) { var m = model(ctx); return m.ok && m.points.length > 1 ? 0.745 : 0; },
    render: function (el, ctx) {
      ensureStyle();
      var Hh = ctx.h, m = model(ctx);
      var root = Hh("div", { class: "rsp rsp-atlas" });
      el.appendChild(root);
      var tip = tooltip(root);
      var state = { gram: null, side: "top" };
      var narr = Hh("p", { class: "rsp-narr" });
      root.appendChild(narr);
      root.appendChild(policyChips(Hh, m));
      var host = Hh("div", { class: "rsp-chart" });
      root.appendChild(host);
      var habitHost = Hh("div");
      root.appendChild(habitHost);
      var note = Hh("p", { class: "rsp-note" });
      root.appendChild(note);
      function paint() {
        host.innerHTML = "";
        habitHost.innerHTML = "";
        var dist = m.distance || {};
        narr.textContent = "Distance is the normalised edit distance between two episodes' step tokens — Levenshtein over the longer stream, 0 identical, 1 nothing in common."
          + (state.gram ? " Lit: the " + state.gram.episodes + " episodes that play " + state.gram.text + "." : "");
        responsive(host, function () { drawAtlas(host, ctx, m, tip, state); }, "rl-atlas");
        habitHost.appendChild(habits(Hh, m, state, paint));
        var layout = (m.space && m.space.layout) || {};
        note.textContent = "Classical MDS of that distance matrix: the axes have no units and no direction, only how far apart two marks sit"
          + (isNum(layout.stress) ? " (stress " + plain(layout.stress) + ": how much the plane had to bend to hold the distances)" : "") + ". "
          + "Filled = solved, hollow diamond = failed, size = |return|, a dashed ring = its nearest neighbour belongs to the other policy. "
          + (dist.note ? dist.note.charAt(0).toUpperCase() + dist.note.slice(1) + "; at most " + dist.episode_cap + " episodes enter the matrix. " : "")
          + "Click a mark to open its task.";
      }
      paint();
    },
  });

  // ======================================================= the divergence tree

  /* The trie as a thread with branches. The thread is the heaviest path
   * from the root — the behaviour most episodes share; every other child of
   * a node on it hangs below as a branch, itself drawn as its own heaviest
   * path. Quiet runs (single-child nodes that are neither a branch point nor
   * a leaf) fold into one ×N segment that dilates on click. */
  function chainOf(node) {
    var nodes = [node], branches = [], cur = node, guard = 0;
    while (cur && Array.isArray(cur.children) && cur.children.length && guard++ < 400) {
      var kids = cur.children.slice().sort(function (p, q) {
        return (q.episodes - p.episodes) || String(p.token).localeCompare(String(q.token));
      });
      for (var i = 1; i < kids.length; i++) branches.push({ at: nodes.length - 1, node: kids[i] });
      nodes.push(kids[0]);
      cur = kids[0];
    }
    return { nodes: nodes, branches: branches };
  }
  function isJunction(n) { return Array.isArray(n.children) && n.children.length > 1; }
  //: a chain as drawable units: single nodes where something happens, folds elsewhere
  function unitsOf(chain, state, marked) {
    var out = [], nodes = chain.nodes, branchAt = {};
    chain.branches.forEach(function (b) { branchAt[b.at] = true; });
    var i = 0;
    while (i < nodes.length) {
      var n = nodes[i];
      var quiet = i > 0 && i < nodes.length - 1 && !isJunction(n) && !branchAt[i] && !marked[n.id]
        && !isNum(n.tail) && !isNum(n.truncated);
      if (quiet) {
        var j = i;
        while (j < nodes.length) {
          var q = nodes[j];
          if (j >= nodes.length - 1 || isJunction(q) || branchAt[j] || marked[q.id] || isNum(q.tail) || isNum(q.truncated)) break;
          j++;
        }
        var group = nodes.slice(i, j);
        if (group.length >= 2 && !state.open[group[0].id]) {
          out.push({ type: "fold", id: group[0].id, nodes: group, index: i, last: j - 1 });
          i = j;
          continue;
        }
      }
      out.push({ type: "node", id: n.id, node: n, index: i });
      i++;
    }
    return out;
  }

  function drawTree(host, ctx, m, tip, state, repaint) {
    if (!d3 || !m.trie || !m.trie.root) return;
    var W = width(host), narrow = W < 560;
    var root = m.trie.root, total = Math.max(1, root.episodes);
    var ranked = (m.branches && Array.isArray(m.branches.points) ? m.branches.points : []);
    var marked = {};
    ranked.forEach(function (p, i) { marked[p.id] = i + 1; });
    var mix = mixer(host, m);
    var SEG = narrow ? 15 : 21, padL = 6, padR = narrow ? 46 : 96;
    var avail = Math.max(80, W - padL - padR);
    var rows = [], laid = [], hidden = 0, hiddenEps = 0;
    var cap = state.rows || MAX_ROWS;

    function widthOf(u) { return u.type === "fold" ? foldW(u.nodes.length) : SEG; }
    // lay a chain out at a row, then its branches on the rows below
    function lay(node, x0, row, depth) {
      var chain = chainOf(node), units = unitsOf(chain, state, marked);
      var xs = [], x = x0;
      units.forEach(function (u) { xs.push(x); x += widthOf(u); });
      var item = { units: units, xs: xs, row: row, x0: x0, x1: x, chain: chain, depth: depth };
      laid.push(item);
      rows[row] = item;
      if (depth > 3) return item;
      var pending = chain.branches.slice().sort(function (p, q) { return (q.node.episodes - p.node.episodes) || (p.at - q.at); });
      pending.forEach(function (b) {
        // where the branch leaves the thread: the right edge of the unit holding its parent
        var ui = 0;
        for (var i = 0; i < units.length; i++) {
          var u = units[i];
          if ((u.type === "node" && u.index === b.at) || (u.type === "fold" && b.at >= u.index && b.at <= u.last)) { ui = i; break; }
        }
        var bx = xs[ui] + widthOf(units[ui]);
        if (rows.length >= cap) { hidden++; hiddenEps += b.node.episodes; return; }
        var r = rows.length;
        rows.push(null);
        var kid = lay(b.node, bx, r, depth + 1);
        kid.from = { row: row, x: bx };
      });
      return item;
    }
    rows.push(null);
    lay(root, padL, 0, 0);
    // scale so the longest row fits
    var maxX = 0;
    laid.forEach(function (it) { maxX = Math.max(maxX, it.x1); });
    var kx = maxX > padL + avail ? avail / Math.max(1, maxX - padL) : 1;
    function X(v) { return padL + (v - padL) * kx; }

    var top = 16, Hh = top + rows.length * ROW + (hidden ? ROW : 0) + 14;
    var svg = d3.select(host).append("svg").attr("viewBox", "0 0 " + W + " " + Hh).attr("role", "img")
      .attr("aria-label", "the policy trie as a thread: " + total + " episodes from one root, a branch's thickness the episodes through it, its colour the mix of the two policies, "
        + ranked.length + " ranked branch points ringed");
    function rowY(r) { return top + r * ROW; }
    function strokeW(n) { return 1 + 4.5 * Math.sqrt(Math.max(0, n) / total); }

    function nodeLines(n) {
      var by = m.policies.map(function (p) { return p.name + " " + ((n.by_policy && n.by_policy[p.name]) || 0); }).join(" · ");
      return [
        { b: true, text: "step " + n.depth + (n.token ? " · " + n.token : " · the root") },
        { text: n.episodes + " episodes (" + by + ") · return " + signed(n.mean_return) + " · " + (isNum(n.success_rate) ? Math.round(n.success_rate * 100) + "% solved" : "—") },
        n.prefix && n.prefix.length ? { text: "…" + n.prefix.slice(-4).join(" → ") } : null,
        isNum(n.tail) ? { text: n.tail + " more steps folded: one episode goes on alone" } : null,
        isNum(n.truncated) ? { text: n.truncated + " more steps beyond the depth cap" } : null,
        marked[n.id] ? { text: "branch point #" + marked[n.id] } : null,
      ];
    }

    laid.forEach(function (it) {
      // the elbow from the parent thread down to this row
      if (it.from) {
        svg.append("path").attr("class", "rsp-elbow")
          .attr("d", "M" + X(it.from.x).toFixed(2) + "," + rowY(it.from.row) + "V" + rowY(it.row))
          .attr("fill", "none").attr("stroke", mix(it.chain.nodes[0].by_policy)).attr("stroke-width", Math.max(1, strokeW(it.chain.nodes[0].episodes) * 0.8)).attr("stroke-opacity", 0.5);
      }
      it.units.forEach(function (u, ui) {
        var x = X(it.xs[ui]), w = Math.max(2, widthOf(u) * kx), y = rowY(it.row);
        if (u.type === "fold") {
          var eps = u.nodes[0].episodes;
          var fg = svg.append("g").attr("class", "rsp-unit rsp-fold").attr("data-fold", u.id).attr("data-steps", u.nodes.length)
            .attr("transform", "translate(" + x + "," + y + ")");
          fg.append("line").attr("x1", 0).attr("x2", w).attr("y1", 0).attr("y2", 0)
            .attr("stroke", mix(u.nodes[0].by_policy)).attr("stroke-width", Math.max(1.2, strokeW(eps) * 0.7))
            .attr("stroke-dasharray", "1.5 2.5").attr("stroke-linecap", "round").attr("stroke-opacity", 0.75);
          if (w >= 18) fg.append("text").attr("x", w / 2).attr("y", -5).attr("text-anchor", "middle").text("×" + u.nodes.length);
          fg.append("rect").attr("x", 0).attr("y", -8).attr("width", w).attr("height", 16).attr("fill", "transparent");
          fg.append("title").text(u.nodes.length + " steps every one of these " + eps + " episodes takes the same way; click to open");
          fg.on("pointermove", function (evt) {
            tip.show(evt, [{ b: true, text: "×" + u.nodes.length + " steps folded" },
              { text: u.nodes.map(function (n) { return n.token; }).join(" → ") },
              { text: eps + " episodes, all the same way · click to open" }]);
          }).on("pointerleave", tip.hide).on("click", function () { tip.hide(); state.open[u.id] = true; repaint(); });
          return;
        }
        var n = u.node;
        var g = svg.append("g").attr("class", "rsp-unit").attr("data-node", n.id).attr("data-token", n.token || "root")
          .attr("data-episodes", n.episodes).attr("transform", "translate(" + x + "," + y + ")");
        g.append("line").attr("class", "seg").attr("x1", 0).attr("x2", w).attr("y1", 0).attr("y2", 0)
          .attr("stroke", mix(n.by_policy)).attr("stroke-width", strokeW(n.episodes)).attr("stroke-opacity", 0.85).attr("stroke-linecap", "round");
        g.append("rect").attr("x", 0).attr("y", -8).attr("width", Math.max(w, 6)).attr("height", 16).attr("fill", "transparent");
        g.append("title").text((n.token || "root") + " · " + n.episodes + " episodes · return " + signed(n.mean_return));
        var lines = nodeLines(n);
        g.on("pointermove", function (evt) { tip.show(evt, lines); }).on("pointerleave", tip.hide)
          .on("click", function () { tip.hide(); if (state.open[n.id]) delete state.open[n.id]; repaint(); });
        if (marked[n.id]) {
          var bg = svg.append("g").attr("class", "rsp-bp").attr("data-node", n.id).attr("data-rank", marked[n.id])
            .attr("transform", "translate(" + x + "," + y + ")");
          bg.append("circle").attr("r", 5).attr("fill", "var(--bg)").attr("stroke", "var(--ink)").attr("stroke-width", 1.4)
            .attr("stroke-opacity", state.point === n.id ? 1 : 0.75);
          bg.append("text").attr("x", 0).attr("y", 3).attr("text-anchor", "middle").text(marked[n.id]);
          bg.append("title").text("branch point #" + marked[n.id] + " — " + (ranked[marked[n.id] - 1] || {}).label);
          bg.on("pointermove", function (evt) { tip.show(evt, [{ b: true, text: "branch point #" + marked[n.id] }, { text: (ranked[marked[n.id] - 1] || {}).label }].concat(nodeLines(n).slice(1))); })
            .on("pointerleave", tip.hide).on("click", function () { tip.hide(); state.point = state.point === n.id ? null : n.id; repaint(); });
        }
      });
      // the end of a thread: what it holds
      var last = it.chain.nodes[it.chain.nodes.length - 1];
      var endX = X(it.x1) + 5, room = W - endX - 2;
      var label = last.episodes + (last.episodes === 1 ? " ep" : " eps") + " · " + signed(last.mean_return);
      if (room > 6) {
        svg.append("text").attr("class", "lab mono dim").attr("x", endX).attr("y", rowY(it.row) + 3.5)
          .attr("fill", it.row === 0 ? "var(--ink-2)" : "var(--ink-3)").text(trunc(label, Math.floor(room / CH)))
          .append("title").text(label + (isNum(last.tail) ? " · " + last.tail + " more steps folded" : ""));
      }
    });
    if (hidden) {
      var y = rowY(rows.length);
      var more = svg.append("g").attr("class", "rsp-unit rsp-more").attr("data-hidden", hidden)
        .on("click", function () { state.rows = cap + MAX_ROWS; repaint(); }).style("cursor", "pointer");
      more.append("text").attr("class", "lab dim").attr("x", padL + 10).attr("y", y + 4)
        .text("+" + hidden + " more branch" + (hidden === 1 ? "" : "es") + " · " + hiddenEps + " episodes — click to draw them");
    }
  }

  function branchTable(H, m, state, repaint) {
    var br = m.branches;
    if (!br || !br.measurable || !br.points.length) {
      return H("p", { class: "rsp-note", text: "Branch points: " + ((br && br.reason) || "none.") });
    }
    var a = br.policies[0], b = br.policies[1];
    var table = H("table", { class: "rsp-table" });
    table.appendChild(H("thead", {}, [H("tr", {}, [
      H("th", { text: "#" }), H("th", { text: "step" }),
      H("th", { text: a }), H("th", { text: b }), H("th", { text: "reach" }), H("th", { text: "imbalance" }),
    ])]));
    var body = H("tbody");
    br.points.forEach(function (p, i) {
      var sides = {};
      (p.sides || []).forEach(function (s) { sides[s.policy] = s; });
      function cell(name) {
        var s = sides[name];
        if (!s) return H("td", { text: "—" });
        return H("td", { class: "n" }, [
          H("span", { style: { fontFamily: "var(--mono)", color: "var(--ink)" }, text: s.token }),
          document.createTextNode(" " + signed(s.mean_return) + " (" + s.episodes + ")"),
        ]);
      }
      var row = H("tr", {
        "data-node": p.id, "data-rank": i + 1, "data-score": p.score,
        "aria-current": state.point === p.id ? "true" : "false",
        onclick: function () { state.point = state.point === p.id ? null : p.id; repaint(); },
      }, [
        H("td", { class: "n", text: String(i + 1) }),
        H("td", { class: "n", text: String(p.depth) }),
        cell(a), cell(b),
        H("td", { class: "n", text: p.episodes + " eps" }),
        H("td", { class: "n", text: plain(p.imbalance) }),
      ]);
      body.appendChild(row);
    });
    table.appendChild(body);
    var wrap = H("div", { class: "scroll-x" }, [table]);
    return wrap;
  }

  AgentDiff.block({
    id: "rl-divergence",
    title: "Where the policies part",
    question: "One thread from the first step, branches where the episodes diverge: which step the two policies take differently, and what the return is on each side.",
    group: "training",
    size: "wide",
    relevance: function (ctx) {
      var m = model(ctx);
      return m.ok && m.trie && m.trie.root && m.trie.root.children && m.trie.root.children.length ? 0.74 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, m = model(ctx);
      var root = H("div", { class: "rsp rsp-tree" });
      el.appendChild(root);
      var tip = tooltip(root);
      var state = { open: {}, point: null, rows: MAX_ROWS };
      var head = H("p", { class: "rsp-narr" });
      root.appendChild(head);
      root.appendChild(policyChips(H, m));
      var host = H("div", { class: "rsp-chart" });
      root.appendChild(host);
      var tableHost = H("div");
      root.appendChild(tableHost);
      var note = H("p", { class: "rsp-note" });
      root.appendChild(note);
      function paint() {
        host.innerHTML = ""; tableHost.innerHTML = "";
        var br = m.branches && m.branches.points && m.branches.points.length ? m.branches.points[0] : null;
        head.textContent = br ? br.label.charAt(0).toUpperCase() + br.label.slice(1)
          + " — " + br.episodes + " of " + m.points.length + " episodes reach that step."
          : "Every episode of both policies takes the same path as far as this tree is drawn.";
        responsive(host, function () { drawTree(host, ctx, m, tip, state, paint); }, "rl-divergence");
        tableHost.appendChild(branchTable(H, m, state, paint));
        var pruned = (m.trie && m.trie.pruned) || {};
        note.textContent = "A branch's thickness is the episodes through it and its colour the mix of the two policies; "
          + "a dashed ×N is a run of steps every episode below it takes the same way — click to open it. "
          + "A branch point is scored by how unevenly the two policies split there, weighted by the episodes that reach it. "
          + (pruned.note ? pruned.note.charAt(0).toUpperCase() + pruned.note.slice(1) + ". " : "")
          + "Only the heaviest path is drawn on each row; the rest hang below it.";
      }
      paint();
    },
  });
})(typeof window !== "undefined" ? window : this);
