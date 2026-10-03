"""The hub as static pages, every part of it, for a host that serves files only.

``agentdiff hub ROOT --export DIR`` signs in to a hub over ``ROOT`` as its
demo account and writes every page as a file, with its links pointed at
the other files:

- **Signing in.** The sign-in page comes first. It checks the hub's demo
  account in the page and keeps the signed-in name in the tab's session
  storage. Every other page sends a visitor without it to the sign-in
  page, and *Sign out* clears it. This makes the copy work like the hub;
  it is not a lock. Keep the copy private, as its host does.
- **Every section:** the Overview, runs and each run's report page,
  traces, timelines, Live, Evolve, Evals and Account.
- **Every trace:** its page; its phases page, with the lens and its data
  inline; and for a big run, a page with every panel drawn. A burst link
  becomes ``#burst-N`` on the phases page, and the lens moves there.
- **Live** has nothing running in a copy, so it replays the longest run
  under the root as it grew, phase by phase.

What the copy cannot do is left out, and the page says so: changing a
password, live updates, comparing two runs with the picker, posting
telemetry. The hub's ingest token is never written. The theme follows
the viewer's choice as well as the system's. With ``--bare-index``, the
index page is written without a document shell, for a host that adds
its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = ["export"]

_HEX = r"[0-9a-f]{12}"
_TOP = {"/": "index.html", "/runs": "runs.html", "/traces": "traces.html", "/timeline": "timeline.html",
        "/live": "live.html", "/evolve": "evolve.html", "/evals": "evals.html", "/account": "account.html",
        "/login": "login.html"}
_KEY = "agentdiff-hub-user"
#: on every page but the sign-in page: no signed-in name in this tab, to the sign-in page; and Sign out
_GATE = ("<script>(function(){var who=null;try{who=sessionStorage.getItem('" + _KEY + "');"
         "if(!who){location.replace('login.html');return}}catch(e){}"
         "document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('[data-signout]');"
         "if(!b)return;ev.preventDefault();try{sessionStorage.removeItem('" + _KEY + "')}catch(e){}"
         "location.href='login.html'})})();</script>")


def _target(href: str, here: str, long_ids: set, runs: set) -> Optional[str]:
    """Where a hub link points among the files, or None to drop it."""
    from .urls import parse_query, split
    if not href.startswith("/") or href.startswith("//"):
        return href  # an anchor, or another site
    path, query = split(href)
    frag = ""
    if "#" in (query or ""):
        query, frag = query.split("#", 1)
    if "#" in path:
        path, frag = path.split("#", 1)
    q = parse_query(query or "")
    if path in _TOP:
        return _TOP[path] + (f"#{frag}" if frag else "")
    m = re.fullmatch(rf"/traces/({_HEX})", path)
    if m:
        tid = m.group(1)
        burst = q.get("burst", [""])[0]
        view = q.get("view", [""])[0]
        page = f"trace-{tid}.html"
        if view == "long" or burst or q.get("session") or q.get("t0"):
            page = f"trace-{tid}.phases.html"
        elif (q.get("open") or view == "all") and tid in long_ids:
            page = f"trace-{tid}.all.html"
        if burst.isdigit():
            return (page if page != here else "") + f"#burst-{burst}"
        return (page if page != here or not frag else "") + (f"#{frag}" if frag else "")
    m = re.fullmatch(rf"/runs/({_HEX})(/page)?", path)
    if m:
        if m.group(2):
            return f"run-{m.group(1)}-page.html" if m.group(1) in runs else None
        return f"run-{m.group(1)}.html"
    return None


def _rewrite(html: str, here: str, long_ids: set, runs: set) -> str:
    def link(m):
        t = _target(m.group(2).replace("&amp;", "&"), here, long_ids, runs)
        return f'{m.group(1)}"{"#" if t is None else t.replace("&", "&amp;")}"'
    html = re.sub(r'(<a\b[^>]*?\bhref=)"([^"]*)"', link, html)
    # Sign out clears the tab's sign-in; what needs a server goes
    html = re.sub(r'<form method="post" action="/logout">.*?</form>',
                  '<button class="link" type="button" data-signout>Sign out</button>', html, flags=re.S)
    html = re.sub(r'<form method="get" action="/traces/[^"]*" class="filters".*?</form>', "", html, flags=re.S)
    html = re.sub(r'<span id="live-status"[^>]*>.*?</span>', "", html, flags=re.S)
    html = html.replace('<script src="/static/live.js" defer></script>', "")
    html = html.replace('<script src="/static/longview.js" defer></script>', "")
    html = re.sub(r' data-live="[^"]*"', "", html)
    html = re.sub(r'data-base="[^"]*"', 'data-base=""', html)
    return _themed(html)


def _themed(html: str) -> str:
    """Dark tokens under the system's dark setting unless the viewer chose light,
    and under an explicit dark choice."""
    def swap(m):
        body = m.group(1)
        return (f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{body};color-scheme:dark}}}}'
                f':root[data-theme="dark"]{{{body};color-scheme:dark}}')
    return re.sub(r"@media \(prefers-color-scheme:dark\)\{:root\{([^}]*)\}\}", swap, html)


def _bare(html: str) -> str:
    """A page without its document shell, for a host that wraps it in its own:
    the title and styles first, then the body's content."""
    title = re.search(r"<title>.*?</title>", html, re.S)
    styles = "".join(re.findall(r"<style>.*?</style>", html, re.S))
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    return (title.group(0) if title else "") + styles + (body.group(1) if body else html)


