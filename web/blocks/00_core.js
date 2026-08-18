/* AgentDiff blocks — core.
 *
 * Everything a block module needs, and nothing a block module should do for
 * itself: visitor identity, storage, the block registry, the stack layout
 * engine (drag, reorder, collapse, span), and the personalization ranking.
 *
 * Storage is first-party and local. Nothing here transmits anything: the
 * page has no network code at all, which is why it can be opened from a
 * file:// URL with the machine offline. The visitor id exists so one
 * person's layout and interests persist across reports on this browser, and
 * the "You" panel shows exactly what is held and clears it in one click.
 */
(function (global) {
  "use strict";

  var NS = "agentdiff";
  var VERSION = 1;
  var COOKIE = "agentdiff_vid";
  var YEAR = 60 * 60 * 24 * 365;
  var HALF_LIFE_DAYS = 14;
  //: blocks past this position in a stack open collapsed by default.
  var OPEN_PER_STACK = 3;
  //: a rendered block taller than this is clamped with a "show all" control.
  var CLAMP_HEIGHT = 760;
  /*: The block the page leads with, full width, above the columns.
   *
   * "git diff for AI agents" is a claim about a picture: two trajectories,
   * aligned, with the divergence marked. That picture cannot be read in a
   * 300px column, so it does not live in one — it gets the whole content
   * width and everything else reads as evidence underneath it. It is still
   * an ordinary block rendered through the ordinary render(el, ctx); the
   * lane is a place, not a special case. */
  var DEFAULT_HERO = "tracks";

  // ---------------------------------------------------------------- identity

  function uuid() {
    try {
      if (global.crypto && typeof global.crypto.randomUUID === "function") {
        return global.crypto.randomUUID();
      }
      if (global.crypto && global.crypto.getRandomValues) {
        var bytes = global.crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        var hex = [];
        for (var i = 0; i < bytes.length; i++) {
          hex.push((bytes[i] + 0x100).toString(16).slice(1));
        }
        return (hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
                hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
                hex.slice(10, 16).join(""));
      }
    } catch (err) { /* fall through */ }
    // Last resort: still unique enough to key a local layout, and clearly
    // labelled so the You panel can say the crypto source was unavailable.
    return "fallback-" + Date.now().toString(36) + "-" +
           Math.floor(performance.now() * 1000).toString(36);
  }

  function readCookie(name) {
    try {
      var parts = String(document.cookie || "").split(";");
      for (var i = 0; i < parts.length; i++) {
        var pair = parts[i].trim();
        if (pair.indexOf(name + "=") === 0) {
          return decodeURIComponent(pair.slice(name.length + 1));
        }
      }
    } catch (err) { /* cookies unavailable */ }
    return null;
  }

  function writeCookie(name, value, maxAge) {
    try {
      document.cookie = name + "=" + encodeURIComponent(value) +
        ";path=/;max-age=" + maxAge + ";SameSite=Lax";
      return readCookie(name) === value;
    } catch (err) { return false; }
  }

  /* A store that degrades instead of failing.
   *
   * file:// pages get inconsistent treatment: some browsers refuse cookies
   * for null origins, some partition localStorage per file. So try both,
   * remember which actually worked, and let the You panel report it — a
   * layout that silently fails to persist is worse than one that says it
   * cannot. */
  var Store = (function () {
    var memory = {};
    var backends = { cookie: false, local: false };

    try {
      var probe = NS + ":probe";
      global.localStorage.setItem(probe, "1");
      backends.local = global.localStorage.getItem(probe) === "1";
      global.localStorage.removeItem(probe);
    } catch (err) { backends.local = false; }

    backends.cookie = writeCookie(NS + "_probe", "1", 60);
    if (backends.cookie) writeCookie(NS + "_probe", "", 0);

    return {
      backends: backends,
      get durable() { return backends.local || backends.cookie; },
      backendName: function () {
        if (backends.local) return "localStorage";
        if (backends.cookie) return "cookie only";
        return "memory (this tab only)";
      },
      get: function (key) {
        if (backends.local) {
          try {
            var raw = global.localStorage.getItem(key);
            if (raw !== null) return JSON.parse(raw);
          } catch (err) { /* corrupt entry; fall through */ }
        }
        return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null;
      },
      set: function (key, value) {
        memory[key] = value;
        if (backends.local) {
          try { global.localStorage.setItem(key, JSON.stringify(value)); }
          catch (err) { /* quota or disabled mid-session */ }
        }
      },
      remove: function (key) {
        delete memory[key];
        if (backends.local) {
          try { global.localStorage.removeItem(key); } catch (err) {}
        }
      },
      keys: function (prefix) {
        var found = [];
        if (backends.local) {
          try {
            for (var i = 0; i < global.localStorage.length; i++) {
              var key = global.localStorage.key(i);
              if (key && key.indexOf(prefix) === 0) found.push(key);
            }
          } catch (err) {}
        }
        for (var key2 in memory) {
          if (key2.indexOf(prefix) === 0 && found.indexOf(key2) < 0) found.push(key2);
        }
        return found;
      },
    };
  })();

  function visitorId() {
    var id = readCookie(COOKIE);
    if (!id) id = Store.get(NS + ":vid");
    if (!id || typeof id !== "string") {
      id = uuid();
      Identity.minted = true;
    }
    writeCookie(COOKIE, id, YEAR);
    Store.set(NS + ":vid", id);
    return id;
  }

  var Identity = { minted: false, id: null };

  function key(name) { return NS + ":v" + VERSION + ":" + Identity.id + ":" + name; }

  // ---------------------------------------------------------------- registry

  var REGISTRY = [];
  var BY_ID = {};

  function block(spec) {
    if (!spec || !spec.id || typeof spec.render !== "function") {
      console.warn("AgentDiff: ignoring malformed block", spec);
      return;
    }
    if (BY_ID[spec.id]) {
      console.warn("AgentDiff: duplicate block id", spec.id);
      return;
    }
    var entry = {
      id: spec.id,
      title: spec.title || spec.id,
      question: spec.question || "",
      group: spec.group || "other",
      size: spec.size || "normal",
      relevance: typeof spec.relevance === "function" ? spec.relevance : function () { return 0.5; },
      render: spec.render,
    };
    REGISTRY.push(entry);
    BY_ID[spec.id] = entry;
  }

  // ------------------------------------------------------------- formatting

  var fmt = {
    int: function (n) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return Math.round(n).toLocaleString();
    },
    num: function (n, places) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return Number(n).toFixed(places === undefined ? 2 : places);
    },
    pct: function (n, places) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return (n * 100).toFixed(places === undefined ? 0 : places) + "%";
    },
    usd: function (n) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return "$" + Number(n).toFixed(Math.abs(n) < 0.01 ? 4 : 2);
    },
    sec: function (n) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return Number(n).toFixed(n < 10 ? 2 : 1) + "s";
    },
    tokens: function (n) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return Math.round(n).toLocaleString() + " tok";
    },
    delta: function (n, unit) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      var sign = n > 0 ? "+" : "";
      return sign + (Math.abs(n) < 1 && n !== 0 ? Number(n).toFixed(2) : Math.round(n).toLocaleString()) +
             (unit ? " " + unit : "");
    },
    truncate: function (text, max) {
      text = String(text === null || text === undefined ? "" : text);
      max = max || 120;
      return text.length > max ? text.slice(0, max - 1) + "…" : text;
    },
  };

  function cssVar(name) {
    try {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
    } catch (err) { return "#888"; }
  }

  var color = {};
  Object.defineProperties(color, {
    a:     { get: function () { return cssVar("--a"); }, enumerable: true },
    b:     { get: function () { return cssVar("--b"); }, enumerable: true },
    good:  { get: function () { return cssVar("--good"); }, enumerable: true },
    bad:   { get: function () { return cssVar("--bad"); }, enumerable: true },
    warn:  { get: function () { return cssVar("--warn"); }, enumerable: true },
    ink:   { get: function () { return cssVar("--ink"); }, enumerable: true },
    muted: { get: function () { return cssVar("--ink-3"); }, enumerable: true },
    axis:  { get: function () { return cssVar("--rule-2"); }, enumerable: true },
    grid:  { get: function () { return cssVar("--rule"); }, enumerable: true },
    surface: { get: function () { return cssVar("--surface"); }, enumerable: true },
  });

  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    applyAttrs(el, attrs);
    appendKids(el, kids);
    return el;
  }

  function svg(tag, attrs, kids) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) {
      for (var name in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, name)) continue;
        var value = attrs[name];
        if (value === null || value === undefined || value === false) continue;
        if (name === "text") { el.textContent = value; continue; }
        if (name.indexOf("on") === 0 && typeof value === "function") {
          el.addEventListener(name.slice(2), value);
          continue;
        }
        el.setAttribute(name, value);
      }
    }
    appendKids(el, kids);
    return el;
  }

  function applyAttrs(el, attrs) {
    if (!attrs) return;
    for (var name in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, name)) continue;
      var value = attrs[name];
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") { el.className = value; }
      else if (name === "text") { el.textContent = value; }
      else if (name === "html") { el.innerHTML = value; }
      else if (name === "style" && typeof value === "object") {
        for (var prop in value) {
          if (Object.prototype.hasOwnProperty.call(value, prop)) el.style[prop] = value[prop];
        }
      } else if (name.indexOf("on") === 0 && typeof value === "function") {
        el.addEventListener(name.slice(2), value);
      } else {
        el.setAttribute(name, value);
      }
    }
  }

  function appendKids(el, kids) {
    if (kids === null || kids === undefined) return;
    if (!Array.isArray(kids)) kids = [kids];
    for (var i = 0; i < kids.length; i++) {
      var kid = kids[i];
      if (kid === null || kid === undefined || kid === false) continue;
      el.appendChild(typeof kid === "object" && kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
  }

  // ------------------------------------------------------------------ state

  var State = {
    data: { reports: [], aggregate: {} },
    task: null,
    layout: null,     // {cols, stacks: [[{id, collapsed, span}]], hidden: []}
    prefs: null,      // {personalize, autoApply, theme, seenSuggestion}
    signals: null,    // {blockId: {count, weight, last}}
    suggestion: null,
  };

  function defaultPrefs() {
    return { personalize: true, autoApply: false, theme: "system", reading: true };
  }

  /* The default composition. Groups map to stacks so a first-time visitor
   * gets a coherent reading order — the verdict and its attribution first,
   * then the trajectory it came from, then what it cost — rather than
   * whatever order the modules happened to load in.
   *
   * Four columns, not five. Once the trace has the top of the page, the
   * columns underneath are supporting evidence, and five of them at 1440px
   * gives each one under 280px — too narrow for the tables most of these
   * blocks are. Trajectory and integrity merged because they answer the
   * same question in two registers ("what did the run do" / "does what it
   * did hold up"), and because the hero already leads that reading.
   *
   * Each stack carries a plain-language blurb: "Outcome" and "Signal" are
   * nouns a first-time reader has to guess at, and guessing is the thing
   * this page is supposed to remove. */
  var STACK_PLAN = [
    {
      label: "Outcome",
      groups: ["outcome"],
      blurb: "Who won, what broke, and what caused it.",
    },
    {
      label: "Trajectory",
      groups: ["trajectory", "integrity"],
      blurb: "Step by step: what each run did, and whether the work behind the answer holds up.",
    },
    {
      label: "Cost",
      groups: ["cost"],
      blurb: "What the difference cost, what keeps recurring, and what to change.",
    },
    {
      label: "Evidence",
      groups: ["signal", "other"],
      blurb: "How far these numbers can be trusted — confidence, reliability, blind spots.",
    },
  ];

  function defaultLayout(ctx) {
    var stacks = STACK_PLAN.map(function () { return []; });
    var hidden = [];
    var hero = BY_ID[DEFAULT_HERO] ? { id: DEFAULT_HERO, collapsed: false } : null;
    REGISTRY.forEach(function (entry) {
      var relevance = safeRelevance(entry, ctx);
      var target = 0;
      for (var i = 0; i < STACK_PLAN.length; i++) {
        if (STACK_PLAN[i].groups.indexOf(entry.group) >= 0) { target = i; break; }
      }
      // The hero is placed, just not in a column. Leaving it out of both
      // the stacks and the hidden list keeps exactly one home for it.
      if (hero && entry.id === hero.id) return;
      if (relevance <= 0) { hidden.push(entry.id); return; }
      stacks[target].push({ id: entry.id, collapsed: false });
    });
    /* Within a stack: the plan's own group order first, then the block with
     * most to say about this data. A column that merges two groups reads in
     * the order its heading names them, so the blocks the hero points at
     * ("click a step to open it in Step detail") sit directly under it. */
    stacks.forEach(function (stack, index) {
      var order = STACK_PLAN[index].groups;
      stack.sort(function (x, y) {
        var gx = groupRank(order, BY_ID[x.id].group);
        var gy = groupRank(order, BY_ID[y.id].group);
        if (gx !== gy) return gx - gy;
        return safeRelevance(BY_ID[y.id], ctx) - safeRelevance(BY_ID[x.id], ctx);
      });
      // Everything stays in the layout, but a column that opens as a metre
      // of cards is not a dashboard — past the first few, blocks start
      // collapsed and are one click from open.
      stack.forEach(function (item, index) {
        if (index >= OPEN_PER_STACK) item.collapsed = true;
      });
    });
    return { cols: STACK_PLAN.length, stacks: stacks, hidden: hidden, hero: hero };
  }

  //: a group the plan does not name sorts after the ones it does.
  function groupRank(groups, group) {
    var index = groups.indexOf(group);
    return index < 0 ? groups.length : index;
  }

  function safeRelevance(entry, ctx) {
    if (!entry) return 0;
    try {
      var value = entry.relevance(ctx);
      if (typeof value !== "number" || isNaN(value)) return 0;
      return Math.max(0, Math.min(1, value));
    } catch (err) {
      console.warn("AgentDiff: relevance() threw in", entry.id, err);
      return 0;
    }
  }

  /* Reconcile a stored layout with the block registry as it is now.
   *
   * A stored layout outlives the build that wrote it: modules get added and
   * removed. Dropping unknown ids and appending genuinely new blocks means a
   * returning visitor keeps their arrangement without silently missing
   * whatever shipped since. */
  function reconcile(stored, ctx) {
    var fallback = defaultLayout(ctx);
    if (!stored || !Array.isArray(stored.stacks)) return fallback;

    var seen = {};
    var stacks = STACK_PLAN.map(function () { return []; });

    /* A layout stored before the columns were rearranged has a different
     * number of them, and index 2 no longer means what it meant when it was
     * written. When the shape still matches, honour the visitor's own
     * cross-column drags; when it does not, re-home each block by its group
     * rather than dropping it or filing it under a stack that has since
     * come to mean something else. */
    var byIndex = stored.stacks.length === STACK_PLAN.length;
    for (var i = 0; i < stored.stacks.length; i++) {
      var source = Array.isArray(stored.stacks[i]) ? stored.stacks[i] : [];
      for (var j = 0; j < source.length; j++) {
        var item = source[j];
        var id = item && item.id;
        if (!id || !BY_ID[id] || seen[id]) continue;
        seen[id] = true;
        var target = byIndex ? Math.min(i, STACK_PLAN.length - 1) : stackFor(BY_ID[id]);
        stacks[target].push({ id: id, collapsed: !!item.collapsed, expanded: !!item.expanded });
      }
    }

    /* The hero. `undefined` is an older layout that predates the lane — it
     * gets the default. `null` is a visitor who deliberately sent the hero
     * back to its column, which is a choice, not a gap. */
    var hero;
    if (stored.hero === null) {
      hero = null;
    } else if (stored.hero && BY_ID[stored.hero.id]) {
      hero = {
        id: stored.hero.id,
        collapsed: !!stored.hero.collapsed,
        expanded: !!stored.hero.expanded,
      };
    } else if (typeof stored.hero === "string" && BY_ID[stored.hero]) {
      hero = { id: stored.hero, collapsed: false };
    } else {
      // Either no hero field at all, or one naming a block this build no
      // longer has. Both mean "no choice on record": take the default.
      hero = fallback.hero;
    }
    // Whatever is in the lane is placed; it must not also be appended to a
    // column below as if it were missing.
    if (hero) {
      seen[hero.id] = true;
      for (var s = 0; s < stacks.length; s++) {
        for (var k = stacks[s].length - 1; k >= 0; k--) {
          if (stacks[s][k].id === hero.id) stacks[s].splice(k, 1);
        }
      }
    }

    var hidden = [];
    (Array.isArray(stored.hidden) ? stored.hidden : []).forEach(function (id) {
      if (BY_ID[id] && !seen[id]) { seen[id] = true; hidden.push(id); }
    });
    // Blocks the stored layout never knew about.
    fallback.stacks.forEach(function (stack, index) {
      stack.forEach(function (item) {
        if (seen[item.id]) return;
        seen[item.id] = true;
        stacks[index].push(item);
      });
    });
    fallback.hidden.forEach(function (id) {
      if (!seen[id]) { seen[id] = true; hidden.push(id); }
    });
    return {
      cols: stored.cols >= 1 && stored.cols <= STACK_PLAN.length
        ? stored.cols : STACK_PLAN.length,
      stacks: stacks,
      hidden: hidden,
      hero: hero,
    };
  }

  // --------------------------------------------------------------- the hero

  //: a hero substituted for one with nothing to say keeps its own open state
  //: for the session, so expanding it does not undo itself on the next render.
  var substitute = null;

  /* Which block actually leads the page for the data in front of us.
   *
   * The stored choice is a preference, not a promise: a report with no
   * alignment has nothing for the tracks block to draw, and a hero frame
   * around an empty state is worse than no hero at all. So the choice is
   * honoured when it has something to say, stood in for by the most
   * relevant block when it does not, and dropped entirely when nothing
   * qualifies. */
  function resolveHero(ctx) {
    var stored = State.layout.hero;
    if (stored === null) return null;

    var wanted = stored && BY_ID[stored.id] ? stored : null;
    var source = wanted ? "chosen" : "default";
    if (!wanted && BY_ID[DEFAULT_HERO]) wanted = { id: DEFAULT_HERO, collapsed: false };
    if (wanted && safeRelevance(BY_ID[wanted.id], ctx) > 0) {
      return { item: wanted, source: source, instead: null };
    }

    var best = null, bestScore = 0;
    REGISTRY.forEach(function (entry) {
      if (wanted && entry.id === wanted.id) return;
      var score = safeRelevance(entry, ctx);
      if (score > bestScore) { bestScore = score; best = entry; }
    });
    if (!best) return null;
    if (!substitute || substitute.id !== best.id) {
      substitute = { id: best.id, collapsed: false };
    }
    return {
      item: substitute,
      source: "fallback",
      instead: wanted ? wanted.id : null,
    };
  }

  function promoteHero(id) {
    if (!BY_ID[id]) return;
    var previous = State.layout.hero;
    removeFromLayout(id);
    State.layout.hero = { id: id, collapsed: false };
    if (previous && previous.id !== id && BY_ID[previous.id]) placeBack(previous);
    recordSignal(id, "pin");
    saveLayout();
    renderAll();
  }

  function demoteHero() {
    var hero = State.layout.hero;
    State.layout.hero = null;
    if (hero && BY_ID[hero.id]) placeBack(hero);
    saveLayout();
    renderAll();
  }

  /* Send a block back to the column its group belongs to, unless it is
   * already sitting in one — a substituted hero never left. */
  function placeBack(item) {
    var found = false;
    State.layout.stacks.forEach(function (stack) {
      stack.forEach(function (row) { if (row.id === item.id) found = true; });
    });
    if (found || State.layout.hidden.indexOf(item.id) >= 0) return;
    State.layout.stacks[stackFor(BY_ID[item.id])].unshift({
      id: item.id, collapsed: false, expanded: !!item.expanded,
    });
  }

  function removeFromLayout(id) {
    State.layout.stacks.forEach(function (stack) {
      for (var i = stack.length - 1; i >= 0; i--) {
        if (stack[i].id === id) stack.splice(i, 1);
      }
    });
    var index = State.layout.hidden.indexOf(id);
    if (index >= 0) State.layout.hidden.splice(index, 1);
  }

  // ------------------------------------------------------- personalization

  /* Interest is what the visitor actually did with a block, decayed so a
   * burst of attention three weeks ago stops outranking today's work. */
  function recordSignal(id, kind) {
    if (!State.prefs.personalize) return;
    var weights = { inspect: 1, expand: 0.6, hover: 0.15, pin: 3 };
    var weight = weights[kind] || 0.2;
    var entry = State.signals[id] || { weight: 0, count: 0, last: 0 };
    var now = Date.now();
    entry.weight = decay(entry.weight, entry.last, now) + weight;
    entry.count += 1;
    entry.last = now;
    State.signals[id] = entry;
    saveSignalsSoon();
  }

  function decay(weight, last, now) {
    if (!weight || !last) return weight || 0;
    var days = (now - last) / 86400000;
    if (days <= 0) return weight;
    return weight * Math.pow(0.5, days / HALF_LIFE_DAYS);
  }

  var saveTimer = null;
  function saveSignalsSoon() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      Store.set(key("signals"), State.signals);
      saveTimer = null;
    }, 400);
  }

  function interestScore(id, now) {
    var entry = State.signals[id];
    if (!entry) return 0;
    return decay(entry.weight, entry.last, now);
  }

  /* The ranking: half what this data has to say, half what this visitor
   * keeps coming back to. Both halves are normalised so neither can run away
   * with the ordering. */
  function rank(ctx) {
    var now = Date.now();
    var interests = REGISTRY.map(function (entry) { return interestScore(entry.id, now); });
    var maxInterest = Math.max.apply(null, interests.concat([0])) || 1;
    return REGISTRY.map(function (entry, index) {
      var relevance = safeRelevance(entry, ctx);
      var interest = interests[index] / maxInterest;
      return {
        id: entry.id,
        relevance: relevance,
        interest: interest,
        score: State.prefs.personalize ? 0.55 * relevance + 0.45 * interest : relevance,
      };
    }).sort(function (x, y) { return y.score - x.score; });
  }

  /* A suggestion is offered, never applied behind the visitor's back.
   *
   * Silently reordering the page under someone who has arranged it is the
   * failure mode of every personalized dashboard; the layout they built is a
   * stronger signal than anything inferred from clicks. Auto-apply exists,
   * but it is off until asked for. */
  function computeSuggestion(ctx) {
    if (!State.prefs.personalize) return null;
    var ranked = rank(ctx);
    var scores = {};
    ranked.forEach(function (row) { scores[row.id] = row.score; });

    var moved = 0;
    var stacks = State.layout.stacks.map(function (stack, index) {
      // Reorder within the column's stated structure, not across it: a
      // suggestion that shuffles integrity blocks up among the trajectory
      // ones contradicts the heading the reader just read, and — since the
      // default layout is built the same way — would otherwise offer, on
      // first load, to undo the arrangement the page shipped with.
      var order = STACK_PLAN[index] ? STACK_PLAN[index].groups : [];
      var sorted = stack.slice().sort(function (x, y) {
        var gx = groupRank(order, BY_ID[x.id].group);
        var gy = groupRank(order, BY_ID[y.id].group);
        if (gx !== gy) return gx - gy;
        return (scores[y.id] || 0) - (scores[x.id] || 0);
      });
      for (var i = 0; i < sorted.length; i++) {
        if (sorted[i].id !== stack[i].id) moved++;
      }
      return sorted;
    });

    // A block that is hidden but now both relevant and repeatedly sought.
    var promote = State.layout.hidden.filter(function (id) {
      return (scores[id] || 0) > 0.55 && interestScore(id, Date.now()) > 0;
    });
    if (!moved && !promote.length) return null;
    return { stacks: stacks, promote: promote, moved: moved };
  }

  // ------------------------------------------------------------------ views

  var els = {};

  function makeCtx() {
    return {
      report: currentReport(),
      reports: State.data.reports || [],
      aggregate: State.data.aggregate || {},
      task: State.task,
      selectTask: selectTask,
      signal: function () {},   // replaced per block in renderBlock
      h: h, svg: svg, fmt: fmt, color: color,
      empty: function (el, message) {
        el.appendChild(h("div", { class: "empty", text: message || "Nothing to show for this run." }));
      },
      width: function (el) {
        var w = el && el.clientWidth;
        return w && w > 40 ? w : 320;
      },
    };
  }

  function currentReport() {
    var reports = State.data.reports || [];
    if (!reports.length) return null;
    for (var i = 0; i < reports.length; i++) {
      if (reports[i] && reports[i].task && reports[i].task.id === State.task) return reports[i];
    }
    return reports[0];
  }

  function renderAll() {
    var ctx = makeCtx();
    var hero = resolveHero(ctx);
    renderHero(hero, ctx);
    renderReading(hero);

    els.stacks.setAttribute("data-cols", State.layout.cols);
    els.stacks.innerHTML = "";

    State.layout.stacks.forEach(function (stack, index) {
      var column = h("div", { class: "stack", "data-stack": index });
      column.appendChild(stackLabel(index, hero ? 2 : 1));
      stack.forEach(function (item) {
        // A substituted hero is still in its column; render it once.
        if (hero && item.id === hero.item.id) return;
        column.appendChild(renderBlock(item, index, ctx, null));
      });
      wireStackDrop(column, index);
      els.stacks.appendChild(column);
    });

    els.cols.textContent = "▥ " + State.layout.cols;
    State.suggestion = computeSuggestion(ctx);
    maybeOfferSuggestion();
  }

  //: `base` is 1 when the lane is collapsed, so the numbering never starts
  //: at 2 with no step 1 anywhere on the page.
  function stackLabel(index, base) {
    var plan = STACK_PLAN[index];
    return h("div", { class: "stack-label" }, [
      h("div", { class: "name" }, [
        h("span", { class: "pip", text: String(index + base) }),
        h("span", { text: plan.label }),
      ]),
      plan.blurb ? h("div", { class: "sub", text: plan.blurb }) : null,
    ]);
  }

  /* The lane. Nothing here knows what a trace looks like: the hero is
   * rendered through the same render(el, ctx) as any other block, in a
   * container that happens to be the full content width. */
  function renderHero(hero, ctx) {
    els.hero.innerHTML = "";
    if (!hero) {
      els.hero.hidden = true;
      return;
    }
    els.hero.hidden = false;
    els.hero.appendChild(renderBlock(hero.item, null, ctx, hero));
  }

  function renderReading(hero) {
    var host = els.reading;
    host.innerHTML = "";
    if (!State.prefs.reading) { host.hidden = true; return; }
    host.hidden = false;
    host.appendChild(h("span", { class: "lead", text: "Read in this order" }));
    if (hero) {
      var entry = BY_ID[hero.item.id];
      host.appendChild(readingChip(1, entry.title, entry.question, true, function () {
        scrollTo_(els.hero);
      }));
    }
    // Only the hero chip carries its question; each column already states
    // what it answers under its own heading, and repeating it here turns a
    // one-line orientation strip into three lines of prose.
    var base = hero ? 2 : 1;
    STACK_PLAN.forEach(function (plan, index) {
      host.appendChild(readingChip(index + base, plan.label, null, false, function () {
        scrollTo_(els.stacks.children[index]);
      }));
    });
    host.appendChild(h("button", {
      class: "btn ghost tiny", text: "Hide",
      title: "Hide the reading order — restore it from the Blocks panel",
      onclick: function () {
        State.prefs.reading = false;
        savePrefs();
        renderReading(hero);
        measureTopbar();
      },
    }));
  }

  function readingChip(n, label, why, isHero, onclick) {
    return h("button", {
      class: "step-chip" + (isHero ? " is-hero" : ""),
      title: label + (why ? " — " + why : ""),
      onclick: onclick,
    }, [
      h("span", { class: "pip", text: String(n) }),
      h("b", { text: label }),
      why ? h("span", { class: "why", text: why }) : null,
    ]);
  }

  function scrollTo_(node) {
    if (!node) return;
    var bar = document.querySelector(".topbar");
    var offset = (bar ? bar.offsetHeight : 48) + 10;
    var top = node.getBoundingClientRect().top + (global.pageYOffset || 0) - offset;
    try { global.scrollTo({ top: Math.max(top, 0), behavior: "smooth" }); }
    catch (err) { global.scrollTo(0, Math.max(top, 0)); }
    node.classList.add("flash");
    setTimeout(function () { node.classList.remove("flash"); }, 900);
  }

  function renderBlock(item, stackIndex, ctx, hero) {
    var entry = BY_ID[item.id];
    var card = h("div", {
      class: "block" + (item.collapsed ? " collapsed" : "") + (hero ? " hero" : ""),
      "data-block": item.id,
      draggable: "true",
    });

    var actions = h("div", { class: "block-actions" }, [
      h("button", {
        class: "icon-btn star" + (hero ? " on" : ""),
        title: hero ? "Return this to its column" : "Lead the page with this block",
        "aria-pressed": hero ? "true" : "false",
        text: hero ? "★" : "☆",
        onclick: function (event) {
          event.stopPropagation();
          if (hero) {
            demoteHero();
            toast(entry.title + " is back in its column", "Undo", function () {
              promoteHero(item.id);
            });
          } else {
            promoteHero(item.id);
            toast(entry.title + " now leads the page");
          }
        },
      }),
      h("button", {
        class: "icon-btn", title: item.collapsed ? "Expand" : "Collapse",
        text: item.collapsed ? "▸" : "▾",
        onclick: function (event) {
          event.stopPropagation();
          item.collapsed = !item.collapsed;
          if (!item.collapsed) recordSignal(item.id, "expand");
          saveLayout();
          renderAll();
        },
      }),
      h("button", {
        class: "icon-btn", title: "Remove from layout", text: "✕",
        onclick: function (event) {
          event.stopPropagation();
          hideBlock(item.id);
        },
      }),
    ]);

    var head = h("div", { class: "block-head" }, [
      hero ? h("span", { class: "hero-tag", text: "Start here" }) : null,
      h("span", { class: "block-title", text: entry.title }),
      h("span", { class: "block-q", text: entry.question }),
      actions,
    ]);

    /* Say why the lane is showing something other than what was asked for,
     * rather than quietly swapping the page's headline. Outside the body,
     * because the body belongs to the block. */
    var note = hero && hero.source === "fallback" && hero.instead
      ? h("div", {
          class: "hero-note",
          text: (BY_ID[hero.instead] ? BY_ID[hero.instead].title : hero.instead) +
                " has nothing to show for this run, so the most relevant block " +
                "is leading instead.",
        })
      : null;

    var body = h("div", { class: "block-body" });
    if (!item.collapsed) {
      var blockCtx = Object.create(ctx);
      blockCtx.signal = function (kind) { recordSignal(item.id, kind || "inspect"); };
      try {
        entry.render(body, blockCtx);
      } catch (err) {
        // One broken block must not take the page with it.
        console.error("AgentDiff: block", item.id, "failed to render", err);
        body.innerHTML = "";
        body.appendChild(h("div", { class: "empty", text: "This block failed to render: " + err.message }));
      }
      body.addEventListener("click", function () { recordSignal(item.id, "inspect"); }, { once: true });
      body.addEventListener("mouseenter", function () { recordSignal(item.id, "hover"); }, { once: true });
      clampIfTall(card, body, item);
    }

    card.appendChild(head);
    if (note) card.appendChild(note);
    card.appendChild(body);
    // The hero has no column to be reordered within; dragging it out of the
    // lane and into one is how you demote it by hand.
    if (stackIndex === null) wireHeroDrag(card, item);
    else wireBlockDrag(card, item, stackIndex);
    return card;
  }

  /* One block with a long list in it should not push everything under it off
   * the screen. Measured after render rather than guessed from the block
   * type, so a block that happens to be short today is left alone. */
  function clampIfTall(card, body, item) {
    if (item.expanded) return;
    requestAnimationFrame(function () {
      if (!body.isConnected || body.scrollHeight <= CLAMP_HEIGHT + 80) return;
      body.style.maxHeight = CLAMP_HEIGHT + "px";
      body.style.overflowY = "hidden";   // overflow-x stays auto
      body.style.maskImage = "linear-gradient(to bottom, #000 84%, transparent)";
      body.style.webkitMaskImage = body.style.maskImage;
      var more = h("button", {
        class: "btn ghost",
        style: { margin: "0 12px 10px", width: "calc(100% - 24px)" },
        text: "Show all",
        onclick: function () {
          item.expanded = true;
          recordSignal(item.id, "expand");
          saveLayout();
          renderAll();
        },
      });
      card.appendChild(more);
    });
  }

  // --------------------------------------------------------- drag & reorder

  var dragging = null;

  function wireHeroDrag(card, item) {
    card.addEventListener("dragstart", function (event) {
      dragging = { id: item.id, from: null };
      card.classList.add("dragging");
      try {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.id);
      } catch (err) {}
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("dragging");
      clearDropMarks();
      dragging = null;
    });
  }

  function wireBlockDrag(card, item, stackIndex) {
    card.addEventListener("dragstart", function (event) {
      dragging = { id: item.id, from: stackIndex };
      card.classList.add("dragging");
      try {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.id);
      } catch (err) {}
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("dragging");
      clearDropMarks();
      dragging = null;
    });
    card.addEventListener("dragover", function (event) {
      if (!dragging || dragging.id === item.id) return;
      event.preventDefault();
      event.stopPropagation();
      var box = card.getBoundingClientRect();
      var after = event.clientY > box.top + box.height / 2;
      clearDropMarks();
      card.classList.add(after ? "drop-after" : "drop-before");
    });
    card.addEventListener("drop", function (event) {
      if (!dragging) return;
      event.preventDefault();
      event.stopPropagation();
      var box = card.getBoundingClientRect();
      var after = event.clientY > box.top + box.height / 2;
      moveBlock(dragging.id, stackIndex, item.id, after);
    });
  }

  function wireStackDrop(column, stackIndex) {
    column.addEventListener("dragover", function (event) {
      if (!dragging) return;
      event.preventDefault();
      column.classList.add("drop-target");
    });
    column.addEventListener("dragleave", function () { column.classList.remove("drop-target"); });
    column.addEventListener("drop", function (event) {
      if (!dragging) return;
      event.preventDefault();
      column.classList.remove("drop-target");
      moveBlock(dragging.id, stackIndex, null, true);
    });
  }

  function clearDropMarks() {
    var marked = els.stacks.querySelectorAll(".drop-before, .drop-after");
    for (var i = 0; i < marked.length; i++) {
      marked[i].classList.remove("drop-before", "drop-after");
    }
  }

  function moveBlock(id, toStack, anchorId, after) {
    var item = null;
    // Dropping the hero into a column is a demotion, and the lane must let
    // go of it or the block would be rendered in two places.
    if (State.layout.hero && State.layout.hero.id === id) {
      item = { id: id, collapsed: false, expanded: !!State.layout.hero.expanded };
      State.layout.hero = null;
    }
    State.layout.stacks.forEach(function (stack) {
      for (var i = stack.length - 1; i >= 0; i--) {
        if (stack[i].id === id) { item = stack.splice(i, 1)[0]; }
      }
    });
    if (!item) {
      var index = State.layout.hidden.indexOf(id);
      if (index >= 0) State.layout.hidden.splice(index, 1);
      item = { id: id, collapsed: false };
    }
    var target = State.layout.stacks[toStack] || State.layout.stacks[0];
    var at = target.length;
    if (anchorId) {
      for (var i = 0; i < target.length; i++) {
        if (target[i].id === anchorId) { at = after ? i + 1 : i; break; }
      }
    }
    target.splice(at, 0, item);
    recordSignal(id, "pin");
    saveLayout();
    renderAll();
  }

  function hideBlock(id) {
    var wasHero = State.layout.hero && State.layout.hero.id === id;
    if (wasHero) State.layout.hero = null;
    State.layout.stacks.forEach(function (stack) {
      for (var i = stack.length - 1; i >= 0; i--) {
        if (stack[i].id === id) stack.splice(i, 1);
      }
    });
    if (State.layout.hidden.indexOf(id) < 0) State.layout.hidden.push(id);
    saveLayout();
    renderAll();
    renderDrawer();
    toast("Removed " + (BY_ID[id] ? BY_ID[id].title : id), "Undo", function () {
      if (wasHero) promoteHero(id);
      else moveBlock(id, 0, null, true);
      renderDrawer();
    });
  }

  function saveLayout() { Store.set(key("layout"), State.layout); }
  function savePrefs() { Store.set(key("prefs"), State.prefs); }

  // ----------------------------------------------------------------- panels

  function renderDrawer() {
    var body = els.drawerBody;
    body.innerHTML = "";
    var ctx = makeCtx();
    var ranked = rank(ctx);
    var placed = {};
    State.layout.stacks.forEach(function (stack) {
      stack.forEach(function (item) { placed[item.id] = true; });
    });

    body.appendChild(h("p", {
      text: "Every block, ranked by what this run has to say and what you use. " +
            "Drag cards between columns to arrange them, or star one to make " +
            "it lead the page.",
    }));

    var hero = resolveHero(ctx);
    if (hero) placed[hero.item.id] = true;
    body.appendChild(h("h3", { text: "Leads the page" }));
    if (hero) {
      var lead = BY_ID[hero.item.id];
      body.appendChild(h("p", {
        text: lead.title + " — " + (lead.question || "") +
              (hero.source === "fallback"
                ? "  (standing in: " +
                  (BY_ID[hero.instead] ? BY_ID[hero.instead].title : hero.instead) +
                  " has nothing to show for this run)"
                : ""),
      }));
      body.appendChild(h("button", {
        class: "btn", text: "Return it to its column",
        onclick: function () { demoteHero(); renderDrawer(); },
      }));
    } else {
      body.appendChild(h("p", {
        text: "Nothing leads the page — the full-width lane is hidden. " +
              "Star any block to put it there.",
      }));
      body.appendChild(h("button", {
        class: "btn", text: "Restore the default lead",
        onclick: function () { promoteHero(DEFAULT_HERO); renderDrawer(); },
      }));
    }

    body.appendChild(h("h3", { text: "In your layout" }));
    var inLayout = ranked.filter(function (row) { return placed[row.id]; });
    inLayout.forEach(function (row) {
      body.appendChild(drawerItem(row, true, hero ? hero.item.id : null));
    });
    if (!inLayout.length) body.appendChild(h("div", { class: "empty", text: "No blocks placed." }));

    body.appendChild(h("h3", { text: "Available" }));
    var available = ranked.filter(function (row) { return !placed[row.id]; });
    available.forEach(function (row) {
      body.appendChild(drawerItem(row, false, hero ? hero.item.id : null));
    });
    if (!available.length) body.appendChild(h("div", { class: "empty", text: "Every block is placed." }));

    body.appendChild(h("h3", { text: "Composition" }));
    body.appendChild(checkbox(
      "Show the reading order",
      "The numbered strip under the top bar, saying where to start and what follows.",
      State.prefs.reading,
      function (on) {
        State.prefs.reading = on;
        savePrefs();
        renderAll();
        measureTopbar();
      }));
    body.appendChild(h("button", {
      class: "btn", text: "Reset to the default layout",
      onclick: function () {
        State.layout = defaultLayout(makeCtx());
        saveLayout();
        renderAll();
        renderDrawer();
        toast("Default layout restored");
      },
    }));
  }

  function drawerItem(row, placed, heroId) {
    var entry = BY_ID[row.id];
    var isHero = row.id === heroId;
    var reason = row.relevance <= 0
      ? "nothing to show for this run"
      : "relevance " + Math.round(row.relevance * 100) + "%" +
        (row.interest > 0 ? " · you use this" : "");
    return h("div", { class: "drawer-item" }, [
      h("div", { class: "meta" }, [
        h("strong", { text: entry.title + (isHero ? "  ★" : "") }),
        h("span", { text: entry.question || reason }),
        h("div", { class: "bar" }, [h("i", { style: { width: Math.round(row.score * 100) + "%" } })]),
        h("span", { class: "mono", text: reason }),
      ]),
      h("button", {
        class: "btn ghost", text: isHero ? "★" : "☆",
        title: isHero ? "Return this to its column" : "Lead the page with this block",
        onclick: function () {
          if (isHero) demoteHero(); else promoteHero(row.id);
          renderDrawer();
        },
      }),
      h("button", {
        class: "btn", text: placed ? "Remove" : "Add",
        onclick: function () {
          if (placed) { hideBlock(row.id); }
          else { moveBlock(row.id, stackFor(entry), null, true); }
          renderDrawer();
        },
      }),
    ]);
  }

  function stackFor(entry) {
    for (var i = 0; i < STACK_PLAN.length; i++) {
      if (STACK_PLAN[i].groups.indexOf(entry.group) >= 0) return i;
    }
    return 0;
  }

  function renderYou() {
    var body = els.youBody;
    body.innerHTML = "";

    body.appendChild(h("h3", { text: "Who this browser thinks you are" }));
    body.appendChild(h("p", {
      text: "A random id, generated in this browser the first time you opened an " +
            "AgentDiff report. It keeps your layout and your block ordering " +
            "across reports. It is not linked to a name, an account, or a report's contents.",
    }));
    body.appendChild(h("div", { class: "vid", text: Identity.id }));
    body.appendChild(h("p", {
      class: "caveat",
      text: "Stored in: " + Store.backendName() + ". Nothing leaves this browser — " +
            "the page contains no network code, which is why it works offline from a file.",
    }));

    body.appendChild(h("h3", { text: "Personalization" }));
    body.appendChild(checkbox(
      "Rank blocks by what I use",
      "Off, blocks are ordered only by what the loaded run has to say.",
      State.prefs.personalize,
      function (on) {
        State.prefs.personalize = on;
        savePrefs();
        renderAll();
        renderYou();
      }));
    body.appendChild(checkbox(
      "Apply suggestions automatically",
      "Off by default: a layout you arranged yourself is a stronger signal than one inferred from clicks, so reordering is offered rather than done.",
      State.prefs.autoApply,
      function (on) {
        State.prefs.autoApply = on;
        savePrefs();
        renderYou();
      }));

    body.appendChild(h("h3", { text: "What is stored" }));
    var counts = Object.keys(State.signals).length;
    body.appendChild(h("dl", { class: "kv" }, [
      h("dt", { text: "Visitor id" }), h("dd", { text: "1 cookie + 1 local entry" }),
      h("dt", { text: "Layout" }), h("dd", { text: State.layout.stacks.reduce(function (n, s) { return n + s.length; }, 0) + " placed, " + State.layout.hidden.length + " hidden" }),
      h("dt", { text: "Leads the page" }), h("dd", { text: leadSummary() }),
      h("dt", { text: "Interest" }), h("dd", { text: counts + " block" + (counts === 1 ? "" : "s") + " with recorded use" }),
      h("dt", { text: "Report data" }), h("dd", { text: "not stored — it is baked into this file" }),
    ]));

    if (counts) {
      var now = Date.now();
      var rows = Object.keys(State.signals).map(function (id) {
        return { id: id, weight: interestScore(id, now), count: State.signals[id].count };
      }).sort(function (x, y) { return y.weight - x.weight; }).slice(0, 8);
      var table = h("table", { class: "grid" }, [
        h("tr", null, [h("th", { text: "Block" }), h("th", { class: "num", text: "Uses" }), h("th", { class: "num", text: "Weight" })]),
      ]);
      rows.forEach(function (row) {
        table.appendChild(h("tr", null, [
          h("td", { text: BY_ID[row.id] ? BY_ID[row.id].title : row.id }),
          h("td", { class: "num", text: row.count }),
          h("td", { class: "num", text: fmt.num(row.weight, 1) }),
        ]));
      });
      body.appendChild(table);
      body.appendChild(h("p", { class: "caveat", text: "Weights halve every " + HALF_LIFE_DAYS + " days, so old habits stop outranking current work." }));
    }

    body.appendChild(h("h3", { text: "Your data" }));
    body.appendChild(h("button", {
      class: "btn", text: "Forget what I use (keep layout)",
      onclick: function () {
        State.signals = {};
        Store.set(key("signals"), State.signals);
        renderAll();
        renderYou();
        toast("Interest history cleared");
      },
    }));
    body.appendChild(h("div", { style: { height: "8px" } }));
    body.appendChild(h("button", {
      class: "btn", text: "Erase everything and start as a new visitor",
      onclick: function () {
        Store.keys(NS + ":").forEach(function (storedKey) { Store.remove(storedKey); });
        Store.remove(NS + ":vid");
        writeCookie(COOKIE, "", 0);
        toast("Erased. Reloading as a new visitor…");
        setTimeout(function () { global.location.reload(); }, 700);
      },
    }));
  }

  /* What the lane is actually showing, which is not always what was chosen:
   * the panel is a disclosure, so it reports the substitution too. */
  function leadSummary() {
    var hero = resolveHero(makeCtx());
    if (!hero) return "nothing — the lane is hidden";
    var title = BY_ID[hero.item.id].title;
    if (hero.source !== "fallback") return title;
    return title + " (standing in for " +
           (BY_ID[hero.instead] ? BY_ID[hero.instead].title : hero.instead) + ")";
  }

  function checkbox(label, help, checked, onChange) {
    var input = h("input", { type: "checkbox" });
    input.checked = !!checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    return h("label", { class: "check" }, [
      input,
      h("span", null, [h("strong", { text: label }), h("br"), h("span", { class: "caveat", text: help })]),
    ]);
  }

  // ----------------------------------------------------------------- toasts

  var toastTimer = null;
  function toast(message, actionLabel, action) {
    var node = els.toast;
    node.innerHTML = "";
    node.appendChild(h("span", { text: message }));
    if (actionLabel && action) {
      node.appendChild(h("button", {
        text: actionLabel,
        onclick: function () { node.classList.remove("show"); action(); },
      }));
    }
    node.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { node.classList.remove("show"); }, 5000);
  }

  function maybeOfferSuggestion() {
    if (!State.suggestion) return;
    if (State.prefs.autoApply) {
      applySuggestion(true);
      return;
    }
    if (State.suggestion.moved < 2) return;   // not worth interrupting for
    toast("Your layout could be reordered to match what you use", "Reorder", function () {
      applySuggestion(false);
    });
  }

  function applySuggestion(silent) {
    if (!State.suggestion) return;
    State.layout.stacks = State.suggestion.stacks;
    State.suggestion.promote.forEach(function (id) {
      var index = State.layout.hidden.indexOf(id);
      if (index >= 0) State.layout.hidden.splice(index, 1);
      State.layout.stacks[stackFor(BY_ID[id])].unshift({ id: id, collapsed: false });
    });
    State.suggestion = null;
    saveLayout();
    renderAll();
    if (!silent) toast("Reordered. Reset any time from the Blocks panel.");
  }

  // ------------------------------------------------------------------- boot

  function selectTask(id) {
    State.task = id;
    if (els.picker) els.picker.value = id;
    renderAll();
  }

  function applyTheme() {
    var theme = State.prefs.theme;
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
  }

  function boot() {
    Identity.id = visitorId();
    State.data = global.DEEPCOMPARE_DATA || { reports: [], aggregate: {} };

    State.prefs = Object.assign(defaultPrefs(), Store.get(key("prefs")) || {});
    State.signals = Store.get(key("signals")) || {};
    if (typeof State.signals !== "object" || State.signals === null) State.signals = {};

    var reports = State.data.reports || [];
    State.task = reports.length && reports[0].task ? reports[0].task.id : null;

    els = {
      stacks: document.getElementById("stacks"),
      hero: document.getElementById("hero-lane"),
      reading: document.getElementById("reading"),
      picker: document.getElementById("task-picker"),
      drawer: document.getElementById("drawer"),
      drawerBody: document.getElementById("drawer-body"),
      you: document.getElementById("you"),
      youBody: document.getElementById("you-body"),
      scrim: document.getElementById("scrim"),
      toast: document.getElementById("toast"),
      cols: document.getElementById("btn-cols"),
    };

    applyTheme();
    measureTopbar();
    global.addEventListener("resize", measureTopbar);
    State.layout = reconcile(Store.get(key("layout")), makeCtx());

    // Task picker
    if (reports.length) {
      reports.forEach(function (report) {
        var id = report && report.task ? report.task.id : null;
        if (!id) return;
        var failed = report.attribution && report.attribution.failed_agent;
        els.picker.appendChild(h("option", { value: id, text: id + (failed ? "  ✗" : "") }));
      });
      els.picker.value = State.task;
      els.picker.addEventListener("change", function () { selectTask(els.picker.value); });
    } else {
      els.picker.appendChild(h("option", { text: "no reports loaded" }));
      els.picker.disabled = true;
    }

    document.getElementById("btn-drawer").addEventListener("click", function () {
      renderDrawer();
      openPanel(els.drawer);
    });
    document.getElementById("btn-you").addEventListener("click", function () {
      renderYou();
      openPanel(els.you);
    });
    document.getElementById("btn-theme").addEventListener("click", function () {
      var order = ["system", "light", "dark"];
      State.prefs.theme = order[(order.indexOf(State.prefs.theme) + 1) % order.length];
      savePrefs();
      applyTheme();
      renderAll();
      toast("Theme: " + State.prefs.theme);
    });
    els.cols.addEventListener("click", function () {
      State.layout.cols = State.layout.cols >= STACK_PLAN.length ? 1 : State.layout.cols + 1;
      saveLayout();
      renderAll();
    });
    els.scrim.addEventListener("click", closePanels);
    var closers = document.querySelectorAll("[data-close]");
    for (var i = 0; i < closers.length; i++) closers[i].addEventListener("click", closePanels);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closePanels();
    });

    renderAll();

    if (Identity.minted) {
      toast("New visitor id created — see “You” for what is stored", "You", function () {
        renderYou();
        openPanel(els.you);
      });
    } else if (!Store.durable) {
      toast("Storage is unavailable here; your layout lasts only this tab");
    }
  }

  /* Panels open below the bar rather than over it, so the bar's other
     buttons stay reachable while one is open. The height is measured
     because it follows the font size rather than a fixed number. */
  function measureTopbar() {
    var bar = document.querySelector(".topbar");
    if (!bar) return;
    document.documentElement.style.setProperty("--topbar-h", bar.offsetHeight + "px");
  }

  function openPanel(panel) {
    closePanels();
    panel.classList.add("open");
    els.scrim.classList.add("open");
  }

  function closePanels() {
    els.drawer.classList.remove("open");
    els.you.classList.remove("open");
    els.scrim.classList.remove("open");
  }

  global.AgentDiff = {
    block: block,
    boot: boot,
    // exposed for the smoke test, not for block modules
    _internals: {
      rank: rank, reconcile: reconcile, defaultLayout: defaultLayout,
      resolveHero: resolveHero, promoteHero: promoteHero, demoteHero: demoteHero,
      DEFAULT_HERO: DEFAULT_HERO,
      State: State, REGISTRY: REGISTRY, BY_ID: BY_ID, fmt: fmt, uuid: uuid,
      decay: decay, Store: Store, STACK_PLAN: STACK_PLAN,
    },
  };
})(typeof window !== "undefined" ? window : this);
