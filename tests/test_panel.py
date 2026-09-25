"""A panel: a judge that reads a corpus and synthesises, checked against it.

The test that matters is the last kind: a fluent, plausible, *well-written*
claim whose citation is not in the trace has to leave the synthesis empty.
Everything else here is the machinery that makes that check possible.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepcompare.harness.panel import (  # noqa: E402
    QUESTIONS, SYSTEM, convene, corpus_tools, one_finding, verify)
from deepcompare.harness.providers import ScriptedProvider  # noqa: E402

SUITE = ROOT / "demo" / "horizon" / "suite"
RUN = "L01_service_migration__drift-lh"
#: step 136 of that run, verbatim: "orchestrator: invoice looks like the
#: previous package; taking it as done"
TRUE_QUOTE = "taking it as done"


def load(pattern="L0[1-4]*.json"):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SUITE.glob(pattern))]


def panelist(claim="A unit is taken as done without being worked.",
             quote=TRUE_QUOTE, run=RUN, step=136, looks=("corpus", "runs")):
    """A stand-in that navigates and then cites. Not a model: what it
    claims is a fact about this function, and the point of every test
    below is what happens to the claim afterwards."""
    def factory():
        state = {"n": 0}

        def script(messages, tools):
            state["n"] += 1
            if state["n"] <= len(looks):
                name = looks[state["n"] - 1]
                args = {"failed": "true"} if name == "runs" else {}
                return {"text": "", "tool_calls": [{"name": name, "arguments": args}]}
            return {"text": json.dumps({"claim": claim, "affects": ["L01_service_migration"],
                                        "cites": [{"run": run, "step": step, "quote": quote}]})}
        return ScriptedProvider(script, model="stand-in-panel")
    return factory


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class TheCorpusInstrumentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces = load()
        cls.tools = {t.name: t for t in corpus_tools(cls.traces)}

    def test_they_are_read_only(self):
        for tool in self.tools.values():
            self.assertEqual(tool.effect, "read", f"{tool.name} could change the corpus")

    def test_corpus_says_what_is_here_before_anything_is_opened(self):
        c = self.tools["corpus"].fn()
        self.assertEqual(c["runs"], len(self.traces))
        self.assertEqual(sorted(c["agents"]), ["drift-lh", "summit-lh"])
        self.assertEqual(c["tasks_with_both_sides"], c["tasks"])

    def test_runs_filters_by_agent_task_and_outcome(self):
        self.assertEqual(self.tools["runs"].fn(agent="drift-lh")["found"], len(self.traces) // 2)
        failed = self.tools["runs"].fn(failed="true")
        self.assertGreater(failed["found"], 0)
        for row in failed["runs"]:
            self.assertIs(row["success"], False)
        passed = self.tools["runs"].fn(failed="false")
        for row in passed["runs"]:
            self.assertIs(row["success"], True)

    def test_contrast_puts_the_two_sides_together_and_finds_where_they_part(self):
        got = self.tools["contrast"].fn("L01_service_migration")
        self.assertEqual(len(got["sides"]), 2)
        self.assertNotEqual(got["sides"][0]["agent"], got["sides"][1]["agent"])
        self.assertIsNotNone(got["first_divergence"])
        self.assertIn("at", got["first_divergence"])

    def test_contrast_of_a_task_with_one_side_says_so(self):
        self.assertIn("fewer than two", self.tools["contrast"].fn("nope")["error"])

    def test_open_descends_into_one_run_with_the_per_run_instruments(self):
        got = self.tools["open"].fn(RUN)
        self.assertEqual(got["graph"]["steps"], 241)
        self.assertTrue(got["flags"]["measurable"])
        self.assertIn("error", self.tools["open"].fn("no_such_run"))

    def test_locate_works_across_the_corpus_and_inside_one_run(self):
        across = self.tools["locate"].fn("invoice")
        self.assertGreater(across["runs_mentioning"], 0)
        self.assertIn("run", across["hits"][0])
        inside = self.tools["locate"].fn("invoice", run=RUN)
        self.assertEqual(inside["run"], RUN)
        self.assertGreater(inside["found"], 0)

    def test_read_needs_a_run_and_returns_that_range(self):
        self.assertIn("error", self.tools["read"].fn(1, 3))
        got = self.tools["read"].fn(134, 138, run=RUN)
        self.assertEqual(len(got["steps"]), 5)
        self.assertIn("[136]", " ".join(got["steps"]))


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class VerificationTest(unittest.TestCase):
    """No model is involved: a citation names a run, a step and a
    fragment, and either the step contains it or it does not."""

    @classmethod
    def setUpClass(cls):
        cls.traces = load()

    def cite(self, **kw):
        base = {"run": RUN, "step": 136, "quote": TRUE_QUOTE}
        base.update(kw)
        return verify({"claim": "x", "cites": [base]}, self.traces)

    def test_a_true_quotation_verifies(self):
        got = self.cite()
        self.assertTrue(got["supported"])
        self.assertEqual((got["verified"], got["cited"]), (1, 1))

    def test_a_quotation_the_step_does_not_contain_fails(self):
        got = self.cite(quote="and then it ran every check twice")
        self.assertFalse(got["supported"])
        self.assertIn("does not contain that text", got["reason"])

    def test_a_run_that_does_not_exist_fails(self):
        self.assertIn("no run named", self.cite(run="invented_run")["reason"])

    def test_a_step_out_of_range_fails(self):
        self.assertIn("has no step 99999", self.cite(step=99999)["reason"])

    def test_citing_nothing_is_not_support(self):
        got = verify({"claim": "a confident sentence with nothing behind it"}, self.traces)
        self.assertFalse(got["supported"])
        self.assertIn("cites nothing", got["reason"])

    def test_one_bad_citation_sinks_the_finding(self):
        """Not a majority vote: a claim resting on two facts of which one
        is invented is not two-thirds true."""
        got = verify({"claim": "x", "cites": [
            {"run": RUN, "step": 136, "quote": TRUE_QUOTE},
            {"run": RUN, "step": 136, "quote": "a thing it made up"}]}, self.traces)
        self.assertFalse(got["supported"])
        self.assertEqual((got["verified"], got["cited"]), (1, 2))

    def test_whitespace_and_case_do_not_decide_it(self):
        self.assertTrue(self.cite(quote="  Taking  It   As Done ")["supported"])


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class TheSynthesisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces = load()

    def test_a_cited_finding_reaches_the_synthesis(self):
        out = convene(self.traces, panelist(), questions=["What goes wrong most often?"])
        self.assertEqual((out["supported"], out["dropped_for_bad_citations"]), (1, 0))
        self.assertEqual(len(out["synthesis"]), 1)
        self.assertEqual(out["synthesis"][0]["claim"], "A unit is taken as done without being worked.")

    def test_a_fluent_claim_with_an_invented_citation_leaves_it_empty(self):
        """The whole point. The claim below is well written, plausible and
        about the right run — and its quotation is not in the trace, so the
        synthesis is empty and the reason is kept."""
        out = convene(self.traces, panelist(
            claim="The orchestrator systematically re-verifies work it has already checked, at real cost.",
            quote="re-verifying the earlier packages"), questions=["What goes wrong most often?"])
        self.assertEqual(out["synthesis"], [])
        self.assertEqual((out["supported"], out["dropped_for_bad_citations"]), (0, 1))
        self.assertIn("does not contain that text", out["dropped"][0]["reason"])
        # the claim is kept, so a reader can see what was rejected
        self.assertIn("re-verifies", out["dropped"][0]["claim"])

    def test_the_panel_looked_before_it_answered_and_that_is_recorded(self):
        out = convene(self.traces, panelist(), questions=["q"])
        finding = out["findings"][0]
        self.assertTrue(finding["looked_before_answering"])
        self.assertEqual(sorted(finding["tools_used"]), ["corpus", "runs"])
        straight = convene(self.traces, panelist(looks=()), questions=["q"])
        self.assertFalse(straight["findings"][0]["looked_before_answering"])
        self.assertEqual(straight["findings"][0]["tool_calls"], 0)

    def test_no_memory_between_questions(self):
        out = convene(self.traces, panelist(), questions=["one", "two", "three"])
        self.assertFalse(out["memory_between_questions"])
        self.assertEqual(out["asked"], 3)
        self.assertEqual([f["question"] for f in out["findings"]], ["one", "two", "three"])

    def test_a_panel_that_returns_no_json_is_an_error_not_a_finding(self):
        def mute():
            return ScriptedProvider(lambda m, t: {"text": "Broadly, things went well."}, model="mute")
        out = convene(self.traces, mute, questions=["q"])
        self.assertEqual(out["synthesis"], [])
        self.assertTrue(out["errors"])

    def test_the_default_questions_are_ones_a_single_run_cannot_answer(self):
        self.assertGreaterEqual(len(QUESTIONS), 3)
        for q in QUESTIONS:
            self.assertRegex(q.lower(), r"(corpus|runs|agents|across|both|common|succeeds)")
        # and the instruction says the citations are checked, so the panel
        # is told the rule it will be held to
        self.assertIn("checked mechanically", SYSTEM)
        self.assertIn("do not paraphrase", SYSTEM)

    def test_the_golden_set_never_reaches_the_panel(self):
        """The panel is asked what it finds; handing it the answers first
        would make the exercise a reading test."""
        golden = json.loads((ROOT / "demo" / "horizon" / "suite_golden.json").read_text(encoding="utf-8"))
        seen = []

        def watcher():
            def script(messages, tools):
                seen.append(json.dumps(messages))
                return {"text": json.dumps({"claim": "c", "cites": [
                    {"run": RUN, "step": 136, "quote": TRUE_QUOTE}]})}
            return ScriptedProvider(script, model="watcher")
        convene(self.traces, watcher, questions=["q"])
        blob = " ".join(seen)
        for task in golden["tasks"][:4]:
            if task.get("failure_mode"):
                self.assertNotIn(task["failure_mode"], blob, task["id"])
            for stone in (task.get("milestones") or [])[:3]:
                if stone.get("evidence"):
                    self.assertNotIn(str(stone["evidence"]), blob)


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class LongRunningTest(unittest.TestCase):
    """Reading a large corpus is a long task, and a long task is
    interrupted. Losing an hour of reading to a blip is a property of the
    harness, not of the model."""

    def test_a_checkpoint_resumes_instead_of_re_asking(self):
        traces = load()
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "panel.ckpt"
            first = convene(traces, panelist(), questions=["one", "two"], checkpoint=ckpt)
            self.assertEqual(first["asked"], 2)
            self.assertTrue(ckpt.is_file())

            asked = []
            def counting():
                asked.append(1)
                return panelist()()
            again = convene(traces, counting, questions=["one", "two"], checkpoint=ckpt)
            self.assertEqual(again["asked"], 2, "the findings came back")
            self.assertEqual(len(asked), 1, "only the probe for the model name, no question re-asked")

            more = convene(traces, panelist(), questions=["one", "two", "three"], checkpoint=ckpt)
            self.assertEqual(more["asked"], 3)
            self.assertEqual([f["question"] for f in more["findings"]], ["one", "two", "three"])

    def test_a_checkpoint_that_is_corrupt_starts_over_rather_than_failing(self):
        traces = load()
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "panel.ckpt"
            ckpt.write_text("{not json", encoding="utf-8")
            out = convene(traces, panelist(), questions=["one"], checkpoint=ckpt)
            self.assertEqual(out["asked"], 1)


@unittest.skipUnless(SUITE.is_dir(), "the long-horizon suite is not generated")
class CommandTest(unittest.TestCase):
    def test_the_cli_prints_the_synthesis_and_what_it_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp) / "t"
            traces.mkdir()
            for path in sorted(SUITE.glob("L0[1-2]*.json")):
                (traces / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            script = Path(tmp) / "p.json"
            script.write_text(json.dumps({"model": "stand-in", "turns": [
                {"text": "", "tool_calls": [{"name": "corpus", "arguments": {}}]},
                {"text": json.dumps({"claim": "Invented.", "cites": [
                    {"run": RUN, "step": 136, "quote": "not in the trace"}]})}]}), encoding="utf-8")
            done = subprocess.run([sys.executable, "-m", "deepcompare", "panel", str(traces),
                                   "--provider", f"p=scripted:{script}", "--ask", "why?",
                                   "-o", str(Path(tmp) / "out")],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            self.assertIn("dropped", done.stdout)
            self.assertIn("0 of 1 finding(s) survived", done.stdout)
            out = json.loads((Path(tmp) / "out" / "panel.json").read_text(encoding="utf-8"))
            self.assertEqual(out["synthesis"], [])
            self.assertEqual(out["dropped_for_bad_citations"], 1)

    def test_one_run_is_a_reading_not_a_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            traces = Path(tmp) / "t"
            traces.mkdir()
            one = sorted(SUITE.glob("L01*drift*.json"))[0]
            (traces / one.name).write_text(one.read_text(encoding="utf-8"), encoding="utf-8")
            done = subprocess.run([sys.executable, "-m", "deepcompare", "panel", str(traces),
                                   "--provider", "p=scripted:none.json"],
                                  cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 2)
            self.assertIn("at least two runs", done.stderr)


if __name__ == "__main__":
    unittest.main()
