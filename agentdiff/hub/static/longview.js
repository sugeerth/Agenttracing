// The lens: a long run, phase by phase, on one tape (agentdiff hub).
//
// The page draws the run without this script; this adds what a script can:
//  - the tape: the phase in focus wide, its neighbours narrower, the rest
//    scrolled out; every call drawn at an even pace inside its phase
//  - a fisheye round the pointer, with a halo, that magnifies the calls under
//    it and lists what they cost
//  - moving phase by phase, animated: buttons, ← →, the wheel, a drag;
//    [ and ] jump to the next failure or checkpoint
//  - filters that bring the events forward: failures, retries, checkpoints, edits
// Data comes from the hub itself (/api/v1/traces/<id>/long), nothing else.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var ACT = {explore: ["a1", "◇", "explore"], edit: ["a2", "✎", "edit"], verify: ["a3", "✓", "check"],
    run: ["a4", "▸", "run"], plan: ["a5", "≡", "plan"], research: ["a5", "⌕", "research"],
    delegate: ["a7", "⇄", "delegate"], other: ["an", "·", "other"], think: ["at", "…", "think"]};
  var LANES = ["explore", "research", "plan", "think", "edit", "run", "verify", "delegate", "other"];
  var W = 980, LEFT = 70, RIGHT = 12, TOP = 58, LANE = 15, R = 140, D = 4.5;

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

  function Lens(root, data) {
    this.root = root; this.data = data;
    var r = data.reading, c = data.calls;
    this.start = r.started_at;
    this.phases = data.phases.map(function (p, i) { p.i = i; return p; });
    // every call, with its phase
    var n = c.i.length, calls = [];
    var byIndex = {};
    for (var k = 0; k < n; k++) {
      var x = {i: c.i[k], s: c.s[k], d: c.d[k], a: data.activities[c.a[k]], name: data.names[c.n[k]],
        e: !!c.e[k], c: c.c[k], k: c.k[k], t: c.t[k]};
      calls.push(x); byIndex[x.i] = x;
    }
    // retries: the same failing call again, right after itself
    for (k = 1; k < n; k++) {
      var a = calls[k - 1], b = calls[k];
      if ((b.e || b.c === 0) && (a.e || a.c === 0) && a.name === b.name && a.t === b.t) { a.retry = b.retry = true; }
    }
    var self = this;
    this.phases.forEach(function (p) {
      p.calls = [];
      if (p.kind === "idle") return;
      for (var i = p.first; i <= p.last; i++) if (byIndex[i]) p.calls.push(byIndex[i]);
      p.fails = p.calls.filter(function (x) { return x.e || x.c === 0; }).length;
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
    if (root.dataset.burst) {
      var bn = +root.dataset.burst;
      start = this.phaseOfBurst(bn);
    } else if (!isNaN(saved)) start = saved;
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
    this.prevB = h("button", "lens-btn", "◀ prev", bar);
    this.title = h("div", "lens-title", "", bar);
    this.title.setAttribute("aria-live", "polite");
    this.nextB = h("button", "lens-btn", "next ▶", bar);
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
      a.href = "#";
      a.addEventListener("click", function (ev) {
        ev.preventDefault(); self.pace = kv[0]; store("lens-pace", kv[0]);
        Array.prototype.forEach.call(a.parentNode.querySelectorAll("[data-p]"), function (b) { b.classList.toggle("on", b === a); });
        self.layout();
      });
      a.dataset.p = kv[0];
    });
    h("span", "muted lens-hint", "← → or wheel: phases · [ ] next failure or checkpoint · hover: the lens", chips);
    var height = TOP + LANE * LANES.length + 44;
    this.height = height;
    var svg = this.svg = el("svg", {"class": "viz lens-tape", viewBox: "0 0 " + W + " " + height, width: W,
      role: "img", tabindex: "0", "aria-label": "the run, phase by phase; arrow keys move between phases"}, root);
    this.gMini = el("g", {}, svg);
    this.gBand = el("g", {}, svg);
    this.gLanes = el("g", {}, svg);
    this.gEv = el("g", {}, svg);
    this.gHalo = el("g", {"class": "lens-halo", style: "display:none"}, svg);
    LANES.forEach(function (a, k) {
      el("line", {"class": "grid", x1: LEFT, x2: W - RIGHT, y1: TOP + (k + 1) * LANE - 1, y2: TOP + (k + 1) * LANE - 1}, svg);
      var t = el("text", {x: 4, y: TOP + k * LANE + 11, style: "font-size:9.5px"}, svg);
      t.textContent = ACT[a][1] + " " + ACT[a][2];
    });
    var below = TOP + LANE * LANES.length + 32;
    this.leftCount = el("text", {x: W - RIGHT - 120, y: below, "text-anchor": "end", style: "font-size:10px"}, svg);
    this.rightCount = el("text", {x: W - RIGHT, y: below, "text-anchor": "end", style: "font-size:10px"}, svg);
    el("circle", {r: R * 0.5, "class": "ring"}, this.gHalo);
    el("line", {"class": "spine", y1: TOP - 6, y2: TOP + LANE * LANES.length + 4}, this.gHalo);
    this.card = h("div", "lens-card", null, root);
    this.idleCard();
    // marks for every call, made once; positions change
    this.marks = [];
    this.phases.forEach(function (p) {
      p.calls.forEach(function (x, j) {
        var lane = LANES.indexOf(x.a); if (lane < 0) lane = LANES.length - 1;
        var m = el("rect", {"class": (ACT[x.a] || ACT.other)[0], y: TOP + lane * LANE + 2, height: LANE - 5, rx: 1.5}, self.gLanes);
        if (x.e || x.c === 0) m.setAttribute("style", "stroke:var(--sc);stroke-width:1.5");
        m._x = x; m._p = p; m._lane = lane; m._j = j;
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
    window.addEventListener("pointerup", function () { if (drag && drag.moved) self.settle(); drag = null; });
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
      if (t._x) {
        var b = self.burstOf(t._x.i);
        if (b != null) location.href = root.dataset.base + "&burst=" + b + "#s" + t._x.i;
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
      case "fail": return x.e || x.c === 0;
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
    // the phase band
    this.gBand.innerHTML = "";
    var before = 0, after = 0;
    this.phases.forEach(function (ph, k) {
      var a = self.fish(xs[k][0]), b = self.fish(xs[k][1]), w = b - a;
      if (w < 0.6) { if (k < f) before++; else if (k > f) after++; return; }
      var cls = ph.kind === "idle" ? "idle" : ph.kind === "loop" ? "loop" : ph.kind === "filler" ? "filler" :
        (ph.status === "progress" || ph.status === "done") ? "prog" : (ph.fails ? "fail" : "work");
      var r = el("rect", {x: a, y: TOP - 24, width: Math.max(0.5, w - 1), height: 16, rx: 3,
        "class": "lens-ph " + cls + (k === f ? " now" : "")}, self.gBand);
      r._phase = k;
      var tip = el("title", {}, r);
      tip.textContent = self.phaseText(ph);
      if (w > 46) {
        var lab = ph.kind === "idle" ? "idle " + dur(ph.seconds) : self.phaseName(ph);
        var t = el("text", {x: a + 4, y: TOP - 12, "class": "lens-ph-lab", style: "font-size:10px"}, self.gBand);
        t.textContent = lab.length * 6 > w - 8 ? lab.slice(0, Math.max(0, Math.floor((w - 8) / 6) - 1)) + "…" : lab;
      }
      if (ph.kind !== "idle" && w > 70) {
        var t2 = el("text", {x: a + 4, y: TOP - 30, style: "font-size:9.5px"}, self.gBand);
        t2.textContent = when(ph.from, self.start);
      }
    });
    this.leftCount.textContent = before ? "← " + before + " phase(s)" : "";
    this.rightCount.textContent = after ? after + " phase(s) →" : "";
    // every call
    var laneEnd = TOP + LANE * LANES.length, labels = [];
    this.marks.forEach(function (m) {
      var ph = m._p, k = ph.i, a = xs[k][0], b = xs[k][1], w = b - a;
      if (w < 0.6) { m.setAttribute("display", "none"); return; }
      m.removeAttribute("display");
      var x = m._x, n = ph.calls.length, j = m._j, x0, x1;
      if (self.pace === "even") { x0 = a + w * j / n; x1 = a + w * (j + 1) / n; }
      else {
        var span = Math.max(1e-3, ph.to - ph.from);
        x0 = a + w * (x.s - ph.from) / span; x1 = a + w * (x.s + x.d - ph.from) / span;
      }
      var fx0 = self.fish(x0), fx1 = self.fish(Math.max(x1, x0 + 0.01));
      var ww = Math.max(0.8, Math.min(18, (fx1 - fx0) * 0.86));
      m.setAttribute("x", fx0.toFixed(2));
      m.setAttribute("width", ww.toFixed(2));
      // under the lens a step grows, like under glass
      var near = self.mx == null ? 1 : Math.abs(fx0 - self.mx) / R;
      var grow = near < 1 ? 1 + 0.55 * (1 - near) : 1;
      var hh = (LANE - 5) * grow;
      m.setAttribute("height", hh.toFixed(2));
      m.setAttribute("y", (TOP + m._lane * LANE + 2 - (hh - (LANE - 5)) / 2).toFixed(2));
      if (k === f && fx1 - fx0 >= 46) labels.push([fx0 + ww + 3, TOP + m._lane * LANE + 11, x.name, fx1 - fx0 - ww - 4]);
      m.setAttribute("opacity", self.match(x) ? (k === f ? 1 : 0.55) : 0.1);
    });
    // checkpoints and regressions, on the event row; the tool beside each step that has room
    this.gEv.innerHTML = "";
    labels.forEach(function (lb) {
      var t = el("text", {x: lb[0].toFixed(1), y: lb[1], style: "font-size:9.5px;pointer-events:none"}, self.gEv);
      var room = Math.floor(lb[3] / 5.8);
      t.textContent = lb[2].length > room ? lb[2].slice(0, Math.max(0, room - 1)) + "…" : lb[2];
    });
    this.phases.forEach(function (ph, k) {
      var a = xs[k][0], b = xs[k][1], w = b - a;
      if (w < 0.6 || !ph.events) return;
      ph.events.forEach(function (ev) {
        var j = 0;
        for (var q = 0; q < ph.calls.length; q++) if (ph.calls[q].i === ev.index) { j = q; break; }
        var x = self.fish(self.pace === "even" ? a + w * (j + 0.5) / ph.calls.length :
          a + w * (ev.t - ph.from) / Math.max(1e-3, ph.to - ph.from));
        var t = el("text", {x: x, y: laneEnd + 14, "text-anchor": "middle",
          "class": ev.kind === "progress" ? "ok" : "bad", style: "font-size:11px"}, self.gEv);
        t.textContent = ev.kind === "progress" ? "◆" : "✗";
        var ti = el("title", {}, t);
        ti.textContent = "step " + ev.index + ": " + ev.check + (ev.kind === "progress" ? " passed (" + ev.how + ")" : " failed after passing");
      });
    });
    var foot = el("text", {x: LEFT, y: laneEnd + 32, style: "font-size:10px"}, this.gEv);
    foot.textContent = this.pace === "even" ? "pace: even — every call the same width inside its phase" :
      "pace: clock — each phase on its own clock";
    // the title, the buttons
    this.title.textContent = "Phase " + (f + 1) + " of " + this.phases.length + " · " + this.phaseText(p);
    this.prevB.disabled = f <= 0; this.nextB.disabled = f >= this.phases.length - 1;
    this.mini(xs, f);
    if (this.mx != null) this.lensAt(this.mx, true);
  };

  Lens.prototype.phaseName = function (p) {
    if (p.kind === "loop") return "↻ loop · bursts " + p.bursts[0] + "–" + p.bursts[1];
    if (p.kind === "filler") return "filler · " + (p.bursts[0] === p.bursts[1] ? "burst " + p.bursts[0] : "bursts " + p.bursts[0] + "–" + p.bursts[1]);
    return (p.bursts[0] === p.bursts[1] ? "burst " + p.bursts[0] : "bursts " + p.bursts[0] + "–" + p.bursts[1]) +
      (p.status ? " · " + p.status : "");
  };
  Lens.prototype.phaseText = function (p) {
    if (p.kind === "idle") return dur(p.seconds) + " with no step at all";
    var s = this.phaseName(p) + " · " + when(p.from, this.start) + " → " + when(p.to, this.start) + " · " +
      dur(p.to - p.from) + " · " + p.calls.length.toLocaleString() + " steps";
    if (p.fails) s += " · " + p.fails + " failed";
    if (p.kind === "loop" && p.failing) s += " · " + p.failing + " kept failing";
    var prog = p.events.filter(function (e) { return e.kind === "progress"; });
    if (prog.length) s += " · ✓ " + prog.map(function (e) { return e.check; }).join(", ");
    return s;
  };

  Lens.prototype.mini = function (xs, f) {
    // where you are: every phase at its base width, the visible ones boxed
    var g = this.gMini; g.innerHTML = "";
    var total = 0; this.phases.forEach(function (p) { total += p.base; });
    var x = LEFT, span = W - LEFT - RIGHT, lo = null, hi = null, self = this;
    el("text", {x: 4, y: 12, style: "font-size:9.5px"}, g).textContent = "the run";
    this.phases.forEach(function (p, k) {
      var w = span * p.base / total;
      var cls = p.kind === "idle" ? "idle" : p.kind === "loop" ? "loop" : p.fails ? "fail" :
        (p.status === "progress" || p.status === "done") ? "prog" : "work";
      var r = el("rect", {x: x, y: 4, width: Math.max(0.5, w - 1), height: 9, "class": "lens-ph " + cls}, g);
      r._phase = k;
      if (xs[k][1] - xs[k][0] >= 0.6) { if (lo == null) lo = x; hi = x + w; }
      x += w;
    });
    if (lo != null) el("rect", {x: lo - 1, y: 2, width: hi - lo + 1, height: 13, rx: 2, "class": "lens-view"}, g);
  };

  // ------------------------------------------------------------ the lens
  Lens.prototype.hover = function (ev) {
    var box = this.svg.getBoundingClientRect();
    var x = (ev.clientX - box.left) * W / box.width;
    this.lensAt(x < LEFT || x > W - RIGHT ? null : x);
  };
  Lens.prototype.lensAt = function (x, fromLayout) {
    var self = this;
    if (x == null) {
      this.mx = null; this.gHalo.style.display = "none"; this.idleCard();
      if (!fromLayout) this.layout();
      return;
    }
    var moved = this.mx !== x;
    this.mx = x;
    if (moved && !fromLayout) { this.layout(); return; }
    this.gHalo.style.display = "";
    var cy = TOP + LANE * LANES.length / 2;
    var ring = this.gHalo.querySelector(".ring"), spine = this.gHalo.querySelector(".spine");
    ring.setAttribute("cx", x); ring.setAttribute("cy", cy);
    spine.setAttribute("x1", x); spine.setAttribute("x2", x);
    // the calls under the halo, and what they cost
    var under = [];
    this.marks.forEach(function (m) {
      if (m.getAttribute("display") === "none") return;
      var mx = +m.getAttribute("x") + +m.getAttribute("width") / 2;
      if (Math.abs(mx - x) <= R * 0.32) under.push(m._x);
    });
    if (!under.length) { this.idleCard(); return; }
    under.sort(function (a, b) { return a.i - b.i; });
    var secs = 0, toks = 0, bad = 0, tools = {};
    under.forEach(function (c) { secs += c.d; toks += c.k; if (c.e || c.c === 0) bad++; tools[c.name] = (tools[c.name] || 0) + c.d; });
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
      var row = h("div", "lens-row" + ((c.e || c.c === 0) ? " bad" : ""), null, list);
      var i = h("i", (ACT[c.a] || ACT.other)[0], (ACT[c.a] || ACT.other)[1], row);
      h("b", "", "#" + c.i + " " + c.name, row);
      h("span", "mono", c.t ? " " + c.t.slice(0, 64) : "", row);
      h("span", "n", dur(c.d) + " · " + c.k.toLocaleString() + " tok" + (c.c === 1 ? " · ✓" : c.c === 0 ? " · ✗" : "") +
        (c.retry ? " · retry" : ""), row);
    });
  };
  Lens.prototype.idleCard = function () {
    this.card.innerHTML = "";
    h("div", "muted", "Move over the tape: the lens magnifies the steps under it, and this lists them with what they " +
      "cost. A step opens its burst.", this.card);
  };

  // ------------------------------------------------------------ start
  function start(root) {
    if (root.dataset.ready) return;
    root.dataset.ready = "1";
    fetch(root.dataset.lens, {credentials: "same-origin", headers: {"Accept": "application/json"}})
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        if (!data.phases || !data.phases.length) return;
        var lens = new Lens(root, data);
        root._lens = lens;
        document.documentElement.classList.add("has-lens");
      })
      .catch(function () { root.textContent = ""; });
  }
  function scan() { Array.prototype.forEach.call(document.querySelectorAll(".lens[data-lens]"), start); }
  document.addEventListener("keydown", function (ev) {
    var lens = document.querySelector(".lens.on");
    if (lens && lens._lens && !ev.defaultPrevented && document.activeElement === document.body) lens._lens.key(ev);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan); else scan();
  // a live page swaps its panel in place: start the new lens
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
