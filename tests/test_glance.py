"""The charts a run's page leads with (agentdiff.hub.glance, .seconds, viz.mini_strip and the lanes):
each is well-formed SVG or HTML, says what it draws in words, and counts what it draws."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agentdiff import claude_sessions as cs  # noqa: E402
from agentdiff.hub import glance, seconds, viz  # noqa: E402
from agentdiff.hub.traces import strip_cells  # noqa: E402
from agentdiff.longrun import longrun  # noqa: E402
from agentdiff.timeline import timeline  # noqa: E402
from test_claude_sessions import write_session  # noqa: E402

SVG = ""     # inline in HTML: no namespace


def _session():
    tmp = tempfile.TemporaryDirectory()
    write_session(Path(tmp.name) / "projects")
    data = cs.convert(cs.find(Path(tmp.name) / "projects")[0])
    tmp.cleanup()
    return data


def _two_stretches():
    """A run of two working stretches a day apart: a check that fails, an edit, a check that passes."""
    def step(i, t, name, typ="tool_call", out="", err=False, inp="{}"):
        return {"index": i, "type": typ, "name": name, "input": inp, "output": out, "started_s": t,
                "latency_s": 2.0, "tokens": 10, "error": err}
    steps = [step(0, 0, "Read", inp='{"file_path": "a.py"}'),
             step(1, 5, "Bash", out="1 failed, 3 passed", err=True, inp='{"command": "pytest -q"}'),
             step(2, 86400, "Edit", inp='{"file_path": "a.py"}'),
             step(3, 86410, "Bash", out="4 passed", inp='{"command": "pytest -q"}'),
             {"index": 4, "type": "answer", "name": "answer", "input": "", "output": "done", "started_s": 86420}]
    return {"schema_version": 1, "trace_id": "t__a", "task": {"id": "t", "prompt": "fix it"},
            "agent": {"name": "a"}, "started_at": 1_780_000_000, "steps": steps,
            "outcome": {"success": True}, "totals": {"latency_s": 86422}}


def _svg(markup: str) -> ET.Element:
    m = re.search(r"<svg.*?</svg>", markup, re.S)
    return ET.fromstring(m.group(0))


class GlanceTest(unittest.TestCase):
    def setUp(self):
        self.data = _session()
        self.tl, self.r = timeline(self.data), longrun(self.data)
        self.html = glance.glance_html(self.data, self.tl, self.r, href="/traces/abc")

    def test_it_is_svg_and_says_what_it_draws(self):
        svg = _svg(self.html)
        self.assertEqual(svg.get("role"), "img")
        self.assertIn("2 prompt(s)", svg.get("aria-label"))
        self.assertIn("1 sub-agent(s)", svg.get("aria-label"))

    def test_each_prompt_is_a_pin_with_what_was_asked_and_a_link_to_its_steps(self):
        svg = _svg(self.html)
        pins = [a for a in svg.iter(f"{SVG}a") if "pin" in (a.get("class") or "")]
        self.assertEqual(len(pins), 2)
        self.assertTrue(pins[0].get("href").startswith("/traces/abc?at="))
        tip = " ".join(t.text or "" for t in pins[0].iter(f"{SVG}text"))
        self.assertIn("Fix the totals in ledger/report.py", tip)

    def test_checks_lanes_and_the_numbers_above_it(self):
        svg = _svg(self.html)
        dots = [c for c in svg.iter(f"{SVG}circle") if "chk" in (c.get("class") or "")]
        self.assertEqual(sorted(c.get("class") for c in dots), ["chk bad", "chk ok"])
        rows = [t.text for t in svg.iter(f"{SVG}text") if (t.get("class") or "") == "row"]
        self.assertIn("find callers", rows)
        self.assertIn("<b>2</b>prompts", self.html)
        self.assertIn("2</b>checks · 1 failed", self.html)

    def test_a_short_run_with_no_clock_gets_seconds_on_its_axis(self):
        steps = [{"index": i, "type": "tool_call", "name": "Bash", "input": "{}", "output": "", "latency_s": 2.0}
                 for i in range(6)]
        data = {"schema_version": 1, "trace_id": "t__a", "task": {"id": "t"}, "agent": {"name": "a"},
                "steps": steps, "outcome": {"success": False}}
        ticks = [t.text for t in _svg(glance.glance_html(data, timeline(data), longrun(data))).iter(f"{SVG}text")
                 if "tick" in (t.get("class") or "")]
        self.assertEqual(ticks[0], "0s")
        self.assertGreater(len(ticks), 2)

    def test_idle_between_working_stretches_is_a_labelled_break(self):
        data = _two_stretches()
        svg = _svg(glance.glance_html(data, timeline(data), longrun(data)))
        titles = [t.text for t in svg.iter(f"{SVG}title")]
        self.assertTrue(any(t and t.startswith("idle 23h") for t in titles), titles)


class SecondsTest(unittest.TestCase):
    def test_bars_add_up_by_activity_tool_and_agent(self):
        data = _session()
        out = seconds.seconds_html([timeline(data)], ["claude-code"], href="/traces/x")
        self.assertIn("By activity", out)
        self.assertIn("By tool", out)
        self.assertIn("By agent", out)
        self.assertIn("main agent", out)
        shares = [int(v) for v in re.findall(r"<b>(\d+)%</b>", out)]
        self.assertAlmostEqual(sum(shares), 100, delta=len(shares))
        tools = re.findall(r'<td class="k" title="([^"]+)">', out.split("By tool")[1].split("</table>")[0])
        self.assertEqual(tools[0], "Agent")      # the sub-agent call took 30s, the longest
        self.assertIn("1 ✗", out)
        self.assertIn('href="/traces/x?at=', out)

    def test_a_short_step_is_not_zero_seconds(self):
        self.assertEqual(seconds.dur(0.4), "0.4s")
        self.assertEqual(seconds.dur(75), "1m 15s")


class StripTest(unittest.TestCase):
    def test_a_list_row_shows_each_stretch_and_where_it_failed(self):
        tl = timeline(_two_stretches())
        cells = strip_cells(tl)
        self.assertEqual(sum(1 for c in cells if c[2]), 1, "one gap between two stretches")
        self.assertTrue(any(c[1] for c in cells), "the failed check is marked")
        svg = _svg(viz.mini_strip(cells))
        self.assertEqual(svg.get("role"), "img")


class WorkedTest(unittest.TestCase):
    def test_hours_add_up_over_runs_into_a_day_by_hour_grid(self):
        from agentdiff.hub.traces import _hours
        h = _hours(_two_stretches())
        self.assertAlmostEqual(sum(h.values()), 8.0, places=3)       # four steps of 2s
        out = glance.worked_html([h, h])
        svg = _svg(out)
        cells = [r for r in svg.iter("rect") if r.get("class") == "h"]
        days = {t.text for t in svg.iter("text") if t.text and t.text[:3] in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}
        self.assertEqual(len(cells), 24 * len(days))
        self.assertEqual(len(days), 2)
        self.assertIn("8.0s working", out.replace("8s working", "8.0s working"))
        self.assertEqual(glance.worked_html([None, {}]), "")


class LanesTest(unittest.TestCase):
    def test_a_run_of_days_keeps_each_stretch_and_its_sub_agents_visible(self):
        data = _session()
        out = viz.run_timeline(timeline(data))
        svg = _svg(out)
        self.assertTrue(any("find callers" in (t.text or "") for t in svg.iter(f"{SVG}text")))
        two = viz.run_timeline(timeline(_two_stretches()))
        self.assertIn("⁄⁄ 24h", two)          # 23h 59m idle, said in one word
        self.assertIn("day 2", two)


if __name__ == "__main__":
    unittest.main()
