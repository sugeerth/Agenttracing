/* AgentDiff — the live client.
 *
 * Only alive when the page was served by `deepcompare watch`, which
 * injects `live.enabled` into the data. Then it listens to that server's
 * event stream and hands every payload to AgentDiff.load; a badge in the
 * corner says what is running and when the last update landed. A page
 * opened from a file has no live block in its data and this module does
 * nothing — it is the one place the page speaks to a server, and it
 * speaks only to the one that served it.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;
  var data = global.DEEPCOMPARE_DATA;
  var live = data && data.live;
  if (!live || live.enabled !== true || typeof global.EventSource !== "function") return;

  var style = document.createElement("style");
  style.textContent = [
    "#live-badge{position:fixed;right:14px;bottom:14px;z-index:40;display:flex;gap:8px;align-items:center;",
    "padding:6px 10px;border-radius:999px;background:var(--ink);color:var(--bg);font-size:var(--fs-xs);box-shadow:var(--shadow)}",
    "#live-badge i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good)}",
    "#live-badge.off i{background:var(--warn);animation:none}",
    "#live-badge i{animation:live-pulse 1.2s ease-in-out infinite alternate}",
    "@keyframes live-pulse{from{opacity:.4}to{opacity:1}}",
    "@media (prefers-reduced-motion:reduce){#live-badge i{animation:none}}",
    "@media (max-width:480px){#live-badge{right:8px;bottom:8px}}",
  ].join("");
  document.head.appendChild(style);

  var badge = document.createElement("div");
  badge.id = "live-badge";
  badge.setAttribute("role", "status");
  badge.setAttribute("aria-live", "polite");
  badge.innerHTML = "<i></i><span></span>";
  document.body.appendChild(badge);
  var text = badge.querySelector("span");

  function describe(payload, state) {
    var l = payload && payload.live ? payload.live : live;
    var running = Array.isArray(l.runs) ? l.runs.length : 0;
    var reports = payload && Array.isArray(payload.reports) ? payload.reports.length : (data.reports || []).length;
    var when = "";
    try { when = new Date().toLocaleTimeString(); } catch (err) { when = ""; }
    text.textContent = "LIVE · " + state + " · " + running + " running · " + reports + " compared" + (when ? " · " + when : "");
    badge.classList.toggle("off", state !== "connected");
  }
  describe(data, "connecting");

  var source = new global.EventSource(live.events || "/events");
  source.addEventListener("report", function (event) {
    var payload;
    try { payload = JSON.parse(event.data); } catch (err) { return; }
    AgentDiff.load(payload);
    describe(payload, "connected");
    AgentDiff._liveVersion = payload.live && payload.live.version;
  });
  source.onopen = function () { describe(null, "connected"); };
  source.onerror = function () { describe(null, "reconnecting"); };
  AgentDiff._live = { source: source, badge: badge };
})(typeof window !== "undefined" ? window : this);
