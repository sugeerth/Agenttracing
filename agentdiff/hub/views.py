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
    rows.append({"label": "verdict", "text": f"{c['runs'] - c['failed']} of {c['runs']} finished run(s) passed; "
                                             f"{c['failed']} failed.", "source": "outcome.success over the trace index",
                 "tone": "bad" if c["failed"] else "ok"})
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
            f"{a['agent']}: {a['passed']}/{a['runs']} passed, a median {a['median_lines']:.0f} line(s) changed"
            + (f", {a['tests_edited']} run(s) edited tests" if a["tests_edited"] else "") for a in code[:6]) + ".",
                     "source": "the harness's diff of each workspace (records)",
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


def overview_page(*, brand: str, user: str, csrf: str, entries: List[Entry], refs: list, ingest: dict) -> str:
    evolving = [x for x in entries if x.kind == "evolution"]
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
            f'{_start_here(refs)}{agents_table(refs)}<h2>Running now</h2><div class="card">{live_html}</div>'
            + (f'<h2>Agents evolving</h2>{_entry_cards(evolving[:4])}<p><a href="/evolve">Every evolving harness →</a></p>'
               if evolving else "")
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
                 every_href: str = "") -> str:
    """Every step; for a long run, the opening, the stretch around where to look
    first and the ending, with what is left out said and a link to all of it."""
    from ..laps import _activity
    rows = []
    keep = None
    if len(steps) > WINDOW_STEPS and not every:
        keep = set(range(0, 10)) | set(range(max(0, len(steps) - 10), len(steps)))
        if focus is not None:
            keep |= set(range(max(0, focus - 15), min(len(steps), focus + 16)))
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
PANELS = (("map", "Trajectory map"), ("trunk", "The run as a trunk"), ("compare", "Two runs on one axis"),
          ("code", "The code it produced"), ("seconds", "Where the seconds went"), ("reward", "Reward & credit"),
          ("lanes", "Every thread on its own lane"), ("laps", "The loop, lap by lap"),
          ("flow", "How it moved between tools"), ("steps", "The steps"))
#: which panels each preset opens; the others stay one click away
PRESETS = (("focus", "focus", ("map", "trunk", "compare", "code", "steps")),
           ("loop", "the loop", ("trunk", "laps", "flow")), ("time", "time", ("seconds", "lanes", "trunk")),
           ("threads", "threads", ("trunk", "lanes")), ("code", "the code", ("code", "map", "steps")),
           ("training", "training", ("reward", "trunk")), ("all", "everything", tuple(k for k, _ in PANELS)))


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
               act: Optional[dict] = None) -> str:
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


