/* AgentDiff library — `AgentDiff.lib`, the helpers every block used to
 * carry its own copy of.
 *
 * Loads right after the core and before every block, and depends on no
 * block: it reads `AgentDiff` (the store, the rerender, the page state)
 * and, lazily, `AgentDiff.charts.responsive` when the story charts are
 * on the page. A block binds what it needs at the top of its IIFE
 * (`var L = AgentDiff.lib, isNum = L.fmt.isNum;`) and defines none of
 * these locally — the surface, and the rule, are in web/blocks/README.md.
 *
 *   fmt     num, signed, pct, secs, short, isNum, plural, trunc(text, n, keep?)
 *   color   side(side, ns), agent(side, i), verdict(kind), good, bad
 *   svg     svg(attrs, kids) — role="img" by default, aria-label required
 *           svg.note(text, cls), svg.tip(host, {class, width})
 *   glyph   interval(g, x, point, lo, hi, opts), foldWidth(n), foldSeconds(s)
 *   layout  responsive(host, draw, key), measure(host, lo, hi)
 *   family  family(key, defaults, opts) → {get, set, subscribe, reset, persist, state}
 *   style   once(id, cssText)
 *
 * Every formatter here is the most complete of the copies it replaced and
 * produces the same string those copies did, so a migrated block draws the
 * same page it drew before.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  // ------------------------------------------------------------------ fmt

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  //: a plain number at p places (2 by default), trailing zeros dropped, a real minus sign
  function num(v, p) {
    if (!isNum(v)) return "—";
    var s = v.toFixed(p === undefined ? 2 : p);
    if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return s.replace("-", "−");
  }
  //: a delta with its sign: +0.50, −1.25, 0.00 (no sign on zero)
  function signed(v, p) {
    if (!isNum(v)) return "—";
    var s = Math.abs(v).toFixed(p === undefined ? 2 : p);
    return v > 0 ? "+" + s : v < 0 ? "−" + s : s;
  }
  //: a fraction as a percentage: rounded to whole points, or to p places when asked
  function pct(v, p) {
    if (!isNum(v)) return "—";
    return (p === undefined ? Math.round(v * 100) : (100 * v).toFixed(p)) + "%";
  }
  //: seconds at the precision a reader can use: 123s, 12s, 1.5s, 0.25s
  function secs(v) { return !isNum(v) ? "—" : v >= 100 ? Math.round(v) + "s" : v >= 10 ? v.toFixed(0) + "s" : v >= 1 ? v.toFixed(1) + "s" : v.toFixed(2) + "s"; }
  //: a task id without its numbering prefix: "rl01_ledger_reconcile" → "ledger reconcile"
  function short(id) { return String(id || "").replace(/^(rl|t)\d+_/, "").replace(/_/g, " "); }

  //: an integer with thousands separators: 1234 → "1,234"; a non-number is "—"
  function int(v) { return isNum(v) ? String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "—"; }
  /* A count with its noun: "1 run", "2 runs", "1,234 characters", "3 fetches",
   * "2 recoveries"; the plural given as the third argument wins ("2 people"). */
  function plural(n, word, pl) {
    word = String(word === null || word === undefined ? "" : word);
    var one = isNum(n) && n === 1;
    var many = pl ? String(pl) : /(ch|sh|s|x|z)$/.test(word) ? word + "es" : /[^aeiou]y$/.test(word) ? word.slice(0, -1) + "ies" : word + "s";
    return int(n) + " " + (one ? word : many);
  }
  /* Text cut to n characters with an ellipsis, its whitespace collapsed
   * first so a label never carries a newline; null and undefined are "".
   * `keep` keeps the whitespace as recorded — for a step's log shown in a
   * <pre>, where the newlines are the text. */
  function trunc(text, n, keep) {
    var s = String(text === null || text === undefined ? "" : text);
    if (!keep) s = s.replace(/\s+/g, " ").trim();
    n = isNum(n) ? n : 0;
    return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "\u2026" : s;
  }

  var fmt = { num: num, signed: signed, pct: pct, secs: secs, short: short, isNum: isNum, plural: plural, trunc: trunc };

  // ---------------------------------------------------------------- color
  //
  // Colour is meaning: run A is `--a`, run B is `--b`, a verdict is
  // `--good` or `--bad`. A block that overrides the two run colours for the
  // dark theme does so through its own namespace (`.im{--im-a:…}`), and
  // asks for `side("a", "im")`.

  var GOOD = "var(--good)", BAD = "var(--bad)";
  var color = {
    side: function (side, ns) { return ns ? "var(--" + ns + "-" + side + ")" : "var(--" + side + ")"; },
    //: the i-th agent given its side: the pair's colours, a third agent warns, the rest are muted
    agent: function (side, i) { return side === "a" ? "var(--a)" : side === "b" ? "var(--b)" : i === 2 ? "var(--warn)" : "var(--ink-3)"; },
    //: a verdict as a colour: a sign (+/−/0), or a word (better/worse/tie); unknown is muted
    verdict: function (kind) {
      if (isNum(kind)) return kind > 0 ? GOOD : kind < 0 ? BAD : "var(--ink)";
      switch (String(kind || "")) {
        case "good": case "better": case "win": case "pass": case "up": case "b": return GOOD;
        case "bad": case "worse": case "loss": case "fail": case "down": case "a": return BAD;
        case "tie": case "same": case "even": return "var(--ink)";
        default: return "var(--ink-3)";
      }
    },
    good: GOOD,
    bad: BAD,
  };

  // ------------------------------------------------------------------ svg

  var SVG_NS = "http://www.w3.org/2000/svg";
  //: the core's ctx.svg, verbatim: null/false attrs skipped, `text` is content, `on*` listens
  function el(tag, attrs, kids) {
    var node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (var name in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, name)) continue;
        var value = attrs[name];
        if (value === null || value === undefined || value === false) continue;
        if (name === "text") { node.textContent = value; continue; }
        if (name.indexOf("on") === 0 && typeof value === "function") { node.addEventListener(name.slice(2), value); continue; }
        node.setAttribute(name, value);
      }
    }
    if (kids) (Array.isArray(kids) ? kids : [kids]).forEach(function (k) {
      if (k === null || k === undefined || k === false) return;
      node.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    });
    return node;
  }
  /* The root <svg> of a chart. Every chart on the page must say what it
   * shows, so a missing aria-label is refused here rather than caught by a
   * test later; role="img" unless the block names another role. */
  function svg(attrs, kids) {
    var label = attrs && attrs["aria-label"];
    if (typeof label !== "string" || !label.trim()) {
      throw new Error("AgentDiff.lib.svg: a chart <svg> needs an aria-label that says what the picture shows");
    }
    var node = el("svg", attrs, kids);
    if (!node.hasAttribute("role")) node.setAttribute("role", "img");
    return node;
  }
  //: a note under a chart — the caveat, the source, the qualifier on the number
  svg.note = function (text, cls) {
    var p = document.createElement("p");
    p.className = cls ? "note " + cls : "note";
    p.textContent = text === null || text === undefined ? "" : String(text);
    return p;
  };
  /* A hover tooltip in `host` (which must be position:relative). `show(evt,
   * lines)` takes [{text, b?, mono?}] — a bold line, a monospace line — or a
   * function that fills the box itself, and keeps the box inside the host;
   * `hide()` hides it. The block's own class styles it; `width` is the
   * box's max width, for the flip. */
  svg.tip = function (host, opts) {
    opts = opts || {};
    var cls = opts.class || "lib-tip", width = isNum(opts.width) ? opts.width : 320;
    if (cls === "lib-tip") style.once("lib-tip", ".lib-tip{position:absolute;z-index:5;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:7px;box-shadow:var(--shadow);padding:6px 9px;font-size:var(--fs-xs);color:var(--ink-2);max-width:320px}.lib-tip b{color:var(--ink)}.lib-tip .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}");
    var tip = document.createElement("div");
    tip.className = cls;
    tip.hidden = true;
    host.appendChild(tip);
    return {
      show: function (evt, lines) {
        tip.innerHTML = "";
        if (typeof lines === "function") lines(tip);
        else lines.forEach(function (l) {
          if (!l) return;
          var d = document.createElement("div");
          if (l.mono) d.className = "mono";
          if (l.b) { var b = document.createElement("b"); b.textContent = l.text; d.appendChild(b); } else d.textContent = l.text;
          tip.appendChild(d);
        });
        tip.hidden = false;
        var r = host.getBoundingClientRect();
        var x = evt.clientX - r.left + 14, y = evt.clientY - r.top + 12;
        if (x + width > r.width) x = Math.max(0, evt.clientX - r.left - (width + 10));
        tip.style.left = x + "px";
        tip.style.top = y + "px";
      },
      hide: function () { tip.hidden = true; },
      node: tip,
    };
  };

  // ---------------------------------------------------------------- glyph

  /* An interval is drawn as an interval: the point as a dot on a line from
   * lo to hi with a tick at each end, on the x scale, at opts.y. A missing
   * bound collapses to the point, so a degenerate interval is still a dot.
   * opts: {y, color, width (2), opacity (0.42), tick (4), r (3.6), dot (true),
   * lineClass, dotClass}. Returns {a, b} — the pixel ends — so the block can
   * place its label and hit target. */
  function interval(g, x, point, lo, hi, opts) {
    opts = opts || {};
    var y = isNum(opts.y) ? opts.y : 0, stroke = opts.color || "currentColor";
    var width = isNum(opts.width) ? opts.width : 2, opacity = isNum(opts.opacity) ? opts.opacity : 0.42;
    var tick = isNum(opts.tick) ? opts.tick : 4, r = isNum(opts.r) ? opts.r : 3.6;
    var a = isNum(lo) ? x(lo) : x(point), b = isNum(hi) ? x(hi) : x(point);
    g.appendChild(el("line", { class: opts.lineClass || null, x1: a, x2: b, y1: y, y2: y, stroke: stroke, "stroke-width": width, "stroke-opacity": opacity, "stroke-linecap": "round" }));
    if (tick > 0) [a, b].forEach(function (px) {
      g.appendChild(el("line", { x1: px, x2: px, y1: y - tick, y2: y + tick, stroke: stroke, "stroke-width": isNum(opts.tickWidth) ? opts.tickWidth : 1.5, "stroke-opacity": opacity }));
    });
    if (opts.dot !== false && isNum(point)) g.appendChild(el("circle", { class: opts.dotClass || null, cx: x(point), cy: y, r: r, fill: stroke }));
    return { a: a, b: b };
  }
  //: a fold's length scales with what it constricts: 4–5 steps ≈ 20px, 11 ≈ 27px, 100 ≈ 46px
  function foldWidth(n) { return 6 + 6 * Math.log(1 + Math.max(0, isNum(n) ? n : 0)) / Math.LN2; }
  //: folded time: the same law over the seconds a fold holds
  function foldSeconds(s) { return foldWidth(s); }

  var glyph = { interval: interval, foldWidth: foldWidth, foldSeconds: foldSeconds };

  // --------------------------------------------------------------- layout

  /* Draw now and again on resize, through the story charts' painter when
   * it is on the page (it redraws only when the width really changed and
   * repaints by key); otherwise draw once. */
  function responsive(host, draw, key) {
    var charts = AgentDiff.charts;
    if (charts && typeof charts.responsive === "function") return charts.responsive(host, draw, key);
    draw();
    return host;
  }
  //: the host's width, clamped to what a chart can use; a detached host reads as 320
  function measure(host, lo, hi) {
    var w = (host && host.clientWidth) || (host && host.parentNode && host.parentNode.clientWidth) || 0;
    return Math.max(isNum(lo) ? lo : 280, Math.min(isNum(hi) ? hi : 1400, w || 320));
  }

  var layout = { responsive: responsive, measure: measure };

  // --------------------------------------------------------------- family
  //
  // One store for the state a family of blocks shares: the reader's
  // selection, an open fold, an axis choice. `family(key, defaults, opts)`
  // returns the same object for the same key, so every block of the
  // family sees one state.
  //
  //   opts.scope    "task" (default): one state per task, made from
  //                 `defaults` the first time a task is seen, so a choice
  //                 never leaks between tasks; "page": one state for the
  //                 page, the reader's preference.
  //   opts.persist  true (default): every field of `defaults` survives a
  //                 reload, through the page's own Store under
  //                 "agentdiff:<key>" — a family persists unless told not
  //                 to. [fields] persists only those; false keeps the state
  //                 in memory (exploration state: an open fold, an open
  //                 cluster). A page family saves under "agentdiff:<key>";
  //                 a task family saves each task's state under
  //                 "agentdiff:<key>:<task>", so a choice on one task is
  //                 never read on another.
  //   opts.rerender whether `set` re-renders the page (default false; a
  //                 block that repaints in place keeps its scroll and
  //                 scrub). `set(patch, {rerender})` overrides per call.
  //
  //   get(task?)          the live state object (mutate it, then persist())
  //   set(patch, how?)    merge `patch` (or call patch(state)), persist,
  //                       notify subscribers, re-render when asked
  //   subscribe(fn, el?)  fn(state) after every set; pruned when `el`
  //                       leaves the document; returns the unsubscribe
  //   reset(task?)        back to defaults, in memory and in the store
  //   persist()           write the persisted fields now
  //   state               getter: get()

  var FAMILIES = {};
  function store() {
    try { return AgentDiff._internals && AgentDiff._internals.Store ? AgentDiff._internals.Store : null; }
    catch (err) { return null; }
  }
  function currentTask() {
    try {
      var st = typeof AgentDiff.state === "function" ? AgentDiff.state() : null;
      return st && st.task !== null && st.task !== undefined ? String(st.task) : "";
    } catch (err) { return ""; }
  }
  function clone(v) {
    if (v === null || typeof v !== "object") return v;
    if (Array.isArray(v)) return v.map(clone);
    var out = {};
    for (var k in v) if (Object.prototype.hasOwnProperty.call(v, k)) out[k] = clone(v[k]);
    return out;
  }
  function family(key, defaults, opts) {
    if (FAMILIES[key]) return FAMILIES[key];
    opts = opts || {};
    defaults = defaults || {};
    var scope = opts.scope === "page" ? "page" : "task";
    var persist = opts.persist === undefined ? true : opts.persist;
    var storeKey = "agentdiff:" + key;
    var subs = [];
    var byTask = {}, pageState = null;

    function fresh() { return clone(defaults); }
    function fields() { return persist === true ? Object.keys(defaults) : Array.isArray(persist) ? persist : []; }
    //: a saved field is taken when it has the default's type; a null default takes a scalar, an array or a plain object (a brush range saved as [lo, hi] restores)
    function accept(f, saved) {
      var d = defaults[f], v = saved[f];
      if (v === undefined) return false;
      if (d === null) return v === null || typeof v === "string" || typeof v === "number" || typeof v === "boolean" || Array.isArray(v) || (typeof v === "object" && Object.prototype.toString.call(v) === "[object Object]");
      if (Array.isArray(d)) return Array.isArray(v);
      if (typeof d === "object") return v !== null && typeof v === "object" && !Array.isArray(v);
      return typeof v === typeof d;
    }
    function taskKey(task) { return task === undefined || task === null ? currentTask() : String(task); }
    //: where a state is saved: the family's key, task-qualified for a task family
    function slot(t) { return scope === "page" ? storeKey : storeKey + ":" + t; }
    function load(t) {
      var st = fresh();
      if (!persist) return st;
      var s = store(), src = s ? s.get(slot(t)) : null;
      if (src && typeof src === "object") fields().forEach(function (f) { if (accept(f, src)) st[f] = clone(src[f]); });
      return st;
    }
    function get(task) {
      if (scope === "page") return pageState || (pageState = load(""));
      var t = taskKey(task);
      return byTask[t] || (byTask[t] = load(t));
    }
    function save(task) {
      if (!persist) return;
      var s = store();
      if (!s) return;
      var st = get(task), out = {};
      fields().forEach(function (f) { if (st[f] !== undefined) out[f] = st[f]; });
      try { s.set(slot(taskKey(task)), out); } catch (err) { /* quota; the session still holds it */ }
    }
    function notify(st) {
      subs = subs.filter(function (s) { return !s.el || s.el.isConnected; });
      subs.forEach(function (s) {
        try { s.fn(st); } catch (err) { if (global.console) console.warn("AgentDiff.lib.family " + key + ": subscriber failed", err); }
      });
    }
    function set(patch, how) {
      how = how || {};
      var st = get(how.task);
      if (typeof patch === "function") patch(st);
      else if (patch && typeof patch === "object") for (var k in patch) if (Object.prototype.hasOwnProperty.call(patch, k)) st[k] = patch[k];
      save(how.task);
      notify(st);
      var rerender = how.rerender !== undefined ? how.rerender : !!opts.rerender;
      if (rerender && typeof AgentDiff._rerender === "function") AgentDiff._rerender();
      return st;
    }
    function reset(task) {
      var t = taskKey(task);
      if (scope === "page") pageState = fresh(); else byTask[t] = fresh();
      var s = store();
      if (persist && s && typeof s.remove === "function") { try { s.remove(slot(t)); } catch (err) { /* nothing saved */ } }
      notify(get(task));
    }
    function subscribe(fn, el) {
      var entry = { fn: fn, el: el || null };
      subs.push(entry);
      return function () { subs = subs.filter(function (s) { return s !== entry; }); };
    }
    var F = { key: key, scope: scope, get: get, set: set, subscribe: subscribe, reset: reset, persist: save };
    Object.defineProperty(F, "state", { get: function () { return get(); }, enumerable: true });
    FAMILIES[key] = F;
    return F;
  }

  // ---------------------------------------------------------------- style

  var STYLED = {};
  //: a block's stylesheet, injected the first time its render asks and never again
  var style = {
    once: function (id, cssText) {
      if (STYLED[id]) return;
      STYLED[id] = true;
      try {
        var node = document.createElement("style");
        node.setAttribute("data-agentdiff-style", id);
        node.textContent = cssText;
        (document.head || document.documentElement).appendChild(node);
      } catch (err) { /* styling is a nicety; the block still reads without it */ }
    },
    has: function (id) { return !!STYLED[id]; },
  };

  AgentDiff.lib = { fmt: fmt, color: color, svg: svg, glyph: glyph, layout: layout, family: family, style: style };
})(typeof window !== "undefined" ? window : this);
