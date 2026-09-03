"""The verdict card and the sentences that were never worth printing.

A fresh-eyes audit of the demo found the CLI leading with a metrics table
and burying the cause on line 14, the trace's own step note never
surfaced, and machine sentences with nothing to say ("avoided 0 extra
step(s), 0 tokens and 0s", "spent +0 steps", "termination undeclared
(not declared)").  The card answers the reader's first five questions
first; every line quotes a section of the report; empty sentences are
not emitted.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare import __version__
from deepcompare.report import compare
from deepcompare.trace import Trajectory
from deepcompare.verdict import format_verdict_card, verdict_card

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "demo" / "traces"

DEGENERATE = re.compile(r"avoided 0 |\+0 steps|\+0 tokens|\+0s latency|0 extra step"
                        r"|\(not declared\)|incorrect tool call \(\"final\"\)")


def _pair(task):
    a = Trajectory.from_json(str(TRACES / f"{task}__atlas-v2.json"))
    b = Trajectory.from_json(str(TRACES / f"{task}__bolt-v3.json"))
    return compare(a, b)


class TestVerdictCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t05 = _pair("t05_flight_duration")
        cls.card = cls.t05["verdict_card"]
        cls.lines = {line["key"]: line for line in cls.card["lines"]}

    def test_the_card_rides_on_the_report_and_leads_with_the_outcome(self):
        keys = [line["key"] for line in self.card["lines"]]
        self.assertEqual(keys[0], "verdict")
        self.assertIn("atlas-v2 solved t05_flight_duration; bolt-v3 failed.",
                      self.lines["verdict"]["text"])

    def test_the_cause_is_the_decisive_step_in_the_traces_own_words(self):
        cause = self.lines["cause"]
        self.assertEqual(cause["step"], self.t05["diagnosis"]["decisive_step"]["step"])
        self.assertEqual(cause["side"], "b")
        note = self.t05["b"]["steps"][cause["step"]]["note"]
        self.assertTrue(note)
        self.assertIn(note, cause["text"])
        self.assertIn("hypothesized", cause["text"])

    def test_cost_names_the_cheaper_side_and_says_what_it_bought(self):
        cost = self.lines["cost"]["text"]
        self.assertTrue(cost.startswith("bolt-v3 spent "))
        self.assertIn("235 tokens", cost)
        self.assertIn("faster to nothing", cost)

    def test_fix_is_the_failing_sides_first_located_next_action(self):
        fix = self.lines["fix"]
        first = self.t05["reading"]["b"]["take_forward"][0]
        self.assertEqual(fix["step"], first["at_step"])
        self.assertIn(first["instead"], fix["text"])

    def test_confidence_says_the_step_is_not_replay_verified(self):
        self.assertIn("not replay-verified", self.lines["confidence"]["text"])
        self.assertIn("n=1", self.lines["confidence"]["text"])

    def test_every_line_names_its_source_section(self):
        for line in self.card["lines"]:
            self.assertTrue(line.get("source"), line)

    def test_text_form_labels_every_line(self):
        text = format_verdict_card(self.card)
        for label in ("VERDICT", "CAUSE", "COST", "FIX", "CONF"):
            self.assertIn(label, text)

    def test_a_pair_with_no_spend_difference_has_no_cost_line(self):
        report = json.loads(json.dumps(self.t05))
        report["tradeoff"]["spend_delta_b_minus_a"] = {
            "tokens": 0, "cost_usd": 0.0, "latency_s": 0.0, "steps": 0}
        keys = [line["key"] for line in verdict_card(report)["lines"]]
        self.assertNotIn("cost", keys)


class TestNoDegenerateSentences(unittest.TestCase):
    """Across every demo pair, the CLI never prints a sentence with nothing
    in it."""

    @classmethod
    def setUpClass(cls):
        cls.outputs = {}
        for a in sorted(TRACES.glob("*__atlas-v2.json")):
            b = TRACES / a.name.replace("atlas-v2", "bolt-v3")
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "compare",
                                   str(a), str(b)], cwd=str(ROOT),
                                  capture_output=True, text=True, check=True)
            cls.outputs[a.name] = proc.stdout

    def test_compare_output_carries_no_empty_sentence(self):
        self.assertGreaterEqual(len(self.outputs), 8)
        for name, out in self.outputs.items():
            hit = DEGENERATE.search(out)
            self.assertIsNone(hit, f"{name}: {hit.group(0) if hit else ''}")

    def test_compare_output_leads_with_the_card(self):
        for name, out in self.outputs.items():
            self.assertTrue(out.startswith("VERDICT"), name)

    def test_explain_output_carries_no_empty_sentence(self):
        for trace in sorted(TRACES.glob("*.json"))[:4]:
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "explain",
                                   str(trace)], cwd=str(ROOT),
                                  capture_output=True, text=True, check=True)
            self.assertIsNone(DEGENERATE.search(proc.stdout), trace.name)


class TestOneCommandDemo(unittest.TestCase):
    def test_demo_writes_the_blocks_report_and_prints_the_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, "-m", "deepcompare", "demo",
                                   "-o", tmp], cwd=str(ROOT),
                                  capture_output=True, text=True, check=True)
            self.assertTrue(proc.stdout.startswith("AgentDiff demo"))
            self.assertIn("VERDICT", proc.stdout)
            self.assertIn("Report:", proc.stdout)
            html = (Path(tmp) / "report.html").read_text(encoding="utf-8")
            # the blocks page, not the legacy viewer: it registers blocks
            self.assertIn("AgentDiff.block(", html)
            self.assertIn('"verdict_card"', html)
            self.assertEqual(len(list(Path(tmp).glob("report_*.json"))), 8)

    def test_compare_html_and_explain_html_write_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            pair = Path(tmp) / "pair.html"
            subprocess.run([sys.executable, "-m", "deepcompare", "compare",
                            str(TRACES / "t05_flight_duration__atlas-v2.json"),
                            str(TRACES / "t05_flight_duration__bolt-v3.json"),
                            "--html", str(pair)], cwd=str(ROOT),
                           capture_output=True, text=True, check=True)
            self.assertIn("AgentDiff.block(", pair.read_text(encoding="utf-8"))
            run = Path(tmp) / "run.html"
            subprocess.run([sys.executable, "-m", "deepcompare", "explain",
                            str(TRACES / "t05_flight_duration__bolt-v3.json"),
                            "--html", str(run)], cwd=str(ROOT),
                           capture_output=True, text=True, check=True)
            html = run.read_text(encoding="utf-8")
            self.assertIn("Reading of bolt-v3", html)
            self.assertIn("The answer rests on", html)
            self.assertIn("Take forward", html)
            self.assertNotIn("(not declared)", html)


class TestVersionAgrees(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
        self.assertEqual(__version__, declared)


class TestDemoLabelsAreSimulations(unittest.TestCase):
    def test_no_vendor_model_name_labels_a_scripted_agent(self):
        # the demo agents are scripts; labelling them with real vendor
        # model names implied a comparison of real models that never ran
        for path in TRACES.glob("*.json"):
            model = json.loads(path.read_text(encoding="utf-8"))["agent"]["model"]
            self.assertTrue(model.startswith("sim-"), f"{path.name}: {model}")


if __name__ == "__main__":
    unittest.main()
