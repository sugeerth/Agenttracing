"""Benchmark rigor: the benchmark audits itself.

From the Agentic Benchmark Checklist (NeurIPS 2025) and the Leaky Model
Organisms critique (2026): a do-nothing control the diagnoser must not
abstain on, a clean twin that must pass its own grader or the pair is
excluded and counted, an implanted artifact that must be reachable
between the decisive step and the answer, and a deliberately dumb
surface-cue probe whose score is subtracted from the engine's — the
margin is the headline, never the raw score.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from deepcompare.bench import (
    format_scorecard, leakage_probe, pair_validity, run_benchmark,
)
from deepcompare.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "demo" / "diagnosis_bench" / "generate_scale.py"


def _generator():
    spec = importlib.util.spec_from_file_location("diag_gen_scale", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNullAgentControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.manifest = _generator().generate(cls.out, 160)
        cls.result = run_benchmark(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_control_exists_and_is_never_abstained_on(self):
        nulls = [r for r in self.result["results"] if r["cause"] == "null_agent"]
        self.assertTrue(nulls, "null_agent scenarios must be in the corpus")
        for r in nulls:
            self.assertNotEqual(r["step_outcome"], "missed_step", r["scenario"])
            self.assertNotIn("grader_or_label", r["actual"], r["scenario"])

    def test_every_clean_twin_passes_and_every_artifact_is_reachable(self):
        self.assertEqual(self.result["invalid_pairs"], [])
        self.assertEqual(self.result["unreachable_artifact"], [])
        self.assertGreater(self.result["artifact_checked"], 100)

    def test_the_probe_is_scored_and_the_margin_is_the_headline(self):
        probe = self.result["leakage_probe"]
        self.assertEqual(probe["kind"]["total"], self.result["overall"]["total"])
        margin = self.result["engine_minus_probe"]
        self.assertIsNotNone(margin["kind"])
        self.assertAlmostEqual(
            margin["kind"],
            self.result["overall"]["accuracy"] - probe["kind"]["accuracy"], places=3)
        card = format_scorecard(self.result)
        self.assertIn("leakage probe", card)
        self.assertIn("engine margin", card)
        self.assertIn("injection contract", card)


class TestDecoyFamilies(unittest.TestCase):
    """The probe's favourite step cue — "the first step without a twin" —
    is a decoy in two families.  `late_decision` diverges harmlessly two
    reads before the real cause; `misread_reason` misreads a correctly
    observed value one benign remark after the last twin.  The engine
    must beat the probe here or the corpus is only measuring its own
    fingerprint; the truth is stated per condition, not assumed."""

    PAIRS = 160

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.manifest = _generator().generate(cls.out, cls.PAIRS)
        cls.result = run_benchmark(cls.out)
        cls.tmp_s = tempfile.TemporaryDirectory()
        cls.out_s = Path(cls.tmp_s.name)
        _generator().generate(cls.out_s, cls.PAIRS, strip=True)
        cls.result_s = run_benchmark(cls.out_s)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        cls.tmp_s.cleanup()

    def _probe_and_engine(self, result, out, family):
        manifest = json.loads((out / "MANIFEST.json").read_text())
        truth = {sc["id"]: sc for sc in manifest["scenarios"]}
        probe_hits = engine_hits = n = 0
        for r in result["results"]:
            if r["cause"] != family:
                continue
            sc = truth[r["scenario"]]
            failing = Trajectory.from_json(str(out / sc["fail"]))
            passing = Trajectory.from_json(str(out / sc["pass"]))
            n += 1
            if leakage_probe(failing, passing)["step"] in sc["decisive_steps"]:
                probe_hits += 1
            if r["step_outcome"] == "exact":
                engine_hits += 1
        return n, engine_hits, probe_hits

    def test_both_decoy_families_are_generated_and_scored(self):
        for family in ("late_decision", "misread_reason"):
            self.assertIn(family, self.result["by_cause"])
            self.assertGreater(self.result["by_cause"][family]["total"], 0)
            self.assertIn(family, self.result_s["by_cause"])

    def test_the_decisive_step_is_never_the_first_novel_step(self):
        # the corpus's own contract: in these families the first step
        # without a twin must precede the decisive step, else the family
        # is not a decoy and the probe would be right for free
        for sc in self.manifest["scenarios"]:
            if sc["cause"] not in ("late_decision", "misread_reason"):
                continue
            failing = Trajectory.from_json(str(self.out / sc["fail"]))
            passing = Trajectory.from_json(str(self.out / sc["pass"]))
            twins = {(s.type, s.name, s.input) for s in passing.steps}
            first_novel = next(i for i, s in enumerate(failing.steps)
                               if (s.type, s.name, s.input) not in twins)
            self.assertLess(first_novel, min(sc["decisive_steps"]), sc["id"])

    def test_engine_beats_the_probe_on_the_decoys_once_annotations_are_gone(self):
        # annotated condition: the generator marks the decisive step
        # `weak` and the probe reads marks, so it can match or beat the
        # engine there — that is the annotation leak, measured not
        # hidden.  Stripped condition: the probe is left with "first
        # step without a twin", which these families make wrong on
        # purpose, and the engine must beat it
        for family in ("late_decision", "misread_reason"):
            n, engine, probe = self._probe_and_engine(
                self.result_s, self.out_s, family)
            self.assertGreater(n, 0)
            self.assertGreater(engine, probe,
                               f"{family} stripped: engine {engine}/{n} vs "
                               f"probe {probe}/{n}")
            n_a, engine_a, _ = self._probe_and_engine(
                self.result, self.out, family)
            # the honest floor, annotated: the engine lands the decoy
            # step in at least three of five scenarios; the valueless
            # ticket domain (one domain in five, no typed wrong fact to
            # re-anchor on) is the measured remainder, adjacent by one —
            # 10/15 late_decision at 300 pairs.  Stripped carries no
            # floor — it is a measurement, and the margin over the probe
            # above is the claim
            self.assertGreaterEqual(engine_a / n_a, 0.6, family)

    def test_re_anchor_never_leaves_a_step_that_emitted_a_value(self):
        # the re-anchor rule's guard, pinned on the pair that found it:
        # bolt's step 1 computes 11:45 in local time; the wrong-fact
        # detector first sees the value as 11h45m two steps later, and
        # the first cut of the rule moved the anchor there.  A step that
        # produced any typed value or number is consequential by
        # construction, whatever the normaliser recognised
        from deepcompare.report import compare
        a = Trajectory.from_json(
            str(ROOT / "demo/traces/t05_flight_duration__atlas-v2.json"))
        b = Trajectory.from_json(
            str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"))
        self.assertEqual(compare(a, b)["diagnosis"]["decisive_step"]["step"], 1)


class TestPairValidity(unittest.TestCase):
    def _pair(self, pass_answer, artifact, fail_steps):
        def traj(name, steps, success, answer):
            return Trajectory.from_dict({
                "schema_version": 1, "trace_id": name,
                "agent": {"name": name, "model": "m", "version": "1"},
                "task": {"id": "t", "prompt": "p", "expected": "The total is $10.00."},
                "outcome": {"success": success, "answer": answer, "score": None,
                            "termination": "agent_stop"},
                "totals": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
                           "latency_s": 0.1},
                "steps": [dict(s, index=i, tokens=1, latency_s=0.1)
                          for i, s in enumerate(steps)],
                "tools": [], "budget": {"max_steps": 5}})
        passing = traj("p", [{"type": "answer", "name": "final", "input": pass_answer,
                              "output": pass_answer}], True, pass_answer)
        failing = traj("f", fail_steps, False, fail_steps[-1]["output"])
        scenario = {"artifact": artifact, "decisive_steps": [0]}
        return pair_validity(scenario, failing, passing)

    def test_a_clean_twin_that_fails_its_grader_invalidates_the_pair(self):
        bad = self._pair("The total is $99.00.", "$7.00", [
            {"type": "answer", "name": "final", "input": "x", "output": "The total is $7.00."}])
        self.assertFalse(bad["clean_twin_passes"])
        good = self._pair("The total is $10.00.", "$7.00", [
            {"type": "answer", "name": "final", "input": "x", "output": "The total is $7.00."}])
        self.assertTrue(good["clean_twin_passes"])

    def test_an_artifact_that_never_appears_after_the_decisive_step_is_unreachable(self):
        result = self._pair("The total is $10.00.", "$7.00", [
            {"type": "answer", "name": "final", "input": "x", "output": "The total is $8.00."}])
        self.assertIs(result["artifact_reachable"], False)
        result = self._pair("The total is $10.00.", None, [
            {"type": "answer", "name": "final", "input": "x", "output": "The total is $8.00."}])
        self.assertIsNone(result["artifact_reachable"])

    def test_the_bench_excludes_and_counts_an_invalid_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _generator().generate(out, 16)
            manifest = json.loads((out / "MANIFEST.json").read_text())
            victim = manifest["scenarios"][0]
            raw = json.loads((out / victim["pass"]).read_text())
            raw["outcome"]["answer"] = "something else entirely"
            raw["steps"][-1]["output"] = "something else entirely"
            raw["steps"][-1]["input"] = "something else entirely"
            (out / victim["pass"]).write_text(json.dumps(raw))
            result = run_benchmark(out)
            self.assertEqual(len(result["invalid_pairs"]), 1)
            self.assertEqual(result["invalid_pairs"][0]["scenario"], victim["id"])
            self.assertEqual(result["overall"]["total"], 15)
            self.assertIn("INVALID PAIRS", format_scorecard(result))


class TestLeakageProbe(unittest.TestCase):
    def test_the_probe_uses_only_surface_cues(self):
        a = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__bolt-v3.json"))
        b = Trajectory.from_json(str(ROOT / "demo/traces/t05_flight_duration__atlas-v2.json"))
        probe = leakage_probe(a, b)
        self.assertIn("kind", probe)
        self.assertIn("cue", probe)
        # deterministic
        self.assertEqual(probe, leakage_probe(a, b))


if __name__ == "__main__":
    unittest.main()
