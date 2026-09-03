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
