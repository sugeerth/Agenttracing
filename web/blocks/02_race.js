/* AgentDiff blocks — the race: agents on one clock, streaming.
 *
 * Every other picture on the page is a reading of runs that have ended.
 * This one is the runs themselves, on the wall clock they shared: one
 * lane per agent, every action a mark at the second it started, as wide
 * as it took, coloured by what it was — exploring, editing, verifying,
 * running, researching, planning, delegating — and the model's own
 * thinking and talking as the quieter marks between them. Under each lane,
 * the tokens it has spent so far, on one scale for both, so the moment
 * one agent pulls away in spend is a place on the chart rather than a
 * number at the end.
 *
 * **Streaming.** Served by `agentdiff duel --live` or `agentdiff watch`,
 * the page receives each run as it grows (`State.data.live`), several times
 * a second; the lanes grow in place, the newest marks fade in, and a
 * pulsing line says where *now* is for a run still going. When a run
 * finishes, its lane takes the check's verdict.
 *
 * **Replay.** Opened from a file, the same block draws the finished runs
 * from the start times the harness recorded, with a scrubber: drag it to
 * any second and both lanes show only what had happened by then, with a
 * line under the chart saying what each agent was doing at that moment
 * and what it had spent. Play runs the clock forward, compressed.
 *
 * Nothing is inferred here: a step's position is its recorded
 * `started_s`; a run whose steps carry no start times is laid end to end
 * by duration and says so; a token line from estimates is dashed and
 * labelled as estimated.
 */