def trace_panel(*, ref, data: dict, lap: dict, tl: Optional[dict] = None, cmp: Optional[dict] = None,
                others: list = (), vs: str = "", axis: str = "step", every: bool = False, view: str = "focus",
                card: Optional[list] = None, change: Optional[dict] = None, code_cmp: Optional[dict] = None,
                other_change: Optional[dict] = None, act: Optional[dict] = None, other_data: Optional[dict] = None,
                al: Optional[dict] = None, task_nav: str = "") -> str:
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
    presets = f'<div class="filters" aria-label="views"><span class="muted">view</span>{chips}</div>'
    panels = []
    folded = f', {tl["folded_steps"]} quiet steps folded' if tl.get("folded_steps") else ""
    if cmp and other_data is not None and al:
        here_b = (cmp["b"].get("look_here") or {}).get("index")
        body = (f'<p class="muted">{e(al["sentence"])} Each column is its run in its own order; a line joins the '
                f'steps that made the same call. A step of A opens below.</p>'
                + viz.trajectory_map(data, other_data, al, names=(str(cmp["a"].get("agent")), str(cmp["b"].get("agent"))),
                                     heres=((tl.get("look_here") or {}).get("index"), here_b,
                                            (tl.get("look_here") or {}).get("kind"),
                                            (cmp["b"].get("look_here") or {}).get("kind"))))
        panels.append(_panel("map", "Trajectory map", f'{al["matched"]} step(s) shared'
                             + (f', parting at row {al["divergence"]}' if al["divergence"] is not None else ""),
                             body, "map" in opened, start=True))
    trunk_body = (f'<p class="muted">The trunk is the run on its {e(tl.get("basis"))} clock: thinking on it, each tool '
                  f'call a branch ending in a leaf, each sub-agent hanging off it where it first acted. Every leaf '
                  f'opens its step.</p>{viz.trunk_svg([tl], axis="time")}{_picker(ref, list(others), vs, axis, view)}'
                  if tl else "")
    gist_trunk = (f'{len(tl.get("lanes") or [])} thread(s) · {len(tl.get("laps") or [])} lap(s) · '
                  f'{_secs(tl.get("span_s"))}{folded}')
    panels.append(_panel("trunk", "The run as a trunk", gist_trunk, trunk_body, "trunk" in opened))
    if cmp:
        d = cmp.get("diverged_at")
        body = (f'<p class="note">{e(cmp["sentence"])}</p>{viz.trunk_svg([cmp["a"], cmp["b"]], cmp=cmp, axis=axis)}'
                f'{viz.pair_timeline(cmp, axis=axis)}<p class="muted">A: {e(cmp["a"].get("agent"))} '
                f'({e(cmp["a"]["look_here"]["sentence"] if cmp["a"].get("look_here") else "")}) · B: '
                f'{e(cmp["b"].get("agent"))} ({e(cmp["b"]["look_here"]["sentence"] if cmp["b"].get("look_here") else "")})'
                + (f' · <a href="#s{e(d)}">step {e(d)} →</a>' if d is not None else "") + '</p>')
        gist = f"first difference at step {d}" if d is not None else "the same calls throughout"
        panels.append(_panel("compare", "Two runs on one axis", gist, body, "compare" in opened))
    gist_code = (f'+{change["added"]} −{change["removed"]} in {len(change["files"])} file(s)'
                 + (f' · {len(change["flags"])} flag(s)' if change["flags"] else "") if change else "not captured")
    panels.append(_panel("code", "The code it produced", gist_code,
                         code_panel(change, compare=code_cmp, other=other_change, act=act), "code" in opened))
    secs_runs = [tl] + ([cmp["b"]] if cmp else [])
    secs_names = ([f'A · {tl.get("agent") or "run"}', f'B · {cmp["b"].get("agent") or "run"}'] if cmp
                  else [str(tl.get("agent") or "run")])
    panels.append(_panel("seconds", "Where the seconds went", f'{_secs(tl.get("span_s"))} on its clock'
                         + (f' against {_secs(cmp["b"].get("span_s"))}' if cmp else ""),
                         '<p class="muted">Area is seconds, on one scale for both runs: a box per lap (per sub-agent '
                         'when it delegated), a tile per step. A tile opens its step.</p>'
                         + viz.seconds_treemap(secs_runs, secs_names), "seconds" in opened))
    rw = viz.reward_steps(secs_runs, secs_names)
    if rw:
        panels.append(_panel("reward", "Reward & credit", "the return, step by step",
                             '<p class="muted">The return as it accumulated, one step at a time, from the rewards the '
                             'trace recorded.</p>' + rw, "reward" in opened))
    panels.append(_panel("lanes", "Every thread on its own lane", f'{len(tl.get("lanes") or [])} lane(s){folded}',
                         viz.run_timeline(tl, live=ref.live) if tl else "", "lanes" in opened))
    lap_gist = (lap.get("summary") or "").split(";")[0]
    panels.append(_panel("laps", "The loop, lap by lap", lap_gist,
                         f'<p class="muted">{e(lap.get("summary"))}</p>{viz.lap_chart(lap)}{viz.lap_table(lap)}',
                         "laps" in opened))
    moves = lap.get("transitions") or []
    tools = len({m["from"] for m in moves} | {m["to"] for m in moves})
    panels.append(_panel("flow", "How it moved between tools", f"{tools} tool(s), {sum(m['count'] for m in moves)} move(s)",
                         viz.flow_ring(lap), "flow" in opened))
    focus = (tl.get("look_here") or {}).get("index")
    title = "Every step" if len(steps) <= WINDOW_STEPS or every else "The steps that matter"
    panels.append(_panel("steps", title, f"{len(steps)} step(s)" + ("" if len(steps) <= WINDOW_STEPS or every
                                                                     else ", a window around where to look"),
                         _steps_table(steps, focus=focus, every=every, every_href=f"/traces/{ref.id}?steps=all&view={view}#s0"),
                         "steps" in opened or every))
    prompt = str((data.get("task") or {}).get("prompt") or "").strip().splitlines()
    prompt_html = f'<p class="prompt">{e(prompt[0][:300])}</p>' if prompt else ""
    return f'{task_nav}{prompt_html}{head}{verdict_html(card or [])}{notes}{presets}{"".join(panels)}'


