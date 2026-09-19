"""The research program's engine items, shipped and measured.

Evidence-class weighting in the diagnosis ledger (#7), the
overdetermination guard and its corpus family (#8), meltdown onset
(R7), pass^k with an interval (#9), one confidence vocabulary across
every output, and the hypothesis-generator registry.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepcompare.bench import run_benchmark
from deepcompare.confidence import confidence, describe
from deepcompare.diagnosis import HYPOTHESIS_GENERATORS, Ledger, _Ledger
from deepcompare.reasoning import read_trace
from deepcompare.report import compare
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "demo" / "diagnosis_bench" / "generate_scale.py"


def _generator():
    spec = importlib.util.spec_from_file_location("diag_gen_scale", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic(steps, success, answer, expected="The refund is $120.00.", name="syn"):
    return Trajectory.from_dict({
        "schema_version": 1, "trace_id": name,
        "agent": {"name": name, "model": "sim", "version": "1"},
        "task": {"id": "syn_task", "prompt": "What is the refund?", "expected": expected},
        "outcome": {"success": success, "answer": answer,
                    "score": 1.0 if success else 0.0, "termination": "agent_stop"},
        "totals": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0, "latency_s": 1.0},
        "steps": [dict(s, index=i, tokens=5, latency_s=0.1) for i, s in enumerate(steps)],
        "tools": [{"name": "lookup", "effect": "read"}],
        "budget": {"max_steps": 12},
    })


class TestEvidenceClasses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        a = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"))
        cls.report = compare(a, b)
        cls.diag = cls.report["diagnosis"]

    def test_every_evidence_item_carries_a_class(self):
        for item in self.diag["evidence"]:
            self.assertIn(item["evidence_class"], ("observable", "annotation", "stated"), item)

    def test_every_hypothesis_counts_its_class_mix(self):
        for h in self.diag["hypotheses"]:
            self.assertEqual(set(h["evidence_classes"]), {"observable", "annotation", "stated"})
            self.assertEqual(sum(h["evidence_classes"].values()),
                             len([e for e in h["supports"]
                                  if any(i["id"] == e for i in self.diag["evidence"])]))

    def test_the_verdict_names_what_the_lead_rests_on(self):
        self.assertIn("on observable evidence", self.diag["verdict"])

    def test_annotation_fields_are_annotations_and_reason_text_is_stated(self):
        ledger = Ledger()
        a = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"))
        e1 = ledger.span("b", 1, "quality", "weak", "quality mark", "annotated")
        e2 = ledger.span("b", 3, "input", "Sanity-check", "reason text", "stated")
        e3 = ledger.span("b", 1, "output", "6:40", "tool output", "measured")
        ledger.classify(a, a)
        classes = {i["id"]: i["evidence_class"] for i in ledger.items}
        self.assertEqual((classes[e1], classes[e2], classes[e3]),
                         ("annotation", "stated", "observable"))
        self.assertIs(_Ledger, Ledger)

    def test_observable_support_breaks_ties(self):
        # same score, different support: the observable one ranks first
        from deepcompare.diagnosis import KINDS
        self.assertTrue(KINDS)  # the registry order is the third key; the
        # second is "has observable support" — pinned by reading the key
        import inspect
        from deepcompare import diagnosis
        src = inspect.getsource(diagnosis.diagnose)
        self.assertIn('0 if observable else 1', src)


class TestGeneratorRegistry(unittest.TestCase):
    def test_every_kind_is_one_registered_function(self):
        names = [g.name for g in HYPOTHESIS_GENERATORS]
        self.assertEqual(names, ["grader_or_label", "harness_termination",
                                 "environment_error", "wrong_fact_propagation",
                                 "divergence", "budget_pressure", "process_pathology"])
        process = HYPOTHESIS_GENERATORS[-1]
        self.assertTrue(process.applies("single_failure", failed=False))
        self.assertFalse(HYPOTHESIS_GENERATORS[0].applies("single_failure", failed=False))
        self.assertFalse(HYPOTHESIS_GENERATORS[4].applies("both_failed", failed=True))


class TestOverdetermination(unittest.TestCase):
    PAIRS = 120

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.manifest = _generator().generate(out, cls.PAIRS)
        cls.result = run_benchmark(out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_family_exists_and_declares_two_decisive_steps(self):
        scenarios = [s for s in self.manifest["scenarios"] if s["cause"] == "overdetermined"]
        self.assertTrue(scenarios)
        for s in scenarios:
            self.assertTrue(s["overdetermined"])
            self.assertEqual(len(s["decisive_steps"]), 2)
        self.assertEqual(self.manifest["version"], 7)

    def test_the_engine_flags_every_overdetermined_pair_and_nothing_else(self):
        rows = self.result["results"]
        over = [r for r in rows if r["overdetermined_truth"]]
        rest = [r for r in rows if not r["overdetermined_truth"]]
        self.assertTrue(over)
        self.assertEqual(sum(1 for r in over if r["overdetermined_flagged"]), len(over))
        self.assertEqual([r["scenario"] for r in rest if r["overdetermined_flagged"]], [])
        bucket = self.result["by_cause"]["overdetermined"]
        self.assertEqual(bucket["overdetermined_flagged"], bucket["total"])

    def test_the_guard_names_both_faults_with_a_recipe_for_each(self):
        good = _synthetic([
            {"type": "plan", "name": "plan", "input": "look it up", "output": ""},
            {"type": "tool_call", "name": "lookup", "input": "lookup(x)",
             "output": "The refund is $120.00.", "effect": "read"},
            {"type": "answer", "name": "final", "input": "The refund is $120.00.",
             "output": "The refund is $120.00."}], True, "The refund is $120.00.", name="good")
        bad = _synthetic([
            {"type": "plan", "name": "plan", "input": "look it up", "output": ""},
            {"type": "tool_call", "name": "lookup", "input": "lookup(x) (stale)",
             "output": "The refund is $95.00.", "effect": "read"},
            {"type": "reason", "name": "reason", "input": "Reading the figure as $80.00.",
             "output": ""},
            {"type": "answer", "name": "final", "input": "The refund is $80.00.",
             "output": "The refund is $80.00."}], False, "The refund is $80.00.", name="bad")
        report = compare(good, bad)
        over = report["diagnosis"]["decisive_step"]["overdetermined"]
        self.assertIsNotNone(over)
        self.assertEqual(over["status"], "possible")
        self.assertEqual([c["step"] for c in over["candidates"]], [1, 2])
        self.assertEqual(len(over["replay_recipe"]), 2)
        self.assertIn("possibly overdetermined", report["diagnosis"]["verdict"])
        cause = next(l for l in report["verdict_card"]["lines"] if l["key"] == "cause")
        self.assertIn("possibly overdetermined", cause["text"])
        self.assertEqual(report["diagnosis"]["decisive_step"]["joint_candidates"],
                         over["candidates"])

    def test_a_single_wrong_value_is_not_overdetermined(self):
        good = _synthetic([
            {"type": "tool_call", "name": "lookup", "input": "lookup(x)",
             "output": "The refund is $120.00.", "effect": "read"},
            {"type": "answer", "name": "final", "input": "The refund is $120.00.",
             "output": "The refund is $120.00."}], True, "The refund is $120.00.", name="good")
        bad = _synthetic([
            {"type": "tool_call", "name": "lookup", "input": "lookup(x) (stale)",
             "output": "The refund is $95.00.", "effect": "read"},
            {"type": "answer", "name": "final", "input": "The refund is $95.00.",
             "output": "The refund is $95.00."}], False, "The refund is $95.00.", name="bad")
        self.assertIsNone(compare(good, bad)["diagnosis"]["decisive_step"]["overdetermined"])


class TestMeltdownOnset(unittest.TestCase):
    def test_six_identical_calls_are_a_meltdown_with_a_located_action(self):
        call = {"type": "tool_call", "name": "lookup", "input": "lookup(x)",
                "output": "Error: timeout", "effect": "read", "error": True}
        steps = [{"type": "plan", "name": "plan", "input": "look it up", "output": ""}] \
            + [dict(call) for _ in range(6)] \
            + [{"type": "answer", "name": "final", "input": "I could not find it.",
                "output": "I could not find it."}]
        reading = read_trace(_synthetic(steps, False, "I could not find it."))
        melt = next(f for f in reading["what_it_means"] if f["kind"] == "meltdown_onset")
        self.assertEqual(melt["steps"][0], 1)
        self.assertEqual(len(melt["steps"]), 6)
        self.assertEqual(melt["evidence_class"], "observable")
        action = next(t for t in reading["take_forward"] if "re-plan" in t["instead"])
        self.assertEqual(action["at_step"], 1)

    def test_four_identical_calls_are_not_yet_a_meltdown(self):
        call = {"type": "tool_call", "name": "lookup", "input": "lookup(x)",
                "output": "Error: timeout", "effect": "read", "error": True}
        steps = [dict(call) for _ in range(4)] + [
            {"type": "answer", "name": "final", "input": "no", "output": "no"}]
        reading = read_trace(_synthetic(steps, False, "no"))
        self.assertFalse([f for f in reading["what_it_means"] if f["kind"] == "meltdown_onset"])


class TestOneConfidenceVocabulary(unittest.TestCase):
    def test_the_shape_and_the_single_observation_ceiling(self):
        c = confidence("high", 1, "one pair", "hypothesized")
        self.assertEqual(c["level"], "medium")
        self.assertIn("capped", c["basis"])
        self.assertEqual(set(c), {"level", "n", "basis", "verified"})
        self.assertEqual(confidence("high", 4, "four tasks")["level"], "high")
        with self.assertRaises(ValueError):
            confidence("certain", 1, "x")
        self.assertIn("(n=1)", describe(c))

    def test_diagnosis_and_reading_quote_it(self):
        a = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"))
        report = compare(a, b)
        dc = report["diagnosis"]["confidence"]
        self.assertEqual((dc["n"], dc["verified"]), (1, "hypothesized"))
        rc = report["reading"]["b"]["confidence"]
        self.assertEqual(rc["n"], 1)
        self.assertNotEqual(rc["level"], "high")

    def test_no_output_says_high_confidence_beside_n_equals_one(self):
        import re
        proc = subprocess.run(
            [sys.executable, "-m", "deepcompare", "compare",
             str(ROOT / "demo/traces/t05_flight_duration__atlas-v2.json"),
             str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json")],
            cwd=str(ROOT), capture_output=True, text=True, check=True)
        for line in proc.stdout.splitlines():
            if "n=1" in line:
                self.assertNotIn("high", line.lower().replace("highest", ""), line)
        self.assertNotIn("+100pt success (1/1 tasks)", proc.stdout)
        self.assertIn("n=1; not a gain estimate", proc.stdout)


class TestPassKInterval(unittest.TestCase):
    def test_runs_prints_pass_k_with_an_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, "-m", "deepcompare", "runs",
                 str(ROOT / "demo" / "runs" / "traces"), "-o", tmp],
                cwd=str(ROOT), capture_output=True, text=True, check=True)
            self.assertRegex(proc.stdout, r"pass\^k\s+k=1:\d\.\d{3} \[\d\.\d\d, \d\.\d\d\]")
            self.assertIn("plug-in", proc.stdout)
            agg = json.loads((Path(tmp) / "aggregate.json").read_text(encoding="utf-8"))
            for row in agg["reliability"]["per_agent"].values():
                for point in row["pass_hat_k"]["curve"]:
                    if point["value"] is not None:
                        lo, hi = point["ci95"]
                        self.assertLessEqual(lo, hi)


if __name__ == "__main__":
    unittest.main()