def _inline_lens(html: str, payload: str, lens_js: str, *, replay: bool = False) -> str:
    """The lens with its data in the page: no fetch, no script file."""
    data = payload.replace("</", "<\\/")
    html = re.sub(r'data-lens="[^"]*"', 'data-lens-inline="lens-data"' + (' data-replay="1"' if replay else ""),
                  html, count=1)
    return html.replace("</body>", f'<script type="application/json" id="lens-data">{data}</script>'
                                   f"<script>{lens_js}</script></body>", 1)


def _signin(html: str, user: str, password: str) -> str:
    """The hub's sign-in page, signing in in the page: the demo account only."""
    html = re.sub(r'<form method="post" action="/login">', '<form id="signin" action="#">', html)
    html = re.sub(r'<input type="hidden" name="(csrf|next)" value="[^"]*">', "", html)
    html = re.sub(r"It exists on this machine only; <code>--no-demo</code> turns it off\.",
                  "This private copy of the hub signs in with it.", html)
    html = html.replace('<form id="signin"', '<p class="note error" id="signin-error" role="alert" hidden>That '
                        'user and password are not the demo account.</p><form id="signin"', 1)
    script = ("<script>(function(){var f=document.getElementById('signin');if(!f)return;"
              "f.addEventListener('submit',function(ev){ev.preventDefault();"
              f"var u=f.user.value.trim(),p=f.password.value;if(u==={json.dumps(user)}&&p==={json.dumps(password)}){{"
              f"try{{sessionStorage.setItem('{_KEY}',u)}}catch(e){{}}location.href='index.html'}}"
              "else{document.getElementById('signin-error').hidden=false;f.password.value='';f.password.focus()}})})();"
              "</script>")
    return html.replace("</body>", script + "</body>", 1)


def _esc(v) -> str:
    import html
    return html.escape("" if v is None else str(v), quote=True)


