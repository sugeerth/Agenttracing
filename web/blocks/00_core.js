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
  var OPEN_PER_STACK = 2;
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
  var DEFAULT_HERO = "trajectory-map";

  //: Story mode: after the hero, these blocks follow at full width in this
  //: order — the reading of the failing run, the adjudicated diagnosis,
  //: what to do next, the step under the cursor, what the difference cost.
  //: They answer the reader's next five questions; everything else stays in
  //: the columns. Dashboard mode puts them back.
  var STORY_BLOCKS = ["reading", "trace-tree", "diagnosis", "reconcile", "take-forward", "actions", "deltas"];
  function isStoryBlock(id) { return State.prefs && State.prefs.view === "story" && STORY_BLOCKS.indexOf(id) >= 0; }

  //: the three views. Story: lead + hero + the story lane. Evidence: the
  //: per-task columns (outcome, trajectory, integrity). Batch: the
  //: cross-task columns (cost, signal, other) — they do not change with the
  //: task and do not deserve to scroll past on every task page.
  var VIEWS = ["story", "evidence", "batch"];
  var VIEW_GROUPS = {
    evidence: ["outcome", "trajectory", "integrity"],
    batch: ["cost", "signal", "other"],
  };
  function stacksForView(view) {
    var groups = VIEW_GROUPS[view];
    if (!groups) return [];
    var out = [];
    STACK_PLAN.forEach(function (plan, index) {
      if (plan.groups.some(function (g) { return groups.indexOf(g) >= 0; })) out.push(index);
    });
    return out;
  }

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
      // lead blocks open the page in their own lane; see renderLead
      lead: spec.lead === true,
      // the section name the story view numbers ("1 · What happened")
      storyTitle: typeof spec.storyTitle === "string" ? spec.storyTitle : null,
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
    return { personalize: true, autoApply: false, theme: "system", reading: true, view: "story" };
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
      // Lead blocks live in the lead lane, never in a column; story blocks
      // live in the story lane while Story mode is on.
      if (entry.lead) return;
      if (isStoryBlock(entry.id)) return;
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

  //: part id → composite id: a block folded into a composite card leaves
  //: the layout (it stays in the drawer, and renders inside its composite)
  var COMPOSED = {};

  function safeRelevance(entry, ctx, raw) {
    if (!entry) return 0;
    if (!raw && COMPOSED[entry.id] && BY_ID[COMPOSED[entry.id]]) return 0;
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
      explain: function (el, def) { return Explain.attach(el, def); },
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
    try { document.body.setAttribute("data-view", State.prefs.view); } catch (err) { /* no body */ }
    renderTaskStrip(ctx);
    renderTitle(ctx);
    renderLead(ctx);
    renderHero(hero, ctx);
    renderStory(ctx, hero);
    renderReading(hero);

    els.stacks.innerHTML = "";
    var visibleStacks = stacksForView(State.prefs.view);
    els.stacks.hidden = visibleStacks.length === 0;
    els.stacks.setAttribute("data-cols", Math.min(State.layout.cols, Math.max(1, visibleStacks.length)));
    if (els.cols) els.cols.hidden = visibleStacks.length === 0;
    syncTabs();

    State.layout.stacks.forEach(function (stack, index) {
      if (visibleStacks.indexOf(index) < 0) return;
      var column = h("div", { class: "stack", "data-stack": index });
      column.appendChild(stackLabel(index, hero ? 2 : 1));
      stack.forEach(function (item) {
        // A substituted hero is still in its column; render it once.
        if (hero && item.id === hero.item.id) return;
        // A lead block found in a stored layout renders in its lane only;
        // so does a story block while Story mode is on.
        if (BY_ID[item.id] && BY_ID[item.id].lead) return;
        if (isStoryBlock(item.id)) return;
        column.appendChild(renderBlock(item, index, ctx, null));
      });
      wireStackDrop(column, index);
      els.stacks.appendChild(column);
    });

    els.cols.textContent = "▥ " + State.layout.cols;
    // A rebuilt page has new term elements (which need their tabindex) and
    // may have removed the one a tooltip was pinned to.
    Explain.close();
    Explain.sweep(document.body);
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
  /* The task strip: one chip per task in the batch — both outcomes as
   * dots, the decisive step as a number — the whole batch at a glance,
   * and the current task marked. Clicking switches the task. */
  function renderTaskStrip(ctx) {
    var host = els.strip;
    if (!host) return;
    host.innerHTML = "";
    var reports = (State.data.reports || []).filter(function (r) { return r && r.task; });
    if (reports.length < 2) { host.hidden = true; return; }
    host.hidden = false;
    var ids = reports.map(function (r) { return r.task.id; });
    var at = ids.indexOf(State.task);
    host.appendChild(h("button", {
      class: "tstrip-nav", type: "button", text: "‹", title: "previous task  [",
      "aria-label": "previous task", disabled: at <= 0 ? "disabled" : null,
      onclick: function () { if (at > 0) selectTask(ids[at - 1]); },
    }));
    reports.forEach(function (report) {
      var id = report.task.id;
      var oa = (report.a && report.a.outcome) || {}, ob = (report.b && report.b.outcome) || {};
      var dec = report.diagnosis && report.diagnosis.decisive_step;
      var step = dec && dec.step !== null && dec.step !== undefined ? dec.step : null;
      var label = String(id).replace(/^t\d+_/, "").replace(/_/g, " ");
      var chip = h("button", {
        class: "tchip", type: "button", "aria-current": id === State.task ? "true" : "false",
        title: id + " — " + (report.a.agent.name + (oa.success ? " solved" : " failed")) + ", "
             + (report.b.agent.name + (ob.success ? " solved" : " failed"))
             + (step !== null ? "; decisive step " + step : ""),
        onclick: function () { selectTask(id); },
      }, [
        h("span", { class: "dots" }, [
          h("i", { class: "dot a" + (oa.success ? "" : " failed") }),
          h("i", { class: "dot b" + (ob.success ? "" : " failed") }),
        ]),
        h("span", { text: label }),
        step !== null ? h("span", { class: "step", text: "@" + step }) : null,
      ]);
      host.appendChild(chip);
    });
    host.appendChild(h("button", {
      class: "tstrip-nav", type: "button", text: "›", title: "next task  ]",
      "aria-label": "next task", disabled: at >= ids.length - 1 ? "disabled" : null,
      onclick: function () { if (at < ids.length - 1) selectTask(ids[at + 1]); },
    }));
    host.appendChild(h("span", { class: "tstrip-hint", text: "[ ] switch task", "aria-hidden": "true" }));
    var current = host.querySelector('[aria-current="true"]');
    if (current && current.scrollIntoView) {
      try { current.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (err) { /* fine */ }
    }
  }

  function stepTask(delta) {
    var ids = (State.data.reports || []).filter(function (r) { return r && r.task; })
      .map(function (r) { return r.task.id; });
    var at = ids.indexOf(State.task);
    var next = ids[at + delta];
    if (next) selectTask(next);
  }

  /* The story lane: Story mode's ordered sequence after the hero. Each
   * block keeps its ordinary controls (collapse, star, remove) and its
   * collapse state persists in the layout under `story`. */
  function syncTabs() {
    if (!els.tabs) return;
    var tabs = els.tabs.querySelectorAll("[role=tab]");
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute("data-view") === State.prefs.view;
      tabs[i].setAttribute("aria-selected", on ? "true" : "false");
      tabs[i].tabIndex = on ? 0 : -1;
    }
  }

  function setView(view) {
    if (VIEWS.indexOf(view) < 0 || view === State.prefs.view) return;
    var wasStory = State.prefs.view === "story";
    State.prefs.view = view;
    savePrefs();
    if (view === "story" && !wasStory) {
      STORY_BLOCKS.forEach(function (id) { if (BY_ID[id]) removeFromLayoutKeepHidden(id); });
    } else if (view !== "story" && wasStory) {
      STORY_BLOCKS.forEach(function (id) {
        if (BY_ID[id] && State.layout.hidden.indexOf(id) < 0) placeBack({ id: id, collapsed: false });
      });
    }
    saveLayout();
    renderAll();
    try { window.scrollTo({ top: 0, behavior: "instant" }); } catch (err) { /* fine */ }
  }

  function renderStory(ctx, hero) {
    var host = els.story;
    if (!host) return;
    host.innerHTML = "";
    if (State.prefs.view !== "story") { host.hidden = true; return; }
    if (!Array.isArray(State.layout.story)) {
      State.layout.story = STORY_BLOCKS.map(function (id) { return { id: id, collapsed: false }; });
    }
    var shown = 0;
    State.layout.story.forEach(function (item) {
      var entry = BY_ID[item.id];
      if (!entry) return;
      if (State.layout.hidden.indexOf(item.id) >= 0) return;
      if (hero && hero.item.id === item.id) return;
      if (safeRelevance(entry, ctx) <= 0) return;
      var card = renderBlock(item, null, ctx, null);
      shown++;
      // the story is numbered and titled as a sequence — what happened,
      // why, take forward — so the reader always knows where they are
      var title = card.querySelector(".block-title");
      if (title) title.textContent = shown + " · " + (entry.storyTitle || entry.title);
      host.appendChild(card);
    });
    host.hidden = shown === 0;
  }

  function removeFromLayoutKeepHidden(id) {
    State.layout.stacks.forEach(function (stack) {
      for (var i = stack.length - 1; i >= 0; i--) {
        if (stack[i].id === id) stack.splice(i, 1);
      }
    });
  }

  /* The page's one <h1>: the task under comparison, so a screen reader
   * and a skim both start from what was asked. */
  function renderTitle(ctx) {
    var host = els.title;
    if (!host) return;
    var task = ctx.report && ctx.report.task;
    var prompt = task && (task.prompt || task.id);
    host.textContent = prompt ? String(prompt) : "AgentDiff report";
    host.hidden = false;
  }

  /* The lead lane: every block registered with `lead: true` that has
   * something to say, in registration order, full width, above the hero.
   * No star, no collapse, no remove — a lead block is the page's opening
   * sentence, not a card in the layout. */
  function renderLead(ctx) {
    var host = els.lead;
    if (!host) return;
    host.innerHTML = "";
    var shown = 0;
    REGISTRY.forEach(function (entry) {
      if (!entry.lead || safeRelevance(entry, ctx) <= 0) return;
      var body = h("div", { class: "block-body" });
      var card = h("div", { class: "block lead", "data-block": entry.id }, [
        h("div", { class: "block-head" }, [
          h("div", { class: "block-title", text: entry.title }),
          entry.question ? h("div", { class: "block-q", text: entry.question }) : null,
        ]),
        body,
      ]);
      try {
        entry.render(body, ctx);
      } catch (err) {
        body.innerHTML = "";
        body.appendChild(h("div", { class: "empty", text: "This block could not render: " + String(err && err.message || err) }));
      }
      accessibleCharts(body, entry);
      host.appendChild(card);
      shown++;
    });
    host.hidden = shown === 0;
  }

  /* Every chart is an image to assistive technology: a root <svg> that
   * a block drew without a role gets role="img", an accessible name from
   * the block's title, and a <title> child — the block's own <title>s and
   * aria-hidden glyphs are left as they are. */
  function accessibleCharts(body, entry) {
    var svgs;
    try { svgs = body.querySelectorAll("svg"); } catch (err) { return; }
    for (var i = 0; i < svgs.length; i++) {
      var node = svgs[i];
      if (node.getAttribute("role") || node.getAttribute("aria-hidden") === "true") continue;
      if (node.parentNode && node.parentNode.closest && node.parentNode.closest("svg")) continue;
      node.setAttribute("role", "img");
      var name = entry.title + (entry.question ? " — " + entry.question : "");
      if (!node.getAttribute("aria-label")) node.setAttribute("aria-label", name);
      var hasTitle = false;
      for (var k = 0; k < node.childNodes.length; k++) {
        if (node.childNodes[k].nodeName === "title") { hasTitle = true; break; }
      }
      if (!hasTitle) {
        var title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = name;
        node.insertBefore(title, node.firstChild);
      }
    }
  }

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
    // the story lane IS the reading order; the strip guides the columns
    if (!State.prefs.reading || State.prefs.view === "story") { host.hidden = true; return; }
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
    var visible = stacksForView(State.prefs.view);
    visible.forEach(function (index, position) {
      var plan = STACK_PLAN[index];
      host.appendChild(readingChip(position + base, plan.label, null, false, function () {
        scrollTo_(els.stacks.children[position]);
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
      // where the block is being drawn: "story" (the one-column narrative),
      // "hero", or "stack" — a block may show less in the story
      blockCtx.lane = hero ? "hero" : (stackIndex === null || stackIndex === undefined) ? "story" : "stack";
      try {
        entry.render(body, blockCtx);
        accessibleCharts(body, entry);
      } catch (err) {
        // One broken block must not take the page with it.
        console.error("AgentDiff: block", item.id, "failed to render", err);
        body.innerHTML = "";
        body.appendChild(h("div", { class: "empty", text: "This block failed to render: " + err.message }));
      }
      body.addEventListener("click", function () { recordSignal(item.id, "inspect"); }, { once: true });
      body.addEventListener("mouseenter", function () { recordSignal(item.id, "hover"); }, { once: true });
      // the hero is the page's picture: never clamped, its inspector included
      if (!hero) clampIfTall(card, body, item);
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
    if (State.visits < 2) return;             // never on the first visit
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

  // ----------------------------------------------------- explanations

  /* The glossary. One entry per term of art the board already uses, written
   * for someone who has never read the docs: `short` is the one-sentence
   * answer, `long` the honest paragraph behind it. The honesty framing of
   * the engine is preserved on purpose — where a number means something
   * narrower than it looks (pass^k, omega squared, the counterfactual),
   * the entry says so rather than smoothing it over. */
  var TERMS = {
    "divergence": {
      label: "divergence",
      short: "The place where the two runs stopped doing the same thing — the first real fork in the road.",
      long: "AgentDiff lays the two runs side by side, step against step. A divergence is a region where that pairing breaks down: one agent takes steps the other never mirrors, or the paired steps stop resembling each other. Each divergence is ranked by how much it mattered downstream — extra steps, extra tokens, and whether the run that took the detour went on to fail. The first-ranked divergence is usually the decision worth reading, because everything after it happened in its shadow.",
    },
    "root-cause": {
      label: "root cause",
      short: "The single step the engine holds responsible for the failure — where the losing run first went wrong.",
      long: "When exactly one run fails, the engine walks back from the wrong answer through the run's own steps to the earliest divergent step that plausibly set it up — a bad source selected, a wrong tool called, a plan that skipped a check. That step is the attributed root cause, and the chain lists the later steps that inherited its mistake. It is an attribution from logged evidence, not a proof: the engine reads what was recorded, it does not re-run the agent to confirm.",
    },
    "propagation": {
      label: "propagation",
      short: "How much of the root mistake's content a later step carries forward.",
      long: "Once a run picks up a wrong fact, the interesting question is whether it ever puts it down. Propagation measures, for each later step, how much of the root divergent step's output shows up again in that step's input — a word-overlap score between the two texts. A high score means the step is still working with the contaminated material; a chain of high scores is a mistake compounding, rather than a mistake made once and recovered from.",
    },
    "alignment-row": {
      label: "alignment row",
      short: "One column of the side-by-side view: a step from run A matched to its counterpart in run B.",
      long: "The two runs rarely have the same number of steps, so before anything is compared they are aligned: each row pairs the A step and the B step that most plausibly correspond, in order. A row can hold both sides (a match, or a drift when they differ), or only one side — when an agent did something the other never did at all. Everything else on this board — the divergences, the tracks picture, the step detail — is indexed by these row numbers.",
    },
    "drift": {
      label: "drift",
      short: "A row where both agents did something comparable, but not the same — the wording or the target slid.",
      long: "Drift sits between a clean match and a full divergence. Both runs have a step in the row and the steps are recognisably the same kind of move, but the similarity is low: the same search with a different query, the same read on a different page. Drift is often where trouble starts — the runs still look parallel from a distance while their contents quietly part — which is why the first drifting rows are worth reading even when the headline divergence comes later.",
    },
    "pass-k": {
      label: "pass^k vs pass@k",
      short: "pass^k asks “does it work every time”; pass@k asks “can it work at all” — reliability versus coverage.",
      long: "With several runs per task, two very different questions hide in the success counts. pass@k rises with k: the chance that at least one of k attempts succeeds — useful when a human will pick the good run, and flattering by construction. pass^k falls with k: the chance that all k attempts succeed — the number that matters when nobody is checking and the agent has to be right three times running. Passing 2 of 3 runs is not “67% reliable”; read strictly, pass^3 there is 0. This board never draws one curve without the other, because quoting pass@k where reliability is at stake overstates the system.",
    },
    "omega-squared": {
      label: "omega squared & the chance floor",
      short: "A bias-corrected share of variance explained, read against the share a factor earns by chance just for having levels.",
      long: "Raw “variance explained” flatters factors with many levels: a 33-level factor explains a chunk of any finite corpus by chance alone, before any real effect exists. That chance floor is drawn on the same axis as the estimate, and omega squared (ω²) is the bias-corrected figure that subtracts the flattery. When ω² sits at or below zero, the honest statement is “indistinguishable from chance”, and the board prints exactly that instead of a bar — a one-pixel positive bar would be a lie about the sign.",
    },
    "confounded": {
      label: "confounded design",
      short: "The corpus cannot tell two factors apart, because they always change together — every model came with its own harness.",
      long: "When each model appears with exactly one harness (or one prompt, or one version), model and harness are the same partition of the data: every difference credited to one could equally be credited to the other. A confounded design does not make the numbers wrong — it makes attribution between the confounded factors meaningless, because any split shown would be an artefact of the order the arithmetic was done in. The fix is in the data, not the math: run at least one model on more than one harness.",
    },
    "residual": {
      label: "residual",
      short: "The variance left over after every measured factor — only safely called “noise” when there are repeated runs.",
      long: "After model, harness and task have taken their shares, what remains is the residual. With repeated runs per configuration it is a measured thing: the same setup coming out differently on a re-run — genuine run-to-run stochasticity. With a single run per cell there is no repeat to measure against, so the residual is interaction and stochasticity fused inseparably: the design cannot say how much is “this combination behaves specially” versus “this run happened to go this way”. The board draws those two cases with different marks rather than letting one impersonate the other.",
    },
    "blind-write": {
      label: "blind write",
      short: "A write to the world before any successful read — acting on state the run never actually looked at.",
      long: "A cancel, a booking, an update is a write; checking what is there first is a read. A blind write is a write that happens before any read has succeeded in that run — the agent changed something whose current state it never established. Three failed lookups followed by the write still count: only a read that worked counts as having looked. Blind writes are invisible to outcome-only grading whenever the guess happens to be right, which is exactly why they are flagged from the trace itself.",
    },
    "false-success": {
      label: "false success",
      short: "The answer claims the job was done while the trace shows nothing was ever written.",
      long: "A run can end with “Done — the booking is cancelled” while its own steps contain no successful write of any kind. The claim and the log disagree, and the log wins. False success is the most dangerous shape of failure, because an outcome oracle that trusts the answer text scores it as a pass. It is detected here by comparing what the final answer asserts against the side effects the trace actually records — a deterministic check, no judge involved.",
    },
    "no-information-step": {
      label: "no-information step",
      short: "A step whose observation is byte-identical to one the run already had — motion without progress.",
      long: "The call was new — a different moment, maybe a different phrasing — but the observation that came back is byte-for-byte something the run had already seen. The step advanced nothing: no new evidence, no changed state, just spend. A few of these are friction; a run of them is a loop in slow motion. They are counted separately from retries, because retrying a failed call is correct behaviour and should not be penalised as churn.",
    },
    "wilson-interval": {
      label: "Wilson interval",
      short: "A confidence interval for a success rate that stays honest at the extremes where small eval suites live.",
      long: "Quoting “2 failures out of 8 tasks” as exactly 25% overstates what 8 tasks can say. The Wilson score interval puts a range around a proportion, and unlike the schoolbook normal approximation it behaves at 0-of-8 and 8-of-8 — precisely where short suites sit. When this board quotes a rate on few tasks, the bracket after it is the Wilson 95% interval: the range of true rates that could plausibly have produced what was observed.",
    },
    "oracle": {
      label: "oracle",
      short: "A ceiling computed with hindsight — what perfect per-task routing would have achieved. A bound, not a policy.",
      long: "The routing oracle assumes you knew, for every task in advance, which agent would succeed, and always picked it. Its coverage and cost are therefore a ceiling: no real router can beat it, and the gap between the best single agent and the oracle is the headroom a router could win — and no more. It is reported to size that headroom, never as a claim that any deployable policy reaches it. The word also names the grader that decides success; a wrong grading oracle is what “failed but clean” runs point at.",
    },
    "passed-but-pathological": {
      label: "passed but pathological",
      short: "The outcome oracle says pass, but the trace shows loops, swallowed errors, or blind writes on the way there.",
      long: "A run can satisfy its success check while doing things nobody would accept on inspection: cycling through the same calls, hitting an error and pressing on as if it had not, writing before reading. A leaderboard that scores outcomes only gives this run the same mark as a clean one — and published measurements put a large share of safety and robustness failures in exactly this blind spot. The verdict exists so a pass with a pathological process is never silently indistinguishable from a pass earned cleanly.",
    },
    "failed-but-clean": {
      label: "failed but clean",
      short: "The run failed with nothing visibly wrong in its process — which is evidence about the grader, not only the agent.",
      long: "When a run fails its success check and the trace shows no loop, no unrecovered error, no blind write and a coherent path to a defensible answer, suspicion should fall on the oracle as much as on the agent: an expected-answer string that is too strict, a check that keys on wording, a gold label that is simply wrong. “Failed but clean” does not assert the agent was right — it flags that the deterministic evidence does not corroborate the failure, and that a human should look at the grading before the agent is blamed.",
    },
    "tokens-basis": {
      label: "tokens: measured vs estimated",
      short: "Whether a token count was reported by the provider (measured) or derived from text length (estimated).",
      long: "Not every runtime reports token usage, so some counts are estimates — typically length-of-text divided by four — and the difference matters the moment costs or deltas are compared. Each step can carry a tokens_basis of “measured” or “estimated”, and the run-level accounting says how much of the total rests on which. The basis is carried through save and load deliberately: an estimate that loses its label becomes indistinguishable from a provider-reported number, and every comparison downstream inherits the confusion. An absent basis stays absent rather than defaulting to “measured”.",
    },
    "splice-counterfactual": {
      label: "splice counterfactual",
      short: "A what-if assembled by splicing steps both runs actually recorded — never a simulation of the agent.",
      long: "“Had the loser made the winner's decision at the divergence” is estimated by grafting the winner's actually-recorded post-divergence steps onto the loser's actually-recorded prefix, then summing the real tokens, latency and cost of those steps. Nothing is simulated: no step in the estimate is invented, no model is re-run. That makes the arithmetic exact but the premise approximate — it assumes the graft point is clean, which is why the estimate carries a confidence level based on how identical the shared prefix really was. A true causal answer would require re-executing the agent, which a tool that only reads logs deliberately does not do.",
    },
    "shapley-share": {
      label: "Shapley share",
      short: "A fair division of the winner–loser gap across the divergences, so overlapping detours are not double-counted.",
      long: "When a run goes wrong in more than one place, summing each divergence's downstream cost double-counts — later detours inherit the extra work earlier ones created. The Shapley allocation treats each divergence region as a player, asks what adopting the other run's path at every subset of regions would have saved, and divides the total gap so the shares sum to it exactly (that efficiency property is checked numerically on every report). The honest name is splice-Shapley: “fixing” a region means adopting the other run's recorded steps there, so the allocation is exact with respect to that splice surrogate — not with respect to re-running the agent, which logged traces cannot support.",
    },
    "hypothesis-status": {
      label: "hypothesis status",
      short: "Where each candidate explanation stands after adjudication: leading, plausible, weak, ruled out, merged into the leading account, or untestable from this trace.",
      long: "The diagnosis scores every candidate explanation against the report's own evidence and then says where each one landed, rather than showing only the winner. “Leading” clears the runner-up by the lead margin; “plausible” scored but did not clear it; “weak” has little support; “ruled out” is contradicted by the evidence and is struck through, not deleted. “Merged” means the hypothesis describes the same mechanism as the leading one and was absorbed into its account — it stays on the list, muted, so the absorption is visible. “Untestable” means the trace lacks the data to score it at all, and it shows no score rather than an invented one.",
    },
    "discriminator": {
      label: "discriminator",
      short: "The concrete check that would confirm or refute this hypothesis — what evidence would change the diagnosis.",
      long: "Every hypothesis carries a discriminator: the specific action — re-grade the answer by hand, check a claim at its origin step, splice the other agent's decision and re-run — that would settle whether it is right. It is there because a diagnosis that cannot say what evidence would change it is a story, not a diagnosis. When the verdict is contested, the discriminators are the way out: run them, and the evidence they produce picks the cause the scores could not.",
    },
    "contested-diagnosis": {
      label: "contested diagnosis",
      short: "No hypothesis clears the runner-up by the lead margin, so the report declines to pick a single cause.",
      long: "When two or more explanations fit the evidence about equally well, the honest verdict is that the comparison cannot adjudicate between them — not whichever happened to score fractionally higher. A contested diagnosis names the hypotheses that are within the lead margin of each other and points at each one's discriminator: the check that would separate them. Treat it as a to-do list for the next run, not as a failure of the analysis.",
    },
    "mast-trail": {
      label: "MAST & TRAIL",
      short: "Two published failure taxonomies this tool maps its findings onto — by method, never as a claimed measurement.",
      long: "MAST (arXiv 2503.13657) and TRAIL (arXiv 2505.08638) are community taxonomies of how agent systems fail, built from expert-annotated trajectories. AgentDiff labels its own deterministic findings with the closest MAST and TRAIL categories so results can be discussed in a shared vocabulary — but the mapping is by definition of method, not a validated measurement in either framework, and some of their categories (multi-agent conversation failures, for instance) are unreachable from a single pairwise trace. The labels situate a finding; they do not certify it.",
    },
  };

  /* The shared tooltip: one element, reused for every term on the page.
   *
   * Hover shows it, keyboard focus shows it (the wiring gives every
   * data-explain element a tabindex), a click pins it so its text can be
   * selected, Escape closes it, and “Show more” expands the one-sentence
   * answer into the paragraph. position: fixed clamps inside the viewport,
   * so it can never widen the page at any width. */
  var Explain = (function () {
    var TIP_CLASS = "explain-tip";
    var TIP_DOM_ID = "agentdiff-explain";
    var tip = null;
    var current = null;
    var pinned = false;
    var hideTimer = null;
    var overrides = typeof WeakMap === "function" ? new WeakMap() : null;
    var overrideList = overrides ? null : [];   // ancient-browser fallback

    function overrideFor(el) {
      if (overrides) return overrides.get(el) || null;
      for (var i = 0; i < overrideList.length; i++) {
        if (overrideList[i][0] === el) return overrideList[i][1];
      }
      return null;
    }

    function setOverride(el, def) {
      if (overrides) overrides.set(el, def);
      else overrideList.push([el, def]);
    }

    function defFor(el) {
      var term = el.getAttribute("data-explain") || "";
      var base = TERMS[term] || null;
      var over = overrideFor(el);
      if (!base && !over) return null;
      return {
        term: term,
        label: (over && over.label) || (base && base.label) || term.replace(/-/g, " "),
        short: (over && over.short) || (base && base.short) || "",
        long: (over && over.long) || (base && base.long) || "",
        evidence: (over && over.evidence) || null,
      };
    }

    function ensureTip() {
      if (tip && tip.isConnected) return tip;
      tip = h("div", { class: TIP_CLASS, role: "tooltip" });
      tip.setAttribute("id", TIP_DOM_ID);
      tip.addEventListener("mouseenter", cancelHide);
      tip.addEventListener("mouseleave", function () { if (!pinned) scheduleHide(); });
      document.body.appendChild(tip);
      return tip;
    }

    function buildContent(def) {
      tip.innerHTML = "";
      tip.appendChild(h("div", { class: "x-label", text: def.label }));
      if (def.short) tip.appendChild(h("div", { class: "x-short", text: def.short }));
      var hasLong = def.long && def.long !== def.short;
      if (hasLong) tip.appendChild(h("div", { class: "x-long", text: def.long }));
      if (def.evidence) tip.appendChild(h("div", { class: "x-ev", text: def.evidence }));
      var foot = h("div", { class: "x-foot" });
      if (hasLong) {
        var more = h("button", { class: "x-more", text: "Show more", type: "button" });
        more.addEventListener("click", function (event) {
          event.stopPropagation();
          var expanded = tip.classList.toggle("expanded");
          more.textContent = expanded ? "Show less" : "Show more";
          if (current) position(current);
        });
        foot.appendChild(more);
      }
      foot.appendChild(h("span", {
        class: "x-hint",
        text: pinned ? "pinned — Esc closes" : "click to pin · Esc closes",
      }));
      tip.appendChild(foot);
    }

    function position(target) {
      if (!tip || !target || !target.getBoundingClientRect) return;
      var box = target.getBoundingClientRect();
      var vw = document.documentElement.clientWidth || global.innerWidth || 360;
      var vh = document.documentElement.clientHeight || global.innerHeight || 640;
      // Measure at origin first so a previous position cannot squash the
      // tooltip against an edge and distort its natural size.
      tip.style.left = "0px";
      tip.style.top = "0px";
      var w = tip.offsetWidth;
      var ht = tip.offsetHeight;
      var x = Math.min(Math.max(10, box.left), Math.max(10, vw - w - 10));
      var y = box.bottom + 8;
      if (y + ht > vh - 8) y = box.top - ht - 8;   // flip above
      if (y < 8) y = Math.max(8, Math.min(vh - ht - 8, box.bottom + 8));
      tip.style.left = Math.round(x) + "px";
      tip.style.top = Math.round(y) + "px";
    }

    function clearTarget() {
      if (current) {
        try { current.removeAttribute("aria-describedby"); } catch (err) {}
      }
      current = null;
    }

    function show(el, pin) {
      var def = defFor(el);
      if (!def) return false;
      ensureTip();
      cancelHide();
      if (current && current !== el) clearTarget();
      current = el;
      pinned = !!pin;
      buildContent(def);
      tip.classList.add("show");
      tip.classList.toggle("pinned", pinned);
      tip.classList.remove("expanded");
      el.setAttribute("aria-describedby", TIP_DOM_ID);
      position(el);
      return true;
    }

    function hide(force) {
      if (pinned && !force) return false;
      cancelHide();
      if (tip) {
        tip.classList.remove("show", "pinned", "expanded");
      }
      clearTarget();
      pinned = false;
      return true;
    }

    //: hide on a short delay, so the pointer can travel from the term into
    //: the tooltip (to reach "Show more") without the tooltip vanishing.
    function scheduleHide() {
      cancelHide();
      hideTimer = setTimeout(function () { hideTimer = null; hide(false); }, 140);
    }

    function cancelHide() {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    }

    //: true when something was open — the caller uses it to decide whether
    //: Escape has done its job or should fall through to closing panels.
    function close() {
      var wasOpen = !!(tip && tip.classList.contains("show"));
      hide(true);
      return wasOpen;
    }

    function isOpen() { return !!(tip && tip.classList.contains("show")); }

    function focusable(el) {
      var tag = (el.tagName || "").toUpperCase();
      if (tag === "BUTTON" || tag === "A" || tag === "INPUT" ||
          tag === "SELECT" || tag === "TEXTAREA") return true;
      return el.hasAttribute("tabindex");
    }

    /* Keyboard reachability. A span with data-explain is not focusable by
     * itself, and a tooltip that only hover can open is not accessible; so
     * after every render the new targets get a tabindex. */
    function sweep(root) {
      if (!root || !root.querySelectorAll) return;
      var nodes = root.querySelectorAll("[data-explain]");
      for (var i = 0; i < nodes.length; i++) {
        if (!focusable(nodes[i])) nodes[i].setAttribute("tabindex", "0");
      }
    }

    /* ctx.explain(el, {term, short, long, evidence}) — mark an element as
     * explainable. A bare {term} uses the shared glossary; short/long/
     * evidence override or extend it for this one element. */
    function attach(el, def) {
      if (!el || !el.setAttribute) return el;
      def = def || {};
      if (def.term) el.setAttribute("data-explain", def.term);
      else if (!el.getAttribute("data-explain")) el.setAttribute("data-explain", "custom");
      if (def.short || def.long || def.evidence || def.label) setOverride(el, def);
      if (!focusable(el)) el.setAttribute("tabindex", "0");
      return el;
    }

    function closestExplain(node) {
      while (node && node.getAttribute) {
        if (node.getAttribute("data-explain")) return node;
        node = node.parentNode;
      }
      return null;
    }

    function noPin(el) { return el.hasAttribute("data-explain-nopin"); }

    var wired = false;
    function wire() {
      if (wired) return;
      wired = true;
      // Delegated, so terms rendered later (every block re-render) need no
      // per-element listeners and no observer.
      document.addEventListener("mouseover", function (event) {
        var target = closestExplain(event.target);
        if (!target) return;
        cancelHide();
        if (pinned) return;   // a pinned tooltip is not stolen by a hover
        if (current !== target || !isOpen()) show(target, false);
      });
      document.addEventListener("mouseout", function (event) {
        var target = closestExplain(event.target);
        if (target && target === current && !pinned) scheduleHide();
      });
      document.addEventListener("focusin", function (event) {
        if (pinned) return;
        var target = closestExplain(event.target);
        if (target) show(target, false);
      });
      document.addEventListener("focusout", function (event) {
        if (pinned || !current) return;
        var to = event.relatedTarget;
        if (to && tip && tip.contains(to)) return;
        if (to && closestExplain(to) === current) return;
        scheduleHide();
      });
      // Click pins (and on touch, where there is no hover, this is also how
      // the tooltip opens). Clicking anywhere else closes a pinned tooltip.
      document.addEventListener("click", function (event) {
        if (tip && tip.contains(event.target)) return;
        var target = closestExplain(event.target);
        if (target && !noPin(target)) {
          if (current === target && pinned) hide(true);
          else show(target, true);
          return;
        }
        if (pinned) hide(true);
      });
      document.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") return;
        var target = closestExplain(event.target);
        if (!target || noPin(target)) return;
        var tag = (target.tagName || "").toUpperCase();
        if (tag === "BUTTON" || tag === "A") return;
        event.preventDefault();
        if (current === target && pinned) hide(true);
        else show(target, true);
      });
      // The page scrolls under a fixed tooltip; follow the target, and let
      // go of a target a re-render has removed.
      var follow = function () {
        if (!isOpen() || !current) return;
        if (!current.isConnected) { hide(true); return; }
        position(current);
      };
      global.addEventListener("scroll", follow, true);
      global.addEventListener("resize", follow);
    }

    return { attach: attach, sweep: sweep, wire: wire, close: close,
             show: show, hide: hide, isOpen: isOpen, defFor: defFor };
  })();

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
    if (State.prefs.story === false && !(Store.get(key("prefs")) || {}).view) State.prefs.view = "evidence";
    if (VIEWS.indexOf(State.prefs.view) < 0) State.prefs.view = "story";
    // a view named in the URL (report.html#view=evidence) wins for this
    // load — a link can open the page on its evidence or its batch
    try {
      var m = /(?:^|[#&])view=(story|evidence|batch)\b/.exec(global.location.hash || "");
      if (m) State.prefs.view = m[1];
    } catch (err) { /* no location: keep the preference */ }
    State.signals = Store.get(key("signals")) || {};
    if (typeof State.signals !== "object" || State.signals === null) State.signals = {};

    var reports = State.data.reports || [];
    State.task = reports.length && reports[0].task ? reports[0].task.id : null;

    els = {
      stacks: document.getElementById("stacks"),
      hero: document.getElementById("hero-lane"),
      lead: document.getElementById("lead-lane"),
      title: document.getElementById("page-title"),
      strip: document.getElementById("task-strip"),
      story: document.getElementById("story-lane"),
      tabs: document.getElementById("view-tabs"),
      reading: document.getElementById("reading"),
      picker: document.getElementById("task-picker"),
      drawer: document.getElementById("drawer"),
      drawerBody: document.getElementById("drawer-body"),
      you: document.getElementById("you"),
      help: document.getElementById("help"),
      helpBtn: document.getElementById("btn-help"),
      youBody: document.getElementById("you-body"),
      scrim: document.getElementById("scrim"),
      toast: document.getElementById("toast"),
      cols: document.getElementById("btn-cols"),
    };

    applyTheme();
    measureTopbar();
    global.addEventListener("resize", measureTopbar);
    State.layout = reconcile(Store.get(key("layout")), makeCtx());

    if (els.helpBtn && els.help) {
      els.helpBtn.addEventListener("click", function () {
        if (els.help.classList.contains("open")) closePanels(); else openPanel(els.help);
      });
    }
    if (els.tabs) {
      var tabButtons = els.tabs.querySelectorAll("[role=tab]");
      for (var t = 0; t < tabButtons.length; t++) {
        tabButtons[t].addEventListener("click", function (event) {
          setView(event.currentTarget.getAttribute("data-view"));
        });
      }
      els.tabs.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        var i = VIEWS.indexOf(State.prefs.view);
        var next = VIEWS[(i + (event.key === "ArrowRight" ? 1 : VIEWS.length - 1)) % VIEWS.length];
        setView(next);
        var active = els.tabs.querySelector('[data-view="' + next + '"]');
        if (active) active.focus();
      });
    }

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
      // [ and ] walk the batch, unless the reader is typing somewhere
      var tag = event.target && event.target.tagName;
      var typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
                   (event.target && event.target.isContentEditable);
      if (!typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        if (event.key === "[") { event.preventDefault(); stepTask(-1); return; }
        if (event.key === "]") { event.preventDefault(); stepTask(1); return; }
        if (event.key === "?" && els.help) {
          event.preventDefault();
          if (els.help.classList.contains("open")) closePanels(); else openPanel(els.help);
          return;
        }
      }
      // Escape closes the innermost thing first: an open tooltip, then the
      // panels — one keypress should never dismiss both at once.
      if (event.key === "Escape") {
        if (Explain.close()) return;
        closePanels();
      }
    });
    Explain.wire();

    // how many times this visitor has opened a report here (durable
    // storage only; a private window is always a first visit)
    try {
      State.visits = (parseInt(Store.get(key("visits")), 10) || 0) + 1;
      Store.set(key("visits"), String(State.visits));
    } catch (err) { State.visits = 1; }

    renderAll();

    // A first visit opens on the report, not on a toast about identity:
    // the You panel already states what is stored and why.
    if (!Identity.minted && !Store.durable) {
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

  var panelOpener = null;   // the element that had focus when a panel opened

  function openPanel(panel) {
    closePanels();
    try { panelOpener = document.activeElement; } catch (err) { panelOpener = null; }
    panel.classList.add("open");
    els.scrim.classList.add("open");
    try {
      var first = panel.querySelector("[data-close], button, [tabindex]");
      if (first) first.focus();
    } catch (err) { /* fine */ }
  }

  function closePanels() {
    var wasOpen = false;
    [els.drawer, els.you, els.help].forEach(function (panel) {
      if (panel && panel.classList.contains("open")) wasOpen = true;
      if (panel) panel.classList.remove("open");
    });
    els.scrim.classList.remove("open");
    if (wasOpen && panelOpener && panelOpener.focus) {
      try { panelOpener.focus(); } catch (err) { /* fine */ }
    }
    panelOpener = null;
  }

  global.AgentDiff = {
    block: block,
    boot: boot,
    /* Look a glossary term up by id — {term, label, short, long} or null.
     * For blocks that want the text inline (or tests that check it exists)
     * rather than the tooltip behaviour. */
    explainTerm: function (id) {
      var entry = TERMS[id];
      if (!entry) return null;
      return { term: id, label: entry.label, short: entry.short, long: entry.long };
    },
    // exposed for the smoke test, not for block modules
    // blocks that keep local state (a show-all toggle) ask for a re-render
    _rerender: function () { renderAll(); },
    /* A composite: one card that lays several blocks out one after another,
     * each under its own small title, only the parts that have something to
     * say, and a repeated note said once. The parts leave the default
     * layout (they stay in the drawer). spec: {id, title, question, group,
     * size, parts: [ids], summary?(ctx, shown, silent) -> string} */
    composite: function (spec) {
      var parts = spec.parts.slice();
      parts.forEach(function (id) { COMPOSED[id] = spec.id; });
      block({
        id: spec.id, title: spec.title, question: spec.question,
        group: spec.group, size: spec.size || "normal",
        relevance: function (ctx) {
          var best = 0;
          parts.forEach(function (id) {
            var entry = BY_ID[id];
            if (entry) best = Math.max(best, safeRelevance(entry, ctx, true));
          });
          return spec.relevance ? spec.relevance(ctx, best) : best;
        },
        render: function (el, ctx) {
          var shown = [], silent = [], seenNotes = {};
          parts.forEach(function (id) {
            var entry = BY_ID[id];
            if (!entry) return;
            if (safeRelevance(entry, ctx, true) <= 0) { silent.push(entry); return; }
            var body = h("div", { class: "cx-body" });
            var section = h("section", { class: "cx-part", "data-part": id }, [
              h("h4", { class: "cx-title", text: entry.title }), body]);
            try { entry.render(body, ctx); } catch (err) {
              body.innerHTML = "";
              body.appendChild(h("div", { class: "empty", text: "This part could not render: " + String(err && err.message || err) }));
            }
            // a part that rendered only an empty state has nothing to say
            var kids = Array.prototype.slice.call(body.children);
            if (!kids.length || kids.every(function (k) { return k.classList && k.classList.contains("empty"); })) {
              silent.push(entry);
              return;
            }
            // the same note, said once
            var notes = body.querySelectorAll(".vz-note, .caveat, .note, .ig-note, .dx-lede, .ax-lede");
            for (var i = 0; i < notes.length; i++) {
              var text = (notes[i].textContent || "").trim().replace(/\s+/g, " ");
              if (!text) continue;
              if (seenNotes[text]) notes[i].hidden = true; else seenNotes[text] = true;
            }
            el.appendChild(section);
            shown.push(entry);
          });
          if (spec.summary) {
            var line = spec.summary(ctx, shown, silent);
            if (line) el.insertBefore(h("p", { class: "cx-summary", text: line }), el.firstChild);
          }
          if (!shown.length) ctx.empty(el, spec.emptyText || "Nothing to show for this run.");
        },
      });
    },
    // a block may host another block's renderer (the map hosts the timeline)
    blockEntry: function (id) { return BY_ID[id] || null; },
    _internals: {
      blockEntry: function (id) { return BY_ID[id] || null; },
      rank: rank, reconcile: reconcile, defaultLayout: defaultLayout,
      resolveHero: resolveHero, promoteHero: promoteHero, demoteHero: demoteHero,
      DEFAULT_HERO: DEFAULT_HERO,
      State: State, REGISTRY: REGISTRY, BY_ID: BY_ID, fmt: fmt, uuid: uuid,
      decay: decay, Store: Store, STACK_PLAN: STACK_PLAN,
      TERMS: TERMS, Explain: Explain,
    },
  };
})(typeof window !== "undefined" ? window : this);
