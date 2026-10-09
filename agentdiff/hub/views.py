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
from pathlib import Path
from typing import Dict, List, Optional

from . import evolvecards, glance, seconds, viz
from .catalog import Entry

__all__ = ["layout", "login_page", "overview_page", "runs_page", "duel_page", "report_page", "telemetry_page",
           "traces_page", "trace_page", "trace_panel", "live_page", "live_panel", "evals_page", "evals_run_page",
           "account_page", "message_page", "evolve_page", "evolution_page", "timeline_page", "ribbon_group", "NAV"]


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


#: the sections, in order: (key, label, path)
NAV = (("overview", "Overview", "/"), ("runs", "Runs", "/runs"), ("traces", "Traces", "/traces"),
       ("live", "Live", "/live"), ("evolve", "Evolve", "/evolve"), ("evals", "Evals", "/evals"),
       ("account", "Account", "/account"))

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
.tag{display:inline-block;font:600 11px system-ui,sans-serif;letter-spacing:.02em;padding:0 6px;margin-left:6px;
  border-radius:4px;border:1px dashed var(--line);color:var(--soft);vertical-align:1px;cursor:help}
.nowrap{white-space:nowrap}
.acrossall h3{font:600 12px system-ui,sans-serif;letter-spacing:.04em;text-transform:uppercase;color:var(--soft);margin:18px 0 6px}
table.across{width:100%;table-layout:fixed}table.across td,table.across th{padding:4px 8px 4px 0;vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}table.across td.mono{max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
table.across th:nth-child(1){width:30%}table.across th:nth-child(2){width:13%}
table.across.fails th:nth-child(3),table.across.fails th:nth-child(4){width:8%}table.across.fails th:nth-child(6){width:10%}
.xbar{display:block;height:9px;border-radius:0 4px 4px 0;background:var(--a1);opacity:.75;min-width:2px}.xbar.bad{background:var(--sc);opacity:.65}
.askp{max-width:60ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.askbar{display:inline-block;height:8px;border-radius:4px;background:var(--accent);opacity:.55;vertical-align:middle}
.ask{color:var(--soft);font-size:13px;margin-top:2px;max-width:46ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--accent);display:inline-block}
.badge.run .dot{animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.badge.run .dot{animation:none}}
.vcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:4px 18px;margin:10px 0 14px}
.vrow{display:grid;grid-template-columns:104px minmax(0,1fr);gap:14px;padding:9px 0;border-bottom:1px solid var(--line)}
.vrow:last-child{border-bottom:0}.vlab{font:700 11px var(--mono);letter-spacing:.9px;text-transform:uppercase;
color:var(--soft);padding-top:3px}.vtext{font-size:15px;line-height:1.45}.vsrc{font:11px var(--mono);color:var(--soft);
margin-top:2px}.vrow.bad .vtext{border-left:3px solid var(--bad);padding-left:9px}
.stepchip{display:inline-block;font:600 11px var(--mono);padding:1px 7px;border-radius:5px;background:var(--chip);
border:1px solid var(--line);margin-right:4px;vertical-align:1px}.taskchips{display:flex;gap:6px;flex-wrap:nowrap;
overflow-x:auto;margin:0 0 10px;padding-bottom:4px}.taskchips a{white-space:nowrap;padding:4px 11px;border-radius:999px;
border:1px solid var(--line);font-size:13px;color:var(--ink)}.taskchips a.on{border-color:var(--accent);
background:var(--chip)}.taskchips i{font-style:normal;letter-spacing:-1px;margin-right:4px}.prompt{font-size:16px;
font-weight:650;margin:0 0 6px}.starthere{font:700 10px var(--mono);letter-spacing:.8px;background:var(--accent);
color:var(--panel);padding:2px 7px;border-radius:5px;margin-right:6px}
.vrow.ok .vtext{border-left:3px solid var(--good);padding-left:9px}.vrow.fix .vtext{border-left:3px solid var(--accent);
padding-left:9px}.vrow.run .vtext{color:var(--accent)}
.tabs{margin:14px 0 12px}.tabbar{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--line)}
.tab{display:flex;flex-direction:column;gap:1px;padding:6px 11px 7px;border:1px solid transparent;border-bottom:none;
border-radius:9px 9px 0 0;color:var(--soft);min-width:0;margin-bottom:-1px;white-space:nowrap}
.tab b{font-size:13px;color:var(--ink);font-weight:600}.tab span{display:none}.tab:hover{text-decoration:none;background:var(--chip)}
.tabs:not(:has(.tabp:target)) .tab.default{background:var(--panel);border-color:var(--line);color:var(--ink);
box-shadow:inset 0 2px 0 var(--accent)}
.tabp{display:none;background:var(--panel);border:1px solid var(--line);border-top:none;border-radius:0 0 12px 12px;
padding:12px 16px 16px;overflow-x:auto;scroll-margin-top:150px}.tabp>h2{margin:4px 0 2px}
.tabp:target,.tabs:not(:has(.tabp:target)) .tabp.default{display:block}
.why ol{padding-left:20px}.why li{margin:0 0 10px}.why li p{margin:2px 0}.why .mit li b{font-size:14px}
pre.cmd{background:var(--chip);border-radius:8px;padding:8px 10px;margin:4px 0;white-space:pre-wrap;word-break:break-word;
font:12.5px var(--mono)}
details.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin:0 0 12px;padding:0 16px;
overflow-x:auto}details.panel>summary{cursor:pointer;list-style:none;display:flex;gap:12px;align-items:baseline;
flex-wrap:wrap;padding:12px 0}details.panel>summary::-webkit-details-marker{display:none}
details.panel>summary h2{margin:0}details.panel>summary::before{content:"▸";color:var(--soft);font-size:12px}
details.panel[open]>summary::before{content:"▾"}.gist{color:var(--soft);font-size:13px}.pbody{padding:0 0 14px}
h3{font-size:14px;margin:16px 0 6px}pre.diff{font-size:12.5px;line-height:1.45}pre.diff .add{color:var(--good)}
pre.diff .del{color:var(--bad)}pre.diff .hunk{color:var(--accent)}pre.diff .file{font-weight:650}
@media (max-width:640px){.vrow{grid-template-columns:1fr;gap:2px}}
.split{display:grid;grid-template-columns:minmax(0,440px) minmax(0,1fr);gap:12px}
.split>.card{margin:0}
.livecard{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 14px;align-items:center}
.livecard .ribbon{grid-column:1/-1}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
.filters a{padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--soft);font-size:13px}
.filters a.on{background:var(--chip);color:var(--ink);border-color:var(--soft)}
.filters form{display:flex;gap:6px;margin-left:auto}.filters input{width:220px;padding:5px 9px}
.filters button{padding:5px 12px}.filters select{padding:5px 8px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink);font:inherit;max-width:320px}
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
""" + viz.VIZ_CSS + glance.GLANCE_CSS + seconds.SECONDS_CSS + evolvecards.CARDS_CSS

_MARK = ('<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">'
         '<rect x="1" y="3" width="6" height="6" rx="2" style="fill:var(--a1)"/>'
         '<rect x="8" y="3" width="6" height="6" rx="2" style="fill:var(--a2)"/>'
         '<rect x="15" y="3" width="6" height="6" rx="2" style="fill:var(--a3)"/>'
         '<rect x="1" y="12" width="6" height="6" rx="2" style="fill:var(--a1)"/>'
         '<rect x="8" y="12" width="6" height="6" rx="2" style="fill:var(--a2)"/>'
         '<rect x="15" y="12" width="6" height="6" rx="2" style="fill:var(--sg)"/></svg>')


def layout(title: str, body: str, *, brand: str, user: Optional[str] = None, csrf: Optional[str] = None,
           active: str = "", live: bool = False, lens: bool = False) -> str:
    top = ""
    if user:
        links = "".join(f'<a href="{path}"{" class=on" if key == active else ""}>{label}</a>'
                        for key, label, path in NAV)
        top = (f'<nav aria-label="sections">{links}</nav>'
               + ('<span id="live-status" class="badge idle" aria-live="polite">connecting…</span>' if live else "")
               + f'<span class="who">{e(user)}</span>'
               f'<form method="post" action="/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
               f'<button class="link" type="submit">Sign out</button></form>')
    script = ('<script src="/static/live.js" defer></script>' if live else "") + (
        '<script src="/static/longview.js" defer></script>' if lens else "")
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
    if s >= 3600:  # hours and days read as such, not as thousands of minutes
        from ..longrun import dur
        return dur(s)
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
    if s.get("ungraded"):
        return '<span class="badge idle" title="no check ran and no expected answer was given">– not graded</span>'
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


def _example_tag(s: dict) -> str:
    """Bundled example runs say so, and say whether they were made up or recorded."""
    ex = s.get("example")
    if not ex:
        return ""
    kind = next((k for k in ("synthetic", "recorded") if str(ex).startswith(k)), "example")
    return f'<span class="tag" title="{e(ex)}">{kind}</span>'


def _trace_sub(r) -> str:
    sess = r.summary.get("session")
    strip = viz.mini_strip((r.summary.get("insight") or {}).get("strip") or [])
    if not sess:
        return f'<div class="muted mono">{e(r.name)}</div>{strip}'
    bits = [f'{sess["asks"]} ask(s)'] + ([f'{sess["subagents"]} sub-agent(s)'] if sess["subagents"] else [])
    if sess["compactions"]:
        bits.append(f'{sess["compactions"]} context summar{"y" if sess["compactions"] == 1 else "ies"}')
    last = f'<div class="ask" title="{e(sess["ask"])}">last ask: {e(sess["ask"])}</div>' if sess["asks"] > 1 else ""
    return f'<div class="muted">{" · ".join(bits)}</div>{strip}{last}'


def _trace_rows(refs) -> str:
    rows = []
    for r in refs:
        s = r.summary
        rows.append(f'<tr><td>{_status(r)}</td><td><a href="/traces/{e(r.id)}"><strong>{e(s.get("task"))}</strong></a>'
                    f'{_example_tag(s)}{_trace_sub(r)}</td><td>{e(s.get("agent"))}</td>'
                    f'<td>{viz.lap_strip(s.get("laps"))}<div>{"" if s.get("session") else _loop_note(s.get("laps"), r.live)}</div></td>'
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
    if x.kind == "evolution":
        s = x.summary
        rates = [r for r in s.get("pass_rates") or [] if r is not None]
        trend = " → ".join(f"{r:.0%}" for r in rates[:6]) or "—"
        return (f'{e(s.get("generations"))} generation(s) · passed {e(trend)} · {e(s.get("kept"))} of '
                f'{e(s.get("tried"))} change(s) kept · {e(s.get("describe"))}')
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
def _start_here(refs: list) -> str:
    from ..insight import corpus_insight
    done = [r for r in refs if not r.live]
    items = []
    for r in done:
        ins = r.summary.get("insight") or {}
        items.append({"look_kind": ins.get("look_kind"), "fix_rule": ins.get("fix_rule"), "fix_change": ins.get("fix_change"),
                      "success": r.summary.get("success"), "agent": r.summary.get("agent"), "lines": ins.get("lines"),
                      "tests_edited": ins.get("tests_edited")})
    c = corpus_insight(items)
    if not c:
        return ""
    rows = []
    where = "; ".join(f"{v} {_WHERE.get(k, k)}" for k, v in sorted(c["kinds"].items(), key=lambda kv: -kv[1]))
    passed = sum(1 for x in items if x["success"] is True)
    ungraded = sum(1 for x in items if x["success"] is None)
    graded = passed + c["failed"]
    verdict = (f"{passed} of {graded} graded run(s) passed; {c['failed']} failed" if graded else "No run here was graded")
    if ungraded:
        verdict += (f"; {ungraded} {'were' if ungraded > 1 else 'was'} not graded (no check ran: "
                    f"agentdiff guard --check grades the sessions after it)")
    rows.append({"label": "verdict", "text": verdict + ".", "source": "outcome.success over the trace index",
                 "tone": "bad" if c["failed"] else "ok" if graded else ""})
    stuck_ungraded = [r for r in done if r.summary.get("success") is None and (r.summary.get("laps") or {}).get("stuck")]
    if stuck_ungraded:
        rows.append({"label": "loops", "text": f"{len(stuck_ungraded)} of the {ungraded} not graded went round the "
                                               f"same lap again and again: open one to see where it started.",
                     "source": "laps.stuck of each run", "tone": "bad"})
    if where:
        rows.append({"label": "where", "text": f"Of the {c['failed']} that failed: {where}.",
                     "source": "timeline.look_here of each failed run", "tone": "bad"})
    if c["top_fix"]:
        f = c["top_fix"]
        rows.append({"label": "fix", "text": f"{f['runs']} of the {c['failed']} failed run(s) point at one change to the "
                                             f"agent: {f['change']} Test it with agentdiff self-evolve.",
                     "source": f"insight.agent_fix: selfevolve.REMEDIES[{f['rule']}]", "tone": "fix"})
    code = [a for a in c["agents"] if a["median_lines"] is not None]
    if code:
        rows.append({"label": "code", "text": "; ".join(
            f"{a['agent']}: " + (f"{a['passed']}/{a['graded']} graded run(s) passed" if a.get("graded") else
                                 f"{a['runs']} run(s), none graded") + f", a median {a['median_lines']:.0f} line(s) changed"
            + (f", {a['tests_edited']} run(s) edited tests" if a["tests_edited"] else "") for a in code[:6]) + ".",
                     "source": ("the agents' own edit calls (Claude Code sessions)" if all((r.summary.get("session") for r in done))
                                else "the harness's diff of each workspace (records)"),
                     "tone": "bad" if any(a["tests_edited"] for a in code) else ""})
    return (f'<h2>Start here</h2>{verdict_html(rows)}'
            f'<p class="muted"><a href="/traces?show=failed">Every failed run →</a> · '
            f'<a href="/traces?show=stuck">the ones stuck in a loop →</a></p>')


def agents_table(refs: list) -> str:
    """Every agent: its runs and tasks, success on its 95% Wilson interval, tokens, seconds, code."""
    import statistics
    by: dict = {}
    for r in refs:
        if r.live:
            continue
        by.setdefault(str(r.summary.get("agent")), []).append(r)
    if not by:
        return ""
    rows = []
    for agent, rs in sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        graded = [r for r in rs if r.summary.get("success") is not None]
        passed = sum(1 for r in graded if r.summary.get("success"))
        toks = [r.summary.get("tokens") for r in rs if isinstance(r.summary.get("tokens"), (int, float))]
        secs = sum(r.summary.get("seconds") or 0 for r in rs)
        lines = [(r.summary.get("insight") or {}).get("lines") for r in rs]
        lines = [x for x in lines if isinstance(x, int)]
        rows.append(f'<tr><td><strong>{e(agent)}</strong></td><td class="n">{len(rs)}</td>'
                    f'<td class="n">{len({r.summary.get("task") for r in rs})}</td><td>{viz.wilson_bar(passed, len(graded))}</td>'
                    f'<td class="n">{_num(statistics.median(toks)) if toks else "—"}</td><td class="n">{_secs(secs)}</td>'
                    f'<td class="n">{f"{statistics.median(lines):.0f}" if lines else "—"}</td></tr>')
    return ('<h2>Levels · the agents</h2><div class="card"><table><tr><th>agent</th><th class="n">runs</th>'
            '<th class="n">tasks</th><th>success · 95% Wilson</th><th class="n">median tokens</th><th class="n">seconds</th>'
            '<th class="n">median lines changed</th></tr>' + "".join(rows[:30]) + '</table>'
            '<p class="muted">Success is passed over graded runs, the line its 95% Wilson interval: two agents whose '
            'lines overlap are not shown apart by these runs.</p></div>')


def guards_card(guards: list, refs: list) -> str:
    """What ``agentdiff guard`` refused in Claude Code sessions, per project:
    counts by guard, and the latest refusals, each linked to its session's trace."""
    if not guards:
        return ""
    by_sid = {}  # a guarded session's trace is named <project>-<first 8 of its session id>
    for r in refs:
        by_sid.setdefault(str(r.summary.get("task") or "").rpartition("-")[2], r)
    what = {"repeat": "a failing command rerun unchanged", "check": "a finish with edits not checked",
            "tests": "an edit to a test"}
    parts = []
    for project, g in guards:
        name = Path(project).name if project not in ("", ".") else "this root"
        counts = " · ".join(f'<strong>{e(n)}</strong> {e(what.get(k, k))}' for k, n in sorted(g["by_guard"].items()))
        rows = []
        for x in reversed(g["recent"]):
            sid = "".join(ch for ch in str(x.get("session") or "") if ch.isalnum())[:8]
            ref = by_sid.get(sid) if sid else None
            link = f'<a href="/traces/{e(ref.id)}">trace</a>' if ref else '<span class="muted">untraced</span>'
            when = _ago(x["t"]) if isinstance(x.get("t"), (int, float)) else ""
            rows.append(f'<tr><td><span class="badge">{e(x.get("guard"))}</span></td>'
                        f'<td>{e(str(x.get("reason") or "").replace("agentdiff guard: ", ""))}</td>'
                        f'<td>{link}</td><td class="muted">{e(when)}</td></tr>')
        parts.append(f'<p><strong>{e(name)}</strong> <span class="muted mono">{e(project)}</span> — '
                     f'{e(g["fired"])} refusal(s) over {e(g["sessions"])} session(s): {counts}</p>'
                     f'<table><tr><th>guard</th><th>what the agent was told</th><th></th><th>when</th></tr>'
                     + "".join(rows) + "</table>")
    return ('<h2>Guards · refused while the agent worked</h2><div class="card">' + "".join(parts)
            + '<p class="muted">Live remedies in Claude Code\'s hooks (<code>agentdiff guard --install</code>). '
              'A refusal is a reason the agent read and acted on, not a failure.</p></div>')


