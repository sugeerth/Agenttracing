/* AgentDiff blocks — the evals the traces wrote.
 *
 * `aggregate.forge` is the engine growing an eval suite from the runs on
 * this page: candidate evals written from where runs went wrong (the
 * page's own trace marks, the recurring issues' signatures, the steps a
 * reader marked, and — with `agentdiff forge --judge` — rules an agent
 * proposed after reading the failing traces), each tried on the learn half
 * of the tasks, and only the ones that pass there tested once on the
 * held-out half, which alone decides adoption.
 *
 * The block's picture is the loop itself: coverage of the wrong runs,
 * round by round, beside the share of right runs the suite wrongly flags
 * — because a suite that "improves" by flagging everything has not
 * improved, and a curve drawn without its false-alarm line would hide
 * exactly that. Then the suite, each eval as the sentence it tests with
 * its counts on both halves; what was not adopted and why; and the wrong
 * runs nothing catches yet, which is where the next round — or a reader —
 * should look.
 *
 * **A reader's marks are part of the loop.** Clicking a mark on the strip
 * chart marks that step as an eval seed; they are kept in this browser
 * only, and "Download marks" writes the JSON `agentdiff batch --seeds` /
 * `agentdiff forge --seeds` reads. Nothing leaves the page on its own.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-forge-css";
  var SEED_KEY = "agentdiff:eval-seeds";
  var SOURCES = { template: "template", signature: "signature", seed: "your mark", judge: "judge", refined: "refined" };

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".fg{display:flex;flex-direction:column;gap:14px}",
      ".fg-lede{font-size:var(--fs-m);line-height:1.6;color:var(--ink);margin:0}",
      ".fg-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);font-weight:700;margin:0 0 6px}",
      ".fg-curve text{font-size:11px;font-weight:500;font-family:inherit;fill:var(--ink-3)}",
      ".fg-curve text.v{font-weight:700;fill:var(--ink)}",
      ".fg-list{list-style:none;margin:0;padding:0}",
      ".fg-row{padding:8px 0;border-bottom:1px solid var(--rule);font-size:var(--fs-s);line-height:1.5}",
      ".fg-row:last-child{border-bottom:0}",
      ".fg-src{display:inline-block;margin-right:7px;padding:0 7px;border-radius:999px;font-size:var(--fs-xs);font-weight:700;",
      "line-height:18px;background:var(--surface-2);color:var(--ink-2)}",
      ".fg-src.judge{color:var(--b)}.fg-src.seed{color:var(--a)}.fg-src.refined{color:var(--good)}",
      ".fg-meta{font-size:var(--fs-xs);color:var(--ink-3);margin:2px 0 0}",
      ".fg-meta code,.fg-code{font-family:ui-monospace,monospace;font-size:var(--fs-xs)}",
      ".fg-tally{display:flex;flex-wrap:wrap;gap:6px}",
      ".fg-chip{font-size:var(--fs-xs);line-height:20px;padding:0 9px;border-radius:999px;background:var(--surface-2);color:var(--ink-2)}",
      ".fg-uncaught{font-size:var(--fs-s);color:var(--ink-2);line-height:1.6;margin:0}",
      ".fg-uncaught code{font-family:ui-monospace,monospace;font-size:var(--fs-xs);color:var(--bad)}",
      ".fg-seeds{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:var(--fs-s);color:var(--ink-2)}",
      ".fg-seeds button{font:inherit;font-size:var(--fs-xs);border:1px solid var(--rule-2);background:var(--surface);",
      "color:var(--ink);border-radius:6px;padding:2px 9px;cursor:pointer}",
      ".fg-seeds button:disabled{opacity:.5;cursor:default}",
      ".fg-note{font-size:var(--fs-xs);color:var(--ink-3);line-height:1.5;margin:0}",
      "details.fg-more>summary{cursor:pointer;font-size:var(--fs-s);color:var(--ink-3)}",
      ".fg.compact{gap:6px}.fg.compact .fg-row{padding:5px 0;font-size:var(--fs-xs);line-height:1.45}",
      ".fg.compact .fg-uncaught,.fg.compact .fg-seeds{font-size:var(--fs-xs)}",
    ].join(""));
  }

  // ------------------------------------------------------------ the marks
  // kept in this browser only; every access guarded, the page works without
  var memory = [];
  function readSeeds() {
    try {
      var got = JSON.parse(global.localStorage.getItem(SEED_KEY) || "[]");
      return Array.isArray(got) ? got : memory;
    } catch (err) { return memory; }
  }
  function writeSeeds(list) {
    memory = list;
    try { global.localStorage.setItem(SEED_KEY, JSON.stringify(list)); } catch (err) { /* memory only */ }
  }
  function seedKey(s) { return s.task + "\u0001" + s.agent + "\u0001" + s.step; }
  var Seeds = {
    list: readSeeds,
    has: function (s) { return readSeeds().some(function (x) { return seedKey(x) === seedKey(s); }); },
    toggle: function (s) {
      var list = readSeeds();
      var k = seedKey(s);
      var at = -1;
      list.forEach(function (x, i) { if (seedKey(x) === k) at = i; });
      if (at >= 0) list.splice(at, 1);
      else list.push({ task: s.task, agent: s.agent, step: s.step, kind: s.kind || null, say: s.say || null });
      writeSeeds(list);
      return at < 0;
    },
    clear: function () { writeSeeds([]); },
    download: function () {
      var blob = new Blob([JSON.stringify({ seeds: readSeeds() }, null, 1)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "eval-seeds.json";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    },
  };
  AgentDiff.seeds = Seeds;

  function forgeOf(ctx) {
    var f = ctx.aggregate && ctx.aggregate.forge;
    return f && (f.measurable || ((f.ledger || {}).rechecked || []).length) ? f : null;
  }

  function curve(H, host, rounds, wrong, right, tall) {
    var d3 = global.d3;
    if (!d3 || !rounds.length) return;
    var draw = function () {
      host.innerHTML = "";
      var width = Math.max(260, host.clientWidth || 480), height = tall || 132;
      var pad = { l: 42, r: 84, t: 12, b: 24 };
      var svg = d3.select(host).append("svg").attr("class", "fg-curve").attr("width", width).attr("height", height)
        .attr("viewBox", "0 0 " + width + " " + height).attr("role", "img")
        .attr("aria-label", "Wrong runs caught and right runs flagged, round by round: " + rounds.map(function (r) {
          return "round " + r.round + " " + r.caught + " of " + wrong + " caught, " + r.false_alarms + " flagged";
        }).join("; "));
      var x = d3.scalePoint().domain(rounds.map(function (r) { return r.round; })).range([pad.l, width - pad.r]).padding(0.2);
      var y = d3.scaleLinear().domain([0, 1]).range([height - pad.b, pad.t]);
      [0, 0.5, 1].forEach(function (t) {
        svg.append("line").attr("x1", pad.l).attr("x2", width - pad.r).attr("y1", y(t)).attr("y2", y(t))
          .attr("stroke", "var(--rule)").attr("stroke-dasharray", t ? "2,4" : null);
        svg.append("text").attr("x", pad.l - 6).attr("y", y(t) + 4).attr("text-anchor", "end").text(Math.round(t * 100) + "%");
      });
      rounds.forEach(function (r) {
        svg.append("text").attr("x", x(r.round)).attr("y", height - 6).attr("text-anchor", "middle").text("round " + r.round);
      });
      [["coverage", "var(--good)", function (r) { return r.caught / Math.max(1, wrong); }, "caught"],
       ["fpr", "var(--bad)", function (r) { return r.false_alarms / Math.max(1, right); }, "flagged"]].forEach(function (s) {
        var line = d3.line().x(function (r) { return x(r.round); }).y(function (r) { return y(s[2](r)); });
        svg.append("path").attr("d", line(rounds)).attr("fill", "none").attr("stroke", s[1]).attr("stroke-width", 2)
          .attr("data-series", s[0]);
        rounds.forEach(function (r) {
          svg.append("circle").attr("cx", x(r.round)).attr("cy", y(s[2](r))).attr("r", 3.5).attr("fill", s[1]);
        });
        var last = rounds[rounds.length - 1];
        // the two end labels share a height when coverage and false alarms
        // end level (both zero, say): coverage goes above, alarms below
        var cy = y(last.caught / Math.max(1, wrong)), fy = y(last.false_alarms / Math.max(1, right));
        var nudge = Math.abs(cy - fy) < 13 ? (s[0] === "coverage" ? -7 : 7) : 0;
        svg.append("text").attr("class", "v").attr("x", x(last.round) + 8).attr("y", y(s[2](last)) + 4 + nudge)
          .attr("fill", s[1]).text(s[0] === "coverage" ? last.caught + "/" + wrong + " caught" : last.false_alarms + "/" + right + " flagged");
      });
    };
    if (AgentDiff.charts && AgentDiff.charts.responsive) AgentDiff.charts.responsive(host, draw, "fg-curve");
    else draw();
  }

  function seedPanel(H, root, compact) {
    var box = H("div", { class: "fg-seeds", "data-role": "seeds" });
    function paint() {
      box.innerHTML = "";
      var n = Seeds.list().length;
      box.appendChild(H("span", { text: n ? n + " step(s) marked on this page as eval seeds." :
        "Click a mark on the strip chart to mark that step as an eval seed." }));
      box.appendChild(H("button", { text: "Download marks", disabled: n ? null : "disabled",
        onclick: function () { Seeds.download(); } }));
      box.appendChild(H("button", { text: "Clear", disabled: n ? null : "disabled",
        onclick: function () { Seeds.clear(); paint(); } }));
    }
    paint();
    box.addEventListener("agentdiff-seeds", paint);
    global.addEventListener("agentdiff-seeds", paint);
    root.appendChild(box);
    if (compact) return;
    root.appendChild(H("p", { class: "fg-note" }, [H("span", { text: "Marks stay in this browser. Feed them back with " }),
      H("code", { class: "fg-code", text: "agentdiff batch --seeds eval-seeds.json" }),
      H("span", { text: " and each becomes a candidate eval, tested like any other." })]));
  }

  function halvesSay(e) {
    var h = e.halves || [];
    function one(x, label) {
      return label + ": caught " + x.caught + " of " + x.wrong + " wrong, flagged " + x.false_alarms + " of " + x.right + " right";
    }
    return (h[0] ? one(h[0], "learn half") : "") + (h[1] ? " · " + one(h[1], "held-out half") : "");
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var f = forgeOf(ctx);
    if (!f) {
      var raw = ctx.aggregate && ctx.aggregate.forge;
      return ctx.empty(el, raw && raw.reason ? "No evals forged: " + raw.reason + "." :
        "No evals forged: `agentdiff batch` writes them from a corpus.");
    }
    var compact = ctx.lane === "focus";
    var root = H("div", { class: "fg" + (compact ? " compact" : "") });
    el.appendChild(root);
    if (!compact) root.appendChild(H("p", { class: "fg-lede", text: f.narrative || "" }));

    var loop = H("section", { "data-role": "curve" });
    if (!compact) loop.appendChild(H("div", { class: "fg-h", text: "The suite, round by round" }));
    var host = H("div");
    loop.appendChild(host);
    root.appendChild(loop);
    curve(H, host, f.rounds || [], f.wrong, f.right, compact ? 92 : 132);

    if ((f.suite || []).length) {
      var sec = H("section", { "data-role": "suite" });
      sec.appendChild(H("div", { class: "fg-h", text: "Adopted — each held on tasks it was not written from" }));
      var list = H("ul", { class: "fg-list" });
      f.suite.forEach(function (e) {
        var li = H("li", { class: "fg-row", "data-eval": e.id, "data-source": e.source });
        li.appendChild(H("span", { class: "fg-src " + e.source, text: SOURCES[e.source] || e.source }));
        li.appendChild(H("span", { text: "flags a run when " + e.says }));
        if (compact) {
          var w = e.whole || {};
          li.appendChild(H("p", { class: "fg-meta", text: "caught " + w.caught + " of " + w.wrong + " wrong · flagged " +
            w.false_alarms + " of " + w.right + " right · adds " + (e.adds || []).length }));
        } else {
          li.appendChild(H("p", { class: "fg-meta", text: halvesSay(e) + " · adds " + (e.adds || []).length +
            " wrong run(s) nothing earlier caught · round " + e.round }));
          li.appendChild(H("p", { class: "fg-meta" }, [H("code", { text: e.id })]));
        }
        list.appendChild(li);
      });
      sec.appendChild(list);
      root.appendChild(sec);
    }

    var tally = f.tally || {};
    var keys = Object.keys(tally).filter(function (k) { return k !== "adopted"; });
    if (keys.length && !compact) {
      var rej = H("section", { "data-role": "rejected" });
      rej.appendChild(H("div", { class: "fg-h", text: "Not adopted" }));
      var chips = H("div", { class: "fg-tally" });
      var WHY = { noisy: "fired on right runs", blind: "caught no wrong run on a half",
                  redundant: "only caught runs already caught", untested: "a half too small to say" };
      keys.sort().forEach(function (k) {
        chips.appendChild(H("span", { class: "fg-chip", "data-status": k, text: tally[k] + " " + k + " — " + (WHY[k] || k) }));
      });
      rej.appendChild(chips);
      var more = H("details", { class: "fg-more" }, [H("summary", { text: "the ones the judge and the refinements proposed" })]);
      var ml = H("ul", { class: "fg-list" });
      (f.tested || []).filter(function (e) { return e.source === "judge" || e.source === "refined" || e.source === "seed"; })
        .slice(0, 24).forEach(function (e) {
          ml.appendChild(H("li", { class: "fg-row", "data-eval": e.id, "data-status": e.status }, [
            H("span", { class: "fg-src " + e.source, text: SOURCES[e.source] || e.source }),
            H("span", { text: e.status + (e.failed_on ? " on the " + e.failed_on + " half" : "") + " — flags a run when " + e.says }),
            H("p", { class: "fg-meta", text: halvesSay(e) })]));
        });
      more.appendChild(ml);
      rej.appendChild(more);
      root.appendChild(rej);
    }

    if ((f.uncaught || []).length) {
      var un = H("p", { class: "fg-uncaught", "data-role": "uncaught" });
      un.appendChild(H("b", { text: "No eval catches yet: " }));
      f.uncaught.forEach(function (r, i) {
        if (i) un.appendChild(H("span", { text: ", " }));
        un.appendChild(H("code", { text: r }));
      });
      un.appendChild(H("span", { text: ". Mark the step where one went wrong and it becomes the next candidate." }));
      root.appendChild(un);
    }

    if (compact) { seedPanel(H, root, true); return; }
    var j = f.judge || {};
    if (j.used) {
      root.appendChild(H("p", { class: "fg-note", "data-role": "judge", text: "An agent judge proposed " + j.proposed +
        " rule(s) over " + (j.rounds || []).length + " round(s); " + j.adopted + " adopted. It saw " + j.saw + "." }));
    }
    var led = f.ledger || {};
    if ((led.rechecked || []).length) {
      root.appendChild(H("p", { class: "fg-note", "data-role": "rechecked", text: "Carried in from earlier corpora: " +
        led.rechecked.map(function (r) { return r.id + " — " + r.status.replace(/_/g, " "); }).join("; ") + "." }));
    }
    seedPanel(H, root);
    if (f.caveat) root.appendChild(H("p", { class: "fg-note", text: f.caveat }));
  }

  AgentDiff.block({
    id: "forge",
    storyTitle: "The evals the traces wrote",
    title: "The evals the traces wrote",
    question: "Which evals, written from where runs went wrong, hold on tasks they were not written from — and what does nothing catch yet?",
    group: "signal",
    size: "wide",
    relevance: function (ctx) { return forgeOf(ctx) ? 0.99 : 0; },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
