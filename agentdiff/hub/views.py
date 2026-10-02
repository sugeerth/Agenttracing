"""The hub's pages, as HTML strings.

Every value that reaches a page goes through :func:`e` (escaped), every
form that changes state carries its token, and the look is one set of
colour tokens with a dark variant. The charts are :mod:`.viz`'s, drawn on
the server. Pages run no script, except a page that says it is live: it
loads the hub's one static file, which listens to the event stream and
re-fetches that page's own fragments, drawn by these same functions.

The full AgentDiff report a run links to is the page the run wrote; the
hub only frames it.
"""

from __future__ import annotations

import html
import time
from typing import List, Optional

from . import viz
from .catalog import Entry

__all__ = ["layout", "login_page", "overview_page", "runs_page", "duel_page", "report_page", "telemetry_page",
           "traces_page", "trace_page", "trace_panel", "live_page", "live_panel", "evals_page", "evals_run_page",
           "account_page", "message_page", "NAV"]


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


#: the sections, in order: (key, label, path)
NAV = (("overview", "Overview", "/"), ("runs", "Runs", "/runs"), ("traces", "Traces", "/traces"),
       ("live", "Live", "/live"), ("evals", "Evals", "/evals"), ("account", "Account", "/account"))

_CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--soft:#5d5d58;--line:#e3e2dc;--accent:#2f5bd3;
--good:#1e7a46;--bad:#b3261e;--warn:#9a6700;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;--chip:#efeee9}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;--soft:#a3a29c;
--line:#33322f;--accent:#8ab0ff;--good:#5cc98a;--bad:#ff8a80;--warn:#e3b341;--chip:#2a2927}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{display:flex;align-items:center;gap:18px;padding:10px 20px;border-bottom:1px solid var(--line);
background:var(--panel);position:sticky;top:0;z-index:2}
header .brand{font-weight:700;letter-spacing:.2px;display:flex;align-items:center;gap:8px;color:var(--ink)}
header nav{flex:1;display:flex;gap:4px;flex-wrap:wrap}header nav a{padding:5px 10px;border-radius:7px;color:var(--soft)}
header nav a.on{background:var(--chip);color:var(--ink);font-weight:600}header nav a:hover{text-decoration:none;color:var(--ink)}
header form{margin:0}.who{color:var(--soft);font-size:14px}
main{max-width:1100px;margin:0 auto;padding:22px 16px 60px}h1{font-size:21px;margin:4px 0 6px}
.sub{color:var(--soft);margin:0 0 16px}
h2{font-size:13px;margin:26px 0 8px;color:var(--soft);font-weight:650;text-transform:uppercase;letter-spacing:.7px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 12px;
overflow-x:auto}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.kind{font:600 11px var(--mono);
text-transform:uppercase;letter-spacing:.6px;padding:2px 7px;border-radius:999px;border:1px solid var(--line);color:var(--soft)}
.muted{color:var(--soft)}.mono{font-family:var(--mono);font-size:13px}.ok{color:var(--good)}.bad{color:var(--bad)}
.warn{color:var(--warn)}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}th{color:var(--soft);font-weight:600;font-size:13px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}td.clip{max-width:340px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:12.5px}
button,input{font:inherit}input[type=text],input[type=password],input[type=search]{width:100%;padding:9px 10px;
border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button{padding:8px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:var(--panel);
cursor:pointer;font-weight:600}button.link{background:none;border:none;color:var(--accent);padding:0;font-weight:400}
.note{border-left:3px solid var(--warn);padding:6px 10px;margin:8px 0;background:var(--panel);border-radius:0 8px 8px 0}
.error{border-left-color:var(--bad)}.good{border-left-color:var(--good)}code{font-family:var(--mono);font-size:13px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 6px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile b{display:block;font-size:26px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.2}
.tile span{color:var(--soft);font-size:13px}
.badge{display:inline-flex;align-items:center;gap:5px;font:600 12px system-ui,sans-serif;padding:2px 9px;
border-radius:999px;border:1px solid var(--line);white-space:nowrap}
.badge.ok{border-color:var(--good)}.badge.bad{border-color:var(--bad)}.badge.run{border-color:var(--accent);color:var(--accent)}
.badge.idle{color:var(--soft)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block}
.badge.run .dot{animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.badge.run .dot{animation:none}}
.split{display:grid;grid-template-columns:minmax(0,440px) minmax(0,1fr);gap:12px}
.split>.card{margin:0}
.livecard{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 14px;align-items:center}
.livecard .ribbon{grid-column:1/-1}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
.filters a{padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--soft);font-size:13px}
.filters a.on{background:var(--chip);color:var(--ink);border-color:var(--soft)}
.filters form{display:flex;gap:6px;margin-left:auto}.filters input{width:220px;padding:5px 9px}
.filters button{padding:5px 12px}
#live-status{font-size:13px}
.login-wrap{display:grid;grid-template-columns:1fr 1fr;max-width:860px;margin:7vh auto;background:var(--panel);
border:1px solid var(--line);border-radius:16px;overflow:hidden}
.login-side{padding:30px;background:var(--chip);display:flex;flex-direction:column;gap:12px}
.login-side h1{font-size:26px;margin:0}.login-side ul{margin:0;padding-left:18px;color:var(--soft)}
.login-side li{margin:4px 0}.login-form{padding:30px}
.login-form label{display:block;margin:12px 0 4px;color:var(--soft);font-size:14px}
.login-form button{width:100%;margin-top:18px;padding:10px}
@media (max-width:760px){.split{grid-template-columns:1fr}.login-wrap{grid-template-columns:1fr;margin:3vh 0}
.login-side{padding:20px}.login-form{padding:20px}}
@media (max-width:640px){header{flex-wrap:wrap;padding:10px 16px;gap:8px}header nav{order:3;flex:1 1 100%}
td,th{font-size:13px;padding:5px}.filters form{margin-left:0;width:100%}.filters input{flex:1;width:auto}}
""" + viz.VIZ_CSS

_MARK = ('<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">'
         '<rect x="1" y="3" width="6" height="6" rx="2" style="fill:var(--a1)"/>'
         '<rect x="8" y="3" width="6" height="6" rx="2" style="fill:var(--a2)"/>'
         '<rect x="15" y="3" width="6" height="6" rx="2" style="fill:var(--a3)"/>'
         '<rect x="1" y="12" width="6" height="6" rx="2" style="fill:var(--a1)"/>'
         '<rect x="8" y="12" width="6" height="6" rx="2" style="fill:var(--a2)"/>'
         '<rect x="15" y="12" width="6" height="6" rx="2" style="fill:var(--sg)"/></svg>')


def layout(title: str, body: str, *, brand: str, user: Optional[str] = None, csrf: Optional[str] = None,
           active: str = "", live: bool = False) -> str:
    top = ""
    if user:
        links = "".join(f'<a href="{path}"{" class=on" if key == active else ""}>{label}</a>'
                        for key, label, path in NAV)
        top = (f'<nav aria-label="sections">{links}</nav>'
               + ('<span id="live-status" class="badge idle" aria-live="polite">connecting…</span>' if live else "")
               + f'<span class="who">{e(user)}</span>'
               f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
               f'<button class="link" type="submit">Sign out</button></form>')
    script = '<script src="/static/live.js" defer></script>' if live else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)} · {e(brand)}</title><style>{_CSS}</style>{script}</head><body>'
            f'<header><a class="brand" href="/">{_MARK}{e(brand)}</a>{top}</header><main>{body}</main></body></html>')


def login_page(*, brand: str, token: str, error: Optional[str], demo: Optional[tuple], next_path: str) -> str:
    hint = ""
    if demo:
        hint = (f'<p class="note">Demo account: <code>{e(demo[0])}</code> / <code>{e(demo[1])}</code>. '
                f'It exists on this machine only; <code>--no-demo</code> turns it off.</p>')
    err = f'<p class="note error" role="alert">{e(error)}</p>' if error else ""
    side = (f'<div class="login-side">{_MARK}<h1>{e(brand)}</h1><p class="muted">git diff for AI agents: '
            f'every run, every trace, and the loop each agent went round, on one page.</p>'
            f'<ul><li>Traces, lap by lap: where an agent converged, and where it went in circles</li>'
            f'<li>Live: runs as they happen, streamed from disk or in-band telemetry</li>'
            f'<li>Evals that evolve with the policy, generation by generation</li></ul></div>')
    form = (f'<div class="login-form"><h1>Sign in</h1>{err}{hint}'
            f'<form method="post" action="/login"><input type="hidden" name="csrf" value="{e(token)}">'
            f'<input type="hidden" name="next" value="{e(next_path)}">'
            f'<label for="u">User</label><input id="u" type="text" name="user" autocomplete="username" required autofocus>'
            f'<label for="p">Password</label><input id="p" type="password" name="password" '
            f'autocomplete="current-password" required>'
            f'<button type="submit">Sign in</button></form></div>')
    return layout("Sign in", f'<div class="login-wrap">{side}{form}</div>', brand=brand)


# ----------------------------------------------------------------- bits
def _ago(t: float) -> str:
    s = max(0, time.time() - t)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{int(s // n)}{unit} ago"
    return "just now"


def _secs(s) -> str:
    if not isinstance(s, (int, float)):
        return "—"
    return f"{s:.1f}s" if s < 120 else f"{s / 60:.1f}m"


def _num(n) -> str:
    return f"{int(n):,}" if isinstance(n, (int, float)) else "—"


#: a running trace with no new frame for this long is shown as stalled
STALL_S = 600.0


def _status(ref) -> str:
    s = ref.summary
    if ref.live:
        if time.time() - ref.updated > STALL_S:
            return f'<span class="badge idle" title="no new step since {e(_ago(ref.updated))}">◌ stalled</span>'
        return '<span class="badge run"><span class="dot"></span>running</span>'
    if s.get("success") is True:
        return '<span class="badge ok"><span class="ok">✓</span>passed</span>'
    if s.get("success") is False:
        return '<span class="badge bad"><span class="bad">✗</span>failed</span>'
    return '<span class="badge idle">– no outcome</span>'


def _loop_note(c: Optional[dict], live: bool = False) -> str:
    if not c:
        return ""
    if c.get("stuck") and not live:
        return '<span class="bad" title="the loop rule: the same block went round and never passed">↻ stuck</span>'
    if c.get("first_pass_lap"):
        return f'<span class="muted">green on lap {e(c["first_pass_lap"])}</span>'
    if c.get("repeated"):
        return f'<span class="warn">↻ {e(c["repeated"])} repeat(s)</span>'
    return ""


def _trace_rows(refs) -> str:
    rows = []
    for r in refs:
        s = r.summary
        rows.append(f'<tr><td>{_status(r)}</td><td><a href="/traces/{e(r.id)}"><strong>{e(s.get("task"))}</strong></a>'
                    f'<div class="muted mono">{e(r.name)}</div></td><td>{e(s.get("agent"))}</td>'
                    f'<td>{viz.lap_strip(s.get("laps"))}<div>{_loop_note(s.get("laps"), r.live)}</div></td>'
                    f'<td class="n">{e(s.get("steps"))}</td><td class="n">{_num(s.get("tokens"))}</td>'
                    f'<td class="n">{_secs(s.get("elapsed_s") if r.live else s.get("seconds"))}</td>'
                    f'<td class="muted">{e(_ago(r.updated))}</td></tr>')
    return ('<table><tr><th>status</th><th>task</th><th>agent</th><th>laps</th><th class="n">steps</th>'
            '<th class="n">tokens</th><th class="n">time</th><th>updated</th></tr>' + "".join(rows) + "</table>")


def _entry_detail(x: Entry) -> str:
    if x.kind == "duel":
        return _score_line(x)
    if x.kind == "telemetry":
        s = x.summary
        return (f'{e(s.get("hops"))} hops across {e(len(s.get("nodes") or []))} process(es), '
                f'{e(s.get("errors"))} not ok' + (" · <strong>live</strong>" if s.get("live") else ""))
    if x.kind == "evals":
        s = x.summary
        fwd = s.get("last_forward")
        return (f'{e(s.get("generations"))} generation(s) · {e(s.get("active"))} active, {e(s.get("retired"))} retired'
                + (f' · last forward coverage {fwd:.0%}' if isinstance(fwd, (int, float)) else ""))
    return e(", ".join(map(str, x.summary.get("agents") or [])))


def _entry_cards(entries: List[Entry]) -> str:
    return "".join(f'<div class="card"><div class="row"><span class="kind">{e(x.kind)}</span>'
                   f'<a href="/runs/{e(x.id)}"><strong>{e(x.title)}</strong></a>'
                   f'<span class="muted">{e(_ago(x.updated))}</span></div>'
                   f'<div class="muted">{_entry_detail(x)}</div></div>' for x in entries)


def _score_line(entry: Entry) -> str:
    parts = []
    for r in entry.summary.get("rows") or []:
        if r.get("graded"):
            cls = "ok" if r["passed"] == r["graded"] else ("bad" if not r["passed"] else "warn")
            parts.append(f'{e(r["agent"])} <span class="{cls}">{e(r["passed"])}/{e(r["graded"])}</span>')
        else:
            parts.append(f'{e(r["agent"])} <span class="muted">ungraded</span>')
    return " · ".join(parts)


def _ingest_card(ingest: dict) -> str:
    return (f'<div class="card"><p class="muted">Agents traced in-band post their vector here; a trace directory '
            f'streams with <code>agentdiff telemetry stream</code>:</p>'
            f'<pre>export AGENTDIFF_HUB={e(ingest["url"])}\nexport AGENTDIFF_HUB_TOKEN={e(ingest["token"])}\n'
            f'agentdiff telemetry send "$AGENTDIFF_INT"        # one run\n'
            f'agentdiff telemetry stream duel-out/traces       # every run in a directory, as it grows</pre></div>')


# ------------------------------------------------------------- overview
def overview_page(*, brand: str, user: str, csrf: str, entries: List[Entry], refs: list, ingest: dict) -> str:
    live = [r for r in refs if r.live and time.time() - r.updated <= STALL_S]
    finished = [r for r in refs if not r.live]
    judged = [r for r in finished if r.summary.get("success") is not None]
    passed = sum(1 for r in judged if r.summary.get("success"))
    stuck = sum(1 for r in finished if (r.summary.get("laps") or {}).get("stuck"))
    rate = f"{passed / len(judged):.0%}" if judged else "—"
    tiles = (f'<div class="tiles">'
             f'<div class="tile"><b>{len(live)}</b><span>running now</span></div>'
             f'<div class="tile"><b>{len(refs)}</b><span>traces</span></div>'
             f'<div class="tile"><b>{rate}</b><span>passed, of {len(judged)} with an outcome</span></div>'
             f'<div class="tile"><b class="{"bad" if stuck else ""}">{stuck}</b><span>stuck in a loop</span></div>'
             f'<div class="tile"><b>{len(entries)}</b><span>runs: duels, reports, evals</span></div></div>')
    live_html = (_trace_rows(live[:8]) if live else
                 '<p class="muted">Nothing running. Start one — <code>agentdiff "the task"</code> — and it appears '
                 'here as it goes; the <a href="/live">Live</a> page follows it step by step.</p>')
    loops = [r for r in finished if (r.summary.get("laps") or {}).get("stuck")][:6]
    body = (f'<h1>Overview</h1><p class="sub">Everything under this hub\'s root, read from disk.</p>{tiles}'
            f'<h2>Running now</h2><div class="card">{live_html}</div>'
            + (f'<h2>Stuck in a loop</h2><div class="card">{_trace_rows(loops)}</div>' if loops else "")
            + f'<h2>Recent traces</h2><div class="card">{_trace_rows(finished[:10]) if finished else "<p class=muted>None yet.</p>"}'
            f'<p><a href="/traces">Every trace →</a></p></div>'
            f'<h2>Recent runs</h2>{_entry_cards(entries[:6]) or "<div class=card><p class=muted>None yet.</p></div>"}'
            f'<p><a href="/runs">Every run →</a></p><h2>Send telemetry here</h2>{_ingest_card(ingest)}')
    return layout("Overview", body, brand=brand, user=user, csrf=csrf, active="overview")


def runs_page(*, brand: str, user: str, csrf: str, entries: List[Entry], ingest: dict, kind: str = "") -> str:
    kinds = sorted({x.kind for x in entries})
    shown = [x for x in entries if not kind or x.kind == kind]
    chips = "".join(f'<a href="/runs{"?kind=" + k if k else ""}"{" class=on" if k == kind else ""}>{e(k or "all")}</a>'
                    for k in [""] + kinds)
    cards = _entry_cards(shown) or ('<div class="card muted">No runs here yet. In a project: <code>agentdiff fix</code> '
                                    'or <code>agentdiff "the task"</code>, then reload.</div>')
    body = (f'<h1>Runs</h1><p class="sub">Duels, reports and eval suites under the root, newest first.</p>'
            f'<div class="filters">{chips}</div>{cards}<h2>Send telemetry here</h2>{_ingest_card(ingest)}')
    return layout("Runs", body, brand=brand, user=user, csrf=csrf, active="runs")


def duel_page(*, brand: str, user: str, csrf: str, entry: Entry, refs: list = ()) -> str:
    s = entry.summary
    head = "".join(f'<th class="n">{h}</th>' for h in ("passed", "median tokens", "cost", "median time"))
    body_rows = []
    for r in s.get("rows") or []:
        cost = f'${r["cost_usd"]:.3f}' if isinstance(r.get("cost_usd"), (int, float)) else "not reported"
        tok = f'{int(r["tokens"]):,}' if isinstance(r.get("tokens"), (int, float)) else "—"
        wall = f'{r["wall_s"]:.0f}s' if isinstance(r.get("wall_s"), (int, float)) else "—"
        passed = f'{r["passed"]} of {r["graded"]}' if r.get("graded") else "ungraded"
        body_rows.append(f'<tr><td>{e(r["agent"])}</td><td class="n">{e(passed)}</td><td class="n">{e(tok)}</td>'
                         f'<td class="n">{e(cost)}</td><td class="n">{e(wall)}</td></tr>')
    notes = []
    if s.get("already_passing"):
        notes.append(f'The check passed before any work on {e(", ".join(s["already_passing"]))}: a pass there shows nothing.')
    if s.get("unequal"):
        notes.append("Not equal between them: " + e(", ".join(s["unequal"])) + ".")
    winners = [r["agent"] for r in s.get("rows") or [] if r.get("graded") and r.get("passed")]
    keep = " or ".join(f"<code>agentdiff apply {e(a)}</code>" for a in winners)
    traces = (f'<h2>Its traces, lap by lap</h2><div class="card">{_trace_rows(refs)}</div>' if refs else "")
    body = (f'<h1>{e(entry.title)}</h1><div class="card"><table><tr><th>agent</th>{head}</tr>'
            f'{"".join(body_rows)}</table></div>'
            + "".join(f'<p class="note">{n}</p>' for n in notes)
            + f'<div class="card"><p>{e(s.get("narrative"))}</p>'
            + (f'<p><a href="/runs/{e(entry.id)}/page">Open the full report</a></p>' if entry.page else "")
            + f'</div>{traces}<h2>From here</h2><div class="card"><p class="mono">cd {e(entry.path.parent)}</p>'
            + (f'<p>Keep a change: {keep}</p>' if keep else "")
            + '<p>More runs of the same comparison: <code>agentdiff again 3</code></p></div>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf, active="runs")


def report_page(*, brand: str, user: str, csrf: str, entry: Entry, refs: list = ()) -> str:
    traces = f'<h2>Its traces</h2><div class="card">{_trace_rows(refs)}</div>' if refs else ""
    body = (f'<h1>{e(entry.title)}</h1><div class="card"><p class="muted">{e(entry.path)}</p>'
            f'<p><a href="/runs/{e(entry.id)}/page">Open the report</a></p></div>{traces}')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf, active="runs")


def telemetry_page(*, brand: str, user: str, csrf: str, entry: Entry, rows: List[dict], info: dict,
                   trace_ref=None) -> str:
    cols = [c for c in ("start", "latency", "tool", "kind", "status", "bytes_in", "bytes_out", "tokens_in",
                        "tokens_out", "node") if any(c in r for r in rows)]

    def cell(r, c):
        v = r.get(c)
        return f"{v:.3f}" if isinstance(v, float) else v
    table = ("<table><tr>" + "".join(f"<th>{e(c)}</th>" for c in cols) + "</tr>"
             + "".join("<tr>" + "".join(
                 f'<td class="{"bad" if c == "status" and r.get(c) not in (None, "ok") else ""}">{e(cell(r, c))}</td>'
                 for c in cols) + "</tr>" for r in rows) + "</table>")
    notes = []
    if info.get("dropped"):
        notes.append(f'{info["dropped"]} hop(s) were refused at the hop budget: the path is incomplete.')
    if info.get("clamped"):
        notes.append("Some values were too large for a 32-bit word and were clamped.")
    span_s = "%.3f" % info["span_s"]
    loop = (f' · <a href="/traces/{e(trace_ref.id)}">its loop, lap by lap →</a>' if trace_ref else "")
    body = (f'<h1>{e(entry.title)}</h1><p class="sub">{e(info["hops"])} hops across '
            f'{e(len(info["nodes"]))} process(es) in {e(span_s)}s · {e(info["errors"])} not ok · '
            f'trace <span class="mono">{e(info["trace_id"])}</span>{loop}</p>'
            + "".join(f'<p class="note">{e(n)}</p>' for n in notes)
            + f'<h2>Hops on one clock</h2><div class="card">{viz.hop_timeline(rows, info["span_s"])}</div>'
            f'<h2>Every hop</h2><div class="card">{table}</div>'
            f'<p class="muted">In-band telemetry carries sizes, times and outcomes, never what a tool read or returned. '
            f'As a trace for every other command: <code>agentdiff batch {e(entry.path / "traces")}</code></p>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf, active="runs")


# --------------------------------------------------------------- traces
def traces_page(*, brand: str, user: str, csrf: str, refs: list, q: str = "", show: str = "") -> str:
    def keep(r) -> bool:
        s = r.summary
        if q and q.lower() not in " ".join(str(x) for x in (s.get("task"), s.get("agent"), r.name, r.group)).lower():
            return False
        if show == "live":
            return r.live
        if show == "failed":
            return not r.live and s.get("success") is False
        if show == "passed":
            return not r.live and s.get("success") is True
        if show == "stuck":
            return bool((s.get("laps") or {}).get("stuck"))
        return True
    shown = [r for r in refs if keep(r)]
    chips = "".join(f'<a href="/traces?show={k}{"&q=" + e(q) if q else ""}"{" class=on" if k == show else ""}>'
                    f'{label}</a>' for k, label in (("", "all"), ("live", "running"), ("passed", "passed"),
                                                     ("failed", "failed"), ("stuck", "stuck")))
    search = (f'<form method="get" action="/traces"><input type="hidden" name="show" value="{e(show)}">'
              f'<input type="search" name="q" value="{e(q)}" placeholder="task, agent, file" aria-label="filter">'
              f'<button type="submit">Filter</button></form>')
    groups: dict = {}
    for r in shown:
        groups.setdefault(r.group, []).append(r)
    blocks = "".join(f'<h2>{e(g)} <span class="muted">· {len(rs)}</span></h2><div class="card">{_trace_rows(rs)}</div>'
                     for g, rs in groups.items())
    if not blocks:
        blocks = ('<div class="card"><p class="muted">No traces match. A trace is any SCHEMA file in a '
                  '<code>traces/</code> directory under the root, or a run an agent posted.</p></div>')
    key = viz.lap_strip({"marks": [["f", False], ["f", True], ["p", False]], "summary": "example"})
    body = (f'<h1>Traces</h1><p class="sub">Every agent\'s attempt at every task, with its loop folded to a strip: '
            f'{key} one square per lap, <span class="ok">✓</span> its check passed, <span class="bad">✗</span> failed, '
            f'an orange bar under a lap that repeated the one before.</p>'
            f'<div class="filters">{chips}{search}</div>{blocks}')
    return layout("Traces", body, brand=brand, user=user, csrf=csrf, active="traces")


def _steps_table(steps: List[dict], most: int = 400) -> str:
    from ..laps import _activity
    rows = []
    for i, s in enumerate(steps[:most]):
        act = _activity(s)
        cls, glyph, label = viz.ACTIVITIES.get(act, viz.ACTIVITIES["other"])
        err = s.get("error")
        rows.append(f'<tr><td class="n">{i}</td><td><span class="legend"><span><i class="{cls}">{e(glyph)}</i>'
                    f'{e(label)}</span></span></td><td>{e(s.get("name") or s.get("type"))}</td>'
                    f'<td class="clip" title="{e(str(s.get("input") or "")[:600])}">{e(str(s.get("input") or "")[:200])}</td>'
                    f'<td class="clip{" bad" if err else ""}" title="{e(str(err or s.get("output") or "")[:600])}">'
                    f'{e(str(err or s.get("output") or "")[:200])}</td>'
                    f'<td class="n">{_secs(s.get("latency_s"))}</td><td class="n">{_num(s.get("tokens"))}</td></tr>')
    more = f'<p class="muted">{len(steps) - most} more step(s) not shown.</p>' if len(steps) > most else ""
    return ('<table><tr><th class="n">#</th><th>activity</th><th>step</th><th>input</th><th>output</th>'
            '<th class="n">time</th><th class="n">tokens</th></tr>' + "".join(rows) + "</table>" + more)


def trace_panel(*, ref, data: dict, lap: dict) -> str:
    """The part of a trace's page that moves while it runs."""
    s = ref.summary
    steps = data.get("steps") or []
    head = (f'<div class="row">{_status(ref)}<span class="muted">{e(s.get("agent"))}'
            + (f' · {e(s.get("model"))}' if s.get("model") else "")
            + f' · {e(len(steps))} steps · {_num(s.get("tokens"))} tokens · '
            f'{_secs(s.get("elapsed_s") if ref.live else s.get("seconds"))}'
            f' · updated {e(_ago(ref.updated))}</span></div>')
    if ref.live:
        # a run still going is drawn, never judged: a verdict on half a run would flip as it grows
        rep = len(lap.get("repeated_laps") or [])
        verdict = (f'<p class="note">{e(lap.get("count"))} lap(s) so far'
                   + (f", {rep} of them the lap before again" if rep else "")
                   + ('; a check has passed' if lap.get("first_pass_lap") else "")
                   + '. Still running: the loop is drawn as far as it has gone, and judged when it ends.</p>')
    else:
        stuck = "stuck:" in (lap.get("summary") or "")
        verdict = (f'<p class="note{" error" if stuck else " good" if lap.get("first_pass_lap") else ""}">'
                   f'{e(lap.get("summary"))}</p>')
    if (data.get("source") or {}).get("format") == "agentdiff-int":
        verdict += ('<p class="note">This trace came in-band: sizes, times and outcomes only. A check\'s pass or '
                    'fail is known only when the tool itself failed, and two laps count as the same when they '
                    'called the same tools, since the arguments never travelled.</p>')
    return (f'{head}{verdict}'
            f'<h2>The loop, lap by lap</h2><div class="card">{viz.lap_chart(lap)}</div>'
            f'<h2>How it moved between tools</h2><div class="split"><div class="card">{viz.flow_ring(lap)}</div>'
            f'<div class="card">{viz.lap_table(lap)}</div></div>'
            f'<h2>Every step</h2><div class="card">{_steps_table(steps)}</div>')


def trace_page(*, brand: str, user: str, csrf: str, ref, data: dict, lap: dict) -> str:
    s = ref.summary
    body = (f'<p class="muted"><a href="/traces">Traces</a> / {e(ref.group)}</p>'
            f'<h1>{e(s.get("task"))}</h1>'
            f'<div data-live="/traces/{e(ref.id)}/panel" data-ids="{e(ref.id)}">{trace_panel(ref=ref, data=data, lap=lap)}</div>'
            f'<p class="muted mono">{e(ref.path.name)} · <a href="/api/v1/traces/{e(ref.id)}">JSON</a></p>')
    return layout(str(s.get("task")), body, brand=brand, user=user, csrf=csrf, active="traces", live=ref.live)


# ----------------------------------------------------------------- live
def live_panel(*, refs: list, steps_of) -> str:
    """Every running trace as a card: its rhythm so far, its loop so far."""
    now = time.time()
    running = [r for r in refs if r.live and now - r.updated <= STALL_S]
    stalled = [r for r in refs if r.live and now - r.updated > STALL_S]
    done = [r for r in refs if not r.live and now - r.updated <= 3600][:8]
    cards = []
    for r in running:
        s = r.summary
        steps = steps_of(r)
        last = steps[-1] if steps else {}
        cards.append(f'<div class="card livecard"><div class="row">{_status(r)}'
                     f'<a href="/traces/{e(r.id)}"><strong>{e(s.get("task"))}</strong></a>'
                     f'<span class="muted">{e(s.get("agent"))} · {e(r.group)}</span></div>'
                     f'<div class="muted n">{e(s.get("steps"))} steps · {_num(s.get("tokens"))} tokens · '
                     f'{_secs(s.get("elapsed_s"))}</div>'
                     f'<div class="ribbon">{viz.step_ribbon(steps)}</div>'
                     f'<div class="row muted">laps so far {viz.lap_strip(s.get("laps"))} {_loop_note(s.get("laps"), True)}'
                     f'<span>· now: {e(last.get("name") or last.get("type") or "—")}</span></div></div>')
    if not cards:
        cards.append('<div class="card"><p class="muted">Nothing is running. This page updates by itself: start a '
                     'run under the root (<code>agentdiff "the task"</code>), or stream one from another machine '
                     '(<code>agentdiff telemetry stream DIR</code>), and it appears here as it goes.</p></div>')
    out = "".join(cards)
    if stalled:
        out += f'<h2>Stalled</h2><div class="card">{_trace_rows(stalled)}</div>'
    if done:
        out += f'<h2>Finished in the last hour</h2><div class="card">{_trace_rows(done)}</div>'
    return out


def live_page(*, brand: str, user: str, csrf: str, panel: str) -> str:
    body = (f'<h1>Live</h1><p class="sub">Runs as they happen: each card is a running agent, its last steps '
            f'newest at the right, and its laps so far. A step on disk or a vector posted reaches this page in '
            f'about a second.</p>{viz.legend(list(viz.ACTIVITIES))}<div data-live="/live/panel">{panel}</div>'
            f'<noscript><p class="note">Without script this page is a snapshot: reload it to see more.</p></noscript>')
    return layout("Live", body, brand=brand, user=user, csrf=csrf, active="live", live=True)


# ---------------------------------------------------------------- evals
def evals_page(*, brand: str, user: str, csrf: str, entries: List[Entry], rivers: dict) -> str:
    blocks = []
    for x in entries:
        blocks.append(f'<h2>{e(x.title)}</h2><div class="card"><p class="muted">{e(x.summary.get("says"))}</p>'
                      f'{rivers.get(x.id) or ""}<p><a href="/runs/{e(x.id)}">Every eval and why it entered and left →</a>'
                      f'</p></div>')
    if not blocks:
        blocks.append('<div class="card"><p class="muted">No eval suites under the root yet. Carry one through '
                      'training generations with <code>agentdiff evolve-evals g0/ g1/ g2/ -o evals/</code>; '
                      'its <code>evolve-evals.json</code> shows here.</p></div>')
    body = (f'<h1>Evals</h1><p class="sub">Each suite across its generations. A bar is the forward coverage: what '
            f'the suite carried in caught of failures it had never seen. A lane is one eval\'s life.</p>'
            + "".join(blocks))
    return layout("Evals", body, brand=brand, user=user, csrf=csrf, active="evals")


def evals_run_page(*, brand: str, user: str, csrf: str, entry: Entry, data: dict) -> str:
    rows = []
    for ev in data.get("evals") or []:
        status = ('<span class="badge ok">active</span>' if ev.get("status") == "active"
                  else '<span class="badge bad">✕ retired</span>')
        rows.append(f'<tr><td class="mono">{e(ev.get("id"))}</td><td>{status}</td><td>{e(ev.get("born"))}</td>'
                    f'<td>{e(ev.get("retired_at") or "")}</td><td>{e(ev.get("says"))}'
                    f'<div class="muted">{e(ev.get("reason") or "")}</div></td></tr>')
    gens = []
    for g in data.get("lineage") or []:
        if g.get("skipped"):
            gens.append(f'<tr><td>{e(g["generation"])}</td><td colspan="5" class="muted">{e(g["skipped"])}</td></tr>')
            continue
        fwd = g.get("forward") or {}
        cov = f'{fwd["coverage"]:.0%}' if g.get("arrived") and fwd.get("coverage") is not None else "—"
        gens.append(f'<tr><td>{e(g["generation"])}</td><td class="n">{e(g["wrong"])}/{e(g["runs"])}</td>'
                    f'<td class="n">{e(cov)}</td><td class="n">{e(fwd.get("false_alarms"))}</td>'
                    f'<td>{e(", ".join(g.get("born") or []))}</td><td>{e(", ".join(g.get("retired") or []))}</td></tr>')
    body = (f'<p class="muted"><a href="/evals">Evals</a></p><h1>{e(entry.title)}</h1>'
            f'<p class="sub">{e(data.get("says"))} · patience {e(data.get("patience"))} · '
            f'noisy above {e(data.get("fpr_max"))} false-alarm rate</p>'
            f'<div class="card">{viz.eval_river(data)}</div><div class="card"><p>{e(data.get("narrative"))}</p></div>'
            f'<h2>Generations</h2><div class="card"><table><tr><th>generation</th><th class="n">to catch</th>'
            f'<th class="n">forward coverage</th><th class="n">false alarms</th><th>born</th><th>retired</th></tr>'
            f'{"".join(gens)}</table></div>'
            f'<h2>Every eval</h2><div class="card"><table><tr><th>eval</th><th>status</th><th>born</th><th>retired</th>'
            f'<th>what it says, and why it left</th></tr>{"".join(rows)}</table></div>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf, active="evals")


# -------------------------------------------------------------- account
def account_page(*, brand: str, user: str, csrf: str, role: str, demo: bool, expires: float, sessions: int,
                 message: Optional[str] = None, error: Optional[str] = None) -> str:
    msg = (f'<p class="note good" role="status">{e(message)}</p>' if message else "") + \
          (f'<p class="note error" role="alert">{e(error)}</p>' if error else "")
    left = max(0, expires - time.time())
    if demo:
        change = ('<p class="muted">This is the demo account: its password comes from the hub\'s settings, so it '
                  'is not changed here. Add a real user with <code>agentdiff hub --add-user NAME</code>.</p>')
    else:
        change = (f'<form method="post" action="/account/password"><input type="hidden" name="csrf" value="{e(csrf)}">'
                  f'<label for="c">Current password</label><input id="c" type="password" name="current" '
                  f'autocomplete="current-password" required>'
                  f'<label for="n">New password, at least 8 characters</label><input id="n" type="password" name="new" '
                  f'autocomplete="new-password" minlength="8" required>'
                  f'<label for="a">New password again</label><input id="a" type="password" name="again" '
                  f'autocomplete="new-password" minlength="8" required>'
                  f'<p><button type="submit">Change password</button></p></form>'
                  f'<p class="muted">Changing it signs out every other session of yours.</p>')
    body = (f'<h1>Account</h1>{msg}<div class="tiles"><div class="tile"><b>{e(user)}</b><span>{e(role)}'
            f'{" · demo" if demo else ""}</span></div><div class="tile"><b>{sessions}</b><span>open session(s)</span></div>'
            f'<div class="tile"><b>{left / 3600:.1f}h</b><span>until this session ends</span></div></div>'
            f'<h2>Password</h2><div class="card login-form" style="padding:14px 16px">{change}</div>')
    return layout("Account", body, brand=brand, user=user, csrf=csrf, active="account")


def message_page(*, brand: str, title: str, text: str, user: Optional[str] = None, csrf: Optional[str] = None) -> str:
    return layout(title, f'<h1>{e(title)}</h1><div class="card"><p>{e(text)}</p></div>', brand=brand,
                  user=user, csrf=csrf)