def _across(mine: list) -> str:
    """What keeps happening over every session: the commands that keep failing (with what they said last and
    a link to that step), the files the agents change most, and the working time by project."""
    from ..longrun import dur
    fails: Dict[str, dict] = {}
    files: Dict[str, dict] = {}
    projects: Dict[str, dict] = {}
    act: Dict[str, float] = {}
    many = len({(r.summary.get("session") or {}).get("project") for r in mine}) > 1
    for r in sorted(mine, key=lambda r: r.updated):
        dg = (r.summary.get("insight") or {}).get("digest") or {}
        proj = (r.summary.get("session") or {}).get("project") or "?"
        p = projects.setdefault(proj, {"sessions": 0, "secs": 0.0, "tokens": 0, "fails": 0})
        p["sessions"] += 1
        p["secs"] += sum((dg.get("act") or {}).values())
        p["tokens"] += int(r.summary.get("tokens") or 0)
        for k, v in (dg.get("act") or {}).items():
            act[k] = act.get(k, 0.0) + v
        for key, f in (dg.get("fails") or {}).items():
            a = fails.setdefault(key, {"n": 0, "runs": set(), "last": None})
            a["n"] += f["n"]
            a["runs"].add(r.id)
            a["last"] = (r.id, f["step"], f["line"])
            p["fails"] += f["n"]
        for path, edits, lines, test in dg.get("files") or []:
            name = f"{proj}/{path}" if many and not str(path).startswith("/") else path
            a = files.setdefault(name, {"edits": 0, "lines": 0, "runs": set(), "test": test})
            a["edits"] += edits
            a["lines"] += lines
            a["runs"].add(r.id)
    out = []
    top = sorted(fails.items(), key=lambda kv: (-len(kv[1]["runs"]), -kv[1]["n"]))[:8]
    if top:
        most = max(v["n"] for _, v in top)
        rows = "".join(
            f'<tr><td class="mono" title="{e(k)}">{e(_cut(k, 70))}</td>'
            f'<td><span class="xbar bad" style="width:{100 * v["n"] / most:.0f}%"></span></td>'
            f'<td class="n">{v["n"]}</td><td class="n">{len(v["runs"])}</td>'
            f'<td class="muted" title="{e(v["last"][2])}">{e(_cut(v["last"][2], 70))}</td>'
            f'<td><a href="/traces/{e(v["last"][0])}?at={v["last"][1]}#s{v["last"][1]}">step {v["last"][1]} →</a></td></tr>'
            for k, v in top)
        out.append('<h3>What keeps failing</h3><table class="across fails"><tr><th>command or tool</th><th></th>'
                   '<th class="n">failed</th><th class="n">sessions</th><th>what it said last</th><th></th></tr>'
                   + rows + '</table>')
    topf = sorted(files.items(), key=lambda kv: (-len(kv[1]["runs"]), -kv[1]["edits"]))[:10]
    if topf:
        most = max(v["edits"] for _, v in topf)
        rows = "".join(
            f'<tr><td class="mono" title="{e(k)}">{e(_cut(k, 70))}{" <span class=tag>test</span>" if v["test"] else ""}</td>'
            f'<td><span class="xbar" style="width:{100 * v["edits"] / most:.0f}%"></span></td>'
            f'<td class="n">{v["edits"]}</td><td class="n">{v["lines"]:,}</td><td class="n">{len(v["runs"])}</td></tr>'
            for k, v in topf)
        out.append('<h3>The files your agents change most</h3><table class="across"><tr><th>file</th><th></th>'
                   '<th class="n">edit calls</th><th class="n">lines</th><th class="n">sessions</th></tr>' + rows + '</table>')
    if projects:
        most = max(p["secs"] for p in projects.values()) or 1.0
        rows = "".join(
            f'<tr><td>{e(name)}</td><td><span class="xbar" style="width:{100 * p["secs"] / most:.0f}%"></span></td>'
            f'<td class="n">{e(dur(p["secs"]))}</td><td class="n">{p["sessions"]}</td><td class="n">{p["tokens"]:,}</td>'
            f'<td class="n">{p["fails"]}</td></tr>'
            for name, p in sorted(projects.items(), key=lambda kv: -kv[1]["secs"]))
        busy = sum(act.values()) or 1.0
        share = " · ".join(f'<span><i style="background:var(--{viz.ACTIVITIES[k][0]})"></i>{e(viz.ACTIVITIES[k][2])} '
                           f'<b>{100 * act[k] / busy:.0f}%</b></span>'
                           for k in sorted(act, key=lambda k: -act[k]) if k in viz.ACTIVITIES and act[k] / busy >= 0.01)
        stack = "".join(f'<span style="flex:{act[k]:.2f};background:var(--{viz.ACTIVITIES[k][0]})" '
                        f'title="{e(viz.ACTIVITIES[k][2])}: {e(dur(act[k]))}"></span>'
                        for k in viz.ACTIVITIES if act.get(k, 0) > 0)
        out.append('<h3>Where the working time goes</h3>'
                   f'<div class="secs"><div class="stack">{stack}</div><div class="parts">{share}</div></div>'
                   '<table class="across"><tr><th>project</th><th></th><th class="n">working</th><th class="n">sessions</th>'
                   '<th class="n">tokens</th><th class="n">failures</th></tr>' + rows + '</table>')
    return f'<div class="acrossall">{"".join(out)}</div>' if out else ""