def export(root: str, out: str, *, bare_index: bool = False, title: Optional[str] = None) -> Dict[str, int]:
    """Write every page of a hub over ``root`` into ``out``. Returns counts."""
    import time

    from ..harness.hub_server import build_app
    from ..longrun import is_long
    from .app import Request
    from .config import load
    from .urls import quote
    config = load(root, env={}, overrides={"demo": True, "port": 0})
    if title:
        from dataclasses import replace
        config = replace(config, title=title)
    app = build_app(config)
    user, password = app.demo_account()
    page = app.handle(Request("GET", "/login")).body.decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    body = f"csrf={quote(token)}&user={quote(user)}&password={quote(password)}&next=/".encode()
    resp = app.handle(Request("POST", "/login", body=body))
    headers = {"Cookie": resp.headers["Set-Cookie"].split(";")[0]}
    dest = Path(out)
    dest.mkdir(parents=True, exist_ok=True)
    lens_js = (Path(__file__).with_name("static") / "longview.js").read_text(encoding="utf-8")
    lens_js = lens_js.replace("</script", "<\\/script")
    refs = app.traces.refs()
    long_ids, sizes = set(), {}
    for r in refs:
        data = app.traces.load(r)
        if data is not None:
            sizes[r.id] = len(data.get("steps") or [])
            if is_long(data):
                long_ids.add(r.id)
    entries = app.catalog.entries()
    runs = {x.id for x in entries if x.page is not None}
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    pages: List[Tuple[str, str]] = [(p, f) for p, f in _TOP.items() if p != "/login"]
    pages += [(f"/traces/{r.id}", f"trace-{r.id}.html") for r in refs]
    pages += [(f"/traces/{r.id}?view=long", f"trace-{r.id}.phases.html") for r in refs]
    pages += [(f"/traces/{r.id}?view=all", f"trace-{r.id}.all.html") for r in refs if r.id in long_ids]
    pages += [(f"/runs/{x.id}", f"run-{x.id}.html") for x in entries]
    counts = {"pages": 0}

    def lens_of(tid: str) -> str:
        got = app.handle(Request("GET", f"/api/v1/traces/{tid}/long", headers))
        return got.body.decode("utf-8") if got.status == 200 else "{}"

    def write(name: str, html: str) -> None:
        if app.ingest_token:  # a static copy never carries the hub's ingest token
            html = html.replace(app.ingest_token, "&lt;the hub's token&gt;")
        (dest / name).write_text(html, encoding="utf-8")
        counts["pages"] += 1

    for path, name in pages:
        r = app.handle(Request("GET", path, headers))
        if r.status != 200 or not r.body:
            continue
        html = _rewrite(r.body.decode("utf-8"), name, long_ids, runs)
        m = re.match(r"trace-(" + _HEX + r")\.phases\.html$", name)
        if m and 'class="lens"' in html:
            html = _inline_lens(html, lens_of(m.group(1)), lens_js)
        if name == "live.html" and sizes:
            # nothing runs in a copy: the longest run here, played back as it grew
            tid = max(sizes, key=sizes.get)
            ref = app.traces.get(tid)
            replay = (f'<h2>Replay · the longest run here, as it grew</h2><div class="card"><p class="muted">'
                      f'<a href="trace-{tid}.phases.html">{_esc(ref.summary.get("task"))} · '
                      f'{_esc(ref.summary.get("agent"))}</a>, {sizes[tid]:,} steps, played back phase by phase. '
                      f'Pause it, then move through it with the arrow keys or the wheel.</p>'
                      f'<div class="lens" data-lens="" data-base=""></div></div>')
            html = re.sub(r"(<h1>Live</h1>(?:<p[^>]*>.*?</p>)?)", lambda mm: mm.group(1) + replay, html, count=1,
                          flags=re.S)
            html = _inline_lens(html, lens_of(tid), lens_js, replay=True)
        if name == "account.html":
            html = html.replace("<h1>Account</h1>", '<h1>Account</h1><p class="note">This private copy signs in with '
                                'the demo account. Users and passwords are kept by the hub itself.</p>', 1)
        if name == "index.html":
            html = html.replace("<main>", f'<main><p class="note" style="margin:10px 0">A private copy of the hub, '
                                          f'taken {when}: every page, every trace, its phases and lens. Live replays '
                                          f'the longest run; live updates and comparing runs need '
                                          f'<code>agentdiff hub</code>.</p>', 1)
            html = re.sub(r"<title>.*?</title>", f"<title>{_esc(config.title)}</title>", html, count=1, flags=re.S)
        html = html.replace("<body>", "<body>" + _GATE, 1)
        if name == "index.html" and bare_index:
            html = _GATE + _bare(html).replace(_GATE, "", 1)
        write(name, html)
    # the sign-in page, which signs in in the page
    r = app.handle(Request("GET", "/login"))
    write("login.html", _signin(_rewrite(r.body.decode("utf-8"), "login.html", long_ids, runs), user, password))
    # each run's own report page, as the run wrote it
    for x in entries:
        if x.id in runs:
            got = app.handle(Request("GET", f"/runs/{x.id}/page", headers))
            if got.status == 200:
                (dest / f"run-{x.id}-page.html").write_bytes(got.body)
                counts["pages"] += 1
    (dest / "manifest.json").write_text(json.dumps({"exported": when, "root": Path(root).resolve().name, **counts},
                                                   indent=1), encoding="utf-8")
    return counts
