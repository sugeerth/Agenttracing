/* AgentDiff block — Next horizon.
 *
 * Why anyone reads a diff: to change something. This section is what the
 * pair hands forward — prompt suggestions for the next run (one per
 * finding the reading located, each quoting its finding and carrying the
 * replay that would test it), the reward-shaping events the labels
 * support, and the preference pair (chosen = the passing run or the
 * reconciled splice, rejected = the failing run) — with the signal
 * downloadable and the CLI that writes it for a whole batch. Everything
 * is `report.feedback`, derived by the engine; nothing here is invented.
 */
(function (global) {
  "use strict";
  var AgentDiff = global.AgentDiff;
  if (!AgentDiff) return;

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var node = document.createElement("style");
    node.textContent = [
      ".nh-lede{font-size:var(--fs-m);color:var(--ink-2);margin:0 0 8px}",
      ".nh-h{font-size:var(--fs-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);margin:12px 0 4px}",
      ".nh-prompts{list-style:none;margin:0;padding:0}",
      ".nh-prompts li{padding:7px 0;border-top:1px solid var(--rule);font-size:var(--fs-m)}",
      ".nh-prompts li:first-child{border-top:0}",
      ".nh-prompt{display:block;font-family:var(--mono);font-size:var(--fs-s);white-space:pre-wrap;color:var(--ink)}",
      ".nh-from{font-size:var(--fs-xs);color:var(--ink-3);margin-top:3px}",
      ".nh-from .tag{font-family:var(--mono);margin-right:4px}",
      ".nh-status{font-size:var(--fs-xs);color:var(--warn);margin-top:2px}",
      ".nh-table{border-collapse:collapse;font-size:var(--fs-s);width:100%;max-width:560px}",
      ".nh-table td{padding:3px 8px 3px 0;border-top:1px solid var(--rule);vertical-align:top}",
      ".nh-table tr:first-child td{border-top:0}",
      ".nh-table .num{font-family:var(--mono);text-align:right;white-space:nowrap}",
      ".nh-sign{font-family:var(--mono);font-weight:700}",
      ".nh-sign.neg{color:var(--bad)}.nh-sign.pos{color:var(--good)}",
      ".nh-pair{font-size:var(--fs-s);color:var(--ink-2)}",
      ".nh-pair code{font-family:var(--mono);font-size:var(--fs-xs)}",
      ".nh-export{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin-top:10px;font-size:var(--fs-xs);color:var(--ink-3)}",
      ".nh-export a{font-size:var(--fs-s);color:var(--accent)}",
      ".nh-export code{font-family:var(--mono);background:var(--surface-2);padding:2px 6px;border-radius:4px}",
      "body[data-view=\"story\"] .nh-export code{background:transparent;padding:0}",
      ".nh-copy{font:inherit;font-size:var(--fs-xs);padding:1px 8px;border:1px solid var(--rule-2);border-radius:999px;background:var(--surface);color:var(--ink-2);cursor:pointer}",
    ].join("");
    document.head.appendChild(node);
  }

  function isNum(v) { return typeof v === "number" && isFinite(v); }

  function copyable(ctx, text) {
    var b = ctx.h("button", { class: "nh-copy", type: "button", text: "copy" });
    b.addEventListener("click", function () {
      var done = function () { b.textContent = "copied"; setTimeout(function () { b.textContent = "copy"; }, 1500); };
      try {
        if (global.navigator && global.navigator.clipboard) { global.navigator.clipboard.writeText(text).then(done, done); return; }
      } catch (err) { /* fall through */ }
      done();
    });
    return b;
  }

  AgentDiff.block({
    id: "next-horizon",
    title: "Next horizon",
    storyTitle: "Next horizon",
    question: "What does this pair hand to the next run, the reward, and the training loop?",
    group: "outcome",
    size: "wide",

    relevance: function (ctx) {
      var fb = ctx.report && ctx.report.feedback;
      if (!fb || typeof fb !== "object") return 0;
      var n = (fb.prompt_suggestions || []).length + (fb.preference_pair ? 1 : 0) + (fb.reward_shaping || []).length;
      return n ? 0.86 : 0;
    },

    render: function (el, ctx) {
      ensureStyle();
      var report = ctx.report;
      var fb = report && report.feedback;
      if (!fb) return ctx.empty(el, "This report carries no feedback signal; re-run compare to get one.");
      var H = ctx.h;
      var prompts = Array.isArray(fb.prompt_suggestions) ? fb.prompt_suggestions : [];
      var shaping = Array.isArray(fb.reward_shaping) ? fb.reward_shaping : [];
      var pair = fb.preference_pair;

      el.appendChild(H("p", { class: "nh-lede", text:
        "Understanding is only worth what it changes. Three things leave this page: sentences for the next run's prompt, " +
        "the events a reward could shape, and a preference pair for a training loop — each derived from the findings above, " +
        "each a hypothesis until a replay confirms it." }));

      // 1. prompt suggestions
      if (prompts.length) {
        el.appendChild(H("div", { class: "nh-h", text: "Prompt suggestions for the next horizon (" + prompts.length + ")" }));
        var list = H("ol", { class: "nh-prompts" });
        prompts.forEach(function (p) {
          var from = p.derived_from || {};
          var li = H("li", { "data-kind": p.kind || "" });
          li.appendChild(H("span", { class: "nh-prompt", text: String(p.text || "") }));
          var meta = H("div", { class: "nh-from" });
          meta.appendChild(H("span", { class: "tag", text: String(p.kind || "").replace(/_/g, " ") }));
          if (isNum(from.at_step)) meta.appendChild(H("span", { class: "tag", text: "at step " + from.at_step }));
          (from.refs || []).forEach(function (r) { meta.appendChild(H("span", { class: "tag", text: String(r) })); });
          if (from.finding) meta.appendChild(H("span", { text: "from: " + String(from.finding) }));
          li.appendChild(meta);
          var test = p.test && typeof p.test === "object" ? p.test : null;
          li.appendChild(H("div", { class: "nh-status", text: String(p.status || "suggested") +
            (test ? " · test: replay " + String(test.replays || "").split(" — ")[0] + (test.expects ? ", expects " + test.expects : "") : "") }));
          list.appendChild(li);
        });
        el.appendChild(list);
        var all = prompts.map(function (p) { return "- " + p.text; }).join("\n");
        var row = H("div", { class: "nh-export" }, [H("span", { text: "all " + prompts.length + " as a prompt block" })]);
        row.appendChild(copyable(ctx, all));
        el.appendChild(row);
      }

      // 2. reward shaping
      if (shaping.length) {
        el.appendChild(H("div", { class: "nh-h", text: "What a reward could shape (from the step labels)" }));
        var table = H("table", { class: "nh-table" });
        shaping.forEach(function (r) {
          table.appendChild(H("tr", { "data-event": r.event }, [
            H("td", null, [H("span", { class: "nh-sign " + (r.sign < 0 ? "neg" : "pos"), text: r.sign < 0 ? "−" : "+" })]),
            H("td", { text: String(r.event || "").replace(/_/g, " ") }),
            H("td", { class: "num", text: "×" + r.count }),
            H("td", { text: String(r.basis || "") }),
          ]));
        });
        el.appendChild(table);
      }

      // 3. the preference pair
      if (pair) {
        el.appendChild(H("div", { class: "nh-h", text: "Preference pair for a training loop" }));
        var chosenN = (pair.chosen && pair.chosen.turns || []).length, rejectedN = (pair.rejected && pair.rejected.turns || []).length;
        el.appendChild(H("p", { class: "nh-pair" }, [
          H("span", { text: "chosen: " + (pair.chosen ? pair.chosen.agent : "?") + " (" + chosenN + " turns; " + (pair.chosen ? pair.chosen.basis : "") + ") · " }),
          H("span", { text: "rejected: " + (pair.rejected ? pair.rejected.agent : "?") + " (" + rejectedN + " turns)" +
            (pair.diverges_at && isNum(pair.diverges_at.step) ? " · diverges at step " + pair.diverges_at.step + " (" + (pair.diverges_at.verification || "") + ")" : "") +
            (pair.confidence ? " · estimate confidence " + pair.confidence : "") }),
        ]));
      }

      // 4. export
      var labels = Array.isArray(fb.step_labels) ? fb.step_labels.length : 0;
      var exportRow = H("div", { class: "nh-export" });
      try {
        var blob = JSON.stringify(fb, null, 1);
        var href = "data:application/json;charset=utf-8," + encodeURIComponent(blob);
        var taskId = report.task && report.task.id ? report.task.id : "report";
        exportRow.appendChild(H("a", { href: href, download: "signal_" + taskId + ".json", text: "download the signal (" + labels + " labelled steps)" }));
      } catch (err) { /* no export */ }
      exportRow.appendChild(H("span", { text: "for a whole batch:" }));
      exportRow.appendChild(H("code", { text: "python -m deepcompare feedback out/ -o signal.json --jsonl pairs.jsonl" }));
      exportRow.appendChild(copyable(ctx, "python -m deepcompare feedback out/ -o signal.json --jsonl pairs.jsonl"));
      el.appendChild(exportRow);
      if (fb.note) el.appendChild(H("p", { class: "nh-from", text: String(fb.note) }));
    },
  });

})(typeof window !== "undefined" ? window : this);