def sessions_card(refs: list) -> str:
    """Your Claude Code sessions, read from this machine: the ones running now first."""
    mine = [r for r in refs if r.summary.get("session")]
    if not mine:
        return ""
    projects = sorted({r.summary["session"]["project"] for r in mine})
    running = sum(1 for r in mine if r.live)
    stuck = sum(1 for r in mine if (r.summary.get("laps") or {}).get("stuck"))
    calls = sum(int(r.summary.get("steps") or 0) for r in mine)
    gist = (f'{len(mine)} session(s) in {len(projects)} project(s) · {running} running now · {calls:,} steps'
            + (f' · <span class="bad">{stuck} went round a loop</span>' if stuck else ""))
    worked = glance.worked_html([r.summary.get("hours") for r in mine]) + _across(mine)
    return (f'<h2>Your Claude Code sessions</h2><div class="card"><p class="muted">{gist}. Read from Claude Code\'s '
            f'own files on this machine; nothing leaves it. Each opens on its timeline, why it went that way, and '
            f'what to change in the agent.</p>{worked}<div class="acrossall"><h3>The sessions, newest first</h3></div>'
            f'{_trace_rows(mine[:12])}'
            f'<p><a href="/traces?q=claude-code">Every session →</a></p></div>')


def overview_page(*, brand: str, user: str, csrf: str, entries: List[Entry], refs: list, ingest: dict,
                  guards: Optional[list] = None) -> str:
    everything = refs
    own = [r for r in refs if not r.summary.get("example")]
    examples = len(refs) - len(own)
    if own and examples:
        refs = own          # your runs are the headline; the bundled examples stay one click away
    evolving = [x for x in entries if x.kind == "evolution"]
    live = [r for r in refs if r.live and time.time() - r.updated <= STALL_S]
    finished = [r for r in refs if not r.live]
    judged = [r for r in finished if r.summary.get("success") is not None]
    passed = sum(1 for r in judged if r.summary.get("success"))
    stuck = sum(1 for r in finished if (r.summary.get("laps") or {}).get("stuck"))
    rate = f"{passed / len(judged):.0%}" if judged else "—"
    ungraded = [r for r in finished if r.summary.get("ungraded")]
    tiles = (f'<div class="tiles">'
             f'<div class="tile"><b>{len(live)}</b><span>running now</span></div>'
             f'<div class="tile"><b>{len(refs)}</b><span>traces</span></div>'
             + (f'<div class="tile"><b>{rate}</b><span>passed, of {len(judged)} with an outcome</span></div>' if judged
                or not ungraded else f'<div class="tile"><b>{len(ungraded)}</b><span>not graded: no check ran</span></div>')
             + f'<div class="tile"><b class="{"bad" if stuck else ""}">{stuck}</b><span>stuck in a loop</span></div>'
             f'<div class="tile"><b>{len(entries)}</b><span>runs: duels, reports, evals</span></div></div>')
    live_html = (_trace_rows(live[:8]) if live else
                 '<p class="muted">Nothing running. Start one — <code>agentdiff "the task"</code> — and it appears '
                 'here as it goes; the <a href="/live">Live</a> page follows it step by step.</p>')
    loops = [r for r in finished if (r.summary.get("laps") or {}).get("stuck")][:6]
    others = [r for r in finished if not r.summary.get("session")]
    sub = ("Everything under this hub's root, read from disk"
           + (f"; the numbers are your runs, not the {examples} bundled example(s), which are under "
              f"<a href=\"/traces\">Traces</a>" if refs is not everything else "") + ".")
    body = (f'<h1>Overview</h1><p class="sub">{sub}</p>{tiles}{sessions_card(refs)}'
            f'{_start_here(refs)}{agents_table(refs)}<h2>Running now</h2><div class="card">{live_html}</div>'
            f'{guards_card(guards or [], refs)}'
            + (f'<h2>Agents evolving</h2>{_entry_cards(evolving[:4])}<p><a href="/evolve">Every evolving harness →</a></p>'
               if evolving else "")
            + (f'<h2>Stuck in a loop</h2><div class="card">{_trace_rows(loops)}</div>' if loops else "")
            + f'<h2>Recent traces</h2><div class="card">{_trace_rows(others[:10]) if others else "<p class=muted>None yet besides your sessions above.</p>"}'
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
    traces = (f'<h2>Its traces, lap by lap <span class="muted">· <a href="/timeline?run={e(entry.id)}">on one clock →'
              f'</a></span></h2><div class="card">{_trace_rows(refs)}</div>' if refs else "")
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
    traces = (f'<h2>Its traces <span class="muted">· <a href="/timeline?run={e(entry.id)}">on one clock →</a></span></h2>'
              f'<div class="card">{_trace_rows(refs)}</div>' if refs else "")
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
    from .urls import quote
    blocks = "".join(f'<h2>{e(g)} <span class="muted">· {len(rs)} · <a href="/timeline?g={e(quote(g))}">'
                     f'on one clock →</a></span></h2><div class="card">{_trace_rows(rs)}</div>'
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


#: steps above which a run's table shows a window, not every step
WINDOW_STEPS = 120


def _steps_table(steps: List[dict], most: int = 400, focus: Optional[int] = None, every: bool = False,
                 every_href: str = "", span: Optional[tuple] = None) -> str:
    """Every step; for a long run, the opening, the stretch around where to look
    first and the ending, with what is left out said and a link to all of it."""
    from ..laps import _activity
    rows = []
    keep = None
    if len(steps) > WINDOW_STEPS and not every:
        keep = set(range(0, 10)) | set(range(max(0, len(steps) - 10), len(steps)))
        if focus is not None:
            keep |= set(range(max(0, focus - 15), min(len(steps), focus + 16)))
        if span is not None:  # the stretch the long view opened: every step of it, up to 600
            keep |= set(range(max(0, span[0]), min(len(steps), span[1] + 1, span[0] + 600)))
        most = len(steps)
    gap = 0
    for i, s in enumerate(steps[:most]):
        if keep is not None and i not in keep:
            gap += 1
            continue
        if gap:
            rows.append(f'<tr><td></td><td colspan="6" class="muted">⋯ {gap} step(s) not shown here; '
                        f'<a href="{e(every_href)}">show every step</a> or open one from the timeline</td></tr>')
            gap = 0
        act = _activity(s)
        cls, glyph, label = viz.ACTIVITIES.get(act, viz.ACTIVITIES["other"])
        err = s.get("error")
        rows.append(f'<tr id="s{i}"><td class="n">{i}</td><td><span class="legend"><span><i class="{cls}">{e(glyph)}</i>'
                    f'{e(label)}</span></span></td><td>{e(s.get("name") or s.get("type"))}</td>'
                    f'<td class="clip" title="{e(str(s.get("input") or "")[:600])}">{e(str(s.get("input") or "")[:200])}</td>'
                    f'<td class="clip{" bad" if err else ""}" title="{e(str(err or s.get("output") or "")[:600])}">'
                    f'{e(str(err or s.get("output") or "")[:200])}</td>'
                    f'<td class="n">{_secs(s.get("latency_s"))}</td><td class="n">{_num(s.get("tokens"))}</td></tr>')
    more = f'<p class="muted">{len(steps) - most} more step(s) not shown.</p>' if len(steps) > most else ""
    return ('<table><tr><th class="n">#</th><th>activity</th><th>step</th><th>input</th><th>output</th>'
            '<th class="n">time</th><th class="n">tokens</th></tr>' + "".join(rows) + "</table>" + more)


#: the trace page's panels, in page order: key -> title
PANELS = (("long", "Phases: sessions, bursts and loops"), ("map", "Trajectory map"), ("trunk", "The run as a trunk"), ("compare", "Two runs on one axis"),
          ("code", "The code it produced"), ("seconds", "Where the seconds went"), ("reward", "Reward & credit"),
          ("lanes", "Every thread on its own lane"), ("laps", "The loop, lap by lap"),
          ("flow", "How it moved between tools"), ("steps", "The steps"))
#: which panels each preset opens; the others stay one click away
PRESETS = (("focus", "focus", ("map", "trunk", "compare", "code", "steps")),
           ("loop", "the loop", ("trunk", "laps", "flow")), ("time", "time", ("seconds", "lanes", "trunk")),
           ("threads", "threads", ("trunk", "lanes")), ("code", "the code", ("code", "map", "steps")),
           ("training", "training", ("reward", "trunk")), ("long", "phases", ("long", "steps")),
           ("all", "everything", tuple(k for k, _ in PANELS)))


def verdict_html(rows: list) -> str:
    """The card a run starts with: what happened, where, what it cost and changed, what to change next."""
    out = []
    for r in rows:
        step = (f'<a class="stepchip" href="#s{e(r["step"])}">step {e(r["step"])}</a> ' if r.get("step") is not None else "")
        out.append(f'<div class="vrow {e(r.get("tone") or "")}"><span class="vlab">{e(r["label"])}</span>'
                   f'<div><div class="vtext">{step}{e(r["text"])}</div><div class="vsrc">from {e(r["source"])}</div></div></div>')
    return f'<div class="vcard" aria-label="the verdict">{"".join(out)}</div>'


def _patch_html(patch: str, most: int = 600) -> str:
    lines = patch.splitlines()
    rows = []
    for ln in lines[:most]:
        cls = ("file" if ln.startswith(("+++", "---", "diff ")) else "hunk" if ln.startswith("@@")
               else "add" if ln.startswith("+") else "del" if ln.startswith("-") else "")
        rows.append(f'<span class="{cls}">{e(ln)}</span>')
    more = f'\n… {len(lines) - most} more line(s)' if len(lines) > most else ""
    return f'<pre class="diff">{chr(10).join(rows)}{e(more)}</pre>'


def code_panel(change: Optional[dict], *, compare: Optional[dict] = None, other: Optional[dict] = None,
               act: Optional[dict] = None, chart: str = "", href: str = "") -> str:
    if change is not None and change.get("source") == "steps":
        return _steps_code(change, chart=chart, href=href, compare=compare, other=other)
    if change is None:
        return ('<p class="muted">No record of its workspace: the code a run produces is captured when it runs '
                'under the harness (<code>agentdiff duel</code>, <code>fix</code>, <code>self-evolve</code>), which '
                'diffs the workspace before and after.</p>')
    flags = "".join(f'<p class="note error">{e(f["sentence"])}</p>' for f in change["flags"])
    rows = "".join(f'<tr><td class="mono">{e(f["path"])}{" <span class=badge>test</span>" if f["test"] else ""}</td>'
                   f'<td>{e(f["status"])}</td><td class="n ok">+{f["added"]}</td><td class="n bad">−{f["removed"]}</td></tr>'
                   for f in change["files"])
    table = (f'<table><tr><th>file</th><th>how</th><th class="n">added</th><th class="n">removed</th></tr>{rows}</table>'
             if rows else '<p class="muted">It changed no file.</p>')
    check = ""
    if change.get("check"):
        verdict = "passed" if change["passed"] else "failed" if change["passed"] is False else "did not say"
        fails = ("".join(f"<li><code>{e(x)}</code></li>" for x in change.get("failures") or []))
        check = (f'<p>The check <code>{e(change["check"])}</code> <strong class="{"ok" if change["passed"] else "bad"}">'
                 f'{verdict}</strong> on its workspace.</p>'
                 + (f'<p>The cases its code got wrong, as the check named them:</p><ul>{fails}</ul>' if fails else "")
                 + ''
                 + (f'<details><summary class="muted">the end of what the check printed</summary>'
                    f'<pre>{e(change["check_tail"])}</pre></details>' if change.get("check_tail") else ""))
    patch = ""
    if change.get("patch"):
        n = change["patch"].count("\n")
        patch = (f'<details{" open" if n <= 160 else ""}><summary class="muted">the patch, {n} line(s)</summary>'
                 f'{_patch_html(change["patch"])}</details>')
    cmp_html = ""
    if compare and other:
        cmp_html = (f'<h3>Beside the other run\'s change</h3><p>{e(compare["sentence"])} A changed '
                    f'{compare["lines"][0]} line(s), B {compare["lines"][1]}.</p>'
                    + (f'<details><summary class="muted">B\'s patch</summary>{_patch_html(other["patch"])}</details>'
                       if other.get("patch") else ""))
    action = ""
    if act:
        action = (f'<h3>{e(act["title"])}</h3><p class="muted">{e(act["why"])}</p><pre>{e(act["command"])}</pre>')
    return f'{flags}{table}{check}{patch}{cmp_html}{action}'


def _steps_code(change: dict, *, chart: str, href: str, compare: Optional[dict], other: Optional[dict]) -> str:
    """The change a run made, read from its own edit calls (insight.code_from_steps)."""
    n_calls = sum(f["edits"] for f in change["files"])
    shell = change.get("shell_edits") or 0
    note = (f'<p class="muted">Read from the agent\'s own edit calls: {n_calls} call(s) over {len(change["files"])} '
            f'file(s). This is what it asked its tools to write, not a diff of the workspace'
            + (f'; {shell} more edit(s) went through shell commands (<code>sed -i</code>, a heredoc, a redirect), which '
               f'name no file this reading can trust, so they are not in these counts' if shell else "")
            + (f'; {change["failed_edits"]} edit call(s) failed and count for nothing' if change.get("failed_edits") else "")
            + '.</p>')
    flags = "".join(f'<p class="note error">{e(f["sentence"])}</p>' for f in change["flags"])
    rows = []
    for f in sorted(change["files"], key=lambda f: -(f["added"] + f["removed"])):
        first = f["steps"][0] if f["steps"] else None
        link = f' <a href="{e(href)}?at={first}#s{first}" class="muted">step {first} →</a>' if first is not None and href else ""
        rows.append(f'<tr><td class="mono">{e(f["path"])}{" <span class=tag>test</span>" if f["test"] else ""}</td>'
                    f'<td>{e(f["status"])}</td><td class="n">{f["edits"]}</td><td class="n ok">+{f["added"]}</td>'
                    f'<td class="n{" bad" if f["removed"] else " muted"}">−{f["removed"]}</td><td>{link}</td></tr>')
    table = ('<table><tr><th>file</th><th>how</th><th class="n">edit calls</th><th class="n">added</th>'
             '<th class="n">removed</th><th></th></tr>' + "".join(rows) + '</table>')
    check = ""
    if change.get("check"):
        verdict = ("passed" if change["passed"] else "failed" if change["passed"] is False
                   else "printed nothing that says whether it passed")
        cls = "ok" if change["passed"] else "bad" if change["passed"] is False else "muted"
        step = change.get("check_step")
        check = (f'<p>The last check it ran, <code>{e(_cut(change["check"], 140))}</code>, <strong class="{cls}">'
                 f'{verdict}</strong>' + (f' (<a href="{e(href)}?at={step}#s{step}">step {step}</a>)' if step is not None
                                          and href else "") + '.</p>'
                 + ("<p>The cases it named as failing:</p><ul>" + "".join(f"<li><code>{e(x)}</code></li>"
                                                                         for x in change["failures"]) + "</ul>"
                    if change.get("failures") else ""))
    patch = ""
    if change.get("patch"):
        n = change["patch"].count("\n") + 1
        patch = (f'<details{" open" if n <= 160 else ""}><summary class="muted">the edits in the order it made them, '
                 f'{n} line(s)</summary>{_patch_html(change["patch"])}</details>')
    cmp_html = ""
    if compare and other:
        cmp_html = (f'<h3>Beside the other run\'s change</h3><p>{e(compare["sentence"])} A changed '
                    f'{compare["lines"][0]} line(s), B {compare["lines"][1]}.</p>')
    return f'{note}{flags}{chart}{table}{check}{patch}{cmp_html}'


def _cut(text: str, most: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[: most - 1] + "…"


def _panel(key: str, title: str, gist: str, body: str, open_: bool, start: bool = False) -> str:
    return (f'<details class="panel" id="p-{key}"{" open" if open_ else ""}><summary>'
            f'{"<span class=starthere>START HERE</span>" if start else ""}<h2>{e(title)}</h2>'
            f'<span class="gist">{e(gist)}</span></summary><div class="pbody">{body}</div></details>')


def task_chips(ref, refs: list) -> str:
    """Every task in this run's directory, a dot per agent run (● passed, ○ failed, ◐ running)."""
    by: dict = {}
    for r in refs:
        if r.group == ref.group:
            by.setdefault(str(r.summary.get("task")), []).append(r)
    if len(by) < 2:
        return ""
    chips = []
    for task, rs in sorted(by.items()):
        rs = sorted(rs, key=lambda r: (str(r.summary.get("agent")), r.name))
        dots = "".join("◐" if r.live else "●" if r.summary.get("success") else "○" for r in rs[:6])
        target = next((r for r in rs if r.summary.get("agent") == ref.summary.get("agent")), rs[0])
        on = task == str(ref.summary.get("task"))
        chips.append(f'<a href="/traces/{e(target.id)}"{" class=on" if on else ""} title="{e(task)}: '
                     f'{sum(1 for r in rs if r.summary.get("success"))} of {len(rs)} passed"><i>{dots}</i>{e(task[:28])}</a>')
    return f'<nav class="taskchips" aria-label="the tasks here">{"".join(chips)}</nav>'


def _picker(ref, others: list, vs: str, axis: str, view: str) -> str:
    if not others:
        return ""
    opts = "".join(f'<option value="{e(r.id)}"{" selected" if r.id == vs else ""}>{e(r.summary.get("agent"))} · '
                   f'{e(r.name)}{" (running)" if r.live else ""}</option>' for r in others)
    return (f'<form method="get" action="/traces/{e(ref.id)}" class="filters" style="margin-top:10px">'
            f'<input type="hidden" name="view" value="{e(view)}">'
            f'<label class="muted" for="vs">compare with</label><select id="vs" name="vs"><option value="">—</option>'
            f'{opts}</select><select name="axis" aria-label="axis">'
            f'<option value="step"{" selected" if axis != "time" else ""}>by step</option>'
            f'<option value="time"{" selected" if axis == "time" else ""}>by time</option></select>'
            f'<button type="submit">Compare</button></form>')


def _glance(ref, data: dict, tl: dict, long: Optional[dict]) -> str:
    """The run at a glance, above its verdict (agentdiff.hub.glance)."""
    if not tl or not tl.get("steps"):
        return ""
    r = (long or {}).get("r")
    if r is None:
        from ..longrun import longrun
        try:
            r = longrun(data)
        except (ValueError, KeyError, TypeError):
            return ""
    return glance.glance_html(data, tl, r, href=f"/traces/{ref.id}")


def asks_rows(data: dict) -> List[dict]:
    """Each prompt a person typed (``turns``, :mod:`agentdiff.claude_sessions`) and what the agent did until the next:
    its steps, how long they ran, the tools, the edits, the checks and how they ended, the errors."""
    from ..laps import _activity, check_outcome
    steps = data.get("steps") or []
    turns = [t for t in data.get("turns") or [] if isinstance(t, dict) and isinstance(t.get("step"), int)]
    rows = []
    for i, t in enumerate(turns):
        lo = t["step"]
        hi = turns[i + 1]["step"] if i + 1 < len(turns) else len(steps)
        mine = steps[lo:hi]
        tools: Dict[str, int] = {}
        edits = checks = failed = errors = 0
        end = float(t.get("at_s") or 0)
        for st in mine:
            end = max(end, float(st.get("started_s") or 0) + float(st.get("latency_s") or 0))
            if st.get("type") != "tool_call":
                continue
            tools[str(st.get("name"))] = tools.get(str(st.get("name")), 0) + 1
            kind = _activity(st)
            edits += kind == "edit"
            if kind == "verify":
                checks += 1
                failed += check_outcome(st) is False
            errors += bool(st.get("error"))
        rows.append({"n": i + 1, "prompt": str(t.get("prompt") or ""), "step": lo, "steps": len(mine),
                     "secs": max(0.0, end - float(t.get("at_s") or 0)), "at": float(t.get("at_s") or 0),
                     "tools": sorted(tools.items(), key=lambda kv: -kv[1])[:3], "calls": sum(tools.values()),
                     "edits": edits, "checks": checks, "failed": failed, "errors": errors})
    return rows


def asks_body(data: dict, href: str = "") -> str:
    rows = asks_rows(data)
    if not rows:
        return ""
    most = max(r["secs"] for r in rows) or 1.0
    out = []
    for r in rows:
        w = 4 + 96 * (r["secs"] / most) ** 0.5
        chk = (f'{r["checks"]} check(s)' + (f', <span class="bad">{r["failed"]} failed</span>' if r["failed"] else "")
               if r["checks"] else '<span class="muted">no check</span>')
        tools = ", ".join(f'{e(n)} ×{c}' for n, c in r["tools"]) or "—"
        out.append(f'<tr><td class="n">{r["n"]}</td><td><div class="askp" title="{e(r["prompt"])}">{e(r["prompt"])}</div>'
                   f'<div class="muted">{e(tools)}</div></td>'
                   f'<td><span class="askbar" style="width:{w:.0f}px"></span> {e(_secs(r["secs"]))}</td>'
                   f'<td class="n">{r["steps"]}</td><td class="n">{r["edits"]}</td><td>{chk}'
                   + (f'<div class="bad">{r["errors"]} error(s)</div>' if r["errors"] else "")
                   + f'</td><td class="nowrap"><a href="{e(href)}?at={r["step"]}#s{r["step"]}">step {r["step"]} →</a>'
                   f'</td></tr>')
    return ('<p class="muted">Each thing you asked, and what the agent did until your next ask: the bar is its time '
            '(square-root scale), the tools are its three most used.</p>'
            '<table class="asks"><tr><th class="n">#</th><th>you asked</th><th>time</th><th class="n">steps</th>'
            '<th class="n">edits</th><th>checks</th><th></th></tr>' + "".join(out) + '</table>')


def trace_panel(*, ref, data: dict, lap: dict, tl: Optional[dict] = None, cmp: Optional[dict] = None,
                others: list = (), vs: str = "", axis: str = "step", every: bool = False, view: str = "focus",
                card: Optional[list] = None, change: Optional[dict] = None, code_cmp: Optional[dict] = None,
                other_change: Optional[dict] = None, act: Optional[dict] = None, other_data: Optional[dict] = None,
                al: Optional[dict] = None, task_nav: str = "", long: Optional[dict] = None,
                extra_open: tuple = (), fix: Optional[dict] = None, at: Optional[int] = None) -> str:
    """The part of a trace's page that moves while it runs: the card, then the panels."""
    from .urls import quote
    s = ref.summary
    steps = data.get("steps") or []
    tl = tl or {}
    head = (f'<div class="row">{_status(ref)}<span class="muted">{e(s.get("agent"))}'
            + (f' · {e(s.get("model"))}' if s.get("model") else "")
            + f' · {e(len(steps))} steps · {_num(s.get("tokens"))} tokens · '
            f'{_secs(s.get("elapsed_s") if ref.live else s.get("seconds"))}'
            f' · updated {e(_ago(ref.updated))}</span></div>')
    notes = ""
    if (data.get("source") or {}).get("format") == "agentdiff-int":
        notes = ('<p class="note">This trace came in-band: sizes, times and outcomes only. A check\'s pass or '
                 'fail is known only when the tool itself failed, and two laps count as the same when they '
                 'called the same tools, since the arguments never travelled.</p>')
    opened = dict((k, set(ks)) for k, _, ks in PRESETS).get(view) or set(dict((k, ks) for k, _, ks in PRESETS)["focus"])
    keep = f"&vs={quote(vs)}&axis={quote(axis)}" if vs else ""
    chips = "".join(f'<a href="/traces/{e(ref.id)}?view={k}{e(keep)}"{" class=on" if k == view else ""}>{e(label)}</a>'
                    for k, label, _ in PRESETS)
    tabs: List[tuple] = []

    def add(key: str, title: str, gist: str, body: str, open_: bool = False, start: bool = False) -> None:
        tabs.append((key, title, gist, body))
    opened = set(opened) | set(extra_open)
    # every view is drawn; only a run of thousands upon thousands of steps draws its heaviest on request
    big = len(steps) > HUGE_STEPS

    def drawn(key: str, make) -> str:
        """A big run's closed panel is drawn when it is opened: a link, not thousands of marks."""
        if not big or key in opened:
            return make()
        return (f'<p class="muted">{len(steps):,} steps: this view is drawn when asked for. '
                f'<a href="/traces/{e(ref.id)}?view={e(view)}{e(keep)}&amp;open={e(key)}#p-{e(key)}">Draw it</a>.</p>')
    if long:
        # phases are a view of any run, opened by its chip or a link into it; never forced on a run for its length
        show = "long" in opened or bool(long.get("win") or long.get("zoom"))
        add("long", "Phases", _long_gist(long["r"]),
            long_body(ref, long, view=view, vs=vs) if show or not big else drawn("long", lambda: ""), show)
    folded = f', {tl["folded_steps"]} quiet steps folded' if tl.get("folded_steps") else ""
    if cmp and other_data is not None and al:
        here_b = (cmp["b"].get("look_here") or {}).get("index")
        body = (f'<p class="muted">{e(al["sentence"])} Each column is its run in its own order; a line joins the '
                f'steps that made the same call. A step of A opens below.</p>'
                + drawn("map", lambda: viz.trajectory_map(
                    data, other_data, al, names=(str(cmp["a"].get("agent")), str(cmp["b"].get("agent"))),
                    heres=((tl.get("look_here") or {}).get("index"), here_b, (tl.get("look_here") or {}).get("kind"),
                           (cmp["b"].get("look_here") or {}).get("kind")))))
        add("map", "Trajectory map", f'{al["matched"]} step(s) shared'
                             + (f', parting at row {al["divergence"]}' if al["divergence"] is not None else ""),
                             body, "map" in opened, start=True)
    trunk_body = drawn("trunk", lambda: (
        f'<p class="muted">The trunk is the run on its {e(tl.get("basis"))} clock: thinking on it, each tool '
        f'call a branch ending in a leaf, each sub-agent hanging off it where it first acted. Every leaf '
        f'opens its step.</p>{viz.trunk_svg([tl], axis="time")}' if tl else "")) + _picker(ref, list(others), vs, axis, view)
    gist_trunk = (f'{len(tl.get("lanes") or [])} thread(s) · {len(tl.get("laps") or [])} lap(s) · '
                  f'{_secs(tl.get("span_s"))}{folded}')
    add("trunk", "The run as a trunk", gist_trunk, trunk_body, "trunk" in opened)
    if cmp:
        d = cmp.get("diverged_at")
        body = (f'<p class="note">{e(cmp["sentence"])}</p>'
                + drawn("compare", lambda: viz.trunk_svg([cmp["a"], cmp["b"]], cmp=cmp, axis=axis)
                        + viz.pair_timeline(cmp, axis=axis))
                + f'<p class="muted">A: {e(cmp["a"].get("agent"))} '
                f'({e(cmp["a"]["look_here"]["sentence"] if cmp["a"].get("look_here") else "")}) · B: '
                f'{e(cmp["b"].get("agent"))} ({e(cmp["b"]["look_here"]["sentence"] if cmp["b"].get("look_here") else "")})'
                + (f' · <a href="#s{e(d)}">step {e(d)} →</a>' if d is not None else "") + '</p>')
        gist = f"first difference at step {d}" if d is not None else "the same calls throughout"
        add("compare", "Two runs on one axis", gist, body, "compare" in opened)
    gist_code = (f'+{change["added"]} −{change["removed"]} in {len(change["files"])} file(s)'
                 + (f' · {len(change["flags"])} flag(s)' if change["flags"] else "") if change else "not captured")
    files_chart = ""
    if change and change.get("source") == "steps" and tl:
        r_long = (long or {}).get("r")
        if r_long is None:
            from ..longrun import longrun
            r_long = longrun(data)
        files_chart = glance.files_html(data, tl, r_long, change, href=f"/traces/{ref.id}")
    add("code", "The code it produced", gist_code,
                         code_panel(change, compare=code_cmp, other=other_change, act=act, chart=files_chart,
                                    href=f"/traces/{ref.id}"), "code" in opened)
    secs_runs = [tl] + ([cmp["b"]] if cmp else [])
    secs_names = ([f'A · {tl.get("agent") or "run"}', f'B · {cmp["b"].get("agent") or "run"}'] if cmp
                  else [str(tl.get("agent") or "run")])
    add("seconds", "Where the seconds went", f'{_secs(tl.get("span_s"))} on its clock'
                         + (f' against {_secs(cmp["b"].get("span_s"))}' if cmp else ""),
                         '<p class="muted">The time each step took, added up: by activity, by tool, by agent, and '
                         'the slowest steps. Idle time between steps is in no bar.'
                         + (' A is the full bar, B the thin one below it, on one scale.' if cmp else '') + '</p>'
                         + drawn("seconds", lambda: seconds.seconds_html(secs_runs, secs_names, href=f"/traces/{ref.id}")),
                         "seconds" in opened)
    rw = viz.reward_steps(secs_runs, secs_names)
    if rw:
        add("reward", "Reward & credit", "the return, step by step",
                             '<p class="muted">The return as it accumulated, one step at a time, from the rewards the '
                             'trace recorded.</p>' + rw, "reward" in opened)
    add("lanes", "Every thread on its own lane", f'{len(tl.get("lanes") or [])} lane(s){folded}',
                         drawn("lanes", lambda: viz.run_timeline(tl, live=ref.live) if tl else ""), "lanes" in opened)
    lap_gist = (lap.get("summary") or "").split(";")[0]
    add("laps", "The loop, lap by lap", lap_gist,
                         f'<p class="muted">{e(lap.get("summary"))}</p>'
                         + drawn("laps", lambda: viz.lap_chart(lap) + viz.lap_table(lap)), "laps" in opened)
    moves = lap.get("transitions") or []
    tools = len({m["from"] for m in moves} | {m["to"] for m in moves})
    add("flow", "How it moved between tools", f"{tools} tool(s), {sum(m['count'] for m in moves)} move(s)",
                         drawn("flow", lambda: viz.flow_ring(lap)), "flow" in opened)
    focus = at if at is not None and 0 <= at < len(steps) else (tl.get("look_here") or {}).get("index")
    title = "Every step" if len(steps) <= WINDOW_STEPS or every else "The steps that matter"
    steps_html = (f'<section class="card steps" id="steps"><h2>{e(title)}</h2><p class="gist">{len(steps)} step(s)'
                  + ("" if len(steps) <= WINDOW_STEPS or every else ", a window around where to look") + '</p>'
                  + _steps_table(steps, focus=focus, every=every, every_href=f"/traces/{ref.id}?steps=all&view={view}#s0",
                                 span=_long_span(long)) + '</section>')
    # the start: what happened, where, what it cost, what to change; the first tab
    start_gist = next((r["text"] for r in card or [] if r.get("label") == "verdict"), "the verdict")
    tabs.insert(0, ("start", "Start here", start_gist, _glance(ref, data, tl, long) + verdict_html(card or []) + notes))
    asked = asks_body(data, f"/traces/{ref.id}")
    if asked:
        n = len(data.get("turns") or [])
        tabs.insert(1, ("asks", "What you asked", f"{n} ask(s), each with its time, steps and checks", asked))
    default = {"loop": "laps", "time": "seconds", "threads": "lanes", "code": "code", "training": "reward",
               "long": "long"}.get(view, "start")
    if extra_open:
        default = extra_open[0]
    if long and (long.get("win") or long.get("zoom")):
        default = "long"
    if default not in {k for k, *_ in tabs}:
        default = "start"
    from ..insight import mitigation
    mit = mitigation(data, tl, lap=lap, phases=(long or {}).get("r"), change=change, fix=fix, act=act)
    prompt = str((data.get("task") or {}).get("prompt") or "").strip().splitlines()
    prompt_html = f'<p class="prompt">{e(prompt[0][:300])}</p>' if prompt else ""
    return (f'{task_nav}{prompt_html}{head}{tabs_html(tabs, default)}{mitigation_html(mit)}{steps_html}')


#: steps past which a run draws its heaviest views when asked, not on every page load
HUGE_STEPS = 5000


def tabs_html(tabs: List[tuple], default: str) -> str:
    """Every view in its own tab, with no script: a tab is a link to its panel,
    the panel it names shows (``:target``), and the default one shows until then."""
    bar, panes, rules = [], [], []
    for key, title, gist, body in tabs:
        on = " default" if key == default else ""
        bar.append(f'<a class="tab{on}" href="#p-{e(key)}" role="tab" title="{e(gist)}"><b>{e(title)}</b>'
                   f'<span>{e(gist)}</span></a>')
        head = "" if key == "start" else f'<h2>{e(title)}</h2><p class="gist">{e(gist)}</p>'
        panes.append(f'<section class="tabp{on}" id="p-{e(key)}" role="tabpanel" aria-label="{e(title)}">'
                     f'{head}{body}</section>')
        rules.append(f'.tabs:has(#p-{key}:target) .tab[href="#p-{key}"]')
    style = ("<style>" + ",".join(rules) + "{background:var(--panel);border-color:var(--line);color:var(--ink);"
             "box-shadow:inset 0 2px 0 var(--accent)}</style>")
    return (f'<div class="tabs">{style}<nav class="tabbar" role="tablist" aria-label="views">{"".join(bar)}</nav>'
            f'<div class="tabpanels">{"".join(panes)}</div></div>')


def mitigation_html(mit: dict) -> str:
    """Why the run went the way it did, and the steps that change the agent."""
    reasons = "".join(
        f'<li>{e(r["text"])}'
        + (f' <a class="stepchip" href="#s{e(r["step"])}">step {e(r["step"])}</a>' if r.get("step") is not None else "")
        + f'<div class="vsrc">from {e(r["source"])}</div></li>' for r in mit.get("reason") or [])
    steps = "".join(
        f'<li><b>{e(st["title"])}</b><p>{e(st["text"])}</p>'
        + (f'<pre class="cmd">{e(st["command"])}</pre>' if st.get("command") else "")
        + f'<div class="vsrc">from {e(st["source"])}</div></li>' for st in mit.get("steps") or [])
    return (f'<section class="card why" id="why"><h2>Why it went this way</h2>'
            + (f'<ol class="reasons">{reasons}</ol>' if reasons else '<p class="muted">Nothing on the record to explain.</p>')
            + f'<h2>What to change in the agent</h2><ol class="mit">{steps}</ol>'
            f'<p class="muted">{e(mit.get("basis"))}.</p></section>')


def trace_page(*, brand: str, user: str, csrf: str, ref, data: dict, lap: dict, **panel) -> str:
    from .urls import quote
    s = ref.summary
    vs, axis, view = panel.get("vs") or "", panel.get("axis") or "step", panel.get("view") or "focus"
    q = f"?view={quote(view)}" + (f"&vs={quote(vs)}&axis={quote(axis)}" if vs else "")
    long = panel.get("long") or {}
    for k in ("burst", "session", "page", "t0", "t1"):
        if long.get(k) not in (None, 0) or (k == "page" and long.get("page")):
            q += f"&{k}={quote(str(long[k]))}"
    body = (f'<p class="muted"><a href="/traces">Traces</a> / {e(ref.group)} · '
            f'<a href="/timeline?g={e(quote(ref.group))}">every run here on one clock</a></p>'
            f'<h1>{e(s.get("task"))}</h1>'
            f'<div data-live="/traces/{e(ref.id)}/panel{e(q)}" data-ids="{e(ref.id)}">'
            f'{trace_panel(ref=ref, data=data, lap=lap, **panel)}</div>'
            f'<p class="muted mono">{e(ref.path.name)} · <a href="/api/v1/traces/{e(ref.id)}">JSON</a></p>')
    return layout(str(s.get("task")), body, brand=brand, user=user, csrf=csrf, active="traces", live=ref.live,
                  lens=lens_open(panel))


def lens_open(panel: dict) -> bool:
    """Whether a trace page shows its phases open, and so runs the lens. A page
    with the panel closed runs no script; its phases are drawn on the server."""
    long = panel.get("long") or {}
    return bool(long) and (panel.get("view") == "long" or "long" in (panel.get("extra_open") or ())
                           or bool(long.get("win") or long.get("zoom")))


# ----------------------------------------------------------------- long runs
def _long_gist(r: dict) -> str:
    from ..longrun import dur
    bits = [f'{dur(r["span_s"])} on the clock', f'{dur(r["active_s"])} working', f'{len(r["sessions"])} session(s)',
            f'{len(r["bursts"])} burst(s)', f'{r["calls"]:,} calls']
    if r.get("loops"):
        lp = max(r["loops"], key=lambda x: x["active_s"])
        bits.append(f'↻ a loop of {lp["count"]} bursts')
    return " · ".join(bits)


def _long_span(long: Optional[dict]) -> Optional[tuple]:
    if not long or not long.get("win") or not long["win"]["steps"]:
        return None
    idx = [x["index"] for x in long["win"]["steps"]]
    page = long.get("page") or 0
    return (min(idx) + 600 * page, max(idx))


def long_body(ref, long: dict, *, view: str = "focus", vs: str = "") -> str:
    """The long run's panel: the lens over the whole run (or the stretch asked
    for), then that stretch's calls, the story, the rhythm, the pace, where the
    time went, and the other run beside it."""
    from ..longrun import dur, when
    from . import longviz
    from .urls import quote
    r = long["r"]
    sa, span = r.get("started_at"), r["span_s"]
    base = f"/traces/{ref.id}?view={quote(view)}" + (f"&vs={quote(vs)}" if vs else "")
    zoom = long.get("zoom")
    win = long.get("win")
    parts = [f'<p class="note">{e(r["sentence"])}</p>']
    if r.get("in_progress") and r.get("quiet_s") is not None:
        parts.append(f'<p class="muted">Running; quiet for {e(dur(r["quiet_s"]))} since its last step.</p>')
    crumbs = [f'<a href="{e(base)}#p-long">the whole run</a>']
    title = ""
    if long.get("session"):
        s = r["sessions"][long["session"] - 1]
        crumbs.append(f'session {s["n"]}')
        title = (f'Session {s["n"]}: {when(s["from"], sa, span)} to {when(s["to"], sa, span)} · {dur(s["seconds"])} · '
                 f'{s["calls"]:,} calls in {len(s["bursts"])} burst(s)')
    elif long.get("burst"):
        b = r["bursts"][long["burst"] - 1]
        crumbs.append(f'<a href="{e(base)}&amp;session={b["session"]}#p-long">session {b["session"]}</a>')
        crumbs.append(f'burst {b["n"]}')
        nav = []
        if b["n"] > 1:
            nav.append(f'<a href="{e(base)}&amp;burst={b["n"] - 1}#p-long">← burst {b["n"] - 1}</a>')
        if b["n"] < len(r["bursts"]):
            nav.append(f'<a href="{e(base)}&amp;burst={b["n"] + 1}#p-long">burst {b["n"] + 1} →</a>')
        title = (f'Burst {b["n"]}: {when(b["from"], sa, span)} · {dur(b["seconds"])} · {b["calls"]:,} calls · '
                 f'{b["status"]}' + (f' · {b["note"]}' if b["note"] else ""))
        crumbs.append(" · ".join(nav))
    elif zoom:
        crumbs.append(f'{when(zoom[0], sa, span)} to {when(zoom[1], sa, span)}')
        title = f'{when(zoom[0], sa, span)} to {when(zoom[1], sa, span)}'
    parts.append(f'<p class="crumbs muted">{" / ".join(crumbs)}</p>')
    # the lens: the script draws it from /api/v1/traces/<id>/long; without it, the line below is the view
    parts.append(f'<div class="lens" data-lens="/api/v1/traces/{e(ref.id)}/long" data-base="{e(base)}" '
                 f'data-burst="{e(long.get("burst") or "")}" aria-label="the lens"></div>')
    if zoom or win:
        if long.get("burst"):
            b = r["bursts"][long["burst"] - 1]
            z0, z1 = b["from"], b["to"]
        else:
            z0, z1 = zoom if zoom else (win["from"], win["to"])
        pad = max(30.0, (z1 - z0) * 0.04)
        parts.append(f'<h3>{e(title)}</h3>' + longviz.long_overview(r, long["items"], base=base, t0=max(0.0, z0 - pad),
                                                                      t1=z1 + pad, live=bool(r.get("in_progress"))))
    if win:
        parts.append(f'<h3>Every call</h3><p class="muted">Above, each call where it happened on the clock; below, '
                     f'the same calls in order, each the same width. A call opens its step.</p>'
                     + longviz.burst_calls(win, r, page=long.get("page") or 0,
                                           base=base + (f"&burst={long['burst']}" if long.get("burst") else
                                                        f"&t0={win['from']:.0f}&t1={win['to']:.0f}")))
    parts.append('<h3>The whole run</h3>' if (zoom or win) else "")
    parts.append(f'<div class="lens-fallback">{longviz.long_overview(r, long["items"], base=base, live=bool(r.get("in_progress")))}</div>')
    parts.append('<h3>The story, phase by phase</h3><p class="muted">A loop of bursts is one row; so is a stretch of '
                 'filler, where no check changed. A phase opens its calls.</p>' + longviz.chapters_table(r, base=base))
    grid = longviz.rhythm_grid(r, base=base)
    if grid:
        parts.append(f'<h3>When it worked</h3><p class="muted">{e(r["rhythm"]["unit"])}'
                     f'{" on the wall clock (UTC)" if r["rhythm"]["wall_clock"] else " from its start"}; '
                     f'a cell opens that stretch.</p>{grid}')
    pace = longviz.pace_chart(r)
    if pace:
        parts.append(f'<h3>Its pace</h3>{pace}')
    tm = longviz.phase_treemap(r, long["items"], base=base)
    if tm:
        parts.append(f'<h3>Where the working time went</h3>{tm}')
    other = long.get("other")
    if other:
        names = (str(r.get("agent") or "A"), str(other["r"].get("agent") or "B"))
        parts.append(f'<h3>Beside the other run, checkpoint by checkpoint</h3>'
                     f'<p class="muted">{e(names[1])}: {e(other["r"]["sentence"])}</p>'
                     + longviz.diff_timeline(long["data"], other["data"], long["items"], other["items"], names=names))
    b = r["basis"]
    parts.append(f'<p class="muted">How it was cut: {e(b["clock"])} clock; sessions split at '
                 f'{e(dur(b["session_gap_s"]))} idle; bursts at {e(b["burst_gap_s"])}s, {e(b["burst_gap_how"])}. '
                 f'Progress is {e(b["progress"])}. A repeat is {e(b["repeat"])}.</p>')
    return "".join(parts)


# ----------------------------------------------------------------- live
def live_panel(*, refs: list, steps_of, ribbons_of=None) -> str:
    """Every running trace as a card: its rhythm so far, its loop so far; and
    every run of the last hour on one clock, the running ones growing."""
    now = time.time()
    refs = [r for r in refs if r.live or not r.summary.get("example")]   # a finished bundled example did not just finish
    running = [r for r in refs if r.live and now - r.updated <= STALL_S]
    stalled = [r for r in refs if r.live and now - r.updated > STALL_S]
    done = [r for r in refs if not r.live and now - r.updated <= 3600][:8]
    cards = []
    if ribbons_of and (running or done):
        shown = (running + done)[:24]
        rows, links, labels = ribbons_of(shown, 3600.0)
        cards.append(f'<div class="card"><p class="muted">Every run of the last hour on one clock; a running one '
                     f'grows as it streams.</p>{viz.ribbons_svg(rows, links=links, labels=labels)}</div>')
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
def evals_page(*, brand: str, user: str, csrf: str, entries: List[Entry], rivers: dict,
               datas: Optional[dict] = None) -> str:
    blocks = []
    for x in entries:
        if datas and datas.get(x.id):
            blocks.append(evolvecards.evals_card(x, datas[x.id]))
            continue
        blocks.append(f'<h2>{e(x.title)}</h2><div class="card"><p class="muted">{e(x.summary.get("says"))}</p>'
                      f'{rivers.get(x.id) or ""}<p><a href="/runs/{e(x.id)}">Every eval and why it entered and left →</a>'
                      f'</p></div>')
    if not blocks:
        blocks.append('<div class="card"><p class="muted">No eval suites under the root yet. Carry one through '
                      'training generations with <code>agentdiff evolve-evals g0/ g1/ g2/ -o evals/</code>; '
                      'its <code>evolve-evals.json</code> shows here.</p></div>')
    body = (f'<h1>Evals</h1><p class="sub">Each suite across its generations: what the evals it carried in caught of '
            f'failures they had never seen (forward coverage), and each eval\'s life, from the failure it was born on '
            f'to the generation it retired.</p>'
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


# ------------------------------------------------------------- timeline
_WHERE = {"loop": "went round the same lap", "check": "ended on a failing check", "error": "left an error unrecovered",
          "unchecked": "answered without a check", "end": "passed its own checks, failed the grader's"}


def ribbon_group(name: str, rows: list, links: list, labels: list, scale: str) -> str:
    failed = [r for r in rows if r["success"] is False and not r["in_progress"]]
    running = sum(1 for r in rows if r["in_progress"])
    kinds: dict = {}
    for r in failed:
        k = (r.get("look_here") or {}).get("kind")
        if k in _WHERE:
            kinds[k] = kinds.get(k, 0) + 1
    where = "; ".join(f"{v} {_WHERE[k]}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
    note = (f'<p class="note error">Of the {len(failed)} that failed: {e(where)}.</p>' if where else "")
    return (f'<h2>{e(name)} <span class="muted">· {len(rows)} run(s), {len(failed)} failed'
            f'{f", {running} running" if running else ""}</span></h2>'
            f'<div class="card">{note}{viz.ribbons_svg(rows, shared=scale != "own", links=links, labels=labels)}</div>')


def timeline_page(*, brand: str, user: str, csrf: str, title: str, groups: list, scale: str, base_q: str,
                  live: bool = False) -> str:
    """Many runs at once: per group, one row per run on one clock."""
    chips = "".join(f'<a href="/timeline?{e(base_q)}&scale={k}"{" class=on" if k == scale else ""}>{label}</a>'
                    for k, label in (("shared", "one clock"), ("own", "each its own clock")))
    blocks = []
    n = 0
    for name, rows, links, labels in groups:
        n += len(rows)
        blocks.append(ribbon_group(name, rows, links, labels, scale))
    if not blocks:
        blocks.append('<div class="card"><p class="muted">No runs here.</p></div>')
    body = (f'<p class="muted"><a href="/traces">Traces</a></p><h1>{e(title)}</h1>'
            f'<p class="sub">One row per run: its steps along its clock, coloured by what it was doing; a '
            f'tick at the end of each lap (green: its check passed, red: failed); the ring is where to look first. '
            f'A name opens the run.</p><div class="filters">{chips}</div>'
            f'<div data-live="/timeline/panel?{e(base_q)}&scale={e(scale)}">{"".join(blocks)}</div>')
    return layout(title, body, brand=brand, user=user, csrf=csrf, active="traces", live=live)


# --------------------------------------------------------------- evolve
def evolve_page(*, brand: str, user: str, csrf: str, entries: List[Entry], rivers: dict,
                datas: Optional[dict] = None) -> str:
    blocks = []
    for x in entries:
        if datas and datas.get(x.id):
            blocks.append(evolvecards.evolve_card(x, datas[x.id]))
            continue
        blocks.append(f'<h2>{e(x.title)}</h2><div class="card"><p class="muted">{e(x.summary.get("describe"))} · '
                      f'stopped: {e(x.summary.get("stop"))}</p>{rivers.get(x.id) or ""}'
                      f'<p><a href="/runs/{e(x.id)}">The harness, every change and its evidence →</a></p></div>')
    if not blocks:
        blocks.append('<div class="card"><p class="muted">No evolving harness under the root yet. Start one: '
                      '<code>agentdiff self-evolve --task tasks.json --agent haiku -o evo/</code>. Each generation '
                      'the agents run, the evals judge them, and the harness tries one change the evals point at; '
                      'it shows here, and its runs stream to <a href="/live">Live</a> as they go.</p></div>')
    body = (f'<h1>Evolve</h1><p class="sub">Agents under a harness that changes itself. Each generation: the agents '
            f'run, the evolving evals judge the runs, the eval that caught the most failures names one change, the '
            f'change is tested against the harness as it was on the same tasks, and kept only on the counts.</p>'
            + ('<div class="ekey"><span><i></i>runs that passed under the harness as it was</span>'
               '<span><i class="c"></i>passed with the change tried</span></div>' if datas else "")
            + "".join(blocks))
    return layout("Evolve", body, brand=brand, user=user, csrf=csrf, active="evolve")


def evolution_page(*, brand: str, user: str, csrf: str, entry: Entry, data: dict, refs: list = ()) -> str:
    from ..selfevolve import remedy_text
    rows, weighed = [], []
    for g in data.get("lineage") or []:
        a = g.get("action") or {}
        t = a.get("test") or {}
        pc, pn = (t.get("passed") or {}).get("current"), (t.get("passed") or {}).get("changed")
        verdict = ""
        if t:
            verdict = ('<span class="badge ok">✓ kept</span>' if t["verdict"] == "kept"
                       else '<span class="badge bad">✗ reverted</span>')
        tok = t.get("median_tokens") or {}
        tok_s = (f'{_num(tok.get("current"))} → {_num(tok.get("changed"))}' if tok.get("changed") is not None else "—")
        rows.append(f'<tr><td>{e(g["generation"])}</td><td>v{e(g["harness"]["version"])}</td>'
                    f'<td class="n">{e(g["failed"])}/{e(g["runs"])}</td>'
                    f'<td>{e(remedy_text(a["remedy"]) if a.get("remedy") else "—")}'
                    f'<div class="muted">{e((a.get("because") or {}).get("says") or "")}</div></td>'
                    f'<td>{verdict}<div class="muted">{e(t.get("why") or "")}</div></td>'
                    f'<td class="n">{f"{pc[0]}/{pc[1]} → {pn[0]}/{pn[1]}" if pc and pn else "—"}</td>'
                    f'<td class="n">{tok_s}</td></tr>')
        items = []
        for w in g.get("weighed") or []:
            fate = ("tested" if a and w.get("remedy") and (a.get("remedy") or {}).get("id") == w["remedy"]["id"]
                    else w.get("skipped") or "not reached")
            items.append(f'<tr><td class="mono">{e(w.get("eval") or "—")}</td><td>{e(w.get("says"))}</td>'
                         f'<td class="n">{e(w.get("caught"))}/{e(w.get("wrong"))}</td><td>{e(w.get("source"))}</td>'
                         f'<td>{e(fate)}</td></tr>')
        if items:
            weighed.append(f'<details><summary>{e(g["generation"])}: {len(items)} candidate(s) weighed</summary>'
                           f'<table><tr><th>eval</th><th>flags a run when</th><th class="n">caught</th><th>from</th>'
                           f'<th>what happened</th></tr>{"".join(items)}</table></details>')
    ev = data.get("evals") or {}
    arms = {}
    for r in refs:
        arms.setdefault(r.group, []).append(r)
    arm_html = "".join(f'<h2>{e(gname)} <span class="muted">· {len(rs)}</span></h2><div class="card">{_trace_rows(rs)}</div>'
                       for gname, rs in arms.items())
    body = (f'<p class="muted"><a href="/evolve">Evolve</a></p><h1>{e(entry.title)}</h1>'
            f'{evolvecards.evolve_card(entry, data, link=False, title=False)}'
            f'<h2>The story, generation by generation</h2><div class="card"><p>{e(data.get("narrative"))}</p>'
            + (f'<details><summary class="muted">the river: every generation on one chart</summary>'
               f'{viz.harness_river(data)}</details>' if len(data.get("lineage") or []) > 1 else "")
            + '</div>'
            f'<h2>Every generation</h2><div class="card"><table><tr><th>generation</th><th>harness</th>'
            f'<th class="n">failed</th><th>change tried, and the failure it answers</th><th>paired test</th>'
            f'<th class="n">passed: as it was → changed</th><th class="n">median tokens</th></tr>{"".join(rows)}</table>'
            f'{"".join(weighed)}</div>'
            + (f'<h2>The evals that judged it</h2>{evolvecards.evals_card(entry, ev, link=False, title=False)}'
               f'<div class="card"><p>{e(ev.get("narrative"))}</p><details><summary class="muted">every eval\'s life on one '
               f'chart</summary>{viz.eval_river(ev)}</details></div>' if ev.get("lineage") else "")
            + f'<h2>Every run, by arm <span class="muted">· <a href="/timeline?run={e(entry.id)}">every version on one '
              f'clock →</a></span></h2>{arm_html or "<div class=card><p class=muted>No traces found under it.</p></div>"}'
            f'<p class="muted">A change is kept when it wins more tasks than it loses, the same tasks run the same '
            f'number of times, and no task goes from always passing to always failing. Every number is a count of '
            f'graded runs.</p>')
    return layout(entry.title, body, brand=brand, user=user, csrf=csrf, active="evolve")


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
