// AgentDiff hub: the one script, loaded only by a page that says it is live.
//
// It listens to the hub's event stream (/api/v1/events), which says what
// changed and never its content. Each block marked data-live="/path" is
// then re-fetched as the signed-in user and swapped in: the server draws
// every fragment with the same views that drew the page, so there is no
// second renderer here. A block with data-ids refreshes only when one of
// its ids moved. Nothing is sent anywhere but this hub.
(function () {
  "use strict";
  var blocks = Array.prototype.slice.call(document.querySelectorAll("[data-live]"));
  var status = document.getElementById("live-status");
  if (!blocks.length || !window.EventSource) { return; }

  function say(text, cls) {
    if (!status) { return; }
    status.textContent = text;
    status.className = "badge " + (cls || "idle");
  }

  var pending = {}, busy = {}, timer = null;

  function refresh(el) {
    var url = el.getAttribute("data-live");
    if (busy[url]) { pending[url] = el; return; }
    busy[url] = true;
    fetch(url, { credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (res) {
        if (res.redirected || res.status === 401 || res.status === 303) {
          say("signed out", "bad");
          source.close();
          throw new Error("signed out");
        }
        if (!res.ok) { throw new Error("status " + res.status); }
        return res.text();
      })
      .then(function (text) {
        var doc = new DOMParser().parseFromString(text, "text/html");
        el.replaceChildren.apply(el, Array.prototype.slice.call(doc.body.childNodes));
        var now = new Date();
        say("live · " + now.toLocaleTimeString(), "run");
      })
      .catch(function () { /* the next event tries again */ })
      .then(function () {
        busy[url] = false;
        if (pending[url]) { var again = pending[url]; delete pending[url]; refresh(again); }
      });
  }

  function changed(ids) {
    // one refresh per block per burst of events
    clearTimeout(timer);
    timer = setTimeout(function () {
      blocks.forEach(function (el) {
        var mine = (el.getAttribute("data-ids") || "").split(" ").filter(Boolean);
        if (!mine.length || mine.some(function (id) { return ids.indexOf(id) >= 0; })) { refresh(el); }
      });
    }, 250);
  }

  var source = new EventSource("/api/v1/events");
  source.addEventListener("open", function () { say("live", "run"); });
  source.addEventListener("error", function () { say("reconnecting…", "idle"); });
  source.addEventListener("change", function (ev) {
    var ids = [];
    try { ids = JSON.parse(ev.data).ids || []; } catch (e) { ids = []; }
    changed(ids);
  });
})();
