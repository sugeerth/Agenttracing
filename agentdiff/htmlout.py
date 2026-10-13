"""A self-contained HTML page for one reading (``explain --html``).

Stdlib only, every string escaped, theme-aware through
``prefers-color-scheme``.  It renders exactly what the CLI prints — the
summary, the phases, what the answer rests on, why it ended, what it
means, what to take forward — so a reader who opens the file sees the
same reading a terminal user sees, and nothing the reading does not say.
"""

from __future__ import annotations

from html import escape
from typing import Optional

_CSS = """
:root{--bg:#fbfaf7;--ink:#1d1c1a;--ink2:#5b5955;--rule:#e2dfd8;--card:#fff;
--accent:#3b6ea5;--ok:#2f7d4f;--warn:#b07a1c;--bad:#b3403a}
@media(prefers-color-scheme:dark){:root{--bg:#141412;--ink:#ece9e2;--ink2:#a6a39b;
--rule:#2c2b28;--card:#1c1b19;--accent:#8ab4e8;--ok:#6cc08b;--warn:#e0b25c;--bad:#e07a75}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,sans-serif}
main{max-width:880px;margin:0 auto;padding:28px 20px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:var(--ink2);margin:22px 0 8px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:12px 14px}
table{border-collapse:collapse;width:100%}td,th{padding:5px 8px;border-bottom:1px solid var(--rule);
text-align:left;vertical-align:top}th{color:var(--ink2);font-weight:600;font-size:12px}
.chip{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11.5px;
border:1px solid var(--rule)}.supported{color:var(--ok)}.contradicted,.unsupported{color:var(--bad)}
.self_asserted,.stale{color:var(--warn)}.cls{font-size:11px;color:var(--ink2);margin-right:6px}
ol,ul{margin:6px 0;padding-left:22px}.muted{color:var(--ink2)}code{font-size:12.5px}
"""


def _phase_rows(reading: dict) -> str:
    rows = []
    for phase in reading.get("phases", []):
        steps = phase.get("steps") or []
        span = (f"steps {steps[0]}–{steps[-1]}" if steps else "no steps")
        rows.append(f"<li><b>{escape(span)}</b>: {escape(str(phase.get('summary') or ''))}</li>")
    return "\n".join(rows)


def reading_html(reading: dict, title: Optional[str] = None) -> str:
    """Render one reading dict (from ``read_trace``) as a complete page."""
    name = escape(str(reading.get("agent", "")))
    task = escape(str(reading.get("task", "")))
    basis = reading.get("answer_basis") or {}
    parts = [f"<!doctype html><meta charset=utf-8><title>{escape(title or f'Reading of {name}')}</title>",
             f"<style>{_CSS}</style><main>",
             f"<h1>Reading of {name} on {task}</h1>",
             f"<p class=card>{escape(str(reading.get('summary', '')))}</p>"]
    if reading.get("phases"):
        parts.append("<h2>What happened</h2><ol>" + _phase_rows(reading) + "</ol>")
    if reading.get("rests_on"):
        parts.append(f"<h2>The answer rests on ({escape(str(basis.get('status')))})</h2>"
                     "<table><tr><th>value</th><th>status</th><th>first at</th>"
                     "<th>source</th><th>vs expected</th></tr>")
        for r in reading["rests_on"]:
            match = ("matches" if r.get("matches_expected") is True else
                     "contradicts" if r.get("status") == "contradicted" else
                     "not in expected" if r.get("matches_expected") is False else "—")
            first = "no earlier step" if r.get("first_step") is None else f"step {r['first_step']}"
            parts.append(f"<tr><td><code>{escape(str(r.get('value')))}</code></td>"
                         f"<td><span class='chip {escape(str(r.get('status')))}'>"
                         f"{escape(str(r.get('status')).replace('_', ' '))}</span></td>"
                         f"<td>{escape(first)}</td><td>{escape(str(r.get('source') or ''))}</td>"
                         f"<td>{escape(match)}</td></tr>")
        parts.append("</table>")
    why = reading.get("why_it_ended") or {}
    if why:
        term = (f"termination {escape(str(why.get('termination')))}" if why.get("declared")
                else "termination not declared")
        parts.append(f"<h2>Why it ended</h2><p class=card>"
                     f"{'succeeded' if why.get('success') else 'failed'}, {term}"
                     f" — {escape(str(why.get('verdict_basis') or ''))}</p>")
    if reading.get("what_it_means"):
        parts.append("<h2>What it means</h2><ul>")
        for f in reading["what_it_means"]:
            steps = f"steps {f.get('steps')}" if f.get("steps") else "run-level"
            parts.append(f"<li><span class=cls>[{escape(str(f.get('evidence_class')))}]</span>"
                         f"{escape(str(f.get('statement')))} <span class=muted>({escape(steps)})"
                         "</span></li>")
        parts.append("</ul>")
    if reading.get("take_forward"):
        parts.append("<h2>Take forward</h2><ol>")
        for t in reading["take_forward"]:
            where = f"at step {t['at_step']}: " if t.get("at_step") is not None else ""
            parts.append(f"<li>{escape(where)}{escape(str(t.get('instead')))}</li>")
        parts.append("</ol>")
    conf = reading.get("confidence") or {}
    if conf:
        parts.append(f"<p class=muted>Confidence: {escape(str(conf.get('level')))} — "
                     f"{escape(str(conf.get('basis') or ''))}</p>")
    parts.append("</main>")
    return "\n".join(parts)