def trace_page(*, brand: str, user: str, csrf: str, ref, data: dict, lap: dict, **panel) -> str:
    from .urls import quote
    s = ref.summary
    vs, axis, view = panel.get("vs") or "", panel.get("axis") or "step", panel.get("view") or "focus"
    q = f"?view={quote(view)}" + (f"&vs={quote(vs)}&axis={quote(axis)}" if vs else "")
    body = (f'<p class="muted"><a href="/traces">Traces</a> / {e(ref.group)} · '
            f'<a href="/timeline?g={e(quote(ref.group))}">every run here on one clock</a></p>'
            f'<h1>{e(s.get("task"))}</h1>'
            f'<div data-live="/traces/{e(ref.id)}/panel{e(q)}" data-ids="{e(ref.id)}">'
            f'{trace_panel(ref=ref, data=data, lap=lap, **panel)}</div>'
            f'<p class="muted mono">{e(ref.path.name)} · <a href="/api/v1/traces/{e(ref.id)}">JSON</a></p>')
    return layout(str(s.get("task")), body, brand=brand, user=user, csrf=csrf, active="traces", live=ref.live)


# ----------------------------------------------------------------- live
def live_panel(*, refs: list, steps_of, ribbons_of=None) -> str:
    """Every running trace as a card: its rhythm so far, its loop so far; and
    every run of the last hour on one clock, the running ones growing."""
    now = time.time()
    running = [r for r in refs if r.live and now - r.updated <= STALL_S]
    stalled = [r for r in refs if r.live and now - r.updated > STALL_S]
    done = [r for r in refs if not r.live and now - r.updated <= 3600][:8]
    cards = []
    if ribbons_of and (running or done):
        shown = (running + done)[:24]
        rows, links, labels = ribbons_of(shown)
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
def evolve_page(*, brand: str, user: str, csrf: str, entries: List[Entry], rivers: dict) -> str:
    blocks = []
    for x in entries:
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
            + "".join(blocks))
    return layout("Evolve", body, brand=brand, user=user, csrf=csrf, active="evolve")


def evolution_page(*, brand: str, user: str, csrf: str, entry: Entry, data: dict, refs: list = ()) -> str:
    from ..selfevolve import remedy_text
    h = data.get("harness") or {}
    now = "".join(f"<li>{e(i)}</li>" for i in h.get("instructions") or [])
    now += "".join(f"<li>denied tool <code>{e(t)}</code></li>" for t in h.get("deny_tools") or [])
    if h.get("max_turns"):
        now += f"<li>at most {e(h['max_turns'])} turns</li>"
    now = f"<ol>{now}</ol>" if now else '<p class="muted">Nothing added: the agent as it ships.</p>'
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
            f'<p class="sub">{e(data.get("describe"))} · stopped: {e(data.get("stop"))}</p>'
            f'<div class="card">{viz.harness_river(data)}</div>'
            f'<div class="card"><p>{e(data.get("narrative"))}</p></div>'
            f'<h2>The harness now</h2><div class="card">{now}</div>'
            f'<h2>Every generation</h2><div class="card"><table><tr><th>generation</th><th>harness</th>'
            f'<th class="n">failed</th><th>change tried, and the failure it answers</th><th>paired test</th>'
            f'<th class="n">passed: as it was → changed</th><th class="n">median tokens</th></tr>{"".join(rows)}</table>'
            f'{"".join(weighed)}</div>'
            + (f'<h2>The evals that judged it</h2><div class="card">{viz.eval_river(ev)}<p>{e(ev.get("narrative"))}</p></div>'
               if ev.get("lineage") else "")
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