(function (global) {
  "use strict";

  var AgentDiff = global.AgentDiff;
  if (!AgentDiff || typeof AgentDiff.block !== "function") return;
  var L = AgentDiff.lib;
  var STYLE_ID = "agentdiff-race-css";

  var KIND = {
    explore: { color: "var(--ink-3)", label: "explore" },
    edit: { color: "var(--warn)", label: "edit" },
    verify: { color: "var(--good)", label: "verify" },
    run: { color: "var(--accent)", label: "run" },
    research: { color: "#7a5cc4", label: "research" },
    plan: { color: "#3f9aa6", label: "plan" },
    delegate: { color: "var(--bad)", label: "delegate" },
    other: { color: "var(--rule-2)", label: "other" },
    think: { color: "color-mix(in srgb, var(--ink-3) 35%, transparent)", label: "thinking" },
  };
  var ORDER = ["explore", "edit", "verify", "run", "research", "plan", "delegate", "other", "think"];

  // the same classes `agentdiff.duel.classify` uses, so the race and the
  // report agree on what an action was
  var VERIFY = /\b(pytest|py\.test|unittest|nosetests|tox|nox|jest|vitest|mocha|go\s+(test|build|vet)|cargo\s+(test|build|check|clippy)|(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck|check)|mvn\s+(test|verify)|gradle\w*\s+(test|build|check)|ruff|mypy|pyright|flake8|pylint|eslint|tsc\b|dotnet\s+(test|build)|ctest|bazel\s+test)/;
  var EXPLORE = { ls: 1, cat: 1, head: 1, tail: 1, rg: 1, grep: 1, egrep: 1, find: 1, fd: 1, tree: 1, wc: 1, pwd: 1,
                  nl: 1, less: 1, more: 1, file: 1, stat: 1, du: 1, which: 1, echo: 1, sed: 1, awk: 1, jq: 1, diff: 1 };
  var CLAUDE = { Read: "explore", Grep: "explore", Glob: "explore", LS: "explore", Edit: "edit", Write: "edit",
                 MultiEdit: "edit", NotebookEdit: "edit", WebSearch: "research", WebFetch: "research",
                 TodoWrite: "plan", Task: "delegate", Agent: "delegate" };

  function command(cmd) {
    cmd = String(cmd || "").replace(/^\s*(\/usr\/bin\/env\s+)?(\/bin\/|\/usr\/bin\/)?(ba|z)?sh\s+-l?c\s+/i, "")
      .replace(/^['"]|['"]$/g, "").trim();
    if (VERIFY.test(cmd)) return "verify";
    if (/(^|\s)(>|>>|tee\s|sed\s+-i|perl\s+-pi|patch\s|apply_patch|git\s+apply)/.test(cmd)) return "edit";
    var first = (cmd.split(/[\s;&|]+/)[0] || "").split("/").pop();
    if (first === "cd") {
      var rest = cmd.split(/&&|;/);
      if (rest.length > 1) return command(rest.slice(1).join("&&"));
    }
    if (EXPLORE[first] || /\bgit\s+(status|diff|log|show|ls-files|grep|blame|branch)\b/.test(cmd)) return "explore";
    return "run";
  }

  function kindOf(step) {
    var t = step.type, name = String(step.name || "");
    if (t === "reason" || t === "answer") return "think";
    if (t === "plan") return "plan";
    if (t === "search" || t === "retrieve") return "research";
    if (t === "read") return "explore";
    if (CLAUDE[name]) return CLAUDE[name];
    if (name === "apply_patch") return "edit";
    if (name === "shell" || name === "Bash" || name === "bash") {
      var cmd = step.input || "";
      if (name === "Bash" && /^\s*\{/.test(cmd)) {
        try { cmd = JSON.parse(cmd).command || ""; } catch (err) { /* keep */ }
      }
      return command(cmd);
    }
    if (step.span && step.span.agent === "sub-agent") return "delegate";
    return "other";
  }

  function ensureStyle() {
    L.style.once(STYLE_ID, [
      ".rc{display:flex;flex-direction:column;gap:10px}",
      ".rc-top{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}",
      ".rc-title{font-size:var(--fs-m);font-weight:600;color:var(--ink);margin:0}",
      ".rc-state{font-size:var(--fs-xs);font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:0 8px;",
      "line-height:20px;border-radius:999px;background:var(--surface-2);color:var(--ink-2)}",
      ".rc-state.live{color:var(--good)}",
      ".rc-state.live:before{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--good);",
      "margin-right:6px;vertical-align:0;animation:rc-pulse 1.1s ease-in-out infinite alternate}",
      "@keyframes rc-pulse{from{opacity:.35}to{opacity:1}}",
      ".rc-key{display:flex;flex-wrap:wrap;gap:3px 11px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".rc-key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}",
      ".rc svg text{font-family:inherit}",
      ".rc-lab{font-size:12px;font-weight:600;fill:var(--ink)}",
      ".rc-sub{font-size:11px;font-weight:500;fill:var(--ink-3)}",
      ".rc-num{font-size:11px;font-weight:700;fill:var(--ink-2);font-variant-numeric:tabular-nums}",
      ".rc-ok{fill:var(--good)}.rc-bad{fill:var(--bad)}",
      ".rc-axis{font-size:11px;font-weight:500;fill:var(--ink-3)}",
      ".rc-mark.fresh{animation:rc-in .45s ease-out both}",
      "@keyframes rc-in{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}",
      ".rc-now{animation:rc-pulse 1.1s ease-in-out infinite alternate}",
      "@media (prefers-reduced-motion:reduce){.rc-mark.fresh,.rc-now,.rc-state.live:before{animation:none}}",
      ".rc-ctl{display:flex;align-items:center;gap:10px;font-size:var(--fs-s);color:var(--ink-2)}",
      ".rc-ctl button{font:inherit;font-size:var(--fs-xs);font-weight:700;border:1px solid var(--rule-2);background:var(--surface);",
      "color:var(--ink);border-radius:6px;padding:2px 10px;cursor:pointer;min-width:64px}",
      ".rc-ctl input[type=range]{flex:1;min-width:120px;accent-color:var(--accent)}",
      ".rc-at{font-size:var(--fs-s);color:var(--ink-2);line-height:1.55;margin:0;min-height:1.55em}",
      ".rc-at b{color:var(--ink)}",
      ".rc-note{font-size:var(--fs-xs);color:var(--ink-3);margin:0;line-height:1.5}",
    ].join(""));
  }

  // -------------------------------------------------------------- the data

  function fmtS(s) {
    s = Math.max(0, s || 0);
    if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + "s";
    var m = Math.floor(s / 60);
    return m + "m " + String(Math.round(s % 60)).padStart(2, "0") + "s";
  }
  function fmtK(n) {
    n = n || 0;
    return n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + "k" : String(Math.round(n));
  }

  /* One lane: the run's steps placed on its own clock. A run with start
     times uses them; one without is laid end to end and says so. */
  function lane(agent, steps, extra) {
    var timed = steps.some(function (s) { return typeof s.started_s === "number"; });
    var t = 0, cum = 0, marks = [], curve = [[0, 0]];
    // a run whose steps carry measured counts draws them as they were
    // spent; one that reports usage only at the end (Codex, per turn) has
    // no honest curve until then
    var measuredSteps = steps.some(function (s) { return s.tokens_basis === "measured"; });
    steps.forEach(function (s, i) {
      var start = timed ? (typeof s.started_s === "number" ? s.started_s : t) : t;
      var dur = Math.max(0, +s.latency_s || 0);
      t = Math.max(t, start + dur);
      if (measuredSteps) {
        cum += s.tokens_basis === "measured" ? (+s.tokens || 0) : 0;
        curve.push([start + dur, cum]);
      }
      marks.push({ i: i, start: start, dur: dur, kind: kindOf(s), error: !!s.error,
                   say: (s.name || s.type) + (s.input ? ": " + String(s.input).slice(0, 90) : "") });
    });
    var totals = extra.totals || {};
    // the run's own clock when it reported one: a CLI's wall time includes
    // the start-up and the wrap-up no step covers
    var elapsed = Math.max(t, +(extra.elapsed_s || 0), extra.running ? 0 : +(totals.latency_s || 0));
    var measured = (+totals.input_tokens || 0) + (+totals.output_tokens || 0);
    var atEnd = !measuredSteps;
    if (atEnd && measured && !extra.running) curve = [[0, 0], [elapsed, measured]];
    return {
      agent: agent, marks: marks, curve: curve, end: elapsed, timed: timed,
      tokens: measuredSteps ? Math.max(cum, measured) : measured,
      tokensBasis: measuredSteps || measured ? "measured" : "unreported",
      atEnd: atEnd, running: !!extra.running, success: extra.success,
      cost: extra.cost, note: extra.note, run: extra.run, actions: marks.filter(function (m) { return m.kind !== "think"; }).length,
    };
  }

  /* The lanes for the task on screen: live runs first (running and
     finished), else the finished pair in the report, else nothing. */
  function lanesOf(ctx) {
    var state = AgentDiff.state ? AgentDiff.state() : {};
    var data = state.data || {};
    var live = data.live;
    var task = state.task;
    var out = [];
    if (live && (live.runs || []).length + (live.finished || []).length) {
      var tasks = {};
      (live.runs || []).concat(live.finished || []).forEach(function (r) { tasks[r.task] = 1; });
      var names = Object.keys(tasks).sort();
      var want = tasks[task] ? task : names[0];
      // one lane per agent, its latest run: finished runs arrive in run
      // order, and a run still going is newer than any finished one
      var byAgent = {};
      (live.finished || []).filter(function (r) { return r.task === want && r.trace; }).forEach(function (r) {
        byAgent[r.agent] = lane(r.agent, r.trace, { totals: r.totals, success: r.success, note: r.note,
          tokens_basis: r.tokens_basis, cost: r.vendor && r.vendor.cost_usd, run: r.run });
      });
      (live.runs || []).filter(function (r) { return r.task === want; }).forEach(function (r) {
        byAgent[r.agent] = lane(r.agent, r.steps || [], { running: true, totals: r.totals, elapsed_s: r.elapsed_s,
          tokens_basis: r.tokens_basis, cost: r.vendor && r.vendor.cost_usd, run: r.run });
      });
      out = Object.keys(byAgent).sort().map(function (k) { return byAgent[k]; });
      return { lanes: out, live: true, task: want };
    }
    var rep = ctx.report;
    if (rep && rep.a && rep.b && ((rep.a.steps || []).length || (rep.b.steps || []).length)) {
      ["a", "b"].forEach(function (side) {
        var s = rep[side];
        var vendor = null;
        out.push(lane((s.agent && s.agent.name) || side, s.steps || [], {
          totals: s.totals, success: s.outcome && s.outcome.success, note: s.outcome && s.outcome.note,
          cost: s.totals && s.totals.cost_usd ? s.totals.cost_usd : vendor }));
      });
      return { lanes: out, live: false, task: rep.task && rep.task.id };
    }
    return { lanes: [], live: false };
  }

  // ------------------------------------------------------------ the chart

  var seen = {};        // agent -> marks drawn, so only new ones fade in
  var clock = { at: null, playing: null };

  function draw(host, H, got, at) {
    var d3 = global.d3;
    host.innerHTML = "";
    if (!d3) return;
    var lanes = got.lanes;
    var width = Math.max(320, host.clientWidth || 800);
    var LANE = 26, CURVE = 22, GAP = 16, HEAD = 18;
    var labelW = Math.min(170, Math.max(96, width * 0.16));
    var padR = 16, padT = 20;
    var height = padT + lanes.length * (HEAD + LANE + CURVE + GAP) + 4;
    var end = d3.max(lanes, function (l) { return l.end; }) || 1;
    var x = d3.scaleLinear().domain([0, end * 1.02]).range([labelW, width - padR]);
    var maxTok = d3.max(lanes, function (l) { return Math.max(l.tokens, d3.max(l.curve, function (c) { return c[1]; }) || 0); }) || 1;
    var svg = d3.select(host).append("svg").attr("width", width).attr("height", height)
      .attr("viewBox", "0 0 " + width + " " + height).attr("role", "img")
      .attr("aria-label", lanes.map(function (l) {
        return l.agent + ": " + l.actions + " actions over " + fmtS(l.end) + ", " + fmtK(l.tokens) + " tokens" +
          (l.running ? ", running" : l.success === true ? ", passed" : l.success === false ? ", failed" : "");
      }).join("; "));

    x.ticks(Math.max(3, Math.floor(width / 120))).forEach(function (t) {
      svg.append("line").attr("x1", x(t)).attr("x2", x(t)).attr("y1", padT - 4).attr("y2", height - 4)
        .attr("stroke", "var(--rule)").attr("stroke-dasharray", "2,4");
      svg.append("text").attr("class", "rc-axis").attr("x", x(t)).attr("y", padT - 8).attr("text-anchor", "middle").text(fmtS(t));
    });

    var y = padT;
    lanes.forEach(function (l, li) {
      var colour = li === 0 ? "var(--a)" : li === 1 ? "var(--b)" : "var(--ink-3)";
      var head = y + 12;
      svg.append("rect").attr("x", 0).attr("y", head - 9).attr("width", 9).attr("height", 9).attr("rx", 2).attr("fill", colour);
      svg.append("text").attr("class", "rc-lab").attr("x", 14).attr("y", head).text(l.agent);
      var status = l.running ? "running" : l.success === true ? "passed" : l.success === false ? "failed" : "done";
      svg.append("text").attr("class", "rc-sub " + (status === "passed" ? "rc-ok" : status === "failed" ? "rc-bad" : ""))
        .attr("x", 14).attr("y", head + 15).text(status);
      var visible = l.marks.filter(function (m) { return at == null || m.start <= at; });
      var tokNow = at == null ? l.tokens : (function () {
        var v = 0;
        l.curve.forEach(function (c) { if (c[0] <= at) v = c[1]; });
        return v;
      })();
      var tokSay = l.atEnd && (l.running || (at != null && at < l.end)) ? "tokens reported at the end"
        : fmtK(tokNow) + " tok";
      svg.append("text").attr("class", "rc-num").attr("x", width - padR).attr("y", head).attr("text-anchor", "end")
        .text(visible.filter(function (m) { return m.kind !== "think"; }).length + " actions · " + tokSay +
              (typeof l.cost === "number" && at == null ? " · $" + l.cost.toFixed(4) : "") +
              " · " + fmtS(at == null ? l.end : Math.min(at, l.end)));
      y += HEAD;

      // the lane
      svg.append("rect").attr("x", x(0)).attr("y", y + LANE / 2 - 1).attr("width", Math.max(1, x(l.end) - x(0)))
        .attr("height", 2).attr("fill", "var(--rule)");
      var before = seen[l.agent] || 0;
      visible.forEach(function (m) {
        var think = m.kind === "think";
        var h = think ? 8 : LANE - 6;
        var r = svg.append("rect").attr("class", "rc-mark" + (m.i >= before && got.live ? " fresh" : ""))
          .attr("x", x(m.start)).attr("y", y + (LANE - h) / 2)
          .attr("width", Math.max(think ? 1.5 : 2.5, x(m.start + m.dur) - x(m.start)))
          .attr("height", h).attr("rx", 1.5).attr("fill", KIND[m.kind].color)
          .attr("data-kind", m.kind);
        if (m.error) r.attr("stroke", "var(--bad)").attr("stroke-width", 1.5);
        r.append("title").text(fmtS(m.start) + " · " + KIND[m.kind].label + (m.error ? " · failed" : "") + " — " + m.say);
      });
      if (got.live) seen[l.agent] = l.marks.length;
      if (l.running && at == null) {
        svg.append("line").attr("class", "rc-now").attr("x1", x(l.end)).attr("x2", x(l.end))
          .attr("y1", y).attr("y2", y + LANE).attr("stroke", colour).attr("stroke-width", 2);
      }
      y += LANE;

      // tokens so far, one scale for every lane
      var ty = d3.scaleLinear().domain([0, maxTok]).range([y + CURVE - 2, y + 2]);
      var pts = l.curve.filter(function (c) { return at == null || c[0] <= at; });
      if (pts.length > 1) {
        var area = d3.area().x(function (c) { return x(c[0]); }).y0(y + CURVE - 2).y1(function (c) { return ty(c[1]); })
          .curve(d3.curveStepAfter);
        svg.append("path").attr("d", area(pts)).attr("fill", colour).attr("fill-opacity", 0.12);
        var line = d3.line().x(function (c) { return x(c[0]); }).y(function (c) { return ty(c[1]); }).curve(d3.curveStepAfter);
        svg.append("path").attr("d", line(pts)).attr("fill", "none").attr("stroke", colour).attr("stroke-width", 1.5)
          .attr("stroke-dasharray", l.atEnd ? "4,3" : null).attr("data-series", "tokens");
      }
      svg.append("text").attr("class", "rc-sub").attr("x", labelW - 8).attr("y", y + CURVE - 6).attr("text-anchor", "end")
        .text(l.atEnd ? "tokens (at end)" : "tokens");
      y += CURVE + GAP;
    });
    if (at != null) {
      svg.append("line").attr("x1", x(at)).attr("x2", x(at)).attr("y1", padT - 4).attr("y2", height - 4)
        .attr("stroke", "var(--ink)").attr("stroke-width", 1.5).attr("data-role", "cursor");
    }
    return { x: x, end: end };
  }

  function sayAt(got, at) {
    return got.lanes.map(function (l) {
      var done = l.marks.filter(function (m) { return m.start <= at && m.kind !== "think"; });
      var last = done[done.length - 1];
      var tok = 0;
      l.curve.forEach(function (c) { if (c[0] <= at) tok = c[1]; });
      return { agent: l.agent, n: done.length, tok: tok, last: last, atEnd: l.atEnd,
               over: at >= l.end, success: l.success };
    });
  }

  function render(el, ctx) {
    ensureStyle();
    var H = ctx.h;
    var got = lanesOf(ctx);
    if (!got.lanes.length) return ctx.empty(el, "No runs to race: this needs two runs with timed steps, or a live duel.");
    var root = H("div", { class: "rc" });
    el.appendChild(root);
    var running = got.lanes.filter(function (l) { return l.running; }).length;
    var top = H("div", { class: "rc-top" }, [
      H("span", { class: "rc-state" + (got.live && running ? " live" : ""), "data-role": "state",
                  text: got.live ? (running ? "live · " + running + " running" : "live · finished") : "replay" }),
      H("p", { class: "rc-title", text: (got.task ? got.task + " — " : "") + got.lanes.map(function (l) { return l.agent; }).join(" vs ")
        + (got.lanes.some(function (l) { return typeof l.run === "number" && l.run > 1; })
          ? " · run " + d3.max(got.lanes, function (l) { return typeof l.run === "number" ? l.run : 0; }) : "") }),
    ]);
    root.appendChild(top);
    var key = H("div", { class: "rc-key" });
    ORDER.forEach(function (k) {
      if (got.lanes.some(function (l) { return l.marks.some(function (m) { return m.kind === k; }); })) {
        key.appendChild(H("span", null, [H("i", { style: { background: KIND[k].color } }), H("span", { text: KIND[k].label })]));
      }
    });
    root.appendChild(key);
    var host = H("div", { "data-role": "race" });
    root.appendChild(host);
    var end = Math.max.apply(null, got.lanes.map(function (l) { return l.end; })) || 1;

    // the scrubber: the same moment on every lane
    var ctl = H("div", { class: "rc-ctl" });
    var play = H("button", { text: "▶ Play", "aria-label": "Replay the race" });
    var range = H("input", { type: "range", min: "0", max: String(end), step: String(end / 400), value: String(end),
                             "aria-label": "Seconds into the race" });
    var readout = H("span", { class: "rc-num-out", text: fmtS(end) });
    ctl.appendChild(play);
    ctl.appendChild(range);
    ctl.appendChild(readout);
    var at = H("p", { class: "rc-at", "data-role": "at" });
    if (!(got.live && running)) {
      root.appendChild(ctl);
      root.appendChild(at);
    }

    function paint(t) {
      draw(host, H, got, t);
      if (t == null) { at.textContent = ""; return; }
      readout.textContent = fmtS(t);
      at.innerHTML = "";
      at.appendChild(H("b", { text: "At " + fmtS(t) + ": " }));
      sayAt(got, t).forEach(function (s, i) {
        if (i) at.appendChild(H("span", { text: "  ·  " }));
        at.appendChild(H("span", { text: s.agent + " — " + (s.over ? (s.success === true ? "done, passed" :
          s.success === false ? "done, failed" : "done") : s.n + " actions, " +
          (s.atEnd ? "tokens not reported until the turn ends" : fmtK(s.tok) + " tokens") +
          (s.last ? ", last: " + s.last.say.slice(0, 60) : "")) }));
      });
    }
    var redraw = function () { paint(clock.at); };
    if (AgentDiff.charts && AgentDiff.charts.responsive) AgentDiff.charts.responsive(host, redraw, "race");
    else redraw();
    if (clock.at != null) range.value = String(clock.at);

    range.addEventListener("input", function () {
      clock.at = +range.value >= end ? null : +range.value;
      paint(clock.at == null ? null : clock.at);
      if (clock.at == null) readout.textContent = fmtS(end);
    });
    play.addEventListener("click", function () {
      if (clock.playing) { global.cancelAnimationFrame(clock.playing); clock.playing = null; play.textContent = "▶ Play"; return; }
      var span = Math.min(12000, Math.max(3000, end * 250));     // compressed: at most twelve seconds
      var t0 = null;
      play.textContent = "❚❚ Pause";
      var step = function (ts) {
        if (t0 === null) t0 = ts - (clock.at != null && clock.at < end ? clock.at / end * span : 0);
        var t = Math.min(end, (ts - t0) / span * end);
        clock.at = t >= end ? null : t;
        range.value = String(t);
        paint(t >= end ? null : t);
        if (t < end) clock.playing = global.requestAnimationFrame(step);
        else { clock.playing = null; play.textContent = "▶ Play"; readout.textContent = fmtS(end); }
      };
      clock.playing = global.requestAnimationFrame(step);
    });

    if (ctx.lane === "focus") return;
    var untimed = got.lanes.filter(function (l) { return !l.timed; }).map(function (l) { return l.agent; });
    root.appendChild(H("p", { class: "rc-note", text: "Each mark starts at the second the step began and is as wide as it took" +
      (untimed.length ? "; " + untimed.join(", ") + " recorded no start times, so " + (untimed.length > 1 ? "their" : "its") +
       " steps are laid end to end" : "") + ". Token lines share one scale; a dashed one is a total its CLI reported " +
      "only when the turn ended, drawn as a step there rather than guessed along the way." }));
  }

  AgentDiff.block({
    id: "race",
    title: "The race",
    question: "Both agents on one clock: what each was doing, second by second, and what it had spent.",
    group: "outcome",
    size: "wide",
    lead: true,
    relevance: function (ctx) {
      var state = AgentDiff.state ? AgentDiff.state() : {};
      var live = (state.data || {}).live;
      if (live && ((live.runs || []).length || (live.finished || []).some(function (r) { return r.trace; }))) return 1;
      var duel = ctx.aggregate && ctx.aggregate.duel;
      var rep = ctx.report;
      var timed = rep && rep.a && (rep.a.steps || []).some(function (s) { return typeof s.started_s === "number"; });
      return duel && duel.measurable && timed ? 1 : 0;
    },
    render: render,
  });
})(typeof window !== "undefined" ? window : this);
