/* AgentDiff blocks — the execution trace of one run.
 *
 * Every other view reads a run as a set of numbers. This one reads it as
 * an execution: the steps in the order they happened, along the clock
 * they happened on, with what the engine already knows about each of
 * them drawn on top — the phases the impact section clustered, the
 * decisive step the diagnosis named, the harness's own interventions
 * (a cached read, a held answer, a refused write — `trace.SCAFFOLD_ACTIONS`,
 * read from the step's field and never from the prose beside it), the
 * divergence rows, the rewards
 * the environment paid, the evidence, the errors, the answer — and three
 * tracks underneath that accumulate as the run proceeds: the tokens
 * spent, the reward earned, the sources read.
 *
 * And it replays. The play button walks the playhead through the run at
 * the pace the trace recorded, revealing the steps up to it and showing
 * the state *so far* rather than the state at the end. That is a view of
 * recorded timings and nothing else: no step is re-executed, no model is
 * called, and the status line says so. `agentdiff replay` is the command
 * that actually re-executes; this is the picture of what was written down.
 *
 *   tr-timeline  the execution, at run / phase / step level, with replay
 *   tr-step      the selected step in full
 *   tr-compare   the other run on the same axis, with the alignment
 *   tr-detail    the run's existing readings, drawn in through renderInto
 *
 * Reads, from a pair report: `a`/`b` (steps), `rl` (rewards, values),
 * `impact` (clusters, lanes, marks), `alignment`, `divergences`,
 * `timing`, `trust`, `diagnosis`, `budget`, `fetches`, `milestones`;
 * from a bundle page: `DEEPCOMPARE_DATA.bundle.levels.records[key]`
 * (steps, budget, fetches, timeline). Nothing is computed here beyond
 * layout and the running sums the tracks draw, each of which is a
 * cumulative sum of a recorded per-step number.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var L = AgentDiff.lib;
  var d3 = global.d3;
  var isNum = L.fmt.isNum, num = L.fmt.num, secs = L.fmt.secs, pct = L.fmt.pct;
  var plural = L.fmt.plural, trunc = L.fmt.trunc, int = L.fmt.int || L.fmt.num;

  //: the step kinds, in the order a run tends to use them
  var KINDS = ["plan", "reason", "search", "retrieve", "read", "tool_call", "answer"];
  //: the largest number of step rectangles drawn one-per-step; past it the
  //: strip bins adjacent steps and the status line says how many to a bin
  /* `trace.SCAFFOLD_ACTIONS`: what the harness did to a step, as opposed
   * to what the agent did. A step carrying one of these is the loop's own
   * scaffold setting acting — the run would have gone differently under a
   * different harness, which is exactly what a reader comparing two runs
   * needs to see rather than infer. */
  var SCAFFOLD = {
    cache_hit: { glyph: "⟳", label: "served from the harness cache, not re-executed" },
    answer_gate: { glyph: "⊣", label: "answer held back until the required tool was called" },
    write_gate: { glyph: "⊘", label: "first write refused until something had been read" },
  };

  var STEP_CAP = 1200;
  //: a gap longer than this many seconds with no step in it is folded
  var QUIET_S = 2.0;
  var SPEEDS = [1, 4, 16];

  function ensureStyle() {
    L.style.once("trace", [
      ".trc{--trc-a:var(--a);--trc-b:var(--b)}",
      ".trc-lede{font-size:var(--fs-m);color:var(--ink);margin:0 0 8px;max-width:95ch}",
      ".trc-nums{font-size:var(--fs-xs);color:var(--ink-2);font-family:var(--mono);font-variant-numeric:tabular-nums;margin:0 0 8px}",
      ".trc-nums b{color:var(--ink)}.trc-nums .sep{color:var(--ink-3);margin:0 6px}",
      ".trc-bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 8px;font-size:var(--fs-xs)}",
      ".trc-btn{font:600 var(--fs-xs)/22px var(--sans);border:1px solid var(--rule-2);background:var(--surface);color:var(--ink-2);border-radius:7px;padding:0 9px;cursor:pointer}",
      ".trc-btn[aria-pressed=true],.trc-btn.on{background:color-mix(in srgb,var(--accent) 12%,var(--surface));color:var(--ink);border-color:var(--accent)}",
      ".trc-btn:focus-visible{outline:2px solid var(--accent);outline-offset:1px}",
      ".trc-scrub{flex:1 1 160px;min-width:120px}",
      ".trc-details{margin-top:8px}.trc-details summary{cursor:pointer;color:var(--ink-2);font-size:var(--fs-xs)}",
      ".trc-table{border-collapse:collapse;font-size:var(--fs-xs);font-variant-numeric:tabular-nums;margin-top:6px}",
      ".trc-table th,.trc-table td{padding:2px 8px;border-bottom:1px solid var(--rule);text-align:left;white-space:nowrap}",
      ".trc-table th{color:var(--ink-2);font-weight:600}.trc-table td.num,.trc-table th.num{text-align:right;font-family:var(--mono)}",
      ".trc-pick{font:400 var(--fs-xs)/22px var(--sans);border:1px solid var(--rule-2);background:var(--surface);color:var(--ink);border-radius:7px;padding:0 6px;max-width:min(360px,60vw)}",
      ".trc-crumb{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-3);margin:0 0 6px}",
      ".trc-crumb button{font:inherit;background:none;border:0;color:var(--ink-2);cursor:pointer;padding:0 2px;text-decoration:underline}",
      ".trc-stage{position:relative}",
      ".trc-stage:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}",
      ".trc-status{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0;max-width:100ch}",
      ".trc-note{font-size:var(--fs-xs);color:var(--ink-3);margin:8px 0 0;max-width:100ch}",
      ".trc-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:var(--fs-xs);color:var(--ink-3);margin-top:6px}",
      ".trc-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}",
      ".trc-kv{border-collapse:collapse;font-size:var(--fs-xs);width:100%;table-layout:fixed}",
      ".trc-kv th{text-align:left;font-family:var(--mono);font-weight:500;color:var(--ink-3);padding:3px 10px 3px 0;width:150px;vertical-align:top}",
      ".trc-kv td{color:var(--ink-2);padding:3px 0;vertical-align:top;overflow-wrap:anywhere}",
      ".trc-text{font-family:var(--mono);font-size:var(--fs-xs);color:var(--ink-2);background:var(--surface-2);border:1px solid var(--rule);border-radius:6px;padding:6px 8px;margin:4px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto}",
      ".trc-part{margin:12px 0 0}.trc-part h4{font:600 var(--fs-xs)/1.4 var(--sans);color:var(--ink-3);margin:0 0 4px;text-transform:uppercase;letter-spacing:.04em}",
      ".trc-tip{position:absolute;z-index:6}",
      ".trc-empty{font-size:var(--fs-xs);color:var(--ink-3);margin:6px 0 0}",
      ".trc-scroll{overflow-x:auto}",
      "@media (max-width:520px){.trc-kv th{width:110px}}",
    ].join("\n"));
  }

  // -------------------------------------------------------------- family

  var FAMILY = null, TIMING = { run: null, steps: null, cap: STEP_CAP }, TIMER = null;
  function family() {
    return FAMILY || (FAMILY = L.family("trace", {
      side: "a", run: null, step: null, level: "run", phase: null, playing: false, t: null, x: "time",
      speed: 4,
    }, { scope: "task", persist: true }));
  }
  function state() { return family().get(); }
  function select(patch) { family().set(patch); }
  function listen(el, fn) {
    family().subscribe(function (st) {
      try { fn(st); } catch (err) { if (global.console) console.warn("AgentDiff trace: repaint failed", err); }
    }, el);
  }

  function stopTimer() {
    if (TIMER) { global.clearInterval(TIMER); TIMER = null; }
  }
  function reduced() {
    try { return global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches; }
    catch (err) { return false; }
  }

  AgentDiff.trace = {
    select: select,
    state: function () {
      var st = state();
      return { side: st.side, run: st.run, step: st.step, level: st.level, phase: st.phase,
               playing: st.playing, t: st.t, x: st.x, speed: st.speed };
    },
    reset: function () { stopTimer(); family().reset(); },
    //: `t` in the state is the recorded second at which the step now
    //: showing began; the replay advances the playhead in recorded
    //: seconds and repaints when it crosses into the next step.
    //: the last draw times in ms and the step cap the strip binned at
    timing: function () { return { run: TIMING.run, steps: TIMING.steps, cap: TIMING.cap, tiled: TIMING.tiled || null }; },
    play: function (speed) { startReplay(speed === "step" ? "step" : (isNum(speed) ? speed : state().speed)); },
    pause: function () { stopTimer(); select({ playing: false }); },
    //: move the playhead to a step index without playing
    seek: function (index) { stopTimer(); select({ playing: false, step: isNum(index) ? Math.round(index) : null }); },
    /* A measurement hook, matching `AgentDiff.levels.tile`: repeat this
     * run's steps n times end to end and redraw, to time the strip at ten
     * times the shipped scale.  The status line says the steps are tiled
     * and by how much, so a tiled strip can never be read as a real run. */
    tile: function (n) {
      TILE = Math.max(1, Math.min(100, isNum(n) ? Math.round(n) : 1));
      stopTimer();
      select({ step: null, phase: null, level: "run", playing: false });
      if (typeof AgentDiff._rerender === "function") AgentDiff._rerender();
      return TILE;
    },
  };
  var TILE = 1;

  // --------------------------------------------------------------- model
  //
  // One run, read from whichever shape this page carries. Every field is
  // the engine's; `cum`, `rewardCum` and `sourcesCum` are running sums of
  // recorded per-step numbers and nothing else.

  function bundleRecords() {
    var d = global.DEEPCOMPARE_DATA, b = d && d.bundle && d.bundle.levels;
    return (b && b.records) || null;
  }

  /* The bundle's level-3 records that carry steps, in a stable order, as
   * options for the picker.  A bundle page holds every run of every member
   * — 322 in the shipped demo — and without this the trace view could only
   * ever show the pair the report happens to open on. */
  function traceableRecords() {
    var recs = bundleRecords();
    if (!recs) return [];
    return Object.keys(recs).sort().filter(function (k) {
      var r = recs[k];
      return r && Array.isArray(r.steps) && r.steps.length;
    }).map(function (k) {
      var r = recs[k];
      return {
        key: k, agent: r.agent || k, task: r.task || null,
        steps: r.steps.length, success: r.success,
        label: (r.agent || k) + " \u00b7 " + (r.task || "?") + " \u00b7 " + plural(r.steps.length, "step")
               + (r.success === true ? " \u00b7 passed" : r.success === false ? " \u00b7 failed" : ""),
      };
    });
  }

  function sideName(report, side) {
    var s = report && report[side];
    return (s && s.agent && s.agent.name) || String(side || "").toUpperCase();
  }

  function marksOf(report, side) {
    var out = { fault: {}, decisive: null, divergence: {}, milestone: {} };
    if (!report) return out;
    var diag = report.diagnosis || {};
    var dec = diag.decisive_step;
    if (dec && isNum(dec.step)) out.decisive = dec.step;
    (report.divergences || []).forEach(function (d) {
      var i = side === "a" ? d.a_index : d.b_index;
      if (isNum(i)) out.divergence[i] = d;
    });
    var imp = report.impact && report.impact[side];
    (imp && imp.clusters || []).forEach(function (c) {
      (c.marks || []).forEach(function (m) {
        if (m && isNum(m.step) && m.kind === "fault") out.fault[m.step] = m.label || "fault";
      });
    });
    var ms = report.milestones && report.milestones[side];
    (ms && ms.milestones || []).forEach(function (m) {
      if (m && m.reached && isNum(m.step)) out.milestone[m.step] = m.label || m.id;
    });
    return out;
  }

  function rewardsOf(report, side) {
    var rl = report && report.rl && report.rl[side];
    var by = {};
    (rl && rl.rewards || []).forEach(function (r) { if (isNum(r.step)) by[r.step] = r; });
    return { by: by, source: (report && report.rl && report.rl.source) || null };
  }

  function fetchesOf(holder) {
    var f = holder && holder.fetches, by = {};
    (f && f.records || []).forEach(function (r) { if (isNum(r.index)) by[r.index] = r; });
    return by;
  }

  /* The steps of one run, each with what the engine recorded for it and
   * the running sums the tracks draw. `steps` are SCHEMA steps. */
  /* Where a step sits on the clock.
   *
   * `started_s` is what the trace says; the running sum is what a reader
   * is left to assume when it is absent — and that assumption is that the
   * run was strictly sequential. All or nothing: a strip with some steps
   * on a recorded clock and some on a running sum would be two pictures
   * drawn over each other, and would not say which was which.
   */
  function clockBasis(steps) {
    var n = (steps || []).length;
    if (!n) return "reconstructed";
    var got = 0;
    (steps || []).forEach(function (s) { if (isNum(s.started_s)) got += 1; });
    return got === n ? "recorded" : "reconstructed";
  }

  function buildSteps(steps, rewards, fetches, marks) {
    var out = [], tokens = 0, reward = 0, sources = 0, t = 0, evidence = 0, seen = {};
    var basis = clockBasis(steps);
    var zero = basis === "recorded" && (steps || []).length
      ? Math.min.apply(null, (steps || []).map(function (s) { return s.started_s; })) : 0;
    (steps || []).forEach(function (s, i) {
      var index = isNum(s.index) ? s.index : i;
      var dur = isNum(s.latency_s) ? s.latency_s : 0;
      var rw = rewards.by[index] || null;
      var r = isNum(s.reward) ? s.reward : (rw && isNum(rw.reward) ? rw.reward : null);
      var f = fetches[index] || null;
      var isFetch = ["search", "retrieve", "read", "tool_call"].indexOf(s.type) >= 0;
      if (isNum(s.tokens)) tokens += s.tokens;
      if (isNum(r)) reward += r;
      if (isFetch) {
        var key = String(s.name || "") + "\u0000" + String(s.input || "");
        if (!seen[key]) { seen[key] = true; sources += 1; }
      }
      var good = (isNum(r) && r > 0) || s.quality === "good";
      if (good) evidence += 1;
      out.push({
        index: index, kind: s.type || "step", name: s.name || s.type || "step",
        tokens: isNum(s.tokens) ? s.tokens : null, basis: s.tokens_basis || null,
        // the counts the trace carries, kept apart from the total: context
        // re-sent and text generated cost differently, and an input the
        // provider served from its own cache was not paid for at all
        inTokens: isNum(s.input_tokens) ? s.input_tokens : null,
        outTokens: isNum(s.output_tokens) ? s.output_tokens : null,
        cachedTokens: isNum(s.cached_tokens) ? s.cached_tokens : null,
        latency: dur, t0: basis === "recorded" ? s.started_s - zero : t,
        t1: (basis === "recorded" ? s.started_s - zero : t) + dur, clock: basis,
        error: !!s.error, effect: s.effect || null, quality: s.quality || null,
        reward: r, value: isNum(s.value) ? s.value : (rw && isNum(rw.value) ? rw.value : null),
        advantage: isNum(s.advantage) ? s.advantage : null,
        why: rw && rw.why ? rw.why : null, labels: (rw && rw.labels) || [],
        model: (s.model && (s.model.name || s.model.model)) || null,
        span: (s.span && s.span.agent) || null,
        input: typeof s.input === "string" ? s.input : "", output: typeof s.output === "string" ? s.output : "",
        inputChars: isNum(s.input_chars) ? s.input_chars : (typeof s.input === "string" ? s.input.length : null),
        outputChars: isNum(s.output_chars) ? s.output_chars : (typeof s.output === "string" ? s.output.length : null),
        fetch: f, evidence: good, isFetch: isFetch,
        cum: tokens, rewardCum: reward, sourcesCum: sources, evidenceCum: evidence,
        fault: marks.fault[index] || null, decisive: marks.decisive === index,
        divergence: marks.divergence[index] || null, milestone: marks.milestone[index] || null,
        // what the *harness* did to this step, not the agent: a cached
        // read, a held answer, a refused write. Read from the trace's own
        // `scaffold` field and never from the prose note beside it — a
        // reading that matched on English would stop finding these the day
        // the sentence was reworded.
        scaffold: SCAFFOLD[s.scaffold] ? s.scaffold : null,
      });
      t += dur;
    });
    return out;
  }

  /* Repeat a run's steps n times end to end, each copy continuing the
   * clock and the running sums, for the scale measurement.  Copies are
   * marked `tiled` and their indices are offset so no copy collides with
   * a real step's marks. */
  function tiled(steps) {
    if (TILE <= 1 || !steps.length) return steps;
    var out = steps.slice(), last = steps[steps.length - 1];
    for (var k = 1; k < TILE; k++) {
      steps.forEach(function (s) {
        var c = Object.assign({}, s);
        c.index = s.index + k * (last.index + 1);
        c.t0 = s.t0 + k * last.t1; c.t1 = s.t1 + k * last.t1;
        c.cum = s.cum + k * last.cum; c.rewardCum = s.rewardCum + k * last.rewardCum;
        c.sourcesCum = s.sourcesCum + k * last.sourcesCum; c.evidenceCum = s.evidenceCum + k * last.evidenceCum;
        c.tiled = true; c.decisive = false; c.divergence = null; c.milestone = null; c.fault = null;
        out.push(c);
      });
    }
    return out;
  }

  function phasesOf(report, side, steps) {
    var imp = report && report.impact && report.impact[side];
    var clusters = (imp && imp.clusters) || [];
    if (!clusters.length || !steps.length) return [];
    return clusters.map(function (c) {
      var from = steps.filter(function (s) { return s.index >= c.from && s.index <= c.to; });
      return {
        id: c.id, from: c.from, to: c.to, kind: c.kind, label: c.label || c.kind,
        why: c.why || "", impact: isNum(c.impact) ? c.impact : null, seconds: c.seconds,
        t0: from.length ? from[0].t0 : 0, t1: from.length ? from[from.length - 1].t1 : 0,
        steps: from.length,
      };
    });
  }

  /* The model for the page's current selection: a side of the pair, or a
   * bundle record when the page is a bundle's and one is chosen. */
  function model(ctx) {
    var st = state(), report = ctx.report;
    var records = bundleRecords();
    if (st.run && records && records[st.run] && Array.isArray(records[st.run].steps) && records[st.run].steps.length) {
      var rec = records[st.run];
      var marks = { fault: {}, decisive: null, divergence: {}, milestone: {} };
      var steps = buildSteps(rec.steps, { by: {} }, fetchesOf(rec), marks);
      return {
        kind: "record", key: st.run, name: rec.agent || rec.key || st.run, task: rec.task || null,
        steps: tiled(steps), clock: clockBasis(rec.steps), phases: [], marks: marks, tiled: TILE > 1 ? TILE : null,
        source: rec.steps_source || "the bundle's record",
        success: rec.success, synthetic: !!rec.synthetic, budget: rec.budget || null, fetchesSec: rec.fetches || null,
        termination: null, stoppedBy: null, totalS: steps.length ? steps[steps.length - 1].t1 : 0,
      };
    }
    if (!report || !report[st.side] || !Array.isArray(report[st.side].steps)) return null;
    var side = st.side, holder = report[side];
    var marks2 = marksOf(report, side);
    var steps2 = buildSteps(holder.steps, rewardsOf(report, side), fetchesOf(report[side] ? { fetches: report.fetches && report.fetches[side] } : null), marks2);
    var trust = report.trust && report.trust[side] && report.trust[side].behaviour;
    var timing = report.timing && report.timing[side];
    return {
      kind: "side", side: side, key: null, name: sideName(report, side),
      task: (report.task && report.task.id) || null,
      steps: tiled(steps2), clock: clockBasis(holder.steps), phases: TILE > 1 ? [] : phasesOf(report, side, steps2), marks: marks2, tiled: TILE > 1 ? TILE : null,
      source: "the pair report", success: holder.outcome && holder.outcome.success,
      synthetic: !!(holder.harness && String(holder.harness.note || "").toUpperCase().indexOf("SYNTHETIC") === 0),
      budget: report.budget && report.budget[side], fetchesSec: report.fetches && report.fetches[side],
      termination: (trust && trust.termination) || null, stoppedBy: (trust && trust.stopped_by) || null,
      totalS: (timing && isNum(timing.total_s)) ? timing.total_s : (steps2.length ? steps2[steps2.length - 1].t1 : 0),
      wastedS: timing && isNum(timing.wasted_s) ? timing.wasted_s : null,
    };
  }

  //: the quiet stretches between steps, folded by the library's law
  function foldsOf(steps) {
    var out = [];
    for (var i = 1; i < steps.length; i++) {
      var gap = steps[i].t0 - steps[i - 1].t1;
      if (gap > QUIET_S) out.push({ after: i - 1, seconds: gap, width: L.glyph.foldSeconds(gap) });
    }
    return out;
  }

  // ------------------------------------------------------------ the strip
  //
  // One drawing, used by the timeline and by the comparison, so the two
  // never disagree about what a step looks like.

  function kindColour(kind) {
    var i = KINDS.indexOf(kind);
    return i >= 0 ? d3.schemeSet2[i % d3.schemeSet2.length] : "var(--ink-3)";
  }

  function stripScale(m, width, x) {
    var steps = m.steps;
    if (!steps.length) return null;
    if (x === "steps") {
      var s = d3.scaleLinear().domain([0, steps.length]).range([0, width]);
      return { at: function (d, i) { return [s(i), Math.max(1, s(i + 1) - s(i) - 1)]; }, folds: [], axis: s, kind: "steps" };
    }
    var folds = foldsOf(steps), folded = 0, offsets = [];
    var total = steps[steps.length - 1].t1 - steps[0].t0;
    var foldWidth = folds.reduce(function (a, f) { return a + f.width; }, 0);
    var sec = d3.scaleLinear().domain([0, Math.max(total - folds.reduce(function (a, f) { return a + f.seconds; }, 0), 0.001)])
      .range([0, Math.max(width - foldWidth, 10)]);
    steps.forEach(function (st, i) {
      var f = folds.filter(function (v) { return v.after < i; });
      folded = f.reduce(function (a, v) { return a + v.seconds; }, 0);
      var extra = f.reduce(function (a, v) { return a + v.width; }, 0);
      offsets.push({ x: sec(st.t0 - steps[0].t0 - folded) + extra, w: Math.max(1.5, sec(st.latency) ) });
    });
    return {
      at: function (d, i) { return [offsets[i].x, offsets[i].w]; },
      folds: folds.map(function (f) {
        var i = f.after;
        return { x: offsets[i].x + offsets[i].w, width: f.width, seconds: f.seconds };
      }),
      axis: sec, kind: "time",
    };
  }

  /* Bin adjacent steps when there are more of them than the cap, so the
   * strip stays one rectangle per thing a reader can point at. */
  function binned(steps, cap) {
    if (steps.length <= cap) return { rows: steps, per: 1 };
    var per = Math.ceil(steps.length / cap), rows = [];
    for (var i = 0; i < steps.length; i += per) {
      var chunk = steps.slice(i, i + per);
      var tok = chunk.reduce(function (a, s) { return a + (isNum(s.tokens) ? s.tokens : 0); }, 0);
      rows.push({
        index: chunk[0].index, last: chunk[chunk.length - 1].index, n: chunk.length,
        kind: chunk[0].kind, name: plural(chunk.length, "step"), tokens: tok,
        t0: chunk[0].t0, t1: chunk[chunk.length - 1].t1, latency: chunk[chunk.length - 1].t1 - chunk[0].t0,
        error: chunk.some(function (s) { return s.error; }),
        decisive: chunk.some(function (s) { return s.decisive; }),
        cum: chunk[chunk.length - 1].cum, rewardCum: chunk[chunk.length - 1].rewardCum,
        sourcesCum: chunk[chunk.length - 1].sourcesCum, reward: null, evidence: chunk.some(function (s) { return s.evidence; }),
      });
    }
    return { rows: rows, per: per };
  }

  // ------------------------------------------------------------- replay

  /* Replay.  `speed` is a multiple of the *recorded* clock: at 4× one real
   * second carries the playhead through four recorded seconds, so a step
   * the trace says took 4.7s occupies four times longer on screen than one
   * that took 1.2s.  That is the whole point — a replay that gave every
   * step an equal tick would be a slideshow claiming to be a clock — and it
   * is why the buttons say "× recorded" rather than a bare number.
   *
   * Two departures from the recorded clock, both of them the picture's:
   * a quiet stretch the strip folded is skipped here too (otherwise the
   * playhead would sit still over a fold for as long as the fold hides),
   * and `speed === "step"` is the honest name for one step per tick, for
   * a run whose latencies are all but equal or absent.  No step is
   * re-executed in either mode.
   */
  var TICK_MS = 60;
  function startReplay(speed) {
    stopTimer();
    var st = state();
    var byStep = speed === "step";
    //: under prefers-reduced-motion there is no animation to watch: the
    //: playhead lands on the end of the run at once and the scrubber and
    //: the arrow keys remain the way through it.  This is the whole
    //: replay, not a shortened one — every state the replay would have
    //: shown is still reachable a step at a time.
    if (reduced()) {
      var mr = LAST_MODEL;
      select({ playing: false, speed: byStep ? "step" : speed,
               step: mr && mr.steps.length ? mr.steps[mr.steps.length - 1].index : null,
               t: mr && mr.steps.length ? mr.steps[mr.steps.length - 1].t0 : null,
               level: st.level === "step" ? "run" : st.level });
      return;
    }
    select({ playing: true, speed: byStep ? "step" : speed, level: st.level === "step" ? "run" : st.level });
    //: the folded gaps, so the playhead crosses them in the time the fold
    //: is drawn rather than in the time it stands for
    var m0 = LAST_MODEL;
    var skip = {};
    if (m0) foldsOf(m0.steps).forEach(function (f) { skip[f.after] = f.seconds; });
    var clock = null;
    TIMER = global.setInterval(function () {
      var s = state();
      if (!s.playing) { stopTimer(); return; }
      var host = document.querySelector('[data-block="tr-timeline"]');
      if (!host || !document.contains(host)) { stopTimer(); return; }
      var m = LAST_MODEL;
      if (!m || !m.steps.length) { stopTimer(); select({ playing: false }); return; }
      var i = isNum(s.step) ? m.steps.findIndex(function (v) { return v.index === s.step; }) : -1;
      if (byStep) {
        if (i + 1 >= m.steps.length) { stopTimer(); select({ playing: false, step: m.steps[m.steps.length - 1].index }); return; }
        select({ step: m.steps[i + 1].index });
        return;
      }
      //: the playhead in recorded seconds; it starts at the end of the step
      //: the selection is already on, so play resumes rather than restarts
      if (clock === null) clock = i < 0 ? 0 : m.steps[i].t1;
      clock += (TICK_MS / 1000) * Math.max(0.25, speed);
      //: a step whose own duration exceeds what one tick can cross still
      //: advances, so a single slow step never stalls the replay for ever
      var next = i + 1;
      if (next >= m.steps.length) { stopTimer(); select({ playing: false, step: m.steps[m.steps.length - 1].index }); return; }
      if (skip[i] && clock < m.steps[next].t0) clock = m.steps[next].t0;
      var target = i;
      while (target + 1 < m.steps.length && clock >= m.steps[target + 1].t0) target += 1;
      if (target === i) return;
      //: `t` is the recorded second the step now showing *began*, not the
      //: accumulator's current value — a number the trace states, so a
      //: reader of the family never sees a clock that drifted between
      //: repaints.  The repaint happens on a step change and not on every
      //: tick, which is what keeps a 600-step run's replay cheap.
      select({ step: m.steps[target].index, t: m.steps[target].t0 });
    }, TICK_MS);
  }

  var LAST_MODEL = null;
  /* Keyboard navigation and a re-rendering page pull against each other:
   * every arrow key changes the family, and four blocks subscribe to it,
   * so the element the key was pressed on is replaced more than once
   * before the dust settles.  A one-shot "restore the focus" flag loses
   * that race.  Instead this stays true for as long as the reader is
   * driving by keyboard — every render re-asserts the focus — and is
   * cleared only when they take it elsewhere themselves, by Tab or by
   * pointer.  Without it the view is reachable by mouse only. */
  var FOCUS_STAGE = false;
  /* Look the stage up in the live DOM rather than holding the node: a
   * re-render replaces the whole stack, so the element this render made is
   * very often already detached by the time a frame has passed.  Each
   * render in the cascade schedules one of these and the last one lands. */
  function restoreFocus() {
    global.setTimeout(function () {
      if (!FOCUS_STAGE) return;
      var live = document.querySelector('[data-block="tr-timeline"] [role="application"]');
      if (!live || live === document.activeElement) return;
      try { live.focus({ preventScroll: true }); } catch (err) { live.focus(); }
    }, 0);
  }

  // ----------------------------------------------------------- timeline

  function drawStrip(host, m, opts) {
    opts = opts || {};
    var width = L.layout.measure(host, 280, 1400);
    var st = state();
    var rows = binned(m.steps, STEP_CAP);
    var scale = stripScale({ steps: m.steps }, width, st.x);
    if (!scale) return null;
    var laneNames = [];
    m.steps.forEach(function (s) { var n = s.span || m.name; if (laneNames.indexOf(n) < 0) laneNames.push(n); });
    var laneH = 26, phaseH = m.phases.length ? 16 : 0, markH = 14;
    var trackH = opts.tracks === false ? 0 : 34;
    var padT = phaseH + markH, padB = 16;
    var height = padT + laneNames.length * laneH + padB + trackH * (opts.tracks === false ? 0 : 3);
    var maxTok = d3.max(m.steps, function (s) { return isNum(s.tokens) ? s.tokens : 0; }) || 1;
    var cutoff = isNum(st.step) ? st.step : null;

    var label = m.name + ": " + plural(m.steps.length, "step") + " along " +
      (st.x === "time" ? "constricted wall-clock time, " + secs(m.totalS) + " in all" : "step order") +
      ", coloured by kind, height by tokens" + (cutoff !== null ? ", revealed to step " + cutoff : "");
    var svg = d3.select(L.svg({ viewBox: "0 0 " + width + " " + height, width: "100%", height: height, "aria-label": label }));

    // phases
    if (m.phases.length) {
      m.phases.forEach(function (p) {
        var first = m.steps.findIndex(function (s) { return s.index >= p.from; });
        var last = m.steps.map(function (s) { return s.index; }).lastIndexOf(p.to);
        if (first < 0) return;
        if (last < first) last = first;
        var a = scale.at(m.steps[first], first), b = scale.at(m.steps[last], last);
        var x0 = a[0], x1 = b[0] + b[1];
        svg.append("rect").attr("class", "trc-phase").attr("data-phase", p.id)
          .attr("x", x0).attr("y", 0).attr("width", Math.max(1, x1 - x0)).attr("height", phaseH - 3)
          .attr("rx", 2).attr("fill", p.kind === "hot" ? "var(--bad)" : p.kind === "quiet" ? "var(--rule-2)" : "var(--accent)")
          .attr("fill-opacity", p.kind === "quiet" ? 0.5 : 0.22).style("cursor", "pointer")
          .on("click", function () { select({ level: "phase", phase: p.id }); });
        if (x1 - x0 > 46) {
          svg.append("text").attr("x", x0 + 4).attr("y", phaseH - 7).attr("class", "lab")
            .attr("font-size", 10).attr("fill", "var(--ink-3)").text(trunc(p.label, Math.floor((x1 - x0) / 6)));
        }
      });
    }

    // the steps, one lane per acting agent
    rows.rows.forEach(function (s, i) {
      var pos = scale.at(s, i);
      var lane = laneNames.indexOf(s.span || m.name);
      if (lane < 0) lane = 0;
      var h = Math.max(4, Math.round((isNum(s.tokens) ? s.tokens : 0) / maxTok * (laneH - 8)) + 4);
      var y = padT + lane * laneH + (laneH - h) - 4;
      var hidden = cutoff !== null && s.index > cutoff;
      var g = svg.append("g").attr("class", "trc-step").attr("data-step", s.index)
        .attr("opacity", hidden ? 0.12 : 1).style("cursor", "pointer")
        .on("click", function () { stopTimer(); select({ playing: false, level: "step", step: s.index }); });
      g.append("rect").attr("x", pos[0]).attr("y", y).attr("width", pos[1]).attr("height", h)
        .attr("rx", 1.5).attr("fill", kindColour(s.kind))
        .attr("fill-opacity", s.basis && s.basis !== "measured" ? 0.55 : 0.95)
        .attr("stroke", s.error ? "var(--bad)" : s.decisive ? "var(--ink)" : "none")
        .attr("stroke-width", s.error || s.decisive ? 1.5 : 0);
      if (isNum(s.reward) && s.reward !== 0) {
        var up = s.reward > 0;
        g.append("line").attr("class", "trc-reward").attr("x1", pos[0] + pos[1] / 2).attr("x2", pos[0] + pos[1] / 2)
          .attr("y1", up ? y - 2 : y + h + 2).attr("y2", up ? y - 7 : y + h + 7)
          .attr("stroke", up ? "var(--good)" : "var(--bad)").attr("stroke-width", 1.5);
      }
      if (s.evidence) g.append("circle").attr("class", "trc-ev").attr("cx", pos[0] + pos[1] / 2).attr("cy", y - 4).attr("r", 1.8).attr("fill", "var(--good)");
    });

    // the folds, drawn where the clock was folded
    scale.folds.forEach(function (f) {
      svg.append("line").attr("class", "trc-fold").attr("x1", f.x).attr("x2", f.x + f.width)
        .attr("y1", padT + laneNames.length * laneH - 6).attr("y2", padT + laneNames.length * laneH - 6)
        .attr("stroke", "var(--ink-3)").attr("stroke-dasharray", "1 2");
    });

    // the marks a reader looks for
    m.steps.forEach(function (s, i) {
      var pos = scale.at(s, i), marks = [];
      if (s.decisive) marks.push(["◆", "var(--ink)", "decisive step"]);
      if (s.fault) marks.push(["▲", "var(--bad)", s.fault]);
      if (s.divergence) marks.push(["┃", "var(--warn)", "divergence " + (s.divergence.kind || "")]);
      if (s.milestone) marks.push(["●", "var(--good)", s.milestone]);
      if (s.scaffold) marks.push([SCAFFOLD[s.scaffold].glyph, "var(--warn)",
                                  "the harness: " + SCAFFOLD[s.scaffold].label]);
      marks.forEach(function (mk, k) {
        svg.append("text").attr("class", "trc-mark").attr("data-mark", mk[2])
          .attr("x", pos[0] + pos[1] / 2).attr("y", phaseH + 10 + k * 0).attr("text-anchor", "middle")
          .attr("font-size", 8).attr("fill", mk[1]).text(mk[0]);
      });
    });

    // three tracks that accumulate: tokens, reward, sources
    if (opts.tracks !== false) {
      var base = padT + laneNames.length * laneH + 6;
      [["tokens", function (s) { return s.cum; }, "var(--accent)"],
       ["reward", function (s) { return s.rewardCum; }, "var(--good)"],
       ["sources read", function (s) { return s.sourcesCum; }, "var(--ink-2)"]].forEach(function (track, k) {
        var acc = m.steps.map(track[1]);
        var lo = Math.min(0, d3.min(acc) || 0), hi = Math.max(d3.max(acc) || 1, lo + 0.001);
        var y = d3.scaleLinear().domain([lo, hi]).range([base + (k + 1) * trackH - 8, base + k * trackH + 4]);
        var visible = m.steps.filter(function (s) { return cutoff === null || s.index <= cutoff; });
        if (visible.length > 1) {
          var line = d3.line().x(function (s) { var i = m.steps.indexOf(s); var p = scale.at(s, i); return p[0] + p[1]; }).y(function (s) { return y(track[1](s)); });
          svg.append("path").attr("class", "trc-track").attr("data-track", track[0])
            .attr("d", line(visible)).attr("fill", "none").attr("stroke", track[2]).attr("stroke-width", 1.2);
        }
        svg.append("text").attr("x", 0).attr("y", base + k * trackH + 9).attr("font-size", 9).attr("fill", "var(--ink-3)").text(track[0]);
      });
    }

    // the playhead
    if (cutoff !== null) {
      var idx = m.steps.findIndex(function (s) { return s.index === cutoff; });
      if (idx >= 0) {
        var p = scale.at(m.steps[idx], idx);
        svg.append("line").attr("class", "trc-playhead").attr("x1", p[0] + p[1]).attr("x2", p[0] + p[1])
          .attr("y1", phaseH).attr("y2", height - 2).attr("stroke", "var(--accent)").attr("stroke-width", 1);
      }
    }

    var tip = L.svg.tip(host, { class: "trc-tip" });
    svg.selectAll("g.trc-step").on("mouseenter", function (evt) {
      var i = +this.getAttribute("data-step");
      var s = m.steps.filter(function (v) { return v.index === i; })[0];
      if (!s) return;
      tip.show(evt, [
        { b: true, text: "step " + s.index + " · " + s.kind + " · " + s.name },
        { mono: true, text: (isNum(s.tokens) ? plural(s.tokens, "token") + " (" + (s.basis || "basis not recorded") + ")" : "tokens not recorded") + " · " + secs(s.latency) },
        isNum(s.reward) ? { mono: true, text: "reward " + num(s.reward, 2) + " · running " + num(s.rewardCum, 2) } : null,
        s.error ? { text: "errored" } : null,
        s.decisive ? { text: "the decisive step" } : null,
        s.scaffold ? { text: "the harness: " + SCAFFOLD[s.scaffold].label } : null,
        s.fault ? { text: s.fault } : null,
        s.model ? { mono: true, text: "model " + s.model } : null,
      ]);
    }).on("mouseleave", function () { tip.hide(); });
    host.appendChild(svg.node());
    return { rows: rows, laneNames: laneNames, scale: scale };
  }

  /* Every chart on this page has a table view: the drawing is the fast
   * read and the table is the exact one, and a reader who cannot use the
   * drawing at all still has every number.  This is the timeline's. */
  function stepTable(H, m, cutoff) {
    var rows = (cutoff === null ? m.steps : m.steps.filter(function (s) { return s.index <= cutoff; }));
    var head = ["#", "kind", "name", "lane", "tokens", "basis", "cum", "t0 (s)", "latency (s)",
                "reward", "running", "value", "effect", "error", "sources so far", "model", "marks"];
    var tbl = H("table", { class: "trc-table" });
    tbl.appendChild(H("thead", null, H("tr", null, head.map(function (c) {
      return H("th", { class: /\(s\)|tokens|cum|reward|running|value|#|so far/.test(c) ? "num" : null, text: c });
    }))));
    var body = H("tbody");
    rows.forEach(function (s) {
      var marks = [];
      if (s.decisive) marks.push("decisive");
      if (s.fault) marks.push("fault");
      if (s.divergence) marks.push("divergence");
      if (s.milestone) marks.push("milestone");
      if (s.evidence) marks.push("evidence");
      if (s.tiled) marks.push("tiled copy");
      body.appendChild(H("tr", { "data-row-step": String(s.index) }, [
        String(s.index), s.kind, s.name, s.span || "—",
        isNum(s.tokens) ? int(s.tokens) : "not recorded", s.basis || "not recorded", int(s.cum),
        num(s.t0, 2), num(s.latency, 2),
        isNum(s.reward) ? num(s.reward, 3) : "—",
        isNum(s.rewardCum) ? num(s.rewardCum, 3) : "—",
        isNum(s.value) ? num(s.value, 3) : "—",
        s.effect || "not declared", s.error ? "yes" : "no", String(s.sourcesCum),
        s.model || "not on the step", marks.join(", ") || "—",
      ].map(function (v, i) { return H("td", { class: [4, 6, 7, 8, 9, 10, 11, 14, 0].indexOf(i) >= 0 ? "num" : null, text: String(v) }); })));
    });
    tbl.appendChild(body);
    return H("details", { class: "trc-details", "data-role": "table" }, [
      H("summary", { text: "table view: " + plural(rows.length, "step") + ", every column"
        + (cutoff === null ? "" : " up to the playhead") + (m.tiled ? " (tiled ×" + m.tiled + ")" : "") }),
      H("div", { class: "scroll-x" }, tbl),
    ]);
  }

  function numbersLine(H, m, cutoff) {
    var upto = cutoff === null ? m.steps : m.steps.filter(function (s) { return s.index <= cutoff; });
    var last = upto.length ? upto[upto.length - 1] : null;
    //: a step that declares no `tokens_basis` is not an estimated step — the
    //: trace simply did not say.  Reporting "0% measured" for a run that never
    //: recorded a basis would assert the stronger, false thing, so the two
    //: cases are worded apart and a mixed run states both counts.
    var declared = upto.filter(function (s) { return s.basis; }).length;
    var measured = upto.filter(function (s) { return s.basis === "measured"; }).length;
    var basisNote = "";
    if (upto.length && declared === 0) basisNote = " (basis not recorded)";
    else if (declared) basisNote = " (" + pct(measured / declared) + " measured"
      + (declared < upto.length ? " of the " + declared + " that say" : "") + ")";
    var parts = [
      ["steps", upto.length + (cutoff === null ? "" : " of " + m.steps.length)],
      ["tokens", last ? int(last.cum) + basisNote : "—"],
      ["seconds", last ? secs(last.t1) : "—"],
      ["sources read", last ? String(last.sourcesCum) : "0"],
      ["evidence", last ? String(last.evidenceCum) : "0"],
      ["reward", last && isNum(last.rewardCum) ? num(last.rewardCum, 2) : "—"],
      ["errors", String(upto.filter(function (s) { return s.error; }).length)],
    ];
    var kids = [];
    parts.forEach(function (p, i) {
      if (i) kids.push(H("span", { class: "sep", text: "·" }));
      kids.push(H("span", { text: p[0] + " " }), H("b", { text: p[1] }));
    });
    if (m.termination) { kids.push(H("span", { class: "sep", text: "·" }), H("span", { text: "ended " + m.termination + (m.stoppedBy ? " (" + m.stoppedBy + ")" : "") })); }
    if (m.synthetic) { kids.push(H("span", { class: "sep", text: "·" }), H("span", { text: "SYNTHETIC" })); }
    if (m.tiled) { kids.push(H("span", { class: "sep", text: "·" }), H("b", { text: "TILED ×" + m.tiled + " — a scale measurement, not a run" })); }
    return H("p", { class: "trc-nums" }, kids);
  }

  AgentDiff.block({
    id: "tr-timeline",
    title: "The execution",
    question: "What did this run do, in the order and at the pace it did it?",
    group: "trace",
    size: "wide",
    relevance: function (ctx) {
      var r = ctx.report, recs = bundleRecords();
      if (recs && Object.keys(recs).length) return 0.95;
      return r && r.a && Array.isArray(r.a.steps) && r.a.steps.length ? 0.95 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, st = state();
      var m = model(ctx);
      LAST_MODEL = m;
      if (!m || !m.steps.length) return ctx.empty(el, "This page carries no run with steps to trace.");
      var root = H("div", { class: "trc" });
      el.appendChild(root);

      var cutoff = isNum(st.step) ? st.step : null;
      root.appendChild(H("p", { class: "trc-lede", text:
        "One run as it executed: " + m.name + (m.task ? " on " + L.fmt.short(m.task) : "") + ", " +
        plural(m.steps.length, "step") + " from " + m.source + "." }));
      root.appendChild(numbersLine(H, m, cutoff));

      // controls: side, x measure, replay
      var bar = H("div", { class: "trc-bar" });
      var traceable = traceableRecords();
      if (traceable.length) {
        //: a bundle page: every recorded run is reachable, not only the pair
        //: the report opened on.  The first option returns to the pair when
        //: this page also carries one.
        var opts = [];
        if (ctx.report && ctx.report.a) opts.push(H("option", { value: "", text: "the pair in this report" }));
        traceable.forEach(function (rec) {
          opts.push(H("option", { value: rec.key, text: rec.label }));
        });
        var pick = H("select", { class: "trc-pick", "data-role": "run-picker",
          "aria-label": "which recorded run to trace, of the " + traceable.length + " the bundle carries",
          onchange: function (e) {
            stopTimer();
            select({ run: e.target.value || null, step: null, phase: null, level: "run", playing: false });
          } }, opts);
        pick.value = st.run || "";
        bar.appendChild(pick);
      }
      if (m.kind === "side" && ctx.report && ctx.report.b) {
        ["a", "b"].forEach(function (side) {
          bar.appendChild(H("button", {
            class: "trc-btn", "data-side": side, "aria-pressed": st.side === side ? "true" : "false",
            text: sideName(ctx.report, side),
            onclick: function () { stopTimer(); select({ side: side, step: null, playing: false, level: "run" }); },
          }));
        });
      }
      [["time", "clock"], ["steps", "steps"]].forEach(function (opt) {
        bar.appendChild(H("button", {
          class: "trc-btn", "data-x": opt[0], "aria-pressed": st.x === opt[0] ? "true" : "false", text: opt[1],
          onclick: function () { select({ x: opt[0] }); },
        }));
      });
      bar.appendChild(H("button", {
        class: "trc-btn", "data-role": "play", "aria-pressed": st.playing ? "true" : "false",
        text: st.playing ? "⏸ pause" : "▶ replay",
        onclick: function () { if (state().playing) { stopTimer(); select({ playing: false }); } else { startReplay(state().speed); } },
      }));
      SPEEDS.concat(["step"]).forEach(function (sp) {
        var on = st.speed === sp || (sp === "step" && st.speed === "step");
        bar.appendChild(H("button", {
          class: "trc-btn" + (on ? " on" : ""), "data-speed": String(sp),
          "aria-pressed": on ? "true" : "false",
          text: sp === "step" ? "by step" : sp + "×",
          title: sp === "step" ? "one step per tick, ignoring the recorded latencies"
                               : sp + " recorded seconds per second of watching",
          onclick: function () { startReplay(sp); },
        }));
      });
      bar.appendChild(H("button", { class: "trc-btn", "data-role": "rewind", text: "⏮",
        onclick: function () { stopTimer(); select({ playing: false, step: null, level: "run" }); } }));
      var scrub = H("input", {
        class: "trc-scrub", type: "range", min: "0", max: String(Math.max(0, m.steps.length - 1)),
        value: String(cutoff === null ? 0 : Math.max(0, m.steps.findIndex(function (s) { return s.index === cutoff; }))),
        "aria-label": "the playhead: which step the run has reached",
        oninput: function (e) { stopTimer(); var i = Math.max(0, Math.min(m.steps.length - 1, +e.target.value)); select({ playing: false, step: m.steps[i].index }); },
      });
      bar.appendChild(scrub);
      root.appendChild(bar);

      var level = st.level === "phase" && st.phase ? "phase" : "run";
      if (level === "phase") {
        var ph = m.phases.filter(function (p) { return p.id === st.phase; })[0];
        root.appendChild(H("p", { class: "trc-crumb" }, [
          H("button", { text: "the run", onclick: function () { select({ level: "run", phase: null }); } }),
          H("span", { text: " › " + (ph ? ph.label : st.phase) }),
        ]));
      }

      var stage = H("div", { class: "trc-stage", tabindex: "0", role: "application",
        "aria-label": "the execution of " + m.name + " at the " + level + " level; arrow keys move the playhead, Enter opens the step, Escape returns to the run" });
      root.appendChild(stage);
      /* Every arrow key changes the family, which re-renders the block and
       * replaces this element — so without restoring the focus the second
       * key press goes nowhere and the view is reachable by mouse only.
       * `FOCUS_STAGE` is set when a key was handled here and consumed on
       * the next render. */
      if (FOCUS_STAGE) restoreFocus();
      stage.addEventListener("pointerdown", function () { FOCUS_STAGE = false; });
      L.layout.responsive(stage, function () {
        stage.innerHTML = "";
        var shown = m;
        if (level === "phase") {
          var p = m.phases.filter(function (v) { return v.id === st.phase; })[0];
          if (p) shown = Object.assign({}, m, { steps: m.steps.filter(function (s) { return s.index >= p.from && s.index <= p.to; }), phases: [] });
        }
        //: measured inside the callback: `L.layout.responsive` defers the
        //: draw, so a timer stopped outside it would report the setup and
        //: not the drawing, which is the number this hook exists for
        var t1 = (global.performance && performance.now) ? performance.now() : null;
        drawStrip(stage, shown, {});
        TIMING.run = t1 === null ? null : performance.now() - t1;
        TIMING.steps = shown.steps.length;
        TIMING.tiled = m.tiled || null;
      }, "trace-strip");

      var status = H("p", { class: "trc-status", role: "status", "aria-live": "polite" });
      var upto = cutoff === null ? m.steps : m.steps.filter(function (s) { return s.index <= cutoff; });
      var lastModel = upto.filter(function (s) { return s.model; }).pop();
      status.textContent = (cutoff === null
        ? "The whole run is shown. Replay walks it at the pace the trace recorded."
        : "At step " + cutoff + " of " + m.steps.length + ": " + int(upto[upto.length - 1].cum) + " tokens, " +
          secs(upto[upto.length - 1].t1) + ", " + upto[upto.length - 1].sourcesCum + " sources read, " +
          upto[upto.length - 1].evidenceCum + " with a recorded evidence signal" +
          (lastModel ? ", last model " + lastModel.model : "") + ".") +
        " Replay is a view of the recorded timings; no step is re-executed and no model is called.";
      root.appendChild(status);

      var folds = foldsOf(m.steps);
      var bins = binned(m.steps, STEP_CAP);
      root.appendChild(H("p", { class: "trc-note", text:
        (st.x === "time"
          ? (folds.length ? plural(folds.length, "quiet stretch") + " folded (a gap over " + QUIET_S + "s, drawn at 6 + 6·log₂(1 + seconds))."
             : m.clock === "recorded"
               ? "No idle stretch to fold: no gap between one step ending and the next beginning."
               : "No idle stretch to fold: every step of this run begins where the last one ended — which is the "
                 + "assumption, not a reading.")
          : "Steps are evenly spaced; switch to the clock to see where the time went.") +
        (bins.per > 1 ? " Past " + STEP_CAP + " steps the strip bins " + bins.per + " to a rectangle." : "") +
        (m.clock === "recorded"
          ? " Each step is placed where the trace says it began, so the clock is read rather than assumed."
          : " No step records when it began, so each is placed by summing the durations before it — which reads "
            + "the run as strictly sequential. Nothing in the trace says it was.") +
        " Every number is the engine's: tokens and their basis, latency, reward, and the marks the diagnosis and impact sections placed." }));

      var leg = H("div", { class: "trc-legend" });
      KINDS.forEach(function (k) {
        if (!m.steps.some(function (s) { return s.kind === k; })) return;
        leg.appendChild(H("span", {}, [H("i", { style: "background:" + kindColour(k) }), H("span", { text: k })]));
      });
      var legend = ["◆ decisive", "▲ fault", "┃ divergence", "● milestone", "tick = reward", "hatched = estimated tokens"];
      // only the interventions this run actually carries: a legend that
      // advertises a glyph the strip never draws is a legend that lies
      Object.keys(SCAFFOLD).forEach(function (k) {
        if (m.steps.some(function (s) { return s.scaffold === k; })) {
          legend.push(SCAFFOLD[k].glyph + " the harness (" + k.replace(/_/g, " ") + ")");
        }
      });
      legend.forEach(function (t) { leg.appendChild(H("span", { text: t })); });
      root.appendChild(leg);
      root.appendChild(stepTable(H, m, cutoff));

      var HANDLED = ["ArrowRight", "ArrowLeft", "Home", "End", "Enter", "Escape", " "];
      stage.addEventListener("keydown", function (e) {
        /* Set before anything else: `select` re-renders the page
         * synchronously, so a flag set after it would be read by the render
         * that has already happened.  Tab and every other key leave it
         * alone, so the stage never becomes a focus trap. */
        if (HANDLED.indexOf(e.key) >= 0) FOCUS_STAGE = true;
        var s = state(), i = isNum(s.step) ? m.steps.findIndex(function (v) { return v.index === s.step; }) : -1;
        if (e.key === "ArrowRight") { stopTimer(); select({ playing: false, step: m.steps[Math.min(m.steps.length - 1, i + 1)].index }); e.preventDefault(); }
        else if (e.key === "ArrowLeft") { stopTimer(); select({ playing: false, step: i <= 0 ? null : m.steps[i - 1].index }); e.preventDefault(); }
        else if (e.key === "Home") { stopTimer(); select({ playing: false, step: m.steps[0].index }); e.preventDefault(); }
        else if (e.key === "End") { stopTimer(); select({ playing: false, step: m.steps[m.steps.length - 1].index }); e.preventDefault(); }
        else if (e.key === "Enter" && i >= 0) { select({ level: "step" }); e.preventDefault(); }
        else if (e.key === "Escape") { stopTimer(); select({ playing: false, level: "run", phase: null }); e.preventDefault(); }
        else if (e.key === " ") { if (state().playing) { stopTimer(); select({ playing: false }); } else if (!reduced()) { startReplay(state().speed); } e.preventDefault(); }
        if (e.key === "Tab") FOCUS_STAGE = false;
      });

      listen(el, function () { if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); });
    },
  });

  // --------------------------------------------------------------- step

  AgentDiff.block({
    id: "tr-step",
    title: "The step",
    question: "What did this one step do, cost, and rest on?",
    group: "trace",
    size: "normal",
    relevance: function (ctx) {
      var r = ctx.report, recs = bundleRecords();
      return (recs && Object.keys(recs).length) || (r && r.a && Array.isArray(r.a.steps) && r.a.steps.length) ? 0.8 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, st = state(), m = model(ctx);
      if (!m || !m.steps.length) return ctx.empty(el, "No run to read a step from.");
      var s = isNum(st.step) ? m.steps.filter(function (v) { return v.index === st.step; })[0] : null;
      var root = H("div", { class: "trc" });
      el.appendChild(root);
      if (!s) {
        root.appendChild(H("p", { class: "trc-lede", text: "No step is selected. Click a step in the execution above, or press ▶ to replay the run and this follows the playhead." }));
        return;
      }
      root.appendChild(H("p", { class: "trc-crumb" }, [
        H("button", { text: m.name, onclick: function () { select({ level: "run", step: null }); } }),
        H("span", { text: " › step " + s.index + " · " + s.kind + " · " + s.name }),
      ]));
      var rows = [
        ["kind", s.kind + (s.span ? " · acting agent " + s.span : "")],
        ["tokens", isNum(s.tokens) ? int(s.tokens) + " (" + (s.basis || "basis not recorded") + "), " + int(s.cum) + " so far" : "not recorded"],
        ["in / out", isNum(s.inTokens) || isNum(s.outTokens)
          ? int(s.inTokens || 0) + " in · " + int(s.outTokens || 0) + " out"
          : "the provider did not split this step's count"],
        ["from cache", isNum(s.cachedTokens)
          ? int(s.cachedTokens) + " of the input, served from the provider's cache and not paid for"
          : "not reported — which is not the same as none"],
        ["latency", secs(s.latency) + " · " + secs(s.t0) + " into the run"
          + (s.clock === "recorded" ? " (as the trace records it)" : " (summed from the steps before it)")],
        ["model", s.model ? s.model + " (as the step records it)" : "not recorded on the step"],
        ["reward", isNum(s.reward) ? num(s.reward, 2) + " · running " + num(s.rewardCum, 2) + (s.why ? " — " + s.why : "") : "none recorded"],
        ["value", isNum(s.value) ? num(s.value, 2) + (isNum(s.advantage) ? " · advantage " + num(s.advantage, 2) : "") : "no critic estimate"],
        ["effect", s.effect || "not declared"],
        ["error", s.error ? "yes" : "no"],
      ];
      if (s.fetch) {
        rows.push(["fetch", (s.fetch.kind || "") + " " + (s.fetch.name || "") + " · " + int(s.fetch.output_chars) + " chars back" +
          (s.fetch.used === true ? " · use recorded" : s.fetch.used === false ? " · recorded as not used" : " · use not recorded") +
          (isNum(s.fetch.repeat_of) ? " · repeats step " + s.fetch.repeat_of : "")]);
      }
      var flags = [];
      if (s.decisive) flags.push("the decisive step of the diagnosis");
      if (s.fault) flags.push(s.fault);
      if (s.divergence) flags.push("a divergence row: " + trunc(s.divergence.summary || "", 160));
      if (s.milestone) flags.push("milestone " + s.milestone);
      if (s.scaffold) flags.push("the harness, not the agent: " + SCAFFOLD[s.scaffold].label);
      if (s.evidence) flags.push("carries a recorded evidence signal");
      if (flags.length) rows.push(["marked", flags.join("; ")]);
      var table = H("table", { class: "trc-kv" });
      rows.forEach(function (r) {
        table.appendChild(H("tr", {}, [H("th", { text: r[0] }), H("td", { text: r[1] })]));
      });
      root.appendChild(table);
      if (s.input) {
        root.appendChild(H("div", { class: "trc-part" }, [H("h4", { text: "input" }),
          H("pre", { class: "trc-text", text: trunc(s.input, 4000, true) })]));
      }
      if (s.output) {
        root.appendChild(H("div", { class: "trc-part" }, [H("h4", { text: "output" }),
          H("pre", { class: "trc-text", text: trunc(s.output, 4000, true) })]));
      }
      var nav = H("div", { class: "trc-bar" });
      var i = m.steps.indexOf(s);
      nav.appendChild(H("button", { class: "trc-btn", text: "‹ previous", onclick: function () { if (i > 0) select({ step: m.steps[i - 1].index }); } }));
      nav.appendChild(H("button", { class: "trc-btn", text: "next ›", onclick: function () { if (i < m.steps.length - 1) select({ step: m.steps[i + 1].index }); } }));
      root.appendChild(nav);
      root.appendChild(H("p", { class: "trc-note", text: "Every field is as the trace recorded it; a field the trace omitted says so rather than reading as zero." }));
      listen(el, function () { if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); });
    },
  });

  // ------------------------------------------------------------ compare

  AgentDiff.block({
    id: "tr-compare",
    title: "The other run, aligned",
    question: "Where did the two runs do the same thing, and where did they part?",
    group: "trace",
    size: "wide",
    relevance: function (ctx) {
      var r = ctx.report;
      return r && r.a && r.b && Array.isArray(r.a.steps) && Array.isArray(r.b.steps) && r.a.steps.length && r.b.steps.length ? 0.7 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h, report = ctx.report, st = state();
      var root = H("div", { class: "trc" });
      el.appendChild(root);
      //: when the execution above is showing a bundle record, the pair in
      //: this report is a different run entirely.  Drawing it here would
      //: put two unrelated runs under a heading that says "the other run",
      //: so the block says what it has instead of showing the wrong thing.
      if (st.run) {
        var chosen = (bundleRecords() || {})[st.run];
        root.appendChild(H("p", { class: "trc-lede", text:
          "The execution above is showing " + ((chosen && chosen.agent) || st.run) +
          ", a single run from the bundle. A bundle record has no matched twin \u2014 the alignment and the " +
          "divergence rows are computed for a pair, and this page carries none for this run. " +
          "Choose \u201cthe pair in this report\u201d in the execution above to compare two runs." }));
        return;
      }
      var sides = ["a", "b"].map(function (side) {
        var marks = marksOf(report, side);
        var steps = buildSteps(report[side].steps, rewardsOf(report, side), fetchesOf({ fetches: report.fetches && report.fetches[side] }), marks);
        return { side: side, name: sideName(report, side), steps: steps, phases: [], marks: marks, name_: side, totalS: steps.length ? steps[steps.length - 1].t1 : 0, synthetic: false };
      });
      var divergences = report.divergences || [];
      root.appendChild(H("p", { class: "trc-lede", text:
        sides[0].name + " ran " + plural(sides[0].steps.length, "step") + " and " + sides[1].name + " " + sides[1].steps.length +
        "; the engine matched them step by step and found " + plural(divergences.length, "divergence", "divergences") + "." }));
      sides.forEach(function (m) {
        var wrap = H("div", { class: "trc-part", "data-side": m.side }, [H("h4", { text: m.name })]);
        root.appendChild(wrap);
        var host = H("div", { class: "trc-stage" });
        wrap.appendChild(host);
        L.layout.responsive(host, function () { host.innerHTML = ""; drawStrip(host, m, { tracks: false }); }, "trace-cmp-" + m.side);
      });
      if (divergences.length) {
        var DIV_CAP = 6;
        var list = H("div", { class: "trc-part", "data-role": "divergences" }, [H("h4", { text: "where they parted" })]);
        divergences.slice(0, DIV_CAP).forEach(function (d) {
          list.appendChild(H("p", { class: "trc-note", "data-divergence": String(d.rank), text:
            "#" + d.rank + " " + (d.kind || "") + " — A step " + d.a_index + ", B step " + d.b_index + ": " + trunc(d.summary || "", 220) }));
        });
        //: never a silent truncation: the count that is not shown is said
        if (divergences.length > DIV_CAP) {
          list.appendChild(H("p", { class: "trc-note", "data-role": "divergence-more", text:
            "The " + DIV_CAP + " highest-ranked of " + divergences.length + " divergence rows are listed; every one of them is drawn on the strips above, and the Evidence view has the full list." }));
        }
        root.appendChild(list);
      }
      root.appendChild(H("p", { class: "trc-note", text:
        "Both strips use the same drawing as the execution above, so a rectangle means the same thing in both. The alignment and the divergence rows are the engine's (`alignment`, `divergences`); nothing here re-computes them." }));
      listen(el, function () { if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); });
    },
  });

  // ------------------------------------------------------------- detail

  AgentDiff.block({
    id: "tr-detail",
    title: "This run's readings",
    question: "What do the other views already say about this run?",
    group: "trace",
    size: "wide",
    relevance: function (ctx) {
      if (typeof AgentDiff.renderInto !== "function") return 0;
      var r = ctx.report, recs = bundleRecords();
      return (recs && Object.keys(recs).length) || (r && r.a && Array.isArray(r.a.steps) && r.a.steps.length) ? 0.5 : 0;
    },
    render: function (el, ctx) {
      ensureStyle();
      var H = ctx.h;
      var root = H("div", { class: "trc" });
      el.appendChild(root);
      root.appendChild(H("p", { class: "trc-lede", text:
        "The readings this run already has, drawn here rather than redrawn: nothing below is computed by this view." }));
      /* Point the run block at the run the timeline is tracing, rather
       * than letting it fall back to the heaviest run in the output — two
       * different runs under one heading is the failure this avoids.  The
       * levels view owns the key format, so it does the resolving. */
      var st = state(), linked = null;
      if (AgentDiff.levels && typeof AgentDiff.levels.find === "function") {
        try {
          if (st.run) linked = AgentDiff.levels.find({ key: st.run });
          else if (ctx.report) {
            linked = AgentDiff.levels.find({
              agent: sideName(ctx.report, st.side),
              task: (ctx.report.task && ctx.report.task.id) || null,
            });
          }
        } catch (err) { linked = null; }
        //: set it only when it differs, so this render does not provoke the
        //: re-render that would bring us straight back here
        if (linked && AgentDiff.levels.state().run !== linked) {
          try { AgentDiff.levels.select({ run: linked }); } catch (err) { /* the block falls back */ }
        }
      }
      var parts = [["lv-run", "Where the tokens went, and what it fetched"],
                   ["dt-chain", "Data → model → agent → answer"],
                   ["dt-provenance", "What the answer rests on"]];
      var shown = 0;
      parts.forEach(function (p) {
        var wrap = H("div", { class: "trc-part" }, [H("h4", { text: p[1] })]);
        var host = H("div", {});
        wrap.appendChild(host);
        var ok = false;
        try { ok = AgentDiff.renderInto(p[0], host); } catch (err) { ok = false; }
        if (ok) { root.appendChild(wrap); shown += 1; }
        else {
          root.appendChild(H("p", { class: "trc-empty", text: p[1] + ": not on this page (" + p[0] + " has nothing to say about this output)." }));
        }
      });
      if (!shown) root.appendChild(H("p", { class: "trc-note", text: "None of the run's other readings are on this page." }));
      else root.appendChild(H("p", { class: "trc-note", text: linked
        ? "The run block above is showing the same run as the execution: " + linked + "."
        : "This page's run index carries no entry matching the run being traced, so the run block "
          + "shows its own default rather than this run — read its own breadcrumb for which." }));
      listen(el, function () { if (typeof AgentDiff._rerender === "function") AgentDiff._rerender(); });
    },
  });
})(typeof window !== "undefined" ? window : this);
