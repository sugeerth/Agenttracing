// The lens: a long run, phase by phase, on one trail (agentdiff hub).
//
// The page draws the run without this script; this adds what a script can:
//  - the trail as a tape: the phase in focus wide, its neighbours narrower,
//    the rest scrolled out; every call a branch off the trunk, at an even pace
//  - a fisheye round the pointer, with a halo, that magnifies the calls under
//    it and lists what they cost
//  - moving phase by phase, animated: buttons, ← →, the wheel, a drag;
//    [ and ] jump to the next failure, checkpoint or loop
//  - filters that bring the events forward: failures, retries, checkpoints, edits
// One ink: a call rises from the trunk, a failed one hangs below it, a passing
// check ends in a dot, a loop is an arc. Data comes from the hub itself.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var GLYPH = {explore: "◇", edit: "✎", verify: "✓", run: "▸", plan: "≡", research: "⌕", delegate: "⇄",
    other: "·", think: "…"};
  var W = 980, LEFT = 16, RIGHT = 16, T = 124, UP = 56, DOWN = 34, R = 140, D = 4.5, H = 188;

  function el(name, attrs, parent) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function h(tag, cls, text, parent) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function dur(s) {
    s = +s || 0;
    if (s >= 86400) return Math.floor(s / 86400) + "d " + Math.floor(s % 86400 / 3600) + "h";
    if (s >= 3600) return Math.floor(s / 3600) + "h " + String(Math.floor(s % 3600 / 60)).padStart(2, "0") + "m";
    if (s >= 60) return Math.floor(s / 60) + "m " + String(Math.floor(s % 60)).padStart(2, "0") + "s";
    return s.toFixed(s < 10 ? 1 : 0) + "s";
  }
  function when(t, start) {
    if (start == null) return "+" + dur(t);
    var d = new Date((start + t) * 1000), d0 = new Date(start * 1000);
    var day = Math.floor((Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) -
      Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), d0.getUTCDate())) / 86400000) + 1;
    return "day " + day + " " + String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0");
  }
  function ease(x) { return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function store(key, val) {
    try { if (val === undefined) return sessionStorage.getItem(key); sessionStorage.setItem(key, val); } catch (e) { return null; }
  }
  function failed(x) { return x.e || x.c === 0; }
  function fit(text, px) {
    var n = Math.floor(px / 6);
    return text.length <= n ? text : n > 3 ? text.slice(0, n - 1) + "…" : "";
  }

  function Lens(root, data) {
    this.root = root; this.data = data;
    var r = data.reading, c = data.calls;
    this.start = r.started_at;
    this.phases = data.phases.map(function (p, i) { p.i = i; return p; });
    var n = c.i.length, calls = [], byIndex = {};
    for (var k = 0; k < n; k++) {
      var x = {i: c.i[k], s: c.s[k], d: c.d[k], a: data.activities[c.a[k]], name: data.names[c.n[k]],
        e: !!c.e[k], c: c.c[k], k: c.k[k], t: c.t[k]};
      calls.push(x); byIndex[x.i] = x;
    }
    // retries: the same failing call again, right after itself
    for (k = 1; k < n; k++) {
      var a = calls[k - 1], b = calls[k];
      if (failed(a) && failed(b) && a.name === b.name && a.t === b.t) { a.retry = b.retry = true; }
    }
    this.phases.forEach(function (p) {
      p.calls = [];
      if (p.kind === "idle") return;
      for (var i = p.first; i <= p.last; i++) if (byIndex[i]) p.calls.push(byIndex[i]);
      p.fails = p.calls.filter(failed).length;
      p.events = (r.events || []).filter(function (ev) { return ev.index >= p.first && ev.index <= p.last; });
    });
    this.bursts = r.bursts;
    var maxc = Math.max.apply(null, this.phases.map(function (p) { return p.calls.length || 1; }));
    this.phases.forEach(function (p) {
      p.base = p.kind === "idle" ? 0.22 : 0.55 + 0.45 * Math.sqrt((p.calls.length || 1) / maxc);
    });
    this.filter = store("lens-filter") || "all";
    this.pace = store("lens-pace") || "even";
    var saved = parseFloat(store("lens-focus:" + data.id));
    var here = r.look_here, start = 0;
    if (root.dataset.burst) start = this.phaseOfBurst(+root.dataset.burst);
    else if (!isNaN(saved)) start = saved;
    else if (here) start = this.phaseOfIndex(here.index);
    this.f = Math.max(0, Math.min(this.phases.length - 1, start));
    this.build();
    this.layout();
  }

  Lens.prototype.phaseOfIndex = function (index) {
    for (var k = 0; k < this.phases.length; k++) {
      var p = this.phases[k];
      if (p.kind !== "idle" && index >= p.first && index <= p.last) return k;
    }
    return 0;
  };
  Lens.prototype.phaseOfBurst = function (n) {
    for (var k = 0; k < this.phases.length; k++) {
      var p = this.phases[k];
      if (p.kind !== "idle" && n >= p.bursts[0] && n <= p.bursts[1]) return k;
    }
    return 0;
  };
  Lens.prototype.burstOf = function (index) {
    var bs = this.bursts, lo = 0, hi = bs.length - 1;
    while (lo <= hi) {
      var m = (lo + hi) >> 1;
      if (index < bs[m].first) hi = m - 1; else if (index > bs[m].last) lo = m + 1; else return bs[m].n;
    }
    return null;
  };

  Lens.prototype.build = function () {
    var self = this, root = this.root;
    root.innerHTML = "";
    root.classList.add("on");
    var bar = h("div", "lens-bar", null, root);
    this.prevB = h("button", "lens-btn", "← prev", bar);
    this.title = h("div", "lens-title", "", bar);
    this.title.setAttribute("aria-live", "polite");
    this.nextB = h("button", "lens-btn", "next →", bar);
    var chips = h("div", "filters lens-chips", null, root);
    h("span", "muted", "bring forward", chips);
    this.chipEls = {};
    [["all", "every call"], ["fail", "failures"], ["retry", "retries"], ["check", "checkpoints"], ["edit", "edits"]]
      .forEach(function (kv) {
        var a = h("a", kv[0] === self.filter ? "on" : "", kv[1], chips);
        a.href = "#"; a.dataset.f = kv[0];
        a.addEventListener("click", function (ev) { ev.preventDefault(); self.setFilter(kv[0]); });
        self.chipEls[kv[0]] = a;
      });
    h("span", "muted", " · pace", chips);
    [["even", "even"], ["clock", "clock"]].forEach(function (kv) {
      var a = h("a", kv[0] === self.pace ? "on" : "", kv[1], chips);
      a.href = "#"; a.dataset.p = kv[0];
      a.addEventListener("click", function (ev) {
        ev.preventDefault(); self.pace = kv[0]; store("lens-pace", kv[0]);
        Array.prototype.forEach.call(chips.querySelectorAll("[data-p]"), function (b) { b.classList.toggle("on", b === a); });
        self.layout();
      });
    });
    h("span", "muted lens-hint", "← → or wheel: phases · [ ] next failure, checkpoint or loop", chips);
    var svg = this.svg = el("svg", {"class": "viz trail lens-tape", viewBox: "0 0 " + W + " " + H, width: W,
      role: "img", tabindex: "0", "aria-label": "the run, phase by phase; arrow keys move between phases"}, root);
    this.gMini = el("g", {}, svg);
    this.gBack = el("g", {}, svg);
    this.gMarks = el("g", {}, svg);
    this.gFront = el("g", {}, svg);
    this.gHalo = el("g", {style: "display:none"}, svg);
    el("circle", {"class": "halo", r: R * 0.42}, this.gHalo);
    el("line", {"class": "tick", y1: T - UP - 10, y2: T + DOWN + 6, style: "stroke-opacity:.6"}, this.gHalo);
    this.foot = h("div", "trail-key", "", root);
    this.card = h("div", "lens-card", null, root);
    this.idleCard();
    // a branch for every step, made once; the layout moves them
    this.marks = [];
    this.phases.forEach(function (p) {
      p.calls.forEach(function (x, j) {
        var think = x.a === "think" || x.a === "plan";
        var m = think ? el("circle", {"class": "mile faint", r: 1.1, cy: T}, self.gMarks)
          : el("line", {"class": "stem", y1: T}, self.gMarks);
        if (!think && (x.c === 1 || x.c === 0)) m._tip = el("circle", {"class": x.c === 1 ? "mile" : "leaf", r: 1.9}, self.gMarks);
        m._x = x; m._p = p; m._j = j; m._think = think;
        m._len = think ? 0 : failed(x) ? Math.min(DOWN, 6 + 6 * Math.log2(1 + x.d)) : Math.min(UP, 6 + 7 * Math.log2(1 + x.d));
        self.marks.push(m);
      });
    });
    this.prevB.addEventListener("click", function () { self.go(Math.round(self.f) - 1); });
    this.nextB.addEventListener("click", function () { self.go(Math.round(self.f) + 1); });
    svg.addEventListener("pointermove", function (ev) { self.hover(ev); });
    svg.addEventListener("pointerleave", function () { self.lensAt(null); });
    svg.addEventListener("wheel", function (ev) {
      var d = Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY;
      if (!d) return;
      ev.preventDefault();
      self.scrollBy(d / 360);
    }, {passive: false});
    var drag = null;
    svg.addEventListener("pointerdown", function (ev) { drag = {x: ev.clientX, f: self.f, moved: false}; });
    window.addEventListener("pointerup", function () { if (drag && drag.moved) self.settle(); setTimeout(function () { drag = null; }, 0); });
    svg.addEventListener("pointermove", function (ev) {
      if (!drag || !(ev.buttons & 1)) return;
      var dx = (ev.clientX - drag.x) * W / svg.getBoundingClientRect().width;
      if (Math.abs(dx) > 4) drag.moved = true;
      if (drag.moved) { self.f = Math.max(0, Math.min(self.phases.length - 1, drag.f - dx / 260)); self.layout(); }
    });
    svg.addEventListener("click", function (ev) {
      if (drag && drag.moved) return;
      var t = ev.target;
      if (t._phase != null) { self.go(t._phase); return; }
      var near = self.nearest();
      if (near) {
        var b = self.burstOf(near.i);
        if (b != null && root.dataset.base) location.href = root.dataset.base + "&burst=" + b + "#s" + near.i;
      }
    });
    root.addEventListener("keydown", function (ev) { self.key(ev); });
    // the treemap: a box takes the tape to its phase
    Array.prototype.forEach.call(document.querySelectorAll("svg.treemap g[data-phase]"), function (g) {
      g.addEventListener("click", function (ev) {
        ev.preventDefault();
        self.go(+g.dataset.phase);
        root.scrollIntoView({behavior: "smooth", block: "center"});
      });
    });
  };

  Lens.prototype.key = function (ev) {
    if (ev.target && /INPUT|SELECT|TEXTAREA/.test(ev.target.tagName)) return;
    var k = Math.round(this.f);
    if (ev.key === "ArrowRight") { this.go(k + 1); ev.preventDefault(); }
    else if (ev.key === "ArrowLeft") { this.go(k - 1); ev.preventDefault(); }
    else if (ev.key === "Home") { this.go(0); ev.preventDefault(); }
    else if (ev.key === "End") { this.go(this.phases.length - 1); ev.preventDefault(); }
    else if (ev.key === "]" || ev.key === "[") {
      var step = ev.key === "]" ? 1 : -1;
      for (var j = k + step; j >= 0 && j < this.phases.length; j += step) {
        var p = this.phases[j];
        if (p.kind !== "idle" && (p.fails || p.events.length || p.kind === "loop")) { this.go(j); break; }
      }
      ev.preventDefault();
    }
  };

  Lens.prototype.setFilter = function (f) {
    this.filter = f; store("lens-filter", f);
    for (var k in this.chipEls) this.chipEls[k].classList.toggle("on", k === f);
    this.layout();
  };

  Lens.prototype.match = function (x) {
    switch (this.filter) {
      case "fail": return failed(x);
      case "retry": return !!x.retry;
      case "check": return x.c === 1 || x.c === 0;
      case "edit": return x.a === "edit";
      default: return true;
    }
  };

  // ------------------------------------------------------------ movement
  Lens.prototype.go = function (target) {
    target = Math.max(0, Math.min(this.phases.length - 1, target));
    // idle is a gap to pass, not a place to stop
    var dir = target >= Math.round(this.f) ? 1 : -1;
    while (this.phases[target] && this.phases[target].kind === "idle" && target + dir >= 0 &&
           target + dir < this.phases.length) target += dir;
    var self = this, from = this.f, t0 = performance.now(), ms = 420;
    if (this.anim) cancelAnimationFrame(this.anim);
    if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) ms = 1;
    function frame(now) {
      var u = Math.min(1, (now - t0) / ms);
      self.f = from + (target - from) * ease(u);
      self.layout();
      if (u < 1) self.anim = requestAnimationFrame(frame);
      else { self.anim = null; store("lens-focus:" + self.data.id, String(target)); }
    }
    this.anim = requestAnimationFrame(frame);
  };
  Lens.prototype.scrollBy = function (d) {
    this.f = Math.max(0, Math.min(this.phases.length - 1, this.f + d));
    this.layout();
    var self = this;
    clearTimeout(this.snap);
    this.snap = setTimeout(function () { self.settle(); }, 220);
  };
  Lens.prototype.settle = function () { this.go(Math.round(this.f)); };

  // ------------------------------------------------------------ layout
  Lens.prototype.widths = function () {
    var f = this.f, out = [], total = 0;
    this.phases.forEach(function (p, k) {
      var d = Math.abs(k - f);
      var fade = d >= 3.6 ? 0 : d <= 2.6 ? 1 : (3.6 - d);   // beyond three phases: scrolled out
      var w = p.base * (0.18 + 3.4 * Math.exp(-d * d / 0.55)) * fade;
      out.push(w); total += w;
    });
    var span = W - LEFT - RIGHT, x = LEFT;
    return out.map(function (w) { var a = x; x += span * w / (total || 1); return [a, x]; });
  };

  Lens.prototype.fish = function (x) {
    var m = this.mx;
    if (m == null) return x;
    var d = x - m, ad = Math.abs(d);
    if (ad >= R) return x;
    var t = ad / R, g = (D + 1) * t / (D * t + 1);
    return m + (d < 0 ? -1 : 1) * R * g;
  };

  Lens.prototype.layout = function () {
    var self = this, xs = this.widths(), f = Math.round(this.f), p = this.phases[f];
    this.xs = xs;
    var back = this.gBack, front = this.gFront;
    back.innerHTML = ""; front.innerHTML = "";
    var before = 0, after = 0, lastLab = -1e9;
    this.phases.forEach(function (ph, k) {
      var a = self.fish(xs[k][0]), b = self.fish(xs[k][1]), w = b - a;
      if (w < 0.6) { if (k < f) before++; else if (k > f) after++; return; }
      // the trunk: solid where it worked, dotted across idle
      el("line", {"class": ph.kind === "idle" ? "gap" : "trunk", x1: a, x2: b, y1: T, y2: T}, back);
      el("line", {"class": "sep", x1: a, x2: a, y1: 46, y2: T + DOWN + 8}, back);
      var hit = el("rect", {x: a, y: 30, width: Math.max(0.5, w), height: 22, "class": "hit"}, back);
      hit._phase = k;
      el("title", {}, hit).textContent = self.phaseText(ph);
      if (w > 40 && a >= lastLab) {
        var lab = ph.kind === "idle" ? dur(ph.seconds) + " idle" : self.phaseName(ph);
        var t = el("text", {x: a + 5, y: 40, "class": k === f ? "now" : "mu"}, back);
        t.textContent = fit(lab, w - 8);
        if (ph.kind !== "idle" && w > 90) {
          var t2 = el("text", {x: a + 5, y: 54, "class": "mu"}, back);
          t2.textContent = when(ph.from, self.start);
        }
        lastLab = a + Math.min(w, lab.length * 6 + 12);
      }
      if (ph.kind === "loop" && w > 8) {
        var peak = T - UP - 14;
        el("path", {"class": "arc", d: "M" + (a + 2) + "," + (T - 1) + " Q" + (a + w / 2) + "," + (2 * peak - T) + " " +
          (b - 2) + "," + (T - 1)}, front);
      }
    });
    this.counts(before, after);
    // every call, a branch off the trunk
    var labels = [], lastTip = -1e9;
    this.marks.forEach(function (m) {
      var ph = m._p, k = ph.i, a = xs[k][0], b = xs[k][1], w = b - a;
      if (w < 0.6) { m.setAttribute("display", "none"); if (m._tip) m._tip.setAttribute("display", "none"); return; }
      m.removeAttribute("display"); if (m._tip) m._tip.removeAttribute("display");
      var x = m._x, n = ph.calls.length, pos;
      if (self.pace === "even") pos = a + w * (m._j + 0.5) / n;
      else pos = a + w * (x.s + x.d / 2 - ph.from) / Math.max(1e-3, ph.to - ph.from);
      var fx = self.fish(pos);
      m._cx = fx;
      var near = self.mx == null ? 1 : Math.abs(fx - self.mx) / R;
      var grow = near < 1 ? 1 + 0.5 * (1 - near) : 1;
      var op = self.match(x) ? (k === f ? 1 : 0.4) : 0.08;
      if (m._think) {
        m.setAttribute("cx", fx.toFixed(1));
        m.setAttribute("opacity", op * 0.8);
        return;
      }
      var dense = w / n < 3;                 // a crowded phase: a quiet comb, its detail under the lens
      if (dense && near >= 1) op *= 0.55;
      var len = m._len * grow, down = failed(x);
      var y2 = down ? T + 1 + len : T - 1 - len;
      m.setAttribute("x1", fx.toFixed(1)); m.setAttribute("x2", fx.toFixed(1));
      m.setAttribute("y1", down ? T + 1 : T - 1); m.setAttribute("y2", y2.toFixed(1));
      m.setAttribute("opacity", op);
      if (m._tip) {
        m._tip.setAttribute("cx", fx.toFixed(1)); m._tip.setAttribute("cy", y2.toFixed(1));
        m._tip.setAttribute("opacity", dense && near >= 1 ? 0 : op);
      }
      if (k === f && op === 1 && fx - lastTip >= 46 && n * 46 < w * 1.2) {
        labels.push([fx, down ? y2 + 12 : y2 - 5, x.name]); lastTip = fx;
      }
    });
    labels.forEach(function (lb) {
      var t = el("text", {x: lb[0].toFixed(1), y: lb[1].toFixed(1), "text-anchor": "middle", "class": "mu"}, front);
      t.textContent = fit(lb[2], 44);
    });
    // checkpoints on the trunk
    this.phases.forEach(function (ph, k) {
      var a = xs[k][0], b = xs[k][1], w = b - a;
      if (w < 0.6 || !ph.events) return;
      ph.events.forEach(function (ev) {
        var j = 0;
        for (var q = 0; q < ph.calls.length; q++) if (ph.calls[q].i === ev.index) { j = q; break; }
        var x = self.fish(self.pace === "even" ? a + w * (j + 0.5) / ph.calls.length :
          a + w * (ev.t - ph.from) / Math.max(1e-3, ph.to - ph.from));
        var c = ev.kind === "progress" ? el("circle", {"class": "mile", cx: x, cy: T, r: 2.6}, front)
          : el("text", {x: x, y: T + DOWN + 14, "text-anchor": "middle", "class": "lab2"}, front);
        if (ev.kind !== "progress") c.textContent = "×";
        el("title", {}, c).textContent = "step " + ev.index + ": " + ev.check +
          (ev.kind === "progress" ? " passed (" + ev.how + ")" : " failed after passing");
      });
    });
    this.foot.textContent = this.pace === "even" ? "Every call the same width inside its phase. A call rises from " +
      "the trunk, taller when it took longer; a failed one hangs below; a dot is a check that passed; an arc is a loop."
      : "Each phase on its own clock: a call sits where it happened.";
    this.title.textContent = "Phase " + (f + 1) + " of " + this.phases.length + " · " + this.phaseText(p);
    this.prevB.disabled = f <= 0; this.nextB.disabled = f >= this.phases.length - 1;
    this.mini(xs);
    if (this.mx != null) this.lensAt(this.mx, true);
  };

  Lens.prototype.counts = function (before, after) {
    var g = this.gFront;
    if (before) el("text", {x: LEFT, y: T + DOWN + 26, "class": "mu"}, g).textContent = "← " + before + " phase(s)";
    if (after) el("text", {x: W - RIGHT, y: T + DOWN + 26, "text-anchor": "end", "class": "mu"}, g).textContent =
      after + " phase(s) →";
  };

  Lens.prototype.phaseName = function (p) {
    var span = p.bursts[0] === p.bursts[1] ? "burst " + p.bursts[0] : "bursts " + p.bursts[0] + "–" + p.bursts[1];
    if (p.kind === "loop") return "↻ loop · " + span;
    if (p.kind === "filler") return "filler · " + span;
    return span + (p.status === "progress" ? " · forward" : p.status ? " · " + p.status : "");
  };
  Lens.prototype.phaseText = function (p) {
    if (p.kind === "idle") return dur(p.seconds) + " with no step at all";
    var s = this.phaseName(p) + " · " + when(p.from, this.start) + " → " + when(p.to, this.start) + " · " +
      dur(p.to - p.from) + " · " + p.calls.length.toLocaleString() + " steps";
    if (p.fails) s += " · " + p.fails + " failed";
    if (p.kind === "loop" && p.failing) s += " · " + p.failing + " kept failing";
    var prog = p.events.filter(function (e) { return e.kind === "progress"; });
    if (prog.length) s += " · passed: " + prog.map(function (e) { return e.check; }).join(", ");
    return s;
  };

  Lens.prototype.mini = function (xs) {
    // the whole run as a thin trail, what is on the tape drawn darker
    var g = this.gMini; g.innerHTML = "";
    var total = 0; this.phases.forEach(function (p) { total += p.base; });
    var x = LEFT, span = W - LEFT - RIGHT, lo = null, hi = null;
    var y = 12;
    this.phases.forEach(function (p, k) {
      var w = span * p.base / total;
      el("line", {"class": p.kind === "idle" ? "gap" : "tick", x1: x, x2: x + w, y1: y, y2: y}, g);
      if (p.kind === "loop") el("path", {"class": "arc", d: "M" + x + "," + y + " Q" + (x + w / 2) + "," + (y - 10) + " " +
        (x + w) + "," + y, style: "stroke-width:1"}, g);
      if (p.events && p.events.some(function (e) { return e.kind === "progress"; }))
        el("circle", {"class": "mile", cx: x + w / 2, cy: y, r: 1.6}, g);
      var hit = el("rect", {x: x, y: 2, width: Math.max(0.5, w), height: 16, "class": "hit"}, g);
      hit._phase = k;
      if (xs[k][1] - xs[k][0] >= 0.6) { if (lo == null) lo = x; hi = x + w; }
      x += w;
    });
    if (lo != null) el("line", {"class": "trunk", x1: lo, x2: hi, y1: y + 6, y2: y + 6, style: "stroke-width:2"}, g);
  };

  // ------------------------------------------------------------ the lens
  Lens.prototype.hover = function (ev) {
    var box = this.svg.getBoundingClientRect();
    var x = (ev.clientX - box.left) * W / box.width, y = (ev.clientY - box.top) * H / box.height;
    this.lensAt(x < LEFT || x > W - RIGHT || y < 26 ? null : x);
  };
  Lens.prototype.nearest = function () {
    var best = null, bd = 1e9, mx = this.mx;
    if (mx == null) return null;
    this.marks.forEach(function (m) {
      if (m.getAttribute("display") === "none" || m._cx == null) return;
      var d = Math.abs(m._cx - mx);
      if (d < bd) { bd = d; best = m._x; }
    });
    return bd <= 12 ? best : null;
  };
  Lens.prototype.lensAt = function (x, fromLayout) {
    if (x == null) {
      this.mx = null; this.gHalo.style.display = "none"; this.idleCard();
      if (!fromLayout) this.layout();
      return;
    }
    var moved = this.mx !== x;
    this.mx = x;
    if (moved && !fromLayout) { this.layout(); return; }
    this.gHalo.style.display = "";
    var ring = this.gHalo.querySelector("circle"), spine = this.gHalo.querySelector("line");
    ring.setAttribute("cx", x); ring.setAttribute("cy", T - 8);
    spine.setAttribute("x1", x); spine.setAttribute("x2", x);
    var under = [];
    this.marks.forEach(function (m) {
      if (m.getAttribute("display") === "none" || m._cx == null) return;
      if (Math.abs(m._cx - x) <= R * 0.32) under.push(m._x);
    });
    if (!under.length) { this.idleCard(); return; }
    under.sort(function (a, b) { return a.i - b.i; });
    var secs = 0, toks = 0, bad = 0, tools = {};
    under.forEach(function (c) { secs += c.d; toks += c.k; if (failed(c)) bad++; tools[c.name] = (tools[c.name] || 0) + c.d; });
    var top = Object.keys(tools).sort(function (a, b) { return tools[b] - tools[a]; }).slice(0, 3);
    var card = this.card;
    card.innerHTML = "";
    h("div", "lens-sum", under.length + " step(s) under the lens · " + dur(secs) + " · " + toks.toLocaleString() +
      " tokens" + (bad ? " · " + bad + " failed" : "") + " · most time: " +
      top.map(function (t) { return t + " " + dur(tools[t]); }).join(", "), card);
    var list = h("div", "lens-list", null, card);
    var shown = under.length > 12 ? under.slice(0, 6).concat([null]).concat(under.slice(-5)) : under;
    shown.forEach(function (c) {
      if (!c) { h("div", "muted", "⋯ " + (under.length - 11) + " more", list); return; }
      var row = h("div", "lens-row" + (failed(c) ? " bad" : ""), null, list);
      h("i", "", GLYPH[c.a] || "·", row);
      h("b", "", "#" + c.i + " " + c.name, row);
      h("span", "mono", c.t ? " " + c.t.slice(0, 64) : "", row);
      h("span", "n", dur(c.d) + " · " + c.k.toLocaleString() + " tok" + (c.c === 1 ? " · passed" : c.c === 0 ? " · failed" : "") +
        (c.e && c.c !== 0 ? " · error" : "") + (c.retry ? " · retry" : ""), row);
    });
  };
  Lens.prototype.idleCard = function () {
    this.card.innerHTML = "";
    h("div", "muted", "Move over the trail: the lens magnifies the calls under it, and this lists them with what " +
      "they cost. A call opens its burst.", this.card);
  };

  // ------------------------------------------------------------ start
  function start(root) {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    if (root.dataset.lensInline) {   // a page written to a file carries its data with it
      try { root._lens = new Lens(root, JSON.parse(document.getElementById(root.dataset.lensInline).textContent)); }
      catch (e) { root.textContent = ""; }
      return;
    }
    fetch(root.dataset.lens, {credentials: "same-origin", headers: {"Accept": "application/json"}})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        if (!data.phases || !data.phases.length) return;
        root._lens = new Lens(root, data);
      })
      .catch(function () { root.textContent = ""; });
  }
  function scan() { Array.prototype.forEach.call(document.querySelectorAll(".lens[data-lens], .lens[data-lens-inline]"), start); }
  document.addEventListener("keydown", function (ev) {
    var lens = document.querySelector(".lens.on");
    if (lens && lens._lens && !ev.defaultPrevented && document.activeElement === document.body) lens._lens.key(ev);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan); else scan();
  // a live page swaps its panel in place: start the new lens
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
