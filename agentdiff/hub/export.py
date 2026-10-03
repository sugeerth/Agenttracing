"""The hub as static pages: every view, linked, for a host that serves files only.

``agentdiff hub ROOT --export DIR`` signs in to a hub over ``ROOT`` as its
demo account and walks it from the Overview: the runs, every trace page,
the timelines, the evolving harnesses and evals. Each page is written as
a file, with its links pointed at the other files.

- The lens's script is inlined in each page, since a static host may
  allow no script file of its own.
- The lens's data is written beside the page (``lens-<id>.json``).
- Each trace has its page, its phases page with the lens
  (``trace-<id>.phases.html``), and for a big run, a page with every
  panel drawn (``trace-<id>.all.html``).
- A burst link (``&burst=N``) becomes ``#burst-N`` on the phases page,
  and the lens moves to that burst.
- What needs a server is left out, and the page says so: signing in,
  live updates, comparing two runs with the picker, a run's sandboxed
  report.

The theme follows the viewer's choice as well as the system's: dark
tokens apply under ``[data-theme=dark]``, and never under
``[data-theme=light]``. The index page is written without a document
shell (``--bare-index``) for a host that adds its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = ["export"]

_HEX = r"[0-9a-f]{12}"
_TOP = {"/": "index.html", "/runs": "runs.html", "/traces": "traces.html", "/timeline": "timeline.html",
        "/live": "live.html", "/evolve": "evolve.html", "/evals": "evals.html"}
_DROP = ("/account", "/login", "/logout", "/healthz", "/api/v1/runs", "/api/v1/traces", "/api/v1/events")
_SNAPSHOT = ('<p class="note" style="margin:10px 0">A static snapshot of the hub, taken {when}. Signing in, live '
             'updates and comparing runs with the picker need the hub itself: <code>agentdiff hub</code>.</p>')


def _target(href: str, here: str, long_ids: set) -> Optional[str]:
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
    if any(path == d or path.startswith(d + "/") for d in _DROP):
        return None
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
    m = re.fullmatch(rf"/runs/({_HEX})", path)
    if m:
        return f"run-{m.group(1)}.html"
    m = re.fullmatch(rf"/api/v1/traces/({_HEX})", path)
    if m:
        return f"trace-{m.group(1)}.json"
    return None


def _rewrite(html: str, here: str, long_ids: set, lens_js: str) -> str:
    def link(m):
        href = m.group(2).replace("&amp;", "&")
        t = _target(href, here, long_ids)
        if t is None:
            return f'{m.group(1)}"#"'
        return f'{m.group(1)}"{t.replace("&", "&amp;")}"'
    html = re.sub(r'<a href="/account"[^>]*>[^<]*</a>', "", html)  # nothing to manage without the hub
    html = re.sub(r'(<a\b[^>]*?\bhref=)"([^"]*)"', link, html)
    # what needs a server: the sign-out form, the compare picker, live script
    html = re.sub(r'<form method="post" action="/logout">.*?</form>', "", html, flags=re.S)
    html = re.sub(r'<form method="get" action="/traces/[^"]*" class="filters".*?</form>', "", html, flags=re.S)
    html = re.sub(r'<span id="live-status"[^>]*>.*?</span>', "", html, flags=re.S)
    html = html.replace('<script src="/static/live.js" defer></script>', "")
    html = html.replace('<script src="/static/longview.js" defer></script>', "")
    html = re.sub(r'data-live="[^"]*"', "", html)
    # the lens: its data beside the page, its script in it
    html = re.sub(rf'data-lens="/api/v1/traces/({_HEX})/long"', r'data-lens="lens-\1.json"', html)
    html = re.sub(r'data-base="[^"]*"', 'data-base=""', html)
    if 'class="lens"' in html:
        html = html.replace("</body>", f"<script>{lens_js}</script></body>")
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


def export(root: str, out: str, *, bare_index: bool = False, title: Optional[str] = None) -> Dict[str, int]:
    """Write every page of a hub over ``root`` into ``out``. Returns counts."""
    import time

    from ..harness.hub_server import build_app
    from ..longrun import is_long
    from .app import Request
    from .config import load
    config = load(root, env={}, overrides={"demo": True, "port": 0})
    if title:
        from dataclasses import replace
        config = replace(config, title=title)
    app = build_app(config)
    user, password = app.demo_account()
    page = app.handle(Request("GET", "/login")).body.decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    from .urls import quote
    body = f"csrf={quote(token)}&user={quote(user)}&password={quote(password)}&next=/".encode()
    resp = app.handle(Request("POST", "/login", body=body))
    headers = {"Cookie": resp.headers["Set-Cookie"].split(";")[0]}
    dest = Path(out)
    dest.mkdir(parents=True, exist_ok=True)
    lens_js = (Path(__file__).with_name("static") / "longview.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    refs = app.traces.refs()
    long_ids = set()
    for r in refs:
        data = app.traces.load(r)
        if data is not None and is_long(data):
            long_ids.add(r.id)
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    pages: List[Tuple[str, str]] = [(p, f) for p, f in _TOP.items()]
    pages += [(f"/traces/{r.id}", f"trace-{r.id}.html") for r in refs]
    pages += [(f"/traces/{r.id}?view=long", f"trace-{r.id}.phases.html") for r in refs]
    pages += [(f"/traces/{r.id}?view=all", f"trace-{r.id}.all.html") for r in refs if r.id in long_ids]
    pages += [(f"/runs/{x.id}", f"run-{x.id}.html") for x in app.catalog.entries()]
    counts = {"pages": 0, "data": 0}
    for path, name in pages:
        r = app.handle(Request("GET", path, headers))
        if r.status != 200 or not r.body:
            continue
        html = _rewrite(r.body.decode("utf-8"), name, long_ids, lens_js)
        if app.ingest_token:  # a static copy never carries the hub's ingest token
            html = html.replace(app.ingest_token, "&lt;the hub's token&gt;")
        html = html.replace("<main>", "<main>" + _SNAPSHOT.format(when=when), 1)
        if name == "index.html":
            html = re.sub(r"<title>.*?</title>", f"<title>{config.title}</title>", html, count=1, flags=re.S)
            if bare_index:
                html = _bare(html)
        (dest / name).write_text(html, encoding="utf-8")
        counts["pages"] += 1
    for r in refs:
        for path, name in ((f"/api/v1/traces/{r.id}", f"trace-{r.id}.json"),
                           (f"/api/v1/traces/{r.id}/long", f"lens-{r.id}.json")):
            got = app.handle(Request("GET", path, headers))
            if got.status == 200:
                (dest / name).write_bytes(got.body)
                counts["data"] += 1
    (dest / "manifest.json").write_text(json.dumps({"exported": when, "root": str(Path(root).resolve().name),
                                                    **counts}, indent=1), encoding="utf-8")
    return counts
